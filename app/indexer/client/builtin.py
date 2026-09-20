import copy
import datetime
import time

import log
from app.conf import SystemConfig
from app.helper import ProgressHelper, ChromeHelper, DbHelper
from app.indexer.client._base import _IIndexClient
from app.indexer.client._haidan import HaiDanSpider
from app.indexer.client._render_spider import RenderSpider
from app.indexer.client._spider import TorrentSpider
from app.indexer.client._spider_new import DefaultSpider
from app.indexer.client._tnode import TNodeSpider
from app.indexer.client._torrentleech import TorrentLeech
from app.indexer.client._plugins import PluginsSpider
from app.indexer.client._mteam import MTeamSpider
from app.sites import Sites
from app.utils import StringUtils
from app.utils.types import SearchType, IndexerType, ProgressKey, SystemConfigKey, MediaType
from config import Config
from web.backend.pro_user import ProUser


def _describe_search_error(err):
    """
    把搜索异常归类成便于定位的简短描述

    用于日志与进度提示：现场最常见的问题是「站点不可达」，但老代码只在
    控制台 print 原始异常，容器日志里看不到，用户只能看到一句
    「未搜索到数据」，无从判断是网络问题还是真的没有资源。

    :param err: 捕获到的异常对象
    :return: 归类后的中文描述
    """
    text = "%s %s" % (type(err).__name__, err)
    low = text.lower()
    if "timeout" in low or "timed out" in low:
        return "连接超时（站点不可达或需要代理）"
    if "connectionreset" in low or "reset by peer" in low or "sslerror" in low or "ssl" in low:
        return "连接被重置（可能被网络拦截，需检查代理）"
    if "connectionrefused" in low or "refused" in low:
        return "连接被拒绝（域名或端口不可用）"
    if "nameresolution" in low or "gaierror" in low or "getaddrinfo" in low \
            or "name or service not known" in low or "nodename nor servname" in low:
        return "域名解析失败（DNS 不可用）"
    if "403" in text or "forbidden" in low:
        return "被站点拒绝（403，Cookie 失效或反爬）"
    if "401" in text or "unauthorized" in low:
        return "鉴权失败（401，Cookie 可能已过期）"
    if "429" in text or "too many" in low:
        return "触发站点限流（429）"
    if "404" in text or "not found" in low:
        return "页面不存在（404，站点规则可能已变更）"
    if "500" in text or "502" in text or "503" in text:
        return "站点服务异常（5xx）"
    return "未知错误"


# 抓取状态 -> 现场可读的提示
#
# 老代码把「抓取被拒」与「真的没有资源」都报成「未搜索到数据」，现场无法区分：
#   HTTP 层被拒（403、Cloudflare 挑战页、200 但返回登录页）不会让 requests 抛异常，
#   feapder 的 validate 默认也不校验状态码，parse() 照常执行但选不到任何种子；
#   网络层失败（超时/连接被拒）时 parse() 根本不执行，is_error 一直是 False。
# 现在按实际状态给出可归因的结论。注意 noResults 刻意沿用原文案，保持向后兼容。
_SEARCH_STATE_TEXT = {
    "noResults": "未搜索到数据",
    "needLogin": "搜索失败（Cookie 失效，站点返回了登录页）",
    "CFBlocked": "搜索失败（被 Cloudflare / 反爬拦截）",
    "httpError": "搜索失败（站点返回异常状态码）",
    "parseError": "搜索失败（页面解析失败，站点可能已改版）",
    "timeout": "搜索失败（请求超时，站点不可达或代理未生效）",
    "searchError": "搜索失败（请求或解析出错）",
}


def _fallback_search_state(error_flag, result_array):
    """
    给不产出细分状态的通道兜底

    TNodeSpider / RenderSpider / TorrentLeech / MTeamSpider / 插件索引器（Jackett、Prowlarr）
    这些通道只返回一个 error_flag 布尔，没有细分状态，这里统一折算口径，
    保证上层取 _SEARCH_STATE_TEXT 时不会拿到 None。

    :param error_flag: 该通道返回的错误标志
    :param result_array: 该通道返回的种子列表
    :return: (state, 状态说明)
    """
    if error_flag:
        return "searchError", "该通道只返回错误标志，未能提供细分状态"
    if not result_array:
        return "noResults", "站点响应正常，但没有返回任何种子"
    return None, ""


