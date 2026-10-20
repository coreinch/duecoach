import sqlite3
import time

from duecoach import db


def test_fresh_database_is_at_latest_version():
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_upgrade_from_the_single_user_schema_keeps_data_and_grandfathers_users(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts REAL, user_id INTEGER);
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, chat_id INTEGER, tz TEXT, notes TEXT DEFAULT '',
                            last_morning TEXT DEFAULT '', last_evening TEXT DEFAULT '');
        CREATE TABLE reminders (id INTEGER PRIMARY KEY, due REAL, text TEXT, sent INTEGER DEFAULT 0, user_id INTEGER);
        INSERT INTO users (user_id, chat_id, tz, notes) VALUES (42, 42, 'Europe/Athens', 'likes timers');
        INSERT INTO messages (role, content, ts, user_id) VALUES ('user', 'hi', 100.0, 42);
        """
    )
    old.commit()
    old.close()
    db.close()
    db.init(path)
    u = db.get_user(42)
    assert u["notes"] == "likes timers"
    assert u["ext_id"] == "42" and u["channel"] == "telegram"
    assert u["consent_at"] and u["tz_set"] == 1  # existing users are not locked out by the new consent step
    assert db.recent_messages(42, 5) == [{"role": "user", "content": "hi"}]
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_init_twice_is_harmless(tmp_path):
    path = str(tmp_path / "again.db")
    db.close()
    db.init(path)
    db.get_or_create_user("telegram", "7", "7")
    db.close()
    db.init(path)
    assert db.get_user(7) is not None


def test_new_users_start_with_checkins_off_and_no_consent():
    u = db.get_or_create_user("telegram", "55", "55", "el")
    assert u["interval_min"] == 0 and not u["consent_at"] and not u["tz_set"] and u["lang"] == "el"


def test_checkin_counts_as_unanswered_only_if_the_user_stayed_silent(user):
    uid = user["user_id"]
    db.set_field(uid, "last_inbound", 100.0)
    db.record_checkin_sent(uid, 100.0)
    assert db.get_user(uid)["unanswered"] == 1
    db.set_field(uid, "last_inbound", 200.0)  # they wrote while the next check-in was being prepared
    db.record_checkin_sent(uid, 100.0)
    assert db.get_user(uid)["unanswered"] == 1  # not counted
    assert db.get_user(uid)["last_proactive"] > 0


def test_failed_checkins_back_off_exponentially(user):
    uid = user["user_id"]
    waits = []
    for _ in range(8):
        db.record_checkin_failed(uid)
        waits.append(round(db.get_user(uid)["checkin_retry_at"] - time.time()))
    assert waits[:3] == [60, 120, 240] and waits[-1] == 1800  # capped at 30 minutes
    db.record_checkin_sent(uid, 0)
    assert db.get_user(uid)["checkin_failures"] == 0


def test_failed_reminders_stay_pending_but_wait_between_attempts(user):
    db.add_reminder(user["user_id"], 0, "x")
    (r,) = db.due_reminders()
    db.mark_failed(r["id"])
    assert db.due_reminders() == []
    db._conn.execute("UPDATE reminders SET retry_at=0")
    assert len(db.due_reminders()) == 1
    db.mark_sent(r["id"])
    assert db.due_reminders() == []


def test_stale_objectives_expire_and_free_their_slot(user):
    uid = user["user_id"]
    for i in range(db.MAX_OPEN_OBJECTIVES):
        assert db.add_objective(uid, None, f"o{i}", "")
    assert db.add_objective(uid, None, "one too many", "") is None
    db._conn.execute("UPDATE objectives SET created=?", (time.time() - 15 * 86400,))
    assert db.expire_stale_objectives() == 3
    assert db.open_objectives(uid) == []
    assert db.add_objective(uid, None, "fresh start", "")


def test_prune_keeps_each_users_latest_messages(user):
    for i in range(50):
        db.add_message(1001, "user", f"m{i}")
    db._conn.execute("UPDATE messages SET ts=?", (time.time() - 200 * 86400,))
    assert db.prune_messages(90, keep_last=40) == 10
    assert db.message_count(1001) == 40 and db.recent_messages(1001, 1)[0]["content"] == "m49"


def test_delete_user_data_removes_everything(user):
    uid = user["user_id"]
    db.add_message(uid, "user", "secret")
    db.add_goal(uid, "g")
    db.add_objective(uid, None, "o", "")
    db.add_tool(uid, "t")
    db.add_reminder(uid, 0, "r")
    db.add_event(uid, "Dentist", time.time() + 3600)
    db.add_turn_stat(uid, "reply", "sent", [], [], 1, 0.5)
    db.add_message(uid, "assistant", "wrong")
    db.flag_last_reply(uid)
    db.add_flagged_draft(uid, ["off_target"], "hi", "a draft", "why")
    db.delete_user_data(uid)
    assert db.get_user(uid) is None
    for table in ("messages", "goals", "objectives", "toolbox", "reminders", "events", "turn_stats", "flags", "flagged_drafts"):
        assert db._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,)).fetchone()[0] == 0


def test_users_for_checkins_skips_those_who_cannot_be_due(user):
    uid = user["user_id"]
    db.set_fields(uid, consent_at=1.0, interval_min=30, snooze_until=0, checkin_retry_at=0)
    assert [u["user_id"] for u in db.users_for_checkins(100.0)] == [uid]
    db.set_fields(uid, snooze_until=500.0)
    assert db.users_for_checkins(100.0) == []
    db.set_fields(uid, snooze_until=0, interval_min=0)
    assert db.users_for_checkins(100.0) == []
    db.set_fields(uid, interval_min=30, consent_at=0)
    assert db.users_for_checkins(100.0) == []


def test_open_timezone_questions_become_flows_when_the_database_is_upgraded(user):
    asking = db.get_or_create_user("telegram", "7001", "7001", "en")["user_id"]
    verifying = db.get_or_create_user("telegram", "7002", "7002", "en")["user_id"]
    db._conn.execute("UPDATE users SET tz_state='asked', tz_attempts=1 WHERE user_id=?", (asking,))
    db._conn.execute("UPDATE users SET tz_state='verify', tz_candidate='Europe/Athens' WHERE user_id=?", (verifying,))
    db._m8_timezone_as_flow()
    import json

    flow = json.loads(db.get_user(asking)["flow_state"])
    assert (flow["flow"], flow["step"], flow["attempts"]) == ("timezone", "asked", 1) and db.get_user(asking)["tz_state"] == ""
    flow = json.loads(db.get_user(verifying)["flow_state"])
    assert (flow["step"], flow["candidate"]) == ("verify", "Europe/Athens") and db.get_user(verifying)["tz_state"] == "done"


def test_a_database_from_before_events_gets_the_new_tables_and_keeps_its_data(tmp_path):
    path = str(tmp_path / "v9.db")
    db.init(path)
    db._conn.execute("DROP TABLE events")
    db._conn.execute("DROP TABLE turn_stats")
    db._conn.execute("DROP TABLE flags")
    db._conn.execute("PRAGMA user_version=9")
    db.add_reminder(7, 123.0, "keep me")
    db.close()
    db.init(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert db._conn.execute("SELECT text FROM reminders").fetchone()[0] == "keep me"
    assert db.add_event(7, "Dentist", time.time() + 60) == 1


def test_events_for_the_state_hide_old_and_long_canceled_ones_and_are_capped(user):
    now = time.time()
    ahead = db.add_event(1001, "Ahead", now + 3600)
    done = db.add_event(1001, "Yesterday", now + 10)
    db._conn.execute("UPDATE events SET start=? WHERE id=?", (now - 2 * 86400, done))
    off = db.add_event(1001, "Off", now + 7200)
    db.cancel_event(1001, off)
    stale = db.add_event(1001, "Off long ago", now + 7200)
    db.cancel_event(1001, stale)
    db._conn.execute("UPDATE events SET changed=? WHERE id=?", (now - 2 * 86400, stale))
    assert [(e["title"], e["status"]) for e in db.events_for_state(1001, now)] == [("Ahead", "upcoming"), ("Off", "canceled")]
    assert not db.cancel_event(1001, off) and not db.move_event(1001, off, now + 9000)  # a canceled event cannot be moved or canceled again
    assert db.move_event(1001, ahead, now + 5000) and not db.move_event(2002, ahead, now + 1)  # only its owner moves it
    for i in range(db.MAX_UPCOMING_EVENTS):
        db.add_event(1001, f"e{i}", now + 100 + i)
    assert db.add_event(1001, "one too many", now + 500) is None


def test_old_events_and_statistics_are_pruned(user):
    db.add_event(1001, "Long ago", time.time() + 1)
    db._conn.execute("UPDATE events SET start=?", (time.time() - 30 * 86400,))
    db.add_event(1001, "Recent", time.time() + 3600)
    db.add_turn_stat(1001, "reply", "sent", [], [], 1, 1.0)
    db._conn.execute("UPDATE turn_stats SET ts=?", (time.time() - 60 * 86400,))
    db.add_turn_stat(1001, "checkin", "dropped", ["off_target"], [], 2, 1.0)
    assert db.prune_events() == 1 and db.prune_turn_stats() == 1
    assert db._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert db.turn_stats_summary(0) == {"checkin:dropped": 1, "flag:off_target": 1}


def test_flagging_needs_a_coach_message_and_points_at_the_latest_one(user):
    assert not db.flag_last_reply(1001)
    db.add_message(1001, "assistant", "first")
    db.add_message(1001, "assistant", "second")
    db.add_message(1001, "user", "no")
    assert db.flag_last_reply(1001)
    assert db._conn.execute("SELECT content FROM messages WHERE id=(SELECT message_id FROM flags)").fetchone()[0] == "second"


def test_correcting_the_timezone_keeps_events_at_the_clock_time_the_user_said(user):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    db.set_fields(1001, tz="UTC", tz_set=1)
    said = (datetime.now(ZoneInfo("UTC")) + timedelta(days=2)).replace(hour=10, minute=30, second=0, microsecond=0)
    eid = db.add_event(1001, "Dentist", said.timestamp())
    db.confirm_timezone(1001, "Europe/Athens")
    moved = datetime.fromtimestamp(db.events_for_state(1001, 0)[0]["start"], ZoneInfo("Europe/Athens"))
    assert (moved.date(), moved.hour, moved.minute) == (said.date(), 10, 30) and eid == 1
    db.confirm_timezone(1001, "Not/AZone")  # an unknown zone leaves the events alone
    assert db.events_for_state(1001, 0)[0]["start"] == moved.timestamp()


def test_correcting_the_timezone_moves_clock_time_reminders_but_not_timers(user):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    db.set_fields(1001, tz="UTC", tz_set=1)
    said = (datetime.now(ZoneInfo("UTC")) + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    at_nine = db.add_reminder(1001, said.timestamp(), "Call the bank", clock=True)
    daily = db.add_reminder(1001, said.timestamp(), "Medication", repeat_daily=True)  # a daily one is always tied to the clock
    timer_due = time.time() + 1200
    timer = db.add_reminder(1001, timer_due, "Stretch")  # "in 20 minutes"
    db.confirm_timezone(1001, "Europe/Athens")
    due = {r["id"]: r["due"] for r in db.pending_reminders(1001)}
    for rid in (at_nine, daily):
        local = datetime.fromtimestamp(due[rid], ZoneInfo("Europe/Athens"))
        assert (local.date(), local.hour, local.minute) == (said.date(), 9, 0)
    assert due[timer] == timer_due


def test_a_message_a_user_reported_is_not_deleted_by_retention(user):
    old = time.time() - 400 * 86400
    db.add_message(1001, "assistant", "the wrong one")
    db.flag_last_reply(1001)
    for i in range(45):
        db.add_message(1001, "user", f"m{i}")
    db._conn.execute("UPDATE messages SET ts=?", (old,))
    db.prune_messages(90)
    assert db._conn.execute("SELECT COUNT(*) FROM messages WHERE content='the wrong one'").fetchone()[0] == 1
    assert db.message_count(1001) == 41


def test_flagged_drafts_are_kept_for_a_few_days_only(user):
    uid = user["user_id"]
    db.add_flagged_draft(uid, ["unbacked", "off_target"], "u" * 900, "d" * 2000, "p" * 3000)
    row = db._conn.execute("SELECT * FROM flagged_drafts").fetchone()
    assert row["kinds"] == "unbacked,off_target" and (len(row["user_text"]), len(row["draft"]), len(row["problems"])) == (600, 1200, 1500)
    assert db.prune_flagged_drafts(7) == 0
    db._conn.execute("UPDATE flagged_drafts SET ts=?", (time.time() - 8 * 86400,))
    assert db.prune_flagged_drafts(7) == 1
