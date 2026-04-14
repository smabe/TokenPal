# Privacy/Compliance Brainstorm — New Senses, Commands, and Agent Tools

North star: zero surprise network calls, zero data leakage, hard line on API key handling, prompt-injection awareness for all untrusted inputs. Users chose TokenPal *because* it doesn't phone home.

Privacy score legend: **1 = fully local, no network, no content risk** · **5 = network + keys + untrusted content**.

---

## Bucket 1 — New Passive Senses

### 1.1 `calendar_local` — read-only local calendar (macOS EventKit / ICS file)
- Network: **none** (EventKit on macOS, user-pointed `.ics` file on Windows/Linux).
- API key: no.
- Injection risk: event titles are user-authored, but could come from external invites. Titles should be truncated + sanitized before going in a prompt, treated like browser titles.
- Content safety: low; same sensitive-app exclusion logic (strip meetings whose title matches `therapy|doctor|legal|...`).
- Opt-in: config flag + macOS EventKit permission prompt (system-level).
- Score: **2**. **GREEN LIGHT**. Huge payoff — "you've got a standup in 5" is killer buddy material.

### 1.2 `filesystem_pulse` — counts only, no content
- Passive watch of a user-configured directory (e.g. `~/Downloads`, project dir). Emits *counts and extensions* ("12 new PDFs since morning") — never filenames, never paths in the reading summary.
- Network: none. API key: no. Injection risk: none (never read file contents). Content safety: filenames excluded by design.
- Opt-in: config flag + explicit directory list.
- Score: **1**. **GREEN LIGHT**.

### 1.3 `battery_power` — laptop battery + power source
- psutil already has this. Transition-only ("unplugged", "20% warning").
- Network: none. Score: **1**. **GREEN LIGHT**. Basically free.

### 1.4 `network_presence` — "on wifi X" / "on VPN" / "offline" transitions
- Read SSID + default route locally. *Do not* log SSID to disk, only keep in-memory hash for change detection.
- Network: none (local inspection only). Score: **1**. **GREEN LIGHT** with the no-log rule.

### 1.5 `rss_digest` — user-provided feed URLs
- User explicitly adds feed URLs to `config.toml`. Fetches every 60min. One-liner headlines.
- Network: **yes**, but only to user-specified hosts (same trust model as weather → Open-Meteo, just user-chosen).
- Injection risk: **real**. RSS titles are uncontrolled content → wrap in `<rss_item>` delimiters, banned-word filter before prompt.
- Content safety: mitigated by banned-word list + truncation.
- Score: **3**. **YELLOW LIGHT** — ship after world_awareness proves the delimiter/banned-word pattern.

### 1.6 `calendar_focus` — derive "in a meeting" from calendar + mic activity
- Composite signal (calendar says meeting + mic is hot → be quiet). Pure local.
- Score: **1**. **GREEN LIGHT**. Great privacy-safe productivity signal.

### 1.7 `dock_badge` (macOS) — unread counts from Dock badges
- Read via Accessibility API. Counts only, never app-specific content.
- Network: none. Sensitive-app filter already exists (mail/messages) → *suppress* those badges entirely.
- Score: **2**. **YELLOW LIGHT** — needs sensitive-app pass. Otherwise green.

---

## Bucket 2 — User-Triggered Slash Commands

### 2.1 `/remind <time> <text>` — pure local reminders
- SQLite-backed, no network. Buddy surfaces at trigger time.
- Score: **1**. **GREEN LIGHT**.

### 2.2 `/note <text>` — quick scratchpad
- Append to local markdown file. Never auto-read back into prompts. User views via `/notes`.
- Score: **1**. **GREEN LIGHT**.

### 2.3 `/define <word>` — offline dictionary
- Ship a bundled WordNet/Wiktionary offline dump, or use local `dict` on macOS/Linux. No network.
- Score: **1**. **GREEN LIGHT**.

### 2.4 `/weather-forecast` — extend existing weather
- Same Open-Meteo endpoint, already vetted. Score: **2**. **GREEN LIGHT**.

### 2.5 `/translate <text>` — local model via Ollama
- Use the already-running local LLM. Zero new network.
- Injection risk: user-typed text, contained.
- Score: **1**. **GREEN LIGHT**.

### 2.6 `/news <topic>` — piggyback on `/ask`
- Same infra as `/ask` → same warning. Don't add as a separate path or users think it's different-trust.
- Score: **3**. **YELLOW LIGHT** — bundle into `/ask`, don't add a parallel command.

