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
    CriticalFactorResponse,
    RevenueForecastResponse,
    TickerReportResponse,
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
    _build_fundamental_metrics_from_snapshots,
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
        "agent", "quality_score", "executive_summary_text",
        "executive_summary_bullets", "core_thesis",
        "fundamental_metrics", "overall_assessment", "revenue_forecast",
        "insider_data", "key_management", "price_action", "revenue_engine",
        "moat_competition", "macro_data", "wall_street_consensus",
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


def test_pros_cons_capped_at_four():
    """Even if the AI returns 7 bull_case bullets, assemble_report
    truncates to 4 (Phase 2 invariant)."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    shell = stage_a_fallback()
    shell["core_thesis"] = {
        "bull_case": [f"bull {i}" for i in range(7)],
        "bear_case": [f"bear {i}" for i in range(6)],
    }
    report = coll.assemble_report(out, shell)
    assert len(report["core_thesis"]["bull_case"]) <= 4
    assert len(report["core_thesis"]["bear_case"]) <= 4


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


@pytest.mark.parametrize("persona_key", sorted(PERSONA_KEYS))
def test_every_persona_has_narrative_lens(persona_key):
    """Phase 2 added narrative_lens — each persona must populate it."""
    p = get_persona_config(persona_key)
    assert p.narrative_lens, f"{persona_key} missing narrative_lens"
    assert len(p.narrative_lens) >= 20, (
        f"{persona_key} narrative_lens too short to give meaningful voice"
    )


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
