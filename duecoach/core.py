"""Channel-independent entry point: one inbound message in, the reply text out.

The order matters and is the safety design: rate limit, then the consent gate (nothing is processed before the user agreed), then
crisis wording (answered before any command or pending question can mistake it for something else), then commands, and
then ordinary coaching.
"""

import asyncio
import logging
import time
from typing import NamedTuple

from . import chat, db, llm, ratelimit
from .chat import say
from .commands import BEFORE_CONSENT, COMMANDS, agree
from .config import VOICE_MAX_SECONDS, VOICE_MODEL, VOICE_URL, is_allowed
from .crisis import is_crisis

log = logging.getLogger("duecoach.core")
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
    crisis = is_crisis(text) and command not in ("deletedata", "forget")
    async with user_lock(uid):
        if not user["consent_at"]:
            # health-related chat goes to a third-party AI provider: nothing is processed before the user has agreed
            if crisis:
                # safety first, ahead of the privacy notice; nothing is stored or sent to a model
                return chat.crisis_message(uid)
            if command == "agree":
                return await agree(uid, args)
            if command in BEFORE_CONSENT:
                return await COMMANDS[command](uid, args) if command not in ("start", "help") else chat.privacy(uid)
            return chat.privacy(uid)
        if not crisis and not command and await llm.precheck(uid, text):
            crisis = True  # wording the list does not know: judged by the model (only after consent: it sends the text to the provider)
        if crisis:
            return await chat.crisis(uid, text)
        handler = COMMANDS.get(command) if command else None
        if handler:
            return await handler(uid, args)
        return await chat.coach(uid, text)


class VoiceResult(NamedTuple):
    text: str | None  # what to send as text
    spoken: str | None  # what to say aloud as well (a coach reply only: never an error, notice or safety message)
    lang: str = "en"  # the language to say it in: the user's own setting


async def handle_voice(channel: str, ext_id: str, chat_id: str, seconds: int, fetch, lang_hint: str = "en") -> VoiceResult:
    """A voice note: turned into text by the provider's audio model, then handled exactly like the same words typed.

    `fetch` downloads the audio (only called once the user is known to have agreed). The reply starts with what was heard, so a
    mishearing (a wrong time, say) is visible and can be corrected.
    """
    user = _user_for(channel, ext_id, chat_id, lang_hint)
    if user is None:
        return VoiceResult(None, None)
    uid = user["user_id"]
    if not user["consent_at"]:
        return VoiceResult(chat.privacy(uid), None)  # nothing, not even audio, goes to the provider before they agreed
    if not (VOICE_MODEL and VOICE_URL):
        return VoiceResult(say(uid, "UNSUPPORTED"), None)
    if ratelimit.full(uid):
        if uid in ratelimit.warned:
            return VoiceResult(None, None)
        ratelimit.warned.add(uid)
        return VoiceResult(say(uid, "RATE_LIMITED"), None)
    if seconds > VOICE_MAX_SECONDS:
        return VoiceResult(say(uid, "VOICE_TOO_LONG", seconds=VOICE_MAX_SECONDS), None)
    try:
        heard = await llm.transcribe(uid, await fetch())
    except Exception:
        log.warning("voice note from %s could not be transcribed", uid, exc_info=True)
        return VoiceResult(say(uid, "VOICE_FAILED"), None)
    if not heard:
        return VoiceResult(say(uid, "VOICE_UNCLEAR"), None)
    reply = await handle_text(channel, ext_id, chat_id, heard, lang_hint)
    if not reply or is_crisis(heard):
        return VoiceResult(reply, None)  # the safety message stands alone, in text
    return VoiceResult(f"🎤 “{heard}”\n\n{reply}", reply, db.get_user(uid)["lang"])


async def handle_unsupported(channel: str, ext_id: str, chat_id: str, lang_hint: str = "en") -> str | None:
    """Inbound that isn't text (voice note, image, ...)."""
    user = _user_for(channel, ext_id, chat_id, lang_hint)
    if user is None:
        return None
    return say(user["user_id"], "UNSUPPORTED") if user["consent_at"] else chat.privacy(user["user_id"])
