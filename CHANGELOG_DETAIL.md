# 改动细节档案（CHANGELOG_DETAIL）

> 本文件存放各版本「怎么改的」实现细节：涉及的文件、测试、踩过的坑。
> 用户向的发布说明见 [CHANGELOG.md](CHANGELOG.md)（只写修复 / 新增 / 怎么用）。

# v6.2.9 (2026-10-01) — 新增：飞牛影视登录态失效自动重登

## 背景

飞牛影视是**登录型**鉴权（`/user/loginByPassword` 取 token，之后每个请求带 `Authorization`），
而登录只在 `TrimeMediaClient.init_config() -> _connect()` 里做**一次**；`init_config()` 只有两个
触发点：**进程启动** 与 **用户登录 NAStool**。于是服务端一旦作废会话，就再也回不来：

| 失效场景 | 后果 |
|---|---|
| NAS / 飞牛影视服务重启 | token 失效 |
| 登录会话过期 | token 失效 |
| 修改飞牛密码 | token 失效 |

而 `__is_ready()` 只检查**本地存的 token 字符串是否非空** —— 服务端早已废弃会话，它照样返回
「就绪」；`request()` 遇到 401 只写日志然后返回失败，**没有「重登再试一次」这条路径**。

症状（会一直持续、不会自愈，只能重启容器）：首页媒体库列表变空、封面不显示、
「媒体库同步」失败或同步出 0 条、入库前的「媒体是否已存在」判断失真（可能重复下载）。

对比：Emby / Jellyfin / Plex 用长期 API Key，不依赖短期会话；绿联影视虽同为登录型，
但登录时带 `keepalive: True` 向服务端申请长会话。**飞牛这两样都没有**。

## 改动（2 文件 / +131 −3）

### 1. `app/mediaserver/client/trimemedia.py`（LF）

**`request()` 新增 `allow_relogin` 参数 + 两个失败出口自愈**：

```python
if not res.ok:
    if allow_relogin and self.__is_auth_error(res.status_code) \
            and self.__auto_relogin():
        return self.request(..., allow_relogin=False)   # 重登成功后重放，且只重放一次
```

响应体 `code` 分支同理（有些网关把 401 塞在 body 里，HTTP 仍是 200）。

**新增 `__auto_relogin()`，三道护栏**：

| 护栏 | 常量 | 默认 | 作用 |
|---|---|---|---|
| 冷却 | `RELOGIN_COOLDOWN` | 30s | 批量同步时几十个请求会同时拿到 401，没有冷却会并发打几十次登录接口 |
| 上限 | `MAX_RELOGIN` | 2 | 连续失败达上限即停止，密码错时不刷日志 |
| 冷静期 | `RELOGIN_RETRY_INTERVAL` | 600s | 停止后隔 10 分钟重开一轮，否则「服务端临时不可用」会让自愈永久失效 |

`__is_auth_error()` 只认 **401 / 403**（HTTP 状态码与响应体业务码通用）。刻意**不猜**
「哪个业务错误码代表未登录」—— 飞牛未登录时返回什么业务码没有可靠依据，乱猜会把正常业务
错误也拿去重登。日后实测到其它未登录码，加进 `AUTH_ERROR_CODES` 即可。

**`login()` / `logout()` 内部的请求一律传 `allow_relogin=False`** —— 否则登录接口自身 401 时会递归重登。

**⚠️ 一个必须踩对的细节：重登失败要把原 token 放回去。**

最初的写法是 `self._token = None` 后再尝试重登。这会踩雷：`__is_ready()` 读的正是这个 token，
置空后它恒为 `False`，而 `TrimeMediaClient` 的 20+ 个方法**第一句就是
`if not self.__is_ready(): return`** —— 连请求都不发，自愈从此再没有机会触发，
连「服务端已经恢复」都不知道。故失败路径必须恢复原值：

```python
old_token = self._token
self._token = None
if self.login(...):
    ...
self._token = old_token      # ← 失败也要放回去
```

**新增 `_norm_int()`**（与 `_norm_bool` 同族）：表单提交一律是字符串，`relogin` 读出来可能是
`"3"` / `""` / `"abc"`，统一归一化并夹在 [0, 10]，非法值回落默认值，**绝不抛异常**
（该函数跑在客户端初始化路径上，抛异常会让整个客户端建不起来）。

### 2. `app/conf/moduleconf.py`（CRLF）

`MEDIASERVER_CONF["trimemedia"]["config"]` 新增字段：

```python
"relogin": {
    "id": "trimemedia.relogin",
    "title": "自动重新登录次数",
    "type": "text",
    "placeholder": "2"
},
```

**不需要改任何模板**：设置页表单由 `macro/form.html::gen_form_config_elements` 按该字典
**动态渲染**，前端 `input_select_GetVal()` 按 input 的 `id` 自动收集 —— 加一个字段就够了。
这与「下载目录设置」那类需要手写控件、且**控件放错容器就永远存不上**的表单完全不同。

## 已知边界（本版刻意不做）

`__is_ready()` 为 False（即**从来没连上过**，例如容器启动时飞牛还没起来）时不走本机制 ——
那时连 token 都没有，没有凭据可重登。这属于「断线重连」，入口是 `_api is None`，
与本版覆盖的「连过、之后失效」是两条不同路径，留待后续。

## 验证

- `_verify_bump_629.py` **65/65 通过**（用托管 venv 跑）。
- 判据全部从真实源码派生：`log` 桩的公开面从真实 `log.py` 的 AST 抽取并**双向断言**
  （桩 ⊆ 真实、产品代码引用 ⊆ 真实）；产品代码传给 `requests.Session.request` 的 kwargs
  必须 ⊆ 真实签名（防「桩比真实更宽容」）；配置项从真实 `moduleconf.py` 取，
  并用**真实 Jinja 模板**渲染（含整页 `setting/mediaserver.html`）。
- 真跑 `_TrimeApi`（只把 HTTP 会话换成假 handler），覆盖 11 组场景：401 自愈重放、
  重放仍 401 不递归、登录接口自身 401 不递归、30s 冷却下连续 5 个请求只重登 1 次、
  连续失败达上限停止、冷静期后重开一轮、响应体 `code=401`、HTTP 500 不动重登、
  `max_relogin=0` 关闭、`logout` 不重登、重登失败后 token 被恢复。
- **3 条反向注入**：`__is_auth_error` 边界值（0 / None / `"abc"`）；把自愈代码从源码里
  删掉后 401 **不再**重登（证明判据确实在测这段新代码）；行尾与装饰器粘连
  （AST 顶层 `MatMult`）体检。

# v6.2.8 (2026-10-01) — 修复：媒体服务器图标改走配置（绿联影视图标 404）

## 背景

「媒体库同步」弹窗的图标一直是**按内部代号硬拼文件名**的：

```jinja
style="background-image: url('../static/img/mediaserver/{{ MediaServerType }}.png')"
```

emby / jellyfin / plex / trimemedia 恰好都有同名 `.png`，所以问题一直没暴露；
但**绿联影视复用的是 emby.png**：