def spider_search(spider, indexer, keyword=None, page=None, mtype=None, timeout=30):
    """
    用 feapder 爬虫抓取单个站点，并等待结果回来

    抽成模块级函数是为了让站点体检（app/sites/site_health.py）能跑完全相同的
    链路做探针搜索 —— 体检的意义就在于「看到的东西和真实搜索一致」。

    :param spider: feapder 爬虫实例（TorrentSpider / HaiDanSpider 等）
    :param indexer: 站点索引器配置
    :param keyword: 关键字
    :param page: 页码
    :param mtype: 媒体类型
    :param timeout: 等待超时的循环次数（每次 sleep 0.5 秒）
    :return: 是否发生错误, 种子列表, 抓取状态, 状态说明
    """
    log.debug(f"spider search start {indexer.name}")

    spider.setparam(indexer=indexer,
                    keyword=keyword,
                    page=page,
                    mtype=mtype)
    spider.start()

    # 循环判断是否获取到数据
    sleep_count = 0
    while not spider.is_complete:
        sleep_count += 1
        time.sleep(0.5)
        if sleep_count > timeout:
            break
    # 等超时仍未完成，说明请求根本没回来（超时 / 连接被拒 / 代理失败）。
    # feapder 在这种情况下会走 exception_request → failed_request，不会调用 parse()，
    # 于是 is_error 一直停在 False，老代码只能把它报成「未搜索到数据」。
    # 这里补一个明确的 timeout 状态，让上层能把它与「真的没有资源」区分开。
    if not spider.is_complete:
        spider.mark_timeout()
        log.warn(f"【Spider】{indexer.name} 等待超时（约 {int(timeout * 0.5)} 秒），"
                 f"请求可能未返回：{spider.search_state_desc}")
    # 是否发生错误
    result_flag = spider.is_error
    # 种子列表
    result_array = spider.torrents_info_array.copy()
    # 重置状态
    spider.torrents_info_array.clear()

    log.debug(f"spider search end  {indexer.name}")
    return result_flag, result_array, spider.search_state, spider.search_state_desc


