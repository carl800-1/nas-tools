import log
from app.helper import DbHelper
from app.indexer import Indexer
from app.plugins import EventManager
from app.utils.commons import singleton
from config import Config
from app.message import Message
from app.downloader import Downloader
from app.media import Media
from app.helper import ProgressHelper
from app.utils.types import SearchType, EventType, ProgressKey


# 第二轮（第二名称）补搜的默认阈值：第一轮有效结果少于该条数时就补搜一轮
DEFAULT_EN_MIN_RESULT = 5
# 默认最多用几个候选名称去搜（含首选名）—— 上限低是为了不把站点请求量放大
DEFAULT_CANDIDATE_MAX = 2


def get_en_second_round_threshold():
    """
    读取「第二名称补搜」的阈值

    背景：老逻辑只在第一轮**一条有效结果都没有**时才用第二个名称重搜，
    意味着第一轮只要认出 1 条，第二个名称就永远不会被尝试。
    而第二个名称通常是英文名/原名，在外文站与中英混排站点的命中率
    明显高于中文名 —— 结果就是「站点上明明有一堆资源，却只拿到零星几条」。

    配置项 laboratory.search_en_min_result：
        1        -> 只有第一轮一条有效结果都没有才补搜（与老代码行为完全一致，可作回退开关）
        5        -> 第一轮不足 5 条时补搜（默认）
        很大的数  -> 无论第一轮多少条都补搜

    配置缺失或写入非法值时回落默认值，保证不会因为配置写错而悄悄关掉补搜。

    :return: 阈值整数
    """
    laboratory = Config().get_config("laboratory") or {}
    try:
        value = int(laboratory.get("search_en_min_result", DEFAULT_EN_MIN_RESULT))
    except (TypeError, ValueError):
        return DEFAULT_EN_MIN_RESULT
    # 小于 1 视作非法：阈值 0 会让比较条件恒为假，等于关掉补搜
    if value < 1:
        return DEFAULT_EN_MIN_RESULT
    return value


def get_search_candidate_max():
    """
    读取「最多用几个候选名称去搜」的上限

    配置项 laboratory.search_candidate_max：
        1  -> 只用首选名（等价于不做任何补搜）
        2  -> 首选名 + 次选名（默认，与老版本「中英两名」的规模一致）
        3  -> 再补一个 TMDB 别名

    为什么要设上限：每个候选名都要对每个站点各发一轮请求，
    候选名一多，站点流控（Sites.check_ratelimit）和整体耗时都会迅速恶化。
    非法值一律回落默认值 2。

    :return: 候选名数量上限（含首选名）
    """
    laboratory = Config().get_config("laboratory") or {}
    try:
        value = int(laboratory.get("search_candidate_max", DEFAULT_CANDIDATE_MAX))
    except (TypeError, ValueError):
        return DEFAULT_CANDIDATE_MAX
    if value < 1:
        return DEFAULT_CANDIDATE_MAX
    return value


def iter_tmdb_alias_names(media_info):
    """
    从已经加载好的 TMDB 信息里逐个吐出别名

    只读 media_info.tmdb_info（不额外发起 TMDB 请求），
    兼容电影（alternative_titles.titles）与剧集（alternative_titles.results），
    以及 translations 里的各语言标题。

    :param media_info: MediaInfo 对象
    :yield: 别名字符串
    """
    tmdb_info = getattr(media_info, "tmdb_info", None)
    if not isinstance(tmdb_info, dict):
        return
    for key in ("alternative_titles", "translations"):
        block = tmdb_info.get(key)
        if not isinstance(block, dict):
            continue
        items = block.get("titles") or block.get("results") or block.get("translations") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            # translations 的数据嵌在 data 里，alternative_titles 直接平铺
            data = item.get("data")
            name = None
            if isinstance(data, dict):
                name = data.get("title") or data.get("name")
            if not name:
                name = item.get("title") or item.get("name")
            if name:
                yield str(name)


def is_cn_name_first():
    """
    是否「中文名优先」

    对应 laboratory.search_en_title：
        False（默认）-> 先搜中文名，再搜英文名
        True         -> 先搜英文名，再搜中文名

    只决定候选名的先后顺序，不再决定「搜不搜第二个名称」
    （那是 search_en_min_result 阈值的事）。

    :return: True 表示中文名优先
    """
    laboratory = Config().get_config("laboratory") or {}
    return not laboratory.get("search_en_title")


