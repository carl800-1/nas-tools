# -*- coding: utf-8 -*-
"""
站点体检（L0–L6）
=================

为什么需要它
------------
现有的「站点连通性测试」（Sites.test_connection）只回答一个问题：
**站点首页能不能打开、Cookie 还在不在**。它回答不了下面这些真正让人抓瞎的情况：

  · 首页打得开，但搜索页被单独拦（搜索引擎触发反爬）
  · 首页 200 且判定「已登录」，但搜索请求返回 403 / Cloudflare 挑战页
  · 页面能取回，站点改版导致解析规则失效 —— 表现同样是「未搜索到数据」
  · 站点在索引器列表里根本不存在（内置规则缺失 / 域名对不上）
  · 站点被停用、没设代理、没配 UA

于是「按片名搜不到资源」时，用户无从判断问题出在网络、Cookie、反爬、规则
还是站点真的没这个资源。本模块把这件事拆成 7 层，逐层给出**可归因**的结论
与**下一步该做什么**。

分层含义
--------
L0 配置完整性   站点地址 / Cookie / UA / 代理 / 启用状态 / 索引器定义是否存在
L1 网络可达     DNS 解析 + TCP 连接（直连探测；站点配了代理时只作参考）
L2 HTTP 语义    状态码、最终 URL、耗时、内容长度、Server / CF-Ray 等关键响应头
L3 反爬识别     Cloudflare / WAF / 挑战页
L4 登录态       是否被重定向到登录页、页面是否含登录表单
L5 搜索可达     带探针关键词真实走一次搜索链路（不是只访问首页）
L6 选择器命中率 探针搜索解析出多少条、关键字段（标题/下载链接/大小/做种数）完整度

设计约定
--------
1. L3 / L4 的判定与索引器搜索**共用**同一份实现
   （app/indexer/client/_spider.py::classify_page_state），
   保证「搜索时看到的结论」与「体检时看到的结论」不会互相矛盾。
2. L5 / L6 的抓取链路与索引器搜索**共用**同一份实现
   （app/indexer/client/builtin.py::spider_search），不复制等待/超时逻辑。
3. 结论只指向**最底层的那个失败项**：L1 网络不通时，不必去猜 L6 选择器 ——
   从最底层开始修，修一层看一层。
"""

import socket
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

import log
from app.helper import SiteHelper
from app.sites import Sites
from app.utils import RequestUtils, StringUtils
from config import Config

# ---------------------------------------------------------------------------
# 层级与状态
# ---------------------------------------------------------------------------

LEVEL_NAMES = {
    "L0": "配置完整性",
    "L0b": "索引器定义",
    "L1": "网络可达",
    "L2": "HTTP 语义",
    "L3": "反爬识别",
    "L4": "登录态",
    "L5": "搜索可达",
    "L6": "选择器命中率",
}
LEVEL_ORDER = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"

STATUS_TEXT = {
    STATUS_PASS: "正常",
    STATUS_WARN: "注意",
    STATUS_FAIL: "异常",
    STATUS_SKIP: "跳过",
}

# 严重程度：越大越严重。skip 不参与「最差层级」的评比（它是「没测」，不是「有问题」）
_STATUS_WEIGHT = {STATUS_SKIP: 0, STATUS_PASS: 1, STATUS_WARN: 2, STATUS_FAIL: 3}

# 探针搜索默认关键词：一定要选一个任何站点几乎必然有结果的词，
# 否则「0 条」无法区分「站点/规则有问题」与「站点真的没这个词的资源」
DEFAULT_PROBE_KEYWORD = "1080p"

# L6 字段完整度检查项：(字段名, 中文名, 是否把 0 也算缺失)
_FIELD_SPECS = (
    ("title", "标题", False),
    ("enclosure", "下载链接", False),
    ("size", "大小", True),
    ("seeders", "做种数", False),
    ("pubdate", "发布时间", False),
)

_HTTP_TIMEOUT = 20
_TCP_TIMEOUT = 5


# ---------------------------------------------------------------------------
# 纯函数（可离线验证）
# ---------------------------------------------------------------------------

