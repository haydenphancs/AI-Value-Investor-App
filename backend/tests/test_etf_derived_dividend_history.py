"""ETF dividend history from DERIVED dividend events (owner decision 2026-11-14).

`/dividends` is outside the FMP licence, so the per-payment history used to be permanently
empty — the card showed "— / — / —" and the sheet opened blank. It is now built from the
entitled `/dividend-adjusted` vs `/full` series: exact ex-dates, amounts ESTIMATED from the
step size and printed with "≈", pay date "—".

The same events drive a yield cross-check. FMP's `profile.lastDividend` was not restated
for SOXX's 3:1 split (2026-11-05): the card showed 0.57% against iShares' 0.27% and said the
fund "pays you 1.7x more in dividends than it charges in fees" when the fee was HIGHER.
The derived trailing total replaces FMP's only when the two disagree by > 25% AND the
derived set is complete for its cadence — every other fund renders exactly as before.

Real numbers below are the measured SOXX / SPY values (2026-11-14).
"""
from __future__ import annotations

import math

import pytest

from app.services import corporate_actions_service as CA
from app.services import etf_service as E
from app.services.corporate_actions_service import (
    AdjustmentEvent,
    derive_adjustment_events,
    estimated_dividend_amount,
)


def _ev(date, amount=None, prev_close=100.0, observed=None):
    """A dividend event whose step implies ``amount`` on ``prev_close``."""
    if observed is None:
        observed = 1.0 - (amount / prev_close)
    return AdjustmentEvent(date=date, observed=observed, prev_close=prev_close)


# ── The amount estimate ──────────────────────────────────────────────────────────────────

def test_the_amount_is_recovered_from_a_known_dividend():
    """$0.26 on a $278 close (AAPL-shaped): divadj 277.74 before, equal after."""
    events = derive_adjustment_events(
        [{"date": "2026-02-06", "close": 278.0}, {"date": "2026-02-09", "close": 278.0}],
        [{"date": "2026-02-06", "adjClose": 277.74}, {"date": "2026-02-09", "adjClose": 278.0}],
        eps=CA._DIVIDEND_FACTOR_EPS, classify=False,
    )
    assert len(events) == 1
    assert events[0].prev_close == 278.0
    assert estimated_dividend_amount(events[0]) == pytest.approx(0.26, abs=1e-9)


def test_the_amount_is_on_todays_share_basis_after_a_split():
    """`/full` is split-adjusted, so a pre-split payment reads in post-split shares —
    the convention of every published split-adjusted list (SOXX's $0.18025, Sep 2025)."""
    # Pre-split raw close $258, 3:1 later -> the /full close is $86.
    events = derive_adjustment_events(
        [{"date": "2025-09-15", "close": 86.0}, {"date": "2025-09-16", "close": 86.0}],
        [{"date": "2025-09-15", "adjClose": 85.82}, {"date": "2025-09-16", "adjClose": 86.0}],
        eps=CA._DIVIDEND_FACTOR_EPS, classify=False,
    )
    assert estimated_dividend_amount(events[0]) == pytest.approx(0.18, abs=1e-9)


@pytest.mark.parametrize("prev_close", [None, 0.0, -5.0, float("nan"), float("inf"), "abc"])
def test_an_unusable_prior_close_is_no_estimate(prev_close):
    assert estimated_dividend_amount(AdjustmentEvent("2026-01-02", 0.999, prev_close=prev_close)) is None


@pytest.mark.parametrize("observed", [1.0, 1.002, 0.4, float("nan"), 0.0])
def test_a_step_that_is_not_a_plausible_payment_is_no_estimate(observed):
    """`observed >= 1` is not a payment; >= 50% of the price is a corrupt bar, not income."""
    assert estimated_dividend_amount(AdjustmentEvent("2026-01-02", observed, prev_close=100.0)) is None


def test_a_cached_event_without_a_prior_close_is_unknown_not_guessed():
    """The Supabase tier stores no `prev_close`; a stored event reads back as None."""
    assert estimated_dividend_amount(AdjustmentEvent("2026-01-02", 0.998)) is None


def test_prev_close_does_not_change_event_equality():
    """Stored rows (no prev_close) must still equal the derived ones they cache."""
    assert AdjustmentEvent("2026-01-02", 0.998) == AdjustmentEvent("2026-01-02", 0.998, prev_close=5.0)


# ── The rows ─────────────────────────────────────────────────────────────────────────────

