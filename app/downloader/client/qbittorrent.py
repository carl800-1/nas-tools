import os
import re
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import log
import qbittorrentapi
from app.downloader.client._base import _IDownloadClient
from app.utils import ExceptionUtils, StringUtils
from app.utils.tags import Tags
from app.utils.types import DownloaderType, DIR_CATEGORY_AUTO


# 「种子管理模式」的三种取值（与 app/conf/moduleconf.py 的下拉选项一一对应）
TORRENT_MANAGEMENT_DEFAULT = "default"
TORRENT_MANAGEMENT_MANUAL = "manual"
TORRENT_MANAGEMENT_AUTO = "auto"
TORRENT_MANAGEMENT_MODES = (TORRENT_MANAGEMENT_DEFAULT,
                            TORRENT_MANAGEMENT_MANUAL,
                            TORRENT_MANAGEMENT_AUTO)

# 下发计划里的提示级别，None 表示「该模式下什么都不提示」
NOTICE_NONE = None
NOTICE_INFO = "info"
NOTICE_WARN = "warn"


def resolve_torrent_management(mode, save_path, category):
    """
    按「种子管理模式」决定下发给 qBittorrent 的参数，以及该不该提示

    **本函数是三种模式语义的唯一实现**：参数下发（``Qbittorrent.add_torrent``）与
    日志提示（``Downloader.download``）都从这里取结果，所以「模式切换 / 参数下发 /
    报警」三者之间不可能各说各话。

    ===========  ==============  =============  ==========  ==============================
    模式           save_path       category       autoTMM     行为
    ===========  ==============  =============  ==========  ==============================
    ``default``   不传            不传           不传         完全沿用下载器自身的设置与行为
    ``manual``    传（NAStool 定）不传           ``False``    目录由 NAStool 决定，强制手动管理
    ``auto``      无分类时才传     有分类才传     有分类 True  有分类 → 下载器按分类落盘；
                                                             无分类 → 按 NAStool 目录；
                                                             都没有 → 什么都不传
    ===========  ==============  =============  ==========  ==============================

    「不传」= 该键传 ``None``。``qbittorrent-api`` 把 ``data`` 里每个值写成
    ``(占位值, 实际值)`` 元组，而 ``requests`` 编码表单时**会跳过元组里的 None**
    （见其 ``torrents.py`` 的 ``data`` 构造 + requests ``_encode_params``）——
    因此传 ``None`` 就等于**不下发这个参数**，下载器会用它自己的设置。
    这一点按 ``qbittorrent-api==2023.9.53`` 的真实源码核对，不是推测。

    提示级别只在该模式下真的缺少必要信息时才给 ``warn``：
    ``default`` 什么都不下发 ⇒ **结构上不可能误报**；``manual`` 没有目录是真故障；
    ``auto`` 分类与目录都没有才是真故障。

    :param mode: ``default`` / ``manual`` / ``auto``；其它取值按 ``default`` 处理
    :param save_path: 「下载目录设置」匹配到的那一行的「下载保存目录」（可能为空）
    :param category: 同一行推导出的 qB 分类名（可能为空）
    :return: dict —— ``save_path`` / ``category`` / ``auto_tmm`` / ``level`` / ``reason``
    """
    if mode not in TORRENT_MANAGEMENT_MODES:
        mode = TORRENT_MANAGEMENT_DEFAULT
    save_path = save_path or None
    category = category or None

    if mode == TORRENT_MANAGEMENT_DEFAULT:
        return {
            "save_path": None,
            "category": None,
            "auto_tmm": None,
            "level": NOTICE_NONE,
            "reason": "default_mode",
        }

    if mode == TORRENT_MANAGEMENT_MANUAL:
        # 手动模式只认「下载保存目录」：分类是「自动模式」的落盘依据，这里不下发。
        # 目录为空时仍然强制 autoTMM=False —— 手动模式的定义就是「不让下载器自动管理」。
        if save_path:
            return {
                "save_path": save_path,
                "category": None,
                "auto_tmm": False,
                "level": NOTICE_INFO,
                "reason": "manual_dir",
            }
        return {
            "save_path": None,
            "category": None,
            "auto_tmm": False,
            "level": NOTICE_WARN,
            "reason": "manual_no_dir",
        }

    # 自动模式：先分类、后目录，两者都没有才什么都不下发
    if category:
        return {
            "save_path": None,
            "category": category,
            "auto_tmm": True,
            "level": NOTICE_INFO,
            "reason": "auto_category",
        }
    if save_path:
        return {
            "save_path": save_path,
            "category": None,
            "auto_tmm": False,
            "level": NOTICE_INFO,
            "reason": "auto_dir",
        }
    return {
        "save_path": None,
        "category": None,
        "auto_tmm": None,
        "level": NOTICE_WARN,
        "reason": "auto_nothing",
    }


