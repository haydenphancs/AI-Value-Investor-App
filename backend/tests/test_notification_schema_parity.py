"""Backend ↔ iOS contract for the notification inbox and price alerts.

A failure here is not a missing row — it is a DECODE CRASH on a user's phone. The iOS
DTOs declare non-optional fields; a backend response that omits one, renames it, or
sends the wrong type takes the whole screen down.

The pattern mirrors `test_ticker_report_schema_parity.py`: build the WORST-CASE row the
database can produce (nulls, empty strings, wrong numeric types, junk in the JSONB),
push it through the production builder, and validate the result against the response
model — then assert the exact field names iOS decodes are present at the documented
level.
"""

import re
from pathlib import Path

import pytest

from app.schemas.notifications import (
    MarkReadResponse,
    NotificationEventResponse,
    NotificationListResponse,
    _iso,
)
from app.schemas.price_alerts import (
    PriceAlertListResponse,
    PriceAlertResponse,
    price_alert_from_row,
)
from app.services.notification_inbox_service import NotificationInboxService

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "frontend/ios/ios/Models/NotificationModels.swift"


def _swift() -> str:
    assert MODELS.exists(), f"missing {MODELS}"
    return MODELS.read_text()


def _coding_keys(struct: str) -> set:
    """The wire names an iOS struct decodes: explicit `case x = "y"` plus bare `case a, b`."""
    src = _swift()
    start = src.index(f"struct {struct}")
    block = src[start: src.index("\n}", start)]
    keys = set(re.findall(r'case\s+\w+\s*=\s*"(\w+)"', block))
    for line in re.findall(r"case\s+([\w,\s]+)\n", block):
        if "=" in line:
            continue
        keys |= {token.strip() for token in line.split(",") if token.strip()}
    return keys


# ── inbox ────────────────────────────────────────────────────────────────────

def _worst_case_row() -> dict:
    """Every column at its most hostile legal value."""
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "kind": None,               # a row written before the registry existed
        "category": None,
        "title": None,              # NOT NULL in the schema, but defend anyway
        "body": "",
        "route": None,              # JSONB null
        "claimed_at": None,
        "read_at": None,
        "push_state": None,
    }


def test_a_worst_case_row_still_produces_a_valid_response():
    service = object.__new__(NotificationInboxService)
    dto = service._to_response(_worst_case_row())
    assert dto is not None
    NotificationEventResponse.model_validate(dto.model_dump())
    # Non-optional on iOS → must never be null on the wire.
    assert isinstance(dto.kind, str) and dto.kind
    assert isinstance(dto.category, str) and dto.category
    assert isinstance(dto.title, str)
    assert isinstance(dto.body, str)
    assert isinstance(dto.route, dict)
    assert isinstance(dto.created_at, str)
    assert isinstance(dto.delivery_state, str) and dto.delivery_state


@pytest.mark.parametrize("junk", [
    {"nested": {"a": 1}},              # a dict — iOS AnyCodable yields "" for it
    {"list": [1, 2, 3]},
    {"null": None},
    {"ok": "AAPL", "bad": {"x": 1}},   # mixed
])
def test_non_scalar_route_values_are_dropped_before_they_reach_ios(junk):
    """auth.md §3: the iOS decoder handles String/Int/Double/Bool and silently yields ""
    for anything else — so a nested value arrives as GARBAGE rather than as an error.
    Dropping it here makes the contract explicit."""
    service = object.__new__(NotificationInboxService)
    row = {**_worst_case_row(), "route": junk}
    dto = service._to_response(row)
    assert all(
        isinstance(v, (str, int, float, bool)) for v in dto.route.values()
    ), dto.route
    assert "nested" not in dto.route and "list" not in dto.route and "null" not in dto.route


def test_scalar_route_values_survive():
    service = object.__new__(NotificationInboxService)
    row = {**_worst_case_row(), "route": {
        "ticker": "AAPL", "asset_type": "stock", "route": "ticker", "n": 3, "b": True,
    }}
    dto = service._to_response(row)
    assert dto.route == {
        "ticker": "AAPL", "asset_type": "stock", "route": "ticker", "n": 3, "b": True,
    }


def test_a_genuinely_unusable_row_is_skipped_not_fatal():
    """One malformed row must not blank the whole inbox."""
    service = object.__new__(NotificationInboxService)
    assert service._to_response({}) is None          # no id at all


def test_the_ios_decoder_expects_exactly_these_inbox_keys():
    backend = set(NotificationEventResponse.model_fields.keys())
    ios = _coding_keys("NotificationEventDTO")
    assert ios, "CodingKeys scan drifted — found none"
    missing = ios - backend
    assert not missing, (
        f"iOS decodes {sorted(missing)} but the backend never emits them — a "
        f"non-optional field that is absent is a decode CRASH, not a nil"
    )


