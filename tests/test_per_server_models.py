"""Tests for per-server model + max_tokens persistence (GH #24)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpal.config.toml_writer import (
    canon_server_url,
    remember_server_max_tokens,
    remember_server_model,
)


@pytest.fixture()
def fake_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    with patch("tokenpal.config.toml_writer.find_config_toml", return_value=path):
        yield path


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:11434", "http://localhost:11434/v1"),
        ("http://localhost:11434/", "http://localhost:11434/v1"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1"),
        ("  http://host:8585  ", "http://host:8585/v1"),
    ],
)
def test_canon_server_url_normalization(raw: str, expected: str) -> None:
    assert canon_server_url(raw) == expected


def test_canon_server_url_idempotent() -> None:
    once = canon_server_url("http://h:1/")
    assert canon_server_url(once) == once


def test_remember_server_model_creates_file(fake_config: Path) -> None:
    assert not fake_config.exists()
    remember_server_model("http://localhost:11434", "gemma4")
    data = _toml(fake_config)
    assert data["llm"]["per_server_models"] == {
        "http://localhost:11434/v1": "gemma4",
    }


def test_remember_server_model_upserts(fake_config: Path) -> None:
    remember_server_model("http://a:1/v1", "modelA")
    remember_server_model("http://b:2/v1", "modelB")
    remember_server_model("http://a:1/v1", "modelA-v2")  # update existing
    data = _toml(fake_config)
    assert data["llm"]["per_server_models"] == {
        "http://a:1/v1": "modelA-v2",
        "http://b:2/v1": "modelB",
    }


def test_remember_server_model_preserves_other_llm_keys(fake_config: Path) -> None:
    fake_config.write_text(
        '[llm]\nmodel_name = "gemma4"\napi_url = "http://x:1/v1"\n'
    )
    remember_server_model("http://x:1/v1", "newmodel")
    data = _toml(fake_config)
    assert data["llm"]["model_name"] == "gemma4"
    assert data["llm"]["api_url"] == "http://x:1/v1"
    assert data["llm"]["per_server_models"]["http://x:1/v1"] == "newmodel"


def test_remember_server_max_tokens_roundtrip(fake_config: Path) -> None:
    remember_server_max_tokens("http://h:1", 256)
    data = _toml(fake_config)
    assert data["llm"]["per_server_max_tokens"]["http://h:1/v1"] == 256


def test_url_variations_collapse_to_one_key(fake_config: Path) -> None:
    remember_server_model("http://h:1", "a")
    remember_server_model("http://h:1/", "b")
    remember_server_model("http://h:1/v1/", "c")
    data = _toml(fake_config)
    assert data["llm"]["per_server_models"] == {"http://h:1/v1": "c"}
