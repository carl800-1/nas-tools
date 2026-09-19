# 自然语言意图理解框架（模糊指令 → 功能触发）

> 目标：用 AI 理解模糊化的自然语言指令，替代以往「固定格式 / 固定关键词」的触发方式。
> 本文分两步：**第一步枚举可触发功能并界定边界**，**第二步设计意图判定与映射方法**。
>
> 本文基于代码实测撰写，涉及的行号与字段名均来自当前源码。
> 相关文档：`docs/message-commands.md`（现有指令体系）、`docs/message-issues.md`（通知系统问题）。

---

## 零、现状诊断：为什么必须换掉关键词触发

### 现有三套触发机制（互不统一）

| 机制 | 位置 | 触发方式 | 数量 |
|---|---|---|---|
| 斜杠命令 | `web/action.py:276-287` | 精确字符串匹配 `/ptr` 等 | 10 |
| 自然语言前缀 | `web/backend/search_torrents.py:263-286` | `startswith("订阅")` / `startswith("搜索")` / `in` 数字 | 5 类 |
| AI Agent | `app/helper/openai_helper.py` | 提示词 + 工具调用 | 48 工具 |

### 关键词触发的六个真实痛点（均有代码依据）

**痛点 1：前缀语义被硬编码，同义词缺失**

```python
elif input_str.startswith("订阅"):   # 只有「订阅」二字算订阅
elif input_str.startswith("http"):   # 只有 http 开头算链接
elif not input_str.startswith("搜索") and not input_str.startswith("下载"):
```

用户说「**追一下**这部剧」「**关注**沙丘」「**帮我下**沙丘」—— 「追」「关注」「帮我下」全部落到 else 分支，
被当成搜索或聊天。只有「订阅」「搜索」「下载」三个词有效。

**痛点 2：选择项只认 1~9**

```python
if input_str.isdigit() and int(input_str) < 10:
```

第 10 个候选（`10`）永远选不中；用户说「**第三个**」「**选 2**」也识别不了。
（`search_torrents.py:196`）

**痛点 3：参数无法从自然语言里剥离**

搜索时用正则硬剥前缀：

```python
input_str = re.sub(r"(搜索|下载)[:：\s]*", "", input_str)
```

「帮我找一下沙丘 2021 年的」剥不掉「帮我找一下」，整句被当成片名去搜。

**痛点 4：AI 与关键词的边界靠 `startswith` 划分，语义割裂**

```python
_ai_available = (not ai_disabled) and not intent and OpenAiHelper().is_agent_available()
_agent_owns = _ai_available and (has_pending(user_id) or config.openai.agent_first)
```

`agent_first` 关闭时（默认），**只有不以 搜索/下载/订阅/http 开头的文本才给 AI**。
结果是：用户说「下载沙丘」走本地关键词，说「想看沙丘」走 AI —— 两条路径、
两套行为、两套回复，体验割裂。

**痛点 5：斜杠命令与自然语言完全隔离**

`/ptr`「自动删种」必须精确输入，说「**帮我清理一下做种**」无效。
10 个斜杠命令没有任何同义说法。

**痛点 6：交互消息散落 21 处，绕开统一入口**

`search_torrents.py` **16 处** + `main.py` **5 处**直接调用 `send_channel_msg`
（实测计数，2026-09-19；详见 `docs/message-issues.md` M5）。每条分支各自拼文案、
各自处理失败，新增一种说法就要改多处。

### 结论

关键词触发的本质问题是：**把「用户想干什么」这件事，编码成了「用户说了哪个词」**。
词表永远列不全，且每加一种说法就要改代码。而 AI Agent 已经具备 48 个工具的
语义化调用能力 —— 应该让它成为**唯一的路由中枢**，关键词路径降级为**兜底**。

---

## 第一步：枚举可触发功能，界定用途与边界

### 1.1 功能清单的两层结构

系统里实际存在**两类可触发功能**，性质完全不同，必须分开枚举：

| 层次 | 范围 | 触发者 | 权限 | 数量 |
|---|---|---|---|---|
| **L1 交互指令** | 斜杠命令 + 自然语言意图 | IM 用户 | 白名单 / 管理员 | 10 + 5 类 |
| **L2 业务能力** | `AGENT_TOOLS` 工具集 | AI Agent | 经 AI 判定 + 审计 | 48 |

**关键认识**：L2 是 L1 的超集。L1 的每一个功能，在 L2 里都有对应工具：

| L1 斜杠命令 | 对应 L2 工具 | 说明 |
|---|---|---|
| `/ptr` 自动删种 | `auto_remove_torrents` | ✅ 已有 |
| `/ptt` 下载文件转移 | **无对应工具** | ⚠️ 需新建（现有仅有 `query_transfer_history` 查询） |
| `/rst` 目录同步 | `run_directory_sync` | ✅ 已有 |
| `/rss` 电影/电视剧订阅 | `add_subscribe` | ✅ 已有 |
| `/ssa` 订阅搜索 | `refresh_subscribe` | ✅ 已有（单条刷新） |
| `/tbl` 清理转移缓存 | `truncate_history` | ✅ 已有 |
| `/trh` 清理RSS缓存 | `delete_rss_history` | ✅ 已有 |
| `/utf` 重新识别 | `re_identify_unknown` | ✅ 已有 |
| `/udt` 系统更新 | `update_system` | ✅ 已有 |
| `/sta` 站点数据统计 | `query_site_statistics` | ✅ 已有 |

