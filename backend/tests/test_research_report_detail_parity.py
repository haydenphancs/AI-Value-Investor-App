"""`ResearchReportDetail` — the shape iOS decodes right after a 20-credit generation.

This was the one response the iOS app reads on a PAID path with no parity coverage, and the two
sides already disagreed: the backend declared `stock_id` and `company_name` as
`Optional[str] = None`, while Swift declares both as non-optional `String`. Swift's synthesised
`Decodable` throws on a null or absent value for a non-optional field, so a single null would
have been a hard decode failure after the user had already been charged and waited ~60s.

It did not fire only because `GET /research/reports/{report_id}` happens to inject `stock_id`
before returning — an implicit guarantee nothing enforced. The fix made both fields REQUIRED on
the response model (so Pydantic raises on our side instead of shipping a null) and coalesced
`company_name` in the endpoint. This test pins that agreement to the Swift source, so a future
edit to either side fails the build rather than the app.

Related and deliberately different: `BackendReportListItem` declares the SAME two fields as
`String?`, so `ResearchReportListItem` keeps them optional. The contract is per-shape.

Source-level + `model_validate`; no network, no Supabase, no app build.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.research import ResearchReportDetail, ResearchReportListItem

_REPO = Path(__file__).resolve().parents[2]
_SWIFT = _REPO / "frontend/ios/ios/Core/Services/TaskPollingManager.swift"


def _swift_struct(name: str) -> str:
    if not _SWIFT.exists():
        pytest.skip(f"{_SWIFT} not present")
    src = _SWIFT.read_text()
    start = src.index(f"struct {name}")
    end = src.index("enum CodingKeys", start)
    return src[start:end]


def _swift_fields(name: str) -> dict[str, bool]:
    """Map Swift property name → is_optional, for `let x: T` / `let x: T?` declarations."""
    fields = {}
    for prop, ty in re.findall(r"\blet (\w+):\s*([^\n=]+)", _swift_struct(name)):
        fields[prop] = ty.strip().endswith("?")
    assert fields, f"no properties parsed from {name} — regex drifted"
    return fields


def _coding_keys(name: str) -> dict[str, str]:
    """Map Swift property name → JSON key, honouring both `case x` and `case x = "y"`."""
    src = _SWIFT.read_text()
    start = src.index(f"struct {name}")
    block_start = src.index("enum CodingKeys", start)
    block = src[block_start: src.index("\n    }", block_start)]

    mapping: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        for part in line[len("case "):].split(","):
            part = part.strip()
            if "=" in part:
                prop, json_key = part.split("=", 1)
                mapping[prop.strip()] = json_key.strip().strip('"')
            elif part:
                mapping[part] = part          # `case id` → same name both sides
    assert mapping, f"no CodingKeys parsed from {name} — regex drifted"
    return mapping


# ── The defect this exists to prevent ────────────────────────────────────────

def test_every_non_optional_swift_field_is_required_on_the_backend():
    """THE guard. A Swift non-optional decodes a null as a THROW, so anything Swift declares
    without `?` must be something the backend can never omit or null."""
    swift = _swift_fields("ResearchReportDetail")
    keys = _coding_keys("ResearchReportDetail")
    model = ResearchReportDetail.model_fields

    offenders = []
    for prop, is_optional in swift.items():
        if is_optional:
            continue
        json_key = keys.get(prop, prop)
        field = model.get(json_key)
        assert field is not None, (
            f"Swift decodes `{json_key}` but ResearchReportDetail has no such field — "
            f"that is an unconditional decode failure"
        )
        if not field.is_required():
            offenders.append(json_key)

    assert not offenders, (
        f"Swift declares these NON-optional while the backend may omit or null them: "
        f"{sorted(offenders)}. Either make them required on the response model (and guarantee "
        f"them in the endpoint), or make the Swift properties optional. A null here is a "
        f"decode crash on a report the user has already paid 20 credits for."
    )


def test_the_two_historically_drifted_fields_stay_required():
    """Named explicitly so a future 'tidy-up' that re-optionalises them has to argue with a
    test rather than a comment."""
    for key in ("stock_id", "company_name"):
        assert ResearchReportDetail.model_fields[key].is_required(), (
            f"{key} went back to Optional — iOS declares it non-optional, so the endpoint's "
            f"injection is the ONLY thing preventing a decode crash"
        )


def test_the_endpoint_actually_guarantees_both_fields():
    """A required field the endpoint does not populate is a 500 on the paid path, which is
    better than a crash but still broken. Pin the injection AND the coalesce."""
    src = (_REPO / "backend/app/api/v1/endpoints/research.py").read_text()
    fn = src[src.index("async def get_research_report("):]
    fn = fn[: fn.index("\n@router")]

    assert 'row["stock_id"] = row["ticker"]' in fn
    assert 'row["company_name"] = row.get("company_name") or row["ticker"]' in fn, (
        "company_name must be coalesced — the column is nullable and the response model now "
        "requires it"
    )


# ── Round-trip against the worst realistic row ───────────────────────────────

def _minimal_row(**over):
    """A row with every nullable column actually null — the shape a freshly-inserted,
    not-yet-generated report has."""
    row = {
        "id": "11111111-2222-4333-8444-555555555555",
        "user_id": "aaaabbbb-cccc-4ddd-8eee-ffff00001111",
        "ticker": "NVDA",
        "investor_persona": "warren_buffett",
        "status": "pending",
        "created_at": "2026-08-07T12:00:00+00:00",
        "company_name": None,
        "industry": None,
        "title": None,
        "executive_summary": None,
        "investment_thesis": None,
        "pros": None,
        "cons": None,
        "moat_analysis": None,
        "valuation_analysis": None,
        "risk_assessment": None,
        "full_report": None,
        "key_takeaways": None,
        # A DB row from before 2026-08-14 still carries `action_recommendation`; kept in
        # this worst-case fixture on purpose, to prove the model IGNORES it rather than
        # re-publishing a stored Buy/Sell verdict.
        "action_recommendation": "Buy",
        "overall_score": None,
        "fair_value_estimate": None,
        "generation_time_seconds": None,
        "tokens_used": None,
        "completed_at": None,
        "user_rating": None,
        "user_feedback": None,
        "pdf_status": None,
        "pdf_generated_at": None,
    }
    row.update(over)
    return row


def _as_endpoint_returns(row: dict) -> dict:
    """Apply exactly what `get_research_report` applies before returning."""
    row = dict(row)
    row["stock_id"] = row["ticker"]
    row["company_name"] = row.get("company_name") or row["ticker"]
    return row


def test_a_worst_case_row_validates_once_the_endpoint_has_touched_it():
    model = ResearchReportDetail.model_validate(_as_endpoint_returns(_minimal_row()))
    assert model.stock_id == "NVDA"
    assert model.company_name == "NVDA", "a null company_name must coalesce to the ticker"
    # Defaults the iOS "[Refunded]" chip depends on.
    assert model.is_refunded is False
    assert model.credits_charged == 20


def test_the_raw_row_is_REJECTED_without_the_endpoint_step():
    """The point of making them required: a row that skips the injection fails HERE, loudly,
    instead of serialising a null that throws inside Swift's decoder."""
    with pytest.raises(ValidationError) as ei:
        ResearchReportDetail.model_validate(_minimal_row())
    missing = {e["loc"][0] for e in ei.value.errors()}
    assert "stock_id" in missing


