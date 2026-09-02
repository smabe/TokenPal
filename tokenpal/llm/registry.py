"""LLM backend discovery, registration, and config marshalling."""

from __future__ import annotations

import dataclasses
import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, Any

from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.util.platform import current_platform

if TYPE_CHECKING:
    from tokenpal.brain.memory import MemoryStore
    from tokenpal.config.schema import TokenPalConfig

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


def backend_config(
    config: TokenPalConfig,
    *,
    memory_store: MemoryStore | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build the flat config dict a backend is constructed from.

    Passing *memory_store* lets backends with a throughput estimator persist
    their EWMAs keyed by (api_url, model) so a known rig doesn't burn its
    3-call bootstrap window on every restart. See plans/gpu-scaling.md.
    """
    llm_config = dataclasses.asdict(config.llm)
    llm_config["server_mode"] = config.server.mode
    if memory_store is not None:
        llm_config["memory_store"] = memory_store
    llm_config.update(overrides)
    return llm_config


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
