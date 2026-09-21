import requests

import log
from app.utils import RequestUtils, MteamUtils
from config import Config


class MTeamSpider(object):
    _appid = "nastool"
    _req = None
    _token = None
    _api_url = "%s/api/torrent/search"
    _pageurl = "%sdetail/%s"

    def __init__(self, indexer):
        if indexer:
            self._indexerid = indexer.id
            self._domain = indexer.domain
            self._name = indexer.name
            self._proxy = Config().get_proxies() if indexer.proxy else None
            self._cookie = indexer.cookie
            self._ua = indexer.ua
        api_url = MteamUtils.build_api_url(self._domain, "/api/torrent/search")
        if not api_url:
            # 域名不含 m-team 时推导失败，拼出 "None/api/..." 会变成一个
            # 看似合法实则必然失败的 URL，不如在调用前拦下
            log.error(f"【MTeam】索引器 {self._name} 的域名 {self._domain} 无法推导 API 地址")
            raise Exception(f"M-Team 索引器域名配置有误：{self._domain}")
        self._api_url = api_url
        self.init_config()

    def init_config(self):
        session = requests.session()
        self._req = MteamUtils.buildRequestUtils(proxies=self._proxy, session=session, content_type="application/json",
            accept_type="application/json", api_key=MteamUtils.get_api_key(self._domain), headers=self._ua, timeout=10)

    def get_discount(self, discount):
        if discount == "PERCENT_50":
            return 1.0, 0.5
        elif discount == "NORMAL":
            return 1.0, 1.0
        elif discount == "PERCENT_70":
            return 1.0, 0.7
        elif discount == "FREE":
            return 1.0, 0.0
        elif discount == "_2X_FREE":
            return 2.0, 0.0
        elif discount == "_2X":
            return 2.0, 1.0
        elif discount == "_2X_PERCENT_50":
            return 2.0, 0.5

    def inner_search(self, keyword, page=None):
        if page:
            page = int(page) + 1
        else:
            page = 1

        param = {
            "categories":[],
            "keyword": keyword,
            "mode":"normal",
            "pageNumber": page,
            "pageSize":100,
            "visible":1
        }
        # 关于「按 IMDb ID 检索」：上游在这里留了一段被注释掉的代码
        # （params['search_imdb'] = imdb_id），但它引用的 params / search_imdb
        # 与上面 param 的实际结构（keyword + mode）对不上，说明当时的接口契约与现在不同。
        # 在拿到 MTeam 当前接口契约或实测结果之前不要贸然启用 ——
        # 猜错请求格式会直接把一个本来能用的索引器搞坏。
        # 按 ID 兜底的能力已在插件通道实现（Jackett / Prowlarr 的 search_by_imdb()），
        # 需要时优先走插件通道。
        res = self._req.post_res(url=self._api_url, json=param)
        torrents = []
        if res and res.status_code == 200:
            results = res.json().get('data') or {}
            # TODO 遍历多个页面获取数据
            totalPages = results.get("totalPages")
            total = results.get("total")
            curData = results.get('data') or []

            for result in curData:
                status = result.get("status")
                up_discount, down_discount = self.get_discount(status.get('discount'))
                torrent = {'indexer': self._indexerid,
                           'title': result.get('name'),
                           'description': result.get('smallDescr'),
                           # enlosure 给 pageurl，后续下载种子的时候，从接口中解析，这里只是为了跳过中间的检验流程
                           'enclosure': self._pageurl % (self._domain, result.get('id')),
                           'size': result.get('size'),
                           'seeders': status.get('seeders'),
                           'peers': status.get('leechers'),
                           # 'freeleech': result.get('discount'),
                           'downloadvolumefactor': down_discount,
                           'uploadvolumefactor': up_discount,
                           'page_url': self._pageurl % (self._domain, result.get('id')),
                           'imdbid': result.get('episode_info').get('imdb') if result.get('episode_info') else ''}
                torrents.append(torrent)
        elif res is not None:
            log.warn(f"【INDEXER】{self._name} 搜索失败，错误码：{res.status_code}")
            return True, []
        else:
            log.warn(f"【INDEXER】{self._name} 搜索失败，无法获取请求结果")
            return True, []
        return False, torrents

    def search(self, keyword, page=None):
        if not keyword:
            return True, []

        return self.inner_search(keyword, page)
