import os
import shutil
import time

import log
from app.utils import ExceptionUtils
from config import Config

# 1MB = 1024 x 1024 字节（固定换算口径，不做十进制/二进制切换）
BYTES_PER_MB = 1024 * 1024


class CleanHelper:
    """
    目录清理服务：扫描指定根目录下的文件夹，
    删除其中总大小小于等于阈值（单位 MB）的文件夹。

    设计约束：
      1. 根目录与阈值均为入参（可来自配置或接口），不写死；
      2. 文件夹大小按递归累加内部所有文件大小计算，1MB = 1024*1024 字节，空文件夹为 0；
      3. recursive=True（默认）时扫描**任意层级**的子文件夹（多级递归）；
         recursive=False 时退回旧行为，只处理根目录的「一级子文件夹」。
         两种模式下根目录本身与根目录下的散落文件都不受影响；
      4. 支持 dry_run：只扫描并返回待删除清单，不落盘删除；
      5. 权限不足、文件占用、符号链接等异常记录日志后跳过，不中断整体流程；
      6. 递归时做「命中即剪枝」：某目录命中阈值后不再下钻，
         避免既删父目录又删子目录导致重复统计与无效删除。
    """

    def __init__(self):
        # 上次扫描结果，供「先预览、后执行」两步式调用复用
        self._last_scan = None

    @staticmethod
    def get_default_config():
        """
        从配置文件读取默认的根目录与阈值，供前端预填。
        配置段（config/config.yaml）：
            clean_dirs:
              root_path: ''
              threshold_mb: 10
              recursive: true
        """
        try:
            conf = Config().get_config("clean_dirs") or {}
        except Exception as err:
            ExceptionUtils.exception_traceback(err)
            conf = {}
        if not isinstance(conf, dict):
            conf = {}
        root_path = conf.get("root_path") or ""
        threshold_mb = conf.get("threshold_mb", 10)
        try:
            threshold_mb = float(threshold_mb)
        except (TypeError, ValueError):
            threshold_mb = 10
        # 缺省为 True：包含子目录（多级递归）
        recursive = conf.get("recursive", True)
        if isinstance(recursive, str):
            recursive = recursive.strip().lower() not in ("false", "0", "no", "off", "")
        else:
            recursive = bool(recursive)
        return {
            "root_path": root_path,
            "threshold_mb": threshold_mb,
            "recursive": recursive
        }

    @staticmethod
    def normalize_threshold(threshold_mb):
        """
        归一化阈值参数：非法值（None / 空 / 非数字 / nan / inf / 负数）一律视为 0，
        即「删除所有自身大小为 0 的（空）文件夹」，避免误删全部。
        """
        if threshold_mb is None or threshold_mb == "":
            return 0.0
        try:
            value = float(threshold_mb)
        except (TypeError, ValueError):
            return 0.0
        # 挡掉 nan / inf
        if value != value or value in (float("inf"), float("-inf")):
            return 0.0
        if value < 0:
            return 0.0
        return value

    @staticmethod
    def normalize_recursive(value, default=True):
        """
        归一化「是否包含子目录」参数。

        Web 表单 / REST 传来的可能是 bool，也可能是字符串（"true"/"1"/"on"…）。
        None / 空串表示「没传」，此时取默认值（默认 True = 包含子目录）。
        """
        if value is None or value == "":
            return bool(default)
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "no", "off")
        return bool(value)

    def get_dir_size(self, dir_path, follow_links=False):
        """
        递归累加目录下所有文件的大小（单位：字节）。

        :param dir_path: 目标目录
        :param follow_links: 是否跟随符号链接目录（默认不跟随）
        :return: (总字节数, 跳过的条目列表) —— 跳过条目形如 {"path":..., "reason":...}
        """
        total_size = 0
        skipped = []
        if not dir_path or not os.path.isdir(dir_path):
            return 0, skipped

        for root, dirs, files in os.walk(dir_path, followlinks=follow_links):
            # 不跟随符号链接时，把指向别处的链接目录从遍历中剔除（链接本身不计入大小，也不递归）
            if not follow_links:
                keep_dirs = []
                for d in dirs:
                    full = os.path.join(root, d)
                    try:
                        if os.path.islink(full):
                            skipped.append({"path": full, "reason": "符号链接目录已跳过"})
                            continue
                    except OSError as err:
                        skipped.append({"path": full, "reason": f"链接状态检查失败：{err}"})
                        continue
                    keep_dirs.append(d)
                dirs[:] = keep_dirs

            for name in files:
                file_path = os.path.join(root, name)
                try:
                    # 符号链接文件不计入大小（islink 先判，避免被 getsize 跟随到目标）
                    if os.path.islink(file_path):
                        skipped.append({"path": file_path, "reason": "符号链接文件已跳过"})
                        continue
                    if not os.path.isfile(file_path):
                        skipped.append({"path": file_path, "reason": "非普通文件已跳过"})
                        continue
                    total_size += os.path.getsize(file_path)
                except PermissionError as err:
                    skipped.append({"path": file_path, "reason": f"权限不足：{err}"})
                except OSError as err:
                    skipped.append({"path": file_path, "reason": f"读取失败：{err}"})
                except Exception as err:  # noqa: BLE001 兜底，保证不中断
                    skipped.append({"path": file_path, "reason": f"未知异常：{err}"})
        return total_size, skipped

    def scan(self, root_path, threshold_mb, follow_links=False, recursive=True):
        """
        扫描根目录下的文件夹，返回大小 <= 阈值的清单。

        :param root_path: 根目录（仅其下的文件夹会被处理，根目录本身不动）
        :param threshold_mb: 阈值（MB），大小 <= 该值的文件夹判为待删
        :param follow_links: 是否跟随符号链接
        :param recursive: True = 递归扫描任意层级子目录；False = 只扫一级子目录
        :return: dict
        """
        threshold_value = self.normalize_threshold(threshold_mb)
        result = {
            "root_path": root_path,
            "threshold_mb": threshold_value,
            "threshold_bytes": int(threshold_value * BYTES_PER_MB),
            "recursive": bool(recursive),
            "total_dirs": 0,
            "matched": [],
            "skipped": [],
            "total_free_bytes": 0,
        }

        if not root_path:
            result["error"] = "未指定根目录"
            return result
        if not os.path.exists(root_path):
            result["error"] = f"根目录不存在：{root_path}"
            return result
        if not os.path.isdir(root_path):
            result["error"] = f"根路径不是目录：{root_path}"
            return result

        try:
            entries = sorted(os.listdir(root_path))
        except PermissionError as err:
            result["error"] = f"根目录无读取权限：{err}"
            return result
        except OSError as err:
            result["error"] = f"根目录读取失败：{err}"
            return result

        def handle_dir(sub_path, name, depth):
            """处理单个候选目录：算大小、判阈值、命中则剪枝"""
            size, sub_skipped = self.get_dir_size(sub_path, follow_links=follow_links)
            result["skipped"].extend(sub_skipped)
            result["total_dirs"] += 1
            if size <= result["threshold_bytes"]:
                result["matched"].append({
                    "path": sub_path,
                    "name": name,
                    "depth": depth,
                    "size_bytes": size,
                    "size_mb": round(size / BYTES_PER_MB, 3),
                })
                result["total_free_bytes"] += size
                # 命中即剪枝：父目录被删时子目录随之消失，
                # 再往下扫只会产生「已随父目录删除」的重复项与无效删除
                return True
            # 未命中且允许递归时，继续看它的子目录
            return False

        def walk(current_path, depth):
            """递归下钻 current_path 下的子文件夹（深度优先）"""
            try:
                sub_entries = sorted(os.listdir(current_path))
            except PermissionError as err:
                result["skipped"].append({"path": current_path, "reason": f"权限不足：{err}"})
                return
            except OSError as err:
                result["skipped"].append({"path": current_path, "reason": f"读取失败：{err}"})
                return
            for name in sub_entries:
                sub_path = os.path.join(current_path, name)
                # 只处理「子文件夹」：文件（含散落文件）与符号链接一律跳过
                try:
                    if os.path.islink(sub_path):
                        result["skipped"].append({"path": sub_path, "reason": "符号链接已跳过"})
                        continue
                    if not os.path.isdir(sub_path):
                        continue  # 散落文件：不受影响，也不记录
                except OSError as err:
                    result["skipped"].append({"path": sub_path, "reason": f"状态检查失败：{err}"})
                    continue
                if handle_dir(sub_path, name, depth):
                    continue  # 命中剪枝，不再下钻
                if recursive:
                    walk(sub_path, depth + 1)

        for name in entries:
            sub_path = os.path.join(root_path, name)
            try:
                if os.path.islink(sub_path):
                    result["skipped"].append({"path": sub_path, "reason": "符号链接已跳过"})
                    continue
                if not os.path.isdir(sub_path):
                    continue  # 根目录下的散落文件：不受影响，也不记录
            except OSError as err:
                result["skipped"].append({"path": sub_path, "reason": f"状态检查失败：{err}"})
                continue
            if handle_dir(sub_path, name, 1):
                continue
            if recursive:
                walk(sub_path, 2)

        # 待删清单按大小升序，便于从最小开始删
        result["matched"].sort(key=lambda x: x["size_bytes"])
        self._last_scan = result
        return result

    def clean(self, root_path, threshold_mb, dry_run=True, follow_links=False, recursive=True):
        """
        执行清理：先扫描，再按 dry_run 决定是否真正删除。

        :param dry_run: True = 只预览（不删除任何东西）；False = 真正执行删除
        :param recursive: True = 递归扫描任意层级子目录；False = 只扫一级子目录
        :return: dict（包含 matched 清单与已删除结果）
        """
        scan_result = self.scan(root_path, threshold_mb, follow_links=follow_links, recursive=recursive)
        result = dict(scan_result)
        result["dry_run"] = bool(dry_run)
        result["deleted"] = []
        result["failed"] = []
        result["deleted_bytes"] = 0

        if scan_result.get("error"):
            return result

        if dry_run:
            # 预览模式：不落盘，只回清单
            return result

        for item in scan_result["matched"]:
            target = item["path"]
            try:
                # 删除前二次确认仍是目录，防止扫描与删除之间被替换
                if not os.path.isdir(target):
                    result["failed"].append({"path": target, "reason": "目录已不存在"})
                    continue
                shutil.rmtree(target)
                result["deleted"].append({
                    "path": target,
                    "name": item["name"],
                    "depth": item.get("depth", 1),
                    "size_bytes": item["size_bytes"],
                    "size_mb": item["size_mb"],
                })
                result["deleted_bytes"] += item["size_bytes"]
                log.info(f"【Clean】已删除目录：{target}（{item['size_mb']} MB）")
            except PermissionError as err:
                reason = f"权限不足：{err}"
                result["failed"].append({"path": target, "reason": reason})
                log.error(f"【Clean】删除目录失败 {target}：{reason}")
            except OSError as err:
                reason = f"删除失败（可能被占用）：{err}"
                result["failed"].append({"path": target, "reason": reason})
                log.error(f"【Clean】删除目录失败 {target}：{reason}")
            except Exception as err:  # noqa: BLE001 兜底，不中断整体流程
                ExceptionUtils.exception_traceback(err)
                reason = f"未知异常：{err}"
                result["failed"].append({"path": target, "reason": reason})
                log.error(f"【Clean】删除目录失败 {target}：{reason}")

        result["deleted_count"] = len(result["deleted"])
        result["failed_count"] = len(result["failed"])
        return result

    @staticmethod
    def format_result_message(result):
        """
        生成人类可读的清理结果摘要
        """
        if result.get("error"):
            return result["error"]
        scope = "各级子文件夹" if result.get("recursive") else "一级子文件夹"
        # 未声明 dry_run 的裸扫描结果，也按预览口径描述
        if result.get("dry_run") or "deleted" not in result:
            released_mb = round(result.get("total_free_bytes", 0) / BYTES_PER_MB, 2)
            return (f"预览完成：扫描 {result.get('total_dirs', 0)} 个{scope}，"
                    f"命中 {len(result.get('matched', []))} 个，"
                    f"预计可释放 {released_mb} MB（未执行删除）")
        released = round(result.get("deleted_bytes", 0) / BYTES_PER_MB, 2)
        return (f"清理完成：删除 {result.get('deleted_count', 0)} 个文件夹，"
                f"实际释放 {released} MB，"
                f"失败 {result.get('failed_count', 0)} 个")
