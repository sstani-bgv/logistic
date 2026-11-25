"""Клиент АТИ: поиск по бирже (тот же JSON, что у сайта) + официальный мессенджер API."""
import os
from contextvars import ContextVar
import httpx
from ati_browser import ATIWebAuthRequired, ATIWebError, session as browser_session

TOKEN = os.environ.get("ATI_TOKEN", "")  # без токена модуль импортируется (тесты, мок), запросы вернут 401
# ATI требует код фирмы интегратора в User-Agent. client_id приложения используется при выдаче токена.
CLIENT_ID_SET = bool(os.environ.get("ATI_CLIENT_ID"))
CLIENT_ID = os.environ.get("ATI_CLIENT_ID") or "5464422"  # без своего ID — ID интегратора (видно в health и check-ati)
HEAD = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json",
        "User-Agent": f"ati_integrator_{os.environ.get('ATI_INTEGRATOR_ID', '5464422')}"}
client = httpx.AsyncClient(headers=HEAD, timeout=30, follow_redirects=True)
search_proxy = None
browser_config = None
search_response_bytes = ContextVar("search_response_bytes", default=0)


async def search(truck, page):
    body = {"page": page, "items_per_page": 10, "exclude_geo_dicts": True,
            "filter": {"from": {"id": truck["city_id"], "type": 2, "radius": min(truck["radius"], 999)},
                       "dates": {"date_option": "today-plus"}, "truck_type": truck["body_mask"],
                       "sorting_type": 2, "extra_params": 0}}
    if truck.get("to_city_id") and not truck.get("to_district"):  # округ отсекаем сами (scoring.district)
        body["filter"]["to"] = {"id": truck["to_city_id"], "type": 2, "radius": min(truck.get("to_radius") or 150, 999)}
    if not truck["body_mask"]:
        body["filter"].pop("truck_type")
    if truck.get("capacity"):
        body["filter"]["weight"] = {"max": truck["capacity"]}
    if browser_config and browser_config.get("enabled"):
        loads, total, response_bytes = await browser_session.search(body, browser_config)
        search_response_bytes.set(response_bytes)
        return loads, total
    # Публичная выдача — без ставок, фирм и контактов. Полную включаем, когда АТИ откроет к ней доступ токену (сейчас 403).
    path = "v1.0" if os.environ.get("ATI_SEARCH_FULL") == "1" else "public/v1.0"
    url = f"https://loads.ati.su/webapi/{path}/loads/search"
    if search_proxy:
        async with httpx.AsyncClient(proxy=search_proxy, headers=HEAD, timeout=30) as proxy_client:
            r = await proxy_client.post(url, json=body)
    else:
        r = await client.post(url, json=body)
    r.raise_for_status()
    search_response_bytes.set(len(r.content))
    j = r.json()
    loads = j.get("loads", [])
    for load in loads:
        load["publicOnly"] = j.get("isUserAuthorized") is False
    return loads, j.get("totalItems", 0)


async def me():
    r = await client.get("https://api.ati.su/v1.0/firms/contacts")
    r.raise_for_status()
    c = r.json()[0]
    return {"ati_id": c["ati_id"], "contact_id": c["id"], "name": c["name"]}


async def open_chat(alias, name, firm_name):
    """alias = '{код фирмы}.{id контакта}' из выдачи. Повторный вызов вернёт тот же диалог."""
    r = await client.post("https://api.ati.su/messenger/1.1/chats/",
                          json={"channel_type": "dialog", "ati_id": alias, "name": name, "description": firm_name})
    r.raise_for_status()
    return r.json()["id"]


async def send(chat_id, text):
    r = await client.post(f"https://api.ati.su/messenger/1.2/chats/{chat_id}/messages",
                          files={"text": (None, text)})  # АТИ принимает только multipart
    r.raise_for_status()
    return r.json()


async def history(chat_id, since=None):
    params = {"num": 50, **({"since": since} if since else {})}
    r = await client.get(f"https://api.ati.su/messenger/1.1/chats/{chat_id}/history/", params=params)
    r.raise_for_status()
    return r.json()

