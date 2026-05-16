# nas-tools 绿联版

基于 [0xforee/nas-tools](https://github.com/0xforee/nas-tools) 的增强版本，**包含绿联影视支持**。

## 功能特性

- ✅ 绿联影视媒体服务器集成
- ✅ 支持绿联影视 API 认证
- ✅ 媒体库自动刷新
- ✅ 电影/电视剧搜索
- ✅ 剧集信息查询
- ✅ 最近更新展示

## 快速开始

### 方式一：使用 GitHub Actions 自动构建（推荐）

1. **Fork 本仓库**
   - 点击右上角 `Fork` 按钮

2. **启用 GitHub Actions**
   - 进入 `Actions` 标签页
   - 点击 `I understand my workflows, go ahead and enable them`

3. **触发构建**
   - 方式 A：手动触发
     - 进入 `Actions` → `Build Docker Image`
     - 点击 `Run workflow` → `Run workflow`
   - 方式 B：创建标签自动触发
     ```bash
     git tag v1.0.0
     git push origin v1.0.0
     ```

4. **下载镜像**
   - 构建完成后，在 `Actions` 页面下载构建产物
   - 或在 `Releases` 页面下载发布版本

### 方式二：本地构建

```bash
# 克隆仓库
git clone https://github.com/你的用户名/nas-tools-ugreen.git
cd nas-tools-ugreen

# 构建镜像
chmod +x build-docker-image.sh
./build-docker-image.sh
```

## 部署到绿联 NAS

### 1. 导入镜像

**方式 A：SSH 命令行**
```bash
# 上传镜像到绿联 NAS
scp nas-tools-ugreen-amd64.tar.gz root@绿联IP:/volume1/docker/

# SSH 登录后导入
ssh root@绿联IP
cd /volume1/docker
docker load -i nas-tools-ugreen-amd64.tar.gz
```

**方式 B：Docker 管理器**
1. 打开绿联 NAS 的 Docker 管理器
2. 进入「镜像管理」
3. 点击「导入」
4. 选择上传的 `.tar.gz` 文件

### 2. 创建容器

使用 docker-compose（推荐）：

```yaml
version: "3"
services:
  nas-tools:
    image: ghcr.io/你的用户名/nas-tools-ugreen:latest
    container_name: nas-tools
    hostname: nas-tools
    ports:
      - 3000:3000
    volumes:
      - /volume1/docker/nas-tools/config:/config
      - /volume1/media:/media
      - /volume1/downloads:/downloads
    environment:
      - PUID=0
      - PGID=0
      - UMASK=022
      - NASTOOL_AUTO_UPDATE=false
      - TZ=Asia/Shanghai
    restart: always
```

### 3. 配置绿联影视

编辑 `/volume1/docker/nas-tools/config/config.yaml`：

```yaml
media:
  media_server: ugreen

ugreen:
  host: http://192.168.1.100:9999  # 绿联 NAS 地址
  username: your_username           # 登录用户名
  password: your_password           # 登录密码
  play_host: http://192.168.1.100:9999
```

## 注意事项

1. **关闭二级认证**：绿联影视集成需要关闭二级认证
2. **端口配置**：HTTP 使用 9999，HTTPS 使用 9443
3. **架构选择**：绿联 DXP4800 Plus 使用 amd64 架构

## 文件说明

| 文件 | 说明 |
|------|------|
| `app/mediaserver/client/ugreen.py` | 绿联影视客户端实现 |
| `Dockerfile.custom` | 自定义 Docker 构建文件 |
| `build-docker-image.sh` | 本地构建脚本 |
| `.github/workflows/build-docker.yml` | GitHub Actions 自动构建配置 |

## 常见问题

### Q: 无法连接绿联影视？
A: 
- 确认 IP 和端口正确
- 确认用户名密码正确
- 确认已关闭二级认证

### Q: 镜像导入失败？
A: 
- 确认架构匹配（amd64/arm64）
- 尝试解压后导入 `.tar` 文件

## 致谢

- [0xforee/nas-tools](https://github.com/0xforee/nas-tools) - 原项目
- [jxxghp/nas-tools](https://github.com/jxxghp/nas-tools) - 上游项目

## License

AGPL-3.0
