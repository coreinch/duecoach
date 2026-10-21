import asyncio
import difflib
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any, NamedTuple

import openai
from openai import AsyncOpenAI

from . import db, playbook, prompts, strings, tools
from .config import (
    FLAGGED_DRAFT_DAYS,
    HISTORY_TURNS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_CONCURRENCY,
    LLM_HELPER_EFFORT,
    LLM_MODEL,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT,
    LLM_VERIFIER_API_KEY,
    LLM_VERIFIER_BASE_URL,
    LLM_VERIFIER_MODEL,
)

log = logging.getLogger("duecoach.llm")

MAX_TOOL_ROUNDS = 4
MAX_EMPTY_RETRIES = 2
VERIFY_TOKENS = 4000  # the verifier thinks before it answers; 2000 was often used up with nothing left for the JSON
MAX_VERIFY_ROUNDS = 2  # times the verifier may send a draft back to the model
REPLY_TOKENS = 3000  # reasoning models spend part of this thinking; too small a limit cuts the answer off mid-sentence
MAX_CONSECUTIVE_ASSISTANT = 2  # in the history sent to the model: proactive check-ins pile up while a user is silent
MODEL_ATTEMPTS = 2  # tries per call: a provider hiccup is retried once
NOTES_EVERY = 20  # new chat messages between refreshes of the long-term notes
# Reasoning models: they think before they answer, and the thinking counts against max_tokens. A small limit
# is used up by the thinking and the answer comes back empty, so even one-sentence jobs get a generous budget.
SHORT_JOB_TOKENS = 2000

_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2)
# The verifier may live at another provider; then it has its own client (otherwise it shares the coach's).
_verifier_client = (
    AsyncOpenAI(api_key=LLM_VERIFIER_API_KEY, base_url=LLM_VERIFIER_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2)
    if (LLM_VERIFIER_BASE_URL, LLM_VERIFIER_API_KEY) != (LLM_BASE_URL, LLM_API_KEY)
    else _client
)


# MiniMax puts its reasoning inside the reply as <think>...</think> unless asked to split it off; the regex is a second safety net.
def _extra(model: str, effort: str | None = None) -> dict[str, Any]:
    if not model.startswith("MiniMax-"):
        return {}
    body: dict[str, Any] = {"reasoning_split": True}
    if model.startswith("MiniMax-M3.1-Flash"):
        body["reasoning_effort"] = effort or LLM_REASONING_EFFORT
    return {"extra_body": body}


_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_slots = asyncio.Semaphore(LLM_CONCURRENCY)  # one slow or rate-limited provider must not be hammered by every user at once


def _system(user_id: int) -> str:
    notes = db.get_notes(user_id)
    lang = db.get_user(user_id)["lang"]
    return (
        prompts.SYSTEM
        + "\n"
        + prompts.LANGUAGE.get(lang, prompts.LANGUAGE["en"])
        # the playbook comes before everything that differs per user, so the long static start of the prompt can be cached by the provider
        + "\n\nCoaching playbook (techniques to apply in your own words; use the one that fits what they need right now. The cards are in "
        "no order of priority: don't default to the first one, and don't use the same technique as in your last reply):\n\n"
        + playbook.full_text()
        + (f"\n\nWhat you know about the user:\n{notes}" if notes else "")
        + "\n\n"
        + tools.coaching_state(user_id)
    )


def _history(user_id: int) -> list[dict]:
    """Recent chat for the model, keeping at most a couple of assistant messages in a row (the latest ones)."""
    out: list[dict] = []
    for msg in db.recent_messages(user_id, HISTORY_TURNS):
        # stored text that must not be shown to the model: it would copy it (a leaked tool call) or take it for something the user said
        if (msg["role"] == "assistant" and _leaked_call(msg["content"])) or (msg["role"] == "user" and _MARKER.match(msg["content"])):
            continue
        out.append(msg)
        run = 0
        for m in reversed(out):
            if m["role"] != "assistant":
                break
            run += 1
        if run > MAX_CONSECUTIVE_ASSISTANT:
            del out[-run]  # drop the oldest message of the run
    return out


