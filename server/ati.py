"""Клиент АТИ: поиск по бирже (тот же JSON, что у сайта) + официальный мессенджер API."""
import os
import httpx

TOKEN = os.environ.get("ATI_TOKEN", "")  # без токена модуль импортируется (тесты, мок), запросы вернут 401
HEAD = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json",
        "User-Agent": "ati_integrator_5464422"}
client = httpx.AsyncClient(headers=HEAD, timeout=30, follow_redirects=True)


async def search(truck, page):
    body = {"page": page, "items_per_page": 10, "exclude_geo_dicts": True,
            "filter": {"from": {"id": truck["city_id"], "type": 2, "radius": truck["radius"]},
                       "dates": {"date_option": "today-plus"}, "truck_type": truck["body_mask"],
                       "sorting_type": 2, "extra_params": 0}}
    if truck.get("to_city_id"):
        body["filter"]["to"] = {"id": truck["to_city_id"], "type": 2, "radius": 150}
    r = await client.post("https://loads.ati.su/webapi/v1.0/loads/search", json=body)
    r.raise_for_status()
    j = r.json()
    return j.get("loads", []), j.get("totalItems", 0)


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
