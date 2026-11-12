import asyncio
import json
import time
from datetime import datetime

import pytest

import calls
import main
from calls import MSK

ST = {"call_enabled": True, "call_after_min": 15, "call_hours": "00:00-24:00", "call_daily": 20}


def at(h, m=0):
    return datetime(2026, 11, 12, h, m, tzinfo=MSK).timestamp()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for t in ("trucks", "loads", "dialogs", "messages", "calls", "do_not_call", "kv"):
        main.DB.execute(f"delete from {t}")
    main.LOG.clear()
    steps = []

    async def fake_step(d):
        steps.append(d["id"])
    monkeypatch.setattr(main, "agent_step", fake_step)
    return steps


def mk_dialog(phone="+7 (921) 555-12-34", status="waiting", load_id="L1"):
    raw = {"id": load_id, "loading": {"location": {"city": "СПб"}}, "unloading": {"location": {"city": "Москва"}},
           "firm": {"id": 1, "contacts": [{"id": 1, "name": "Ольга", "phones": [{"number": phone}] if phone else []}]}}
    main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status) values(?,?,?,?,?,?,?,?)",
                    (load_id, 1, json.dumps(raw), 80, "relevant", "[]", "{}", "dialog"))
    return main.DB.execute("insert into dialogs(load_id,chat_id,goal,status,auto) values(?,?,?,?,1)",
                           (load_id, "c1", "узнать ставку", status)).lastrowid


def msg(did, mine, ts, mid=None):
    main.DB.execute("insert into messages(id,dialog_id,mine,text,ts) values(?,?,?,?,?)",
                    (mid or f"m{did}-{ts}", did, mine, "текст", ts))


def add_call(did, phone, created, status="done"):
    main.DB.execute("insert into calls(dialog_id,phone,status,created) values(?,?,?,?)", (did, phone, status, created))


# --- правила ---

@pytest.mark.parametrize("raw, want", [("8 (921) 555-12-34", "+79215551234"), ("+7 921 555 12 34", "+79215551234"),
                                       ("9215551234", "+79215551234"), ("12345", None), (None, None)])
def test_norm_phone(raw, want):
    assert calls.norm_phone(raw) == want


def test_phone_of_variants():
    assert calls.phone_of({"firm": {"contacts": [{"phones": ["8-921-555-12-34"]}]}}) == "+79215551234"
    assert calls.phone_of({"firm": {"contacts": [{"phones": []}, {"mobile": "89215551234"}]}}) == "+79215551234"
    assert calls.phone_of({"firm": {}}) is None


def test_window_msk():
    assert calls.in_window("09:00-19:00", at(9)) and calls.in_window("09:00-19:00", at(18, 59))
    assert not calls.in_window("09:00-19:00", at(19)) and not calls.in_window("09:00-19:00", at(8, 59))
    assert calls.in_window("мусор", at(10))  # битое значение → 09:00–19:00


def test_blocked_reasons():
    p, did, now = "+79215551234", mk_dialog(), at(12)
    db = main.DB
    assert calls.blocked_reason(db, ST, p, did, now) is None
    assert "выключены" in calls.blocked_reason(db, {**ST, "call_enabled": False}, p, did, now)
    assert calls.blocked_reason(db, {**ST, "call_enabled": False}, p, did, now, manual=True) is None
    assert "вне окна" in calls.blocked_reason(db, {**ST, "call_hours": "09:00-19:00"}, p, did, at(20), manual=True)
    assert "нет телефона" in calls.blocked_reason(db, ST, None, did, now)
    db.execute("insert into do_not_call values(?,?,?,?)", (p, "x", 0, "t"))
    assert "стоп-лист" in calls.blocked_reason(db, ST, p, did, now)


def test_number_gap_and_per_load():
    p, did = "+79215551234", mk_dialog()
    add_call(did, p, at(10))
    assert "2 часов" in calls.blocked_reason(main.DB, ST, p, did, at(11, 30))
    assert calls.blocked_reason(main.DB, ST, p, did, at(12, 1)) is None
    add_call(did, p, at(12, 1))
    assert "2 попытки" in calls.blocked_reason(main.DB, ST, p, did, at(15))
    other = mk_dialog(load_id="L2")
    assert calls.blocked_reason(main.DB, ST, "+79990000000", other, at(15)) is None


