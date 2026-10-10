"""The Price card's Earnings Yield is 1 / the card's own displayed P/E (owner decision 2026-10-09,
payload v8).

THE BUG: the card relayed FMP's key-metrics `earningsYieldTTM` — total TTM net income ÷ CURRENT
market cap — beside a P/E built on per-share earnings to common shareholders. Where preferred
dividends or a moving share count split the two, the card printed a pair that does not invert
(read-only probe 2026-10-09: C P/E 13.75 beside 8.09%, and 1/13.75 = 7.27%; BA 1.62% vs 1.40%;
CRM 5.14% vs 4.80%; GS 7.81% vs 7.33%), while Ask Cay AI — which derives every yield from the
P/E beside it (`app.utils.earnings_yield`) — said 7.27%.

Pins: the value is `earnings_yield_text` of the DISPLAYED P/E (byte-identical to chat, so chat
never rebases a fresh card); no positive P/E → "N/A", never a yield from another source; an absurd
P/E prints "below 0.01%", never "0.00%"; the peer comparison is 1 / the P/E median at the P/E
cell's level (the stored net income ÷ market cap median is no longer read), so the two rows
mirror each other; the row stays unscored; FMP's yield is only logged when > 5% away. Pure: no
FMP, no Supabase.
"""

from __future__ import annotations

import logging
import math

import pytest

import app.services.valuation_snapshot_service as vss
from app.utils.earnings_yield import earnings_yield_text, with_derived_earnings_yield


def _card(fr=None, km=None, bench=None, levels=None, inc=None, ticker="XYZ", withheld=False):
    return vss.build_price_snapshot(
        fr=fr or {}, km=km or {}, cf={}, inc=inc or {}, bs={}, profile={},
        bench=bench if bench is not None else {}, bench_levels=levels, ticker=ticker,
        peers_withheld=withheld,
    )


def _m(snap, key):
    return next(m for m in snap.metrics if m.metric_key == key)


def _ratio(name):
    """The "N.NNx" in a row name, or None."""
    head = name.split("(", 1)[-1]
    return float(head.split("x", 1)[0]) if "x " in head else None


# ── the value ────────────────────────────────────────────────────────────────────

def test_a_card_whose_upstream_yield_disagrees_shows_the_inverse_of_its_pe():
    """C, recorded 2026-10-09: P/E 13.75, FMP yield 8.09% (net income ÷ market cap)."""
    snap = _card(fr={"priceToEarningsRatioTTM": 13.75}, km={"earningsYieldTTM": 0.0809},
                 bench={"pe_ratio": 14.43, "earnings_yield": 0.076},
                 levels={"pe_ratio": "industry", "earnings_yield": "industry"}, ticker="C")
    pe, ey = _m(snap, "pe"), _m(snap, "earnings_yield")
    assert pe.value == "13.75" and ey.value == "7.27%"
    assert "8.09" not in ey.value and "7.60" not in ey.name, "neither FMP figure is relayed"
    assert ey.name == "Earnings Yield (1.05x sector avg 6.93%)" and ey.peer_level == "industry"
    assert ey.score is None


def test_a_self_consistent_card_reads_the_same_as_before():
    """MSFT, recorded 2026-10-09: ratios P/E 29.726 and FMP yield 3.3647% already agree."""
    snap = _card(fr={"priceToEarningsRatioTTM": 29.726}, km={"earningsYieldTTM": 0.033647})
    assert (_m(snap, "pe").value, _m(snap, "earnings_yield").value) == ("29.73", "3.36%")


_MCAP = {"marketCap": 1e11}         # with positive net income, the old net income ÷ market cap rung


