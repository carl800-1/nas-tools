import os
import re

import log
from app.utils import ExceptionUtils
from config import Config, RMT_SUBEXT

# 1MB = 1024 x 1024 字节（与 CleanHelper 同口径，不做十进制/二进制切换）
BYTES_PER_MB = 1024 * 1024

# 只处理全站统一的字幕扩展名（srt / ass / ssa）。
# ⚠️ 刻意**不**把 .sub / .idx / .sup 纳进来：VobSub 是 .idx + .sub 成对使用的，
#    删掉其中一半会让整条字幕彻底不可用，收益为负。
SUB_EXT = [str(ext).lower() for ext in RMT_SUBEXT]

# 「更好的格式」排序：ASS/SSA 带样式与定位信息，通常比 SRT 更有价值。
# 排序只用于「同一语言里挑一条留下」时打分，不影响要不要删。
_FORMAT_RANK = {".ass": 3, ".ssa": 3, ".srt": 2}

# 保留策略：quality = 先看格式再看体积（默认）；size = 一律留体积最大的
_KEEP_POLICIES = ("quality", "size")

# 语言标记 → 归一化语言键。
# 口径对齐 app/filetransfer.py 的 __transfer_subtitles（那边用 _zhcn_sub_re /
# _zhtw_sub_re / _eng_sub_re 判定，且判定顺序是「先简体、再繁体、后英文」），
# 这里沿用同样的优先级，免得同一份字幕在「转移命名」与「清理归类」两处结论打架。
_LANG_TOKENS = (
    ("chs", ("chs", "zh", "zho", "zhcn", "zhsg", "zhhans", "cn", "sc", "sg", "gb", "chinese")),
    ("cht", ("cht", "tw", "hk", "tc", "zhtw", "zhhk", "zhmo", "zhhant", "big5")),
    ("eng", ("eng", "en", "english")),
    ("jpn", ("jpn", "jp", "ja", "japanese")),
    ("kor", ("kor", "ko", "korean")),
)
_LANG_ORDER = [key for key, _ in _LANG_TOKENS]
_TOKEN_2_LANG = {tok: key for key, toks in _LANG_TOKENS for tok in toks}

# 「zh-xx / zh_xx」这类带地区后缀的写法必须**整段**判定，不能按 - 切开：
# 切开后前半段是 zh，会被当成简体，`X.zh-TW.srt` 就变成「简体+繁体」而躲过清理。
# 做法是先拼成无分隔符的形式（zh-TW -> zhtw），再交给上面的词表命中。
_ZH_COMPOUNDS = (
    ("zh-hans", "zhhans"), ("zh-hant", "zhhant"),
    ("zh-cn", "zhcn"), ("zh-sg", "zhsg"), ("zh-my", "zhsg"),
    ("zh-tw", "zhtw"), ("zh-hk", "zhhk"), ("zh-mo", "zhmo"),
)

# 中文语言词（在**单个文件名段落内部**做子串识别，覆盖「简体中文字幕」「国粤双语」这类写法）。
# ⚠️ 刻意不含裸「中文」：`繁体中文` 会因此同时命中简体，把一份繁中字幕错认成「简+繁」；
#    这里与 filetransfer 的口径一致 —— 它认的也是「中字 / 双语 / 简体 / 简中」。
_CN_WORD_RES = (
    ("chs", r"简体|简中|中字|双语|国语"),
    ("cht", r"繁体|繁中|台繁|港繁|粤语繁"),
    ("eng", r"英文|英语"),
    ("jpn", r"日文|日语|日語"),
    ("kor", r"韩文|韩语|韓語"),
)
_LANG_NAMES = {"chs": "简体中文", "cht": "繁体中文", "eng": "英文",
               "jpn": "日语", "kor": "韩语", "und": "未标注"}


def _clean_segment(seg):
    """去掉 (1) / [1] 这类计数后缀并转小写，返回可用于比对的段落"""
    return re.sub(r"[\(\[][^\)\]]*[\)\]]", "", str(seg)).strip().lower()


