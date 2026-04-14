# Senses & Tools — Engineer Brainstorm

Persona: engineer. North star: what actually ships on gemma4 via Ollama, across Mac + two AMD Windows boxes + one Intel NPU laptop, given the 2026 Ollama tool-call+streaming bug. Bias toward passive senses over agent tools until the backend stabilizes.

## Bucket 1 — Passive senses (`@register_sense`, observe-emit)

### 1. `calendar` — next-meeting awareness
- **Shape**: sense. Polls every 60s, emits on transition (meeting-in-15m, in-5m, starting-now, ended).
- **Cross-platform**: Mac uses EventKit via `pyobjc` (read-only, user consents once). Windows uses Graph API for Outlook (OAuth) OR reads local `.ics` exported from Outlook. Linux: local `.ics` file path in config.
- **Deps**: `pyobjc-framework-EventKit` (mac), `icalendar` pip pkg (cross-platform .ics path).
- **Effort**: M for `.ics`-only MVP, L for EventKit + Graph.
- **Risks**: Graph OAuth is a rabbit hole; scope creep. `.ics` export flow is clunky for users. Sensitive event titles need same redaction treatment as app titles.

### 2. `network_state`
- **Shape**: sense. Detects online/offline, SSID changes ("back at home wifi"), VPN up/down, tethered-to-phone.
- **Cross-platform**: all three. `psutil.net_if_stats`, plus platform shims for SSID (`networksetup -getairportnetwork` on mac, `netsh wlan show interfaces` on Windows, `iwgetid` on Linux).
- **Deps**: none new.
- **Effort**: S.
- **Risks**: SSID name leakage in logs (treat like app names — redact). Parsing `netsh` output is fragile.

### 3. `battery` (laptop-only)
- **Shape**: sense. Emits on transition: plugged/unplugged, low-battery, fully-charged.
- **Cross-platform**: all three via `psutil.sensors_battery()`.
- **Deps**: none.
- **Effort**: S.
- **Risks**: returns `None` on desktops — needs graceful skip. Great comedy-per-LOC.

### 4. `keyboard_cadence`
- **Shape**: sense. Derives WPM and burst/pause pattern from existing `pynput` listener (reuse idle sense infra — do NOT add a second global hook).
- **Cross-platform**: all three via pynput.
- **Deps**: none (pynput already present for idle).
- **Effort**: M. The design constraint is *not* spawning a second listener; refactor idle sense to publish keystroke events to a small internal bus that both idle and cadence subscribe to.
- **Risks**: any keylog-adjacent code triggers macOS Input Monitoring prompt already; we don't store content, only timing deltas. Document clearly in privacy section. Cadence data is behaviorally revealing (stress, tiredness) — treat as sensitive.

### 5. `display` — monitor/lid state
- **Shape**: sense. External monitor connect/disconnect, lid open/close, resolution change, dark-mode toggle.
- **Cross-platform**: Mac `Quartz.CGDisplayIsActive` + existing Quartz infra. Windows `ctypes` against `user32.EnumDisplayMonitors`. Linux `xrandr` shell-out.
- **Deps**: none new on Mac, `ctypes` on Windows (stdlib).
- **Effort**: M.
- **Risks**: Linux support on Wayland is a mess — ship X11 only in MVP.

### 6. `process_heat` — what's eating your CPU
- **Shape**: sense. Names the top non-system process when CPU > 80% sustained for 20s. Differs from `hardware` sense which is aggregate.
- **Cross-platform**: all three via `psutil.process_iter`.
- **Deps**: none (psutil already in).
- **Effort**: S.
- **Risks**: process names can be sensitive (e.g., `1password-cli`) — filter list same as sensitive-apps. Runaway Electron app ID gets noisy.

### 7. `workspace_churn` — file-system activity in cwd
- **Shape**: sense. Watches a user-configured directory (default: none, opt-in). Emits "you've touched 40 files in the last hour" or "haven't saved in 20 minutes, live dangerously?".
- **Cross-platform**: all three via `watchdog`.
- **Deps**: `watchdog` pip pkg.
- **Effort**: M.
- **Risks**: fs events are chatty; aggregate into rolling windows before emitting. File *paths* leaking is the content-redaction concern — only emit counts and extensions, never paths.

### 8. `doc_count` — "how many unsaved tabs are you hoarding"
- **Shape**: sense. Counts open browser tabs / editor docs where APIs permit.
- **Cross-platform**: Mac-only realistically — AppleScript Safari/Chrome tab count; VSCode via workspace state file path. Windows/Linux: skip in MVP, return None.
- **Deps**: none.
- **Effort**: M. High comedy potential ("you have 47 tabs open, and we both know you'll never read any of them").
- **Risks**: AppleScript asks permission, user gets prompt fatigue.

## Bucket 2 — User-triggered slash commands (daemon-thread pattern like `/gh`)

### 9. `/ask` — web search (already planned)
- Already in `plans/world-awareness-and-web-search.md`. S-M effort, fits the `/gh` template exactly.

