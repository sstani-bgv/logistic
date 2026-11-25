import pytest

import main
from test_fleet import load_raw


@pytest.fixture(autouse=True)
def clean_search_data():
    for table in ("trucks", "loads", "rules", "kv"):
        main.DB.execute(f"delete from {table}")


def test_general_search_without_fleet_uses_filters_and_returns_real_shape(monkeypatch, make_client):
    async def city(name):
        return {"CityId": 1 if name == "СПб" else 3611, "Latitude": 59.94, "Longitude": 30.32}

    async def search(profile, page):
        assert profile["city_id"] == 1 and profile["to_city_id"] == 3611
        assert profile["body_mask"] == 0 and profile["capacity"] == 12
        assert page == 2
        return [{**load_raw(), "publicOnly": True}], 123

    monkeypatch.setattr(main, "city", city)
    monkeypatch.setattr(main.ati, "search", search)
    client = make_client("logist")
    response = client.post("/api/search", json={"city_name": "СПб", "to_city_name": "Москва",
                                               "body_mask": 0, "capacity": 12, "page": 2})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 123 and result["public_only"] is True
    assert result["loads"][0]["id"] == "L1"
    assert main.q("select count(*) n from trucks")[0]["n"] == 0


def test_saved_search_profile_requires_owner_and_survives_read(make_client):
    owner = make_client("owner")
    profile = {"city_name": "Казань", "body_mask": 4, "capacity": 10, "default_mode": "trucks"}
    assert make_client("logist").put("/api/search/profile", json=profile).status_code == 403
    assert owner.put("/api/search/profile", json=profile).status_code == 200
    result = owner.get("/api/search/profile").json()
    assert result["city_name"] == "Казань" and result["default_mode"] == "trucks"


def test_search_cache_avoids_requests_and_survives_failed_refresh(monkeypatch, make_client):
    import httpx
    requests = []

    async def city(name):
        return {"CityId": 1, "Latitude": 59.94, "Longitude": 30.32}

    async def search(profile, page):
        requests.append(page)
        if len(requests) > 1:
            raise httpx.ConnectError("provider unavailable")
        return [load_raw()], 1

    monkeypatch.setattr(main, "city", city)
    monkeypatch.setattr(main.ati, "search", search)
    client = make_client("logist")
    assert client.get("/api/search/cache").json() is None
    original = client.post("/api/search", json={"refresh": True}).json()
    assert original["updated_at"]
    assert client.post("/api/search", json={}).json() == original
    assert requests == [1]
    assert client.post("/api/search", json={"refresh": True}).status_code == 502
    assert client.get("/api/search/cache").json() == original


def test_proxy_settings_are_owner_only_and_password_is_hidden(make_client, monkeypatch):
    monkeypatch.setattr(main.ati, "search_proxy", None)
    client = make_client("owner")
    config = {"enabled": True, "url": "http://proxy.example:8080", "username": "operator", "password": "secret"}
    assert make_client("logist").put("/api/search/proxy", json=config).status_code == 403
    assert client.put("/api/search/proxy", json=config).status_code == 200
    saved = client.get("/api/search/proxy").json()
    assert saved["password"] == "" and saved["password_set"]
    assert client.put("/api/search/proxy", json={**config, "password": ""}).status_code == 200
    assert main.kv_get("search_proxy")["password"] == "secret"
    assert client.put("/api/search/proxy", json={**config, "url": "file:///tmp/proxy"}).status_code == 400


def test_ati_browser_account_is_owner_only_hides_password_and_connects(make_client, monkeypatch):
    async def close():
        return None

    async def login(config, code=""):
        assert config["enabled"] and config["username"] == "dispatcher"
        assert config["password"] == "secret"
        assert code in ("", "1234")
        return {"connected": not code, "state": "connected" if not code else "verification_required"}

    monkeypatch.setattr(main.ati.browser_session, "close", close)
    monkeypatch.setattr(main.ati.browser_session, "login", login)
    config = {"enabled": True, "username": "dispatcher", "password": "secret"}
    assert make_client("logist").put("/api/search/ati", json=config).status_code == 403
    client = make_client("owner")
    assert client.put("/api/search/ati", json=config).status_code == 200
    saved = client.get("/api/search/ati").json()
    assert saved["password"] == "" and saved["password_set"]
    assert client.post("/api/search/ati/connect").json()["connected"]
    assert client.post("/api/search/ati/verify", json={"code": "1234"}).status_code == 200
    assert client.post("/api/search/ati/verify", json={"code": "12"}).status_code == 422


def test_scheduled_refresh_obeys_interval_and_disabled_state(monkeypatch):
    import asyncio
    calls = []

    async def city(name):
        return {"CityId": 1, "Latitude": 59.94, "Longitude": 30.32}

    async def search(profile, page):
        calls.append(page)
        return [load_raw()], 1

    monkeypatch.setattr(main, "city", city)
    monkeypatch.setattr(main.ati, "search", search)
    asyncio.run(main.refresh_general_if_due())
    assert calls == []
    main.kv_set("search_profile", main.SearchProfile(auto_refresh=True).model_dump_json())
    asyncio.run(main.refresh_general_if_due())
    asyncio.run(main.refresh_general_if_due())
    assert calls == [1]
    main.kv_set("search_last_attempt", main.time.time() - 4 * 3600 - 1)
    asyncio.run(main.refresh_general_if_due())
    assert calls == [1, 1]


