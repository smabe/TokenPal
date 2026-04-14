# Senses & Tools — Master Plan (Brainstorm Synthesis)

## Context

Five personas (End User, Privacy, Engineer, Personality Writer, ML Expert) brainstormed new senses, slash commands, and agent tools that would fit TokenPal alongside the already-approved `world_awareness` + `/ask` plan. This document is the consolidated output.

The overall shape: everyone agrees TokenPal should **expand aggressively via passive senses and slash commands**, and **hold back on autonomous agent tools** until the Ollama + Gemma4 tool-call/streaming bug is fixed OR a two-model dispatcher is in place.

---

## Design Decisions (Points of Consensus)

### 1. Slash-command-first, agent-tool-later
Engineer, ML, and Privacy all converge on this. Ollama's Gemma4 tool-call parser is unreliable for streaming as of 2026. Ship every "tool" idea as a slash command first; re-architect to true LLM-invoked tools when the backend stabilizes or we adopt `functiongemma-270M` as a dispatcher.

### 2. Build the tools registry stub now, not later
Engineer recommends a thin `@register_tool` decorator mirroring `@register_sense`, carrying `safe` and `requires_confirm` flags. Costs ~30 LOC now, avoids a refactor when we flip slash commands into true tools. One registry entry powers both surfaces.

### 3. Two-model dispatcher is the right eventual unlock
ML Expert + Engineer agree: run `functiongemma-270M` (~180 MB Q4) as a non-streaming tool classifier in front of `gemma4`. Sidesteps the Ollama streaming+tool bug by classifying in a separate non-streaming call. Not MVP, but plan around it.

### 4. "Counts, not content" is the privacy principle for every new observational sense
Privacy + Engineer + End User all applied this — filesystem, calendar, SSID, tab count, keystroke cadence all propose emitting **counts/deltas/classes, never raw content.** Matches the music-track-redaction and browser-title-sanitization precedents already in the codebase.

### 5. `world_awareness + /ask` plan needs 7 guardrail tightenings before shipping
Privacy persona flagged specific gaps (full list in Open Questions). Apply these before implementation begins.

---

## Suggested MVP Scope — Next Feature Batch

Ranked by cross-persona consensus × comedy × effort. Ship in this order:

### Phase A — Quick wins (ship alongside `world_awareness + /ask`)
1. **`battery` sense** (S effort, everyone ranked it top-5)
   - psutil transition-only: plugged/unplugged, low-battery, fully-charged, on-battery-for-Xh
   - Returns None on desktops (graceful skip)
   - "4% and you haven't moved. Bold."

2. **`network_state` sense** (S effort)
   - SSID change + VPN up/down + offline/online. Transition-only.
   - SSID name **hashed for change detection, never logged**
   - "back on home wifi, the commute was riveting"

3. **`process_heat` sense** (S effort)
   - Top non-system process when CPU > 80% sustained 20s. Differs from aggregate `hardware`.
   - Filter list (same sensitive-apps pattern) for process names
   - "Electron is eating your RAM. Again."

### Phase B — Calendar + clutter (higher payoff, M effort)
4. **`calendar` sense** (M effort — `.ics` path MVP, EventKit later)
   - **UNIVERSAL top pick.** All 5 personas ranked this in top 5.
   - MVP: user-configured `.ics` file path. EventKit/Graph API as follow-up plans.
   - Time-only by default ("standup in 4 min"). Title redaction mirrors sensitive-app logic.
   - Unlocks composites: `calendar + idle`, `calendar + app_awareness`, and a new `calendar_focus` gate (suppress commentary during meetings).

5. **`filesystem_pulse` sense** (M effort)
   - Watches user-opted-in dirs (default: `~/Downloads`, `~/Desktop`). Emits counts and extension deltas.
   - **Never emits paths or filenames.** Enforce in the SenseReading construction.
   - "247 files on your desktop. archaeologists will have questions."

