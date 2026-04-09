# TokenPal Next Feature Batch -- UX Designer Persona Analysis

**Persona**: UX Designer (terminal-native interfaces, ASCII art systems, CLI tool ergonomics)
**Date**: 2026-04-08
**Scope**: Console overlay improvements, animation, color, layout, interaction, and quality-of-life features

---

## 1. Console Layout Improvements

### Current state

The render pipeline in `console_overlay.py` clears the entire screen (`\033[2J\033[H`) and redraws every element on each frame. The layout is: blank line, centered header, blank line, speech bubble OR "zzz..." status, blank line, buddy, blank line, bottom border, quit hint.

### Problems

- **No vertical anchoring.** The buddy floats at the top of the terminal. On a tall terminal (40+ rows), most of the screen is blank below the footer. The buddy should feel like it lives at the bottom of the window, grounded on the status bar.
- **Bubble centering uses `len()` on strings containing box-drawing characters.** Unicode box-drawing chars are single-width, so this works, but if any future text includes CJK or emoji, centering will break. Use `wcwidth` or `unicodedata.east_asian_width` for padding calculations.
- **No breathing room control.** The single blank line between bubble and buddy is hardcoded. When the bubble is long (multi-line), the visual separation feels cramped.

### Recommendations

```
Proposed vertical layout (bottom-anchored):

  Row 0   ─────────── TokenPal ───────────   <- header
  Row 1
  ...     (empty vertical fill)
  Row N-K ╭──────────────────╮                <- speech bubble
          │ Hey, nice code!  │
          ╰──────────────────╯
            ╲
  Row N-6                                     <- 1-line spacer
  Row N-5   ╭───╮                             <- buddy (6 lines)
  Row N-4   │ o o│
  Row N-3   │  ▽ │
  Row N-2   ╰─┬─╯
  Row N-1    /|\
  Row N      / \
  Row N+1 ──────────────────────────────────  <- status bar
  Row N+2   idle | hw: ok | Ctrl+C to quit    <- status content
```

Anchor the buddy to a fixed offset from the bottom of the terminal. Fill unused vertical space above the bubble with empty lines. This makes the buddy feel planted in place and prevents it from jumping when the bubble appears/disappears.

---

## 2. Animation: Idle Breathing and Typing Effect

### Idle breathing

The buddy currently shows a static `BUDDY_IDLE` frame with "zzz..." above it. This looks dead. A subtle 2-frame breathing cycle would make it feel alive without being distracting.

Proposed breathing frames (alternate every ~1.5s):

```
Frame A (inhale):        Frame B (exhale):
  ╭───╮                    ╭───╮
  │ o o│                    │ o o│
  │  ▽ │                    │  ▽ │
  ╰─┬─╯                    ╰─┬─╯
   /|\                      /|\
   / \                      /  \
```

The only difference is the leg spacing (` / \ ` vs ` /  \ `). Subtle enough to register as motion without being annoying. Implement as a timer in `run_loop` that toggles between idle sub-frames when no bubble is active.

### Typing effect for speech

Currently, the full bubble appears instantly. A character-by-character typing effect inside the bubble would:
- Make speech feel natural (the buddy is "saying" it)
- Give users time to read long messages
- Create a reason to show the `BUDDY_TALKING` frame for a sustained period

Implementation approach:
1. On `show_speech()`, start with an empty bubble and the talking frame.
2. Append one character every ~30ms, re-rendering the bubble each time.
3. On completion, hold the full bubble for the existing auto-hide duration.
4. If a new speech arrives mid-typing, cancel the current animation and start the new one.

The render cost is low -- the console is small and `sys.stdout.write` of ~20 lines is sub-millisecond.

---

## 3. Color Scheme

### Current palette

| Element     | Color                          | Code                         |
|-------------|--------------------------------|------------------------------|
| Buddy       | Bright green (`#00FF88`)       | `\033[38;2;0;255;136m`       |
| Bubble text | Near-white (`#DCDCDC`)         | `\033[38;2;220;220;220m`     |
| Borders     | Dim (ANSI dim attribute)       | `\033[2m`                    |

