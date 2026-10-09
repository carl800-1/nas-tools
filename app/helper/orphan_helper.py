"""
媒体库残留清理（服务 → 媒体库残留清理）

场景：在媒体服务器（Emby / Jellyfin / Plex / 飞牛影视 / 绿联影视）里删除了影片，
但媒体库目录下的文件夹仍留在磁盘上，越积越多。

本模块以「媒体服务器**实时**条目」为准，扫描媒体库根目录下的一级子文件夹，
把「文件系统里有、媒体服务器里查不到」的目录列为残留，供人工确认后删除。

安全约束（务必遵守）：
  1. **拿不到媒体服务器条目时绝不产生清单**（否则整个媒体库都会被判成残留），
     此时直接返回 error，matched 恒为空；
  2. 只处理扫描根目录下的**一级子文件夹**（一部电影 / 一部剧一个目录），
     根目录本身与根目录下散落的文件不受影响；
  3. 匹配顺序 **路径优先 → 片名兜底**，任一命中即视为「媒体服务器里还在」——
     宁可漏判为「还在」，绝不误判为「残留」；
  4. 容器内外挂载点不一致时，用「尾部路径段」容错比对；媒体服务器报出的
     媒体库目录还要经 map_server_path() **翻译成本环境可访问的路径**
     （`/vol3/1000/video/...` → `/video/...`），否则会一律显示「不可访问」，
     进而拿着错误的默认目录去扫（整层目录被误判成残留）；
  5. **媒体库目录本身、以及任何「现存条目的上级目录」都不算残留** ——
     扫描根若比媒体库目录浅一层（例如选到了分类层的上一层），
     被扫到的其实是媒体库目录，必须保留；
  6. 符号链接一律跳过；单条异常只记录、不中断整体流程；
  7. 预览（dry_run）与执行分离，执行由前端二次确认。
"""

import json
import os
import re
import shutil

import log
from app.utils import ExceptionUtils
from config import Config

# 1MB = 1024 x 1024 字节（与目录清理保持一致的口径）
BYTES_PER_MB = 1024 * 1024

# 路径「尾部段」容错比对：最多比较的路径段数
_MAX_TAIL_SEG = 4
# 至少要比较 2 段路径，避免单段（仅片名）过松误命中
_MIN_TAIL_SEG = 2

# 目录名里的年份：取最后一个 4 位年份（避免「银翼杀手2049 (2017)」把 2049 当年份）
_YEAR_RE = re.compile(r'(19\d{2}|20\d{2})')
# 片名归一化：抹掉空白与常见标点，只留内容
_TITLE_NOISE_RE = re.compile(
    r'[\s\u3000\.\-_·:：,，;；!！?？\'"“”‘’\(\)\[\]{}<>《》【】（）]+')


