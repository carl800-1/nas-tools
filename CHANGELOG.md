# v6.0.1 (2026-09-23)

## 改进：下载器设置界面重做，说明文字统一收进问号气泡

这一版**只动界面，没有改任何后端行为** —— 下载器表单提交的字段名与个数一个没变
（`save_path` / `type` / `category` / `container_path` / `label` / `auto_category`），
老配置读进来、存回去都跟以前一模一样。改的是「同样的东西怎么呈现」。

### 现象（为什么改）

「编辑下载器」是配置下载器的主要入口，但它一直是一长条平铺的表单：

- 连接设置、下载行为、下载目录设置三大块混在一起，没有分区标题，得靠肉眼找；
- 下载目录每行 6 个控件挤在一行，**没有列小标题** —— 只能靠输入框里的占位文字
  猜哪一格是「分类标签」、哪一格是「自动分类」；
- 字段说明全靠 `title="..."`：鼠标必须**停在输入框上**才出提示，而且浏览器原生
  tooltip 显示窄、不折行，一整段说明被压成一条细线，实际读不了；
- v6.0.0 加的「种子管理模式 × 自动分类」冲突说明是**常驻的红底提示条**，
  一直占着版面，红色还容易让人误以为「配置错了」。

### 改法

**1. 表单分区**：按语义分成四块并各带标题 —— **基本信息 / 连接设置 / 下载行为 /
下载目录设置**。

**2. 下载目录行卡片化**：每行由「控件平铺」改为**卡片** —— 卡片内先是一行列小标题
（类型 / 二级分类 / 分类标签 / 自动分类 / 下载保存目录或 ID / NAStool 访问目录），
下面才是控件。配了「自动分类」的行**整体高亮**，一眼看出哪几行真的参与自动归类；
折叠标题右侧加**规则条数徽标**，直接显示当前配了几条规则。

**3. 说明统一成 `?` 气泡**：全页字段说明统一为与其它设置页一致的 `?` 圆角图标 +
Bootstrap tooltip，`data-bs-html="true"` 支持多行、最大宽度放宽到 26rem 并左对齐，
长说明不再被压扁。「自动分类」原来那段按取值分行的解释完整保留。

**4. 常驻红底提示条 → 同款小角标**：冲突说明改成标题行上的小角标 ——
**蓝底 `i` = 一般说明**（手动模式、互不冲突时）、**琥珀 `!` = 需要注意**（自动模式，
或下载器不是 qBittorrent 时）。悬停 / 聚焦 / 点击都能展开，内容一点没少
（含 `auto_category_skip_auto_tmm` 的风险与建议）。角标**不再用红色**。

### 两个必须显式处理的细节

**动态插入的控件不会自动获得气泡。** 下载目录行是 `add_downloaddir()` **运行期
append** 的，而页面装载时调用的 `fresh_tooltip()` 只对当时的 DOM 做一次
`querySelectorAll` —— 新插入的 `?` 不会被初始化。因此补了
`init_dyn_tooltip(scope)` / `dispose_dyn_tooltip(scope)`：插入后初始化、删除前销毁
（tooltip 节点挂在被删元素的父级旁边，不销毁会留下游离节点）。另外本项目用的是
**Bootstrap 5 原生 API、没有 jQuery 的 `.tooltip()` 桥接**，只能写
`bootstrap.Tooltip.getInstance(el)` + `dispose()`。

**角标在 `<summary>` 里，点它会顺手折叠整段。** 已用 `preventDefault +
stopPropagation` 拦住，做到「出气泡但不折叠」。

### 验证

- `downloader_ui_verify.py`：**43 通过 / 0 失败**，24 条反向注入用例逐条精确命中
  （拿掉 `?`、换回 `title=`、恢复红底、去掉 click 触发、去掉 `html:true`、去掉
  `<summary>` 点击拦截、删掉动态 tooltip 初始化…）；
- `downloader_ui_preview.py`：真实模板渲染 **9 张截图、0 console error**，并对真实
  DOM 断言：角标状态（自动→`!` / 手动→`i` / 非 qB→`!` / 未启用自动分类→隐藏）、
  规则条数徽标数字等于实际行数、`?` 气泡确已实例化、删行后无残留节点；
- 后端零改动，相邻套件回归 `auto_category_verify.py` 73/0、
  `auto_category_empty_path_verify.py` 14/0。

# v6.0.0 (2026-09-23)

## 新功能：按种子自动判断媒体类型，自动归类到下载器分类

这是一个大版本 —— 新增一套**纯本地、不联网的媒体类型判定引擎**，按判定结果把下载器里的
任务自动归到「电影 / 电视剧 / 其它」分类。它与以往所有功能最大的不同是：
**能覆盖 nas-tools 自己没下载过的任务。**

### 现象（为什么要做这个）

现有 `MediaType` 体系只覆盖「走 nas-tools 下载链路」的任务：下载时按类型匹配「下载目录设置」
的行，把该行的 `label` 当分类写进去。可下载器里还有大量任务从来不走这条路 —— 手工添加的、
刷流下载的、以前就在的、别的工具塞进去的。这些任务在下载器里没有分类，nas-tools 对它们
也一无所知。

更根本的空白是：**「其它」这一类在现有体系里根本不存在**。识别媒体类型的唯一入口是 TMDB
（`__get_tmdb_type`），既要求 API Key 又要联网，且 TMDB 本身就只认电影/电视剧，遇到软件、
音乐、游戏、电子书、教程这类资源给不出结论（它返回的是「未知」，不等于「其它」）。

### 改法

**1. 新增本地判定引擎 `app/utils/media_classifier.py`**

`MediaClassifier.classify(title, files=None, category=None, extra_other_keywords=None)`
→ `result.category / result.reason`。判定按可靠性**串行降级**，先命中先返回：

| 顺序 | 信号 | 说明 |
|---|---|---|
| 1 | 站点分类 / 下载器分类字段 | 最可靠，直接采信 |
| 2 | 非影视关键词黑名单 | 软件 / 游戏 / 音乐 / 电子书 / 教程 / 驱动 / 字幕… → 其它 |
| 3 | 剧集特征 | `SxxExx` / 第x季 / 第x集 / 全xx集 / 完结 / 综艺 → 电视剧 |
| 4 | 电影特征 | 有年份 → 电影 |
| 5 | 文件清单（可选） | 标题无特征时，看任务内有没有多个带 `SxxExx` 的视频文件 |

结论落成三类：`MOVIE / TV / OTHER`。**动漫并入「电视剧」**，不单列一类。

**2. 配置入口放在「下载目录设置」，规则即开关**

下载器设置 → 编辑下载器 → 下载目录设置，每行末尾新增一列下拉 **「自动分类」**：

| 取值 | 行为 |
|---|---|
| `不启用`（默认） | 这一行不参与自动分类 |
| `自动` | 只接收判定结果与该行「类型」一致的任务 |
| `电影 / 电视剧 / 其它` | 固定接收该类任务（自定义） |

分类名取该行的「分类标签」；留空则回落到判定结果本身。规则按界面从上到下**先命中先用**，
所以可以把「电影」行放前面做精细化。所有行都选「不启用」就等于关闭功能，**不再需要额外总开关**。

**3. qBittorrent 分类读写接口**

`Qbittorrent` 新增 `get_categories()` / `create_category(name)` / `set_torrents_category(ids, category)`，
分别走 `qbittorrentapi` 的 `torrent_categories.categories` / `torrents_create_category` / `torrents_set_category`。

### 三道安全防线

1. **建分类绝不带保存路径**。分类一旦绑了路径，qB 就会把任务文件搬过去 —— 有源码级断言锁着。
2. **默认跳过开了「自动种子管理(auto_tmm)」的任务**。qB 改动这类任务的分类时会连文件一起搬到
   该分类的保存路径下，可能搬走正在做种的数据。日志里会汇总提示跳过了几个；确认分类都没绑
   保存路径、或本就希望交给 qB 按分类归位时，可把 `auto_category_skip_auto_tmm` 改为 `false`。
3. **挡空 ids**。qB 的 `setCategory` 对空 hashes 的行为没有保证，万一被解释成「全部任务」，
   会一次刷掉整个下载器的分类。

### 需要留意

- **分类名建议与 qB 里现有分类对齐。** 删种策略的「分类过滤」与下载设置的「分类隔离」
  都读 `category` 字段，写入新分类会改变这些规则的匹配结果。
- **Transmission 没有「分类」概念**，只有 labels，本功能仅对 qBittorrent 生效。
- Web 保存回来的值是**字符串**，`bool("false")` 是 `True`，因此布尔开关统一走 `_cfg_bool()` 解析，
  避免开关关不掉。

### 涉及文件

- `app/utils/media_classifier.py`（新增，275 行）
- `app/downloader/downloader.py`：新增 `get_auto_category_rules()` / `auto_category_torrents()` / `_cfg_bool()`
- `app/downloader/client/qbittorrent.py`：分类读写接口
- `web/templates/setting/downloader.html`：目录设置新增「自动分类」列
- `config/config.yaml`：`pt` 段新增 `auto_category_*` 兜底项（`scope` / `create` / `skip_auto_tmm` / `use_files` / `other_keywords`），默认值通常不用改

### 验证

- `auto_category_verify.py` **73/0**（含 5 组反向验证：规则覆盖 / 动漫映射 / 布尔解析 / auto_tmm 守卫 / 建分类不带 save_path）
- `qb_category_api_verify.py` **23/0**（对**真实 `qbittorrentapi`** 核实三个方法名确实存在，再用假客户端驱动真实 `Qbittorrent` 类断言发出的参数）
- `media_classifier_verify.py` **58/0**、`media_classifier_reverse_verify.py` 6 组破坏全部命中
- 回归：`download_fallback_verify` 64/0、`tags_utils_verify` 29/0、`config_save_comment_verify` 15/0、装饰器结构 6/6、路由装饰器真实 eval 6/6、全量 `compileall` 通过

## 版本号

`v5.2.5` → `v6.0.0`

---

# v5.2.5 (2026-09-22)

## 修复：择优下载第 1 名失败就整部片子判死（48 条候选，只用掉 1 条）

这一版有两件事：**下载失败后自动回退到下一个同名候选**（核心修复），
以及**刷流日志全中文化**（把 `【Brush】` 之类英文前缀换掉）。

### 现象

搜到 48 条有效资源、择优也正常选中了第 1 名，但添加下载失败后直接输出
「未下载到资源」——第 2 名到第 48 名一次都没被尝试。日志长这样：

```
择优下载：按「站点优先」排序，候选 48 条
第 1 名 馒头 | 20684820380 | ... ← 选中
择优下载选择：馒头 | Mayday 2026 2160p ...
无法打开链接：https://fr1.halomt.com?...
馒头 求救信号 (2026) 添加下载任务失败：无法打开链接：...
求救信号 未下载到资源
```

### 根因

`Torrent.get_download_list()` 做「按名称控重」时，把同名的其余候选**直接丢掉**，
`download_list` 里只剩每个名称的第 1 名：

```python
if media_name not in can_download_list:   # ← 控重
    can_download_list_item.append(t_item)
```

而电影路径是不接返回值的裸调用，失败后没有任何下一步：

```python
for item in download_list:
    if item.type == MediaType.MOVIE:
        __download(item)      # ← 失败就结束了
```

所以「第 1 名的下载域名被网络阻断」= 整部片子判死。
**不是重试逻辑写错，是根本没有重试逻辑。**

### 改法

**1. 同名候选不再丢弃，按择优顺序挂成回退备选**

`get_download_list()` 保留原来的控重结果（谁被选中不变），同时把同名的其他候选
按同一套排序键挂在选中项的 `_fallback_list` 上。日志会直接告诉你还有多少条可退：

```
第 1 名 馒头 | 20684820380 | ... ← 选中（另有 47 个同名候选可回退）
```

**2. 中间候选静默失败，只在「全部失败」时通知一次**

不能简单逐条重试 —— 48 个候选在站点不通时会产生 48 条飞书消息 + 48 次 webhook。
所以 `download()` 新增 `notify_fail` 参数（默认 `True`，原有行为不变）：

- 回退过程中的候选：只打一条 warn 日志，**不发事件、不发消息**
- 所有候选都失败：统一发 1 条失败消息 + 1 次 `DownloadFail` 事件，文案里带上尝试次数

```
【Downloader】求救信号 (2026) 第 1/11 个候选添加失败：馒头 | Mayday 2026 ... —— 无法打开链接，继续尝试下一个候选
【Downloader】求救信号 (2026) 前 1 个候选均失败，已回退到第 2 个候选添加成功：学校 | ...
```

失败汇总（仅当全部失败）：`添加下载任务失败：已尝试 21 个同名候选全部失败，最后一个失败原因：…`

**3. 新增配置项 `laboratory.search_retry_max`（默认 20）**

```yaml
laboratory:
  search_retry_max: 20   # 0=关闭回退 / 20=默认 / -1=试完所有同名候选
```

为什么不默认不限：站点不通时每个候选都要等一次超时，同名候选可能有几十条，
全部试完会把单次搜索拖到数分钟。配置缺失或写入非法值一律回落默认 20，
不会因为配置写错而悄悄关掉回退。

**4. 顺带修正 TV 路径的判重口径**

回退成功时实际下载的是**同名候选**，而 TV 路径原先按原始 item 判断「这一季集是否已下过」
（`if item in return_items`），会导致回退成功后原始项在后续季集轮次里被再下一次。
已改为按媒体名判重（`Torrent._media_name(item) in downloaded_names`）。

### 顺带：刷流日志中文化

刷流日志正文里的英文前缀 `【Brush】` / `【BRUSH】` 共 57 处，统一改为 `【刷流】`，
并把日志里的英文字段名一并中文化：

| 原 | 现 | 处数 |
|---|---|---|
| `【Brush】` / `【BRUSH】` | `【刷流】` | 57 |
| `【刷流】刷流任务 xxx` | `【刷流】任务 xxx`（去掉重复读法） | 7 |
| `seed_size not configuration` | `【刷流】任务 %s 未配置保种体积，不限制新增下载` | 1 |
| `peer_count:` / `threshold:` | `做种人数:` / `阈值:` | 2 |
| `left:` / `right:` | `下限:` / `上限:` | 1 |
| `pubdate:` / `year:` | `发布时间:` / `年份:` | 6 |
| `id: %s, title: %s` | `id: %s，标题: %s` | 1 |
| `时间间隔：%f hour` | `时间间隔：%f 小时` | 1 |
| `从下载器获取种子为 None` | `从下载器获取种子为空` | 1 |

PT 专业词按原样保留：`H&R`、`FREE`、`2XFREE`、`RSS`、`Cookie`、`GB`、`Kb/s`。

### 涉及文件

| 文件 | 改动 |
|---|---|
| `app/utils/torrent.py` | 抽出 `_media_name()`；控重时把同名候选挂到 `_fallback_list`；候选清单日志标注可回退数 |
| `app/downloader/downloader.py` | `download(notify_fail=...)` + 抽出失败通知；`batch_download` 回退循环；`downloaded_names` 判重；新增 `get_download_retry_max()` |
| `config/config.yaml` | `laboratory.search_retry_max: 20`（含注释） |
| `app/brushtask.py` | 刷流日志中文化（58 行） |

### 验证

新增 `.workbuddy/tests/download_fallback_verify.py`：**64 断言全通过**，
覆盖 `_media_name`、`_fallback_list` 分组顺序、日志文案、配置解析（含非法值回落）、
静默失败与默认通知不变、`batch_download` 七种回退场景（首个成功 / 第 2 个成功 / 全失败 /
`retry_max=0` / `=2` / `=20` / `=-1`）、TV 判重源码断言。

反向验证：把修复前的版本导出到另一目录跑同一套检查 → 20+ 项 FAIL；
再把默认值改回 10 跑 → 精确 FAIL 3 项。证明这套检查确实有鉴别力。

回归：`py_compile` 全量通过、`decorator_structure_check` 6/6、
`web_main_import_verify` 6/6、`brushtask_state_verify` 26/0、
`brushtask_scheduler_verify` 18/0、`mteam_dlv2_fix_verify` 34/0、
`tags_utils_verify` 29/0、`tag_behavior_verify` 16/0、
`tag_system_source_verify` 71/0、`transfer_ledger_verify` 25/0、
`config_save_comment_verify` 15/0。

## 版本号

`v5.2.4` → `v5.2.5`

---

# v5.2.4 (2026-09-22)

## 修复：实时日志不自动滚到最新（零容差的判据，一旦不成立就永久失效）

「实时日志」弹窗里新日志一直在追加，但画面停在原地不动，得手动往上滚才看得见。
手动能滚说明容器本身没问题，问题出在**「该不该自动跟随」的判据**上。

### 根因

旧写法在每次追加前算一次：

```js
bool_ToScrolTop = scrollTop + offsetHeight >= scrollHeight
```

而 `#logging_table` 上 `offsetHeight == clientHeight`（实测，余量为 0），
于是它要求 `scrollTop` **精确等于**最大值。这里有两个问题：

1. **零容差**：浏览器会把 `scrollTop` 吸附到物理像素、布局里也可能有亚像素，
   滚到底时常常差零点几像素到不了理论最大值 ⇒ 判据为假。实测只差 1px 就锁死。
2. **闩锁**：判据只在「追加前」算一次，一旦为假就再也不会去滚 —— 之后每批新
   日志都只追加、不跟随，且**永远不会自己恢复**，只能靠用户手动滚到底（而且要
   滚得足够精确）才会重新变真。

### 改法

把「算位置」改成「记意图」：

- 默认**总是跟随最新**；只有用户主动往上翻才暂停（判据留 8px 容差）
- 打开弹窗、切换日志来源时复位为跟随
- 追加后立刻滚一次，500ms 后再补一次（长日志换行会让 `scrollHeight` 再变化）
- 暂停时按钮栏出现「回到底部」，点一下或自己滚回底部即恢复跟随
- 兜底：若 `#logging_table` 不可滚，依次尝试外层容器

### 涉及文件

| 文件 | 改动 |
|---|---|
| `web/static/js/functions.js` | 跟随逻辑重写（意图开关 + 容差 + 双次滚动 + 捕获阶段监听 scroll） |
| `web/templates/navigation.html` | 日志弹窗按钮栏新增「回到底部」 |

# v5.2.3 (2026-09-22)

## 调整：刷流「发布年份」的下拉文案改为「大于 / 小于」，边界改为严格比较

「发布年份」这个条件原本写作「不早于 / 不晚于」，判断是**含端点**的
（`gt#2000` = 年份 ≥ 2000）。字面换成「大于 / 小于」之后，如果只改文案、
不改判断，「大于 2000」就会放行 2000 年的片子 —— 名字和行为对不上。
这一版把两边一起改。

| 选项 | 存储值 | v5.2.2 的判断 | v5.2.3 的判断 |
|---|---|---|---|
| 大于 | `gt#2000` | 年份 ≥ 2000（含 2000） | 年份 **>** 2000（不含，2001 年起） |
| 小于 | `lt#2020` | 年份 ≤ 2020（含 2020） | 年份 **<** 2020（不含，至 2019 年） |
| 介于 | `bw#2000,2020` | 含两端 | **不变**，仍含两端 |
| 不限制 | `#` | 不看年份 | **不变** |

> **已有任务立刻按新口径生效。** 原本卡在边界年上的设置要留意：原先的
> 「不早于 2000」从升级起不再放行 2000 年的种子。想连边界年一起放行，
> 把值减 1 即可（`gt#1999` 等价于原来的 ≥ 2000）。

术语还出现在另外三处，本次一并同步，避免界面上说法互相矛盾：
任务卡片「选种规则」区里的徽章、开放 API 的字段说明、运行日志里的判断记录。

「解析不到年份不拦截」的行为没有变化 —— 标题里没有年份的种子照旧放行。

### 涉及文件

