"""
Regression tests for the Holders-tab defects surfaced by the adversarial review.

Primary defect (F1/F2): holders_service had a _safe_float WITHOUT the isfinite
guard that growth/profit_power/etc. have, PLUS direct float()/int() casts on FMP
aggregate fields (institutional position counts + net 13F share change). A NaN/Inf
FMP token would:

  * flow into a REQUIRED response float and 500 the WHOLE Holders tab — FastAPI
    renders via Starlette's JSONResponse (json.dumps(..., allow_nan=False)), which
    raises on NaN/Inf;
  * raise ValueError from int(NaN) in the flow-summary/quarter-flow builders (→ 502);
  * poison the hedge_fund_quarters cache — Postgres numeric rejects NaN and the
    buy_volume>=0 CHECK rejects a NaN volume on upsert.

httpx's response.json() parses the non-standard JSON tokens NaN / Infinity /
-Infinity into Python floats by default, so an FMP payload really can inject them.

Pure-function tests; no network, no Supabase (services are built via __new__ to skip
the FMP/Supabase client init in __init__).
"""

from __future__ import annotations

import json
import math

import pytest

from app.services.holders_service import (
    HoldersService,
    _safe_float,
    _safe_int,
)
from app.schemas.holders import SmartMoneyFlowDataPointSchema


# ── _safe_float now rejects NaN/Inf (was passed straight through) ─────────────

@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"),
    "NaN", "Infinity", "-Infinity", "inf", "nan",
])
def test_safe_float_rejects_non_finite(value):
    assert _safe_float({"x": value}, "x") == 0.0
    assert _safe_float({"x": value}, "x", default=-1.0) == -1.0


@pytest.mark.parametrize("value,expected", [
    (12.5, 12.5), ("12.5", 12.5), (0, 0.0), (-3.2, -3.2), (1_000_000, 1_000_000.0),
])
def test_safe_float_passes_finite(value, expected):
    assert _safe_float({"x": value}, "x") == expected


def test_safe_float_none_and_missing_and_garbage():
    assert _safe_float({"x": None}, "x") == 0.0
    assert _safe_float({}, "missing") == 0.0
    assert _safe_float({"x": "abc"}, "x") == 0.0
    assert _safe_float({"x": {"nested": 1}}, "x") == 0.0
    assert _safe_float({}, "missing", default=7.0) == 7.0


# ── _safe_int: int(NaN) raises ValueError; int(inf) raises OverflowError ───────

@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"), None, "abc", [], {},
])
def test_safe_int_rejects_bad(value):
    assert _safe_int(value) == 0
    assert _safe_int(value, default=9) == 9


@pytest.mark.parametrize("value,expected", [
    (5, 5), (5.0, 5), (5.9, 5), ("7", 7), ("7.0", 7), (0, 0), (-2, -2),
])
def test_safe_int_passes_finite(value, expected):
    assert _safe_int(value) == expected


# ── _estimate_buy_sell: invariants across normal + degenerate inputs ──────────

@pytest.mark.parametrize("net,buyers,sellers", [
    (100.0, 60, 40),      # net buy, buyer-skewed
    (-50.0, 30, 70),      # net sell, seller-skewed
    (0.0, 50, 50),        # flat
    (200.0, 50, 50),      # 50/50 split → MAX_GROSS cap path
    (10.0, 5, 0),         # only buyers
    (-10.0, 0, 5),        # only sellers
    (100.0, 0, 0),        # net but no counts (total==0 branch)
    (0.0, 0, 0),          # nothing
])
def test_estimate_buy_sell_invariants(net, buyers, sellers):
    buy, sell = HoldersService._estimate_buy_sell(net, buyers, sellers)
    # Non-negative (hedge_fund_quarters CHECK buy_volume>=0, sell_volume>=0)
    assert buy >= 0.0 and sell >= 0.0
    assert math.isfinite(buy) and math.isfinite(sell)
    # Core constraint: buy - sell == net (within rounding)
    assert abs((buy - sell) - net) < 0.05
    # Gross never exceeds 5× |net| (MAX_GROSS_MULTIPLIER) + rounding slack
    if net != 0:
        assert (buy + sell) <= abs(net) * 5.0 + 0.05


