"""SQLite storage. Call init() once at startup (importing this module has no side effects)."""

import os
import sqlite3
import time

from .config import DB_PATH, EVENING_HOUR, MORNING_HOUR, TIMEZONE

_conn: sqlite3.Connection | None = None


def _columns(table: str) -> set[str]:
    return {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}


def _ensure_columns(table: str, columns: dict[str, str]) -> None:
    """Add any missing columns (older databases were grown with ad-hoc ALTERs, so upgrades are idempotent)."""
    have = _columns(table)
    for name, ddl in columns.items():
        if name not in have:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _m1_baseline() -> None:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS seen_messages (key TEXT PRIMARY KEY, ts REAL);
        CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS objectives (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS toolbox (id INTEGER PRIMARY KEY);
        """
    )
    _ensure_columns("messages", {"role": "TEXT", "content": "TEXT", "ts": "REAL", "user_id": "INTEGER"})
    _ensure_columns(
        "reminders",
        {
            "due": "REAL",
            "text": "TEXT",
            "sent": "INTEGER DEFAULT 0",
            "user_id": "INTEGER",
            "attempts": "INTEGER DEFAULT 0",
            "retry_at": "REAL DEFAULT 0",
        },
    )
    _ensure_columns(
        "users",
        {
            "chat_id": "INTEGER",
            "tz": "TEXT",
            "notes": "TEXT DEFAULT ''",
            "last_morning": "TEXT DEFAULT ''",
            "last_evening": "TEXT DEFAULT ''",
            "morning_hour": f"INTEGER DEFAULT {MORNING_HOUR}",
            "evening_hour": f"INTEGER DEFAULT {EVENING_HOUR}",
            "lang": "TEXT DEFAULT 'en'",
            "channel": "TEXT DEFAULT 'telegram'",
            "ext_id": "TEXT",
            "last_inbound": "REAL DEFAULT 0",
            "created_at": "REAL",
            "unanswered": "INTEGER DEFAULT 0",
            "last_proactive": "REAL DEFAULT 0",
            "interval_min": "INTEGER DEFAULT 0",
            "snooze_until": "REAL DEFAULT 0",
        },
    )
    _ensure_columns("goals", {"user_id": "INTEGER", "text": "TEXT", "status": "TEXT DEFAULT 'active'", "created": "REAL"})
    _ensure_columns(
        "objectives",
        {
            "user_id": "INTEGER",
            "goal_id": "INTEGER",
            "text": "TEXT",
            "incentive": "TEXT DEFAULT ''",
            "status": "TEXT DEFAULT 'open'",
            "barrier": "TEXT DEFAULT ''",
            "note": "TEXT DEFAULT ''",
            "created": "REAL",
            "closed": "REAL",
        },
    )
    _ensure_columns("toolbox", {"user_id": "INTEGER", "text": "TEXT", "created": "REAL"})
    _conn.execute("UPDATE users SET ext_id=CAST(user_id AS TEXT) WHERE ext_id IS NULL")
    _conn.execute(
        "UPDATE users SET created_at=COALESCE((SELECT MIN(ts) FROM messages m WHERE m.user_id=users.user_id), ?) WHERE created_at IS NULL",
        (time.time(),),
    )
    _conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, id);
        CREATE INDEX IF NOT EXISTS idx_goals_user ON goals (user_id);
        CREATE INDEX IF NOT EXISTS idx_objectives_user ON objectives (user_id);
        CREATE INDEX IF NOT EXISTS idx_toolbox_user ON toolbox (user_id);
        CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders (sent, due);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_identity ON users (channel, ext_id);
        """
    )


def _m2_consent_and_resilience() -> None:
    """Consent, confirmed timezone, check-in retry back-off, weekly-review tracking. Existing users are grandfathered in."""
    had_users = _conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    _ensure_columns(
        "users",
        {
            "consent_at": "REAL",
            "tz_set": "INTEGER DEFAULT 0",
            "checkin_failures": "INTEGER DEFAULT 0",
            "checkin_retry_at": "REAL DEFAULT 0",
            "last_review": "TEXT DEFAULT ''",
        },
    )
    if had_users:
        _conn.execute("UPDATE users SET consent_at=COALESCE(created_at, ?), tz_set=1 WHERE consent_at IS NULL", (time.time(),))


