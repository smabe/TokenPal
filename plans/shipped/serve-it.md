# serve-it — server switching in the options modal

## Goal
Surface `/server switch` inside the top-level Options modal so users can
flip between local and remote inference without typing a slash command.

## Non-goals
- No changes to the underlying `/server` slash command or its
  persistence/model-restoration logic — the modal reuses it.
- No new server-config editing UI (host/port/api key). Those stay in
  `config.toml`.
- No auto-discovery of remote servers on the LAN.
- No streaming connectivity indicator beyond the existing
  `llm.is_reachable` snapshot.

## Files to touch
- `tokenpal/ui/options_modal.py` — add a "Server" section showing a
  list of known servers, each row rendered as `URL — model` (or
  `URL — (no model remembered)` when we have no mapping). The current
  server gets a visual marker (bold / "(active)" suffix). One Button
  per row triggers a switch. Below the list: a Custom… input for
  ad-hoc URLs.
  - Extend `OptionsModalState` with `current_api_url: str`,
    `known_servers: tuple[ServerEntry, ...]` where each entry carries
    `url`, `model: str | None`, and a display `label`
    (e.g. `"local"`, `"remote"`, or raw host).
  - Extend `OptionsModalResult` with `switch_server_to: str | None`.
- `tokenpal/app.py` — in `_open_options_modal`, build `known_servers`
  by merging:
    1. the configured local URL (`http://localhost:11434/v1`)
    2. the configured remote (from `config.server.host`/`.port`)
    3. every key already present in `config.llm.per_server_models`
       (deduped via `canon_server_url`)
  Each entry pulls its model from `config.llm.per_server_models.get(key)`
  — and for the *active* server, fall back to `llm.model_name` so users
  see what's actually loaded even before a model has been persisted.
  In `on_save`, when `result.switch_server_to` is set, re-dispatch
  through `_cmd_server(f"switch {target}")`.
- `tests/ui/test_options_modal.py` (extend or add) — cover:
  dedup + ordering of the server list, model column rendering (incl.
  `(no model)` placeholder), active marker, Custom… empty-input
  validation, Save with no server change is a no-op.

## Failure modes to anticipate
- Clicking the already-active server — do nothing (no spam, no
  refetch). Diff check in the modal OR the app layer.
- Server list dedup: `config.llm.per_server_models` is already keyed
  by canonical URL, but the configured local/remote URLs need the
  same `canon_server_url` pass before comparison, otherwise the active
  server can show up twice.
- Model column for the active server when no per-server model has
  been persisted yet: show `llm.model_name` so the list isn't empty
  on a fresh machine.
- `per_server_models` growing unbounded over time — out of scope here,
  but keep the list scrollable.
- Custom URL typed without scheme — the existing `_cmd_server` handles
  it, but the modal should feel consistent. Pass the raw string through
  and let `_cmd_server` normalize.
- Custom URL field left empty — disable the Apply button or validate
  on press; don't silently fall through.
- Modal stacking: if the user opens Options → clicks a launcher, the
  current pattern dismisses-then-reopens. Server switching shouldn't
  need to reopen anything, so just dismiss with the result.
- Config persistence: `_cmd_server` already writes to `config.toml`.
  Ensure the modal doesn't also write (would double-persist / race).
- Reachability check: after a switch, `llm.is_reachable` is stale until
  the next poll. Status bar will catch up; don't block the modal on a
  probe.

## Done criteria
- `/options` (or F3) shows a "Server" section with a list of known
  servers, each displaying `URL — model`, plus a Custom… input.
- The active server is visually marked; clicking its row is a no-op.
- Clicking any other row triggers the same codepath as
  `/server switch <url>` — URL updates, model restores if remembered,
  config.toml persists.
- Clicking Custom… with a URL dispatches `/server switch <value>`;
  empty input is rejected at the widget.
- Clicking Save with no server change leaves the server alone.
- Chat-history section still works exactly as before; no regressions
  in `clear-history` or `max_persisted` save.
- Tests pass; ruff + mypy clean on the touched files.

## Phase 2 — model switcher

Extend the Server section (or add a sibling Model section) so the user
can pick from models available on the *active* server.

### Files to touch
- `tokenpal/llm/http_backend.py` — cache the `/v1/models` result during
  `_try_connect` into `self._available_models`, expose via an
  `available_models` property (returns `tuple[str, ...]`, empty when
  not yet probed). No extra HTTP call on the hot path.
- `tokenpal/ui/options_modal.py` — add a "Models (on this server)"
  section: one Button per advertised model labeled `name` with the
  active one bold/disabled. Extend `OptionsModalState` with
  `available_models: tuple[str, ...]`, `current_model: str`. Extend
  `OptionsModalResult` with `switch_model_to: str | None`.
- `tokenpal/app.py` — populate the new state fields from
  `llm.available_models` + `llm.model_name`. In `on_save`, when
  `switch_model_to` is set, re-dispatch through `_cmd_model(model)` so
  persistence via `remember_server_model` stays in one place.
- `tests/test_options_modal.py` — cover new fields + defaults.

### Failure modes
- Server just reconnected / `available_models` still empty — render the
  current model alone with a "(probing…)" hint; don't crash.
- User switches server AND model in the same modal open — disallow for
  v1: server switch dismisses first and the model list is server-scoped
  anyway. Document this.
- Model list is huge (20+ entries): make the section scrollable within
  the existing VerticalScroll body — no special handling needed.

### Done criteria
- Options modal shows a "Models" section listing models advertised by
  `/v1/models` on the active server.
- Active model row is visually marked and disabled.
- Clicking another row switches + persists via `_cmd_model`.
- Empty `available_models` degrades gracefully.

## Parking lot
