"""User-facing text per language. Model-facing instructions live in prompts.py."""

LANGUAGES = {"el": "Ελληνικά", "en": "English"}
DEFAULT_LANG = "en"


def lang_from_telegram(code: str | None) -> str:
    return "el" if code and code.lower().startswith("el") else "en"


def t(lang: str, key: str, **kw) -> str:
    return STRINGS.get(lang, STRINGS[DEFAULT_LANG])[key].format(**kw)


STRINGS = {
    "el": {
        "HELP": (
            "Είμαι ο προσωπικός σου coach για ADHD. Μίλα μου ελεύθερα ή χρησιμοποίησε:\n"
            "/stuck [εργασία] - δεν μπορείς να ξεκινήσεις; θα τη μικρύνω\n"
            "/plan [πλαίσιο] - σχεδιάζουμε τη μέρα\n"
            "/overwhelm [πλαίσιο] - είναι πολλά, βοήθησέ με να τα μαζέψω\n"
            "/remind <λεπτά> <κείμενο> - υπενθύμιση αργότερα\n"
            "/morning <0-23|off> - ώρα που ξεκινούν τα check-in της μέρας\n"
            "/evening <0-23|off> - ώρα που τελειώνουν\n"
            "/interval <15-240|on|off> - κάθε πόσα λεπτά (προεπιλογή 30, αρχικά κλειστό)\n"
            "/timezone <Περιοχή/Πόλη> - ζώνη ώρας για τα check-in (π.χ. Europe/Athens)\n"
            "/goals - οι στόχοι και οι εβδομαδιαίοι στόχοι σου\n"
            "/progress - σημείωση προόδου της εβδομάδας\n"
            "/toolbox - τι έχει δουλέψει για σένα\n"
            "/language <el|en> - γλώσσα\n"
            "/notes - τι θυμάμαι για σένα\n"
            "/privacy - πώς χρησιμοποιώ τα δεδομένα σου\n"
            "/deletedata - σβήσε τα πάντα για σένα\n"
            "/forget - σβήσε τις σημειώσεις μου για σένα"
        ),
        "LLM_ERROR": "Το μυαλό μου κόλλησε για λίγο. Δοκίμασε ξανά σε λίγο;",
        "REMIND_DEFAULT": "Ο χρόνος τελείωσε!",
        "REMIND_PREFIX": "Υπενθύμιση: {text}",
        "REMIND_USAGE": "Χρήση: /remind 25 ξεκίνα την αναφορά",
        "REMIND_OK": "Εντάξει. Θα σου θυμίσω σε {minutes:g} λεπτά.",
        "NOTES_EMPTY": "Τίποτα ακόμα. Μόλις αρχίσαμε.",
        "NOTES_CLEARED": "Οι σημειώσεις σβήστηκαν. (Το πρόσφατο ιστορικό συνομιλίας εξακολουθεί να χρησιμοποιείται.)",
        "TZ_SHOW": "Η ζώνη ώρας σου είναι {tz}. Άλλαξέ την με /timezone Europe/Athens",
        "TZ_UNKNOWN": "Δεν ξέρω αυτή τη ζώνη ώρας. Χρησιμοποίησε τη μορφή Περιοχή/Πόλη, π.χ. Europe/Athens.",
        "TZ_SET": "Η ζώνη ώρας ορίστηκε σε {tz}.",
        "LABEL_morning": "ξεκινούν",
        "LABEL_evening": "τελειώνουν",
        "CHECKIN_SHOW_OFF": "Τα check-in είναι απενεργοποιημένα (το /{name} είναι off). Ενεργοποίησέ το με /{name} 8",
        "CHECKIN_SHOW_ON": "Τα check-in της μέρας {label} στις {hour}:00. Άλλαξέ το με /{name} 8 ή /{name} off",
        "CHECKIN_BAD": "Δώσε ώρα από 0 έως 23, ή off. Παράδειγμα: /{name} 8",
        "CHECKIN_OFF": "Τα check-in απενεργοποιήθηκαν. Ενεργοποίησέ τα ξανά με /{name} <ώρα>.",
        "CHECKIN_SET": "Τα check-in της μέρας {label} στις {hour}:00 ({tz}).",
        "LANG_SHOW": "Η γλώσσα είναι {name}. Άλλαξέ την με /language en ή /language el",
        "LANG_BAD": "Διάλεξε el (Ελληνικά) ή en (English).",
        "LANG_SET": "Η γλώσσα ορίστηκε σε {name}.",
        "GOALS_HEADER": "Οι στόχοι σου",
        "GOALS_NONE": "Δεν έχουμε στόχους ακόμα. Πες μου τι θα ήθελες να δουλέψουμε και θα τους φτιάξουμε μαζί.",
        "OBJ_HEADER": "Αυτή την εβδομάδα",
        "OBJ_NONE": "Δεν υπάρχουν ανοιχτοί στόχοι εβδομάδας αυτή τη στιγμή.",
        "TOOLBOX_HEADER": "Το εργαλειοκουτί σου (τι έχει δουλέψει)",
        "TOOLBOX_EMPTY": "Άδειο ακόμα. Θα βάζουμε εδώ ό,τι σε βοηθάει.",
        "INTERVAL_SHOW": "Θα σε ρωτάω περίπου κάθε {minutes} λεπτά (πιο αραιά αν δεν απαντάς). Άλλαξέ το με /interval 60 ή /interval off",
        "INTERVAL_OFF": "Τα check-in σταμάτησαν. Ενεργοποίησέ τα ξανά με /interval 30.",
        "INTERVAL_SET": "Εντάξει, κάθε {minutes} λεπτά περίπου (πιο αραιά αν δεν απαντάς).",
        "INTERVAL_BAD": "Δώσε αριθμό από 15 έως 240, ή on/off. Παράδειγμα: /interval 30",
        "PRIVACY": (
            "Πριν ξεκινήσουμε: είμαι βοηθός coaching με τεχνητή νοημοσύνη, όχι θεραπευτής ή ιατρική υπηρεσία. "
            "Όσα μου γράφεις (και οι στόχοι και η πρόοδός σου) αποθηκεύονται για να σε καθοδηγώ, και κάθε μήνυμα "
            "στέλνεται σε πάροχο μοντέλου τεχνητής νοημοσύνης για να φτιαχτεί η απάντηση. Μην μοιράζεσαι κάτι που δεν θα ήθελες "
            "να επεξεργαστεί έτσι. Βλέπεις τι κρατάω με /notes και /goals και σβήνεις τα πάντα με /deletedata. "
            "Αν βρίσκεσαι σε κρίση ή κίνδυνο, κάλεσε τον τοπικό αριθμό έκτακτης ανάγκης (112 στην ΕΕ) αντί για εμένα.\n\n"
            "Στείλε /agree για να συνεχίσουμε."
        ),
        "AGREED": "Ευχαριστώ, είμαστε έτοιμοι. Τα check-in είναι προς το παρόν απενεργοποιημένα. Πες μου τι σε έφερε εδώ και αργότερα ορίζουμε ζώνη ώρας και check-in. Το /help δείχνει τις εντολές.",
        "DELETE_CONFIRM": "Αυτό σβήνει οριστικά ό,τι έχω για σένα: ιστορικό συνομιλίας, σημειώσεις, στόχους, εβδομαδιαίους στόχους, εργαλειοκουτί και υπενθυμίσεις. Στείλε /deletedata confirm για να προχωρήσω.",
        "DELETED": "Έγινε. Όλα όσα είχα για σένα σβήστηκαν. Μπορείς να ξεκινήσεις από την αρχή όποτε θέλεις.",
        "RATE_LIMITED": "Στέλνεις μηνύματα πολύ γρήγορα. Θα κάνω μια παύση λίγων λεπτών για να προλαβαίνω.",
        "EMPTY_REPLY": "Είμαι εδώ. Θέλεις να μου πεις τι συμβαίνει;",
        "CHECKIN_ORDER": "Η ώρα έναρξης πρέπει να είναι πριν την ώρα λήξης (π.χ. /morning 9 και /evening 21).",
        "NEED_TZ": "Πρώτα πες μου τη ζώνη ώρας σου για να έρχονται τα check-in σε λογικές ώρες: /timezone Europe/Athens (ή πες μου απλώς την πόλη σου).",
        "UNSUPPORTED": "Προς το παρόν διαβάζω μόνο γραπτά μηνύματα. Γράψε μου τι σκέφτεσαι;",
    },
    "en": {
        "HELP": (
            "I'm your ADHD coach. Just talk to me, or use:\n"
            "/stuck [task] - can't start? I'll shrink it\n"
            "/plan [context] - plan today\n"
            "/overwhelm [context] - too much, help me reduce\n"
            "/remind <minutes> <text> - nudge me later\n"
            "/morning <0-23|off> - hour your daily check-ins start\n"
            "/evening <0-23|off> - hour they stop\n"
            "/interval <15-240|on|off> - minutes between check-ins (default 30, off at first)\n"
            "/timezone <Area/City> - set your timezone for check-ins (e.g. Europe/Athens)\n"
            "/goals - your goals and this week's objectives\n"
            "/progress - this week's progress note\n"
            "/toolbox - what has worked for you\n"
            "/language <el|en> - language\n"
            "/notes - see what I remember about you\n"
            "/privacy - how your data is used\n"
            "/deletedata - erase everything about you\n"
            "/forget - wipe my memory of you"
        ),
        "LLM_ERROR": "My brain glitched for a sec. Try again in a moment?",
        "REMIND_DEFAULT": "Time's up!",
        "REMIND_PREFIX": "Reminder: {text}",
        "REMIND_USAGE": "Usage: /remind 25 start the report",
        "REMIND_OK": "Got it. I'll nudge you in {minutes:g} min.",
        "NOTES_EMPTY": "Nothing yet. We're just getting started.",
        "NOTES_CLEARED": "Notes cleared. (Recent chat history is still used for context.)",
        "TZ_SHOW": "Your timezone is {tz}. Change it with /timezone Europe/Athens",
        "TZ_UNKNOWN": "I don't know that timezone. Use the Area/City form, e.g. America/New_York.",
        "TZ_SET": "Timezone set to {tz}.",
        "LABEL_morning": "start",
        "LABEL_evening": "end",
        "CHECKIN_SHOW_OFF": "Check-ins are off (/{name} is off). Turn them on with /{name} 8",
        "CHECKIN_SHOW_ON": "Your daily check-ins {label} at {hour}:00. Change it with /{name} 8 or /{name} off",
        "CHECKIN_BAD": "Use an hour from 0 to 23, or off. Example: /{name} 8",
        "CHECKIN_OFF": "Check-ins are turned off. Turn them back on with /{name} <hour>.",
        "CHECKIN_SET": "Your daily check-ins {label} at {hour}:00 ({tz}).",
        "LANG_SHOW": "Your language is {name}. Change it with /language en or /language el",
        "LANG_BAD": "Pick el (Ελληνικά) or en (English).",
        "LANG_SET": "Language set to {name}.",
        "GOALS_HEADER": "Your goals",
        "GOALS_NONE": "No goals yet. Tell me what you'd like to work on and we'll shape them together.",
        "OBJ_HEADER": "This week",
        "OBJ_NONE": "No open objectives right now.",
        "TOOLBOX_HEADER": "Your toolbox (what has worked)",
        "TOOLBOX_EMPTY": "Empty for now. We'll keep whatever helps you here.",
        "INTERVAL_SHOW": "I check in about every {minutes} minutes (further apart if you go quiet). Change it with /interval 60 or /interval off",
        "INTERVAL_OFF": "Check-ins stopped. Turn them back on with /interval 30.",
        "INTERVAL_SET": "Okay, about every {minutes} minutes (further apart if you go quiet).",
        "INTERVAL_BAD": "Use a number from 15 to 240, or on/off. Example: /interval 30",
        "PRIVACY": (
            "Before we start: I'm an AI coaching assistant, not a therapist or a medical service. What you write to me (including your "
            "goals and progress) is stored so I can coach you, and each message is sent to an AI model provider to generate the reply. "
            "Please don't share anything you wouldn't want processed that way. You can see what I keep with /notes and /goals, and "
            "erase everything with /deletedata. If you're in crisis or danger, contact your local emergency number (112 in the EU) "
            "instead of me.\n\nSend /agree to continue."
        ),
        "AGREED": "Thanks, you're set. Check-ins are off for now. Tell me what brought you here, and later we can set your timezone and check-ins. /help lists the commands.",
        "DELETE_CONFIRM": "This permanently erases everything I have about you: chat history, notes, goals, objectives, toolbox and reminders. Send /deletedata confirm to go ahead.",
        "DELETED": "Done. Everything I had about you has been erased. You're welcome to start fresh any time.",
        "RATE_LIMITED": "You're sending messages very fast. I'll pause for a few minutes so I can keep up.",
        "EMPTY_REPLY": "I'm here. Want to tell me what's going on?",
        "CHECKIN_ORDER": "The start hour must be earlier than the end hour (for example /morning 9 and /evening 21).",
        "NEED_TZ": "First tell me your timezone so check-ins arrive at sensible times: /timezone Europe/Athens (or just tell me your city).",
        "UNSUPPORTED": "I can only read text messages for now. Could you type it out?",
    },
}
