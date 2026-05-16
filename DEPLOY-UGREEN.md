# 绿联 NAS Docker 镜像构建与部署指南

本文档说明如何将修改后的 nas-tools（包含绿联影视支持）打包成 Docker 镜像，并部署到绿联 NAS DXP4800 Plus。

## 目录结构

```
nas-tools/
├── Dockerfile.custom          # 自定义镜像构建文件
├── build-for-ugreen.sh        # 一键构建脚本
├── docker-compose-ugreen.yml  # 绿联 NAS 部署配置（构建后生成）
├── DEPLOY-UGREEN.md           # 本文档
├── app/
│   └── mediaserver/
│       └── client/
│           └── ugreen.py      # 绿联影视客户端实现
├── config/
│   └── config.yaml            # 配置文件模板
└── ...
```

## 前置要求

1. **Docker 环境**：用于构建镜像
2. **绿联 NAS**：DXP4800 Plus 或其他支持 Docker 的型号
3. **代码修改已完成**：已添加绿联影视支持（ugreen.py 等文件）

## 构建步骤

### 方法一：使用一键构建脚本（推荐）

1. **进入项目目录**
   ```bash
   cd /workspace/nas-tools
   ```

2. **运行构建脚本**
   ```bash
   chmod +x build-for-ugreen.sh
   ./build-for-ugreen.sh
   ```

3. **等待构建完成**
   - 脚本会自动检测架构（amd64/arm64）
   - 构建 Docker 镜像
   - 导出为 tar 文件
   - 生成 docker-compose 配置文件

4. **输出文件**
   - `nas-tools-ugreen-amd64-YYYYMMDD.tar` 或 `nas-tools-ugreen-arm64-YYYYMMDD.tar`
   - `docker-compose-ugreen.yml`

### 方法二：手动构建

1. **构建镜像**
   ```bash
   cd /workspace/nas-tools
   docker build -f Dockerfile.custom -t nas-tools-ugreen:latest .
   ```

2. **导出镜像**
   ```bash
   docker save -o nas-tools-ugreen.tar nas-tools-ugreen:latest
   ```

3. **压缩（可选，减小传输体积）**
   ```bash
   gzip nas-tools-ugreen.tar
   ```

## 部署到绿联 NAS

### 方式一：通过 SSH 命令行（推荐）

1. **上传镜像文件**
   ```bash
   # 从电脑上传到绿联 NAS
   scp nas-tools-ugreen-amd64-20250101.tar root@192.168.1.100:/volume1/docker/
   ```

2. **SSH 登录绿联 NAS**
   ```bash
   ssh root@192.168.1.100
   ```

3. **导入镜像**
   ```bash
   cd /volume1/docker
   docker load -i nas-tools-ugreen-amd64-20250101.tar
   ```

4. **创建配置目录**
   ```bash
   mkdir -p /volume1/docker/nas-tools/config
   ```

5. **上传并修改 docker-compose 文件**
   ```bash
   # 编辑 docker-compose-ugreen.yml，修改路径为你的实际路径
   vi docker-compose-ugreen.yml
   ```

6. **启动容器**
   ```bash
   docker-compose -f docker-compose-ugreen.yml up -d
   ```

### 方式二：通过绿联 Docker 管理器（图形界面）

1. **导入镜像**
   - 打开绿联 NAS 的 Docker 管理器
   - 进入「镜像管理」
   - 点击「导入」按钮
   - 选择上传的 `nas-tools-ugreen-xxx.tar` 文件
   - 等待导入完成

2. **创建容器**
   - 进入「容器管理」
   - 点击「创建容器」
   - 选择导入的 `nas-tools-ugreen` 镜像

3. **配置容器**
   - **名称**：nas-tools
   - **端口映射**：主机端口 3000 → 容器端口 3000
   - **卷映射**：
     - `/volume1/docker/nas-tools/config` → `/config`
     - `/volume1/media` → `/media`（你的媒体库路径）
     - `/volume1/downloads` → `/downloads`（你的下载路径）
   - **环境变量**：
     - `PUID=0`
     - `PGID=0`
     - `UMASK=022`
     - `NASTOOL_AUTO_UPDATE=false`
     - `TZ=Asia/Shanghai`

4. **启动容器**
   - 点击「应用」或「启动」

## 配置绿联影视

1. **访问 nas-tools**
   - 浏览器打开 `http://绿联NAS-IP:3000`
   - 默认账号：admin
   - 默认密码：password

2. **修改配置文件**
   编辑 `/volume1/docker/nas-tools/config/config.yaml`：

   ```yaml
   media:
     # 选择绿联影视作为媒体服务器
     media_server: ugreen
   
   # 绿联影视配置
   ugreen:
     # 绿联 NAS 地址（端口 9999 是绿联影视的默认端口）
     host: http://192.168.1.100:9999
     # 绿联 NAS 登录用户名
     username: your_username
     # 绿联 NAS 登录密码（需要关闭二级认证）
     password: your_password
     # 播放地址
     play_host: http://192.168.1.100:9999
   ```

3. **重启容器**
   ```bash
   docker restart nas-tools
   ```

## 注意事项

### 关于绿联影视 API

- **端口**：绿联影视 API 默认使用 9999（HTTP）或 9443（HTTPS）
- **认证**：需要使用用户名/密码登录获取 token
- **二级认证**：必须关闭，否则无法连接
- **功能限制**：绿联影视暂不支持 Webhook、播放会话等功能

### 关于自动更新

由于使用的是自定义镜像（包含绿联影视修改），建议：
- 设置 `NASTOOL_AUTO_UPDATE=false`
- 如需更新，重新构建镜像并部署

### 关于架构

- DXP4800 Plus 是 x86_64 架构（amd64）
- 确保在相同架构上构建镜像，或启用 Docker 跨架构构建

## 故障排查

### 1. 容器无法启动

```bash
# 查看日志
docker logs nas-tools

# 检查配置文件权限
ls -la /volume1/docker/nas-tools/config/
```

### 2. 无法连接绿联影视

- 确认绿联 NAS IP 和端口正确
- 确认用户名/密码正确
- 确认已关闭二级认证
- 测试访问：`curl http://绿联IP:9999`

### 3. 镜像导入失败

- 检查镜像文件完整性
- 确认架构匹配（amd64/arm64）
- 尝试重新导出镜像

## 更新镜像

当代码有更新时，需要重新构建镜像：

1. **更新代码**
   ```bash
   cd /workspace/nas-tools
   git pull origin master
   ```

2. **重新构建**
   ```bash
   ./build-for-ugreen.sh
   ```

3. **重新部署**
   - 停止旧容器：`docker stop nas-tools && docker rm nas-tools`
   - 导入新镜像
   - 启动新容器

## 参考链接

- [绿联 NAS 官方文档](https://www.ugnas.com/)
- [nas-tools 原版项目](https://github.com/0xforee/nas-tools)
- [Docker 官方文档](https://docs.docker.com/)
