"""Label + `ref_id` parsing for the credit history statement.

The statement is only as good as this mapping: an unmapped `reason` renders as a row the
user cannot interpret, and a `ref_id` read with the wrong parser prints an ET month stamp
or an Apple transaction id at them as if it were a ticker.

Every test here asserts the CORRECT DEGRADED result for a bad input — no subtitle —
rather than merely "it didn't crash". A wrong ticker on a money screen is worse than a
missing one.

Pure functions only: no Supabase, no network, no service instance.
"""

from __future__ import annotations

import pytest

from app.services.credit_history_service import (
    KIND_GRANT,
    KIND_OTHER,
    KIND_PURCHASE,
    KIND_REFUND,
    KIND_REVOKE,
    KIND_SPEND,
    KNOWN_REASONS,
    _looks_like_ticker,
    _pool_note,
    _ticker_from_chat_ref,
    _ticker_from_report_ref,
    describe_reason,
    describe_transaction,
)


# ── every shipped reason is mapped, with the right kind ──────────────────────

# The full inventory as written by app/, scripts/ and the SQL functions. Kept as a
# literal (rather than derived from the mapping under test) so this list is an
# independent statement of what production writes.
_EXPECTED_KINDS = {
    "report_charge": KIND_SPEND,
    "chat_charge": KIND_SPEND,
    "report_refund": KIND_REFUND,
    "report_refund_deleted": KIND_REFUND,
    "report_refund_reconciled": KIND_REFUND,
    "chat_refund": KIND_REFUND,
    "chat_cache_hit": KIND_REFUND,
    "chat_undelivered": KIND_REFUND,
    "chat_stream_fallback_failed": KIND_REFUND,
    "chat_stream_empty": KIND_REFUND,
    "chat_stream_persist_failed": KIND_REFUND,
    "chat_stream_cancelled": KIND_REFUND,
    "grant": KIND_GRANT,
    "monthly_reset": KIND_GRANT,
    "tier_upgrade": KIND_GRANT,
    "tester_grant": KIND_GRANT,
    "tier_revoked": KIND_REVOKE,
    "pack_purchase": KIND_PURCHASE,
    "pack_revoked": KIND_REVOKE,
}


@pytest.mark.parametrize("reason,expected_kind", sorted(_EXPECTED_KINDS.items()))
def test_every_shipped_reason_has_a_kind_and_a_title(reason, expected_kind):
    kind, title, _ref_style = describe_reason(reason)
    assert kind == expected_kind
    assert title.strip(), f"{reason} renders an empty title"


def test_the_mapping_covers_exactly_the_shipped_inventory():
    """A reason added to one side only is the failure this catches."""
    assert set(KNOWN_REASONS) == set(_EXPECTED_KINDS)


# ── the prefix family ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    [
        "chat_degraded_no_specialists",  # chat_service.py, live today
        "chat_degraded_unmerged",        # chat_service.py, live today
        "chat_degraded_something_new",   # a third suffix needs NO endpoint change
    ],
)
def test_chat_degraded_is_matched_as_a_prefix_not_a_dict_key(reason):
    kind, title, _ = describe_reason(reason)
    assert kind == KIND_REFUND
    assert title.strip()
    # And it must not be reachable by having been quietly added as an exact key —
    # that would leave a future suffix uncovered again.
    assert reason not in KNOWN_REASONS


def test_a_bare_prefix_without_a_suffix_still_maps():
    kind, title, _ = describe_reason("chat_degraded_")
    assert kind == KIND_REFUND
    assert title.strip()


# ── the fallback ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["", "   ", None, 0, [], "totally_new_reason_2027"])
def test_unknown_or_junk_reasons_degrade_to_a_generic_row(reason):
    kind, title, _ = describe_reason(reason)
    assert kind == KIND_OTHER
    assert title.strip(), "the fallback must still render something readable"


def test_reason_is_matched_after_stripping_whitespace():
    assert describe_reason("  report_charge  ")[0] == KIND_SPEND


# ── ticker recognition ───────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["ORCL", "A", "BRK.B", "RDS-A", "BTCUSD", "nvda"])
def test_real_symbols_are_recognised(value):
    assert _looks_like_ticker(value) is True


@pytest.mark.parametrize(
    "value,why",
    [
        ("2026-08", "an ET month stamp from a grant/reset row"),
        ("2000000812345678", "an Apple StoreKit transaction id"),
        ("12345", "a short all-digit id"),
        ("550e8400-e29b-41d4-a716-446655440000", "a chat session uuid"),
        ("", "empty"),
        ("   ", "whitespace only"),
        (None, "missing"),
        (42, "not a string"),
        ("WAY-TOO-LONG-FOR-A-SYMBOL", "beyond the length bound"),
        ("has space", "a space is not valid in a symbol"),
        ("under_score", "an underscore is not valid in a symbol"),
    ],
)
def test_non_symbols_are_rejected(value, why):
    assert _looks_like_ticker(value) is False, why


# ── report ref_id parsing ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref_id,expected",
    [
        ("ORCL", "ORCL"),                              # /research/generate
        ("ORCL:warren_buffett", "ORCL"),               # GET /stocks/{t}/report
        ("orcl:warren_buffett", "ORCL"),               # normalised upward
        ("BRK.B:benjamin_graham", "BRK.B"),
        ("ORCL:", "ORCL"),                             # trailing colon
        ("ORCL:a:b:c", "ORCL"),                        # extra colons
        ("  ORCL  ", "ORCL"),                          # padded
    ],
)
def test_report_ref_yields_the_ticker(ref_id, expected):
    assert _ticker_from_report_ref(ref_id) == expected


