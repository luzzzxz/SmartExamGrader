#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y nginx curl certbot python3-certbot-nginx iptables-persistent

# Some VPS images reject every inbound port except SSH by default.
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
netfilter-persistent save

if ! id manualgrade >/dev/null 2>&1; then
    useradd --system --home /var/lib/manual-grading --shell /usr/sbin/nologin manualgrade
fi

install -d -m 0755 /opt/manual-grading
install -d -o manualgrade -g manualgrade -m 0750 /var/lib/manual-grading
install -m 0644 /tmp/manual_grading_web_server.py /opt/manual-grading/manual_grading_web_server.py
install -o manualgrade -g manualgrade -m 0640 /tmp/manual_web_server_config.json /var/lib/manual-grading/config.json

cat >/etc/systemd/system/manual-grading-web.service <<'EOF'
[Unit]
Description=Manual Grading Web Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=manualgrade
Group=manualgrade
WorkingDirectory=/opt/manual-grading
Environment=PYTHONUNBUFFERED=1
Environment=MANUAL_WEB_HOST=127.0.0.1
Environment=MANUAL_WEB_PORT=18765
Environment=MANUAL_WEB_DATA_DIR=/var/lib/manual-grading/data
Environment=MANUAL_WEB_CONFIG=/var/lib/manual-grading/config.json
ExecStart=/usr/bin/python3 /opt/manual-grading/manual_grading_web_server.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/manual-grading

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/nginx/sites-available/manual-grading <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name your-domain.com 192.0.2.1 _;

    client_max_body_size 256m;

    location / {
        proxy_pass http://127.0.0.1:18765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/manual-grading /etc/nginx/sites-enabled/manual-grading

systemctl daemon-reload
systemctl enable --now manual-grading-web.service
nginx -t
systemctl enable --now nginx
systemctl reload nginx

curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18765/ >/dev/null
curl --fail --silent --show-error --max-time 10 -H 'Host: your-domain.com' http://127.0.0.1/ >/dev/null

certbot --nginx -d your-domain.com \
    --non-interactive --agree-tos --register-unsafely-without-email --redirect
systemctl enable --now certbot.timer
curl --fail --silent --show-error --max-time 15 https://your-domain.com/login >/dev/null

echo MANUAL_GRADING_DEPLOY_OK

