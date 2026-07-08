#!/bin/bash
# VDI 云桌面平台 - 模块化安装主菜单 (最终稳定版)
set -e
GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
DEPLOY_DIR="/opt/vdi-deploy"
MODULES_DIR="${DEPLOY_DIR}/modules"

echo -e "${GREEN}╔════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   VDI 云桌面平台 - 模块化安装     ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════╝${NC}"
echo "  1) 全部安装（推荐）"
echo "  2) 系统初始化 + Docker"
echo "  3) Guacamole 部署"
echo "  4) 数据库扩展表"
echo "  5) VDI Web 部署"
echo "  6) Nginx 配置"
echo "  0) 退出"
read -p "  请选择 [1]: " choice
choice=${choice:-1}

run() {
    echo -e "${BLUE}▶ 正在运行 $1 ...${NC}"
    bash "${MODULES_DIR}/$2"
    echo -e "${GREEN}  ✓ $1 完成${NC}"
}

case $choice in
    1)
        run "系统初始化" "01-system.sh"
        run "Docker 安装" "02-docker.sh"
        run "Guacamole 部署" "03-guacamole.sh"
        run "数据库扩展表" "04-database.sh"
        run "VDI Web 部署" "05-vdi-web.sh"
        run "Nginx 配置" "06-nginx.sh"
        ;;
    2) run "系统初始化" "01-system.sh"; run "Docker 安装" "02-docker.sh" ;;
    3) run "Guacamole 部署" "03-guacamole.sh" ;;
    4) run "数据库扩展表" "04-database.sh" ;;
    5) run "VDI Web 部署" "05-vdi-web.sh" ;;
    6) run "Nginx 配置" "06-nginx.sh" ;;
    0) exit 0 ;;
esac

if [ -f "${DEPLOY_DIR}/config.env" ]; then
    source "${DEPLOY_DIR}/config.env"
    echo -e "\n${GREEN}╔════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           安装完成                ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════╝${NC}"
    echo "  管理面板: http://${GUAC_HOST}/"
    echo "  Guacamole: http://${GUAC_HOST}:${GUAC_PORT:-8080}/guacamole/"
    echo "  管理员: admin / ${VDI_ADMIN_PASSWORD}"
fi