| 文件 | 改动 |
|---|---|
| `web/templates/site/brushtask.html` | 下拉两项文案 + 悬停说明 |
| `app/brushtask.py` | 判定改严格比较（`gt` 用 `<=` 拦截、`lt` 用 `>=` 拦截）+ 2 处日志措辞 |
| `web/action.py` | 任务卡片徽章字典 |
| `web/apiv1.py` | 开放 API 的 `brushtask_year` 字段说明 |
| `README.md` | 2.15 节保留 v5.2.2 原貌，新增 2.16 节说明本次口径变化 |

# v5.2.2 (2026-09-22)

## 修复：刷流的「转移到媒体库」开关以前是死的；另加发布年份过滤、默认转移方式统一为复制

刷流任务表单里有个「转移到媒体库」开关，关掉它的本意是**只做种、不整理**。
但从 v5.1.3 去掉「已整理」标签、v5.2.0 改用转移账本之后，这个开关就**没有任何
代码读它了** —— 关掉它，下载完的文件照样会被刮削入库，与界面写的意思正好相反。
这一版把它补活，顺手给选种规则补了一个年份控制。

### 1. 开关为什么失效

开关的值一直好好地存在数据库 `SITE_BRUSH_TASK.TRANSFER` 字段里，**问题在读的那一头**：

| 版本 | 判断「这个种子要不要整理」的依据 |
|---|---|
| v5.1.3 之前 | 下载器标签里有没有「已整理」 |
| v5.1.3 ~ v5.2.1 | 无 —— 标签机制被移除，`TRANSFER` 再没人消费 |
| v5.2.2 | `TRANSFER` 开关（+ 转移账本去重） |

### 2. 两条自动入库路径都认这个开关

入库有三条路径，其中两条是自动的，本次都拦住了：

| 入库路径 | 触发方式 | 本次的拦截依据 |
|---|---|---|
| 下载器监控 | 每 300 秒扫一次下载器，本按 hash 判断 | 种子的 **hash 清单**（60 秒缓存） |
| 目录同步 | watchdog 实时 + 60 秒兜底扫描 | 文件**路径前缀**精确匹配 |
| 手动识别 | 你自己点 | 不拦（手动即意图） |

- 目录同步只认「文件落在监控目录」，**与种子来源无关**，所以这里只能按路径判断；
  匹配按分隔符对齐，`/brush/MovieB` 的规则不会误伤 `/brush/MovieBB`；
  Windows 反斜杠与 POSIX 正斜杠都成立。
- 下载器侧的清单带 60 秒缓存，避免监控循环每轮都查库；**查库异常时降级为空清单**，
  宁可不跳过，也不因异常漏整理。
- 只有**明确关闭**转移的任务才跳过；开关为「开」、或历史数据里没有这个键的，
  一律照常入库。

界面上的悬停说明也改成了「关＝只做种，不整理」，并写明两条路径都会跳过。

### 3. 转移方式默认统一为「复制」

三处入口各写各的默认值，口径不一：

| 位置 | 原默认值 |
|---|---|
| 目录同步 → 新增同步目录 | 复制 |
| 下载设置 → 新增下载器 | **硬链接**（写死在页面里） |
| 手动识别弹窗 | 读 `media.default_rmt_mode`（出厂复制） |

现在三处**同源于 `media.default_rmt_mode`**（出厂 `copy`），`filetransfer.py`
里原来的兜底也从影子配置 `pt.rmt_mode` 换到同一个键。配置缺失、为空、或填了
非法值（例如拼错的 `hardlink`）一律回落**复制**；已保存的配置不受影响 ——
想用硬链接或移动，把基础设置里的「默认文件转移方式」改一下即可。

另提醒一句：`SystemUtils.link` 是裸 `os.link`，**跨卷（EXDEV）直接失败、没有
copy 兜底**，同机跨盘转移时「复制」比「硬链接」稳。

### 4. 选种规则新增「发布年份」

刷流最容易白下的是老片，这版在「选种规则」里加了发布年份条件：

| 选项 | 存储值 | 含义 |
|---|---|---|
| 不限制 | `#` | 不看年份 |
| 不早于 | `gt#2000` | 只下 2000 年（含）以后的 |
| 不晚于 | `lt#2020` | 只下 2020 年（含）以前的 |
| 介于 | `bw#2000,2020` | 只下 2000–2020（含两端） |

- 年份从种子标题解析，用 **guessit**（项目既有依赖）而不是纯正则：
  《2012》《Blade Runner 2049》这类标题里的数字不会被误当成年份；
  正则兜底另加了 `1920x1080` 分辨率防护。
- **解析不到年份不拦截**（fail-open）：宁可多下一个，也不因规则误伤正常的种子。
- 区间上下界写反自动对调；只填一个边界即单边限制；填 0 或不填视为不限制。

作用时机：**保存任务时**写入 `rss_rule` 的 `year` 键，**每次轮询新种时**在
`__check_rss_rule` 里判定，不匹配的直接跳过、不入队下载。开放 API 同步提供
`brushtask_year` 参数。

### 5. 移除「当前站点下载任务数」

这个条件只有任务**同时配了标签**时才生效（标签为空时刷流会跳过整段站点总量闸门），
是个「看起来设了、实际经常不设防」的项，而且与「当前站点下载数」重叠。
这版把它整体删掉（界面 / 保存逻辑 / 开放 API 三处），空出的位置由「发布年份」顶上，
选种规则仍是 12 项、每行 3 列不留孤列。老任务里存的 `current_site_dlcount`
键不再读取，**无需迁移**。

### 6. 「标签」卡片排版收口（基础设置）

- 标签库与「整理去重」改为等宽两列，不再一宽一窄；
- 「行数上限」「过期天数」的标题移到输入框**上方**（与其它表单一致，原先在下方）；
- 标签库空态在框内居中、并随卡片撑满，不再缩在左上角；
- 重命名/删除标签时只重建标签块，不再整块清空后回填（消除一次可见的闪烁）；
- 章节标题统一 15px/600、说明文字统一 13px（并清掉 tabler 里并不存在的
  `font-weight-medium` 用法），「测试连接」按钮不再强行拉满宽度。

## 验证

改动全部用**真实源码**离线验证（不是在副本上跑）：

| 脚本 | 断言数 | 覆盖内容 |
|---|---|---|
| `_verify_brush_transfer.py` | 40 | 路径前缀匹配（含前缀相似的兄弟目录、Windows 正反斜杠）、hash 清单缓存与降级、三处同步调用点守卫 |
| `_verify_default_rmt_mode.py` | 40 | 三处下拉渲染后的选中项、`get_default_rmt_mode()` 的多种输入回落 |
| `_verify_brush_year.py` | 72 | 四种取值的判定与徽章、编辑回填与新建清空、区间写反、Jinja 渲染后的列位置 |
| `_verify_drop_site_dlcount.py` | 55 | 字段删除后的列数整除、保存链路 12 个字段后端全读得到、开放 API 不再暴露旧参数、内联 JS 过 `node --check` |

发版前体检：1537 个装饰器表达式零异常、45 个页面片段顶层词法声明为零、
8 个 py 文件 `py_compile` 通过。

## 版本号

`v5.2.1` → `v5.2.2`

---

# v5.2.1 (2026-09-22)

## 修复：刷流任务的「停止下载新种」形同虚设，日志也会误导

有用户反馈：刷流任务明明都停着（界面上状态点全是红的），日志却说
「5 个刷流服务正常启动」，怀疑停止功能没生效。排查后发现是**三件事**，
其中两件是真缺陷，一件是文案误导。

### 1. 三态状态被压成了两态（功能等于不存在）

刷流任务的状态设计是**三态**：

| 值 | 含义 | 界面显示 |
|---|---|---|
| `Y` | 正常（下载新种 + 做种） | 正在运行（绿） |
| `S` | 停止下载新种，只做种保种 | 停止下载新种（橙） |
| `N` | 完全停止 | 已停用（红） |

但 `db_helper.py` 的 `update_brushtask_state` 把它写死了：

```python
"STATE": "Y" if state == "Y" else "N"   # ← S 被静默改写成 N
```

后果：**任何入口传入 `S` 都会被变成 `N`**，包括任务表单里的「状态」下拉框。
也就是说「停止下载新种」这个选项选了等于没选，界面、调度器、删种逻辑里
为三态写的分支全成了死代码。

修复：原样保留 `Y` / `S` / `N`，只有非法值才回落到 `N`。

### 2. 批量按钮会把「只改一个」变成「全改」

`update_brushtask_state` 的批量接口在**未勾选任何任务**时（`ids` 为空）
走的是「更新全表」分支。界面上没有任何提示，很容易误伤 ——
想停一个任务，结果全部停了。

修复：前端在未勾选时弹确认框，明确告知「将把全部 N 个刷流任务设为…」；
后端同时加上 `state` 参数校验，非法值直接拒绝。

### 3. 日志文案误导（本次排查的起因）

```python
running_task = len(self._scheduler.get_jobs())   # 本次注册了几个 job
if running_task > 0:
    log.info(f"{running_task} 个刷流服务正常启动")
```

这个数字是「**本次注册进调度器的任务数**」，不是「共几个服务在跑」。
而 `init_config()` 每次被调用都会打一条，所以：

- 点一次「停止」→ 触发一次 `init_config` → 打一条日志
- 连续调几次，同一秒里就会出现 `5 / 4 / 3 / 2 / 1` 五条

乍看像"服务被反复启动"，实际只是每次重新注册时活跃任务数在减少。
日志本身没错，但读起来完全反了。

修复后的文案：

```
本次已启动 2 个刷流任务（共 5 个，另有 3 个处于停止状态）
没有需要启动的刷流任务（共 5 个，均处于停止状态）
```

### 顺带确认：停止功能本身是好的

一并验证了停止链路，确认无问题：

- 全部 `N` 时**不注册任何 job、不启动调度器**
- `check_task_rss` 有 `if state != 'Y': return`，即使被误触发也不会下载
- 删种任务跳过 `state == 'N'`，N 的任务连删种都不做
- 重新 `init_config` 时旧调度器会被 `shutdown`，旧任务不残留

### 验证

新增两个用**真实源码**跑的验证脚本：

| 脚本 | 断言数 | 覆盖内容 |
|---|---|---|
| `.workbuddy/tests/brushtask_state_verify.py` | 26 | 真实 SQLAlchemy + 临时 SQLite 跑真实 `update_brushtask_state`，覆盖三态保留、非法值回落、前端确认框、后端参数校验 |
| `.workbuddy/tests/brushtask_scheduler_verify.py` | 18 | 直接调用真实 `init_config`，覆盖调度注册、日志文案、旧调度器释放 |

回归全绿：`decorator_structure_check` 6/6、`web_main_import_verify` 6/6、
`mteam_dlv2_fix_verify` 34/0、`tags_utils_verify` 29/0、`tag_behavior_verify` 16/0、
`tag_system_source_verify` 71/0、`transfer_ledger_verify` 25/0。

## 版本号

`v5.2.0` → `v5.2.1`

---

# v5.2.0 (2026-09-22)

## 新特性：整理去重改用「转移账本」，不再依赖下载器标签

v5.1.3 把程序写死的「已整理」标签去掉了，但留下一个尾巴：程序怎么判断
某个种子已经整理过了？当时的答案是「你自己把『已整理』填进标签」——
这等于把去重的正确性押在你记得填上。v5.2.0 换成程序自己记账。

### 1. 旧机制为什么不可靠（本次排查的结论）

排查发现，**「已整理」标签在 qBittorrent 上其实从未真正生效过**：

| 环节 | 事实 |
|---|---|
| `downloader.py:637/644` | 调 `set_torrents_status(ids=..., tags=task.get("tags"))` |
| `qbittorrent.py` `get_transfer_task` | 返回的字典**只有 `path` 和 `id`，没有 `tags` 键** |
| 结果 | `task.get("tags")` 恒为 `None` → `Tags.split(None)` 为空 → 直接 `return` |

也就是说，`move` / `rclone` / `minio` 模式（种子整理后被删除，本来就不需要去重）
之外，真正需要去重的 `copy` / `link` / `softlink` 模式，恰恰是标签写不进去的那条路。

那为什么没出过问题？因为还有一层兜底：`filetransfer.py:400` 检查
`os.path.exists(new_file)`，文件在就跳过。代价是**每轮都要重扫一遍下载目录**。

结论：判断的输入（下载器标签）根本没人写，靠恢复旧逻辑（只读不写）是空判断，
必须换成程序自己维护的状态。

### 2. 新表 `TRANSFER_LEDGER`

```
ID          主键
DOWNLOADER  下载器 ID（索引）
TORRENT_ID  种子 hash（索引）
PATH        整理后的路径
DATE        登记时间（索引）
```

配套迁移脚本 `scripts/versions/89ea3ada7589_1_3_6.py`
（`down_revision = 'd116d793ba9f'`，建表 + 3 个索引，容忍表已存在）。

为什么不用现成的表：

- `TRANSFER_HISTORY` 被 `/trh` 命令暴露给用户，会 `delete()` 整表 —— 拿它当账本，
  用户清一次记录就全部重复整理；
- `DOWNLOAD_HISTORY` 会被后续的 `update` 改写，状态不稳定。

所以用**独立专表**，且**不接入任何用户可触发的清空入口**。

### 3. 两个防膨胀机制

用户指出「不做回收的话，长期运行会无限增长爆掉」，故补上双保险：

| 机制 | 配置项 | 默认 | 行为 |
|---|---|---|---|
| 行数硬上限 | `pt.ledger_max_rows` | 5000 | 插入前若总行数达上限，先删到 80% 再插，行数封顶 |
| 过期清理 | `pt.ledger_expire_days` | 90 | 按 `DATE` 删除过期记录，每小时最多执行一次 |

另有 `pt.ledger_enable`（默认 `true`）总开关。

行数上限是**硬闸门**，不依赖定时器是否按时跑；过期清理复用 downloader 现有的
5 分钟调度并做 1 小时节流，不额外开线程。

容量不是问题：按每天 20 个种子算，5000 条约覆盖 8 个月，一年数据量约十几 MB
（SQLite 单库上限 281 TB）。阈值取 5000 是保守选择。

### 4. 移除旧 API

- `config/config.yaml`：删除 `pt.tag_organized`
- `app/utils/tags.py`：删除 `DEFAULT_ORGANIZED_TAG`、`get_organized_tag()`、
  `is_organized()`；模块 docstring 说明改由账本承担
- `web/templates/setting/basic.html`：删除「整理标记标签」输入框，
  替换为「整理去重（转移账本）」区块（开关 + 行数上限 + 过期天数）
- `app/brushtask.py`、`iyuuautoseed.py`、`torrenttransfer.py`：清理相关提示文案

### 5. 顺带修掉：标签隔离的子串误判

开启「只处理指定标签」时，原实现是 `if tag not in torrent_tags`（字符串子串匹配），
于是标签 `NASTOOLX` 会被误判为命中了 `NASTOOL`。现在改为
`(torrent_tags or "").split(",")` 后精确比对 —— qBittorrent 与 Transmission 同时修正。

### 6. 新增工具方法

- `app/utils/number_utils.py`：新增 `NumberUtils.get_int()`（从字符串安全取整数，
  用于界面上 `type="number"` 提交上来的字符串）
- `app/downloader/client/_base.py`：新增非抽象方法 `is_transferred(torrent_id)`，
  内部查账本；下载器下线时返回 `False`（按未转移处理，不阻断流程）
- `web/templates/setting/basic.html`：新增 `_coerce_ledger_numbers()`，
  把两个数字字段 `parseInt` 后回写，避免 YAML 里存成字符串

### 7. 验证

| 检查 | 结果 |
|---|---|
| `transfer_ledger_verify.py`（真 SQLAlchemy + 临时 SQLite 跑真实 `DbHelper`） | 25 通过 / 0 失败 |
| `tag_behavior_verify.py`（账本判定 + 标签精确匹配，16 项） | 16 通过 / 0 失败 |
| `tags_utils_verify.py`（含「旧 API 已移除」断言） | 29 通过 / 0 失败 |
| `tag_system_source_verify.py`（71 项源码级断言） | 71 通过 / 0 失败 |
| `config_save_comment_verify.py`（注释保留 + 幂等） | 15 通过 / 0 失败 |
| `decorator_structure_check.py` | 6/6 通过 |
| `web_main_import_verify.py`（64 个路由装饰器真实 eval） | 6/6 通过 |
| 模板语法（basic / brushtask / site） | 全部通过 |
| `py_compile`（12 个改动文件） | 全部通过 |
| `config.yaml` 解析与类型 | `true` / `5000` / `90`（bool / int / int） |

## 版本号

v5.2.0

---

# v5.1.3 (2026-09-21)

## 新特性：标签完全由你自己定义，程序不再自动追加

### 1. 问题：凭空多出来的「已整理」标签

**现象**：qb 里的下载任务总会多出一个「已整理」标签，而这个标签在界面上
找不到任何地方能配置、修改或删除。

**原因**：它在 6 个文件 13 处被写死在代码里。

| 文件 | 写死方式 |
|---|---|
| `brushtask.py:787,789` | 未开启「转移」时强行 `tag += ["已整理"]` |
| `qbittorrent.py:250` | `set_torrents_status` 直接 `tags="已整理"` |
| `transmission.py:169,171,173` | 同样合成标签 |
| `_base.py:79` | 抽象方法注释即承诺此行为 |
| `iyuuautoseed.py:69` | `_torrent_tags = ["已整理", "辅种"]` |
| `torrenttransfer.py:68` | `_torrent_tags = ["已整理", "转移做种"]` |

其中两个插件的标签是**类属性硬编码，界面上根本没有对应输入框**，
用户无论如何都改不了。

**改动**：全部删除。程序不再向你的标签集合里添加任何默认标签。

### 2. 新增 `app/utils/tags.py`，标签处理收口到一处

提供 `split` / `join` / `merge` / `is_organized`，以及 `pt.tags` 标签库读写。
合并顺序固定为「用户填写的在前」，这也是界面视觉区分的依据。

### 3. 「是否已整理」的判定保留，但改为只读

这层判定不是装饰，它决定已完成任务要不要再整理一遍：

```python
if Tags.is_organized(torrent_tags):
    continue    # 整理过就别再整理
```

所以不能一删了事。新增只读配置 `pt.tag_organized`（默认「已整理」）——
**程序只读不写**。你在站点标签或任务标签里填了这个名字，它就跳过重复整理；
留空则关闭这层保护。想换成「已转移」这个名字同样生效。

### 4. 顺手修掉一个真 bug：分隔符两套标准

`downloader.py` 原先按**英文分号**拆分下载设置与站点标签，而 `brushtask.py`
按**逗号**拆分。后果是同一个标签换个入口写法就变，永远匹配不上；
而站点页的提示文案写的还是「多个使用英文分号即;分割」。现四处统一为逗号，
提示文案同步更正。

### 5. 修掉一个会静默损坏配置文件的隐患

标签库保存时必须**就地修改**配置对象。config.yaml 是 ruamel 的注释感知
结构加载的，一旦用 `dict()` 做浅拷贝再写盘，全部说明注释都会丢失 ——
实测 11558 字节缩到 10215 字节，注释全没了。已加回归测试锁死这条不变量。

### 6. 插件标签改为界面可配置

`iyuuautoseed` 与 `torrenttransfer` 新增「辅种任务标签」「转移做种任务标签」
输入框，不再硬编码。

### 7. 界面

- **设置 → 标签**：新增标签卡片，可新增 / 重命名 / 删除标签，
  并配置整理标记标签名。
- **刷流任务列表**：标签以彩色徽标常驻显示在卡片标题旁，
  不再藏在折叠后的表格里。
- 站点标签、刷流任务标签输入框接入标签库下拉补全，
  提示文案统一为「多个使用,分隔」。

### 8. 验证

| 脚本 | 结果 |
|---|---|
| `tags_utils_verify.py` | 36 通过 / 0 失败 |
| `tag_system_source_verify.py` | 60 通过 / 0 失败 |
| `tag_behavior_verify.py` | 8 通过 / 0 失败 |
| `config_save_comment_verify.py` | 15 通过 / 0 失败 |
| `decorator_structure_check.py` | 6/6 通过 |
| `web_main_import_verify.py` | 6/6 通过 |

