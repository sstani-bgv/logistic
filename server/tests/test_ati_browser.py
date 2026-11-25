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