| 类型 | 配置里的 `img_url` | 硬拼出的文件 | 磁盘是否存在 |
|---|---|---|---|
| emby | `.../emby.png` | `emby.png` | ✓ |
| jellyfin | `.../jellyfin.jpg` | `jellyfin.png` | ✓（侥幸，且与设置页图标不一致） |
| plex | `.../plex.png` | `plex.png` | ✓ |
| **ugreen** | **`.../emby.png`** | **`ugreen.png`** | **✗ 不存在 ⇒ 404 空白** |
| trimemedia | `.../trimemedia.png` | `trimemedia.png` | ✓ |

## 根因

同一个语义（「这台媒体服务器的图标」）在**两处用了两套取法**：
设置页 `setting/mediaserver.html:22` 取 `MediaServer.img_url`（读配置，正确），
首页弹窗按 `MediaServerType` 拼文件名（猜文件名，错）。

## 改动（2 文件 / +7 −1）

1. `web/main.py::index()` 新增模板变量 `MediaServerImg`，与已有 `MediaServerName` 同族：

```python
MediaServerImg = (
    (ModuleConf.MEDIASERVER_CONF.get(str(MSType).lower()) or {}).get("img_url")
    or f"../static/img/mediaserver/{MSType}.png"
)
```

   末尾的 `or ...` 是**兜底**：配置里查不到该类型时，行为与改前**完全一致**（仍按代号拼），
   不引入新分支。

2. `web/templates/index.html`：图标表达式改为 `url('{{ MediaServerImg }}')`。

   前缀 `../static/img/mediaserver/` 与改前逐字符相同 ⇒ **相对路径解析基准不变**，
   未新增 / 删除任何静态资源。

## 已知副作用（正向）

Jellyfin 的弹窗图标从 `jellyfin.png` 变为 `jellyfin.jpg`（配置里本就是这个），
结果与设置页**看齐**，两处图标从此一致。

## 刻意未改

`web/main.py` 仍照旧传 `MediaServerType=MSType`。模板已不再引用它，但保留传参属
**零风险保留**（避免影响任何潜在的其它引用）。

## 验证

- `_verify_bump_628.py` 全部通过。判据全部从**真实源码派生**：`MEDIASERVER_CONF` 用
  `ast.literal_eval` 从 `moduleconf.py` 抽真字典，`MediaServerImg` 表达式用
  `ast.get_source_segment` 从 `main.py` 抽真实源码，模板行为用**真实 Jinja 渲染**比对。
- **新增一条磁盘守卫**：断言 5 种类型配置的 `img_url` 指向的文件**必须真实存在**
  —— 防的正是「将来再加媒体服务器时又指向不存在的文件」这类复发。
- 含 **4 条反向注入**（旧表达式复现 `ugreen.png` / 磁盘守卫报红 / 变量回退 / 模板回退）。
- 存量回归：飞牛全套 + 媒体同步 + 排版守卫全绿。

## 附带改动：发布说明标题去掉「（MoviePilot 风格）」

`CHANGELOG.md` 第 1 行就是 GitHub Release 的正文标题 —— CI `.github/workflows/build.yml`
用 `body_path: CHANGELOG.md` 把**整份文件**作为 Release body，所以那行会出现在
**每个** Release 页面的最顶部。本次一并改为 `# 发布说明`，历史 Release 的正文也通过
API 同步清理。

正文里**技术性**的 MoviePilot 引用（「参照上游实现」「报文逐字节对齐」这类溯源说明）
一概保留 —— 那是实现依据，与标题措辞是两回事。

# v6.2.7 (2026-10-01) — 补齐：同步弹窗标题也走显示名

## 背景

v6.2.6 把首页标题从 `MediaServerType`（内部代号）换成了 `MediaServerName`（显示名），
但**只改了页面标题那一处**。用户实测发现，点「媒体库同步」弹出的那个完成弹窗顶部
仍写着 `Trimemedia` —— 那是同一份模板里的另一处引用，v6.2.6 漏掉了。

## 根因

`web/templates/index.html` 里，同一个模板变量被用于两层不同语义：

| 用途 | 变量 | 为什么不能互换 |
|---|---|---|
| 静态图标文件名 | `MediaServerType` | 文件名就是内部代号（`trimemedia.png`），换中文名会 404 |
| 给人看的名字 | `MediaServerName` | 取 `ModuleConf.MEDIASERVER_CONF[type]["name"]`，已是中文名 |

第 193 行用 `MediaServerType` 拼图标路径（**正确，不动**）；第 195 行原本也用了
`MediaServerType`，但套了个 `|title` 过滤器 —— 它把 `trimemedia` 变成 `Trimemedia`，
看着像个正经人名，所以从界面上一眼看不出来。

⚠️ `web/main.py` 里另有 `MediaServerType.PLEX` / `.JELLYFIN` / `.EMBY` 这类引用，
那是 `app.utils.types.MediaServerType` **枚举**（值本身已是 `Plex` / `绿联影视` / `飞牛影视`），
与模板变量同名但完全无关，**不能一起改**。本次锚点核对专门把这三行排除在外。

## 改动（1 文件 / 1 行）

| 文件 | 改动 |
|---|---|
| `web/templates/index.html` | 第 195 行 `{{ MediaServerType\|title }}` → `{{ MediaServerName }}` |

`git diff --stat` = `1 file changed, 1 insertion(+), 1 deletion(-)`；行尾保持 CRLF
（补丁走「归一成 LF 替换 → 按原行尾写回」，并断言 CRLF/裸 LF 计数不变）。

## 影响面

| 媒体服务器 | 改前显示 | 改后显示 |
|---|---|---|
| 飞牛影视 | `Trimemedia` | `飞牛影视` |
| 绿联影视 | `Ugreen` | `绿联影视` |
| Emby / Jellyfin / Plex | `Emby` / `Jellyfin` / `Plex` | 不变（显示名与代号仅大小写之差） |

## 测试

- `_verify_bump_627.py` 共 **67 项**，含 6 条反向注入，确保判据不是「永远为真」。
- 存量回归：全套通过（trimemedia / fix / regression / runtime / e2e / realhttp /
  diagnose / log 守卫 / bump_626 等）。
- **判据按「行为」而非「字面」**：断言的是「模板里交给用户看的那处用 `MediaServerName`」
  且「图标那处仍用 `MediaServerType`」—— 否则会把本来正确的图标表达式误判成漏改
  （v6.0.3 踩过同类坑：文档里说明「某字段已移除」必然要写出该字段名，导致「出现即失败」的
  扫描判据在发版后必红）。

# v6.2.6 (2026-10-01) — 图片鉴权闭环：库封面 / 观看记录封面 + 标题显示名

## 背景

v6.2.5 收口了「连不上」的可观测性。用户改对地址后飞牛影视接通，随即暴露两个新问题：

1. 首页标题把内部代号直接写在了页面上：`我的媒体库 - trimemedia`；
2. 「我的媒体库」页里的媒体库封面、以及「正在观看」的封面**全部不显示**。

## 根因（对照上游 MoviePilot 源码确认）

飞牛的图片接口（`/api/v1/sys/img/...`）与业务接口一样校验登录态，**并且需要把登录 token
以 Cookie（`Trim-MC-token`）形式携带**；而 `<img>` 标签无法附加请求头。上游
`app/modules/trimemedia/trimemedia.py::get_image_cookies()` 正是这个口径，且自带同源校验：

