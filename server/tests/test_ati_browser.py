import asyncio

import pytest

from ati_browser import ATIWebAuthRequired, ATIWebError, ATIWebSession


class FakePage:
    def __init__(self, result):
        self.result = result

    async def evaluate(self, script, payload=None):
        return self.result


def test_authenticated_search_returns_private_result_and_counts_payload(monkeypatch):
    session = ATIWebSession()
    body = '{"loads":[{"id":"1"}],"totalItems":12,"isUserAuthorized":true}'

    async def ensure(config):
        return FakePage({"status": 200, "text": body})

    async def authorized(page, navigate=False):
        return True

    async def save():
        return None

    monkeypatch.setattr(session, "_ensure", ensure)
    monkeypatch.setattr(session, "_authorized", authorized)
    monkeypatch.setattr(session, "_save_state", save)
    loads, total, size = asyncio.run(session.search({"page": 1}, {"enabled": True}))
    assert total == 12 and loads[0]["publicOnly"] is False
    assert size == len(body.encode())


def test_authenticated_search_rejects_public_fallback(monkeypatch):
    session = ATIWebSession()

    async def ensure(config):
        return FakePage({"status": 200, "text": '{"loads":[],"isUserAuthorized":false}'})

    async def authorized(page, navigate=False):
        return True

    monkeypatch.setattr(session, "_ensure", ensure)
    monkeypatch.setattr(session, "_authorized", authorized)
    monkeypatch.setattr(session, "_save_state", lambda: None)
    with pytest.raises(ATIWebAuthRequired):
        asyncio.run(session.search({}, {"enabled": True}))


def test_browser_failure_closes_it_and_reports_readable_error(monkeypatch):
    session = ATIWebSession()
    closed = []

    async def ensure(config):
        raise TimeoutError("Page.goto: Timeout 60000ms exceeded")

    async def close():
        closed.append(True)

    monkeypatch.setattr(session, "_ensure", ensure)
    monkeypatch.setattr(session, "close", close)
    with pytest.raises(ATIWebError, match="ATI не ответил вовремя"):
        asyncio.run(session.login({"enabled": True}))
    assert closed  # повисший Firefox не остаётся в памяти


def test_search_does_not_leave_the_code_page(monkeypatch):
    session = ATIWebSession()
    session.last_status = {"connected": False, "state": "verification_required"}

    async def ensure(config):
        raise AssertionError("поиск не должен трогать браузер, пока ATI ждёт код")

    monkeypatch.setattr(session, "_ensure", ensure)
    with pytest.raises(ATIWebAuthRequired, match="код"):
        asyncio.run(session.search({}, {"enabled": True}))


def test_fingerprint_is_kept_between_launches(monkeypatch, tmp_path):
    import ati_browser
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    draws = iter(["first", "second"])

    def fake_launch_options(**kw):
        n = next(draws)
        return {"env": {"API_KEY": "secret", "CAMOU_CONFIG_1": n}, "firefox_user_prefs": {"id": n}, "executable_path": f"/bin/{n}"}

    monkeypatch.setattr(ati_browser, "launch_options", fake_launch_options)
    one = ati_browser._launch_options({})
    two = ati_browser._launch_options({})
    assert two["env"]["CAMOU_CONFIG_1"] == one["env"]["CAMOU_CONFIG_1"] == "first"  # то же «устройство»
    assert two["firefox_user_prefs"] == {"id": "first"} and two["executable_path"] == "/bin/second"
    assert "secret" not in (tmp_path / "ati-browser-fingerprint.json").read_text()  # ключи API в файл не попадают
