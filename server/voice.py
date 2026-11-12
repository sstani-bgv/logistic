"""Голос агента: звук разговора ⇄ этот релей ⇄ Yandex AI Studio Realtime API.

Та же схема, что у голосового менеджера на сайте (ai-managers-site/server/voice.py): модель сама слушает и говорит,
перебивание — серверный VAD. Ключ остаётся на сервере. С «той стороны» сейчас браузер логиста (тестовый звонок,
логист играет заказчика); после доступа к ВАТС сюда же подключится SIP-линия — события те же.

Инструкции на звонок — agent.call_instructions(): то же ядро, что и в чате (только узнать, ничего не подтверждать).
"""
import asyncio
import json
import os
import re
import time

import websockets

RATE = 44100  # PCM16 моно, как в браузерном клиенте сайта
ALLOWED_IN = ("input_audio_buffer.append", "response.cancel", "conversation.item.truncate")
TO_CLIENT = {"response.created", "response.output_audio.delta", "input_audio_buffer.speech_started",
             "input_audio_buffer.speech_stopped", "response.done"}
SAY = ("response.output_audio_transcript.delta", "response.output_text.delta")  # текст ответа кусками
HEARD = "conversation.item.input_audio_transcription.completed"
FATAL = {"upstream_unavailable", "server_error", "internal_error"}

CONNECT = websockets.connect  # тесты подменяют на поддельный Yandex — без трат


def cfg():
    """Читается на каждый звонок: ключи и лимиты меняются в .env без пересборки."""
    folder = os.environ.get("YC_FOLDER_ID", "")
    model = os.environ.get("YC_RT_MODEL", "speech-realtime-max-tts-live/latest")
    return {
        "key": os.environ.get("YC_RT_API_KEY") or os.environ.get("YC_API_KEY", ""),
        "url": os.environ.get("YC_RT_URL") or f"wss://ai.api.cloud.yandex.net/v1/realtime/openai?model=gpt://{folder}/{model}",
        "ready": bool((os.environ.get("YC_RT_API_KEY") or os.environ.get("YC_API_KEY")) and (folder or os.environ.get("YC_RT_URL"))),
        "voice": os.environ.get("YC_RT_VOICE") or ("sergey" if "tts-live" in model else "kirill"),
        # пауза собеседника, после которой агент отвечает: по телефону люди говорят с паузами после запятых
        "silence_ms": int(os.environ.get("VOICE_SILENCE_MS", 1100)),
        "call_sec": int(os.environ.get("VOICE_CALL_SEC", 240)),
        "idle_sec": int(os.environ.get("VOICE_IDLE_SEC", 25)),
        "rub_sec": float(os.environ.get("VOICE_RUB_SEC", 0.1)),  # ≈6 ₽/мин — оценка как на сайте; сверить со счётом Яндекса
        "rub_day": float(os.environ.get("VOICE_RUB_DAY", 50)),  # потолок трат на тестовые звонки в день
    }


def session(instructions, c):
    pcm = {"type": "audio/pcm", "rate": RATE}
    return {"type": "realtime", "output_modalities": ["audio"], "instructions": instructions,
            "audio": {"input": {"format": pcm,
                                "turn_detection": {"type": "server_vad", "threshold": 0.5,
                                                   "silence_duration_ms": c["silence_ms"]}},
                      "output": {"format": pcm, "voice": c["voice"]}}}


def from_client(ev):
    """Событие с той стороны (браузер/SIP) → событие для Yandex. Всё прочее выбрасываем."""
    if not isinstance(ev, dict):
        return None
    t = ev.get("type")
    if t == "input_audio_buffer.append" and isinstance(ev.get("audio"), str) and len(ev["audio"]) <= 200_000:
        return {"type": t, "audio": ev["audio"]}
    if t == "response.cancel":
        return {"type": t}
    if (t == "conversation.item.truncate" and isinstance(ev.get("item_id"), str) and len(ev["item_id"]) <= 100
            and isinstance(ev.get("audio_end_ms"), int) and 0 <= ev["audio_end_ms"] < 10**7):
        return {"type": t, "item_id": ev["item_id"], "content_index": 0, "audio_end_ms": ev["audio_end_ms"]}
    return None


def collect(ev, turns, buf):
    """Текст разговора. Yandex присылает распознанное у собеседника нарастающим итогом («да» → «да слушаю» → …) —
    берём только новое и склеиваем подряд идущие куски; ответ агента — из кусков, по концу ответа;
    ответ, который собеседник перебил, — с «…», огрызки в одну-две буквы не пишем."""
    t, said = ev.get("type"), str(ev.get("transcript") or "").strip()
    if t == HEARD and said:
        # сравниваем по словам без пунктуации и регистра: распознавание может поправить «Да слушаю» → «Да, слушаю вас»
        words, prev = said.split(), buf.get("_heard", [])
        norm = [re.sub(r"[^\w]+", "", w.lower()) for w in words]
        new = " ".join(words[len(prev):]) if prev and norm[:len(prev)] == prev else said
        buf["_heard"] = norm
        if new:
            if turns and turns[-1]["who"] == "Заказчик":
                turns[-1]["text"] += " " + new
            else:
                turns.append({"who": "Заказчик", "text": new})
    elif t in SAY:
        buf.setdefault(t, []).append(str(ev.get("delta") or ""))
    elif t in ("response.created", "response.done"):
        text = "".join(buf.get(SAY[0]) or buf.get(SAY[1]) or []).strip()
        for k in SAY:
            buf.pop(k, None)
        cut = (ev.get("response") or {}).get("status") in ("cancelled", "incomplete")
        if len(text) > 2:
            turns.append({"who": "Агент", "text": text + (" …" if cut else "")})


