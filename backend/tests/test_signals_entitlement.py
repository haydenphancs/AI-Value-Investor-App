"""App-Exclusive Signals tier gate — the Pro/Max lock on the Home signal cards.

The card stays on screen for everyone (icon, title, description, and the aggregate stat
"3 members buying"); what is withheld from a Free/guest caller is the TICKER and the
drill-down leaders behind it. The client blurs the masked symbol, but the blur is
cosmetic — the gate is this server-side redaction, and these tests are what pin that.

Two things here are load-bearing beyond the happy path:

  • **The redactor must not mutate its input.** It is handed the object stored in the
    class-level ``SignalsService._cache`` — ONE instance shared by every caller for 45
    minutes. An in-place edit would strip tickers for every PAYING user until the next
    rebuild, long after the free request that caused it.
  • **The masked payload must contain no real symbol anywhere.** Asserted against the
    serialised JSON, not the object graph, so a field added later that quietly carries a
    ticker through (a `name`, a future `company`) fails here instead of shipping.

Pure module — no network, no Supabase, no fixtures.
"""
from __future__ import annotations

import pytest

from app.schemas.home_dashboard import (
    SignalGroupResponse,
    SignalRowResponse,
    SignalsGroupResponse,
)
from app.services import entitlements as ent
from app.services.signals_service import (
    _MASK_CHAR,
    _MASK_MAX_LEN,
    _MASK_MIN_LEN,
    _mask_symbol,
    redact_signals,
)


# ── builders ─────────────────────────────────────────────────────────────────

_REAL_SYMBOLS = ["HONA", "AMZN", "UZE", "NVDA", "MSFT", "GOOGL", "TSLA", "AAPL", "AMD", "META"]


def _group(kind: str, *, count: int = 10) -> SignalGroupResponse:
    return SignalGroupResponse(
        kind=kind,
        entries=[
            SignalRowResponse(
                rank=i + 1,
                symbol=_REAL_SYMBOLS[i % len(_REAL_SYMBOLS)],
                name=f"{_REAL_SYMBOLS[i % len(_REAL_SYMBOLS)]} Inc.",
                value=float(10 - i),
            )
            for i in range(count)
        ],
        as_of_date="2026-08-07",
    )


def _groups(**kwargs) -> SignalsGroupResponse:
    return SignalsGroupResponse(
        congress=kwargs.get("congress", _group("congress")),
        whale=kwargs.get("whale", _group("whale")),
        earnings=kwargs.get("earnings", _group("earnings")),
    )


# ── the gate itself ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("tier", ["pro", "premium", " Pro ", "PREMIUM"])
def test_paid_tiers_are_unlocked(tier):
    assert ent.signals_unlocked(tier) is True
    assert ent.required_tier_for_signals(tier) is None


@pytest.mark.parametrize(
    "tier", ["free", "FREE", " free ", "", None, "enterprise", "plus", 123, [], {}]
)
def test_free_unknown_and_garbage_tiers_are_locked(tier):
    """A feature gate fails the dangerous way when it falls OPEN: get the default wrong
    and the paid surface is handed to everyone with no error to notice."""
    assert ent.signals_unlocked(tier) is False
    assert ent.required_tier_for_signals(tier) == ent.TIER_PRO


def test_the_upsell_names_the_cheapest_unlocking_plan_not_the_next_rung():
    """A floor, not a ladder walk. `required_tier_for_more_tickers` steps free→pro→premium;
    signals must send a free user to Pro because Pro already unlocks everything — pointing
    them one rung at a time would upsell Premium to a Pro user who is not locked at all."""
    assert ent.required_tier_for_signals("free") == "pro"
    assert ent.required_tier_for_signals("pro") is None
    assert ent.TIER_PRO in ent.SIGNALS_UNLOCKED_TIERS
    assert ent.TIER_MAX in ent.SIGNALS_UNLOCKED_TIERS
    assert ent.TIER_FREE not in ent.SIGNALS_UNLOCKED_TIERS


def test_every_tier_in_the_ladder_is_classified():
    """A tier added to TIER_ORDER but forgotten here resolves to locked, which is the safe
    direction — this pins that it is a DECISION, not an oversight."""
    for tier in ent.TIER_ORDER:
        assert isinstance(ent.signals_unlocked(tier), bool)


# ── the redaction ────────────────────────────────────────────────────────────

def test_locked_payload_carries_no_real_ticker_anywhere():
    """Asserted on the serialised JSON, not the object graph: a field added later that
    carries a symbol through (name, company, a nested holder) fails here, not in prod."""
    redacted = redact_signals(_groups(), "pro")
    blob = redacted.model_dump_json()
    for symbol in _REAL_SYMBOLS:
        assert symbol not in blob, f"{symbol} leaked into a locked response"


def test_locked_groups_are_flagged_with_the_upgrade_target():
    redacted = redact_signals(_groups(), "pro")
    for group in (redacted.congress, redacted.whale, redacted.earnings):
        assert group is not None
        assert group.is_locked is True
        assert group.tier_required == "pro"
        assert group.locked_count == 10


def test_only_the_headline_entry_survives():
    """iOS derives the headline from entries[0] and a locked row cannot expand, so the
    other nine are leak surface with nothing to render them."""
    redacted = redact_signals(_groups(), "pro")
    assert len(redacted.congress.entries) == 1
    assert redacted.congress.entries[0].rank == 1
    assert redacted.congress.entries[0].name == ""


