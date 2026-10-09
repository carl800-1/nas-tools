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
        # 最近一次失败原因（网络异常 / 非 JSON / 业务码 / 公钥为空 / 未返回 token），
        # 供上层「测试连接」把真实原因回显到页面上
        self.last_error = None
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
            # 新版登录客户端标识，与 Client-Id 同值（对齐 MoviePilot 上游 _common_headers）；
            # 只在登录阶段的明文请求里带，加密请求头不需要
            "UG-Client-Id": self._client_id,
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
        except Exception as err:
            # 网络层失败（连接被拒 / 超时 / DNS / SSL 校验）—— Errno 就在这里，
            # 113=找不到主机（IP 错/设备离线/跨 VLAN）111=端口无监听 110=被 DROP，
            # 这是排障的第一线索，必须原样回传
            self.last_error = f"请求 {url} 失败：{type(err).__name__}: {err}"
            log.error(f"请求绿联接口失败：{url} {err}")
            return None
        try:
            return resp.json()
        except Exception:
            # HTTP 已返回但不是 JSON（反代 HTML / 404 页 / 登录页）。只报
            # 「Expecting value」根本定位不了，必须打出状态码与响应片段
            snippet = (resp.text or "")[:200]
            self.last_error = (
                f"返回非 JSON 响应（HTTP {resp.status_code}，{url}），"
                f"Content-Type：{resp.headers.get('Content-Type')}，响应：{snippet}")
            log.error(
                f"请求绿联接口返回非 JSON：{url} HTTP {resp.status_code}，"
                f"Content-Type：{resp.headers.get('Content-Type')}，响应片段：{snippet!r}")
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
        self.last_error = None
        if not username or not password:
            self.last_error = "未填写用户名或密码"
            return None
        headers = self._common_headers()
        check_url = f"{self._host}/ugreen/v1/verify/check"
        try:
            import requests as req_lib
            session = self._session or req_lib
            check_resp = session.post(
                url=check_url,
                headers=headers, json={"username": username},
                timeout=self._timeout, verify=self._verify_ssl,
            )
        except Exception as err:
            # 网络层失败 —— Errno 113/111/110 是「地址或端口不对」的确证，
            # 此时协议、路径、签名、账号全都还没轮到
            self.last_error = (
                f"获取登录公钥失败：请求 {check_url} 无响应或异常"
                f"（{type(err).__name__}: {err}）")
            log.error(f"绿联{self.last_error}")
            return None
        try:
            check_json = check_resp.json()
        except Exception:
            # HTTP 已返回但不是 JSON（端口连到了别的服务 / 反代 HTML / 404 页）。
            # 只报「Expecting value」定位不了，必须打出状态码与响应片段
            snippet = (check_resp.text or "")[:200]
            self.last_error = (
                f"获取登录公钥失败：返回非 JSON 响应"
                f"（HTTP {check_resp.status_code}，{check_url}），"
                f"Content-Type：{check_resp.headers.get('Content-Type')}，响应：{snippet}")
            log.error(f"绿联{self.last_error}")
            return None
        check_result = self._build_result(check_json)
        if not check_result["code"] == 200:
            self.last_error = (
                f"获取登录公钥失败："
                f"{check_result['msg'] or ('code=%s' % check_result['code'])}（{check_url}）")
            log.error(f"绿联{self.last_error}")
            return None
        rsa_token = self._extract_rsa_token(check_json, check_resp.headers)
        login_public_key = self._decode_public_key(rsa_token)
        if not login_public_key:
            self.last_error = "获取登录公钥失败：服务端未返回 RSA 公钥（接口版本可能已变更）"
            log.error(f"绿联{self.last_error}")
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
            # _request_json 已把网络层 / 非 JSON 的具体原因写进 self.last_error
            self.last_error = self.last_error or "登录失败：登录接口无响应"
            log.error(f"绿联{self.last_error}")
            return None
        login_result = self._build_result(login_json)
        if not login_result["code"] == 200 or not isinstance(login_result["data"], dict):
            self.last_error = (
                f"登录失败："
                f"{login_result['msg'] or ('code=%s' % login_result['code'])}")
            log.error(f"绿联{self.last_error}")
            return None
        token = str(login_result["data"].get("token") or "").strip()
        public_key = self._decode_public_key(str(login_result["data"].get("public_key") or ""))
        if not token or not public_key:
            self.last_error = "登录失败：服务端未返回 token/public_key（接口版本可能已变更）"
            log.error(f"绿联{self.last_error}")
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
        self.last_error = None
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
            self.last_error = "未登录（加密上下文未初始化，请先完成登录）"
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
            # _request_json 已写具体原因（网络异常 / 非 JSON 响应），这里只兜底
            self.last_error = self.last_error or f"接口请求失败（{url}）"
            return {"code": -1, "msg": "接口请求失败", "data": None}
        decrypted = self._crypto.decrypt_response(payload, aes_key)
        result = self._build_result(decrypted)
        if result["code"] == 200:
            self.last_error = None
        else:
            self.last_error = f"接口 {api_path} 返回 code={result['code']}：{result['msg']}"
        return result

    def current_user(self):
        result = self.request("v1/user/current/user")
        if result["code"] == 200 and isinstance(result["data"], dict):
            return result["data"]
        return None

    def video_all(self, classification, page=1, page_size=20,
                  sort_type=1, order_type=2):
        """全量条目接口。

        ⚠️ 默认 sort_type 必须是**稳定**值：服务端 sort_type=2（原默认）排序键
        有并列值、切页会跨页重叠 ⇒ 逐页拉取会静默漏条目。详见
        `UgreenClient._fetch_classification()` 的注释。
        """
        result = self.request(
            "v1/video/all",
            params={
                "page": page, "pageSize": page_size,
                "classification": classification,
                "sort_type": sort_type, "order_type": order_type,
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
    _video_info_cache = {}
    # 最近一次失败原因 —— 「测试连接」失败时由 web 层直接回显到页面上
    # （web/action.py::__test_connection 已支持读该属性，飞牛影视在用）
    last_error = None
    # v6.3.5：媒体库「库ID -> [根路径, ...]」映射。绿联的 media_list() 不返回 path，
    # 路径只能从不带 path 调 poster_wall_get_folder() 的 folder_arr 里取。
    _lib_paths = None
    # v6.3.7：媒体库「库ID -> [封面路径, ...]」。与 _lib_paths 从**同一次**
    # poster_wall_get_folder 请求里顺带取出（folder.cover），零额外请求。
    # 为什么需要它：media_list() 自带的 poster_paths 可能是**远程** scraper 地址
    # （scraper.ugnas.com 带 auth_key，实测已过期 ⇒ 403），全远程的库必须靠
    # 这份**本地**目录封面兜底，否则该库卡片仍然是黑图。
    _lib_covers = None
    # v6.3.5：v1/video/all 两个分类全量拉取后的原始条目缓存
    _all_videos = None
    # v6.3.5：全量分页用的排序参数。sort_type=2（服务端默认）的排序键存在大量
    # 并列值，分页窗口会**跨页重叠** —— 实测 833 条电影里有 6 条同时出现在相邻
    # 两页，于是另外 6 条被挤出所有页窗口、永远拉不到（只能拿到 827 条）。
    # sort_type=1 / 3 实测零重叠，用作主通道与备用通道。
    STABLE_SORT = (1, 2)
    FALLBACK_SORT = (3, 2)

    def __init__(self, config=None):
        if config:
            self._client_config = config
        else:
            self._client_config = Config().get_config('ugreen')
        self.init_config()

    def init_config(self):
        # v6.3.5：路径映射与全量条目缓存都是「配置相关」的派生数据，配置一变必须重建
        self._lib_paths = None
        self._lib_covers = None
        self._all_videos = None
        self._video_info_cache = {}
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
        else:
            self.last_error = (
                "未读到「绿联影视」配置：请先在 设置 → 媒体服务器 → 绿联影视 里"
                "填好地址/用户名/密码，并点「确定」保存")

    def _connect(self):
        """使用绿联加密 API 登录"""
        if not HAS_CRYPTO:
            self.last_error = "缺少 cryptography 库（镜像内未安装），请重装镜像或执行 pip install cryptography"
            log.error(f"【{self.client_name}】{self.last_error}")
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
            if not token:
                # api.last_error 里是登录环节的具体原因，且自身已带
                # 「获取登录公钥失败：」/「登录失败：」前缀，不要再套一层
                self.last_error = api.last_error or "登录失败：用户名或密码不正确"
                log.error(f"【{self.client_name}】{self.last_error}")
                api.close()
                return
            self._api = api
            self._userinfo = api.current_user()
            if self._userinfo is None:
                self.last_error = (
                    f"登录成功但获取用户信息失败："
                    f"{api.last_error or 'token 未被服务端接受'}")
                log.error(f"【{self.client_name}】{self.last_error}")
                self._api = None
                api.close()
                return
            self.last_error = None
            log.info(f"【{self.client_name}】登录成功，用户：{self._username}")
        except Exception as e:
            self.last_error = f"登录异常：{type(e).__name__}: {e}"
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】{self.last_error}")

    @classmethod
    def match(cls, ctype):
        return True if ctype in [cls.client_id, cls.client_type, cls.client_name] else False

    def get_type(self):
        return self.client_type

    def get_status(self):
        """
        测试连通性
        """
        if not self._host:
            self.last_error = "未填写服务端地址（请在 设置 → 媒体服务器 → 绿联影视 里填写并保存）"
            log.error(f"【{self.client_name}】{self.last_error}")
            return False
        if not self._username or not self._password:
            self.last_error = "用户名或密码未填写（请补全后保存再测试）"
            log.error(f"【{self.client_name}】{self.last_error}")
            return False
        if not self._api:
            # init_config/_connect 已把更具体的原因写进 last_error，优先用它；
            # 注意「未建立连接」的真正原因通常在上一句，不要在这里覆盖掉
            self.last_error = self.last_error or (
                f"未建立连接（地址 {self._host}），请检查地址、端口、用户名与密码")
            log.error(f"【{self.client_name}】{self.last_error}")
            return False
        try:
            user = self._api.current_user()
            if user is None:
                self.last_error = (
                    f"连接中断：{self._api.last_error or 'token 未被服务端接受'}")
                log.error(f"【{self.client_name}】{self.last_error}")
                return False
            self.last_error = None
            return True
        except Exception as e:
            self.last_error = f"测试连接出错：{type(e).__name__}: {e}"
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】{self.last_error}")
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

        ⚠️ 旧实现靠 `v1/video/all` 的 `search` 参数按标题搜 —— 实测该参数被服务端
        **完全忽略**（传任何词都返回同一批默认排序条目），所以命中判断 `name == title`
        恒不成立 ⇒ **恒返回空** ⇒ 下载查重失效，媒体库里已有的电影会被重复下载。
        改为在本地全量条目里过滤（`_all_library_videos()` 带进程内缓存）。

        另：旧实现用 `or` 连接「片名相同」与「年份相同」，导致**年份相同但片名不同**
        的电影也被判为「已存在」（假阳性，会拦住本该下载的片子）。这里改为：片名必须
        匹配（自动忽略「第 N 季」等后缀差异），年份只在存在相符候选时用于收窄。
        """
        if not self._api:
            return None
        try:
            matched = []
            for video in (self._all_library_videos() or []):
                if not isinstance(video, dict):
                    continue
                info = self._flatten_item(video)
                if info.get("type") != 1:
                    # 1 = 电影；剧集为 2，不参与电影查重
                    continue
                name = info.get("name") or info.get("title") or ""
                if not self._title_matches(name, title):
                    continue
                matched.append({
                    "title": name,
                    "year": str(info.get("year") or info.get("release_year") or ""),
                })
            if year and matched:
                narrowed = [m for m in matched if m.get("year") == str(year)]
                if narrowed:
                    matched = narrowed
            return matched
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】搜索电影出错：" + str(e))
            return None

    def get_tv_episodes(self, item_id=None, title=None, year=None, tmdbid=None, season=None):
        """
        根据标题、年份、季查询电视剧所有集信息

        ⚠️ 绿联的剧集结构与 Emby/Plex 完全不同，两个必须知道的事实：
        ① `ug_video_info_id` 的粒度是**季**而不是「剧」—— 同一部剧的每一季都是一条
           独立条目（「宙斯之血 第 1 季」id=681／「第 2 季」id=677）；
        ② `v2/video/details/getTV` 的返回里，**季列表在 `season_info`、集列表在
           `tv_info`**，`tv_info[i]["episode"]` 才是集号（`ep_name` 形如
           「宙斯之血 - S02E01 - 第 1 集」）。旧实现读的 `episodes` / `episode_arr`
           两个键在真实返回里**根本不存在** ⇒ 恒定返回空列表。

        另一条旧实现的死路：靠 `v1/video/all` 的 `search` 参数按标题搜剧 —— 该参数
        被服务端忽略（传什么都返回同一批默认排序条目），且条目名带「第 N 季」后缀，
        `vi_name == title` 的精确比较永远不成立 ⇒ item_id 恒为空。
        """
        if not self._api:
            return []
        try:
            candidates = []
            if item_id:
                candidates = [{"item_id": item_id, "season_num": 0, "name": ""}]
            else:
                if not title:
                    return []
                candidates = self._find_tv_candidates(title, year)
                if not candidates:
                    log.info(f"【{self.client_name}】未在媒体库中找到剧集：{title}"
                             f"（{year or '不限年份'}）")
                    return []
            if season is not None and candidates:
                # 先用条目名解析出的季号预筛，避免对无关季白跑 getTV 请求；
                # 解析不出季号的条目（season_num=0）保守保留，交由 getTV 复核。
                narrowed = [c for c in candidates
                            if not c.get("season_num")
                            or int(c["season_num"]) == int(season)]
                if narrowed:
                    candidates = narrowed
            exists_episodes = []
            for cand in candidates:
                detail = self._api.get_tv(cand.get("item_id"))
                if not detail:
                    continue
                # 权威季号以 getTV 的 video_info.season 为准（实测多季剧准确）
                video_info = detail.get("video_info")
                video_info = video_info if isinstance(video_info, dict) else {}
                season_num = 0
                raw_season = video_info.get("season")
                if isinstance(raw_season, int) and raw_season > 0:
                    season_num = raw_season
                if not season_num:
                    season_num = self._parse_season_num(
                        video_info.get("name") or cand.get("name") or "")
                if not season_num:
                    season_num = int(cand.get("season_num") or 0)
                for ep in (detail.get("tv_info") or []):
                    if not isinstance(ep, dict):
                        continue
                    ep_num = ep.get("episode")
                    if not isinstance(ep_num, int) or ep_num <= 0:
                        ep_num = 0
                    ep_season, ep_from_name = self._parse_season_episode(ep.get("ep_name"))
                    if not ep_num:
                        # 兜底：从集名 "S02E01" 解析集号
                        ep_num = ep_from_name
                    if not ep_num:
                        continue
                    real_season = season_num or ep_season or 0
                    if season is not None and real_season and int(season) != int(real_season):
                        continue
                    exists_episodes.append({"season_num": real_season,
                                            "episode_num": ep_num})
            return exists_episodes
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取剧集信息出错：" + str(e))
            return []

    def get_no_exists_episodes(self, meta_info, season, total_num):
        """
        查询缺少哪几集
        """
        # 查询不了就返回 None（当前契约：None = 无法确认，由调用方回退到目录扫描）。
        # 原先返回 [] 会被 check_exists_medias 当成「一集都不缺」⇒ 误判订阅已完成并清除，
        # 而 emby/plex 在这一层返回的都是 None。
        if not self._api:
            return None
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
                return None
            exists_episodes = [ep.get("episode_num") for ep in exists_episodes]
            total_episodes = [ep for ep in range(1, total_num + 1)]
            return list(set(total_episodes).difference(set(exists_episodes)))
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】查询缺失集数出错：" + str(e))
            return None

    def get_episode_image_by_id(self, item_id, season_id, episode_id):
        """
        根据itemid、season_id、episode_id查询图片地址

        ⚠️ 同源缺陷：旧实现读 `video_info.get("episodes") or .get("episode_arr")`，
        这两个键在真实返回里不存在 ⇒ 恒返回空。集列表在 getTV 的 `tv_info` 里，
        且每条集自带 `cover_path`（本地封面路径），无需再按集 id 回查详情接口。
        """
        if not self._api:
            return ""
        try:
            detail = self._api.get_tv(item_id)
            if not detail:
                return ""
            for ep in (detail.get("tv_info") or []):
                if not isinstance(ep, dict):
                    continue
                ep_num = ep.get("episode")
                if not isinstance(ep_num, int) or ep_num <= 0:
                    _, ep_num = self._parse_season_episode(ep.get("ep_name"))
                if not ep_num or ep_num != episode_id:
                    continue
                cover = ep.get("cover_path")
                if cover:
                    url = self._api.get_image_stream_url(cover)
                    if url:
                        return url
                return self.get_remote_image_by_id(
                    ep.get("ug_television_episode_id"), "Primary") or ""
            return ""
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

    def get_local_image_by_id(self, item_id, remote=True, inner=False, video_info=None):
        """
        根据ItemId查询本地图片地址
        :param video_info: 可选的已缓存 video_info 数据，避免重复请求

        ⚠️ v6.3.7 修复（网页端封面全黑）：旧实现 inner 分支拼的是
        `getImageStream`（少了 'a'）且**不带凭证**，真机实测该端点恒返回
        {"code":9405,...}（授权错误，77 字节 JSON）；/img 中转把这 77 字节
        JSON 当图片吐给浏览器 ⇒ 条目封面 / 媒体库封面 / 正在观看 / 最近添加
        **全部黑图**。正解是 getImaStream + query 凭证，见 _image_url_by_path()。
        """
        if not self._api:
            return ""
        try:
            info = video_info
            if info is None:
                info = self._api.video_info(item_id)
            if not info:
                return ""
            path = info.get("poster_path") or info.get("poster")
            if not path:
                return ""
            if remote:
                return self._api.get_image_stream_url(path)
            return self._image_url_by_path(path, inner=inner)
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

    # ==================================================================
    # v6.3.5 新增：媒体库路径映射 / 类型推断 / 全量条目获取
    # ------------------------------------------------------------------
    # 背景：绿联 media_list() 只返回 media_lib_set_id / media_name / video_count，
    # 既没有 path 也没有 media_lib_type。旧实现把 lib['path'] 当库根目录用，
    # 恒为空 ⇒ get_items() 在每个库的第一道门槛就 return [] ⇒ 同步数量恒为 0。
    # ==================================================================

    def _load_library_paths(self):
        """
        建立「媒体库ID -> [根路径, ...]」映射。

        路径只能从不带 path 调 poster_wall_get_folder() 的返回里取：
        它的 folder_arr 每个元素带 media_lib_set_id 与 path。
        ★ 一个库可能有多个根目录（实测：电影 = 华语电影 + 外语电影，
          电视剧 = 国产剧 + 欧美剧），必须全部保留，否则会漏掉整个子库。
        """
        if self._lib_paths is not None:
            return self._lib_paths
        mapping = {}
        covers = {}
        try:
            data = self._api.poster_wall_get_folder(page=1, page_size=200) or {}
            for folder in (data.get("folder_arr") or []):
                if not isinstance(folder, dict):
                    continue
                lib_id = folder.get("media_lib_set_id")
                if lib_id is None:
                    continue
                lib_path = folder.get("path")
                if lib_path:
                    paths = mapping.setdefault(str(lib_id), [])
                    if str(lib_path) not in paths:
                        paths.append(str(lib_path))
                # v6.3.7：folder 自带的封面字段是**本地**路径，用于 media_list()
                # 的 poster_paths 全是远程地址（会 403）时兜底。
                # v6.10.1：原来只取 folder["cover"]，真机上有库这一项为空 ⇒ 卡片黑图。
                # 同一条 folder 还带 covers[]（多条）与 backdrop_path，全部收下当候选。
                self._push_cover(covers, lib_id, folder.get("cover"))
                for extra in (folder.get("covers") or []):
                    self._push_cover(covers, lib_id, extra)
                self._push_cover(covers, lib_id, folder.get("backdrop_path"))
            # v6.10.1：同一次请求的 video_arr（库根目录下的条目）也常带本地海报，
            # 按 media_lib_set_id 分桶收进候选 —— 零额外请求，专门救「库自身
            # 封面全是云端地址」的那些库。
            for video in (data.get("video_arr") or []):
                if not isinstance(video, dict):
                    continue
                lib_id = video.get("media_lib_set_id")
                if lib_id is None:
                    continue
                info = self._flatten_item(video)
                for key in ("poster_path", "poster", "cover"):
                    self._push_cover(covers, lib_id, info.get(key))
            log.info(f"【{self.client_name}】媒体库根路径映射：{mapping}")
            log.info(f"【{self.client_name}】媒体库封面候选："
                     f"{ {k: len(v) for k, v in covers.items()} }")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体库根路径出错：" + str(e))
        self._lib_paths = mapping
        self._lib_covers = covers
        return mapping

    @staticmethod
    def _push_cover(covers, lib_id, value):
        """
        v6.10.1：把一个候选封面路径并入 `covers[lib_id]`（去空、去重、只留本地路径）。

        远程地址（`http(s)://`，即 scraper.ugnas.com 那批带 auth_key 的云端海报）
        一律**不收**：它们签名有时效，实测已过期返回 403，收进来只会白占位。
        """
        value = str(value or "").strip()
        if not value or value.startswith("http"):
            return
        arr = covers.setdefault(str(lib_id), [])
        if value not in arr:
            arr.append(value)

    def _image_url_by_path(self, path, inner=True):
        """
        v6.3.7 新增：把绿联的**本地图片路径**转成浏览器可用的图片地址。

        ⚠️ 真机实测要点（踩过坑，勿改）：
          · 端点必须是 `getImaStream`（少了 'a' 的 `getImageStream` 恒返回
            {"code":9405,...} 授权错误，77 字节 JSON）。
          · 凭证**必须在 query 里**（token / ugk，由 get_image_stream_url 按
            _is_ugk 自动选名）；header / cookie 一律不认 —— 所以不能走
            get_image_cookies() 那条「中转注入 header」的路子。
          · name 参数由 get_image_stream_url() 内部 urlencode **恰好编码一次**
            （空格→'+'、'/'→'%2F'）；**不要**再套一层编码 —— 实测单层编码
            5/5 取到 JPEG，双重编码 0/5（服务端只认单层编码的路径）。
        """
        if not path or not self._api:
            return ""
        stream_url = self._api.get_image_stream_url(path)
        if not stream_url:
            return ""
        if inner:
            return self.get_nt_image_url(stream_url)
        return stream_url

    def _library_cover_path(self, lib):
        """
        v6.3.7 新增：为媒体库挑一个**可用的本地封面路径**。

        取值优先级（都是实测过的真实字段）：
          1. custom_cover（用户自定义封面）
          2. media_list() 的 poster_paths —— 第一个**本地**路径
          3. poster_wall_get_folder() 的 folder.cover / folder.covers / folder.backdrop_path
             （同一次请求顺带取到，见 `_load_library_paths()`）
          4. **该库任一已缓存条目的本地海报**（`_lib_item_cover()`）
          5. media_list() 的 backdrop_paths —— 第一个**本地**路径
        ⚠️ 远程地址（http(s):// 开头，即 scraper.ugnas.com 那批）一律跳过：
           它们带 auth_key 有时效，实测已过期返回 403 ⇒ 用不了。
        ⚠️ 必须先调过 _load_library_paths()（get_libraries 里已保证）。
        ⚠️ 全部落空时**返回空串**：由模板决定画什么。**不要**在这里编造路径，
           否则 `<img>` 会请求一个不存在的地址（浏览器画不出图，白折腾一趟）。
        """
        lib_id = str(lib.get("media_lib_set_id") or lib.get("id", ""))
        candidates = []
        custom = lib.get("custom_cover")
        if custom:
            candidates.append(custom)
        candidates.extend(lib.get("poster_paths") or [])
        candidates.extend((self._lib_covers or {}).get(lib_id) or [])
        candidates.extend(lib.get("backdrop_paths") or [])
        for one in candidates:
            one = str(one or "").strip()
            if one and not one.startswith("http"):
                return one
        # v6.10.1：最后再试「该库某条已缓存条目的本地海报」—— 真机上有库的
        # custom_cover / poster_paths / folder 封面**全是云端地址或为空**，
        # 但条目自己的 poster_path 是本地文件，用它兜底。
        return self._lib_item_cover(lib_id)

    def _lib_item_cover(self, lib_id):
        """
        v6.10.1：取「该库第一条带**本地**海报的已缓存条目」的海报路径。

        ⚠️ 只在条目列表**已经在内存里**时命中（`_all_videos`，由「媒体库同步」
        或功能查询填充）。这里**绝不**主动触发全量拉取 —— 否则每次打开首页
        都会多跑几十个请求，而首页刷新是高频操作。
        ⚠️ 只认本地路径，云端地址（带 auth_key、会过期 403）一样不能用。
        """
        videos = getattr(self, "_all_videos", None)
        if not videos or not lib_id:
            return ""
        for video in videos:
            if not isinstance(video, dict):
                continue
            if str(video.get("media_lib_set_id")) != str(lib_id):
                continue
            info = self._flatten_item(video)
            for key in ("poster_path", "poster", "cover"):
                one = str(info.get(key) or "").strip()
                if one and not one.startswith("http"):
                    return one
        return ""

    # ------------------- v6.3.6：剧集定位与集号解析 -------------------
    # 绿联把「季」做成**独立条目**：同一部剧的每一季都是一条独立条目，名字带
    # 「第 N 季」后缀（实测「宙斯之血 第 1 季」id=681、「宙斯之血 第 2 季」id=677），
    # `ug_video_info_id` 的粒度就是**季**而不是「剧」。因此按标题找剧必须先归一化
    # 掉季后缀，否则条目名与 TMDB 标题永远不相等（旧实现恒空的成因之一）。

    @staticmethod
    def _normalize_title(name):
        """把绿联条目名归一化成可比较的剧名：去掉「第 N 季」/「季 N」/「Season N」/尾部年份。

        ⚠️ 季后缀有**两种语序**，且在真实媒体库里并存（实测 123 条剧集：「第 N 季」
        58 条、「季 N」47 条，如「诛仙 季 1」「生活大爆炸 季 12」）。只剥离「第 N 季」
        会让「诛仙 季 1」归一化后仍是「诛仙 季 1」≠「诛仙」⇒ 条目被判为不存在 ⇒
        整部剧所有季都误报「一集都没有」，并触发重复下载。
        """
        if not name:
            return ""
        text = str(name).strip()
        for _ in range(2):
            text = re.sub(r"[\s\-_·:：]*第\s*[\d一二三四五六七八九十]+\s*季.*$", "", text).strip()
            text = re.sub(r"[\s\-_·:：]*[Ss]eason\s*\d+.*$", "", text).strip()
            text = re.sub(r"[\s\-_·:：]*季\s*\d+.*$", "", text).strip()
        text = re.sub(r"[\s\-_·]*[（(]\s*(?:19|20)\d{2}\s*[)）]\s*$", "", text).strip()
        return text

    @classmethod
    def _title_matches(cls, name, title):
        """条目名与目标标题是否指向同一部剧（自动忽略「第 N 季」后缀差异）。"""
        if not name or not title:
            return False
        raw_name = str(name).strip()
        raw_title = str(title).strip()
        if raw_name == raw_title:
            return True
        base_name = cls._normalize_title(raw_name)
        base_title = cls._normalize_title(raw_title)
        return bool(base_name) and base_name == base_title

    @staticmethod
    def _cn_numeral(text):
        """中文数字转整数（一~九十九）：'十二' -> 12；解析不到返回 0。"""
        if not text:
            return 0
        text = str(text).strip()
        if text.isdigit():
            return int(text)
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if "十" in text:
            left, _, right = text.partition("十")
            if left and left not in digits:
                return 0
            if right and right not in digits:
                return 0
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        total = 0
        for ch in text:
            if ch not in digits:
                return 0
            total = total * 10 + digits[ch]
        return total

    @staticmethod
    def _parse_season_num(name):
        """从条目名解析季号：'宙斯之血 第 2 季'/'诛仙 季 1'/'大道朝天 第一季' -> 1~N；解析不到返回 0。

        ⚠️ 必须同时认「季 N」这种语序（数字在「季」**之后**，实测 47/123 条剧集这么写）：
        只认「第 N 季」会让这些条目的季号恒为 0，多季剧的目标季在预筛阶段被整条滤掉，
        最终误报「该季一集都没有」。
        """
        if not name:
            return 0
        text = str(name)
        matched = re.search(r"第\s*([\d一二三四五六七八九十]+)\s*季", text)
        if matched:
            return UgreenClient._cn_numeral(matched.group(1))
        matched = re.search(r"[Ss]eason\s*(\d+)", text)
        if matched:
            return int(matched.group(1))
        matched = re.search(r"季\s*(\d+)", text)
        if matched:
            return int(matched.group(1))
        return 0

    @staticmethod
    def _parse_season_episode(ep_name):
        """从集名解析 (季, 集)：'宙斯之血 - S02E01 - 第 1 集' -> (2, 1)。"""
        if not ep_name:
            return 0, 0
        matched = re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", str(ep_name))
        if matched:
            return int(matched.group(1)), int(matched.group(2))
        return 0, 0

    def _find_tv_candidates(self, title, year=None):
        """
        按标题在**本地全量条目**里找剧集候选，返回 [(item_id, 季号, 年份, 名字), ...]。

        ⚠️ 为什么不能在服务端按标题搜：实测 `v1/video/all` 的 `search` 参数被
        服务端**完全忽略** —— 传「宙斯之血」「遗失的世界」「宙斯」返回的都是同一批
        前 20 条（默认排序），压根不是搜索。这一版固件上筛选参数（media_lib_set_id /
        search）统统不生效，只能全量拉回本地过滤（`_all_library_videos()` 有缓存）。

        同一部剧的每一季都是独立条目（名字带「第 N 季」），所以返回**列表**。
        `year` 是**软条件**：多季剧各季年份不同（「宙斯之血」第 1 季 2020、第 2 季
        2024），硬过滤会把整部剧滤掉，反而误报「一集都没有」。
        """
        matched = []
        for video in (self._all_library_videos() or []):
            if not isinstance(video, dict):
                continue
            info = self._flatten_item(video)
            if info.get("type") != 2:
                # 2 = 剧集；只要剧集（电影的 type 为 1）
                continue
            name = info.get("name") or info.get("title") or ""
            if not self._title_matches(name, title):
                continue
            item_id = info.get("ug_video_info_id") or info.get("id")
            if not item_id:
                continue
            matched.append({
                "item_id": item_id,
                "season_num": self._parse_season_num(name),
                "year": str(info.get("year") or info.get("release_year") or ""),
                "name": name,
            })
        if year and matched:
            # year 是**软条件**（只排序、不过滤）：多季剧各季条目年份不同
            # （实测「宙斯之血」第 1 季 2020、第 2 季 2024），硬过滤会把目标季
            # 整条滤掉；而 get_no_exists_episodes 传的正是**首播年**
            # ⇒ 除首季外都会误报「一集都没有」，进而触发重复下载。
            # 这里只把年份相符的候选排到前面（sort 稳定，不改变同优先级顺序）。
            matched.sort(key=lambda m: 0 if m.get("year") == str(year) else 1)
        return matched

    @staticmethod
    def _flatten_item(video, detail=None):
        """
        把条目摊平成一份 dict：**内层 video_info 先铺、外层条目再盖（外层优先）**。

        为什么必须摊平、而不是逐字段两来源查找：绿联 `/v1/video/all` 的条目结构
        随固件版本变过 —— 老版本把详情（含 poster_path）包在 `video_info` 子字典里，
        新版本直接平铺在条目外层。`get_local_image_by_id()` 内部只认
        `info["poster_path"]`，若把原始条目整份交给它，老固件结构下图片恒为空。
        摊平后两种结构对下游完全一致。
        :param detail: 可选的 `v1/video/info` 回源结果，只补摊平后仍缺失的键
        """
        merged = {}
        inner = video.get("video_info")
        if isinstance(inner, dict):
            merged.update(inner)
        for key, value in video.items():
            if key != "video_info":
                merged[key] = value
        if isinstance(detail, dict):
            for key, value in detail.items():
                if merged.get(key) in (None, "", [], {}):
                    merged[key] = value
        return merged

    def _video_info_cached(self, item_id):
        """
        按 item_id 取 `v1/video/info` 详情（进程内缓存，同一部片只请求一次）。

        只在原始条目缺关键字段时调用 —— 老版本固件每个条目都要回源（与原实现一致），
        新版本固件字段齐全则一次都不用请求。
        """
        if item_id is None:
            return {}
        cache = getattr(self, "_video_info_cache", None)
        if cache is None:
            cache = self._video_info_cache = {}
        if item_id in cache:
            return cache[item_id]
        try:
            info = self._api.video_info(item_id) or {}
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取条目详情出错 id={item_id}：" + str(e))
            info = {}
        cache[item_id] = info
        return info

    @staticmethod
    def _infer_library_type(lib_name, paths=None):
        """
        绿联 media_list() 不返回媒体库类型，只能推断。

        ⚠️ 判定顺序必须是「先电影、后剧」：否则「动漫电影」会因含「动漫」被判成剧集。
        （上游 MoviePilot 的 __infer_library_type 正是「剧/综艺/动漫/纪录片」在前，
          会把「动漫电影」判成剧集，这里不跟。）
        """
        name = str(lib_name or "")
        path_text = ",".join(paths or [])
        if "电影" in name or "电影" in path_text:
            return MediaType.MOVIE.value
        if any(key in name for key in ("电视剧", "剧", "综艺", "动漫", "纪录片")):
            return MediaType.TV.value
        if "电视剧" in path_text:
            return MediaType.TV.value
        return MediaType.MOVIE.value

    def _all_library_videos(self, force=False):
        """
        拉取媒体服务器上**全部**视频条目（movie + tv），供按库分桶使用。

        为什么不用目录树遍历（poster_wall_get_folder 递归下钻）：
          实测同一台 NAS 上，完整 BFS 需要 900+ 次请求（每个影片目录一次），
          而 v1/video/all 全量只需 ceil(833/20) + ceil(123/20) = 49 次。
        为什么只能全量拉、不能在服务端按库筛选：
          实测 media_lib_set_id 传 1/3/5 返回的都是全量 833 条（该参数被忽略），
          且 pageSize 上限为 20（传 100/200/500/1000 都只返回 20 条）。
        好在每条 item 自带 media_lib_set_id，本地分桶即可精确归库。
        """
        if self._all_videos is not None and not force:
            return self._all_videos
        videos = []
        try:
            # -102 电影 / -103 电视剧（其余取值实测返回「参数错误！」）
            for classification in (-102, -103):
                total, items = self._fetch_classification(classification,
                                                          *self.STABLE_SORT)
                if total and len(items) < total:
                    # 稳定排序也取不全（服务端仍可能重叠）⇒ 换一个稳定排序再拉一遍，
                    # 只补缺不重复计，最大程度逼近服务端 total_num。
                    log.warn(f"【{self.client_name}】分类 {classification} 按稳定排序只取到 "
                             f"{len(items)}/{total} 条，改用备用排序补拉")
                    _, more = self._fetch_classification(classification,
                                                         *self.FALLBACK_SORT)
                    have = {it.get("ug_video_info_id") or it.get("id") for it in items}
                    for it in more:
                        item_id = it.get("ug_video_info_id") or it.get("id")
                        if item_id in have:
                            continue
                        have.add(item_id)
                        items.append(it)
                if total and len(items) < total:
                    log.warn(f"【{self.client_name}】全量条目仍缺 {total - len(items)} 条："
                             f"接口报 {total} 条，实得 {len(items)} 条"
                             f"（classification={classification}）")
                videos.extend(items)
            log.info(f"【{self.client_name}】全量条目拉取完成：{len(videos)} 条")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】全量拉取条目出错：" + str(e))
        self._all_videos = videos
        return videos

    def _fetch_classification(self, classification, sort_type, order_type):
        """
        按分类逐页拉全量条目，返回 `(服务端 total_num, 条目列表)`（已按 id 去重）。

        ⚠️⚠️ 为什么必须指定**稳定**的 sort_type（这里用 1，备用 3）：
          `v1/video/all` 的页窗口是按排序键切的。`sort_type=2`（服务端默认、
          也是改前的写法）排序键有大量并列值，服务端切页**不稳定** —— 实测同一台
          机器上 833 条电影里有 6 条（870/848/825/750/683/617）同时出现在相邻两页
          （4/5、5/6、6/7、9/10、12/13、15/16），于是另外 6 条被挤出所有页窗口、
          **永远拉不到**，只能拿到 827 条。且服务端 total_num 仍报 833（把跨页
          重复也算进去了），所以「实收条数 == total_num」这个判据**发现不了**问题
          —— 用原始累计条数做判据会被骗，必须用**去重后**的条数。
          换 sort_type=1 / 3 实测去重后正好 833，零重叠。
        ⚠️ pageSize 服务端硬上限 20（传 50/100/200 都只回 20 条），不要改大。
        """
        items = []
        seen = set()
        page = 1
        total = 0
        # 翻页上限按 total_num 动态放宽：写死页数会在「库里条目超过
        # 上限 × 20」时**静默少同步**，而日志里完全看不出来。
        max_pages = 200
        while page <= max_pages:
            data = self._api.video_all(classification=classification, page=page,
                                       page_size=20, sort_type=sort_type,
                                       order_type=order_type)
            if not data:
                break
            if not total:
                total = data.get("total_num") or 0
                if total:
                    max_pages = -(-total // 20) + 5
            arr = [x for x in (data.get("video_arr") or []) if isinstance(x, dict)]
            if not arr:
                break
            for item in arr:
                item_id = item.get("ug_video_info_id") or item.get("id")
                if item_id is not None:
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                items.append(item)
            # 判据用**去重后**的条数：用原始累计条数会被「跨页重复」骗过。
            if (total and len(seen) >= total) or data.get("is_last_page"):
                break
            page += 1
        return total, items

    def _build_play_url(self, item_id, video_type, media_lib_set_id):
        """
        按绿联 Web 端的固定格式拼播放链接（与 get_play_url() 的兜底分支一致）。

        同步上千条条目时，逐条再打一次 v1/video/play_url/get 是分钟级的浪费，
        而同步链路本身并不消费 link 字段，因此这里直接拼。
        """
        base = self._play_host or self._host or ""
        if not base:
            return ""
        if not base.endswith("/"):
            base += "/"
        return (f"{base}ugreen/v1/video/play?ug_video_info_id={item_id}"
                f"&type={video_type}&media_lib_set_id={media_lib_set_id}")

    def _walk_library_videos(self, lib_id):
        """
        兜底：按目录树 BFS 遍历某个媒体库。

        用于「条目不带 media_lib_set_id」的老固件/特殊固件 —— 此时 _all_library_videos()
        的分桶会落空。算法与上游 MoviePilot 的 _iter_library_videos 一致：
        收 video_arr 的同时把 folder_arr 的子目录入队下钻。
        """
        from collections import deque
        paths = (self._load_library_paths() or {}).get(str(lib_id)) or []
        if not paths:
            log.warn(f"【{self.client_name}】get_items: 库 {lib_id} 没有可用根路径，无法遍历")
            return []
        queue = deque(paths)
        visited = set()
        result = []
        while queue and len(visited) < 20000:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            page = 1
            while page <= 200:
                data = self._api.poster_wall_get_folder(
                    path=current, page=page, page_size=100,
                    sort_type=1, order_type=1,
                )
                if not data:
                    break
                for video in (data.get("video_arr") or []):
                    if isinstance(video, dict):
                        result.append(video)
                for folder in (data.get("folder_arr") or []):
                    if not isinstance(folder, dict):
                        continue
                    sub_path = folder.get("path")
                    if sub_path and str(sub_path) not in visited:
                        queue.append(str(sub_path))
                if data.get("is_last_page"):
                    break
                page += 1
        log.info(f"【{self.client_name}】get_items: 库 {lib_id} 目录树遍历到 {len(result)} 条")
        return result

    def get_libraries(self):
        """
        获取媒体服务器所有媒体库列表
        """
        if not self._api:
            return []
        try:
            libs = self._api.media_list()
            if not libs:
                log.warn(f"【{self.client_name}】media_list() 返回空列表")
                return []
            # v6.3.5：media_list() 不含 path / media_lib_type，分别靠映射与推断补齐
            path_map = self._load_library_paths()
            libraries = []
            for lib in libs:
                lib_id = str(lib.get("media_lib_set_id") or lib.get("id", ""))
                lib_name = lib.get("media_name") or lib.get("name", "")
                lib_paths = path_map.get(lib_id) or []
                lib_path = ",".join(lib_paths)
                lib_type = lib.get("media_lib_type", "")
                if lib_type == "movies":
                    library_type = MediaType.MOVIE.value
                elif lib_type == "tv":
                    library_type = MediaType.TV.value
                elif lib_type:
                    library_type = lib_type
                else:
                    # 条目录上带 type 时以条目为准（最准），否则按名称/路径推断
                    library_type = self._infer_library_type(lib_name, lib_paths)
                # 生成库跳转链接（绿联 Web 端根地址，深链会失效）
                lib_link = (self._play_host or self._host or "").rstrip("/") + "/"
                libraries.append({
                    "id": lib_id,
                    "name": lib_name,
                    "type": library_type,
                    "path": lib_path,
                    # v6.3.7：库封面。此前这里**没有 image**，模板
                    # {% if Library.image %} 判空后回落到 Plex 专用组件 ⇒ 卡片黑图。
                    "image": self._image_url_by_path(
                        self._library_cover_path(lib)),
                    "link": lib_link,
                })
                log.info(f"【{self.client_name}】发现媒体库：id={lib_id}, name={lib_name}, type={library_type}, path={lib_path}")
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
        :param parent: 媒体库ID (media_lib_set_id)
        """
        if not self._api:
            return []
        try:
            lib_id = str(parent)
            lib_name = lib_id
            for lib in (self._api.media_list() or []):
                if str(lib.get("media_lib_set_id") or lib.get("id")) == lib_id:
                    lib_name = lib.get("media_name") or lib.get("name") or lib_id
                    break
            # v6.3.5 主通道：全量条目 + 本地按 media_lib_set_id 分桶
            videos = [v for v in (self._all_library_videos() or [])
                      if isinstance(v, dict)
                      and str(v.get("media_lib_set_id")) == lib_id]
            if not videos:
                # 兜底：老固件条目可能不带 media_lib_set_id，退回目录树 BFS
                log.warn(f"【{self.client_name}】get_items: 库 {lib_name}(id={lib_id}) "
                         f"在全量条目里没有命中，改用目录树遍历兜底")
                videos = self._walk_library_videos(lib_id)
            log.info(f"【{self.client_name}】get_items: 媒体库 {lib_name}(id={lib_id}) "
                     f"候选原始条目 {len(videos)} 条")
            ret_items = []
            seen_ids = set()
            fallback_count = 0
            for video in videos:
                if not isinstance(video, dict):
                    continue
                # 先摊平：内层 video_info 与外层条目合成一份（外层优先）
                info = self._flatten_item(video)
                # type 定义：1=电影 2=剧集（两种固件结构都兼容）
                video_type = info.get("type", 0)
                if video_type not in [1, 2]:
                    continue
                item_id = info.get("ug_video_info_id") or info.get("id")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                # 关键字段缺失时回源详情接口并重新摊平（新固件字段齐全则完全不请求）
                if not ((info.get("name") or info.get("title"))
                        and (info.get("poster_path") or info.get("poster"))
                        and info.get("tmdb_id")):
                    detail = self._video_info_cached(item_id) or {}
                    if detail:
                        fallback_count += 1
                        info = self._flatten_item(video, detail)
                name = info.get("name") or info.get("title") or ""
                item_type = MediaType.MOVIE.value if video_type == 1 else MediaType.TV.value
                file_path = info.get("file_path")
                if isinstance(file_path, list) and file_path:
                    item_path = str(file_path[0])
                elif isinstance(file_path, str) and file_path:
                    item_path = file_path
                else:
                    item_path = str(info.get("path") or "")
                play_url = self._build_play_url(
                    item_id, video_type, info.get("media_lib_set_id") or lib_id)
                ret_items.append({
                    "id": item_id,
                    "library": info.get("media_lib_set_id") or lib_id,
                    "type": item_type,
                    "title": name,
                    "originalTitle": info.get("original_name")
                    or info.get("original_title") or "",
                    "year": str(info.get("year") or info.get("release_year") or ""),
                    "tmdbid": info.get("tmdb_id"),
                    "imdbid": info.get("imdb_id"),
                    "path": item_path,
                    "json": str(info),
                    "image": self.get_local_image_by_id(
                        item_id, remote=False, inner=True, video_info=info),
                    "link": f"/open?url={quote(play_url)}&type=ugreen",
                })
            if fallback_count:
                log.info(f"【{self.client_name}】get_items: 媒体库 {lib_name} 有 "
                         f"{fallback_count} 条原始条目缺字段，已回源 video_info 补齐")
            log.info(f"【{self.client_name}】get_items: 媒体库 {lib_name} 收集到 "
                     f"{len(ret_items)} 个条目")
            return ret_items
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体列表出错：" + str(e))
            return []

    def get_play_url(self, item_id, item_info=None):
        """
        获取播放地址
        :param item_info: 可选的已缓存 item_info 数据，避免重复 video_info 请求
        """
        if not self._api or not item_id:
            return ""
        try:
            play_data = self._api.get_play_url(item_id)
            if play_data:
                url = play_data.get("play_url") or play_data.get("url") or ""
                if url:
                    return url
            info = item_info
            if info is None:
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
                vi = self._flatten_item(item)
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
                vi = self._flatten_item(item)
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
