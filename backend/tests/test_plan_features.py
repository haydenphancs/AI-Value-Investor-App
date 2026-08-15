"""Paywall copy must never drift from the gate that enforces it.

This file IS the justification for serving the paywall's feature list from the backend
instead of hardcoding it in Swift. Without these assertions the design is decorative: the
numbers would simply have moved from one place that can drift into another.

The rule every test here enforces: a number shown on the paywall is READ from
`entitlements.py`, not typed twice.
"""

import re

import pytest

from app.services import entitlements
from app.services import plan_features as pf

REPORT_COST = 20
CHAT_COST = 1

# Mirrors the seeded `plan_credits` rows (migration 100). Only used as INPUT — every
# assertion below derives its expectation from `entitlements`, never from this table.
CREDITS = {
    entitlements.TIER_FREE: 50,
    entitlements.TIER_PRO: 1200,
    entitlements.TIER_MAX: 4000,
}


def rows_for(tier: str, *, credits=None, report_cost=REPORT_COST, chat_cost=CHAT_COST):
    return pf.features_for_tier(
        tier,
        monthly_credits=CREDITS[tier] if credits is None else credits,
        report_cost=report_cost,
        chat_cost=chat_cost,
    )


def row(tier: str, key: str, **kw):
    matches = [r for r in rows_for(tier, **kw) if r["key"] == key]
    assert len(matches) == 1, f"expected exactly one {key!r} row for {tier}, got {len(matches)}"
    return matches[0]


# ── The anti-drift tests — the reason this module exists ─────────────────────────────


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_updates_row_states_the_limit_the_server_enforces(tier):
    limit = entitlements.updates_ticker_limit(tier)
    title = row(tier, pf.KEY_UPDATES_TICKERS)["title"]
    assert str(limit) in title, f"{tier}: {title!r} does not state the enforced limit {limit}"
    # Not "1 tickers" / "15 ticker" — the row is read as a sentence.
    assert ("ticker" if limit == 1 else "tickers") in title


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_whale_tracking_row_states_the_follow_limit(tier):
    limit = entitlements.whale_follow_limit(tier)
    title = row(tier, pf.KEY_WHALE_TRACKING)["title"]
    if limit is None:
        assert "unlimited" in title.lower()
    else:
        assert str(limit) in title, f"{tier}: {title!r} does not state the enforced limit {limit}"


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_a_none_follow_limit_never_leaks_a_sentinel_into_the_copy(tier):
    """`whale_follow_limit` returns None for Max. Formatted naively that prints
    "Track None investors" on the most expensive plan."""
    text = " ".join(f"{r['title']} {r['detail']}" for r in rows_for(tier))
    assert "None" not in text
    assert "-1" not in text


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_credits_row_derives_both_figures_from_the_real_costs(tier):
    credits = CREDITS[tier]
    detail = row(tier, pf.KEY_CREDITS)["detail"]
    assert f"{credits // REPORT_COST:,}" in detail
    assert f"{credits // CHAT_COST:,}" in detail
    assert f"{credits:,}" in row(tier, pf.KEY_CREDITS)["title"]


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_credits_are_described_as_one_shared_pool(tier):
    """Reports and chat draw on the SAME balance. "2 reports and 50 replies" would
    overstate a 50-credit plan by 2x."""
    detail = row(tier, pf.KEY_CREDITS)["detail"]
    reports = f"{CREDITS[tier] // REPORT_COST:,} AI research reports"
    replies = f"{CREDITS[tier] // CHAT_COST:,} Cay AI replies"
    assert f"{reports} or {replies}" in detail
    assert f"{reports} and {replies}" not in detail
    assert "shared pool" in detail


# ── Gate parity: `included` must equal the boolean the server actually checks ─────────


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
@pytest.mark.parametrize("key,gate", [
    (pf.KEY_SIGNALS, entitlements.signals_unlocked),
    (pf.KEY_WHALE_DETAIL, entitlements.whale_detail_unlocked),
    (pf.KEY_LEARN_AUDIO, entitlements.learn_audio_unlocked),
])
def test_included_matches_the_enforcing_gate(tier, key, gate):
    assert row(tier, key)["included"] is gate(tier)


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_quantity_rows_are_never_rendered_as_locked(tier):
    """Free genuinely gets 1 Updates ticker and 1 tracked investor. Marking these
    `included=False` would draw them struck through and misdescribe the gate."""
    for key in (pf.KEY_CREDITS, pf.KEY_UPDATES_TICKERS, pf.KEY_WHALE_TRACKING):
        assert row(tier, key)["included"] is True