def _m3_timezone_onboarding() -> None:
    """The bot asks new users for their city itself: where that conversation stands, and the zone awaiting a yes/no."""
    _ensure_columns("users", {"tz_state": "TEXT DEFAULT ''", "tz_candidate": "TEXT DEFAULT ''", "tz_attempts": "INTEGER DEFAULT 0"})


def _m4_coaching_flows() -> None:
    """Code-led goal / weekly step / follow-up conversations: the flow in progress (JSON) and when each was last offered."""
    _ensure_columns(
        "users",
        {
            "flow_state": "TEXT DEFAULT ''",
            "goal_asked_at": "REAL DEFAULT 0",
            "obj_asked_at": "REAL DEFAULT 0",
            "followup_asked_at": "REAL DEFAULT 0",
        },
    )


def _m5_question_spacing() -> None:
    """How many chat messages existed when the bot last added a question of its own, so questions are never back to back."""
    _ensure_columns("users", {"question_at_count": "INTEGER DEFAULT -100"})


MIGRATIONS = [_m1_baseline, _m2_consent_and_resilience, _m3_timezone_onboarding, _m4_coaching_flows, _m5_question_spacing]


def init(path: str | None = None) -> None:
    """Open the database (creating or upgrading it) and make the module ready for use."""
    global _conn
    path = path or DB_PATH
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _conn.execute("PRAGMA busy_timeout=5000")
    try:
        os.chmod(path, 0o600)  # chats about mental health: keep the file private to its owner
    except OSError:
        pass
    version = _conn.execute("PRAGMA user_version").fetchone()[0]
    for number, migrate in enumerate(MIGRATIONS[version:], start=version + 1):
        migrate()
        _conn.execute(f"PRAGMA user_version={number}")
        _conn.commit()


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


USER_FIELDS = {
    "chat_id",
    "tz",
    "notes",
    "last_morning",
    "last_evening",
    "morning_hour",
    "evening_hour",
    "lang",
    "last_inbound",
    "unanswered",
    "last_proactive",
    "interval_min",
    "snooze_until",
    "consent_at",
    "tz_set",
    "checkin_failures",
    "checkin_retry_at",
    "last_review",
    "tz_state",
    "tz_candidate",
    "tz_attempts",
    "flow_state",
    "goal_asked_at",
    "obj_asked_at",
    "followup_asked_at",
    "question_at_count",
}


# --- users ---


def get_or_create_user(channel: str, ext_id: str, chat_id: str, lang: str = "en") -> sqlite3.Row:
    """Find the user behind a channel identity, creating them on first contact.

    Telegram users keep their Telegram id as internal id; users on other channels get negative ids so the two can't clash.
    New users start with check-ins off, no consent and an unconfirmed timezone.
    """
    row = get_user_by_identity(channel, ext_id)
    if row:
        if str(row["chat_id"]) != str(chat_id):
            _conn.execute("UPDATE users SET chat_id=? WHERE user_id=?", (chat_id, row["user_id"]))
            _conn.commit()
        return get_user(row["user_id"])
    if channel == "telegram":
        user_id = int(ext_id)
    else:
        user_id = min(-1, (_conn.execute("SELECT MIN(user_id) FROM users").fetchone()[0] or 0) - 1)
    _conn.execute(
        "INSERT INTO users (user_id, channel, ext_id, chat_id, tz, lang, created_at, morning_hour, evening_hour, interval_min, tz_set)"
        " VALUES (?,?,?,?,?,?,?,?,?,0,0)",
        (user_id, channel, ext_id, chat_id, TIMEZONE, lang, time.time(), MORNING_HOUR, EVENING_HOUR),
    )
    _conn.commit()
    return get_user(user_id)


def get_user(user_id: int) -> sqlite3.Row | None:
    return _conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user_by_identity(channel: str, ext_id: str) -> sqlite3.Row | None:
    return _conn.execute("SELECT * FROM users WHERE channel=? AND ext_id=?", (channel, ext_id)).fetchone()


