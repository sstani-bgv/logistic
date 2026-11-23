#!/bin/bash
# Дни недели ISO: 1 — понедельник, 7 — воскресенье. TZ задаётся в Compose.
set -euo pipefail
AT=${BACKUP_AT:-00:00}
DAYS=${BACKUP_DAYS:-1,3,5}

next_backup() {
  local now=$1 candidate weekday offset
  for offset in {0..7}; do
    candidate=$(date -d "today +$offset days $AT" +%s)
    weekday=$(date -d "@$candidate" +%u)
    if [[ ",$DAYS," == *",$weekday,"* ]] && [ "$candidate" -gt "$now" ]; then
      echo "$candidate"
      return
    fi
  done
  echo "Некорректное расписание бэкапов: BACKUP_DAYS=$DAYS BACKUP_AT=$AT" >&2
  return 1
}

[[ ${BASH_SOURCE[0]} != "$0" ]] && return 0
while true; do
  now=$(date +%s)
  next=$(next_backup "$now")
  echo "Следующий бэкап: $(date -d "@$next" '+%F %T %Z')"
  sleep $((next - now))
  backup.sh || echo "$(date '+%F %T') бэкап не удался" >&2
done
