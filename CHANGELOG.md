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
