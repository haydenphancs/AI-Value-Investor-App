"""`DELETE /watchlist` must survive its own error path.

`remove_from_watchlist` bound `ticker` INSIDE the `try`, after the first `.execute()`.
When that first call raised (a Supabase 520, a pooler timeout), the `except` block itself
referenced `ticker` → `UnboundLocalError` escaped the handler. Net effect: the ERROR log
line that names the ticker/user was never written, and the client got FastAPI's bare 500
instead of the handler's own message. The failure that most needed diagnosing was the one
that left no trace.

No network / Supabase — a fake whose `.execute()` raises on demand.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.watchlist as wl
from app.schemas.watchlist import RemoveFromWatchlistRequest


class _Boom:
    """A Supabase client whose first N `.execute()` calls raise."""

    def __init__(self, failures: int = 1, exc: Exception | None = None):
        self.failures = failures
        self.exc = exc or RuntimeError("supabase down")
        self.calls = 0

    def table(self, _name):
        return self

    def delete(self):
        return self

    def eq(self, _c, _v):
        return self

    def execute(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return type("R", (), {"data": []})()


def _run(request_ticker: str, sb) -> HTTPException:
    with pytest.raises(HTTPException) as info:
        asyncio.run(
            wl.remove_from_watchlist(
                RemoveFromWatchlistRequest(stock_id=request_ticker), {"id": "u1"}, sb
            )
        )
    return info.value


def test_a_failing_first_delete_raises_the_handlers_own_500_not_unboundlocalerror(
    monkeypatch, caplog
):
    """The regression: with `ticker` unbound, `pytest.raises(HTTPException)` would see an
    `UnboundLocalError` instead and this test would error rather than fail."""
    monkeypatch.setattr(wl, "invalidate_feed_cache", lambda _uid: None)
    monkeypatch.setattr(wl, "_delete_through_from_groups", lambda *_a: None)
    sb = _Boom(failures=1)

    with caplog.at_level(logging.ERROR, logger=wl.logger.name):
        exc = _run("ltc", sb)

    assert exc.status_code == 500
    assert "LTC" in str(exc.detail), exc.detail
    # The diagnostic line must survive the failure and carry the identifiers.
    records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert records, "the DB error was swallowed without an ERROR record"
    joined = " ".join(r.getMessage() for r in records)
    assert "LTC" in joined and "u1" in joined and "RuntimeError" in joined, joined


def test_a_failing_canonical_retry_still_names_the_raw_ticker(monkeypatch):
    """First delete matches nothing, the canonical retry raises: the message still
    names what the user asked to remove."""
    monkeypatch.setattr(wl, "invalidate_feed_cache", lambda _uid: None)
    monkeypatch.setattr(wl, "_delete_through_from_groups", lambda *_a: None)

    class _SecondBoom(_Boom):
        def execute(self):
            self.calls += 1
            if self.calls == 2:
                raise self.exc
            return type("R", (), {"data": []})()

    exc = _run("btc", _SecondBoom())
    assert exc.status_code == 500
    assert "BTC" in str(exc.detail)
