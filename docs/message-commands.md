# 消息指令体系：发什么、怎么回

> 基于 v4.5.5 代码实测。飞书与其他渠道（TG / 微信 / Synology）**共用同一套指令解析**，
> 差异只在接收方式。本文以飞书为主，标注所有渠道的通用行为。

---

## 一、消息进来的完整链路

```
飞书长连接收到消息
   │  app/message/client/feishu.py:437  __on_message_receive
   │  · 3 秒内必须返回（超时飞书会重推 → 命令执行两次）
   │  · 只解析+转发，耗时操作丢后台线程
   ▼
POST http://127.0.0.1:<port>/feishu?apikey=xxx
   │  feishu.py:478  __forward_message
   ▼
web/main.py:1440  feishu()
   │  · 校验 IP（SecurityHelper.check_feishu_ip）
   │  · 按「/ 开头」分流做权限校验（详见第三节）
   ▼
web/action.py:394  handle_message_job(msg, in_from, user_id, user_name)
   │  ① 触发 MessageIncoming 事件
   │  ② 查内置命令表 _commands  → 命中则启动服务并回「正在运行 xxx ...」
   │  ③ 查插件命令               → 命中则发事件并回「正在运行 xxx ...」
   │  ④ 都没命中 → 交给 search_media_by_message（搜索/订阅/AI 聊天）
   ▼
web/backend/search_torrents.py:194  search_media_by_message
```

---

## 二、指令全清单

### 2.1 斜杠命令（仅管理员，`web/action.py:276-287`）

| 指令 | 功能 | 回复 |
|---|---|---|
| `/ptr` | 自动删种 | `正在运行 自动删种 ...` |
| `/ptt` | 下载文件转移 | `正在运行 下载文件转移 ...` |
| `/rst` | 目录同步 | `正在运行 目录同步 ...` |
| `/rss` | 电影/电视剧订阅 | `正在运行 电影/电视剧订阅 ...` |
| `/ssa` | 订阅搜索 | `正在运行 订阅搜索 ...` |
| `/tbl` | 清理转移缓存 | `正在运行 清理转移缓存 ...` |
| `/trh` | 清理RSS缓存 | `正在运行 清理RSS缓存 ...` |
| `/utf` | 重新识别 | `正在运行 重新识别 ...` |
| `/udt` | 系统更新 | `正在运行 系统更新 ...` |
| `/sta` | 站点数据统计 | `正在运行 站点数据统计 ...` |

**回复是即时的任务确认**（`action.py:416-417`），不是执行结果。
结果由各任务自己后续推送。

> 插件也可注册斜杠命令，通过 `PluginManager().get_plugin_commands()` 汇入，响应同样是
> `正在运行 <插件命令描述> ...`。

### 2.2 自然语言指令（所有白名单用户，`search_torrents.py:243-280`）

| 你发的 | 识别为 | 行为 |
|---|---|---|
| `搜索 沙丘` | SEARCH | 走 TMDB 识别 → 搜索资源 |
| `下载 沙丘` | SEARCH | **同上**（`下载` 与 `搜索` 同义，都会被剥掉） |
| `沙丘` | SEARCH | 无前缀也当搜索 |
| `订阅 沙丘` / `订阅:沙丘` | SUBSCRIBE | 添加订阅 |
| `http://...` | DOWNLOAD | 当种子/磁力链下载 |
| `1` ~ `9` | 选择项 | 选第 N 个搜索结果 |
| 其他文本 | ASK | 交给 AI 聊天（**仅当 AI 可用**）|

**关键细节**：

- **前缀剥离**：`re.sub(r"(搜索|下载)[:：\s]*", "", input_str)` —— 冒号/空格都可有可无；
  订阅是 `re.sub(r"订阅[:：\s]*", "", input_str)`。
- **数字选择**：`input_str.isdigit() and int(input_str) < 10`（`search_torrents.py:196`），
  **只支持 1~9**，不支持 0 或两位数。
  选择后按上一步的意图分流：SEARCH → 搜索，SUBSCRIBE → 订阅。

### 2.3 AI 接管的边界（`search_torrents.py:244-286`）

这是最容易踩坑的地方。判定顺序**很重要**：

```python
_ai_available = (not ai_disabled) and not intent and OpenAiHelper().is_agent_available()
_agent_owns = _ai_available and (
        OpenAiHelper().has_pending(user_id)                          # ① 追问流程中
        or bool(config.openai.agent_first))                          # ② 全局开关
```

| 条件 | 结果 |
|---|---|
| 用户正处在 AI 追问流程中（`has_pending`） | **直接给 AI**，跳过所有关键词判断 |
| `agent_first: true` | **所有文本都给 AI**，AI 用工具完成搜索订阅 |
| 关闭 `agent_first`，且文本不以搜索/下载/订阅/http 开头 | 给 AI 聊天 |
| 关闭 `agent_first`，文本以 `搜索`/`下载`/`订阅`/`http` 开头 | 走本地关键词路径 |

**注意 `agent_first` 开启后的副作用**：`搜索 沙丘` 也会先进 AI 再经工具搜索，
链路变长但结果一致；而 AI 故障时会自动回退本地（见下）。

**AI 不可用时的回退**（`search_torrents.py:327-344`）：

```
AI 助手暂时不可用：<原因>
已切换为本地模式处理，可直接发送「搜索 片名」或片名，也支持「订阅 片名」与磁力链/种子链接。
```

然后**自动用本地路径重跑一次**（`ai_disabled=True`），所以订阅/搜索不会因 AI 故障而失败。
这个设计很关键 —— AI 只是增强，不是必需。

