"""Адаптеры телефонии. TELEPHONY=megafon | mock (имитация для разработки) | пусто/off — звонков нет.

Итог звонка приходит в main.apply_call_result() одним форматом:
  {"status": "talking"|"done"|"no_answer"|"busy"|"failed", "duration": сек, "recording_url": str,
   "transcript": str, "provider_id": str}
— от мока, от вебхука ВАТС (запись, длительность) и от голосового бота (расшифровка).
"""
import asyncio
import os

import httpx

FINAL = ("done", "no_answer", "busy", "failed")
TRANSPORT = None  # тесты подменяют сеть (httpx.MockTransport)


def provider_name():
    return (os.environ.get("TELEPHONY") or "off").strip().lower()


class OffProvider:
    """Телефония не подключена: звонки не ставятся, причина видна логисту и владельцу."""
    ready, mode = False, "off"
    why = "телефония не подключена (TELEPHONY=megafon в .env после доступа к ВАТС)"

    def __init__(self, on_result):
        self.on_result = on_result

    async def start(self, call, context):
        raise RuntimeError(self.why)


MOCK_MARK = "[ИМИТАЦИЯ — TELEPHONY=mock, это не настоящий разговор]"


class MockProvider:
    """Локальная имитация: через MOCK_CALL_SECONDS «дозвонились» и получили расшифровку (помеченную как имитация)."""
    ready, mode, why = True, "mock", ""

    def __init__(self, on_result):
        self.on_result = on_result

    async def start(self, call, context):
        delay = float(os.environ.get("MOCK_CALL_SECONDS", "3"))
        outcome = os.environ.get("MOCK_CALL_OUTCOME", "done")

        async def later():
            await asyncio.sleep(delay)
            if outcome != "done":
                await self.on_result(call["id"], {"status": outcome, "duration": 0})
                return
            transcript = (f"{MOCK_MARK}\nАгент: Добрый день! Звоню по вашему грузу на АТИ, "
                          f"{context.get('route', 'по маршруту из объявления')}. Он ещё актуален?\n"
                          "Заказчик: Да, актуален. Грузим завтра с девяти.\n"
                          "Агент: Подскажите ставку и форму оплаты?\n"
                          "Заказчик: Шестьдесят тысяч с НДС, оплата пять банковских дней.\n"
                          "Агент: Спасибо, логист перезвонит подтвердить.")
            await self.on_result(call["id"], {"status": "done", "duration": 74, "transcript": transcript,
                                              "recording_url": None})
        asyncio.get_running_loop().create_task(later())
        return f"mock-{call['id']}"


