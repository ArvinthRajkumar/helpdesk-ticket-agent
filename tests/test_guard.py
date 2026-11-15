"""The commit guard is a script, not a package module, so load it from its path."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "guard_commit", Path(__file__).resolve().parents[1] / "scripts" / "guard_commit.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def matches(text: str) -> bool:
    return any(p.search(text) for p in guard.SECRET_PATTERNS)


@pytest.mark.parametrize(
    "line",
    [
        "GEMINI_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstuvw",  # Google: AIza + 35 chars
        "key = 'sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'",  # Anthropic
        "api_key: 0123456789abcdef0123456789abcdef0123",  # generic long token on a key name
    ],
)
def test_secret_patterns_catch_real_shapes(line: str) -> None:
    assert matches(line)


@pytest.mark.parametrize(
    "line",
    [
        "GEMINI_API_KEY=",  # the .env.example placeholder
        "AIzaTooShort",
        "The AIza prefix is documented in guard_commit.py",
        "model: gemini-3.1-flash-lite",
    ],
)
def test_secret_patterns_leave_ordinary_text_alone(line: str) -> None:
    assert not matches(line)


def test_env_files_are_blocked_except_the_example() -> None:
    assert guard.BLOCKED_PATHS.search(".env")
    assert guard.BLOCKED_PATHS.search("config/.env.local")
    assert ".env.example" in guard.ALLOWED_PATHS