@pytest.mark.parametrize(
    "ref_id",
    [
        None,
        "",
        ":warren_buffett",   # missing ticker half
        ":",
        "2026-08",           # a month stamp reaching the wrong parser
        "2000000812345678",  # an Apple txn id reaching the wrong parser
        42,
    ],
)
def test_report_ref_degrades_to_no_subtitle(ref_id):
    assert _ticker_from_report_ref(ref_id) is None


# ── chat ref_id parsing ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref_id,expected",
    [
        ("report_chat:ORCL:0f1e2d3c4b5a", "ORCL"),
        ("report_chat:orcl:0f1e2d3c4b5a", "ORCL"),
        ("report_chat:BRK.B:0f1e2d", "BRK.B"),
        ("report_chat:ORCL", "ORCL"),  # uuid half absent
    ],
)
def test_report_chat_ref_yields_the_ticker(ref_id, expected):
    assert _ticker_from_chat_ref(ref_id) == expected


@pytest.mark.parametrize(
    "ref_id,why",
    [
        ("550e8400-e29b-41d4-a716-446655440000:0f1e2d", "a PLAIN chat turn names nothing"),
        ("report_chat:", "prefix with no ticker"),
        ("report_chat::0f1e2d", "empty ticker segment"),
        ("report_chat:2026-08:0f1e2d", "a month stamp is not a ticker"),
        ("ORCL:warren_buffett", "a REPORT ref must not be read by the chat parser"),
        (None, "missing"),
        ("", "empty"),
        (42, "not a string"),
    ],
)
def test_chat_ref_degrades_to_no_subtitle(ref_id, why):
    assert _ticker_from_chat_ref(ref_id) is None, why


def test_a_plain_chat_session_id_is_never_shown_as_a_ticker():
    """The regression this guards: the session id exists to keep a charge greppable, and
    it is the single most likely thing to leak into a user-visible subtitle."""
    row = {
        "id": 1,
        "delta": -1,
        "reason": "chat_charge",
        "ref_id": "550e8400-e29b-41d4-a716-446655440000:9a8b7c6d",
    }
    assert describe_transaction(row).subtitle is None


# ── pool note ────────────────────────────────────────────────────────────────


def test_granted_only_movement_says_nothing():
    assert _pool_note(-20, 0, KIND_SPEND) is None


def test_unknown_legacy_split_says_nothing():
    """Pre-migration-118 rows carry 0/0 beside a non-zero delta. That split is UNKNOWN,
    not zero — inventing '20 monthly' over it would be a lie on a money screen."""
    assert _pool_note(0, 0, KIND_SPEND) is None


def test_mixed_pool_spend_is_broken_out():
    assert _pool_note(-15, -5, KIND_SPEND) == "15 monthly + 5 purchased"


def test_purchased_only_spend_is_named():
    assert _pool_note(0, -5, KIND_SPEND) == "5 purchased"


def test_a_pack_purchase_states_the_compliance_fact():
    assert _pool_note(0, 540, KIND_PURCHASE) == "Never expires"


@pytest.mark.parametrize("bad", ["x", None, [], {}])
def test_pool_note_survives_junk_deltas(bad):
    assert _pool_note(bad, bad, KIND_SPEND) is None


# ── whole-row description ────────────────────────────────────────────────────


def test_a_row_with_no_id_is_rejected_rather_than_rendered_as_none():
    for bad in ({"delta": -1, "reason": "chat_charge"}, {"id": None}, {"id": "  "}):
        with pytest.raises(ValueError):
            describe_transaction(bad)


def test_a_huge_bigint_id_survives_as_a_string():
    row = {"id": 9223372036854775807, "delta": -1, "reason": "chat_charge"}
    assert describe_transaction(row).id == "9223372036854775807"


def test_a_junk_delta_degrades_to_zero_rather_than_raising():
    row = {"id": 1, "delta": "not a number", "reason": "chat_charge"}
    assert describe_transaction(row).delta == 0


def test_pack_name_is_used_when_resolved_and_omitted_when_not():
    row = {"id": 1, "delta": 540, "reason": "pack_purchase", "ref_id": "2000000812345678"}
    assert describe_transaction(row, pack_names={"2000000812345678": "Power"}).subtitle == "Power"
    # Unresolved: the row still renders, just without a name. It must NEVER fall through
    # to showing the raw Apple transaction id.
    described = describe_transaction(row, pack_names={})
    assert described.subtitle is None
    assert "2000000812345678" not in (described.title + (described.subtitle or ""))


def test_is_reversed_is_carried_through():
    row = {"id": 7, "delta": -1, "reason": "chat_charge"}
    assert describe_transaction(row, is_reversed=True).is_reversed is True
    assert describe_transaction(row).is_reversed is False


def test_the_raw_reason_is_preserved_for_support():
    row = {"id": 1, "delta": 1, "reason": "chat_degraded_brand_new_suffix"}
    described = describe_transaction(row)
    assert described.reason == "chat_degraded_brand_new_suffix"
    assert described.kind == KIND_REFUND
