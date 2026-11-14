import asyncio
import base64
import difflib
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from typing import Any, NamedTuple

import httpx
import openai
from openai import AsyncOpenAI

from . import db, playbook, prompts, strings, tools
from .config import (
    FLAGGED_DRAFT_DAYS,
    HISTORY_TURNS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_CONCURRENCY,
    LLM_DEEPSEEK_EFFORT,
    LLM_GROK_EFFORT,
    LLM_HELPER_EFFORT,
    LLM_MODEL,
    LLM_REASONING_EFFORT,
    LLM_SIDE_MODEL,
    LLM_TIMEOUT,
    LLM_VERIFIER_API_KEY,
    LLM_VERIFIER_BASE_URL,
    LLM_VERIFIER_MODEL,
    TELEGRAM_BOT_TOKEN,
    VOICE_MODEL,
    VOICE_URL,
)

log = logging.getLogger("duecoach.llm")

MAX_TOOL_ROUNDS = 4
MAX_EMPTY_RETRIES = 2
# The verifier thinks before it answers. On DeepSeek (effort low) half the calls use under 430 tokens (5 s), 83% under 1,400, and about 1 in 12
# runs to any cap with nothing left for the JSON: at 4,000 tokens that was 48 s for an empty answer, twice, before the reply went out unchecked.
VERIFY_TOKENS = 2000
NO_THINKING_TOKENS = 1200  # the retry after an empty answer answers directly: JSON or a short summary fits
VERIFY_RETRY_TOKENS = 800  # the retry answers directly (no reasoning where the model allows that), so a little is enough
MAX_VERIFY_ROUNDS = 2  # times the verifier may send a draft back to the model
REPLY_TOKENS = 3000  # reasoning models spend part of this thinking; too small a limit cuts the answer off mid-sentence
MAX_CONSECUTIVE_ASSISTANT = 2  # in the history sent to the model: proactive check-ins pile up while a user is silent
MODEL_ATTEMPTS = 2  # tries per call: a provider hiccup is retried once
NOTES_EVERY = 20  # new chat messages between refreshes of the long-term notes
# Reasoning models: they think before they answer, and the thinking counts against max_tokens. A small limit
# is used up by the thinking and the answer comes back empty, so even one-sentence jobs get a generous budget.
SHORT_JOB_TOKENS = 2000
AGREEMENT_MAX_WORDS = 30
VOICE_TOKENS = 1500  # a transcript of up to a couple of minutes of speech
FIRST_PASS_TOKENS = 4000  # the main model thinks before it answers


_session: ContextVar[int | None] = ContextVar("llm_session", default=None)  # whose conversation the model calls in this task belong to


def _for(user_id: int) -> None:
    """Mark the model calls that follow (in this task and the ones it starts) as this user's conversation."""
    _session.set(user_id)


