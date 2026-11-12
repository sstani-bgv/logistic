"""Голосовой релей на поддельном Yandex Realtime — без сети и без трат."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

import main
import voice
from test_fleet import dialog, load_raw, truck  # noqa: F401


class FakeYandex:
    """Ведёт себя как Realtime API: на response.create агент говорит, на звук собеседника — расшифровка и ответ."""
    def __init__(self, script=None):
        self.got, self.out = [], asyncio.Queue()
        self.script = script or []
        self.url = self.headers = None

    def __call__(self, url, additional_headers=None, **kw):
        self.url, self.headers = url, additional_headers
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, raw):
        ev = json.loads(raw)
        self.got.append(ev)
        if ev["type"] == "response.create":
            for x in [{"type": "response.created"},
                      {"type": "response.output_audio.delta", "delta": "AAAA", "item_id": "i1"},
                      {"type": "response.output_audio_transcript.delta", "delta": "Здравствуйте, "},
                      {"type": "response.output_audio_transcript.delta", "delta": "груз актуален?"},
                      {"type": "response.done"}]:
                await self.out.put(x)
        if ev["type"] == "input_audio_buffer.append":
            for x in self.script:
                await self.out.put(x)
            self.script = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.dumps(await self.out.get())


@pytest.fixture(autouse=True)
def clean():
    for t in ("trucks", "loads", "dialogs", "messages", "voice_usage"):
        main.DB.execute(f"delete from {t}")
    main.VOICE_LIVE.clear()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("YC_API_KEY", "test-key")
    monkeypatch.setenv("YC_FOLDER_ID", "b1gtest")
    monkeypatch.setenv("VOICE_IDLE_SEC", "3")
    main.DB.execute("delete from voice_usage")


def test_from_client_filters():
    assert voice.from_client({"type": "input_audio_buffer.append", "audio": "QQ=="}) == {"type": "input_audio_buffer.append", "audio": "QQ=="}
    assert voice.from_client({"type": "session.update", "session": {"instructions": "взлом"}}) is None  # промпт не подменить
    assert voice.from_client({"type": "input_audio_buffer.append", "audio": "x" * 300_000}) is None
    assert voice.from_client({"type": "conversation.item.truncate", "item_id": "i", "audio_end_ms": 5})["content_index"] == 0


def test_talk_collects_transcript_and_uses_instructions(env, monkeypatch):
    fake = FakeYandex([{"type": "conversation.item.input_audio_transcription.completed", "transcript": "Да, грузим завтра с 10"},
                       {"type": "response.created"},
                       {"type": "response.output_audio_transcript.delta", "delta": "Спасибо, логист перезвонит."},
                       {"type": "response.done"}])
    monkeypatch.setattr(voice, "CONNECT", fake)
    inbox = [{"type": "input_audio_buffer.append", "audio": "QQ=="}, {"type": "session.update"}]
    sent = []

    async def recv():
        await asyncio.sleep(0.05)
        return inbox.pop(0) if inbox else None

    async def send(ev):
        sent.append(ev)
    reason, turns, secs = asyncio.run(voice.talk(recv, send, "ИНСТРУКЦИЯ", voice.cfg()))
    assert reason == "hangup"
    assert fake.headers == {"Authorization": "Api-Key test-key"} and "gpt://b1gtest/" in fake.url
    assert fake.got[0]["session"]["instructions"] == "ИНСТРУКЦИЯ"
    assert not any(e["type"] == "session.update" for e in fake.got[1:])  # с той стороны промпт не меняется
    assert voice.transcript(turns) == "Агент: Здравствуйте, груз актуален?\nЗаказчик: Да, грузим завтра с 10\nАгент: Спасибо, логист перезвонит."
    assert {"type": "response.output_audio.delta", "delta": "AAAA", "item_id": "i1"} in sent
    assert not any("transcript" in e for e in sent)  # клиенту — только звук и служебное


def test_ws_test_call(env, monkeypatch, make_client):
    did = dialog(truck(), load_raw(), status="waiting")
    fake = FakeYandex()
    monkeypatch.setattr(voice, "CONNECT", fake)
    c = make_client("logist")
    with c.websocket_connect(f"/api/voice/test/{did}") as ws:
        assert ws.receive_json()["type"] == "response.created"
        ws.close()
    instr = fake.got[0]["session"]["instructions"]
    assert "по телефону" in instr and "Цель переговоров: g" in instr and "ничего не покупаешь" in instr
    row = main.q("select * from voice_usage")[0]
    assert row["dialog_id"] == did and row["login"]
    assert main.q("select count(*) n from messages where dialog_id=?", did)[0]["n"] == 0  # тест в переписку не попадает


def test_ws_requires_login_and_same_origin(env, make_client):
    did = dialog(truck(), load_raw(), status="waiting")
    with pytest.raises(Exception):
        with TestClient(main.app).websocket_connect(f"/api/voice/test/{did}") as ws:
            ws.receive_json()
    with pytest.raises(Exception):
        with make_client("logist").websocket_connect(f"/api/voice/test/{did}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_ws_not_configured_and_daily_cap(env, monkeypatch, make_client):
    did = dialog(truck(), load_raw(), status="waiting")
    c = make_client("logist")
    monkeypatch.delenv("YC_API_KEY")
    with c.websocket_connect(f"/api/voice/test/{did}") as ws:
        assert "не настроен" in ws.receive_json()["text"]
    monkeypatch.setenv("YC_API_KEY", "k")
    main.DB.execute("insert into voice_usage values(strftime('%s','now'),?,600,60,'x','limit')", (did,))
    with c.websocket_connect(f"/api/voice/test/{did}") as ws:
        assert "лимит" in ws.receive_json()["text"]


def test_collect_cumulative_transcript_and_cut_answers():
    turns, buf = [], {}
    for x in ["да", "да слушаю", "да слушаю груз актуален"]:
        voice.collect({"type": voice.HEARD, "transcript": x}, turns, buf)
    voice.collect({"type": "response.created"}, turns, buf)
    voice.collect({"type": "response.output_audio_transcript.delta", "delta": "Г"}, turns, buf)
    voice.collect({"type": "response.done", "response": {"status": "cancelled"}}, turns, buf)  # огрызок перебитого
    voice.collect({"type": "response.output_audio_transcript.delta", "delta": "Понял, а адрес погрузки"}, turns, buf)
    voice.collect({"type": "response.done", "response": {"status": "cancelled"}}, turns, buf)
    voice.collect({"type": voice.HEARD, "transcript": "да слушаю груз актуален Шушары"}, turns, buf)
    voice.collect({"type": voice.HEARD, "transcript": "Колпино"}, turns, buf)  # новый отрезок, не продолжение
    assert voice.transcript(turns) == ("Заказчик: да слушаю груз актуален\nАгент: Понял, а адрес погрузки …\n"
                                       "Заказчик: Шушары Колпино")


def test_voice_prompt_forbids_agreeing():
    import agent
    v = agent.VOICE.split("\n")[1]
    assert v.startswith("ГЛАВНОЕ ПРАВИЛО") and "«подходит»" in v and "«берём»" in v


def test_greeting_has_route_and_goes_first(env, monkeypatch):
    import agent
    g = agent.greeting(load_raw(), "Иван")
    assert g.startswith("Здравствуйте! Я помощник логиста Иван") and "Санкт-Петербург — Москва" in g
    assert agent.greeting({}, "Иван").endswith("на АТИ. Вам удобно сейчас говорить?")  # без маршрута — без «: »
    fake = FakeYandex()
    monkeypatch.setattr(voice, "CONNECT", fake)

    async def recv():
        return None
    asyncio.run(voice.talk(recv, lambda ev: asyncio.sleep(0), "И", voice.cfg(), greeting=g))
    assert fake.got[1] == {"type": "response.create", "response": {"instructions": f"Начни звонок этой фразой, почти дословно: {g}"}}


def test_call_ends_after_goodbye(env, monkeypatch):
    fake = FakeYandex([{"type": "conversation.item.input_audio_transcription.completed", "transcript": "Всё, жду звонка"},
                       {"type": "response.created"},
                       {"type": "response.output_audio.delta", "delta": "AAAA", "item_id": "i2"},
                       {"type": "response.output_audio_transcript.delta", "delta": "Спасибо, логист перезвонит. До свидания!"},
                       {"type": "response.done"}])
    monkeypatch.setattr(voice, "CONNECT", fake)
    monkeypatch.setenv("VOICE_IDLE_SEC", "30")
    sent = [{"type": "input_audio_buffer.append", "audio": "QQ=="}]

    async def recv():
        if sent:
            return sent.pop()
        await asyncio.sleep(60)  # собеседник молчит и трубку не кладёт

    async def send(ev):
        pass
    t0 = time.monotonic()
    reason, turns, _ = asyncio.run(voice.talk(recv, send, "И", voice.cfg()))
    assert reason == "bye" and time.monotonic() - t0 < 5  # не ждём 30 секунд тишины за деньги
    assert turns[-1]["text"].endswith("До свидания!")
