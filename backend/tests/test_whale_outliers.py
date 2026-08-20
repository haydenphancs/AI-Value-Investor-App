"""Outlier + degradation tests for the Whales tab.

Every case here is a NON-happy-path input that reached production code: a NaN from FMP,
a JSON `null` inside untyped JSONB, a 13F position whose value moved because the PRICE
moved, an unrecognised congressional trade type. The happy path for all of this is
already covered by the eleven sibling `test_whale_*.py` files; this one exists for the
inputs nobody pictured.

Run via `python -m pytest` from backend/.
"""

import math

import pytest

from app.schemas.whale import WhaleProfileResponse, WhaleHoldingResponse
from app.services import whale_service as wsvc
from app.services.whale_service import (
    WhaleService,
    _as_aware,
    _finite_float,
    _format_amount,
    _looks_like_uuid,
    _snapshot_group_id,
)
from app.services._whale_common import (
    format_amount_short,
    resolve_congress_action,
)


# ── NaN / Inf must never reach a Pydantic float ──────────────────────────────
#
# Starlette renders with `allow_nan=False`, so a single non-finite float raises DURING
# serialization — after the endpoint's try/except has already returned. The result is an
# uncatchable 500 for the whole screen, which is exactly how the holders tab went down.


class _TestSvc(WhaleService):
    def __init__(self):  # skip the FMP client
        pass


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", None, "abc", {}]
)
def test_finite_float_neutralizes_every_non_finite_form(bad):
    assert _finite_float(bad) == 0.0


def test_apply_change_percent_survives_a_nan_previous_value():
    """The guard `prev_total <= 0` is FALSE for NaN — every NaN comparison is.

    So a NaN in one prior-quarter row used to sail past it, poison `prev_total`, and
    make every single `change_percent` NaN.
    """
    svc = _TestSvc()
    holdings = [{"ticker": "AAPL", "allocation": 12.5}]
    prev_raw = [
        {"symbol": "AAPL", "value": float("nan")},
        {"symbol": "MSFT", "value": 1_000_000.0},
    ]
    out = svc._apply_change_percent(holdings, prev_raw)
    for h in out:
        assert math.isfinite(h["change_percent"]), h

    # And the value must survive the Pydantic boundary that actually 500'd.
    WhaleHoldingResponse(
        id="1", ticker="AAPL", company_name="Apple",
        allocation=12.5, change_percent=out[0]["change_percent"],
    )


def test_apply_change_percent_with_all_nan_previous_is_a_no_op():
    svc = _TestSvc()
    holdings = [{"ticker": "AAPL", "allocation": 12.5}]
    out = svc._apply_change_percent(holdings, [{"symbol": "AAPL", "value": float("nan")}])
    assert all(math.isfinite(h.get("change_percent", 0.0)) for h in out)


def test_stat_disclosure_rejects_a_stored_nan():
    """Postgres `numeric` accepts 'NaN' AND passes a `>= 0` CHECK for it (NaN sorts
    above every numeric), so a NaN genuinely round-trips through the column. And
    `float("NaN")` SUCCEEDS, so the old `except (TypeError, ValueError)` never fired."""
    svc = _TestSvc()
    out = svc._stat_disclosure(
        {"portfolio_value": float("nan"), "ytd_return": float("nan")}, None
    )
    assert math.isfinite(out["portfolio_value"])
    assert math.isfinite(out["ytd_return"])
    # An unusable return must degrade to the honest "not enough history" tile rather
    # than a confident green +0.0%.
    assert out["return_status"] != "ok"


def test_stat_disclosure_keeps_a_real_zero_distinct_from_unusable():
    svc = _TestSvc()
    ok = svc._stat_disclosure({"portfolio_value": 0.0, "ytd_return": 0.0}, None)
    assert ok["ytd_return"] == 0.0
    assert ok["return_status"] == "ok"      # 0.0 is a REAL flat year, not missing data


