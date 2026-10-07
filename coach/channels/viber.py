import asyncio
import hashlib
import hmac
import json
import logging

import httpx
from aiohttp import web

from .. import core, db, strings
from ..config import is_allowed
from . import SendError, Unreachable

log = logging.getLogger("coach.viber")
API = "https://chatapi.viber.com/pa"
UNREACHABLE_STATUSES = {5, 6}  # receiver not registered / not subscribed


class ViberChannel:
    name = "viber"

    def __init__(self, auth_token: str, bot_name: str, http: httpx.AsyncClient):
        self.token, self.bot_name, self.http = auth_token, bot_name, http
        self._tasks: set[asyncio.Task] = set()

    async def _call(self, endpoint: str, payload: dict) -> dict:
        r = await self.http.post(f"{API}/{endpoint}", json=payload, headers={"X-Viber-Auth-Token": self.token})
        data = r.json()
        if r.status_code != 200 or data.get("status") != 0:
            error = Unreachable if data.get("status") in UNREACHABLE_STATUSES else SendError
            raise error(f"viber {endpoint}: {data.get('status_message', r.text[:200])}")
        return data

    async def set_webhook(self, url: str) -> None:
        await self._call("set_webhook", {"url": url, "event_types": ["message", "conversation_started", "subscribed"]})

    def can_send(self, user, template_ok: bool = True) -> bool:
        return True

    async def send(self, user, text: str) -> None:
        await self._call(
            "send_message",
            {"receiver": str(user["chat_id"]), "min_api_version": 1, "sender": {"name": self.bot_name}, "type": "text", "text": text},
        )

    async def handle_request(self, request: web.Request) -> web.Response:
        raw = await request.read()
        expected = hmac.new(self.token.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("X-Viber-Content-Signature", "").encode(), expected.encode()):
            return web.Response(status=403)
        try:
            data = json.loads(raw)
        except ValueError:
            return web.Response(status=400)
        event = data.get("event")
        if event == "message":
            # Reply from a background task: Viber expects a fast HTTP answer and the LLM can be slow.
            task = asyncio.create_task(self._process(data))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif event == "conversation_started" and (user := data.get("user")) and is_allowed(self.name, user["id"]):
            # The user just opened the chat: whatever we answer here is shown as the welcome message.
            lang = strings.lang_from_telegram(user.get("language"))
            return web.json_response({"sender": {"name": self.bot_name}, "type": "text", "text": strings.t(lang, "HELP")})
        return web.Response(text="ok")

    async def _process(self, data: dict) -> None:
        try:
            sender, message = data["sender"], data["message"]
            if (token := data.get("message_token")) and not db.mark_seen(f"viber:{token}"):
                return
            lang = strings.lang_from_telegram(sender.get("language"))
            if message.get("type") == "text":
                reply = await core.handle_text(self.name, sender["id"], sender["id"], message.get("text", ""), lang)
            else:
                reply = await core.handle_unsupported(self.name, sender["id"], sender["id"], lang)
            if reply:
                await self.send(db.get_user_by_identity(self.name, sender["id"]), reply)
        except Exception:
            log.exception("failed to process Viber message")
