"""A 13F quarter is compared ONLY with the ADJACENT previous quarter (2026-10-09).

Found 2026-10-09 by a read-only run of `scripts/measure_13f_option_notional.py`: Norges Bank's
2026-Q2 was diffed with 2025-Q4. FMP's `institutional-ownership/dates` lists no 2026-Q1 —
Norges files its Q1 and Q3 books under SEC confidential treatment (`isConfidentialOmitted`, a
near-empty public table) and discloses each a year later in a 13F-HR/A — and both whale
writers took the most recent EARLIER filing as "the previous quarter"
(`whale_service._find_previous_quarter`). Six months of share changes were written to
`whale_trades` as Q2 trades: the whale profile cards, the followed-whales feed, the Home
Whale Accumulation card, smart-money pushes, the Tracking tab and the "Large whale move"
banner all read them.

Owner decision 2026-10-09 (option A — the Trillion-Dollar Club's `gap` / `first_filing`): a
quarter with no adjacent previous quarter on file writes NO trades. Its holdings are shown,
change_percent is not compared (0.0: iOS decodes a non-optional Double), the summaries say
why, and a group an older derivation stored for that quarter is DELETED — never re-created,
because `whale_trades.created_at` is the smart-money push cursor. `THIRTEEN_F_DIFF_VERSION` 4,
with the comparison basis in the hash, re-derives every fund's latest quarter once.

Hermetic: a fake FMP and an in-memory Supabase; no network.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from typing import Any, Dict, List, Optional

import pytest

import scripts.hydrate_whales as hw
from app.services import whale_service as wsvc
from app.services._whale_common import (
    COMPARISON_FIRST_FILING,
    COMPARISON_GAP,
    COMPARISON_QUARTER,
    THIRTEEN_F_DIFF_VERSION,
    select_13f_comparison,
    thirteen_f_raw_hash,
    uncompared_13f_behavior,
)
from app.services.whale_service import WhaleService

Q2_26, Q1_26, Q4_25, Q3_25 = (2026, 2), (2026, 1), (2025, 4), (2025, 3)
_ENDS = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def _dates(*yqs):
    return [{"date": f"{y}-{_ENDS[q]}", "year": y, "quarter": q} for (y, q) in yqs]


def _row(sym, value, shares):
    return {"symbol": sym, "securityName": f"{sym} Inc", "value": value, "sharesNumber": shares}


# The Norges shape, invented: AAPL +100k shares in Q2 is the only real Q2 trade. NVDA was
# bought and TSLA sold during the CONFIDENTIAL Q1, which FMP never lists.
EXTRACTS = {
    Q2_26: [_row("AAPL", 200_000_000, 1_000_000), _row("MSFT", 250_000_000, 500_000),
            _row("NVDA", 360_000_000, 2_000_000)],
    Q1_26: [_row("AAPL", 180_000_000, 900_000), _row("MSFT", 240_000_000, 500_000),
            _row("NVDA", 300_000_000, 2_000_000)],
    Q4_25: [_row("AAPL", 150_000_000, 600_000), _row("MSFT", 230_000_000, 500_000),
            _row("TSLA", 40_000_000, 100_000)],
    Q3_25: [_row("AAPL", 140_000_000, 600_000)],
}
INDUSTRY = [{"industryTitle": "ELECTRONIC COMPUTERS", "weight": 60.0},
            {"industryTitle": "PHARMACEUTICAL PREPARATIONS", "weight": 40.0}]

GAP_LIST = (Q2_26, Q4_25, Q3_25)            # no 2026-Q1
ADJACENT_LIST = (Q2_26, Q1_26, Q4_25, Q3_25)
FIRST_LIST = (Q2_26,)


class _FMP:
    def __init__(self, listed, extracts=None):
        self.listed, self.extracts = listed, extracts if extracts is not None else EXTRACTS
        self.extract_calls: List[tuple] = []
        self.request_failures = 0

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        return _dates(*self.listed)

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        self.extract_calls.append((year, quarter))
        return copy.deepcopy(self.extracts.get((year, quarter), []))

    async def get_institutional_industry_breakdown(self, cik, year=0, quarter=0):
        return copy.deepcopy(INDUSTRY)

    async def get_institutional_performance(self, cik):
        return []


class _Actions:
    """The corporate-actions seam: no split, nothing unclassified; calls recorded."""

    def __init__(self):
        self.calls: List[str] = []

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.calls.append(t)
        return []

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, *,
                                          effective_from=None, effective_to=None):
        self.calls.append(t)
        return False


# ── an in-memory Supabase ──────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, data):
        self.data = data


class _MemQuery:
    def __init__(self, sb, table):
        self.sb, self.table, self.verb, self.payload = sb, table, "select", None
        self.filters: List[tuple] = []
        self.rng: Optional[tuple] = None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def neq(self, *a, **k): return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def eq(self, col, val):
        self.filters.append((col, "eq", val))
        return self

    def in_(self, col, vals):
        self.filters.append((col, "in", list(vals)))
        return self

    def delete(self):
        self.verb = "delete"
        return self

    def update(self, payload):
        self.verb, self.payload = "update", payload
        return self

    def insert(self, payload, *a, **k):
        self.verb, self.payload = "insert", payload
        return self

    def upsert(self, payload, *a, **k):
        self.verb, self.payload = "upsert", payload
        return self

    def _match(self, row):
        for col, op, val in self.filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "in" and row.get(col) not in val:
                return False
        return True

    def execute(self):
        if (self.table, self.verb) in self.sb.fail_on:
            raise RuntimeError(f"supabase down: {self.verb} {self.table}")
        self.sb.ops.append((self.table, self.verb))
        rows = self.sb.tables.setdefault(self.table, [])
        hit = [r for r in rows if self._match(r)]
        if self.verb == "delete":
            self.sb.tables[self.table] = [r for r in rows if not self._match(r)]
            return _Resp([dict(r) for r in hit])
        if self.verb == "update":
            for r in hit:
                r.update(self.payload)
            return _Resp([dict(r) for r in hit])
        if self.verb in ("insert", "upsert"):
            if self.table in self.sb.never_written:
                raise AssertionError(f"{self.verb} on {self.table}: a row was (re-)created")
            return _Resp([{"id": f"{self.table}-1"}])
        hit.sort(key=lambda r: str(r.get("id")))
        if self.rng:
            hit = hit[self.rng[0]:self.rng[1] + 1]
        return _Resp([dict(r) for r in hit])


class _MemSB:
    def __init__(self, tables=None, *, never_written=("whale_trades", "whale_trade_groups")):
        self.tables: Dict[str, List[Dict[str, Any]]] = copy.deepcopy(tables or {})
        self.ops: List[tuple] = []
        self.fail_on: set = set()
        self.never_written = set(never_written)

    def table(self, name):
        return _MemQuery(self, name)


def _stored_state():
    """What an older derivation left: Norges' gap-diffed Q2 2026 group (g1, three trades,
    created in August) and its banner, beside rows the clear must never touch."""
    return {
        "whale_trade_groups": [
            {"id": "g1", "whale_id": "w1", "date": "2026-06-30"},
            {"id": "g0", "whale_id": "w1", "date": "2025-12-31"},
            {"id": "g2", "whale_id": "w2", "date": "2026-06-30"},
        ],
        "whale_trades": [
            {"id": f"t{i}", "trade_group_id": g, "ticker": tk, "action": a,
             "date": d, "created_at": "2026-08-13T02:00:00+00:00"}
            for i, (g, tk, a, d) in enumerate([
                ("g1", "NVDA", "BOUGHT", "2026-06-30"), ("g1", "TSLA", "SOLD", "2026-06-30"),
                ("g1", "AAPL", "BOUGHT", "2026-06-30"), ("g0", "MSFT", "BOUGHT", "2025-12-31"),
                ("g2", "NVDA", "BOUGHT", "2026-06-30"),
            ])
        ],
        "whale_alerts": [
            {"id": "a1", "whale_id": "w1", "is_active": True},
            {"id": "a0", "whale_id": "w1", "is_active": False},
            {"id": "a2", "whale_id": "w2", "is_active": True},
        ],
    }


# ── drivers ────────────────────────────────────────────────────────────────────────────


async def _sb_exec(_query, *a, **k):
    return _Resp([])


def _live(monkeypatch, listed, extracts=None):
    """Drive the live profile path. Returns (fmp, actions, snapshot, sync kwargs+args)."""
    synced: Dict[str, Any] = {}

    async def _enrich(self, holdings, need_sectors=False):
        return holdings, []

    async def _sync(self, whale_id, holdings, sectors, trade_groups, behavior, sentiment,
                    total_value, perf_data, whale=None, **kwargs):
        synced.update(trade_groups=trade_groups, behavior=behavior, sentiment=sentiment,
                      holdings=holdings, **kwargs)

    monkeypatch.setattr(WhaleService, "_enrich_from_profiles", _enrich)
    monkeypatch.setattr(WhaleService, "_sync_to_whale_tables", _sync)
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _MemSB())
    monkeypatch.setattr(wsvc, "sb_exec", _sb_exec)
    wsvc._filing_dates_cache.clear()
    svc = WhaleService.__new__(WhaleService)
    svc.fmp = _FMP(listed, extracts)
    svc.corporate_actions = _Actions()
    try:
        snap = asyncio.run(svc._process_13f_path("w1", "0001374170"))
    finally:
        wsvc._filing_dates_cache.clear()
    return svc.fmp, svc.corporate_actions, snap, synced


def _nightly(monkeypatch, listed, extracts=None):
    """Drive the hydrator's `_process_13f`. Returns (fmp, actions, raw result)."""
    monkeypatch.setattr(hw, "FMP_SEMAPHORE", asyncio.Semaphore(5))
    h = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    h.fmp = _FMP(listed, extracts)
    h.corporate_actions = _Actions()
    out = asyncio.run(h._process_13f("w1", "0001374170"))
    return h.fmp, h.corporate_actions, out


