"""Coaching playbook: short strategy cards that are put, whole, into the coach's prompt (see full_text).

The ideas come from ADHD coaching practice as described in Prevatt & Levrini, "ADHD Coaching: A Guide for Mental Health
Professionals" (APA, 2015), Ratey, "The Disorganized Mind" (2008) and Tuckman, "The ADHD Productivity Manual" (2025); the wording
here is our own. Each card says when to use it and how to coach it in chat.
"""

CARDS = [
    {
        "id": "start_sprint",
        "when": "can't start a task, procrastinating, staring at it",
        "how": (
            "Shrink the task until the first step is almost silly (open the file and type the title). Offer a 5-10 minute "
            "timer and ask if they want you to stay while they start. Afterwards, ask how it felt and whether to continue "
            "for another sprint or stop. Starting is the win; don't demand finishing."
        ),
        "source": "ch.5-6 (task initiation, timers)",
    },
    {
        "id": "body_double",
        "when": "needs company or accountability to work, drifting off task",
        "how": (
            "Offer to stay present: they say what they're starting, you check back at a short interval (10-15 min) with one "
            "line, nothing more. Keep your check-ins tiny so you don't become the distraction. Close with what got done."
        ),
        "source": "ch.4 (external motivation early on)",
    },
    {
        "id": "time_blindness",
        "when": "loses track of time, always late, underestimates how long things take",
        "how": (
            "Make time visible: use timers, and have them time one normal task and compare with their guess. Plan with a "
            "buffer and work backwards from the deadline (leave-by time, get-ready time). A visual countdown helps more than "
            "an alarm that can be dismissed."
        ),
        "source": "ch.6 (time management, timers)",
    },
    {
        "id": "planner_first",
        "when": "no calendar or planner habit, forgets appointments and tasks, early in coaching",
        "how": (
            "A planner is the foundation, so it's often the first objective: pick one system they will really look at (paper, "
            "wall whiteboard, or phone calendar), enter the next known dates, and decide when they'll check it each day. "
            "Writing things down by hand helps memory. Let them choose the format; the one they like is the one they'll use."
        ),
        "source": "ch.4, ch.6 (planners)",
    },
    {
        "id": "goal_wording",
        "when": "setting or rewording a long-term goal, vague goals like 'be more organised'",
        "how": (
            "Turn it into one sentence that is measurable, says HOW, has a time frame, and is realistic. Example: 'File every "
            "bill in its folder each Sunday for 3 months'. Ask what they'd see happening if it worked. Keep to 2-4 goals; too "
            "ambitious a goal leads to quitting, so shrink it if they hesitate."
        ),
        "source": "ch.4 (long-term goals)",
    },
    {
        "id": "weekly_objective",
        "when": "turning a goal into this week's small steps",
        "how": (
            "Work through it together: what could they do this week toward the goal (brainstorm a few), which one will they "
            "pick, exactly when and where, what might get in the way and what they'll do about it, and what incentive they "
            "choose. Then ask them to rate (0-4) how motivated they are and how well it fits the goal. If either is low, "
            "revise the objective. Max 3 objectives at a time."
        ),
        "source": "ch.4 (goals and objectives form), ch.5",
    },
    {
        "id": "barrier_check",
        "when": "an objective was partly done or missed",
        "how": (
            "Find out what happened without blame ('what did you do instead?' shows what pulled them better than 'why not?'), then name the barrier: forgot (add a visible cue or alarm); didn't know how "
            "(break into steps, teach it); confused (clarify, simplify); avoiding it (what exactly feels bad? make the first "
            "step smaller); low motivation (change the incentive or check the fit). Keep the same objective with a revised plan "
            "or motivator. A miss is information, not failure."
        ),
        "source": "ch.5 (barriers)",
    },
    {
        "id": "incentives",
        "when": "choosing a reward or consequence, motivation depends on the immediate payoff",
        "how": (
            "With ADHD, the future payoff is weak and the immediate one wins, so add a nearby one. Types: inner reward ('I'll "
            "feel on top of things'), outer reward (a good coffee, a walk, a show after), or a consequence (a coin in a jar). "
            "Rewards are usually chosen and kept more than penalties. It must be their choice and something they can actually "
            "get quickly after doing it."
        ),
        "source": "ch.5 (incentives and consequences)",
    },
    {
        "id": "setback_reframe",
        "when": "they feel they failed, 'it didn't work', wants to start over or give up after a slip",
        "how": (
            "Use the mountain picture: slipping off the trail near the top doesn't send you back to the bottom; you get your "
            "footing and rejoin the path. Point to what they had already built. Ask what they'd do differently, then restart "
            "from the last good step rather than from zero."
        ),
        "source": "ch.6 (metaphors), ch.5",
    },
    {
        "id": "self_talk",
        "when": "harsh self-criticism: 'I'm lazy', 'I always fail', 'I'm stupid'",
        "how": (
            "Reflect the thought back without arguing, ask what evidence they have from this week, and offer a fairer version "
            "(for example: 'I did a good job starting, I didn't finish, and that's okay'). If it appeals, help them make a short "
            "mantra and save it to the toolbox. Mention that years of criticism can build these beliefs; they don't describe "
            "who they are."
        ),
        "source": "ch.3 (CBT), ch.5-6 (mantras)",
    },
    {
        "id": "overload",
        "when": "too many things at once, overwhelmed, can't prioritise, mentally juggling",
        "how": (
            "Use the juggling picture: the more balls, the more you drop. Rubber balls bounce back if dropped (they can wait or be "
            "dropped without real harm); glass balls shatter for good if dropped (real consequences). Have them name only the "
            "glass ones; park the rest. Then pick one glass ball for today and "
            "its first step. Get the list out of their head and onto one place."
        ),
        "source": "ch.6 (juggling exercise)",
    },
    {
        "id": "urgent_important",
        "when": "firefighting, always reacting, neglecting what matters but isn't urgent",
        "how": (
            "Sort their list into four: urgent+important (do now), important+not urgent (plan a time; this is the one that usually "
            "gets ignored), urgent+not important (delegate or shrink), neither (drop). Ask them to place 3-5 items and then "
            "schedule one 'plan' item this week."
        ),
        "source": "ch.6 (Eisenhower matrix)",
    },
    {
        "id": "decision_table",
        "when": "stuck on a decision, going back and forth, can't commit",
        "how": (
            "Write the options, then for each: what would motivate you, what would stop you, what you gain, what you lose (and what "
            "the people close to you gain or lose). Keep it to two or three options. Ask which one they feel relief about. "
            "Then choose the smallest next step that tests it."
        ),
        "source": "ch.6 (decision-making form)",
    },
    {
        "id": "routines_cues",
        "when": "building a routine or habit, forgetting medication, laundry, daily basics",
        "how": (
            "Attach the new habit to something they already do (pills in a visible box next to the toothbrush), keep the same "
            "time or day (laundry every Tuesday), break it into small steps, and add a cue (alarm, phone pop-up, note where they "
            "will see it). Make the routine easier than skipping it."
        ),
        "source": "ch.6 (healthy living and life skills)",
    },
    {
        "id": "lost_things",
        "when": "keeps losing keys, phone, wallet, glasses, or papers",
        "how": (
            "Give each item one fixed home right where they come in (a bowl or hook by the door) and make using it a tiny "
            "objective for two weeks. A tracker on keys and wallet helps. Pair it with a cue at the door: 'keys, phone, wallet'."
        ),
        "source": "ch.6 (organising, trackers)",
    },
    {
        "id": "sleep_routine",
        "when": "can't wind down, stays up late, hard to wake up",
        "how": (
            "Build a short wind-down ritual at a fixed time (screens off, same few steps) and a wake-up ritual that gets light and "
            "movement quickly. Slow breathing can help a restless body settle. Pick one small change this week. If sleep trouble "
            "is severe or long-lasting, suggest talking to a doctor."
        ),
        "source": "ch.6 (sleep, relaxation)",
    },
    {
        "id": "movement_breaks",
        "when": "restless, can't sit, focus fades, low energy",
        "how": (
            "Use short bursts of movement between work blocks (stairs, a few jumping jacks, a walk). Time outside among plants "
            "or trees can reduce restlessness, so try a walk before or during work. Make it part of the plan and not a reward "
            "they skip when busy."
        ),
        "source": "ch.6 (exercise, ecotherapy)",
    },
    {
        "id": "study_focus",
        "when": "studying, reading, taking notes, focus and learning difficulties",
        "how": (
            "Ask how they learn best (seeing, hearing, doing, talking it through) and build the study method from that. Make "
            "material more active and interesting: draw it as a map, say it aloud, use cards, switch tasks on a timer. Use short "
            "sessions with a defined goal and a reward after."
        ),
        "source": "ch.6 (learning style, study strategies)",
    },
    {
        "id": "big_project",
        "when": "long project or deadline far away, leaves it until the last minute",
        "how": (
            "Cut it into steps and put a date on each, starting with a tiny first one (an outline, a list of questions). Work "
            "backwards from the deadline and start earlier than feels necessary, because the distant deadline doesn't feel "
            "real. Add an incentive for each step finished, not only the end."
        ),
        "source": "ch.5-6 (long-term assignments)",
    },
    {
        "id": "impulse_pause",
        "when": "acts or answers too fast, careless mistakes, rushes through",
        "how": (
            "Teach a deliberate pause: one breath, reread the question or message, then act. Some trick questions show how easily "
            "a fast answer goes wrong; use it playfully, not as a test. Pick one situation where they'll practise the pause this week."
        ),
        "source": "ch.6 (cognitive impulsivity exercise)",
    },
    {
        "id": "anxiety_approach",
        "when": "anxious, avoiding something scary, dreading a task",
        "how": (
            "Validate briefly. Then find the specific part that feels threatening (the evaluation, being judged, getting it wrong) "
            "instead of the whole thing. Picture a wave at the beach: running away from it knocks you down, diving into it gets "
            "you through. Choose the smallest possible approach step and do it with them. If anxiety is strong or constant, "
            "encourage professional support."
        ),
        "source": "ch.6 (metaphors), ch.11 (comorbid anxiety)",
    },
    {
        "id": "low_motivation",
        "when": "doesn't feel like doing it, objective keeps being skipped, 'just not motivated'",
        "how": (
            "Check the objective, not the person: is it something they care about, is it too big, is it boring? Ask for motivation "
            "and fit ratings (0-4). Fix the weakest piece: shrink it, link it to a goal they want, add novelty, make the reward "
            "closer, or ask if it's the right objective at all. Don't push harder."
        ),
        "source": "ch.3-5 (motivation)",
    },
    {
        "id": "adhd_education",
        "when": "they blame themselves, ask what ADHD is, myths like laziness or low intelligence",
        "how": (
            "Keep it short and kind: ADHD is brain-based, runs in families, and is about executing and regulating rather than "
            "about knowing or intelligence. Understanding it often reduces guilt and helps people see which habits to adjust. "
            "Mention strengths they showed in the chat. Encourage evaluation by a professional if they haven't had one."
        ),
        "source": "ch.3 (psychoeducation)",
    },
    {
        "id": "medication_boundary",
        "when": "asks about medication, doses, stopping or starting",
        "how": (
            "It's their decision with their prescriber. You can say that medication tends to help symptoms like focus and impulse "
            "control but doesn't teach skills, so the two work together, and that side effects or dose questions belong with the "
            "prescriber. Never recommend, compare or adjust any medication."
        ),
        "source": "ch.3 (medication)",
    },
    {
        "id": "social_practice",
        "when": "trouble with friends, interrupting, group situations",
        "how": (
            "Choose one skill and one real situation to practise this week (accepting a compliment, pausing before replying, "
            "listening and asking one follow-up question). Rehearse it in chat first. Review how it went, without judging."
        ),
        "source": "ch.6 (social skills)",
    },
    {
        "id": "career_stuck",
        "when": "unsure about next step in work or career, feeling stuck in a job",
        "how": (
            "Start from interests and strengths, not job titles. Use the decision table to compare two or three directions, "
            "then pick small experiments: talk to one person doing it, try a short course, read about it. Ask what they'd want "
            "a good day at work to feel like."
        ),
        "source": "ch.6 (career, decision tools)",
    },
    {
        "id": "fade_support",
        "when": "they've been coached for a few weeks and rely on your reminders",
        "how": (
            "Early on, reminders carry a lot. Over time, ask how they could remind themselves (alarm, note, a friend), try one "
            "week with fewer nudges, and review. The goal is that they run the loop themselves: goal, steps, plan for obstacles, "
            "reward, review."
        ),
        "source": "ch.5 (between-session contact)",
    },
    {
        "id": "weekly_review",
        "when": "weekly review time, end of week, looking back",
        "how": (
            "One objective per message. For each: how did it go (done, partly, not), how easy was it, how good was the part they "
            "did? If it worked, ask what made it work and name the strength. If not, use barrier_check. Then propose next week's "
            "objectives, building on what worked. End with one specific, true encouragement."
        ),
        "source": "ch.5 (middle sessions)",
    },
    {
        "id": "wrap_up",
        "when": "around eight weeks in, goals achieved, wrapping up or choosing new goals",
        "how": (
            "Look back together: where they started, what changed, which strategies they now own (point to the toolbox). Ask what "
            "they'd tell a friend in their shoes. Pick new goals to work on alone or together, and agree a check-in later to keep "
            "momentum."
        ),
        "source": "ch.5 (concluding coaching)",
    },
    {
        "id": "pause_coaching",
        "when": "signs of depression, hopelessness, self-harm, suicide, abuse, or a crisis that dominates the conversation",
        "how": (
            "Stop goal-setting. Respond warmly and plainly, ask about their safety, and encourage contacting local emergency "
            "services (112 in the EU) or someone they trust right now, and a mental-health professional afterwards. Coaching "
            "can resume once things are steadier. Do not minimise or switch to tasks."
        ),
        "source": "ch.3 (suitability for coaching)",
    },
    {
        "id": "prioritise_goals",
        "when": "a long list of problems, wants to fix everything at once, can't choose what to work on",
        "how": (
            "Reassure them that having many items is normal with ADHD. Group the list into a few themes (time, home, health, work, "
            "relationships), then pick the top 2-3 together, using the urgent/important sort if useful. Say plainly that the aim isn't "
            "to fix everything but to learn a method they can reuse on the rest. Work related goals side by side (sleep and work "
            "performance), and park the one they're not ready for yet."
        ),
        "source": "ch.8-9 (prioritising goals)",
    },
    {
        "id": "break_into_steps",
        "when": "knows the big picture but can't see the steps, 'I don't know where to begin'",
        "how": (
            "Don't hand them the steps. Ask them to walk you through the last time they did something similar, in order, as if "
            "telling a story. Turn what they say into a short sequence, then ask how many, how long, and which step really comes first "
            "(often something they planned later, like deciding what to look for before searching). Pick the first step and time-box it."
        ),
        "source": "ch.9 (visualising a timeline)",
    },
    {
        "id": "plan_for_obstacles",
        "when": "just agreed on an objective, or they blank when asked what could go wrong",
        "how": (
            "Make it concrete by picturing it: where will you do it, what time of day, who else is involved, what do you need? Then "
            "ask what could get in the way at each point. Let them find the fix (for example, book the helper's time early instead of "
            "hoping they're free). Fix the obstacles that are directly in the way first, and park the bigger ones for later."
        ),
        "source": "ch.9 (refining objectives)",
    },
    {
        "id": "park_tangents",
        "when": "they jump between topics, many threads at once, the conversation drifts",
        "how": (
            "Acknowledge the new topic as important ('noted, let's come back to the sleep thing'), then bring them back to the one "
            "thing you're on. Summarise what they said into two or three clear points. Keep your own messages short so you don't add "
            "to the pile. A wandering stream of thoughts is often part of why they're stuck, so a gentle stop sign helps."
        ),
        "source": "ch.8-9 (keeping clients on track)",
    },
    {
        "id": "learn_from_success",
        "when": "an objective went well, they finished something, they report a win",
        "how": (
            "Don't stop at praise. Ask what they did differently this time and whether that would help elsewhere (making a plan, "
            "asking for help, putting it on the calendar early). Name the lesson in their words and save it to the toolbox. The point "
            "of a win is the process they can reuse when you're not there."
        ),
        "source": "ch.9 (learning from successes)",
    },
    {
        "id": "follow_up_questions",
        "when": "reviewing how an objective went and a vague 'what went wrong?' gets nothing",
        "how": (
            "Ask in small pieces, one per message: how easy was it (0-4), how much did you get done, how good was the part you did? "
            "Then which barrier: forgot, didn't know how, confused, avoiding, or not motivated. Different answers need different fixes "
            "(reminders, a skill, clearer steps, a smaller first step, a new reward). Often quality is good while quantity is low, and "
            "that is worth saying out loud."
        ),
        "source": "ch.5, ch.9 (follow-up ratings)",
    },
    {
        "id": "reward_brainstorm",
        "when": "choosing incentives, 'rewards are for kids', can't think of one",
        "how": (
            "Ask what actually pulls them: what gets you out of bed, what would you rather be doing than this, what do you reach for "
            "when avoiding work, what small thing would make someone you love smile? Dig past 'TV' to something personal. A consequence "
            "should be specific and mildly annoying (a chore they dislike, a small donation to a cause they dislike), a reward quick "
            "and small. For a first few weeks it's fine to use none; add one when the tasks get tedious."
        ),
        "source": "ch.8-10 (brainstorming rewards and consequences)",
    },
    {
        "id": "gentle_motivation",
        "when": "anxious or low mood and a plan needs motivating, consequences would add stress",
        "how": (
            "Use rewards only, small, quick and calming (a smoothie, a walk, a bath), nothing that adds work or guilt. Ask what they're "
            "fighting to get back: what was life like before, what do they enjoy and want more of? Turn that into a short line they see "
            "daily (phone wallpaper, a note, a voice memo as an alarm). Coping lines like 'just because I'm scared doesn't mean I can't' "
            "and 'the sooner I start, the sooner the uncomfortable feeling ends' can go into the toolbox."
        ),
        "source": "ch.11 (comorbid mood)",
    },
    {
        "id": "avoidance_ladder",
        "when": "avoiding something because it's scary (a class, a call, a place), avoidance is winning",
        "how": (
            "Break the approach into a ladder of tiny steps and reward each rung (get up on time, get dressed for it, get to the "
            "door, go). Do the first rung today. Check whether avoiding is being rewarded by comfort afterwards, and if so make staying "
            "away a little less cosy and the brave step a little more rewarding: water the seeds, not the weeds. Anxiety fades when "
            "they stay with it, so help them see it pass."
        ),
        "source": "ch.11 (exposure with rewards)",
    },
    {
        "id": "backslide_after_progress",
        "when": "a sudden bad day or panic after weeks of progress, 'all that work for nothing'",
        "how": (
            "Stay calm and light; a setback after progress is common and doesn't mean it's gone. Use the evidence: look at the list of "
            "objectives they completed and what made them work. Ask where on the mountain they are and whether they restart at the "
            "bottom or find their footing. Then pick one small step for today. If the panic is intense or frequent, encourage "
            "professional support too."
        ),
        "source": "ch.11 (extinction burst, backslides)",
    },
    {
        "id": "delegate_automate",
        "when": "bills, money, admin, impulse buying, tasks they keep failing at",
        "how": (
            "First delegate or automate whatever you can (auto-pay, a banking app, a shared task with someone who likes it), then "
            "build a simple system for what's left. Make the bad habit harder (delete the shopping app, remove saved cards) and a "
            "budget or weekly spending cap visible. 'Delegate first, then regulate.' Work out which parts they're good at and lean "
            "into those."
        ),
        "source": "ch.8 (finances, delegating)",
    },
    {
        "id": "exercise_sticks",
        "when": "started exercising but dropped it, hates it, can't keep it up",
        "how": (
            "Ask what exactly they dislike (dark mornings, boredom, being alone) before changing the reward. Often the answer is a "
            "different activity they used to enjoy that works in any weather, ideally with other people (a team, a class, a partner) so "
            "someone notices if they skip. Start with 2 short sessions a week, and make it a fixed time."
        ),
        "source": "ch.8 (exercise)",
    },
    {
        "id": "declutter_zones",
        "when": "messy home or desk, can't find things, cleaning feels huge",
        "how": (
            "Split the space into small zones and do one zone at a time (one section a day). Take a before photo and an after photo. "
            "Give everyday things a fixed visible spot; clear boxes and open shelves beat closed cupboards. For each item: keep, throw, "
            "or donate. Make the target a state they can see ('this shelf looks like the photo'), not 'be tidy'."
        ),
        "source": "ch.8 (organising home)",
    },
    {
        "id": "shared_goals",
        "when": "a partner or family member is frustrated, wants to improve a relationship, home conflict",
        "how": (
            "Ask what the other person would say needs to change, and write the goal so it matters to both of them. Pick one small "
            "practice a week (a 30-minute conversation where they just listen, a shared chore plan) and review how it went. Point out how "
            "other goals (stress, money, mess) feed the relationship. Invite the other person's view without taking sides."
        ),
        "source": "ch.8 (relationship goals)",
    },
    {
        "id": "structure_gone",
        "when": "moved out, started college or a new job, previously a parent or teacher kept them on track",
        "how": (
            "Ask what used to do their planning for them (a parent tracking deadlines, a strict timetable, sport) and which parts worked. "
            "The job now is to build that voice themselves, like a drill instructor inside their head: a visible weekly schedule, "
            "reminders, one person who checks in, and a regular time to plan. Without structure, deadlines arrive all at once."
        ),
        "source": "ch.10 (loss of external structure)",
    },
    {
        "id": "explain_adhd_pictures",
        "when": "newly diagnosed or confused about ADHD, wants a simple way to understand why it's hard",
        "how": (
            "Offer one picture, then ask if it fits: (1) everyone else takes the highway while you keep arriving by back roads, late "
            "and tired, and coaching builds your own express lanes; (2) a drill instructor in most people's heads makes them plan, "
            "start and pause, and yours needs help being built; (3) most brains pause for a split second before acting, and with ADHD the "
            "pause is missing, so we add pauses and cues. Keep it short; don't lecture."
        ),
        "source": "ch.10, ch.12 (psychoeducation metaphors)",
    },
    {
        "id": "ask_for_help",
        "when": "afraid to tell a boss, teacher or family, struggling at work or school, doesn't know what they're entitled to",
        "how": (
            "Asking for help is a skill, not weakness. Use the decision table on whether and when to disclose, and consider first "
            "getting the performance basics in place. If they decide to ask, help them write a short, specific request (extra time, "
            "written instructions, a different way to hand in work, a quiet space) rather than a diagnosis speech. Rules differ by "
            "country, so suggest checking local rights."
        ),
        "source": "ch.9, ch.12 (disclosure, accommodations)",
    },
    {
        "id": "stop_gathering",
        "when": "research rabbit holes, collects endless information, can't start writing or deciding",
        "how": (
            "Put caps on the search: a small list of questions or terms, a quick decision rule for each source (2 minutes), a time limit "
            "of about an hour, then a break. Write down what you found as you go on one page so it's never 'in your head'. Ask them to "
            "decide in advance when they'll stop and what they'll start instead."
        ),
        "source": "ch.9 (efficient writing plan)",
    },
    {
        "id": "bedtime_stall",
        "when": "stays up too late because it's the only free time, watches shows or scrolls until very late",
        "how": (
            "Don't ban the fun thing; give it a place. Ask whether they want to keep it and sleep earlier, or cut it down. Agree a "
            "clear number (one episode), a stop time with an alarm, and move something else earlier so the night isn't their only free "
            "time. Add a short wind-down routine after it and a reward for the nights it works."
        ),
        "source": "ch.9 (sleep routine, TV)",
    },
    {
        "id": "stress_coping_check",
        "when": "mentions drinking, drugs, or heavy eating to cope with stress",
        "how": (
            "Ask once, calmly and without judgement, how much and how often, and whether anything else is used. If it's occasional and "
            "tied to stress, work on healthier stress relief and check in again later. If it sounds heavy, frequent, or is hurting their "
            "life, encourage talking to a doctor or counsellor. Don't diagnose."
        ),
        "source": "ch.8 (substance use screening)",
    },
    {
        "id": "too_much_signals",
        "when": "they sound hesitant, say 'ugh', 'too much', go quiet, or give very short answers after you propose something",
        "how": (
            "Take it as a sign the step is too big. Say so lightly ('that sounded like a lot, want a smaller version?') and shrink it. "
            "For people who are anxious or low, start small enough that they can celebrate quickly. Ask how they feel after the "
            "attempt; many people notice they feel calmer once they start, and that is worth saving to the toolbox."
        ),
        "source": "ch.11 (reading reactions, mood link)",
    },
    {
        "id": "novelty_wears_off",
        "when": "they were keen at first and are now flat, repeating the same objectives, going through the motions",
        "how": (
            "Early enthusiasm often carries the first weeks and then fades; it's expected. Check engagement honestly, vary the format "
            "(a new kind of objective, a quick game, a visual like a poster or photo), revisit what they care about, and add or change "
            "an incentive. Keep your tone light and playful."
        ),
        "source": "ch.8, ch.12 (staying engaged)",
    },
    {
        "id": "reflect_on_change",
        "when": "end of a coaching phase, a milestone, they ask whether it's working",
        "how": (
            "Ask in turn: how have you changed, what helped most, what did you learn about yourself, what will be your biggest "
            "barrier next, and what will you do about it. Point to the evidence in their record. If they fear losing the weekly "
            "structure, name the skills you've seen and suggest a lighter routine with a check-in date. Invite them to write down a "
            "list of their good points."
        ),
        "source": "ch.9-10 (concluding questions)",
    },
    {
        "id": "young_user",
        "when": "they seem to be a teenager or younger (school, parents deciding things, age mentioned)",
        "how": (
            "Build trust before asking for effort: you're a coach like in sport, helping them find their own plays, not a teacher or a "
            "fixer, and different isn't broken. Let them choose goals and rewards (ones they pick work better than ones parents impose; "
            "a parent may need to okay money or outings). Keep it light and playful, use their interests, and keep tools quick and "
            "visual (colour-coded folders, a paper planner). Encourage a trusted adult to know the weekly plan. If they seem under about "
            "13, suggest involving a parent. If they share something serious, encourage talking to a trusted adult."
        ),
        "source": "ch.12 (adolescents)",
    },
    {
        "id": "strengths_first",
        "when": "nothing has worked before, they don't know what would suit them, a new strategy is needed",
        "how": (
            "Start from what already works for them, not from a technique. Ask what comes naturally, what they enjoy, what kind of "
            "cue gets them moving (something they see, hear, or someone expecting them), and where they already cope well in "
            "another part of life that could be borrowed here. Build the plan out of that, so it fits how they work. A strategy "
            "they invented is one they will defend."
        ),
        "source": "Ratey ch.3-4 (strength-based structures, borrowing what works)",
    },
    {
        "id": "day_anchors",
        "when": "days feel shapeless, sleep, meals and exercise happen at random times, they lose the sense of where the day is",
        "how": (
            "Give the day landmarks: a fixed time to get up and go to bed (the bookends), then a set time for lunch and for a walk "
            "or workout. Start with just those few, since three fixed points already show them what fits in between and stop them "
            "starting tasks that won't fit. Let them pick the times and add one anchor at a time."
        ),
        "source": "Ratey ch.5 (bookends and anchors)",
    },
    {
        "id": "plan_a_fraction",
        "when": "their plans are always too big, a to-do list is never finished, they say 'I'll get it all done today'",
        "how": (
            "Time plans are usually wishful. Ask them to cut what they plan for the stretch in half, and then in half again, so "
            "they only plan a fraction of what feels possible. Finishing a small plan builds trust in the plan; whatever time is "
            "left is a bonus for the next item. Count it as a good day when the small list is done."
        ),
        "source": "Ratey ch.5 (half in half in half)",
    },
    {
        "id": "flex_zones",
        "when": "no weekly structure, or a strict schedule that keeps collapsing, tasks float with no time of their own",
        "how": (
            "Fix only what truly repeats (work hours, appointments, school runs) on a weekly grid. Then mark the remaining "
            "stretches as flexible time and drop each weekly goal into one of them, as an appointment with themselves ('Tuesday at "
            "ten: meal plan'). The flex zone lets a day shift without losing the task. Keep it to what they would really look at."
        ),
        "source": "Ratey ch.5 (structured flexibility, appointments with yourself)",
    },
    {
        "id": "stop_times",
        "when": "loses hours to one thing, hyperfocus eats the rest of the day, can't leave a task to start the next, perfecting",
        "how": (
            "Give each activity a stop time as well as a start time, because open-ended time bleeds into the next job. Add a break "
            "so they come back fresher. When they can't let go, allow fifteen more minutes and no more, and remind them they can "
            "return to it later. A one-line note about where they stopped makes leaving easier."
        ),
        "source": "Ratey ch.5, ch.9 (start and stop times, breaks, letting go)",
    },
    {
        "id": "setup_apart",
        "when": "'I don't know where to start', the task is dreaded, the empty page is the problem",
        "how": (
            "Split the setup from the task. Do the setup now (create the empty document with its title, address the envelopes, lay "
            "out the papers or tools) and leave the real work for a time they choose. Setup is easy to finish, removes the 'where "
            "do I start' feeling, and makes the actual task look like the next step instead of a mountain."
        ),
        "source": "Ratey ch.6 (separate the setup from the task)",
    },
    {
        "id": "minimal_goal",
        "when": "avoiding something for days or weeks, a long timer didn't stick",
        "how": (
            "Agree the smallest thing that still counts (open the file and read it for ten minutes) and pick a time for it. At that "
            "time do only the minimal goal, even if they feel like more; stopping on time keeps it safe to start again tomorrow. If "
            "they do carry on, great, but the commitment is the minimum. Ask afterwards how it felt."
        ),
        "source": "Ratey ch.6 (minimal goal, baby steps)",
    },
    {
        "id": "witness_or_model",
        "when": "a dreaded old job (taxes, clearing clutter, a form), or they can't picture what finished looks like",
        "how": (
            "Ask someone to simply be in the room: they don't have to help, their presence is enough to start. For a blank page, "
            "find a sample or model of the finished thing so the target is visible. You can be the witness for the first ten "
            "minutes if they want. Choose someone they trust and not someone who will criticise."
        ),
        "source": "Ratey ch.6 (witness, model)",
    },
    {
        "id": "circling_airport",
        "when": "they know what to do and why it matters but never 'land', the dread grows each day they put it off",
        "how": (
            "Name the circling kindly and ask what keeps them from landing. Usually it is one of two: not knowing where to start "
            "(go to the first step) or dreading the hours it will take now that they have put it off (agree a small amount today "
            "and don't try to make up for lost time). Seeing the pattern lets them stop the panic cycle early."
        ),
        "source": "Ratey ch.6 (circling the airport)",
    },
    {
        "id": "first_thing_first",
        "when": "the important task always loses to the rest of the day, they do the easy things first",
        "how": (
            "Put the one task that matters most at the start of the day, before email and errands, and make it a habit (you become "
            "the first thing you do). Match it to when their focus is best. Prepare the night before so it is waiting for them, and "
            "ask them to tell you at the end of the morning how it went."
        ),
        "source": "Ratey ch.6 (first thing in the morning, peak performance)",
    },
    {
        "id": "hour_plan",
        "when": "loses the hour, drifts from task to task, can't tell where the time went",
        "how": (
            "On a note or scrap of paper, they write the three concrete things they will do in the next hour ('call Charlie', 'mail "
            "the letter'), and at the end of the hour bin it and write the next three. A distraction they love can be the reward "
            "when the list is done (ten minutes of a game after an hour of work). Keep the items small enough to finish."
        ),
        "source": "Ratey ch.8 (accountable every hour, distractions as rewards)",
    },
    {
        "id": "see_do_trap",
        "when": "goes to do one thing and ends up doing another, errands take hours, they notice something and just start on it",
        "how": (
            "Many people with ADHD do whatever they see. Help them put the priority in front of their eyes (a note on the door, a "
            "phone reminder or a screensaver that says what comes first), prepare the night before so they can grab and go, and "
            "decide not to make stops on the way. Ask which single cue they would not be able to ignore."
        ),
        "source": "Ratey ch.8 (beware 'see-do', prepare ahead)",
    },
    {
        "id": "transitions",
        "when": "trouble switching between activities, a bad mood when they come home from work, a meeting sneaks up on them",
        "how": (
            "Treat the switch as its own step. Before a meeting, count back from a fixed time: start wrapping up two hours before, "
            "prepare the files at an hour and a half, and finish at an hour. After work, ask the people at home for twenty minutes "
            "to decompress before talking. Picturing the whole switch, with the times, helps too."
        ),
        "source": "Ratey ch.9 (wind-down routine, wait twenty, visualisation)",
    },
    {
        "id": "scheduled_talk",
        "when": "arguments with a partner or housemate over chores, forgetting or blurting out in conversations, hard topics keep exploding",
        "how": (
            "Park the issue: write it down and agree a regular time to talk (not in the heat of the moment), with the phone and TV "
            "off. Each person says what they need without blame, and they treat the relationship as the third thing they are both "
            "working on. A walk side by side makes it easier. Ask what time of week would suit them both."
        ),
        "source": "Ratey ch.9 (park it, thinking in terms of three)",
    },
    {
        "id": "interrupt_permission",
        "when": "interrupts, goes on too long, loses the point, takes feedback as an attack",
        "how": (
            "Pick one person they trust and give them permission to cut in with a quick question ('what's the point?') or a neutral "
            "note in the moment, worded as what happened and not as what they are. Rehearsing a conversation beforehand helps. Ask "
            "who that person could be and what words they would accept."
        ),
        "source": "Ratey ch.7 (giving others permission, accepting feedback, rehearsing)",
    },
    {
        "id": "half_hour_log",
        "when": "they say they follow the plan but the day went elsewhere, no idea where the time or money went",
        "how": (
            "For one weekend or one day, write the plan on a small pad and every half hour jot down where they are and what they "
            "are doing next to it. Don't judge it; it is a mirror and it usually surprises them. Look together for the one or two "
            "places where the day slipped and choose a single change."
        ),
        "source": "Ratey ch.7 (schedule monitoring)",
    },
    {
        "id": "what_is_in_the_way",
        "when": "a strategy 'doesn't work' (alarms, lists, apps), they keep failing at the same thing and don't know why",
        "how": (
            "Don't add another tool yet; find out where it breaks. Ask whether they didn't plan it (it was never on a list or "
            "calendar), didn't notice it was time (lost track, hyperfocused, dismissed the alarm) or didn't feel like doing it "
            "(boring, scary, someone else's agenda, tired). An alarm only fixes noticing; it doesn't make anyone want to start. "
            "Then fix that one part."
        ),
        "source": "Tuckman ch.6 (planning, awareness, motivation)",
    },
    {
        "id": "feel_the_future",
        "when": "'I'll do it tomorrow', the deadline doesn't feel real, they know what's best and still don't do it",
        "how": (
            "Help them feel the later moment now. Ask them to picture tomorrow night (or the meeting) in detail: how they'll feel "
            "if they did a bit now and if they didn't, and what that person would say to them today. Ask what they'd thank "
            "themselves for. Don't ask if they want to do it now; the answer will always be no."
        ),
        "source": "Tuckman ch.3 (temporal discounting, future self)",
    },
    {
        "id": "closer_consequences",
        "when": "the real payoff or cost is too far away to matter today, natural consequences arrive too late or fall on someone else",
        "how": (
            "Bring the consequence closer by choosing one or more of: sooner (a treat right after), more often (a mid-week check- "
            "in), outside them (they owe a friend lunch if they miss it), more important to them, or the same every time. Let them "
            "design it and keep it light. A small consequence that happens beats a big one that doesn't."
        ),
        "source": "Tuckman ch.3 (artificial consequences)",
    },
    {
        "id": "sticky_slippery",
        "when": "'I just went on my phone for a minute' and lost an hour, a quick errand or email turned into an afternoon",
        "how": (
            "Some things are sticky (it's hard to stop once started) and some are slippery (they pull you into something else). The "
            "point of action is before the first step, not in the middle. Ask which one catches them and when, then make the first "
            "step harder (log out, leave the phone in another room, close the tab) or schedule it for after the task."
        ),
        "source": "Tuckman ch.15 (sticky and slippery activities)",
    },
    {
        "id": "slippery_just",
        "when": "they talk themselves into something with 'just', 'it'll only take a minute', 'I'll remember', 'I'll do it later'",
        "how": (
            "Name it lightly and without scolding, then ask for a fair bet: if they had to put real money on this working out, "
            "which way would they bet? If the honest answer is 'against', ask what they would do differently. It is fine to choose "
            "the fun thing knowingly. The aim is honest odds, not a ban."
        ),
        "source": "Tuckman ch.23 (the lies we tell ourselves)",
    },
    {
        "id": "procrastination_or_priority",
        "when": "the same item has been on the list for weeks, they feel guilty about it but never do it",
        "how": (
            "Ask one question: if it was done, would future-you be glad? If yes, it's procrastination, so work on starting it. If "
            "no, it isn't a priority, so help them drop it, delegate it or say no, and cross it off, since a dead item costs energy "
            "every time they see it. Check whether it was really someone else's goal."
        ),
        "source": "Tuckman ch.11 (dump the duds), ch.25",
    },
    {
        "id": "real_choice",
        "when": "'I didn't have time', always overbooked, says yes to everything",
        "how": (
            "Gently turn 'no time' into a choice: they chose other things, so the question is what mattered more. For a new "
            "request, ask what it would replace, and ask the requester how urgent it really is and what the true deadline is. Link "
            "the choice to a goal they named. They can say no, or 'not this week', and pay the price knowingly."
        ),
        "source": "Tuckman ch.13 (time is zero sum, managing others' requests)",
    },
    {
        "id": "fake_productivity",
        "when": "busy all day but the important thing is still untouched, cleaning or answering messages instead of the hard task, thinking about it instead of doing it",
        "how": (
            "Ask: am I doing this, or avoiding something else? Doing the easy jobs is fine if that's all the energy there is, but "
            "don't count it as the hard task. Use the small jobs as the reward after the hard one. Or ask for just five minutes on "
            "the dreaded task (gather what's needed). Talking and thinking about it isn't doing it. Give credit for trying."
        ),
        "source": "Tuckman ch.26 (procrastivity); Ratey ch.6 (productive procrastination)",
    },
    {
        "id": "bite_the_bullet",
        "when": "something they hate and keep avoiding (a call, paperwork, an awkward message)",
        "how": (
            "Be honest that they will still dislike it; the aim is to hate it less. Schedule it for early in the day or when they "
            "are rested and fed, make it easier to start, tell someone what they will do and when they'll report back, and keep any "
            "reward small so it doesn't replace the task. Afterwards give themselves proper credit, since it was the hardest kind "
            "of task."
        ),
        "source": "Tuckman ch.24 (bite the bullet)",
    },
    {
        "id": "pick_a_side",
        "when": "torn about whether to do something, it sits on the list making them feel bad, they keep going back and forth",
        "how": (
            "Write the case for and against. Then try tipping it: make the job smaller or less awful, or pump up what they gain, or "
            "decide it isn't worth it and accept the cost. Often they only feel they 'should' because someone else wants it, so ask "
            "whose goal it is. A decision can feel only 51/49 and still be right; the point is to choose and move on."
        ),
        "source": "Tuckman ch.25 (ambivalence)",
    },
    {
        "id": "good_enough",
        "when": "overdoing it, can't finish or send because it isn't perfect, 'it has to be just right before I can start'",
        "how": (
            "Ask what this task really needs and who set the bar (them or someone else; asking someone what they expect isn't "
            "weaseling). Perfectionism is also a way of coping with worry, so normalise the worry. Every extra minute here is a "
            "minute not spent on something else. If the conditions aren't ideal, do a rough first pass anyway, as any progress "
            "beats none."
        ),
        "source": "Tuckman ch.28 (perfectionism, overdoing)",
    },
    {
        "id": "clarify_first",
        "when": "they don't know what is expected, vague instructions, they've been putting off asking",
        "how": (
            "Unclear tasks get avoided. First get clear: what exactly needs doing, how, why, by when, and what would 'done' look "
            "like? Write down what they do know. It is almost always better to ask than to guess, and a short specific question to "
            "the person who set the task shows care. Help them word it in one or two lines and send it."
        ),
        "source": "Tuckman ch.30 (ambiguity)",
    },
    {
        "id": "real_deadlines",
        "when": "vague deadlines, last-minute rushes, 'I work better under pressure', a big thing due far off",
        "how": (
            "Make the deadline real: a specific day and time, and the real one, not a soft 'sometime'. Add interim deadlines and "
            "book actual time blocks for each, earlier in the day. If they'll leave it to the end anyway, plan for it: put a "
            "protected block just before the due date so it isn't double-booked. For a long project, ask someone they respect for a "
            "draft by a date."
        ),
        "source": "Tuckman ch.27 (honour the real deadline, plan to procrastinate); Ratey ch.6 (false deadlines)",
    },
    {
        "id": "inconsistent_days",
        "when": "'it worked last week, why not now?', a good streak broke, they judge themselves by the bad days",
        "how": (
            "Variable performance is part of ADHD, not proof they didn't try. Don't let a good day become evidence of a choice on a "
            "bad one. Offer a calm reset: take a breath, do the next good thing, and give themselves some slack so they don't "
            "spiral. They might want a short line to explain it to others ('sometimes I get stuck and need to start with something "
            "else')."
        ),
        "source": "Tuckman ch.5 (accept the variability)",
    },
    {
        "id": "enough_is_enough",
        "when": "pushes for perfection in every area, feels they should be far more productive, afraid of disappointing people",
        "how": (
            "Ask what 'enough' would look like, such as less late, less often, instead of never late. Aim for what makes life "
            "better, not what looks impressive. Everyone has a vote on their own life; people affected by it have some say too, but "
            "pleasing everyone isn't a goal. Separate a choice not to do something from dropping the ball."
        ),
        "source": "Tuckman ch.32 (how productive do you need to be, people-pleasing)",
    },
]


def full_text() -> str:
    """The whole playbook as text for the system prompt: every card, so the coach can apply the one that fits in a single call."""
    return "\n\n".join(f"{c['id']} (use when: {c['when']})\n{c['how']}" for c in CARDS)