行为验证（桩件跑真实 `qbittorrent.py`）确认：无标签时零写入、
用户标签原样落盘不追加、整理去重判定在自定义标签名下同样生效。

## 版本号

v5.1.3

# v5.1.2 (2026-09-21)

## 修复与优化：站点体检不再白等，并直接告诉你「是不是缺代理」

### 1. L2 已经是网络层失败时，跳过 L5 / L6 探针搜索

**现象**：一个连不上的站点，体检固定要等约 16 秒才给结论。

**原因**：L2 失败之后 L5 仍会真实跑一次搜索。抓取侧的等待是
`while not spider.is_complete: sleep(0.5)`，上限 31 × 0.5s ≈ **15.5 秒**
（`builtin.py::spider_search` 默认 `timeout=30`），到期只调 `mark_timeout()`，
既不 stop 也不 join。请求在 L2 就没出去，L5 走同一条网络路径必然同样失败
—— 这 15.5 秒纯属白等。（这也是为什么「L5 耗时」总是卡在 15500 毫秒附近，
它不是站点慢，是本地等待上限。）

**改动**：`site_health.py::check()` 依据 L2 的「归类」明细项判断失败发生在哪一层。
只有**请求根本没取到响应**（`res is None`）时才跳过 L5/L6，并在层级说明里写明
原因（如「因 L2 请求未取到响应（reset）而跳过」）；若 L2 拿到了响应（例如
HTTP 500），L5 照常执行 —— 那种情况下探针搜索仍有诊断价值，不该跳过。

### 2. 直连失败而系统里配了代理时，自动用代理复测一次

**现象**：报告只写「连接被重置」，用户既不知道为什么，也不知道该动哪一步。

**背景**：站点没打开「代理」开关时，**即使系统里配了全局代理也是裸连** ——
`site_health.py:616` 与 `_spider.py` 都是 `Config().get_proxies() if site.proxy else None`。

**改动**：新增 `retry_via_proxy()`。当 L2 直连失败、`app.proxies` 里**确实填了**
代理、且该站点**没有**打开「代理」开关时，自动改用代理再请求一次，并把结果写进
L2 明细：

| 明细 | 含义与下一步 |
|---|---|
| `经代理复测：可达（HTTP 200）` | 就是缺代理 —— 到站点管理打开该站点的「代理」开关 |
| `经代理复测：仍失败（reset）` | 代理本身不通，或该代理对本站点不可用 |
| （没有这一项） | 系统里没配代理，或该站点本来就已打开代理开关 |

顺带修掉一个真实陷阱：`config.yaml` 的默认值是 `proxies: {http: , https: }`
—— 一个「键存在、值为空」的字典，`if not proxies` 为 `False`，会把「没配代理」
误判成「配了代理」。新增的 `usable_proxies()` **逐项判空**之后才算数。

### 3. 修正 AGSVPT_New（末日）的域名打错

内置索引器库里该站的 `domain` 写成了 `hhttps://new.agsvpt.com/` —— 多了一个 `h`。
不以 `http` 开头时 `get_url_netloc()` 会把整串当成 netloc，于是**永远匹配不上**，
该站 L0b 必然失败。佐证：它的 `conf` 键 `new.agsvpt.com` 是 96 个键里**唯一**
没有对应条目的孤立键。已改为 `https://new.agsvpt.com/`；全库 111 条规则与全部
conf 其余**零改动**。

### 验证

离线跑 `_verify_v512.py`，用真实的 `check()`（桩掉网络与外部依赖）覆盖 6 个场景
共 36 项断言，全部通过：

| 场景 | 期望 | 结果 |
|---|---|---|
| 直连被重置 + 全局代理可用 | 出现「经代理复测 可达」，L5/L6 跳过 | ✅ |
| 直连被重置 + 全局代理也不通 | 出现「经代理复测 仍失败」 | ✅ |
| 直连被重置 + 根本没配代理 | 不出现复测项，只发 1 次请求 | ✅ |
| L2 拿到响应但状态码 500 | **不**跳过 L5 | ✅ |
| 站点已勾代理 + 代理可用 | 走代理成功，不做复测 | ✅ |
| 站点已勾代理 + 代理坏了 | 不误导用户去「打开代理开关」 | ✅ |

发版前体检：258 个文件编译 0 失败、装饰器粘连 0 处、装饰器真实求值仅 1 处
已知的桩不全（`login_check` 的 `wraps(func)`）→ ✅

# v5.1.1 (2026-09-21)

## 修复：内置索引器补充 PTzone，站点体检 L0b 不再失败

### 现象

「PTzone」站点体检在 **L0b** 失败，提示「找不到该站点的索引器定义」，
搜索时该站被静默跳过；而用浏览器访问完全正常。

面板上同时显示「探针搜索未成功：请求超时或被拒绝」，容易让人误判成网络问题。

### 原因

内置索引器库里**没有这个站**。L0b 由 `app/sites/site_health.py::__get_indexer()` →
`ProUser().get_indexer()` 判断：它把站点地址与内置规则**按域名**逐一比对，
比对不到即判 L0b 失败。内置库 110 条规则中确实没有任何 ptzone 相关项。

两处容易误读的地方顺带说明：

- 「探针搜索未成功」出现在 **L5**，是 L0b 之后的表现，不是独立原因；
- 在仓库源码里 `grep` 站名**一条都搜不到** —— 因为索引器规则并不写在 `.py` 里，
  而是放在 `web/backend/user.sites.bin`（base64 编码的 JSON）。

### 改动

只改 `web/backend/user.sites.bin`（内置索引器库），**只新增、不删改**：

- `indexer` 追加一条 `id=ptzone` / `domain=https://www.ptzone.xyz/`，
  位置紧邻同族的大青虫（两者同为 NexusPHP，分类 ID 相同，便于对照维护）
- `conf` 追加键 `ptzone.xyz`，提供详情页 `FREE` / `2XFREE` / `HR` / `PEER_COUNT` 的 XPath

第二处不可省：`app/sites/siteconf.py::check_torrent_attr()` 在 `conf` 取不到该站时
**直接返回默认值**，也就是免费与 HR 判定恒为假 —— 会让该站的刷流「免费识别」
全部失效（把非免费种子当免费下载）。

### 依据

规则不是靠猜，均取自该站点专属来源：

| 项 | 来源 |
|---|---|
| 站型为 NexusPHP | PTPP `resource/sites/www.ptzone.xyz/config.json` 标 `schema=NexusPHP`；站点实际返回标题 `PTzone :: 登錄 - Powered by NexusPHP` |
| 分类 ID 401/402/403/404/405 | Jackett `Definitions/ptzone.yml` 的 `categorymappings`，并与 easy-upload `PTZone.yaml`、nexus-media `sites/html/ptzone.json` 三个来源一致 |
| `conf` 的 XPath | nexus-media 的 `ptzone.json` |

字段机件沿用本仓库中 **86 个同形 NexusPHP 站**的模板，以保证与本仓
`app/indexer/client/_spider.py::__filter_text` 已实现的过滤器集合严格一致。

### 验证

离线、走真实代码路径（非纸面推演）：

- 写库前先确认 `base64(compact JSON)` 能**逐字节复现**原文件，才允许动内容；
- L0b：用真实 `StringUtils.get_url_domain()` 匹配 `ptzone.xyz` / `www.ptzone.xyz/` /
  带路径的 URL 均命中（`www.` 会被归一化剥掉，两种填法等效）；
- 搜索 URL：加载真实 `_spider.py` 走 `start_requests()`，电影搜索得到 `cat401=1`、
  电视剧搜索得到 `cat402..405=1`，路径 `torrents.php` 正确；
- `conf`：用真实 `lxml` 复刻判定逻辑，跑 6 组页面（2X免费 / 仅免费 / 仅 2X / 普通 /
  50% / 带 hitandrun）全部符合预期，其中「仅 2X」**不会**被误判为下载免费；
- 回归：原有 110 条规则与 96 个原有 `conf` 键**零改动**。

### 使用

站点管理里确认该站地址为 `https://www.ptzone.xyz/`（或 `https://ptzone.xyz/`，两者等效），
并确保该站已在「索引器」页勾选；重新体检应显示 L0b 通过。

# v5.1.0 (2026-09-21)

## 修复：单页片段脚本二次注入失效，站点/服务页整页按钮失灵

### 现象

「站点」页的**连通性测试**按钮点了完全没反应 —— 连按钮文字都不变（不是卡在
「测试中...」）。同页的「添加站点」「编辑」等按钮也一并失效。

关键特征：**F5 整页刷新后首次进入正常，切到别的页面再切回来就坏**，
所以看起来时好时坏。

### 原因

这个 Web 是单页结构：`navigation.html` 是框架，页面片段通过
`web/static/js/functions.js:70` 的 `page_content.html(data)` 注入 `#page_content`。
jQuery 的 `.html()` **会执行**片段里的 `<script>` ——
也就是说 **每点一次导航菜单，片段脚本就重新执行一次**。

而片段里存在**顶层 `let` / `const` 声明**：

```js
let SITEHEALTH_SITEID = null;      // web/templates/site/site.html
let backup_item_defs = [];         // web/templates/service.html
const rankingCache = new Map();    // web/templates/site/statistics.html
```

顶层 `let`/`const` 在全局作用域**只能声明一次**。第二次注入时抛出：

```
Uncaught SyntaxError: Identifier 'SITEHEALTH_SITEID' has already been declared
```

这个错误的时机是关键：它发生在**全局声明实例化阶段**，**先于任何语句执行** ——
所以整段脚本**一句都不执行**，包括末尾的
`$("#sitetest_btn").unbind("click").click(...)` 按钮绑定。

而 `function show_sitetest_modal()` 早在第一次注入时就已经提升为全局函数，
**弹窗照样能打开** —— 于是形成「弹窗能开、里面所有按钮全死」这个特征签名。

### 三处来源

| 文件 | 顶层声明 | 引入提交 | 归属 |
|---|---|---|---|
| `web/templates/site/site.html` | `let SITEHEALTH_SITEID` | `233272e` | v5.0.0 站点体检 |
| `web/templates/service.html` | `let backup_item_defs` | `1944403` | v4.1.0 备份条目化 |
| `web/templates/site/statistics.html` | `const rankingCache` | `ace0398` | 上游 |

### 改动

三处顶层 `let`/`const` 一律改为 `var`（`var` 重复声明合法），各加一行
防回退注释说明原因。共 3 个文件 +8/-3。

### 验证

1. **真实脚本块 A/B 对照**：从仓库直接取出 `site.html` / `service.html` /
   `statistics.html` 的脚本块，走与线上完全相同的 jQuery 注入路径，各注入两次。

   | 文件 | 修复前（第 2 次注入） | 修复后（第 2 次注入） |
   |---|---|---|
   | `site.html` | 执行 0 次、未跑到末尾、`already been declared` | 执行 1 次、跑到末尾、0 错误 |
   | `service.html` | 同上 | 执行 1 次、跑到末尾、0 错误 |
   | `statistics.html` | `already been declared` | 不再出现该错误 |

2. 三个脚本块 `node --check` 全部通过。
3. 全仓库片段扫描（`web/templates/**/*.html`）→ 顶层 `let`/`const` 违规 **0 处**。

### 附：两个次要缺陷（本版未修）

- 站点测试弹窗里**一个站都没勾选**时，按钮会永久卡在「测试中...」（无参数校验）。
- `ajax_post` 的错误分支只处理 `status === 200`，其余（500 / 登录态失效重定向）
  一律静默，同样表现成「点了没反应」。

## 版本号

- 从 v5.0.8 升级至 v5.1.0

# v5.0.8 (2026-09-20)

## 修复：在线复核不筛年份，导致「ID 不符」刷屏

### 现象

站点明明返回了几十条结果，最终却全部未通过，归因长这样：

```
学校 55 条数据全部未通过（TMDB无条目2/ID不符24/季集年不符29）
```

其中 **ID 不符 24 条**占比最大。这类失败以前在日志里只留一句
「名称匹配，但 tmdbid 为 xxx，匹配失败」，用户完全看不出该改什么。

### 原因

`app/indexer/client/_base.py` 的在线复核分支有个前置条件 ——
**只有种子名解析不出年份时才会走到这里**（解析得出年份的话，前面
`year_match` 那道闸就已经把年份对不上的资源拦掉了）。

而它去 TMDB 查候选时**没带年份**：

```python
cached_tmdb_infos = self.media.get_tmdb_infos(title=en_title,
                                              mtype=match_media.type,
                                              page=1)
```

`get_tmdb_infos` 本来就支持 `year` 参数（会透传给 TMDB 的年份查询），
只是这里没传。于是 TMDB 按名称返回一堆同名条目 —— 重启版、同一译名下的
另一部片、不同年份的翻拍都在里面。它们的名字与目标媒体**相似度照样能过
0.95**，一路走到最后那道 `tmdb_id` 比对，然后集体被判「不符」。

一句话：**该在入口挡掉的东西，被拖到最后才挡，于是全部记成了 ID 不符。**

### 改动

1. 两处在线复核分支（`filter_search_results_local_for_tv` 与
   `filter_search_results_local_for_tv_rss` 同名逻辑）查询时带上年份：

   ```python
   cached_tmdb_infos = self.media.get_tmdb_infos(title=en_title,
                                                 year=match_media.year,
                                                 mtype=match_media.type,
                                                 page=1)
   ```

2. 新增 `__tmdb_info_year_match()`，在候选循环里再卡一道年份预筛。
   TMDB 的 year 查询**并非严格过滤** —— 没有该年条目时会放宽返回，
   所以不能只靠查询参数，循环里还要复核一遍：

   ```python
   if match_media.year and not self.__tmdb_info_year_match(info, match_media.year):
       continue
   ```

3. **取不到年份的候选放行**（`release_date` / `first_air_date` 都为空时返回
   `True`）。宁可交给后面的 id 比对，也不要因为 TMDB 缺字段而误杀 ——
   收紧的前提是不能引入新的假阴性。

电影看 `release_date`、剧集看 `first_air_date`，两者都用 `_norm_year()`
归一化后比较，与 v5.0.1 确立的年份口径保持一致。

### 验证

`candidate_year_prefilter_verify.py` **14 项 0 失败**（抠真实源码执行，
不依赖 Flask / TMDB）：

- 同名不同年剔除：重启版 2019、另一部 2011、剧集重启版 2020 **全部剔除**；
- 同年放行：`"1999-04-16"` / `first_air_date` / 目标年份为 int 1999 **全部放行**；
- 缺字段放行：无年份字段、空串、`None` 三种形态**全部放行**（不误杀）；
- 电影 / 剧集字段各自生效；
- 收敛模拟：5 条同名候选（仅 1 条同 id），改动前 4 条会被记「ID 不符」，
  改动后降到 1 条，且**目标本体仍在候选内**。

### 未改动与已知边界

- `app/filter.py::is_torrent_match_sey` 的年份分支**保持原样**。它在
  `_base.py` 的三处调用都发生在 `merge_media_info` 之后，而 merge 会把
  卡片的 `release_date[0:4]` 覆盖到种子侧 `media_info.year`，两侧同源，
  该分支实际是自比自 —— 既不会误杀，也拦不住东西。真正干活的闸门是
  merge 之前执行的 `year_match` / `season_match`。改它没有收益，风险却不小
  （订阅链路共用），故不动。
- 「季集年不符」这一类仍混着季号不符、集号不符、季集列表为空三种情况，
  暂未细分归因。
- `ID 不符` 的另一处来源（RSS 路径重新识别后比对 `tmdb_id`）逻辑不同，
  本次未涉及。

## 版本号

- 从 v5.0.7 升级至 v5.0.8

# v5.0.7 (2026-09-20)

## 优化：下载器配置脱敏、择优下载可解释、超时语义澄清

三件事都指向同一个毛病 —— **日志没把该说的话说清楚**。判定逻辑一行没动。

### 一、下载器配置进日志前先脱敏（安全）

现场日志原文：

```
下载器 before, down_dir: None, media: <app.media.meta.metavideo.MetaVideo object at 0x...>,
down_conf: {'id': 1, 'name': 'QB', ..., 'config': {'host': '192.168.3.26', 'port': '8085',
           'username': 'Frank', 'password': 'Frank1980', ...}}
```

qBittorrent / Transmission 的登录密码在配置里就是**明文**字段，整条 dict 打进日志后，
日志文件、容器 stdout、以及复制出来贴给别人的日志片段都会一直带着它 ——
等于把下载器账号写进了长期留存的文件里。

新增模块级 `_mask_secrets()`：按小写子串匹配 `password / passwd / pwd / secret / token /
passkey / api_key / apikey / auth / cookie / private_key`，**只脱敏值、保留键名**，
递归处理嵌套 dict / list，返回副本不改原对象。`host / port / username / type` 一律保留
（排查连接问题要用），所以现在长这样：

```
down_conf: {'config': {'host': '192.168.3.26', 'port': '8085', 'username': 'Frank',
           'password': '***', ...}}
```

> 顺带说明：`username` 没脱敏。它不是凭据，而「到底用的是哪个账号」是排障必需信息。

### 二、择优下载把「凭什么选它」写进日志

现场问题：日志里只有一句

```
实际下载了 1 个资源
```

看不到候选清单，也看不到**规则优先级 / 站点优先级 / 做种数**这些真正决定结果的数值 ——
它们全在配置里，日志里一个都没有，于是「为什么是学校那条而不是 HDTime 那条」只能翻配置反推。

`app/utils/torrent.py` 把排序键拆成 `_sort_key_parts()`（既用于排序也用于展示，**单一来源**），
排序后打印候选与决胜键：

```
【Downloader】择优下载：按「站点优先」排序，候选 2 条；排序键 片名 > 规则优先级 > 站点优先级 > 做种数 > 季集完整度（均为数值越大越优先）
【Downloader】  第 1 名 学校 | 5.92G | 片名=逃出绝命街 规则优先级=100 站点优先级=100 做种数=96 季集完整度=0季0集 ← 选中
【Downloader】  第 2 名 HDTime | 4.31G | 片名=逃出绝命街 规则优先级=100 站点优先级=90 做种数=12 季集完整度=0季0集
【Downloader】择优下载选择：学校 | The.End.of.Oak.Street...BYNDR —— 决胜键：第 3 位「站点优先级」（100 > 90）
```

- 候选多于 10 条时只列前 10 条并说明总数；
- 各排序键完全相同时**如实说明**「取排序中先出现的那条」，不编造决胜键；
- 该函数是纯日志：不读也不写任何状态，**选择结果与改动前逐一致**
  （`_verify_v507_explain.py` 用 `git show 565ed65:` 取旧实现逐条对照，两种
  `download_order` 的排序结果完全一致）。

### 三、站点「等待超时」的文案说清它只是本地等待上限

`spider_search()` 的等超时是**本地轮询上限**（`30 × 0.5 = 15` 秒），不是站点拒绝；
后台爬虫线程还在跑，只是本轮结果被 `torrents_info_array.clear()` 丢弃了。
原文案只说「请求可能未返回」，现场很容易读成「这个站坏了」。现在补上：

```
【Spider】学校 等待超时（约 15 秒），请求可能未返回：请求超时或被拒绝，页面未取回（检查站点域名与代理设置）
（这是「本地不再等待」的上限，不代表站点不可用：后台请求可能仍在跑，本轮已取到的结果一律作废；
换一个候选名会对同站点重新发起请求，若那一轮成功，说明只是这一次慢，不是站点故障）
```

> 改 `builtin.py` 必须同步 `app/utils/types.py::BuiltinIndexerFileMd5`，
> 否则启动时会误报「内置索引文件被改动」。本次已同步为 `67cf2de95790a197e2d84fcb613e73ca`
> （校验口径：字节读取后把 `\r\n` / `\r` 归一化为 `\n` 再算 MD5，
> 见 `StringUtils.md5_hash_file_lf`）。`_verify_v507_explain.py` 里加了一条断言
> 把「文件真实 MD5 == 常量」钉死，以后忘了同步会当场报红。

### 四、下载目录解析为空时明确告警