> ⚠️ **`/ptt`（下载文件转移）在 L2 里没有对应工具**，这是唯一缺口。
> 也就是说：用户能用斜杠命令触发转移，但**无法用自然语言触发**。
> 阶段 2 应新建一个 `run_transfer` 工具（`kind: ops`）补上。
> 另需注意 `/ssa`「订阅搜索」是<b>全部订阅</b>批搜，而 `refresh_subscribe` 是单条刷新，
> 语义不完全等价，可能也需要一个批搜工具。

**所以改造方向不是「重建一套」，而是「让 AI 用自然语言去驱动已有的 L2 工具，
并把 L1 的斜杠命令统一收敛成 L2 的别名」。**

### 1.1.1 工具的三个分类维度（正交，不是一套）

`AGENT_TOOLS` 表里每条工具有**三个互相独立的分类标记**，服务不同消费者。
理解这一点是理解整个工具集的关键 —— 它们不是"一套分类的三种叫法"，而是三个正交维度。

#### 维度 A：`group` —— 业务领域（8 组，给人和文本协议看）

```python
GROUP_ORDER = ["下载管理", "订阅与搜索", "站点", "媒体库",
               "刷流与同步", "历史与清理", "系统运维", "使用帮助"]
```

- **消费者**：能力菜单（`get_capability_menu`，给用户看）+ 文本协议提示词
  （`get_tools_prompt`，给模型看）
- **原生 function calling 不用 group** —— 工具以扁平数组喂给模型
- `GROUP_ORDER` 只决定**展示顺序**；未登记的组自动兜底排最后：
  ```python
  for name in GROUP_ORDER:
      if groups.get(name): ordered.append(...)
  for name, items in groups.items():      # 兜底，不会丢组
      ordered.append((name, items))
  ```

#### 维度 B：`kind` —— 副作用分级（4 档，给系统看）

| kind | 数量 | 含义 | 系统行为 |
|---|---|---|---|
| `read` | 26 | 只读查询 | **不写审计日志** |
| `write` | 14 | 常规写入 | 写审计日志 |
| `ops` | 6 | 运维级 | 写审计日志 |
| `meta` | 2 | 元能力 | **不写审计** |

判定用**白名单**而非黑名单（安全侧默认开启）：

```python
_NON_ACTION_KIND = ("read", "meta")

def is_action_tool(self, name):
    return bool(tool and tool.get("kind") not in _NON_ACTION_KIND)
```

> **白名单的意义**：将来新增 kind（如 `admin`），默认就记审计，不会漏。

#### 维度 C：`dangerous` —— 独立安全开关（7 个，人工判断）

⚠️ **`dangerous` 不是 `kind == "ops"` 的同义词**，两者必须分开：

| | 数量 | 成员 |
|---|---|---|
| `kind: ops` | 6 | `auto_remove_torrents` / `delete_rss_history` / `truncate_history` / `truncate_unknown` / `restart_service` / `update_system` |
| `dangerous: true` | **7** | 上面 6 个 **+ `torrent_delete`** |

**`torrent_delete` 是 `kind: write` 但被标记危险** —— 它「删除下载任务（连同已下载文件）」，会丢数据。

| 维度 | 判据 | 漏判后果 |
|---|---|---|
| 用 `kind == "ops"` 当危险判据 | ✘ | `torrent_delete` 漏掉二次确认 → **误删文件** |
| 用 `dangerous` 当审计判据 | ✘ | `clear_meta_cache`（write，非危险）漏审计 |

**所以三个维度缺一不可**，合并任意两个都会出错。

#### 分类设计中一处「不整齐」

「历史与清理」组只有 3 个工具，且**全是 `ops` + 危险，一个 `read` 都没有**。
根因是 `group` 的划分标准在**「操作对象」与「操作性质」之间摇摆**：

- 「媒体库」按**对象**分 → 组内既有查询（`query_transfer_history`）也有写入（`re_identify_unknown`）
- 「历史与清理」按**性质**分 → 组内只有「清理」这个动作

结果「**查历史**」与「**清历史**」被分到了两个组。

**建议改法（方案 A，倾向）**：把 3 个清理工具并入各自对象所属组，取消该组 ——
`truncate_history` → 媒体库、`delete_rss_history` → 订阅与搜索、`truncate_unknown` → 媒体库。
理由：`group` 的第一消费者是用户，用户想的是「我要清理媒体库的东西」，
而不是「我要做清理这个动作」。

### 1.2 L2 功能枚举（48 工具，按 8 组）

> 以下清单直接从 `app/helper/agent_tools.py` 的 `AGENT_TOOLS` 表派生。
> 该表是**单一数据源**：能力菜单、工具说明、参数校验、审计分类全部从它派生，
> 新增工具只需加一条记录，其它地方自动生效。

#### 组 1：下载管理（8）

| 工具 | 用途 | 触发意图 | 边界 / 前置条件 |
|---|---|---|---|
| `query_downloading` | 看正在下载/做种 | 「在下载什么」「进度如何」 | 只读；需下载器已配置 |
| `query_downloaded` | 看已完成下载 | 「下载完了吗」 | 只读 |
| `query_downloaders` | 列出下载器 | 「有哪些下载器」 | 只读 |
| `query_download_settings` | 看下载配置 | 「下载设置」 | 只读 |
| `download_by_link` | 按磁力/种子链接下载 | 用户**自己发来**链接 | 需合法链接；AI 不得主动索要 |
| `torrent_start` | 开始任务 | 「开始下载」「继续」 | 需任务 ID |
| `torrent_stop` | 暂停任务 | 「暂停」「停止下载」 | 需任务 ID |
| `torrent_delete` | **删任务（含文件）** | 「删掉这个任务」 | ⚠️ **危险**，需二次确认 |

