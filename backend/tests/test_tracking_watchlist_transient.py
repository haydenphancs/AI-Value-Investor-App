"""/tracking/assets: retry the watchlist read, and report ONE Sentry event, not two.

Two production defects are pinned here.

1. **Double Sentry report.** `tracking_service` did `logger.exception` on the raw
   postgrest `APIError` and then `tracking.py` re-logged the wrapping
   `WatchlistUnavailableError` with `exc_info=True`. With
   `LoggingIntegration(event_level=ERROR)` that is TWO issues per blip, always moving
   in lockstep — exactly what Sentry showed for 2026-07-30. The service must now log
   NOTHING; the endpoint is the single reporter and picks the level.

2. **A gateway blip 503'd real users.** Supabase's Cloudflare edge answers with an
   HTML `520` page; postgrest raises `APIError('JSON could not be generated')`. The
   read is idempotent, so it must be retried before giving up.

What must NOT change: an unreadable watchlist still RAISES. iOS purges every
portfolio ticker absent from this feed, so degrading a failed read into an empty
200 permanently deletes the user's portfolios.
"""
from __future__ import annotations

import logging

import pytest
from postgrest.exceptions import APIError, generate_default_error_message

from app.api.v1.endpoints import tracking as tracking_ep
from app.services import tracking_service as ts
from app.services.tracking_service import TrackingService, WatchlistUnavailableError


# ── builders ─────────────────────────────────────────────────────────────────

def _gateway_error(status: int = 520) -> APIError:
    class _R:
        status_code = status
        content = b"<!DOCTYPE html><title>520: Web server is returning an unknown error</title>"

    return APIError(generate_default_error_message(_R()))


class _FakeResp:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    """Fluent stub; each .execute() pops the next scripted item."""

    def __init__(self, script):
        self._script = list(script)
        self.execute_calls = 0

    def table(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        self.execute_calls += 1
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)


@pytest.fixture(autouse=True)
def _no_feed_cache():
    """The 30s per-user feed cache would serve a stale hit across tests."""
    ts._feed_cache.clear()
    yield
    ts._feed_cache.clear()


def _install(monkeypatch, script):
    fake = _FakeSupabase(script)
    monkeypatch.setattr(ts, "get_supabase", lambda: fake)
    return fake


# ── the retry ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watchlist_read_retries_a_520_then_succeeds(monkeypatch):
    # Empty rows on the retry → get_tracking_feed returns early, so no FMP fan-out
    # needs stubbing and the test stays about the read path only.
    fake = _install(monkeypatch, [_gateway_error(520), []])

    feed = await TrackingService().get_tracking_feed("u1")

    assert fake.execute_calls == 2  # blipped once, retried, succeeded
    assert feed.assets == []


@pytest.mark.asyncio
async def test_a_23505_is_not_retried(monkeypatch):
    """Negative control — only transient gateway failures get a second attempt."""
    err = APIError({"message": "dupe", "code": "23505", "hint": None, "details": None})
    fake = _install(monkeypatch, [err])

    with pytest.raises(WatchlistUnavailableError):
        await TrackingService().get_tracking_feed("u1")
    assert fake.execute_calls == 1


@pytest.mark.asyncio
async def test_persistent_transient_still_raises_and_never_empties_the_feed(monkeypatch):
    """The retry must not become a back door to the empty-feed data loss.

    Proves BOTH that the retry ran (attempt count) AND that exhausting it still
    refuses to answer with an empty feed.
    """
    fake = _install(monkeypatch, [_gateway_error(520)])

    with pytest.raises(WatchlistUnavailableError):
        await TrackingService().get_tracking_feed("u1")
    assert fake.execute_calls > 1


@pytest.mark.asyncio
async def test_the_postgrest_error_survives_as_the_cause(monkeypatch):
    """`raise ... from exc` is what lets the endpoint classify and keep the stack."""
    _install(monkeypatch, [_gateway_error(520)])

    with pytest.raises(WatchlistUnavailableError) as excinfo:
        await TrackingService().get_tracking_feed("u1")
    assert isinstance(excinfo.value.__cause__, APIError)
    assert excinfo.value.__cause__.code == 520


