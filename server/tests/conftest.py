import os
import tempfile

# До импорта main: своя временная база и фиктивный токен АТИ — тесты не трогают data.db и сеть.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="logistic-test-")
os.environ.setdefault("ATI_TOKEN", "test")
os.environ.setdefault("TELEPHONY", "mock")  # в проде по умолчанию звонков нет; тестам нужна имитация
os.environ["TZ"] = "Europe/Moscow"  # как в main.py: «сегодня» у тестов и приложения одно и то же
import time  # noqa: E402
time.tzset()

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def make_client():
    """Клиент, вошедший под пользователем с ролью role (пользователь создаётся при необходимости)."""
    import auth
    import main

    def make(role="owner", login=None):
        login = login or f"{role}1"
        if not main.q("select 1 from users where login=?", login):
            auth.create_user(main.DB, login, "password123", role)
        c = TestClient(main.app)  # без with: lifespan (мониторинг АТИ) не запускается
        assert c.post("/api/login", json={"login": login, "password": "password123"}).status_code == 200
        return c
    yield make
    auth.FAILS.clear()
    for t in ("users", "sessions", "actions"):
        main.DB.execute(f"delete from {t}")
