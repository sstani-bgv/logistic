#!/usr/bin/env bash
# Раз в минуту (systemd-таймер logistic-autodeploy): свежий код из main и свежий образ из ghcr.io.
# docker compose up -d пересоздаёт только то, что изменилось, — если ничего не менялось, это пустой проход.
# Установка: bash deploy/autodeploy.sh install
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = install ]; then
  cat > /etc/systemd/system/logistic-autodeploy.service <<UNIT
[Unit]
Description=logistic: pull code and image, restart what changed
[Service]
Type=oneshot
ExecStart=$PWD/deploy/autodeploy.sh
UNIT
  cat > /etc/systemd/system/logistic-autodeploy.timer <<UNIT
[Unit]
Description=logistic autodeploy every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload && systemctl enable --now logistic-autodeploy.timer
  exit 0
fi
git pull -q --ff-only origin main
docker compose pull -q app
docker compose up -d --remove-orphans   # без --build: app собирает GitHub, backup — только при первом запуске
docker image prune -f >/dev/null
