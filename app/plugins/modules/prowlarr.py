import requests
from datetime import datetime, timedelta
from threading import Event
import xml.dom.minidom
from jinja2 import Template
import re

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.utils import RequestUtils
from app.indexer.indexerConf import IndexerConf
from app.utils import ExceptionUtils, StringUtils

from app.plugins.modules._base import _IPluginModule
from config import Config


class Prowlarr(_IPluginModule):
    # 插件名称
    module_name = "Prowlarr"
    # 插件描述
    module_desc = "让内荐索引器支持检索Prowlarr站点资源"
    # 插件图标
    module_icon = "prowlarr.png"
    # 主题色
    module_color = "#7F4A28"
    # 插件版本
    module_version = "1.5"
    # 插件作者
    module_author = "hsuyelin"
    # 作者主页
    author_url = "https://github.com/hsuyelin"
    # 插件配置项ID前缀
    module_config_prefix = "prowlarr"
    # 加载顺序
    module_order = 16
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    eventmanager = None
    _scheduler = None
    _enable = False
    _host = ""
    _api_key = ""
    _onlyonce = False
    _sites = None

    # 退出事件
    _event = Event()

    @staticmethod
    def get_fields():
        return [
            # 同一板块
            {
                'type': 'div',
                'content': [
                    # 同一行
                    [
                        {
                            'title': 'Prowlarr地址',
                            'required': "required",
                            'tooltip': 'Prowlarr访问地址和端口，如为https需加https://前缀。注意需要先在Prowlarr中添加搜刮器，同时勾选所有搜刮器后搜索一次，才能正常测试通过和使用',
                            'type': 'text',
                            'content': [
                                {
                                    'id': 'host',
                                    'placeholder': 'http://127.0.0.1:9696',
                                }
                            ]
                        },
                        {
                            'title': 'Api Key',
                            'required': "required",
                            'tooltip': '在Prowlarr->Settings->General->Security-> API Key中获取',
                            'type': 'text',
                            'content': [
                                {
                                    'id': 'api_key',
                                    'placeholder': '',
                                }
                            ]
                        }
                    ],
                    [
                        {
                            'title': '更新周期',
                            'required': "",
                            'tooltip': '索引列表更新周期，支持5位cron表达式，默认每24小时运行一次',
                            'type': 'text',
                            'content': [
                                {
                                    'id': 'cron',
                                    'placeholder': '0 0 */24 * *',
                                }
                            ]
                        }
                    ],
                    [
                        {
                            'title': '立即运行一次',
                            'required': "",
                            'tooltip': '打开后立即运行一次获取索引器列表，否则需要等到预先设置的更新周期才会获取',
                            'type': 'switch',
                            'id': 'onlyonce',
                        }
                    ]
                ]
            },
        ]

    def get_page(self):
        """
        插件的额外页面，返回页面标题和页面内容
        :return: 标题，页面内容，确定按钮响应函数
        """
        if not isinstance(self._sites, list) or len(self._sites) <= 0:
            return None, None, None
        template = """
          <div class="table-responsive table-modal-body">
            <table class="table table-vcenter card-table table-hover table-striped">
              <thead>
              {% if IndexersCount > 0 %}
              <tr>
                <th>id</th>
                <th>索引</th>
                <th>是否公开</th>
                <th></th>
              </tr>
              {% endif %}
              </thead>
              <tbody>
              {% if IndexersCount > 0 %}
                {% for Item in Indexers %}
                  <tr id="indexer_{{ Item.id }}">
                    <td>{{ Item.id }}</td>
                    <td>{{ Item.domain }}</td>
                    <td>{{ Item.public }}</td>
                  </tr>
                {% endfor %}
              {% endif %}
              </tbody>
            </table>
          </div>
        """
        return "索引列表", Template(template).render(IndexersCount=len(self._sites), Indexers=self._sites), None

    def init_config(self, config=None):
        self.info(f"初始化配置{config}")

        if config:
            self._host = config.get("host")
            if self._host:
                if not self._host.startswith('http'):
                    self._host = "http://" + self._host
                if self._host.endswith('/'):
                    self._host = self._host.rstrip('/')
            self._api_key = config.get("api_key")
            self._enable = self.get_status()
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            if not StringUtils.is_string_and_not_empty(self._cron):
                self._cron = "0 0 */24 * *"


        # 停止现有任务
        self.stop_service()

        # 启动定时任务 & 立即运行一次
        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=Config().get_timezone())

            if self._cron:
                self.info(f"【{self.module_name}】 索引更新服务启动，周期：{self._cron}")
                self._scheduler.add_job(self.get_status, CronTrigger.from_crontab(self._cron))

            if self._onlyonce:
                self.info(f"【{self.module_name}】开始获取索引器状态")
                self._scheduler.add_job(self.get_status, 'date',
                                        run_date=datetime.now(tz=pytz.timezone(Config().get_timezone())) + timedelta(
                                            seconds=3))
                # 关闭一次性开关
                self._onlyonce = False
                self.__update_config()

            if self._cron or self._onlyonce:
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_status(self):
        """
        检查连通性
        :return: True、False
        """
        if not self._api_key or not self._host:
            return False
        self._sites = self.get_indexers()
        return True if isinstance(self._sites, list) and len(self._sites) > 0 else False

    def get_state(self):
        return self._enable

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            self.error(f"【{self.module_name}】停止插件错误: {str(e)}")

    def __update_config(self):
        """
        更新优选插件配置
        """
        self.update_config({
            "onlyonce": False,
            "cron": self._cron,
            "host": self._host,
            "api_key": self._api_key
        })

    def get_indexers(self, check=True, indexer_id=None, public=True, plugins=True):
        """
        获取配置的prowlarr indexer
        :return: indexer 信息 [(indexerId, indexerName, url)]
        """
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": Config().get_ua(),
            "X-Api-Key": self._api_key,
            "Accept": "application/json, text/javascript, */*; q=0.01"
        }
        indexer_query_url = f"{self._host}/api/v1/indexerstats"
        try:
            ret = RequestUtils(headers=headers).get_res(indexer_query_url)
            if not ret:
                return []
            if not RequestUtils.check_response_is_valid_json(ret):
                self.info(f"【{self.module_name}】参数设置不正确，请检查所有的参数是否填写正确")
                return []
            if not ret.json():
                return []
            ret_indexers = ret.json()["indexers"]
            if not ret or ret_indexers == [] or ret is None:
                return []

            # /api/v1/indexerstats 不返回站点的公开/私有属性。
            # 老代码在这里硬编码 public=True，后果是私有站被当成公开站，
            # 而 _base.py 里「做种数为0则过滤」这条规则只对非公开站生效，
            # 于是 Prowlarr 接入的私有站会漏过 0 做种的死种。
            # 这里额外取一次站点定义拿 privacy，取不到时保守按「非公开」处理。
            privacy_map = self.__get_privacy_map()

            indexers = [IndexerConf({"id": f'{v["indexerName"]}-prowlarr',
                                 "name": f'{v["indexerName"]}(Prowlarr)',
                                 "domain": f'{self._host}/api/v1/indexer/{v["indexerId"]}',
                                 "public": privacy_map.get(v["indexerName"], False),
                                 "builtin": False,
                                 "proxy": True,
                                 "parser": self.module_name})
                    for v in ret_indexers]
            return indexers
        except Exception as e2:
            ExceptionUtils.exception_traceback(e2)
            return []

    def __get_privacy_map(self):
        """
        取「索引器名 -> 是否公开」的映射

        indexerstats 接口不含公开属性，需另查 /api/v1/indexer 定义接口。
        查询失败或字段缺失时返回空字典，调用方会保守地按「非公开」处理
        （即启用做种数为0过滤，与内置私有站点行为一致）。

        :return: {索引器名: bool}
        """
        try:
            ret = RequestUtils(headers={
                "User-Agent": Config().get_ua(),
                "X-Api-Key": self._api_key,
                "Accept": "application/json, text/javascript, */*; q=0.01"
            }).get_res(f"{self._host}/api/v1/indexer")
            if not ret or not RequestUtils.check_response_is_valid_json(ret):
                return {}
            return {v.get("name"): v.get("privacy") == "public"
                    for v in ret.json() if v.get("name")}
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return {}

    def __indexer_headers(self):
        """
        构造 Prowlarr API 请求头
        """
        return {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": Config().get_ua(),
            "X-Api-Key": self._api_key,
            "Accept": "application/json, text/javascript, */*; q=0.01"
        }

    def __extract_indexer_id(self, indexer):
        """
        从站点 domain（形如 http://host:9696/api/v1/indexer/12）里取出 indexerId

        :return: id 字符串，取不到返回空串
        """
        match = re.search(r"/indexer/([^/]+)", indexer.domain or "")
        return match.group(1) if match else ""

    def __parse_releases(self, entries):
        """
        把 Prowlarr 搜索响应条目转成统一的种子字典

        Prowlarr 的搜索响应本来就带促销系数与 IMDb ID，
        老代码把这几项一律写成 None，导致 Prowlarr 站点上
        「免费/促销」过滤失效（downloadvolumefactor 为 None 时按 1.0 处理），
        以及 IMDb 维度匹配完全用不上。这里按实际字段回填。

        :param entries: /api/v1/search 返回的 JSON 数组
        :return: 种子字典列表
        """
        torrents = []
        for entry in (entries or []):
            download_volume_factor = entry.get("downloadVolumeFactor")
            upload_volume_factor = entry.get("uploadVolumeFactor")
            torrents.append({
                'indexer_id': entry.get("indexerId"),
                'indexer': entry.get("indexer"),
                'title': entry.get("title"),
                'enclosure': entry.get("downloadUrl"),
                'description': entry.get("sortTitle"),
                'size': entry.get("size"),
                'seeders': entry.get("seeders"),
                # Prowlarr 没有 torznab 的 peers 字段，用 leechers 近似
                'peers': entry.get("leechers"),
                'freeleech': (download_volume_factor == 0)
                             if download_volume_factor is not None else None,
                'downloadvolumefactor': download_volume_factor,
                'uploadvolumefactor': upload_volume_factor,
                'page_url': entry.get("guid"),
                'imdbid': self.__normalize_imdb_id(entry.get("imdbId"))
            })
        return torrents

    def __search_api(self, api_url):
        """
        调 Prowlarr 搜索接口并解析

        :param api_url: 完整的搜索 URL
        :return: 种子字典列表；请求失败或返回非 JSON 时为空列表
        """
        try:
            ret = RequestUtils(headers=self.__indexer_headers()).get_res(api_url)
            if not ret:
                return []
            if not RequestUtils.check_response_is_valid_json(ret):
                self.info(f"【{self.module_name}】参数设置不正确，请检查所有的参数是否填写正确")
                return []
            return self.__parse_releases(ret.json())
        except Exception as e2:
            ExceptionUtils.exception_traceback(e2)
            return []

    def search(self, indexer,
               keyword,
               page):
        """
        根据关键字多线程检索
        """
        if not indexer or not keyword:
            return None
        self.info(f"【{self.module_name}】开始检索Indexer：{indexer.name} ...")

        indexerId = self.__extract_indexer_id(indexer)
        if not StringUtils.is_string_and_not_empty(indexerId):
            self.info(f"【{self.module_name}】{indexer.name} 索引id为空")
            return []

        api_url = f"{self._host}/api/v1/search?query={keyword}&indexerIds={indexerId}" \
                  f"&type=search&limit=100&offset={int(page or 0) * 100}"
        return self.__search_api(api_url)

    def search_by_imdb(self, indexer,
                       imdb_id,
                       page=0):
        """
        按 IMDb ID 检索（Prowlarr 的 type=movie）

        为什么需要它：中文片名在 PT 站的命中率往往很低（译名差异、站点只留原名），
        而 IMDb 编号是全球唯一的、不受中英文与译名影响，这一轮能把这类情况兜回来。

        注意：并非所有 indexer 都支持按 ID 检索，不支持时 Prowlarr 返回空，
        这属于预期情况 —— 本方法返回空列表，由调用方静默跳过，不影响关键词检索。

        :param indexer: 站点配置
        :param imdb_id: 形如 tt0111161
        :param page: 页码
        :return: 种子字典列表，不支持或失败时为空列表
        """
        if not indexer or not imdb_id:
            return []
        indexerId = self.__extract_indexer_id(indexer)
        if not StringUtils.is_string_and_not_empty(indexerId):
            return []
        self.info(f"【{self.module_name}】开始按 IMDb ID 检索：{indexer.name} / {imdb_id} ...")
        api_url = f"{self._host}/api/v1/search?query={imdb_id}&indexerIds={indexerId}" \
                  f"&type=movie&limit=100&offset={int(page or 0) * 100}"
        torrents = self.__search_api(api_url)
        if not torrents:
            self.info(f"【{self.module_name}】{indexer.name} 按 IMDb ID 未检索到数据"
                      f"（该 indexer 可能不支持按 ID 检索）")
        else:
            self.warn(f"【{self.module_name}】{indexer.name} 按 IMDb ID 返回数据：{len(torrents)}")
        return torrents

    @staticmethod
    def __normalize_imdb_id(imdb_id):
        """
        把 Prowlarr 返回的 IMDb ID 归一成 tt 前缀格式

        Prowlarr 不同版本有的返回 "tt0111161"，有的返回纯数字 111161，
        而 _base.py 的 IMDb 匹配是字符串相等比较（str(imdbid) == str(match_media.imdb_id)，
        后者形如 tt0111161），不归一化则永远匹配不上。

        :param imdb_id: 原始值，可能为 None / 数字 / 字符串
        :return: 形如 tt0111161 的字符串；无法识别时原样返回字符串形式；空值返回 None
        """
        if imdb_id is None or imdb_id == "":
            return None
        text = str(imdb_id).strip()
        if not text:
            return None
        if text.isdigit():
            return "tt" + text.zfill(7)
        return text