import asyncio
import logging
import re
import time
from typing import Any

import openai
from openai import AsyncOpenAI

from . import db, playbook, prompts, strings, tools
from .config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN,
    HISTORY_TURNS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_BUDGET,
    LLM_CONCURRENCY,
    LLM_FALLBACK_MODELS,
    LLM_MODEL,
    LLM_MODEL_COOLDOWN,
    LLM_TIMEOUT,
)

log = logging.getLogger("duecoach.llm")

MAX_TOOL_ROUNDS = 4
MAX_EMPTY_RETRIES = 2
REPLY_TOKENS = 3000  # reasoning models spend part of this thinking; too small a limit cuts the answer off mid-sentence
MAX_CONSECUTIVE_ASSISTANT = 2  # in the history sent to the model: proactive check-ins pile up while a user is silent
MODEL_ATTEMPTS = 2  # tries per call when there is only one model to use
NOTES_EVERY = 20  # new chat messages between refreshes of the long-term notes
# Free routes are reasoning models: they think before they answer, and the thinking counts against max_tokens. A small limit
# is used up by the thinking and the answer comes back empty, so even one-sentence jobs get a generous budget.
SHORT_JOB_TOKENS = 2000

_client = AsyncOpenAI(api_key=LLM_API_KEY or "unused", base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2)
# Models named "@cf/..." run on Cloudflare Workers AI (its OpenAI-compatible endpoint); everything else on LLM_BASE_URL.
_cf_client = (
    AsyncOpenAI(
        api_key=CLOUDFLARE_API_TOKEN,
        base_url=f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        timeout=LLM_TIMEOUT,
        max_retries=2,
    )
    if CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID
    else None
)


def _client_for(model: str) -> AsyncOpenAI:
    return _cf_client if _cf_client is not None and model.startswith("@cf/") else _client


def _usable(model: str) -> bool:
    return _cf_client is not None or not model.startswith("@cf/")  # a Cloudflare model without Cloudflare credentials can't run


MODELS = [m for m in [LLM_MODEL, *[m for m in LLM_FALLBACK_MODELS if m != LLM_MODEL]] if _usable(m)]  # in order of preference
SEED_MODELS = list(MODELS)  # from the environment: used until the first automatic check, and as the last resort after it
auto_info: dict = {}  # the last automatic model check, for /healthz (set by modelpicker)
_down_until: dict[str, float] = {}  # model -> time before which it is skipped (it failed recently)
_slots = asyncio.Semaphore(LLM_CONCURRENCY)  # one slow or rate-limited gateway must not be hammered by every user at once


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
        out.append(msg)
        run = 0
        for m in reversed(out):
            if m["role"] != "assistant":
                break
            run += 1
        if run > MAX_CONSECUTIVE_ASSISTANT:
            del out[-run]  # drop the oldest message of the run
    return out


class ModelError(RuntimeError):
    """No model produced a completion (outages, rate limits, timeouts), with what each one reported."""


def _order() -> list[str]:
    """Models to try now: those not recently failed, best first. If every model is cooling down, try them all anyway."""
    now = time.time()
    return [m for m in MODELS if _down_until.get(m, 0) <= now] or list(MODELS)


def set_models(picked: list[str]) -> None:
    """Use `picked` first (in order), then the configured models as a last resort. Changes the shared list in place."""
    MODELS[:] = [*picked, *[m for m in SEED_MODELS if m not in picked]]


def status() -> dict:
    """For /healthz: which models are configured and which are cooling down after a failure (seconds left)."""
    now = time.time()
    return {
        "models": list(MODELS),
        "cooling_down": {m: round(t - now) for m, t in _down_until.items() if t > now},
        "auto": dict(auto_info),
    }


async def _chat(messages: Any, tool_defs: list[dict] | None = None, max_tokens: int = 1500):
    kwargs: dict[str, Any] = {"tools": tool_defs} if tool_defs else {}
    attempts = MODEL_ATTEMPTS if len(MODELS) == 1 else 1  # with fallbacks available, move on rather than retry the same model
    failures: list[str] = []
    started = time.time()
    for model in _order():
        if failures and time.time() - started > LLM_BUDGET:
            failures.append(f"not tried (over the {LLM_BUDGET:g}s budget): {model}")
            break
        problem = "no completion"
        for attempt in range(attempts):
            try:
                async with _slots:
                    resp = await _client_for(model).chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **kwargs)
            except openai.OpenAIError as error:  # connection, timeout, rate limit, server error, or a request this model rejects
                problem = f"{type(error).__name__}: {error}"[:200]
                rejected = isinstance(error, (openai.BadRequestError, openai.UnprocessableEntityError))
            else:
                if resp.choices:
                    _down_until.pop(model, None)
                    if model != MODELS[0]:
                        log.warning("answered by fallback model %s", model)
                    choice = resp.choices[0]
                    choice.message.finish_reason = getattr(choice, "finish_reason", None)  # "length": the answer was cut off
                    return choice.message
                # free routes answer HTTP 200 with {"error": {...}} when the provider behind them is down
                error_body = getattr(resp, "error", None) or (getattr(resp, "model_extra", None) or {}).get("error")
                problem, rejected = str(error_body or "empty response")[:200], False
            log.warning("model %s failed (%s)%s", model, problem, ", retrying" if attempt + 1 < attempts else "")
            if attempt + 1 < attempts:
                await asyncio.sleep(1)
        if not rejected:  # a model that merely rejected this one request is not down; don't skip it for the next user
            _down_until[model] = time.time() + LLM_MODEL_COOLDOWN
        failures.append(f"{model}: {problem}")
    raise ModelError("; ".join(failures))


