# -*- coding: utf-8 -*-
"""
CleanHelper（目录清理服务）单元测试。

覆盖要点：
  - 阈值边界：大小「等于」阈值的子文件夹必须命中（判据为 <= N）
  - 嵌套的小文件夹：只按一级子文件夹递归汇总，深层嵌套正确累加
  - 空目录：大小为 0，必然命中
  - dry-run：只返回清单，不落盘删除
  - 符号链接：默认跳过（不计入大小、不删除）
  - 异常容忍：单个文件读取失败不中断整体流程
  - 隔离性：根目录本身与根目录下的散落文件不受影响
  - 参数归一化：非法阈值回落为 0，避免误删

运行：python -m unittest tests.test_clean_helper -v
"""
import glob
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_clean_helper():
    """
    按文件路径直接加载 clean_helper 模块。

    不用 `from app.helper.clean_helper import ...`，因为 app/helper/__init__.py 会
    连带拉起 chrome_helper（undetected_chromedriver）等重依赖，在未安装全量依赖的
    环境下会 ImportError。这里桩掉 log / config / app.utils 后独立加载，保证测试
    只依赖标准库。
    """
    if "log" not in sys.modules:
        stub_log = types.ModuleType("log")
        for level in ("info", "error", "warn", "warning", "debug"):
            setattr(stub_log, level, lambda *a, **k: None)
        sys.modules["log"] = stub_log

    if "config" not in sys.modules:
        stub_cfg = types.ModuleType("config")

        class _Config:
            def get_config(self, node=None):
                return {}
        stub_cfg.Config = _Config
        sys.modules["config"] = stub_cfg

    if "app.utils" not in sys.modules:
        stub_utils = types.ModuleType("app.utils")

        class _ExceptionUtils:
            @staticmethod
            def exception_traceback(err):
                pass
        stub_utils.ExceptionUtils = _ExceptionUtils
        sys.modules["app.utils"] = stub_utils

    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(os.path.dirname(here), "app", "helper", "clean_helper.py")
    spec = importlib.util.spec_from_file_location("_clean_helper_under_test", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ch = _load_clean_helper()
CleanHelper = _ch.CleanHelper
BYTES_PER_MB = _ch.BYTES_PER_MB


class CleanHelperTest(unittest.TestCase):

    def setUp(self):
        self.helper = CleanHelper()
        self.root = tempfile.mkdtemp(prefix="clean_helper_test_")
        # 在 Windows 上创建符号链接需要权限，先探测能力（在独立临时目录中探测，避免污染 root）
        self.symlink_supported = self._probe_symlink()

    def tearDown(self):
        if os.path.exists(self.root):
            shutil.rmtree(self.root, ignore_errors=True)

    # ---------------- 工具方法 ----------------

    def _probe_symlink(self):
        """在独立的临时目录里探测符号链接能力，探测完彻底清理，不污染 self.root。"""
        probe = tempfile.mkdtemp(prefix="clean_helper_probe_")
        try:
            src = os.path.join(probe, "src")
            link = os.path.join(probe, "link")
            os.makedirs(src)
            os.symlink(src, link, target_is_directory=True)
            return os.path.islink(link)
        except (OSError, NotImplementedError, AttributeError, TypeError):
            return False
        finally:
            shutil.rmtree(probe, ignore_errors=True)

    def _mkfile(self, rel_path, size):
        """在根目录下创建指定大小的文件（内容为 b'x' 重复）"""
        full = os.path.join(self.root, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"x" * size)
        return full

    def _names(self, result):
        return sorted(item["name"] for item in result["matched"])

    # ---------------- 边界：等于阈值 ----------------

    def test_size_equal_to_threshold_is_matched(self):
        """总大小正好等于阈值的子文件夹应命中（判据为 <= N）"""
        self._mkfile(os.path.join("exact", "f.bin"), 2 * BYTES_PER_MB)
        result = self.helper.scan(self.root, 2)
        self.assertIn("exact", self._names(result))
        self.assertEqual(result["matched"][0]["size_bytes"], 2 * BYTES_PER_MB)

    def test_size_just_below_is_matched(self):
        """略小于阈值：命中"""
        self._mkfile(os.path.join("under", "f.bin"), 2 * BYTES_PER_MB - 1)
        result = self.helper.scan(self.root, 2)
        self.assertIn("under", self._names(result))

    def test_size_just_above_is_not_matched(self):
        """略大于阈值：不命中"""
        self._mkfile(os.path.join("over", "f.bin"), 2 * BYTES_PER_MB + 1)
        result = self.helper.scan(self.root, 2)
        self.assertNotIn("over", self._names(result))

    def test_threshold_zero_matches_only_empty_dirs(self):
        """阈值为 0 时，只有空文件夹命中"""
        os.makedirs(os.path.join(self.root, "empty"))
        self._mkfile(os.path.join("tiny", "f.bin"), 1)
        result = self.helper.scan(self.root, 0)
        self.assertEqual(self._names(result), ["empty"])

    # ---------------- 边界：空目录 ----------------

    def test_empty_dir_size_is_zero(self):
        """空文件夹大小为 0"""
        empty = os.path.join(self.root, "empty")
        os.makedirs(empty)
        size, skipped = self.helper.get_dir_size(empty)
        self.assertEqual(size, 0)
        self.assertEqual(skipped, [])

    def test_nested_empty_dirs_still_zero(self):
        """多层空目录之和仍为 0"""
        os.makedirs(os.path.join(self.root, "a", "b", "c"))
        size, _ = self.helper.get_dir_size(os.path.join(self.root, "a"))
        self.assertEqual(size, 0)

    # ---------------- 边界：嵌套的小文件夹 ----------------

    def test_nested_small_dir_size_accumulated(self):
        """嵌套结构的文件大小应递归累加"""
        self._mkfile(os.path.join("nested", "s1", "s2", "f.bin"), 100)
        self._mkfile(os.path.join("nested", "s1", "g.bin"), 200)
        self._mkfile(os.path.join("nested", "h.bin"), 300)
        size, _ = self.helper.get_dir_size(os.path.join(self.root, "nested"))
        self.assertEqual(size, 600)

    def test_nested_over_threshold_not_matched(self):
        """嵌套后总量超过阈值则不命中（不能只看一级）"""
        self._mkfile(os.path.join("deep", "a", "f.bin"), BYTES_PER_MB)
        self._mkfile(os.path.join("deep", "b", "f.bin"), BYTES_PER_MB)
        result = self.helper.scan(self.root, 1)
        self.assertNotIn("deep", self._names(result))

    def test_only_first_level_subdirs_are_cleaned(self):
        """深层子目录不会被单独列为待删项（只处理一级子文件夹）"""
        self._mkfile(os.path.join("parent", "child", "f.bin"), 10)
        result = self.helper.scan(self.root, 1)
        names = self._names(result)
        self.assertIn("parent", names)
        self.assertNotIn("child", names)

    # ---------------- dry-run ----------------

    def test_dry_run_does_not_delete(self):
        """dry-run 模式不删除任何内容"""
        target = os.path.join(self.root, "small")
        self._mkfile(os.path.join("small", "f.bin"), 10)
        result = self.helper.clean(self.root, 1, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["deleted"], [])
        self.assertTrue(os.path.isdir(target))
        self.assertTrue(os.path.exists(os.path.join(target, "f.bin")))

    def test_run_mode_deletes_matched_dirs(self):
        """执行模式下命中的文件夹被递归删除"""
        self._mkfile(os.path.join("small", "f.bin"), 10)
        self._mkfile(os.path.join("big", "f.bin"), 2 * BYTES_PER_MB)
        result = self.helper.clean(self.root, 1, dry_run=False)
        self.assertFalse(os.path.exists(os.path.join(self.root, "small")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "big")))
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["deleted_bytes"], 10)

    def test_dry_run_and_run_report_same_targets(self):
        """预览与执行命中的是同一批目录"""
        self._mkfile(os.path.join("a", "f.bin"), 10)
        self._mkfile(os.path.join("b", "f.bin"), 20)
        preview = self.helper.clean(self.root, 1, dry_run=True)
        run = self.helper.clean(self.root, 1, dry_run=False)
        self.assertEqual(
            sorted(x["path"] for x in preview["matched"]),
            sorted(x["path"] for x in run["deleted"]))

    # ---------------- 隔离性 ----------------

    def test_root_dir_itself_not_deleted(self):
        """根目录本身永远不受影响"""
        self._mkfile(os.path.join("small", "f.bin"), 10)
        self.helper.clean(self.root, 1, dry_run=False)
        self.assertTrue(os.path.isdir(self.root))

    def test_loose_files_in_root_not_touched(self):
        """根目录下的散落文件不受影响"""
        loose = self._mkfile("loose.bin", 10)
        self._mkfile(os.path.join("small", "f.bin"), 10)
        self.helper.clean(self.root, 1, dry_run=False)
        self.assertTrue(os.path.exists(loose))
        self.assertFalse(os.path.exists(os.path.join(self.root, "small")))

    def test_loose_file_alone_triggers_nothing(self):
        """只有散落文件时没有待删项"""
        self._mkfile("loose.bin", 10)
        result = self.helper.clean(self.root, 100, dry_run=False)
        self.assertEqual(result["matched"], [])
        self.assertTrue(os.path.exists(os.path.join(self.root, "loose.bin")))

    # ---------------- 符号链接 ----------------

    def test_symlink_dir_skipped_by_default(self):
        """符号链接目录默认跳过：不计入大小也不删除"""
        if not self.symlink_supported:
            self.skipTest("当前环境不支持创建符号链接")
        outside = tempfile.mkdtemp(prefix="clean_helper_outside_")
        try:
            with open(os.path.join(outside, "big.bin"), "wb") as f:
                f.write(b"x" * (2 * BYTES_PER_MB))
            link = os.path.join(self.root, "linkdir")
            os.symlink(outside, link, target_is_directory=True)

            result = self.helper.scan(self.root, 10)
            # 链接本身不作为待删项
            self.assertNotIn("linkdir", self._names(result))
            self.assertTrue(any("符号链接" in s["reason"] for s in result["skipped"]))

            self.helper.clean(self.root, 10, dry_run=False)
            # 链接被保留，目标目录内容不受影响
            self.assertTrue(os.path.islink(link))
            self.assertTrue(os.path.exists(os.path.join(outside, "big.bin")))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_symlink_file_not_counted_in_size(self):
        """符号链接文件不计入目录大小"""
        if not self.symlink_supported:
            self.skipTest("当前环境不支持创建符号链接")
        target = self._mkfile("real.bin", 100)
        link = os.path.join(self.root, "d", "link.bin")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(target, link)
        size, skipped = self.helper.get_dir_size(os.path.join(self.root, "d"))
        self.assertEqual(size, 0)  # 链接本身不算
        self.assertTrue(any("符号链接" in s["reason"] for s in skipped))

    def test_symlink_dir_logic_skips_without_deleting(self):
        """符号链接目录逻辑：不列为待删项、记入 skipped、目标内容不受影响。

        用 mock 模拟「某个子目录是符号链接」，避免依赖系统创建 symlink 的权限。
        """
        outside = tempfile.mkdtemp(prefix="clean_helper_outside_")
        try:
            with open(os.path.join(outside, "big.bin"), "wb") as f:
                f.write(b"x" * (2 * BYTES_PER_MB))
            target = os.path.join(self.root, "linkdir")
            os.makedirs(target)
            real_islink = os.path.islink

            def fake_islink(p):
                if os.path.abspath(str(p)) == os.path.abspath(target):
                    return True
                return real_islink(p)

            with mock.patch("os.path.islink", side_effect=fake_islink):
                result = self.helper.scan(self.root, 10)
                self.assertNotIn("linkdir", self._names(result))
                self.assertTrue(any("符号链接" in s["reason"] for s in result["skipped"]))
                self.helper.clean(self.root, 10, dry_run=False)
                # 被当作链接的目录不会被删
                self.assertTrue(os.path.isdir(target))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_symlink_file_logic_not_counted_in_size(self):
        """符号链接文件逻辑：不计入大小、记入 skipped（用 mock 模拟，不依赖系统权限）。"""
        real = self._mkfile(os.path.join("d", "real.bin"), 100)
        fake_link = os.path.join(self.root, "d", "link.bin")
        with open(fake_link, "wb") as f:
            f.write(b"x" * 100)
        real_islink = os.path.islink

        def fake_islink(p):
            if os.path.abspath(str(p)) == os.path.abspath(fake_link):
                return True
            return real_islink(p)

        with mock.patch("os.path.islink", side_effect=fake_islink):
            size, skipped = self.helper.get_dir_size(os.path.join(self.root, "d"))
        # real.bin 计入，link.bin 不计入
        self.assertEqual(size, 100)
        self.assertTrue(any("符号链接" in s["reason"] for s in skipped))

    # ---------------- 异常容忍 ----------------

    def test_permission_error_on_file_does_not_break_scan(self):
        """单个文件读取异常时记录并跳过，不影响其他条目"""
        self._mkfile(os.path.join("mixed", "ok.bin"), 100)
        self._mkfile(os.path.join("mixed", "bad.bin"), 100)
        real_getsize = os.path.getsize

        def fake_getsize(path, *args, **kwargs):
            if str(path).endswith("bad.bin"):
                raise PermissionError("denied by test")
            return real_getsize(path, *args, **kwargs)

        with mock.patch("os.path.getsize", side_effect=fake_getsize):
            result = self.helper.scan(self.root, 1)
        # 扫描未中断，仍有结果返回
        self.assertIn("mixed", self._names(result))
        self.assertTrue(any("权限不足" in s["reason"] for s in result["skipped"]))

    def test_delete_failure_is_recorded_not_raised(self):
        """删除失败时记录到 failed 列表，不抛异常"""
        self._mkfile(os.path.join("small", "f.bin"), 10)
        with mock.patch("shutil.rmtree", side_effect=PermissionError("locked")):
            result = self.helper.clean(self.root, 1, dry_run=False)
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("权限不足", result["failed"][0]["reason"])

    def test_missing_root_returns_error(self):
        """根目录不存在时返回 error，不抛异常"""
        result = self.helper.clean(os.path.join(self.root, "no_such_dir"), 1)
        self.assertIn("error", result)
        self.assertEqual(result["matched"], [])

    def test_root_is_file_returns_error(self):
        """根路径是文件时返回 error"""
        f = self._mkfile("afile.bin", 10)
        result = self.helper.clean(f, 1)
        self.assertIn("error", result)

    def test_empty_root_path_returns_error(self):
        """未指定根目录时返回 error"""
        result = self.helper.scan("", 1)
        self.assertIn("error", result)

    # ---------------- 阈值归一化 ----------------

    def test_normalize_threshold_invalid_values(self):
        """非法阈值一律回落为 0"""
        for bad in (None, "", "abc", "nan", float("nan"), float("inf"), -5, "-1"):
            self.assertEqual(CleanHelper.normalize_threshold(bad), 0.0,
                             "阈值 %r 应归一化为 0" % (bad,))

    def test_normalize_threshold_valid_values(self):
        """合法阈值原样保留"""
        self.assertEqual(CleanHelper.normalize_threshold(10), 10.0)
        self.assertEqual(CleanHelper.normalize_threshold("2.5"), 2.5)
        self.assertEqual(CleanHelper.normalize_threshold(0), 0.0)

    def test_invalid_threshold_does_not_wipe_nonempty_dirs(self):
        """非法阈值回落为 0，只清空目录，不误删有内容的目录"""
        self._mkfile(os.path.join("hascontent", "f.bin"), 1)
        os.makedirs(os.path.join(self.root, "empty"))
        result = self.helper.clean(self.root, "not-a-number", dry_run=False)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "hascontent")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "empty")))

    # ---------------- 单位换算 ----------------

    def test_mb_conversion_is_binary(self):
        """1MB = 1024×1024 字节"""
        self.assertEqual(BYTES_PER_MB, 1024 * 1024)
        self._mkfile(os.path.join("one_mb", "f.bin"), 1024 * 1024)
        result = self.helper.scan(self.root, 1)
        self.assertIn("one_mb", self._names(result))

    # ---------------- 汇总统计 ----------------

    def test_total_free_bytes_accumulates(self):
        """预计释放空间 = 所有命中目录大小之和"""
        self._mkfile(os.path.join("a", "f.bin"), 10)
        self._mkfile(os.path.join("b", "f.bin"), 20)
        self._mkfile(os.path.join("c", "f.bin"), 2 * BYTES_PER_MB)  # 超阈值
        result = self.helper.scan(self.root, 1)
        self.assertEqual(result["total_free_bytes"], 30)
        self.assertEqual(result["total_dirs"], 3)

    def test_matched_sorted_by_size_asc(self):
        """命中清单按大小升序排列"""
        self._mkfile(os.path.join("big", "f.bin"), 900)
        self._mkfile(os.path.join("small", "f.bin"), 10)
        result = self.helper.scan(self.root, 1)
        sizes = [x["size_bytes"] for x in result["matched"]]
        self.assertEqual(sizes, sorted(sizes))

    def test_format_result_message_variants(self):
        """结果摘要文案覆盖预览与执行两种口径"""
        self._mkfile(os.path.join("a", "f.bin"), 10)
        preview = self.helper.clean(self.root, 1, dry_run=True)
        self.assertIn("预览完成", CleanHelper.format_result_message(preview))
        run = self.helper.clean(self.root, 1, dry_run=False)
        self.assertIn("清理完成", CleanHelper.format_result_message(run))
        self.assertIn("目录不存在", CleanHelper.format_result_message({"error": "目录不存在"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
