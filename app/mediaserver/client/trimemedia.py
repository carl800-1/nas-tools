import hashlib
import json
import random
import time
from enum import Enum
from urllib.parse import quote, urlsplit, urlunsplit

import requests

import log
from app.mediaserver.client._base import _IMediaClient
from app.utils import ExceptionUtils
from app.utils.types import MediaType, MediaServerType
from config import Config


def _norm_bool(value, default=True):
    """配置项里的布尔值可能是字符串，统一归一化为 bool"""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


class _TrimeCategory(Enum):
    """飞牛影视媒体库分类"""
    MOVIE = "Movie"
    TV = "TV"
    MIX = "Mix"
    OTHERS = "Others"

    @classmethod
    def _missing_(cls, value):
        return cls.OTHERS


class _TrimeType(Enum):
    """飞牛影视媒体条目类型"""
    MOVIE = "Movie"
    TV = "TV"
    SEASON = "Season"
    EPISODE = "Episode"
    VIDEO = "Video"
    DIRECTORY = "Directory"

    @classmethod
    def _missing_(cls, value):
        return cls.VIDEO


class _TrimeApi:
    """
    飞牛影视 API 客户端

    认证机制（三重）：
      1. API Key   —— 固定客户端标识，参与 authx 签名
      2. authx     —— 每个请求按 MD5(盐_路径_nonce_ts_bodyHash_apikey) 计算签名
      3. Token     —— 登录响应下发，通过 Authorization 头携带
    另外支持「访问码」：开启后需先 GET /c/<code> 获取会话凭证，否则无法访问登录页与业务接口。
    """

    # 飞牛影视客户端 API Key（MoviePilot 同款，用于 authx 签名）
    API_KEY = "16CCEB3D-AB42-077D-36A1-F355324E4237"
    # authx 签名盐值
    AUTH_SALT = "NDzZTVxnRKP8Z0jXg1VAMonaG8akvh"

    # 图片接口前缀（飞牛返回的相对图片路径必须补此前缀才能访问）
    IMG_API_PREFIX = "/api/v1/sys/img"
    # 分页单页最大条数
    PAGE_SIZE = 100
    # 目录递归最大页数保护
    MAX_PAGES = 500

    def __init__(self, host, access_code=None, timeout=10, ssl_verify=True):
        self._host = self._normalize_base_url(host)
        self._apikey = self.API_KEY
        self._access_code = access_code
        self._api_path = "/api/v1"
        self._token = None
        self._version = {}
        self._timeout = timeout
        self._ssl_verify = _norm_bool(ssl_verify)
        # 访问码校验下发的会话凭证、以及服务端 cookie 都靠同一会话保持，
        # 因此必须使用独立的 requests.Session，不能每次请求临时建连接。
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": Config().get_ua(),
            "Accept": "application/json",
        })
        # 最近一次失败原因（HTTP 状态码 / 非 JSON 响应 / 业务错误码 / 网络异常），
        # 供上层「测试连接」把真实原因回显到页面上
        self.last_error = None

    @staticmethod
    def _normalize_base_url(host):
        """标准化基础地址，保留 /v 后缀识别所需信息"""
        if not host:
            return ""
        host = str(host).strip()
        if not host.startswith("http"):
            host = "http://" + host
        parsed = urlsplit(host)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")

    @property
    def host(self):
        return self._host

    @property
    def token(self):
        return self._token

    @property
    def version(self):
        return self._version

    def verify_access_code(self):
        """
        校验访问码

        :return: 未配置访问码或校验通过返回 True
        """
        if not self._access_code:
            return True
        # 访问码校验地址位于设备根路径，不在 /v 下
        root = self._host[:-len("/v")] if self._host.endswith("/v") else self._host
        url = f"{root}/c/{quote(str(self._access_code), safe='')}"
        try:
            res = self._session.get(
                url,
                timeout=self._timeout,
                verify=self._ssl_verify,
                allow_redirects=True,
            )
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【飞牛影视】校验访问码异常：{str(e)}")
            return False
        if res is None:
            log.error(f"【飞牛影视】校验访问码失败，无法访问 {url}")
            return False
        if res.status_code == 404:
            log.error("【飞牛影视】访问码校验失败，请检查访问码是否正确")
            return False
        if not res.ok:
            log.error(f"【飞牛影视】访问码校验失败，状态码：{res.status_code}")
            return False
        return True

    def sys_version(self):
        """
        飞牛影视版本号

        :return: {'frontend': 影视版本, 'backend': 服务版本}，失败返回 None
        """
        res = self.request("/sys/version")
        if res and res.get("success") and res.get("data"):
            data = res["data"]
            self._version = {
                "frontend": data.get("version"),
                "backend": data.get("mediasrvVersion"),
            }
            return self._version
        return None

    def __get_authx(self, api_path, body):
        """
        计算 authx 请求签名

        :param api_path: 形如 /v/api/v1/item/list 的完整路径
        :param body: 请求体明文（GET 时为 query 串）
        """
        if not api_path.startswith("/v"):
            api_path = "/v" + api_path
        nonce = str(random.randint(100000, 999999))
        ts = str(int(time.time() * 1000))
        data_hash = hashlib.md5((body or "").encode()).hexdigest()
        sign = hashlib.md5(
            "_".join(
                [self.AUTH_SALT, api_path, nonce, ts, data_hash, self._apikey]
            ).encode()
        ).hexdigest()
        return f"nonce={nonce}&timestamp={ts}&sign={sign}"

    @staticmethod
    def __encode_types(obj):
        """JSON 编码时把枚举转为字符串值"""
        if isinstance(obj, Enum):
            return obj.value
        return str(obj)

    def request(self, api, method=None, params=None, data=None,
                base_path=None, suppress_log=False):
        """
        请求飞牛影视 API

        :param api: 接口路径，如 /item/list（自动补 /api/v1 前缀）
        :param method: 请求方法，None 时按有无 data 自动判定
        :param params: query 参数
        :param data: 请求体（dict）
        :param base_path: 接口路径前缀（如 /api/v2），默认 /api/v1
        :param suppress_log: 是否抑制错误日志
        :return: {'success': bool, 'code': int, 'msg': str, 'data': any} 或 None
        """
        if not self._host or not api:
            self.last_error = "未配置服务端地址"
            return None

        prefix = base_path if base_path is not None else self._api_path
        if not api.startswith("/"):
            api_path = f"{prefix}/{api}"
        else:
            api_path = prefix + api
        url = self._host + api_path

        if method is None:
            method = "get" if data is None else "post"
        method = method.upper()

        if method != "GET":
            # 与 MoviePilot 上游逐字节对齐：POST 无 body 时发空串而非 None，
            # 且此时仍带 Content-Type: application/json
            json_body = json.dumps(data, allow_nan=False, default=self.__encode_types) if data else ""
        else:
            json_body = None

        queries_unquoted = (
            "&".join([f"{k}={v}" for k, v in params.items()]) if params else None
        )

        headers = {
            "Accept": "application/json",
            "Referer": self._host,
            "authx": self.__get_authx(api_path, json_body or queries_unquoted),
        }
        if self._token:
            headers["Authorization"] = self._token
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            res = self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=json_body,
                timeout=self._timeout,
                verify=self._ssl_verify,
                allow_redirects=True,
            )
            if not res:
                self.last_error = f"请求 {url} 无响应"
                if not suppress_log:
                    log.error(f"【飞牛影视】请求接口 {url} 失败，无响应")
                return None
            if not res.ok:
                # 非 2xx：典型是地址写错、路径少了 /v、被反代/访问码拦截
                snippet = (res.text or "")[:200]
                self.last_error = (
                    f"HTTP {res.status_code}（{url}）"
                    f"Content-Type：{res.headers.get('Content-Type')}，响应：{snippet}")
                if not suppress_log:
                    log.error(
                        f"【飞牛影视】请求接口 {url} 返回 HTTP {res.status_code}，"
                        f"Content-Type：{res.headers.get('Content-Type')}，"
                        f"响应片段：{snippet!r}")
                return {"success": False, "code": res.status_code,
                        "msg": f"HTTP {res.status_code}", "data": None}
            try:
                resp = res.json()
            except Exception:
                # 返回的不是 JSON（访问码页 / 反代 HTML / 404 页面）——只报
                # 「Expecting value」根本无法定位，必须打出状态码与响应片段
                snippet = (res.text or "")[:200]
                self.last_error = (
                    f"返回非 JSON 响应（HTTP {res.status_code}，{url}），"
                    f"Content-Type：{res.headers.get('Content-Type')}，响应：{snippet}")
                if not suppress_log:
                    log.error(
                        f"【飞牛影视】请求接口 {url} 返回非 JSON 响应，"
                        f"HTTP {res.status_code}，Content-Type：{res.headers.get('Content-Type')}，"
                        f"响应片段：{snippet!r}")
                return None
            code = int(resp.get("code", -1))
            msg = resp.get("msg")
            if code:
                self.last_error = f"错误码 {code}：{msg}（{url}）"
                if not suppress_log:
                    log.error(f"【飞牛影视】请求接口 {url} 失败，错误码：{code} {msg}")
                return {"success": False, "code": code, "msg": msg, "data": None}
            self.last_error = None
            return {"success": True, "code": 0, "msg": msg, "data": resp.get("data")}
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}（{url}）"
            if not suppress_log:
                ExceptionUtils.exception_traceback(e)
                log.error(f"【飞牛影视】请求接口 {url} 异常：" + str(e))
            return None

    # ---------------------------------------------------------------- 认证

    def login(self, username, password):
        """
        登录飞牛影视

        新版服务端已废弃 v1 明文登录，优先使用 v2 协议（密码传输 SHA256 摘要），
        v2 接口不存在时回退旧版 v1 明文登录。

        :return: 成功返回 token，否则返回 None
        """
        if not username or not password:
            return None
        # 开启访问码后需先通过访问码校验，否则无法访问登录接口
        if not self.verify_access_code():
            return None

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        res = self.request(
            "/user/loginByPassword",
            data={
                "username": username,
                "password": password_hash,
                "app_name": "trimemedia-web",
            },
            base_path="/api/v2",
            suppress_log=True,
        )
        if res and res.get("success"):
            self._token = (res.get("data") or {}).get("token")
            return self._token
        if res:
            # v2 接口存在但登录失败（如账号密码错误），回退 v1 也无法成功
            log.error(f"【飞牛影视】登录失败，错误码：{res.get('code')} {res.get('msg')}")
            return None
        # v2 接口不可用（旧版服务端），回退 v1 明文登录
        res = self.request(
            "/login",
            data={
                "username": username,
                "password": password,
                "app_name": "trimemedia-web",
            },
        )
        if res and res.get("success"):
            self._token = (res.get("data") or {}).get("token")
        return self._token

    def logout(self):
        """退出登录"""
        if not self._token:
            return True
        res = self.request("/user/logout", method="post")
        if res and res.get("success"):
            self._token = None
            return True
        return False

    def user_info(self):
        """当前登录用户信息"""
        res = self.request("/user/info")
        if res and res.get("success"):
            return res.get("data")
        return None

    def user_list(self):
        """用户列表（仅管理员）"""
        res = self.request("/manager/user/list")
        if res and res.get("success"):
            return res.get("data") or []
        return None

    # ------------------------------------------------------------ 媒体库

    def mediadb_sum(self):
        """媒体数量统计"""
        res = self.request("/mediadb/sum")
        if res and res.get("success"):
            return res.get("data")
        return None

    def mediadb_list(self):
        """媒体库列表（普通用户）"""
        res = self.request("/mediadb/list")
        if res and res.get("success"):
            return res.get("data") or []
        return None

    def mdb_list(self):
        """媒体库列表（管理员，含 dir_list）"""
        res = self.request("/mdb/list")
        if res and res.get("success"):
            return res.get("data") or []
        return None

    def mdb_scanall(self):
        """扫描所有媒体库"""
        res = self.request("/mdb/scanall", method="post")
        return bool(res and res.get("success") and res.get("data"))

    def mdb_scan(self, guid):
        """扫描指定媒体库"""
        res = self.request(f"/mdb/scan/{guid}", method="post", data={})
        return bool(res and res.get("success") and res.get("data"))

    def task_running(self):
        """查询当前是否有正在运行的任务（刷新前必须调用，否则易误报 -14 Task duplicate）"""
        res = self.request("/task/running")
        return bool(res and res.get("success") and res.get("data"))

    # -------------------------------------------------------------- 条目

    def item_list(self, guid=None, types=None, exclude_grouped_video=True,
                  page=1, page_size=20, sort_by="create_time", sort="DESC"):
        """媒体列表"""
        if types is None:
            types = [_TrimeType.MOVIE, _TrimeType.TV,
                     _TrimeType.DIRECTORY, _TrimeType.VIDEO]
        post = {
            "tags": {"type": types} if types else {},
            "sort_type": sort,
            "sort_column": sort_by,
            "page": page,
            "page_size": page_size,
        }
        if guid:
            post["ancestor_guid"] = guid
        if exclude_grouped_video:
            post["exclude_grouped_video"] = 1
        res = self.request("/item/list", data=post)
        if res and res.get("success"):
            return (res.get("data") or {}).get("list", [])
        return None

    def item_count(self, guid, types=None):
        """指定媒体库的媒体条目总数"""
        if types is None:
            types = [_TrimeType.MOVIE, _TrimeType.TV]
        post = {
            "ancestor_guid": guid,
            "tags": {"type": types},
            "exclude_grouped_video": 1,
            "page": 1,
            "page_size": 1,
        }
        res = self.request("/item/list", data=post)
        if res and res.get("success"):
            data = res.get("data") or {}
            total = data.get("total")
            if total is None:
                total = data.get("total_count")
            try:
                return int(total) if total is not None else 0
            except (TypeError, ValueError):
                return 0
        return None

    def search_list(self, keywords):
        """搜索影片、演员"""
        res = self.request("/search/list", params={"q": keywords})
        if res and res.get("success"):
            return res.get("data") or []
        return None

    def item(self, guid):
        """媒体详情"""
        res = self.request(f"/item/{guid}")
        if res and res.get("success"):
            return res.get("data")
        return None

    def season_list(self, tv_guid):
        """季列表"""
        res = self.request(f"/season/list/{tv_guid}")
        if res and res.get("success"):
            return res.get("data") or []
        return None

    def episode_list(self, season_guid):
        """剧集列表"""
        res = self.request(f"/episode/list/{season_guid}")
        if res and res.get("success"):
            return res.get("data") or []
        return None

    def play_list(self):
        """继续观看列表"""
        res = self.request("/play/list")
        if res and res.get("success"):
            return res.get("data") or []
        return None

    @staticmethod
    def build_img_api_url(img_path):
        """把图片相对路径拼成可访问的 API 地址（幂等：已带前缀则原样返回）"""
        if not img_path:
            return None
        img_path = str(img_path)
        if img_path.startswith(_TrimeApi.IMG_API_PREFIX):
            return img_path
        if not img_path.startswith("/"):
            img_path = "/" + img_path
        return f"{_TrimeApi.IMG_API_PREFIX}{img_path}"

    def close(self):
        """关闭 API 会话"""
        if self._session:
            self._session.close()


