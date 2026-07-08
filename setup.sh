#!/bin/bash
# 一键生成 VDI 部署脚本（模块5启动增强版）
mkdir -p /opt/vdi-deploy/modules

# ---------- 主控脚本（不变） ----------
cat > /opt/vdi-deploy/install.sh << 'EOF'
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
EOF
# ---------- 模块 1 ----------
cat > /opt/vdi-deploy/modules/01-system.sh << 'EOF'
#!/bin/bash
set -e
echo "系统更新与基础依赖安装..."
export DEBIAN_FRONTEND=noninteractive
# 强制使用阿里云镜像源
if [ -f /etc/apt/sources.list ]; then
    cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null
    codename=$(lsb_release -cs)
    cat > /etc/apt/sources.list << EOL
deb http://mirrors.aliyun.com/ubuntu/ ${codename} main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ ${codename}-updates main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ ${codename}-security main restricted universe multiverse
deb http://mirrors.aliyun.com/ubuntu/ ${codename}-backports main restricted universe multiverse
EOL
fi
apt-get update -o Acquire::http::Timeout=30
apt-get install -y -qq curl wget gnupg ca-certificates python3 python3-pip python3-venv nginx iptables-persistent
for port in 80 5000 8080; do
    iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport $port -j ACCEPT
done
iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || iptables -I INPUT -i lo -j ACCEPT
netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4 2>/dev/null
echo "  ✓ 系统初始化完成"
EOF

# ---------- 模块 2 ----------
cat > /opt/vdi-deploy/modules/02-docker.sh << 'EOF'
#!/bin/bash
set -e
if command -v docker &>/dev/null; then echo "  ✓ Docker 已安装"; exit 0; fi
echo "安装 Docker..."
MIRRORS=("https://mirrors.aliyun.com/docker-ce/linux/ubuntu" "https://download.docker.com/linux/ubuntu")
mkdir -p /etc/apt/keyrings
for mirror in "${MIRRORS[@]}"; do
    if curl -fsSL "${mirror}/gpg" 2>/dev/null | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null; then
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] ${mirror} $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list
        apt-get update -qq 2>/dev/null
        if apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin > /dev/null 2>&1; then
            systemctl enable docker
            mkdir -p /etc/docker
            echo '{"registry-mirrors":["https://docker.m.daocloud.io"]}' > /etc/docker/daemon.json
            systemctl restart docker
            echo "  ✓ Docker 安装完成"
            exit 0
        fi
    fi
    rm -f /etc/apt/sources.list.d/docker.list
done
echo "Docker 安装失败"; exit 1
EOF

# ---------- 模块 3 ----------
cat > /opt/vdi-deploy/modules/03-guacamole.sh << 'EOF'
#!/bin/bash
set -e
DEPLOY_DIR="/opt/vdi-deploy"
source "${DEPLOY_DIR}/config.env"
GUAC_DIR="${DEPLOY_DIR}/guacamole"
mkdir -p "${GUAC_DIR}/init"
python3 << PYEOF
import os
cfg = {}
with open("${DEPLOY_DIR}/config.env") as f:
    for line in f:
        if line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
