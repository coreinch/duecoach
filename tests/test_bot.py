import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from duecoach import bot, channels, db
from duecoach.channels.telegram import TelegramChannel


class Recorder:
    name = "telegram"

    def __init__(self):
        self.sent = []

    async def send(self, user, text):
        self.sent.append(text)


async def test_a_due_reminder_is_sent_once_and_marked(user, monkeypatch):
    channel = Recorder()
    monkeypatch.setitem(channels.REGISTRY, "telegram", channel)
    db.add_reminder(1001, 0, "call mum")
    await bot.deliver_reminders()
    await bot.deliver_reminders()
    assert len(channel.sent) == 1 and "call mum" in channel.sent[0]


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


def test_maintenance_logs_the_turn_counts_of_the_last_hour_without_any_text(user, monkeypatch, caplog):
    db.add_turn_stat(1001, "reply", "sent", ["off_target"], ["add_event"], 2, 1.0)
    db.add_turn_stat(1001, "checkin", "dropped", [], [], 1, 1.0)
    db.add_event(1001, "Long ago", time.time() + 1)
    db._conn.execute("UPDATE events SET start=?", (time.time() - 30 * 86400,))
    monkeypatch.setattr(bot, "_last_maintenance", 0.0)
    with caplog.at_level("INFO", logger="duecoach"):
        bot.maintain()
    assert "turns in the last hour: checkin:dropped=1, flag:off_target=1, reply:sent=1" in caplog.text
    assert db._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_a_second_instance_on_the_same_database_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "duecoach.db"))
    first = bot.acquire_instance_lock()
    with pytest.raises(SystemExit, match="already using"):
        bot.acquire_instance_lock()
    first.close()
    bot.acquire_instance_lock().close()  # free again once the first one is gone
    assert os.path.exists(tmp_path / "duecoach.lock")


async def test_the_bot_refuses_to_start_without_a_telegram_token(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "duecoach.db"))
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(channels, "REGISTRY", {})
    monkeypatch.setattr(db, "init", lambda *a: None)
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        await bot.main(asyncio.Event())


class FakeTelegram:
    name = "telegram"
    running = False

    def __init__(self, token):
        pass

    async def start(self):
        FakeTelegram.running = True

    async def stop(self):
        FakeTelegram.running = False

    async def send(self, user, text):
        pass


async def test_the_bot_starts_serves_health_and_shuts_down_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_PATH", str(tmp_path / "duecoach.db"))
    monkeypatch.setattr(bot, "TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setattr(bot, "TelegramChannel", FakeTelegram)
    monkeypatch.setattr(bot, "WEB_PORT", 0)
    monkeypatch.setattr(channels, "REGISTRY", {})
    monkeypatch.setattr(db, "init", lambda *a: None)
    monkeypatch.setattr(db, "close", lambda: None)
    stop = asyncio.Event()
    task = asyncio.create_task(bot.main(stop))
    await asyncio.sleep(0.3)
    assert "telegram" in channels.REGISTRY and FakeTelegram.running and not task.done()
    stop.set()
    await asyncio.wait_for(task, 10)
    assert not FakeTelegram.running  # the channel was stopped
    assert bot.acquire_instance_lock()  # the lock was released on the way out


async def test_the_telegram_adapter_sends_to_the_users_chat():
    channel = TelegramChannel("123456:TEST-TOKEN")
    sent = []

    async def send_message(chat_id, text, **kw):
        sent.append((chat_id, text))

    channel.app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    await channel.send({"chat_id": "42"}, "hello")
    assert sent == [(42, "hello")]  # chat ids are numbers for Telegram
