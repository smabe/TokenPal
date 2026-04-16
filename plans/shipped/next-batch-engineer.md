# TokenPal Next Feature Batch -- Python Engineer Analysis

**Perspective:** Python engineer responsible for implementing, testing, and shipping the next batch of senses on all 4 target machines (Mac M-series, Dell XPS 16 Intel, AMD laptop RTX 4070, AMD desktop RX 9070 XT).

---

## 1. Priority Matrix: Planned Senses Ranked by Cost vs. Value

| Sense | Effort | Value | Ship Order | Rationale |
|---|---|---|---|---|
| **Idle** | ~2h | HIGH | 1st | Zero new deps. `pynput` already in deps. Just track last-input timestamp. |
| **Clipboard** | ~3h | HIGHEST | 2nd | `pyperclip` already in deps. Poll-and-diff. Privacy filtering is the real work, not the code. |
| **Music** | ~6h | MEDIUM-HIGH | 3rd | macOS is `osascript` one-liner; Windows needs COM/WinRT; Linux is D-Bus. Three separate impls. |
| **Deep Hardware** | ~4h | MEDIUM | 4th | GPU monitoring via `pynvml` (NVIDIA), `pyamdgpuinfo` (AMD Linux), WMI (Windows). Already have `psutil` base to extend. |
| **Screen Capture** | ~3h | MEDIUM | 5th | `mss` already in deps. Capture is trivial -- the question is what to DO with the bytes without OCR/vision. Useful as a building block. |
| **Web Search** | ~8h | LOW | 6th | Needs an HTTP call to a search API, result parsing, query extraction from context. More of a "brain" feature than a sense. Defer. |
| **OCR** | ~10h | MEDIUM | 7th | Depends on screen_capture. macOS has `Vision.framework` via pyobjc; Windows has `Windows.Media.Ocr`; Linux needs Tesseract. Three codepaths, all painful. |
| **Vision** | ~12h | HIGH (long-term) | 8th | Requires a multimodal LLM backend (LLaVA, Gemma 3 vision). Depends on screen_capture. The `AbstractLLMBackend.supports_vision()` hook exists but nothing uses it yet. Big project. |
| **Voice** | ~15h | LOW | 9th (skip) | Needs always-on mic access, VAD, speech-to-text. Privacy nightmare. `vosk` or `whisper.cpp` bindings are flaky cross-platform. Not worth it for quip generation. |

**Recommendation:** Ship idle + clipboard together as one PR. Then music. Everything else is batch 3+.

### Idle Sense -- Implementation Sketch

```python
# tokenpal/senses/idle/idle_sense.py
@register_sense
class IdleSense(AbstractSense):
    sense_name = "idle"
    platforms = ("windows", "darwin", "linux")
    priority = 100

    async def setup(self) -> None:
        from pynput import mouse, keyboard
        self._last_input = time.monotonic()
        # Listeners run in their own threads (pynput handles this)
        self._mouse_listener = mouse.Listener(on_move=self._on_input)
        self._kb_listener = keyboard.Listener(on_press=self._on_input)
        self._mouse_listener.start()
        self._kb_listener.start()
        self._was_idle = False

    def _on_input(self, *args: Any) -> None:
        self._last_input = time.monotonic()

    async def poll(self) -> SenseReading | None:
        idle_seconds = time.monotonic() - self._last_input
        is_idle = idle_seconds > 120  # 2-minute threshold, configurable

        if is_idle and not self._was_idle:
            self._was_idle = True
            return self._reading(
                data={"idle_seconds": idle_seconds, "event": "went_idle"},
                summary=f"User went idle {int(idle_seconds)}s ago",
            )
        elif not is_idle and self._was_idle:
            self._was_idle = False
            return self._reading(
                data={"idle_seconds": 0, "event": "returned"},
                summary=f"User returned after {int(idle_seconds)}s away",
            )
        return None  # Only report transitions, not steady-state

    async def teardown(self) -> None:
        self._mouse_listener.stop()
        self._kb_listener.stop()
```

Key detail: only emit readings on **transitions** (active->idle, idle->active), not every poll. This prevents the brain from getting spammed with "still idle" readings.

