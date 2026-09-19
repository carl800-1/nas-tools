# 通知系统（message）问题清单与改造方案

> 本文聚焦 `app/message/` 自身的问题，**不含代理相关项**（用户在国内，代理需求少，后续按需再处理）。
> 所有结论均经代码实测复现，标注文件行号。

---

## 一、问题总览

| 编号 | 问题 | 严重度 | 影响 | 修复难度 | 状态 |
|---|---|---|---|---|---|
| M1 | 同秒内多条消息会丢失 | **P0** | 用户看不到消息 | 低 | **已修复** |
| M2 | 跨天时旧消息被当新消息重复推送 | **P0** | 重复提醒 | 低 | **已修复** |
| M3 | 消息中心不落库，重启即丢 | P1 | 历史丢失 | 中 | 待处理 |
| M4 | 分段发送失败即中断，静默丢尾段 | P1 | 长消息不完整 | 低 | **已修复** |
| M5 | 交互消息散落 21 处，绕开开关体系 | P1 | 难维护、开关失效 | 中 | 待处理 |
| M6 | 无失败告警，发送失败用户不知情 | P1 | 静默失效 | 低 | 部分（返回值契约已就绪） |
| M7 | 无消息级别，用户无法表达优先级 | P2 | 体验差 | 中 | 待处理 |
| M8 | 消息文案硬编码，不可自定义 | P2 | 灵活性差 | 中 | 待处理 |
| M9 | AI 助手未接入通知系统 | P2 | 错失能力 | 中 | 待处理 |
| M10 | 首屏 20 条 + 队列 50 条，阈值不一致会漏推 | P2 | 极端场景漏推 | 低 | 待处理 |

---

## 一之二、已修复项的实现与验证（2026-09-19）

### M1 + M2：游标改为自增 seq

改动 5 个文件：

| 文件 | 改动 |
|---|---|
| `app/message/message_center.py` | 新增 `_seq` 自增序号写入消息体；`get_system_messages(lst_time)` → `(lst_seq)`，改用 seq 比较；游标做 `int()` 归一化 |
| `web/action.py:1787` | `get_system_message(lst_time)` → `(lst_seq)`，回包字段改为 `lst_seq` |
| `web/main.py:1928` | 入参读 `lst_seq`，转发给前端的消息体补上 `seq` 字段 |
| `web/static/js/functions.js` | `render_message` / `get_message` 全部改用 `lst_seq`；首屏传 `0` |
| `app/message/message.py` | M4：分段发送不中断（见下） |

**验证**：`.workbuddy/tests/message_center_fix_verify.py`，加载项目**真实源码**，
22 个断言全部通过：

```
[场景 1] 同秒插入 2 条（M1 核心）              ✔
[场景 2] 循环连插 5 条同秒（转移电视剧场景）    ✔ 4 条全拿到，再拉无重复
[场景 3] 跨天旧消息不推送（M2 核心）           ✔ 游标不倒退
[场景 4] 正常间隔                              ✔
[场景 5] 空队列首次拉取                        ✔
[场景 6] 队列超 maxlen=50 后游标仍连续         ✔
[场景 7] 游标类型容错（0 / None / "" / "0"）   ✔
[场景 8] 无新消息时游标不倒退                  ✔
```

另做了端到端 JSON 往返验证（复刻 action + WebSocket 收发逻辑）：

```
首屏            : ['A']            lst_seq = 1
同秒增量 3 条   : ['D','C','B']    lst_seq = 4
无新消息        : []               lst_seq = 4
字符串游标 "4"  : []               lst_seq = 4
缺少游标字段    : ['D','C','B','A'] lst_seq = 4
```

### M4：分段发送不再中断

`app/message/message.py:137-157`：

- 原来某段失败即 `return`，后续分段永远不发；
- 现在分段独立处理，统计 `fail_count`，全部跑完再统一返回；
- 返回值统一为 `(state, error)` 二元组；`send_channel_msg` 解包后仍返回单状态，**调用方契约不变**；
- 渠道路径缺失时由 `return None` 改为 `return False, "消息渠道未配置"`，避免调用方解包崩溃。

