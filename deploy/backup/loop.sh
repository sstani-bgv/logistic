#!/bin/bash
# Каждый день в BACKUP_AT (по умолчанию 03:30, TZ из окружения) запускаем backup.sh.
AT=${BACKUP_AT:-03:30}
while true; do
  now=$(date +%s); next=$(date -d "today $AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow $AT" +%s)
  sleep $((next - now))
  backup.sh || echo "$(date '+%F %T') бэкап не удался" >&2
done
