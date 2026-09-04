"""The contract every desktop-content tool must satisfy.

Registry-driven: it enumerates ``reads_desktop_content`` actions rather than
naming them, so a tool added by #52-#55 is measured the moment it registers.
The parametrized cases run for ``read_selection``, the first marked tool; one
unparametrized case names it so a lost registration cannot pass as a green
"skipped" run. The others pin the strings and the registry/catalog parity the
enforcement depends on.

What is NOT here, by design: a marked tool's sensitive-source refusal and an
``assert_no_leak`` sweep over its real read path. A generic test cannot supply
valid arguments or fake an OS read for an unknown tool, so those two are each
tool's own tests.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys

import pytest

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.catalog import find_entry
from tokenpal.actions.registry import _ACTION_REGISTRY, discover_actions
from tokenpal.brain.idle_rules import M1_RULES
from tokenpal.brain.idle_tools_m3 import M3_CATALOG
from tokenpal.config.consent import Category
from tokenpal.config.schema import DEFAULT_TOOLS
from tokenpal.desktop.content import DesktopContent

# Collection-time: parametrize needs the registry populated before the module
# body finishes.
discover_actions()

_MARKED: list[type[AbstractAction]] = [
    cls for cls in _ACTION_REGISTRY.values() if cls.reads_desktop_content
]

# Every tool name any deterministic idle rule can fire, chain calls included.
_IDLE_RULE_TOOLS: frozenset[str] = frozenset(
    {rule.tool_name for rule in M1_RULES}
    | {name for rule in M1_RULES for name in rule.extra_tool_names}
)

# Modules that put bytes on the wire. A tool reaching any of these must be
# consent-gated in the catalog, or the agent's post-content tool drop lets it
# through.
_NETWORK_MODULES = (
    "aiohttp",
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "http.client",
    "socket",
    "ssl",
    "websockets",
    "tokenpal.util.http_json",
)


def _imported_modules(module_name: str) -> set[str]:
    """Module names imported by *module_name*, read from its source.

    Source rather than runtime globals because ``from x.y import f`` binds a
    function, not a module, and that is how this repo imports its helpers.
    """
    mod = sys.modules.get(module_name)
    if mod is None:
        try:
            mod = importlib.import_module(module_name)
        except Exception:
            return set()
    try:
        tree = ast.parse(inspect.getsource(mod))
    except (OSError, TypeError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # `from . import x` / `from .pkg import x` — resolve against the
                # containing package or the walker silently sees nothing.
                pkg = (getattr(mod, "__package__", "") or "").split(".")
                anchor = pkg[: len(pkg) - node.level + 1]
                base = ".".join([*anchor, base]) if base else ".".join(anchor)
            if not base:
                continue
            names.add(base)
            names |= {f"{base}.{alias.name}" for alias in node.names}
    return names


def _reaches_network(cls: type[AbstractAction]) -> bool:
    """True when the tool's own code imports a network client.

    Recursion stops at the ``tokenpal.actions`` package boundary: that covers
    a tool's private helpers (``network/_http.py``, the research package)
    without following ``brain.memory`` into every module in the process,
    which would flag purely local tools.
    """
    seen: set[str] = set()
    stack = [cls.__module__]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for imported in _imported_modules(name):
            if imported.startswith("tokenpal.actions."):
                stack.append(imported)
            else:
                seen.add(imported)
    return any(mod in seen for mod in _NETWORK_MODULES)


def _reaches_network_anywhere(cls: type[AbstractAction]) -> bool:
    """Same walk, but following every ``tokenpal.`` module.

    The bounded walk above is right for the whole registry — an unbounded one
    reaches ``brain.memory`` -> ``research`` -> ``urllib`` and flags purely
    local tools. For a *marked* tool the stricter rule is the correct one: it
    has no business touching a network client at all, however indirectly, and
    ``_needs_consent`` cannot protect it if the catalog says it is ungated.
    """
    seen: set[str] = set()
    stack = [cls.__module__]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for imported in _imported_modules(name):
            if imported.startswith("tokenpal."):
                stack.append(imported)
            else:
                seen.add(imported)
    return any(mod in seen for mod in _NETWORK_MODULES)


def _module_closure(cls: type[AbstractAction], prefix: str) -> set[str]:
    """Every module reachable from *cls*'s module, recursing within *prefix*."""
    seen: set[str] = set()
    stack = [cls.__module__]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for imported in _imported_modules(name):
            if imported.startswith(prefix):
                stack.append(imported)
            else:
                seen.add(imported)
    return seen


