"""
Congress-vs-13F whale amount + date handling.

Guards the fix for three defects the user flagged on the Whale profile /
"Whales Sold" alert / AI sentiment:

1. Congress trades (STOCK Act) are disclosed only as RANGES — never a precise
   dollar. We keep the midpoint for internal sort/aggregation math but must
   surface an honest summed range for congress, and an exact figure for 13F.
2. Congress trades are disclosed on a 30-45 day lag, so trade groups must be
   dated by the real disclosure filing — never a "now" stamp that fabricates a
   Today / Yesterday timeline.
3. The AI sentiment must not restate a fabricated precise dollar figure (fixed
   upstream by never feeding it one for congress).

Pure math + schema parity — no network, no Supabase.
"""

import pytest

from app.services._whale_common import (
    parse_congress_amount_dollars,
    parse_congress_amount_bounds,
    sum_amount_bounds,
    format_amount_range,
    format_amount_short,
    snapshot_db_row,
    calc_13f_trade_dollars,
)
from app.services.whale_service import WhaleService
from app.services.tracking_service import _format_amount_or_range
from app.schemas.whale import WhaleTradeGroupResponse, WhaleTradeResponse
from app.schemas.tracking import WhaleTradeItemResponse


def _svc() -> WhaleService:
    # Bypass __init__ (which builds an FMP client) — we exercise only pure math.
    return WhaleService.__new__(WhaleService)


def _raw(symbol, type_, amount, tx, disc):
    return {
        "symbol": symbol,
        "type": type_,
        "amount": amount,
        "transactionDate": tx,
        "disclosureDate": disc,
        "assetDescription": f"{symbol} Corp",
    }


# ── Bounds parsing ──────────────────────────────────────────────────

def test_parse_bounds_range():
    assert parse_congress_amount_bounds("$1,001 - $15,000") == (1001.0, 15000.0)


def test_parse_bounds_over_is_open_ended():
    low, high = parse_congress_amount_bounds("Over $50,000,000")
    assert low == 50_000_000.0
    assert high is None  # open-ended top bucket, not a fabricated 1.5x


def test_parse_bounds_single_value_collapses():
    assert parse_congress_amount_bounds("100000") == (100_000.0, 100_000.0)


def test_parse_bounds_empty():
    assert parse_congress_amount_bounds("") == (0.0, 0.0)
    assert parse_congress_amount_bounds("garbage") == (0.0, 0.0)


def test_midpoint_helper_unchanged_for_internal_math():
    # The midpoint is still used for sorting / net direction — must not change.
    assert parse_congress_amount_dollars("$1,001 - $15,000") == pytest.approx(8000.5)


def test_sum_bounds_open_ended_propagates():
    low, high = sum_amount_bounds([(1001, 15000), (50_000_000, None)])
    assert low == 50_001_001.0
    assert high is None


def test_format_amount_range_variants():
    assert format_amount_range(50_000, 250_000) == "$50K – $250K"
    assert format_amount_range(8000, 8000) == "$8K"            # collapsed
    assert format_amount_range(50_000_000, None) == "$50.0M+"  # open-ended
    assert format_amount_short(455_000) == "$455K"


# ── Congress aggregation: per-disclosure groups, real dates, ranges ──

def test_congress_groups_by_disclosure_not_now():
    raw = [
        _raw("ORCL", "Sale", "$250,001 - $500,000", "2026-06-01", "2026-06-30"),
        _raw("NVDA", "Sale", "$1,001 - $15,000", "2026-06-02", "2026-06-30"),
        _raw("AAPL", "Purchase", "$15,001 - $50,000", "2026-05-01", "2026-05-15"),
    ]
    _holdings, groups, _sectors = _svc()._aggregate_congressional_trades(
        raw, as_of_date="2026-07-01"
    )
    dates = {g["date"] for g in groups}
    # One group per disclosure filing, dated by disclosure — NOT by "now".
    assert dates == {"2026-06-30", "2026-05-15"}
    assert "2026-07-01" not in dates  # no fabricated "today" stamp


def test_congress_group_carries_range_and_split_dates():
    raw = [
        _raw("ORCL", "Sale", "$250,001 - $500,000", "2026-06-01", "2026-06-30"),
        _raw("NVDA", "Sale", "$1,001 - $15,000", "2026-06-02", "2026-06-30"),
    ]
    _h, groups, _s = _svc()._aggregate_congressional_trades(raw, "2026-07-01")
    assert len(groups) == 1
    g = groups[0]
    assert g["net_action"] == "SOLD"
    # Summed honest range: (250001 + 1001) .. (500000 + 15000) = 251002 .. 515000
    assert g["net_amount_range"] == "$251K – $515K"
    assert g["disclosure_date"] == "2026-06-30"
    assert g["transaction_date"] == "2026-06-02"  # latest tx in the filing
    # Every trade preserves its raw STOCK Act bucket string.
    assert all(t["amount_range"] for t in g["trades"])


