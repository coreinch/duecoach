import sqlite3
import time

from coach import db


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


def test_other_channels_get_negative_ids_that_do_not_clash_with_telegram():
    a = db.get_or_create_user("viber", "abc=", "abc=")
    b = db.get_or_create_user("whatsapp", "3069", "3069")
    assert a["user_id"] == -1 and b["user_id"] == -2
    assert db.get_or_create_user("viber", "abc=", "abc=")["user_id"] == -1


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
    db.delete_user_data(uid)
    assert db.get_user(uid) is None
    for table in ("messages", "goals", "objectives", "toolbox", "reminders"):
        assert db._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,)).fetchone()[0] == 0


def test_webhook_duplicates_are_detected():
    assert db.mark_seen("whatsapp:abc") is True
    assert db.mark_seen("whatsapp:abc") is False


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