def _uses_desktop_helpers(cls: type[AbstractAction]) -> bool:
    """True when the tool, or any helper under ``tokenpal.actions.``, imports
    the desktop-content module. Recursive because the OS read usually lands in
    a ``<platform>_impl`` helper, not in the action class's own module."""
    return "tokenpal.desktop.content" in _module_closure(cls, "tokenpal.actions.")


def _code_string_constants(module_name: str) -> list[str]:
    """String literals in *module_name* that are not docstrings.

    Excluding docstrings matters: the docs tell authors "never hand-build the
    <desktop_content> envelope", and a tool that quotes that rule in a comment
    or docstring is complying, not violating.
    """
    mod = sys.modules.get(module_name)
    if mod is None:
        return []
    try:
        tree = ast.parse(inspect.getsource(mod))
    except (OSError, TypeError, SyntaxError):
        return []
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _dummy_arg_sets(cls: type[AbstractAction]) -> list[dict[str, object]]:
    """Argument shapes to try against ``execute()``.

    A no-argument call only exercises the default branch. Every tool in
    #52-#55 has a mode argument, and a tool that checks consent on one branch
    only would pass a single call — so every value of every declared enum is
    tried, one at a time, on top of the base shape.
    """
    base = _dummy_args(cls)
    shapes = [base]
    schema = getattr(cls, "parameters", None) or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if isinstance(props, dict):
        for name, spec in props.items():
            enum = spec.get("enum") if isinstance(spec, dict) else None
            for value in enum or []:
                shapes.append({**base, name: value})
    return shapes


def _dummy_args(cls: type[AbstractAction]) -> dict[str, object]:
    """Plausible values for the tool's declared required arguments."""
    schema = getattr(cls, "parameters", None) or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", list(props)) if isinstance(schema, dict) else []
    by_type: dict[str, object] = {
        "string": "x", "integer": 1, "number": 1.0, "boolean": True,
        "array": [], "object": {},
    }
    out: dict[str, object] = {}
    for name in required:
        spec = props.get(name, {}) if isinstance(props, dict) else {}
        enum = spec.get("enum") if isinstance(spec, dict) else None
        out[name] = enum[0] if enum else by_type.get(spec.get("type", "string"), "x")
    return out


def _instantiate(cls: type[AbstractAction]) -> AbstractAction:
    try:
        return cls({})
    except Exception as exc:  # platform-gated tool on the wrong host
        pytest.skip(f"{cls.action_name} not constructible here: {exc}")


def test_the_first_marked_tool_is_registered_on_every_host() -> None:
    """``discover_actions`` swallows ImportError, so a marked tool whose module
    stops importing (a pyobjc import escaping function scope, say) would make
    every parametrized case above skip. The import comes first so the
    traceback names the cause. Add the next marked tool to the set."""
    importlib.import_module("tokenpal.actions.read_selection")
    assert {"read_selection"} <= {cls.action_name for cls in _MARKED}


@pytest.mark.parametrize("cls", _MARKED, ids=lambda c: c.action_name)
def test_marked_action_is_never_cached(cls: type[AbstractAction]) -> None:
    """A cached desktop read would replay content the user did not re-request,
    and the agent's cache is keyed on arguments alone."""
    assert cls.cacheable is False


@pytest.mark.parametrize("cls", _MARKED, ids=lambda c: c.action_name)
def test_marked_action_is_safe_or_confirmed(cls: type[AbstractAction]) -> None:
    assert cls.safe is True or cls.requires_confirm is True


@pytest.mark.parametrize("cls", _MARKED, ids=lambda c: c.action_name)
def test_marked_action_is_absent_from_every_ambient_path(
    cls: type[AbstractAction],
) -> None:
    """M3 and the M1 rules build tool lists from hand-written allowlists that
    consult no marker, and both feed the persisted observation path."""
    assert cls.action_name not in M3_CATALOG
    assert cls.action_name not in _IDLE_RULE_TOOLS
    assert cls.action_name not in DEFAULT_TOOLS


