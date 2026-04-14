# Senses Expansion — Phase A

## Goal
Ship three small psutil-flavored passive senses (`battery`, `network_state`, `process_heat`) plus a `@register_tool` registry stub in one bundled pass. All four were top-ranked cross-persona in the brainstorm and share a common implementation shape — bundle them to avoid per-feature plan overhead.

## Non-goals
- No calendar sense — that's Phase B, needs its own plan (EventKit vs Graph vs .ics is a real cross-platform call)
- No filesystem_pulse — Phase B, deserves its own privacy review + watchdog integration
- No typing_cadence — Phase B, needs the idle pynput listener refactored to publish events to a shared bus
- No slash command bundle (`/recap`, `/roast`, `/define`, `/wiki`, `/commit`) — Phase C, separate plan
- No real LLM tool-calling wiring yet (Ollama Gemma4 streaming+tool-parser bug blocks). Register the `@register_tool` decorator + registry; DO NOT yet feed the `tools=[...]` array to the LLM backend
- No new moods (`gossipy`, `forensic`, `smug`, `resigned`) or easter eggs — separate, smaller plans once the new senses have produced real material
- No two-model dispatcher, no embeddings layer, no `functiongemma` — Phase D architecture epic

## Files to touch

**Battery sense:**
- `tokenpal/senses/battery/__init__.py` — new package
- `tokenpal/senses/battery/sense.py` — new, `@register_sense`, transition-only readings. Uses `psutil.sensors_battery()`. Returns `None` on desktops (no battery). Thresholds: plugged/unplugged, low (<20%), critical (<5%), fully-charged (≥99% + plugged). `poll_interval_s = 30.0`, `reading_ttl_s = 300.0`
- `tokenpal/config/schema.py` — add `battery: bool = False` to `SensesConfig`
- `config.default.toml` — add `battery = false  # plugged/unplugged transitions, low-battery warnings`

**Network state sense:**
- `tokenpal/senses/network_state/__init__.py` — new package
- `tokenpal/senses/network_state/sense.py` — new, `@register_sense`. Detects: online/offline transitions via default route, SSID change, VPN up/down. Emits on transition only.
- `tokenpal/senses/network_state/platform_impl.py` — SSID readers per platform. Mac: `networksetup -getairportnetwork en0`. Windows: `netsh wlan show interfaces`. Linux: `iwgetid -r`.
- **SSID privacy rule**: hash SSID for change detection (store `hashlib.sha256(ssid.encode()).hexdigest()[:16]` in state), generate summaries using generic terms ("switched wifi", "back on known wifi" keyed by a local-only label map in config) — **never log or emit the raw SSID**. If the user has opted-in to labeling, they can map hashes→friendly names in config.toml `[network_state] ssid_labels`.
- `tokenpal/config/schema.py` — add `network_state: bool = False` + new `NetworkStateConfig` with `ssid_labels: dict[str, str]` default `{}`
- `config.default.toml` — add `network_state = false` + commented-out `[network_state]` section

**Process heat sense:**
- `tokenpal/senses/process_heat/__init__.py` — new package
- `tokenpal/senses/process_heat/sense.py` — new. Polls every ~10s. When system CPU > 80% sustained for 20s, names the top non-system process eating it. Emits transition-only readings (on-trigger, on-clear).
- **Sensitive-app filter**: use `contains_sensitive_term` from `brain.personality` on process names — if the hog is a sensitive app, emit a generic "something's working hard" rather than the app name.
- **Known noisy processes whitelist**: Electron-family apps often dominate CPU in short bursts (slack, discord, vscode renderer) — aggregate by parent name where possible to avoid "Electron Helper (Renderer)" as the summary.
- `tokenpal/config/schema.py` — add `process_heat: bool = False`
- `config.default.toml` — add `process_heat = false`

**Tools registry stub:**
- `tokenpal/tools/registry.py` — new. `@register_tool(name, schema, safe, requires_confirm)` decorator + `get_registered_tools()` discovery function via `pkgutil.walk_packages`. Mirror `tokenpal/senses/registry.py` almost exactly.
- `tokenpal/tools/__init__.py` — exists, check for conflicts with existing `tokenpal/tools/` contents (there's existing ML-training tooling there — DON'T clobber). **May need to put registry at `tokenpal/actions/registry.py` instead** since `@register_action` is an existing decorator and this is adjacent work. Verify during research pass.
- One trivial example tool at `tokenpal/tools/builtin/do_math.py` (or actions/) — `do_math(expr: str) -> str` using `ast.literal_eval`-style safe eval. Purpose: prove the registry works end-to-end. Expose as `/math <expr>` slash command via the existing dispatcher; registry entry stays unused on the LLM side.
- Registry entries carry `safe: bool` and `requires_confirm: bool` flags for future autonomous-invocation gating.

