"""Приёмка по ТЗ, раздел 5 — одним сквозным сценарием через API и фоновые циклы.

Подменены только внешние системы: биржа АТИ, модель и телефония (мок). Логика — настоящая:
scan_truck → auto_start → agent_step → call_tick → MockProvider → apply_call_result → finish_call → negotiate.
"""
import asyncio
import json
import time
from datetime import date, timedelta

import pytest

import calls
import main

TODAY = date.today()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
UNLOAD = (TODAY + timedelta(days=3)).isoformat()
PHONE = "+79215550011"
SPB = {"CityName": "Санкт-Петербург", "CityId": 1, "Latitude": 59.9387, "Longitude": 30.3162}


def ati_load(lid="A1", firm=True):
    l = {"id": lid, "loadNumber": "777", "note": "", "similarCount": 0, "route": {"distance": 710},
         "loading": {"location": {"city": "Санкт-Петербург", "lat": 59.95, "lon": 30.33, "cityId": 1}, "firstDate": TOMORROW},
         "unloading": {"location": {"city": "Москва", "lat": 55.75, "lon": 37.62, "cityId": 3611}, "firstDate": UNLOAD},
         "load": {"cargoType": "ТНП", "weight": 10, "height": 2.4}, "truck": {"carTypesBitSum": 1},
         "rate": {"priceNds": 70000, "price": 70000, "moneyType": 1}}
    if firm:
        l["firm"] = {"id": 42, "name": "ООО Груз", "profile": "грузовладелец", "isConfirmedShipper": True,
                     "rating": {"score": 5}, "contacts": [{"id": 7, "name": "Ольга", "phones": [{"number": PHONE}]}]}
    return l


class FakeATI:
    def __init__(self, loads):
        self.loads, self.sent, self.chats = loads, [], []

    async def search(self, truck, page):
        return (self.loads, len(self.loads)) if page == 1 else ([], len(self.loads))

    async def open_chat(self, alias, name, firm_name):
        self.chats.append(alias)
        return f"chat-{len(self.chats)}"

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"id": f"msg-{len(self.sent)}", "ts": time.time()}

    async def history(self, chat_id, since=None):
        return []


def negotiate_fake(seen):
    """Модель: без звонка — задаёт вопрос и ждёт; по расшифровке звонка — заполняет факты и ставит ready."""
    async def negotiate(load, metrics, truck, goal, chat, notes, me_name):
        seen.append({"goal": goal, "chat": chat})
        called = [x["text"] for x in chat if x["text"].startswith("[РАСШИФРОВКА ЗВОНКА]")]
        if not called:
            return {"reply": "Добрый день! Груз актуален? Подскажите адрес и время погрузки.", "status": "waiting",
                    "summary": "Спросили актуальность", "facts": {k: None for k in ("actual", "price", "payment", "address", "time", "other")}}
        return {"reply": None, "status": "ready", "summary": "По звонку: актуален, 60 000 с НДС, 5 б.д.",
                "facts": {"actual": "актуален", "price": "60 000 ₽ с НДС", "payment": "5 банковских дней",
                          "address": None, "time": "завтра с 9:00", "other": None}}
    return negotiate


@pytest.fixture
def world(monkeypatch, make_client):
    for t in ("trucks", "loads", "dialogs", "messages", "rules", "kv", "calls", "do_not_call", "scans"):
        main.DB.execute(f"delete from {t}")
    main.STEPPING.clear()
    fake = FakeATI([ati_load()])
    for name in ("search", "open_chat", "send", "history"):
        monkeypatch.setattr(main.ati, name, getattr(fake, name))

    async def city(name):
        return SPB if "Петербург" in name else {"CityName": name, "CityId": 3611, "Latitude": 55.75, "Longitude": 37.62}
    monkeypatch.setattr(main, "city", city)
    seen = []
    monkeypatch.setattr(main.agent, "negotiate", negotiate_fake(seen))
    monkeypatch.setattr(main.asyncio, "sleep", lambda s, _r=asyncio.sleep: _r(0))  # «темп человека» в проходе — не ждём
    monkeypatch.setenv("MOCK_CALL_SECONDS", "0")
    monkeypatch.setattr(main, "PHONE", main.telephony.MockProvider(main.apply_call_result))
    owner, logist = make_client("owner", "boss"), make_client("logist", "olga")
    return {"ati": fake, "seen": seen, "owner": owner, "logist": logist}


async def settle():
    """Дождаться фоновых задач: шаги агента, итог мок-звонка и разбор расшифровки."""
    for _ in range(50):
        await asyncio.sleep(0)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending and not main.TASKS:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def run(coro_fn):
    async def go():
        r = await coro_fn()
        await settle()
        return r
    return asyncio.run(go())


def truck_body(name, status="free", free_from=None):
    return {"name": name, "city_name": "Санкт-Петербург", "radius": 150, "body_mask": 1, "body_name": "тент",
            "height": 2.7, "capacity": 20, "free_from": free_from or TODAY.isoformat(), "status": status}


