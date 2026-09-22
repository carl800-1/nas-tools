"""
媒体类型分类器 —— 纯本地规则判定「电影 / 电视剧 / 其它」三类。

设计目标
--------
面向「下载器里已有任务」的批量打标签场景。这类任务可能是手动添加、刷流下载
或历史遗留的，不一定走过 nastool 的识别链路，因此本模块：

* **不发起任何网络请求**，不依赖 TMDB / 豆瓣，不需要 API Key；
* 只吃种子名称（可选再补充文件清单、站点分类字段），毫秒级返回，可安全批量调用；
* 动漫按产品需求**并入电视剧**，不单列一类。

与 ``MediaType`` / ``MetaInfo.__get_tmdb_type`` 的关系
----------------------------------------------------
那套逻辑必须请求 TMDB 才能区分电影与电视剧，并把动漫单列为第四类。
本模块是它的**离线三分类替身**，两者互不影响：媒体识别链路继续用 TMDB，
下载器打标签用本模块。分类结果只用于打标签，不回写任何媒体元数据。

判定顺序（先命中先返回）
------------------------
1. 显式分类字段（站点分类 / 下载器 category）—— 最可信，直接映射
2. 非影视关键词（软件、游戏、无损音乐、电子书、教程…）→ 「其它」
   故意放在剧集规则**之前**：``「某某视频教程 第 3 集」`` 这类标题同样带
   「第 x 集」，若先跑剧集规则会被误判成电视剧。
3. 剧集特征（S01E01 / 第 x 季 / 第 x 集 / 全 xx 集 / 完结 / 综艺…）→ 「电视剧」
4. 电影特征（发行年份，画质/来源标记为加分项）→ 「电影」
5. 兜底 → 「其它」

最后一步为什么把「只有年份」也算电影
------------------------------------
PT 站点的电影资源绝大多数带 ``2160p / BluRay / WEB-DL`` 这类标记，属于高置信命中。
不放心的场景是 ``Adobe Photoshop 2024``、``某某行业报告 2023`` 这类带年份的非影视
资源 —— 它们本应由第 2 步的关键词黑名单拦下。为了让黑名单兜住这些漏网之鱼，
用户可以把自己遇到的关键词写进配置的「其它类补充关键词」，命中即优先归「其它」。

已知局限
--------
* 命名极度不规范（无季集号、无年份、无画质）的资源只能落到「其它」；
* ``剧场版``、``多部电影合集`` 这类边界情况按标题特征归类，可能不符合个人预期；
* 分类结果仅供参考，调用方不应把它当作绝对正确的元数据。
"""

import re
from dataclasses import dataclass
from enum import Enum


class MediaCategory(Enum):
    """
    分类结果枚举。

    枚举值只是便于日志与界面展示的中文名；真正写入下载器的标签由调用方
    按用户配置映射 —— 用户完全可以把「电影」这个标签改叫 ``Movies``。
    """

    MOVIE = "电影"
    TV = "电视剧"
    OTHER = "其它"


@dataclass(frozen=True)
class Classification:
    """
    一次分类的结果。

    :param category: 语义类别
    :param reason: 判定依据，仅用于日志排查，不参与任何业务逻辑
    """

    category: MediaCategory
    reason: str


