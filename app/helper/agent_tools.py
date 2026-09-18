"""
AI Agent 工具集

把 NAStool 已有能力（WebAction._actions）包装成 function calling 工具，
供飞书 / 微信 / Telegram 等 IM 渠道的自然语言指令调用。

关于依赖方向：本模块位于 app 层，却需要调用 web 层的 WebAction，因此所有对
web 层的导入都写在函数内部（延迟导入），避免与 web.action 形成循环依赖。

关于结果裁剪：工具返回值最终会作为上下文喂给模型，站点列表、转移历史这类
结果动辄上千条，必须裁剪，否则一次调用就能把上下文撑爆。

关于「单一数据源」：能力菜单（给用户看）、工具说明（给模型看）、参数校验、
审计分类、接入范围文档，全部从下面这一张 AGENT_TOOLS 表派生。
新增一个工具只需要在表里加一条，其它地方自动生效，不会出现"文档与实现不同步"。
"""

import json

import log

# ---------------------------------------------------------------------------
# 字段契约
#   name      工具名，模型据此调用，英文小写下划线
#   action    对应的 WebAction().action 命令名；为 None 时走 handler
#   group     能力分组，决定它出现在能力菜单的哪一节
#   label     用户可读的一句话说明（出现在能力菜单里，写给用户看）
#   sample    用户可能怎么说（出现在能力菜单里，给用户当范例）
#   desc      给模型看的描述，需写清"什么时候用"，这是模型选对工具的唯一依据
#   params    JSON Schema 参数定义，required 用于缺参追问
#   ask       可选，缺参时追问用户的话术；缺省则由参数描述自动生成
#   kind      read=只读查询 / write=写操作 / ops=运维级 / meta=元能力
#            非 read 且非 meta 的操作会写审计日志
#   dangerous 是否危险操作（执行前需用户二次确认）
#   max_items 结果中列表的最大条数
#   hidden    不在能力菜单里展示（如 ask_user 这类内部工具）
# ---------------------------------------------------------------------------

# 能力分组及其在菜单中的展示顺序
GROUP_ORDER = [
    "下载管理",
    "订阅与搜索",
    "站点",
    "媒体库",
    "刷流与同步",
    "历史与清理",
    "系统运维",
    "使用帮助",
]