### Clipboard Sense -- Implementation Sketch

```python
# tokenpal/senses/clipboard/clipboard_sense.py
@register_sense
class ClipboardSense(AbstractSense):
    sense_name = "clipboard"
    platforms = ("windows", "darwin", "linux")
    priority = 100

    async def setup(self) -> None:
        self._prev_hash: int = 0
        self._copy_count: int = 0

    async def poll(self) -> SenseReading | None:
        try:
            text = pyperclip.paste()
        except pyperclip.PyperclipException:
            return None

        if not text:
            return None

        h = hash(text)
        if h == self._prev_hash:
            return None
        self._prev_hash = h
        self._copy_count += 1

        # Classify without leaking content
        shape = self._classify(text)
        return self._reading(
            data={"shape": shape, "length": len(text), "copy_number": self._copy_count},
            summary=f"User copied {shape} ({len(text)} chars, copy #{self._copy_count} this session)",
        )

    def _classify(self, text: str) -> str:
        if text.startswith(("http://", "https://")):
            return "a URL"
        if "\n" in text and len(text) > 200:
            return "a large code/text block"
        if any(kw in text.lower() for kw in ("error", "exception", "traceback")):
            return "an error message"
        if len(text) < 20:
            return "a short snippet"
        return "some text"
```

Critical: **never put clipboard content into the SenseReading summary or data**. Only shapes and metadata. The LLM prompt must never see raw clipboard text.

---

## 2. Cross-Platform Gotchas

### App Awareness

The current `MacOSAppAwareness` uses `NSWorkspace` + `Quartz.CGWindowListCopyWindowInfo`. Windows equivalent:

| Task | macOS | Windows | Linux |
|---|---|---|---|
| Foreground app | `NSWorkspace.sharedWorkspace().frontmostApplication()` | `win32gui.GetForegroundWindow()` + `win32process` | `xdotool getactivewindow` or `Gio` via D-Bus |
| Window title | `CGWindowListCopyWindowInfo` | `win32gui.GetWindowText(hwnd)` | `xdotool getactivewindow getwindowname` |

Windows impl needs `pywin32` (already in optional deps). File goes in `tokenpal/senses/app_awareness/win32_apps.py`. The `platforms = ("windows",)` declaration handles the rest -- the registry picks the right impl automatically.

**The actual gotcha:** `pyobjc` imports are sprinkled at module top level in `macos_apps.py` (line 28-29 in `setup()`). This is already correctly deferred to `setup()` with a try/except. The same pattern must be used for `win32gui` imports on Windows. Never import platform-specific modules at the top of the file.

### Music

This is the worst cross-platform sense:
- **macOS:** `osascript -e 'tell application "Music" to get {name, artist} of current track'` or Spotify's AppleScript interface. Subprocess call, ~50ms.
- **Windows:** Spotify has no COM API. Options: (a) `Windows.Media.Control` via `winsdk` -- the "System Media Transport Controls" API that reads whatever is in the Windows media overlay. This is the right answer. (b) Polling Spotify's local HTTP API on port 4381 (undocumented, breaks randomly). Use (a).
- **Linux:** D-Bus `org.mpris.MediaPlayer2` -- standardized, works with Spotify/VLC/everything.

Recommendation: three separate files (`macos_music.py`, `win32_music.py`, `linux_music.py`), all `@register_sense` with `sense_name = "music"`. The registry's priority system handles the rest.

### Clipboard

`pyperclip` handles cross-platform clipboard access but has edge cases:
- **macOS:** Works via `pbpaste`. Fine.
- **Windows:** Works via `win32clipboard`. Fine.
- **Linux:** Requires `xclip` or `xsel` installed, or Wayland's `wl-paste`. `pyperclip` will raise `PyperclipException` if none are found. The `setup()` method should detect this and `self.disable()` with a warning.

### Idle Detection

`pynput` works on all three platforms but:
- **macOS:** Needs Accessibility permissions in System Preferences. If denied, listeners silently fail. The `setup()` method should test if events are actually being received within 2 seconds and warn.
- **Linux Wayland:** `pynput` does not work on Wayland. It needs X11. On Wayland, fall back to `dbus` idle detection (`org.gnome.Mutter.IdleMonitor` or `org.freedesktop.ScreenSaver`).
- **Windows:** Works out of the box.