async def _complete(messages: list[dict], max_tokens: int = 1500) -> str:
    return ((await _chat(messages, max_tokens=max_tokens)).content or "").strip()


def _tool_call(call) -> dict:
    """A tool call as sent back to the model. Gemini 3 attaches an `extra_content` (its thought signature) that must be returned."""
    out: dict[str, Any] = {
        "id": call.id,
        "type": "function",
        "function": {"name": call.function.name, "arguments": call.function.arguments},
    }
    extra = (getattr(call, "model_extra", None) or {}).get("extra_content")
    if extra:
        out["extra_content"] = extra
    return out


async def reply(user_id: int, user_text: str, instruction: str | None = None) -> str:
    """Chat with history, in one model call: the whole playbook is in the system prompt, so no lookup round is needed.

    `instruction` is a hidden steering note for this turn (e.g. a /stuck command or a check-in's purpose).
    """
    if user_text:
        db.add_message(user_id, "user", user_text)
    messages = [{"role": "system", "content": _system(user_id)}, *_history(user_id), {"role": "system", "content": prompts.TOOL_GUARD}]
    if instruction:
        messages.append({"role": "system", "content": instruction})
    text, empty_retries = "", 0
    for _ in range(MAX_TOOL_ROUNDS + MAX_EMPTY_RETRIES):
        msg = await _chat(messages, tools.TOOLS, max_tokens=REPLY_TOKENS)
        text = (msg.content or "").strip()
        truncated = getattr(msg, "finish_reason", None) == "length"
        if not msg.tool_calls:
            if (text and not truncated) or empty_retries >= MAX_EMPTY_RETRIES:
                break
            empty_retries += 1  # the free models sometimes return only hidden reasoning, or stop mid-sentence: ask again
            log.warning("%s model reply, retrying (%d)", "cut-off" if text else "empty", empty_retries)
            continue
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [_tool_call(c) for c in msg.tool_calls],
            }
        )
        for call in msg.tool_calls:
            result = tools.run_tool(user_id, call.function.name, call.function.arguments)
            # log the outcome, not the arguments: they contain what the user wrote about their life. A refusal is our own fixed
            # text, so it is logged in full: it is what explains why a call failed.
            log.info("tool %s -> %s", call.function.name, result[:120] if result.startswith("error") else result.split(":", 1)[0])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    if truncated and text:
        text = _trim_to_sentence(text)
    if not text:
        # Say so honestly, and keep it out of the history: stored as the coach's own words it makes the model imitate it.
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


async def suggest_goals(user_id: int) -> list[str]:
    """Three goal ideas drawn from what the user has said (interview answers, notes, recent messages); [] if the model can't."""
    profile, notes = db.get_profile(user_id), db.get_notes(user_id)
    said = [m["content"] for m in db.recent_messages(user_id, 12) if m["role"] == "user"][-6:]
    labels = {
        "why": "Why they came",
        "tried": "What they tried",
        "obstacle": "What gets in their way",
        "strength": "Strengths",
        "rhythm": "Their day",
    }
    parts = [f"{label}: {profile[key]}" for key, label in labels.items() if profile.get(key)]
    if notes:
        parts.append(f"Notes: {notes}")
    if said:
        parts.append("Recent messages: " + " | ".join(said))
    if not parts:
        return []
    language = prompts.LANGUAGE_NAME.get(db.get_user(user_id)["lang"], "English")
    system = prompts.SUGGEST_GOALS.format(language=language, material="\n".join(parts))
    out = await _complete(
        [{"role": "system", "content": system}, {"role": "user", "content": "Suggest the three goals."}], max_tokens=SHORT_JOB_TOKENS
    )
    lines = [re.sub(r"^\s*(?:\d+[.)]|[-*\u2022])\s*", "", line).strip().strip("\"'\u00ab\u00bb\u201c\u201d ") for line in out.splitlines()]
    lines = [line for line in lines if 10 <= len(line) <= 240]
    return lines[:3] if len(lines) >= 3 else []


async def draft(user_id: int, kind: str, text: str, previous: str | None = None, goal: str = "") -> str | None:
    """Wording help for the goal / weekly-step flows: the user's words as one well-formed sentence.

    Returns None if the model says the text isn't a goal or step at all (so the caller treats it as ordinary chat), and falls back
    to the user's own words when the model is unavailable or returns nothing, so the flow never gets stuck on the model.
    """
    language = prompts.LANGUAGE_NAME.get(db.get_user(user_id)["lang"], "English")
    template = {
        ("goal", False): prompts.DRAFT_GOAL,
        ("goal", True): prompts.DRAFT_GOAL_REVISION,
        ("objective", False): prompts.DRAFT_OBJECTIVE,
        ("objective", True): prompts.DRAFT_OBJECTIVE_REVISION,
    }[(kind, previous is not None)]
    system = template.format(language=language, goal=goal, previous=previous or "")
    fallback = " ".join(text.split())[:200]
    try:
        out = await _complete([{"role": "system", "content": system}, {"role": "user", "content": text}], max_tokens=SHORT_JOB_TOKENS)
    except Exception:
        log.exception("drafting failed, using the user's own words")
        return fallback
    out = out.strip().strip("\"'\u00ab\u00bb\u201c\u201d ")
    if out.upper().rstrip(".! ") == "NONE" and previous is None:
        return None
    return " ".join(out.split())[:240] or fallback
