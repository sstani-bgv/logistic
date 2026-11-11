"""Служебные команды.

  uv run python manage.py create-owner <login>    — первый владелец (пароль спросит; или env OWNER_PASSWORD)
  uv run python manage.py reset-password <login>  — сменить пароль и выкинуть сессии
  uv run python manage.py check-ati               — проверить токен и тариф АТИ: аккаунт, поиск, мессенджер
"""
import getpass
import os
import sqlite3
import sys
from pathlib import Path

from fastapi import HTTPException

import auth


def db():
    d = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
    d.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(d / "data.db", isolation_level=None)
    c.row_factory = sqlite3.Row
    auth.init(c)
    return c


def ask_password():
    p = os.environ.get("OWNER_PASSWORD")
    if p:
        return p
    p = getpass.getpass("Пароль (от 8 символов): ")
    if p != getpass.getpass("Ещё раз: "):
        sys.exit("Пароли не совпали")
    return p


async def check_ati():
    import ati
    ok = True

    async def step(name, coro):
        nonlocal ok
        try:
            print(f"✓ {name}: {await coro}")
        except Exception as e:
            ok = False
            print(f"✗ {name}: {e}")
    await step("аккаунт (firms/contacts)", ati.me())
    truck = {"city_id": 1, "radius": 100, "body_mask": 1}  # СПб — проверка доступа к поиску
    await step("поиск грузов", _count(ati.search(truck, 1)))
    await step("мессенджер (список чатов)", _chats(ati))
    print("Тариф даёт всё нужное" if ok else "Не хватает доступа — проверьте тариф/токен АТИ")
    return ok


async def _count(coro):
    loads, total = await coro
    return f"{total} грузов в выдаче"


async def _chats(ati):
    r = await ati.client.get("https://api.ati.su/messenger/1.1/chats/", params={"num": 1})
    r.raise_for_status()
    return "доступен"


def main(argv):
    if len(argv) == 2 and argv[1] == "check-ati":
        import asyncio
        sys.exit(0 if asyncio.run(check_ati()) else 1)
    if len(argv) != 3 or argv[1] not in ("create-owner", "reset-password"):
        sys.exit(__doc__)
    cmd, login = argv[1], argv[2]
    c = db()
    try:
        if cmd == "create-owner":
            uid = auth.create_user(c, login, ask_password(), "owner")
            print(f"Владелец «{login}» создан (id {uid})")
        else:
            row = c.execute("select id from users where login=?", (login,)).fetchone()
            if not row:
                sys.exit(f"Нет пользователя «{login}»")
            p = ask_password()
            auth.check_password(p)
            c.execute("update users set password_hash=? where id=?", (auth.PH.hash(p), row["id"]))
            auth.drop_sessions(c, row["id"])
            print(f"Пароль «{login}» сменён")
    except HTTPException as e:
        sys.exit(e.detail)


if __name__ == "__main__":
    main(sys.argv)
