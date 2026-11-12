# Деплой на VPS

Нужно: VPS с Ubuntu 22.04+ (2 vCPU / 2–4 GB), открытые порты 80 и 443, домен (или `<ip>.sslip.io`).

## Первый запуск

```bash
# на сервере
curl -fsSL https://get.docker.com | sh
git clone -b feature/llm-api https://github.com/sstani-bgv/logistic.git /opt/logistic && cd /opt/logistic   # в main этого кода пока нет; после слияния убрать -b
cp .env.example .env && nano .env      # ATI_*, LLM_*, SITE, TELEPHONY*, бэкапы; DEMO_SEED и LLM_PROXY_MODE — пустые
cp backup.env.example backup.env && nano backup.env   # ключи хранилища бэкапов
chmod 600 .env backup.env
docker compose up -d --build
docker compose exec app python manage.py create-owner <логин>   # спросит пароль
docker compose exec app python manage.py check-ati             # токен и тариф АТИ
```

Проверка: `https://<SITE>` открывает страницу входа; `docker compose ps` — `app` в статусе `healthy`, остальные `running`. После входа слева должно быть «Мониторинг АТИ работает», плашки «Требует внимания» быть не должно. Если она есть, в ней написано, что чинить (например, без `backup.env` после первого бэкапа будет предупреждение: внешнее хранилище не настроено).

## Обновление

```bash
cd /opt/logistic && git pull && docker compose up -d --build
```

База и записи звонков лежат в volume `data` и при обновлении не трогаются. После правки `.env` нужен `docker compose up -d` — `docker compose restart` новые переменные не подхватит.

## Бэкапы

- Каждый день в `BACKUP_AT` (по МСК): `sqlite3 .backup` + проверка целостности → `/backups` и, если настроено, в S3 (`BACKUP_BUCKET`), хранение `KEEP_DAYS` (14) дней. Записи звонков копируются в `s3:<bucket>/recordings/`.
- Сделать бэкап сейчас: `docker compose exec backup backup.sh`
- Восстановить (скрипт проверен тестом; на VPS один раз проверить руками при запуске):

```bash
docker compose stop app
docker compose run --rm backup restore.sh latest        # или имя файла data-YYYY-MM-DD_HHMM.db.gz (если его нет локально — ищется в S3)
docker compose start app
```

`latest` — последний локальный бэкап. Текущая база перед восстановлением сохраняется как `data.db.before-restore-<время>`.

В бэкапах переписка, записи и хэши паролей: бакет — приватный, по возможности с шифрованием (`RCLONE_CONFIG_S3_SERVER_SIDE_ENCRYPTION`).

## Звонки и голос

По умолчанию звонков нет: `TELEPHONY` пусто (в compose — `off`). На проде с ВАТС МегаФон: `TELEPHONY=megafon`, `MEGAFON_API_URL`, `MEGAFON_API_KEY`, `MEGAFON_BOT_USER`, `TELEPHONY_WEBHOOK_SECRET` (не короче 32 символов: `openssl rand -hex 24`, иначе вебхук выключен), `RECORDING_HOSTS` — только если записи отдаются не с хоста из `MEGAFON_API_URL` (он добавляется сам). `TELEPHONY=mock` — имитация с выдуманными расшифровками, на проде не нужна.

Адрес вебхука в ВАТС: `https://<SITE>/api/telephony/webhook/<секрет>`.

Голос: `YC_FOLDER_ID` и `YC_API_KEY` (Yandex AI Studio Realtime). Без них кнопки тестового звонка в диалоге нет. SIP-линия ВАТС пока не подключена: после доступа к ВАТС она подключится к тому же релею (`server/voice.py`).

## Перенос базы прототипа

База прототипа — правила базы знаний клиента и история переговоров. Положите `data.db` прототипа в `/opt/logistic` и выполните (сначала `up`, чтобы создался volume):

```bash
docker compose up -d --build
docker compose stop app
docker compose run --rm -v "$PWD/data.db:/import.db:ro" backup sh -c 'cp /import.db /data/data.db && chown 10001:10001 /data/data.db'
docker compose start app
```

Новые таблицы и колонки добавятся при старте `app`. Владелец включает агента в «Настройках» или командой. Если в перенесённой базе не было своих настроек, агент выключен (значения по умолчанию) — проверьте:

```
docker compose exec app python manage.py agent status   # что включено
docker compose exec app python manage.py agent on       # пишет заказчикам и сам берёт лучшие грузы
docker compose exec app python manage.py calls on       # звонит, если в чате молчат
docker compose exec app python manage.py agent off      # выключить (звонки — calls off)
```

Перезапуск не нужен: приложение подхватывает переключение само.
