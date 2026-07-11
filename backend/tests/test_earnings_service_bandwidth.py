"""
Bandwidth-regression guard for EarningsService.

The Earnings Timeline used to build a ticker's earnings by querying the GLOBAL
`earnings-calendar` endpoint (ALL companies, up to 4000 records/call) across ~2 windows
per quarter + 5 forward windows, then discarding every row except this ticker. That single
path was ~99% of the app's FMP bandwidth (~4.3 GB observed on the FMP dashboard). It was
replaced by ONE per-symbol `/stable/earnings?symbol=X` call (get_earning_calendar_full),
which returns the same past + upcoming announcements with paired actual/estimate.

These tests pin that fix: building earnings makes exactly ONE per-symbol earnings call and
ZERO global `earnings-calendar` fan-out — and the upcoming date still resolves from the
per-symbol data (so next_earnings_date didn't regress to the analyst-estimate fallback).

No network — the FMP client is faked and records every call.
"""

import pytest

from app.services.earnings_service import EarningsService


class _FakeFMP:
    def __init__(self):
        self.calls = []              # public method calls
        self.make_request_calls = []  # raw _make_request endpoints (a global fan-out would show here)

    async def get_income_statement(self, ticker, period="annual", limit=10):
        self.calls.append(("get_income_statement", period, limit))
        return [
            {"date": "2025-03-31", "period": "Q1", "acceptedDate": "2025-04-25",
             "fiscalYear": 2025, "eps": 1.0, "revenue": 1000},
            {"date": "2024-12-31", "period": "Q4", "acceptedDate": "2025-01-30",
             "fiscalYear": 2024, "eps": 0.9, "revenue": 950},
        ]

    async def get_analyst_estimates(self, ticker, period="annual", limit=10):
        self.calls.append(("get_analyst_estimates", period, limit))
        # A future estimate that WOULD be the fallback if the per-symbol upcoming row
        # weren't found — the test asserts we DON'T fall back to this quarter-end date.
        return [{"date": "2099-06-30", "estimatedEpsAvg": 1.1, "estimatedRevenueAvg": 1100}]

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        self.calls.append(("get_historical_prices", ticker, from_date, to_date))
        return [{"date": "2025-03-31", "close": 150.0}, {"date": "2024-12-31", "close": 140.0}]

    async def get_earning_calendar_full(self, ticker):
        self.calls.append(("get_earning_calendar_full", ticker))
        return [
            # Reported quarters (epsActual present) with paired non-GAAP actual/estimate.
            {"date": "2025-04-24", "epsActual": 1.05, "eps": 1.05, "epsEstimated": 1.0,
             "revenueActual": 1010, "revenue": 1010, "revenueEstimated": 1000, "time": "amc"},
            {"date": "2025-01-28", "epsActual": 0.92, "eps": 0.92, "epsEstimated": 0.9,
             "revenueActual": 955, "revenue": 955, "revenueEstimated": 950, "time": "bmo"},
            # UPCOMING (epsActual None) — the real report date the removed forward global
            # fetch used to supply. Proves the per-symbol call alone covers next_earnings_date.
            {"date": "2099-07-29", "epsActual": None, "epsEstimated": 1.1,
             "revenueActual": None, "revenueEstimated": 1100, "time": "amc"},
        ]

    async def _make_request(self, endpoint, params=None):
        # No earnings_service path should reach the raw client anymore; a global
        # `earnings-calendar` fetch reappearing here would be the exact regression.
        self.make_request_calls.append(endpoint)
        return []


@pytest.mark.asyncio
async def test_earnings_uses_one_per_symbol_call_not_global_fanout():
    svc = EarningsService()
    svc.fmp = _FakeFMP()  # type: ignore[assignment]
    resp = await svc._build_earnings("AAPL")

    fake = svc.fmp
    per_symbol = [c for c in fake.calls if c[0] == "get_earning_calendar_full"]
    assert len(per_symbol) == 1, "should make exactly ONE per-symbol earnings call"
    assert fake.make_request_calls == [], (
        f"no raw FMP calls expected — a global earnings-calendar fan-out regressed: "
        f"{fake.make_request_calls}"
    )


@pytest.mark.asyncio
async def test_next_earnings_date_resolves_from_per_symbol_upcoming_row():
    """The upcoming per-symbol row (epsActual=None) must drive a CONFIRMED next date —
    not the analyst-estimate quarter-end fallback (2099-06-30)."""
    svc = EarningsService()
    svc.fmp = _FakeFMP()  # type: ignore[assignment]
    resp = await svc._build_earnings("AAPL")

    assert resp.next_earnings_date is not None
    assert resp.next_earnings_date.date == "2099-07-29"   # the real report date, not 06-30
    assert resp.next_earnings_date.is_confirmed is True


def test_global_calendar_fanout_helpers_are_gone():
    """The bandwidth-heavy per-quarter/forward global fetch helpers must not come back."""
    assert not hasattr(EarningsService, "_fetch_earnings_calendar_for_symbol")
    assert not hasattr(EarningsService, "_fetch_earnings_calendar_window")
