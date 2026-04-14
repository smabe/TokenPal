# Brainstorm: New Senses, Slash Commands, and Agent Tools

North star: the buddy's strength is pattern recognition over time, not one-shot reactions. Filter every idea through: *does this generate callback material a week from now, or just a single quip that gets stale by Tuesday?*

---

## 1. NEW PASSIVE SENSES

### 1.1 `calendar_awareness` — local calendar peek (macOS EventKit / ICS file)
Reads next event title (optionally redacted to duration + attendee count) and "time until next meeting."
- **Material**: callback gold. "Third 'quick sync' this week — define quick." Pairs with productivity sense for "you have 14 min before your 1:1 and you're in Reddit."
- **Quips**: "8 minutes till standup. Plenty of time to stare into the void." / "Back-to-back till 4. Rest in peace." / "Your calendar says 'focus time.' The calendar lies."
- **Callbacks**: YES. Pattern: meeting density by weekday, recurring-meeting fatigue, pre-meeting doom-scrolling ritual.
- **Composite**: `calendar + app_awareness` ("3 min before meeting, opened Twitter"), `calendar + idle` ("meeting in 2 min, already idle 15").
- **Easter egg candidate**: "calendar empty on a Monday 10am" → bypass LLM with a stock "suspicious" line.
- **Half-life**: long. Meetings are fractally annoying.

### 1.2 `battery_and_power` — psutil battery + power source transitions
Laptop plugged/unplugged, battery % on threshold crossings, "on battery for 2h 14m."
- **Material**: one-shot mostly, but *transitions* are callback-able ("you always unplug right before lunch").
- **Quips**: "3% and you haven't moved. Bold." / "Unplugged. Living dangerously." / "Been on charger for 6 hours. The battery is crying."
- **Callbacks**: medium. "You panic-charge at 18% every time" is reusable.
- **Composite**: `battery + idle` ("4% and idle — you're gonna lose work"), `battery + late-night` ("2am, 8%, please sleep").
- **Half-life**: medium-short. Three good jokes, then repetition.

### 1.3 `terminal_activity` — shell history tail / last command class
Classifies recent commands (git, test, lint, rm, sudo) without reading contents. Cross-platform via zsh/bash history file mtime + last-line sniff.
- **Material**: RUNNING GAG goldmine. "You've run `pytest` 14 times. Try reading it." "Fourth `git status` in a minute. It didn't change."
- **Quips**: "`rm -rf` detected. Godspeed." / "You `cd`'d into the same dir three times. You live here now." / "npm install — grab a coffee, or two."
- **Callbacks**: YES. Command-frequency patterns by day, "Monday is always a `brew upgrade` morning."
- **Composite**: `terminal + git sense` ("committed after 22 `git status`es"), `terminal + hardware` ("ran build, fans are screaming").
- **Privacy**: sniff command VERB only, never args. Sensitive-command list silent (ssh, scp, curl with tokens).
- **Half-life**: long. Devs repeat themselves forever.

### 1.4 `network_weather` — ping / DNS health, not traffic inspection
Simple reachability + latency to 1.1.1.1. Detects outages, wifi switches via default gateway MAC.
- **Material**: one-shot with occasional callback ("third outage this week on home wifi").
- **Quips**: "Internet's doing that thing again." / "400ms ping. Are you on hotel wifi?" / "DNS just died. RIP productivity."
- **Callbacks**: medium. Wifi-location inference ("you're at the cafe again").
- **Half-life**: short. Outages are rare enough to stay fresh, but the joke space is small.

### 1.5 `focus_mode` — macOS Focus/DND state, Windows Focus Assist
Detects when user has DND on.
- **Material**: compositional, not standalone.
- **Quips**: "DND's on. Cute." / "Focus mode + Slack. Sure."
- **Callbacks**: YES. "You turn DND on every afternoon at 3 and then still answer Slack."
- **Composite**: strong with `app_awareness` (DND on + messaging app open = hypocrisy gag).
- **Half-life**: medium. Depth comes from the hypocrisy angle.

### 1.6 `display_state` — external monitor connect/disconnect, resolution changes
- **Material**: mostly one-shot. Transitions are funny once.
- **Quips**: "Monitor unplugged. You're going somewhere." / "Four displays. You're overcompensating."
- **Callbacks**: weak. Borderline filler. **Demote unless paired with `calendar` (leaving desk before a meeting).**
- **Half-life**: short. Skip.

### 1.7 `input_cadence` — keystroke/mouse rate (buckets only, no content)
Typing speed, mouse-idle vs keyboard-idle distinction. pynput already in use for idle sense.
- **Material**: callback seeds. "You type fast on Tuesdays, slow on Mondays." "Mouse-only for 20 min — reading or pretending?"
- **Quips**: "45 wpm and rising. Something's wrong or very right." / "You've been mousing without typing for 12 min. What are we doing?"
- **Callbacks**: YES. Typing cadence is a rich temporal signal.
- **Privacy**: rate only, never keys. Already have precedent with idle.
- **Half-life**: long IF framed as pattern detection, short if framed as one-shot.

