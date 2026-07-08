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
