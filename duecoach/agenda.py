"""The planner side of the bot: what is on a given day (events, reminders, dated and overdue to-dos), written by code, not the model.

Used by the `/today` and `/calendar` commands (which cost no model call) and by the check-ins, which are handed the same facts so the
coach talks about what is really on the user's day instead of asking what is on their plate.
"""

import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, strings

_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3,
    "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
    "δευ": 0, "δευτέρα": 0, "τρί": 1, "τρι": 1, "τρίτη": 1, "τετ": 2, "τετάρτη": 2, "πέμ": 3, "πεμ": 3, "πέμπτη": 3,
    "παρ": 4, "παρασκευή": 4, "σάβ": 5, "σαβ": 5, "σάββατο": 5, "κυρ": 6, "κυριακή": 6,
}  # fmt: skip
_TODAY = {"today", "σήμερα", "σημερα"}
_TOMORROW = {"tomorrow", "tmrw", "αύριο", "αυριο"}
_DAY_MONTH = re.compile(r"^(\d{1,2})[/.](\d{1,2})$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def zone_of(uid: int) -> ZoneInfo:
    try:
        return ZoneInfo(db.get_user(uid)["tz"])
    except Exception:
        return ZoneInfo("UTC")


def today_for(uid: int) -> date:
    return datetime.fromtimestamp(time.time(), zone_of(uid)).date()


def parse_day(word: str, today: date) -> date | None:
    """'today', 'tomorrow', a weekday name (the next one, never today), 'YYYY-MM-DD' or 'D/M' (the next such day); None if not a day."""
    w = word.strip().lower().rstrip(",")
    if w in _TODAY:
        return today
    if w in _TOMORROW:
        return today + timedelta(days=1)
    if w in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[w] - today.weekday() - 1) % 7 + 1)
    try:
        return date.fromisoformat(w)
    except ValueError:
        pass
    m = _DAY_MONTH.match(w)
    if m:
        try:
            found = date(today.year, int(m[2]), int(m[1]))
            return found if found >= today else date(today.year + 1, int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def parse_time(word: str) -> tuple[int, int] | None:
    m = _TIME.match(word.strip())
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        return int(m[1]), int(m[2])
    return None


def day_bounds(day: date, zone: ZoneInfo) -> tuple[float, float]:
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    nxt = day + timedelta(days=1)
    return start.timestamp(), datetime(nxt.year, nxt.month, nxt.day, tzinfo=zone).timestamp()


def day_name(lang: str, day: date) -> str:
    return f"{strings.WEEKDAYS[lang][day.weekday()]} {day.day} {strings.MONTHS[lang][day.month - 1]}"


def due_label(lang: str, due: str, today: date) -> str:
    """'today', 'tomorrow', 'Fri 20 Nov' or 'overdue (Mon 9 Nov)'."""
    try:
        day = date.fromisoformat(due)
    except ValueError:
        return ""
    if day == today:
        return strings.t(lang, "DUE_TODAY")
    if day == today + timedelta(days=1):
        return strings.t(lang, "DUE_TOMORROW")
    if day < today:
        return strings.t(lang, "DUE_OVERDUE", day=day_name(lang, day))
    return day_name(lang, day)


def task_line(lang: str, t, today: date, step: bool = False) -> str:
    star = "★ " if t["priority"] else ""
    due = due_label(lang, t["due"] or "", today)
    return f"{'    ◦' if step else '•'} #{t['id']} {star}{t['text']}" + (f" — {due}" if due else "")


def task_tree(uid: int) -> list[tuple]:
    """The open to-dos grouped for display: one section per active project (even one with nothing open), then the rest.
    Each section is (project row or None, [(task row, is a step under another task), ...]) with steps right after their task."""
    tasks = db.open_tasks(uid)
    steps: dict[int, list] = {}
    for t in tasks:
        if t["parent_id"]:
            steps.setdefault(t["parent_id"], []).append(t)
    top = [t for t in tasks if not t["parent_id"]]

    def rows(items: list) -> list[tuple]:
        out = []
        for t in items:
            out.append((t, False))
            out += [(c, True) for c in steps.get(t["id"], [])]
        return out

    sections: list[tuple] = [(p, rows([t for t in top if t["project_id"] == p["id"]])) for p in db.active_projects(uid)]
    in_project = {p["id"] for p in db.active_projects(uid)}
    sections.append((None, rows([t for t in top if t["project_id"] not in in_project])))
    return sections


def projects_text(uid: int) -> str:
    """Each active project with its next step and how many to-dos are open, for `/projects`."""
    lang = db.get_user(uid)["lang"]
    lines = []
    for project, rows in task_tree(uid):
        if project is None:
            continue
        # the next step: the first step under the first to-do if it has any, else that to-do itself
        nxt = rows[1][0] if len(rows) > 1 and rows[1][1] else rows[0][0] if rows else None
        detail = strings.t(lang, "PROJECTS_NEXT", step=nxt["text"], n=len(rows)) if nxt else strings.t(lang, "PROJECTS_NO_STEP")
        lines.append(f"• {project['title']} — {detail}")
    if not lines:
        return strings.t(lang, "PROJECTS_EMPTY")
    return f"{strings.t(lang, 'PROJECTS_HEADER')}:\n" + "\n".join(lines)


def tasks_text(uid: int) -> str:
    """The whole to-do list for `/tasks`: by project, steps under their task."""
    lang, today = db.get_user(uid)["lang"], today_for(uid)
    blocks: list[str] = []
    for project, rows in task_tree(uid):
        lines = [task_line(lang, t, today, step) for t, step in rows]
        if project is None:
            if lines:
                head = strings.t(lang, "TASKS_OTHER") if blocks else None
                blocks.append((f"{head}:\n" if head else "") + "\n".join(lines))
        else:
            lines = lines or [strings.t(lang, "PROJECT_NO_STEP")]
            blocks.append(f"{strings.t(lang, 'PROJECT_HEADER', title=project['title'])}\n" + "\n".join(lines))
    if not blocks:
        return strings.t(lang, "TASKS_EMPTY")
    return f"{strings.t(lang, 'TASKS_HEADER')}:\n\n" + "\n\n".join(blocks)


def event_line(event, zone: ZoneInfo, with_day: bool = False, lang: str = "en") -> str:
    when = datetime.fromtimestamp(event["start"], zone)
    stamp = f"{day_name(lang, when.date())} {when:%H:%M}" if with_day else f"{when:%H:%M}"
    return f"• {stamp} {event['title']} (#{event['id']})"


def _split_tasks(uid: int, today: date) -> tuple[list, list, list, list]:
    """(overdue, due today, due later, no date) among the open to-dos."""
    overdue, now_, later, undated = [], [], [], []
    for t in db.open_tasks(uid):
        if not t["due"]:
            undated.append(t)
        elif t["due"] < today.isoformat():
            overdue.append(t)
        elif t["due"] == today.isoformat():
            now_.append(t)
        else:
            later.append(t)
    return overdue, now_, later, undated


def _section(lang: str, key: str, lines: list[str]) -> str:
    return f"{strings.t(lang, key)}:\n" + "\n".join(lines) if lines else ""


def today_text(uid: int) -> str:
    """What is on today, for the `/today` command."""
    lang, zone, today = db.get_user(uid)["lang"], zone_of(uid), today_for(uid)
    start, end = day_bounds(today, zone)
    now = time.time()
    events = db.events_between(uid, start, end)
    reminders = db.reminders_between(uid, max(start, now), end)
    overdue, due_today, _, undated = _split_tasks(uid, today)
    parts = [
        _section(lang, "AGENDA_EVENTS", [event_line(e, zone) for e in events]),
        _section(
            lang,
            "AGENDA_REMINDERS",
            [f"• {datetime.fromtimestamp(r['due'], zone):%H:%M} {r['text']}" for r in reminders],
        ),
        _section(lang, "AGENDA_OVERDUE", [task_line(lang, t, today) for t in overdue]),
        _section(lang, "AGENDA_DUE_TODAY", [task_line(lang, t, today) for t in due_today]),
        _section(lang, "AGENDA_NO_DATE", [task_line(lang, t, today) for t in undated[:5]]),
    ]
    body = "\n\n".join(p for p in parts if p)
    head = strings.t(lang, "AGENDA_HEADER", day=day_name(lang, today))
    return f"{head}\n\n{body}" if body else f"{head}\n\n{strings.t(lang, 'AGENDA_EMPTY')}"


def calendar_text(uid: int, days: int = 7) -> str:
    """The next `days` days, day by day, for the `/calendar` command: events, reminders and to-dos with a due date."""
    lang, zone, today = db.get_user(uid)["lang"], zone_of(uid), today_for(uid)
    overdue, _, _, _ = _split_tasks(uid, today)
    blocks = []
    if overdue:
        blocks.append(_section(lang, "AGENDA_OVERDUE", [task_line(lang, t, today) for t in overdue]))
    tasks = [t for t in db.open_tasks(uid) if t["due"]]
    for offset in range(days):
        day = today + timedelta(days=offset)
        start, end = day_bounds(day, zone)
        lines = [event_line(e, zone) for e in db.events_between(uid, start, end)]
        lines += [
            f"• {datetime.fromtimestamp(r['due'], zone):%H:%M} ⏰ {r['text']}"
            for r in db.reminders_between(uid, max(start, time.time()), end)
        ]
        lines += [task_line(lang, t, today) for t in tasks if t["due"] == day.isoformat()]
        if lines:
            blocks.append(f"{day_name(lang, day)}:\n" + "\n".join(lines))
    if not blocks:
        return strings.t(lang, "CALENDAR_EMPTY", days=days)
    return strings.t(lang, "CALENDAR_HEADER", days=days) + "\n\n" + "\n\n".join(blocks)


# --- what the check-ins are told ---

_USE = (
    " These are facts from their own calendar and to-do list: use them to coach, don't read them back as a list. Mention at most "
    "three items, lead with the one that matters most or comes first, and turn it into a concrete next step (when, where, a tiny first "
    "move). Never say an item is done or missed unless the lists say so."
)


def prompt_block(uid: int, kind: str) -> str:
    """Facts for a check-in instruction ('morning', 'evening' or 'pulse'), or '' when there is nothing on their calendar or list."""
    zone, today, now = zone_of(uid), today_for(uid), time.time()
    overdue, due_today, due_later, _ = _split_tasks(uid, today)
    start, end = day_bounds(today, zone)
    lines: list[str] = []

    def tasks(label: str, items: list) -> None:
        if items:
            lines.append(f"{label}: " + "; ".join(f"#{t['id']} {'(top priority) ' if t['priority'] else ''}{t['text']}" for t in items[:6]))

    stalled = [p["title"] for p, rows in task_tree(uid) if p is not None and not rows]
    if kind == "morning":
        events = db.events_between(uid, max(start, now), end)
        if events:
            lines.append(
                "Still ahead today: " + "; ".join(f"{datetime.fromtimestamp(e['start'], zone):%H:%M} {e['title']}" for e in events)
            )
        tasks("Due today", due_today)
        tasks("Overdue (carried over; offer to reschedule, shrink or drop, no guilt)", overdue)
        top = [t for t in db.open_tasks(uid) if not t["due"] and t["priority"]]
        tasks("Top-priority with no date", top)
    elif kind == "evening":
        tomorrow = today + timedelta(days=1)
        t_start, t_end = day_bounds(tomorrow, zone)
        events = db.events_between(uid, t_start, t_end)
        if events:
            lines.append("Tomorrow: " + "; ".join(f"{datetime.fromtimestamp(e['start'], zone):%H:%M} {e['title']}" for e in events))
        tasks("Due tomorrow", [t for t in due_later if t["due"] == tomorrow.isoformat()])
        tasks("Still open from today or earlier", overdue + due_today)
        finished = [t for t in db.recent_finished_tasks(uid, 1) if datetime.fromtimestamp(t["closed"], zone).date() == today]
        if finished:
            lines.append("Ticked off today (name it, it counts): " + "; ".join(t["text"] for t in finished[:5]))
    if stalled and kind != "pulse":
        lines.append(
            "Projects with no open next step: "
            + "; ".join(stalled)
            + " (offer ONE small concrete next step for one of them, about 10 minutes; they say yes and you save it next turn)"
        )
    if kind == "pulse":  # a mid-day pulse: only what is close
        events = db.events_between(uid, now, min(end, now + 3 * 3600))
        if events:
            lines.append(
                "Coming up in the next 3 hours: "
                + "; ".join(f"{datetime.fromtimestamp(e['start'], zone):%H:%M} {e['title']}" for e in events)
                + " (help them get ready or leave on time)"
            )
        tasks("Due today", due_today)
        tasks("Overdue", overdue[:3])
    if not lines:
        return ""
    return f" Their agenda ({lang_note(kind)}):\n" + "\n".join(lines) + _USE


def lang_note(kind: str) -> str:
    return {"morning": "for today", "evening": "for the rest of today and tomorrow", "pulse": "right now"}[kind]
