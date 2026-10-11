import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from duecoach import bot


async def test_health_endpoint_reports_stale_loops(monkeypatch):
    app = web.Application()
    app.add_routes([web.get("/healthz", bot.healthz)])
    async with TestClient(TestServer(app)) as client:
        monkeypatch.setattr(bot, "_heartbeat", {"reminders": time.time()})
        assert (await client.get("/healthz")).status == 200
        monkeypatch.setattr(bot, "_heartbeat", {"reminders": 1.0})
        response = await client.get("/healthz")
        assert response.status == 503 and (await response.json())["stale_loops"] == ["reminders"]


def test_telegram_channel_builds_with_concurrent_updates_and_handles_media():
    from duecoach.channels.telegram import TelegramChannel

    channel = TelegramChannel("123456:TEST-TOKEN")
    assert channel.app.concurrent_updates  # one user's slow reply must not block the others
    assert len(channel.app.handlers[0]) == 2  # text and media handlers


async def test_health_endpoint_shows_the_model(monkeypatch):
    from duecoach import llm

    monkeypatch.setattr(bot, "_heartbeat", {})
    app = web.Application()
    app.add_routes([web.get("/healthz", bot.healthz)])
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/healthz")
        body = await response.json()
        assert response.status == 200 and body["llm"] == {"model": llm.LLM_MODEL}