def new_level(code, status, summary, detail="", items=None, suggestion=""):
    """
    构造一层体检结果

    :param code: 层级代码，如 "L2"
    :param status: pass / warn / fail / skip
    :param summary: 一句话结论
    :param detail: 补充说明（通常是原始错误信息）
    :param items: 明细列表 [{"key":..., "value":..., "ok":True/False/None}]
    :param suggestion: 该层不通时建议怎么处理
    :return: 层级结果字典
    """
    return {
        "code": code,
        "name": LEVEL_NAMES.get(code, code),
        "status": status,
        "status_text": STATUS_TEXT.get(status, status),
        "summary": summary,
        "detail": detail or "",
        "items": list(items or []),
        "suggestion": suggestion or "",
    }


def worst_status(levels):
    """
    取一组层级结果里最严重的状态（skip 视为「没测」，不参与评比）

    :param levels: 层级结果列表
    :return: 最严重状态；全是 skip 时返回 skip
    """
    worst = STATUS_SKIP
    for lv in levels or []:
        st = (lv or {}).get("status") or STATUS_SKIP
        if _STATUS_WEIGHT.get(st, 0) > _STATUS_WEIGHT.get(worst, 0):
            worst = st
    return worst


def build_conclusion(levels, site_name=""):
    """
    由各层结果汇总出「结论 + 下一步建议」

    原则：只指向**最底层的那个失败项**。L1 网络都不通时去提示「选择器可能失效」
    是误导 —— 用户会去改规则，而真正的问题是连不上站。

    :param levels: 层级结果列表（按 L0→L6 顺序）
    :param site_name: 站点名，用于措辞
    :return: (结论字符串, 建议字符串, 最关键层级代码或 None)
    """
    levels = list(levels or [])
    name = site_name or "该站点"

    # 找第一个 fail（按层级从低到高）
    first_fail = None
    for lv in levels:
        if lv.get("status") == STATUS_FAIL:
            first_fail = lv
            break

    if first_fail:
        suggestion = first_fail.get("suggestion") or "请按上方明细逐步排查。"
        return ("%s 体检未通过：%s（%s 层）—— %s"
                % (name, first_fail.get("name"), first_fail.get("code"),
                   first_fail.get("summary")),
                suggestion,
                first_fail.get("code"))

    # 没有 fail，看有没有 warn
    warns = [lv for lv in levels if lv.get("status") == STATUS_WARN]
    if warns:
        detail = "；".join("%s：%s" % (lv.get("name"), lv.get("summary")) for lv in warns)
        return ("%s 体检基本可用，但有 %d 处需要注意 —— %s" % (name, len(warns), detail),
                warns[0].get("suggestion") or "留意上方「注意」项，通常不影响可用性。",
                warns[0].get("code"))

    tested = [lv for lv in levels if lv.get("status") != STATUS_SKIP]
    if not tested:
        return ("%s 未完成任何体检项，无法给出结论。" % name,
                "请检查站点配置是否完整（地址、Cookie）。", None)

    return ("%s 体检全部通过，搜索链路健康。" % name,
            "无需处理。若仍搜不到某个片名，问题通常不在站点侧，"
            "而在「搜索用的名称/判定条件」，可到「记录查询」里看该次搜索的"
            "「不匹配」明细（名称脏 / TMDB无条目 / ID不符 / 季集年不符）。",
            None)


def split_url(url):
    """
    拆出 scheme / host / port

    :param url: 任意站点地址（可以不带 scheme）
    :return: (scheme, host, port)；无法解析时返回 (None, None, None)
    """
    if not url:
        return None, None, None
    text = str(url).strip()
    if not text:
        return None, None, None
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urlparse(text)
    except Exception:
        return None, None, None
    host = parsed.hostname
    if not host:
        return None, None, None
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def measure_fields(items):
    """
    统计解析出来的种子字段完整度

    体检不能只看「解析出几条」，还要看**解析出来的东西能不能用**：
    标题缺失的记录在判定层会被直接当错误丢掉（index_error），
    下载链接缺失的记录即便匹配上也无法下载。

    :param items: 解析出来的种子字典列表
    :return: 明细列表 [{"key","label","ok","missing","total","rate"}]
    """
    items = [it for it in (items or []) if isinstance(it, dict)]
    total = len(items)
    result = []
    for key, label, zero_is_missing in _FIELD_SPECS:
        missing = 0
        for it in items:
            val = it.get(key)
            if val is None or val == "":
                missing += 1
            elif zero_is_missing and not val:
                missing += 1
        ok = total - missing
        result.append({
            "key": key,
            "label": label,
            "ok": ok,
            "missing": missing,
            "total": total,
            "rate": round(ok * 100.0 / total, 1) if total else 0.0,
        })
    return result