def test_13f_trade_stays_exact_no_range():
    # 13F amount is real (shares x implied price); no range surfaced.
    action, amount = calc_13f_trade_dollars(
        curr_shares=1000, curr_value=200_000,
        prev_shares=500, prev_value=90_000,
    )
    assert action == "BOUGHT"
    assert amount == pytest.approx(100_000.0)  # 500 shares x $200


# ── Group response assembly: congress vs 13F ────────────────────────

def test_assemble_group_13f_precise_no_range():
    trades = [
        WhaleTradeResponse(
            id="1", ticker="AAPL", company_name="Apple", action="BOUGHT",
            trade_type="Increased", amount=1_000_000.0, amount_range=None,
            date="2026-03-31",
        )
    ]
    tg = {"date": "2026-03-31", "trade_count": 1,
          "net_action": "BOUGHT", "net_amount": 1_000_000.0}
    resp = _svc()._assemble_group_response("g1", tg, trades)
    assert resp.net_amount_range is None
    assert resp.disclosure_date is None
    assert resp.net_amount == 1_000_000.0


def test_assemble_group_congress_derives_range_from_db_trades():
    # DB path: group row has no range/date columns → derived from the trades.
    trades = [
        WhaleTradeResponse(
            id="1", ticker="ORCL", company_name="Oracle", action="SOLD",
            trade_type="Decreased", amount=375_000.0,
            amount_range="$250,001 - $500,000",
            date="2026-06-01", disclosure_date="2026-06-30",
        )
    ]
    tg = {"date": "2026-06-30", "trade_count": 1,
          "net_action": "SOLD", "net_amount": 375_000.0}
    resp = _svc()._assemble_group_response("g2", tg, trades)
    assert resp.net_amount_range == "$250K – $500K"
    assert resp.disclosure_date == "2026-06-30"
    assert resp.transaction_date == "2026-06-01"


# ── Tracking alert roll-up: range vs single (explicit is_congress) ──

def test_tracking_all_institutional_single_figure():
    # 13F-only bucket (is_congress=False) → exact figure, no range.
    bounds = [(500_000, 500_000), (200_000, 200_000)]
    assert _format_amount_or_range(bounds, 700_000, False) == "$700K"


def test_tracking_congress_shows_range():
    bounds = [(250_001, 500_000), (1_001, 15_000)]
    label = _format_amount_or_range(bounds, 386_501, True)
    assert label == "$251K – $515K"


def test_tracking_mixed_congress_and_13f_shows_range():
    # institutional exact ($455K) + congress range → honest combined range
    bounds = [(455_000, 455_000), (250_001, 500_000)]
    label = _format_amount_or_range(bounds, 830_000, True)
    assert "–" in label  # must not collapse a mixed bucket to false precision


# BUG6 regression: congress-ness is EXPLICIT, not inferred from bound spread.
def test_tracking_congress_all_malformed_never_precise_dollar():
    # Every row's amount_range unparseable ("N/A" -> (0,0)) but is_congress=True
    # → must NOT render "$0" as if precise; render the "—" estimate sentinel.
    assert _format_amount_or_range([(0.0, 0.0)], 0.0, True) == "—"


def test_tracking_congress_single_value_marked_estimate():
    # A single-value congress bucket collapses to low==high; with is_congress it
    # must be flagged an estimate ("~$250K"), never a bare precise "$250K".
    assert _format_amount_or_range([(250_000, 250_000)], 250_000, True) == "~$250K"


def test_tracking_congress_open_ended_bucket():
    # "Over $50M" → high None propagates → "$50.0M+", never a fake ceiling.
    assert _format_amount_or_range([(50_000_000, None)], 75_000_000, True) == "$50.0M+"


def test_tracking_collapsed_bounds_but_13f_stays_exact():
    # Same collapsed bounds, but is_congress=False (real 13F point) → exact.
    assert _format_amount_or_range([(250_000, 250_000)], 250_000, False) == "$250K"


# ── BUG1: snapshot rows must never carry the phantom trade_groups column ──

