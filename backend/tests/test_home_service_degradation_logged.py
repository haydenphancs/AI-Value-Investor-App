"""F7 (2026-09-11): the two Home table reads degrade LOUDLY.

`home_service._get_market_insight` / `_get_daily_briefings` read `market_insights` and
`daily_briefings` behind a bare `except Exception: pass`. Those tables had no service_role
GRANT (migration 169 adds it), so every request answered 42501, fell through to the SPY /
earnings-calendar fallback — and nothing, anywhere, said so. CLAUDE.md: never swallow
silently; log the exception TYPE and the operation.
"""

from __future__ import annotations

import logging

import pytest

import app.services.home_service as hs


class _Raising:
    """A Supabase stub whose query chain raises at execute(), like PostgREST's 42501."""

    def __init__(self, msg):
        self.msg = msg

    def table(self, _name):
        return self

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        raise Exception(self.msg)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    hs._cache.clear() if hasattr(hs, "_cache") else None
    monkeypatch.setattr(hs, "get_supabase", lambda: _Raising("permission denied for table market_insights"))


@pytest.mark.asyncio
async def test_market_insight_read_failure_is_logged_and_falls_back(monkeypatch, caplog):
    class _Quote:
        async def get_quote(self, symbol):
            return {"symbol": symbol, "price": 500.0, "changePercentage": 0.4, "change": 2.0}

    monkeypatch.setattr(hs, "price_source", lambda: _Quote())
    svc = hs.HomeService.__new__(hs.HomeService)
    with caplog.at_level(logging.WARNING, logger="app.services.home_service"):
        out = await svc._get_market_insight()
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("market_insights read failed" in m and "Exception" in m and "permission denied" in m for m in msgs), msgs
    assert out is None or hasattr(out, "headline")   # fallback path, never an exception


@pytest.mark.asyncio
async def test_daily_briefings_read_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(hs, "get_supabase", lambda: _Raising("permission denied for table daily_briefings"))

    class _FMP:
        async def get_earnings_calendar(self, *a, **k):
            return []

        def __getattr__(self, _name):
            async def _empty(*a, **k):
                return []
            return _empty

    svc = hs.HomeService.__new__(hs.HomeService)
    svc.fmp = _FMP()
    with caplog.at_level(logging.WARNING, logger="app.services.home_service"):
        try:
            out = await svc._get_daily_briefings()
        except Exception as e:  # the fallback may need more of the service than this stub has
            out = e
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("daily_briefings read failed" in m and "permission denied" in m for m in msgs), msgs
