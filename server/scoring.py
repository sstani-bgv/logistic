"""Оценка груза под конкретную машину по правилам логиста + база знаний."""
from datetime import date, datetime
from math import radians, sin, cos, asin, sqrt

MOSCOW_CENTER = (55.7558, 37.6173)
ROAD_FACTOR = 1.25  # ponytail: прямая * коэффициент; точные км — платный API расстояний АТИ
MONEY = {1: "с НДС", 23: "на карту", 2: "без НДС", 3: "наличными", 0: "запрос"}
MAX_RADIUS = 999  # больше АТИ не принимает
SHORT_KM = 100

# Федеральные округа = глобальные гео-списки АТИ (loads.ati.su/webapi/public/firms/v1.0/lists/geo/global).
# list_id уходит в фильтр поиска (тип 5), подстроки регионов — для оценки. Составы округов — как у АТИ, не по закону:
# Бурятия и Забайкалье у АТИ в Сибирском, Севастополя и новых регионов в Южном нет.
# Порядок важен: УФО раньше СЗФО («Ямало-Ненецкий»), ПФО раньше СЗФО («Нижний Новгород»).
DISTRICTS = {
    "ЦФО": ("Центральный", "ee634ef8-e210-e311-b4ec-00259038ec34",
            "москв москов белгород брянск владимир воронеж иванов калуж костром курск липецк орлов рязан смолен "
            "тамбов твер тульск тула ярослав"),
    "УФО": ("Уральский", "54654ef8-e210-e311-b4ec-00259038ec34", "курган свердлов екатеринбург тюмен челябин ханты ямал"),
    "ПФО": ("Приволжский", "7c654ef8-e210-e311-b4ec-00259038ec34",
            "нижегород нижний башкор марий мордов татарстан удмурт чуваш пермск пермь кировск оренбург пензен самар "
            "саратов ульянов"),
    "СЗФО": ("Северо-Западный", "6f5e4ef8-e210-e311-b4ec-00259038ec34",
             "петербург ленинград карел коми архангел вологод калинингр мурман новгород псков"),
    "ЮФО": ("Южный", "79644ef8-e210-e311-b4ec-00259038ec34", "адыге калмык крым краснодар астрахан волгоград ростов"),
    "СКФО": ("Северо-Кавказский", "9f654ef8-e210-e311-b4ec-00259038ec34",
             "дагестан ингуш кабардин карачаев осети чечен ставрополь"),
    "СФО": ("Сибирский", "1f654ef8-e210-e311-b4ec-00259038ec34",
            "алтай тыва тува хакас краснояр иркут кемеров кузбасс новосибир омск томск бурят забайкал"),
    "ДФО": ("Дальневосточный", "e1644ef8-e210-e311-b4ec-00259038ec34",
            "саха якут камчат примор хабаров амур магадан сахалин еврей чукот"),
}


def district(loc):
    """Код федерального округа точки по региону; без региона — по городу (Москва, Петербург, Севастополь). None — не узнали."""
    loc = loc or {}
    name = str(loc.get("region") or loc.get("city") or "").lower()
    return next((code for code, (_, _id, stems) in DISTRICTS.items() if any(w in name for w in stems.split())), None)


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