**验证**：`.workbuddy/tests/message_segment_fix_verify.py`，19 个断言全部通过，
重点覆盖「第 2 段失败后第 3 段仍发出」：

```
[场景 2] 第 2 段失败（M4 核心）  各段内容 ['T','efgh','ijkl']  ✔ 第 3 段未丢
[场景 7] send_channel_msg 返回值仍为单个状态                        ✔
```

### 附带发现：浏览器通知的 3 秒时间窗（M1 的孪生问题）

`functions.js:293` 保留了一段历史逻辑：

```javascript
let message_time = new Date(msg.time.replace(/-/g, '/'));
if (message_time > PageLoadedTime) { browserNotification(...); }
```

`msg.time` 精度只到**秒**（秒位被截断），所以「页面打开后同一秒内到达」的消息
会被判定为不晚于 `PageLoadedTime`，**不弹浏览器通知**。
站内列表正常，仅浏览器推送受影响，属前端表现层问题，
本次未改（改动涉及浏览器通知取舍，单独评估），记作后续项。


---

## 二、P0 问题详述（实测复现）

### M1 同秒内多条消息会丢失

**位置**：`app/message/message_center.py:44-49`

```python
for message in list(self._message_queue):
    if (datetime.datetime.strptime(message.get("time"), '%Y-%m-%d %H:%M:%S')
            - datetime.datetime.strptime(lst_time, '%Y-%m-%d %H:%M:%S')).seconds > 0:
        ret_messages.append(message)
    else:
        break        # ← 时间相同即 break，同秒的后续消息全部丢弃
```

**触发链**：
1. 时间戳精度只到**秒**（`time.strftime('%Y-%m-%d %H:%M:%S')`，`message_center.py:32`）；
2. `action.py:1791` 把 `lst_time` 推进到最新消息的时间；
3. 前端每 3 秒轮询一次（`functions.js:302`）。

**必然触发场景**（代码里真实存在）：
- `send_transfer_tv_message`（`message.py:314-347`）：**循环体内每部剧插一条消息**，
  一次转移 N 部剧 → 同一秒插入 N 条；
- `send_simplify_transfer_tv_message` 同样结构。

**实测结果**：
```
插入 A(10:00:00) → 前端 lst_time=10:00:00
插入 B(10:00:00) → 查询返回 0 条   ❌ B 永久丢失
```

**修复**（**游标必须用自增序号 seq，不能用时间戳**）：

> ⚠️ 最初考虑「把时间精度提到毫秒」，但**实测证明不够**：
> 同秒插入的 5 条消息共享同一时间戳，前端回传「最新一条的 ts」后，
> 用 `ts > lst_ts` 比较会把同 ts 的其余 4 条全部排除（详见验证结果）。
> 时间戳无法唯一标识一条消息，**必须引入单调递增序号**。

```python
# message_center.py
_seq = 0

def __append_message_queue(self, title, content):
    MessageCenter._seq += 1
    self._message_queue.appendleft({
        "title": title,
        "content": content,
        "seq": MessageCenter._seq,      # 新增：单调递增，唯一标识
        "time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())),
    })

def get_system_messages(self, num=20, lst_seq=None):
    if not lst_seq:
        return list(self._message_queue)[-num:]
    ret_messages = []
    for message in list(self._message_queue):
        if message.get("seq", 0) > lst_seq:
            ret_messages.append(message)
        else:
            break       # 队列按 seq 递减排列，遇到不新的即可停
    return ret_messages
```

**为什么 seq 更好**：
- 唯一性有保证，同秒多条也不会混淆；
- 队列按 seq 递减排列，`break` 的语义正确（遇到更旧的才停）；
- 与将来落库后的**自增主键天然一致**，M3 落库时可无缝过渡；
- 彻底消除 M2 的跨天问题（不再比较时间）。

### M2 跨天时旧消息被当新消息重复推送

**根因**：`.seconds` 只返回「时/分/秒」部分，**丢弃天数且不保留负号**。

