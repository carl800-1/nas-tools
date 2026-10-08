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
# 排序只用于「同一类里挑一条留下」时打分，不影响要不要删。
_FORMAT_RANK = {".ass": 3, ".ssa": 3, ".srt": 2}

# 保留策略：quality = 先看格式再看体积（默认）；size = 一律留体积最大的
_KEEP_POLICIES = ("quality", "size")

# ─────────────────────────── 保留规则（用户 2026-10-08 明确） ───────────────────────────
#
#   每集（电影则每部）**只保留这三类**：
#     ① 中文（简体）        —— 同类里留 1 条
#     ② 该剧原语言          —— 同类里留 1 条
#     ③ 中 × 原语言 双语    —— 按「上排语言」区分形态，每种形态留 1 条，最多 2 条
#
#   **除此之外一律删除，而且是「全删」（单条也删，不做去重判断）**：
#     · 其它语言（日语 / 韩语 / 法语 …，原语言不是它的话）
#     · 繁体中文（用户明确要求删除）
#     · 无法识别语言的字幕
#     · 与中文/原语言无关的双语组合（如美剧里的「中日对照」）
#
# 例：美剧（原语言 = 英语）目录下
#   简体中文×3 → 留 1；繁体中文 → 全删；纯英文×2 → 留 1；纯日文/韩文 → 全删；
#   中英双语（上中下英）→ 留 1；中英双语（上英下中）→ 留 1（另一形态）；
#   中日双语 / 英日双语 → 全删；无语言标记 → 全删。
_MAX_BILINGUAL_FORMS = 2

# 原语言识别不出来时的兜底（用户：取不到就默认英语）
_DEFAULT_ORIGINAL_LANG = "eng"

# ─────────────────────────── 语言词表 ───────────────────────────
# 拉丁标记 → 归一化语言键。口径对齐 app/filetransfer.py 的 __transfer_subtitles
# （那边判定顺序是「先简体、再繁体、后英文」），免得同一份字幕在两处结论打架。
_LANG_TOKENS = (
    ("chs", ("chs", "zh", "zho", "zhcn", "zhsg", "zhhans", "cn", "sc", "sg", "gb", "chinese")),
    ("cht", ("cht", "tw", "hk", "tc", "zhtw", "zhhk", "zhmo", "zhhant", "big5")),
    ("eng", ("eng", "en", "english")),
    ("jpn", ("jpn", "jp", "ja", "japanese")),
    ("kor", ("kor", "ko", "korean")),
)
_LANG_ORDER = [key for key, _ in _LANG_TOKENS]
_TOKEN_2_LANG = {tok: key for key, toks in _LANG_TOKENS for tok in toks}

# 「zh-xx」这类带地区后缀的写法必须**整段**判定，不能按 - 切开：
# 切开后前半段是 zh，会被当成简体，`X.zh-TW.srt` 就变成「简体+繁体」而躲过归类。
_ZH_COMPOUNDS = (
    ("zh-hans", "zhhans"), ("zh-hant", "zhhant"),
    ("zh-cn", "zhcn"), ("zh-sg", "zhsg"), ("zh-my", "zhsg"),
    ("zh-tw", "zhtw"), ("zh-hk", "zhhk"), ("zh-mo", "zhmo"),
)

# 中文里的「双语组合」写法 → 语言键（**按出现顺序**返回，形态回退要用到顺序）。
# 放在单词词表之前判定，否则「中英」会被「中文」先吃掉一半。
_CN_COMBO_RES = (
    (r"中英|简英|中英双语", ("chs", "eng")),
    (r"英中|英简", ("eng", "chs")),
    (r"中日|简日", ("chs", "jpn")),
    (r"日中", ("jpn", "chs")),
    (r"中韩|简韩", ("chs", "kor")),
    (r"韩中", ("kor", "chs")),
)