def test_snapshot_db_row_strips_trade_groups():
    snap = {
        "whale_id": "w1", "filing_period": "2026-06", "trade_group": {"date": "x"},
        "trade_groups": [{"date": "x"}, {"date": "y"}], "raw_hash": "h",
    }
    row = snapshot_db_row(snap)
    assert "trade_groups" not in row          # the phantom column is gone
    assert row["trade_group"] == {"date": "x"}  # the real column survives
    assert row["raw_hash"] == "h"
    # Original dict is untouched (still needed in-memory for the DB sync).
    assert "trade_groups" in snap


def test_snapshot_db_row_noop_when_absent():
    snap = {"whale_id": "w1", "trade_group": None}
    assert snapshot_db_row(snap) == snap


# ── BUG5: trade-group endpoints route through the assembler (range/dates) ──

def test_assemble_congress_group_from_db_rows_net_direction_only():
    # Filing net SOLD: range must reflect the SOLD bounds, not the buys.
    trades = [
        WhaleTradeResponse(
            id="1", ticker="ORCL", company_name="Oracle", action="SOLD",
            trade_type="Decreased", amount=375_000.0,
            amount_range="$250,001 - $500,000",
            date="2026-06-01", disclosure_date="2026-06-30",
        ),
        WhaleTradeResponse(
            id="2", ticker="AAPL", company_name="Apple", action="BOUGHT",
            trade_type="Increased", amount=8_000.0,
            amount_range="$1,001 - $15,000",
            date="2026-06-02", disclosure_date="2026-06-30",
        ),
    ]
    tg = {"date": "2026-06-30", "trade_count": 2,
          "net_action": "SOLD", "net_amount": 367_000.0}
    resp = _svc()._assemble_group_response("g", tg, trades)
    # Only the SOLD trade contributes → "$250K – $500K", not incl. the buy.
    assert resp.net_amount_range == "$250K – $500K"
    assert resp.disclosure_date == "2026-06-30"


# ── Schema parity: new fields are optional + present ────────────────

def test_group_response_backward_compat_without_new_fields():
    old = {"id": "x", "date": "2026-03-31", "trade_count": 1,
           "net_action": "BOUGHT", "net_amount": 1000.0}
    g = WhaleTradeGroupResponse.model_validate(old)
    assert g.net_amount_range is None
    assert g.disclosure_date is None
    assert g.transaction_date is None
    # Keys must exist in the serialized shape the iOS decoder reads.
    dumped = g.model_dump()
    for k in ("net_amount_range", "disclosure_date", "transaction_date"):
        assert k in dumped


def test_group_response_with_congress_fields():
    g = WhaleTradeGroupResponse.model_validate({
        "id": "x", "date": "2026-06-30", "trade_count": 2,
        "net_action": "SOLD", "net_amount": 386_501.0,
        "net_amount_range": "$251K – $515K",
        "disclosure_date": "2026-06-30", "transaction_date": "2026-06-02",
    })
    assert g.net_amount_range == "$251K – $515K"
    assert g.transaction_date == "2026-06-02"


def test_trade_response_disclosure_date_optional():
    t = WhaleTradeResponse.model_validate({
        "id": "1", "ticker": "ORCL", "company_name": "Oracle",
        "action": "SOLD", "trade_type": "Decreased", "amount": 375_000.0,
        "date": "2026-06-01",
    })
    assert t.disclosure_date is None
    assert "disclosure_date" in t.model_dump()


# BUG3: tracking item exposes per-item bounds + is_congress so iOS can
# re-aggregate an honest RANGE after trimming to the active portfolio.
def test_whale_trade_item_bounds_backward_compat():
    old = {"ticker": "ORCL", "company_name": "Oracle", "whale_count": 1,
           "amount": "$455K", "raw_amount": 455_000.0}
    it = WhaleTradeItemResponse.model_validate(old)
    assert it.raw_amount_low is None and it.raw_amount_high is None
    assert it.is_congress is False
    dumped = it.model_dump()
    for k in ("raw_amount_low", "raw_amount_high", "is_congress"):
        assert k in dumped


def test_whale_trade_item_congress_bounds():
    it = WhaleTradeItemResponse.model_validate({
        "ticker": "ORCL", "company_name": "Oracle", "whale_count": 2,
        "amount": "$251K – $515K", "raw_amount": 386_501.0,
        "raw_amount_low": 251_002.0, "raw_amount_high": 515_000.0,
        "is_congress": True,
    })
    assert it.is_congress is True
    assert it.raw_amount_high == 515_000.0