def _segment_langs(seg):
    """一个文件名段落里出现的全部语言键（按固定优先级返回）"""
    raw = str(seg)
    # 整段就是一个计数（如 ".8." 里的 8、(1) 这种独立段）→ 不含语言信息
    if re.fullmatch(r"[\(\[\s]*\d+[\)\]\s]*", raw):
        return []
    cleaned = _clean_segment(raw)
    if not cleaned:
        return []
    # zh_CN / zh-TW 这类带地区的写法先并成整段，避免被下面的 - 切开后误判
    for compound, joined in _ZH_COMPOUNDS:
        cleaned = cleaned.replace(compound, joined)
    found = []
    # 拉丁语言标记：按 & - _ + 空格再切一次，覆盖 chs&eng / chs-jpn
    for token in re.split(r"[&\-_+\s]+", cleaned):
        key = _TOKEN_2_LANG.get(token)
        if key and key not in found:
            found.append(key)
    # 中文语言词
    for key, pattern in _CN_WORD_RES:
        if key not in found and re.search(pattern, cleaned):
            found.append(key)
    return [key for key in _LANG_ORDER if key in found]


def _is_strippable_segment(seg):
    """该段落是否只是「语言标记 / 流序号 / 计数」，可以从身份键里剥掉"""
    raw = str(seg)
    if not raw:
        return False
    # 独立计数段：(1) / [2]
    if re.fullmatch(r"[\(\[]\s*\d+\s*[\)\]]", raw):
        return True
    cleaned = _clean_segment(raw)
    if not cleaned:
        return False
    # 流序号：2 / 3 / 8。⚠️ 限制 1~2 位——4 位纯数字多为年份（Show.2024.srt），
    # 剥掉会把不同影片的身份键撞到一起。
    if cleaned.isdigit() and len(cleaned) <= 2:
        return True
    return bool(_segment_langs(raw))


