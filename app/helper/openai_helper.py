import json
import re
import time

import openai

import log
from app.utils import OpenAISessionCache, ExceptionUtils
from app.utils.commons import singleton
from config import Config


class _UnsupportedToolsError(Exception):
    """
    后端不支持 function calling 时抛出，用于触发文本协议降级
    """
    pass


# 默认的 OpenAI 官方地址（用户未填写 API Url 时使用）
_DEFAULT_API_BASE = "https://api.openai.com"


def _normalize_api_url(api_url):
    """
    规整用户填写的 API 地址：去掉末尾斜杠与多余的 /v1

    调用方统一在末尾追加 /v1，所以用户填 https://xx 、 https://xx/ 、
    https://xx/v1 三种写法都能得到同一个请求地址，避免出现 //v1 或 /v1/v1。
    """
    url = (api_url or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def _strip_code_fence(text):
    """
    剥掉模型输出外层的 markdown 代码围栏（```json ... ```）

    云端模型与本地模型都很容易把 JSON 包在围栏里返回，直接 json.loads 会
    抛错，表现为「功能悄悄失效」。文本协议解析与文件名识别共用本函数。
    """
    if not text:
        return ""
    s = str(text).strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s).strip()
    return s


def _extract_json_object(text):
    """
    从模型输出里尽力取出一个 JSON 对象，成功返回 dict，否则返回 None

    容忍两种常见偏差：外层 markdown 代码围栏、前后多余的说明文字。
    """
    s = _strip_code_fence(text)
    if not s:
        return None
    if not (s.startswith("{") and s.endswith("}")):
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            return None
        s = s[start:end + 1]
    try:
        data = json.loads(s)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _looks_like_tool_call(text):
    """
    判断模型输出是否「本意是工具调用」，而不是给用户看的话

    用于守住最后的出口：解析失败时若仍长得像工具调用，绝不能把它当成用户答复
    发出去 —— 用户会收到一串内部 JSON，而且工具一次都没执行过。
    容错分两层：能解析出 JSON 就看键名；JSON 被截断解析不动时，退回键名特征匹配。
    """
    if not text:
        return False
    data = _extract_json_object(text)
    if isinstance(data, dict) and ("tool" in data or "args" in data):
        return True
    s = _strip_code_fence(text)
    if s[:1] in ("{", "[") or "```" in str(text):
        return bool(re.search(r'["\']?(tool|args)["\']?\s*:', s, re.I))
    return False


def _describe_error(err, base, model, timeout):
    """
    把 SDK 异常翻译成用户能直接照做的结论

    注意：openai 0.28 的异常体系与 1.x 不同——
    没有 NotFoundError，HTTP 404 也归到 InvalidRequestError，
    状态码要从 err.http_status 取；而且这些异常都直接继承 OpenAIError。
    """
    status = getattr(err, "http_status", None)
    detail = ""
    json_body = getattr(err, "json_body", None)
    if isinstance(json_body, dict):
        detail = ((json_body.get("error") or {}).get("message") or "").strip()
    if not detail:
        detail = str(err).strip()

    if isinstance(err, openai.error.AuthenticationError):
        return "连接失败：API Key 无效或未授权（HTTP 401），请检查 Key 是否正确、是否已失效"
    if isinstance(err, openai.error.PermissionError):
        return "连接失败：API Key 无权访问该接口（HTTP 403）"
    if isinstance(err, openai.error.RateLimitError):
        return "连接失败：被限流或余额不足（HTTP 429），请稍后重试或检查账户额度"
    if isinstance(err, openai.error.Timeout):
        return "连接超时：%d 秒内没有收到响应。地址是通的，但服务未及时返回，" \
               "请确认推理服务已启动、模型已加载" % timeout
    if isinstance(err, openai.error.APIConnectionError):
        cause = err.__cause__
        return "连接失败：无法访问 %s（%s）。请检查地址、端口、网络与代理设置" % (
            base, str(cause) if cause else "网络不可达")
    if status == 404 or "not found" in detail.lower():
        return "连接失败：接口地址不存在（HTTP 404）。请检查 API Url 是否正确" \
               "（程序会自动补 /v1，无需自己填），以及该地址是否提供 OpenAI 兼容接口"
    if isinstance(err, openai.error.InvalidRequestError):
        return "连接失败：请求被拒绝（HTTP %s）：%s。常见原因是模型名 %s 不存在或参数不受支持" % (
            status or 400, detail, model)
    if isinstance(err, (openai.error.ServiceUnavailableError, openai.error.TryAgain)):
        return "连接失败：后端服务暂时不可用（HTTP %s）：%s，请稍后重试" % (status or "5xx", detail)
    if status:
        return "连接失败：后端返回 HTTP %s：%s" % (status, detail)
    return "连接失败：%s：%s" % (type(err).__name__, detail or "未知错误")