实测：
```
昨天 23:59 的消息 - 今天 00:01 的查询 = 负时间段
  .seconds       = 85200   ← 正数！判定为「新消息」
  .total_seconds = -1200   ← 正确值
反向 30 分钟差时：.seconds = 84600（应为 -1800）
```

**影响**：跨天（或任意负时间差）时，旧消息会被判定为新消息 → 重复推送、
重复弹浏览器通知。

**修复**：采用上面的 **seq 游标方案后此问题自然消失**（不再做时间比较）。
若暂时不想改游标，最小修复是 `.seconds` → `.total_seconds()`，
但那只能修 M2、修不了 M1，**建议一次做对**。

---

## 三、P1 问题详述

### M3 消息中心不落库

`MessageCenter` 用内存 `deque(maxlen=50)`（`message_center.py:10`）。

**影响**：
- 容器重启（升级镜像、改配置触发重启）后**站内消息全丢**；
- 上限仅 50 条，高频场景下很快被顶掉；
- 无法做历史检索，也是 M9（AI 查询通知）的前置阻塞。

**数据基础已具备**：`send_xxx_message` 的事件本身都有数据库记录
（下载历史、RSS 历史、转移历史），缺的只是消息表本身。

**方案**：新增 `SYSTEM_MESSAGE` 表（title / content / level / event_type / create_time），
`MessageCenter` 改为「写库 + 内存缓存」双层，保留 `maxlen` 作为热数据窗口。

### M4 分段发送失败即中断

**位置**：`app/message/message.py:137-149`

```python
for txt in texts:
    ...
    state, ret_msg = client.get('client').send_msg(...)
    title = None
    if not state:
        log.error(...)
        return state        # ← 直接返回，后续分段永远不发
```

**影响**：只有 2 个渠道设了 `max_length`（微信 2048、slack 3000，`moduleconf.py:108/328`），
其余走单段。但这 2 个渠道一旦中途失败，**用户收到的是残缺消息且毫不知情**。

**修复**：分段独立处理，记录失败段数，全部结束后统一返回。
失败段可选重试（退避 1s/2s），并把失败写入消息中心。

### M5 交互消息散落 21 处

| 文件 | 调用数 |
|---|---|
| `web/backend/search_torrents.py` | 16 |
| `web/main.py` | 5 |

> 计数口径：`grep -c "send_channel_msg"`，2026-09-19 实测复核。
> 早前记录的「20 + 6 = 26 处」为估算，已按实测修正。

**问题**：
- 交互消息**绕开了消息开关体系**（开关只在 `send_xxx_message` 里判断）；
- 新增渠道要改多处业务代码；
- 多数调用点**不检查返回值**，失败静默。

**方案**：抽象出统一的交互消息编排方法，业务侧只描述「发给谁、什么内容」，
由编排层负责渠道解析、开关判断、失败处理。
考虑到改动面在 `search_torrents.py`，建议单独一次提交、充分回归。

### M6 无失败告警

所有渠道发送失败时，系统照常运行。`MessageCenter` 也不记录发送状态。

**方案**：随 M3 落库一起做 —— 发送结果（成功/失败/原因）写入消息表，
前端在通知设置页展示「最近一次发送结果」，让静默失败可见。

---

## 四、P2 问题详述

### M7 消息级别

当前只有 14 个「按事件」的开关，无法表达「下载成功不用告诉我，失败必须告诉我」。
（实际上开关就是按事件的，用户得手动一个个勾。）

**方案**：引入 4 级 `debug / info / warn / error`，渠道配置增加「最低级别」下拉。
原开关保留（提供更细粒度控制），级别作为附加过滤。

### M8 消息文案不可自定义

14 个渠道里只有 `webhook` 支持 Jinja2 模板，其余文案硬编码在 `message.py` 的 20 个方法里。
例如 `send_download_message` 有 12 处 `msg_text = f"{msg_text}\n..."` 手工拼接。

**方案**：把 webhook 已有的 Jinja2 能力提升为全渠道通用，
每个事件定义标准变量集，用户可选「默认模板」或自定义。

### M9 AI 助手未接入

