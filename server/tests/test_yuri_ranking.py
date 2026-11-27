"""Претензии Юрия 26.11: дешёвый груз выше дорогого, правило «СПб → Москва» на Петрозаводск, «1 претензия — не негатив»."""
from datetime import date

from scoring import score

TRUCK = {"lat": 59.94, "lon": 30.32, "radius": 150, "free_from": date.today().isoformat(), "height": 2.7, "capacity": 20, "body_mask": 1}
ST = {"vat": 22, "min_rate_km": 30, "threshold": 40}


def load(city="Москва", lat=55.75, lon=37.62, price=90000, claims=0, dist=700):
    return {"loading": {"location": {"lat": 59.93, "lon": 30.36, "city": "Санкт-Петербург"}, "firstDate": date.today().isoformat()},
            "unloading": {"location": {"lat": lat, "lon": lon, "city": city}}, "load": {"weight": 10, "height": 0, "cargoType": "ТНП"},
            "truck": {"carTypesBitSum": "1"}, "route": {"distance": dist}, "rate": {"price": price, "moneyType": 2},
            "firm": {"id": 1, "claims": claims}}


def rule(**kw):
    return {"active": 1, "block": 0, "delta": -30, "text": "t", "match2": "none", "value2": "", **kw}


def test_higher_rate_ranks_higher_even_above_norm():
    assert score(load(price=31500), TRUCK, [], ST)[0] > score(load(price=24500), TRUCK, [], ST)[0]


def test_direction_rule_needs_both_cities():
    spb_msk = rule(match="load_city", value="петербург", match2="unload_city", value2="москв")
    msk, ptz = load(), load("Петрозаводск", 61.79, 34.36, dist=442)
    assert score(msk, TRUCK, [spb_msk], ST)[0] == score(msk, TRUCK, [], ST)[0] - 30
    assert score(ptz, TRUCK, [spb_msk], ST)[0] == score(ptz, TRUCK, [], ST)[0]


def test_claims_rule_replaces_builtin_penalty():
    two_plus = rule(match="claims", value="2", delta=-15)
    one, two = load(claims=1), load(claims=2)
    assert score(one, TRUCK, [two_plus], ST)[0] == score(load(), TRUCK, [two_plus], ST)[0]
    assert score(two, TRUCK, [two_plus], ST)[0] == score(load(), TRUCK, [], ST)[0] - 15
    assert score(one, TRUCK, [], ST)[0] < score(load(), TRUCK, [], ST)[0]  # без правила — как раньше


def test_city_run_does_not_top_the_list():
    city = load("Санкт-Петербург", 59.95, 30.30, price=15000, dist=3)
    s, _, reasons, m = score(city, TRUCK, [], ST)
    assert m["per_km"] < 200 and s < score(load(price=40000), TRUCK, [], ST)[0]
    assert any("Короткий рейс" in r["t"] for r in reasons)