`down_dir: None` 原先只在 info 行里露个 `None`，容易被当成噪声。现在会补一条 warn，
说明该下载器没有可用目录、本次交给下载器默认保存路径 ——
否则「种子下到哪去了」「转移为什么找不到文件」「手动选择下载目录的下拉框为什么是空的」
三件事都要靠猜。

### 验证

`_verify_v507_explain.py` **45 项 0 失败**：

- 用现场那份真实配置（含 `'password': 'Frank1980'`）跑真实 `_mask_secrets`：
  密码 / token / api_key 全部变 `***`、原对象未被修改、嵌套 list[dict] 递归生效、结果里不含明文；
- 逐项验证五个排序键的「谁赢」都对，且**新旧实现排序结果完全相同**（反向对照钉死 `565ed65`）；
- 决胜键识别、并列如实说明、空列表不打日志、超 10 条截断等日志文本逐条断言。

反向对照钉死 revision 的四步流程（写脚本先用 HEAD → commit 后回填 `PREV` → 再完整跑一遍 →
顶部留注释）本次照做，`PREV` 已在脚本顶部显式声明并附原因。

# v5.0.6 (2026-09-20)

## 优化：事件分发日志不再打印函数对象的内存地址

### 现象

日志里的事件分发记录长这样（用户现场原文）：

```
处理事件：subtitle.download - [<function ChineseSubFinder.download at 0x7ff12ef285e0>, <function OpenSubtitles.download at 0x7ff12efe32e0>, <function Webhook.send at 0x7ff12ef97250>]
处理事件：transfer.finished - [<function LibraryRefresh.refresh at 0x7ff12ef7b490>, <function Webhook.send at 0x7ff12ef97250>]
```

带着内存地址、看不出插件全名，一行里挤三四个还换行 —— 排查时基本读不出「谁在听这个事件」。

### 改了什么

**1. 只打「插件类.方法」，一行读完**

`app/plugins/plugin_manager.py::__run` 新增模块级 `_handler_name()`：

```
处理事件：subtitle.download - [ChineseSubFinder.download, OpenSubtitles.download, Webhook.send]
处理事件：transfer.finished - [LibraryRefresh.refresh, Webhook.send]
```

- `_handler_name()` 优先 `__qualname__`，退化为 `__name__`，最后才用 `repr` 并正则剥掉 ` at 0x...`
  —— **不变量：显示名里永远不出现内存地址**；
- 没有任何监听者时老实打 `处理事件：xxx - []`，而不是空一片。

**2. 修掉一个静默失败：「事件发了，插件却一声不吭」**

`handler.__qualname__.split(".")[1]` 对**没有点号的函数**（模块级函数、`functools.partial`）
直接抛 `IndexError`，恰好被外层 `except` 吞掉 —— 事件已经分发出来，插件却没被执行，
报错里也看不出是哪个插件。现在改为长度判断 + 一条可照做的告警：

```
事件处理跳过：transfer.finished - plain_handler 不是插件类的方法，无法定位所属插件
```

> 仍按 `split(".")` 取前两段，**没有改成 `rpartition`** —— 只修崩溃，不动原有语义。

**3. 插件内部异常从 `print()` 改为 `log.error()`**

原先只进 stdout，**写不到 `config/logs` 下的日志文件**，排障时等于不存在。
现在带上插件名与方法名：

```
插件运行出错：AutoSub.download - RuntimeError: ... - Traceback ...
```

**4. 成功路径补一条 debug**

`事件处理完成：transfer.finished - LibraryRefresh.refresh`，便于判断卡在哪个插件
（需把日志级别调到 debug）。

### 验证

`_verify_v506_eventlog.py` 37 项断言：exec 真实 `plugin_manager.py`
（去掉 `@singleton`，用 `__new__` 绕过 `__init__`）+ 桩 `log` + 假插件实例，
**真跑一遍事件处理主循环**再核对日志文本；反向对照钉死 `b1089e3`（v5.0.5），
旧版必须复现 `0x` 地址、`<function`、`IndexError` 与「异常不进日志」四件事。
既有回归 132 项 0 失败，合计 169 项。

# v5.0.5 (2026-09-20)

## 修复：点「下载」没反应 —— 消息中心序号崩溃 + 下载目录为空崩溃

### 现象

Web 端点「下载」后界面毫无反应，日志里只剩一行异常（其余什么提示都没有）：

```
Exception: 'function' object has no attribute '_seq'
Callstack: Traceback (most recent call last):
  File "/nas-tools/web/action.py", line 611, in __download
    _, ret, dir, ret_msg = Downloader().download(media_info=media, ...
  File "/nas-tools/app/downloader/downloader.py", line 375, in download
    __download_fail("请检查下载设置所选下载器是否有效且启用")
  File "/nas-tools/app/downloader/downloader.py", line 297, in ...
```

同一份日志里还有**另一个独立**的异常（打开下载对话框时取下载目录）：

```
Exception: 'NoneType' object is not iterable
  File "/nas-tools/app/downloader/downloader.py", line 1177, in get_download_dirs
    save_path_list = [attr.get("save_path") for attr in downloaddir if attr.get("save_path")]
```

### 根因一：`@singleton` 把类名换成了函数，`MessageCenter._seq` 必然崩

`app/utils/commons.py` 的 `singleton` 是装饰器工厂，它把被装饰的类整个替换成一个
包装函数：

```python
def singleton(cls):
    def _singleton(*args, **kwargs):
        if cls not in INSTANCES:
            INSTANCES[cls] = cls(*args, **kwargs)
        return INSTANCES[cls]
    return _singleton
```

于是模块级名字 `MessageCenter` 从「类」变成了「函数 `_singleton`」。而
`message_center.py` 的 `__append_message_queue()` 里写的是：

```python
MessageCenter._seq += 1
```

类体方法里引用 `MessageCenter` 走的是全局查找，拿到的是那个包装函数，于是
`'function' object has no attribute '_seq'`。

后果不是「消息中心偶尔出错」，而是**所有 `insert_system_message()` 全部失败**：
`message.py` 里 20 多处调用（下载失败、订阅成功、转移完成、AI 助手消息……）无一幸免。
又因为 `__download_fail()` 里「插消息中心」排在「推送消息客户端」**之前**，异常直接
冒泡出 `download()`，所以表现为：**下载失败既不提示、也不推送，接口还 500。**

### 根因二：下载器的「下载目录」可能是 `None`

装载下载器配置时：

```python
"download_dir": json.loads(downloader_conf.DOWNLOAD_DIR)
```

该字段在 DB 里是 JSON 文本，未配置时可能是空串 / `None` / `"null"`，`json.loads`
得到 `None`。而下游直接遍历它（`for attr in downloaddir`、
`download_dirs += downloaddir`），`None` 抛
`TypeError: 'NoneType' object is not iterable` —— Web 端「下载目录」下拉框 500。

### 修复

**`app/message/message_center.py`**

- 序号计数器改为模块级 `_seq_counter` + 加锁的 `_next_seq()`，不再引用被替换掉的类名
- 语义保持不变：单调递增、只增不减；`get_system_messages()` 的游标行为一字未改

**`app/downloader/downloader.py`**

- 新增 `_load_download_dir()`：`None` / 空串 / `"null"` / 非列表 一律归一化成 `[]`
- `get_download_dirs()` / `get_download_visit_dirs()` 的遍历处补 `or []` 兜底
- 下载器失配时报出**可定位**的原因，而不是笼统的「无效或未启用」：
  - 没选下载器 → `下载设置「预设」未指定下载器，请到「设置 → 下载器」…`
  - 选了但查不到 / 建不起来 → `下载器 ID=99 不存在、未启用或初始化失败…`
- 同时修正了判断顺序：`get_downloader_conf()` 在 `did` 为空时会返回**全部下载器配置**
  这个真值 dict，原来先取配置会把「根本没选下载器」伪装成「下载器不存在」

### 验证

新增 `_verify_v505_download.py` **42 项断言**，全部用真实代码执行，并用
`git show <旧提交>:<文件>` 取旧版做反向对照：

- 旧版 `insert_system_message` 精确复现 `'function' object has no attribute '_seq'`，新版正常
- 序号并发唯一性：8 线程 × 500 次取号，无重号、无丢失
- 游标语义回归：`lst_seq=0` / 字符串 `"0"` / 非法值 / 等于最新序号，行为与原实现一致
- 旧版 `get_download_dirs` 精确复现 `'NoneType' object is not iterable`，新版返回 `[]`
- `_load_download_dir` 九种输入（`None` / 空串 / `"null"` / `{}` / 垃圾串 / 正常配置 …）
- `download()` 诊断分支：新旧实现日志逐条对照

既有回归 469 项（agent 231 + guard 27 + guide 144 + 解析 24 + MTeam 11 + 失败日志 32）
**0 失败**，合计 **511 项**。

### 兼容性

- 无配置变更、无数据库迁移
- `get_system_messages()` 的返回结构与游标语义完全不变，前端无需改动

### 排查口诀

`@singleton` 装饰过的类，**类体里不要再写 `类名.属性`** ——
那时类名已经是个函数了。仓库里另外 35 个 `@singleton` 类已扫描确认无同类问题。

# v5.0.4 (2026-09-20)

## 修复：下载失败时日志里没有任何原因，只能靠猜

### 现象

RSS / 自动下载失败后，日志里只剩一行插件的事件分发记录：

```
处理事件：download.fail - [<function Webhook.send at 0x...>]
```

**没有任何一行说明失败原因。** 排查时只能翻消息中心，或依赖外部插件。

### 根因

`app/downloader/downloader.py` 的 `__download_fail()` 只做两件事：发 `DownloadFail`
事件、（`in_from` 非空时）发消息给消息客户端 —— **唯独不写日志**。
所有失败路径（下载链接为空 / 取种子失败 / 下载器无效 / 添加被拒 / 异常）都要经过它，
于是失败原因在日志里彻底不可见。

### 修复

`__download_fail()` 内新增一行 `log.error`，打印：

```
【Downloader】{站点} {片名} 添加下载任务失败：{原因}
```

- 站点为空时显示「未知站点」，原因为空时显示「未知原因」
- **事件与消息的原有行为一字未改**

### 顺带澄清（重要诊断要点）

`download.add` 事件是在 `download()` 的**第一行**发出的，含义是「**开始尝试**下载」，
**不代表下载器真的收下了任务** —— 取种子失败也会先发它。
看到 `download.add` 不能推断取链成功，它后面跟着 `download.fail` 也是正常的。

### 验证

新增 `_verify_v504_faillog.py`：从真实源码切出 `__download_fail`（切片 + 闭包注桩）跑起来，
并用 `git show HEAD:...` 取上一版实现做反向对照（旧实现日志必须为空）。
32 项全过；既有回归 437 项 0 失败，合计 **469 项**。

# v5.0.3 (2026-09-20)

## 修复：搜到了资源却下载不动 —— M-Team 种子链接的 302 重定向被错误跟随

关闭 AI 助手后，在飞书发一条纯片名（如「逃出绝命街」），本地搜索链路一切正常：

```
所有站点搜索完成，有效资源数：8，总耗时 15 秒
候选名「The End of Oak Street」搜索到 8 条，合并去重后共 8 条（原 4 条）
```

但紧接着就卡在下载环节，3 秒后失败：

```
无法打开链接：https://api.m-team.cc/api/rss/dlv2?sign=e2c280e01ae05c2e4e5b03fc4eb40f11&t=1789882748&tid=1253303&uid=351045
处理事件：download.fail - [<function Webhook.send at 0x7f75edf885e0>]
```

而用户侧完全静默 —— 搜索明明成功了，却什么都没拿到。

### 根因：一个被「跟随」掉的重定向

`app/utils/torrent.py` 的 `save_torrent_file()` 里本来有一段专门处理重定向的循环，
能识别 **302 → `magnet:`** 这种情况：

```python
while req and req.status_code in [301, 302]:
    url = req.headers['Location']
    if url and url.startswith("magnet:"):
        return None, url, f"获取到磁力链接：{url}"
```

但 M-Team 走的是专属通道 `MteamUtils.get_mteam_torrent_req()`，那里用的是
`allow_redirects=True` —— **重定向在请求层就被跟完了**，上面那段循环在 M-Team
场景下永远是死代码。

M-Team 的 `dlv2` 链接经常直接 302 到磁力链，而 `requests` 不支持 `magnet:` 协议，
跟随时会抛 `InvalidSchema`（`RequestException` 的子类），被 `RequestUtils.get_res()`
的 `except` 静默吞成 `None`，对外只剩一句「无法打开链接」，真实原因完全不可见。

### 修复（3 个文件）

| 文件 | 改动 |
|---|---|
| `app/utils/mteam_utils.py` | `get_mteam_torrent_req()` 改为 `allow_redirects=False`，把重定向交回上层循环统一处理（与普通站点分支一致）；同时 `raise_exception=True`，失败时记录 `【MTeam】获取种子链接失败：<类型>: <原因>` |
| `app/utils/http_utils.py` | `get_res(raise_exception=True)` 由 `raise <异常类>` 改为 `raise e`，不再丢掉原始异常信息 |
| `app/utils/torrent.py` | 修正 `f"mteam 种子链接获取出错，详情地址为 {url}"` —— 此处 `url` 已被覆盖为 `None`，报错等于没给线索，改为保留 `origin_url` |

### 验证

新增 `_verify_v503_mteam.py`，**用真实 `requests` + 本地 HTTP 服务**复现 302，
而不是模拟：

| 层 | 内容 | 结果 |
|---|---|---|
| 层1 | 真实 `RequestUtils`：`allow_redirects=True` + 302→magnet → 返回 `None` | 精确复现「无法打开链接」 |
| 层1 | 真实 `RequestUtils`：`allow_redirects=False` → 拿到 302 原始响应 | 修复生效 |
| 层2 | 真实 `save_torrent_file()` 切片跑完整链路：旧参数报错 / 新参数拿到磁力链 | 反向复现 + 正向通过 |
| 层3 | 真实 `MteamUtils`：确认传参正确、且仍走站点 api_key 鉴权 | 通过 |

11 项断言全部通过；既有回归 426 项（agent 231 + guard 27 + guide 144 + 解析 24）
0 失败。

### 附带说明

- 本版**没有**改动搜索与判定逻辑，v5.0.1 的年份修复、v5.0.2 的协议修复原样保留。
- 下载失败的通知受消息客户端里「下载失败」开关控制（设置 → 通知 → 编辑对应客户端）。
  没勾选时失败是静默的，建议开启，否则出问题只能翻日志。
- 若本版之后仍出现下载失败，日志里会有明确的
  `【MTeam】获取种子链接失败：<异常类型>: <原因>`，可直接看出是超时、连接被拒还是协议问题。

# v5.0.2 (2026-09-20)

## 修复：在飞书发片名时 AI 回「没能正确理解你的指令」，工具一次都没执行

v5.0.1 发版后实测：在飞书里发一条纯片名（如「逃出绝命街」），AI 助手回了
「抱歉，我这次没能正确理解你的指令。请换一种说法再说一遍」。日志形如：

```
当前模型不支持 function calling（***.BadRequestError: OpenAIException - {"error":
{"message":"Invalid JSON data: Failed to deserialize the JSON body into the target
type: function_call: data did not match any variant of untagged enum FunctionCall
at line 1 column 51944","type":"invalid_request_error","code":"json_parse_error"}}），改用文本协议
模型输出疑似工具调用但未能识别，已拦下不外发：{"tool": "search_media", "args": {"keyword": "逃出绝命街"}} </think> {"tool": "search_media", "args": {"keyword": "逃出绝命街"}}
```

这是**两个独立缺陷**叠在一起，第二个把第一个的兜底也吃掉了。

### 缺陷一：请求体里多了一个网关不接受的 `function_call`

`__agent_loop_functions` 显式传了 `function_call="auto"`，而该网关的 `FunctionCall`
是一个**不接受字符串**的 untagged enum，反序列化请求体时直接 400：

```
function_call: data did not match any variant of untagged enum FunctionCall
```

错误文本里含 `function`，被 `_UnsupportedToolsError` 的判定条件捕获 → 日志打成
「当前模型不支持 function calling」并降级 —— **看起来像模型/网关不支持，实际是
我们多发了一个字段**。

按 OpenAI 规范，请求里带 `functions` 时 `function_call` 默认即为 `auto`，该字段可以省略：

```python
completion = self.__get_model(message=messages,
                              user=userid,
                              functions=functions,
                              timeout=90)   # 不再传 function_call="auto"
```

### 缺陷二：文本协议解析不了「思维链 + 重复输出」

降级到文本协议后，带思考的模型（Qwen3 等）把工具调用**输出了两遍**，
中间还夹着思维链结束标签：

```
{"tool": "search_media", "args": {"keyword": "逃出绝命街"}} </think> {"tool": "search_media", "args": {"keyword": "逃出绝命街"}}
```

旧实现取「首尾大括号之间」的整段再 `json.loads` —— 两个对象拼在一起必然失败。
于是模型明明给出了正确的工具调用，守卫却只能把它拦下、重试、再回一句兜底话术，
**用户什么也拿不到**。

修复（`app/helper/openai_helper.py`）：

- 新增 `_strip_thinking()` —— 剥掉思维链；只剩结束标签时**取它之后的内容**
  （那才是最终答复，思维链里那份常是半成品参数）
- 新增 `_extract_json_strict()` —— 改用 `json.JSONDecoder().raw_decode()` 逐个 `{`
  尝试，解析出一个完整对象即返回，**天然忽略对象之后的多余内容**
- `_extract_json_object()` 先剥思维链再解析，失败才回退原文
- 文本协议提示词补一句「也不要在思考过程里重复输出」

### 验证（426 项断言全通过）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 首次请求是否带 `function_call` | 是（`"auto"`） | **否** |
| 模拟该网关（拒绝字符串 `function_call`） | 400 → **降级到文本协议** | 原生 functions 走通，2 次调用 |
| 该场景下工具是否真的执行 | **否** | 是（`action=version`，结果回填第 2 轮） |
| 用户最终拿到 | 「没能正确理解你的指令」 | 正常答复 |

`_nastool_agent_test.py` 231 项（本版新增 6 项，含上述严格网关复现）
+ `_check_agent_guard.py` 27 项 + `_nastool_agent_guide_test.py` 144 项
+ 解析专项 24 项（用日志原文反向复现了故障）。

### 兼容性

- 不带 `function_call` 的请求语义与 `"auto"` 完全等价，支持原生 function calling
  的后端行为不变；
- 若后端**确实**不支持 functions，降级链路原样保留，行为与之前一致（不劣化）；
- 降级日志文案由「当前模型不支持 function calling」改为「原生 function calling
  不可用」，避免再把网关兼容问题误报成模型能力问题；

# v5.0.1 (2026-09-20)

## 紧急修复：所有带年份的资源被判「年份不匹配」

v5.0.0 发版后实测「异次元骇客」（The Thirteenth Floor, 1999）仍然搜不到，
日志呈现为**全站零有效**：

```
学校 返回数据：9
The Thirteenth Floor 1999 CEE BluRay 1080p x264 TrueHD 5.1-UBits 与 1999 年份不匹配
...
Local:【Indexer】学校 9 条数据中，过滤 0，不匹配 9（年份不符9），错误 0，有效 0
Local:【Indexer】馒头 16 条数据中，过滤 0，不匹配 16（年份不符16），错误 0，有效 0
```

**种子名里写着 1999，目标年份也是 1999，却被判「年份不匹配」**。而站点侧完全健康
（馒头 16 条、学校 9 条、HDTime 4 条、红豆饭 5 条全部正常返回，片名也对得上）。

### 根因：年份两侧类型不一致，严格相等恒为 False

| 侧 | 来源 | 值 | 类型 |
|---|---|---|---|
| 种子名 | `MetaVideoV2` → `guessit` | `1999` | **int** |
| 目标媒体 | `set_tmdb_info` → `release_date[0:4]` | `'1999'` | **str** |

`1999 == '1999'` 恒为 `False`。在开启「增强识别V2」（`laboratory.recognize_enhance_enable: true`，
本机配置正是开启状态）时，判定层三处年份比较全部因此失守：

