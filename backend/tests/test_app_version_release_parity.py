"""The version the app SHIPS passes every server-side version gate (2026-10-08).

The release after 1.0 is named "1.01" (owner, 2026-10-08). Apple and
`app.core.client_app_version.parse_app_version` both compare each number as a whole number, so
"1.01" reads as (1, 1, 0) — the same as "1.1" — and passes the gates written for 1.1:
`chat_web_search_service.WEB_SEARCH_MIN_APP_VERSION` (report-chat web search) and
`entitlements.THEME_LOCK_MIN_APP_VERSION` (the Free theme list's top-5 lock).

A name that parses LOWER than a gate would switch those features off with no error anywhere:
"1.0.5" reads as (1, 0, 5) < (1, 1, 0), so that build would get no web search and every Free user
would get the full theme list, exactly as if it were build 1.0. These tests read the shipped
`MARKETING_VERSION` from the Xcode project, so a rename that breaks the gates fails here first.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from app.core.client_app_version import parse_app_version
from app.services.chat_web_search_service import WEB_SEARCH_MIN_APP_VERSION
from app.services.entitlements import THEME_LOCK_MIN_APP_VERSION

_REPO = Path(__file__).resolve().parents[2]
_PBXPROJ = _REPO / "frontend" / "ios" / "ios.xcodeproj" / "project.pbxproj"
_ASC_SCRIPT = _REPO / "backend" / "scripts" / "asc_review_resubmit.py"


def _marketing_versions() -> list:
    return re.findall(r"MARKETING_VERSION = ([^;]+);", _PBXPROJ.read_text(encoding="utf-8"))


def _shipped_version() -> str:
    versions = set(_marketing_versions())
    assert len(versions) == 1, f"targets disagree on MARKETING_VERSION: {sorted(versions)}"
    return versions.pop().strip().strip('"')


def test_every_target_ships_the_same_version():
    """The app and the widget extension must carry one version (an extension's version must
    match its app's), in every configuration."""
    versions = _marketing_versions()
    assert len(versions) >= 4, versions  # app + widget, Debug + Release
    assert len(set(versions)) == 1, versions


def test_the_shipped_version_passes_every_server_gate():
    shipped = _shipped_version()
    parsed = parse_app_version(shipped)
    assert parsed is not None, f"MARKETING_VERSION {shipped!r} is not a version the backend can read"
    assert parsed >= WEB_SEARCH_MIN_APP_VERSION, (shipped, parsed, WEB_SEARCH_MIN_APP_VERSION)
    assert parsed >= THEME_LOCK_MIN_APP_VERSION, (shipped, parsed, THEME_LOCK_MIN_APP_VERSION)


def test_one_point_oh_one_reads_as_one_point_one_and_a_patch_name_would_not():
    """The trap this file exists for: a "1.0.x" name reads as older than 1.1."""
    assert parse_app_version("1.01") == parse_app_version("1.1") == (1, 1, 0)
    assert parse_app_version("1.0.5") < WEB_SEARCH_MIN_APP_VERSION
    assert parse_app_version("1.0.5") < THEME_LOCK_MIN_APP_VERSION


def test_the_review_notes_script_targets_the_shipped_version():
    spec = importlib.util.spec_from_file_location("asc_review_resubmit_version_probe", _ASC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module._VERSION == _shipped_version()
