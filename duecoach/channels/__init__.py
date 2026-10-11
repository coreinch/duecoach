"""Outbound delivery. The channel registers itself here under its name; inbound is handled by the channel module."""

import logging
from typing import Protocol

log = logging.getLogger("duecoach.channels")

MAX_LEN = 4000  # below Telegram's limit of 4096


class SendError(Exception):
    pass


class Unreachable(SendError):
    """The recipient can never be messaged (they blocked the bot, deleted the chat): retrying is pointless."""


class Channel(Protocol):
    name: str

    async def send(self, user, text: str) -> None: ...


REGISTRY: dict[str, Channel] = {}


def register(channel) -> None:
    REGISTRY[channel.name] = channel


async def send(user, text: str) -> None:
    channel = REGISTRY.get(user["channel"])
    if channel is None:
        raise SendError(f"channel {user['channel']!r} is not enabled")
    await channel.send(user, text[:MAX_LEN])