### Screen Capture

`mss` works everywhere. No gotchas. The frames come as raw BGRA bytes. If feeding to OCR/vision, convert to PNG in-memory with `PIL` or `mss`'s built-in `.png` export.

---

## 3. Resource Usage and Polling Intervals

TokenPal runs 8-10 hours/day. Every watt matters on battery. Current architecture: single `asyncio.sleep(poll_interval)` loop polls ALL senses every 2 seconds. This is wrong for an all-day app.

### Per-Sense Polling Intervals

Senses have wildly different costs and staleness tolerances. Add a `poll_interval_s` class variable to `AbstractSense`:

```python
class AbstractSense(abc.ABC):
    sense_name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]]
    priority: ClassVar[int] = 100
    poll_interval_s: ClassVar[float] = 2.0  # NEW: per-sense cadence
```

Then the brain loop becomes:

```python
async def _poll_all_senses(self) -> list[SenseReading]:
    now = time.monotonic()
    tasks = []
    for s in self._senses:
        if s.enabled and (now - s._last_polled) >= s.poll_interval_s:
            tasks.append(self._poll_one(s))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ...
```

Recommended intervals:

| Sense | Interval | Why |
|---|---|---|
| time_awareness | 30s | Time doesn't change fast. |
| app_awareness | 2s | User switches apps frequently. |
| hardware | 10s | CPU/RAM don't swing in < 10s. Battery even slower. |
| clipboard | 1s | Copies happen in bursts. Need low latency to catch them. |
| idle | 1s | Input events are tracked by pynput listeners, but poll checks transitions. |
| music | 5s | Track changes every ~3 minutes. 5s is plenty. |
| screen_capture | 10s | Expensive (full frame grab). Only if OCR/vision is active. |
| ocr | 15s | CPU-heavy. Only run on screen_capture change. |
| vision | 30s | GPU-heavy. Only run when something interesting is on screen. |

### Power Budget

On battery, double all intervals and skip screen_capture entirely. Detect battery state (already available via `psutil.sensors_battery()`):

```python
# In Brain.__init__ or a dedicated PowerManager
if psutil.sensors_battery() and not psutil.sensors_battery().power_plugged:
    for sense in self._senses:
        sense.poll_interval_s *= 2
```

### Measured Costs (Targets)

- Idle state (no LLM calls): **< 0.5% CPU, < 50 MB RSS**
- Active polling (no LLM): **< 1% CPU**
- LLM generation (Ollama): **spikes to 30-80% CPU/GPU for 0.5-2s**, then back to idle. This is fine -- it's the LLM's problem, not ours.

The main risk is `mss` screen capture. A full-screen grab on a 4K display is ~30 MB of BGRA data. Don't keep more than 1-2 frames in memory. Don't capture more often than every 10 seconds.

---

## 4. Interestingness Scoring

The current implementation in `tokenpal/brain/context.py` (lines 43-63) computes interestingness as `len(changed_lines) / len(current_lines)`. This is a pure text diff ratio. Problems:

1. **Time sense changes every poll** (the timestamp string changes). This means interestingness is always >= 0.2 just from time changing. The threshold of 0.3 barely filters anything.
2. **No weighting.** Switching apps is more interesting than CPU ticking from 12% to 13%.
3. **No decay.** If context hasn't changed in 5 minutes, the FIRST change should be highly interesting. Currently it's scored the same as a change after 10 seconds.
4. **No semantic awareness.** Seeing "VS Code" -> "VS Code" (same app, different file) vs "VS Code" -> "Firefox" (app switch) are treated identically if the summary text happens to have the same diff ratio.

### Proposed Replacement: Weighted Event Scoring

Replace the line-diff approach with explicit event scoring on `SenseReading`:

```python
@dataclass
class SenseReading:
    sense_name: str
    timestamp: float
    data: dict[str, Any]
    summary: str
    confidence: float = 1.0
    interest_weight: float = 0.5  # NEW: sense self-reports how interesting this is
```