#### 组 2：订阅与搜索（10）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `query_movie_subscribes` | 查电影订阅 | 「我订阅了哪些电影」 | 只读 |
| `query_tv_subscribes` | 查剧集订阅 | 「追的剧有哪些」 | 只读 |
| `query_subscribe_history` | 订阅下载历史 | 「订阅都下过什么」 | 只读 |
| `query_media_info` | 查影片资料 | 「沙丘是哪年的」 | 只查资料，**不搜索** |
| `query_calendar_events` | 上映日历 | 「最近有什么新片」 | 只读 |
| `add_subscribe` | **新增订阅** | 「订阅/追/关注 X」 | ⚠️ 同名多部时需 `query_media_info` 确认 |
| `delete_subscribe` | 删除订阅 | 「不追了」「取消订阅」 | 需明确对象 |
| `refresh_subscribe` | 刷新订阅（重搜） | 「重新搜一下订阅」 | 会触发 RSS 检索 |
| `search_media` | **发起资源搜索** | 「找/想看/下载 X」 | 结果推送给用户挑选，**不是下载** |
| `re_subscribe_history` | 补下订阅历史 | 「补下漏掉的」 | — |

> **关键边界**：`query_media_info`（查资料）≠ `search_media`（找资源）。
> 提示词第 13 条专门强调：用户说「想找/想看/下载某片」时应调 `search_media`，
> **不能只查资料就结束，更不能向用户索要链接**。

#### 组 3：站点（6）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `query_sites` | 站点列表 | 「有哪些站」 | 只读 |
| `query_site_statistics` | 站点数据统计 | 「站点的数据」 | 只读 |
| `query_site_activity` | 站点活动 | 「这站活跃吗」 | 需站点名（有 `ask`） |
| `query_site_seeding` | 做种情况 | 「这站做种多少」 | 需站点名（有 `ask`） |
| `query_site_resources` | 站点资源 | 「这站有什么资源」 | 需站点名 |
| `query_indexers` | 索引器状态 | 「索引器正常吗」 | 只读 |

#### 组 4：媒体库（8）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `query_transfer_history` | 转移历史 | 「整理了什么」 | 只读，结果需裁剪 |
| `query_transfer_statistics` | 转移统计 | 「整理了多少」 | 只读 |
| `query_library_space` | 媒体库空间 | 「还剩多少空间」 | 只读 |
| `query_media_count` | 媒体数量 | 「库里有多少片」 | 只读 |
| `query_play_history` | 播放历史 | 「最近看了什么」 | 只读 |
| `query_unknown_files` | 未识别文件 | 「有哪些没认出来」 | 只读 |
| `re_identify_unknown` | 重新识别 | 「重新识别一下」 | 写操作 |
| `sync_media_library` | 同步媒体库 | 「同步一下库」 | 写操作 |

#### 组 5：刷流与同步（5）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `run_brushtask` | 执行刷流任务 | 「刷一下流」 | 写操作 |
| `run_directory_sync` | 执行目录同步 | 「同步目录」 | 写操作 |
| `query_directory_sync` | 查同步状态 | 「同步完了吗」 | 只读 |
| `query_auto_remove_tasks` | 查自动删种配置 | 「删种规则」 | 只读 |
| `auto_remove_torrents` | **立刻删种** | 「清理做种」 | ⚠️ **危险**，可能删文件 |

#### 组 6：历史与清理（3）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `truncate_history` | **清空某类历史** | 「清理记录」 | ⚠️ **危险**，不可恢复；有 `ask` 追问清哪类 |
| `delete_rss_history` | **删单条订阅历史** | 「删掉这条记录」 | ⚠️ **危险** |
| `truncate_unknown` | **清空未识别记录** | 「清空未识别」 | ⚠️ **危险**，不可恢复 |

#### 组 7：系统运维（6）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `query_system_processes` | 系统进程 | 「系统卡吗」 | 只读 |
| `query_version` | 版本号 | 「什么版本」 | 只读 |
| `query_backup_items` | 备份项 | 「备份了什么」 | 只读 |
| `clear_meta_cache` | 清理元数据缓存 | 「清缓存」 | 写操作（非危险） |
| `restart_service` | **重启服务** | 「重启一下」 | ⚠️ **危险**，会中断服务 |
| `update_system` | **升级系统** | 「升级」 | ⚠️ **危险**，会重启 |

#### 组 8：使用帮助（2，meta）

| 工具 | 用途 | 触发意图 | 边界 |
|---|---|---|---|
| `list_capabilities` | 发能力清单 | 「你能做什么」「帮助」「菜单」 | meta，不操作 |
| `ask_user` | **向用户追问** | 缺关键参数时 | `hidden`，不在菜单显示 |

### 1.3 边界界定的四个维度

每个功能都必须在这四个维度上明确，缺一个就会在模糊场景下出错：

| 维度 | 要回答的问题 | 实例 |
|---|---|---|
| **权限** | 谁能触发？ | 斜杠命令仅管理员；自然语言所有白名单用户 |
| **副作用** | 只读还是写入？ | `query_*` 只读；`add_subscribe` 写入；`restart_service` 破坏性 |
| **可逆性** | 能否撤销？ | 启动任务可停止；清空历史**不可恢复** |
| **前置条件** | 需要什么先决状态？ | 查下载需下载器已配；订阅需 TMDB 可达 |

