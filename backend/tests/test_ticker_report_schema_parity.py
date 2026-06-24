"""
Schema parity tests for the ticker-report pipeline.

These tests pin the contract between the backend and the iOS Swift
Codable. If a Pydantic field is renamed, dropped, or its type drifts,
these tests fail before the Swift app does — which is the failure we
care about because Swift's JSONDecoder is strict and a single bad
field crashes the whole report screen.

Coverage:
  1. `assemble_report` with worst-case AI inputs (empty/None/fallback)
     still produces a `TickerReportResponse`-validating dict.
  2. The structured error response from `error_response` matches the
     iOS `APIErrorResponse` decoder shape.
  3. The `_split_structured_error` helper round-trips both legacy
     plain-string and new JSON-encoded error_message DB rows.
  4. Every persona key has a non-empty `narrative_lens`.
  5. The 24h `ticker_report_cache` helper uses the
     `(ticker, persona)` composite key.

These tests run without network or Supabase — they exercise the data
shape, not external services. Slow integration coverage lives in the
manual smoke checks in the plan file.
"""

from __future__ import annotations

import json

import pytest

from app.api.error_response import (
    ErrorCode,
    classify_exception,
    error_body_from_exception,
    make_error_response,
)
from app.schemas.ticker_report import (
    CapitalAllocationResponse,
    CriticalFactorResponse,
    DeepDiveMetricCardResponse,
    MetricHistoryPointResponse,
    RevenueForecastResponse,
    TickerReportResponse,
)
from app.schemas.signal_of_confidence import (
    DividendInfoSchema,
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services.agents.narrative_prompts import (
    build_narrative_jobs,
    stage_a_fallback,
)
from app.services.agents.persona_config import (
    PERSONA_KEYS,
    get_persona_config,
)
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
    _build_capital_allocation_block,
    _build_fundamental_metrics_from_snapshots,
    _build_fundamentals_history,
    _build_price_action,
    _compute_price_volatility,
    _snapshot_to_card,
    _tier_for_z,
    _z_score_for_window,
    build_financial_context,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _make_collected_data(
    *, ticker: str = "AAPL", persona: str = "warren_buffett"
) -> CollectedTickerData:
    """Build a CollectedTickerData with realistic-ish stub data, then
    let the collector compute metrics + sections from it."""
    coll = TickerReportDataCollector()
    out = CollectedTickerData(ticker=ticker, persona_key=persona)

    out.profile = {
        "companyName": "Apple Inc.",
        "exchangeShortName": "NASDAQ",
        "industry": "Consumer Electronics",
        "sector": "Technology",
        "mktCap": 3_500_000_000_000,
        "ceo": "Tim Cook",
        "image": "https://example.com/aapl.png",
        "fullTimeEmployees": 164000,
        "description": "Apple designs and sells consumer electronics, software, and services.",
    }
    out.quote = {
        "price": 230.0,
        "pe": 32.5,
        "yearLow": 165.0,
        "yearHigh": 250.0,
    }
    out.income = [
        {"calendarYear": 2024, "revenue": 391_000_000_000, "netIncome": 93_700_000_000, "operatingIncome": 114_000_000_000},
        {"calendarYear": 2023, "revenue": 383_000_000_000, "netIncome": 97_000_000_000, "operatingIncome": 110_000_000_000},
    ]
    out.balance = [
        {
            "totalAssets": 364_000_000_000,
            "totalLiabilities": 308_000_000_000,
            "totalCurrentAssets": 152_000_000_000,
            "totalCurrentLiabilities": 176_000_000_000,
            "retainedEarnings": -19_000_000_000,
            "totalDebt": 105_000_000_000,
            "cashAndCashEquivalents": 30_000_000_000,
        }
    ]
    out.cash_flow = [
        {
            "freeCashFlow": 109_000_000_000,
            "operatingCashFlow": 118_000_000_000,
            "commonStockRepurchased": -94_000_000_000,
        }
    ]
    out.ratios = [
        {
            "grossProfitMargin": 0.46,
            "netProfitMargin": 0.24,
            "operatingProfitMargin": 0.29,
            "returnOnEquity": 1.65,
            "returnOnAssets": 0.27,
            "priceEarningsRatio": 32.5,
            "priceToBookRatio": 50.0,
            "priceToSalesRatio": 9.0,
            "priceToFreeCashFlowsRatio": 32.0,
            "enterpriseValueOverEBITDA": 26.0,
            "debtEquityRatio": 1.87,
            "currentRatio": 0.86,
            "interestCoverage": 28.0,
        }
    ]
    out.estimates = [
        {"date": "2025-09-30", "estimatedRevenueAvg": 410_000_000_000, "estimatedEpsAvg": 7.20},
        {"date": "2026-09-30", "estimatedRevenueAvg": 440_000_000_000, "estimatedEpsAvg": 7.85},
        {"date": "2027-09-30", "estimatedRevenueAvg": 470_000_000_000, "estimatedEpsAvg": 8.50},
    ]
    out.historical = {
        "historical": [
            {"date": f"2026-04-{d:02d}", "close": 230 - d * 0.5} for d in range(1, 21)
        ]
    }
    out.news = []
    out.insider_trades = []
    out.insider_roster = []
    out.segments_raw = []
    out.earnings_dates = []
    out.analyst_analysis = None
    out.holders_response = None

    coll._compute_metrics(out)
    coll._build_sections(out)
    return out


# ── Tests ─────────────────────────────────────────────────────────────


def test_assemble_report_with_stage_a_fallback_validates_pydantic():
    """Worst-case AI failure path: collector + stage_a_fallback() merge
    must still produce a Pydantic-valid TickerReportResponse."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    shell = stage_a_fallback()

    report = coll.assemble_report(out, shell)
    # Will raise pydantic.ValidationError on schema drift.
    TickerReportResponse.model_validate(report)


def test_assemble_report_top_level_keys_match_swift_codable():
    """Every top-level key in TickerReportAPIResponse (Swift) must be
    present in the assembled dict so JSONDecoder doesn't reject."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())

    expected = {
        "symbol", "company_name", "exchange", "logo_url", "live_date",
        "price_close_date",
        "agent", "quality_score", "executive_summary_text",
        "executive_summary_bullets", "core_thesis",
        "fundamental_metrics", "growth_chart", "profit_power",
        "overall_assessment", "revenue_forecast",
        "insider_data", "key_management", "price_action", "revenue_engine",
        "moat_competition", "macro_data", "wall_street_consensus",
        "hidden_market_signals",
        "critical_factors", "disclaimer_text",
    }
    missing = expected - set(report.keys())
    # `_scoring_inputs` is an INTERNAL scoring field: assemble_report still builds
    # it (the persona-rating input), but it's stripped from the iOS response
    # (patch_legacy_price_action / schema model_dump) and is NOT in the Swift
    # contract — so it's allowed as an extra key here, just not required.
    extra = set(report.keys()) - expected - {"_scoring_inputs"}
    assert not missing, f"missing top-level keys: {missing}"
    assert not extra, f"unexpected top-level keys (would fail iOS decoder): {extra}"


def test_growth_chart_frozen_into_report_and_validates():
    """The rich Growth chart (parity with the free Growth chart) is frozen into
    the report and round-trips through GrowthResponse / TickerReportResponse —
    the shape iOS decodes. Absent growth_chart stays None (legacy-safe)."""
    from app.schemas.growth import GrowthResponse, GrowthDataPointSchema

    coll = TickerReportDataCollector()
    out = _make_collected_data()
    out.growth_chart = GrowthResponse(
        symbol="AAPL",
        eps_annual=[GrowthDataPointSchema(
            period="2024", value=6.75, yoy_change_percent=10.1, sector_average_yoy=7.2)],
        eps_quarterly=[],
        revenue_annual=[GrowthDataPointSchema(
            period="2024", value=391_000_000_000.0, yoy_change_percent=2.1, sector_average_yoy=5.0)],
        revenue_quarterly=[],
        net_income_annual=[], net_income_quarterly=[],
        operating_profit_annual=[], operating_profit_quarterly=[],
        free_cash_flow_annual=[], free_cash_flow_quarterly=[],
    )
    report = coll.assemble_report(out, stage_a_fallback())

    # Frozen as a JSON-clean dict that validates back to GrowthResponse...
    assert isinstance(report["growth_chart"], dict)
    gc = GrowthResponse.model_validate(report["growth_chart"])
    assert gc.symbol == "AAPL"
    assert gc.revenue_annual[0].value == 391_000_000_000.0
    assert gc.eps_annual[0].yoy_change_percent == 10.1
    assert gc.eps_annual[0].sector_average_yoy == 7.2
    # ...and the full report validates with growth_chart coerced to the model.
    validated = TickerReportResponse.model_validate(report)
    assert validated.growth_chart is not None
    assert validated.growth_chart.revenue_annual[0].value == 391_000_000_000.0

    # Legacy-safe: no growth_chart → None, report still valid.
    out.growth_chart = None
    report2 = coll.assemble_report(out, stage_a_fallback())
    assert report2["growth_chart"] is None
    assert TickerReportResponse.model_validate(report2).growth_chart is None


def test_profit_power_frozen_into_report_and_validates():
    """The rich Profit Power chart (margins + per-margin sector medians) is frozen
    into the report and round-trips through ProfitPowerResponse / TickerReportResponse
    — the shape iOS decodes. Absent profit_power stays None (legacy-safe)."""
    from app.schemas.profit_power import (
        ProfitPowerResponse, ProfitPowerDataPointSchema,
    )

    coll = TickerReportDataCollector()
    out = _make_collected_data()
    out.profit_power = ProfitPowerResponse(
        symbol="AAPL",
        annual=[ProfitPowerDataPointSchema(
            period="2024",
            gross_margin=46.2, operating_margin=31.5,
            fcf_margin=27.0, net_margin=25.3,
            sector_average_net_margin=18.4,
            sector_average_gross_margin=38.0,
            sector_average_operating_margin=22.1,
            sector_average_fcf_margin=12.0,
        )],
        quarterly=[],
        peer_group_level="industry",
    )
    report = coll.assemble_report(out, stage_a_fallback())

    # Frozen as a JSON-clean dict that validates back to ProfitPowerResponse...
    assert isinstance(report["profit_power"], dict)
    pp = ProfitPowerResponse.model_validate(report["profit_power"])
    assert pp.symbol == "AAPL"
    assert pp.annual[0].fcf_margin == 27.0
    assert pp.annual[0].sector_average_fcf_margin == 12.0
    assert pp.annual[0].sector_average_gross_margin == 38.0
    # peer_group_level drives the iOS "Industry Avg" vs "Sector Avg" label.
    assert pp.peer_group_level == "industry"
    # ...and the full report validates with profit_power coerced to the model.
    validated = TickerReportResponse.model_validate(report)
    assert validated.profit_power is not None
    assert validated.profit_power.annual[0].net_margin == 25.3

    # Legacy-safe: no profit_power → None, report still valid.
    out.profit_power = None
    report2 = coll.assemble_report(out, stage_a_fallback())
    assert report2["profit_power"] is None
    assert TickerReportResponse.model_validate(report2).profit_power is None


def test_fundamental_cards_carry_peer_group_level():
    """All 4 fundamental cards carry the ticker-wide peer_group_level
    ("industry"/"sector") that labels the "vs ___ average" footnote + drill-down
    legend on iOS. Optional → legacy reports omit it (None)."""
    from app.services.agents.ticker_report_data_collector import (
        _build_fundamental_metrics_from_snapshots,
    )
    from app.schemas.ticker_report import DeepDiveMetricCardResponse

    cards = _build_fundamental_metrics_from_snapshots(
        profitability=None, growth=None, valuation=None, health=None,
        peer_group_level="industry",
    )
    assert len(cards) == 4
    for c in cards:
        assert c["peer_group_level"] == "industry"
        # Validates against the response schema iOS decodes.
        DeepDiveMetricCardResponse.model_validate(c)

    # Default (legacy-safe) → None on every card.
    legacy = _build_fundamental_metrics_from_snapshots(
        profitability=None, growth=None, valuation=None, health=None,
    )
    assert all(c["peer_group_level"] is None for c in legacy)
    assert DeepDiveMetricCardResponse.model_validate(legacy[0]).peer_group_level is None


def test_pros_cons_capped_at_five():
    """Even if the AI returns 7 bull_case bullets, assemble_report truncates
    to 5 — the signal-driven thesis count tops out at the UI's 2-5 range."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    shell = stage_a_fallback()
    shell["core_thesis"] = {
        "bull_case": [f"bull {i}" for i in range(7)],
        "bear_case": [f"bear {i}" for i in range(6)],
    }
    report = coll.assemble_report(out, shell)
    assert len(report["core_thesis"]["bull_case"]) <= 5
    assert len(report["core_thesis"]["bear_case"]) <= 5


def test_thesis_bullets_label_insider_activity():
    """A Bull/Bear thesis bullet that cites insider buy/sell counts must say
    "insider". The bullets render with NO section header, so "55 sells vs 1 buy
    in 12 months" is unreadable — it could be insiders, institutions, congress,
    or analysts. The synthesis prompt asks the model to label it but the model
    doesn't reliably comply (and a leading "Insider:" prefix is stripped by
    _post_process), so BOTH sanitizers enforce the label deterministically.
    A different source that also has buyers/sellers must NOT be relabeled."""
    from app.services._insider_common import ensure_insider_label
    from app.services.agents.ticker_report_data_collector import _sanitize_thesis
    from app.services.agents.narrative_prompts import _clean_thesis_points

    # The reported bug: an unlabeled insider sell/buy bullet is labeled INLINE
    # (a prefix wouldn't survive _post_process), without disturbing the numbers.
    fixed = ensure_insider_label("55 sells ($1.9B) vs 1 buy ($112K) in 12 months.")
    assert fixed == "55 insider sells ($1.9B) vs 1 buy ($112K) in 12 months."

    # Already-labeled bullets are left untouched (idempotent).
    assert ensure_insider_label("55 insider sells vs 1 buy") == "55 insider sells vs 1 buy"
    assert (
        ensure_insider_label("Insiders sold 9.1M shares vs 1 buy")
        == "Insiders sold 9.1M shares vs 1 buy"
    )

    # A DIFFERENT source that also has buyers/sellers must not be mislabeled.
    for other in (
        "Congress: 3 sells vs 1 buy in 12 months",
        "Institutions: 40 sellers vs 12 buyers last quarter",
        "Hedge funds: 5 sells vs 2 buys",
    ):
        assert ensure_insider_label(other) == other

    # Non buy/sell bullets are untouched.
    assert (
        ensure_insider_label("Debt-to-Equity 3.63 vs sector 0.26")
        == "Debt-to-Equity 3.63 vs sector 0.26"
    )

    # Both sanitizers carry the label through; non-insider bullets pass clean.
    san = _sanitize_thesis({
        "bull_case": ["ROE 50.38% (4.00x sector avg) shows capital efficiency"],
        "bear_case": ["55 sells ($1.9B) vs 1 buy ($112K) in 12 months."],
    })
    assert "insider" in san["bear_case"][0].lower()
    assert san["bull_case"] == ["ROE 50.38% (4.00x sector avg) shows capital efficiency"]

    cleaned = _clean_thesis_points(["55 sells ($1.9B) vs 1 buy ($112K) in 12 months."])
    assert cleaned and "insider" in cleaned[0].lower()


def test_narrative_jobs_build_without_error_for_fallback_shell():
    """Stage B job builder must walk an assembled fallback report
    without KeyError or AttributeError."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())
    persona = get_persona_config("warren_buffett")
    evidence = build_financial_context(out)

    jobs = build_narrative_jobs(persona, evidence, report)
    assert len(jobs) > 0, "expected some narrative jobs in the fallback path"
    for j in jobs:
        assert isinstance(j.prompt, str) and len(j.prompt) > 50
        assert callable(j.apply)


