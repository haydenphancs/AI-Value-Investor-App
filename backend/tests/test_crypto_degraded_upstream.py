"""Crypto detail when CoinGecko PARTIALLY degrades — the case that ships wrong numbers.

A total outage is easy: the request fails and the user sees an error. The dangerous case is
a SPLIT failure, where some CoinGecko calls succeed and others do not, because the screen
then renders as healthy while carrying fabricated figures.

Three defects lived in that gap, all confirmed by adversarial review of the deployed code:

  1. `_usd(field, default=0)` floored every missing market-data field at 0, so a failed
     `/coins/{id}` shipped "Market Cap $0.00 / 24h Volume $0.00 / 24h High $0.00 /
     Circulating Supply 0 BTC / Fully Diluted Val. $0.00" as FACTS — underneath a header
     price recovered from the chart, which made them look measured rather than missing.
     The same builder's 52-week column already degraded to "—" via `_fmt(None)`.
  2. "Avg. Volume (30D)" fell back to the 24-HOUR volume when history was empty, and
     averaged whatever it had (11 days for a new listing) under a 30-day label.
  3. `_cg_52_week_band` and the altcoin BTC-benchmark were awaited UNGUARDED outside the
     main gather, so one /ohlc 429 turned a complete screen into an error screen.

The house rule: an unknown number is None, never 0.0, and a label must describe the data
actually shown.
"""

from __future__ import annotations

import pytest

from app.services.crypto_service import CryptoService, _AVG_VOLUME_DAYS

DASH = "—"


def _stats(**over):
    """Key statistics as {label: value} for a given set of inputs."""
    base = dict(
        price=79_093.0, market_cap=None, volume=None, avg_volume=None,
        day_high=None, day_low=None, year_high=126_080.0, year_low=57_779.0,
        circulating_supply=None, total_supply=None, max_supply=21_000_000,
        fdv=None, symbol="BTC",
    )
    base.update(over)
    groups = object.__new__(CryptoService)._build_key_statistics(**base)
    return {i.label: i.value for g in groups for i in g.statistics}


# ── 1. absent market data renders "—", never $0.00 ───────────────────────────

FABRICATION_PRONE = [
    "Market Cap", "24h Volume", "Volume/Mkt Cap", "24h High", "24h Low",
    "Circulating Supply", "Fully Diluted Val.", "Avg. Volume (30D)",
]


@pytest.mark.parametrize("label", FABRICATION_PRONE)
def test_absent_market_data_renders_an_em_dash_not_a_zero(label):
    """`md == {}` is what a failed /coins/{id} produces. Nothing may read as measured."""
    s = _stats()
    assert s[label] == DASH, f"{label} rendered {s[label]!r} for absent data"


def test_no_statistic_is_a_fabricated_zero_when_everything_is_unknown():
    s = _stats()
    zeros = {k: v for k, v in s.items()
             if v.strip() in {"$0.00", "$0.000000", "0.00%", "+0.00%", "0", "0 BTC"}}
    assert zeros == {}, f"fabricated zero-values: {zeros}"


def test_the_52_week_column_still_renders_when_only_market_data_is_missing():
    """The split failure: band from /ohlc succeeded, /coins/{id} did not."""
    s = _stats()
    assert s["52-Week High"] == "$126,080.00"
    assert s["52-Week Low"] == "$57,779.00"


def test_healthy_input_is_unchanged():
    """Anti-vacuity: the degrade must not have blanked the working path."""
    s = _stats(market_cap=1.58e12, volume=3.7e10, avg_volume=3.1e10,
               day_high=79_500.0, day_low=78_100.0, circulating_supply=19_900_000,
               total_supply=19_900_000, fdv=1.66e12)
    assert s["Market Cap"] == "$1.58T"
    assert s["24h Volume"] == "$37.00B"
    assert s["24h High"] == "$79,500.00"
    assert s["Circulating Supply"] == "19.90M BTC"
    assert s["Avg. Volume (30D)"] == "$31.00B"


# ── the ratio must not claim "no trading" when it is simply unknown ──────────

def test_volume_to_market_cap_is_unknown_not_zero_percent():
    assert _stats()["Volume/Mkt Cap"] == DASH
    assert _stats(volume=3.7e10, market_cap=None)["Volume/Mkt Cap"] == DASH
    assert _stats(volume=None, market_cap=1.58e12)["Volume/Mkt Cap"] == DASH


