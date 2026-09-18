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
    _agent_protocol = "auto"

    # 危险操作待确认队列：user_id -> {"tool": 工具名, "args": 参数, "time": 时间戳}
    # 放在 __init__ 里初始化，不能在 init_config 里重置（配置保存会触发 init_config）
    _pending_confirm = {}

    # 待确认状态的有效期（秒），超时视为用户已放弃
    _CONFIRM_TTL = 300

    # 用户表示确认的常见措辞
    _CONFIRM_WORDS = ("确认", "确定", "执行", "可以", "好的", "好", "是的", "是", "嗯",
                      "y", "yes", "ok", "confirm")

    def __init__(self):
        self._pending_confirm = {}
        self.init_config()

    def init_config(self):
        openai_conf = Config().get_config("openai") or {}
        self._api_key = openai_conf.get("api_key")
        if self._api_key:
            openai.api_key = self._api_key
        self._api_url = openai_conf.get("api_url")
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
        self._agent_protocol = (openai_conf.get("agent_protocol") or "auto").lower()

    def get_state(self):
        return True if self._api_key else False

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
            completion = self.__get_model(prompt=_filename_prompt, message=filename)
            result = completion.choices[0].message.content
            return json.loads(result)
        except Exception as e:
            print(f"{str(e)}：{result}")
            return {}

    def get_answer(self, text, userid, context=None):
        """
        获取答案

        Agent 模式（默认）：大模型可自主调用工具查询、操作系统（下载器/站点/订阅/媒体库等）。
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
        result = completion.choices[0].message.content
        if result:
            # 注意：此处保存的是模型的回复。历史实现曾误传用户输入，
            # 导致会话里助手的话全是用户自己的问题，已修正。
            self.__save_session(userid, result)
        return result

    def __agent_run(self, text, userid, context):
        """
        Agent 模式主流程
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

        # 二、按协议执行，functions 不被支持时自动降级为文本协议
        if self._agent_protocol == "prompt":
            return self.__agent_loop_prompt(userid, text, context, tools)
        try:
            return self.__agent_loop_functions(userid, text, context, tools)
        except _UnsupportedToolsError as err:
            log.warn("【Agent】当前模型不支持 function calling（%s），改用文本协议" % str(err))
            return self.__agent_loop_prompt(userid, text, context, tools)

    def __agent_loop_functions(self, userid, text, context, tools):
        """
        协议一：原生 function calling
        """
        messages = self.__agent_messages(userid, text, self.__agent_prompt(tools))
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
            if not call:
                answer = (msg.get("content") or "").strip()
                self.__save_turn(userid, text, answer)
                return self.__decorate(answer, tool_calls)

            name, args = call
            tool_calls.append(name)

            # 危险操作不直接执行，转为待用户确认
            if tools.is_dangerous(name) and self._agent_confirm_dangerous:
                self._pending_confirm[userid] = {"tool": name, "args": args, "time": time.time()}
                return tools.get_danger_prompt(name, args)

            tool_result = tools.call(name, args, context)
            self.__audit(userid, name, args)
            messages.append({
                "role": "assistant",
                "content": None,
                "function_call": {"name": name,
                                  "arguments": json.dumps(args or {}, ensure_ascii=False)},
            })
            messages.append({"role": "function", "name": name, "content": tool_result})

        return self.__too_many_rounds(tool_calls)

    def __agent_loop_prompt(self, userid, text, context, tools):
        """
        协议二：文本 JSON 协议（后端不支持 function calling 时的兜底）
        """
        messages = self.__agent_messages(userid, text,
                                         self.__agent_prompt(tools, text_protocol=True))
        tool_calls = []

        for _ in range(max(1, self._agent_max_rounds)):
            completion = self.__get_model(message=messages, user=userid, timeout=90)
            content = (completion.choices[0].message.content or "").strip()
            call = self.__parse_text_call(content)
            if not call:
                self.__save_turn(userid, text, content)
                return self.__decorate(content, tool_calls)

            name, args = call
            tool_calls.append(name)

            if tools.is_dangerous(name) and self._agent_confirm_dangerous:
                self._pending_confirm[userid] = {"tool": name, "args": args, "time": time.time()}
                return tools.get_danger_prompt(name, args)

            tool_result = tools.call(name, args, context)
            self.__audit(userid, name, args)
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": "工具 %s 的执行结果：\n%s\n\n请基于结果用中文简洁回复用户。"
                           % (name, tool_result),
            })

        return self.__too_many_rounds(tool_calls)

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
            if answer:
                return answer
        except Exception as err:
            log.error("【Agent】结果转述失败：%s" % str(err))
        return "操作已执行。"

    def __agent_prompt(self, tools=None, text_protocol=False):
        """
        构造 Agent 的系统提示词
        """
        from app.helper.agent_tools import AgentTools
        tools = tools or AgentTools()
        prompt = (
            "你是 NAStool 的智能管家，运行在用户的家庭服务器上，通过即时通讯工具接受指令。\n"
            "NAStool 是 PT 下载与媒体库管理工具，负责搜索资源、下载、整理入库、站点签到、刷流等。\n\n"
            "你可以调用下列工具来查询和操作系统，工具返回的是真实数据：\n"
            "%s\n\n"
            "工作原则：\n"
            "1. 用户询问系统状态（下载、站点、订阅、媒体库、磁盘空间等）时，必须调用工具获取真实数据，"
            "绝不能凭想象编造数字或列表。\n"
            "2. 用户要求执行操作时，调用对应工具。\n"
            "3. 不确定片名对应哪部作品时，先用 query_media_info 确认，再执行订阅等操作。\n"
            "4. 涉及删除、重启、升级、清空历史的操作，直接调用工具即可，"
            "系统会自动向用户请求确认，你不需要自己再问一遍。\n"
            "5. 工具返回的结果要提炼成简洁的中文回复，不要输出原始 JSON。\n"
            "6. 一次回复最多调用一个工具；拿到结果后若还需要其它信息，可以继续调用。\n"
            "7. 工具执行失败时如实说明原因，不要假装成功。\n"
            "8. 与系统无关的普通问题（闲聊、常识、技术问答）直接回答，不要调用工具。\n"
            "9. 用户问「你能做什么」或寻求帮助时，用简洁的中文列出你能查询和操作的范围"
            "（下载任务、PT站点、订阅、媒体库、刷流、系统运维等），并给两三个示例说法。\n"
        ) % tools.get_tools_prompt()
        if text_protocol:
            prompt += (
                "\n输出格式（必须严格遵守）：\n"
                "需要调用工具时，只输出一个 JSON，前后不要有任何其它文字：\n"
                '{"tool": "工具名", "args": {"参数名": "参数值"}}\n'
                "不需要调用工具时，直接输出给用户的自然语言回复即可。\n"
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
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
        if not (text.startswith("{") and text.endswith("}")):
            return None
        try:
            data = json.loads(text)
        except Exception:
            return None
        if not isinstance(data, dict) or "tool" not in data:
            return None
        args = data.get("args")
        return str(data.get("tool")), args if isinstance(args, dict) else {}

    def __is_confirm(self, text):
        """
        判断用户输入是否为确认
        """
        return (text or "").strip().lower() in self._CONFIRM_WORDS

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
            if not tool_name or tool_name.startswith("query_"):
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
