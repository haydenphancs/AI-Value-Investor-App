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


# ── 7. EV/EBITDA: ask for the key that exists ────────────────────────
#
# `enterpriseValueOverEBITDATTM` is None for EVERY ticker on `/stable` (measured across
# AAPL, MSFT, KO, NVDA, JPM, XOM, MRNA, PLUG, RIVN, UBER), so the primary lookup always
# failed and a reconstruction ladder ran instead — dividing a CURRENT enterprise value by
# the LAST FISCAL YEAR's EBITDA. AAPL: 4648.51B/144.43B = 32.19 against a true TTM
# 4648.51B/168.49B = 27.59, i.e. ~17% overstated, and the only annual metric on an
# otherwise-TTM card.


def _valuation_service():
    from app.services.valuation_snapshot_service import ValuationSnapshotService
    return ValuationSnapshotService.__new__(ValuationSnapshotService)


@pytest.mark.parametrize("ticker,ttm_value", [("AAPL", 27.59), ("MRNA", -20.84)])
def test_ev_ebitda_prefers_the_populated_stable_key(ticker, ttm_value):
    """The measured real values. `enterpriseValueMultipleTTM` was verified to equal
    EV(TTM)/EBITDA(TTM) exactly."""
    from app.services.valuation_snapshot_service import _fmt_ratio, _safe_float

    fr = {"enterpriseValueMultipleTTM": ttm_value}   # and NO enterpriseValueOverEBITDA*
    assert _safe_float(fr, "enterpriseValueOverEBITDATTM") is None
    got = _safe_float(fr, "enterpriseValueMultipleTTM")
    assert got == ttm_value
    assert _fmt_ratio(got) == (f"{ttm_value:.2f}" if ttm_value > 0 else "Neg.")


class _RatiosFMP:
    """Serves a ratios-ttm payload plus statement data the reconstruction ladder would use.

    The annual EBITDA and market cap below are chosen so the LADDER produces a visibly
    different number from the TTM key: mcap 1e12 ÷ annual EBITDA 144.43e9 = 6.92, versus
    the 27.59 the key carries. That difference is what makes the assertion discriminating.

    ⚠️ "Was the income endpoint called?" does NOT work as the control — `_compute` fetches
    all six endpoints up front in a single `asyncio.gather` regardless of whether the
    ladder runs, so the call always happens. Only the VALUE separates the two paths.
    """

    #: mcap ÷ annual EBITDA — what the ladder yields for these inputs.
    RECONSTRUCTED = "6.92"

    def __init__(self, ratios_ttm):
        self._r = ratios_ttm

    async def get_company_profile(self, t):
        # `mktCap`, not `marketCap`: `fmp._normalize_profile` aliases the renamed field
        # back before any service sees it, so a realistic profile carries BOTH. Modelling
        # the raw payload here would test a shape the code never receives.
        return {"sector": "Technology", "industry": "Software",
                "mktCap": 1_000_000_000_000, "marketCap": 1_000_000_000_000,
                "price": 100.0}

    async def get_ratios_ttm(self, t):
        return [dict(self._r)]

    async def get_key_metrics_ttm(self, t):
        return [{}]

    async def get_income_statement(self, t, period=None, limit=None):
        return [{"ebitda": 144_430_000_000}]      # the ANNUAL figure, deliberately stale

    async def get_cash_flow_statement(self, t, period=None, limit=None):
        return [{}]

    async def get_balance_sheet(self, t, period=None, limit=None):
        return [{"totalDebt": 0, "cashAndCashEquivalents": 0}]


def _ev_metric(snapshot):
    return next(m.value for m in snapshot.metrics if m.name.startswith("EV/EBITDA"))


async def _compute_with(ratios_ttm, monkeypatch):
    import app.services.valuation_snapshot_service as mod
    monkeypatch.setattr(mod, "get_current_benchmarks", lambda *a, **k: {}, raising=False)
    svc = mod.ValuationSnapshotService.__new__(mod.ValuationSnapshotService)
    svc.fmp = _RatiosFMP(ratios_ttm)
    svc.supabase = None
    return await svc._compute("AAPL")


