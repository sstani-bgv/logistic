"""Звонки агента (ТЗ 2.2): когда звонить, лимиты, стоп-лист, обработка итогов.

Сам звонок делает адаптер телефонии (telephony.py): mock — локально, megafon — ВАТС МегаФон + голосовой бот.
Здесь только правила и данные, без сети — всё тестируется локально.
"""
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
NUMBER_GAP = 2 * 3600  # одному номеру — не чаще раза в 2 часа
PER_LOAD = 2  # не больше 2 попыток на груз
DEFAULTS = {"call_enabled": False, "call_after_min": 15, "call_hours": "09:00-19:00", "call_daily": 20}
ACTIVE = ("queued", "ringing", "talking")  # звонок ещё идёт
DNC_PHRASES = re.compile(r"не\s+звон(ите|и|ить)|больше\s+не\s+звон|уберите\s+(мой\s+)?номер|не\s+беспокойте", re.I)

SCHEMA = """
create table if not exists calls(id integer primary key, dialog_id int, phone text, started real, duration int default 0,
  status text, recording_url text, transcript text, cost real default 0, provider_id text, error text, created real,
  by_login text);
create table if not exists do_not_call(phone text primary key, reason text, created real, by_login text);
"""


def init(db):
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("pragma table_info(messages)")}
    if "kind" not in cols:
        db.execute("alter table messages add column kind text default 'text'")


def norm_phone(p):
    """Любой российский формат → +7XXXXXXXXXX; не похоже на телефон → None."""
    d = re.sub(r"\D", "", str(p or ""))
    if len(d) == 11 and d[0] in "78":
        d = "7" + d[1:]
    elif len(d) == 10:
        d = "7" + d
    return f"+{d}" if len(d) == 11 and d[0] == "7" else None


def phone_of(load):
    """Телефон контакта из выдачи АТИ (firm.contacts[].phones[] или .phone/.mobile)."""
    for c in (load.get("firm") or {}).get("contacts") or []:
        cands = [x.get("number") if isinstance(x, dict) else x for x in c.get("phones") or []]
        cands += [c.get("phone"), c.get("mobile"), c.get("mobilePhone")]
        for x in cands:
            if n := norm_phone(x):
                return n
    return None


def parse_hours(s):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m:
        return (9, 0), (19, 0)
    a, b, c, d = map(int, m.groups())
    return (a, b), (c, d)


def in_window(hours, now=None):
    t = datetime.fromtimestamp(now or time.time(), MSK)
    (h1, m1), (h2, m2) = parse_hours(hours)
    return (h1, m1) <= (t.hour, t.minute) < (h2, m2)


def msk_day_start(now=None):
    t = datetime.fromtimestamp(now or time.time(), MSK)
    return t.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def blocked_reason(db, st, phone, dialog_id, now=None, manual=False):
    """Почему звонить нельзя (текст) или None. Ручной звонок обходит только «выключено» и задержку, не лимиты."""
    now = now or time.time()
    if not phone:
        return "нет телефона"
    if not manual and not st["call_enabled"]:
        return "звонки выключены в настройках"
    if not in_window(st["call_hours"], now):
        return f"вне окна звонков {st['call_hours']} МСК"
    if db.execute("select 1 from do_not_call where phone=?", (phone,)).fetchone():
        return "номер в стоп-листе"
    if db.execute("select 1 from calls where dialog_id=? and status in (%s)" % ",".join("?" * len(ACTIVE)),
                  (dialog_id, *ACTIVE)).fetchone():
        return "звонок уже идёт"
    if db.execute("select count(*) from calls where dialog_id=?", (dialog_id,)).fetchone()[0] >= PER_LOAD:
        return f"уже {PER_LOAD} попытки по этому грузу"
    last = db.execute("select max(created) from calls where phone=?", (phone,)).fetchone()[0]
    if last and now - last < NUMBER_GAP:
        return "этому номеру звонили меньше 2 часов назад"
    today = db.execute("select count(*) from calls where created>=?", (msk_day_start(now),)).fetchone()[0]
    if today >= st["call_daily"]:
        return f"дневной лимит звонков ({st['call_daily']}) исчерпан"
    return None


def due_dialogs(db, st, now=None):
    """Диалоги, где агент написал, а заказчик молчит дольше call_after_min (последнее сообщение — наше)."""
    now = now or time.time()
    edge = now - st["call_after_min"] * 60
    rows = db.execute("""
        select d.* from dialogs d where d.status='waiting' and d.auto=1
          and exists (select 1 from messages m where m.dialog_id=d.id)
          and (select m.mine from messages m where m.dialog_id=d.id order by m.ts desc limit 1)=1
          and (select max(m.ts) from messages m where m.dialog_id=d.id) < ?
        order by d.updated""", (ts_like(db, edge),)).fetchall()
    return [dict(r) for r in rows]


def ts_like(db, edge):
    """В messages.ts АТИ кладёт микросекунды или секунды — сравниваем в тех же единицах."""
    mx = db.execute("select max(ts) from messages").fetchone()[0] or 0
    return edge * 1e6 if mx > 1e12 else edge


def wants_no_calls(transcript):
    return bool(DNC_PHRASES.search(transcript or ""))


def cost(duration_sec, rub_per_min):
    """Связь тарифицируется поминутно с округлением вверх."""
    return round(-(-int(duration_sec or 0) // 60) * rub_per_min, 2)


def call_message(c):
    m, s = divmod(int(c["duration"] or 0), 60)
    return f"Звонок {c['phone']}, {m}:{s:02d}\n{c['transcript'] or '(расшифровки нет)'}"
