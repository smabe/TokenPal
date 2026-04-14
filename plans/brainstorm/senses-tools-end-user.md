# Brainstorm: New Senses, Slash Commands, Agent Tools (End-User POV)

North star: **Would this still be funny at hour 3?** I run TokenPal 8-10 hours a day. I don't need a second assistant — I need a roommate who notices patterns and roasts me for them. Comedy and callbacks over utility.

---

## Bucket 1: NEW PASSIVE SENSES

### 1. `calendar_awareness` — read-only local calendar (macOS EventKit / ICS file)
- Quip: "meeting in 4 minutes and you're still on Reddit. bold."
- Comedy: 5 | Privacy: 3 (titles are sensitive — hash/strip by default, only expose "meeting in N min")
- Callbacks: HIGH — "third standup this week you joined late"
- Fit: tension/dread material is gold. Must default to time-only, never title content.

### 2. `battery_and_power` sense
- Quip: "4% battery. you like living dangerously, or just forgetful?"
- Comedy: 4 | Privacy: 1
- Callbacks: MEDIUM — "you let it die twice this week"
- Fit: psutil already there, near-free. Classic sitcom material.

### 3. `network_vibes` — SSID change + rough latency to 1.1.1.1
- Quip: "new wifi. coffee shop arc begins."
- Comedy: 4 | Privacy: 2 (SSID is local-only, never logged)
- Callbacks: HIGH — "you've been to 'Blue Bottle' 6 times this month, just go to work"
- Fit: location-ish without GPS. Transition-only readings, like idle sense.

### 4. `filesystem_pressure` — downloads folder count + desktop clutter
- Quip: "247 files on your desktop. archaeologists will have questions."
- Comedy: 5 | Privacy: 2 (counts only, never names — sanitize aggressively)
- Callbacks: HIGH — "desktop grew by 30 files this week, congrats on the avalanche"
- Fit: observational, gently shaming, zero utility. Perfect.

### 5. `focus_decay` — tab/window count via app_awareness extension
- Quip: "you have 41 windows open. which one contains the actual work?"
- Comedy: 4 | Privacy: 2 (counts, no titles)
- Callbacks: MEDIUM — "peak chaos: Tuesday 3pm, 58 windows"
- Fit: extends existing sense cheaply. Power-user self-recognition.

### 6. `typing_cadence` — pynput keystroke RATE only (no content, ever)
- Quip: "steady rhythm for 40 minutes. are you okay? blink twice."
- Comedy: 3 | Privacy: 4 (keylogging is radioactive even if "just rates" — surface warning, off by default, never log per-key)
- Callbacks: MEDIUM — flow-state streaks, doom-scrolling lulls
- Fit: risky. Only ship if UX makes it obviously rate-only, and chat log never shows raw counts. Probably defer.

### 7. `ambient_noise` — macOS mic RMS only, never audio bytes
- Quip: "room's loud. headphones on, or is your roommate winning an argument?"
- Comedy: 3 | Privacy: 5 (mic permission is a deal-breaker for most users)
- Callbacks: LOW
- Fit: REJECT. Violates the "voice/STT is creepy" bar even without STT. Mic-access alone is a trust cliff.

---

## Bucket 2: USER-TRIGGERED SLASH COMMANDS

### 8. `/roast` — one-shot maximum-snark mode on whatever you're doing
- Quip: *reads context* "opening the same Jira ticket for the fourth time today — what exactly are you hoping changes?"
- Comedy: 5 | Privacy: 1 (uses existing senses)
- Callbacks: HIGH — output can be logged as "best roasts" replay material
- Fit: gives the user a deliberate pressure valve. Better than waiting for the brain's cooldown.

### 9. `/vibecheck` — buddy summarizes your last hour in-character
- Quip: "last hour: 6 app switches, 2 coffee breaks, zero commits. we call that 'productive thinking.'"
- Comedy: 4 | Privacy: 1
- Callbacks: MEDIUM — the summary itself becomes a callback seed
- Fit: pairs beautifully with existing `productivity` sense. Textual-first.

