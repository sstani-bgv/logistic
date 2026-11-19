#!/usr/bin/env bash
# Обратный туннель для SSH: сервер сам подключается к ретранслятору bore.pub, а разработчик заходит через него.
# Нужен, когда фильтр режет входящие соединения к серверу в РФ из-за границы. Вход по-прежнему только по SSH-ключу.
# Запуск в веб-консоли Timeweb под root:
#   curl -fsSL https://raw.githubusercontent.com/sstani-bgv/logistic/main/deploy/reverse-tunnel.sh | bash
# Подключение:  ssh -p 41873 root@bore.pub
# Убрать:       systemctl disable --now ssh-bore && rm /etc/systemd/system/ssh-bore.service /usr/local/bin/bore
set -euo pipefail
PORT=41873
ARCH=$(uname -m); [ "$ARCH" = "arm64" ] && ARCH=aarch64
curl -fsSL "https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-${ARCH}-unknown-linux-musl.tar.gz" | tar -xz -C /usr/local/bin bore
chmod +x /usr/local/bin/bore
cat > /etc/systemd/system/ssh-bore.service <<UNIT
[Unit]
Description=Reverse SSH tunnel via bore.pub (port ${PORT})
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/bore local 22 --to bore.pub --port ${PORT}
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now ssh-bore
sleep 4
if journalctl -u ssh-bore -n 20 --no-pager | grep -q "listening at bore.pub:${PORT}"; then
  echo "ГОТОВО: туннель открыт, порт ${PORT}"
else
  echo "ОШИБКА, пришлите скриншот этого:"; journalctl -u ssh-bore -n 10 --no-pager
fi