```python
if not image_url or not SecurityUtils.is_safe_url(image_url, [self._api.host], strict=True):
    return None
cookies = {"Trim-MC-token": self._api.token}
if self._access_code:
    cookies.update(self._api.cookies)
```

本仓此前**整条图片链路不带任何凭证**，且媒体库封面返回的是飞牛的绝对地址、由浏览器直连
⇒ 100% 失败。

## 改动（6 文件）

| 文件 | 改动 |
|---|---|
| `app/mediaserver/client/_base.py` | 新增**非抽象**钩子 `get_image_cookies(image_url)`，默认 `None` |
| `app/mediaserver/client/trimemedia.py` | 新增 `_TrimeApi.cookies` 属性；实现 `get_image_cookies()`；`get_libraries()` 的 `image` / `image_list` 改走 `get_nt_image_url()` 本机中转 |
| `web/backend/web_utils.py` | `request_cache(url, cookies=None)`；凭证指纹进 `lru_cache` 键；新增 `get_image_cookies(url)` |
| `web/main.py::Img()` | 取凭证并传给 `request_cache`；Etag 含凭证指纹 |
| `web/templates/index.html` | 标题改用 `MediaServerName` |
| `scripts/diagnose_trimemedia.py` | 新增 `probe_image_auth()` 封面鉴权探测 |

## 关键设计

- **同源白名单（安全红线）**：`get_image_cookies` 只在图片地址与本机配置的 `host` 同源
  （scheme + host + port 完全一致）时才返回凭证。否则 `/img?url=<任意地址>` 会变成
  「带着 token 请求任意 URL」的凭证外泄 / SSRF 通道 —— 比不做更危险。实测拦下
  `userinfo 伪装`（`http://ip:port@evil.com`）与「把目标地址塞进 query」两种写法。
- **钩子必须非抽象、默认 `None`**：写成 `@abstractmethod` 会逼 Emby / Jellyfin / Plex / 绿联
  五个已有客户端全部实现；默认返回 `None` ⇒ 老客户端行为完全不变。
- **`lru_cache` 键必须含凭证指纹**：`dict` 不可哈希 ⇒ 转成 `tuple(sorted(...))`；
  否则「换了凭证仍命中旧缓存」，表现为登录态变了图片却还是旧的。
- **登录 token 权威**：先合并会话 Cookie、最后写 `Trim-MC-token`。上游顺序相反，
  若会话里恰好存在同名 Cookie 会把登录 token 顶掉。

## 离线验证逮到的两个真 bug

1. **`request_cache` 的 douban 分支会丢凭证**：原代码 `if url.find('douban'):` 缺 `!= -1`，
   实际几乎总走这一支，而这一支调用 `RequestUtils(referer=...)` **没带 cookies**
   ⇒ 即便实现了凭证注入，图片依然取不到。已在两支都带上。
2. **`_split_origin` 的宽 `except Exception` 会把编程错误吞成「不同源」**：测试命名空间漏了
   `urlsplit` 时它静默返回 `None`（= 白名单永久不通过），表现为「功能不生效但不报错」。
   已收窄为 `except ValueError`，只吞「地址本身畸形」。

## 测试

- `_verify_bump_626.py` **65 项全绿**，含 4 条反向注入（标题回退 / 钩子加 `@abstractmethod` /
  摘掉同源校验 / 缓存键去掉凭证指纹），确保判据不是「永远为真」。
- 存量回归：trimemedia 26 / fix 63 / regression 31 / runtime 20 / e2e 36 / realhttp 59 /
  diagnose 6 / log 守卫 7 / bump_625 48，全部通过。
- 两处**过期判据**随行为变化更新（行为是刻意改的）：
  `_verify_trimemedia_fix.py` 的「库封面是绝对地址」→「库封面走本机 /img 中转」；
  `_verify_diagnose_script.py` 的服务端验签**豁免图片请求**（`<img>` 式请求按设计不带 authx）。

# v6.2.5 (2026-10-01) — 连接诊断闭环：首页给原因 + 诊断脚本前置体检 + 保存后回读

## 背景

v6.2.3 / v6.2.4 解决的是「配置没落盘」与「报错被覆盖」两个具体缺陷。但真机上仍可能出现
「测试失败 / 首页连接失败」而**信息不足**：报错文案写死的是 Emby/Jellyfin/Plex、且不给原因；
诊断脚本只有接口层，一旦 DNS / 端口 / 证书 / 反向代理这条链断了，它给出的仍然只是一句「请求异常」。

本版把这三处补上，目标是**把「猜」换成「读」**。不涉及任何协议改动，零新依赖。

## 一、首页报错文案：动态名称 + 真实原因

| 文件 | 改动 |
|---|---|
| `web/action.py::get_library_mediacount` | 失败时优先读客户端的 `last_error`，拼成 `媒体库服务器连接失败：<原因>` |
| `web/main.py::index` | 新增 `ServerError` / `MediaServerName` 两个模板上下文 |
| `web/templates/index.html` | 报错文案改为由上下文的名称与原因拼装 |

显示名取自 `ModuleConf.MEDIASERVER_CONF[<type>]["name"]`，即「飞牛影视 / 绿联影视 / Emby …」，
与「设置 → 媒体服务器」卡片上写的一致。

`last_error` 只有实现了该属性的客户端（飞牛影视）才有，用 `getattr(client, "last_error", None)`
读取；Emby / Jellyfin / Plex / 绿联影视 走原路径，文案与以前完全一致（已验证）。

## 二、诊断脚本：四段前置体检

`scripts/diagnose_trimemedia.py` 新增 `preflight()`，在每个候选地址做接口探测**之前**执行：

| 段落 | 判据 | 失败时的提示 |
|---|---|---|
| DNS | `socket.getaddrinfo` | 改用 IP；或给容器配 DNS / 写 hosts |
| TCP | `socket.create_connection(timeout=5)` + 计时 | 端口、服务、容器网络、防火墙 |
| TLS | 先不校验证书取 `getpeercert()`，再单独跑一次真校验 | 自签名 / 主机名不匹配 ⇒ 关「校验SSL证书」 |
| 反向代理 | 不带签名裸探 `{根}/` 与 `{根}/v/`，看 Server 头 / 3xx / 5xx | 重定向 = 网关页；502/504 = 后端不可达；401/403 = 网关鉴权 |

体检不通过就**跳过接口探测**（省掉 10s 超时等待），新增 `--no-preflight` 可关闭。

## 三、保存后回读

`config.py` 新增 `read_config_file()`（从磁盘重读，不改变内存配置）；
`web/action.py::__update_config`：

1. `save_config` 包 `try/except`，写失败返回 `{"code": 1, "msg": "配置文件写入失败：…"}`（此前会 500）；
2. 写成功后调用 `__verify_config_saved(cfgs)` 逐键回读。

