#!/bin/bash
# ============================================
# 绿联 NAS 专用 nas-tools 构建脚本
# 包含绿联影视支持
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 镜像信息
IMAGE_NAME="nas-tools-ugreen"
IMAGE_TAG="latest"
FULL_IMAGE_NAME="${IMAGE_NAME}:${IMAGE_TAG}"

# 架构检测
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    PLATFORM="linux/amd64"
    ARCH_NAME="amd64"
elif [ "$ARCH" = "aarch64" ]; then
    PLATFORM="linux/arm64"
    ARCH_NAME="arm64"
else
    echo -e "${RED}不支持的架构: $ARCH${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  nas-tools 绿联版构建脚本${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "检测到架构: ${YELLOW}$ARCH ($PLATFORM)${NC}"
echo ""

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    exit 1
fi

# 获取当前目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo -e "${YELLOW}步骤 1/4: 检查代码修改...${NC}"
# 检查绿联影视文件是否存在
if [ -f "app/mediaserver/client/ugreen.py" ]; then
    echo -e "${GREEN}✓ 绿联影视客户端文件存在${NC}"
else
    echo -e "${RED}✗ 绿联影视客户端文件不存在，请先添加绿联支持${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}步骤 2/4: 构建 Docker 镜像...${NC}"
echo "这可能需要几分钟时间..."

# 构建镜像
docker build \
    --platform "$PLATFORM" \
    -f Dockerfile.custom \
    -t "$FULL_IMAGE_NAME" \
    .

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 镜像构建成功${NC}"
else
    echo -e "${RED}✗ 镜像构建失败${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}步骤 3/4: 导出镜像...${NC}"

# 导出镜像为 tar 文件（方便传输到绿联 NAS）
EXPORT_FILE="nas-tools-ugreen-${ARCH_NAME}-$(date +%Y%m%d).tar"
docker save -o "$EXPORT_FILE" "$FULL_IMAGE_NAME"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 镜像导出成功: $EXPORT_FILE${NC}"
    ls -lh "$EXPORT_FILE"
else
    echo -e "${RED}✗ 镜像导出失败${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}步骤 4/4: 生成部署文件...${NC}"

# 创建 docker-compose 文件
cat > docker-compose-ugreen.yml << 'EOF'
version: "3"
services:
  nas-tools:
    image: nas-tools-ugreen:latest
    container_name: nas-tools
    hostname: nas-tools
    ports:
      - 3000:3000
    volumes:
      # 配置文件目录（绿联 NAS 路径示例）
      - /volume1/docker/nas-tools/config:/config
      # 媒体库目录（根据你的实际路径修改）
      - /volume1/media:/media
      # 下载目录（根据你的实际路径修改）
      - /volume1/downloads:/downloads
    environment:
      - PUID=0
      - PGID=0
      - UMASK=022
      # 关闭自动更新（因为使用的是自定义镜像）
      - NASTOOL_AUTO_UPDATE=false
      - NASTOOL_CN_UPDATE=true
      - TZ=Asia/Shanghai
    restart: always
    network_mode: bridge
EOF

echo -e "${GREEN}✓ 部署文件已生成: docker-compose-ugreen.yml${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  构建完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "输出文件:"
echo -e "  - 镜像文件: ${YELLOW}$EXPORT_FILE${NC}"
echo -e "  - 部署配置: ${YELLOW}docker-compose-ugreen.yml${NC}"
echo ""
echo -e "部署到绿联 NAS 的步骤:"
echo -e "  1. 将 ${YELLOW}$EXPORT_FILE${NC} 上传到绿联 NAS"
echo -e "  2. 在绿联 NAS 上导入镜像:"
echo -e "     ${YELLOW}docker load -i $EXPORT_FILE${NC}"
echo -e "  3. 将 ${YELLOW}docker-compose-ugreen.yml${NC} 上传到绿联 NAS"
echo -e "  4. 修改 yml 文件中的路径配置"
echo -e "  5. 启动容器:"
echo -e "     ${YELLOW}docker-compose -f docker-compose-ugreen.yml up -d${NC}"
echo ""
echo -e "或者直接在绿联 Docker 管理器中:"
echo -e "  1. 镜像管理 -> 导入 -> 选择 ${YELLOW}$EXPORT_FILE${NC}"
echo -e "  2. 容器管理 -> 创建容器 -> 选择 nas-tools-ugreen 镜像"
echo -e "  3. 映射端口 3000 和必要的卷"
echo ""
