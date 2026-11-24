"""Телефон робота: SIP-линия сотрудника-бота в ВАТС МегаФон ⇄ голос агента (voice.talk).

Как идёт звонок: программа командой Call API просит ВАТС позвонить заказчику (first_call=contact);
заказчик снял трубку → ВАТС звонит сотруднику-боту → эта линия сразу отвечает → звук разговора
идёт через voice.talk() в Yandex Realtime и обратно. Всё на asyncio + UDP, без внешних SIP-программ.

Звук: G.711 (PCMA/PCMU, 8 кГц) ⇄ PCM16 16 кГц для Yandex. Кадры RTP по 20 мс.
Настройки — в «Настройках» → «Телефония» (сервер, логин, пароль линии) или в .env (SIP_*).
"""
import asyncio
import audioop
import base64
import hashlib
import os
import random
import re
import secrets
import socket
import struct
import time

VOICE_RATE = 16000  # PCM16 для Yandex Realtime
FRAME = 160  # 20 мс при 8 кГц
CODECS = {8: ("PCMA", audioop.alaw2lin, audioop.lin2alaw, b"\xd5"), 0: ("PCMU", audioop.ulaw2lin, audioop.lin2ulaw, b"\xff")}
UA = "texso-dispatcher"


def log(msg, kind="info"):
    print(f"[sip] {msg}", flush=True)


# ---------- разбор SIP ----------

COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length", "c": "content-type"}


class Msg:
    """SIP-сообщение: первая строка, заголовки (с повторами, в порядке), тело."""

    def __init__(self, first, headers, body=""):
        self.first, self.headers, self.body = first, headers, body

    @classmethod
    def parse(cls, data):
        text = data.decode("utf-8", "replace")
        head, _, body = text.partition("\r\n\r\n")
        lines = head.split("\r\n")
        hs = []
        for ln in lines[1:]:
            if ln[:1] in (" ", "\t") and hs:  # перенос заголовка
                hs[-1] = (hs[-1][0], hs[-1][1] + " " + ln.strip())
                continue
            k, _, v = ln.partition(":")
            k = k.strip().lower()
            hs.append((COMPACT.get(k, k), v.strip()))
        return cls(lines[0], hs, body)

    def get(self, k, default=""):
        return next((v for kk, v in self.headers if kk == k), default)

    def all(self, k):
        return [v for kk, v in self.headers if kk == k]

    @property
    def is_request(self):
        return not self.first.startswith("SIP/2.0")

    @property
    def method(self):
        return self.first.split(" ", 1)[0] if self.is_request else self.get("cseq").split()[-1].upper()

    @property
    def status(self):
        return 0 if self.is_request else int(self.first.split()[1])

    def build(self):
        body = self.body or ""
        hs = [(k, v) for k, v in self.headers if k != "content-length"] + [("content-length", str(len(body.encode())))]
        return (self.first + "\r\n" + "".join(f"{nice(k)}: {v}\r\n" for k, v in hs) + "\r\n" + body).encode()


def nice(k):
    return {"call-id": "Call-ID", "cseq": "CSeq", "www-authenticate": "WWW-Authenticate"}.get(k, "-".join(p.capitalize() for p in k.split("-")))


def param(hval, name):
    m = re.search(rf"(?i)[;,\s]{name}=\"?([^\";,]+)\"?", " " + hval)
    return m.group(1) if m else None


def uri_of(hval):
    m = re.search(r"<([^>]+)>", hval)
    return m.group(1) if m else hval.split(";")[0].strip()