**误报控制**（本节最需要小心的地方）：只校验「提交了非空标量值」的键，且只在磁盘上
「找不到 / 是 None / 是空串」时才报；值不同但都存在则不报（后端有密码散列、代理包装等正常转换）。
`app.login_password` 与 `app.proxies` 直接跳过。回读结果不是 dict、或读取抛异常，都只给提示不抛。
返回 `{"code": 0, "msg": ...}` —— `code` 语义不变，其它调用方不受影响。

前端 `mediaserver.html` 在「保存」与「测试」两条回调里都会把 `msg` 弹出来。

## 四、验证

`_verify_bump_625.py`：**48 项断言全绿**。被测逻辑一律用 AST 从真实文件抽源码来跑，
只有 `Config` / `MediaServer` / `log` 等外部依赖做桩。

踩到的两个**测试自身**的坑（已记入技能）：

- `MediaServer.server` 是 `@property`，桩若写成 `@staticmethod`，`instance.server` 拿到的是
  **函数对象**而不是客户端实例 ⇒ 测试假红。这是「桩必须从真实模块派生」的又一变体。
- `ruamel.yaml` 要用 `import ruamel.yaml`，只 `__import__("ruamel")` 拿不到 `.yaml` 属性 ⇒ 测试假红。

## 五、附带修正：log API 守卫的假阳性

`_verify_log_api_usage.py` 的 [B] 段原先用文本正则找 `for n in ("info", ...)`，而
`_patch_bump_624.py` 的发布说明里**贴了一段含 `"warning"` 的示例代码**，被误判成「桩造了不存在的名字」。
现改为 AST 识别（`ast.For` + `ast.Tuple` + 邻近 `setattr(`），并加两条反向注入：
真桩要能识别、贴在文档字符串里的同款样例不能识别。

## 六、其它

- `_check_anchors_625_bump.py`（只读锚点核对）在本版实测拦下一个错锚点：
  `index.html` 里 `MediaServerName` 我写 want=3，实际是同一行出现 2 次。
- 灰度结论：本版**不承诺「改完就能连上」**。「连不上」的成因可能在地址 / 网络 / 反代侧，
  本版的作用是把它**指出来**。协议层报文与上游 MoviePilot 的比对仍是 40/40 一致。

# v6.2.4 (2026-09-30) — 修复 log.warning 越界调用 + 加固测试桩

## 用户可见的问题

飞牛影视点「测试连接」弹窗：

```
测试失败：
地址探测异常（http://192.168.3.3:5666）：AttributeError: module 'log' has no attribute 'warning'
```

## 根因

仓库根 `log.py` 只提供 `debug / info / error / warn / refresh_loglevel / console`，
**没有 `warning`**（这个项目里叫 `warn`）。

而 v6.2.3 新增的 `_resolve_api()` 诊断日志里写了 `log.warning(...)`：

```python
detail = api.last_error or "版本接口未通过校验"
log.warning(f"【…】地址探测失败（{cand}）：{detail}")   # ← 这一行自己抛 AttributeError
```

**为什么杀伤力被放大**：该行位于 `try` 内部，下面紧跟 `except Exception` ——
它自己抛的 `AttributeError` 被**当成「探测失败的原因」记了下来**，
把 `api.last_error` 里**真正**的原因（HTTP 状态码 / 返回非 JSON / 连接被拒）覆盖掉了。
两个候选地址走完，用户能看到的就只剩这句 AttributeError。

同类越界调用共 2 处：
- `_resolve_api()` 里这处 —— 会覆盖真实原因，**危害大**；
- `_connect()` 里「播放地址不可达」那处 —— 会直接中断 `init_config`。

## 修复

1. `log.warning(` → `log.warn(`（2 处）。
2. **候选地址失败原因改为累积**：原先 `last_detail` 会被后一个候选覆盖，
   现在用 `reasons` 列表把每个候选的原因都收起来，最终 `last_error` 形如
   `所有候选地址均无法连接；http://ip:5666/v → HTTP 404…；http://ip:5666 → …`。

## 为什么 330 项断言没拦住（真正的教训）

`_verify_*` 系列脚本里的 `log` 桩是**手写**的：

```python
lg = types.ModuleType("log")
for n in ("info", "error", "warn", "warning", "debug"):   # ← 我凭空造了 "warning"
    setattr(lg, n, lambda *a, **k: None)
```

**桩比真实实现更宽容** ⇒ 产品代码里不存在的调用也照样「通过」。
这与 v6.2.0「132 项全绿却真机必挂」是**同一类错误**，只是换了个模块。

### 新增守卫 `_verify_log_api_usage.py`

双向设防，全部**从真实源码派生**、不手写：

| 段 | 内容 |
|---|---|
| A | 解析 `log.py` AST 得到真实公开面；扫描第一方代码里所有 `log.<name>` 调用，越界即失败 |
| B | 扫描所有验证脚本的 `log` 桩名字表，**桩里出现 `log.py` 没有的名字即失败** |
| C | 把 `trimemedia.py` 依赖的 `ExceptionUtils` / `MediaType` / `MediaServerType` 也按真实成员校验 |

[A] 实跑：`log.warn` 146 次、`log.info` 423 次、`log.error` 345 次…全部合法，越界 0。
[B] 实跑：**10 个脚本**（不只本次相关的）都把 `"warning"` 写进了桩 —— 已用
`_fix_logstubs.py` 按真实公开面统一矫正，并在每处加注释指回守卫。

**反向测试**（证明守卫有牙）：临时写入一个含 `import log` + `log.warning("x")` 的文件，
守卫立即报「产品代码使用了 log.warning（1 处，首处 app\_tmp_logcheck.py:5）」✓，随后删除。

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | `log.warning` → `log.warn`（2 处）；`_resolve_api` 累积每个候选地址的失败原因 |

## 测试

| 脚本 | 项数 | 结果 |
|---|---|---|
| `_verify_log_api_usage.py`（**新增守卫**） | 5 | ✓ |
| `_verify_tri_vs_upstream.py` | 40 | ✓ |
| `_verify_trimemedia_diagnostics.py` | 28 | ✓ |
| `_verify_diagnose_script.py` | 6 | ✓ |
| `_verify_trimemedia_realhttp.py` | 59 | ✓ |
| `_verify_trimemedia_fix.py` | 63 | ✓ |
| `_verify_trimemedia_e2e.py` | 36 | ✓ |
| `_verify_trimemedia_regression.py` | 31 | ✓ |
| `_verify_trimemedia.py` | 26 | ✓ |
| `_verify_trimemedia_runtime.py` | 20 | ✓ |

合计 **314 项断言，0 失败**，且本次全部使用**已校正的严格桩**。

---

# v6.2.3 (2026-09-30) — 连接诊断与「测试即保存」

## 背景

用户反馈「测试也是失败」，并质疑「你怎么测试的」。核对后确认两点：

1. **代码协议层没有问题** —— 本版补了「与上游逐字节比对」的验证（见下），40 项全一致；
2. **接入层确实有两个坑**：点「测试」不保存配置；失败原因被吞掉。两者叠加，用户看到的
   就是「测试失败 / 首页媒体服务器连接失败 / 媒体库列表 0/0」，而且完全无从下手。

## 坑 1：点「测试」= 配置从不落盘

`web/action.py::__update_config`：

