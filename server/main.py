"""Логист-ассистент: мониторинг биржи АТИ, отбор, ИИ-переговоры, база знаний."""
import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import ati
from scoring import score

ROOT = Path(__file__).parent
DB = sqlite3.connect(ROOT / "data.db", check_same_thread=False, isolation_level=None)
DB.row_factory = sqlite3.Row
DB.executescript("""
create table if not exists trucks(id integer primary key, name text, driver text, city_name text, city_id int,
  lat real, lon real, radius int, to_city_id int, to_city_name text, body_mask int, body_name text, height real,
  capacity real, free_from text, active int default 1);
create table if not exists loads(id text primary key, truck_id int, raw text, score int, verdict text, reasons text,
  metrics text, status text default 'new', reject_reason text, first_seen real, updated real);
create table if not exists rules(id integer primary key, match text, value text, block int, delta int, text text,
  source text, load_id text, active int default 1, created real);
create table if not exists dialogs(id integer primary key, load_id text, chat_id text, goal text, status text,
  summary text, facts text, auto int default 1, last_ts int default 0, created real, updated real);
create table if not exists messages(id text primary key, dialog_id int, mine int, text text, ts int);
create table if not exists kv(k text primary key, v text);
create table if not exists scans(ts real, truck_id int, seen int, total int);
""")
DEFAULTS = {"vat": 22, "min_rate_km": 55, "threshold": 55, "scan_minutes": 5, "pages": 4, "send_enabled": True,
            # агент сам берёт лучшие грузы; лимиты — чтобы не спамить биржу
            "auto_start": True, "auto_score": 85, "auto_per_cycle": 2, "auto_daily": 10,
            "default_goal": "Узнать то, чего нет в объявлении и что нужно для решения: актуален ли груз; если ставка «запрос» — "
                            "какую ставку и форму оплаты дают; точный адрес и время погрузки; особые требования (доверенность, "
                            "ремни, растентовка). Первое сообщение — 1–2 коротких вопроса, без торга. Если ставка ниже 55 ₽/км "
                            "чистыми — потом вежливо попробовать поднять."}

# Парк для демо: города/кузова — пример, правится на странице «Машины»
SEED = [
    ("Volvo FH · А742КС", "Сергей", "Санкт-Петербург", 1, 59.9387, 30.3162, 150, 1, "тент", 2.7, 20, 0),
    ("Scania R450 · В118ОР", "Андрей", "Тверь", 251, 56.8587, 35.9208, 150, 1, "тент", 3.0, 20, 1),
    ("MAN TGX · Е905МН", "Игорь", "Великий Новгород", 138, 58.521, 31.2758, 200, 1, "тент", 2.7, 20, 0),
    ("DAF XF · К330ТТ", "Павел", "Ярославль", 290, 57.6264, 39.8934, 200, 1, "тент", 2.7, 20, 2),
]


def q(sql, *a):
    return [dict(r) for r in DB.execute(sql, a).fetchall()]


def settings():
    s = dict(DEFAULTS)
    for r in q("select * from kv"):
        s[r["k"]] = json.loads(r["v"])
    return s