- 电影本地判定：`meta_info.year == match_media.year`
- 剧集本地判定 `year_match()`：`meta_info.year == season.air_date[0:4]`
- 在线判定：`match_media.year in torrent_name`（若为 int 更会直接 TypeError）

后果是**所有带年份的资源被误杀**，只有名字里恰好没写年份的极少数种子能通过 ——
这正好解释了更早那次「馒头 7 条中，不匹配 6，有效 1」。

### 修复

新增 `_norm_year()`，在**全部三处**比较点把两侧统一成字符串后再比：

- 只做类型归一，**仍然要求严格相等**，不放松任何口径（目标年份 2011 的资源照样被拒）
- 兼容 `"1999-04-16"`、`"1999年"` 一类带后缀的写法；`"19996"` 这类不成形的值保持原样，不误截
- 判定层之外的 `app/filter.py::is_torrent_match_sey` 原本就用 `str()` 比较，不受影响

顺带把年份不匹配的日志改成**两侧都打印**：

```
（改前）The Thirteenth Floor 1999 ... 与 1999 年份不匹配
（改后）The Thirteenth Floor 1999 ... 资源年份 1999 与目标年份 1999 不匹配
```

改前那句在现场是看不懂的 —— 正因为两边都显示 1999，才没人想到是类型问题。

### 附带：异常不再被静默吞掉

三个判定变体的 `except Exception as err: print(str(err))` 改为写日志并计入「错误」：

- 以前出错的种子只 `print` 到 stdout，既不进日志也不计数，现场只看到「有效 0、错误 0」，
  无法区分「站点真没有」与「程序内部报错」
- 现在会出现在「错误 E」计数与日志里

### 验证

新增 `_verify_d1_year_type.py`：用**真实的 `_base.py`** 加**现场日志里的真实标题**
端到端跑 `filter_search_results_local`，35 项断言全部通过：

- 修复后：馒头 17 条 → `过滤 0，不匹配 0，错误 0，有效 17`
- 把 `_norm_year` 换回旧逻辑：`不匹配 17（年份不符17），错误 0，有效 0` —— 精确复现现场
- 目标年份改成 2011：仍然 `有效 0`（口径没被放松）
- 剧集 `year_match`、异常可见性各自单独验证

既有 6 个脚本（A1 / B1 / B2 / B3 / A3 纯逻辑 / A3 端到端）回归通过。

### 升级提示

- 本次只改判定层，**不碰抓取层、不新增配置项**，无需调整配置
- 若之前为了绕过这个问题而关掉过「增强识别V2」，升级到 5.0.1 后可以重新打开
  （本次问题与识别方式无关，是两侧年份类型不一致）
- 现场日志里还有三项属站点侧/账号侧，与本次修复无关，需自行处理：
  - `聆音 抓取未成功[needLogin]` → 该站 Cookie 已失效，需更新
  - `大青虫 / 织梦 等待超时` → 域名或代理问题
  - `肉丝 未解析到种子` → 站点响应正常但未解析到种子，若该站确有资源请单独反馈

# v5.0.0 (2026-09-20)

## 为什么是 5.0.0：对「搜不到资源」的系统性治理

这一版不是修一个 bug，而是把**「明明站点上有一堆资源，程序却只搜到零星几条、甚至一条都搜不到」**
这条链路上的三个盲区一次性打开：**看不见（黑箱）**、**搜不对（选词维度窄）**、**判不准（不匹配不解释）**。

### 定性依据：问题不在站点，在程序自己

实测症状：

```
馒头 7 条数据中，过滤 0，不匹配 6，错误 0，有效 1
```

这一行四个计数直接排除了站点侧：**抓回 7 条说明网络、Cookie、反爬、站点侧全部正常**，
`过滤 0` 说明规则层也没动手。7 条里 6 条被程序自己判掉、只留 1 条 —— 症结在
**② 选词（用什么片名去搜）** 和 **④ 逐条判定（判掉的凭什么判掉）**。

因此本版**没有**动抓取层（不挂 Jackett/Prowlarr 做后端、不移植第三方转换器、不重构索引器）。
抓取类改造对上述症状**全部无效** —— 站点侧本来就是健康的。改动集中在选词、判定、可观测三处。

---

## 一、可观测：黑箱打开（A1 + A2）

### 1.1 「未搜索到数据」不再是大口袋（A1）

**问题**：原来只要一条种子都没解析出来，无论真实原因是什么，界面与日志一律显示
`XX 未搜索到数据`。而这背后至少藏着 4 种完全不同的成因，处置方式也完全不同：

| 真实原因 | 用户看到 | 该做什么 |
|---|---|---|
| Cloudflare 挑战页 | 未搜索到数据 | 换 UA / 挂代理 / 换域名 |
| Cookie 失效被踢到登录页 | 未搜索到数据 | 更新 Cookie |
| HTTP 403 / 429 / 5xx | 未搜索到数据 | 站点侧限流或故障 |
| 请求超时根本没回来 | 未搜索到数据 | 网络/代理不通 |
| 真的没有这个资源 | 未搜索到数据 | 换关键词，**这才是唯一该换词的情况** |

最后一行是操作指引的**反向误导**：前四种情况下去换关键词，换一辈子也搜不到。

**根因**：feapder 的爬虫在 HTTP 被拒或请求失败时不抛异常、不调用 `parse()`，
`is_error` 始终停在 `False`，于是上层只能把它统统报成「没有数据」。

**改动**：

- `app/indexer/client/_spider.py`：新增 `search_state` / `search_state_desc` 两个类属性；
  新增**模块级纯函数 `classify_page_state(html_text, status_code, final_url, headers)`**，
  按 **响应头 > 状态码 > 最终 URL > 页面文本** 的可靠性顺序归类为
  `noResults` / `needLogin` / `CFBlocked` / `httpError`；`mark_timeout()` 补上第五种 `timeout`。
- 文本特征只保留**高特异串**（如 `cf-mitigated` 响应头、`name="password"` 表单元素），
  刻意不用「登录」这类会出现在正常页面导航栏的普通词，避免把好页面误判成登录页。
- `app/indexer/client/builtin.py`：`__spider_search` 由 2 元组扩为 **4 元组**
  （多返回状态与说明）；新增 `_SEARCH_STATE_TEXT` 文案表、`_fallback_search_state` 给非 feapder 通道兜底。
- **向后兼容**：`noResults` 仍沿用原文案「未搜索到数据」，老用户看到的措辞不变，
  只有真正出问题时才会多出那几种明确提示。

### 1.2 「不匹配」拆成 7 个子项（A2）

**问题**：`不匹配 6` 是个**大口袋** —— 程序内部有 7 个不同的出口都会给这个计数 +1：

| 出口 | 含义 |
|---|---|
| `name` | 种子名与片名不像 |
| `ratio` | 名称相似度不够 |
| `tmdb` | 回查 TMDB 查不到条目 |
| `id` | TMDB ID 不一致 |
| `year` | 年份不符 |
| `season` | 季不符 |
| `sey` | 集不符 |

四个计数里最关键的「不匹配」恰恰是最不透明的：**知道 6 条死了，不知道死在哪一步**。

**改动**（`app/indexer/client/_base.py`，**只加计数与文案，一个字都没动判定逻辑**）：

- 新增 `_MATCH_FAIL_LABELS`（7 元组）、`_bump_match_fail()`、`_format_match_fail_detail()`、
  `_dominant_match_fail_hint()`。
- 17 处 `index_match_fail += 1` **全部配对**上分类自增；6 处进度文案改为带明细。
- 三个变体（Online / Local / Local-TV）都已覆盖。

**效果**：重搜一次就能看到

```
馒头 7 条数据中，过滤 0，不匹配 6（名称脏1/TMDB无条目1/ID不符2/季集年不符2），错误 0，有效 1
```

`名称脏` → 去改**自定义识别词**；`ID不符/年份不符` → 去改**匹配口径**；
`TMDB无条目` → 是 TMDB 侧的问题。**一次搜索直接定因，不用再逐条猜。**

---

## 二、检索维度：从「一个名字搜一次」到「候选名轮询 + IMDb 兜底」（B1 + B2 + B3）

### 2.1 英文名兜底：触发条件从「零结果」放宽到「不足 5 条」（B1）

**这是本版对「搜不到」最直接的一处命中。**

**问题**：原代码只有在第一轮**一条有效结果都没有**（`len(media_list) == 0`）时才会换名称重搜。
意味着**第一轮只要认出 1 条，第二个名称就永远不会被尝试** —— 而第二个名称通常是**英文名/原名**，
它在外文站与中英混排站点的命中率明显高于中文名。

于是出现最典型的症状：**站点上明明有 50 个资源，程序只给你 1 个，然后收工。**

**改动**：

- `app/searcher.py`：把 `get_en_second_round_threshold()`、`media_dedupe_key()`、
  `merge_media_lists()` 提升为**模块级函数**，供 `web` 与 `app` 两条入口共用同一份实现。
- 兜底条件改为 `len(media_list) < get_en_second_round_threshold()`，阈值来自新配置
  `laboratory.search_en_min_result`，**默认 5**。
- 两轮结果**合并去重**（`merge_media_lists`）：只增加、不减少，第一轮结果一条都不会丢。
- 覆盖两条入口：网页搜索（`web/backend/search_torrents.py`）
  与消息渠道/订阅搜索（`app/searcher.py`）。

**回退开关**：把 `search_en_min_result` 设为 `1`，行为与老版本**完全一致**。

### 2.2 IMDb ID 检索通道（B2）

原代码早已能从 TMDB 拿到 `imdb_id`，但**只解析、从不使用** —— 发出去查的那一侧始终是片名。
而 IMDb 编号（`tt0111161`）全球唯一，**不受中英文译名、别名、繁简、错字影响**，
是中文片名搜不到时最可靠的兜底维度。

**改动**：

- `app/indexer/client/_plugins.py`：`search()` 新增 `imdb_id` 参数，追加 `search_by_imdb()` 一轮；
  新增 `__merge_by_enclosure` 按下载链接去重合并。
  **插件未实现该方法时静默降级**（`run_plugin_method` 返回 `None`），第三方插件不受影响。
- `app/plugins/modules/jackett.py`：新增 `search_by_imdb`，走 Torznab `t=movie&imdbid=tt...` 并支持分页。
- `app/plugins/modules/prowlarr.py`：新增 `search_by_imdb`（`type=movie&query=tt...`），
  同时重构 `search` 抽出 `__indexer_headers` / `__extract_indexer_id` / `__parse_releases` / `__search_api`。
- `app/indexer/client/_spider.py`：新增 `imdbid` 类属性，`setparam` 增加 `imdb_id` 参数，
  `inputs_dict` 三处补 `"imdbid"` 占位符 —— 内置站点规则里没写这个占位符时传空串，**行为不变**。
- **顺带纠正一处语义取反**：`error_flag` 原代码在成功时反而记为出错（`True` 表示异常），
  已按 `True = 出错` 统一。
- `app/indexer/client/_mteam.py`：保留被注释的 IMDB 检索代码，改为**说明性注释**
  （接口契约已变更，不贸然启用，改为可查的线索而非死代码）。

### 2.3 多候选关键词轮询（B3）

上一版只有固定「第一轮 + 第二轮」两个名称，本版改为**候选名单轮询**。

- `app/searcher.py` 新增：
  - `get_search_candidate_max()` —— 候选上限，读 `laboratory.search_candidate_max`，默认 **2**；
  - `iter_tmdb_alias_names(media_info)` —— 从**已在内存中的** `tmdb_info` 里取别名
    （电影 `alternative_titles.titles`、剧集 `results`、`translations`），**不额外请求 TMDB**；
  - `build_search_candidates(media_info, cn_first, max_candidates)` ——
    返回有序去重名单：**首选名 → 次选名 → 原名 → TMDB 别名**；
    `media_info.keyword` 有值（RSS/订阅里手填的搜索词）时**只用它，短路返回**。
- 搜索主流程改为：逐个候选名搜索，**累计有效结果达到阈值即停**。
- **请求量不放大**：候选上限默认 2，与老版本「中英两个名字」的规模**完全相同**；
  只有主动调大 `search_candidate_max` 才会增加轮次。

---

## 三、站点体检：七层分开测（A3，新模块）

**新增 `app/sites/site_health.py`（805 行）** —— 以前站点出问题只能靠 `test_connection()`
给一个「通 / 不通」，本版把它扩成**分层体检**，每层独立给结论与建议：

| 层 | 检查内容 |
|---|---|
| `L0` | 配置完整性（站点名、域名、Cookie、UA） |
| `L0b` | **索引器定义**（内置规则 or 插件索引器是否匹配该域名） |
| `L1` | 网络可达（DNS 解析 + TCP 连通，直连与代理分别测） |
| `L2` | HTTP 语义（状态码、重定向、最终地址） |
| `L3` | 反爬（Cloudflare 挑战页识别） |
| `L4` | 登录态（Cookie 是否有效、是否被踢到登录页） |
| `L5` | **探针搜索**（真跑一次搜索，看能不能抓到种子） |
| `L6` | **选择器字段完整度矩阵**（标题/下载链接/大小/做种数等字段的命中率） |

**设计与易用性要点**：

- **结论只指向最底层的失败项**：L1 连不上时不会再提示你去改选择器，避免把人引到错误方向。
- **支持自定义探针关键词** —— 最有用的一招是：
  **把搜不到的那个片名填进去**，直接看它死在哪一层。默认关键词 `1080p`。
- **全通过却仍搜不到**时，结论会明确把你引回「选词 / 判定层」（即本版第一、二节的内容），
  告诉你站点没问题、别再折腾站点。
- 站点配了代理时，L1 直连失败只报「注意」**不报「异常」**，不制造假故障。
- 出口：**站点管理 → 站点测试 → 每行右侧「体检」按钮**；
  后端新增 `site_health` 动作与 `/site/health` API。

**顺手做的两处重构（重要）**：

1. 把 A1 的判定树提炼为 `_spider.py::classify_page_state()` 纯函数 ——
   **索引器搜索与站点体检共用同一份判定**，不会再出现「搜索说 A、体检说 B」的两套真相。
2. 把 `BuiltinIndexer.__spider_search` 提炼为 `builtin.py::spider_search()` ——
   **体检的探针搜索与真实搜索跑的是同一条链路**，体检通过就等于真实搜索可用。

---

## 四、修复：索引器与插件索引器的 5 个真缺陷（C1–C5）

| # | 文件 | 缺陷 | 后果 |
|---|---|---|---|
| C1 | `_plugins.py` | `def __int__(self, indexer)` 是 `__init__` 的笔误 | 该类**从未按预期初始化过**，无参构造直接 `TypeError` |
| C2 | `builtin.py` | `PluginsSpider().sites()` 被调用了两次 | 每次搜索**白白多打一轮插件站点列表请求**（插件多时开销明显） |
| C3 | `prowlarr.py` | `public` 硬编码为 `True` | **所有 Prowlarr 站点都被当成公开站**，隐私站被错误判定，影响做种/限免判断 |
| C4 | `prowlarr.py` | `downloadvolumefactor` / `uploadvolumefactor` / `peers` / `freeleech` / `imdbid` 字段恒为 `None` | 免费/做种数信息丢失，**刷流与限免判断失真** |
| C5 | `jackett.py` / `prowlarr.py` | 分页偏移写死为 `0` | **翻页永远只返回第一页**，抓不到更多结果 |

C4 顺带补了 `__normalize_imdb_id`（Prowlarr 返回的 `imdbid` 格式不统一，需归一后才可比较）；
C5 同时给 Jackett 的关键词补了 `quote()` 转义（原样拼接，中文/特殊字符关键词会拼出非法 URL）。

---

## 五、新增配置项

| 配置项 | 默认 | 位置 | 说明 |
|---|---|---|---|
| `laboratory.search_en_min_result` | `5` | 基础设置 → 实验室 | 第一轮有效结果少于该条数时用另一个名称补搜；`1` = 老行为 |
| `laboratory.search_candidate_max` | `2` | 基础设置 → 实验室 | 候选关键词上限；`2` 与老版本请求量相同 |

两项均已加入**「实验室」设置页**并配悬浮说明，存盘即时生效，无需改文件。

---

## 六、验证

**6 个离线验证脚本、213 项断言、0 失败**（均在项目 `.workbuddy/artifacts/` 下，可复跑）：

| 脚本 | 覆盖 | 断言数 |
|---|---|---|
| `_verify_a1_classify.py` | 抓取状态分类（CF/登录/HTTP/超时/正常页，纯函数与适配层**两路必须一致**） | 36 |
| `_verify_b1_en_fallback.py` | 阈值触发 + 合并去重（含「第一轮 1 条 + 第二轮 5 条 → 5 条」的真实场景） | 9 |
| `_verify_b2_imdb.py` | 静默降级、去重合并、`error_flag` 语义、URL 拼装 | — |
| `_verify_b3_candidates.py` | 候选名单构造、去重、上限、keyword 短路 | 40 |
| `_verify_a3_site_health.py` | 体检各层纯逻辑、字段矩阵统计 | 45 |
| `_verify_a3_flow.py` | 体检端到端分层顺序与短路路径 | 53 |

另：全部改动文件 `py_compile` 通过；`site.html` 结构校验通过（`<div>` 130/130 平衡、Jinja 标签配对）。

**内置索引文件校验**：`app/indexer/client/builtin.py` 已改动，`BuiltinIndexerFileMd5`
常量**同步更新为 `3dc2f31259442247aca11e8c24fc6654`**（LF 归一化后计算），
启动时不会出现「内置索引文件被改动」的误报。

---

## 七、兼容性与升级提示

- **数据库、配置文件、目录结构均无破坏性变更**，可直接覆盖升级。
- **老用户不改 `config.yaml` 也能用**：两个新配置项在代码内均有默认值（5 与 2）。
- **请求量默认不变**：`search_candidate_max = 2` 等价于老版本的「中英两个名字」规模。
- **界面文案兼容**：真的没有资源时仍是原来那句「未搜索到数据」。
- **一键回退**：`search_en_min_result: 1` 即可让补搜逻辑回到老行为（只有零结果才补搜）。

### 关于「搜不到」的排障顺序（本版之后的推荐路径）

1. 看站点搜索结果行 —— 现在它自己会说明问题：
   - `搜索失败（Cloudflare 拦截 / 需要登录 / HTTP xxx / 超时）` → **站点侧**，别换关键词
   - `不匹配 N（明细…）` → 看明细：`名称脏` 去改自定义识别词，`ID不符/年份不符` 去改匹配口径
   - `0 条数据` → 站点侧真的没有

   > 实测过的站间差异：中文名在部分中英混排站点命中率低是**站点命名习惯**决定的，
   > 与程序无关；现在由英文名/别名轮询兜住。
2. 还定不了因 → **站点管理 → 站点测试 → 体检**，把那个搜不到的片名填进探针关键词，
   一次看清它死在哪一层。
3. 体检全绿但仍搜不到 → 回到第 1 步的「不匹配」明细，问题在**判定口径**，不在站点。

## 版本号

- 从 `v4.5.10` 升级至 `v5.0.0`（**大版本**：检索策略、可观测性、站点诊断三块能力同时变更）
- **未包含**：D 组（年份口径放宽、归因增强）与 E 组（选择器回退链、挂 Jackett/Prowlarr 做后端）
  按计划缓做

# v4.5.10 (2026-09-20)

## 修复：启动时误报「内置索引文件被改动」

**现象**：每次启动都输出下面这段，且**照做重装后依旧报警**：

```
------------------------------------------------------------------
【Config】内置索引文件被改动，为保证稳定性，请检查是否安装第三方插件或者人为修改
1. 如果为docker容器/套件，请删除容器/套件重新添加
2. 如果为其他版本，请重新下载
------------------------------------------------------------------
```

这段提示会让人以为是自己装了插件或改过文件，于是删容器、重装、重下，
全都白折腾 —— 问题其实在程序自身。

**根因**：**硬编码 MD5 与换行符差异相互叠加**，两个问题各占一半。

1. `initializer.py` 启动时用 `StringUtils.verify_integrity` 校验
   `app/indexer/client/builtin.py` 的 MD5，期望值硬编码在 `types.py`。