```python
config_test = False
for key, value in cfgs:
    if key == "test" and value:
        config_test = True
        continue
    cfg = self.set_config_value(cfg, key, value)   # 就地改 Config()._config
if not config_test:
    Config().save_config(cfg)                      # ← 只有非测试分支才落盘
```

而 `web/templates/setting/mediaserver.html::test_mediaserver_config` 传的正是 `test=true`：

```js
save_config(type, function (ret) { ... }, true);   // ← 第三个参数就是 test
```

`set_config_value` 是**就地修改** `Config()._config` 的，所以：

- 点「测试」→ 值进内存 → 本次测试能用到新值 ✓
- **但从不 `save_config()` 落盘** → 容器一重启，配置回滚到上一次保存的状态 ⇒ 全空
- 用户随后看到的「首页媒体服务器连接失败」「媒体库列表 0/0」正是配置为空的直接后果

**修复**：`test_mediaserver_config` 改为**先保存、再测试**（去掉 `test=true`）；
`save_config` 也只在真的需要「仅测试」时才带 `test` 字段（原先无条件写 `test: false`，
会把这个无用字段顺手写进配置文件）。

## 坑 2：失败原因被吞掉

`__test_connection` 原先只返回 `{"code": 0/1}`，前端只能显示「测试失败！」。
一个连不上飞牛的用户，拿不到任何可行动的线索。

**修复**：新增 `last_error` 字段，把最底层的原因一路带上来。

| 层 | 内容 |
|---|---|
| `_TrimeApi.last_error` | `HTTP 404（url）Content-Type：... 响应：...` / `返回非 JSON 响应（HTTP 200，url）...` / `错误码 1001：用户名或密码错误（url）` / `ConnectionError: ...（url）` |
| `TrimeMediaClient.last_error` | `未读到「飞牛影视」配置：请先…` / `配置不完整，缺少：用户名、密码` / `登录失败：错误码 1001…` / `获取用户信息失败：…` |
| `__test_connection` | 读 `last_error` 放进返回的 `msg`；**导入/构造类异常也回显**（此前被静默吞成「测试失败」） |
| `mediaserver.html` | 失败时 `alert` 弹出原因 |

## 坑 3：请求报文与上游有 2 处细节差异

与 MoviePilot 官方 `api.py` 逐字节比对后发现：

| # | 差异 | 处理 |
|---|---|---|
| 1 | POST 且无请求体时：上游发 `""` 并带 `Content-Type: application/json`，本仓发 `None` 且不带 | 对齐上游 |
| 2 | `json.dumps` 上游带 `allow_nan=False`，本仓带 `ensure_ascii=False` | 对齐上游（`ensure_ascii` 只影响中文转义、不影响签名自洽，但一致更稳） |

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | `last_error` 全链路；`request()` 补 HTTP 状态码/Content-Type/响应片段；POST 空体与 Content-Type 对齐上游；`_resolve_api` 逐候选记日志并带出底层原因 |
| `web/action.py` | `__test_connection` 回显 `last_error` 与异常 |
| `web/templates/setting/mediaserver.html` | 测试按钮改为「先保存再测试」；失败弹窗显示原因；去掉 `test:false` 脏写 |
| `scripts/diagnose_trimemedia.py` | **新增**：独立诊断脚本（不依赖 NAStool 代码，容器内可直接跑） |

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| **报文级交叉验证（新增）** | `_verify_tri_vs_upstream.py` | **40** | ✓ 与 MoviePilot 上游 api.py 逐字节一致 |
| **失败原因链路（新增）** | `_verify_trimemedia_diagnostics.py` | **28** | ✓ |
| **诊断脚本（新增）** | `_verify_diagnose_script.py` | **6** | ✓ 四个分支 + 服务端侧独立复算 authx |
| 真实 HTTP 链路 | `_verify_trimemedia_realhttp.py` | 59 | ✓ |
| 修复专项 | `_verify_trimemedia_fix.py` | 63 | ✓ |
| 端到端 | `_verify_trimemedia_e2e.py` | 36 | ✓ |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ |
| 声明 / 运行时 / 类型 / 契约 / 缺口审计 | 5 个脚本 | 26+20+21+契约+无缺口 | ✓ |

合计 **330 项断言，0 失败**。

## 为什么这次的验证可信（回应「你怎么测试的」）

此前 v6.2.0 的验证是「自己写假服务端 + 自己写桩」，而**桩比真实实现更宽容**，
形成自证循环，所以 132 项全绿却真机必挂。

本版新增的三件事专门用来打破自证：

1. **`_verify_tri_vs_upstream.py`**：把 MoviePilot 官方 `api.py` 从 GitHub 拉下来，与本仓实现
   在**相同输入**下驱动，固定 `random` / `time` 后逐字节比对实际发出的
   `method / url / params / body / headers / authx` —— **上游代码是协议的唯一权威参照**。
2. **`_verify_diagnose_script.py`**：起本地假服务端，**服务端侧独立复算 authx**
   （不复用被测算法的任何代码），签名对不上直接返回错误码。
3. **`scripts/diagnose_trimemedia.py`**：交付给用户在**真实环境**跑，不依赖 NAStool 任何代码 ——
   它的输出就是真机的第一手证据。

---

# v6.2.2 (2026-09-30) — 飞牛影视测试连接修复

## 背景

用户反馈「飞牛影视测试一直失败」。v6.2.0 / v6.2.1 的功能只在**本地假服务端**验证过，
这是该客户端第一次与真实服务端联调。

## 根因：HTTP 层调错 API，每个请求必然抛错

`trimemedia.py` 的请求层写的是：

```python
RequestUtils(verify=self._ssl_verify, session=True)
```

而 nas-tools 真实的 `RequestUtils.__init__` 签名是：

```
(headers, cookies, api_key, proxies, session, timeout, referer, content_type, accept_type)
```

- **根本没有 `verify` 参数** ⇒ `TypeError: unexpected keyword argument 'verify'`；
- `session` 要的是 `requests.Session` **实例**，传 `True` 会在内部变成 `True.get(...)` ⇒ `AttributeError`。

于是链路必然断在第一步：

```
_resolve_api() → verify_access_code() / sys_version() 恒失败 → _api = None
→ get_status() 恒 False → 前端永远显示「测试失败」
```

**100% 复现，不可能偶尔通。**

## 为什么 132 项验证全绿仍然漏掉：桩自证循环

v6.2.0 的假服务端验证脚本里的 `_RequestUtils` 是**按记忆手写的桩**，
其签名恰好写成 `(headers, timeout, verify=True, session=False, ...)` ——
**正好接受了我写错的参数**。于是「写错的调用」与「过于宽容的桩」互相印证，形成自证循环。

**教训**：**验证用的桩不能比真实实现更宽容**。参数合法性应当用 `inspect.signature`
对着**真实源码**断言，而不是靠桩「能跑通」来证明。

## 修复