# 中文语言词（在**单个文件名段落内部**做子串识别）。
# ⚠️ 繁体的判定必须压在简体之前：`繁体中文` 里含「中文」，若先让简体命中就会被
#    错认成简繁混合；这里用「前面不是繁/簡/简」的回溯把裸「中文」限定成简体。
_CN_WORD_RES = (
    ("cht", r"繁体|繁中|台繁|港繁|粵語繁|粤语繁|繁體"),
    # 裸「中文」限定为简体，两个条件同时成立才算：
    #   · 前面不是 繁/簡/简/体/體（挡住「繁体中文」「繁體中文」）
    #   · 后面不再出现「繁」（挡住「中文繁體」这种倒装写法）
    ("chs", r"简体|简中|中字|国语|國語|简繁|(?<![繁簡简体體])(?!.*繁)中文"),
    ("eng", r"英文|英语|英語"),
    ("jpn", r"日文|日语|日語"),
    ("kor", r"韩文|韩语|韓語"),
)

# 「双语」这种不写清是哪两门的写法：解析成 {中文, 该剧原语言}
_GENERIC_BILINGUAL = "bi"

_LANG_NAMES = {"chs": "简体中文", "cht": "繁体中文", "eng": "英文", "jpn": "日语",
               "kor": "韩语", "und": "未标注"}

# 内容判定用（哪门语言排在上面）
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_ASS_BREAK_RE = re.compile(r"\\N|\\n|<br\s*/?>", re.I)

# 字幕文件只读头部这么多字节来判断「上排语言」，避免大文件整份读入
_CONTENT_SNIFF_BYTES = 64 * 1024

# 原始语言（TMDB ISO 639-1）→ 归一化语言键。
# 只认这五种：其余语种（法语/德语/泰语…）本工具没有词表可归类，TMDB 结果按「查不到」处理，
# 回落到「文件推断 → 英语」，不会把整部剧的字幕误删。
_TMDB_ISO_MAP = {"zh": "chs", "cn": "chs", "en": "eng", "ja": "jpn", "ko": "kor"}

# 「季 / 特别篇 / 光盘」这类目录名 —— 原语言按它们的**上一层**（剧名目录）判定
_SEASON_DIR_RE = re.compile(
    r"^(?:season\s*\d+|s\s*\d+|specials?|extras?|featurettes?|"
    r"cd\s*\d+|disc\s*\d+|dvd\s*\d+|part\s*\d+|"
    r"第[0-9０-９一二三四五六七八九十百零]+季)$", re.I)

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_RELEASE_NOISE_RE = re.compile(
    r"\b(?:s\d{1,2}(?:e\d{1,3})?|e\d{1,3}|season\s*\d+|complete|bluray|blu-ray|"
    r"web-?dl|webrip|hdrip|bdrip|remux|hdtv|dvdrip|x264|x265|h\.?264|h\.?265|hevc|"
    r"avc|aac|ac3|dts|ddp?5[\.\s]?1|\d{3,4}p|4k|8k|uhd|hdr|dovi|dv|10bit|hq|"
    r"repack|proper|internal|multi|chs|cht|eng|jpn|kor)\b", re.I)


def _clean_segment(seg):
    """去掉 (1) / [1] 这类计数后缀并转小写，返回可用于比对的段落"""
    return re.sub(r"[\(\[][^\)\]]*[\)\]]", "", str(seg)).strip().lower()


def _segment_langs(seg):
    """一个文件名段落里出现的全部语言键（按出现先后返回，去重）"""
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

    def _add(keys):
        for key in keys:
            if key not in found:
                found.append(key)

    # ① 中文双语组合（中英 / 英中 / 中日…）优先，必须压过下面的单词词表
    for pattern, keys in _CN_COMBO_RES:
        if re.search(pattern, cleaned):
            _add(keys)
    # ② 泛双语（不写清哪两门）→ 交给该剧原语言解析
    if re.search(r"双语|雙語", cleaned):
        _add([_GENERIC_BILINGUAL])
    # ③ 拉丁语言标记：按 & - _ + 空格再切一次，覆盖 chs&eng / chs-jpn
    for token in re.split(r"[&\-_+\s]+", cleaned):
        key = _TOKEN_2_LANG.get(token)
        if key:
            _add([key])
    # ④ 中文语言词
    for key, pattern in _CN_WORD_RES:
        if re.search(pattern, cleaned):
            _add([key])
    return found


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