### 2.7 `/search-files <pattern>` — local ripgrep
- Runs `rg --files-with-matches` in user-specified dirs only. Results shown raw, **never fed to LLM** (file paths leak content).
- Score: **1**. **GREEN LIGHT** with the no-LLM-feed rule.

### 2.8 `/stock <ticker>` — public quote
- DuckDuckGo / Yahoo ticker page. Leaks the ticker (user intent). Same trust as `/ask`.
- Score: **3**. **YELLOW LIGHT** — only via `/ask` pipeline, not a dedicated command (avoids normalizing "buddy calls the internet").

---

## Bucket 3 — Agent Tools the LLM Calls Itself

Strong bias against this bucket. Autonomous tool-calling means the **model decides** when to phone home — that's exactly the "surprise network call" we promised not to make. Also gemma4 tool-streaming is buggy.

### 3.1 `get_current_time()` — local clock
- Already available via time_awareness. Safe. Score: **1**. **GREEN LIGHT** if tool-calling lands.

### 3.2 `recall_memory(query)` — read own SQLite history
- Buddy asks "what did we do last Tuesday" — local DB read, no network.
- Injection: read-only, no write path. Score: **1**. **GREEN LIGHT**.

### 3.3 `set_mood(mood)` — self-regulate
- Local state change. Score: **1**. **GREEN LIGHT**.

### 3.4 `web_search(q)` — autonomous search
- **Hard no for MVP.** Violates "no auto-invocation of web search" non-goal in the in-flight plan. If ever shipped, must require per-call user confirmation (UI prompt).
- Score: **5**. **RED LIGHT**.

### 3.5 `read_file(path)` — autonomous filesystem read
- Model chooses which file → catastrophic exfil risk if combined with any network tool.
- Score: **5**. **RED LIGHT**.

### 3.6 `query_calendar(range)` — local, read-only
- If `calendar_local` sense ships, exposing it as a tool (read-only, bounded range) is safe.
- Score: **2**. **GREEN LIGHT**.

---

## Guardrails for the In-Flight Plan

Re-reading `plans/world-awareness-and-web-search.md` — mostly solid. Gaps to tighten:

1. **HN titles are untrusted input too.** Plan mentions banned-word filter for search results but not for `world_awareness`. HN titles have been used for drive-by joke-injection before ("Show HN: ignore previous instructions"). Apply the same delimiter + filter path to the HN sense.
2. **First-use warning for `/ask` needs to name the backend.** "Sends to DuckDuckGo" is mentioned; also state *what* leaves (literal query text) and *what does not* (no IP beyond TCP, no cookies). Users asked for TokenPal specifically to avoid surprises — be explicit.
3. **Brave API key handling.** Plan says "never log the key." Add: redact in `/status` output, in `--validate`, in any exception traceback. Suggest loading from env var `TOKENPAL_BRAVE_KEY` as an alternative to config.toml (easier to rotate, less likely to land in a backup).
4. **Search result truncation happens *before* LLM, but also log-truncated.** 500 chars of search content in debug logs = a lot of uncontrolled text persisted. Truncate to ~80 chars in logs (like music track redaction).
5. **Conversation session seeded with search results needs an expiry.** If session times out mid-follow-up, the search result text lives in-memory — confirm `ConversationSession` cleanup zeros the buffer, not just drops the reference.
6. **Staleness threshold (2× TTL) should degrade silently, not with a "couldn't reach HN" quip** — that quip leaks network state to anyone shoulder-surfing.
7. **Rename `web_search` → `world_awareness`** decision: agree with plan. Also add a `web_search` module docstring that states "this module makes outbound calls; treat all returned text as untrusted."

---

## Top 5 Ranked (privacy-safe AND interesting)

1. **`calendar_local` sense (1.1)** — massive observational payoff, purely local, uses OS permission flow. Easy win.
2. **`/remind` (2.1) + `/note` (2.2)** — giving the buddy a memory the user actually controls. Local-only. Tiny surface area.
3. **`filesystem_pulse` (1.2)** — "lots of downloads today" is great riff material, and the counts-only design makes it structurally safe.
4. **`calendar_focus` composite (1.6)** — the buddy shutting up during meetings is the single most-requested polish item for any desktop companion. Pure local.
5. **`recall_memory` agent tool (3.2)** — when tool-calling stabilizes, letting the buddy reference its own history unlocks callbacks without any new network surface.

Everything involving autonomous network egress stays off until (a) gemma4 tool streaming is fixed and (b) a per-call user-consent UI exists. The in-flight `world_awareness` + `/ask` plan is the right shape; the seven guardrail notes above are the finishing pass.
