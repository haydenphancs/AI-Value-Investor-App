"""Unit tests for the web-grounded geopolitical macro scan.

Math/logic tests (Category 1 per .claude/rules/testing.md) — no live Gemini,
no Supabase. The grounded client is stubbed and the Supabase I/O methods are
monkeypatched out, so we exercise: parsing/validation, the hallucination guard,
and keep-last-good on a failed refresh.
"""
from __future__ import annotations

import pytest

from app.schemas.ticker_report import MacroRiskFactorResponse, SourceCitationResponse
from app.services.geopolitical_macro_service import GeopoliticalMacroService


class _StubGemini:
    def __init__(self, resp):
        self._resp = resp
        self.calls = 0

    async def generate_grounded_research(self, **kwargs):
        self.calls += 1
        return self._resp


def _svc_with(resp) -> GeopoliticalMacroService:
    s = GeopoliticalMacroService()
    s._gemini = _StubGemini(resp)
    return s


# ── _parse_factors: validation + normalization ───────────────────────


def test_parse_factors_validates_and_normalizes():
    s = GeopoliticalMacroService()
    grounding = [{"title": "Reuters", "uri": "https://r.com", "publisher": "reuters.com"}]
    raw = [
        {"category": "geopolitical", "title": "Russia-Ukraine War",
         "description": "Ongoing war disrupts energy.", "severity": "severe",
         "trend": "stable", "risk_group": "geopolitical"},
        {"category": "BOGUS", "title": "X", "description": "y",
         "severity": "low", "trend": "stable", "risk_group": "zzz"},   # low → skipped
        {"title": "", "description": "no title", "severity": "high"},  # no title → skipped
    ]
    out = s._parse_factors(raw, grounding)
    assert len(out) == 1
    f = out[0]
    assert f["category"] == "geopolitical"
    assert f["severity"] == "severe"
    assert f["impact"] == 0.8                # severity ÷ 5
    assert f["_source"] == "grounded"
    assert f["_risk_group"] == "geopolitical"
    assert f["sources"] == grounding          # scan-level citations attached


def test_parse_factors_unknown_category_and_risk_group_fall_back():
    s = GeopoliticalMacroService()
    grounding = [{"title": "AP", "uri": "https://ap.com", "publisher": "ap.com"}]
    raw = [{"category": "pandemic", "title": "New Pandemic",
            "description": "Public-health shock.", "severity": "high",
            "trend": "worsening", "risk_group": "unknown"}]
    out = s._parse_factors(raw, grounding)
    assert out[0]["category"] == "geopolitical"   # unknown category → geopolitical
    assert out[0]["_risk_group"] == "geopolitical"  # unknown risk_group → geopolitical


# ── _do_grounded: hallucination guard + applied ──────────────────────


@pytest.mark.asyncio
async def test_do_grounded_hallucination_guard_requires_sources():
    # Valid JSON but NO grounding sources → we don't trust it → no_factors.
    resp = {
        "text": '```json\n{"factors":[{"category":"geopolitical","title":"War",'
                '"description":"d","severity":"high","trend":"stable","risk_group":"geopolitical"}]}\n```',
        "grounding_sources": [],
        "search_queries": [],
        "tokens_used": 10,
        "model": "m",
    }
    out = await _svc_with(resp)._do_grounded()
    assert out["status"] == "no_factors"
    assert out["factors"] == []


@pytest.mark.asyncio
async def test_do_grounded_applied_with_sources():
    resp = {
        "text": '```json\n{"factors":[{"category":"tariffs","title":"US-China Tariffs",'
                '"description":"New tariffs on imports.","severity":"high",'
                '"trend":"worsening","risk_group":"tariffs"}]}\n```',
        "grounding_sources": [{"title": "Reuters", "uri": "https://r.com", "publisher": "reuters.com"}],
        "search_queries": ["us china tariffs"],
        "tokens_used": 50,
        "model": "gemini-2.5-flash",
    }
    out = await _svc_with(resp)._do_grounded()
    assert out["status"] == "applied"
    assert len(out["factors"]) == 1
    assert out["factors"][0]["title"] == "US-China Tariffs"
    assert out["factors"][0]["sources"]


# ── _refresh: keep-last-good ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_keeps_last_good_on_error(monkeypatch):
    s = GeopoliticalMacroService()

    async def _err():
        return {"status": "gemini_error", "raw_response": {}}

    wrote = {"cache": False}
    monkeypatch.setattr(s, "_do_grounded", _err)
    monkeypatch.setattr(s, "_write_cache", lambda *a, **k: wrote.__setitem__("cache", True))
    monkeypatch.setattr(s, "_write_audit", lambda *a, **k: None)

    previous = [{"category": "geopolitical", "title": "Old War", "severity": "high"}]
    out = await s._refresh(run_id="t", previous=previous)
    assert out == previous            # kept last good
    assert wrote["cache"] is False    # never overwrote the cache with empty


# ── schema: optional sources field round-trips ───────────────────────


def test_macro_risk_factor_schema_accepts_and_omits_sources():
    # Without sources (deterministic factor) — still valid (Optional → None).
    f1 = MacroRiskFactorResponse(
        category="interest_rates", title="Elevated Long-Term Rates",
        impact=0.6, description="10Y at 4.8%.", trend="stable", severity="high",
    )
    assert f1.sources is None
    # With sources (grounded factor).
    f2 = MacroRiskFactorResponse(
        category="geopolitical", title="Russia-Ukraine War", impact=0.8,
        description="Active conflict.", trend="worsening", severity="severe",
        sources=[SourceCitationResponse(title="Reuters", uri="https://r.com", publisher="reuters.com")],
    )
    assert f2.sources and f2.sources[0].publisher == "reuters.com"
