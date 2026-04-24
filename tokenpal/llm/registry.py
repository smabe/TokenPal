"""LLM backend discovery and registration."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_BACKEND_REGISTRY: dict[str, type[AbstractLLMBackend]] = {}


def register_backend(cls: type[AbstractLLMBackend]) -> type[AbstractLLMBackend]:
    """Decorator. Registers a concrete LLM backend."""
    _BACKEND_REGISTRY[cls.backend_name] = cls
    return cls


def discover_backends() -> None:
    """Import all modules under tokenpal.llm so decorators fire."""
    import tokenpal.llm as llm_pkg

    for _importer, modname, _ispkg in pkgutil.walk_packages(
        llm_pkg.__path__, prefix=llm_pkg.__name__ + "."
    ):
        try:
            importlib.import_module(modname)
        except ImportError as e:
            log.debug("Skipping backend module %s: %s", modname, e)


def resolve_backend(config: dict[str, Any]) -> AbstractLLMBackend:
    """Pick the backend matching config['backend'] and instantiate it."""
    backend_name = config.get("backend", "http")
    plat = current_platform()

    cls = _BACKEND_REGISTRY.get(backend_name)
    if cls is None:
        available = list(_BACKEND_REGISTRY.keys())
        raise RuntimeError(f"Unknown LLM backend '{backend_name}'. Available: {available}")

    if plat not in cls.platforms:
        log.warning(
            "Backend '%s' not officially supported on %s, trying anyway",
            backend_name, plat,
        )

    log.info("Using LLM backend: %s (%s)", cls.__name__, backend_name)
    return cls(config)
