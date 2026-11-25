"""Authenticated ATI web session used only for the loads exchange.

The official ATI API client remains in :mod:`ati`.  ATI does not expose the
loads exchange through that API, so this module keeps a normal browser session
and performs the same search request as the signed-in web application.
"""
from __future__ import annotations

import asyncio
import contextlib
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
CODE_FIELDS = ["input[autocomplete='one-time-code']", "input[name*='code' i]", "input[inputmode='numeric']"]


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
        # браузер мог уже упасть — закрываем что осталось, иначе висящий Firefox съедает память, а следующий запуск — ещё столько же
        for closing in (self._context and self._context.close, self._browser and self._browser.close,
                        self._playwright and self._playwright.stop):
            if closing:
                with contextlib.suppress(Exception):
                    await closing()
        self._playwright = self._browser = self._context = self._page = None
        self._config_fingerprint = ""

    @contextlib.asynccontextmanager
    async def _locked(self):
        """Один запрос к браузеру за раз; любой сбой браузера (таймаут, падение) — закрыть его и понятная ошибка вместо 500."""
        async with self.lock:
            try:
                yield
            except ATIWebError as error:
                print(f"[ati] {error} · {self._page.url if self._page else '-'}", flush=True)
                if self._page:
                    await self._snapshot(self._page)
                raise
            except Exception as error:
                print(f"[ati] сбой браузера: {type(error).__name__}: {str(error)[:300]} · {self._page.url if self._page else '-'}", flush=True)
                if self._page:
                    await self._snapshot(self._page)
                await self.close()
                raise ATIWebError(f"ATI не ответил вовремя, попробуйте ещё раз ({type(error).__name__})") from error

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
        self._page.set_default_timeout(60_000)  # слабый сервер: Firefox грузит страницы АТИ десятки секунд
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
        async with self._locked():
            page = await self._ensure(config)
            if code:
                field = await self._first_visible(page, CODE_FIELDS)
                if not field:
                    raise ATIWebError("Форма кода подтверждения ATI не найдена — нажмите «Войти и проверить» ещё раз")
                await field.fill(code)
                await self._submit(field, page)
            elif await self._authorized(page, navigate=True):
                return await self._connected()
            else:
                username, password = config.get("username", ""), config.get("password", "")
                if not username or not password:
                    raise ATIWebAuthRequired("Укажите логин и пароль ATI")
                await page.goto(LOGIN_URL, wait_until="domcontentloaded")
                login = await self._first_visible(page, [
                    "input[autocomplete='username']", "input[name*='login' i]", "input[name='username']", "input[type='email']", "input[type='text']"
                ])
                secret = await self._first_visible(page, ["input[autocomplete='current-password']", "input[type='password']"])
                if not login or not secret:
                    raise ATIWebError("ATI изменил форму входа — поля логина и пароля не найдены")
                await login.fill(username)
                await secret.fill(password)
                # Enter в поле пароля, а не клик по «Войти»: в шапке АТИ есть скрытая кнопка «Войти», клик по ней висит до таймаута
                await self._submit(secret, page)

            if await self._first_visible(page, CODE_FIELDS):
                await self._snapshot(page)
                return self._status("verification_required", page)
            body = (await page.locator("body").inner_text())[:2_000].lower()
            if "captcha" in body or "капч" in body:
                await self._snapshot(page)
                return self._status("manual_required", page)
            if "id.ati.su/login" not in page.url and await self._authorized(page, navigate=True):
                return await self._connected()
            await self._snapshot(page)
            return self._status("login_failed", page)

    def _status(self, state: str, page: Page) -> dict[str, Any]:
        print(f"[ati] вход: {state} · {page.url}", flush=True)
        self.last_status = {"connected": state == "connected", "state": state}
        return self.last_status

    async def _connected(self) -> dict[str, Any]:
        await self._save_state()
        assert self._page
        return self._status("connected", self._page)

    @staticmethod
    async def _submit(field, page: Page) -> None:
        """Отправить форму и дождаться, что АТИ ответил: ушёл со страницы или нарисовал поле кода / ошибку."""
        before = page.url
        await field.press("Enter")
        with contextlib.suppress(Exception):
            await page.wait_for_url(lambda url: url != before, timeout=20_000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1_500)  # поле кода и текст ошибки АТИ рисует скриптом

    @staticmethod
    async def _snapshot(page: Page) -> None:
        """Снимок неудачного входа для разбора: /data/ati-login-last.png (без паролей — поля уже отправлены)."""
        with contextlib.suppress(Exception):
            await page.screenshot(path=str(_state_path().with_name("ati-login-last.png")))

    async def search(self, body: dict[str, Any], config: dict[str, Any]) -> tuple[list[dict], int, int]:
        async with self._locked():
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
