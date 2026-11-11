"""Статус машины вручную, без GPS (ТЗ 2.3): free / busy, где и когда освободится."""
from datetime import date, datetime, timedelta
from math import ceil

KM_PER_DAY = 500  # расчётный суточный пробег, если в грузе нет даты выгрузки
SCAN_AHEAD_DAYS = 2  # занятые машины начинаем сканировать за 2 дня до освобождения


def _d(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date() if s else None
    except ValueError:
        return None


def scan_filter(today=None):
    """SQL-условие и параметры для monitor(): активные свободные + занятые, что освобождаются скоро."""
    edge = ((today or date.today()) + timedelta(days=SCAN_AHEAD_DAYS)).isoformat()
    return "active=1 and (coalesce(status,'free')='free' or free_from<=?)", (edge,)


def busy_until(load, metrics, today=None):
    """Куда и когда освободится машина после груза: город выгрузки + дата выгрузки (или погрузка + дни пути)."""
    un, ld = load.get("unloading") or {}, load.get("loading") or {}
    loc = un.get("location") or {}
    when = _d(un.get("lastDate")) or _d(un.get("firstDate"))
    if not when:
        start = _d(ld.get("lastDate")) or _d(ld.get("firstDate")) or today or date.today()
        dist = (metrics or {}).get("dist") or (load.get("route") or {}).get("distance") or 0
        when = start + timedelta(days=max(1, ceil(dist / KM_PER_DAY)))
    return {"city_name": loc.get("city"), "city_id": loc.get("cityId") or loc.get("city_id"),
            "lat": loc.get("lat"), "lon": loc.get("lon"), "free_from": when.isoformat()}


def release_due(db, today=None):
    """Занятые машины, у которых наступил free_from, становятся свободными. Возвращает их id."""
    t = (today or date.today()).isoformat()
    ids = [r[0] for r in db.execute("select id from trucks where status='busy' and free_from<=?", (t,)).fetchall()]
    if ids:
        db.execute(f"update trucks set status='free' where id in ({','.join('?' * len(ids))})", ids)
    return ids
