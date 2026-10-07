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
            "/goals - οι στόχοι και τα βήματα της εβδομάδας\n"
            "/goal - όρισε νέο στόχο\n"
            "/step - διάλεξε το βήμα της εβδομάδας\n"
            "/progress - σημείωση προόδου της εβδομάδας\n"
            "/toolbox - τι έχει δουλέψει για σένα\n"
            "/language <el|en> - γλώσσα\n"
            "/notes - τι θυμάμαι για σένα\n"
            "/intake - ξανακάνε τις ερωτήσεις γνωριμίας\n"
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
        "AGREED": "Ευχαριστώ, είμαστε έτοιμοι. Πες μου τι σε έφερε εδώ. Τα check-in ξεκινούν μόλις ξέρω τη ζώνη ώρας σου. Το /help δείχνει τις εντολές.",
        "DELETE_CONFIRM": "Αυτό σβήνει οριστικά ό,τι έχω για σένα: ιστορικό συνομιλίας, σημειώσεις, στόχους, εβδομαδιαίους στόχους, εργαλειοκουτί και υπενθυμίσεις. Στείλε /deletedata confirm για να προχωρήσω.",
        "DELETED": "Έγινε. Όλα όσα είχα για σένα σβήστηκαν. Μπορείς να ξεκινήσεις από την αρχή όποτε θέλεις.",
        "RATE_LIMITED": "Στέλνεις μηνύματα πολύ γρήγορα. Θα κάνω μια παύση λίγων λεπτών για να προλαβαίνω.",
        "EMPTY_REPLY": "Είμαι εδώ. Θέλεις να μου πεις τι συμβαίνει;",
        "CHECKIN_ORDER": "Η ώρα έναρξης πρέπει να είναι πριν την ώρα λήξης (π.χ. /morning 9 και /evening 21).",
        "NEED_TZ": "Πρώτα πες μου τη ζώνη ώρας σου για να έρχονται τα check-in σε λογικές ώρες: /timezone Europe/Athens (ή πες μου απλώς την πόλη σου).",
        "TZ_ASK": "Παρεμπιπτόντως, πού μένεις; Πες μου την πόλη (ή τη χώρα) σου και θα ορίσω τη ζώνη ώρας, ώστε υπενθυμίσεις και check-in να έρχονται στη σωστή ώρα. Μπορείς και να πεις «παράλειψη».",
        "TZ_MULTI": "Η χώρα {country} έχει πολλές ζώνες ώρας. Ποια πόλη είναι πιο κοντά σου;",
        "TZ_RETRY": "Δεν κατάλαβα τη ζώνη ώρας από αυτό. Δοκίμασε μια μεγαλύτερη κοντινή πόλη, ή τη μορφή Περιοχή/Πόλη (π.χ. Europe/Athens), ή πες «παράλειψη».",
        "TZ_SKIPPED": "Κανένα πρόβλημα. Μπορείς να την ορίσεις όποτε θέλεις με /timezone Europe/Athens· τα check-in ξεκινούν μόλις οριστεί.",
        "GOAL_ASK": "Ας ορίσουμε έναν στόχο μαζί. Ποιο είναι το ένα πράγμα που θα ήθελες περισσότερο να αλλάξεις ή να βελτιώσεις; Αρκεί μια-δυο προτάσεις, ή πες «παράλειψη».",
        "GOAL_MORE": "Πες μου λίγο περισσότερα, σε μία-δυο προτάσεις.",
        "GOAL_CONFIRM": "Έτσι θα τον έγραφα ως στόχο: «{goal}»\nΣου ταιριάζει; Απάντησε ναι, ή πες μου τι να αλλάξω.",
        "GOAL_SAVED": "Ο στόχος αποθηκεύτηκε: «{goal}»",
        "GOAL_SKIPPED": "Κανένα πρόβλημα. Γράψε /goal όποτε θέλεις να ορίσεις έναν.",
        "GOAL_FULL": "Έχεις ήδη το μέγιστο των 4 στόχων. Ολοκλήρωσε ή άφησε έναν πριν προσθέσεις άλλον.",
        "REVISE": "Τι θα άλλαζες;",
        "OBJ_ASK": "Ποιο είναι ένα μικρό βήμα προς το «{goal}» που μπορείς να κάνεις αυτή την εβδομάδα; Πες πότε και πού αν μπορείς, ή πες «παράλειψη».",
        "OBJ_MORE": "Πες μου λίγο περισσότερα για το βήμα, σε μία-δυο προτάσεις.",
        "OBJ_CONFIRM": "Έτσι θα έγραφα το βήμα: «{step}»\nΣου ταιριάζει; Απάντησε ναι, ή πες μου τι να αλλάξω.",
        "OBJ_REWARD": "Διάλεξε μια μικρή ανταμοιβή για όταν το κάνεις (έναν καφέ, μια βόλτα, ένα επεισόδιο), ή απάντησε κανένα.",
        "OBJ_REWARD_NOTE": " (ανταμοιβή: {reward})",
        "OBJ_SAVED": "Αποθήκευσα το βήμα της εβδομάδας: «{step}»{reward}. Πες μου πώς πάει· θα σε ρωτήσω σε μερικές μέρες όταν τα πούμε.",
        "OBJ_SKIPPED": "Κανένα πρόβλημα. Γράψε /step όταν είσαι έτοιμος να διαλέξεις βήμα.",
        "OBJ_FULL": "Έχεις ήδη 3 ανοιχτά βήματα. Πες μου πρώτα πώς πήγαν αυτά.",
        "FU_ASK": "Πώς πήγε αυτό: «{step}»;\nΑπάντησε 1 = έγινε, 2 = κατά μέρος, 3 = όχι ακόμα.",
        "FU_AGAIN": "Απάντησε 1 (έγινε), 2 (κατά μέρος) ή 3 (όχι ακόμα).",
        "FU_BARRIER": "Τι εμπόδισε;\n1 = ξέχασα\n2 = δεν ήξερα πώς\n3 = μπερδεύτηκα\n4 = το απέφευγα\n5 = δεν είχα κίνητρο\nΑπάντησε με αριθμό ή πες το με δικά σου λόγια.",
        "FU_LATER": "Εντάξει, θα το ξαναδούμε.",
        "CRISIS": (
            "Λυπάμαι πολύ που νιώθεις έτσι, και χαίρομαι που μου το είπες. Μετράς και δεν χρειάζεται να το αντιμετωπίσεις μόνος ή μόνη. "
            "Αν υπάρχει πιθανότητα να πράξεις σύμφωνα με αυτές τις σκέψεις ή βρίσκεσαι σε κίνδυνο, κάλεσε τώρα τον τοπικό αριθμό έκτακτης "
            "ανάγκης (112 στην ΕΕ) ή πήγαινε στο κοντινότερο τμήμα επειγόντων. Αν μπορείς, πες το σε κάποιον που εμπιστεύεσαι και σκέψου "
            "να μιλήσεις με επαγγελματία ψυχικής υγείας ή με μια γραμμή βοήθειας στη χώρα σου.{help}\n\n"
            "Είσαι ασφαλής αυτή τη στιγμή; Είμαι εδώ και μπορούμε να συνεχίσουμε να μιλάμε."
        ),
        "INTAKE_INTRO": "Για να σε βοηθήσω σωστά, θα σου κάνω μερικές γρήγορες ερωτήσεις (περίπου 6). Μπορείς να παραλείψεις όποια θες γράφοντας «παράλειψη», ή να πεις «αρκετά» για να σταματήσουμε.",
        "INTAKE_Q_why": "Πρώτα: τι σε έκανε να ψάξεις υποστήριξη αυτή την περίοδο;",
        "INTAKE_Q_tried": "Τι έχεις ήδη δοκιμάσει και τι σε βοήθησε, έστω και λίγο;",
        "INTAKE_Q_obstacle": "Τι σε δυσκολεύει περισσότερο; Για παράδειγμα: να ξεκινήσεις πράγματα, ο χρόνος, η οργάνωση, η συγκέντρωση, να θυμάσαι, το κίνητρο, ο ύπνος ή το άγχος. Με δικά σου λόγια.",
        "INTAKE_Q_strength": "Σε τι είσαι καλός ή καλή, ή τι σε έχει βοηθήσει να τα καταφέρνεις στο παρελθόν;",
        "INTAKE_Q_rhythm": "Πώς είναι μια συνηθισμένη μέρα σου; Πότε ξυπνάς και κοιμάσαι, και τι πιάνει το μεγαλύτερο μέρος της μέρας σου;",
        "INTAKE_Q_mood": "Τελευταία: πώς νιώθεις τον τελευταίο καιρό, σε διάθεση και άγχος; Αν ήταν βαρύς, είναι εντάξει να το πεις.",
        "INTAKE_ACK": "Ευχαριστώ.",
        "INTAKE_DONE": "Ευχαριστώ, αυτό βοηθά πολύ. Κράτησα ένα σύντομο προφίλ (δες /notes). Να πώς δουλεύουμε: πες μου ό,τι σε απασχολεί όποτε θέλεις, και θα ορίζουμε έναν στόχο και ένα μικρό βήμα κάθε εβδομάδα.",
        "INTAKE_STOPPED": "Εντάξει, σταματάμε εδώ. Ό,τι μου είπες το κράτησα (δες /notes). Πες μου ό,τι σε απασχολεί όποτε θέλεις, γράψε /goal όταν θες να ορίσεις στόχο, ή /intake για να συνεχίσεις τις ερωτήσεις.",
        "INTAKE_MOOD_HEAVY": "Ευχαριστώ που μου το είπες. Ακούγεται βαρύ. Το coaching βοηθά στο πρακτικό κομμάτι, αλλά δεν αντικαθιστά τη θεραπεία: αν κρατά καιρό, σκέψου να μιλήσεις και με γιατρό ή ψυχολόγο. Είμαι εδώ έτσι κι αλλιώς.",
        "PROFILE_HEADER": "Για σένα (από την πρώτη μας κουβέντα)",
        "PROFILE_why": "Γιατί ήρθες",
        "PROFILE_tried": "Τι έχεις δοκιμάσει",
        "PROFILE_obstacle": "Τι σε δυσκολεύει",
        "PROFILE_strength": "Δυνατά σου σημεία",
        "PROFILE_rhythm": "Η μέρα σου",
        "PROFILE_mood": "Διάθεση",
        "TZ_ASSUMED": "Εντάξει. Εκεί που είσαι είναι τώρα {time}, σωστά;",
        "TZ_VERIFIED": "Τέλεια, ευχαριστώ.",
        "TZ_ASK_TIME": "Συγγνώμη γι' αυτό. Τι ώρα είναι εκεί που είσαι αυτή τη στιγμή; Για παράδειγμα 15:30 ή 3:30μμ.",
        "TZ_FIXED": "Ευχαριστώ, το διόρθωσα. Εκεί που είσαι είναι τώρα {time}.",
        "TZ_TIME_RETRY": "Δεν μπόρεσα να το διαβάσω. Πες μου την ώρα όπως 15:30 ή 3:30μμ, ή το όνομα της πόλης σου.",
        "TZ_GIVE_UP": "Κανένα πρόβλημα, το αφήνω προς το παρόν. Όρισέ την όποτε θες με /timezone Europe/Athens· τα check-in ξεκινούν μόλις οριστεί.",
        "CHECKINS_ENABLED": "Θα σε ρωτάω αφού περάσουν περίπου {minutes} λεπτά ησυχίας, και πιο αραιά αν δεν απαντάς. Με /interval off σταματούν, με /interval 60 τα αραιώνεις.",
        "GOAL_ASK_INTAKE": "Ανέφερες: «{obstacle}». Ας το κάνουμε στόχο. Τι θα ήθελες να αλλάξεις πρώτο; Αρκεί μία πρόταση, ή πες «παράλειψη».",
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
            "/goals - your goals and this week's steps\n"
            "/goal - set a new goal\n"
            "/step - pick this week's step\n"
            "/progress - this week's progress note\n"
            "/toolbox - what has worked for you\n"
            "/language <el|en> - language\n"
            "/notes - see what I remember about you\n"
            "/intake - redo the getting-to-know-you questions\n"
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
        "AGREED": "Thanks, you're set. Tell me what brought you here. Check-ins start once I know your timezone. /help lists the commands.",
        "DELETE_CONFIRM": "This permanently erases everything I have about you: chat history, notes, goals, objectives, toolbox and reminders. Send /deletedata confirm to go ahead.",
        "DELETED": "Done. Everything I had about you has been erased. You're welcome to start fresh any time.",
        "RATE_LIMITED": "You're sending messages very fast. I'll pause for a few minutes so I can keep up.",
        "EMPTY_REPLY": "I'm here. Want to tell me what's going on?",
        "CHECKIN_ORDER": "The start hour must be earlier than the end hour (for example /morning 9 and /evening 21).",
        "NEED_TZ": "First tell me your timezone so check-ins arrive at sensible times: /timezone Europe/Athens (or just tell me your city).",
        "TZ_ASK": 'By the way, where do you live? Tell me your city (or country) and I\'ll set your timezone, so reminders and check-ins land at the right time. You can also say "skip".',
        "TZ_MULTI": "{country} spans several timezones. Which city are you closest to?",
        "TZ_RETRY": 'I couldn\'t work out a timezone from that. Try a bigger nearby city, or the Area/City form like Europe/Athens, or say "skip".',
        "TZ_SKIPPED": "No problem. You can set it any time with /timezone Europe/Athens; check-ins start once it's set.",
        "GOAL_ASK": "Let's set a goal together. What's one thing you'd most like to change or get better at? A sentence or two is plenty, or say \"skip\".",
        "GOAL_MORE": "Tell me a little more, in a sentence or two.",
        "GOAL_CONFIRM": "Here's how I'd write that as a goal: \"{goal}\"\nDoes that fit? Reply yes, or tell me what to change.",
        "GOAL_SAVED": 'Goal saved: "{goal}"',
        "GOAL_SKIPPED": "No problem. Say /goal whenever you want to set one.",
        "GOAL_FULL": "You already have the maximum of 4 goals. Finish or drop one before adding another.",
        "REVISE": "What would you change?",
        "OBJ_ASK": 'What\'s one small step toward "{goal}" you could take this week? Say when and where if you can, or say "skip".',
        "OBJ_MORE": "Tell me a little more about the step, in a sentence or two.",
        "OBJ_CONFIRM": 'I\'d write the step like this: "{step}"\nDoes that work? Reply yes, or tell me what to change.',
        "OBJ_REWARD": "Pick a small reward for when you do it (a coffee, a walk, an episode of something), or reply none.",
        "OBJ_REWARD_NOTE": " (reward: {reward})",
        "OBJ_SAVED": 'Saved your step for this week: "{step}"{reward}. Tell me how it goes; I\'ll ask about it in a couple of days when we talk.',
        "OBJ_SKIPPED": "No problem. Say /step when you're ready to pick a step.",
        "OBJ_FULL": "You already have 3 open steps. Tell me how those went first.",
        "FU_ASK": 'How did this go: "{step}"?\nReply 1 = done, 2 = partly, 3 = not yet.',
        "FU_AGAIN": "Please reply 1 (done), 2 (partly) or 3 (not yet).",
        "FU_BARRIER": "What got in the way?\n1 = I forgot\n2 = I didn't know how\n3 = I got confused about it\n4 = I was avoiding it\n5 = I wasn't motivated\nReply with a number, or tell me in your own words.",
        "FU_LATER": "Okay, we'll come back to it.",
        "CRISIS": (
            "I'm really sorry you're feeling this way, and I'm glad you told me. You matter, and you don't have to face this alone. "
            "If you might act on these thoughts or you're in danger, please call your local emergency number now (112 in the EU) or go to "
            "the nearest emergency department. If you can, tell someone you trust how you're feeling, and consider reaching out to a "
            "mental-health professional or a helpline in your country.{help}\n\n"
            "Are you safe right now? I'm here, and we can keep talking."
        ),
        "INTAKE_INTRO": 'To help you properly, I\'d like to ask a few quick questions (about 6). You can skip any by saying "skip", or say "enough" to stop.',
        "INTAKE_Q_why": "First: what made you look for support at this point?",
        "INTAKE_Q_tried": "What have you already tried, and did anything help, even a little?",
        "INTAKE_Q_obstacle": "What gets in your way most? For example: starting things, time, organising, focus, remembering, motivation, sleep or stress. Your own words are fine.",
        "INTAKE_Q_strength": "What are you good at, or what has helped you get things done in the past?",
        "INTAKE_Q_rhythm": "What does a typical day look like: when do you wake up and go to bed, and what takes most of your day?",
        "INTAKE_Q_mood": "Last one: how have you been feeling lately, mood and stress wise? If it has been heavy, it's okay to say so.",
        "INTAKE_ACK": "Thanks.",
        "INTAKE_DONE": "Thanks, that helps a lot. I've saved a short profile (see /notes). Here's how this works: tell me what's on your mind any time, and we'll set a goal and one small step each week.",
        "INTAKE_STOPPED": "Okay, we'll stop here. What you told me is saved (see /notes). Tell me what's on your mind any time, send /goal when you want to set a goal, or /intake to carry on with the questions.",
        "INTAKE_MOOD_HEAVY": "Thank you for telling me. That sounds heavy. Coaching can help with the practical side, but it isn't a substitute for treatment: if this has lasted a while, please consider talking to a doctor or therapist as well. I'm here either way.",
        "PROFILE_HEADER": "About you (from our first chat)",
        "PROFILE_why": "Why you came",
        "PROFILE_tried": "What you've tried",
        "PROFILE_obstacle": "What gets in your way",
        "PROFILE_strength": "Your strengths",
        "PROFILE_rhythm": "Your day",
        "PROFILE_mood": "Mood",
        "TZ_ASSUMED": "Got it. It's {time} where you are right now, right?",
        "TZ_VERIFIED": "Great, thanks.",
        "TZ_ASK_TIME": "Sorry about that. What time is it where you are right now? For example 15:30 or 3:30pm.",
        "TZ_FIXED": "Thanks, I've fixed it. It's {time} where you are now.",
        "TZ_TIME_RETRY": "I couldn't read that. Tell me the time like 15:30 or 3:30pm, or the name of your city.",
        "TZ_GIVE_UP": "No problem, I'll leave it for now. Set it any time with /timezone Europe/Athens; check-ins start once it's set.",
        "CHECKINS_ENABLED": "I'll check in after about {minutes} minutes of quiet, and less often if you don't reply. /interval off stops it, /interval 60 spaces it out.",
        "GOAL_ASK_INTAKE": 'You mentioned: "{obstacle}". Let\'s turn that into a goal. What would you most like to change first? A sentence is enough, or say "skip".',
        "UNSUPPORTED": "I can only read text messages for now. Could you type it out?",
    },
}