def _session_id(user_id: int | None) -> str:
    """A stable id per user for the provider's routing and prompt cache. Hashed with the bot token: the provider never sees the real id."""
    if user_id is None:
        return "duecoach"
    return "duecoach-" + hmac.new(TELEGRAM_BOT_TOKEN.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()[:16]


def _headers(base_url: str, user_id: int | None = None) -> dict[str, str]:
    """OpenCode routes and caches by session (one constant id for everyone got no cache hits, one per user does) and wants the client
    to name itself; other providers need neither."""
    if "opencode.ai" in base_url:
        return {"x-opencode-session": _session_id(user_id), "User-Agent": "duecoach/1.0"}
    return {}


_client = AsyncOpenAI(
    api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2, default_headers=_headers(LLM_BASE_URL)
)
# The verifier may live at another provider; then it has its own client (otherwise it shares the coach's).
_verifier_client = (
    AsyncOpenAI(
        api_key=LLM_VERIFIER_API_KEY,
        base_url=LLM_VERIFIER_BASE_URL,
        timeout=LLM_TIMEOUT,
        max_retries=2,
        default_headers=_headers(LLM_VERIFIER_BASE_URL),
    )
    if (LLM_VERIFIER_BASE_URL, LLM_VERIFIER_API_KEY) != (LLM_BASE_URL, LLM_API_KEY)
    else _client
)


# MiniMax puts its reasoning inside the reply as <think>...</think> unless asked to split it off; the regex is a second safety net.
# Grok 4.3 and newer take reasoning_effort and think at a slow default without it (on the real prompt: low 18 s, medium 51 s); grok-4.20 (reasoning or not) rejects the parameter.
_GROK_EFFORT = re.compile(r"grok-(4\.[3-9](?!\d)|[5-9])")


def _extra(model: str, effort: str | None = None) -> dict[str, Any]:
    if _GROK_EFFORT.match(model):
        return {"extra_body": {"reasoning_effort": effort or LLM_GROK_EFFORT}}
    if model.startswith("deepseek-"):
        return {"extra_body": {"reasoning_effort": effort or LLM_DEEPSEEK_EFFORT}}
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
        # the playbook comes before everything that differs per user, so the static start of the prompt is identical for all users (MiniMax does not cache it today: cached_tokens stays near 128)
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
        done = getattr(usage, "completion_tokens", None)
        log.info(
            "usage %s: prompt %d tokens, cached %s, answer %s", model, prompt, _cached_tokens(usage), done if isinstance(done, int) else "?"
        )


async def _chat(
    messages: Any, tool_defs: list[dict] | None = None, max_tokens: int = 1500, model: str | None = None, effort: str | None = None
):
    model = model or LLM_MODEL
    client = _verifier_client if model == LLM_VERIFIER_MODEL and model != LLM_MODEL else _client
    headers = _headers(LLM_BASE_URL if client is _client else LLM_VERIFIER_BASE_URL, _session.get())
    kwargs: dict[str, Any] = {"tools": tool_defs} if tool_defs else {}
    problem = "no completion"
    for attempt in range(MODEL_ATTEMPTS):
        try:
            async with _slots:
                resp = await client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, extra_headers=headers or None, **kwargs, **_extra(model, effort)
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


async def _complete(messages: list[dict], max_tokens: int = 1500, model: str | None = None, effort: str | None = None) -> str:
    """A short side job at the helper effort, on the side model unless a model is named (the verifier names its own).

    DeepSeek sometimes spends the whole budget thinking and answers with nothing (about 1 call in 12 on the verifier: ~25 s for an empty
    answer, and for the notes refresh a retry after every following message). An empty answer is asked for once more without the
    thinking. A caller that sets the effort itself handles its own retry.
    """
    model = model or LLM_SIDE_MODEL
    out = ((await _chat(messages, max_tokens=max_tokens, model=model, effort=effort or LLM_HELPER_EFFORT)).content or "").strip()
    if not out and effort is None and model.startswith("deepseek-"):
        log.warning("model %s answered with nothing (its thinking used the budget), asking again without thinking", model)
        out = ((await _chat(messages, max_tokens=min(max_tokens, NO_THINKING_TOKENS), model=model, effort="none")).content or "").strip()
    return out


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


_UNREAD: Any = object()  # "the agreement check has not been run yet" (None means it ran and found nothing)
_preread: dict[int, tuple[str, Any]] = {}  # per user: (message, what the agreement check read), set by precheck, used by the next reply


async def read_agreements(user_id: int, user_text: str) -> dict | None:
    """The model call of the agreement check: what the user just agreed to, if anything. Saves nothing.

    Only runs while a goal or a step is still missing, and only when the coach's last message is there to be agreed to.
    """
    _for(user_id)
    goals, steps = db.active_goals(user_id), db.open_objectives(user_id)
    if (goals and steps) or user_text.startswith("/") or len(user_text.split()) > AGREEMENT_MAX_WORDS:
        return None  # a long message is something new they are telling the coach, not a yes to what it proposed
    last = next((m["content"] for m in reversed(db.recent_messages(user_id, 6)) if m["role"] == "assistant"), None)
    if not last:
        return None
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
        return None
    return found if isinstance(found, dict) else None


async def record_agreements(user_id: int, user_text: str, found: Any = _UNREAD) -> list[str]:
    """Before the coach answers: if the user just agreed to a goal or a step the coach proposed, save it.

    Saving is not left to the coach's own tool call, which the model skips about half the time. A separate small call reads the one
    exchange (`read_agreements`) and the code writes what was agreed. Returns notes for the coach about what was saved.
    """
    if found is _UNREAD:
        found = await read_agreements(user_id, user_text)
    if not found:
        return []
    goals, steps = db.active_goals(user_id), db.open_objectives(user_id)
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


SORT_MIN_WORDS = 3  # "ok", "yes please" and the like have nothing to file
SORT_COOLDOWN = 60  # seconds between planner passes for one user: a burst is sorted by one run at its end (it reads the latest messages)
_last_sort: dict[int, float] = {}
_covered: set[int] = set()  # users whose latest message the triage call has already filed from (the background sort then skips it)
TRIAGE_TOKENS = 2500


def _words(text: str) -> int:
    return len(text.split())


def _wants_agreements(user_id: int, text: str) -> bool:
    """Is there anything for the agreement reading to do: a goal or step still missing, a short reply, something of the coach's to agree to."""
    if text.startswith("/") or _words(text) > AGREEMENT_MAX_WORDS or (db.active_goals(user_id) and db.open_objectives(user_id)):
        return False
    return any(m["role"] == "assistant" for m in db.recent_messages(user_id, 6))


def _wants_planner(user_id: int, text: str) -> bool:
    """Is there anything for the planner filing to do: a message with enough words, not in a burst, after the user's first full pass."""
    user = db.get_user(user_id)
    return bool(
        user is not None
        and user["consent_at"]
        and (user["planner_sorted"] or 0) >= SORT_VERSION
        and _words(text) >= SORT_MIN_WORDS
        and time.monotonic() - _last_sort.get(user_id, -SORT_COOLDOWN) >= SORT_COOLDOWN
    )


async def _triage(user_id: int, text: str, agreements: bool, planner: bool) -> dict | None:
    """One model call for the safety check and, as they apply, the agreement reading and the planner filing. Writes nothing.

    Returns the parsed answer, or None when the call failed or answered badly.
    """
    _for(user_id)
    user = db.get_user(user_id)
    history = db.recent_messages(user_id, RECENT_MESSAGES)
    last_coach = max((i for i, m in enumerate(history) if m["role"] == "assistant"), default=-1)
    # the coach's last message is what the user may be agreeing to: it is shown nearly in full
    recent = "\n".join(f"{m['role']}: {m['content'][: 1200 if i == last_coach else 600]}" for i, m in enumerate(history)) or "(none)"
    template, schema, context = prompts.TRIAGE_HEAD + prompts.TRIAGE_CRISIS, ['"crisis": true or false'], ""
    if agreements:
        goals, steps = db.active_goals(user_id), db.open_objectives(user_id)
        context += (
            f"\nGoals already saved: {'; '.join(g['text'] for g in goals) or 'none'}\n"
            f"Open steps already saved: {'; '.join(o['text'] for o in steps) or 'none'}\n"
        )
        template += prompts.TRIAGE_AGREEMENT
        schema.append('"goal": ..., "step": ...')
    if planner:
        projects, todos, events = _planner_snapshot(user_id)
        context += (
            "\nAlready in their planner (never file any of this again, not even in other words):\n"
            f"Projects: {projects}\nTo-dos: {todos}\nAppointments: {events}\n"
        )
        template += prompts.TRIAGE_PLANNER
        schema.append('"tasks": [...], "projects": [...], "events": [...]')
    prompt = template.format(
        now=datetime.now(tools._local_zone(user_id)).strftime("%A %Y-%m-%d %H:%M") + f" ({user['tz']})",
        recent=recent,
        user=text[:600],
        context=context,
        schema="{" + ", ".join(schema) + "}",
        language=prompts.LANGUAGE_NAME.get(user["lang"], "English"),
        limits="4 to-dos, 2 projects and 3 appointments",
        first_pass_rules="",
    )
    try:
        out = await _complete([{"role": "user", "content": prompt}], max_tokens=TRIAGE_TOKENS)
        found = json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0))  # type: ignore[union-attr]
    except Exception:
        log.warning("could not read the triage answer for %s, falling back to the separate checks", user_id, exc_info=True)
        return None
    return found if isinstance(found, dict) else None


