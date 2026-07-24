"""
Outlier regression tests for the 2026-07-23 Holders-tab adversarial review.

Complements ``test_holders_service_guards.py`` (the 2026-07-19 NaN/500 pass) and
``test_hedge_fund_flow_math.py``. Everything here pins a defect that was
CONFIRMED against live FMP data by an independent verification pass — each test
names the concrete wrong number or crash it prevents.

Cross-screen parity is the theme: the same quantity is rendered on the Holders
tab (``TickerDetailView``), inside ``TickerReportView``, and on the Overview
"Insiders & Ownership" snapshot. All three read ``HoldersService``, so a unit or
window bug here shows up as two screens disagreeing.

Pure-function tests: no network, no Supabase (services built via ``__new__`` to
skip the FMP/Supabase client init in ``__init__``).
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import pytest

from app.schemas.holders import (
    DailyPricePointSchema,
    HoldersResponse,
    SmartMoneyDataSchema,
    SmartMoneyFlowSummarySchema,
)
from app.services.holders_service import (
    HoldersService,
    _cache,
    _cache_set,
    _CACHE_MAX_ENTRIES,
    _holder_name,
)
from app.services.ownership_snapshot_service import (
    OwnershipSnapshotService,
    _fmt_share_flow,
)
from app.utils.period_labels import latest_filed_13f_quarter


def _svc() -> HoldersService:
    return object.__new__(HoldersService)


# ── 13F filing lag: the newest quarter is only half-filed ─────────────────────
#
# SEC Rule 13f-1 gives filers 45 days after quarter-end. Selecting the quarter
# that merely ENDED most recently read a partially-filed aggregate: on
# 2026-07-23, AAPL's Q2'26 summary held 1,760 of 6,347 filers, so the 4,587 that
# had not filed yet were counted as `closedPositions` and produced
# numberOf13FsharesChange = -9.1B → "$434.0B in / $3058.5B out".

@pytest.mark.parametrize(
    "today,expected",
    [
        ("2026-01-10", (2025, 3)),   # Q4'25 ended Dec 31, due ~Feb 14 — not filed
        ("2026-02-20", (2025, 4)),   # Q4'25 deadline passed
        ("2026-05-14", (2025, 4)),   # Q1'26 due May 15 — one day early, still Q4
        ("2026-05-16", (2026, 1)),   # Q1'26 deadline passed
        ("2026-07-23", (2026, 1)),   # the live case that produced the $3T exodus
        ("2026-08-20", (2026, 2)),   # Q2'26 deadline passed
        ("2026-11-15", (2026, 3)),
        ("2027-01-01", (2026, 3)),   # year rollover, Q4'26 not yet due
        ("2027-02-14", (2026, 4)),
    ],
)
def test_latest_filed_13f_quarter_respects_the_45_day_deadline(today, expected):
    now = datetime.fromisoformat(today).replace(tzinfo=timezone.utc)
    assert latest_filed_13f_quarter(now) == expected


def test_quarter_keys_never_include_an_unfiled_quarter():
    """The 8 chart quarters must all be settled, oldest-first and contiguous."""
    pairs = HoldersService._generate_quarter_keys(8)
    assert len(pairs) == 8
    assert pairs[-1] == latest_filed_13f_quarter()
    assert pairs == sorted(pairs)
    for (y0, q0), (y1, q1) in zip(pairs, pairs[1:]):
        assert (y1, q1) == (y0 + 1, 1) if q0 == 4 else (y1, q1) == (y0, q0 + 1)


# ── Reverse splits: the ambiguous-zone test was direction-blind ───────────────

def _quarter(**over):
    data = {
        "numberOf13Fshares": 105_000_000,
        "lastNumberOf13Fshares": 100_000_000,
        "numberOf13FsharesChange": 5_000_000,
        "newPositions": 30, "increasedPositions": 70,
        "closedPositions": 10, "reducedPositions": 40,
    }
    data.update(over)
    return data


def test_reverse_split_quarter_keeps_real_flow_when_counts_did_not_move():
    """A 1:10 reverse split with an ALREADY-ADJUSTED 13F basis (ratio_obs ~1.0)
    must keep the real +5M-share buying.

    The guard was `ratio_obs >= (1 + split_ratio) / 2`, whose midpoint only
    separates "jumped toward the split" from "did not move" when the split
    INFLATES the count. For ratio 0.1 the midpoint is 0.55, so a perfectly normal
    ratio_obs of 1.05 tripped it and zeroed the bar.
    """
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(
        _quarter(), split_ratio=0.1
    )
    assert net == pytest.approx(5.0)
    assert (buy, sell) != (0.0, 0.0)
    assert (buyers, sellers) == (100, 50)


def test_reverse_split_quarter_restates_a_real_reverse_split():
    """A genuine 1:10 (count collapses ~10x) is cleanly detected and restated,
    so the -89.5M raw change is NOT shown as a mass exodus."""
    _, _, net, _, _ = HoldersService._compute_quarter_flow(
        _quarter(
            numberOf13Fshares=10_500_000,
            lastNumberOf13Fshares=100_000_000,
            numberOf13FsharesChange=-89_500_000,
        ),
        split_ratio=0.1,
    )
    assert net == pytest.approx(0.5)  # 10.5M - 100M*0.1


def test_reverse_split_ambiguous_zone_still_suppresses():
    """Count moved decisively TOWARD the reverse ratio but not cleanly → the raw
    net is an inseparable split+flow mix, so emit no bar (counts survive)."""
    buy, sell, net, buyers, sellers = HoldersService._compute_quarter_flow(
        _quarter(
            numberOf13Fshares=30_000_000,
            lastNumberOf13Fshares=100_000_000,
            numberOf13FsharesChange=-70_000_000,
        ),
        split_ratio=0.1,
    )
    assert (buy, sell, net) == (0.0, 0.0, 0.0)
    assert (buyers, sellers) == (100, 50)


def test_forward_split_behaviour_is_unchanged():
    """Regression guard: the reverse-split fix must not alter forward splits."""
    assert HoldersService._compute_quarter_flow(_quarter(), 2.0)[2] == pytest.approx(5.0)
    assert HoldersService._compute_quarter_flow(
        _quarter(
            numberOf13Fshares=200_000_000,
            lastNumberOf13Fshares=100_000_000,
            numberOf13FsharesChange=100_000_000,
        ),
        2.0,
    )[:3] == (0.0, 0.0, 0.0)


# ── Holder names: null / blank / non-string must never crash or blank out ─────

@pytest.mark.parametrize(
    "record,expected",
    [
        ({"investorName": None, "holder": None}, "Unknown"),
        ({}, "Unknown"),
        ({"investorName": ""}, "Unknown"),
        ({"investorName": "   "}, "Unknown"),
        ({"investorName": "", "holder": "FMR LLC"}, "FMR LLC"),
        ({"investorName": "  VANGUARD  "}, "VANGUARD"),
        ({"investorName": 12345}, "12345"),
    ],
)
def test_holder_name_never_returns_none_or_blank(record, expected):
    """`.get(k, default)` does NOT fall back on a present-but-null value, and
    `InstitutionalHolderSchema.name` / `TopInstitutionSchema.name` are required
    `str` — a None would ValidationError and 502 the whole tab. A blank name also
    rendered as an anonymous row in the Top-10 sheet."""
    assert _holder_name(record) == expected


def test_top_institutions_survive_a_null_name_row():
    rows = [
        {"investorName": None, "holder": None, "marketValue": 4.6e8, "ownership": 0.04},
        {"investorName": "VANGUARD GROUP INC", "marketValue": 2.5e11, "ownership": 8.3},
    ]
    out = HoldersService._build_top_institutions(_svc(), rows)
    assert [i.name for i in out] == ["VANGUARD GROUP INC", "Unknown"]
    # _categorize_institution does name.lower() — a None would AttributeError.
    assert all(i.category for i in out)


# ── Top-10 dollar precision: the $100M grid ──────────────────────────────────

def _ios_format_value_in_billions(v: float) -> str:
    """Mirror of iOS `TopInstitution.formattedValue` (HoldersModels.swift)."""
    return f"${v:.1f}B" if v >= 1 else f"${v * 1000:.0f}M"


@pytest.mark.parametrize(
    "market_value,expected",
    [
        (48_500_000, "$48M"),      # was "$0M"
        (52_000_000, "$52M"),      # was "$100M" (+92%)
        (149_000_000, "$149M"),    # was "$100M" (-33%)
        (2.5e11, "$250.0B"),       # mega-cap branch unchanged
    ],
)
def test_top_institution_value_survives_the_round_trip(market_value, expected):
    """Backend rounded `value / 1e9` to 1 dp — $100M of resolution — while iOS
    re-expands the sub-$1B branch as `valueInBillions * 1000` millions. The
    sub-$1B branch could therefore only ever print $0M/$100M/$200M/..."""
    out = HoldersService._build_top_institutions(
        _svc(), [{"investorName": "X", "marketValue": market_value, "ownership": 1.0}]
    )
    assert _ios_format_value_in_billions(out[0].value_in_billions) == expected


def test_top_institution_percent_keeps_sub_tenth_precision():
    """iOS `formatOwnershipPercent` prints down to 4 dp; backend `round(pct, 1)`
    collapsed every sub-0.05% stake to 0.0 → "0.00%"."""
    out = HoldersService._build_top_institutions(
        _svc(), [{"investorName": "X", "marketValue": 1e6, "ownership": 0.0412}]
    )
    assert out[0].percent_ownership == pytest.approx(0.0412)


def test_top_institutions_rank_by_value_across_the_full_fetched_pool():
    """FMP returns rows sorted by OWNERSHIP %, but this sheet ranks by MARKET
    VALUE. Pre-truncating the caller's list to [:10] dropped rows that ranked
    outside the top 10 by percent yet inside it by value (LUV Q1'26: Ameriprise
    $375.1M discarded while FMR LLC $366.8M was shown at #10)."""
    rows = [
        {"investorName": f"H{i}", "marketValue": 1e9 - i * 1e6, "ownership": 10 - i * 0.1}
        for i in range(10)
    ]
    rows.append({"investorName": "BIGGEST", "marketValue": 5e9, "ownership": 0.5})
    out = HoldersService._build_top_institutions(_svc(), rows)
    assert out[0].name == "BIGGEST"
    assert len(out) == 10


# ── Top-10 insiders: ranking must survive a failed quote ─────────────────────

def test_top_insiders_rank_by_shares_when_price_is_unavailable():
    """current_price == 0 zeroed every valueInMillions, so the sort was a no-op
    and the ranking degenerated to raw FMP roster order — a junior officer could
    outrank the founder while the share counts beside them said otherwise."""
    roster = [
        {"owner": "JUNIOR, ANALYST", "title": None, "numberOfShares": 1_000},
        {"owner": "ELLISON LAWRENCE J", "title": "officer: CEO",
         "numberOfShares": 1_100_000_000},
        {"owner": "MID, MANAGER", "typeOfOwner": "director", "numberOfShares": 50_000},
    ]
    out = HoldersService._build_top_insiders(
        _svc(), roster, current_price=0.0, outstanding_shares=2.8e9
    )
    assert [i.rank for i in out] == [1, 2, 3]
    assert out[0].name.endswith("Ellison")
    assert out[-1].name.endswith("Junior")
    # A present-but-null title must not become None (required str on the schema).
    assert all(isinstance(i.title, str) and i.title for i in out)


# ── Shareholder breakdown: the clamp fabricated "Public/Other 0.0%" ──────────

def test_overlapping_ownership_is_reported_honestly_not_clamped():
    """PLCE Q1'26: freeFloat 34.956 → insiders 65.0%, ownershipPercent 78.35.
    The old `min(100 - insiders, institutions)` rewrote institutions to 35.0%,
    contradicting the SAME screen's Overview tab ("% Held Inst. 78.35%") by 43pp.
    Insider blocks that are themselves 13F filers legitimately overlap."""
    out = HoldersService._build_shareholder_breakdown(
        _svc(),
        profile={"freeFloat": 34.956},
        inst_holders=[],
        insider_roster=[],
        current_price=10.0,
        inst_summary={"ownershipPercent": 78.3514},
    )
    assert out.insiders_percent == pytest.approx(65.0, abs=0.1)
    assert out.institutions_percent == pytest.approx(78.4, abs=0.1)
    assert out.public_other_percent == 0.0  # floored, not fabricated from a rewrite


def test_breakdown_slices_are_never_negative():
    for free_float, own in [(0.0, 0.0), (100.0, 0.0), (150.0, 200.0), (-5.0, -5.0)]:
        out = HoldersService._build_shareholder_breakdown(
            _svc(), {"freeFloat": free_float}, [], [], 0.0,
            {"ownershipPercent": own},
        )
        for v in (out.insiders_percent, out.institutions_percent,
                  out.public_other_percent):
            assert v >= 0.0 and math.isfinite(v)


# ── Congress: dedup key, Exchange, non-string amounts ────────────────────────

def test_dedup_keeps_distinct_same_day_disclosures():
    """NVDA 2026-06-26, Cleo Fields, Purchase, "$1,001 - $15,000" arrives as three
    rows differing only by assetDescription; MPWR 2026-06-05 Byron Donalds has a
    Self row and a Spouse row. The old key omitted both fields, so 2 of 3 (and the
    Spouse account) were silently dropped — understating congress buy/sell totals,
    the monthly bar, AND the report's Hidden Market Signals module."""
    base = {
        "transactionDate": "2026-06-26", "firstName": "Cleo", "lastName": "Fields",
        "type": "Purchase", "amount": "$1,001 - $15,000", "owner": "",
    }
    rows = [
        {**base, "assetDescription": "NVIDIA Corporation"},
        {**base, "assetDescription": "NVIDIA Corporation (1)"},
        {**base, "assetDescription": "NVIDIA Corporation (2)"},
        {**base, "assetDescription": "NVIDIA Corporation", "owner": "Spouse"},
    ]
    assert len(HoldersService._dedup_congress_trades(rows, [])) == 4


def test_dedup_still_collapses_the_same_row_from_both_feeds():
    """The disclosure feed and the latest feed return the SAME disclosure; that
    must still collapse, including across the "Sale (Full)"/"Sale (Partial)"
    type variants FMP emits for one trade."""
    row = {
        "transactionDate": "2026-06-05", "firstName": "Byron", "lastName": "Donalds",
        "type": "Sale (Full)", "amount": "$1,001 - $15,000", "owner": "Spouse",
        "assetDescription": "Monolithic Power Systems",
    }
    variant = {**row, "type": "Sale (Partial)"}
    assert len(HoldersService._dedup_congress_trades([row], [variant])) == 1
    # Case / whitespace noise must not defeat it either.
    noisy = {**row, "firstName": " BYRON ", "lastName": "donalds"}
    assert len(HoldersService._dedup_congress_trades([row], [noisy])) == 1


def test_exchange_disclosures_are_not_counted_as_purchases():
    """HONA 2026-06-29: three "Exchange" rows are the Honeywell Aerospace spinoff
    distribution — no money moved. Counting them as buys made the report assert
    net_direction="buy" for a ticker whose only real disclosure was a SALE."""
    trades = [
        {"transactionDate": "2026-06-29", "firstName": "Kevin", "lastName": "Hern",
         "type": "Exchange", "amount": "$15,001 - $50,000", "district": "OK01"},
        {"transactionDate": "2026-07-10", "firstName": "Dan", "lastName": "Newhouse",
         "type": "Sale", "amount": "$1,001 - $15,000", "district": "WA04"},
    ]
    acts = HoldersService._build_congress_activities(_svc(), [], trades, None)
    assert [a.transaction_type for a in acts] == ["Sale"]

    summary = HoldersService._build_congress_activity_summary(acts)
    assert summary.num_buyers == 0 and summary.num_sellers == 1
    assert summary.total_buys_in_millions == 0.0

    # The chart predicate must agree with the list predicate, or the bars and the
    # summary card describe different trade sets.
    sm = HoldersService._build_congress_smart_money(_svc(), [], trades, {}, None)
    assert sum(p.buy_volume for p in sm.flow_data) == 0.0
    assert sum(p.sell_volume for p in sm.flow_data) > 0.0


@pytest.mark.parametrize("amount", [15000, 15000.5, None, "", "garbage", "$1,001 - $15,000"])
def test_parse_congress_amount_max_never_raises(amount):
    """FMP occasionally returns `amount` as a NUMBER; `int.replace` would
    AttributeError → 502 for the whole tab."""
    out = HoldersService._parse_congress_amount_max(amount)
    assert isinstance(out, float) and math.isfinite(out) and out >= 0.0


# ── Institutional activity precision ─────────────────────────────────────────

def _ios_format_total_held(v: float) -> str:
    """Mirror of iOS `InstitutionalActivity.formattedTotalHeld`."""
    return f"Held: ${v:.1f}B" if v >= 1 else f"Held: ${v * 1000:.0f}M"


def test_total_held_keeps_sub_100m_resolution():
    """1 dp in billions snapped every position to a $100M grid: a real $45.3M
    holding rendered "Held: $0M"."""
    rows = [{
        "investorName": "SMALLCAP FUND", "sharesNumber": 1_000_000,
        "changeInSharesNumber": 100_000, "marketValue": 45_300_000,
    }]
    acts = HoldersService._build_institutional_activities(_svc(), rows)
    assert acts, "row should not be dropped"
    assert _ios_format_total_held(acts[0].total_held_in_billions) == "Held: $45M"


def test_flow_summary_keeps_sub_billion_resolution():
    """iOS derives BOTH the net-flow headline ((in - out) * 1000 millions) and
    the green/red bar split from these two fields. At 1 dp a $40M-in / $10M-out
    quarter serialized as 0.0 / 0.0 → "Net Flow: +$0M" over a fabricated even
    50/50 bar (the iOS `total == 0` guard branch)."""
    y, q = latest_filed_13f_quarter()
    quarter_end = f"{y}-{q * 3:02d}-28"
    out = HoldersService._build_institutional_flow_summary(
        _svc(),
        activities=[],
        aggregate_data={
            "newPositions": 40, "increasedPositions": 40,
            "closedPositions": 10, "reducedPositions": 10,
            "numberOf13FsharesChange": 3_000_000,
        },
        daily_prices=[DailyPricePointSchema(date=quarter_end, price=12.0)],
    )
    total = out.in_flow_in_billions + out.out_flow_in_billions
    assert total > 0.0, "a real quarter must not serialize as 0.0 / 0.0"
    # $36M net on a 50/50 buyer/seller split — well under $0.05B, which the old
    # 1-dp rounding erased entirely.
    assert total < 1.0


def test_flow_summary_does_not_use_an_implied_price_when_close_is_missing():
    """The totalInvested/numberOf13Fshares fallback produced a ~20x implied price
    on the still-amending quarter, turning a $12B/$18B quarter into
    "$244.0B in / $365.9B out". With no quarter-end close we fall through to the
    per-holder sum instead."""
    out = HoldersService._build_institutional_flow_summary(
        _svc(),
        activities=[],
        aggregate_data={
            "newPositions": 100, "increasedPositions": 100,
            "closedPositions": 100, "reducedPositions": 100,
            "numberOf13FsharesChange": 50_000_000,
            "totalInvested": 1e13,          # inconsistent with the share count
            "numberOf13Fshares": 1e9,
        },
        daily_prices=None,                   # no quarter-end close
    )
    assert out.in_flow_in_billions == 0.0
    assert out.out_flow_in_billions == 0.0


# ── Price line: never plot a $0 price the stock never had ────────────────────

def test_price_data_is_empty_when_history_is_missing():
    """`get_historical_prices` degrades to [] on failure. Forward-filling from
    last_known_price = 0.0 emitted every month at 0.0, and the iOS chart drew a
    flat blue price line at $0 across the whole window above correct bars."""
    keys = HoldersService._generate_month_keys(12)
    assert HoldersService._build_price_data(_svc(), {}, keys) == []


def test_price_data_skips_leading_months_before_the_first_close():
    """A recent IPO has no closes for the earliest months — those must be omitted,
    not emitted at $0."""
    keys = HoldersService._generate_month_keys(12)
    prices = {keys[-1]: 42.0, keys[-2]: 40.0}
    out = HoldersService._build_price_data(_svc(), prices, keys)
    assert [p.month for p in out] == keys[-2:]
    assert all(p.price > 0 for p in out)


# ── Cross-screen parity: the Overview snapshot reads holders' SHARE counts ────

@pytest.mark.parametrize(
    "net_millions,is_positive,expected",
    [
        (-40.83, False, "Net Sell 40.83M shares"),   # was "Net Sell $41"
        (2.5, True, "Net Buy 2.50M shares"),         # was "Net Buy $2"
        (0.4, False, "Net Sell 400K shares"),        # was "Net Sell $0"
        (1250.0, True, "Net Buy 1.25B shares"),
        (0.0, True, "Neutral"),
        (float("nan"), True, "—"),
        (float("inf"), True, "—"),
    ],
)
def test_ownership_snapshot_formats_share_counts_not_dollars(
    net_millions, is_positive, expected
):
    """`insider_data.summary.total_net_flow` and `hedge_funds_data.summary
    .total_net_flow` are MILLIONS OF SHARES. Formatting them as raw dollars was
    wrong twice — unit ("$" on a share count) and scale (2.5 million shares
    printed as "$2") — so the Overview card and the Holders tab reported the same
    quantity a million-fold apart. Mirrors the iOS `.shares` branch of
    `SmartMoneyFlowSummary.formattedNetFlow`."""
    assert _fmt_share_flow(net_millions, is_positive) == expected


def test_ownership_snapshot_rating_thresholds_are_share_scaled():
    """The 40%-weight insider factor and the 20% institutional factor compared a
    millions-of-shares figure against DOLLAR thresholds (10_000_000 /
    1_000_000_000), so neither could ever fire — every ticker in the app scored
    the same 4 (any buying) or 3 (any selling) on the strongest signal."""
    svc = object.__new__(OwnershipSnapshotService)
    heavy_sell = svc._compute_rating(5.0, 60.0, -20.0, False, -50.0, False)
    heavy_buy = svc._compute_rating(5.0, 60.0, 20.0, True, 50.0, True)
    assert heavy_buy > heavy_sell, "the insider/institutional factors must be live"
    assert 1 <= heavy_sell <= 5 and 1 <= heavy_buy <= 5


# ── Caching / concurrency ────────────────────────────────────────────────────

def test_in_memory_cache_is_bounded():
    """A HoldersResponse is one of the largest payloads in the codebase and the
    dict had no eviction — a crawl over thousands of symbols grew it without
    bound."""
    _cache.clear()
    try:
        for i in range(_CACHE_MAX_ENTRIES + 50):
            _cache_set(f"holders:T{i}", HoldersResponse(symbol=f"T{i}"))
        assert len(_cache) <= _CACHE_MAX_ENTRIES
        # The most recent write must survive the sweep.
        assert f"holders:T{_CACHE_MAX_ENTRIES + 49}" in _cache
    finally:
        _cache.clear()


@pytest.mark.asyncio
async def test_cancelled_build_does_not_hang_joined_waiters():
    """`CancelledError` is a BaseException, so `except Exception` never fired and
    the `_inflight` future was left unresolved: the key was popped (stopping NEW
    joiners) but every request that had ALREADY joined awaited forever, pinning a
    worker until the client timed out."""
    svc = _svc()

    async def _never(_ticker):
        await asyncio.sleep(3600)

    svc._build_holders = _never
    svc._check_supabase_cache = lambda _t: None

    first = asyncio.create_task(svc.get_holders("AAPL"))
    await asyncio.sleep(0.05)
    joiner = asyncio.create_task(svc.get_holders("AAPL"))
    await asyncio.sleep(0.05)
    first.cancel()

    with pytest.raises(Exception):
        await asyncio.wait_for(joiner, timeout=2.0)  # resolves fast, never hangs


def test_one_sided_cache_check_tolerates_missing_counts():
    """Legacy persisted rows carry no buyers/sellers counts; the staleness probe
    must not treat them as corrupt (that forced a 12-call FMP rebuild per read)."""
    hf = SmartMoneyDataSchema(
        tab="Institutions",
        flow_data=[{"month": "Q1\n'26", "buy_volume": 5.0, "sell_volume": 0.0,
                    "has_activity": True}],
        summary=SmartMoneyFlowSummarySchema(),
    )
    resp = HoldersResponse(symbol="X", hedge_funds_data=hf)
    assert HoldersService._has_one_sided_hedge_fund_data(resp) is False
