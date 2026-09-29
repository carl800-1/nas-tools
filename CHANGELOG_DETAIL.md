# 改动细节档案（CHANGELOG_DETAIL）

> 本文件存放各版本「怎么改的」实现细节：涉及的文件、测试、踩过的坑。
> 用户向的发布说明见 [CHANGELOG.md](CHANGELOG.md)（只写修复 / 新增 / 怎么用）。

# v6.0.8 (2026-09-29) — 我的媒体库同步全面修复

## 症状

点「媒体库同步」后弹窗永久停在 **「正在获取 XXX 数据...」**，进度条不动、不报错、结束不了；
`media.db` 里一条记录都没有，页面显示 0 条。Emby / Jellyfin / Plex 均命中；
绿联（Ugreen）因 `get_items` 恰好返回 list 而幸免。

## 根因（一条主因 + 四条放大器）

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `app/mediaserver/media_server.py` | `items = self.get_items(lib_id)` 之后直接 `len(items)`，而 emby/jellyfin/plex 的 `get_items` 是**生成器** | `TypeError: object of type 'generator' has no len()`，整轮同步在第一个库就崩 |
| 2 | 同上 | `sync_mediaserver` 没有兜底，异常直接冒泡出线程 | `progress.end()` 永不执行 ⇒ 前端 `refresh_process` 拿到的 value 永远 < 100 ⇒ **弹窗永久卡在「正在获取...」** |
| 3 | `app/mediaserver/client/emby.py`（jellyfin 同构） | `get_items` 里逐个条目调 `get_iteminfo`，该函数在「无响应 / 非 200」时**隐式返回 None** | 下一行 `item_info.get(...)` 抛 `AttributeError`，被外层 `except` 吞掉后**整个库的 for 循环直接中断**，后面的条目全丢 |
| 4 | 同上 | `get_libraries` 只认 `CollectionType` 为 `movies` / `tvshows` | **混合库（该字段为 null）**、`musicvideos`、`homevideos` 三类库被静默丢弃 ——「动画电影」「综艺」「纪录片」这些库在同步列表里根本不存在 |
| 5 | `app/db/media_db.py` | `query()` 只按 `SERVER` + `TMDBID`/`TITLE` 查，**不区分媒体类型** | 同名电影与剧集（如《三体》）互相命中，把另一类型误判为「已存在」 |

## 改动文件

### `app/mediaserver/media_server.py`

- `get_items` 结果先物化成 `list` 再计数与遍历（`list(self.get_items(lib_id) or [])`）；
- 同步主体拆出 `__sync_mediaserver_impl()`，由 `sync_mediaserver()` 用 **`try/except/finally`** 包裹，**任何**异常都保证 `progress.end()` 落地并写明「同步异常终止」；
- 条目级 `try/except`：单条入库失败只记日志跳过，不再中断整库；
- `check_item_exists` 把媒体类型下推给 `media_db.query(mtype=...)`，从 SQL 层收敛同名电影/剧集。

### `app/mediaserver/client/emby.py`（`jellyfin.py` 同步修）

- `get_iteminfo` 统一契约：无响应 / 非 200 / 抛异常 / `json()` 为 None **一律返回 `{}`**；
- `get_items` 增加**条目级** `try/except`，单条详情失败时降级使用列表接口自带的字段（`Name`/`Type`/`ProductionYear`），**条目不丢、循环不断**；
- 类型分派补全：`BoxSet`（合集）、`Season`（季）、`Folder`（目录）继续下钻，单独出现的 `Episode` 按条目登记；
- 条目补 `image` / `link` 字段，与绿联客户端口径一致；
- `get_libraries` 的 `CollectionType` 兜底：`None`/空 → 混合库放行（类型留空，逐条判定），新增 `musicvideos` / `homevideos`；`music`/`books`/`games`/`livetv`/`channels` 跳过并**打日志**。

### `app/mediaserver/client/plex.py`

- `get_items` 增加条目级 `try/except`，并补 `image` / `link`；
- `get_libraries` 跳过非影视库时补日志（原先静默 `continue`，用户完全无从排障）。

### `app/db/media_db.py`

- `query()` 新增可选 `mtype` 参数，按 `ITEM_TYPE` 收敛电影/剧集，别名表覆盖中文（`电影`/`电视剧`）与英文（`Movie`/`Series`/`show`/`movies`/`tvshows`）；无法归一的类型宽松放行，避免升级后老数据被判为「不存在」；
- 归一在 `@cached` **外层**完成（cachetools 会先对实参取 hash，传入不可哈希对象时会在进函数体之前就抛 `TypeError`）；
- `insert` / `empty` 成功后清理查询缓存，避免缓存里残留被删除的 ORM 实例（`DetachedInstanceError`）。

