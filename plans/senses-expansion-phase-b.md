# Senses Expansion - Phase B

## Goal
Ship the three Phase A non-goals: `calendar`, `filesystem_pulse`, `typing_cadence`. These three are NOT bundleable the way Phase A's psutil senses were - each has a genuine cross-platform or architectural decision that needs its own research + design pass. This plan scopes the decisions, not the bundling.

## Non-goals
- No Phase C slash command bundle (`/recap`, `/roast`, `/define`, `/wiki`, `/commit`) - separate plan once new senses have produced material
- No new moods or easter eggs - wait for Phase B senses to generate real signal first
- No two-model dispatcher, embeddings layer, or `functiongemma` - Phase D architecture epic
- No LLM tool-calling wiring yet (Gemma4 streaming+tool-parser bug still blocks)

## Scope - one sense per sub-section, order-of-priority

### 1. `typing_cadence` (do first - lowest risk, highest frequency signal)
Detect bursts of keyboard activity vs silence. Signals: WPM buckets (`idle` / `slow` / `normal` / `rapid` / `furious`), sustained-burst detection ("you've been typing nonstop for 10 min"), and post-burst silence ("you stopped mid-thought").

**Architectural decision:** the `idle` sense already owns a `pynput.keyboard.Listener`. Two listeners on the same keyboard will double-fire and tank performance. Options:
- (A) Refactor `idle` to publish key events on an internal pub/sub bus that other senses subscribe to. Biggest change, right long-term shape - Phase D-ish.
- (B) Add a `typing_cadence` method to the existing idle sense; split the reading into two `SenseReading` emissions per poll (one for idle state, one for cadence). Simplest; keeps the scope tight.
- (C) Share a global singleton listener module at `tokenpal/senses/_keyboard_bus.py` with a simple list-of-callbacks API. Middle ground.

