"""Sense plugin discovery and registration."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from tokenpal.senses.base import AbstractSense
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_SENSE_REGISTRY: dict[str, list[type[AbstractSense]]] = {}


def register_sense(cls: type[AbstractSense]) -> type[AbstractSense]:
    """Decorator. Registers a concrete sense implementation."""
    _SENSE_REGISTRY.setdefault(cls.sense_name, []).append(cls)
    return cls


def discover_senses(extra_packages: list[str] | None = None) -> None:
    """Walk tokenpal.senses.* and any extra packages, importing all modules
    so @register_sense decorators fire."""
    import tokenpal.senses as senses_pkg

    _walk_and_import(senses_pkg)
    for pkg_name in extra_packages or []:
        try:
            pkg = importlib.import_module(pkg_name)
            _walk_and_import(pkg)
        except ImportError:
            log.warning("Could not import plugin package: %s", pkg_name)


def _walk_and_import(package: Any) -> None:
    for _importer, modname, _ispkg in pkgutil.walk_packages(
        package.__path__, prefix=package.__name__ + "."
    ):
        try:
            importlib.import_module(modname)
        except ImportError as e:
            log.debug("Skipping %s: %s", modname, e)


def resolve_senses(
    sense_flags: dict[str, bool],
    sense_overrides: dict[str, str] | None = None,
    sense_configs: dict[str, dict[str, Any]] | None = None,
) -> list[AbstractSense]:
    """For each enabled sense, pick the best platform-compatible impl and instantiate it."""
    plat = current_platform()
    overrides = sense_overrides or {}
    configs = sense_configs or {}
    instances: list[AbstractSense] = []

    for sense_name, enabled in sense_flags.items():
        if not enabled:
            continue

        candidates = _SENSE_REGISTRY.get(sense_name, [])
        compatible = [c for c in candidates if plat in c.platforms]

        if not compatible:
            log.warning("No implementation for sense '%s' on %s", sense_name, plat)
            continue

        override = overrides.get(sense_name)
        if override:
            chosen = next((c for c in compatible if c.__name__ == override), None)
            if chosen is None:
                log.warning(
                    "Override '%s' not found for sense '%s', using default",
                    override, sense_name,
                )
                chosen = min(compatible, key=lambda c: c.priority)
        else:
            chosen = min(compatible, key=lambda c: c.priority)

        sense_config = configs.get(sense_name, {})
        instances.append(chosen(sense_config))
        log.info(
            "Loaded sense '%s' -> %s (priority %d)",
            sense_name, chosen.__name__, chosen.priority,
        )

    return instances