def probe_tcp(host, port=443, timeout=_TCP_TIMEOUT):
    """
    TCP 连通性探测（DNS 解析 + 建连）

    单独做这一层是为了把「网络不通」与「HTTP 返回异常」分开：
    前者属于环境/域名/代理问题，后者属于站点侧问题，处理方式完全不同。

    :param host: 主机名
    :param port: 端口
    :param timeout: 超时秒数
    :return: {"dns_ok","tcp_ok","ip","ms","error"}
    """
    result = {"dns_ok": False, "tcp_ok": False, "ip": None, "ms": 0, "error": ""}
    if not host:
        result["error"] = "站点地址为空"
        return result
    start = time.time()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as err:
        result["ms"] = int((time.time() - start) * 1000)
        result["error"] = "DNS 解析失败：%s" % err
        return result
    except Exception as err:
        result["ms"] = int((time.time() - start) * 1000)
        result["error"] = "DNS 解析异常：%s" % err
        return result

    result["dns_ok"] = True
    last_err = None
    for family, socktype, proto, _canon, sockaddr in infos:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            result["tcp_ok"] = True
            result["ip"] = sockaddr[0]
            break
        except OSError as err:
            last_err = err
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    result["ms"] = int((time.time() - start) * 1000)
    if not result["tcp_ok"]:
        result["error"] = "TCP 连接失败：%s" % (last_err if last_err else "无可用地址")
    return result


def classify_http_error(err):
    """
    把 requests 异常归类成便于行动的中文说明

    :param err: 异常对象
    :return: (归类名, 中文说明)
    """
    text = "%s %s" % (type(err).__name__, err)
    low = text.lower()
    if "timeout" in low or "timed out" in low:
        return "timeout", "连接超时（站点不可达、被墙或需要代理）"
    if "sslerror" in low or "ssl" in low and "certificate" in low:
        return "ssl", "TLS/证书错误（可能是中间设备拦截或证书过期）"
    if "connectionreset" in low or "reset by peer" in low:
        return "reset", "连接被重置（通常是被网络中间设备拦截）"
    if "connectionrefused" in low or "refused" in low:
        return "refused", "连接被拒绝（域名或端口不可用）"
    if "nameresolution" in low or "gaierror" in low or "getaddrinfo" in low \
            or "name or service not known" in low or "nodename nor servname" in low:
        return "dns", "域名解析失败（DNS 不可用）"
    if "toomanyredirects" in low or "too many redirects" in low:
        return "redirect", "重定向次数过多（可能被反爬循环跳转）"
    return "unknown", "请求失败：%s" % err


# ---------------------------------------------------------------------------
# 体检主体
# ---------------------------------------------------------------------------

