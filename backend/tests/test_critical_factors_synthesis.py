"""Unit tests for the post-assembly cross-module critical-factors synthesis.

Math/logic tests (Category 1 per .claude/rules/testing.md) — no live Gemini,
no Supabase. The Gemini client is stubbed; we exercise the ungated macro block,
validation/normalization, the overwrite-on-success path, and keep-fallback on
incomplete/errored synthesis.
"""
from __future__ import annotations

import json

import pytest

from app.services.agents.narrative_prompts import (
    _clean_critical_factors,
    _format_macro_watch_block,
    synthesize_critical_factors,
)
from app.services.agents.persona_config import get_persona_config

PERSONA = get_persona_config("warren_buffett")


def _report():
    return {
        "symbol": "ORCL",
        "company_name": "Oracle Corporation",
        "core_thesis": {
            "bull_case": ["70.51% gross margin"],
            "bear_case": [
                "Free Cash Flow of -$394M is a concern",
                "Debt/Equity 4.21 is high",
            ],
        },
        # Stage A/B fallback already on the report:
        "critical_factors": [
            {"title": "Free Cash Flow", "description": "FCF negative.",
             "severity": "high", "watch": "Next earnings."},
        ],
        "macro_data": {
            "overall_threat_level": "elevated",  # NOT high+ → digest gates it out
            "risk_factors": [
                {"title": "US-China Tariffs", "category": "tariffs",
                 "severity": "high", "trend": "worsening"},
                {"title": "Sharp Rate Move", "category": "interest_rates",
                 "severity": "elevated", "trend": "worsening"},
            ],
        },
        "fundamental_metrics": [
            {"title": "Financial Health", "star_rating": 2,
             "metrics": [{"label": "Debt/Equity", "value": "4.21"}]},
        ],
        "overall_assessment": {"average_rating": 3.0, "strong_count": 2, "weak_count": 3},
        "wall_street_consensus": {"rating": "buy", "current_price": 213.0,
                                  "target_price": 250.0, "valuation_status": "Fairly Valued"},
    }


class _StubGemini:
    def __init__(self, text):
        self._text = text
        self.last_prompt = None

    async def generate_json(self, prompt, system_instruction=None):
        self.last_prompt = prompt
        return {"text": self._text}


# ── _format_macro_watch_block — UNGATED (unlike the thesis digest) ───


def test_macro_watch_block_is_ungated():
    block = _format_macro_watch_block(_report())
    assert "Overall macro threat: elevated" in block   # elevated would be gated out of the digest
    assert "US-China Tariffs" in block
    assert "Sharp Rate Move" in block


# ── _clean_critical_factors — validation / normalization ─────────────


def test_clean_critical_factors_validates_and_normalizes():
    raw = [
        {"title": "Competitive Moat", "description": "Moat narrowing vs AWS.",
         "severity": "HIGH", "watch": "Track cloud market share."},
        {"title": "", "description": "no title", "severity": "high", "watch": "x"},  # skipped
        {"title": "X", "description": "", "severity": "high"},                        # skipped
        {"title": "Fed & Rate Policy", "description": "Rates pressure multiples.",
         "severity": "bogus", "watch": "null"},   # sev → medium, watch "null" → None
    ]
    out = _clean_critical_factors(raw)
    assert len(out) == 2
    assert out[0]["title"] == "Competitive Moat"
    assert out[0]["severity"] == "high"            # normalized lowercase
    assert out[0]["watch"] == "Track cloud market share."
    assert out[1]["severity"] == "medium"          # bogus → medium
    assert out[1]["watch"] is None                 # "null" → None


# ── synthesize_critical_factors ──────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_overwrites_with_distinct_areas_on_success():
    report = _report()
    payload = {"critical_factors": [
        {"title": "Free Cash Flow", "severity": "high",
         "description": "FCF -$394M strains owner earnings.",
         "watch": "Confirm FCF swings positive next 10-Q."},
        {"title": "Geopolitical Exposure", "severity": "medium",
         "description": "US-China tariffs threaten cloud demand.",
         "watch": "Escalation in US-China tariffs."},
        {"title": "Fed & Rate Policy", "severity": "medium",
         "description": "Debt/Equity 4.21 amplifies rate risk.",
         "watch": "The next Fed rate decision."},
    ]}
    gem = _StubGemini(json.dumps(payload))
    await synthesize_critical_factors(report, PERSONA, gem, "EVIDENCE D/E 4.21")

    cf = report["critical_factors"]
    assert [c["title"] for c in cf] == [
        "Free Cash Flow", "Geopolitical Exposure", "Fed & Rate Policy",
    ]
    # the prompt carried the BEAR CASE + the (ungated) macro/geopolitical block
    assert "BEAR CASE" in gem.last_prompt
    assert "Debt/Equity 4.21 is high" in gem.last_prompt
    assert "MACRO / GEOPOLITICAL" in gem.last_prompt
    assert "US-China Tariffs" in gem.last_prompt


@pytest.mark.asyncio
async def test_synthesize_keeps_fallback_when_incomplete():
    report = _report()
    fallback = report["critical_factors"]
    gem = _StubGemini(json.dumps({"critical_factors": [
        {"title": "Only One", "severity": "high", "description": "single.", "watch": "x"},
    ]}))
    await synthesize_critical_factors(report, PERSONA, gem, "EV")
    assert report["critical_factors"] is fallback   # <2 valid → keep Stage A/B


@pytest.mark.asyncio
async def test_synthesize_keeps_fallback_on_error():
    report = _report()
    fallback = report["critical_factors"]

    class _Boom:
        async def generate_json(self, prompt, system_instruction=None):
            raise RuntimeError("quota")

    await synthesize_critical_factors(report, PERSONA, _Boom(), "EV")
    assert report["critical_factors"] is fallback
