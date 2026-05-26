"""
Unit tests for competitor_intel_service.

Covers the pure-helper math (ticker normalization, source-label
derivation) and the FMP-validation pass (using injected fake clients
since the testing rule forbids hitting live FMP / Gemini).

No Supabase, no network.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.competitor_intel_service import (
    CompetitorIntelService,
    _COMPETITOR_MAX_N,
    _derive_source_label,
    _normalize_ticker,
)


# ── Ticker normalization ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MSFT", "MSFT"),
        ("msft", "MSFT"),
        ("  MSFT  ", "MSFT"),
        ("$MSFT", "MSFT"),
        ("NASDAQ:MSFT", "MSFT"),
        ("NYSE:BRK.B", "BRK.B"),
        ("MSFT (Microsoft Corporation)", "MSFT"),
        ("BRK.B", "BRK.B"),
        ("BRK-B", "BRK-B"),
    ],
)
def test_normalize_ticker_accepts_common_decorations(raw, expected):
    assert _normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "    ",
        "lowercase only",
        "TOOLONGTICKERNAME",
        "!!!",
        None,
        123,
    ],
)
def test_normalize_ticker_rejects_garbage(raw):
    assert _normalize_ticker(raw) == ""


# ── Source label derivation ─────────────────────────────────────────────


def test_derive_source_label_dedupes_and_capitalizes():
    sources = [
        {"publisher": "reuters"},
        {"publisher": "reuters"},  # dup
        {"publisher": "bloomberg"},
        {"publisher": "sec"},
    ]
    assert _derive_source_label(sources) == ["Reuters", "Bloomberg", "Sec"]


def test_derive_source_label_caps_at_four():
    sources = [{"publisher": f"pub{i}"} for i in range(10)]
    assert len(_derive_source_label(sources)) == 4


def test_derive_source_label_empty_input():
    assert _derive_source_label([]) == []
    assert _derive_source_label(None) == []  # type: ignore[arg-type]


def test_derive_source_label_skips_blanks_and_non_dicts():
    sources = [
        {"publisher": ""},
        {"publisher": "  "},
        "not a dict",
        {"publisher": "reuters"},
    ]
    assert _derive_source_label(sources) == ["Reuters"]  # type: ignore[arg-type]


# ── FMP validation pass ─────────────────────────────────────────────────


class _FakeFMP:
    """Stand-in for FMPClient.get_company_profiles_batch / get_company_profile.

    Initialize with a dict of ticker → profile dict; absent tickers
    return as missing (FMP doesn't recognize them).
    """

    def __init__(self, profiles: Dict[str, Dict[str, Any]]):
        self._profiles = profiles

    async def get_company_profiles_batch(
        self, tickers: List[str]
    ) -> List[Dict[str, Any]]:
        return [self._profiles[t] for t in tickers if t in self._profiles]

    async def get_company_profile(
        self, ticker: str
    ) -> Optional[Dict[str, Any]]:
        return self._profiles.get(ticker, {})


def _make_service_with_fake_fmp(profiles: Dict[str, Dict[str, Any]]) -> CompetitorIntelService:
    svc = CompetitorIntelService()
    svc._fmp = _FakeFMP(profiles)  # type: ignore[assignment]
    return svc


@pytest.mark.asyncio
async def test_fmp_validate_drops_unknown_tickers():
    svc = _make_service_with_fake_fmp({
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        # "FAKE1" intentionally absent
    })
    validated, rejected = await svc._fmp_validate(["MSFT", "FAKE1"], focal="ORCL")
    assert [v["ticker"] for v in validated] == ["MSFT"]
    assert {"ticker": "FAKE1", "reason": "rejected_unknown_ticker"} in rejected


@pytest.mark.asyncio
async def test_fmp_validate_drops_zero_or_null_mkt_cap():
    svc = _make_service_with_fake_fmp({
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "ZERO": {"symbol": "ZERO", "mktCap": 0},
        "NULL": {"symbol": "NULL", "mktCap": None},
        "NEG":  {"symbol": "NEG",  "mktCap": -100},
    })
    validated, rejected = await svc._fmp_validate(
        ["MSFT", "ZERO", "NULL", "NEG"], focal="ORCL",
    )
    assert [v["ticker"] for v in validated] == ["MSFT"]
    reasons = {r["ticker"]: r["reason"] for r in rejected}
    assert reasons == {
        "ZERO": "rejected_no_mktcap",
        "NULL": "rejected_no_mktcap",
        "NEG": "rejected_no_mktcap",
    }


@pytest.mark.asyncio
async def test_fmp_validate_keeps_small_cap_survivors():
    """Phase 2 has NO $27.3B floor. A $10B niche rival with verifiable
    revenue overlap must survive (Snowflake-vs-Oracle scenario).
    """
    svc = _make_service_with_fake_fmp({
        "BIG":   {"symbol": "BIG",   "mktCap": 500_000_000_000},
        "SMALL": {"symbol": "SMALL", "mktCap":  10_000_000_000},
    })
    validated, rejected = await svc._fmp_validate(["BIG", "SMALL"], focal="X")
    assert [v["ticker"] for v in validated] == ["BIG", "SMALL"]
    assert rejected == []


# ── End-to-end extract+validate with fake Gemini ───────────────────────


class _FakeGemini:
    """Returns whatever response dict is given at construction."""

    def __init__(self, response: Dict[str, Any]):
        self._response = response

    async def generate_grounded_research(self, **_: Any) -> Dict[str, Any]:
        return self._response


def _gemini_response_with_competitors(tickers: List[str]) -> Dict[str, Any]:
    """Build a synthetic Gemini grounded-research response payload."""
    competitors_json = ",\n    ".join(
        '{{"ticker":"{t}","name":"{t} Inc","segment_overlap":"x","source_citation":"10-K"}}'.format(t=t)
        for t in tickers
    )
    text = (
        "Some intro prose about the competitors.\n\n"
        "```json\n"
        "{\n"
        f'  "competitors": [\n    {competitors_json}\n  ],\n'
        '  "confidence": "high"\n'
        "}\n"
        "```\n"
    )
    return {
        "text": text,
        "tokens_used": 1234,
        "grounding_sources": [
            {"publisher": "reuters", "title": "reuters.com", "uri": "https://reuters.com/x"},
        ],
        "search_queries": ["who competes with oracle"],
        "model": "gemini-2.5-flash",
    }


@pytest.mark.asyncio
async def test_extract_and_validate_returns_validated_list_under_7():
    """Gemini returns 5 → no trim, all 5 returned (no mkt-cap floor applied)."""
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["MSFT", "AMZN", "CRM", "SAP", "IBM"])
    )
    svc._fmp = _FakeFMP({  # type: ignore[assignment]
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "AMZN": {"symbol": "AMZN", "mktCap": 2_000_000_000_000},
        "CRM":  {"symbol": "CRM",  "mktCap":   300_000_000_000},
        "SAP":  {"symbol": "SAP",  "mktCap":   200_000_000_000},
        "IBM":  {"symbol": "IBM",  "mktCap":   180_000_000_000},
    })
    result = await svc._extract_and_validate(
        ticker="ORCL",
        profile={"companyName": "Oracle", "sector": "Tech", "industry": "Software"},
    )
    assert result.status == "applied"
    assert result.validated_tickers == ["MSFT", "AMZN", "CRM", "SAP", "IBM"]
    assert result.rejected == []


@pytest.mark.asyncio
async def test_extract_and_validate_trims_only_when_over_7():
    """Gemini returns 9 → trim to 7 by mkt-cap, lowest 2 dropped to
    `rejected` with reason `trimmed_to_7_by_mkt_cap`.
    """
    nine = ["A", "B", "C", "D", "E", "F", "G", "H", "I"]
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(_gemini_response_with_competitors(nine))  # type: ignore[assignment]
    # Assign descending mkt caps so order is A > B > ... > I.
    profiles = {
        t: {"symbol": t, "mktCap": (10 - i) * 10_000_000_000}
        for i, t in enumerate(nine)
    }
    svc._fmp = _FakeFMP(profiles)  # type: ignore[assignment]

    result = await svc._extract_and_validate(
        ticker="X",
        profile={"companyName": "X Corp"},
    )
    assert result.status == "applied_with_rejections"
    assert result.validated_tickers == ["A", "B", "C", "D", "E", "F", "G"]
    assert len(result.validated_tickers) == _COMPETITOR_MAX_N
    trimmed = [r for r in result.rejected if "trimmed_to" in r["reason"]]
    assert {r["ticker"] for r in trimmed} == {"H", "I"}


@pytest.mark.asyncio
async def test_extract_and_validate_drops_focal_self_suggestion():
    """If Gemini accidentally lists the focal itself, it gets removed
    and tagged in the audit's rejected list (reason='is_focal').
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["ORCL", "MSFT", "AMZN"])
    )
    svc._fmp = _FakeFMP({  # type: ignore[assignment]
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "AMZN": {"symbol": "AMZN", "mktCap": 2_000_000_000_000},
    })
    result = await svc._extract_and_validate(
        ticker="ORCL",
        profile={"companyName": "Oracle"},
    )
    assert "ORCL" not in result.validated_tickers
    assert result.validated_tickers == ["MSFT", "AMZN"]
    assert any(r["reason"] == "is_focal" and r["ticker"] == "ORCL"
               for r in result.rejected)


