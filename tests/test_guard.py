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


# Built at runtime so the key shapes never appear literally in source, where the commit guard
# itself and push protection would flag them.
GOOGLE_SHAPE = "GEMINI_API_KEY=" + "AI" + "za" + "A" * 35
ANTHROPIC_SHAPE = "key = '" + "sk-" + "ant-" + "x" * 30 + "'"
GENERIC_SHAPE = "api_key: " + "0123456789abcdef" * 2 + "0123"
HELPDESK_SHAPE = "HELPDESK_API_KEY=" + "nrd_" + "round_" + "x7" * 8


@pytest.mark.parametrize("line", [GOOGLE_SHAPE, ANTHROPIC_SHAPE, GENERIC_SHAPE, HELPDESK_SHAPE])
def test_secret_patterns_catch_real_shapes(line: str) -> None:
    assert matches(line)


@pytest.mark.parametrize(
    "line",
    [
        "GEMINI_API_KEY=",  # the .env.example placeholder
        "HELPDESK_API_KEY=",
        "export KEY=" + "nrd_" + "round_...",  # the documented placeholder in the API reference
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