def _hydrate(monkeypatch, listed, extracts=None):
    """Drive the hydrator's `_hydrate_one` end to end; returns the snapshot `_persist` got."""
    from app.services._whale_common import AnnualReturn, RETURN_OK, SOURCE_STOCK

    monkeypatch.setattr(hw, "FMP_SEMAPHORE", asyncio.Semaphore(5))
    h = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    h.fmp = _FMP(listed, extracts)
    h.corporate_actions = _Actions()
    h.sb = _MemSB()
    h.force, h.dry_run = False, False
    h.stats = {"processed": 0, "skipped": 0, "failed": 0, "errors": 0, "no_data": 0,
               "upstream_failed": 0}
    persisted: Dict[str, Any] = {}

    async def _logos(self, holdings, existing):
        return holdings, {}

    async def _ytd(self, whale_id, total, perf, whale=None):
        return AnnualReturn(value=1.0, window_years=1, source=SOURCE_STOCK, status=RETURN_OK)

    async def _persist(self, whale_id, snapshot, annual_return, risk_profile=None,
                       associated_ticker=None, *, prune_stale_trades=False):
        persisted.update(snapshot=snapshot, prune_stale_trades=prune_stale_trades)

    async def _ai(self, *a, **k):
        persisted["ai_called"] = True
        return {"action": "Accumulating", "primaryFocus": "x", "secondaryAction": "y",
                "secondaryFocus": "z"}, "model text"

    monkeypatch.setattr(hw.WhaleHydrator, "_enrich_logos", _logos)
    monkeypatch.setattr(hw.WhaleHydrator, "_compute_ytd_return", _ytd)
    monkeypatch.setattr(hw.WhaleHydrator, "_persist", _persist)
    monkeypatch.setattr(hw.WhaleHydrator, "_generate_ai_summaries", _ai)
    whale = {"id": "w1", "name": "Norges Bank", "data_source": "13f", "cik": "0001374170"}
    asyncio.run(h._hydrate_one(whale))
    return h, persisted


