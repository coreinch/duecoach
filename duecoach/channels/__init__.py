"""Outbound delivery. Each channel registers itself here under its name; inbound is handled by the channel modules."""

import logging
from typing import Protocol

log = logging.getLogger("duecoach.channels")

MAX_LEN = 4000  # below the 4096 limit of Telegram and WhatsApp


class SendError(Exception):
    pass


class Unreachable(SendError):
    """The recipient can never be messaged (they blocked the bot, deleted the chat, unsubscribed): retrying is pointless."""


class Channel(Protocol):
    name: str

    def can_send(self, user, template_ok: bool = True) -> bool: ...

    async def send(self, user, text: str) -> None: ...


REGISTRY: dict[str, Channel] = {}


def register(channel) -> None:
    REGISTRY[channel.name] = channel


def can_send(user, template_ok: bool = True) -> bool:
    """False when we know a proactive message to this user can't be delivered right now.

    template_ok=False means "only if it can go out as a normal (free) message", for frequent messages where paid
    template messages would add up (WhatsApp outside its 24h window).
    """
    channel = REGISTRY.get(user["channel"])
    return channel is not None and channel.can_send(user, template_ok)


async def send(user, text: str) -> None:
    channel = REGISTRY.get(user["channel"])
    if channel is None:
        raise SendError(f"channel {user['channel']!r} is not enabled")
    await channel.send(user, text[:MAX_LEN])
