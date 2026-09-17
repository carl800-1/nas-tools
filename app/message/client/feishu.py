import json
import os
import re
import threading
import time
from threading import Lock
from urllib.parse import urlparse

import requests

import log
from app.helper import ThreadHelper
from app.message.client._base import _IMessageClient
from app.utils import ExceptionUtils
from config import Config

# 长连接依赖 lark-oapi，未安装时仅影响消息接收，不影响消息发送
try:
    import lark_oapi as lark
except ImportError:  # pragma: no cover
    lark = None

lock = Lock()

# 飞书（中国）与国际版 Lark 的开放平台域名
FEISHU_DOMAIN = "https://open.feishu.cn"
LARK_DOMAIN = "https://open.larksuite.com"

# tenant_access_token 提前过期时间（秒）
_TOKEN_REFRESH_AHEAD = 600
# 消息卡片标题颜色
_CARD_TEMPLATE = "blue"
# 列表类消息最多展示的条数
_MAX_LIST_ITEMS = 10
# 图片 image_key 缓存条数上限
_IMAGE_CACHE_SIZE = 50
# 飞书图片消息支持的格式
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
# 无需处理但需要兜底注册的事件：未注册的事件会抛异常并让飞书收到500而重推
_NOOP_EVENTS = (
    "im.message.reaction.created_v1",
    "im.message.reaction.deleted_v1",
    "im.message.recalled_v1",
    "im.message.message_read_v1",
    "im.chat.updated_v1",
    "im.chat.disbanded_v1",
    "im.chat.member.bot.added_v1",
    "im.chat.member.bot.deleted_v1",
    "im.chat.member.user.added_v1",
    "im.chat.member.user.deleted_v1",
    "im.chat.member.user.withdrawn_v1",
    "im.chat.access_event.bot_p2p_chat_entered_v1",
)

# 当前进程内的长连接实例（lark-oapi 的 ws 客户端共用模块级事件循环，同一进程只能存在一个连接）
_WS_CLIENT = None


