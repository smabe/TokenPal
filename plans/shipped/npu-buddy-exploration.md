# "Windoze" — NPU Desktop Buddy Exploration Plan

## Context
Build a desktop buddy: an **ASCII character** that floats on screen, **watches what you're doing** (screen capture → vision model), **comments on it** via local LLM, and optionally **speaks/listens** — all using local accelerators. Two target machines, very different hardware profiles.

## Your two machines

| | **Dell XPS 16 (2026) — Work** | **AMD Ryzen 9 8945HS — Personal** |
|---|---|---|
| CPU | Intel Core Ultra (latest) | AMD Ryzen 9 8945HS (Hawk Point) |
| NPU | Intel AI Boost (40+ TOPS) | AMD XDNA 1st gen (**16 TOPS** — below Copilot+ 40 TOPS threshold) |
| GPU | Integrated Intel Arc | **RTX 4070 Laptop** + Radeon 780M iGPU |
| RAM | TBD | 32 GB |
| Copilot+ PC? | **Yes** — Windows AI APIs available | **No** — 16 TOPS is too low |
| Best inference target | NPU (power-efficient, always-on) | **RTX 4070** via CUDA/TensorRT (way more powerful than the NPU) |

**Key insight:** On the personal laptop, forget the NPU — the RTX 4070 is ~10x more capable. The NPU story there is limited to small background tasks at best. On the Dell, the NPU is the star.

---

## SDK options across both machines

### Option A: Python + ONNX Runtime (cross-machine, recommended for prototyping)
- **Works on both laptops.** ONNX Runtime auto-selects execution provider:
  - Dell: OpenVINO EP → Intel NPU
  - AMD+4070: CUDA EP → RTX 4070 (or Vitis AI EP → XDNA NPU for small models)
- UI via **tkinter** (transparent, always-on-top, click-through windows are well-supported on Windows)
- Screen capture via **mss** or **PIL.ImageGrab**
- TTS via **pyttsx3** (wraps Windows SAPI) or **Piper** (ONNX-based, better voices)
- STT via **Whisper** (whisper.cpp or faster-whisper with CUDA on the 4070)
- **Pros:** Fast iteration, one codebase, huge model ecosystem, can target GPU or NPU
- **Cons:** Python on Windows is fiddly, tkinter UI is basic, packaging is annoying (PyInstaller/Nuitka)

### Option B: C# + WinUI 3 / WPF (Dell-focused, best Windows AI integration)
- First-class access to **Phi Silica**, **Windows.Graphics.Capture**, **Windows AI APIs** (OCR, Image Description)
- Best for the Dell where NPU + Copilot+ APIs are the selling point
- OpenVINO.NET available for fallback
- **Pros:** Cleanest WinRT/NPU integration, proper Windows desktop app
- **Cons:** Doesn't help on the AMD machine (no Copilot+ APIs), C# learning curve if unfamiliar

### Option C: llama.cpp + Vulkan/CUDA backend (GPU-focused, personal laptop)
- **llama.cpp** with CUDA backend → RTX 4070 runs 7B+ models easily
- Can also use Vulkan backend for cross-GPU compatibility
- Pair with a Python or Tauri frontend
- **Pros:** Runs much bigger models (7B-13B) on the 4070, mature ecosystem
- **Cons:** Not using the NPU at all

### Option D: Foundry Local / LM Studio (highest-level, least code)
- **Foundry Local** (Microsoft) or **LM Studio** — pre-built local model runners
- Your app just calls a local HTTP API (OpenAI-compatible)
- They handle model download, quantization, NPU/GPU dispatch
- **Pros:** Literally zero model management code. Works on both machines.
- **Cons:** External dependency running alongside your app, less control, one more thing to install

---

## Recommended approach: **Python + ONNX Runtime** (Option A) with Option D as fallback

**Why Python?** You want to explore on both machines quickly. Python + ONNX Runtime lets you:
1. Write one codebase that targets NPU on Dell and GPU on the personal laptop
2. Swap models trivially (Phi-3-mini → Florence-2 → Whisper)
3. Use tkinter for the ASCII buddy overlay (transparent window is ~20 lines)
4. Graduate to C#/WinUI 3 later if you want a polished Dell-specific version

