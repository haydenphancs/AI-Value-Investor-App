"""When upstream gives us nothing, refuse — never ship or cache a fabricated reading.

Three confirmed defects, all the same shape: a degraded path produced a value that is
indistinguishable from a real measurement, and in two cases persisted it.

  1. `index_service._derive_from_history` — `if d.get("volume")` is not a guard, because
     NaN is TRUTHY. One poisoned row made `int(sum(vols)/len(vols))` raise ValueError
     (Infinity → OverflowError, a string → TypeError). That escaped into
     `_build_index_detail`'s gather, which has NO `return_exceptions=True`, so the whole
     index screen 502'd — and STICKILY, because `_get_history` caches the raw rows for 12h
     BEFORE the derive runs, so every retry rebuilt from the same poisoned list.

  2. `crypto_service.get_crypto_detail` — the last-close recovery is guarded by
     `if not price and historical`, and since history moved to CoinGecko both legs share
     one provider and fail together. So `historical` was `[]` exactly when the recovery
     was needed, and the screen shipped `current_price=0.0 / +0.00%` as fact.

  3. `sentiment_service` — every arm degrading to the neutral sentinel 50 yields a
     confident "Neutral", which was then CACHED for 15 minutes. A cached failure that is
     byte-identical to a real empty answer cannot be fixed by any TTL; the writer must
     refuse.
"""

from __future__ import annotations

import inspect
import math
import re

import pytest

from app.services.index_service import IndexService

NAN, INF = float("nan"), float("inf")


def _rows(n=29, volume=1e6):
    return [{"date": f"2026-08-{i:02d}", "close": 100.0 + i, "volume": volume}
            for i in range(1, n)]


# ── 1. index derived stats survive a poisoned volume ─────────────────────────

@pytest.mark.parametrize("bad", [NAN, INF, -INF, "123", None, "", {}, []])
def test_a_poisoned_volume_never_raises_out_of_the_derive(bad):
    """NaN is truthy, so the old `if d.get("volume")` filter KEPT it."""
    rows = _rows() + [{"date": "2026-08-29", "close": 128.0, "volume": bad}]
    out = IndexService._derive_from_history(rows)
    assert isinstance(out["avg_volume_30d"], int)


@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_a_poisoned_volume_is_excluded_from_the_average(bad):
    """It must be DROPPED, not coerced to zero — a zero would drag the mean down."""
    clean = IndexService._derive_from_history(_rows())["avg_volume_30d"]
    poisoned = IndexService._derive_from_history(
        _rows() + [{"date": "2026-08-29", "close": 128.0, "volume": bad}]
    )["avg_volume_30d"]
    assert poisoned == clean, f"the bad row moved the mean: {clean} -> {poisoned}"


def test_every_volume_bad_yields_zero_not_a_crash():
    out = IndexService._derive_from_history(
        [{"date": "2026-08-01", "close": 1.0, "volume": NAN}]
    )
    assert out["avg_volume_30d"] == 0


@pytest.mark.parametrize("rows", [[], None])
def test_empty_history_is_handled(rows):
    out = IndexService._derive_from_history(rows or [])
    assert out["avg_volume_30d"] == 0


def test_a_non_dict_row_does_not_crash_the_derive():
    out = IndexService._derive_from_history(_rows() + [None, "x", 5])
    assert isinstance(out["avg_volume_30d"], int)


def test_the_derive_is_contained_so_it_cannot_kill_the_screen():
    """`_build_index_detail` gathers this leg WITHOUT return_exceptions=True.

    So anything escaping `_get_derived` takes the entire index screen down, and does so
    on every request until the 12h history cache expires. The containment must stay.
    """
    src = inspect.getsource(IndexService._get_derived)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    assert "try:" in stripped and "except Exception" in stripped, (
        "_get_derived must contain a derive failure — the caller's gather has no "
        "return_exceptions=True, so a raise here 502s the whole screen"
    )
    assert "return {}" in stripped, "the degrade must be an empty bundle, not a re-raise"


def test_the_gather_assumption_this_containment_rests_on_still_holds():
    """If the caller ever gains return_exceptions=True, revisit the containment above."""
    from app.services import index_service

    src = inspect.getsource(index_service.IndexService._build_index_detail)
    gather_at = src.find("asyncio.gather(")
    assert gather_at != -1
    window = src[gather_at:gather_at + 500]
    assert "return_exceptions=True" not in window, (
        "the detail gather now tolerates exceptions — the _get_derived containment and "
        "this test's premise both need rereading"
    )


