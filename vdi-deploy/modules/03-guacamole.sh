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
