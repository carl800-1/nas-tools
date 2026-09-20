
import log
from app.plugins import PluginManager
from config import Config

class PluginsSpider(object):

    # 私有方法
    _level = 99
    _plugin = {}
    _proxy = None
    _indexer = None

    def __init__(self, indexer=None):
        """
        注意：indexer 必须可选。
        本类在调用方一律以 PluginsSpider() 无参构造（见 builtin.py 的 169/170/241/242/358/359 行），
        若这里把 indexer 写成必填参数，改名后会直接抛 TypeError。
        原代码方法名拼成了 __int__（内置的整型转换方法），不是 __init__，
        因此这段初始化从未被执行过 —— 属长期潜伏的笔误。
        """
        if indexer is None:
            return
        self._indexer = indexer
        if indexer.proxy:
            self._proxy = Config().get_proxies()
        self._plugin = PluginManager().get_plugin_apps(self._level).get(self._indexer.parser)

    def status(self, indexer):
        try:
            plugin = PluginManager().get_plugin_apps(self._level).get(indexer.parser)
            return True if plugin else False
        except Exception as e:
            return False

    def search(self, keyword, indexer, page=0, imdb_id=None):
        """
        关键词检索，可选叠加一轮「按 IMDb ID 检索」

        返回值第一个元素是 error_flag（True = 抓取出错）。

        注意历史问题：老代码在**成功**时返回 True、失败时返回 False，
        与 TNodeSpider / RenderSpider / MTeamSpider 等通道“True=出错”的约定正好相反，
        导致插件索引器的搜索会被记成失败（insert_indexer_statistics 里 result='N'）。
        这里按统一约定纠正为「True=出错」。

        :param keyword: 搜索关键词
        :param indexer: 站点配置
        :param page: 页码
        :param imdb_id: 可选，形如 tt0111161；给了就补一轮按 ID 检索
        :return: (error_flag, 种子字典列表)
        """
        try:
            result_array = PluginManager().run_plugin_method(
                pid=indexer.parser, method='search',
                keyword=keyword, indexer=indexer, page=page)
            result_array = result_array or []

            # 补一轮按 IMDb ID 检索。
            # 这一轮是「补充」而不是「替换」：站点不支持时插件返回空，静默回落，
            # 行为与只做关键词检索完全一致。中文片名在 PT 站往往命中率很低，
            # 而 IMDb 编号全球唯一、不受中英文与译名差异影响，能把这类情况兜回来。
            if imdb_id:
                extra = self.search_by_imdb(indexer=indexer, imdb_id=imdb_id, page=page)
                if extra:
                    before = len(result_array)
                    result_array = self.__merge_by_enclosure(result_array, extra)
                    log.info(f"【PluginSpider】{indexer.name} 按 IMDb ID {imdb_id} 补到 "
                             f"{len(extra)} 条，合并去重后共 {len(result_array)} 条（原 {before} 条）")

            return False, result_array
        except Exception as e:
            return True, []

    def search_by_imdb(self, indexer, imdb_id, page=0):
        """
        按 IMDb ID 检索（插件的可选能力）

        插件实现了 search_by_imdb() 才会生效；没实现的插件不会报错，
        run_plugin_method 会因为 hasattr 失败直接返回 None，相当于静默跳过，
        因此第三方插件不受影响。

        :param indexer: 站点配置
        :param imdb_id: 形如 tt0111161
        :param page: 页码
        :return: 种子字典列表；不支持或失败时返回空列表
        """
        if not imdb_id:
            return []
        try:
            result_array = PluginManager().run_plugin_method(
                pid=indexer.parser, method='search_by_imdb',
                indexer=indexer, imdb_id=imdb_id, page=page)
            return result_array or []
        except Exception as e:
            return []

    @staticmethod
    def __merge_by_enclosure(primary, secondary):
        """
        合并两轮插件结果并按下载链接去重

        插件返回的是普通 dict（不是 MediaInfo），主键取 enclosure，
        缺失时退回「索引器 + 标题」。

        :param primary: 关键词轮结果
        :param secondary: 按 ID 轮结果
        :return: 合并去重后的新列表
        """
        merged = list(primary or [])
        seen = set()

        def _key(item):
            if not isinstance(item, dict):
                return str(item)
            return str(item.get("enclosure") or "%s|%s" % (item.get("indexer_id"), item.get("title")))

        for item in merged:
            seen.add(_key(item))
        for item in (secondary or []):
            k = _key(item)
            if k in seen:
                continue
            seen.add(k)
            merged.append(item)
        return merged

    def sites(self):
        result = []
        try:
            plugins = PluginManager().get_plugin_apps(self._level)
            for key in plugins:
                if plugins.get(key)['installed']:
                    result_array = PluginManager().run_plugin_method(pid=plugins.get(key)['id'], method='get_indexers')
                    if result_array:
                        result.extend(result_array)
        except Exception as e:
            pass
        return result