AGENT_TOOLS = [
    # ---------------- 下载管理 ----------------
    {
        "name": "query_downloading",
        "action": "get_downloading",
        "group": "下载管理",
        "label": "看正在下载/做种的任务（进度、速度、剩余时间）",
        "sample": "在下载什么",
        "desc": "查询下载器中正在下载或做种的任务，返回名称、进度、状态、速度、剩余时间。"
                "当用户问「在下载什么」「下载进度如何」「还有几个任务」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "下载器ID，留空表示查询所有下载器"}
            },
        },
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_downloaded",
        "action": "get_downloaded",
        "group": "下载管理",
        "label": "看已下载完成的记录",
        "sample": "最近下载了哪些片子",
        "desc": "查询已下载完成的媒体历史记录。当用户问「下载过什么」「最近下载了哪些片子」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "页码，从1开始，默认1"}
            },
        },
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_downloaders",
        "action": "get_downloaders",
        "group": "下载管理",
        "label": "看下载器列表和连接状态",
        "sample": "下载器都正常吗",
        "desc": "查询已配置的下载器（qBittorrent/Transmission等）及其连接状态。"
                "当用户问「有哪些下载器」「下载器正常吗」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 20,
    },
    {
        "name": "query_download_settings",
        "action": "get_download_setting",
        "group": "下载管理",
        "label": "看下载器的下载目录与分类设置",
        "sample": "下载目录是怎么配的",
        "desc": "查询下载器的下载设置（分类、保存目录、标签等）。"
                "当用户问「下载到哪个目录」「下载器配置」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "sid": {"type": "string", "description": "下载设置ID，留空返回全部"}
            },
        },
        "kind": "read",
        "max_items": 20,
    },
    {
        "name": "download_by_link",
        "action": "download_link",
        "group": "下载管理",
        "label": "用磁力链/种子链接直接添加下载",
        "sample": "下载这个磁力链 …",
        "desc": "把一个磁力链（magnet:）或种子文件下载链接直接加入下载器。"
                "当用户发来 magnet 链接或说「下载这个链接」时使用。"
                "注意：普通 http 网页链接不适用，只有磁力链或 .torrent 地址才用本工具。",
        "params": {
            "type": "object",
            "properties": {
                "enclosure": {"type": "string", "description": "磁力链或种子文件下载地址"},
                "title": {"type": "string", "description": "资源标题，可留空"},
                "site": {"type": "string", "description": "来源站点名称，可留空"}
            },
            "required": ["enclosure"]
        },
        "kind": "write",
    },
    {
        "name": "torrent_start",
        "action": "pt_start",
        "group": "下载管理",
        "label": "继续（恢复）下载任务",
        "sample": "把暂停的那个任务继续下",
        "desc": "开始（恢复）下载器中的指定任务。当用户说「把某个暂停的任务继续下载」时使用。"
                "调用前若不确定任务是哪个，先用 query_downloading 查到任务ID。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务ID列表，逗号分隔"}
            },
            "required": ["id"]
        },
        "ask": {"id": "要继续的是哪个任务？（可先问「在下载什么」看列表）"},
        "kind": "write",
    },
    {
        "name": "torrent_stop",
        "action": "pt_stop",
        "group": "下载管理",
        "label": "暂停下载任务",
        "sample": "暂停第2个下载",
        "desc": "暂停下载器中的指定任务。当用户说「暂停某个下载」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务ID列表，逗号分隔"}
            },
            "required": ["id"]
        },
        "ask": {"id": "要暂停的是哪个任务？（可先问「在下载什么」看列表）"},
        "kind": "write",
    },
    {
        "name": "torrent_delete",
        "action": "pt_remove",
        "group": "下载管理",
        "label": "删除下载任务（连同已下载文件）",
        "sample": "把那个任务删了",
        "desc": "从下载器删除指定任务并删除已下载的文件。这是不可逆操作，"
                "调用前必须明确用户要删哪个任务。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务ID列表，逗号分隔"}
            },
            "required": ["id"]
        },
        "ask": {"id": "要删除的是哪个任务？（这是不可逆操作，请用户明确任务名）"},
        "kind": "write",
        "dangerous": True,
    },

    # ---------------- 订阅与搜索 ----------------
    {
        "name": "query_movie_subscribes",
        "action": "get_movie_rss_list",
        "group": "订阅与搜索",
        "label": "看电影订阅列表",
        "sample": "我订阅了哪些电影",
        "desc": "查询所有电影订阅。当用户问「订阅了哪些电影」「某部电影订阅了吗」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 40,
    },
    {
        "name": "query_tv_subscribes",
        "action": "get_tv_rss_list",
        "group": "订阅与搜索",
        "label": "看电视剧订阅列表及追剧进度",
        "sample": "那部剧追到第几集了",
        "desc": "查询所有电视剧订阅，包含已下载到第几季第几集。"
                "当用户问「订阅了哪些剧」「某部剧追到哪了」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 40,
    },
    {
        "name": "query_subscribe_history",
        "action": "get_rss_history",
        "group": "订阅与搜索",
        "label": "看订阅的下载历史",
        "sample": "订阅都下过什么",
        "desc": "查询订阅的下载历史。当用户问「订阅都下过什么」「某部剧下载记录」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"}
            },
        },
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_media_info",
        "action": "search_media_infos",
        "group": "订阅与搜索",
        "label": "查一部片的资料（片名/年份/TMDB ID）",
        "sample": "查一下沙丘是哪一年的",
        "desc": "按名称查询影视作品的媒体资料（TMDB/豆瓣），返回片名、年份、TMDB ID、简介。"
                "当用户问「某部片子是哪一年的」「帮我查一下这部电影」"
                "或需要在添加订阅前确认是哪部片子时使用。",
        "params": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "片名关键字"},
                "searchtype": {"type": "string", "enum": ["TMDB", "DB"], "description": "TMDB 或 DB(豆瓣)，默认 TMDB"}
            },
            "required": ["keyword"]
        },
        "ask": {"keyword": "要查的是哪部片子？（直接说片名即可）"},
        "kind": "read",
        "max_items": 10,
    },
    {
        "name": "query_calendar_events",
        "action": "get_ical_events",
        "group": "订阅与搜索",
        "label": "看已订阅影片的上映/播出日历",
        "sample": "最近有什么要上映的",
        "desc": "查询订阅影片的上映与播出日历事件。"
                "当用户问「最近有什么新片上映」「订阅的片子什么时候播」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "add_subscribe",
        "action": "add_rss_media",
        "group": "订阅与搜索",
        "label": "订阅电影或电视剧（自动搜索下载）",
        "sample": "帮我订阅沙丘",
        "desc": "添加电影或电视剧订阅，之后系统会自动搜索并下载匹配的资源。"
                "当用户说「帮我订阅某部片」「追某部剧」时使用。"
                "调用前应先用 query_media_info 确认片子正确。",
        "params": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "片名"},
                "year": {"type": "string", "description": "年份，用于区分同名影片"},
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"},
                "season": {"type": "integer", "description": "剧集的起始季，仅电视剧需要"},
                "keyword": {"type": "string", "description": "搜索关键字，一般与片名相同"},
                "mediaid": {"type": "string", "description": "TMDB ID，若能通过 query_media_info 获得则一并传入更准确"},
                "fuzzy_match": {"type": "boolean", "description": "是否模糊匹配，默认false"}
            },
            "required": ["name", "type"]
        },
        "ask": {"name": "要订阅的是哪部片子？",
                "type": "订阅的是电影还是电视剧？"},
        "kind": "write",
    },
    {
        "name": "delete_subscribe",
        "action": "remove_rss_media",
        "group": "订阅与搜索",
        "label": "取消（删除）订阅",
        "sample": "取消订阅沙丘",
        "desc": "删除已有的订阅。当用户说「取消订阅某部片」时使用。"
                "删除前应先查询订阅列表确认要删的是哪一条。",
        "params": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "订阅的片名"},
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"},
                "year": {"type": "string", "description": "年份"},
                "rssid": {"type": "string", "description": "订阅ID，最精确的定位方式"},
                "tmdbid": {"type": "string", "description": "TMDB ID"}
            },
            "required": ["type"]
        },
        "ask": {"type": "要取消的是电影订阅还是电视剧订阅？"},
        "kind": "write",
    },
    {
        "name": "refresh_subscribe",
        "action": "refresh_rss",
        "group": "订阅与搜索",
        "label": "立刻重新搜索某个订阅的资源",
        "sample": "重新搜一下沙丘",
        "desc": "立即重新搜索某个订阅的可用资源。当用户说「重新搜一下某部片」「刷新订阅」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"},
                "rssid": {"type": "string", "description": "订阅ID"}
            },
            "required": ["type"]
        },
        "ask": {"type": "要刷新的是电影订阅还是电视剧订阅？"},
        "kind": "write",
    },
    {
        "name": "search_media",
        "action": None,
        "group": "订阅与搜索",
        "label": "搜站点资源并把结果发给你挑",
        "sample": "帮我找沙丘 2021",
        "desc": "按名称搜索站点资源，并把搜索结果推送给用户挑选下载。"
                "当用户说「帮我找某部片」「搜索某部片子」时使用。"
                "注意：本工具会直接把搜索结果推送给用户，无需你复述结果。",
        "params": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜索关键字，可包含片名、年份、季集，如「沙丘 2021」"
                }
            },
            "required": ["keyword"]
        },
        "ask": {"keyword": "要找的是哪部片子？（可以说片名+年份，如「沙丘 2021」）"},
        "kind": "write",
    },
    {
        "name": "re_subscribe_history",
        "action": "re_rss_history",
        "group": "订阅与搜索",
        "label": "按下载历史重新订阅",
        "sample": "把以前下过的那部剧重新订阅",
        "desc": "根据订阅下载历史里的记录重新添加订阅。"
                "当用户说「重新订阅以前下过的某部片」时使用，需要先查订阅历史拿到 rssid。",
        "params": {
            "type": "object",
            "properties": {
                "rssid": {"type": "string", "description": "订阅历史记录ID"},
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"}
            },
            "required": ["rssid", "type"]
        },
        "ask": {"rssid": "要重新订阅哪条记录？（可先查订阅下载历史）",
                "type": "是电影还是电视剧？"},
        "kind": "write",
    },

    # ---------------- 站点 ----------------
    {
        "name": "query_sites",
        "action": "get_sites",
        "group": "站点",
        "label": "看 PT 站点列表和状态",
        "sample": "哪些站点挂了",
        "desc": "查询所有已配置的PT站点列表及其状态（是否可用、签到情况、是否维护中）。"
                "当用户问「有哪些站点」「站点正常吗」「哪个站挂了」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "basic": {"type": "boolean", "description": "是否返回详细信息，默认true"}
            },
        },
        "kind": "read",
        "max_items": 60,
    },
    {
        "name": "query_site_statistics",
        "action": "get_site_user_statistics",
        "group": "站点",
        "label": "看站点数据（上传/下载/分享率/魔力值）",
        "sample": "我的分享率多少",
        "desc": "查询站点的用户数据统计（上传量、下载量、分享率、魔力值、做种数）。"
                "当用户问「我的数据怎么样」「分享率多少」「哪个站要挂了」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "sites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "站点名称列表，留空表示查询所有站点"
                }
            },
        },
        "kind": "read",
        "max_items": 60,
    },
    {
        "name": "query_site_activity",
        "action": "get_site_activity",
        "group": "站点",
        "label": "看单个站点的上传/下载/魔力值变化趋势",
        "sample": "某站最近的数据变化",
        "desc": "查询指定站点的历史活动数据（上传、下载、魔力值的变化曲线）。"
                "当用户问「某站最近数据变化」「上传量趋势」时使用，需要站点名称。",
        "params": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "站点名称"}
            },
            "required": ["name"]
        },
        "ask": {"name": "要查哪个站点的数据变化？（说站点名称即可）"},
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_site_seeding",
        "action": "get_site_seeding_info",
        "group": "站点",
        "label": "看单个站点的做种分布（大小/做种数）",
        "sample": "某站做种情况",
        "desc": "查询指定站点的做种分布信息（按大小、做种数）。"
                "当用户问「某站做种多少」「做种分布」时使用，需要站点名称。",
        "params": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "站点名称"}
            },
            "required": ["name"]
        },
        "ask": {"name": "要查哪个站点的做种情况？（说站点名称即可）"},
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_site_resources",
        "action": "list_site_resources",
        "group": "站点",
        "label": "看某个站点的最新资源",
        "sample": "某站最近有什么新片",
        "desc": "查询某个站点的最新资源列表。当用户问「某站最近有什么新片」时使用，"
                "需要提供站点的URL。",
        "params": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "站点URL"},
                "page": {"type": "integer", "description": "页码，从0开始"},
                "keyword": {"type": "string", "description": "可选的过滤关键字"}
            },
            "required": ["site"]
        },
        "ask": {"site": "要看哪个站点的资源？（说站点名称即可，可先查站点列表拿地址）"},
        "kind": "read",
        "max_items": 25,
    },
    {
        "name": "query_indexers",
        "action": "get_indexers",
        "group": "站点",
        "label": "看索引器列表和状态",
        "sample": "有哪些索引器",
        "desc": "查询已配置的索引器及其状态。当用户问「有哪些索引器」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 30,
    },

    # ---------------- 媒体库 ----------------
    {
        "name": "query_transfer_history",
        "action": "get_transfer_history",
        "group": "媒体库",
        "label": "看入库/整理历史",
        "sample": "最近入库了什么",
        "desc": "查询媒体文件整理（转移/刮削入库）的历史记录，包含源路径、目标路径、识别结果。"
                "当用户问「入库了什么」「整理记录」「某部片子入库了吗」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "按关键字过滤，留空查询全部"},
                "page": {"type": "integer", "description": "页码，从1开始"}
            },
        },
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_transfer_statistics",
        "action": "get_transfer_statistics",
        "group": "媒体库",
        "label": "看最近 90 天的入库统计",
        "sample": "这个月入库了多少",
        "desc": "查询最近90天的媒体整理统计（电影/电视剧/动漫各多少部）。"
                "当用户问「总共入库了多少」「统计一下入库量」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
    },
    {
        "name": "query_library_space",
        "action": "get_library_spacesize",
        "group": "媒体库",
        "label": "看媒体库磁盘空间占用",
        "sample": "磁盘还剩多少",
        "desc": "查询媒体库磁盘空间占用情况。当用户问「磁盘还剩多少」「空间够吗」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
    },
    {
        "name": "query_media_count",
        "action": "get_library_mediacount",
        "group": "媒体库",
        "label": "看媒体库的影片数量统计",
        "sample": "媒体库有多少部电影",
        "desc": "查询媒体服务器中的影片数量统计（电影数、剧集数、集数、用户数）。"
                "当用户问「媒体库里有多少片子」「有多少个用户」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
    },
    {
        "name": "query_play_history",
        "action": "get_library_playhistory",
        "group": "媒体库",
        "label": "看最近的播放记录",
        "sample": "最近谁看了什么",
        "desc": "查询媒体库最近30天的播放记录。"
                "当用户问「最近看了什么」「播放记录」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "query_unknown_files",
        "action": "get_unknown_list",
        "group": "媒体库",
        "label": "看未识别/入库失败的文件",
        "sample": "有哪些没识别",
        "desc": "查询未能识别、未成功入库的文件列表。"
                "当用户问「有哪些没识别」「入库失败的」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "re_identify_unknown",
        "action": "re_identification",
        "group": "媒体库",
        "label": "重新识别未识别文件",
        "sample": "重新识别那些没识别的文件",
        "desc": "对未识别的文件重新执行识别。当用户说「重新识别一下那些没识别的文件」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "flag": {"type": "string", "enum": ["unidentification"], "description": "固定传 unidentification"},
                "ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要重新识别的记录ID列表，留空表示全部"
                }
            }
        },
        "kind": "write",
    },
    {
        "name": "sync_media_library",
        "action": "start_mediasync",
        "group": "媒体库",
        "label": "触发一次媒体库同步",
        "sample": "同步一下媒体库",
        "desc": "触发媒体服务器（Emby/Jellyfin/Plex）的媒体库同步刷新。"
                "当用户说「同步媒体库」「刷新媒体库」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "librarys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要同步的媒体库名称列表，留空表示全部"
                }
            },
        },
        "kind": "write",
    },

    # ---------------- 刷流与同步 ----------------
    {
        "name": "run_brushtask",
        "action": "run_brushtask",
        "group": "刷流与同步",
        "label": "立刻跑一次刷流任务",
        "sample": "跑一下刷流",
        "desc": "立即执行指定的刷流任务。当用户说「跑一下刷流」「执行刷流任务」时使用，"
                "需要提供刷流任务ID。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "刷流任务ID"}
            },
            "required": ["id"]
        },
        "ask": {"id": "要跑哪个刷流任务？（说任务名或ID）"},
        "kind": "write",
    },
    {
        "name": "run_directory_sync",
        "action": "run_directory_sync",
        "group": "刷流与同步",
        "label": "立刻执行一次目录同步",
        "sample": "同步一下目录",
        "desc": "立即执行一次目录同步任务。当用户说「同步一下目录」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "sid": {"type": "string", "description": "同步目录的ID"}
            },
            "required": ["sid"]
        },
        "ask": {"sid": "要同步哪个目录任务？（可先问「有哪些目录同步任务」）"},
        "kind": "write",
    },
    {
        "name": "query_directory_sync",
        "action": "get_sync_path",
        "group": "刷流与同步",
        "label": "看目录同步任务列表",
        "sample": "有哪些目录同步任务",
        "desc": "查询已配置的目录同步任务。"
                "当用户问「有哪些目录同步任务」「同步目录配置」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "sid": {"type": "string", "description": "同步目录ID，留空返回全部"}
            },
        },
        "kind": "read",
        "max_items": 20,
    },
    {
        "name": "query_auto_remove_tasks",
        "action": "get_torrent_remove_task",
        "group": "刷流与同步",
        "label": "看自动删种任务配置",
        "sample": "有哪些自动删种任务",
        "desc": "查询已配置的自动删种任务（删种规则、条件）。"
                "当用户问「有哪些删种任务」「删种规则」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "tid": {"type": "string", "description": "任务ID，留空返回全部"}
            },
        },
        "kind": "read",
        "max_items": 20,
    },
    {
        "name": "auto_remove_torrents",
        "action": "auto_remove_torrents",
        "group": "刷流与同步",
        "label": "立刻执行自动删种（可能删文件）",
        "sample": "跑一下自动删种",
        "desc": "立即执行自动删种任务，按配置的规则删除种子（可能同时删除文件）。"
                "仅当用户明确要求时调用。",
        "params": {
            "type": "object",
            "properties": {
                "tid": {"type": "string", "description": "删种任务ID，留空表示执行全部任务"}
            },
        },
        "kind": "ops",
        "dangerous": True,
    },

    # ---------------- 历史与清理 ----------------
    {
        "name": "truncate_history",
        "action": None,
        "group": "历史与清理",
        "label": "清空某类历史记录（不可恢复）",
        "sample": "清空入库历史",
        "desc": "清空历史记录，清空后无法恢复。可选择清空哪一类记录。"
                "仅当用户明确要求清理时才调用。",
        "params": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["transfer", "rss", "blacklist"],
                    "description": "transfer=媒体整理历史，rss=订阅历史，blacklist=转移黑名单"
                }
            },
            "required": ["kind"]
        },
        "ask": {"kind": "要清空哪一类记录？（媒体整理历史 / 订阅历史 / 转移黑名单）"},
        "kind": "ops",
        "dangerous": True,
    },
    {
        "name": "delete_rss_history",
        "action": "delete_rss_history",
        "group": "历史与清理",
        "label": "删除单条订阅下载历史",
        "sample": "删掉那条订阅记录",
        "desc": "删除指定的订阅下载历史记录。"
                "当用户说「删掉某条订阅历史」时使用，需要先查历史拿到 rssid。",
        "params": {
            "type": "object",
            "properties": {
                "rssid": {"type": "string", "description": "订阅历史记录ID"}
            },
            "required": ["rssid"]
        },
        "ask": {"rssid": "要删除哪条订阅历史记录？（可先查订阅下载历史）"},
        "kind": "ops",
        "dangerous": True,
    },
    {
        "name": "truncate_unknown",
        "action": "truncate_transfer_unknown",
        "group": "历史与清理",
        "label": "清空未识别文件记录（不可恢复）",
        "sample": "清空未识别列表",
        "desc": "清空「未识别文件」列表记录。仅当用户明确要求时调用。",
        "params": {"type": "object", "properties": {}},
        "kind": "ops",
        "dangerous": True,
    },

    # ---------------- 系统运维 ----------------
    {
        "name": "query_system_processes",
        "action": "get_system_processes",
        "group": "系统运维",
        "label": "看系统 CPU/内存占用",
        "sample": "系统负载怎么样",
        "desc": "查询系统正在运行的进程与资源占用。当用户问「系统负载」「CPU内存占用」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 20,
    },
    {
        "name": "query_version",
        "action": "version",
        "group": "系统运维",
        "label": "检查有没有新版本",
        "sample": "有没有新版本",
        "desc": "检查 NAStool 是否有新版本可升级。当用户问「有没有新版本」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
    },
    {
        "name": "query_backup_items",
        "action": "get_backup_items",
        "group": "系统运维",
        "label": "看备份可以备份哪些条目",
        "sample": "备份都能备什么",
        "desc": "列出备份/恢复功能支持的可勾选条目（站点、订阅、刷流任务等）。"
                "当用户问「备份包含什么」「能备份哪些内容」时使用。"
                "注意：实际执行备份/恢复需要上传或下载文件，请在 Web 界面操作。",
        "params": {"type": "object", "properties": {}},
        "kind": "read",
        "max_items": 30,
    },
    {
        "name": "clear_meta_cache",
        "action": "clear_tmdb_cache",
        "group": "系统运维",
        "label": "清理 TMDB 元数据缓存",
        "sample": "清一下TMDB缓存",
        "desc": "清空本地的 TMDB 元数据缓存。当用户说「清一下TMDB缓存」「媒体信息不更新」时使用。",
        "params": {"type": "object", "properties": {}},
        "kind": "write",
    },
    {
        "name": "restart_service",
        "action": "restart",
        "group": "系统运维",
        "label": "重启 NAStool 服务",
        "sample": "重启一下服务",
        "desc": "重启 NAStool 服务。会中断正在进行的搜索、转移、刷流等任务，"
                "重启期间服务不可用。仅当用户明确要求重启时调用。",
        "params": {"type": "object", "properties": {}},
        "kind": "ops",
        "dangerous": True,
    },
    {
        "name": "update_system",
        "action": "update_system",
        "group": "系统运维",
        "label": "升级 NAStool 到最新版",
        "sample": "升级到最新版",
        "desc": "升级 NAStool 到最新版本。升级会重启服务，且失败时可能需要手动恢复。"
                "仅当用户明确要求升级时调用。",
        "params": {"type": "object", "properties": {}},
        "kind": "ops",
        "dangerous": True,
    },

    # ---------------- 元能力（模型用来自我介绍与追问） ----------------
    {
        "name": "list_capabilities",
        "action": None,
        "group": "使用帮助",
        "label": "列出我全部能做的事",
        "sample": "你能做什么",
        "desc": "列出你能为用户做的全部事情（按分组），供用户挑选。"
                "当用户问「你能做什么」「有什么功能」「帮助」「菜单」「怎么用」"
                "或用户不知道该说什么、消息无法识别意图、只是打招呼时使用。"
                "调用后把菜单原样发给用户即可，不要改写条目。",
        "params": {"type": "object", "properties": {}},
        "kind": "meta",
    },
    {
        "name": "ask_user",
        "action": None,
        "group": "使用帮助",
        "hidden": True,
        "desc": "当你无法判断用户想做什么、或要执行某个操作但缺少必要信息时，用本工具向用户提问。"
                "系统会把问题原样发给用户，并在用户回答后把上下文带回给你。"
                "只问最关键的一个问题，可以给两三个候选，不要一次问一堆。",
        "params": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的问题，简洁口语化，一次只问一件事"},
                "tool": {"type": "string", "description": "打算在用户回答后调用的工具名（若已确定）"},
                "args": {"type": "object", "description": "目前已确定的参数"},
                "missing": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "还缺少的参数名"
                }
            },
            "required": ["question"]
        },
        "kind": "meta",
    },
]