**现有实现已具备的基础（可直接复用，不必重造）**：

- `kind` 字段已区分 `read`(26) / `write`(14) / `ops`(6) / `meta`(2) —— **副作用维度已编码**
- `dangerous` 字段已标记 7 个高危工具 —— **可逆性维度已编码**
- 非 `read` 且非 `meta` 的操作**自动写审计日志**（`__audit`）
- `get_required(name)` 提供必填参数 —— **前置条件维度已编码**
- `ask` 字段（**16 处**）承载缺参追问话术

**危险工具完整清单（7 个）**：

```
torrent_delete         删除下载任务（连同已下载文件）
auto_remove_torrents   立刻执行自动删种（可能删文件）
truncate_history       清空某类历史记录（不可恢复）
delete_rss_history     删除单条订阅下载历史
truncate_unknown       清空未识别文件记录（不可恢复）
restart_service        重启 NAStool 服务
update_system          升级 NAStool 到最新版
```

---

## 第二步：设计 AI 意图判定与功能映射方法

### 2.1 总体架构：AI 为路由中枢，关键词为兜底

```
用户输入
   │
   ├─ 数字 1~9 ────────────────────────► 候选选择（确定性，不问 AI）
   │
   ├─ 「/」开头 ───────────────────────► 斜杠命令（确定性，管理员校验）
   │
   └─ 其它文本
        │
        ├─ 待追问中（has_pending）─────► 直接交给 AI 合并参数
        │
        ├─ AI 可用 ─────────────────────► AI Agent 路由（主导）
        │                                   │
        │                                   ├─ 意图清晰 → 调工具
        │                                   ├─ 缺参数   → ask_user 追问
        │                                   ├─ 无法匹配 → list_capabilities
        │                                   └─ 与系统无关 → 直接回答
        │
        └─ AI 不可用 / 失败 ───────────► 本地关键词路径（兜底，保底可用）
```

**设计原则：三条确定性路径保留在 AI 之前**（数字选择、斜杠命令、待追问），
因为它们的语义**无歧义**，交给模型反而增加延迟和不确定性。

**AI 是增强不是必需**：AI 挂掉时自动回退本地路径（`is_error_answer` 判定），
订阅/搜索不受影响。这条底线必须保住。

### 2.2 意图判定方法：四层判定

```
第 1 层  显式意图（调用方指定）
         工具内部调用 search_media 时直接传 intent，跳过路由推断。
         代码在路由之后赋值（`if intent: SEARCH_MEDIA_TYPE[user_id] = intent`），
         避免被路由分支覆盖。

第 2 层  结构判定（正则 / 类型，零成本）
         isdigit() → 选择项；startswith("http") → 链接。
         这层不用 AI，因为格式本身已无歧义。

第 3 层  流程判定（会话状态）
         has_pending(user_id) → 用户正在回答追问，直接交给 AI。
         优先级最高，否则追问会被关键词分支截断。

第 4 层  语义判定（AI Agent）★ 本次改造的重点
         由模型读取用户输入 + 工具表，自行决定调用哪个工具。
```

**第 4 层的可靠性来源**（不靠模型「猜」，靠工程约束）：

| 约束 | 实现 |
|---|---|
| 工具说明写「什么时候用」 | `desc` 字段，**这是模型选对工具的唯一依据** |
| 参数结构强约束 | `params` 用 JSON Schema，含 `required` |
| 缺参强制追问 | `ask` 字段 16 处；追问话术由工具表生成，不绕模型 |
| 编造数据被禁止 | 提示词第 1 条：必须调工具拿真实数据 |
| 危险操作自动确认 | 提示词第 4 条：直接调工具，系统会代为请求确认 |
| 一次一个工具 | 提示词第 6 条：防乱序并行调用 |

### 2.3 模糊表达的界定与处理

**模糊的四种类型，各有对应策略：**

| 类型 | 表现 | 判定信号 | 处理 |
|---|---|---|---|
| **参数缺失** | 「订阅」没说订阅啥 | `required` 参数未填 | `ask_user` 只问最关键一项，可给 2~3 候选 |
| **指代不明** | 「删掉那个」 | 出现「这个/那个/它」且无上文对象 | 追问具体对象，不漏出候选列表（防误操作） |
| **粒度模糊** | 「清理一下」没说清什么 | 动词泛化 + 无宾语 | 有 `ask` 的工具优先；无 `ask` 则先查可用范围再问 |
| **意图冲突** | 「把订阅删了重新搜」 | 句中含多个动作动词 | 见 2.4 多意图 |

**判定要点（写进提示词的硬规则）**：

1. **缺什么问什么，一次只问一项** —— 提示词第 8 条明确：
   「调用 ask_user 只问最关键的一项，可以给两三个候选；不要一次抛出一堆问题」
2. **已问过的不重复问** —— 第 10 条：「用户补充信息后，把信息合并进参数直接执行」
3. **宁问不猜（危险操作）** —— 删除、清空、重启类操作，
   对象不明确时**必须**追问；宁可多一轮对话，不能猜错执行
4. **能猜就猜（只读操作）** —— 查询类可先按最可能的解释执行，结果不对用户会纠正。
   低成本操作不值得多一轮往返