def test_active_call_and_daily_limit():
    p, did = "+79215551234", mk_dialog()
    add_call(did, p, at(9), status="ringing")
    assert "уже идёт" in calls.blocked_reason(main.DB, ST, p, did, at(12))
    other = mk_dialog(load_id="L2")
    assert "дневной лимит" in calls.blocked_reason(main.DB, {**ST, "call_daily": 1}, "+79990000000", other, at(12))


def test_due_dialogs():
    now = time.time()
    silent, answered, fresh, closed = mk_dialog(), mk_dialog(load_id="L2"), mk_dialog(load_id="L3"), mk_dialog(status="ready", load_id="L4")
    msg(silent, 1, now - 20 * 60)
    msg(answered, 1, now - 30 * 60); msg(answered, 0, now - 25 * 60)
    msg(fresh, 1, now - 5 * 60)
    msg(closed, 1, now - 60 * 60)
    assert [d["id"] for d in calls.due_dialogs(main.DB, ST, now)] == [silent]


def test_due_dialogs_microseconds():
    now, did = time.time(), mk_dialog()
    msg(did, 1, int((now - 20 * 60) * 1e6))
    assert [d["id"] for d in calls.due_dialogs(main.DB, ST, now)] == [did]


def test_cost_and_dnc_phrases():
    assert calls.cost(61, 3) == 6 and calls.cost(0, 3) == 0 and calls.cost(60, 3) == 3
    assert calls.wants_no_calls("Заказчик: больше не звоните сюда")
    assert calls.wants_no_calls("уберите мой номер") and not calls.wants_no_calls("звоните после обеда")


# --- звонок целиком ---

def test_mock_call_end_to_end(monkeypatch, clean):
    monkeypatch.setenv("MOCK_CALL_SECONDS", "0")
    main.DB.execute("insert into kv values('call_hours', ?)", (json.dumps("00:00-24:00"),))
    did = mk_dialog()

    async def go():
        call, why = await main.start_call(main.q("select * from dialogs where id=?", did)[0], manual=True)
        assert why is None and call["status"] == "ringing" and call["provider_id"] == f"mock-{call['id']}"
        await asyncio.sleep(0.05)  # мок «дозвонился»
        return call["id"]
    cid = asyncio.run(go())
    c = main.q("select * from calls where id=?", cid)[0]
    # 2 мин × (3 связь + 6 голос)
    assert c["status"] == "done" and c["duration"] == 74 and c["cost"] == 18 and "Шестьдесят" in c["transcript"]
    m = main.q("select * from messages where id=?", f"call-{cid}")[0]
    assert m["kind"] == "call" and m["mine"] == 0
    assert clean == [did]  # negotiate по расшифровке


def test_no_answer_adds_nothing(clean):
    did = mk_dialog()
    add_call(did, "+79215551234", time.time(), status="ringing")
    cid = main.q("select id from calls")[0]["id"]
    asyncio.run(main.apply_call_result(cid, {"status": "no_answer", "duration": 0}))
    assert main.q("select status from calls")[0]["status"] == "no_answer"
    assert not main.q("select * from messages") and clean == []


def test_transcript_before_final_status(clean):
    """Бот прислал расшифровку раньше, чем ВАТС — итоговый статус; сообщение всё равно появится один раз."""
    did = mk_dialog()
    main.DB.execute("insert into calls(dialog_id,phone,status,created,provider_id) values(?,?,?,?,?)",
                    (did, "+79215551234", "ringing", time.time(), "vats-1"))
    cid = main.q("select id from calls")[0]["id"]
    asyncio.run(main.apply_call_result(cid, {"status": "talking", "transcript": "Заказчик: актуально"}))
    assert not main.q("select * from messages")
    asyncio.run(main.apply_call_result("vats-1", {"status": "done", "duration": 30}, by_provider=True))
    assert len(main.q("select * from messages where kind='call'")) == 1 and clean == [did]
    asyncio.run(main.apply_call_result("vats-1", {"status": "done", "duration": 30}, by_provider=True))
    assert clean == [did]  # повторный вебхук не запускает агента второй раз