def explain(e):
    """Причина сбоя голосового звонка — по-русски, без технических простыней."""
    status = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status_code", None)
    if status in (401, 403):
        return f"Yandex отклонил ключ ({status}) — проверьте YC_API_KEY и роль сервисного аккаунта"
    if status == 429:
        return "Yandex: превышен лимит запросов, попробуйте через минуту"
    if status == 402:
        return "Yandex: закончились деньги на балансе"
    if isinstance(e, (OSError, asyncio.TimeoutError)):
        return f"нет связи с Yandex ({type(e).__name__})"
    return f"{type(e).__name__}: {str(e)[:200]}"


def transcript(turns):
    return "\n".join(f"{x['who']}: {x['text']}" for x in turns)


def fatal(err, heard):
    """Класть ли трубку на error от Yandex: сбой сервера — всегда, промах cancel/truncate — никогда, прочее — до первого звука."""
    code, kind = str(err.get("code") or ""), str(err.get("type") or "")
    if code in FATAL or kind in FATAL:
        return True
    if "cancel" in f"{code} {err.get('message') or ''}".lower() or "truncat" in f"{code} {err.get('message') or ''}".lower():
        return False
    return not heard


BYE = re.compile(r"(?i)(до свидания|всего доброго|всего хорошего|хорошего дня|до связи)")


async def talk(recv, send, instructions, c=None, greeting=None):
    """Один разговор. recv() -> dict | None (None — та сторона положила трубку), send(dict) — событие той стороне.
    -> (причина конца, реплики, секунды). Причины: hangup | bye (агент попрощался) | limit | idle | error.
    greeting — готовая первая фраза: модель начинает с неё почти дословно."""
    c = c or cfg()
    turns, buf = [], {}
    t0 = last = time.monotonic()
    heard = False
    said = 0.0  # секунд звука в текущем ответе агента
    bye_at = None  # агент попрощался: положить трубку, когда договорит
    async with CONNECT(c["url"], additional_headers={"Authorization": f"Api-Key {c['key']}"},
                       max_size=4 * 1024 * 1024, open_timeout=15) as up:
        if greeting:  # в инструкции сессии, а не подменой ответа — правила действуют и в первой реплике
            instructions += f"\nНачни звонок этой фразой, почти дословно: {greeting}"
        await up.send(json.dumps({"type": "session.update", "session": session(instructions, c)}))
        first = {"type": "response.create"}  # агент звонит — он и начинает разговор
        await up.send(json.dumps(first))

        async def down():
            nonlocal last, heard, said, bye_at
            async for raw in up:
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "error":
                    if fatal(ev.get("error") or {}, heard):
                        return "error"
                    continue
                n = len(turns)
                collect(ev, turns, buf)
                if t in ("response.created", "input_audio_buffer.speech_started"):
                    if t == "response.created":
                        said = 0.0
                    bye_at = None  # заговорил снова или собеседник перебил — разговор продолжается
                elif t == "response.output_audio.delta":
                    said += len(ev.get("delta") or "") * 3 / 4 / (2 * RATE)
                elif (t == "response.done" and (ev.get("response") or {}).get("status", "completed") == "completed"
                      and len(turns) > n and turns[-1]["who"] == "Агент" and BYE.search(turns[-1]["text"])):
                    bye_at = time.monotonic() + said + 1.5
                if t in TO_CLIENT:
                    last, heard = time.monotonic(), heard or t == "response.output_audio.delta"
                    await send({k: ev[k] for k in ("type", "delta", "item_id") if k in ev})
            return "error"

        async def uplink():
            while (ev := await recv()) is not None:
                if out := from_client(ev):
                    await up.send(json.dumps(out))
            return "hangup"

        async def clock():
            while True:
                await asyncio.sleep(1)
                now = time.monotonic()
                if bye_at and now >= bye_at:
                    return "bye"
                if now - t0 >= c["call_sec"]:
                    return "limit"
                if now - last >= c["idle_sec"]:
                    return "idle"

        tasks = [asyncio.create_task(x) for x in (down(), uplink(), clock())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    collect({"type": "response.done"}, turns, buf)  # ответ, который оборвал конец звонка
    return done.pop().result(), turns, time.monotonic() - t0
