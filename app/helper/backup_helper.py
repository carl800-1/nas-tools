import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import log
from app.utils import ExceptionUtils
from config import Config
from version import APP_VERSION

# 备份文件内的元数据文件名
MANIFEST_FILE = "nastool_backup.json"
# 备份文件格式版本：1 = 旧版本（无元数据），2 = 当前（带元数据、可分包恢复）
FORMAT_VERSION = 2
# 主数据库文件名
USER_DB = "user.db"
# 配置文件名（不属于分类策略）
CONFIG_FILE = "config.yaml"
# 兜底条目：未被其它条目覆盖的表（用于兼容后续版本新增的表）
OTHERS_KEY = "others"

# user.db 中不属于业务数据的内部表，不参与备份/恢复
IGNORE_TABLES = ("alembic_version", "sqlite_sequence")

# ---------------------------------------------------------------------------
# 备份/恢复条目定义
#   type = file   配置目录下的指定文件
#   type = yaml   配置目录下的全部 *.yaml（config.yaml 除外，即二级分类策略）
#   type = table  user.db 中的表
#   type = others 兜底：未被其它条目覆盖的表
#   default       是否默认勾选
# ---------------------------------------------------------------------------
BACKUP_ITEMS = [
    {
        "key": "config",
        "name": "基础设置",
        "desc": "config.yaml：日志、TMDB、媒体库目录、分类策略名、站点搜索、安全、实验室等全部WEB设置",
        "type": "file",
        "files": [CONFIG_FILE],
        "default": True
    },
    {
        "key": "category",
        "name": "二级分类策略",
        "desc": "配置目录下的全部分类策略文件（default-category.yaml 及自定义策略）",
        "type": "yaml",
        "default": True
    },
    {
        "key": "site",
        "name": "站点",
        "desc": "「站点维护」中维护的全部站点，含优先级、RSS地址、签到地址、Cookie、API Key、RSS规则及站点图标",
        "type": "table",
        "tables": ["CONFIG_SITE", "SITE_FAVICON"],
        "default": True
    },
    {
        "key": "brush",
        "name": "刷流任务",
        "desc": "「刷流」中的全部任务设置，含站点、RSS规则、免费/删种/分享率规则、做种体积、上下行限速、保存目录、下载器、周期等，以及已下载种子记录",
        "type": "table",
        "tables": ["SITE_BRUSH_TASK", "SITE_BRUSH_TORRENTS"],
        "default": True
    },
    {
        "key": "downloader",
        "name": "下载器与同步目录",
        "desc": "下载器配置、下载设置及目录同步路径",
        "type": "table",
        "tables": ["DOWNLOADER", "DOWNLOAD_SETTING", "CONFIG_SYNC_PATHS"],
        "default": True
    },
    {
        "key": "rss",
        "name": "订阅与过滤规则",
        "desc": "电影/电视剧订阅、订阅解析规则、过滤规则组及规则、自动删种任务、识别词与识别词组",
        "type": "table",
        "tables": ["RSS_MOVIES", "RSS_TVS", "RSS_TV_EPISODES", "CONFIG_RSS_PARSER",
                   "CONFIG_FILTER_GROUP", "CONFIG_FILTER_RULES", "TORRENT_REMOVE_TASK",
                   "CUSTOM_WORDS", "CUSTOM_WORD_GROUPS"],
        "default": True
    },
    {
        "key": "user",
        "name": "用户与权限",
        "desc": "用户列表、权限设置及用户个性化订阅配置",
        "type": "table",
        "tables": ["CONFIG_USERS", "CONFIG_USER_RSS"],
        "default": True
    },
    {
        "key": "message",
        "name": "消息通知渠道",
        "desc": "各消息通知渠道的配置与开关",
        "type": "table",
        "tables": ["MESSAGE_CLIENT"],
        "default": True
    },
    {
        "key": "system",
        "name": "系统设置项",
        "desc": "默认下载器/默认下载设置/默认订阅设置、索引站点、刮削配置、CookieCloud、自定义JS与CSS、媒体库展示模块等",
        "type": "table",
        "tables": ["SYSTEM_DICT"],
        "default": True
    },
    {
        "key": "media",
        "name": "媒体库同步数据",
        "desc": "媒体库同步条目与统计（media.db），用于媒体库页面的展示",
        "type": "file",
        "files": ["media.db"],
        "default": True
    },
    {
        "key": "history",
        "name": "历史与统计记录",
        "desc": "下载/转移/同步/订阅历史、搜索缓存、站点流量与做种统计等，数据量大且无恢复必要，默认不备份",
        "type": "table",
        "tables": ["TRANSFER_HISTORY", "TRANSFER_UNKNOWN", "TRANSFER_BLACKLIST",
                   "SYNC_HISTORY", "DOWNLOAD_HISTORY", "SEARCH_RESULT_INFO",
                   "RSS_TORRENTS", "RSS_HISTORY", "USERRSS_TASK_HISTORY", "PLUGIN_HISTORY",
                   "SITE_STATISTICS_HISTORY", "SITE_USER_INFO_STATS",
                   "SITE_USER_SEEDING_INFO", "INDEXER_STATISTICS"],
        "default": False
    },
    {
        "key": OTHERS_KEY,
        "name": "其它数据",
        "desc": "未被其它分类覆盖的数据表（随版本升级新增的表会自动归入此项），建议保留勾选",
        "type": "others",
        "tables": [],
        "default": True
    }
]