def test_dnc_from_transcript(clean):
    did = mk_dialog()
    add_call(did, "+79215551234", time.time(), status="talking")
    cid = main.q("select id from calls")[0]["id"]
    asyncio.run(main.apply_call_result(cid, {"status": "done", "duration": 10, "transcript": "Заказчик: не звоните мне больше"}))
    assert main.q("select phone from do_not_call") == [{"phone": "+79215551234"}]


# --- API ---

def test_call_now_api(make_client, monkeypatch):
    monkeypatch.setenv("MOCK_CALL_SECONDS", "999")
    c = make_client("logist")
    main.DB.execute("insert into kv values('call_hours', ?)", (json.dumps("00:00-24:00"),))
    did = mk_dialog()
    r = c.post(f"/api/dialogs/{did}/call")
    assert r.status_code == 200 and r.json()["phone"] == "+79215551234"
    assert c.post(f"/api/dialogs/{did}/call").status_code == 400  # звонок уже идёт
    d = [x for x in c.get("/api/dialogs").json() if x["id"] == did][0]
    assert d["phone"] == "+79215551234" and len(d["calls"]) == 1
    nophone = mk_dialog(phone=None, load_id="L9")
    assert "нет телефона" in c.post(f"/api/dialogs/{nophone}/call").json()["detail"]


def test_dnc_api(make_client):
    c = make_client("logist")
    assert c.post("/api/dnc", json={"phone": "8 921 555 12 34", "reason": "просили"}).json()["phone"] == "+79215551234"
    assert c.post("/api/dnc", json={"phone": "123"}).status_code == 400
    assert c.get("/api/dnc").json()[0]["by_login"] == "logist1"
    assert c.delete("/api/dnc/+79215551234").status_code == 200 and c.get("/api/dnc").json() == []


def test_webhook(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "s3cret")
    anon = TestClient(main.app)
    did = mk_dialog()
    main.DB.execute("insert into calls(dialog_id,phone,status,created,provider_id) values(?,?,?,?,?)",
                    (did, "+79215551234", "ringing", time.time(), "abc"))
    assert anon.post("/api/telephony/webhook/wrong", data={"cmd": "history"}).status_code == 403
    r = anon.post("/api/telephony/webhook/s3cret", data={"cmd": "history", "callid": "abc", "status": "Success",
                                                        "duration": "125"})
    assert r.json() == {"ok": True, "matched": True}
    c = main.q("select * from calls")[0]
    assert (c["status"], c["duration"], c["cost"]) == ("done", 125, 27)
    r = anon.post("/api/telephony/webhook/s3cret", json={"provider_id": "abc", "transcript": "Заказчик: да"})
    assert r.json()["matched"] and main.q("select kind from messages")[0]["kind"] == "call"


def test_webhook_disabled_without_secret(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "")
    assert TestClient(main.app).post("/api/telephony/webhook/", data={}).status_code in (403, 404, 405)
    assert TestClient(main.app).post("/api/telephony/webhook/x", data={}).status_code == 403


# --- после ревью звонков и безопасности ---

def ringing(did, pid="p1", created=None, **kw):
    main.DB.execute("insert into calls(dialog_id,phone,status,created,provider_id,transcript,duration) values(?,?,?,?,?,?,?)",
                    (did, "+79215551234", kw.get("status", "ringing"), created or time.time(), pid,
                     kw.get("transcript"), kw.get("duration", 0)))
    return main.q("select id from calls order by id desc limit 1")[0]["id"]


def test_duplicate_transcript_runs_agent_once(clean):
    did = mk_dialog()
    cid = ringing(did)
    res = {"status": "done", "duration": 40, "transcript": "Заказчик: да"}
    asyncio.run(main.apply_call_result(cid, res))
    asyncio.run(main.apply_call_result(cid, res))
    assert clean == [did] and len(main.q("select * from messages")) == 1


def test_empty_values_do_not_overwrite(clean):
    did = mk_dialog()
    cid = ringing(did)
    asyncio.run(main.apply_call_result(cid, {"status": "done", "duration": 90, "transcript": "Заказчик: да"}))
    asyncio.run(main.apply_call_result(cid, {"status": "done", "duration": 0, "transcript": "", "recording_url": ""}))
    c = main.q("select * from calls")[0]
    assert (c["transcript"], c["duration"], c["cost"]) == ("Заказчик: да", 90, 18)