class Qbittorrent(_IDownloadClient):
    # 下载器ID
    client_id = "qbittorrent"
    # 下载器类型
    client_type = DownloaderType.QB
    # 下载器名称
    client_name = DownloaderType.QB.value

    # 私有属性
    _client_config = {}
    _torrent_management = False

    qbc = None
    ver = None
    host = None
    port = None
    username = None
    password = None
    download_dir = []
    name = "测试"

    def __init__(self, config):
        self._client_config = config
        self.init_config()
        self.connect()
        # 种子自动管理模式，根据下载路径设置为下载器设置分类
        self.init_torrent_management()
        if self.qbc:
            # 设置未完成种子添加!qb后缀
            self.qbc.app_set_preferences({"incomplete_files_ext": True})

    def init_config(self):
        if self._client_config:
            self.host = self._client_config.get('host')
            self.port = int(self._client_config.get('port')) if str(self._client_config.get('port')).isdigit() else 0
            self.username = self._client_config.get('username')
            self.password = self._client_config.get('password')
            self.download_dir = self._client_config.get('download_dir') or []
            self.name = self._client_config.get('name') or ""
            # 种子管理模式
            self._torrent_management = self._client_config.get('torrent_management')
            if self._torrent_management not in ["default", "manual", "auto"]:
                self._torrent_management = "default"

    @classmethod
    def match(cls, ctype):
        return True if ctype in [cls.client_id, cls.client_type, cls.client_name] else False

    def get_type(self):
        return self.client_type

    def connect(self):
        if self.host and self.port:
            self.qbc = self.__login_qbittorrent()

    def __login_qbittorrent(self):
        """
        连接qbittorrent
        :return: qbittorrent对象
        """
        try:
            # 登录
            qbt = qbittorrentapi.Client(host=self.host,
                                        port=self.port,
                                        username=self.username,
                                        password=self.password,
                                        VERIFY_WEBUI_CERTIFICATE=False,
                                        REQUESTS_ARGS={'timeout': (15, 60)})
            try:
                qbt.auth_log_in()
                self.ver = qbt.app_version()
            except qbittorrentapi.LoginFailed as e:
                log.error(f"【{self.client_name}】{self.name} 登录出错：{str(e)}")
            return qbt
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 连接出错：{str(err)}")
            return None

    def get_status(self):
        if not self.qbc:
            return False
        try:
            return True if self.qbc.transfer_info() else False
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 获取状态出错：{str(err)}")
            return False

    def init_torrent_management(self):
        """
        自动管理模式下，根据「下载目录设置」创建 / 更新下载器里的 qB 分类

        **只有「自动」模式会写入下载器**：

        * 「默认」模式完全沿用下载器自身的设置，NAStool 不做任何干预 —— 既不改
          分类，也不改分类的保存路径；
        * 「手动」模式的目录由 NAStool 逐任务下发，同样不需要维护分类；
        * 「自动」模式的落盘位置依赖分类，所以必须保证分类存在，且其保存路径与
          「下载目录设置」里的一致。
        """
        if self._torrent_management != TORRENT_MANAGEMENT_AUTO:
            return
        # 获取下载器目前的分类信息
        categories = self.__get_qb_category()
        # 更新下载器中分类设置
        for dir_item in self.download_dir:
            # 「分类标签」列（v6.0.2 起合并了原「自动分类」列）：「自定义」档的值就是
            # 分类名；「自动判定」档没有静态分类名（运行期按判定结果生成、由自动分类
            # 自己创建），这里跳过。老数据的分类名可能还在 label 键里，一并兜底。
            label = str(dir_item.get("auto_category") or "").strip() \
                or str(dir_item.get("label") or "").strip()
            save_path = dir_item.get("save_path")
            if not label or label == DIR_CATEGORY_AUTO or not save_path:
                continue
            # 查询分类是否存在
            category_item = categories.get(label)
            if not category_item:
                # 分类不存在，则创建
                self.__update_category(name=label, save_path=save_path)
            else:
                # 如果分类存在，但是路径不一致，则更新
                if os.path.normpath(category_item.get("savePath")) != os.path.normpath(save_path):
                    self.__update_category(name=label, save_path=save_path, is_edit=True)

    def __get_qb_category(self):
        """
        查询下载器中已设置的分类
        """
        if not self.qbc:
            return {}
        return self.qbc.torrent_categories.categories or {}

    def __update_category(self, name, save_path, is_edit=False):
        """
        更新分类
        """
        try:
            if is_edit:
                self.qbc.torrent_categories.edit_category(name=name, save_path=save_path)
                log.info(f"【{self.client_name}】{self.name} 更新分类：{name}，路径：{save_path}")
            else:
                self.qbc.torrent_categories.create_category(name=name, save_path=save_path)
                log.info(f"【{self.client_name}】{self.name} 创建分类：{name}，路径：{save_path}")
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 设置分类：{name}，路径：{save_path} 错误：{str(err)}")

    def get_torrent_management(self):
        """
        返回「种子管理模式」（``default`` / ``manual`` / ``auto``）

        ``Downloader.download()`` 按它决定下发哪些参数、要不要提示 ——
        判定逻辑见模块级函数 :func:`resolve_torrent_management`。
        """
        return self._torrent_management

    def get_torrents(self, ids=None, status=None, tag=None):
        """
        获取种子列表
        return: 种子列表, 是否发生异常
        """
        if not self.qbc:
            return [], True
        try:
            torrents = self.qbc.torrents_info(torrent_hashes=ids,
                                              status_filter=status)
            if tag:
                results = []
                if not isinstance(tag, list):
                    tag = [tag]
                for torrent in torrents:
                    include_flag = True
                    for t in tag:
                        if t and t not in torrent.get("tags"):
                            include_flag = False
                            break
                    if include_flag:
                        results.append(torrent)
                return results or [], False
            return torrents or [], False
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 获取种子列表出错：{str(err)}")
            return [], True

    def get_completed_torrents(self, ids=None, tag=None):
        """
        获取已完成的种子
        return: 种子列表, 如发生异常则返回None
        """
        if not self.qbc:
            return None
        torrents, error = self.get_torrents(status=["completed"], ids=ids, tag=tag)
        return None if error else torrents or []

    def get_downloading_torrents(self, ids=None, tag=None):
        """
        获取正在下载的种子
        return: 种子列表, 如发生异常则返回None
        """
        if not self.qbc:
            return None
        torrents, error = self.get_torrents(ids=ids,
                                            status=["downloading"],
                                            tag=tag)
        return None if error else torrents or []

    def remove_torrents_tag(self, ids, tag):
        """
        移除种子Tag
        :param ids: 种子Hash列表
        :param tag: 标签内容
        """
        try:
            return self.qbc.torrents_delete_tags(torrent_hashes=ids, tags=tag)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 移除种子tag出错：{str(err)}")
            return False

    def set_torrents_status(self, ids, tags=None):
        """
        设置种子状态标签。

        程序不再自动写入任何默认标签（历史版本会强行打上「已整理」）。
        只写入调用方传来的标签，调用方没传则不写。

        :param ids: 种子Hash列表
        :param tags: 调用方指定的标签（来自用户填写，字符串或列表）
        """
        if not self.qbc:
            return
        tag_list = Tags.split(tags)
        if not tag_list:
            # 没有标签要写，直接返回，避免产生空标签
            return
        try:
            # 打标签
            self.qbc.torrents_add_tags(tags=Tags.SEPARATOR.join(tag_list), torrent_hashes=ids)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 设置种子标签出错：{str(err)}")

    def get_categories(self):
        """
        获取下载器中的分类清单

        :return: dict，{分类名: 保存路径}；读取失败返回空 dict
        """
        if not self.qbc:
            return {}
        try:
            categories = self.qbc.torrents_categories(requests_args={'timeout': (10, 30)}) or {}
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 获取分类清单出错：{str(err)}")
            return {}
        return {name: ((item or {}).get("savePath") or "")
                for name, item in categories.items()}

    def create_category(self, name):
        """
        创建分类（**不绑定保存路径**）

        只传 name、不传 save_path 是有意为之：分类一旦绑了保存路径，
        qBittorrent 在任务开着「自动种子管理」时会把任务文件搬到那个路径下。
        自动分类只负责给任务归类，不该动用户的数据位置。

        :param name: 分类名
        :return: bool，是否创建成功
        """
        if not self.qbc or not name:
            return False
        try:
            self.qbc.torrents_create_category(name=name)
            log.info(f"【{self.client_name}】{self.name} 创建分类：{name}（不带保存路径）")
            return True
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 创建分类 {name} 出错：{str(err)}")
            return False

    def set_torrents_category(self, ids, category):
        """
        设置种子分类

        ⚠️ qBittorrent 的分类**自带保存路径**：任务开着「自动种子管理」时，
        改分类会让 qB 把任务文件搬到该分类的保存路径下（分类没有保存路径则搬回
        默认保存路径）。调用方必须自己判断任务信息里的 ``auto_tmm`` 字段，
        决定是否跳过 —— 本方法不做这个判断，因为能否接受搬移只有用户知道。

        :param ids: 种子 Hash 列表
        :param category: 分类名
        :return: bool，是否写入成功
        """
        # 空 ids 必须挡住：qB 的 setCategory 对空 hashes 的行为没有保证，
        # 万一被解释成「全部任务」，就会把整个下载器的分类一次刷掉。
        if not self.qbc or not category or not ids:
            return False
        try:
            self.qbc.torrents_set_category(torrent_hashes=ids, category=category)
            return True
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 设置种子分类出错：{str(err)}")
            return False

    def torrents_set_force_start(self, ids):
        """
        设置强制作种
        """
        try:
            self.qbc.torrents_set_force_start(enable=True, torrent_hashes=ids)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 设置强制做种出错：{str(err)}")

    def get_transfer_task(self, tag=None, match_path=False):
        """
        获取下载文件转移任务种子
        """
        # 处理下载完成的任务
        torrents = self.get_completed_torrents() or []
        trans_tasks = []
        for torrent in torrents:
            torrent_tags = torrent.get("tags") or ""
            # 已整理过的（查程序自己的转移账本）不再处理
            if self.is_transferred(torrent.get("hash")):
                continue
            # 开启标签隔离，未包含指定标签的不处理
            if tag and tag not in (torrent_tags or "").split(","):
                log.debug(f"【{self.client_name}】{self.name} 开启标签隔离， {torrent.get('name')} 未包含指定标签：{tag}")
                continue
            path = torrent.get("save_path")
            # 无法获取下载路径的不处理
            if not path:
                log.debug(f"【{self.client_name}】{self.name} 未获取到 {torrent.get('name')} 下载保存路径")
                continue
            true_path, replace_flag = self.get_replace_path(path, self.download_dir)
            # 开启目录隔离，未进行目录替换的不处理
            if match_path and not replace_flag:
                log.debug(f"【{self.client_name}】{self.name} 开启目录隔离， {torrent.get('name')} 未匹配下载目录范围")
                continue
            content_path = torrent.get("content_path")
            if content_path:
                trans_name = content_path.replace(path, "").replace("\\", "/")
                if trans_name.startswith('/'):
                    trans_name = trans_name[1:]
            else:
                trans_name = torrent.get('name')
            trans_tasks.append({
                'path': os.path.join(true_path, trans_name).replace("\\", "/"),
                'id': torrent.get('hash')
            })
        return trans_tasks

    def get_remove_torrents(self, config=None):
        """
        获取自动删种任务种子
        """
        if not config:
            return []
        remove_torrents = []
        remove_torrents_ids = []
        torrents, error_flag = self.get_torrents(tag=config.get("filter_tags"))
        if error_flag:
            return []
        ratio = config.get("ratio")
        # 做种时间 单位：小时
        seeding_time = config.get("seeding_time")
        # 大小 单位：GB
        size = config.get("size")
        minsize = size[0] * 1024 * 1024 * 1024 if size else 0
        maxsize = size[-1] * 1024 * 1024 * 1024 if size else 0
        # 平均上传速度 单位 KB/s
        upload_avs = config.get("upload_avs")
        savepath_key = config.get("savepath_key")
        tracker_key = config.get("tracker_key")
        qb_state = config.get("qb_state")
        qb_category = config.get("qb_category")
        for torrent in torrents:
            date_done = torrent.completion_on if torrent.completion_on > 0 else torrent.added_on
            date_now = int(time.mktime(datetime.now().timetuple()))
            torrent_seeding_time = date_now - date_done if date_done else 0
            torrent_upload_avs = torrent.uploaded / torrent_seeding_time if torrent_seeding_time else 0
            if ratio and torrent.ratio <= ratio:
                continue
            if seeding_time and torrent_seeding_time <= seeding_time * 3600:
                continue
            if size and (torrent.size >= maxsize or torrent.size <= minsize):
                continue
            if upload_avs and torrent_upload_avs >= upload_avs * 1024:
                continue
            if savepath_key and not re.findall(savepath_key, torrent.save_path, re.I):
                continue
            if tracker_key and not re.findall(tracker_key, torrent.tracker, re.I):
                continue
            if qb_state and torrent.state not in qb_state:
                continue
            if qb_category and torrent.category not in qb_category:
                continue
            remove_torrents.append({
                "id": torrent.hash,
                "name": torrent.name,
                "site": StringUtils.get_url_sld(torrent.tracker),
                "size": torrent.size
            })
            remove_torrents_ids.append(torrent.hash)
        if config.get("samedata") and remove_torrents:
            remove_torrents_plus = []
            for remove_torrent in remove_torrents:
                name = remove_torrent.get("name")
                size = remove_torrent.get("size")
                for torrent in torrents:
                    if torrent.name == name and torrent.size == size and torrent.hash not in remove_torrents_ids:
                        remove_torrents_plus.append({
                            "id": torrent.hash,
                            "name": torrent.name,
                            "site": StringUtils.get_url_sld(torrent.tracker),
                            "size": torrent.size
                        })
            remove_torrents_plus += remove_torrents
            return remove_torrents_plus
        return remove_torrents

    def __get_last_add_torrentid_by_tag(self, tag, status=None):
        """
        根据种子的下载链接获取下载中或暂停的钟子的ID
        :return: 种子ID
        """
        try:
            torrents, _ = self.get_torrents(status=status, tag=tag)
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            return None
        if torrents:
            return torrents[0].get("hash")
        else:
            return None

    def get_torrent_id_by_tag(self, tag, status=None):
        """
        通过标签多次尝试获取刚添加的种子ID，并移除标签
        """
        torrent_id = None
        # QB添加下载后需要时间，重试5次每次等待5秒
        for i in range(1, 6):
            time.sleep(5)
            torrent_id = self.__get_last_add_torrentid_by_tag(tag=tag,
                                                              status=status)
            if torrent_id is None:
                continue
            else:
                self.remove_torrents_tag(torrent_id, tag)
                break
        return torrent_id

    @staticmethod
    def __get_mapping_value(response: Any, key: str):
        if isinstance(response, Mapping):
            return response.get(key)
        if hasattr(response, "get"):
            try:
                return response.get(key)
            except TypeError:
                pass
        return getattr(response, key, None)

    def __parse_add_torrent_response(self, response: Any) -> bool:
        if not response:
            return False
        if isinstance(response, str):
            return "Ok" in response

        success_count = self.__get_mapping_value(response, "success_count") or 0
        pending_count = self.__get_mapping_value(response, "pending_count") or 0
        added_torrent_ids = self.__get_mapping_value(response, "added_torrent_ids") or []

        if not isinstance(added_torrent_ids, list):
            try:
                added_torrent_ids = list(added_torrent_ids)
            except TypeError:
                added_torrent_ids = [added_torrent_ids]

        added_torrent_ids = [str(torrent_id) for torrent_id in added_torrent_ids if torrent_id]
        if added_torrent_ids:
            return True
        if success_count or pending_count:
            return True
        return "Ok" in str(response)

    def add_torrent(self,
                    content,
                    is_paused=False,
                    download_dir=None,
                    tag=None,
                    category=None,
                    content_layout=None,
                    upload_limit=None,
                    download_limit=None,
                    ratio_limit=None,
                    seeding_time_limit=None,
                    auto_tmm=None,
                    cookie=None
                    ):
        """
        添加种子
        :param content: 种子urls或文件
        :param is_paused: 添加后暂停
        :param tag: 标签
        :param download_dir: 下载路径
        :param category: 分类
        :param content_layout: 布局
        :param upload_limit: 上传限速 Kb/s
        :param download_limit: 下载限速 Kb/s
        :param ratio_limit: 分享率限制
        :param seeding_time_limit: 做种时间限制
        :param auto_tmm: 是否使用下载器的「自动种子管理」（True / False / None）。
            由调用方按「种子管理模式」算出（见 resolve_torrent_management），
            None 表示不下发该参数、由下载器用它自身的设置
        :param cookie: 站点Cookie用于辅助下载种子
        :return: bool
        """
        if not self.qbc or not content:
            return False
        if isinstance(content, str):
            urls = content
            torrent_files = None
        else:
            urls = None
            torrent_files = content
        # save_path 与 auto_tmm 都由调用方按「种子管理模式」算好传进来（见
        # resolve_torrent_management）。这里不再自行按模式改判 —— 旧实现一旦
        # 拿到非空 download_dir 就把 is_auto 钉成 False，导致「自动模式」在填了
        # 「下载保存目录」的行上静默退化成手动。
        save_path = download_dir or None
        if not category:
            category = None
        if tag:
            tags = tag
        else:
            tags = None
        if not content_layout:
            content_layout = None
        if upload_limit:
            upload_limit = int(upload_limit) * 1024
        else:
            upload_limit = None
        if download_limit:
            download_limit = int(download_limit) * 1024
        else:
            download_limit = None
        if ratio_limit:
            ratio_limit = round(float(ratio_limit), 2)
        else:
            ratio_limit = None
        if seeding_time_limit:
            seeding_time_limit = int(seeding_time_limit)
        else:
            seeding_time_limit = None

        try:
            # 添加下载
            qbc_ret = self.qbc.torrents_add(urls=urls,
                                            torrent_files=torrent_files,
                                            save_path=save_path,
                                            category=category,
                                            is_paused=is_paused,
                                            tags=tags,
                                            content_layout=content_layout,
                                            upload_limit=upload_limit,
                                            download_limit=download_limit,
                                            ratio_limit=ratio_limit,
                                            seeding_time_limit=seeding_time_limit,
                                            use_auto_torrent_management=auto_tmm,
                                            cookie=cookie)
            return self.__parse_add_torrent_response(qbc_ret)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 添加种子出错：{str(err)}")
            return False

    def start_torrents(self, ids):
        if not self.qbc:
            return False
        try:
            return self.qbc.torrents_resume(torrent_hashes=ids)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 开始下载出错：{str(err)}")
            return False

    def stop_torrents(self, ids):
        if not self.qbc:
            return False
        try:
            return self.qbc.torrents_pause(torrent_hashes=ids)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 停止下载出错：{str(err)}")
            return False

    def delete_torrents(self, delete_file, ids):
        if not self.qbc:
            return False
        if not ids:
            return False
        try:
            self.qbc.torrents_delete(delete_files=delete_file, torrent_hashes=ids)
            return True
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 删除种子出错：{str(err)}")
            return False

    def get_files(self, tid):
        try:
            return self.qbc.torrents_files(torrent_hash=tid)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 获取文件列表出错：{str(err)}")
            return None

    def set_files(self, **kwargs):
        """
        设置下载文件的状态，priority为0为不下载，priority为1为下载
        """
        if not kwargs.get("torrent_hash") or not kwargs.get("file_ids"):
            return False
        try:
            self.qbc.torrents_file_priority(torrent_hash=kwargs.get("torrent_hash"),
                                            file_ids=kwargs.get("file_ids"),
                                            priority=kwargs.get("priority"))
            return True
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 设置下载文件状态出错：{str(err)}")
            return False

    def set_torrent_tag(self, **kwargs):
        pass

    def get_download_dirs(self):
        if not self.qbc:
            return []
        ret_dirs = []
        try:
            categories = self.qbc.torrents_categories(requests_args={'timeout': (10, 30)}) or {}
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 获取下载文件夹出错：{str(err)}")
            return []
        for category in categories.values():
            if category and category.get("savePath") and category.get("savePath") not in ret_dirs:
                ret_dirs.append(category.get("savePath"))
        return ret_dirs

    def set_uploadspeed_limit(self, ids, limit):
        """
        设置上传限速，单位bytes/sec
        """
        if not self.qbc:
            return
        if not ids or not limit:
            return
        self.qbc.torrents_set_upload_limit(limit=int(limit),
                                           torrent_hashes=ids)

    def set_downloadspeed_limit(self, ids, limit):
        """
        设置下载限速，单位bytes/sec
        """
        if not self.qbc:
            return
        if not ids or not limit:
            return
        self.qbc.torrents_set_download_limit(limit=int(limit),
                                             torrent_hashes=ids)

    def change_torrent(self, **kwargs):
        """
        修改种子状态
        """
        pass

    def get_downloading_progress(self, tag=None, ids=None):
        """
        获取正在下载的种子进度
        """
        Torrents = self.get_downloading_torrents(tag=tag, ids=ids) or []
        DispTorrents = []
        for torrent in Torrents:
            # 进度
            progress = round(torrent.get('progress') * 100, 1)
            if torrent.get('state') in ['pausedDL']:
                state = "Stoped"
                speed = "已暂停"
            else:
                state = "Downloading"
                _dlspeed = StringUtils.str_filesize(torrent.get('dlspeed'))
                _upspeed = StringUtils.str_filesize(torrent.get('upspeed'))
                if progress >= 100:
                    speed = "%s%sB/s %s%sB/s" % (chr(8595), _dlspeed, chr(8593), _upspeed)
                else:
                    eta = StringUtils.str_timelong(torrent.get('eta'))
                    speed = "%s%sB/s %s%sB/s %s" % (chr(8595), _dlspeed, chr(8593), _upspeed, eta)
            # 主键
            DispTorrents.append({
                'id': torrent.get('hash'),
                'name': torrent.get('name'),
                'speed': speed,
                'state': state,
                'site_url': self.host.rstrip('/') + ':' + str(self.port).lstrip('/'),
                'progress': progress
            })
        return DispTorrents

    def set_speed_limit(self, download_limit=None, upload_limit=None):
        """
        设置速度限制
        :param download_limit: 下载速度限制，单位KB/s
        :param upload_limit: 上传速度限制，单位kB/s
        """
        if not self.qbc:
            return
        download_limit = download_limit * 1024
        upload_limit = upload_limit * 1024
        try:
            if self.qbc.transfer.upload_limit != upload_limit:
                self.qbc.transfer.upload_limit = upload_limit
            if self.qbc.transfer.download_limit != download_limit:
                self.qbc.transfer.download_limit = download_limit
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 设置速度限制出错：{str(err)}")
            return False

    def recheck_torrents(self, ids):
        if not self.qbc:
            return False
        try:
            return self.qbc.torrents_recheck(torrent_hashes=ids)
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 检验种子出错：{str(err)}")
            return False

    def get_client_speed(self):
        if not self.qbc:
            return False
        try:
            transfer_info = self.qbc.transfer.info
            if transfer_info:
                return {
                    "up_speed": transfer_info.get('up_info_speed'),
                    "dl_speed": transfer_info.get('dl_info_speed')
                }
            return False
        except Exception as err:
            log.error(f"【{self.client_name}】{self.name} 获取客户端速度出错：{str(err)}")
            return False
