#!/bin/sh
# Сборка фронта + запуск: http://127.0.0.1:8010
# Если задан LLM_PROXY_MODE (codex|mock) — рядом поднимается локальный OpenAI-совместимый эндпоинт на 8011.
cd "$(dirname "$0")"
set -a; . ./.env; set +a
PNPM="pnpm"; command -v pnpm >/dev/null || PNPM="npx -y pnpm@10"
(cd web && $PNPM install --frozen-lockfile >/dev/null && $PNPM build >/dev/null)
cd server
if [ -n "$LLM_PROXY_MODE" ]; then
  uv run uvicorn llm_proxy:app --host 127.0.0.1 --port ${LLM_PROXY_PORT:-8011} --log-level warning &
  PROXY=$!
  trap 'kill $PROXY 2>/dev/null' EXIT INT TERM
fi
uv run uvicorn main:app --host 127.0.0.1 --port ${PORT:-8010}
