"""Адаптеры телефонии. TELEPHONY=megafon | mock (имитация для разработки) | пусто/off — звонков нет.

Итог звонка приходит в main.apply_call_result() одним форматом:
  {"status": "talking"|"done"|"no_answer"|"busy"|"failed", "duration": сек, "recording_url": str,
   "transcript": str, "provider_id": str}
— от мока, от вебхука ВАТС (запись, длительность) и от голосового бота (расшифровка).
"""
import asyncio
import base64
import functools
import json
import os
import ssl
from pathlib import Path
from datetime import datetime, timedelta

import httpx

FINAL = ("done", "no_answer", "busy", "failed")
RU_ROOT = Path(__file__).parent / "certs" / "russian_trusted_root_ca.pem"  # Минцифры, SHA-256 D2:6D:2D:02:…:CF:31


@functools.cache
def megafon_tls():
    """Сертификаты МегаФона выдаёт Russian Trusted Root CA (Минцифры) — его нет в обычном наборе.
    Доверяем ему только в запросах к ВАТС, остальной трафик программы — как обычно."""
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    if RU_ROOT.exists():
        ctx.load_verify_locations(cafile=str(RU_ROOT))
    return ctx
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


CP_CALLAPI = "https://callapi.cp.megafon.ru/v4.0"
CP_DATAAPI = "https://data-api.cp.megafon.ru/v2.0"


def is_cp_token(token):
    """Ключ нового кабинета МегаФона (vatsNNN.cp.megafon.ru, «Настройки API»): JWT от sso.cp.megafon.ru."""
    parts = (token or "").strip().split(".")
    if len(parts) != 3:
        return False
    try:
        pl = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(pl, dict) and "cp.megafon.ru" in str(pl.get("iss", ""))


