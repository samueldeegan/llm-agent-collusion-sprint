"""The zero-client guard is active inside module-scoped fixtures, not only inside test functions."""
import anthropic
import openai
import pytest

from tests.conftest import API_GUARD_MESSAGE, api_guard


def _attempt(cls, **kw):
    try:
        cls(**kw)
    except RuntimeError as e:
        return str(e)
    return None


@pytest.fixture(scope="module")
def module_attempts():
    # no explicit api_guard() here: the session guard alone must already be active
    return {"anthropic": _attempt(anthropic.Anthropic, api_key="x"),
            "openai": _attempt(openai.OpenAI, api_key="x")}


@pytest.fixture(scope="module")
def module_attempts_explicit():
    with api_guard():
        return {"anthropic": _attempt(anthropic.Anthropic, api_key="x"),
                "openai": _attempt(openai.OpenAI, api_key="x")}


def test_guard_active_in_module_fixture(module_attempts):
    assert module_attempts == {"anthropic": API_GUARD_MESSAGE, "openai": API_GUARD_MESSAGE}


def test_explicit_guard_in_module_fixture(module_attempts_explicit):
    assert module_attempts_explicit == {"anthropic": API_GUARD_MESSAGE, "openai": API_GUARD_MESSAGE}


def test_guard_active_in_test_function():
    with pytest.raises(RuntimeError, match=API_GUARD_MESSAGE):
        anthropic.Anthropic(api_key="x")
