import main
import scoring

TRUCK = {"lat": 58.521, "lon": 31.2758, "radius": 200, "height": 2.7, "capacity": 20, "body_mask": 1,
         "free_from": "2026-01-01"}  # Великий Новгород


def load(lat, lon, firm=None):
    l = {"loading": {"location": {"lat": lat, "lon": lon, "city": "X"}, "firstDate": "2099-01-01"},
         "unloading": {"location": {"lat": 55.75, "lon": 37.62, "city": "Москва"}},
         "load": {"weight": 10}, "truck": {"carTypesBitSum": 1}, "route": {"distance": 700},
         "rate": {"price": 80000, "moneyType": 1}}
    if firm is not None:
        l["firm"] = firm
    return l


def blocked(res):
    return [r["t"] for r in res[2] if r["kind"] == "block"]


def test_radius_is_straight_line_like_ati():
    spb = scoring.score(load(59.9387, 30.3162), TRUCK, [], dict(main.DEFAULTS))  # ≈160 км по прямой, ≈200+ по дороге
    assert not blocked(spb) and spb[3]["dead"] > 190  # не отсекаем, но пробег по дороге учитываем
    far = scoring.score(load(61.0, 30.0), TRUCK, [], dict(main.DEFAULTS))  # ≈290 км по прямой
    assert any("далеко" in b for b in blocked(far))


def test_hidden_firm_does_not_crash():
    for firm in (None, {}, {"contacts": []}):
        s = scoring.score(load(58.6, 31.3, firm), TRUCK, [], dict(main.DEFAULTS))
        assert 0 <= s[0] <= 100


def test_free_date_in_past_means_free_today():
    from datetime import date, timedelta
    l = load(58.6, 31.3)
    l["loading"]["firstDate"] = (date.today() + timedelta(days=1)).isoformat()
    old_truck = {**TRUCK, "free_from": (date.today() - timedelta(days=9)).isoformat()}
    res = scoring.score(l, old_truck, [], dict(main.DEFAULTS))
    assert not any("позже чем через 3 дня" in r["t"] for r in res[2])  # погрузка завтра, машина свободна сейчас


def test_destination_city_and_district_cut_other_directions():
    """Машине нужно в Москву (или в ЦФО): выгрузка в другом месте — отсекаем, в нужном — небольшой плюс."""
    from scoring import district, score
    from test_fleet import load_raw
    st = {"vat": 22, "min_rate_km": 55, "threshold": 0}
    truck = {"lat": 59.94, "lon": 30.32, "radius": 150, "body_mask": 1, "capacity": 20, "height": 2.7,
             "free_from": "2000-01-01", "rate": None}
    load = {**load_raw(), "rate": {"price": 90000, "moneyType": 23}}  # выгрузка в Москве
    base = score(load, truck, [], st)[0]
    to_msk = score(load, {**truck, "to_lat": 55.75, "to_lon": 37.62, "to_radius": 100, "to_city_name": "Москва"}, [], st)
    to_spb = score(load, {**truck, "to_lat": 59.94, "to_lon": 30.32, "to_radius": 100, "to_city_name": "СПб"}, [], st)
    assert to_msk[1] == "relevant" and to_msk[0] > base and to_spb[1] == "filtered"
    assert score(load, {**truck, "to_district": "ЦФО"}, [], st)[1] == "relevant"
    assert score(load, {**truck, "to_district": "СЗФО"}, [], st)[1] == "filtered"
    assert district({"region": "Московская обл."}) == "ЦФО" and district({"region": "Ямало-Ненецкий АО"}) == "УФО"
    assert district({"region": "Нижегородская обл."}) == "ПФО" and district({"city": "Санкт-Петербург"}) == "СЗФО"
    assert district({"region": "Минская обл."}) is None
    assert district({"region": "Республика Бурятия"}) == "СФО"  # как в гео-списках АТИ
