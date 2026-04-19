# Natural-language triggers for /intent and /summary

## Goal
Let the user trigger `/intent` and `/summary` with plain English ("give me a summary", "remind me to finish the auth PR", "what am I working on") so they don't have to remember the slash syntax. Typed slashes keep working unchanged.

## Non-goals
- NL triggers for any other slash command (no `/ask`, `/mood`, `/research`, etc.). If this proves useful we can extend later.
- LLM-based intent classification. This is pure regex — cheap, deterministic, testable.
- Natural-language disambiguation UI ("did you mean…?"). If a phrase doesn't match, it falls through to the brain as normal conversation.
- Rewording/paraphrase coverage beyond the specific phrasings listed below. We're not chasing 100% recall.
- Fuzzy matching or typo tolerance.

## Files to touch
- `tokenpal/nl_commands.py` — NEW module. Pure function `match_nl_command(text) -> tuple[name, args] | None`. Draft already written — needs review + possible tightening after research pass.
- `tokenpal/app.py` — hook `match_nl_command` into `_on_user_input` (around line 1003). On hit, route through the existing slash dispatcher so bubbles/errors behave identically.
- `tests/test_nl_commands.py` — NEW. Cover each pattern (summary: bare / today / yesterday; intent: set / status / clear; timer-hint guard; non-match fall-through; slash-prefixed input ignored).
- `CLAUDE.md` — one-line note under the Slash Commands section that `/intent` and `/summary` also respond to plain English.

## Failure modes to anticipate
- **Timer vs intent collision**: "remind me in 5 min to drink water" should NOT become an intent — that's the `timer` LLM tool. Guarded by a `_TIMER_HINT` regex that detects `in N <unit>` and falls through.
- **Overzealous matching during conversation**: user says "recap" mid-chat about something else and gets a surprise EOD bubble. Mitigated by strict anchored patterns (`^...$`) and short, specific phrasings — not keyword-sniffing.
- **Intent pattern catches "remind me" generically**: "remind me why we did this" would become an intent titled "why we did this". Acceptable tradeoff — user can `/intent clear` to undo. Documented in plan, not fixed.
- **Trailing punctuation**: user types "give me a summary?" or "remind me to X." — matcher strips `? . !` from the end before matching.
- **Case sensitivity**: all patterns `re.IGNORECASE`.
- **Empty-goal from loose regex**: "remind me to" (no goal) must NOT set an empty intent. Guarded by `if not goal: return None`.
- **Slash-prefixed input reaching `_on_user_input`**: shouldn't happen (textual_overlay splits on `/`), but defensive `startswith("/")` short-circuit anyway.
- **User input logged twice**: `_on_user_input` is called AFTER `_log_user(text)` in the overlay, so the original NL text is already in the chat log. The slash-dispatch result will follow it — fine.
- **Thread context**: `_on_user_input` runs on the Textual main thread. Dispatching slash commands from there is already done by `_on_command`, so no new threading concerns.

## Done criteria
- `match_nl_command` returns the expected `(name, args)` tuples for every phrasing listed in the draft module.
- `match_nl_command` returns `None` for: empty string, slash-prefixed input, arbitrary conversation ("how are you"), timer-hint phrases ("remind me in 5 min to X"), empty-goal phrases ("remind me to").
- `_on_user_input` in `app.py` calls the matcher; on hit, dispatches through the existing slash dispatcher and returns without forwarding to `brain.submit_user_input`. On miss, behavior is byte-identical to before.
- `pytest tests/test_nl_commands.py` green.
- `ruff check tokenpal/nl_commands.py tokenpal/app.py tests/test_nl_commands.py` clean.
- Manual smoke: launch `tokenpal`, type "give me a summary" and "remind me to test this", observe correct behavior.
- CLAUDE.md updated with the one-line note.

## Parking lot