def _trades(group):
    return sorted((t["ticker"], t["action"], t["trade_type"]) for t in (group or {}).get("trades", []))


# ── 1. The selector ────────────────────────────────────────────────────────────────────


def test_the_adjacent_quarter_listed_is_compared():
    s = select_13f_comparison(_dates(*ADJACENT_LIST))
    assert (s.latest, s.latest_date, s.comparison, s.previous, s.older) == \
        (Q2_26, "2026-06-30", COMPARISON_QUARTER, Q1_26, Q1_26)
    assert s.compared and s.period == "2026-Q2" and s.basis == "quarter:2026-Q1"


def test_the_norges_shape_is_a_gap_and_is_not_compared():
    s = select_13f_comparison(_dates(*GAP_LIST))
    assert (s.comparison, s.previous, s.older, s.adjacent) == (COMPARISON_GAP, None, Q4_25, Q1_26)
    assert not s.compared and s.basis == "gap"


def test_a_lone_quarter_is_a_first_filing():
    s = select_13f_comparison(_dates(*FIRST_LIST))
    assert (s.comparison, s.previous, s.older, s.compared, s.basis) == \
        (COMPARISON_FIRST_FILING, None, None, False, "first_filing")


def test_the_newest_quarter_decides_whatever_fmps_list_order():
    # The writers used to trust `dates[0]` and take the FIRST earlier entry in list order.
    s = select_13f_comparison(_dates(Q3_25, Q2_26, Q1_26))
    assert (s.latest, s.comparison, s.previous) == (Q2_26, COMPARISON_QUARTER, Q1_26)
    s = select_13f_comparison(_dates(Q2_26, Q3_25, Q4_25))
    assert (s.comparison, s.older) == (COMPARISON_GAP, Q4_25)      # never 2025-Q3


