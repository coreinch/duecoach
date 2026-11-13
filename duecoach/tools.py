"""Tools the coach can call (OpenAI function-calling format) and the coaching-state summary shown to the model."""

import json
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import agenda, db
from .config import CHECKIN_INTERVAL_MINUTES

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
        "set_reminder",
        "Send the user a message later: a timer, a nudge, or a reminder. Call it once per reminder (one call per time of day). "
        "Give EITHER `at` (a clock time in the user's local time, best for anything tied to the time of day) OR `minutes` "
        "(a short timer). Set daily=true for something that repeats every day, like medication. The time must be one the user gave: if they "
        "named only a day, ask for the time first, and never reuse the time of another appointment.",
        {
            "at": {
                "type": "string",
                "description": "Local date and time as 'YYYY-MM-DD HH:MM' (24h). Use the 'Now' line to work out the date; "
                "for a daily reminder give its next occurrence.",
            },
            "minutes": {"type": "number", "description": "Alternative to `at`: minutes from now (1 to 43200)."},
            "message": {
                "type": "string",
                "description": "Short message to send, in the user's language, informal singular, as in the chat. Only what the user said "
                "they want to be reminded of: add no details of your own (how, with what, before or after what).",
            },
            "daily": {"type": "boolean", "description": "Repeat every day at the same local time."},
        },
        ["message"],
    ),
    _fn(
        "cancel_reminder",
        "Cancel a pending reminder (by the id in the reminders list) when it is wrong or no longer wanted.",
        {"reminder_id": {"type": "integer"}},
        ["reminder_id"],
    ),
    _fn(
        "add_event",
        "Save an appointment, call or plan that has a day AND a time of day, so you always know whether it is still ahead. Call it when the "
        "user tells you one. The time must be one they gave: if they named only a day, ask for the time first, and never reuse the time of "
        "another event.",
        {
            "title": {"type": "string", "description": "What it is, short, in the user's language (e.g. 'Dentist')."},
            "at": {"type": "string", "description": "Local date and time as 'YYYY-MM-DD HH:MM' (24h), worked out from the 'Now' line."},
        },
        ["title", "at"],
    ),
    _fn(
        "reschedule_event",
        "Move a saved event to the new day and time the user gave (by the id in the events list). Ask for the time if they gave only a day.",
        {
            "event_id": {"type": "integer", "description": "The #id shown in the events list."},
            "at": {"type": "string", "description": "New local date and time as 'YYYY-MM-DD HH:MM' (24h)."},
        },
        ["event_id", "at"],
    ),
    _fn(
        "cancel_event",
        "Cancel a saved event when the user says it is canceled or off (by the id in the events list). Also use it to undo an event saved "
        "with a wrong time.",
        {"event_id": {"type": "integer", "description": "The #id shown in the events list."}},
        ["event_id"],
    ),
    _fn(
        "snooze_checkins",
        "Pause your own proactive check-ins because the user is busy, in a meeting, driving, sleeping, or wants space. 0 resumes.",
        {"minutes": {"type": "number", "description": "How long to stay quiet, 0 to 10080 (7 days)."}},
        ["minutes"],
    ),
    _fn(
        "set_timezone",
        "Save or correct the user's timezone: when they tell you their city or country, or say the saved one is wrong. Work out the IANA "
        "name yourself from the place (for a country with several zones, ask which city). If they only give the time it is for them, "
        "work it out from the 'Now' line in the record, or ask for their city. Needed before check-ins can be turned on.",
        {"name": {"type": "string", "description": "IANA timezone name such as Europe/Athens or America/New_York."}},
        ["name"],
    ),
    _fn(
        "skip_timezone",
        "Record that the user does not want to give their timezone (they said skip, no, or declined). Then never ask again unless they bring it up.",
        {},
        [],
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
        "Save a small concrete step toward a goal that the user just agreed to do (today or this week), with when/where in the text if they said so. Call it in the same turn they agree. Max 3 open at once.",
        {
            "text": {
                "type": "string",
                "description": 'The objective including when/where, in the user\'s language. Use a weekday or clock time, never "tomorrow" or "today": it is read again later.',
            },
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
        "park_idea",
        "Set aside, on your own initiative, something the user drifted onto that is NOT what they are doing right now (an errand, a "
        "new idea, a tangent) so they can stay on track and it is not lost; you bring it back later at a calm moment. Call it in the "
        "same turn they drift, without asking. Not for their main topic, anything with a day and time (add_event / set_reminder), "
        "feelings or anything about safety.",
        {"text": {"type": "string", "description": "The idea in a few words, in the user's language, in their terms: never add to it."}},
        ["text"],
    ),
    _fn(
        "resolve_parked",
        "Close a parked idea once it has been dealt with (by the id in the parked list): moved = it became a to-do or an event, "
        "done = they did it, dropped = they no longer want it.",
        {
            "parked_id": {"type": "integer", "description": "The #id shown in the parked list."},
            "outcome": {"type": "string", "enum": ["moved", "done", "dropped"]},
        },
        ["parked_id", "outcome"],
    ),
    _fn(
        "log_activity",
        "Note something the user told you they did, or are doing right now (finished the report, went for a run, started cleaning the "
        "kitchen, had the awkward call), so you can build on it later and suggest related next steps. Call it in the same turn they say "
        "it, only for what they actually said, in their words: never guess, infer or embellish. Not for plans (use add_task), "
        "appointments (add_event) or how a weekly objective went (close_objective).",
        {
            "text": {
                "type": "string",
                "description": "What they did or are doing, one short line, in the user's language, past or present tense.",
            },
            "status": {"type": "string", "enum": ["done", "doing"], "description": "done = finished; doing = under way now."},
        },
        ["text", "status"],
    ),
    _fn(
        "forget_activity",
        "Delete a logged activity that is wrong or that the user asks you to forget (by the id in the activity list).",
        {"activity_id": {"type": "integer", "description": "The #id shown in the activity list."}},
        ["activity_id"],
    ),
    _fn(
        "add_task",
        "Put something on the user's to-do list: a thing they said they want or need to do. It may have a due DAY (a deadline such as "
        "'by Friday') but no time of day; with a time of day use add_event or set_reminder instead. Call it in the same turn they mention "
        "it ('I need to email the landlord', 'I keep meaning to renew my passport'), so it is not forgotten. Not for coaching goals or "
        "weekly steps (use add_goal / add_objective) and not for facts about them.",
        {
            "text": {"type": "string", "description": "The thing to do, short, starting with a verb, in the user's language."},
            "due": {
                "type": "string",
                "description": "Only if they gave a deadline day: 'YYYY-MM-DD' in their local time (work it out from the 'Now' line). "
                "Never invent one.",
            },
            "important": {
                "type": "boolean",
                "description": "True only if they said it matters most, or it is a real deadline with consequences.",
            },
        },
        ["text"],
    ),
    _fn(
        "complete_task",
        "Tick a to-do off when the user says they did it (by the id in the to-do list).",
        {"task_id": {"type": "integer", "description": "The #id shown in the to-do list."}},
        ["task_id"],
    ),
    _fn(
        "drop_task",
        "Remove a to-do the user no longer wants or needs to do, or one saved by mistake (by the id in the to-do list).",
        {"task_id": {"type": "integer", "description": "The #id shown in the to-do list."}},
        ["task_id"],
    ),
    _fn(
        "update_task",
        "Change an open to-do (by the id in the to-do list): reword it, for example to a smaller first step, move or clear its due day, "
        "or change whether it is important. Give only what changes.",
        {
            "task_id": {"type": "integer"},
            "text": {"type": "string", "description": "The new wording."},
            "due": {"type": "string", "description": "New due day 'YYYY-MM-DD' (local), or an empty string to remove the due day."},
            "important": {"type": "boolean"},
            "project_id": {"type": "integer", "description": "Move it into this project (0 takes it out of its project)."},
        },
        ["task_id"],
    ),
    _fn(
        "break_down_task",
        "Split an open to-do into smaller steps, saved as steps under it (by the id in the to-do list). Use it whenever a to-do is big, "
        "vague or has several parts, or the user says they are stuck on it or overwhelmed by it. Give 2 to 7 steps in the order to do "
        "them: each one concrete and literal (the action itself, a single sitting of about 5 to 30 minutes at most), the FIRST one tiny "
        "(about 5 minutes) so starting is easy. The parent stays as the umbrella; ticking off the steps completes it.",
        {
            "task_id": {"type": "integer", "description": "The #id of the to-do to split."},
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The steps in order, each starting with a verb, in the user's language, in their terms.",
            },
        },
        ["task_id", "steps"],
    ),
    _fn(
        "add_project",
        "Start a project: something bigger than one to-do that takes several steps over days or weeks (move flat, apply for a job, "
        "finish the thesis, plan a trip). Call it when they describe such a thing they actually want to do. Give its first 2 to 6 steps "
        "right away (they become to-dos in the project, the first one tiny); a project must never be left without a next step. Not for "
        "a single to-do and not for coaching goals (add_goal).",
        {
            "title": {"type": "string", "description": "What the project is, short, in the user's language and words."},
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The first steps in order, concrete, each starting with a verb.",
            },
        },
        ["title", "steps"],
    ),
    _fn(
        "close_project",
        "Close a project (by the id in the record): done = it is finished, dropped = they no longer want it (its open to-dos go too).",
        {"project_id": {"type": "integer"}, "outcome": {"type": "string", "enum": ["done", "dropped"]}},
        ["project_id", "outcome"],
    ),
    _fn(
        "add_note",
        "Keep a note the user asked you to write down ('note that the wifi password is...', 'remember the plumber's number'). Only on "
        "request or when they clearly want something kept, in their words; never for things you merely learned about them (that is "
        "your own memory) and not for to-dos, appointments or reminders.",
        {"text": {"type": "string", "description": "The note, in the user's own words and language, as complete as they gave it."}},
        ["text"],
    ),
    _fn(
        "delete_note",
        "Delete one of the user's notes when they ask (by the id in their notes list).",
        {"note_id": {"type": "integer", "description": "The #id shown in the notes list."}},
        ["note_id"],
    ),
    _fn(
        "save_to_toolbox",
        "Save a coping strategy that actually worked for the user (or a mantra they chose) so you can bring it back later. Only that: "
        "never facts about them, their projects, names, or anything you were told rather than something that helped.",
        {
            "text": {
                "type": "string",
                "description": 'The strategy in one short line, in the user\'s language, in the second person or neutral ("Setting a 20-minute timer keeps you from drifting"), never "me" or "my".',
            }
        },
        ["text"],
    ),
    _fn(
        "remove_from_toolbox",
        "Delete a toolbox item that is wrong, a duplicate, or not a strategy, when the user asks or you notice it. Use the id from the toolbox list.",
        {"tool_id": {"type": "integer", "description": "The #id shown in the toolbox list."}},
        ["tool_id"],
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


def _local_zone(uid: int) -> ZoneInfo:
    try:
        return ZoneInfo(db.get_user(uid)["tz"])
    except Exception:
        return ZoneInfo("UTC")


_REMINDER_WORDS = re.compile(
    r"remind|timer|alarm|ping|notify|nudge|wake|check (in )?(on|with|back)|\d|υπενθ|θύμισ|θυμισ|χρονομετρ|ξύπν|ξυπν|ειδοποι",
    re.IGNORECASE,
)


def _asked_for_reminder(uid: int) -> bool:
    """False when the user's latest message is only a question ("are you working now?") that says nothing about a reminder.

    The prompt forbids setting one then, but the model still does it; this is the check that does not depend on the model.
    """
    latest = next((m["content"] for m in reversed(db.recent_messages(uid, 4)) if m["role"] == "user"), "")
    asking = latest.rstrip().endswith(("?", ";", "\u037e"))
    return not (asking and len(latest.split()) > 3 and not _REMINDER_WORDS.search(latest))


def _set_reminder(uid: int, a: dict) -> str:
    if not _asked_for_reminder(uid):
        return "error: the user's latest message did not ask for a reminder or timer. Reply in plain text and do not set one"
    message, daily = _text(a, "message"), bool(a.get("daily"))
    if a.get("at"):
        zone = _local_zone(uid)
        try:
            when = datetime.strptime(str(a["at"]).strip().replace("T", " ")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=zone)
        except ValueError:
            return "error: `at` must look like 'YYYY-MM-DD HH:MM' (24h, the user's local time)"
        if when.timestamp() <= time.time():
            if not daily:
                return "error: that time has already passed; check the 'Now' line and use a future date and time"
            while when.timestamp() <= time.time():  # a daily reminder given today's already-passed time starts tomorrow
                when += timedelta(days=1)
        if when.timestamp() - time.time() > MAX_REMINDER_MINUTES * 60:
            return "error: too far ahead; the limit is 30 days"
        due = when.timestamp()
    elif a.get("minutes") is not None:
        minutes = float(a["minutes"])
        if not 0 < minutes <= MAX_REMINDER_MINUTES:
            return f"error: minutes must be between 0 and {MAX_REMINDER_MINUTES}"
        due = time.time() + minutes * 60
    else:
        return "error: give `at` (local 'YYYY-MM-DD HH:MM') or `minutes`"
    for r in db.pending_reminders(uid) + list(db.recent_reminders(uid, 1)):
        if abs(r["due"] - due) < 120 and _norm(r["text"]) == _norm(message):
            shown = datetime.fromtimestamp(r["due"], _local_zone(uid)).strftime("%a %H:%M")
            return f"ok: reminder #{r['id']} for that time was already set (for {shown} local time); don't set it again"
    rid = db.add_reminder(uid, due, message, daily, clock=bool(a.get("at")))
    if rid is None:
        return f"error: already {db.MAX_PENDING_REMINDERS} reminders waiting; cancel one first"
    shown = datetime.fromtimestamp(due, _local_zone(uid)).strftime("%a %H:%M")
    return f"ok: reminder #{rid} set for {shown} local time" + (", repeating every day" if daily else "")


def _local_time(uid: int, at: object) -> float | str:
    """The timestamp of a local 'YYYY-MM-DD HH:MM', or an error text for the model."""
    try:
        when = datetime.strptime(str(at).strip().replace("T", " ")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=_local_zone(uid))
    except ValueError:
        return "error: `at` must look like 'YYYY-MM-DD HH:MM' (24h, the user's local time)"
    if when.timestamp() <= time.time():
        return "error: that time has already passed; check the 'Now' line and use a future date and time"
    if when.timestamp() - time.time() > 365 * 86400:
        return "error: too far ahead; the limit is one year"
    return when.timestamp()


def _shown(uid: int, ts: float) -> str:
    return datetime.fromtimestamp(ts, _local_zone(uid)).strftime("%a %Y-%m-%d %H:%M")


def _add_event(uid: int, a: dict) -> str:
    title = _text(a, "title")
    start = _local_time(uid, a.get("at"))
    if isinstance(start, str):
        return start
    for e in db.events_for_state(uid, time.time()):
        if e["status"] == "upcoming" and abs(e["start"] - start) < 120 and _norm(e["title"]) == _norm(title):
            return f"ok: event #{e['id']} was already saved for {_shown(uid, e['start'])} local time; don't save it again"
    eid = db.add_event(uid, title, start)
    if eid is None:
        return f"error: already {db.MAX_UPCOMING_EVENTS} events waiting; cancel one first"
    return f"ok: event #{eid} saved for {_shown(uid, start)} local time"


def _reschedule_event(uid: int, a: dict) -> str:
    start = _local_time(uid, a.get("at"))
    if isinstance(start, str):
        return start
    if not db.move_event(uid, int(a["event_id"]), start):
        return "error: no such upcoming event"
    return f"ok: event #{int(a['event_id'])} moved to {_shown(uid, start)} local time"


def _cancel_event(uid: int, a: dict) -> str:
    return "ok: event cancelled" if db.cancel_event(uid, int(a["event_id"])) else "error: no such upcoming event"


def _cancel_reminder(uid: int, a: dict) -> str:
    return "ok: reminder cancelled" if db.cancel_reminder(uid, int(a["reminder_id"])) else "error: no such pending reminder"


def _set_timezone(uid: int, a: dict) -> str:
    name = _text(a, "name")
    try:
        zone = ZoneInfo(name)
    except Exception:
        return "error: unknown timezone; use an IANA name like Europe/Athens (Area/City)"
    switched_on = db.confirm_timezone(uid, name)
    db.set_field(uid, "tz_state", "done")
    local = datetime.now(zone).strftime("%H:%M")
    result = f"ok: timezone set to {name}; it is {local} there now: tell the user that time so they can correct you"
    if switched_on:
        result += (
            f"; check-ins were switched on automatically (about every {CHECKIN_INTERVAL_MINUTES} minutes of quiet): mention it briefly"
        )
    return result


def _skip_timezone(uid: int, a: dict) -> str:
    db.set_field(uid, "tz_state", "skipped")
    return "ok: noted that they would rather not say; don't ask again unless they bring it up"


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


def _words(text: str) -> set[str]:
    return set(re.findall(r"[\w-]+", text.casefold()))


def _similar(a: str, b: str) -> bool:
    """The same idea in other words ("shrink the task, use a 10-minute timer" saved twice): most of the words are shared."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


def _save_to_toolbox(uid: int, a: dict) -> str:
    text = _text(a)
    for t in db.toolbox(uid):
        if _norm(t["text"]) == _norm(text) or _similar(t["text"], text):
            return f"ok: already in the toolbox as #{t['id']}; not saved again"
    return "ok: saved to toolbox" if db.add_tool(uid, text) else "error: toolbox is full"


def _remove_from_toolbox(uid: int, a: dict) -> str:
    return "ok: removed from the toolbox" if db.remove_tool(uid, int(a["tool_id"])) else "error: no such toolbox item"


def _park_idea(uid: int, a: dict) -> str:
    text = _text(a)
    for p in db.open_parked(uid):
        if _norm(p["text"]) == _norm(text) or _similar(p["text"], text):
            return f"ok: already parked as #{p['id']}; not saved again"
    pid = db.park_idea(uid, text)
    return f"ok: parked as #{pid}" if pid else f"error: already {db.MAX_PARKED} parked ideas; resolve some first"


def _resolve_parked(uid: int, a: dict) -> str:
    outcome = str(a.get("outcome") or "")
    if outcome not in ("moved", "done", "dropped"):
        return "error: outcome must be moved, done or dropped"
    return "ok: parked idea closed" if db.resolve_parked(uid, int(a["parked_id"]), outcome) else "error: no such parked idea"


def _log_activity(uid: int, a: dict) -> str:
    text, status = _text(a), str(a.get("status") or "done")
    if status not in ("done", "doing"):
        return "error: status must be done or doing"
    for r in db.recent_activities(uid, 1, 5):
        if _norm(r["text"]) == _norm(text) or _similar(r["text"], text):
            return f"ok: already noted as #{r['id']}; not saved again"
    return f"ok: noted as #{db.add_activity(uid, text, status)}"


def _forget_activity(uid: int, a: dict) -> str:
    return "ok: forgotten" if db.forget_activity(uid, int(a["activity_id"])) else "error: no such activity"


def _due_day(uid: int, value: object) -> str | None:
    """A model-given due day as 'YYYY-MM-DD' ('' for none); None when it is not a valid day or is already past."""
    text = str(value or "").strip()[:10]
    if not text:
        return ""
    try:
        day = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return text if day >= agenda.today_for(uid) else None


_BAD_DUE = "error: `due` must be a day from today on, as 'YYYY-MM-DD' (check the 'Now' line); leave it out if they gave no deadline"


def _add_task(uid: int, a: dict) -> str:
    text = _text(a)
    due = _due_day(uid, a.get("due"))
    if due is None:
        return _BAD_DUE
    for t in db.open_tasks(uid):
        if _norm(t["text"]) == _norm(text) or _similar(t["text"], text):
            if due and t["due"] != due:
                db.update_task(uid, t["id"], due=due)
                return f"ok: already on the to-do list as #{t['id']}; its due day is now {due}"
            return f"ok: already on the to-do list as #{t['id']}; not saved again"
    project_id = int(a.get("project_id") or 0)
    if project_id and project_id not in {p["id"] for p in db.active_projects(uid)}:
        return "error: no such active project"
    tid = db.add_task(uid, text, due, 1 if a.get("important") else 0, project_id)
    if tid is None:
        return f"error: already {db.MAX_OPEN_TASKS} open to-dos; finish or drop some first"
    return f"ok: to-do #{tid} saved" + (f", due {due}" if due else "")


def _complete_task(uid: int, a: dict) -> str:
    return "ok: ticked off" if db.close_task(uid, int(a["task_id"]), "done") else "error: no such open to-do"


def _drop_task(uid: int, a: dict) -> str:
    return "ok: removed from the to-do list" if db.close_task(uid, int(a["task_id"]), "dropped") else "error: no such open to-do"


def _update_task(uid: int, a: dict) -> str:
    due = _due_day(uid, a["due"]) if "due" in a else None
    if "due" in a and due is None:
        return _BAD_DUE
    text = _text(a) if a.get("text") else None
    important = None if a.get("important") is None else int(bool(a["important"]))
    project = None if a.get("project_id") is None else int(a["project_id"])
    if project and project not in {p["id"] for p in db.active_projects(uid)}:
        return "error: no such active project"
    if text is None and due is None and important is None and project is None:
        return "error: nothing to change; give text, due, important or project_id"
    return "ok: updated" if db.update_task(uid, int(a["task_id"]), text, due, important, project) else "error: no such open to-do"


def _steps(a: dict, low: int, high: int) -> list[str] | str:
    """The cleaned list of steps from a tool call, or an error text."""
    raw = a.get("steps")
    if not isinstance(raw, list):
        return "error: `steps` must be a list of short texts"
    steps: list[str] = []
    for item in raw:
        text = " ".join(str(item).split())[:200].strip()
        if text and not any(_norm(text) == _norm(x) for x in steps):
            steps.append(text)
    if not low <= len(steps) <= high:
        return f"error: give {low} to {high} distinct steps"
    return steps


def _break_down_task(uid: int, a: dict) -> str:
    steps = _steps(a, 2, 7)
    if isinstance(steps, str):
        return steps
    parent = db.get_open_task(uid, int(a["task_id"]))
    if parent is None:
        return "error: no such open to-do"
    if parent["parent_id"]:
        return "error: that is already a step; split the to-do it belongs to, or reword the step"
    have = [t for t in db.open_tasks(uid) if t["parent_id"] == parent["id"]]
    if have:
        return f"ok: it already has {len(have)} steps (see the list); don't split it again, change them with update_task or add_task"
    saved: list[int] = []
    for text in steps:
        tid = db.add_task(uid, text, parent["due"] or "", 0, 0, parent["id"])
        if tid is None:
            return f"error: the list is full ({db.MAX_OPEN_TASKS}); saved {len(saved)} steps" + (f" (#{saved[0]}...)" if saved else "")
        saved.append(tid)
    return f"ok: {len(saved)} steps saved under #{parent['id']} as #{saved[0]} to #{saved[-1]}; the first one is the one to start with"


def _add_project(uid: int, a: dict) -> str:
    title = _text(a, "title")
    steps = _steps(a, 1, 6)
    if isinstance(steps, str):
        return steps
    for p in db.active_projects(uid):
        if _norm(p["title"]) == _norm(title) or _similar(p["title"], title):
            return f"ok: already a project as #{p['id']}; add its steps with add_task (project_id), don't start it again"
    pid = db.add_project(uid, title)
    if pid is None:
        return f"error: already {db.MAX_PROJECTS} active projects; close one first"
    saved = [db.add_task(uid, text, "", 0, pid) for text in steps]
    made = [t for t in saved if t]
    return (
        f"ok: project #{pid} saved with {len(made)} steps (to-dos #{made[0]} on)"
        if made
        else f"ok: project #{pid} saved, but the to-do list is full"
    )


def _close_project(uid: int, a: dict) -> str:
    outcome = str(a.get("outcome") or "")
    if outcome not in ("done", "dropped"):
        return "error: outcome must be done or dropped"
    return "ok: project closed" if db.close_project(uid, int(a["project_id"]), outcome) else "error: no such active project"


def _add_note(uid: int, a: dict) -> str:
    text = _text(a)
    for m in db.memos(uid, 5):
        if _norm(m["text"]) == _norm(text):
            return f"ok: already noted as #{m['id']}; not saved again"
    mid = db.add_memo(uid, text)
    return f"ok: note #{mid} saved" if mid else f"error: the notebook is full ({db.MAX_MEMOS}); ask which notes to delete"


def _delete_note(uid: int, a: dict) -> str:
    return "ok: note deleted" if db.delete_memo(uid, int(a["note_id"])) else "error: no such note"


_DISPATCH = {
    "park_idea": _park_idea,
    "resolve_parked": _resolve_parked,
    "log_activity": _log_activity,
    "forget_activity": _forget_activity,
    "add_task": _add_task,
    "complete_task": _complete_task,
    "drop_task": _drop_task,
    "update_task": _update_task,
    "break_down_task": _break_down_task,
    "add_project": _add_project,
    "close_project": _close_project,
    "add_note": _add_note,
    "delete_note": _delete_note,
    "set_timezone": _set_timezone,
    "skip_timezone": _skip_timezone,
    "set_checkins": _set_checkins,
    "snooze_checkins": _snooze_checkins,
    "set_reminder": _set_reminder,
    "cancel_reminder": _cancel_reminder,
    "add_event": _add_event,
    "reschedule_event": _reschedule_event,
    "cancel_event": _cancel_event,
    "add_goal": _add_goal,
    "retire_goal": _retire_goal,
    "add_objective": _add_objective,
    "close_objective": _close_objective,
    "save_to_toolbox": _save_to_toolbox,
    "remove_from_toolbox": _remove_from_toolbox,
}


def _ago(ts: float, zone: ZoneInfo) -> str:
    days = int((time.time() - ts) // 86400)
    return f"{datetime.fromtimestamp(ts, zone).strftime('%a %Y-%m-%d')}, " + ("today" if days < 1 else f"{days}d ago")


def _span(seconds: float) -> str:
    minutes = max(int(seconds // 60), 0)
    return f"{minutes} min" if minutes < 90 else f"{minutes // 60}h {minutes % 60:02d}min"


def _event_when(event, zone: ZoneInfo) -> str:
    """'Fri 2026-10-16 11:20 — starts in 2h 15min: NOT yet, so do not ask how it went'."""
    when = datetime.fromtimestamp(event["start"], zone).strftime("%a %Y-%m-%d %H:%M")
    if event["status"] == "canceled":
        return f"{when} — CANCELED (it is not happening; they may reschedule it)"
    ahead = event["start"] - time.time()
    if ahead > 0:
        return f"{when} — starts in {_span(ahead)}: NOT yet, so never say it passed or ask how it went"
    return f"{when} — started {_span(-ahead)} ago: under way or over, so you may ask how it went"


def coaching_state(user_id: int) -> str:
    """The user's coaching record as text for the system prompt (ids let the model refer to items in tool calls)."""
    user = db.get_user(user_id)
    weeks = int((time.time() - (user["created_at"] or time.time())) // (7 * 86400)) + 1
    goals, open_obj = db.active_goals(user_id), db.open_objectives(user_id)
    zone = _local_zone(user_id)
    local = datetime.now(zone)
    if user["tz_set"]:
        tz_note = "set from what the user told us; if they say it is wrong, correct it with set_timezone"
    elif user["tz_state"] == "skipped":
        tz_note = "NOT set: they declined to give it; don't ask again unless they bring it up"
    elif user["tz_state"] == "asked":
        tz_note = (
            "NOT set yet: the welcome message asked for their city, and they may answer in any message (call set_timezone when they do). "
            "If they ignore it, ask at most once more, at a calm moment; if they decline, call skip_timezone"
        )
    else:
        tz_note = "NOT set yet: ask for their city once when it fits, then save it with set_timezone"
    checkins = (
        f"on, about every {user['interval_min']} min between {user['morning_hour']}:00 and {user['evening_hour']}:59"
        if user["interval_min"] and user["morning_hour"] >= 0 and user["evening_hour"] >= 0
        else "off (offer them if it fits)"
    )
    lines = [
        f"Now: {local.strftime('%A %Y-%m-%d %H:%M')} local time ({user['tz']}, {tz_note}). Check-ins: {checkins}.",
        f"Coaching record (week {weeks} of working together):",
    ]
    lines.append("Goals:" if goals else "Goals: none yet (get to know them first; offer a goal when they have named a struggle or a wish).")
    lines += [f"  #{g['id']} {g['text']}" for g in goals]
    lines.append("Open objectives:" if open_obj else "Open objectives: none.")
    for o in open_obj:
        extra = f" (incentive: {o['incentive']})" if o["incentive"] else ""
        lines.append(f"  #{o['id']} [goal #{o['goal_id'] or '-'}] {o['text']}{extra}, set {_ago(o['created'], zone)}")
    closed = db.recent_closed_objectives(user_id)
    if closed:
        lines.append("Closed in the last 7 days:")
        for o in closed:
            detail = ", ".join(x for x in (o["barrier"], o["note"]) if x)
            lines.append(f"  #{o['id']} {o['status']}: {o['text']}" + (f" ({detail})" if detail else ""))
    fresh = db.unannounced_auto(user_id)
    if any(fresh.values()):
        lines.append(
            "Filed for them by yourself from what they told you, not yet mentioned (say so in ONE short sentence at the start of this "
            "reply, naming at most three of the things in their words (with how many more, if so, and that /tasks shows all of it), and that "
            "they can tell you if any of it is wrong; then carry on):"
        )
        lines += [f"  to-do #{t['id']} {t['text']}" + (f", due {t['due']}" if t["due"] else "") for t in fresh["tasks"]]
        lines += [f"  project #{p['id']} {p['title']}" for p in fresh["projects"]]
        lines += [f"  appointment #{e['id']} {e['title']}, {_shown(user_id, e['start'])}" for e in fresh["events"]]
    today = local.date()
    sections = agenda.task_tree(user_id)
    has_todo = any(rows for _, rows in sections)
    lines.append(
        "To-do list (what they said they want or need to do, grouped by project; dated ones first; 'important' ones matter most; "
        "an indented step belongs to the to-do above it; bring them back gently when it fits, and never let a due day slip past unmentioned):"
        if has_todo or db.active_projects(user_id)
        else "To-do list: empty. Projects: none."
    )
    for project, rows in sections:
        if project is not None:
            lines.append(
                f"  Project #{project['id']} {project['title']}"
                + ("" if rows else ": NO OPEN NEXT STEP, so offer one small concrete step at a calm moment and save it once they agree")
            )
        elif rows and db.active_projects(user_id):
            lines.append("  Not in a project:")
        for t, step in rows:
            due = agenda.due_label("en", t["due"] or "", today)
            flags = (f", due {due}" if due else "") + (", important" if t["priority"] else "")
            lines.append(f"{'      step' if step else '    '} #{t['id']} {t['text']}{flags}; on the list since {_ago(t['created'], zone)}")
    finished = db.recent_finished_tasks(user_id)
    if finished:
        lines.append("Ticked off in the last 3 days (worth a word of recognition, not a new question):")
        lines += [f"  {t['text']}" for t in finished]
    parked = db.open_parked(user_id)
    if parked:
        lines.append(
            "Parked ideas (set aside mid-conversation so they could stay on track; oldest first; bring one back only when nothing is "
            "in progress, as a short offer they can take up, put on the list or let go):"
        )
        lines += [f"  #{p['id']} {p['text']}, parked {_ago(p['created'], zone)}" for p in parked]
    else:
        lines.append("Parked ideas: none.")
    acts = db.recent_activities(user_id)
    if acts:
        lines.append(
            "Activity they told you about (their words, last 2 weeks, oldest first; build on it and suggest related next steps; "
            "'doing' entries from earlier than a few hours ago are probably over, so ask rather than assume):"
        )
        lines += [f"  #{a['id']} [{a['status']}] {a['text']}, {_ago(a['created'], zone)}" for a in acts]
    else:
        lines.append("Activity they told you about: nothing noted yet.")
    reminders = db.pending_reminders(user_id)
    lines.append("Reminders waiting (already set, don't set them again):" if reminders else "Reminders waiting: none.")
    for r in reminders:
        when = datetime.fromtimestamp(r["due"], zone).strftime("%a %Y-%m-%d %H:%M")
        lines.append(f"  #{r['id']} {when}{' daily' if r['repeat_daily'] else ''}: {r['text']}")
    fired = db.recent_reminders(user_id)
    if fired:
        lines.append("Reminders already delivered in the last 24h (done: don't set them again):")
        lines += [f"  {datetime.fromtimestamp(r['due'], zone).strftime('%a %H:%M')}: {r['text']}" for r in fired]
    events = db.events_for_state(user_id, time.time())
    lines.append(
        "Events with a time (appointments, calls, plans; this list is the truth about whether something is still ahead or over):"
        if events
        else "Events with a time: none saved."
    )
    for e in events:
        lines.append(f"  #{e['id']} {e['title']}: {_event_when(e, zone)}")
    notes = db.memos(user_id, 8)
    if notes:
        lines.append(
            "Their own notes (they asked you to keep them; newest first, the 8 latest; tell them what is in one when it is relevant "
            "or asked for, never recite them unprompted):"
        )
        lines += [f"  #{m['id']} {m['text'][:200]}" for m in notes]
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
        lines.append("What they told you in the early interview (their words; use it, don't re-ask):")
        lines += [f"  {label}: {answer}" for label, answer in told.items()]
        if profile.get("mood_heavy"):
            lines.append("  (they described a heavy mood: stay gentle, and encourage professional support if it persists)")
    tools = db.toolbox(user_id, 8)
    if tools:
        lines.append("Toolbox (what has worked for them):")
        lines += [f"  #{t['id']} {t['text']}" for t in tools]
    return "\n".join(lines)
