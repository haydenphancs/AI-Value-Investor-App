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


def test_no_buy_sell_verdict_is_derived_or_served_anymore():
    """`_derive_recommendation` emitted a literal "Buy"/"Sell"/"Hold"/"Watch" call on a
    named security, persisted it and served it, and NO View ever rendered it — all of the
    App Review 5.1.1(ix)/3.1.5 "advice, not information" risk for none of the benefit.
    Removed 2026-08-14.

    Pinned in three places because reintroducing any ONE of them re-opens the risk:
    the derivation, the write, and — most easily missed — the response field, which would
    otherwise keep serving the verdicts already stored on pre-removal report rows.
    """
    from pathlib import Path

    from app.schemas.research import ResearchReportDetail

    assert not hasattr(ResearchService, "_derive_recommendation"), (
        "_derive_recommendation is back. If a Buy/Sell verdict is wanted again it needs a "
        "disclaimer surface first — see the Technical Meter, which has one."
    )
    assert "action_recommendation" not in ResearchReportDetail.model_fields, (
        "ResearchReportDetail serves action_recommendation again. Reports generated before "
        "2026-08-14 still hold a stored verdict, so this field re-publishes them."
    )
    service_src = Path(
        __file__
    ).resolve().parents[1] / "app/services/research_service.py"
    body = "\n".join(
        line for line in service_src.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )
    assert '"action_recommendation":' not in body, (
        "research_service writes action_recommendation again"
    )


@pytest.mark.parametrize("key", ["_scoring_inputs", "key_vitals"])
def test_extract_moat_and_valuation_handle_none_slots(key):
    svc = _svc()
    data = {key: {"moat": None, "valuation": None}, "moat_competition": {}}
    assert svc._extract_moat(data) is None
    assert svc._extract_valuation(data) is None
    # Score layer None entirely must also be safe.
    assert svc._extract_moat({key: None}) is None
    assert svc._extract_valuation({key: None}) is None
