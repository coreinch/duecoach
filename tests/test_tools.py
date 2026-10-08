import json
import time

from duecoach import db, playbook, tools


def run(uid, tool, **args):
    return tools.run_tool(uid, tool, json.dumps(args))


def test_goals_are_deduplicated_and_capped(user):
    uid = user["user_id"]
    assert run(uid, "add_goal", text="Be on time at least 4 days a week").startswith("ok: goal #")
    assert "already saved" in run(uid, "add_goal", text="  be on TIME at least 4 days a week. ")
    for i in range(3):
        run(uid, "add_goal", text=f"goal {i}")
    assert run(uid, "add_goal", text="one more").startswith("error: already 4 active goals")


def test_objective_flow_and_reward_is_added_to_a_duplicate(user):
    uid = user["user_id"]
    first = run(uid, "add_objective", text="Lay out clothes at 9pm")
    again = run(uid, "add_objective", text="Lay out clothes at 9pm", incentive="coffee")
    assert "already saved" in again and "incentive added" in again
    assert db.open_objectives(uid)[0]["incentive"] == "coffee" and first.startswith("ok")
    oid = db.open_objectives(uid)[0]["id"]
    assert run(uid, "close_objective", objective_id=oid, outcome="missed", barrier="forgot", note="show").startswith("ok")
    assert "no such open objective" in run(uid, "close_objective", objective_id=oid, outcome="done")
    assert run(uid, "close_objective", objective_id=oid, outcome="bogus").startswith("error")


def test_users_cannot_touch_each_others_records(user):
    other = db.get_or_create_user("telegram", "2002", "2002")["user_id"]
    run(user["user_id"], "add_objective", text="mine")
    oid = db.open_objectives(user["user_id"])[0]["id"]
    assert "no such open objective" in run(other, "close_objective", objective_id=oid, outcome="done")
    assert "no such active goal" in run(other, "retire_goal", goal_id=1, outcome="dropped")


def test_bad_input_returns_errors_instead_of_raising(user):
    uid = user["user_id"]
    assert tools.run_tool(uid, "add_goal", "not json").startswith("error")
    assert tools.run_tool(uid, "add_goal", "[]").startswith("error")
    assert tools.run_tool(uid, "no_such_tool", "{}").startswith("error: unknown tool")
    assert run(uid, "add_goal", text="").startswith("error")
    assert run(uid, "set_reminder", minutes=-5, message="x").startswith("error")
    assert run(uid, "set_reminder", minutes="soon", message="x").startswith("error")


def test_set_reminder_schedules_in_the_future(user):
    assert run(user["user_id"], "set_reminder", minutes=10, message="stretch").startswith("ok")
    assert db.due_reminders() == []
    (due,) = db._conn.execute("SELECT due FROM reminders").fetchone()
    assert 590 < due - time.time() < 610


def test_set_reminder_at_a_local_time_and_daily_repeat(user):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    uid = user["user_id"]
    zone = ZoneInfo(db.get_user(uid)["tz"])
    tomorrow = (datetime.now(zone) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    out = run(uid, "set_reminder", at=tomorrow.strftime("%Y-%m-%d %H:%M"), message="meds", daily=True)
    assert out.startswith("ok") and "every day" in out
    (rem,) = db.pending_reminders(uid)
    assert rem["due"] == tomorrow.timestamp() and rem["repeat_daily"]
    db._conn.execute(
        "UPDATE reminders SET due=?", ((tomorrow - timedelta(days=2)).timestamp(),)
    )  # now due: delivering it schedules the next day instead
    db.mark_sent(rem["id"])
    (rem,) = db.pending_reminders(uid)
    assert rem["due"] > time.time() and datetime.fromtimestamp(rem["due"], zone).hour == 9
    assert "daily" in tools.coaching_state(uid) and "meds" in tools.coaching_state(uid)
    assert run(uid, "cancel_reminder", reminder_id=rem["id"]).startswith("ok") and db.pending_reminders(uid) == []


def test_set_reminder_rejects_bad_times_with_a_helpful_error(user):
    uid = user["user_id"]
    assert "already passed" in run(uid, "set_reminder", at="2020-01-01 09:00", message="x")
    assert "YYYY-MM-DD" in run(uid, "set_reminder", at="9 o'clock", message="x")
    assert "give `at`" in run(uid, "set_reminder", message="x")
    for i in range(db.MAX_PENDING_REMINDERS):
        assert run(uid, "set_reminder", minutes=10 + i, message=f"r{i}").startswith("ok")
    assert "cancel one first" in run(uid, "set_reminder", minutes=5, message="one too many")


def test_checkins_need_a_confirmed_timezone():
    uid = db.get_or_create_user("telegram", "3003", "3003")["user_id"]
    assert "set_timezone first" in run(uid, "set_checkins", interval_minutes=30)
    assert run(uid, "set_timezone", name="Nowhere/Land").startswith("error")
    assert run(uid, "set_timezone", name="America/New_York").startswith("ok")
    assert run(uid, "set_checkins", interval_minutes=30).startswith("ok") and db.get_user(uid)["interval_min"] == 30
    assert run(uid, "set_checkins", interval_minutes=5).startswith("error")
    assert run(uid, "set_checkins", interval_minutes=0).startswith("ok") and db.get_user(uid)["interval_min"] == 0


def test_snooze_pauses_and_resumes(user):
    uid = user["user_id"]
    run(uid, "snooze_checkins", minutes=60)
    assert db.get_user(uid)["snooze_until"] > time.time() + 3000
    run(uid, "snooze_checkins", minutes=0)
    assert db.get_user(uid)["snooze_until"] == 0
    assert run(uid, "snooze_checkins", minutes=99999).startswith("error")


def test_coaching_state_shows_time_and_record(user):
    uid = user["user_id"]
    run(uid, "add_goal", text="Sleep before midnight for a month")
    state = tools.coaching_state(uid)
    assert "Europe/Athens" in state and "confirmed by the user" in state
    assert "Sleep before midnight" in state and "Check-ins: off" in state


def test_every_playbook_card_is_complete_and_the_whole_text_is_bounded():
    ids = [c["id"] for c in playbook.CARDS]
    assert len(ids) == len(set(ids)) >= 50
    assert all(set(c) == {"id", "when", "how", "source"} and all(c.values()) for c in playbook.CARDS)
    assert len(playbook.full_text().split()) < 6000