class MegafonProvider:
    """ВАТС МегаФон, интеграция «Ваша CRM / REST API» (кабинет: «Настройки» → «Интеграция с CRM»).

    Два формата API у МегаФона: старый — POST https://<кабинет>/sys/crm_api.wcgp (cmd, token),
    новый — https://<кабинет>/crmapi/v1/... с заголовком X-API-KEY. Какой у клиента — определяет check().
    Итоги звонков ВАТС шлёт на «Адрес вашей CRM» (наш вебхук), с полем crm_token.
    ПРОВЕРИТЬ на живой ВАТС: ответы accounts/users и makeCall (документация МегаФона неполная).
    """
    mode = "megafon"

    def __init__(self, on_result, url=None, token=None, user=None, api=None):
        """Параметры — из «Настроек» программы; если там пусто — из .env (MEGAFON_API_URL / _KEY / _BOT_USER)."""
        self.on_result = on_result
        raw = (url or os.environ.get("MEGAFON_API_URL", "")).strip()
        self.token = (token or os.environ.get("MEGAFON_API_KEY", "")).strip()
        self.user = (user or os.environ.get("MEGAFON_BOT_USER", "")).strip()  # логин сотрудника-бота в ВАТС
        self.base, self.api = self.parse_url(raw)
        self.api = api or self.api
        self.url = raw
        self.ready = bool(self.base and self.token and self.user)
        self.why = "" if self.ready else "ВАТС МегаФон не настроена: в «Настройках» → «Телефония» нужны адрес кабинета, ключ и логин бота"

    @staticmethod
    def parse_url(raw):
        """«vats865841.cp.megafon.ru», ссылка на кабинет или полный адрес API → (https://хост, формат API)."""
        s = raw.strip()
        if not s:
            return "", "legacy"
        if "://" not in s:
            s = "https://" + s
        u = httpx.URL(s)
        api = "v1" if "/crmapi/" in u.path else "legacy"
        return (f"https://{u.host}" if u.host else ""), api

    async def _send(self, method, path, data=None, api=None):
        api = api or self.api
        try:
            async with httpx.AsyncClient(timeout=20, transport=TRANSPORT) as c:
                if api == "v1":
                    r = await c.request(method, self.base + path, data=data, headers={"X-API-KEY": self.token})
                else:
                    r = await c.post(self.base + "/sys/crm_api.wcgp", data={**(data or {}), "token": self.token})
        except httpx.HTTPError as e:
            raise RuntimeError(f"ВАТС не отвечает ({type(e).__name__}) — проверьте адрес кабинета; сервер должен быть в России") from e
        if r.status_code in (401, 403):
            raise RuntimeError(f"ВАТС отклонила ключ ({r.status_code}) — проверьте «Ключ для авторизации в АТС»")
        if r.status_code == 404:
            raise LookupError("нет такого адреса API")
        if r.status_code >= 400:
            raise RuntimeError(f"ВАТС HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return {}

    async def check(self):
        """Проверка без звонка: список сотрудников ВАТС; заодно определяем формат API.
        -> {"employees": n, "bot_found": bool, "api": "legacy" | "v1"}."""
        if not self.ready:
            raise RuntimeError(self.why)
        last = None
        for api, method, path, data in (("legacy", "POST", "", {"cmd": "accounts"}), ("v1", "GET", "/crmapi/v1/users", None)):
            try:
                j = await self._send(method, path, data, api)
            except LookupError as e:
                last = e
                continue
            self.api = api
            items = j if isinstance(j, list) else next((j[k] for k in ("accounts", "users", "data", "items")
                                                         if isinstance(j, dict) and isinstance(j.get(k), list)), [])
            names = {str(x.get(k) or "").lower() for x in items if isinstance(x, dict)
                     for k in ("login", "name", "user", "ext", "realName")}
            short = self.user.split("@")[0].lower()
            return {"employees": len(items), "bot_found": self.user.lower() in names or short in names, "api": api}
        raise RuntimeError("По этому адресу нет API ВАТС — вставьте адрес кабинета из строки браузера "
                           "и проверьте, что в кабинете включена «Интеграция с CRM»") from last

    async def start(self, call, context):
        if not self.ready:
            raise RuntimeError(self.why)
        phone = call["phone"].lstrip("+")
        try:
            if self.api == "v1":
                j = await self._send("POST", "/crmapi/v1/makecall", {"phone": phone, "user": self.user})
            else:
                j = await self._send("POST", "", {"cmd": "makeCall", "user": self.user, "phone": phone})
        except LookupError as e:
            raise RuntimeError("Адрес API ВАТС не найден — нажмите «Проверить подключение» в «Настройках»") from e
        return str(j.get("uuid") or j.get("callid") or j.get("call_id") or "") if isinstance(j, dict) else ""


MEGAFON_STATUS = {"success": "done", "missed": "no_answer", "busy": "busy", "notavailable": "failed",
                  "notallowed": "failed", "cancel": "no_answer"}


def parse_megafon(form):
    """Вебхук ВАТС (cmd=history) → общий формат. Остальные события (event, contact) игнорируем."""
    if (form.get("cmd") or "").lower() != "history":
        return None, None
    status = MEGAFON_STATUS.get((form.get("status") or "").lower().replace("_", ""), "failed")
    try:
        duration = int(float(form.get("duration") or 0))
    except ValueError:
        duration = 0
    return form.get("callid"), {"status": status, "duration": duration,
                                "recording_url": form.get("link") or None}


def make(on_result, cfg=None):
    """cfg — настройки из программы ({mode, url, token, user}); пустые поля — из .env."""
    cfg = cfg or {}
    name = (cfg.get("mode") or provider_name()).strip().lower()
    if name == "megafon":
        return MegafonProvider(on_result, cfg.get("url"), cfg.get("token"), cfg.get("user"), cfg.get("api"))
    if name == "mock":
        return MockProvider(on_result)
    if name in ("off", "none", "0", ""):
        return OffProvider(on_result)
    raise RuntimeError(f"Неизвестный TELEPHONY={name!r}: megafon, mock или off")