def test_unusable_rows_are_skipped_and_counted_and_a_date_names_a_quarter():
    rows = _dates(Q2_26) + [
        "junk", None, {"year": True, "quarter": 1},                 # a bool is not a year
        {"year": 2026, "quarter": 5},                                # no such quarter, no date
        {"date": "2026-03-31", "year": 2026, "quarter": None},      # → 2026-Q1 from its date
    ]
    s = select_13f_comparison(rows)
    assert (s.comparison, s.previous, s.skipped_rows) == (COMPARISON_QUARTER, Q1_26, 4)
    assert select_13f_comparison([]) is None
    assert select_13f_comparison(None) is None
    # A quarter that has not ended cannot have a 13F: under "newest wins" one bogus row would
    # otherwise stand in for the real latest filing (and freeze the whale on an empty extract).
    s = select_13f_comparison(_dates(Q2_26, Q1_26) + [{"year": 2099, "quarter": 1}])
    assert (s.latest, s.comparison, s.skipped_rows) == (Q2_26, COMPARISON_QUARTER, 1)
    from datetime import date
    on = lambda d: select_13f_comparison(_dates((2026, 3), Q2_26), today=d)   # noqa: E731
    assert on(date(2026, 9, 30)).latest == (2026, 3)        # the quarter's last day counts
    assert on(date(2026, 9, 29)).latest == Q2_26            # one day before, it has not ended
    assert on(date(2026, 9, 29)).skipped_rows == 1
    assert select_13f_comparison({"error": "x"}) is None
    assert select_13f_comparison(["junk", {"date": "not-a-date"}]) is None


def test_a_dateless_latest_row_dates_the_real_quarter_end():
    # The writers' fallback built `{year}-{q*3:02d}-30`: 03-30 / 12-30 — days nobody filed.
    assert select_13f_comparison([{"year": 2025, "quarter": 4}]).latest_date == "2025-12-31"
    assert select_13f_comparison([{"year": 2026, "quarter": 1}]).latest_date == "2026-03-31"
    # A repeated quarter keeps its first listed date.
    rows = [{"date": "2026-06-30", "year": 2026, "quarter": 2},
            {"date": "2026-06-29", "year": 2026, "quarter": 2}]
    assert select_13f_comparison(rows).latest_date == "2026-06-30"
    # A date that does not parse never becomes a group's key (the quarter still counts).
    for junk in ("garbage", "2026-6-30", 20260630):
        s = select_13f_comparison([{"date": junk, "year": 2026, "quarter": 2}])
        assert (s.latest, s.latest_date, s.skipped_rows) == (Q2_26, "2026-06-30", 0)


