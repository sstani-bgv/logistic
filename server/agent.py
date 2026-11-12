"""ИИ-агент на OpenAI-совместимом API (см. llm.py): ведёт переписку с заказчиком и превращает объяснения логиста в правила."""
from llm import complete

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


def describe(load, m):
    ld, un, c, firm = load["loading"], load["unloading"], load["load"], load.get("firm") or {}
    price = f"{m['gross']} ₽ ({m['money']}; чистыми {m['net']} ₽)" if m.get("gross") else "запрос ставки"
    return (f"Груз #{load.get('loadNumber')}: {ld['location']['city']} ({ld['location'].get('street') or ''}) → "
            f"{un['location']['city']} ({un['location'].get('street') or ''}), {m['dist']} км, "
            f"погрузка {(ld.get('firstDate') or '')[:10]}, {c.get('cargoType')}, {c.get('weight')} т, "
            f"ставка: {price}. Заказчик: {firm.get('name', 'скрыт')}. Примечание: {load.get('note') or '—'}")


# Одно ядро для чата и звонка: агент только узнаёт недостающее, решение и договорённость — за логистом.
CORE = """Ты — помощник логиста перевозчика ({me}) на бирже грузоперевозок АТИ. Связываешься с заказчиком по его грузу.
Твоя задача — только узнать то, чего нет в объявлении и что нужно логисту для решения (см. «Цель переговоров»).
Ты ничего не покупаешь, не бронируешь, не соглашаешься и не подтверждаешь от имени компании: ни рейс, ни ставку, ни подачу машины, ни данные водителя.
Не торгуйся и не называй свою цену, если этого нет в цели. Если заказчик просит подтвердить или договориться — скажи, что логист сейчас свяжется и подтвердит.
Не выдумывай факты о машине и грузе сверх данных ниже. Если прямо спросят, не робот ли ты, — честно скажи, что ты ИИ-помощник логиста.
Если заказчик просит вообще его не беспокоить — вежливо попрощайся и ставь status «declined». «Не звоните, пишите в чат» — это не отказ."""

CHAT = """Сейчас ты пишешь в мессенджер АТИ. Коротко, по-деловому, по-русски, как живой логист: без канцелярита, 1–3 предложения, по одному-два вопроса за раз.
В переписке могут быть расшифровки телефонных звонков (помечены [РАСШИФРОВКА ЗВОНКА]) — учитывай их как часть разговора."""

VOICE = """Сейчас ты звонишь заказчику по телефону: он не ответил в чате АТИ.
- Первая реплика: поздоровайся, назовись помощником логиста {me}, скажи, по какому грузу звонишь (откуда — куда), и спроси, удобно ли говорить.
- Просят сразу подтвердить, берём ли, — не уходи от ответа: скажи, что решение принимает логист и он перезвонит сразу после разговора, и задай свой вопрос.
- Неудобно — спроси, когда перезвонить, или предложи ответить в чате АТИ, и заканчивай.
- Говори просто, как живой человек: 1–2 коротких предложения, по одному вопросу за раз. Сначала — актуален ли груз.
- Только живая речь: без списков, разметки и скобок. Числа и деньги — словами, как вслух.
- Не спрашивай то, что уже известно из переписки ниже. Узнал всё нужное — поблагодари, скажи, что логист свяжется, и попрощайся."""


def context(load, metrics, truck, goal, chat, notes):
    convo = "\n".join(f"{'МЫ' if x['mine'] else 'ЗАКАЗЧИК'}: {x['text']}" for x in chat) or "(переписки ещё нет)"
    return f"""Наша машина: {truck['name']}, {truck['body_name']}, {truck['capacity']} т, высота кузова {truck['height']} м, свободна в {truck['city_name']} с {truck['free_from']}.
{describe(load, metrics)}
Цель переговоров: {goal}
Знания и предпочтения компании: {notes or '—'}

Переписка:
{convo}"""


async def negotiate(load, metrics, truck, goal, chat, notes, me_name):
    """Следующий шаг в чате; им же разбираем итог звонка (расшифровка лежит в переписке)."""
    prompt = f"""{CORE.format(me=me_name)}
{CHAT}

{context(load, metrics, truck, goal, chat, notes)}

Реши следующий шаг:
- reply: следующее сообщение заказчику (null, если писать сейчас нечего, например ждём ответа на наш последний вопрос).
- status: "ready" — всё нужное по цели узнали, логисту остаётся решить и подтвердить; "declined" — груз уже ушёл, условия точно не подходят или просят не беспокоить; иначе "waiting".
- facts: что уже известно (актуальность, итоговая ставка, форма оплаты, точный адрес, время погрузки, прочее); null — если неизвестно.
- summary: одна строка для логиста, что сейчас происходит."""
    return await complete(prompt, NEGOTIATE_SCHEMA)


def call_instructions(load, metrics, truck, goal, chat, notes, me_name):
    """Инструкции голосовой модели на один звонок (Yandex Realtime, как voice.py в ai-managers-site).
    После звонка расшифровка идёт в переписку и разбирается тем же negotiate()."""
    return f"""{CORE.format(me=me_name)}
{VOICE.format(me=me_name)}

{context(load, metrics, truck, goal, chat, notes)}"""


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
    return await complete(prompt, RULE_SCHEMA)
