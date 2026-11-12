"""Логист-ассистент: мониторинг биржи АТИ, отбор, ИИ-переговоры, база знаний."""
import asyncio
import hmac
import json
import logging
import os
import re
import sqlite3
import time

os.environ.setdefault("TZ", "Europe/Moscow")  # «сегодня», лимиты дня и освобождение машин — по Москве, как окно звонков
time.tzset()

import httpx
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent
import llm
import ati
import auth
import calls
import fleet
import telephony
import voice
from urllib.parse import urlparse
from scoring import score

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = sqlite3.connect(DATA_DIR / "data.db", check_same_thread=False, isolation_level=None)
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

auth.init(DB)
calls.init(DB)
DB.execute("create table if not exists llm_usage(ts real, model text, prompt_tokens int, completion_tokens int, cost real)")
DB.execute("create table if not exists voice_usage(ts real, dialog_id int, seconds real, cost real, login text, reason text)")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


def migrate():
    """Новые колонки для старых баз (create table if not exists их не добавит)."""
    cols = {r[1] for r in DB.execute("pragma table_info(trucks)")}
    if "status" not in cols:
        DB.execute("alter table trucks add column status text default 'free'")
        DB.execute("update trucks set status='busy' where free_from>?", (date.today().isoformat(),))
    dcols = {r[1] for r in DB.execute("pragma table_info(dialogs)")}
    if "draft" not in dcols:  # ответ агента при выключенной отправке
        DB.execute("alter table dialogs add column draft text")
    if "fails" not in dcols:  # подряд неудачные шаги агента — для повтора
        DB.execute("alter table dialogs add column fails int default 0")
    if "by_agent" not in dcols:  # диалог начал агент сам (в дневной лимит автостарта), а не логист
        DB.execute("alter table dialogs add column by_agent int default 0")


migrate()
# На новой (и перенесённой) базе агент молчит: сначала знакомство с интерфейсом, потом владелец включает
# переключателями в «Настройках» или командой `manage.py agent on`. Поиск и оценка грузов работают сразу.
DEFAULTS = {"vat": 22, "min_rate_km": 55, "threshold": 55, "scan_minutes": 5, "pages": 4, "send_enabled": False,
            # агент сам берёт лучшие грузы; лимиты — чтобы не спамить биржу
            "auto_start": False, "auto_score": 85, "auto_per_cycle": 1, "auto_daily": 3,  # старт у клиента — осторожно, потом поднять
            "default_goal": "Узнать то, чего нет в объявлении и что нужно для решения: актуален ли груз; если ставка «запрос» — "
                            "какую ставку и форму оплаты дают; точный адрес и время погрузки; особые требования (доверенность, "
                            "ремни, растентовка). Первое сообщение — 1–2 коротких вопроса, без торга. Если ставка ниже {min_rate_km} ₽/км "
                            "чистыми — потом вежливо попробовать поднять.",
            # готовые сценарии — кнопки в окне «Поручить агенту»; правит владелец в Настройках
            "goal_presets": ["Узнать, актуален ли груз и можно ли поднять ставку до 60 ₽/км",
                             "Уточнить точный адрес, время погрузки и нужна ли доверенность",
                             "Предложить нашу машину и спросить про обратную загрузку"]}
DEFAULTS.update(calls.DEFAULTS)

# Парк для демо (DEMO_SEED=1): города/кузова — пример, правится на странице «Машины». На проде не вставляется.
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


if os.environ.get("DEMO_SEED") == "1" and not q("select id from trucks limit 1"):  # демо-парк только локально
    for n, d, c, cid, lat, lon, rad, mask, body, h, cap, dd in SEED:
        DB.execute("insert into trucks(name,driver,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,free_from,status)"
                   " values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (n, d, c, cid, lat, lon, rad, mask, body, h, cap, (date.today() + timedelta(days=dd)).isoformat(),
                    "busy" if dd else "free"))

ME = {}
# Состояние внешних систем для интерфейса (ATI, модель, телефония): ok=None — ещё не проверяли
HEALTH = {k: {"ok": None, "err": "", "ts": 0.0} for k in ("ati", "llm", "phone")}


def mark(part, ok, err=""):
    """Обновить состояние части системы; ошибку пишем в ленту только при смене текста (без спама каждый цикл)."""
    old = HEALTH[part]
    err = str(err)[:300]
    if not ok and (old["ok"] is not False or old["err"] != err):
        log(f"{ {'ati': 'АТИ', 'llm': 'Модель', 'phone': 'Телефония'}[part] }: {err}", "error")
    HEALTH[part] = {"ok": ok, "err": "" if ok else err, "ts": time.time()}


def ati_err(e):
    """Понятный текст ошибки АТИ (401/403 — токен)."""
    if not ati.TOKEN:
        return "ATI_TOKEN не задан в .env — поиск и переписка не работают"
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return f"АТИ отклонила токен ({code}) — проверьте ATI_TOKEN и тариф"
        return f"АТИ ответила {code}"
    if isinstance(e, httpx.TransportError):
        return f"АТИ недоступна: {type(e).__name__}"
    return f"{type(e).__name__}: {e}"


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
            try:
                s, verdict, reasons, m = score(l, t, rules, st)
            except Exception as e:  # необычный груз из выдачи — пропускаем его, а не весь проход
                log(f"Груз {l.get('id')} пропущен: {type(e).__name__}: {e}", "error")
                continue
            old = q("select truck_id, score, status from loads where id=?", l["id"])
            if old and old[0]["status"] != "new":  # в работе: только освежаем объявление, машину не меняем
                DB.execute("update loads set raw=?, updated=? where id=?", (json.dumps(l, ensure_ascii=False), time.time(), l["id"]))
                continue
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
    mark("ati", True)
    log(f"{t['name']}: просмотрено {seen} свежих грузов из {total} в радиусе {t['radius']} км от {t['city_name']}")


def rescore():
    st, rules, trucks = settings(), q("select * from rules where active=1"), {t["id"]: t for t in q("select * from trucks")}
    for l in q("select id, truck_id, raw from loads"):
        t = trucks.get(l["truck_id"])
        if t:
            try:
                s, v, r, m = score(json.loads(l["raw"]), t, rules, st)
            except Exception as e:  # один странный груз не должен ронять сохранение настроек/правила
                log(f"Груз {l['id']} не пересчитан: {type(e).__name__}: {e}", "error")
                continue
            DB.execute("update loads set score=?, verdict=?, reasons=?, metrics=? where id=?",
                       (s, v, json.dumps(r, ensure_ascii=False), json.dumps(m), l["id"]))


async def monitor():
    while True:
        try:
            for tid in fleet.release_due(DB):
                log(f"Машина #{tid} освободилась по дате — снова ищем грузы")
            where, args = fleet.scan_filter()
            for t in q(f"select * from trucks where {where}", *args):
                try:
                    await scan_truck(t)
                except Exception as e:
                    mark("ati", False, f"поиск грузов: {ati_err(e)}")
            await auto_start()
        except Exception as e:  # ошибка одного прохода не должна останавливать поиск навсегда
            log(f"Ошибка мониторинга: {e}", "error")
        try:
            pause = max(60, int(settings()["scan_minutes"]) * 60)
        except Exception:
            pause = 300
        await asyncio.sleep(pause)


# ---------- переговоры ----------
def dialog_ctx(d):
    l = q("select * from loads where id=?", d["load_id"])[0]
    t = q("select * from trucks where id=?", l["truck_id"])[0]
    return json.loads(l["raw"]), json.loads(l["metrics"]), t


def spawn(coro):
    t = asyncio.create_task(coro)
    TASKS.add(t)
    t.add_done_callback(_task_done)


def _task_done(t):
    TASKS.discard(t)
    if not t.cancelled() and t.exception():
        log(f"Фоновая задача упала: {type(t.exception()).__name__}: {t.exception()}", "error")


STEPPING = set()  # диалоги, по которым агент сейчас думает: второй шаг параллельно = два ответа заказчику
AGAIN = set()  # пока думал, пришло новое (ответ, итог звонка) — ещё один шаг сразу после текущего
FAIL_SQL = ("update dialogs set status=case when status='writing' then 'waiting' else status end, summary=?, "
            "fails=coalesce(fails,0)+1 where id=? and status in ('writing','waiting','ready')")  # «Готово» ошибкой не понижаем