# ── The Investor Journey exception ───────────────────────────────────────────────────


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_journey_narration_is_offered_on_every_tier_as_an_always_row(tier):
    r = row(tier, pf.KEY_JOURNEY_AUDIO)
    assert r["included"] is True
    assert r["group"] == pf.GROUP_ALWAYS
    assert entitlements.journey_audio_unlocked(tier) is True


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_no_paid_row_claims_narration_in_general(tier):
    """A blanket "unlock narration" sells the reader something they already have: 207 of
    the 230 clips are Journey, and those are free on every tier."""
    for r in rows_for(tier):
        if r["group"] != pf.GROUP_PLAN:
            continue
        assert "Journey" not in r["title"] and "Journey" not in r["detail"]
        if "narration" in r["title"].lower():
            # The only paid narration row must name WHICH content it covers.
            assert r["key"] == pf.KEY_LEARN_AUDIO
            assert "Money Moves" in r["title"] and "book" in r["title"].lower()


# ── Claims the app cannot back up ────────────────────────────────────────────────────

# "priority"/"faster"/"queue": no code path reads `tier` for scheduling — the report
# semaphore and MAX_CONCURRENT_REPORTS_PER_USER are global. This is the exact claim the
# old paywall made ("plus priority analysis") and it was never true.
# "personaliz": CHAT_PERSONALIZATION_ENABLED / CHAT_MEMORY_FACTS_ENABLED ship False.
_FORBIDDEN = re.compile(
    r"priorit|faster|advanced analytics|personaliz|queue|unlimited reports",
    re.IGNORECASE,
)


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_no_row_claims_something_the_app_does_not_do(tier):
    for r in rows_for(tier):
        blob = f"{r['title']} {r['detail']}"
        assert not _FORBIDDEN.search(blob), f"{tier}/{r['key']}: unsupported claim in {blob!r}"


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_no_row_names_a_real_investor_or_quotes_a_roster_size(tier):
    """`FREE_TIER_WHALE_NAME` is a living person and the roster SIZE lives in
    whale_registry.json — quoting either creates a drift source or an
    app-store-listing.md rule-1 problem on the most screenshotted screen.

    The only investor count allowed to appear is the follow limit `entitlements` enforces,
    so this doubles as a second anti-drift assertion: "Track 10 investors" is fine because
    10 came from `whale_follow_limit`; "browse all 53 investors" is not, because 53 came
    from a JSON file this module cannot see.
    """
    blob = " ".join(f"{r['title']} {r['detail']}" for r in rows_for(tier))
    assert entitlements.FREE_TIER_WHALE_NAME not in blob

    limit = entitlements.whale_follow_limit(tier)
    for figure in re.findall(r"\b([\d,]+)\s+(?:investors?|whales?)\b", blob):
        assert int(figure.replace(",", "")) == limit, (
            f"{tier}: copy states {figure} investors, which is not the enforced "
            f"follow limit ({limit}) — a count from outside entitlements.py"
        )


# ── Shape / contract ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier", entitlements.TIER_ORDER)
def test_rows_are_well_formed(tier):
    rows = rows_for(tier)
    assert rows, f"{tier} produced no rows"
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), f"duplicate keys for {tier}: {keys}"
    for r in rows:
        assert set(r) == {"key", "title", "detail", "icon", "accent", "included", "group"}
        assert r["key"] and r["title"] and r["detail"]
        assert r["group"] in (pf.GROUP_PLAN, pf.GROUP_ALWAYS)
        assert isinstance(r["included"], bool)
        # iOS maps accents to audited tokens by exact string; an unmapped one falls back
        # to a neutral colour and the row silently loses its meaning.
        assert r["accent"] in pf.ACCENTS
        # SF Symbol names, validated client-side too — but a typo here is cheaper to
        # catch than a blank glyph in the App Store screenshot.
        assert re.fullmatch(r"[a-z0-9.]+", r["icon"]), r["icon"]


def test_every_tier_offers_the_same_row_keys():
    """The client renders one plan at a time and animates between them. A key present on
    Pro but absent on Free would make rows appear and disappear on selection instead of
    changing value — which reads as a bug, not a comparison."""
    per_tier = {t: [r["key"] for r in rows_for(t)] for t in entitlements.TIER_ORDER}
    first = per_tier[entitlements.TIER_FREE]
    for tier, keys in per_tier.items():
        assert keys == first, f"{tier} row keys diverge: {keys} != {first}"


