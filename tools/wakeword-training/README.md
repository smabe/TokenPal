# Custom wakeword training — `hey_tokenpal.onnx`

Status: **not trained yet.** TokenPal currently uses the stock `hey_jarvis_v0.1.onnx` from openWakeWord's v0.5.1 release as a placeholder. Training a custom `hey_tokenpal` model is a one-shot dev-only task; once shipped, every install benefits.

## Why train a custom one
- Wake on the buddy's actual name instead of "hey jarvis".
- Tune false-fire rate to the user's voice (custom verifier model on top of synthetic-data base) — openWakeWord supports per-user verifier models for first-run wizard flows.
- Drop dependence on a third-party wakeword release for the runtime path.

## How: openWakeWord's Colab notebook
The upstream maintainer ships a Google Colab notebook that trains a wakeword model from synthetic data (Piper TTS generates the positive examples; the negative set comes from a sampled corpus). ~1 hour on a free T4 GPU.

1. Open <https://github.com/dscripka/openWakeWord> → README → "Training Custom Models" → Colab badge.
2. Set `target_phrase = "hey tokenpal"`. Default training params (`n_samples=2000`, `steps=10000`) produce a usable model with 5-15% FRR and 1-3 false-fires/hour at threshold 0.7. For tighter results scale to `n_samples=30000`, `steps=50000` (~3-4h on T4).
3. Run all cells. Download the resulting `hey_tokenpal.onnx`.
4. Drop the file at `~/.tokenpal/audio/wakeword/hey_tokenpal.onnx`.
5. Set `[audio] wakeword_model_name = "hey_tokenpal"` in `~/.tokenpal/config.toml`. (One-line wire-up needed in `tokenpal/audio/input.py` — the model name is currently hardcoded; see `OpenWakeWordBackend(data_dir, model_name=..., ...)`.)
6. Restart the buddy. `tokenpal --validate` should now show `wakeword + VAD models present`. Say "hey tokenpal" — log line `voice: wake (hey_tokenpal @ 0.XX)` confirms it fires.

## Quality expectations (synthetic-data baseline)
- ~5-15% false-reject rate at threshold 0.7 (user has to repeat ~1 in 10 wakes)
- ~1-3 false-fires/hour from environmental audio (TV, conversation in the next room)
- Scales improve linearly with `n_samples` until ~50k where you hit diminishing returns

If false-rejects exceed 20% in dogfood, the next move is a custom verifier model trained on ~10 minutes of the user's actual voice — openWakeWord supports this via `custom_verifier_models=` on `Model()`. Wire that up after the base model is in.

## What does NOT live here
- The trained `.onnx` itself. Don't commit it to git — fetch via the install path or document the URL once we host one. ~200KB so committing is feasible if we host on GitHub releases the way openWakeWord does.
- Training data. Synthetic, regenerated per run.
- Inference code. That's `tokenpal/audio/backends/wake_openwakeword.py`.
