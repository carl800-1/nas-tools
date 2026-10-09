# -*- coding: utf-8 -*-
"""
OrphanHelper（媒体库残留清理服务）单元测试。

覆盖要点：
  - 路径归一化与「尾部路径段」容错比对（容忍容器内外挂载点不同）
  - ★ 路径映射：媒体服务器报 /vol3/1000/video/... → 本环境 /video/...（map_server_path）
  - ★ 媒体库目录本身、以及任何「现存条目的上级目录」都不算残留
  - 目录名解析 (片名, 年份)：年份取最后一个 4 位数字（银翼杀手2049 (2017)）
  - 匹配顺序：路径优先 → 片名+年份兜底；任一命中即视为「仍在媒体服务器」
  - 片名命中但年份缺失/不同时的保守保留
  - 扫描：只处理一级子文件夹；根目录本身与散落文件不受影响
  - 符号链接默认跳过
  - dry-run 只出清单、不落盘；执行才真删
  - ★ 安全闸门：拿不到媒体服务器条目 / 连接报错时，绝不产生清单
  - 参数归一化：roots 空 → 明确报错；目录不存在 → skipped
  - 默认扫描目录回落「设置 → 媒体」的媒体库目录
  - 服务器路径翻译：map_server_path（挂载点不同也能对齐）

运行：python -m unittest tests.test_orphan_helper -v
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _ConfigStub:
    """可注入的 Config 桩：store 由测试用例改写"""
    store = {}

    def get_config(self, node=None):
        if node is None:
            return _ConfigStub.store
        return _ConfigStub.store.get(node) or {}


def _load_orphan_helper():
    """
    按文件路径直接加载 orphan_helper 模块。

    不用 `from app.helper.orphan_helper import ...`，因为 app/helper/__init__.py 会
    连带拉起 chrome_helper（undetected_chromedriver）等重依赖，在未安装全量依赖的
    环境下会 ImportError。这里桩掉 log / config / app.utils 后独立加载，保证测试
    只依赖标准库。
    """
    if "log" not in sys.modules:
        stub_log = types.ModuleType("log")
        for level in ("info", "error", "warn", "debug"):
            setattr(stub_log, level, lambda *a, **k: None)
        sys.modules["log"] = stub_log
    else:
        # 复用已存在桩，但确保 warn 存在（日志桩不能凭空暴露 warning 之外的级别）
        if not hasattr(sys.modules["log"], "warn"):
            setattr(sys.modules["log"], "warn", lambda *a, **k: None)

    stub_cfg = types.ModuleType("config")
    stub_cfg.Config = _ConfigStub
    sys.modules["config"] = stub_cfg

    stub_utils = types.ModuleType("app.utils")
    if "app" not in sys.modules:
        sys.modules["app"] = types.ModuleType("app")

    class _ExceptionUtils:
        @staticmethod
        def exception_traceback(err):
            pass
    stub_utils.ExceptionUtils = _ExceptionUtils
    sys.modules["app.utils"] = stub_utils

    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(os.path.dirname(here), "app", "helper", "orphan_helper.py")
    spec = importlib.util.spec_from_file_location("_orphan_helper_under_test", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_oh = _load_orphan_helper()
OrphanHelper = _oh.OrphanHelper
BYTES_PER_MB = _oh.BYTES_PER_MB


def _item(title="", year="", path="", original_title="", mtype="Movie"):
    return {"type": mtype, "title": title, "original_title": original_title,
            "year": year, "path": path}


class OrphanHelperTest(unittest.TestCase):

    def setUp(self):
        self.helper = OrphanHelper()
        self.tmp = tempfile.mkdtemp(prefix="orphan_helper_test_")
        self.symlink_supported = self._probe_symlink()
        # 还原默认桩：collect_server_items 每个用例自行覆盖
        _ConfigStub.store = {}

    def tearDown(self):
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------- 工具 ----------------

    def _probe_symlink(self):
        try:
            base = tempfile.mkdtemp(prefix="orphan_symlink_probe_")
            target = os.path.join(base, "t")
            link = os.path.join(base, "l")
            os.mkdir(target)
            os.symlink(target, link)
            shutil.rmtree(base, ignore_errors=True)
            return True
        except (OSError, NotImplementedError, AttributeError):
            return False

    def _make_movie(self, root, name, size=1024):
        """在 root 下建一个影片目录，里面放一个指定大小的文件"""
        d = os.path.join(root, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "movie.mkv"), "wb") as fh:
            fh.write(b"\0" * size)
        return d

    def _patch_items(self, items, name="测试服务器", err=""):
        """桩掉 collect_server_items，返回还原函数"""
        original = OrphanHelper.__dict__.get("collect_server_items")
        OrphanHelper.collect_server_items = classmethod(
            lambda cls, server_type=None: (list(items), name, err))
        self.addCleanup(setattr, OrphanHelper, "collect_server_items", original)

    def _install_fake_server(self, libraries, server_id="ugreen", error=None, items=None):
        """
        桩掉 app.mediaserver：既能给出媒体库列表（get_libraries），也能给出条目（get_items）。
        用于验证「自动读取媒体库目录」全链路。
        """
        libs = list(libraries or [])
        entries = list(items or [])

        class _Client:
            # 类属性才是配置段 id；实例上再塞个同名属性，验证「读类属性」不会被遮蔽（防御性用例）
            client_id = server_id
            client_name = server_id

            def __init__(self):
                self.client_id = "uuid-shadow"

            def get_type(self):
                return server_id

            def get_libraries(self):
                if error:
                    raise RuntimeError(error)
                return list(libs)

            def get_items(self, _lib_id):
                return list(entries)

        class _MediaServer:
            def __init__(self):
                self.server = _Client()

            def get_server_by_type(self, ctype, conf=None):
                return _Client()

        fake = types.ModuleType("app.mediaserver")
        fake.MediaServer = _MediaServer
        sys.modules["app.mediaserver"] = fake
        self.addCleanup(sys.modules.pop, "app.mediaserver", None)

    # ---------------- 参数归一化 ----------------

    def test_normalize_roots_string_variants(self):
        self.assertEqual(_oh.OrphanHelper.normalize_roots(None), [])
        self.assertEqual(_oh.OrphanHelper.normalize_roots(""), [])
        self.assertEqual(_oh.OrphanHelper.normalize_roots("  "), [])
        self.assertEqual(OrphanHelper.normalize_roots("/a/b/"), ["/a/b"])
        self.assertEqual(OrphanHelper.normalize_roots("/a/b/, /c/d ，/e"),
                         ["/a/b", "/c/d", "/e"])
        self.assertEqual(OrphanHelper.normalize_roots(["/a", "/a", " /b "]), ["/a", "/b"])

    def test_split_title_year_takes_last_four_digits(self):
        # 银翼杀手2049 里的 2049 不能被当年份
        self.assertEqual(OrphanHelper.split_title_year("银翼杀手2049 (2017)"),
                         ("银翼杀手2049", "2017"))
        self.assertEqual(OrphanHelper.split_title_year("冰川时代2 (2006)"),
                         ("冰川时代2", "2006"))
        self.assertEqual(OrphanHelper.split_title_year("Show Name"),
                         ("Show Name", ""))
        self.assertEqual(OrphanHelper.split_title_year("某片（2019）"),
                         ("某片", "2019"))

    # ---------------- 路径 ----------------

    def test_norm_path_and_tail_keys(self):
        self.assertEqual(OrphanHelper._norm_path("A\\b//c/"), "A/b/c")
        # 至少 2 段才生成签名
        self.assertEqual(OrphanHelper._tail_keys("/only"), set())
        self.assertEqual(OrphanHelper._tail_keys("/a/b"), {"a/b"})
        keys = OrphanHelper._tail_keys("/video/电影/沙丘 (2021)")
        self.assertIn("电影/沙丘 (2021)", keys)
        self.assertIn("video/电影/沙丘 (2021)", keys)
        # 最多 4 段
        self.assertNotIn("x/video/电影/沙丘 (2021)", keys)

    def test_tail_match_tolerates_different_mount_roots(self):
        """容器内外挂载点不同：/video/... 与 /volume1/video/... 也要能对上"""
        path_keys, _, library_keys = OrphanHelper._build_index(
            [_item(path="/volume1/video/电影/沙丘 (2021)/沙丘.mkv")])
        keep, why = OrphanHelper._match("/video/电影/沙丘 (2021)", "沙丘 (2021)",
                                        path_keys, set(), library_keys)
        self.assertTrue(keep)
        self.assertEqual(why, "条目所在目录")

    def test_path_exact_inside_dir_matches(self):
        root = os.path.join(self.tmp, "video", "电影")
        d = self._make_movie(root, "沙丘 (2021)")
        path_keys, _, library_keys = OrphanHelper._build_index(
            [_item(path=os.path.join(d, "movie.mkv"))])
        keep, why = OrphanHelper._match(d, "沙丘 (2021)", path_keys, set(), library_keys)
        self.assertTrue(keep)
        self.assertEqual(why, "条目所在目录")


    def test_ancestor_dir_is_never_residue(self):
        """★ 扫描根比媒体库目录浅一层时，被扫到的其实是媒体库目录 —— 不能判成残留"""
        base = os.path.join(self.tmp, "video", "01.电影")
        lib = os.path.join(base, "华语电影")
        os.makedirs(os.path.join(lib, "某片 (2020)"), exist_ok=True)
        os.makedirs(os.path.join(base, "外语电影"), exist_ok=True)
        # 条目在 华语电影/某片 (2020) 下；外语电影 里没有任何现存条目
        path_keys, _, library_keys = OrphanHelper._build_index(
            [_item(title="某片", year="2020",
                   path=os.path.join(lib, "某片 (2020)", "movie.mkv"))])
        keep, why = OrphanHelper._match(lib, "华语电影", path_keys, set(), library_keys)
        self.assertTrue(keep, "媒体库目录（现存条目的上级目录）不能算残留")
        self.assertEqual(why, "条目所在目录")
        # 真正没有条目的分类目录仍然要被列出来
        other = os.path.join(base, "外语电影")
        self.assertFalse(OrphanHelper._match(other, "外语电影", path_keys, set(), library_keys)[0])

    def test_server_library_dir_is_kept_by_library_keys(self):
        """媒体库目录本身（哪怕条目 path 用的是另一种挂载点）也要保留"""
        path_keys, _, library_keys = OrphanHelper._build_index(
            [_item(title="x", year="2020", path="/volume1/video/剧/a.mkv")],
            libraries=[{"id": "2", "name": "电视剧",
                        "paths": ["/volume1/video/02.电视剧/国产剧"]}])
        keep, why = OrphanHelper._match("/video/02.电视剧/国产剧", "国产剧",
                                        path_keys, set(), library_keys)
        self.assertTrue(keep)
        self.assertEqual(why, "媒体库目录")

    def test_scan_keeps_library_dir_when_root_is_its_parent(self):
        """★ 扫描根选成媒体库目录的上一层时，媒体库目录本身不能被判成残留"""
        base = os.path.join(self.tmp, "video", "01.电影")
        lib = os.path.join(base, "华语电影")
        os.makedirs(os.path.join(lib, "某片 (2020)"), exist_ok=True)
        other = os.path.join(base, "外语电影")   # 没有任何现存条目的分类目录
        os.makedirs(other, exist_ok=True)
        self._install_fake_server(
            [{"id": "1", "name": "华语电影", "type": "电影", "path": lib}],
            items=[{"type": "Movie", "title": "某片", "year": "2020",
                    "path": os.path.join(lib, "某片 (2020)", "movie.mkv")}])
        _ConfigStub.store = {"media_orphan": {"roots": "", "server": ""},
                             "media": {"media_server": "ugreen"}}
        result = self.helper.scan(roots=base, dry_run=True)
        self.assertIsNone(result["error"])
        names = [x["name"] for x in result["matched"]]
        self.assertNotIn("华语电影", names, "媒体库目录（现存条目的上级目录）不能算残留")
        self.assertIn("外语电影", names, "真正没有现存条目的目录仍要列出来")

    def test_scan_auto_roots_translates_foreign_mount(self):
        """★ 媒体服务器报 /vol3/1000/video/...，本环境是 <tmp>/video/... → 自动翻译后照常扫描"""
        vroot = os.path.join(self.tmp, "video")
        lib = os.path.join(vroot, "01.电影", "华语电影")
        os.makedirs(os.path.join(lib, "某片 (2020)"), exist_ok=True)
        self._make_movie(lib, "残留片 (2019)")
        self._install_fake_server(
            [{"id": "1", "name": "华语电影", "type": "电影",
              "path": "/vol3/1000/video/01.电影/华语电影"}],
            items=[{"type": "Movie", "title": "某片", "year": "2020",
                    "path": os.path.join(lib, "某片 (2020)", "movie.mkv")}])
        # 锚点：「设置 → 媒体」里配置的本环境媒体库目录
        _ConfigStub.store = {"media_orphan": {"roots": "", "server": ""},
                             "media": {"media_server": "ugreen",
                                       "movie_path": os.path.join(vroot, "01.电影")}}
        result = self.helper.scan(roots="", dry_run=True)
        self.assertIsNone(result["error"])
        self.assertEqual(result["root_source"], "server")
        self.assertEqual(result["roots"], [OrphanHelper._norm_path(lib)])
        self.assertEqual([x["name"] for x in result["matched"]], ["残留片 (2019)"])

    # ---------------- 片名 ----------------

    def test_name_match_and_year_rules(self):
        _, name_keys, _lib = OrphanHelper._build_index([_item(title="沙丘", year="2021")])
        # 年份一致
        self.assertTrue(OrphanHelper._match("/x/沙丘 (2021)", "沙丘 (2021)", set(), name_keys)[0])
        # 目录没写年份 → 仍按片名命中（保守：宁可保留）
        self.assertTrue(OrphanHelper._match("/x/沙丘", "沙丘", set(), name_keys)[0])
        # 年份不一致 → 仍命中（索引里存了 (片名, "") 的宽松项）
        self.assertTrue(OrphanHelper._match("/x/沙丘 (1999)", "沙丘 (1999)", set(), name_keys)[0])
        # 片名不同 → 判为残留
        self.assertFalse(OrphanHelper._match("/x/流浪地球 (2019)", "流浪地球 (2019)", set(), name_keys)[0])

    def test_original_title_also_indexed(self):
        _, name_keys, _lib = OrphanHelper._build_index(
            [_item(title="沙丘", original_title="Dune", year="2021")])
        self.assertTrue(OrphanHelper._match("/x/Dune (2021)", "Dune (2021)", set(), name_keys)[0])

    def test_normalize_title_strips_punctuation(self):
        self.assertEqual(OrphanHelper._norm_title("沙丘：Part Two (2024)"), "沙丘parttwo2024")
        self.assertEqual(OrphanHelper._norm_title("  The   Matrix  "), "thematrix")

    # ---------------- 扫描：正常流程 ----------------

    def _build_fixture(self):
        """构造 <tmp>/video/电影 下的 5 个影片目录 + 1 个散落文件"""
        root = os.path.join(self.tmp, "video", "电影")
        os.makedirs(root, exist_ok=True)
        path_hit = self._make_movie(root, "路径命中 (2019)")
        self._make_movie(root, "片名命中 (2023)")
        self._make_movie(root, "容器外命中 (2018)")
        self._make_movie(root, "残留甲 (2020)")
        self._make_movie(root, "残留乙 (2021)")
        with open(os.path.join(root, "readme.txt"), "w", encoding="utf-8") as fh:
            fh.write("散落文件不应受影响")
        items = [
            _item(title="路径命中", year="2019",
                  path=os.path.join(path_hit, "movie.mkv")),
            _item(title="片名命中", year="2023"),
            _item(path="/volume1/video/电影/容器外命中 (2018)/movie.mkv"),
        ]
        return root, items

    def test_scan_preview_lists_only_residue_and_does_not_delete(self):
        root, items = self._build_fixture()
        self._patch_items(items)
        result = self.helper.scan(roots=root, dry_run=True)

        self.assertIsNone(result["error"])
        self.assertEqual(result["server"], "测试服务器")
        self.assertEqual(result["server_items"], 3)
        self.assertEqual(result["total_dirs"], 5)
        self.assertEqual(sorted(x["name"] for x in result["matched"]),
                         ["残留乙 (2021)", "残留甲 (2020)"])
        self.assertEqual(sorted(x["name"] for x in result["kept"]),
                         ["容器外命中 (2018)", "片名命中 (2023)", "路径命中 (2019)"])
        self.assertEqual(len(result["deleted"]), 0)
        # 预览不落盘
        self.assertTrue(os.path.isdir(os.path.join(root, "残留甲 (2020)")))
        self.assertTrue(os.path.isdir(os.path.join(root, "残留乙 (2021)")))
        # 散落文件不动
        self.assertTrue(os.path.isfile(os.path.join(root, "readme.txt")))

    def test_scan_run_deletes_only_residue(self):
        root, items = self._build_fixture()
        self._patch_items(items)
        result = self.helper.scan(roots=root, dry_run=False)

        self.assertIsNone(result["error"])
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertFalse(os.path.exists(os.path.join(root, "残留甲 (2020)")))
        self.assertFalse(os.path.exists(os.path.join(root, "残留乙 (2021)")))
        # 保留项与散落文件仍在
        self.assertTrue(os.path.isdir(os.path.join(root, "路径命中 (2019)")))
        self.assertTrue(os.path.isdir(os.path.join(root, "片名命中 (2023)")))
        self.assertTrue(os.path.isfile(os.path.join(root, "readme.txt")))
        # 目录大小被统计
        self.assertGreater(result["deleted"][0]["size_bytes"], 0)

    def test_scan_accepts_multiple_roots(self):
        root_a, items = self._build_fixture()
        root_b = os.path.join(self.tmp, "video", "电视剧")
        os.makedirs(root_b, exist_ok=True)
        self._make_movie(root_b, "剧残留 (2015)")
        self._patch_items(items)
        result = self.helper.scan(roots=[root_a, root_b], dry_run=True)
        self.assertEqual(result["total_dirs"], 6)
        self.assertEqual(sorted(x["name"] for x in result["matched"]),
                         ["剧残留 (2015)", "残留乙 (2021)", "残留甲 (2020)"])

    def test_symlink_dir_is_skipped(self):
        if not self.symlink_supported:
            self.skipTest("当前环境不支持创建符号链接")
        root, items = self._build_fixture()
        # 链接指向一个「仍在库中」的目录：既验证链接被跳过，也验证不会顺着链接
        # 把这个目录当成两处而误判/误删
        kept_dir = os.path.join(root, "片名命中 (2023)")
        link = os.path.join(root, "链接目录")
        os.symlink(kept_dir, link)
        self._patch_items(items)
        result = self.helper.scan(roots=root, dry_run=True)
        self.assertIn(link, [x["path"] for x in result["skipped"]])
        self.assertNotIn("链接目录", [x["name"] for x in result["matched"]])
        self.assertNotIn("链接目录", [x["name"] for x in result["kept"]])
        # 执行时不会顺着链接去删目标
        self.helper.scan(roots=root, dry_run=False)
        self.assertTrue(os.path.isdir(kept_dir))

    # ---------------- 安全闸门 ----------------

    def test_gate_no_server_items_aborts(self):
        """★ 一条条目都没拿到时绝不能出清单，否则会删光整个媒体库"""
        root, _ = self._build_fixture()
        self._patch_items([])
        result = self.helper.scan(roots=root, dry_run=False)
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["matched"], [])
        self.assertEqual(result["deleted_count"], 0)
        # 一个目录都不能被删
        self.assertTrue(os.path.isdir(os.path.join(root, "残留甲 (2020)")))
        self.assertTrue(os.path.isdir(os.path.join(root, "残留乙 (2021)")))

    def test_gate_server_error_aborts(self):
        root, _ = self._build_fixture()
        self._patch_items([], err="获取媒体库列表失败：连接被拒绝")
        result = self.helper.scan(roots=root, dry_run=False)
        self.assertIn("连接被拒绝", result["error"])
        self.assertEqual(result["matched"], [])
        self.assertEqual(result["deleted_count"], 0)

    def test_gate_no_roots_aborts(self):
        result = self.helper.scan(roots="", dry_run=True)
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["matched"], [])

    def test_scan_records_root_source(self):
        root, items = self._build_fixture()
        self._patch_items(items)
        result = self.helper.scan(roots=root, dry_run=True)
        self.assertEqual(result["root_source"], "input")
        self.assertEqual(result["roots"], [self._norm(root)])

    def test_scan_auto_roots_from_server_libraries(self):
        """roots 留空 → 自动读取媒体服务器的媒体库目录并直接扫描"""
        root, items = self._build_fixture()
        self._install_fake_server([{"id": "1", "name": "电影", "type": "电影", "path": root}],
                                  items=items)
        _ConfigStub.store = {"media": {"media_server": "ugreen"}, "media_orphan": {"roots": ""}}
        result = self.helper.scan(roots="", dry_run=True)
        self.assertIsNone(result["error"])
        self.assertEqual(result["root_source"], "server")
        # 服务器读到的目录会经 map_server_path 归一成本环境路径（走 _norm_path，正斜杠）
        self.assertEqual(result["roots"], [OrphanHelper._norm_path(root)])
        self.assertEqual(sorted(x["name"] for x in result["matched"]),
                         ["残留乙 (2021)", "残留甲 (2020)"])

    def test_missing_root_recorded_as_skipped(self):
        self._patch_items([_item(title="x", year="2020")])
        missing = os.path.join(self.tmp, "not_exists")
        result = self.helper.scan(roots=missing, dry_run=True)
        self.assertIsNone(result["error"])
        self.assertEqual(result["total_dirs"], 0)
        self.assertEqual(result["skipped"][0]["path"], missing)

    # ---------------- 配置 ----------------

    def test_get_configured_roots_prefers_media_orphan_section(self):
        _ConfigStub.store = {
            "media_orphan": {"roots": "/explicit/a,/explicit/b"},
            "media": {"movie_path": "/m", "tv_path": ["/t1", "/t2"], "anime_path": "/a"},
        }
        conf = OrphanHelper.get_default_config()
        self.assertEqual(OrphanHelper.get_configured_roots(conf), ["/explicit/a", "/explicit/b"])

    def test_get_configured_roots_falls_back_to_media_section(self):
        _ConfigStub.store = {
            "media_orphan": {"roots": ""},
            "media": {"movie_path": "/m", "tv_path": ["/t1", "/t2"], "anime_path": "/a"},
        }
        conf = OrphanHelper.get_default_config()
        self.assertEqual(OrphanHelper.get_configured_roots(conf), ["/m", "/t1", "/t2", "/a"])

    # ---------------- 自动检测媒体服务器 / 读取媒体库目录 ----------------

    @staticmethod
    def _norm(path):
        """与本模块 normalize_roots 一致：去空白、去掉结尾的 / 与 \\（不做斜杠方向转换）"""
        return str(path).strip().rstrip("/").rstrip("\\")

    def test_client_type_id_reads_class_attribute(self):
        """展示名取「配置段 id」：必须读类属性（实例上的 client_id 是连接用 UUID）"""
        class _Client:
            client_id = "trimemedia"
            client_name = "飞牛影视"

            def __init__(self):
                self.client_id = "uuid-shadow"

        self.assertEqual(OrphanHelper._client_type_id(_Client()), "trimemedia")
        self.assertEqual(OrphanHelper._client_type_id(_Client(), "emby"), "trimemedia")

        class _Bare:
            pass

        # 没有 client_id 时回落到传入的 fallback
        self.assertEqual(OrphanHelper._client_type_id(_Bare(), "PLEX"), "plex")

    def test_get_server_libraries_normalizes_paths_and_checks_access(self):
        """绿联是逗号分隔字符串、飞牛是列表；并把目录映射成本环境可访问的路径"""
        real = os.path.join(self.tmp, "电影库")
        os.makedirs(real, exist_ok=True)
        missing = os.path.join(self.tmp, "不存在的库")
        self._install_fake_server([
            {"id": "1", "name": "电影", "type": "电影",
             "path": real + "," + missing},          # 绿联：逗号分隔字符串
            {"id": "2", "name": "电视剧", "type": "电视剧", "path": [real]},  # 飞牛：列表
        ])
        _ConfigStub.store = {"media": {"media_server": "ugreen"}, "media_orphan": {"server": ""}}
        libs, name, err = OrphanHelper.get_server_libraries()
        self.assertEqual(err, "")
        self.assertEqual(name, "绿联影视")
        self.assertEqual(libs[0]["paths"], [self._norm(real), self._norm(missing)])
        self.assertFalse(libs[0]["accessible"], "含不可访问目录时整体不算全可访问")
        self.assertEqual(libs[0]["present"], [OrphanHelper._norm_path(real)])
        self.assertEqual(libs[0]["missing"], [self._norm(missing)])
        self.assertTrue(libs[1]["accessible"])
        # 逐条映射明细：可访问的原样返回，不可访问的给出原因
        detail = {d["server"]: d for d in libs[0]["details"]}
        self.assertTrue(detail[self._norm(real)]["ok"])
        self.assertIn("直接可访问", detail[self._norm(real)]["reason"])
        self.assertFalse(detail[self._norm(missing)]["ok"])
        self.assertEqual(detail[self._norm(missing)]["local"], "")

    def test_map_server_path_translates_foreign_mount_root(self):
        """★ 媒体服务器报 /vol3/1000/video/...，本环境是 /video/... → 按尾部路径段对齐"""
        vroot = os.path.join(self.tmp, "video")
        lib_cn = os.path.join(vroot, "01.电影", "华语电影")
        lib_en = os.path.join(vroot, "01.电影", "外语电影")
        os.makedirs(lib_cn, exist_ok=True)
        os.makedirs(lib_en, exist_ok=True)
        anchors = {"movie_path": [os.path.join(vroot, "01.电影")],
                   "tv_path": [], "anime_path": []}
        local, why = OrphanHelper.map_server_path(
            "/vol3/1000/video/01.电影/华语电影", anchors)
        self.assertEqual(local, OrphanHelper._norm_path(lib_cn))
        self.assertIn("尾部路径段", why)
        # 锚点只有 /video 这种浅前缀时也能对上
        anchors_loose = {"movie_path": [self._norm(vroot)],
                         "tv_path": [], "anime_path": []}
        local, _why = OrphanHelper.map_server_path(
            "/vol3/1000/video/01.电影/外语电影", anchors_loose)
        self.assertEqual(local, OrphanHelper._norm_path(lib_en))
        # 本环境本来就能访问 → 原样返回
        local, why = OrphanHelper.map_server_path(OrphanHelper._norm_path(lib_en), anchors)
        self.assertEqual(local, OrphanHelper._norm_path(lib_en))
        self.assertIn("直接可访问", why)
        # 对不上任何锚点 → 映射失败
        local, _why = OrphanHelper.map_server_path("/vol9/unknown/某处", anchors)
        self.assertEqual(local, "")
        self.assertEqual(OrphanHelper.map_server_path("")[0], "")

    def test_get_server_libraries_error_and_empty(self):
        self._install_fake_server([], error="连接被拒绝")
        _ConfigStub.store = {"media": {"media_server": "ugreen"}, "media_orphan": {"server": ""}}
        libs, _name, err = OrphanHelper.get_server_libraries()
        self.assertEqual(libs, [])
        self.assertIn("连接被拒绝", err)

        self._install_fake_server([])
        libs, _name, err = OrphanHelper.get_server_libraries()
        self.assertEqual(libs, [])
        self.assertIn("没有返回任何媒体库", err)

    def test_resolve_roots_explicit_and_config_win(self):
        _ConfigStub.store = {"media_orphan": {"roots": "/cfg/a,/cfg/b"}}
        self.assertEqual(OrphanHelper.resolve_roots("/given"), (["/given"], "input"))
        self.assertEqual(OrphanHelper.resolve_roots(None), (["/cfg/a", "/cfg/b"], "config"))

    def test_resolve_roots_detects_from_server_then_media(self):
        lib = os.path.join(self.tmp, "库A")
        os.makedirs(lib, exist_ok=True)
        # 媒体服务器读得到 → 用它（优先于 media 段配置）
        self._install_fake_server([{"id": "1", "name": "电影", "type": "电影", "path": lib}])
        _ConfigStub.store = {"media_orphan": {"roots": ""},
                            "media": {"media_server": "ugreen", "movie_path": "/media/电影"}}
        # 服务器读到的目录会经 map_server_path → _norm_path 归一（正斜杠）
        self.assertEqual(OrphanHelper.resolve_roots(None), ([OrphanHelper._norm_path(lib)], "server"))

        # 媒体服务器读不到 → 回落「设置 → 媒体」目录
        self._install_fake_server([], error="连不上")
        _ConfigStub.store = {"media_orphan": {"roots": ""},
                            "media": {"media_server": "ugreen",
                                      "movie_path": "/media/电影", "tv_path": ["/media/剧"]}}
        self.assertEqual(OrphanHelper.resolve_roots(None),
                         (["/media/电影", "/media/剧"], "media"))

        # 什么都没有 → 空
        _ConfigStub.store = {"media_orphan": {"roots": ""}, "media": {"media_server": "ugreen"}}
        self.assertEqual(OrphanHelper.resolve_roots(None), ([], ""))

    def test_detect_reports_libraries_and_suggested_roots(self):
        lib = os.path.join(self.tmp, "电影库")
        os.makedirs(lib, exist_ok=True)
        self._install_fake_server([{"id": "1", "name": "电影", "type": "电影", "path": lib}])
        _ConfigStub.store = {"media_orphan": {"roots": "", "server": ""},
                            "media": {"media_server": "ugreen", "movie_path": "/media/电影"},
                            "ugreen": {"host": "http://10.0.0.1:9999"}}
        info = OrphanHelper.detect()
        self.assertEqual(info["server"]["id"], "ugreen")
        self.assertTrue(info["server"]["configured"])
        self.assertEqual(info["libraries"][0]["name"], "电影")
        self.assertEqual(info["detected_roots"], [OrphanHelper._norm_path(lib)])
        self.assertEqual(info["suggested_roots"], [OrphanHelper._norm_path(lib)])
        self.assertEqual(info["source"], "server")
        self.assertEqual(info["error"], "")

    def test_detect_keeps_media_fallback_when_server_unreachable(self):
        self._install_fake_server([], error="连接被拒绝")
        _ConfigStub.store = {"media_orphan": {"roots": "", "server": ""},
                            "media": {"media_server": "ugreen", "movie_path": "/media/电影"}}
        info = OrphanHelper.detect()
        self.assertIn("连接被拒绝", info["error"])
        self.assertEqual(info["libraries"], [])
        self.assertEqual(info["detected_roots"], [])
        self.assertEqual(info["suggested_roots"], ["/media/电影"])
        self.assertEqual(info["source"], "media")

    def test_detect_prefers_explicit_config_roots(self):
        self._install_fake_server([{"id": "1", "name": "电影", "type": "电影", "path": "/x"}])
        _ConfigStub.store = {"media_orphan": {"roots": "/explicit", "server": ""},
                            "media": {"media_server": "ugreen"}}
        info = OrphanHelper.detect()
        self.assertEqual(info["suggested_roots"], ["/explicit"])
        self.assertEqual(info["source"], "config")

    def test_resolve_server_prefers_explicit_argument(self):
        _ConfigStub.store = {
            "media_orphan": {"server": "trimemedia"},
            "media": {"media_server": "ugreen"},
            "plex": {"host": "http://10.0.0.1:32400"},
        }
        info = OrphanHelper.resolve_server("plex")
        self.assertEqual(info["id"], "plex")
        self.assertEqual(info["name"], "Plex")
        self.assertTrue(info["configured"])

    def test_resolve_server_falls_back_to_config_then_current(self):
        # 界面不选：配置里指定了 → 以配置为准（无界面时的越权开关）
        _ConfigStub.store = {
            "media_orphan": {"server": "trimemedia"},
            "media": {"media_server": "ugreen"},
            "trimemedia": {"host": "http://10.0.0.2:8000"},
            "ugreen": {"host": "http://10.0.0.1:9999"},
        }
        info = OrphanHelper.resolve_server()
        self.assertEqual(info["id"], "trimemedia")
        self.assertEqual(info["name"], "飞牛影视")
        self.assertTrue(info["configured"])
        # 配置也留空 → 用「设置 → 媒体服务器」里当前启用的那一台
        _ConfigStub.store = {
            "media_orphan": {"server": ""},
            "media": {"media_server": "ugreen"},
            "ugreen": {"host": "http://10.0.0.1:9999"},
        }
        info = OrphanHelper.resolve_server()
        self.assertEqual(info["id"], "ugreen")
        self.assertEqual(info["name"], "绿联影视")
        self.assertTrue(info["configured"])

    def test_resolve_server_marks_unconfigured(self):
        # 段落存在但没填 host → 界面要给出明确提示（连不上会在安全闸门处中止）
        _ConfigStub.store = {
            "media_orphan": {"server": ""},
            "media": {"media_server": "ugreen"},
            "ugreen": {"host": ""},
        }
        info = OrphanHelper.resolve_server()
        self.assertEqual(info["id"], "ugreen")
        self.assertFalse(info["configured"])

    def test_resolve_server_empty_when_nothing_configured(self):
        _ConfigStub.store = {"media_orphan": {"server": ""}, "media": {}}
        info = OrphanHelper.resolve_server()
        self.assertEqual(info["id"], "")
        self.assertEqual(info["name"], "")
        self.assertFalse(info["configured"])

    def test_collect_server_items_client_selection(self):
        """与当前启用一致时复用已连接的客户端；指定另一台（飞牛/绿联）才单独构造"""
        created = []

        class _Client:
            # 类属性才是「配置段 id」；实例上再塞一个同名属性，
            # 验证取展示名/类型 id 时读的是类属性、不会被实例属性遮蔽（防御性用例）
            client_id = "ugreen"
            client_name = "绿联影视"

            def __init__(self, name):
                self._name = name
                self.client_id = "uuid-abc"  # 实例级同名属性，不应被读到

            def get_type(self):
                # 真实客户端返回的是 MediaServerType **枚举对象**，不是字符串
                return self._name

            def get_libraries(self):
                return []

            def get_items(self, _lib_id):
                return []

        class _MediaServer:
            def __init__(self):
                self.server = _Client("当前启用")

            def get_server_by_type(self, ctype, conf=None):
                created.append(ctype)
                typed = type("_TypedClient", (_Client,),
                             {"client_id": ctype, "client_name": ctype})
                return typed(ctype)

        fake = types.ModuleType("app.mediaserver")
        fake.MediaServer = _MediaServer
        sys.modules["app.mediaserver"] = fake
        self.addCleanup(sys.modules.pop, "app.mediaserver", None)

        _ConfigStub.store = {"media": {"media_server": "ugreen"},
                             "media_orphan": {"server": ""}}
        _items, name, err = OrphanHelper.collect_server_items("ugreen")
        self.assertEqual(created, [], "与当前启用一致时不应重复构造客户端")
        # 读的是**类属性** client_id → 展示名是中文名，而不是实例上的 "uuid-abc"
        self.assertEqual(name, "绿联影视")
        self.assertIn("没有返回任何媒体库", err)

        _ConfigStub.store = {"media": {"media_server": "ugreen"},
                             "media_orphan": {"server": ""}}
        _items, name, err = OrphanHelper.collect_server_items("trimemedia")
        self.assertEqual(created, ["trimemedia"], "指定另一台时应按类型单独构造")
        self.assertEqual(name, "飞牛影视")
        self.assertIn("没有返回任何媒体库", err)

    def test_get_dir_size_ignores_symlink(self):
        root = os.path.join(self.tmp, "size")
        d = self._make_movie(root, "影片", size=2048)
        self.assertEqual(OrphanHelper.get_dir_size(d), 2048)

    # ---------------- 摘要 ----------------

    def test_format_result_message(self):
        self._patch_items([])
        self.assertEqual(OrphanHelper.format_result_message({"error": "boom"}), "boom")
        preview = {"error": None, "dry_run": True, "server": "绿联影视", "server_items": 10,
                   "total_dirs": 4, "matched": [{"size_bytes": 1048576}, {"size_bytes": 1048576}]}
        self.assertIn("预览完成", OrphanHelper.format_result_message(preview))
        self.assertIn("2", OrphanHelper.format_result_message(preview))
        done = {"error": None, "dry_run": False, "server": "绿联影视", "total_dirs": 4,
                "deleted_count": 2, "deleted": [{"size_bytes": 1048576}], "failed_count": 0}
        self.assertIn("清理完成", OrphanHelper.format_result_message(done))


if __name__ == "__main__":
    unittest.main(verbosity=2)