class MegafonProvider:
    """ВАТС МегаФон, интеграция «Ваша CRM / REST API» (кабинет: «Настройки» → «Интеграция с CRM»).

    Новый кабинет (vatsNNN.cp.megafon.ru, платформа CoMagic/UIS): ключ — JWT из «Настройки API»,
    JSON-RPC: Data API (get.employees, get.virtual_numbers) и Call API (start.employee_call), access_token в params.
    Итог звонка ВАТС шлёт «Уведомлением» (событие «Завершение звонка», POST JSON по нашему шаблону).
    Старые форматы API у МегаФона: старый — POST https://<кабинет>/sys/crm_api.wcgp (cmd, token),
    новый — https://<кабинет>/crmapi/v1/... с заголовком X-API-KEY. Какой у клиента — определяет check().
    Итоги звонков ВАТС шлёт на «Адрес вашей CRM» (наш вебхук), с полем crm_token.
    ПРОВЕРИТЬ на живой ВАТС: ответы accounts/users и makeCall (документация МегаФона неполная).
    """
    mode = "megafon"

    def __init__(self, on_result, url=None, token=None, user=None, api=None, number=None):
        """Параметры — из «Настроек» программы; если там пусто — из .env (MEGAFON_API_URL / _KEY / _BOT_USER)."""
        self.on_result = on_result
        raw = (url or os.environ.get("MEGAFON_API_URL", "")).strip()
        self.token = (token or os.environ.get("MEGAFON_API_KEY", "")).strip()
        self.user = (user or os.environ.get("MEGAFON_BOT_USER", "")).strip()  # логин сотрудника-бота в ВАТС
        self.number = (number or os.environ.get("MEGAFON_NUMBER", "")).strip().lstrip("+")  # номер для исходящих
        self.base, self.api = self.parse_url(raw)
        if api and api != "cp":
            self.api = api
        if is_cp_token(self.token):
            self.api = "cp"
        self.url = raw
        self._emp = None  # id сотрудника-бота и номер, найденные в ВАТС (кэш до перезагрузки настроек)
        if self.api == "cp":
            self.ready = bool(self.token and self.user)
            self.why = "" if self.ready else "ВАТС МегаФон не настроена: в «Настройках» → «Телефония» нужны ключ API и сотрудник-бот"
        else:
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
            async with httpx.AsyncClient(timeout=20, transport=TRANSPORT, verify=megafon_tls()) as c:
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

    async def _rpc(self, url, method, params=None):
        """JSON-RPC нового кабинета; ключ — в params.access_token."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"access_token": self.token, **(params or {})}}
        try:
            async with httpx.AsyncClient(timeout=20, transport=TRANSPORT, verify=megafon_tls()) as c:
                r = await c.post(url, json=body)
        except httpx.HTTPError as e:
            raise RuntimeError(f"ВАТС не отвечает ({type(e).__name__}) — IP сервера должен быть в «Настройки API» кабинета, "
                               "сервер — в России") from e
        if r.status_code in (401, 403):
            raise RuntimeError(f"ВАТС отклонила ключ ({r.status_code}) — скопируйте ключ заново в «Настройки API»")
        if r.status_code >= 400:
            raise RuntimeError(f"ВАТС HTTP {r.status_code}: {r.text[:200]}")
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"ВАТС ответила не JSON: {r.text[:200]}") from None
        if isinstance(j, dict) and j.get("error"):
            e = j["error"]
            msg = (e.get("message") or "") if isinstance(e, dict) else str(e)
            data = e.get("data") if isinstance(e, dict) else None
            mnem = (data or {}).get("mnemonic", "") if isinstance(data, dict) else ""
            raise RuntimeError(f"ВАТС: {method} — {msg} {mnem}".strip())
        res = j.get("result") if isinstance(j, dict) else None
        return (res or {}).get("data") if isinstance(res, dict) else None

    async def _cp_lookup(self):
        """Сотрудник-бот (по логину, ФИО, внутреннему номеру или id) и виртуальный номер для исходящих."""
        emps = await self._rpc(CP_DATAAPI, "get.employees") or []
        want = self.user.lower()
        bot = None
        for e in emps if isinstance(emps, list) else []:
            if not isinstance(e, dict):
                continue
            ext = (e.get("extension") or {}).get("extension_phone_number") if isinstance(e.get("extension"), dict) else None
            keys = {str(e.get(k) or "").lower() for k in ("id", "login", "full_name", "email")} | {str(ext or "").lower()}
            if want in keys or want.split("@")[0] in keys:
                bot = e
                break
        nums = await self._rpc(CP_DATAAPI, "get.virtual_numbers") or []
        nums = [str(n.get("virtual_phone_number") or "") for n in nums if isinstance(n, dict)] if isinstance(nums, list) else []
        nums = [n for n in nums if n]
        number = self.number or (nums[0] if nums else "")
        return {"employees": len(emps) if isinstance(emps, list) else 0, "bot": bot, "numbers": nums, "number": number}

    async def check(self):
        """Проверка без звонка: список сотрудников ВАТС; заодно определяем формат API.
        -> {"employees": n, "bot_found": bool, "api": "legacy" | "v1"}."""
        if not self.ready:
            raise RuntimeError(self.why)
        if self.api == "cp":
            r = await self._cp_lookup()
            self._emp = r if r["bot"] else None
            return {"employees": r["employees"], "bot_found": bool(r["bot"]), "api": "cp",
                    "numbers": r["numbers"], "number": r["number"]}
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

    async def completed_calls(self, pending):
        """Итоги своих звонков из Data API, в том числе когда уведомление ВАТС не настроено."""
        by_provider = {str(call["provider_id"]): call for call in pending if str(call.get("provider_id") or "").isdigit()}
        if self.api != "cp" or not by_provider:
            return []
        earliest = min(call["created"] for call in by_provider.values())
        rows = await self._rpc(CP_DATAAPI, "get.calls_report", {
            "date_from": datetime.fromtimestamp(earliest - 300).strftime("%Y-%m-%d %H:%M:%S"),
            "date_till": (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "limit": 100, "filter": {"field": "id", "operator": "in", "value": [int(key) for key in by_provider]},
            "fields": ["id", "finish_time", "talk_duration", "finish_reason", "full_record_file_link"]})
        results = []
        for row in rows or []:
            call = by_provider.get(str(row.get("id")))
            if call and row.get("finish_time"):
                _, result = parse_cp({"call_session_id": row["id"], "talk_time_duration": row.get("talk_duration"),
                                      "finish_reason": row.get("finish_reason"), "record_url": row.get("full_record_file_link")})
                results.append((call["id"], result))
        return results

    async def start(self, call, context):
        if not self.ready:
            raise RuntimeError(self.why)
        phone = call["phone"].lstrip("+")
        if self.api == "cp":
            r = self._emp or await self._cp_lookup()
            if not r["bot"]:
                raise RuntimeError(f"В ВАТС нет сотрудника «{self.user}» — проверьте поле «Сотрудник-бот»")
            if not r["number"]:
                raise RuntimeError("В ВАТС нет виртуального номера для исходящих звонков")
            self._emp = r
            d = await self._rpc(CP_CALLAPI, "start.employee_call", {
                # first_call=contact: сначала дозвон до заказчика, снял трубку — ВАТС зовёт бота (агент не говорит в гудки)
                "first_call": "contact", "switch_at_once": True, "virtual_phone_number": r["number"],
                "contact": phone, "external_id": str(call["id"]), "direction": "out",
                "employee": {"id": r["bot"]["id"]}})
            return str((d or {}).get("call_session_id") or "")
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


def parse_cp(b):
    """Уведомление нового кабинета («Завершение звонка», JSON по шаблону из «Настроек») → общий формат."""
    pid = str(b.get("call_session_id") or "").strip()
    if not pid or pid.startswith("{{"):
        return None, None
    try:
        talk = int(float(b.get("talk_time_duration") or 0))
    except (TypeError, ValueError):
        talk = 0
    reason = str(b.get("finish_reason") or "").lower()
    status = "done" if talk > 0 else "busy" if "busy" in reason else "no_answer"
    out = {"status": status, "duration": talk}
    link = b.get("record_url") or b.get("file_link")
    if isinstance(link, str) and link.startswith("https://"):
        out["recording_url"] = link
    return pid, out


def cp_template(crm_token):
    """Тело уведомления, которое владелец вставляет в кабинет ВАТС («Уведомления» → «Завершение звонка»)."""
    return json.dumps({"call_session_id": "{{call_session_id}}", "external_id": "{{external_id}}",
                       "contact_phone_number": "{{contact_phone_number}}", "finish_reason": "{{finish_reason}}",
                       "talk_time_duration": "{{talk_time_duration}}", "crm_token": crm_token}, ensure_ascii=False)


def make(on_result, cfg=None):
    """cfg — настройки из программы ({mode, url, token, user}); пустые поля — из .env."""
    cfg = cfg or {}
    name = (cfg.get("mode") or provider_name()).strip().lower()
    if name == "megafon":
        return MegafonProvider(on_result, cfg.get("url"), cfg.get("token"), cfg.get("user"), cfg.get("api"),
                               cfg.get("number"))
    if name == "mock":
        return MockProvider(on_result)
    if name in ("off", "none", "0", ""):
        return OffProvider(on_result)
    raise RuntimeError(f"Неизвестный TELEPHONY={name!r}: megafon, mock или off")
