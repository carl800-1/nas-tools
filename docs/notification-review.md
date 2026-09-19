# 通知系统梳理与规范化建议

> 基于 v4.5.5 代码实测，非推测。所有结论均标注文件与行号。

## 一、现状盘点

### 1.1 架构分层

```
业务代码 (rss / brushtask / sites / downloader / web/backend ...)
        │  直接调 send_xxx_message()
        ▼
   Message (app/message/message.py, 844 行)      ← 消息中枢，20 个 send_* 入口
        │  ① 插入 MessageCenter  ② 遍历命中开关的渠道 ③ 调 __sendmsg
        ▼
   MessageCenter (message_center.py, 50 行)      ← 站内消息，内存 deque(maxlen=50)
        │
        ▼
   渠道客户端 × 14 (app/message/client/*.py)      ← 各自实现协议
        │
        ▼
   外部服务 (Telegram / 飞书 / 企业微信 / Bark / ntfy / Slack ...)
```

**渠道清单（14 个）**：telegram、feishu、wechat、slack、webhook、synologychat、
pushplus、ntfy、gotify、bark、chanify、pushdeer、iyuu、serverchan

### 1.2 消息开关

14 个开关定义在 `app/conf/moduleconf.py:575-620`，每个渠道独立勾选：

| 开关 ID | 显示名 | 开关 ID | 显示名 |
|---|---|---|---|
| `download_start` | 新增下载 | `site_signin` | 站点签到 |
| `download_fail` | 下载失败 | `site_message` | 站点消息 |
| `transfer_finished` | 入库完成 | `brushtask_added` | 刷流下种 |
| `transfer_fail` | 入库失败 | `brushtask_remove` | 刷流删种 |
| `rss_added` | 新增订阅 | `auto_remove_torrents` | 自动删种 |
| `rss_finished` | 订阅完成 | `ptrefresh_date_message` | 数据统计 |
| `mediaserver_message` | 媒体服务 | `custom_message` | 插件消息 |

### 1.3 做得好的地方（应当保留）

1. **抽象层干净**：`_IMessageClient` 只定义 `match` / `send_msg` / `send_list_msg` 三个方法，
   14 个渠道统一实现，新增渠道成本低（`_base.py`，35 行）。
2. **渠道自动发现**：`SubmoduleHelper.import_submodules` + `hasattr(obj, 'schema')`，
   丢一个文件进目录即生效，不需要注册。
3. **消息中心一致性 100%**：20 个 `send_*` 入口全部调用了 `insert_system_message`，
   无遗漏（已逐一核对）。
4. **分段发送**：`__sendmsg` 按渠道 `max_length` 自动切分长文本（如企业微信 2048）。
5. **URL 域补全**：统一把相对路径拼成 `_domain` 开头的完整地址，并区分「唤起 App」与「跳转页面」。

---

## 二、发现的问题（按严重度排序）

### P0-1 代理支持只覆盖 2/14 渠道 —— 实际是功能缺失

实测各渠道 `RequestUtils` 传参情况：

| 渠道 | 是否传 proxies | 渠道 | 是否传 proxies |
|---|---|---|---|
| telegram | ✅ | bark / chanify / gotify | ❌ |
| feishu | ✅ | iyuu / ntfy / pushdeer | ❌ |
| | | pushplus / serverchan / slack | ❌ |
| | | synologychat / wechat / webhook | ❌ |

**影响**：Bark、ntfy、Gotify、Slack、PushDeer、Chanify、ServerChan、IYUU **全部是境外服务**。
在需要代理的网络环境下，这 8 个渠道**完全无法工作**，而用户在「基础设置 → 代理服务器」
里明明填了代理。这与刚修复的 RSS 代理问题是同一类缺陷。

典型代码（`bark.py:47`）：
```python
res = RequestUtils().post_res(sc_url)     # ← 没传 proxies，恒直连
```

### P0-2 交互消息散落 20+ 处，无统一编排

`web/backend/search_torrents.py` 一个文件里就有 **20 处** `send_channel_msg` 调用，
`web/main.py` 另有 6 处。每处都要手动传 `channel` / `title` / `text` / `user_id`，
且 `channel` 是 `SearchType` 枚举，与渠道 `schema` 字符串之间靠隐式约定对应。

**影响**：
- 新增一个渠道要改动多处业务代码，违背了 `_IMessageClient` 抽象层「新增渠道零成本」的初衷；
- 交互消息无法被「消息开关」管辖（开关只在 `send_xxx_message` 里判断，交互链路绕开了它）；
- 渠道不可用时（如未配置），`send_channel_msg` 静默 `return False`，调用方多数不检查返回值。

### P1-1 消息中心不落库，重启即失

