import asyncio
import logging
import time

import openai
from openai import AsyncOpenAI

from . import db, playbook, prompts, strings, tools
from .config import (
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

log = logging.getLogger("coach.llm")

MAX_TOOL_ROUNDS = 4
MAX_EMPTY_RETRIES = 2
MODEL_ATTEMPTS = 2  # tries per call when there is only one model to use

MODELS = [LLM_MODEL, *[m for m in LLM_FALLBACK_MODELS if m != LLM_MODEL]]  # in order of preference
_down_until: dict[str, float] = {}  # model -> time before which it is skipped (it failed recently)
_cards_read: dict[int, list[str]] = {}  # user -> playbook cards read while writing their latest reply


def cards_read(user_id: int) -> set[str]:
    """Which playbook cards the coach read for this user's latest reply (tells the bot what kind of moment this was)."""
    return set(_cards_read.get(user_id, []))


MAX_CONSECUTIVE_ASSISTANT = 2  # in the history sent to the model: proactive check-ins pile up while a user is silent

READ_PLAYBOOK = {"type": "function", "function": {"name": "get_strategy"}}

_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT, max_retries=2)
_slots = asyncio.Semaphore(LLM_CONCURRENCY)  # one slow or rate-limited gateway must not be hammered by every user at once


def _system(user_id: int) -> str:
    notes = db.get_notes(user_id)
    lang = db.get_user(user_id)["lang"]
    return (
        prompts.SYSTEM
        + "\n"
        + prompts.LANGUAGE.get(lang, prompts.LANGUAGE["en"])
        + (f"\n\nWhat you know about the user:\n{notes}" if notes else "")
        + "\n\nPlaybook index (technique ids you can read with get_strategy):\n"
        + playbook.index_text()
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


async def _chat(messages: list[dict], tool_defs: list[dict] | None = None, max_tokens: int = 1500, force: dict | None = None):
    kwargs = {"tools": tool_defs} if tool_defs else {}
    if force:
        kwargs["tool_choice"] = force
    attempts = MODEL_ATTEMPTS if len(MODELS) == 1 else 1  # with fallbacks available, move on rather than retry the same model
    failures, started = [], time.time()
    for model in _order():
        if failures and time.time() - started > LLM_BUDGET:
            failures.append(f"not tried (over the {LLM_BUDGET:g}s budget): {model}")
            break
        problem = "no completion"
        for attempt in range(attempts):
            try:
                async with _slots:
                    resp = await _client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, **kwargs)
            except openai.OpenAIError as error:  # connection, timeout, rate limit, server error, or a request this model rejects
                problem = f"{type(error).__name__}: {error}"[:200]
                rejected = isinstance(error, (openai.BadRequestError, openai.UnprocessableEntityError))
            else:
                if resp.choices:
                    _down_until.pop(model, None)
                    if model != MODELS[0]:
                        log.warning("answered by fallback model %s", model)
                    return resp.choices[0].message
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


async def reply(user_id: int, user_text: str, instruction: str | None = None, coach: bool = True) -> str:
    """Chat with history. `instruction` is a hidden steering note for this turn (e.g. a /stuck command).

    With coach=True (the default) the model must read a playbook card before it answers, on every turn.
    """
    if user_text:
        db.add_message(user_id, "user", user_text)
    messages = [{"role": "system", "content": _system(user_id)}, *_history(user_id)]
    if instruction:
        messages.append({"role": "system", "content": instruction})
    text, empty_retries, force = "", 0, READ_PLAYBOOK if coach else None
    _cards_read[user_id] = []
    for _ in range(MAX_TOOL_ROUNDS + MAX_EMPTY_RETRIES):
        msg = await _chat(messages, tools.TOOLS, force=force)
        force = None  # only the first round is forced; after that the model answers or uses other tools
        text = (msg.content or "").strip()
        if not msg.tool_calls:
            if text or empty_retries >= MAX_EMPTY_RETRIES:
                break
            empty_retries += 1  # the free models sometimes return only hidden reasoning: ask again
            log.warning("empty model reply, retrying (%d)", empty_retries)
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
            if call.function.name == "get_strategy" and result.startswith(tuple(playbook.IDS)):
                _cards_read[user_id].append(result.split(" ", 1)[0])
            # log the outcome, not the arguments: they contain what the user wrote about their life
            log.info("tool %s -> %s", call.function.name, result.split(":", 1)[0])
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    text = text or strings.t(db.get_user(user_id)["lang"], "EMPTY_REPLY")
    db.add_message(user_id, "assistant", text)
    return text


async def refresh_notes(user_id: int) -> None:
    """Fold recent conversation into long-term notes every 20 messages."""
    if db.message_count(user_id) % 20 != 0:
        return
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in db.recent_messages(user_id, 40))
    prompt = prompts.SUMMARIZE.format(
        notes=db.get_notes(user_id) or "(none)", convo=convo, language=prompts.LANGUAGE_NAME.get(db.get_user(user_id)["lang"], "English")
    )
    notes = await _complete([{"role": "user", "content": prompt}], max_tokens=1200)
    if notes:
        db.set_field(user_id, "notes", notes)


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
        out = await _complete([{"role": "system", "content": system}, {"role": "user", "content": text}], max_tokens=400)
    except Exception:
        log.exception("drafting failed, using the user's own words")
        return fallback
    out = out.strip().strip("\"'\u00ab\u00bb\u201c\u201d ")
    if out.upper().rstrip(".! ") == "NONE" and previous is None:
        return None
    return " ".join(out.split())[:240] or fallback