def test_done_not_downgraded(clean):
    did = mk_dialog()
    cid = ringing(did)
    asyncio.run(main.apply_call_result(cid, {"status": "done", "duration": 30, "transcript": "Заказчик: да"}))
    asyncio.run(main.apply_call_result(cid, {"status": "no_answer"}))
    assert main.q("select status from calls")[0]["status"] == "done"


def test_garbage_result_ignored(clean):
    did = mk_dialog()
    cid = ringing(did)
    asyncio.run(main.apply_call_result(cid, {"status": "completed", "duration": "abc", "recording_url": 5}))
    assert main.q("select status, duration from calls")[0] == {"status": "ringing", "duration": 0}
    asyncio.run(main.apply_call_result(cid, ["not", "a", "dict"]))


def test_stale_call_reaped(clean):
    did = mk_dialog()
    ringing(did, created=time.time() - 30 * 60)
    asyncio.run(main.reap_stale_calls())
    c = main.q("select * from calls")[0]
    assert c["status"] == "failed" and c["error"] == "нет итога от телефонии"
    assert "звонок уже идёт" not in (calls.blocked_reason(main.DB, ST, "+79215551234", did, time.time() + 3 * 3600) or "")


def test_failed_before_telephony_not_counted():
    did = mk_dialog()
    for _ in range(3):
        main.DB.execute("insert into calls(dialog_id,phone,status,created) values(?,?,?,?)",
                        (did, "+79215551234", "failed", time.time() - 3600))
    assert calls.blocked_reason(main.DB, ST, "+79215551234", did) is None  # в лимиты не идут


def test_telephony_glitch_does_not_redial_every_minute():
    did = mk_dialog()
    main.DB.execute("insert into calls(dialog_id,phone,status,created,error) values(?,?,?,?,?)",
                    (did, "+79215551234", "failed", time.time() - 60, "телефония не вернула id звонка"))
    assert "повтор позже" in calls.blocked_reason(main.DB, ST, "+79215551234", did)
    assert calls.blocked_reason(main.DB, ST, "+79215551234", did, manual=True) is None  # логист может сам
    for i in range(3):
        main.DB.execute("insert into calls(dialog_id,phone,status,created) values(?,?,?,?)",
                        (did, "+79215551234", "failed", time.time() - 7200 - i))
    assert "слишком много" in calls.blocked_reason(main.DB, ST, "+79215551234", did, time.time() + 3600)


def test_telephony_error_is_reported(make_client, monkeypatch):
    class Broken:
        ready, mode, why = True, "megafon", ""

        async def start(self, call, ctx):
            raise RuntimeError("ВАТС не настроена")
    monkeypatch.setattr(main, "PHONE", Broken())
    main.DB.execute("insert into kv values('call_hours', ?)", (json.dumps("00:00-24:00"),))
    c = make_client("logist")
    r = c.post(f"/api/dialogs/{mk_dialog()}/call")
    assert r.status_code == 502 and "ВАТС не настроена" in r.json()["detail"]


def test_ts_units():
    now, did = time.time(), mk_dialog()
    msg(did, 1, int((now - 20 * 60) * 1e3))  # миллисекунды
    other = mk_dialog(load_id="L2")
    msg(other, 1, int(now - 60))  # секунды, свежее — звонить рано
    assert [d["id"] for d in calls.due_dialogs(main.DB, ST, now)] == [did]
    assert calls.ts_unit(1.7e15) == 1e6 and calls.ts_unit(1.7e12) == 1e3 and calls.ts_unit(1.7e9) == 1


def test_overnight_window_and_validation():
    assert calls.in_window("22:00-06:00", at(23)) and calls.in_window("22:00-06:00", at(5))
    assert not calls.in_window("22:00-06:00", at(12))
    assert calls.valid_hours("09:00-19:00") and calls.valid_hours("00:00-24:00")
    assert not calls.valid_hours("9-19") and not calls.valid_hours("10:00-10:00")


def test_settings_reject_bad_hours(make_client):
    c = make_client("owner")
    assert c.put("/api/settings", json={"call_hours": "с утра"}).status_code == 400
    assert c.put("/api/settings", json={"call_hours": "10:00-18:00"}).json()["call_hours"] == "10:00-18:00"


