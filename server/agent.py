"""ИИ-агент на Codex CLI: ведёт переписку с заказчиком и превращает объяснения логиста в правила."""
import asyncio
import json
import os
import tempfile

NEGOTIATE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "status", "facts", "summary"],
    "properties": {
        "reply": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": ["waiting", "ready", "declined"]},
        "summary": {"type": "string"},
        "facts": {"type": "object", "additionalProperties": False,
                  "required": ["actual", "price", "payment", "address", "time", "other"],
                  "properties": {k: {"type": ["string", "null"]} for k in
                                 ["actual", "price", "payment", "address", "time", "other"]}},
    },
}

RULE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["match", "value", "block", "delta", "text"],
    "properties": {
        "match": {"type": "string", "enum": ["firm_id", "firm_name", "cargo", "load_city",
                                             "unload_city", "note", "profile", "none"]},
        "value": {"type": "string"},
        "block": {"type": "boolean"},
        "delta": {"type": "integer"},
        "text": {"type": "string"},
    },
}


async def codex(prompt, schema):
    with tempfile.TemporaryDirectory() as d:
        sp, out = os.path.join(d, "s.json"), os.path.join(d, "out.json")
        with open(sp, "w") as f:
            json.dump(schema, f)
        p = await asyncio.create_subprocess_exec(
            "codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
            "-C", d, "--output-schema", sp, "-o", out, "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(p.communicate(prompt.encode()), 240)
        if p.returncode:
            raise RuntimeError(err.decode()[-500:])
        with open(out) as f:
            return json.load(f)


def describe(load, m):
    ld, un, c, firm = load["loading"], load["unloading"], load["load"], load.get("firm") or {}
    price = f"{m['gross']} ₽ ({m['money']}; чистыми {m['net']} ₽)" if m.get("gross") else "запрос ставки"
    return (f"Груз #{load.get('loadNumber')}: {ld['location']['city']} ({ld['location'].get('street') or ''}) → "
            f"{un['location']['city']} ({un['location'].get('street') or ''}), {m['dist']} км, "
            f"погрузка {(ld.get('firstDate') or '')[:10]}, {c.get('cargoType')}, {c.get('weight')} т, "
            f"ставка: {price}. Заказчик: {firm.get('name', 'скрыт')}. Примечание: {load.get('note') or '—'}")


async def negotiate(load, metrics, truck, goal, chat, notes, me_name):
    convo = "\n".join(f"{'МЫ' if x['mine'] else 'ЗАКАЗЧИК'}: {x['text']}" for x in chat) or "(переписки ещё нет)"
    prompt = f"""Ты — логист перевозчика ({me_name}) на бирже грузоперевозок АТИ. Пишешь заказчику в мессенджер АТИ от лица логиста.
Пиши коротко, по-деловому, по-русски, как живой логист: без «я ИИ», без канцелярита, 1–3 предложения. Не выдумывай факты о машине сверх данных ниже.

Наша машина: {truck['name']}, {truck['body_name']}, {truck['capacity']} т, высота кузова {truck['height']} м, свободна в {truck['city_name']} с {truck['free_from']}.
{describe(load, metrics)}
Цель переговоров: {goal}
Знания и предпочтения компании: {notes or '—'}

Переписка:
{convo}

Реши следующий шаг:
- reply: следующее сообщение заказчику (null, если писать сейчас нечего, например ждём ответа на наш последний вопрос).
- status: "ready" — цель достигнута, логисту остаётся позвонить и подтвердить; "declined" — груз уже ушёл или условия точно не подходят; иначе "waiting".
- facts: что уже известно (актуальность, итоговая ставка, форма оплаты, точный адрес, время погрузки, прочее); null — если неизвестно.
- summary: одна строка для логиста, что сейчас происходит."""
    return await codex(prompt, NEGOTIATE_SCHEMA)


async def learn_rule(reason, load=None, metrics=None):
    ctx = describe(load, metrics) if load else "(без привязки к грузу)"
    if load and load.get("firm"):
        ctx += f" Код фирмы: {load['firm'].get('id')}. Профиль: {load['firm'].get('profile')}."
    prompt = f"""Логист объяснил, почему груз не подходит (или дал общее правило). Преврати это в одно правило фильтрации грузов.
Груз: {ctx}
Объяснение логиста: «{reason}»

Поля: match — по чему срабатывает: firm_id (конкретная фирма, value=код), firm_name, cargo (тип груза, подстрока),
load_city / unload_city (город, подстрока), note (подстрока примечания), profile ('эксп' для экспедиторов), none — только если ни одно поле не подходит.
Если в объяснении есть город, тип груза или фирма — правило всегда по этому полю; условия вроде «зимой» или «обычно» превращай в штраф (block=false, delta от -30 до -15), а не в заметку.
value — что искать (в нижнем регистре). block — true, если такие грузы вообще не показывать; иначе false и delta — штраф/бонус к оценке от -30 до +20.
text — короткая формулировка правила для базы знаний, от лица компании (например: «Не возим кирпич — бьётся тент»)."""
    return await codex(prompt, RULE_SCHEMA)