# A check-in that is a goodbye ("Talk tomorrow.", "Sleep well.") is wrong whatever the time: the model copies the sign-off that ended the
# chat hours ago. Only the evening wrap-up and the last "I'll go quiet" message may say goodnight.
_SIGN_OFF = re.compile(
    r"sleep well|talk (to you )?tomorrow|see you tomorrow|good ?night|have a (good|great|nice|lovely) (evening|night|day)|"
    r"καληνύχτα|καλό βράδυ|τα λέμε αύριο",
    re.IGNORECASE,
)
# A tool call written as text instead of made ("[Calling set_reminder with parameter at = ...]"): no tool name ever belongs in a message
# to the user, and the check-in turn has no tools at all, so the model sometimes "calls" one in words.
_TOOL_NAMES = "|".join(t["function"]["name"] for t in tools.TOOLS)
_LEAKED_CALL = re.compile(rf"\b(?:{_TOOL_NAMES})\b|<\s*/?\s*(?:tool_call|function_call|invoke)\b|\[\s*calling\s+\w+", re.IGNORECASE)
# Old stored markers of the removed scripted flows, which are not things the user wrote
_MARKER = re.compile(r"^\((?:finished the intake interview|step result)")
_QUESTION_ENDINGS = ("?", ";", "\u037e")
_MARKDOWN: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL), lambda m: m.group(1) or m.group(2)),
    (re.compile(r"^\s*#{1,6}\s*", re.MULTILINE), ""),
    (re.compile(r"^(\s*)\*\s+", re.MULTILINE), r"\1- "),
]


def _plain(text: str) -> str:
    """Chat apps show the model's markdown as literal asterisks and hashes: strip bold and headings, keep the words."""
    for pattern, repl in _MARKDOWN:
        text = pattern.sub(repl, text)
    return text


# Clock times in a reply that confirms a reminder must be the ones the tool reported: the model has written "in an hour, at 21:00" at 10:02.
_CLOCK = re.compile(r"(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")


def _clocks(text: str) -> set[tuple[int, int]]:
    return {(int(h), int(m)) for h, m in _CLOCK.findall(text)}


def _reminder_times(results: list[str]) -> set[tuple[int, int]]:
    """The local times the set_reminder calls of this turn reported ("ok: reminder #3 set for Sat 11:33 local time")."""
    return {t for r in results if r.startswith("ok: reminder #") for t in _clocks(r)}


def _fix_reminder_times(text: str, results: list[str], user_text: str) -> tuple[str, list[str]]:
    """Check the clock times a reply states after a reminder was set. Returns the text, corrected when the fix is unambiguous, and the
    invented times still left in it (empty when the reply is fine)."""
    reported = _reminder_times(results)
    if not reported:
        return text, []
    known = reported | {(h % 24, m) for h, m in _clocks(user_text)} | {((h + 12) % 24, m) for h, m in _clocks(user_text)}
    invented = sorted(_clocks(text) - known)
    if not invented:
        return text, []
    if len(reported) == 1 and len(invented) == 1:
        (h, m), (rh, rm) = invented[0], next(iter(reported))
        pattern = re.compile(rf"(?<![\d:.]){h:02d}:{m:02d}(?![\d:])|(?<![\d:.]){h}:{m:02d}(?![\d:])")
        return pattern.sub(f"{rh:02d}:{rm:02d}", text), []
    return text, [f"{h:02d}:{m:02d}" for h, m in invented]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;\u037e])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.split()) >= 5]  # shorter ones ("Go.", "Nice work.") may repeat


def _norm_sentence(sentence: str) -> str:
    return " ".join(re.findall(r"\w+", sentence.casefold()))


def _repeated(text: str, user_id: int) -> list[str]:
    """Sentences of this draft that the coach already said, almost word for word, in its last three replies."""
    earlier = [m["content"] for m in db.recent_messages(user_id, 10) if m["role"] == "assistant"][-3:]
    seen = [_norm_sentence(s) for message in earlier for s in _sentences(message)]
    return [s for s in _sentences(text) if any(difflib.SequenceMatcher(None, _norm_sentence(s), old).ratio() >= 0.85 for old in seen)]


def _without(text: str, sentences: list[str]) -> str:
    """The text with those sentences left out, or unchanged if nothing else would remain."""
    rest = text
    for sentence in sentences:
        rest = rest.replace(sentence, "")
    rest = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", rest)).strip()
    return rest if rest else text


# Ids in a reply: the user never sees record ids.
_ID_MENTION = re.compile(r"\b(?:goal|objective|step|reminder|item)\s+#\d+", re.IGNORECASE)


