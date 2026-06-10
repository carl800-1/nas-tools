import base64
import hashlib
import json
import os
import re
import uuid
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import log
from app.mediaserver.client._base import _IMediaClient
from app.utils import RequestUtils, SystemUtils, ExceptionUtils, IpUtils
from app.utils.types import MediaType, MediaServerType
from config import Config

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


class _UgreenCrypto:
    """绿联接口请求加解密工具"""

    def __init__(self, public_key, token=None, client_id=None,
                 client_version="76363", ug_agent="PC/WEB", language="zh-CN"):
        self.public_key_pem = self.normalize_public_key(public_key)
        self.public_key = serialization.load_pem_public_key(
            self.public_key_pem.encode("utf-8")
        )
        self.token = token
        self.client_id = client_id
        self.client_version = client_version
        self.ug_agent = ug_agent
        self.language = language

    @staticmethod
    def normalize_public_key(public_key):
        key = (public_key or "").strip().strip('"').replace("\\n", "\n")
        if "BEGIN" in key:
            return key if key.endswith("\n") else f"{key}\n"
        return (
            "-----BEGIN RSA PUBLIC KEY-----\n"
            f"{key}\n"
            "-----END RSA PUBLIC KEY-----\n"
        )

    @staticmethod
    def generate_aes_key():
        return uuid.uuid4().hex

    def rsa_encrypt_long(self, plaintext):
        if not plaintext:
            return ""
        key_size = self.public_key.key_size // 8
        max_chunk = key_size - 11
        encrypted_chunks = []
        raw = plaintext.encode("utf-8")
        for start in range(0, len(raw), max_chunk):
            chunk = raw[start:start + max_chunk]
            encrypted_chunks.append(
                self.public_key.encrypt(chunk, padding.PKCS1v15())
            )
        return base64.b64encode(b"".join(encrypted_chunks)).decode("utf-8")

    @staticmethod
    def aes_gcm_encrypt(plaintext, aes_key):
        iv = os.urandom(12)
        cipher = AESGCM(aes_key.encode("utf-8"))
        encrypted = cipher.encrypt(iv, plaintext.encode("utf-8"), None)
        return base64.b64encode(iv + encrypted).decode("utf-8")

    @staticmethod
    def aes_gcm_decrypt(payload_b64, aes_key):
        raw = base64.b64decode(payload_b64)
        iv = raw[:12]
        encrypted = raw[12:]
        cipher = AESGCM(aes_key.encode("utf-8"))
        plain = cipher.decrypt(iv, encrypted, None)
        return plain.decode("utf-8")

    @staticmethod
    def build_security_key(token):
        return hashlib.md5(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_body(data):
        if isinstance(data, str):
            return data
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode("utf-8")
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    def encrypt_body(self, data, aes_key):
        plain = self._normalize_body(data)
        return {
            "encrypt_req_body": self.aes_gcm_encrypt(plain, aes_key),
            "req_body_sha256": hashlib.sha256(plain.encode("utf-8")).hexdigest(),
        }

    def build_headers(self, aes_key, token=None):
        token_value = token if token is not None else self.token
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Client-Id": self.client_id or "",
            "Client-Version": self.client_version,
            "UG-Agent": self.ug_agent,
            "X-Specify-Language": self.language,
        }
        if token_value:
            headers["X-Ugreen-Security-Key"] = self.build_security_key(token_value)
            headers["X-Ugreen-Security-Code"] = self.rsa_encrypt_long(aes_key)
            headers["X-Ugreen-Token"] = self.rsa_encrypt_long(token_value)
        return headers

    @staticmethod
    def _flatten_query(prefix, value):
        pairs = []
        if isinstance(value, dict):
            for key, item in value.items():
                next_prefix = f"{prefix}[{key}]" if prefix else str(key)
                pairs.extend(_UgreenCrypto._flatten_query(next_prefix, item))
            return pairs
        if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                next_prefix = f"{prefix}[]"
                pairs.extend(_UgreenCrypto._flatten_query(next_prefix, item))
            return pairs
        if isinstance(value, bool):
            pairs.append((prefix, "true" if value else "false"))
            return pairs
        if value is None:
            pairs.append((prefix, ""))
            return pairs
        pairs.append((prefix, str(value)))
        return pairs

    @classmethod
    def encode_query(cls, params):
        if not params:
            return ""
        pairs = []
        for key, value in params.items():
            pairs.extend(cls._flatten_query(str(key), value))
        return urlencode(pairs, doseq=False, quote_via=quote, safe="")

    def build_encrypted_request(self, url, method="GET", params=None, data=None):
        parsed = urlsplit(url)
        clean_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
        url_query_plain = parsed.query
        input_query_plain = self.encode_query(params)
        plain_query = "&".join(filter(None, [url_query_plain, input_query_plain]))
        aes_key = self.generate_aes_key()
        encrypted_query = self.aes_gcm_encrypt(plain_query, aes_key)
        req_json = None
        if data is not None:
            req_json = self.encrypt_body(data, aes_key)
        headers = self.build_headers(aes_key)
        if req_json is not None:
            headers["Content-Type"] = "application/json"
        return clean_url, headers, {"encrypt_query": encrypted_query}, req_json, aes_key

    def decrypt_response(self, response_json, aes_key):
        if not isinstance(response_json, dict):
            return response_json
        encrypted = response_json.get("encrypt_resp_body")
        if not encrypted:
            return response_json
        plain = self.aes_gcm_decrypt(str(encrypted), aes_key)
        try:
            return json.loads(plain)
        except json.JSONDecodeError:
            return plain


