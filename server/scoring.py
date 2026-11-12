"""Оценка груза под конкретную машину по правилам логиста + база знаний."""
from datetime import date, datetime
from math import radians, sin, cos, asin, sqrt

MOSCOW_CENTER = (55.7558, 37.6173)
ROAD_FACTOR = 1.25  # ponytail: прямая * коэффициент; точные км — платный API расстояний АТИ
MONEY = {1: "с НДС", 23: "на карту", 2: "без НДС", 3: "наличными", 0: "запрос"}


def km(a, b):
    lat1, lon1, lat2, lon2 = map(radians, (*a, *b))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def _d(s):
    return datetime.fromisoformat(s[:10]).date() if s else None


def net_price(rate, vat):
    """Сколько реально остаётся: «60 на карту лучше, чем 60 с НДС»."""
    if rate.get("priceNoNds"):  # для «с НДС» АТИ сам отдаёт сумму без НДС
        return rate["priceNoNds"]
    p = rate.get("price") or rate.get("priceNds") or 0
    if not p:
        return None
    return p / (1 + vat / 100) if rate.get("moneyType") == 1 else p


def gross_price(rate):
    return rate.get("price") or rate.get("priceNds") or None


def score(load, truck, rules, settings):
    """-> (score 0..100, verdict 'relevant'|'filtered', reasons[{t, d, kind}], metrics)"""
    reasons, s, blocked = [], 60, False

    def add(text, delta=0, kind=None):
        nonlocal s, blocked
        kind = kind or ("plus" if delta > 0 else "minus" if delta < 0 else "info")
        if kind == "block":
            blocked = True
        s += delta
        reasons.append({"t": text, "d": delta, "kind": kind})

    ld, un, cargo, firm = load["loading"], load["unloading"], load["load"], load.get("firm") or {}
    lpos = (ld["location"]["lat"], ld["location"]["lon"])
    upos = (un["location"]["lat"], un["location"]["lon"])
    dist = load["route"].get("distance") or km(lpos, upos) * ROAD_FACTOR

    # холостой пробег до погрузки
    # радиус — по прямой, как в поиске АТИ (иначе отсекаем то, что биржа честно нашла в радиусе); дорога ≈ прямая * 1.25
    straight = km((truck["lat"], truck["lon"]), lpos)
    dead = straight * ROAD_FACTOR
    if dead <= 30:
        add(f"Погрузка рядом с машиной (≈{dead:.0f} км)", 10)
    elif straight <= truck["radius"]:
        add(f"Холостой пробег ≈{dead:.0f} км", -round(dead / 30))
    else:
        add(f"Погрузка далеко: {straight:.0f} км по прямой при радиусе {truck['radius']}", kind="block")

    # даты
    free = _d(truck["free_from"]) or date.today()
    last = _d(ld.get("lastDate")) or _d(ld.get("firstDate"))
    if last and last < free:
        add(f"Погрузка до {last:%d.%m}, машина свободна с {free:%d.%m}", kind="block")
    elif _d(ld.get("firstDate")) and (_d(ld["firstDate"]) - free).days > 3:
        add("Погрузка позже чем через 3 дня после освобождения", -8)

    # габариты
    h = cargo.get("height") or 0
    if h and h > truck["height"]:
        add(f"Высота груза {h} м > кузова {truck['height']} м", kind="block")
    if (cargo.get("weight") or 0) > truck["capacity"]:
        add(f"Вес {cargo['weight']} т > {truck['capacity']} т", kind="block")
    if int(load["truck"].get("carTypesBitSum") or 0) & truck["body_mask"] == 0:
        add("Кузов не подходит", kind="block")

    # ставка
    rate = load["rate"]
    net = net_price(rate, settings["vat"])
    per_km = net / (dist + dead) if net else None
    mt = MONEY.get(rate.get("moneyType"), "—")
    if per_km is None:
        add("Ставка «запрос» — агент уточнит", -8)
    else:
        target = settings["min_rate_km"]
        if per_km >= target * 1.15:
            add(f"Ставка {per_km:.0f} ₽/км чистыми ({mt}) — выше нормы", 18)
        elif per_km >= target:
            add(f"Ставка {per_km:.0f} ₽/км чистыми ({mt})", 8)
        elif per_km >= target * 0.85:
            add(f"Ставка {per_km:.0f} ₽/км — чуть ниже нормы {target}", -10)
        else:
            add(f"Ставка {per_km:.0f} ₽/км — ниже нормы {target}", -30)

    # выгрузка в центре Москвы
    if km(upos, MOSCOW_CENTER) < 7:
        add("Выгрузка в центре Москвы — сложный заезд", -10)

    # заказчик
    if not firm:
        add("Заказчик скрыт", -5)
    else:
        if (firm.get("userLists") or {}).get("black"):
            add("Фирма в вашем чёрном списке", kind="block")
        if firm.get("claims"):
            add(f"Претензии к фирме: {firm['claims']}", -15)
        if firm.get("isConfirmedShipper"):
            add("Подтверждённый грузовладелец", 6)
        elif (firm.get("profile") or "").startswith("эксп"):
            add("Экспедитор (посредник)", -3)
        if (firm.get("rating") or {}).get("score", 0) >= 4:
            add(f"Рейтинг АТИ {firm['rating']['score']}★", 4)
    if (load.get("similarCount") or 0) >= 15:
        add(f"Массовая рассылка: ещё {load['similarCount']} похожих", -12)

    # база знаний
    for r in rules:
        if r["active"] and rule_hits(r, load):
            add(f"База знаний: {r['text']}", r.get("delta") or 0, "block" if r["block"] else None)

    s = max(0, min(100, round(s)))
    verdict = "filtered" if blocked or s < settings["threshold"] else "relevant"
    metrics = {"dist": round(dist), "dead": round(dead), "net": round(net) if net else None, "gross": gross_price(rate),
               "per_km": round(per_km, 1) if per_km else None, "money": mt}
    return s, verdict, reasons, metrics


