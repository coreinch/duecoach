import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from duecoach import channels, db, strings, watch


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    class FakeChannel:
        name = "telegram"

        async def send(self, user, text):
            sent.append((user["chat_id"], text))

    monkeypatch.setitem(channels.REGISTRY, "telegram", FakeChannel())
    return sent


def stopped_during_setup(user, days_ago=3):
    db.set_fields(user["user_id"], consent_at=1.0, interval_min=0, last_inbound=time.time() - days_ago * 86400)
    db._conn.execute("UPDATE users SET onboarded=0 WHERE user_id=?", (user["user_id"],))
    db._conn.commit()


async def test_someone_who_stopped_during_setup_is_asked_once_ever(user, outbox):
    stopped_during_setup(user)
    await watch.nudge_unfinished_setup()
    await watch.nudge_unfinished_setup()
    assert [t for _, t in outbox] == [strings.t(user["lang"], "NUDGE_SETUP")]
    assert db.get_user(user["user_id"])["nudged_at"] > 0


@pytest.mark.parametrize(
    "extra",
    [
        {"onboarded": 1},
        {"interval_min": 30},
        {"consent_at": 0},
        {"last_inbound": time.time() - 3600},
        {"last_inbound": time.time() - 60 * 86400},
    ],
)
async def test_nobody_else_is_nudged(user, outbox, extra):
    stopped_during_setup(user)
    if "onboarded" in extra:
        db._conn.execute("UPDATE users SET onboarded=1 WHERE user_id=?", (user["user_id"],))
        db._conn.commit()
    else:
        db.set_fields(user["user_id"], **extra)
    await watch.nudge_unfinished_setup()
    assert outbox == []


async def test_a_nudge_that_cannot_be_sent_is_not_retried(user, monkeypatch):
    stopped_during_setup(user)
    calls = []

    async def broken(u, text):
        calls.append(1)
        raise channels.Unreachable("blocked")

    monkeypatch.setattr(channels, "send", broken)
    await watch.nudge_unfinished_setup()
    await watch.nudge_unfinished_setup()
    assert calls == [1]


def test_the_digest_counts_replies_fallbacks_and_flags(user):
    uid = user["user_id"]
    for seconds in (2.0, 4.0, 6.0, 40.0):
        db.add_turn_stat(uid, "reply", "sent", ["off_target"] if seconds == 4.0 else [], [], 1, seconds)
    db.add_turn_stat(uid, "reply", "fallback", ["unbacked"], [], 3, 90.0)
    db.add_turn_stat(uid, "checkin", "sent", [], [], 1, 5.0)
    db.add_turn_stat(uid, "checkin", "dropped", ["off_target"], [], 3, 50.0)
    digest = db.turn_digest(0)
    assert (digest["replies"], digest["checkins"], digest["fallbacks"], digest["withheld"]) == (5, 2, 1, 1)
    assert digest["reply_p50"] == 6.0 and digest["reply_p95"] == 90.0 and digest["flags"] == {"off_target": 2, "unbacked": 1}
    assert "5 replies" in watch.format_digest(digest, 24) and "off_target 2" in watch.format_digest(digest, 24)


async def test_the_digest_goes_out_once_a_day_after_nine_and_only_with_an_admin_chat(user, outbox, monkeypatch):
    zone = ZoneInfo("Europe/Athens")
    monkeypatch.setattr(watch, "TIMEZONE", "Europe/Athens")
    monkeypatch.setattr(watch, "ADMIN_CHAT_ID", "")
    await watch.report(datetime(2026, 10, 21, 10, 0, tzinfo=zone))
    assert outbox == []  # nobody to tell: the log has it
    db.set_meta("last_digest", "")
    monkeypatch.setattr(watch, "ADMIN_CHAT_ID", "777")
    await watch.report(datetime(2026, 10, 21, 8, 0, tzinfo=zone))
    assert outbox == []  # too early
    await watch.report(datetime(2026, 10, 21, 10, 0, tzinfo=zone))
    await watch.report(datetime(2026, 10, 21, 11, 0, tzinfo=zone))
    assert len(outbox) == 1 and outbox[0][0] == "777" and "last 24 h" in outbox[0][1]
    await watch.report(datetime(2026, 10, 22, 9, 30, tzinfo=zone))
    assert len(outbox) == 2


async def test_repeated_fallbacks_raise_an_alert_with_a_cooldown(user, outbox, monkeypatch):
    uid = user["user_id"]
    monkeypatch.setattr(watch, "ADMIN_CHAT_ID", "777")
    now = datetime.now(ZoneInfo("UTC"))
    db.set_meta("last_digest", now.date().isoformat())  # today's digest is already out: it is not what is tested here
    await watch.report(now)
    assert outbox == []
    for _ in range(2):
        db.add_turn_stat(uid, "reply", "fallback", ["unbacked"], [], 3, 30.0)
    await watch.report(now)
    await watch.report(now)
    assert len(outbox) == 1 and outbox[0][1].startswith("Duecoach alert: 2 'say it again'")