> **这条「双向不对称」是核心权衡**：
> 查询猜错的代价 = 一次无效回复；删除猜错的代价 = 数据永久丢失。
> 所以模糊度容忍度**按副作用分级**，而不是统一标准。

### 2.4 多意图的处理

**三种多意图形态，策略不同：**

| 形态 | 例子 | 策略 |
|---|---|---|
| **并列多意图** | 「看看下载进度，顺便查下站点数据」 | 分轮执行：先执行第一个，结果回来后再执行第二个 |
| **依赖多意图** | 「找到沙丘然后订阅它」 | 串行链：`search_media` → 用户确认 → `add_subscribe` |
| **冲突多意图** | 「删掉订阅但保留历史」 | **必须澄清**：「订阅删除后，历史记录会一并清理，是否继续？」 |

**处理原则**：

- 提示词第 6 条限定「一次回复最多调用一个工具」，但第 6 条同时说明
  「拿到结果后若还需要其它信息，可以继续调用」—— 即**允许同一轮内串行多工具**，
  只是不允许并行乱序
- 有依赖关系时必须串行：`add_subscribe` 前置 `query_media_info`
  （提示词第 3 条：「不确定片名对应哪部作品时，先用 query_media_info 确认」）
- 冲突意图不进执行，转澄清

### 2.5 无法匹配时的处理

**四道防线，逐级降级：**

| 层级 | 场景 | 处理 |
|---|---|---|
| **1. 判定为闲聊** | 「今天天气不错」 | 直接回答，不调工具（提示词第 11 条） |
| **2. 判定为求助** | 「你能做什么」「帮助」「菜单」 | 调 `list_capabilities` 发能力清单并邀请说需求（第 9 条） |
| **3. 完全无法判断** | 一句无意义的话 | 同样调 `list_capabilities`（第 9 条把「无法判断意图的话」归入此类） |
| **4. AI 本身故障** | 网络失败 / Key 失效 / 返回异常 | **回退本地关键词路径**，并回一句「AI 助手暂时不可用…已切换为本地模式」 |

**第 4 层的实现细节**（已落地）：

```python
_ANSWER_ERROR_PREFIX = (
    "ChatGPT网络连接失败",
    "没有接收到ChatGPT的返回消息",
    "请求ChatGPT出现错误",
    "请求被ChatGPT拒绝了",
)
```

路由据此判断「这次不是 AI 不会答，而是 AI 根本没答上」→ 回退。
**改文案不需要动路由代码，反向也成立** —— 这个解耦设计要保留。

另有 `_looks_like_tool_call` 守最后一道口子：模型输出解析失败时，
若仍长得像工具调用，**绝不能当作用户答复发出去**（用户会收到一串内部 JSON，
而工具一次都没执行）。

### 2.6 改造路线：三阶段

#### 阶段 1：让 AI 成为默认路由（小改动，高收益）

**目标**：把 `agent_first` 的默认值改为 `true`，让所有非确定性文本先过 AI。

- 现状：`agent_first` 默认关闭时，`startswith("搜索"/"下载"/"订阅")` 的文本走本地
- 问题：同一意图（想找片）因措辞不同走两条路径，行为不一致
- 改法：开启后关键词路径仅作 AI 失败兜底
- **风险**：AI 判定错误时用户体验下降。缓解：保留 `agent_first=false` 作为保守选项

#### 阶段 2：补齐语义覆盖（中等改动）

逐项消除第一步诊断出的六个痛点：

| 痛点 | 改法 |
|---|---|
| 前缀同义词缺失 | 由 AI 接管，不再依赖词表 |
| 选择项只认 1~9 | 扩展到任意位数；同时支持「第三个」「选 2」交给 AI 解析 |
| 参数无法剥离 | 由 AI 提取参数，正则仅作兜底 |
| AI/关键词边界割裂 | 见阶段 1 |
| 斜杠命令无同义说法 | **把 10 个斜杠命令注册成 L2 工具的别名**，自然语言也能触发 |
| 交互消息散落 26 处 | 抽象统一编排层（即 `message-issues.md` 的 M5） |

**斜杠命令收敛的具体做法**：在 `AGENT_TOOLS` 表里给对应工具加
`aliases: ["/ptr"]` 字段，路由时先查别名表 → 命中则直接执行工具。
这样斜杠命令从「独立机制」变成「工具的别名」，**一套实现两处受益**。

#### 阶段 3：意图质量可观测（长期）

当前缺失的能力 —— **无法知道 AI 判定得准不准**：

- 记录每次判定的 `(用户输入, 选中工具, 是否成功, 是否追问, 是否澄清)`
- 从中提取高频「判错」样本，反哺 `desc` 与提示词
- 建议指标：**误触发率**（执行了用户没想要的操作）、**漏触发率**（该执行却答了闲话）、
  **追问率**（追问过多说明工具说明不清或参数设计不合理）

> 阶段 3 依赖落库。`docs/message-issues.md` 的 M3（消息中心落库）是其前置，
> 但可以先用轻量日志表实现，不必等完整落库方案。

---

## 三、关键判定要点汇总（可直接作为实施检查表）

### 3.1 路由判定的优先级（顺序不可调换）

```
1. 待追问中      has_pending(user_id)           ← 最高，否则追问被截断
2. 显式意图      intent 参数由调用方指定
3. 结构判定      isdigit() / startswith("http")
4. 斜杠命令      以 "/" 开头 + 管理员校验
5. 语义判定      AI Agent（主导）
6. 关键词兜底    AI 不可用时
```

