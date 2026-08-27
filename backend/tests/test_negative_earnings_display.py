"""A negative number is DATA. Rendering it as "—" tells the user we have nothing.

TestFlight, build 1.0 (6): *"Data is missing? Double check for me"* — a photo of MRNA's Key
Statistics with four rows circled: `P/E (TTM) —`, `P/E (FWD) —`, `EPS (TTM) —`, `Dividends —`.

The tester was right, and it was not Moderna-specific. Measured against live FMP at the time:

    MRNA TTM diluted EPS      -7.98   (four real quarters: -1.96, -3.40, -2.11, -0.51)
    MRNA priceToEarningsRatioTTM      -18.75      MRNA FY2027 analyst epsAvg   -4.90
    MRNA lastDividend           0     (it genuinely pays none)

    EPS hidden for 8 of a 10-ticker basket — every loss-maker:
      MRNA -7.98  PLUG -1.36  RIVN -2.58  LCID -13.69  NIO -3.97
      SNAP -0.19  RIOT -3.72  BYND -52.88   |   UBER 4.55 ok   AAPL 8.73 ok

`PLUG` sits on that same tester's Home screen under Holdings.

One root cause: **code that treats "negative" as "absent"**. The distinction already existed in
this codebase — `_fmt_pfcf`'s docstring spells it out ("different signal from data missing") —
and MRNA's own Price card rendered `P/FCF = "Neg."` two rows below a `P/E = "—"`. It was simply
never generalised.

This file pins the three-way rule and, just as importantly, pins that a PROFITABLE company's
output did not move — that is the regression a copy fix like this is most likely to cause.
"""

import pytest

from app.services.valuation_snapshot_service import _fmt_ratio, _fmt_pfcf


# ── 1. The formatter: three outcomes, not two ────────────────────────


@pytest.mark.parametrize("val,expected", [
    (None, "—"),        # genuinely unknown — the upstream gave us nothing
    (-18.75, "Neg."),   # MRNA's real P/E: known, and undefined as a multiple
    (-0.01, "Neg."),    # just below zero is still negative
    (-52.88, "Neg."),
    (0.0, "—"),         # FMP uses 0 for absent on these fields
    (26.4, "26.40"),    # ordinary
    (0.004, "0.00"),    # rounds to zero but is a real positive multiple
    (1234.5, "1234.50"),
])
def test_fmt_ratio_separates_unknown_from_undefined(val, expected):
    assert _fmt_ratio(val) == expected


def test_the_three_outcomes_are_actually_distinct():
    """Anti-vacuity. A regression that made everything "Neg." — or everything "—" —
    would satisfy most of the table above one row at a time."""
    assert len({_fmt_ratio(None), _fmt_ratio(-1.0), _fmt_ratio(1.0)}) == 3


def test_pfcf_still_recovers_the_sign_when_the_ratio_is_absent():
    """FMP leaves `priceToFreeCashFlowsRatioTTM` null for MRNA, so `_fmt_pfcf` recovers the
    sign from `freeCashFlowYield` / the cash-flow statement. That path must survive the
    delegation to `_fmt_ratio`."""
    assert _fmt_pfcf(None, {"freeCashFlowYield": -0.03}) == "Neg."
    assert _fmt_pfcf(None, {}, {"freeCashFlow": -1_200_000_000}) == "Neg."
    assert _fmt_pfcf(None, {}, {"freeCashFlow": 5_000_000}) == "—"
    assert _fmt_pfcf(None, {}) == "—"
    # And when the ratio IS present it answers directly.
    assert _fmt_pfcf(18.2, {}) == "18.20"
    assert _fmt_pfcf(-4.0, {}) == "Neg."


# ── 2. End-to-end through the real key-statistics builder ────────────


def _quarters(eps_values):
    """Four quarterly income statements carrying these diluted EPS figures."""
    return [{"epsDiluted": v, "date": f"2026-0{i+1}-30"} for i, v in enumerate(eps_values)]


def _build(*, eps_quarters, price, profile=None, analyst_est=None, key_metrics=None):
    """Drive the real builder with inline data — no network, no Supabase.

    `_build_key_statistics` returns `(flat_list, grouped)`; the screen renders the grouped
    form and `_stats` flattens it back to label -> value.
    """
    from app.services.stock_overview_service import StockOverviewService

    svc = StockOverviewService.__new__(StockOverviewService)
    return svc._build_key_statistics(
        {"price": price},                       # quote
        {} if profile is None else profile,     # profile
        key_metrics or [],                      # key_metrics
        analyst_est or [],                      # analyst_est
        price,
        income_quarterly=eps_quarters,
    )