def test_acceptance_end_to_end(world):
    ati, owner, logist = world["ati"], world["owner"], world["logist"]

    # --- машины: свободная и занятая надолго
    assert owner.post("/api/trucks", json=truck_body("Volvo")).status_code == 200
    assert owner.post("/api/trucks", json=truck_body("Scania", "busy", (TODAY + timedelta(days=10)).isoformat())).status_code == 200
    trucks = {t["name"]: t for t in logist.get("/api/trucks").json()}
    assert trucks["Volvo"]["status"] == "free" and trucks["Scania"]["status"] == "busy"

    # --- «Грузы находятся только под свободные машины»
    where, args = main.fleet.scan_filter()
    scanned = [t["name"] for t in main.q(f"select * from trucks where {where}", *args)]
    assert scanned == ["Volvo"]
    for t in main.q(f"select * from trucks where {where}", *args):
        run(lambda t=t: main.scan_truck(t))
    loads = logist.get("/api/loads").json()
    assert [l["id"] for l in loads] == ["A1"] and loads[0]["truck_id"] == trucks["Volvo"]["id"]

    # --- агент выключен по умолчанию: ничего не пишет и не звонит
    run(main.auto_start)
    assert ati.sent == [] and main.q("select count(*) n from dialogs")[0]["n"] == 0
    assert run(main.call_tick) == []

    # --- логист не видит настройки и пользователей
    assert logist.put("/api/settings", json={"send_enabled": True}).status_code == 403
    assert logist.get("/api/users").status_code == 403
    assert "threshold" not in logist.get("/api/settings").json()

    # --- владелец включает агента и звонки (окно на весь день — чтобы тест не зависел от часа)
    r = owner.put("/api/settings", json={"send_enabled": True, "auto_start": True, "auto_score": 50, "call_enabled": True,
                                         "call_hours": "00:00-24:00", "call_after_min": 15})
    assert r.status_code == 200

    # --- агент пишет в АТИ
    run(main.auto_start)
    d = logist.get("/api/dialogs").json()[0]
    assert d["status"] == "waiting" and d["chat_id"] == "chat-1" and ati.chats == ["42.7"]
    assert ati.sent and ati.sent[0][0] == "chat-1" and "актуален" in ati.sent[0][1]
    assert world["seen"][0]["goal"] == main.settings()["default_goal"]  # та же цель, что в настройках

    # --- раньше N минут — не звоним
    assert run(main.call_tick) == []

    # --- «без ответа через N минут звонит»
    main.DB.execute("update messages set ts=? where dialog_id=?", (time.time() - 16 * 60, d["id"]))
    made = run(main.call_tick)
    assert [(x[0], x[2]) for x in made] == [(d["id"], None)]

    # --- «запись и расшифровка видны в диалоге, facts заполнены»
    d = logist.get("/api/dialogs").json()[0]
    c = d["calls"][0]
    assert c["status"] == "done" and c["duration"] == 74 and c["cost"] > 0 and c["phone"] == PHONE
    assert "Шестьдесят тысяч" in c["transcript"]
    kinds = [m["kind"] for m in d["messages"]]
    assert kinds[-1] == "call" and "Шестьдесят тысяч" in d["messages"][-1]["text"]
    assert d["status"] == "ready" and d["facts"]["price"] == "60 000 ₽ с НДС"
    assert any(x["text"].startswith("[РАСШИФРОВКА ЗВОНКА]") for x in world["seen"][-1]["chat"])  # через тот же negotiate
    assert world["seen"][-1]["goal"] == world["seen"][0]["goal"]  # в звонке та же цель, что в переписке

    # --- повторно этот диалог не звонит: последнее слово за заказчиком (звонок)
    assert run(main.call_tick) == []

    # --- «Взяли груз» делает машину занятой до выгрузки
    r = logist.patch(f"/api/dialogs/{d['id']}", json={"status": "taken"})
    assert r.status_code == 200
    v = logist.get("/api/trucks").json()
    volvo = next(t for t in v if t["name"] == "Volvo")
    assert volvo["status"] == "busy" and volvo["free_from"] == UNLOAD and volvo["city_name"] == "Москва"

    # --- автор ручных действий — в журнале
    acts = [(a["login"], a["action"]) for a in owner.get("/api/actions").json()]
    assert ("olga", "dialog_taken") in acts or any(l == "olga" and "taken" in a for l, a in acts)
    assert ("boss", "settings") in acts and ("boss", "truck_add") in acts

    # --- «занятая машина становится свободной с free_from»
    main.DB.execute("update trucks set free_from=? where name='Volvo'", ((TODAY - timedelta(days=1)).isoformat(),))
    assert volvo["id"] in main.fleet.release_due(main.DB)
    assert main.q("select status from trucks where name='Volvo'")[0]["status"] == "free"