def test_moat_dimensions_carry_source_tier():
    """Each pillar must report `source` ∈ {deterministic, grounded,
    ai_legacy} so iOS can show provenance separately from confidence.
    Regression guard for the September 2025 bug where the field was
    silently dropped by Pydantic before being added to the schema."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())
    dims = report["moat_competition"]["dimensions"]
    assert len(dims) == 5, f"expected 5 pillars, got {len(dims)}"
    valid_sources = {"deterministic", "grounded", "ai_legacy"}
    for d in dims:
        assert "source" in d, f"pillar {d.get('name')!r} missing `source`"
        assert d["source"] in valid_sources, (
            f"pillar {d.get('name')!r} has invalid source {d.get('source')!r}"
        )


def test_revenue_forecast_carries_insight_field():
    """Future Forecast gained a Stage-B `insight` (the "why" narrative).
    The assembled dict must always carry the key (seeded by the collector
    partial), the job builder must emit a `revenue_forecast_insight` job
    targeting it, and a populated insight must validate against
    RevenueForecastResponse so the iOS RevenueForecastDTO decode can't crash."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())

    rf = report["revenue_forecast"]
    assert "insight" in rf, "revenue_forecast must always carry the `insight` key"

    # The Stage-B job that fills it must exist and target revenue_forecast.
    persona = get_persona_config("warren_buffett")
    evidence = build_financial_context(out)
    jobs = build_narrative_jobs(persona, evidence, report)
    insight_jobs = [j for j in jobs if j.label == "revenue_forecast_insight"]
    assert len(insight_jobs) == 1, "expected exactly one revenue_forecast_insight job"

    # Stage B mutates the assembled report in place — applying the job's
    # value must land on this dict, and a populated insight stays valid.
    insight_jobs[0].apply("Revenue compounds ~15% on cloud demand; EPS faster on leverage.")
    assert rf["insight"].startswith("Revenue compounds")
    RevenueForecastResponse.model_validate(rf)