2. 本项目 `core.autocrlf=true`（无 `.gitattributes`），Windows 工作区文件被检出为
   **CRLF**（实测 `builtin.py` 含 419 处 CRLF），而期望值与仓库内 blob 均为 **LF**。
   按字节做 MD5 必然不同 —— **与用户环境、插件完全无关**。
3. 该常量最后一次同步是在 `ace0398`；此后 `d7fa9d6` 修改了 `builtin.py`
   （新增 `_describe_search_error` 做搜索错误归类），**但未同步更新常量**。
   于是从该提交起，所有版本都会误报。

逐版本实测（对仓库内 blob 计算 MD5）：

| 提交 | 说明 | builtin.py 实际 MD5 | 期望常量 | 匹配 |
|---|---|---|---|---|
| `b48e0f0` | Initial commit | `e87e1a15…` | `e87e1a15…` | 是 |
| `ace0398` | 修复绿联影视卡顿 | `e87e1a15…` | `e87e1a15…` | 是 |
| `d7fa9d6` | 索引器搜索失败不再与「无资源」混为一谈 | `f69c98fa…` | `e87e1a15…` | **否** |
| `b1a6729` | v4.5.9 | `f69c98fa…` | `e87e1a15…` | **否** |

**改动**：

- `app/utils/string_utils.py`：新增 `StringUtils.md5_hash_file_lf()`，
  计算前把 `CRLF`/`CR` 统一归一为 `LF`；`verify_integrity()` 改用它。
- `app/utils/types.py`：常量更新为 `f69c98fa943764540181756230de7b4b`。

**没有只改常量了事**。之所以要同时改校验逻辑：只要 MD5 是按原始字节算的，
换行符一变就再次误报 —— 这次更新常量只是把问题推迟到下一次。改为归一化比较后，
**无论在 Windows 还是 Linux 检出、换行符如何变化，同一份源码都得到同一结果**，
不会再次误报，同时**保留了对真实篡改的检出能力**。

**验证**：`.workbuddy/tests/builtin_md5_crlf_verify.py`（桩件跑真实源码），12 项全部通过。

| # | 项目 | 结果 |
|---|---|---|
| 1 | 同一文件 LF 版与 CRLF 版：**旧逻辑**算出不同 MD5 | `f69c98fa…` vs `0d30fd22…`（问题复现） |
| 2 | 同一文件 LF 版与 CRLF 版：**新逻辑**结果一致 | `f69c98fa…` == `f69c98fa…` |
| 3 | 常量 = LF 归一化后 `builtin.py` 的 MD5 | 相符 |
| 4 | CRLF 文件通过校验（修复前会失败） | 通过 |
| 5 | LF 文件通过校验 | 通过 |
| 6 | 篡改文件后仍能检出 | 检出 |
| 7 | 文件不存在 / 期望值为空时放行（保持原语义） | 通过 |
| 8 | 源码级校验：新方法已定义、旧常量值已移除 | 通过 |

**影响**：该检查原本只 `log.error` 打日志，**不抛异常、不阻断启动**，
因此升级前也不会影响功能，只是噪音与误导。升级后该提示不再出现。

## 版本号

- 从 `v4.5.9` 升级至 `v4.5.10`

# v4.5.9 (2026-09-19)

## 修复：种子名匹配做繁简归一化，繁体命名站点不再搜不到

**现象**：用简体中文名搜索时，港台等使用**繁体命名**种子的站点始终搜不到资源，
即使站内确实有该片。典型例子：搜「异次元骇客」，站点明明有
`異次元駭客.The.Thirteenth.Floor.1999.1080p.BluRay` 却一条都匹配不上。
而同一个关键词在 TMDB 能查到、在 PTHelper 能搜到，更让人以为是自己站点配错了。

**根因**：

资源过滤的 `name_match`（`app/indexer/client/_base.py`）用**子串包含**（`in`）比较，
而简体「异次元骇客」与繁体「異次元駭客」是两组**完全不同的 Unicode 码位**，
互不为子串：

```
'异次元骇客' ⊄ '異次元駭客'        '異次元駭客' ⊄ '异次元骇客'
```

于是繁体命名的种子必然匹配失败，被静默丢入弱识别分支 —— 
**站点有资源、却显示搜不到**。实测四种命名方式：

| 种子命名 | 改动前 | 改动后 |
|---|---|---|
| `異次元駭客.The.Thirteenth.Floor.1999.1080p.BluRay` | 未命中 | **命中** |
| `异次元骇客.The.Thirteenth.Floor.1999.1080p` | 命中 | 命中 |
| `The.Thirteenth.Floor.1999.1080p.BluRay.x264` | 未命中 | 未命中 |
| `异次元骇客 異次元駭客 The Thirteenth Floor 1999` | 命中 | 命中 |

**改动**：

- `app/utils/string_utils.py`：新增 `StringUtils.to_simplified()`。
  **复用项目既有的 `zhconv`**（`requirements.txt:125` 早已声明，8 个文件在用），
  **不引入任何新依赖**。对空值、非字符串、转换异常均有兜底 ——
  最坏情况退化为改动前的行为。
- `app/indexer/client/_base.py`：`name_match` 改为**先把种子名、描述、
  `org_string`、`original_title` 四者归一化为简体，再做子串比较**。

放在 `StringUtils` 而非 `_base.py` 内部，因为该文件本来就 `import zhconv`，
是同字形转换的既有归属地，其他模块将来也能直接复用。

**只统一字形，不引入新的匹配维度** —— 因此误匹配率不变。已验证
`鐵達尼號.Titanic.1997` 等无关影片仍不会误命中。

**验证**：

| # | 项目 | 结果 |
|---|---|---|
| 1 | 纯繁体命名 | 未命中 → **命中** |
| 2 | 繁体出现在描述里 | 未命中 → **命中** |
| 3 | 纯简体 / 繁简英混合 | 命中 → 命中（不回归） |
| 4 | 纯英文命名 | 仍不命中 |
| 5 | 无关中文片、纯数字符号串 | 仍不命中（**无新增误匹配**） |
| 6 | `to_simplified` 单元断言 6 项（含幂等、`None`、空串） | 全部通过 |
| 7 | 源码级校验 6 项（确认真实文件内改动到位、旧写法已移除） | 全部通过 |

验证脚本：`.workbuddy/tests/name_match_t2s_verify.py`（桩件跑真实源码）。

**性能**：`zhconv.convert` 单次约 **14 微秒**；按「100 站 × 100 条 × 2 字段」
的极端量估算总开销约 **0.28 秒**，相对搜索本身的网络耗时（秒级）可忽略。

**已知边界**（不影响本次修复的场景）：

- 只统一**繁简字形**，**不处理译名差异** —— 例如「十三度凶间」与「异次元骇客」
  是不同译名，归一化无法互通（实测确认仍不命中）。这类场景需换用别名搜索。
- 不处理标点、空格、全半角等其他差异。

## 文档：新增自然语言意图理解框架梳理

新增 `docs/nl-intent-framework.md`，基于代码实测整理意图路由的现状诊断、
功能边界枚举、四层意图判定方法，以及分「必改 / 建议改 / 可选 / 已核实无问题 /
待确认」五档的待修改项清单。

## 版本号

- 从 `v4.5.8` 升级至 `v4.5.9`

# v4.5.8 (2026-09-19)

## 修复：文件管理页整理完成后跳回初始状态

**现象**：在「媒体整理 → 文件管理」中整理完某个文件夹后，界面会自动刷新并
跳回最原始的状态。例如从 `download` 进入 `complete` 下的一个子目录，
整理完成后立即重置回初始列表，**当前所在目录与滚动位置全部丢失**。
整理多部影片时，每整理完一部就要重新逐级点回原来的目录，非常影响效率。

**根因**：

整理成功后的刷新走的是 `navmenu(source)`，而 `navmenu` 的设计是「切换页面」——
它把 `#page_content` 整块替换成新页面的 HTML。对「原地刷新」而言用错了工具：
当前页被当成新页重新加载，模板重新渲染 `#mediafile_path`（回到 `{{ Dir }}`
默认值），`init_mediafile_tree()` 重新执行，于是目录树、当前路径、
滚动位置随之全部重置。

**改动**：

- `web/static/js/functions.js`：`manual_media_transfer` 处理成功后**按来源页面分流** ——
  文件管理页走新增的原地刷新 `refresh_mediafile()`，
  其它页面（未识别、历史记录等）行为保持不变，仍走 `navmenu`。
  加 `typeof` 守卫，因为 `refresh_mediafile` 只定义在文件管理页模板内。
- `web/templates/rename/mediafile.html`：
  - 目录栏**新增「刷新」按钮**，可独立刷新当前目录；
    并给「同步」「媒体」「下载」三个按钮补上 `title`，说明各自列出哪些目录。
  - 新增 `refresh_mediafile()`：记住文件区滚动位置 → 重建目录树 →
    在数据渲染完成的回调里还原滚动位置，**全程不改动当前目录**。
  - `refresh_files()` 与 `init_mediafile_tree()` 各新增一个可选回调参数，
    旧调用方式不传即保持原行为（**向后兼容**）。
  - 还原回调采用「消费即置空」，确保只在本次初始化的**首个刷新**中触发，
    避免展开子目录时的刷新反复重置滚动位置。

### 验证

`.workbuddy/tests/mediafile_refresh_verify.js`（Node + 最小 DOM 桩件，
加载 `mediafile.html` 的真实内联脚本）—— **17 项断言全部通过**：

| 组 | 覆盖内容 |
|---|---|
| 1 | `done` 回调在数据渲染**之后**才调用；文件计数已更新 |
| 2 | 不传回调的旧调用不报错（向后兼容） |
| 3 | **还原回调只触发一次**，子目录展开不会重复还原滚动 |
| 4 | 记住并还原 `scrollTop`；刷新过程**不改动当前目录** |
| 5 | 文件管理页走原地刷新、其它页面仍走 `navmenu`、守卫存在 |
| 6 | 刷新按钮存在；同步 / 媒体 / 下载三个按钮均保留并补了说明 |

`node --check` 校验 `functions.js` 与抽取出的内联脚本均通过。

## 版本号

- 从 `v4.5.7` 升级至 `v4.5.8`

# v4.5.7 (2026-09-19)

## 修复：TMDB 网页兜底只认唯一结果，导致「无法识别媒体信息」

**现象**：转移任务对这类目录报「无法识别媒体信息」：

```
/downloads/complete/凡人修仙传 年番4.A.Record.of.a.Mortals.Journey.to.Immortality.2020.S01.2160p.WEB-DL.H265.HDR.AAC-PTerWEB
```

即使开启 TMDB 网页兜底也照样失败，日志只给出一句「TMDB网站返回数据过多」。

**根因**：

目录名本身的识别是**正常的** —— 实测 `MetaVideoV2` 得到名称
`A Record Of A Mortals Journey To Immortality`、年份 `2020`、季 `1`，全部正确。
问题出在下一环的 TMDB 查询。

该关键词在 TMDB 搜索页返回**两条**结果：

| 结果 | 作品 | 年份 |
|---|---|---|
| `/tv/106449` | 凡人修仙传 | 2020 ← 正主 |
| `/tv/282348` | 凡人修仙传：虚天战纪 | 2025 ← 续作 |

而 `__search_tmdb_web` 的判定是 `if len(tmdb_links) == 1` —— **结果多于一条就整体放弃**，
不做任何区分。续作、衍生剧与正主并存是常见情况，于是这类片名永远识别不出来。

顺带修掉一个既存缺陷：链接形如 `/tv/1396-breaking-bad`，原用 `split("/")[-1]`
取 ID 会得到 `1396-breaking-bad`（不是数字），单结果场景下也会取到非法 ID。

**改动**（均在 `app/media/media.py`）：

- `__search_tmdb_web` 重写为「字典收集 + 分流」：单条结果直接返回，
  多条结果走收敛逻辑，**不再一刀切放弃**。
- 新增 `__parse_tmdb_web_card`：**直接从搜索结果卡片解析标题与年份**，
  不额外发请求。同一作品在页面上会有两个 `<a>`（封面图无文本、标题有文本），
  已处理重复文本折叠，避免标题被写成两遍。
- 新增 `__pick_tmdb_web_link`：三级收敛，任一级收敛到唯一即采用 ——
  ① 标题匹配且年份一致；
  ② 标题匹配的候选其实指向**同一个 TMDB ID**（不同译名/别名指向同一部剧）；
  ③ 仅按年份收敛且只剩一条。
  三级都收敛不到唯一结果时返回 `None`，交由上层继续走其它兜底。
- 新增 `__tmdb_id_of`：用 `re.match(r"^/(?:tv|movie)/(\d+)")` 正确提取数字 ID。

**设计取舍**：这一步是**纯规则**（标题比对 + 年份比对），没有引入 AI。
原因是现有的「AI 识别文件名」链路只做要素抽取（`{title, year, season, episode}`），
抽完仍旧回落 TMDB 查询，**并不参与多候选决策**，解决不了「候选并存」。
本场景年份这一条规则就已足够。

### 验证

- `.workbuddy/tests/tmdb_web_multi_verify.py` —— **10 个场景全部通过**，
  覆盖：无年份不收敛、年份 2020 命中正主、年份 2025 命中续作、单候选直通、
  分辨率数字不当年份、年份缺失不崩、slug 取 ID、同 ID 多记录收敛、
  同标题不同 ID 必须拒绝、年份冲突不误选。
- `.workbuddy/tests/tmdb_web_e2e_verify.py`（联网端到端）：
  - 凡人修仙传 2020 → 精确命中 `/tv/106449`
  - 凡人修仙传 2025 → 精确命中 `/tv/282348`
  - Breaking Bad（13 条候选，其中 3 条同标题但分属不同剧集）→
    返回 `None`，**正确拒绝而不是乱选**
- `.workbuddy/tests/metainfo_fanren_verify.py` —— `MetaVideoV2` 识别回归正常。
- `py_compile` 通过。

## 版本号

- 从 `v4.5.6` 升级至 `v4.5.7`

# v4.5.6 (2026-09-19)

## 修复：消息中心会漏消息、跨天重复推送，长消息分段失败还会丢尾段

三个问题一起修，因为都在同一条消息链路上。

**现象一（漏消息）**：一次入库多部剧，站内消息中心只有第一条能看到，
后面的消息再也刷不出来 —— 不是延迟，是永久丢失。

**现象二（重复推送）**：跨天之后，昨天的旧消息会被当成新消息重新推送一遍，
浏览器通知也跟着重复弹。

**现象三（丢尾段）**：微信、Slack 这类超长消息会分段发送，
只要中间某一段失败，**后面的分段就全部不发了**，用户拿到一条残缺消息且毫不知情。

**根因**：

1. 增量拉取用**秒级时间戳**当游标（`message_center.py`），并配合 `.seconds` 做比较、
   遇到不新的就 `break`。而 `action.py` 会把游标推进到最新一条消息的时间。
   时间戳精度只到秒，同秒插入的多条消息**共享同一个时间戳** ——
   游标一旦落到这个时间上，同秒的其余消息就全被判定为「已读」而永久丢失。
   必然触发场景：`send_transfer_tv_message` 在循环体内为每部剧插一条消息，
   一次转移 N 部剧就是同秒插 N 条。

2. 同一个 `.seconds` 还会**丢弃天数且不保留负号**：
   昨天 23:59 相对今天 00:01 得到的是 `.seconds = 85200`（正数！），
   于是旧消息被判定为新消息。正确值应是 `.total_seconds = -1200`。

3. `__sendmsg` 的分段循环里，某一段失败就 `return`，后续分段永远不会执行。

**改动**：

- `app/message/message_center.py`：
  - 引入**自增序号 `seq`** 唯一标识一条消息，队列按 `seq` 递减排列；
  - `get_system_messages` 的游标由 `lst_time` 改为 `lst_seq`，比较也改用 `seq`，
    `break` 的语义随之变正确（遇到更旧的才停）；
  - 游标做 `int()` 归一化 —— 前端 JSON 可能传来字符串，只靠 `or 0` 兜底会让
    `"0"`（truthy）参与比较而抛 `TypeError`；
  - 顺带移除已无用的 `datetime` 导入。
- `web/action.py`：`get_system_message` 的入参出参由 `lst_time` 改为 `lst_seq`。
- `web/main.py`：WebSocket 处理器同步改用 `lst_seq`，并在转发给前端的
  消息体里补上 `seq` 字段。
- `web/static/js/functions.js`：`render_message` 与 `get_message` 全部改用
  `lst_seq`，首屏传 `0`。
- `app/message/message.py`：分段独立处理，统计失败段数，**全部跑完再统一返回**；
  返回值统一为 `(状态, 错误信息)` 二元组，`send_channel_msg` 解包后仍返回
  单状态，**调用方契约不变**；渠道路径缺失时由 `return None` 改为返回二元组，
  避免调用方解包崩溃。

> 为什么不用毫秒时间戳：最初考虑「把时间精度提到毫秒」，
> 但实测证明**同样不够** —— 同秒插入的 5 条消息共享同一时间戳，
> 前端回传「最新一条的时间」后，用 `ts > lst_ts` 相比会把同 `ts` 的其余 4 条
> 全部排除。**时间戳无法唯一标识一条消息**，必须引入单调递增序号。
> 选 `seq` 还有两个额外好处：与将来落库的自增主键天然一致，
> 且彻底消除跨天问题（不再比较时间）。

### 验证

两套回归脚本均**加载项目真实源码**（桩件替换导入依赖，而非复刻实现）：

- `.workbuddy/tests/message_center_fix_verify.py` —— 22 个断言全部通过，
  8 个场景：同秒插入 2 条、循环连插 5 条同秒、跨天不推送、正常间隔、
  空队列、超 `maxlen=50`、游标类型容错（`0`/`None`/`""`/`"0"`）、游标不倒退。
- `.workbuddy/tests/message_segment_fix_verify.py` —— 19 个断言全部通过，
  重点覆盖「第 2 段失败后第 3 段仍发出」，并确认 `send_channel_msg` 返回值
  仍为单个状态、失败时为 `False` 而不是元组。

端到端复刻 action + WebSocket 的 JSON 往返：

```
首屏            : ['A']             lst_seq = 1
同秒增量 3 条   : ['D','C','B']     lst_seq = 4
无新消息        : []                lst_seq = 4
字符串游标 "4"  : []                lst_seq = 4
缺少游标字段    : ['D','C','B','A']  lst_seq = 4
```

`py_compile` 四个文件与 `node --check functions.js` 均通过，
并确认全项目无 `lst_time` 残留。

### 已知遗留（本次未改）

- 前端浏览器通知仍用秒级 `msg.time` 与页面打开时间比较
  （`functions.js`），页面打开后**同一秒内**到达的消息不会弹浏览器通知，
  站内列表正常。属前端表现层，改动涉及通知取舍，单独评估。
- 首屏默认取 20 条而消息队列上限为 50 条，积压 20~50 条之间时游标会停在
  中间，剩余部分不会补发。与后续「消息落库」一起解决更自然。

## 版本号

- 从 `v4.5.5` 升级至 `v4.5.6`

> **从 `v4.5.4` 及更早版本升级，请直接用本版本**：`v4.5.5` 未单独发布镜像
> （详见其下说明），本版本已包含 `v4.5.5` 与 `v4.5.6` 的全部改动。

# v4.5.5 (2026-09-19)

## 修复：RSS 订阅未按站点设置走代理，解析失败也不再报天书

**现象一**：订阅拉取某个站点的 RSS 时，日志里只打出一行 XML 解析异常的
完整堆栈（`Exception: syntax error ... rss_helper.py, line 46`），既看不出
是哪个站点，也看不出站点到底返回了什么，无从下手。

**现象二**：在站点维护里开启了「使用代理服务器」，订阅路径拉 RSS 列表依然
直连。对需要代理才能打开的站点，表现就是「未获取到数据」；可同一轮里对
种子详情的 Free/HR 检测却能正常走通 —— 前后矛盾，极易误判成站点挂了。

**根因**分两处：