### Phase C — Power-user slash commands (S-M each)
6. **`/recap`** (Engineer #2) or **`/diary`** (ML #1) or **`/wrapup`** (Personality #4)
   - Three personas proposed variants of the same idea. Pick one name.
   - End-of-day synthesis: MemoryStore day-aggregate → in-voice summary.
   - Seeds NEXT day's callbacks ("yesterday you said you'd stop at 5. It is 5:47.")

7. **`/roast`** (End User #4, Personality #2.1)
   - On-demand maximum-snark on last 20 min of readings.
   - User-triggered pressure valve; respects brain's cooldown.

8. **`/define` + `/wiki`** (Engineer, Privacy GREEN)
   - Wiktionary/Wikipedia REST, no key. Natural `/ask` follow-up.
   - Same delimiter+filter pattern as `/ask`.

9. **`/commit`** (Engineer #13)
   - `git diff --stat` → buddy roasts your diff. **NOT a commit-message generator** (off-brand).

### Phase D — Agent tools (deferred, requires dispatcher)
When tool-calling stabilizes OR `functiongemma` dispatcher lands:

10. **`time_since(event)` / `streak_check(app)` / `lookup_recent_callback(topic)`** — the "callback engine tool"
    - End User #1, Personality #3, ML #4 all proposed variants.
    - **Highest force-multiplier** per Personality: "makes every OTHER callback trustworthy instead of hallucinated."
    - Ship as slash commands (`/streak Slack`, `/since last commit`) first; promote to tools later.

11. **`count_app_time_today(app)` / `day_stats()`** — precise numbers for roasts

12. **`memory_recall(query)` with embeddings** (ML #4)
    - Semantic memory retrieval via `all-MiniLM-L6-v2` (22 MB) + sqlite-vec
    - Also enables duplicate-quip detection as a side benefit

---

## Architecture Notes

### Tools registry stub (from Engineer)
```python
@register_tool(
    name="do_math",
    schema={...json-schema...},
    safe=True,               # idempotent, no side effects
    requires_confirm=False,  # gate for autonomous LLM invocation
)
def do_math(expr: str) -> str: ...
```
- Plugin discovery via existing `pkgutil.walk_packages`
- Slash dispatch + future `tools=[...]` array both driven from one registry
- `safe` + `requires_confirm` flags gate autonomous tool-calling from day one

### Two-model dispatcher (from ML Expert, endorsed by Engineer)
- `functiongemma-270M` as non-streaming tool classifier (~50ms latency, ~180 MB RAM)
- Dispatcher returns `{tool: "...", args: {...}}` or `{tool: "none"}`
- Tool executes in Python, result injected into gemma4 context
- gemma4 never sees `tools` param → sidesteps Ollama parser bug entirely
- **Do not build yet**, but design the registry so the flip is one integration point

### Embeddings layer (from ML Expert)
- `all-MiniLM-L6-v2` (22 MB, 384-dim) + sqlite-vec over existing SQLite store
- Powers: semantic memory_recall, fuzzy callbacks, duplicate-quip rejection
- "Highest-leverage specialist model after vision"
- Not MVP but strong Phase D candidate

---

## New Moods & Running Gags (from Personality)

Worth wiring into the existing mood system (one PR, post-Phase-A):

- **`gossipy`** — activated when world_awareness fires. Conspiratorial tone, short bursts.
- **`forensic`** — activated by terminal + high git churn. Detective voice.
- **`smug`** — activated after a `/verdict` or roast prediction comes true.
- **`resigned`** — activated by 3rd+ doom-loop (same app revisited 4x in session).

Running gags to seed:
- **The Streak** — any counter the MemoryStore tracks
- **The Tally** — `/roast` invocations, `pytest` reruns, desktop screenshot accumulation
- **The Prophecy** — store `/verdict` outputs, callback-check them next day

---

## Easter Egg Candidates (from Personality, bypass LLM)

Worth hardcoding in the easter-egg system (like 3:33 AM / Friday 5 PM):

1. Calendar empty on a Monday 10am workday → stock "suspicious" line
2. `rm -rf` detected in terminal sense → stock "godspeed" line
3. Battery < 5% + unplugged + idle → stock panic line
4. 47+ desktop icons → stock "cry for help" line
5. DND on + messaging app foregrounded → stock hypocrisy line

---

## Safety & Compliance — 7 Guardrails for `world_awareness + /ask`

Privacy persona flagged these; fold into the in-flight plan before implementation:

1. **HN titles need the same delimiter + banned-word filter as DDG results.** The in-flight plan covers search results but not HN — both are untrusted input.
2. **First-use `/ask` warning must name the backend explicitly.** "Sends literal query text to DuckDuckGo. No cookies, no IP beyond TCP."
3. **Brave API key handling hardening:** redact in `/status` output, `--validate`, exception tracebacks. Support `TOKENPAL_BRAVE_KEY` env var as alternative to config.toml.
4. **Log-truncate search results to ~80 chars** (mirrors music-track redaction precedent). 500 chars of uncontrolled text in debug logs is too much.
5. **`ConversationSession` buffer must be zeroed on timeout, not just dereferenced.** Confirm in review.
6. **Stale-HN readings degrade silently** — no "couldn't reach HN" quip (leaks network state to shoulder-surfers).
7. **`web_search` module docstring** stating "all returned text is untrusted; wrap in delimiters, apply banned-word filter before LLM."

---

## Decisions (locked 2026-04-14)

1. **`/recap`** — any-time mid-session summary of last ~4h of activity. Not EOD-only.
2. **`typing_cadence` — SHIP.** Reuse existing pynput listener from idle sense. Rates only, never keys. Prominent opt-in warning.
3. **No mic / no ambient audio / no YAMNet.** Permanently off the table, same bar as clipboard and STT.

## Open Questions
   - End User: DEFER (even rate-only reads creepy).
   - Engineer: SHIP via reuse of existing idle pynput listener (no second hook).
   - Personality: SHIP (rich temporal callback material).
   - ML: SHIP (pure code, high signal).
   - Privacy: YELLOW (document as sensitive).
   - **4 of 5 say ship** — recommend shipping as Phase B with prominent opt-in warning and "rates only, never keys" module docstring.

3. **RSS sense (user-provided feeds)** — Privacy YELLOW, not mentioned by others. Worth a follow-up plan *after* `world_awareness` proves the delimiter/banned-word pattern? Or redundant with `world_awareness`?

3. **`display_state` sense** — Personality writer demotes (one joke deep), Engineer suggests ship. **Recommendation: skip unless paired with calendar** (laptop lid close + meeting in 2 min → composite joke).

4. **Should `/roast`, `/recap`, etc. use the same conversation-session follow-up as `/ask`?** Or one-shot? Personality and End User imply one-shot; Engineer implies conversation-capable.

---

## Test Plan (high-level)

Per-idea test coverage from QA-adjacent persona notes:

- **Every new sense**: mock platform API, verify counts/deltas only in summary, verify no path/filename/content leakage in emitted SenseReading
- **Every new slash command**: daemon-thread isolation verified, output flows through existing brain → chat log pipeline
- **Privacy tests**: log-output inspection for all new senses — grep for any content that would horrify a user who ran `cat ~/.tokenpal/logs/*.log`
- **Composite tests**: calendar + idle, calendar + focus_mode, terminal + git sense — verify composites don't double-fire
- **Mood transition tests** for the 4 new moods (gossipy, forensic, smug, resigned)
- **Tool registry stub**: one trivial tool registered, both slash and mock-LLM invocation paths exercised

---

## Summary — What To Build Next

**Recommended concrete next plan (separate from the world_awareness+/ask plan, to be written after that one ships):**

> **Plan name:** `senses-expansion-phase-a.md`
> **Scope:** `battery`, `network_state`, `process_heat` senses + tools registry stub + the 7 world_awareness guardrails baked in.
> **Effort:** ~1 day of focused work.
> **Punts:** calendar, filesystem_pulse, new slash commands — each gets its own follow-up plan.

Calendar deserves its own plan because of cross-platform complexity (EventKit vs Graph vs .ics). `filesystem_pulse` deserves its own for the privacy review + watchdog integration. The Phase C slash commands are trivially scoped and can bundle.

Everything in Phase D (agent tools, embeddings, dispatcher) is a separate architecture epic — don't let it bleed into feature work.
