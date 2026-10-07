import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from coach import bot, channels, core, db
from coach.channels.viber import ViberChannel
from coach.channels.whatsapp import WhatsAppChannel


@pytest.fixture
def api():
    """Fake Viber/WhatsApp HTTP APIs: records every outgoing request."""
    requests = []

    def handler(request: httpx.Request):
        requests.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"status": 0})

    return requests, httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def replies(monkeypatch):
    async def fake_handle_text(channel, ext_id, chat_id, text, lang_hint="en"):
        db.get_or_create_user(channel, ext_id, chat_id, lang_hint)
        db.set_field(db.get_user_by_identity(channel, ext_id)["user_id"], "last_inbound", __import__("time").time())
        return f"echo: {text}"

    monkeypatch.setattr(core, "handle_text", fake_handle_text)


def viber_sig(body: bytes) -> dict:
    return {"X-Viber-Content-Signature": hmac.new(b"vtok", body, hashlib.sha256).hexdigest()}


def wa_sig(body: bytes) -> dict:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()}


def wa_payload(msg_id="w1", text="hello", kind="text", sender="306912345678"):
    message = {"from": sender, "id": msg_id, "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    return json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PHONE"}, "messages": [message]}}]}]}).encode()


async def settle():
    for _ in range(5):
        await asyncio.sleep(0.02)


async def test_viber_rejects_bad_signatures_and_answers_good_ones_once(api, replies):
    requests, http = api
    viber = ViberChannel("vtok", "Coach", http)
    app = web.Application()
    app.add_routes([web.post("/viber", viber.handle_request)])
    body = json.dumps(
        {"event": "message", "message_token": "t1", "sender": {"id": "abc="}, "message": {"type": "text", "text": "hi"}}
    ).encode()
    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/viber", data=body, headers={"X-Viber-Content-Signature": "wrong"})).status == 403
        assert (
            await client.post("/viber", data=body, headers={"X-Viber-Content-Signature": "é"})
        ).status == 403  # non-ASCII must not crash
        assert (await client.post("/viber", data=body, headers=viber_sig(body))).status == 200
        await client.post("/viber", data=body, headers=viber_sig(body))  # Viber retries deliveries
        await settle()
    sends = [r for r in requests if r[0].endswith("send_message")]
    assert len(sends) == 1 and sends[0][1]["text"] == "echo: hi" and sends[0][1]["receiver"] == "abc="


async def test_whatsapp_handshake_signature_and_dedupe(api, replies):
    requests, http = api
    wa = WhatsAppChannel("wtok", "PHONE", "verify", "secret", "", http)
    app = web.Application()
    app.add_routes([web.get("/whatsapp", wa.handle_verify), web.post("/whatsapp", wa.handle_request)])
    async with TestClient(TestServer(app)) as client:
        ok = await client.get("/whatsapp?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=123")
        assert ok.status == 200 and await ok.text() == "123"
        assert (await client.get("/whatsapp?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1")).status == 403
        body = wa_payload()
        assert (await client.post("/whatsapp", data=body, headers={"X-Hub-Signature-256": "sha256=bad"})).status == 403
        assert (await client.post("/whatsapp", data=body, headers=wa_sig(body))).status == 200
        await client.post("/whatsapp", data=body, headers=wa_sig(body))
        reaction = wa_payload("w2", kind="reaction")
        await client.post("/whatsapp", data=reaction, headers=wa_sig(reaction))
        await settle()
    assert [r[1]["text"]["body"] for r in requests if "PHONE" in r[0]] == ["echo: hello"]  # one reply, none for the reaction


async def test_whatsapp_only_uses_templates_outside_the_24h_window_and_never_for_pulses(api):
    requests, http = api
    wa = WhatsAppChannel("wtok", "PHONE", "verify", "secret", "coach_nudge", http)
    channels.register(wa)
    user = db.get_or_create_user("whatsapp", "306912345678", "306912345678", "el")
    db.set_field(user["user_id"], "last_inbound", 1.0)  # long ago: the window is closed
    user = db.get_user(user["user_id"])
    assert channels.can_send(user) and not channels.can_send(user, template_ok=False)
    await channels.send(user, "line one\nline two")
    payload = requests[-1][1]
    assert payload["type"] == "template" and payload["template"]["language"]["code"] == "el"
    assert payload["template"]["components"][0]["parameters"][0]["text"] == "line one line two"
    wa.template = ""
    assert not channels.can_send(user)  # no template configured: wait until they write


async def test_health_endpoint_reports_stale_loops(monkeypatch):
    app = web.Application()
    app.add_routes([web.get("/healthz", bot.healthz)])
    async with TestClient(TestServer(app)) as client:
        monkeypatch.setattr(bot, "_heartbeat", {"reminders": __import__("time").time()})
        assert (await client.get("/healthz")).status == 200
        monkeypatch.setattr(bot, "_heartbeat", {"reminders": 1.0})
        response = await client.get("/healthz")
        assert response.status == 503 and (await response.json())["stale_loops"] == ["reminders"]


def test_telegram_channel_builds_with_concurrent_updates_and_handles_media():
    from coach.channels.telegram import TelegramChannel

    channel = TelegramChannel("123456:TEST-TOKEN")
    assert channel.app.concurrent_updates  # one user's slow reply must not block the others
    assert len(channel.app.handlers[0]) == 2  # text and media handlers
