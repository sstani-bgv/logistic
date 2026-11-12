#!/bin/bash
# Восстановление базы из бэкапа. Приложение на время остановить:
#   docker compose stop app && docker compose run --rm backup restore.sh [имя.db.gz|latest] && docker compose start app
# Имя без пути ищется локально, затем в S3. Текущая база сохраняется рядом как data.db.before-restore-<время>.
set -euo pipefail
DATA=${DATA:-/data}; OUT=${OUT:-/backups}
name=${1:-latest}
if [ "$name" = latest ]; then
  name=$(ls -1t "$OUT"/db/data-*.db.gz 2>/dev/null | head -1 || true)
  [ -n "$name" ] || { echo "Локальных бэкапов нет — укажите имя файла из S3" >&2; exit 1; }
fi
src="$name"
[ -f "$src" ] || src="$OUT/db/$(basename "$name")"
if [ ! -f "$src" ] && [ -n "${BACKUP_BUCKET:-}" ]; then
  rclone copy "s3:$BACKUP_BUCKET/db/$(basename "$name")" "$OUT/db/" --s3-no-check-bucket
fi
[ -f "$src" ] || { echo "Не нашёл бэкап $name" >&2; exit 1; }
tmp=$(mktemp -d)
gunzip -c "$src" > "$tmp/data.db"
[ "$(sqlite3 "$tmp/data.db" 'pragma integrity_check')" = ok ] || { echo "Бэкап битый" >&2; exit 1; }
[ -f "$DATA/data.db" ] && cp "$DATA/data.db" "$DATA/data.db.before-restore-$(date +%F_%H%M%S)"
rm -f "$DATA/data.db-wal" "$DATA/data.db-shm"
mv "$tmp/data.db" "$DATA/data.db"
# приложение работает от uid 10001 — база и записи должны принадлежать ему, иначе «readonly database»
[ "$(id -u)" = 0 ] && chown "${APP_UID:-10001}:${APP_UID:-10001}" "$DATA/data.db"
if [ -n "${BACKUP_BUCKET:-}" ] && [ "${WITH_RECORDINGS:-1}" = 1 ]; then
  mkdir -p "$DATA/recordings"
  rclone copy "s3:$BACKUP_BUCKET/recordings/" "$DATA/recordings/" --s3-no-check-bucket && echo "Записи звонков восстановлены из S3"
  [ "$(id -u)" = 0 ] && chown -R "${APP_UID:-10001}:${APP_UID:-10001}" "$DATA/recordings"
fi
echo "Восстановлено из $src: $(sqlite3 "$DATA/data.db" 'select count(*) from trucks') машин, $(sqlite3 "$DATA/data.db" 'select count(*) from dialogs') диалогов"