**Recommend (B) for Phase B**, defer (A) to Phase D when we have more subscribers. Measure key-event rate in a rolling window, bucket WPM, emit on bucket-transition only (like battery's state machine).

**Privacy:** DO NOT log, store, or emit any key values. Only timestamps and counts. Ever. Add a unit test that greps the module for `key.char`/`key.name`/`key_event.key` access outside the counter increment.

### 2. `filesystem_pulse` (do second - biggest scope)
Detect "project activity" via filesystem changes in watched dirs. Signals: bursts of edits in one dir ("hammering on TokenPal"), dir-switch transitions ("moved to a different project"), idle periods ("haven't touched the repo in 2 hrs"). Does NOT care about file contents - only paths + mtimes.

**Architectural decisions:**
- **Watching mechanism:** `watchdog` (adds a dep, robust, cross-platform) vs. periodic `os.scandir` polling (no dep, simpler, higher latency). Watchdog is the right call for responsiveness but adds ~2MB wheels. Poll fallback if watchdog unavailable.
- **Watched roots:** how do we decide what to watch? Three options:
  - (A) Hardcoded: just `~/projects/*` and `~/code/*` - bad, user-specific
  - (B) Config-driven: `[filesystem_pulse] roots = [...]` - good, explicit
  - (C) Derive from `git` sense's detected repos - slick but coupled
  - Recommend (B) with a first-run wizard hook similar to `/zip` for setting it.
- **Privacy:** paths themselves can be sensitive (e.g., `~/projects/client-name`). Must pass emitted path through a config-driven alias map (like `ssid_labels`), OR default to emitting only the leaf dir name, never the full path.
- **Bounds:** recursive watchers can explode (node_modules, .venv, build/). Must respect `.gitignore` where present, and hard-skip conventional huge dirs.

### 3. `calendar` (do third - cross-platform call is the hard part)
Detect upcoming meetings, current meeting ("you're in a standup right now"), meeting-heavy-day detection.

**Architectural decision - data source:**
- **macOS native:** EventKit via PyObjC. Requires calendar-access permission prompt. Most accurate, zero auth.
- **Windows native:** Microsoft Graph API. Requires Azure AD app registration + OAuth. Heavy.
- **Cross-platform:** `.ics` URL polling (Google Calendar, Outlook, iCloud all publish these). Users configure a URL; we GET + parse. Lowest-effort, works everywhere, privacy-aware (no OS-level auth, user picks what to expose).

**Recommend:** `.ics` URL approach for Phase B. EventKit integration can be its own follow-up plan for mac users who prefer it. Skip Graph entirely.

**Privacy:** meeting titles can leak. Same `contains_sensitive_term` filter as `world_awareness` title filtering. Rounded times only, never attendee names, never location details.

**Dep:** `ics` PyPI package (~20kb, pure-python). Already-considered alternative: hand-parse VEVENTs - fine-line between "short parser" and "bug factory" - use the library.

## Shared infrastructure to consider

- **Config-writer helper** - Phase A flagged (see issue #17) that `weather.py`, `train_voice.py`, `senses_writer.py` all duplicate the same regex-based upsert. Phase B will add `[filesystem_pulse]` roots + `[calendar]` ics_url, making it four. **Do issue #17's tomli-w refactor FIRST** so Phase B writes go through the new helper.
- **`_find_config_toml`** - same issue; move to `tokenpal/config/paths.py` before Phase B senses start importing it.
- **`_SECTION_MAP`** bug - issue #16. Fix same-PR as the tomli-w refactor; otherwise new `[filesystem_pulse]`/`[calendar]` sections will be silently ignored.

## Failure modes to anticipate

- **typing_cadence:** if `idle`'s pynput listener is not loaded (option B tight-coupling), cadence sense becomes a no-op silently. Document the dependency loudly.
- **filesystem_pulse:** watchdog on Windows can hit path-length limits (MAX_PATH). Degrade gracefully, log once, stop watching that root.
- **filesystem_pulse:** NFS/SMB/Dropbox/iCloud mounts generate spurious events. Default exclusion list for known sync-folder patterns.
- **calendar:** `.ics` URLs served over HTTP with self-signed certs - do we trust user-provided certs? Yes, but warn on HTTP vs HTTPS.
- **calendar:** recurring events with exclusions (`EXDATE`), timezones, all-day events all have edge cases in `ics` lib parsing. Test with real-world Google Calendar export.

## Done criteria (per-sense, not bundled)
Each sense:
- Opt-in `[senses] <name> = true` in config.toml
- `@register_sense` emission of transition-only readings
- Natural-language `summary` field
- Sensitive-term filter where external text enters the system
- Unit tests covering transitions, privacy assertions, failure paths
- README + CLAUDE.md entry
- Manual smoke-test checklist in the PR description

Bundled:
- `ruff check` clean on all new files
- `mypy --ignore-missing-imports` clean on all new files
- Full `pytest` passes

## Parking lot
(empty - append "ooh shiny" thoughts that surface mid-work)

---

## Implementation sequencing (next sessions)

**Prerequisite session (issues #16 + #17):**
- Fix `_SECTION_MAP` drops of `[web_search]` + `[conversation]`
- Rewrite weather.py + train_voice.py + senses_writer.py onto a shared `tomllib`+`tomli_w` helper
- Move `_find_config_toml` to `tokenpal/config/paths.py`
- One commit, review-able diff

**Session 1 - typing_cadence (smallest):**
- Decide A/B/C (recommend B - inline into idle)
- Implement, test, ship

**Session 2 - filesystem_pulse:**
- Research pass: watchdog vs. poll; default excludes; root-configuration UX
- Plan a first-run wizard step or `/watch add <path>` slash command
- Implement, test, ship

**Session 3 - calendar:**
- Research pass: `.ics` URL flow, add `[calendar] ics_url` config, wizard/slash-cmd to set it
- Sensitive-term filter on meeting titles
- Implement, test, ship

**Research pass (before session 1):**
- Grep the codebase for existing pynput usage - confirm `idle` is the only listener
- Verify `psutil`/`pynput` compatible with Python 3.14 (we're on 3.14.3 per the test run)
- Read the `ics` library docs to confirm it handles Google Calendar + Outlook exports
