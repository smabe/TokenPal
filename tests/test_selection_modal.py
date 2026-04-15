"""Tests for the shared SelectionModal widget."""

from __future__ import annotations

import pytest

from tokenpal.ui.selection_modal import SelectionGroup, SelectionItem, SelectionModal


def _groups() -> list[SelectionGroup]:
    return [
        SelectionGroup(
            title="Default",
            items=(
                SelectionItem("timer", "timer", initial=True, locked=True),
                SelectionItem("system_info", "system_info", initial=True, locked=True),
            ),
        ),
        SelectionGroup(
            title="Local",
            items=(
                SelectionItem("read_file", "read_file", initial=False),
                SelectionItem("git_log", "git_log", initial=True),
            ),
        ),
        SelectionGroup(
            title="Empty",
            items=(),
        ),
    ]


@pytest.mark.asyncio
async def test_modal_collect_after_mount() -> None:
    """After mount, _collect reflects initial selections plus locked rows."""
    from textual.app import App, ComposeResult

    class _Harness(App[None]):
        def compose(self) -> ComposeResult:
            return iter(())

        def on_mount(self) -> None:
            self.push_screen(SelectionModal("Tools", _groups()))

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, SelectionModal)
        result = modal._collect()

    assert set(result["Default"]) == {"timer", "system_info"}
    assert set(result["Local"]) == {"git_log"}
    assert result["Empty"] == []


@pytest.mark.asyncio
async def test_modal_dismiss_delivers_result() -> None:
    """dismiss(payload) resolves the push_screen callback."""
    from textual.app import App, ComposeResult

    received: list[dict[str, list[str]] | None] = []

    class _Harness(App[None]):
        def compose(self) -> ComposeResult:
            return iter(())

        def on_mount(self) -> None:
            self.push_screen(SelectionModal("Tools", _groups()), received.append)

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, SelectionModal)
        modal.dismiss(modal._collect())
        await pilot.pause()

    assert received and received[0] is not None
    assert "Default" in received[0]
    assert set(received[0]["Local"]) == {"git_log"}


@pytest.mark.asyncio
async def test_modal_dismiss_none_for_cancel() -> None:
    from textual.app import App, ComposeResult

    received: list[dict[str, list[str]] | None] = []

    class _Harness(App[None]):
        def compose(self) -> ComposeResult:
            return iter(())

        def on_mount(self) -> None:
            self.push_screen(SelectionModal("Tools", _groups()), received.append)

    async with _Harness().run_test() as pilot:
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, SelectionModal)
        modal.dismiss(None)
        await pilot.pause()

    assert received == [None]


def test_list_id_is_css_safe() -> None:
    group = SelectionGroup(title="Agent / Research!", items=())
    sid = SelectionModal._list_id(group)
    assert sid.startswith("group-")
    assert all(c.isalnum() or c == "-" for c in sid)