compose = f"""services:
  guacd:
    image: guacamole/guacd:1.6.0
    container_name: guacd
    restart: always
    volumes: [guacd-drive:/drive]
  mysql:
    image: mysql:8.0
    container_name: guac-mysql
    restart: always
    command: --default-authentication-plugin=mysql_native_password
    environment:
      MYSQL_ROOT_PASSWORD: "{cfg['MYSQL_ROOT_PASSWORD']}"
      MYSQL_DATABASE: guacamole_db
      MYSQL_USER: guacamole_user
      MYSQL_PASSWORD: "{cfg['MYSQL_GUAC_PASSWORD']}"
    volumes: [mysql-data:/var/lib/mysql, ./init:/docker-entrypoint-initdb.d]
    ports: ["127.0.0.1:3306:3306"]
  guacamole:
    image: guacamole/guacamole:1.6.0
    container_name: guacamole
    restart: always
    ports: ["{cfg.get('GUAC_PORT','8080')}:8080"]
    environment:
      GUACD_HOSTNAME: guacd
      MYSQL_HOSTNAME: mysql
      MYSQL_DATABASE: guacamole_db
      MYSQL_USER: guacamole_user
      MYSQL_PASSWORD: "{cfg['MYSQL_GUAC_PASSWORD']}"
    depends_on: [guacd, mysql]
volumes:
  guacd-drive:
  mysql-data:
"""
with open("${GUAC_DIR}/docker-compose.yml", "w") as f: f.write(compose)
PYEOF
cd "${GUAC_DIR}"
for img in guacamole/guacd:1.6.0 guacamole/guacamole:1.6.0 mysql:8.0; do
    docker pull "$img" 2>/dev/null || docker pull "docker.m.daocloud.io/$img" 2>/dev/null && docker tag "docker.m.daocloud.io/$img" "$img" && docker rmi "docker.m.daocloud.io/$img" 2>/dev/null
done
docker run --rm guacamole/guacamole:1.6.0 /opt/guacamole/bin/initdb.sh --mysql > init/initdb.sql 2>/dev/null || true
docker compose down 2>/dev/null || true
docker compose up -d
for i in $(seq 1 30); do
    if docker exec guac-mysql mysqladmin ping -u root -p"${MYSQL_ROOT_PASSWORD}" --silent 2>/dev/null; then break; fi
    sleep 2
done
echo "  ✓ Guacamole 部署完成"
EOF

# ---------- 模块 4 ----------
cat > /opt/vdi-deploy/modules/04-database.sh << 'EOF'
#!/bin/bash
set -e
DEPLOY_DIR="/opt/vdi-deploy"
source "${DEPLOY_DIR}/config.env"
echo "创建数据库扩展表..."
pip install --quiet mysql-connector-python 2>/dev/null || apt-get install -y python3-mysql.connector
pip install --quiet mysql-connector-python 2>/dev/null || true
python3 << PYEOF
import os, mysql.connector
cfg = {}
with open("${DEPLOY_DIR}/config.env") as f:
    for line in f:
        if line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
