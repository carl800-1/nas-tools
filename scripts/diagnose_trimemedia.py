#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞牛影视连接诊断工具（独立实现，不依赖 NAStool 自身代码）

用途：当 NAStool 里「飞牛影视」测试连接失败、媒体库列表为空时，用本脚本在
**同一个容器 / 同一台机器**上逐步打真实接口，把每一步的 HTTP 状态码、Content-Type
与响应片段打出来，从而定位：

    · 地址是否可达（DNS / 端口 / 路由 / 反代）
    · 路径是否需要 /v 后缀
    · 访问码是否正确
    · 账号密码是否被接受
    · 媒体库列表接口到底返回了什么

用法（容器内，推荐）：
    docker exec -it nas-tools python /nas-tools/scripts/diagnose_trimemedia.py

    # 也可显式传参（覆盖配置文件里的值）
    docker exec -it nas-tools python /nas-tools/scripts/diagnose_trimemedia.py \\
        --host http://192.168.3.3:5666 --username admin --password 'xxx'

也支持直接在 NAS 上运行（需 pip install requests pyyaml）。
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import requests
except ImportError:
    print("缺少 requests，请先执行：pip install requests")
    sys.exit(2)

# 与 MoviePilot 上游 / NAStool 实现完全一致的常量
API_KEY = "16CCEB3D-AB42-077D-36A1-F355324E4237"
AUTH_SALT = "NDzZTVxnRKP8Z0jXg1VAMonaG8akvh"

TRY_PORTS = (5666,)


def out(msg=""):
    print(msg)
    sys.stdout.flush()


def hr(title):
    out("")
    out("=" * 72)
    out(title)
    out("=" * 72)


def load_conf(path):
    """从 config.yaml 读 trimemedia 段（尽力而为，读不到返回空 dict）"""
    if not path or not os.path.exists(path):
        return {}
    try:
        import yaml  # PyYAML
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("trimemedia") or {}
    except ImportError:
        pass
    except Exception as err:
        out("  ! 读取 %s 失败：%s" % (path, err))
        return {}
    try:
        from ruamel.yaml import YAML  # NAStool 自带
        y = YAML(typ="safe")
        with open(path, "r", encoding="utf-8") as f:
            data = y.load(f) or {}
        return data.get("trimemedia") or {}
    except Exception:
        return {}


def normalize(host):
    if not host:
        return ""
    host = str(host).strip()
    if not host.startswith("http"):
        host = "http://" + host
    p = urlsplit(host)
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), "", "")).rstrip("/")


def build_authx(api_path, body):
    if not api_path.startswith("/v"):
        api_path = "/v" + api_path
    nonce = str(random.randint(100000, 999999))
    ts = str(int(time.time() * 1000))
    data_hash = hashlib.md5((body or "").encode()).hexdigest()
    sign = hashlib.md5("_".join(
        [AUTH_SALT, api_path, nonce, ts, data_hash, API_KEY]).encode()).hexdigest()
    return "nonce=%s&timestamp=%s&sign=%s" % (nonce, ts, sign)


class Probe:
    def __init__(self, verify_ssl=True):
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": "NAStool-TrimeMedia-Diagnose/1.0"})
        self.verify = verify_ssl
        self.token = None
        self.last = None

    def raw(self, url, method="GET", headers=None, data=None, params=None):
        try:
            r = self.sess.request(method, url, headers=headers or {}, data=data,
                                  params=params, timeout=10, verify=self.verify,
                                  allow_redirects=True)
        except Exception as err:
            out("  ✗ 请求异常：%s %s" % (type(err).__name__, err))
            return None
        ct = r.headers.get("Content-Type", "")
        snippet = (r.text or "").replace("\n", " ")[:300]
        flag = "✓" if r.ok else "✗"
        out("  %s %s %s -> HTTP %s  %s" % (flag, method, url, r.status_code, ct))
        out("      响应：%s" % snippet)
        return r

    def api(self, base, api, method="GET", data=None, params=None,
            base_path="/api/v1", with_token=False, quiet=False):
        api_path = (base_path + api) if api.startswith("/") else (base_path + "/" + api)
        url = base + api_path
        if method != "GET":
            body = json.dumps(data, allow_nan=False) if data else ""
        else:
            body = None
        headers = {"Accept": "application/json", "Referer": base,
                   "authx": build_authx(api_path, body)}
        if with_token and self.token:
            headers["Authorization"] = self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        r = self.raw(url, method, headers, body, params)
        if r is None:
            return None
        try:
            payload = r.json()
        except Exception:
            out("      ! 响应不是 JSON（访问码页 / 反代 HTML / 路由错误都会这样）")
            return None
        if not quiet:
            out("      code=%s msg=%s" % (payload.get("code"), payload.get("msg")))
        return payload


