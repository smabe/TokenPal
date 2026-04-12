"""Training worker — runs the fine-tuning pipeline in a background thread."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from tokenpal.server.job_store import AbstractJobStore
from tokenpal.server.models import TrainingJob, TrainingStatus
from tokenpal.tools.voice_profile import slugify

log = logging.getLogger(__name__)

_training_lock = asyncio.Lock()
_active_task: asyncio.Task[None] | None = None


def _run_pipeline(job: TrainingJob, data_dir: Path, output_dir: Path) -> None:
    """Synchronous training pipeline. Runs via asyncio.to_thread().

    Updates job.progress in-place (list appends are thread-safe).
    Raises on failure.
    """
    from tokenpal.tools.dataset_prep import build_system_prompt, prepare_dataset
    from tokenpal.tools.finetune_voice import (
        LoRAConfig,
        _check_gpu,
        _count_lines,
        auto_tune,
        merge_adapter,
        register_ollama,
        setup_model,
        train,
    )
    from tokenpal.tools.train_voice import train_from_wiki

    def progress(msg: str) -> None:
        job.progress.append(msg)
        log.info("[%s] %s", job.job_id, msg)

    # Step 0: Wiki fetch + voice profile generation
    job.status = TrainingStatus.FETCHING
    progress(f"Fetching {job.wiki} transcripts for {job.character}...")
    profile = train_from_wiki(
        wiki=job.wiki, character=job.character, progress_callback=progress,
    )
    if profile is None:
        raise ValueError(
            f"Not enough lines for '{job.character}' on {job.wiki}.fandom.com. "
            f"Check character name spelling (case-sensitive)."
        )

    # Step 1: Dataset prep
    job.status = TrainingStatus.PREPARING
    progress(f"Preparing dataset ({profile.line_count} lines)...")
    train_path, val_path = prepare_dataset(profile, data_dir)
    num_train = _count_lines(train_path)
    progress(f"Dataset ready: {num_train} training samples")

    # Step 2: Train — first unload Ollama's model to free VRAM
    job.status = TrainingStatus.TRAINING
    if not _check_gpu():
        raise RuntimeError("No CUDA GPU detected. Training requires a CUDA GPU.")
    progress("Unloading Ollama models to free VRAM...")
    try:
        import json as _json
        import urllib.request

        # Find loaded models and unload each one
        resp = urllib.request.urlopen("http://localhost:11434/api/ps", timeout=10)
        running = _json.loads(resp.read()).get("models", [])
        for m in running:
            name = m.get("name", "")
            if name:
                req = urllib.request.Request(
                    "http://localhost:11434/api/generate",
                    data=_json.dumps({"model": name, "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
                progress(f"Unloaded {name}")
    except Exception:
        pass  # Best-effort — Ollama may not have models loaded
    config = LoRAConfig(base_model=job.base_model)
    config = auto_tune(config, num_train)
    progress(f"Training (rank={config.lora_rank}, epochs={config.epochs})...")
    model, tokenizer = setup_model(config)
    adapter_dir = train(model, tokenizer, train_path, val_path, config, output_dir)
    progress("Training complete")

    # Free GPU memory before merge
    del model, tokenizer
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass

    # Step 3: Merge
    job.status = TrainingStatus.MERGING
    progress("Merging adapter into base model...")
    merged_dir = output_dir / "merged"
    merge_adapter(adapter_dir, config.base_model, merged_dir)

    # Step 4: Register with Ollama
    job.status = TrainingStatus.REGISTERING
    slug = slugify(profile.character)
    model_name = f"tokenpal-{slug}"
    system_prompt = build_system_prompt(profile)
    progress(f"Registering {model_name} with Ollama...")
    if not register_ollama(merged_dir, model_name, system_prompt):
        raise RuntimeError(f"Ollama registration failed for {model_name}.")
    job.model_name = model_name
    progress(f"Done! Model '{model_name}' registered and ready")


async def submit_training_job(
    wiki: str,
    character: str,
    base_model: str,
    store: AbstractJobStore,
) -> TrainingJob:
    """Submit a new training job. Returns immediately with job metadata.

    Raises ValueError if a training job is already running.
    """
    global _active_task  # noqa: PLW0603

    # Atomically check for active jobs via the store, not the lock.
    # This avoids the TOCTOU race where two concurrent calls both see
    # locked()==False before either acquires the lock.
    active = store.get_active()
    if active is not None:
        raise ValueError(f"Training already in progress: {active.job_id}")

    job_id = uuid.uuid4().hex[:12]
    job = TrainingJob(
        job_id=job_id,
        status=TrainingStatus.QUEUED,
        wiki=wiki,
        character=character,
        base_model=base_model,
    )
    store.put(job)
    _active_task = asyncio.create_task(_run_training(job, store))
    return job


async def _run_training(job: TrainingJob, store: AbstractJobStore) -> None:
    """Acquire GPU lock, run pipeline in thread, update job on completion."""
    async with _training_lock:
        slug = job.character.lower().replace(" ", "")
        base_dir = Path.home() / ".tokenpal-server" / "finetune" / slug

        try:
            await asyncio.to_thread(
                _run_pipeline, job, base_dir / "data", base_dir,
            )
            job.status = TrainingStatus.COMPLETE
        except Exception as exc:
            job.status = TrainingStatus.FAILED
            job.error = str(exc)
            err = str(exc).lower()
            if "cuda out of memory" in err or "oom" in err:
                job.error_hint = "GPU out of memory. Try a smaller base model."
            elif "401" in err or "gated" in err or "access" in err:
                job.error_hint = "HuggingFace auth error. Set HF_TOKEN on the server."
            elif "ollama" in err:
                job.error_hint = "Ollama may be down. Check: ollama list"
            elif "not enough lines" in err:
                job.error_hint = "Check character name spelling (case-sensitive)."
            log.exception("Training job %s failed", job.job_id)
        finally:
            store.put(job)