**Why Option D fallback?** If ONNX Runtime EP setup is painful, just run LM Studio or Foundry Local in the background and hit `localhost:1234/v1/chat/completions`. Your buddy code becomes pure UI + screen capture + API calls.

---

## Stack per capability

| Capability | Both machines | Dell NPU-specific | Personal laptop GPU-specific |
|---|---|---|---|
| Chat LLM | Phi-3-mini INT4 via ONNX Runtime | Phi Silica (if available) | Llama-3-8B via llama.cpp CUDA |
| Screen vision | Florence-2 or Moondream via ONNX | Windows AI Image Description API | Qwen2.5-VL-7B via CUDA (bigger = better) |
| Screen capture | `mss` or `PIL.ImageGrab` | `Windows.Graphics.Capture` (C# only) | same as left |
| TTS | `pyttsx3` (Windows SAPI) or Piper ONNX | `Windows.Media.SpeechSynthesis` | same as left |
| STT | Whisper-tiny ONNX | Whisper-tiny via OpenVINO on NPU | Whisper-medium via faster-whisper CUDA |
| UI | tkinter transparent overlay | WinUI 3 (if going C#) | same as left |

---

## Buddy "senses" — what the passive commentator can perceive

The buddy is a **passive commentator** — it watches, reacts, and comments but never takes action. Its personality comes from *what it notices*. Each "sense" is a modular skill that feeds observations into the LLM for commentary.

### Tier 1 — Easy wins (Python + Win32, no AI needed)

| Sense | What it detects | How | Commentary examples |
|---|---|---|---|
| **App awareness** | Which app is in foreground + window title | `win32gui.GetForegroundWindow()` + `GetWindowText()` via pywin32 | "Oh cool, another 47 Chrome tabs" / "Back to VS Code... let's see how long this lasts" |
| **Idle detection** | How long since last input | `GetLastInputInfo()` via ctypes | "Hello? Anyone home?" / "Impressive nap. 23 minutes." |
| **Time awareness** | Time of day, day of week, how long you've been working | `datetime` stdlib | "It's 2 AM and you're still in Excel. Respect." / "Happy Friday! ...why are you in Jira?" |
| **Clipboard watching** | Text copied to clipboard (not images/files) | `AddClipboardFormatListener` via ctypes or `pyperclip` polling | "You just copied a Stack Overflow URL. Classic." / "That's a lot of JSON." |
| **Music awareness** | Currently playing song/artist | `GlobalSystemMediaTransportControlsSessionManager` via `winrt` or `winmedia-controller` PyPI | "Ah, lo-fi beats. The universal 'I'm pretending to focus' signal." |
| **System vitals** | CPU %, RAM %, battery, plugged/unplugged | `psutil` | "Battery at 12%. Living dangerously." / "RAM at 94%. Chrome wins again." |
| **GPU/NPU monitoring** | Per-GPU load, VRAM usage, NPU utilization | `pynvml` (NVIDIA), `psutil` + WMI for AMD/Intel, `GPUtil` | "RTX 4070 at 98% — who's mining crypto?" / "NPU just woke up. Rare sighting." |
| **Thermals & fans** | CPU/GPU temps, fan RPM | `wmi` + `OpenHardwareMonitor` / `LibreHardwareMonitor` (via its WMI interface) or `psutil.sensors_temperatures()` | "CPU at 97°C. We're cooking. Literally." / "Fans just hit jet engine mode." |
| **Per-process breakdown** | Top CPU/RAM/GPU hogs by process | `psutil.process_iter()` + `pynvml` for GPU-per-process | "Chrome is using 8 GB of RAM across 47 processes. A personal best." |
| **Network I/O** | Upload/download rates, active connections | `psutil.net_io_counters()` + `net_connections()` | "Downloading at 2 KB/s. The 90s called." |
| **Disk activity** | Read/write rates, free space | `psutil.disk_io_counters()` + `disk_usage()` | "C: drive has 3 GB free. We're living on the edge." |
| **Window counting** | How many windows/tabs are open | `EnumWindows` via pywin32 | "67 windows open. This is fine." |

### Tier 2 — Needs AI (NPU or GPU inference)

| Sense | What it detects | How | Commentary examples |
|---|---|---|---|
| **Screen reading** | What's visually on screen (text, images, UI elements) | Screenshot via `mss` → vision model (Florence-2, Moondream, or Windows AI Image Description) | "That error message has been on screen for 10 minutes. Just saying." |
| **OCR** | Readable text from screen regions | Windows AI OCR API (Copilot+ only) or Tesseract fallback | "I see you're writing an email to your boss that starts with 'Per my last email'... bold move." |
| **Sentiment/vibe** | Whether what you're doing looks stressful vs. chill | Screen content → LLM inference with "rate the stress level" prompt | "You've switched apps 14 times in 2 minutes. Everything okay?" |

### On-demand skill: Web search (only when asked)

The buddy is normally passive, but you can **ask it a question** (via text input or push-to-talk). When asked, it can search the web to inform its response. Still in character — it's not a helpful assistant, it just happens to know things.

| Aspect | Implementation |
|---|---|
| **Trigger** | User types a question in the buddy's input box, or speaks via push-to-talk |
| **Search backend** | `duckduckgo-search` PyPI package (no API key needed) or `requests` + SearXNG self-hosted |
| **Flow** | Question → web search → top 3 snippets → LLM generates in-character answer |
| **Persona stays** | "You asked me what the weather is. It's 72°F. I don't know why you couldn't just look outside." |

### Tier 3 — Stretch goals

| Sense | What it detects | How | Notes |
|---|---|---|---|
| **Voice listening** | Ambient speech / push-to-talk | Whisper-tiny via ONNX Runtime | Can react to what you say out loud; also used to trigger on-demand questions |
| **Meeting detection** | Whether you're in a video call | Detect Zoom/Teams window + camera/mic usage via Windows device APIs | "I'll keep quiet while you're in this meeting... unless it gets boring" |
| **Git awareness** | Recent commits, branch, dirty state | Shell out to `git status` / `git log` in CWD | "3 hours on this branch and 0 commits. Brave." |
| **Weather** | Current weather via free API | `requests` + OpenWeatherMap free tier | "It's 95°F outside. Good thing you're here with me." |

### How senses feed the LLM

Every N seconds (configurable, default 30s), the buddy:
1. Polls all active Tier 1 senses (cheap, no AI)
2. Optionally captures a screenshot for Tier 2 senses (expensive, rate-limited)
3. Builds a **context blob** like:
   ```
   [TIME] 2:47 AM, Tuesday. Working for 4h 12m.
   [APP] VS Code — "main.py - windoze"
   [IDLE] Last input 3s ago
   [MUSIC] "Midnight City" by M83
   [BATTERY] 34%, unplugged
   [SCREEN] Code editor showing Python, terminal panel open with error traceback
   [CLIPBOARD] Last copied: "IndexError: list index out of range"
   ```
4. Feeds context blob + persona system prompt → LLM → comment
5. **Throttle:** Only surface a comment if it's "interesting enough" (simple heuristic: did the context change meaningfully since last comment?)

### Persona system prompt (example)
```
You are Windoze, a tiny ASCII creature who lives in the corner of a Windows desktop.
You are a passive observer — you NEVER offer help, suggestions, or solutions.
You just comment on what you see, like a sarcastic roommate glancing at your screen.
Keep comments under 15 words. Be funny, not mean. Reference specific details you can see.
If nothing interesting is happening, say nothing (respond with [SILENT]).
```

---

## macOS on Apple Silicon — Feasibility

**Very feasible.** Apple Silicon is arguably the *best* platform for this kind of app.

| Aspect | macOS (M-series) | Notes |
|---|---|---|
| LLM inference | **MLX** (Apple's framework, built for Apple Silicon) or llama.cpp → Metal | MLX is purpose-built for M-series unified memory. First-class Python support via `mlx-lm`. |
| Neural Engine | 16-core ANE on M1+, ~80x more power-efficient per op than GPU | Good for small background models (Whisper-tiny, classifiers). Not great for LLMs yet — GPU/Metal is preferred. M5 Neural Accelerators in the GPU change this. |
| Screen capture | `mss` (cross-platform) or `CGWindowListCreateImage` via pyobjc | Needs Screen Recording permission in System Preferences |
| Overlay | tkinter works, but **pyobjc + NSWindow** needed for proper full-screen overlay | `NSWindowCollectionBehaviorCanJoinAllSpaces` + `FullScreenAuxiliary` |
| App awareness | `NSWorkspace.sharedWorkspace().frontmostApplication()` via pyobjc | Also `CGWindowListCopyWindowInfo` for window titles |
| Hardware monitoring | `psutil` for basics, `powermetrics` (sudo) for detailed thermals/ANE | No `pynvml` equivalent — Apple doesn't expose GPU utilization per-process |
| Music | macOS Media Remote framework, or `osascript` to query Music.app/Spotify | Less clean than Windows SMTC but works |
| Clipboard | `pyperclip` or `NSPasteboard` via pyobjc | Cross-platform with pyperclip |
| TTS | `NSSpeechSynthesizer` via pyobjc, or `say` command | macOS built-in voices are excellent |
| STT | Whisper via MLX, or Apple Speech Recognition framework | MLX Whisper is very fast on M-series |

**Bottom line:** macOS + M-series is a first-class target. MLX for LLM, pyobjc for native overlay, psutil + powermetrics for hardware. The main platform-specific work is the UI overlay (NSWindow vs tkinter) and the LLM backend (MLX vs ONNX/llama.cpp).

---

## Architecture — Modular OOP Design

### Design principles
- **Every component is swappable** — senses, LLM backends, UI overlays are all abstract base classes with platform-specific concrete implementations
- **Plugin discovery via decorators** — `@register_sense`, `@register_backend`, `@register_overlay` — no core code changes to add new components
- **Config-driven** — TOML config determines which senses are active, which LLM backend, polling intervals, persona prompt
- **Async senses, sync UI** — senses poll concurrently via `asyncio.gather()` in a background thread; UI runs on main thread (required by tkinter/AppKit)

### Directory structure

```
windoze/
├── pyproject.toml
├── config.default.toml          # ships with app
├── config.toml                  # user overrides (gitignored)
├── windoze/
│   ├── __init__.py
│   ├── __main__.py              # entry point: python -m windoze
│   ├── app.py                   # bootstrap: discovery → resolution → wiring → run
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── schema.py            # dataclass config schema (WindozeConfig)
│   │   └── loader.py            # TOML loading, env overrides, validation
│   │
│   ├── brain/
│   │   ├── __init__.py
│   │   ├── orchestrator.py      # central Brain loop — polls senses, feeds LLM, decides when to comment
│   │   ├── context.py           # ContextWindowBuilder — assembles sense data into LLM prompt
│   │   └── personality.py       # persona prompt templates, comment filtering, [SILENT] detection
│   │
│   ├── senses/
│   │   ├── __init__.py
│   │   ├── base.py              # AbstractSense + SenseReading dataclass
│   │   ├── registry.py          # @register_sense, discover_senses(), resolve_senses()
│   │   ├── screen_capture/
│   │   │   ├── base.py          # AbstractScreenCapture(AbstractSense)
│   │   │   ├── mss_capture.py   # cross-platform fallback (priority 200)
│   │   │   ├── win32_capture.py # Windows GDI/DXGI (priority 100)
│   │   │   └── macos_capture.py # CGWindowListCreateImage (priority 100)
│   │   ├── app_awareness/
│   │   │   ├── base.py
│   │   │   ├── win32_apps.py    # win32gui.GetForegroundWindow
│   │   │   └── macos_apps.py    # NSWorkspace.frontmostApplication
│   │   ├── clipboard/
│   │   │   ├── base.py
│   │   │   ├── generic_clipboard.py  # pyperclip (all platforms, priority 200)
│   │   │   ├── win32_clipboard.py    # AddClipboardFormatListener
│   │   │   └── macos_clipboard.py    # NSPasteboard
│   │   ├── hardware/
│   │   │   ├── base.py               # AbstractHardwareSense with typed methods
│   │   │   ├── psutil_hardware.py    # cross-platform baseline (priority 200)
│   │   │   ├── nvidia_hardware.py    # extends psutil + pynvml (priority 100)
│   │   │   ├── win32_hardware.py     # extends nvidia + WMI thermals/NPU (priority 50)
│   │   │   └── macos_hardware.py     # extends psutil + powermetrics/IOKit (priority 100)
│   │   ├── music/
│   │   │   ├── base.py
│   │   │   ├── win32_music.py        # GlobalSystemMediaTransportControls
│   │   │   └── macos_music.py        # Media Remote / osascript
│   │   ├── idle/
│   │   │   ├── base.py
│   │   │   ├── win32_idle.py         # GetLastInputInfo
│   │   │   └── macos_idle.py         # CGEventSourceSecondsSinceLastEventType
│   │   ├── time_awareness/
│   │   │   └── time_sense.py         # pure stdlib, all platforms
│   │   ├── ocr/
│   │   │   ├── base.py
│   │   │   └── tesseract_ocr.py
│   │   ├── vision/
│   │   │   ├── base.py
│   │   │   └── llm_vision.py         # delegates to LLM backend's vision capability
│   │   ├── voice/
│   │   │   ├── base.py
│   │   │   ├── whisper_voice.py
│   │   │   └── macos_voice.py
│   │   └── web_search/
│   │       ├── base.py
│   │       └── duckduckgo_search.py
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py              # AbstractLLMBackend + LLMResponse dataclass
│   │   ├── registry.py          # same pattern as senses
│   │   ├── onnx_backend.py      # ONNX Runtime (OpenVINO EP → Intel NPU, CUDA EP → GPU)
│   │   ├── llamacpp_backend.py  # llama-cpp-python (CUDA on Windows, Metal on macOS)
│   │   ├── mlx_backend.py       # MLX for Apple Silicon (darwin only)
│   │   └── http_backend.py      # OpenAI-compatible API (LM Studio, Ollama, Foundry)
│   │
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── base.py              # AbstractOverlay
│   │   ├── registry.py
│   │   ├── tk_overlay.py        # tkinter (all platforms, fallback)
│   │   ├── macos_overlay.py     # pyobjc NSWindow (full-screen support)
│   │   └── ascii_renderer.py    # pure logic: BuddyFrame + SpeechBubble dataclasses
│   │
│   └── util/
│       ├── platform.py          # current_platform(), has_nvidia_gpu(), has_npu(), is_apple_silicon()
│       └── logging.py
│
├── plugins/                     # drop-in third-party senses
│   └── example_sense/
│
└── tests/
    ├── conftest.py
    ├── test_brain/
    ├── test_senses/
    ├── test_llm/
    └── test_ui/
```

### Core abstractions

**AbstractSense** (`senses/base.py`):
```python
class AbstractSense(abc.ABC):
    sense_name: ClassVar[str]            # e.g. "screen_capture"
    platforms: ClassVar[tuple[str, ...]]  # ("windows", "darwin", "linux")
    priority: ClassVar[int] = 100        # lower = preferred when multiple impls exist

    async def setup(self) -> None: ...
    async def poll(self) -> SenseReading | None: ...  # None = nothing interesting
    async def teardown(self) -> None: ...
```

**AbstractLLMBackend** (`llm/base.py`):
```python
class AbstractLLMBackend(abc.ABC):
    backend_name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]]

    async def setup(self) -> None: ...
    async def generate(self, prompt: str, max_tokens: int = 256) -> LLMResponse: ...
    async def stream(self, prompt: str, max_tokens: int = 256) -> AsyncIterator[str]: ...
    async def supports_vision(self) -> bool: ...
    async def teardown(self) -> None: ...
```

**AbstractOverlay** (`ui/base.py`):
```python
class AbstractOverlay(abc.ABC):
    overlay_name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]]

    def setup(self) -> None: ...
    def show_buddy(self, frame: BuddyFrame, x: int, y: int) -> None: ...
    def show_speech(self, bubble: SpeechBubble) -> None: ...
    def hide_speech(self) -> None: ...
    def run_loop(self) -> None: ...              # blocks main thread
    def schedule_callback(self, cb, delay_ms=0): ...  # thread-safe UI update
    def teardown(self) -> None: ...
```

### Plugin registration pattern (same for senses, LLM, UI)

```python
@register_sense
class MssScreenCapture(AbstractSense):
    sense_name = "screen_capture"
    platforms = ("windows", "darwin", "linux")
    priority = 200  # generic fallback

@register_sense
class MacOSScreenCapture(AbstractSense):
    sense_name = "screen_capture"
    platforms = ("darwin",)
    priority = 100  # preferred on macOS
```

At startup: `discover_senses()` walks all subpackages → decorators fire → `resolve_senses()` picks the best impl per platform + config.

### Threading model

```
Main Thread                    Brain Thread (asyncio)
───────────                    ──────────────────────
overlay.setup()                brain.start()
overlay.run_loop()  ◄───────── brain._ui_callback() → overlay.schedule_callback()
  tkinter mainloop             asyncio.gather(sense.poll()...)
  or NSRunLoop                 llm.generate(prompt)
```

### Config (`config.default.toml`)

```toml
[senses]
screen_capture = true
app_awareness = true
clipboard = true
music = false
hardware = true
idle = true
time_awareness = true
ocr = false
vision = false
voice = false
web_search = false

[llm]
backend = "http"                            # "onnx" | "llamacpp" | "mlx" | "http"
api_url = "http://localhost:1234/v1"
model_path = ""
max_tokens = 256
temperature = 0.8

[ui]
overlay = "auto"                            # "auto" | "tkinter" | "macos_nswindow"
buddy_name = "Windoze"
position = "bottom_right"

[brain]
poll_interval_s = 2.0
comment_cooldown_s = 15.0
interestingness_threshold = 0.3
persona_prompt = """You are Windoze, a tiny ASCII creature on a desktop.
You just comment on what you see, like a sarcastic roommate.
Keep comments under 15 words. Be funny, not mean. Reference specifics.
If nothing interesting is happening, respond with [SILENT]."""

[plugins]
extra_packages = []
```

---

## Exploration roadmap

### Phase 0 — Verify hardware on both machines (30 min each)

**On the Dell XPS 16:**
1. Task Manager → Performance → confirm "NPU 0" appears
2. Install latest Intel NPU driver
3. Install **Windows AI Dev Gallery** from Microsoft Store — test Phi Silica, Image Description, OCR
4. Note which APIs work and which are CPU-fallback

**On the AMD personal laptop:**
1. Task Manager → confirm NPU 0 shows "AMD Radeon NPU Compute Accelerator Device" (you already have this ✓)
2. Install **Ryzen AI Software SDK** from amd.com (includes Vitis AI EP for ONNX Runtime)
3. Verify RTX 4070 CUDA works: `python -c "import torch; print(torch.cuda.is_available())"`
4. Install **LM Studio** → download Phi-3-mini-4k GGUF → confirm it runs on GPU

### Phase 1 — Python "hello NPU/GPU" on both machines (half day)
- `pip install onnxruntime-genai` (or `onnxruntime-gpu` for CUDA)
- Load Phi-3-mini INT4 ONNX model
- Generate text, confirm the right accelerator is being used (Task Manager)
- On personal laptop: also test via llama.cpp with `--n-gpu-layers 99` to compare

### Phase 2 — ASCII buddy shell (1 day)
- Tkinter transparent overlay window, always-on-top
- ASCII art frames for idle/talking/thinking states
- Speech bubble that displays text
- Hardcoded funny comments first — no AI yet, just get the UI right

### Phase 3 — Screen capture → vision → comment pipeline (1-2 days)
- Capture foreground window every 10-30 seconds via `mss`
- Feed to Florence-2 or Moondream (small VLM, ~2B params) via ONNX Runtime
- Pipe description to chat LLM with buddy persona prompt
- Display comment in speech bubble
- Throttle aggressively — buddy should surprise you, not spam you

### Phase 4 — Voice (optional, 1 day)
- Push-to-talk via global hotkey (`keyboard` or `pynput` library)
- Whisper-tiny for STT
- pyttsx3 or Piper for TTS
- Full loop: you speak → buddy hears → buddy sees screen → buddy responds with voice

---

## Critical resources
- [Copilot+ PCs developer guide](https://learn.microsoft.com/en-us/windows/ai/npu-devices/)
- [Windows ML execution providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers)
- [Microsoft Foundry on Windows](https://learn.microsoft.com/en-us/windows/ai/overview)
- [AMD Ryzen AI Software SDK](https://www.amd.com/en/developer/resources/ryzen-ai-software.html)
- [Ryzen AI SDK docs (v1.7)](https://ryzenai.docs.amd.com/en/latest/)
- [AMD + Windows ML blog](https://www.amd.com/en/blogs/2025/empowering-ai-pcs-with-amd-and-windowsml.html)
- [OpenVINO NPU device docs](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)
- [Intel NPU driver](https://www.intel.com/content/www/us/en/download/794734/intel-npu-driver-windows.html)
- [DirectML NPU support](https://blogs.windows.com/windowsdeveloper/2024/08/29/directml-expands-npu-support-to-copilot-pcs-and-webnn/)
- [LM Studio Snapdragon NPU tests](https://www.xda-developers.com/these-llms-run-locally-snapdragon-x-elite-npu-surprisingly-good/)
- [Running SLMs on NPU with Copilot+ PCs](https://pkbullock.com/blog/2025/running-slm-models-using-npu-with-copilot-pc)
- [tkinter transparent overlays guide](https://medium.com/@skash03/getting-started-with-desktop-overlays-with-python-and-tkinter-bfa92a23cf0)

## Verification
- **Phase 0:** Both machines show accelerator utilization during inference; Dev Gallery works on Dell
- **Phase 1:** Python script generates text using NPU (Dell) or GPU (personal), confirmed via Task Manager
- **Phase 2:** ASCII character renders in transparent overlay, stays on top, doesn't steal focus
- **Phase 3:** Screen capture → vision description → LLM comment pipeline runs end-to-end in <5s. Buddy says something at least vaguely relevant to what's on screen.
- **Phase 4:** Speak → hear transcription → see buddy respond

## Honest assessment
- **Personal laptop NPU (16 TOPS XDNA 1):** Realistically too weak for LLMs. Use it for tiny background tasks (keyword detection, simple classification) at best. The RTX 4070 is your real AI engine here.
- **Dell NPU (Intel AI Boost):** The proper Copilot+ experience. Phi Silica + Windows AI APIs should "just work" if Intel support has landed. Power-efficient, always-on inference.
- **Vision is still the hardest part** on both machines. Small VLMs exist but are less polished than text-only models.
- **Python prototype first, C# polish later** if you want to ship something nice on the Dell.

---

---

## Dev Environment

### Primary dev machine: Mac (Apple Silicon)

**Why:** Cleanest inference stack (MLX), already set up, forces cross-platform abstractions from day one. 80% of initial work is platform-agnostic Python.

### Toolchain
- **Python 3.12+** via Homebrew (ARM64 native)
- **venv** per project (no conda, no poetry — keep it simple)
- **Ollama** as the day-one LLM backend (HTTP API, zero model management code)
- **Git** + GitHub for cross-machine sync
- **VS Code** or terminal — whatever you prefer
- **ruff** for linting/formatting, **mypy** for type checking, **pytest** for tests

### Day-one workflow
```
Mac (dev machine)                    Other machines (later)
─────────────────                    ─────────────────────
1. Scaffold project structure        4. Clone repo
2. Implement core abstractions       5. pip install + platform deps
   - AbstractSense, registry         6. Run verification checklist
   - AbstractLLMBackend (http only)     from dev-setup-*.md
   - AbstractOverlay (tkinter)       7. Implement platform-specific
   - Brain orchestrator                 senses (win32, nvidia, amd)
   - Config system                   8. Test on each machine
3. Build first working prototype
   - Ollama for LLM (http backend)
   - tkinter overlay (cross-platform)
   - 2-3 senses: app_awareness,
     time_awareness, hardware (psutil)
```

### Machine roles going forward
| Machine | Role | When to use |
|---|---|---|
| **Mac (M-series)** | Primary dev, macOS testing, MLX backend dev | Daily driver |
| **Desktop (9800X3D + 9070 XT)** | Stress testing, big models (13B+), ROCm/Vulkan backend dev | When you need 16 GB VRAM or AMD GPU testing |
| **Personal laptop (8945HS + RTX 4070)** | CUDA backend dev, NVIDIA hardware sense, mobile testing | When you need CUDA or are away from desk |
| **Dell XPS 16 (Intel Core Ultra)** | NPU testing, Copilot+ APIs, OpenVINO backend dev | When exploring NPU-specific features |

### Git strategy
- Single repo, one branch for core development
- Feature branches for platform-specific work
- Each machine clones the same repo
- `config.toml` is gitignored (machine-specific settings)
- `config.default.toml` ships with sane defaults + HTTP backend

### What to build first (on the Mac)
1. Project scaffold (`pyproject.toml`, directory structure, config system)
2. `AbstractSense` + `SenseReading` + registry pattern
3. `AbstractLLMBackend` + `HttpBackend` (talks to Ollama)
4. `AbstractOverlay` + `TkOverlay` (transparent window, ASCII character)
5. `Brain` orchestrator (poll → context → LLM → display)
6. 3 starter senses: `time_awareness`, `app_awareness` (macOS), `hardware` (psutil)
7. First working demo: buddy that comments on what app you're in + time of day

That's your "it works on one machine" milestone. Everything after is adding senses and platform backends.

---

## Starter prompts

### For the Dell XPS 16 (work laptop)
```
I'm building "Windoze" — an ASCII desktop buddy that floats on screen, watches what I'm doing via screen capture + a vision model, and comments on it using a local LLM. All inference should target the Intel AI Boost NPU.

Hardware: Dell XPS 16 (2026), Intel Core Ultra, Intel AI Boost NPU, Copilot+ PC.

Before writing any code, verify my setup:
1. Check Intel NPU driver: `pnputil /enum-devices /class {a4643098-56eb-4022-a6b2-b855ee979993}`
2. Check Windows AI APIs: `Get-WindowsCapability -Online | Where-Object Name -like '*Windows.AI*'`
3. Check if Windows AI Dev Gallery is installed from Microsoft Store

Then create a Python project with:
- onnxruntime-genai for Phi-3-mini INT4 text generation on NPU
- mss for screen capture
- tkinter for a transparent always-on-top ASCII character overlay
- Start with just the chat LLM hello world — confirm NPU utilization in Task Manager
```

### For the AMD personal laptop
```
I'm building "Windoze" — an ASCII desktop buddy that floats on screen, watches what I'm doing via screen capture + a vision model, and comments on it using a local LLM.

Hardware: AMD Ryzen 9 8945HS, RTX 4070 Laptop GPU, 32GB RAM, AMD XDNA NPU (16 TOPS — too weak for LLMs, ignore it). Target the RTX 4070 for inference.

Before writing any code, verify my setup:
1. Check CUDA is available: `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
2. Install LM Studio and download Phi-3-mini-4k-instruct GGUF — confirm it runs on GPU

Then create a Python project with:
- llama-cpp-python with CUDA backend for chat LLM (Phi-3-mini or larger)
- mss for screen capture
- tkinter for a transparent always-on-top ASCII character overlay
- Start with just the chat LLM hello world — confirm GPU utilization in Task Manager
```