def _stats(built):
    """label -> value, from the GROUPED statistics the detail screen renders."""
    _flat, groups = built
    return {item.label: item.value for g in groups for item in g.statistics}


# The measured, real TTM figures behind the report.
_LOSS_MAKERS = [
    ("MRNA", [-1.96, -3.40, -2.11, -0.51], -7.98),
    ("BYND", [-13.22, -13.22, -13.22, -13.22], -52.88),
    ("SNAP", [-0.05, -0.05, -0.05, -0.04], -0.19),
    ("PLUG", [-0.34, -0.34, -0.34, -0.34], -1.36),
]


@pytest.mark.parametrize("ticker,quarters,ttm", _LOSS_MAKERS,
                         ids=[t for t, _, _ in _LOSS_MAKERS])
def test_a_loss_makers_eps_is_shown_not_swallowed(ticker, quarters, ttm):
    """THE bug. The TTM sum was computed correctly and then discarded for being negative."""
    stats = _stats(_build(eps_quarters=_quarters(quarters), price=147.63))
    assert stats["EPS (TTM)"] == f"{ttm:.2f}", (
        f"{ticker}: EPS (TTM) is {stats['EPS (TTM)']!r} but we have {ttm} — a negative EPS "
        f"is the most important number on a loss-maker's screen, not a missing value"
    )


def test_a_loss_makers_pe_reads_undefined_not_missing():
    stats = _stats(_build(eps_quarters=_quarters([-1.96, -3.40, -2.11, -0.51]), price=147.63))
    assert stats["P/E (TTM)"] == "Neg."
    assert stats["EPS (TTM)"] == "-7.98"


def test_forward_pe_reads_undefined_when_analysts_forecast_a_loss():
    """MRNA's nearest future fiscal year (2027) has epsAvg -4.90 — a real estimate."""
    stats = _stats(_build(
        eps_quarters=_quarters([-1.96, -3.40, -2.11, -0.51]),
        price=147.63,
        analyst_est=[{"date": "2099-12-31", "epsAvg": -4.90403}],
    ))
    assert stats["P/E (FWD)"] == "Neg."


def test_break_even_eps_of_exactly_zero_is_rendered():
    """The falsy-zero trap. `if not eps` sent a real 0.00 down the fallback path to be
    overwritten; `if eps and eps > 0` then rendered it as missing."""
    stats = _stats(_build(eps_quarters=_quarters([0.0, 0.0, 0.0, 0.0]), price=50.0))
    assert stats["EPS (TTM)"] == "0.00"
    # P/E is genuinely undefined at zero earnings — and that is "unknown", not "negative".
    assert stats["P/E (TTM)"] == "—"


def test_missing_quarters_still_read_as_missing():
    """The fix must not turn "we have nothing" into a number. Three quarters is not a TTM."""
    stats = _stats(_build(eps_quarters=_quarters([-1.0, -1.0, -1.0])[:3], price=50.0))
    assert stats["EPS (TTM)"] == "—"
    assert stats["P/E (TTM)"] == "—"


def test_a_non_finite_eps_is_rejected():
    """FMP emits bare NaN/Infinity JSON tokens and `json.loads` parses them; NaN is TRUTHY,
    so it sails past a bare `or 0` and would land in a formatted string."""
    stats = _stats(_build(
        eps_quarters=_quarters([float("nan"), 1.0, 1.0, 1.0]), price=50.0))
    assert stats["EPS (TTM)"] == "—"
    assert stats["P/E (TTM)"] == "—"


# ── 3. The regression that matters most: profitable names must not move ──


@pytest.mark.parametrize("ticker,quarters,ttm,price,pe", [
    ("AAPL", [2.18, 2.18, 2.18, 2.19], 8.73, 313.40, "35.90"),
    ("MSFT", [4.49, 4.49, 4.49, 4.48], 17.95, 496.28, "27.65"),
    ("KO",   [0.83, 0.83, 0.83, 0.84], 3.33, 90.08, "27.05"),
])
def test_a_profitable_company_is_unchanged(ticker, quarters, ttm, price, pe):
    """These are the values production served on the day the bug was found. A copy fix must
    not touch them."""
    stats = _stats(_build(eps_quarters=_quarters(quarters), price=price))
    assert stats["EPS (TTM)"] == f"{ttm:.2f}"
    assert stats["P/E (TTM)"] == pe


