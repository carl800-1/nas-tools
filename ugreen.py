import json
import re
from urllib.parse import quote

import log
from app.mediaserver.client._base import _IMediaClient
from app.utils import RequestUtils, SystemUtils, ExceptionUtils, IpUtils
from app.utils.types import MediaType, MediaServerType
from config import Config


class UgreenClient(_IMediaClient):
    """
    绿联影视媒体服务器客户端
    """

    client_id = "ugreen"
    client_type = MediaServerType.UGREEN
    client_name = MediaServerType.UGREEN.value

    _client_config = {}
    _host = None
    _play_host = None
    _username = None
    _password = None
    _access_token = None
    _user_id = None
    _server_info = None

    def __init__(self, config=None):
        if config:
            self._client_config = config
        else:
            self._client_config = Config().get_config('ugreen')
        self.init_config()

    def init_config(self):
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
                self._access_token = self.__get_access_token()
                if self._access_token:
                    self._user_id = self.get_user_id()
                    self._server_info = self.get_server_info()

    def __get_access_token(self):
        if not self._host or not self._username or not self._password:
            return None
        req_url = f"{self._host}emby/Users/AuthenticateByName"
        try:
            data = {
                "Username": self._username,
                "Pw": self._password
            }
            res = RequestUtils().post_res(req_url, json=data)
            if res and res.status_code == 200:
                return res.json().get("AccessToken")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】用户认证失败：" + str(e))
        return None

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

    def get_user_id(self):
        """
        获取用户ID
        """
        if not self._host or not self._access_token:
            return None
        req_url = f"{self._host}Users?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                users = res.json()
                for user in users:
                    if user.get("Policy", {}).get("IsAdministrator"):
                        return user.get("Id")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Users出错：" + str(e))
        return None

    def get_server_info(self):
        """
        获取服务器信息
        """
        if not self._host or not self._access_token:
            return None
        req_url = f"{self._host}System/Info?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                return res.json()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接System/Info出错：" + str(e))
        return None

    def get_user_count(self):
        """
        获取用户数量
        """
        if not self._host or not self._access_token:
            return 0
        req_url = f"{self._host}emby/Users/Query?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                return res.json().get("TotalRecordCount", 0)
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Users/Query出错：" + str(e))
        return 0

    def get_activity_log(self, num):
        """
        获取活动记录
        """
        if not self._host or not self._access_token:
            return []
        req_url = f"{self._host}emby/System/ActivityLog/Entries?api_key={self._access_token}"
        ret_array = []
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                ret_json = res.json()
                items = ret_json.get('Items', [])
                for item in items[:num]:
                    if item.get("Type") == "AuthenticationSucceeded":
                        event_type = "LG"
                        event_date = SystemUtils.get_local_time(item.get("Date"))
                        event_str = "%s, %s" % (item.get("Name"), item.get("ShortOverview"))
                        activity = {"type": event_type, "event": event_str, "date": event_date}
                        ret_array.append(activity)
                    elif item.get("Type") in ["VideoPlayback", "VideoPlaybackStopped"]:
                        event_type = "PL"
                        event_date = SystemUtils.get_local_time(item.get("Date"))
                        event_str = item.get("Name")
                        activity = {"type": event_type, "event": event_str, "date": event_date}
                        ret_array.append(activity)
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接System/ActivityLog/Entries出错：" + str(e))
        return ret_array

    def get_medias_count(self):
        """
        获取电影、电视剧、音乐媒体数量
        """
        if not self._host or not self._access_token:
            return {"MovieCount": 0, "SeriesCount": 0, "MusicCount": 0, "EpisodeCount": 0}
        req_url = f"{self._host}emby/Items/Counts?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                return res.json()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Items/Counts出错：" + str(e))
        return {"MovieCount": 0, "SeriesCount": 0, "MusicCount": 0, "EpisodeCount": 0}

    def get_movies(self, title, year=None):
        """
        根据标题和年份，检查电影是否存在
        """
        if not self._host or not self._access_token:
            return []
        req_url = f"{self._host}emby/Items?IncludeItemTypes=Movie&Fields=ProductionYear&StartIndex=0&Recursive=true&SearchTerm={title}&Limit=10&IncludeSearchTypes=false&api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                res_items = res.json().get("Items", [])
                ret_movies = []
                for res_item in res_items:
                    if res_item.get('Name') == title and (
                            not year or str(res_item.get('ProductionYear')) == str(year)):
                        ret_movies.append({
                            'title': res_item.get('Name'),
                            'year': str(res_item.get('ProductionYear'))
                        })
                return ret_movies
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Items出错：" + str(e))
        return []

    def get_tv_episodes(self, item_id=None, title=None, year=None, tmdbid=None, season=None):
        """
        根据标题、年份、季查询电视剧所有集信息
        """
        if not self._host or not self._access_token:
            return []

        if not item_id:
            item_id = self.__get_series_id_by_name(title, year)
            if not item_id:
                return []

        if not season:
            season = ""
        req_url = f"{self._host}emby/Shows/{item_id}/Episodes?Season={season}&IsMissing=false&api_key={self._access_token}"
        try:
            res_json = RequestUtils().get_res(req_url)
            if res_json:
                res_items = res_json.json().get("Items", [])
                exists_episodes = []
                for res_item in res_items:
                    exists_episodes.append({
                        "season_num": res_item.get("ParentIndexNumber") or 0,
                        "episode_num": res_item.get("IndexNumber") or 0
                    })
                return exists_episodes
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Shows/Id/Episodes出错：" + str(e))
        return []

    def __get_series_id_by_name(self, name, year):
        """
        根据名称查询剧集ID
        """
        if not self._host or not self._access_token:
            return None
        req_url = f"{self._host}emby/Items?IncludeItemTypes=Series&Fields=ProductionYear&StartIndex=0&Recursive=true&SearchTerm={name}&Limit=10&IncludeSearchTypes=false&api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                res_items = res.json().get("Items", [])
                for res_item in res_items:
                    if res_item.get('Name') == name and (
                            not year or str(res_item.get('ProductionYear')) == str(year)):
                        return res_item.get('Id')
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Items出错：" + str(e))
        return None

    def get_no_exists_episodes(self, meta_info, season, total_num):
        """
        查询缺少哪几集
        """
        if not self._host or not self._access_token:
            return []

        if not season:
            season = 1

        exists_episodes = self.get_tv_episodes(
            title=meta_info.title,
            year=meta_info.year,
            tmdb_id=meta_info.tmdb_id,
            season=season
        )

        if not isinstance(exists_episodes, list):
            return []

        exists_episodes = [episode.get("episode_num") for episode in exists_episodes]
        total_episodes = [episode for episode in range(1, total_num + 1)]
        return list(set(total_episodes).difference(set(exists_episodes)))

    def get_episode_image_by_id(self, item_id, season_id, episode_id):
        """
        根据itemid、season_id、episode_id查询图片地址
        """
        if not self._host or not self._access_token:
            return ""

        req_url = f"{self._host}emby/Shows/{item_id}/Episodes?Season={season_id}&IsMissing=false&api_key={self._access_token}"
        try:
            res_json = RequestUtils().get_res(req_url)
            if res_json:
                res_items = res_json.json().get("Items", [])
                for res_item in res_items:
                    if res_item.get("IndexNumber") == episode_id:
                        img_url = self.get_remote_image_by_id(res_item.get("Id"), "Primary")
                        if not img_url and not IpUtils.is_internal(self._play_host) \
                                and res_item.get('ImageTags', {}).get('Primary'):
                            return f"{self._play_host}emby/Items/{res_item.get('Id')}/Images/Primary?maxHeight=225&maxWidth=400&tag={res_item.get('ImageTags', {}).get('Primary')}&quality=90"
                        return img_url
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Shows/Id/Episodes出错：" + str(e))
        return ""

    def get_remote_image_by_id(self, item_id, image_type):
        """
        根据ItemId查询远程图片地址
        """
        if not self._host or not self._access_token:
            return ""
        req_url = f"{self._host}emby/Items/{item_id}/RemoteImages?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                images = res.json().get("Images", [])
                for image in images:
                    if image.get("ProviderName") == "TheMovieDb" and image.get("Type") == image_type:
                        return image.get("Url")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Items/Id/RemoteImages出错：" + str(e))
        return ""

    def get_local_image_by_id(self, item_id, remote=True, inner=False):
        """
        根据ItemId查询本地图片地址
        """
        if not self._host or not self._access_token:
            return ""

        if not remote:
            image_url = f"{self._host}Items/{item_id}/Images/Primary"
            if inner:
                return self.get_nt_image_url(image_url)
            return image_url
        else:
            host = self._play_host or self._host
            image_url = f"{host}Items/{item_id}/Images/Primary"
            if IpUtils.is_internal(host):
                return self.get_nt_image_url(url=image_url, remote=True)
            return image_url

    def refresh_root_library(self):
        """
        刷新整个媒体库
        """
        if not self._host or not self._access_token:
            return
        req_url = f"{self._host}emby/Library/Refresh?api_key={self._access_token}"
        try:
            RequestUtils().post_res(req_url)
            log.info(f"【{self.client_name}】媒体库刷新请求已发送")
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Library/Refresh出错：" + str(e))

    def refresh_library_by_items(self, items):
        """
        按类型、名称、年份来刷新媒体库
        """
        if not items:
            return
        log.info(f"【{self.client_name}】开始刷新媒体库...")
        for item in items:
            if not item:
                continue
            self.refresh_root_library()
            break
        log.info(f"【{self.client_name}】媒体库刷新完成")

    def get_libraries(self):
        """
        获取媒体服务器所有媒体库列表
        """
        if not self._host or not self._access_token or not self._user_id:
            return []

        libraries = []
        req_url = f"{self._host}emby/Users/{self._user_id}/Views?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                items = res.json().get("Items", [])
                for library in items:
                    collection_type = library.get("CollectionType")
                    if collection_type == "movies":
                        library_type = MediaType.MOVIE.value
                    elif collection_type == "tvshows":
                        library_type = MediaType.TV.value
                    else:
                        continue

                    image = self.get_local_image_by_id(library.get("Id"), remote=False, inner=True)
                    libraries.append({
                        "id": library.get("Id"),
                        "name": library.get("Name"),
                        "path": library.get("Path"),
                        "type": library_type,
                        "image": image,
                        "link": f'{self._play_host or self._host}web/index.html#!/videos?parentId={library.get("Id")}'
                    })
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接User/Views出错：" + str(e))
        return libraries

    def get_iteminfo(self, itemid):
        """
        获取单个项目详情
        """
        if not itemid:
            return {}
        if not self._host or not self._access_token or not self._user_id:
            return {}
        req_url = f"{self._host}emby/Users/{self._user_id}/Items/{itemid}?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res and res.status_code == 200:
                return res.json()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
        return {}

    def get_items(self, parent):
        """
        获取媒体库中的所有媒体
        """
        if not parent:
            yield {}
        if not self._host or not self._access_token or not self._user_id:
            yield {}

        req_url = f"{self._host}emby/Users/{self._user_id}/Items?ParentId={parent}&api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res and res.status_code == 200:
                results = res.json().get("Items", [])
                for result in results:
                    if not result:
                        continue
                    if result.get("Type") in ["Movie", "Series"]:
                        item_info = self.get_iteminfo(result.get("Id"))
                        yield {
                            "id": result.get("Id"),
                            "library": item_info.get("ParentId"),
                            "type": item_info.get("Type"),
                            "title": item_info.get("Name"),
                            "originalTitle": item_info.get("OriginalTitle"),
                            "year": item_info.get("ProductionYear"),
                            "tmdbid": item_info.get("ProviderIds", {}).get("Tmdb"),
                            "imdbid": item_info.get("ProviderIds", {}).get("Imdb"),
                            "path": item_info.get("Path"),
                            "json": json.dumps(item_info)
                        }
                    elif "Folder" in result.get("Type"):
                        for item in self.get_items(parent=result.get('Id')):
                            yield item
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Users/Items出错：" + str(e))
        yield {}

    def get_play_url(self, item_id):
        """
        获取播放URL
        """
        if not item_id:
            return ""
        server_id = self._server_info.get("Id") if self._server_info else ""
        return f"{self._play_host or self._host}web/index.html#!/item?id={item_id}&context=home&serverId={server_id}"

    def get_playing_sessions(self):
        """
        获取正在播放的会话
        """
        if not self._host or not self._access_token:
            return []

        playing_sessions = []
        req_url = f"{self._host}emby/Sessions?api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res and res.status_code == 200:
                sessions = res.json()
                for session in sessions:
                    if session.get("NowPlayingItem") and not session.get("PlayState", {}).get("IsPaused"):
                        playing_sessions.append(session)
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
        return playing_sessions

    def get_webhook_message(self, message):
        """
        解析Webhook报文
        """
        event_item = {'event': message.get('Event', '')}

        if message.get('Item'):
            item_type = message.get('Item', {}).get('Type')
            if item_type == 'Episode':
                event_item['item_type'] = "TV"
                event_item['item_name'] = "%s S%sE%s %s" % (
                    message.get('Item', {}).get('SeriesName'),
                    str(message.get('Item', {}).get('ParentIndexNumber')).zfill(2),
                    str(message.get('Item', {}).get('IndexNumber')).zfill(2),
                    message.get('Item', {}).get('Name')
                )
                event_item['item_id'] = message.get('Item', {}).get('SeriesId')
                event_item['season_id'] = message.get('Item', {}).get('ParentIndexNumber')
                event_item['episode_id'] = message.get('Item', {}).get('IndexNumber')
            elif item_type == 'Audio':
                event_item['item_type'] = "AUD"
                event_item['item_name'] = message.get('Item', {}).get('Album')
                event_item['overview'] = message.get('Item', {}).get('FileName')
                event_item['item_id'] = message.get('Item', {}).get('AlbumId')
            else:
                event_item['item_type'] = "MOV"
                event_item['item_name'] = "%s (%s)" % (
                    message.get('Item', {}).get('Name'),
                    message.get('Item', {}).get('ProductionYear')
                )
                event_item['item_path'] = message.get('Item', {}).get('Path')
                event_item['item_id'] = message.get('Item', {}).get('Id')

            event_item['tmdb_id'] = message.get('Item', {}).get('ProviderIds', {}).get('Tmdb')
            overview = message.get('Item', {}).get('Overview')
            if overview:
                event_item['overview'] = overview[:100] + "..." if len(overview) > 100 else overview
            else:
                event_item['overview'] = ""

            event_item['percentage'] = message.get('TranscodingInfo', {}).get('CompletionPercentage')
            if not event_item['percentage']:
                if message.get('PlaybackInfo', {}).get('PositionTicks'):
                    event_item['percentage'] = message.get('PlaybackInfo', {}).get('PositionTicks') / \
                                              message.get('Item', {}).get('RunTimeTicks') * 100

            event_item['play_url'] = f"/open?url={quote(self.get_play_url(event_item.get('item_id')))}&type=ugreen"

        if message.get('Session'):
            event_item['ip'] = message.get('Session').get('RemoteEndPoint')
            event_item['device_name'] = message.get('Session').get('DeviceName')
            event_item['client'] = message.get('Session').get('Client')

        if message.get("User"):
            event_item['user_name'] = message.get("User").get('Name')

        return event_item

    def get_resume(self, num=12):
        """
        获取继续观看列表
        """
        if not self._host or not self._access_token or not self._user_id:
            return []

        req_url = f"{self._host}Users/{self._user_id}/Items/Resume?Limit={num}&MediaTypes=Video&api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                result = res.json().get("Items", [])
                ret_resume = []
                for item in result:
                    if item.get("Type") not in ["Movie", "Episode"]:
                        continue

                    item_type = MediaType.MOVIE.value if item.get("Type") == "Movie" else MediaType.TV.value
                    link = self.get_play_url(item.get("Id"))

                    if item_type == MediaType.MOVIE.value:
                        title = item.get("Name")
                        if item.get("BackdropImageTags"):
                            image = f"{self._host}Items/{item.get('Id')}/Images/Backdrop?tag={item.get('BackdropImageTags')[0]}&fillWidth=666&api_key={self._access_token}"
                        else:
                            image = self.get_local_image_by_id(item.get("Id"), remote=False, inner=True)
                    else:
                        if item.get("ParentIndexNumber") == 1:
                            title = f'{item.get("SeriesName")} 第{item.get("IndexNumber")}集'
                        else:
                            title = f'{item.get("SeriesName")} 第{item.get("ParentIndexNumber")}季第{item.get("IndexNumber")}集'
                        image = self.get_local_image_by_id(item.get("SeriesId"), remote=False, inner=True)

                    ret_resume.append({
                        "id": item.get("Id"),
                        "name": title,
                        "type": item_type,
                        "image": image,
                        "link": link,
                        "percent": item.get("UserData", {}).get("PlayedPercentage")
                    })
                return ret_resume
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Users/Items/Resume出错：" + str(e))
        return []

    def get_latest(self, num=20):
        """
        获取最近添加
        """
        if not self._host or not self._access_token or not self._user_id:
            return []

        req_url = f"{self._host}Users/{self._user_id}/Items/Latest?Limit={num}&MediaTypes=Video&api_key={self._access_token}"
        try:
            res = RequestUtils().get_res(req_url)
            if res:
                result = res.json().get("Items", [])
                ret_latest = []
                for item in result:
                    if item.get("Type") not in ["Movie", "Series"]:
                        continue

                    item_type = MediaType.MOVIE.value if item.get("Type") == "Movie" else MediaType.TV.value
                    link = self.get_play_url(item.get("Id"))
                    image = self.get_local_image_by_id(item_id=item.get("Id"), remote=False, inner=True)

                    ret_latest.append({
                        "id": item.get("Id"),
                        "name": item.get("Name"),
                        "type": item_type,
                        "image": image,
                        "link": link
                    })
                return ret_latest
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            log.error(f"【{self.client_name}】连接Users/Items/Latest出错：" + str(e))
        return []

    def get_host(self):
        """
        获取 host 地址
        """
        return self._host