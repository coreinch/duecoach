"""User-facing text per language. Model-facing instructions live in prompts.py."""

LANGUAGES = {"el": "Ελληνικά", "en": "English"}
DEFAULT_LANG = "en"


def lang_from_telegram(code: str | None) -> str:
    return "el" if code and code.lower().startswith("el") else "en"


WEEKDAYS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "el": ("Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"),
}
MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "el": ("Ιαν", "Φεβ", "Μαρ", "Απρ", "Μαΐ", "Ιουν", "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ"),
}


def t(lang: str, key: str, **kw) -> str:
    return STRINGS.get(lang, STRINGS[DEFAULT_LANG])[key].format(**kw)


STRINGS = {
    "el": {
        "HELP": (
            "Είμαι το Duecoach, ο coach σου για ADHD: σε βοηθάω να περνάς από την πρόθεση στην πράξη. Μίλα μου ελεύθερα ή χρησιμοποίησε:\n"
            "/stuck [εργασία] - δεν μπορείς να ξεκινήσεις; θα τη μικρύνω\n"
            "/plan [πλαίσιο] - σχεδιάζουμε τη μέρα\n"
            "/overwhelm [πλαίσιο] - είναι πολλά, βοήθησέ με να τα μαζέψω\n"
            "/remind <λεπτά> <κείμενο> - υπενθύμιση αργότερα\n"
            "/morning <0-23|off> - ώρα που ξεκινούν τα check-in της μέρας\n"
            "/evening <0-23|off> - ώρα που τελειώνουν\n"
            "/interval <15-240|on|off> - κάθε πόσα λεπτά (προεπιλογή 30, αρχικά κλειστό)\n"
            "/timezone <Περιοχή/Πόλη> - ζώνη ώρας για τα check-in (π.χ. Europe/Athens)\n"
            "/goals - οι στόχοι και τα βήματα της εβδομάδας\n"
            "/wrong - το τελευταίο μήνυμά μου ήταν λάθος\n"
            "/progress - σημείωση προόδου της εβδομάδας\n"
            "/toolbox - τι έχει δουλέψει για σένα\n"
            "/today - τι έχεις σήμερα\n"
            "/calendar - η επόμενη εβδομάδα\n"
            "/tasks - οι εργασίες σου\n"
            "/projects - τα projects σου και το επόμενο βήμα\n"
            "/notes - οι σημειώσεις σου\n"
            "/log - τι μου είπες ότι έκανες πρόσφατα\n"
            "/parked - ιδέες που άφησα στην άκρη για αργότερα\n"
            "/language <el|en> - γλώσσα\n"
            "/memory - τι θυμάμαι για σένα\n"
            "/privacy - πώς χρησιμοποιώ τα δεδομένα σου\n"
            "/deletedata - σβήσε τα πάντα για σένα\n"
            "/forget - ξέχασε ό,τι έμαθα για σένα"
        ),
        "LLM_ERROR": "Το μυαλό μου κόλλησε για λίγο. Δοκίμασε ξανά σε λίγο;",
        "REMIND_DEFAULT": "Ώρα να ξεκινήσεις!",
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
        "PARKED_HEADER": "Ιδέες που άφησα στην άκρη για αργότερα",
        "PARKED_EMPTY": "Δεν έχω αφήσει τίποτα στην άκρη. Αν ξεφύγεις σε κάτι άλλο ενώ δουλεύεις, θα το κρατήσω και θα σου το θυμίσω αργότερα.",
        "LOG_HEADER": "Τι μου είπες ότι έκανες (τελευταίες 2 εβδομάδες)",
        "LOG_EMPTY": "Δεν έχω σημειώσει τίποτα ακόμα. Πες μου τι κάνεις ή τι ολοκλήρωσες και θα το κρατήσω.",
        "DUE_TODAY": "σήμερα",
        "DUE_TOMORROW": "αύριο",
        "DUE_OVERDUE": "έληξε ({day})",
        "AGENDA_HEADER": "Σήμερα, {day}",
        "AGENDA_EMPTY": "Δεν έχεις τίποτα προγραμματισμένο σήμερα. Πες μου τι θέλεις να κάνεις.",
        "AGENDA_EVENTS": "Ραντεβού",
        "AGENDA_REMINDERS": "Υπενθυμίσεις",
        "AGENDA_OVERDUE": "Έχουν περάσει",
        "AGENDA_DUE_TODAY": "Για σήμερα",
        "AGENDA_NO_DATE": "Χωρίς ημερομηνία",
        "CALENDAR_HEADER": "Οι επόμενες {days} μέρες",
        "CALENDAR_EMPTY": "Δεν έχεις τίποτα προγραμματισμένο τις επόμενες {days} μέρες. Πες μου τι έχεις και θα το γράψω.",
        "NOTE_HEADER": "Οι σημειώσεις σου (οι πιο πρόσφατες πρώτα)",
        "NOTE_EMPTY": "Δεν έχεις σημειώσεις. Πες μου «κράτα σημείωση ότι…» και θα τη γράψω.",
        "PROJECTS_HEADER": "Τα projects σου",
        "PROJECTS_NEXT": "επόμενο βήμα: {step} ({n} ανοιχτά)",
        "PROJECTS_NO_STEP": "δεν υπάρχει επόμενο βήμα ακόμα",
        "PROJECTS_EMPTY": "Δεν έχεις projects. Πες μου για κάτι που θέλει αρκετά βήματα (μετακόμιση, αναζήτηση δουλειάς, ένα ταξίδι) και θα το στήσω.",
        "PROJECT_HEADER": "Project: {title}",
        "PROJECT_NO_STEP": "    (δεν υπάρχει επόμενο βήμα ακόμα)",
        "TASKS_OTHER": "Άλλες εργασίες",
        "TASKS_HEADER": "Η λίστα σου",
        "TASKS_EMPTY": "Δεν υπάρχει τίποτα στη λίστα. Πες μου τι πρέπει να κάνεις, θα το κρατήσω και θα σου το θυμίσω όταν ταιριάζει.",
        "TOOLBOX_HEADER": "Το εργαλειοκουτί σου (τι έχει δουλέψει)",
        "TOOLBOX_EMPTY": "Άδειο ακόμα. Θα βάζουμε εδώ ό,τι σε βοηθάει.",
        "INTERVAL_SHOW": "Θα σε ρωτάω περίπου κάθε {minutes} λεπτά (πιο αραιά αν δεν απαντάς). Άλλαξέ το με /interval 60 ή /interval off",
        "INTERVAL_OFF": "Τα check-in σταμάτησαν. Ενεργοποίησέ τα ξανά με /interval 30.",
        "INTERVAL_SET": "Εντάξει, κάθε {minutes} λεπτά περίπου (πιο αραιά αν δεν απαντάς).",
        "INTERVAL_BAD": "Δώσε αριθμό από 15 έως 240, ή on/off. Παράδειγμα: /interval 30",
        "PRIVACY": (
            "Γεια, είμαι το Duecoach. Σε βοηθάω να ολοκληρώνεις όσα σκοπεύεις να κάνεις: διαλέγεις στόχο, κάνεις ένα μικρό βήμα την εβδομάδα και παίρνεις ώθηση όταν κολλάς.\n\n"
            "Πριν ξεκινήσουμε: είμαι βοηθός coaching με τεχνητή νοημοσύνη, όχι θεραπευτής ή ιατρική υπηρεσία. "
            "Όσα μου γράφεις (και οι στόχοι και η πρόοδός σου) αποθηκεύονται για να σε καθοδηγώ, και κάθε μήνυμα "
            "στέλνεται σε πάροχο μοντέλου τεχνητής νοημοσύνης για να φτιαχτεί η απάντηση (τα φωνητικά μηνύματα στέλνονται επίσης, για να γίνουν κείμενο· ο ήχος δεν διατηρείται). Μην μοιράζεσαι κάτι που δεν θα ήθελες "
            "να επεξεργαστεί έτσι. Βλέπεις τι κρατάω με /memory, /tasks, /notes και /goals και σβήνεις τα πάντα με /deletedata, και τα αντίγραφα ασφαλείας αντικαθίστανται μέσα σε περίπου μία εβδομάδα.{keeping} "
            "Αν βρίσκεσαι σε κρίση ή κίνδυνο, κάλεσε τον τοπικό αριθμό έκτακτης ανάγκης (112 στην ΕΕ) αντί για εμένα.\n\n"
            "Στείλε /agree για να συνεχίσουμε."
        ),
        "KEEP_MESSAGES": " Τα μηνύματα της συνομιλίας σβήνονται μετά από {days} ημέρες.",
        "KEEP_INACTIVE": " Όλα όσα έχω για σένα σβήνονται μετά από {days} ημέρες χωρίς μήνυμά σου.",
        "AGREED": "Ευχαριστώ, είμαστε έτοιμοι. Πρώτα, πού μένεις; Πες μου την πόλη (ή τη χώρα) σου και θα ορίσω τη ζώνη ώρας, ώστε υπενθυμίσεις και check-in να έρχονται στη σωστή ώρα. Μπορείς και να πεις «παράλειψη». Μετά πες μου τι σε έφερε εδώ.",
        "DELETE_CONFIRM": "Αυτό σβήνει οριστικά ό,τι έχω για σένα: ιστορικό συνομιλίας, σημειώσεις, στόχους, εβδομαδιαία βήματα, εργασίες της λίστας, ραντεβού, σημειώσεις σου, σημειώσεις δραστηριότητας, ιδέες στην άκρη, εργαλειοκουτί και υπενθυμίσεις. Στείλε /deletedata confirm για να προχωρήσω.",
        "DELETED": "Έγινε. Όλα όσα είχα για σένα σβήστηκαν (τα αντίγραφα ασφαλείας αντικαθίστανται μέσα σε περίπου μία εβδομάδα). Μπορείς να ξεκινήσεις από την αρχή όποτε θέλεις.",
        "RATE_LIMITED": "Είναι πολλά μαζί. Θα κάνω μια παύση λίγων λεπτών για να προλάβω.",
        "EMPTY_REPLY": "Δεν κατάφερα να σκεφτώ σωστή απάντηση αυτή τη στιγμή. Μπορείς να το ξαναγράψεις σε λίγο;",
        "UNSURE": "Μάλλον δεν το κατάλαβα σωστά. Μπορείς να μου το ξαναπείς πιο συγκεκριμένα;",
        "NUDGE_SETUP": "Γεια, είμαι ο Duecoach. Σταματήσαμε στη μέση της εγκατάστασης. Θες να συνεχίσουμε; Απάντησε με μια λέξη ή γράψε /help. Αν δεν θέλεις, αγνόησέ το: δεν θα ξαναγράψω.",
        "WRONG_OK": "Ευχαριστώ, το σημείωσα ότι το τελευταίο μήνυμά μου ήταν λάθος. Πες μου τι έπρεπε να πω ή τι να διορθώσω.",
        "WRONG_NONE": "Δεν έχω κάποιο μήνυμά μου να σημειώσω ακόμα.",
        "CHECKIN_ORDER": "Η ώρα έναρξης πρέπει να είναι πριν την ώρα λήξης (π.χ. /morning 9 και /evening 21).",
        "NEED_TZ": "Πρώτα πες μου τη ζώνη ώρας σου για να έρχονται τα check-in σε λογικές ώρες: /timezone Europe/Athens (ή πες μου απλώς την πόλη σου).",
        "CRISIS": (
            "Λυπάμαι πολύ που νιώθεις έτσι, και χαίρομαι που μου το είπες. Μετράς και δεν χρειάζεται να το αντιμετωπίσεις μόνος ή μόνη. "
            "Αν υπάρχει πιθανότητα να πράξεις σύμφωνα με αυτές τις σκέψεις ή βρίσκεσαι σε κίνδυνο, κάλεσε τώρα τον τοπικό αριθμό έκτακτης "
            "ανάγκης (112 στην ΕΕ) ή πήγαινε στο κοντινότερο τμήμα επειγόντων. Αν μπορείς, πες το σε κάποιον που εμπιστεύεσαι και σκέψου "
            "να μιλήσεις με επαγγελματία ψυχικής υγείας ή με μια γραμμή βοήθειας στη χώρα σου.{help}\n\n"
            "Είσαι ασφαλής αυτή τη στιγμή; Είμαι εδώ και μπορούμε να συνεχίσουμε να μιλάμε."
        ),
        "PROFILE_HEADER": "Για σένα (από την πρώτη μας κουβέντα)",
        "PROFILE_why": "Γιατί ήρθες",
        "PROFILE_tried": "Τι έχεις δοκιμάσει",
        "PROFILE_obstacle": "Τι σε δυσκολεύει",
        "PROFILE_strength": "Δυνατά σου σημεία",
        "PROFILE_rhythm": "Η μέρα σου",
        "PROFILE_mood": "Διάθεση",
        "CHECKINS_ENABLED": "Θα σε ρωτάω αφού περάσουν περίπου {minutes} λεπτά ησυχίας, και πιο αραιά αν δεν απαντάς. Με /interval off σταματούν, με /interval 60 τα αραιώνεις.",
        "UNSUPPORTED": "Προς το παρόν διαβάζω μόνο γραπτά και φωνητικά μηνύματα. Γράψε μου τι σκέφτεσαι;",
        "VOICE_TOO_LONG": "Το φωνητικό είναι πολύ μεγάλο (έως {seconds} δευτερόλεπτα). Μπορείς να το σπάσεις σε μικρότερα ή να το γράψεις;",
        "VOICE_FAILED": "Δεν κατάφερα να ακούσω το φωνητικό. Δοκίμασε ξανά ή γράψε το μου.",
        "VOICE_UNCLEAR": "Δεν έπιασα λέξεις στο φωνητικό. Μπορείς να το πεις ξανά πιο κοντά στο μικρόφωνο ή να το γράψεις;",
    },
    "en": {
        "HELP": (
            "I'm Duecoach, your ADHD coach. I help you get from meaning to do it to having done it. Just talk to me, or use:\n"
            "/stuck [task] - can't start? I'll shrink it\n"
            "/plan [context] - plan today\n"
            "/overwhelm [context] - too much, help me reduce\n"
            "/remind <minutes> <text> - nudge me later\n"
            "/morning <0-23|off> - hour your daily check-ins start\n"
            "/evening <0-23|off> - hour they stop\n"
            "/interval <15-240|on|off> - minutes between check-ins (default 30, off at first)\n"
            "/timezone <Area/City> - set your timezone for check-ins (e.g. Europe/Athens)\n"
            "/goals - your goals and this week's steps\n"
            "/wrong - my last message was wrong\n"
            "/progress - this week's progress note\n"
            "/toolbox - what has worked for you\n"
            "/today - what's on today\n"
            "/calendar - the next week\n"
            "/tasks - your to-dos\n"
            "/projects - your projects and their next step\n"
            "/notes - your notes\n"
            "/log - what you told me you did lately\n"
            "/parked - ideas I set aside for later\n"
            "/language <el|en> - language\n"
            "/memory - what I have learned about you\n"
            "/privacy - how your data is used\n"
            "/deletedata - erase everything about you\n"
            "/forget - wipe my memory of you"
        ),
        "LLM_ERROR": "My brain glitched for a sec. Try again in a moment?",
        "REMIND_DEFAULT": "Time to start!",
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
        "PARKED_HEADER": "Ideas I set aside for later",
        "PARKED_EMPTY": "Nothing parked. If you drift onto something else while you work, I'll keep it and bring it back later.",
        "LOG_HEADER": "What you told me you did (last 2 weeks)",
        "LOG_EMPTY": "Nothing noted yet. Tell me what you are doing or finished and I'll keep it.",
        "DUE_TODAY": "today",
        "DUE_TOMORROW": "tomorrow",
        "DUE_OVERDUE": "overdue ({day})",
        "AGENDA_HEADER": "Today, {day}",
        "AGENDA_EMPTY": "Nothing planned for today. Tell me what you want to get done.",
        "AGENDA_EVENTS": "Appointments",
        "AGENDA_REMINDERS": "Reminders",
        "AGENDA_OVERDUE": "Overdue",
        "AGENDA_DUE_TODAY": "Due today",
        "AGENDA_NO_DATE": "No date",
        "CALENDAR_HEADER": "The next {days} days",
        "CALENDAR_EMPTY": "Nothing planned for the next {days} days. Tell me what's coming up and I'll put it in.",
        "NOTE_HEADER": "Your notes (newest first)",
        "NOTE_EMPTY": 'No notes yet. Tell me "note down that…" and I\'ll write it for you.',
        "PROJECTS_HEADER": "Your projects",
        "PROJECTS_NEXT": "next: {step} ({n} open)",
        "PROJECTS_NO_STEP": "no next step yet",
        "PROJECTS_EMPTY": "No projects yet. Tell me about something that takes several steps (a move, a job search, a trip) and I'll set it up.",
        "PROJECT_HEADER": "Project: {title}",
        "PROJECT_NO_STEP": "    (no next step yet)",
        "TASKS_OTHER": "Other to-dos",
        "TASKS_HEADER": "Your to-do list",
        "TASKS_EMPTY": "Nothing on the list. Tell me what you need to get done and I'll keep it for you and nudge you when it fits.",
        "TOOLBOX_HEADER": "Your toolbox (what has worked)",
        "TOOLBOX_EMPTY": "Empty for now. We'll keep whatever helps you here.",
        "INTERVAL_SHOW": "I check in about every {minutes} minutes (further apart if you go quiet). Change it with /interval 60 or /interval off",
        "INTERVAL_OFF": "Check-ins stopped. Turn them back on with /interval 30.",
        "INTERVAL_SET": "Okay, about every {minutes} minutes (further apart if you go quiet).",
        "INTERVAL_BAD": "Use a number from 15 to 240, or on/off. Example: /interval 30",
        "PRIVACY": (
            "Hi, I'm Duecoach. I help you follow through on what you mean to do: pick a goal, take one small step a week, and get a nudge when you get stuck.\n\n"
            "Before we start: I'm an AI coaching assistant, not a therapist or a medical service. What you write to me (including your "
            "goals and progress) is stored so I can coach you, and each message is sent to an AI model provider to generate the reply (voice notes are sent too, to be turned into text; the audio is not kept). "
            "Please don't share anything you wouldn't want processed that way. You can see what I keep with /memory, /tasks, /notes and /goals, and "
            "erase everything with /deletedata, and backup copies are overwritten within about a week.{keeping} If you're in crisis or danger, contact your local emergency number (112 in the EU) "
            "instead of me.\n\nSend /agree to continue."
        ),
        "KEEP_MESSAGES": " Chat messages are deleted after {days} days.",
        "KEEP_INACTIVE": " Everything about you is deleted after {days} days without a message from you.",
        "AGREED": "Thanks, you're set. First, where do you live? Tell me your city (or country) and I'll set your timezone, so reminders and check-ins land at the right time. You can also say \"skip\". Then tell me what brought you here.",
        "DELETE_CONFIRM": "This permanently erases everything I have about you: chat history, notes, goals, weekly steps, to-dos, appointments, your own notes, activity notes, parked ideas, toolbox and reminders. Send /deletedata confirm to go ahead.",
        "DELETED": "Done. Everything I had about you has been erased (backup copies are overwritten within about a week). You're welcome to start fresh any time.",
        "RATE_LIMITED": "That's a lot at once. I'll pause for a few minutes to catch up.",
        "EMPTY_REPLY": "I couldn't put a proper answer together just now. Could you send that again in a moment?",
        "UNSURE": "I probably didn't get that right. Could you say it again, a bit more precisely?",
        "NUDGE_SETUP": "Hi, it's Duecoach. We stopped in the middle of getting set up. Want to pick it up? Reply with a word, or send /help. If not, just ignore this: I won't write again.",
        "WRONG_OK": "Thanks, I've marked my last message as wrong. Tell me what I should have said or what to fix.",
        "WRONG_NONE": "I don't have a message of mine to mark yet.",
        "CHECKIN_ORDER": "The start hour must be earlier than the end hour (for example /morning 9 and /evening 21).",
        "NEED_TZ": "First tell me your timezone so check-ins arrive at sensible times: /timezone Europe/Athens (or just tell me your city).",
        "CRISIS": (
            "I'm really sorry you're feeling this way, and I'm glad you told me. You matter, and you don't have to face this alone. "
            "If you might act on these thoughts or you're in danger, please call your local emergency number now (112 in the EU) or go to "
            "the nearest emergency department. If you can, tell someone you trust how you're feeling, and consider reaching out to a "
            "mental-health professional or a helpline in your country.{help}\n\n"
            "Are you safe right now? I'm here, and we can keep talking."
        ),
        "PROFILE_HEADER": "About you (from our first chat)",
        "PROFILE_why": "Why you came",
        "PROFILE_tried": "What you've tried",
        "PROFILE_obstacle": "What gets in your way",
        "PROFILE_strength": "Your strengths",
        "PROFILE_rhythm": "Your day",
        "PROFILE_mood": "Mood",
        "CHECKINS_ENABLED": "I'll check in after about {minutes} minutes of quiet, and less often if you don't reply. /interval off stops it, /interval 60 spaces it out.",
        "UNSUPPORTED": "I can only read text and voice messages for now. Could you type it out?",
        "VOICE_TOO_LONG": "That voice note is too long (up to {seconds} seconds). Could you split it up or type it?",
        "VOICE_FAILED": "I couldn't listen to that voice note. Try again, or type it for me.",
        "VOICE_UNCLEAR": "I couldn't make out any words in that voice note. Could you say it again closer to the mic, or type it?",
    },
}