def test_dnc_only_customer_lines():
    assert not calls.wants_no_calls("Агент: если неудобно — не звоните нам, мы сами\nЗаказчик: хорошо")
    assert calls.wants_no_calls("Заказчик: хватит звонить")
    assert not calls.wants_no_calls("Заказчик: не звоните на этот номер, звоните Ивану")


def test_recording_url_allowlist(monkeypatch):
    monkeypatch.setenv("RECORDING_HOSTS", "vats.example.ru")
    monkeypatch.delenv("MEGAFON_API_URL", raising=False)
    with pytest.raises(ValueError, match="не в RECORDING_HOSTS"):
        main.safe_recording_url("http://169.254.169.254/latest/meta-data")
    monkeypatch.setenv("RECORDING_HOSTS", "localhost")
    with pytest.raises(ValueError, match="внутренний адрес"):
        main.safe_recording_url("http://localhost/x.mp3")


# --- после сверки с ТЗ ---

def test_logist_sees_only_goal(make_client):
    lg, ow = make_client("logist"), make_client("owner")
    assert set(lg.get("/api/settings").json()) == {"default_goal", "goal_presets", "send_enabled", "auto_start", "call_enabled", "voice_ready"}
    assert "call_daily" in ow.get("/api/settings").json()
    assert lg.get("/api/costs").status_code == 403 and ow.get("/api/costs").status_code == 200


def test_no_chat_but_phone_creates_call_dialog(monkeypatch):
    async def broken_chat(*a):
        raise RuntimeError("мессенджер недоступен")
    monkeypatch.setattr(main.ati, "open_chat", broken_chat)
    raw = {"id": "N1", "firm": {"id": 3, "name": "Ф", "contacts": [{"id": 1, "name": "И", "phones": ["89215551234"]}]}}
    main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status) values('N1',1,?,90,'relevant','[]','{}','new')",
                    (json.dumps(raw),))
    d = asyncio.run(main.start_dialog(main.q("select * from loads where id='N1'")[0], "цель"))
    assert d["chat_id"] is None and d["status"] == "waiting"
    assert [x["id"] for x in calls.due_dialogs(main.DB, ST)] == [d["id"]]  # звонить сразу, без ожидания чата
    with pytest.raises(ValueError, match="уже ведёт"):  # второй диалог по тому же грузу не открываем
        asyncio.run(main.start_dialog(main.q("select * from loads where id='N1'")[0], "цель"))
    main.DB.execute("update dialogs set status='declined'")
    raw["firm"]["contacts"][0]["phones"] = []
    main.DB.execute("update loads set raw=?, status='new' where id='N1'", (json.dumps(raw),))
    with pytest.raises(ValueError, match="телефона нет"):
        asyncio.run(main.start_dialog(main.q("select * from loads where id='N1'")[0], "цель"))


def test_dnc_from_chat_message():
    did = mk_dialog()
    main.dnc_from_dialog(main.q("select * from dialogs where id=?", did)[0], "написали в чате")
    assert main.q("select phone, reason from do_not_call") == [{"phone": "+79215551234", "reason": "написали в чате"}]


def test_attempts_counted_per_load():
    first = mk_dialog()
    add_call(first, "+79215551234", at(9))
    add_call(first, "+79990000000", at(10))
    again = main.DB.execute("insert into dialogs(load_id,chat_id,goal,status,auto) values('L1','c2','g','waiting',1)").lastrowid
    assert "2 попытки" in calls.blocked_reason(main.DB, ST, "+79991112233", again, at(15))


def test_send_manual_404(make_client):
    assert make_client("logist").post("/api/dialogs/9999/send", json={"text": "x"}).status_code == 404


def test_llm_error_goes_to_summary(monkeypatch):
    did = mk_dialog()
    main.DB.execute("update loads set metrics='{}' where id='L1'")
    main.DB.execute("insert into trucks(id,name,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,free_from)"
                    " values(1,'T','СПб',1,59.9,30.3,150,1,'тент',2.7,20,'2026-01-01')")

    async def bad(*a):
        raise main.llm.LLMError("LLM вернула невалидный JSON: oops")
    monkeypatch.setattr(main.agent, "negotiate", bad)
    asyncio.run(main._agent_step(main.q("select * from dialogs where id=?", did)[0]))
    d = main.q("select status, summary from dialogs where id=?", did)[0]
    assert d["status"] == "waiting" and "невалидный JSON" in d["summary"]