def check_code(base, access_code, verify_ssl):
    """校验访问码（未配置则跳过）"""
    if not access_code:
        out("  · 未配置访问码，跳过")
        return True
    root = base[:-len("/v")] if base.endswith("/v") else base
    url = "%s/c/%s" % (root, quote(str(access_code), safe=""))
    p = Probe(verify_ssl)
    r = p.raw(url, "GET")
    if r is None:
        return False
    if r.status_code == 404:
        out("  ✗ 访问码校验失败（404）：请检查访问码是否正确")
        return False
    if not r.ok:
        out("  ✗ 访问码校验失败：HTTP %s" % r.status_code)
        return False
    out("  ✓ 访问码校验通过")
    return True


def _split_host_port(base):
    """把 http(s)://host:port/v 拆成 (scheme, hostname, port, origin, path)"""
    p = urlsplit(base)
    scheme = (p.scheme or "http").lower()
    hostname = p.hostname or ""
    port = p.port or (443 if scheme == "https" else 80)
    path = p.path.rstrip("/")
    if path.endswith("/v"):
        path = path[:-2]
    origin = "%s://%s" % (scheme, p.netloc or hostname)
    return scheme, hostname, port, origin, path


def _cert_field(cert, name):
    items = cert.get(name) or []
    return ", ".join("%s=%s" % (k, v) for item in items for k, v in item) or "(空)"


def preflight(base, verify_ssl):
    """
    连接前置体检：DNS → TCP → TLS → 反向代理识别。

    目的是把「还没走到协议层就已经失败」的情况单独摘出来讲清楚：
    这类问题改代码是改不好的，只能改地址 / 网络 / 代理配置。
    返回 True 表示可以继续做接口探测。
    """
    import socket
    import ssl as _ssl

    scheme, hostname, port, origin, path = _split_host_port(base)
    out("")
    out("[0/5] 连接前置体检（DNS / TCP / TLS / 反向代理）")

    # —— 0.1 DNS ——
    out("  · DNS：解析 %s" % hostname)
    addrs = []
    try:
        for info in socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP):
            addr = info[4][0]
            if addr not in addrs:
                addrs.append(addr)
        out("      ✓ 解析到：%s" % ", ".join(addrs))
    except Exception as err:
        out("      ✗ DNS 解析失败：%s %s" % (type(err).__name__, err))
        out("      → 容器内解析不了这个主机名。请改用 IP 地址，或给容器配 DNS / 写 hosts。")
        return False

    # —— 0.2 TCP ——
    out("  · TCP：连接 %s:%s" % (hostname, port))
    t0 = time.time()
    try:
        socket.create_connection((hostname, port), timeout=5).close()
        out("      ✓ 端口连通，耗时 %.0f ms" % ((time.time() - t0) * 1000))
    except Exception as err:
        out("      ✗ 端口不通：%s %s（耗时 %.0f ms）"
            % (type(err).__name__, err, (time.time() - t0) * 1000))
        out("      → 端口写错 / 服务没起 / 容器与飞牛不在同一网络或被防火墙挡住。")
        out("        容器内可先试：ping %s" % hostname)
        return False

    # —— 0.3 TLS ——
    if scheme != "https":
        out("  · TLS：地址是 http，跳过")
    else:
        out("  · TLS：握手并读取服务端证书")
        try:
            raw = socket.create_connection((hostname, port), timeout=5)
            tls = _ssl._create_unverified_context().wrap_socket(raw, server_hostname=hostname)
            cert = tls.getpeercert()
            tls.close()
        except Exception as err:
            out("      ✗ TLS 握手失败：%s %s" % (type(err).__name__, err))
            out("      → 服务端可能不是 https，或端口/协议不匹配，请改用 http 试试。")
            return False
        subject = _cert_field(cert, "subject")
        issuer = _cert_field(cert, "issuer")
        out("      ✓ 握手成功")
        out("        证书主体：%s" % subject)
        out("        签发者　：%s" % issuer)
        out("        有效期　：%s ~ %s" % (cert.get("notBefore"), cert.get("notAfter")))
        if subject != "(空)" and subject == issuer:
            out("      ! 自签名证书 ⇒ 请在设置里关闭「校验SSL证书」，或加 --no-ssl-verify")
        if verify_ssl:
            try:
                csock = socket.create_connection((hostname, port), timeout=5)
                _ssl.create_default_context().wrap_socket(csock, server_hostname=hostname).close()
                out("      ✓ 证书校验通过")
            except Exception as err:
                out("      ✗ 证书校验失败：%s" % err)
                out("      → 自签名 / 主机名不匹配 ⇒ 不关掉证书校验，后面每个接口都会 SSLError。")
        else:
            out("      · 已关闭证书校验，跳过")

    # —— 0.4 反向代理 / 网关识别 ——
    out("  · 反向代理：不带签名裸探根路径与 /v/ 路径")
    sess = requests.Session()
    sess.headers.update({"User-Agent": "NAStool-TrimeMedia-Diagnose/1.0"})
    for url in (origin + path + "/", origin + path + "/v/"):
        try:
            r = sess.get(url, timeout=8, verify=verify_ssl, allow_redirects=False)
        except Exception as err:
            out("      · %s -> 请求异常：%s" % (url, type(err).__name__))
            continue
        ct = (r.headers.get("Content-Type") or "").lower()
        kind = "HTML" if "html" in ct else (ct or "-")
        loc = r.headers.get("Location")
        out("      · %s -> HTTP %s  Server=%s  %s%s"
            % (url, r.status_code, r.headers.get("Server") or "-", kind,
               ("  Location=" + loc) if loc else ""))
        if r.status_code in (502, 503, 504):
            out("        → 反向代理活着但后端不可达，检查代理转发目标。")
        elif r.status_code in (301, 302, 303, 307, 308) and loc:
            out("        → 被重定向（多半是登录页 / 网关），确认该地址就是你平时浏览器访问飞牛的入口。")
        elif r.status_code in (401, 403):
            out("        → 网关层就要求鉴权（反向代理的账号密码、或 IP 白名单）。")
        elif r.status_code == 404:
            out("        → 路径不匹配，确认该地址后面没有多写/少写子路径。")
    out("  ✓ 前置体检通过，继续做接口探测")
    return True