async def precheck(user_id: int, text: str) -> bool:
    """Before the coach answers: the safety check, and with it (in the same model call) whatever else applies to this message.

    A message that needs only the safety check uses the single-purpose safety prompt. Otherwise one call also reads whether the user
    agreed to a goal or step, and files the to-dos, projects and appointments in it. Returns whether the message shows a crisis; then
    nothing is saved or filed. When the call fails it says no (the word list still applies) and the reply and the background sort do
    their own work, as before (and the safety check is asked again on its own).
    """
    _for(user_id)
    _preread.pop(user_id, None)
    _covered.discard(user_id)
    agreements, planner = _wants_agreements(user_id, text), _wants_planner(user_id, text)
    if not (agreements or planner):
        return await signals_crisis(user_id, text)
    found = await _triage(user_id, text, agreements, planner)
    if found is None:
        return await signals_crisis(user_id, text)  # a failed merged call must not leave the message unchecked: ask the safety prompt alone
    if found.get("crisis") is True:
        return True
    if agreements:
        _preread[user_id] = (text, {"goal": found.get("goal"), "step": found.get("step")})
    if planner:
        _last_sort[user_id] = time.monotonic()
        filed = _file(user_id, found, 4, 2, 3)
        sizes = {k: len(found[k]) if isinstance(found.get(k), list) else 0 for k in ("tasks", "projects", "events")}
        log.info("planner sort for %s (with the safety check): the model found %s, filed %d", user_id, sizes, filed)
        _covered.add(user_id)
    return False


