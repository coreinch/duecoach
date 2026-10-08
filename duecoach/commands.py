"""The slash commands: each is an async function (user id, arguments) -> reply text."""

import time
from zoneinfo import ZoneInfo

from . import db, flows, prompts, ratelimit, strings
from .chat import coach, privacy, say
from .config import CHECKIN_INTERVAL_MINUTES


async def _goal(uid, args):
    if len(db.active_goals(uid)) >= db.MAX_GOALS:
        return say(uid, "GOAL_FULL")
    return await flows.start_goal(uid)


async def _step(uid, args):
    return await flows.start_objective(uid)


async def _help(uid, args):
    return say(uid, "HELP")


async def _stuck(uid, args):
    task = " ".join(args)
    return await coach(uid, f"/stuck {task}".strip(), prompts.STUCK.format(task=f" on: {task}" if task else ""))


async def _plan(uid, args):
    extra = " ".join(args)
    return await coach(uid, f"/plan {extra}".strip(), prompts.PLAN.format(extra=f". Context: {extra}" if extra else ""))


async def _overwhelm(uid, args):
    extra = " ".join(args)
    return await coach(uid, f"/overwhelm {extra}".strip(), prompts.OVERWHELM.format(extra=f": {extra}" if extra else ""))


async def _remind(uid, args):
    try:
        minutes = float(args[0])
        if not 0 < minutes <= 60 * 24 * 30:
            raise ValueError
    except (IndexError, ValueError):
        return say(uid, "REMIND_USAGE")
    db.add_reminder(uid, time.time() + minutes * 60, " ".join(args[1:]) or say(uid, "REMIND_DEFAULT"))
    return say(uid, "REMIND_OK", minutes=minutes)


async def _goals(uid, args):
    goals, objectives = db.active_goals(uid), db.open_objectives(uid)
    parts = [
        f"{say(uid, 'GOALS_HEADER')}:\n" + "\n".join(f"{i}. {g['text']}" for i, g in enumerate(goals, 1))
        if goals
        else say(uid, "GOALS_NONE")
    ]
    if goals or objectives:
        parts.append(
            f"{say(uid, 'OBJ_HEADER')}:\n"
            + "\n".join(f"• {o['text']}" + (f" ({o['incentive']})" if o["incentive"] else "") for o in objectives)
            if objectives
            else say(uid, "OBJ_NONE")
        )
    return "\n\n".join(parts)


async def _toolbox(uid, args):
    items = db.toolbox(uid)
    if not items:
        return say(uid, "TOOLBOX_EMPTY")
    return f"{say(uid, 'TOOLBOX_HEADER')}:\n" + "\n".join(f"• {t['text']}" for t in items)


async def _progress(uid, args):
    return await coach(uid, "/progress", prompts.PROGRESS)


def _profile_text(uid: int) -> str:
    profile = db.get_profile(uid)
    lines = [f"{say(uid, f'PROFILE_{topic}')}: {profile[topic]}" for topic in flows.INTAKE_STEPS if profile.get(topic)]
    return f"{say(uid, 'PROFILE_HEADER')}:\n" + "\n".join(lines) if lines else ""


async def _notes(uid, args):
    parts = [part for part in (_profile_text(uid), db.get_notes(uid)) if part]
    return "\n\n".join(parts) or say(uid, "NOTES_EMPTY")


async def _intake(uid, args):
    return flows.start_intake(uid, restart=True)


async def _forget(uid, args):
    db.set_fields(uid, notes="", profile="")
    return say(uid, "NOTES_CLEARED")


async def _timezone(uid, args):
    if not args:
        return say(uid, "TZ_SHOW", tz=db.get_user(uid)["tz"])
    try:
        ZoneInfo(args[0])
    except Exception:
        return say(uid, "TZ_UNKNOWN")
    switched_on = db.confirm_timezone(uid, args[0])
    flows.settle_timezone(uid)  # answered by command: a timezone question that was open is over
    note = "\n\n" + say(uid, "CHECKINS_ENABLED", minutes=CHECKIN_INTERVAL_MINUTES) if switched_on else ""
    return say(uid, "TZ_SET", tz=args[0]) + note


