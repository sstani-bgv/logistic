# Logistic — AI-диспетчер для ATI.su

Прототип: ищет грузы на ATI.su под свои машины, скорит их (`server/scoring.py`), отсеивает лишнее и ведёт переговоры с заказчиком в мессенджере ATI через ИИ-агента (`server/agent.py`, Codex CLI со structured output). Объяснения логиста агент превращает в правила (база знаний).

- `server/` — FastAPI + SQLite, интеграция с ATI API (`ati.py`)
- `web/` — React 19 + Vite + Tailwind + shadcn/ui: Грузы, Переговоры (канбан/диалоги), База знаний, Машины

## Запуск

Нужны `uv`, `pnpm`, `codex` CLI и токен ATI.

```bash
cp .env.example .env   # вписать ATI_CLIENT_ID и ATI_TOKEN
(cd web && pnpm install)
./run.sh               # http://127.0.0.1:8010
```
