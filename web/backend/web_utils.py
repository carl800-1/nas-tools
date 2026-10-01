from functools import lru_cache

import cn2an

from app.media import Media, Bangumi, DouBan
from app.media.meta import MetaInfo
from app.utils import StringUtils, ExceptionUtils, SystemUtils, RequestUtils, IpUtils
from app.utils.types import MediaType
from config import Config
from version import APP_VERSION


class WebUtils:

    @staticmethod
    def get_location(ip):
        """
        根据IP址查询真实地址
        """
        if not IpUtils.is_ipv4(ip):
            return ""
        url = 'https://sp0.baidu.com/8aQDcjqpAAV3otqbppnN2DJv/api.php?co=&resource_id=6006&t=1529895387942&ie=utf8' \
              '&oe=gbk&cb=op_aladdin_callback&format=json&tn=baidu&' \
              'cb=jQuery110203920624944751099_1529894588086&_=1529894588088&query=%s' % ip
        try:
            r = RequestUtils().get_res(url)
            if r:
                r.encoding = 'gbk'
                html = r.text
                c1 = html.split('location":"')[1]
                c2 = c1.split('","')[0]
                return c2
            else:
                return ""
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return ""

    @staticmethod
    def get_current_version():
        """
        获取当前版本号
        """
        return APP_VERSION

    @staticmethod
    def get_latest_version():
        """
        获取最新版本号
        """
        try:
            releases_update_only = True
            version_res = RequestUtils(proxies=Config().get_proxies()).get_res(
                "https://api.github.com/repos/carl800-1/nas-tools/releases/latest")
            commit_res = RequestUtils(proxies=Config().get_proxies()).get_res(
                "https://api.github.com/repos/carl800-1/nas-tools/commits/master")
            if version_res and commit_res:
                ver_json = version_res.json()
                commit_json = commit_res.json()
                if releases_update_only:
                    version = f"{ver_json['tag_name']}"
                else:
                    version = f"{ver_json['tag_name']} {commit_json['sha'][:7]}"
                url = ver_json["html_url"]
                return version, url
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
        return None, None

    @staticmethod
    def get_mediainfo_from_id(mtype, mediaid, wait=False):
        """
        根据TMDB/豆瓣/BANGUMI获取媒体信息
        """
        if not mediaid:
            return None
        media_info = None
        if str(mediaid).startswith("DB:"):
            # 豆瓣
            doubanid = mediaid[3:]
            info = DouBan().get_douban_detail(doubanid=doubanid, mtype=mtype, wait=wait)
            if not info:
                return None
            title = info.get("title")
            original_title = info.get("original_title")
            year = info.get("year")
            # 支持自动识别类型
            if not mtype:
                mtype = MediaType.TV if info.get("episodes_count") else MediaType.MOVIE
            if original_title:
                media_info = Media().get_media_info(title=f"{original_title} {year}",
                                                    mtype=mtype,
                                                    append_to_response="all")
            if not media_info or not media_info.tmdb_info:
                media_info = Media().get_media_info(title=f"{title} {year}",
                                                    mtype=mtype,
                                                    append_to_response="all")
            media_info.douban_id = doubanid
        elif str(mediaid).startswith("BG:"):
            # BANGUMI
            bangumiid = str(mediaid)[3:]
            info = Bangumi().detail(bid=bangumiid)
            if not info:
                return None
            title = info.get("name")
            title_cn = info.get("name_cn")
            year = info.get("date")[:4] if info.get("date") else ""
            media_info = Media().get_media_info(title=f"{title} {year}",
                                                mtype=MediaType.TV,
                                                append_to_response="all")
            if not media_info or not media_info.tmdb_info:
                media_info = Media().get_media_info(title=f"{title_cn} {year}",
                                                    mtype=MediaType.TV,
                                                    append_to_response="all")
        else:
            # TMDB
            info = Media().get_tmdb_info(tmdbid=mediaid,
                                         mtype=mtype,
                                         append_to_response="all")
            if not info:
                return None
            media_info = MetaInfo(title=info.get("title") if mtype == MediaType.MOVIE else info.get("name"))
            media_info.set_tmdb_info(info)

        return media_info

    @staticmethod
    def search_media_infos(keyword, source=None, page=1):
        """
        搜索TMDB或豆瓣词条
        :param: keyword 关键字
        :param: source 渠道 tmdb/douban
        :param: season 季号
        :param: episode 集号
        """
        if not keyword:
            return []
        mtype, key_word, season_num, episode_num, _, content = StringUtils.get_keyword_from_string(keyword)
        if source == "tmdb":
            use_douban_titles = False
        elif source == "douban":
            use_douban_titles = True
        else:
            use_douban_titles = Config().get_config("laboratory").get("use_douban_titles")
        if use_douban_titles:
            medias = DouBan().search_douban_medias(keyword=key_word,
                                                   mtype=mtype,
                                                   season=season_num,
                                                   episode=episode_num,
                                                   page=page)
        else:
            meta_info = MetaInfo(title=content)
            tmdbinfos = Media().get_tmdb_infos(title=meta_info.get_name(),
                                               year=meta_info.year,
                                               mtype=mtype,
                                               page=page)
            medias = []
            for tmdbinfo in tmdbinfos:
                tmp_info = MetaInfo(title=keyword)
                tmp_info.set_tmdb_info(tmdbinfo)
                if meta_info.type != MediaType.MOVIE and tmp_info.type == MediaType.MOVIE:
                    continue
                if tmp_info.begin_season:
                    tmp_info.title = "%s 第%s季" % (tmp_info.title, cn2an.an2cn(meta_info.begin_season, mode='low'))
                if tmp_info.begin_episode:
                    tmp_info.title = "%s 第%s集" % (tmp_info.title, meta_info.begin_episode)
                medias.append(tmp_info)
        return medias

    @staticmethod
    def get_page_range(current_page, total_page):
        """
        计算分页范围
        """
        if total_page <= 5:
            StartPage = 1
            EndPage = total_page
        else:
            if current_page <= 3:
                StartPage = 1
                EndPage = 5
            elif current_page >= total_page - 2:
                StartPage = total_page - 4
                EndPage = total_page
            else:
                StartPage = current_page - 2
                if total_page > current_page + 2:
                    EndPage = current_page + 2
                else:
                    EndPage = total_page
        return range(StartPage, EndPage + 1)

    @staticmethod
    def get_image_cookies(url):
        """
        取当前媒体服务器客户端为某张图片提供的凭证

        只有目标属于该客户端自己那台服务器时，客户端才会返回凭证（客户端内部校验），
        避免 /img 变成「带着凭证请求任意 URL」的通道。
        """
        try:
            # 延迟导入：避免 web 层与 app.mediaserver 形成导入环
            from app.mediaserver import MediaServer
            server = MediaServer().server
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return None
        if not server:
            return None
        getter = getattr(server, "get_image_cookies", None)
        if not callable(getter):
            return None
        try:
            return getter(url) or None
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return None

    @staticmethod
    def request_cache(url, cookies=None):
        """
        带缓存的请求

        :param url: 图片地址
        :param cookies: 需要随请求携带的 Cookies（飞牛这类鉴权图片需要凭证）
        """
        # dict 不可哈希，先归一化成可哈希的元组才能进 lru_cache 的键；
        # 键里必须含凭证指纹，否则同一 URL 换了凭证会命中旧缓存。
        cookie_key = tuple(sorted((cookies or {}).items()))
        return WebUtils._request_cache(url, cookie_key)

    @staticmethod
    @lru_cache(maxsize=128)
    def _request_cache(url, cookie_key):
        cookies = dict(cookie_key) if cookie_key else None
        if url.find('douban'):
            # ⚠️ 这个判断缺 `!= -1`，实际几乎总走这一支；凭证必须在这里也带上，
            # 否则飞牛这类需要鉴权的图片永远取不到（v6.2.6 由离线测试逮到）。
            ret = RequestUtils(referer="https://movie.douban.com",
                               cookies=cookies).get_res(url)
        else:
            ret = RequestUtils(cookies=cookies).get_res(url)
        if ret:
            return ret.content
        
        # 避免 lru 缓存失败的情况，exception 不会被缓存
        raise Exception('request failed')
