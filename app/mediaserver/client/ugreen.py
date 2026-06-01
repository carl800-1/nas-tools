from app.mediaserver.client._base import _IMediaClient
from app.utils.types import MediaServerType


class UgreenClient(_IMediaClient):
    """
    绿联影视媒体服务器客户端
    """

    client_id = "ugreen"
    client_type = MediaServerType.UGREEN
    client_name = MediaServerType.UGREEN.value

    def __init__(self, conf=None):
        self.conf = conf or {}
        self.host = self.conf.get("host") or "http://127.0.0.1:8096"
        self.api_key = self.conf.get("api_key") or ""
        self.play_host = self.conf.get("play_host") or self.host

    def match(self, ctype):
        """
        匹配实例
        """
        return str(ctype).lower() == "ugreen"

    def get_type(self):
        """
        获取媒体服务器类型
        """
        return self.client_type

    def get_status(self):
        """
        检查连通性
        """
        return True

    def get_user_count(self):
        """
        获得用户数量
        """
        return 0

    def get_activity_log(self, num):
        """
        获取活动记录
        """
        return []

    def get_medias_count(self):
        """
        获得电影、电视剧、动漫媒体数量
        """
        return {"Movie": 0, "Series": 0, "Music": 0, "Episodes": 0}

    def get_movies(self, title, year):
        """
        根据标题和年份，检查电影是否存在
        """
        return []

    def get_tv_episodes(self, item_id=None, title=None, year=None, tmdbid=None, season=None):
        """
        根据标题、年份、季查询电视剧所有集信息
        """
        return []

    def get_no_exists_episodes(self, meta_info, season, total_num):
        """
        查询缺少哪几集
        """
        return []

    def get_remote_image_by_id(self, item_id, image_type):
        """
        根据ItemId查询远程图片地址
        """
        return ""

    def get_local_image_by_id(self, item_id):
        """
        根据ItemId查询本地图片地址
        """
        return ""

    def refresh_root_library(self):
        """
        刷新整个媒体库
        """
        pass

    def refresh_library_by_items(self, items):
        """
        按类型、名称、年份来刷新媒体库
        """
        pass

    def get_libraries(self):
        """
        获取媒体服务器所有媒体库列表
        """
        return []

    def get_items(self, parent):
        """
        获取媒体库中的所有媒体
        """
        return []

    def get_play_url(self, item_id):
        """
        获取播放URL
        """
        return ""

    def get_playing_sessions(self):
        """
        获取正在播放的会话
        """
        return []

    def get_webhook_message(self, message):
        """
        解析Webhook报文
        """
        return {}

    def get_host(self):
        """
        获取 host 地址
        """
        return self.host
