"""Статус машины вручную, без GPS (ТЗ 2.3): free / busy, где и когда освободится."""
from datetime import date, datetime, timedelta
from math import ceil
import re

KM_PER_DAY = 500  # расчётный суточный пробег, если в грузе нет даты выгрузки
SCAN_AHEAD_DAYS = 2  # занятые машины начинаем сканировать за 2 дня до освобождения


def _d(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date() if s else None
    except ValueError:
        return None


def scan_filter(today=None, alias=""):
    """SQL-условие и параметры для monitor()/auto_start(): активные свободные + занятые, что освобождаются скоро."""
    edge = ((today or date.today()) + timedelta(days=SCAN_AHEAD_DAYS)).isoformat()
    a = f"{alias}." if alias else ""
    return f"{a}active=1 and (coalesce({a}status,'free')='free' or coalesce({a}free_from,'')<=?)", (edge,)


def geo_city_id(loc):
    """Код города АТИ из geoCode выдачи: «2_80» = город (тип 2) с кодом 80 (Казань). Есть в каждом реальном грузе."""
    m = re.fullmatch(r"2_(\d+)", str((loc or {}).get("geoCode") or ""))
    return int(m.group(1)) if m else None


def busy_until(load, metrics, today=None):
    """Куда и когда освободится машина после груза: город выгрузки + дата выгрузки (или погрузка + дни пути)."""
    un, ld = load.get("unloading") or {}, load.get("loading") or {}
    loc = un.get("location") or {}
    when = _d(un.get("lastDate")) or _d(un.get("firstDate"))
    if not when:
        start = _d(ld.get("lastDate")) or _d(ld.get("firstDate")) or today or date.today()
        dist = (metrics or {}).get("dist") or (load.get("route") or {}).get("distance") or 0
        when = start + timedelta(days=max(1, ceil(dist / KM_PER_DAY)))
    return {"city_name": loc.get("city"), "city_id": loc.get("cityId") or loc.get("city_id") or geo_city_id(loc),
            "lat": loc.get("lat"), "lon": loc.get("lon"), "free_from": when.isoformat()}


def nearest_large_city(load):
    """Ближайший крупный город к месту выгрузки из подсказки АТИ (unloading.largeCities) — у него есть код города."""
    cs = [c for c in ((load.get("unloading") or {}).get("largeCities") or []) if isinstance(c, dict) and c.get("cityId")]
    return min(cs, key=lambda c: c.get("distance") or 0) if cs else None


def release_due(db, today=None):
    """Занятые машины, у которых наступил free_from, становятся свободными. Возвращает их id."""
    t = (today or date.today()).isoformat()
    ids = [r[0] for r in db.execute("select id from trucks where status='busy' and (free_from<=? or free_from is null"
                                    " or free_from='')", (t,)).fetchall()]
    if ids:
        db.execute(f"update trucks set status='free' where id in ({','.join('?' * len(ids))})", ids)
    return ids