def _dominant_lang(text):
    """一段对白文本里「排在最上面」的那门语言"""
    if not text:
        return None
    # 假名 / 谚文一出现就基本锁定语种，优先级高于汉字
    if _KANA_RE.search(text):
        return "jpn"
    if _HANGUL_RE.search(text):
        return "kor"
    han = len(_HAN_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if han and han >= latin:
        return "chs"
    if latin:
        return "eng"
    if han:
        return "chs"
    return None


class SubtitleHelper:
    """
    字幕清理服务（v6.9.0 规则）：

    对指定根目录下的字幕做「按剧集白名单 + 分类去重」，**每个视频只保留三类字幕**：

      ① 中文（简体）      同类留 1 条
      ② 该剧原语言        同类留 1 条
      ③ 中 × 原语言 双语  按「上排语言」分形态，每形态留 1 条，最多 2 条

    **其余一律删除（全删，单条也删）**：其它语言、繁体中文、无法识别语言的字幕、
    以及与本剧无关的双语组合。

    设计要点：

      1. 归组口径 = (所在目录, 身份键)。身份键 = 文件名剥掉尾部「语言标记 / 流序号 /
         (n) 计数」后的主干：`X.S01E15.chs.ass`、`X.S01E15.chs.简体中文(1).srt`、
         `X.S01E15.8.zh-CN.srt` 都是 `X.S01E15`，同一集。目录不同不归一组。
      2. 原语言（该剧说的语言）**自动判定**，三级兜底：
         a) 按「剧名目录」名去 TMDB 查 original_language；
         b) TMDB 无结果 → 按该剧目录里**实际出现的语言**推断（除中文外出现最多的那门）；
         c) 仍无 → 英语。
         判定单位见 `_show_dir()`：季目录（Season 1 / 第2季）向上归到剧名目录。
      3. 双语「形态」以**字幕内容**为准：取前若干条对白，看哪门语言排在上面
         （.srt 取首个非空文本行；.ass 取首个 Dialogue 行并按 \\N 切第一段）。
         内容读不出来时回退到文件名里语言标记的先后顺序。
      4. 保留策略 keep_policy：
         * `quality`（默认）—— 先比格式（ASS/SSA 优于 SRT），同格式比体积；
         * `size`            —— 一律留体积最大的，同体积比格式。
         并列项按「文件名更短者优先」再按字典序兜底，保证结果可复现。
      5. 支持 dry_run：只扫描并返回清单，不落盘删除。
      6. 符号链接（目录与文件）默认跳过且不跟随；权限不足、文件被占用等异常逐条记录
         后跳过，不中断整体流程。
      7. root_path 为文件系统根目录（`/` 或盘符根，例如 C 盘根）时直接拒绝 ——
         一次误操作会横扫整个盘。
      8. 繁体中文（cht）按用户要求**一律删除**，不参与「中文」的去重。
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
        识别字幕文件的语言，返回归一化语言键（多语言字幕保持**出现顺序**，
        如 chs&eng 返回 `chs+eng`、eng&chs 返回 `eng+chs`；不写清哪两门的
        「双语」返回 `bi`）。认不出来返回 None，调用方按「无法识别 → 全删」处理。
        """
        stem = os.path.splitext(os.path.basename(filename))[0]
        found = []
        # ⚠️ 这里**不能**按 - 切：`zh-TW` 一旦被切成 zh + TW 就会同时命中简繁两档。
        for seg in re.split(r"[._ ]", stem):
            for key in _segment_langs(seg):
                if key not in found:
                    found.append(key)
        if not found:
            return None
        if found == [_GENERIC_BILINGUAL]:
            return _GENERIC_BILINGUAL
        return "+".join(key for key in found if key != _GENERIC_BILINGUAL)

    @classmethod
    def split_identity(cls, filename):
        """
        把字幕文件名拆成 (身份键, 语言键)。

        身份键 = 剥掉尾部「语言标记 / 流序号 / (n) 计数」后的主干；
        剥空时退回原始主干（宁可单独成组，也不和别的文件混在一起）。
        """
        stem = os.path.splitext(os.path.basename(filename))[0]
        lang = cls.classify_language(filename)
        # 按「段 + 分隔符」切开后从尾部往前剥；只剥 . _ - 空格 这几种分隔符
        parts = re.split(r"([._\- ])", stem)
        while len(parts) >= 3 and parts[-2] in (".", "_", "-", " "):
            if not _is_strippable_segment(parts[-1]):
                break
            parts = parts[:-2]
        identity = "".join(parts).strip(" ._-")
        # 归组时**忽略空白**：同一集在不同来源里的写法可能是「第15集」与「第 15 集」，
        # 集号本身（S01E15 / 第15集）不含空格，去掉空白不会把不同集撞到一起。
        identity = re.sub(r"\s+", "", identity)
        return (identity or re.sub(r"\s+", "", stem)), lang

    @staticmethod
    def language_name(lang_key):
        """语言键 → 展示名（chs+eng → 简体中文+英文）"""
        if not lang_key:
            return _LANG_NAMES["und"]
        if lang_key == _GENERIC_BILINGUAL:
            return "双语"
        parts = [part for part in str(lang_key).split("+") if part]
        return "+".join(_LANG_NAMES.get(part, part) for part in parts)

    @classmethod
    def _lang_set_of(cls, lang_key, original_lang):
        """文件名语言键 → 语言集合（`bi` 按该剧原语言展开成 {中文, 原语言}）"""
        if not lang_key:
            return set()
        if lang_key == _GENERIC_BILINGUAL:
            return {"chs", original_lang}
        return set(lang_key.split("+"))

    # -------------------------------------------------------------- 原语言识别

    @staticmethod
    def _show_dir(file_dir, root_path):
        """
        文件所在目录向上归到「剧名目录」：季目录（Season 1 / 第2季 / CD1）不算剧名。
        始终不超过 root_path。
        """
        root_abs = os.path.abspath(root_path)
        cur = os.path.abspath(file_dir)
        while cur != root_abs:
            if not _SEASON_DIR_RE.match(os.path.basename(cur).strip()):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur or not (parent == root_abs or parent.startswith(root_abs + os.sep)):
                break
            cur = parent
        return root_abs

    @staticmethod
    def _split_title_year(dirname):
        """目录名 → (剧名, 年份)，供 TMDB 查询用"""
        name = str(dirname or "")
        year = None
        m = _YEAR_RE.search(name)
        if m:
            year = m.group(1)
            name = name[:m.start()] + " " + name[m.end():]
        name = re.sub(r"[\[\]\(\)\{\}]", " ", name)
        name = _RELEASE_NOISE_RE.sub(" ", name)
        name = re.sub(r"[\._]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" -_.·")
        return (name or str(dirname or "").strip(), year)

    @staticmethod
    def _tmdb_original_language(title, year=None):
        """
        按剧名查 TMDB 的 original_language（ISO 639-1），映射到本工具的语言键。
        查不到 / 未配置 API Key / 网络异常 / 语种不在词表内，一律返回 None（转下一级兜底）。
        返回 (语言键, TMDB 命中的标题)。
        """
        if not title:
            return None, None
        try:
            # 延迟导入：Media 会拉起 TMDB / scraper 一整套依赖，
            # 放在函数里能保证本模块在离线环境下仍可单独导入与测试。
            from app.media import Media
        except Exception as err:  # noqa: BLE001
            ExceptionUtils.exception_traceback(err)
            return None, None
        try:
            search = getattr(Media(), "search", None)
            if not search:
                return None, None
            results = search.multi({"query": title}) or []
        except Exception as err:  # noqa: BLE001
            ExceptionUtils.exception_traceback(err)
            return None, None
        cands = [r for r in results if r.get("media_type") in ("movie", "tv")]
        if not cands:
            return None, None
        if year:
            exact = [r for r in cands
                     if str(r.get("release_date") or r.get("first_air_date") or "")[:4] == str(year)]
            if exact:
                cands = exact
        picked = cands[0]
        iso = str(picked.get("original_language") or "").strip().lower()
        key = _TMDB_ISO_MAP.get(iso)
        if not key:
            return None, None
        return key, (picked.get("title") or picked.get("name") or "")

    @classmethod
    def _detect_original_language(cls, show_dir, show_langs, cache):
        """
        判定一部剧/一部电影的「原语言」：TMDB → 文件推断 → 英语。
        :param show_langs: 该剧目录下出现过的语言计数 {lang_key: 文件数}（不含中文/繁体/双语）
        """
        if show_dir in cache:
            return cache[show_dir]
        title, year = cls._split_title_year(os.path.basename(show_dir))
        tmdb_lang, tmdb_title = cls._tmdb_original_language(title, year)
        if tmdb_lang:
            info = {"original_language": tmdb_lang, "source": "tmdb",
                    "query": title, "matched": tmdb_title}
        elif show_langs:
            best = sorted(show_langs.items(),
                          key=lambda kv: (-kv[1],
                                          _LANG_ORDER.index(kv[0]) if kv[0] in _LANG_ORDER else 99))[0][0]
            info = {"original_language": best, "source": "files", "query": title, "matched": ""}
        else:
            info = {"original_language": _DEFAULT_ORIGINAL_LANG, "source": "default",
                    "query": title, "matched": ""}
        cache[show_dir] = info
        return info

    # -------------------------------------------------------------- 内容嗅探

    @staticmethod
    def _read_head(path):
        """只读文件头部若干字节，按常见编码猜一份文本出来（读不到返回 None）"""
        data = None
        try:
            with open(path, "rb") as f:
                data = f.read(_CONTENT_SNIFF_BYTES)
        except OSError:
            return None
        if not data:
            return None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="ignore")

    @classmethod
    def _content_top_language(cls, path):
        """
        读字幕内容判断「排在上面」的是哪门语言（用户要求：双语形态按内容区分）。
        取首个有效对白行：.srt 取首个非空且非序号/非时间轴的文本行；
        .ass/.ssa 取首个 Dialogue 行，去掉 {样式} 后按 \\N 取第一段。
        判定不出来返回 None（调用方回退到文件名里的语言先后）。
        """
        text = cls._read_head(path)
        if not text:
            return None
        ext = os.path.splitext(path)[-1].lower()
        if ext in (".ass", ".ssa"):
            for line in text.splitlines():
                if not line.startswith("Dialogue:"):
                    continue
                field = line.split(",", 9)
                if len(field) < 10:
                    continue
                body = _ASS_TAG_RE.sub("", field[9])
                body = _ASS_BREAK_RE.split(body)[0]
                lang = _dominant_lang(body)
                if lang:
                    return lang
            return None
        for line in text.splitlines():
            seg = line.strip()
            if not seg or seg.isdigit() or "-->" in seg:
                continue
            # 去掉常见的 srt 内联标签
            seg = _ASS_TAG_RE.sub("", seg)
            lang = _dominant_lang(seg)
            if lang:
                return lang
        return None

    @classmethod
    def _file_form(cls, item, original_lang):
        """
        双语字幕的「形态」键：先读内容判上排语言，读不出来回退到文件名里的语言先后。
        """
        top = cls._content_top_language(item["path"])
        if top in item["langs"]:
            return top
        return "+".join(item["ordered"]) or "unknown"

    # -------------------------------------------------------------- 打分挑选

    @classmethod
    def _rank(cls, item, policy):
        """越大越该留下"""
        ext_rank = _FORMAT_RANK.get(item["ext"], 1)
        name_len = -len(item["name"])          # 文件名更短的优先（少了 (1) 这类尾巴）
        if policy == "size":
            return (item["size_bytes"], ext_rank, name_len, item["name"])
        return (ext_rank, item["size_bytes"], name_len, item["name"])

    # -------------------------------------------------------------- 目录遍历

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

    # -------------------------------------------------------------- 扫描 / 清理

    def _collect_files(self, root_path, recursive, follow_links, skipped):
        """收集全部字幕文件条目（按目录名、文件名稳定排序）"""
        files = []
        total_dirs = 0
        for cur_dir in self._iter_dirs(root_path, recursive, follow_links, skipped):
            total_dirs += 1
            try:
                entries = sorted(os.listdir(cur_dir))
            except PermissionError as err:
                skipped.append({"path": cur_dir, "reason": "权限不足：%s" % err})
                continue
            except OSError as err:
                skipped.append({"path": cur_dir, "reason": "读取失败：%s" % err})
                continue
            for name in entries:
                ext = os.path.splitext(name)[-1].lower()
                if ext not in SUB_EXT:
                    continue
                full = os.path.join(cur_dir, name)
                try:
                    if os.path.islink(full):
                        skipped.append({"path": full, "reason": "符号链接文件已跳过"})
                        continue
                    if not os.path.isfile(full):
                        skipped.append({"path": full, "reason": "非普通文件已跳过"})
                        continue
                    size = os.path.getsize(full)
                except PermissionError as err:
                    skipped.append({"path": full, "reason": "权限不足：%s" % err})
                    continue
                except OSError as err:
                    skipped.append({"path": full, "reason": "读取失败：%s" % err})
                    continue
                files.append({"dir": cur_dir, "name": name, "path": full,
                              "ext": ext, "size_bytes": size,
                              "size_mb": round(size / BYTES_PER_MB, 3)})
        return files, total_dirs

    @staticmethod
    def _reject_reason(langs):
        """非符合项为什么被删（给前端看的原因文案）"""
        if not langs:
            return "无法识别语言，不保留"
        if "cht" in langs:
            return "繁体中文，不保留"
        return "不属于中文/原语言/中×原语言双语，不保留"

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
            "scopes": [],
            "groups": [],
            "matched": [],
            "rejected": [],
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

        files, total_dirs = self._collect_files(root_path, recursive, follow_links,
                                                result["skipped"])
        result["total_dirs"] = total_dirs
        result["total_files"] = len(files)
        if not files:
            result["kept_count"] = 0
            result["remove_count"] = 0
            result["reject_count"] = 0
            result["group_count"] = 0
            return result

        # ① 解析文件名：身份键 + 语言键（"bi" 先原样保留，等原语言确定后再展开）
        for f in files:
            identity, lang_key = self.split_identity(f["name"])
            f["identity"] = identity
            f["lang_key"] = lang_key

        # ② 按「剧名目录」聚合语言分布，逐剧判定原语言
        by_show = {}
        for f in files:
            by_show.setdefault(self._show_dir(f["dir"], root_path), []).append(f)
        cache = {}
        show_meta = {}
        for show_dir, show_files in sorted(by_show.items()):
            lang_count = {}
            for f in show_files:
                for key in self._lang_set_of(f["lang_key"], ""):
                    if key in ("chs", "cht"):
                        continue
                    lang_count[key] = lang_count.get(key, 0) + 1
            meta = self._detect_original_language(show_dir, lang_count, cache)
            show_meta[show_dir] = meta
            result["scopes"].append({
                "dir": show_dir,
                "name": os.path.basename(show_dir) or show_dir,
                "original_language": meta["original_language"],
                "lang_name": self.language_name(meta["original_language"]),
                "source": meta["source"],
                "query": meta["query"],
                "matched": meta.get("matched") or "",
                "files": len(show_files),
            })

        # ③ 逐条定类：中文 / 原语言 / 中×原语言双语 / 其它（全删）
        for f in files:
            orig = show_meta[self._show_dir(f["dir"], root_path)]["original_language"]
            f["original_language"] = orig
            if f["lang_key"] == _GENERIC_BILINGUAL:
                f["langs"] = {"chs", orig}
                f["ordered"] = ["chs", orig]
            else:
                f["langs"] = set(str(f["lang_key"] or "").split("+")) - {""}
                f["ordered"] = [k for k in str(f["lang_key"] or "").split("+") if k]
            if not f["langs"]:
                f["category"] = "reject"
            elif f["langs"] == {"chs"}:
                f["category"] = "chs"
            elif f["langs"] == {orig}:
                f["category"] = "orig"
            elif f["langs"] == {"chs", orig}:
                f["category"] = "bilingual"
            else:
                f["category"] = "reject"
            f["lang"] = "+".join(_LANG_NAMES.get(k, k) for k in f["ordered"]) or _LANG_NAMES["und"]

        # ④ 逐 (目录, 身份键) 归组，按白名单保留
        buckets = {}
        for f in files:
            buckets.setdefault((f["dir"], f["identity"]), []).append(f)

        groups = []
        matched = []
        kept_count = 0
        for (cur_dir, identity), items in sorted(buckets.items()):
            keeps = []
            removes = []

            def _take(bucket, keep_n=1):
                """同类里留 keep_n 条，其余进删除清单"""
                nonlocal kept_count
                if not bucket:
                    return
                ordered = sorted(bucket, key=lambda it: self._rank(it, policy), reverse=True)
                for it in ordered[:keep_n]:
                    keeps.append(it)
                    kept_count += 1
                for it in ordered[keep_n:]:
                    removes.append((it, "同类多余，仅保留 1 条"))

            _take([f for f in items if f["category"] == "chs"])
            # 原语言就是中文时，「中文」与「原语言」是同一类，不要去重两次
            if items and items[0]["original_language"] != "chs":
                _take([f for f in items if f["category"] == "orig"])

            # 双语：按内容形态分组，每形态留 1 条，最多 _MAX_BILINGUAL_FORMS 种形态
            bil = [f for f in items if f["category"] == "bilingual"]
            if bil:
                forms = {}
                for f in bil:
                    f["form"] = self._file_form(f, items[0]["original_language"])
                    forms.setdefault(f["form"], []).append(f)
                keep_forms = sorted(
                    forms.values(),
                    key=lambda fl: self._rank(sorted(fl, key=lambda it: self._rank(it, policy),
                                                     reverse=True)[0], policy),
                    reverse=True)[:_MAX_BILINGUAL_FORMS]
                keep_set = {id(f) for fl in keep_forms for f in
                            sorted(fl, key=lambda it: self._rank(it, policy), reverse=True)[:1]}
                for f in bil:
                    if id(f) in keep_set:
                        keeps.append(f)
                        kept_count += 1
                    else:
                        removes.append((f, "双语同形态重复，仅保留 1 条"))

            # 其余一律全删（不做重复判断）
            for f in items:
                if f["category"] == "reject":
                    removes.append((f, self._reject_reason(f["langs"])))

            if not removes:
                continue
            keep_by_cat = {}
            for k in keeps:
                key = "bilingual" if k["category"] == "bilingual" else k["category"]
                keep_by_cat.setdefault(key, []).append(k)
            remove_bytes = sum(f["size_bytes"] for f, _ in removes)
            groups.append({
                "dir": cur_dir,
                "identity": identity,
                "original_language": items[0]["original_language"],
                "keep": keeps[0] if keeps else None,          # 兼容旧字段：取第一条保留项
                "keeps": [{"path": k["path"], "name": k["name"], "lang": k["lang"],
                           "category": k["category"],
                           "form": k.get("form", ""),
                           "dir": k["dir"], "size_bytes": k["size_bytes"],
                           "size_mb": k["size_mb"]} for k in keeps],
                "remove": [{"path": f["path"], "name": f["name"], "lang": f["lang"],
                            "category": f["category"], "reason": reason,
                            "dir": f["dir"], "size_bytes": f["size_bytes"],
                            "size_mb": f["size_mb"]} for f, reason in
                           sorted(removes, key=lambda rf: (rf[0]["size_bytes"], rf[0]["path"]))],
                "remove_bytes": remove_bytes,
                "remove_mb": round(remove_bytes / BYTES_PER_MB, 3),
                "all_removed": not keeps,
            })
            for f, reason in removes:
                keep_path = None
                same_cat = keep_by_cat.get("bilingual" if f["category"] == "bilingual"
                                           else f["category"]) or []
                if same_cat:
                    keep_path = same_cat[0]["path"]
                entry = {
                    "path": f["path"], "name": f["name"], "dir": f["dir"],
                    "lang": f["lang"], "category": f["category"],
                    "reason": reason, "keep_path": keep_path,
                    "size_bytes": f["size_bytes"], "size_mb": f["size_mb"],
                }
                matched.append(entry)
                if f["category"] == "reject":
                    result["rejected"].append(entry)
            result["total_free_bytes"] += remove_bytes

        # 待删清单按体积升序，便于从最小开始删；分组清单按可释放空间降序，先看大头
        matched.sort(key=lambda x: (x["size_bytes"], x["path"]))
        groups.sort(key=lambda g: (-g["remove_bytes"], g["dir"], g["identity"]))
        result["group_count"] = len(groups)
        result["groups"] = groups
        result["matched"] = matched
        result["remove_count"] = len(matched)
        result["reject_count"] = len(result["rejected"])
        result["kept_count"] = kept_count
        return result

    @staticmethod
    def _to_payload(full):
        """把全量结果裁成给前端的载荷（明细截断，计数保真）"""
        payload = dict(full)
        matched = full.get("matched") or []
        groups = full.get("groups") or []
        rejected = full.get("rejected") or []
        payload["truncated"] = (len(matched) > SubtitleHelper.MAX_DETAIL
                                or len(groups) > SubtitleHelper.MAX_DETAIL)
        payload["matched"] = matched[:SubtitleHelper.MAX_DETAIL]
        payload["groups"] = groups[:SubtitleHelper.MAX_DETAIL]
        payload["rejected"] = rejected[:SubtitleHelper.MAX_DETAIL]
        return payload

    def scan(self, root_path, keep_policy="quality", recursive=True, follow_links=False):
        """
        扫描并返回清理清单（不删除任何文件）。

        :return: dict，含 scopes（每部剧识别到的原语言）、groups（每集保留哪些、删哪些）、
                 matched（全部待删明细）、rejected（非符合项明细）、skipped 等
        """
        full = self._scan_full(root_path, keep_policy, recursive, follow_links)
        self._last_scan = full
        return self._to_payload(full)

    def clean(self, root_path, keep_policy="quality", dry_run=True,
              recursive=True, follow_links=False):
        """
        执行清理：先扫描，再按 dry_run 决定是否真正删除。

        :param dry_run: True = 只预览（不删除任何东西）；False = 真正删除
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
                keep_path = item.get("keep_path")
                result["deleted"].append({
                    "path": target,
                    "name": item["name"],
                    "dir": item["dir"],
                    "lang": item["lang"],
                    "category": item["category"],
                    "reason": item.get("reason") or "",
                    "keep_path": keep_path,
                    "size_bytes": item["size_bytes"],
                    "size_mb": item["size_mb"],
                })
                result["deleted_bytes"] += item["size_bytes"]
                log.info("【Sub】已删除字幕：%s（%s%s）"
                         % (target, item.get("reason") or "",
                            "；保留 %s" % keep_path if keep_path else ""))
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
        scopes = result.get("scopes") or []
        if len(scopes) == 1:
            head = "（原语言：%s，%s）" % (scopes[0]["lang_name"],
                                          {"tmdb": "TMDB 识别", "files": "按目录内语言推断",
                                           "default": "默认"}[scopes[0]["source"]])
        elif scopes:
            head = "（%d 部剧，原语言已逐部自动识别）" % len(scopes)
        else:
            head = ""
        if result.get("dry_run") or "deleted" not in result:
            released_mb = round(result.get("total_free_bytes", 0) / BYTES_PER_MB, 2)
            reject = result.get("reject_count", 0)
            tail = "；其中 %d 条属于「其它语言/繁体/无法识别」，将全部删除" % reject if reject else ""
            return ("预览完成：检查 %s 的 %d 个目录、%d 个字幕文件%s，"
                    "保留 %d 条、删除 %d 条，预计可释放 %s MB（未执行删除）%s"
                    % (scope, result.get("total_dirs", 0), result.get("total_files", 0), head,
                       result.get("kept_count", 0), result.get("remove_count", 0),
                       released_mb, tail))
        released = round(result.get("deleted_bytes", 0) / BYTES_PER_MB, 2)
        return ("清理完成%s：删除 %d 条，实际释放 %s MB，失败 %d 条（保留策略：%s）"
                % (head, result.get("deleted_count", 0), released,
                   result.get("failed_count", 0), policy))
