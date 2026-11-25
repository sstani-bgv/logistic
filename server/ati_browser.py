"""Authenticated ATI web session used only for the loads exchange.

The official ATI API client remains in :mod:`ati`.  ATI does not expose the
loads exchange through that API, so this module keeps a normal browser session
and performs the same search request as the signed-in web application.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from camoufox.async_api import AsyncNewBrowser
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


LOGIN_URL = "https://id.ati.su/login/"
LOADS_URL = "https://loads.ati.su/"
SEARCH_URL = "https://loads.ati.su/webapi/v1.0/loads/search"
BLOCKED_TYPES = {"image", "media", "font"}


class ATIWebError(RuntimeError):
    pass


class ATIWebAuthRequired(ATIWebError):
    pass


class ATIWebVerificationRequired(ATIWebError):
    pass


def _state_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "ati-browser-state.json"


def _proxy(config: dict[str, Any]) -> dict[str, str] | None:
    proxy = config.get("proxy") or {}
    if not proxy.get("enabled"):
        return None
    value = {"server": proxy.get("url", "")}
    if proxy.get("username"):
        value["username"] = proxy["username"]
        value["password"] = proxy.get("password", "")
    return value


class ATIWebSession:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._config_fingerprint = ""
        self.last_status: dict[str, Any] = {"connected": False, "state": "not_checked"}

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None
        self._config_fingerprint = ""

    async def _ensure(self, config: dict[str, Any]) -> Page:
        fingerprint = json.dumps({"proxy": _proxy(config)}, sort_keys=True)
        if self._page and self._config_fingerprint == fingerprint:
            return self._page
        await self.close()
        self._playwright = await async_playwright().start()
        # Camoufox — Firefox с подменой отпечатка: headless Chromium АТИ распознаёт как робота.
        # Размер окна и прочий отпечаток задаёт сам Camoufox, свой viewport не ставим — иначе отпечаток не сходится.
        launch: dict[str, Any] = {"headless": True, "os": "linux", "locale": "ru-RU"}  # ОС как у сервера: иначе Camoufox шумит canvas, это заметно
        if _proxy(config):
            launch["proxy"] = _proxy(config)
        self._browser = await AsyncNewBrowser(self._playwright, **launch)
        context: dict[str, Any] = {"service_workers": "block"}
        state = _state_path()
        if state.exists():
            context["storage_state"] = str(state)
        self._context = await self._browser.new_context(**context)

        async def route_request(route) -> None:
            url = route.request.url.lower()
            if route.request.resource_type in BLOCKED_TYPES or any(
                marker in url for marker in ("google-analytics", "metrika", "doubleclick", "mc.yandex")
            ):
                await route.abort()
            else:
                await route.continue_()

        await self._context.route("**/*", route_request)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(15_000)
        self._config_fingerprint = fingerprint
        return self._page

    async def _save_state(self) -> None:
        assert self._context
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(path), indexed_db=True)
        path.chmod(0o600)

    async def _authorized(self, page: Page, navigate: bool = False) -> bool:
        if navigate and "loads.ati.su" not in page.url:
            await page.goto(LOADS_URL, wait_until="domcontentloaded")
        if "loads.ati.su" not in page.url:
            return False
        try:
            return bool(await page.evaluate("() => Boolean(window.atiUser && window.atiUser.is_user)"))
        except Exception:
            return False

    @staticmethod
    async def _first_visible(page: Page, selectors: list[str]):
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                return locator
        return None

    async def login(self, config: dict[str, Any], code: str = "") -> dict[str, Any]:
        async with self.lock:
            page = await self._ensure(config)
            if code:
                field = await self._first_visible(page, [
                    "input[autocomplete='one-time-code']", "input[name*='code' i]", "input[inputmode='numeric']"
                ])
                if not field:
                    raise ATIWebError("Форма кода подтверждения ATI не найдена")
                await field.fill(code)
                button = page.get_by_role("button", name="Продолжить").or_(
                    page.get_by_role("button", name="Войти")
                ).first
                await button.click()
                await page.wait_for_timeout(1_000)
            elif not await self._authorized(page, navigate=True):
                username, password = config.get("username", ""), config.get("password", "")
                if not username or not password:
                    raise ATIWebAuthRequired("Укажите логин и пароль ATI")
                await page.goto(LOGIN_URL, wait_until="domcontentloaded")
                login = await self._first_visible(page, [
                    "input[autocomplete='username']", "input[name*='login' i]", "input[type='email']", "input[type='text']"
                ])
                secret = await self._first_visible(page, ["input[autocomplete='current-password']", "input[type='password']"])
                if not login or not secret:
                    raise ATIWebError("ATI изменил форму входа — поля логина и пароля не найдены")
                await login.fill(username)
                await secret.fill(password)
                button = page.get_by_role("button", name="Войти").first
                if not await button.count():
                    button = page.locator("button[type='submit'], input[type='submit']").first
                await button.click()
                await page.wait_for_timeout(1_000)

            if await self._authorized(page):
                await self._save_state()
                self.last_status = {"connected": True, "state": "connected"}
                return self.last_status
            verification = await self._first_visible(page, [
                "input[autocomplete='one-time-code']", "input[name*='code' i]", "input[inputmode='numeric']"
            ])
            if verification:
                self.last_status = {"connected": False, "state": "verification_required"}
                return self.last_status
            body = (await page.locator("body").inner_text())[:2_000]
            if "captcha" in body.lower() or "капч" in body.lower():
                self.last_status = {"connected": False, "state": "manual_required"}
                return self.last_status
            self.last_status = {"connected": False, "state": "login_failed"}
            return self.last_status

    async def search(self, body: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict], int, int]:
        async with self.lock:
            page = await self._ensure(config)
            if not await self._authorized(page, navigate=True):
                raise ATIWebAuthRequired("Сессия ATI не подключена. Откройте Настройки → ATI и выполните вход.")
            result = await page.evaluate(
                """async ({url, body}) => {
                    const response = await fetch(url, {
                        method: 'POST', credentials: 'include',
                        headers: {'content-type': 'application/json', 'accept': 'application/json'},
                        body: JSON.stringify(body)
                    });
                    const text = await response.text();
                    return {status: response.status, text};
                }""",
                {"url": SEARCH_URL, "body": body},
            )
            if result["status"] in (401, 403):
                self.last_status = {"connected": False, "state": "expired"}
                raise ATIWebAuthRequired("Сессия ATI истекла — выполните вход заново")
            if result["status"] >= 400:
                raise ATIWebError(f"ATI вернул ошибку {result['status']}")
            data = json.loads(result["text"])
            if data.get("isUserAuthorized") is False:
                self.last_status = {"connected": False, "state": "expired"}
                raise ATIWebAuthRequired("ATI вернул публичную выдачу — выполните вход заново")
            await self._save_state()
            loads = data.get("loads", [])
            for load in loads:
                load["publicOnly"] = False
            return loads, data.get("totalItems", 0), len(result["text"].encode())


session = ATIWebSession()
