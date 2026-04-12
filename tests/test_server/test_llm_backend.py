"""Tests for set_api_url() on LLM backends."""

import pytest

from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.http_backend import HttpBackend


def test_set_api_url_changes_endpoint():
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    assert backend._api_url == "http://localhost:11434/v1"

    backend.set_api_url("http://geefourteen:8585/v1")
    assert backend._api_url == "http://geefourteen:8585/v1"


def test_set_api_url_resets_state():
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    backend._reachable = True
    backend._model_available = True

    backend.set_api_url("http://geefourteen:8585/v1")
    assert backend._reachable is False
    assert backend._model_available is False


def test_set_api_url_strips_trailing_slash():
    backend = HttpBackend({"api_url": "http://localhost:11434/v1"})
    backend.set_api_url("http://geefourteen:8585/v1/")
    assert backend._api_url == "http://geefourteen:8585/v1"


def test_abstract_backend_raises_not_implemented():
    class DummyBackend(AbstractLLMBackend):
        backend_name = "dummy"
        platforms = ("darwin",)
        async def setup(self): pass
        async def generate(self, prompt, max_tokens=256): pass
        async def teardown(self): pass

    backend = DummyBackend({})
    with pytest.raises(NotImplementedError, match="does not support URL switching"):
        backend.set_api_url("http://example.com")
