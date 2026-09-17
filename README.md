# NAS 媒体库管理工具（NAS-Tools 增强版）

[![GitHub stars](https://img.shields.io/github/stars/carl800-1/nas-tools?style=plastic)](https://github.com/carl800-1/nas-tools/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/carl800-1/nas-tools?style=plastic)](https://github.com/carl800-1/nas-tools/network/members)
[![GitHub issues](https://img.shields.io/github/issues/carl800-1/nas-tools?style=plastic)](https://github.com/carl800-1/nas-tools/issues)
[![GitHub license](https://img.shields.io/github/license/carl800-1/nas-tools?style=plastic)](https://github.com/carl800-1/nas-tools/blob/main/LICENSE.md)
[![Docker pulls](https://img.shields.io/docker/pulls/carl800-1/nas-tools?style=plastic)](https://github.com/carl800-1/nas-tools/pkgs/container/nas-tools)
[![Platform](https://img.shields.io/badge/platform-amd64%20%7C%20arm64-pink?style=plastic)](https://github.com/carl800-1/nas-tools/pkgs/container/nas-tools)

> 当前版本：**v3.8.0** ｜ 镜像：`ghcr.io/carl800-1/nas-tools` ｜ 端口：`3000` ｜ 协议：AGPL-3.0

Docker 镜像：https://github.com/carl800-1/nas-tools/pkgs/container/nas-tools

TG 交流群：https://t.me/nastoolsolder ｜ TG 通知频道：https://t.me/ntolder_notify

---

## 目录

- [这是什么](#这是什么)
- [本分支增强点](#本分支增强点)
- [功能总览](#功能总览)
- [支持的平台与客户端](#支持的平台与客户端)
- [快速开始](#快速开始)
- [目录规划与挂载（重要）](#目录规划与挂载重要)
- [首次配置](#首次配置)
- [环境变量](#环境变量)
- [典型使用流程](#典型使用流程)
- [插件](#插件)
- [升级与维护](#升级与维护)
- [常见问题](#常见问题)
- [开发与构建](#开发与构建)
- [免责声明与许可](#免责声明与许可)

---

## 这是什么

NAS-Tools 是一套运行在 NAS / 服务器上的 **媒体库自动化管理工具**，把「找资源 → 下载 → 整理重命名 → 入库刮削 → 通知」整条链路串起来，替代大量手工操作。

一句话概括它能做什么：

```
订阅 / 搜索  →  站点与索引器检索  →  择优下载  →  联网识别重命名  →  硬链接/复制入库  →  通知媒体服务器刷新
```

典型使用场景：

- 想看某部电影 / 某部剧，在「订阅管理」里加一条，之后更新自动下载、自动整理入库；
- 已有的历史下载文件，用「媒体整理 → 手动识别 / 目录同步」批量重命名归档到规范目录；
- 用「站点管理 → 刷流任务」在 PT 站做保种、限免监控、自动删种，提升分享率；
- 作为统一入口：从面板直接跳到下载器、索引器、媒体服务器的对应页面。

本仓库基于官方主线 **nas-tools** 演进而来（版本号与配置均延续主线，**可直接迁移**），在保留官方数据结构的前提下补充了一批实用能力，详见下一节。

## 本分支增强点

### 1. 刷流（Brush）能力强化

| 能力 | 说明 |
|---|---|
| 部分下载（拆包） | 可灵活配置下载的种子大小与比例，最大化提升刷流效率。**部分站点可能禁止，请慎用** |
| 限免到期检测 | 超过限免时间自动把速度降为 1B/s，防止流量偷跑（默认关闭；已测试 mteam、hdmayi） |
| 界面信息补充 | 展示已保种大小；种子明细增加种子大小、限免过期时间 |
| 刷流限制解除 | 取消新手刷流限制（`pt.force_enable_brush` 可强制开启） |

### 2. 索引能力扩展

- **保留 BT 能力与内置 BT 站点**，可继续索引和下载 BT 磁链、种子文件；
- **支持 Jackett 与 Prowlarr 索引器**，可与内置索引器并存，通过插件启用；
- **完美支持 MTeam 新架构**：站点配置里填 `api-key`（馒头 → 控制台 → 实验室 → 存取令牌）即可；
  - 站点签到同样支持，可在控制台的「登录设备活动记录」中核对签到（登录）记录。

### 3. 跳转与入口优化

方便把 NAS-Tools 当作媒体管理主入口：

- 下载管理 → 正在下载：可直接跳转下载器查看种子详情；
- 我的媒体库：标题可跳转对应媒体库；
- 索引器：Jackett / Prowlarr 可跳转各自服务；
- 媒体服务器：每个服务器配置界面可直接跳转该服务。

### 4. 飞书消息通知（双向交互）

基于 `lark-oapi` 的 WebSocket 长连接接入飞书自建应用，**无需公网 IP、域名或内网穿透**：

| 能力 | 说明 |
|---|---|
| 通知推送 | 下载、入库、订阅、签到、刷流、站点消息、媒体服务器等全部推送开关 |
| 交互式搜索 | 在飞书中直接发送关键字搜索站点资源，回复序号即可下载或订阅 |
| 管理命令 | 支持 `/` 开头命令，支持用户白名单与管理员白名单（基于 `open_id`） |
| 卡片消息 | 自动上传并展示海报图片，附带「查看详情」跳转按钮 |

### 5. 其他

- 优化用户认证与权限分级（WEB 登录 + 用户管理）；
- 优化新手刷流体验；
- 页脚仅展示版本号（不再附带提交短哈希，v3.8.0 起）。

> 历史增量说明见 [diff.md](diff.md)，版本变更见 [CHANGELOG.md](CHANGELOG.md)。

## 功能总览

界面按左侧菜单组织，主要模块如下：

| 模块 | 子功能 | 说明 |
|---|---|---|
| **我的媒体库** | 首页统计 | 媒体库概览、最近入库、统计图表 |
| **探索** | 榜单推荐、豆瓣电影/电视剧、TMDB 电影/电视剧、BANGUMI、资源搜索 | 发现内容并一键订阅或搜索下载 |
| **站点管理** | 站点维护、数据统计、刷流任务、站点资源 | PT 站点维护、做种/刷流、站点资源浏览 |
| **订阅管理** | 电影订阅、电视剧订阅、自定义订阅、订阅日历 | 按条件自动追更，支持洗版、集数过滤 |
| **下载管理** | 正在下载、近期下载、自动删种、媒体整理、文件管理、手动识别、历史记录、TMDB 缓存 | 下载监控与文件整理归档全流程 |
| **服务** | 插件、系统设置 | 插件启停与全部系统配置 |
| **系统设置** | 基础设置、用户管理、媒体库、目录同步、消息通知、过滤规则、自定义识别词、索引器、下载器、媒体服务器、电影/电视剧订阅、系统进程、备份&恢复 | 配置中心 |

其他常用工具：「订阅搜索」「下载文件转移」「目录同步」「清理转移缓存」「清理 RSS 缓存」「名称识别测试」「过滤规则测试」「网络连通性测试」「备份&恢复」。

### 核心能力清单

- **资源检索**：内置 PT 站点蜘蛛 + MTeam / TNode / TorrentLeech / 海胆等专用客户端；Jackett、Prowlarr；内置 BT 站点（磁链 / 种子）
- **自动订阅**：电影 / 电视剧 / 自定义订阅，支持质量、分辨率、过滤规则、「与」关系判定，支持洗版覆盖
- **下载管理**：多下载器并行，标签隔离（`NASTOOL` 标签识别自己的种子）
- **文件整理**：联网识别（TMDB / 豆瓣 / Bangumi），按命名格式重命名，支持 NFO 与海报生成
- **转移方式**：硬链接、软链接、复制、移动；支持二级分类（`default-category` 等策略文件）
- **目录同步**：基于 inotify 的实时同步 + 定时目录同步插件
- **媒体服务器联动**：下载前控重、入库后刷新媒体库、播放记录参与订阅判断
- **消息通知**：全流程可推送，支持远程「点击选择下载」
- **辅助插件**：自动签到、自动删种、IYUU 自动辅种、CookieCloud、Cloudflare 优选 IP、磁盘空间清理、自定义 Hosts 等
- **外部集成**：Jellyseerr / Overseerr 订阅接口（API Key 认证）、微信 / Telegram 交互

## 支持的平台与客户端

| 类别 | 支持列表 |
|---|---|
| **下载器** | qBittorrent、Transmission、Aria2、115 网盘、PikPak |
| **媒体服务器** | Emby、Jellyfin、Plex、绿联影视（UGREEN） |
| **索引器** | 内置 PT 站点、MTeam、TNode、TorrentLeech、海胆、Jackett、Prowlarr、内置 BT |
| **元数据源** | TMDB、豆瓣、Bangumi、Fanart |
| **字幕** | 站点字幕下载、ChineseSubFinder、OpenSubtitles、AutoSub |
| **消息通知** | 飞书（长连接，支持双向交互）、Telegram、微信（企业应用）、Bark、Gotify、ntfy、PushDeer、PushPlus、ServerChan、Slack、Synology Chat、Chanify、IYUU、Webhook |
| **运行平台** | Docker（amd64 / arm64）、Linux、Windows；Python 3.10 |

## 快速开始

### 方式一：Docker（推荐）

```bash
docker pull ghcr.io/carl800-1/nas-tools:latest
```

**docker compose**（推荐，新建 `docker-compose.yaml` 后 `docker compose up -d`）：

```yaml
version: "3"
services:
  nas-tools:
    image: ghcr.io/carl800-1/nas-tools:latest
    ports:
      - 3000:3000                     # WEBUI 端口
    volumes:
      - ./config:/config              # 配置与数据库持久化目录
      - /你的媒体目录:/容器内目录      # 媒体目录，多个目录分别映射
    environment:
      - PUID=0
      - PGID=0
      - UMASK=000
      - NASTOOL_AUTO_UPDATE=false     # 启动时自动拉取程序更新
    restart: always
    network_mode: bridge
    hostname: nas-tools
    container_name: nas-tools
```

**docker cli**：

```bash
docker run -d \
    --name nas-tools \
    --hostname nas-tools \
    -p 3000:3000 \
    -v $(pwd)/config:/config \
    -v /你的媒体目录:/你想设置的容器内能见到的目录 \
    -e PUID=0 -e PGID=0 -e UMASK=000 \
    -e NASTOOL_AUTO_UPDATE=false \
    -e NASTOOL_CN_UPDATE=true \
    --restart=always \
    ghcr.io/carl800-1/nas-tools:latest
```

创建容器 → 按 [首次配置](#首次配置) 修改 `config/config.yaml` → 重启容器 → 浏览器访问 `http://<ip>:3000`。

> 详细部署说明（PUID/PGID 选择、镜像特点、更新机制）见 [docker/readme.md](docker/readme.md)。
>
> 网络访问 GitHub 困难时：不要开启自动更新（`NASTOOL_AUTO_UPDATE=false`），并把 `NASTOOL_CN_UPDATE` 设为 `true` 以使用国内源加速依赖安装。

### 方式二：本地运行

需要 **Python 3.10**，并预先安装 `cython`（部分依赖需要编译）：

```bash
git clone --recurse-submodule https://github.com/carl800-1/nas-tools.git
cd nas-tools
python3 -m pip install -r requirements.txt

export NASTOOL_CONFIG="/xxx/config/config.yaml"
nohup python3 run.py &
```

Windows 下 `run.py` 会在程序目录下自动定位 / 生成 `config` 目录与 `config.yaml`。若提示缺少依赖包，按报错额外安装。

## 目录规划与挂载（重要）

正确规划目录是「硬链接不跨盘」和「媒体库识别」的前提，**建议第一次部署就按此结构规划**。

```yaml
# config/config.yaml → media 段（关键项）
media:
  movie_path:          # 电影库（可多个，不同硬盘映射为不同根目录）
  tv_path:             # 电视剧库（可多个）
  anime_path:          # 动漫库（可选，独立存放动漫剧集）
  unknown_path:        # 未识别文件兜底目录（跨盘时需分别配置）
  category: default-category   # 二级分类策略，留空则不分二级
  media_server: emby           # emby / jellyfin / plex，建议配置
  default_rmt_mode: copy       # 默认转移方式：copy/link/softlink/move
  movie_name_format: '{title} ({year})/{title}-{part} ({year}) - {videoFormat}'
  nfo_poster: false            # 转移时生成 nfo 与海报
  min_filesize: 150            # 转移最小文件大小（MB）
```

**规则要点**

1. **下载目录与媒体库目录必须映射进容器**，且下载目录不要位于媒体库目录之下（否则会重复转移）。
2. **硬链接不跨盘**：转移前后的路径必须位于同一文件系统。群晖中不同的「共享文件夹」会被视为跨盘；多硬盘时请为每块盘分别配置 `movie_path` / `tv_path`。
3. **目录名尽量用英文**，避免 `#`、`&`、空格等特殊字符带来的转义问题。
4. 使用 `move`（移动）转移方式时，源文件会被搬走；若同时有多个触发器（下载器监控、目录同步、手动整理）覆盖同一目录，容易触发重复转移的报错，详见 [常见问题](#常见问题)。

<a id="配置"></a>

## 首次配置

1. **配置 TMDB API Key（必须）**
   在 https://www.themoviedb.org/ 申请后填入 `config/config.yaml → app.rmt_tmdbkey`。**不配置将无法识别媒体资源、无法重命名。**
   需保证网络可访问 `api.themoviedb.org`、`webservice.fanart.tv`；无法访问时把 `app.tmdb_domain` 设为 `api.tmdb.org`，或在 `app.proxies` 中配置代理。

2. **修改登录账号**
   `app.login_user`（默认 `admin`）与 `app.login_password`（默认 `password`）。**务必修改默认密码。**

3. **设置外网访问地址**
   系统设置 → 基础设置 → 系统 → 外网访问地址。否则消息通知里的「点击选择下载」等跳转链接不可用。

4. **添加下载器与媒体服务器**
   系统设置 → 下载器 / 媒体服务器，逐个测试连通性后启用。媒体服务器建议配置，用于下载控重与媒体库展示。

5. **配置站点与索引器**
   系统设置 → 索引器，配置站点 Cookie / API Key；使用 Jackett / Prowlarr 时在「插件」中启用对应插件。

6. **配置消息通知**
   系统设置 → 消息通知，按需启用渠道并点「测试」。

7. **开启订阅服务周期**
   系统设置 → 基础设置 → 服务，启用「订阅 RSS 周期」与「订阅搜索周期」；否则新增订阅会一直停留在队列中。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PUID` | `0` | 运行程序的用户 uid（应与媒体文件属主一致） |
| `PGID` | `0` | 运行程序的用户 gid |
| `UMASK` | `000` | 文件权限掩码，可考虑设为 `022` |
| `NASTOOL_CONFIG` | `/config/config.yaml` | 配置文件路径 |
| `NASTOOL_AUTO_UPDATE` | `false` | 启动容器时自动拉取程序更新 |
| `NASTOOL_CN_UPDATE` | `true` | 使用国内源加速依赖 / 程序更新 |
| `NASTOOL_VERSION` | `main` | 拉取更新的分支 / 版本 |
| `REPO_URL` | 本仓库地址 | 自动更新使用的仓库地址，网络差时可换成代理地址 |
| `TZ` | `Asia/Shanghai` | 时区 |

> `PUID`/`PGID` 说明：在宿主机上以媒体文件属主身份执行 `id -u` / `id -g` 获取。若同时使用 Emby、Jellyfin、qBittorrent、Transmission 等镜像，**请保持 PUID/PGID 一致**，否则会出现权限问题。

## 典型使用流程

1. **配置就绪** → 系统设置里把下载器、媒体服务器、索引器、通知逐个点「测试」通过。
2. **加订阅** → 探索页选片或搜索资源，加入「电影/电视剧订阅」；也可在订阅列表手工新增。
3. **自动下载** → 到达 RSS / 搜索周期后，程序按质量、分辨率、过滤规则择优下载到下载器。
4. **自动整理** → 下载完成后（下载器监控或目录同步触发）自动识别、重命名并转移入库。
5. **入库通知** → 通知媒体服务器刷新媒体库，并推送通知消息。
6. **历史文件补整理** → 下载管理 → 媒体整理 → 手动识别，或配置「目录同步」批量整理存量文件。
7. **PT 维护（可选）** → 站点管理 → 刷流任务做保种；插件启用自动签到、自动删种、IYUU 辅种。

## 插件

系统设置 → 插件，按需启用（共 30+ 个）：

| 分类 | 插件 |
|---|---|
| **站点 / 签到** | 站点自动签到、Cloudflare 优选 IP、CookieCloud、自定义 Hosts |
| **索引器** | Jackett、Prowlarr |
| **整理 / 同步** | 定时目录同步、自动备份、媒体库刮削、媒体库刷新、媒体库归档、下载器辅助、下载文件转移 |
| **订阅 / 探索** | 豆瓣榜单、豆瓣同步、随机电影、猜你喜欢、MTeam 刷流 RSS 生成 |
| **字幕** | 中文字幕查找、OpenSubtitles、AutoSub |
| **保种 / 清理** | 自动删种、IYUU 自动辅种、限速、种子标记、磁盘空间清理 |
| **通知 / 联动** | Webhook、媒体库删除同步、自定义、自定义发布组 |

## 升级与维护

**Docker 更新程序（不换镜像）**

- 设置 `NASTOOL_AUTO_UPDATE=true` 后，**重启容器**即会自动拉取最新程序；
- 若启动日志提示「更新失败，继续使用旧的程序来启动...」，再重启一次；持续失败说明网络不通；
- 若提示「无法安装依赖，请更新镜像...」，需删除旧容器与旧镜像，重新 `pull` 后重建容器。

**换新版本镜像**

```bash
docker pull ghcr.io/carl800-1/nas-tools:3.8.0   # 也可继续用 latest
docker compose up -d
```

**备份**：`config/` 目录包含全部配置与数据库（`config.yaml`、`user.db`、`media.db`、`logs/`），**直接备份该目录即可**；也可用「系统设置 → 备份&恢复」。

## 常见问题

完整列表见 [Q&A.md](Q&A.md)，高频问题速览：

| 现象 | 处理方向 |
|---|---|
| 启动报 inotify 相关错误 / 目录同步不自动 | 宿主机提高 inotify 限制（见 Q&A 1），或改用「定时目录同步」插件 |
| 启动报数据库 `no such column` | 用 SQLite 工具删除 `user.db` 中的 `alembic_version` 表后重启 |
| 消息通知无法跳转 | 设置「外网访问地址」 |
| 订阅一直队列中 | 启用订阅 RSS / 搜索周期，或手动点「刷新」 |
| 目录同步出现重复转移文件 | 同步的目的目录不要在源目录之下 |
| 识别转移错误码 `-1` | 多为硬链接跨盘；群晖中不同共享文件夹视为跨盘 |
| 电视剧未完结就被删订阅 | TMDB 未更新集数或资源集数识别失败，可在订阅中指定总集数 |
| 转移日志出现「未找到文件 / FileNotFoundError」 | 同一源文件被多个触发器重复处理（文件通常已转移成功，属非致命报错）。请检查是否有下载器监控与目录同步同时覆盖同一目录，并统一转移方式 |

排查建议：把 `app.loglevel` 调为 `debug` 查看详细日志；「服务 → 系统进程」可观察调度任务状态。

## 开发与构建

- **技术栈**：Python 3.10 + Flask + SQLite（SQLAlchemy / Alembic）+ 原生 Web Components 前端
- **主要目录**

  ```
  app/           后端业务（downloader / indexer / filetransfer / media / sites / plugins ...）
  web/           Web 服务（Flask 蓝图、Jinja 模板、静态前端组件）
  config/        配置与数据库（运行时生成）
  docker/        Dockerfile、compose、rootfs 启动脚本
  scripts/       脚本与数据库迁移（alembic）
  tests/         测试用例
  third_party/   第三方子模块（feapder）
  package/       本地打包（PyInstaller spec 等）
  ```

- **CI/CD**：推送 `version.py` 变更即触发 [`.github/workflows/build.yml`](.github/workflows/build.yml)，自动构建 `linux/amd64` + `linux/arm64` 镜像并推送到 GHCR（标签：版本号 + `latest`），同时基于 `CHANGELOG.md` 创建 GitHub Release。
- **发版流程**：改 `version.py` → 更新 `CHANGELOG.md` → 提交推送，镜像与 Release 由 CI 产出。

## 免责声明与许可

1. 本软件**不提供任何内容**，仅作为辅助工具简化用户手工操作，对用户的行为及内容毫不知情；使用本软件产生的任何责任需由使用者本人承担。
2. 本软件代码开源，基于开源代码进行修改、人为去除相关限制导致软件被分发、传播并造成责任事件的，需由代码修改发布者承担全部责任。
3. 按 **AGPL-3.0** 协议要求，基于此软件代码的**所有修改必须开源**，详见 [LICENSE.md](LICENSE.md)。
4. 所有搜索结果均来自源站，本软件不承担任何责任。
5. 本软件仅供学习交流，请保持低调，勿公开传播。

---

_Last updated: 2026-09-17 ｜ 当前版本 v3.8.0_