Each sense knows its own domain. `AppAwarenessSense` can report `interest_weight=0.9` for an app switch and `0.1` for same-app-same-title. `TimeSense` can report `0.0` for normal ticks and `0.7` for "it's now 2 AM and you're still working."

Then `ContextWindowBuilder.interestingness()` becomes:

```python
def interestingness(self) -> float:
    if not self._recent_readings:
        return 0.0

    # Weighted sum of recent readings, decayed by age
    now = time.monotonic()
    score = 0.0
    for r in self._recent_readings:
        age = now - r.timestamp
        decay = max(0.0, 1.0 - (age / _READING_TTL))
        score += r.interest_weight * decay

    # Normalize: if nothing has happened for a while, first event gets bonus
    time_since_last_comment = now - self._last_comment_time
    boredom_bonus = min(0.3, time_since_last_comment / 300)  # caps at 5min

    return min(1.0, score + boredom_bonus)
```

This lets the end-user persona's advice ("silence is the default") emerge naturally: senses report low interest most of the time, the threshold filters it, and the boredom bonus lets TokenPal break silence when there's been nothing to say for a while.

---

## 5. Error Handling

Current pattern (from `orchestrator.py` lines 47-53 and 74-84):

```python
# Setup: try/except per sense, disable on failure
try:
    await sense.setup()
except Exception:
    log.exception("Failed to set up sense '%s'", sense.sense_name)
    sense.disable()

# Poll: gather with return_exceptions=True, log and skip
results = await asyncio.gather(*tasks, return_exceptions=True)
for r in results:
    if isinstance(r, Exception):
        log.debug("Sense poll error: %s", r)
```

**Assessment: Good foundation, but needs two additions.**

### Addition 1: Exponential Backoff for Flaky Senses

A sense that throws on every poll will log at `DEBUG` level and be silently ignored, but it still gets called every 2 seconds forever. Add a failure counter and backoff:

```python
class AbstractSense(abc.ABC):
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._enabled = True
        self._consecutive_failures: int = 0
        self._max_failures: int = 10  # disable after 10 consecutive failures
        self._backoff_until: float = 0.0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        # Backoff: 2s, 4s, 8s, 16s, 32s... capped at 60s
        backoff = min(60.0, 2.0 ** self._consecutive_failures)
        self._backoff_until = time.monotonic() + backoff
        if self._consecutive_failures >= self._max_failures:
            log.warning("Sense '%s' failed %d times, disabling", self.sense_name, self._max_failures)
            self.disable()

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._backoff_until = 0.0

    @property
    def ready(self) -> bool:
        return self._enabled and time.monotonic() >= self._backoff_until
```

Then `_poll_all_senses` checks `s.ready` instead of `s.enabled`, and wraps each result with `record_failure()` / `record_success()`.

### Addition 2: Sense Health in UI

The console overlay should show a dim status line like `senses: time ok | app ok | hardware ok | clipboard FAIL`. The user currently has no way to know if a sense silently disabled itself.

---

## 6. Session Memory Architecture

This is the #1 feature from the end-user analysis. Here's how to build it.

### Storage: SQLite, Not JSON

JSON works for config but not for append-heavy observation logs. SQLite gives us:
- Atomic appends (no file corruption on crash)
- Efficient time-range queries ("what happened yesterday at 2pm?")
- Size management via `DELETE WHERE timestamp < ?`
- Ships with Python (`sqlite3` stdlib)

Location: `~/.tokenpal/memory.db` (XDG on Linux: `$XDG_DATA_HOME/tokenpal/memory.db`).

### Schema

```sql
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,          -- Unix epoch
    sense_name TEXT NOT NULL,
    event_type TEXT NOT NULL,         -- 'app_switch', 'idle_return', 'clipboard_copy', etc.
    summary TEXT NOT NULL,            -- Human-readable, same as SenseReading.summary
    data_json TEXT,                   -- Optional structured data, JSON-encoded
    session_id TEXT NOT NULL          -- UUID per session, for grouping
);

CREATE TABLE daily_summaries (
    date TEXT PRIMARY KEY,            -- 'YYYY-MM-DD'
    summary TEXT NOT NULL,            -- LLM-generated or rule-based daily summary
    top_apps TEXT,                    -- JSON list of [app, minutes] pairs
    total_active_minutes INTEGER,
    total_idle_minutes INTEGER
);

CREATE INDEX idx_obs_time ON observations(timestamp);
CREATE INDEX idx_obs_sense ON observations(sense_name);
```

