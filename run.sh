#!/bin/sh
# Сборка фронта + запуск: http://127.0.0.1:8010
cd "$(dirname "$0")"
set -a; . ./.env; set +a
(cd web && pnpm build >/dev/null)
cd server && exec uv run uvicorn main:app --host 127.0.0.1 --port ${PORT:-8010}
