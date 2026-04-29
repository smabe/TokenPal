# TokenPal

Cross-platform AI desktop buddy: an ASCII character that observes the user's
screen via modular senses and generates witty, in-character commentary
through a local LLM. This file captures the domain language used across the
codebase so future changes (and the agents helping with them) speak the same
words.

## Language

**Sense**:
A module that observes one slice of the user's environment (foreground app,
typing pace, weather, git state, etc.) and produces a `SenseReading`. Plug-in
discovered via `@register_sense`.
_Avoid_: monitor, watcher, probe, plugin.

**SenseReading**:
The output of one poll of a Sense. Carries `.summary` (natural language,
never bracketed tags), `.changed_from` (transition metadata), `.confidence`,
and `.data`. Has a per-sense `reading_ttl_s` that bounds how long the cached
reading is treated as live.
_Avoid_: observation, snapshot (ambiguous with the brain's snapshot string),
event.

**Brain**:
The async orchestrator. Runs in a daemon thread, polls Senses on a tick,
runs Wedges, builds and filters LLM bubbles, manages conversation sessions
and proactive nudges.
_Avoid_: orchestrator (acceptable in code, but Brain is the user-facing
word), engine, controller.

**Wedge**:
A module that competes for the Brain's one emission slot per tick. Examples:
rage detector, git-nudge detector, intent drift, idle-tool roller. A Wedge
proposes at most one `EmissionCandidate` per tick. The Brain ranks
candidates, applies a gate policy, and runs a single shared riff pipeline
to produce the bubble. ProactiveScheduler is **not** a Wedge: it is a
multi-tenant scheduler that self-emits on its own clock.
_Avoid_: detector, nudger, trigger, generator (each describes one Wedge,
not the role).

**EmissionCandidate**:
What a Wedge returns when it wants to fire. Carries the payload the Wedge
will need to build its prompt, plus implicit metadata (priority and
gate policy live on the Wedge class, not the candidate).
_Avoid_: signal (used for the wedge-internal pre-candidate, e.g.
`RageSignal`), event.

**GatePolicy**:
How a Wedge's candidate interacts with the Brain's comment-rate cap and
fallback paths. Three values: `BYPASS_CAP` (always emit), `NEEDS_CAP_OPEN`
(only when the cap is open), `IDLE_FILL` (only when no other Wedge
proposed this tick).
_Avoid_: priority (priority is a separate integer rank), urgency.

**Riff**:
The Brain's act of turning an `EmissionCandidate` into the final
in-character bubble: build prompt, call LLM, run text guards (clean-english,
drift, sensitive-term filter), emit through `ui_callback`. Owned by the
Brain; the Wedge supplies only `build_prompt(...)`.
_Avoid_: comment (overloaded - "comment" is the user-facing word for any
bubble), generation.

**Action**:
An LLM-callable tool (`@register_action`). Distinct from a Wedge: an Action
is invoked by the LLM during a riff or conversation; a Wedge runs every
brain tick before any LLM call.
_Avoid_: tool (acceptable in user-facing strings; Action is the code term),
function, command.

**Backend**:
A pluggable subsystem registered via the `_BackendRegistry[B]` triple
(`register_X_backend` / `get_X_backend` / `registered_X_backends`). LLM,
audio input, audio output, and TTS all use this shape.
_Avoid_: provider, driver, adapter (Adapter has a specific architectural
meaning; Backend is the registry-membership term).

**Overlay**:
A UI surface that hosts the buddy and routes user input back to the Brain.
Concrete overlays: Qt, Textual, tkinter, console. Plug-in discovered via
`@register_overlay`.
_Avoid_: window, surface, frontend, view.

## Relationships

- A **Sense** produces zero or one **SenseReading** per poll.
- A **Wedge** ingests a list of **SenseReading**s and proposes at most one
  **EmissionCandidate** per Brain tick.
- A **Wedge** declares one **GatePolicy** that controls when its
  **EmissionCandidate** is eligible to riff.
- The **Brain** ranks all **EmissionCandidates** for a tick, picks one,
  and runs one **Riff** through the shared LLM pipeline.
- A **Riff** may invoke zero or more **Actions** during prompt construction
  (e.g. idle-tool wedge invokes a tool whose output goes into the prompt).
- An **Overlay** displays the bubble produced by a **Riff** and forwards
  user input back to the **Brain**.

## Example dialogue

> **Dev:** "If I add a new **Wedge** for screensaver-detection, where does
> the prompt template live?"
> **Domain expert:** "On the Wedge class. The Brain owns the **Riff**
> pipeline, but each Wedge owns its own `build_prompt`. The Wedge's
> `propose` returns an `EmissionCandidate` with whatever payload the prompt
> needs; the Brain calls `wedge.build_prompt(candidate, ctx)` and pipes the
> result through the shared LLM + filter chain."

> **Dev:** "Is `ProactiveScheduler` a **Wedge**?"
> **Domain expert:** "No. A Wedge competes for one slot per tick.
> ProactiveScheduler is a multi-tenant clock that fires registered nudges
> on their own intervals; it self-emits through `ui_callback` and never
> goes through the riff pipeline."

## Flagged ambiguities

- "tool" - in user-facing copy and slash commands (`/idle_tools`), means an
  **Action**. In code, prefer **Action**. The string `tool_name` on
  `IdleFireResult` is grandfathered.
- "signal" - used internally by individual Wedges for their pre-candidate
  state (`RageSignal`, `GitNudgeSignal`). Distinct from
  **EmissionCandidate**, which is the cross-Wedge currency the Brain ranks.
- "comment" vs "bubble" vs "riff" - **Riff** is the act, the **bubble** is
  what the user sees on the **Overlay**, and "comment" is the user-facing
  word in copy and metrics. Prefer Riff in code paths that produce them.
