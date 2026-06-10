APP_VERSION = 'v3.7.10'

# v3.7.10 更新日志
# - 绿联影视客户端重写为原生加密API（参考MoviePilot实现）
# - 修复绿联测试连接始终失败的问题
# - 修复媒体库同步/列表功能失败：
#   - get_libraries: 改用 media_list API
#   - get_items: 改用 poster_wall_get_folder 按目录树遍历
#   - get_tv_episodes: 改用 v2/video/details/getTV 获取剧集详情
#   - refresh_root_library: 遍历所有库逐个扫描
# - 绿联客户端使用 requests.Session 复用连接
# - config.yaml 添加绿联影视默认配置节点
# - Docker构建移除阿里云镜像源，改用官方源