async def agent_step(d):
    if d["id"] in STEPPING:
        AGAIN.add(d["id"])
        return
    STEPPING.add(d["id"])
    try:
        while True:
            AGAIN.discard(d["id"])
            try:
                await _agent_step(d)
            except Exception as e:  # любая ошибка шага — в summary, чтобы логист её видел (закрытый диалог не трогаем)
                DB.execute(FAIL_SQL, (f"Ошибка: {type(e).__name__}: {e}", d["id"]))
                log(f"Ошибка агента в диалоге #{d['id']}: {e}", "error")
            if d["id"] not in AGAIN:
                break
            d = q("select * from dialogs where id=?", d["id"])[0]
    finally:
        STEPPING.discard(d["id"])
        AGAIN.discard(d["id"])


def prompt_inputs(d):
    """Всё, что агент знает о диалоге, — одинаково для чата, звонка и тестового звонка."""
    load, m, t = dialog_ctx(d)
    chat = [{"mine": bool(x["mine"]), "call": x.get("kind") == "call",
             "text": f"{agent.CALL_MARK} {x['text']}" if x.get("kind") == "call" else x["text"]}
            for x in q("select * from messages where dialog_id=? order by ts", d["id"])]
    notes = "; ".join(r["text"] for r in q("select text from rules where active=1"))
    goal = str(d["goal"] or "").replace("{min_rate_km}", str(settings()["min_rate_km"]))
    return load, m, t, goal, chat, notes


def instructions_for_call(d):
    """Инструкции голосу. Знания компании по телефону не передаём: собеседник не должен вытянуть их вслух."""
    load, m, t, goal, chat, _ = prompt_inputs(d)
    return agent.call_instructions(load, m, t, goal, chat, "", ME.get("name", "логист"))


def greeting_for_call(d):
    load, *_ = prompt_inputs(d)
    return agent.greeting(load, ME.get("name", "логист"))


def safe_greeting(d):
    try:
        return greeting_for_call(d)
    except Exception:
        return None


def safe_instructions(d):
    try:
        return instructions_for_call(d)
    except Exception as e:  # нет машины/груза — звоним всё равно, голос возьмёт цель из ctx
        log(f"Инструкции звонка по диалогу #{d['id']} не собрались: {type(e).__name__}: {e}", "error")
        return None


# Ответ, который агент не должен отправлять сам: обещание от имени компании, внутренние правила, простыня
RISKY = re.compile(r"(?i)\b(подтверждаем|подтверждаю|забираем|договорились|бронируем|ставим машину|выезжаем)\b"
                   r"|\b(берём|берем|возьмём|возьмем)\b(?!\s+ли)|\b(нас|нам)\s+(устраивает|подходит)\b")


def risky_reply(reply, notes):
    """Похоже на обещание от имени компании (вопросы не в счёт), на внутренние правила или на простыню."""
    if len(reply) > 600:
        return True
    if any(RISKY.search(s) for s in re.split(r"(?<=[.!…])\s+", reply) if s.strip() and not s.rstrip().endswith("?")):
        return True
    low = reply.lower()
    return any(len(frag) > 20 and frag in low for frag in (x.strip().lower() for x in notes.split(";")))


async def _agent_step(d):
    load, m, t, goal, chat, notes = prompt_inputs(d)
    prev = (q("select status from dialogs where id=?", d["id"]) or [{"status": "waiting"}])[0]["status"]
    if not DB.execute("update dialogs set status='writing', updated=? where id=? and status in ('writing','waiting','ready')",
                      (time.time(), d["id"])).rowcount:
        return  # логист уже закрыл диалог («Берём»/«Отказ») — не воскрешаем
    try:
        res = await agent.negotiate(load, m, t, goal, chat, notes, ME.get("name", "логист"))
    except Exception as e:  # сбой модели: возвращаем прежний статус («Готово» не понижаем), причину — в summary
        DB.execute("update dialogs set status=?, summary=?, fails=coalesce(fails,0)+1 where id=? and status='writing'",
                   ("ready" if prev == "ready" else "waiting", f"Ошибка агента: {e}", d["id"]))
        mark("llm", False, e)
        return
    mark("llm", True)
    now = q("select status from dialogs where id=?", d["id"])[0]["status"]
    if now in ("taken", "declined"):  # пока агент думал, логист сам закрыл диалог — его решение главнее
        DB.execute("update dialogs set summary=?, facts=?, updated=? where id=?",
                   (res["summary"], json.dumps(res["facts"], ensure_ascii=False), time.time(), d["id"]))
        return
    hold = bool(res["reply"]) and risky_reply(res["reply"], notes)
    if hold:  # видно в карточке: заказчик ждёт, а ответ лежит черновиком
        res["summary"] = f"⚠ Ответ задержан — проверьте черновик. {res['summary']}"
        log(f"Ответ агента по #{load.get('loadNumber')} похож на обещание — оставили черновиком для логиста", "agent")
    if res["reply"] and d["chat_id"] and settings()["send_enabled"] and not hold:
        try:
            msg = await ati.send(d["chat_id"], res["reply"])
        except Exception as e:
            mark("ati", False, f"отправка сообщения: {ati_err(e)}")
            raise
        DB.execute("insert or ignore into messages(id,dialog_id,mine,text,ts) values(?,?,?,?,?)", (msg["id"], d["id"], 1, res["reply"], msg["ts"]))
        DB.execute("update dialogs set last_ts=max(last_ts,?), draft=null where id=?", (msg["ts"], d["id"]))
        log(f"Агент → #{load.get('loadNumber')}: {res['reply']}", "agent")
    elif res["reply"] and d["chat_id"]:  # отправка выключена — черновик, логист отправит или поправит сам
        DB.execute("update dialogs set draft=? where id=?", (res["reply"], d["id"]))
        log(f"Черновик агента для #{load.get('loadNumber')} ждёт логиста", "agent")
    if not DB.execute("update dialogs set status=?, summary=?, facts=?, updated=?, fails=0 where id=? and status='writing'",
                      (res["status"], res["summary"], json.dumps(res["facts"], ensure_ascii=False), time.time(), d["id"])).rowcount:
        DB.execute("update dialogs set summary=?, facts=? where id=?",  # закрыли, пока отправляли: решение логиста главнее
                   (res["summary"], json.dumps(res["facts"], ensure_ascii=False), d["id"]))
        return
    if res["status"] == "ready":
        DB.execute("update loads set status='ready' where id=?", (d["load_id"],))
        log(f"Готово для логиста: #{load.get('loadNumber')} — {res['summary']}", "ready")
    elif res["status"] == "declined":
        DB.execute("update loads set status='declined' where id=?", (d["load_id"],))


def dnc_from_dialog(d, reason):
    l = q("select raw from loads where id=?", d["load_id"])
    phone = calls.phone_of(json.loads(l[0]["raw"])) if l else None
    if phone and not q("select 1 from do_not_call where phone=?", phone):
        DB.execute("insert or ignore into do_not_call values(?,?,?,?)", (phone, reason, time.time(), "агент"))
        log(f"{phone}: {reason} — номер в стоп-листе", "info")


def is_mine(user):
    """Сообщение от нашей фирмы (агент или логист с сайта АТИ). Формат user: «код фирмы» или «код.контакт»."""
    me = str(ME.get("ati_id") or "")
    return bool(me) and str(user or "").split(".")[0] == me


FIRST_RETRY_SEC, FIRST_RETRIES = 300, 3  # шаг не удался (модель/АТИ) — повторяем, а не бросаем диалог


async def retry_failed(now=None):
    """Шаг агента не удался (модель/АТИ): первое сообщение не ушло или ответ заказчика остался без ответа — повторяем."""
    rows = q(f"select d.* from dialogs d where d.status='waiting' and d.auto=1 and d.chat_id is not null"
             f" and coalesce(d.fails,0) between 1 and ? and d.updated<? and (not exists(select 1 from messages m where m.dialog_id=d.id)"
             f" or (select m.mine from messages m where m.dialog_id=d.id order by {calls.TS_SEC} desc limit 1)=0)",
             FIRST_RETRIES, (now or time.time()) - FIRST_RETRY_SEC)
    for d in rows:
        log(f"Повторяем шаг агента в диалоге #{d['id']} после сбоя", "agent")
        await agent_step(d)
    return [d["id"] for d in rows]