class MediaClassifier:
    """
    纯本地规则的媒体类型三分类器。所有方法均为静态方法，可直接调用。
    """

    # 视频文件扩展名：用文件清单辅助判断时，非视频文件（.nfo / .jpg）直接跳过
    VIDEO_EXTS = (
        ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv",
        ".flv", ".rmvb", ".rm", ".mpg", ".mpeg", ".m4v", ".strm", ".iso",
    )

    # ---------------------------------------------------------------- 分类字段
    # 站点分类 / 下载器 category 是人工维护的字段，可信度高于标题推断
    CATEGORY_FIELD_MAP = (
        (re.compile(r"电影|影剧|movie|film", re.I), MediaCategory.MOVIE),
        (re.compile(
            r"电视剧|剧集|美剧|英剧|日剧|韩剧|国剧|港剧|台剧|泰剧|连续剧"
            r"|综艺|纪录片|动漫|番剧|动画"
            r"|(?<![A-Za-z])(?:tv|series|show|anime)(?![A-Za-z])",
            re.I,
        ), MediaCategory.TV),
        (re.compile(r"其它|其他|(?<![A-Za-z])(?:other|misc|others)(?![A-Za-z])", re.I), MediaCategory.OTHER),
    )

    # ---------------------------------------------------------------- 非影视关键词
    NON_MEDIA_RULES = (
        (re.compile(r"软件|安装包|破解版|绿色版|便携版|注册机|激活工具|汉化补丁"), "软件/工具"),
        (re.compile(
            r"(?<![A-Za-z])(?:PC|PS[45]|PSP|Switch|XBOX|NSP|NSZ|XCI)(?![A-Za-z])"
            r".{0,12}(?:游戏|GAME|ROM)",
            re.I,
        ), "游戏"),
        (re.compile(r"无损音乐|音乐专辑|原声大碟|演唱会|音乐会|交响乐|专辑合集"), "音乐/演出"),
        (re.compile(r"(?<![A-Za-z])(?:FLAC|APE|WAV|DSD|MP3)(?![A-Za-z])", re.I), "无损音频"),
        (re.compile(r"电子书|(?<![A-Za-z])(?:EPUB|MOBI|AZW3|PDF)(?![A-Za-z])", re.I), "电子书/文档"),
        (re.compile(r"视频教程|在线课程|培训视频|教学视频|网课|课件|公开课"), "教程/课程"),
        (re.compile(r"PS素材|AE模板|PR模板|字体包|壁纸包|图包|素材包|插件包"), "素材/模板"),
        (re.compile(r"写真|图集|图片集|漫画|(?<![A-Za-z])(?:Comic|Manga)(?![A-Za-z])", re.I), "写真/漫画"),
    )

    # ---------------------------------------------------------------- 剧集特征
    TV_RULES = (
        (re.compile(r"(?<![A-Za-z0-9])[Ss]\d{1,2}\s*[Ee]\d{1,4}(?![0-9])"), "季集号 SxxExx"),
        (re.compile(r"(?<![A-Za-z0-9])[Ss]\d{1,2}\s*[-~]\s*[Ss]?\d{1,2}(?![0-9])"), "季范围 Sxx-Sxx"),
        (re.compile(r"(?<![A-Za-z0-9])[Ss]\d{1,2}(?![A-Za-z0-9])"), "季号 Sxx"),
        (re.compile(r"(?<![A-Za-z])Season\s*\d{1,2}(?![0-9])", re.I), "Season x"),
        (re.compile(r"第\s*[0-9一二三四五六七八九十百零两]+\s*[季部辑]"), "第 x 季"),
        (re.compile(r"第\s*\d{1,4}\s*[集话話期]"), "第 x 集/期"),
        (re.compile(r"(?<![A-Za-z0-9])EP\s*\d{1,4}(?![0-9])", re.I), "EPxx"),
        (re.compile(r"(?<![A-Za-z0-9])[Ee]\d{2,4}(?![A-Za-z0-9])"), "Exx"),
        (re.compile(r"全\s*\d{1,4}\s*[集话話]"), "全 xx 集"),
        (re.compile(r"\d{1,4}\s*[集话話]\s*全"), "xx 集全"),
        (re.compile(r"\d{1,2}\s*[季部]\s*全"), "x 季全"),
        (re.compile(r"(?<![A-Za-z])Complete(?![A-Za-z])", re.I), "Complete"),
        (re.compile(r"完结|全集打包"), "完结/全集"),
        (re.compile(r"综艺|脱口秀|访谈节目"), "综艺"),
    )

    # 文件清单里用于统计的「季集号」形状（比 TV_RULES 更严格，避免标题里的杂项误计数）
    TV_FILE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[Ss]\d{1,2}\s*[Ee]\d{1,4}(?![0-9])")

    # ---------------------------------------------------------------- 电影特征
    YEAR_PATTERN = re.compile(r"(?<![0-9])(?:19|20)\d{2}(?![0-9xX])")

    SOURCE_RULES = (
        (re.compile(
            r"(?<![A-Za-z])(?:BluRay|Blu-ray|BDRip|BRRip|BDMV|REMUX|WEB-?DL|WEB-?Rip"
            r"|HDTV|HDRip|DVDRip|DVDScr|HD-?DVD|UHDTV)(?![A-Za-z])",
            re.I,
        ), "来源 BluRay/WEB-DL 等"),
        (re.compile(
            r"(?<![A-Za-z0-9])(?:2160[Pp]|1080[Pp]|720[Pp]|480[Pp]|4K|UHD)(?![A-Za-z0-9])",
            re.I,
        ), "分辨率 2160p/1080p 等"),
        (re.compile(
            r"(?<![A-Za-z0-9])(?:x264|x265|H\.?264|H\.?265|HEVC|AVC|10bit|8bit)(?![A-Za-z0-9])",
            re.I,
        ), "编码 x264/x265/HEVC"),
        (re.compile(
            r"(?<![A-Za-z])(?:DTS|TrueHD|Atmos|EAC3|AC3|DDP|DD5\.1|DTS-?HD)(?![A-Za-z])",
            re.I,
        ), "音轨 DTS/Atmos 等"),
    )

    # ================================================================ 对外接口

    @staticmethod
    def classify(title, files=None, category=None, extra_other_keywords=None):
        """
        判定单个种子的媒体类型。

        :param title: 种子名称（标题），判定主依据
        :param files: 可选，种子内文件名列表，用于补充判断剧集
        :param category: 可选，站点分类或下载器 category 字段，可信度最高
        :param extra_other_keywords: 可选，用户补充的「其它类」关键词列表，
                                     命中即优先归入「其它」，用于修正黑名单漏网之鱼
        :return: :class:`Classification`
        """
        text = str(title or "").strip()

        # 1. 显式分类字段
        field_hit = MediaClassifier._match_category_field(category)
        if field_hit:
            return Classification(field_hit, f"分类字段「{str(category).strip()}」")

        if not text and not files:
            return Classification(MediaCategory.OTHER, "标题与文件清单均为空")

        # 2. 非影视关键词（内置黑名单 + 用户补充词）
        hit = MediaClassifier._match_rules(text, MediaClassifier.NON_MEDIA_RULES)
        if hit:
            return Classification(MediaCategory.OTHER, f"命中非影视关键词：{hit}")

        for keyword in (extra_other_keywords or []):
            keyword = str(keyword or "").strip()
            if keyword and keyword.lower() in text.lower():
                return Classification(MediaCategory.OTHER, f"命中自定义非影视词：{keyword}")

        # 3. 剧集特征：标题优先，其次看文件清单
        hit = MediaClassifier._match_rules(text, MediaClassifier.TV_RULES)
        if hit:
            return Classification(MediaCategory.TV, f"标题命中剧集特征：{hit}")

        file_hit = MediaClassifier._match_tv_files(files)
        if file_hit:
            return Classification(MediaCategory.TV, f"文件清单命中剧集特征：{file_hit}")

        # 4. 电影特征：年份是必要条件，画质/来源是加分项
        year = MediaClassifier.YEAR_PATTERN.search(text)
        if year:
            source = MediaClassifier._match_rules(text, MediaClassifier.SOURCE_RULES)
            if source:
                return Classification(MediaCategory.MOVIE, f"年份 {year.group(0)} + {source}")
            return Classification(MediaCategory.MOVIE, f"含发行年份 {year.group(0)}，无剧集特征")

        # 5. 兜底
        return Classification(MediaCategory.OTHER, "未命中任何影视特征")

    @staticmethod
    def classify_label(title, files=None, category=None, extra_other_keywords=None):
        """
        与 :meth:`classify` 相同，但直接返回中文类别名（``电影`` / ``电视剧`` / ``其它``）。

        便于日志与界面直接使用；需要判定依据时请改用 :meth:`classify`。
        """
        return MediaClassifier.classify(
            title, files=files, category=category, extra_other_keywords=extra_other_keywords
        ).category.value

    # ================================================================ 内部实现

    @staticmethod
    def _match_rules(text, rules):
        """
        按顺序在文本中匹配规则集，返回第一条命中的描述；全部未命中返回 None。
        """
        if not text:
            return None
        for pattern, description in rules:
            if pattern.search(text):
                return description
        return None

    @staticmethod
    def _match_category_field(category):
        """
        把分类字段映射为语义类别；无法识别时返回 None（继续走标题推断）。
        """
        if not category:
            return None
        value = str(category).strip()
        if not value:
            return None
        for pattern, target in MediaClassifier.CATEGORY_FIELD_MAP:
            if pattern.search(value):
                return target
        return None

    @staticmethod
    def _match_tv_files(files):
        """
        用种子内文件清单补充判断是否为剧集。

        判据：出现 **≥2 个** 带季集号（SxxExx）的视频文件。要求 ≥2 个是为了
        避免把「单个花絮」「单集泄露版」这类单文件资源当成整部剧集；电影即使
        命名不规范成 ``Movie.2024.E01.mkv``，也只有一个文件，不会被误判。
        """
        if not files:
            return None
        count = 0
        for name in files:
            if not name:
                continue
            name = str(name)
            lower = name.lower()
            if "." in lower and not lower.endswith(MediaClassifier.VIDEO_EXTS):
                continue
            if MediaClassifier.TV_FILE_PATTERN.search(name):
                count += 1
                if count >= 2:
                    return f"{count} 个以上视频文件带 SxxExx"
        return None
