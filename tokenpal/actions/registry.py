"""Action plugin discovery and registration."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from tokenpal.actions.base import AbstractAction
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_ACTION_REGISTRY: dict[str, type[AbstractAction]] = {}


def register_action(cls: type[AbstractAction]) -> type[AbstractAction]:
    """Decorator. Registers a concrete action implementation."""
    _ACTION_REGISTRY[cls.action_name] = cls
    return cls


def discover_actions() -> None:
    """Import all modules under tokenpal.actions so @register_action decorators fire."""
    import tokenpal.actions as actions_pkg

    for _importer, modname, _ispkg in pkgutil.walk_packages(
        actions_pkg.__path__, prefix=actions_pkg.__name__ + "."
    ):
        try:
            importlib.import_module(modname)
        except ImportError as e:
            log.debug("Skipping action module %s: %s", modname, e)


def resolve_actions(
    enabled: dict[str, bool],
    action_configs: dict[str, dict[str, Any]] | None = None,
) -> list[AbstractAction]:
    """Instantiate all enabled, platform-compatible actions."""
    plat = current_platform()
    configs = action_configs or {}
    instances: list[AbstractAction] = []

    for action_name, cls in _ACTION_REGISTRY.items():
        if not enabled.get(action_name, True):
            log.debug("Action '%s' disabled by config", action_name)
            continue

        if plat not in cls.platforms:
            log.debug("Action '%s' not supported on %s", action_name, plat)
            continue

        action_config = configs.get(action_name, {})
        instances.append(cls(action_config))
        log.info("Loaded action '%s'", action_name)

    return instances