> ⚠️ **第 1 条的位置是整个路由的关键**。代码注释明确指出：
> 「放在关键词规则之前，否则 AI 的追问会被截断」。
> 用户对追问的回答往往就是「沙丘」这种极短文本，一旦落到关键词分支追问流程就断了。

### 3.2 模糊度的容忍策略（按副作用分级）

| 操作类型 | 模糊容忍度 | 依据 |
|---|---|---|
| 只读查询 | **高** —— 可猜，猜错代价低 | 一次无效回复 |
| 普通写入 | **中** —— 对象明确即可执行 | 可撤销或可重做 |
| 危险操作 | **零** —— 必须澄清 | 数据永久丢失 / 服务中断 |

### 3.3 必守的五条底线

1. **AI 是增强不是必需** —— 故障时必须回退本地路径，订阅/搜索不可因 AI 挂掉而失败
2. **不向用户索要链接** —— 用户说「想看某片」就该去搜资源，不是问用户要磁力链
3. **不编造数据** —— 状态查询必须调工具，禁止模型凭想象生成数字
4. **危险操作零模糊** —— 7 个 `dangerous` 工具，对象不明必须追问
5. **解析失败不外泄** —— 长得像工具调用的输出绝不能当答复发给用户

### 3.4 扩展功能时的最小改动路径

**首选：在 `AGENT_TOOLS` 表里加一条记录。**

由于该表是单一数据源，新增工具后自动获得：
- 能力菜单展示（`get_capability_menu`）
- 模型可见的工具说明（`get_tools_prompt`）
- 参数校验与缺参追问（`get_required` / `ask`）
- 审计分类（`is_action_tool`）
- 危险操作二次确认（`is_dangerous` / `get_danger_prompt`）

**次选：加斜杠命令**（`action.py:276` 的 `_commands` 加一行，`get_commands()` 自动暴露前端）。

**不推荐：加自然语言前缀**（`search_torrents.py:263` 的 elif 链）。
⚠️ 该链位于 `_agent_owns` 判断**之后**，`agent_first` 开启时新前缀会被 AI 接管；
若要始终走本地，需把判断提到前面 —— 这是与「让 AI 主导」方向相反的改动。

---

## 四、附：与现有文档的关系

| 文档 | 关系 |
|---|---|
| `docs/message-commands.md` | 描述现状（10 斜杠命令 + 5 类自然语言）。本文是其演进方向 |
| `docs/message-issues.md` | M5（交互消息散落 21 处）是本文阶段 2 的一项；M3（落库）是阶段 3 的前置 |
| `app/helper/agent_tools.py` | 单一数据源，本文第一步的清单全部由它派生 |
| `app/helper/openai_helper.py` | 提示词与判定逻辑所在，本文第二步的实施位置 |

---

## 五、待修改项清单（2026-09-19 代码实测）

> 以下每一项都经源码核实，标注了文件行号与改动量级。
> 按「必改 / 建议改 / 可选」三档排序。

### 5.1 必改（功能缺口，现有能力无法达成）

#### ① 补 `run_transfer` 工具 —— 唯一的功能缺口

**问题**：`/ptt`「下载文件转移」能用斜杠命令触发，但**无法用自然语言触发**。

**核实**：
- 实现存在：`app/downloader/downloader.py:538` → `def transfer(self, downloader_id=None)`
- `web/action.py` 的 `_commands` 里有 `/ptt` 指向它
- 但 `AGENT_TOOLS` 里**没有**任何执行转移的工具 —— 只有 `query_transfer_history`（查询）

**改法**：两处改动。

**改动 1**：在 `AGENT_TOOLS` 表的「媒体库」组加一条：

```python
{
    "name": "run_transfer",
    "action": None,                      # 走 _dispatch 的专用分支（见改动 2）
    "group": "媒体库",
    "label": "立刻执行一次下载文件转移（把下载完的文件整理入库）",
    "sample": "把下载好的整理一下",
    "desc": "触发一次下载文件转移，把下载器中已完成的任务按规则转移到媒体库目录。"
            "当用户说「整理一下」「转移一下」「把下载好的入库」时使用。",
    "params": {"type": "object", "properties": {}},   # 无必填参数
    "kind": "ops",                       # 会改动文件系统，记审计
    "dangerous": False,                  # 只增不删，非破坏性
}
```

**改动 2**（关键，易漏）：`_dispatch` 里必须加专用分支。

`_dispatch` 的结构是「**几个特殊工具逐个 if，其余统一走 `_call_action(tool["action"], args)`**」：

```python
def _dispatch(self, tool, args, context):
    name = tool["name"]
    if name == "list_capabilities": return self.get_capability_menu()
    if name == "ask_user":          return self._build_ask_payload(args)
    if name == "truncate_history":  ...          # 组合型
    if name == "search_media":      ...          # 搜索类
    return self._call_action(tool["action"], args)   # ← action=None 会在这里炸
```

所以新增 `run_transfer` 时必须插入：

```python
    # 转移类：直接调下载器的 transfer
    if name == "run_transfer":
        return self._call_action("pttransfer", args)
```

⚠️ **注意命令名是 `pttransfer`，不是 `transfer`**（已核实 `web/action.py:546`）：

```python
"pttransfer": Downloader().transfer,
```

两张表要分清：
- `_commands`（斜杠命令表，`action.py:276`）：键是 `/ptt`，值是 `{"func": Downloader().transfer, ...}`
- `_actions`（Web 动作表）：键是 **`pttransfer`** —— 这才是 `_call_action` 要用的名字

