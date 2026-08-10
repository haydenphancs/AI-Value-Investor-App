"""Whales tier gate — the Pro/Max lock on tracking and on position-level detail.

The packaging: browsing is free on every tier (all 53 whales listed, searchable, openable,
with header / bio / risk profile / stat tiles / sector exposure). What is paid is
  • TRACKING — Free may follow only the one designated whale, Pro 10, Max unlimited; and
  • POSITION DETAIL — holdings, trade groups and the AI sentiment summary.

Three things here are load-bearing beyond the happy path:

  • **The redactor must not mutate its input.** `_whale_profile_cache` holds ONE
    `WhaleProfileResponse` per whale, shared by every caller for an hour — its own
    in-flight comment says the build is "follow-state-free; each caller overlays its own
    per-user follow state". Stripping it in place empties the holdings for every PAYING
    user until the next rebuild.
  • **Unfollow is never gated.** A user over the limit (post-downgrade, or from before this
    gate existed) must always be able to get back under it.
  • **Unknown tiers fall CLOSED.** A feature gate that defaults open hands the paid surface
    to everyone with no error to notice.

Pure module — no network, no Supabase, no fixtures.
"""
from __future__ import annotations

import pytest

from app.schemas.whale import (
    TrendingWhaleResponse,
    WhaleBehaviorSummaryResponse,
    WhaleHoldingResponse,
    WhaleProfileResponse,
    WhaleSectorAllocationResponse,
    WhaleTradeGroupResponse,
    WhaleTradeResponse,
)
from app.services import entitlements as ent
from app.services.whale_service import (
    _LOCKED_BEHAVIOR,
    _apply_follow_locks,
    redact_whale_profile,
)


# ── builders ─────────────────────────────────────────────────────────────────

_HOLDING_TICKERS = ["MSFT", "BRK-B", "WM", "CNI", "ECL"]


def _profile(**overrides) -> WhaleProfileResponse:
    base = dict(
        id="w1",
        name="Warren Buffett",
        title="CEO of Berkshire Hathaway",
        description="The Oracle of Omaha.",
        firm_name="Berkshire Hathaway",
        risk_profile="Conservative",
        portfolio_value=302_500_000_000.0,
        ytd_return=12.4,
        sector_exposure=[
            WhaleSectorAllocationResponse(
                id="s1", name="Technology", percentage=41.2, color_hex="3B82F6"
            ),
            WhaleSectorAllocationResponse(
                id="s2", name="Financials", percentage=22.8, color_hex="10B981"
            ),
        ],
        current_holdings=[
            WhaleHoldingResponse(
                id=f"h{i}", ticker=t, company_name=f"{t} Inc.",
                allocation=10.0 - i, change_percent=0.5,
            )
            for i, t in enumerate(_HOLDING_TICKERS)
        ],
        recent_trade_groups=[
            WhaleTradeGroupResponse(
                id="g1", date="2026-03-31", trade_count=3, net_action="SOLD",
                net_amount=-5_150_000_000.0, summary="Trimmed positions",
                insights=["Exited MSFT entirely"],
                trades=[
                    WhaleTradeResponse(
                        id="t1", ticker="MSFT", company_name="Microsoft",
                        action="SOLD", trade_type="Closed", amount=3_720_000_000.0,
                        previous_allocation=10.5, new_allocation=0.0,
                        date="2026-03-31",
                    )
                ],
            )
        ],
        recent_trades=[
            WhaleTradeResponse(
                id="t2", ticker="BRK-B", company_name="Berkshire", action="SOLD",
                trade_type="Decreased", amount=1_130_000_000.0,
                previous_allocation=27.6, new_allocation=25.8, date="2026-03-31",
            )
        ],
        behavior_summary=WhaleBehaviorSummaryResponse(
            action="Accumulating", primary_focus="Technology",
            secondary_action="Trimming", secondary_focus="Financials",
        ),
        sentiment_summary="Positioning defensively into a slowing consumer.",
        is_following=True,
    )
    base.update(overrides)
    return WhaleProfileResponse(**base)


