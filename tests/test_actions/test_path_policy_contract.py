"""The contract every filesystem-facing tool must satisfy.

Path policy is declared per tool (``path_params`` / ``path_roots`` /
``path_screen``) and enforced in ``ToolInvoker``, so a tool that forgets the
declaration gets no containment at all and receives whatever the model sent.
This file fails closed on that:

- the selector is the tool's JSON **schema**, not its declaration. Enumerating
  ``path_params`` could only ever check the tools that already remembered;
- an empty selector is a failure of its own test, because an empty
  ``parametrize`` list collects as a *skip*;
- an unimportable action module is a finding, not a quietly smaller registry —
  ``discover_actions`` swallows ImportError;
- a constructor that raises on a host the tool claims to support is a failure,
  not a skip.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import tokenpal.actions as actions_pkg
from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.actions.registry import _ACTION_REGISTRY, discover_actions
from tokenpal.util.paths import _OUTSIDE, ResolvedPath
from tokenpal.util.platform import current_platform

# Collection-time: parametrize needs the registry populated before the module
# body finishes.
discover_actions()

# Substring, not equality: `file_path`, `target_dir` and `folder` are all the
# same argument shape as `path`.
_PATH_WORDS = ("path", "file", "dir", "folder")

_KNOWN_PATH_TOOLS: dict[str, str] = {
    "read_file": "tokenpal.actions.read_file",
    "grep_codebase": "tokenpal.actions.grep_codebase",
    "open_path": "tokenpal.actions.open_path",
}


def _schema(cls: type[AbstractAction]) -> dict[str, Any]:
    schema = getattr(cls, "parameters", None) or {}
    return schema if isinstance(schema, dict) else {}


def _path_shaped(cls: type[AbstractAction]) -> list[str]:
    """Declared argument names that carry a filesystem path, by name alone."""
    props = _schema(cls).get("properties", {})
    if not isinstance(props, dict):
        return []
    return sorted(
        name for name in props if any(word in name.lower() for word in _PATH_WORDS)
    )


def _required(cls: type[AbstractAction]) -> set[str]:
    required = _schema(cls).get("required", [])
    return set(required) if isinstance(required, list) else set()


def _dummy_args(cls: type[AbstractAction]) -> dict[str, Any]:
    """Plausible values for the tool's declared required arguments."""
    props = _schema(cls).get("properties", {})
    by_type: dict[str, Any] = {
        "string": "x", "integer": 1, "number": 1.0, "boolean": True,
        "array": [], "object": {},
    }
    out: dict[str, Any] = {}
    for name in _required(cls):
        spec = props.get(name, {}) if isinstance(props, dict) else {}
        enum = spec.get("enum") if isinstance(spec, dict) else None
        out[name] = enum[0] if enum else by_type.get(spec.get("type", "string"), "x")
    return out


def _instantiate(cls: type[AbstractAction]) -> AbstractAction:
    """Construct, failing rather than skipping on a host the tool claims."""
    try:
        return cls({})
    except Exception as exc:
        plat = current_platform()
        if plat in cls.platforms:
            pytest.fail(
                f"{cls.action_name} declares support for {plat} but its "
                f"constructor raised {exc!r}"
            )
        pytest.skip(f"{cls.action_name} does not support {plat}")


_DECLARING: list[type[AbstractAction]] = sorted(
    (cls for cls in _ACTION_REGISTRY.values() if cls.path_params),
    key=lambda cls: cls.action_name,
)


def test_the_selectors_are_not_empty() -> None:
    """An empty ``parametrize`` list collects as a skip, so every parametrized
    case below would report green if the registry came up empty. This is the
    test that turns that into a failure."""
    assert _DECLARING, "no registered action declares path_params"
    shaped = sorted(name for name, cls in _ACTION_REGISTRY.items() if _path_shaped(cls))
    assert shaped, "no registered action declares a path-shaped argument"


def test_the_known_path_tools_are_registered_and_declare_a_policy() -> None:
    """Not parametrized: ``discover_actions`` swallows ImportError, so a tool
    whose module stops importing would empty the cases above instead of
    failing. The import comes first so the traceback names the cause."""
    for module in _KNOWN_PATH_TOOLS.values():
        importlib.import_module(module)
    declared = {cls.action_name for cls in _DECLARING}
    assert set(_KNOWN_PATH_TOOLS) <= declared, (
        f"path tools missing a declared policy: "
        f"{sorted(set(_KNOWN_PATH_TOOLS) - declared)}"
    )


def test_every_action_module_imports() -> None:
    """An unreadable module is a finding, not a smaller registry."""
    broken: list[str] = []
    for _importer, modname, _ispkg in pkgutil.walk_packages(
        actions_pkg.__path__, prefix=actions_pkg.__name__ + "."
    ):
        try:
            importlib.import_module(modname)
        except Exception as exc:
            broken.append(f"{modname}: {exc!r}")
    assert sorted(broken) == [], (
        f"action modules that do not import, so discover_actions drops them "
        f"and every registry-driven contract stops seeing their tools: {broken}"
    )