### `web/templates/index.html`

- 媒体库列表加 **「全选 / 全不选」** 与 **「已选 N / M 个库」** 实时计数；
- **一个库都没勾选**时点「开始同步」会先弹确认。

## 测试

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `_verify_mediasync_v608.py` | 源码断言 + 前端 JS 配平 + 端到端桩执行（7 场景） | 51 / 51 |
| `_verify_mediasync_clients_v608.py` | emby 客户端四类失败路径 / 容器下钻 / 混合库取舍 | 37 / 37 |
| `_verify_media_db_v608.py` | 真实 SQLite 验证类型收敛、缓存键可哈希、缓存隔离 | 28 / 28 |

另：`python -m compileall app web config.py version.py` 全量通过；全部改动文件保持原有行尾（纯 CRLF）。

## 兼容性

- `media_db.query` 的 `mtype` 为**可选**参数，不传时行为与旧版一致；
- `MediaType` 枚举、`SyncLibrary` 配置、`MEDIASYNC_ITEMS` 表结构**均未变**，无需迁移；
- 混合库的 `type` 为**空字符串**，`sync_library` 里按 `movie`/`tv` 归档时不会被误选，按库 ID 勾选则照常同步。

## 踩过的坑

- **`ast.unparse` 抽取方法体放进桩类执行时，私有方法名不会触发名字改写**：`self.__foo()` 经 `ast.unparse` 输出后仍是字面 `__foo` 调用（Python 只在编译期对类体内出现的方法名改写）。因此桩类必须用 `setattr(cls, "__foo", fn)` 按字面双下划线名绑定；反之在类体内写 `X = fn` 会被改写成 `_Cls__X`，两边都对不上。
- **`@cached` 的实参哈希发生在函数体之前**：想在函数体里判 `None` / 归一类型来规避不可哈希实参是徒劳的，必须先在外层把实参归一成字符串，再调用被缓存的内部方法。

---

# v6.0.7 (2026-09-24) — 目录清理

## 新增文件
| 文件 | 说明 |
|---|---|
| `app/helper/clean_helper.py` | `CleanHelper`：核心服务（扫描 / 删除 / 结果摘要），无 UI 依赖 |
| `tests/test_clean_helper.py` | 31 项单元测试（阈值边界、嵌套、空目录、dry-run、符号链接、异常容忍等） |

## 改动文件
| 文件 | 改动 |
|---|---|
| `web/backend/pro_user.py` | `SERVICE_CONF` 新增 `clean_dirs` 卡片（服务菜单入口） |
| `web/action.py` | 注册 `clean_dirs_scan`（预览）/ `clean_dirs_run`（执行）两个 action |
| `web/templates/service.html` | 新增 `modal-clean-dirs` 弹窗：填参数 → 预览清单 → 二次确认 → 执行 |
| `web/main.py` | `service()` 路由传入配置默认值（`CleanDefaultRoot` / `CleanDefaultThreshold`） |
| `web/apiv1.py` | 新增 REST 接口 `/system/clean_dirs/scan` 与 `/system/clean_dirs/run` |
| `config/config.yaml` | 新增 `clean_dirs` 段（`root_path` / `threshold_mb`） |
| `README.md` | 新增 §2.27 说明 |

## 行为口径
- **判据**：子文件夹总大小 **≤ 阈值** 即命中；大小等于阈值也删。
- **单位**：1MB = 1024×1024 字节；空文件夹计为 0。
- **范围**：仅根目录的一级子文件夹；根目录本身与根目录下散落文件不受影响。
- **删除方式**：`shutil.rmtree` 递归删除整个文件夹。
- **dry-run**：预览模式只返回清单与预计释放空间，不落盘。
- **容错**：权限不足 / 文件占用 / 符号链接等记录日志并跳过，不中断；符号链接默认不跟随。
- **参数校验**：阈值非法（空 / 非数字 / nan / inf / 负数）回落为 0，避免误删有内容目录。

## 测试
`python -m unittest tests.test_clean_helper` → **31 项通过**（2 项依赖系统 symlink 权限的用例在无权限环境下自动跳过，其逻辑另由 mock 版用例覆盖）。