def test_rows_are_newest_first_with_approximate_amounts_and_no_pay_date():
    rows = E._derived_dividend_rows([
        _ev("2026-06-15", 0.09424),
        _ev("2026-09-15", 0.10835),
        _ev("2026-03-17", 0.06931),
    ])
    assert [r.ex_dividend_date for r in rows] == ["Sep 15, 2026", "Jun 15, 2026", "Mar 17, 2026"]
    assert [r.dividend_per_share for r in rows] == ["≈$0.11", "≈$0.09", "≈$0.07"]
    assert all(r.pay_date == "—" for r in rows)


def test_every_amount_is_marked_approximate():
    """An estimate is never printed as an exact figure (+0.9% .. +2.7% measured on SOXX)."""
    rows = E._derived_dividend_rows([_ev(f"2026-{m:02d}-01", 1.2345) for m in range(1, 13)])
    assert rows and all(r.dividend_per_share.startswith("≈$") for r in rows)


@pytest.mark.parametrize("amount, shown", [
    (0.004, "—"),          # rounds below a cent — never "≈$0.00"
    (0.005, "≈$0.01"),
    (12.3456, "≈$12.35"),
    (1234.5, "≈$1,234.50"),
    (None, "—"),
    (float("nan"), "—"),
])
def test_amount_formatting(amount, shown):
    assert E._format_estimated_amount(amount) == shown


def test_a_row_without_an_estimate_keeps_its_exact_date():
    rows = E._derived_dividend_rows([AdjustmentEvent("2026-09-15", 0.999)])
    assert rows[0].ex_dividend_date == "Sep 15, 2026"
    assert rows[0].dividend_per_share == "—"


def test_the_detail_cap_and_duplicate_or_malformed_dates():
    events = [_ev(f"2024-{m:02d}-01", 0.3) for m in range(1, 13)] + \
             [_ev(f"2025-{m:02d}-01", 0.3) for m in range(1, 13)]
    events += [_ev("2025-12-01", 0.3), AdjustmentEvent("not-a-date", 0.99, prev_close=10.0),
               AdjustmentEvent(None, 0.99, prev_close=10.0)]
    rows = E._derived_dividend_rows(events, E._DETAIL_DIVIDEND_ROWS)
    assert len(rows) == 20
    assert rows[0].ex_dividend_date == "Dec 01, 2025"
    assert len({r.ex_dividend_date for r in rows}) == 20, "a duplicate ex-date made two rows"
    full = E._derived_dividend_rows(events)
    assert len(full) == 24


def test_no_events_is_no_rows():
    assert E._derived_dividend_rows([]) == []
    assert E._derived_dividend_rows(None) == []


# ── The yield cross-check ────────────────────────────────────────────────────────────────

TODAY = "2026-11-14"
SOXX_EVENTS = [  # measured: derived amounts on today's (post-split) share basis
    _ev("2026-09-15", 0.1101, 165.80),
    _ev("2026-06-15", 0.0968, 198.75),
    _ev("2026-03-17", 0.0711, 112.61),
    _ev("2025-12-16", 0.1494, 99.34),
    _ev("2025-09-16", 0.1818, 85.93),   # outside the trailing year
]


def _reconcile(last_div, events, frequency="Quarterly", price=149.30, today=TODAY):
    profile = {"lastDividend": last_div}
    y, known = E.dividend_yield_from_profile(profile, price)
    return E.reconcile_dividend_yield("TEST", profile, price, y, known, events, frequency, today)


def test_soxx_stale_split_figure_is_replaced_by_the_derived_total():
    y, known = _reconcile(0.8468, SOXX_EVENTS)
    assert known is True
    assert y == pytest.approx(0.29, abs=0.005), "0.4274 / 149.30 — iShares states 0.27%"


def test_an_agreeing_fund_keeps_fmps_figure_byte_for_byte():
    """SPY: FMP 7.58272 vs derived 7.5888."""
    spy = [_ev(d, a, 700.0) for d, a in
           [("2026-09-18", 1.8947), ("2026-06-18", 1.9140), ("2026-03-20", 1.7972), ("2025-12-19", 2.0034)]]
    assert _reconcile(7.58272, spy, price=750.0) == E.dividend_yield_from_profile({"lastDividend": 7.58272}, 750.0)


def test_an_incomplete_derived_set_never_pulls_a_correct_yield_down(caplog):
    """A payer partly below the detection floor: 2 of 4 quarters seen. FMP stays."""
    y, known = _reconcile(1.0, [_ev("2026-09-15", 0.25), _ev("2026-03-15", 0.25)])
    assert (y, known) == E.dividend_yield_from_profile({"lastDividend": 1.0}, 149.30)
    assert "keeping FMP's yield" in caplog.text


