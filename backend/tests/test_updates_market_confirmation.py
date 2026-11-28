"""Market confirmation for move cards (2026-11-27, owner: "Yes, when code confirms it").

A big mover's Insights card may name the wider market as the cause only when the CODE
confirms the move was market- or group-wide (`updates_materiality.market_confirmation`):

* INDEX — the S&P 500 (a session-stamped SPY row) moved the same way by ≥ 1.5% and the
  stock moved at most 6x the index;
* BREADTH — at least 6 OTHER live equity jumpers moved the same way, they are ≥ 80% of
  the other jumpers with a usable change, and the stock moved at most 6x the median
  same-way peer.

The figures below are the replays' session changes (FMP quotes, rounded): CRWV on Mon
2026-11-23 (+22% on a +4.7% S&P day, 17 of the 18 other jumpers up) and PLUG on Wed
2026-11-25 (−10% at the open on a −2.6% S&P morning; +10% in the afternoon on a flat S&P
with mixed jumpers). No web data is involved anywhere.
"""

import math
from collections.abc import Mapping

import pytest

from app.services.insight_move import MARKET_BASIS_BREADTH, MARKET_BASIS_INDEX
from app.services.updates_materiality import (
    MARKET_CONFIRM_BREADTH_MIN,
    MARKET_CONFIRM_BREADTH_SHARE,
    MARKET_CONFIRM_INDEX_PCT,
    MARKET_CONFIRM_MAX_MULTIPLE,
    market_confirmation,
)


def _peers(*changes):
    return {f"P{i:02d}": c for i, c in enumerate(changes)}


# 11-23: 17 of the 18 other live jumpers up, one down.
PEERS_1123 = _peers(8.1, 9.4, 10.2, 11.0, 12.5, 13.1, 14.0, 9.9, 15.2, 16.8, 18.0, 11.7,
                    12.2, 10.6, 9.1, 20.3, 13.9, -7.2)


def test_the_thresholds_are_pinned():
    assert MARKET_CONFIRM_INDEX_PCT == 1.5
    assert MARKET_CONFIRM_MAX_MULTIPLE == 6.0
    assert MARKET_CONFIRM_BREADTH_MIN == 6
    assert MARKET_CONFIRM_BREADTH_SHARE == 0.8
    assert (MARKET_BASIS_INDEX, MARKET_BASIS_BREADTH) == ("index", "breadth")


# ── the replays ──────────────────────────────────────────────────────────────

def test_crwv_2026_11_23_is_index_confirmed_and_the_index_wins_over_breadth():
    assert market_confirmation(22.0, 4.7, PEERS_1123) == MARKET_BASIS_INDEX
    # Without a usable index row the breadth alone confirms it.
    assert market_confirmation(22.0, 4.7, PEERS_1123, index_session_ok=False) == (
        MARKET_BASIS_BREADTH)
    assert market_confirmation(22.0, None, PEERS_1123) == MARKET_BASIS_BREADTH


def test_plug_2026_11_25_open_is_index_confirmed():
    # −10% on a −2.6% S&P morning: 3.8x, the slide was market-wide.
    assert market_confirmation(-10.0, -2.6, _peers(-9.5, 6.0, -11.0)) == MARKET_BASIS_INDEX


def test_plug_2026_11_25_rebound_is_not_confirmed():
    mixed = _peers(10.4, -9.8, 11.2, -12.0, 9.6, 10.9, -10.1)
    for index in (0.05, -0.04, 0.3, 0.0):
        assert market_confirmation(10.05, index, mixed) is None


# ── index boundaries ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("index,expected", [
    (1.5, MARKET_BASIS_INDEX), (-1.5, None), (1.49, None), (1.51, MARKET_BASIS_INDEX),
])
def test_the_index_must_move_at_least_one_and_a_half_percent_the_same_way(index, expected):
    assert market_confirmation(5.0, index, {}) == expected


@pytest.mark.parametrize("stock,expected", [
    (12.0, MARKET_BASIS_INDEX),    # exactly 6.0x
    (12.02, None),                 # 6.01x
    (-12.0, None),                 # the opposite way
    (2.0, MARKET_BASIS_INDEX),     # smaller than the index is still "with" it
])
def test_the_stock_may_move_at_most_six_times_the_index(stock, expected):
    assert market_confirmation(stock, 2.0, {}) == expected


def test_a_down_day_mirrors_an_up_day():
    assert market_confirmation(-9.0, -1.5, {}) == MARKET_BASIS_INDEX
    assert market_confirmation(-9.02, -1.5, {}) is None
    assert market_confirmation(-5.0, 2.0, {}) is None


