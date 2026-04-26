# Three world senses

## Goal
Add three new ambient external-world senses to give the buddy more material to riff on when local-machine signals are stale: `lobsters` (HN-alike), `sun_position` (sunrise/sunset/golden-hour transitions), and `github_trending` (top new repo this week).

## Non-goals
- No new senses beyond these three. The other ideas from the table (weather alerts, earthquakes, APOD, wikipedia featured) stay in parking lot.
- No changes to the existing `world_awareness` (HN) or `weather` senses.
- No new idle-tool actions. These are passive context-feeding senses, not LLM-callable tools.
- No auth tokens. All three must work keyless. (sun_position is local compute; lobsters has open .json; github_trending uses unauth GitHub Search API at well under the 10-req/min keyless cap.)
- No new config UI / wizard step. Edit `config.toml` to enable.
- No "comment exactly once per reading" semantics — defer that whole conversation. Existing topic-picker novelty/change weights are good enough for v1.

## Files to touch

Per sense (mirror `tokenpal/senses/world_awareness/` shape):
- `tokenpal/senses/lobsters/__init__.py` — new
- `tokenpal/senses/lobsters/sense.py` — new
- `tokenpal/senses/lobsters/_client.py` — new
- `tokenpal/senses/sun_position/__init__.py` — new
- `tokenpal/senses/sun_position/sense.py` — new (compute-only, no _client)
- `tokenpal/senses/github_trending/__init__.py` — new
- `tokenpal/senses/github_trending/sense.py` — new
- `tokenpal/senses/github_trending/_client.py` — new

Cross-cutting:
- `tokenpal/config/schema.py` — add 3 fields to `SensesConfig` (default False) + new `SunPositionConfig` dataclass
- `tokenpal/config/loader.py` — register `[sun_position]` → `SunPositionConfig` in `_SECTION_MAP`
- `tokenpal/app.py` — wire `sense_configs["sun_position"]` from `config.sun_position` (mirror weather)
- `config.default.toml` — add 3 lines under `[senses]` (default false, with one-line opt-in comment) + commented-out `[sun_position]` block
- `config.toml` — add 3 lines (set to true, since user wants them on) + `[sun_position]` lat/lon
- `tokenpal/brain/orchestrator.py` — add 3 entries to `_TOPIC_FOCUS_HINTS`

Tests:
- `tests/test_senses/test_lobsters.py` — new (mirror `tests/test_world_awareness.py` minus the obsolete-disable test)
- `tests/test_senses/test_sun_position.py` — new (transitions table)
- `tests/test_senses/test_github_trending.py` — new

## Failure modes to anticipate
- **lobsters .json schema drift** — endpoint returns a JSON array of stories; field names (`title`, `score`, `url`) could shift. Wrap in defensive `dict.get` with sentinel + log-and-skip on parse failure, same pattern as `hn_client.py`.
- **github_trending rate limit** — keyless GitHub API is 10 req/min per IP, 60 req/hr unauth. With `poll_interval_s=1800` we hit it twice an hour: safe. But `429` responses need silent backoff — never crash the brain loop.
- **sun_position needs lat/lon** — the weather sense already resolves zip→lat/lon; sun_position must read from the same source (probably the user's `[weather] zip` config), not invent its own. If lat/lon is unresolvable, sense disables silently. Don't open a second network round-trip just for sunrise.
- **sun_position transition spam** — naive "minute-bucket the sun's elevation" emits a reading every poll. Need to gate so we only emit when crossing a labeled boundary (pre-sunrise, sunrise, golden-hour-AM, midday, golden-hour-PM, sunset, blue-hour, deep-night). One reading per crossing, like the idle sense's tier transitions.
- **github_trending `created:>X` query** — date math wrong = empty results. Use `(today - 7 days).isoformat()` and verify the response in the test, not just mock.
- **Topic spam** — three new senses with identical 30-min poll cycles could each get picked early and back-to-back. Existing novelty penalty + 3-consecutive block should handle it; don't add new gating.
- **First-poll lag** — if all three are active, the first 30 min of buddy life has only one HN reading. That's fine — defer "fire all senses on startup" to parking lot.
- **Config field-rename gotcha** — adding fields to `SensesConfig` mid-version breaks existing `~/.tokenpal/config.toml` files that don't have them. Schema dataclass defaults handle it (default False), but verify by running `tokenpal --check` on a config without the new fields.
- **sun_position math correctness** — solar elevation from lat/lon/datetime is well-known but easy to get sign/timezone wrong. Use `astral` library if installed (already a transitive dep? check) — otherwise a 30-line NOAA SPA approximation. Test against known sunrise/sunset for a fixed lat/lon/date.

## Done criteria
- All three senses load via the registry on startup (visible in `tokenpal --check` and the launch log).
- With each sense enabled, a `Top Lobsters: …`, sun-state, or `Trending GitHub: …` line appears in the orchestrator's context snapshot within one poll cycle of startup (verified via `~/.tokenpal/logs/tokenpal.log` debug context lines).
- Each sense has a passing test file under `tests/test_senses/` covering: parse-success path, parse-failure-graceful path, and (for sun_position) at least one transition-boundary case.
- `pytest` full suite green; `ruff check tokenpal/senses/` clean.
- Buddy actually riffs on at least one of the three within ~10 min of running (manual smoke test, log inspection).

## Phases
1. **lobsters** — copy world_awareness, swap endpoint + summary string, ship.
2. **github_trending** — copy lobsters, swap to GitHub Search API.
3. **sun_position** — independent shape (local compute, transition gating). Land last because it's the most novel of the three.

Each phase: code → /simplify → tests → commit → push (the user has standing approval for these via the auto-mode pattern).

## Parking lot
- **`run_in_executor` for keyless polling senses** — efficiency review flagged
  that `http_json` blocks the asyncio loop. Pattern-wide (all three new senses
  + world_awareness all do it). Worth one cross-cutting wrap at the
  `tokenpal/util/http_json.py` layer rather than per-sense. File as issue when
  this plan ships.
- ~~Shared `[location]` config block~~ — RESOLVED in phase 3 simplify pass:
  sun_position reads `config.weather.latitude/longitude` directly via the same
  `load_config()` pattern that `actions/utilities/sunrise_sunset.py` uses. No
  duplication, no new config table.
