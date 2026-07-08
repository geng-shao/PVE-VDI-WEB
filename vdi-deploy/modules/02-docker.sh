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