class TrimeMediaClient(_IMediaClient):
    """
    飞牛影视媒体服务器客户端

    使用飞牛影视自研 API（非 Emby 协议）：
      - 认证：API Key + authx 签名 + 登录 token（支持访问码）
      - 媒体库：管理员走 /mdb/list，普通用户走 /mediadb/list
      - 条目：/item/list + 目录递归
    """

    client_id = "trimemedia"
    client_type = MediaServerType.TRIMEMEDIA
    client_name = MediaServerType.TRIMEMEDIA.value

    _client_config = {}
    _host = None
    _play_host = None
    _username = None
    _password = None
    _access_code = None
    _sync_libraries = []
    _scan_mode = None
    _ssl_verify = True

    _api = None
    _userinfo = None
    _libraries = {}
    # 最近一次失败原因 —— 「测试连接」失败时由 web 层直接回显到页面上
    last_error = None

    def __init__(self, config=None):
        if config:
            self._client_config = config
        else:
            self._client_config = Config().get_config('trimemedia')
        self.init_config()

    def init_config(self):
        if not self._client_config:
            self.last_error = (
                "未读到「飞牛影视」配置：请先在 设置 → 媒体服务器 → 飞牛影视 里"
                "填好地址/用户名/密码，并点「确定」保存")
            return
        conf = self._client_config

        def _norm(url):
            if not url:
                return None
            url = str(url).strip()
            if not url.startswith("http"):
                url = "http://" + url
            return url.rstrip("/") + "/"

        self._host = _norm(conf.get("host"))
        # 播放地址未填写时留空，由 get_play_url / get_libraries 回落到已含 /v 的 API 地址；
        # 若在这里回落成 _host，会因缺少 /v 前缀导致播放链接打不开。
        self._play_host = _norm(conf.get("play_host"))
        self._username = conf.get("username")
        self._password = conf.get("password")
        self._access_code = conf.get("access_code")
        self._scan_mode = conf.get("scan_mode")
        self._ssl_verify = conf.get("ssl_verify", True)

        sync_library = conf.get("sync_library")
        if isinstance(sync_library, list):
            self._sync_libraries = sync_library
        elif sync_library:
            self._sync_libraries = [sync_library]
        else:
            self._sync_libraries = []

        if self._host and self._username and self._password:
            self._connect()
        else:
            missing = []
            if not self._host:
                missing.append("地址")
            if not self._username:
                missing.append("用户名")
            if not self._password:
                missing.append("密码")
            self.last_error = "配置不完整，缺少：" + "、".join(missing)
            log.error(f"【{self.client_name}】配置不完整，缺少：{'、'.join(missing)}")

    def _connect(self):
        """连接并登录飞牛影视"""
        try:
            self._api = None
            self._userinfo = None
            self._libraries = {}

            api = self._resolve_api(self._host)
            if not api:
                # _resolve_api 已把最底层原因写进 self.last_error
                if not self.last_error:
                    self.last_error = f"无法连接服务端，请检查地址 {self._host}"
                log.error(f"【{self.client_name}】无法连接服务端（{self.last_error}）")
                return
            if not api.login(self._username, self._password):
                self.last_error = (
                    f"登录失败：{api.last_error or '用户名或密码不正确'}")
                log.error(f"【{self.client_name}】{self.last_error}")
                api.close()
                return
            self._api = api
            self._userinfo = api.user_info()
            if self._userinfo is None:
                self.last_error = (
                    f"获取用户信息失败：{api.last_error or 'token 未被服务端接受'}")
                log.error(f"【{self.client_name}】{self.last_error}")
                self._api = None
                return
            self.last_error = None
            log.info(f"【{self.client_name}】登录成功，用户：{self._username}")
            # 外网播放地址按同样规则探测（飞牛地址可能需要补 /v），
            # 探测失败时保留用户填写值，不阻断主流程
            if self._play_host:
                play_api = self._resolve_api(self._play_host)
                if play_api:
                    self._play_host = play_api.host
                    play_api.close()
                else:
                    log.warning(
                        f"【{self.client_name}】播放地址 {self._play_host} 无法连接，将按填写值使用")
            # 刷新媒体库列表缓存
            self.get_libraries()
        except Exception as e:
            self.last_error = f"连接异常：{type(e).__name__}: {e}"
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接异常：" + str(e))

    def _resolve_api(self, host):
        """
        解析可用的 API 地址

        飞牛影视地址可能带 /v 后缀也可能不带，逐个尝试并校验可达性。
        每个候选地址的探测结果都写日志，便于定位「地址填错 / 网络不通 / SSL 校验失败」。
        """
        if not host:
            return None
        candidates = []
        base = host.rstrip("/")
        if base.endswith("/v"):
            candidates.append(base)
        else:
            candidates.append(base + "/v")
            candidates.append(base)
        last_detail = None
        for cand in candidates:
            try:
                api = _TrimeApi(
                    host=cand,
                    access_code=self._access_code,
                    ssl_verify=self._ssl_verify,
                )
            except Exception as e:
                ExceptionUtils.exception_traceback(e)
                last_detail = f"初始化请求会话失败（{cand}）：{str(e)}"
                log.error(f"【{self.client_name}】{last_detail}")
                continue
            try:
                if api.verify_access_code() and api.sys_version():
                    log.info(f"【{self.client_name}】服务端地址探测成功：{cand}")
                    return api
                last_detail = api.last_error or f"地址 {cand} 的版本接口未通过校验"
                log.warning(f"【{self.client_name}】服务端地址探测失败，将继续尝试下一个候选：{cand}")
            except Exception as e:
                ExceptionUtils.exception_traceback(e)
                last_detail = f"地址探测异常（{cand}）：{type(e).__name__}: {e}"
                log.error(f"【{self.client_name}】{last_detail}")
            api.close()
        log.error(
            f"【{self.client_name}】所有候选地址均无法连接（已尝试：{'、'.join(candidates)}）；"
            f"请确认该地址从本容器内可达、端口正确、地址形如 http://ip:5666/v")
        self.last_error = last_detail or f"所有候选地址均无法连接：{'、'.join(candidates)}"
        return None

    @classmethod
    def match(cls, ctype):
        return True if ctype in [cls.client_id, cls.client_type, cls.client_name] else False

    def get_type(self):
        return self.client_type

    def get_host(self):
        return self._play_host or self._host or ""

    def __is_ready(self):
        return self._api is not None and self._api.token is not None

    # ---------------------------------------------------------- 基础信息

    def get_status(self):
        """测试连通性"""
        if not self._host:
            self.last_error = "未填写服务端地址（请在 设置 → 媒体服务器 → 飞牛影视 里填写并保存）"
            log.error(f"【{self.client_name}】{self.last_error}")
            return False
        if not self._username or not self._password:
            self.last_error = "用户名或密码未填写（请补全后保存再测试）"
            log.error(f"【{self.client_name}】{self.last_error}")
            return False
        try:
            if not self.__is_ready():
                # init_config/_connect 已把更具体的原因写进 last_error，优先用它
                self.last_error = self.last_error or (
                    f"未建立连接（地址 {self._host}），请检查地址、端口、访问码、用户名与密码")
                log.error(f"【{self.client_name}】{self.last_error}")
                return False
            info = self._api.user_info()
            if info is None:
                self.last_error = f"连接中断：{self._api.last_error or 'token 未被服务端接受'}"
                log.error(f"【{self.client_name}】{self.last_error}")
                return False
            self.last_error = None
            return True
        except Exception as e:
            self.last_error = f"测试连接出错：{type(e).__name__}: {e}"
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】{self.last_error}")
            return False

    def get_user_count(self):
        """用户数量（仅管理员可查，普通用户返回 1）"""
        if not self.__is_ready():
            return 0
        try:
            if self._userinfo and self._userinfo.get("is_admin") == 1:
                users = self._api.user_list()
                return len(users) if users is not None else 0
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
        return 1

    def get_medias_count(self):
        """电影、电视剧、音乐媒体数量"""
        if not self.__is_ready():
            return {}
        try:
            info = self._api.mediadb_sum()
            if not info:
                return {}
            return {
                "MovieCount": int(info.get("movie") or 0),
                "SeriesCount": int(info.get("tv") or 0),
                "SongCount": int(info.get("music") or info.get("audio") or 0),
            }
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体数量出错：" + str(e))
            return {}

    # -------------------------------------------------------------- 查询

    def _build_item(self, item):
        """把飞牛条目统一成 nas-tools 内部结构"""
        if not isinstance(item, dict):
            return {}
        item_type = item.get("type")
        if item_type and not isinstance(item_type, str):
            item_type = getattr(item_type, "value", str(item_type))

        # 年份：电视剧取 air_date，其余取 release_date
        if item_type == _TrimeType.TV.value and item.get("air_date"):
            year = str(item.get("air_date"))[:4]
        elif item.get("release_date"):
            year = str(item.get("release_date"))[:4]
        else:
            year = ""

        trim_id = item.get("trim_id") or ""
        tmdbid = None
        if isinstance(trim_id, str) and len(trim_id) > 2 and trim_id[:2] in ("tt", "tm"):
            # 飞牛给 tmdbid 加了前缀区分 tv/movie
            try:
                tmdbid = int(trim_id[2:])
            except ValueError:
                tmdbid = None

        return {
            "id": item.get("guid"),
            "library": item.get("ancestor_guid") or "",
            "type": MediaType.TV.value if item_type == _TrimeType.TV.value
                    else MediaType.MOVIE.value,
            "title": item.get("title") or "",
            "originalTitle": item.get("original_title") or "",
            "year": year,
            "tmdbid": tmdbid,
            "imdbid": item.get("imdb_id"),
            "path": item.get("path") or "",
            "json": str(item),
        }

    def get_movies(self, title, year=None):
        """根据标题和年份，检查电影是否存在"""
        if not self.__is_ready():
            return []
        try:
            ret_movies = []
            for item in self._api.search_list(keywords=title) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != _TrimeType.MOVIE.value:
                    continue
                names = [item.get("title"), item.get("original_title")]
                if title not in names:
                    continue
                rel = item.get("release_date")
                if year and not (rel and str(rel)[:4] == str(year)):
                    continue
                ret_movies.append({
                    "title": item.get("title"),
                    "year": str(rel)[:4] if rel else "",
                })
            return ret_movies
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】搜索电影出错：" + str(e))
            return []

    def __get_series_id_by_name(self, name, year):
        """按标题和年份查找电视剧 guid"""
        for item in self._api.search_list(keywords=name) or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != _TrimeType.TV.value:
                continue
            names = [item.get("title"), item.get("original_title")]
            if name not in names:
                continue
            air = item.get("air_date")
            if year and not (air and str(air)[:4] == str(year)):
                continue
            return item.get("guid")
        return None

    def get_tv_episodes(self, item_id=None, title=None, year=None,
                        tmdbid=None, season=None):
        """根据标题、年份、季查询电视剧所有集信息"""
        if not self.__is_ready():
            return []
        try:
            cached_id = item_id
            if not item_id:
                if not title:
                    return []
                item_id = self.__get_series_id_by_name(title, year)
                if not item_id:
                    return []

            info = self._api.item(item_id)
            if not info and cached_id and title:
                # 媒体删除后重新入库会导致缓存 ID 失效，回退到标题搜索
                log.warn(f"【{self.client_name}】缓存的剧集 ID {cached_id} 已失效，改按标题搜索：{title}")
                item_id = self.__get_series_id_by_name(title, year)
                if not item_id:
                    return []
                info = self._api.item(item_id)
            if not info:
                return []

            seasons = self._api.season_list(item_id)
            if not seasons:
                return []
            if season is not None:
                matched = [s for s in seasons
                           if isinstance(s, dict) and s.get("season_number") == season]
                if not matched:
                    return []
                seasons = matched

            exists_episodes = []
            for s in seasons:
                if not isinstance(s, dict):
                    continue
                for ep in self._api.episode_list(s.get("guid")) or []:
                    if not isinstance(ep, dict):
                        continue
                    exists_episodes.append({
                        "season_num": ep.get("season_number") or 0,
                        "episode_num": ep.get("episode_number") or 0,
                    })
            return exists_episodes
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取剧集信息出错：" + str(e))
            return []

    def get_no_exists_episodes(self, meta_info, season, total_num):
        """查询缺少哪几集"""
        if not self.__is_ready():
            return []
        if not season:
            season = 1
        try:
            exists = self.get_tv_episodes(
                title=meta_info.title,
                year=meta_info.year,
                tmdbid=meta_info.tmdb_id,
                season=season,
            )
            if not isinstance(exists, list):
                return []
            exists_nums = [ep.get("episode_num") for ep in exists]
            all_nums = list(range(1, int(total_num) + 1))
            return sorted(set(all_nums).difference(set(exists_nums)))
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】查询缺失集数出错：" + str(e))
            return []

    def get_iteminfo(self, itemid):
        """根据 ItemId 查询项目详情"""
        if not self.__is_ready() or not itemid:
            return {}
        try:
            info = self._api.item(itemid)
            return self._build_item(info) if info else {}
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取项目详情出错：" + str(e))
            return {}

    # -------------------------------------------------------------- 图片

    def __abs_image_url(self, path):
        """
        把飞牛返回的图片路径拼成可访问的绝对地址

        ⚠️ 飞牛返回的是相对路径（如 media/img/xxx.jpg），必须补上
        `/api/v1/sys/img` 前缀才能取到图，这与 MoviePilot 的
        `__build_img_api_url` 同口径；若本身就是绝对地址则原样返回。
        """
        if not path:
            return ""
        if isinstance(path, (list, tuple)):
            path = path[0] if path else ""
            if not path:
                return ""
        path = str(path)
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self._api.host}{_TrimeApi.build_img_api_url(path)}"

    def get_remote_image_by_id(self, item_id, image_type):
        """根据 ItemId 查询远程图片地址"""
        if not self.__is_ready():
            return ""
        try:
            info = self._api.item(item_id)
            if not info:
                return ""
            if image_type == "Backdrop":
                path = info.get("backdrops") or info.get("posters")
            else:
                path = info.get("posters") or info.get("poster")
            if path:
                return self.__abs_image_url(path)
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取远程图片出错：" + str(e))
        return ""

    def get_local_image_by_id(self, item_id, remote=True, inner=False, video_info=None):
        """根据 ItemId 查询本地图片地址"""
        if not self.__is_ready():
            return ""
        try:
            info = video_info if video_info is not None else self._api.item(item_id)
            if not info:
                return ""
            path = info.get("poster") or info.get("posters")
            if not path:
                return ""
            image_url = self.__abs_image_url(path)
            if not image_url:
                return ""
            if remote:
                return image_url
            if inner:
                return self.get_nt_image_url(image_url)
            return image_url
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取本地图片出错：" + str(e))
            return ""

    # -------------------------------------------------------------- 库

    def get_libraries(self):
        """获取媒体服务器所有媒体库列表"""
        if not self.__is_ready():
            return []
        try:
            is_admin = bool(self._userinfo and self._userinfo.get("is_admin") == 1)
            raw_list = self._api.mdb_list() if is_admin else self._api.mediadb_list()
            if raw_list is None:
                log.warn(f"【{self.client_name}】媒体库列表获取失败")
                return []
            libraries = []
            self._libraries = {}
            for lib in raw_list:
                if not isinstance(lib, dict):
                    continue
                guid = lib.get("guid")
                if not guid:
                    continue
                category = lib.get("category")
                if category == _TrimeCategory.MOVIE.value:
                    library_type = MediaType.MOVIE.value
                elif category == _TrimeCategory.TV.value:
                    library_type = MediaType.TV.value
                elif str(category).lower() in ("music", "audio"):
                    # MediaType 无 MUSIC 档，音乐库按 UNKNOWN 处理（与其它客户端一致）
                    library_type = MediaType.UNKNOWN.value
                elif category == _TrimeCategory.OTHERS.value:
                    continue
                else:
                    library_type = MediaType.UNKNOWN.value

                self._libraries[guid] = {
                    "guid": guid,
                    "name": lib.get("name") or lib.get("title") or guid,
                    "category": category,
                    "dir_list": lib.get("dir_list") or [],
                }
                # 库封面：飞牛 /mdb/list 会带 posters 数组（相对路径）
                posters = lib.get("posters") or []
                if not isinstance(posters, list):
                    posters = [posters]
                libraries.append({
                    "id": guid,
                    "name": lib.get("name") or lib.get("title") or guid,
                    "type": library_type,
                    "path": self._libraries[guid]["dir_list"],
                    "image": self.__abs_image_url(posters[0]) if posters else "",
                    # 无封面时前端会退到 custom-plex-library-img，该组件会对
                    # img-src-list 做 split(",")，故这里必须给字符串而不是列表
                    "image_list": ",".join(self.__abs_image_url(p) for p in posters[:4] if p),
                    "link": (self._play_host or self._api.host).rstrip("/") + f"/library/{guid}",
                })
                log.info(f"【{self.client_name}】发现媒体库：{libraries[-1]['name']} ({library_type})")
            return libraries
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体库列表出错：" + str(e))
            return []

    def __is_library_blocked(self, library_guid):
        """判断媒体库是否被同步范围排除"""
        if self._sync_libraries and "all" not in self._sync_libraries:
            return library_guid not in self._sync_libraries
        return False

    def get_items(self, parent):
        """
        获取媒体库中的所有媒体（生成器）

        :param parent: 媒体库 GUID
        """
        if not self.__is_ready() or not parent:
            return
        try:
            for item in self.__iter_items(parent):
                yield item
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体列表出错：" + str(e))

    def __iter_items(self, parent, depth=0):
        """
        递归遍历媒体库条目

        :param parent: 媒体库或目录 GUID
        :param depth: 递归深度保护
        """
        if depth > 10:
            log.warn(f"【{self.client_name}】目录递归过深，终止：{parent}")
            return
        page = 1
        while page <= _TrimeApi.MAX_PAGES:
            items = self._api.item_list(
                guid=parent,
                page=page,
                page_size=_TrimeApi.PAGE_SIZE,
                types=[_TrimeType.MOVIE, _TrimeType.TV, _TrimeType.DIRECTORY],
            )
            if items is None:
                break
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == _TrimeType.DIRECTORY.value:
                    for sub in self.__iter_items(item.get("guid"), depth + 1):
                        yield sub
                elif item_type in (_TrimeType.MOVIE.value, _TrimeType.TV.value):
                    built = self._build_item(item)
                    if built.get("id"):
                        yield built
            if len(items) < _TrimeApi.PAGE_SIZE:
                break
            page += 1

    # -------------------------------------------------------------- 刷新

    def refresh_root_library(self):
        """通知飞牛刷新整个媒体库（仅管理员）"""
        if not self.__is_ready():
            return False
        if not self._userinfo or self._userinfo.get("is_admin") != 1:
            log.error(f"【{self.client_name}】仅支持管理员账号刷新媒体库")
            return False
        try:
            # 必须先查询运行中任务，否则易误报 -14 Task duplicate
            self._api.task_running()
            log.info(f"【{self.client_name}】刷新所有媒体库")
            return self._api.mdb_scanall()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】刷新媒体库出错：" + str(e))
            return False

    def refresh_library_by_items(self, items):
        """按路径刷新所在的媒体库（仅管理员）"""
        if not items:
            return False
        if not self.__is_ready():
            return False
        if not self._userinfo or self._userinfo.get("is_admin") != 1:
            log.error(f"【{self.client_name}】仅支持管理员账号刷新媒体库")
            return False
        try:
            guids = set()
            for item in items:
                target = getattr(item, "target_path", None)
                matched = self.__match_library_by_path(str(target)) if target else None
                if not matched:
                    # 有匹配失败的，直接刷新整个库
                    return self.refresh_root_library()
                guids.add(matched)
            self._api.task_running()
            for guid in guids:
                lib = self._libraries.get(guid) or {}
                log.info(f"【{self.client_name}】刷新媒体库：{lib.get('name', guid)}")
                if not self._api.mdb_scan(guid):
                    return self.refresh_root_library()
            return True
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】按路径刷新媒体库出错：" + str(e))
            return False

    def __match_library_by_path(self, path):
        """按路径反查所属媒体库 guid"""
        if not path:
            return None
        for guid, lib in self._libraries.items():
            for d in lib.get("dir_list") or []:
                if d and path.startswith(str(d).rstrip("/")):
                    return guid
        return None

    # -------------------------------------------------------- 播放/会话

    @staticmethod
    def _to_trime_type(item_type):
        """
        把条目类型归一化为飞牛原始类型值

        入参可能是：
          - 飞牛原始值（'Movie' / 'TV' / 'Episode' / 'Season' / 'Video' / 'Directory'）
          - _build_item 产物（MediaType 值：'电影' / '电视剧'）
          - 枚举对象 _TrimeType.XXX
        """
        if item_type is None:
            return None
        if isinstance(item_type, _TrimeType):
            return item_type.value
        val = str(getattr(item_type, "value", item_type))
        # MediaType 值 -> 飞牛类型
        if val == MediaType.MOVIE.value:
            return _TrimeType.MOVIE.value
        if val == MediaType.TV.value:
            return _TrimeType.TV.value
        return val

    def get_play_url(self, item_id, item_info=None):
        """获取播放地址"""
        if not self.__is_ready() or not item_id:
            return ""
        try:
            info = item_info if item_info is not None else self._api.item(item_id)
            if not info:
                return ""
            item_type = self._to_trime_type(info.get("type"))
            host = (self._play_host or self._api.host).rstrip("/")
            if item_type == _TrimeType.EPISODE.value:
                return f"{host}/tv/episode/{item_id}"
            elif item_type == _TrimeType.SEASON.value:
                return f"{host}/tv/season/{item_id}"
            elif item_type == _TrimeType.MOVIE.value:
                return f"{host}/movie/{item_id}"
            elif item_type == _TrimeType.TV.value:
                return f"{host}/tv/{item_id}"
            return f"{host}/other/{item_id}"
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取播放地址出错：" + str(e))
            return ""

    # ---------------------------------------------------------- 首页数据

    @staticmethod
    def __item_sort_key(item):
        """「最近添加」的排序键：飞牛不同版本字段名不一，逐个兜底"""
        if not isinstance(item, dict):
            return 0.0
        for key in ("create_time", "create_time_ms", "add_time", "ctime", "update_time"):
            val = item.get(key)
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def __build_resume_name(item, item_type):
        """拼「继续观看」卡片的标题"""
        title = item.get("title") or ""
        if item_type == _TrimeType.EPISODE.value:
            tv_title = item.get("tv_title") or item.get("parent_title") or title
            season = item.get("season_number")
            episode = item.get("episode_number")
            if season is not None and episode is not None:
                return f"{tv_title} 第{season}季第{episode}集"
            return tv_title
        return title

    @staticmethod
    def __calc_percent(item):
        """
        已播放百分比

        飞牛给的是 ts（已播放秒）与 duration（片长秒），而首页进度条要的是百分比。
        """
        ts = item.get("ts")
        duration = item.get("duration")
        try:
            if ts is None or not duration:
                return None
            percent = round(float(ts) * 100.0 / float(duration), 1)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return max(0.0, min(100.0, percent))

    def get_resume(self, num=12):
        """
        获得继续观看（首页「正在观看」模块）

        走 /play/list（播放历史），条目自带 ts / duration / watched。
        """
        if not self.__is_ready():
            return []
        try:
            items = self._api.play_list()
            if not items:
                return []
            ret_resume = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if len(ret_resume) >= num:
                    break
                # 已看完（watched=1）的不算「继续观看」
                if item.get("watched") == 1:
                    continue
                item_type = self._to_trime_type(item.get("type"))
                if item_type not in (_TrimeType.MOVIE.value,
                                     _TrimeType.EPISODE.value,
                                     _TrimeType.TV.value,
                                     _TrimeType.VIDEO.value):
                    continue
                guid = item.get("guid")
                if not guid:
                    continue
                ret_resume.append({
                    "id": guid,
                    "name": self.__build_resume_name(item, item_type),
                    # 首页按中文类型值决定角标配色（电影=绿、其余=蓝）
                    "type": MediaType.TV.value
                            if item_type in (_TrimeType.EPISODE.value, _TrimeType.TV.value)
                            else MediaType.MOVIE.value,
                    "image": self.get_local_image_by_id(guid, remote=False,
                                                        inner=True, video_info=item),
                    "link": self.get_play_url(guid, item_info=item),
                    "percent": self.__calc_percent(item),
                })
            return ret_resume
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取继续观看出错：" + str(e))
            return []

    def get_latest(self, num=20):
        """
        获得最近添加（首页「最新入库」模块）

        先按全库（不带 ancestor_guid）取 create_time 倒序；若服务端不支持全库
        列表会返回空，此时退化为「逐媒体库取 + 合并重排」。
        """
        if not self.__is_ready():
            return []
        try:
            types = [_TrimeType.MOVIE, _TrimeType.TV]
            items = self._api.item_list(types=types,
                                        page=1,
                                        page_size=num,
                                        sort_by="create_time",
                                        sort="DESC")
            if not items:
                merged = []
                for library in self.get_libraries():
                    part = self._api.item_list(guid=library.get("id"),
                                               types=types,
                                               page=1,
                                               page_size=num,
                                               sort_by="create_time",
                                               sort="DESC")
                    if part:
                        merged.extend(part)
                items = sorted(merged, key=self.__item_sort_key, reverse=True)

            ret_latest = []
            seen = set()
            for item in items:
                if not isinstance(item, dict):
                    continue
                if len(ret_latest) >= num:
                    break
                item_type = self._to_trime_type(item.get("type"))
                if item_type not in (_TrimeType.MOVIE.value, _TrimeType.TV.value):
                    continue
                guid = item.get("guid")
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                ret_latest.append({
                    "id": guid,
                    "name": item.get("title") or "",
                    "type": MediaType.TV.value if item_type == _TrimeType.TV.value
                            else MediaType.MOVIE.value,
                    "image": self.get_local_image_by_id(guid, remote=False,
                                                        inner=True, video_info=item),
                    "link": self.get_play_url(guid, item_info=item),
                })
            return ret_latest
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取最近添加出错：" + str(e))
            return []

    def get_episode_image_by_id(self, item_id, season_id, episode_id):
        """
        根据 itemid、season_id、episode_id 查询某一集的图片地址

        飞牛的 episode_id 一般是「集条目 GUID」，与 Emby 的「集号」语义不同，
        这里按「集 -> 季 -> 剧」逐个尝试，取到即返回。
        """
        if not self.__is_ready():
            return None
        for guid in (episode_id, season_id, item_id):
            if not guid:
                continue
            try:
                info = self._api.item(guid)
            except Exception as e:
                ExceptionUtils.exception_traceback(e)
                continue
            if not info:
                continue
            image = self.get_local_image_by_id(guid, remote=False,
                                               inner=True, video_info=info)
            if image:
                return image
        return None

    def get_playing_sessions(self):
        """获取正在播放的会话（飞牛暂不支持）"""
        return []

    def get_activity_log(self, num):
        """获取活动记录（飞牛暂不支持）"""
        return []

    def get_webhook_message(self, message):
        """解析 Webhook 报文（飞牛暂不支持）"""
        return {}
