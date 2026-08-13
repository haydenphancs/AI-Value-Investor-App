"""Backend ⇄ iOS contract for `GET/PUT /users/me/investor-profile`.

Mandatory per .claude/rules/testing.md: iOS decodes this response, so a drift here is
a decode crash in production, not a style nit.

Two halves:
  * the response model survives worst-case inputs (missing row, unknown stored values,
    a partial dict read back from a row written by an older app version);
  * the Swift DTO's CodingKeys and the Pydantic field names actually agree — checked by
    reading the Swift source, since there is no way to run the decoder from here.
"""

import re
from pathlib import Path

import pytest

from app.schemas.investor_profile import (
    InvestorProfileResponse,
    UpdateInvestorProfileRequest,
)
from app.services.user_investor_profile_service import (
    ARRAY_FIELDS,
    SCALAR_FIELDS,
    is_empty_profile,
    sanitize_profile,
)

_SWIFT = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "ios" / "ios" / "Models" / "InvestorProfileModels.swift"
)


def _response_from(raw: dict, *, tier: str = "free") -> InvestorProfileResponse:
    """Mirror `users._profile_response` without importing the endpoint module."""
    from app.services.entitlements import required_tier_for_signals, signals_unlocked

    profile = sanitize_profile(raw)
    empty = is_empty_profile(profile)
    return InvestorProfileResponse(
        experience_level=profile["experience_level"],
        explanation_style=profile["explanation_style"],
        answer_depth=profile["answer_depth"],
        topics=profile["topics"],
        learning_goals=profile["learning_goals"],
        follow_signals=profile["follow_signals"],
        has_profile=bool(raw),
        is_empty=empty,
        applied=signals_unlocked(tier) and not empty,
        required_tier=required_tier_for_signals(tier),
    )


# ── the response survives worst-case inputs ─────────────────────────────────

def test_no_stored_row_still_validates():
    r = _response_from({})
    assert r.has_profile is False and r.is_empty is True
    assert r.topics == [] and r.applied is False


def test_a_row_with_unknown_stored_values_still_validates():
    """A value can outlive a vocabulary change; a READ must not 500 because of it."""
    r = _response_from({
        "experience_level": "guru", "topics": ["dividends", "obsolete_topic"],
    })
    assert r.experience_level in SCALAR_FIELDS["experience_level"]
    assert r.topics == ["dividends"]


def test_a_partial_row_validates():
    r = _response_from({"topics": ["value"]})
    assert r.topics == ["value"] and r.is_empty is False


def test_every_vocabulary_value_round_trips():
    """Each enum member must satisfy the response `Literal`s — a value the DB allows
    but the schema rejects would 500 on read for exactly the users who chose it."""
    for field, allowed in SCALAR_FIELDS.items():
        for value in allowed:
            assert getattr(_response_from({field: value}), field) == value
    for field, allowed in ARRAY_FIELDS.items():
        r = _response_from({field: list(allowed)})
        assert getattr(r, field) == list(allowed)


# ── the tier verdict ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tier,expected", [
    ("free", False), ("pro", True), ("premium", True),
    (None, False), ("wizard", False), ("", False),
])
def test_applied_follows_the_tier_and_falls_closed(tier, expected):
    r = _response_from({"topics": ["value"]}, tier=tier)
    assert r.applied is expected


def test_an_empty_profile_is_never_applied_even_on_a_paid_tier():
    """Nothing to personalize with — claiming otherwise would be a lie in the UI."""
    assert _response_from({}, tier="premium").applied is False


def test_required_tier_names_the_plan_the_server_enforced():
    assert _response_from({}, tier="free").required_tier == "pro"
    assert _response_from({}, tier="pro").required_tier is None


# ── the request model rejects at the edge ───────────────────────────────────

def test_request_rejects_an_out_of_vocabulary_value():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UpdateInvestorProfileRequest(experience_level="wizard")
    with pytest.raises(ValidationError):
        UpdateInvestorProfileRequest(topics=["not_a_topic"])


def test_request_allows_a_fully_omitted_body():
    """Onboarding steps are individually skippable."""
    req = UpdateInvestorProfileRequest()
    assert req.model_dump(exclude_none=True) == {}


def test_request_distinguishes_omitted_from_explicitly_empty():
    """`exclude_none` is what makes an omitted field keep its stored value; an
    intentional clear sends []."""
    assert "topics" not in UpdateInvestorProfileRequest().model_dump(exclude_none=True)
    assert UpdateInvestorProfileRequest(topics=[]).model_dump(exclude_none=True)["topics"] == []


# ── Swift DTO parity ────────────────────────────────────────────────────────

def _swift() -> str:
    assert _SWIFT.exists(), f"missing {_SWIFT} — the iOS DTO for this endpoint"
    return _SWIFT.read_text()


def test_swift_dto_declares_every_response_field():
    """APIClient does NOT set .convertFromSnakeCase, so each snake_case key must be
    spelled out in CodingKeys or it decodes as a throw."""
    src = _swift()
    for field in InvestorProfileResponse.model_fields:
        assert re.search(rf'\b{field}\b', src), (
            f"iOS InvestorProfileModels.swift never mentions `{field}` — the backend "
            f"sends it and the DTO must map it explicitly (no convertFromSnakeCase)"
        )


def _swift_raw_values() -> set[str]:
    """Every raw value the Swift enums can produce.

    Swift gives a String-raw-value enum an IMPLICIT rawValue equal to the case name,
    so `case new` yields "new" and only renamed cases carry an explicit
    `= "small_cap"`. A scan that looked only for quoted literals would miss every
    implicit case — and then report a missing vocabulary value that is actually there.
    Comma-separated case lists (`case value, growth, dividends`) are the dominant style
    in this file, so they must be split rather than read as one identifier.
    """
    values: set[str] = set()
    for line in _swift().splitlines():
        stripped = line.strip()
        if not stripped.startswith("case "):
            continue
        body = stripped[len("case "):]
        for part in body.split(","):
            part = part.strip()
            if not part:
                continue
            explicit = re.search(r'=\s*"([^"]+)"', part)
            if explicit:
                values.add(explicit.group(1))
            else:
                name = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", part)
                if name:
                    values.add(name.group(1))
    return values


def test_the_swift_scan_found_cases():
    """Guard against the guard: an extractor that matches nothing passes vacuously."""
    found = _swift_raw_values()
    assert len(found) >= 25, f"expected the enum cases, found {sorted(found)}"
    assert "small_cap" in found, "explicit raw values must be picked up"
    assert "brief" in found, "implicit raw values must be picked up"


def test_swift_dto_knows_every_vocabulary_value():
    """A missing enum case makes that member decode to nothing for the users who chose
    it — silently emptying a selection they made."""
    found = _swift_raw_values()
    for allowed in list(SCALAR_FIELDS.values()) + list(ARRAY_FIELDS.values()):
        missing = set(allowed) - found
        assert not missing, f"iOS DTO is missing vocabulary value(s): {sorted(missing)}"
