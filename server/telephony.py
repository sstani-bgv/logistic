"""Адаптеры телефонии. TELEPHONY=mock (по умолчанию) | megafon.

Итог звонка приходит в main.apply_call_result() одним форматом:
  {"status": "talking"|"done"|"no_answer"|"busy"|"failed", "duration": сек, "recording_url": str,
   "transcript": str, "provider_id": str}
— от мока, от вебхука ВАТС (запись, длительность) и от голосового бота (расшифровка).
"""
import asyncio
import os

import httpx

FINAL = ("done", "no_answer", "busy", "failed")


def provider_name():
    return os.environ.get("TELEPHONY", "mock")


class MockProvider:
    """Локальная имитация: через MOCK_CALL_SECONDS «дозвонились» и получили расшифровку."""

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
            transcript = ("Агент: Добрый день! Звоню по вашему грузу на АТИ, "
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
    """ВАТС МегаФон, CRM API (команда makeCall от сотрудника-бота; события — вебхуком history).

    ПРОВЕРИТЬ В СПАЙКЕ: точный адрес и формат API конкретной ВАТС клиента, как звонок попадает в голосовой бот
    (сотрудник-бот с SIP-аккаунтом, который регистрирует наш voice-сервис) и поля вебхука.
    """

    def __init__(self, on_result):
        self.on_result = on_result
        self.url = os.environ.get("MEGAFON_API_URL", "")  # https://<vats>.megapbx.ru/sys/crm_api.wcgp
        self.token = os.environ.get("MEGAFON_API_KEY", "")
        self.user = os.environ.get("MEGAFON_BOT_USER", "")  # логин сотрудника-бота в ВАТС

    async def start(self, call, context):
        if not (self.url and self.token and self.user):
            raise RuntimeError("ВАТС МегаФон не настроена: MEGAFON_API_URL, MEGAFON_API_KEY, MEGAFON_BOT_USER")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(self.url, data={"cmd": "makeCall", "token": self.token, "user": self.user,
                                             "phone": call["phone"].lstrip("+")})
        if r.status_code >= 400:
            raise RuntimeError(f"ВАТС HTTP {r.status_code}: {r.text[:200]}")
        try:
            j = r.json()
        except ValueError:
            j = {}
        return str(j.get("uuid") or j.get("callid") or "")


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


def make(on_result):
    name = provider_name()
    if name == "megafon":
        return MegafonProvider(on_result)
    if name == "mock":
        return MockProvider(on_result)
    raise RuntimeError(f"Неизвестный TELEPHONY={name!r}: mock или megafon")
