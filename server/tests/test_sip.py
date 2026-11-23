"""Телефон робота (sip.py) на поддельной АТС: регистрация с паролем, входящий, звук в обе стороны, BYE.
Сеть — только 127.0.0.1, Yandex — поддельный (FakeYandex), без трат."""
import asyncio
import audioop
import base64
import hashlib
import json
import secrets
import struct
import time

import pytest

import main
import sip
import voice
from test_fleet import dialog, load_raw, truck  # noqa: F401
from test_voice import FakeYandex

LOGIN, PASSWORD = "705@vats1.cp.megafon.ru", "s3cret-line"


class FakePBX(asyncio.DatagramProtocol):
    """АТС: просит пароль (401 + digest), принимает регистрацию, звонит линии, шлёт звук и слушает ответ."""

    def __init__(self):
        self.registered = asyncio.Event()
        self.answered = asyncio.Event()
        self.bye = asyncio.Event()
        self.nonce = secrets.token_hex(8)
        self.contact = None
        self.answer = None
        self.got_auth = None
        self.statuses = []

    def connection_made(self, t):
        self.t = t

    def datagram_received(self, data, addr):
        m = sip.Msg.parse(data)
        if m.is_request and m.method == "REGISTER":
            auth = m.get("authorization")
            if not auth:
                return self.respond(m, addr, "401 Unauthorized",
                                    [("www-authenticate", f'Digest realm="vats1", nonce="{self.nonce}", qop="auth", algorithm=MD5')])
            self.got_auth = auth
            h = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
            p = {k: sip.param(auth, k) for k in ("username", "uri", "nc", "cnonce", "response")}
            want = h(f"{h(p['username'] + ':vats1:' + PASSWORD)}:{self.nonce}:{p['nc']}:{p['cnonce']}:auth:{h('REGISTER:' + p['uri'])}")
            if p["response"] != want:
                return self.respond(m, addr, "403 Forbidden")
            self.contact = addr
            self.respond(m, addr, "200 OK")
            self.registered.set()
        elif m.is_request and m.method == "BYE":
            self.respond(m, addr, "200 OK")
            self.bye.set()
        elif not m.is_request:
            self.statuses.append(m.status)
            if m.status == 200 and m.method == "INVITE":
                self.answer = m
                self.answered.set()
                ack = sip.Msg("ACK sip:705@127.0.0.1 SIP/2.0", [("via", "SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKack"),
                              ("from", m.get("from")), ("to", m.get("to")), ("call-id", m.get("call-id")), ("cseq", "1 ACK")])
                self.t.sendto(ack.build(), addr)

    def respond(self, m, addr, status, extra=()):
        hs = [("via", m.get("via")), ("from", m.get("from")), ("to", m.get("to") + ";tag=pbx"),
              ("call-id", m.get("call-id")), ("cseq", m.get("cseq"))] + list(extra)
        self.t.sendto(sip.Msg("SIP/2.0 " + status, hs).build(), addr)

    def invite(self, rtp_port, call_id="call-1"):
        sdp = ("v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
               f"m=audio {rtp_port} RTP/AVP 0 8 101\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n")
        hs = [("via", "SIP/2.0/UDP 127.0.0.1;branch=z9hG4bKinv"), ("from", "<sip:79215551234@vats1>;tag=c1"),
              ("to", "<sip:705@vats1>"), ("call-id", call_id), ("cseq", "1 INVITE"),
              ("contact", "<sip:pbx@127.0.0.1>"), ("content-type", "application/sdp")]
        self.t.sendto(sip.Msg("INVITE sip:705@127.0.0.1 SIP/2.0", hs, sdp).build(), self.contact)


class RTPPeer(asyncio.DatagramProtocol):
    def __init__(self):
        self.frames = []

    def connection_made(self, t):
        self.t = t

    def datagram_received(self, data, addr):
        self.frames.append(data)