> **教训**：`action: None` 并非"自动走 handler"，
> 而是"必须由 `_dispatch` 显式识别"。现有 4 个 `action=None` 的工具
> （`search_media` / `truncate_history` / `list_capabilities` / `ask_user`）
> 在 `_dispatch` 里都各有分支。
> 且 `_call_action` 的入参取自 **`_actions` 表**，与 `_commands` 表的键名不同，
> 不能凭斜杠命令名反推。

**注意**：`kind` 应为 `ops`（移动文件是运维级），但 `dangerous` 应为 `False`
—— 它与 `truncate_*` 不同，**不会丢数据**。这正是 1.1.1 节强调的「两个维度独立」。

#### ② 补 `/ssa`「订阅搜索」的批搜能力

**问题**：`/ssa` 是**全部订阅**批量搜索，而现有 `refresh_subscribe` 是**单条刷新**，语义不等价。

**核实**：
- 实现存在：`app/subscribe.py:677` → `def subscribe_search_all(self)`
- 注册在 `_commands`：`action.py:281` → `"/ssa": {"func": Subscribe().subscribe_search_all, ...}`
- 工具表里只有单条 `refresh_subscribe`（`action: refresh_rss`，`required: ["type"]`）
  —— 它需要 `type`（MOV/TV）甚至 `rssid`，**做不了"全部订阅一键批搜"**

**改法**：加 `search_all_subscribes` 工具（`kind: write`，非危险）。

✅ **好消息**：`_actions` 表里**已有**对应注册（`web/action.py:549`）：

```python
"subscribe_search_all": Subscribe().subscribe_search_all,
```

所以只需**两步**：

1. 在 `AGENT_TOOLS` 加一条记录，`action` 填 `"subscribe_search_all"`
2. 因 `action` 不为 `None`，**无需**改 `_dispatch`（直接走 `_call_action`）

对比 ① 的 `pttransfer`：那个键名与斜杠命令名（`/ptt`）不同，
而这个键名与函数名一致 —— **说明两表键名并无统一规律，必须逐个查证**。

若业务上批搜不常用，也可暂不加，但**应在文档里注明该能力仅斜杠可用**。

### 5.2 建议改（设计问题，会导致误判或体验割裂）

#### ③ `torrent_delete` 的危险性复核（**优先确认**）

**当前状态**：`kind: write` + `dangerous: True`。

**为什么值得复核**：它是唯一一个「非 ops 但危险」的工具。
需要确认二次确认流程（`get_danger_prompt`）**确实覆盖了它** ——
`is_dangerous` 只看 `dangerous` 字段，理论上覆盖；
但 `is_action_tool`（审计判据）看的是 `kind`，`write` 也会记审计，**两条路都通**。

**结论**：当前标注正确，**无需改动**。此项列出是为了记录"已核实"，
避免后续有人误以为它是漏标。

#### ④ `group` 划分标准不统一

见 1.1.1 节。**建议方案 A**：取消「历史与清理」组，
3 个工具按操作对象并入 ── `truncate_history` / `truncate_unknown` → 媒体库，
`delete_rss_history` → 订阅与搜索。

**影响面**：只改 `group` 字段值 + `GROUP_ORDER` 删一项，**不动任何逻辑**。
能力菜单与文本协议提示词自动跟随。

#### ⑤ `agent_first` 默认值

**现状**：默认关闭。关闭时 `startswith("搜索"/"下载"/"订阅")` 的文本走本地关键词，
其余走 AI —— **同一意图因措辞不同走两条路径**。

**改法**：把 `config.yaml:210-222` 的 `agent_first` 默认改为 `true`，
让 AI 成为默认路由，关键词仅作 AI 故障兜底。

**风险与缓解**：AI 判定错误时体验下降。保留该开关作为保守选项，
且 AI 故障仍会回退本地路径（`is_error_answer` 判定，已实现）。

### 5.3 可选（体验优化，非必需）

#### ⑥ 选择项扩展到任意位数

**现状**：`if input_str.isdigit() and int(input_str) < 10:`（`search_torrents.py:196`）
→ 第 10 个候选永远选不中。

**改法**：判断改为与候选数比较（`0 <= choose < len(cache)`），去掉 `< 10` 硬上限。
括号内已有越界检查，改起来安全。

#### ⑦ 斜杠命令收敛为工具别名

**改法**：给对应工具加 `aliases: ["/ptr"]` 字段，路由时先查别名表。
这样斜杠命令从「独立机制」变成「工具的别名」，**一套实现两处受益**。

**影响面**：需改 `web/action.py` 的命令路由 + `agent_tools.py` 加字段。
建议单独一次提交。

#### ⑧ 浏览器通知的同秒盲区

`functions.js:293` 的 `message_time > PageLoadedTime` 因时间戳只到秒而有盲区。
**只影响通知弹窗**，不影响站内列表。属前端表现层，与本次改造无关，单列备查。

### 5.4 已核实无问题的项（不必改）