if not q("select id from trucks limit 1"):
    for n, d, c, cid, lat, lon, rad, mask, body, h, cap, dd in SEED:
        DB.execute("insert into trucks(name,driver,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,free_from)"
                   " values(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (n, d, c, cid, lat, lon, rad, mask, body, h, cap, (date.today() + timedelta(days=dd)).isoformat()))

ME = {}
TASKS = set()  # держим ссылки, иначе asyncio может собрать задачу до завершения
LOG = []  # лента событий для интерфейса


def log(text, kind="info"):
    LOG.insert(0, {"ts": time.time(), "text": text, "kind": kind})
    del LOG[200:]


# ---------- мониторинг биржи ----------
async def scan_truck(t):
    st, rules = settings(), q("select * from rules where active=1")
    seen = total = 0
    for page in range(1, st["pages"] + 1):
        loads, total = await ati.search(t, page)
        for l in loads:
            seen += 1
            s, verdict, reasons, m = score(l, t, rules, st)
            old = q("select truck_id, score, status from loads where id=?", l["id"])
            if old and old[0]["truck_id"] != t["id"] and old[0]["score"] >= s:
                continue  # для этого груза уже есть машина получше
            DB.execute("""insert into loads(id,truck_id,raw,score,verdict,reasons,metrics,first_seen,updated)
                values(?,?,?,?,?,?,?,?,?) on conflict(id) do update set truck_id=excluded.truck_id, raw=excluded.raw,
                score=excluded.score, verdict=excluded.verdict, reasons=excluded.reasons, metrics=excluded.metrics,
                updated=excluded.updated""",
                       (l["id"], t["id"], json.dumps(l, ensure_ascii=False), s, verdict,
                        json.dumps(reasons, ensure_ascii=False), json.dumps(m), time.time(), time.time()))
        await asyncio.sleep(1.5)  # темп как у человека
    DB.execute("insert into scans values(?,?,?,?)", (time.time(), t["id"], seen, total))
    log(f"{t['name']}: просмотрено {seen} свежих грузов из {total} в радиусе {t['radius']} км от {t['city_name']}")


def rescore():
    st, rules, trucks = settings(), q("select * from rules where active=1"), {t["id"]: t for t in q("select * from trucks")}
    for l in q("select id, truck_id, raw from loads"):
        t = trucks.get(l["truck_id"])
        if t:
            s, v, r, m = score(json.loads(l["raw"]), t, rules, st)
            DB.execute("update loads set score=?, verdict=?, reasons=?, metrics=? where id=?",
                       (s, v, json.dumps(r, ensure_ascii=False), json.dumps(m), l["id"]))


async def monitor():
    while True:
        for t in q("select * from trucks where active=1"):
            try:
                await scan_truck(t)
            except Exception as e:
                log(f"Ошибка поиска ({t['name']}): {e}", "error")
        await auto_start()
        await asyncio.sleep(settings()["scan_minutes"] * 60)


# ---------- переговоры ----------
def dialog_ctx(d):
    l = q("select * from loads where id=?", d["load_id"])[0]
    t = q("select * from trucks where id=?", l["truck_id"])[0]
    return json.loads(l["raw"]), json.loads(l["metrics"]), t


def spawn(coro):
    t = asyncio.create_task(coro)
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)


async def agent_step(d):
    try:
        await _agent_step(d)
    except Exception as e:
        DB.execute("update dialogs set status='waiting', summary=? where id=?", (f"Ошибка: {e}", d["id"]))
        log(f"Ошибка агента в диалоге #{d['id']}: {e}", "error")


async def _agent_step(d):
    load, m, t = dialog_ctx(d)
    chat = [{"mine": bool(x["mine"]), "text": x["text"]} for x in q("select * from messages where dialog_id=? order by ts", d["id"])]
    notes = "; ".join(r["text"] for r in q("select text from rules where active=1"))
    DB.execute("update dialogs set status='writing', updated=? where id=?", (time.time(), d["id"]))
    try:
        res = await agent.negotiate(load, m, t, d["goal"], chat, notes, ME.get("name", "логист"))
    except Exception as e:
        DB.execute("update dialogs set status='waiting', summary=? where id=?", (f"Ошибка агента: {e}", d["id"]))
        log(f"Агент не ответил по #{load.get('loadNumber')}: {e}", "error")
        return
    if res["reply"] and settings()["send_enabled"]:
        msg = await ati.send(d["chat_id"], res["reply"])
        DB.execute("insert or ignore into messages values(?,?,?,?,?)", (msg["id"], d["id"], 1, res["reply"], msg["ts"]))
        DB.execute("update dialogs set last_ts=max(last_ts,?) where id=?", (msg["ts"], d["id"]))
        log(f"Агент → #{load.get('loadNumber')}: {res['reply']}", "agent")
    DB.execute("update dialogs set status=?, summary=?, facts=?, updated=? where id=?",
               (res["status"], res["summary"], json.dumps(res["facts"], ensure_ascii=False), time.time(), d["id"]))
    if res["status"] == "ready":
        DB.execute("update loads set status='ready' where id=?", (d["load_id"],))
        log(f"Готово для логиста: #{load.get('loadNumber')} — {res['summary']}", "ready")
    elif res["status"] == "declined":
        DB.execute("update loads set status='declined' where id=?", (d["load_id"],))