async def poll_dialogs():
    while True:
        if not ME:
            try:
                ME.update(await ati.me())
                mark("ati", True)
            except Exception as e:
                mark("ati", False, f"аккаунт: {ati_err(e)}")
                await asyncio.sleep(60)
                continue  # без своего ati_id не отличить наши сообщения от ответов заказчика — переписку не читаем
        await retry_failed()
        for d in q("select * from dialogs where status in ('waiting','ready') and chat_id is not null"):
            try:
                new = [x for x in await ati.history(d["chat_id"], d["last_ts"] or None)
                       if x.get("text") and not x.get("event") and x["ts"] > d["last_ts"]]
                mark("ati", True)
                incoming = False
                for x in sorted(new, key=lambda x: x["ts"]):
                    mine = is_mine(x.get("user"))
                    DB.execute("insert or ignore into messages(id,dialog_id,mine,text,ts) values(?,?,?,?,?)", (x["id"], d["id"], int(mine), x["text"], x["ts"]))
                    DB.execute("update dialogs set last_ts=max(last_ts,?) where id=?", (x["ts"], d["id"]))
                    incoming |= not mine
                    if not mine and calls.wants_no_calls(x["text"]):
                        dnc_from_dialog(d, "написали в чате АТИ: не звонить")
                if incoming:
                    log(f"Заказчик ответил в диалоге #{d['id']}", "reply")
                    fresh = q("select * from dialogs where id=?", d["id"])[0]  # логист мог закрыть или выключить агента
                    if fresh["auto"] and fresh["status"] == "waiting":
                        await agent_step(fresh)
            except Exception as e:  # сбой одного чата — в ленту с номером; плашку АТИ красим только за токен/сеть
                log(f"Диалог #{d['id']}: переписка не прочиталась — {ati_err(e)}", "error")
                if isinstance(e, httpx.TransportError) or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403)):
                    mark("ati", False, f"чтение переписки: {ati_err(e)}")
        await asyncio.sleep(15)


# ---------- звонки ----------
RECORDINGS = DATA_DIR / "recordings"
CALL_RUB_PER_MIN = float(os.environ.get("CALL_RUB_PER_MIN", "3"))  # связь ВАТС
# голос Yandex Realtime: та же оценка, что у тестовых звонков (VOICE_RUB_SEC × 60), пока не задано своё
SPEECH_RUB_PER_MIN = float(os.environ.get("SPEECH_RUB_PER_MIN") or voice.cfg()["rub_sec"] * 60)
WEBHOOK_SECRET = os.environ.get("TELEPHONY_WEBHOOK_SECRET", "")
if 0 < len(WEBHOOK_SECRET) < 32:
    log("TELEPHONY_WEBHOOK_SECRET короче 32 символов — вебхук выключен (openssl rand -hex 24)", "error")
    WEBHOOK_SECRET = ""
MAX_RECORDING = 50 * 1024 * 1024
STALE_CALL_MIN = 20  # без итога от телефонии дольше — считаем звонок несостоявшимся


async def start_call(d, manual=False):
    """Поставить звонок по диалогу. -> (call | None, причина отказа | None)."""
    l = q("select raw from loads where id=?", d["load_id"])
    raw = json.loads(l[0]["raw"]) if l else {}
    phone = calls.phone_of(raw)  # телефоны приходят в выдаче АТИ: firm.contacts[].phones[]
    if not PHONE.ready:
        return None, PHONE.why
    why = calls.blocked_reason(DB, settings(), phone, d["id"], manual=manual)
    if not why and len(q("select 1 from calls where status in (%s)" % ",".join("?" * len(calls.ACTIVE)), *calls.ACTIVE)) >= CALL_LINES:
        why = "все линии заняты — позвоним следующим проходом"
    if why:
        return None, why
    u = auth.current() if manual else None
    by = u["login"] if u else "агент"
    cid = DB.execute("insert into calls(dialog_id,phone,status,created,by_login) values(?,?,?,?,?)",
                     (d["id"], phone, "queued", time.time(), by)).lastrowid
    call = q("select * from calls where id=?", cid)[0]
    ctx = {"goal": d["goal"], "route": f"{raw.get('loading', {}).get('location', {}).get('city', '')} — "
                                      f"{raw.get('unloading', {}).get('location', {}).get('city', '')}",
           "load": raw, "facts": json.loads(d.get("facts") or "{}"),  # для голосового бота: та же цель и контекст
           "chat": [x["text"] for x in q("select text from messages where dialog_id=? order by ts", d["id"])][-10:],
           "instructions": safe_instructions(d), "greeting": safe_greeting(d)}
    try:
        pid = await PHONE.start(call, ctx)
        if not pid:  # ВАТС приняла команду, но id не вернула — попытку считаем (иначе перезвон каждые 30 минут)
            pid = f"unknown-{cid}"
            log(f"Телефония не вернула id звонка #{cid} — итог может не прийти", "error")
        DB.execute("update calls set provider_id=?, status=case when status='queued' then 'ringing' else status end,"
                   " started=? where id=?", (pid, time.time(), cid))
    except Exception as e:  # не дозвонились до телефонии — попытка не считается (provider_id пустой)
        DB.execute("update calls set status='failed', error=? where id=?", (str(e)[:500], cid))
        mark("phone", False, f"звонок не начался: {e}")
        return q("select * from calls where id=?", cid)[0], f"ошибка телефонии: {e}"
    mark("phone", True)
    log(f"{'Звоним' if not manual else by + ': звоним'} {phone} по диалогу #{d['id']}", "agent")
    return q("select * from calls where id=?", cid)[0], None


def clean_result(res):
    """Итог от мока/ВАТС/бота → только известные поля разумных типов; мусор отбрасываем."""
    if not isinstance(res, dict):
        return {}
    out = {}
    if res.get("status") in calls.ACTIVE + telephony.FINAL:
        out["status"] = res["status"]
    try:
        if res.get("duration") is not None:
            out["duration"] = max(0, min(int(float(res["duration"])), 36000))
    except (TypeError, ValueError):
        pass
    if isinstance(res.get("recording_url"), str) and res["recording_url"].startswith(("http://", "https://")):
        out["recording_url"] = res["recording_url"][:2000]
    if isinstance(res.get("transcript"), str) and res["transcript"].strip():
        out["transcript"] = res["transcript"][:20000]
    return out


async def apply_call_result(call_id, res, by_provider=False, background=False):
    """Итог звонка; источники (мок, вебхук ВАТС, голосовой бот) дополняют друг друга, повторы безопасны."""
    rows = q("select * from calls where provider_id=?" if by_provider else "select * from calls where id=?", str(call_id))
    if not rows:
        return None
    c, new = rows[0], clean_result(res)
    was_final = c["status"] in telephony.FINAL
    st = new.get("status")
    if st and was_final and (st not in telephony.FINAL or
                             (c["status"] == "done" and st != "done" and (c["transcript"] or c["duration"]))):
        new.pop("status")  # не откатываем завершённый и не понижаем состоявшийся разговор
    if "duration" in new:
        new["duration"] = max(new["duration"], c["duration"] or 0)
        new["cost"] = calls.cost(new["duration"], CALL_RUB_PER_MIN + SPEECH_RUB_PER_MIN)
    fresh_transcript = new.get("transcript") not in (None, c["transcript"])
    if not fresh_transcript:
        new.pop("transcript", None)
    if c["recording_url"] and c["recording_url"].startswith("/api/"):
        new.pop("recording_url", None)  # запись уже скачана
    if new:
        DB.execute(f"update calls set {', '.join(f'{k}=?' for k in new)} where id=?", (*new.values(), c["id"]))
    c = q("select * from calls where id=?", c["id"])[0]
    if "recording_url" in new:
        spawn(fetch_recording(c["id"], new["recording_url"]))
    if c["status"] in telephony.FINAL and (not was_final or fresh_transcript):
        job = finish_call(c, fresh_transcript)
        if background:
            spawn(job)  # вебхук отвечает сразу, агент работает в фоне — провайдер не ретраит по таймауту
        else:
            await job
    return c