### Memory Manager

```python
# tokenpal/memory/store.py
class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._session_id = uuid.uuid4().hex[:8]
        self._init_schema()

    def record(self, reading: SenseReading) -> None:
        """Append an observation. Called from brain loop."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO observations (timestamp, sense_name, event_type, summary, data_json, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), reading.sense_name, reading.data.get("event", "poll"),
                 reading.summary, json.dumps(reading.data), self._session_id),
            )
            self._conn.commit()

    def recent_context(self, hours: int = 24, limit: int = 20) -> list[str]:
        """Fetch recent observations for LLM context injection."""
        cutoff = time.time() - (hours * 3600)
        rows = self._conn.execute(
            "SELECT summary FROM observations WHERE timestamp > ? ORDER BY timestamp DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        return [r[0] for r in reversed(rows)]

    def daily_summary(self, date: str) -> str | None:
        """Get or generate a daily summary."""
        row = self._conn.execute(
            "SELECT summary FROM daily_summaries WHERE date = ?", (date,)
        ).fetchone()
        return row[0] if row else None

    def prune(self, keep_days: int = 30) -> int:
        """Delete observations older than keep_days. Run nightly."""
        cutoff = time.time() - (keep_days * 86400)
        with self._lock:
            cursor = self._conn.execute("DELETE FROM observations WHERE timestamp < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount
```

### Integration with Brain

The `PersonalityEngine.build_prompt()` gets a new `memory_context` parameter:

```python
def build_prompt(self, context_snapshot: str, memory_lines: list[str] | None = None) -> str:
    parts = [self._persona, "", "What you see right now:", context_snapshot]
    if memory_lines:
        parts.extend(["", "What you remember from earlier:", *memory_lines])
    parts.extend(["", "Your comment (one short sentence, under 12 words):"])
    return "\n".join(parts)
```

**Size budget:** With `gemma3:4b`'s 8K context, the persona prompt takes ~300 tokens, current context ~200 tokens, leaving ~500 tokens for memory (roughly 20 one-line observations). This is tight. Memory lines should be pre-filtered to only the most "callback-worthy" items: app switches, idle returns, clipboard events. Not hardware ticks.

---

## 7. Testing Strategy

The current test directory has empty `__init__.py` files and an empty `conftest.py`. There's no test coverage. Here's how to build it without needing a running OS environment.

### Layer 1: Pure Logic Tests (No Mocking Needed)

These test the parts that don't touch the OS:

```python
# tests/test_brain/test_context.py
def test_interestingness_first_reading_is_max():
    ctx = ContextWindowBuilder()
    ctx.ingest([SenseReading(sense_name="test", timestamp=1.0, data={}, summary="hello")])
    assert ctx.interestingness() == 1.0

def test_interestingness_identical_readings_is_zero():
    ctx = ContextWindowBuilder()
    reading = SenseReading(sense_name="test", timestamp=1.0, data={}, summary="hello")
    ctx.ingest([reading])
    _ = ctx.interestingness()  # prime
    ctx.ingest([reading])
    assert ctx.interestingness() == 0.0

# tests/test_brain/test_personality.py
def test_filter_strips_quotes():
    pe = PersonalityEngine("test")
    assert pe.filter_response('"Hello world"') == "Hello world"

def test_filter_returns_none_for_silent():
    pe = PersonalityEngine("test")
    assert pe.filter_response("[SILENT]") is None

# tests/test_ui/test_ascii_renderer.py
def test_speech_bubble_wraps_text():
    bubble = SpeechBubble(text="a" * 60, max_width=40)
    lines = bubble.render()
    assert all(len(line) <= 42 for line in lines)  # +2 for border chars
```

### Layer 2: Sense Tests with Fakes

Create a `FakeSense` in conftest that returns canned readings:

```python
# tests/conftest.py
class FakeSense(AbstractSense):
    sense_name = "fake"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, readings: list[SenseReading | None]) -> None:
        super().__init__({})
        self._readings = iter(readings)

    async def setup(self) -> None: pass
    async def poll(self) -> SenseReading | None:
        return next(self._readings, None)
    async def teardown(self) -> None: pass
```

This lets you test the brain orchestrator's polling/interestingness/cooldown logic without touching any real OS APIs.

### Layer 3: Sense Integration Tests with Platform Guards

```python
# tests/test_senses/test_hardware.py
import pytest
import psutil

@pytest.mark.skipif(not hasattr(psutil, "cpu_percent"), reason="psutil not available")
async def test_hardware_sense_returns_reading():
    sense = PsutilHardware({})
    await sense.setup()
    reading = await sense.poll()
    assert reading is not None
    assert "cpu_percent" in reading.data
    await sense.teardown()
```

For macOS-only senses:

```python
# tests/test_senses/test_app_awareness.py
import sys
import pytest

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
async def test_macos_app_awareness():
    from tokenpal.senses.app_awareness.macos_apps import MacOSAppAwareness
    sense = MacOSAppAwareness({})
    await sense.setup()
    if sense.enabled:  # pyobjc might not be installed
        reading = await sense.poll()
        assert reading is not None
        assert "app_name" in reading.data
    await sense.teardown()
```

### Layer 4: Clipboard/Idle Tests with Mocking

```python
# tests/test_senses/test_clipboard.py
from unittest.mock import patch

async def test_clipboard_detects_new_copy():
    sense = ClipboardSense({})
    await sense.setup()
    with patch("pyperclip.paste", return_value="https://example.com"):
        reading = await sense.poll()
    assert reading is not None
    assert reading.data["shape"] == "a URL"
    # Content is NOT in summary
    assert "example.com" not in reading.summary
```

### CI Setup

Use GitHub Actions with a matrix:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
    python-version: ["3.12", "3.13"]
```

Platform-specific tests auto-skip on the wrong OS via `pytest.mark.skipif`. The pure logic tests (Layer 1-2) run everywhere.

---

## 8. Packaging

### Option A: PyInstaller (Recommended for v1)

PyInstaller bundles Python + deps into a single executable. This is the fastest path to "non-dev can install it."

```bash
pip install pyinstaller
pyinstaller --onefile --name tokenpal --add-data "config.default.toml:." tokenpal/__main__.py
```

Gotchas:
- **pyobjc on macOS:** PyInstaller struggles with pyobjc's dynamic imports. Need explicit `--hidden-import` flags for `AppKit`, `Quartz`, etc. Or use `--collect-submodules pyobjc`.
- **pynput on macOS:** Needs Accessibility permissions. The `.app` bundle needs an `Info.plist` with `NSAccessibilityUsageDescription`. Use `--osx-bundle-identifier com.tokenpal.desktop` + a custom plist.
- **Tkinter on macOS:** Not bundled by default. Either ship console-only or add `--hidden-import tkinter`.
- **Windows antivirus:** PyInstaller executables are frequently flagged as malware. Code-sign the exe (costs ~$200/year for an EV cert) or accept that users will need to whitelist.
- **Binary size:** Expect 30-80 MB depending on included deps. `--onefile` is slow to start (unpacks to temp dir). `--onedir` is faster but messier.

### Option B: Homebrew Tap (macOS Only)

Create a tap at `github.com/smabe/homebrew-tokenpal`:

```ruby
class Tokenpal < Formula
  include Language::Python::Virtualenv
  desc "AI desktop buddy powered by local LLMs"
  homepage "https://github.com/smabe/TokenPal"
  url "https://github.com/smabe/TokenPal/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "..."
  depends_on "python@3.14"
  depends_on "python-tk@3.14"  # if using tkinter overlay

  def install
    virtualenv_install_with_resources
  end