@pytest.mark.asyncio
async def test_extract_and_validate_handles_missing_json_fence():
    """Plain-text Gemini response with no ```json``` block → gemini_error
    status, no validated tickers, but raw_text preserved in audit.
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini({  # type: ignore[assignment]
        "text": "no code fence here",
        "tokens_used": 50,
        "grounding_sources": [],
        "search_queries": [],
        "model": "gemini-2.5-flash",
    })
    svc._fmp = _FakeFMP({})  # type: ignore[assignment]
    result = await svc._extract_and_validate(
        ticker="X", profile={"companyName": "X"},
    )
    assert result.status == "gemini_error"
    assert result.validated_tickers == []
    assert "no ```json``` code fence" in result.raw_response.get("error", "")


@pytest.mark.asyncio
async def test_extract_and_validate_handles_all_rejected():
    """Gemini returns plausible-shape tickers but FMP doesn't recognize
    any → rejected_no_validated, no cache write should follow.
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["FAKE1", "FAKE2"])
    )
    svc._fmp = _FakeFMP({})  # empty: FMP recognizes nothing  # type: ignore[assignment]
    result = await svc._extract_and_validate(
        ticker="X", profile={"companyName": "X"},
    )
    assert result.status == "rejected_no_validated"
    assert result.validated_tickers == []
    rejected_reasons = {r["reason"] for r in result.rejected}
    assert "rejected_unknown_ticker" in rejected_reasons


@pytest.mark.asyncio
async def test_extract_and_validate_dedupes_repeated_suggestions():
    """Gemini sometimes lists the same ticker twice — silently dedupe
    before validation.
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["MSFT", "MSFT", "AMZN"])
    )
    svc._fmp = _FakeFMP({  # type: ignore[assignment]
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "AMZN": {"symbol": "AMZN", "mktCap": 2_000_000_000_000},
    })
    result = await svc._extract_and_validate(
        ticker="X", profile={"companyName": "X"},
    )
    assert result.validated_tickers == ["MSFT", "AMZN"]
