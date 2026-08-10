"""Plan entitlements — the app's first tier-based feature gate.

Everything monetised before this was metered in credits, where the gate is a balance check
and an unknown tier simply means "no allocation". A FEATURE gate fails the other way: get
the default wrong and the paid surface is handed to everyone, silently and with no error to
notice. So the unknown/None/garbage cases are pinned as hard as the happy path.

Pure module — no network, no Supabase, no fixtures.
"""
from __future__ import annotations

import pytest

from app.services import entitlements as ent


# ── the ladder ───────────────────────────────────────────────────────────────

def test_the_shipped_ladder():
    assert ent.updates_ticker_limit("free") == 1
    assert ent.updates_ticker_limit("pro") == 15
    assert ent.updates_ticker_limit("premium") == 30


def test_limits_are_monotonic_up_the_ladder():
    """A paid tier must never see FEWER chips than the one below it. Cheap to get wrong
    when retuning three constants by hand, and invisible until a user complains."""
    limits = [ent.updates_ticker_limit(t) for t in ent.TIER_ORDER]
    assert limits == sorted(limits)
    assert len(set(limits)) == len(limits), "two tiers with the same limit is not a ladder"


def test_every_known_tier_has_a_limit():
    """A tier in TIER_ORDER but missing from the table would raise a KeyError out of
    `updates_ticker_limit` — a 500 on the tab bar for a paying user, from a typo."""
    for tier in ent.TIER_ORDER:
        assert tier in ent.UPDATES_TICKER_LIMITS


def test_normalisation_is_anchored_to_the_ladder_not_to_one_gates_table():
    """There is more than one gate now (updates chips, signals tickers). `normalize_tier`
    resolves against TIER_ORDER so adding a gate whose table omits a tier cannot silently
    downgrade a paying user everywhere else."""
    for tier in ent.TIER_ORDER:
        assert ent.normalize_tier(tier) == tier


# ── unknown tiers must fall CLOSED, never open ───────────────────────────────

@pytest.mark.parametrize("bogus", [
    None, "", "   ", "enterprise", "max", "plus", "p ro", "premium!", "0",
    0, 1, [], {}, True, 3.5, object(),
])
def test_unrecognised_tiers_resolve_to_free(bogus):
    """Falling open would hand the paid surface to every guest and to every degraded
    identity dict.

    Note `"max"` is in this list deliberately: "Max" is the DISPLAY name for the `premium`
    key (`subscription_service.TIER_DISPLAY_NAMES`), and a caller that sends the label
    instead of the key must not be silently upgraded to 30 chips.
    """
    assert ent.updates_ticker_limit(bogus) == ent.UPDATES_TICKER_LIMITS[ent.TIER_FREE]


def test_case_and_whitespace_are_normalised_not_rejected():
    """`users.tier` is a Postgres enum today, but the value also arrives from identity
    dicts and could arrive from a newer client. Tolerate presentation, not invention."""
    assert ent.updates_ticker_limit("PRO") == 15
    assert ent.updates_ticker_limit(" premium ") == 30
    assert ent.normalize_tier("Free") == ent.TIER_FREE


# ── the upsell target ────────────────────────────────────────────────────────

def test_required_tier_walks_one_rung_up():
    assert ent.required_tier_for_more_tickers("free") == "pro"
    assert ent.required_tier_for_more_tickers("pro") == "premium"


def test_the_top_tier_has_nothing_to_upsell():
    """None means "render no upgrade chip". Returning a tier here would show a Max user
    a button to buy Max."""
    assert ent.required_tier_for_more_tickers("premium") is None


def test_an_unknown_tier_is_upsold_as_if_free():
    assert ent.required_tier_for_more_tickers("enterprise") == "pro"
    assert ent.required_tier_for_more_tickers(None) == "pro"


# ── selection: the alphabetical rule ─────────────────────────────────────────

def test_the_stated_rule_from_the_product_decision():
    """The literal worked example: BDU, DIF, BDX on a one-chip plan shows BDU."""
    assert ent.select_visible_tickers(["BDU", "DIF", "BDX"], 1) == ["BDU"]


def test_selection_is_independent_of_input_order():
    """Determinism is the whole reason the rule is alphabetical rather than group order —
    the same group must yield the same chip however `portfolio_items.position` is arranged."""
    for order in (["BDU", "DIF", "BDX"], ["BDX", "BDU", "DIF"], ["DIF", "BDX", "BDU"]):
        assert ent.select_visible_tickers(order, 1) == ["BDU"]


def test_a_limit_at_or_above_the_group_size_returns_everything():
    got = ent.select_visible_tickers(["BDU", "DIF", "BDX"], 15)
    assert got == ["BDU", "BDX", "DIF"]
    assert ent.select_visible_tickers(["AAPL"], 30) == ["AAPL"]


def test_empty_and_none_inputs():
    assert ent.select_visible_tickers([], 5) == []
    assert ent.select_visible_tickers(None, 5) == []


def test_a_non_positive_limit_degrades_to_empty_rather_than_raising():
    """An unknown tier must cost the user their chips, never their tab bar."""
    assert ent.select_visible_tickers(["AAPL"], 0) == []
    assert ent.select_visible_tickers(["AAPL"], -1) == []


def test_duplicates_collapse_so_a_limit_of_n_yields_n_distinct_chips():
    """A duplicate would render as two identical pills AND consume a paid slot. iOS keys
    its ForEach by scope, so a dup is also a SwiftUI identity collision."""
    assert ent.select_visible_tickers(["AAPL", "AAPL", "aapl", "MSFT"], 2) == ["AAPL", "MSFT"]


def test_blank_and_non_string_entries_are_dropped():
    assert ent.select_visible_tickers(["", "   ", "AAPL", None, 42, "MSFT"], 5) == [
        "AAPL", "MSFT"
    ]


def test_whitespace_is_trimmed_before_comparison():
    assert ent.select_visible_tickers([" AAPL ", "AAPL"], 5) == ["AAPL"]


def test_case_insensitive_ordering_not_ascii_ordering():
    """Plain `sorted()` puts every uppercase symbol before every lowercase one, so a
    lowercase row from an unmigrated source would sort to the end regardless of letter."""
    assert ent.select_visible_tickers(["bdx", "BDU", "aaa"], 3) == ["aaa", "BDU", "bdx"]


def test_non_ascii_symbols_do_not_raise():
    """Nothing in the product creates these, but `casefold()` on arbitrary upstream text is
    exactly where a tab-bar 500 would come from."""
    got = ent.select_visible_tickers(["ÅBC", "ABC", "日経"], 3)
    assert len(got) == 3


def test_selection_never_exceeds_the_limit_for_any_group_size():
    for size in range(0, 40):
        tickers = [f"T{i:03d}" for i in range(size)]
        for tier in (*ent.TIER_ORDER, "bogus"):
            limit = ent.updates_ticker_limit(tier)
            got = ent.select_visible_tickers(tickers, limit)
            assert len(got) == min(size, limit)
            assert len(set(got)) == len(got)


def test_the_input_list_is_not_mutated():
    """The caller computes `locked_count` from the original list right after this call."""
    original = ["DIF", "BDU", "BDX"]
    ent.select_visible_tickers(original, 1)
    assert original == ["DIF", "BDU", "BDX"]