`agent_tools.py` 的 51 个工具里无任何通知相关。可加：
- **查询类**：`get_recent_notifications` / `get_notification_stats`（依赖 M3 落库）
- **配置类**：`update_channel_switch` / `test_notification_channel`
- **摘要类**：`summarize_notifications(period)`，建议做成定时摘要

---

## 五、建议排期

### 第一批（P0）—— **已完成 2026-09-19**

- **M1 同秒丢消息** + **M2 跨天重复推送** —— seq 游标方案，改动 5 个文件
- **M4 分段发送不中断** —— 顺带一并修复（同在 message 链路，一次验证）
- 附 2 个回归脚本：`message_center_fix_verify.py`（22 断言）、
  `message_segment_fix_verify.py`（19 断言），均加载真实源码

### 第二批（P1，一次迁移一起验证）

- **M3 落库**（含 Alembic 迁移）+ **M6 失败告警**
  - M4 已让发送失败能如实返回 `(False, err)`，M6 只需把结果写进消息表即可
- **M10 首屏条数 / 队列长度对齐** —— 首屏 20 条 vs `maxlen=50`，
  积压超过 20 条时游标会停在中间，剩余的不会补发。落库时一并解决最自然

### 第三批（P1，需设计，改动面较大）

- **M5 交互消息统一编排**

### 第四批（P2，作为独立大版本）

- **M7 级别 + M8 模板化 + M9 AI 接入**
- M9 依赖 M3，需在第二批之后

---

## 六、附：M1/M2 修复的前后端契约调整（seq 游标）—— 已实施

**改造前**（`web/action.py:1787`）：

```python
def get_system_message(lst_time):
    messages = MessageCenter().get_system_messages(lst_time=lst_time)
    if messages:
        lst_time = messages[0].get("time")      # 秒级字符串
    return {"code": 0, "message": messages, "lst_time": lst_time}
```

**改造后（实际落地版本）**：

```python
# web/action.py
@staticmethod
def get_system_message(lst_seq):
    try:
        lst_seq = int(lst_seq or 0)
    except (TypeError, ValueError):
        lst_seq = 0
    messages = MessageCenter().get_system_messages(lst_seq=lst_seq)
    # 游标推进到本批最新一条的序号；本条没有更新时保持原值
    new_seq = messages[0].get("seq") if messages else lst_seq
    return {"code": 0, "message": messages, "lst_seq": new_seq}
```

```javascript
// web/static/js/functions.js
if (lst_seq) {
  setTimeout(`get_message(${lst_seq})`, 3000);
} else if (msgs) {
  setTimeout(`get_message(0)`, 3000);
}
```

`web/main.py` 的 WebSocket 处理器同步改为读写 `lst_seq`，
并在转发给前端的消息体里补上 `seq` 字段（`message.get("seq", 0)`）。

**向后兼容**：`lst_seq` 缺省为 0，等价于「拉全部」，首屏行为不变。
游标做了 `int()` 归一化 —— 前端 JSON 可能传来字符串，只靠 `or 0` 兜底
会让 `"0"`（truthy）参与比较而抛 `TypeError`。

---

## 七、验证方法（可复现）

```bash
# M1/M2 修复后回归（加载项目真实源码，22 个断言）
python .workbuddy/tests/message_center_fix_verify.py

# M4 分段发送修复后回归（19 个断言）
python .workbuddy/tests/message_segment_fix_verify.py

# 三种游标方案的历史对比（现网 vs ts vs seq）
python .workbuddy/tests/message_cursor_diag.py
```

历史对比结论（该脚本用独立复刻实现，用于说明方案选型）：

| 场景 | 现网实现 | 方案一 ts | 方案二 seq |
|---|---|---|---|
| 同秒插入 2 条（M1 核心） | ✘ 丢消息 | ✘ 丢消息 | **✔** |
| 循环连插 5 条同秒（真实场景） | ✘ 丢消息 | ✘ 丢消息 | **✔** |
| 跨天旧消息不推送（M2 核心） | ✔ | ✔ | **✔** |
| 正常间隔 5 秒 | ✔ | ✔ | **✔** |

**只有 seq 方案全场景通过。** 时间戳方案在同秒场景下同样失败 ——
这是实测推翻了最初假设，故最终采用 seq。