def _roster(followed: int = 0, include_free: bool = True) -> list[TrendingWhaleResponse]:
    names = ["Bill Gates", "Warren Buffett", "Ray Dalio", "Cathie Wood", "Michael Burry"]
    if not include_free:
        names = names[1:]
    out = []
    for i, n in enumerate(names):
        out.append(
            TrendingWhaleResponse(
                id=f"w{i}", name=n, category="investors", is_following=i < followed
            )
        )
    return out


# ── the limit table ──────────────────────────────────────────────────────────

def test_the_shipped_whale_ladder():
    assert ent.whale_follow_limit("free") == 1
    assert ent.whale_follow_limit("pro") == 10
    assert ent.whale_follow_limit("premium") is None      # unlimited


def test_unlimited_is_none_not_a_sentinel():
    """`None`, not -1 or a huge int. A sentinel eventually gets compared as a real cap —
    `count >= -1` is true immediately, which would lock Max out of everything."""
    assert ent.WHALE_FOLLOW_LIMITS[ent.TIER_MAX] is None


@pytest.mark.parametrize("tier", ["free", "FREE", "", None, "enterprise", 0, [], {}])
def test_unknown_tiers_fall_closed(tier):
    assert ent.whale_follow_limit(tier) == 1
    assert ent.whale_detail_unlocked(tier) is False
    assert ent.required_tier_for_whales(tier) == ent.TIER_PRO


@pytest.mark.parametrize("tier", ["pro", "premium", " Pro ", "PREMIUM"])
def test_paid_tiers_unlock_detail(tier):
    assert ent.whale_detail_unlocked(tier) is True
    assert ent.required_tier_for_whales(tier) is None


def test_whales_and_signals_share_one_floor():
    """Kept as the same frozenset so the two gates cannot drift into 'Pro unlocks signals
    but only Max unlocks whales' — which no pricing page would ever describe."""
    assert ent.WHALE_DETAIL_UNLOCKED_TIERS is ent.SIGNALS_UNLOCKED_TIERS


def test_every_tier_in_the_ladder_has_a_follow_limit():
    for tier in ent.TIER_ORDER:
        assert tier in ent.WHALE_FOLLOW_LIMITS


