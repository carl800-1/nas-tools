"""
AI Agent 工具集

把 NAStool 已有能力（WebAction._actions）包装成 function calling 工具，
供飞书 / 微信 / Telegram 等 IM 渠道的自然语言指令调用。

关于依赖方向：本模块位于 app 层，却需要调用 web 层的 WebAction，因此所有对
web 层的导入都写在函数内部（延迟导入），避免与 web.action 形成循环依赖。

关于结果裁剪：工具返回值最终会作为上下文喂给模型，站点列表、转移历史这类
结果动辄上千条，必须裁剪，否则一次调用就能把上下文撑爆。
"""

import json

import log

# 工具表
#   name      工具名，模型据此调用，英文小写下划线
#   action    对应的 WebAction().action 命令名；为 None 时走 handler
#   desc      给模型看的描述，需写清"什么时候用"，这是模型选对工具的唯一依据
#   params    JSON Schema 参数定义
#   dangerous 是否危险操作（执行前需用户二次确认）
#   max_items 结果中列表的最大条数
AGENT_TOOLS = [
    # ---------------- 查询类 ----------------
    {
        "name": "query_downloading",
        "action": "get_downloading",
        "desc": "查询下载器中正在下载或做种的任务，返回名称、进度、状态、速度、剩余时间。"
                "当用户问「在下载什么」「下载进度如何」「还有几个任务」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "下载器ID，留空表示查询所有下载器"}
            },
        },
        "max_items": 30,
    },
    {
        "name": "query_downloaded",
        "action": "get_downloaded",
        "desc": "查询已下载完成的媒体历史记录。当用户问「下载过什么」「最近下载了哪些片子」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "页码，从1开始，默认1"}
            },
        },
        "max_items": 30,
    },
    {
        "name": "query_transfer_history",
        "action": "get_transfer_history",
        "desc": "查询媒体文件整理（转移/刮削入库）的历史记录，包含源路径、目标路径、识别结果。"
                "当用户问「入库了什么」「整理记录」「某部片子入库了吗」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "按关键字过滤，留空查询全部"},
                "page": {"type": "integer", "description": "页码，从1开始"}
            },
        },
        "max_items": 30,
    },
    {
        "name": "query_transfer_statistics",
        "action": "get_transfer_statistics",
        "desc": "查询最近90天的媒体整理统计（电影/电视剧/动漫各多少部）。"
                "当用户问「总共入库了多少」「统计一下入库量」时使用。",
        "params": {"type": "object", "properties": {}},
    },
    {
        "name": "query_sites",
        "action": "get_sites",
        "desc": "查询所有已配置的PT站点列表及其状态（是否可用、签到情况、是否维护中）。"
                "当用户问「有哪些站点」「站点正常吗」「哪个站挂了」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "basic": {"type": "boolean", "description": "是否返回详细信息，默认true"}
            },
        },
        "max_items": 60,
    },
    {
        "name": "query_site_statistics",
        "action": "get_site_user_statistics",
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
        "max_items": 60,
    },
    {
        "name": "query_movie_subscribes",
        "action": "get_movie_rss_list",
        "desc": "查询所有电影订阅。当用户问「订阅了哪些电影」「某部电影订阅了吗」时使用。",
        "params": {"type": "object", "properties": {}},
        "max_items": 40,
    },
    {
        "name": "query_tv_subscribes",
        "action": "get_tv_rss_list",
        "desc": "查询所有电视剧订阅，包含已下载到第几季第几集。"
                "当用户问「订阅了哪些剧」「某部剧追到哪了」时使用。",
        "params": {"type": "object", "properties": {}},
        "max_items": 40,
    },
    {
        "name": "query_subscribe_history",
        "action": "get_rss_history",
        "desc": "查询订阅的下载历史。当用户问「订阅都下过什么」「某部剧下载记录」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"}
            },
        },
        "max_items": 30,
    },
    {
        "name": "query_downloaders",
        "action": "get_downloaders",
        "desc": "查询已配置的下载器（qBittorrent/Transmission等）及其连接状态。"
                "当用户问「有哪些下载器」「下载器正常吗」时使用。",
        "params": {"type": "object", "properties": {}},
        "max_items": 20,
    },
    {
        "name": "query_indexers",
        "action": "get_indexers",
        "desc": "查询已配置的索引器及其状态。当用户问「有哪些索引器」时使用。",
        "params": {"type": "object", "properties": {}},
        "max_items": 30,
    },
    {
        "name": "query_media_info",
        "action": "search_media_infos",
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
        "max_items": 10,
    },
    {
        "name": "query_library_space",
        "action": "get_library_spacesize",
        "desc": "查询媒体库磁盘空间占用情况。当用户问「磁盘还剩多少」「空间够吗」时使用。",
        "params": {"type": "object", "properties": {}},
    },
    {
        "name": "query_unknown_files",
        "action": "get_unknown_list",
        "desc": "查询未能识别、未成功入库的文件列表。"
                "当用户问「有哪些没识别」「入库失败的」时使用。",
        "params": {"type": "object", "properties": {}},
        "max_items": 30,
    },
    {
        "name": "query_site_resources",
        "action": "list_site_resources",
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
        "max_items": 25,
    },
    {
        "name": "query_system_processes",
        "action": "get_system_processes",
        "desc": "查询系统正在运行的进程与资源占用。当用户问「系统负载」「CPU内存占用」时使用。",
        "params": {"type": "object", "properties": {}},
        "max_items": 20,
    },
    {
        "name": "query_version",
        "action": "version",
        "desc": "检查 NAStool 是否有新版本可升级。当用户问「有没有新版本」时使用。",
        "params": {"type": "object", "properties": {}},
    },

    # ---------------- 操作类 ----------------
    {
        "name": "add_subscribe",
        "action": "add_rss_media",
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
    },
    {
        "name": "delete_subscribe",
        "action": "remove_rss_media",
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
    },
    {
        "name": "torrent_start",
        "action": "pt_start",
        "desc": "开始（恢复）下载器中的指定任务。当用户说「把某个暂停的任务继续下载」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务ID列表，逗号分隔"}
            },
            "required": ["id"]
        },
    },
    {
        "name": "torrent_stop",
        "action": "pt_stop",
        "desc": "暂停下载器中的指定任务。当用户说「暂停某个下载」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务ID列表，逗号分隔"}
            },
            "required": ["id"]
        },
    },
    {
        "name": "torrent_delete",
        "action": "pt_remove",
        "desc": "从下载器删除指定任务并删除已下载的文件。这是不可逆操作，"
                "调用前必须明确用户要删哪个任务。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "任务ID列表，逗号分隔"}
            },
            "required": ["id"]
        },
        "dangerous": True,
    },
    {
        "name": "re_identify_unknown",
        "action": "re_identification",
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
    },
    {
        "name": "refresh_subscribe",
        "action": "refresh_rss",
        "desc": "立即重新搜索某个订阅的可用资源。当用户说「重新搜一下某部片」「刷新订阅」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["MOV", "TV"], "description": "MOV电影 / TV电视剧"},
                "rssid": {"type": "string", "description": "订阅ID"}
            },
            "required": ["type"]
        },
    },
    {
        "name": "run_brushtask",
        "action": "run_brushtask",
        "desc": "立即执行指定的刷流任务。当用户说「跑一下刷流」「执行刷流任务」时使用，"
                "需要提供刷流任务ID。",
        "params": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "刷流任务ID"}
            },
            "required": ["id"]
        },
    },
    {
        "name": "run_directory_sync",
        "action": "run_directory_sync",
        "desc": "立即执行一次目录同步任务。当用户说「同步一下目录」时使用。",
        "params": {
            "type": "object",
            "properties": {
                "sid": {"type": "string", "description": "同步目录的ID"}
            },
            "required": ["sid"]
        },
    },
    {
        "name": "search_media",
        "action": None,
        "desc": "按名称搜索站点资源并自动下载最匹配的结果。"
                "当用户说「帮我找某部片并下载」「搜索某部片子」时使用。"
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
    },

    # ---------------- 运维类（危险操作，均需二次确认） ----------------
    {
        "name": "restart_service",
        "action": "restart",
        "desc": "重启 NAStool 服务。会中断正在进行的搜索、转移、刷流等任务，"
                "重启期间服务不可用。仅当用户明确要求重启时调用。",
        "params": {"type": "object", "properties": {}},
        "dangerous": True,
    },
    {
        "name": "update_system",
        "action": "update_system",
        "desc": "升级 NAStool 到最新版本。升级会重启服务，且失败时可能需要手动恢复。"
                "仅当用户明确要求升级时调用。",
        "params": {"type": "object", "properties": {}},
        "dangerous": True,
    },
    {
        "name": "auto_remove_torrents",
        "action": "auto_remove_torrents",
        "desc": "立即执行自动删种任务，按配置的规则删除种子（可能同时删除文件）。"
                "仅当用户明确要求时调用。",
        "params": {
            "type": "object",
            "properties": {
                "tid": {"type": "string", "description": "删种任务ID，留空表示执行全部任务"}
            },
        },
        "dangerous": True,
    },
    {
        "name": "truncate_history",
        "action": None,
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
        "dangerous": True,
    },
]