1. `RssHelper.parse_rssxml` 自身有三处问题：
   - 过期判定写的是 `if ret_xml in _rss_expired_msg`，**全等比较**，
     实际响应体是「RSS 链接已过期, 您需要获得一个新的!」这类句子，几乎永不成立；
   - **HTML 页面是合法 XML**，`parseString` 会成功，`getElementsByTagName("item")`
     返回空后直接静默返回空列表，从不进入 `except` —— 站点返回登录页时日志里
     一个字都没有；
   - 请求失败分支静默 `return []`，没有任何日志。

2. `app/rss.py:133` 调用 `parse_rssxml` 时**未传 `proxy`**。函数内 `proxy`
   默认 `False`，`proxies` 被置为 `None`，于是恒不走代理。而 `site_proxy`
   在第 124 行就已经读出来了，并在第 199 行传给 `check_torrent_rss`、
   最终第 458 行用于解析种子详情；`brushtask.py:218` 也传了。全项目六处
   站点访问里，**只有这一处漏了**。

> 补充说明代理机制：`app.proxies` 只是「代理服务器地址」，不是统一开关。
> `RequestUtils._proxies` 默认为 `None`，必须由调用方显式传 `proxies=` 才生效。
> 是否使用代理由站点的 `proxy` 字段决定，基础设置页也明确写着
> 「站点默认不使用代理，如需使用需在站点维护中开启」。

**改动**：

- `app/helper/rss_helper.py`：
  - 新增 `__describe()`，把「拿到的响应不是有效 RSS」翻译成站点维度的原因，
    按响应体形态分五类输出（空内容 / 返回网页 / 接口错误 / 非 RSS 文本 /
    内容残缺），均带站点域名与响应片段；
  - 过期特征改为**包含匹配**，并覆盖「RSS链接已过期」等无空格变体；
  - 新增 HTML 根元素判定，补上原本完全静默的盲区；
  - 请求失败分支补 `warn` 日志；原始异常降为 `debug`，排查能力不减。
- `app/rss.py`：`parse_rssxml` 传入 `proxy=site_proxy`，与刷流路径对齐；
  「未获取到数据」的提示改为指向站点维度的错误日志。
- `app/brushtask.py`：同步该提示措辞。

### 验证

- 桩件无依赖复现（`.workbuddy/tests/rss_err_diag.py`），8 个场景对比改动前后。
  原报错现场从 `not well-formed (invalid token): line 1, column 8` 变为：

  ```
  【Rss】站点 pt.example.com 返回的不是RSS格式，响应片段：您的账号权限不足，请重新登录后再试
  【Rss】站点 pt.example.com 的返回内容可以确认不是RSS，请到站点维护中核对 rssurl 以及 Cookie 是否有效
  ```

  同时覆盖并验证链接过期、JSON 鉴权失败、HTML 登录页、空响应、XML 残缺；
  正常 RSS 回归仍返回 1 条种子。
- 代理传导验证（`.workbuddy/tests/proxy_passthrough.py`）：站点 `proxy=True`
  时 `requests` 收到 `app.proxies`，`proxy=False` 时直连；订阅与刷流两条
  RSS 路径行为一致。
- `py_compile` 三个文件语法通过。

## 版本号

- 从 `v4.5.4` 升级至 `v4.5.5`

> **本版本未单独发布镜像**：`v4.5.5` 与 `v4.5.6` 是同一批推送的，
> 而构建流程一次推送只为分支最新提交触发一次，因此只有 `v4.5.6` 产出了
> 镜像与 Release。**本版本的改动已全部包含在 `v4.5.6` 中**，
> 升级请直接使用 `4.5.6`（或 `latest`）。

# v4.5.4 (2026-09-18)

## 修复：飞书消息卡片的标题和内容一模一样

**现象**：在飞书里收到的消息，卡片顶部标题栏的文字与卡片正文完全相同，
一句话被重复展示两遍。

**根因**在两层之间：
1. `app/message/message.py` 的 `__sendmsg` 有一条通用规则 —— **调用方没给标题时，
   把正文顶到标题上，正文置空**（对多数渠道这是对的：通知栏要标题，正文可以空着）；
2. 交互链路正好大量使用「只有一段文字」的消息，例如 AI 应答
   （`search_channel_msg(title="", text=回答)`）、"开始搜索 XXX…"、"输入有误！"等，
   经上一条规则后变成 `title=那段文字, text=""`；
3. 飞书的 `send_msg` 里，`text` 为空时为了满足「卡片 elements 不能为空」，
   会兜底补一个正文块，而它的内容取的正是 `title` —— 于是 header 与正文一字不差。

只有一段文字时才有这个现象；`标题 + 正文`、多行标题（首行作标题、其余进正文）
本来就正常，所以此前没被发现。

**修法**（`app/message/client/feishu.py`）：
- `send_msg` 内新增 `header_title` / `body`：**有独立正文时**头部显示标题、正文显示内容；
  **没有独立正文时**头部留空、由正文区承载内容 —— 内容只出现一次；
- 图片的 `alt` 在标题为空时退回用正文，不再出现空 alt；
- `__send_card` 支持不下发 `header` 字段（飞书卡片 header 可选，elements 非空即可）。

多行消息的行为保持不变（首行进标题栏、其余行进正文），列表选择卡片同样不受影响。

### 验证

新增飞书卡片专项 10 项（`artifacts/_check_feishu_dup.py`，修复前 4 项失败）：
逐条断言「标题栏内容不得与任一正文段落完全相同」，覆盖 AI 应答单行 / 多行、
纯标题通知、纯标题报错、标题+正文、纯标题+图片+按钮、标题+正文+按钮、只有正文、
双空入参、列表选择卡片。

# v4.5.3 (2026-09-18)

## 修复：关闭 AI 后消息没人管；AI 掉线没有本地回退；发片名只给简介还反过来要链接

三个问题同源：**消息路由把「AI 能不能用」想得太简单** —— 只看 API Key 填没填，
不看「AI 助手」开关；AI 调用失败时也没有退路；而 AI 的工具链路还带着一个错误的意图标记。

### 一、关掉「AI 助手」，消息反而没人管了

路由判定用的是 `get_state()`，它只检查 **API Key 是否非空**，完全不知道开关的状态。
于是关掉「AI 助手」之后：
- 以「订阅」「搜索」「下载」开头的消息，在开了「AI优先接管」时仍被送进 AI；
- 其余文本被当成聊天消息交给 AI 的纯聊天模式，既不搜索也不订阅。

用户看到的就是「关了 AI 反而不听使唤」。修复：新增 `is_agent_available()`
（API Key 非空 **且** 开关打开），路由改用它判定 —— 关闭开关后消息回到本地关键词路径，
**与没有配置 AI 时完全一致**。`get_state()` 语义保持不变，因为「AI 识别文件名」
（`laboratory.chatgpt_enable`）走的是那条线：关掉「AI 助手」不该连带停掉文件识别。

### 二、AI 联网失败后，消息被一句「连接失败」挡住

AI 不可用时（网络不通、接口报错、Key 失效），原来只会把错误文案发给用户就结束，
「搜索 片名」「订阅 片名」「磁力链」这些**没有 AI 时本来就能用**的能力全部不可用。
修复：新增 `is_error_answer()` 集中判定失败文案，命中则先提示一句
「AI 助手暂时不可用：…，已切换为本地模式处理」，再把这条消息**按本地关键词规则重新处理一遍**。
回退过程中带 `ai_disabled=True`，确保不会再送回 AI 形成死循环。

### 三、发片名只回简介、还反过来要链接

两处叠加：
1. 路由把这条消息判成「聊天」，`SEARCH_MEDIA_TYPE` 存的是 `ASK`；AI 的 `search_media`
   工具发起搜索时沿用了这个标记，搜索链路里「只有一条匹配」的分支与「用户回复序号」
   都会走成**添加订阅**——搜片搜成了订阅。修复：`search_media` 显式传 `intent="SEARCH"`，
   且该意图的优先级高于路由推断（放在路由之后落地，避免被覆盖）。
2. 提示词与工具描述没有说清「给片名该干什么」：`query_media_info` 只查资料，
   `download_by_link` 缺链接时会向用户追问。修复：新增提示词第 13 条 ——
   用户给出片名要**直接调 `search_media` 搜资源**，不要只查资料就结束，
   **更不要向用户索要链接/磁力链/种子**；两个工具的说明也据此补明确。

### 验证

新增消息路由专项 26 项（开关语义 / 关掉 AI 后的本地可用性 / 联网失败回退 /
工具意图 / 端到端「模型决定搜片 → 真搜资源而不是订阅」），
加上既有 Agent 主套件 225、追问与能力目录 144、出口守卫 27、识别口径 32、
连接测试与排版 80、模板自检，全部通过。

# v4.5.2 (2026-09-18)

## 修复：设置页字段网格跨行错位；config.yaml 一个键名带空格致百度 OCR 静默失效

### 一、设置页「系统 / 媒体 / 服务」三张卡片的字段列没有对齐

**现象**：同一张卡片里，上面一行 4 个字段、下面一行 2 个字段时，下面这行的字段会跑到
页面中线，与上面的字段对不上（用户截图报告「排版有问题，没有对齐」）。

**根因**：`.col-lg` 是 `flex: 1 0 0%` —— 它按**本行**剩余空间等分。设置页每个逻辑行
都是一个独立的 `<div class="row">`，于是 3 列的行每列 1/3、2 列的行每列 1/2，
**列起点随行内项数变化，跨行必然错位**。

**修法**：同卡片内统一到 1/4 网格基元 `col-lg-3`，每行填满整行——

| 行内项数 | 列类名 |
|---|---|
| 4 项 | `col-lg-3` ×4 |
| 3 项 | `col-lg-3`, `col-lg-3`, `col-lg-6` |
| 2 项 | `col-lg-6` ×2 |
| 1 项 | 保持整行（`col-lg`，`flex:1` 即占满） |

涉及 `basic_system`（13 个字段）、`basic_media`（22 个字段）、`basic_service`（7 个字段），
共修改 39 行 class。`basic_media` 的 8 个开关由原来的三行（3/3/2）改为 4+4 两行，
与其上方的 4 列字段完全同列。窄屏行为不变：`col-lg-*` 在 <992px 时自动整行堆叠。

**实测**（Edge 无头 + `getBoundingClientRect`，1440 视口）：

| | 卡片内出现过的列左边界(px) |
|---|---|
| 修复前 | 8 / 357.5 / **474** / 707 / **940** / 1056.5（混了两套网格，间距 349.5 与 466 并存） |
| 修复后 | 8 / 357.5 / 707 / 1056.5（严格 349.5 等距，即 1/4 网格） |

三张卡片共 42 个字段列全部落在网格上（结构层断言 + 浏览器实测双重校验）。

### 二、`config.yaml` 的 `baiduocr_secret key` 键名里带空格

`config/config.yaml` 里写的是 `baiduocr_secret key:`（**键名中间有空格**），后端读的是
`ocr.get('baiduocr_secret_key')` —— 名字不同，永远读不到。

后果：**直接编辑配置文件**按注释填 Secret Key 的用户，`baiduocr_avaliable()` 恒为 False，
百度 OCR 静默失效并回退到自建/第三方公共后端，界面上没有任何提示（与 v4.5.1 修的
JSON 静默失效同类）。从界面填写保存不受影响（前端 id 本来就是 `ocr.baiduocr_secret_key`）。

已对全文件扫描键名，确认这是唯一一处不合规的键。修好只影响以后新建配置的用户
（config.yaml 仅在配置文件不存在时被复制），已存在的用户配置需手工把那个空格去掉。

### 验证

离线断言：模板自检 + 识别口径 32 + 连接测试与排版 80 + Agent 主套件 224 +
追问与能力目录 144 + 出口守卫 27，全部通过；另加本轮新增的网格专项：
结构层（列起点是否落 1/4 网格）+ 浏览器实测（列坐标是否等距）。

# v4.5.1 (2026-09-18)

## 修复：模型的工具调用 JSON 被当成答复发给用户

向飞书问「正在下载」时，用户收到的是 `{"tool": "query_downloading", "args": {}}`
这串原始 JSON，而不是「正在下载 0 个任务，下载器空闲中」。**这不是"格式难看"的问题**：
被当成答复，意味着这次工具调用**根本没有执行**，用户还得自己面对一串内部结构。

### 根因

Agent 的两条协议各有一个「本轮没有工具调用 → 把模型输出当最终答复」的出口，
两处都只判定了「不是工具调用」，没有判定「看着就是工具调用」：

| 出口 | 触发条件 |
|---|---|
| functions 协议（`openai_helper.py:566`） | 后端声称支持 functions，模型却把调用写进了正文，而不是结构化 `function_call` |
| 文本协议（`openai_helper.py:612`） | `__parse_text_call` 要求整体以 `{` 开头、`}` 结尾；JSON 前后多一句说明、结尾多一个句号即判失败 |
| 纯聊天模式（`openai_helper.py:485`） | 没有工具可用，但模型仍可能吐工具调用 JSON |
| 结果转述（`openai_helper.py:667`） | 提示词里写了「不要输出原始 JSON」，但没有代码兜底 |

`query_downloading` 的 `id` 是**可选参数**，所以 `{"args": {}}` 是合法调用——
本该成功的一次查询被吞掉了。更麻烦的是 `__save_turn` 会把原始 JSON 当成助手回复
写进会话历史，下一轮模型看到自己上轮"回答"了 JSON，倾向于继续这么输出。

### 修复

- **两条协议互认**：functions 出口取不到结构化 `function_call` 时，再按文本协议把正文
  解析一次。模型写在正文里的调用现在会被真正执行，而不是当成答复发出去。
- **新增统一出口守卫** `_looks_like_tool_call()`：解析不出来、但看着仍是工具调用时
  （含 JSON 被截断的情况）**绝不外发**——先请模型用自然语言重答一次，仍不行就发一句
  可行动的兜底提示「请换一种说法再说一遍，例如「在下载什么」」。
- **放宽文本协议解析**：`__parse_text_call` 改用 `_extract_json_object()`，容忍代码围栏、
  前后说明文字与结尾标点（此前要求严格匹配首尾大括号，正是泄露的入口之一）。
- **不再污染会话历史**：守卫命中时写进历史的是替代文案，不是那串 JSON。
- **同类出口一并兜住**：纯聊天模式与危险操作后的结果转述套用同一守卫，避免下次换个
  入口又冒出来。

### 验证

离线断言：出口守卫专项 27 项 + Agent 主套件 224 + 追问与能力目录 144 + 识别口径 32 +
连接测试与排版 80 + 模板自检，全部通过。覆盖「正文里的调用（纯 JSON / 带说明文字 /
带代码围栏 / 结尾标点）必须被识别并真正执行」「被截断的调用走重试与兜底」「正常答复
不被误伤」「纯聊天与结果转述不外发」「原有追问与危险操作确认机制未受影响」。

# v4.5.0 (2026-09-18)

## 变更：「AI 识别文件名」并入 AI 助手，并修复静默失效

原名「ChatGPT增强识别」（配置键 `laboratory.chatgpt_enable` 保持不变），现改名为
**「AI 识别文件名」**，并从「识别与搜索」分区**移入「AI 助手」分区**。

它与 AI 助手本就共用同一个 OpenAI 客户端和同一份 `openai.*` 配置，**不需要单独申请
API Key**。此前开关在「识别与搜索」分区、而它依赖的 Api Url / Key 在「AI 助手」分区，
容易被误认为是两套独立配置（"没有 API 怎么用"）。

### 修复

- **识别结果解析加容错**：模型把 JSON 包在 Markdown 代码围栏里、或前后带说明文字时，
  此前会直接抛 `JSONDecodeError`，被 `except` 吞掉后表现为「ChatGPT识别失败」并静默
  降级到下一步，日志里看不出原因。现在统一走 `_extract_json_object()`：先剥掉代码
  围栏，再取首尾大括号之间的内容，全部尝试失败才返回空。
- **补上请求超时**：该路径此前未传 `request_timeout`，会走 SDK 默认的 **600 秒**，
  后端卡住时会把搜索/整理流程挂住；现在显式传 60 秒（Agent 各入口一直传的是 90 秒）。
- **失败时打印原始返回**：解析不出来时在日志里输出模型原始返回的前 200 字符，
  便于区分「模型输出格式问题」与「接口问题」。
- **文本协议解析复用同一套围栏剥离逻辑**：`__parse_text_call` 与文件名识别现在共用
  `_strip_code_fence()`，避免两处实现再次分叉（文本协议解析本来就做了剥离，只有识别
  路径漏了）。

### 说明

- 该开关**默认关闭**，且位于识别回退链第 4 层（识别词解析 → TMDB → WEB增强识别 →
  本项 → 关键字猜测），**前三层成功时不会被调用**，开着不影响正常流程；价值集中在
  冷门片、番剧和命名很乱的资源上。
- 配置键名未改动，**老用户无需调整 `config.yaml`**。
- 未填写 OpenAI API Key 时该开关无效，可先用「测试连接」确认 AI 配置可用。

# v4.4.0 (2026-09-18)

## 新增功能：AI 助手连接自检 + 配置区重排

### 1. 新增「测试连接」，明确反馈成功或失败

设置页 AI 助手区新增**测试连接**按钮：用**界面上当前填写的内容**（无需先保存）实测一次，几秒内给出结论。

- 成功时回显**后端真实模型名、耗时、模型返回内容**，并额外探测该后端**是否支持原生 function calling**
  （不支持会提示「将自动降级为文本协议，功能不受影响」，这正是本地小模型最常见的情况）
- 失败时按类型给出可照做的结论：`401` Key 无效、`403` 无权限、`404` 地址不存在、
  `400` 模型名不存在、`429` 限流/余额不足、连接被拒（带出底层原因）、超时（提示模型可能仍在加载）
- 测试**只使用请求级 `api_key` / `api_base`，不改动 SDK 全局配置**
  —— 因此不会把正在运行的 AI 助手切到未保存的地址上，测试期间其他消息照常工作
- 缺 API Key 时前端先拦截提示，不发无效请求

### 2. 实验室卡片按用途分区，选项统一对齐

- 卡片重排为**三个分区**，各自带标题与说明：
  ① **Telegram Bot API 代理**（仅 Telegram 机器人反代，标注「与 AI 助手无关」）；
  ② **AI 助手**（只留 AI 通用配置）；③ **识别与搜索**（原有媒体识别/搜索策略开关）
- 此前 Telegram 反代项与 AI 配置上下相邻、无分区标题，容易被当成 AI 的一部分；现在归属清晰
- **全部选项统一四列网格**：文本类选项连排、开关类连排，不再出现「标签在上的输入框」与
  「标签在侧的开关」混在同一行导致标签错位；窄屏自动堆叠，标签与控件始终同行同高
- 「测试连接」按钮与 API Url / Key / 模型同排齐平，结果行紧随其下

### 3. API 地址容错

- `openai.api_url` 现在会**自动规整**：去掉末尾斜杠与多余的 `/v1`，
  用户填 `https://xx`、`https://xx/`、`https://xx/v1` 三种写法都能正常工作
  （此前带 `/v1` 会拼成 `/v1/v1`，带斜杠会拼成 `//v1`，直接连不上）
- 规整规则在连接测试与正式调用中**完全一致**，所以「测试通过」约等于「保存后可用」

# v4.3.0 (2026-09-18)

## 新增功能：AI 主动询问与引导式调用

AI 助手从「被动应答」升级为「会问、会引导」，使用者不需要预先知道有哪些功能、也不要求措辞标准。

### 1. 缺参数时主动追问，并记住上下文

- 新增**必填参数校验**：必填项缺失时**不再带着残缺参数硬调底层功能**，而是转为一个具体问题发给用户
- 追问问题由工具表生成（可为每个参数配置口语化话术，未配置则回退为参数名 + 说明），
  **不交给模型措辞** —— 缺参是确定性事件，代码生成可保证「一定会问」，不会因为模型忽略提示而出错
- 新增**追问补全状态（`_pending_ask`）**：追问后挂起待办，用户回答时把「正在等什么、已知什么、还缺什么」
  注入系统提示词，模型据此合并参数并直接执行，不重复追问同一项
