"""Сравнение моделей для агента (ТЗ 2.1): не выдумывает факты, верно ставит ready/declined, время и токены.

Модель берётся из .env (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, LLM_JSON_MODE) — для 2–3 моделей запустить 2–3 раза
с разными .env и сравнить отчёты:

  uv run python eval_models.py --label yandexgpt              # сценарии из eval_scenarios.json
  uv run python eval_models.py --label deepseek --from-db     # + реальные диалоги из базы (итог проставил логист)

Отчёт — eval-<label>.md рядом.
"""
import argparse
import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

import agent
import llm

ROOT = Path(__file__).parent
FACTS = ["actual", "price", "payment", "address", "time", "other"]


def mark_calls(chat):
    """Расшифровка звонка отмечается флагом call (как в main.prompt_inputs), а не по тексту."""
    return [{**x, "call": bool(x.get("call")) or str(x.get("text", "")).startswith(agent.CALL_MARK)} for x in chat]


def scenarios_from_file():
    return [{**s, "chat": mark_calls(s["chat"])} for s in json.loads((ROOT / "eval_scenarios.json").read_text())]


def scenarios_from_db():
    """Реальные диалоги, где логист сам поставил итог: taken → ждём ready, declined → declined."""
    db = sqlite3.connect(Path(os.environ.get("DATA_DIR", ROOT)) / "data.db")
    db.row_factory = sqlite3.Row
    out = []
    for d in db.execute("select * from dialogs where status in ('taken','declined')"):
        l = db.execute("select * from loads where id=?", (d["load_id"],)).fetchone()
        t = db.execute("select * from trucks where id=?", (l["truck_id"],)).fetchone() if l else None
        msgs = db.execute("select * from messages where dialog_id=? order by ts", (d["id"],)).fetchall()
        if not (l and t and msgs):
            continue
        out.append({"name": f"db#{d['id']}", "load": json.loads(l["raw"]), "metrics": json.loads(l["metrics"] or "{}"),
                    "truck": dict(t), "goal": d["goal"],
                    "chat": [{"mine": bool(m["mine"]), "call": m["kind"] == "call",
                              "text": f"{agent.CALL_MARK} {m['text']}" if m["kind"] == "call" else m["text"]} for m in msgs],
                    "expect_status": ["ready", "waiting"] if d["status"] == "taken" else ["declined"]})
    return out


def check(sc, res):
    """-> список замечаний (пусто = сценарий пройден)."""
    issues = []
    want = sc.get("expect_status")
    if want and res["status"] not in want:
        issues.append(f"status={res['status']}, ждали {'/'.join(want)}")
    for k in sc.get("expect_null", []):  # чего в переписке не было — не должно появиться в фактах
        if res["facts"].get(k):
            issues.append(f"выдумал {k}: «{res['facts'][k]}»")
    for k, needle in (sc.get("expect_facts") or {}).items():  # "*" — факт в любом поле
        got = " ".join(str(v) for v in res["facts"].values() if v) if k == "*" else (res["facts"].get(k) or "")
        if needle.lower() not in got.lower():
            issues.append(f"{k} без «{needle}»: «{res['facts'].get(k)}»")
    reply = (res.get("reply") or "").lower()
    if sc.get("expect_reply") is True and not reply:
        issues.append("нет сообщения заказчику")
    if sc.get("expect_reply") is False and reply:
        issues.append(f"пишет, хотя надо ждать: «{res['reply']}»")
    for bad in sc.get("forbid_in_reply", []):  # агент ничего не подтверждает от имени компании
        if bad.lower() in reply:
            issues.append(f"в ответе запрещённое «{bad}»: «{res['reply']}»")
    return issues


async def run(label, use_db):
    scs = scenarios_from_file() + (scenarios_from_db() if use_db else [])
    rows, ok, total_t = [], 0, 0.0
    c = llm.cfg()
    for sc in scs:
        t = time.time()
        try:
            res = await agent.negotiate(sc["load"], sc["metrics"], sc["truck"], sc["goal"], sc["chat"], sc.get("notes", ""), "Иван")
            issues = check(sc, res)
        except Exception as e:
            res, issues = None, [f"ошибка: {e}"]
        dt = time.time() - t
        total_t += dt
        ok += not issues
        rows.append((sc["name"], dt, issues, res))
        print(f"{'✓' if not issues else '✗'} {sc['name']} ({dt:.1f} с) {'; '.join(issues)}")
    lines = [f"# Модель: {label}", "", f"`{c['model']}` @ `{c['base_url']}`, режим {c['json_mode']}", "",
             f"**Пройдено {ok} из {len(scs)}**, среднее время {total_t / max(len(scs), 1):.1f} с", "",
             "| Сценарий | Время | Итог |", "|---|---|---|"]
    for name, dt, issues, _ in rows:
        lines.append(f"| {name} | {dt:.1f} с | {'✓' if not issues else '✗ ' + '; '.join(issues).replace('|', '/')} |")
    lines += ["", "## Ответы", ""]
    for name, _, _, res in rows:
        lines.append(f"- **{name}**: `{json.dumps(res, ensure_ascii=False)}`")
    out = ROOT / f"eval-{label}.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"\nПройдено {ok}/{len(scs)}. Отчёт: {out}")
    return ok, len(scs)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--label", default=os.environ.get("LLM_MODEL", "model").replace("/", "_").replace(":", "_"))
    p.add_argument("--from-db", action="store_true", help="добавить реальные диалоги из data.db")
    a = p.parse_args()
    asyncio.run(run(a.label, a.from_db))