def probe_candidate(raw_host, access_code, username, password, verify_ssl):
    """对单个候选地址做一遍完整探测，返回 (是否成功, 诊断小结)"""
    base = normalize(raw_host)
    out("")
    out("-" * 72)
    out("► 候选地址：%s" % base)
    out("-" * 72)

    p = Probe(verify_ssl)

    # 1) 版本接口（最先探，不需要登录）
    out("[1/5] 探版本接口 /sys/version（含 /v 路径与 authx 签名校验）")
    ver = p.api(base, "/sys/version")
    if not ver or ver.get("code") != 0:
        out("      ✗ 版本接口不通 —— 地址/端口/路径/签名 之一有问题，本候选地址不可用")
        return False
    data = ver.get("data") or {}
    out("      ✓ 飞牛影视版本：%s，服务版本：%s"
        % (data.get("version"), data.get("mediasrvVersion")))

    # 2) 访问码
    out("[2/5] 校验访问码")
    if not check_code(base, access_code, verify_ssl):
        return False

    # 3) 登录
    out("[3/5] 登录（优先 v2 摘要登录，失败回退 v1 明文）")
    pwd_hash = hashlib.sha256(str(password).encode()).hexdigest()
    login = p.api(base, "/user/loginByPassword", method="POST",
                  data={"username": username, "password": pwd_hash,
                        "app_name": "trimemedia-web"},
                  base_path="/api/v2")
    token = None
    if login and login.get("code") == 0:
        token = (login.get("data") or {}).get("token")
    else:
        out("      · v2 未成功，回退 v1 /login（旧版服务端）")
        login = p.api(base, "/login", method="POST",
                      data={"username": username, "password": password,
                            "app_name": "trimemedia-web"})
        if login and login.get("code") == 0:
            token = (login.get("data") or {}).get("token")
    if not token:
        out("      ✗ 登录失败：账号或密码不正确（请用飞牛影视里能正常登录的账号）")
        return False
    p.token = token
    out("      ✓ 登录成功，token = %s..." % token[:12])

    # 4) 用户信息（决定走管理员还是普通用户接口）
    out("[4/5] 取当前用户 /user/info")
    info = p.api(base, "/user/info", with_token=True)
    if not info or info.get("code") != 0:
        out("      ✗ 取用户信息失败 —— token 未被服务端接受")
        return False
    udata = info.get("data") or {}
    is_admin = int(udata.get("is_admin") or 0)
    out("      ✓ 用户：%s，is_admin=%s" % (udata.get("username"), is_admin))

    # 5) 媒体库列表
    out("[5/5] 取媒体库列表（%s）" % ("/mdb/list 管理员" if is_admin == 1 else "/mediadb/list 普通用户"))
    libs = None
    if is_admin == 1:
        r = p.api(base, "/mdb/list", with_token=True)
        libs = (r or {}).get("data")
    if not libs:
        r = p.api(base, "/mediadb/list", with_token=True)
        if r and r.get("code") == 0 and r.get("data"):
            libs = r.get("data")
    if not libs:
        out("      ✗ 媒体库列表为空 —— 该账号在飞牛影视里没有可见媒体库，"
            "或媒体库尚未扫描入库")
        return False
    out("      ✓ 共 %d 个媒体库：" % len(libs))
    for lib in libs:
        if isinstance(lib, dict):
            out("         - guid=%s  category=%s  name=%s"
                % (lib.get("guid"), lib.get("category"),
                   lib.get("name") or lib.get("title")))

    out("")
    out("► 该地址可用：%s" % base)
    return True


