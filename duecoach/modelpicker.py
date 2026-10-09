"""Pick the models to use automatically: probe the gateway's free models now and then, keep the fastest ones that do the job.

A candidate must be free (an explicit ":free" id, so auto-routers that hand each request to some other model are left out),
support tool calling, and have a large enough context. It is then asked for a short reply in Greek (the hardest language we
serve: weak models mix in other alphabets) and for a daily reminder through the tool. Only models that get both right are kept,
ranked by how fast the reply came. If the check finds nothing, the current list stays as it is.
"""

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass

import httpx

from . import llm
from .config import (
    DB_PATH,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MIN_CONTEXT,
    LLM_MODEL_DENYLIST,
    LLM_POOL_SIZE,
    LLM_REFRESH_HOURS,
)

log = logging.getLogger("duecoach.modelpicker")

MAX_PROBES = 10  # candidates tried per check: every probe spends some of the free daily quota
PROBE_TIMEOUT = 45.0
PROBE_CONCURRENCY = 2  # separate from the chat slots, so a check never makes a user wait
KEEP_PRIMARY_BONUS = 0.75  # the current first choice is kept unless another is more than 25% faster (no flip-flopping)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "models.json")

SYSTEM = (
    "You are Duecoach, an ADHD coach. Chat style: 2-4 short sentences, plain text, at most one question. "
    "Reply in natural, conversational Modern Greek. Now: Thursday 2026-10-08 20:05 local time (Europe/Athens)."
)
CHAT = "Θέλω να οργανώσουμε τη μέρα μου. Ξυπνάω, πίνω καφέ, παίρνω τα φάρμακα και πάω για δουλειά."
REMIND = "Βάλε μου υπενθύμιση για τα φάρμακα κάθε μέρα στις 9 το πρωί."
TOOL = {
    "type": "function",
    "function": {
        "name": "set_reminder",
        "description": "Send the user a message later. Give `at` (local 'YYYY-MM-DD HH:MM') or `minutes`. daily=true repeats every day.",
        "parameters": {
            "type": "object",
            "properties": {
                "at": {"type": "string"},
                "minutes": {"type": "number"},
                "message": {"type": "string"},
                "daily": {"type": "boolean"},
            },
            "required": ["message"],
        },
    },
}


@dataclass
class Probe:
    model: str
    ok: bool
    seconds: float = 0.0
    why: str = ""


def eligible(entry: dict) -> bool:
    """Whether a gateway model entry may be probed: free, text, tool calling, enough context, not on the denylist."""
    model = str(entry.get("id") or "")
    if not model.endswith(":free") or re.search(LLM_MODEL_DENYLIST, model, re.I):
        return False
    if "tools" not in (entry.get("supported_parameters") or []):
        return False
    if (entry.get("context_length") or 0) < LLM_MIN_CONTEXT:
        return False
    out = (entry.get("architecture") or {}).get("output_modalities")
    return out is None or out == ["text"]