class SiteHealth:
    """
    站点体检

    用法：
        report = SiteHealth().check(site_id=3, keyword="沙丘")
    返回结构化报告，前端按 L0–L6 渲染即可。
    """

    def check(self, site_id, keyword=None, timeout=_HTTP_TIMEOUT):
        """
        对单个站点做 L0–L6 分层体检

        :param site_id: 站点编号（Sites 配置里的 id）
        :param keyword: 探针搜索用的关键词；不传则用 DEFAULT_PROBE_KEYWORD
        :return: 报告字典
        """
        started = datetime.now()
        probe_keyword = (keyword or "").strip() or DEFAULT_PROBE_KEYWORD
        levels = []

        site_info = Sites().get_sites(siteid=site_id) or {}
        if not site_info:
            levels.append(new_level(
                "L0", STATUS_FAIL, "站点不存在（编号 %s 在站点配置里查不到）" % site_id,
                suggestion="请到「站点管理」确认该站点是否已被删除。"))
            return self.__build_report({}, levels, probe_keyword, started)

        site_name = site_info.get("name") or ("站点%s" % site_id)
        site_url = StringUtils.get_base_url(
            site_info.get("signurl") or site_info.get("rssurl"))
        log.info("【站点体检】开始体检 %s（%s），探针关键词「%s」"
                 % (site_name, site_url or "无地址", probe_keyword))

        # ---------------- L0 配置完整性 ----------------
        levels.append(self.__check_config(site_info, site_url))
        l0 = levels[-1]
        if l0.get("status") == STATUS_FAIL:
            # 地址/Cookie 都没有，后续层无从谈起，直接给出报告
            for code in ("L1", "L2", "L3", "L4", "L5", "L6"):
                levels.append(new_level(code, STATUS_SKIP, "因 L0 未通过而跳过"))
            return self.__build_report(site_info, levels, probe_keyword, started)

        # 索引器定义（内置规则 or 插件索引器）—— 归在 L0b，紧跟 L0 之后渲染
        indexer, indexer_note = self.__get_indexer(site_info, site_url)
        if indexer is None:
            levels.append(new_level(
                "L0b", STATUS_FAIL, "找不到该站点的索引器定义",
                detail=indexer_note,
                suggestion="内置索引器里没有匹配该域名的规则："
                           "请在「索引器」页面确认站点域名是否正确、"
                           "或该站点是否应由 Jackett / Prowlarr 等插件索引器提供。"))
        else:
            levels.append(new_level(
                "L0b", STATUS_PASS, "已找到索引器定义（抓取通道：%s）"
                                    % (getattr(indexer, "parser", None) or "内置抓取")))

        # ---------------- L1 网络可达 ----------------
        levels.append(self.__check_network(site_info, site_url))

        # ---------------- L2/L3/L4 HTTP + 反爬 + 登录态 ----------------
        http_levels = self.__check_http(site_info, site_url, indexer, timeout)
        levels.extend(http_levels)

        # ---------------- L5/L6 探针搜索 ----------------
        if indexer is not None:
            levels.extend(self.__check_search(site_info, indexer, probe_keyword))
        else:
            for code in ("L5", "L6"):
                levels.append(new_level(code, STATUS_SKIP, "缺少索引器定义，无法进行探针搜索"))

        report = self.__build_report(site_info, levels, probe_keyword, started)
        log.info("【站点体检】%s 完成，结论：%s" % (site_name, report.get("conclusion")))
        return report

    # ------------------------------------------------------------------
    # L0
    # ------------------------------------------------------------------
    @staticmethod
    def __check_config(site_info, site_url):
        items = []

        def add(key, value, ok):
            items.append({"key": key, "value": value, "ok": ok})

        has_url = bool(site_url)
        add("站点地址", site_url or "（未配置）", has_url)

        has_cookie = bool(site_info.get("cookie"))
        add("Cookie", "已配置" if has_cookie else "（未配置）", has_cookie)

        add("UA", site_info.get("ua") or "（用全局默认 UA）", None)

        proxy_on = bool(site_info.get("proxy"))
        add("代理", "已启用" if proxy_on else "未启用", None)

        add("浏览器渲染", "已启用" if site_info.get("chrome") else "未启用", None)

        rss_on = bool(site_info.get("rss_enable"))
        add("参与 RSS", "是" if rss_on else "否", None)

        add("RSS 地址", site_info.get("rssurl") or "（未配置）", None)
        add("签到地址", site_info.get("signurl") or "（未配置）", None)

        if not has_url:
            return new_level("L0", STATUS_FAIL, "站点地址为空，无法体检",
                             items=items,
                             suggestion="请到「站点管理」补全该站点的 RSS 或签到地址。")
        if not has_cookie:
            return new_level("L0", STATUS_FAIL, "站点未配置 Cookie，绝大多数站点必须登录才能搜索",
                             items=items,
                             suggestion="请到「站点管理」用「更新 Cookie」重新获取并保存 Cookie。")
        return new_level("L0", STATUS_PASS, "地址与 Cookie 已配置", items=items)

    # ------------------------------------------------------------------
    # 索引器定义
    # ------------------------------------------------------------------
    @staticmethod
    def __get_indexer(site_info, site_url):
        """
        取该站点对应的索引器配置

        内置索引器按域名匹配；找不到时说明站点域名与内置规则对不上
        （常见于站点换了域名、或该站点本应由插件索引器提供）。

        :return: (IndexerConf 或 None, 说明文字)
        """
        if not site_url:
            return None, "站点地址为空"
        try:
            from web.backend.pro_user import ProUser
        except Exception as err:
            return None, "索引器模块不可用：%s" % err
        try:
            indexer = ProUser().get_indexer(url=site_url,
                                            siteid=site_info.get("id"),
                                            cookie=site_info.get("cookie"),
                                            ua=site_info.get("ua"),
                                            name=site_info.get("name"),
                                            rule=site_info.get("rule"),
                                            pri=site_info.get("pri"),
                                            public=False,
                                            proxy=site_info.get("proxy"),
                                            render=site_info.get("chrome"))
        except Exception as err:
            return None, "构造索引器配置失败：%s" % err
        if not indexer:
            return None, ("域名 %s 没有匹配到任何内置索引器规则"
                          % StringUtils.get_url_domain(site_url))
        return indexer, ""

    # ------------------------------------------------------------------
    # L1
    # ------------------------------------------------------------------
    def __check_network(self, site_info, site_url):
        scheme, host, port = split_url(site_url)
        use_proxy = bool(site_info.get("proxy"))
        probe = probe_tcp(host, port)
        items = [
            {"key": "主机", "value": host or "-", "ok": bool(host)},
            {"key": "端口", "value": port, "ok": None},
            {"key": "DNS", "value": (probe.get("ip") or "解析失败"), "ok": probe["dns_ok"]},
            {"key": "TCP", "value": "可达" if probe["tcp_ok"] else "不可达", "ok": probe["tcp_ok"]},
            {"key": "耗时", "value": "%s 毫秒" % probe["ms"], "ok": None},
        ]

        if probe["tcp_ok"]:
            return new_level("L1", STATUS_PASS, "DNS 与 TCP 连接正常", items=items)

        # 站点配了代理时，直连失败属于正常现象（流量本来就该走代理），只提示不判失败
        if use_proxy:
            return new_level(
                "L1", STATUS_WARN, "直连不可达（该站点已启用代理，属正常现象）",
                detail=probe["error"], items=items,
                suggestion="该站点配置了代理，体检的直连探测只作参考；"
                           "真正的连通性看 L2 的结果。若 L2 也失败，请检查代理是否可用。")
        return new_level(
            "L1", STATUS_FAIL, "网络不可达", detail=probe["error"], items=items,
            suggestion="该站点未启用代理。若你所在网络需要代理才能访问它，"
                       "请在「站点管理」中为它启用代理；"
                       "否则请确认域名是否已变更、DNS 是否正常。")

    # ------------------------------------------------------------------
    # L2 / L3 / L4
    # ------------------------------------------------------------------
    def __check_http(self, site_info, site_url, indexer, timeout):
        from app.indexer.client._spider import classify_page_state

        parser = getattr(indexer, "parser", None) if indexer is not None else None

        # M-Team 走 API 通道，页面文本判定不适用，改用专用连通性检测
        if parser == "MTeamSpider":
            return self.__check_mteam(site_info)

        ua = site_info.get("ua") or Config().get_ua()
        proxies = Config().get_proxies() if site_info.get("proxy") else None
        request_url = site_url
        # 个别站点根路径不返回内容，沿用连通性测试里的处理
        if "1ptba" in request_url or "zmpt" in request_url:
            request_url = request_url + "/index.php"

        # 这里直接用 requests 而不是 RequestUtils.get_res()：
        # get_res(raise_exception=True) 抛的是异常**类**而不是实例
        # （`raise requests.exceptions.RequestException`），原始错误信息
        # （超时 / DNS / 证书）会全部丢失，而体检恰恰要靠这些信息区分故障类型。
        start = time.time()
        res = None
        err = None
        try:
            res = requests.get(request_url,
                               verify=False,
                               headers={"User-Agent": ua},
                               proxies=proxies,
                               cookies=RequestUtils.cookie_parse(site_info.get("cookie")),
                               timeout=timeout,
                               allow_redirects=True)
        except Exception as ex:
            err = ex
        elapsed_ms = int((time.time() - start) * 1000)

        # ---------- 请求直接失败 ----------
        if res is None:
            kind, desc = classify_http_error(err) if err else ("unknown", "请求没有返回任何内容")
            items = [
                {"key": "请求地址", "value": request_url, "ok": None},
                {"key": "耗时", "value": "%s 毫秒" % elapsed_ms, "ok": None},
            ]
            # 结论里给中文说明（kind 是给程序看的英文归类码，不直接抛给用户）
            skip_detail = [{"key": "归类", "value": kind, "ok": False}]
            return [
                new_level("L2", STATUS_FAIL, "HTTP 请求失败：%s" % desc, detail=desc,
                          items=items + skip_detail,
                          suggestion="请先解决网络层问题：确认是否需要代理、"
                                     "域名是否仍然有效。可用 curl 手工验证同一地址。"),
                new_level("L3", STATUS_SKIP, "因 L2 失败而跳过"),
                new_level("L4", STATUS_SKIP, "因 L2 失败而跳过"),
            ]

        status = res.status_code
        final_url = str(res.url or "")
        headers = {str(k).lower(): str(v) for k, v in dict(res.headers or {}).items()}
        text = res.text or ""
        redirected = bool(final_url) and final_url.rstrip("/") != request_url.rstrip("/")

        # ---------- L2 ----------
        l2_items = [
            {"key": "状态码", "value": status, "ok": 200 <= status < 300},
            {"key": "最终地址", "value": final_url or request_url, "ok": None},
            {"key": "重定向", "value": "是" if redirected else "否", "ok": None},
            {"key": "耗时", "value": "%s 毫秒" % elapsed_ms, "ok": None},
            {"key": "内容长度", "value": "%s 字节" % len(text.encode("utf-8", "ignore")), "ok": None},
            {"key": "Server", "value": headers.get("server", "-"), "ok": None},
            {"key": "CF-Ray", "value": headers.get("cf-ray", "-"), "ok": None},
            {"key": "CF-Mitigated", "value": headers.get("cf-mitigated", "-"), "ok": None},
        ]
        if 200 <= status < 300:
            l2 = new_level("L2", STATUS_PASS, "HTTP %s，页面已取回" % status, items=l2_items)
        elif status in (401, 403):
            l2 = new_level("L2", STATUS_FAIL, "站点拒绝访问（HTTP %s）" % status, items=l2_items,
                           suggestion="HTTP 401/403 通常意味着 Cookie 失效或触发反爬。"
                                      "先更新 Cookie 重试；仍失败则考虑为该站点启用代理。")
        elif status == 429:
            l2 = new_level("L2", STATUS_FAIL, "触发站点限流（HTTP 429）", items=l2_items,
                           suggestion="该站点对请求频率做了限制，请降低搜索频率后再试。")
        elif status >= 500:
            l2 = new_level("L2", STATUS_FAIL, "站点服务异常（HTTP %s）" % status, items=l2_items,
                           suggestion="这是站点侧的问题，过一段时间再试。")
        else:
            l2 = new_level("L2", STATUS_WARN, "HTTP 状态码异常（%s）" % status, items=l2_items,
                           suggestion="确认该地址是否需要额外的路径（如 /index.php）。")

        # ---------- L3 / L4：与搜索共用同一判定 ----------
        state, desc = classify_page_state(text, status_code=status,
                                          final_url=final_url, headers=headers)

        if state == "CFBlocked":
            l3 = new_level("L3", STATUS_FAIL, "被 Cloudflare / 反爬拦截", detail=desc,
                           suggestion="该站点部署了 Cloudflare 挑战。"
                                      "可在「站点管理」中为该站点启用「浏览器渲染」"
                                      "（需要浏览器内核可用），或改用支持该站点的插件索引器。")
        else:
            l3 = new_level("L3", STATUS_PASS, "未检测到反爬挑战特征")

        if state == "needLogin":
            l4 = new_level("L4", STATUS_FAIL, "Cookie 已失效（页面表现为未登录）", detail=desc,
                           suggestion="请用「更新 Cookie」重新获取该站点的 Cookie 并保存。")
        elif SiteHelper.is_logged_in(text):
            l4 = new_level("L4", STATUS_PASS, "页面呈现已登录状态")
        elif state in ("noResults",) and 200 <= status < 300:
            # 首页本身不带登录态特征（例如首页是门户页），不能据此断言 Cookie 失效
            l4 = new_level("L4", STATUS_WARN, "首页未出现登录态特征（无法据此判定 Cookie 是否有效）",
                           suggestion="站点首页可能本来就不显示用户信息，"
                                      "以 L5 探针搜索的结果为准。")
        else:
            l4 = new_level("L4", STATUS_WARN, "无法判定登录态", detail=desc)

        return [l2, l3, l4]

    def __check_mteam(self, site_info):
        """M-Team 专用：走 API 通道，不适用页面文本判定"""
        try:
            from app.utils import MteamUtils
            ok, reason = MteamUtils.test_connection(site_info)
        except Exception as err:
            ok, reason = False, "调用 M-Team API 失败：%s" % err
        if ok:
            l2 = new_level("L2", STATUS_PASS, "API 通道连通（M-Team 专用检测）")
        else:
            l2 = new_level("L2", STATUS_FAIL, "API 通道不通", detail=reason,
                           suggestion="M-Team 走 API Key 通道，不依赖 Cookie。"
                                      "请确认 API Key 是否有效、是否需要代理。")
        l3 = new_level("L3", STATUS_SKIP, "M-Team 走 API 通道，不适用基于页面文本的反爬判定")
        l4 = new_level("L4", STATUS_SKIP, "M-Team 走 API Key 鉴权，不适用 Cookie 登录态判定")
        return [l2, l3, l4]

    # ------------------------------------------------------------------
    # L5 / L6
    # ------------------------------------------------------------------
    def __check_search(self, site_info, indexer, keyword):
        """
        探针搜索：真实走一次抓取链路

        这是最有价值的一层 —— 它同时验证了「搜索页可达」与「解析规则有效」，
        而这两件事都不是访问首页能发现的。
        """
        start = time.time()
        try:
            flag, result_array, state, desc, channel = run_probe_search(
                indexer=indexer, keyword=keyword)
        except Exception as err:
            elapsed = int((time.time() - start) * 1000)
            fail = new_level("L5", STATUS_FAIL, "探针搜索抛出异常", detail=str(err),
                             items=[{"key": "关键词", "value": keyword, "ok": None},
                                    {"key": "耗时", "value": "%s 毫秒" % elapsed, "ok": None}],
                             suggestion="这通常说明站点规则与该站点当前页面结构已不匹配，"
                                        "请到「索引器」页面确认该站点的规则是否需要更新。")
            return [fail, new_level("L6", STATUS_SKIP, "因 L5 失败而跳过")]
        elapsed = int((time.time() - start) * 1000)

        count = len(result_array or [])
        l5_items = [
            {"key": "关键词", "value": keyword, "ok": None},
            {"key": "抓取通道", "value": channel, "ok": None},
            {"key": "解析条数", "value": count, "ok": count > 0},
            {"key": "耗时", "value": "%s 毫秒" % elapsed, "ok": None},
        ]

        if state in ("needLogin", "CFBlocked", "httpError", "timeout", "parseError", "searchError"):
            l5 = new_level("L5", STATUS_FAIL, "探针搜索未成功：%s" % (desc or state), detail=desc,
                           items=l5_items + [{"key": "状态", "value": state, "ok": False}],
                           suggestion="该站点在**搜索环节**失败，而不是首页不可达。"
                                      "请对照上方的状态说明处理："
                                      "needLogin=更新 Cookie；CFBlocked=启用浏览器渲染或代理；"
                                      "timeout=检查代理；parseError=规则可能已失效。")
        elif count == 0:
            l5 = new_level("L5", STATUS_WARN,
                           "搜索页可达，但用「%s」解析出 0 条" % keyword,
                           detail=desc, items=l5_items,
                           suggestion="换一个更通用的关键词（如 1080p、2024）再测一次。"
                                      "若依然为 0，很可能是该站点的解析规则已失效。")
        else:
            l5 = new_level("L5", STATUS_PASS, "搜索页可达，解析出 %s 条种子" % count, items=l5_items)

        # ---------- L6 选择器命中率 ----------
        if count == 0:
            return [l5, new_level("L6", STATUS_SKIP, "没有解析出任何种子，无字段可统计")]

        fields = measure_fields(result_array)
        f_items = [{"key": f["label"],
                    "value": "%s/%s（%s%%）" % (f["ok"], f["total"], f["rate"]),
                    "ok": f["missing"] == 0}
                   for f in fields]
        broken = [f for f in fields if f["ok"] == 0]
        partial = [f for f in fields if 0 < f["ok"] < f["total"]]

        if broken:
            names = "、".join(f["label"] for f in broken)
            l6 = new_level("L6", STATUS_FAIL, "关键字段完全解析不出：%s" % names, items=f_items,
                           suggestion="这些字段的选择器已经失效（通常是站点改版）。"
                                      "该站点的结果会大量进入「不匹配」或无法下载，"
                                      "需要更新该站点的解析规则。")
        elif partial:
            names = "、".join("%s(%s%%)" % (f["label"], f["rate"]) for f in partial)
            l6 = new_level("L6", STATUS_WARN, "部分字段有缺失：%s" % names, items=f_items,
                           suggestion="缺失字段可能导致部分资源被过滤掉。"
                                      "若「记录查询」里该站的「名称脏」计数偏高，"
                                      "就是这个原因。")
        else:
            l6 = new_level("L6", STATUS_PASS, "标题/下载链接/大小/做种数/发布时间全部解析成功",
                           items=f_items)

        return [l5, l6]

    # ------------------------------------------------------------------
    # 报告汇总
    # ------------------------------------------------------------------
    @staticmethod
    def __build_report(site_info, levels, probe_keyword, started):
        site_name = (site_info or {}).get("name") or "站点"
        conclusion, suggestion, key_level = build_conclusion(levels, site_name)
        worst = worst_status(levels)
        return {
            "site": {
                "id": (site_info or {}).get("id"),
                "name": site_name,
                "url": StringUtils.get_base_url(
                    (site_info or {}).get("signurl") or (site_info or {}).get("rssurl")),
            },
            "probe_keyword": probe_keyword,
            "levels": levels,
            "worst": worst,
            "worst_text": STATUS_TEXT.get(worst, worst),
            "conclusion": conclusion,
            "suggestion": suggestion,
            "key_level": key_level,
            "elapsed": int((datetime.now() - started).total_seconds() * 1000),
        }