async def finish_call(c, fresh_transcript):
    d = q("select * from dialogs where id=?", c["dialog_id"])
    if not d:
        return
    d = d[0]
    m, s = divmod(int(c["duration"] or 0), 60)
    log(f"Звонок {c['phone']}: {c['status']}, {m}:{s:02d}, {c['cost'] or 0} ₽", "info")
    have = q("select ts from messages where id=?", f"call-{c['id']}")
    if not c["transcript"] or (have and not fresh_transcript):
        return  # расшифровки нет или она уже в переписке
    ts = have[0]["ts"] if have else msg_now(d["id"])
    DB.execute("insert or replace into messages(id,dialog_id,mine,text,ts,kind) values(?,?,?,?,?,?)",
               (f"call-{c['id']}", d["id"], 0, calls.call_message(c), ts, "call"))
    if calls.wants_no_calls(c["transcript"]):
        DB.execute("insert or ignore into do_not_call values(?,?,?,?)", (c["phone"], "попросили не звонить", time.time(), "агент"))
        log(f"{c['phone']} попросили не звонить — номер в стоп-листе", "info")
    if d["status"] not in ("taken", "declined"):
        await agent_step(d)  # facts/summary/status по расшифровке — тем же negotiate


def msg_now(dialog_id):
    """Метка времени в тех же единицах, что у сообщений этого диалога (АТИ отдаёт микросекунды)."""
    last = DB.execute("select max(ts) from messages where dialog_id=?", (dialog_id,)).fetchone()[0]
    return int(time.time() * calls.ts_unit(last))


def recording_hosts():
    hosts = {h.strip().lower() for h in os.environ.get("RECORDING_HOSTS", "").split(",") if h.strip()}
    api = os.environ.get("MEGAFON_API_URL", "")
    if api:
        hosts.add((httpx.URL(api).host or "").lower())
    return hosts - {""}


def safe_recording_url(url):
    """Защита от SSRF: только хосты ВАТС из allowlist и только публичные адреса."""
    import ipaddress
    import socket
    u = httpx.URL(url)
    host = (u.host or "").lower()
    if u.scheme not in ("https", "http") or host not in recording_hosts():
        raise ValueError(f"хост {host or '?'} не в RECORDING_HOSTS")
    for info in socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError(f"{host} указывает на внутренний адрес {ip}")


async def fetch_recording(cid, url):
    f = RECORDINGS / f"{cid}.mp3"
    try:
        if f.exists():
            DB.execute("update calls set recording_url=? where id=?", (f"/api/calls/{cid}/recording", cid))
            return
        await asyncio.to_thread(safe_recording_url, url)
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        tmp, size = f.with_suffix(".part"), 0
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
            async with c.stream("GET", url) as r:
                r.raise_for_status()
                with open(tmp, "wb") as out:
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RECORDING:
                            raise ValueError("запись больше 50 МБ")
                        out.write(chunk)
        tmp.rename(f)
        DB.execute("update calls set recording_url=? where id=?", (f"/api/calls/{cid}/recording", cid))
    except Exception as e:
        f.with_suffix(".part").unlink(missing_ok=True)
        DB.execute("update calls set error=? where id=?", (f"запись не скачалась: {e}"[:300], cid))
        log(f"Запись звонка #{cid} не скачалась: {e}", "error")


PHONE = telephony.make(apply_call_result)
CALL_LINES = max(1, int(os.environ.get("CALL_LINES", "1")))  # одновременных звонков (линий бота в ВАТС)
llm.USAGE_HOOK = lambda model, pin, pout, rub: DB.execute("insert into llm_usage values(?,?,?,?,?)",
                                                          (time.time(), model, pin, pout, rub))


def costs_today():
    day = calls.msk_day_start()
    u = q("select count(*) n, coalesce(sum(cost),0) rub, coalesce(sum(prompt_tokens+completion_tokens),0) tokens"
          " from llm_usage where ts>=?", day)[0]
    c = q("select count(*) n, coalesce(sum(cost),0) rub, coalesce(sum(duration),0) sec from calls where created>=?", day)[0]
    v = q("select count(*) n, coalesce(sum(cost),0) rub from voice_usage where ts>=?", day)[0]
    return {"llm_requests": u["n"], "llm_tokens": u["tokens"], "llm_rub": round(u["rub"], 2),
            "calls": c["n"], "call_minutes": round(c["sec"] / 60, 1), "calls_rub": round(c["rub"], 2),
            "voice_tests": v["n"], "voice_rub": round(v["rub"], 2)}


async def reap_stale_calls():
    """Звонок без итога дольше STALE_CALL_MIN (рестарт, потерянный вебхук) не должен блокировать диалог навсегда."""
    edge = time.time() - STALE_CALL_MIN * 60
    for c in q("select * from calls where status in (%s) and created<?" % ",".join("?" * len(calls.ACTIVE)),
               *calls.ACTIVE, edge):
        DB.execute("update calls set error=? where id=?", ("нет итога от телефонии", c["id"]))
        await apply_call_result(c["id"], {"status": "done" if c["transcript"] else "failed"})


async def call_loop():
    """Раз в минуту: закрыть зависшие звонки; молчат в чате дольше call_after_min — звоним (в окне, в лимитах)."""
    while True:
        try:
            await call_tick()
        except Exception as e:
            log(f"Ошибка планировщика звонков: {e}", "error")
        await asyncio.sleep(60)


async def call_tick():
    """Один проход планировщика: закрыть зависшие, позвонить тем, кто молчит. -> [(dialog_id, call|None, причина)]."""
    await reap_stale_calls()
    st, done = settings(), []
    if st["call_enabled"] and not PHONE.ready:
        return done  # причину показывает health(): «звонки включены, но телефония не подключена»
    if st["call_enabled"] and calls.in_window(st["call_hours"]):
        for d in calls.due_dialogs(DB, st):
            call, why = await start_call(d)
            done.append((d["id"], call, why))
            if why and (why.startswith("дневной лимит") or why.startswith("ошибка телефонии")):
                break
    return done


