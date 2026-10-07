"""Tools the coach can call (OpenAI function-calling format) and the coaching-state summary shown to the model."""

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, playbook

MAX_REMINDER_MINUTES = 60 * 24 * 30
BARRIERS = ["forgot", "didnt_know_how", "confused", "avoidance", "low_motivation", "other"]


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOLS = [
    _fn(
        "get_strategy",
        "Read the coaching playbook card for a technique before you suggest it. Pick the id from the playbook index.",
        {"id": {"type": "string", "enum": playbook.IDS}},
        ["id"],
    ),
    _fn(
        "set_reminder",
        "Send the user a message after a delay: a timer, a nudge, or a reminder. Call it once per reminder.",
        {
            "minutes": {"type": "number", "description": "Minutes from now until the message is sent (1 to 43200)."},
            "message": {"type": "string", "description": "Short message to send, in the user's language."},
        },
        ["minutes", "message"],
    ),
    _fn(
        "snooze_checkins",
        "Pause your own proactive check-ins because the user is busy, in a meeting, driving, sleeping, or wants space. 0 resumes.",
        {"minutes": {"type": "number", "description": "How long to stay quiet, 0 to 10080 (7 days)."}},
        ["minutes"],
    ),
    _fn(
        "set_timezone",
        "Save the user's timezone once they've told you their city or region. Needed before check-ins can be turned on.",
        {"name": {"type": "string", "description": "IANA timezone name such as Europe/Athens or America/New_York."}},
        ["name"],
    ),
    _fn(
        "set_checkins",
        "Turn proactive check-ins on or off, only when the user asks for them. Their timezone must be saved first.",
        {"interval_minutes": {"type": "number", "description": "Minutes between check-ins, 15 to 240; 0 turns them off."}},
        ["interval_minutes"],
    ),
    _fn(
        "add_goal",
        "Save a long-term coaching goal once the user has agreed its wording. It must be measurable, say how, and have a time frame. Max 4 active goals.",
        {"text": {"type": "string", "description": "The goal, in the user's language."}},
        ["text"],
    ),
    _fn(
        "retire_goal",
        "Mark a goal as achieved or dropped.",
        {"goal_id": {"type": "integer"}, "outcome": {"type": "string", "enum": ["achieved", "dropped"]}},
        ["goal_id", "outcome"],
    ),
    _fn(
        "add_objective",
        "Save a small objective for the coming week that the user agreed to, with when/where in the text. Max 3 open at once.",
        {
            "text": {"type": "string", "description": "The objective including when/where, in the user's language."},
            "goal_id": {"type": "integer", "description": "The goal it serves, if any."},
            "incentive": {"type": "string", "description": "The reward or consequence the user chose, if any."},
        },
        ["text"],
    ),
    _fn(
        "close_objective",
        "Record how an open objective went, after the user told you.",
        {
            "objective_id": {"type": "integer"},
            "outcome": {"type": "string", "enum": ["done", "partial", "missed", "dropped"]},
            "barrier": {"type": "string", "enum": BARRIERS, "description": "Main obstacle if it was not fully done."},
            "note": {"type": "string", "description": "What helped or what got in the way, briefly."},
        },
        ["objective_id", "outcome"],
    ),
    _fn(
        "save_to_toolbox",
        "Save a strategy that worked for the user (or a mantra they chose) so you can bring it back later.",
        {"text": {"type": "string", "description": "The strategy in one short line, in the user's language."}},
        ["text"],
    ),
]


def run_tool(user_id: int, name: str, arguments: str) -> str:
    """Execute one tool call; the returned string goes back to the model as the tool result."""
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise TypeError
        result = _DISPATCH[name](user_id, args)
    except KeyError as e:
        return f"error: unknown tool {name}" if name not in _DISPATCH else f"error: missing argument {e}"
    except (ValueError, TypeError):
        return f"error: invalid arguments for {name}"
    return result


def _norm(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" .!;:")


def _text(args: dict, key: str = "text") -> str:
    value = str(args[key]).strip()
    if not value:
        raise ValueError(key)
    return value[:500]


def _set_reminder(uid: int, a: dict) -> str:
    minutes = float(a["minutes"])
    if not 0 < minutes <= MAX_REMINDER_MINUTES:
        return f"error: minutes must be between 0 and {MAX_REMINDER_MINUTES}"
    db.add_reminder(uid, time.time() + minutes * 60, _text(a, "message"))
    return f"ok: reminder set for {minutes:g} minutes from now"


def _get_strategy(uid: int, a: dict) -> str:
    return playbook.lookup(str(a["id"])) or "error: unknown strategy id"


def _set_timezone(uid: int, a: dict) -> str:
    name = _text(a, "name")
    try:
        ZoneInfo(name)
    except Exception:
        return "error: unknown timezone; use an IANA name like Europe/Athens"
    db.set_fields(uid, tz=name, tz_set=1)
    return f"ok: timezone set to {name}"


def _set_checkins(uid: int, a: dict) -> str:
    minutes = float(a["interval_minutes"])
    if minutes == 0:
        db.set_field(uid, "interval_min", 0)
        return "ok: check-ins turned off"
    if not 15 <= minutes <= 240:
        return "error: interval_minutes must be 0 or between 15 and 240"
    if not db.get_user(uid)["tz_set"]:
        return "error: ask for their timezone and save it with set_timezone first"
    db.set_field(uid, "interval_min", int(minutes))
    return f"ok: check-ins about every {int(minutes)} minutes during their day"


def _snooze_checkins(uid: int, a: dict) -> str:
    minutes = float(a["minutes"])
    if not 0 <= minutes <= 10080:
        return "error: minutes must be between 0 and 10080"
    db.set_field(uid, "snooze_until", time.time() + minutes * 60 if minutes else 0)
    return f"ok: check-ins paused for {minutes:g} minutes" if minutes else "ok: check-ins resumed"