def planner_covered(user_id: int) -> bool:
    """Did the triage call already file from the message just answered? (Read once: it is forgotten.)"""
    covered = user_id in _covered
    _covered.discard(user_id)
    return covered


async def signals_crisis(user_id: int, text: str) -> bool:
    """Does the message show a crisis (suicide, self-harm, abuse, danger), in any language or wording?

    The word list in crisis.py catches the plain cases without a model; this reads what a list cannot (other languages, indirect or
    slang wording). A call that fails or answers badly says no: the list is still there, and a coaching reply is never blocked by it.
    """
    _for(user_id)
    recent = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in db.recent_messages(user_id, 4)) or "(none)"
    prompt = prompts.CRISIS_CHECK.format(recent=recent, user=text[:600])
    try:
        out = await _complete([{"role": "user", "content": prompt}], max_tokens=SHORT_JOB_TOKENS)
        return bool(json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0)).get("crisis") is True)  # type: ignore[union-attr]
    except Exception:
        log.warning("could not read the safety check, relying on the word list")
        return False


_voice_client = httpx.AsyncClient(timeout=LLM_TIMEOUT)


async def transcribe(user_id: int, audio: bytes, mime: str = "audio/ogg") -> str:
    """A voice note as text, from Gemini's own API (the chat-completions endpoint refuses audio). Nothing of the audio is kept.

    Empty when no words could be made out; raises ModelError when the call fails.
    """
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompts.TRANSCRIBE}, {"inlineData": {"mimeType": mime, "data": base64.b64encode(audio).decode()}}],
            }
        ],
        "generationConfig": {"maxOutputTokens": VOICE_TOKENS},
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", **_headers(LLM_BASE_URL, user_id)}
    problem = "no answer"
    for attempt in range(MODEL_ATTEMPTS):
        try:
            async with _slots:
                resp = await _voice_client.post(VOICE_URL.format(model=VOICE_MODEL), json=body, headers=headers)
            resp.raise_for_status()
            parts = resp.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            return "" if text.lower().strip("[]. ") in ("unclear", "") else text
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:  # connection, status, or an answer without text
            problem = f"{type(error).__name__}: {error}"[:200]
        log.warning("voice model %s failed (%s)%s", VOICE_MODEL, problem, ", retrying" if attempt + 1 < MODEL_ATTEMPTS else "")
        if attempt + 1 < MODEL_ATTEMPTS:
            await asyncio.sleep(1)
    raise ModelError(f"{VOICE_MODEL}: {problem}")


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
        out = await _complete(
            [{"role": "user", "content": prompt}], max_tokens=VERIFY_TOKENS, model=LLM_VERIFIER_MODEL, effort=LLM_HELPER_EFFORT
        )
        if not out:
            # a reasoning model sometimes spends the whole budget thinking and answers with nothing: ask once more, without the thinking
            # where the model allows that (asking again the same way just spent the same budget twice)
            out = await _complete(
                [{"role": "user", "content": prompt}],
                max_tokens=VERIFY_RETRY_TOKENS if LLM_VERIFIER_MODEL.startswith("deepseek-") else VERIFY_TOKENS,
                model=LLM_VERIFIER_MODEL,
                effort="none" if LLM_VERIFIER_MODEL.startswith("deepseek-") else None,
            )
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
    _for(user_id)
    stat: dict[str, Any] = {"outcome": "sent", "flags": [], "tools": [], "rounds": 0}
    started, began = time.monotonic(), time.time()
    try:
        out = await _reply(user_id, user_text, instruction, stat)
        db.mark_auto_announced(user_id, began)  # the coach's notes listed what was filed before this reply began
        return out
    except Exception:
        stat["outcome"] = "dropped" if not user_text else "error"
        raise
    finally:
        _record_stat(user_id, user_text, stat, started)


