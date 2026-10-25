from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_api_calls(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("API client constructed inside the test suite")
    import anthropic
    import openai
    monkeypatch.setattr(anthropic.Anthropic, "__init__", boom)
    monkeypatch.setattr(openai.OpenAI, "__init__", boom)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
