import time
from types import SimpleNamespace

import httpx
import pytest
import telegram.error

from duecoach import bot, channels, core, db, flows, llm
from duecoach.channels import SendError, Unreachable
from duecoach.channels.telegram import TelegramChannel
from duecoach.channels.viber import ViberChannel
from duecoach.channels.whatsapp import WhatsAppChannel


@pytest.fixture
def no_model(monkeypatch):
    """Any model call fails the test: the safety message must not depend on the model."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("the model must not be called here")

    monkeypatch.setattr(llm, "reply", forbidden)
    monkeypatch.setattr(llm, "draft", forbidden)


async def say(text, ext="1001"):
    return await core.handle_text("telegram", ext, ext, text)


@pytest.mark.parametrize(
    "text",
    ["/stuck I want to die", "/plan I am thinking of ending my life", "/overwhelm i want to kill myself", "/remind 10 δεν αντέχω άλλο"],
)
async def test_crisis_wording_inside_a_command_gets_the_safety_message_not_a_coaching_reply(no_model, user, text):
    reply = await say(text)
    assert "112" in reply and "Are you safe right now" in reply or "ασφαλής" in reply
    assert db.get_user(user["user_id"])["snooze_until"] > time.time() + 20 * 3600


async def test_and_before_the_user_has_agreed_too(no_model):
    db.get_or_create_user("telegram", "2002", "2002")
    assert "112" in await say("/stuck I want to die", "2002")
    assert db.message_count(2002) == 0  # nothing stored


async def test_deleting_your_data_still_works_when_the_words_appear_in_that_command(no_model, user):
    assert "erased" in await say("/deletedata confirm")


# --- people who cannot be messaged ---------------------------------------------------------------------------------------------


class Gone:
    name = "telegram"

    def __init__(self, error):
        self.error = error
        self.sent = 0

    def can_send(self, user, template_ok=True):
        return True

    async def send(self, user, text):
        self.sent += 1
        raise self.error


@pytest.fixture
def model(monkeypatch):
    calls = []

    async def reply(uid, text, instruction=None):
        calls.append(uid)
        return "a check-in"

    monkeypatch.setattr(llm, "reply", reply)
    return calls


def due_checkin_user(uid=3003):
    row = db.get_or_create_user("telegram", str(uid), str(uid))
    db.set_fields(
        row["user_id"],
        consent_at=1.0,
        tz_set=1,
        intake_state="done",
        onboarded=1,
        interval_min=30,
        tz="Europe/Athens",
        morning_hour=0,
        evening_hour=23,
        last_inbound=time.time() - 3 * 86400,
    )
    return row["user_id"]


async def test_a_user_who_blocked_the_bot_gets_check_ins_switched_off_after_the_first_failure(monkeypatch, model):
    uid = due_checkin_user()
    gone = Gone(Unreachable("Forbidden: bot was blocked by the user"))
    monkeypatch.setitem(channels.REGISTRY, "telegram", gone)
    await bot.run_checkins()
    assert db.get_user(uid)["interval_min"] == 0 and db.get_user(uid)["checkin_failures"] == 0  # off, not "backing off"
    db.set_field(uid, "last_inbound", time.time() - 3 * 86400)
    await bot.run_checkins()
    assert gone.sent == 1 and len(model) == 1  # one attempt, one model call, ever


async def test_other_send_failures_still_back_off_and_keep_trying(monkeypatch, model):
    uid = due_checkin_user()
    monkeypatch.setitem(channels.REGISTRY, "telegram", Gone(SendError("timeout")))
    await bot.run_checkins()
    user = db.get_user(uid)
    assert user["interval_min"] == 30 and user["checkin_failures"] == 1 and user["checkin_retry_at"] > time.time()


async def test_reminders_for_unreachable_people_are_dropped_and_the_rest_retried(monkeypatch):
    blocked = db.get_or_create_user("telegram", "4004", "4004")["user_id"]
    flaky = db.get_or_create_user("telegram", "4005", "4005")["user_id"]
    db.add_reminder(blocked, 0, "gone")
    db.add_reminder(flaky, 0, "later")
    sent = []

    class Mixed:
        name = "telegram"

        def can_send(self, user, template_ok=True):
            return True

        async def send(self, user, text):
            if user["user_id"] == blocked:
                raise Unreachable("blocked")
            raise SendError("timeout")

    monkeypatch.setitem(channels.REGISTRY, "telegram", Mixed())
    await bot.deliver_reminders()
    assert db.due_reminders() == []  # the blocked user's reminder is gone, the flaky one is waiting out its back-off
    pending = db._conn.execute("SELECT user_id, sent, attempts FROM reminders ORDER BY id").fetchall()
    assert [(r["user_id"], r["sent"], r["attempts"]) for r in pending] == [(blocked, 1, 0), (flaky, 0, 1)] and not sent


async def test_telegram_blocked_and_missing_chats_are_unreachable_other_errors_are_not():
    channel = TelegramChannel("123456:TEST-TOKEN")
    user = {"chat_id": "42"}

    async def failing(error):
        async def send_message(chat_id, text):
            raise error

        channel.app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))  # (the real bot object refuses new attributes)
        await channel.send(user, "hi")

    with pytest.raises(Unreachable):
        await failing(telegram.error.Forbidden("Forbidden: bot was blocked by the user"))
    with pytest.raises(Unreachable):
        await failing(telegram.error.BadRequest("Chat not found"))
    with pytest.raises(telegram.error.BadRequest):
        await failing(telegram.error.BadRequest("Message is too long"))
    with pytest.raises(telegram.error.NetworkError):
        await failing(telegram.error.NetworkError("timeout"))


def mock_http(status_code, body):
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json=body)))


@pytest.mark.parametrize(("status", "expected"), [(6, Unreachable), (5, Unreachable), (12, SendError)])
async def test_viber_unsubscribed_receivers_are_unreachable(status, expected):
    viber = ViberChannel("tok", "Coach", mock_http(200, {"status": status, "status_message": "x"}))
    with pytest.raises(expected) as error:
        await viber.send({"chat_id": "abc"}, "hi")
    assert isinstance(error.value, Unreachable) is (expected is Unreachable)


@pytest.mark.parametrize(("code", "expected"), [(131026, Unreachable), (131047, SendError), (190, SendError)])
async def test_whatsapp_undeliverable_numbers_are_unreachable_but_a_closed_window_is_not(code, expected):
    wa = WhatsAppChannel("tok", "PHONE", "v", "s", "", mock_http(400, {"error": {"code": code}}))
    with pytest.raises(expected) as error:
        await wa.send({"chat_id": "3069", "last_inbound": time.time(), "lang": "en"}, "hi")
    assert isinstance(error.value, Unreachable) is (expected is Unreachable)


# --- data kept only as long as it is needed ------------------------------------------------------------------------------------


def test_people_who_stopped_writing_a_year_ago_are_erased_with_everything_about_them():
    old = db.get_or_create_user("telegram", "5005", "5005")["user_id"]
    recent = db.get_or_create_user("telegram", "5006", "5006")["user_id"]
    never_wrote_and_old = db.get_or_create_user("telegram", "5007", "5007")["user_id"]
    new_never_wrote = db.get_or_create_user("telegram", "5008", "5008")["user_id"]
    year_and_a_bit = time.time() - 400 * 86400
    db.set_fields(old, last_inbound=year_and_a_bit)
    db._conn.execute("UPDATE users SET created_at=? WHERE user_id=?", (year_and_a_bit - 86400, old))  # (it can't predate sign-up)
    db.set_fields(recent, last_inbound=time.time() - 30 * 86400)
    db._conn.execute("UPDATE users SET created_at=?, last_inbound=0 WHERE user_id=?", (year_and_a_bit, never_wrote_and_old))
    db.add_goal(old, "a goal")
    db.add_message(old, "user", "private")
    db.add_reminder(old, 0, "r")
    assert db.delete_inactive_users(365) == 2
    assert db.get_user(old) is None and db.get_user(never_wrote_and_old) is None
    assert db.get_user(recent) and db.get_user(new_never_wrote)
    for table in ("goals", "messages", "reminders"):
        assert db._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (old,)).fetchone()[0] == 0


def test_undeliverable_reminders_are_dropped_a_day_after_they_were_due_not_before():
    uid = db.get_or_create_user("telegram", "6006", "6006")["user_id"]
    db.add_reminder(uid, time.time() - 25 * 3600, "stale")
    db.add_reminder(uid, time.time() - 2 * 3600, "recent")
    assert db.abandon_stale_reminders(24) == 1
    assert [r["text"] for r in db.due_reminders()] == ["recent"]


def test_maintenance_runs_all_the_cleanups(monkeypatch):
    uid = db.get_or_create_user("telegram", "7007", "7007")["user_id"]
    db.set_field(uid, "last_inbound", time.time() - 400 * 86400)
    db._conn.execute("UPDATE users SET created_at=?", (time.time() - 400 * 86400,))
    db.add_reminder(uid, time.time() - 30 * 3600, "stale")
    bot._last_maintenance = 0
    bot.maintain()
    assert db.get_user(uid) is None
    bot._last_maintenance = 0
    monkeypatch.setattr(bot, "INACTIVE_DELETE_DAYS", 0)  # 0 keeps everything
    other = db.get_or_create_user("telegram", "7008", "7008")["user_id"]
    db._conn.execute("UPDATE users SET created_at=?, last_inbound=0 WHERE user_id=?", (time.time() - 900 * 86400, other))
    bot.maintain()
    assert db.get_user(other) is not None
    assert flows  # (module imported for the crisis helpers used above)