| # | 内容 |
|---|---|
| 1 | 改用真实 `requests.Session`（与 ugreen 客户端同构）+ User-Agent。**副产品**：`ssl_verify` 配置从此真正生效（`RequestUtils` 内部把 `verify` 硬编码成了 False，用它等于一个死开关） |
| 2 | 图片前缀**幂等** —— 服务端若已返回带 `/api/v1/sys/img` 的地址，不再重复叠加 |
| 3 | `_play_host` 未填时不再回落成 `_host`（缺 `/v`，播放链接 404），改为回落到已含 `/v` 的 API 地址 |
| 4 | `get_status()` 补失败原因日志（未配置地址 / 缺用户名密码 / 未建立连接），便于用户自查 |

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | 请求层改真实 `requests.Session`；图片前缀幂等；`play_host` 回落修正；`get_status` 补日志 |

合计 1 文件 +66 / −19。

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| 抽象方法契约 | `_verify_trimemedia_contract.py` | 19 | ✓ |
| 声明 | `_verify_trimemedia.py` | 26 | ✓ |
| 运行时 | `_verify_trimemedia_runtime.py` | 20 | ✓ |
| **真实 HTTP 链路（新增，HTTP 层不桩）** | `_verify_trimemedia_realhttp.py` | **59** | ✓ 43 个 `/v` 请求全部通过服务端侧 authx 复算 |
| 端到端 | `_verify_trimemedia_e2e.py` | 36 | ✓ |
| 修复专项 | `_verify_trimemedia_fix.py` | 63 | ✓ |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ |
| 类型判定 | `_verify_mediasync_type.py` | 21 | ✓ |

合计 **275 项断言，0 失败**。

新脚本 `_verify_trimemedia_realhttp.py` 为三段式：

- **A** 从真实源码取 `inspect.signature`，AST 扫描全部媒体服务器客户端的请求构造，参数非法即红；
- **B** 把旧写法喂给真实实现，断言它**确实抛错**（根因确证，反向复现）；
- **C** 真实 `requests` 打假服务端，服务端侧独立复算 authx。

这条护栏对存量客户端同样生效。

## 踩坑

- **验证脚本的桩不能比真实实现宽容** —— 本次根因，详见上节。
- **测试连接用的是「已保存」的配置**：前端点测试时带 `test=true`，后端**不落盘**
  ⇒ 顺序必须是**先「保存」再「测试」**，改了表单直接点测试，测的是上一次保存的配置。
- **真实环境联调前，所有验证都只是「协议自洽」**，不能替代与真机对接。本次就是靠用户实测才暴露。

## 兼容性

- 仅 HTTP 层实现变更，无接口变更、无新依赖、无数据迁移；
- 存量 emby / jellyfin / plex / ugreen **不受影响**（它们各有自己的请求方式，未共用本次改动）；
- `ssl_verify` 在本客户端从「无效」变为「有效」，属**修正**而非行为破坏。

---

# v6.2.1 (2026-09-30) — 飞牛影视首页数据与图片链路修复

## 背景

v6.2.0 接入飞牛影视后，用户反馈「媒体库同步能跑了，但『我的媒体库』里的内容没变化」。
排查发现这不是同步的问题 —— **「媒体库同步」与首页「我的媒体库」是两条互不相干的链路**：

- 「媒体库同步」把条目写进 `media.db`，**只服务于**「该媒体是否已存在」的判断（搜索结果、详情页标记）；
- 首页「我的媒体库」的「正在观看」/「最新入库」/ 库卡片是**每次打开页面实时**从媒体服务器拉的。

真正的缺陷是首页这条链路上的三个问题。

## 缺陷 1：首页直接 500（缺 2 个方法 + 1 个转发契约）

`web/main.py:297/300` 分别调用 `MediaServer().get_resume()` 与 `MediaServer().get_latest()`，
但这两个方法**不在** `_IMediaClient` 的 19 个抽象方法里，`_base.py` 也**没有默认实现**，
v6.2.0 的飞牛客户端因此漏掉它们 ⇒ 调用直接 `AttributeError`，而那两行**没有 try 兜底** ⇒ **整个首页 500**。

另 `media_server.py` 会转发 `get_episode_image_by_id()`，同样缺失。

**修复**（新增 3 个方法）：

- `get_resume(num=12)`：走飞牛 `/play/list`，用 `ts` / `duration` 换算播放进度，`watched=1` 自动过滤；
- `get_latest(num=20)`：走飞牛 `/item/list` 按 `create_time` 倒序；**服务端不支持全库查询时自动退化为逐库取再合并**；
- `get_episode_image_by_id()`：补齐转发契约。

## 缺陷 2：所有封面图 404

飞牛接口返回的图片是**相对路径**（如 `media/img/x.jpg`），客户端此前直接拼 `host + path`，
少了 `/api/v1/sys/img` 前缀（MoviePilot `__build_img_api_url` 的口径）⇒ 图片全部取不到。

**修复**：新增幂等助手 `__abs_image_url()` 统一处理（已是绝对 URL 则原样返回，兼容 list 形态），
`get_remote_image_by_id` / `get_local_image_by_id` 改用它，并给 `get_libraries` 补充库封面 `image` / `image_list`。

## 缺陷 3：类型判定只认英文（存量 bug，影响面更大）

`media_server.py` 判断条目类型时写死了 `['Movie', 'movie']` / `['Series', 'show']`，
而 **ugreen 与飞牛客户端输出的是 `MediaType` 中文值**（「电影」/「电视剧」）⇒

- 首页统计的**电影数 / 剧集数恒为 0**；
- **剧集根本不会去调 `get_tv_episodes()`** ⇒ 剧集的季集信息为空 ⇒ 剧集「是否已存在」的判断永远失败。

**这是 v6.2.0 之前就存在的问题**（绿联影视同样中招），不是飞牛引入的。
**修复**：改用 `MediaDb.MOVIE_TYPES` / `TV_TYPES`（本身同时含中英文），与库侧口径统一。

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | +3 个方法（`get_resume` / `get_latest` / `get_episode_image_by_id`）+ 新增 `__abs_image_url` + 图片与库封面接线 |
| `app/mediaserver/media_server.py` | 类型判定由英文白名单改为 `MediaDb.MOVIE_TYPES` / `TV_TYPES` |

合计 2 文件 +216 / −4。

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| 抽象方法契约 | `_verify_trimemedia_contract.py` | 19 | ✓ |
| 声明 | `_verify_trimemedia.py` | 26 | ✓ |
| 运行时 | `_verify_trimemedia_runtime.py` | 20 | ✓ |
| 端到端（v6.2.0 既有） | `_verify_trimemedia_e2e.py` | 36 | ✓ |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ |
| **本次专项** | `_verify_trimemedia_fix.py` | **63** | ✓ 真实 HTTP 假服务端 + 服务端侧复算 authx，覆盖 `get_resume` / `get_latest` / 图片 URL / 库封面 |
| **类型判定** | `_verify_mediasync_type.py` | **21** | ✓ 加载真实 `media_server.py`，注入中 / 英文口径真跑同步 |

合计 **216 项断言，0 失败**。

其中最有分量的一条：**修复前中文口径下 `get_tv_episodes()` 完全不会被调用**；
修复后正确调用、统计 1 / 1，且 emby 的英文口径未受影响。

## 踩坑

