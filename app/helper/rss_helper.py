import re
import xml.dom.minidom

import log
from app.db import MainDb, DbPersist
from app.db.models import RSSTORRENTS
from app.utils import RssTitleUtils, StringUtils, RequestUtils, ExceptionUtils, DomUtils
from config import Config


class RssHelper:
    _db = MainDb()

    @staticmethod
    def __describe(url, resp, body):
        """
        把「拿到的响应不是有效RSS」这件事翻译成站点维度的原因描述
        :param url: RSS地址
        :param resp: requests 响应对象，可能为None
        :param body: 响应文本，可能为空
        :return: 人类可读的原因描述
        """
        domain = StringUtils.get_url_domain(url) or "未知站点"
        head = (body or "").strip()
        snippet = re.sub(r"\s+", " ", head[:120])

        if not head:
            if resp is not None:
                return f"站点 {domain} 返回了空内容(HTTP {resp.status_code})，通常是需要Cookie/Passkey，或站点临时异常"
            return f"站点 {domain} 没有返回任何内容，请检查网络连通性或站点是否需要代理"

        # 详情页而非RSS：常见于抓取到登录页/Cloudflare盾
        if head[:200].lower().startswith("<!doctype html") or head[:60].lower().startswith("<html"):
            return (f"站点 {domain} 返回的是网页而不是RSS，通常是登录态失效或站点开了人机校验(Cloudflare)，"
                    f"请在站点维护中更新Cookie后重试；响应片段：{snippet}")

        # JSON 错误体：多为接口鉴权失败
        if head.startswith("{") or head.startswith("["):
            return f"站点 {domain} 返回了接口错误而不是RSS，通常是鉴权失败或Passkey已失效；响应片段：{snippet}"

        # 非XML纯文本：多为站点给出的提示语（含链接过期文案）
        if not head.startswith("<"):
            return f"站点 {domain} 返回的不是RSS格式，响应片段：{snippet}"

        # 以 < 开头却解析失败：先看是不是 HTML 网页误当RSS
        if re.match(r"^<\s*!doctype\s+html|^<\s*html", head, re.IGNORECASE):
            return (f"站点 {domain} 返回的是网页而不是RSS，通常是登录态失效或站点开启了人机校验(Cloudflare)，"
                    f"请在站点维护中更新Cookie后重试；响应片段：{snippet}")
        # 其余以 < 开头却解析失败的情况：XML 本身残缺或编码异常
        return f"站点 {domain} 返回的RSS内容残缺或编码异常，无法解析；响应片段：{snippet}"

    @staticmethod
    def parse_rssxml(url, proxy=False):
        """
        解析RSS订阅URL，获取RSS中的种子信息
        :param url: RSS地址
        :param proxy: 是否使用代理
        :return: 种子信息列表，如为None代表Rss过期
        """
        _special_title_sites = {
            'pt.keepfrds.com': RssTitleUtils.keepfriends_title
        }

        # 链接过期特征（站点直接以纯文本返回，故用包含匹配）
        _rss_expired_features = [
            "RSS 链接已过期",
            "RSS链接已过期",
            "RSS Link has expired"
        ]
        # XML 无法解析时，响应体的正常开头（用于生成排查提示）
        _xml_head = "^\\s*(<\\?xml|<rss|<feed|<channel)"

        # 开始处理
        ret_array = []
        if not url:
            return []
        domain = StringUtils.get_url_domain(url) or "未知站点"
        try:
            ret = RequestUtils(proxies=Config().get_proxies() if proxy else None).get_res(url)
            if not ret:
                log.warn(f"【Rss】站点 {domain} 请求失败，没有收到任何响应，"
                         f"请检查网络连通性或该站点是否需要开启代理")
                return []
            ret.encoding = ret.apparent_encoding
        except Exception as e2:
            log.error(f"【Rss】站点 {domain} 请求异常：{str(e2)}")
            return []
        if ret:
            ret_xml = ret.text
            try:
                # 解析XML
                dom_tree = xml.dom.minidom.parseString(ret_xml)
                rootNode = dom_tree.documentElement
                # HTML 页面是「合法」的XML，能解析成功却取不到 item，
                # 若不做判定会一路静默返回空列表，日志里看不出站点到底出了什么事
                if (rootNode.tagName or "").lower() == "html":
                    log.error(f"【Rss】{RssHelper.__describe(url, ret, ret_xml)}")
                    log.warn(f"【Rss】站点 {domain} 的返回内容可以确认不是RSS，"
                             f"请到站点维护中核对 rssurl 以及 Cookie 是否有效")
                    return []
                items = rootNode.getElementsByTagName("item")
                for item in items:
                    try:
                        # 标题
                        title = DomUtils.tag_value(item, "title", default="")
                        if not title:
                            continue
                        # 标题特殊处理
                        if domain and domain in _special_title_sites:
                            title = _special_title_sites.get(domain)(title)
                        # 描述
                        description = DomUtils.tag_value(item, "description", default="")
                        # 种子页面
                        link = DomUtils.tag_value(item, "link", default="")
                        # 种子链接
                        enclosure = DomUtils.tag_value(item, "enclosure", "url", default="")
                        if not enclosure and not link:
                            continue
                        # 部分RSS只有link没有enclosure
                        if not enclosure and link:
                            enclosure = link
                            link = None
                        # 大小
                        size = DomUtils.tag_value(item, "enclosure", "length", default=0)
                        if size and str(size).isdigit():
                            size = int(size)
                        else:
                            size = 0
                        # 发布日期
                        pubdate = DomUtils.tag_value(item, "pubDate", default="")
                        if pubdate:
                            # 转换为时间
                            pubdate = StringUtils.get_time_stamp(pubdate)
                        # 返回对象
                        tmp_dict = {'title': title,
                                    'enclosure': enclosure,
                                    'size': size,
                                    'description': description,
                                    'link': link,
                                    'pubdate': pubdate}
                        ret_array.append(tmp_dict)
                    except Exception as e1:
                        ExceptionUtils.exception_traceback(e1)
                        continue
            except Exception as e2:
                # 站点多以纯文本返回过期提示，故用包含匹配而非全等
                _body = (ret_xml or "").strip()
                if any(msg in _body for msg in _rss_expired_features):
                    log.error(f"【Rss】站点 {domain} 的RSS链接已过期，请重新获取链接")
                    return None
                # 不能直接把 XML 解析异常抛给用户（ExpatError 的 traceback 看不出是哪个站点出的问题），
                # 按响应内容判断真实原因并以站点维度输出，附响应片段便于定位
                log.error(f"【Rss】{RssHelper.__describe(url, ret, ret_xml)}")
                if not re.match(_xml_head, _body, re.IGNORECASE):
                    log.warn(f"【Rss】站点 {domain} 的返回内容可以确认不是RSS，"
                             f"请到站点维护中核对 rssurl 以及 Cookie 是否有效")
                # 保留原始异常，便于排查XML残缺等极端情况
                log.debug(f"【Rss】站点 {domain} 解析XML的原始异常：{str(e2)}")
        return ret_array

    @DbPersist(_db)
    def insert_rss_torrents(self, media_info):
        """
        将RSS的记录插入数据库
        """
        self._db.insert(
            RSSTORRENTS(
                TORRENT_NAME=media_info.org_string,
                ENCLOSURE=media_info.enclosure,
                TYPE=media_info.type.value,
                TITLE=media_info.title,
                YEAR=media_info.year,
                SEASON=media_info.get_season_string(),
                EPISODE=media_info.get_episode_string()
            ))

    def is_rssd_by_enclosure(self, enclosure):
        """
        查询RSS是否处理过，根据下载链接
        """
        if not enclosure:
            return True
        if self._db.query(RSSTORRENTS).filter(RSSTORRENTS.ENCLOSURE == enclosure).count() > 0:
            return True
        else:
            return False

    def is_rssd_by_simple(self, torrent_name, enclosure):
        """
        查询RSS是否处理过，根据名称
        """
        if not torrent_name and not enclosure:
            return True
        if enclosure:
            ret = self._db.query(RSSTORRENTS).filter(RSSTORRENTS.ENCLOSURE == enclosure).count()
        else:
            ret = self._db.query(RSSTORRENTS).filter(RSSTORRENTS.TORRENT_NAME == torrent_name).count()
        return True if ret > 0 else False

    @DbPersist(_db)
    def simple_insert_rss_torrents(self, title, enclosure):
        """
        将RSS的记录插入数据库
        """
        self._db.insert(
            RSSTORRENTS(
                TORRENT_NAME=title,
                ENCLOSURE=enclosure
            ))

    @DbPersist(_db)
    def simple_delete_rss_torrents(self, title, enclosure=None):
        """
        删除RSS的记录
        """
        if enclosure:
            self._db.query(RSSTORRENTS).filter(RSSTORRENTS.TORRENT_NAME == title,
                                               RSSTORRENTS.ENCLOSURE == enclosure).delete()
        else:
            self._db.query(RSSTORRENTS).filter(RSSTORRENTS.TORRENT_NAME == title).delete()

    @DbPersist(_db)
    def truncate_rss_history(self):
        """
        清空RSS历史记录
        """
        self._db.query(RSSTORRENTS).delete()