conn = mysql.connector.connect(host="127.0.0.1", port=3306, user="root", password=cfg["MYSQL_ROOT_PASSWORD"], database="guacamole_db")
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS vdi_audit_log (id INT AUTO_INCREMENT PRIMARY KEY, operator VARCHAR(64), action VARCHAR(32), target VARCHAR(128), details TEXT, ip_address VARCHAR(45), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
cur.execute("CREATE TABLE IF NOT EXISTS vdi_schedule (id INT AUTO_INCREMENT PRIMARY KEY, connection_id INT, vmid INT, action VARCHAR(16), execute_at DATETIME, executed TINYINT DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
conn.commit()
print("  ✓ 扩展表创建完成")
PYEOF
EOF

# ---------- 模块 5（增强版）----------
cat > /opt/vdi-deploy/modules/05-vdi-web.sh << 'EOF'
#!/bin/bash
set -e
DEPLOY_DIR="/opt/vdi-deploy"
source "${DEPLOY_DIR}/config.env"
VDI_DIR="${DEPLOY_DIR}/vdi-web"
mkdir -p "${VDI_DIR}/templates"

# 生成 config.yaml
python3 << PYEOF
import os, secrets
cfg = {}
with open("${DEPLOY_DIR}/config.env") as f:
    for line in f:
        if line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
yaml = f"""proxmox:
  host: "{cfg['PROXMOX_HOST']}"
  node: "{cfg['PROXMOX_NODE']}"
  user: "root@pam"
  token_name: "{cfg['PROXMOX_TOKEN_NAME']}"
  token_value: "{cfg['PROXMOX_TOKEN_VALUE']}"
  verify_ssl: false
  default_storage: "local-lvm"
database:
  host: "127.0.0.1"
  port: 3306
  user: "guacamole_user"
  password: "{cfg['MYSQL_GUAC_PASSWORD']}"
  database: "guacamole_db"
guacamole:
  protocol: "http"
  host: "{cfg['GUAC_HOST']}"
  port: "{cfg.get('GUAC_PORT','8080')}"
  path: "/guacamole/"
  datasource: "mysql"
web:
  admin_password: "{cfg['VDI_ADMIN_PASSWORD']}"
  secret_key: "{secrets.token_hex(16)}"
  listen_host: "0.0.0.0"
  listen_port: 5000
email:
  enabled: false
rdp_defaults:
  port: "3389"
  ignore-cert: "true"
  resize-method: "display-update"
  enable-wallpaper: "false"
  enable-theming: "false"
  enable-font-smoothing: "true"
"""
with open("${VDI_DIR}/config.yaml", "w") as f: f.write(yaml)
PYEOF

# 安装 Python 依赖
python3 -m venv "${VDI_DIR}/venv" 2>/dev/null || true
source "${VDI_DIR}/venv/bin/activate"
pip install --quiet -i https://mirrors.aliyun.com/pypi/simple/ flask mysql-connector-python proxmoxer pyyaml apscheduler requests 2>/dev/null || \
pip install --quiet flask mysql-connector-python proxmoxer pyyaml apscheduler requests 2>/dev/null
deactivate

# 检查并复制应用文件
# 检查并复制应用文件（修正模板复制路径）

[ -f "${DEPLOY_DIR}/app.py" ] && cp "${DEPLOY_DIR}/app.py" "${VDI_DIR}/app.py" || { echo "  ✗ app.py 缺失"; exit 1; }
[ -d "${DEPLOY_DIR}/templates" ] && cp -r "${DEPLOY_DIR}/templates/." "${VDI_DIR}/templates/"

# 创建 systemd 服务文件
cat > /etc/systemd/system/vdi-web.service << EOF
[Unit]
Description=VDI Web Manager
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=${VDI_DIR}
ExecStartPre=/bin/sleep 10
ExecStart=${VDI_DIR}/venv/bin/python3 ${VDI_DIR}/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

systemctl daemon-reload
systemctl enable vdi-web
systemctl restart vdi-web

EOF

# 循环检测直到服务端口可达（最多等待 60 秒）
echo "  等待 VDI Web 启动..."
for i in $(seq 1 2); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/ 2>/dev/null || true)
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
        echo "  ✓ VDI Web 已启动 (HTTP $HTTP_CODE)"
        exit 0
    fi
    sleep 1
done
echo "  ⚠ VDI Web 启动超时，请检查日志: journalctl -u vdi-web"
journalctl -u vdi-web --no-pager -n 10
EOF

# ---------- 模块 6（不变）----------
cat > /opt/vdi-deploy/modules/06-nginx.sh << 'EOF'
#!/bin/bash
set -e
cat > /etc/nginx/sites-available/vdi << 'NGX'
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
server {
    listen 80; server_name _;
    location /guacamole/ { proxy_pass http://127.0.0.1:8080/guacamole/; proxy_buffering off; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade; proxy_cookie_path /guacamole/ /; }
    location / { proxy_pass http://127.0.0.1:5000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; }
}
NGX
ln -sf /etc/nginx/sites-available/vdi /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
echo "  ✓ Nginx 配置完成"

systemctl daemon-reload
systemctl enable vdi-web
systemctl restart vdi-web
EOF

# 赋予执行权限并输出提示
chmod +x /opt/vdi-deploy/install.sh /opt/vdi-deploy/modules/*.sh

GREEN='\033[0;32m'; NC='\033[0m'
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           所有部署脚本已生成                                           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════════════╝${NC}"
echo -e "${GREEN}║     下一步操作：                                                       ║${NC}"
echo -e "${GREEN}║     1. 将 app.py 和 templates/ 目录复制到 /opt/vdi-deploy/             ║${NC}"
echo -e "${GREEN}║     2. 编辑config.env 修改配置参数                                     ║${NC}"
echo -e "${GREEN}║     所有部署脚本已生成，执行 bash /opt/vdi-deploy/install.sh 开始安装  ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════════════╝${NC}"