def main():
    ap = argparse.ArgumentParser(description="飞牛影视连接诊断")
    ap.add_argument("--host", help="服务端地址，如 http://192.168.3.3:5666")
    ap.add_argument("--username")
    ap.add_argument("--password")
    ap.add_argument("--access-code", dest="access_code")
    ap.add_argument("--no-ssl-verify", action="store_true", help="跳过 HTTPS 证书校验")
    ap.add_argument("--config", default=None, help="config.yaml 路径")
    ap.add_argument("--no-preflight", action="store_true",
                    help="跳过 DNS/TCP/TLS/反代 前置体检，直接做接口探测")
    args = ap.parse_args()

    conf_paths = [args.config] if args.config else \
        ["/config/config.yaml", "config/config.yaml", "./config.yaml"]
    conf = {}
    used_path = None
    for cp in conf_paths:
        c = load_conf(cp)
        if c:
            conf = c
            used_path = cp
            break

    host = args.host or conf.get("host")
    username = args.username or conf.get("username")
    password = args.password or conf.get("password")
    access_code = args.access_code if args.access_code is not None else conf.get("access_code")
    verify_ssl = not args.no_ssl_verify
    if conf.get("ssl_verify") is not None and args.no_ssl_verify is False:
        v = conf.get("ssl_verify")
        if isinstance(v, str):
            verify_ssl = v.strip().lower() not in ("false", "0", "no", "off")
        elif v is not None:
            verify_ssl = bool(v)

    hr("飞牛影视连接诊断")
    out("配置文件      ：%s" % (used_path or "(未找到，使用命令行参数)"))
    out("服务端地址    ：%s" % host)
    out("用户名        ：%s" % username)
    out("密码          ：%s" % ("*" * len(str(password)) if password else "(空)"))
    out("访问码        ：%s" % ("已配置" if access_code else "(未配置)"))
    out("校验 SSL 证书 ：%s" % verify_ssl)

    if not host or not username or not password:
        out("")
        out("✗ 配置不完整：地址 / 用户名 / 密码 必须齐全。")
        out("  请在 NAStool「设置 → 媒体服务器 → 飞牛影视」里填好后**先点保存**，再跑本脚本；")
        out("  或显式传参：--host ... --username ... --password ...")
        return 2

    raw = str(host).strip()
    if not raw.startswith("http"):
        raw = "http://" + raw
    base = normalize(raw)
    if base.endswith("/v"):
        candidates = [base]
    else:
        candidates = [base + "/v", base]

    hr("开始探测（共 %d 个候选地址）" % len(candidates))
    ok_base = None
    for cand in candidates:
        try:
            if not args.no_preflight and not preflight(cand, verify_ssl):
                out("")
                out("► 前置体检未通过，跳过接口探测：%s" % normalize(cand))
                continue
            if probe_candidate(cand, access_code, username, password, verify_ssl):
                ok_base = normalize(cand)
                break
        except Exception as err:
            out("  ✗ 探测过程异常：%s %s" % (type(err).__name__, err))

    hr("结论")
    if ok_base:
        out("✓ 连接正常，可用地址 = %s" % ok_base)
        out("  若 NAStool 里仍报连接失败：")
        out("    1) 确认容器里跑的是最新镜像（设置 → 关于 查看版本号）")
        out("    2) 确认「设置 → 媒体服务器」里选中了飞牛影视并已保存")
        return 0

    out("✗ 全部候选地址均无法完成探测。请按上面每一步的 HTTP 状态码定位：")
    out("  · 前置体检（[0/5]）就已失败")
    out("      → 属于网络/地址/证书问题，改代码无用：看那一小节给的具体提示")
    out("  · 连接被拒绝 / 超时 (ConnectionError/Timeout)")
    out("      → 地址或端口不对，或 NAStool 容器与飞牛不在同一网络（容器内试 ping）")
    out("  · HTTP 404 且响应是 HTML")
    out("      → 地址缺 /v 或多了错误路径；也检查是否被反向代理改写")
    out("  · HTTP 401/403")
    out("      → 开了访问码没填，或账号密码不对")
    out("  · SSLError / certificate verify failed")
    out("      → 用 https 自签证书，请在设置里关闭「校验SSL证书」或加 --no-ssl-verify")
    out("  · 版本接口 code!=0")
    out("      → 签名或 /v 路径不匹配，把上面完整响应贴出来")
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
