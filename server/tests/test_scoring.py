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