### Issues

- **No background color.** On terminals with light backgrounds, the dim borders vanish and the green buddy loses contrast.
- **No semantic color differentiation.** Thought bubbles, speech bubbles, and shout bubbles all render in the same white. The `style` field on `SpeechBubble` (speech/thought/shout) should map to distinct colors.
- **No "energy" feedback.** The buddy could shift hue slightly based on activity (e.g., warmer when lots of senses are firing, cooler when idle).

### Recommended palette expansion

| Element          | Color              | Rationale                              |
|------------------|--------------------|----------------------------------------|
| Speech text      | White `#DCDCDC`    | Neutral, readable                      |
| Thought text     | Cyan `#00CED1`     | Cooler tone = internal, reflective     |
| Shout text       | Yellow `#FFD700`   | High energy, attention-grabbing        |
| Status bar text  | Dim white          | Low priority, should not compete       |
| Error/alert      | Red `#FF6B6B`      | Standard danger signal                 |
| Header title     | Green (match buddy)| Visual unity between name and character|

Add a `--no-color` flag and respect the `NO_COLOR` environment variable (see https://no-color.org/) for accessibility.

---

## 4. Speech Bubble Alignment Issues

### Bug: centering is off by one when terminal width is odd

In `_render()`:

```python
pad = max(0, (term_width - len(bl)) // 2)
```

When `term_width` is odd and the bubble line length is even (or vice versa), the bubble shifts left by half a character relative to the buddy. Since the buddy lines have fixed width (10 chars) and the bubble width varies with text, they rarely align.

### Fix

Center both bubble and buddy relative to the same anchor column. Compute the center column once per render:

```python
center = term_width // 2
```

Then for each line, pad as:

```python
pad = max(0, center - (len(line) // 2))
```

This guarantees the midpoints of buddy and bubble are on the same column.

### Tail alignment

The speech tail (`╲`) is hardcoded at column 2 of the bubble output. It should point toward the buddy's head, which means it needs to be positioned relative to the buddy's center column, not the bubble's left edge. When the bubble is wide, the tail appears far left of the buddy:

```
Current (bubble wider than buddy):

  ╭──────────────────────────────────╮
  │ This is a really long message    │
  ╰──────────────────────────────────╯
    ╲                                     <- tail at col 2 of bubble
                ╭───╮                     <- buddy center is here
                │ o o│
```

The tail should be repositioned to align with the buddy's horizontal center.

---

## 5. Status Bar Content

### Current state

The bottom status bar shows only `Ctrl+C to quit`. This wastes valuable screen real estate and provides no ongoing feedback.

### Recommended status bar layout

```
─────────────────────────────────────────────────────
  idle  |  5 senses active  |  last spoke 2m ago  |  Ctrl+C quit
```

Four segments, left-to-right:
1. **Buddy state** -- idle / talking / thinking / surprised
2. **Sense summary** -- count of active senses, or a short list (`hw, clip, idle`)
3. **Recency** -- time since last comment (gives users a sense of activity rhythm)
4. **Key hint** -- keep Ctrl+C, add future keybinds here (e.g., `[m] minimal mode`)

Use dim coloring for the entire bar. Update it on every render cycle. The data is already available -- `Brain` has `_last_comment_time` and the sense list is known at startup.

---

## 6. Notification Transitions

### Current behavior

Speech appears instantly (full bubble render) and disappears instantly (bubble set to `None`, re-render). This is jarring -- the buddy snaps between states with no visual continuity.

### Proposed transition sequence

```
Phase 1: Anticipation (~300ms)
  - Switch to BUDDY_SURPRISED frame
  - Show "..." in a small bubble

Phase 2: Speaking
  - Switch to BUDDY_TALKING frame
  - Typing effect fills the bubble (see section 2)

Phase 3: Holding
  - Full bubble displayed
  - Duration = max(4s, len(text) * 0.1s)  [current behavior, keep it]

Phase 4: Fade-out (~500ms)
  - Bubble text dims (apply \033[2m to bubble content)
  - After dim period, remove bubble entirely

Phase 5: Return to idle
  - Switch to BUDDY_IDLE frame
  - Resume breathing animation
```

This gives each comment a natural arc: notice something, say it, let it linger, fade out.

Implementation: the existing `threading.Timer` for auto-hide can be extended into a small state machine driven by `schedule_callback` with staged delays.

---

## 7. ASCII Art Quality

### Current issues

The buddy is 10 characters wide and 6 lines tall. At this size:

- **The face is asymmetric.** `│ o o│` has a leading space before the first `o` but no trailing space after the second `o` before the border. Compare: `│ o o │` would be balanced. This is a 1-character fix but it changes the head width from 5 to 7 inner characters.
- **Arms/legs use ASCII slashes** which look thin compared to the box-drawing head. Consider using diagonal box-drawing characters (`╱` and `╲`, U+2571/U+2572) where terminal support allows, with a fallback to `/` and `\`.
- **No accessory system.** Future personality could be expressed through hats, held items, etc. The ASCII structure should be modular: head (2 lines), body (2 lines), legs (2 lines) as separate arrays that can be mixed.

### Proposed improved idle buddy (7-wide head)

```
   ╭─────╮
   │ o o  │
   │  ▽   │
   ╰──┬──╯
     /|\
     / \
```

This gives the face more breathing room and makes it feel less cramped.

### Frame consistency check

All four frames should have identical outer dimensions so swapping between them never shifts the buddy horizontally. Currently they do (all are 10-char wide), which is correct. Maintain this invariant as new frames are added.

---

## 8. Terminal Size Handling

### Current behavior

`shutil.get_terminal_size()` is called on every render. If the terminal is resized, the next render adapts. However:

- **No minimum size enforcement.** If the terminal is narrower than the buddy (10 cols) or shorter than the layout requires (~14 rows), the output will wrap and look broken.
- **No SIGWINCH handling.** Resize events do not trigger a re-render. The display stays broken until the next scheduled render (speech or idle toggle). On most terminals, resizing causes visual artifacts.

### Recommendations

1. **Listen for SIGWINCH** (on Unix) or poll size in `run_loop` (cross-platform). On resize, immediately re-render.
2. **Enforce minimums.** If terminal is below 20 columns or 12 rows, show a condensed single-line mode:
   ```
   TokenPal: "Hey, nice code!" (o_o)
   ```
3. **Clamp bubble width** to `min(max_width, term_width - 4)` so the bubble never overflows the terminal. Currently `max_width` is hardcoded at 40 in `SpeechBubble` and does not respond to terminal width.

---

## 9. Minimal Mode

### Concept

A single-line mode for users who want TokenPal running but need most of their terminal real estate. Toggled with a keypress (e.g., `m`) or `--minimal` flag.

### Mockup

```
Full mode (current):                  Minimal mode:

  ──── TokenPal ────                  TokenPal: "Nice refactor!" (°◇°) | idle | m=expand
  ╭──────────────────╮
  │ Nice refactor!   │
  ╰──────────────────╯
    ╲
    ╭───╮
    │ ° °│
    │  ◇ │
    ╰─┬─╯
     \|/
     / \
  ──────────────────────
    Ctrl+C to quit
```

Minimal mode renders everything on one line:
- Name
- Last speech in quotes (or "zzz..." if idle)
- Inline emoji-style face derived from the current frame
- State label
- Expand hint

Face mappings for minimal mode:
| Frame     | Inline face |
|-----------|-------------|
| idle      | `(-_-)zzz`  |
| talking   | `(°◇°)`     |
| thinking  | `(-~-)`     |
| surprised | `(O□O)`     |

The `AbstractOverlay` interface does not need to change -- `show_buddy` and `show_speech` still work; only the internal `_render()` method branches on a `self._minimal` flag.

---

## 10. Interactive Elements

### Type-back (user input)

Allow the user to type a message to TokenPal. This is the single most impactful UX addition -- it transforms TokenPal from a passive commenter into a conversational companion.

#### Design constraints

- The console overlay owns `stdout` and clears the screen on every render. User input must not be destroyed by re-renders.
- `run_loop` currently uses `time.sleep(0.1)` polling. It does not read `stdin`.

#### Proposed approach

1. Reserve the bottom 1-2 lines of the terminal for an input area, below the status bar:
   ```
   ──────────────────────────────────────
     idle | 5 senses | Ctrl+C quit
   > _                                      <- input line
   ```
2. Use non-blocking `stdin` reads (e.g., `select.select` on Unix, `msvcrt.kbhit` on Windows) in the `run_loop` poll cycle.
3. When the user presses Enter, send the text to `Brain` via a new `inject_user_message(text)` method, which pushes it into the context window with high interestingness so the LLM responds immediately.
4. While typing, show `BUDDY_THINKING` frame (the buddy is listening).
5. The render function must not clear the input line -- use cursor positioning (`\033[{row};1H`) to redraw only the lines above the input area.

This requires the biggest architectural change (partial screen updates instead of full clears), but it is essential for the product to feel interactive rather than ambient.

### Scroll past comments (history)

Users will miss comments that auto-hide. Provide a scrollable history.

#### Design

- Keep a ring buffer of the last N comments (e.g., 50) with timestamps.
- Pressing `h` (or Up arrow) enters history mode:
  ```
  ──── TokenPal ──── [HISTORY 3/12] ────
  ╭──────────────────────────────╮
  │ 2m ago:                      │
  │ "I see you're writing tests" │
  ╰──────────────────────────────╯

    ╭───╮
    │ - -│    <- thinking face (reviewing history)
    │  ~ │
    ╰─┬─╯
     /|
     / \
  ──────────────────────────────────────
    [Up/Down] navigate  [Esc] exit history
  ```
- While in history mode, the buddy shows the `thinking` frame and the bubble shows the historical comment with a relative timestamp.
- New live comments queue silently and appear when history mode is exited.
- `Esc` or `q` exits history mode and returns to live view.

---

## Priority Ranking

| Priority | Feature                        | Effort | Impact |
|----------|--------------------------------|--------|--------|
| P0       | Speech bubble alignment fix    | Small  | High   |
| P0       | Terminal size handling          | Small  | High   |
| P1       | Bottom-anchored layout         | Medium | High   |
| P1       | Status bar content             | Small  | Medium |
| P1       | Color scheme expansion         | Small  | Medium |
| P1       | Notification transitions       | Medium | High   |
| P2       | Idle breathing animation       | Small  | Medium |
| P2       | Typing effect                  | Medium | Medium |
| P2       | ASCII art quality fixes        | Small  | Low    |
| P2       | Minimal mode                   | Medium | Medium |
| P3       | Type-back user input           | Large  | High   |
| P3       | Scroll past comments           | Medium | Medium |

P0 items are bugs/robustness. Ship them first. P1 items are the visual polish pass. P2 items are the animation/delight pass. P3 items are the interaction model expansion, requiring deeper architectural changes to `ConsoleOverlay` and `Brain`.

---

## Implementation Notes

- All changes are confined to `tokenpal/ui/console_overlay.py` and `tokenpal/ui/ascii_renderer.py` except for type-back, which also touches `tokenpal/brain/orchestrator.py`.
- The `AbstractOverlay` interface in `tokenpal/ui/base.py` may need a `show_status(segments: list[str])` method for the status bar, and an `inject_input(text: str)` callback for type-back.
- Test with at least these terminal sizes: 80x24 (standard), 120x40 (large), 40x12 (small), and 20x8 (tiny/degenerate).
- Respect `$TERM`, `$COLORTERM`, and `$NO_COLOR` for color capability detection.