def clean_greek(text: str) -> bool:
    """A Greek reply without the failures weak models show: other alphabets, or Latin and Greek letters mixed inside a word.

    (A whole word in Latin letters is allowed: a brand name can't be told from a slip without a dictionary.)
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    scripts = [unicodedata.name(c, "").split(" ")[0] for c in letters]
    if any(s not in ("GREEK", "LATIN") for s in scripts):
        return False
    if scripts.count("GREEK") < 0.6 * len(letters):
        return False
    for word in re.findall(r"\w+", text):
        kinds = {unicodedata.name(c, "").split(" ")[0] for c in word if c.isalpha()}
        if {"GREEK", "LATIN"} <= kinds:
            return False
    return True


def good_reminder(message) -> bool:
    """The model called set_reminder with daily=true and a local time of 09:00."""
    for call in message.tool_calls or []:
        if call.function.name != "set_reminder":
            continue
        try:
            args = json.loads(call.function.arguments or "{}")
        except ValueError:
            return False
        return bool(args.get("daily")) and str(args.get("at", "")).strip().endswith("09:00")
    return False


async def probe(model: str, gate: asyncio.Semaphore) -> Probe:
    """One Greek chat reply and one reminder tool call; both must be right."""
    async with gate:
        started = time.monotonic()
        try:
            chat = await llm._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": CHAT}],
                max_tokens=3000,
                timeout=PROBE_TIMEOUT,
            )
            seconds = time.monotonic() - started
            if not chat.choices:
                return Probe(model, False, why=str(getattr(chat, "error", None) or "no choices")[:80])
            choice = chat.choices[0]
            text = (choice.message.content or "").strip()
            if not text or choice.finish_reason != "stop":
                return Probe(model, False, seconds, "empty or cut off")
            if not clean_greek(text):
                return Probe(model, False, seconds, "unclean Greek")
            call = await llm._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": REMIND}],
                tools=[TOOL],
                max_tokens=3000,
                timeout=PROBE_TIMEOUT,
            )
            if not call.choices or not good_reminder(call.choices[0].message):
                return Probe(model, False, seconds, "wrong tool call")
            return Probe(model, True, seconds)
        except Exception as error:  # rate limits, timeouts, provider errors: this model is not usable right now
            return Probe(model, False, time.monotonic() - started, f"{type(error).__name__}: {error}"[:80])


async def fetch_candidates() -> list[str]:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(f"{LLM_BASE_URL.rstrip('/')}/models", headers={"Authorization": f"Bearer {LLM_API_KEY}"})
        resp.raise_for_status()
        entries = resp.json().get("data", [])
    return [e["id"] for e in entries if eligible(e)]


def rank(results: list[Probe], previous_first: str | None = None) -> list[str]:
    """Passing models, fastest first (the previous first choice gets a head start so the order doesn't flip on noise)."""

    def score(p: Probe) -> float:
        return p.seconds * (KEEP_PRIMARY_BONUS if p.model == previous_first else 1)

    return [p.model for p in sorted((p for p in results if p.ok), key=score)]


async def check() -> list[Probe]:
    """Probe the candidates (those in use now first, then the rest) and return every result."""
    candidates = await fetch_candidates()
    current = [m for m in llm.MODELS if m in candidates]
    order = [*current, *[m for m in candidates if m not in current]][:MAX_PROBES]
    gate = asyncio.Semaphore(PROBE_CONCURRENCY)
    return list(await asyncio.gather(*[probe(m, gate) for m in order]))


def save(picked: list[str], results: list[Probe]) -> None:
    state = {"checked": time.time(), "models": picked, "results": {p.model: [p.ok, round(p.seconds, 1), p.why] for p in results}}
    try:
        with open(STATE_FILE + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except OSError:
        log.exception("could not save the picked models")


def load() -> dict:
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        return state if isinstance(state.get("models"), list) else {}
    except (OSError, ValueError):
        return {}


def apply(picked: list[str], checked: float, results: list[Probe] | None = None) -> None:
    llm.set_models(picked)
    llm.auto_info.clear()
    llm.auto_info.update(
        checked=round(checked),
        picked=list(picked),
        failed={p.model: p.why for p in results or [] if not p.ok},
    )


async def refresh() -> list[str]:
    """Run one check and switch to the result. Returns the picked models ([] if none passed: the list is then left alone)."""
    results = await check()
    previous_first = llm.MODELS[0] if llm.MODELS else None
    picked = rank(results, previous_first)[:LLM_POOL_SIZE]
    log.info(
        "model check: %d probed, %d passed; using %s",
        len(results),
        sum(p.ok for p in results),
        ", ".join(picked) or "(unchanged)",
    )
    for p in results:
        if not p.ok:
            log.info("model check: %s failed (%s)", p.model, p.why)
    if picked:
        save(picked, results)
        apply(picked, time.time(), results)
    return picked


async def run() -> None:
    """Background loop: use the last saved result right away, and re-check when it is older than LLM_REFRESH_HOURS."""
    if LLM_REFRESH_HOURS <= 0:
        return
    interval = LLM_REFRESH_HOURS * 3600
    saved = load()
    if saved.get("models"):
        apply(saved["models"], saved.get("checked", 0))
    wait = max(60.0, saved.get("checked", 0) + interval - time.time()) if saved.get("models") else 60.0
    while True:
        await asyncio.sleep(wait)
        try:
            await refresh()
        except Exception:
            log.exception("model check failed; keeping the current models")
        wait = interval


if __name__ == "__main__":  # a dry run for operators: python -m duecoach.modelpicker
    logging.basicConfig(level=logging.INFO)

    async def dry_run() -> None:
        results = await check()
        for p in sorted(results, key=lambda p: (not p.ok, p.seconds)):
            print(f"{'PASS' if p.ok else 'fail'} {p.seconds:5.1f}s {p.model} {p.why}")
        print("would use:", ", ".join(rank(results)[:LLM_POOL_SIZE]) or "(nothing; list unchanged)")

    asyncio.run(dry_run())