@asynccontextmanager
async def lifespan(app):
    try:
        ME.update(await ati.me())
        mark("ati", True)
    except Exception as e:
        mark("ati", False, f"аккаунт: {ati_err(e)}")
    for d in q("select * from dialogs where status='writing'"):  # дожать прерванные
        spawn(agent_step(d))
    await reap_stale_calls()
    tasks = [asyncio.create_task(monitor()), asyncio.create_task(poll_dialogs()), asyncio.create_task(call_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan, dependencies=[Depends(auth.make_guard(DB))],
              docs_url=None, redoc_url=None, openapi_url=None)  # схему API наружу не светим


# ---------- вход и пользователи ----------
class Login(BaseModel):
    login: str = Field(max_length=128)
    password: str = Field(max_length=128)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/login")
async def do_login(b: Login, request: Request, resp: Response):
    token, u = auth.login(DB, b.login, b.password, request.client.host if request.client else "")
    auth.set_cookie(resp, token)
    return auth.public_user(u)


@app.post("/api/logout")
async def do_logout(request: Request, resp: Response):
    auth.logout(DB, request.cookies.get(auth.COOKIE))
    resp.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me():
    return auth.public_user(auth.current())


class NewUser(BaseModel):
    login: str
    password: str
    role: str = "logist"


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@app.get("/api/users", dependencies=[Depends(auth.require_owner)])
async def users():
    return [auth.public_user(u) for u in q("select * from users order by id")]


@app.post("/api/users", dependencies=[Depends(auth.require_owner)])
async def add_user(b: NewUser):
    uid = auth.create_user(DB, b.login, b.password, b.role)
    auth.record(DB, "user_add", uid, f"{b.login} ({b.role})")
    return auth.public_user(q("select * from users where id=?", uid)[0])


@app.patch("/api/users/{uid}", dependencies=[Depends(auth.require_owner)])
async def patch_user(uid: int, b: UserPatch):
    u = q("select * from users where id=?", uid)
    if not u:
        raise HTTPException(404)
    demote = (b.role and b.role != "owner") or b.active is False
    if u[0]["role"] == "owner" and demote and not auth.owners_left(DB, uid):
        raise HTTPException(400, "Нельзя убрать последнего владельца")
    if b.role:
        auth.check_role(b.role)
    if b.password:
        auth.check_password(b.password)
    if b.role:
        DB.execute("update users set role=? where id=?", (b.role, uid))
    if b.active is not None:
        DB.execute("update users set active=? where id=?", (int(b.active), uid))
        if not b.active:
            auth.drop_sessions(DB, uid)
    if b.password:
        DB.execute("update users set password_hash=? where id=?", (auth.PH.hash(b.password), uid))
        auth.drop_sessions(DB, uid)
    auth.record(DB, "user_edit", uid, json.dumps({k: v for k, v in b.model_dump().items() if v is not None and k != "password"}
                                                 | ({"password": "сменён"} if b.password else {}), ensure_ascii=False))
    return auth.public_user(q("select * from users where id=?", uid)[0])


@app.get("/api/actions", dependencies=[Depends(auth.require_owner)])
async def actions(limit: int = Query(200, ge=1, le=1000)):
    return q("select * from actions order by ts desc limit ?", min(limit, 1000))


# ---------- API ----------
def load_row(r):
    raw = json.loads(r["raw"])
    return {**{k: r[k] for k in ("id", "truck_id", "score", "verdict", "status", "reject_reason", "first_seen")},
            "reasons": json.loads(r["reasons"]), "metrics": json.loads(r["metrics"]), "raw": raw}


LIST_TTL = 24 * 3600  # в списке — то, что биржа показывала за сутки; старое объявление скорее всего ушло
RELEVANT_NOW = "verdict='relevant' and status='new' and updated>=?"


@app.get("/api/stats")
async def stats():
    day = datetime.combine(date.today(), datetime.min.time()).timestamp()
    seen = q("select count(*) n, sum(case when verdict='relevant' then 1 else 0 end) rel from loads where first_seen>=?", day)[0]
    scans = q("select count(*) n, coalesce(sum(seen),0) s from scans where ts>=?", day)[0]
    relevant = q(f"select count(*) n from loads where {RELEVANT_NOW}", time.time() - LIST_TTL)[0]["n"]  # как список «Подходят»
    return {"seen": seen["n"], "relevant": relevant, "filtered": seen["n"] - (seen["rel"] or 0),
            "views": scans["s"], "scans": scans["n"],
            "dialogs": q("select count(*) n from dialogs where status in ('writing','waiting')")[0]["n"],
            "ready": q("select count(*) n from dialogs where status='ready'")[0]["n"],
            "rules": q("select count(*) n from rules where active=1")[0]["n"],
            "hours_saved": round(scans["s"] * 25 / 3600, 1),  # ~25 сек логиста на просмотр одного груза
            "me": ME, "log": LOG[:40], "health": health()}


BACKUP_DIR = os.environ.get("BACKUP_DIR", "")  # том бэкапов (только чтение): backup.sh пишет туда last_ok / last_error


def backup_state(now=None):
    if not BACKUP_DIR:
        return None
    now = now or time.time()
    d = Path(BACKUP_DIR)
    try:
        ok = float((d / "last_ok").read_text().strip())
    except (OSError, ValueError):
        ok = 0.0
    err = (d / "last_error").read_text().strip()[:200] if (d / "last_error").is_file() else ""
    err_ts = (d / "last_error").stat().st_mtime if err else 0
    if err and err_ts > ok:
        return f"бэкап не удался: {err}"
    if not ok:
        return "бэкапов ещё не было — проверьте сервис backup"
    if now - ok > 26 * 3600:
        return f"последний бэкап {int((now - ok) / 3600)} ч назад"
    return None


def health(now=None):
    """Что сломано или требует внимания — понятными словами, для плашки в интерфейсе."""
    now = now or time.time()
    st, out = settings(), []

    def add(part, text, level="error"):
        out.append({"part": part, "text": text, "level": level})
    a = HEALTH["ati"]
    if a["ok"] is False:
        add("ati", a["err"])
    trucks = q("select count(*) n from trucks where active=1")[0]["n"]
    last = q("select max(ts) t from scans")[0]["t"] or 0
    if trucks and last and now - last > 3 * max(60, int(st["scan_minutes"]) * 60) + 120 and a["ok"] is not False:
        add("ati", f"поиск грузов не проходил {int((now - last) / 60)} мин")
    if not os.environ.get("LLM_BASE_URL"):
        add("llm", "модель не настроена: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY в .env")
    elif HEALTH["llm"]["ok"] is False:
        add("llm", f"модель: {HEALTH['llm']['err']}")
    if PHONE.mode == "mock":
        add("phone", "телефония в режиме имитации (TELEPHONY=mock): звонки и расшифровки не настоящие", "warn")
    if st["call_enabled"] and not PHONE.ready:
        add("phone", f"звонки включены, но {PHONE.why}")
    elif HEALTH["phone"]["ok"] is False:
        add("phone", HEALTH["phone"]["err"])
    if PHONE.mode == "megafon" and not WEBHOOK_SECRET:
        add("phone", "вебхук ВАТС выключен: TELEPHONY_WEBHOOK_SECRET не задан или короче 32 символов — итоги звонков не придут")
    if not ati.CLIENT_ID_SET:
        add("ati", "ATI_CLIENT_ID не задан — запросы идут с ID интегратора", "warn")
    if b := backup_state(now):
        add("backup", b)
    if st["auto_start"] and not st["send_enabled"]:
        add("agent", "агент сам берёт грузы, но отправка выключена — только черновики", "info")
    return {"problems": out, "ati_ok": a["ok"], "ati_last_scan": last, "telephony": PHONE.mode}


@app.get("/api/loads")
async def loads(view: str = "relevant", truck: int | None = None, limit: int = Query(200, ge=1, le=1000)):
    where = {"relevant": f"{RELEVANT_NOW}", "filtered": "verdict='filtered' and status='new' and updated>=?",
             "rejected": "status='rejected'", "all": "1=1"}.get(view, "1=1")
    args = [time.time() - LIST_TTL] if view in ("relevant", "filtered") else []
    if truck:
        where += f" and truck_id={int(truck)}"
    order = "score desc, updated desc" if view == "relevant" else "updated desc"
    return [load_row(r) for r in q(f"select * from loads where {where} order by {order} limit ?", *args, limit)]


class Reason(BaseModel):
    reason: str
    learn: bool = True


@app.post("/api/loads/{lid}/reject")
async def reject(lid: str, b: Reason):
    l = q("select * from loads where id=?", lid)
    if not l:
        raise HTTPException(404)
    DB.execute("update loads set status='rejected', reject_reason=? where id=?", (b.reason, lid))
    who = auth.record(DB, "load_reject", lid, b.reason)
    log(f"{who}: отказ по грузу — {b.reason or 'без причины'}")
    rule = None
    if b.learn and b.reason.strip():
        try:
            rule = await add_rule_from_text(b.reason, json.loads(l[0]["raw"]), json.loads(l[0]["metrics"]), lid)
        except Exception as e:  # отказ уже записан; правило можно добавить позже на странице «База знаний»
            log(f"Правило из причины отказа не создано: {e}", "error")
            return {"rule": None, "error": f"Отказ сохранён, но правило не создано: {e}"}
        auth.record(DB, "rule_add", rule["id"], rule["text"])
    return {"rule": rule}


async def add_rule_from_text(text, load=None, metrics=None, lid=None):
    r = await agent.learn_rule(text, load, metrics)
    cur = DB.execute("insert into rules(match,value,block,delta,text,source,load_id,created) values(?,?,?,?,?,?,?,?)",
                     (r["match"], r["value"].lower(), int(r["block"]), r["delta"], r["text"], text, lid, time.time()))
    rescore()
    log(f"База знаний пополнена: {r['text']}", "learn")
    return {**r, "id": cur.lastrowid}


class Goal(BaseModel):
    goal: str | None = Field(None, max_length=1500)


@app.post("/api/loads/{lid}/agent")
async def start_agent(lid: str, b: Goal):
    l = q("select * from loads where id=?", lid)
    if not l:
        raise HTTPException(404)
    auth.record(DB, "agent_start", lid, b.goal)
    try:
        return await start_dialog(l[0], b.goal or settings()["default_goal"])
    except ValueError as e:
        raise HTTPException(400, str(e))


async def start_dialog(l, goal, by_agent=False):
    goal = str(goal or "").replace("{min_rate_km}", str(settings()["min_rate_km"]))  # в карточке — число, не шаблон
    if q("select 1 from dialogs where load_id=? and status in ('writing','waiting','ready')", l["id"]):
        raise ValueError("По этому грузу агент уже ведёт переговоры")
    raw = json.loads(l["raw"])
    firm = raw.get("firm") or {}
    if not firm.get("contacts"):
        raise ValueError("У груза скрыт заказчик — написать некому")
    c = firm["contacts"][0]
    try:
        chat_id = await ati.open_chat(f"{firm['id']}.{c['id']}", c["name"], firm.get("name", ""))
    except Exception as e:
        if not calls.phone_of(raw):
            raise ValueError(f"Чат с заказчиком не открылся ({e}), телефона нет") from e
        # чата нет, но есть телефон — диалог «только звонок», call_loop позвонит без ожидания ответа в чате
        mark("ati", False, f"чат не открылся: {ati_err(e)}")
        cur = DB.execute("insert into dialogs(load_id,chat_id,goal,status,summary,created,updated,by_agent) values(?,?,?,?,?,?,?,?)",
                         (l["id"], None, goal, "waiting", f"Чат не открылся ({ati_err(e)}) — позвоним по телефону",
                          time.time(), time.time(), int(by_agent)))
        DB.execute("update loads set status='dialog' where id=?", (l["id"],))
        log(f"Чат с {firm.get('name')} не открылся ({e}) — поставили звонок", "agent")
        return q("select * from dialogs where id=?", cur.lastrowid)[0]
    cur = DB.execute("insert into dialogs(load_id,chat_id,goal,status,summary,created,updated,by_agent) values(?,?,?,?,?,?,?,?)",
                     (l["id"], chat_id, goal, "writing",
                      "Агент сам выбрал груз и готовит вопросы" if by_agent else "Агент готовит первое сообщение",
                      time.time(), time.time(), int(by_agent)))
    DB.execute("update loads set status='dialog' where id=?", (l["id"],))
    d = q("select * from dialogs where id=?", cur.lastrowid)[0]
    spawn(agent_step(d))
    return d


AUTO_LOCK = asyncio.Lock()
FRESH_SCANS = 3  # груз, который биржа не показывала дольше 3 проходов, считаем ушедшим


def fresh_edge(st=None):
    st = st or settings()
    return time.time() - FRESH_SCANS * max(60, int(st["scan_minutes"]) * 60)


async def auto_start():
    async with AUTO_LOCK:  # фон и кнопка «Взять сейчас» не должны взять один груз дважды
        await _auto_start()


async def _auto_start():
    """Агент сам берёт лучшие свежие грузы и начинает переписку."""
    st = settings()
    if not (st["auto_start"] and st["send_enabled"]):
        return
    day = datetime.combine(date.today(), datetime.min.time()).timestamp()
    left = st["auto_daily"] - q("select count(*) n from dialogs where created>=? and by_agent=1", day)[0]["n"]
    busy_firms = {(json.loads(r["raw"]).get("firm") or {}).get("id") for r in
                  q("select l.raw from dialogs d join loads l on l.id=d.load_id where d.status in ('writing','waiting','ready')")}
    started = 0
    where, args = fleet.scan_filter(alias="t")
    for l in q(f"select l.* from loads l join trucks t on t.id=l.truck_id where l.verdict='relevant' and l.status='new'"
               f" and l.score>=? and l.updated>=? and {where} order by l.score desc, l.updated desc", st["auto_score"], fresh_edge(st), *args):
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
    st = settings()
    if not st["send_enabled"]:
        return {"started": 0, "reason": "Отправка сообщений выключена в настройках — агент только готовит черновики"}
    if not st["auto_start"]:
        return {"started": 0, "reason": "«Агент сам берёт лучшие грузы» выключено в настройках"}
    day = datetime.combine(date.today(), datetime.min.time()).timestamp()
    if q("select count(*) n from dialogs where created>=? and by_agent=1", day)[0]["n"] >= st["auto_daily"]:
        return {"started": 0, "reason": f"Дневной лимит новых диалогов ({st['auto_daily']}) исчерпан"}
    before = q("select count(*) n from dialogs")[0]["n"]
    await auto_start()
    n = q("select count(*) n from dialogs")[0]["n"] - before
    return {"started": n, "reason": None if n else f"Нет свежих грузов с оценкой от {st['auto_score']} и открытым заказчиком"}


@app.get("/api/dialogs")
async def dialogs():
    out = []
    for d in q("select * from dialogs order by updated desc"):
        l = q("select * from loads where id=?", d["load_id"])
        raw = json.loads(l[0]["raw"]) if l else {}
        out.append({**d, "facts": json.loads(d["facts"] or "{}"), "load": load_row(l[0]) if l else None,
                    "messages": q("select * from messages where dialog_id=? order by ts", d["id"]),
                    "calls": q("select * from calls where dialog_id=? order by created", d["id"]),
                    "phone": calls.phone_of(raw)})
    return out


class Text(BaseModel):
    text: str


@app.post("/api/dialogs/{did}/send")
async def send_manual(did: int, b: Text):
    d = q("select * from dialogs where id=?", did)
    if not d:
        raise HTTPException(404)
    d = d[0]
    if not d["chat_id"]:
        raise HTTPException(400, "Чата с заказчиком нет — только звонок")
    try:
        msg = await ati.send(d["chat_id"], b.text)
    except httpx.HTTPError as e:
        mark("ati", False, f"отправка сообщения: {ati_err(e)}")
        raise HTTPException(502, ati_err(e)) from e
    DB.execute("insert or ignore into messages(id,dialog_id,mine,text,ts) values(?,?,?,?,?)", (msg["id"], did, 1, b.text, msg["ts"]))
    DB.execute("update dialogs set last_ts=max(last_ts,?), updated=?, draft=null where id=?", (msg["ts"], time.time(), did))
    auth.record(DB, "message_send", did, b.text[:500])
    return {"ok": True}


# ---------- тестовый голосовой звонок: логист играет заказчика, агент говорит по сценарию диалога ----------
VOICE_LIVE = set()
VOICE_MAX_LIVE = 2
VOICE_END = {"hangup": "Звонок завершён.", "bye": "Агент попрощался — звонок завершён.", "limit": "Время тестового звонка вышло.",
             "idle": "Звонок завершён: вас не слышно.", "error": "Связь с голосовой моделью прервалась."}


@app.websocket("/api/voice/test/{did}")
async def voice_test(ws: WebSocket, did: int):
    u = auth.user_by_token(DB, ws.cookies.get(auth.COOKIE))
    origin = ws.headers.get("origin")
    if not u or (origin and urlparse(origin).netloc != ws.headers.get("host")):  # чужой сайт не позвонит от имени логиста
        await ws.close(code=1008)
        return
    auth.CURRENT.set(u)
    await ws.accept()

    async def end(text, **extra):
        try:
            await ws.send_json({"type": "end", "text": text, **extra})
            await ws.close()
        except Exception:
            pass

    c = voice.cfg()
    d = q("select * from dialogs where id=?", did)
    if not c["ready"]:
        return await end("Голос не настроен: в .env нужны YC_API_KEY и YC_FOLDER_ID.")
    if not d:
        return await end("Диалог не найден.")
    spent = q("select coalesce(sum(cost),0) rub from voice_usage where ts>=?", calls.msk_day_start())[0]["rub"]
    allow = (min(c["call_sec"], int((c["rub_day"] - spent) / c["rub_sec"] / (len(VOICE_LIVE) + 1)))  # остаток делим на идущие
             if c["rub_sec"] > 0 else c["call_sec"])
    if allow < 30:
        return await end(f"Дневной лимит на тестовые звонки ({c['rub_day']:g} ₽) исчерпан.")
    if did in VOICE_LIVE or len(VOICE_LIVE) >= VOICE_MAX_LIVE:
        return await end("Сейчас уже идёт тестовый звонок — попробуйте через минуту.")
    instructions, hello = instructions_for_call(d[0]), greeting_for_call(d[0])

    async def recv():
        try:
            msg = await ws.receive()
        except Exception:
            return None
        if msg.get("type") == "websocket.disconnect":
            return None
        try:
            return json.loads(msg.get("text") or "")
        except ValueError:
            return {}  # бинарный кадр или мусор — пропускаем, звонок не роняем

    async def send(ev):
        try:
            await ws.send_json(ev)
        except Exception:
            pass

    VOICE_LIVE.add(did)
    reason, turns, why, t0 = "error", [], "", None

    async def send(ev, _send=send):
        nonlocal t0
        t0 = t0 or time.monotonic()  # оплата — с первого ответа Yandex, а не с попытки подключиться
        await _send(ev)
    try:
        reason, turns, _ = await voice.talk(recv, send, instructions, {**c, "call_sec": allow}, greeting=hello)
    except Exception as e:
        why = voice.explain(e)
        log(f"Тестовый звонок по диалогу #{did}: {why}", "error")
    finally:
        VOICE_LIVE.discard(did)
        secs = time.monotonic() - t0 if t0 else 0.0  # считаем сами: оборвался исключением — всё равно оплачен
        rub = round(secs * c["rub_sec"], 2)
        DB.execute("insert into voice_usage values(?,?,?,?,?,?)", (time.time(), did, secs, rub, u["login"], reason))
        auth.record(DB, "voice_test", did, f"{secs:.0f} с, {rub} ₽, {reason}")
    # расшифровку в переписку НЕ кладём: логист играл заказчика, это не слова настоящего заказчика
    text = f"Голосовой звонок не состоялся: {why}" if why else VOICE_END.get(reason, "Звонок завершён.")
    await end(text, transcript=voice.transcript(turns), seconds=round(secs), rub=rub)


DIALOG_STATUSES = ("writing", "waiting", "ready", "declined", "taken")


@app.post("/api/dialogs/{did}/call")
async def call_now(did: int):
    d = q("select * from dialogs where id=?", did)
    if not d:
        raise HTTPException(404)
    call, why = await start_call(d[0], manual=True)
    if why:
        raise HTTPException(502 if call else 400, f"Не звоним: {why}")
    auth.record(DB, "call_now", did, call["phone"])
    return call


@app.get("/api/calls/{cid}/recording")
async def recording(cid: int):
    f = RECORDINGS / f"{cid}.mp3"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="audio/mpeg")


