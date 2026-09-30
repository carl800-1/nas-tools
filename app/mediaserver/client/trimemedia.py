import hashlib
import json
import random
import time
from enum import Enum
from urllib.parse import quote, urlsplit, urlunsplit

import log
from app.mediaserver.client._base import _IMediaClient
from app.utils import RequestUtils, ExceptionUtils
from app.utils.types import MediaType, MediaServerType
from config import Config


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
        self._ssl_verify = ssl_verify

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
            res = RequestUtils(
                timeout=self._timeout,
                verify=self._ssl_verify,
                session=True,
            ).get_res(url)
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

        if method != "GET" and data:
            json_body = json.dumps(data, ensure_ascii=False, default=self.__encode_types)
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
            req = RequestUtils(
                headers=headers,
                timeout=self._timeout,
                verify=self._ssl_verify,
                session=True,
            )
            if method == "GET":
                res = req.get_res(url, params=params)
            else:
                res = req.post_res(url, data=json_body, params=params)
            if not res:
                if not suppress_log:
                    log.error(f"【飞牛影视】请求接口 {url} 失败，无响应")
                return None
            resp = res.json()
            code = int(resp.get("code", -1))
            msg = resp.get("msg")
            if code:
                if not suppress_log:
                    log.error(f"【飞牛影视】请求接口 {url} 失败，错误码：{code} {msg}")
                return {"success": False, "code": code, "msg": msg, "data": None}
            return {"success": True, "code": 0, "msg": msg, "data": resp.get("data")}
        except Exception as e:
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
        """把图片相对路径拼成可访问的 API 地址"""
        if not img_path:
            return None
        if not str(img_path).startswith("/"):
            img_path = "/" + str(img_path)
        return f"/api/v1/sys/img{img_path}"

    def close(self):
        """关闭 API（本实现使用短连接，无需释放）"""
        pass


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

    def __init__(self, config=None):
        if config:
            self._client_config = config
        else:
            self._client_config = Config().get_config('trimemedia')
        self.init_config()

    def init_config(self):
        if not self._client_config:
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
        self._play_host = _norm(conf.get("play_host")) or self._host
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

    def _connect(self):
        """连接并登录飞牛影视"""
        try:
            self._api = None
            self._userinfo = None
            self._libraries = {}

            api = self._resolve_api(self._host)
            if not api:
                log.error(f"【{self.client_name}】无法连接服务端，请检查地址 {self._host}")
                return
            if not api.login(self._username, self._password):
                log.error(f"【{self.client_name}】登录失败，请检查用户名和密码")
                api.close()
                return
            self._api = api
            self._userinfo = api.user_info()
            if self._userinfo is None:
                log.error(f"【{self.client_name}】获取用户信息失败")
                self._api = None
                return
            log.info(f"【{self.client_name}】登录成功，用户：{self._username}")
            # 刷新媒体库列表缓存
            self.get_libraries()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接异常：" + str(e))

    def _resolve_api(self, host):
        """
        解析可用的 API 地址

        飞牛影视地址可能带 /v 后缀也可能不带，逐个尝试并校验可达性。
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
        for cand in candidates:
            api = _TrimeApi(
                host=cand,
                access_code=self._access_code,
                ssl_verify=self._ssl_verify,
            )
            if api.verify_access_code() and api.sys_version():
                return api
            api.close()
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
            return False
        try:
            if not self.__is_ready():
                return False
            return self._api.user_info() is not None
        except Exception:
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
                return f"{self._api.host}{path}"
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
            image_url = f"{self._api.host}{path}"
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
                libraries.append({
                    "id": guid,
                    "name": lib.get("name") or lib.get("title") or guid,
                    "type": library_type,
                    "path": self._libraries[guid]["dir_list"],
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

    def get_playing_sessions(self):
        """获取正在播放的会话（飞牛暂不支持）"""
        return []

    def get_activity_log(self, num):
        """获取活动记录（飞牛暂不支持）"""
        return []

    def get_webhook_message(self, message):
        """解析 Webhook 报文（飞牛暂不支持）"""
        return {}