def build_search_candidates(media_info, cn_first=True, max_candidates=None,
                            cn_name=None, en_name=None):
    """
    构造有序的搜索候选名称名单

    顺序：首选名 -> 次选名 -> 原名 -> TMDB 别名（若已有）。
    cn_first=True 时首选中文名、次选英文/原名；False 时反过来
    （对应 laboratory.search_en_title）。

    两种「只用关键词」的短路情形：
      · media_info 为 None（未识别到媒体，走快速搜索）
      · media_info.keyword 有值（RSS/订阅里用户手填的搜索词）

    cn_name / en_name 可由调用方显式传入：调用方原本就要做一次
    「英文名兜底」（en_name 缺失且非英语原片时调 TMDB 查英文标题），
    把结果传进来可以保留这段逻辑，同时让本函数保持纯计算、不联网。

    :param media_info: MediaInfo 对象，可为 None
    :param cn_first: 是否中文名优先
    :param max_candidates: 候选名数量上限，None 时读配置
    :param cn_name: 显式指定的中文名，为空时从 media_info 取
    :param en_name: 显式指定的英文名，为空时从 media_info 取
    :return: 去重后的候选名列表，至少含 1 个元素
    """
    if max_candidates is None:
        max_candidates = get_search_candidate_max()
    if max_candidates < 1:
        max_candidates = 1

    names = []

    def _add(name):
        if not name:
            return
        text = str(name).strip()
        if text and text not in names:
            names.append(text)

    # 用户手填的搜索词优先级最高，直接只用它
    keyword = getattr(media_info, "keyword", None) if media_info else None
    if keyword:
        _add(keyword)
        return names[:max_candidates] or [str(keyword)]

    if not cn_name:
        cn_name = (getattr(media_info, "cn_name", None)
                   or getattr(media_info, "title", None)) if media_info else None
    if not en_name:
        en_name = getattr(media_info, "en_name", None) if media_info else None
    original_title = getattr(media_info, "original_title", None) if media_info else None
    if not en_name:
        en_name = original_title

    # 英语名缺失时用原名顶上位，保证「英文优先」在只有原名时依然成立
    en_or_original = en_name

    if cn_first:
        _add(cn_name)
        _add(en_or_original)
    else:
        _add(en_or_original)
        _add(cn_name)
    # 原名单独再列一次：它常常是外文站真正认得的名字（中英之外的第三选择）
    _add(original_title)

    # 再补 TMDB 别名，直到达到上限
    if media_info and len(names) < max_candidates:
        for alias in iter_tmdb_alias_names(media_info):
            if len(names) >= max_candidates:
                break
            _add(alias)

    return names[:max_candidates]


def search_medias_by_candidates(searcher,
                                media_info,
                                filter_args,
                                in_from,
                                candidates=None,
                                fallback_name=None,
                                log_prefix="【Searcher】",
                                on_round=None):
    """
    按候选名称名单逐个搜索，累计有效结果达到阈值即停

    老逻辑是固定的「第一轮 + 第二轮」：只有第一轮一条有效结果都没有，
    才会拿第二个名称重搜（见 laboratory.search_en_min_result 的说明）。
    这里把它推广成「候选名单轮询」，并用同一个阈值控制停不停：

        · 第一个候选名无条件搜（保证至少发一轮，与老代码一致）
        · 之后每个候选名，只有在「已累计结果 < search_en_min_result」时才搜
        · 每轮结果用 merge_media_lists 合并去重，先来的顺序不变

    候选名数量由 laboratory.search_candidate_max 在上游限制（默认 2），
    因此默认情况下请求量与老版本完全相同，不会放大站点压力。

    :param searcher: Searcher 实例（用它的 search_medias）
    :param media_info: MediaInfo 对象，可为 None（快速搜索）
    :param filter_args: 过滤条件
    :param in_from: 搜索渠道
    :param candidates: 候选名称列表，None 时按 media_info 现场构造
    :param fallback_name: 候选名为空时的兜底名称（通常是原始关键字）
    :param log_prefix: 日志前缀
    :param on_round: 可选回调 on_round(index, name, 已累计条数, 候选总数)，
        在每一轮真正发起前调用；web 端用它重置进度计数、更新提示文案
    :return: 合并去重后的命中资源列表
    """
    if candidates is None:
        candidates = build_search_candidates(media_info, cn_first=is_cn_name_first())
    candidates = [c for c in (candidates or []) if c]
    if not candidates and fallback_name:
        candidates = [str(fallback_name)]
    if not candidates:
        return []

    threshold = get_en_second_round_threshold()
    total = len(candidates)
    media_list = []

    for index, name in enumerate(candidates):
        # 第一个候选名无条件搜；之后达不到阈值才继续换名
        if index > 0 and len(media_list) >= threshold:
            log.info("%s已累计 %s 条有效资源（≥阈值 %s），不再尝试 %s"
                     % (log_prefix, len(media_list), threshold,
                        " / ".join(candidates[index:])))
            break

        if on_round:
            try:
                on_round(index, name, len(media_list), total)
            except Exception as e:
                # 回调只影响提示文案，不能因此中断搜索
                log.warn("%s搜索轮次回调异常：%s" % (log_prefix, e))

        if index == 0:
            # 第一轮沿用老行为：异常照旧向上抛，不在这里吞掉
            media_list = list(searcher.search_medias(key_word=name,
                                                     filter_args=filter_args,
                                                     match_media=media_info,
                                                     in_from=in_from) or [])
            continue

        try:
            round_list = searcher.search_medias(key_word=name,
                                                filter_args=filter_args,
                                                match_media=media_info,
                                                in_from=in_from)
        except Exception as e:
            # 补充轮失败不该把第一轮辛苦搜到的结果一起丢掉，记日志后继续
            log.error("%s候选名「%s」补充搜索异常，保留已有 %s 条结果：%s"
                      % (log_prefix, name, len(media_list), e))
            continue

        if not round_list:
            log.info("%s候选名「%s」未搜索到资源" % (log_prefix, name))
            continue

        before_count = len(media_list)
        media_list = merge_media_lists(media_list, round_list)
        log.info("%s候选名「%s」搜索到 %s 条，合并去重后共 %s 条（原 %s 条）"
                 % (log_prefix, name, len(round_list), len(media_list), before_count))

    return media_list