class Phone(BaseModel):
    phone: str
    reason: str = ""


@app.get("/api/dnc")
async def dnc_list():
    return q("select * from do_not_call order by created desc")


@app.post("/api/dnc")
async def dnc_add(b: Phone):
    p = calls.norm_phone(b.phone)
    if not p:
        raise HTTPException(400, "Не похоже на российский номер")
    who = auth.record(DB, "dnc_add", p, b.reason)
    DB.execute("insert or replace into do_not_call values(?,?,?,?)", (p, b.reason or "вручную", time.time(), who))
    return {"phone": p}


@app.delete("/api/dnc/{phone}")
async def dnc_del(phone: str):
    DB.execute("delete from do_not_call where phone=?", (calls.norm_phone(phone) or phone,))
    auth.record(DB, "dnc_delete", phone)
    return {"ok": True}


@app.post("/api/telephony/webhook/{secret}")
async def telephony_webhook(secret: str, request: Request):
    """Вебхук ВАТС (form, cmd=history) и голосового бота (JSON: call_id, status, transcript, duration)."""
    if not WEBHOOK_SECRET or not hmac.compare_digest(secret.encode(), WEBHOOK_SECRET.encode()):
        raise HTTPException(403)
    if "json" in request.headers.get("content-type", ""):
        try:
            b = await request.json()
        except ValueError:
            raise HTTPException(400, "невалидный JSON")
        if not isinstance(b, dict):
            raise HTTPException(400, "ожидается объект")
        key = b.get("call_id") or b.get("provider_id")  # бот адресует нашим call_id, ВАТС — своим id
        c = await apply_call_result(key, b, by_provider=not b.get("call_id"), background=True) if key else None
    else:
        pid, res = telephony.parse_megafon(dict(await request.form()))
        c = await apply_call_result(pid, res, by_provider=True, background=True) if pid else None
    return {"ok": True, "matched": bool(c)}


