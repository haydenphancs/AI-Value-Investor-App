"""Backend↔iOS contract for `CryptoDetailResponse`. None existed before Phase 5.

`.claude/rules/testing.md` makes a parity test mandatory when a response shape iOS
decodes changes, and moving crypto from FMP to CoinGecko changes almost every field's
PROVENANCE. A failure here is a decode crash on the crypto detail screen in production.

The non-negotiable half is the **non-Optional `Double`s**. `CryptoDetailAPIResponse`
declares `currentPrice` / `priceChange` / `priceChangePercent` and `RelatedCryptoDTO`
declares `price` / `changePercent` as plain `Double`, so on those fields:

  * `null` CRASHES the decode outright, and
  * `0.0` is a fabricated fact — the `$0.00` every related coin rendered before this work.

"Unknown" therefore has exactly one legal representation on these fields: the ROW IS
OMITTED. That is what `_build_related_cryptos` now does, and what these tests pin.

`PerformancePeriodDTO.changePercent` is likewise non-Optional, so an unreachable window
(3Y/5Y/10Y/All-Time under a 2-year source) must be an ABSENT ROW, never a null or a zero.
"""

from __future__ import annotations

import json
import math

import pytest

from app.schemas.crypto import (
    CryptoDetailResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    PerformancePeriodResponse,
    RelatedCryptoResponse,
)


def _minimal(**over):
    """A worst-case-but-valid payload: every list empty, every optional absent."""
    base = dict(
        symbol="BTC",
        name="Bitcoin",
        current_price=78_595.0,
        price_change=-402.0,
        price_change_percent=-0.51,
        market_status="24/7 Trading",
        chart_data=[],
        key_statistics_groups=[],
        performance_periods=[],
        snapshots=[],
        crypto_profile={
            "description": "", "symbol": "BTC", "launch_date": "",
            "consensus_mechanism": "", "blockchain": "", "website": "",
        },
        related_cryptos=[],
        benchmark_summary=None,
        news_articles=[],
    )
    base.update(over)
    return base


# ── the shape iOS declares ───────────────────────────────────────────────────

def test_the_minimal_payload_validates_and_carries_every_field_ios_decodes():
    r = CryptoDetailResponse.model_validate(_minimal())
    d = r.model_dump()
    # Names the iOS CodingKeys map, at the level they are read.
    for key in (
        "symbol", "name", "current_price", "price_change", "price_change_percent",
        "market_status", "chart_data", "key_statistics_groups", "performance_periods",
        "snapshots", "crypto_profile", "related_cryptos", "news_articles",
    ):
        assert key in d, f"iOS decodes {key!r} as non-Optional"
    # benchmark_summary is Optional on BOTH sides — a nil hides the card.
    assert d["benchmark_summary"] is None


def test_a_suppressed_benchmark_is_null_not_a_zeroed_card():
    """Under a 2-year history the CAGR card is suppressed. `BenchmarkSummaryDTO?` is
    Optional, so null is legal and hides the card; a zero-filled object would render
    "0.00% avg annual return" as if measured."""
    r = CryptoDetailResponse.model_validate(_minimal(benchmark_summary=None))
    assert r.benchmark_summary is None


# ── the fields that CRASH on null ────────────────────────────────────────────

@pytest.mark.parametrize("field", ["current_price", "price_change", "price_change_percent"])
def test_the_header_doubles_reject_null(field):
    """iOS declares these `Double`, not `Double?` — a null is a decode crash."""
    with pytest.raises(Exception):
        CryptoDetailResponse.model_validate(_minimal(**{field: None}))


@pytest.mark.parametrize("field", ["price", "change_percent"])
def test_related_coin_doubles_reject_null(field):
    payload = {"symbol": "ETH", "name": "Ethereum", "price": 2496.0, "change_percent": 0.06}
    payload[field] = None
    with pytest.raises(Exception):
        RelatedCryptoResponse.model_validate(payload)


def test_an_unpriceable_related_coin_is_omitted_rather_than_zeroed():
    """The contract the $0.00 bug violated, stated as a test.

    There is no representation of "unknown" on a non-Optional Double, so the ONLY
    correct degrade is to leave the row out. Six coins at `$0.00 / +0.00%` is what the
    screen showed when the builder emitted a row per requested symbol regardless.
    """
    from app.services.crypto_service import CryptoService

    out = CryptoService._build_related_cryptos(
        CryptoService.__new__(CryptoService),
        # raw_quotes: only ETH came back priced. Symbols carry the USD pair suffix,
        # which is how the quote map is keyed.
        [
            {"symbol": "ETHUSD", "price": 2496.0, "changePercentage": 0.06},
            {"symbol": "SOLUSD", "price": None, "changePercentage": None},
            # DOGE absent from the response entirely — the other way a row goes missing.
        ],
        ["ETH", "SOL", "DOGE"],
    )
    assert [r.symbol for r in out] == ["ETH"], (
        "an unpriceable related coin must be OMITTED, never emitted at price 0.0"
    )
    assert all(r.price > 0 for r in out)


# ── nothing non-finite may reach the wire ────────────────────────────────────

def test_the_whole_response_serializes_under_allow_nan_false():
    """FastAPI's encoder uses allow_nan=False; a NaN anywhere is a hard 500.

    Crypto is the worst case for this: sub-penny prices, derived percentages with
    near-zero denominators, and a source that returns JSON nulls mid-series.
    """
    r = CryptoDetailResponse.model_validate(_minimal(
        chart_data=[{"date": "2026-09-08", "open": None, "high": None,
                     "low": None, "close": 0.00000539, "volume": 1.0}],
        key_statistics_groups=[KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="52-Week High", value="$126,080.00",
                             is_highlighted=False),
        ])],
        performance_periods=[PerformancePeriodResponse(label="1 Year", change_percent=-12.5)],
        related_cryptos=[RelatedCryptoResponse(
            symbol="ETH", name="Ethereum", price=2496.0, change_percent=0.06)],
    ))
    json.dumps(r.model_dump(), allow_nan=False)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_header_price_never_serializes_silently(bad):
    """Pydantic accepts these floats, so the guard has to be the ENCODER contract.

    This test documents why every builder routes through `_finite_or_none`: without it
    a NaN validates cleanly here and then 500s the request at serialization time.
    """
    r = CryptoDetailResponse.model_validate(_minimal(current_price=bad))
    assert not math.isfinite(r.current_price)
    with pytest.raises(ValueError):
        json.dumps(r.model_dump(), allow_nan=False)


# ── sub-penny coins must survive rounding ────────────────────────────────────

def test_sub_penny_prices_are_not_collapsed_to_zero():
    """SHIB trades near $0.00000539; `round(close, 2)` renders every bar as 0.0."""
    from app.services.crypto_service import _round_close

    for price in (5.39e-06, 1.2e-08, 0.00012345):
        assert _round_close(price) > 0, f"{price} collapsed to zero"