def test_find_previous_quarter_returns_the_adjacent_entry_only():
    assert wsvc._find_previous_quarter(_dates(*GAP_LIST), 2026, 2) is None
    assert wsvc._find_previous_quarter(_dates(*ADJACENT_LIST), 2026, 2)["date"] == "2026-03-31"
    assert wsvc._find_previous_quarter(_dates(Q1_26, Q4_25), 2026, 1)["quarter"] == 4
    dated_only = wsvc._find_previous_quarter([{"date": "2026-03-31"}], 2026, 2)
    assert (dated_only["year"], dated_only["quarter"]) == (2026, 1)
    assert wsvc._find_previous_quarter(None, 2026, 2) is None


# ── 2. The live profile path ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("listed, note", [
    (GAP_LIST, "no 13F holdings are on file for Q1 2026, the quarter before"),
    (FIRST_LIST, "this is the first 13F on file"),
])
def test_the_live_path_writes_no_trades_for_a_quarter_it_cannot_compare(monkeypatch, listed, note):
    fmp, actions, snap, synced = _live(monkeypatch, listed)
    assert fmp.extract_calls == [Q2_26]                 # neither 2025-Q4 nor the unlisted 2026-Q1
    assert actions.calls == []                          # no diff → no split lookups
    assert snap["trade_group"] is None
    assert synced["trade_groups"] == []
    assert synced["clear_13f_group_date"] == "2026-06-30"
    # Holdings shown; nothing compared.
    assert [h["ticker"] for h in snap["holdings_data"]] == ["NVDA", "MSFT", "AAPL"]
    assert {h["change_percent"] for h in snap["holdings_data"]} == {0.0}
    # Summaries say why — never "no significant changes", which is a comparison claim.
    assert snap["sentiment_text"].endswith(f"Changes since the previous quarter are not shown: {note}.")
    assert "no significant changes" not in snap["sentiment_text"]
    assert snap["behavior_summary"]["action"] == "Holding"
    assert snap["behavior_summary"]["secondaryAction"] == "Concentrated in"
    assert snap["raw_hash"] is not None                 # a final answer, not a degraded one


def test_the_live_path_still_diffs_the_adjacent_quarter(monkeypatch):
    """Anti-vacuity: the fixture DOES produce trades when the adjacent quarter is listed —
    and they are the real Q2 trade only, not six months of changes."""
    fmp, _actions, snap, synced = _live(monkeypatch, ADJACENT_LIST)
    assert sorted(fmp.extract_calls) == [Q1_26, Q2_26]
    assert _trades(snap["trade_group"]) == [("AAPL", "BOUGHT", "Increased")]
    assert synced["clear_13f_group_date"] is None
    assert "not shown" not in snap["sentiment_text"]


def test_today_s_gap_diff_would_have_booked_six_months_as_q2(monkeypatch):
    """The defect, pinned from the other side: diffing 2025-Q4 (the old pick) books NVDA's
    Q1 purchase and TSLA's exit as Q2 trades. The writers now never make that call."""
    svc = WhaleService.__new__(WhaleService)
    total = sum(r["value"] for r in EXTRACTS[Q2_26])
    across = svc._diff_quarters(EXTRACTS[Q2_26], EXTRACTS[Q4_25], "2026-06-30", total)
    assert {("NVDA", "BOUGHT", "New"), ("TSLA", "SOLD", "Closed")} <= set(_trades(across))


def test_an_adjacent_quarter_with_no_change_clears_a_stale_group(monkeypatch):
    # Compared, nothing moved: no group is written, so a group an older derivation stored
    # for this quarter must not survive either — and the texts may say "no changes" here.
    same = dict(EXTRACTS)
    same[Q1_26] = copy.deepcopy(EXTRACTS[Q2_26])
    _fmp, _a, snap, synced = _live(monkeypatch, ADJACENT_LIST, same)
    assert snap["trade_group"] is None and synced["trade_groups"] == []
    assert synced["clear_13f_group_date"] == "2026-06-30"
    assert "no significant changes" in snap["sentiment_text"]


