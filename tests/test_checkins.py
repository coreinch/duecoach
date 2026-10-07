import datetime as dt
import time
from zoneinfo import ZoneInfo

import pytest

from coach import backoff, bot, channels, db, llm, prompts

TZ = ZoneInfo("Europe/Athens")


class Clock:
    def __init__(self, start: dt.datetime):
        self.t = start.timestamp()

    def advance(self, minutes: float):
        self.t += minutes * 60

    def local(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.t, TZ)


@pytest.fixture
def clock(monkeypatch):
    c = Clock(dt.datetime(2026, 10, 12, 6, 0, tzinfo=TZ))  # a Monday
    monkeypatch.setattr(time, "time", lambda: c.t)

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.fromtimestamp(c.t, tz)

    monkeypatch.setattr(bot, "datetime", FakeDatetime)
    return c


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    kinds = {prompts.MORNING: "morning", prompts.EVENING: "evening", prompts.PULSE: "pulse", prompts.WEEKLY_REVIEW: "review"}

    async def fake_reply(uid, text, instruction=None, coach=True):
        return kinds.get(instruction, "reengage")

    class FakeChannel:
        name = "telegram"

        def can_send(self, u, template_ok=True):
            return True

        async def send(self, u, text):
            sent.append((u["user_id"], text))

    monkeypatch.setattr(llm, "reply", fake_reply)
    monkeypatch.setitem(channels.REGISTRY, "telegram", FakeChannel())
    return sent


def enable_checkins(uid, **extra):
    db.set_fields(uid, interval_min=30, tz="Europe/Athens", tz_set=1, last_inbound=time.time() - 3 * 86400, **extra)


async def run_for(clock, hours):
    for _ in range(int(hours * 2)):
        clock.advance(30)
        await bot.run_checkins()


def test_backoff_gaps_and_final_message():
    assert (
        backoff.allowed(0, 0, 30)
        and not backoff.allowed(1, 1000, 30, now=1000 + 59 * 60)
        and backoff.allowed(1, 1000, 30, now=1000 + 60 * 60)
    )
    assert not backoff.allowed(len(backoff.BACKOFF_MINUTES), 0, 30)  # every step used: stay quiet
    assert backoff.instruction(0, 0) is None and backoff.instruction(len(backoff.BACKOFF_MINUTES) - 1, 0) == prompts.REENGAGE_LAST
    assert backoff.allowed(0, 1000, 120, now=1000 + 90 * 60) is False  # the user's own interval is a floor


async def test_a_silent_user_gets_less_and_less_then_nothing(clock, outbox, user):
    enable_checkins(user["user_id"])
    stamps = []
    for _ in range(45 * 48):
        clock.advance(30)
        before = len(outbox)
        await bot.run_checkins()
        if len(outbox) > before:
            stamps.append(clock.local().strftime("%m-%d %H:%M"))
    days = [s[:5] for s in stamps]
    assert stamps[:4] == ["10-12 09:00", "10-12 10:00", "10-12 12:00", "10-12 16:00"]
    assert len(stamps) == len(backoff.BACKOFF_MINUTES) and days[-1] == "11-10"
    assert db.get_user(user["user_id"])["unanswered"] == len(backoff.BACKOFF_MINUTES)


async def test_replying_resets_the_back_off(clock, outbox, user):
    uid = user["user_id"]
    enable_checkins(uid)
    await run_for(clock, 5)  # 06:00 -> 11:00: check-ins at 09:00 and 10:00
    assert db.get_user(uid)["unanswered"] == 2
    db.set_fields(uid, last_inbound=time.time(), unanswered=0)
    n = len(outbox)
    await run_for(clock, 1)
    assert len(outbox) == n + 1  # back to the normal rhythm: one check-in 30 minutes after the reply


async def test_only_inside_the_daily_window_and_not_without_consent(clock, outbox, user):
    uid = user["user_id"]
    enable_checkins(uid)
    await run_for(clock, 2.5)  # 06:00 -> 08:30
    assert outbox == []
    db.set_field(uid, "consent_at", 0)
    await run_for(clock, 1)
    assert outbox == []


async def test_snooze_and_off_switch_are_respected(clock, outbox, user):
    uid = user["user_id"]
    enable_checkins(uid, snooze_until=clock.t + 5 * 3600)  # snoozed until 11:00
    await run_for(clock, 4.5)  # to 10:30
    assert outbox == []
    db.set_field(uid, "interval_min", 0)
    await run_for(clock, 4)
    assert outbox == []


async def test_failed_check_ins_back_off_instead_of_retrying_every_pass(clock, outbox, user, monkeypatch):
    uid = user["user_id"]
    enable_checkins(uid)
    calls = []

    async def failing_reply(uid, text, instruction=None, coach=True):
        calls.append(clock.t)
        raise RuntimeError("429")

    monkeypatch.setattr(llm, "reply", failing_reply)
    await run_for(clock, 3.5)  # 06:00 -> 09:30: first attempt at 09:00
    await run_for(clock, 1.5)  # -> 11:00
    assert 2 <= len(calls) <= 5  # one attempt per growing wait, not one every 30 s pass
    gaps = [b - a for a, b in zip(calls, calls[1:], strict=False)]
    assert gaps == sorted(gaps) and outbox == []


async def test_user_writing_during_generation_is_not_counted_as_ignoring(clock, outbox, user, monkeypatch):
    uid = user["user_id"]
    enable_checkins(uid)

    async def slow_reply(uid, text, instruction=None, coach=True):
        db.set_field(uid, "last_inbound", time.time() + 1)  # they write while the check-in is being prepared
        return "hello"

    monkeypatch.setattr(llm, "reply", slow_reply)
    await run_for(clock, 3.5)
    assert len(outbox) == 1 and db.get_user(uid)["unanswered"] == 0


async def test_no_check_in_talks_over_a_conversation_in_progress(clock, outbox, user):
    from coach import core

    uid = user["user_id"]
    enable_checkins(uid)
    await run_for(clock, 3.5)  # reach the first check-in
    outbox.clear()
    db.set_fields(uid, unanswered=0, last_inbound=time.time(), last_proactive=0)
    await core.user_lock(uid).acquire()
    try:
        await run_for(clock, 1.5)
    finally:
        core.user_lock(uid).release()
    assert outbox == []


def test_overdue_weekly_review_is_not_skipped(clock, user):
    uid = user["user_id"]
    db.add_goal(uid, "a goal")
    enable_checkins(uid, last_morning=clock.local().date().isoformat())
    db.set_field(uid, "last_review", (clock.local().date() - dt.timedelta(days=9)).isoformat())
    clock.advance(6 * 60)  # 12:00 on a Monday: not the review weekday, but the review is 9 days old
    plan = bot.plan_checkin(db.get_user(uid), clock.t, clock.local())
    assert plan[0] == prompts.WEEKLY_REVIEW and plan[1] == {"last_review": clock.local().date().isoformat()}
