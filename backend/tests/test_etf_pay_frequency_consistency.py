"""A pay-frequency label needs evidence a partial detection cannot fake.

Ex-dividend dates are now DERIVED from the price series with a detection floor
(`_DIVIDEND_FACTOR_EPS`). A low-yield quarterly fund can surface only two of its four
dates — evenly spaced, ~182 days apart — and `_infer_pay_frequency` rendered a confident
"Semi-Annually"; Q1 + Q4 alone read "Annually"; a December quarterly plus a December
capital-gain distribution read "Monthly". Two agreeing gaps cannot distinguish a partial
detection from a real cadence, so the label now needs at least three gaps of the same
size, and degrades to "—" otherwise.

The flip side (caught by the regression review of that fix): three agreeing gaps need
FOUR dates, and the derivation's default open window is 400 days — so a real
semi-annual payer (≤3 dates) or annual payer (≤2 dates) could never earn its label at
all. The two ETF call sites now derive over `_pay_frequency_window()` (four years), which
gives an annual payer 4 dates / 3 gaps.
"""
from __future__ import annotations

import pytest

import inspect
import re
from datetime import date

from app.services import etf_service
from app.services.etf_service import ETFService, _PAY_FREQUENCY_LOOKBACK_DAYS, _pay_frequency_window


def _dates(*ds):
    return [{"date": d} for d in ds]


@pytest.fixture
def svc():
    return object.__new__(ETFService)


def test_two_of_four_quarterly_dates_do_not_read_semi_annually(svc):
    assert svc._infer_pay_frequency(_dates("2026-09-15", "2026-03-16", "2025-09-15")) == "—"


def test_q1_and_q4_alone_do_not_read_annually(svc):
    assert svc._infer_pay_frequency(_dates("2026-12-15", "2026-03-16")) == "—"


def test_a_december_pair_does_not_read_monthly(svc):
    assert svc._infer_pay_frequency(_dates("2026-12-20", "2026-12-18")) == "—"


def test_a_full_quarterly_year_reads_quarterly(svc):
    assert svc._infer_pay_frequency(
        _dates("2026-09-15", "2026-06-15", "2026-03-16", "2025-12-15", "2025-09-15")
    ) == "Quarterly"


def test_a_monthly_payer_reads_monthly(svc):
    ds = [f"2026-{m:02d}-15" for m in range(9, 0, -1)]
    assert svc._infer_pay_frequency(_dates(*ds)) == "Monthly"


def test_irregular_gaps_degrade_rather_than_average(svc):
    # 30, 90, 180 days: an average of 100 would say "Quarterly" for something that is not.
    assert svc._infer_pay_frequency(_dates("2026-09-15", "2026-08-16", "2026-05-18", "2025-11-19")) == "—"


@pytest.mark.parametrize("rows", [[], [{"date": "2026-09-15"}], [{"date": "bad"}, {"date": None}]])
def test_thin_or_malformed_input_is_a_dash(svc, rows):
    assert svc._infer_pay_frequency(rows) == "—"


# ── the window is wide enough for the cadences the label can name ─────────────

def test_a_real_semi_annual_payer_reads_semi_annually(svc):
    """Four dates ~183 days apart — what four years of derivation yields."""
    assert svc._infer_pay_frequency(
        _dates("2026-06-15", "2025-12-15", "2025-06-16", "2024-12-16")
    ) == "Semi-Annually"


def test_a_real_annual_payer_reads_annually(svc):
    assert svc._infer_pay_frequency(
        _dates("2026-03-16", "2025-03-17", "2024-03-15", "2023-03-15")
    ) == "Annually"


def test_the_frequency_window_spans_at_least_four_years():
    begin, end = _pay_frequency_window("2026-09-11")
    assert end == "2026-09-11"
    assert (date.fromisoformat(end) - date.fromisoformat(begin)).days >= 4 * 365
    assert _PAY_FREQUENCY_LOOKBACK_DAYS >= 4 * 365


def test_both_etf_call_sites_derive_over_the_wide_window():
    """Anti-vacuity for the constant: the two `get_ex_dividend_dates` calls in
    etf_service must use `_pay_frequency_window()`, not the 400-day default."""
    src = inspect.getsource(etf_service)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    calls = [m.start() for m in re.finditer(r"get_ex_dividend_dates\(", code)]
    assert len(calls) >= 2, "the two ETF call sites moved — re-point this test"
    for i in calls:
        window = code[i:i + 200]
        assert "_pay_frequency_window()" in window, window
        assert "window_for_range(" not in window
