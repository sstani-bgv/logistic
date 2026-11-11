import json
from datetime import date, timedelta

import pytest

import fleet
import main

TODAY = date.today()


def iso(days):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def clean():
    for t in ("trucks", "loads", "dialogs", "messages", "rules"):
        main.DB.execute(f"delete from {t}")
    main.LOG.clear()


@pytest.fixture
def client(make_client):
    return make_client("logist")


def truck(name="Volvo", status="free", free_from=None, active=1, city="Санкт-Петербург"):
    cur = main.DB.execute(
        "insert into trucks(name,driver,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,free_from,active,status)"
        " values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, "", city, 1, 59.94, 30.32, 150, 1, "тент", 2.7, 20, free_from or iso(0), active, status))
    return cur.lastrowid


def load_raw(unload_date=None, city_id=2, load_date=None, dist=700):
    un = {"location": {"city": "Москва", "lat": 55.75, "lon": 37.62, **({"cityId": city_id} if city_id else {})}}
    if unload_date:
        un["firstDate"] = unload_date
    return {"id": "L1", "loading": {"firstDate": load_date or iso(1),
                                    "location": {"city": "Санкт-Петербург", "lat": 59.94, "lon": 30.32}},
            "unloading": un, "route": {"distance": dist}, "load": {"weight": 10, "cargoType": "ТНП"},
            "truck": {"carTypesBitSum": 1}, "rate": {"price": 60000, "moneyType": 1}}


def dialog(tid, raw, metrics=None, status="ready"):
    main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status) values(?,?,?,?,?,?,?,?)",
                    (raw["id"], tid, json.dumps(raw), 80, "relevant", "[]", json.dumps(metrics or {"dist": 700}), "dialog"))
    return main.DB.execute("insert into dialogs(load_id,chat_id,goal,status) values(?,?,?,?)",
                           (raw["id"], "c1", "g", status)).lastrowid


def get_truck(tid):
    return main.q("select * from trucks where id=?", tid)[0]


# --- чистая логика ---

def test_busy_until_uses_unload_date():
    u = fleet.busy_until(load_raw(unload_date=iso(3) + "T00:00:00"), {"dist": 700})
    assert u == {"city_name": "Москва", "city_id": 2, "lat": 55.75, "lon": 37.62, "free_from": iso(3)}


@pytest.mark.parametrize("dist, days", [(700, 2), (300, 1), (0, 1), (1600, 4)])
def test_busy_until_estimates_from_loading(dist, days):
    u = fleet.busy_until(load_raw(load_date=iso(1), dist=dist), {"dist": dist})
    assert u["free_from"] == iso(1 + days)


def test_scan_filter():
    free = truck("free")
    soon = truck("soon", "busy", iso(2))
    far = truck("far", "busy", iso(5))
    truck("off", "free", active=0)
    where, args = fleet.scan_filter()
    assert {t["id"] for t in main.q(f"select * from trucks where {where}", *args)} == {free, soon}
    assert far not in {t["id"] for t in main.q(f"select * from trucks where {where}", *args)}


def test_release_due():
    due, later = truck("due", "busy", iso(0)), truck("later", "busy", iso(1))
    assert fleet.release_due(main.DB) == [due]
    assert get_truck(due)["status"] == "free" and get_truck(later)["status"] == "busy"


def test_migration_adds_status():
    main.DB.execute("alter table trucks drop column status")
    main.migrate()
    assert "status" in {r[1] for r in main.DB.execute("pragma table_info(trucks)")}


# --- API ---

def test_taken_makes_truck_busy_until_unload(client):
    tid = truck()
    did = dialog(tid, load_raw(unload_date=iso(3)))
    r = client.patch(f"/api/dialogs/{did}", json={"status": "taken"})
    assert r.status_code == 200
    t = get_truck(tid)
    assert (t["status"], t["city_name"], t["city_id"], t["free_from"]) == ("busy", "Москва", 2, iso(3))
    assert main.q("select status from loads where id='L1'")[0]["status"] == "taken"


def test_taken_looks_up_city_when_no_id(client, monkeypatch):
    async def fake_city(name):
        return {"CityName": name, "CityId": 77, "Latitude": 1.0, "Longitude": 2.0}
    monkeypatch.setattr(main, "city", fake_city)
    tid = truck()
    did = dialog(tid, load_raw(city_id=None, load_date=iso(1)))
    client.patch(f"/api/dialogs/{did}", json={"status": "taken"})
    t = get_truck(tid)
    assert (t["status"], t["city_id"], t["free_from"]) == ("busy", 77, iso(3))