def digest(method, uri, user, password, challenge):
    """Ответ на 401/407 (RFC 2617, MD5, qop=auth)."""
    realm, nonce = param(challenge, "realm") or "", param(challenge, "nonce") or ""
    qop, opaque = param(challenge, "qop"), param(challenge, "opaque")
    h = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
    ha1, ha2 = h(f"{user}:{realm}:{password}"), h(f"{method}:{uri}")
    out = f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", algorithm=MD5'
    if qop:
        qop = "auth"
        cnonce, nc = secrets.token_hex(8), "00000001"
        resp = h(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
        out += f', qop={qop}, nc={nc}, cnonce="{cnonce}"'
    else:
        resp = h(f"{ha1}:{nonce}:{ha2}")
    out += f', response="{resp}"'
    if opaque:
        out += f', opaque="{opaque}"'
    return out


def sdp_offer(body):
    """-> (ip, port, [payload types]) из SDP собеседника."""
    ip = port = None
    pts = []
    for ln in body.splitlines():
        if ln.startswith("c=IN IP4 "):
            ip = ln.split()[-1]
        elif ln.startswith("m=audio "):
            f = ln.split()
            port, pts = int(f[1]), [int(x) for x in f[3:] if x.isdigit()]
    return ip, port, pts


def sdp_answer(ip, port, pt):
    sid = str(random.randint(10**6, 10**9))
    name = CODECS[pt][0]
    return ("v=0\r\n" f"o=- {sid} {sid} IN IP4 {ip}\r\n" "s=texso\r\n" f"c=IN IP4 {ip}\r\n" "t=0 0\r\n"
            f"m=audio {port} RTP/AVP {pt} 101\r\n" f"a=rtpmap:{pt} {name}/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n" "a=fmtp:101 0-15\r\n" "a=ptime:20\r\n" "a=sendrecv\r\n")


def rtp_payload(data):
    """-> (payload type, полезная нагрузка) или None."""
    if len(data) < 12 or data[0] >> 6 != 2:
        return None
    cc, ext, pt = data[0] & 0x0F, data[0] & 0x10, data[1] & 0x7F
    off = 12 + 4 * cc
    if ext and len(data) >= off + 4:
        off += 4 + 4 * struct.unpack("!H", data[off + 2:off + 4])[0]
    if data[0] & 0x20 and len(data) > off:  # padding
        data = data[:len(data) - data[-1]]
    return pt, data[off:]


# ---------- звонок ----------

class Call:
    """Один разговор: RTP в обе стороны; recv()/send() — тот же интерфейс, что у браузера для voice.talk()."""

    def __init__(self, phone, invite, addr, rtp_sock, rtp_port, remote, pt):
        self.phone, self.invite, self.addr = phone, invite, addr
        self.call_id = invite.get("call-id")
        self.sock, self.rtp_port, self.remote, self.pt = rtp_sock, rtp_port, remote, pt
        self.decode, self.encode, self.silence = CODECS[pt][1], CODECS[pt][2], CODECS[pt][3]
        self.inq = asyncio.Queue()
        self.out = bytearray()  # PCM16 8 кГц к отправке
        self.inbuf = bytearray()
        self.carry = b""
        self.rs_in = self.rs_out = None
        self.ended = asyncio.Event()
        self.local_tag = secrets.token_hex(4)
        self.acked = asyncio.Event()
        self.started = time.monotonic()
        self.cseq = 1
        self.tasks = []
        self.caller = uri_of(invite.get("from"))

    # --- голос → voice.talk ---
    async def recv(self):
        if self.ended.is_set() and self.inq.empty():
            return None
        get = asyncio.ensure_future(self.inq.get())
        end = asyncio.ensure_future(self.ended.wait())
        done, _ = await asyncio.wait({get, end}, return_when=asyncio.FIRST_COMPLETED)
        if get in done:
            end.cancel()
            return get.result()
        get.cancel()
        return None

    async def send(self, ev):
        t = ev.get("type")
        if t == "response.output_audio.delta" and ev.get("delta"):
            try:
                pcm = self.carry + base64.b64decode(ev["delta"])
            except ValueError:
                return
            cut = len(pcm) & ~1  # куски могут рваться посреди отсчёта — хвост ждёт следующего
            pcm, self.carry = pcm[:cut], pcm[cut:]
            if pcm:
                pcm, self.rs_out = audioop.ratecv(pcm, 2, 1, VOICE_RATE, 8000, self.rs_out)
                self.out += pcm
        elif t == "input_audio_buffer.speech_started":
            self.out.clear()  # заказчик перебил — замолкаем сразу
            self.carry = b""

    # --- RTP ---
    def on_rtp(self, data, addr):
        r = rtp_payload(data)
        if not r or r[0] != self.pt:
            return
        self.remote = addr  # симметричный RTP: отвечаем туда, откуда пришёл звук (NAT)
        pcm = self.decode(r[1], 2)
        pcm, self.rs_in = audioop.ratecv(pcm, 2, 1, 8000, VOICE_RATE, self.rs_in)
        self.inbuf += pcm
        if len(self.inbuf) >= VOICE_RATE * 2 // 10:  # копим по 100 мс
            self.inq.put_nowait({"type": "input_audio_buffer.append", "audio": base64.b64encode(bytes(self.inbuf)).decode()})
            self.inbuf.clear()

    async def pacer(self):
        """Шлём кадр каждые 20 мс; нечего сказать — тишина (держит NAT и таймеры ВАТС)."""
        seq, ts, ssrc = random.randint(0, 65535), random.randint(0, 2**31), random.randint(0, 2**31)
        loop = asyncio.get_running_loop()
        t = loop.time()
        first = True
        while not self.ended.is_set():
            need = FRAME * 2
            if len(self.out) >= need:
                chunk = bytes(self.out[:need])
                del self.out[:need]
                payload = self.encode(chunk, 2)
            else:
                payload = self.silence * FRAME
            hdr = struct.pack("!BBHII", 0x80, (0x80 if first else 0) | self.pt, seq, ts, ssrc)
            first = False
            if self.remote:
                try:
                    self.sock.sendto(hdr + payload, self.remote)
                except OSError:
                    pass
            seq, ts = (seq + 1) & 0xFFFF, (ts + FRAME) & 0xFFFFFFFF
            t += 0.02
            await asyncio.sleep(max(0, t - loop.time()))

    # --- SIP ---
    def hangup_local(self):
        """Агент положил трубку: BYE собеседнику."""
        if self.ended.is_set():
            return
        self.ended.set()
        inv = self.invite
        target = uri_of(inv.get("contact")) or self.caller
        routes = inv.all("record-route")
        self.cseq += 1
        hs = [("via", self.phone.via()), ("max-forwards", "70"),
              ("from", f"{inv.get('to')};tag={self.local_tag}" if "tag=" not in inv.get("to") else inv.get("to")),
              ("to", inv.get("from")), ("call-id", self.call_id), ("cseq", f"{self.cseq} BYE"), ("user-agent", UA)]
        hs += [("route", r) for r in routes]
        self.phone.send(Msg(f"BYE {target} SIP/2.0", hs), self.addr)
        self.close()

    def close(self):
        self.ended.set()
        for t in self.tasks:
            t.cancel()
        try:
            self.sock.close()
        except OSError:
            pass
        self.phone.calls.pop(self.call_id, None)

    @property
    def seconds(self):
        return time.monotonic() - self.started


class RTPProto(asyncio.DatagramProtocol):
    def __init__(self, call_ref):
        self.call_ref = call_ref

    def datagram_received(self, data, addr):
        if self.call_ref[0]:
            self.call_ref[0].on_rtp(data, addr)


# ---------- линия ----------

class Phone(asyncio.DatagramProtocol):
    """SIP-линия: регистрация, приём входящих (их шлёт ВАТС, когда заказчик снял трубку), BYE.

    on_call(call) — корутина: ведёт разговор (voice.talk) и возвращается; трубку кладём мы.
    """

    def __init__(self, server, login, password, on_call, public_ip=None, port=5070, rtp_ports=(40000, 40019),
                 expires=300, max_calls=1, accept=lambda m: True):
        user, _, domain = login.partition("@")
        host, _, sport = server.partition(":")
        self.user, self.domain = user, domain or host
        self.auth_user = user
        self.password, self.on_call = password, on_call
        self.server = (host, int(sport or 5060))
        self.public_ip, self.port = public_ip, port
        self.rtp_ports, self.expires, self.max_calls = rtp_ports, expires, max_calls
        self.granted = expires
        self.accept = accept  # accept(INVITE) -> bool: берём трубку, только если звонок поставили мы
        self.transport = None
        self.calls = {}
        self.registered = False
        self.state = "не подключена"
        self.reg_call_id, self.reg_tag, self.reg_cseq = secrets.token_hex(8), secrets.token_hex(4), 0
        self.reg_waiter = None
        self.task = None

    # --- служебное ---
    def via(self):
        return f"SIP/2.0/UDP {self.public_ip}:{self.port};branch=z9hG4bK{secrets.token_hex(8)};rport"

    def send(self, msg, addr=None):
        if self.transport:
            self.transport.sendto(msg.build(), addr or self.server)

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if not data.strip():
            return  # keep-alive
        try:
            m = Msg.parse(data)
        except Exception:
            return
        if not m.is_request:
            if m.get("call-id") == self.reg_call_id and self.reg_waiter and not self.reg_waiter.done():
                if m.status >= 200:
                    self.reg_waiter.set_result(m)
            return
        meth = m.method
        if meth == "INVITE":
            asyncio.get_running_loop().create_task(self.on_invite(m, addr))
        elif meth == "ACK":
            c = self.calls.get(m.get("call-id"))
            if c:
                c.acked.set()
        elif meth in ("BYE", "CANCEL"):
            self.reply(m, 200, "OK", addr)
            c = self.calls.get(m.get("call-id"))
            if c:
                c.close()
        elif meth in ("OPTIONS", "NOTIFY", "INFO"):
            self.reply(m, 200, "OK", addr)
        else:
            self.reply(m, 405, "Method Not Allowed", addr)

    def reply(self, req, code, text, addr, body="", to_tag=None, extra=()):
        to = req.get("to")
        if to_tag and "tag=" not in to:
            to += f";tag={to_tag}"
        hs = [("via", v) for v in req.all("via")] + [("from", req.get("from")), ("to", to),
                                                    ("call-id", req.get("call-id")), ("cseq", req.get("cseq"))]
        hs += [("record-route", r) for r in req.all("record-route")]
        hs += list(extra) + [("user-agent", UA)]
        if body:
            hs.append(("content-type", "application/sdp"))
        self.send(Msg(f"SIP/2.0 {code} {text}", hs, body), addr)

    # --- регистрация ---
    async def register_once(self):
        uri = f"sip:{self.domain}"

        def req(auth=None):
            self.reg_cseq += 1
            hs = [("via", self.via()), ("max-forwards", "70"),
                  ("from", f"<sip:{self.user}@{self.domain}>;tag={self.reg_tag}"), ("to", f"<sip:{self.user}@{self.domain}>"),
                  ("call-id", self.reg_call_id), ("cseq", f"{self.reg_cseq} REGISTER"),
                  ("contact", f"<sip:{self.user}@{self.public_ip}:{self.port}>"), ("expires", str(self.expires)),
                  ("user-agent", UA)]
            if auth:
                hs.append(auth)
            return Msg(f"REGISTER {uri} SIP/2.0", hs)

        auth = None
        for _ in range(3):
            self.reg_waiter = asyncio.get_running_loop().create_future()
            self.send(req(auth))
            try:
                r = await asyncio.wait_for(self.reg_waiter, 8)
            except asyncio.TimeoutError:
                raise RuntimeError(f"сервер {self.server[0]}:{self.server[1]} не отвечает") from None
            if r.status == 200:
                self.granted = self.granted_sec(r)
                return True
            if r.status in (401, 407):
                hdr = "www-authenticate" if r.status == 401 else "proxy-authenticate"
                name = "authorization" if r.status == 401 else "proxy-authorization"
                auth = (name, digest("REGISTER", uri, self.auth_user, self.password, r.get(hdr)))
                continue
            raise RuntimeError(f"регистрация отклонена: {r.first}")
        raise RuntimeError("неверный логин или пароль линии")

    def granted_sec(self, r):
        """На сколько секунд сервер принял линию: expires в нашем Contact, потом заголовок Expires, потом что просили.
        МегаФон даёт 120 вместо запрошенных 300 — обновляем по его сроку, иначе линия пропадает между обновлениями."""
        mine = next((p for v in (v for k, v in r.headers if k == "contact") for p in v.split(",")
                     if f"{self.public_ip}:{self.port}" in p), "")
        m = re.search(r"[;,]\s*expires\s*=\s*\"?(\d+)", mine, re.I) or re.match(r"\s*(\d+)", r.get("expires") or "")
        return min(self.expires, int(m.group(1))) if m else self.expires

    async def run(self):
        loop = asyncio.get_running_loop()
        if not self.public_ip:
            self.public_ip = await loop.run_in_executor(None, outward_ip, self.server[0])
        await loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", self.port))
        while True:
            try:
                await self.register_once()
                if not self.registered:
                    log(f"линия {self.user}@{self.domain} подключена")
                self.registered, self.state = True, "подключена"
                await asyncio.sleep(max(20, self.granted * 0.6))  # запас на потерянный пакет и повтор
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.registered, self.state = False, f"не подключена: {e}"
                log(self.state, "error")
                await asyncio.sleep(10)

    def start(self):
        self.task = asyncio.get_running_loop().create_task(self.run())
        return self.task

    def stop(self):
        if self.task:
            self.task.cancel()
        for c in list(self.calls.values()):
            c.hangup_local()
        if self.transport:
            self.transport.close()

    # --- входящий ---
    async def on_invite(self, m, addr):
        cid = m.get("call-id")
        if cid in self.calls:  # повтор INVITE или re-INVITE — отвечаем тем же
            c = self.calls[cid]
            self.reply(m, 200, "OK", addr, sdp_answer(self.public_ip, c.rtp_port, c.pt), c.local_tag,
                       [("contact", f"<sip:{self.user}@{self.public_ip}:{self.port}>")])
            return
        self.reply(m, 100, "Trying", addr)
        ip, port, pts = sdp_offer(m.body)
        pt = next((p for p in (8, 0) if p in pts), None)
        if len(self.calls) >= self.max_calls:
            return self.reply(m, 486, "Busy Here", addr, to_tag=secrets.token_hex(4))
        if not self.accept(m):  # чужой входящий (кто-то набрал внутренний номер бота) — не отвечаем
            return self.reply(m, 480, "Temporarily Unavailable", addr, to_tag=secrets.token_hex(4))
        if pt is None or not ip:
            return self.reply(m, 488, "Not Acceptable Here", addr, to_tag=secrets.token_hex(4))
        loop = asyncio.get_running_loop()
        ref = [None]
        sock = None
        for p in range(self.rtp_ports[0], self.rtp_ports[1] + 1):
            try:
                tr, _ = await loop.create_datagram_endpoint(lambda: RTPProto(ref), local_addr=("0.0.0.0", p))
                sock, rtp_port = tr, p
                break
            except OSError:
                continue
        if not sock:
            return self.reply(m, 503, "Service Unavailable", addr, to_tag=secrets.token_hex(4))
        c = Call(self, m, addr, sock, rtp_port, (ip, port), pt)
        ref[0] = c
        self.calls[cid] = c
        answer = sdp_answer(self.public_ip, rtp_port, pt)
        contact = [("contact", f"<sip:{self.user}@{self.public_ip}:{self.port}>")]
        for i in range(7):  # 200 OK до ACK (UDP теряет пакеты)
            self.reply(m, 200, "OK", addr, answer, c.local_tag, contact)
            try:
                await asyncio.wait_for(c.acked.wait(), 0.5 * 2 ** min(i, 2))
                break
            except asyncio.TimeoutError:
                continue
        log(f"входящий от {c.caller}, {CODECS[pt][0]}")
        c.tasks.append(loop.create_task(c.pacer()))
        try:
            await self.on_call(c)
        except Exception as e:
            log(f"разговор оборвался: {e}", "error")
        finally:
            c.hangup_local()


def outward_ip(host):
    """Свой IP, которым сервер выходит к ВАТС (в Docker это внутренний — тогда задайте SIP_PUBLIC_IP)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 5060))
        return s.getsockname()[0]
    finally:
        s.close()


def cfg_from_env():
    lo, _, hi = (os.environ.get("SIP_RTP_PORTS") or "40000-40019").partition("-")
    return {"server": os.environ.get("SIP_SERVER", ""), "login": os.environ.get("SIP_LOGIN", ""),
            "password": os.environ.get("SIP_PASSWORD", ""), "public_ip": os.environ.get("SIP_PUBLIC_IP") or None,
            "port": int(os.environ.get("SIP_PORT") or 5070), "rtp_ports": (int(lo), int(hi or lo))}