# ── `.get(k, default)` on untyped JSONB: a present NULL returns None ─────────


def test_build_trade_responses_tolerates_null_jsonb_fields():
    """`{"amount": None}` returns None from `.get("amount", 0)` — the default never
    applies — and `float(None)` is a TypeError that 503s the whole profile."""
    svc = _TestSvc()
    out = svc._build_trade_responses([
        {
            "ticker": "AAPL", "company_name": None, "action": None,
            "trade_type": None, "amount": None, "date": None,
            "previous_allocation": None, "new_allocation": None,
        }
    ])
    assert len(out) == 1
    t = out[0]
    assert t.amount == 0.0 and t.previous_allocation == 0.0
    assert t.action == "BOUGHT" and t.trade_type == "Increased"
    assert t.company_name == "" and t.date == ""


def test_assemble_group_response_tolerates_null_columns():
    svc = _TestSvc()
    g = svc._assemble_group_response(
        "g1",
        {"date": None, "trade_count": None, "net_action": None,
         "net_amount": None, "insights": None},
        [],
    )
    assert g.date == "" and g.trade_count == 0 and g.net_amount == 0.0
    assert g.insights == []


# ── One roll-up rule for every dollar surface ────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (999_999_999, "+$1.00B"),   # was "+$1000.0M" while the profile card said "$1.00B"
        (999_600, "+$1.0M"),        # was "+$1000K"
        (212_600_000, "+$212.6M"),
        (1_499, "+$1K"),
    ],
)
def test_format_amount_rolls_up_at_unit_boundaries(value, expected):
    assert _format_amount(value, "BOUGHT") == expected


def test_format_amount_agrees_with_format_amount_short():
    """The two helpers render the SAME figure on adjacent screens; they must not
    disagree about where a unit boundary is."""
    for v in (999_499, 999_500, 999_949_999, 999_950_000, 1_000_000_000, 0, 12_345):
        assert _format_amount(v, "BOUGHT").lstrip("+") == format_amount_short(v)


def test_format_amount_only_signs_negative_for_an_actual_sale():
    # The old form was `"+" if action == "BOUGHT" else "-"`, so ANY other value —
    # blank, lower-cased, a future action — printed a buy as a loss.
    assert _format_amount(5_000_000, "BOUGHT").startswith("+")
    assert _format_amount(5_000_000, "SOLD").startswith("-")
    assert _format_amount(5_000_000, "bought").startswith("+")
    assert _format_amount(5_000_000, "").startswith("+")


def test_format_amount_never_emits_nan_text():
    """A NaN here does not 500 (the field is `str`) — it renders the literal "+$nan",
    and `_generate_sentiment_summary` PERSISTS that string into the DB."""
    for bad in (float("nan"), float("inf")):
        out = _format_amount(bad, "BOUGHT")
        assert "nan" not in out.lower() and "inf" not in out.lower(), out


def test_format_amount_does_not_sign_a_perfect_wash():
    assert _format_amount(0, "SOLD") == "$0"


# ── Congressional trade type: never guess a direction ────────────────────────


@pytest.mark.parametrize("raw", ["receive", "transfer", "", None, 123, "  ", "gift"])
def test_unknown_congress_type_is_unresolved_not_a_purchase(raw):
    """Defaulting to BOUGHT inflated `total_bought` and could flip a whole filing's
    `net_action` from SOLD to BOUGHT."""
    assert resolve_congress_action(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("purchase", "BOUGHT"), ("Purchase", "BOUGHT"), ("  PURCHASE ", "BOUGHT"),
        ("sale_full", "SOLD"), ("sale_partial", "SOLD"), ("Sale (Full)", "SOLD"),
        ("exchange", "BOUGHT"),
        ("Sale (Partial) - Spouse", "SOLD"),   # unseen decoration, unambiguous word
    ],
)
def test_known_congress_types_resolve(raw, expected):
    assert resolve_congress_action(raw) == expected


