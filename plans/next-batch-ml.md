# Next Batch ML Strategy — TokenPal

## 1. Chat Model Selection

**Current: gemma3:4b via Ollama — verdict: KEEP, but have a fallback.**

| Model | Size (Q4) | Pros | Cons | Verdict |
|---|---|---|---|---|
| gemma3:4b | ~2.5 GB | Good humor, fast, solid instruction-following | 13s cold start via Ollama | **Current pick** |
| llama3.2:3b | ~2.0 GB | Smaller, fast, decent at short text | Weaker at sarcasm/wit than Gemma 3 | Good fallback |
| phi-4-mini (3.8B) | ~2.3 GB | Strong reasoning for size | Tends toward helpful/formal tone, fights sarcasm persona | Skip |
| smollm2:1.7b | ~1.0 GB | Tiny, fast, low memory | Noticeably worse output quality for creative tasks | Emergency fallback only |
| qwen3:4b | ~2.5 GB | Strong multilingual | `<think>` tags break OpenAI-compat API | **Do not use** |
| gemma3:1b | ~0.8 GB | Ultra-light | Quality cliff for humor — too often states facts | Low-memory fallback |

**Recommendation:** Stay with gemma3:4b primary. Add fallback_models config:
```toml
[llm]
model_name = "gemma3:4b"
fallback_models = ["llama3.2:3b", "smollm2:1.7b"]
```

## 2. Vision Model Strategy

| Model | Params | Size (Q4) | Speed | Quality | Best Platform |
|---|---|---|---|---|---|
| **Moondream2** (2B) | 1.9B | ~1.2 GB | ~1.5s/image | Good scene description | All (ONNX, MLX, llama.cpp) |
| **Florence-2-base** | 0.23B | ~0.5 GB | ~0.3s/image | Excellent OCR+caption | All (ONNX best) |
| **Florence-2-large** | 0.77B | ~1.5 GB | ~0.8s/image | Better descriptions | All (ONNX best) |
| **Qwen2.5-VL-3B** | 3B | ~2.0 GB | ~2s/image | Best quality at size | Ollama, MLX, llama.cpp |
| **InternVL2.5-1B** | 1B | ~0.7 GB | ~0.8s/image | Decent balance | ONNX, llama.cpp |

**Per-platform:**
- **Mac (Apple Silicon):** Moondream2 via MLX or Qwen2.5-VL-3B via Ollama
- **Dell XPS 16 (Intel NPU):** Florence-2-base via ONNX Runtime + OpenVINO EP — strongest NPU play
- **AMD laptop (RTX 4070):** Moondream2 via llama.cpp with CUDA
- **AMD desktop (RX 9070 XT):** Moondream2 via llama.cpp with Vulkan, or Qwen2.5-VL-3B for quality

Vision should be a **sense** (VisionSense), not an LLM feature. VLM produces text description → chat LLM never sees raw images.

## 3. Whisper Variants

| Variant | Params | Size | Latency (5s clip) | RAM |
|---|---|---|---|---|
| **whisper-tiny** | 39M | 42 MB | ~0.3s | ~150 MB |
| **whisper-base** | 74M | 80 MB | ~0.5s | ~250 MB |
| **whisper-small** | 244M | 260 MB | ~1.2s | ~500 MB |
| **distil-whisper-small.en** | 166M | 180 MB | ~0.6s | ~350 MB |

Default to whisper-base. For push-to-talk commands, even whisper-tiny is fine.

## 4. Multi-Model Memory Budgets

| Platform | Available | Chat | Vision | Whisper | Headroom |
|---|---|---|---|---|---|
| Mac 16 GB | ~10 GB | 2.5 GB | 1.2 GB | 0.5 GB | 5.8 GB |
| Dell XPS 16 | ~16 GB sys | 2.5 GB (CPU) | 0.5 GB (NPU) | 0.25 GB (NPU) | 12.75 GB |
| AMD laptop | 8 GB VRAM | 2.5 GB | 1.2 GB | 0.5 GB (CPU) | 3.8 GB VRAM |
| AMD desktop | 16 GB VRAM | 2.5 GB | 1.2 GB | 0.5 GB | 11.8 GB VRAM |

**Rules:** Chat always loaded. Vision: load on demand, keep warm 60s. Whisper: load on first push-to-talk, keep warm 120s. Never run vision + whisper simultaneously (asyncio.Lock).

## 5. NPU Utilization

**Intel NPU: worth it.** Florence-2 (~200ms/image INT8), Whisper-base (~300ms/5s audio).
**AMD XDNA 1 (16 TOPS): skip.** RTX 4070 is right there.
**Bad NPU workload:** Chat LLM (autoregressive = memory-bandwidth-bound).

## 6. MLX Backend

- Lazy loading: MLX memory-maps weights, ~100ms "load" vs 13s Ollama cold start
- Unified memory: no device transfers, 2.5 GB model = 2.5 GB shared pool
- Multi-model: MLX holds multiple models memory-mapped simultaneously
- Warm-up: single-token generation at startup (~0.5s shader compilation)
- Generation speed: M1 ~40 tok/s, M2 ~55 tok/s, M3/M4 ~70+ tok/s for 4B Q4

## 7. Quantization

| Model Type | Recommendation |
|---|---|
| Chat (gemma3:4b) | INT4 (Q4_K_M) everywhere |
| Vision (Moondream2) | INT4 constrained, INT8 desktop |
| Vision (Florence-2) | INT8 for NPU, FP16 on GPU |
| Whisper | INT8 always (degrades badly below) |

## 8. Ollama vs Native — Migration Path

1. Phase 1 (now): HttpBackend only
2. Phase 2: Add MlxBackend, default on macOS
3. Phase 3: Add LlamaCppBackend for Windows
4. Phase 4: OnnxBackend for Intel NPU (vision/whisper only, not chat)

## 9. Model Warm-up

| Model | Load Trigger | Keep Warm | Unload Trigger |
|---|---|---|---|
| Chat | App start | Always | App exit |
| Vision | First screen poll | 60s after last use | Idle/memory pressure |
| Whisper | First push-to-talk | 120s after last use | Idle/memory pressure |

## 10. Latency Budgets

| Feature | Target | Acceptable | Unacceptable |
|---|---|---|---|
| Chat (quip) | <500ms | <1s | >2s |
| Vision (screen desc) | <2s | <3s | >5s |
| OCR | <1s | <2s | >3s |
| STT (push-to-talk) | <1s | <2s | >3s |

## 11. Offline Fallback — Graceful Degradation

1. **Full:** All models loaded
2. **Chat-only:** Vision/Whisper failed
3. **Degraded:** Primary model unavailable, use fallback
4. **Canned:** No model, random from `data/canned_quips.json` (~100 quips)
5. **Silent:** Canned exhausted, idle animation only

## 12. Implementation Priority

| Priority | Feature | Model | Effort |
|---|---|---|---|
| P0 | MLX backend | gemma3:4b mlx-community 4-bit | Medium |
| P0 | Graceful degradation + canned quips | N/A | Small |
| P1 | Vision sense | Moondream2 Q4 | Large |
| P1 | Fallback model config | llama3.2:3b, smollm2:1.7b | Small |
| P2 | STT (push-to-talk) | whisper-base INT8 | Medium |
| P2 | OCR via Florence-2 | Florence-2-base INT8 ONNX | Medium |
| P3 | Intel NPU acceleration | Florence-2 + Whisper via OpenVINO | Medium |
| P3 | LlamaCppBackend | gemma3:4b GGUF Q4_K_M | Medium |
| P4 | TTS | Orpheus-TTS (150M) or Kokoro-TTS (82M) | Medium |
