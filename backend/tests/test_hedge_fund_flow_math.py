"""
Math/regression tests for the Hedge Funds quarterly flow pipeline
(`HoldersService` static helpers).

The flow is stored in MILLIONS OF SHARES (not dollars): the net is the real
``numberOf13FsharesChange`` from the positions-summary — comparable across
quarters, and the one institutional-flow figure FMP reports completely. The
gross buy/sell SPLIT is estimated from the net + buyer/seller counts (13F
discloses positions, not transactions, so only the net is measured).

Background — the bugs these guard against:
  * The old DOLLAR formula multiplied the net share change by a quarter-end
    price. When 13F filings were incomplete, the price fallback exploded
    (~$2,985 vs a real ~$147), producing an axis-dominating outlier that got
    frozen in the cache and flattened every other quarter. Shares have no
    price multiplier, so that entire class of bug is impossible now.
  * The most recent quarters are unsettled (13F amendments keep arriving) and
    must always be recomputed — the volatile-refresh window.

All pure/static — no network, no Supabase, no instance construction.
"""

from __future__ import annotations

from app.services.holders_service import (
    HoldersService,
    _REFRESH_RECENT_QUARTERS,
)


def _summary(
    net_shares,
    *,
    new=200, incr=1700, closed=500, reduced=1400,
    total_invested=236_000_000_000, shares=1_200_000_000,
):
    """A positions-summary FMP row with a given net 13F share change."""
    return {
        "numberOf13FsharesChange": net_shares,
        "newPositions": new, "increasedPositions": incr,
        "closedPositions": closed, "reducedPositions": reduced,
        # price-related fields — must be IGNORED by the shares math:
        "totalInvested": total_invested, "numberOf13Fshares": shares,
    }


# ── _compute_quarter_flow: shares, net constraint, no price ───────────


def test_flow_net_is_share_change_in_millions():
    """net = numberOf13FsharesChange / 1e6, in millions of shares."""
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(
        _summary(-40_832_086)
    )
    assert abs(net - (-40.83)) < 0.01
    assert buyers == 1900 and sellers == 1900   # 200+1700 , 500+1400
    # The gross split must preserve buy - sell == net (buy/sell each round to
    # 2 dp, so allow a hair of rounding slack).
    assert abs((buy - sell) - net) < 0.05
    assert buy >= 0 and sell >= 0


def test_flow_positive_net_means_net_buying():
    buy, sell, net, _, _ = HoldersService._compute_quarter_flow(
        _summary(13_033_736)
    )
    assert net > 0
    assert buy > sell


def test_flow_has_no_price_dependency():
    """A huge totalInvested with a tiny share count (which used to explode the
    dollar price fallback to ~$2,985) must NOT affect the shares-based flow."""
    normal = HoldersService._compute_quarter_flow(_summary(-40_000_000))
    exploding = HoldersService._compute_quarter_flow(
        _summary(-40_000_000, total_invested=50_000_000_000, shares=16_700_000)
    )
    # Same net + counts → identical flow; the price fields are never read.
    assert normal == exploding
    # And the magnitude stays sane: tens of millions of shares, never billions.
    buy, sell, net, _, _ = exploding
    assert max(buy, sell) < 1000   # < 1000 million shares


def test_flow_zero_change_is_flat():
    buy, sell, net, _, _ = HoldersService._compute_quarter_flow(_summary(0))
    assert net == 0
    assert buy == 0 and sell == 0


# ── volatile refresh window ───────────────────────────────────────────


def test_recent_quarters_are_marked_volatile():
    """The most recent _REFRESH_RECENT_QUARTERS keys are always recomputed
    (never served stale from cache)."""
    pairs = HoldersService._generate_quarter_keys(8)
    volatile = pairs[-_REFRESH_RECENT_QUARTERS:]
    assert len(volatile) == _REFRESH_RECENT_QUARTERS
    assert volatile == sorted(pairs)[-_REFRESH_RECENT_QUARTERS:]