async def _reply(user_id: int, user_text: str, instruction: str | None, stat: dict[str, Any]) -> str:
    pre = _preread.pop(user_id, None)
    found = pre[1] if pre and pre[0] == user_text else _UNREAD
    recorded = await record_agreements(user_id, user_text, found) if user_text and not instruction else []
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
    _for(user_id)
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


_sorting: dict[int, asyncio.Lock] = {}  # one planner pass at a time per user, so two quick messages cannot file the same thing twice
BACKFILL_MESSAGES = 300  # the first pass reads this many of the latest messages (a month of chat is a few thousand words)
SORT_VERSION = 2  # raised when the first pass improves: everyone below it gets one more full pass (what is already filed is skipped)
RECENT_MESSAGES = 6


def _planner_snapshot(user_id: int) -> tuple[str, str, str]:
    """What is already in the planner, as short lines for the sorter: (projects, to-dos, appointments)."""
    zone = tools._local_zone(user_id)
    projects = "; ".join(f"#{p['id']} {p['title']}" for p in db.active_projects(user_id)) or "none"
    todos = "; ".join(
        f"#{t['id']} {t['text']}" + (f" [project #{t['project_id']}]" if t["project_id"] else "") for t in db.open_tasks(user_id)
    )
    events = "; ".join(
        f"{e['title']} {datetime.fromtimestamp(e['start'], zone):%Y-%m-%d %H:%M}"
        for e in db.events_between(user_id, time.time() - 86400, time.time() + 90 * 86400)
    )
    return projects, todos or "none", events or "none"


def _backfill_background(user_id: int) -> str:
    """Everything else the bot knows about someone, for the one-time first pass over what they have already told it."""
    lines = []
    if notes := db.get_notes(user_id):
        lines.append(f"Long-term notes about them:\n{notes}")
    if goals := db.active_goals(user_id):
        lines.append("Their coaching goals (already stored; context only): " + "; ".join(g["text"] for g in goals))
    if parked := db.open_parked(user_id):
        lines.append("Ideas they set aside (do NOT file these): " + "; ".join(p["text"] for p in parked))
    return "\n".join(lines) + "\n\n" if lines else ""


