"""13F diffing + congressional ingestion outliers in `scripts/hydrate_whales.py`.

A 13F reports POSITIONS, not transactions, and the reported value moves with the share
price. The hydrator used to diff dollar value (`curr_val - prev_val`), so it booked pure
price appreciation as a trade — while `whale_service` and `holders_service` both already
used `calc_13f_trade_dollars`, which strips exactly that out. Two code paths, two
different answers for the same whale.

These tests pin the share-based diff and the congressional guards that came with it.
"""

import pytest

import scripts.hydrate_whales as hw
from scripts.hydrate_whales import WhaleHydrator, _generate_trade_summary


def _h():
    return WhaleHydrator.__new__(WhaleHydrator)


def _hold(sym, value, shares, name=None):
    return {
        "symbol": sym,
        "securityName": name or f"{sym} Inc",
        "value": value,
        "sharesNumber": shares,
    }


# ── The headline bug: a price move is not a trade ────────────────────────────


def test_pure_price_appreciation_is_not_a_trade():
    """1,000,000 NVDA held across the quarter; the stock went from $100 to $180.

    Value-diffing reported this as BOUGHT / Increased $80,000,000.
    """
    prev = [_hold("NVDA", 100_000_000, 1_000_000)]
    curr = [_hold("NVDA", 180_000_000, 1_000_000)]
    assert _h()._diff_quarters(curr, prev, "2026-06-30", 180_000_000) is None


def test_pure_price_decline_is_not_a_trade():
    prev = [_hold("NVDA", 180_000_000, 1_000_000)]
    curr = [_hold("NVDA", 100_000_000, 1_000_000)]
    assert _h()._diff_quarters(curr, prev, "2026-06-30", 100_000_000) is None


def test_selling_into_a_rally_reads_as_a_SALE_not_a_purchase():
    """Shares DOWN, value UP. Value-diffing called this a purchase — the single most
    misleading output the old code could produce."""
    prev = [_hold("NVDA", 100_000_000, 1_000_000)]     # $100/sh
    curr = [_hold("NVDA", 150_000_000, 833_333)]       # $180/sh, ~167k shares sold
    group = _h()._diff_quarters(curr, prev, "2026-06-30", 150_000_000)
    assert group is not None
    trade = group["trades"][0]
    assert trade["action"] == "SOLD"
    assert trade["trade_type"] == "Decreased"


def test_a_real_purchase_is_priced_off_the_shares_bought():
    prev = [_hold("NVDA", 100_000_000, 1_000_000)]     # $100/sh
    curr = [_hold("NVDA", 270_000_000, 1_500_000)]     # $180/sh, 500k bought
    group = _h()._diff_quarters(curr, prev, "2026-06-30", 270_000_000)
    trade = group["trades"][0]
    assert trade["action"] == "BOUGHT"
    assert trade["amount"] == pytest.approx(90_000_000, rel=0.001)  # 500k x $180


def test_a_split_is_restated_away_when_the_ratio_is_known():
    """10:1 split: shares 10x, value unchanged. Without restatement this is a ~9x
    'purchase' of the position."""
    prev = [_hold("NVDA", 100_000_000, 1_000_000)]
    curr = [_hold("NVDA", 100_000_000, 10_000_000)]
    assert _h()._diff_quarters(
        curr, prev, "2026-06-30", 100_000_000, {"NVDA": 10.0}
    ) is None


def test_new_and_closed_positions_still_report_their_full_value():
    """One-sided positions have no implied price on the other side; the position's own
    value IS the trade."""
    prev = [_hold("NVDA", 100_000_000, 1_000_000)]
    curr = [_hold("AAPL", 50_000_000, 200_000)]
    group = _h()._diff_quarters(curr, prev, "2026-06-30", 50_000_000)
    by_ticker = {t["ticker"]: t for t in group["trades"]}
    assert by_ticker["AAPL"]["trade_type"] == "New"
    assert by_ticker["AAPL"]["action"] == "BOUGHT"
    assert by_ticker["NVDA"]["trade_type"] == "Closed"
    assert by_ticker["NVDA"]["action"] == "SOLD"


# ── Degenerate inputs ────────────────────────────────────────────────────────


def test_empty_and_unusable_inputs_degrade_to_none():
    h = _h()
    assert h._diff_quarters([], [], "2026-06-30", 0) is None
    assert h._diff_quarters([], [_hold("A", 1, 1)], "2026-06-30", 0) is None
    # every row unusable (blank / placeholder symbols)
    assert h._diff_quarters(
        [{"symbol": "--", "value": 1, "sharesNumber": 1}], [], "2026-06-30", 0
    ) is None