`MessageCenter` 用内存 `deque(maxlen=50)`。容器重启（升级镜像、改配置触发重启）后
**所有站内消息历史全部丢失**。而 `send_xxx_message` 的数据来源其实都有数据库记录
（下载历史、RSS 历史、转移历史），说明是有能力落库的。

另：`get_system_messages(lst_time=...)` 用 `.seconds` 比较时间差（`message_center.py:46`），
`.seconds` 只取「时/分/秒」部分，跨天时差值会算错 —— 应当用 `.total_seconds()`。

### P1-2 无重试、无去重、无失败告警

三个「无」：
- **无重试**：`__sendmsg` 发送失败即 `return state` 并中断后续分段（`message.py:147-149`）。
  长消息分 5 段，第 3 段失败 → 第 4、5 段永远收不到，且用户不知道自己少收了两段。
- **无去重**：同一事件重复触发时会重复发送。例如 RSS 每小时扫描，若同一资源反复命中，
  会持续性轰炸（`rss_added` 有 `is_rssd_by_enclosure` 兜底，但其他事件没有）。
- **无失败告警**：所有渠道都发送失败时，系统照常运行，用户毫不知情。
  `MessageCenter` 里也没有记录发送失败状态。

### P1-3 渠道配置与消息内容耦合，模板能力不均

- 14 个渠道里只有 `webhook` 支持 Jinja2 模板（`moduleconf.py` 的 `json_tpl` / `json_list_tpl`）；
- 其余渠道的消息文案**硬编码在 `message.py` 的 20 个方法里**，用户无法自定义格式；
- 同一个 `send_xxx_message` 内大量手工拼字符串，例如
  `send_download_message` 有 12 处 `msg_text = f"{msg_text}\n..."`，
  增删字段要小心处理换行与分隔符。

### P2-1 消息级别缺失

没有 info / warn / error 分级。用户无法表达「下载成功不用告诉我，但失败必须告诉我」
这类诉求，只能靠 14 个开关的粒度，而开关是「按事件」不是「按级别」。
`ptrefresh_date_message`（数据统计）与 `download_fail`（下载失败）在用户感知上
完全不是一个重要级别的消息，却用同一套机制。

### P2-2 AI 助手完全未接入通知系统

`app/helper/agent_tools.py` 的 51 个工具里，**没有任何一个涉及消息通知**。
AI 能帮用户搜索、订阅、管理下载，但不能：
- 查询「最近有什么通知」；
- 让用户说「以后下载失败直接发飞书」；
- 在排查问题时主动推送结论。

`message.py` 也没有为 agent 提供任何批量/摘要接口。

---

## 三、规范化建议

### 3.1 建议分四步走，每步独立可发版

#### 第 1 步：补齐代理支持（P0-1，建议立即做）

统一在 `_IMessageClient` 基类里收敛代理获取：

```python
# app/message/client/_base.py
class _IMessageClient(metaclass=ABCMeta):
    def _get_proxies(self):
        """渠道统一代理获取，子类无需各自处理"""
        return Config().get_proxies()
```

然后 12 个渠道把 `RequestUtils()` 改为
`RequestUtils(proxies=self._get_proxies())`。

**注意**：像 `bark`/`ntfy` 这类自建服务可能部署在**内网**，走代理反而会失败。
建议在渠道配置里加一个「使用代理」开关（`type: switch`），默认关闭，
与站点维护里 `proxy` 字段的设计保持一致 —— 这样既补上能力，又不会误伤内网部署。

#### 第 2 步：消息中心落库（P1-1）

新增表 `SYSTEM_MESSAGE`（标题、内容、级别、事件类型、创建时间），
`MessageCenter` 改为写库 + 内存缓存双层。收益：
- 重启不丢消息；
- 顺带修掉 `.seconds` 的时间计算 bug；
- 为后续「通知历史检索」「AI 查询通知」提供数据基础。

#### 第 3 步：发送链路加固（P1-2）

```
__sendmsg 改造：
  ① 分段发送改为「逐段独立」，某段失败不再中断后续
  ② 每段失败后重试 N 次（指数退避，N 可配，默认 2）
  ③ 全部失败时记录到 MessageCenter（级别=error），前端可查
  ④ 同一 channel + 同一事件 + 相同内容在 T 秒内只发一次（去重窗口可配）
```

第 ④ 点的去重键建议用 `hash(channel + event + title + text)`，
窗口默认 300 秒，避免 RSS 类高频事件轰炸。

#### 第 4 步：引入消息级别 + 模板化（P1-3 / P2-1）

**级别**（4 级，简单够用）：

| 级别 | 语义 | 典型事件 |
|---|---|---|
| `debug` | 调试，默认不发 | 处理细节 |
| `info` | 正常流程 | 新增下载、入库完成 |
| `warn` | 需要留意但非错误 | 站点签到异常、RSS 未获取到数据 |
| `error` | 必须知道 | 下载失败、入库失败、渠道发送失败 |

