from contextlib import contextmanager
from pathlib import Path

import pytest

API_GUARD_MESSAGE = "API client constructed inside the test suite"


def _boom(*a, **k):
    raise RuntimeError(API_GUARD_MESSAGE)


@contextmanager
def api_guard():
    """Make constructing an Anthropic or OpenAI client raise, for any fixture scope.

    The function-scoped autouse ``_no_api_calls`` is not active while module- or session-scoped
    fixtures run, so those fixtures enter this guard themselves (``_api_guard_session`` also
    covers them, since session autouse fixtures are set up before any module fixture)."""
    import anthropic
    import openai
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(anthropic.Anthropic, "__init__", _boom)
        mp.setattr(openai.OpenAI, "__init__", _boom)
        yield


@pytest.fixture(scope="session", autouse=True)
def _api_guard_session():
    with api_guard():
        yield


@pytest.fixture(autouse=True)
def _no_api_calls(monkeypatch):
    import anthropic
    import openai
    monkeypatch.setattr(anthropic.Anthropic, "__init__", _boom)
    monkeypatch.setattr(openai.OpenAI, "__init__", _boom)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
