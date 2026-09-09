"""Math and outlier guards for `app/services/coingecko_adapter`.

Crypto's entire price path now flows through this module, and its two conversions are
invisible when wrong: a UTC timestamp renders a plausible chart shifted by four hours, and
a zipped volume array produces plausible indicators computed against the wrong bars.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.services.coingecko_adapter import (
    epoch_ms_to_et,
    market_chart_to_rows,
    markets_rows_by_id,
    ohlc_to_rows,
)

# 2026-09-09 02:07:30 UTC == 2026-09-08 22:07:30 America/New_York (EDT, UTC-4)
_UTC_MS = int(dt.datetime(2026, 9, 9, 2, 7, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)


# ── Timestamps are ET wall-clock ─────────────────────────────────────────────

def test_intraday_timestamps_are_ET_not_UTC():
    """`chart_helper._bar_minute_of_day` and iOS's `inputDateTimeFormatter` both read bar
    strings as America/New_York. Emitting UTC shifts every crypto bar 4-5h — the crosshair
    reads 02:07 for a 22:07 print, and the 1D window starts the previous evening."""
    assert epoch_ms_to_et(_UTC_MS, intraday=True) == "2026-09-08 22:07:30"
    assert epoch_ms_to_et(_UTC_MS, intraday=False) == "2026-09-08"
    # And it must NOT be the UTC rendering.
    assert epoch_ms_to_et(_UTC_MS, intraday=True) != "2026-09-09 02:07:30"


def test_the_ET_offset_follows_daylight_saving():
    """January is EST (UTC-5), September EDT (UTC-4). A fixed offset would be wrong for
    half the year — and the wrong half is the one nobody tests in summer."""
    jan = int(dt.datetime(2026, 1, 15, 2, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert epoch_ms_to_et(jan, intraday=True) == "2026-01-14 21:30:00"   # −5
    assert epoch_ms_to_et(_UTC_MS, intraday=True) == "2026-09-08 22:07:30"  # −4


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan"), float("inf"), [], {}])
def test_an_unusable_timestamp_yields_none_not_epoch_zero(bad):
    assert epoch_ms_to_et(bad, intraday=False) is None


# ── prices × volumes join ────────────────────────────────────────────────────

def _ms(day: int) -> int:
    return int(dt.datetime(2026, 6, day, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_volume_is_joined_on_timestamp_not_zipped():
    """The arrays are independent and their lengths diverge. A positional zip attaches
    day 3's volume to day 2's price — silent, and it feeds OBV and the 30-day average."""
    payload = {
        "prices":        [[_ms(1), 10.0], [_ms(2), 20.0], [_ms(3), 30.0]],
        # volumes is SHORTER and starts at a different point
        "total_volumes": [[_ms(2), 222.0], [_ms(3), 333.0]],
    }
    rows = market_chart_to_rows(payload, intraday=False)
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-06-01"]["volume"] is None, "a zip would have put 222 here"
    assert by_date["2026-06-02"]["volume"] == 222.0
    assert by_date["2026-06-03"]["volume"] == 333.0


def test_rows_are_oldest_first_regardless_of_input_order():
    payload = {"prices": [[_ms(3), 30.0], [_ms(1), 10.0], [_ms(2), 20.0]], "total_volumes": []}
    rows = market_chart_to_rows(payload, intraday=False)
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_no_OHLC_is_fabricated_from_the_close():
    """`market_chart` has no high/low. Synthesising them from the close asserts an
    intraday range that was never observed — and downstream, `_compute_fibonacci` would
    render seven identical levels and MFI a hardcoded 'neutral 50'."""
    rows = market_chart_to_rows({"prices": [[_ms(1), 10.0]], "total_volumes": []},
                                intraday=False)
    assert set(rows[0]) == {"date", "close", "volume"}
    for absent in ("open", "high", "low"):
        assert absent not in rows[0]


# ── Outliers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    None, {}, [], "nonsense", 42,
    {"prices": None}, {"prices": "x"}, {"prices": []},
])
def test_an_unusable_payload_yields_an_empty_series(payload):
    assert market_chart_to_rows(payload, intraday=False) == []


@pytest.mark.parametrize("bad_close", [None, 0, -1.0, float("nan"), float("inf"), "x"])
def test_unusable_closes_are_dropped_not_zeroed(bad_close):
    payload = {"prices": [[_ms(1), bad_close], [_ms(2), 20.0]], "total_volumes": []}
    rows = market_chart_to_rows(payload, intraday=False)
    assert len(rows) == 1 and rows[0]["close"] == 20.0


def test_a_sub_penny_price_survives_the_adapter():
    """SHIB trades at 5.42e-06. Anything that rounds to 2dp here reports it as $0.00."""
    payload = {"prices": [[_ms(1), 5.42e-06]], "total_volumes": []}
    assert market_chart_to_rows(payload, intraday=False)[0]["close"] == pytest.approx(5.42e-06)


def test_a_malformed_pair_does_not_break_the_series():
    payload = {"prices": [[_ms(1), 10.0], "junk", [], [_ms(2)], None, [_ms(3), 30.0]],
               "total_volumes": [["nope"], [_ms(3), 5.0]]}
    rows = market_chart_to_rows(payload, intraday=False)
    assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-03"]


def test_duplicate_dates_collapse_when_bucketing_intraday_into_days():
    """`days=1` is 5-minute data; asking for daily rows from it must not emit 288 rows
    all stamped with the same date."""
    same_day = [[int(dt.datetime(2026, 6, 1, h, tzinfo=dt.timezone.utc).timestamp() * 1000), 10.0 + h]
                for h in range(8, 20)]
    rows = market_chart_to_rows({"prices": same_day, "total_volumes": []}, intraday=False)
    assert len(rows) == 1


# ── /ohlc, for the 52-week band ──────────────────────────────────────────────

def test_ohlc_rows_carry_true_high_low():
    payload = [[_ms(1), 10.0, 15.0, 9.0, 12.0], [_ms(2), 12.0, 20.0, 11.0, 18.0]]
    rows = ohlc_to_rows(payload)
    assert max(r["high"] for r in rows) == 20.0
    assert min(r["low"] for r in rows) == 9.0
    # ...and that band is WIDER than the closes, which is the whole reason to make the call.
    closes = [r["close"] for r in rows]
    assert max(r["high"] for r in rows) > max(closes)
    assert min(r["low"] for r in rows) < min(closes)


@pytest.mark.parametrize("payload", [None, {}, "x", [None], [[1, 2]], [[]]])
def test_unusable_ohlc_yields_empty(payload):
    assert ohlc_to_rows(payload) == []


# ── /coins/markets keying ────────────────────────────────────────────────────

def test_markets_are_keyed_by_id_so_MATIC_and_POL_cannot_collide():
    """Both symbols resolve to `polygon-ecosystem-token`. CoinGecko answers with one
    canonical symbol, so keying by `row["symbol"]` drops the other — and MATIC appears in
    six `_RELATED_CRYPTOS` sets, i.e. six screens with a permanently missing row."""
    rows = [
        {"id": "polygon-ecosystem-token", "symbol": "pol", "current_price": 0.5},
        {"id": "ethereum", "symbol": "eth", "current_price": 2500.0},
    ]
    by_id = markets_rows_by_id(rows)
    assert set(by_id) == {"polygon-ecosystem-token", "ethereum"}
    # The caller can now serve BOTH requested symbols from the one row.
    for requested_symbol, coin_id in (("MATIC", "polygon-ecosystem-token"),
                                      ("POL", "polygon-ecosystem-token")):
        assert by_id[coin_id]["current_price"] == 0.5


@pytest.mark.parametrize("rows", [None, {}, "x", [None, 1, "a"], [{"symbol": "eth"}]])
def test_markets_survive_a_malformed_response(rows):
    assert markets_rows_by_id(rows) == {}