_UPDATE_MENTIONS = re.compile(
    r"\btimer\b|\breminder\b|check(?:ing)? (?:back )?in\b|\b\d{1,2}:\d{2}\b|i'?ll be here|almost there|nearly (?:there|done)|one last (?:look|check)",
    re.IGNORECASE,
)


def _running_timer_hint(user_id: int, text: str, saved: set[str], user_text: str = "") -> str | None:
    """Style hint for a short update while a timer runs, or None. (Whether a timer claim is true is the verifier's job.)"""
    pending = db.pending_reminders(user_id)
    if pending and len(user_text.split()) <= 4 and "set_reminder" not in saved and len(text.split()) < 4:
        return "That reply is too short. Write one specific sentence about the task itself (6-15 words), not just a word or two."
    if pending and len(user_text.split()) <= 4 and "set_reminder" not in saved and _UPDATE_MENTIONS.search(text):
        return "A timer is running and they sent a short update. Answer about the task only: don't mention the timer, a time, checking in or how far along they are."
    return None


# The model's own reasoning written into the reply ("I can see the coaching record is empty. I should acknowledge what they shared"):
# the coach talks TO them, so it never says "the user", mentions the record, or narrates what it should do.
_META = re.compile(
    r"\bmy (?:brain|body)\b|\bI(?:'m| am) (?:tired|hungry)\b|\btired here too\b|"
    r"\bthe user\b|\bcoaching record\b|\bI should (?:acknowledge|offer|ask|respond|start|give|keep|not|use|check|follow|reflect|validate)\b"
    r"|\bmy (?:instructions|guidelines|system prompt)\b|\bthey'?ve named\b|\baccording to (?:the|my) (?:record|guidelines|instructions)\b",
    re.IGNORECASE,
)


def _meta_reasoning(text: str) -> bool:
    return bool(_META.search(text))


def _adhd_lecture(user_id: int, text: str) -> bool:
    """The coach explains ADHD to someone who never brought it up ("common with ADHD"): they know, and it reads as a lecture."""
    if not re.search(r"\bADHD\b", text):
        return False
    said = " ".join(m["content"] for m in db.recent_messages(user_id, 10) if m["role"] == "user")
    return not re.search(r"\bADHD\b|\bAD/?HD\b|ΔΕΠΥ", said, re.IGNORECASE)


def _drop_sentences(text: str, pattern: re.Pattern[str]) -> str:
    """The text without the sentences that match, or unchanged if nothing would remain."""
    parts = re.split(r"(?<=[.!?;\u037e])\s+|\n+", text)
    kept = [p for p in parts if p.strip() and not pattern.search(p)]
    return " ".join(kept) if kept else text


def _without_ids(text: str) -> str:
    return _ID_MENTION.sub(lambda m: m.group(0).split("#")[0].rstrip(), text)


def _leaked_call(text: str) -> bool:
    return bool(_LEAKED_CALL.search(text))


def _unfit_checkin(text: str, instruction: str | None) -> bool:
    """A check-in must coach: not a few words with nothing to answer, and not a goodnight at the wrong time."""
    if len(text.split()) < 6 and not text.rstrip().endswith(_QUESTION_ENDINGS):
        return True
    return bool(_SIGN_OFF.search(text)) and instruction not in (prompts.EVENING, prompts.REENGAGE_LAST)


class ModelError(RuntimeError):
    """The model produced no completion (outage, rate limit, timeout), with what it reported."""


class CheckinWithheld(ModelError):
    """A check-in was written but is not fit to send (still flagged, a goodbye, a leaked call, empty): the guards working, not a failure."""


def status() -> dict:
    """For /healthz: which model is in use."""
    return {"model": LLM_MODEL}


def _cached_tokens(usage: Any) -> int | None:
    """Tokens of the prompt that the provider served from its cache, or None when the response does not say (names differ by provider)."""
    details = getattr(usage, "prompt_tokens_details", None)
    extra = getattr(usage, "model_extra", None) or {}
    for found in (
        getattr(details, "cached_tokens", None),
        getattr(usage, "prompt_cache_hit_tokens", None),
        extra.get("prompt_cache_hit_tokens"),
        extra.get("cache_read_input_tokens"),
        extra.get("cached_tokens"),
    ):
        if isinstance(found, int):
            return found
    return None