- **「同步成功」≠「首页有内容」**：两者是不同链路（见「背景」），排查时别往同步方向使劲。
- **抽象方法契约会漏**：`_IMediaClient` 只列了 19 个方法，但 `MediaServer` 转发的方法**多于 19 个**
  （`get_resume` / `get_latest` / `get_episode_image_by_id` 都不在其中）⇒ 真正可靠的做法是
  **用脚本自动交叉核对「`MediaServer` 转发的方法」vs「客户端已实现的方法」**，而不是人肉数抽象方法。
- **`.git/packed-refs` 被文本模式写成 CRLF**：v6.2.0 推送后写跟踪引用时用了文本模式 `open(..., 'w')`，
  Windows 把 `\n` 全转成 `\r\n` ⇒ 之后**所有 git 命令**报 `fatal: unexpected line in .git/packed-refs`
  （push / fetch / ls-remote 全废），而 `git status` 竟也不报错（当时只用 REST 复核，REST 不读该文件）。
  ⇒ **`.git` 下的文件一律 `open(..., 'wb')` 二进制写**；已沉淀 `_fix_git_refs.py` 作为发版第 0 步预检。

## 兼容性

- 修复均为**补齐 / 放宽**，无破坏性变更；
- `media_server.py` 的类型判定改动**同时惠及绿联影视**（同一 bug 的另一受害者）；
- 存量 emby / jellyfin / plex 的英文口径**未受影响**（已实测）；
- 无数据库迁移、无接口变更、无新依赖。

---

# v6.2.0 (2026-09-30) — 媒体服务器接入「飞牛影视」

## 背景

飞牛 OS（fnOS）自带影视中心「飞牛影视」，内部代号 **TrimMedia**。MoviePilot 已支持接入，本项目参照其实现完整移植，使 nas-tools 也能把飞牛影视当作媒体服务器使用（媒体库展示 + 下载控重 + 入库后刷新媒体库）。

## 协议要点（三重认证）

| 层 | 说明 |
|---|---|
| API Key | 固定值 `16CCEB3D-AB42-077D-36A1-F355324E4237`，**参与签名**（非用户配置项） |
| authx 签名 | `MD5(盐_路径_nonce_ts_bodyHash_apikey)`，盐 = `NDzZTVxnRKP8Z0jXg1VAMonaG8akvh`；`api_path` 需以 `/v` 开头；GET 用**未 URL 编码**的 query 串算 bodyHash |
| Token | 登录响应下发，走 `Authorization` 头（**无 Bearer 前缀**） |
| 访问码 | 可选，`GET {设备根}/c/{code}`（**在设备根路径，不在 /v 下**），404 = 码错 |

登录优先 `POST /api/v2/user/loginByPassword`（密码传 **SHA256 hex 小写摘要**），v2 不存在才回退 v1 `POST /api/v1/login` 明文。

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | **新增** +1069（`_TrimeApi` 29 方法 + `TrimeMediaClient` 30 方法） |
| `app/conf/moduleconf.py` | +94（`MEDIASERVER_CONF` 新增 `trimemedia` 卡片，10 个字段） |
| `config/config.yaml` | +23（新增 `trimemedia:` 段） |
| `app/utils/types.py` | +1（`MediaServerType.TRIMEMEDIA = "飞牛影视"`） |
| `web/static/img/mediaserver/trimemedia.png` | **新增** 5234 bytes（512×512 图标） |

合计 5 文件 +1187 行 / **0 删除**。

## 前端零改动

`mediaserver.html` 与 `macro/form.html::gen_form_config_elements` 均为数据驱动（循环 `MEDIASERVER_CONF`），支持 `switch` / `select` / `text` / `password` / `textarea` 五种类型自动出控件 ⇒ 新增媒体服务器**不需要改任何前端文件**。

客户端通过 `SubmoduleHelper.import_submodules('app.mediaserver.client', filter_func=lambda _, obj: hasattr(obj, 'client_id'))` 自动发现，无需注册表；`__build_class` 用 `match(ctype)` 匹配类型。

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| L1 契约 | `_verify_trimemedia_contract.py` | 19 | ✓ `_IMediaClient` 19 个抽象方法全覆盖 |
| L2 声明 | `_verify_trimemedia.py` | 26 | ✓ 语法 / 卡片 / 字段 / 四处一致 / authx 算法自检 |
| L3 运行时 | `_verify_trimemedia_runtime.py` | 20 | ✓ 真实导入 + 发现契约 + **authx 与 MoviePilot 参考实现逐字符一致** |
| L4 端到端 | `_verify_trimemedia_e2e.py` | 36 | ✓ 本地假 HTTP 服务端跑通全链路，**42 个 `/v` 请求全部通过服务端侧 authx 校验** |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ emby / jellyfin / plex / ugreen 零影响 |

合计 **132 断言 + 19 契约项，0 失败**。

## 踩坑

- **MoviePilot 默认分支是 `v3` 不是 `main`** ⇒ 直接按 main 拉源码会 404。
- **`raw.githubusercontent.com` DNS 被封**（`getaddrinfo failed`），两个企业代理同时失效 ⇒ 改用 **Contents API**（`/repos/{owner}/{repo}/contents/{path}?ref=v3` 返 base64）拉源码。
- **`MediaType.MUSIC` 枚举不存在**：音乐库类别误用后会 `AttributeError`，**AST / `py_compile` 完全抓不到**，运行才炸 ⇒ 已改 `MediaType.UNKNOWN`，并加静态断言防回归。
- **`get_play_url` 类型两形态**：`item_info` 可能收到飞牛原始 dict（`type="Movie"`）或 `_build_item` 产物（`type="电影"`） ⇒ 新增 `_to_trime_type()` 归一化，否则恒走 `/other/` 分支。
- **假服务端要在服务端侧独立复算 authx**（客户端自算自比无意义），且服务端需对 query **先 `unquote` 再复算** —— 客户端用 requests `params` 交中文会被编码成 `%E6...`，与「未编码」口径不一致（真实飞牛服务端即此行为）。
- **`-14 Task duplicate`**：飞牛扫描接口在已有任务在跑时会拒绝 ⇒ 客户端先调 `task_running()` 规避。
- **图标背景色冲突**：飞牛初版用 `bg-purple` 与 jellyfin 重复 ⇒ 改 `bg-cyan`（未占用）。

## 兼容性

- **纯增量**：5 文件 +1187 行，**0 删除**；
- 存量 4 个媒体服务器（emby / jellyfin / plex / ugreen）的名称、背景色、`test_command`、枚举值、`config.yaml` 段**全部未变**（已逐项快照比对）；
- 无数据库迁移、无接口变更。

---

# v6.1.1 (2026-09-30) — 设置页「识别与搜索」区排版归组

## 症状（无头浏览器实测）

| 行 | 改动前行高 | 内容 |
|---|---|---|
| 1 | 36px | 4 个开关 |
| 2 | **80px** | 3 个开关 + 1 个数值输入框 |
| 3 | **80px** | 1 个数值输入框 + 2 个开关（右下角空一格） |

## 根因

1. **开关与输入框混排**：开关控件高 18px、数值输入框高 36px，同处一个网格 ⇒ 行高 36/80 交替，参差不齐；
2. **两个数值框分散**：分别落在第 2 行第 4 列与第 3 行第 1 列，同类控件对不齐；
3. **输入框挤占列位**：第 3 行只剩 3 个元素，右下角留白。