class DStatus(BaseModel):
    status: str | None = None
    auto: bool | None = None


@app.patch("/api/dialogs/{did}")
async def patch_dialog(did: int, b: DStatus):
    d = q("select * from dialogs where id=?", did)
    if not d:
        raise HTTPException(404)
    truck = None
    if b.status:
        if b.status not in DIALOG_STATUSES or b.status == "writing":
            raise HTTPException(400, f"Статус должен быть одним из {[s for s in DIALOG_STATUSES if s != 'writing']}")
        DB.execute("update dialogs set status=?, updated=? where id=?", (b.status, time.time(), did))
        lid = d[0]["load_id"]
        DB.execute("update loads set status=? where id=?", ({"taken": "taken", "declined": "declined"}.get(b.status, "dialog"), lid))
        who = auth.record(DB, f"dialog_{b.status}", did)
        if b.status in ("taken", "declined"):
            log(f"{who}: {'взяли груз' if b.status == 'taken' else 'отказались'} (диалог #{did})", "info")
        if b.status == "taken" and d[0]["status"] != "taken":
            truck = await occupy_truck(lid)
        elif d[0]["status"] == "taken" and b.status != "taken":
            log(f"{who}: диалог #{did} больше не «взяли» — проверьте статус и дату машины вручную", "error")
    if b.auto is not None:
        DB.execute("update dialogs set auto=? where id=?", (int(b.auto), did))
        auth.record(DB, "dialog_auto_on" if b.auto else "dialog_auto_off", did)
    return {"ok": True, "truck": truck}


async def occupy_truck(lid):
    """«Взяли груз»: машина занята до выгрузки, освободится в городе выгрузки."""
    l = q("select * from loads where id=?", lid)
    if not l or not l[0]["truck_id"]:
        return None
    t = q("select * from trucks where id=?", l[0]["truck_id"])[0]
    u = fleet.busy_until(json.loads(l[0]["raw"]), json.loads(l[0]["metrics"] or "{}"))
    city_name = u["city_name"] or t["city_name"]
    city_id, lat, lon = u["city_id"], u["lat"], u["lon"]
    if city_name != t["city_name"] and not city_id:
        try:
            c = await city(city_name)
            city_id, lat, lon = c["CityId"], lat or c["Latitude"], lon or c["Longitude"]
        except Exception as e:  # справочник недоступен — логист поправит город руками
            log(f"Не нашёл город выгрузки «{city_name}» в справочнике АТИ: {e}", "error")
    t = q("select * from trucks where id=?", t["id"])[0]  # пока ходили в справочник, машину могли поправить
    if not city_id:  # город выгрузки не определили — оставляем текущее место целиком, логист поправит
        city_name, city_id, lat, lon = t["city_name"], t["city_id"], t["lat"], t["lon"]
    free_from = normalize_free_from("busy", u["free_from"])
    DB.execute("update trucks set status='busy', city_name=?, city_id=?, lat=?, lon=?, free_from=? where id=?",
               (city_name, city_id, lat if lat is not None else t["lat"], lon if lon is not None else t["lon"],
                free_from, t["id"]))
    rescore()
    log(f"{t['name']} занята до {free_from}, освободится в {city_name}", "info")
    return q("select * from trucks where id=?", t["id"])[0]


@app.get("/api/trucks")
async def trucks():
    if fleet.release_due(DB):
        rescore()
    return q("select t.*, (select count(*) from loads l where l.truck_id=t.id and l.verdict='relevant' and l.status='new'"
             " and l.updated>=?) relevant"
             " from trucks t order by id", time.time() - LIST_TTL)  # как в списке «Подходят»