# ── 4. Dividends: "None" is not "—" ──────────────────────────────────


def test_a_company_that_pays_no_dividend_says_so():
    """MRNA pays nothing and `lastDividend` is 0. `_safe_float` defaults a MISSING key to
    0.0 too, so this used to be indistinguishable from a failed profile fetch — and it
    rendered as a fourth dash in a row of dashes."""
    stats = _stats(_build(
        eps_quarters=_quarters([-1.96, -3.40, -2.11, -0.51]),
        price=147.63,
        profile={"lastDividend": 0, "beta": 0.899},
    ))
    assert stats["Dividends"] == "None"


def test_no_profile_still_means_unknown():
    stats = _stats(_build(eps_quarters=_quarters([1.0] * 4), price=100.0, profile={}))
    assert stats["Dividends"] == "—"


def test_a_paying_company_is_unchanged():
    """KO's production string on the day of the report."""
    stats = _stats(_build(
        eps_quarters=_quarters([0.83, 0.83, 0.83, 0.84]),
        price=90.08,
        profile={"lastDividend": 2.08},
    ))
    assert stats["Dividends"] == "2.08 (2.31%)"


# ── 5. The flat list and the grouped list must agree ─────────────────


def test_both_renderings_carry_the_same_values():
    """`key_statistics` (flat) and `key_statistics_groups` (what the screen renders) are
    built from the same variables. If they ever diverge, one surface silently keeps the
    old behaviour — which is exactly how a fix ships and appears not to work."""
    built = _build(
        eps_quarters=_quarters([-1.96, -3.40, -2.11, -0.51]),
        price=147.63,
        profile={"lastDividend": 0},
    )
    flat_list, _groups = built
    flat = {i.label: i.value for i in flat_list}
    grouped = _stats(built)
    for label in ("P/E (TTM)", "P/E (FWD)", "EPS (TTM)", "Dividends"):
        assert flat[label] == grouped[label], f"{label} disagrees between the two renderings"


# ── 6. The payload version that stops the two cards disagreeing ─────


def test_a_pre_fix_cached_row_is_rejected_not_served():
    """`snapshot_cache` stores the FORMATTED strings, so a rendering change does not reach
    a user until the row ages out — 24h here. That window shipped a screen contradicting
    itself: Key Statistics rebuilt live and said "Neg." while the Price card served a
    pre-fix row saying "—". Measured on MRNA while fixing it."""
    from app.services.valuation_snapshot_service import (
        _SNAPSHOT_PAYLOAD_VERSION, _VERSION_KEY, ValuationSnapshotService,
    )

    svc = ValuationSnapshotService.__new__(ValuationSnapshotService)
    pre_fix_row = {                      # what production had cached for MRNA
        "category": "Price", "rating": 2, "full_report_available": True,
        "metrics": [{"name": "P/E (sector avg 22)", "value": "—"}],
    }

    class _FakeSupabase:
        def __init__(self, payload):
            self._payload = payload
        def table(self, _): return self
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            from datetime import datetime, timezone
            return type("R", (), {"data": [{
                "response_json": self._payload,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }]})()

    # Unversioned (pre-fix) → rejected, even though it is only seconds old.
    svc.supabase = _FakeSupabase(pre_fix_row)
    assert svc._check_supabase_cache("MRNA") is None, (
        "a row written before the formatting change must be rebuilt, not served"
    )

    # Current version → served, so the cache is not permanently cold.
    svc.supabase = _FakeSupabase({**pre_fix_row, _VERSION_KEY: _SNAPSHOT_PAYLOAD_VERSION})
    assert svc._check_supabase_cache("MRNA") is not None


def test_the_version_is_written_back():
    """A read-side check is useless if the write side never stamps it."""
    import inspect

    from app.services.valuation_snapshot_service import ValuationSnapshotService

    src = inspect.getsource(ValuationSnapshotService._upsert_supabase_cache)
    assert "_VERSION_KEY" in src and "_SNAPSHOT_PAYLOAD_VERSION" in src, (
        "the upsert must stamp the payload version, or every row reads as pre-fix "
        "forever and the cache never serves a hit again"
    )
