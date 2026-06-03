"""
Math/regression tests for the Hedge Funds quarterly flow pipeline
(`HoldersService` static helpers).

NAMING: "hedge fund" / the `hedge_fund_quarters` table = FMP 13F institutional-
ownership data; the UI labels it "Institutions" (SmartMoneyTab.hedgeFunds).

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
    """A huge totalInvested must NOT affect the shares-based flow. The old dollar
    formula divided totalInvested by the share count and exploded; the shares
    math never reads price fields. (Share count stays realistic here so the
    magnitude guard isn't legitimately triggered — that's covered separately.)"""
    normal = HoldersService._compute_quarter_flow(_summary(-40_000_000))
    exploding = HoldersService._compute_quarter_flow(
        _summary(-40_000_000, total_invested=50_000_000_000)
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


# ── split adjustment + magnitude guard ────────────────────────────────


def test_flow_suppresses_impossible_change():
    """A net change exceeding ~half the shares HELD can't be real flow — it's a
    corporate-action / data artifact (reverse-split micro-caps, mergers like
    SIRI). Suppress it to zero flow (chart renders no bar) but KEEP the real
    holder counts so the row isn't re-fetched forever."""
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(
        _summary(-40_000_000, shares=16_700_000)   # |net| 40M >> 0.5 * 16.7M
    )
    assert (buy, sell, net) == (0.0, 0.0, 0.0)
    assert buyers == 1900 and sellers == 1900       # counts preserved


def test_split_quarter_adjusted_when_count_matches_ratio():
    """A real 10:1 split (share count actually multiplied ~10x) is restated onto
    the post-split basis. The raw +14.6B change is impossible (suppressed); the
    adjusted value is the real post-split buying."""
    data = {
        "numberOf13FsharesChange": 14_600_000_000,   # raw: dominated by the split
        "numberOf13Fshares": 16_200_000_000,         # post-split + 0.2B real buying
        "lastNumberOf13Fshares": 1_600_000_000,      # pre-split (~1/10)
        "newPositions": 200, "increasedPositions": 1700,
        "closedPositions": 500, "reducedPositions": 1400,
    }
    _, _, raw_net, _, _ = HoldersService._compute_quarter_flow(data)         # ratio 1
    _, _, adj_net, _, _ = HoldersService._compute_quarter_flow(data, 10.0)   # 10:1
    assert raw_net == 0.0                # raw +14.6B > shares held → suppressed
    assert abs(adj_net - 200.0) < 1.0    # split removed → real +200M buying


def test_odd_ratio_not_adjusted_when_count_unchanged():
    """A spinoff mislabeled as a 1.253 'split' (share count barely moved, NOT up
    1.25x) must NOT be adjusted — leave the raw change, don't fabricate one."""
    data = {
        "numberOf13FsharesChange": -6_000_000,       # small real change
        "numberOf13Fshares": 850_000_000,
        "lastNumberOf13Fshares": 856_000_000,        # cur/last ≈ 0.99, NOT 1.253
        "newPositions": 200, "increasedPositions": 1700,
        "closedPositions": 500, "reducedPositions": 1400,
    }
    _, _, net_raw, _, _ = HoldersService._compute_quarter_flow(data)
    _, _, net_ratio, _, _ = HoldersService._compute_quarter_flow(data, 1.253)
    assert net_raw == net_ratio          # ratio ignored — not a real split
    assert abs(net_raw - (-6.0)) < 0.01


def test_quarter_split_ratios_window_and_value():
    """A split is attributed to the quarter its date falls in (prev q-end < date
    <= q-end), as numerator/denominator. Other quarters get 1.0."""
    splits = [{"date": "2024-06-10", "numerator": 10, "denominator": 1}]
    ratios = HoldersService._quarter_split_ratios(splits, [(2024, 1), (2024, 2), (2024, 3)])
    assert ratios[(2024, 2)] == 10.0     # Jun 10 is in Q2 (Apr 1 – Jun 30)
    assert ratios[(2024, 1)] == 1.0 and ratios[(2024, 3)] == 1.0


# ── volatile refresh window ───────────────────────────────────────────


def test_recent_quarters_are_marked_volatile():
    """The most recent _REFRESH_RECENT_QUARTERS keys are always recomputed
    (never served stale from cache)."""
    pairs = HoldersService._generate_quarter_keys(8)
    volatile = pairs[-_REFRESH_RECENT_QUARTERS:]
    assert len(volatile) == _REFRESH_RECENT_QUARTERS
    assert volatile == sorted(pairs)[-_REFRESH_RECENT_QUARTERS:]
