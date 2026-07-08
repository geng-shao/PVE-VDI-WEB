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