# v6.0.6 (2026-09-24) — 刷流弹窗版式收紧

## 改动（仅 `web/templates/site/brushtask.html`）
| 项 | 改动 |
|---|---|
| 保存目录 | 从独占一整行（`col-lg-12`）上移到与「标签 / 任务时长」同行（`col-lg-4`，约占内容宽 32%） |
| 4 个开关间距 | `me-4`（24px）→ `me-2`（8px），省 48px，确保一排放得下 |
| 预览/断言脚本 | 新增 720px 窄档硬断言 —— 真实渲染比 800px 预览更紧，预览必须比真实更严才可信 |

## 为什么上次没拦住
v6.0.5 的预览只在 800px 弹窗宽度下断言「4 开关一排」，实际浏览器渲染宽度更紧，导致 `me-4` 间距下第 4 个开关溢出换行。这次把断言压到 720px 再验一次，实测 800px 与 720px 双档均一排。

# v6.0.5 (2026-09-24) — 刷流弹窗精简 + 转移到媒体库开关修复

## 弹窗精简（只动 `web/templates/site/brushtask.html`）
| 项 | 改动 |
|---|---|
| RSS 地址 | 输入框去掉，保存目录由 `col-lg` 改为 `col-lg-12` 占满整行 |
| 同时下载任务数 | 去掉（输入框 + 校验 + 编辑回填 + 提交参数） |
| 部分下载（拆包）规则 | 整块去掉（3 个输入框 + 约 60 行校验逻辑） |
| 包含 / 排除 | 从选种规则中间移到该区**末尾**（发布年份之后） |
| 4 个开关 | `row + col-lg-4`（一行只放 3 个）改为 `d-flex flex-wrap` 一排 |

RSS 字段改为隐藏域，**提交参数（`brushtask_rssurl`）、编辑回填（`rss_url_show`）、新建清空三处接线保持不变** —— 老任务里已填的自定义 RSS 不会被编辑保存时静默清空，语义仍是「留空则用站点配置的 RSS」。

### 行为口径：保存时一并清空
前端不再提交「同时下载任务数」与「部分下载规则」→ 后端取到 `None` → 落库即空。**后端字段与开放 API 一行未动**（`apiv1.py` 的 `brushtask_dlcount` / `frac_*` 仍在）。
- **未重新保存的老任务仍按库里旧值运行**（任务卡片会继续显示「同时下载: N」徽标，正好当提示）
- **重新保存一次后**：不再限制同时下载数、不再走拆包下载
- 想恢复这两项能力：把字段加回界面即可，后端逻辑没坏

顺带修掉一个旧隐患：`dlcount` / `frac_*` 在「新建任务」时**从来没被清空过**（编辑完 A 再点「新建」会残留 A 的拆包规则），字段删掉后这个问题自动消失。

## 修复：刷流「转移到媒体库」开关的两处缺口
刷流任务的「转移到媒体库」关掉后，本意是「只做种、不整理」。之前有两种情况会漏掉：

1. **不勾「识别重命名」时开关失效**：守卫只挂在「识别转移」分支上，「只转移不识别（`__link`）」的两个入口 `file_change_handler` / `transfer_sync` 都没有守卫 → 关了开关照样被 link 进媒体库。
2. **种子刚下载完成的一瞬间可能被整理**：刷流跳过清单有 60 秒缓存，而路径判定只查一次。「下载目录 == 目录同步源目录 == 刷流保存目录」时，文件刚落地、清单还没刷新，就会被当普通文件整理入库（最长 60 秒窗口）。

### 改动
- `app/sync.py`：`file_change_handler` / `transfer_sync` 的 `__link` 分支补刷流守卫（守卫排在 `__link` 调用**之前**，不勾「识别重命名」时开关同样生效）
- `app/downloader/downloader.py`：
  - 新增 `BRUSH_SKIP_RECHECK_INTERVAL = 5`
  - `get_brush_skip_map` 支持 `min_interval`，可自定义缓存时长
  - 抽出 `__match_brush_skip`；`is_brush_skip_path` **未命中**时用短间隔兜底重查一次

**命中仍走 60 秒缓存（零额外开销）**，只有「疑似要整理」的文件才可能触发重查，且有间隔限流，不会拖垮目录同步。

### 不变的前提
「同目录不误伤」这条没有变：排除范围是**逐种子内容路径**（`content_path` 相对 `save_path` 拼出来的），不是整个保存目录 —— 同目录下的普通下载照常整理，**目录本身也不被排除**。
