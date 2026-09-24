"""`backend/.env.example` must boot when copied to `.env` as-is.

It carried two values that did not: `ALLOWED_ORIGINS=*` (the field is `list[str]`, which
pydantic-settings decodes as JSON, so `Settings()` raised "error parsing value for field
ALLOWED_ORIGINS" before the app could start) and the legacy FMP base URL (CLAUDE.md
invariant #1 — a fresh provision would have pointed every FMP call at the deprecated API).
"""

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