# ── _compute_quarter_flow: NaN-laden FMP data must stay CHECK-safe & finite ────

def test_compute_quarter_flow_nan_data_is_check_safe():
    data = {
        "newPositions": float("nan"),
        "increasedPositions": float("inf"),   # int(inf) would OverflowError raw
        "closedPositions": None,
        "reducedPositions": 2,
        "numberOf13FsharesChange": float("nan"),
        "numberOf13Fshares": float("nan"),
    }
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(data)
    for v in (buy, sell, net):
        assert math.isfinite(v)
    assert buy >= 0.0 and sell >= 0.0        # CHECK-safe
    assert isinstance(buyers, int) and isinstance(sellers, int)
    assert buyers >= 0 and sellers >= 0
    # buyers = 0 (nan) + 0 (inf) ; sellers = 0 (None) + 2
    assert buyers == 0 and sellers == 2


def test_compute_quarter_flow_normal_net_sign():
    # 3 net buyers, +40M share net change → positive net, both bars non-negative
    data = {
        "newPositions": 2, "increasedPositions": 3,
        "closedPositions": 1, "reducedPositions": 1,
        "numberOf13FsharesChange": 40_000_000,
        "numberOf13Fshares": 1_000_000_000,
    }
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(data)
    assert net == pytest.approx(40.0, abs=0.01)   # millions of shares
    assert buy > sell                              # net positive
    assert buyers == 5 and sellers == 2


def test_compute_quarter_flow_magnitude_suppression_keeps_counts():
    # Net change > 50% of shares HELD → corporate-action artifact → zero the bars
    # but keep the real holder counts (chart renders no bar, not garbage).
    data = {
        "newPositions": 3, "increasedPositions": 2,
        "closedPositions": 1, "reducedPositions": 1,
        "numberOf13FsharesChange": 80_000_000,
        "numberOf13Fshares": 100_000_000,        # 80M > 0.5 * 100M
    }
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(data)
    assert (buy, sell, net) == (0.0, 0.0, 0.0)
    assert buyers == 5 and sellers == 2


def test_compute_quarter_flow_split_restatement_cancels_fake_flow():
    # 10:1 split during the quarter: shares 100M → 1000M. Raw change +900M looks
    # like massive buying; restated onto the post-split basis it's ~0 real flow.
    data = {
        "numberOf13Fshares": 1_000_000_000,
        "lastNumberOf13Fshares": 100_000_000,
        "numberOf13FsharesChange": 900_000_000,
        "newPositions": 1, "increasedPositions": 1,
        "closedPositions": 1, "reducedPositions": 1,
    }
    _, _, net, _, _ = HoldersService._compute_quarter_flow(data, split_ratio=10.0)
    assert abs(net) < 1.0    # restated ≈ 0M, not +900M


def test_compute_quarter_flow_spinoff_ratio_not_applied():
    # A 1.25 "split" where the share count did NOT actually grow ~1.25x is a
    # spinoff/ADR change — restatement must NOT fire (would fabricate huge flow).
    data = {
        "numberOf13Fshares": 500_000_000,
        "lastNumberOf13Fshares": 495_000_000,   # barely moved, not ×1.25
        "numberOf13FsharesChange": 5_000_000,
        "newPositions": 2, "increasedPositions": 3,
        "closedPositions": 2, "reducedPositions": 2,
    }
    _, _, net, _, _ = HoldersService._compute_quarter_flow(data, split_ratio=1.25)
    assert net == pytest.approx(5.0, abs=0.01)   # raw change kept, not restated


# ── The actual 500 regression: NaN aggregate → JSON-serializable flow summary ─