def mk_waiting_dialog(lid, phone=PHONE, minutes_silent=20):
    raw = ati_load(lid)
    raw["firm"]["contacts"][0]["phones"] = [{"number": phone}]
    main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status,updated) values(?,?,?,?,?,?,?,?,?)",
                    (lid, 1, json.dumps(raw), 90, "relevant", "[]", json.dumps({"dist": 710}), "dialog", time.time()))
    did = main.DB.execute("insert into dialogs(load_id,chat_id,goal,status,auto,created,updated) values(?,?,?,?,1,?,?)",
                          (lid, f"c-{lid}", "цель", "waiting", time.time(), time.time())).lastrowid
    main.DB.execute("insert into messages(id,dialog_id,mine,text,ts) values(?,?,?,?,?)",
                    (f"m-{lid}", did, 1, "Актуально?", time.time() - minutes_silent * 60))
    return did


def test_no_calls_outside_window_over_limit_or_dnc(world):
    owner = world["owner"]
    owner.post("/api/trucks", json=truck_body("Volvo"))
    now_h = time.localtime().tm_hour
    closed = f"{(now_h + 2) % 24:02d}:00-{(now_h + 3) % 24:02d}:00"  # окно, в которое мы сейчас точно не попадаем
    owner.put("/api/settings", json={"call_enabled": True, "call_hours": closed, "call_daily": 1})
    mk_waiting_dialog("W1", "+79215550001")
    assert run(main.call_tick) == []  # «звонков вне окна нет»

    owner.put("/api/settings", json={"call_hours": "00:00-24:00"})
    mk_waiting_dialog("W2", "+79215550002")
    made = run(main.call_tick)
    assert sum(1 for x in made if x[1] and x[1]["status"] != "failed" and not x[2]) == 1  # «сверх лимита нет»: call_daily=1
    assert any(x[2] and "лимит" in x[2] for x in made) or len(made) == 1

    owner.put("/api/settings", json={"call_daily": 20})
    did = mk_waiting_dialog("W3", "+79215550003")
    owner.post("/api/dnc", json={"phone": "+7 921 555-00-03", "reason": "просили"})
    made = run(main.call_tick)
    assert all(x[0] != did or x[2] == "номер в стоп-листе" for x in made)  # «стоп-лист работает»
    assert not main.q("select 1 from calls c join dialogs d on d.id=c.dialog_id where d.load_id='W3'")


def test_call_asks_not_to_call_goes_to_dnc(world, monkeypatch):
    owner = world["owner"]
    owner.put("/api/settings", json={"call_enabled": True, "call_hours": "00:00-24:00", "send_enabled": True})
    did = mk_waiting_dialog("D1", "+79215550009")
    run(main.call_tick)
    c = main.q("select * from calls where dialog_id=?", did)[0]

    async def result():
        await main.apply_call_result(c["id"], {"status": "done", "duration": 20,
                                               "transcript": "Агент: Добрый день!\nЗаказчик: Не звоните мне больше."})
    run(result)
    assert main.q("select 1 from do_not_call where phone='+79215550009'")


def test_chat_missing_but_phone_calls_right_away(world, monkeypatch):
    owner = world["owner"]
    owner.post("/api/trucks", json=truck_body("Volvo"))
    owner.put("/api/settings", json={"call_enabled": True, "call_hours": "00:00-24:00"})

    async def no_chat(*a):
        raise RuntimeError("мессенджер недоступен")
    monkeypatch.setattr(main.ati, "open_chat", no_chat)
    tid = main.q("select id from trucks")[0]["id"]
    main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status,updated) values(?,?,?,?,?,?,?,?,?)",
                    ("N1", tid, json.dumps(ati_load("N1")), 90, "relevant", "[]", json.dumps({"dist": 710}), "new", time.time()))
    r = world["logist"].post("/api/loads/N1/agent", json={"goal": "узнать ставку"})
    assert r.status_code == 200 and r.json()["chat_id"] is None
    made = run(main.call_tick)  # «или чата нет, но есть телефон» — без ожидания N минут
    assert made and made[0][2] is None


def test_invalid_model_json_goes_to_summary(world, monkeypatch):
    """2.1: «при невалидном JSON — ошибка в summary»; и любая другая ошибка шага тоже видна логисту."""
    world["owner"].post("/api/trucks", json=truck_body("Volvo"))
    did = mk_waiting_dialog("J1")

    async def broken(*a):
        raise main.llm.LLMError("Модель вернула невалидный JSON")
    monkeypatch.setattr(main.agent, "negotiate", broken)
    run(lambda: main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    d = main.q("select * from dialogs where id=?", did)[0]
    assert d["status"] == "waiting" and "невалидный JSON" in d["summary"]


def test_step_error_before_model_is_visible(world):
    did = mk_waiting_dialog("J2")  # у груза нет машины (truck_id=1 не создан) — шаг падает до модели
    main.DB.execute("delete from trucks")
    run(lambda: main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    assert main.q("select summary from dialogs where id=?", did)[0]["summary"].startswith("Ошибка:")