def test_the_ios_list_wrapper_matches():
    backend = set(NotificationListResponse.model_fields.keys())
    ios = _coding_keys("NotificationListDTO")
    assert ios <= backend, sorted(ios - backend)
    # The badge and the cursor are the two the UI cannot work without.
    assert {"unread_count", "next_cursor"} <= backend


def test_the_mark_read_response_matches():
    backend = set(MarkReadResponse.model_fields.keys())
    ios = _coding_keys("MarkNotificationsReadDTO")
    assert ios <= backend, sorted(ios - backend)


def test_timestamps_cross_the_wire_as_strings():
    """iOS Codable decodes these as `String`, and the repo's convention is ISO strings
    rather than `datetime` (backend-python.md). A datetime would serialize into a shape
    iOS has never parsed."""
    from datetime import datetime, timezone

    assert isinstance(_iso(datetime.now(timezone.utc)), str)
    assert isinstance(_iso("2026-08-07T12:00:00+00:00"), str)
    assert _iso(None) is None


# ── price alerts ─────────────────────────────────────────────────────────────

def test_a_worst_case_price_alert_row_still_validates():
    """`NUMERIC` columns come back from Supabase as STRINGS, and a raw string reaching a
    `float` field is a validation error that would 500 the entire list for one bad row."""
    row = {
        "id": "22222222-2222-2222-2222-222222222222",
        "ticker": "aapl",
        "asset_type": None,
        "kind": None,
        "threshold": "250.50",       # string-typed NUMERIC
        "repeat_mode": None,
        "is_active": None,
        "armed": None,
        "last_price": None,
        "last_triggered_at": None,
        "trigger_count": None,
        "note": None,
        "created_at": None,
    }
    dto = price_alert_from_row(row)
    PriceAlertResponse.model_validate(dto.model_dump())
    assert dto.ticker == "AAPL"           # normalised for display
    assert dto.threshold == pytest.approx(250.50)
    assert dto.asset_type == "stock"
    assert dto.repeat_mode == "once"
    assert dto.trigger_count == 0


@pytest.mark.parametrize("bad", [None, "", "n/a", {}, []])
def test_an_unparseable_threshold_degrades_to_zero_rather_than_500ing(bad):
    dto = price_alert_from_row({"id": "x", "ticker": "AAPL", "threshold": bad})
    assert dto.threshold == 0.0
    PriceAlertResponse.model_validate(dto.model_dump())


def test_the_ios_decoder_expects_exactly_these_price_alert_keys():
    backend = set(PriceAlertResponse.model_fields.keys())
    ios = _coding_keys("PriceAlertDTO")
    assert ios, "CodingKeys scan drifted — found none"
    missing = ios - backend
    assert not missing, (
        f"iOS decodes {sorted(missing)} but the backend never emits them: {sorted(backend)}"
    )


def test_the_ios_price_alert_list_wrapper_matches():
    backend = set(PriceAlertListResponse.model_fields.keys())
    ios = _coding_keys("PriceAlertListDTO")
    assert ios <= backend, sorted(ios - backend)


def test_the_ios_enums_cover_every_kind_the_backend_can_store():
    """`PriceAlertKind` / `PriceAlertRepeat` fall back on an unknown raw value rather than
    failing to decode — but a kind the app cannot NAME renders as the wrong label, which
    is worse than an error because it looks correct."""
    from app.services.price_alert_engine import VALID_KINDS, VALID_REPEAT_MODES

    swift = _swift()
    ios_kinds = set(re.findall(r'case \w+ = "(price_above|price_below|percent_move)"', swift))
    assert ios_kinds == set(VALID_KINDS), (
        f"backend stores {sorted(VALID_KINDS)}, iOS names {sorted(ios_kinds)}"
    )
    # `once` / `daily` are bare enum cases on iOS (rawValue == case name).
    for mode in VALID_REPEAT_MODES:
        assert re.search(rf"case {mode}\b", swift), f"iOS PriceAlertRepeat is missing {mode}"


def test_every_registered_kind_is_nameable_by_the_ios_router():
    """`route_kind` is what `NotificationRouter` switches on. A value it has no branch for
    lands in the inbox — safe, but it means that notification's tap goes nowhere useful."""
    from app.services.notification_kinds import NOTIFICATION_KINDS

    router = (REPO / "frontend/ios/ios/Core/Services/NotificationRouter.swift").read_text()
    for kind in NOTIFICATION_KINDS.values():
        assert f'"{kind.route_kind}"' in router or kind.route_kind == "ticker", (
            f"NotificationRouter has no branch for route_kind={kind.route_kind!r} "
            f"(from kind {kind.key!r})"
        )