# 危险等级对应的中文提示，用于二次确认
_DANGER_LABEL = {
    "restart_service": "重启 NAStool 服务（会中断正在运行的任务）",
    "update_system": "升级 NAStool 到最新版本（会重启服务）",
    "auto_remove_torrents": "执行自动删种（可能删除种子文件）",
    "truncate_history": "清空历史记录（不可恢复）",
    "torrent_delete": "删除下载任务及其文件（不可恢复）",
}

# 清空历史时 kind 到 action 的映射
_TRUNCATE_ACTION = {
    "transfer": "truncate_transfer_history",
    "rss": "truncate_rsshistory",
    "blacklist": "truncate_blacklist",
}


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
        for t in AGENT_TOOLS:
            props = (t.get("params") or {}).get("properties") or {}
            args = "、".join(
                f"{k}({(v.get('type') or 'string')})" for k, v in props.items()
            )
            lines.append("- %s：%s\n  参数：%s" % (t["name"], t["desc"], args or "无"))
        return "\n".join(lines)

    def is_dangerous(self, name):
        """
        判断工具是否为危险操作
        """
        tool = self._tools.get(name)
        return bool(tool and tool.get("dangerous"))

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

    def call(self, name, args=None, context=None):
        """
        执行工具调用
        :param name: 工具名
        :param args: 参数字典
        :param context: 调用上下文 {"user_id":…, "in_from":…, "user_name":…}
        :return: 给模型看的文本结果
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

        try:
            result = self._dispatch(tool, clean_args, context)
        except Exception as err:
            log.error("【Agent】工具 %s 执行失败：%s" % (name, str(err)))
            return "工具 %s 执行失败：%s" % (name, str(err))

        log.info("【Agent】执行工具 %s，参数 %s，调用者 %s"
                 % (name, json.dumps(clean_args, ensure_ascii=False),
                    context.get("user_id") or "未知"))

        # search_media 会自行推送结果给用户，这里只回一句状态
        if name == "search_media":
            return result

        return _format_result(result, max_items=tool.get("max_items") or 20)

    def _dispatch(self, tool, args, context):
        """
        按工具定义分发到具体实现
        """
        name = tool["name"]

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
