"""Spoken replies: Google Cloud Text-to-Speech (a plain Standard voice, the kind navigation apps use) turns the coach's text into
an Ogg Opus voice note, the format Telegram plays as a voice message. Only sent to someone who sent a voice note themselves."""

import base64
import logging
import re

import httpx

from .config import GOOGLE_TTS_API_KEY, LLM_TIMEOUT, TTS_MAX_CHARS, TTS_VOICES

log = logging.getLogger("duecoach.tts")
URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_client = httpx.AsyncClient(timeout=LLM_TIMEOUT)

_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_`#>~|]+")
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️‍]")
_ID = re.compile(r"\s#\d+\b")
_SENTENCE_END = re.compile(r"[.!?;;…]+(?:\s|$)|\n")


_GREEK = re.compile("[\u0370-\u03ff\u1f00-\u1fff]")
_LATIN = re.compile("[A-Za-z]")


def language_of(text: str, fallback: str = "en") -> str:
    """The voice language for this text, judged from its letters: the stored language setting says what the user chose, not what the
    reply is written in (a Greek speaker with the setting on English got Greek words read by the English voice)."""
    greek, latin = len(_GREEK.findall(text)), len(_LATIN.findall(text))
    if greek == latin:
        return fallback
    return "el" if greek > latin else "en"


def enabled() -> bool:
    return bool(GOOGLE_TTS_API_KEY)


def speakable(text: str, limit: int | None = None) -> str:
    """The reply as plain speech: no markup, links, emoji or ids, and at most `limit` characters ending at a sentence if it can."""
    limit = limit or TTS_MAX_CHARS
    text = _URL.sub("", text)
    text = _ID.sub("", text)  # before the markup: "#" is stripped there too
    text = _MARKUP.sub(" ", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"^\s*[•\-–]\s*", "", text, flags=re.MULTILINE)  # list bullets
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(cut)]
    if ends and ends[-1] >= limit // 3:
        return cut[: ends[-1]].strip()
    return cut.rsplit(" ", 1)[0].strip()  # one very long sentence: stop at a word


async def synthesize(text: str, lang: str) -> bytes | None:
    """The text as an Ogg Opus voice note, or None when speech is off, there is nothing to say, or the call failed."""
    text = speakable(text)
    if not enabled() or not text:
        return None
    code, name = TTS_VOICES.get(language_of(text, lang), TTS_VOICES["en"])
    body = {
        "input": {"text": text},
        "voice": {"languageCode": code, "name": name},
        "audioConfig": {"audioEncoding": "OGG_OPUS"},
    }
    try:
        resp = await _client.post(URL, json=body, headers={"X-Goog-Api-Key": GOOGLE_TTS_API_KEY})
        resp.raise_for_status()
        return base64.b64decode(resp.json()["audioContent"])
    except (httpx.HTTPError, KeyError, ValueError) as error:
        log.warning("text-to-speech failed: %s", f"{type(error).__name__}: {error}"[:200])
        return None
