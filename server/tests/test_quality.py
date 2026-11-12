"""Найденное аудитом: состояние систем, телефония по умолчанию, защита ответа, повторы, МегаФон, бэкап, маршруты."""
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
import main
import telephony
from test_fleet import dialog, load_raw, truck  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
REPLY = {"reply": "Добрый день!", "status": "waiting", "summary": "s",
         "facts": {k: None for k in ("actual", "price", "payment", "address", "time", "other")}}


@pytest.fixture(autouse=True)
def clean():
    for t in ("trucks", "loads", "dialogs", "messages", "rules", "kv", "calls", "scans"):
        main.DB.execute(f"delete from {t}")
    for k in main.HEALTH:
        main.HEALTH[k] = {"ok": None, "err": "", "ts": 0.0}
    main.STEPPING.clear()
    main.AGAIN.clear()


def kv(**kw):
    for k, v in kw.items():
        main.DB.execute("insert into kv values(?,?) on conflict(k) do update set v=excluded.v", (k, json.dumps(v)))


def parts(h=None):
    return {(p["part"], p["level"]): p["text"] for p in (h or main.health())["problems"]}


# --- состояние систем ---

def test_ati_401_is_visible(monkeypatch):
    t = truck()
    resp = httpx.Response(401, request=httpx.Request("POST", "https://loads.ati.su/x"))

    async def search(tr, page):
        raise httpx.HTTPStatusError("401", request=resp.request, response=resp)
    monkeypatch.setattr(main.ati, "search", search)
    monkeypatch.setattr(main.asyncio, "sleep", lambda s, _r=asyncio.sleep: _r(0))

    async def once():
        task = asyncio.create_task(main.monitor())
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(once())
    assert t and "отклонила токен (401)" in parts()[("ati", "error")]
    assert main.health()["ati_ok"] is False


def test_llm_not_configured_and_failing(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    assert "LLM_BASE_URL" in parts()[("llm", "error")]
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    main.mark("llm", False, "HTTP 402: нет денег")
    assert "402" in parts()[("llm", "error")]


def test_telephony_states(monkeypatch):
    monkeypatch.setattr(main, "PHONE", telephony.OffProvider(None))
    kv(call_enabled=True)
    assert "телефония не подключена" in parts()[("phone", "error")]
    monkeypatch.setattr(main, "PHONE", telephony.MockProvider(None))
    assert "имитации" in parts()[("phone", "warn")]
    monkeypatch.setenv("MEGAFON_API_URL", "https://v.megapbx.ru/sys/crm_api.wcgp")
    monkeypatch.setenv("MEGAFON_API_KEY", "k")
    monkeypatch.setenv("MEGAFON_BOT_USER", "bot")
    monkeypatch.setattr(main, "PHONE", telephony.MegafonProvider(None))
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "")
    assert "вебхук ВАТС выключен" in parts()[("phone", "error")]


def test_default_telephony_is_off(monkeypatch):
    monkeypatch.delenv("TELEPHONY", raising=False)
    assert telephony.make(None).mode == "off"
    monkeypatch.setenv("TELEPHONY", "nonsense")
    with pytest.raises(RuntimeError, match="Неизвестный TELEPHONY"):
        telephony.make(None)


def test_off_telephony_manual_call_says_why(monkeypatch, make_client):
    monkeypatch.setattr(main, "PHONE", telephony.OffProvider(None))
    did = dialog(truck(), {**load_raw(), "firm": {"id": 1, "contacts": [{"id": 1, "name": "О", "phones": ["+79215550101"]}]}}, status="waiting")
    r = make_client("logist").post(f"/api/dialogs/{did}/call")
    assert r.status_code == 400 and "телефония не подключена" in r.json()["detail"]
    assert main.q("select count(*) n from calls")[0]["n"] == 0  # попытка не создаётся
    kv(call_enabled=True, call_hours="00:00-24:00")
    assert asyncio.run(main.call_tick()) == []


def test_backup_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "BACKUP_DIR", str(tmp_path))
    assert "бэкапов ещё не было" in main.backup_state()
    (tmp_path / "last_ok").write_text(str(time.time()))
    assert main.backup_state() is None
    (tmp_path / "last_ok").write_text(str(time.time() - 30 * 3600))
    assert "30 ч назад" in main.backup_state()
    (tmp_path / "last_error").write_text("диск полон")
    assert "диск полон" in main.backup_state()


# --- ответ агента, похожий на обещание, не уходит сам ---