# ── the designated free whale ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name,expected",
    [
        ("Bill Gates", True),
        ("bill gates", True),
        ("  Bill Gates  ", True),
        ("BILL GATES", True),
        ("Warren Buffett", False),
        ("Bill Gates Foundation", False),   # prefix must not match
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_free_whale_match_is_exact_but_forgiving_about_presentation(name, expected):
    assert ent.is_free_tier_whale(name) is expected


# ── roster locking ───────────────────────────────────────────────────────────

def test_max_locks_nothing():
    roster = _roster()
    locked = _apply_follow_locks(roster, "premium")
    assert all(not w.is_locked for w in locked)


def test_free_locks_everyone_except_the_free_whale():
    locked = _apply_follow_locks(_roster(), "free")
    by_name = {w.name: w.is_locked for w in locked}
    assert by_name["Bill Gates"] is False
    assert all(v for n, v in by_name.items() if n != "Bill Gates")


def test_pro_locks_nothing_until_the_limit_is_reached():
    """The 10th follow is offered, the 11th is not — so a Pro user with 5 follows sees an
    unlocked roster, not a wall."""
    assert all(not w.is_locked for w in _apply_follow_locks(_roster(followed=2), "pro"))


def test_pro_locks_unfollowed_rows_once_at_the_limit(monkeypatch):
    monkeypatch.setitem(ent.WHALE_FOLLOW_LIMITS, ent.TIER_PRO, 2)
    locked = _apply_follow_locks(_roster(followed=2), "pro")
    assert [w.is_locked for w in locked] == [False, False, True, True, True]


def test_an_already_followed_whale_is_never_locked():
    """Including on Free, and including whales followed before the gate existed — a locked
    button on something you already track reads as data loss, and it would strand a user
    above the cap with no way back down."""
    roster = _roster(followed=3)          # Gates + Buffett + Dalio already followed
    locked = _apply_follow_locks(roster, "free")
    assert [w.is_locked for w in locked] == [False, False, False, True, True]


def test_locking_does_not_mutate_the_cached_roster():
    """`_whale_list_cache` holds these objects. In-place writes would pin one plan's locks
    onto that user's next 5 minutes of requests."""
    roster = _roster()
    _apply_follow_locks(roster, "free")
    assert all(not w.is_locked for w in roster)


def test_an_empty_roster_does_not_raise():
    assert _apply_follow_locks([], "free") == []


def test_free_with_no_free_whale_in_the_roster_locks_everything():
    """A category filter that excludes the free whale (e.g. ?category=politicians) must not
    accidentally unlock the first row."""
    locked = _apply_follow_locks(_roster(include_free=False), "free")
    assert all(w.is_locked for w in locked)


# ── profile redaction ────────────────────────────────────────────────────────

def test_locked_profile_withholds_position_detail():
    redacted = redact_whale_profile(_profile(), "pro")
    assert redacted.current_holdings == []
    assert redacted.recent_trade_groups == []
    assert redacted.recent_trades == []
    assert redacted.sentiment_summary == ""
    assert redacted.behavior_summary == _LOCKED_BEHAVIOR
    assert redacted.is_locked is True
    assert redacted.tier_required == "pro"


def test_locked_profile_keeps_everything_a_reader_judges_the_investor_by():
    """The profile must PREVIEW the product, not wall it — this is the 5.1.1(v) posture."""
    redacted = redact_whale_profile(_profile(), "pro")
    assert redacted.name == "Warren Buffett"
    assert redacted.description
    assert redacted.risk_profile == "Conservative"
    assert redacted.portfolio_value == 302_500_000_000.0
    assert redacted.ytd_return == 12.4
    assert len(redacted.sector_exposure) == 2          # the donut stays
    assert redacted.is_following is True               # follow state survives


def test_no_holding_ticker_survives_anywhere_in_a_locked_profile():
    """Asserted on the serialised JSON, not the object graph: a field added later that
    carries a position through (a nested trade, a 'top pick' string) fails here, not in
    production."""
    blob = redact_whale_profile(_profile(), "pro").model_dump_json()
    for ticker in _HOLDING_TICKERS:
        assert ticker not in blob, f"{ticker} leaked into a locked profile"


def test_the_behaviour_summary_prose_is_withheld_too():
    """'Accumulating Technology' IS the withheld holdings detail, restated. Blanking the
    lists while leaving this would describe the book we just refused to show."""
    blob = redact_whale_profile(_profile(), "pro").model_dump_json()
    assert "Accumulating" not in blob
    assert "Technology" in blob      # ...but the SECTOR name stays, via sector_exposure


# ── the shared-cache regression (P0) ─────────────────────────────────────────

def test_redaction_does_not_mutate_its_input():
    """THE bug this design is shaped around. `_whale_profile_cache` holds one instance per
    whale for an hour, shared by every caller regardless of plan."""
    profile = _profile()
    before = profile.model_dump_json()

    redact_whale_profile(profile, "pro")

    assert profile.model_dump_json() == before
    assert len(profile.current_holdings) == len(_HOLDING_TICKERS)
    assert profile.sentiment_summary
    assert profile.is_locked is False
    assert profile.tier_required is None


def test_redaction_returns_a_new_object():
    profile = _profile()
    assert redact_whale_profile(profile, "pro") is not profile


@pytest.mark.asyncio
async def test_a_free_request_does_not_poison_the_cache_for_the_next_paid_one():
    """The end-to-end form, through the real `get_whale_profile` and the real Tier-1 cache:
    the ORDER of two requests against one shared entry must not matter.

    This is the shape the bug would actually take in production — a free user opens Warren
    Buffett, and every Pro user who opens him for the next hour gets an empty portfolio.
    """
    from app.services import whale_service as ws

    ws._whale_profile_cache.clear()
    ws._cache_set(ws._whale_profile_cache, "profile:w1", _profile())
    svc = ws.WhaleService()

    free = await svc.get_whale_profile(whale_id="w1", user_id=None, tier="free")
    paid = await svc.get_whale_profile(whale_id="w1", user_id=None, tier="pro")

    assert free.is_locked is True and free.current_holdings == []
    assert paid.is_locked is False
    assert [h.ticker for h in paid.current_holdings] == _HOLDING_TICKERS
    assert paid.sentiment_summary

    # And the cached object itself is still whole, not just this one response.
    cached = ws._cache_get(ws._whale_profile_cache, "profile:w1", ws.WHALE_PROFILE_CACHE_TTL)
    assert len(cached.current_holdings) == len(_HOLDING_TICKERS)


@pytest.mark.asyncio
async def test_omitting_tier_defaults_to_locked():
    """`get_whale_profile` has a defaulted `tier`, so a call site that forgets to pass it
    must withhold rather than hand the paid sections to everyone."""
    from app.services import whale_service as ws

    ws._whale_profile_cache.clear()
    ws._cache_set(ws._whale_profile_cache, "profile:w1", _profile())

    result = await ws.WhaleService().get_whale_profile(whale_id="w1", user_id=None)
    assert result.is_locked is True


# ── the free whale is exempt ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_designated_free_whale_is_fully_unlocked_on_free():
    """Free gets ONE investor in full — the point of designating one. Without this a Free
    user follows Gates, sees his trades in the feed, and hits a paywall on every tap into
    them: a feed that leads nowhere and an example that never demonstrates anything."""
    from app.services import whale_service as ws

    ws._whale_profile_cache.clear()
    ws._cache_set(
        ws._whale_profile_cache, "profile:gates",
        _profile(id="gates", name=ent.FREE_TIER_WHALE_NAME),
    )

    result = await ws.WhaleService().get_whale_profile(
        whale_id="gates", user_id=None, tier="free"
    )
    assert result.is_locked is False
    assert len(result.current_holdings) == len(_HOLDING_TICKERS)
    assert result.sentiment_summary


@pytest.mark.asyncio
async def test_every_other_whale_stays_locked_on_free():
    """The exemption is for ONE whale, not a hole in the gate."""
    from app.services import whale_service as ws

    ws._whale_profile_cache.clear()
    ws._cache_set(ws._whale_profile_cache, "profile:w1", _profile())   # Warren Buffett

    result = await ws.WhaleService().get_whale_profile(
        whale_id="w1", user_id=None, tier="free"
    )
    assert result.is_locked is True


# ── the activity feed honours the allowance ──────────────────────────────────
#
# Reuses the fake-Supabase harness from test_whale_activity_feed.py rather than a second
# copy of it.

def _activity_tables():
    """Three followed whales, one of them the designated free whale."""
    tg = {
        "date": "2026-07-01", "net_action": "BOUGHT", "trade_count": 3,
        "net_amount": 1_200_000.0, "summary": "Added 3 positions",
        "net_amount_range": None, "disclosure_date": None, "transaction_date": None,
    }
    return {
        "whale_follows": [{"whale_id": "wa"}, {"whale_id": "wb"}, {"whale_id": "wc"}],
        "whale_trade_groups": [
            {**tg, "id": "t1", "whale_id": "wa"},
            {**tg, "id": "t2", "whale_id": "wb"},
            {**tg, "id": "t3", "whale_id": "wc"},
        ],
        "whales": [
            {"id": "wa", "name": ent.FREE_TIER_WHALE_NAME, "avatar_url": None,
             "category": "investors", "firm_name": "Gates Foundation Trust"},
            {"id": "wb", "name": "Warren Buffett", "avatar_url": None,
             "category": "investors", "firm_name": "Berkshire Hathaway"},
            {"id": "wc", "name": "Ray Dalio", "avatar_url": None,
             "category": "investors", "firm_name": "Bridgewater"},
        ],
    }


class _StrictQuery:
    """A fake that HONOURS `in_` and `ilike`, unlike the shared one in
    test_whale_activity_feed.py which treats every filter as a no-op.

    Needed because the whole point here is that the tier truncation narrows `whale_ids`
    before the trade-group query — against a fake that ignores `in_`, the narrowing is
    computed and then discarded, and the test passes or fails for reasons unrelated to the
    gate. The shared fake stays permissive on purpose (its tests supply pre-filtered rows),
    so this is a local stricter one rather than a change to it.
    """

    def __init__(self, data):
        self._data = data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def in_(self, column, values):
        allowed = {str(v) for v in values}
        return _StrictQuery([r for r in self._data if str(r.get(column)) in allowed])

    def ilike(self, column, pattern):
        needle = str(pattern).strip().casefold()
        return _StrictQuery([
            r for r in self._data
            if str(r.get(column, "")).strip().casefold() == needle
        ])

    def execute(self):
        return type("R", (), {"data": self._data})()


class _StrictSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _StrictQuery(self._tables.get(name, []))


def _feed_for(monkeypatch, tier):
    from app.services import whale_service as ws
    import asyncio

    ws._whale_activity_cache.clear()
    monkeypatch.setattr(ws, "get_supabase", lambda: _StrictSupabase(_activity_tables()))
    ws._free_whale_id = None                       # force a fresh name→id resolution
    try:
        return asyncio.get_event_loop().run_until_complete(
            ws.WhaleService().get_whale_activity_feed("u1", tier=tier)
        )
    finally:
        ws._free_whale_id = None


def test_free_feed_carries_only_the_designated_whale(monkeypatch):
    """The follow rows are NOT deleted — they are simply not served. "Free tracks one
    investor" has to be true on the timeline too, not only on the Follow button."""
    names = {a.entity_name for a in _feed_for(monkeypatch, "free")}
    assert names == {ent.FREE_TIER_WHALE_NAME}


def test_paid_feed_carries_every_follow(monkeypatch):
    names = {a.entity_name for a in _feed_for(monkeypatch, "premium")}
    assert len(names) == 3


def test_upgrading_restores_the_withheld_whales_immediately(monkeypatch):
    """The rows survived the Free period, so an upgrade is not a re-follow exercise. Also
    pins that the two tiers do not share a cache entry."""
    assert len(_feed_for(monkeypatch, "free")) == 1
    assert len(_feed_for(monkeypatch, "premium")) == 3


# ── degenerate inputs ────────────────────────────────────────────────────────

def test_redacting_an_already_empty_profile_is_a_no_op_but_still_flags():
    """A whale with no hydrated data must still report as locked, or the client renders an
    'empty portfolio' rather than an upgrade prompt."""
    redacted = redact_whale_profile(
        _profile(
            current_holdings=[], recent_trade_groups=[], recent_trades=[],
            sector_exposure=[], sentiment_summary="",
        ),
        "pro",
    )
    assert redacted.is_locked is True
    assert redacted.sector_exposure == []


def test_congressional_profile_redacts_without_13f_sections():
    """Politicians carry no sector exposure or holdings — the gate lands on trades and
    sentiment only, and must not trip over the absent sections."""
    redacted = redact_whale_profile(
        _profile(sector_exposure=[], current_holdings=[], portfolio_as_of=None), "pro"
    )
    assert redacted.recent_trade_groups == []
    assert redacted.sentiment_summary == ""
    assert redacted.is_locked is True


def test_redacted_profile_still_validates_against_the_response_model():
    """The guard against the gate shipping a shape iOS crashes on — `behavior_summary` is
    the one non-defaulted field, so blanking it wrongly would 500 or fail to decode."""
    dumped = redact_whale_profile(_profile(), "pro").model_dump()
    revalidated = WhaleProfileResponse(**dumped)
    assert revalidated.is_locked is True
    assert revalidated.behavior_summary.action == ""