def test_a_tool_with_a_path_shaped_argument_declares_a_path_policy() -> None:
    """The declaration is not just this file's selector — it is the switch
    ``ToolInvoker`` reads to resolve, screen and contain the argument. A tool
    that forgets it is handed the model's raw string."""
    offenders = sorted(
        f"{name}.{prop}"
        for name, cls in _ACTION_REGISTRY.items()
        for prop in _path_shaped(cls)
        if prop not in cls.path_params
    )
    assert offenders == [], (
        f"these take a filesystem path but do not declare it in path_params, "
        f"so the invoker contains nothing and the tool receives the raw "
        f"argument: {offenders}. Declare path_params/path_roots/path_screen "
        f"and write no containment of your own — see docs/claude/actions.md"
    )


@pytest.mark.parametrize("cls", _DECLARING, ids=lambda cls: cls.action_name)
async def test_a_declaring_tool_refuses_a_value_the_invoker_did_not_contain(
    cls: type[AbstractAction],
) -> None:
    """The invoker skips containment when a declared argument is absent, blank
    or not a ``str``, and hands the raw value to ``execute``. Every declaring
    tool must refuse it rather than trust the harness."""
    action = _instantiate(cls)
    base = _dummy_args(cls)

    for name in cls.path_params:
        shapes: list[tuple[str, dict[str, Any]]] = [
            (f"{name}={value!r}", {**base, name: value})
            for value in ("/etc/passwd", ["/etc/passwd"], 123, True, {"p": "/etc"})
        ]
        if name in _required(cls):
            # Optional declared paths mean "no path" when blank or absent, and
            # the tool's own default is contained; a required one must refuse.
            shapes += [
                (f"{name}=''", {**base, name: ""}),
                (f"{name}='   '", {**base, name: "   "}),
                (f"{name} absent", {k: v for k, v in base.items() if k != name}),
            ]
        for label, arguments in shapes:
            result = await action.execute(**arguments)
            assert result.success is False, (
                f"{cls.action_name} accepted an uncontained {label}: only a "
                f"ResolvedPath has been screened and contained"
            )


@pytest.mark.parametrize("cls", _DECLARING, ids=lambda cls: cls.action_name)
async def test_a_declared_path_outside_every_root_is_refused(
    cls: type[AbstractAction], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routed through ``ToolInvoker``, because containment lives there: a
    direct ``execute()`` call measures nothing. No per-tool knowledge, so a
    tool added later is covered the moment it declares."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "notes.txt"
    secret.write_text("MARKERSECRET\n")

    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(repo))
    cfg = SimpleNamespace(paths=SimpleNamespace(allowed_dirs=[str(repo)]))
    monkeypatch.setattr("tokenpal.util.paths.load_config", lambda: cfg)

    action = _instantiate(cls)
    for name in cls.path_params:
        result = await ToolInvoker().invoke(
            action, {**_dummy_args(cls), name: str(secret)}
        )
        assert result.success is False, (
            f"{cls.action_name} accepted {name} outside every declared root"
        )
        # The INVOKER's refusal, not the tool's own guard. Asserting only
        # `success is False` passes with containment deleted, because each tool
        # then refuses the raw str for its own reasons -- the same verdict for a
        # different reason, which is what this file exists not to accept.
        assert result.output == _OUTSIDE[cls.path_roots], (
            f"{cls.action_name} refused {name}, but not at the containment layer"
        )
        # The name itself can be the secret, so a refusal never echoes it.
        assert str(secret) not in result.output
        assert "notes.txt" not in result.output
        assert "MARKERSECRET" not in result.output


@pytest.mark.parametrize("cls", _DECLARING, ids=lambda c: c.action_name)
async def test_a_declared_path_inside_a_root_arrives_contained(
    cls: type[AbstractAction], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive half: an in-root path reaches ``execute`` as a ResolvedPath.

    Without this, the file is green with ``_contain_paths`` deleted -- every
    refusal case still refuses, on each tool's own type guard. This is the only
    case that fails when containment stops running.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "inside.txt").write_text("hello\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(repo))
    cfg = SimpleNamespace(paths=SimpleNamespace(allowed_dirs=[str(repo)]))
    monkeypatch.setattr("tokenpal.util.paths.load_config", lambda: cfg)

    action = _instantiate(cls)
    for name in cls.path_params:
        seen: dict[str, object] = {}

        async def _capture(**kwargs: object) -> ActionResult:
            seen.update(kwargs)
            return ActionResult(output="ok")

        monkeypatch.setattr(action, "execute", _capture)
        await ToolInvoker().invoke(
            action, {**_dummy_args(cls), name: "inside.txt"}
        )
        assert isinstance(seen.get(name), ResolvedPath), (
            f"{cls.action_name} received {name} as {type(seen.get(name)).__name__}, "
            "not a ResolvedPath -- containment did not run"
        )