# ── 2. crypto refuses a zero price rather than shipping it ───────────────────

def test_crypto_detail_refuses_rather_than_shipping_a_zero_price():
    """The recovery is dead when BOTH legs fail; the refusal is what stops the $0.00."""
    from app.services import crypto_service

    src = inspect.getsource(crypto_service.CryptoService.get_crypto_detail)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    assert "CoinGeckoUnavailableException" in stripped, (
        "a build with no usable price must raise a TYPED, retryable error — not ship 0.0"
    )
    i = stripped.find("if not price or price <= 0:")
    assert i != -1, "the zero-price refusal is missing"
    # It must come BEFORE the statistics are built, or the zero is already on the wire.
    assert i < stripped.find("_usd_opt(\"high_24h\")"), (
        "the refusal must precede the key-statistics build"
    )


def test_the_typed_error_maps_to_a_retryable_code():
    from app.api.error_response import ErrorCode, classify_exception
    from app.integrations.coingecko import CoinGeckoUnavailableException

    code, status = classify_exception(CoinGeckoUnavailableException("down"))
    assert code == ErrorCode.COINGECKO_UNAVAILABLE
    assert status == 502, "must be an upstream error the client can retry, not a 500"


# ── 3. sentiment must not CACHE a reading nothing measured ───────────────────

def test_sentiment_refuses_to_cache_when_no_arm_produced_a_signal():
    """A cached failure byte-identical to a real answer cannot be fixed by a TTL."""
    from app.services import sentiment_service

    src = inspect.getsource(sentiment_service.SentimentService.get_sentiment)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    cache_at = stripped.find("_cache_set(f\"sentiment:{ticker}\"")
    assert cache_at != -1, "guard is stale — the cache write moved"
    before = stripped[:cache_at]
    assert "has_price_data" in before, (
        "availability must be judged from the INPUTS: both price helpers return the "
        "neutral sentinel 50 for empty input, so the score cannot reveal a failure"
    )
    assert "return response" in before, (
        "the total-failure path must return BEFORE the cache write"
    )


def test_price_availability_is_derived_from_what_was_measured():
    """This used to require `has_price_data = bool(price_data) or bool(hist_prices)`, on the
    grounds that "a `price_score != 50` test would misread a genuine neutral as a failure".

    That was correct WHILE both price helpers returned 50 for empty input — 50 meant either
    "flat" or "no data" and the two were indistinguishable. They now return **None** when
    unmeasurable and 50 only for a genuinely flat move, so reading the scores is strictly
    better than inferring from the inputs. The old expression was wrong twice:

      • it was true for a payload that was non-empty but UNUSABLE (a quote carrying no
        change field, a one-row history), and
      • it was ONE flag across BOTH windows, so a present 24h quote made the 7-day arm look
        measured when it had no history at all — which is exactly the case that let a
        fabricated Neutral carry 30-45% of the 7-day blend.
    """
    from app.services import sentiment_service

    raw = inspect.getsource(sentiment_service.SentimentService.get_sentiment)
    # ⚠️ COMMENT-STRIPPED before the NEGATIVE assertion. The note beside the fix names the
    # retired expression, so an unstripped scan fails on the explanation of the bug — and,
    # symmetrically, a positive scan would PASS on prose after a revert. This suite has now
    # tripped over its own commentary three times.
    src = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    assert (
        "has_price_data = price_score_24h is not None or price_score_7d is not None" in src
    ), "availability must come from the scores now that they can express 'unmeasured'"
    assert "bool(price_data) or bool(hist_prices)" not in src, (
        "the input-shaped test is back — it cannot tell 'unusable' from 'present'"
    )


def test_a_genuine_neutral_is_still_reported_as_available():
    """The reason the input-shaped test existed in the first place: a truly flat week must
    NOT be classified as a failure, or the response stops being cached for a real reading."""
    from app.services.sentiment_service import SentimentService as _S

    assert _S._compute_price_sentiment({"changePercentage": 0.0}) == 50
    assert _S._compute_price_sentiment_7d([{"close": 100.0}] * 8) == 50