def test_a_failed_adjacent_fetch_still_refuses_rather_than_booking_a_whole_book(monkeypatch):
    # The adjacent quarter is LISTED but its extract comes back empty: an outage, not "no
    # comparison". Both writers keep refusing (the live caller then serves the stored
    # snapshot; the sweep skips the whale) — the rule change must not turn it into a gap.
    broken = {k: v for k, v in EXTRACTS.items() if k != Q1_26}
    with pytest.raises(RuntimeError, match="previous-quarter fetch returned empty"):
        _live(monkeypatch, ADJACENT_LIST, broken)
    _fmp, _a, out = _nightly(monkeypatch, ADJACENT_LIST, broken)
    assert out is None


# ── 3. The nightly sweep ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("listed", [GAP_LIST, FIRST_LIST])
def test_the_nightly_sweep_writes_no_trades_for_a_quarter_it_cannot_compare(monkeypatch, listed):
    fmp, actions, out = _nightly(monkeypatch, listed)
    assert fmp.extract_calls == [Q2_26] and actions.calls == []
    assert out["trade_group"] is None and out["prev_holdings"] == []
    assert out["uncompared"] is not None and not out["uncompared"].compared
    assert out["filing_period"] == "2026-Q2" and out["filing_date"] == "2026-06-30"


@pytest.mark.parametrize("listed", [GAP_LIST, FIRST_LIST])
def test_hydrate_one_neither_compares_change_nor_asks_the_model(monkeypatch, listed):
    h, persisted = _hydrate(monkeypatch, listed)
    snap = persisted["snapshot"]
    assert "ai_called" not in persisted                  # no model call for a non-comparison
    # An empty previous book made every change its whole weight ("+40.7%" on everything);
    # the live path leaves 0.0 — now both do.
    assert {x["change_percent"] for x in snap["holdings_data"]} == {0.0}
    assert snap["trade_group"] is None and snap["trade_groups"] == []
    assert "Changes since the previous quarter are not shown" in snap["sentiment_text"]
    assert persisted["prune_stale_trades"] is True       # 13F: its `_persist` may clear
    assert h.stats["processed"] == 1


def test_hydrate_one_still_compares_an_adjacent_quarter(monkeypatch):
    _h, persisted = _hydrate(monkeypatch, ADJACENT_LIST)
    snap = persisted["snapshot"]
    assert persisted.get("ai_called") is True
    assert _trades(snap["trade_group"]) == [("AAPL", "BOUGHT", "Increased")]
    assert {x["change_percent"] for x in snap["holdings_data"]} != {0.0}


# ── 4. Both writers agree ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("listed", [GAP_LIST, FIRST_LIST, ADJACENT_LIST])
def test_both_writers_store_the_same_answer(monkeypatch, listed):
    _f, _a, live, synced = _live(monkeypatch, listed)
    _f, _a, nightly = _nightly(monkeypatch, listed)
    _h, persisted = _hydrate(monkeypatch, listed)
    snap = persisted["snapshot"]
    assert live["raw_hash"] == nightly["raw_hash"] == snap["raw_hash"]
    assert _trades(live["trade_group"]) == _trades(nightly["trade_group"])
    assert [(x["ticker"], x["change_percent"]) for x in live["holdings_data"]] == \
        [(x["ticker"], x["change_percent"]) for x in snap["holdings_data"]]
    if listed is not ADJACENT_LIST:
        assert live["sentiment_text"] == snap["sentiment_text"]
        assert live["behavior_summary"] == snap["behavior_summary"]
        # Anti-vacuity: both read a real top sector, not the empty-list fallback.
        assert live["behavior_summary"]["secondaryFocus"] != "various sectors"
        assert "various sectors" not in live["sentiment_text"]


# ── 5. The hash ────────────────────────────────────────────────────────────────────────


def test_the_comparison_basis_is_part_of_the_hash():
    """FMP listing 2026-Q1 LATER (a confidential book disclosed while Q2 is still the latest)
    must re-derive: the hydrator skips only on an equal hash, so with the extract alone in
    it the gap answer would stick until the next filing."""
    curr = EXTRACTS[Q2_26]
    gap = thirteen_f_raw_hash(curr, basis="gap", previous_raw=[])
    first = thirteen_f_raw_hash(curr, basis="first_filing", previous_raw=[])
    quarter = thirteen_f_raw_hash(curr, basis="quarter:2026-Q1", previous_raw=EXTRACTS[Q1_26])
    assert len({gap, first, quarter}) == 3


