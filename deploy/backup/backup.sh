#!/bin/bash
# Снимок базы (sqlite3 .backup — безопасно на живой базе) + записи звонков → локально и в S3, хранение KEEP_DAYS.
# S3 (Yandex Object Storage и др.) — через переменные RCLONE_CONFIG_S3_* и BACKUP_BUCKET; без них — только локально.
set -euo pipefail
DATA=${DATA:-/data}; OUT=${OUT:-/backups}; KEEP_DAYS=${KEEP_DAYS:-14}
ts=$(date +%F_%H%M)
mkdir -p "$OUT/db"
snap="$OUT/db/data-$ts.db"
sqlite3 "$DATA/data.db" ".backup '$snap'"
check=$(sqlite3 "$snap" "pragma integrity_check")
[ "$check" = "ok" ] || { echo "Бэкап битый: $check" >&2; rm -f "$snap"; exit 1; }
gzip -f "$snap"
echo "$(date '+%F %T') база: $snap.gz ($(du -h "$snap.gz" | cut -f1))"
find "$OUT/db" -name 'data-*.db.gz' -mtime +"$KEEP_DAYS" -delete

if [ -n "${BACKUP_BUCKET:-}" ] && [ -n "${RCLONE_CONFIG_S3_TYPE:-}" ]; then
  rclone copy "$snap.gz" "s3:$BACKUP_BUCKET/db/" --s3-no-check-bucket
  rclone delete "s3:$BACKUP_BUCKET/db/" --min-age "${KEEP_DAYS}d" --s3-no-check-bucket
  [ -d "$DATA/recordings" ] && rclone copy "$DATA/recordings" "s3:$BACKUP_BUCKET/recordings/" --s3-no-check-bucket
  rclone delete "s3:$BACKUP_BUCKET/recordings/" --min-age "${KEEP_DAYS}d" --s3-no-check-bucket || true
  echo "$(date '+%F %T') выгружено в s3:$BACKUP_BUCKET"
else
  echo "ВНИМАНИЕ: внешнее хранилище не настроено (BACKUP_BUCKET, RCLONE_CONFIG_S3_*) — бэкап только на этом сервере" >&2
fi
