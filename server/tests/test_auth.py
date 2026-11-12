import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import auth
import main


@pytest.fixture
def anon():
    return TestClient(main.app)


def test_api_closed_without_login(anon):
    for method, path in [("get", "/api/stats"), ("get", "/api/loads"), ("get", "/api/trucks"), ("get", "/api/rules"),
                         ("get", "/api/dialogs"), ("get", "/api/settings"), ("put", "/api/settings"),
                         ("get", "/api/users"), ("get", "/api/me"), ("get", "/api/nope")]:
        assert getattr(anon, method)(path).status_code == 401, path
    assert anon.get("/api/health").status_code == 200


def test_login_cookie_flags(make_client, anon):
    make_client("owner", "boss")
    r = anon.post("/api/login", json={"login": "BOSS", "password": "password123"})  # логин без учёта регистра
    assert r.status_code == 200 and r.json()["role"] == "owner"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert anon.get("/api/me").json()["login"] == "boss"


def test_session_token_not_stored_plain(make_client):
    c = make_client("owner")
    token = c.cookies.get(auth.COOKIE)
    stored = [r["token_hash"] for r in main.q("select token_hash from sessions")]
    assert token not in stored and len(stored[0]) == 64


def test_wrong_password_and_lockout(make_client, anon):
    make_client("logist", "ivan")
    for _ in range(auth.LOCK_FAILS):
        assert anon.post("/api/login", json={"login": "ivan", "password": "wrong-pass"}).status_code == 401
    r = anon.post("/api/login", json={"login": "ivan", "password": "password123"})
    assert r.status_code == 429
    assert anon.post("/api/login", json={"login": "nobody", "password": "x"}).status_code == 401


def test_logout(make_client):
    c = make_client("logist")
    assert c.post("/api/logout").status_code == 200
    assert c.get("/api/me").status_code == 401


def test_logist_cannot_owner_things(make_client):
    c = make_client("logist")
    assert c.get("/api/trucks").status_code == 200
    assert c.get("/api/settings").status_code == 200  # читать можно — фронту нужны лимиты
    assert c.put("/api/settings", json={"threshold": 10}).status_code == 403
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/users", json={"login": "x", "password": "password123"}).status_code == 403
    assert c.get("/api/actions").status_code == 403


def test_owner_manages_users(make_client, anon):
    c = make_client("owner")
    r = c.post("/api/users", json={"login": "petr", "password": "password123", "role": "logist"})
    assert r.status_code == 200
    uid = r.json()["id"]
    assert c.post("/api/users", json={"login": "Petr", "password": "password123"}).status_code == 409
    assert c.post("/api/users", json={"login": "a", "password": "short"}).status_code == 400
    assert c.post("/api/users", json={"login": "a", "password": "password123", "role": "admin"}).status_code == 400

    p = TestClient(main.app)
    assert p.post("/api/login", json={"login": "petr", "password": "password123"}).status_code == 200
    assert c.patch(f"/api/users/{uid}", json={"active": False}).status_code == 200
    assert p.get("/api/me").status_code == 401  # деактивация выкидывает сессии
    assert anon.post("/api/login", json={"login": "petr", "password": "password123"}).status_code == 401

    c.patch(f"/api/users/{uid}", json={"active": True, "password": "newpassword1"})
    assert anon.post("/api/login", json={"login": "petr", "password": "newpassword1"}).status_code == 200


def test_last_owner_protected(make_client):
    c = make_client("owner")
    me = c.get("/api/me").json()
    assert c.patch(f"/api/users/{me['id']}", json={"role": "logist"}).status_code == 400
    assert c.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 400


def test_actions_record_author(make_client):
    lg = make_client("logist", "olga")
    for t in ("trucks", "rules"):
        main.DB.execute(f"delete from {t}")
    tid = main.DB.execute("insert into trucks(name,city_name,city_id,lat,lon,radius,body_mask,body_name,height,capacity,"
                          "free_from,active,status) values('T','СПб',1,59.9,30.3,150,1,'тент',2.7,20,'2026-01-01',1,'free')"
                          ).lastrowid
    assert lg.patch(f"/api/trucks/{tid}", json={"status": "busy"}).status_code == 200  # sync-эндпоинт
    owner = make_client("owner")
    acts = owner.get("/api/actions").json()
    assert acts[0]["login"] == "olga" and acts[0]["action"] == "truck_busy" and acts[0]["ref"] == str(tid)


def test_cli_create_owner(tmp_path):
    env = {"DATA_DIR": str(tmp_path), "OWNER_PASSWORD": "password123", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "manage.py", "create-owner", "admin"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, "manage.py", "create-owner", "admin"], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "занят" in r.stderr


def test_docs_closed(anon):
    for p in ("/docs", "/redoc", "/openapi.json"):
        r = anon.get(p)
        assert r.status_code == 404 or "openapi" not in r.text.lower(), p


def test_ip_lockout_across_logins(make_client, anon):
    make_client("logist", "real")
    for i in range(auth.IP_FAILS):
        anon.post("/api/login", json={"login": f"guess{i}", "password": "x"})
    assert anon.post("/api/login", json={"login": "real", "password": "password123"}).status_code == 429


def test_long_password_rejected(anon):
    assert anon.post("/api/login", json={"login": "a", "password": "x" * 1000}).status_code == 422


def test_spa_static_no_traversal(anon):
    import main as m
    if not m.DIST.exists():
        pytest.skip("фронт не собран")
    assert anon.get("/vite.svg").headers["content-type"].startswith("image/svg")
    r = anon.get("/..%2F..%2Fserver%2Fmain.py")
    assert "import" not in r.text


def test_goal_presets(make_client):
    owner, lg = make_client("owner"), make_client("logist", "olga")
    got = lg.get("/api/settings").json()
    assert set(got) == {"default_goal", "goal_presets", "send_enabled", "auto_start", "call_enabled", "voice_ready", "default_goal_text"} and len(got["goal_presets"]) == 3  # логисту — только сценарии
    r = owner.put("/api/settings", json={"goal_presets": ["  Узнать ставку  ", "", "Нужна ли доверенность"]})
    assert r.status_code == 200 and r.json()["goal_presets"] == ["Узнать ставку", "Нужна ли доверенность"]
    assert lg.get("/api/settings").json()["goal_presets"] == ["Узнать ставку", "Нужна ли доверенность"]
    assert lg.put("/api/settings", json={"goal_presets": []}).status_code == 403
    for bad in ("строка", [1, 2], ["x" * 1001], ["a"] * 21):
        assert owner.put("/api/settings", json={"goal_presets": bad}).status_code == 400, bad
    assert owner.put("/api/settings", json={"default_goal": "  "}).status_code == 400
