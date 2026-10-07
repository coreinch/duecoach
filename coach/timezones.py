"""Turn what a person types ("Athens", "I live in new york", "Greece", "Αθήνα") into an IANA timezone, offline.

Uses the tz database tables shipped with tzdata (zone.tab: country -> zones, iso3166.tab: country names) plus a small list of
well-known places the database doesn't name. The caller always shows the result back to the user before saving it.
"""

import importlib.resources
import os
import re
import unicodedata
import zoneinfo
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

# places people live in that aren't a zone name in the tz database (the database names one city per zone)
ALIASES = {
    "thessaloniki": "Europe/Athens",
    "salonica": "Europe/Athens",
    "patras": "Europe/Athens",
    "patra": "Europe/Athens",
    "heraklion": "Europe/Athens",
    "irakleio": "Europe/Athens",
    "larissa": "Europe/Athens",
    "volos": "Europe/Athens",
    "ioannina": "Europe/Athens",
    "chania": "Europe/Athens",
    "rhodes": "Europe/Athens",
    "kalamata": "Europe/Athens",
    "αθηνα": "Europe/Athens",
    "θεσσαλονικη": "Europe/Athens",
    "πατρα": "Europe/Athens",
    "ηρακλειο": "Europe/Athens",
    "λαρισα": "Europe/Athens",
    "βολος": "Europe/Athens",
    "ιωαννινα": "Europe/Athens",
    "χανια": "Europe/Athens",
    "ροδος": "Europe/Athens",
    "καλαματα": "Europe/Athens",
    "ελλαδα": "Europe/Athens",
    "κυπρος": "Asia/Nicosia",
    "λευκωσια": "Asia/Nicosia",
    "λεμεσος": "Asia/Nicosia",
    "limassol": "Asia/Nicosia",
    "larnaca": "Asia/Nicosia",
    "manchester": "Europe/London",
    "birmingham": "Europe/London",
    "glasgow": "Europe/London",
    "edinburgh": "Europe/London",
    "liverpool": "Europe/London",
    "leeds": "Europe/London",
    "bristol": "Europe/London",
    "uk": "Europe/London",
    "england": "Europe/London",
    "scotland": "Europe/London",
    "wales": "Europe/London",
    "munich": "Europe/Berlin",
    "hamburg": "Europe/Berlin",
    "frankfurt": "Europe/Berlin",
    "cologne": "Europe/Berlin",
    "barcelona": "Europe/Madrid",
    "valencia": "Europe/Madrid",
    "seville": "Europe/Madrid",
    "milan": "Europe/Rome",
    "naples": "Europe/Rome",
    "turin": "Europe/Rome",
    "lyon": "Europe/Paris",
    "marseille": "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",
    "rotterdam": "Europe/Amsterdam",
    "geneva": "Europe/Zurich",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "san diego": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "boston": "America/New_York",
    "washington": "America/New_York",
    "miami": "America/New_York",
    "atlanta": "America/New_York",
    "philadelphia": "America/New_York",
    "houston": "America/Chicago",
    "dallas": "America/Chicago",
    "austin": "America/Chicago",
    "minneapolis": "America/Chicago",
    "nyc": "America/New_York",
    "new york city": "America/New_York",
    "la": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "calgary": "America/Edmonton",
    "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "beijing": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "dubai": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai",
    "istanbul": "Europe/Istanbul",
    "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv",
    "sao paulo": "America/Sao_Paulo",
    "rio": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo",
}
COUNTRY_ALIASES = {
    "usa": "US",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "great britain": "GB",
    "britain": "GB",
    "ελλαδα": "GR",
    "greece": "GR",
    "uae": "AE",
    "holland": "NL",
    "russia": "RU",
    "south korea": "KR",
    "korea": "KR",
}
MAX_PHRASE = 3  # longest place name we try to recognise inside a short answer, in words


def normalize(text: str) -> str:
    """Lower-case, accents and punctuation removed, single spaces: 'São  Paulo!' -> 'sao paulo', 'Αθήνα' -> 'αθηνα'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s_\-.,;:!?()'\"/]+", " ", text.casefold()).strip()


def _tab(name: str) -> list[list[str]]:
    """Rows of a tz database table (zone.tab, iso3166.tab) from the tzdata package or the system, or [] if unavailable."""
    try:
        text = (importlib.resources.files("tzdata.zoneinfo") / name).read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        text = ""
        for base in zoneinfo.TZPATH:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
                break
    return [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]


def _distinct(zones: list[str]) -> list[str]:
    """Drop zones whose clock is identical to an earlier one all year round (Germany's Berlin and Busingen, Cyprus's two).

    Such zones are interchangeable for scheduling, so a country made only of them needs no follow-up question.
    """
    year = datetime.now().year
    probes = [datetime(year + y, m, 15, 12, tzinfo=zoneinfo.ZoneInfo("UTC")) for y in (0, 1) for m in (1, 4, 7, 10)]
    seen: dict[tuple, str] = {}
    for zone in zones:
        info = zoneinfo.ZoneInfo(zone)
        seen.setdefault(tuple(info.utcoffset(p.astimezone(info).replace(tzinfo=None)) for p in probes), zone)
    return list(seen.values())


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, list[str]], dict[str, str], dict[str, list[str]], dict[str, str]]:
    cities: dict[str, list[str]] = {}
    for row in _tab("zone.tab"):  # one row per named place: "Europe/Berlin", "Europe/Busingen", ...
        if len(row) >= 3:
            cities.setdefault(normalize(row[2].rsplit("/", 1)[-1]), []).append(row[2])
    zones_by_country: dict[str, list[str]] = {}
    for row in _tab("zone.tab"):
        if len(row) >= 3:
            zones_by_country.setdefault(row[0], []).append(row[2])
    zones_by_country = {code: _distinct(zones) for code, zones in zones_by_country.items()}
    country_codes = {normalize(row[1]): row[0] for row in _tab("iso3166.tab") if len(row) >= 2}
    country_names = {row[0]: row[1] for row in _tab("iso3166.tab") if len(row) >= 2}
    return cities, country_codes, zones_by_country, country_names


@dataclass
class Resolution:
    zone: str | None = None
    country: str | None = None  # set when the country has several zones and a city is needed


def resolve(text: str) -> Resolution:
    """Best timezone for a short answer, or a hint that a country needs a city, or an empty Resolution."""
    raw = text.strip()
    if "/" in raw:  # "Europe/Athens" typed out in full (bare words like "Japan" are legacy zone names: treat them as places)
        for zone in zoneinfo.available_timezones():
            if zone.casefold() == raw.casefold():
                return Resolution(zone=zone)
    cities, country_codes, zones_by_country, country_names = _tables()
    words = normalize(raw).split()
    # longest phrase first, so "new york" wins over "york" and "south africa" over "africa"
    for size in range(min(MAX_PHRASE, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start : start + size])
            if phrase in ALIASES:
                return Resolution(zone=ALIASES[phrase])
            if len(cities.get(phrase, [])) == 1 and len(phrase) > 2:
                return Resolution(zone=cities[phrase][0])
            code = COUNTRY_ALIASES.get(phrase) or country_codes.get(phrase)
            if code and zones_by_country.get(code):
                zones = zones_by_country[code]
                return Resolution(zone=zones[0]) if len(zones) == 1 else Resolution(country=country_names.get(code, phrase.title()))
    return Resolution()


def local_time(zone: str) -> str:
    return datetime.now(zoneinfo.ZoneInfo(zone)).strftime("%H:%M")
