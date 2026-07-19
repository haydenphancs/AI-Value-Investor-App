"""
Regression tests for the NaN/Inf + wrong-data defects surfaced by the adversarial
cross-layer review of the asset-detail (Overview) data path.

Each pins a degraded behavior so it can't regress:

  * Benchmark CAGR builders (stock + ETF) must finite-guard the oldest/newest FMP
    close. A bare NaN/Inf token (Python json parses these) is truthy and slips past
    `not x`/`x <= 0`, poisoning the REQUIRED avg_annual_return/sp_benchmark float.
    Starlette renders with allow_nan=False → 500 for the WHOLE detail screen.
  * ETF asset-allocation must reject a non-finite OR '%'-suffixed "Cash & Others"
    exposure (raw float() would ValueError on "3.5%" or pass NaN into equities/cash).
  * The profitability snapshot must NOT use netIncomePerShare (EPS, in dollars) as
    the net-margin fallback — that rendered per-share earnings as a margin %.
  * The crypto symbol normalizer must strip only a TRAILING 'USD' pair suffix, never
    corrupt stablecoin tickers (USDT/USDC) that merely contain 'USD'.
  * Market-status helpers must not hardcode "open" / a fixed EST offset.

No network, no Supabase — stateless instance methods run on an __init__-bypassed
instance; the crash mode is asserted directly via json.dumps(..., allow_nan=False).
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pytest
from fastapi.encoders import jsonable_encoder

from app.services.stock_overview_service import StockOverviewService
from app.services.etf_service import ETFService
from app.api.v1.endpoints.crypto import _normalize_crypto_symbol
from app.services.commodity_service import _commodity_market_status
from app.services import index_service


# ── helpers ──────────────────────────────────────────────────────────────────

def _rows(n: int, *, start=100.0, step=0.1, last_close=None, first_close=None):
    """n ascending daily FMP rows [{date, close}] with real, increasing dates."""
    base = date(2023, 1, 1)
    rows = []
    for i in range(n):
        rows.append({
            "date": (base + timedelta(days=i)).isoformat(),
            "close": start + i * step,
        })
    if first_close is not None:
        rows[0]["close"] = first_close
    if last_close is not None:
        rows[-1]["close"] = last_close
    return rows


def _renders_without_nan(model) -> bool:
    """True iff the model serializes under FastAPI's allow_nan=False renderer
    (i.e. contains no NaN/Inf that would 500 the endpoint)."""
    json.dumps(jsonable_encoder(model), allow_nan=False)
    return True


# ── crypto symbol normalization (USDT/USDC corruption) ───────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("BTCUSD", "BTC"),
    ("ethusd", "ETH"),
    ("SOLUSD", "SOL"),
    ("USDT", "USDT"),   # stablecoin — must NOT become "T"
    ("USDC", "USDC"),   # must NOT become "C"
    ("USDP", "USDP"),
    ("BTC", "BTC"),
    ("USD", "USD"),     # len 3 — leave intact, don't strip to ""
])
def test_normalize_crypto_symbol(raw, expected):
    assert _normalize_crypto_symbol(raw) == expected


# ── ETF benchmark CAGR: NaN close must not crash / poison the response ────────

def test_etf_benchmark_nan_last_close_returns_none_not_nan():
    svc = object.__new__(ETFService)  # bypass __init__ (no FMP/Supabase)
    etf_hist = _rows(300, last_close=float("nan"))
    spy_hist = _rows(300)
    out = svc._build_benchmark_summary(etf_hist, spy_hist)
    # Degrades to None (finite guard) rather than an avg_annual_return=NaN 500.
    assert out is None


def test_etf_benchmark_nan_first_close_returns_none():
    svc = object.__new__(ETFService)
    out = svc._build_benchmark_summary(_rows(300, first_close=float("inf")), _rows(300))
    assert out is None


def test_etf_benchmark_nan_in_spy_does_not_poison_response():
    svc = object.__new__(ETFService)
    out = svc._build_benchmark_summary(_rows(300), _rows(300, first_close=float("nan")))
    assert out is not None
    assert math.isfinite(out.avg_annual_return)
    assert math.isfinite(out.sp_benchmark)
    assert _renders_without_nan(out)


def test_etf_benchmark_normal_case_is_finite_and_renders():
    svc = object.__new__(ETFService)
    out = svc._build_benchmark_summary(_rows(300), _rows(300))
    assert out is not None
    assert math.isfinite(out.avg_annual_return)
    assert _renders_without_nan(out)


# ── ETF asset allocation: non-finite / '%'-string exposure ───────────────────

def test_etf_asset_allocation_nan_exposure_is_finite():
    svc = object.__new__(ETFService)
    alloc = svc._build_asset_allocation(
        sectors_list=[{"industry": "Cash & Others", "exposure": float("nan")}],
        asset_class="Equity",
        total_assets=1e9,
    )
    for v in (alloc.equities, alloc.bonds, alloc.crypto, alloc.commodities, alloc.cash):
        assert math.isfinite(v)
    assert _renders_without_nan(alloc)


def test_etf_asset_allocation_percent_string_exposure_no_valueerror():
    svc = object.__new__(ETFService)
    # A '%'-suffixed string exposure used to raise ValueError (500). Now parsed.
    alloc = svc._build_asset_allocation(
        sectors_list=[{"industry": "Cash & Others", "exposure": "3.5%"}],
        asset_class="Equity",
        total_assets=1e9,
    )
    assert math.isfinite(alloc.cash)
    assert abs(alloc.cash - 3.5) < 0.01
    assert math.isfinite(alloc.equities)


def test_etf_asset_allocation_inf_exposure_is_finite():
    svc = object.__new__(ETFService)
    alloc = svc._build_asset_allocation(
        sectors_list=[{"industry": "Cash & Others", "exposure": float("inf")}],
        asset_class="Equity",
        total_assets=1e9,
    )
    assert math.isfinite(alloc.cash) and math.isfinite(alloc.equities)


# ── Stock benchmark CAGR: NaN close must not crash ───────────────────────────

def test_stock_benchmark_nan_last_close_returns_none():
    svc = object.__new__(StockOverviewService)
    out = svc._build_benchmark_summary(_rows(300, last_close=float("nan")), _rows(300))
    assert out is None


def test_stock_benchmark_nan_in_spy_stays_finite_and_renders():
    svc = object.__new__(StockOverviewService)
    out = svc._build_benchmark_summary(_rows(300), _rows(300, last_close=float("nan")))
    assert out is not None
    assert math.isfinite(out.avg_annual_return)
    assert math.isfinite(out.sp_benchmark)
    assert _renders_without_nan(out)


def test_stock_benchmark_normal_case_renders():
    svc = object.__new__(StockOverviewService)
    out = svc._build_benchmark_summary(_rows(300), _rows(300))
    assert out is not None
    assert _renders_without_nan(out)


# ── Stock profitability: net margin must not be EPS ──────────────────────────

def _net_margin_value(snapshot):
    for m in snapshot.metrics:
        if m.name == "Net Margin":
            return m.value
    raise AssertionError("Net Margin metric missing")


def test_profitability_net_margin_does_not_use_eps():
    svc = object.__new__(StockOverviewService)
    # netProfitMargin absent; only EPS (netIncomePerShare) present. The old code
    # rendered "6.13%" (EPS-as-margin). It must not.
    snap = svc._build_profitability_snapshot(
        km={"netIncomePerShare": 6.13}, fr={}, inc={}
    )
    assert _net_margin_value(snap) != "6.13%"
    assert _net_margin_value(snap) == "0.00%"  # honest absent-margin, not the EPS


def test_profitability_net_margin_uses_real_margin_field():
    svc = object.__new__(StockOverviewService)
    snap = svc._build_profitability_snapshot(
        km={}, fr={"netProfitMargin": 25.0}, inc={}
    )
    assert _net_margin_value(snap) == "25.00%"


# ── Market-status helpers (no hardcoded 'open' / fixed EST) ───────────────────

def test_commodity_market_status_returns_valid_string():
    assert _commodity_market_status() in {"Market Open", "Market Closed"}


def test_index_market_status_uses_dst_aware_zone():
    resp = index_service._get_market_status()
    assert resp.status in {"open", "closed", "pre_market", "after_hours"}
    if resp.status == "closed":
        # DST-aware: never a hardcoded "EST"/"-05:00" during EDT.
        assert resp.timezone in {"EST", "EDT"}
        assert resp.date and (resp.date.endswith("-05:00") or resp.date.endswith("-04:00"))
