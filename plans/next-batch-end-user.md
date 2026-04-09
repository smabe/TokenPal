# TokenPal Next Feature Batch -- End User Persona Analysis

**Persona:** Power user, 8-10 hours/day at the terminal. Runs TokenPal in a dedicated pane alongside tmux sessions. Finds it genuinely funny for the first 30 minutes and wants it to still be funny at hour 3.

---

## What Gets Old Fast

The single biggest fatigue vector is **repetitive trigger-response patterns**. If I switch to Firefox and TokenPal says "Oh, browsing again?" three times in an hour, I will kill the process. Specifically:

- **Same app, same joke structure.** "You opened Safari" observations with no variation are dead on arrival after the second time.
- **Frequency without novelty.** 15-second intervals are fine *if context actually changed*. If I've been in VS Code for 20 minutes straight, silence is better than forced commentary. The current "comment every ~15s when context changes" is correct in principle but the definition of "change" needs to be strict. Switching from one VS Code tab to another is not a context change worth commenting on.
- **Generic CPU/RAM observations.** "Your CPU is high" is not funny. It's never funny. It's what a monitoring tool says. Unless the quip ties it to *what I'm doing* ("Running `npm install` and your fans sound like a 747 -- bold choice"), raw metric commentary is filler.

## What Makes Me Smile at Hour 3

**Callbacks and pattern recognition.** This is the entire game. The things that stay funny are the ones that prove the buddy has been *paying attention*:

- "Back to Stack Overflow. Third time today. At some point you have to admit you don't know Python."
- "You closed Slack 4 minutes ago and just reopened it. You're not checking for anything new. You know that."
- Noticing I've been idle for 10 minutes then suddenly started typing furiously -- "Nap's over?"

The comedy lives in **observed behavioral patterns over time**, not in one-shot reactions to sensor readings.

## Sense Ranking by Entertainment Value

From most to least entertaining to add:

### 1. Clipboard (HIGHEST VALUE)
This is the richest comedy vein available. Knowing *what* I copied changes everything. TokenPal doesn't need to understand the content deeply -- just detecting patterns is gold:
- Copied a URL: "Sharing links now? Very productive."
- Copied the same thing twice: "You already copied that. It's still there."
- Copied a massive block of text: "That's a lot of text to steal from somewhere."
- Copied an error message: "Copying the error into Google. Classic."

**Privacy note:** This is also the sense most likely to make users uncomfortable. It needs an explicit opt-in, not opt-out. And TokenPal should never *repeat* clipboard contents in its comments -- just react to the *shape* of what was copied (length, URL vs code vs prose, repetition).

### 2. Idle Detection (HIGH VALUE)
Idle is comedy gold because the *return from idle* is the funniest moment. The buddy has been alone. It noticed. Possible behaviors:
- Short idle (2-5 min): No comment needed.
- Medium idle (5-15 min): "Oh, you're back." / One dry acknowledgment.
- Long idle (30+ min): "I've been sitting here. Alone. Thinking about what you said before you left. Which was nothing, because you never talk to me."
- Return-from-idle comments should reference what you were doing *before* you left: "You walked away mid-compile. Brave."

### 3. Music/Audio Detection (MEDIUM-HIGH VALUE)
Knowing what's playing adds personality-mirroring comedy:
- Genre shifts: "You went from lo-fi beats to death metal in 10 minutes. Everything okay?"
- Pausing music: "The silence is louder somehow."
- Same playlist on loop: "This is the third hour of this playlist. You know other music exists."

Implementation note: Spotify's API or `osascript` on macOS for Music.app. Don't try to analyze audio content -- just track play state, track name, and genre if available.

### 4. Screen Capture / OCR (MEDIUM VALUE, HIGH COMPLEXITY)
Being able to *see* what's on screen is powerful but expensive and creepy. Probably not worth the complexity for the entertainment gain. A lightweight version -- just detecting if a video is playing full-screen, or if a terminal has a wall of red error text -- could work. Full OCR is overkill for a quip generator.

### 5. Voice (LOW VALUE)
Listening to the user talk adds very little. TokenPal is a text-in-terminal buddy. Voice detection ("You just sighed") sounds fun in theory but is technically fragile, privacy-invasive, and the comedy payoff is marginal. Skip this entirely.

## Comment Frequency

The current ~15s cadence is a ceiling, not a target. Actual recommendation:

| Situation | Frequency |
|---|---|
| Rapid context switching (3+ app switches in 60s) | One comment per burst, not per switch |
| Steady work in one app | One comment every 2-5 minutes max, only if something interesting happens |
| Return from idle | Exactly one comment |
| Genuinely funny pattern detected (clipboard, callback) | Immediately, even if recent comment was made |
| Nothing interesting happening | **Silence.** Zero comments. |