def test_the_previous_positions_are_part_of_the_hash_but_their_row_order_is_not():
    curr, prev = EXTRACTS[Q2_26], EXTRACTS[Q1_26]
    base = thirteen_f_raw_hash(curr, basis="quarter:2026-Q1", previous_raw=prev)
    assert base == thirteen_f_raw_hash(curr, basis="quarter:2026-Q1", previous_raw=prev[::-1])
    noisy = [dict(r, industryTitle="X") for r in prev]           # a field the diff never reads
    assert base == thirteen_f_raw_hash(curr, basis="quarter:2026-Q1", previous_raw=noisy)
    restated = copy.deepcopy(prev)
    restated[0]["sharesNumber"] += 1_000                          # a 13F-HR/A moved a position
    assert base != thirteen_f_raw_hash(curr, basis="quarter:2026-Q1", previous_raw=restated)


def test_the_single_argument_hash_is_the_extract_and_the_version_only():
    raw = EXTRACTS[Q2_26]
    payload = json.dumps(raw, sort_keys=True, default=str)
    expected = hashlib.sha256(f"13f-diff-v{THIRTEEN_F_DIFF_VERSION}\n{payload}".encode()).hexdigest()
    assert thirteen_f_raw_hash(raw) == expected


def test_the_rule_change_bumped_the_derivation_version():
    # Every stored latest quarter was hashed under v3 or older: the bump makes the nightly
    # sweep re-derive each one once — Norges' gap group included.
    assert THIRTEEN_F_DIFF_VERSION >= 4


# ── 6. Clearing a group an older derivation stored ─────────────────────────────────────


def test_the_clear_deletes_the_quarters_group_its_trades_and_the_banner_only():
    sb = _MemSB(_stored_state())
    assert wsvc._clear_13f_trade_group(sb, "w1", "2026-06-30") == (1, 3)
    assert {g["id"] for g in sb.tables["whale_trade_groups"]} == {"g0", "g2"}
    assert {t["id"] for t in sb.tables["whale_trades"]} == {"t3", "t4"}
    assert {a["id"]: a["is_active"] for a in sb.tables["whale_alerts"]} == \
        {"a1": False, "a0": False, "a2": True}
    writes = [op for op in sb.ops if op[1] != "select"]
    # Trades before their group (a failure in between leaves an empty group, never orphans);
    # delete and the banner update only — no row is ever (re-)created.
    assert writes.index(("whale_trades", "delete")) < writes.index(("whale_trade_groups", "delete"))
    assert {verb for _t, verb in writes} == {"delete", "update"}


def test_the_clear_is_a_no_op_without_a_stored_group_and_never_wipes_on_blank_keys():
    sb = _MemSB(_stored_state())
    assert wsvc._clear_13f_trade_group(sb, "w1", "2024-03-31") == (0, 0)
    assert all(verb == "select" for _t, verb in sb.ops)          # banner untouched too
    for whale_id, date in (("", "2026-06-30"), ("w1", ""), (None, None)):
        sb = _MemSB(_stored_state())
        assert wsvc._clear_13f_trade_group(sb, whale_id, date) == (0, 0)
        assert sb.ops == []


def test_a_clear_failure_raises_to_its_caller():
    sb = _MemSB(_stored_state())
    sb.fail_on.add(("whale_trades", "delete"))
    with pytest.raises(RuntimeError):
        wsvc._clear_13f_trade_group(sb, "w1", "2026-06-30")
    assert {g["id"] for g in sb.tables["whale_trade_groups"]} == {"g0", "g1", "g2"}   # group kept


def _live_sync(monkeypatch, sb, **kwargs):
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)
    asyncio.run(WhaleService.__new__(WhaleService)._sync_to_whale_tables(
        "w1", [], [], [], uncompared_13f_behavior([]), "", 1.0, [], **kwargs,
    ))