def _log_usage(model: str, resp: Any) -> None:
    """One line per model call with the prompt size and how much of it came from the provider's cache (to check that caching works)."""
    usage = getattr(resp, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    if isinstance(prompt, int):
        log.info("usage %s: prompt %d tokens, cached %s", model, prompt, _cached_tokens(usage))


async def _chat(
    messages: Any, tool_defs: list[dict] | None = None, max_tokens: int = 1500, model: str | None = None, effort: str | None = None
):
    model = model or LLM_MODEL
    client = _verifier_client if model == LLM_VERIFIER_MODEL and model != LLM_MODEL else _client
    kwargs: dict[str, Any] = {"tools": tool_defs} if tool_defs else {}
    problem = "no completion"
    for attempt in range(MODEL_ATTEMPTS):
        try:
            async with _slots:
                resp = await client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, **kwargs, **_extra(model, effort)
                )
        except openai.OpenAIError as error:  # connection, timeout, rate limit, server error, or a request the model rejects
            problem = f"{type(error).__name__}: {error}"[:200]
        else:
            _log_usage(model, resp)
            if resp.choices:
                choice = resp.choices[0]
                choice.message.finish_reason = getattr(choice, "finish_reason", None)  # "length": the answer was cut off
                if choice.message.content and "<think>" in choice.message.content:  # reasoning must never reach the user
                    choice.message.content = _THINK.sub("", choice.message.content).strip()
                return choice.message
            # some providers answer HTTP 200 with {"error": {...}} when the service behind them is down
            error_body = getattr(resp, "error", None) or (getattr(resp, "model_extra", None) or {}).get("error")
            problem = str(error_body or "empty response")[:200]
        log.warning("model %s failed (%s)%s", model, problem, ", retrying" if attempt + 1 < MODEL_ATTEMPTS else "")
        if attempt + 1 < MODEL_ATTEMPTS:
            await asyncio.sleep(1)
    raise ModelError(f"{model}: {problem}")


async def _complete(messages: list[dict], max_tokens: int = 1500, model: str | None = None) -> str:
    if model and model != LLM_MODEL:  # only a different model is passed on
        return ((await _chat(messages, max_tokens=max_tokens, model=model, effort=LLM_HELPER_EFFORT)).content or "").strip()
    return ((await _chat(messages, max_tokens=max_tokens, effort=LLM_HELPER_EFFORT)).content or "").strip()


def _since(last_inbound: float | None) -> str:
    """'The user last wrote 3 hours ago, so anything they asked for then is done or over.' (empty if they never wrote)."""
    if not last_inbound:
        return ""
    minutes = int((time.time() - last_inbound) // 60)
    ago = f"{minutes} minutes" if minutes < 90 else f"{minutes // 60} hours" if minutes < 2880 else f"{minutes // 1440} days"
    return (
        f"The user last wrote {ago} ago, so a timer or reminder they asked for in that message has already fired. "
        "An appointment, call or plan they named for a later time is NOT over until the Now line has passed that time: "
        "check the clock before saying it passed or asking how it went."
    )


async def record_agreements(user_id: int, user_text: str) -> list[str]:
    """Before the coach answers: if the user just agreed to a goal or a step the coach proposed, save it.

    Saving is not left to the coach's own tool call, which the model skips about half the time. A separate small call reads the one
    exchange and returns what, if anything, was agreed; the code writes it. Only runs while a goal or a step is still missing.
    Returns notes for the coach about what was saved.
    """
    goals, steps = db.active_goals(user_id), db.open_objectives(user_id)
    if (goals and steps) or user_text.startswith("/"):
        return []
    last = next((m["content"] for m in reversed(db.recent_messages(user_id, 6)) if m["role"] == "assistant"), None)
    if not last:
        return []
    prompt = prompts.RECORDER.format(
        coach=last[:1200],
        user=user_text[:600],
        goals="; ".join(g["text"] for g in goals) or "none",
        steps="; ".join(o["text"] for o in steps) or "none",
        language=prompts.LANGUAGE_NAME.get(db.get_user(user_id)["lang"], "English"),
    )
    try:
        out = await _complete([{"role": "user", "content": prompt}], max_tokens=SHORT_JOB_TOKENS)
        found = json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0))  # type: ignore[union-attr]
    except Exception:
        log.warning("could not read the agreement check, leaving the saving to the coach")
        return []
    notes = []
    goal, step = found.get("goal"), found.get("step")
    if isinstance(goal, str) and goal.strip() and not goals:
        result = tools.run_tool(user_id, "add_goal", json.dumps({"text": goal.strip()}))
        if result.startswith("ok"):
            notes.append(f"goal saved: {goal.strip()}")
            goals = db.active_goals(user_id)
    if isinstance(step, str) and step.strip() and not steps:
        args = {"text": step.strip()} | ({"goal_id": goals[0]["id"]} if goals else {})
        if tools.run_tool(user_id, "add_objective", json.dumps(args)).startswith("ok"):
            notes.append(f"step saved: {step.strip()}")
    return notes


