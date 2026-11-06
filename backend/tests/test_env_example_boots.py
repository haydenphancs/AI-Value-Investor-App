"""`backend/.env.example` must boot when copied to `.env` as-is.

It carried two values that did not: `ALLOWED_ORIGINS=*` (the field is `list[str]`, which
pydantic-settings decodes as JSON, so `Settings()` raised "error parsing value for field
ALLOWED_ORIGINS" before the app could start) and the legacy FMP base URL (CLAUDE.md
invariant #1 — a fresh provision would have pointed every FMP call at the deprecated API).

Every live earnings setting has a line too, commented out like the blocks beside it, so each
switch can be found. A line shows the declared default, which is why none can hold a secret.
"""

import re
from pathlib import Path

from dotenv import dotenv_values

from app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _example_values() -> dict:
    values = dotenv_values(ENV_EXAMPLE)
    assert values, f"{ENV_EXAMPLE} parsed to nothing"
    return values


def test_the_example_file_builds_settings(monkeypatch):
    values = _example_values()
    # The process environment outranks the dotenv file; clear every key the example sets so
    # a value exported in the shell cannot mask a bad one in the file.
    for key in values:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=ENV_EXAMPLE)
    assert settings.ALLOWED_ORIGINS == ["*"]
    assert settings.FMP_BASE_URL == values["FMP_BASE_URL"]


def test_the_example_points_fmp_at_the_stable_api():
    assert _example_values()["FMP_BASE_URL"] == "https://financialmodelingprep.com/stable"


def _live_earnings_lines() -> dict:
    """The `# EARNINGS_LIVE_*=value` lines of the example, by name."""
    found: dict = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"# (EARNINGS_LIVE_[A-Z_]+)=(\S+)", line.strip())
        if match:
            assert match.group(1) not in found, f"{match.group(1)} is listed twice"
            found[match.group(1)] = match.group(2)
    return found


def _as_written(default) -> str:
    return str(default).lower() if isinstance(default, bool) else str(default)


def test_every_live_earnings_setting_has_a_line_showing_what_ships():
    declared = {name: _as_written(field.default) for name, field in Settings.model_fields.items()
                if name.startswith("EARNINGS_LIVE_")}
    assert "EARNINGS_LIVE_RESULT_PUSH_ENABLED" in declared, "a rename must not empty this test"
    assert _live_earnings_lines() == declared


def _comment_above(name: str) -> str:
    """The comment lines right above a setting's commented-out line."""
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith(f"# {name}="))
    comment = []
    for line in reversed(lines[:at]):
        if not line.startswith("# ") or re.fullmatch(r"# [A-Z_]+=\S+", line):
            break
        comment.insert(0, line[2:])
    return " ".join(comment)


def test_the_sec_budget_line_says_it_limits_a_round_and_not_a_day():
    """An owner sizes this number from its comment. A report with its figures in leaves the list
    and the next takes its slot, so 25 is no ceiling on the tickers one day reads."""
    comment = _comment_above("EARNINGS_LIVE_SEC_MAX_SYMBOLS")
    assert "a round" in comment and "not a daily budget" in comment
    assert "per report day" not in comment


def test_the_result_push_line_says_what_off_can_lose():
    """Off is more than a wait for 16:00 ET, and the line that switches it must say so."""
    comment = _comment_above("EARNINGS_LIVE_RESULT_PUSH_ENABLED")
    assert "16:00 ET" in comment and "earnings_live_sender" in comment


def test_no_live_earnings_line_is_active():
    """Active, a copied `.env` would pin today's default past the day it changes."""
    assert not [key for key in _example_values() if key.startswith("EARNINGS_LIVE_")]


def test_every_live_earnings_line_boots_as_written(monkeypatch):
    lines = _live_earnings_lines()
    assert lines, "no line to check"
    for name, value in lines.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=ENV_EXAMPLE)
    for name in lines:
        assert getattr(settings, name) == Settings.model_fields[name].default, name