def test_volume_to_market_cap_has_no_sign_prefix():
    """It is a RATIO, not a change — "+2.34%" would read as a move."""
    v = _stats(volume=3.7e10, market_cap=1.58e12)["Volume/Mkt Cap"]
    assert v == "2.34%", v


def test_a_zero_market_cap_does_not_divide():
    assert _stats(volume=3.7e10, market_cap=0)["Volume/Mkt Cap"] == DASH


# ── supply math must not TypeError on a half-present pair ────────────────────

def test_total_supply_present_but_circulating_absent_does_not_raise():
    """`abs(total - None)` is a TypeError that would 500 the whole detail response."""
    s = _stats(total_supply=19_900_000, circulating_supply=None)
    assert s["Circulating Supply"] == DASH


def test_circulating_present_but_total_absent_does_not_raise():
    s = _stats(circulating_supply=19_900_000, total_supply=None)
    assert s["Circulating Supply"] == "19.90M BTC"


# ── 2. the 30-day label must mean 30 days ────────────────────────────────────

def test_the_avg_volume_window_constant_matches_the_label():
    assert _AVG_VOLUME_DAYS == 30, "the row is labelled (30D); the window must agree"


@pytest.mark.parametrize("value", [None])
def test_absent_avg_volume_is_a_dash_not_the_24h_volume(value):
    """The old fallback put a ONE-DAY figure under a THIRTY-DAY label."""
    s = _stats(volume=3.7e10, avg_volume=value)
    assert s["Avg. Volume (30D)"] == DASH
    assert s["Avg. Volume (30D)"] != s["24h Volume"]


# ── the EXTRACTION layer, not just the formatting layer ─────────────────────
#
# The tests above call `_build_key_statistics` directly, so they pin how a None RENDERS.
# They cannot see which extractor produced it — verified by hand: swapping the four
# `_usd_opt` reads back to `_usd` (the exact regression) left every test above green.
# `_usd_opt` is a closure inside `get_crypto_detail` and cannot be imported, so this is a
# source scan. Per .claude/rules/testing.md it is comment-stripped and bounded to the
# assignment block it means to check, and it was mutation-tested by hand.

import pathlib
import re

_SERVICE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "crypto_service.py"

# Fields whose ABSENCE must stay absent. `current_price` is deliberately NOT here: it
# keeps `_usd`'s 0 default because it has its own last-close recovery below the block.
_MUST_BE_OPTIONAL = ["high_24h", "low_24h", "total_volume", "market_cap",
                     "fully_diluted_valuation"]


def _stripped_source() -> str:
    src = _SERVICE.read_text(encoding="utf-8")
    out = []
    for line in src.splitlines():
        if line.strip().startswith("#"):
            continue
        out.append(re.sub(r"\s#.*$", "", line))
    return "\n".join(out)


@pytest.mark.parametrize("field", _MUST_BE_OPTIONAL)
def test_absent_market_fields_are_read_with_the_optional_extractor(field):
    """`_usd` floors at 0; `_usd_opt` returns None. These fields must use the latter."""
    src = _stripped_source()
    assert f'_usd_opt("{field}")' in src, (
        f'{field} must be read with _usd_opt — _usd(default=0) turns an absent CoinGecko '
        f'field into a fabricated $0.00 statistic'
    )
    assert f'_usd("{field}")' not in src, (
        f'{field} is still read with the 0-defaulting _usd'
    )


def test_current_price_deliberately_keeps_the_zero_default():
    """Anti-vacuity: not everything should be Optional.

    `price` has its own recovery path (the last CoinGecko close) and a downstream
    `if price <= 0` refusal, so it keeps `_usd`. If this ever flips, that refusal
    silently becomes a None comparison.
    """
    assert '_usd("current_price")' in _stripped_source()


def test_supply_fields_are_not_coerced_to_zero():
    """`md.get("circulating_supply", 0) or 0` was the supply-side twin of the same bug."""
    src = _stripped_source()
    assert 'md.get("circulating_supply", 0) or 0' not in src
    assert 'md.get("total_supply", 0) or 0' not in src