async def sort_into_planner(user_id: int) -> int:
    """After a reply: file what the user said they need to do (to-dos, projects, appointments) that the coach has not saved itself.

    The coach saves these with its own tools, but skips them often, and someone who has told the bot about their life for weeks has
    nothing in the planner. A small separate call reads the latest exchange (the first time for a user: everything the bot still has
    from them) and returns what to file; the code writes it through the same checked tools the coach uses, marked as filed by itself
    so the coach tells the user once. Returns how many things were filed.
    """
    _for(user_id)
    user = db.get_user(user_id)
    if user is None or not user["consent_at"]:
        return 0
    lock = _sorting.setdefault(user_id, asyncio.Lock())
    async with lock:
        first_pass = (user["planner_sorted"] or 0) < SORT_VERSION
        history = db.recent_messages(user_id, BACKFILL_MESSAGES if first_pass else RECENT_MESSAGES)
        if not any(m["role"] == "user" for m in history):
            return 0
        projects, todos, events = _planner_snapshot(user_id)
        zone = tools._local_zone(user_id)
        prompt = prompts.SORTER.format(
            now=datetime.now(zone).strftime("%A %Y-%m-%d %H:%M") + f" ({user['tz']})",
            mode=(
                "This is the first pass over everything they have told you so far: file what is still open."
                if first_pass
                else "This is the latest exchange: file only what is new in it."
            ),
            projects=projects,
            tasks=todos,
            events=events,
            background=_backfill_background(user_id) if first_pass else "",
            convo="\n".join(f"{m['role']}: {m['content'][: 300 if m['role'] == 'assistant' and first_pass else 600]}" for m in history),
            language=prompts.LANGUAGE_NAME.get(user["lang"], "English"),
            first_pass_rules=prompts.SORTER_FIRST_PASS if first_pass else "",
            limits="10 to-dos, 4 projects and 6 appointments" if first_pass else "4 to-dos, 2 projects and 3 appointments",
        )
        try:
            # the first pass is a long chat and a hard judgement (which to-dos are one project); the fast side model missed projects the
            # main model found on the same chat, and it runs once per user, so it uses the main model
            out = await _complete(
                [{"role": "user", "content": prompt}],
                max_tokens=FIRST_PASS_TOKENS if first_pass else SHORT_JOB_TOKENS,
                model=LLM_MODEL if first_pass else None,
            )
            found = json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0))  # type: ignore[union-attr]
        except Exception:
            log.warning("could not read the planner sort for %s, trying again after the next message", user_id)
            return 0
        if first_pass:
            db.set_field(user_id, "planner_sorted", SORT_VERSION)
        filed = _file(user_id, found, 10 if first_pass else 4, 4 if first_pass else 2, 6 if first_pass else 3)
        sizes = {
            k: len(found.get(k) or []) if isinstance(found, dict) and isinstance(found.get(k), list) else 0
            for k in ("tasks", "projects", "events")
        }
        log.info(
            "planner sort for %s (%s): the model found %s, filed %d",
            user_id,
            "first pass" if first_pass else "latest exchange",
            sizes,
            filed,
        )
        return filed


def _file(user_id: int, found: dict, max_tasks: int, max_projects: int, max_events: int) -> int:
    """Write what the sorter found through the coach's own tools (so duplicates, caps and bad days are refused the same way)."""

    def items(key: str, limit: int) -> list[dict]:
        raw = found.get(key) if isinstance(found, dict) else None
        return [i for i in raw if isinstance(i, dict)][:limit] if isinstance(raw, list) else []

    token = db.filing_by_itself.set(True)
    filed = 0
    try:
        for p in items("projects", max_projects):
            args = {"title": p.get("title"), "steps": p.get("steps") or [], "task_ids": p.get("task_ids") or []}
            filed += tools.run_tool(user_id, "add_project", json.dumps(args)).startswith("ok: project #")
        for t in items("tasks", max_tasks):
            args = {"text": t.get("text"), "important": bool(t.get("important"))}
            if t.get("due"):
                args["due"] = t["due"]
            if isinstance(t.get("project_id"), int) and t["project_id"] > 0:
                args["project_id"] = t["project_id"]
            filed += tools.run_tool(user_id, "add_task", json.dumps(args)).startswith("ok: to-do #")
        for e in items("events", max_events):
            filed += tools.run_tool(user_id, "add_event", json.dumps({"title": e.get("title"), "at": e.get("at")})).startswith(
                "ok: event #"
            )
    finally:
        db.filing_by_itself.reset(token)
    if filed:
        log.info("filed %d things in the planner of %s by itself", filed, user_id)
    return filed


