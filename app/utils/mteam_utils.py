from urllib.parse import urlparse

import log

from app.utils import RequestUtils, StringUtils
from config import Config


class MteamUtils:

    @staticmethod
    def get_api_url(url):
        """
        从站点地址推导 M-Team 的 API 基地址（如 kp.m-team.cc -> api.m-team.cc）

        ⚠️ 只对含 ``m-team`` 的主站域名有效。M-Team 的下载直链域名（dlv2 的
        ``*.halomt.com``）不含 ``m-team``，此处若硬取会抛 ValueError；
        v5.2.1 起改为返回 None，由调用方决定如何降级，避免把异常抛到业务链路里。
        """
        parsed_url = urlparse(url)
        hostname = parsed_url.hostname or ""
        index = hostname.find('m-team')
        if index < 0:
            log.debug(f"【MTeam】{hostname} 不是 M-Team 主站域名，无法推导 api 地址")
            return None
        last_domain = hostname[index:]
        return f"{parsed_url.scheme}://api.{last_domain}"

    @staticmethod
    def build_api_url(url, path):
        """
        拼出完整的 API 地址；域名推导失败时返回 None

        统一入口，避免各处 `"%s/api/xxx" % get_api_url(...)` 在推导失败时
        拼出 ``None/api/xxx`` 这种「看起来像 URL、实际必然失败」的字符串。
        """
        api_url = MteamUtils.get_api_url(url)
        if not api_url:
            return None
        return f"{api_url}{path}"

    @staticmethod
    def get_mteam_torrent_info(torrent_url, ua=None, proxy=False):
        api = MteamUtils.build_api_url(torrent_url, "/api/torrent/detail")
        if not api:
            log.warn(f"【MTeam】无法从 {torrent_url} 推导 API 地址，跳过种子详情获取")
            return None
        torrent_id = torrent_url.split('/')[-1]
        req = MteamUtils.buildRequestUtils(api_key=MteamUtils.get_api_key(torrent_url), headers=ua, proxies=proxy).post_res(url=api, params={"id": torrent_id})

        if req and req.status_code == 200:
            return req.json().get("data")

        return None

    @staticmethod
    def test_connection(site_info):
        site_url = site_info.get("signurl")
        api = MteamUtils.build_api_url(site_url, "/api/member/profile")
        if not api:
            return False, f"无法从 {site_url} 推导 API 地址"
        site_api_key = site_info.get("api_key")
        ua = site_info.get("ua")
        res = MteamUtils.buildRequestUtils(
            headers=ua,
            api_key=site_api_key,
            proxies=Config().get_proxies() if site_info.get("proxy") else None,
            timeout=15
        ).post_res(url=api)
        if res:
            if res.status_code == 200:
                user_info = res.json()
                if user_info and user_info.get("data"):
                    return True, "连接成功"
            else:
                return False, "连接失败：" + str(res.status_code)
        return False, "连接失败"

    @staticmethod
    def get_mteam_torrent_url(url, ua=None, referer=None, proxy=False):
        """
        把 M-Team 的详情页/种子页链接换成可下载的直链

        ⚠️ 两种情况直接原样返回，不再去调 genDlToken：
          1. ``api/rss/dl`` —— RSS 下载链接本身就带签名，自足
          2. 域名不含 ``m-team`` —— 说明已经是 dlv2 直链（如 ``*.halomt.com``），
             再拿它去推导 api 地址会得到 None，原写法会拼出 "None/api/torrent/genDlToken"
             这样的坏 URL，反而把可用的直链弄坏
        """
        if url.find('api/rss/dl') != -1:
            return url
        api = MteamUtils.build_api_url(url, "/api/torrent/genDlToken")
        if not api:
            # 已是直链（域名如 *.halomt.com），原样返回交给请求环节处理
            log.debug(f"【MTeam】{url} 已是下载直链，跳过 genDlToken 换取")
            return url
        parsed_url = urlparse(url)
        torrent_id = parsed_url.path.split('/')[-1]

        req = MteamUtils.buildRequestUtils(
            headers=ua,
            api_key=MteamUtils.get_api_key(url),
            referer=referer,
            proxies=Config().get_proxies() if proxy else None
        ).post_res(url=api, params={"id": torrent_id})

        if req and req.status_code == 200:
            return req.json().get("data")

        return None

    @staticmethod
    def get_mteam_torrent_req(url, ua=None, referer=None, proxy=False):
        """
        请求 M-Team 种子链接，返回原始响应（**不自动跟随重定向**）

        这里必须 allow_redirects=False：M-Team 的 dlv2 链接经常返回 302，
        目标既可能是种子文件地址，也可能是磁力链（magnet:?xt=...）。
        调用方 Torrent.save_torrent_file 有一段 while 循环专门处理 301/302
        并识别 magnet:，若在此处就自动跟随，requests 遇到 magnet: 协议会抛
        InvalidSchema（被 RequestUtils.get_res 吞成 None），对外只剩一句
        「无法打开链接」，真正的失败原因完全看不到。
        """
        try:
            return MteamUtils.buildRequestUtils(
                api_key=MteamUtils.get_api_key(url),
                headers=ua,
                referer=referer,
                proxies=Config().get_proxies() if proxy else None
            ).get_res(url=url, allow_redirects=False, raise_exception=True)
        except Exception as err:
            # 把域名单独打出来：M-Team 下载直链用 *.halomt.com，该域名可能被
            # 网络阻断（TCP 通、TLS 握手被 RST），此时异常多半是 SSLError /
            # ConnectionError。分开打能一眼区分「域名被墙」和「鉴权/接口变更」。
            host = (urlparse(url).hostname or "?") if url else "?"
            log.error(f"【MTeam】获取种子链接失败：域名={host} "
                      f"{type(err).__name__}: {str(err)}")
            if "halomt.com" in (url or ""):
                log.warn("【MTeam】下载域名 *.halomt.com 疑似被网络阻断，"
                         "可尝试为 M-Team 站点开启「使用代理」后重试")
            return None

    @staticmethod
    def get_api_key(url):
        """
        取 M-Team 的 API Key（x-api-key 请求头用）

        两级查找：
          1. 按传入 URL 的域名精确匹配站点配置（正常情况，站点填的是 kp.m-team.cc）
          2. 匹配不到时回退到「任一 M-Team 站点」—— **这是关键**

        ⚠️ 为什么必须回退：M-Team 的下载直链走 dlv2 格式，域名是 ``*.halomt.com``
        （如 ``fr1.halomt.com``），它**不在站点表里**。若此处直接返回 None，
        请求就会丢掉 x-api-key 变成裸请求，最终对外只剩一句「无法打开链接」，
        完全看不出是鉴权问题（2026-09-22 实测踩到）。

        api_key 对同一账号的 M-Team 全站通用，所以用任一站点的 key 是安全的。
        """
        from app.sites import Sites
        sites = Sites()
        site_info = sites.get_sites(siteurl=url)
        if not site_info:
            # 回退：直链域名（halomt.com 等）匹配不到站点配置，找一个 M-Team 站点借 key
            for candidate in sites.get_sites():
                site_url = candidate.get("signurl") or candidate.get("domain") or ""
                if "m-team" in site_url:
                    site_info = candidate
                    break
            if not site_info:
                log.warn(f"【MTeam】{url} 匹配不到站点配置，且未找到任何 M-Team 站点，无法取 API Key")
                return None
            log.debug(f"【MTeam】{url} 未匹配到站点配置，已回退到站点「{site_info.get('name')}」取 API Key")

        api_key = site_info.get("api_key")
        if not api_key:
            log.warn(f"【MTeam】站点「{site_info.get('name')}」未配置 API Key，"
                     f"M-Team 下载与搜索将不可用")
            return None
        return api_key

    @staticmethod
    def buildRequestUtils(cookies=None, api_key=None, headers=None, proxies=False, content_type=None, accept_type=None, session=None, referer=None, timeout=30):
        if api_key:
            # use api key
            return RequestUtils(headers=headers, api_key=api_key, timeout=timeout, referer=referer,
                                content_type=content_type, session=session, accept_type=accept_type,
                                proxies=Config().get_proxies() if proxies else None)
        return RequestUtils(headers=headers, cookies=cookies, timeout=timeout, referer=referer,
                            content_type=content_type, session=session, accept_type=accept_type,
                            proxies=Config().get_proxies() if proxies else None)

