"""Unit tests for the dossier Phase B (AI-driven research overrides).

Pins the validation gates so the operator can trust the audit log:
  - Confidence threshold (low → reject)
  - Source-count threshold (<2 sources → reject)
  - Numeric bounds on TAM and CAGR
  - Sanity-vs-Phase-A divergence (warn at 3x, reject at 10x)
  - Kill switch (env var) skips entire phase

No network — Gemini responses are constructed inline.
"""

from __future__ import annotations

import pytest

from app.services.industry_override_service import IndustryOverrideService


def _ok_payload(**overrides) -> dict:
    """Baseline Gemini response that passes every validation gate."""
    base = {
        "current_tam_b": 791.0,
        "future_tam_b": 1275.0,
        "current_year": "2025",
        "future_year": "2030",
        "cagr_5y_pct": 10.0,
        "source_label": "SIA / WSTS Global Semi Forecast 2025",
        "research_notes": "Median across SIA, WSTS, McKinsey.",
        "sources_cited": [
            {"publisher": "SIA", "title": "Global Semi Forecast", "url": "https://semiconductors.org/x"},
            {"publisher": "WSTS", "title": "Spring 2025", "url": "https://wsts.org/y"},
        ],
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_validate_response_accepts_clean_response():
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(), phase_a_tam=117.0)
    # 791/117 ≈ 6.8x → warn but accept
    assert v["status"] == "ok"
    assert v["warn"] is True


def test_validate_response_no_warn_when_phase_a_close():
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(current_tam_b=600.0), phase_a_tam=500.0)
    # 600/500 = 1.2x → no warn
    assert v["status"] == "ok"
    assert v["warn"] is False


def test_validate_response_no_phase_a_baseline():
    """First-deploy scenario: no Phase A row exists yet for this industry.
    Divergence check is skipped (no baseline) so no warn fires."""
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(), phase_a_tam=None)
    assert v["status"] == "ok"
    assert v["warn"] is False


def test_validate_response_rejects_low_confidence():
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(confidence="low"), phase_a_tam=100.0)
    assert v["status"] == "rejected_low_confidence"


def test_validate_response_rejects_single_source():
    svc = IndustryOverrideService()
    p = _ok_payload(sources_cited=[
        {"publisher": "SIA", "title": "x", "url": "https://x"},
    ])
    v = svc._validate_response(p, phase_a_tam=100.0)
    assert v["status"] == "rejected_validation"
    assert "1 valid source" in v["reason"]


def test_validate_response_rejects_zero_sources():
    """sources_cited empty or missing → reject."""
    svc = IndustryOverrideService()
    v1 = svc._validate_response(_ok_payload(sources_cited=[]), phase_a_tam=100.0)
    v2 = svc._validate_response(
        {**_ok_payload(), **{"sources_cited": None}}, phase_a_tam=100.0,
    )
    assert v1["status"] == "rejected_validation"
    assert v2["status"] == "rejected_validation"


def test_validate_response_rejects_out_of_bounds_cagr():
    """CAGR > 50% → reject (no industry sustains that)."""
    svc = IndustryOverrideService()
    v_high = svc._validate_response(_ok_payload(cagr_5y_pct=75.0), phase_a_tam=100.0)
    v_low = svc._validate_response(_ok_payload(cagr_5y_pct=-50.0), phase_a_tam=100.0)
    assert v_high["status"] == "rejected_validation"
    assert v_low["status"] == "rejected_validation"


def test_validate_response_rejects_tam_too_large():
    """TAM > $50T → impossible (~2x US GDP)."""
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(current_tam_b=60_000.0), phase_a_tam=100.0)
    assert v["status"] == "rejected_validation"


def test_validate_response_rejects_tam_too_small():
    """TAM < $1B → suspect for a listed-equity industry."""
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(current_tam_b=0.5), phase_a_tam=100.0)
    assert v["status"] == "rejected_validation"


def test_validate_response_rejects_future_tam_explosion():
    """future_tam > 5x current_tam → would imply >38% CAGR, reject."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_tam_b=100.0, future_tam_b=1000.0),
        phase_a_tam=100.0,
    )
    assert v["status"] == "rejected_validation"
    assert "5x" in v["reason"] or "5.0x" in v["reason"]


def test_validate_response_rejects_sanity_10x_divergence():
    """When Gemini's TAM is >10x Phase A's TAM, reject as a hallucination.

    Use a future_tam below the 5x bounds check so we exercise the
    sanity gate specifically (not the prior numeric-bounds gate)."""
    svc = IndustryOverrideService()
    # Phase A=10, Gemini=200 → 20x divergence; future_tam=300 (1.5x) → bounds OK
    v = svc._validate_response(
        _ok_payload(current_tam_b=200.0, future_tam_b=300.0, cagr_5y_pct=8.4),
        phase_a_tam=10.0,
    )
    assert v["status"] == "rejected_sanity"
    assert "divergence" in v["reason"].lower()


def test_validate_response_rejects_invalid_years():
    """Future year ≤ current year → reject."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_year="2030", future_year="2025"),
        phase_a_tam=100.0,
    )
    assert v["status"] == "rejected_validation"


def test_validate_response_rejects_unparseable_years():
    """Year strings that aren't 4-digit ints → reject."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_year="soonish"),
        phase_a_tam=100.0,
    )
    assert v["status"] == "rejected_validation"


def test_validate_response_rejects_non_numeric_tam():
    """Gemini sometimes returns "$791B" instead of 791. Validation rejects."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_tam_b="$791B"), phase_a_tam=100.0,
    )
    assert v["status"] == "rejected_validation"


@pytest.mark.asyncio
async def test_kill_switch_skips_phase_b(monkeypatch):
    """When INDUSTRY_OVERRIDE_AI_ENABLED=False, every curated industry
    returns status='skipped_kill_switch' WITHOUT calling Gemini."""
    from app.services import industry_override_service as mod
    from app.services.industry_override_service import IndustryOverrideService

    # Disable via monkeypatch (don't mutate real settings)
    monkeypatch.setattr(mod.settings, "INDUSTRY_OVERRIDE_AI_ENABLED", False)

    # Also short-circuit the audit-log writer so we don't need Supabase
    # in this unit test.
    svc = IndustryOverrideService()
    monkeypatch.setattr(svc, "_write_audit_log", lambda *a, **kw: None)

    summary = await svc.refresh_all_overrides()
    counts = summary["status_counts"]
    assert counts.get("skipped_kill_switch", 0) == len(mod.CURATED_OVERRIDE_INDUSTRIES)
    # No other statuses should appear
    assert set(counts.keys()) == {"skipped_kill_switch"}
    assert summary["total_tokens_used"] == 0


def test_curated_list_industries_unique():
    """Catch accidental duplicates if someone adds an industry that's
    already in the list."""
    from app.services.industry_override_service import CURATED_OVERRIDE_INDUSTRIES
    industries = [i for i, _ in CURATED_OVERRIDE_INDUSTRIES]
    assert len(industries) == len(set(industries)), \
        f"Duplicate industries in CURATED_OVERRIDE_INDUSTRIES: {industries}"