class _UgreenApi:
    """绿联影视 API 客户端（加密通道）"""

    def __init__(self, host, client_version="76363", language="zh-CN",
                 ug_agent="PC/WEB", timeout=20, verify_ssl=True):
        self._host = self._normalize_base_url(host)
        self._session = None
        self._token = None
        self._static_token = None
        self._is_ugk = False
        self._public_key = None
        self._crypto = None
        self._username = None
        self._client_id = f"{uuid.uuid4()}-WEB"
        self._client_version = client_version
        self._language = language
        self._ug_agent = ug_agent
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._init_session()

    def _init_session(self):
        """初始化 HTTP 会话"""
        try:
            import requests as req_lib
            self._session = req_lib.Session()
        except ImportError:
            self._session = None

    def close(self):
        """关闭 HTTP 会话"""
        if self._session:
            self._session.close()
            self._session = None

    @staticmethod
    def _normalize_base_url(host):
        if not host:
            return ""
        host = host.strip().rstrip("/")
        if not host.startswith("http"):
            host = "http://" + host
        parsed = urlsplit(host)
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")

    @property
    def host(self):
        return self._host

    @property
    def token(self):
        return self._token

    @property
    def static_token(self):
        return self._static_token

    @property
    def is_ugk(self):
        return self._is_ugk

    @staticmethod
    def _decode_public_key(raw):
        if not raw:
            return None
        value = str(raw).strip()
        if not value:
            return None
        if "BEGIN" in value:
            return value
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception:
            return None

    @staticmethod
    def _extract_rsa_token(resp_json, headers):
        token = headers.get("x-rsa-token") or headers.get("X-Rsa-Token")
        if token:
            return token
        token = resp_json.get("xRsaToken") or resp_json.get("x-rsa-token")
        if token:
            return token
        data = resp_json.get("data") if isinstance(resp_json, dict) else None
        if isinstance(data, dict):
            return data.get("xRsaToken") or data.get("x-rsa-token")
        return None

    def _common_headers(self):
        return {
            "Accept": "application/json, text/plain, */*",
            "Client-Id": self._client_id,
            "Client-Version": self._client_version,
            "UG-Agent": self._ug_agent,
            "X-Specify-Language": self._language,
        }

    def _request_json(self, url, method="GET", headers=None, params=None, json_data=None):
        try:
            import requests as req_lib
            session = self._session or req_lib
            method = method.upper()
            if method == "POST":
                resp = session.post(
                    url=url, headers=headers, params=params, json=json_data,
                    timeout=self._timeout, verify=self._verify_ssl,
                )
            else:
                resp = session.get(
                    url=url, headers=headers, params=params,
                    timeout=self._timeout, verify=self._verify_ssl,
                )
            return resp.json()
        except Exception as err:
            log.error(f"请求绿联接口失败：{url} {err}")
            return None

    @staticmethod
    def _build_result(payload):
        if not isinstance(payload, dict):
            return {"code": -1, "msg": "响应格式错误", "data": None, "raw": None}
        code = payload.get("code")
        try:
            code = int(code)
        except Exception:
            code = -1
        return {
            "code": code,
            "msg": str(payload.get("msg") or ""),
            "data": payload.get("data"),
            "raw": dict(payload),
        }

    def login(self, username, password, keepalive=True):
        """登录绿联账号并初始化加密上下文"""
        if not username or not password:
            return None
        headers = self._common_headers()
        try:
            import requests as req_lib
            session = self._session or req_lib
            check_resp = session.post(
                url=f"{self._host}/ugreen/v1/verify/check",
                headers=headers, json={"username": username},
                timeout=self._timeout, verify=self._verify_ssl,
            )
            check_json = check_resp.json()
        except Exception as err:
            log.error(f"绿联获取登录公钥失败：{err}")
            return None
        check_result = self._build_result(check_json)
        if not check_result["code"] == 200:
            log.error(f"绿联获取登录公钥失败：{check_result['msg']}")
            return None
        rsa_token = self._extract_rsa_token(check_json, check_resp.headers)
        login_public_key = self._decode_public_key(rsa_token)
        if not login_public_key:
            log.error("绿联获取登录公钥失败：公钥为空")
            return None
        encrypted_password = _UgreenCrypto(public_key=login_public_key).rsa_encrypt_long(password)
        login_json = self._request_json(
            url=f"{self._host}/ugreen/v1/verify/login",
            method="POST", headers=headers,
            json_data={
                "username": username,
                "password": encrypted_password,
                "keepalive": keepalive,
                "otp": True,
                "is_simple": True,
            },
        )
        if not login_json:
            return None
        login_result = self._build_result(login_json)
        if not login_result["code"] == 200 or not isinstance(login_result["data"], dict):
            log.error(f"绿联登录失败：{login_result['msg']}")
            return None
        token = str(login_result["data"].get("token") or "").strip()
        public_key = self._decode_public_key(str(login_result["data"].get("public_key") or ""))
        if not token or not public_key:
            log.error("绿联登录失败：未返回 token/public_key")
            return None
        self._token = token
        static_token = str(login_result["data"].get("static_token") or "").strip()
        self._static_token = static_token or self._token
        self._is_ugk = bool(login_result["data"].get("is_ugk"))
        self._public_key = public_key
        self._crypto = _UgreenCrypto(
            public_key=self._public_key, token=self._token,
            client_id=self._client_id, client_version=self._client_version,
            ug_agent=self._ug_agent, language=self._language,
        )
        self._username = username
        return self._token

    def logout(self):
        if not self._token or not self._crypto:
            return
        try:
            url, headers, params, _, _ = self._crypto.build_encrypted_request(
                url=f"{self._host}/ugreen/v1/verify/logout",
            )
            import requests as req_lib
            session = self._session or req_lib
            session.get(url, headers=headers, params=params,
                        timeout=self._timeout, verify=self._verify_ssl)
        except Exception:
            pass
        self._token = None
        self._static_token = None
        self._is_ugk = False
        self._public_key = None
        self._crypto = None
        self._username = None

    def request(self, path, method="GET", params=None, data=None):
        """统一请求入口"""
        if not self._crypto:
            return {"code": -1, "msg": "未登录", "data": None}
        api_path = path.strip("/")
        url, headers, req_params, req_json, aes_key = self._crypto.build_encrypted_request(
            url=f"{self._host}/ugreen/{api_path}",
            method=method.upper(), params=params or {}, data=data,
        )
        payload = self._request_json(
            url=url, method=method, headers=headers,
            params=req_params, json_data=req_json,
        )
        if payload is None:
            return {"code": -1, "msg": "接口请求失败", "data": None}
        decrypted = self._crypto.decrypt_response(payload, aes_key)
        return self._build_result(decrypted)

    def current_user(self):
        result = self.request("v1/user/current/user")
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def video_all(self, classification, page=1, page_size=20):
        result = self.request(
            "v1/video/all",
            params={
                "page": page, "pageSize": page_size,
                "classification": classification,
                "sort_type": 2, "order_type": 2,
                "release_date_begin": -9999999999,
                "release_date_end": -9999999999,
                "identify_status": 0, "watch_status": -1,
                "ug_style_id": 0, "ug_country_id": 0, "clarity": -1,
            },
        )
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def recently_played(self, page=1, page_size=20):
        result = self.request(
            "v1/video/recently_played",
            params={"page": page, "page_size": page_size},
        )
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def recently_updated(self, page=1, page_size=20):
        result = self.request(
            "v1/video/recently_updated",
            params={"page": page, "page_size": page_size},
        )
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def video_info(self, item_id):
        result = self.request(
            "v1/video/info",
            params={"ug_video_info_id": item_id},
        )
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def media_lib_get_all(self):
        result = self.request("v1/video/media_lib/get_all",
                              params={"mediaLib_get_all_req_type": 2})
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def media_list(self):
        """获取媒体库列表（v1/video/homepage/media_list）"""
        result = self.request("v1/video/homepage/media_list")
        if result["code"] == 200 and isinstance(result["data"], dict):
            items = result["data"].get("media_lib_info_list")
            return items if isinstance(items, list) else []
        return []

    def poster_wall_get_folder(self, path=None, page=1, page_size=100, sort_type=1, order_type=1):
        """获取海报墙文件夹与条目（v1/video/poster_wall/media_lib/get_folder）"""
        params = {
            "page": page, "page_size": page_size,
            "sort_type": sort_type, "order_type": order_type,
        }
        if path:
            params["path"] = path
        result = self.request("v1/video/poster_wall/media_lib/get_folder", params=params)
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def get_tv(self, item_id, folder_path="ALL"):
        """获取剧集详情（含季/集信息）"""
        result = self.request(
            "v2/video/details/getTV",
            params={"ug_video_info_id": item_id, "folder_path": folder_path},
        )
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def media_lib_scan(self, media_lib_set_id, scan_type=2, op_type=2):
        result = self.request(
            "v1/video/media_lib/scan",
            params={
                "op_type": op_type,
                "media_lib_set_id": media_lib_set_id,
                "media_lib_scan_type": scan_type,
            },
        )
        return result["code"] == 200

    def get_play_url(self, item_id, folder_path=None):
        params = {"ug_video_info_id": item_id}
        if folder_path:
            params["folder_path"] = folder_path
        result = self.request("v1/video/play_url/get", params=params)
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def get_image_stream_url(self, source_url, size=1):
        if not self._static_token and not self._token:
            return None
        auth_token = self._static_token or self._token
        params = {"app_name": "web", "name": source_url, "size": size}
        if self._is_ugk:
            params["ugk"] = auth_token
        else:
            params["token"] = auth_token
        base = self._host
        query = urlencode(params)
        return f"{base}/ugreen/v1/video/getImaStream?{query}"


