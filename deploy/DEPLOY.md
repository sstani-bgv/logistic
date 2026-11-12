# Деплой на VPS

Нужно: VPS с Ubuntu 22.04+ (2 vCPU / 2–4 GB), открытые порты 80 и 443, домен (или `<ip>.sslip.io`).

## Первый запуск

```bash
# на сервере
curl -fsSL https://get.docker.com | sh
git clone https://github.com/sstani-bgv/logistic.git /opt/logistic && cd /opt/logistic
cp .env.example .env && nano .env      # ATI_*, LLM_*, SITE, TELEPHONY*, бэкапы; DEMO_SEED и LLM_PROXY_MODE — пустые
chmod 600 .env
docker compose up -d --build
docker compose exec app python manage.py create-owner <логин>   # спросит пароль
```

Проверка: `https://<SITE>` открывает страницу входа; `docker compose ps` — все сервисы `healthy/running`.

## Обновление

```bash
cd /opt/logistic && git pull && docker compose up -d --build
```

База и записи звонков лежат в volume `data` и при обновлении не трогаются.

## Бэкапы

- Каждый день в `BACKUP_AT` (МСК): `sqlite3 .backup` + проверка целостности → `/backups` и в S3 (`BACKUP_BUCKET`), хранение `KEEP_DAYS` (14) дней. Записи звонков копируются в `s3:<bucket>/recordings/`.
- Сделать бэкап сейчас: `docker compose exec backup backup.sh`
- Восстановить (один раз проверить руками при запуске):

```bash
docker compose stop app
docker compose run --rm backup restore.sh latest        # или имя файла data-YYYY-MM-DD_HHMM.db.gz (ищется и в S3)
docker compose start app
```

Текущая база перед восстановлением сохраняется как `data.db.before-restore-<время>`.

В бэкапах переписка, записи и хэши паролей: бакет — приватный, по возможности с шифрованием (`RCLONE_CONFIG_S3_SERVER_SIDE_ENCRYPTION`).

## Звонки

На проде обязательно `TELEPHONY=megafon` (по умолчанию `mock` — звонки только имитируются), `MEGAFON_*`, `TELEPHONY_WEBHOOK_SECRET` (не короче 32 символов: `openssl rand -hex 24`, иначе вебхук выключен), `RECORDING_HOSTS` — если записи отдаются не с хоста из `MEGAFON_API_URL`.
В ВАТС адрес вебхука: `https://<SITE>/api/telephony/webhook/<секрет>`. Голос — `YC_FOLDER_ID`, `YC_API_KEY` (Yandex AI Studio Realtime); уже сейчас работает тестовый звонок из браузера, SIP-линия ВАТС подключается к тому же релею (`server/voice.py`) после спайка.

## Перенос базы прототипа

База прототипа (`server/data.db` у Стаса) — это правила базы знаний клиента и история переговоров. Перед первым запуском положите её в том данных: `docker compose cp data.db app:/data/data.db` (или в `DATA_DIR` локально), затем `docker compose restart app`. Новые таблицы и колонки добавятся сами. На новой и перенесённой базе агент **выключен**: ищет и оценивает грузы, но никому не пишет и не звонит — сотрудники спокойно знакомятся с интерфейсом. Включает владелец в «Настройках» или командой:

```
docker compose exec app python manage.py agent status   # что включено
docker compose exec app python manage.py agent on       # пишет заказчикам и сам берёт лучшие грузы
docker compose exec app python manage.py calls on       # звонит, если в чате молчат
docker compose exec app python manage.py agent off      # выключить (звонки — calls off)
```

Перезапуск не нужен: приложение подхватывает переключение само.