@pytest.mark.parametrize("cls", _MARKED, ids=lambda c: c.action_name)
async def test_marked_action_checks_consent_before_arguments(
    cls: type[AbstractAction], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consent is checked first, before any argument validation, so a missing
    grant refuses identically no matter how the model called the tool."""
    # Patch both bindings: content.py's module-level one, and the source in
    # tokenpal.config.consent, so a tool that imports has_consent itself cannot
    # fall through to the developer's real ~/.tokenpal/.consent.json and make
    # this test's verdict depend on the host.
    monkeypatch.setattr("tokenpal.desktop.content.has_consent", lambda *a, **k: False)
    monkeypatch.setattr("tokenpal.config.consent.has_consent", lambda *a, **k: False)
    action = _instantiate(cls)

    result = await action.execute()
    assert result.success is False
    assert "'desktop content' consent" in result.output

    # Again for every declared argument shape, including each enum value: a
    # tool that checks consent on one branch only must not slip through.
    for shape in _dummy_arg_sets(cls):
        with_args = await action.execute(**shape)
        assert with_args.success is False, f"no refusal for arguments {shape!r}"
        assert "'desktop content' consent" in with_args.output, (
            f"wrong refusal for arguments {shape!r}: {with_args.output!r}"
        )


def test_consent_category_string_is_the_one_the_docs_cite() -> None:
    assert Category.DESKTOP_CONTENT == "desktop_content"
    assert DesktopContent.__module__ == "tokenpal.desktop.content"


def test_every_registered_action_has_a_catalog_entry() -> None:
    """``AgentRunner._needs_consent`` decides "this tool reaches the network"
    by catalog lookup, and a missing entry reads as ungated — so an
    uncatalogued network tool would survive the post-content tool drop."""
    orphans = sorted(name for name in _ACTION_REGISTRY if find_entry(name) is None)
    assert orphans == [], f"registered but missing from the catalog: {orphans}"


def test_every_network_reaching_action_declares_a_consent_category() -> None:
    def gated(name: str) -> bool:
        found = find_entry(name)
        return found is not None and bool(found[0].consent_category)

    ungated = sorted(
        name
        for name, cls in _ACTION_REGISTRY.items()
        if _reaches_network(cls) and not gated(name)
    )
    assert ungated == [], f"network tools with no consent category: {ungated}"


@pytest.mark.parametrize("cls", _MARKED, ids=lambda c: c.action_name)
def test_marked_action_reaches_no_network_client(cls: type[AbstractAction]) -> None:
    """Stricter than the registry-wide rule below. A marked tool that reaches
    the wire — even through HttpBackend, whose [llm] api_url may be a remote
    box — is not protected by the post-content tool drop, because that drop
    keys on the catalog's consent_category and not on the marker.
    """
    assert not _reaches_network_anywhere(cls)


@pytest.mark.parametrize("cls", _MARKED, ids=lambda c: c.action_name)
def test_marked_action_does_not_hand_build_the_envelope(
    cls: type[AbstractAction],
) -> None:
    """``to_prompt_block()`` is the only sanctioned producer of the envelope:
    it neutralizes forged closing tags and sanitizes both attributes. A tool
    that formats the tag itself silently opts out of both."""
    modules = _module_closure(cls, "tokenpal.actions.")
    # Only the tool's own code. The closure also holds leaf modules it merely
    # imports — including tokenpal.desktop.content, which legitimately owns
    # the tag literal.
    own = [m for m in modules if m.startswith("tokenpal.actions.")]
    literals = [lit for m in own for lit in _code_string_constants(m)]
    offenders = [lit for lit in literals if "desktop_content" in lit]
    assert offenders == [], (
        f"builds the envelope tag from string literals {offenders!r} — use "
        f"DesktopContent.to_prompt_block(), which neutralizes forged closing "
        f"tags and sanitizes both attributes"
    )
    assert any(
        "to_prompt_block" in (inspect.getsource(sys.modules[m]) if m in sys.modules
                              else "")
        for m in own
    ), "no call to to_prompt_block(): the envelope must not be built by hand"


def test_a_tool_that_reads_desktop_content_declares_the_marker() -> None:
    """The marker is not just this test's selector — it is the switch every
    runtime guard reads (trace redaction, cache bypass, conversation filter
    and refusal). A tool that forgets it is invisible to all of them, and to
    the parametrized cases above, which is why this one is not parametrized.
    """
    unmarked = sorted(
        name
        for name, cls in _ACTION_REGISTRY.items()
        if not cls.reads_desktop_content and _uses_desktop_helpers(cls)
    )
    assert unmarked == [], (
        f"these use the desktop-content helpers without declaring "
        f"reads_desktop_content = True: {unmarked}"
    )