def tone(ms=100):
    """Громкий звук заказчика (PCMA, 8 кГц)."""
    pcm = b"".join(struct.pack("<h", 8000 if (i // 10) % 2 else -8000) for i in range(8 * ms))
    return audioop.lin2alaw(pcm, 2)


def test_digest_and_parse():
    m = sip.Msg.parse(b"SIP/2.0 401 Unauthorized\r\nv: SIP/2.0/UDP x\r\ni: abc\r\nCSeq: 2 REGISTER\r\n"
                      b'WWW-Authenticate: Digest realm="r", nonce="n"\r\n\r\n')
    assert m.status == 401 and m.method == "REGISTER" and m.get("call-id") == "abc"
    a = sip.digest("REGISTER", "sip:d", "u", "p", m.get("www-authenticate"))
    h = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
    assert f'response="{h(h("u:r:p") + ":n:" + h("REGISTER:sip:d"))}"' in a  # без qop — классическая схема
    assert sip.sdp_offer("c=IN IP4 1.2.3.4\r\nm=audio 4000 RTP/AVP 8 101\r\n") == ("1.2.3.4", 4000, [8, 101])
    pkt = struct.pack("!BBHII", 0x80, 8, 1, 160, 7) + b"\xd5" * 160
    assert sip.rtp_payload(pkt) == (8, b"\xd5" * 160)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("YC_API_KEY", "test-key")
    monkeypatch.setenv("YC_FOLDER_ID", "b1gtest")
    monkeypatch.setenv("VOICE_IDLE_SEC", "3")
    for t in ("trucks", "loads", "dialogs", "messages", "calls"):
        main.DB.execute(f"delete from {t}")


@pytest.mark.parametrize("trial", [False, True])
def test_line_call_end_to_end(env, monkeypatch, trial):
    """ВАТС зовёт бота → линия отвечает → агент говорит голосом в RTP, слышит заказчика → итог и расшифровка в звонке.
    trial — пробный звонок из настроек: без диалога, по примерному грузу."""
    did = main.TEST_CALL if trial else dialog(truck(), load_raw(), status="waiting")
    main.DB.execute("insert into calls(dialog_id,phone,status,created,started,provider_id) values(?,?,?,?,?,?)",
                    (did, "+79215551234", "ringing", time.time(), time.time(), "555"))
    agent_pcm = base64.b64encode(b"\x10\x00" * 3200).decode()  # 100 мс звука агента (16 кГц)
    fake = FakeYandex([{"type": "conversation.item.input_audio_transcription.completed", "transcript": "Да, груз актуален"},
                       {"type": "response.created"},
                       {"type": "response.output_audio.delta", "delta": agent_pcm, "item_id": "i2"},
                       {"type": "response.output_audio_transcript.delta", "delta": "Спасибо, до свидания."},
                       {"type": "response.done"}])
    monkeypatch.setattr(voice, "CONNECT", fake)
    monkeypatch.setenv("VOICE_IDLE_SEC", "2")
    finished = []

    async def go():
        loop = asyncio.get_running_loop()
        pbx = FakePBX()
        pt, _ = await loop.create_datagram_endpoint(lambda: pbx, local_addr=("127.0.0.1", 0))
        pbx_port = pt.get_extra_info("sockname")[1]
        peer = RTPPeer()
        rt, _ = await loop.create_datagram_endpoint(lambda: peer, local_addr=("127.0.0.1", 0))
        rtp_port = rt.get_extra_info("sockname")[1]

        async def on_call(c):
            await main.line_talk(c)
            finished.append(True)
        line = sip.Phone(f"127.0.0.1:{pbx_port}", LOGIN, PASSWORD, on_call, public_ip="127.0.0.1", port=0,
                         rtp_ports=(41000, 41010), accept=lambda m: main.pending_line_call() is not None)
        # порт 0 → берём любой свободный: подменяем run, чтобы узнать его
        await loop.create_datagram_endpoint(lambda: line, local_addr=("127.0.0.1", 0))
        line.port = line.transport.get_extra_info("sockname")[1]
        await line.register_once()
        assert pbx.registered.is_set() and 'username="705"' in pbx.got_auth
        pbx.invite(rtp_port)
        await asyncio.wait_for(pbx.answered.wait(), 3)
        _, port, pts = sip.sdp_offer(pbx.answer.body)
        assert pts[0] == 8  # PCMA — предпочтительный кодек в России
        for i in range(8):  # заказчик говорит
            pkt = struct.pack("!BBHII", 0x80, 8, i, i * 800, 99) + tone()
            rt.sendto(pkt, ("127.0.0.1", port))
            await asyncio.sleep(0.02)
        await asyncio.wait_for(pbx.bye.wait(), 10)  # агент попрощался — линия кладёт трубку сама
        for _ in range(30):
            if finished:
                break
            await asyncio.sleep(0.1)
        loud = [f for f in peer.frames if f[12:] != b"\xd5" * 160]
        line.stop()
        pt.close()
        rt.close()
        return loud
    loud = asyncio.run(go())
    assert loud, "агент должен говорить в RTP не только тишиной"
    assert any(e["type"] == "input_audio_buffer.append" for e in fake.got)  # звук заказчика ушёл в Yandex
    assert fake.got[0]["session"]["audio"]["input"]["format"]["rate"] == sip.VOICE_RATE
    c = main.q("select * from calls where provider_id='555'")[0]
    assert c["status"] == "done" and "Заказчик: Да, груз актуален" in c["transcript"]
    if trial:
        assert "Санкт-Петербург — Москва" in json.dumps(fake.got, ensure_ascii=False)
        assert not main.q("select * from messages")


def test_foreign_invite_rejected(env):
    """Кто-то набрал внутренний номер бота, а звонка мы не ставили — трубку не берём (480)."""
    async def go():
        loop = asyncio.get_running_loop()
        pbx = FakePBX()
        pt, _ = await loop.create_datagram_endpoint(lambda: pbx, local_addr=("127.0.0.1", 0))
        line = sip.Phone(f"127.0.0.1:{pt.get_extra_info('sockname')[1]}", LOGIN, PASSWORD, None, public_ip="127.0.0.1",
                         accept=lambda m: main.pending_line_call() is not None)
        await loop.create_datagram_endpoint(lambda: line, local_addr=("127.0.0.1", 0))
        line.port = line.transport.get_extra_info("sockname")[1]
        await line.register_once()
        pbx.invite(40999)
        await asyncio.sleep(0.5)
        line.stop()
        pt.close()
        return pbx.statuses
    assert 480 in asyncio.run(go())


def test_wrong_password(env):
    async def go():
        loop = asyncio.get_running_loop()
        pbx = FakePBX()
        pt, _ = await loop.create_datagram_endpoint(lambda: pbx, local_addr=("127.0.0.1", 0))
        line = sip.Phone(f"127.0.0.1:{pt.get_extra_info('sockname')[1]}", LOGIN, "wrong", None, public_ip="127.0.0.1")
        await loop.create_datagram_endpoint(lambda: line, local_addr=("127.0.0.1", 0))
        try:
            await line.register_once()
        finally:
            line.stop()
            pt.close()
    with pytest.raises(RuntimeError, match="отклонена"):
        asyncio.run(go())


def test_phone_settings_line(make_client):
    owner = make_client("owner")
    r = owner.put("/api/phone", json={"sip_server": "sip.example.ru", "sip_login": "705@vats1", "sip_password": "pw-1"})
    v = r.json()
    assert r.status_code == 200 and v["sip_login"] == "705@vats1" and v["sip_set"] and "pw-1" not in r.text
    for k in ("sip_server", "sip_login", "sip_password"):
        main.DB.execute("delete from kv where k=?", (k,))
