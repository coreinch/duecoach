"""Channel-independent entry point: one inbound message in, the reply text out.

The order matters and is the safety design: rate limit, then the consent gate (nothing is processed before the user agreed), then
crisis wording (answered before any command or pending question can mistake it for something else), then commands, then the
question in progress, and only then ordinary coaching.
"""

import asyncio
import logging
import time

from . import chat, db, flows, ratelimit
from .chat import say
from .commands import BEFORE_CONSENT, COMMANDS, agree
from .config import is_allowed

log = logging.getLogger("coach.core")
_locks: dict[int, asyncio.Lock] = {}


def user_lock(uid: int) -> asyncio.Lock:
    """One conversation at a time per user: messages, commands and proactive check-ins all take this lock."""
    return _locks.setdefault(uid, asyncio.Lock())


def _user_for(channel: str, ext_id: str, chat_id: str, lang_hint: str):
    """The user row, or None if this identity isn't allowed to use the bot."""
    if not is_allowed(channel, ext_id):
        log.info("ignored message from unauthorised %s:%s", channel, ext_id)
        return None
    user = db.get_or_create_user(channel, ext_id, chat_id, lang_hint)
    db.set_fields(user["user_id"], last_inbound=time.time(), unanswered=0)  # they wrote, so check-ins return to normal
    return user


async def handle_text(channel: str, ext_id: str, chat_id: str, text: str, lang_hint: str = "en") -> str | None:
    """Process one inbound text message and return the reply to send (None = send nothing)."""
    user = _user_for(channel, ext_id, chat_id, lang_hint)
    text = text.strip()
    if user is None or not text:
        return None
    uid = user["user_id"]
    if ratelimit.limited(uid):
        if uid in ratelimit.warned:
            return None
        ratelimit.warned.add(uid)
        return say(uid, "RATE_LIMITED")
    command, args = None, []
    if text.startswith("/"):
        word, *args = text.split()
        command = word[1:].split("@")[0].lower()
    # Crisis wording is answered first, whatever the message is: plain text, or inside a command ("/stuck I want to die").
    crisis = flows.is_crisis(text) and command not in ("deletedata", "forget")
    async with user_lock(uid):
        if not user["consent_at"]:
            # health-related chat goes to a third-party AI provider: nothing is processed before the user has agreed
            if crisis:
                # safety first, ahead of the privacy notice; nothing is stored or sent to a model
                return chat.crisis_message(uid)
            if command == "agree":
                return await agree(uid, args)
            if command in BEFORE_CONSENT:
                return await COMMANDS[command](uid, args) if command not in ("start", "help") else say(uid, "PRIVACY")
            return say(uid, "PRIVACY")
        if crisis:
            return await chat.crisis(uid, text)
        handler = COMMANDS.get(command) if command else None
        if handler:
            return await handler(uid, args)
        if (answer := await chat.flow_answer(uid, text)) is not None:
            return answer
        return await chat.coach_with_question(uid, text)


async def handle_unsupported(channel: str, ext_id: str, chat_id: str, lang_hint: str = "en") -> str | None:
    """Inbound that isn't text (voice note, image, ...)."""
    user = _user_for(channel, ext_id, chat_id, lang_hint)
    if user is None:
        return None
    return say(user["user_id"], "UNSUPPORTED" if user["consent_at"] else "PRIVACY")