def run_probe_search(indexer, keyword):
    """
    按索引器的通道跑一次探针搜索（与 BuiltinIndexer.search 的分发逻辑一致）

    刻意不复制抓取等待逻辑：feapder 通道直接调 builtin.spider_search，
    插件通道直接调 PluginsSpider，保证「体检」与「真实搜索」跑的是同一套代码。

    :param indexer: 索引器配置
    :param keyword: 探针关键词
    :return: (error_flag, 种子列表, 状态, 状态说明, 通道名)
    """
    from app.indexer.client.builtin import _fallback_search_state, spider_search
    from app.indexer.client._plugins import PluginsSpider

    parser = getattr(indexer, "parser", None)
    state, desc = None, ""

    if parser == "TNodeSpider":
        from app.indexer.client._tnode import TNodeSpider
        flag, array = TNodeSpider(indexer).search(keyword=keyword)
        channel = "TNodeSpider"
    elif parser == "RenderSpider":
        from app.indexer.client._render_spider import RenderSpider
        flag, array = RenderSpider(indexer).search(keyword=keyword)
        channel = "RenderSpider（浏览器渲染）"
    elif parser == "TorrentLeech":
        from app.indexer.client._torrentleech import TorrentLeech
        flag, array = TorrentLeech(indexer).search(keyword=keyword)
        channel = "TorrentLeech"
    elif parser == "MTeamSpider":
        from app.indexer.client._mteam import MTeamSpider
        flag, array = MTeamSpider(indexer=indexer).search(keyword=keyword)
        channel = "MTeamSpider（API）"
    elif parser == "HaiDanSpider":
        from app.indexer.client._haidan import HaiDanSpider
        flag, array, state, desc = spider_search(HaiDanSpider(), indexer, keyword=keyword)
        channel = "HaiDanSpider"
    elif PluginsSpider().status(indexer=indexer):
        flag, array = PluginsSpider().search(keyword=keyword, indexer=indexer)
        channel = "插件索引器（%s）" % (parser or "plugin")
    else:
        from app.indexer.client._spider import TorrentSpider
        flag, array, state, desc = spider_search(TorrentSpider(), indexer, keyword=keyword)
        channel = "内置抓取（TorrentSpider）"

    if not state:
        state, desc = _fallback_search_state(flag, array)
    if not state and not array:
        state, desc = "noResults", "站点响应正常，但没有返回任何种子"
    return flag, array, state, desc, channel