async def poll_dialogs():
    while True:
        for d in q("select * from dialogs where status in ('waiting','ready')"):
            try:
                new = [x for x in await ati.history(d["chat_id"], d["last_ts"] or None)
                       if x.get("text") and not x.get("event") and x["ts"] > d["last_ts"]]
                incoming = False
                for x in sorted(new, key=lambda x: x["ts"]):
                    mine = str(x.get("user", "")).startswith(ME.get("ati_id", "~"))
                    DB.execute("insert or ignore into messages values(?,?,?,?,?)", (x["id"], d["id"], int(mine), x["text"], x["ts"]))
                    DB.execute("update dialogs set last_ts=max(last_ts,?) where id=?", (x["ts"], d["id"]))
                    incoming |= not mine
                if incoming:
                    log(f"Заказчик ответил в диалоге #{d['id']}", "reply")
                    if d["auto"] and d["status"] == "waiting":
                        await agent_step(q("select * from dialogs where id=?", d["id"])[0])
            except Exception as e:
                log(f"Ошибка чтения диалога #{d['id']}: {e}", "error")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app):
    try:
        ME.update(await ati.me())
    except Exception as e:
        log(f"АТИ API недоступен: {e}", "error")
    for d in q("select * from dialogs where status='writing'"):  # дожать прерванные
        spawn(agent_step(d))
    tasks = [asyncio.create_task(monitor()), asyncio.create_task(poll_dialogs())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


# ---------- API ----------
def load_row(r):
    raw = json.loads(r["raw"])
    return {**{k: r[k] for k in ("id", "truck_id", "score", "verdict", "status", "reject_reason", "first_seen")},
            "reasons": json.loads(r["reasons"]), "metrics": json.loads(r["metrics"]), "raw": raw}


@app.get("/api/stats")
def stats():
    day = datetime.combine(date.today(), datetime.min.time()).timestamp()
    seen = q("select count(*) n, sum(case when verdict='relevant' then 1 else 0 end) rel from loads where first_seen>=?", day)[0]
    scans = q("select count(*) n, coalesce(sum(seen),0) s from scans where ts>=?", day)[0]
    return {"seen": seen["n"], "relevant": seen["rel"] or 0, "filtered": seen["n"] - (seen["rel"] or 0),
            "views": scans["s"], "scans": scans["n"],
            "dialogs": q("select count(*) n from dialogs where status in ('writing','waiting')")[0]["n"],
            "ready": q("select count(*) n from dialogs where status='ready'")[0]["n"],
            "rules": q("select count(*) n from rules where active=1")[0]["n"],
            "hours_saved": round(scans["s"] * 25 / 3600, 1),  # ~25 сек логиста на просмотр одного груза
            "me": ME, "log": LOG[:40]}


@app.get("/api/loads")
def loads(view: str = "relevant", truck: int | None = None, limit: int = 200):
    where = {"relevant": "verdict='relevant' and status='new'", "filtered": "verdict='filtered' and status='new'",
             "rejected": "status='rejected'", "all": "1=1"}.get(view, "1=1")
    if truck:
        where += f" and truck_id={int(truck)}"
    order = "score desc, updated desc" if view == "relevant" else "updated desc"
    return [load_row(r) for r in q(f"select * from loads where {where} order by {order} limit ?", limit)]


class Reason(BaseModel):
    reason: str
    learn: bool = True


@app.post("/api/loads/{lid}/reject")
async def reject(lid: str, b: Reason):
    l = q("select * from loads where id=?", lid)
    if not l:
        raise HTTPException(404)
    DB.execute("update loads set status='rejected', reject_reason=? where id=?", (b.reason, lid))
    rule = None
    if b.learn and b.reason.strip():
        rule = await add_rule_from_text(b.reason, json.loads(l[0]["raw"]), json.loads(l[0]["metrics"]), lid)
    return {"rule": rule}


async def add_rule_from_text(text, load=None, metrics=None, lid=None):
    r = await agent.learn_rule(text, load, metrics)
    cur = DB.execute("insert into rules(match,value,block,delta,text,source,load_id,created) values(?,?,?,?,?,?,?,?)",
                     (r["match"], r["value"].lower(), int(r["block"]), r["delta"], r["text"], text, lid, time.time()))
    rescore()
    log(f"База знаний пополнена: {r['text']}", "learn")
    return {**r, "id": cur.lastrowid}


class Goal(BaseModel):
    goal: str | None = None


@app.post("/api/loads/{lid}/agent")
async def start_agent(lid: str, b: Goal):
    l = q("select * from loads where id=?", lid)
    if not l:
        raise HTTPException(404)
    try:
        return await start_dialog(l[0], b.goal or settings()["default_goal"])
    except ValueError as e:
        raise HTTPException(400, str(e))


async def start_dialog(l, goal, by_agent=False):
    raw = json.loads(l["raw"])
    firm = raw.get("firm") or {}
    if not firm.get("contacts"):
        raise ValueError("У груза скрыт заказчик — написать некому")
    c = firm["contacts"][0]
    chat_id = await ati.open_chat(f"{firm['id']}.{c['id']}", c["name"], firm.get("name", ""))
    cur = DB.execute("insert into dialogs(load_id,chat_id,goal,status,summary,created,updated) values(?,?,?,?,?,?,?)",
                     (l["id"], chat_id, goal, "writing",
                      "Агент сам выбрал груз и готовит вопросы" if by_agent else "Агент готовит первое сообщение",
                      time.time(), time.time()))
    DB.execute("update loads set status='dialog' where id=?", (l["id"],))
    d = q("select * from dialogs where id=?", cur.lastrowid)[0]
    spawn(agent_step(d))
    return d


async def auto_start():
    """Агент сам берёт лучшие свежие грузы и начинает переписку."""
    st = settings()
    if not (st["auto_start"] and st["send_enabled"]):
        return
    day = datetime.combine(date.today(), datetime.min.time()).timestamp()
    left = st["auto_daily"] - q("select count(*) n from dialogs where created>=?", day)[0]["n"]
    busy_firms = {json.loads(r["raw"]).get("firm", {}).get("id") for r in
                  q("select l.raw from dialogs d join loads l on l.id=d.load_id where d.status in ('writing','waiting','ready')")}
    started = 0
    for l in q("select * from loads where verdict='relevant' and status='new' and score>=? order by score desc, updated desc",
               st["auto_score"]):
        if started >= min(st["auto_per_cycle"], left):
            break
        firm = json.loads(l["raw"]).get("firm") or {}
        if not firm.get("contacts") or firm.get("id") in busy_firms:
            continue  # одной фирме — один диалог за раз
        try:
            await start_dialog(l, st["default_goal"], by_agent=True)
            busy_firms.add(firm["id"])
            started += 1
            log(f"Агент сам взял груз в работу: {firm.get('name')} (оценка {l['score']})", "agent")
        except Exception as e:
            log(f"Не удалось начать диалог с {firm.get('name')}: {e}", "error")


@app.post("/api/agent/run")
async def agent_run():
    before = q("select count(*) n from dialogs")[0]["n"]
    await auto_start()
    return {"started": q("select count(*) n from dialogs")[0]["n"] - before}


@app.get("/api/dialogs")
def dialogs():
    out = []
    for d in q("select * from dialogs order by updated desc"):
        l = q("select * from loads where id=?", d["load_id"])
        out.append({**d, "facts": json.loads(d["facts"] or "{}"), "load": load_row(l[0]) if l else None,
                    "messages": q("select * from messages where dialog_id=? order by ts", d["id"])})
    return out


class Text(BaseModel):
    text: str


@app.post("/api/dialogs/{did}/send")
async def send_manual(did: int, b: Text):
    d = q("select * from dialogs where id=?", did)[0]
    msg = await ati.send(d["chat_id"], b.text)
    DB.execute("insert or ignore into messages values(?,?,?,?,?)", (msg["id"], did, 1, b.text, msg["ts"]))
    DB.execute("update dialogs set last_ts=max(last_ts,?), updated=? where id=?", (msg["ts"], time.time(), did))
    return {"ok": True}


class DStatus(BaseModel):
    status: str | None = None
    auto: bool | None = None


@app.patch("/api/dialogs/{did}")
def patch_dialog(did: int, b: DStatus):
    if b.status:
        DB.execute("update dialogs set status=?, updated=? where id=?", (b.status, time.time(), did))
        lid = q("select load_id from dialogs where id=?", did)[0]["load_id"]
        DB.execute("update loads set status=? where id=?", ({"taken": "taken", "declined": "declined"}.get(b.status, "dialog"), lid))
    if b.auto is not None:
        DB.execute("update dialogs set auto=? where id=?", (int(b.auto), did))
    return {"ok": True}


@app.get("/api/trucks")
def trucks():
    return q("select t.*, (select count(*) from loads l where l.truck_id=t.id and l.verdict='relevant' and l.status='new') relevant"
             " from trucks t order by id")


class Truck(BaseModel):
    name: str
    driver: str = ""
    city_name: str
    radius: int = 150
    body_mask: int = 1
    body_name: str = "тент"
    height: float = 2.7
    capacity: float = 20
    free_from: str
    active: bool = True


async def city(name):
    r = await ati.client.get("https://api.ati.su/v1.0/dictionaries/cities", params={"name": name})
    r.raise_for_status()
    c = (r.json() or [None])[0]
    if not c:
        raise HTTPException(400, f"Город «{name}» не найден в справочнике АТИ")
    return c


@app.post("/api/trucks")
async def add_truck(b: Truck):
    c = await city(b.city_name)
    DB.execute("insert into trucks(name,driver,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,free_from,active)"
               " values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (b.name, b.driver, c["CityName"], c["CityId"], c["Latitude"], c["Longitude"], b.radius, b.body_mask,
                b.body_name, b.height, b.capacity, b.free_from, int(b.active)))
    return {"ok": True}


