"""Unit tests for the dossier Phase B (AI-driven research overrides).

Pins the validation gates so the operator can trust the audit log:
  - Numeric bounds on TAM and CAGR
  - Year sanity (parseable, future > current)
  - Phase-A floor: Gemini TAM < Phase A → reject (override only RAISES)
  - Kill switch (env var) skips entire phase

Per migration 053, confidence and source-count gates were dropped —
operators review the audit log instead.

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
    }
    base.update(overrides)
    return base


def test_validate_response_accepts_clean_response():
    """Gemini TAM (791) above Phase A (117) — accepted. The 6.8x ratio
    is logged but no longer gated."""
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(), phase_a_tam=117.0)
    assert v["status"] == "ok"


def test_validate_response_accepts_when_phase_a_close():
    """Gemini TAM (600) above Phase A (500) — accepted, no divergence flag."""
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(current_tam_b=600.0), phase_a_tam=500.0)
    assert v["status"] == "ok"


def test_validate_response_no_phase_a_baseline():
    """First-deploy scenario: no Phase A row exists yet — divergence
    check is skipped entirely."""
    svc = IndustryOverrideService()
    v = svc._validate_response(_ok_payload(), phase_a_tam=None)
    assert v["status"] == "ok"


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


def test_validate_response_rejects_below_phase_a():
    """When Gemini's TAM is below Phase A, reject — the override exists
    to FIX undercounting and only raises TAM, never lowers it."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_tam_b=80.0, future_tam_b=120.0),
        phase_a_tam=100.0,
    )
    assert v["status"] == "rejected_below_phase_a"
    assert "phase a" in v["reason"].lower()


def test_validate_response_skips_floor_for_broad_fred_phase_a():
    """A BROAD FRED GDP proxy (e.g. BEA all-manufacturing) OVERCOUNTS the
    industry, so the floor must NOT block a smaller, accurate global figure.
    Gemini TAM below Phase A is ACCEPTED when Phase A is FRED-sourced — this is
    the Consumer Electronics / Beverages case (Phase A ≈ $2.9T all-manufacturing
    GDP, real global ≈ $0.9T)."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_tam_b=887.0, future_tam_b=1200.0),
        phase_a_tam=2896.0,
        phase_a_label="BEA Manufacturing GDP (via FRED)",
    )
    assert v["status"] == "ok"


def test_validate_response_keeps_floor_for_census_phase_a():
    """A precise US Census Phase A is a LOWER BOUND for the global market, so a
    below-Census Gemini figure is under-researched → keep the floor (reject).
    This is the Software - Infrastructure case (Census $526B, Gemini $198B)."""
    svc = IndustryOverrideService()
    v = svc._validate_response(
        _ok_payload(current_tam_b=198.0, future_tam_b=260.0),
        phase_a_tam=525.9,
        phase_a_label="US Census AIES — Software publishers (NAICS 5112)",
    )
    assert v["status"] == "rejected_below_phase_a"


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