| 项 | 核实结果 |
|---|---|
| 工具表字段完整性 | 48 条记录**无缺失字段**；仅 `ask_user` 无 `label`/`sample`（因其 `hidden: True`，属预期） |
| `action` 引用有效性 | 44 个有 `action` 的工具**全部**在 `WebAction._actions` 已注册，**无悬空引用** |
| `kind` 值域 | 全部落在 `read/write/ops/meta` 四档内，**无非法值** |
| 危险标记一致性 | 无「`dangerous` 但 `kind: read`」的矛盾；无「`ops` 但非 `dangerous`」的漏标 |
| 审计覆盖 | 非 `read`/`meta` 的工具**全部**会被 `is_action_tool` 判定为需审计 |
| `pttransfer` 命令 | 已在 `_actions` 注册（`action.py:546`），做 ① 时**不必**新增注册 |
| `subscribe_search_all` | 已在 `_actions` 注册（`action.py:549`），做 ② 时**不必**新增注册 |

### 5.5 两张命令表的辨析（动 ① ② 前必读）

`web/action.py` 里有**两张独立的表**，键名**没有统一规律**，极易搞错：

| 表 | 位置 | 键 | 用途 | 命中方式 |
|---|---|---|---|---|
| `_commands` | `action.py:276` | 斜杠命令名（`/ptt`、`/ssa`） | IM 斜杠命令 | `handle_message_job` 按 `/` 前缀查 |
| `_actions` | `action.py` 散落 80~560 行 | 内部命令名 | Web API + 工具调用 | `WebAction().action(cmd, data)` |

**键名对照（实测，规律完全不一致）**：

| 功能 | `_commands` 键 | `_actions` 键 | 工具表 `action` 值 | 三处一致？ |
|---|---|---|---|---|
| 下载文件转移 | `/ptt` | **`pttransfer`** | 无（待加） | — |
| 目录同步 | `/rst` | **`sync`** | `run_directory_sync` | ✘ 三处全不同 |
| 订阅搜索（全部） | `/ssa` | `subscribe_search_all` | 无（待加） | — |
| 自动删种 | `/ptr` | `auto_remove_torrents` | `auto_remove_torrents` | ✘ 部分相同 |
| 清理RSS缓存 | `/trh` | `truncate_rsshistory` | `delete_rss_history` | ✘ 三处全不同 |
| 重新识别 | `/utf` | `unidentification` | `re_identify_unknown` | ✘ 三处全不同 |

**三个具体陷阱**：

1. **`/rst` 目录同步有两个注册项**：
   - `_commands`：`"/rst": {"func": Sync().transfer_sync}`（`action.py:279`）
   - `_actions["sync"]`：`Sync().transfer_sync`（`action.py:547`）
   - `_actions["run_directory_sync"]`：`self.__run_directory_sync`（`action.py:246`）← **另一个方法**

   工具表用的是 `"action": "run_directory_sync"`（`agent_tools.py:626`），指向 `__run_directory_sync`
   —— **不是** `sync`。两个注册项都能触发同步，但入口不同，**用哪个要以工具表实际注册为准**。

2. **`/trh` 与工具名对不上，且语义不同**：
   - `_commands`：`/trh` → `self.truncate_rsshistory`（`action.py:283`）→ **清空全部** RSS 历史
   - `_actions["truncate_rsshistory"]`：`action.py:128`
   - 工具表：`delete_rss_history` → `_actions["delete_rss_history"]` → `__delete_rss_history`（`action.py:185`）
     → **删单条**（需 `rssid`）

   **这是两个不同粒度的功能，不是同一个的两个名字**。工具表只覆盖了「删单条」，
   「清空全部 RSS 历史」**仅斜杠可用** —— 与 ① ② 类似的能力缺口。

3. **`pttransfer` / `sync` 这类「表外名」**：`_actions` 里有一批
   不来自任何斜杠命令的注册项（`action.py:540-560` 一带），专供 Web/工具调用。

**结论**：给工具表填 `action` 字段时，**必须去 `_actions` 表里逐个查证**，
不能凭斜杠命令名、也不能凭函数名推测。查法：

```bash
# 先找到功能底层的实现类/函数名，再在 action.py 里反查它的注册键
grep -n "你的函数名\|你的命令名" web/action.py
```

### 5.6 待确认项（需业务判断，不能靠代码推断）

| 项 | 疑问 | 需确认什么 |
|---|---|---|
| `/trh` vs `delete_rss_history` | 一个是清空全部 RSS 历史，一个是删单条 | 「清空全部」是否也要做成工具？当前工具表**只有删单条** |
| `run_directory_sync` vs `sync` | 两个注册项都能触发目录同步（`action.py:246` / `:547`） | 工具表选了 `run_directory_sync`，是否有意为之？还是应该用 `sync`？ |

### 5.7 斜杠命令的能力缺口汇总

按 5.1 与 5.5 的核实，**10 个斜杠命令中有 3 个无法用自然语言触发**：

| 斜杠命令 | 功能 | 缺口性质 |
|---|---|---|
| `/ptt` | 下载文件转移 | 工具表**完全没有**执行类工具（只有查询） |
| `/ssa` | 订阅搜索（全部） | 工具表只有**单条** `refresh_subscribe`，无批搜 |
| `/trh` | 清空全部 RSS 历史 | 工具表只有**删单条** `delete_rss_history`，无清空全部 |

其余 7 个（`/ptr` `/rst` `/rss` `/tbl` `/utf` `/udt` `/sta`）在工具表里都有对应工具，
只是**名字不同，无法用自然语言直达** —— 这正是阶段 2「斜杠命令收敛为工具别名」要解决的。

**一句话**：斜杠命令与自然语言目前是**两套并行且部分不重叠的能力面**，
收敛后应做到「斜杠命令能做到的，自然语言也能做到」。