## 改动文件

### `web/templates/setting/basic.html`

把这一区**拆成两个语义分组**，各占一整个 `col-12`：

- **开关组**：外层 `col-12` + 内层 `row`，9 个开关各自 `col-12 col-sm-6 col-xl-3`；
- **数值组**：外层 `col-12` + 内层 `row`，2 个数值项各自 `col-12 col-md-6 col-xl-6`。

> 列类由原来的 `col-12 col-xl-3` 改为带 `sm`/`md` 断点的版本，使小屏自动降列。

## 测试

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `_verify_lab_layout.py` | **Jinja 真实渲染** + 11 个字段 id 完整性 + 列类统计 + div 配平 + 输入框数量不变 | 19 / 19 |
| `_measure_cdp.py` | 无头 Edge（CDP）实测 3 种视口下的行高与列宽 | 1400 / 1200 / 900px 均通过 |
| `_diff_lab_structure.py` | 改动前后 label / span / input / div 数量与 id 总数对比 | 全部一致（div 各 +4 且配平） |

另：`python -m compileall app web config.py version.py` 通过。

## 兼容性

- **纯排版改动**：不动任何 `id`、`name`、字段与保存逻辑；
- `label` / `span` / `input` 数量与改动前**完全一致**（已逐项比对）；
- 无数据库、配置、接口变更。

## 踩过的坑

- **无头 Edge 的 `--dump-dom` 不再等待脚本执行**（新版 headless）：用它取不到 JS 注入的量测结果，拿回的仍是原始 HTML。⇒ 改用 **CDP**（`--remote-debugging-port` + `Runtime.evaluate`），并补 `--remote-allow-origins=*` 否则 WebSocket 握手 403。
- **本机外网 CDN 不可达**：量测页改用项目自带的 `web/static/css/tabler.min.css`（与线上完全一致，且离线可跑）。
- **`open()` 默认会把 CRLF 归一为 LF**（universal newlines）⇒ 读 CRLF 模板做行切片时必须 `newline=''`，否则 `split('\r\n')` 只得到 1 行。
- **切片边界要数清 div 归属**：本区切到原第 1056 行（关闭最后一个 `col`）即可，结尾**不可**再补 `</div>`（关闭 `.row` 的那行本就在切片之外）——多补一个会导致 div 不配平，靠结构对比脚本发现。

---

# v6.0.9 (2026-09-29) — 媒体库同步「响应速度」优化 + 界面精简

## 症状

1. **点了没反应**：点首页「媒体库」打开同步弹窗后，界面要停留一小会儿才弹出来；
2. **进度条长时间不动**：点「开始同步」后，进度条会长时间停在「正在获取 XXX 数据...」；
3. **同步整体偏慢**：条目多时整个流程耗时明显。

## 根因（前端 2 处 + 后端 2 处）

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `index.html::show_mediasync_modal` | 弹窗在**两次串行 ajax**（`refresh_process` → `mediasync_state`）都返回后才 `modal('show')` | 服务器稍慢就「点了没反应」 |
| 2 | `index.html::start_media_sync` | 用 `setTimeout(..., 1000)` 延迟 1 秒才建立进度流 | 点「开始同步」后 1 秒内界面无任何反馈 |
| 3 | `media_server.py` | `items = list(self.get_items(lib_id) or [])` —— `get_items` 是生成器且**每条都要单独请求详情接口**，`list()` 必须等全部抓完才返回 | 进度条在整个媒体库抓完前纹丝不动 |
| 4 | `media_db.py` | 每条目一次 `commit()` | SQLite 每次 commit 都是一次 fsync，NAS 上尤其慢 |

## 改动文件

### `web/templates/index.html`

- `show_mediasync_modal`：**先 `modal('show')`**（带「正在获取同步状态...」占位），再异步取状态；
- `start_media_sync`：去掉 1 秒 `setTimeout`，改为**立即建立进度流**并显示「正在启动同步...」；
- SSE `onmessage`：仅在 `ret.text` 非空时覆盖文案，避免瞬时空值抹掉占位提示；
- 移除「全选 / 全不选」两个按钮与「不勾选任何库时…」提示行（保留「已选 N / M」计数与未勾选时的二次确认）。

### `app/mediaserver/media_server.py`

- 去掉 `list()` 预物化，改为 `for item in (self.get_items(lib_id) or []):` **边取边处理**，收一条就更新进度；
- 引入 `BATCH_SIZE = 50` 的写入缓冲，每批落盘后刷新一次进度，每个库收尾与全局结束各做一次兜底 flush；
- 条目级 `except` 中的 `ExceptionUtils` / `log` 调用再包一层 try/except，避免「异常处理器自身报错」逃逸出去中断整库循环。

### `app/db/media_db.py`

- 新增 `insert_batch(server_type, rows)`：一批一个事务，返回**实际写入条数**（跳过 `None` 条目）；
- 批内**每条用 `session.begin_nested()`（savepoint）隔离**：单条失败只回滚自己，其余照常入库 —— 否则「一条坏数据拖垮整批」会比旧版逐条提交更糟；
- `insert()` 保留（向后兼容）；新增 `import log`。

## 测试

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `_verify_speed_ui_609.py` | 源码断言 + 界面精简 + 即时响应 + AST 结构 | 27 / 27 |
| `_verify_insert_batch_609.py` | **真实 SQLite**：批量写入 / 去重 / 空值 / savepoint 隔离 / 缓存清理 / 类型收敛 | 14 / 14 |
| `_verify_mediasync_v608.py`（回归） | v6.0.8 五根因 + 端到端（桩已同步支持 `insert_batch`） | 51 / 51 |
| `_verify_mediasync_clients_v608.py`（回归） | emby 客户端失败路径 / 容器下钻 / 混合库 | 37 / 37 |
| `_verify_media_db_v608.py`（回归） | 类型收敛 / 缓存键 / 缓存隔离 | 28 / 28 |

另：`python -m compileall app web config.py version.py` 通过。

## 兼容性

- 数据库表结构、配置项均未变，无需迁移；
- `insert()` 保留，旧调用点不受影响；
- 批量提交不改变最终入库结果（去重语义、类型收敛、缓存清理均与逐条一致，已有真实 SQLite 用例覆盖）。

## 踩过的坑

- **批量事务会把「单条失败」放大成「整批丢失」**：旧版逐条 `commit` 时坏数据只影响自己，改成一批一提交后必须补 savepoint，否则退步。测试用「不可 JSON 序列化的 seasoninfo」成功复现。
- **异常处理器自己也可能抛异常**：`log.error(...)` / `ExceptionUtils.exception_traceback(...)` 若失败，会逃出内层 `except` 被外层捕获 → 触发 `rollback()` → 整批回滚。⇒ 异常处理路径必须自身兜底。
- **`core.autocrlf=true` 下 `git status` 会对 LF 工作树报 `LF will be replaced by CRLF`**：只影响下次 checkout 的工作树显示，**不影响仓库存储**（本仓远端存储为 LF，`README.md` 除外，为 CRLF）。

---

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