---

## 三、权限模型

飞书渠道配置里有两个 ID 列表（`feishu.py:128-138`）：

| 配置项 | 控制 |
|---|---|
| **用户 Open ID** | 白名单，可用普通指令（搜索/订阅/聊天） |
| **管理员 Open ID** | 可用斜杠命令 `/xxx` |

判定在 `web/main.py:1462-1479`：**按消息是否以 `/` 开头区分**。

被拒时的回复（**会带上你的 open_id，方便管理员添加**）：

```
只有管理员才有权限执行此命令
你的用户ID（open_id）：ou_xxxxxxxx
如需使用管理命令，请让管理员把该ID加入飞书渠道配置的「管理员 Open ID」
```

```
你不在用户白名单中，无法使用此机器人
你的用户ID（open_id）：ou_xxxxxxxx
请让管理员把该ID加入飞书渠道配置的「用户 Open ID」或「管理员 Open ID」
```

> 这是很贴心的设计 —— 新用户被拦时能自己抄下 ID 找管理员。

---

## 四、回复格式与分类

### 4.1 交互回复（`send_channel_msg`，只回发给发起人）

| 场景 | 回复 |
|---|---|
| 开始搜索 | `开始搜索 <片名> ...` |
| 搜索有结果 | `<片名> 共搜索到<N>个资源，点击选择下载` + 列表卡片 |
| 搜索无结果 | `<片名> 未搜索到任何资源` |
| 有结果但没下载成功 | `<片名> 共搜索到<N>个结果，但没有下载到任何资源` |
| 无法识别输入 | `无法识别搜索内容！` |
| TMDB 查不到 | `<片名> 查询不到媒体信息！` |
| 输入非法数字 | `输入有误！` |
| 订阅失败 | `<片名> 添加订阅失败：<原因>` |
| 前导确认 | `<片名> 识别为 <标题> (<评分>)` |

- `title=""` 表示无标题，正文直接承载内容；系统会自动补全 URL 域名。
- 列表卡片走 `send_list_msg`，飞书是交互式卡片（`feishu.py:234`），
  用户点选后再回发数字。

### 4.2 通知类（`send_xxx_message`，按开关群发所有渠道）

与交互回复是**两套完全独立的通路**：

| 通路 | 方法 | 目标 | 受消息开关管辖 |
|---|---|---|---|
| 交互回复 | `send_channel_msg` | 只回发问的人 | ❌ 不受 |
| 事件通知 | `send_xxx_message` | 所有勾选了该开关的渠道 | ✅ 受 |

---

## 五、常见问题与坑

### 5.1 指令发了没反应，怎么排查

按链路逐段看日志：

```bash
# ① 飞书是否收到（含 open_id / chat_id）
grep "收到消息：open_id" 日志

# ② 是否被 IP 校验拦下
grep "非法飞书消息" 日志

# ③ 是否被权限拦下
grep "只有管理员才有权限\|不在用户白名单" 日志

# ④ 是否进到搜索
grep "收到飞书消息" 日志
```

`__forward_message` 第一行就会打印 `open_id` 和 `chat_id`（`feishu.py:483`），
**这是排查权限问题最有用的一行日志**。

### 5.2 已知坑

| 坑 | 说明 |
|---|---|
| 命令执行两次 | 飞书要求事件 3 秒内返回，超时会重推。代码已用后台线程规避，但若本地接口卡住仍可能重复 |
| `下载` 和 `搜索` 等价 | 两个前缀都会被剥掉且识别为 SEARCH，想「只下载不搜索」做不到 |
| 数字只到 9 | `int(input_str) < 10`，第 10 个及以后的结果无法用数字选 |
| 群聊要 @ 机器人 | 解析时用 `re.sub(r"@_user_\d+", "", text)` 剔除占位符；@ 后跟内容才有效 |
| 非文本消息 | 图片/文件等直接忽略并记日志（`feishu.py:466`） |
| `agent_first` 会改变链路 | 开启后连 `搜索 沙丘` 也先过 AI，排查时容易困惑 |

### 5.3 交互回复不进消息中心？

`send_channel_msg` 里 `channel == SearchType.WEB` 才插入消息中心
（`message.py:164-166`）。**飞书/TG 等渠道的交互回复不进站内消息中心** ——
这是有意设计（交互是点对点对话，不是系统事件），但意味着网页端看不到你和机器人的对话记录。

---

## 六、想扩展指令怎么办

### 6.1 加一个斜杠命令（改 1 处）

```python
# web/action.py:276  _commands 里加一行
"/mycmd": {"func": MyService().my_action, "desc": "我的功能"},
```

`get_commands()`（`action.py:5459`）会自动把它暴露给前端展示，无需另改。

### 6.2 加一个自然语言前缀（改 1 处）

```python
# web/backend/search_torrents.py:263  在 elif 链里插一支
elif input_str.startswith("我的前缀"):
    SEARCH_MEDIA_TYPE[user_id] = "MY_INTENT"
    input_str = re.sub(r"我的前缀[:：\s]*", "", input_str)
```

⚠️ **注意顺序**：这个 `elif` 链在 `_agent_owns` 判断**之后**，
所以 `agent_first` 开启时新增前缀会被 AI 接管。若希望新前缀始终走本地路径，
需要把它提到 `_agent_owns` 判断之前。

### 6.3 让 AI 处理（推荐，不改指令表）

在 `app/helper/agent_tools.py` 加工具即可，AI 会自己决定何时调用。
这是当前架构下**最省事、最不侵入**的扩展方式。