@pytest.mark.asyncio
@pytest.mark.parametrize("ttm_value,expected", [(27.59, "27.59"), (-20.84, "Neg.")])
async def test_ev_ebitda_uses_the_populated_key_not_the_annual_reconstruction(
    ttm_value, expected, monkeypatch
):
    """Drives the REAL `_compute`. Renaming the lookup to a key that does not exist must
    fail this — an earlier source-scan version stayed green when exactly that was done
    (mutation-verified), because it only compared the ORDER of two strings."""
    snap = await _compute_with({"enterpriseValueMultipleTTM": ttm_value}, monkeypatch)
    got = _ev_metric(snap)
    assert got == expected
    assert got != _RatiosFMP.RECONSTRUCTED, (
        "the annual-EBITDA reconstruction won even though the correct TTM key was "
        "available — that is the ~17% overstatement this change removes"
    )


@pytest.mark.asyncio
async def test_the_reconstruction_ladder_still_works_when_every_key_is_absent(monkeypatch):
    """The control. With no ratio key of any spelling the ladder MUST still fire — and it
    must produce the reconstructed number, proving the assertion above could have caught
    the ladder had it run. Without this, deleting the ladder outright leaves the pair
    green and a ticker FMP omits the ratio for would silently show nothing."""
    snap = await _compute_with({}, monkeypatch)
    assert _ev_metric(snap) == _RatiosFMP.RECONSTRUCTED


def test_a_negative_ev_ebitda_renders_undefined_not_missing():
    """The last `—` on a loss-maker's Price card."""
    from app.services.valuation_snapshot_service import _fmt_ratio
    assert _fmt_ratio(-20.84) == "Neg."
    assert _fmt_ratio(-2.31) == "Neg."


def test_the_payload_version_rejects_the_pre_fix_row():
    """v2 rows carry the old 32.19. Without the bump they survive 24h and the screen
    contradicts itself again."""
    from app.services.valuation_snapshot_service import _SNAPSHOT_PAYLOAD_VERSION
    assert _SNAPSHOT_PAYLOAD_VERSION >= 3, (
        "bump the payload version whenever these strings change shape, or every "
        "already-cached ticker keeps serving the old number"
    )


# ── 8. The report quotes what the screen shows ───────────────────────


def _collected(*, annual, ttm):
    from app.services.agents.ticker_report_data_collector import CollectedTickerData
    out = CollectedTickerData(ticker="MSFT", persona_key="warren_buffett")
    out.ratios = [annual]
    out.ratios_ttm = [ttm]
    return out


# The measured MSFT divergence: FMP prices its ANNUAL ratios at the fiscal year close
# (annual P/E 20.72 x FY epsDiluted 17.95 = 371.98, and MSFT closed at 373.02 on its FY
# end 2026-06-30) while it traded at 496.37.
_MSFT_ANNUAL = {
    "priceToEarningsRatio": 20.72, "priceToBookRatio": 6.26,
    "priceToSalesRatio": 8.35, "enterpriseValueMultiple": 13.87,
    "priceToFreeCashFlowRatio": 40.0,
}
_MSFT_TTM = {
    "priceToEarningsRatioTTM": 27.58, "priceToBookRatioTTM": 8.34,
    "priceToSalesRatioTTM": 11.11, "enterpriseValueMultipleTTM": 18.28,
    "priceToFreeCashFlowRatioTTM": 55.02,
}


def _computed(out):
    from app.services.agents.ticker_report_data_collector import (
        TickerReportDataCollector,
    )
    svc = TickerReportDataCollector.__new__(TickerReportDataCollector)
    svc._compute_metrics(out)
    return out.computed or {}


def test_the_report_current_multiples_are_ttm():
    """These reach the Gemini prompt and the persona style-fit term. On the annual path
    Cay AI described MSFT as a 20.7x business while the screen said 27.6x."""
    c = _computed(_collected(annual=_MSFT_ANNUAL, ttm=_MSFT_TTM))
    assert c["pe_ratio"] == 27.58
    assert c["pb_ratio"] == 8.34
    assert c["ps_ratio"] == 11.11
    assert c["ev_ebitda"] == 18.28


def test_the_report_falls_back_to_annual_when_ttm_is_absent():
    """A missing TTM field must degrade to the annual figure, not to None — that would
    turn a present number into a dash."""
    c = _computed(_collected(annual=_MSFT_ANNUAL, ttm={}))
    assert c["pe_ratio"] == 20.72
    assert c["ev_ebitda"] == 13.87


def test_no_ratios_at_all_still_yields_none():
    c = _computed(_collected(annual={}, ttm={}))
    assert c["pe_ratio"] is None and c["ev_ebitda"] is None


