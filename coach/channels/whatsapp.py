import asyncio
import hashlib
import hmac
import json
import logging
import re
import time

import httpx
from aiohttp import web

from .. import core, db
from ..config import WHATSAPP_API_VERSION
from . import SendError

log = logging.getLogger("coach.whatsapp")
IGNORED_TYPES = {"reaction", "unsupported", "system", "request_welcome", "ephemeral"}  # not messages the user expects a reply to
WINDOW = 23.5 * 3600  # free-form replies are only allowed for 24h after the user's last message


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(self, token: str, phone_number_id: str, verify_token: str, app_secret: str, template: str, http: httpx.AsyncClient):
        self.token, self.phone_id, self.verify_token = token, phone_number_id, verify_token
        self.app_secret, self.template, self.http = app_secret, template, http
        self._tasks: set[asyncio.Task] = set()

    def _in_window(self, user) -> bool:
        return time.time() - (user["last_inbound"] or 0) < WINDOW

    def can_send(self, user, template_ok: bool = True) -> bool:
        # Outside the 24h window only a pre-approved template message is allowed.
        return self._in_window(user) or (template_ok and bool(self.template))

    async def _post(self, payload: dict) -> None:
        r = await self.http.post(
            f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{self.phone_id}/messages",
            json={"messaging_product": "whatsapp", **payload},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if r.status_code >= 400:
            raise SendError(f"whatsapp {r.status_code}: {r.text[:300]}")

    async def send(self, user, text: str) -> None:
        to = re.sub(r"\D", "", str(user["chat_id"]))
        if self._in_window(user):
            await self._post({"to": to, "type": "text", "text": {"body": text}})
        elif self.template:
            # Template variables can't contain newlines/tabs or long runs of spaces.
            flat = re.sub(r"\s+", " ", text).strip()
            await self._post(
                {
                    "to": to,
                    "type": "template",
                    "template": {
                        "name": self.template,
                        "language": {"code": "el" if user["lang"] == "el" else "en"},
                        "components": [{"type": "body", "parameters": [{"type": "text", "text": flat}]}],
                    },
                }
            )
        else:
            raise SendError("outside the 24h window and no WHATSAPP_TEMPLATE configured")

    async def handle_verify(self, request: web.Request) -> web.Response:
        q = request.query
        if q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", "").encode(), self.verify_token.encode()):
            return web.Response(text=q.get("hub.challenge", ""))
        return web.Response(status=403)

    async def handle_request(self, request: web.Request) -> web.Response:
        raw = await request.read()
        expected = "sha256=" + hmac.new(self.app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", "").encode(), expected.encode()):
            return web.Response(status=403)
        try:
            data = json.loads(raw)
        except ValueError:
            return web.Response(status=400)
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("metadata", {}).get("phone_number_id") != self.phone_id:
                    continue
                for message in value.get("messages", []):
                    task = asyncio.create_task(self._process(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        return web.Response(text="ok")  # always 200 quickly, or Meta retries the delivery

    async def _process(self, message: dict) -> None:
        try:
            if not db.mark_seen(f"whatsapp:{message['id']}"):
                return
            if message.get("type") in IGNORED_TYPES:
                return
            wa_id = re.sub(r"\D", "", message["from"])
            lang = "el" if wa_id.startswith("30") else "en"  # no locale in the payload: guess from the country code
            if message.get("type") == "text":
                reply = await core.handle_text(self.name, wa_id, wa_id, message["text"]["body"], lang)
            else:
                reply = await core.handle_unsupported(self.name, wa_id, wa_id, lang)
            if reply:
                await self.send(db.get_user_by_identity(self.name, wa_id), reply)
        except Exception:
            log.exception("failed to process WhatsApp message")