def test_cache_keeps_last_hundred_searches(monkeypatch, make_client):
    async def fetch(query):
        return {"loads": [], "total": 0, "page": query.page, "profile": query.model_dump(),
                "public_only": False}

    monkeypatch.setattr(main, "fetch_general_search", fetch)
    client = make_client("logist")
    for radius in range(100, 202):
        assert client.post("/api/search", json={"radius": radius, "refresh": True}).status_code == 200
    keys = main.kv_get("search_cache_keys")
    assert len(keys) == 100
    assert len(main.q("select k from kv where k like 'search_cache:%'")) == 100
    latest = client.get("/api/search/cache").json()
    assert latest["profile"]["radius"] == 201


def test_general_search_exposes_provider_failure(monkeypatch, make_client):
    import httpx

    async def city(name):
        return {"CityId": 1, "Latitude": 59.94, "Longitude": 30.32}

    async def search(profile, page):
        response = httpx.Response(403, request=httpx.Request("POST", "https://loads.ati.su/search"))
        response.raise_for_status()

    monkeypatch.setattr(main, "city", city)
    monkeypatch.setattr(main.ati, "search", search)
    response = make_client("logist").post("/api/search", json={})
    assert response.status_code == 502 and "403" in response.json()["detail"]
    assert main.health()["ati_search_ok"] is False


def test_hidden_rate_is_partial_and_rescored_with_own_rate(monkeypatch, make_client):
    """Публичная выдача: балла про выгоду нет и форма оплаты не показана; логист вписал ставку — полный пересчёт, без записи в базу."""
    from test_fleet import load_raw

    async def city(name):
        return {"CityId": 1, "Latitude": 59.94, "Longitude": 30.32}

    async def search(profile, page):
        return [{**load_raw(), "id": "pub", "rate": {"moneyType": 1}, "publicOnly": True}], 1
    monkeypatch.setattr(main, "city", city)
    monkeypatch.setattr(main.ati, "search", search)
    c = make_client("logist")
    page = c.post("/api/search", json={}).json()
    l = page["loads"][0]
    assert l["metrics"]["partial"] and l["metrics"]["money"] == "с НДС" and not l["metrics"]["asked"]
    texts = " ".join(x["t"] for x in l["reasons"])
    assert "Без ставки" in texts and "точкой поиска" in texts and "машиной" not in texts

    def rate(**kw):
        return c.post("/api/search/rate", json={"load": l["raw"], "profile": page["profile"], "price": 90000, "money": 1, **kw})
    good, bad = rate().json(), rate(price=15000).json()
    assert not good["metrics"]["partial"] and good["metrics"]["gross"] == 90000 and good["metrics"]["per_km"] > 55
    assert good["score"] > l["score"] > bad["score"]
    assert rate(price=0).status_code == 422 and rate(money=9).status_code == 400 and rate(load={"id": "x"}).status_code == 400
    assert not main.q("select 1 from loads where id='pub'")


def test_public_pay_terms_and_request():
    from scoring import pay_terms, score
    from test_fleet import iso, load_raw
    p = {"lat": 59.94, "lon": 30.32, "radius": 150, "body_mask": 1, "capacity": 20, "height": 2.7, "free_from": iso(0)}
    st = {"vat": 22, "min_rate_km": 55, "threshold": 55}
    def m(**rate):
        _, _, why, x = score({**load_raw(), "rate": rate, "publicOnly": True}, p, [], st)
        return x, " ".join(r["t"] for r in why)
    x, why = m(moneyType=0)
    assert x["asked"] and x["money"] == "запрос" and "запрос" in why and "скрыта" not in why
    x, _ = m(moneyType=23, prepayEnabled=True, prepay=50, payDaysEnabled=True, payDays=5)
    assert x["money"] == "на карту" and x["pay"] == "предоплата 50%, отсрочка 5 дн."
    assert pay_terms({"payOnUnload": True, "payDaysEnabled": True, "payDays": 5}) == "оплата на выгрузке"
    assert pay_terms({"prepay": 50, "payDays": 5}) == ""  # галочки сняты — условий нет



def test_take_general_load_under_truck(make_client):
    """Груз из общего поиска берётся под машину из сохранённой выдачи, а не из того, что прислал браузер."""
    import json
    from test_fleet import truck
    tid = truck()
    raw = {**load_raw(), "id": "take-1", "firm": {"id": "1", "name": "Фирма", "contacts": [{"id": 1, "name": "Анна"}]}}
    main.kv_set("search_cache:test", json.dumps({"loads": [{"id": "take-1", "raw": raw}]}))
    main.kv_set("search_cache_keys", ["search_cache:test"])
    client = make_client("logist")
    r = client.post("/api/loads/take", json={"load_id": "take-1", "truck_id": tid})
    assert r.status_code == 200, r.text
    assert r.json()["truck_id"] == tid and r.json()["raw"]["firm"]["name"] == "Фирма"
    assert main.q("select truck_id from loads where id='take-1'")[0]["truck_id"] == tid
    assert client.post("/api/loads/take", json={"load_id": "nope", "truck_id": tid}).status_code == 404