async def signals_crisis(user_id: int, text: str) -> bool:
    """Does the message show a crisis (suicide, self-harm, abuse, danger), in any language or wording?

    The word list in crisis.py catches the plain cases without a model; this reads what a list cannot (other languages, indirect or
    slang wording). A call that fails or answers badly says no: the list is still there, and a coaching reply is never blocked by it.
    """
    recent = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in db.recent_messages(user_id, 4)) or "(none)"
    prompt = prompts.CRISIS_CHECK.format(recent=recent, user=text[:600])
    try:
        out = await _complete([{"role": "user", "content": prompt}], max_tokens=SHORT_JOB_TOKENS)
        return bool(json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0)).get("crisis") is True)  # type: ignore[union-attr]
    except Exception:
        log.warning("could not read the safety check, relying on the word list")
        return False


def _checkin_rules_for_verifier(user_id: int) -> str:
    """The check-in rules for the verifier; stricter when the code knows the user has stopped answering check-ins."""
    quiet = db.get_user(user_id)["unanswered"] or 0
    return prompts.VERIFIER_CHECKIN + (prompts.VERIFIER_QUIET.format(n=quiet) if quiet > 0 else "")


class Verdict(NamedTuple):
    """What the verifier found: the note that goes back to the coach, and which kinds of problem it was."""

    feedback: str
    kinds: tuple[str, ...]


async def _verify(user_id: int, user_text: str, draft: str, done: list[str]) -> Verdict | None:
    """Ask a separate model call whether the draft claims something no tool did, or leaves something the user said unhandled.

    Returns feedback for the coach, or None when the draft stands. A verifier that fails or answers badly never blocks a reply.
    """
    checkin = not user_text  # an automatic check-in: nothing new from the user, and the draft is not stored yet
    recent = db.recent_messages(user_id, 5)
    prompt = prompts.VERIFIER.format(
        recent="\n".join(f"{m['role']}: {m['content'][:300]}" for m in (recent if checkin else recent[:-1])) or "(none)",
        user="(nothing: automatic check-in)" if checkin else user_text[:600],
        checkin=_checkin_rules_for_verifier(user_id) if checkin else "",
        draft=draft[:1200],
        done=", ".join(done) or "none",
        tools=", ".join(t["function"]["name"] for t in tools.TOOLS),
        state=tools.coaching_state(user_id),
    )
    out = ""
    try:
        for _ in range(2):  # a reasoning model sometimes spends the whole budget thinking and answers with nothing: ask once more
            out = await _complete([{"role": "user", "content": prompt}], max_tokens=VERIFY_TOKENS, model=LLM_VERIFIER_MODEL)
            if out:
                break
        found = json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0))  # type: ignore[union-attr]
        kinds = ("unbacked", "unhandled", "off_target", "invented_time", "unclear_reference")
        problems = [str(x) for k in kinds for x in found.get(k, []) if str(x).strip()]
        flagged = tuple(k for k in kinds if any(str(x).strip() for x in found.get(k, [])))
    except Exception as error:  # the answer itself is not logged: it quotes the user
        log.warning("could not use the verifier answer (%s, %d chars), sending the reply unchecked", type(error).__name__, len(out))
        return None
    if not problems:
        log.info("verifier ok")
        return None
    log.warning("verifier flagged a draft: %s (%d problem(s))", ", ".join(flagged), len(problems))
    if FLAGGED_DRAFT_DAYS > 0:
        try:
            db.add_flagged_draft(user_id, list(flagged), user_text, draft, "; ".join(problems))
        except Exception:
            log.exception("could not keep the flagged draft")
    feedback = (
        f'Your draft was: "{draft[:600]}"\nA check found: '
        + "; ".join(problems)
        + ". "
        + (
            "Rewrite the check-in without those claims: ask instead of asserting. No tools. "
            if checkin
            else "For what the user asked for or agreed to, call the missing tools now; for anything only the draft promised or assumed, "
            "delete it (never set a reminder, timer or save they did not ask for). Then write the reply so that it says only what "
            "is true and answers what they said. "
        )
        + (
            "Where something is unclear, say in a few words what it is, in the user's own terms, not in the wording it was saved with. "
            if "unclear_reference" in flagged
            else ""
        )
        + "Do not mention this check."
    )
    return Verdict(feedback, flagged)