class BuiltinIndexer(_IIndexClient):
    # 索引器ID
    client_id = "builtin"
    # 索引器类型
    client_type = IndexerType.BUILTIN
    # 索引器名称
    client_name = IndexerType.BUILTIN.value

    # 私有属性
    _client_config = {}
    _show_more_sites = False
    progress = None
    sites = None
    dbhelper = None
    user = None
    chromehelper = None
    systemconfig = None

    def __init__(self, config=None):
        super().__init__()
        self.quick_search = False
        self._client_config = config or {}
        self.init_config()

    def init_config(self):
        self.sites = Sites()
        self.progress = ProgressHelper()
        self.dbhelper = DbHelper()
        self.user = ProUser()
        self.chromehelper = ChromeHelper()
        self.systemconfig = SystemConfig()
        self._show_more_sites = Config().get_config("laboratory").get('show_more_sites')
        self.quick_search = Config().get_config("laboratory").get('quick_search')

    @classmethod
    def match(cls, ctype):
        return True if ctype in [cls.client_id, cls.client_type, cls.client_name] else False

    def get_type(self):
        return self.client_type

    def get_status(self):
        """
        检查连通性
        :return: True、False
        """
        return True

    def get_indexer(self, url):
        """
        获取单个索引器配置
        """
        # 检查浏览器状态
        chrome_ok = self.chromehelper.get_status()
        site = self.sites.get_sites(siteurl=url)
        if site:
            return self.user.get_indexer(url=url,
                                         siteid=site.get("id"),
                                         cookie=site.get("cookie"),
                                         ua=site.get("ua"),
                                         name=site.get("name"),
                                         rule=site.get("rule"),
                                         pri=site.get('pri'),
                                         public=False,
                                         proxy=site.get("proxy"),
                                         render=False if not chrome_ok else site.get("chrome"))
        return None

    def get_indexers(self, check=True, public=True):
        ret_indexers = []
        _indexer_domains = []
        # 选中站点配置
        indexer_sites = self.systemconfig.get(SystemConfigKey.UserIndexerSites) or []
        # 检查浏览器状态
        chrome_ok = self.chromehelper.get_status()
        # 私有站点
        for site in self.sites.get_sites():
            url = site.get("signurl") or site.get("rssurl")
            cookie = site.get("cookie")
            if not url or not cookie:
                continue
            render = False if not chrome_ok else site.get("chrome")
            indexer = self.user.get_indexer(url=url,
                                            siteid=site.get("id"),
                                            cookie=cookie,
                                            ua=site.get("ua"),
                                            name=site.get("name"),
                                            rule=site.get("rule"),
                                            pri=site.get('pri'),
                                            public=False,
                                            proxy=site.get("proxy"),
                                            render=render)
            if indexer:
                if check and (not indexer_sites or indexer.id not in indexer_sites):
                    continue
                if indexer.domain not in _indexer_domains:
                    _indexer_domains.append(indexer.domain)
                    indexer.name = site.get("name")
                    ret_indexers.append(indexer)
        # 公开站点
        if public and self._show_more_sites:
            for site_url in self.user.get_public_sites():
                indexer = self.user.get_indexer(url=site_url)
                if indexer:
                    if check and (not indexer_sites or indexer.id not in indexer_sites):
                        continue
                    if indexer.domain not in _indexer_domains:
                        _indexer_domains.append(indexer.domain)
                        ret_indexers.append(indexer)
        # 获取插件站点
        # 注意：sites() 内部会遍历所有已安装插件并逐个调用其 get_indexers()，
        # 每调用一次就会向 Jackett / Prowlarr 各发一轮 API 请求。
        # 老代码在这里连续调用了两次 PluginsSpider().sites()（一次判空、一次遍历），
        # 等于每次刷新索引器列表都要多发一倍请求，这里改为只取一次。
        plugin_sites = PluginsSpider().sites()
        if plugin_sites:
            for indexer in plugin_sites:
                if indexer:
                    if check and (not indexer_sites or indexer.id not in indexer_sites):
                        continue
                    if indexer.domain not in _indexer_domains:
                        _indexer_domains.append(indexer.domain)
                        ret_indexers.append(indexer)
        return ret_indexers
    def is_indebug(self):
        loglevel = Config().get_config('app').get('loglevel') or "info"
        if loglevel == "debug":
            return True
        return False
    def search(self, order_seq,
               indexer,
               key_word,
               filter_args: dict,
               match_media,
               in_from: SearchType):
        """
        根据关键字多线程搜索
        """
        if not indexer or not key_word:
            return None
        # 站点流控
        if self.sites.check_ratelimit(indexer.siteid):
            self.progress.update(ptype=ProgressKey.Search, text=f"{indexer.name} 触发站点流控，跳过 ...")
            return []
        # fix 共用同一个dict时会导致某个站点的更新全局全效
        if filter_args is None:
            _filter_args = {}
        else:
            _filter_args = copy.deepcopy(filter_args)
        # 不在设定搜索范围的站点过滤掉
        if _filter_args.get("site") and indexer.name not in _filter_args.get("site"):
            return []
        # 搜索条件没有过滤规则时，使用站点的过滤规则
        if not _filter_args.get("rule") and indexer.rule:
            _filter_args.update({"rule": indexer.rule})
        # 计算耗时
        start_time = datetime.datetime.now()

        log.info(f"【{self.client_name}】开始搜索Indexer：{indexer.name} ...")
        # 特殊符号处理
        search_word = StringUtils.handler_special_chars(text=key_word,
                                                        replace_word=" ",
                                                        allow_space=True)
        # 避免对英文站搜索中文
        if indexer.language == "en" and StringUtils.is_chinese(search_word):
            log.warn(f"【{self.client_name}】{indexer.name} 无法使用中文名搜索")
            return []
        # 开始索引
        result_array = []
        # 本次抓取的实际状态与说明（仅用于把失败原因说清楚，不参与任何匹配判定）
        search_state = None
        search_state_desc = ""
        try:
            if indexer.parser == "TNodeSpider":
                error_flag, result_array = TNodeSpider(indexer).search(keyword=search_word)
            elif indexer.parser == "RenderSpider":
                error_flag, result_array = RenderSpider(indexer).search(
                    keyword=search_word,
                    mtype=match_media.type if match_media and match_media.tmdb_info else None)
            elif indexer.parser == "TorrentLeech":
                error_flag, result_array = TorrentLeech(indexer).search(keyword=search_word)
            elif indexer.parser == "MTeamSpider":
                error_flag, result_array = MTeamSpider(indexer=indexer).search(keyword=search_word)
            elif indexer.parser == "HaiDanSpider":
                error_flag, result_array, search_state, search_state_desc = self.__spider_search(
                    spider=HaiDanSpider(),
                    keyword=search_word,
                    indexer=indexer,
                    mtype=match_media.type if match_media and match_media.tmdb_info else None)
            else:
                if PluginsSpider().status(indexer=indexer):
                    # 顺带把 IMDb 编号传下去：插件会据此再补一轮「按 ID 检索」，
                    # 用于兜住「中文片名在该站搜不到」的情况（IMDb 编号全球唯一，
                    # 不受中英文与译名差异影响）。站点不支持时插件返回空，静默跳过。
                    error_flag, result_array = PluginsSpider().search(
                        keyword=search_word,
                        indexer=indexer,
                        imdb_id=match_media.imdb_id if match_media else None)
                else:
                    error_flag, result_array, search_state, search_state_desc = self.__spider_search(
                        spider=TorrentSpider(),
                        keyword=search_word,
                        indexer=indexer,
                        mtype=match_media.type if match_media and match_media.tmdb_info else None)
            # 非 feapder 通道（TNode / Render / TorrentLeech / MTeam / 插件索引器）
            # 不产出细分状态，这里折算兜底，保证后面取状态文案时不会落空
            if not search_state:
                search_state, search_state_desc = _fallback_search_state(error_flag, result_array)
        except Exception as err:
            error_flag = True
            search_state = "searchError"
            # 异常原因必须写进日志：老代码只 print 到控制台，容器日志里根本看不到，
            # 导致「站点连不上」「域名失效」「反爬拦截」与「真的没搜到」在现场无法区分
            error_desc = _describe_search_error(err)
            search_state_desc = error_desc
            log.error(f"【{self.client_name}】{indexer.name} 搜索出错[{error_desc}]：{err}")
            self.progress.update(ptype=ProgressKey.Search,
                                 text=f"{indexer.name} 搜索失败：{error_desc}")

        # 索引花费的时间
        seconds = round((datetime.datetime.now() - start_time).seconds, 1)
        # 索引统计
        self.dbhelper.insert_indexer_statistics(indexer=indexer.name,
                                                itype=self.client_id,
                                                seconds=seconds,
                                                result='N' if error_flag else 'Y')
        # 返回结果
        if len(result_array) == 0:
            # 区分「抓取被拒」与「确实没有资源」：
            #   error_flag=True  -> 抓取阶段就出问题了，不能断言「没有资源」
            #   error_flag=False -> 站点正常响应但 0 条，通常是真的没搜到
            # 但只有 error_flag 这一个布尔时，403/CF 挑战页/登录页/超时 四种情况
            # 全都塌成同一句「未搜索到数据」。这里按 search_state 给出可归因的结论。
            _state_text = _SEARCH_STATE_TEXT.get(search_state) or "未搜索到数据"
            if error_flag:
                log.warn(f"【{self.client_name}】{indexer.name} 抓取未成功[{search_state}]："
                         f"{search_state_desc or _state_text}")
            else:
                log.warn(f"【{self.client_name}】{indexer.name} 未搜索到数据[{search_state}]："
                         f"{search_state_desc or _state_text}")
            self.progress.update(ptype=ProgressKey.Search, text=f"{indexer.name} {_state_text}")
            return []
        else:
            log.warn(f"【{self.client_name}】{indexer.name} 返回数据：{len(result_array)}")
            # 更新进度
            self.progress.update(ptype=ProgressKey.Search, text=f"{indexer.name} 返回 {len(result_array)} 条数据")
            # 过滤
            if self.quick_search and in_from == SearchType.WEB:
                log.debug(f"quick search start")
                if match_media:
                    if match_media.type == MediaType.MOVIE:
                        result = self.filter_search_results_local(
                        result_array=result_array,
                        order_seq=order_seq,
                        indexer=indexer,
                        filter_args=_filter_args,
                        match_media=match_media,
                        start_time=start_time
                    )
                    else:
                        result = self.filter_search_results_local_for_tv(
                            result_array=result_array,
                            order_seq=order_seq,
                            indexer=indexer,
                            filter_args=_filter_args,
                            match_media=match_media,
                            start_time=start_time
                        )
                    # debug mode, compare with online search
                    # if self.is_indebug():
                    #     self.filter_search_results(result_array=result_array,
                    #                                order_seq=order_seq,
                    #                                indexer=indexer,
                    #                                filter_args=_filter_args,
                    #                                match_media=match_media,
                    #                                start_time=start_time)

                    return result

            return self.filter_search_results(result_array=result_array,
                                       order_seq=order_seq,
                                       indexer=indexer,
                                       filter_args=_filter_args,
                                       match_media=match_media,
                                       start_time=start_time)

    def list(self, url, page=0, keyword=None):
        """
        根据站点ID搜索站点首页资源
        """
        if not url:
            return []
        indexer = self.get_indexer(url)
        if not indexer:
            return []

        # 计算耗时
        start_time = datetime.datetime.now()
        # 抓取状态（仅用于把失败原因说清楚，不参与判定）；
        # 只有 feapder 通道会产出，其余分支保持 None
        _state = None
        _state_desc = ""

        if indexer.parser == "RenderSpider":
            error_flag, result_array = RenderSpider(indexer).search(keyword=keyword,
                                                                    page=page)
        elif indexer.parser == "TNodeSpider":
            error_flag, result_array = TNodeSpider(indexer).search(keyword=keyword,
                                                                   page=page)
        elif indexer.parser == "TorrentLeech":
            error_flag, result_array = TorrentLeech(indexer).search(keyword=keyword,
                                                            page=page)
        elif indexer.parser == "MTeamSpider":
            error_flag, result_array = MTeamSpider(indexer=indexer).inner_search(keyword=keyword, page=page)
        elif indexer.parser == "HaiDanSpider":
            error_flag, result_array, _state, _state_desc = self.__spider_search(spider=HaiDanSpider(),
                                                            indexer=indexer,
                                                            page=page,
                                                            keyword=keyword)
            # spider = HaiDanSpider()
            # spider.setparam(indexer=indexer,
            #                 keyword=keyword,
            #                 page=page)
            # error_flag, result_array = spider.search()
        else:
            if PluginsSpider().status(indexer=indexer):
                error_flag, result_array = PluginsSpider().search(keyword=keyword, 
                                                                  indexer=indexer, 
                                                                  page=page)

            else:
                error_flag, result_array, _state, _state_desc = self.__spider_search(spider=TorrentSpider(),
                                                                indexer=indexer,
                                                                page=page,
                                                                keyword=keyword)
        if error_flag:
            log.warn(f"【{self.client_name}】{indexer.name} 首页资源抓取未成功[{_state}]："
                     f"{_state_desc or '未提供细分状态'}")
        # 索引花费的时间
        seconds = round((datetime.datetime.now() - start_time).seconds, 1)

        # 索引统计
        self.dbhelper.insert_indexer_statistics(indexer=indexer.name,
                                                itype=self.client_id,
                                                seconds=seconds,
                                                result='N' if error_flag else 'Y')
        return result_array

    @staticmethod
    def __spider_search(spider, indexer, keyword=None, page=None, mtype=None, timeout=30):
        """
        根据关键字搜索单个站点

        实现已提炼为模块级 spider_search()，这里只是保持方法调用点不变。
        提炼原因：站点体检（app/sites/site_health.py）要跑同样的链路做探针搜索，
        不能把等待/超时判定逻辑复制一份 —— 复制品一旦与这里走偏，
        体检结论就会和真实搜索结论互相打脸。
        """
        return spider_search(spider=spider,
                             indexer=indexer,
                             keyword=keyword,
                             page=page,
                             mtype=mtype,
                             timeout=timeout)
