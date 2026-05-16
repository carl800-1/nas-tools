import json
import re
from urllib.parse import quote, urljoin

import log
from app.mediaserver.client._base import _IMediaClient
from app.utils import RequestUtils, SystemUtils, ExceptionUtils, IpUtils
from app.utils.types import MediaType, MediaServerType
from config import Config


class Ugreen(_IMediaClient):
    """
    绿联影视客户端
    """
    # 媒体服务器ID
    client_id = "ugreen"
    # 媒体服务器类型
    client_type = MediaServerType.UGREEN
    # 媒体服务器名称
    client_name = MediaServerType.UGREEN.value

    # 私有属性
    _client_config = {}
    _host = None
    _play_host = None
    _username = None
    _password = None
    _token = None
    _api_base = None

    def __init__(self, config=None):
        if config:
            self._client_config = config
        else:
            self._client_config = Config().get_config('ugreen')
        self.init_config()

    def init_config(self):
        """初始化配置"""
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
            
            # 绿联API基础路径
            self._api_base = f"{self._host}ugreen/v1/"
            
            # 获取token
            if self._host and self._username and self._password:
                self._token = self.__get_token()

    def __get_token(self):
        """
        获取绿联API访问token
        通过登录接口获取
        """
        try:
            # 绿联登录接口
            login_url = f"{self._host}api/login"
            login_data = {
                "username": self._username,
                "password": self._password
            }
            
            res = RequestUtils(
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
            ).post_res(login_url, json=login_data)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    # 从登录响应中获取token
                    token = result.get('data', {}).get('token') or result.get('data', {}).get('api_token')
                    if token:
                        log.info(f"【{self.client_name}】登录成功，获取到访问令牌")
                        return token
                    else:
                        log.error(f"【{self.client_name}】登录响应中未找到token")
                else:
                    log.error(f"【{self.client_name}】登录失败：{result.get('msg', '未知错误')}")
            else:
                log.error(f"【{self.client_name}】登录请求失败，状态码：{res.status_code if res else '无响应'}")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】登录出错：{str(e)}")
        return None

    def __get_api_url(self, endpoint):
        """构建API URL"""
        if self._token:
            separator = "&" if "?" in endpoint else "?"
            return f"{self._api_base}{endpoint}{separator}token={self._token}"
        return f"{self._api_base}{endpoint}"

    @classmethod
    def match(cls, ctype):
        return True if ctype in [cls.client_id, cls.client_type, cls.client_name] else False

    def get_type(self):
        return self.client_type

    def get_status(self):
        """
        测试连通性
        """
        return True if self.get_medias_count() else False

    def get_user_count(self):
        """
        获得用户数量
        绿联影视暂不支持获取用户数量，返回1表示主用户
        """
        return 1 if self._token else 0

    def get_activity_log(self, num):
        """
        获取活动记录
        绿联影视暂不支持此功能
        """
        return []

    def get_medias_count(self):
        """
        获得电影、电视剧媒体数量
        """
        if not self._host or not self._token:
            return {}
        
        try:
            # 获取媒体库统计信息
            req_url = self.__get_api_url("media/library/stats")
            res = RequestUtils().get_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    data = result.get('data', {})
                    return {
                        "MovieCount": data.get('movie_count', 0),
                        "SeriesCount": data.get('tv_count', 0),
                        "SongCount": 0
                    }
                else:
                    log.error(f"【{self.client_name}】获取媒体数量失败：{result.get('msg', '未知错误')}")
            else:
                log.error(f"【{self.client_name}】获取媒体数量请求失败")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体数量出错：{str(e)}")
        
        return {}

    def get_movies(self, title, year=None):
        """
        根据标题和年份，检查电影是否存在
        """
        if not self._host or not self._token:
            return None
        
        try:
            # 搜索媒体
            req_url = self.__get_api_url(f"media/search?keyword={quote(title)}&type=movie")
            res = RequestUtils().get_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    items = result.get('data', {}).get('items', [])
                    ret_movies = []
                    for item in items:
                        item_title = item.get('title', '')
                        item_year = str(item.get('year', ''))
                        
                        if item_title == title and (not year or item_year == str(year)):
                            ret_movies.append({
                                'title': item_title,
                                'year': item_year
                            })
                    return ret_movies
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】搜索电影出错：{str(e)}")
        
        return []

    def get_tv_episodes(self, item_id=None, title=None, year=None, tmdbid=None, season=None):
        """
        根据标题、年份、季查询电视剧所有集信息
        """
        if not self._host or not self._token:
            return None
        
        try:
            # 如果没有item_id，先搜索获取
            if not item_id and title:
                req_url = self.__get_api_url(f"media/search?keyword={quote(title)}&type=tv")
                res = RequestUtils().get_res(req_url)
                
                if res and res.status_code == 200:
                    result = res.json()
                    if result.get('code') == 200:
                        items = result.get('data', {}).get('items', [])
                        for item in items:
                            if item.get('title') == title and (not year or str(item.get('year')) == str(year)):
                                item_id = item.get('id')
                                break
            
            if not item_id:
                return []
            
            # 获取剧集详情
            req_url = self.__get_api_url(f"media/tv/{item_id}/episodes")
            if season:
                req_url += f"&season={season}"
            
            res = RequestUtils().get_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    episodes = result.get('data', {}).get('episodes', [])
                    exists_episodes = []
                    for ep in episodes:
                        exists_episodes.append({
                            "season_num": ep.get("season_number", 1),
                            "episode_num": ep.get("episode_number", 0)
                        })
                    return exists_episodes
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取剧集信息出错：{str(e)}")
        
        return []

    def get_no_exists_episodes(self, meta_info, season, total_num):
        """
        根据标题、年份、季、总集数，查询缺少哪几集
        """
        if not self._host or not self._token:
            return None
        
        if not season:
            season = 1
        
        exists_episodes = self.get_tv_episodes(
            title=meta_info.title,
            year=meta_info.year,
            season=season
        )
        
        if not isinstance(exists_episodes, list):
            return None
        
        exists_episodes = [episode.get("episode_num") for episode in exists_episodes]
        total_episodes = [episode for episode in range(1, total_num + 1)]
        return list(set(total_episodes).difference(set(exists_episodes)))

    def get_remote_image_by_id(self, item_id, image_type):
        """
        根据ItemId查询远程图片地址
        绿联影视使用本地图片，此方法返回空
        """
        return None

    def get_local_image_by_id(self, item_id, remote=True, inner=False):
        """
        根据ItemId查询本地图片地址
        """
        if not self._host or not self._token or not item_id:
            return None
        
        try:
            req_url = self.__get_api_url(f"media/item/{item_id}/poster")
            
            if not remote:
                return req_url
            else:
                host = self._play_host or self._host
                image_url = f"{host}ugreen/v1/media/item/{item_id}/poster?token={self._token}"
                if IpUtils.is_internal(host):
                    return self.get_nt_image_url(url=image_url, remote=True)
                return image_url
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取图片地址出错：{str(e)}")
        
        return None

    def refresh_root_library(self):
        """
        刷新整个媒体库
        """
        if not self._host or not self._token:
            return False
        
        try:
            req_url = self.__get_api_url("media/library/refresh")
            res = RequestUtils().post_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    log.info(f"【{self.client_name}】媒体库刷新成功")
                    return True
                else:
                    log.error(f"【{self.client_name}】刷新媒体库失败：{result.get('msg', '未知错误')}")
            else:
                log.error(f"【{self.client_name}】刷新媒体库请求失败")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】刷新媒体库出错：{str(e)}")
        
        return False

    def refresh_library_by_items(self, items):
        """
        按类型、名称、年份来刷新媒体库
        """
        if not items:
            return
        
        log.info(f"【{self.client_name}】开始刷新绿联影视媒体库...")
        
        # 绿联影视暂不支持按项目刷新，直接刷新整个库
        self.refresh_root_library()
        
        log.info(f"【{self.client_name}】绿联影视媒体库刷新完成")

    def get_libraries(self):
        """
        获取媒体服务器所有媒体库列表
        """
        if not self._host or not self._token:
            return []
        
        try:
            req_url = self.__get_api_url("media/libraries")
            res = RequestUtils().get_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    libraries = []
                    items = result.get('data', {}).get('libraries', [])
                    
                    for library in items:
                        library_type = library.get('type', '')
                        if library_type == 'movie':
                            media_type = MediaType.MOVIE.value
                        elif library_type == 'tv':
                            media_type = MediaType.TV.value
                        else:
                            continue
                        
                        library_id = library.get('id', '')
                        image = self.get_local_image_by_id(library_id, remote=False, inner=True)
                        
                        libraries.append({
                            "id": library_id,
                            "name": library.get('name', ''),
                            "path": library.get('path', ''),
                            "type": media_type,
                            "image": image,
                            "link": f'{self._play_host or self._host}#/media/{library_id}'
                        })
                    
                    return libraries
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体库列表出错：{str(e)}")
        
        return []

    def get_items(self, parent):
        """
        获取媒体库中的所有媒体
        """
        if not parent:
            yield {}
        if not self._host or not self._token:
            yield {}
        
        try:
            req_url = self.__get_api_url(f"media/library/{parent}/items")
            res = RequestUtils().get_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    items = result.get('data', {}).get('items', [])
                    for item in items:
                        if not item:
                            continue
                        
                        item_type = item.get('type', '')
                        if item_type == 'movie':
                            media_type = 'Movie'
                        elif item_type == 'tv':
                            media_type = 'Series'
                        else:
                            continue
                        
                        yield {
                            "id": item.get('id'),
                            "library": parent,
                            "type": media_type,
                            "title": item.get('title'),
                            "originalTitle": item.get('original_title'),
                            "year": item.get('year'),
                            "tmdbid": item.get('tmdb_id'),
                            "imdbid": item.get('imdb_id'),
                            "path": item.get('path'),
                            "json": str(item)
                        }
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取媒体列表出错：{str(e)}")
        
        yield {}

    def get_play_url(self, item_id):
        """
        拼装媒体播放链接
        """
        return f"{self._play_host or self._host}#/player/{item_id}"

    def get_playing_sessions(self):
        """
        获取正在播放的会话
        绿联影视暂不支持此功能
        """
        return []

    def get_webhook_message(self, message):
        """
        解析Webhook报文
        绿联影视暂不支持Webhook
        """
        return {}

    def get_resume(self, num=12):
        """
        获得继续观看
        绿联影视暂不支持此功能
        """
        return []

    def get_latest(self, num=20):
        """
        获得最近更新
        """
        if not self._host or not self._token:
            return []
        
        try:
            req_url = self.__get_api_url(f"media/latest?limit={num}")
            res = RequestUtils().get_res(req_url)
            
            if res and res.status_code == 200:
                result = res.json()
                if result.get('code') == 200:
                    ret_latest = []
                    items = result.get('data', {}).get('items', [])
                    
                    for item in items:
                        item_type = item.get('type', '')
                        if item_type == 'movie':
                            media_type = MediaType.MOVIE.value
                        elif item_type == 'tv':
                            media_type = MediaType.TV.value
                        else:
                            continue
                        
                        item_id = item.get('id')
                        link = self.get_play_url(item_id)
                        image = self.get_local_image_by_id(item_id, remote=False, inner=True)
                        
                        ret_latest.append({
                            "id": item_id,
                            "name": item.get('title'),
                            "type": media_type,
                            "image": image,
                            "link": link
                        })
                    
                    return ret_latest
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】获取最近更新出错：{str(e)}")
        
        return []

    def get_host(self):
        """
        获取 host 地址
        """
        return self._host