_NEW_TIME = re.compile(r"^ok: (reminder|event) #(\d+) (?:set|saved) for")


def _undo_new_times(user_id: int, results: list[str]) -> None:
    """The verifier still objected to a reminder or event set this turn for a time nobody gave: take it back rather than leave it waiting."""
    for result in results:
        found = _NEW_TIME.match(result)
        if found:
            (db.cancel_reminder if found.group(1) == "reminder" else db.cancel_event)(user_id, int(found.group(2)))


def _record_stat(user_id: int, user_text: str, stat: dict, started: float) -> None:
    """Statistics about the turn without any text of it: what the verifier flagged, which tools ran, how it ended."""
    try:
        db.add_turn_stat(
            user_id,
            "reply" if user_text else "checkin",
            stat["outcome"],
            stat["flags"],
            stat["tools"],
            stat["rounds"],
            round(time.monotonic() - started, 1),
        )
    except Exception:
        log.exception("could not record the turn statistics")


async def reply(user_id: int, user_text: str, instruction: str | None = None) -> str:
    """Chat with history, in one model call: the whole playbook is in the system prompt, so no lookup round is needed.

    `instruction` is a hidden steering note for this turn (e.g. a /stuck command or a check-in's purpose).
    """
    stat: dict[str, Any] = {"outcome": "sent", "flags": [], "tools": [], "rounds": 0}
    started = time.monotonic()
    try:
        return await _reply(user_id, user_text, instruction, stat)
    except Exception:
        stat["outcome"] = "dropped" if not user_text else "error"
        raise
    finally:
        _record_stat(user_id, user_text, stat, started)


