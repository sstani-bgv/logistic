"""Языковая модель через OpenAI-совместимый /chat/completions со structured output.

Провайдер, модель и ключ — только в .env (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY): DeepSeek, Yandex Cloud AI Studio,
OpenRouter или локальный llm_proxy.py (Codex CLI / мок) — код один и тот же.
"""
import asyncio
import json
import logging
import os

import httpx

log = logging.getLogger("llm")
TRANSPORT = None  # в тестах подменяется на httpx.MockTransport
RETRY_DELAY = 1.0


class LLMError(RuntimeError):
    pass


def cfg():
    """Читаем .env при каждом вызове: удобно в тестах и при смене провайдера."""
    e = os.environ
    return {
        "base_url": e.get("LLM_BASE_URL", "http://127.0.0.1:8011/v1").rstrip("/"),
        "model": e.get("LLM_MODEL", "codex"),
        "key": e.get("LLM_API_KEY", ""),
        # json_object понимают DeepSeek, Yandex и почти все совместимые API; json_schema — строгий режим OpenAI
        "json_mode": e.get("LLM_JSON_MODE", "json_object"),
        "timeout": float(e.get("LLM_TIMEOUT", "90")),
        "temperature": float(e.get("LLM_TEMPERATURE", "0.3")),
    }


async def complete(prompt, schema):
    """Промпт → dict, проверенный по JSON-схеме. Любая проблема — LLMError с понятным текстом."""
    c = cfg()
    data = await post(f"{c['base_url']}/chat/completions", payload(prompt, schema, c),
                      {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}, c["timeout"])
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"Неожиданный ответ LLM: {str(data)[:300]}")
    if isinstance(data.get("usage"), dict):
        u = data["usage"]
        log.info("LLM %s: %s → %s токенов", c["model"], u.get("prompt_tokens"), u.get("completion_tokens"))
    return check(normalize(parse(text), schema), schema)


def payload(prompt, schema, c):
    system = "Отвечай строго одним JSON-объектом, без пояснений и markdown."
    if c["json_mode"] == "json_schema":
        fmt = {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}
    else:
        fmt = {"type": "json_object"}
        system += " JSON должен соответствовать схеме:\n" + json.dumps(schema, ensure_ascii=False)
    return {"model": c["model"], "temperature": c["temperature"], "response_format": fmt,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}


async def post(url, body, headers, timeout):
    """POST с одним повтором на таймаут, сетевую ошибку, 429 и 5xx. 4xx не повторяем — это ошибка настройки."""
    last = None
    async with httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) as client:
        for attempt in range(2):
            try:
                r = await client.post(url, json=body, headers=headers)
            except httpx.TimeoutException:
                last = LLMError(f"LLM не ответила за {timeout:.0f} с")
            except httpx.TransportError as e:
                last = LLMError(f"LLM недоступна ({url}): {e}")
            else:
                if r.status_code < 400:
                    try:
                        return r.json()
                    except ValueError:
                        raise LLMError(f"LLM вернула не JSON: {r.text[:200]}")
                last = LLMError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code != 429 and r.status_code < 500:
                    raise last
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
    raise last


def parse(text):
    if text.startswith("```"):  # некоторые модели заворачивают JSON в markdown
        text = text.strip("`").strip().removeprefix("json").strip()
    try:
        res = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError(f"LLM вернула невалидный JSON: {text[:200]}")
    if not isinstance(res, dict):
        raise LLMError(f"LLM вернула не объект: {text[:200]}")
    return res


def normalize(v, schema):
    """В режиме json_object модели иногда пропускают nullable-поля — дополняем их null."""
    if isinstance(v, dict) and "properties" in schema:
        for k, sub in schema["properties"].items():
            t = sub.get("type")
            if k not in v and isinstance(t, list) and "null" in t:
                v[k] = None
            elif k in v:
                normalize(v[k], sub)
    return v


PY = {"string": str, "object": dict, "boolean": bool, "null": type(None), "array": list}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, PY[t])


def check(v, schema, path="$"):
    """Проверка по нашим схемам (type, enum, required, properties); лишние поля отбрасываем."""
    types = schema.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    if types and not any(_is(v, t) for t in types):
        raise LLMError(f"Ответ LLM не по схеме: {path} — ожидался {'/'.join(types)}, получено {v!r}"[:250])
    if "enum" in schema and v not in schema["enum"]:
        raise LLMError(f"Ответ LLM не по схеме: {path} = {v!r}, допустимо {schema['enum']}")
    if isinstance(v, dict) and "properties" in schema:
        for k in schema.get("required", []):
            if k not in v:
                raise LLMError(f"Ответ LLM не по схеме: нет поля {path}.{k}")
        for k, sub in schema["properties"].items():
            if k in v:
                check(v[k], sub, f"{path}.{k}")
        if schema.get("additionalProperties") is False:
            for k in [k for k in v if k not in schema["properties"]]:
                v.pop(k)
    return v