@pytest.mark.parametrize("reply, held", [("Подтверждаем рейс, ставим машину завтра", True), ("Хорошо, берём!", True),
                                         ("Логист перезвонит и подтвердит. Адрес погрузки?", False),
                                         ("Вы согласны на оплату по факту?", False), ("Берём ли мы — решает логист.", False),
                                         ("Нас устраивает ставка, ждите машину.", True), ("Машина подходит?", False),
                                         ("Не возим кирпич — бьётся тент, это наше правило", True), ("Ок" * 400, True)])
def test_risky_reply_becomes_draft(monkeypatch, reply, held):
    did = dialog(truck(), load_raw(), status="waiting")
    main.DB.execute("insert into rules(match,value,block,delta,text,active) values('cargo','кирпич',1,0,'Не возим кирпич — бьётся тент',1)")
    kv(send_enabled=True)

    async def neg(*a):
        return {**REPLY, "reply": reply}
    sent = []

    async def send(chat, text):
        sent.append(text)
        return {"id": "m", "ts": time.time()}
    monkeypatch.setattr(main.agent, "negotiate", neg)
    monkeypatch.setattr(main.ati, "send", send)
    asyncio.run(main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    draft = main.q("select draft from dialogs where id=?", did)[0]["draft"]
    assert (draft == reply and not sent) if held else (sent == [reply] and draft is None)
    if held:  # логист видит, что ответ задержан
        assert main.q("select summary from dialogs where id=?", did)[0]["summary"].startswith("⚠ Ответ задержан")


# --- повтор после сбоя модели: потерянный ответ заказчика ---

def test_lost_reply_is_retried(monkeypatch):
    did = dialog(truck(), load_raw(), status="waiting")
    main.DB.execute("update dialogs set auto=1, fails=1, updated=? where id=?", (time.time() - 600, did))
    main.DB.execute("insert into messages(id,dialog_id,mine,text,ts) values('a',?,1,'Актуально?',1),('b',?,0,'Да, 60 тысяч',2)", (did, did))
    seen = []

    async def neg(*a):
        seen.append(1)
        return REPLY
    monkeypatch.setattr(main.agent, "negotiate", neg)
    kv(send_enabled=False)
    assert asyncio.run(main.retry_failed()) == [did] and seen
    assert main.q("select fails from dialogs where id=?", did)[0]["fails"] == 0
    assert asyncio.run(main.retry_failed()) == []  # починилось — больше не повторяем


def test_error_keeps_ready_status(monkeypatch):
    did = dialog(truck(), load_raw(), status="ready")

    async def boom(*a):
        raise main.llm.LLMError("модель недоступна")
    monkeypatch.setattr(main.agent, "negotiate", boom)
    asyncio.run(main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    d = main.q("select * from dialogs where id=?", did)[0]
    assert d["status"] == "ready" and "недоступна" in d["summary"]  # «Готово» сбоем модели не понижаем


def test_background_task_error_is_logged():
    async def go():
        async def boom():
            raise ValueError("сломалось")
        main.spawn(boom())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    main.LOG.clear()
    asyncio.run(go())
    assert any("сломалось" in x["text"] for x in main.LOG)


# --- МегаФон ---

@pytest.mark.parametrize("form, want", [
    ({"cmd": "history", "callid": "x", "status": "Success", "duration": "61", "link": "https://v/r.mp3"},
     ("x", {"status": "done", "duration": 61, "recording_url": "https://v/r.mp3"})),
    ({"cmd": "history", "callid": "x", "status": "Missed"}, ("x", {"status": "no_answer", "duration": 0, "recording_url": None})),
    ({"cmd": "history", "callid": "x", "status": "NOT_AVAILABLE", "duration": "abc"}, ("x", {"status": "failed", "duration": 0, "recording_url": None})),
    ({"cmd": "history", "callid": "x", "status": "Busy"}, ("x", {"status": "busy", "duration": 0, "recording_url": None})),
    ({"cmd": "event", "callid": "x"}, (None, None)),
])
def test_parse_megafon(form, want):
    assert telephony.parse_megafon(form) == want


def test_megafon_start(monkeypatch):
    for k, v in {"MEGAFON_API_URL": "https://v.megapbx.ru/sys/crm_api.wcgp", "MEGAFON_API_KEY": "tok", "MEGAFON_BOT_USER": "bot"}.items():
        monkeypatch.setenv(k, v)
    got = []

    def handler(req):
        got.append(dict(httpx.QueryParams(req.content.decode())))
        return httpx.Response(200, json={"uuid": "u-1"}) if len(got) == 1 else httpx.Response(500, text="боль")
    monkeypatch.setattr(telephony, "TRANSPORT", httpx.MockTransport(handler))
    p = telephony.MegafonProvider(None)
    assert asyncio.run(p.start({"id": 1, "phone": "+79215551234"}, {})) == "u-1"
    assert got[0] == {"cmd": "makeCall", "token": "tok", "user": "bot", "phone": "79215551234"}
    with pytest.raises(RuntimeError, match="ВАТС HTTP 500"):
        asyncio.run(p.start({"id": 2, "phone": "+79215551234"}, {}))


def test_megafon_without_id_still_counts(monkeypatch):
    class NoId:
        ready, mode, why = True, "megafon", ""

        async def start(self, call, ctx):
            return ""
    monkeypatch.setattr(main, "PHONE", NoId())
    kv(call_hours="00:00-24:00")
    did = dialog(truck(), {**load_raw(), "firm": {"id": 1, "contacts": [{"id": 1, "name": "О", "phones": ["+79215550202"]}]}}, status="waiting")
    call, why = asyncio.run(main.start_call(main.q("select * from dialogs where id=?", did)[0], manual=True))
    assert why is None and call["provider_id"] == f"unknown-{call['id']}"  # попытка учтена — не перезваниваем каждые 30 минут


# --- маршруты и вход ---

def test_every_api_route_needs_login():
    anon = TestClient(main.app)
    public = {"/api/login", "/api/logout", "/api/health"}
    checked = 0
    for r in main.app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or path in public or path.startswith(("/api/telephony/", "/api/voice/")):
            continue
        url = path.replace("{did}", "1").replace("{tid}", "1").replace("{lid}", "x").replace("{cid}", "1") \
                  .replace("{rid}", "1").replace("{uid}", "1").replace("{phone}", "1").replace("{path:path}", "x")
        for m in getattr(r, "methods", []) or []:
            if m in ("HEAD", "OPTIONS"):
                continue
            assert anon.request(m, url).status_code == 401, (m, path)
            checked += 1
    assert checked > 25


def test_cookie_secure_flag(monkeypatch, make_client):
    make_client("logist", "sec")
    monkeypatch.setattr(auth, "COOKIE_SECURE", True)
    r = TestClient(main.app).post("/api/login", json={"login": "sec", "password": "password123"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_patch_cannot_set_writing_and_goal_limit(make_client):
    did = dialog(truck(), load_raw(), status="waiting")
    c = make_client("logist")
    assert c.patch(f"/api/dialogs/{did}", json={"status": "writing"}).status_code == 400
    assert c.post("/api/loads/L1/agent", json={"goal": "x" * 1501}).status_code == 422
    assert make_client("owner").put("/api/settings", json={"default_goal": "x" * 1501}).status_code == 400


# --- бэкап и восстановление (скрипты из deploy/backup) ---

@pytest.mark.skipif(not (shutil.which("sqlite3") and shutil.which("gzip") and shutil.which("bash")), reason="нет sqlite3/gzip/bash")
def test_backup_and_restore_scripts(tmp_path):
    data, out = tmp_path / "data", tmp_path / "bk"
    data.mkdir()
    db = sqlite3.connect(data / "data.db")
    db.execute("create table trucks(id int)")
    db.execute("create table dialogs(id int)")
    db.executemany("insert into trucks values(?)", [(1,), (2,)])
    db.commit()
    db.close()
    env = {**os.environ, "DATA": str(data), "OUT": str(out), "BACKUP_BUCKET": "", "RCLONE_CONFIG_S3_TYPE": ""}
    sh = ROOT / "deploy" / "backup"
    r = subprocess.run(["bash", sh / "backup.sh"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "last_ok").is_file() and "внешнее хранилище не настроено" in (out / "last_error").read_text()
    assert list((out / "db").glob("data-*.db.gz"))

    db = sqlite3.connect(data / "data.db")
    db.execute("delete from trucks")
    db.commit()
    db.close()
    r = subprocess.run(["bash", sh / "restore.sh", "latest"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert sqlite3.connect(data / "data.db").execute("select count(*) from trucks").fetchone()[0] == 2
    assert list(data.glob("data.db.before-restore-*"))

    bad = out / "db" / "data-bad.db.gz"
    bad.write_bytes(b"not gzip")
    before = (data / "data.db").read_bytes()
    r = subprocess.run(["bash", sh / "restore.sh", str(bad)], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and (data / "data.db").read_bytes() == before  # битый бэкап базу не портит


def test_empty_ati_token_message(monkeypatch):
    monkeypatch.setattr(main.ati, "TOKEN", "")
    assert "ATI_TOKEN не задан" in main.ati_err(httpx.LocalProtocolError("Illegal header value b'Bearer '"))


def test_rule_add_when_model_down(monkeypatch, make_client):
    async def down(*a, **k):
        raise main.llm.LLMError("LLM HTTP 402")
    monkeypatch.setattr(main.agent, "learn_rule", down)
    r = make_client("logist").post("/api/rules", json={"text": "не возим кирпич"})
    assert r.status_code == 503 and "402" in r.json()["detail"]


def test_default_goal_shown_with_number(make_client):
    kv(min_rate_km=60)
    s = make_client("logist").get("/api/settings").json()
    assert "{min_rate_km}" in s["default_goal"] and "{" not in s["default_goal_text"] and "60 ₽/км" in s["default_goal_text"]


def test_add_truck_when_ati_down_says_why(monkeypatch, make_client):
    async def down(*a, **k):
        raise httpx.ConnectError("нет сети")
    monkeypatch.setattr(main.ati.client, "get", down)
    r = make_client("owner").post("/api/trucks", json={"name": "T", "city_name": "Москва", "free_from": "2026-11-12"})
    assert r.status_code == 502 and "справочнике АТИ" in r.json()["detail"]


def test_rescore_only_fresh_and_purge_old():
    t = truck()
    old, fresh = time.time() - 40 * 86400, time.time()
    rows = [("OLD", old, "new"), ("OLDREJ", old, "rejected"), ("OLDDLG", old, "dialog"), ("FRESH", fresh, "new")]
    for lid, upd, st in rows:
        main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status,updated) values(?,?,?,?,?,?,?,?,?)",
                        (lid, t, json.dumps({**load_raw(), "id": lid}), 0, "x", "[]", "{}", st, upd))
    main.DB.execute("insert into dialogs(load_id,goal,status) values('OLDDLG','g','taken')")
    main.rescore()
    scored = {r["id"]: r["verdict"] for r in main.q("select id, verdict from loads")}
    assert scored["FRESH"] != "x" and scored["OLD"] == "x"  # старое не пересчитываем
    assert main.purge_old_loads() == 1
    assert sorted(r["id"] for r in main.q("select id from loads")) == ["FRESH", "OLDDLG", "OLDREJ"]  # с переговорами и отказом — храним


def test_taken_uses_large_city_hint_when_lookup_fails(monkeypatch, make_client):
    tid = truck()
    raw = {**load_raw(), "id": "LC"}
    raw["unloading"] = {"location": {"city": "Чехов г.", "lat": 55.11, "lon": 37.51}, "firstDate": "2099-01-05T00:00:00",
                        "largeCities": [{"cityId": 3611, "city": "Москва", "distance": 71}, {"cityId": 1257, "city": "Подольск", "distance": 34}]}
    did = dialog(tid, raw, status="ready")

    async def down(name):
        raise main.HTTPException(502, "АТИ недоступна")
    monkeypatch.setattr(main, "city", down)
    assert make_client("logist").patch(f"/api/dialogs/{did}", json={"status": "taken"}).status_code == 200
    t = main.q("select * from trucks where id=?", tid)[0]
    assert (t["status"], t["city_name"], t["city_id"], t["lat"], t["free_from"]) == ("busy", "Чехов г.", 1257, 55.11, "2099-01-05")


def test_taken_strips_city_suffix(monkeypatch, make_client):
    tid = truck()
    raw = {**load_raw(), "id": "SX"}
    raw["unloading"] = {"location": {"city": "Колпино г.", "lat": 59.75, "lon": 30.6}, "firstDate": "2099-01-05T00:00:00"}
    did = dialog(tid, raw, status="ready")
    asked = []

    async def lookup(name):
        asked.append(name)
        if name == "Колпино":
            return {"CityName": "Колпино", "CityId": 1165, "Latitude": 59.75, "Longitude": 30.6}
        raise main.HTTPException(400, "не найден")
    monkeypatch.setattr(main, "city", lookup)
    make_client("logist").patch(f"/api/dialogs/{did}", json={"status": "taken"})
    assert asked == ["Колпино г.", "Колпино"] and main.q("select city_id from trucks where id=?", tid)[0]["city_id"] == 1165