**Tests:**
- `tests/test_battery_sense.py` — mock `psutil.sensors_battery()`, cover transitions, desktop None case, low/critical thresholds
- `tests/test_network_state_sense.py` — mock platform shims, SSID hashing, label-map lookup, hash-never-emitted-raw guarantee
- `tests/test_process_heat_sense.py` — mock `psutil.cpu_percent` + `process_iter`, sustained-trigger threshold, sensitive-app filter, Electron aggregation
- `tests/test_tools_registry.py` — registration, discovery, duplicate-name rejection, safe/requires_confirm flag plumbing, `/math` end-to-end via CommandDispatcher

**Docs:**
- `CLAUDE.md` — add the three new senses to Senses section, note tools registry scaffold
- `README.md` — one-line mention in the Features table's Senses row

## Failure modes to anticipate
- **`psutil.sensors_battery()` quirks**: returns `None` on desktops, returns `percent=100.0` + `power_plugged=True` for some docked laptops even mid-discharge, some Linux distros report `secsleft=-1` (unknown) — need defensive coalescing
- **SSID privacy leak risk**: the whole point of hashing is to prevent SSID names leaking into logs/chat. A careless `log.debug("new ssid: %s", ssid)` anywhere undoes it. **Add a lint-test** that greps the module for `log.*ssid` patterns to catch accidental raw logging
- **Platform shim fragility**: `netsh wlan show interfaces` output format differs across Windows locales (English "SSID" vs German "SSID" vs French "SSID" — OK actually those match, but the preamble differs). `iwgetid` may not be installed on headless Linux. Need graceful skip when the shim command is missing
- **VPN detection is hard**: there's no portable "is VPN up" API. Heuristics: default route interface starts with `utun`/`tun`/`tap`/`wg`, or DNS resolv.conf contains specific strings. Accept heuristics, document false-positive/negative risk
- **Process_heat noise on Electron**: a single Slack/Discord spike that lasts 22s would trigger. Consider 60s sustained threshold instead of 20s, or require >90% peak rather than >80% sustained. Tune during manual testing
- **Sensitive-app process names**: "1password-cli", "com.agilebits.onepassword7" etc. `contains_sensitive_term` is substring-match on the personality.py list — verify the list catches process-style names or extend it
- **Tools registry vs existing `@register_action` decorator**: TokenPal already has `@register_action` for LLM tool calling. Need to decide: is `@register_tool` the same thing with a better interface, or is it a different concept (slash-command-first, autonomous-later)? If overlapping, unify instead of duplicating. **Research pass must confirm before writing the decorator.**
- **Config-migration for users with existing `config.toml`**: adding new `[senses]` flags is backward-compatible (dataclass defaults). Adding `[network_state]` section is additive. No migration needed.
- **The `/math` slash command**: if math eval accepts `__import__` or similar, that's a security hole. Use `ast.parse` + walk the tree restricting to `BinOp`/`Num`/`Constant` nodes. NOT `eval()` or `exec()`.
- **Teardown on all three senses**: psutil handles don't need explicit teardown; platform-shim subprocesses must not leak (use `subprocess.check_output` with timeout, or async subprocess with explicit cleanup)

## Done criteria
- All three senses ship: opt-in via config, register via `@register_sense`, emit transition-only readings with natural-language summaries, filter through existing sensitive-app logic where applicable
- `@register_tool` registry + `get_registered_tools()` discovery works: `pytest` verifies a registered tool is findable, duplicates raise, flags round-trip correctly
- `/math <expr>` slash command works end-to-end as the proof-of-registry
- Three senses + registry + `/math` covered by unit tests. Full `pytest` suite passes (≥ 400 tests)
- `ruff check` clean on all new files. `mypy --ignore-missing-imports` clean on all new files
- `CLAUDE.md` Senses section lists all three. README Features table updated
- Manual smoke test: enable all three senses in config.toml, run `tokenpal`, verify at least one reading per sense fires during a 5-min session (battery needs `pmset` toggle or equivalent; network_state needs wifi toggle; process_heat needs a stress test like `yes > /dev/null &`)
- No raw SSID names in logs, chat, or memory.db (grep-verified)
- Plan-skill ship: move this file to `plans/shipped/` when done

## Parking lot
(empty — append "ooh shiny" thoughts that surface mid-work)

---

## Implementation sequencing (next session)

**Wave 1 (parallel agents — 4 small tasks):**
- Agent A: `battery` sense + tests
- Agent B: `network_state` sense + platform shims + tests
- Agent C: `process_heat` sense + tests
- Agent D: tools registry + `/math` example + tests

**Wave 2 (sequential, main thread):**
- Config schema + `config.default.toml` changes (all 3 senses + NetworkStateConfig)
- Docs (CLAUDE.md + README.md)
- Simplify pass (3 parallel review agents on the full diff)
- Full test run + ruff + mypy
- Commit + push

**Research pass (before Wave 1):**
- Confirm whether `@register_tool` is a new thing or should merge with existing `@register_action`. Read `tokenpal/actions/` first.
- Verify `psutil.sensors_battery()` returns what we expect on the target Mac (run a quick smoke)
- Check that `contains_sensitive_term` matches the process-name shape we'll see from `psutil.process_iter`
- Confirm `pynput`/`psutil` are both already in `pyproject.toml` (they are, via existing senses — just verify)
