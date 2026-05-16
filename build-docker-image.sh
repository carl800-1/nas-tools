#!/bin/bash
# ============================================
# 绿联 NAS 专用 nas-tools Docker 镜像构建脚本
# 包含绿联影视支持
# ============================================
# 
# 使用说明：
# 1. 确保已安装 Docker
# 2. 解压 nas-tools-ugreen-20260511.tar.gz
# 3. 进入 nas-tools 目录
# 4. 运行此脚本: chmod +x build-docker-image.sh && ./build-docker-image.sh
#
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 镜像信息
IMAGE_NAME="nas-tools-ugreen"
IMAGE_TAG="latest"
FULL_IMAGE_NAME="${IMAGE_NAME}:${IMAGE_TAG}"

echo -e "${GREEN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║       nas-tools 绿联版 Docker 镜像构建脚本                  ║"
echo "║            (包含绿联影视支持)                               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 检查 Docker
echo -e "${YELLOW}[1/6] 检查 Docker 环境...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装！${NC}"
    echo ""
    echo "请先安装 Docker:"
    echo "  - Windows/Mac: https://www.docker.com/products/docker-desktop"
    echo "  - Linux: curl -fsSL https://get.docker.com | sh"
    exit 1
fi
echo -e "${GREEN}✓ Docker 已安装: $(docker --version)${NC}"

# 架构检测
echo ""
echo -e "${YELLOW}[2/6] 检测系统架构...${NC}"
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    PLATFORM="linux/amd64"
    ARCH_NAME="amd64"
    echo -e "${GREEN}✓ 检测到 x86_64 架构 (amd64)${NC}"
    echo -e "  绿联 DXP4800 Plus 使用此架构"
elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    PLATFORM="linux/arm64"
    ARCH_NAME="arm64"
    echo -e "${GREEN}✓ 检测到 ARM64 架构${NC}"
else
    echo -e "${RED}不支持的架构: $ARCH${NC}"
    exit 1
fi

# 获取当前目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 检查必要文件
echo ""
echo -e "${YELLOW}[3/6] 检查必要文件...${NC}"
REQUIRED_FILES=(
    "Dockerfile.custom"
    "app/mediaserver/client/ugreen.py"
    "app/utils/types.py"
    "config/config.yaml"
    "docker/rootfs"
)

ALL_FILES_EXIST=true
for file in "${REQUIRED_FILES[@]}"; do
    if [ -e "$file" ]; then
        echo -e "${GREEN}  ✓ $file${NC}"
    else
        echo -e "${RED}  ✗ $file (缺失)${NC}"
        ALL_FILES_EXIST=false
    fi
done

if [ "$ALL_FILES_EXIST" = false ]; then
    echo -e "${RED}错误: 缺少必要文件！${NC}"
    exit 1
fi

# 检查绿联影视文件
echo ""
echo -e "${YELLOW}[4/6] 验证绿联影视支持...${NC}"
if grep -q "UGREEN" app/utils/types.py; then
    echo -e "${GREEN}✓ 绿联影视类型枚举已添加${NC}"
else
    echo -e "${RED}✗ 绿联影视类型枚举未找到${NC}"
    exit 1
fi

if [ -f "app/mediaserver/client/ugreen.py" ]; then
    echo -e "${GREEN}✓ 绿联影视客户端文件存在${NC}"
else
    echo -e "${RED}✗ 绿联影视客户端文件不存在${NC}"
    exit 1
fi

# 构建 Docker 镜像
echo ""
echo -e "${YELLOW}[5/6] 构建 Docker 镜像...${NC}"
echo -e "${BLUE}这可能需要 10-20 分钟，请耐心等待...${NC}"
echo ""

docker build \
    --platform "$PLATFORM" \
    -f Dockerfile.custom \
    -t "$FULL_IMAGE_NAME" \
    --progress=plain \
    .

if [ $? -ne 0 ]; then
    echo -e "${RED}✗ 镜像构建失败！${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✓ 镜像构建成功！${NC}"

# 导出镜像
echo ""
echo -e "${YELLOW}[6/6] 导出镜像文件...${NC}"
EXPORT_FILE="nas-tools-ugreen-${ARCH_NAME}-$(date +%Y%m%d).tar"

echo -e "${BLUE}正在导出镜像到: $EXPORT_FILE${NC}"
echo -e "${BLUE}镜像文件较大（约 500MB-1GB），请稍候...${NC}"
echo ""

docker save -o "$EXPORT_FILE" "$FULL_IMAGE_NAME"

if [ $? -ne 0 ]; then
    echo -e "${RED}✗ 镜像导出失败！${NC}"
    exit 1
fi

# 压缩镜像（可选）
echo ""
read -p "是否压缩镜像文件以减小体积？(y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}压缩中...${NC}"
    gzip -f "$EXPORT_FILE"
    EXPORT_FILE="${EXPORT_FILE}.gz"
    echo -e "${GREEN}✓ 已压缩为: $EXPORT_FILE${NC}"
fi

# 显示文件信息
echo ""
echo -e "${GREEN}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                    构建完成！                              ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

FILE_SIZE=$(ls -lh "$EXPORT_FILE" | awk '{print $5}')
echo -e "镜像文件: ${YELLOW}$EXPORT_FILE${NC}"
echo -e "文件大小: ${YELLOW}$FILE_SIZE${NC}"
echo ""

# 生成 docker-compose 文件
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
      - NASTOOL_AUTO_UPDATE=false
      - NASTOOL_CN_UPDATE=true
      - TZ=Asia/Shanghai
    restart: always
    network_mode: bridge
EOF

echo -e "部署配置: ${YELLOW}docker-compose-ugreen.yml${NC}"
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署到绿联 NAS 的步骤:${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "1. 将 ${YELLOW}$EXPORT_FILE${NC} 上传到绿联 NAS"
echo ""
echo -e "2. SSH 登录绿联 NAS，导入镜像:"
echo -e "   ${YELLOW}docker load -i $EXPORT_FILE${NC}"
echo ""
echo -e "3. 或在绿联 Docker 管理器中:"
echo -e "   镜像管理 → 导入 → 选择 ${YELLOW}$EXPORT_FILE${NC}"
echo ""
echo -e "4. 配置绿联影视 (编辑 config.yaml):"
echo -e "   ${YELLOW}media:"
echo -e "     media_server: ugreen"
echo -e "   ugreen:"
echo -e "     host: http://绿联IP:9999"
echo -e "     username: 你的用户名"
echo -e "     password: 你的密码${NC}"
echo ""