end
```

This handles Python version management and PATH setup. Users do `brew install smabe/tokenpal/tokenpal && tokenpal`.

### Option C: pipx (Cross-Platform, Developer-Adjacent)

For users who have Python but aren't developers:

```bash
pipx install tokenpal
# or from git:
pipx install git+https://github.com/smabe/TokenPal.git
```

`pipx` creates an isolated venv and puts `tokenpal` on PATH. No system Python pollution. This is the best middle ground.

### Recommendation

Ship `pipx` instructions first (zero packaging work). Build PyInstaller bundles for releases. Add Homebrew tap when there's a stable release. Windows gets a `.exe` from PyInstaller + optional winget manifest later.

Add to `pyproject.toml`:

```toml
[project.gui-scripts]
tokenpal-gui = "tokenpal.app:main"  # for PyInstaller to find the entry point
```

---

## 9. Performance Budget

### Targets

| State | CPU (single core %) | RSS (MB) | Rationale |
|---|---|---|---|
| Idle (no LLM calls, senses polling) | < 0.5% | < 50 | Must be invisible in Activity Monitor. |
| Active (LLM generating) | < 2% (our code) + LLM cost | < 80 | The LLM uses its own memory. We control ours. |
| Screen capture active | < 1% spike per capture | < 120 | One 4K frame = ~30 MB. Release immediately. |
| Peak (capture + OCR + LLM) | < 5% sustained | < 200 | This is the ceiling. Above this, users notice. |

### Where to Measure

Add a lightweight self-monitor to the brain loop:

```python
# tokenpal/util/perf.py
import os, psutil

_PROCESS = psutil.Process(os.getpid())

def snapshot() -> dict[str, float]:
    mem = _PROCESS.memory_info()
    return {
        "rss_mb": mem.rss / (1024 ** 2),
        "cpu_percent": _PROCESS.cpu_percent(interval=None),
    }
```

Log this every 60 seconds at DEBUG level. If RSS exceeds 200 MB, log a warning. This gives you visibility into real-world usage patterns.

### Known Cost Centers

1. **`psutil.cpu_percent()`**: Calls into the kernel each time. At 2s intervals this is fine. At 0.5s it adds measurable overhead. This is why hardware should poll at 10s.
2. **`mss.grab()`**: Allocates a full-frame buffer. On a 3840x2160 display at 4 bytes/pixel, that's 33 MB per grab. The garbage collector handles it, but if you grab twice before the first buffer is freed, you're briefly at 66 MB just for screen data. Always `del` the frame after use.
3. **`pyobjc` CGWindowListCopyWindowInfo**: Returns an NSArray of NSDictionaries. These are bridged Python objects. The iteration in `macos_apps.py` (lines 47-58) creates Python wrappers for every window on screen. On a machine with 50+ windows, this takes ~5-10ms. Not a problem at 2s intervals, but don't drop this below 1s.
4. **SQLite writes (memory store)**: `INSERT` + `COMMIT` per observation is ~0.1ms. But if you're writing inside the async brain loop, the blocking I/O stalls the event loop. Either use `aiosqlite` or run writes in `asyncio.to_thread()`.

### Memory Leak Watch

The `ContextWindowBuilder._history` deque is capped at 50 entries (line 20 of `context.py`). Good. But `_readings` is a plain dict that grows by one key per unique `sense_name` -- currently 3-4 entries, but with 10+ senses this dict should be checked for stale entries. The `_READING_TTL` of 120s already handles staleness in `snapshot()`, but the dict itself never shrinks. Add periodic cleanup:

```python
def _prune_stale(self) -> None:
    now = time.monotonic()
    stale = [k for k, v in self._readings.items() if now - v.timestamp > _READING_TTL]
    for k in stale:
        del self._readings[k]
```

---

## 10. Technical Debt to Fix Before Adding Features

### Debt 1: No Per-Sense Polling Cadence (BLOCKING)

The brain loop polls every sense at the same interval (`poll_interval_s = 2.0`). Adding screen_capture at 2s intervals will kill battery. **Fix this before adding any expensive sense.** See Section 3 for the design.

Implementation: add `poll_interval_s` and `_last_polled` to `AbstractSense` in `tokenpal/senses/base.py`. Update the gather loop in `tokenpal/brain/orchestrator.py` lines 74-84.

### Debt 2: No Sense Configuration Passthrough (BLOCKING)

`resolve_senses()` in `tokenpal/senses/registry.py` (line 79) passes `configs.get(sense_name, {})` to sense constructors, but `app.py` (line 34-37) never populates `sense_configs`:

```python
senses = resolve_senses(
    sense_flags=sense_flags,
    sense_overrides=config.plugins.sense_overrides,
    # sense_configs is missing!
)
```

Senses like clipboard need config (e.g., `privacy_mode = true`). Idle needs `idle_threshold_s`. Add a `[senses.clipboard]`, `[senses.idle]`, etc. section to the TOML schema and wire it through.

Proposed TOML addition:

```toml
[senses.clipboard]
enabled = true
privacy_mode = true    # never log content

