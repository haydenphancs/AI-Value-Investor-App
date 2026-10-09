"""Report chat's web search stays off for app versions whose in-app copy predates it.

Build 1.0 (10) went public on 2026-10-05 with an in-app Privacy Policy and AI data-consent sheet
written before the Brave web search existed; 1.1 carries both. So the search is gated on the
caller's `X-App-Version` (`app.core.client_app_version`), captured per request by a router-level
dependency on the chat router, and checked in `report_web_search_available` — the one function
all three web-search gates read.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.core import client_app_version as cav
from app.services import chat_web_search_service as ws

_CHAT_ENDPOINT = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "chat.py"


def _in_fresh_context(fn, *args):
    """Run with the request-scoped value isolated, as each request is."""
    return contextvars.copy_context().run(fn, *args)


def _as_caller(header, fn, *args):
    def run():
        cav.set_client_app_version(header)
        return fn(*args)
    return _in_fresh_context(run)


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("1.0", (1, 0, 0)),
    ("1.1", (1, 1, 0)),
    (" 1.1 ", (1, 1, 0)),
    ("1.01", (1, 1, 0)),   # the release name after 1.0 (owner, 2026-10-08): read as 1.1
    ("1.02", (1, 2, 0)),
    ("1.33", (1, 33, 0)),
    ("1.0.2", (1, 0, 2)),
    ("2", (2, 0, 0)),
    ("10.0", (10, 0, 0)),
    ("1.10", (1, 10, 0)),
])
def test_parses_marketing_versions(raw, expected):
    assert cav.parse_app_version(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "abc", "1.0 (10)", "v1.1", "1.2.3.4", "1..1", "-1.0", "1.0-beta",
    "1." , ".1", "1" * 40, 1.1, 11, b"1.1",
])
def test_unreadable_versions_parse_to_none(raw):
    assert cav.parse_app_version(raw) is None


def test_ten_sorts_after_nine_not_before_two():
    # Tuple, not string, comparison: "1.10" is newer than "1.9".
    assert cav.parse_app_version("1.10") > cav.parse_app_version("1.9")


# ── who counts as older ──────────────────────────────────────────────────────

@pytest.mark.parametrize("header, older", [
    (None, False),          # no header: a test, a script, a future web client
    ("garbage", False),     # unreadable: fail open
    ("9" * 33, False),      # over-long: dropped, i.e. treated as absent
    ("1.0", True),          # build 10, the App Store build until 1.1
    ("1.0.9", True),
    ("0.9", True),
    ("1.1", False),
    ("1.1.0", False),
    ("1.01", False),        # the shipped name of the release after 1.0
    ("1.2", False),
    ("2.0", False),
])
def test_client_is_older_than_1_1(header, older):
    assert _as_caller(header, cav.client_is_older_than, (1, 1, 0)) is older


def test_the_value_does_not_leak_between_requests():
    _as_caller("1.0", lambda: None)
    assert _in_fresh_context(cav.client_app_version) is None


# ── the gates ────────────────────────────────────────────────────────────────

@pytest.fixture
def search_switched_on(monkeypatch):
    monkeypatch.setattr(ws.settings, "CHAT_REPORT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(ws.brave_search, "is_configured", lambda: True)


_ASK = "Can you search the web for the latest news on this?"


def test_the_ask_used_below_is_a_web_search_intent():
    assert ws.is_web_search_intent(_ASK)


@pytest.mark.parametrize("header, available", [
    (None, True), ("1.1", True), ("1.01", True), ("2.0", True), ("1.0", False), ("1.0.3", False),
])
def test_availability_follows_the_app_version(search_switched_on, header, available):
    assert _as_caller(header, ws.report_web_search_available) is available


def test_switch_off_wins_for_every_version(monkeypatch):
    monkeypatch.setattr(ws.settings, "CHAT_REPORT_WEB_SEARCH_ENABLED", False)
    monkeypatch.setattr(ws.brave_search, "is_configured", lambda: True)
    for header in (None, "1.0", "1.1"):
        assert _as_caller(header, ws.report_web_search_available) is False


def test_a_1_0_caller_is_never_offered_a_search_and_is_told_none_ran(search_switched_on):
    turn = _as_caller("1.0", ws.open_web_search_turn, "REPORT", "TICKER_REPORT", _ASK, "user-1", "AAPL")
    assert turn is None
    assert _as_caller("1.0", ws.web_search_intent_unserved, "REPORT", "TICKER_REPORT", _ASK) is True
    assert _as_caller("1.0", ws.web_search_offered_on_request, "REPORT", "TICKER_REPORT") is False


def test_a_1_1_caller_gets_the_search(search_switched_on):
    turn = _as_caller("1.1", ws.open_web_search_turn, "REPORT", "TICKER_REPORT", _ASK, "user-1", "AAPL")
    assert turn is not None and turn.tier == ws.TIER_REPORT_EXPLICIT
    unserved = _as_caller("1.1", lambda: ws.web_search_intent_unserved(
        "REPORT", "TICKER_REPORT", _ASK, user_id="user-1"))
    assert unserved is False
    assert _as_caller("1.1", ws.web_search_offered_on_request, "REPORT", "TICKER_REPORT") is True


# ── the request plumbing, end to end in a real FastAPI app ───────────────────

def _probe_app() -> FastAPI:
    router = APIRouter(dependencies=[Depends(cav.capture_client_app_version)])

    @router.get("/probe")
    async def probe():
        async def body():
            yield f"handler={cav.client_app_version()};"
            task_seen = await asyncio.create_task(asyncio.sleep(0, result=cav.client_app_version()))
            yield f"task={task_seen};"
            thread_seen = await asyncio.to_thread(cav.client_app_version)
            yield f"thread={thread_seen}"
        return StreamingResponse(body(), media_type="text/plain")

    app = FastAPI()
    app.include_router(router)
    return app


def test_a_router_dependency_reaches_the_stream_its_tasks_and_threads():
    client = TestClient(_probe_app())
    old = client.get("/probe", headers={"X-App-Version": "1.0"}).text
    assert old == "handler=(1, 0, 0);task=(1, 0, 0);thread=(1, 0, 0)"
    # The next request without the header starts from the default — nothing carried over.
    none = client.get("/probe").text
    assert none == "handler=None;task=None;thread=None"


def test_the_chat_router_captures_the_version():
    src = "\n".join(
        re.sub(r"\s#.*$", "", line) for line in _CHAT_ENDPOINT.read_text().splitlines()
        if not line.strip().startswith("#")
    )
    assert re.search(r"router\s*=\s*APIRouter\(\s*dependencies=\[\s*Depends\(capture_client_app_version\)", src), (
        "the chat router no longer records X-App-Version — build 1.0 would get report web search"
    )


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_every_chat_route_in_the_real_app_runs_the_version_capture():
    """The regex above passes even if a route moved to a second router. This walks what is
    actually served: every /api/v1/chat route must run `capture_client_app_version`, or the
    ContextVar stays unset there and the gate fails OPEN for build 1.0."""
    from fastapi.routing import APIRoute
    from app.main import app

    chat_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1/chat")]
    assert len(chat_routes) >= 5, f"only {len(chat_routes)} chat routes found — this scan has drifted"
    missing = sorted(
        f"{sorted(r.methods)} {r.path}" for r in chat_routes
        if cav.capture_client_app_version not in set(_dependency_calls(r.dependant))
    )
    assert not missing, f"chat routes without the X-App-Version capture: {missing}"


def test_only_the_chat_endpoint_module_reaches_the_chat_service():
    """Any other endpoint module that can reach the chat service (and so web search) would need
    the capture too; today there is none, and this fails if one appears."""
    endpoints = _CHAT_ENDPOINT.parent
    reaching = sorted(
        p.name for p in endpoints.glob("*.py")
        if re.search(r"\b(chat_service|chat_web_search_service|get_chat_service)\b", p.read_text())
    )
    assert reaching == ["chat.py"], reaching


# ── the WITHHELD log line ─────────────────────────────────────────────────────

def _withheld(caplog):
    return [r.getMessage() for r in caplog.records if "REPORT_WEB_SEARCH_WITHHELD" in r.getMessage()]


def test_a_withheld_ask_is_logged_with_its_reason_and_version_but_never_the_query(search_switched_on, caplog):
    caplog.set_level("INFO", logger=ws.logger.name)
    _as_caller("1.0", ws.open_web_search_turn, "REPORT", "TICKER_REPORT", _ASK, "user-1", "AAPL")
    lines = _withheld(caplog)
    assert lines == ["REPORT_WEB_SEARCH_WITHHELD reason=app_version app_version=1.0.0"]
    assert "latest news" not in caplog.text and "search the web" not in caplog.text


@pytest.mark.parametrize("setting, value, reason", [
    ("CHAT_REPORT_WEB_SEARCH_ENABLED", False, "switch_off"),
])
def test_the_switch_off_reason(monkeypatch, caplog, setting, value, reason):
    monkeypatch.setattr(ws.settings, setting, value)
    monkeypatch.setattr(ws.brave_search, "is_configured", lambda: True)
    caplog.set_level("INFO", logger=ws.logger.name)
    _as_caller("1.1", ws.open_web_search_turn, "REPORT", "TICKER_REPORT", _ASK, "user-1", "AAPL")
    assert _withheld(caplog) == [f"REPORT_WEB_SEARCH_WITHHELD reason={reason} app_version=1.1.0"]


def test_the_no_key_reason_and_an_absent_header(monkeypatch, caplog):
    monkeypatch.setattr(ws.settings, "CHAT_REPORT_WEB_SEARCH_ENABLED", True)
    monkeypatch.setattr(ws.brave_search, "is_configured", lambda: False)
    caplog.set_level("INFO", logger=ws.logger.name)
    _as_caller(None, ws.open_web_search_turn, "REPORT", "TICKER_REPORT", _ASK, "user-1", "AAPL")
    assert _withheld(caplog) == ["REPORT_WEB_SEARCH_WITHHELD reason=no_key app_version=none"]


def test_nothing_is_logged_when_nobody_asked_for_a_search(search_switched_on, caplog):
    caplog.set_level("INFO", logger=ws.logger.name)
    _as_caller("1.0", ws.open_web_search_turn, "REPORT", "TICKER_REPORT", "what is the moat?", "user-1", "AAPL")
    assert _withheld(caplog) == []
