# Logistic — AI-диспетчер для ATI.su

Прототип: ищет грузы на ATI.su под свои машины, скорит их (`server/scoring.py`), отсеивает лишнее и ведёт переговоры с заказчиком в мессенджере ATI через ИИ-агента (`server/agent.py` → `server/llm.py`: любой OpenAI-совместимый API со structured output). Объяснения логиста агент превращает в правила (база знаний).

- `server/` — FastAPI + SQLite: ATI (`ati.py`), модель (`llm.py`), машины (`fleet.py`), логины (`auth.py`), звонки (`calls.py`, `telephony.py`)
- `web/` — React 19 + Vite + Tailwind + shadcn/ui: Грузы, Переговоры (канбан/диалоги, звонки), База знаний, Машины, Настройки, Сотрудники
- `deploy/` — Caddy, бэкапы, [инструкция по деплою](deploy/DEPLOY.md); памятка логиста — [docs/ПАМЯТКА.md](docs/ПАМЯТКА.md); план — [docs/PLAN.md](docs/PLAN.md)

## Запуск

Нужны `uv`, `pnpm` и токен ATI. Модель — любой OpenAI-совместимый API (Yandex Cloud, DeepSeek…), задаётся в `.env`.

```bash
cp .env.example .env   # вписать ATI_CLIENT_ID, ATI_TOKEN и блок LLM_*
(cd web && pnpm install)
./run.sh               # http://127.0.0.1:8010
```

Первый вход: `cd server && uv run python manage.py create-owner <логин>` (пароль спросит).

## Модель (LLM)

Приложение ходит только в `LLM_BASE_URL/chat/completions`; провайдер меняется в `.env` без правки кода (примеры — в `.env.example`).

Для разработки есть локальный OpenAI-совместимый эндпоинт `server/llm_proxy.py` (порт 8011, `run.sh` поднимает его сам, если задан `LLM_PROXY_MODE`):

- `LLM_PROXY_MODE=codex` — ответы через Codex CLI (`npm i -g @openai/codex`, затем `codex login`);
- `LLM_PROXY_MODE=mock` — мгновенные ответы по схеме, без сети и ключей.

Таймаут `LLM_TIMEOUT`, один повтор на таймаут/сеть/429/5xx; ответ не по схеме — ошибка в summary диалога.

## Тесты

```bash
cd server && uv run pytest
```
