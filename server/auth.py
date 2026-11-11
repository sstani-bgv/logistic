"""Логины и роли (ТЗ 2.5): owner — всё; logist — грузы, переговоры, машины, правила.

Сессия — случайный токен в httpOnly cookie, в БД храним только его sha256. Пароли — argon2.
"""
import hashlib
import os
import secrets
import time
from contextvars import ContextVar

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

ROLES = ("owner", "logist")
COOKIE = "session"
SESSION_DAYS = 30
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"  # на проде за HTTPS — 1
LOCK_FAILS, LOCK_SECONDS = 5, 15 * 60  # 5 неверных паролей — пауза 15 минут на логин

PH = PasswordHasher()
CURRENT: ContextVar[dict | None] = ContextVar("current_user", default=None)
PUBLIC = {"/api/login", "/api/logout", "/api/health"}
FAILS: dict[str, list[float]] = {}

SCHEMA = """
create table if not exists users(id integer primary key, login text unique collate nocase, password_hash text,
  role text, active int default 1, created real);
create table if not exists sessions(token_hash text primary key, user_id int, created real, expires real);
create table if not exists actions(id integer primary key, ts real, user_id int, login text, action text,
  ref text, detail text);
"""


def init(db):
    db.executescript(SCHEMA)


def _hash_token(t):
    return hashlib.sha256(t.encode()).hexdigest()


def public_user(u):
    return {k: u[k] for k in ("id", "login", "role", "active")}


def check_role(role):
    if role not in ROLES:
        raise HTTPException(400, f"Роль: {' или '.join(ROLES)}")


def check_password(p):
    if len(p or "") < 8:
        raise HTTPException(400, "Пароль — не короче 8 символов")


def create_user(db, login, password, role):
    login = (login or "").strip()
    if not login:
        raise HTTPException(400, "Нужен логин")
    check_role(role)
    check_password(password)
    if db.execute("select 1 from users where login=?", (login,)).fetchone():
        raise HTTPException(409, f"Логин «{login}» уже занят")
    cur = db.execute("insert into users(login,password_hash,role,active,created) values(?,?,?,1,?)",
                     (login, PH.hash(password), role, time.time()))
    return cur.lastrowid


def _locked(login):
    now = time.time()
    FAILS[login] = [t for t in FAILS.get(login, []) if now - t < LOCK_SECONDS]
    return len(FAILS[login]) >= LOCK_FAILS


def login(db, login, password):
    """-> (token, user). Одинаковый ответ на «нет логина» и «неверный пароль»."""
    key = (login or "").strip().lower()
    if _locked(key):
        raise HTTPException(429, "Слишком много попыток входа, подождите 15 минут")
    row = db.execute("select * from users where login=?", (key,)).fetchone()
    ok = False
    if row and row["active"]:
        try:
            ok = PH.verify(row["password_hash"], password or "")
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            ok = False
    if not ok:
        FAILS.setdefault(key, []).append(time.time())
        raise HTTPException(401, "Неверный логин или пароль")
    FAILS.pop(key, None)
    if PH.check_needs_rehash(row["password_hash"]):
        db.execute("update users set password_hash=? where id=?", (PH.hash(password), row["id"]))
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute("insert into sessions values(?,?,?,?)", (_hash_token(token), row["id"], now, now + SESSION_DAYS * 86400))
    db.execute("delete from sessions where expires<?", (now,))
    return token, dict(row)


def logout(db, token):
    if token:
        db.execute("delete from sessions where token_hash=?", (_hash_token(token),))


def user_by_token(db, token):
    if not token:
        return None
    row = db.execute("select u.* from sessions s join users u on u.id=s.user_id where s.token_hash=? and s.expires>?"
                     " and u.active=1", (_hash_token(token), time.time())).fetchone()
    return dict(row) if row else None


def set_cookie(resp, token):
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=COOKIE_SECURE, path="/")


def make_guard(db):
    async def guard(request: Request):
        """Зависимость на всё приложение: /api/* только после входа (кроме PUBLIC)."""
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC:
            return None
        u = user_by_token(db, request.cookies.get(COOKIE))
        if not u:
            raise HTTPException(401, "Нужно войти")
        CURRENT.set(u)
        return u
    return guard


def current():
    return CURRENT.get()


def require_owner():
    u = CURRENT.get()
    if not u or u["role"] != "owner":
        raise HTTPException(403, "Только для владельца")
    return u


def record(db, action, ref=None, detail=None):
    """Журнал ручных действий: кто отказал, добавил правило, взял груз и т.д."""
    u = CURRENT.get() or {}
    db.execute("insert into actions(ts,user_id,login,action,ref,detail) values(?,?,?,?,?,?)",
               (time.time(), u.get("id"), u.get("login", "система"), action, None if ref is None else str(ref), detail))
    return u.get("login", "система")


def owners_left(db, excluding_id):
    return db.execute("select count(*) from users where role='owner' and active=1 and id!=?",
                      (excluding_id,)).fetchone()[0]


def drop_sessions(db, user_id):
    db.execute("delete from sessions where user_id=?", (user_id,))
