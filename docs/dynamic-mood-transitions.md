# Dynamic Mood Transitions (V2)

Parked from the custom-moods feature. V1 uses role-mapping (each voice's 6 moods tagged with heuristic roles like default/sleepy/bored). This doc tracks the richer V2 approach.

## Idea

During voice training, the LLM generates character-specific transition rules instead of just mood names. Rules map measurable signals to mood shifts, enabling per-character behavior like BMO going DRAMATIC when a game is detected.

## Available Signals

These are the signals `update_mood()` already tracks:

| Signal | Type | Example |
|---|---|---|
| `hour` | int (0-23) | Late night, early morning |
| `context_unchanged_count` | int | Same app for N poll cycles |
| `elapsed_in_mood` | float (seconds) | Time since last mood shift |
| `app_switch` | bool | App changed this cycle |
| `context_keywords` | list[str] | "commit", "deploy", etc. in context |

## What's Needed

1. **Rule schema** — structured format for conditions. Probably a list of dicts: `{"trigger": "hour_range", "params": [2, 5], "target": "WORRIED", "priority": 1}`. Not freeform natural language — must map to measurable signals.
2. **Training prompt** — ask LLM to output transition rules in the structured format alongside mood definitions. Main risk: parsing reliability.
3. **Data-driven `update_mood()`** — replace if/elif chain with a priority-ordered rule loop. ~30 lines.
4. **Validation** — detect broken rules during training (circular transitions, unreachable moods, missing default fallback).
5. **VoiceProfile expansion** — `mood_transitions: list[MoodRule]` field + serialization.
6. **Character-specific triggers** — the real payoff. Pirate gets EXCITED near weather apps, BMO goes DRAMATIC for games. Requires an app-pattern matching system in the rules.

## Why V2

The role-mapping approach in V1 covers 90% of the value (character-appropriate mood names and descriptions) with zero added runtime complexity. Dynamic transitions add expressiveness but also add training fragility and a new validation surface. Better to ship V1, see how voices feel in practice, then layer this on.