async def _reply(user_id: int, user_text: str, instruction: str | None, stat: dict[str, Any]) -> str:
    recorded = await record_agreements(user_id, user_text) if user_text and not instruction else []
    if user_text:
        db.add_message(user_id, "user", user_text)
    messages = [
        {"role": "system", "content": _system(user_id)},
        *_history(user_id),
    ]
    if recorded:
        messages.append(
            {
                "role": "system",
                "content": "Already saved for them just now: " + "; ".join(recorded) + ". You may confirm it in a few words (no ids); "
                "do not save it again.",
            }
        )
    messages.append({"role": "system", "content": prompts.TOOL_GUARD if user_text else prompts.PROACTIVE_GUARD})
    if instruction and user_text:
        messages.append({"role": "system", "content": instruction})
    elif instruction:
        # A check-in: the last thing in the chat must not look like an unanswered message from the user, so the instruction closes it
        # as an automatic trigger (a system message at the end leaves their old request as the last thing they said).
        since = _since(db.get_user(user_id)["last_inbound"])
        trigger = (
            "[Automatic check-in trigger. This is not a message from the user."
            + (f" {since}" if since else "")
            + " Whatever ended the earlier chat (a goodnight, 'talk tomorrow') is over: time has passed, so never answer with a sign-off.]"
        )
        messages.append({"role": "user", "content": f"{trigger}\n{instruction}"})
    # A check-in has no new message from the user, and the model acts on their last (old) one: no tools then, so it can't, for example,
    # set again the reminder they asked for hours ago. Their answer to the check-in is a normal turn with tools.
    tool_defs = tools.TOOLS if user_text else None
    text, empty_retries = "", 0
    results: list[str] = []
    wrong_times: list[str] = []
    repeats: list[str] = []
    saved: set[str] = {"add_goal" if n.startswith("goal") else "add_objective" for n in recorded}
    id_mention = lecture = meta = False
    timer_hint: str | None = None
    verify_rounds = 0
    unresolved: tuple[str, ...] = ()  # what the verifier still objected to after the last allowed send-back
    sent_back: tuple[str, ...] = ()  # the kinds the verifier flagged in a draft that went back and has no successor yet
    for _ in range(MAX_TOOL_ROUNDS + MAX_EMPTY_RETRIES + MAX_VERIFY_ROUNDS):
        msg = await _chat(messages, tool_defs, max_tokens=REPLY_TOKENS)
        stat["rounds"] += 1
        sent_back = ()
        text = (msg.content or "").strip()
        truncated = getattr(msg, "finish_reason", None) == "length"
        if not msg.tool_calls:
            if text:
                text, wrong_times = _fix_reminder_times(text, results, user_text)
            repeats = _repeated(text, user_id) if text else []
            id_mention = bool(text) and bool(_ID_MENTION.search(text))
            timer_hint = _running_timer_hint(user_id, text, saved, user_text) if text and user_text else None
            lecture = bool(text) and bool(user_text) and _adhd_lecture(user_id, text)
            meta = bool(text) and _meta_reasoning(text)
            unfit = bool(text) and (
                bool(wrong_times)
                or bool(repeats)
                or id_mention
                or bool(timer_hint)
                or lecture
                or meta
                or _leaked_call(text)
                or (not user_text and _unfit_checkin(text, instruction))
            )
            if text and not truncated and not unfit:
                verdict = await _verify(user_id, user_text, text, sorted(saved))
                if verdict:
                    stat["flags"] += verdict.kinds
                    if verify_rounds < MAX_VERIFY_ROUNDS:
                        verify_rounds += 1
                        sent_back = verdict.kinds
                        feedback = verdict.feedback
                        if "invented_time" in verdict.kinds:
                            # Not left to the model: a model that set a reminder for a time nobody gave tends to set it again after being
                            # told. Take it back here, close the tools for the rest of the turn and have it ask for the real time.
                            _undo_new_times(user_id, results)
                            saved -= {"set_reminder", "add_event", "reschedule_event"}
                            tool_defs = None
                            feedback += (
                                "\nThe reminder or event with the invented time is already removed, so nothing is set or saved for it now: do not say "
                                "that it is saved, set or already there. Call no tool: reply in words only and ask the user for the real time."
                            )
                        messages.append({"role": "system", "content": feedback})
                        continue
                    unresolved = verdict.kinds  # the last draft is still flagged: see below
            if (text and not truncated and not unfit) or empty_retries >= MAX_EMPTY_RETRIES:
                break
            if wrong_times:
                messages.append(
                    {
                        "role": "system",
                        "content": f"Your draft gave the time {', '.join(wrong_times)}, which is not the time of the reminder you set. "
                        "Give only the time from the tool result, exactly as it is, or no clock time at all.",
                    }
                )
            if repeats and not wrong_times:
                messages.append(
                    {
                        "role": "system",
                        "content": "Your draft repeats what you already said in an earlier reply: "
                        + " / ".join(f'"{s}"' for s in repeats[:2])
                        + ". Say it once only: leave it out or say something new.",
                    }
                )
            if id_mention:
                messages.append(
                    {"role": "system", "content": 'Don\'t say record ids or numbers like "goal #1" to the user; leave them out.'}
                )
            if timer_hint:
                messages.append({"role": "system", "content": timer_hint})
            if meta:
                messages.append(
                    {
                        "role": "system",
                        "content": 'Your draft described your own reasoning or a human life. Write only the message to them, talking to "you".',
                    }
                )
            if lecture:
                messages.append(
                    {
                        "role": "system",
                        "content": "They never mentioned ADHD. Don't explain ADHD or their pattern; go to the next concrete thing.",
                    }
                )
            # the model sometimes returns only hidden reasoning, stops mid-sentence, or (check-in) says goodbye: ask again
            empty_retries += 1
            log.warning(
                "%s model reply, retrying (%d)",
                "leaked call, wrong time, repeat, unsaved claim, id or unfit check-in" if unfit else "cut-off" if text else "empty",
                empty_retries,
            )
            continue
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in msg.tool_calls
                ],
            }
        )
        for call in msg.tool_calls:
            result = tools.run_tool(user_id, call.function.name, call.function.arguments)
            # log the outcome, not the arguments: they contain what the user wrote about their life. A refusal is our own fixed
            # text, so it is logged in full: it is what explains why a call failed.
            log.info("tool %s -> %s", call.function.name, result[:120] if result.startswith("error") else result.split(":", 1)[0])
            results.append(result)
            stat["tools"].append(call.function.name)
            if not result.startswith("error"):
                saved.add(call.function.name)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    else:
        # Out of rounds without a final answer: the last thing the model produced was a tool call (its text, if any, was never checked),
        # or a draft the verifier had just sent back. Neither may go out as it is.
        if msg.tool_calls:
            text = ""
        elif sent_back:
            unresolved = sent_back
    if unresolved:
        if not user_text:
            raise CheckinWithheld("the check-in was still flagged by the verifier after the retries: " + ", ".join(unresolved))
        if {"unbacked", "invented_time"} & set(unresolved):  # it claims or sets something nobody asked for: don't send that
            if "invented_time" in unresolved:
                _undo_new_times(user_id, results)
            stat["outcome"] = "fallback"
            # what was just taken back no longer exists: the last reply must not be written from it
            text = await _last_resort(
                messages, [r for r in results if not _NEW_TIME.match(r)] if "invented_time" in unresolved else results, unresolved
            )
            if text:
                db.add_message(user_id, "assistant", text)
                return text
            return strings.t(db.get_user(user_id)["lang"], "UNSURE")
    if truncated and text:
        text = _trim_to_sentence(text)
    if text and (_leaked_call(text) or wrong_times or _meta_reasoning(text)):
        if user_text:
            text = ""  # the apology below; a tool call written out is never shown to the user
        else:
            raise CheckinWithheld("the check-in contained a tool call written as text or a wrong time")
    if not user_text and text and _unfit_checkin(text, instruction):
        raise CheckinWithheld("the check-in was a goodbye or too short to coach")  # nothing is sent; the caller backs off and tries later
    if text and repeats:
        text = _without(text, repeats)  # still repeating after the retries: say it without the repeated sentences
    if text and lecture:
        text = _drop_sentences(text, re.compile(r"\bADHD\b"))  # explained ADHD to someone who never brought it up: cut that sentence
    text = _without_ids(_plain(text))
    if not text:
        # Say so honestly, and keep it out of the history: stored as the coach's own words it makes the model imitate it.
        if not user_text:
            raise CheckinWithheld("the check-in came back empty")  # never send the apology as a check-in
        stat["outcome"] = "fallback"
        return strings.t(db.get_user(user_id)["lang"], "EMPTY_REPLY")
    db.add_message(user_id, "assistant", text)
    return text