def test_critical_factors_capped_at_five():
    """'never more than 5' — even if Stage A returns 7 critical_factors,
    assemble_report truncates to 5 before Stage B fans out narrative jobs."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    shell = stage_a_fallback()
    shell["critical_factors"] = [
        {"title": f"Factor {i}", "severity": "medium", "description": "", "watch": ""}
        for i in range(7)
    ]
    report = coll.assemble_report(out, shell)
    assert len(report["critical_factors"]) <= 5


def test_critical_factors_carry_watch_action():
    """Each critical factor gains a forward-looking `watch` line written by a
    `critical_factor_watch_*` Stage-B job; a populated watch validates against
    CriticalFactorResponse so the iOS CriticalFactorDTO decode can't crash."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    shell = stage_a_fallback()
    shell["critical_factors"] = [
        {"title": "Free Cash Flow", "severity": "high", "description": "", "watch": ""},
        {"title": "Valuation", "severity": "medium", "description": "", "watch": ""},
    ]
    report = coll.assemble_report(out, shell)

    persona = get_persona_config("warren_buffett")
    evidence = build_financial_context(out)
    jobs = build_narrative_jobs(persona, evidence, report)
    watch_jobs = [j for j in jobs if j.label.startswith("critical_factor_watch_")]
    assert len(watch_jobs) == len(report["critical_factors"]), (
        "expected one critical_factor_watch_* job per factor"
    )

    # Populate one factor end-to-end; watch is optional/nullable but must stay
    # Pydantic-valid when present.
    cf = report["critical_factors"][0]
    cf["watch"] = "Next earnings — is operating cash flow catching up to capex?"
    CriticalFactorResponse.model_validate(cf)


def test_executive_summary_bullets_removed_emits_empty():
    """The Executive Summary is now a general overview paragraph; its category
    bullets were removed (the specifics moved to Bull/Bear). assemble_report
    must always emit an empty list — even if a legacy shell carried bullets —
    so the response contract / iOS decode stays stable and nothing renders."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    shell = stage_a_fallback()
    shell["executive_summary_bullets"] = [
        {"category": "Risk", "sentiment": "negative", "text": "legacy bullet"}
    ]
    report = coll.assemble_report(out, shell)
    assert report["executive_summary_bullets"] == []


def test_narrative_fallbacks_keep_pydantic_valid():
    """If every Stage B call fails, applying job.fallback_value on each
    one must leave the report still Pydantic-valid."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())
    persona = get_persona_config("warren_buffett")
    evidence = build_financial_context(out)
    for j in build_narrative_jobs(persona, evidence, report):
        j.apply(j.fallback_value)
    TickerReportResponse.model_validate(report)