@pytest.mark.parametrize("ok", [False, None, 1, "yes", "True"])
def test_only_a_strictly_true_session_stamp_lets_the_index_confirm(ok):
    assert market_confirmation(9.0, 3.0, {}, index_session_ok=ok) is None


# ── breadth ──────────────────────────────────────────────────────────────────

def test_breadth_needs_six_same_way_peers():
    assert market_confirmation(12.0, None, _peers(*([10.0] * 5))) is None
    assert market_confirmation(12.0, None, _peers(*([10.0] * 6))) == MARKET_BASIS_BREADTH


def test_breadth_needs_eighty_percent_of_the_usable_peers():
    assert market_confirmation(12.0, None, _peers(*([10.0] * 8), -9.0, -9.0)) == (
        MARKET_BASIS_BREADTH)                                   # 8 of 10 = 80%
    assert market_confirmation(12.0, None, _peers(*([10.0] * 6), -9.0, -9.0)) is None  # 75%
    assert market_confirmation(12.0, None, _peers(*([10.0] * 7), -9.0, -9.0, -9.0)) is None


def test_a_flat_peer_counts_against_the_share_but_junk_peers_do_not():
    # 8 up, 2 that round to zero → 80%; junk values are not peers at all.
    peers = _peers(*([10.0] * 8), 0.0, 0.001)
    assert market_confirmation(12.0, None, peers) == MARKET_BASIS_BREADTH
    peers = _peers(*([10.0] * 8), 0.0, 0.001, 0.002)            # 8 of 11
    assert market_confirmation(12.0, None, peers) is None
    junk = dict(_peers(*([10.0] * 6)), A=float("nan"), B=None, C="9.0", D=True,
                E=math.inf, F=[1], G={"x": 1})
    assert market_confirmation(12.0, None, junk) == MARKET_BASIS_BREADTH


def test_breadth_caps_the_stock_at_six_times_the_median_same_way_peer():
    peers = _peers(2.0, 2.0, 2.0, 3.0, 3.0, 3.0, -2.0)          # median same-way = 2.5
    assert market_confirmation(15.0, None, peers) == MARKET_BASIS_BREADTH   # 6.0x
    assert market_confirmation(15.03, None, peers) is None                  # 6.01x


def test_breadth_counts_only_the_stocks_own_direction():
    peers = _peers(*([-10.0] * 10))
    assert market_confirmation(12.0, None, peers) is None
    assert market_confirmation(-12.0, None, peers) == MARKET_BASIS_BREADTH


def test_breadth_holds_when_the_index_moved_the_other_way_or_too_little():
    assert market_confirmation(12.0, -2.0, PEERS_1123) == MARKET_BASIS_BREADTH
    assert market_confirmation(12.0, 0.4, PEERS_1123) == MARKET_BASIS_BREADTH


# ── never confirmed ──────────────────────────────────────────────────────────

def test_the_market_scope_and_coins_are_never_confirmed():
    assert market_confirmation(5.0, 4.7, PEERS_1123, is_market_scope=True) is None
    assert market_confirmation(12.0, 4.7, PEERS_1123, is_crypto=True) is None


@pytest.mark.parametrize("stock", [0.0, 0.004, -0.004])
def test_a_flat_stock_is_never_confirmed(stock):
    assert market_confirmation(stock, 4.7, PEERS_1123) is None


@pytest.mark.parametrize("stock", [None, float("nan"), math.inf, -math.inf, True, False,
                                   "9.0", "x", [9.0], {"v": 9.0}, object()])
def test_an_unusable_stock_move_is_never_confirmed(stock):
    assert market_confirmation(stock, 4.7, PEERS_1123) is None


@pytest.mark.parametrize("index", [None, float("nan"), math.inf, True, "4.7", object()])
def test_an_unusable_index_falls_back_to_breadth_and_never_raises(index):
    assert market_confirmation(12.0, index, PEERS_1123) == MARKET_BASIS_BREADTH
    assert market_confirmation(12.0, index, {}) is None


@pytest.mark.parametrize("peers", [None, [10.0] * 8, "PEERS", 7, object(), {}])
def test_unusable_peers_never_raise(peers):
    assert market_confirmation(12.0, None, peers) is None
    assert market_confirmation(9.0, 3.0, peers) == MARKET_BASIS_INDEX


class _ExplodingPeers(Mapping):
    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        raise RuntimeError("a broken mapping")

    def __len__(self):
        return 1


def test_a_mapping_that_raises_is_logged_and_unconfirmed(caplog):
    assert market_confirmation(12.0, None, _ExplodingPeers()) is None
    assert "market_confirmation raised" in caplog.text