def test_flow_summary_nan_aggregate_is_json_safe():
    """Feed a NaN/Inf-laden positions-summary through the real builder and prove
    the resulting schema serializes under allow_nan=False — the exact path that
    500'd the whole Holders tab before the guards."""
    svc = HoldersService.__new__(HoldersService)   # skip FMP/Supabase init
    aggregate = {
        "newPositions": float("nan"),
        "increasedPositions": float("inf"),
        "closedPositions": None,
        "reducedPositions": 4,
        "numberOf13FsharesChange": float("nan"),
        "totalInvested": float("nan"),
        "numberOf13Fshares": float("nan"),
    }
    summary = svc._build_institutional_flow_summary(
        [], aggregate_data=aggregate, daily_prices=None
    )
    # Starlette renders with allow_nan=False — this must NOT raise.
    json.dumps(summary.model_dump(), allow_nan=False)
    assert math.isfinite(summary.in_flow_in_billions)
    assert math.isfinite(summary.out_flow_in_billions)


# ── G10: negative FMP position counts are clamped (hedge_fund_quarters CHECK) ──

def test_compute_quarter_flow_negative_counts_clamped():
    # FMP data artifact: a negative position count. buyers_count/sellers_count
    # must land >= 0 or the whole 8-row upsert batch fails the CHECK atomically.
    data = {
        "newPositions": -5, "increasedPositions": 2,      # sum -3 → clamp 0
        "closedPositions": -1, "reducedPositions": -2,    # sum -3 → clamp 0
        "numberOf13FsharesChange": 1_000_000,
        "numberOf13Fshares": 1_000_000_000,
    }
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(data)
    assert buyers >= 0 and sellers >= 0
    assert buy >= 0.0 and sell >= 0.0


# ── G8: an undetected low-ratio split w/ real change must NOT emit a wrong bar ──

def test_compute_quarter_flow_ambiguous_split_suppressed():
    # 2:1 split (ratio 2.0) while institutions trimmed ~20%: last=100M pre-split,
    # cur=160M post-split. Raw change +60M reads as a huge BUY, but on the
    # post-split basis it's a ~40M SELL. Undetected by the ±15% clean test
    # (cur/last=1.6, |1.6-2|=0.4 > 0.3) yet clearly a real split (1.6 >= 1.5
    # midpoint) → suppress the flow rather than fabricate a wrong-sign bar.
    data = {
        "numberOf13Fshares": 160_000_000,
        "lastNumberOf13Fshares": 100_000_000,
        "numberOf13FsharesChange": 60_000_000,
        "newPositions": 4, "increasedPositions": 3,
        "closedPositions": 5, "reducedPositions": 6,
    }
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(
        data, split_ratio=2.0
    )
    assert (buy, sell, net) == (0.0, 0.0, 0.0)   # no fabricated bar
    assert buyers == 7 and sellers == 11         # real counts kept


def test_compute_quarter_flow_no_split_ratio_unaffected():
    # Same numbers WITHOUT a split present → normal raw-net behavior (the
    # magnitude guard applies: 60M vs 160M held is < 50% → real +60M flow).
    data = {
        "numberOf13Fshares": 160_000_000,
        "lastNumberOf13Fshares": 100_000_000,
        "numberOf13FsharesChange": 60_000_000,
        "newPositions": 4, "increasedPositions": 3,
        "closedPositions": 5, "reducedPositions": 6,
    }
    _, _, net, _, _ = HoldersService._compute_quarter_flow(data)  # split_ratio=1.0
    assert net == pytest.approx(60.0, abs=0.01)


# ── G2: "Over $X" congress max must be >= the midpoint used for change_in_millions

def test_parse_congress_amount_max_over_bucket_not_below_midpoint():
    amt = "Over $50,000,000"
    midpoint_m = HoldersService._parse_congress_amount(amt)      # 75.0 (base*1.5)
    max_m = HoldersService._parse_congress_amount_max(amt)       # must be >= 75.0
    assert max_m >= midpoint_m
    assert max_m == pytest.approx(75.0, abs=0.001)


