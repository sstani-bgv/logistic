"""Груз вручную: ставка и телефон от логиста → оценка и звонок, без чата и автосбора АТИ."""
import asyncio
import json

import pytest

import ati
import main
from test_fleet import clean, iso, truck  # noqa: F401

CITIES = {"Санкт-Петербург": (1, 59.94, 30.32), "Москва": (2, 55.75, 37.62)}


@pytest.fixture
def client(make_client, monkeypatch):
    async def city(name):
        i, lat, lon = CITIES[name]
        return {"CityId": i, "Latitude": lat, "Longitude": lon}
    monkeypatch.setattr(main, "city", city)

    async def no_chat(*a):
        raise AssertionError("чат АТИ для ручного груза не открываем")
    monkeypatch.setattr(ati, "open_chat", no_chat)
    main.DB.execute("delete from calls")
    main.DB.execute("delete from kv where k in ('scan_auto','min_rate_km','threshold')")
    return make_client("logist")


def body(**kw):
    return {"truck_id": kw.pop("tid"), "from_city": "Санкт-Петербург", "to_city": "Москва", "load_date": iso(1), "weight": 10,
            "price": 90000, "money": 1, "distance": 700, "firm": "ООО Ромашка", "contact": "Ольга", "phone": "8 921 555-12-34", **kw}


def test_rate_changes_score_and_agent_calls_only(client):
    tid = truck()
    good = client.post("/api/loads/manual", json=body(tid=tid)).json()
    bad = client.post("/api/loads/manual", json=body(tid=tid, price=20000)).json()
    unknown = client.post("/api/loads/manual", json=body(tid=tid, price=0)).json()
    assert good["verdict"] == "relevant" and good["metrics"]["per_km"] > 55
    assert good["score"] > unknown["score"] > bad["score"]
    assert good["id"] in [x["id"] for x in client.get("/api/loads?view=relevant").json()]
    d = client.post(f"/api/loads/{good['id']}/agent", json={"goal": "узнать адрес"}).json()
    assert d["chat_id"] is None and d["status"] == "waiting"
    instr = main.instructions_for_call(main.q("select * from dialogs where id=?", d["id"])[0])
    assert "90000" in instr and "ООО Ромашка" in instr
    assert main.calls.phone_of(json.loads(main.q("select raw from loads where id=?", good["id"])[0]["raw"])) == "+79215551234"


def test_agent_never_starts_manual_load_itself(client):
    client.post("/api/loads/manual", json=body(tid=truck()))
    for k, v in (("auto_start", "true"), ("send_enabled", "true"), ("auto_score", "0")):
        main.DB.execute("insert or replace into kv values(?,?)", (k, v))
    asyncio.run(main.auto_start())
    for k in ("auto_start", "send_enabled", "auto_score"):
        main.DB.execute("delete from kv where k=?", (k,))
    assert not main.q("select * from dialogs")


def test_manual_load_validation(client):
    assert client.post("/api/loads/manual", json=body(tid=999)).status_code == 400
    tid = truck()
    assert client.post("/api/loads/manual", json=body(tid=tid, phone="123")).status_code == 400
    assert client.post("/api/loads/manual", json=body(tid=tid, money=7)).status_code == 400
    l = client.post("/api/loads/manual", json=body(tid=tid, phone="")).json()
    assert client.post(f"/api/loads/{l['id']}/agent", json={}).status_code == 400  # телефона нет — звонить некуда
    assert not main.q("select * from dialogs")


@pytest.mark.parametrize("auto, want", [(True, 1), (False, 0)])
def test_auto_scan_switch(client, monkeypatch, auto, want):
    truck()
    main.DB.execute("insert or replace into kv values('scan_auto',?)", (json.dumps(auto),))
    seen = []

    async def scan(t):
        seen.append(t["id"])

    async def stop(*a):
        raise asyncio.CancelledError
    monkeypatch.setattr(main, "scan_truck", scan)
    monkeypatch.setattr(main.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.monitor())
    main.DB.execute("delete from kv where k='scan_auto'")
    assert len(seen) == want
