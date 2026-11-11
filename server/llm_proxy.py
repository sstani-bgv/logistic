"""Локальный OpenAI-совместимый эндпоинт для разработки: /v1/chat/completions.

LLM_PROXY_MODE=codex — отвечает через Codex CLI (`codex exec --output-schema`), нужен установленный codex.
LLM_PROXY_MODE=mock  — мгновенный детерминированный ответ по JSON-схеме, без сети и ключей (тесты, демо).

Запуск: uv run uvicorn llm_proxy:app --port 8011
Приложение: LLM_BASE_URL=http://127.0.0.1:8011/v1  LLM_JSON_MODE=json_schema
"""
import asyncio
import json
import os
import tempfile
import time
import uuid

from fastapi import FastAPI, HTTPException

app = FastAPI(title="LLM proxy (codex/mock)")
MODE = os.environ.get("LLM_PROXY_MODE", "codex")
# Должен быть меньше LLM_TIMEOUT приложения, иначе клиент уйдёт на повтор, пока codex ещё думает
CODEX_TIMEOUT = float(os.environ.get("CODEX_TIMEOUT", "80"))
LIMIT = asyncio.Semaphore(int(os.environ.get("CODEX_PARALLEL", "2")))  # не плодим процессы codex
SCHEMA_MARK = "JSON должен соответствовать схеме:\n"


def extract_schema(body):
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        return (fmt.get("json_schema") or {}).get("schema")
    for m in body.get("messages", []):  # режим json_object: llm.py кладёт схему в system
        text = m.get("content") or ""
        if m.get("role") == "system" and SCHEMA_MARK in text:
            try:
                return json.loads(text.split(SCHEMA_MARK, 1)[1])
            except ValueError:
                pass
    return None


# --- mock ---

MOCK_STRINGS = {
    "reply": "Добрый день! Груз ещё актуален? Подскажите точный адрес и время погрузки.",
    "summary": "Мок: спросили актуальность, ждём ответа заказчика",
    "text": "Мок-правило: не берём такие грузы",
    "value": "мок",
}


def mock_value(schema, key=""):
    if "enum" in schema:
        return {"status": "waiting", "match": "cargo"}.get(key, schema["enum"][0])
    t = schema.get("type")
    types = t if isinstance(t, list) else [t]
    if "object" in types:
        return {k: mock_value(s, k) for k, s in schema.get("properties", {}).items()}
    if key in MOCK_STRINGS and "string" in types:
        return MOCK_STRINGS[key]
    if "null" in types:
        return None
    return {"string": "мок", "integer": 0, "number": 0, "boolean": False, "array": []}.get(types[0])


# --- codex ---

async def codex(prompt, schema):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out.json")
        args = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", d, "-o", out]
        if schema:
            sp = os.path.join(d, "s.json")
            with open(sp, "w") as f:
                json.dump(schema, f)
            args += ["--output-schema", sp]
        try:
            p = await asyncio.create_subprocess_exec(*args, "-", stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            raise HTTPException(500, "codex CLI не найден в PATH")
        try:
            _, err = await asyncio.wait_for(p.communicate(prompt.encode()), CODEX_TIMEOUT)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            raise HTTPException(504, f"codex не ответил за {CODEX_TIMEOUT:.0f} с")
        if p.returncode:
            raise HTTPException(502, "codex: " + err.decode()[-500:])
        if not os.path.exists(out):
            raise HTTPException(502, "codex завершился без ответа")
        with open(out) as f:
            return f.read().strip()


@app.post("/v1/chat/completions")
async def chat(body: dict):
    schema = extract_schema(body)
    if MODE == "mock":
        content = json.dumps(mock_value(schema) if schema else {}, ensure_ascii=False)
    else:
        prompt = "\n\n".join(f"[{m.get('role')}]\n{m.get('content') or ''}" for m in body.get("messages", []))
        async with LIMIT:
            content = await codex(prompt, schema)
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", MODE),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODE, "object": "model", "owned_by": "local"}]}
