"""Regression guard: research_service's report derivations must be None-safe
against score-input slots being None.

The internal score layer is keyed `_scoring_inputs` (legacy alias `key_vitals`
in reports stored before the key rename — those user-history rows are never
invalidated, so both names must keep working). Every slot is Optional
(valuation/moat/... can be None), so a slot may legitimately be None. The
derivations use `(data.get(...) or {}).get("valuation") or {}` — the `or {}`
guards against a None slot that would otherwise raise
`AttributeError: 'NoneType' object has no attribute 'get'` and fail the whole
report. These tests pin the fix for BOTH the current key and the legacy alias.
"""

from __future__ import annotations

import pytest

from app.services.research_service import ResearchService


def _svc() -> ResearchService:
    # Skip __init__ (it builds Supabase/Gemini/FMP clients). The derivation
    # helpers below only read the `data` dict — no instance state needed.
    return ResearchService.__new__(ResearchService)


@pytest.mark.parametrize("key", ["_scoring_inputs", "key_vitals"])
def test_derive_recommendation_handles_none_valuation_slot(key):
    svc = _svc()
    # The exact production crash case: score layer present, valuation slot None.
    data = {"quality_score": 62, key: {"valuation": None}}
    assert svc._derive_recommendation(data) in {"Buy", "Sell", "Hold", "Watch"}
    # Score layer entirely absent -> falls back to score-only logic.
    assert svc._derive_recommendation({"quality_score": 30}) == "Sell"
    # Score layer is None.
    assert svc._derive_recommendation({"quality_score": 70, key: None}) == "Hold"


@pytest.mark.parametrize("key", ["_scoring_inputs", "key_vitals"])
def test_extract_moat_and_valuation_handle_none_slots(key):
    svc = _svc()
    data = {key: {"moat": None, "valuation": None}, "moat_competition": {}}
    assert svc._extract_moat(data) is None
    assert svc._extract_valuation(data) is None
    # Score layer None entirely must also be safe.
    assert svc._extract_moat({key: None}) is None
    assert svc._extract_valuation({key: None}) is None