class Feishu(_IMessageClient):
    schema = "feishu"

    _client_config = {}
    _interactive = False
    _domain = FEISHU_DOMAIN
    _app_id = None
    _app_secret = None
    _chat_id = None
    _user_ids = []
    _admin_ids = []
    _api_key = None
    _ds_url = None
    _enabled = True
    _ws_client = None
    _ws_thread = None
    _tenant_token = None
    _token_expire_at = 0
    _image_cache = {}
    _image_cache_lock = None

    def __init__(self, config):
        self._config = Config()
        self._client_config = config or {}
        self._interactive = self._client_config.get("interactive")
        self._enabled = True
        self._user_ids = []
        self._admin_ids = []
        self._image_cache = {}
        self._image_cache_lock = Lock()
        self.init_config()

    def init_config(self):
        if not self._client_config:
            return
        self._app_id = self._client_config.get("app_id")
        self._app_secret = self._client_config.get("app_secret")
        self._chat_id = self._client_config.get("chat_id")
        self._domain = LARK_DOMAIN if self._client_config.get("domain") == "lark" else FEISHU_DOMAIN
        self._admin_ids = self.__split_ids(self._client_config.get("admin_ids"))
        self._user_ids = list(self._admin_ids)
        self._user_ids.extend(self.__split_ids(self._client_config.get("user_ids")))
        if not self._app_id or not self._app_secret:
            return
        _web_port = self._config.get_config("app").get("web_port")
        self._api_key = self._config.get_config("security").get("api_key")
        self._ds_url = "http://127.0.0.1:%s/feishu?apikey=%s" % (_web_port, self._api_key)
        if self._interactive:
            self.__start_message_service()

    @classmethod
    def match(cls, ctype):
        return True if ctype == cls.schema else False

    def get_admin(self):
        """
        获取允许使用管理命令的open_id列表
        """
        return self._admin_ids

    def get_users(self):
        """
        获取允许使用飞书机器人的open_id列表
        """
        return self._user_ids

    def stop_service(self):
        """
        停止消息接收服务
        """
        self._enabled = False
        ws_client = self._ws_client
        self._ws_client = None
        if not ws_client:
            return
        global _WS_CLIENT
        with lock:
            if _WS_CLIENT is ws_client:
                _WS_CLIENT = None
        # lark-oapi 的 ws 客户端未提供公开的停止方法，只能停掉它内部使用的模块级事件循环，
        # 阻塞在 start() 的线程会随之返回（见 __run_ws_client）
        self.__shutdown_ws_client(ws_client)
        log.info("【Feishu】消息接收服务已停止")

    def send_msg(self, title, text="", image="", url="", user_id=""):
        """
        发送飞书消息（消息卡片）
        :param title: 消息标题
        :param text: 消息内容
        :param image: 消息图片地址，会自动上传至飞书换取image_key
        :param url: 点击消息跳转的URL
        :param user_id: 消息发送对象的open_id，为空则发到配置的chat_id或用户列表
        """
        if not title and not text:
            return False, "标题和内容不能同时为空"
        if not self._app_id or not self._app_secret:
            return False, "参数未配置"
        try:
            # 拼装消息内容
            titles = str(title).split('\n')
            if len(titles) > 1:
                title = titles[0]
                if not text:
                    text = "\n".join(titles[1:])
                else:
                    text = "%s\n%s" % ("\n".join(titles[1:]), text)
            elements = []
            if text:
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": text.replace("\n\n", "\n")
                    }
                })
            # 消息图片
            if image:
                image_key = self.__upload_image(image)
                if image_key:
                    elements.append({
                        "tag": "img",
                        "img_key": image_key,
                        "alt": {"tag": "plain_text", "content": title}
                    })
                else:
                    elements.append({
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "[查看图片](%s)" % image
                        }
                    })
            # 跳转链接
            if url and str(url).startswith("http"):
                elements.append({
                    "tag": "action",
                    "actions": [{
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看详情"},
                        "type": "primary",
                        "url": str(url)
                    }]
                })
            if not elements:
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": title}
                })
            return self.__send_card(title=title, elements=elements, user_id=user_id)
        except Exception as msg_e:
            ExceptionUtils.exception_traceback(msg_e)
            return False, str(msg_e)

    def send_list_msg(self, medias: list, user_id="", title="", url="", **kwargs):
        """
        发送列表类消息（回复序号即可选择，见 web/backend/search_torrents.py）
        """
        if not medias:
            return False, "参数有误"
        if not self._app_id or not self._app_secret:
            return False, "参数未配置"
        try:
            if not title:
                title = "共找到%s条相关信息" % len(medias)
            elements = []
            if medias:
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "请回复序号（1-%s）选择" % min(len(medias), _MAX_LIST_ITEMS)
                    }
                })
                index = 1
                for media in medias[:_MAX_LIST_ITEMS]:
                    if index > 1:
                        elements.append({"tag": "hr"})
                    lines = ["**%s. %s**" % (index, media.get_title_string())]
                    if media.get_type_string():
                        lines.append(media.get_type_string())
                    if media.get_vote_string():
                        lines.append(media.get_vote_string())
                    if media.get_detail_url():
                        lines.append(media.get_detail_url())
                    elements.append({
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": "\n".join(lines)}
                    })
                    index += 1
            # 列表消息附带跳转按钮，方便直接回到NAStool界面操作
            if url and str(url).startswith("http"):
                elements.append({
                    "tag": "action",
                    "actions": [{
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开NAStool"},
                        "type": "default",
                        "url": str(url)
                    }]
                })
            return self.__send_card(title=title, elements=elements, user_id=user_id)
        except Exception as msg_e:
            ExceptionUtils.exception_traceback(msg_e)
            return False, str(msg_e)

    def __build_event_handler(self):
        """
        构建事件派发器：注册消息接收事件，并兜底注册同权限范围内的其他事件

        SDK 对未注册的事件会抛出异常，长连接会因此给飞书返回500而触发反复重推，
        因此这里把消息之外的事件一并注册为空实现（SDK 版本不支持的事件自动跳过）。
        """
        builder = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self.__on_message_receive)
        for event_type in _NOOP_EVENTS:
            register = getattr(builder, "register_p2_" + event_type.replace(".", "_"), None)
            if register:
                register(lambda data: None)
        return builder.build()

    def __start_message_service(self):
        """
        启动长连接消息接收服务（独立后台线程）
        """
        if lark is None:
            log.error("【Feishu】未安装 lark-oapi，无法接收飞书消息，请先安装依赖：pip install lark-oapi")
            return
        try:
            event_handler = self.__build_event_handler()
            # 长连接使用与服务端接口相同的域名，Lark国际版需显式指定
            ws_client = lark.ws.Client(self._app_id,
                                       self._app_secret,
                                       event_handler=event_handler,
                                       log_level=lark.LogLevel.INFO,
                                       domain=self._domain)
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            log.error("【Feishu】消息接收服务初始化失败：%s" % str(err))
            return
        global _WS_CLIENT
        with lock:
            old_client = _WS_CLIENT
            _WS_CLIENT = ws_client
            self._ws_client = ws_client
        if old_client and old_client is not ws_client:
            self.__shutdown_ws_client(old_client)
        # 长连接是常驻线程，不占用 ThreadHelper 的线程池
        self._ws_thread = threading.Thread(target=self.__run_ws_client,
                                           args=(ws_client,),
                                           name="feishu-ws",
                                           daemon=True)
        self._ws_thread.start()

    def __run_ws_client(self, ws_client):
        """
        阻塞运行长连接（lark-oapi 的 start() 会一直阻塞到连接结束）
        """
        try:
            log.info("【Feishu】消息接收服务启动")
            ws_client.start()
        except Exception as err:
            if self._enabled:
                ExceptionUtils.exception_traceback(err)
                log.error("【Feishu】消息接收服务异常退出：%s" % str(err))
            else:
                log.info("【Feishu】消息接收服务已停止")

    @staticmethod
    def __shutdown_ws_client(ws_client):
        """
        释放遗留的长连接实例
        """
        try:
            from lark_oapi.ws.client import loop as ws_loop
        except Exception:
            return
        if not ws_loop.is_running():
            return

        def _graceful_stop():
            try:
                ws_loop.create_task(ws_client._disconnect())
            except Exception:
                pass
            ws_loop.call_later(0.5, ws_loop.stop)

        try:
            ws_loop.call_soon_threadsafe(_graceful_stop)
        except Exception:
            pass

    def __on_message_receive(self, data):
        """
        飞书消息事件回调

        注意：飞书要求事件在3秒内处理完成，否则会触发超时重推（同一条命令被执行两次）。
        lark-oapi 会在事件循环中同步调用本回调，并把它执行的毫秒数作为 biz_rt 上报给飞书，
        因此这里只做解析和转发，耗时的搜索/下载交给后台线程。
        """
        try:
            user_id, text, chat_id = self.__parse_message_event(data)
            if text and self._ds_url:
                ThreadHelper().start_thread(self.__forward_message, (user_id, text, chat_id))
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            log.error("【Feishu】消息事件处理失败：%s" % str(err))

    @staticmethod
    def __parse_message_event(data):
        """
        解析 im.message.receive_v1 事件，返回用户的open_id、文本内容与群组chat_id
        """
        event = getattr(data, "event", None)
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if not message or not sender:
            return None, "", None
        user_id = getattr(getattr(sender, "sender_id", None), "open_id", None)
        chat_id = getattr(message, "chat_id", None)
        message_type = getattr(message, "message_type", None)
        if message_type != "text":
            log.info("【Feishu】暂不支持的消息类型：%s" % message_type)
            return user_id, "", chat_id
        content = getattr(message, "content", "") or ""
        try:
            text = json.loads(content).get("text", "")
        except Exception:
            text = content
        # 群聊中@机器人会带上@_user_x占位符，需剔除
        text = re.sub(r"@_user_\d+", "", str(text)).strip()
        return user_id, text, chat_id

    def __forward_message(self, user_id, text, chat_id=None):
        """
        转发消息到本地接口处理
        """
        # 记录open_id与chat_id，便于配置用户白名单与群组Chat ID
        log.info("【Feishu】收到消息：open_id=%s，chat_id=%s" % (user_id, chat_id))
        try:
            res = requests.post(self._ds_url,
                                json={"user_id": user_id, "text": text},
                                timeout=10)
            log.debug("【Feishu】message: %s processed, response is: %s" % (text, res.text))
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            log.error("【Feishu】消息转发失败：%s" % str(err))

    def __send_card(self, title, elements: list, user_id=""):
        """
        发送消息卡片，user_id为空时发到配置的chat_id或用户列表
        """
        targets = self.__get_targets(user_id)
        if not targets:
            return False, "未配置消息接收对象"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": _CARD_TEMPLATE,
                "title": {"tag": "plain_text", "content": title or ""}
            },
            "elements": elements
        }
        content = json.dumps(card, ensure_ascii=False)
        for receive_id_type, receive_id in targets:
            flag, msg = self.__send_message(receive_id_type=receive_id_type,
                                            receive_id=receive_id,
                                            msg_type="interactive",
                                            content=content)
            if not flag:
                log.error("【Feishu】消息发送失败：%s" % msg)
                return flag, msg
        return True, ""

    def __get_targets(self, user_id=""):
        """
        计算消息接收对象，返回[(receive_id_type, receive_id)]
        """
        if user_id:
            return [("open_id", str(user_id))]
        if self._chat_id:
            return [("chat_id", str(self._chat_id))]
        return [("open_id", str(uid)) for uid in self._user_ids]

    def __send_message(self, receive_id_type, receive_id, msg_type, content):
        """
        调用飞书发送消息接口
        """
        token, err = self.__get_tenant_token()
        if not token:
            return False, err
        req_url = "%s/open-apis/im/v1/messages?receive_id_type=%s" % (self._domain, receive_id_type)
        try:
            res = requests.post(req_url,
                                headers={
                                    "Authorization": "Bearer %s" % token,
                                    "Content-Type": "application/json; charset=utf-8"
                                },
                                json={
                                    "receive_id": receive_id,
                                    "msg_type": msg_type,
                                    "content": content
                                },
                                proxies=Config().get_proxies(),
                                timeout=20)
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return False, str(err)
        if res is None:
            return False, "未获取到返回信息"
        try:
            ret = res.json()
        except Exception:
            return False, "返回内容解析失败：%s" % res.text
        if ret.get("code") == 0:
            return True, ""
        return False, "错误码：%s，错误信息：%s" % (ret.get("code"), ret.get("msg"))

    def __get_tenant_token(self):
        """
        获取并缓存 tenant_access_token
        """
        if self._tenant_token and time.time() < self._token_expire_at:
            return self._tenant_token, ""
        with lock:
            if self._tenant_token and time.time() < self._token_expire_at:
                return self._tenant_token, ""
            req_url = "%s/open-apis/auth/v3/tenant_access_token/internal" % self._domain
            try:
                res = requests.post(req_url,
                                    json={
                                        "app_id": self._app_id,
                                        "app_secret": self._app_secret
                                    },
                                    proxies=Config().get_proxies(),
                                    timeout=20)
                ret = res.json() if res is not None else {}
            except Exception as err:
                ExceptionUtils.exception_traceback(err)
                return None, str(err)
            if ret.get("code") != 0:
                return None, "获取token失败，错误码：%s，错误信息：%s" % (ret.get("code"), ret.get("msg"))
            self._tenant_token = ret.get("tenant_access_token")
            self._token_expire_at = time.time() + int(ret.get("expire") or 7200) - _TOKEN_REFRESH_AHEAD
            return self._tenant_token, ""

    def __upload_image(self, image):
        """
        下载图片并上传至飞书换取image_key，失败返回None（降级为文字链接）
        """
        if not image or not str(image).startswith("http"):
            return None
        with self._image_cache_lock:
            image_key = self._image_cache.get(image)
        if image_key:
            return image_key
        token, _ = self.__get_tenant_token()
        if not token:
            return None
        try:
            res = requests.get(image, verify=False, proxies=Config().get_proxies(), timeout=20)
            if res is None or res.status_code != 200 or not res.content:
                return None
            ret = requests.post("%s/open-apis/im/v1/images" % self._domain,
                                headers={"Authorization": "Bearer %s" % token},
                                data={"image_type": "message"},
                                files={"image": (self.__image_name(image), res.content)},
                                proxies=Config().get_proxies(),
                                timeout=30)
            result = ret.json() if ret is not None else {}
            if result.get("code") != 0:
                log.warn("【Feishu】图片上传失败，错误码：%s，错误信息：%s" % (
                    result.get("code"), result.get("msg")))
                return None
            image_key = result.get("data", {}).get("image_key")
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return None
        if not image_key:
            return None
        with self._image_cache_lock:
            if len(self._image_cache) >= _IMAGE_CACHE_SIZE:
                self._image_cache.clear()
            self._image_cache[image] = image_key
        return image_key

    @staticmethod
    def __image_name(image):
        """
        按原始地址推断图片文件名，便于飞书识别格式
        """
        name = os.path.basename(urlparse(str(image)).path)
        if name and name.lower().endswith(_IMAGE_SUFFIXES):
            return name
        return "image.jpg"

    @staticmethod
    def __split_ids(ids):
        """
        拆分逗号分隔的open_id列表
        """
        if not ids:
            return []
        return [str(one).strip() for one in str(ids).split(",") if str(one).strip()]