class Truck(BaseModel):
    name: str
    driver: str = ""
    city_name: str
    radius: int = Field(150, ge=10, le=2000)
    body_mask: int = Field(1, ge=1)
    body_name: str = "тент"
    height: float = Field(2.7, gt=0, le=5)
    capacity: float = Field(20, gt=0, le=60)
    free_from: date
    active: bool = True
    status: str = "free"


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
    check_status(b.status)
    st, ff = form_status(b.status, b.free_from.isoformat())
    DB.execute("insert into trucks(name,driver,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,free_from,active,status)"
               " values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (b.name, b.driver, c["CityName"], c["CityId"], c["Latitude"], c["Longitude"], b.radius, b.body_mask,
                b.body_name, b.height, b.capacity, ff, int(b.active), st))
    auth.record(DB, "truck_add", None, b.name)
    return {"ok": True}


@app.put("/api/trucks/{tid}")
async def edit_truck(tid: int, b: Truck):
    t = q("select * from trucks where id=?", tid)
    if not t:
        raise HTTPException(404)
    t = t[0]
    c = {"CityName": t["city_name"], "CityId": t["city_id"], "Latitude": t["lat"], "Longitude": t["lon"]}
    if b.city_name != t["city_name"]:
        c = await city(b.city_name)
    check_status(b.status)
    st, ff = form_status(b.status, b.free_from.isoformat())
    DB.execute("update trucks set name=?,driver=?,city_name=?,city_id=?,lat=?,lon=?,radius=?,body_mask=?,body_name=?,"
               "height=?,capacity=?,free_from=?,active=?,status=? where id=?",
               (b.name, b.driver, c["CityName"], c["CityId"], c["Latitude"], c["Longitude"], b.radius, b.body_mask,
                b.body_name, b.height, b.capacity, ff, int(b.active), st, tid))
    auth.record(DB, "truck_edit", tid, b.name)
    rescore()
    return {"ok": True}


def normalize_free_from(status, free_from):
    """free — не позже сегодня; busy — хотя бы завтра, иначе release_due тут же снимет занятость."""
    today = date.today()
    if status == "free" and (free_from or "") > today.isoformat():
        return today.isoformat()
    if status == "busy" and (free_from or "") <= today.isoformat():
        return (today + timedelta(days=1)).isoformat()
    return free_from


def form_status(status, free_from):
    """Форма машины: дата освобождения в будущем = машина занята до неё."""
    if (free_from or "") > date.today().isoformat():
        return "busy", free_from
    return status, normalize_free_from(status, free_from)


def check_status(s):
    if s not in ("free", "busy"):
        raise HTTPException(400, "Статус машины: free или busy")


class TruckStatus(BaseModel):
    status: str


@app.patch("/api/trucks/{tid}")
async def set_truck_status(tid: int, b: TruckStatus):
    """Быстрый переключатель на карточке. «Свободна» = свободна сегодня, если дата освобождения была в будущем."""
    check_status(b.status)
    t = q("select * from trucks where id=?", tid)
    if not t:
        raise HTTPException(404)
    free_from = normalize_free_from(b.status, t[0]["free_from"])
    DB.execute("update trucks set status=?, free_from=? where id=?", (b.status, free_from, tid))
    auth.record(DB, f"truck_{b.status}", tid)
    rescore()
    return q("select * from trucks where id=?", tid)[0]


@app.post("/api/trucks/{tid}/scan")
async def scan_now(tid: int):
    t = q("select * from trucks where id=?", tid)
    if not t:
        raise HTTPException(404)
    try:
        await scan_truck(t[0])
    except httpx.HTTPError as e:
        mark("ati", False, f"поиск грузов: {ati_err(e)}")
        raise HTTPException(502, ati_err(e)) from e
    return {"ok": True}


@app.get("/api/rules")
async def rules():
    return q("select * from rules order by created desc")


@app.post("/api/rules")
async def add_rule(b: Text):
    try:
        r = await add_rule_from_text(b.text)
    except llm.LLMError as e:  # модель недоступна — говорим об этом, а не «Ошибка 500»
        mark("llm", False, e)
        raise HTTPException(503, f"Модель не ответила, правило не добавлено: {e}") from e
    auth.record(DB, "rule_add", r["id"], r["text"])
    return r


class RulePatch(BaseModel):
    active: bool


@app.patch("/api/rules/{rid}")
async def patch_rule(rid: int, b: RulePatch):
    DB.execute("update rules set active=? where id=?", (int(b.active), rid))
    auth.record(DB, "rule_on" if b.active else "rule_off", rid)
    rescore()
    return {"ok": True}


@app.delete("/api/rules/{rid}")
async def del_rule(rid: int):
    DB.execute("delete from rules where id=?", (rid,))
    auth.record(DB, "rule_delete", rid)
    rescore()
    return {"ok": True}


@app.get("/api/settings")
async def get_settings():
    u = auth.current()
    if u and u["role"] != "owner":  # логист настроек не видит — только цель агента для запуска диалога
        s = settings()
        return {**{k: s[k] for k in ("default_goal", "goal_presets", "send_enabled", "auto_start", "call_enabled")},
                "voice_ready": voice.cfg()["ready"], "default_goal_text": goal_text(s)}
    s = settings()
    return {**s, "voice_ready": voice.cfg()["ready"], "default_goal_text": goal_text(s)}


def goal_text(s):
    """Сценарий по умолчанию с подставленной нормой ₽/км — так его видит логист в окне «Поручить агенту»."""
    return str(s["default_goal"]).replace("{min_rate_km}", str(s["min_rate_km"]))


@app.get("/api/costs", dependencies=[Depends(auth.require_owner)])
async def costs():
    return costs_today()


# пустое поле на форме не должно превращаться в 0 (радиус 0, звонок сразу, 0 страниц)
NUM_RANGES = {"vat": (0, 30), "min_rate_km": (1, 1000), "threshold": (0, 100), "scan_minutes": (1, 1440), "pages": (1, 20),
              "auto_score": (0, 100), "auto_per_cycle": (1, 50), "auto_daily": (0, 500),
              "call_after_min": (1, 1440), "call_daily": (0, 1000)}


@app.put("/api/settings", dependencies=[Depends(auth.require_owner)])
async def put_settings(b: dict):
    if "call_hours" in b and not calls.valid_hours(b["call_hours"]):
        raise HTTPException(400, "Окно звонков — в виде 09:00-19:00")
    for k, (lo, hi) in NUM_RANGES.items():
        if k in b:
            v = b[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise HTTPException(400, f"«{k}»: число от {lo} до {hi}")
            if isinstance(DEFAULTS[k], int):
                b[k] = int(v)
    for k in ("send_enabled", "auto_start", "call_enabled"):
        if k in b and not isinstance(b[k], bool):
            raise HTTPException(400, f"«{k}»: да/нет")
    if "goal_presets" in b:
        g = b["goal_presets"]
        if not isinstance(g, list) or not all(isinstance(x, str) for x in g):
            raise HTTPException(400, "Сценарии — список строк")
        g = [x.strip() for x in g if x.strip()]
        if len(g) > 20 or any(len(x) > 1000 for x in g):
            raise HTTPException(400, "Сценариев не больше 20, каждый до 1000 символов")
        b["goal_presets"] = g
    if "default_goal" in b and not (isinstance(b["default_goal"], str) and 0 < len(b["default_goal"].strip()) <= 1500):
        raise HTTPException(400, "Сценарий по умолчанию: от 1 до 1500 символов")
    for k, v in b.items():
        if k in DEFAULTS:
            DB.execute("insert into kv values(?,?) on conflict(k) do update set v=excluded.v", (k, json.dumps(v)))
    auth.record(DB, "settings", None, json.dumps({k: v for k, v in b.items() if k in DEFAULTS}, ensure_ascii=False)[:1000])
    rescore()
    return settings()


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def api_not_found(path: str):
    """Неизвестный /api/* — 404, но только после проверки входа (как и всё под /api), собран фронт или нет."""
    raise HTTPException(404)


DIST = ROOT.parent / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (DIST / path).resolve()
        if path and f.is_file() and f.is_relative_to(DIST.resolve()):  # иконка и прочее из web/public
            return FileResponse(f)
        # index.html не кэшируем: после обновления браузер сразу получит новые бандлы (у них хэш в имени)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
