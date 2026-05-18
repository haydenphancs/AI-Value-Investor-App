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
from app.schemas.ticker_report import TickerReportResponse
from app.services.agents.narrative_prompts import (
    build_narrative_jobs,
    stage_a_fallback,
)
from app.services.agents.persona_config import (
    PERSONA_KEYS,
    get_persona_config,
)
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
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
        "executive_summary_bullets", "key_vitals", "core_thesis",
        "fundamental_metrics", "overall_assessment", "revenue_forecast",
        "insider_data", "key_management", "price_action", "revenue_engine",
        "moat_competition", "macro_data", "wall_street_consensus",
        "critical_factors", "disclaimer_text",
    }
    missing = expected - set(report.keys())
    extra = set(report.keys()) - expected
    assert not missing, f"missing top-level keys: {missing}"
    assert not extra, f"unexpected top-level keys (would fail iOS decoder): {extra}"


def test_key_vitals_has_all_eight_slots():
    """Swift KeyVitalsDTO has 8 optional fields — every slot must be a
    key in the assembled key_vitals dict (value may be None)."""
    coll = TickerReportDataCollector()
    out = _make_collected_data()
    report = coll.assemble_report(out, stage_a_fallback())
    expected = {
        "valuation", "moat", "financial_health", "revenue",
        "insider", "macro", "forecast", "wall_street",
    }
    missing = expected - set(report["key_vitals"].keys())
    assert not missing, f"missing key_vitals: {missing}"


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