# 危险等级对应的中文提示，用于二次确认
_DANGER_LABEL = {
    "restart_service": "重启 NAStool 服务（会中断正在运行的任务）",
    "update_system": "升级 NAStool 到最新版本（会重启服务）",
    "auto_remove_torrents": "执行自动删种（可能删除种子文件）",
    "truncate_history": "清空历史记录（不可恢复）",
    "torrent_delete": "删除下载任务及其文件（不可恢复）",
    "delete_rss_history": "删除订阅下载历史（不可恢复）",
    "truncate_unknown": "清空未识别文件记录（不可恢复）",
}

# 清空历史时 kind 到 action 的映射
_TRUNCATE_ACTION = {
    "transfer": "truncate_transfer_history",
    "rss": "truncate_rsshistory",
    "blacklist": "truncate_blacklist",
}

# 缺参追问的载荷前缀。AgentTools 产出后由 OpenAiHelper 识别，
# 直接把问题发给用户并挂起待办，不再经过模型。
ASK_PREFIX = "__NASTOOL_ASK__"

# 不是"动作"的工具，不写审计日志
_NON_ACTION_KIND = ("read", "meta")


def _trim_lists(obj, max_items, depth=0):
    """
    递归裁剪结果中的列表，避免超长结果撑爆模型上下文
    """
    if depth > 4:
        return str(obj)[:200]
    if isinstance(obj, dict):
        return {str(k): _trim_lists(v, max_items, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        items = [_trim_lists(v, max_items, depth + 1) for v in list(obj)[:max_items]]
        if len(obj) > max_items:
            items.append(f"（共 {len(obj)} 条，已省略其余）")
        return items
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    # 数据库记录对象等，取 as_dict 或直接转字符串
    as_dict = getattr(obj, "as_dict", None)
    if callable(as_dict):
        try:
            return _trim_lists(as_dict(), max_items, depth + 1)
        except Exception:
            pass
    return str(obj)[:200]


def _format_result(result, max_items=20, max_len=3500):
    """
    把 action 的返回值整理成给模型看的紧凑文本
    """
    if result is None:
        return "（操作已提交执行，无返回数据）"
    trimmed = _trim_lists(result, max_items)
    try:
        text = json.dumps(trimmed, ensure_ascii=False, default=str)
    except Exception:
        text = str(trimmed)
    if len(text) > max_len:
        text = text[:max_len] + f"……（结果过长，已截断。原始长度 {len(text)} 字符）"
    return text


class AgentTools:
    """
    AI Agent 工具集：把 NAStool 的能力暴露给大模型调用
    """

    def __init__(self):
        self._tools = {t["name"]: t for t in AGENT_TOOLS}

    # ---------------- 工具表访问 ----------------

    @staticmethod
    def get_tools():
        """
        返回全部工具定义
        """
        return AGENT_TOOLS

    @staticmethod
    def get_tool_names():
        """
        返回全部工具名
        """
        return [t["name"] for t in AGENT_TOOLS]

    def get_tool(self, name):
        """
        返回单个工具定义
        """
        return self._tools.get(name)

    @staticmethod
    def get_schemas():
        """
        生成 OpenAI functions 参数格式的工具声明
        """
        schemas = []
        for t in AGENT_TOOLS:
            schemas.append({
                "name": t["name"],
                "description": t["desc"],
                "parameters": t.get("params") or {"type": "object", "properties": {}},
            })
        return schemas

    @staticmethod
    def get_tools_prompt():
        """
        生成文本协议下使用的工具说明（后端不支持 function calling 时的兜底）
        """
        lines = []
        current_group = None
        for t in AGENT_TOOLS:
            if t.get("hidden"):
                continue
            group = t.get("group")
            if group and group != current_group:
                lines.append("## %s" % group)
                current_group = group
            props = (t.get("params") or {}).get("properties") or {}
            required = (t.get("params") or {}).get("required") or []
            args = "、".join(
                "%s%s(%s)" % (k, "*" if k in required else "", v.get("type") or "string")
                for k, v in props.items()
            )
            lines.append("- %s：%s\n  参数：%s" % (t["name"], t["desc"], args or "无"))
        lines.append("（参数名后带 * 的为必填）")
        return "\n".join(lines)

    # ---------------- 能力目录（给用户看） ----------------

    @staticmethod
    def get_capabilities():
        """
        按分组聚合可用于展示的能力，返回 [(分组名, [工具定义, ...]), ...]
        """
        groups = {}
        for t in AGENT_TOOLS:
            if t.get("hidden") or not t.get("sample"):
                continue
            if not t.get("label"):
                continue
            groups.setdefault(t.get("group") or "其它", []).append(t)
        ordered = []
        for name in GROUP_ORDER:
            if groups.get(name):
                ordered.append((name, groups[name]))
                groups.pop(name)
        # 未在 GROUP_ORDER 中登记的分组兜底排在后面
        for name, items in groups.items():
            ordered.append((name, items))
        return ordered

    @classmethod
    def get_capability_menu(cls):
        """
        生成面向用户的能力清单文本。

        这份文本是本工具的"产品说明书"，既用于「你能做什么」的回答，
        也用于首次接触时的主动介绍，保证两种场景口径一致。
        """
        lines = ["我能帮你做这些事，直接用大白话说就行：", ""]
        for group, items in cls.get_capabilities():
            lines.append("【%s】" % group)
            for t in items:
                lines.append("· %s  → 例：「%s」" % (t.get("label"), t.get("sample")))
            lines.append("")
        lines.append("记不住也没关系，直接说需求就行；不确定我会先问你。")
        return "\n".join(lines).strip()

    # ---------------- 校验与判定 ----------------

    def is_dangerous(self, name):
        """
        判断工具是否为危险操作
        """
        tool = self._tools.get(name)
        return bool(tool and tool.get("dangerous"))

    def is_action_tool(self, name):
        """
        判断工具是否需要记审计日志（写操作/运维操作）
        """
        tool = self._tools.get(name)
        return bool(tool and tool.get("kind") not in _NON_ACTION_KIND)

    @staticmethod
    def get_required(name):
        """
        返回工具的必填参数名列表
        """
        for t in AGENT_TOOLS:
            if t["name"] == name:
                return list((t.get("params") or {}).get("required") or [])
        return []

    @staticmethod
    def validate_args(name, args):
        """
        校验必填参数，返回缺失的参数名列表
        """
        args = args or {}
        missing = []
        for key in AgentTools.get_required(name):
            value = args.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(key)
        return missing

    @staticmethod
    def build_ask_payload(name, args, missing):
        """
        生成缺参追问载荷。

        故意不交给模型措辞：缺参是确定性的，由代码直接生成问题可以保证
        「一定追问」，不会因为模型忽略提示而带着缺失参数硬调工具。
        若工具表里为该参数配了 ask 话术，就用人话；否则退回参数描述。
        """
        tool = None
        for t in AGENT_TOOLS:
            if t["name"] == name:
                tool = t
                break
        props = ((tool or {}).get("params") or {}).get("properties") or {}
        ask_map = (tool or {}).get("ask") or {}

        parts = []
        for key in missing:
            text = ask_map.get(key)
            if not text:
                desc = (props.get(key) or {}).get("description") or key
                text = "请提供「%s」（%s）" % (key, desc)
            parts.append(text)
        question = " ".join(parts).strip() or "请补充更多信息。"

        return ASK_PREFIX + json.dumps({
            "question": question,
            "tool": name,
            "args": args or {},
            "missing": missing,
        }, ensure_ascii=False)

    @staticmethod
    def is_ask(result):
        """
        判断工具返回值是否是「向用户提问」的载荷
        """
        return isinstance(result, str) and result.startswith(ASK_PREFIX)

    @staticmethod
    def extract_ask(result):
        """
        解析追问载荷，返回 {"question":…, "tool":…, "args":…, "missing":…}
        """
        if not AgentTools.is_ask(result):
            return {}
        try:
            data = json.loads(result[len(ASK_PREFIX):])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def get_danger_prompt(name, args):
        """
        生成二次确认的提示文案
        """
        label = _DANGER_LABEL.get(name) or name
        detail = ""
        if args:
            try:
                detail = "，参数：" + json.dumps(args, ensure_ascii=False)
            except Exception:
                detail = ""
        return "即将执行：%s%s。\n\n请回复「确认」执行，回复其它内容取消。" % (label, detail)

    # ---------------- 执行 ----------------

    def call(self, name, args=None, context=None):
        """
        执行工具调用
        :param name: 工具名
        :param args: 参数字典
        :param context: 调用上下文 {"user_id":…, "in_from":…, "user_name":…}
        :return: 给模型看的文本结果；若为 ASK_PREFIX 开头则是追问载荷
        """
        tool = self._tools.get(name)
        if not tool:
            return "未找到名为 %s 的工具，请改用其它工具或直接回答用户。" % name

        args = args if isinstance(args, dict) else {}
        context = context or {}

        # 参数清理：去掉 None / 空串，避免污染底层逻辑
        clean_args = {}
        for k, v in args.items():
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            clean_args[k] = v.strip() if isinstance(v, str) else v

        # 必填参数校验：缺参数不执行，转为向用户追问
        missing = self.validate_args(name, clean_args)
        if missing:
            if name == "ask_user":
                # 反问工具自己缺 question，说明模型用错了，退回提示让它重来
                return "ask_user 缺少 question 参数，请重新调用并填写要问用户的问题。"
            log.info("【Agent】工具 %s 缺少必填参数 %s，转为追问用户" % (name, missing))
            return self.build_ask_payload(name, clean_args, missing)

        try:
            result = self._dispatch(tool, clean_args, context)
        except Exception as err:
            log.error("【Agent】工具 %s 执行失败：%s" % (name, str(err)))
            return "工具 %s 执行失败：%s" % (name, str(err))

        log.info("【Agent】执行工具 %s，参数 %s，调用者 %s"
                 % (name, json.dumps(clean_args, ensure_ascii=False),
                    context.get("user_id") or "未知"))

        # 元能力与搜索类自行返回成品文本，不做 JSON 裁剪
        if tool.get("kind") == "meta" or name == "search_media":
            return result

        return _format_result(result, max_items=tool.get("max_items") or 20)

    def _dispatch(self, tool, args, context):
        """
        按工具定义分发到具体实现
        """
        name = tool["name"]

        # 元能力：能力清单
        if name == "list_capabilities":
            return self.get_capability_menu()

        # 元能力：向用户提问
        if name == "ask_user":
            return self._build_ask_payload(args)

        # 组合型工具：一次调用映射到多个底层 action
        if name == "truncate_history":
            action = _TRUNCATE_ACTION.get(str(args.get("kind")))
            if not action:
                return "kind 参数有误，只能是 transfer / rss / blacklist"
            return self._call_action(action, {})

        # 搜索类：直接走消息搜索链路，结果由系统推送给用户
        if name == "search_media":
            keyword = str(args.get("keyword") or "").strip()
            if not keyword:
                return "请提供要搜索的关键词。"
            return self._search_media(keyword, context)

        return self._call_action(tool["action"], args)

    @staticmethod
    def _build_ask_payload(args):
        """
        把模型给出的 ask_user 参数整理成标准追问载荷
        """
        question = str(args.get("question") or "").strip()
        if not question:
            question = "能否再说明一下你的需求？"
        known = args.get("args") if isinstance(args.get("args"), dict) else {}
        missing = args.get("missing") if isinstance(args.get("missing"), list) else []
        return ASK_PREFIX + json.dumps({
            "question": question,
            "tool": args.get("tool"),
            "args": known,
            "missing": [str(m) for m in missing],
        }, ensure_ascii=False)

    @staticmethod
    def _call_action(action, data):
        """
        调用 WebAction 的 action 接口
        """
        if not action:
            return None
        from web.action import WebAction
        return WebAction().action(action, data)

    @staticmethod
    def _search_media(keyword, context):
        """
        走既有消息搜索链路搜索资源，结果由系统直接推送给用户
        """
        from web.backend.search_torrents import search_media_by_message
        from app.utils.types import SearchType

        in_from = context.get("in_from") or SearchType.OT
        search_media_by_message(input_str=keyword,
                                in_from=in_from,
                                user_id=context.get("user_id"),
                                user_name=context.get("user_name"))
        return "已发起「%s」的资源搜索，搜索结果稍后推送给用户，无需你再复述。" % keyword