def test_parse_congress_amount_max_ranges_and_garbage():
    assert HoldersService._parse_congress_amount_max("$1,001 - $15,000") == pytest.approx(0.015, abs=1e-9)
    assert HoldersService._parse_congress_amount_max("") == 0.0
    assert HoldersService._parse_congress_amount_max("garbage") == 0.0
    # A non-finite token must not leak NaN into amount_range_max_millions.
    assert HoldersService._parse_congress_amount_max("NaN - NaN") == 0.0


# ── G1/G3: insider names normalized + reportingCik fallback can't crash ────────

def test_build_insider_activities_normalizes_names_and_dedups_counts():
    svc = HoldersService.__new__(HoldersService)
    # SAME insider reported under two FMP name shapes on two buys → one buyer.
    trades = [
        {"reportingName": "ELLISON LAWRENCE JOSEPH", "transactionType": "P-Purchase",
         "securityName": "Common Stock", "securitiesTransacted": 1000, "price": 10.0,
         "transactionDate": "2026-05-01"},
        {"reportingName": "Ellison, Lawrence Joseph", "transactionType": "P-Purchase",
         "securityName": "Common Stock", "securitiesTransacted": 2000, "price": 10.0,
         "transactionDate": "2026-05-02"},
    ]
    acts = svc._build_insider_activities(trades, roster=[])
    assert len(acts) == 2
    names = {a.name for a in acts}
    assert names == {"Lawrence Joseph Ellison"}          # one canonical name
    summary = svc._build_insider_activity_summary(acts)
    assert summary.num_buyers == 1                        # not double-counted


def test_build_insider_activities_numeric_cik_does_not_crash():
    svc = HoldersService.__new__(HoldersService)
    # reportingName absent, reportingCik a numeric JSON value → must not raise
    # AttributeError on `.lower()` (str-coerced) and must still 500-safely build.
    trades = [
        {"reportingCik": 1214128, "transactionType": "S-Sale",
         "securityName": "Common Stock", "securitiesTransacted": 500, "price": 20.0,
         "transactionDate": "2026-05-03"},
    ]
    acts = svc._build_insider_activities(trades, roster=[])
    assert len(acts) == 1
    assert isinstance(acts[0].name, str) and acts[0].name


# ── F4 gate: one-sided cache check only fires when BOTH sides participated ──────

def _flow_pt(buy, sell, buyers, sellers):
    return SmartMoneyFlowDataPointSchema(
        month="Q1\n'25", buy_volume=buy, sell_volume=sell, has_activity=True,
        net_flow=buy - sell, buyers_count=buyers, sellers_count=sellers,
    )


def test_one_sided_check_ignores_legit_accumulation_quarter():
    from app.schemas.holders import HoldersResponse, SmartMoneyDataSchema
    # Accumulation quarter: buyers>0, sellers==0 → sell_volume legitimately 0.
    # Must NOT be flagged stale (that would defeat the 24h cache forever).
    hf = SmartMoneyDataSchema(tab="Institutions", flow_data=[_flow_pt(5.0, 0.0, 8, 0)])
    resp = HoldersResponse(symbol="X", hedge_funds_data=hf)
    assert HoldersService._has_one_sided_hedge_fund_data(resp) is False


def test_one_sided_check_flags_legacy_both_sided_row():
    from app.schemas.holders import HoldersResponse, SmartMoneyDataSchema
    # Legacy pre-estimate row: BOTH sides have institutions but only buy has
    # volume → genuinely stale, should be rejected.
    hf = SmartMoneyDataSchema(tab="Institutions", flow_data=[_flow_pt(5.0, 0.0, 8, 4)])
    resp = HoldersResponse(symbol="X", hedge_funds_data=hf)
    assert HoldersService._has_one_sided_hedge_fund_data(resp) is True
