import builtins
import functools
import io
import os
import shutil
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


REPO_ROOT = Path(__file__).resolve().parents[1]
_PROTECTED_DIRS = (REPO_ROOT / "results", REPO_ROOT / "config")
_PROTECTED_FILES = (REPO_ROOT / "docs" / "DECISIONS.md",)


def _is_protected(p) -> bool:
    try:
        q = Path(os.fspath(p)).resolve()
    except (TypeError, ValueError, OSError):
        return False  # file descriptors and other non-path arguments
    if q in _PROTECTED_FILES:
        return True
    return any(q == d or d in q.parents for d in _PROTECTED_DIRS)


def _refuse(p):
    raise PermissionError(f"test suite must not write under {p}")


def _is_write_mode(mode) -> bool:
    return isinstance(mode, str) and any(c in mode for c in "wax+")


@pytest.fixture(scope="session", autouse=True)
def _protect_repo_dirs():
    """Refuse any write, create, delete or move under results/, config/ or DECISIONS.md."""
    real_open = builtins.open

    def guard_open(file, mode="r", *a, **k):
        if _is_write_mode(mode) and _is_protected(file):
            _refuse(file)
        return real_open(file, mode, *a, **k)

    def wrap(fn, nargs=1):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            for a in args[:nargs]:
                if _is_protected(a):
                    _refuse(a)
            return fn(*args, **kwargs)
        return inner

    def wrap_method(fn):
        def inner(self, *args, **kwargs):
            if _is_protected(self):
                _refuse(self)
            return fn(self, *args, **kwargs)
        return inner

    def wrap_path_open(fn):
        def inner(self, mode="r", *a, **k):
            if _is_write_mode(mode) and _is_protected(self):
                _refuse(self)
            return fn(self, mode, *a, **k)
        return inner

    def wrap_rename(fn):  # Path.rename/replace: source and target
        def inner(self, target, *a, **k):
            for x in (self, target):
                if _is_protected(x):
                    _refuse(x)
            return fn(self, target, *a, **k)
        return inner

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(builtins, "open", guard_open)
        mp.setattr(io, "open", guard_open)
        for mod, name, n in [(os, "mkdir", 1), (os, "makedirs", 1), (os, "remove", 1),
                             (os, "unlink", 1), (os, "rename", 2), (os, "replace", 2),
                             (shutil, "rmtree", 1), (shutil, "move", 2)]:
            mp.setattr(mod, name, wrap(getattr(mod, name), n))
        for name in ("mkdir", "write_text", "write_bytes", "unlink"):
            mp.setattr(Path, name, wrap_method(getattr(Path, name)))
        mp.setattr(Path, "open", wrap_path_open(Path.open))
        mp.setattr(Path, "rename", wrap_rename(Path.rename))
        mp.setattr(Path, "replace", wrap_rename(Path.replace))
        yield