def test_the_stat_value_survives_verbatim():
    """The card still reads "3 members buying" / "+200%+ surprise" — that number is the
    tease, and it names no ticker. Losing it would gut the upsell."""
    groups = _groups()
    original_value = groups.congress.entries[0].value
    redacted = redact_signals(groups, "pro")
    assert redacted.congress.entries[0].value == original_value


def test_card_chrome_survives():
    """kind + as_of_date drive the client's hardcoded title/icon and the "As of" subtitle;
    dropping them would blank the card instead of locking it."""
    redacted = redact_signals(_groups(), "pro")
    assert redacted.congress.kind == "congress"
    assert redacted.whale.kind == "whale"
    assert redacted.earnings.kind == "earnings"
    assert redacted.congress.as_of_date == "2026-08-07"


# ── the shared-cache regression (P0) ─────────────────────────────────────────

def test_redaction_does_not_mutate_its_input():
    """THE bug this whole design is shaped around. `redact_signals` is handed the object
    living in SignalsService._cache — shared by every caller for 45 minutes. Mutating it
    would strip tickers for every PAYING user until the next rebuild."""
    groups = _groups()
    before = groups.model_dump_json()

    redact_signals(groups, "pro")

    assert groups.model_dump_json() == before
    assert groups.congress.entries[0].symbol == "HONA"
    assert len(groups.congress.entries) == 10
    assert groups.congress.is_locked is False
    assert groups.congress.tier_required is None
    assert groups.congress.locked_count == 0


def test_redaction_returns_new_objects_not_aliases():
    """Even a non-mutating call that hands back the SAME nested object is a landmine: a
    later `.entries.append(...)` anywhere downstream would write into the cache."""
    groups = _groups()
    redacted = redact_signals(groups, "pro")
    assert redacted is not groups
    assert redacted.congress is not groups.congress
    assert redacted.congress.entries[0] is not groups.congress.entries[0]


def test_an_untouched_group_is_still_a_copy():
    """An EMPTY group is passed through unlocked — it must still be a copy, or the
    pass-through path quietly re-aliases the cache."""
    groups = _groups(congress=SignalGroupResponse(kind="congress", entries=[]))
    redacted = redact_signals(groups, "pro")
    assert redacted.congress is not groups.congress


# ── degenerate inputs ────────────────────────────────────────────────────────

def test_absent_groups_stay_absent():
    """One source failing → that card is None and iOS omits it. The gate must not
    resurrect it as an empty locked card."""
    redacted = redact_signals(SignalsGroupResponse(), "pro")
    assert redacted.congress is None
    assert redacted.whale is None
    assert redacted.earnings is None


def test_an_empty_group_is_not_flagged_as_locked():
    """Nothing was withheld, so claiming a lock would be a lie the UI can't render — iOS
    omits an entry-less card either way."""
    groups = _groups(whale=SignalGroupResponse(kind="whale", entries=[]))
    redacted = redact_signals(groups, "pro")
    assert redacted.whale.is_locked is False
    assert redacted.whale.locked_count == 0
    assert redacted.whale.entries == []


def test_a_single_entry_group_reports_a_locked_count_of_one():
    groups = _groups(congress=_group("congress", count=1))
    redacted = redact_signals(groups, "pro")
    assert redacted.congress.locked_count == 1
    assert len(redacted.congress.entries) == 1


def test_negative_and_zero_values_survive():
    """Earnings shockers are signed — a MISS is negative. Clamping or dropping it would
    turn a -25% miss into a beat on a locked card."""
    groups = _groups(
        earnings=SignalGroupResponse(
            kind="earnings",
            entries=[
                SignalRowResponse(rank=1, symbol="UZE", name="Uze", value=-25.4),
                SignalRowResponse(rank=2, symbol="AMZN", name="Amazon", value=0.0),
            ],
        )
    )
    redacted = redact_signals(groups, "pro")
    assert redacted.earnings.entries[0].value == -25.4


# ── the mask ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "symbol,expected_len",
    [
        ("HONA", 4),
        ("F", _MASK_MIN_LEN),            # 1-char tickers exist (F, T, X)
        ("AMZN", 4),
        ("GOOGL", 5),
        ("BRK.B", 5),
        ("VERYLONGSYM", _MASK_MAX_LEN),  # clamped so the chip can't stretch the row
        ("  NVDA  ", 4),                 # trimmed before measuring
    ],
)
def test_mask_preserves_length_within_the_clamp(symbol, expected_len):
    masked = _mask_symbol(symbol)
    assert masked == _MASK_CHAR * expected_len
    assert _MASK_MIN_LEN <= len(masked) <= _MASK_MAX_LEN


@pytest.mark.parametrize("symbol", ["", "   ", None, 123, [], {}])
def test_mask_of_a_missing_symbol_is_still_a_mask(symbol):
    """A blank slot above the stat reads as a rendering bug, not as a lock."""
    masked = _mask_symbol(symbol)
    assert masked and set(masked) == {_MASK_CHAR}
    assert _MASK_MIN_LEN <= len(masked) <= _MASK_MAX_LEN


def test_the_mask_is_not_a_plausible_ticker():
    """It must never be mistaken for real data if the client's blur fails to apply —
    fail-safe (visible dots) rather than fail-open (a readable symbol)."""
    masked = _mask_symbol("HONA")
    assert not masked.isalnum()
    assert masked.strip(_MASK_CHAR) == ""
