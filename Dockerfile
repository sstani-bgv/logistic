# Сборка фронта
FROM node:22-alpine AS web
WORKDIR /web
# npmjs.org из РФ бывает недоступен: на сервере NPM_REGISTRY=https://registry.npmmirror.com в .env
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV npm_config_registry=$NPM_REGISTRY
RUN npm install -g pnpm@10 --no-fund --no-audit
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# Приложение
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 DATA_DIR=/data TZ=Europe/Moscow
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Camoufox (Firefox с подменой отпечатка) для входа на биржу АТИ: системные библиотеки Firefox и сам браузер
# в кэше пользователя app (~/.cache/camoufox), где его ищет camoufox; модель отпечатков fpgen — в site-packages, поэтому под root.
RUN playwright install-deps firefox && rm -rf /var/lib/apt/lists/* \
    && useradd -r -m -u 10001 app && mkdir -p /data && chown app /data \
    && HOME=/home/app python -m camoufox fetch && chown -R app /home/app \
    && rm -rf /home/app/.cache/camoufox/browsers/*/*/fonts/W /home/app/.cache/camoufox/browsers/*/*/fonts/M  # шрифты Windows/macOS (1,2 ГБ): отпечаток у нас Linux
USER app
COPY server/ ./
COPY --from=web /web/dist /app/web/dist
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8010/api/health')"
# --no-access-log: в путях вебхука секрет, в логи его не пишем
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--ws-max-size", "262144"]