class BackupHelper:
    """
    配置备份与恢复

    备份：把用户配置拆分为若干可勾选条目，打包为 zip。
          zip 内保持扁平结构（config.yaml / *.yaml / user.db / media.db / nastool_backup.json），
          因此旧版本生成的备份文件仍可直接恢复。
    恢复：先解析备份文件内容，再按用户勾选的条目逐项还原，
          数据库按表还原（不替换整个 user.db），因此不会破坏数据库的版本记录。
    """

    # ------------------------------------------------------------------ 工具

    @staticmethod
    def get_items():
        """
        获取备份/恢复条目定义，供前端渲染勾选项
        """
        return [{
            "key": item.get("key"),
            "name": item.get("name"),
            "desc": item.get("desc"),
            "default": bool(item.get("default", True))
        } for item in BACKUP_ITEMS]

    @staticmethod
    def __item_map():
        return {item.get("key"): item for item in BACKUP_ITEMS}

    @staticmethod
    def __tables_of(item):
        return list(item.get("tables") or [])

    @staticmethod
    def __defined_tables():
        """
        已经归属于具体条目的表（不含兜底条目）
        """
        tables = []
        for item in BACKUP_ITEMS:
            if item.get("key") == OTHERS_KEY:
                continue
            for table in BackupHelper.__tables_of(item):
                if table not in tables:
                    tables.append(table)
        return tables

    @staticmethod
    def __default_items():
        return [item.get("key") for item in BACKUP_ITEMS if item.get("default", True)]

    @staticmethod
    def __norm_items(items):
        """
        规整勾选项：支持逗号分隔字符串，过滤未知条目并去重（保持定义顺序）
        """
        if not items:
            return []
        if isinstance(items, str):
            items = items.split(",")
        item_map = BackupHelper.__item_map()
        keys = []
        for key in items:
            key = str(key).strip()
            if key and key in item_map and key not in keys:
                keys.append(key)
        return keys

    @staticmethod
    def __list_tables(db_path):
        """
        读取数据库中的业务表名
        """
        tables = []
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for row in cursor.fetchall():
                name = row[0]
                if not name or name in IGNORE_TABLES or name.startswith("sqlite_"):
                    continue
                tables.append(name)
            cursor.close()
            conn.close()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
        return tables

    @staticmethod
    def __table_columns(db_path, table):
        """
        读取表的字段名
        """
        columns = []
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('PRAGMA table_info("%s")' % table)
            columns = [row[1] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
        return columns

    @staticmethod
    def __table_count(db_path, table):
        """
        读取表的记录数
        """
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(1) FROM "%s"' % table)
            count = cursor.fetchone()[0]
            cursor.close()
            conn.close()
            return count
        except Exception:
            return 0

    @staticmethod
    def __read_manifest(path):
        manifest_path = os.path.join(path, MANIFEST_FILE)
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
            except Exception as e:
                ExceptionUtils.exception_traceback(e)
        return {}

    @staticmethod
    def __detect_items(path):
        """
        解析备份文件内容，返回其中实际包含的可恢复条目

        :param path: 备份文件解压后的目录
        :return: [{"key","name","desc","default","detail":[...], "tables":[...]}]
        """
        names = set(os.listdir(path))
        db_path = os.path.join(path, USER_DB)
        db_tables = BackupHelper.__list_tables(db_path) if os.path.exists(db_path) else []
        defined_tables = BackupHelper.__defined_tables()

        items = []
        for item in BACKUP_ITEMS:
            key = item.get("key")
            itype = item.get("type")
            detail = []
            tables = []
            if itype == "file":
                detail = [name for name in (item.get("files") or []) if name in names]
            elif itype == "yaml":
                detail = [name for name in sorted(names)
                          if name.lower().endswith(".yaml") and name != CONFIG_FILE]
            elif itype == "table":
                detail = tables = [t for t in BackupHelper.__tables_of(item) if t in db_tables]
            elif itype == "others":
                detail = tables = [t for t in db_tables if t not in defined_tables]
            if detail:
                items.append({
                    "key": key,
                    "name": item.get("name"),
                    "desc": item.get("desc"),
                    "type": itype,
                    "default": bool(item.get("default", True)),
                    "detail": detail,
                    "tables": tables
                })
        return items

    # ------------------------------------------------------------------ 备份

    @staticmethod
    def backup(items=None, full_backup=False, bk_path=None):
        """
        创建备份文件

        :param items       需要备份的条目（key 列表或逗号分隔字符串），为空时按默认勾选项
        :param full_backup 是否完整备份（含历史记录在内的全部条目）
        :param bk_path     自定义备份路径
        :return: 备份文件（zip）路径，失败返回 None
        """
        try:
            config_path = Path(Config().get_config_path())
            backup_name = "bk_" + time.strftime('%Y%m%d%H%M%S')
            if bk_path:
                backup_path = Path(bk_path) / backup_name
            else:
                backup_path = config_path / "backup_file" / backup_name
            backup_path.mkdir(parents=True, exist_ok=True)

            item_map = BackupHelper.__item_map()
            if full_backup:
                selected = [item.get("key") for item in BACKUP_ITEMS]
            else:
                selected = BackupHelper.__norm_items(items) or BackupHelper.__default_items()

            manifest = {
                "format": FORMAT_VERSION,
                "app_version": APP_VERSION,
                "created_at": time.strftime('%Y-%m-%d %H:%M:%S'),
                "files": [],
                "items": []
            }

            # 1、文件类条目（配置项与分类策略）
            for key in selected:
                item = item_map.get(key) or {}
                if item.get("type") == "file":
                    for name in (item.get("files") or []):
                        src = config_path / name
                        if src.exists():
                            shutil.copy(str(src), str(backup_path / name))
                            manifest["files"].append(name)
                elif item.get("type") == "yaml":
                    for src in sorted(config_path.glob("*.yaml")):
                        if src.name == CONFIG_FILE:
                            continue
                        shutil.copy(str(src), str(backup_path / src.name))
                        manifest["files"].append(src.name)

            # 2、数据库表条目：先整体复制，再删除未勾选的表，使备份文件只包含需要恢复的内容
            live_db = config_path / USER_DB
            live_tables = BackupHelper.__list_tables(str(live_db)) if live_db.exists() else []
            defined_tables = BackupHelper.__defined_tables()

            selected_tables = []
            for key in selected:
                item = item_map.get(key) or {}
                if item.get("type") == "table":
                    selected_tables.extend(BackupHelper.__tables_of(item))
                elif item.get("type") == "others":
                    selected_tables.extend([t for t in live_tables if t not in defined_tables])
            selected_tables = list(dict.fromkeys([t for t in selected_tables if t in live_tables]))

            if selected_tables and live_db.exists():
                shutil.copy(str(live_db), str(backup_path / USER_DB))
                backup_db = str(backup_path / USER_DB)
                drop_tables = [t for t in live_tables if t not in selected_tables]
                conn = sqlite3.connect(backup_db)
                try:
                    cursor = conn.cursor()
                    for table in drop_tables:
                        cursor.execute('DROP TABLE IF EXISTS "%s";' % table)
                    conn.commit()
                    cursor.close()
                finally:
                    conn.close()

            # 3、记录各条目内容，便于恢复前预览
            backup_db = str(backup_path / USER_DB)
            has_db = os.path.exists(backup_db)
            for key in selected:
                item = item_map.get(key) or {}
                itype = item.get("type")
                record = {"key": key, "name": item.get("name"), "files": [], "tables": [], "rows": 0}
                if itype == "file":
                    record["files"] = [n for n in (item.get("files") or []) if n in manifest["files"]]
                elif itype == "yaml":
                    record["files"] = [n for n in manifest["files"]
                                       if n.lower().endswith(".yaml") and n != CONFIG_FILE]
                elif itype == "table":
                    record["tables"] = [t for t in BackupHelper.__tables_of(item) if t in selected_tables]
                elif itype == "others":
                    record["tables"] = [t for t in selected_tables if t not in defined_tables]
                if has_db:
                    for table in record["tables"]:
                        record["rows"] += BackupHelper.__table_count(backup_db, table)
                if record["files"] or record["tables"]:
                    manifest["items"].append(record)

            # 4、写入元数据并打包
            with open(str(backup_path / MANIFEST_FILE), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            zip_file = str(backup_path) + '.zip'
            shutil.make_archive(str(backup_path), 'zip', str(backup_path))
            shutil.rmtree(str(backup_path))

            log.info("【Backup】备份完成：%s，包含条目 %s" % (
                os.path.basename(zip_file),
                "、".join([r.get("name") for r in manifest["items"]]) or "无"))
            return zip_file
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return None

    # ------------------------------------------------------------------ 恢复

    @staticmethod
    def get_info(file_name):
        """
        解析备份文件，返回其中包含的可恢复条目与基本信息

        :param file_name: 已上传到临时目录的备份文件名
        """
        temp_path = Config().get_temp_path()
        zip_path = os.path.join(temp_path, file_name)
        if not os.path.exists(zip_path):
            return {"code": 1, "msg": "备份文件不存在，请重新上传"}

        unpack_path = os.path.join(temp_path, "bk_preview_" + str(int(time.time())))
        try:
            os.makedirs(unpack_path, exist_ok=True)
            shutil.unpack_archive(zip_path, unpack_path, format='zip')
            manifest = BackupHelper.__read_manifest(unpack_path)
            items = BackupHelper.__detect_items(unpack_path)
            return {
                "code": 0,
                "msg": "",
                "legacy": not manifest,
                "info": {
                    "app_version": manifest.get("app_version") or "",
                    "created_at": manifest.get("created_at") or ""
                },
                "items": items
            }
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return {"code": 1, "msg": "备份文件解析失败：%s" % str(e)}
        finally:
            if os.path.exists(unpack_path):
                shutil.rmtree(unpack_path, ignore_errors=True)

    @staticmethod
    def restore(file_name, items=None):
        """
        按勾选条目恢复备份文件

        :param file_name: 已上传到临时目录的备份文件名
        :param items     需要恢复的条目（key 列表或逗号分隔字符串），为空时恢复备份文件中的全部内容
        """
        temp_path = Config().get_temp_path()
        config_path = Config().get_config_path()
        zip_path = os.path.join(temp_path, file_name)
        if not os.path.exists(zip_path):
            return {"code": 1, "msg": "备份文件不存在，请重新上传"}

        unpack_path = os.path.join(temp_path, "bk_restore_" + str(int(time.time())))
        success = False
        try:
            os.makedirs(unpack_path, exist_ok=True)
            shutil.unpack_archive(zip_path, unpack_path, format='zip')

            available = BackupHelper.__detect_items(unpack_path)
            available_map = {item.get("key"): item for item in available}
            selected = BackupHelper.__norm_items(items)
            if selected:
                selected = [key for key in selected if key in available_map]
            else:
                selected = [item.get("key") for item in available]
            if not selected:
                return {"code": 1, "msg": "备份文件中没有可恢复的内容，或未勾选任何恢复项"}

            restored = []

            # 1、文件类条目（基础设置与分类策略）
            for key in selected:
                item = available_map.get(key) or {}
                if item.get("type") not in ("file", "yaml"):
                    continue
                for name in item.get("detail") or []:
                    src = os.path.join(unpack_path, name)
                    if os.path.exists(src):
                        shutil.copy(src, os.path.join(config_path, name))
                        restored.append(name)

            # 2、数据库表条目：按表还原，不替换整个 user.db
            selected_tables = []
            for key in selected:
                for table in (available_map.get(key) or {}).get("tables") or []:
                    if table not in selected_tables:
                        selected_tables.append(table)

            restored_tables = {}
            if selected_tables:
                backup_db = os.path.join(unpack_path, USER_DB)
                live_db = os.path.join(config_path, USER_DB)
                if not os.path.exists(backup_db):
                    return {"code": 1, "msg": "备份文件缺少数据库文件，无法恢复所选内容"}
                if not os.path.exists(live_db):
                    return {"code": 1, "msg": "当前配置目录下未找到 %s" % USER_DB}
                restored_tables = BackupHelper.__restore_tables(backup_db, live_db, selected_tables)

            names = [item.get("name") for item in available if item.get("key") in selected]
            log.info("【Backup】恢复完成：%s，包含条目 %s" % (file_name, "、".join(names) or "无"))
            success = True
            return {
                "code": 0,
                "msg": "",
                "restored": names,
                "files": restored,
                "tables": restored_tables
            }
        except sqlite3.OperationalError as e:
            ExceptionUtils.exception_traceback(e)
            return {"code": 1, "msg": "数据库被占用，恢复失败，请稍后重试（%s）" % str(e)}
        except Exception as e:
            ExceptionUtils.exception_traceback(e)
            return {"code": 1, "msg": str(e)}
        finally:
            if os.path.exists(unpack_path):
                shutil.rmtree(unpack_path, ignore_errors=True)
            # 恢复失败时保留上传的备份文件，便于直接重试
            if success and os.path.exists(zip_path):
                os.remove(zip_path)

    @staticmethod
    def __restore_tables(backup_db, live_db, tables):
        """
        把备份库中指定表的数据覆盖到当前库

        只复制两边都存在的字段，字段不同的表也能最大限度还原；
        整个过程放在一个事务里，任一步失败即整体回滚。

        :return: {表名: 恢复的记录数}
        """
        stats = {}
        backup_conn = sqlite3.connect(backup_db)
        live_conn = sqlite3.connect(live_db, timeout=30)
        try:
            backup_cursor = backup_conn.cursor()
            live_cursor = live_conn.cursor()
            live_conn.execute("PRAGMA foreign_keys=OFF")
            for table in tables:
                backup_columns = [row[1] for row in
                                  backup_cursor.execute('PRAGMA table_info("%s")' % table).fetchall()]
                live_columns = [row[1] for row in
                                live_cursor.execute('PRAGMA table_info("%s")' % table).fetchall()]
                if not live_columns:
                    log.warn("【Backup】当前数据库不存在表 %s，已跳过" % table)
                    continue
                columns = [col for col in backup_columns if col in live_columns]
                if not columns:
                    log.warn("【Backup】表 %s 与当前数据库字段不匹配，已跳过" % table)
                    continue
                column_sql = ", ".join(['"%s"' % col for col in columns])
                rows = backup_cursor.execute('SELECT %s FROM "%s"' % (column_sql, table)).fetchall()
                live_cursor.execute('DELETE FROM "%s"' % table)
                if rows:
                    placeholders = ", ".join(["?"] * len(columns))
                    live_cursor.executemany(
                        'INSERT INTO "%s" (%s) VALUES (%s)' % (table, column_sql, placeholders), rows)
                stats[table] = len(rows)
            live_conn.commit()
            live_cursor.close()
            backup_cursor.close()
            return stats
        except Exception:
            live_conn.rollback()
            raise
        finally:
            backup_conn.close()
            live_conn.close()