async def _last_resort(messages: list[dict], results: list[str], unresolved: tuple[str, ...]) -> str:
    """One more model call when the verifier would not pass any draft: a short, true reply instead of a canned "say that again".

    It sees the same chat and the tool results, with no tools and no verifier (another round would not end). A call that fails, or
    an answer that is empty or a tool call written as text, returns "" and the caller falls back to the fixed sentence.
    """
    note = (
        f"Your drafts for this message kept being flagged ({', '.join(unresolved)}). Write one short reply now. Say only what is true: "
        "nothing was set, saved, cancelled or changed except what these tool results show: "
        + ("; ".join(results) if results else "none")
        + ". Give a clock time only as it appears there. If you are unsure what they meant, ask ONE short question. Do not mention this check."
    )
    try:
        msg = await _chat([*messages, {"role": "system", "content": note}], None, max_tokens=REPLY_TOKENS)
    except Exception:
        log.exception("the last-resort reply failed")
        return ""
    text = _without_ids(_plain((msg.content or "").strip()))
    return "" if not text or _leaked_call(text) or getattr(msg, "finish_reason", None) == "length" else text


def _trim_to_sentence(text: str) -> str:
    """Cut a reply that ran out of tokens back to its last complete sentence (the whole text if there is none)."""
    cut = max(text.rfind(c) for c in ".!?;\u037e\u2026")
    return text[: cut + 1] if cut >= len(text) // 3 else text


async def refresh_notes(user_id: int) -> None:
    """Fold recent conversation into long-term notes once 20 or more new messages have piled up since the last time.

    (Counting "since the last time" matters: check-ins store a single message, so the count is not always a multiple of 20.)
    """
    count = db.message_count(user_id)
    if count - (db.get_user(user_id)["notes_at_count"] or 0) < NOTES_EVERY:
        return
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in db.recent_messages(user_id, 40))
    prompt = prompts.SUMMARIZE.format(
        notes=db.get_notes(user_id) or "(none)", convo=convo, language=prompts.LANGUAGE_NAME.get(db.get_user(user_id)["lang"], "English")
    )
    notes = await _complete([{"role": "user", "content": prompt}], max_tokens=SHORT_JOB_TOKENS)
    if notes:
        db.set_fields(user_id, notes=notes, notes_at_count=count)
