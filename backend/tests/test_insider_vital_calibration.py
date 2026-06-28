"""Insider vital is ASYMMETRIC: insider BUYING (informative) is rewarded fully,
SELLING (routine comp/diversification noise at healthy companies) is only mildly
penalized and FLOORED at 3 — never the old symmetric `5+ratio*5` that scored every
net-seller ~1 and dragged 84% of real companies (NVDA/MSFT/every successful name) to
the bottom of the insider dimension.
"""

from datetime import datetime, timedelta, timezone

from app.services.agents.ticker_report_data_collector import _build_insider_sections


def _row(ttype: str, shares: float, price: float, days_ago: int = 30) -> dict:
    d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "transactionType": ttype,
        "securityName": "Common Stock",
        "transactionDate": d,
        "securitiesTransacted": shares,
        "price": price,
    }


def test_routine_net_selling_is_not_scored_as_distress():
    # Heavy net selling → mild, floored at 3.0 (was 1.0 under the symmetric formula).
    _, vital = _build_insider_sections([_row("S-Sale", 100_000, 100.0)])
    assert vital["score"]["value"] == 3.0
    assert vital["sentiment"] == "negative"


def test_insider_buying_rewarded_fully():
    _, vital = _build_insider_sections([_row("P-Purchase", 100_000, 100.0)])
    assert vital["score"]["value"] == 10.0
    assert vital["sentiment"] == "positive"


def test_balanced_is_neutral_5():
    _, vital = _build_insider_sections(
        [_row("P-Purchase", 1_000, 100.0), _row("S-Sale", 1_000, 100.0)]
    )
    assert vital["score"]["value"] == 5.0


def test_mild_net_selling_above_floor():
    # 3:1 sell:buy by value → ratio = -0.5 → 5 + (-0.5)*2 = 4.0 (above the 3 floor).
    _, vital = _build_insider_sections(
        [_row("P-Purchase", 1_000, 100.0), _row("S-Sale", 3_000, 100.0)]
    )
    assert vital["score"]["value"] == 4.0


def test_no_informative_trades_unmeasured():
    _, vital = _build_insider_sections([])
    assert vital["score"]["value"] is None


def test_non_finite_price_is_unmeasured_not_max_bullish():
    # A corrupt inf price must NOT NaN-poison the score clamp into a FAKE bullish 10.0
    # (the order-dependent max/min NaN-clamp). It degrades to unmeasured (None).
    _, sell = _build_insider_sections([_row("S-Sale", 1_000, float("inf"))])
    assert sell["score"]["value"] is None
    _, buy = _build_insider_sections([_row("P-Purchase", 1_000, float("inf"))])
    assert buy["score"]["value"] is None


def test_nan_shares_does_not_crash():
    # NaN shares must not crash int(nan) inside _format_shares_short — degrades to None.
    _, vital = _build_insider_sections([_row("S-Sale", float("nan"), 100.0)])
    assert vital["score"]["value"] is None
