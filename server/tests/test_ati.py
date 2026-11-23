import asyncio

import httpx

import ati


def test_search_uses_current_public_endpoint_and_keeps_access_limit(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/webapi/v1.0/loads/search":
            return httpx.Response(403, json={"error": "access_denied"})
        assert request.url.path == "/webapi/public/v1.0/loads/search"
        return httpx.Response(200, json={"loads": [{"id": "real-load"}], "totalItems": 12,
                                       "isUserAuthorized": False})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(ati, "client", client)
            return await ati.search({"city_id": 1, "radius": 100, "body_mask": 1}, 1)

    loads, total = asyncio.run(run())
    assert total == 12 and loads == [{"id": "real-load", "publicOnly": True}]
    assert len(requests) == 1