async def _privacy(uid, args):
    return privacy(uid)


async def agree(uid, args):
    db.set_field(uid, "consent_at", time.time())
    return say(uid, "AGREED")


async def _deletedata(uid, args):
    if args[:1] != ["confirm"]:
        return say(uid, "DELETE_CONFIRM")
    message = say(uid, "DELETED")  # read the language before the user row disappears
    db.delete_user_data(uid)
    ratelimit.forget(uid)
    return message


async def _language(uid, args):
    if not args:
        return say(uid, "LANG_SHOW", name=strings.LANGUAGES[db.get_user(uid)["lang"]])
    code = args[0].lower()
    if code not in strings.LANGUAGES:
        return say(uid, "LANG_BAD")
    db.set_field(uid, "lang", code)
    return say(uid, "LANG_SET", name=strings.LANGUAGES[code])


def _checkin(name: str):
    async def run(uid, args):
        field, label = f"{name}_hour", say(uid, f"LABEL_{name}")
        if not args:
            h = db.get_user(uid)[field]
            return say(uid, "CHECKIN_SHOW_OFF" if h < 0 else "CHECKIN_SHOW_ON", label=label, name=name, hour=h)
        arg = args[0].lower()
        if arg == "off":
            hour = -1
        elif arg.isdigit() and 0 <= int(arg) <= 23:
            hour = int(arg)
        else:
            return say(uid, "CHECKIN_BAD", name=name)
        user = db.get_user(uid)
        start, end = (hour, user["evening_hour"]) if name == "morning" else (user["morning_hour"], hour)
        if hour >= 0 and 0 <= start and 0 <= end and start >= end:
            return say(uid, "CHECKIN_ORDER")
        db.set_field(uid, field, hour)
        if hour < 0:
            return say(uid, "CHECKIN_OFF", label=label, name=name)
        return say(uid, "CHECKIN_SET", label=label, hour=hour, tz=db.get_user(uid)["tz"])

    return run


async def _interval(uid, args):
    if not args:
        minutes = db.get_user(uid)["interval_min"] or 0
        return say(uid, "INTERVAL_SHOW", minutes=minutes) if minutes else say(uid, "INTERVAL_OFF")
    arg = args[0].lower()
    if arg == "off":
        db.set_field(uid, "interval_min", 0)
        return say(uid, "INTERVAL_OFF")
    minutes = CHECKIN_INTERVAL_MINUTES if arg == "on" else int(arg) if arg.isdigit() else 0
    if not 15 <= minutes <= 240:
        return say(uid, "INTERVAL_BAD")
    if not db.get_user(uid)["tz_set"]:
        return say(uid, "NEED_TZ")  # check-ins run all day: a wrong timezone would mean pings in the middle of the night
    db.set_field(uid, "interval_min", minutes)
    return say(uid, "INTERVAL_SET", minutes=minutes)


COMMANDS = {
    "start": _help,
    "help": _help,
    "stuck": _stuck,
    "plan": _plan,
    "overwhelm": _overwhelm,
    "remind": _remind,
    "notes": _notes,
    "forget": _forget,
    "goals": _goals,
    "toolbox": _toolbox,
    "progress": _progress,
    "timezone": _timezone,
    "language": _language,
    "interval": _interval,
    "goal": _goal,
    "step": _step,
    "intake": _intake,
    "morning": _checkin("morning"),
    "evening": _checkin("evening"),
    "privacy": _privacy,
    "agree": agree,
    "deletedata": _deletedata,
}


BEFORE_CONSENT = {"start", "help", "privacy", "agree", "language"}  # everything else waits until the user has agreed
