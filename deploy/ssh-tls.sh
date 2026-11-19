#!/usr/bin/env bash
# Доступ по SSH, завёрнутый в TLS (порт 8443): обходит фильтры, которые режут SSH к серверам в РФ из-за границы.
# Запуск в веб-консоли Timeweb под root:
#   curl -fsSL https://raw.githubusercontent.com/sstani-bgv/logistic/main/deploy/ssh-tls.sh | bash
# Подключение с ноутбука:
#   ssh -o ProxyCommand="openssl s_client -quiet -connect %h:8443 -servername %h" root@<IP>
# Вход только по SSH-ключу, как обычно: TLS лишь обёртка. Убрать: apt-get purge -y stunnel4
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq stunnel4 openssl >/dev/null
install -d -m 700 /etc/stunnel
[ -f /etc/stunnel/ssh.pem ] || openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=server" \
  -keyout /etc/stunnel/ssh.pem -out /etc/stunnel/ssh.pem 2>/dev/null
chmod 600 /etc/stunnel/ssh.pem
cat > /etc/stunnel/ssh.conf <<'CONF'
pid = /run/stunnel-ssh.pid
[ssh]
accept = 0.0.0.0:8443
connect = 127.0.0.1:22
cert = /etc/stunnel/ssh.pem
CONF
cat > /etc/systemd/system/stunnel-ssh.service <<'UNIT'
[Unit]
Description=SSH over TLS (port 8443)
After=network-online.target
[Service]
Type=forking
ExecStart=/usr/bin/stunnel /etc/stunnel/ssh.conf
PIDFile=/run/stunnel-ssh.pid
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now stunnel-ssh
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 8443/tcp; fi
ss -tln | grep -q ':8443' && echo "ГОТОВО: SSH через TLS слушает порт 8443" || { echo "ОШИБКА: порт 8443 не слушается"; exit 1; }