def _add_goal(uid: int, a: dict) -> str:
    text = _text(a)
    for g in db.active_goals(uid):  # models sometimes repeat a call; saving twice would clutter the record
        if _norm(g["text"]) == _norm(text):
            return f"ok: goal #{g['id']} was already saved"
    gid = db.add_goal(uid, text)
    return f"ok: goal #{gid} saved" if gid else f"error: already {db.MAX_GOALS} active goals; ask which one to retire first"


def _retire_goal(uid: int, a: dict) -> str:
    ok = db.retire_goal(uid, int(a["goal_id"]), "achieved" if a["outcome"] == "achieved" else "dropped")
    return "ok: goal updated" if ok else "error: no such active goal"


def _add_objective(uid: int, a: dict) -> str:
    goal_id = int(a["goal_id"]) if a.get("goal_id") is not None else None
    text, incentive = _text(a), str(a.get("incentive") or "").strip()[:200]
    for o in db.open_objectives(uid):
        if _norm(o["text"]) == _norm(text):
            if incentive and not o["incentive"]:
                db.set_objective_incentive(uid, o["id"], incentive)
            return f"ok: objective #{o['id']} was already saved" + (" (incentive added)" if incentive and not o["incentive"] else "")
    oid = db.add_objective(uid, goal_id, text, incentive)
    return f"ok: objective #{oid} saved" if oid else f"error: already {db.MAX_OPEN_OBJECTIVES} open objectives; close one first"


def _close_objective(uid: int, a: dict) -> str:
    if a["outcome"] not in ("done", "partial", "missed", "dropped"):
        raise ValueError("outcome")
    barrier = str(a.get("barrier") or "")
    barrier = barrier if barrier in BARRIERS else ""
    ok = db.close_objective(uid, int(a["objective_id"]), a["outcome"], barrier, str(a.get("note") or "").strip()[:300])
    return "ok: objective recorded" if ok else "error: no such open objective"


def _save_to_toolbox(uid: int, a: dict) -> str:
    text = _text(a)
    if any(_norm(t["text"]) == _norm(text) for t in db.toolbox(uid)):
        return "ok: already in the toolbox"
    return "ok: saved to toolbox" if db.add_tool(uid, text) else "error: toolbox is full"


_DISPATCH = {
    "set_timezone": _set_timezone,
    "set_checkins": _set_checkins,
    "snooze_checkins": _snooze_checkins,
    "get_strategy": _get_strategy,
    "set_reminder": _set_reminder,
    "add_goal": _add_goal,
    "retire_goal": _retire_goal,
    "add_objective": _add_objective,
    "close_objective": _close_objective,
    "save_to_toolbox": _save_to_toolbox,
}


def _ago(ts: float) -> str:
    days = int((time.time() - ts) // 86400)
    return "today" if days < 1 else f"{days}d ago"


def coaching_state(user_id: int) -> str:
    """The user's coaching record as text for the system prompt (ids let the model refer to items in tool calls)."""
    user = db.get_user(user_id)
    weeks = int((time.time() - (user["created_at"] or time.time())) // (7 * 86400)) + 1
    goals, open_obj = db.active_goals(user_id), db.open_objectives(user_id)
    try:
        local = datetime.now(ZoneInfo(user["tz"]))
    except Exception:
        local = datetime.now(ZoneInfo("UTC"))
    tz_note = "confirmed by the user" if user["tz_set"] else "NOT confirmed yet (the system asks for it; do not ask yourself)"
    checkins = (
        f"on, about every {user['interval_min']} min between {user['morning_hour']}:00 and {user['evening_hour']}:59"
        if user["interval_min"] and user["morning_hour"] >= 0 and user["evening_hour"] >= 0
        else "off (offer them if it fits)"
    )
    lines = [
        f"Now: {local.strftime('%A %Y-%m-%d %H:%M')} local time ({user['tz']}, {tz_note}). Check-ins: {checkins}.",
        f"Coaching record (week {weeks} of working together):",
    ]
    lines.append("Goals:" if goals else "Goals: none yet, so you are in the intake stage.")
    lines += [f"  #{g['id']} {g['text']}" for g in goals]
    lines.append("Open objectives:" if open_obj else "Open objectives: none.")
    for o in open_obj:
        extra = f" (incentive: {o['incentive']})" if o["incentive"] else ""
        lines.append(f"  #{o['id']} [goal #{o['goal_id'] or '-'}] {o['text']}{extra}, set {_ago(o['created'])}")
    closed = db.recent_closed_objectives(user_id)
    if closed:
        lines.append("Closed in the last 7 days:")
        for o in closed:
            detail = ", ".join(x for x in (o["barrier"], o["note"]) if x)
            lines.append(f"  #{o['id']} {o['status']}: {o['text']}" + (f" ({detail})" if detail else ""))
    profile = db.get_profile(user_id)
    told = {
        "why they came": profile.get("why"),
        "what they have tried": profile.get("tried"),
        "main obstacle": profile.get("obstacle"),
        "strengths": profile.get("strength"),
        "daily rhythm": profile.get("rhythm"),
        "mood lately": profile.get("mood"),
    }
    told = {label: answer for label, answer in told.items() if answer}
    if told:
        lines.append("What they told you at intake (their words; use it, don't re-ask):")
        lines += [f"  {label}: {answer}" for label, answer in told.items()]
        if profile.get("mood_heavy"):
            lines.append("  (they described a heavy mood: stay gentle, and encourage professional support if it persists)")
    tools = db.toolbox(user_id, 8)
    if tools:
        lines.append("Toolbox (what has worked for them):")
        lines += [f"  - {t['text']}" for t in tools]
    return "\n".join(lines)