@pytest.mark.parametrize("fr,km,inc", [
    # A loss-maker with a positive upstream yield AND positive fiscal-year net income: the old
    # fallbacks put a positive yield beside "Neg.".
    ({"priceToEarningsRatioTTM": -18.75}, {"earningsYieldTTM": 0.02, **_MCAP}, {"netIncome": 5e9}),
    # The same with no upstream yield: the old chain fell to net income ÷ market cap (5.00%).
    ({"priceToEarningsRatioTTM": -18.75}, dict(_MCAP), {"netIncome": 5e9}),
    # No P/E at all (ratios-ttm empty), an upstream yield present / only net income ÷ market cap.
    ({}, {"earningsYieldTTM": 0.031, **_MCAP}, {"netIncome": 5e9}),
    ({}, dict(_MCAP), {"netIncome": 5e9}),
    # Zero (FMP's "absent"), NaN and infinity.
    ({"priceToEarningsRatioTTM": 0}, {"earningsYieldTTM": 0.031, **_MCAP}, {"netIncome": 5e9}),
    ({"priceToEarningsRatioTTM": "NaN"}, {"earningsYieldTTM": 0.031, **_MCAP}, {"netIncome": 5e9}),
    ({"priceToEarningsRatioTTM": float("inf")}, dict(_MCAP), {"netIncome": 5e9}),
    # A P/E that prints "0.00": no yield is the inverse of a printed zero.
    ({"priceToEarningsRatioTTM": 0.001}, dict(_MCAP), {"netIncome": 5e9}),
])
def test_no_positive_pe_means_no_yield_from_any_other_source(fr, km, inc):
    snap = _card(fr=fr, km=km, inc=inc, bench={"pe_ratio": 26.3}, levels={"pe_ratio": "sector"})
    ey = _m(snap, "earnings_yield")
    assert ey.value == "N/A"
    # The median still prints (the card's own "no value" form) — never a "N.NNx".
    assert ey.name == "Earnings Yield (sector avg 3.80%)"


def test_a_zero_upstream_yield_does_not_hide_a_real_pe():
    snap = _card(fr={"priceToEarningsRatioTTM": 25.0}, km={"earningsYieldTTM": 0.0})
    assert _m(snap, "earnings_yield").value == "4.00%"


@pytest.mark.parametrize("pe", [1e6, 1e12])
def test_an_absurd_pe_never_prints_a_zero_yield(pe):
    snap = _card(fr={"priceToEarningsRatioTTM": pe}, bench={"pe_ratio": 26.3},
                 levels={"pe_ratio": "industry"})
    ey = _m(snap, "earnings_yield")
    assert ey.value == "below 0.01%" and "0.00%" not in ey.value
    assert _ratio(ey.name) is None, "no '0.00x' multiple beside an unprintable yield"


def test_the_annual_fallback_card_inverts_its_own_annual_pe():
    """`stock_overview_service._build_valuation_snapshot` hands the same builder ANNUAL ratios
    (bare key names) and no medians."""
    snap = _card(fr={"priceToEarningsRatio": 22.0}, km={"earningsYield": 0.06})
    assert (_m(snap, "pe").value, _m(snap, "earnings_yield").value) == ("22.00", "4.55%")
    assert _m(snap, "earnings_yield").name == "Earnings Yield"


@pytest.mark.parametrize("pe", [0.5, 4.96, 7.32, 13.75, 19.995, 29.726, 33.335, 71.3, 154.34,
                                324.32, 999.999, 12345.678])
@pytest.mark.parametrize("median", [None, 9.73, 14.43, 26.3, 45.57])
def test_chat_never_rebases_a_fresh_card(pe, median):
    """The card's text IS what chat derives from the displayed P/E — so the chat backstop is a
    no-op on every v8 card (value AND name, byte for byte)."""
    bench = {"pe_ratio": median} if median else {}
    snap = _card(fr={"priceToEarningsRatioTTM": pe}, km={"earningsYieldTTM": 0.05},
                 bench=bench, levels={"pe_ratio": "industry"})
    ey = _m(snap, "earnings_yield")
    assert ey.value == earnings_yield_text(_m(snap, "pe").value)
    (derived,) = [m for m in with_derived_earnings_yield(snap.metrics)
                  if getattr(m, "metric_key", None) == "earnings_yield"]
    assert (derived.name, derived.value) == (ey.name, ey.value)