# ── the service must be silent ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_emits_nothing_at_error_level(monkeypatch, caplog):
    """Half of the double-report fix: the service must not open a Sentry issue.

    `LoggingIntegration(event_level=ERROR)` (app/main.py) is the exact threshold, so
    that — not "no logging at all" — is the contract. Retry notices from the shared
    helper are WARNING and are welcome; a `logger.exception` here is what used to
    open the second, always-in-lockstep `APIError` issue.
    """
    _install(monkeypatch, [_gateway_error(520)])

    with caplog.at_level(logging.DEBUG, logger=ts.logger.name):
        with pytest.raises(WatchlistUnavailableError):
            await TrackingService().get_tracking_feed("u1")

    offenders = [
        r for r in caplog.records
        if r.name == ts.logger.name and r.levelno >= logging.ERROR
    ]
    assert offenders == [], (
        "tracking_service must not report at ERROR — the endpoint is the single "
        f"reporter; got {[r.getMessage() for r in offenders]}"
    )


@pytest.mark.asyncio
async def test_service_does_not_report_the_failure_itself(monkeypatch, caplog):
    """Complements the level check: no *failure report* at any level either.

    A demotion to `logger.warning('watchlist read failed …')` would dodge the ERROR
    assertion above while still duplicating what the endpoint says. Only the shared
    helper's '… — retrying' notices are permitted.
    """
    _install(monkeypatch, [_gateway_error(520)])

    with caplog.at_level(logging.DEBUG, logger=ts.logger.name):
        with pytest.raises(WatchlistUnavailableError):
            await TrackingService().get_tracking_feed("u1")

    non_retry = [
        r for r in caplog.records
        if r.name == ts.logger.name and "retrying" not in r.getMessage()
    ]
    assert non_retry == [], f"unexpected report: {[r.getMessage() for r in non_retry]}"


# ── the endpoint is the single reporter ──────────────────────────────────────

class _StubService:
    def __init__(self, exc):
        self._exc = exc

    async def get_tracking_feed(self, _user_id):
        raise self._exc


def _chain(inner: BaseException) -> WatchlistUnavailableError:
    """Reproduce the service's `raise WatchlistUnavailableError(...) from exc` shape."""
    err = WatchlistUnavailableError("watchlist read failed for user u1")
    err.__cause__ = inner
    return err


async def _call_endpoint(monkeypatch, exc):
    monkeypatch.setattr(tracking_ep, "TrackingService", lambda: _StubService(exc))
    return await tracking_ep.get_tracking_assets(user={"id": "u1"})


@pytest.mark.asyncio
async def test_endpoint_emits_exactly_one_warning_for_a_transient(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG, logger=tracking_ep.logger.name):
        await _call_endpoint(monkeypatch, _chain(_gateway_error(520)))

    records = [r for r in caplog.records if r.name == tracking_ep.logger.name]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_endpoint_emits_exactly_one_error_with_a_stack_for_a_real_bug(monkeypatch, caplog):
    """Negative control: the demotion must not swallow genuine failures."""
    with caplog.at_level(logging.DEBUG, logger=tracking_ep.logger.name):
        await _call_endpoint(monkeypatch, _chain(KeyError("ticker")))

    records = [r for r in caplog.records if r.name == tracking_ep.logger.name]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None  # stack preserved for diagnosis


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", [_gateway_error(520), KeyError("ticker")])
async def test_503_watchlist_unavailable_contract_holds_on_both_paths(monkeypatch, cause):
    """The level decision must not disturb the wire contract iOS depends on."""
    response = await _call_endpoint(monkeypatch, _chain(cause))

    assert response.status_code == 503
    import json

    body = json.loads(bytes(response.body))
    assert body["error_code"] == "WATCHLIST_UNAVAILABLE"