The ratio should be roughly 60% silence, 30% reactive quips, 10% unsolicited observations. TokenPal should feel like it *chose* to speak, not like it's on a timer.

## Moods

Moods are worth adding but only if they shift based on *your* behavior, not randomly:

- **Bored:** You've been in one app doing nothing interesting for 20+ minutes. Comments get shorter and more deadpan.
- **Judgmental:** You've been on Reddit/Twitter/YouTube for a while during work hours. Peak sarcasm.
- **Impressed (rare):** You've been in a terminal or IDE for 2+ hours straight with no distractions. "Okay, I'll admit it. You're actually working." This should be *rare* -- if it fires too often it's meaningless.
- **Needy:** You've been ignoring TokenPal's comments (no interaction). "Fine. Don't acknowledge me. I'm used to it."
- **Chaotic:** Friday afternoon. Late night. CPU pegged. Everything is on fire. Comments get unhinged.

Moods should be visible in the ASCII art (different expressions/poses) and affect comment tone, not just content. Transitions should be gradual, not instant.

## Idle Behavior

When the user is away, TokenPal should NOT keep generating comments into the void. Instead:

- After 2 minutes of idle: TokenPal's ASCII art shifts to a "waiting" pose.
- After 10 minutes: "Sleeping" pose. Maybe a single `zzz` animation.
- After 30 minutes: Stays asleep. No output.
- On return: Wake-up animation + one callback comment.

The terminal should not have 50 unread quips when I come back from lunch. That's the fastest way to get killed with `ctrl+c`.

## Session Memory

This is the **second most important feature** after clipboard sensing. Without memory, TokenPal resets every session and every joke is isolated. With memory, it becomes a character.

What to remember:
- **App usage patterns by time of day.** "It's 2pm, which means... yep, there's Reddit."
- **Recurring behaviors.** "You always open Spotify right before you start coding. It's your little ritual."
- **Cross-session callbacks.** "Yesterday you mass-closed 14 tabs at 5pm. Let's see if today's any different." 
- **Running tallies.** "That's the 7th time you've opened the fridge app-- I mean Slack -- today."

What NOT to remember:
- Clipboard contents (privacy).
- Specific URLs or file paths (creepy).
- Anything that would be embarrassing if someone read the memory file.

Storage: simple JSON or SQLite, human-readable, stored locally, easy to delete. The user should be able to `cat` the memory file and not be horrified.

## The Line Between Funny and Annoying

The line is **consent and control**. Specifically:

1. **Volume control.** A simple `--chattiness` flag (1-5) that affects how often TokenPal speaks. Default 3.
2. **Mute command.** Typing something in the TokenPal pane (or a hotkey) to shut it up for N minutes.
3. **No comments during focus.** If the user is in a Do Not Disturb mode or a Pomodoro timer is running, TokenPal goes quiet.
4. **Never punch down.** Quips about the user's *behavior* (procrastinating, context-switching) are funny. Quips about the user's *competence* ("you don't know what you're doing") get old fast and feel mean. The LLM prompt needs a hard guardrail here.
5. **No repetition.** A rolling window of recent comments should prevent the same joke structure from appearing twice in an hour. This is non-negotiable.

## The ONE Killer Feature

**Session memory with cross-session callbacks.**

Everything else is incremental. Clipboard is great. Moods are nice. Idle behavior is polish. But the thing that transforms TokenPal from a novelty into something you actually keep running is when it says:

> "You've started every Monday this month by opening Twitter before your IDE. I'm not judging. Actually, I am."

That's the moment it stops being a toy and becomes *your* sarcastic companion. It's the difference between a random quip generator and a character that knows you. Every comedy writer knows: callbacks are the hardest laughs. TokenPal has a structural advantage here -- it literally watches you all day. Use that.

The memory doesn't need to be sophisticated. A simple log of `{timestamp, app, duration, mood}` entries with a weekly summary that gets fed into the prompt context is enough. The LLM will do the rest. The hard part isn't the AI -- it's curating what to remember and making the memory file small enough to fit in a 4B model's context window.

---

## Priority Stack for Next Batch

1. **Session memory + cross-session callbacks** -- the killer feature
2. **Clipboard sensing** (opt-in) -- richest new comedy input  
3. **Idle detection + return-from-idle comments** -- low effort, high payoff
4. **Mood system** -- makes the character feel alive
5. **Frequency tuning** (silence as default, speak only when interesting) -- prevents fatigue
6. **Chattiness control / mute** -- user agency prevents uninstalls
7. **Music detection** -- nice-to-have personality layer
8. Screen capture -- only if lightweight; skip full OCR
9. Voice -- skip entirely
