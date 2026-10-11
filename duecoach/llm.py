import asyncio
import difflib
import logging
import re
import time
from typing import Any

import openai
from openai import AsyncOpenAI

from . import db, playbook, prompts, strings, tools
from .config import HISTORY_TURNS, LLM_API_KEY, LLM_BASE_URL, LLM_CONCURRENCY, LLM_MODEL, LLM_TIMEOUT

log = logging.getLogger("duecoach.llm")

MAX_TOOL_ROUNDS = 4
MAX_EMPTY_RETRIES = 2
REPLY_TOKENS = 3000  # reasoning models spend part of this thinking; too small a limit cuts the answer off mid-sentence
MAX_CONSECUTIVE_ASSISTANT = 2  # in the history sent to the model: proactive check-ins pile up while a user is silent
MODEL_ATTEMPTS = 2  # tries per call: a provider hiccup is retried once
NOTES_EVERY = 20  # new chat messages between refreshes of the long-term notes
# Reasoning models: they think before they answer, and the thinking counts against max_tokens. A small limit
# is used up by the thinking and the answer comes back empty, so even one-sentence jobs get a generous budget.
SHORT_JOB_TOKENS = 2000

_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2)
# MiniMax puts its reasoning inside the reply as <think>...</think> unless asked to split it off; the regex is a second safety net.
_EXTRA: dict[str, Any] = {"extra_body": {"reasoning_split": True}} if LLM_MODEL.startswith("MiniMax-") else {}
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_slots = asyncio.Semaphore(LLM_CONCURRENCY)  # one slow or rate-limited provider must not be hammered by every user at once


def _system(user_id: int) -> str:
    notes = db.get_notes(user_id)
    lang = db.get_user(user_id)["lang"]
    return (
        prompts.SYSTEM
        + "\n"
        + prompts.LANGUAGE.get(lang, prompts.LANGUAGE["en"])
        + (f"\n\nWhat you know about the user:\n{notes}" if notes else "")
        + "\n\nCoaching playbook (techniques to apply in your own words; use the one that fits what they need right now. The cards are in "
        "no order of priority: don't default to the first one, and don't use the same technique as in your last reply):\n\n"
        + playbook.full_text()
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
_MARKDOWN = [
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


def _leaked_call(text: str) -> bool:
    return bool(_LEAKED_CALL.search(text))


def _unfit_checkin(text: str, instruction: str | None) -> bool:
    """A check-in must coach: not a few words with nothing to answer, and not a goodnight at the wrong time."""
    if len(text.split()) < 6 and not text.rstrip().endswith(_QUESTION_ENDINGS):
        return True
    return bool(_SIGN_OFF.search(text)) and instruction not in (prompts.EVENING, prompts.REENGAGE_LAST)


class ModelError(RuntimeError):
    """The model produced no completion (outage, rate limit, timeout), with what it reported."""


def status() -> dict:
    """For /healthz: which model is in use."""
    return {"model": LLM_MODEL}


async def _chat(messages: Any, tool_defs: list[dict] | None = None, max_tokens: int = 1500):
    kwargs: dict[str, Any] = {"tools": tool_defs} if tool_defs else {}
    problem = "no completion"
    for attempt in range(MODEL_ATTEMPTS):
        try:
            async with _slots:
                resp = await _client.chat.completions.create(model=LLM_MODEL, messages=messages, max_tokens=max_tokens, **kwargs, **_EXTRA)
        except openai.OpenAIError as error:  # connection, timeout, rate limit, server error, or a request the model rejects
            problem = f"{type(error).__name__}: {error}"[:200]
        else:
            if resp.choices:
                choice = resp.choices[0]
                choice.message.finish_reason = getattr(choice, "finish_reason", None)  # "length": the answer was cut off
                if choice.message.content and "<think>" in choice.message.content:  # reasoning must never reach the user
                    choice.message.content = _THINK.sub("", choice.message.content).strip()
                return choice.message
            # some providers answer HTTP 200 with {"error": {...}} when the service behind them is down
            error_body = getattr(resp, "error", None) or (getattr(resp, "model_extra", None) or {}).get("error")
            problem = str(error_body or "empty response")[:200]
        log.warning("model %s failed (%s)%s", LLM_MODEL, problem, ", retrying" if attempt + 1 < MODEL_ATTEMPTS else "")
        if attempt + 1 < MODEL_ATTEMPTS:
            await asyncio.sleep(1)
    raise ModelError(f"{LLM_MODEL}: {problem}")


async def _complete(messages: list[dict], max_tokens: int = 1500) -> str:
    return ((await _chat(messages, max_tokens=max_tokens)).content or "").strip()


def _since(last_inbound: float | None) -> str:
    """'The user last wrote 3 hours ago, so anything they asked for then is done or over.' (empty if they never wrote)."""
    if not last_inbound:
        return ""
    minutes = int((time.time() - last_inbound) // 60)
    ago = f"{minutes} minutes" if minutes < 90 else f"{minutes // 60} hours" if minutes < 2880 else f"{minutes // 1440} days"
    return f"The user last wrote {ago} ago, so anything they asked for in that message (a timer, a reminder) is already done or over."


async def reply(user_id: int, user_text: str, instruction: str | None = None) -> str:
    """Chat with history, in one model call: the whole playbook is in the system prompt, so no lookup round is needed.

    `instruction` is a hidden steering note for this turn (e.g. a /stuck command or a check-in's purpose).
    """
    if user_text:
        db.add_message(user_id, "user", user_text)
    messages = [
        {"role": "system", "content": _system(user_id)},
        *_history(user_id),
        {"role": "system", "content": prompts.TOOL_GUARD if user_text else prompts.PROACTIVE_GUARD},
    ]
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
    text, empty_retries, results, wrong_times, repeats = "", 0, [], [], []
    for _ in range(MAX_TOOL_ROUNDS + MAX_EMPTY_RETRIES):
        msg = await _chat(messages, tool_defs, max_tokens=REPLY_TOKENS)
        text = (msg.content or "").strip()
        truncated = getattr(msg, "finish_reason", None) == "length"
        if not msg.tool_calls:
            if text:
                text, wrong_times = _fix_reminder_times(text, results, user_text)
            repeats = _repeated(text, user_id) if text else []
            unfit = bool(text) and (
                bool(wrong_times) or bool(repeats) or _leaked_call(text) or (not user_text and _unfit_checkin(text, instruction))
            )
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
            # the model sometimes returns only hidden reasoning, stops mid-sentence, or (check-in) says goodbye: ask again
            empty_retries += 1
            log.warning(
                "%s model reply, retrying (%d)",
                "leaked tool call, wrong time, repeated sentence or unfit check-in" if unfit else "cut-off" if text else "empty",
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
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    if truncated and text:
        text = _trim_to_sentence(text)
    if text and (_leaked_call(text) or wrong_times):
        if user_text:
            text = ""  # the apology below; a tool call written out is never shown to the user
        else:
            raise ModelError("the check-in contained a tool call written as text or a wrong time")
    if not user_text and text and _unfit_checkin(text, instruction):
        raise ModelError("the check-in was a goodbye or too short to coach")  # nothing is sent; the caller backs off and tries later
    if text and repeats:
        text = _without(text, repeats)  # still repeating after the retries: say it without the repeated sentences
    text = _plain(text)
    if not text:
        # Say so honestly, and keep it out of the history: stored as the coach's own words it makes the model imitate it.
        if not user_text:
            raise ModelError("the check-in came back empty")  # never send the apology as a check-in
        return strings.t(db.get_user(user_id)["lang"], "EMPTY_REPLY")
    db.add_message(user_id, "assistant", text)
    return text


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