class UgreenClient(_IMediaClient):
    """
    绿联影视媒体服务器客户端
    使用绿联原生加密 API（非 Emby API）
    """

    client_id = "ugreen"
    client_type = MediaServerType.UGREEN
    client_name = MediaServerType.UGREEN.value

    _client_config = {}
    _host = None
    _play_host = None
    _username = None
    _password = None
    _api = None
    _userinfo = None

    def __init__(self, config=None):
        if config:
            self._client_config = config
        else:
            self._client_config = Config().get_config('ugreen')
        self.init_config()

    def init_config(self):
        if self._client_config:
            self._host = self._client_config.get('host')
            if self._host:
                if not self._host.startswith('http'):
                    self._host = "http://" + self._host
                if not self._host.endswith('/'):
                    self._host = self._host + "/"
            self._play_host = self._client_config.get('play_host')
            if not self._play_host:
                self._play_host = self._host
            else:
                if not self._play_host.startswith('http'):
                    self._play_host = "http://" + self._play_host
                if not self._play_host.endswith('/'):
                    self._play_host = self._play_host + "/"
            self._username = self._client_config.get('username')
            self._password = self._client_config.get('password')
            if self._host and self._username and self._password:
                self._connect()

    def _connect(self):
        """使用绿联加密 API 登录"""
        if not HAS_CRYPTO:
            log.error(f"【{self.client_name}】缺少 cryptography 库，请安装：pip install cryptography")
            return
        try:
            # 关闭旧会话
            if self._api:
                try:
                    self._api.close()
                except Exception:
                    pass
                self._api = None
            api = _UgreenApi(host=self._host)
            token = api.login(self._username, self._password)
            if token:
                self._api = api
                self._userinfo = api.current_user()
                log.info(f"【{self.client_name}】登录成功，用户：{self._username}")
            else:
                log.error(f"【{self.client_name}】登录失败，请检查用户名和密码")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】登录异常：" + str(e))

    @classmethod
    def match(cls, ctype):
        return True if ctype in [cls.client_id, cls.client_type, cls.client_name] else False

    def get_type(self):
        return self.client_type

    def get_status(self):
        """
        测试连通性
        """
        if not self._host or not self._api:
            return False
        try:
            user = self._api.current_user()
            return user is not None
        except Exception:
            return False

    def get_user_id(self):
        """
        获取用户ID
        """
        if not self._userinfo:
            return None
        return self._userinfo.get("id") or self._userinfo.get("userId")

    def get_server_info(self):
        """
        获取服务器信息
        """
        if not self._api:
            return None
        return {"host": self._host, "username": self._username}

    def get_user_count(self):
        """
        获取用户数量（绿联单用户，返回1）
        """
        if not self._api:
            return 0
        return 1

    def get_medias_count(self):
        """
        获取电影、电视剧媒体数量
        """
        if not self._api:
            return {}
        try:
            movie_data = self._api.video_all(classification=-102, page=1, page_size=1) or {}
            tv_data = self._api.video_all(classification=-103, page=1, page_size=1) or {}
            return {
                "MovieCount": int(movie_data.get("total_num") or 0),
                "SeriesCount": int(tv_data.get("total_num") or 0),
                "SongCount": 0,
            }
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体数量出错：" + str(e))
            return {}

    def get_movies(self, title, year=None):
        """
        根据标题和年份，检查电影是否存在
        """
        if not self._api:
            return []
        try:
            result = self._api.request(
                "v1/video/all",
                params={
                    "page": 1, "pageSize": 10,
                    "classification": -102,
                    "sort_type": 2, "order_type": 2,
                    "release_date_begin": -9999999999,
                    "release_date_end": -9999999999,
                    "identify_status": 0, "watch_status": -1,
                    "ug_style_id": 0, "ug_country_id": 0, "clarity": -1,
                    "search": title,
                },
            )
            if result["code"] != 200 or not isinstance(result["data"], dict):
                return []
            items = result["data"].get("video_arr") or []
            ret_movies = []
            for item in items:
                video_info = item.get("video_info") if isinstance(item, dict) else {}
                name = video_info.get("name") or video_info.get("title") or ""
                if name == title or (year and str(video_info.get("release_year")) == str(year)):
                    ret_movies.append({
                        "title": name,
                        "year": str(video_info.get("release_year") or ""),
                    })
            return ret_movies
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】搜索电影出错：" + str(e))
            return []

    def get_tv_episodes(self, item_id=None, title=None, year=None, tmdbid=None, season=None):
        """
        根据标题、年份、季查询电视剧所有集信息
        """
        if not self._api:
            return []
        try:
            if not item_id and title:
                # 先搜索电视剧
                result = self._api.request(
                    "v1/video/all",
                    params={
                        "page": 1, "pageSize": 10,
                        "classification": -103,
                        "sort_type": 2, "order_type": 2,
                        "release_date_begin": -9999999999,
                        "release_date_end": -9999999999,
                        "identify_status": 0, "watch_status": -1,
                        "ug_style_id": 0, "ug_country_id": 0, "clarity": -1,
                        "search": title,
                    },
                )
                if result["code"] == 200 and isinstance(result["data"], dict):
                    items = result["data"].get("video_arr") or []
                    for item in items:
                        vi = item.get("video_info") if isinstance(item.get("video_info"), dict) else {}
                        vi_name = vi.get("name", "")
                        if vi_name == title and (not year or str(vi.get("release_year")) == str(year)):
                            item_id = vi.get("ug_video_info_id") or vi.get("id")
                            break
            if not item_id:
                return []
            # 使用 get_tv API 获取剧集详情（含季/集）
            tv_data = self._api.get_tv(item_id)
            if not tv_data:
                # 降级使用 video_info
                info = self._api.video_info(item_id)
                if not info:
                    return []
                episodes = info.get("episodes") or info.get("episode_arr") or []
            else:
                episodes = tv_data.get("episodes") or tv_data.get("episode_arr") or []
            exists_episodes = []
            for ep in episodes:
                exists_episodes.append({
                    "season_num": ep.get("season_num") or ep.get("parent_index_number") or 0,
                    "episode_num": ep.get("episode_num") or ep.get("index_number") or 0,
                })
            return exists_episodes
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取剧集信息出错：" + str(e))
            return []

    def get_no_exists_episodes(self, meta_info, season, total_num):
        """
        查询缺少哪几集
        """
        if not self._api:
            return []
        if not season:
            season = 1
        try:
            exists_episodes = self.get_tv_episodes(
                title=meta_info.title,
                year=meta_info.year,
                tmdbid=meta_info.tmdb_id,
                season=season,
            )
            if not isinstance(exists_episodes, list):
                return []
            exists_episodes = [ep.get("episode_num") for ep in exists_episodes]
            total_episodes = [ep for ep in range(1, total_num + 1)]
            return list(set(total_episodes).difference(set(exists_episodes)))
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】查询缺失集数出错：" + str(e))
            return []

    def get_episode_image_by_id(self, item_id, season_id, episode_id):
        """
        根据itemid、season_id、episode_id查询图片地址
        """
        if not self._api:
            return ""
        try:
            info = self._api.video_info(item_id)
            if not info:
                return ""
            episodes = info.get("episodes") or info.get("episode_arr") or []
            for ep in episodes:
                if ep.get("episode_num") == episode_id or ep.get("index_number") == episode_id:
                    img_url = self.get_remote_image_by_id(ep.get("ug_video_info_id") or ep.get("id"), "Primary")
                    return img_url or ""
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取剧集图片出错：" + str(e))
        return ""

    def get_remote_image_by_id(self, item_id, image_type):
        """
        根据ItemId查询远程图片地址
        """
        if not self._api:
            return ""
        try:
            info = self._api.video_info(item_id)
            if not info:
                return ""
            if image_type == "Backdrop":
                path = info.get("backdrop_path") or info.get("backdrop")
            else:
                path = info.get("poster_path") or info.get("poster")
            if path:
                return self._api.get_image_stream_url(path)
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取远程图片出错：" + str(e))
        return ""

    def get_local_image_by_id(self, item_id, remote=True, inner=False):
        """
        根据ItemId查询本地图片地址
        """
        if not self._api:
            return ""
        try:
            info = self._api.video_info(item_id)
            if not info:
                return ""
            path = info.get("poster_path") or info.get("poster")
            if not path:
                return ""
            if remote:
                return self._api.get_image_stream_url(path)
            else:
                image_url = f"{self._host}ugreen/v1/video/getImageStream?name={path}&size=1"
                if inner:
                    return self.get_nt_image_url(image_url)
                return image_url
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取本地图片出错：" + str(e))
            return ""

    def refresh_root_library(self):
        """
        刷新整个媒体库（遍历所有库逐个扫描）
        """
        if not self._api:
            return
        try:
            libs = self._api.media_list()
            if not libs:
                return
            for lib in libs:
                lib_id = lib.get("media_lib_set_id") or lib.get("id")
                lib_name = lib.get("media_name") or lib.get("name", lib_id)
                if lib_id:
                    self._api.media_lib_scan(lib_id)
                    log.info(f"【{self.client_name}】媒体库刷新请求已发送：{lib_name}")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】刷新媒体库出错：" + str(e))

    def refresh_library_by_items(self, items):
        """
        按类型、名称、年份来刷新媒体库
        """
        if not items:
            return
        self.refresh_root_library()

    def get_libraries(self):
        """
        获取媒体服务器所有媒体库列表
        """
        if not self._api:
            return []
        try:
            libs = self._api.media_list()
            if not libs:
                return []
            libraries = []
            for lib in libs:
                lib_id = str(lib.get("media_lib_set_id") or lib.get("id", ""))
                lib_name = lib.get("media_name") or lib.get("name", "")
                lib_type = lib.get("media_lib_type", "")
                if lib_type == "movies":
                    library_type = MediaType.MOVIE.value
                elif lib_type == "tv":
                    library_type = MediaType.TV.value
                else:
                    library_type = lib_type
                libraries.append({
                    "id": lib_id,
                    "name": lib_name,
                    "type": library_type,
                    "path": lib.get("path", ""),
                })
            return libraries
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体库列表出错：" + str(e))
            return []

    def get_iteminfo(self, itemid):
        """
        根据ItemId查询项目详情
        """
        if not self._api:
            return None
        try:
            return self._api.video_info(itemid)
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取项目详情出错：" + str(e))
            return None

    def get_items(self, parent):
        """
        获取媒体库中的所有媒体
        :param parent: 媒体库ID
        """
        if not self._api:
            return []
        try:
            # 先获取媒体库列表，找到目标库的路径
            libs = self._api.media_list()
            target_lib = None
            for lib in libs:
                if str(lib.get("media_lib_set_id") or lib.get("id")) == str(parent):
                    target_lib = lib
                    break
            if not target_lib:
                return []
            lib_path = target_lib.get("path", "")
            if not lib_path:
                return []
            # 使用 poster_wall_get_folder 遍历目录树获取所有视频
            ret_items = []
            page = 1
            while True:
                data = self._api.poster_wall_get_folder(
                    path=lib_path, page=page, page_size=100,
                    sort_type=1, order_type=1,
                )
                if not data:
                    break
                for video in data.get("video_arr") or []:
                    if not isinstance(video, dict):
                        continue
                    vi = video.get("video_info") if isinstance(video.get("video_info"), dict) else video
                    video_type = vi.get("type", 0)
                    if video_type not in [1, 2]:
                        continue
                    item_id = vi.get("ug_video_info_id") or vi.get("id")
                    if not item_id:
                        continue
                    name = vi.get("name") or vi.get("title") or ""
                    item_type = MediaType.MOVIE.value if video_type == 1 else MediaType.TV.value
                    link = f"/open?url={quote(self.get_play_url(item_id))}&type=ugreen"
                    image = self.get_local_image_by_id(item_id, remote=False, inner=True)
                    ret_items.append({
                        "id": item_id,
                        "name": name,
                        "type": item_type,
                        "image": image,
                        "link": link,
                    })
                if data.get("is_last_page"):
                    break
                page += 1
            return ret_items
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体列表出错：" + str(e))
            return []

    def get_play_url(self, item_id):
        """
        获取播放地址
        """
        if not self._api or not item_id:
            return ""
        try:
            play_data = self._api.get_play_url(item_id)
            if play_data:
                url = play_data.get("play_url") or play_data.get("url") or ""
                if url:
                    return url
            info = self._api.video_info(item_id)
            if info:
                video_type = info.get("type", 1)
                media_lib_set_id = info.get("media_lib_set_id", "")
                return f"{self._play_host}ugreen/v1/video/play?ug_video_info_id={item_id}&type={video_type}&media_lib_set_id={media_lib_set_id}"
            return ""
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取播放地址出错：" + str(e))
            return ""

    def get_playing_sessions(self):
        """
        获取正在播放的会话（绿联暂不支持）
        """
        return []

    def get_activity_log(self, num):
        """
        获取活动记录（绿联暂不支持）
        """
        return []

    def get_webhook_message(self, message):
        """
        解析Webhook报文（绿联暂不支持）
        """
        return {}

    def get_resume(self, num=12):
        """
        获取继续观看列表
        """
        if not self._api:
            return []
        try:
            data = self._api.recently_played(page=1, page_size=num)
            if not data:
                return []
            items = data.get("video_arr") or []
            ret_resume = []
            for item in items[:num]:
                if not isinstance(item, dict):
                    continue
                vi = item.get("video_info") if isinstance(item.get("video_info"), dict) else {}
                item_id = vi.get("ug_video_info_id") or vi.get("id")
                if not item_id:
                    continue
                name = vi.get("name") or vi.get("title") or ""
                video_type = vi.get("type", 1)
                item_type = MediaType.MOVIE.value if video_type == 1 else MediaType.TV.value
                link = f"/open?url={quote(self.get_play_url(item_id))}&type=ugreen"
                image = self.get_local_image_by_id(item_id, remote=False, inner=True)
                percent = item.get("play_progress") or item.get("progress")
                ret_resume.append({
                    "id": item_id,
                    "name": name,
                    "type": item_type,
                    "image": image,
                    "link": link,
                    "percent": percent,
                })
            return ret_resume
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取继续观看出错：" + str(e))
            return []

    def get_latest(self, num=20):
        """
        获取最近添加
        """
        if not self._api:
            return []
        try:
            data = self._api.recently_updated(page=1, page_size=num)
            if not data:
                return []
            items = data.get("video_arr") or []
            ret_latest = []
            for item in items[:num]:
                if not isinstance(item, dict):
                    continue
                vi = item.get("video_info") if isinstance(item.get("video_info"), dict) else {}
                item_id = vi.get("ug_video_info_id") or vi.get("id")
                if not item_id:
                    continue
                name = vi.get("name") or vi.get("title") or ""
                video_type = vi.get("type", 1)
                item_type = MediaType.MOVIE.value if video_type == 1 else MediaType.TV.value
                link = f"/open?url={quote(self.get_play_url(item_id))}&type=ugreen"
                image = self.get_local_image_by_id(item_id, remote=False, inner=True)
                ret_latest.append({
                    "id": item_id,
                    "name": name,
                    "type": item_type,
                    "image": image,
                    "link": link,
                })
            return ret_latest
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取最近添加出错：" + str(e))
            return []

    def get_host(self):
        """
        获取 host 地址
        """
        return self._host
