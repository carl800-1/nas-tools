import datetime
import os.path
import time
import re
import tempfile
import hashlib
from urllib.parse import quote, unquote, urlencode, urlparse

try:
    import libtorrent
    HAS_LIBTORRENT = True
except ImportError:
    libtorrent = None
    HAS_LIBTORRENT = False

try:
    from bencode import bencode, bdecode
except ImportError:
    from bencode import encode as bencode, decode as bdecode

import log
from app.utils import StringUtils
from app.utils.mteam_utils import MteamUtils
from app.utils.http_utils import RequestUtils
from app.utils.types import MediaType
from config import Config


class Torrent:
    _torrent_temp_path = None

    def __init__(self):
        self._torrent_temp_path = Config().get_temp_path()
        if not os.path.exists(self._torrent_temp_path):
            os.makedirs(self._torrent_temp_path, exist_ok=True)

    def get_torrent_info(self, url, cookie=None, ua=None, referer=None, proxy=False):
        """
        把种子下载到本地，返回种子内容
        :param url: 种子链接
        :param cookie: 站点Cookie
        :param ua: 站点UserAgent
        :param referer: 关联地址，有的网站需要这个否则无法下载
        :param proxy: 是否使用内置代理
        :return: 种子保存路径、种子内容、种子文件列表主目录、种子文件列表、错误信息
        """
        if not url:
            return None, None, "", [], "URL为空"
        if url.startswith("magnet:"):
            return None, url, "", [], f"{url} 为磁力链接"
        try:
            # 下载保存种子文件
            file_path, content, errmsg = self.save_torrent_file(url=url,
                                                                cookie=cookie,
                                                                ua=ua,
                                                                referer=referer,
                                                                proxy=proxy)
            if not file_path:
                return None, content, "", [], errmsg
            # 解析种子文件
            files_folder, files, retmsg = self.get_torrent_files(file_path)
            # 种子文件路径、种子内容、种子文件列表主目录、种子文件列表、错误信息
            return file_path, content, files_folder, files, retmsg

        except Exception as err:
            return None, None, "", [], "下载种子文件出现异常：%s" % str(err)

    def save_torrent_file(self, url, cookie=None, ua=None, referer=None, proxy=False):
        """
        把种子下载到本地
        :return: 种子保存路径，错误信息
        """
        if url.find("m-team") != -1:
            # 先留存原始链接：genDlToken 会改写 url，改写失败时 url 已为空，
            # 原写法 f"...{url}" 打印出来是 None，等于没给排查线索
            origin_url = url
            url = MteamUtils.get_mteam_torrent_url(url, ua, referer, proxy)
            if not url:
                return None, None, f"mteam 种子链接获取出错，详情地址为 {origin_url}"
            req = MteamUtils.get_mteam_torrent_req(url, ua, referer, proxy)
        else:
            req = RequestUtils(
                headers=ua,
                cookies=cookie,
                referer=referer,
                proxies=Config().get_proxies() if proxy else None
            ).get_res(url=url, allow_redirects=False)
        while req and req.status_code in [301, 302]:
            url = req.headers['Location']
            if url and url.startswith("magnet:"):
                return None, url, f"获取到磁力链接：{url}"
            req = RequestUtils(
                headers=ua,
                cookies=cookie,
                referer=referer,
                proxies=Config().get_proxies() if proxy else None
            ).get_res(url=url, allow_redirects=False)
        if req and req.status_code == 200:
            if not req.content:
                return None, None, "未下载到种子数据"
            # 解析内容格式
            if req.text and str(req.text).startswith("magnet:"):
                # 磁力链接
                return None, req.text, "磁力链接"
            elif req.text and "下载种子文件" in req.text:
                # 首次下载提示页面
                skip_flag = False
                try:
                    form = re.findall(r'<form.*?action="(.*?)".*?>(.*?)</form>', req.text, re.S)
                    if form:
                        action = form[0][0]
                        if not action or action == "?":
                            action = url
                        elif not action.startswith('http'):
                            action = StringUtils.get_base_url(url) + action
                        inputs = re.findall(r'<input.*?name="(.*?)".*?value="(.*?)".*?>', form[0][1], re.S)
                        if action and inputs:
                            data = {}
                            for item in inputs:
                                data[item[0]] = item[1]
                            # 改写req
                            req = RequestUtils(
                                headers=ua,
                                cookies=cookie,
                                referer=referer,
                                proxies=Config().get_proxies() if proxy else None
                            ).post_res(url=action, data=data)
                            if req and req.status_code == 200:
                                # 检查是不是种子文件，如果不是抛出异常
                                bdecode(req.content)
                                # 跳过成功
                                log.info(f"【Downloader】触发了站点首次种子下载，已自动跳过：{url}")
                                skip_flag = True
                            elif req is not None:
                                log.warn(f"【Downloader】触发了站点首次种子下载，且无法自动跳过，"
                                         f"返回码：{req.status_code}，错误原因：{req.reason}")
                            else:
                                log.warn(f"【Downloader】触发了站点首次种子下载，且无法自动跳过：{url}")
                except Exception as err:
                    log.warn(f"【Downloader】触发了站点首次种子下载，尝试自动跳过时出现错误：{str(err)}，链接：{url}")

                if not skip_flag:
                    return None, None, "种子数据有误，请确认链接是否正确，如为PT站点则需手工在站点下载一次种子"
            else:
                # 检查是不是种子文件，如果不是仍然抛出异常
                try:
                    bdecode(req.content)
                except Exception as err:
                    print(str(err))
                    return None, None, "种子数据有误，请确认链接是否正确"
            # 读取种子文件名
            file_name = self.__get_url_torrent_filename(req, url)
            # 种子文件路径
            file_path = os.path.join(self._torrent_temp_path, file_name)
            # 种子内容
            file_content = req.content
            # 写入磁盘
            with open(file_path, 'wb') as f:
                f.write(file_content)
        elif req is None:
            # req 为 None 只说明「请求没成功」，具体是超时、被拒还是域名不可达
            # 都已被 RequestUtils 吞掉。M-Team 的下载直链走 *.halomt.com，
            # 该域名在部分网络下会被阻断（TCP 通、TLS 握手即被 RST），
            # 此时对外只喊「无法打开链接」会让人以为是程序 bug —— 补一句可操作的提示。
            hint = ""
            if "halomt.com" in url:
                hint = ("（M-Team 下载域名可能被网络阻断，请查看日志中的具体异常；"
                        "可尝试在站点设置中为 M-Team 开启「使用代理」后重试）")
            return None, None, "无法打开链接：%s%s" % (url, hint)
        elif req.status_code == 429:
            return None, None, "触发站点流控，请稍后重试"
        else:
            return None, None, "下载种子出错，状态码：%s" % req.status_code

        return file_path, file_content, ""

    @staticmethod
    def get_torrent_files(path):
        """
        解析Torrent文件，获取文件清单
        :return: 种子文件列表主目录、种子文件列表、错误信息
        """
        if not path or not os.path.exists(path):
            return "", [], f"种子文件不存在：{path}"
        file_names = []
        file_folder = ""
        try:
            torrent = bdecode(open(path, 'rb').read())
            if torrent.get("info"):
                files = torrent.get("info", {}).get("files") or []
                if files:
                    for item in files:
                        if item.get("path"):
                            file_names.append(item["path"][0])
                    file_folder = torrent.get("info", {}).get("name")
                else:
                    file_names.append(torrent.get("info", {}).get("name"))
        except Exception as err:
            return file_folder, file_names, "解析种子文件异常：%s" % str(err)
        return file_folder, file_names, ""

    def read_torrent_content(self, path):
        """
        读取本地种子文件的内容
        :return: 种子内容、种子文件列表主目录、种子文件列表、错误信息
        """
        if not path or not os.path.exists(path):
            return None, "", [], "种子文件不存在：%s" % path
        content, retmsg, file_folder, files = None, "", "", []
        try:
            # 读取种子文件内容
            with open(path, 'rb') as f:
                content = f.read()
            # 解析种子文件
            file_folder, files, retmsg = self.get_torrent_files(path)
        except Exception as e:
            retmsg = "读取种子文件出错：%s" % str(e)
        return content, file_folder, files, retmsg

    @staticmethod
    def __get_url_torrent_filename(req, url):
        """
        从下载请求中获取种子文件名
        """
        if not req:
            return ""
        disposition = req.headers.get('content-disposition') or ""
        file_name = re.findall(r"filename=\"?(.+)\"?", disposition)
        if file_name:
            file_name = unquote(str(file_name[0].encode('ISO-8859-1').decode()).split(";")[0].strip())
            if file_name.endswith('"'):
                file_name = file_name[:-1]
        elif url and url.endswith(".torrent"):
            file_name = unquote(url.split("/")[-1])
        else:
            file_name = str(datetime.datetime.now())
        return file_name.replace('/', '')

    @staticmethod
    def get_intersection_episodes(target, source, title):
        """
        对两个季集字典进行判重，有相同项目的取集的交集
        """
        if not source or not title:
            return target
        if not source.get(title):
            return target
        if not target.get(title):
            target[title] = source.get(title)
            return target
        index = -1
        for target_info in target.get(title):
            index += 1
            source_info = None
            for info in source.get(title):
                if info.get("season") == target_info.get("season"):
                    source_info = info
                    break
            if not source_info:
                continue
            if not source_info.get("episodes"):
                continue
            if not target_info.get("episodes"):
                target_episodes = source_info.get("episodes")
                target[title][index]["episodes"] = target_episodes
                continue
            target_episodes = list(set(target_info.get("episodes")).intersection(set(source_info.get("episodes"))))
            target[title][index]["episodes"] = target_episodes
        return target

    # 排序键的展示名，顺序必须与 _sort_key_parts 返回的片段一一对应
    _SORT_KEY_NAMES = {
        "site": ["片名", "规则优先级", "站点优先级", "做种数", "季集完整度"],
        "seeder": ["片名", "规则优先级", "做种数", "站点优先级", "季集完整度"],
    }

    @staticmethod
    def _sort_key_parts(x, download_order):
        """
        把排序键拆成片段

        拆开只为了日志能逐项打印（见 __log_download_order），不改变任何语义：
        片段拼起来必须与历史实现逐字节相同 —— 历史上就是
        「标题、资源类型/规则优先级、站点优先级、做种、季集」五段定长拼接后整体逆序，
        拼法一变，选中的种子就会变。

        :return: (定长片段列表, 可读值列表)，两个列表长度一致、顺序一致
        """
        season_len = str(len(x.get_season_list())).rjust(2, '0')
        episode_len = str(len(x.get_episode_list())).rjust(4, '0')
        title_part = str(x.title).ljust(100, ' ')
        res_part = str(x.res_order).rjust(3, '0')
        site_part = str(x.site_order).rjust(3, '0')
        seeder_part = str(x.seeders).rjust(10, '0')
        season_episode_part = "%s%s" % (season_len, episode_len)
        readable_season_episode = "%s季%s集" % (len(x.get_season_list()),
                                            len(x.get_episode_list()))
        if download_order == "seeder":
            # 排序：标题、资源类型、做种、站点、季集
            return ([title_part, res_part, seeder_part, site_part, season_episode_part],
                    [str(x.title), str(x.res_order), str(x.seeders), str(x.site_order),
                     readable_season_episode])
        # 排序：标题、资源类型、站点、做种、季集
        return ([title_part, res_part, site_part, seeder_part, season_episode_part],
                [str(x.title), str(x.res_order), str(x.site_order), str(x.seeders),
                 readable_season_episode])

    @staticmethod
    def get_download_list(media_list, download_order):
        """
        对媒体信息进行排序、去重
        """
        if not media_list:
            return []

        def get_sort_str(x):
            # 五段定长拼接后整体逆序；片段定义见 _sort_key_parts
            return "".join(Torrent._sort_key_parts(x, download_order)[0])

        # 匹配的资源中排序分组选最好的一个下载
        # 按站点顺序、资源匹配顺序、做种人数下载数逆序排序
        media_list = sorted(media_list, key=get_sort_str, reverse=True)
        # 控重
        can_download_list_item = []
        can_download_list = []
        # 被选中的项在「排序后列表」里的下标，仅用于日志标注，不参与选择
        picked_index = []
        # 排序后重新加入数组，按真实名称控重，即只取每个名称的第一个
        for index, t_item in enumerate(media_list):
            # 控重的主链是名称、年份、季、集
            if t_item.type != MediaType.MOVIE:
                media_name = "%s%s" % (t_item.get_title_string(),
                                       t_item.get_season_episode_string())
            else:
                media_name = t_item.get_title_string()
            if media_name not in can_download_list:
                can_download_list.append(media_name)
                can_download_list_item.append(t_item)
                picked_index.append(index)

        # 把「为什么选了这一个」写进日志
        # 现场问题：日志里只有一句「实际下载了 1 个资源」，既看不到候选清单，
        # 也看不到规则优先级 / 站点优先级 / 做种数这些真正决定选择的数值 ——
        # 而它们全在配置里，日志里一个都没有，于是只能靠翻配置反推。
        Torrent.__log_download_order(media_list, download_order, picked_index)
        return can_download_list_item

    @staticmethod
    def __log_download_order(media_list, download_order, picked_index):
        """
        打印择优下载的候选清单、排序键与最终选择

        纯日志，不读也不写任何状态，绝不影响选择结果。
        """
        if not media_list:
            return
        key_names = Torrent._SORT_KEY_NAMES.get(download_order) or Torrent._SORT_KEY_NAMES["site"]
        log.info("【Downloader】择优下载：按「%s优先」排序，候选 %s 条；排序键 %s（均为数值越大越优先）"
                 % ("做种数" if download_order == "seeder" else "站点",
                    len(media_list),
                    " > ".join(key_names)))
        shown = media_list[:10]
        for index, item in enumerate(shown):
            _, readable = Torrent._sort_key_parts(item, download_order)
            detail = " ".join("%s=%s" % (name, value)
                              for name, value in zip(key_names, readable))
            log.info("【Downloader】  第 %s 名 %s | %s | %s%s"
                     % (index + 1,
                        item.site or "未知站点",
                        item.size or "-",
                        detail,
                        " ← 选中" if index in picked_index else ""))
        if len(media_list) > len(shown):
            log.info("【Downloader】  ...（共 %s 条候选，只列前 %s 条）" % (len(media_list), len(shown)))
        # 第 1 名相对第 2 名的决胜点：只比前两名就足以回答「凭什么选它」
        if len(media_list) > 1:
            first = Torrent._sort_key_parts(media_list[0], download_order)[1]
            second = Torrent._sort_key_parts(media_list[1], download_order)[1]
            for index, (left, right) in enumerate(zip(first, second)):
                if left != right:
                    log.info("【Downloader】择优下载选择：%s | %s —— 决胜键：第 %s 位「%s」（%s > %s）"
                             % (media_list[0].site or "未知站点",
                                media_list[0].org_string,
                                index + 1, key_names[index], left, right))
                    break
            else:
                log.info("【Downloader】择优下载选择：%s | %s —— 各排序键与第 2 名完全相同，"
                         "取排序中先出现的那条"
                         % (media_list[0].site or "未知站点", media_list[0].org_string))

    @staticmethod
    def magent2torrent(url, path, timeout=20):
        """
        磁力链接转种子文件
        :param url: 磁力链接
        :param path: 保存目录
        :param timeout: 获取元数据超时时间
        :return: 转换后种子路径
        """
        if not HAS_LIBTORRENT:
            return None, "未安装libtorrent依赖，无法进行磁力链接转换"

        log.info(f"【Downloader】转换磁力链接：{url}")
        session = libtorrent.session()
        magnet_info = libtorrent.parse_magnet_uri(url)
        magnet_info.save_path = path
        handle = session.add_torrent(magnet_info)

        log.debug("【Downloader】获取元数据中")
        tout = 0
        while not handle.status().name:
            time.sleep(1)
            tout += 1
            if tout > timeout:
                log.debug("【Downloader】元数据获取超时")
                return None, "种子元数据获取超时"
        session.pause()

        log.debug("【Downloader】获取元数据完成")
        tf = handle.torrent_file()
        ti = libtorrent.torrent_info(tf)
        torrent_file = libtorrent.create_torrent(ti)
        torrent_file.set_comment(ti.comment())
        torrent_file.set_creator(ti.creator())

        file_path = os.path.join(path, "%s.torrent" % handle.status().name)

        with open(file_path, 'wb') as f_handle:
            f_handle.write(libtorrent.bencode(torrent_file.generate()))
            f_handle.close()

        session.remove_torrent(handle, 1)
        log.info(f"【Downloader】转换后的种子路径：{file_path}")
        return file_path, ""

    @staticmethod
    def _write_binary_to_temp_file(binary_data):
        """
        种子内容转种子文件
        :param binary_data: 种子内容
        :return: 转换后种子路径
        """
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False)
            temp_file.write(binary_data)
            temp_file.close()
            return temp_file.name
        except Exception as e:
            log.error(f"【Downloader】种子内容无法写入临时文件")
            return None

    @staticmethod
    def _parse_torrent_dict(torrent_data):
        """
        获取种子文件的信息
        :param torrent_data: 种子内容的二进制数据
        :return: 种子文件的信息
        """
        try:
            torrent_dict = bdecode(torrent_data)
            return torrent_dict
        except Exception as e:
            log.error(f"【Downloader】无法解析种子文件内容")
            return None

    @staticmethod
    def _create_magnet_link(torrent_dict):
        """
        根据种子信息生成磁力链接
        :param torrent_dict: 种子信息
        :return: 磁力链接
        """
        if torrent_dict is None:
            return None
        
        magnet_info = {}
        
        if 'info' in torrent_dict:
            info_hash = hashlib.sha1(bencode(torrent_dict['info'])).hexdigest()
            magnet_info['xt'] = 'urn:btih:' + info_hash
        
        if 'name' in torrent_dict['info']:
            magnet_info['dn'] = torrent_dict['info']['name']
        
        if 'announce' in torrent_dict:
            magnet_info['tr'] = torrent_dict['announce']
        
        if 'announce-list' in torrent_dict:
            magnet_info['tr'] = [announce[0] for announce in torrent_dict['announce-list']]

        magnet_link = 'magnet:?{}'.format(urlencode(magnet_info))
        return magnet_link

    @staticmethod
    def binary_data_to_magnet_link(binary_data):
        """
        根据种子内容生成磁力链接
        :param binary_data: 种子内容
        :return: 磁力链接
        """
        temp_file_path = Torrent._write_binary_to_temp_file(binary_data)
        if not temp_file_path:
            return None
        
        with open(temp_file_path, 'rb') as torrent_file:
            torrent_data = torrent_file.read()
            torrent_dict = Torrent._parse_torrent_dict(torrent_data)
            magnet_link = Torrent._create_magnet_link(torrent_dict)
            Torrent._close_and_delete_file(temp_file_path)
            return magnet_link

    @staticmethod
    def _close_and_delete_file(file_path):
        """
        清理临时生成的种子文件
        :param file_path: 种子文件路径
        :return: 是否删除成功
        """
        try:
            with open(file_path, 'r+') as file:
                file.close()
        except:
            pass

        try:
            os.remove(file_path)
            return True
        except Exception as e:
            return False

    @staticmethod
    def is_magnet(link):
        """
        判断是否是磁力
        """
        return link.lower().startswith("magnet:?xt=urn:btih:")

    @staticmethod        
    def maybe_torrent_url(link):
        """
        判断是否可能是种子url
        """
        try:
            parsed = urlparse(link)
            return bool(parsed.netloc) and parsed.scheme in ['http', 'https', 'ftp']
        except Exception as err:
            return False

    @staticmethod
    def format_enclosure(link):
        """
        格式化一个链接
        如果是磁力链接或者为私有PT站点则直接返回
        如果不是磁力链接看是否是种子链接，如果是则下载种子后转换为磁力链接
        """
        if not StringUtils.is_string_and_not_empty(link):
            return None
        if Torrent.is_magnet(link):
            return link
        if not Torrent.maybe_torrent_url(link):
            return None

        _, torrent_content, _, _, retmsg = Torrent().get_torrent_info(link)
        if not torrent_content:
            print(f"下载种子文件出错: {retmsg}")
            return None
        return torrent_content