def test_the_live_sync_clears_only_when_asked(monkeypatch):
    sb = _MemSB(_stored_state())
    _live_sync(monkeypatch, sb, prune_stale_trades=True, clear_13f_group_date="2026-06-30")
    assert {g["id"] for g in sb.tables["whale_trade_groups"]} == {"g0", "g2"}
    sb = _MemSB(_stored_state())
    _live_sync(monkeypatch, sb, prune_stale_trades=True)
    assert len(sb.tables["whale_trade_groups"]) == 3 and len(sb.tables["whale_trades"]) == 5


def test_a_live_clear_failure_is_logged_not_raised(monkeypatch, caplog):
    import logging

    sb = _MemSB(_stored_state())
    sb.fail_on.add(("whale_trades", "delete"))
    with caplog.at_level(logging.WARNING, logger="app.services.whale_service"):
        _live_sync(monkeypatch, sb, prune_stale_trades=True, clear_13f_group_date="2026-06-30")
    assert any("stale 13F trade group clear failed" in r.getMessage() for r in caplog.records)


def _hydrator_persist(monkeypatch, sb, *, prune, groups=()):
    from types import SimpleNamespace
    from app.services._whale_common import AnnualReturn, RETURN_OK, SOURCE_STOCK
    from app.utils import supabase_errors as se

    h = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    h.sb = sb
    h.fmp = SimpleNamespace(request_failures=0)
    monkeypatch.setattr(hw.WhaleHydrator, "_maybe_generate_alert", lambda self, *a, **k: None)
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    snapshot = {
        "whale_id": "w1", "filing_period": "2026-Q2", "filing_date": "2026-06-30",
        "total_value": 1.0, "behavior_summary": {}, "sentiment_text": "", "raw_hash": "h",
        "holdings_data": [], "sector_data": [], "trade_group": None, "trade_groups": list(groups),
    }
    ret = AnnualReturn(value=1.0, window_years=1, source=SOURCE_STOCK, status=RETURN_OK)
    asyncio.run(h._persist("w1", snapshot, ret, prune_stale_trades=prune))
    return sb


def test_the_hydrator_clears_a_13f_quarter_with_no_group(monkeypatch):
    sb = _hydrator_persist(monkeypatch, _MemSB(_stored_state()), prune=True)
    assert {g["id"] for g in sb.tables["whale_trade_groups"]} == {"g0", "g2"}
    assert {t["id"] for t in sb.tables["whale_trades"]} == {"t3", "t4"}
    assert ("whale_filing_snapshots", "update") not in sb.ops      # nothing failed: hash kept


def test_the_hydrator_never_clears_for_congress(monkeypatch):
    sb = _hydrator_persist(monkeypatch, _MemSB(_stored_state()), prune=False)
    assert len(sb.tables["whale_trade_groups"]) == 3 and len(sb.tables["whale_trades"]) == 5


def test_the_hydrator_does_not_clear_a_quarter_that_wrote_a_group(monkeypatch):
    group = {"date": "2026-06-30", "trade_count": 1, "net_action": "BOUGHT", "net_amount": 1.0,
             "trades": [{"ticker": "NVDA", "action": "BOUGHT", "trade_type": "New",
                         "amount": 1.0, "date": "2026-06-30"}]}
    calls = []
    monkeypatch.setattr(hw, "_clear_13f_trade_group", lambda *a: calls.append(a) or (0, 0))
    monkeypatch.setattr(hw.WhaleHydrator, "_upsert_trades", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(hw, "_prune_stale_13f_trades", lambda *a, **k: 0)
    _hydrator_persist(monkeypatch, _MemSB(_stored_state(), never_written=()), prune=True,
                      groups=[group])
    assert calls == []


def test_a_failed_hydrator_clear_resets_the_hash_so_the_next_run_retries(monkeypatch):
    sb = _MemSB(_stored_state())
    sb.fail_on.add(("whale_trades", "delete"))
    sb = _hydrator_persist(monkeypatch, sb, prune=True)
    assert ("whale_filing_snapshots", "update") in sb.ops          # 5b: raw_hash → NULL
