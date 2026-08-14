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
    """Shape a stored row exactly as the endpoint does — by CALLING the endpoint's builder.

    ⚠️ This used to be a hand-written mirror ("without importing the endpoint module"), and
    it drifted the moment `_profile_response` changed: it kept computing the old two-arm
    `applied = signals_unlocked(tier) and not is_empty_profile(profile)` after the real one
    started delegating to `may_apply_profile`, which additionally requires `consented_at`
    and the feature flag. Measured on the same input, the mirror said `True` where the API
    said `False` — and the suite stayed green, because a copy of the logic can only ever
    test itself. This file's entire purpose is parity, so a copy is the one thing it must
    not contain. (`_SESSION_LIST_COLUMNS` learned the same lesson: derive, never duplicate.)

    `sanitize_profile` is still applied here because `_profile_response` expects a row that
    has already been through the service, which is what `get_profile` always hands it.
    """
    from app.api.v1.endpoints.users import _profile_response

    profile = sanitize_profile(raw)
    # `sanitize_profile` owns only the closed-vocabulary fields; consent and row-existence
    # ride alongside it on a real row, so mirror that here.
    if raw.get("consented_at"):
        profile["consented_at"] = raw["consented_at"]
    profile["has_profile"] = bool(raw)
    return _profile_response(profile, {"id": "u1", "tier": tier})


# A profile that satisfies every arm of the gate EXCEPT the ones a given test is varying.
_CONSENTED = "2026-08-13T00:00:00+00:00"


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
def test_applied_follows_the_tier_and_falls_closed(tier, expected, monkeypatch):
    """The TIER axis, with every other arm of the gate satisfied.

    The flag and the consent timestamp are set here deliberately: without them every row
    would be `False` for a reason unrelated to the tier, and this parametrisation would
    pass while testing nothing. That is exactly how the old hand-copied mirror hid the
    drift it was supposed to catch.
    """
    monkeypatch.setattr("app.config.settings.CHAT_PERSONALIZATION_ENABLED", True)
    r = _response_from({"topics": ["value"], "consented_at": _CONSENTED}, tier=tier)
    assert r.applied is expected


def test_applied_is_false_without_consent_on_every_tier(monkeypatch):
    """The CONSENT arm — the one the old mirror omitted entirely."""
    monkeypatch.setattr("app.config.settings.CHAT_PERSONALIZATION_ENABLED", True)
    for tier in ("pro", "premium"):
        assert _response_from({"topics": ["value"]}, tier=tier).applied is False


def test_applied_is_false_while_the_feature_flag_is_off():
    """The FLAG arm — the other omission, and the state the build ships in."""
    r = _response_from({"topics": ["value"], "consented_at": _CONSENTED}, tier="pro")
    assert r.applied is False


def test_an_empty_profile_is_never_applied_even_on_a_paid_tier(monkeypatch):
    """Nothing to personalize with — claiming otherwise would be a lie in the UI."""
    monkeypatch.setattr("app.config.settings.CHAT_PERSONALIZATION_ENABLED", True)
    assert _response_from({"consented_at": _CONSENTED}, tier="premium").applied is False


def test_the_wire_field_never_disagrees_with_the_runtime_gate(monkeypatch):
    """Parity stated directly, across the whole grid, so a future re-implementation fails.

    Enumerating outcomes cannot catch a FIFTH arm being added to `may_apply_profile` and
    not to the endpoint; asserting equivalence can.
    """
    from app.services.agents.investor_profile_prompt import may_apply_profile

    for flag in (True, False):
        monkeypatch.setattr("app.config.settings.CHAT_PERSONALIZATION_ENABLED", flag)
        for tier in ("pro", "premium", "free", None, "wizard", ""):
            for raw in (
                {"topics": ["value"], "consented_at": _CONSENTED},
                {"topics": ["value"]},
                {"consented_at": _CONSENTED},
                {},
            ):
                profile = sanitize_profile(raw)
                if raw.get("consented_at"):
                    profile["consented_at"] = raw["consented_at"]
                assert _response_from(raw, tier=tier).applied is may_apply_profile(
                    profile, tier
                ), f"wire disagrees with the gate: flag={flag} tier={tier!r} raw={raw}"


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