def rule_hits(rule, load):
    """Правила из базы знаний: простые структурные условия, которые Codex извлёк из текста."""
    m, v = rule["match"], (rule["value"] or "").lower()
    firm = load.get("firm") or {}
    field = {
        "firm_id": str(firm.get("id", "")),
        "firm_name": (firm.get("name") or "").lower(),
        "cargo": (load["load"].get("cargoType") or "").lower(),
        "load_city": (load["loading"]["location"].get("city") or "").lower(),
        "unload_city": (load["unloading"]["location"].get("city") or "").lower(),
        "note": (load.get("note") or "").lower(),
        "profile": (firm.get("profile") or "").lower(),
    }.get(m)
    if field is None:
        return False
    return field == v if m == "firm_id" else bool(v) and v in field


if __name__ == "__main__":
    truck = {"lat": 59.94, "lon": 30.32, "radius": 300, "free_from": date.today().isoformat(),
             "height": 2.7, "capacity": 20, "body_mask": 1}
    base = {"loading": {"location": {"lat": 59.93, "lon": 30.36, "city": "Санкт-Петербург"},
                        "firstDate": date.today().isoformat()},
            "unloading": {"location": {"lat": 55.48, "lon": 37.46, "city": "Андреевское"}},
            "load": {"weight": 20, "height": 0, "cargoType": "Кирпич"},
            "truck": {"carTypesBitSum": "129"}, "route": {"distance": 700},
            "firm": {"id": 7, "isConfirmedShipper": True, "rating": {"score": 5}}}
    st = {"vat": 22, "min_rate_km": 55, "threshold": 55}
    card = {**base, "rate": {"price": 48000, "moneyType": 23}}
    nds = {**base, "rate": {"price": 48000, "moneyType": 1}}
    assert score(card, truck, [], st)[0] > score(nds, truck, [], st)[0], "карта должна быть лучше НДС"
    tall = {**card, "load": {**card["load"], "height": 2.9}}
    assert score(tall, truck, [], st)[1] == "filtered"
    rule = {"active": 1, "match": "cargo", "value": "кирпич", "block": 1, "text": "кирпич не возим", "delta": 0}
    assert score(card, truck, [rule], st)[1] == "filtered"
    print("ok", score(card, truck, [], st)[:2], score(nds, truck, [], st)[:2])
