# Commentary Variety Finetune

## Goal
Give TokenPal more things to talk about and smarter topic selection so comments aren't 90% "you're in [app] again". Add new senses (weather, music, productivity patterns), composite cross-sense observations, change detection metadata, and a topic roulette system that forces variety.

## Non-goals
- News/Reddit/HN integration (Tier 2 — separate plan later)
- Fun facts / trivia APIs (Tier 2)
- Changing the LLM model or prompt template structure
- Voice training changes
- Windows implementations of new senses (macOS first, Windows stubs)
- Browser tab content reading beyond window titles (privacy guardrails deferred)
- Touching the fine-tuned model pipeline

## Files to touch
- `tokenpal/senses/weather/` (new) — Open-Meteo sense, poll every 30 min
- `tokenpal/senses/music/` (new) — Music detection sense (AppleScript + window title)
- `tokenpal/senses/productivity/` (new) — Productivity patterns from existing MemoryStore data
- `tokenpal/brain/context.py` — Add composite observations, change detection metadata, topic roulette weights
- `tokenpal/brain/personality.py` — Update prompt to include "what changed" and topic hint
- `tokenpal/brain/orchestrator.py` — Wire new senses, integrate topic roulette into comment gate
- `tokenpal/config/schema.py` — Config for new senses (weather location, music players, etc.)
- `config.default.toml` — Default config entries for new senses
- `tests/` — Tests for each new sense + composite logic

## Failure modes to anticipate
- Open-Meteo rate limiting or downtime — need graceful degradation (skip weather, don't crash)
- `/zip` geocoding could return wrong city (ambiguous zip codes) — show result and let user confirm
- AppleScript launching Music.app/Spotify when checking if they're running — must check `app.running` first
- YouTube Music runs in browser — window title parsing is fragile if Google changes format
- Productivity sense reading from MemoryStore on main thread could block — needs async or cached
- Composite observations could produce absurdly long context — need to cap total context length
- Topic roulette could force comments on boring topics (weather hasn't changed in 6 hours) — need staleness check
- Cross-platform: new senses need platform stubs that return None so Windows doesn't crash
- Weather location is PII-adjacent — don't log lat/lon, don't send to LLM, only send conditions

## Done criteria
- Weather sense returns current conditions and the buddy comments on weather naturally
- Music sense detects playing track from Music.app, Spotify, or YouTube Music (browser tab)
- Productivity sense computes time-in-app, switch frequency, and streaks from existing data
- Cross-sense composites fire when multiple signals align (e.g., high CPU + high RAM + app switching)
- Change detection metadata tells the LLM *what changed* not just *what is*
- Topic roulette prevents 3+ consecutive comments on the same topic
- All new senses degrade gracefully (return None on failure, don't crash the app)
- `pytest` passes, `ruff check` clean, `mypy` clean
- Manual test: run the buddy for 10+ minutes and observe varied commentary topics

## Parking lot
- **Status bar enrichment**: Show current weather (temp + condition), currently playing song (artist - track), productivity streak, and other live sense data in the console status bar. UX designer recommended: `snarky | Ghostty | 72F sunny | Radiohead - OK Computer | 11 min` with truncation rules (app 12 chars, weather temp + 1 word, music 25 chars, omit segments with no data)