def test_zero_share_rows_do_not_divide_by_zero():
    prev = [_hold("A", 0, 0)]
    curr = [_hold("A", 0, 0)]
    assert _h()._diff_quarters(curr, prev, "2026-06-30", 0) is None


def test_a_below_noise_move_is_ignored():
    prev = [_hold("A", 1_000_000, 10_000)]           # $100/sh
    curr = [_hold("A", 1_000_100, 10_001)]           # one extra share
    assert _h()._diff_quarters(curr, prev, "2026-06-30", 1_000_100) is None


# ── Trade summary: every branch reachable, and grammatical ───────────────────


def _n(n):
    return [{"x": 1}] * n


@pytest.mark.parametrize(
    "buys,sells,expected",
    [
        (1, 0, "Pure buying activity with 1 position"),   # was "Heavy accumulation with 1 buys"
        (0, 1, "Pure selling activity with 1 position"),
        (3, 0, "Pure buying activity with 3 positions"),
        (0, 3, "Pure selling activity with 3 positions"),
        (4, 1, "Heavy accumulation with 4 buys"),
        (1, 4, "Significant reduction with 4 sells"),
        (2, 2, "Portfolio rebalancing"),
        (0, 0, "Portfolio rebalancing"),
    ],
)
def test_trade_summary_branches(buys, sells, expected):
    assert _generate_trade_summary(_n(buys), _n(sells), "BOUGHT") == expected


# ── Risk profile: no dead branch, no missing-data bias ───────────────────────


def test_very_high_turnover_reaches_the_most_aggressive_branch():
    """`turnover > 0.75` sat AFTER `turnover > 0.50`, so it was unreachable and the
    most aggressive filers scored the same as merely active ones."""
    h = _h()
    holdings = [{"ticker": f"T{i}", "allocation": 10} for i in range(10)]
    # Two sectors (+10 concentration) so the two turnover scores land either side of
    # the aggressive/very_aggressive band rather than both inside "aggressive".
    sectors = [{"name": "Technology", "allocation": 60},
               {"name": "Healthcare", "allocation": 40}]
    hot = h._compute_risk_profile(holdings, sectors, None, {"turnover": 0.9})
    warm = h._compute_risk_profile(holdings, sectors, None, {"turnover": 0.6})
    assert hot == "very_aggressive", hot
    assert warm == "aggressive", warm


def test_missing_holding_period_does_not_bias_toward_aggressive():
    """`perf.get("averageHoldingPeriod", 0)` turned ABSENT into 0 -> `< 3` -> "+10
    short-term trader", so a whale with no data was pushed toward 'aggressive'."""
    h = _h()
    holdings = [{"ticker": f"T{i}", "allocation": 10} for i in range(10)]
    absent = h._compute_risk_profile(holdings, [], None, {"turnover": 0.3})
    explicit_zero = h._compute_risk_profile(
        holdings, [], None, {"averageHoldingPeriod": 0.0, "turnover": 0.3}
    )
    # An explicit 0 IS evidence of a very short holding period; an absent key is not.
    assert absent != explicit_zero


def test_null_holding_period_does_not_raise():
    """`None > 20` raises TypeError."""
    h = _h()
    holdings = [{"ticker": "T", "allocation": 100}]
    assert h._compute_risk_profile(
        holdings, [], None, {"averageHoldingPeriod": None}
    )


# ── Congressional: never fabricate a dollar figure or a direction ────────────


def test_unparseable_bucket_does_not_become_eight_thousand_dollars():
    """`parse_congress_amount_dollars(...) or 8_000` replaced a real 0.0 with a
    fabricated $8,000 that was then persisted and summed into the group net."""
    h = _h()
    holdings, groups = h._aggregate_congressional(
        [{
            "symbol": "MSFT", "type": "purchase", "amount": "$50,000,000+",
            "transactionDate": "2026-07-01", "disclosureDate": "2026-08-01",
            "assetDescription": "Microsoft",
        }],
        "2026-08-18",
    )
    trades = [t for g in groups for t in g["trades"]]
    assert trades, "the trade must still be recorded"
    assert trades[0]["amount"] == 0.0
    # ...and the honest bucket string is kept for display.
    assert trades[0]["amount_range"] == "$50,000,000+"


def test_unrecognised_type_is_skipped_not_booked_as_a_purchase():
    h = _h()
    holdings, groups = h._aggregate_congressional(
        [{
            "symbol": "MSFT", "type": "receive", "amount": "$1,001 - $15,000",
            "transactionDate": "2026-07-01", "disclosureDate": "2026-08-01",
        }],
        "2026-08-18",
    )
    trades = [t for g in groups for t in g["trades"]]
    assert trades == [], "an unknown direction must not be booked as BOUGHT"