def test_build_financial_context_quotes_the_ttm_multiples():
    """The model must be told the number the user is looking at."""
    from app.services.agents.ticker_report_data_collector import build_financial_context

    out = _collected(annual=_MSFT_ANNUAL, ttm=_MSFT_TTM)
    _computed(out)
    ctx = build_financial_context(out)
    assert "P/E: 27.58" in ctx, f"prompt quotes the wrong P/E:\n{ctx}"
    assert "EV/EBITDA: 18.28" in ctx
    assert "20.72" not in ctx, "the fiscal-year-end multiple leaked into the prompt"


def test_build_financial_context_passes_a_negative_through():
    """Telling the model a loss-maker has no P/E and telling it the P/E is -18.75 produce
    different prose, and only the second is true."""
    from app.services.agents.ticker_report_data_collector import build_financial_context

    out = _collected(annual={}, ttm={"priceToEarningsRatioTTM": -18.75})
    _computed(out)
    ctx = build_financial_context(out)
    assert "P/E: -18.75" in ctx


def test_the_annual_series_is_untouched():
    """`ratios` stays ANNUAL — it drives the 10-year tap-to-expand history charts, where
    a fiscal series is exactly right. Only the single headline value moved to TTM."""
    out = _collected(annual=_MSFT_ANNUAL, ttm=_MSFT_TTM)
    _computed(out)
    assert out.ratios == [_MSFT_ANNUAL], "the annual history source was mutated"


# ── 9. The keys we hand the MODEL must exist ─────────────────────────
#
# `/stable` renaming a field is not an error here — these are presence-based SELECT
# lists and a dead name simply VANISHES from what Cay AI receives. Verified against a
# live AAPL payload: the income tool was returning no EPS and no year label at all.


def test_the_gemini_statement_tools_request_live_field_names():
    from app.services.agents.fmp_tools import _compress_financial_data

    live_income = {
        "date": "2025-09-27", "fiscalYear": 2025, "period": "FY",
        "revenue": 4.16e11, "grossProfit": 1.9e11, "operatingIncome": 1.2e11,
        "netIncome": 9.4e10, "epsDiluted": 6.08, "eps": 6.11,
        "operatingExpenses": 6.0e10,
    }
    row = _compress_financial_data([live_income], "income")["data"][0]
    assert row.get("epsDiluted") == 6.08, (
        "the income tool hands the model no EPS — it asks for `epsdiluted` (lowercase d), "
        "which /stable does not return"
    )
    assert row.get("fiscalYear") == 2025, "no year label reaches the model"

    live_cf = {
        "date": "2025-09-27", "fiscalYear": 2025, "period": "FY",
        "operatingCashFlow": 1.2e11, "capitalExpenditure": -1.1e10,
        "freeCashFlow": 1.09e11, "netDividendsPaid": -1.5e10,
        "commonStockRepurchased": -8.0e10,
    }
    cf_row = _compress_financial_data([live_cf], "cash_flow")["data"][0]
    assert cf_row.get("netDividendsPaid") == -1.5e10, (
        "the cash-flow tool drops dividends — `dividendsPaid` was renamed"
    )
    assert cf_row.get("fiscalYear") == 2025


def test_the_statement_tools_still_accept_the_legacy_names():
    """Both spellings are listed on purpose, so an upstream revert keeps working."""
    from app.services.agents.fmp_tools import _compress_financial_data

    legacy = {"date": "2024-09-28", "calendarYear": "2024", "period": "FY",
              "revenue": 3.9e11, "epsdiluted": 6.08}
    row = _compress_financial_data([legacy], "income")["data"][0]
    assert row.get("calendarYear") == "2024"
    assert row.get("epsdiluted") == 6.08


def test_the_ai_context_labels_each_statement_with_a_year():
    """It printed a literal "?" for every year, so the model could not tell three
    income statements apart or order them."""
    from app.services.agents.ticker_report_data_collector import (
        CollectedTickerData, build_financial_context,
    )

    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.income = [
        {"fiscalYear": 2025, "date": "2025-09-27", "revenue": 4.16e11, "netIncome": 9.4e10},
        {"date": "2024-09-28", "revenue": 3.91e11, "netIncome": 9.37e10},   # no year key
    ]
    ctx = build_financial_context(out)
    assert "[2025]" in ctx
    assert "[2024]" in ctx, "the date fallback did not supply a year"
    assert "[?]" not in ctx, "a statement reached the model with no year label"