@singleton
class OpenAiHelper:
    _api_key = None
    _api_url = None
    _model = "gpt-3.5-turbo"

    # Agent 模式配置
    _agent_enable = True
    _agent_max_rounds = 5
    _agent_confirm_dangerous = True
    _agent_show_tools = False
    _agent_guide_enable = True
    _agent_protocol = "auto"

    # 配置快照：用于检测配置是否在进程运行期间被界面保存修改（见 __ensure_fresh）
    _conf_snapshot = None

    # 危险操作待确认队列：user_id -> {"tool": 工具名, "args": 参数, "time": 时间戳}
    # 只在 __init__ 里初始化，不能被 init_config 重置
    # （__ensure_fresh 会在配置变更时重新调用 init_config）
    _pending_confirm = {}

    # 追问补全队列：user_id -> {"tool": 目标工具, "args": 已知参数,
    #                          "missing": 待补参数列表, "time": 时间戳}
    # 与 _pending_confirm 同理，不能被 init_config 重置
    _pending_ask = {}

    # 待确认状态的有效期（秒），超时视为用户已放弃
    _CONFIRM_TTL = 300

    # 追问的有效期（秒）。比确认长一些，因为用户可能要想一下片名/站点名
    _ASK_TTL = 600

    # 疑似工具调用被拦下时的兜底回复（绝不把模型输出的 JSON 发给用户）
    _GUARD_REPLY = ("抱歉，我这次没能正确理解你的指令。请换一种说法再说一遍，"
                    "例如「在下载什么」「磁盘还剩多少」。")

    # 拦下后重试一次用的纠正指令
    _RETRY_HINT = ("你刚才的输出看起来是工具调用 JSON，但系统没能识别它。"
                   "请直接用简洁的中文回答用户，不要输出任何 JSON。")

    # 用户表示确认的常见措辞
    _CONFIRM_WORDS = ("确认", "确定", "执行", "可以", "好的", "好", "是的", "是", "嗯",
                      "y", "yes", "ok", "confirm")

    # 用户表示放弃的常见措辞
    _CANCEL_WORDS = ("取消", "算了", "不用了", "不弄了", "不需要", "别弄了", "cancel")

    def __init__(self):
        self._pending_confirm = {}
        self._pending_ask = {}
        self.init_config()

    def init_config(self):
        openai_conf = Config().get_config("openai") or {}
        self._api_key = openai_conf.get("api_key")
        if self._api_key:
            openai.api_key = self._api_key
        self._api_url = _normalize_api_url(openai_conf.get("api_url"))
        if self._api_url:
            openai.api_base = self._api_url + "/v1"
        else:
            proxy_conf = Config().get_proxies()
            if proxy_conf and proxy_conf.get("https"):
                openai.proxy = proxy_conf.get("https")
        # 模型名与 Agent 参数，允许通过配置覆盖（本地模型 / 兼容中转常需要改模型名）
        self._model = openai_conf.get("model") or "gpt-3.5-turbo"
        self._agent_enable = openai_conf.get("agent_enable", True)
        self._agent_max_rounds = int(openai_conf.get("agent_max_rounds") or 5)
        self._agent_confirm_dangerous = openai_conf.get("agent_confirm_dangerous", True)
        self._agent_show_tools = bool(openai_conf.get("agent_show_tools", False))
        self._agent_guide_enable = openai_conf.get("agent_guide_enable", True)
        self._agent_protocol = (openai_conf.get("agent_protocol") or "auto").lower()
        self._conf_snapshot = self.__make_snapshot(openai_conf)

    @staticmethod
    def __make_snapshot(openai_conf):
        """
        生成配置快照，用于检测配置是否被修改过
        """
        return (
            openai_conf.get("api_key"), openai_conf.get("api_url"),
            openai_conf.get("model"), openai_conf.get("agent_enable"),
            openai_conf.get("agent_max_rounds"), openai_conf.get("agent_confirm_dangerous"),
            openai_conf.get("agent_show_tools"), openai_conf.get("agent_guide_enable"),
            openai_conf.get("agent_protocol"),
        )

    def __ensure_fresh(self):
        """
        配置可能在进程运行期间被界面保存修改，这里按需重新加载，
        避免出现「改了配置必须重启服务才生效」的问题。
        配置未变化时只有一个元组比较的开销，不影响性能。
        """
        try:
            conf = Config().get_config("openai") or {}
            if self.__make_snapshot(conf) != self._conf_snapshot:
                self.init_config()
                log.info("【OpenAI】检测到配置变更，已重新加载")
        except Exception as err:
            log.error("【OpenAI】重新加载配置失败：%s" % str(err))

    def get_state(self):
        self.__ensure_fresh()
        return True if self._api_key else False

    @staticmethod
    def test_connection(api_url=None, api_key=None, model=None, timeout=12):
        """
        连通性测试：用传入的参数（可以是界面上还没保存的内容）向 OpenAI 兼容后端
        发一次最小请求，返回结构化的结果供设置页「测试连接」展示。

        - 只使用**请求级** api_key / api_base，不改动 openai 模块的全局配置，
          因此测试不会影响正在运行的 AI 助手（不会把它切到未保存的地址上）。
        - 地址规整规则与 init_config 完全一致，所以「测试通过」约等于「保存后可用」。

        :return: {"success": bool, "msg": str, "model": str,
                  "elapsed": int(毫秒), "functions": True/False/None,
                  "reply": str, "hint": str}
        """
        result = {
            "success": False,
            "msg": "",
            "model": (model or "").strip() or "gpt-3.5-turbo",
            "elapsed": 0,
            "functions": None,
            "reply": "",
            "hint": "",
        }
        raw_url = api_url or ""
        api_url = _normalize_api_url(raw_url)
        api_key = (api_key or "").strip()
        result["model"] = (model or "").strip() or "gpt-3.5-turbo"
        if raw_url.strip() and raw_url.strip() != api_url:
            result["hint"] = "已自动规整地址末尾的 / 或 /v1"
        if not api_key:
            result["msg"] = "未填写 API Key：使用 OpenAI 官方或第三方中转时必须填写；" \
                            "接入本地模型时填任意非空值即可"
            return result

        base = (api_url or _DEFAULT_API_BASE) + "/v1"
        # ① 最小对话请求：能返回就说明「地址可通 + 鉴权通过 + 模型可用」
        started = time.time()
        try:
            completion = openai.ChatCompletion.create(
                model=result["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=16,
                api_key=api_key,
                api_base=base,
                request_timeout=timeout,
            )
        except Exception as err:
            result["elapsed"] = int((time.time() - started) * 1000)
            result["msg"] = _describe_error(err, base, result["model"], timeout)
            return result
        result["elapsed"] = int((time.time() - started) * 1000)
        result["success"] = True
        try:
            result["model"] = completion["model"] or result["model"]
        except Exception:
            pass
        try:
            reply = (completion["choices"][0]["message"]["content"] or "").strip()
            result["reply"] = reply.replace("\n", " ")[:40]
        except Exception:
            pass
        # ② 工具调用探测：AI 助手优先走原生 function calling，
        #    后端不接受 functions 参数时会自动降级为文本协议（功能不受影响，仅效率略低）
        try:
            openai.ChatCompletion.create(
                model=result["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=16,
                api_key=api_key,
                api_base=base,
                request_timeout=timeout,
                functions=[{
                    "name": "noop",
                    "description": "no operation",
                    "parameters": {"type": "object", "properties": {}},
                }],
            )
            result["functions"] = True
        except Exception:
            result["functions"] = False
        result["msg"] = "连接成功"
        return result

    def has_pending(self, userid):
        """
        该用户是否正处于「追问补全」流程中。

        消息路由需要这个判断：用户对追问的回答（比如「沙丘」）往往不以
        「搜索/下载」等关键词开头，若不加干预会被别的分支截走，
        追问流程就断了。
        """
        if not userid:
            return False
        info = self._pending_ask.get(str(userid))
        if not info:
            return False
        if time.time() - info.get("time", 0) > self._ASK_TTL:
            self._pending_ask.pop(str(userid), None)
            return False
        return True

    @staticmethod
    def __save_session(session_id, message):
        """
        保存会话
        :param session_id: 会话ID
        :param message: 消息
        :return:
        """
        seasion = OpenAISessionCache.get(session_id)
        if seasion:
            seasion.append({
                "role": "assistant",
                "content": message
            })
            OpenAISessionCache.set(session_id, seasion)

    @staticmethod
    def __get_session(session_id, message):
        """
        获取会话
        :param session_id: 会话ID
        :return: 会话上下文
        """
        seasion = OpenAISessionCache.get(session_id)
        if seasion:
            seasion.append({
                "role": "user",
                "content": message
            })
        else:
            seasion = [
                {
                    "role": "system",
                    "content": "请在接下来的对话中请使用中文回复，并且内容尽可能详细。"
                },
                {
                    "role": "user",
                    "content": message
                }]
            OpenAISessionCache.set(session_id, seasion)
        return seasion

    @staticmethod
    def __build_message(message, prompt=None):
        """
        把字符串输入整理成标准的 messages 结构
        """
        if isinstance(message, list):
            return message
        if prompt:
            return [
                {
                    "role": "system",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": message
                }
            ]
        return [
            {
                "role": "user",
                "content": message
            }
        ]

    def __get_model(self,
                    message,
                    prompt=None,
                    user="NAStool",
                    **kwargs):
        """
        获取模型
        """
        return openai.ChatCompletion.create(
            model=self._model,
            user=user,
            messages=self.__build_message(message, prompt),
            **kwargs
        )

    @staticmethod
    def __clear_session(session_id):
        """
        清除会话
        :param session_id: 会话ID
        :return:
        """
        if OpenAISessionCache.get(session_id):
            OpenAISessionCache.delete(session_id)

    def get_media_name(self, filename):
        """
        从文件名中提取媒体名称等要素

        供「AI 辅助识别」（laboratory.chatgpt_enable）使用。与 AI 助手共用同一份
        openai.* 配置和同一个客户端实例，不需要单独配置 API。
        识别失败返回 {}，后端不可用返回 None，调用方据此区分。

        :param filename: 文件名
        :return: Json
        """
        if not self.get_state():
            return None
        result = ""
        try:
            _filename_prompt = "I will give you a movie/tvshow file name.You need to return a Json." \
                               "\nPay attention to the correct identification of the film name." \
                               "\n{\"title\":string,\"version\":string,\"part\":string,\"year\":string,\"resolution\":string,\"season\":number|null,\"episode\":number|null}"
            # 这是自动流程里的一次单轮调用，超时按 60 秒给足即可；
            # 不传会走 SDK 默认的 600 秒，后端卡住时会把搜索/整理流程挂住
            completion = self.__get_model(prompt=_filename_prompt,
                                          message=filename,
                                          timeout=60)
            result = completion.choices[0].message.content
            data = _extract_json_object(result)
            if data is None:
                # 模型回了内容但不是 JSON：把原始返回打出来。否则日志里只有
                # 「识别失败」，无法判断是模型输出格式的问题还是接口的问题
                log.error("【OpenAI】识别文件名返回的内容不是 JSON：%s"
                          % str(result).replace("\n", " ")[:200])
                return {}
            return data
        except Exception as e:
            log.error("【OpenAI】识别文件名失败：%s（原始返回：%s）"
                      % (str(e), str(result).replace("\n", " ")[:200]))
            return {}

    def get_answer(self, text, userid, context=None):
        """
        获取答案

        Agent 模式（默认）：大模型可自主调用工具查询、操作系统（下载器/站点/订阅/媒体库等），
        信息不全时会主动向用户追问，用户回答后自动补全并执行。
        关闭 openai.agent_enable 后退回纯文本聊天，行为与旧版一致。

        :param text: 输入文本
        :param userid: 用户ID
        :param context: 调用上下文，如 {"in_from": SearchType.FEISHU}，供工具回推消息使用
        :return: 回复文本
        """
        if not self.get_state():
            return ""
        if not userid:
            return "用户信息错误"
        userid = str(userid)
        context = dict(context or {})
        context.setdefault("user_id", userid)

        if text and text.strip() == "#清除":
            self.__clear_session(userid)
            self._pending_confirm.pop(userid, None)
            self._pending_ask.pop(userid, None)
            return "会话已清除"

        try:
            if not self._agent_enable:
                return self.__chat_only(text, userid)
            return self.__agent_run(text, userid, context)
        except openai.error.RateLimitError as e:
            return f"请求被ChatGPT拒绝了，{str(e)}"
        except openai.error.APIConnectionError as e:
            return "ChatGPT网络连接失败！"
        except openai.error.Timeout as e:
            return "没有接收到ChatGPT的返回消息！"
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return f"请求ChatGPT出现错误：{str(e)}"

    def __chat_only(self, text, userid):
        """
        纯聊天模式：不开放工具，仅带历史上下文问答
        """
        messages = self.__get_session(userid, text)
        completion = self.__get_model(message=messages, user=userid)
        result = (completion.choices[0].message.content or "").strip()
        if _looks_like_tool_call(result):
            # 纯聊天模式没有工具，但模型仍可能吐工具调用 JSON；
            # 这种内容一旦发出去，用户看到的就是一串内部 JSON
            log.warn("【OpenAI】纯聊天模式输出疑似工具调用，已拦下不外发")
            result = self._GUARD_REPLY
        if result:
            # 注意：此处保存的是模型的回复。历史实现曾误传用户输入，
            # 导致会话里助手的话全是用户自己的问题，已修正。
            self.__save_session(userid, result)
        return result

    def __agent_run(self, text, userid, context):
        """
        Agent 模式主流程

        触发顺序：危险操作确认 → 追问补全 → 正常处理。
        前两者都是"上一轮遗留的待办"，必须先结清，否则用户这轮的回复会被误解。
        """
        from app.helper.agent_tools import AgentTools
        tools = AgentTools()

        # 一、处理上一步遗留的危险操作确认
        pending = self._pending_confirm.pop(userid, None)
        if pending:
            if self.__is_confirm(text):
                if time.time() - pending.get("time", 0) > self._CONFIRM_TTL:
                    return ("该确认已超过 %d 分钟有效期，出于安全考虑已作废，请重新发起操作。"
                            % (self._CONFIRM_TTL // 60))
                result = tools.call(pending.get("tool"), pending.get("args"), context)
                self.__audit(userid, pending.get("tool"), pending.get("args"), via_ai=False)
                return self.__summarize(userid,
                                        "用户已确认并执行了操作「%s」。" % pending.get("tool"),
                                        result)
            # 用户没有确认：丢弃该待办，继续按普通消息处理

        # 二、处理上一步遗留的追问补全
        ask_ctx = self._pending_ask.pop(userid, None)
        if ask_ctx and time.time() - ask_ctx.get("time", 0) > self._ASK_TTL:
            ask_ctx = None
        if ask_ctx and self.__is_cancel(text):
            return "好的，已取消。还需要我做什么，直接说就行。"

        # 三、是否是与该用户的首次对话（用于主动介绍能力）
        first_time = not OpenAISessionCache.get(userid)

        # 四、按协议执行，functions 不被支持时自动降级为文本协议
        if self._agent_protocol == "prompt":
            return self.__agent_loop_prompt(userid, text, context, tools, ask_ctx, first_time)
        try:
            return self.__agent_loop_functions(userid, text, context, tools, ask_ctx, first_time)
        except _UnsupportedToolsError as err:
            log.warn("【Agent】当前模型不支持 function calling（%s），改用文本协议" % str(err))
            return self.__agent_loop_prompt(userid, text, context, tools, ask_ctx, first_time)

    def __agent_loop_functions(self, userid, text, context, tools, ask_ctx=None, first_time=False):
        """
        协议一：原生 function calling
        """
        messages = self.__agent_messages(userid, text,
                                         self.__agent_prompt(tools,
                                                             ask_ctx=ask_ctx,
                                                             first_time=first_time))
        functions = tools.get_schemas()
        tool_calls = []

        for _ in range(max(1, self._agent_max_rounds)):
            try:
                completion = self.__get_model(message=messages,
                                              user=userid,
                                              functions=functions,
                                              function_call="auto",
                                              timeout=90)
            except openai.error.OpenAIError as err:
                err_text = str(err).lower()
                if any(k in err_text for k in ("function", "tool", "unsupported", "not support")):
                    raise _UnsupportedToolsError(str(err))
                raise

            msg = completion.choices[0].message
            call = self.__extract_function_call(msg)
            answer = (msg.get("content") or "").strip()
            if not call:
                # 协议互认：后端声称支持 functions，模型却把调用写进了正文
                # （本地小模型与部分中转很常见）。这里按文本协议认下来，
                # 否则这次调用会被当成"答复"直接发给用户。
                call = self.__parse_text_call(answer)
            if not call:
                # 仍认不出来：先确认它真的不是工具调用，再当答复返回
                guard = self.__guard_answer(userid, text, messages, answer)
                if guard:
                    return self.__decorate(guard, tool_calls)
                self.__save_turn(userid, text, answer)
                return self.__decorate(answer, tool_calls)

            name, args = call
            tool_calls.append(name)

            # 危险操作不直接执行，转为待用户确认
            if tools.is_dangerous(name) and self._agent_confirm_dangerous:
                self._pending_confirm[userid] = {"tool": name, "args": args, "time": time.time()}
                self.__save_turn(userid, text, tools.get_danger_prompt(name, args))
                return tools.get_danger_prompt(name, args)

            tool_result = tools.call(name, args, context)

            # 缺参数会返回追问载荷：直接把问题发给用户，并挂起待办
            if tools.is_ask(tool_result):
                return self.__handle_ask(userid, text, tool_result)

            self.__audit(userid, name, args)
            messages.append({
                "role": "assistant",
                "content": None,
                "function_call": {"name": name,
                                  "arguments": json.dumps(args or {}, ensure_ascii=False)},
            })
            messages.append({"role": "function", "name": name, "content": tool_result})

        return self.__too_many_rounds(tool_calls)

    def __agent_loop_prompt(self, userid, text, context, tools, ask_ctx=None, first_time=False):
        """
        协议二：文本 JSON 协议（后端不支持 function calling 时的兜底）
        """
        messages = self.__agent_messages(userid, text,
                                         self.__agent_prompt(tools,
                                                             text_protocol=True,
                                                             ask_ctx=ask_ctx,
                                                             first_time=first_time))
        tool_calls = []

        for _ in range(max(1, self._agent_max_rounds)):
            completion = self.__get_model(message=messages, user=userid, timeout=90)
            content = (completion.choices[0].message.content or "").strip()
            call = self.__parse_text_call(content)
            if not call:
                guard = self.__guard_answer(userid, text, messages, content)
                if guard:
                    return self.__decorate(guard, tool_calls)
                self.__save_turn(userid, text, content)
                return self.__decorate(content, tool_calls)

            name, args = call
            tool_calls.append(name)

            if tools.is_dangerous(name) and self._agent_confirm_dangerous:
                self._pending_confirm[userid] = {"tool": name, "args": args, "time": time.time()}
                self.__save_turn(userid, text, tools.get_danger_prompt(name, args))
                return tools.get_danger_prompt(name, args)

            tool_result = tools.call(name, args, context)

            if tools.is_ask(tool_result):
                return self.__handle_ask(userid, text, tool_result)

            self.__audit(userid, name, args)
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": "工具 %s 的执行结果：\n%s\n\n请基于结果用中文简洁回复用户。"
                           % (name, tool_result),
            })

        return self.__too_many_rounds(tool_calls)

    def __retry_plain_answer(self, userid, messages, bad_content):
        """
        疑似工具调用时重试一次，请模型改用自然语言作答

        只重试一次：重试要额外花一次模型调用，而模型若执意输出 JSON，
        再试多少次都一样，直接退到兜底回复更划算。
        """
        retry_messages = list(messages)
        retry_messages.append({"role": "assistant", "content": bad_content})
        retry_messages.append({"role": "user", "content": self._RETRY_HINT})
        try:
            completion = self.__get_model(message=retry_messages, user=userid, timeout=60)
            retry = (completion.choices[0].message.content or "").strip()
        except Exception as err:
            log.error("【Agent】疑似工具调用重试失败：%s" % str(err))
            return None
        if retry and not _looks_like_tool_call(retry):
            return retry
        return None

    def __guard_answer(self, userid, text, messages, content):
        """
        非工具调用出口的统一守卫

        正常答复返回 None（调用方照常发出）；内容疑似工具调用时返回替代文案：
        先请模型用自然语言重答一次，仍不行就发兜底回复。
        无论走哪条路，写进会话历史的都不是那串 JSON —— 否则模型下一轮会照抄自己。
        """
        if not _looks_like_tool_call(content):
            return None
        log.warn("【Agent】模型输出疑似工具调用但未能识别，已拦下不外发：%s"
                 % content[:200].replace("\n", " "))
        answer = self.__retry_plain_answer(userid, messages, content) or self._GUARD_REPLY
        self.__save_turn(userid, text, answer)
        return answer

    def __handle_ask(self, userid, text, tool_result):
        """
        工具返回追问载荷时的统一处理

        追问是确定性行为（缺参数就是缺参数），所以问题由工具表生成后直接发给用户，
        不再绕模型一圈 —— 这样不会因为模型"忘了问"而带着残缺参数硬执行。
        """
        from app.helper.agent_tools import AgentTools
        ask = AgentTools.extract_ask(tool_result)
        question = ask.get("question") or "请再具体说明一下你的需求。"

        if not self._agent_guide_enable:
            # 用户关掉了引导式询问：不挂起待办，只说明缺什么
            log.info("【Agent】引导式询问已关闭，放弃追问：%s" % question)
            return "还缺少必要信息，暂时没法继续。请把需求说得更具体一些。\n\n" + question

        self._pending_ask[userid] = {
            "tool": ask.get("tool"),
            "args": ask.get("args") or {},
            "missing": ask.get("missing") or [],
            "time": time.time(),
        }
        log.info("【Agent】向用户 %s 追问（目标工具 %s）：%s"
                 % (userid, ask.get("tool") or "未定", question))
        self.__save_turn(userid, text, question)
        return question

    def __summarize(self, userid, instruction, tool_result):
        """
        让模型把工具执行结果转述成自然语言（不再开放工具，纯汇报）
        """
        messages = [
            {"role": "system",
             "content": "你是 NAStool 智能管家，请用简洁中文向用户汇报操作结果，不要输出原始 JSON。"},
            {"role": "user", "content": "%s\n\n执行结果：\n%s" % (instruction, tool_result)},
        ]
        try:
            completion = self.__get_model(message=messages, user=userid, timeout=60)
            answer = (completion.choices[0].message.content or "").strip()
            if answer and not _looks_like_tool_call(answer):
                return answer
            if answer:
                log.warn("【Agent】结果转述输出疑似 JSON，已改用兜底文案")
        except Exception as err:
            log.error("【Agent】结果转述失败：%s" % str(err))
        return "操作已执行。"

    def __agent_prompt(self, tools=None, text_protocol=False, ask_ctx=None, first_time=False):
        """
        构造 Agent 的系统提示词

        这是"询问与调用"的中枢：模型据此决定该直接干活、还是先问用户。
        """
        from app.helper.agent_tools import AgentTools
        tools = tools or AgentTools()
        prompt = (
            "你是 NAStool 的智能管家，运行在用户的家庭服务器上，通过即时通讯工具接受指令。\n"
            "NAStool 是 PT 下载与媒体库管理工具，负责搜索资源、下载、整理入库、站点签到、刷流等。\n\n"
            "你可以调用下列工具来查询和操作系统，工具返回的是真实数据：\n"
            "%s\n\n"
            "【调用原则】\n"
            "1. 用户询问系统状态（下载、站点、订阅、媒体库、磁盘空间等）时，必须调用工具获取真实数据，"
            "绝不能凭想象编造数字或列表。\n"
            "2. 用户要求执行操作时，调用对应工具。\n"
            "3. 不确定片名对应哪部作品时，先用 query_media_info 确认，再执行订阅等操作。\n"
            "4. 涉及删除、重启、升级、清空历史的操作，直接调用工具即可，"
            "系统会自动向用户请求确认，你不需要自己再问一遍。\n"
            "5. 工具执行失败时如实说明原因，不要假装成功。\n"
            "6. 一次回复最多调用一个工具；拿到结果后若还需要其它信息，可以继续调用。\n\n"
            "【询问原则】\n"
            "7. 意图明确、信息齐备时，直接调用工具，不要多余地问东问西。\n"
            "8. 缺少关键信息时（不知道订阅哪部片、删哪个任务、查哪个站点等），"
            "调用 ask_user 只问最关键的一项，可以给两三个候选；不要一次抛出一堆问题。\n"
            "9. 用户问「你能做什么」「有什么功能」「帮助」「菜单」「怎么用」，"
            "或只是打招呼、说了一句无法判断意图的话时，调用 list_capabilities "
            "把能力清单发给用户，并邀请用户直接说出需求。\n"
            "10. 用户补充信息后，把信息合并进参数直接执行，不要重复追问同一项。\n"
            "11. 与系统无关的普通问题（闲聊、常识、技术问答）直接回答，不要调用工具。\n"
            "12. 面向用户的回复都要提炼成简洁的中文，不要输出原始 JSON。\n"
        ) % tools.get_tools_prompt()
        if text_protocol:
            prompt += (
                "\n输出格式（必须严格遵守）：\n"
                "需要调用工具时，只输出一个 JSON，前后不要有任何其它文字：\n"
                '{"tool": "工具名", "args": {"参数名": "参数值"}}\n'
                "不需要调用工具时，直接输出给用户的自然语言回复即可。\n"
            )
        if first_time:
            prompt += (
                "\n【首次对话】这是你与该用户的第一次对话。"
                "请先调用 list_capabilities 拿到能力清单，用简短的几句话介绍你能帮他做什么"
                "（不要照抄整个清单，挑主要几类），然后回答他的问题。\n"
            )
        if ask_ctx:
            prompt += (
                "\n【追问补全中】你上一轮已经向用户提过问，正在等他补充信息。"
                "打算执行的操作是「%s」，已知参数 %s，还缺 %s。\n"
                "若用户本轮的回复是在补充这些信息，请合并参数后直接调用该工具；"
                "若用户换了话题或提出新需求，就忽略这个待办，按新需求处理。\n"
                % (ask_ctx.get("tool") or "尚未确定",
                   json.dumps(ask_ctx.get("args") or {}, ensure_ascii=False),
                   "、".join(ask_ctx.get("missing") or []) or "不限")
            )
        return prompt

    def __agent_messages(self, userid, text, system_prompt):
        """
        构造 Agent 会话消息。返回缓存会话的副本，避免多轮工具调用污染历史。
        """
        history = OpenAISessionCache.get(userid)
        messages = [dict(m) for m in history] if history else []
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": system_prompt}
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": text})
        return messages

    @staticmethod
    def __extract_function_call(msg):
        """
        从模型回复中提取 function_call，返回 (工具名, 参数) 或 None
        """
        func_call = None
        try:
            func_call = msg.get("function_call")
        except Exception:
            func_call = getattr(msg, "function_call", None)
        if not func_call:
            return None
        try:
            name = func_call.get("name")
            raw_args = func_call.get("arguments")
        except Exception:
            name = getattr(func_call, "name", None)
            raw_args = getattr(func_call, "arguments", None)
        if not name:
            return None
        try:
            args = json.loads(raw_args) if raw_args else {}
        except Exception:
            args = {}
        return name, args if isinstance(args, dict) else {}

    @staticmethod
    def __parse_text_call(content):
        """
        从文本协议回复中解析工具调用指令，返回 (工具名, 参数) 或 None
        """
        if not content:
            return None
        # 用 _extract_json_object 而非裸 json.loads：模型常把 JSON 包在
        # ``` 围栏里，或前后带一句说明文字、结尾多个句号。先前要求整体
        # 以 {} 开头结尾，这些情况一律被判为"不是工具调用"，
        # 结果模型的本意（干活）被当成答复发给了用户。
        data = _extract_json_object(content)
        if not isinstance(data, dict) or "tool" not in data:
            return None
        name = data.get("tool")
        if not isinstance(name, str) or not name.strip():
            return None
        args = data.get("args")
        return name.strip(), args if isinstance(args, dict) else {}

    def __is_confirm(self, text):
        """
        判断用户输入是否为确认
        """
        return (text or "").strip().lower() in self._CONFIRM_WORDS

    def __is_cancel(self, text):
        """
        判断用户输入是否为放弃
        """
        return (text or "").strip().lower() in self._CANCEL_WORDS

    def __decorate(self, answer, tool_calls):
        """
        按配置决定是否在回复前标注本次调用了哪些工具
        """
        if answer and self._agent_show_tools and tool_calls:
            return "（已调用：%s）\n\n%s" % ("、".join(tool_calls), answer)
        return answer or "（模型没有返回内容）"

    def __too_many_rounds(self, tool_calls):
        """
        轮数用尽时的兜底回复
        """
        return ("已连续尝试 %d 次仍未得出结论（调用过：%s）。请把需求描述得更具体一些。"
                % (self._agent_max_rounds, "、".join(tool_calls) or "无"))

    def __save_turn(self, userid, text, answer):
        """
        保存一轮完整对话（用户输入 + 助手回复）到会话缓存
        """
        if not answer:
            return
        history = OpenAISessionCache.get(userid)
        if history is None:
            history = []
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": answer})
        OpenAISessionCache.set(userid, history)

    @staticmethod
    def __audit(userid, tool_name, args, via_ai=True):
        """
        审计：把 AI 实际执行的非查询操作记入系统消息中心，便于事后追溯
        """
        try:
            # 延迟导入：agent_tools 是纯数据+包装层，不会被本模块的导入顺序影响
            from app.helper.agent_tools import AgentTools
            if not AgentTools().is_action_tool(tool_name):
                return
            # 延迟导入：app.message 会反向依赖 app.media，而本模块被 app.media 依赖
            from app.message.message_center import MessageCenter
            MessageCenter().insert_system_message(
                title="【AI助手】执行操作",
                content="%s 用户 %s 执行 %s，参数：%s" % (
                    "AI 为" if via_ai else "确认后为",
                    userid, tool_name, json.dumps(args or {}, ensure_ascii=False)))
        except Exception as err:
            log.error("【Agent】审计记录失败：%s" % str(err))

    def translate_to_zh(self, text):
        """
        翻译为中文
        :param text: 输入文本
        """
        if not self.get_state():
            return False, None
        system_prompt = "You are a translation engine that can only translate text and cannot interpret it."
        user_prompt = f"translate to zh-CN:\n\n{text}"
        result = ""
        try:
            completion = self.__get_model(prompt=system_prompt,
                                          message=user_prompt,
                                          temperature=0,
                                          top_p=1,
                                          frequency_penalty=0,
                                          presence_penalty=0)
            result = completion.choices[0].message.content.strip()
            return True, result
        except Exception as e:
            print(f"{str(e)}：{result}")
            return False, str(e)

    def get_question_answer(self, question):
        """
        从给定问题和选项中获取正确答案
        :param question: 问题及选项
        :return: Json
        """
        if not self.get_state():
            return None
        result = ""
        try:
            _question_prompt = "下面我们来玩一个游戏，你是老师，我是学生，你需要回答我的问题，我会给你一个题目和几个选项，你的回复必须是给定选项中正确答案对应的序号，请直接回复数字"
            completion = self.__get_model(prompt=_question_prompt, message=question)
            result = completion.choices[0].message.content
            return result
        except Exception as e:
            print(f"{str(e)}：{result}")
            return {}