### 1.8 `finder_churn` — downloads folder size delta, desktop file count (macOS/Windows)
Polls every 5 min. Detects "12 new files on desktop today."
- **Material**: callback fodder. "Downloads folder gained 2GB this week. Spring cleaning?"
- **Quips**: "Desktop is at 47 icons. A cry for help." / "Another `Screenshot 2026-...png`. That's 19 today."
- **Callbacks**: YES. Weekly digest energy.
- **Half-life**: medium-long.

---

## 2. USER-TRIGGERED SLASH COMMANDS

### 2.1 `/roast` — buddy roasts current app/session on demand
Pulls last 20 min of sense readings, asks LLM for a pointed roast.
- **Material**: the user LOVES this once a day. Callback-friendly because the roast references real patterns.
- **Quips**: "You've been in Slack for 2 hours. Slack is not a job." / "Six tabs of the same Stack Overflow question. Pick one."
- **Half-life**: long — fuel regenerates as user's day changes.

### 2.2 `/defend` — opposite: buddy writes a one-line justification for whatever you're doing
- **Material**: running gag. "Reddit is research." "The nap is strategic."
- **Callbacks**: YES. Tracks "defenses invoked" as a running tally.
- **Half-life**: medium.

### 2.3 `/wrapup` — end-of-day summary in character
Pulls MemoryStore day-aggregate, generates a 3-line recap in voice.
- **Material**: callback seed for NEXT day ("yesterday you said you'd stop at 5. It is 5:47.").
- **Half-life**: very long. One per day = never stale.

### 2.4 `/verdict <decision>` — user asks buddy to pick
"/verdict ship it or nap" → buddy chooses with a reason in voice.
- **Material**: running gag fodder (track verdicts and callback when user ignores them).
- **Half-life**: long.

### 2.5 `/dare` — one small productivity dare tied to current state
"You've had Spotify open 3h. Dare: silence for 10 min."
- **Half-life**: medium. Could get nagging — gate behind mood.

### 2.6 `/alibi` — invents a plausible excuse for your last idle stretch
Pure running gag. Pairs with `idle` sense history.
- **Half-life**: long if creative.

---

## 3. AGENT TOOLS THE LLM CALLS ITSELF

Tool-calling is already wired (Ollama OpenAI-compat). Give the model a *small* tool menu it can invoke mid-quip.

### 3.1 `lookup_recent_callback(topic)` — query MemoryStore for prior pattern
Model calls this when it wants to reference a behavioral pattern without hallucinating.
- **Why**: prevents the "you always do X" lie when the model has no evidence.
- **Callback multiplier**: huge — this is the tool that MAKES callbacks trustworthy.

### 3.2 `count_app_time_today(app)` — precise numbers for roasts
Model asks "how long in Figma?" and gets "2h 34m."
- **Why**: numbers are funnier than vibes. Gemma4 tends to round to "a while."

### 3.3 `pick_running_gag()` — returns one of user's current active gags
Lets model weave a running joke into any comment instead of starting cold.

### 3.4 `fetch_hn_oneliner()` — on-demand world awareness (not just passive)
Model decides "I need an outside reference" and pulls one HN headline.
- Pairs with the planned `world_awareness` sense but is *pull*, not push.

### 3.5 `day_stats()` — returns commits, switches/hr, peak hour
Single call, rich context, used in `/wrapup` and freeform thoughts.

### 3.6 `check_weather_drama()` — returns weather only if it's extreme
Guardrail against "nice day out" filler quips — tool returns null on boring weather.

---

## New moods & running gags

- **`gossipy`** — activated when `world_awareness` or any social sense fires. Short bursts, conspiratorial tone.
- **`forensic`** — activated by `terminal_activity` + high `git` churn. Detective voice, "the evidence suggests…"
- **`smug`** — activated after `/verdict` is ignored. "Told you."
- **`resigned`** — activated by 3rd+ doom-loop pattern in a session (same app revisited 4x).

Running gags to seed:
- **"The Streak"** — any counter the MemoryStore tracks (consecutive days opening Slack before coffee app, etc.).
- **"The Tally"** — `/defend` invocations, `pytest` reruns, desktop screenshots.
- **"The Prophecy"** — `/verdict` outputs stored and callback-checked next day.

---

## Top 5 by (callback potential × comedy half-life)

1. **`terminal_activity` sense** — highest. Devs repeat commands; each rerun is a callback. Pairs with git + hardware.
2. **`calendar_awareness` sense** — deep well. Meeting fatigue is evergreen, composites everywhere.
3. **`lookup_recent_callback` agent tool** — force multiplier. Makes every OTHER callback trustworthy, not hallucinated.
4. **`/wrapup` slash command** — one per day, seeds next-day callbacks, never stale.
5. **`input_cadence` sense** — temporal richness, privacy-safe, feeds mood detection (forensic/resigned).

## Demote / skip
- `display_state` — filler, one joke deep.
- `/dare` — nag risk, narrow voice fit.
- `network_weather` — keep as low-weight ambient only.

## Easter eggs worth hardcoding (bypass LLM)
- Calendar empty on a Monday 10am workday → stock "suspicious" line.
- `rm -rf` verb from terminal sense → stock "godspeed" line.
- Battery < 5% + unplugged + idle → stock panic line.
- 47+ desktop icons → stock "cry for help" line.
- DND on + messaging app foregrounded → stock hypocrisy line.