def all_users() -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM users").fetchall()


def set_fields(user_id: int, **fields: str | int | float) -> None:
    """Update several user columns in one statement and one commit."""
    assert fields and fields.keys() <= USER_FIELDS, fields.keys()
    assignments = ", ".join(f"{name}=?" for name in fields)
    _conn.execute(f"UPDATE users SET {assignments} WHERE user_id=?", (*fields.values(), user_id))
    _conn.commit()


def set_field(user_id: int, field: str, value: str | int | float) -> None:
    set_fields(user_id, **{field: value})


def get_notes(user_id: int) -> str:
    u = get_user(user_id)
    return (u["notes"] if u else "") or ""


def record_checkin_sent(user_id: int, seen_inbound: float) -> None:
    """A proactive check-in went out. It only counts as unanswered if the user hasn't written since we started preparing it."""
    _conn.execute(
        "UPDATE users SET last_proactive=?, checkin_failures=0, checkin_retry_at=0,"
        " unanswered=unanswered + (CASE WHEN last_inbound=? THEN 1 ELSE 0 END) WHERE user_id=?",
        (time.time(), seen_inbound, user_id),
    )
    _conn.commit()


def record_checkin_failed(user_id: int) -> None:
    """Back off 1, 2, 4 ... up to 30 minutes before trying this user's check-in again."""
    failures = (get_user(user_id)["checkin_failures"] or 0) + 1
    set_fields(user_id, checkin_failures=failures, checkin_retry_at=time.time() + min(60 * 2 ** (failures - 1), 1800))


def delete_user_data(user_id: int) -> None:
    """Erase everything stored about this user."""
    for table in ("messages", "reminders", "goals", "objectives", "toolbox"):
        _conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    _conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    _conn.commit()


# --- messages ---


def add_message(user_id: int, role: str, content: str) -> None:
    _conn.execute("INSERT INTO messages (user_id, role, content, ts) VALUES (?,?,?,?)", (user_id, role, content, time.time()))
    _conn.commit()