def pay_terms(rate):
    """Условия оплаты из объявления — АТИ отдаёт их и без ставки."""
    out = []
    if rate.get("prepayEnabled") and rate.get("prepay"):
        out.append(f"предоплата {rate['prepay']}%")
    if rate.get("payOnUnload"):
        out.append("оплата на выгрузке")
    elif rate.get("payDaysEnabled") and rate.get("payDays"):
        out.append(f"отсрочка {rate['payDays']} дн.")
    return ", ".join(out)


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
        add(f"Погрузка рядом с {'машиной' if truck.get('name') else 'точкой поиска'} (≈{dead:.0f} км)", 10)
    elif straight <= truck["radius"]:
        add(f"{'Холостой пробег' if truck.get('name') else 'От точки поиска'} ≈{dead:.0f} км", -round(dead / 30))
    else:
        add(f"Погрузка далеко: {straight:.0f} км по прямой при радиусе {truck['radius']}", kind="block")

    # куда машине нужно: город (с радиусом) или федеральный округ. Не то направление — отсекаем, а не штрафуем:
    # логист сам выбрал, куда едет, и АТИ по городу так же режет выдачу.
    if truck.get("to_district"):
        got = district(un["location"])
        want = DISTRICTS.get(truck["to_district"], (truck["to_district"],))[0]
        if got == truck["to_district"]:
            add(f"Выгрузка в нужном округе ({want})", 5)
        elif got:
            add(f"Выгрузка не в {want} ФО ({DISTRICTS[got][0]})", kind="block")
        else:
            add(f"Не удалось понять, в {want} ФО ли выгрузка", -5)
    elif truck.get("to_lat") is not None and truck.get("to_lon") is not None:
        to_km, r = km(upos, (truck["to_lat"], truck["to_lon"])), truck.get("to_radius") or 150
        if to_km <= r:
            add(f"Выгрузка в нужном направлении: {truck.get('to_city_name') or 'город назначения'}", 5)
        else:
            add(f"Выгрузка в {to_km:.0f} км от {truck.get('to_city_name') or 'города назначения'} (радиус {r})", kind="block")

    # даты
    free = max(_d(truck["free_from"]) or date.today(), date.today())  # дата освобождения в прошлом = свободна сегодня
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
    if truck["body_mask"] and int(load["truck"].get("carTypesBitSum") or 0) & truck["body_mask"] == 0:
        add("Кузов не подходит", kind="block")

    # ставка
    rate = load["rate"]
    net = net_price(rate, settings["vat"])
    # ponytail: короче 100 км считаем как 100 — иначе рейс по городу за 15 000 ₽ даёт тысячи ₽/км и встаёт наверх
    per_km = net / max(dist + dead, SHORT_KM) if net else None
    if dist < SHORT_KM:
        add(f"Короткий рейс ≈{dist:.0f} км — ставка за км посчитана как за {SHORT_KM} км", -10)
    partial = bool(load.get("publicOnly")) and per_km is None  # ставку АТИ не отдала: балл не про выгоду
    asked = partial and rate.get("moneyType") == 0  # «запрос»: заказчик ставку не указал, скрывать нечего
    mt = MONEY.get(rate.get("moneyType"), "—")
    if per_km is None:
        add("Без ставки: оценены только маршрут, вес, кузов и даты" if partial and not asked else "Ставка «запрос» — агент уточнит", -8)
    else:
        # плавно, а не ступеньками: 45 ₽/км должны стоять выше 35, даже если обе выше нормы
        target = settings["min_rate_km"]
        ratio = per_km / target
        if ratio >= 1:
            top = 8 if dist < SHORT_KM else 35  # городской рейс за ₽/км не поднимаем — сравним только с межгородом
            add(f"Ставка {per_km:.0f} ₽/км чистыми ({mt})" + (" — выше нормы" if ratio >= 1.15 else ""), min(top, round(8 + 30 * (ratio - 1))))
        else:
            add(f"Ставка {per_km:.0f} ₽/км — {'чуть ' if ratio >= 0.85 else ''}ниже нормы {target}", max(-35, round(-8 - 60 * (1 - ratio))))

    # выгрузка в центре Москвы
    if km(upos, MOSCOW_CENTER) < 7:
        add("Выгрузка в центре Москвы — сложный заезд", -10)

    # заказчик
    if not firm:
        add("Заказчик скрыт", -5)
    else:
        if (firm.get("userLists") or {}).get("black"):
            add("Фирма в вашем чёрном списке", kind="block")
        if firm.get("claims") and not any(r["active"] and "claims" in (r["match"], r.get("match2")) for r in rules):
            add(f"Претензии к фирме: {firm['claims']}", -15)  # своё правило о претензиях в базе знаний — заменяет это
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
               "per_km": round(per_km, 1) if per_km else None, "money": mt, "partial": partial, "asked": asked, "pay": pay_terms(rate)}
    return s, verdict, reasons, metrics


def rule_hits(rule, load):
    """Правила из базы знаний: одно-два условия (И), которые модель извлекла из текста. «СПб → Москва» — два условия."""
    m2 = rule.get("match2")
    return cond_hits(rule["match"], rule["value"], load) and (not m2 or m2 == "none" or cond_hits(m2, rule.get("value2"), load))


def cond_hits(m, v, load):
    v = (v or "").lower()
    firm = load.get("firm") or {}
    if m == "claims":  # value — с какого числа претензий срабатывает
        return v.isdigit() and int(firm.get("claims") or 0) >= max(1, int(v))
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