- 边界控制：**10 分钟**有效期；回复「取消 / 算了 / 不用了」即放弃；用户直接换话题则自动放下待办
- 会话级 `#清除` 会一并清掉待办

### 2. 能力清单：不知道能做什么就问它

- 新增**能力目录**（`AGENT_CAPABILITIES` 派生自工具表，单一数据源，新增工具自动进菜单）：
  按 **8 个分组**组织，每组给出「能做什么 + 一句示例说法」
- 新增工具 `list_capabilities`：用户问「你能做什么 / 有什么功能 / 帮助 / 菜单」、
  或消息无法识别意图、仅为问候时调用，直接输出分组清单
- **首次接触主动介绍**：与某用户在本会话的第一次对话（或 `#清除` 后重新开始），
  AI 会先简短介绍能力范围再回答问题

### 3. 工具范围扩展：31 → 48 个

- 新增查询类 11 个：目录同步任务、下载目录设置、备份条目、自动删种任务、单站活动趋势、
  单站做种分布、媒体库影片数量、播放记录、索引器统计、上映日历
- 新增操作类 4 个：磁力链/种子链接直接下载、按订阅历史重新订阅、媒体库同步、清理元数据缓存
- 新增清理类 2 个（危险，需确认）：删除单条订阅历史、清空未识别记录
- 新增元能力 2 个：`list_capabilities`（列能力）、`ask_user`（向用户提问，内部工具不入菜单）
- 工具表新增 `group` / `label` / `sample` / `kind` / `ask` 字段：
  `kind` 区分**只读 / 写操作 / 运维 / 元能力**，审计日志据此分类（只读与元能力不再误记审计）

### 4. 触发条件与路由

- 新增**「AI优先接管」**开关（`openai.agent_first`，默认**关**）：开启后所有文本消息优先交给 AI 理解，
  含以「订阅 / 搜索 / 下载」开头的消息；关闭时这些消息仍走原关键词规则
- **追问期间强制走 AI**（`has_pending`）：用户对追问的回答往往是「沙丘」这类极短文本，
  若不加干预会被关键词分支截走导致追问流程断裂；现在只要存在未过期待办，无论内容以什么开头都交给 AI
- 新增**「引导式询问」**开关（`openai.agent_guide_enable`，默认**开**）：关闭后信息不足只提示缺什么，
  不挂起待办、不进入多轮补全

### 5. 其他

- 系统提示词重组为「调用原则」+「询问原则」两节，明确「信息齐备就直接执行，不要多问」与
  「缺关键信息只问最关键的一项」
- 危险操作二次确认补充保存会话记录，追问与确认两条待办互不干扰
- `agent_guide_enable` 纳入配置快照，改完保存即时生效（无需重启）

> **升级提示**：新增配置项在代码中有默认值，老用户无需改动 `config.yaml` 即可使用；
> 「AI优先接管」默认关闭，升级后原有消息路由行为不变。

# v4.2.1 (2026-09-18)

## 调整：AI 助手配置项统一归入「实验室」
- AI 助手的全部配置从「设定 → 基础设置 → **媒体**」迁移到「**实验室**」，紧接 Telegram Bot Api 代理，与其它消息、识别相关开关同区，便于集中查找
- 迁移后为完整的一组 **6 项**：`OpenAI API Url`、`OpenAI API Key`、`OpenAI 模型`、`AI助手`、`危险操作需确认`、`显示工具调用`
  - 此前 `API Url` / `API Key` 留在「媒体」卡片，与模型、开关分离，需在两个卡片分别保存，易漏配，且单看「媒体」里的模型输入框无法判断该怎么配
- 补齐配置指引（悬浮说明同步更新）：
  - `API Url`：留空使用 `https://api.openai.com`；接本地模型时填本地推理服务地址，如 `http://192.168.1.10:8000`（**不要带 `/v1`**，程序会自行拼接）
  - `API Key`：必填，留空则 AI 能力整体不可用；接本地模型时填任意非空值即可
  - `OpenAI 模型`：填模型名，如 `gpt-4o-mini`、`deepseek-chat`、`qwen3-32b`
- 新增「AI 助手」分区小标题，替代此前散落在媒体卡片中的孤立输入框

> **升级提示**：配置存放于 `config.yaml`，与界面位置无关，升级后无需重新填写。
> **保存逻辑**：两个卡片的保存按钮各自收集所在卡片内的字段，后端按 key 部分更新，互不覆盖；迁移后点「实验室」的保存即可一并写入这 6 项。

## 修复：改完配置必须重启服务才生效
- `OpenAiHelper` 是单例，`init_config()` 原先**只在进程启动时执行一次**，而保存配置只写文件、
  不通知任何组件 —— 结果是**在界面上填好 API Key / 模型并保存后，进程内仍是旧值**，
  必须重启服务才生效，且没有任何报错提示，表现为「AI 配好了却完全不工作」
- 新增配置快照比对（`__ensure_fresh`）：各使用入口在使用前比对关键配置项
  （`get_media_name` / `get_answer` / `translate_to_zh` / `get_question_answer` 均先调用 `get_state`，
  因此只需在 `get_state` 中刷新即可全覆盖），发生变化则自动重新加载
- 配置未变化时仅有一次元组比较，无额外开销；其余组件（下载器、刷流、过滤等）本就是「使用前重载」模式，此处补齐

# v4.2.0 (2026-09-18)

## 新增功能：AI 助手，消息渠道可远程控制 NAStool
- 消息渠道（飞书 / 微信 / Telegram 等）中的对话从「纯聊天」升级为 **Agent**：AI 可自主调用工具查询状态、执行操作，共 **30 个工具**
  - **查询类 17 个**：下载任务、转移历史与统计、站点列表与流量数据、电影与剧集订阅、订阅历史、下载器、索引器、媒体信息、媒体库空间、未识别文件、站点最新资源、系统进程、版本检查
  - **操作类 9 个**：增删订阅、搜索并下载、启停下载任务、删除下载、重新识别未识别文件、刷新订阅、执行刷流任务、执行目录同步
  - **运维类 4 个**：重启服务、系统升级、执行删种、清空历史记录
- 新增 `app/helper/agent_tools.py`：以声明式工具表把 `WebAction().action()` 的既有能力包装成可被 function calling 调用的工具；工具只负责参数翻译与结果裁剪，不重复实现业务逻辑，因此不会与 Web 端行为产生分歧
- 工具返回值按**条目数与总长度双重裁剪**，避免站点列表、转移历史这类长结果撑爆模型上下文
- **危险操作二次确认**：删除、重启、升级、清空历史等操作不直接执行，先转为待确认状态并回提示，用户回复「确认」后才执行（确认请求 5 分钟内有效，超时作废）；可在配置中关闭
- **操作审计**：非查询类操作写入系统消息中心，可追溯执行者、时间与参数
- **协议自动降级**：优先使用原生 function calling；后端报错不支持时自动改走「JSON 指令」文本协议重试，无需手动切换；若后端静默忽略工具参数，可显式指定 `openai.agent_protocol: prompt`
- 新增 `openai.model` 配置项，可对接本地模型（OMLX / Ollama / vLLM 等，走标准 OpenAI 接口协议）与第三方中转

## 新增配置项
| 配置项 | 默认 | 说明 |
|---|---|---|
| `openai.model` | `gpt-3.5-turbo` | 模型名称，接入本地模型或中转时填写 |
| `openai.agent_enable` | `true` | AI 助手总开关，关闭则退回纯聊天 |
| `openai.agent_protocol` | `auto` | 工具调用协议：`auto` / `functions` / `prompt` |
| `openai.agent_max_rounds` | `5` | 单次对话最大工具调用轮数，防止死循环 |
| `openai.agent_confirm_dangerous` | `true` | 危险操作是否需二次确认 |
| `openai.agent_show_tools` | `false` | 是否在回复中标注调用了哪些工具 |

- 「设定 → 基础设置」新增模型名输入框与三个开关（AI助手 / 危险操作需确认 / 显示工具调用），升级用户可直接调整；未在配置文件中的键由代码内默认值兜底，老配置无需手工补键

## 缺陷修复
- 修复 `app/helper/openai_helper.py` 中 `get_answer()` 的会话保存错误：历史实现把**用户输入**当作助手回复写入上下文，导致多轮对话中模型看到的「自己说过的话」全是用户的提问，已改为保存模型回复，并新增 `__save_turn()` 成对保存一问一答
- 修复 `app/indexer/client/_base.py:32` 误读配置键：`recognize_enhance_enable` 此前读取的是 `simplify_library_notification`，导致种子搜索/匹配路径上「是否预填 TMDB 信息」的闸门实际由**入库通知精简**开关控制，两个互不相关的开关串线；修复后该闸门回到「增强识别V2」开关
  - ⚠️ 行为变化：该闸门默认值随「增强识别V2」变为**开启**（`config.yaml` 中 `recognize_enhance_enable: true`），种子搜索匹配成功时会按上游设计预填 TMDB 信息；如不需要，在界面上关闭「增强识别V2」即可

## 版本号
- 从 `v4.1.1` 升级至 `v4.2.0`

# v4.1.1 (2026-09-17)

## 缺陷修复：飞书长连接在保存配置后失效
- 修复「保存消息通知配置后，飞书机器人对任何消息都不再响应」的问题：原实现拆除旧长连接时，停掉的是 lark-oapi 的**模块级事件循环**，且该 stop 动作要 **0.5 秒后才生效**；而一次保存恰好踩在这个空档上 —— `web/action.py` 的 `__update_message_client` 先删除旧配置触发 `init_config` 停掉旧连接（此时循环仍在运行），紧接着插入新配置再触发 `init_config` 立刻新建连接并 `start()`，新连接的 `run_until_complete` 撞上那个尚未停下的循环，抛 `RuntimeError: This event loop is already running` 后当场失效；0.5 秒后旧循环停止，又把旧连接一并带走，最终一条连接都不剩
- 因触发条件与保存时序强相关，症状表现为**时好时坏**：首次配置（尚无旧连接）能正常建立并收到消息，填完 `open_id` 再保存即失效；若某次保存恰好错过 0.5 秒窗口，下一次保存又会自行恢复，容易误判为「玄学」
- 修复方式：`app/message/client/feishu.py` 新增 `__install_ws_loop()`，每条长连接启动前独占一个全新的 `asyncio` 事件循环（替换 `lark_oapi.ws.client.loop`），不再复用可能仍在运行的旧循环，从根上隔离；改动不涉及订阅与消息发送逻辑
- 新增 `__close_ws_loop()`：连接被取代时先取消残留任务再关闭其独占循环，避免反复保存配置泄漏文件描述符，同时消除 `Task was destroyed but it is pending!` 日志噪音
- `__shutdown_ws_client()` 改为显式接收所属事件循环（换循环后若仍从模块属性反查会关错对象）；`__run_ws_client()` 启动前复核当前连接是否已被取代，防止遗留连接与新连接同时收消息导致重复响应

## 说明
- 设置页的「测试」按钮通过只代表 HTTP 发送链路正常，它不启动长连接，**不能**作为接收能力的判据
- 修复需重启进程/容器后生效；重启后无论保存多少次配置，长连接均不再失效

## 版本号
- 从 `v4.1.0` 升级至 `v4.1.1`

# v4.1.0 (2026-09-17)

## 新增功能：备份/恢复条目化，支持按需勾选
- 备份能力从「整库文件拷贝」重构为**条目化导出**：`app/helper/backup_helper.py` 定义 12 个备份条目，逐项覆盖站点、刷流、订阅、下载器、用户权限、消息渠道、系统设置、媒体库同步等全部配置
- **补齐以往遗漏的配置**：
  - 站点及其「站点维护中」状态（`CONFIG_SITE` 的 Cookie、启用状态、维护标记）完整纳入
  - 刷流任务与刷流做种记录（`SITE_BRUSH_TASK`、`SITE_BRUSH_TORRENTS`）完整纳入
  - 新增 `media.db` 媒体库同步数据备份（旧版从未备份）
  - 新增自定义二级分类策略 `*.yaml` 备份（旧版仅备份默认分类）
  - 不再 DROP `alembic_version`，避免恢复后重启时迁移脚本被静默重跑
- **恢复可选择**：上传备份包后先解析条目与记录数，前端以复选框展示，可勾选需要恢复的具体内容；支持「全选/全不选」，未勾选条目原样保留
- **恢复方式改为逐表合并**：按条目标对数据表做 `DELETE` + `INSERT`（仅写入备份与当前库共有的列），单一事务执行，失败整体回滚，不再整库覆盖
- 备份包新增 `nastool_backup.json` 元数据（格式版本、应用版本、生成时间、每条目记录数）；同时保持扁平目录结构，旧版备份包可直接上传恢复
- 新增接口：`/config/backup_items`（条目定义）、`/config/backup_info`（解析备份包）；`/config/restore` 与 `/backup` 均支持传入 `items` 参数
- 服务页面「备份与恢复」弹窗重做为两段式：备份区（勾选导出范围）+ 恢复区（上传 → 预览条目 → 勾选恢复 → 结果回显）
- 兜底条目 `其它数据`：自动收纳 34 张业务表中未被显式归类者，后续新增表不会漏备

## 版本号
- 从 `v4.0.2` 升级至 `v4.1.0`

# v4.0.2 (2026-09-17)

## 文档完善：飞书配置与使用说明
- 新增「飞书机器人配置与使用」整节文档，涵盖四步流程：开放平台创建企业自建应用（权限申请、**长连接**事件订阅、版本发布与可用范围）→ NAS 侧填写凭证 → **三步自举获取自身 `open_id`** → 群通知配置（可选）
- 补充「使用方式」表：通知推送、交互式搜索、管理命令的实际输入形态（纯数字选候选、`订阅 xxx`、`http...` 直下、关键字搜索）
- 补充「飞书常见故障」排查表，覆盖 `230013`（应用可用范围/未发布）、`9991003` 等错误码对应的处理方式
- 「本分支增强点」的飞书小节拆分为「已落地的体验改进」与「实现要点」（3 秒约束规避、未注册事件兜底、卡片与图片缓存、token 提前刷新、依赖降级、回环接口安全）
- 修正 README 中滞后的版本号（页眉、升级示例、页脚）

## 版本号
- 从 `v4.0.1` 升级至 `v4.0.2`（仅文档变更，功能与上一版一致）

# v4.0.1 (2026-09-17)

## 体验优化：飞书配置与失败排查
- **解决「拿不到 open_id 就无法测试」的死锁**：被拒绝的用户发消息时，机器人会回复其本人的 `open_id`，可直接复制给管理员加入白名单（此前只提示「你不在白名单中」，用户无从得知自己的 ID）
- 「测试失败」提示改为可照做的自举指引：保存配置并开启「交互」→ 给机器人发一条消息 → 从日志读取 `open_id` → 填回管理员白名单 → 再点测试
- 新增接收者 ID 类型自动识别：按前缀区分 `ou_`（用户 open_id）/ `oc_`（群 chat_id）/ `on_`（union_id）/ 含 `@`（邮箱），群 ID 误填进用户列表也能正确发送
- 新增配置阶段 ID 格式校验，填写不符合飞书命名约定时在日志中告警，不再等到发送才报错
- 发送失败与获取 token 失败时附带错误码排查建议（覆盖 `230013` 可用范围/未发布、`230002` 接收者无效、`230098` 机器人不在群、`99991663`/`99991664` 凭证错误、`10003` App ID 不存在等）
- 渠道配置项标题改为飞书原生叫法：`群 Chat ID`、`用户 Open ID`、`管理员 Open ID`，并在提示中说明各项 ID 的具体获取步骤（存储键未变更，不影响已有配置）

## 版本号
- 从 `v4.0.0` 升级至 `v4.0.1`

# v4.0.0 (2026-09-17)

## 新增功能：飞书消息通知（支持双向交互）
- 新增 `app/message/client/feishu.py` 飞书消息客户端，基于 `lark-oapi` 的 WebSocket 长连接接收消息，**无需公网 IP、域名或内网穿透**
- 通知推送：支持下载、入库、订阅、签到、刷流、站点消息、媒体服务器等全部推送开关
- 交互式搜索：可直接在飞书中发送关键字搜索站点资源，回复序号即可下载或订阅
- 管理命令：支持 `/` 开头的管理命令，支持用户白名单与管理员白名单（基于 `open_id`）
- 消息以卡片形式发送，自动上传并展示海报图片，附带「查看详情」跳转按钮
- 新增 `/feishu` 本地回环接口接收长连接转发的事件，仅允许 `127.0.0.1` 访问
- 新增 `SearchType.FEISHU`、`SecurityHelper.check_feishu_ip()`
- 新增渠道配置项：App ID、App Secret、Chat ID、User IDs、Admin IDs、接口域名（飞书 / Lark 国际版）
- 新增依赖 `lark-oapi`

## 说明
- 飞书开放平台需创建企业自建应用并开启机器人能力，事件订阅选择「长连接」方式并订阅 `im.message.receive_v1`，发布应用后生效
- 事件回调必须在 3 秒内返回，否则飞书会超时重推（同一条命令被执行两次），因此回调只做解析并立即转发，耗时操作交由后台线程处理

## 版本号
- 从 `v3.8.0` 升级至 `v4.0.0`

# v3.8.0 (2026-09-17)

## 界面优化：页脚版本号精简
- `web/backend/web_utils.py` 的 `WebUtils.get_current_version()` 去除短提交哈希后缀，页脚仅显示版本号（如 `v3.8.0`，原为 `v3.7.11 aaa0ded`）
- 移除对 `git rev-parse HEAD` 的调用，避免在非 git 环境（如 Docker 构建产物）下取不到提交号
- 更新检测逻辑不受影响：`get_latest_version()` 走 `releases_update_only=True` 分支只返回 tag，前端 `compareVersion` 的哈希对比分支本就不可达

## 版本号
- 从 `v3.7.11` 升级至 `v3.8.0`

# v3.7.11 (2026-06-14)

## 性能修复：绿联影视媒体库同步卡顿问题
- 修复 `get_items()` 中对每个媒体条目重复请求 3 次 `video_info` API 的问题（通过 `_local_video_cache` 缓存机制，减少冗余请求约 66%）
- 修复 `get_play_url()` 和 `get_local_image_by_id()` 在内部重复调用 `video_info` 的问题（增加可选参数传递已缓存数据）
- 为分页循环添加 `MAX_PAGES=500` 上限保护，防止 API 不返回末页标记时无限循环
- 优化：1000个条目的媒体库同步时间从约10-60分钟降至合理范围

# v3.7.10 (2026-06-10)

## 绿联影视客户端重写
- 将绿联客户端从错误的 Emby API 替换为绿联原生加密 API（参考 MoviePilot 实现）
- 新增 `_UgreenCrypto` 类处理 RSA/AES-GCM 加解密
- 新增 `_UgreenApi` 类封装绿联登录、媒体列表等接口
- 修复绿联测试连接始终失败的问题

## 媒体库同步/列表修复
- `get_libraries`: 改用 `media_list` API（`v1/video/homepage/media_list`）
- `get_items`: 改用 `poster_wall_get_folder`（`v1/video/poster_wall/media_lib/get_folder`）按目录树遍历，精确获取指定媒体库内容
- `get_tv_episodes`: 改用 `get_tv`（`v2/video/details/getTV`）获取剧集详情，降级使用 `video_info`
- `refresh_root_library`: 遍历所有库逐个扫描，而非只扫第一个库
- 新增 `media_list`、`poster_wall_get_folder`、`get_tv` API 方法

## 连接优化
- 使用 `requests.Session` 复用连接，替代每次新建请求
- `_connect()` 时先关闭旧 `_api` 会话再创建新的

## 配置
- `config.yaml` 模板添加绿联影视默认配置节点（host/username/password 等字段）

## Docker
- 移除阿里云镜像源（`mirrors.aliyun.com`），改用官方 `deb.debian.org` 和 `pypi.org`
- `init-0-update` 脚本将清华镜像源替换为官方 Debian 源

## 版本号
- 从 `v3.7.7` 升级至 `v3.7.10`