def test_a_missing_amount_in_the_window_keeps_fmp():
    events = list(SOXX_EVENTS)
    events[1] = AdjustmentEvent("2026-06-15", 0.9995)   # no prev_close -> no estimate
    assert _reconcile(0.8468, events) == E.dividend_yield_from_profile({"lastDividend": 0.8468}, 149.30)


@pytest.mark.parametrize("frequency", ["—", "", "Weekly", None])
def test_an_unknown_cadence_keeps_fmp(frequency):
    assert _reconcile(0.8468, SOXX_EVENTS, frequency=frequency) == \
        E.dividend_yield_from_profile({"lastDividend": 0.8468}, 149.30)


def test_fmps_zero_is_never_overridden():
    """ARKK / GLD / IBIT: FMP says 0 and nothing must manufacture a yield."""
    assert _reconcile(0, SOXX_EVENTS) == (0.0, True)


def test_an_unknown_fmp_yield_stays_unknown():
    assert _reconcile(None, SOXX_EVENTS) == (0.0, False)
    assert _reconcile(0.8468, SOXX_EVENTS, price=None) == (0.0, False)


def test_a_derivation_that_found_nothing_keeps_fmp():
    """SPLG: derived 0 (detection failed) against a correct FMP 0.90785."""
    assert _reconcile(0.90785, [], price=88.0) == E.dividend_yield_from_profile({"lastDividend": 0.90785}, 88.0)


def test_the_window_is_the_trailing_year_only():
    """Five events, the oldest 424 days back: only four count."""
    y, _ = _reconcile(0.8468, SOXX_EVENTS)
    assert y == pytest.approx(round((0.1101 + 0.0968 + 0.0711 + 0.1494) / 149.30 * 100, 2))


def test_the_boundary_is_more_than_25_percent():
    """Estimates carry float noise, so the pin is just inside vs just outside the line."""
    four = [_ev(d, 0.25) for d in ("2026-10-01", "2026-07-01", "2026-04-01", "2026-01-02")]
    keep = E.dividend_yield_from_profile({"lastDividend": 1.24}, 100.0)
    assert _reconcile(1.24, four, price=100.0) == keep         # 24% apart: FMP stays
    assert _reconcile(1.26, four, price=100.0) == (1.0, True)  # 26% apart: derived wins


def test_a_reverse_stale_figure_too_low_is_corrected_too():
    four = [_ev(d, 0.25) for d in ("2026-10-01", "2026-07-01", "2026-04-01", "2026-01-02")]
    assert _reconcile(0.3, four, price=100.0) == (1.0, True)


def test_a_monthly_payer_needs_all_twelve_payments():
    months = [_ev(f"2026-{m:02d}-02", 0.3) for m in range(1, 12)] + [_ev("2025-12-02", 0.3)]
    assert _reconcile(9.0, months, frequency="Monthly", price=100.0) == (3.6, True)
    for short in (months[:11], months[:9]):   # DIA-like: a step under the floor
        assert _reconcile(9.0, short, frequency="Monthly", price=100.0) == \
            E.dividend_yield_from_profile({"lastDividend": 9.0}, 100.0)


def test_an_annual_payer_whose_date_drifts_earlier_is_not_double_counted():
    """REVIEW 2026-11-14. 2025-11-17 and 2026-11-10 both sit in (2025-11-14, 2026-11-14]:
    a band of (1, 2) summed both, read $4 against FMP's CORRECT $2 and showed 4.00%."""
    annual = [_ev(d, 2.0) for d in ("2026-11-10", "2025-11-17", "2024-11-20", "2023-11-22")]
    assert _reconcile(2.0, annual, frequency="Annually", price=100.0) == \
        E.dividend_yield_from_profile({"lastDividend": 2.0}, 100.0)
    # ...and a genuinely stale annual figure is still corrected, from ONE payment.
    assert _reconcile(6.0, annual, frequency="Annually", price=100.0) == (2.0, True)


def test_a_semi_annual_payer_with_three_in_the_window_is_not_one_and_a_half_times():
    semi = [_ev(d, 1.0) for d in ("2026-11-10", "2026-05-12", "2025-11-17", "2025-05-15")]
    assert _reconcile(2.0, semi, frequency="Semi-Annually", price=100.0) == \
        E.dividend_yield_from_profile({"lastDividend": 2.0}, 100.0)
    assert _reconcile(4.0, semi, frequency="Semi-Annually", price=100.0) == (2.0, True)


