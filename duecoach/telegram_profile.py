"""Set the bot's public Telegram profile (name, descriptions, command menu): python -m duecoach.telegram_profile

Telegram keeps this on its side, outside the running bot, and rate-limits name changes, so it is a manual step rather than
something done at every start-up. The avatar can only be set through BotFather (/setuserpic).
"""

import asyncio

from telegram import Bot, BotCommand

from .config import TELEGRAM_BOT_TOKEN

NAME = "duecoach"

# language_code None is what Telegram shows to everyone without a more specific translation
SHORT = {
    "en": "ADHD coach: from meaning to do it to having done it.",
    "el": "Coach για ADHD: από την πρόθεση στην πράξη.",
}
ABOUT = {
    "en": (
        "duecoach is an ADHD coach that helps you follow through: pick a goal, take one small step a week, "
        "and get nudged when you get stuck. Not therapy or medical advice. Send /start to begin."
    ),
    "el": (
        "Το duecoach είναι coach για ADHD που σε βοηθάει να ολοκληρώνεις: διαλέγεις στόχο, κάνεις ένα μικρό βήμα "
        "την εβδομάδα και παίρνεις ώθηση όταν κολλάς. Δεν αντικαθιστά θεραπεία ή ιατρική συμβουλή. Στείλε /start."
    ),
}
MENU = {
    "en": {
        "stuck": "Can't start? I'll shrink the task",
        "plan": "Plan today",
        "overwhelm": "Too much? Let's reduce it",
        "remind": "Nudge me later: /remind 25 start the report",
        "goals": "Your goals and this week's steps",
        "goal": "Set a new goal",
        "step": "Pick this week's step",
        "progress": "This week's progress note",
        "toolbox": "What has worked for you",
        "notes": "What I remember about you",
        "timezone": "Set your timezone for check-ins",
        "language": "Language: el or en",
        "help": "All commands",
        "privacy": "How your data is used",
        "deletedata": "Erase everything about you",
    },
    "el": {
        "stuck": "Δεν μπορείς να ξεκινήσεις; Θα μικρύνω την εργασία",
        "plan": "Σχεδιάζουμε τη μέρα",
        "overwhelm": "Είναι πολλά; Να τα μαζέψουμε",
        "remind": "Υπενθύμιση αργότερα: /remind 25 ξεκίνα την αναφορά",
        "goals": "Οι στόχοι και τα βήματα της εβδομάδας",
        "goal": "Όρισε νέο στόχο",
        "step": "Διάλεξε το βήμα της εβδομάδας",
        "progress": "Σημείωση προόδου της εβδομάδας",
        "toolbox": "Τι έχει δουλέψει για σένα",
        "notes": "Τι θυμάμαι για σένα",
        "timezone": "Ζώνη ώρας για τα check-in",
        "language": "Γλώσσα: el ή en",
        "help": "Όλες οι εντολές",
        "privacy": "Πώς χρησιμοποιώ τα δεδομένα σου",
        "deletedata": "Σβήσε τα πάντα για σένα",
    },
}


async def apply(bot: Bot) -> None:
    await bot.set_my_name(NAME)
    for code, language_code in (("en", None), ("en", "en"), ("el", "el")):
        await bot.set_my_short_description(SHORT[code], language_code=language_code)
        await bot.set_my_description(ABOUT[code], language_code=language_code)
        await bot.set_my_commands([BotCommand(name, text) for name, text in MENU[code].items()], language_code=language_code)


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first")
    async with Bot(TELEGRAM_BOT_TOKEN) as bot:
        await apply(bot)
    print("Telegram profile updated. Set the avatar in BotFather with /setuserpic.")


if __name__ == "__main__":
    asyncio.run(main())