[senses.idle]
enabled = true
threshold_s = 120      # seconds before considered idle

[senses.music]
enabled = false
```

This requires changing `SensesConfig` from flat booleans to a dict-of-dicts, or adding a separate `SenseSettingsConfig` dataclass. The flat-boolean approach in `schema.py` won't scale.

### Debt 3: `time.monotonic()` in SenseReading Timestamps

`SenseReading.timestamp` uses `time.monotonic()` (set in `base.py` line 62). Monotonic clocks are good for measuring intervals but meaningless for session memory -- you can't store "4:30 PM" as a monotonic value. The memory store needs wall-clock timestamps.

Fix: add both. Keep `monotonic_ts` for brain-loop freshness checks, add `wall_ts: float` (from `time.time()`) for persistence and display.

### Debt 4: No Graceful Degradation for Missing Ollama

If Ollama isn't running, `HttpBackend.setup()` logs a warning (line 37 of `http_backend.py`) but doesn't disable itself. Then `generate()` throws `httpx.ConnectError` every 15 seconds, caught by the brain loop's bare `except Exception`. TokenPal silently does nothing.

Fix: add a health state to `AbstractLLMBackend`. If `setup()` can't connect, enter a retry-with-backoff mode. Show "LLM offline" in the UI status line. When it reconnects, resume.

### Debt 5: Brain Stop is Racy

`app.py` line 85: `asyncio.run(brain.stop())` creates a NEW event loop to run `stop()`. But the brain is running in its own event loop in the daemon thread. This means `brain.stop()` runs in a different loop than `brain.start()`, so `self._running = False` is a cross-thread write on a plain bool (not thread-safe in theory, though CPython's GIL makes it safe in practice).

Fix: use `threading.Event` instead of a bare `bool` for `_running`. Or better, use the brain thread's loop to schedule the stop:

```python
# In app.py, instead of asyncio.run(brain.stop()):
brain_loop = asyncio.get_event_loop()  # save ref when creating the thread
brain_loop.call_soon_threadsafe(lambda: asyncio.ensure_future(brain.stop()))
```

### Debt 6: No `__all__` Exports

None of the `__init__.py` files define `__all__`. This isn't a bug, but `pkgutil.walk_packages` imports everything it finds, which can trigger unexpected side effects if a module has top-level code. Adding `__all__` to each package's `__init__.py` is good hygiene.

### Debt 7: Missing `py.typed` Marker

`pyproject.toml` enables strict mypy, but the package doesn't include a `py.typed` marker file. Add an empty `tokenpal/py.typed` so downstream consumers (and mypy itself in some configurations) know this package ships type information.

---

## Summary: Recommended Execution Order

1. **Fix Debt 1-2** (per-sense polling + config passthrough) -- 2-3 hours. These block everything else.
2. **Fix Debt 3-4** (timestamps + Ollama health) -- 1-2 hours. Quick wins.
3. **Implement IdleSense** -- 2 hours. Depends on Debt 1 for proper polling.
4. **Implement ClipboardSense** -- 3 hours. Depends on Debt 2 for privacy config.
5. **Implement session memory** (MemoryStore + brain integration) -- 4-6 hours. The big project.
6. **Implement interestingness v2** -- 2-3 hours. Can happen in parallel with memory.
7. **Add tests** (Layers 1-3) -- 3-4 hours. Can happen in parallel with everything.
8. **Implement MusicSense** (macOS first, then Windows/Linux) -- 6 hours total, split across platforms.
9. **Fix Debt 5-7** -- 1 hour. Cleanup pass.
10. **PyInstaller packaging** -- 2-3 hours per platform.

Total estimate: ~30-40 hours of focused implementation for the full batch.