def test_a_stale_quarterly_figure_with_five_in_the_window_uses_the_newest_four():
    five = [_ev(d, 0.5) for d in ("2026-11-10", "2026-08-12", "2026-05-13", "2026-02-11", "2025-11-17")]
    assert _reconcile(4.0, five, price=100.0) == (2.0, True)


def test_the_result_is_always_finite():
    y, _ = _reconcile(0.8468, SOXX_EVENTS)
    assert math.isfinite(y)


# ── End to end through the real detail build and the endpoint ────────────────────────────

class _Actions:
    def __init__(self, events=None, exc=None):
        self.events, self.exc, self.calls = events or [], exc, 0

    async def get_dividend_events(self, symbol, from_date=None, to_date=None):
        self.calls += 1
        if self.exc:
            raise self.exc
        return list(self.events)

    async def get_ex_dividend_dates(self, *a, **k):
        return [e.date for e in self.events]


def _svc(last_div, actions, price=149.30):
    E._cache.clear()
    E._inflight.clear()
    svc = E.ETFService.__new__(E.ETFService)

    async def _quote(symbol): return {"symbol": symbol, "price": price, "previousClose": price}
    async def _fund(symbol):
        return {"profile": {"companyName": "Test ETF", "lastDividend": last_div},
                "etf_info": {"expenseRatio": 0.33}, "dividends": []}
    async def _chart(symbol, chart_range, interval, fast_only=False): return []
    async def _related(symbol): return []
    async def _hist(symbol): return [{"date": "2026-11-12", "close": price}, {"date": "2026-11-13", "close": price}]
    async def _derived(symbol, index_tracked=""):
        return {"performance_periods": [], "benchmark_summary": None, "sma_50": None}
    async def _hook(**kw): return kw.get("fallback") or "hook"

    svc._get_quote, svc._get_fundamentals, svc._get_chart = _quote, _fund, _chart
    svc._get_related, svc._get_history, svc._get_derived = _related, _hist, _derived
    svc._generate_hook_text = _hook
    svc.corporate_actions = actions
    svc._check_snapshot_cache = lambda *a, **k: None
    svc.writes = []
    svc._upsert_snapshot_cache = lambda *a, **k: svc.writes.append(a)
    return svc


@pytest.mark.asyncio
async def test_detail_build_fills_the_history_and_corrects_the_soxx_yield(monkeypatch):
    monkeypatch.setattr(E, "_pay_frequency_window", lambda today=None: ("2022-10-15", TODAY))
    monkeypatch.setattr(E, "reconcile_dividend_yield",
                        lambda *a, **k: _real_reconcile(*a[:7], today=TODAY))
    actions = _Actions(SOXX_EVENTS)
    resp = await _svc(0.8468, actions)._build_etf_detail("SOXX", "3M", None)
    ny = resp.net_yield
    assert actions.calls == 1, "one derivation shared by history, frequency and yield"
    assert ny.pay_frequency == "Quarterly"
    assert ny.dividend_yield == pytest.approx(0.29, abs=0.005)
    assert "higher than" in ny.verdict, ny.verdict
    assert ny.last_dividend_payment.ex_dividend_date == "Sep 15, 2026"
    assert ny.last_dividend_payment.dividend_per_share == "≈$0.11"
    assert ny.last_dividend_payment.pay_date == "—"
    assert len(ny.dividend_history) == 5


_real_reconcile = E.reconcile_dividend_yield


@pytest.mark.asyncio
async def test_a_failed_derivation_is_exactly_the_old_card(monkeypatch, caplog):
    actions = _Actions(exc=RuntimeError("boom"))
    resp = await _svc(0.8468, actions)._build_etf_detail("SOXX", "3M", None)
    ny = resp.net_yield
    assert ny.dividend_history == []
    assert ny.last_dividend_payment.dividend_per_share == "—"
    assert ny.pay_frequency == "—"
    assert ny.dividend_yield == pytest.approx(0.57)   # FMP's figure, unchanged
    assert "dividend event derivation failed for SOXX" in caplog.text