def recent_messages(user_id: int, n: int) -> list[dict]:
    rows = _conn.execute("SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def message_count(user_id: int) -> int:
    return _conn.execute("SELECT COUNT(*) FROM messages WHERE user_id=?", (user_id,)).fetchone()[0]


def prune_messages(retention_days: int, keep_last: int = 40) -> int:
    """Delete messages older than the retention period, always keeping each user's most recent `keep_last`."""
    cur = _conn.execute(
        "DELETE FROM messages WHERE ts<? AND id NOT IN ("
        " SELECT id FROM messages m2 WHERE m2.user_id=messages.user_id ORDER BY id DESC LIMIT ?)",
        (time.time() - retention_days * 86400, keep_last),
    )
    _conn.commit()
    return cur.rowcount


def mark_seen(key: str) -> bool:
    """True the first time a webhook message id is seen. Webhooks are retried, so callers use this to drop duplicates."""
    now = time.time()
    _conn.execute("DELETE FROM seen_messages WHERE ts<?", (now - 3 * 86400,))
    inserted = _conn.execute("INSERT OR IGNORE INTO seen_messages (key, ts) VALUES (?,?)", (key, now)).rowcount == 1
    _conn.commit()
    return inserted


# --- reminders ---


def add_reminder(user_id: int, due: float, text: str) -> None:
    _conn.execute("INSERT INTO reminders (user_id, due, text) VALUES (?,?,?)", (user_id, due, text))
    _conn.commit()


def due_reminders() -> list[sqlite3.Row]:
    now = time.time()
    return _conn.execute("SELECT id, text, user_id FROM reminders WHERE sent=0 AND due<=? AND retry_at<=?", (now, now)).fetchall()


def mark_failed(reminder_id: int) -> None:
    """A send failed: keep the reminder pending but wait 1, 2, 4 ... up to 30 minutes before the next attempt."""
    attempts = _conn.execute("SELECT attempts FROM reminders WHERE id=?", (reminder_id,)).fetchone()[0] or 0
    _conn.execute(
        "UPDATE reminders SET attempts=?, retry_at=? WHERE id=?", (attempts + 1, time.time() + min(60 * 2**attempts, 1800), reminder_id)
    )
    _conn.commit()


def mark_sent(reminder_id: int) -> None:
    _conn.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
    _conn.commit()


# --- coaching record: goals, weekly objectives, toolbox ---

MAX_GOALS = 4
MAX_OPEN_OBJECTIVES = 3
MAX_TOOLBOX = 30
OBJECTIVE_MAX_AGE_DAYS = 14


def add_goal(user_id: int, text: str) -> int | None:
    """None when the user already has the maximum number of active goals."""
    if len(active_goals(user_id)) >= MAX_GOALS:
        return None
    cur = _conn.execute("INSERT INTO goals (user_id, text, created) VALUES (?,?,?)", (user_id, text, time.time()))
    _conn.commit()
    return cur.lastrowid


def active_goals(user_id: int) -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM goals WHERE user_id=? AND status='active' ORDER BY id", (user_id,)).fetchall()


def retire_goal(user_id: int, goal_id: int, status: str) -> bool:
    cur = _conn.execute("UPDATE goals SET status=? WHERE id=? AND user_id=? AND status='active'", (status, goal_id, user_id))
    _conn.commit()
    return cur.rowcount == 1


def add_objective(user_id: int, goal_id: int | None, text: str, incentive: str) -> int | None:
    """None when the user already has the maximum number of open objectives."""
    if len(open_objectives(user_id)) >= MAX_OPEN_OBJECTIVES:
        return None
    if goal_id is not None and not _conn.execute("SELECT 1 FROM goals WHERE id=? AND user_id=?", (goal_id, user_id)).fetchone():
        goal_id = None
    cur = _conn.execute(
        "INSERT INTO objectives (user_id, goal_id, text, incentive, created) VALUES (?,?,?,?,?)",
        (user_id, goal_id, text, incentive, time.time()),
    )
    _conn.commit()
    return cur.lastrowid


def open_objectives(user_id: int) -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM objectives WHERE user_id=? AND status='open' ORDER BY id", (user_id,)).fetchall()


def recent_closed_objectives(user_id: int, days: int = 7) -> list[sqlite3.Row]:
    return _conn.execute(
        "SELECT * FROM objectives WHERE user_id=? AND status!='open' AND closed>? ORDER BY closed", (user_id, time.time() - days * 86400)
    ).fetchall()


def expire_stale_objectives(max_age_days: int = OBJECTIVE_MAX_AGE_DAYS) -> int:
    """Objectives are weekly steps: one left open for two weeks is abandoned. Expiring them frees the slot for new ones."""
    now = time.time()
    cur = _conn.execute(
        "UPDATE objectives SET status='expired', closed=?, note=CASE WHEN note='' THEN 'left open for too long' ELSE note END"
        " WHERE status='open' AND created<?",
        (now, now - max_age_days * 86400),
    )
    _conn.commit()
    return cur.rowcount


def set_objective_incentive(user_id: int, objective_id: int, incentive: str) -> None:
    _conn.execute("UPDATE objectives SET incentive=? WHERE id=? AND user_id=?", (incentive, objective_id, user_id))
    _conn.commit()


def close_objective(user_id: int, objective_id: int, status: str, barrier: str, note: str) -> bool:
    cur = _conn.execute(
        "UPDATE objectives SET status=?, barrier=?, note=?, closed=? WHERE id=? AND user_id=? AND status='open'",
        (status, barrier, note, time.time(), objective_id, user_id),
    )
    _conn.commit()
    return cur.rowcount == 1


def add_tool(user_id: int, text: str) -> int | None:
    if _conn.execute("SELECT COUNT(*) FROM toolbox WHERE user_id=?", (user_id,)).fetchone()[0] >= MAX_TOOLBOX:
        return None
    cur = _conn.execute("INSERT INTO toolbox (user_id, text, created) VALUES (?,?,?)", (user_id, text, time.time()))
    _conn.commit()
    return cur.lastrowid


def toolbox(user_id: int, n: int = MAX_TOOLBOX) -> list[sqlite3.Row]:
    return _conn.execute("SELECT * FROM toolbox WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
