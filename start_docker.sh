#!/bin/bash

# 设置颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "  BTC交易机器人 - Docker启动脚本"
echo "========================================"
echo ""

# 检查Docker是否运行
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}[错误]${NC} Docker未运行或未安装！"
    echo ""
    echo "请先启动Docker服务"
    echo "Ubuntu/Debian: sudo systemctl start docker"
    echo "macOS: 启动Docker Desktop"
    echo ""
    exit 1
fi

echo -e "${GREEN}[✓]${NC} Docker运行正常"
echo ""

# 检查.env文件
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}[警告]${NC} 未找到.env配置文件！"
    echo ""
    echo "请先创建.env文件并配置API密钥"
    echo "参考.env.example文件"
    echo ""
    exit 1
fi

echo -e "${GREEN}[✓]${NC} 配置文件已找到"
echo ""

# 检查docker-compose.yml
if [ ! -f "docker-compose.yml" ]; then
    echo -e "${RED}[错误]${NC} 未找到docker-compose.yml文件！"
    exit 1
fi

echo "[启动] 正在启动Docker容器..."
echo ""

# 启动容器
docker compose up -d

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================"
    echo "  启动成功！"
    echo "========================================"
    echo ""
    echo "访问地址: http://localhost:8080"
    echo ""
    echo "常用命令:"
    echo "  查看日志: docker-compose logs -f"
    echo "  停止服务: docker-compose down"
    echo "  重启服务: docker-compose restart"
    echo ""
    
    # 尝试打开浏览器（可选）
    if command -v xdg-open > /dev/null; then
        xdg-open http://localhost:8080 2>/dev/null
    elif command -v open > /dev/null; then
        open http://localhost:8080 2>/dev/null
    fi
else
    echo ""
    echo -e "${RED}[错误]${NC} 启动失败，请查看错误信息"
    echo ""
    exit 1
fi

