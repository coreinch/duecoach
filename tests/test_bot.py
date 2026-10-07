import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from coach import bot, channels, db
from coach.channels.telegram import TelegramChannel


class Recorder:
    name = "telegram"

    def __init__(self, can=True):
        self.can, self.sent = can, []

    def can_send(self, user, template_ok=True):
        return self.can

    async def send(self, user, text):
        self.sent.append(text)


async def test_a_due_reminder_is_sent_once_and_marked(user, monkeypatch):
    channel = Recorder()
    monkeypatch.setitem(channels.REGISTRY, "telegram", channel)
    db.add_reminder(1001, 0, "call mum")
    await bot.deliver_reminders()
    await bot.deliver_reminders()
    assert len(channel.sent) == 1 and "call mum" in channel.sent[0]


async def test_a_reminder_waits_while_the_user_cannot_be_reached_yet(user, monkeypatch):
    channel = Recorder(can=False)
    monkeypatch.setitem(channels.REGISTRY, "telegram", channel)
    db.add_reminder(1001, 0, "call mum")
    await bot.deliver_reminders()
    assert channel.sent == [] and len(db.due_reminders()) == 1
    channel.can = True
    await bot.deliver_reminders()
    assert len(channel.sent) == 1


def test_maintenance_runs_at_most_once_an_hour(user, monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "_last_maintenance", 0.0)
    monkeypatch.setattr(db, "expire_stale_objectives", lambda: calls.append(1) or 0)
    bot.maintain()
    bot.maintain()
    assert calls == [1]
    monkeypatch.setattr(bot, "_last_maintenance", time.time() - bot.MAINTENANCE_SECONDS - 1)
    bot.maintain()
    assert calls == [1, 1]


def test_a_second_instance_on_the_same_database_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "coach.db"))
    first = bot.acquire_instance_lock()
    with pytest.raises(SystemExit, match="already using"):
        bot.acquire_instance_lock()
    first.close()
    bot.acquire_instance_lock().close()  # free again once the first one is gone
    assert os.path.exists(tmp_path / "coach.lock")


async def test_the_bot_refuses_to_start_without_a_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "coach.db"))
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(bot, "VIBER_AUTH_TOKEN", "")
    monkeypatch.setattr(bot, "WHATSAPP_TOKEN", "")
    monkeypatch.setattr(channels, "REGISTRY", {})
    monkeypatch.setattr(db, "init", lambda *a: None)
    with pytest.raises(SystemExit, match="No channel configured"):
        await bot.main(asyncio.Event())


async def test_whatsapp_without_its_secrets_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "coach.db"))
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(bot, "VIBER_AUTH_TOKEN", "")
    monkeypatch.setattr(bot, "WHATSAPP_TOKEN", "t")
    monkeypatch.setattr(bot, "WHATSAPP_PHONE_NUMBER_ID", "1")
    monkeypatch.setattr(bot, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(channels, "REGISTRY", {})
    monkeypatch.setattr(db, "init", lambda *a: None)
    with pytest.raises(SystemExit, match="WHATSAPP_APP_SECRET"):
        await bot.main(asyncio.Event())


async def test_the_bot_starts_serves_health_and_shuts_down_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "coach.db"))
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(bot, "VIBER_AUTH_TOKEN", "viber-token")
    monkeypatch.setattr(bot, "PUBLIC_URL", "")
    monkeypatch.setattr(bot, "WEB_PORT", 0)
    monkeypatch.setattr(channels, "REGISTRY", {})
    monkeypatch.setattr(db, "init", lambda *a: None)
    monkeypatch.setattr(db, "close", lambda: None)
    stop = asyncio.Event()
    task = asyncio.create_task(bot.main(stop))
    await asyncio.sleep(0.3)
    assert "viber" in channels.REGISTRY and not task.done()
    stop.set()
    await asyncio.wait_for(task, 10)
    assert bot.acquire_instance_lock()  # the lock was released on the way out


async def test_the_telegram_adapter_sends_to_the_users_chat():
    channel = TelegramChannel("123456:TEST-TOKEN")
    sent = []

    async def send_message(chat_id, text, **kw):
        sent.append((chat_id, text))

    channel.app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    assert channel.can_send({"chat_id": "42"}, True)
    await channel.send({"chat_id": "42"}, "hello")
    assert sent == [(42, "hello")]  # chat ids are numbers for Telegram
