"""Smoke tests for `_handle_voice_command` and the /voice subcommand helpers.

Locks in current behavior BEFORE the dispatch-refactor that splits the
body into per-subcommand helpers (shared by the upcoming VoiceModal and
the slash command). After the refactor, these same tests must still
pass — that's the parity guarantee.

Scope:
  - One test per subcommand (list, info, off, switch, train, finetune,
    finetune-setup, regenerate, ascii, import) plus the bare-usage fallback.
  - Async branches (train/finetune/regenerate/ascii/finetune-setup) patch
    `threading.Thread` so worker bodies never run; we just verify the
    dispatcher kicked off a thread and returned the expected ack.
  - Pure-dataclass / pure-function style (no Textual harness).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tokenpal.app import _handle_voice_command
from tokenpal.tools.voice_profile import VoiceProfile, save_profile

# --- Fixtures ---------------------------------------------------------------


@pytest.fixture()
def voices_dir(tmp_path: Path) -> Path:
    d = tmp_path / "voices"
    d.mkdir()
    return d


@pytest.fixture()
def saved_profile(voices_dir: Path) -> VoiceProfile:
    profile = VoiceProfile(
        character="Finn",
        source="adventuretime.fandom.com",
        created="2026-01-01T00:00:00",
        lines=["line one", "line two", "line three"],
        persona="ADHD hero.",
    )
    save_profile(profile, voices_dir)
    return profile


@pytest.fixture()
def finetuned_profile(voices_dir: Path) -> VoiceProfile:
    profile = VoiceProfile(
        character="Jake",
        source="adventuretime.fandom.com",
        created="2026-01-01T00:00:00",
        lines=["hey buddy", "groovy"],
        persona="chill dog.",
        finetuned_model="tokenpal-jake",
    )
    save_profile(profile, voices_dir)
    return profile


@pytest.fixture()
def personality() -> MagicMock:
    p = MagicMock()
    p.voice_name = ""
    p.is_finetuned = False
    return p


@pytest.fixture()
def overlay() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def llm() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def config() -> MagicMock:
    cfg = MagicMock()
    cfg.llm.model_name = "gemma4"
    cfg.finetune.remote.host = "gpu-box"
    cfg.finetune.base_model = "google/gemma-2-2b-it"
    return cfg


def _call(
    args: str,
    personality: MagicMock,
    voices_dir: Path,
    overlay: MagicMock,
    **kw: Any,
) -> Any:
    return _handle_voice_command(
        args, personality, voices_dir, overlay,
        brain=kw.get("brain"),
        llm=kw.get("llm"),
        config=kw.get("config"),
        on_voice_loaded=kw.get("on_voice_loaded"),
    )


# --- Sync subcommands -------------------------------------------------------


def test_list_empty(personality, voices_dir, overlay) -> None:
    r = _call("list", personality, voices_dir, overlay)
    assert "No voices saved yet" in r.message


def test_list_with_profiles(
    personality, voices_dir, overlay, saved_profile
) -> None:
    r = _call("list", personality, voices_dir, overlay)
    assert "Finn" in r.message
    assert "3 lines" in r.message


def test_info_default_voice(personality, voices_dir, overlay) -> None:
    personality.voice_name = ""
    r = _call("info", personality, voices_dir, overlay)
    assert "Using default TokenPal voice" in r.message


def test_info_custom_voice(personality, voices_dir, overlay) -> None:
    personality.voice_name = "Finn"
    personality.is_finetuned = False
    r = _call("info", personality, voices_dir, overlay)
    assert r.message == "Voice: Finn"


def test_info_custom_voice_finetuned(
    personality, voices_dir, overlay
) -> None:
    personality.voice_name = "Jake"
    personality.is_finetuned = True
    r = _call("info", personality, voices_dir, overlay)
    assert "fine-tuned" in r.message


def test_off_from_plain_voice_leaves_server_model_alone(
    personality, voices_dir, overlay, llm, config
) -> None:
    personality.voice_name = "Finn"
    personality.is_finetuned = False
    with patch("tokenpal.tools.train_voice.activate_voice") as activate:
        r = _call(
            "off", personality, voices_dir, overlay, llm=llm, config=config,
        )
    personality.set_voice.assert_called_once_with(None)
    llm.set_model.assert_not_called()
    activate.assert_called_once_with("")
    assert "default TokenPal" in r.message


def test_off_from_finetuned_voice_reverts_to_base_model(
    personality, voices_dir, overlay, llm, config
) -> None:
    personality.voice_name = "Jake"
    personality.is_finetuned = True
    with patch("tokenpal.tools.train_voice.activate_voice"):
        _call(
            "off", personality, voices_dir, overlay, llm=llm, config=config,
        )
    llm.set_model.assert_called_once_with("gemma4")


def test_switch_missing_arg(personality, voices_dir, overlay) -> None:
    r = _call("switch", personality, voices_dir, overlay)
    assert "Usage: /voice switch" in r.message


def test_switch_unknown_voice(personality, voices_dir, overlay) -> None:
    r = _call("switch ghost", personality, voices_dir, overlay)
    assert "not found" in r.message


def test_switch_loads_profile_without_clobbering_server_model(
    personality, voices_dir, overlay, llm, config, saved_profile,
) -> None:
    personality.is_finetuned = False
    loaded: dict[str, bool] = {"called": False}
    def _on_loaded() -> None:
        loaded["called"] = True

    with patch("tokenpal.tools.train_voice.activate_voice") as activate:
        r = _call(
            "switch finn", personality, voices_dir, overlay,
            llm=llm, config=config, on_voice_loaded=_on_loaded,
        )
    personality.set_voice.assert_called_once()
    called_profile = personality.set_voice.call_args.args[0]
    assert called_profile.character == "Finn"
    assert loaded["called"] is True
    # Non-finetuned previous AND non-finetuned new: server's auto-adopted
    # model must not be overwritten.
    llm.set_model.assert_not_called()
    activate.assert_called_once_with("finn")
    assert "Switched to Finn" in r.message


def test_switch_from_finetuned_to_plain_reverts_to_base_model(
    personality, voices_dir, overlay, llm, config, saved_profile,
) -> None:
    personality.is_finetuned = True
    with patch("tokenpal.tools.train_voice.activate_voice"):
        _call(
            "switch finn", personality, voices_dir, overlay,
            llm=llm, config=config,
        )
    llm.set_model.assert_called_once_with("gemma4")


def test_switch_finetuned_profile_swaps_model(
    personality, voices_dir, overlay, llm, config, finetuned_profile,
) -> None:
    personality.is_finetuned = False
    with patch("tokenpal.tools.train_voice.activate_voice"):
        _call(
            "switch jake", personality, voices_dir, overlay,
            llm=llm, config=config,
        )
    llm.set_model.assert_called_once_with("tokenpal-jake")


# --- Async subcommands (patch threading.Thread) -----------------------------


def _patch_thread() -> Any:
    """Patch app.threading.Thread so worker functions never run."""
    return patch("tokenpal.app.threading.Thread", autospec=True)


def test_train_requires_two_args(personality, voices_dir, overlay) -> None:
    r = _call("train", personality, voices_dir, overlay)
    assert "Usage: /voice train" in r.message


def test_train_kicks_off_thread(
    personality, voices_dir, overlay,
) -> None:
    with _patch_thread() as Thread:
        r = _call(
            'train https://finn.fandom.com "Finn the Human"',
            personality, voices_dir, overlay,
        )
    Thread.assert_called_once()
    assert Thread.call_args.kwargs.get("name") == "voice-train"
    Thread.return_value.start.assert_called_once()
    assert r.message == ""


def test_finetune_missing_arg(personality, voices_dir, overlay) -> None:
    r = _call("finetune", personality, voices_dir, overlay)
    assert "Usage: /voice finetune" in r.message


def test_finetune_unknown_voice(personality, voices_dir, overlay) -> None:
    r = _call("finetune ghost", personality, voices_dir, overlay)
    assert "not found" in r.message


def test_finetune_no_remote_host(
    personality, voices_dir, overlay, saved_profile,
) -> None:
    cfg = MagicMock()
    cfg.finetune.remote.host = ""
    r = _call(
        "finetune finn", personality, voices_dir, overlay, config=cfg,
    )
    assert "No remote GPU configured" in r.message


def test_finetune_kicks_off_thread(
    personality, voices_dir, overlay, config, saved_profile,
) -> None:
    with _patch_thread() as Thread:
        r = _call(
            "finetune finn", personality, voices_dir, overlay, config=config,
        )
    Thread.assert_called_once()
    assert Thread.call_args.kwargs.get("name") == "voice-finetune"
    assert r.message == ""


def test_finetune_setup_no_remote_host(
    personality, voices_dir, overlay,
) -> None:
    cfg = MagicMock()
    cfg.finetune.remote.host = ""
    r = _call(
        "finetune-setup", personality, voices_dir, overlay, config=cfg,
    )
    assert "No remote GPU configured" in r.message


def test_finetune_setup_kicks_off_thread(
    personality, voices_dir, overlay, config,
) -> None:
    with _patch_thread() as Thread:
        r = _call(
            "finetune-setup", personality, voices_dir, overlay, config=config,
        )
    Thread.assert_called_once()
    assert Thread.call_args.kwargs.get("name") == "finetune-setup"
    assert r.message == ""


def test_regenerate_no_active_voice(personality, voices_dir, overlay) -> None:
    personality.voice_name = ""
    r = _call("regenerate", personality, voices_dir, overlay)
    assert "No active voice" in r.message


def test_regenerate_all_empty_dir(personality, voices_dir, overlay) -> None:
    r = _call("regenerate --all", personality, voices_dir, overlay)
    assert "No voice profiles found" in r.message


def test_regenerate_kicks_off_thread(
    personality, voices_dir, overlay, saved_profile,
) -> None:
    personality.voice_name = "Finn"
    with _patch_thread() as Thread:
        r = _call(
            "regenerate", personality, voices_dir, overlay,
        )
    Thread.assert_called_once()
    assert Thread.call_args.kwargs.get("name") == "voice-regen"
    assert r.message == ""


def test_ascii_no_active_voice(personality, voices_dir, overlay) -> None:
    personality.voice_name = ""
    r = _call("ascii", personality, voices_dir, overlay)
    assert "No active voice" in r.message


def test_ascii_all_empty_dir(personality, voices_dir, overlay) -> None:
    r = _call("ascii --all", personality, voices_dir, overlay)
    assert "No voice profiles found" in r.message


def test_ascii_kicks_off_thread(
    personality, voices_dir, overlay, saved_profile,
) -> None:
    personality.voice_name = "Finn"
    with _patch_thread() as Thread:
        r = _call("ascii", personality, voices_dir, overlay)
    Thread.assert_called_once()
    assert Thread.call_args.kwargs.get("name") == "voice-ascii-regen"
    assert r.message == ""


# --- Import (sync) ----------------------------------------------------------


def test_import_missing_arg(personality, voices_dir, overlay) -> None:
    r = _call("import", personality, voices_dir, overlay)
    assert "Usage: /voice import" in r.message


def test_import_missing_file(
    personality, voices_dir, overlay, tmp_path,
) -> None:
    r = _call(
        f"import {tmp_path}/nope.gguf", personality, voices_dir, overlay,
    )
    assert "File not found" in r.message


def test_import_wrong_extension(
    personality, voices_dir, overlay, tmp_path,
) -> None:
    bogus = tmp_path / "model.bin"
    bogus.write_bytes(b"x")
    r = _call(
        f"import {bogus}", personality, voices_dir, overlay,
    )
    assert "Expected a .gguf" in r.message


def test_import_no_matching_profile(
    personality, voices_dir, overlay, tmp_path,
) -> None:
    gguf = tmp_path / "mystery.gguf"
    gguf.write_bytes(b"x")
    r = _call(
        f"import {gguf}", personality, voices_dir, overlay,
    )
    assert "No voice profile for 'mystery'" in r.message


def test_import_happy_path(
    personality, voices_dir, overlay, llm, tmp_path, saved_profile,
) -> None:
    gguf = tmp_path / "finn.gguf"
    gguf.write_bytes(b"x")
    with patch(
        "tokenpal.tools.finetune_voice.register_ollama", return_value=True,
    ) as reg, patch(
        "tokenpal.tools.dataset_prep.build_system_prompt",
        return_value="sys",
    ):
        r = _call(
            f"import {gguf}", personality, voices_dir, overlay, llm=llm,
        )
    reg.assert_called_once()
    personality.set_voice.assert_called_once()
    llm.set_model.assert_called_once_with("tokenpal-finn")
    assert "Imported and activated" in r.message

    # Profile on disk was updated with finetuned_model.
    updated = json.loads((voices_dir / "finn.json").read_text())
    assert updated["finetuned_model"] == "tokenpal-finn"


# --- Bare usage fallback ----------------------------------------------------


def test_unknown_subcommand_returns_usage(
    personality, voices_dir, overlay,
) -> None:
    r = _call("", personality, voices_dir, overlay)
    assert "Usage: /voice" in r.message
    r = _call("whatever", personality, voices_dir, overlay)
    assert "Usage: /voice" in r.message