@pytest.mark.asyncio
async def test_the_endpoint_returns_every_derived_row_and_never_pins_them():
    """REVIEW 2026-11-14: a 1 h / 24 h copy let this screen keep yesterday's rows while the
    detail card had re-derived a new ex-date. The derivation is cached underneath; the rows
    are rebuilt on every call, so the two screens always agree."""
    events = [_ev(f"2026-{m:02d}-02", 0.24, 74.0) for m in range(1, 11)] + SOXX_EVENTS[1:]
    actions = _Actions(events)
    svc = _svc(2.9, actions)
    resp = await svc.get_dividend_history("bnd")
    assert resp.symbol == "BND"
    assert resp.total_dividends == len(resp.dividends) == 14
    assert all(d.pay_date == "—" for d in resp.dividends)
    assert resp.dividends[0].ex_dividend_date == "Oct 02, 2026"
    assert svc.writes == [] and E._cache_get("etf_dividend_history_BND", 3600) is None
    # A new ex-date appears: the endpoint shows it on the very next call.
    actions.events = [_ev("2026-11-03", 0.24, 74.0)] + events
    again = await svc.get_dividend_history("BND")
    assert again.dividends[0].ex_dividend_date == "Nov 03, 2026"


@pytest.mark.asyncio
@pytest.mark.parametrize("dates", [["2023-09-08"], ["2023-09-08", "2025-02-03"]])
async def test_a_lone_step_with_no_cadence_is_not_a_payment(dates):
    """ARKK: FMP's series shifts on 2023-09-08 (≈$0.36); no published list has it, and the
    fund has paid nothing since Dec 2021. One or two unpatterned steps show nothing."""
    svc = _svc(0, _Actions([_ev(d, 0.36, 43.68) for d in dates]))
    hist = await svc.get_dividend_history("ARKK")
    assert hist.dividends == [] and hist.pay_frequency == "—"
    resp = await svc._build_etf_detail("ARKK", "3M", None)
    assert resp.net_yield.dividend_history == []
    assert resp.net_yield.last_dividend_payment.ex_dividend_date == "—"


@pytest.mark.asyncio
async def test_three_unpatterned_steps_are_still_shown():
    svc = _svc(0, _Actions([_ev(d, 0.5, 50.0) for d in ("2026-06-01", "2025-12-20", "2024-03-01")]))
    hist = await svc.get_dividend_history("IRR")
    assert hist.total_dividends == 3


# ── Which steps can be payments ───────────────────────────────────────────────────────────

def test_a_factor_rise_is_never_a_payment():
    assert CA.dividend_steps([AdjustmentEvent("2026-03-04", 1.003, prev_close=100.0)]) == []


def test_a_one_bar_glitch_that_reverts_is_dropped_both_ways():
    """REVIEW 2026-11-14: a dividend leg at 97 for one session made a 1.0309 rise and a 0.97
    drop — two rows, one a fake '≈$3.00'."""
    events = derive_adjustment_events(
        [{"date": f"2026-03-0{d}", "close": 100.0} for d in (2, 3, 4, 5, 6)],
        [{"date": f"2026-03-0{d}", "adjClose": 97.0 if d == 4 else 100.0} for d in (2, 3, 4, 5, 6)],
        eps=CA._DIVIDEND_FACTOR_EPS, classify=False,
    )
    assert len(events) == 2, "test setup: the glitch pair must exist"
    assert CA.dividend_steps(events) == []
    # The inverse glitch (a drop then a rise) is dropped too.
    events = derive_adjustment_events(
        [{"date": f"2026-03-0{d}", "close": 100.0} for d in (2, 3, 4, 5, 6)],
        [{"date": f"2026-03-0{d}", "adjClose": 99.0 if d in (2, 3) else 100.0} for d in (2, 3, 4, 5, 6)]
        + [],
        eps=CA._DIVIDEND_FACTOR_EPS, classify=False,
    )
    real = CA.dividend_steps(events)
    assert [e.date for e in real] == ["2026-03-04"], "a real one-way step survives"


def test_a_real_payment_next_to_a_glitch_survives():
    events = [
        AdjustmentEvent("2026-01-05", 0.997, prev_close=100.0),   # real
        AdjustmentEvent("2026-02-02", 0.98, prev_close=100.0),    # glitch down
        AdjustmentEvent("2026-02-03", 1 / 0.98, prev_close=98.0), # ...reverted
    ]
    assert [e.date for e in CA.dividend_steps(events)] == ["2026-01-05"]


@pytest.mark.asyncio
@pytest.mark.parametrize("actions", [_Actions([]), _Actions(exc=RuntimeError("down"))])
async def test_the_endpoint_never_caches_an_empty_derivation(actions):
    svc = _svc(0, actions)
    resp = await svc.get_dividend_history("ARKK")
    assert resp.total_dividends == 0 and resp.dividends == []
    assert resp.pay_frequency == "—"
    assert svc.writes == [], "an empty/failed derivation must not be pinned for 24 h"
    assert E._cache_get("etf_dividend_history_ARKK", 3600) is None
