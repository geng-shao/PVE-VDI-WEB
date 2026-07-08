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