def test_the_always_group_is_identical_across_tiers():
    def always(t):
        return [r for r in rows_for(t) if r["group"] == pf.GROUP_ALWAYS]

    baseline = always(entitlements.TIER_FREE)
    assert baseline
    for tier in entitlements.TIER_ORDER:
        assert always(tier) == baseline, f"{tier}'s 'included on every plan' block differs"


# ── Degradation ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier", ["platinum", "PRO", " free ", "", None, 7, "premium "])
def test_an_unrecognised_tier_yields_no_rows_rather_than_free_s(tier):
    """`normalize_tier` folds anything unknown to "free". Rendering Free's limits under a
    "Platinum" header would state limits the server is not enforcing for that plan — a
    worse failure than an empty list, which makes the client use its bundled table."""
    assert pf.features_for_tier(
        tier, monthly_credits=999, report_cost=REPORT_COST, chat_cost=CHAT_COST
    ) == []


@pytest.mark.parametrize("credits", [0, None, -5])
def test_a_missing_or_negative_credit_allocation_degrades_instead_of_raising(credits):
    """`plan_credits.monthly_credits` is a column a human edits. A NULL there must not
    take down the one screen that has to render for everyone."""
    rows = rows_for(entitlements.TIER_FREE, credits=credits)
    assert rows
    assert "0 credits a month" in row(entitlements.TIER_FREE, pf.KEY_CREDITS, credits=credits)["title"]


@pytest.mark.parametrize("report_cost,chat_cost", [(0, 1), (20, 0), (0, 0)])
def test_a_zero_action_cost_omits_its_clause_instead_of_dividing_by_zero(report_cost, chat_cost):
    r = row(entitlements.TIER_PRO, pf.KEY_CREDITS, report_cost=report_cost, chat_cost=chat_cost)
    assert "1,200 credits a month" in r["title"]
    assert r["detail"]
    if report_cost == 0:
        assert "AI research reports" not in r["detail"]
    if chat_cost == 0:
        assert "Cay AI replies" not in r["detail"]
    if report_cost == 0 and chat_cost == 0:
        assert "About" not in r["detail"]


_ENTITLEMENT_TABLES = frozenset({
    "UPDATES_TICKER_LIMITS", "WHALE_FOLLOW_LIMITS", "SIGNALS_UNLOCKED_TIERS",
    "WHALE_DETAIL_UNLOCKED_TIERS", "LEARN_AUDIO_UNLOCKED_TIERS",
    "JOURNEY_AUDIO_UNLOCKED_TIERS",
})


def _tables_indexed_in(source: str) -> set:
    """Names from `_ENTITLEMENT_TABLES` that `source` SUBSCRIPTS.

    Parsed, not grepped. A substring scan matches this module's own docstring — which
    quotes `UPDATES_TICKER_LIMITS[tier]` precisely to explain why it must not do that —
    and a guard that fires on its own explanation is a guard nobody keeps.
    """
    import ast

    hits = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Subscript):
            continue
        target = node.value
        name = (
            target.attr if isinstance(target, ast.Attribute)
            else target.id if isinstance(target, ast.Name)
            else None
        )
        if name in _ENTITLEMENT_TABLES:
            hits.add(name)
    return hits


def test_the_module_never_indexes_the_entitlement_tables_directly():
    """Indexing raises KeyError on an unknown tier; the accessor functions normalise.

    A source-level assertion because the behaviour it protects is only reachable through
    a tier no test can name in advance — the whole point is the tier nobody thought of.
    """
    import inspect

    # The detector is mutation-tested inline, so this cannot pass vacuously: if the AST
    # walk stopped working, the second pair of assertions goes red.
    assert _tables_indexed_in("entitlements.UPDATES_TICKER_LIMITS[tier]") == {
        "UPDATES_TICKER_LIMITS"
    }
    assert _tables_indexed_in("WHALE_FOLLOW_LIMITS[t]") == {"WHALE_FOLLOW_LIMITS"}
    assert _tables_indexed_in("entitlements.whale_follow_limit(tier)") == set()

    offenders = _tables_indexed_in(inspect.getsource(pf))
    assert not offenders, f"plan_features indexes {sorted(offenders)} — use the accessors"