### 10. `/define <word>` + `/wiki <topic>`
- **Shape**: slash command. Wiktionary/Wikipedia REST, no key needed.
- **Cross-platform**: all three (network only).
- **Effort**: S. Great small-feature; natural follow-up to `/ask`.
- **Risks**: zero beyond prompt-injection hygiene already needed for `/ask`.

### 11. `/timer <duration> [label]`
- **Shape**: slash command that registers an in-process timer. On fire, buddy comments in-voice ("pomodoro's up, go stretch before your spine fuses").
- **Cross-platform**: all three.
- **Effort**: S.
- **Risks**: app close loses timers — document or persist to `memory.db`.

### 12. `/recap`
- **Shape**: slash command. Queries MemoryStore for last 4h of app-switching history and feeds it to the LLM for an in-voice summary. Complements the cross-session callbacks system.
- **Cross-platform**: all three.
- **Effort**: S.
- **Risks**: exposes memory.db query surface; filter sensitive apps before prompt.

### 13. `/commit` — let the buddy roast your diff
- **Shape**: slash command. `git diff --stat` → buddy comment, not a commit-message generator (that's off-brand; buddy is a critic, not a coworker).
- **Cross-platform**: all three.
- **Effort**: S.
- **Risks**: big diffs blow the 4k context — truncate to stat + first 20 lines of diff.

## Bucket 3 — Agent tools (LLM-invoked)

**TL;DR given the Ollama tool-call+streaming bug on gemma4:** ship these as slash commands first. Build the registry pattern anyway so we can flip them to true tools when the backend improves or when we route through `functiongemma` (270M tool specialist) as a sidecar router.

### 14. `get_time_in_location(tz)` / `convert_units()` / `do_math(expr)`
- **Shape**: agent tool (trivial, deterministic).
- **Ollama bug impact**: this is exactly the class of call that the current parser mangles. Workaround: run a small non-streaming gemma4 call *just* to detect tool intent ("does this message want a calculation? emit JSON or say NO"), then execute in Python, then stream the final response. It's a two-shot hack but it sidesteps the streaming parser entirely.
- **Effort**: S per tool, M for the two-shot router.
- **Risks**: latency doubles. `functiongemma` as the router is better once we've dogfooded it.

### 15. `search_memory(query)` — let the buddy pull its own callbacks
- **Shape**: agent tool. Exposes the cross-session memory layer to the LLM on demand, rather than always pre-injecting.
- **Ollama bug impact**: same as above. For MVP, keep the current auto-injection approach and don't expose as a tool; revisit when streaming+tools is stable.
- **Effort**: S once the router exists.
- **Risks**: the buddy calling memory every turn = prompt bloat. Rate-limit.

### 16. `set_mood(name)` / `toggle_sense(name, bool)` — self-modifying tools
- **Shape**: agent tool with a confirmation guardrail (can't silently disable `app_awareness`).
- **Ollama bug impact**: high — don't ship until stable. Comedy upside is real ("I'm muting hardware sense, your fan noise is upsetting me").
- **Effort**: M including confirm flow.
- **Risks**: prompt-injection-driven self-disable. Require a user-typed y/n in chat log before any sense toggles.

## Tools registry pattern — worth designing now?

**Yes, but as a thin stub.** Mirror `@register_sense`:

```python
@register_tool(
    name="do_math",
    schema={...json-schema...},
    safe=True,               # idempotent, no side effects
    requires_confirm=False,
)
def do_math(expr: str) -> str: ...
```

Plugin discovery via the existing `pkgutil.walk_packages` loop. In MVP, the registry powers `/`-slash-command dispatch (one code path for both humans typing `/math 2+2` and the future LLM calling `do_math`). When Ollama's bug clears, the same registry feeds the `tools=[...]` array to the OpenAI-compat endpoint. Designing it now costs maybe 30 lines and avoids a refactor later — worth it.

Key design choice: the registry entries carry both a human-invocable surface (slash command) and an LLM-invocable surface (JSON schema). `safe` + `requires_confirm` flags gate what the LLM is allowed to call autonomously.

## Top 5 by comedy-per-effort

1. **`battery` sense** (S) — "plugged in for 4 hours, what are you, a Tesla?" Trivial code, constant payoff.
2. **`/recap`** (S) — leverages memory we already have; roast-your-day is on-brand.
3. **`process_heat` sense** (S) — naming the hog is comedy gold; Slack eating 6GB writes itself.
4. **`doc_count` sense, Mac-only MVP** (M) — tab-hoarder material is universal. Accept Mac-only, add other platforms when someone complains.
5. **`network_state` sense** (S) — "back on home wifi, the commute was riveting I'm sure". Trivial platform shims, high transition-event density.

Deferred but worth designing the registry for: the three agent-tool ideas (14-16). Don't ship true tool-calling on Ollama+gemma4 until the streaming parser is fixed or we adopt `functiongemma` as a router sidecar. Slash-command-first, tool-call-later is the right sequencing.