def test_price_action_carries_ground_truth_fields():
    """price_action must include change_pct/direction/window_label/tag so
    iOS renders the same direction the AI narrative was grounded in.
    Without these the Stage B prompt can write 'dip' against a +12% chart."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())
    pa = report["price_action"]
    assert "change_pct" in pa, "iOS PriceActionDTO requires change_pct"
    assert "direction" in pa, "iOS PriceActionDTO requires direction"
    assert "window_label" in pa, "iOS PriceActionDTO requires window_label"
    assert "tag" in pa, "iOS PriceActionDTO requires tag"
    assert pa["direction"] in {"up", "down", "flat"}, (
        f"direction must be up/down/flat, got {pa['direction']!r}"
    )
    assert isinstance(pa["change_pct"], (int, float))
    assert isinstance(pa["window_label"], str) and pa["window_label"]
    assert isinstance(pa["tag"], str) and pa["tag"]
    # _news_headlines is Pydantic-ignored — must not leak into the serialized response.
    assert "_news_headlines" not in TickerReportResponse.model_validate(
        report
    ).price_action.model_dump()


# ── Capital Allocation block parity ───────────────────────────────────


def _make_signal_of_confidence(
    *, share_count_change: float = 3.7
) -> SignalOfConfidenceResponse:
    """Two quarters of rising shares (a 'Diluting' story like Oracle) + the
    T12M summary, mirroring what SignalOfConfidenceService produces."""
    points = [
        SignalOfConfidenceDataPointSchema(
            period="Q2 '23",
            dividend_yield=0.90,
            buyback_yield=0.40,
            dividend_amount=900.0,
            buyback_amount=400.0,
            shares_outstanding=1000.0,
        ),
        SignalOfConfidenceDataPointSchema(
            period="Q2 '25",
            dividend_yield=0.93,
            buyback_yield=0.50,
            dividend_amount=950.0,
            buyback_amount=500.0,
            shares_outstanding=1037.0,
        ),
    ]
    return SignalOfConfidenceResponse(
        symbol="ORCL",
        data_points=points,
        summary=SignalOfConfidenceSummarySchema(
            total_yield=1.43,
            dividend_yield=0.93,
            buyback_yield=0.50,
            share_count_change=share_count_change,
        ),
        dividend_info=DividendInfoSchema(
            ex_dividend_date="2025-04-10",
            payment_date="2025-04-24",
            five_year_avg_yield=1.0,
            status="Low",
            buyback_status="Diluting",
        ),
    )


def test_capital_allocation_block_forwards_data_points():
    """The Insider & Management capital-allocation card now carries the
    per-quarter `data_points` series so iOS can draw the dilution mini-chart
    and label the share-count window. The block must forward each point with
    the exact 6 keys the iOS SignalOfConfidenceDataPointDTO decodes, preserve
    oldest→newest order, and validate against CapitalAllocationResponse — drift
    here is a JSONDecoder crash on the report screen."""
    block = _build_capital_allocation_block(_make_signal_of_confidence())

    assert block is not None
    # Summary fields still present (unchanged contract)
    assert block["buyback_status"] == "Diluting"
    assert block["share_count_change"] == 3.7

    dps = block["data_points"]
    assert isinstance(dps, list) and len(dps) == 2
    expected_keys = {
        "period", "dividend_yield", "buyback_yield",
        "dividend_amount", "buyback_amount", "shares_outstanding",
    }
    for dp in dps:
        assert set(dp.keys()) == expected_keys
    # oldest → newest preserved (drives the window label + chart x-axis)
    assert dps[0]["period"] == "Q2 '23"
    assert dps[-1]["period"] == "Q2 '25"

    # Whole block must Pydantic-validate (list-of-dicts coerces to the schema).
    model = CapitalAllocationResponse.model_validate(block)
    assert len(model.data_points) == 2
    assert model.data_points[-1].shares_outstanding == 1037.0


def test_capital_allocation_block_none_when_no_signal():
    """No Signal of Confidence → None so iOS hides the whole card (and the
    schema's data_points default stays an empty list, never None)."""
    assert _build_capital_allocation_block(None) is None
    assert CapitalAllocationResponse(
        buyback_status="Low", dividend_status="Fair",
        dividend_yield=0.0, buyback_yield=0.0,
        total_yield=0.0, share_count_change=0.0,
    ).data_points == []


# ── Insider trend chart + recent transactions parity ──────────────────


def test_assemble_report_forwards_insider_flow_and_transactions():
    """The Insider section carries the insider flow series + a recent-trade list,
    reused from holders_response (same source as the Holders tab). assemble_report
    forwards them with the keys iOS decodes (SmartMoneyDataDTO / InsiderActivityDTO),
    KEEPS the price line and forwards the daily series AS-IS — windowing it to the
    trailing 365 days is the source's job (holders_service._build_insider_smart_money,
    covered by test_build_insider_smart_money_windows_daily_prices), so the report
    path is a pure forwarder and must NOT re-trim. It still WINDOWS the trades to the
    365-day cutoff (the Insider table + flow chart), keeps ALL in-window informative
    trades newest-first (no cap, so a counted buy is never hidden), and stays
    Pydantic-valid. None-safe when holders are absent."""
    from datetime import datetime, timezone, timedelta
    from app.schemas.holders import (
        HoldersResponse,
        SmartMoneyDataSchema,
        SmartMoneyFlowDataPointSchema,
        StockPriceDataPointSchema,
        DailyPricePointSchema,
        RecentActivitiesSchema,
        InsiderActivitiesDataSchema,
        InsiderActivitySchema,
    )

    now = datetime.now(timezone.utc)

    def _d(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    coll = TickerReportDataCollector()
    out = _make_collected_data()
    activities = [
        # 12 recent in-window sells (newest-first dates)
        *[
            InsiderActivitySchema(
                name=f"Insider {i}", title="Officer", date=_d(10 + i),
                change_in_millions=-0.01, transaction_type="Informative Sell",
                price_at_transaction=160.5,
            )
            for i in range(12)
        ],
        # an older-but-in-window BUY — would have been pushed off a top-10 cap,
        # which is exactly the bug this windowing+uncapping fixes.
        InsiderActivitySchema(
            name="Director Buyer", title="Director", date=_d(200),
            change_in_millions=0.0005, transaction_type="Informative Buy",
            price_at_transaction=233.87,
        ),
        # a trade OUTSIDE the 365-day window — must be dropped.
        InsiderActivitySchema(
            name="Ancient Seller", title="Officer", date=_d(400),
            change_in_millions=-5.0, transaction_type="Informative Sell",
            price_at_transaction=90.0,
        ),
    ]
    out.holders_response = HoldersResponse(
        symbol="AAPL",
        insider_data=SmartMoneyDataSchema(
            tab="Insider",
            price_data=[
                StockPriceDataPointSchema(month="12/2025", price=160.0),
                StockPriceDataPointSchema(month="01/2026", price=165.0),
            ],
            daily_prices=[
                # In production the source (_build_insider_smart_money) already
                # windows these to 365d; this out-of-window 400-day point is here
                # only to prove the report path forwards as-is and does NOT re-trim.
                DailyPricePointSchema(date=_d(400), price=120.0),
                DailyPricePointSchema(date=_d(200), price=150.0),
                DailyPricePointSchema(date=_d(5), price=165.0),
            ],
            flow_data=[
                SmartMoneyFlowDataPointSchema(month="12/2025", buy_volume=0.0, sell_volume=0.015),
                SmartMoneyFlowDataPointSchema(month="01/2026", buy_volume=0.5, sell_volume=0.0),
            ],
        ),
        recent_activities=RecentActivitiesSchema(
            insider_activities=InsiderActivitiesDataSchema(activities=activities),
        ),
    )

    report = coll.assemble_report(out, stage_a_fallback())
    insider = report["insider_data"]

    # Flow series forwarded with the iOS-decoded keys. The price line is KEPT
    # (the report overlays price on the bars, like the Holders tab) and the daily
    # series is forwarded AS-IS — the report path adds no windowing of its own
    # (that's the source's job; see test_build_insider_smart_money_windows_daily_
    # prices). All three input points survive, including the out-of-window one.
    flow = insider["insider_flow"]
    assert flow is not None
    assert {"month", "buy_volume", "sell_volume"} <= set(flow["flow_data"][0].keys())
    assert flow["price_data"], "monthly price line forwarded (was stripped before)"
    assert len(flow["daily_prices"]) == 3  # all points forwarded, none re-trimmed
    assert any(p["date"] == _d(400) for p in flow["daily_prices"])  # not windowed here

    # Recent trades: windowed to 365d (the 400-day trade dropped), uncapped,
    # newest-first, and the in-window BUY survives (never hidden by a cap).
    recent = insider["recent_transactions"]
    assert recent is not None
    acts = recent["activities"]
    assert len(acts) == 13  # 12 sells + 1 buy; the 400-day-old sell excluded
    cutoff = _d(365)
    assert all(a["date"] >= cutoff for a in acts)
    assert any(a["transaction_type"] == "Informative Buy" for a in acts)
    assert [a["date"] for a in acts] == sorted(
        (a["date"] for a in acts), reverse=True
    )
    assert {
        "name", "title", "date", "change_in_millions",
        "transaction_type", "price_at_transaction",
    } <= set(acts[0].keys())

    # Whole report still validates against the iOS contract.
    TickerReportResponse.model_validate(report)

    # None-safe: no holders_response → blocks omitted (schema defaults to None).
    bare = coll.assemble_report(_make_collected_data(), stage_a_fallback())
    assert bare["insider_data"].get("insider_flow") is None
    assert bare["insider_data"].get("recent_transactions") is None


def test_build_insider_smart_money_windows_daily_prices():
    """The Holders Insider chart overlays a price line on buy/sell bars that span
    the trailing 365 days. The raw daily series spans ~2 years (sized for the
    hedge-fund chart), so _build_insider_smart_money must window the daily series
    to the SAME 365-day cutoff as the bars — otherwise the 2-year line stretched
    over 13-month bars sits each bar under the wrong date (misreading "did insiders
    sell into strength or weakness?"). The price line's left edge must land ~12
    months ago, not ~2 years."""
    from datetime import datetime, timezone, timedelta
    from app.services.holders_service import HoldersService
    from app.schemas.holders import DailyPricePointSchema

    now = datetime.now(timezone.utc)

    def _d(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    # Bypass __init__ (which wires the FMP + Supabase clients) — the method under
    # test is a pure transform that touches neither.
    svc = HoldersService.__new__(HoldersService)

    daily = [
        DailyPricePointSchema(date=_d(740), price=90.0),   # ~2yr — must be trimmed
        DailyPricePointSchema(date=_d(400), price=120.0),  # >365d — must be trimmed
        DailyPricePointSchema(date=_d(360), price=150.0),  # inside window — kept
        DailyPricePointSchema(date=_d(5), price=165.0),    # recent — kept
    ]

    sm = svc._build_insider_smart_money(
        insider_trades=[], monthly_prices={}, daily_prices=daily,
    )

    cutoff = _d(365)
    assert sm.daily_prices, "in-window daily points survive"
    assert all(dp.date >= cutoff for dp in sm.daily_prices)
    assert len(sm.daily_prices) == 2  # the 740- and 400-day points trimmed
    # Left edge of the price line ≈ 12 months ago (matching the leftmost bar),
    # not ~2 years — the whole point of the fix.
    assert min(dp.date for dp in sm.daily_prices) == _d(360)


def test_build_congress_smart_money_windows_daily_prices():
    """The Holders Congress chart (same SmartMoneyFlowChart molecule as Insider)
    overlays a price line on buy/sell bars that span the trailing 12 months. The
    raw daily series spans ~2 years (sized for the hedge-fund chart, which keeps
    the full series), so _build_congress_smart_money must window the daily series
    to the SAME span as the bars — the first day of the oldest of the 12 month
    keys — otherwise the 2-year line stretched over 12-month bars sits each bar
    under the wrong date. Mirrors test_build_insider_smart_money_windows_daily_
    prices; the hedge-fund tab legitimately keeps the full series and is untouched."""
    from datetime import datetime, timezone, timedelta
    from app.services.holders_service import HoldersService
    from app.schemas.holders import DailyPricePointSchema

    now = datetime.now(timezone.utc)

    def _d(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    # Bypass __init__ (which wires the FMP + Supabase clients) — the method under
    # test is a pure transform that touches neither.
    svc = HoldersService.__new__(HoldersService)

    # The cutoff the production code computes: first day of the oldest of the 12
    # month keys (month-aligned, so it lands ~334–365 days ago depending on the
    # day-of-month). Test offsets keep a comfortable margin from that boundary.
    month_keys = svc._generate_month_keys(12)
    o_month, o_year = month_keys[0].split("/")
    cutoff_str = f"{o_year}-{o_month}-01"

    daily = [
        DailyPricePointSchema(date=_d(740), price=90.0),   # ~2yr — must be trimmed
        DailyPricePointSchema(date=_d(400), price=120.0),  # >12mo — must be trimmed
        DailyPricePointSchema(date=_d(200), price=150.0),  # inside window — kept
        DailyPricePointSchema(date=_d(5), price=165.0),    # recent — kept
    ]

    sm = svc._build_congress_smart_money(
        senate_trades=[], house_trades=[], monthly_prices={}, daily_prices=daily,
    )

    assert sm.tab == "Congress"
    assert sm.daily_prices, "in-window daily points survive"
    assert all(dp.date >= cutoff_str for dp in sm.daily_prices)
    assert len(sm.daily_prices) == 2  # the 740- and 400-day points trimmed
    # Left edge of the price line lands within the trailing 12 months (matching
    # the leftmost bar), not ~2 years — the whole point of the fix.
    assert min(dp.date for dp in sm.daily_prices) == _d(200)


@pytest.mark.parametrize("persona_key", sorted(PERSONA_KEYS))
def test_every_persona_has_narrative_lens(persona_key):
    """Phase 2 added narrative_lens — each persona must populate it."""
    p = get_persona_config(persona_key)
    assert p.narrative_lens, f"{persona_key} missing narrative_lens"
    assert len(p.narrative_lens) >= 20, (
        f"{persona_key} narrative_lens too short to give meaningful voice"
    )


@pytest.mark.parametrize("persona_key", sorted(PERSONA_KEYS))
def test_every_persona_has_style_fields(persona_key):
    """Each persona must carry the structured style fields that drive BOTH the
    style-fit score (persona_scoring) and the narrative lens directives
    (narrative_prompts). An empty field silently collapses a persona back to
    generic behavior, so pin them."""
    p = get_persona_config(persona_key)
    assert p.key_metrics, f"{persona_key} missing key_metrics"
    assert p.bull_priority, f"{persona_key} missing bull_priority"
    assert p.bear_priority, f"{persona_key} missing bear_priority"
    assert p.red_flags, f"{persona_key} missing red_flags"
    assert p.score_rules and len(p.score_rules) >= 40, (
        f"{persona_key} score_rules too short to steer the score"
    )
    # The bias block must be wired into the system prompt, AFTER the identity rule.
    assert p.system_prompt.startswith("CRITICAL IDENTITY RULE"), persona_key
    assert "HOW TO BIAS YOUR VERDICT" in p.system_prompt, persona_key


def test_style_signals_nested_under_scoring_inputs_and_stripped():
    """The persona style-fit raw signals live nested INSIDE _scoring_inputs
    (never a new top-level key, which would break the iOS decoder) and are
    stripped from the serialized response by model_dump — mirroring the
    _scoring_inputs / _news_headlines strip."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())

    assert "_style_signals" not in report, "_style_signals must NOT be a top-level key"
    assert "_style_signals" in report.get("_scoring_inputs", {}), (
        "_style_signals must be nested inside _scoring_inputs"
    )

    dumped = TickerReportResponse.model_validate(report).model_dump()
    blob = json.dumps(dumped, default=str)
    assert "_style_signals" not in blob, "_style_signals leaked into the iOS response"
    assert "_scoring_inputs" not in blob, "_scoring_inputs leaked into the iOS response"


# ── Error contract tests ──────────────────────────────────────────────


def test_make_error_response_matches_ios_decoder_shape():
    """iOS APIErrorResponse decodes {error_code, message, user_message,
    action?, details?}. The structured error helper must emit exactly
    those keys at the body root."""
    resp = make_error_response(
        ErrorCode.FMP_RATE_LIMITED,
        message="FMP returned 429 on /quote",
        details={"ticker": "AAPL", "step": "collector"},
    )
    body = json.loads(resp.body.decode())
    assert set(body.keys()) == {
        "error_code", "message", "user_message", "action", "details"
    }
    assert body["error_code"] == "FMP_RATE_LIMITED"
    assert body["message"] == "FMP returned 429 on /quote"
    assert body["user_message"]  # non-empty default
    assert body["details"] == {"ticker": "AAPL", "step": "collector"}


def test_classify_exception_routes_value_error_to_ticker_not_found():
    code, status = classify_exception(
        ValueError("No company profile found for ticker: XYZ")
    )
    assert code == ErrorCode.TICKER_NOT_FOUND
    assert status == 404


def test_classify_exception_routes_quota_keyword_to_gemini():
    code, _ = classify_exception(
        RuntimeError("ResourceExhausted: 429 quota exceeded for free tier")
    )
    assert code == ErrorCode.GEMINI_QUOTA_EXCEEDED


def test_classify_exception_unknown_falls_back_to_generic():
    code, _ = classify_exception(KeyError("totalAssets"))
    assert code == ErrorCode.REPORT_GENERATION_FAILED


def test_error_body_from_exception_carries_underlying():
    body = error_body_from_exception(
        RuntimeError("kaboom"), ticker="AAPL", step="stage_a",
    )
    assert body["error_code"] == ErrorCode.REPORT_GENERATION_FAILED.value
    assert body["details"]["ticker"] == "AAPL"
    assert body["details"]["step"] == "stage_a"
    assert "RuntimeError" in body["details"]["underlying"]


# ── _split_structured_error round-trip ────────────────────────────────


def test_split_structured_error_decodes_json_blob():
    """The status endpoint helper must unpack a JSON-encoded blob into
    (code, user_message). Imported lazily so tests don't require
    Supabase deps."""
    from app.api.v1.endpoints.research import _split_structured_error

    body = {
        "error_code": "FMP_RATE_LIMITED",
        "user_message": "Try again in a minute.",
        "message": "FMP 429",
        "details": {"step": "collector"},
    }
    code, msg = _split_structured_error(json.dumps(body))
    assert code == "FMP_RATE_LIMITED"
    assert msg == "Try again in a minute."


def test_split_structured_error_passes_legacy_plain_string():
    from app.api.v1.endpoints.research import _split_structured_error

    legacy = "ValueError: No company profile found for ticker: ZZZ"
    code, msg = _split_structured_error(legacy)
    assert code is None
    assert msg == legacy


def test_split_structured_error_handles_none():
    from app.api.v1.endpoints.research import _split_structured_error

    code, msg = _split_structured_error(None)
    assert code is None
    assert msg is None


# ── Fundamentals & Growth snapshot-to-card parity ─────────────────────


def test_health_snapshot_carries_five_metrics_to_health_card():
    """When health snapshot includes Current Ratio (5 visible metrics), the
    assembled Health card on TickerReportView must surface all five with
    their exact backend names and values — including the sector-comparison
    suffix that drives the iOS asterisk."""
    health_snap = SnapshotItemResponse(
        category="Financial Health",
        rating=4,
        metrics=[
            SnapshotMetricResponse(name="Altman Z-Score", value="2.10"),
            SnapshotMetricResponse(name="Debt-to-Equity (vs sector 1.30)", value="4.21"),
            SnapshotMetricResponse(name="Current Ratio (vs sector 1.50)", value="0.95"),
            SnapshotMetricResponse(name="Interest Coverage", value="4.77"),
            SnapshotMetricResponse(name="Quick Ratio", value="1.21"),
        ],
        full_report_available=True,
    )
    card = _snapshot_to_card("Health", health_snap)

    assert card["star_rating"] == 4
    labels = [m["label"] for m in card["metrics"]]
    values = [m["value"] for m in card["metrics"]]
    assert labels == [
        "Altman Z-Score",
        "Debt-to-Equity (vs sector 1.30)",
        "Current Ratio (vs sector 1.50)",
        "Interest Coverage",
        "Quick Ratio",
    ]
    assert values == ["2.10", "4.21", "0.95", "4.77", "1.21"]


def test_valuation_snapshot_pfcf_neg_passes_through_verbatim():
    """P/FCF must surface "Neg." verbatim when free cash flow is negative
    (different signal from missing data → "—"). The sector-comparison
    suffix should still appear in the label so iOS renders the asterisk."""
    valuation_snap = SnapshotItemResponse(
        category="Price",
        rating=3,
        metrics=[
            SnapshotMetricResponse(name="P/E (1.20x sector avg 25)", value="32.50"),
            SnapshotMetricResponse(name="P/FCF (sector avg 24)", value="Neg."),
            SnapshotMetricResponse(name="EV/EBITDA (sector avg 18)", value="—"),
        ],
        full_report_available=True,
    )
    card = _snapshot_to_card("Valuation", valuation_snap)

    pfcf = next(m for m in card["metrics"] if m["label"].startswith("P/FCF"))
    ev = next(m for m in card["metrics"] if m["label"].startswith("EV/EBITDA"))

    # "Neg." must survive the assembly unchanged
    assert pfcf["value"] == "Neg."
    # The sector suffix must remain on the label (iOS regex looks for it)
    assert "sector" in pfcf["label"], "P/FCF must keep sector suffix so iOS renders '*'"
    assert ev["value"] == "—"
    assert "sector" in ev["label"], "EV/EBITDA must keep sector suffix so iOS renders '*'"


def test_fundamentals_section_order_is_stable():
    """The four cards must appear in the order iOS expects:
    Profitability, Growth, Valuation, Health."""
    snap = SnapshotItemResponse(
        category="x", rating=3, metrics=[], full_report_available=False,
    )
    cards = _build_fundamental_metrics_from_snapshots(snap, snap, snap, snap)
    titles = [c["title"] for c in cards]
    assert titles == ["Profitability", "Growth", "Valuation", "Health"]


def _annual_statements():
    """3 newest-first FMP-shaped annual rows for the history builder."""
    income = [
        {"calendarYear": "2024", "period": "FY", "date": "2024-09-30",
         "revenue": 400, "epsDiluted": 6.0, "operatingIncome": 120, "ebitda": 140},
        {"calendarYear": "2023", "period": "FY", "date": "2023-09-30",
         "revenue": 380, "epsDiluted": 5.5, "operatingIncome": 110, "ebitda": 130},
        {"calendarYear": "2022", "period": "FY", "date": "2022-09-30",
         "revenue": 350, "epsDiluted": 5.0, "operatingIncome": 100, "ebitda": 120},
    ]
    balance = [
        {"calendarYear": y, "date": f"{y}-09-30", "totalAssets": 1000,
         "totalLiabilities": 600, "totalCurrentAssets": 300,
         "totalCurrentLiabilities": 280, "retainedEarnings": 200}
        for y in ("2024", "2023", "2022")
    ]
    cash_flow = [
        {"calendarYear": y, "date": f"{y}-09-30", "freeCashFlow": 90,
         "depreciationAndAmortization": 20}
        for y in ("2024", "2023", "2022")
    ]
    key_metrics = [
        {"calendarYear": y, "date": f"{y}-09-30", "returnOnEquity": 0.30,
         "returnOnAssets": 0.12, "marketCap": 3000, "enterpriseValue": 3200}
        for y in ("2024", "2023", "2022")
    ]
    ratios = [
        {"calendarYear": "2024", "period": "FY", "date": "2024-09-30",
         "grossProfitMargin": 0.46, "operatingProfitMargin": 0.30,
         "netProfitMargin": 0.25, "priceToEarningsRatio": 35.8,
         "priceToBookRatio": 41.2, "priceToSalesRatio": 9.7,
         "currentRatio": 1.07, "quickRatio": 1.02, "debtToEquityRatio": 0.8,
         "interestCoverageRatio": 12.0, "earningsYield": 0.0279},
        {"calendarYear": "2023", "period": "FY", "date": "2023-09-30",
         "grossProfitMargin": 0.44, "operatingProfitMargin": 0.29,
         "netProfitMargin": 0.24, "priceToEarningsRatio": 30.1,
         "priceToBookRatio": 38.0, "priceToSalesRatio": 9.0,
         "currentRatio": 1.05, "quickRatio": 1.00, "debtToEquityRatio": 0.9,
         "interestCoverageRatio": 11.0, "earningsYield": 0.0331},
        {"calendarYear": "2022", "period": "FY", "date": "2022-09-30",
         "grossProfitMargin": 0.42, "operatingProfitMargin": 0.28,
         "netProfitMargin": 0.23, "priceToEarningsRatio": 28.0,
         "priceToBookRatio": 35.0, "priceToSalesRatio": 8.5,
         "currentRatio": 1.02, "quickRatio": 0.98, "debtToEquityRatio": 1.0,
         "interestCoverageRatio": 10.0, "earningsYield": 0.0357},
    ]
    return income, balance, cash_flow, key_metrics, ratios


def test_fundamentals_history_is_computed_and_oldest_first():
    """The history builder emits a per-metric series for every card metric,
    oldest→newest, with correct unit conversions."""
    income, balance, cash_flow, key_metrics, ratios = _annual_statements()
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profile = {"industry": "Consumer Electronics"}
    out.income, out.balance, out.cash_flow = income, balance, cash_flow
    out.key_metrics, out.ratios = key_metrics, ratios

    hist = _build_fundamentals_history(out)

    # All four cards' metrics are represented.
    for key in ("gross_margin", "roe", "pe", "ev_ebitda", "altman_z",
                "debt_to_equity", "revenue_growth", "earnings_yield"):
        assert key in hist, f"missing history key {key}"

    # Margins are fractions → percent, oldest first.
    gm = hist["gross_margin"]["annual"]
    assert [p["period"] for p in gm] == ["2022", "2023", "2024"]
    assert gm[-1]["value"] == 42.0 + 4.0  # 2024 = 46.0%
    assert hist["gross_margin"]["unit"] == "percent"

    # Multiples carry the "x" unit; raw value passthrough.
    assert hist["pe"]["unit"] == "x"
    assert hist["pe"]["annual"][-1]["value"] == 35.8

    # YoY growth uses a 1-year gap → the oldest year has no prior, so the
    # series is one shorter and starts at the second year.
    rg = hist["revenue_growth"]["annual"]
    assert [p["period"] for p in rg] == ["2023", "2024"]
    assert rg[-1]["value"] == 5.3  # 380 → 400


def test_fundamentals_history_attaches_and_validates_on_card():
    """Attached history survives DeepDiveMetricCardResponse.model_validate
    (the contract the iOS decoder mirrors), and the sector-suffix label still
    resolves to the right history key."""
    income, balance, cash_flow, key_metrics, ratios = _annual_statements()
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profile = {"industry": "Consumer Electronics"}
    out.income, out.balance, out.cash_flow = income, balance, cash_flow
    out.key_metrics, out.ratios = key_metrics, ratios
    hist = _build_fundamentals_history(out)

    prof = SnapshotItemResponse(
        category="Profitability", rating=4,
        metrics=[
            SnapshotMetricResponse(
                name="Gross Margin (1.20x sector avg 38.0%)", value="46.0%"),
            SnapshotMetricResponse(
                name="Return on Equity (ROE) (1.10x sector avg 27%)", value="30.0%"),
        ],
        full_report_available=True,
    )
    card_dict = _build_fundamental_metrics_from_snapshots(
        prof, None, None, None, history_lookup=hist,
    )[0]
    card = DeepDiveMetricCardResponse.model_validate(card_dict)

    gm_metric = card.metrics[0]
    assert gm_metric.history_key == "gross_margin"
    assert gm_metric.history_unit == "percent"
    assert gm_metric.annual_history is not None
    assert len(gm_metric.annual_history) == 3
    assert isinstance(gm_metric.annual_history[0], MetricHistoryPointResponse)
    assert card.metrics[1].history_key == "roe"


def test_fundamentals_card_validates_without_history():
    """Backward-compat: a card built with no history_lookup leaves the new
    fields None and still validates — exactly how legacy/cached reports and
    older iOS builds must keep working."""
    snap = SnapshotItemResponse(
        category="Profitability", rating=3,
        metrics=[SnapshotMetricResponse(name="Gross Margin", value="46.0%")],
        full_report_available=True,
    )
    card_dict = _build_fundamental_metrics_from_snapshots(snap, None, None, None)[0]
    card = DeepDiveMetricCardResponse.model_validate(card_dict)
    assert card.metrics[0].annual_history is None
    assert card.metrics[0].history_key is None


def test_fundamentals_history_survives_full_json_roundtrip():
    """The history must survive validate → dump(json) → json.dumps → loads →
    validate — i.e. the exact encode/decode the iOS JSONDecoder performs.
    Catches any snake_case / optional-field serialization drift."""
    income, balance, cash_flow, key_metrics, ratios = _annual_statements()
    out = CollectedTickerData(ticker="AAPL", persona_key="warren_buffett")
    out.profile = {"industry": "Consumer Electronics"}
    out.income, out.balance, out.cash_flow = income, balance, cash_flow
    out.key_metrics, out.ratios = key_metrics, ratios
    # Sector-average history (fractions for the margin metric → ×100 downstream).
    out.sector_benchmark_history = {
        "annual": {"gross_margin": {"2024": 0.38, "2023": 0.37, "2022": 0.36}},
        "quarterly": {},
    }
    hist = _build_fundamentals_history(out)

    prof = SnapshotItemResponse(
        category="Profitability", rating=4,
        metrics=[SnapshotMetricResponse(name="Gross Margin", value="46.0%")],
        full_report_available=True,
    )
    card_dict = _build_fundamental_metrics_from_snapshots(
        prof, None, None, None, history_lookup=hist)[0]

    card = DeepDiveMetricCardResponse.model_validate(card_dict)
    blob = json.dumps(card.model_dump(mode="json"))
    reparsed = DeepDiveMetricCardResponse.model_validate(json.loads(blob))

    metric = reparsed.metrics[0]
    assert metric.history_key == "gross_margin"
    assert metric.history_unit == "percent"
    assert metric.annual_history is not None and len(metric.annual_history) == 3
    # snake_case keys survived the JSON encode (the iOS CodingKey contract).
    assert '"annual_history"' in blob
    assert '"sector_annual_history"' in blob
    assert metric.annual_history[0].period == "2022"
    # Sector overlay survived round-trip, aligned + ×100.
    assert metric.sector_annual_history is not None
    assert [p.value for p in metric.sector_annual_history] == [36.0, 37.0, 38.0]


# ── Price action: volatility math + tier classification ──────────────


def _flat_prices(count: int, start: float = 100.0) -> list[float]:
    """A perfectly-flat baseline — σ_daily should be 0."""
    return [start] * count


def _gentle_walk(count: int, start: float = 100.0, daily_pct: float = 0.005) -> list[float]:
    """Deterministic ±0.5% alternating walk → ~0.5% daily σ baseline."""
    out = [start]
    for i in range(1, count):
        out.append(out[-1] * (1 + (daily_pct if i % 2 == 0 else -daily_pct)))
    return out


def test_tier_for_z_thresholds():
    """Z-score → tier mapping must match the design spec exactly."""
    assert _tier_for_z(None) == "Typical"
    assert _tier_for_z(0.5) == "Typical"
    assert _tier_for_z(0.99) == "Typical"
    assert _tier_for_z(1.0) == "Notable"
    assert _tier_for_z(1.5) == "Notable"
    assert _tier_for_z(2.0) == "Unusual"
    assert _tier_for_z(2.99) == "Unusual"
    assert _tier_for_z(3.0) == "Extreme"
    assert _tier_for_z(10.0) == "Extreme"


def test_z_score_uses_sqrt_n_scaling():
    """A 5% move over 7 days vs 30 days must scale by √N — not the same z."""
    sigma = 0.015  # 1.5% daily
    z_7 = _z_score_for_window(5.0, sigma, 7)
    z_30 = _z_score_for_window(5.0, sigma, 30)
    assert z_7 is not None and z_30 is not None
    # 7-day band ≈ 1.5 × √7 = 3.97% → z ≈ 5/3.97 ≈ 1.26
    # 30-day band ≈ 1.5 × √30 = 8.22% → z ≈ 5/8.22 ≈ 0.61
    assert 1.20 < z_7 < 1.30, f"7-day z out of range: {z_7}"
    assert 0.55 < z_30 < 0.65, f"30-day z out of range: {z_30}"
    # The same move is "more unusual" over a shorter window.
    assert z_7 > z_30


def test_z_score_returns_none_for_zero_sigma():
    assert _z_score_for_window(5.0, 0.0, 30) is None
    assert _z_score_for_window(5.0, None, 30) is None


def test_compute_price_volatility_with_insufficient_history():
    """Fewer than 30 closes → no σ, default 30-day window, Typical tier."""
    out = _compute_price_volatility(_flat_prices(20))
    assert out["sigma_daily"] is None
    assert out["chosen_window"] == 30
    assert out["tier"] == "Typical"
    assert out["windows"] == []


def test_compute_price_volatility_with_flat_baseline():
    """Zero σ (perfectly flat) → no z computable, default window, Typical."""
    out = _compute_price_volatility(_flat_prices(200))
    assert out["sigma_daily"] is None or out["sigma_daily"] == 0
    assert out["chosen_window"] == 30
    assert out["tier"] == "Typical"


def test_compute_price_volatility_picks_most_unusual_window():
    """Stock with quiet baseline then a single big drop on day 7 should
    trigger a Notable+ tier on the short window."""
    # Quiet baseline of 190 closes
    prices = _gentle_walk(190, start=100.0, daily_pct=0.005)
    # Now bolt on a 7-day window with a -8% cliff
    cliff_start = prices[-1]
    drop_path = [cliff_start * (1 - 0.012 * i) for i in range(1, 11)]
    prices.extend(drop_path)
    out = _compute_price_volatility(prices)
    assert out["sigma_daily"] is not None
    assert out["sigma_daily"] > 0
    # Should pick a short window (7 or 15) as most unusual.
    assert out["chosen_window"] in (7, 15)
    # Tier should be Notable, Unusual, or Extreme — definitely NOT Typical.
    assert out["tier"] in {"Notable", "Unusual", "Extreme"}


def test_compute_price_volatility_quiet_stock_defaults_to_30():
    """Stock whose every window is within ±1σ falls back to 30-day default."""
    prices = _gentle_walk(200, start=100.0, daily_pct=0.005)
    out = _compute_price_volatility(prices)
    assert out["sigma_daily"] is not None
    # All windows should be calm (|z| < 1) — fall through to 30-day default.
    assert out["chosen_window"] == 30
    assert out["tier"] == "Typical"


def test_build_price_action_emits_new_fields():
    """The assembled price_action dict must carry the four volatility fields
    so iOS can render the sub-label. Existing fields must also remain."""
    prices = _gentle_walk(200, start=100.0, daily_pct=0.005)
    out = _build_price_action(
        recent_prices=prices,
        current_price=prices[-1],
        earnings_dates=[],
        news=None,
    )
    # Existing contract
    assert "change_pct" in out
    assert "direction" in out
    assert "window_label" in out
    assert "tag" in out
    # New volatility fields
    assert "tier" in out and out["tier"] in {"Typical", "Notable", "Unusual", "Extreme"}
    assert "z_score" in out
    assert "sigma_daily_pct" in out
    assert "expected_band_pct" in out
    # Sparkline is trimmed to the chosen window (max 46 = 45+1), not the full 200
    assert len(out["prices"]) <= 46


def test_build_price_action_handles_empty_history():
    """Empty price array → honest empty state, all new fields present as None."""
    out = _build_price_action(
        recent_prices=[],
        current_price=100.0,
        earnings_dates=[],
        news=None,
    )
    assert out["prices"] == []
    assert out["tier"] == "Typical"
    assert out["z_score"] is None
    assert out["sigma_daily_pct"] is None
    assert out["expected_band_pct"] is None


def test_build_price_action_schema_validates():
    """Even the empty path must produce a Pydantic-valid PriceActionResponse."""
    from app.schemas.ticker_report import PriceActionResponse
    out = _build_price_action(
        recent_prices=_gentle_walk(200),
        current_price=110.0,
        earnings_dates=[],
        news=None,
    )
    # Pydantic strips _news_headlines; that's fine — it's the iOS contract.
    out_filtered = {k: v for k, v in out.items() if not k.startswith("_")}
    out_filtered["narrative"] = "test"  # required non-null in schema
    PriceActionResponse.model_validate(out_filtered)


def test_overall_assessment_averages_card_ratings():
    """The final score (overall_assessment.average_rating) is the mean of
    the four card star ratings — recomputed from the deterministic cards,
    not from any AI output."""
    from app.services.agents.ticker_report_data_collector import (
        _overall_assessment_from_cards,
    )
    cards = [
        {"star_rating": 4},  # Profitability
        {"star_rating": 3},  # Growth
        {"star_rating": 3},  # Valuation
        {"star_rating": 5},  # Health  (with new CR-aware blend)
    ]
    out = _overall_assessment_from_cards(cards, ai_text=None)
    assert out["average_rating"] == 3.8
    assert out["strong_count"] == 2   # Profitability=4 and Health=5
    assert out["weak_count"] == 0


# ── Wall Street Consensus: hedge-fund smart-money passthrough ─────────
# NAMING: "hedge fund" / `hedge_fund_*` = FMP 13F institutional data; the iOS UI
# labels it "Institutions" (SmartMoneyTab.hedgeFunds = "Institutions").


def _monthly_prices_12() -> list[dict]:
    return [{"month": f"{m:02d}/2025", "price": 200.0 + m} for m in range(1, 13)]


def test_wall_street_consensus_carries_hedge_fund_smart_money():
    """The report's WS Consensus must pass the Holders quarterly smart-money
    payload through verbatim (snake_case) so the iOS Institutions chart +
    net-flow badge mirror the Holders tab. Guards the nested shape iOS
    decodes via SmartMoneyDataDTO."""
    from app.schemas.holders import (
        HoldersResponse,
        SmartMoneyDataSchema,
        SmartMoneyFlowDataPointSchema,
        SmartMoneyFlowSummarySchema,
        StockPriceDataPointSchema,
    )
    from app.schemas.ticker_report import WallStreetConsensusResponse
    from app.services.agents.ticker_report_data_collector import (
        _build_wall_street_sections,
    )

    hedge = SmartMoneyDataSchema(
        tab="Institutions",
        price_data=[StockPriceDataPointSchema(month="Q3\n'25", price=210.0)],
        daily_prices=[],
        flow_data=[
            SmartMoneyFlowDataPointSchema(
                month="Q3\n'25", buy_volume=12.0, sell_volume=4.0, has_activity=True
            )
        ],
        summary=SmartMoneyFlowSummarySchema(
            total_net_flow=8.0, total_buy=12.0, total_sell=4.0,
            is_positive=True, period_description="2-Year",
        ),
    )
    holders = HoldersResponse(symbol="AAPL", hedge_funds_data=hedge)

    _, consensus = _build_wall_street_sections(
        analyst=None, holders=holders, current_price=215.0,
        fair_value=None, monthly_prices=_monthly_prices_12(),
    )

    sm = consensus["hedge_fund_smart_money"]
    assert sm is not None, "holders present → smart-money payload must be populated"
    assert set(sm.keys()) >= {"tab", "price_data", "daily_prices", "flow_data", "summary"}
    assert sm["summary"]["period_description"] == "2-Year"
    assert sm["flow_data"][0]["buy_volume"] == 12.0
    assert sm["flow_data"][0]["has_activity"] is True

    # The whole consensus block must still validate (note filled by AI later).
    consensus["wall_street_insight"] = "test note"
    WallStreetConsensusResponse.model_validate(consensus)


def test_wall_street_consensus_smart_money_none_without_holders():
    """Legacy / holders-missing path: optional field is None and the
    block still validates (iOS falls back to the monthly chart)."""
    from app.schemas.ticker_report import WallStreetConsensusResponse
    from app.services.agents.ticker_report_data_collector import (
        _build_wall_street_sections,
    )

    _, consensus = _build_wall_street_sections(
        analyst=None, holders=None, current_price=215.0,
        fair_value=None, monthly_prices=_monthly_prices_12(),
    )
    assert consensus["hedge_fund_smart_money"] is None
    # momentum_maintains + analyst distribution are forwarded (0 in the
    # analyst-less path) and validate.
    assert consensus["momentum_maintains"] == 0
    assert consensus["analyst_buy"] == 0 and consensus["analyst_strong_sell"] == 0
    consensus["wall_street_insight"] = None
    WallStreetConsensusResponse.model_validate(consensus)


# ── Wall Street Consensus: honest empty state for analyst targets ─────


def _analyst_with_targets(
    *, low: float = 200.0, avg: float = 260.0, high: float = 320.0,
    current: float = 215.0,
):
    """Minimal real AnalystAnalysisResponse carrying a price-target range."""
    from app.schemas.analyst import (
        AnalystActionsSummary,
        AnalystAnalysisResponse,
        AnalystConsensus,
        AnalystPriceTarget,
    )

    return AnalystAnalysisResponse(
        symbol="AAPL",
        total_analysts=30,
        updated_date="2026-01-01",
        consensus=AnalystConsensus.BUY,
        target_price=avg,
        target_upside=round((avg - current) / current * 100, 1),
        distributions=[],
        price_target=AnalystPriceTarget(
            low_price=low, average_price=avg, high_price=high, current_price=current,
        ),
        momentum_data=[],
        net_positive=20,
        net_negative=2,
        actions_summary=AnalystActionsSummary(upgrades=5, maintains=10, downgrades=1),
        actions=[],
    )


def test_wall_street_consensus_emits_real_analyst_targets():
    """With real FMP coverage, the consensus block carries the exact
    analyst Low / Avg / High — no rounding-away, no substitution."""
    from app.schemas.ticker_report import WallStreetConsensusResponse
    from app.services.agents.ticker_report_data_collector import (
        _build_wall_street_sections,
    )

    _, consensus = _build_wall_street_sections(
        analyst=_analyst_with_targets(low=200.0, avg=260.0, high=320.0),
        holders=None, current_price=215.0, fair_value=None,
        monthly_prices=_monthly_prices_12(),
    )
    assert consensus["target_price"] == 260.0
    assert consensus["low_target"] == 200.0
    assert consensus["high_target"] == 320.0
    consensus["wall_street_insight"] = None
    WallStreetConsensusResponse.model_validate(consensus)


def test_wall_street_consensus_targets_null_without_analyst_coverage():
    """Honest empty state: with no analyst coverage the three targets are
    null — NOT fabricated from current price or DCF fair value. The block
    must still validate (iOS renders a 'no analyst targets' state). A
    fair_value is supplied to prove we no longer fall back to it here."""
    from app.schemas.ticker_report import WallStreetConsensusResponse
    from app.services.agents.ticker_report_data_collector import (
        _build_wall_street_sections,
    )

    _, consensus = _build_wall_street_sections(
        analyst=None, holders=None, current_price=215.0,
        fair_value=300.0, monthly_prices=_monthly_prices_12(),
    )
    assert consensus["target_price"] is None
    assert consensus["low_target"] is None
    assert consensus["high_target"] is None
    consensus["wall_street_insight"] = None
    WallStreetConsensusResponse.model_validate(consensus)
