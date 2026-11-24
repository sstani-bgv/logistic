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
    assert l["metrics"]["partial"] and l["metrics"]["money"] == "—"
    texts = " ".join(x["t"] for x in l["reasons"])
    assert "Без ставки" in texts and "точкой поиска" in texts and "машиной" not in texts

    def rate(**kw):
        return c.post("/api/search/rate", json={"load": l["raw"], "profile": page["profile"], "price": 90000, "money": 1, **kw})
    good, bad = rate().json(), rate(price=15000).json()
    assert not good["metrics"]["partial"] and good["metrics"]["gross"] == 90000 and good["metrics"]["per_km"] > 55
    assert good["score"] > l["score"] > bad["score"]
    assert rate(price=0).status_code == 422 and rate(money=9).status_code == 400 and rate(load={"id": "x"}).status_code == 400
    assert not main.q("select 1 from loads where id='pub'")