# ── the peer comparison ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("pe,median", [(13.75, 14.43), (29.726, 26.3), (8.0, 25.0), (60.0, 20.0)])
def test_the_yield_row_mirrors_the_pe_row(pe, median):
    snap = _card(fr={"priceToEarningsRatioTTM": pe}, bench={"pe_ratio": median},
                 levels={"pe_ratio": "industry"})
    pe_row, ey_row = _m(snap, "pe"), _m(snap, "earnings_yield")
    assert ey_row.peer_level == pe_row.peer_level == "industry"
    assert math.isclose(_ratio(ey_row.name), 1.0 / _ratio(pe_row.name), rel_tol=0.02)


def test_the_stored_yield_median_is_no_longer_read():
    """A stored earnings-yield median with no P/E median prints nothing: the row compares only
    with 1 / the P/E median."""
    snap = _card(fr={"priceToEarningsRatioTTM": 25.0}, bench={"earnings_yield": 0.05},
                 levels={"earnings_yield": "industry"})
    ey = _m(snap, "earnings_yield")
    assert (ey.name, ey.value, ey.peer_level) == ("Earnings Yield", "4.00%", None)


def test_a_withheld_peer_group_prints_a_bare_yield():
    snap = _card(fr={"priceToEarningsRatioTTM": 30.0}, withheld=True)
    ey = _m(snap, "earnings_yield")
    assert (ey.name, ey.value, ey.peer_level, ey.score) == ("Earnings Yield", "3.33%", None, None)


@pytest.mark.parametrize("pe,median", [(13.75, 14.43), (8.0, 25.0), (60.0, 20.0), (-5.0, 20.0)])
def test_the_yield_stays_out_of_the_rating(pe, median):
    """The composite is exactly the five multiples' documented weights (an unscored one votes a
    neutral 3) — a yield row that ever carried weight would break the equality."""
    snap = _card(fr={"priceToEarningsRatioTTM": pe, "priceToBookRatioTTM": 1.0,
                     "priceToSalesRatioTTM": 3.0},
                 bench={"pe_ratio": median, "pb_ratio": 1.2, "ps_ratio": 2.0})
    weights = {"pe": 0.25, "pb": 0.15, "ps": 0.15, "pfcf": 0.20, "ev_ebitda": 0.25}
    expected = sum(w * (3 if _m(snap, k).score is None else _m(snap, k).score)
                   for k, w in weights.items())
    assert snap.weighted_score == round(expected, 3)
    assert snap.rating == max(1, min(5, round(expected)))
    assert _m(snap, "earnings_yield").score is None


# ── the log and the cache version ───────────────────────────────────────────────

def test_a_large_upstream_gap_is_logged_at_info_and_a_small_one_is_not(caplog):
    with caplog.at_level(logging.INFO, logger=vss.logger.name):
        _card(fr={"priceToEarningsRatioTTM": 13.75}, km={"earningsYieldTTM": 0.0809}, ticker="C")
        _card(fr={"priceToEarningsRatioTTM": 29.726}, km={"earningsYieldTTM": 0.033647},
              ticker="MSFT")
    gaps = [r for r in caplog.records if "[earnings-yield-source-gap]" in r.getMessage()]
    assert len(gaps) == 1 and gaps[0].levelno == logging.INFO
    assert "C:" in gaps[0].getMessage() and "0.0809" in gaps[0].getMessage()


def test_the_payload_version_rebuilds_pre_change_rows():
    assert vss._SNAPSHOT_PAYLOAD_VERSION == 8


@pytest.mark.parametrize("pe", [20000.0, 15000.0, 9999.99])
def test_a_multiple_that_would_print_zero_is_left_out(pe):
    """A printable yield (0.01%) against a median of 3.80% would read "0.00x": the card prints
    only the median, and chat derives the very same row."""
    snap = _card(fr={"priceToEarningsRatioTTM": pe}, bench={"pe_ratio": 26.3},
                 levels={"pe_ratio": "industry"})
    ey = _m(snap, "earnings_yield")
    assert ey.value.endswith("%") and ey.value[:1].isdigit()
    assert ey.name == "Earnings Yield (sector avg 3.80%)" and "0.00x" not in ey.name
    (derived,) = [m for m in with_derived_earnings_yield(snap.metrics)
                  if getattr(m, "metric_key", None) == "earnings_yield"]
    assert (derived.name, derived.value) == (ey.name, ey.value)
