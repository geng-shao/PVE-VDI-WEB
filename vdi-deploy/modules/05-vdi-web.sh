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