def media_dedupe_key(media_info):
    """
    生成搜索结果去重键

    优先用下载链接（同一份资源无论用哪个关键词搜到，链接都相同），
    缺失时退回「站点 + 片名」，保证合并两轮结果时不出现重复条目。

    :param media_info: MediaInfo 对象
    :return: 去重键字符串
    """
    enclosure = getattr(media_info, "enclosure", None)
    if enclosure:
        return str(enclosure)
    return "%s|%s" % (getattr(media_info, "site", ""),
                      getattr(media_info, "org_string", "") or getattr(media_info, "title", ""))


def merge_media_lists(primary, secondary):
    """
    合并两轮搜索结果并去重

    第一轮结果原样保留在前面（顺序不变），第二轮只补充第一轮没有的条目。
    不修改传入的列表。

    :param primary: 第一轮结果列表
    :param secondary: 第二轮结果列表
    :return: 合并去重后的新列表
    """
    merged = list(primary or [])
    seen = {media_dedupe_key(item) for item in merged}
    for item in (secondary or []):
        key = media_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


@singleton
class Searcher:
    downloader = None
    media = None
    message = None
    indexer = None
    progress = None
    dbhelper = None
    eventmanager = None

    _search_auto = True

    def __init__(self):
        self.init_config()

    def init_config(self):
        self.downloader = Downloader()
        self.media = Media()
        self.message = Message()
        self.progress = ProgressHelper()
        self.dbhelper = DbHelper()
        self.indexer = Indexer()
        self.eventmanager = EventManager()
        self._search_auto = Config().get_config("pt").get('search_auto', True)

    def search_medias(self,
                      key_word: [str, list],
                      filter_args: dict,
                      match_media=None,
                      in_from: SearchType = None):
        """
        根据关键字调用索引器检查媒体
        :param key_word: 搜索的关键字，不能为空
        :param filter_args: 过滤条件
        :param match_media: 区配的媒体信息
        :param in_from: 搜索渠道
        :return: 命中的资源媒体信息列表
        """
        if not key_word:
            return []
        if not self.indexer:
            return []
        # 触发事件
        media_info_dict = {"id": match_media.tmdb_id} if match_media else {}
        self.eventmanager.send_event(EventType.SearchStart, {
            "key_word": key_word,
            "media_info": media_info_dict,
            "filter_args": filter_args,
            "search_type": in_from.value if in_from else None
        })
        return self.indexer.search_by_keyword(key_word=key_word,
                                              filter_args=filter_args,
                                              match_media=match_media,
                                              in_from=in_from)

    def search_one_media(self, media_info,
                         in_from: SearchType,
                         no_exists: dict,
                         sites: list = None,
                         filters: dict = None,
                         user_name=None):
        """
        只搜索和下载一个资源，用于精确搜索下载，由微信、Telegram或豆瓣调用
        :param media_info: 已识别的媒体信息
        :param in_from: 搜索渠道
        :param no_exists: 缺失的剧集清单
        :param sites: 搜索哪些站点
        :param filters: 过滤条件，为空则不过滤
        :param user_name: 用户名
        :return: 请求的资源是否全部下载完整，如完整则返回媒体信息
                 请求的资源如果是剧集则返回下载后仍然缺失的季集信息
                 搜索到的结果数量
                 下载到的结果数量，如为None则表示未开启自动下载
        """
        if not media_info:
            return None, {}, 0, 0
        # 进度计数重置
        self.progress.start(ProgressKey.Search)
        # 查找的季
        if media_info.begin_season is None:
            search_season = None
        else:
            search_season = media_info.get_season_list()
        # 查找的集
        search_episode = media_info.get_episode_list()
        if search_episode and not search_season:
            search_season = [1]
        # 过滤条件
        filter_args = {"season": search_season,
                       "episode": search_episode,
                       "year": media_info.year,
                       "type": media_info.type,
                       "site": sites,
                       "seeders": True}
        if filters:
            filter_args.update(filters)
        # 搜索名称候选名单
        #
        # 与 web/backend/search_torrents.py 同一处问题：老逻辑只在第一轮
        # 一条有效结果都没有时才换名称重搜，导致「认出 1 条就不再看英文名」。
        # 现在改成「候选名单轮询 + 阈值停」并合并去重，只放宽、不收窄：
        #   laboratory.search_en_min_result 决定什么时候停（1 = 老行为，默认 5）
        #   laboratory.search_candidate_max 决定最多用几个名字（默认 2，不放大请求量）
        if media_info.keyword:
            # 直接使用搜索词搜索
            search_candidates = [media_info.keyword]
        else:
            # 中文名
            if media_info.cn_name:
                search_cn_name = media_info.cn_name
            else:
                search_cn_name = media_info.title
            # 英文名
            search_en_name = None
            if media_info.en_name:
                search_en_name = media_info.en_name
            else:
                if media_info.original_language == "en":
                    search_en_name = media_info.original_title
                else:
                    # 获取英文标题
                    en_title = self.media.get_tmdb_en_title(media_info)
                    if en_title:
                        search_en_name = en_title
            search_candidates = build_search_candidates(media_info,
                                                        cn_first=is_cn_name_first(),
                                                        cn_name=search_cn_name,
                                                        en_name=search_en_name)
        # 开始搜索
        log.info("【Searcher】开始搜索 %s ..." % " / ".join(search_candidates))
        media_list = search_medias_by_candidates(searcher=self,
                                                 media_info=media_info,
                                                 filter_args=filter_args,
                                                 in_from=in_from,
                                                 candidates=search_candidates,
                                                 log_prefix="【Searcher】")

        if len(media_list) == 0:
            log.info("【Searcher】%s 未搜索到任何资源" % " / ".join(search_candidates))
            return None, no_exists, 0, 0
        else:
            if in_from in self.message.get_search_types():
                # 保存搜索记录
                self.delete_all_search_torrents()
                # 搜索结果排序
                media_list = sorted(media_list, key=lambda x: "%s%s%s%s" % (str(x.title).ljust(100, ' '),
                                                                            str(x.res_order).rjust(3, '0'),
                                                                            str(x.site_order).rjust(3, '0'),
                                                                            str(x.seeders).rjust(10, '0')),
                                    reverse=True)
                # 插入数据库
                self.insert_search_results(media_list)
                # 微信未开自动下载时返回
                if not self._search_auto:
                    return None, no_exists, len(media_list), None
            # 择优下载
            download_items, left_medias = self.downloader.batch_download(in_from=in_from,
                                                                         media_list=media_list,
                                                                         need_tvs=no_exists,
                                                                         user_name=user_name)
            # 统计下载情况，下全了返回True，没下全返回False
            if not download_items:
                log.info("【Searcher】%s 未下载到资源" % media_info.title)
                return None, left_medias, len(media_list), 0
            else:
                log.info("【Searcher】实际下载了 %s 个资源" % len(download_items))
                # 还有剩下的缺失，说明没下完，返回False
                if left_medias:
                    return None, left_medias, len(media_list), len(download_items)
                # 全部下完了
                else:
                    return download_items[0], no_exists, len(media_list), len(download_items)

    def get_search_result_by_id(self, dl_id):
        """
        根据下载ID获取搜索结果
        :param dl_id: 下载ID
        :return: 搜索结果
        """
        return self.dbhelper.get_search_result_by_id(dl_id)

    def get_search_results(self):
        """
        获取搜索结果
        :return: 搜索结果
        """
        return self.dbhelper.get_search_results()

    def delete_all_search_torrents(self):
        """
        删除所有搜索结果
        """
        self.dbhelper.delete_all_search_torrents()

    def insert_search_results(self, media_items: list, title=None, ident_flag=True):
        """
        插入搜索结果
        :param media_items: 搜索结果
        :param title: 搜索标题
        :param ident_flag: 是否标识
        """
        self.dbhelper.insert_search_results(media_items, title, ident_flag)