def test_taken_keeps_city_when_lookup_fails(client, monkeypatch):
    async def broken(name):
        raise RuntimeError("АТИ недоступен")
    monkeypatch.setattr(main, "city", broken)
    tid = truck()
    did = dialog(tid, load_raw(city_id=None))
    client.patch(f"/api/dialogs/{did}", json={"status": "taken"})
    t = get_truck(tid)
    assert t["status"] == "busy" and t["city_name"] == "Санкт-Петербург" and t["city_id"] == 1
    assert any("Не нашёл город" in x["text"] for x in main.LOG)


def test_other_statuses_dont_touch_truck(client):
    tid = truck()
    did = dialog(tid, load_raw(unload_date=iso(3)))
    client.patch(f"/api/dialogs/{did}", json={"status": "declined"})
    assert get_truck(tid)["status"] == "free"


def test_bad_dialog_status(client):
    did = dialog(truck(), load_raw())
    assert client.patch(f"/api/dialogs/{did}", json={"status": "lol"}).status_code == 400
    assert client.patch("/api/dialogs/9999", json={"status": "taken"}).status_code == 404


def test_toggle_free_resets_future_date(client):
    tid = truck(status="busy", free_from=iso(5))
    r = client.patch(f"/api/trucks/{tid}", json={"status": "free"}).json()
    assert (r["status"], r["free_from"]) == ("free", iso(0))


def test_toggle_busy_needs_future_date(client):
    tid = truck(status="free", free_from=iso(-3))
    r = client.patch(f"/api/trucks/{tid}", json={"status": "busy"}).json()
    assert (r["status"], r["free_from"]) == ("busy", iso(1))
    assert client.get("/api/trucks").json()[0]["status"] == "busy"  # не освобождается тут же


def test_toggle_validation(client):
    assert client.patch(f"/api/trucks/{truck()}", json={"status": "x"}).status_code == 400
    assert client.patch("/api/trucks/9999", json={"status": "free"}).status_code == 404


def test_list_releases_due(client):
    tid = truck(status="busy", free_from=iso(0))
    assert client.get("/api/trucks").json()[0]["status"] == "free"
    assert get_truck(tid)["status"] == "free"


def test_form_future_date_means_busy(client):
    tid = truck()
    body = {"name": "Volvo", "city_name": "Санкт-Петербург", "free_from": iso(4), "status": "free"}
    assert client.put(f"/api/trucks/{tid}", json=body).status_code == 200
    assert (get_truck(tid)["status"], get_truck(tid)["free_from"]) == ("busy", iso(4))


# --- после ревью ---

def test_agent_step_respects_manual_taken(monkeypatch):
    import asyncio
    tid = truck()
    did = dialog(tid, load_raw(unload_date=iso(3)), status="waiting")

    async def slow_negotiate(*a):
        main.DB.execute("update dialogs set status='taken' where id=?", (did,))  # логист нажал «Берём», пока агент думал
        return {"reply": "Ещё вопрос", "status": "waiting", "summary": "s", "facts": {}}
    sent = []

    async def fake_send(*a):
        sent.append(a)
    monkeypatch.setattr(main.agent, "negotiate", slow_negotiate)
    monkeypatch.setattr(main.ati, "send", fake_send)
    asyncio.run(main._agent_step(main.q("select * from dialogs where id=?", did)[0]))
    d = main.q("select * from dialogs where id=?", did)[0]
    assert d["status"] == "taken" and d["summary"] == "s" and not sent


def test_auto_start_skips_busy_trucks(monkeypatch):
    import asyncio
    busy = truck("busy", "busy", iso(5))
    raw = {**load_raw(), "firm": {"id": 1, "name": "F", "contacts": [{"id": 1, "name": "N"}]}}
    main.DB.execute("insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,status) values(?,?,?,?,?,?,?,?)",
                    ("B1", busy, json.dumps(raw), 99, "relevant", "[]", "{}", "new"))
    started = []

    async def fake_start(l, goal, by_agent=False):
        started.append(l["id"])
    monkeypatch.setattr(main, "start_dialog", fake_start)
    asyncio.run(main.auto_start())
    assert started == []
    main.DB.execute("update trucks set status='free' where id=?", (busy,))
    asyncio.run(main.auto_start())
    assert started == ["B1"]


def test_taken_with_past_unload_date_stays_busy(client):
    tid = truck()
    did = dialog(tid, load_raw(unload_date=iso(-2)))
    client.patch(f"/api/dialogs/{did}", json={"status": "taken"})
    assert client.get("/api/trucks").json()[0]["status"] == "busy"
    assert get_truck(tid)["free_from"] == iso(1)


def test_truck_form_validation(client):
    assert client.put("/api/trucks/9999", json={"name": "x", "city_name": "y", "free_from": iso(0)}).status_code == 404
    tid = truck()
    assert client.put(f"/api/trucks/{tid}", json={"name": "x", "city_name": "Санкт-Петербург",
                                                  "free_from": "abc"}).status_code == 422


def test_release_due_handles_empty_date():
    tid = truck("broken", "busy", "")
    assert fleet.release_due(main.DB) == [tid]