### 10. `/excuse` — generate a plausible excuse in-voice for whatever you're late on
- Quip: "tell them the CI pipeline gained sentience and demanded a union."
- Comedy: 5 | Privacy: 1
- Callbacks: LOW (one-shot jokes, user remembers them not the buddy)
- Fit: pure comedy utility, no external deps. High replay value.

### 11. `/rate <thing>` — buddy rates a file/PR/commit message in-character
- Quip: "commit message 'fix stuff' — bold minimalism. 3/10. my grandma writes more."
- Comedy: 4 | Privacy: 2 (reads one file the user pastes — no auto-scan)
- Callbacks: HIGH — "remember when you tried 'fix stuff' on Tuesday?"
- Fit: user-initiated so no creep factor. Uses existing `/gh`-style daemon pattern.

### 12. `/dearbuddy <vent>` — user vents, buddy replies as character, NOT saved to disk
- Quip: "boss said that? bold. file it under 'problems beyond my pay grade.'"
- Comedy: 4 | Privacy: 1 (memory-only, conversation-session pattern already exists)
- Callbacks: LOW by design (vents shouldn't come back)
- Fit: extends existing conversation-session. Therapist roleplay without claiming to be a therapist.

---

## Bucket 3: AGENT TOOLS THE LLM CALLS ITSELF

*Note: gemma4 + Ollama streaming tool-call parser is buggy as of 2026 — keep these tools single-shot, no chaining, degrade gracefully if parse fails.*

### 13. `calculate(expr)` — safe arithmetic evaluator
- Quip (LLM sees user ask "how much is $14/hr over 3 months") → tool call → "$7,280 before taxes eat half of it. welcome to adulthood."
- Comedy: 3 | Privacy: 1
- Callbacks: LOW
- Fit: cheap win, sandboxed `ast.literal_eval` on parsed expressions. Makes the buddy seem sharper without actual intelligence.

### 14. `lookup_defn(word)` — local dictionary (WordNet via NLTK, offline)
- Quip: "you keep saying 'idempotent' — it means 'do it twice, get the same result.' now stop showing off."
- Comedy: 4 | Privacy: 1 (fully offline)
- Callbacks: HIGH — "that word you googled Tuesday, still don't know what it means, do you?"
- Fit: lets the buddy correct the user mid-quip. Sitcom gold. Offline so no API pressure.

### 15. `time_since(event)` — query MemoryStore for "how long since X"
- Quip: "you haven't committed to that repo in 11 days. the file you opened just then won't save itself."
- Comedy: 5 | Privacy: 1 (all local SQLite)
- Callbacks: EXTREMELY HIGH — this IS the callback engine
- Fit: the most TokenPal-native tool in this list. Turns pattern detection into active roasting.

### 16. `streak_check(app_or_activity)` — MemoryStore query for streaks
- Quip: "17 days straight opening Slack before 9am. hostage situation or hustle?"
- Comedy: 5 | Privacy: 1
- Callbacks: EXTREMELY HIGH
- Fit: overlaps with cross-session callbacks, but invocable mid-quip for precision burns.

### 17. `roll_dice(sides)` / `coin_flip()` — tiny RNG tool
- Quip: "I rolled a d20 on whether you should merge this. natural 1. don't."
- Comedy: 3 | Privacy: 1
- Callbacks: LOW
- Fit: silly, cheap, embraces the buddy's "opinion" shtick without needing real reasoning.

---

## Top 5 (ranked by "still funny at hour 3" × callback potential)

1. **`time_since()` agent tool** — the callback engine made explicit. Every quip that uses it is pre-sharpened with receipts.
2. **`filesystem_pressure` sense** — desktop clutter is universally shameful, counts are privacy-safe, weekly deltas are endless material.
3. **`calendar_awareness` sense** — dread and late-arrivals are infinite comedy fuel; must be title-blind by default.
4. **`/roast` slash command** — user-triggered pressure valve. Respects the brain's cooldown while still letting me summon snark on demand.
5. **`lookup_defn()` agent tool** — offline, safe, and perfectly weaponizable for correcting the user mid-sentence.

## Bottom of the list

- **Ambient noise / mic** — REJECT. Trust cliff.
- **Typing cadence** — DEFER. Even rate-only keylogging reads as creepy; not worth the UX debt.
- **Dice roll tool** — ship only if bundled with others; too thin alone.