class SubtitleHelper:
    """
    字幕清理服务：扫描指定根目录下的字幕文件，把**同一个视频 / 同一集**的
    同语言字幕去重，每种语言只保留一份，其余删除。

    设计约束：

      1. 归组口径 = (所在目录, 身份键, 语言)。三者全同才算「重复」，缺一不删：
         * 身份键：文件名剥掉尾部的「语言标记 / 流序号 / (n) 计数」后的主干。
           `X.S01E15.chs.ass` / `X.S01E15.chs.简体中文(1).srt` / `X.S01E15.8.zh-CN.srt`
           三者的身份键都是 `X.S01E15`、语言都是简体中文 ⇒ 同一组，只留一条。
         * 目录不同不归一组：不同剧集 / 不同季的同名字幕互不影响。
         * 语言认不出来的按 `und` 处理，**只列出、绝不删除**：同一集下「没打语言标记的
           .ass 与 .srt」完全可能是两种不同语言，删哪条都有丢字幕的风险。
      2. 语言判定刻意**保守**：只认「单个文件名段落」里的标记，认不出就按 `und` 处理。
         宁可少删（两条都留下），也不要错删（把繁中当简中删掉）。
      3. 保留策略 keep_policy：
         * `quality`（默认）—— 先比格式（ASS/SSA 优于 SRT），同格式比体积；
         * `size`            —— 一律留体积最大的，同体积比格式。
         同一策略下的并列项按「文件名更短者优先」再按字典序兜底，保证结果可复现。
      4. 支持 dry_run：只扫描并返回清单，不落盘删除。
      5. 符号链接（目录与文件）默认跳过且不跟随；权限不足、文件被占用等异常
         逐条记录后跳过，不中断整体流程。
      6. root_path 为文件系统根目录（`/` 或盘符根，例如 C 盘根）时直接拒绝 ——
         一次误操作会横扫整个盘。
    """

    # 返回给前端的明细条数上限：整库扫描可能命中成千上万条，
    # 全量塞进 JSON 会把响应撑到几 MB。计数永远是真实全量，只有明细会被截断。
    MAX_DETAIL = 1000

    def __init__(self):
        # 上次扫描的**全量**结果，供「先预览、后执行」两步式调用复用
        self._last_scan = None

    # ------------------------------------------------------------------ 配置

    @staticmethod
    def get_default_config():
        """
        从配置文件读取默认的根目录与保留策略，供前端预填。
        配置段（config/config.yaml）：
            clean_subs:
              root_path: ''
              keep_policy: quality
              recursive: true
        """
        try:
            conf = Config().get_config("clean_subs") or {}
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            conf = {}
        if not isinstance(conf, dict):
            conf = {}
        # 缺省为 True：含各级子目录
        recursive = conf.get("recursive", True)
        if isinstance(recursive, str):
            recursive = recursive.strip().lower() not in ("false", "0", "no", "off", "")
        else:
            recursive = bool(recursive)
        return {
            "root_path": conf.get("root_path") or "",
            "keep_policy": SubtitleHelper.normalize_keep_policy(conf.get("keep_policy")),
            "recursive": recursive,
        }

    @staticmethod
    def normalize_keep_policy(value, default="quality"):
        """
        归一「保留策略」：非法 / 缺省一律回落 quality（先看格式，再看体积）
        """
        if value is None or value == "":
            return default
        value = str(value).strip().lower()
        return value if value in _KEEP_POLICIES else default

    @staticmethod
    def normalize_recursive(value, default=True):
        """
        归一「是否包含子目录」参数（Web 表单 / REST 传来的可能是 bool 或字符串）
        """
        if value is None or value == "":
            return bool(default)
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "no", "off")
        return bool(value)

    # -------------------------------------------------------------- 文件名解析

    @classmethod
    def classify_language(cls, filename):
        """
        识别字幕文件的语言，返回归一化语言键（多语言字幕如 chs&eng 返回 chs+eng）。
        认不出来返回 None，调用方应回落到 `und`（单独成组，不参与跨语言去重）。
        """
        stem = os.path.splitext(os.path.basename(filename))[0]
        found = []
        # ⚠️ 这里**不能**按 - 切：`zh-TW` 一旦被切成 zh + TW 就会同时命中简繁两档。
        #    - 交给 _segment_langs 内部按词表处理（它认识 zh-tw 这类组合，也认识 chs&eng）。
        for seg in re.split(r"[._ ]", stem):
            for key in _segment_langs(seg):
                if key not in found:
                    found.append(key)
        if not found:
            return None
        return "+".join(key for key in _LANG_ORDER if key in found)

    @classmethod
    def split_identity(cls, filename):
        """
        把字幕文件名拆成 (身份键, 语言键)。

        身份键 = 剥掉尾部「语言标记 / 流序号 / (n) 计数」后的主干；
        剥空时退回原始主干（宁可单独成组，也不和别的文件混在一起）。
        """
        stem = os.path.splitext(os.path.basename(filename))[0]
        lang = cls.classify_language(filename) or "und"
        # 按「段 + 分隔符」切开后从尾部往前剥；只剥 . _ - 空格 这几种分隔符
        parts = re.split(r"([._\- ])", stem)
        while len(parts) >= 3 and parts[-2] in (".", "_", "-", " "):
            if not _is_strippable_segment(parts[-1]):
                break
            parts = parts[:-2]
        identity = "".join(parts).strip(" ._-")
        # 归组时**忽略空白**：同一集在不同来源里的写法可能是「第15集」与「第 15 集」，
        # 集号本身（S01E15 / 第15集）不含空格，去掉空白不会把不同集撞到一起，
        # 却能把这类「只差空格」的写法并进同一组。
        identity = re.sub(r"\s+", "", identity)
        return (identity or re.sub(r"\s+", "", stem)), lang

    @staticmethod
    def language_name(lang_key):
        """语言键 → 展示名（chs+eng → 简体中文+英文）"""
        if not lang_key:
            return _LANG_NAMES["und"]
        parts = [part for part in str(lang_key).split("+") if part]
        return "+".join(_LANG_NAMES.get(part, part) for part in parts)

    # -------------------------------------------------------------- 打分挑选

    @classmethod
    def _rank(cls, item, policy):
        """越大越该留下"""
        ext_rank = _FORMAT_RANK.get(item["ext"], 1)
        name_len = -len(item["name"])          # 文件名更短的优先（少了 (1) 这类尾巴）
        if policy == "size":
            return (item["size_bytes"], ext_rank, name_len, item["name"])
        return (ext_rank, item["size_bytes"], name_len, item["name"])

    # -------------------------------------------------------------- 扫描 / 清理

    @staticmethod
    def _iter_dirs(root_path, recursive, follow_links, skipped):
        """按固定顺序产出待检查目录：根目录自身，以及（可选）各级子目录"""
        yield root_path
        if not recursive:
            return
        for cur, dirs, _ in os.walk(root_path, followlinks=follow_links):
            if not follow_links:
                keep = []
                for name in dirs:
                    full = os.path.join(cur, name)
                    try:
                        if os.path.islink(full):
                            skipped.append({"path": full, "reason": "符号链接目录已跳过"})
                            continue
                    except OSError as err:
                        skipped.append({"path": full, "reason": "链接状态检查失败：%s" % err})
                        continue
                    keep.append(name)
                dirs[:] = sorted(keep)
            else:
                dirs[:] = sorted(dirs)
            for name in dirs:
                yield os.path.join(cur, name)

    def _scan_full(self, root_path, keep_policy, recursive, follow_links):
        """全量扫描（内部用，不做明细截断）"""
        policy = self.normalize_keep_policy(keep_policy)
        result = {
            "root_path": root_path,
            "keep_policy": policy,
            "recursive": bool(recursive),
            "follow_links": bool(follow_links),
            "total_dirs": 0,
            "total_files": 0,
            "groups": [],
            "matched": [],
            "unknown": [],
            "skipped": [],
            "total_free_bytes": 0,
        }

        if not root_path:
            result["error"] = "未指定根目录"
            return result
        if not os.path.exists(root_path):
            result["error"] = "根目录不存在：%s" % root_path
            return result
        if not os.path.isdir(root_path):
            result["error"] = "根路径不是目录：%s" % root_path
            return result
        if os.path.dirname(os.path.abspath(root_path)) == os.path.abspath(root_path):
            result["error"] = "拒绝在文件系统根目录上执行：%s（请指定具体的媒体目录）" % root_path
            return result

        # (目录, 身份键, 语言) -> [文件条目]
        buckets = {}
        for cur_dir in self._iter_dirs(root_path, recursive, follow_links, result["skipped"]):
            result["total_dirs"] += 1
            try:
                entries = sorted(os.listdir(cur_dir))
            except PermissionError as err:
                result["skipped"].append({"path": cur_dir, "reason": "权限不足：%s" % err})
                continue
            except OSError as err:
                result["skipped"].append({"path": cur_dir, "reason": "读取失败：%s" % err})
                continue
            for name in entries:
                ext = os.path.splitext(name)[-1].lower()
                if ext not in SUB_EXT:
                    continue
                full = os.path.join(cur_dir, name)
                try:
                    if os.path.islink(full):
                        result["skipped"].append({"path": full, "reason": "符号链接文件已跳过"})
                        continue
                    if not os.path.isfile(full):
                        result["skipped"].append({"path": full, "reason": "非普通文件已跳过"})
                        continue
                    size = os.path.getsize(full)
                except PermissionError as err:
                    result["skipped"].append({"path": full, "reason": "权限不足：%s" % err})
                    continue
                except OSError as err:
                    result["skipped"].append({"path": full, "reason": "读取失败：%s" % err})
                    continue
                result["total_files"] += 1
                identity, lang = self.split_identity(name)
                buckets.setdefault((cur_dir, identity, lang), []).append({
                    "path": full,
                    "name": name,
                    "dir": cur_dir,
                    "ext": ext,
                    "size_bytes": size,
                    "size_mb": round(size / BYTES_PER_MB, 3),
                })

        groups = []
        for (cur_dir, identity, lang), items in sorted(buckets.items()):
            if len(items) < 2:
                continue
            if lang == "und":
                # 语言认不出来 ⇒ **一律不动**。同一集下「没有语言标记的 .ass 与 .srt」
                # 完全可能是两种不同语言，删掉任何一条都有丢失字幕的风险；
                # 这类只列出来提示用户，交给用户自己改名后再清。
                result["unknown"].append({
                    "dir": cur_dir,
                    "identity": identity,
                    "count": len(items),
                    "files": [it["name"] for it in items],
                })
                continue
            keep = max(items, key=lambda it: self._rank(it, policy))
            remove = [it for it in items if it["path"] != keep["path"]]
            free_bytes = sum(it["size_bytes"] for it in remove)
            groups.append({
                "dir": cur_dir,
                "identity": identity,
                "lang_key": lang,
                "lang": self.language_name(lang),
                "count": len(items),
                "keep": keep,
                "remove": sorted(remove, key=lambda it: it["name"]),
                "free_bytes": free_bytes,
                "free_mb": round(free_bytes / BYTES_PER_MB, 3),
            })
            for it in remove:
                result["matched"].append(dict(it, identity=identity, lang=lang,
                                              keep_path=keep["path"]))
            result["total_free_bytes"] += free_bytes

        # 待删清单按体积升序，便于从最小开始删；分组清单按可释放空间降序，先看大头
        result["matched"].sort(key=lambda x: (x["size_bytes"], x["path"]))
        groups.sort(key=lambda g: (-g["free_bytes"], g["dir"], g["identity"], g["lang_key"]))
        result["group_count"] = len(groups)
        result["unknown_count"] = len(result["unknown"])
        result["groups"] = groups
        return result

    @staticmethod
    def _to_payload(full):
        """把全量结果裁成给前端的载荷（明细截断，计数保真）"""
        payload = dict(full)
        matched = full.get("matched") or []
        groups = full.get("groups") or []
        payload["truncated"] = len(matched) > SubtitleHelper.MAX_DETAIL
        payload["matched"] = matched[:SubtitleHelper.MAX_DETAIL]
        payload["groups"] = groups[:SubtitleHelper.MAX_DETAIL]
        return payload

    def scan(self, root_path, keep_policy="quality", recursive=True, follow_links=False):
        """
        扫描并返回去重清单（不删除任何文件）。

        :return: dict，含 groups（每组保留哪条、删哪些）、matched（待删明细）、
                 skipped（跳过的条目）、total_free_bytes 等
        """
        full = self._scan_full(root_path, keep_policy, recursive, follow_links)
        self._last_scan = full
        return self._to_payload(full)

    def clean(self, root_path, keep_policy="quality", dry_run=True,
              recursive=True, follow_links=False):
        """
        执行清理：先扫描，再按 dry_run 决定是否真正删除。

        :param dry_run: True = 只预览（不删除任何东西）；False = 真正删除多余的副本
        """
        full = self._scan_full(root_path, keep_policy, recursive, follow_links)
        self._last_scan = full
        result = self._to_payload(full)
        result["dry_run"] = bool(dry_run)
        result["deleted"] = []
        result["failed"] = []
        result["deleted_bytes"] = 0

        if full.get("error"):
            return result
        if dry_run:
            # 预览模式：不落盘，只回清单
            return result

        for item in full["matched"]:
            target = item["path"]
            try:
                # 删除前二次确认仍是普通文件：扫描与实际删除之间文件可能已被别的链路挪走
                if not os.path.exists(target):
                    result["failed"].append({"path": target, "reason": "文件已不存在"})
                    continue
                if not os.path.isfile(target) or os.path.islink(target):
                    result["failed"].append({"path": target, "reason": "不再是普通文件，已跳过"})
                    continue
                os.remove(target)
                result["deleted"].append({
                    "path": target,
                    "name": item["name"],
                    "dir": item["dir"],
                    "lang": item["lang"],
                    "keep_path": item.get("keep_path"),
                    "size_bytes": item["size_bytes"],
                    "size_mb": item["size_mb"],
                })
                result["deleted_bytes"] += item["size_bytes"]
                log.info("【Sub】已删除多余字幕：%s（保留 %s）" % (target, item.get("keep_path")))
            except PermissionError as err:
                reason = "权限不足：%s" % err
                result["failed"].append({"path": target, "reason": reason})
                log.error("【Sub】删除字幕失败 %s：%s" % (target, reason))
            except OSError as err:
                reason = "删除失败（可能被占用）：%s" % err
                result["failed"].append({"path": target, "reason": reason})
                log.error("【Sub】删除字幕失败 %s：%s" % (target, reason))
            except Exception as err:  # noqa: BLE001 兜底，不中断整体流程
                ExceptionUtils.exception_traceback(err)
                reason = "未知异常：%s" % err
                result["failed"].append({"path": target, "reason": reason})
                log.error("【Sub】删除字幕失败 %s：%s" % (target, reason))

        result["deleted_count"] = len(result["deleted"])
        result["failed_count"] = len(result["failed"])
        return result

    @staticmethod
    def format_result_message(result):
        """生成人类可读的清理结果摘要"""
        if result.get("error"):
            return result["error"]
        scope = "根目录及其各级子目录" if result.get("recursive") else "根目录自身"
        policy = "优先保留 ASS/SSA，同格式留体积大的" if result.get("keep_policy") == "quality" \
            else "一律保留体积最大的"
        if result.get("dry_run") or "deleted" not in result:
            released_mb = round(result.get("total_free_bytes", 0) / BYTES_PER_MB, 2)
            unknown = result.get("unknown_count", 0)
            tail = "；另有 %d 组未标注语言，已跳过（可手动改名后再清）" % unknown if unknown else ""
            return ("预览完成：检查 %s 的 %d 个目录、%d 个字幕文件，"
                    "其中 %d 组存在重复（共 %d 条多余），预计可释放 %s MB（未执行删除）%s"
                    % (scope, result.get("total_dirs", 0), result.get("total_files", 0),
                       result.get("group_count", 0), len(result.get("matched", [])),
                       released_mb, tail))
        released = round(result.get("deleted_bytes", 0) / BYTES_PER_MB, 2)
        return ("清理完成：删除 %d 条多余字幕，实际释放 %s MB，失败 %d 条（保留策略：%s）"
                % (result.get("deleted_count", 0), released,
                   result.get("failed_count", 0), policy))