async def group_into_projects(user_id: int) -> int:
    """Put loose to-dos that are about the same thing into a project (or into one that exists), on the main model.

    The per-message pass runs on the fast side model, which does not reliably see that three to-dos are one project, and nothing ever
    looked at the loose to-dos together later. This small call does, whenever the set of loose to-dos has changed since it last looked.
    Returns how many projects were made or filled.
    """
    _for(user_id)
    user = db.get_user(user_id)
    if user is None or not user["consent_at"]:
        return 0
    async with _sorting.setdefault(user_id, asyncio.Lock()):

        def loose() -> list:
            return [t for t in db.open_tasks(user_id) if not t["project_id"] and not t["parent_id"]]

        now = loose()
        if len(now) < 2 or ",".join(str(t["id"]) for t in now) == (user["grouped_ids"] or ""):
            return 0
        projects = db.active_projects(user_id)
        prompt = prompts.GROUPER.format(
            loose="\n".join(f"#{t['id']} {t['text']}" for t in now),
            projects="; ".join(f"#{p['id']} {p['title']}" for p in projects) or "none",
            goals="; ".join(g["text"] for g in db.active_goals(user_id)) or "none",
            notes=f"Notes about them: {db.get_notes(user_id)[:800]}\n" if db.get_notes(user_id) else "",
            language=prompts.LANGUAGE_NAME.get(user["lang"], "English"),
        )
        try:
            out = await _complete([{"role": "user", "content": prompt}], max_tokens=FIRST_PASS_TOKENS, model=LLM_MODEL)
            found = json.loads(re.search(r"\{.*\}", out, re.DOTALL).group(0))  # type: ignore[union-attr]
        except Exception:
            log.warning("could not read the project grouping for %s, trying again later", user_id)
            return 0
        made = _group(user_id, found, {t["id"] for t in now}, {p["id"] for p in projects})
        db.set_field(user_id, "grouped_ids", ",".join(str(t["id"]) for t in loose()))  # what is still loose is not asked about again
        log.info("project grouping for %s: %d loose to-dos, %d projects made or filled", user_id, len(now), made)
        return made


def _group(user_id: int, found: dict, loose_ids: set[int], project_ids: set[int]) -> int:
    def ids(item: object) -> list[int]:
        raw = item.get("task_ids") if isinstance(item, dict) else None
        return [i for i in raw if isinstance(i, int) and i in loose_ids] if isinstance(raw, list) else []

    made = 0
    token = db.filing_by_itself.set(True)  # a project the sorter makes is announced to the user once
    try:
        for item in (found.get("new_projects") if isinstance(found, dict) else None) or []:
            title = item.get("title") if isinstance(item, dict) else None
            wanted = [i for i in ids(item) if i in loose_ids]
            if title and len(set(wanted)) >= 2:
                result = tools.run_tool(user_id, "add_project", json.dumps({"title": title, "steps": [], "task_ids": wanted}))
                if result.startswith("ok: project #"):
                    made += 1
                    loose_ids -= set(wanted)
        for item in (found.get("add_to") if isinstance(found, dict) else None) or []:
            pid = item.get("project_id") if isinstance(item, dict) else None
            if isinstance(pid, int) and pid in project_ids:
                for tid in ids(item):
                    if tid in loose_ids and db.update_task(user_id, tid, project_id=pid):
                        loose_ids.discard(tid)
                        made += 1
    finally:
        db.filing_by_itself.reset(token)
    return made


async def refresh_planner(user_id: int) -> None:
    """Bring the planner up to date before it is shown (`/projects`, `/tasks`): the pass for someone not yet read, then the grouping."""
    user = db.get_user(user_id)
    if user is None or not user["consent_at"]:
        return
    if (user["planner_sorted"] or 0) < SORT_VERSION:
        await sort_into_planner(user_id)
    await group_into_projects(user_id)
