"""Найденное ревью: гонки, черновики, проверка настроек, устаревшие грузы, перенос груза между машинами."""
import asyncio
import json
import time

import pytest

import main
from test_fleet import dialog, load_raw, truck  # noqa: F401 — clean() из test_fleet чистит таблицы

REPLY = {"reply": "Добрый день! Груз актуален?", "status": "waiting", "summary": "спросили",
         "facts": {k: None for k in ("actual", "price", "payment", "address", "time", "other")}}


@pytest.fixture(autouse=True)
def clean():
    for t in ("trucks", "loads", "dialogs", "messages", "rules", "kv", "calls"):
        main.DB.execute(f"delete from {t}")
    main.STEPPING.clear()
    yield


def set_kv(**kw):
    for k, v in kw.items():
        main.DB.execute("insert into kv values(?,?) on conflict(k) do update set v=excluded.v", (k, json.dumps(v)))


def test_closed_dialog_not_resurrected(monkeypatch):
    did = dialog(truck(), load_raw(), status="waiting")

    async def slow(*a):  # пока агент думает, логист жмёт «Берём»
        main.DB.execute("update dialogs set status='taken' where id=?", (did,))
        return {**REPLY, "status": "ready"}
    monkeypatch.setattr(main.agent, "negotiate", slow)
    sent = []
    monkeypatch.setattr(main.ati, "send", lambda *a: sent.append(a))
    asyncio.run(main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    assert main.q("select status from dialogs where id=?", did)[0]["status"] == "taken" and not sent

    main.DB.execute("update dialogs set status='declined' where id=?", (did,))  # уже закрыт до шага
    asyncio.run(main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    assert main.q("select status from dialogs where id=?", did)[0]["status"] == "declined"


def test_one_step_per_dialog(monkeypatch):
    did = dialog(truck(), load_raw(), status="waiting")
    runs = []

    live = []

    async def think(*a):
        runs.append(1)
        live.append(1)
        assert len(live) == 1  # параллельно не думает
        await asyncio.sleep(0.05)
        live.pop()
        return REPLY
    monkeypatch.setattr(main.agent, "negotiate", think)
    set_kv(send_enabled=False)
    d = main.q("select * from dialogs where id=?", did)[0]

    async def both():
        await asyncio.gather(main.agent_step(d), main.agent_step(d))
    asyncio.run(both())
    assert len(runs) == 2  # второй вызов не теряется и не идёт параллельно: шаг повторяется после первого


def test_draft_when_sending_off(monkeypatch, make_client):
    did = dialog(truck(), load_raw(), status="waiting")
    set_kv(send_enabled=False)

    async def neg(*a):
        return REPLY
    monkeypatch.setattr(main.agent, "negotiate", neg)
    asyncio.run(main.agent_step(main.q("select * from dialogs where id=?", did)[0]))
    d = make_client("logist").get("/api/dialogs").json()[0]
    assert d["draft"] == REPLY["reply"] and d["messages"] == []

    async def send(chat_id, text):
        return {"id": "m1", "ts": time.time()}
    monkeypatch.setattr(main.ati, "send", send)
    make_client("logist", "olga").post(f"/api/dialogs/{did}/send", json={"text": d["draft"]})
    assert main.q("select draft from dialogs where id=?", did)[0]["draft"] is None


def test_scan_does_not_move_load_in_work(monkeypatch):
    a, b = truck("A"), truck("B")
    raw = load_raw()
    dialog(a, raw, status="waiting")

    async def search(t, page):
        return ([raw], 1) if page == 1 else ([], 1)
    monkeypatch.setattr(main.ati, "search", search)
    monkeypatch.setattr(main.asyncio, "sleep", lambda s, _real=asyncio.sleep: _real(0))
    asyncio.run(main.scan_truck({**main.q("select * from trucks where id=?", b)[0], "pages": 1}))
    assert main.q("select truck_id from loads where id='L1'")[0]["truck_id"] == a


def test_bad_load_skipped_not_whole_scan(monkeypatch):
    t = truck()
    good = {**load_raw(), "id": "OK"}

    async def search(tr, page):
        return ([{"id": "BAD", "loading": None}, good], 2) if page == 1 else ([], 2)
    monkeypatch.setattr(main.ati, "search", search)
    monkeypatch.setattr(main.asyncio, "sleep", lambda s, _real=asyncio.sleep: _real(0))
    asyncio.run(main.scan_truck(main.q("select * from trucks where id=?", t)[0]))
    assert [r["id"] for r in main.q("select id from loads")] == ["OK"]


@pytest.mark.parametrize("body", [{"scan_minutes": "5"}, {"scan_minutes": 0}, {"pages": None}, {"threshold": 150},
                                  {"call_enabled": "false"}, {"call_after_min": True}, {"vat": -1}])
def test_settings_rejects_bad_values(make_client, body):
    r = make_client("owner").put("/api/settings", json=body)
    assert r.status_code == 400, body
    assert main.settings()["scan_minutes"] == main.DEFAULTS["scan_minutes"]


def test_settings_float_to_int(make_client):
    assert make_client("owner").put("/api/settings", json={"pages": 3.0}).json()["pages"] == 3


def test_stale_loads_hidden(make_client):
    t = truck()
    for lid, upd in (("NEW", time.time()), ("OLD", time.time() - 3 * 86400)):
        main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status,updated) values(?,?,?,?,?,?,?,?,?)",
                        (lid, t, json.dumps({**load_raw(), "id": lid}), 80, "relevant", "[]", "{}", "new", upd))
    c = make_client("logist")
    assert [x["id"] for x in c.get("/api/loads").json()] == ["NEW"]
    assert c.get("/api/stats").json()["relevant"] == 1


@pytest.mark.parametrize("user, me, mine", [("5464422", "5464422", True), ("5464422.7", "5464422", True),
                                            ("54644221", "5464422", False), ("123", "", False), (None, "5464422", False)])
def test_is_mine(monkeypatch, user, me, mine):
    monkeypatch.setattr(main, "ME", {"ati_id": me} if me else {})
    assert main.is_mine(user) is mine


def test_agent_run_explains(make_client):
    c = make_client("owner")
    set_kv(send_enabled=False)
    assert "черновики" in c.post("/api/agent/run").json()["reason"]
    set_kv(send_enabled=True, auto_start=False)
    assert "выключено" in c.post("/api/agent/run").json()["reason"]


def test_user_patch_all_or_nothing(make_client):
    c = make_client("owner")
    uid = c.post("/api/users", json={"login": "petr", "password": "password123", "role": "logist"}).json()["id"]
    assert c.patch(f"/api/users/{uid}", json={"role": "owner", "password": "short"}).status_code == 400
    assert main.q("select role from users where id=?", uid)[0]["role"] == "logist"  # роль не сменилась наполовину


def test_agent_off_by_default():
    s = main.settings()
    assert not s["send_enabled"] and not s["auto_start"] and not s["call_enabled"]


def test_manage_agent_switch(tmp_path):
    import subprocess
    import sys
    env = {"DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], capture_output=True, text=True, env=env)
    assert "✗ агент отправляет" in run("agent", "status").stdout
    out = run("agent", "on").stdout
    assert "✓ агент отправляет" in out and "✓ агент сам берёт" in out and "✗ агент звонит" in out
    assert "✓ агент звонит" in run("calls", "on").stdout
    out = run("agent", "off").stdout
    assert "✗ агент отправляет" in out and "✓ агент звонит" in out
    import sqlite3
    db = sqlite3.connect(tmp_path / "data.db")
    assert [r[0] for r in db.execute("select action from actions order by rowid")] == ["agent_on", "calls_on", "agent_off"]