# ── Small helpers whose failure modes are silent ─────────────────────────────


def test_looks_like_uuid_rejects_a_path_parameter_postgrest_would_reject():
    assert _looks_like_uuid("6f9d1c2a-4b7e-5d38-9a10-c7f2e5b41d90")
    for bad in ("", "abc", "1; DROP TABLE", None, "../../etc/passwd"):
        assert not _looks_like_uuid(bad)


def test_snapshot_group_id_is_stable_across_calls():
    """A random id per request re-keyed SwiftUI's Identifiable list on every refresh."""
    a = _snapshot_group_id("w1", "2026-06-30")
    b = _snapshot_group_id("w1", "2026-06-30")
    assert a == b
    assert a != _snapshot_group_id("w1", "2026-03-31")
    assert a != _snapshot_group_id("w2", "2026-06-30")
    assert _looks_like_uuid(a)


def test_as_aware_treats_a_legacy_naive_stamp_as_utc():
    """Rows written before the UTC fix are naive. Subtracting naive from aware raises
    TypeError, which at one call site degraded into "refetch every ticker from FMP"."""
    naive = _as_aware("2026-08-18T12:00:00")
    aware = _as_aware("2026-08-18T12:00:00+00:00")
    assert naive is not None and naive.tzinfo is not None
    assert naive == aware
    assert _as_aware("not-a-date") is None
    assert _as_aware(None) is None


# ── Cache invalidation must be scoped to the caller ──────────────────────────


def test_follow_invalidation_does_not_wipe_other_users():
    """One user tapping Follow used to `.clear()` all three module caches, so every
    other user's roster, feed and profile were thrown away too."""
    wsvc._whale_activity_cache.clear()
    wsvc._whale_list_cache.clear()
    wsvc._whale_profile_cache.clear()

    wsvc._cache_set(wsvc._whale_activity_cache, "activity:userA:free", ["a"])
    wsvc._cache_set(wsvc._whale_activity_cache, "activity:userB:pro", ["b"])
    wsvc._cache_set(wsvc._whale_list_cache, "whales:all:userA", ["a"])
    wsvc._cache_set(wsvc._whale_list_cache, "whales:all:userB", ["b"])
    wsvc._cache_set(wsvc._whale_profile_cache, "profile:w1", "shared")

    wsvc._invalidate_follow_caches("userA", "w1")

    assert "activity:userA:free" not in wsvc._whale_activity_cache
    assert "activity:userB:pro" in wsvc._whale_activity_cache
    assert "whales:all:userA" not in wsvc._whale_list_cache
    assert "whales:all:userB" in wsvc._whale_list_cache
    # The profile cache is follow-state-FREE (overlaid per request), so a follow
    # toggle must not evict it at all.
    assert "profile:w1" in wsvc._whale_profile_cache


# ── The redacted profile must still be a valid response ──────────────────────


def test_redacted_profile_still_validates_and_hides_positions():
    from app.services.whale_service import redact_whale_profile
    from app.schemas.whale import WhaleBehaviorSummaryResponse

    full = WhaleProfileResponse(
        id="w1", name="Someone", title="t", description="d",
        behavior_summary=WhaleBehaviorSummaryResponse(
            action="Accumulating", primary_focus="tech",
            secondary_action="Trimming", secondary_focus="energy",
        ),
        current_holdings=[
            WhaleHoldingResponse(
                id="h1", ticker="SECRET", company_name="Secret Co", allocation=9.9
            )
        ],
        sentiment_summary="a lot of detail",
    )
    locked = redact_whale_profile(full, "pro")
    assert locked.is_locked and locked.tier_required == "pro"
    assert locked.current_holdings == [] and locked.sentiment_summary == ""
    assert "SECRET" not in locked.model_dump_json()
    # Non-mutating: the shared cached object must be untouched.
    assert full.current_holdings and full.current_holdings[0].ticker == "SECRET"
    WhaleProfileResponse.model_validate(locked.model_dump())
