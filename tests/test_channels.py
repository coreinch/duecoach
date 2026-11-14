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
    assert len(channel.app.handlers[0]) == 3  # text, voice and other media handlers


async def test_health_endpoint_shows_the_model(monkeypatch):
    from duecoach import llm

    monkeypatch.setattr(bot, "_heartbeat", {})
    app = web.Application()
    app.add_routes([web.get("/healthz", bot.healthz)])
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/healthz")
        body = await response.json()
        assert response.status == 200 and body["llm"] == {"model": llm.LLM_MODEL}


async def test_a_telegram_voice_note_is_downloaded_only_when_core_asks_and_the_reply_is_sent(monkeypatch):
    from types import SimpleNamespace as NS

    from duecoach import core
    from duecoach.channels.telegram import TelegramChannel

    sent, downloads, seen = [], [], {}

    async def get_file():
        async def download_as_bytearray():
            downloads.append(1)
            return bytearray(b"ogg")

        return NS(download_as_bytearray=download_as_bytearray)

    async def reply_text(text):
        sent.append(text)

    async def send_action(action):
        return None

    async def fake_handle_voice(channel, ext_id, chat_id, seconds, fetch, lang):
        seen.update(seconds=seconds, before=len(downloads), audio=await fetch(), lang=lang)
        return core.VoiceResult("heard you", None)

    monkeypatch.setattr(core, "handle_voice", fake_handle_voice)
    update = NS(
        effective_user=NS(id=1, language_code="el"),
        effective_chat=NS(id=2, send_action=send_action),
        message=NS(voice=NS(duration=7, get_file=get_file), reply_text=reply_text),
    )
    await TelegramChannel("123456:TEST-TOKEN")._on_voice(update, None)
    assert sent == ["heard you"] and seen == {"seconds": 7, "before": 0, "audio": b"ogg", "lang": "el"}


async def test_a_spoken_reply_follows_the_text_and_a_failure_to_speak_never_loses_the_text(monkeypatch):
    from types import SimpleNamespace as NS

    from duecoach import core, tts
    from duecoach.channels.telegram import TelegramChannel

    texts, voices = [], []

    async def reply_text(text):
        texts.append(text)

    async def reply_voice(voice):
        voices.append(voice.read())

    async def send_action(action):
        return None

    async def fake_handle_voice(channel, ext_id, chat_id, seconds, fetch, lang):
        return core.VoiceResult("🎤 “hi”\n\nGot it.", "Got it.", "el")

    asked = []

    async def synthesize(text, lang):
        asked.append((text, lang))
        return b"opus"

    monkeypatch.setattr(core, "handle_voice", fake_handle_voice)
    monkeypatch.setattr(tts, "enabled", lambda: True)
    monkeypatch.setattr(tts, "synthesize", synthesize)
    update = NS(
        effective_user=NS(id=1, language_code="en"),
        effective_chat=NS(id=2, send_action=send_action),
        message=NS(voice=NS(duration=3, get_file=None), reply_text=reply_text, reply_voice=reply_voice),
    )
    channel = TelegramChannel("123456:TEST-TOKEN")
    await channel._on_voice(update, None)
    assert texts == ["🎤 “hi”\n\nGot it."] and voices == [b"opus"] and asked == [("Got it.", "el")]

    async def broken(text, lang):
        raise RuntimeError("tts down")

    monkeypatch.setattr(tts, "synthesize", broken)
    texts.clear()
    await channel._on_voice(update, None)  # must not raise
    assert texts == ["🎤 “hi”\n\nGot it."]