渠道配置里增加「最低级别」下拉，用户就能表达「只要 error」。
这比继续堆开关更符合直觉。

**模板**：把 `webhook` 已有的 Jinja2 能力提升为**所有渠道通用**。
每个事件定义一组标准变量（如 `download_start` 提供 `title`/`site`/`size`/`quality`/`seeders`），
用户在渠道配置里可选「默认模板」或「自定义模板」。
收益是把 `message.py` 里 12 处手工拼字符串收敛成模板渲染，代码量大幅下降。

#### 3.2 AI 结合点（P2-2）

AI 助手已有 51 个工具、双协议（原生 function calling + 提示词文本协议）的成熟底座，
接入通知系统是自然的延伸。建议加一批工具，分三类：

**A. 查询类**（低风险，`is_dangerous=False`）
- `get_recent_notifications(limit, level, event_type)` —— 查最近通知，
  回答「我错过了什么重要通知」
- `get_notification_stats(days)` —— 统计各渠道发送成功率，
  回答「飞书这几天发消息是不是有问题」→ 这正好能自动发现 P1-2 的静默失败

**B. 配置类**（中风险，建议 `ask` 字段确认）
- `list_notification_channels()` —— 列出已配置渠道及其开关
- `update_channel_switch(channel, event, enabled)` —— 「以后下载失败直接发飞书」
- `test_notification_channel(channel)` —— 触发测试消息

**C. 摘要类**（高价值，体现 AI 相对规则系统的优势）
- `summarize_notifications(period)` —— 把「昨天 37 条通知」压缩成
  「昨天入库 3 部电影，2 个站点签到失败，1 个订阅完成」
- 建议做成**定时摘要**：每天/每周把通知汇总成一条，替代逐条推送，
  从根本上缓解通知过多的问题

**接入要点**：
- 工具实现应走 `Message()` 的现有入口，不要绕过消息中心另开一套；
- 配置类工具必须复用 `agent_confirm_dangerous` 机制（改配置属于影响面较大的操作）；
- 查询类工具依赖第 2 步的落库，否则只能查内存里最后 50 条。

### 3.3 前端交互改进（建议随第 4 步一起）

当前 14 个开关平铺在通知设置页，用户需要「每个渠道 × 逐个勾选」。
建议：
- 按业务域分组（下载 / 订阅 / 站点 / 刷流 / 系统），减少认知负担；
- 增加「一键模板」：如「只要重要通知」= 自动勾选 `*_fail` + `site_message`，级别设 `warn`；
- 每个渠道旁显示**最近一次发送结果**（成功/失败/时间），让 P1-2 的静默失败可见。

---

## 四、优先级与建议排期

| 优先级 | 事项 | 理由 | 影响面 |
|---|---|---|---|
| **P0** | 补齐 12 渠道代理支持 | 境外渠道在当前网络下完全不可用 | 12 文件，改动机械 |
| **P0** | 交互消息统一编排 | 新增渠道要改多处业务代码 | 需设计，改动中等 |
| **P1** | 消息中心落库 | 重启丢消息 + 时间计算 bug | 需迁移脚本 |
| **P1** | 分段失败不中断 + 重试 + 去重 | 静默丢消息，用户不知情 | 集中在 `__sendmsg` |
| **P2** | 消息级别 + 模板化 | 用户无法表达优先级诉求 | 较大，建议单独发版 |
| **P2** | AI 通知工具 + 定时摘要 | 高价值，但依赖 P1 落库 | 增量添加，风险可控 |

**建议节奏**：
1. 先发 **P0 两项**（补齐代理 + 交互编排）—— 都是修「本该能用但没生效」的功能，风险低；
2. 再发 **P1 两项**（落库 + 发送加固）—— 一次迁移，一起验证；
3. 最后做 **P2**（级别/模板 + AI）—— 这对用户体验改变最大，作为一个大版本。

---

## 五、附：本次梳理用的核对方法

结论均可复现，命令如下：

```bash
# 渠道清单与规模
find app/message -name "*.py" | xargs wc -l | sort -n

# 各渠道代理传参情况
for f in app/message/client/*.py; do
  echo "$(basename $f): RequestUtils=$(grep -c 'RequestUtils(' $f), proxies=$(grep -c 'proxies=' $f)"
done

# 开关清单
grep -n '"switch"' -A 60 app/conf/moduleconf.py

# 交互消息散落情况
grep -rn "send_channel_msg" --include=*.py app/ web/ | grep -v "def send_channel"

# 消息中心一致性
grep -c "def send_" app/message/message.py          # 20
grep -c "insert_system_message" app/message/message.py  # 20
```