def test_a_null_company_name_is_rejected_rather_than_serialised():
    row = _as_endpoint_returns(_minimal_row())
    row["company_name"] = None
    with pytest.raises(ValidationError):
        ResearchReportDetail.model_validate(row)


def test_every_swift_coding_key_exists_on_the_model():
    """A key Swift asks for that the backend never sends is silently nil for an optional — and
    a permanently blank field in the UI. Catch the rename."""
    model = set(ResearchReportDetail.model_fields)
    unknown = {k for k in _coding_keys("ResearchReportDetail").values() if k not in model}
    assert not unknown, (
        f"Swift decodes keys the backend does not define: {sorted(unknown)}"
    )


# ── The list shape must stay OPPOSITE, deliberately ──────────────────────────

def test_the_list_item_keeps_the_same_fields_optional():
    """`BackendReportListItem` declares `stockId`/`companyName` as `String?`, so tightening the
    list model would be a change with no benefit and a real cost: the list endpoint returns
    many rows, and one bad row would fail the whole response instead of one card."""
    swift = _swift_fields("BackendReportListItem")
    assert swift["stockId"] is True and swift["companyName"] is True, (
        "the list DTO went non-optional — either tighten ResearchReportListItem to match, or "
        "revert; they must not disagree"
    )
    for key in ("stock_id", "company_name"):
        assert not ResearchReportListItem.model_fields[key].is_required()


def test_list_non_optionals_are_required_too():
    """Same guard as the detail shape, applied to the list."""
    swift = _swift_fields("BackendReportListItem")
    keys = _coding_keys("BackendReportListItem")
    model = ResearchReportListItem.model_fields

    for prop, is_optional in swift.items():
        if is_optional:
            continue
        json_key = keys.get(prop, prop)
        field = model.get(json_key)
        assert field is not None, f"Swift decodes `{json_key}`, absent from the list model"
        assert field.is_required(), (
            f"Swift declares `{json_key}` non-optional but the list model may omit it"
        )