@app.put("/api/trucks/{tid}")
async def edit_truck(tid: int, b: Truck):
    t = q("select * from trucks where id=?", tid)[0]
    c = {"CityName": t["city_name"], "CityId": t["city_id"], "Latitude": t["lat"], "Longitude": t["lon"]}
    if b.city_name != t["city_name"]:
        c = await city(b.city_name)
    DB.execute("update trucks set name=?,driver=?,city_name=?,city_id=?,lat=?,lon=?,radius=?,body_mask=?,body_name=?,"
               "height=?,capacity=?,free_from=?,active=? where id=?",
               (b.name, b.driver, c["CityName"], c["CityId"], c["Latitude"], c["Longitude"], b.radius, b.body_mask,
                b.body_name, b.height, b.capacity, b.free_from, int(b.active), tid))
    rescore()
    return {"ok": True}


@app.post("/api/trucks/{tid}/scan")
async def scan_now(tid: int):
    await scan_truck(q("select * from trucks where id=?", tid)[0])
    return {"ok": True}


@app.get("/api/rules")
def rules():
    return q("select * from rules order by created desc")


@app.post("/api/rules")
async def add_rule(b: Text):
    return await add_rule_from_text(b.text)


class RulePatch(BaseModel):
    active: bool


@app.patch("/api/rules/{rid}")
def patch_rule(rid: int, b: RulePatch):
    DB.execute("update rules set active=? where id=?", (int(b.active), rid))
    rescore()
    return {"ok": True}


@app.delete("/api/rules/{rid}")
def del_rule(rid: int):
    DB.execute("delete from rules where id=?", (rid,))
    rescore()
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return settings()


@app.put("/api/settings")
def put_settings(b: dict):
    for k, v in b.items():
        if k in DEFAULTS:
            DB.execute("insert into kv values(?,?) on conflict(k) do update set v=excluded.v", (k, json.dumps(v)))
    rescore()
    return settings()


DIST = ROOT.parent / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        return FileResponse(DIST / "index.html")