class OrphanHelper:
    """
    媒体库残留清理服务。
    """

    # 媒体服务器类型 → 展示名（与 MediaServerType 枚举保持一致）
    _SERVER_NAMES = {
        "emby": "Emby",
        "jellyfin": "Jellyfin",
        "plex": "Plex",
        "ugreen": "绿联影视",
        "trimemedia": "飞牛影视",
    }

    def __init__(self):
        # 上次扫描结果，供「先预览、后执行」两步式调用复用
        self._last_scan = None

    # ------------------------------------------------------------ 配置

    @classmethod
    def get_default_config(cls):
        """
        读取 media_orphan 配置段（config/config.yaml）：
            media_orphan:
              roots: ''     # 扫描目录，多个用逗号分隔；留空则自动用 media 段的媒体库目录
              server: ''    # 媒体服务器：留空＝用 media.media_server 里当前启用的那台
                            #（界面固定用当前启用的，此项是无界面时的越权开关）
        """
        try:
            conf = Config().get_config("media_orphan") or {}
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            conf = {}
        if not isinstance(conf, dict):
            conf = {}
        return {
            "roots": conf.get("roots") or "",
            "server": conf.get("server") or "",
        }

    @staticmethod
    def normalize_roots(value):
        """
        把 roots 归一化为去重后的路径列表。

        支持 None / 字符串（逗号、分号、换行分隔）/ list。
        """
        if value is None:
            items = []
        elif isinstance(value, (list, tuple, set)):
            items = [str(x) for x in value]
        else:
            # 英文逗号 / 中文逗号 / 分号 / 换行 都当分隔符
            items = re.split(r'[,;，；\r\n]+', str(value))
        roots = []
        for it in items:
            path = str(it or "").strip().rstrip("/").rstrip("\\")
            if not path:
                continue
            if path not in roots:
                roots.append(path)
        return roots

    @classmethod
    def parse_selection(cls, value):
        """
        解析「只删除哪些目录」的勾选集合（界面上预览清单里勾中的行）。

        与 normalize_roots 的关键区别是**空值的语义**：
            None（没传该字段）→ 返回 None，表示「没做勾选」，沿用「删除全部命中项」
                                  （REST 直调等旧调用方式不受影响）；
            [] / "" / "[]"     → 返回 []，表示「勾选为空」，调用方必须拒绝执行。

        支持 list / tuple / set / JSON 数组字符串 / 英文逗号分隔字符串。
        """
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            return cls.normalize_roots(value)
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return cls.normalize_roots(parsed)
        return cls.normalize_roots(text)

    @classmethod
    def get_media_roots(cls):
        """
        读取「设置 → 媒体」里的媒体库根目录，按类型返回：
            {"movie_path": [...], "tv_path": [...], "anime_path": [...]}
        """
        result = {"movie_path": [], "tv_path": [], "anime_path": []}
        try:
            media = Config().get_config("media") or {}
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return result
        if not isinstance(media, dict):
            return result
        for key in result:
            result[key] = cls.normalize_roots(media.get(key))
        return result

    @classmethod
    def get_configured_roots(cls, conf=None):
        """
        实际用于扫描的根目录：
        media_orphan.roots 优先；留空时回落到 media 段的电影 / 电视剧 / 动漫目录。
        """
        conf = conf if isinstance(conf, dict) else cls.get_default_config()
        roots = cls.normalize_roots(conf.get("roots"))
        if roots:
            return roots
        media = cls.get_media_roots()
        merged = []
        for key in ("movie_path", "tv_path", "anime_path"):
            for path in media.get(key) or []:
                if path not in merged:
                    merged.append(path)
        return merged

    @classmethod
    def _current_server_type(cls):
        """「设置 → 媒体服务器」里**当前启用**的服务器类型（media.media_server）"""
        try:
            media = Config().get_config("media") or {}
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return ""
        if not isinstance(media, dict):
            return ""
        return str(media.get("media_server") or "").strip().lower()

    @classmethod
    def resolve_server(cls, server_type=None):
        """
        确定实际要查询的媒体服务器（界面展示与后端执行**共用同一套优先级**）：
            1. 显式指定（REST /media_orphan/* 的 server_type 参数）；
            2. media_orphan.server（config/config.yaml，无界面时的越权开关）；
            3. media.media_server —— 「设置 → 媒体服务器」里当前启用的那一台。
        界面上不提供选择，默认就是第 3 项：要清理另一套影视的残留，
        去设置里切换「当前启用」即可（或走第 1、2 项）。
        :return: {"id": 类型, "name": 展示名, "configured": 该配置段是否填了 host}
        """
        sid = str(server_type or "").strip().lower()
        if not sid:
            try:
                conf = cls.get_default_config() or {}
            except Exception as err:
                ExceptionUtils.exception_traceback(err)
                conf = {}
            sid = str(conf.get("server") or "").strip().lower()
        if not sid:
            sid = cls._current_server_type()
        if not sid:
            return {"id": "", "name": "", "configured": False}
        # 该配置段是否真填了地址：没填必然连不上，界面要给出明确提示
        configured = False
        try:
            whole = Config().get_config() or {}
            node = whole.get(sid) if isinstance(whole, dict) else None
            configured = bool(isinstance(node, dict) and str(node.get("host") or "").strip())
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
        return {
            "id": sid,
            "name": cls._SERVER_NAMES.get(sid, sid.upper()),
            "configured": configured,
        }

    @staticmethod
    def _client_type_id(server, fallback=""):
        """
        取客户端实例对应的**配置段 id**（emby / jellyfin / plex / ugreen / trimemedia）。

        各客户端的 `client_id` 都声明在**类体**上（`_IMediaClient.client_id = ""`，
        具体实现如 `UgreenClient.client_id = "ugreen"`），与 config.yaml 的配置段名一致。
        这里显式取类属性，避免任何实例级同名属性把类属性遮蔽掉。

        ⚠️ 不要用 `get_type()`：各实现都是 `return self.client_type`，返回的是
        `MediaServerType` **枚举对象**，`str()` 出来是 `MediaServerType.UGREEN`，
        既不是配置段 id、也不是中文展示名。
        """
        cid = getattr(type(server), "client_id", None)
        if isinstance(cid, str) and cid.strip():
            return cid.strip().lower()
        return str(fallback or "").strip().lower()

    @classmethod
    def _resolve_client(cls, server_type=None):
        """
        按类型构造（或复用「当前启用」的）媒体服务器客户端。
        :return: (server, sid, name, error)
        """
        # 延迟导入：app.mediaserver 依赖链很重，且与 app.helper 存在循环引用
        try:
            from app.mediaserver import MediaServer
        except Exception as err:
            return None, "", "", "媒体服务器模块加载失败：%s" % err

        sid = str(server_type or "").strip().lower()
        try:
            mediaserver = MediaServer()
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return None, sid, cls._SERVER_NAMES.get(sid, ""), "初始化媒体服务器失败：%s" % err
        try:
            # 与「当前启用」一致（或未指定）时直接复用已建好的客户端，避免重复登录
            if sid and sid != cls._current_server_type():
                server = mediaserver.get_server_by_type(sid)
            else:
                server = mediaserver.server
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return None, sid, cls._SERVER_NAMES.get(sid, ""), "创建媒体服务器客户端失败：%s" % err
        if not server:
            return None, sid, cls._SERVER_NAMES.get(sid, ""), \
                "未找到可用的媒体服务器客户端，请检查「设置 → 媒体服务器」"
        # 以客户端实际类型为准（REST 传错类型时也纠正回真实类型）
        sid = cls._client_type_id(server, sid)
        name = (cls._SERVER_NAMES.get(sid)
                or str(getattr(type(server), "client_name", "") or "")
                or sid.upper())
        return server, sid, name, ""

    @classmethod
    def map_server_path(cls, path, anchors=None):
        """
        把**媒体服务器那一侧**的目录翻译成本环境（nas-tools 容器内）可访问的路径。

        容器内外挂载点经常不同：媒体服务器报 `/vol3/1000/video/01.电影/华语电影`，
        而 nas-tools 里同一目录是 `/video/01.电影/华语电影`。直接 os.path.isdir 判定
        会一律「不可访问」。做法是用「设置 → 媒体」里已配置的媒体库目录当**锚点**，
        在媒体服务器路径里找锚点的**尾部路径段**，命中后把剩余段拼到锚点之后，
        并要求拼出来的路径在本环境真实存在；命中锚点尾段最长的候选优先（越具体越可信）。

        :param path: 媒体服务器报出的路径
        :param anchors: 锚点（get_media_roots() 的返回值）；None 表示现取
        :return: (本地路径, 说明)；本地路径为 "" 表示无法映射
        """
        norm = cls._norm_path(path)
        if not norm:
            return "", "路径为空"
        if os.path.isdir(norm):
            return norm, "本环境直接可访问"

        anchors = anchors if isinstance(anchors, dict) else cls.get_media_roots()
        anchor_list = []
        for key in ("movie_path", "tv_path", "anime_path"):
            for item in anchors.get(key) or []:
                if item not in anchor_list:
                    anchor_list.append(item)

        segs = [s for s in norm.split("/") if s]
        best_len = 0
        best_path = ""
        for anchor in anchor_list:
            a_norm = cls._norm_path(anchor)
            a_segs = [s for s in a_norm.split("/") if s]
            for n in range(len(a_segs), 0, -1):
                # 只想找比已有结果更长的匹配
                if n > len(segs) or n <= best_len:
                    continue
                tail = a_segs[-n:]
                for i in range(0, len(segs) - n + 1):
                    if segs[i:i + n] != tail:
                        continue
                    rest = segs[i + n:]
                    cand = cls._norm_path(
                        a_norm + ("/" + "/".join(rest) if rest else ""))
                    if cand and os.path.isdir(cand):
                        best_len, best_path = n, cand
                        break
        if best_path:
            return best_path, "按尾部路径段对齐到本环境"
        return "", "无法映射到本环境可访问的路径"

    @classmethod
    def get_server_libraries(cls, server_type=None):
        """
        读取媒体服务器上的媒体库列表（名称 / 类型 / 目录），并把目录**翻译成本环境
        可访问的路径**。

        各家客户端返回的 `path` 形态不一致（绿联是逗号分隔字符串、飞牛是列表），
        统一走 normalize_roots 归一；再用 map_server_path() 把「媒体服务器那边」的
        路径翻成本环境可访问的路径 —— 不翻的话容器内外挂载点不同时会一律被判成
        「不可访问」，进而拿不到正确的默认扫描目录。

        :return: (libraries, server_name, error)
            每个库：id / name / type / paths（服务器原样）/ details（逐条映射明细）/
                    present（本环境可用的路径）/ missing（映射不到的原始路径）/
                    accessible
        """
        server, _sid, name, err = cls._resolve_client(server_type)
        if err:
            return [], name, err
        try:
            raw_list = server.get_libraries() or []
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return [], name, "获取媒体库列表失败：%s" % e
        if not raw_list:
            return [], name, "媒体服务器没有返回任何媒体库（可能未连接或未配置）"

        anchors = cls.get_media_roots()
        libraries = []
        for lib in raw_list:
            if not isinstance(lib, dict):
                continue
            paths = cls.normalize_roots(lib.get("path"))
            details = []
            present = []
            missing = []
            for path in paths:
                local, why = cls.map_server_path(path, anchors)
                details.append({"server": path, "local": local,
                                "ok": bool(local), "reason": why})
                if local:
                    if local not in present:
                        present.append(local)
                else:
                    missing.append(path)
            libraries.append({
                "id": str(lib.get("id") or ""),
                "name": str(lib.get("name") or lib.get("id") or ""),
                "type": str(lib.get("type") or ""),
                "paths": paths,
                "details": details,
                "present": present,
                "missing": missing,
                "accessible": bool(paths) and not missing,
            })
        return libraries, name, ""

    @classmethod
    def resolve_roots(cls, roots=None, server_type=None, conf=None):
        """
        确定实际要扫描的根目录，三级兜底：
            1. 显式传入的 roots / `media_orphan.roots`（配置）；
            2. **媒体服务器上各媒体库的目录**（自动检测，只取当前环境可访问的）；
            3. 「设置 → 媒体」里的电影 / 电视剧 / 动漫目录。
        :return: (roots, source)  source ∈ {"input","config","server","media",""}
        """
        explicit = cls.normalize_roots(roots)
        if explicit:
            return explicit, "input"
        conf = conf if isinstance(conf, dict) else cls.get_default_config()
        conf_roots = cls.normalize_roots(conf.get("roots"))
        if conf_roots:
            return conf_roots, "config"

        # 2) 媒体服务器的媒体库目录（自动检测）
        try:
            libraries, _name, _err = cls.get_server_libraries(server_type)
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            libraries = []
        detected = []
        for lib in libraries:
            for path in lib.get("present") or []:
                if path not in detected:
                    detected.append(path)
        if detected:
            return detected, "server"

        # 3) 「设置 → 媒体」里的媒体库目录
        media = cls.get_media_roots()
        merged = []
        for key in ("movie_path", "tv_path", "anime_path"):
            for path in media.get(key) or []:
                if path not in merged:
                    merged.append(path)
        if merged:
            return merged, "media"
        return [], ""

    @classmethod
    def detect(cls, server_type=None):
        """
        「自动检测媒体服务器 + 读取媒体库目录」，供界面打开时展示并让用户确认。

        :return: dict
            server          实际使用的媒体服务器 {id, name, configured}
            libraries       媒体库列表（含目录、映射后的本地路径、是否可访问）
            config_roots    media_orphan.roots（显式配置）
            media_roots     「设置 → 媒体」里的目录
            detected_roots  从媒体服务器读到并成功映射到本环境的目录
            unmapped_roots  读到了但映射不到本环境的原始目录
            suggested_roots 建议作为「扫描目录」的值（按上面的优先级）
            source          建议值的来源：config / server / media / ''
            error           读媒体库时的错误（为空表示检测成功）
        """
        info = dict(cls.resolve_server(server_type))
        libraries, server_name, err = cls.get_server_libraries(server_type)
        if server_name:
            info["name"] = server_name

        conf = cls.get_default_config()
        config_roots = cls.normalize_roots(conf.get("roots"))
        media = cls.get_media_roots()
        media_roots = []
        for key in ("movie_path", "tv_path", "anime_path"):
            for path in media.get(key) or []:
                if path not in media_roots:
                    media_roots.append(path)

        detected = []
        unmapped = []
        for lib in libraries:
            for path in lib.get("present") or []:
                if path not in detected:
                    detected.append(path)
            for path in lib.get("missing") or []:
                if path not in unmapped:
                    unmapped.append(path)
        if config_roots:
            suggested, source = config_roots, "config"
        elif detected:
            suggested, source = detected, "server"
        else:
            suggested, source = media_roots, "media"

        return {
            "server": info,
            "libraries": libraries,
            "config_roots": config_roots,
            "media_roots": media_roots,
            "detected_roots": detected,
            "unmapped_roots": unmapped,
            "suggested_roots": suggested,
            "source": source,
            "error": err,
        }

    # ------------------------------------------------------- 路径 / 片名

    @staticmethod
    def _norm_path(path):
        """路径归一化：统一正斜杠、去掉重复斜杠与结尾斜杠"""
        text = str(path or "").strip().replace("\\", "/")
        while "//" in text:
            text = text.replace("//", "/")
        return text.rstrip("/")

    @classmethod
    def _tail_keys(cls, path):
        """
        取路径的「尾部 n 段」签名集合（n = 2..min(段数, 4)）。

        用于容忍容器内外挂载根不同：
            /video/电影/流浪地球2 (2023)  与
            /volume1/video/电影/流浪地球2 (2023)
        尾部 2~3 段是相同的，即可认为指向同一目录。
        """
        segs = [s for s in cls._norm_path(path).split("/") if s]
        keys = set()
        for n in range(_MIN_TAIL_SEG, min(len(segs), _MAX_TAIL_SEG) + 1):
            keys.add("/".join(segs[-n:]))
        return keys

    @staticmethod
    def _norm_title(title):
        """片名归一化：小写 + 抹掉空白与标点"""
        return _TITLE_NOISE_RE.sub("", str(title or "").strip().lower())

    @classmethod
    def split_title_year(cls, name):
        """
        从目录名解析出 (片名, 年份)。
        年份取**最后一个** 4 位数字，避免「银翼杀手2049 (2017)」把 2049 当年份。
        """
        text = str(name or "").strip()
        years = _YEAR_RE.findall(text)
        year = years[-1] if years else ""
        title = text
        if year:
            title = re.sub(r'[\(\（\[\【]?\s*' + year + r'\s*[\)\）\]\】]?', ' ', text).strip()
        return title, year

    # ----------------------------------------------------------- 索引

    @classmethod
    def _build_index(cls, items, libraries=None):
        """
        建立索引：
          path_keys    —— 「条目路径」的尾部段签名集合：条目自身、其父目录（条目 path
                          可能是具体文件 xxx.mkv），以及**所有上级目录**；
          name_keys    —— (归一化片名, 年份) 与 (归一化片名, "") 集合；
          library_keys —— 媒体服务器媒体库目录的尾部段签名集合。

        ★ 为什么连**所有上级目录**一起进索引：扫描根可能比媒体库目录浅一层
          （媒体库目录是 `…/分类/华语电影`，用户却选了 `…/分类`），此时被扫到的
          「一级子文件夹」其实就是媒体库目录本身，条目都在它下面 —— 若只索引条目
          自身的路径，这些目录会被整层误判成残留。把上级目录一并索引后，任何
          「现存条目的上级目录」都会判为「仍在库中」。
        """
        path_keys = set()
        name_keys = set()
        library_keys = set()
        for lib in libraries or []:
            if not isinstance(lib, dict):
                continue
            for path in cls.normalize_roots(lib.get("paths")):
                library_keys |= cls._tail_keys(path)
        for item in items or []:
            if not isinstance(item, dict):
                continue
            raw_path = item.get("path") or ""
            if raw_path:
                path = cls._norm_path(raw_path)
                segs = [s for s in path.split("/") if s]
                for i in range(len(segs), 0, -1):
                    path_keys |= cls._tail_keys("/" + "/".join(segs[:i]))
            for title in (item.get("title"), item.get("original_title")):
                norm_title = cls._norm_title(title)
                if not norm_title:
                    continue
                year = str(item.get("year") or "")
                name_keys.add((norm_title, year))
                name_keys.add((norm_title, ""))
        return path_keys, name_keys, library_keys

    @classmethod
    def _match(cls, dir_path, dir_name, path_keys, name_keys, library_keys=None):
        """
        判定目录是否仍存在于媒体服务器。
        :return: (是否保留, 命中依据)
        """
        keys = cls._tail_keys(dir_path)
        if library_keys and keys & library_keys:
            return True, "媒体库目录"
        if keys & path_keys:
            return True, "条目所在目录"
        title, year = cls.split_title_year(dir_name)
        norm_title = cls._norm_title(title)
        if norm_title and ((norm_title, year) in name_keys or (norm_title, "") in name_keys):
            return True, "片名匹配"
        return False, ""

    # ------------------------------------------------- 媒体服务器条目

    @classmethod
    def collect_server_items(cls, server_type=None):
        """
        实时拉取媒体服务器上的全部媒体条目。

        :param server_type: 媒体服务器类型；为空表示用「当前启用」的那一台
        :return: (items, server_name, error)
        """
        server, _sid, name, err = cls._resolve_client(server_type)
        if err:
            return [], name, err

        try:
            libraries = server.get_libraries() or []
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return [], name, "获取媒体库列表失败：%s" % err
        if not libraries:
            return [], name, "媒体服务器没有返回任何媒体库（可能未连接或未配置）"

        items = []
        errors = []
        for library in libraries:
            if not isinstance(library, dict):
                continue
            lib_id = library.get("id")
            try:
                for item in (server.get_items(lib_id) or []):
                    if not isinstance(item, dict):
                        continue
                    items.append({
                        "type": str(item.get("type") or ""),
                        "title": item.get("title") or "",
                        "original_title": item.get("originalTitle")
                                          or item.get("original_title") or "",
                        "year": str(item.get("year") or ""),
                        "path": str(item.get("path") or ""),
                    })
            except Exception as err:
                ExceptionUtils.exception_traceback(err)
                errors.append(str(err))
                log.error("【Orphan】读取媒体库 %s 的条目失败：%s" % (lib_id, err))
        # 一个条目都没拿到、又确实报过错 → 把错误透出去（触发安全闸门）
        if not items and errors:
            return [], name, "读取媒体库条目失败：%s" % errors[0]
        return items, name, ""

    # ------------------------------------------------------------ 扫描

    def scan(self, roots, server_type=None, dry_run=True, selected_paths=None):
        """
        扫描根目录下的一级子文件夹，找出媒体服务器里已经不存在的残留。

        :param roots: 扫描根目录（字符串或列表）
        :param server_type: 媒体服务器类型，为空表示当前启用的那一台
        :param dry_run: True = 只出清单不删除
        :param selected_paths: 只删除这些目录（界面上勾中的行）。None = 未做勾选、
                               删除全部命中项；传空列表 = 勾选为空，调用方应拒绝执行。
        :return: dict
        """
        result = {
            "roots": [],
            "root_source": "",
            "server": "",
            "server_items": 0,
            "libraries": 0,
            "total_dirs": 0,
            "matched": [],
            "kept": [],
            "skipped": [],
            # 勾选（界面上只删勾中的行）：勾中数量 + 被勾选排除、因而保留的命中项
            "selected_count": 0,
            "unselected": [],
            "dry_run": bool(dry_run),
            "error": None,
            "deleted": [],
            "failed": [],
            "deleted_count": 0,
            "failed_count": 0,
        }

        root_list, root_source = self.resolve_roots(roots, server_type)
        result["roots"] = root_list
        result["root_source"] = root_source
        if not root_list:
            result["error"] = ("未能确定扫描目录：媒体服务器上没有读到可映射到本环境的媒体库目录，"
                               "「设置 → 媒体」也未配置 —— 请在窗口的「扫描目录」里选择要扫描的目录")
            return result

        items, server_name, err = self.collect_server_items(server_type)
        if server_name:
            result["server"] = server_name
        if err:
            result["error"] = err
            return result
        if not items:
            # ★ 安全闸门：一条都没拿到时绝不产生清单，否则会把整个媒体库判成残留
            result["error"] = "未从媒体服务器获取到任何媒体条目，为避免误删已中止"
            return result

        result["server_items"] = len(items)
        # 媒体库目录本身也要参与判定（扫描根比媒体库目录浅时，一级子目录就是媒体库目录）
        libraries, _lib_name, _lib_err = self.get_server_libraries(server_type)
        result["libraries"] = len(libraries)
        path_keys, name_keys, library_keys = self._build_index(items, libraries)
        for root in root_list:
            self._scan_root(root, path_keys, name_keys, library_keys, result)

        for item in result["matched"]:
            item["reason"] = "媒体服务器中已不存在"

        self._last_scan = result
        if dry_run:
            return result
        # 执行阶段才收窄范围：预览要给出**完整**清单供界面勾选，
        # 真正落盘删除的只有用户在清单里勾中的那些。
        self._apply_selection(result, selected_paths)
        return self._delete(result)

    @classmethod
    def _apply_selection(cls, result, selected_paths):
        """
        把删除范围收窄到用户在预览清单里勾选的目录。

        selected_paths 为 None ⇒ 没做勾选（REST 直调等），保持「删除全部命中项」；
        传了列表 ⇒ 只留下勾中的，其余从 matched 移到 unselected，不会被动到。

        路径一律经 _norm_path 归一后比对（前端回传的就是本环境路径，
        但 Windows 形态的反斜杠配置 / 手工调 REST 仍可能传进来）。
        """
        matched = result.get("matched") or []
        if selected_paths is None:
            result["selected_count"] = len(matched)
            result["unselected"] = []
            return result
        wanted = {cls._norm_path(p) for p in selected_paths if str(p or "").strip()}
        picked, left = [], []
        for item in matched:
            if cls._norm_path(item.get("path")) in wanted:
                picked.append(item)
            else:
                left.append(item)
        result["matched"] = picked
        result["selected_count"] = len(picked)
        result["unselected"] = left
        if left:
            log.info("【Orphan】按勾选执行：命中 %s 个，勾选 %s 个，未勾选的 %s 个已保留"
                     % (len(picked) + len(left), len(picked), len(left)))
        return result

    def _scan_root(self, root, path_keys, name_keys, library_keys, result):
        """扫描单个根目录下的一级子文件夹"""
        if not os.path.exists(root):
            result["skipped"].append({"path": root, "reason": "目录不存在"})
            return
        if not os.path.isdir(root):
            result["skipped"].append({"path": root, "reason": "不是目录"})
            return
        try:
            entries = sorted(os.listdir(root))
        except PermissionError as err:
            result["skipped"].append({"path": root, "reason": "权限不足：%s" % err})
            return
        except OSError as err:
            result["skipped"].append({"path": root, "reason": "读取失败：%s" % err})
            return

        for name in entries:
            sub_path = os.path.join(root, name)
            try:
                # 符号链接先判，避免被跟到外部目录
                if os.path.islink(sub_path):
                    result["skipped"].append({"path": sub_path, "reason": "符号链接已跳过"})
                    continue
                if not os.path.isdir(sub_path):
                    continue  # 根目录下的散落文件不受影响
            except OSError as err:
                result["skipped"].append({"path": sub_path, "reason": "状态检查失败：%s" % err})
                continue
            result["total_dirs"] += 1
            keep, why = self._match(sub_path, name, path_keys, name_keys, library_keys)
            if keep:
                result["kept"].append({"path": sub_path, "name": name, "reason": why})
            else:
                result["matched"].append({
                    "path": sub_path,
                    "name": name,
                    "size_bytes": self.get_dir_size(sub_path),
                })

    # ------------------------------------------------------------ 删除

    @staticmethod
    def get_dir_size(dir_path):
        """递归累加目录下所有普通文件的大小（符号链接不计入）"""
        total = 0
        try:
            for root, _dirs, files in os.walk(dir_path, followlinks=False):
                for name in files:
                    file_path = os.path.join(root, name)
                    try:
                        if os.path.islink(file_path) or not os.path.isfile(file_path):
                            continue
                        total += os.path.getsize(file_path)
                    except OSError:
                        continue
        except OSError:
            return total
        return total

    def _delete(self, result):
        """按清单真正删除（不可撤销）"""
        for item in result["matched"]:
            target = item["path"]
            try:
                # 删除前二次确认：防止扫描与删除之间被替换
                if os.path.islink(target):
                    result["failed"].append({"path": target, "reason": "已被替换为符号链接，跳过"})
                    continue
                if not os.path.isdir(target):
                    result["failed"].append({"path": target, "reason": "目录已不存在"})
                    continue
                size = self.get_dir_size(target)
                shutil.rmtree(target)
                result["deleted"].append({
                    "path": target,
                    "name": item.get("name") or os.path.basename(target),
                    "size_bytes": size,
                })
                log.info("【Orphan】已删除残留目录：%s（%.3f MB）" % (target, size / BYTES_PER_MB))
            except PermissionError as err:
                reason = "权限不足：%s" % err
                result["failed"].append({"path": target, "reason": reason})
                log.error("【Orphan】删除残留目录失败 %s：%s" % (target, reason))
            except OSError as err:
                reason = "删除失败（可能被占用）：%s" % err
                result["failed"].append({"path": target, "reason": reason})
                log.error("【Orphan】删除残留目录失败 %s：%s" % (target, reason))
            except Exception as err:  # noqa: BLE001 兜底，不中断整体流程
                ExceptionUtils.exception_traceback(err)
                reason = "未知异常：%s" % err
                result["failed"].append({"path": target, "reason": reason})
                log.error("【Orphan】删除残留目录失败 %s：%s" % (target, reason))
        result["deleted_count"] = len(result["deleted"])
        result["failed_count"] = len(result["failed"])
        return result

    def clean(self, roots, server_type=None, dry_run=True, selected_paths=None):
        """scan 的别名，语义上表示「执行清理」"""
        return self.scan(roots=roots, server_type=server_type, dry_run=dry_run,
                         selected_paths=selected_paths)

    # ------------------------------------------------------------ 摘要

    @staticmethod
    def format_result_message(result):
        """生成人类可读的结果摘要"""
        if result.get("error"):
            return result["error"]
        freed = round(sum(x.get("size_bytes", 0) for x in result.get("matched", [])) / BYTES_PER_MB, 2)
        if result.get("dry_run", True):
            return ("预览完成：媒体服务器「%s」共 %s 条条目，"
                    "扫描 %s 个影片目录，命中残留 %s 个，"
                    "预计可释放 %s MB（未执行删除）" % (
                        result.get("server") or "当前启用",
                        result.get("server_items", 0),
                        result.get("total_dirs", 0),
                        len(result.get("matched", [])),
                        freed))
        released = round(sum(x.get("size_bytes", 0) for x in result.get("deleted", [])) / BYTES_PER_MB, 2)
        # 只删勾选时，把「没勾、因而保留」的数量一并说明，避免用户以为漏删
        kept_txt = ""
        if result.get("unselected"):
            kept_txt = "，另有 %s 个未勾选已保留" % len(result["unselected"])
        return ("清理完成：媒体服务器「%s」，扫描 %s 个影片目录，"
                "已删除残留 %s 个%s，实际释放 %s MB，失败 %s 个" % (
                    result.get("server") or "当前启用",
                    result.get("total_dirs", 0),
                    result.get("deleted_count", 0),
                    kept_txt,
                    released,
                    result.get("failed_count", 0)))
