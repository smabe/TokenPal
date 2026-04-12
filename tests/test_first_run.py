"""Tests for the first-run welcome wizard."""

from __future__ import annotations

from pathlib import Path

from tokenpal.first_run import mark_first_run_done, needs_first_run


def test_needs_first_run_fresh(tmp_path: Path):
    """Fresh data dir → first run needed."""
    assert needs_first_run(tmp_path) is True


def test_needs_first_run_after_marker(tmp_path: Path):
    """After marking done → first run not needed."""
    mark_first_run_done(tmp_path)
    assert needs_first_run(tmp_path) is False


def test_marker_creates_dir(tmp_path: Path):
    """Marker creation works even if data dir doesn't exist yet."""
    nested = tmp_path / "deep" / "nested"
    mark_first_run_done(nested)
    assert (nested / ".first_run_done").exists()


def test_marker_file_content(tmp_path: Path):
    """Marker file is a simple text file."""
    mark_first_run_done(tmp_path)
    content = (tmp_path / ".first_run_done").read_text()
    assert content.strip() == "done"
