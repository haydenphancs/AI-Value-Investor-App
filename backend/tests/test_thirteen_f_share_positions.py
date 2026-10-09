"""13F share positions, the stale-trade prune and the versioned raw hash (2026-10-09).

Both 13F diff writers (`whale_service._diff_quarters`, `hydrate_whales._diff_quarters`)
keyed FMP's extract rows by symbol with the LAST row winning. One stock arrives as several
rows — put/call option rows carrying the underlying's share count, a PRN (principal) line,
the same CUSIP once per manager, a 13F-HR/A next to the original — so a fund's put
position or one manager's slice overwrote the stock's own share count and the "trade" was
the gap between two unrelated rows. That fed `whale_trades`, the whale profile trade cards
and the Home Whale Accumulation card. Both writers now read
`_whale_common.thirteen_f_share_positions`.

Re-derivations upserted on (trade_group_id, ticker, action, date) and never pruned, so a
quarter re-derived with a different answer kept its old rows (even a BOUGHT next to the
new SOLD). 13F groups are now pruned after the upsert — without re-creating surviving rows,
because `smart_money_sender` reads `whale_trades` by `created_at`.

`thirteen_f_raw_hash` folds `THIRTEEN_F_DIFF_VERSION` into the snapshot hash, so a diff fix
re-derives every fund's latest quarter in the next nightly sweep.

The HOLDINGS read the same positions since v3 (§7, owner decision 2026-10-09): both builders
summed every row per symbol, so a put or call added its UNDERLYING's notional to the "13F
Equity Portfolio" figure, the holdings, their allocation and change_percent, and the diffs'
allocation denominators.

Hermetic: plain dicts and in-memory fakes; no Supabase, no FMP.
"""

import asyncio
import hashlib
import json
import math
import re
from pathlib import Path

import pytest

import scripts.hydrate_whales as hw
from app.services import _whale_common as wc
from app.services import whale_service as wsvc
from app.services._whale_common import (
    THIRTEEN_F_DIFF_VERSION,
    is_13f_non_share_row,
    thirteen_f_holdings,
    thirteen_f_raw_hash,
    thirteen_f_share_positions,
)
from app.services.whale_service import WhaleService, _prune_stale_13f_trades

_BACKEND = Path(__file__).resolve().parents[1]


def _row(sym, shares, value, *, cusip=None, put_call=None, shares_type=None, link=None,
         filed=None, name=None):
    r = {"symbol": sym, "sharesNumber": shares, "value": value}
    if cusip is not None:
        r["securityCusip"] = cusip
    if put_call is not None:
        r["putCallShare"] = put_call
    if shares_type is not None:
        r["sharesType"] = shares_type
    if link is not None:
        r["link"] = link
    if filed is not None:
        r["filingDate"] = filed
    if name is not None:
        r["securityName"] = name
    return r


_NVDA_CUSIP = "67066G104"
_ORIG = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm"
_AMEND = "https://www.sec.gov/Archives/edgar/data/1/000000000126000009/0000000001-26-000009-index.htm"


# ── 1. thirteen_f_share_positions ───────────────────────────────────────────────


def test_option_and_principal_rows_are_not_shares_owned():
    pos = thirteen_f_share_positions([
        _row("NVDA", 1_000, 180_000, put_call="Share", shares_type="SH"),
        _row("NVDA", 50_000, 9_000_000, put_call="Put"),        # notional of the underlying
        _row("NVDA", 20_000, 3_600_000, put_call="CALL"),       # case-insensitive
        _row("XYZ", 5_000_000, 5_000_000, shares_type="PRN"),   # a bond's principal
    ])
    assert pos == {"NVDA": {"symbol": "NVDA", "name": "NVDA", "value": 180_000.0, "shares": 1_000}}


def test_a_missing_type_is_read_as_shares_exactly_as_before():
    # Older rows and every existing diff fixture carry neither field: dropping them would
    # turn every such position into an exit.
    assert thirteen_f_share_positions([_row("AAPL", 10, 2_000)])["AAPL"]["shares"] == 10
    assert is_13f_non_share_row({}) is False
    assert is_13f_non_share_row({"putCallShare": "", "sharesType": ""}) is False
    assert is_13f_non_share_row({"sharesType": " sh "}) is False
    assert is_13f_non_share_row({"putCallShare": " put "}) is True
    assert is_13f_non_share_row({"sharesType": "PRN"}) is True


def test_one_position_reported_per_manager_is_summed():
    # A multi-manager filer lists one CUSIP once per manager / discretion.
    pos = thirteen_f_share_positions([
        _row("AAPL", 100, 20_000, cusip="037833100", link=_ORIG),
        _row("AAPL", 250, 50_000, cusip="037833100", link=_ORIG),
        _row("AAPL", 50, 10_000, cusip="037833100", link=_ORIG),
    ])
    assert pos["AAPL"]["shares"] == 400 and pos["AAPL"]["value"] == 80_000.0


def test_two_cusips_resolving_to_one_symbol_are_summed():
    pos = thirteen_f_share_positions([
        _row("GOOGL", 100, 15_000, cusip="02079K305"),
        _row("GOOGL", 40, 6_000, cusip="02079K107"),
    ])
    assert pos["GOOGL"]["shares"] == 140


def test_an_amendment_replaces_its_original_per_position_and_adds_new_ones():
    pos = thirteen_f_share_positions([
        _row("NVDA", 1_000, 180_000, cusip=_NVDA_CUSIP, link=_ORIG, filed="2026-05-14"),
        _row("NVDA", 1_200, 216_000, cusip=_NVDA_CUSIP, link=_AMEND, filed="2026-05-20"),   # restated
        _row("TSM", 500, 90_000, cusip="874039100", link=_AMEND, filed="2026-05-20"),     # added (CT)
    ])
    assert pos["NVDA"]["shares"] == 1_200
    assert pos["TSM"]["shares"] == 500
    # The answer does not depend on the order FMP returned the rows in.
    rev = thirteen_f_share_positions([
        _row("TSM", 500, 90_000, cusip="874039100", link=_AMEND, filed="2026-05-20"),
        _row("NVDA", 1_200, 216_000, cusip=_NVDA_CUSIP, link=_AMEND, filed="2026-05-20"),
        _row("NVDA", 1_000, 180_000, cusip=_NVDA_CUSIP, link=_ORIG, filed="2026-05-14"),
    ])
    assert rev == pos


def test_malformed_rows_degrade_and_never_raise():
    pos = thirteen_f_share_positions([
        "x", None, 7, {},
        {"symbol": "--", "sharesNumber": 1, "value": 1},
        {"symbol": "", "tickercusip": "", "sharesNumber": 1, "value": 1},
        {"tickercusip": "abc", "shares": 3, "value": 30},                  # tickercusip fallback
        _row("NAN", float("nan"), float("inf")),
        _row("TXT", "12", "1,000"),                                         # unparseable value → 0
        {"symbol": " msft ", "sharesNumber": 2, "value": 800, "companyName": "Microsoft"},
    ])
    assert pos["ABC"] == {"symbol": "ABC", "name": "ABC", "value": 30.0, "shares": 3}
    assert pos["NAN"]["shares"] == 0 and pos["NAN"]["value"] == 0.0
    assert pos["TXT"]["shares"] == 12 and pos["TXT"]["value"] == 0.0
    assert pos["MSFT"]["name"] == "Microsoft"
    assert set(pos) == {"ABC", "NAN", "TXT", "MSFT"}
    assert thirteen_f_share_positions(None) == {} and thirteen_f_share_positions("rows") == {}


# ── 2. Both diff writers read it (and agree) ────────────────────────────────────


def _both_diffs(curr, prev, total):
    svc = WhaleService.__new__(WhaleService)
    hyd = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    return (svc._diff_quarters(curr, prev, "2026-06-30", total),
            hyd._diff_quarters(curr, prev, "2026-06-30", total))


def _trades_by_ticker(group):
    return {t["ticker"]: t for t in (group or {}).get("trades", [])}


def test_a_put_position_change_is_not_a_stock_trade_in_either_writer():
    # 1,000,000 NVDA shares held unchanged at $100; the fund's PUTS doubled. Last-row-wins
    # read the put row as the stock: 50,000 → 100,000 "shares" … then compared it with the
    # previous quarter's share row and booked a huge sale or purchase.
    prev = [_row("NVDA", 1_000_000, 100_000_000, put_call="Share"),
            _row("NVDA", 50_000, 5_000_000, put_call="Put")]
    curr = [_row("NVDA", 1_000_000, 100_000_000, put_call="Share"),
            _row("NVDA", 100_000, 10_000_000, put_call="Put")]
    for group in _both_diffs(curr, prev, 110_000_000):
        assert "NVDA" not in _trades_by_ticker(group)


def test_per_manager_rows_diff_as_one_position_in_either_writer():
    # Three managers held 100 + 200 + 300 = 600 shares; next quarter 150 + 250 + 400 = 800.
    prev = [_row("AAPL", n, n * 100.0, cusip="037833100") for n in (100, 200, 300)]
    curr = [_row("AAPL", n, n * 100.0, cusip="037833100") for n in (150, 250, 400)]
    for group in _both_diffs(curr, prev, 80_000.0):
        t = _trades_by_ticker(group)["AAPL"]
        assert t["action"] == "BOUGHT" and t["trade_type"] == "Increased"
        assert math.isclose(t["amount"], 200 * 100.0)          # +200 shares × $100, not 400-300


def test_both_writers_produce_identical_trades_for_one_filing():
    prev = [_row("NVDA", 1_000, 100_000), _row("NVDA", 900, 90_000, put_call="Call"),
            _row("MSFT", 500, 200_000), _row("OLD", 10, 5_000)]
    curr = [_row("NVDA", 1_500, 180_000), _row("NVDA", 100, 12_000, put_call="Call"),
            _row("MSFT", 400, 170_000), _row("NEW", 300, 60_000)]
    svc_group, hyd_group = _both_diffs(curr, prev, 422_000.0)
    pick = lambda g: sorted((t["ticker"], t["action"], t["trade_type"], round(t["amount"], 2))
                            for t in g["trades"])
    assert pick(svc_group) == pick(hyd_group)
    assert {(t, a) for t, a, _, _ in pick(svc_group)} == {
        ("NVDA", "BOUGHT"), ("MSFT", "SOLD"), ("OLD", "SOLD"), ("NEW", "BOUGHT"),
    }


def test_both_writers_delegate_to_the_shared_normaliser():
    # Comments stripped: the explanatory notes name the old last-row-wins loop.
    for path, fn in ((_BACKEND / "app/services/whale_service.py", "def _diff_quarters("),
                     (_BACKEND / "scripts/hydrate_whales.py", "def _diff_quarters(")):
        code = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))
        body = code[code.index(fn):]
        body = body[:body.index("\n    def ", 10)]
        assert "current_map = thirteen_f_share_positions(current_raw)" in body, path.name
        assert "prev_map = thirteen_f_share_positions(previous_raw)" in body, path.name
        assert "current_map[sym] =" not in body and "prev_map[sym] =" not in body, path.name


# ── 3. The stale-trade prune ────────────────────────────────────────────────────


class _Resp:
    def __init__(self, data):
        self.data = data


class _TradesQuery:
    """`whale_trades` in memory: select/eq/order/range/execute and delete/in_/execute."""

    def __init__(self, store):
        self._store, self._eq, self._ids, self._delete, self._range = store, {}, None, False, None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def in_(self, col, vals):
        assert col == "id"
        self._ids = list(vals)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def delete(self):
        self._delete = True
        return self

    def execute(self):
        if self._store.fail:
            raise RuntimeError("supabase down")
        if self._delete:
            self._store.delete_calls.append(list(self._ids))
            self._store.rows = [r for r in self._store.rows if r["id"] not in self._ids]
            return _Resp([])
        rows = [r for r in self._store.rows
                if all(r.get(c) == v for c, v in self._eq.items())]
        rows.sort(key=lambda r: r["id"])
        if self._range:
            rows = rows[self._range[0]:self._range[1] + 1]
        return _Resp([dict(r) for r in rows])


class _TradesStore:
    def __init__(self, rows):
        self.rows, self.delete_calls, self.fail = rows, [], False

    def table(self, name):
        assert name == "whale_trades"
        return _TradesQuery(self)


def _stored(i, ticker, action="BOUGHT", date="2026-06-30", group="g1"):
    return {"id": f"id{i:03d}", "trade_group_id": group, "ticker": ticker, "action": action,
            "date": date, "created_at": f"2026-08-0{1 + i % 9}"}


def test_prune_deletes_only_what_the_new_derivation_dropped():
    store = _TradesStore([
        _stored(1, "NVDA"),                    # kept: same key
        _stored(2, "AAPL"),                    # stale: no longer derived
        _stored(3, "MSFT", action="BOUGHT"),   # stale: the derivation now says SOLD
        _stored(4, "TSM", group="g2"),         # another group: never touched
    ])
    new_rows = [{"ticker": "NVDA", "action": "BOUGHT", "date": "2026-06-30"},
                {"ticker": "MSFT", "action": "SOLD", "date": "2026-06-30"}]
    assert _prune_stale_13f_trades(store, "g1", new_rows) == 2
    assert {r["id"] for r in store.rows} == {"id001", "id004"}
    # The surviving row is the ORIGINAL (same id, same created_at): never re-created, so the
    # smart-money cursor (`created_at > cursor`) does not see it again.
    assert next(r for r in store.rows if r["id"] == "id001")["created_at"] == "2026-08-02"


def test_prune_is_a_no_op_when_nothing_is_stale_and_batches_large_deletes():
    store = _TradesStore([_stored(1, "NVDA")])
    assert _prune_stale_13f_trades(store, "g1", [{"ticker": "NVDA", "action": "BOUGHT",
                                                  "date": "2026-06-30"}]) == 0
    assert store.delete_calls == []
    assert _prune_stale_13f_trades(store, "", []) == 0               # no group id: never a wipe
    big = _TradesStore([_stored(i, f"T{i}") for i in range(250)])
    assert _prune_stale_13f_trades(big, "g1", []) == 250
    assert [len(c) for c in big.delete_calls] == [100, 100, 50] and big.rows == []


def test_a_prune_failure_raises_to_its_caller():
    store = _TradesStore([_stored(1, "NVDA")])
    store.fail = True
    with pytest.raises(RuntimeError):
        _prune_stale_13f_trades(store, "g1", [])


# ── 4. Who prunes: 13F groups only ──────────────────────────────────────────────


class _SyncQuery:
    def __init__(self, sb, table):
        self._sb, self._table = sb, table

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        return _Resp([{"id": f"{self._table}-1"}])


class _SyncSB:
    def table(self, name):
        return _SyncQuery(self, name)


def _live_sync(monkeypatch, *, prune, prune_impl=None):
    calls = []
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _SyncSB())
    monkeypatch.setattr(wsvc, "_bulk_write_trades", lambda sb, rows: calls.append(("write", len(rows))))

    def _fake_prune(sb, tg_id, rows):
        calls.append(("prune", tg_id, len(rows)))
        if prune_impl:
            return prune_impl()
        return 1

    monkeypatch.setattr(wsvc, "_prune_stale_13f_trades", _fake_prune)
    group = {"date": "2026-06-30", "trade_count": 1, "net_action": "BOUGHT", "net_amount": 5.0,
             "trades": [{"ticker": "NVDA", "action": "BOUGHT", "trade_type": "New", "amount": 5.0,
                         "date": "2026-06-30"}]}
    kwargs = {"prune_stale_trades": True} if prune else {}
    asyncio.run(WhaleService.__new__(WhaleService)._sync_to_whale_tables(
        "w1", [], [], [group], {}, "", 1.0, [], **kwargs,
    ))
    return calls


def test_the_live_13f_path_prunes_and_congress_never_does(monkeypatch):
    assert _live_sync(monkeypatch, prune=True) == [("write", 1), ("prune", "whale_trade_groups-1", 1)]
    assert _live_sync(monkeypatch, prune=False) == [("write", 1)]


def test_a_live_prune_failure_is_logged_not_raised(monkeypatch, caplog):
    import logging

    def boom():
        raise RuntimeError("supabase down")

    with caplog.at_level(logging.WARNING, logger="app.services.whale_service"):
        calls = _live_sync(monkeypatch, prune=True, prune_impl=boom)
    assert ("write", 1) in calls
    assert any("stale 13F trade prune failed" in r.getMessage() for r in caplog.records)


def test_only_the_13f_call_site_opts_in():
    src = (_BACKEND / "app/services/whale_service.py").read_text()
    assert src.count("prune_stale_trades=True") == 1
    i = src.index("prune_stale_trades=True")
    assert src.rfind("async def _process_13f_path", 0, i) > src.rfind("async def _process_congressional_path", 0, i)
    hyd = (_BACKEND / "scripts/hydrate_whales.py").read_text()
    assert "prune_stale_trades=not is_congress," in hyd


class _PersistQuery:
    def __init__(self, sb, table):
        self._sb, self._table, self._verb = sb, table, "select"

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def update(self, *a, **k):
        self._verb = "update"
        return self

    def execute(self):
        self._sb.ops.append((self._table, self._verb))
        return _Resp([{"id": f"{self._table}-1"}])


class _PersistSB:
    def __init__(self):
        self.ops = []

    def table(self, name):
        return _PersistQuery(self, name)


def _hydrator_persist(monkeypatch, *, prune, prune_impl):
    from types import SimpleNamespace
    from app.services._whale_common import AnnualReturn, RETURN_OK, SOURCE_STOCK

    h = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    h.sb = _PersistSB()
    h.fmp = SimpleNamespace(request_failures=0)
    h.stats = {"processed": 0, "skipped": 0, "failed": 0, "errors": 0, "no_data": 0,
               "upstream_failed": 0}
    monkeypatch.setattr(hw.WhaleHydrator, "_maybe_generate_alert", lambda self, *a, **k: None)
    monkeypatch.setattr(hw.WhaleHydrator, "_upsert_trades", staticmethod(lambda *a, **k: None))
    pruned = []

    def _fake_prune(sb, tg_id, trades):
        pruned.append((tg_id, len(trades)))
        return prune_impl()

    monkeypatch.setattr(hw, "_prune_stale_13f_trades", _fake_prune)
    from app.utils import supabase_errors as se
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)
    snapshot = {
        "filing_period": "2026-Q2", "total_value": 1.0, "behavior_summary": "",
        "sentiment_text": "", "raw_hash": "h", "holdings_data": [], "sector_data": [],
        "trade_groups": [{"date": "2026-06-30", "trade_count": 1, "net_action": "BOUGHT",
                          "net_amount": 1.0,
                          "trades": [{"ticker": "NVDA", "action": "BOUGHT", "trade_type": "New",
                                      "amount": 1.0, "date": "2026-06-30"}]}],
    }
    ret = AnnualReturn(value=1.0, window_years=1, source=SOURCE_STOCK, status=RETURN_OK)
    asyncio.run(h._persist("w1", snapshot, ret, prune_stale_trades=prune))
    return h.sb, pruned


def test_the_hydrator_prunes_13f_and_a_failed_prune_resets_the_hash(monkeypatch):
    sb, pruned = _hydrator_persist(monkeypatch, prune=True, prune_impl=lambda: 0)
    assert pruned == [("whale_trade_groups-1", 1)]
    assert ("whale_filing_snapshots", "update") not in sb.ops          # nothing failed

    def boom():
        raise RuntimeError("supabase down")

    sb, pruned = _hydrator_persist(monkeypatch, prune=True, prune_impl=boom)
    assert pruned == [("whale_trade_groups-1", 1)]
    # The 5b self-heal: raw_hash → NULL, so the next run re-derives instead of skipping.
    assert ("whale_filing_snapshots", "update") in sb.ops


def test_the_hydrator_never_prunes_a_congressional_group(monkeypatch):
    _, pruned = _hydrator_persist(monkeypatch, prune=False, prune_impl=lambda: 0)
    assert pruned == []


# ── 5. The versioned raw hash ───────────────────────────────────────────────────


def test_the_hash_carries_the_diff_version(monkeypatch):
    raw = [_row("NVDA", 1, 2)]
    legacy = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    current = thirteen_f_raw_hash(raw)
    assert current != legacy, "a stored pre-v2 hash must mismatch, or no quarter is re-derived"
    assert current == thirteen_f_raw_hash(list(raw))                   # deterministic
    monkeypatch.setattr(wc, "THIRTEEN_F_DIFF_VERSION", THIRTEEN_F_DIFF_VERSION + 1)
    assert thirteen_f_raw_hash(raw) != current


def test_both_writers_stamp_the_versioned_hash():
    # v4 (2026-10-09): the hash also carries what the quarter was compared WITH
    # (`tests/test_thirteen_f_gap_quarter.py`).
    for path in (_BACKEND / "app/services/whale_service.py", _BACKEND / "scripts/hydrate_whales.py"):
        code = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))
        assert re.search(
            r"raw_hash: Optional\[str\] = thirteen_f_raw_hash\(\s*current_raw,\s*"
            r"basis=selection\.basis,\s*previous_raw=prev_raw,?\s*\)", code,
        ), path.name
        assert not re.search(r"sha256\(\s*json\.dumps\(current_raw", code), path.name


# ── 6. Split suspects come from the SAME positions the diff reads ───────────────


def test_split_suspects_are_picked_from_the_share_positions():
    from app.services._whale_common import thirteen_f_share_rows
    from app.services.thirteen_f_splits import suspicious_split_tickers

    # A 10:1 split held through unchanged: 1,000 sh @ $1,000 → 10,000 sh @ $100. The
    # current filing's LAST raw row is an empty manager line, so last-row-wins reads NVDA as
    # 0 shares, never flags it, never looks the split up — and the share diff then books a
    # fabricated 9,000-share BOUGHT. The summed position is flagged and restated instead.
    prev = [_row("NVDA", 1_000, 1_000_000, cusip=_NVDA_CUSIP)]
    curr = [_row("NVDA", 10_000, 1_000_000, cusip=_NVDA_CUSIP),
            _row("NVDA", 0, 0, cusip=_NVDA_CUSIP)]
    assert suspicious_split_tickers(curr, prev) == []                       # the old input
    assert suspicious_split_tickers(thirteen_f_share_rows(curr), thirteen_f_share_rows(prev)) == ["NVDA"]


def test_both_writers_feed_the_split_resolver_the_share_rows():
    for path in (_BACKEND / "app/services/whale_service.py", _BACKEND / "scripts/hydrate_whales.py"):
        code = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))
        i = code.index("await resolve_13f_split_adjustments(")
        assert "thirteen_f_share_rows(current_raw), thirteen_f_share_rows(prev_raw)," in code[i:i + 300], path.name


def test_a_later_row_supplies_the_name_the_first_one_lacked():
    pos = thirteen_f_share_positions([
        _row("AAPL", 100, 20_000, cusip="037833100"),
        _row("AAPL", 50, 10_000, cusip="037833100", name="APPLE INC"),
        _row("AAPL", 25, 5_000, cusip="037833100", name="SHOULD NOT REPLACE"),
    ])
    assert pos["AAPL"]["name"] == "APPLE INC" and pos["AAPL"]["shares"] == 175


# ── 7. The HOLDINGS read the same positions (v3, owner decision 2026-10-09) ──────


def test_holdings_are_share_positions_only():
    # A 13F values an option at its UNDERLYING's shares: these puts and calls were 95% of
    # the old "13F Equity Portfolio" figure, and a PLTR put was the #1 "holding".
    holdings = thirteen_f_holdings([
        _row("MOH", 125_000, 20_000_000, cusip="60855R100"),
        _row("PLTR", 5_000_000, 900_000_000, cusip="69608A108", put_call="Put"),
        _row("PFE", 6_000_000, 150_000_000, cusip="717081103", put_call="Call"),
        _row("XYZ", 5_000_000, 5_000_000, shares_type="PRN"),
        _row("LULU", 100_000, 16_000_000, cusip="550021109"),
        _row("NVDA", 1_000, 100_000, cusip=_NVDA_CUSIP),
        _row("NVDA", 1_000_000, 180_000_000, cusip=_NVDA_CUSIP, put_call="Put"),
    ])
    assert [h["ticker"] for h in holdings] == ["MOH", "LULU", "NVDA"]
    assert holdings[0] == {
        "ticker": "MOH", "company_name": "MOH", "logo_url": None, "value": 20_000_000.0,
        "shares": 125_000, "allocation": 55.4, "change_percent": 0.0,
    }
    assert [h["allocation"] for h in holdings] == [55.4, 44.32, 0.28]   # of the $36.1M share book


def test_holdings_keep_one_row_per_ticker_inside_the_allocation_check():
    # `whale_holdings` is UNIQUE(whale_id, ticker) and allocation CHECK(0..100).
    holdings = thirteen_f_holdings([
        _row("GOOGL", 100, 15_000, cusip="02079K305"),
        _row("GOOGL", 40, 6_000, cusip="02079K107"),                    # second share class
        _row("AAPL", 100, 21_000, cusip="037833100", link=_ORIG),
        _row("AAPL", 50, 10_000, cusip="037833100", link=_ORIG),        # second manager
        _row("ZZZ", 10, 21_000),                                        # ties GOOGL's 21,000
        _row("BAD", 10, -5_000), _row("NAN", 10, float("nan")), "garbage", None,
    ])
    assert [h["ticker"] for h in holdings] == ["AAPL", "GOOGL", "ZZZ"]
    assert [h["value"] for h in holdings] == [31_000.0, 21_000.0, 21_000.0]   # a tie → by ticker
    assert all(0 <= h["allocation"] <= 100 for h in holdings)
    assert thirteen_f_holdings([]) == [] and thirteen_f_holdings(None) == []
    assert thirteen_f_holdings([_row("SPY", 1_000, 9_000, put_call="Put")]) == []


def test_both_holdings_builders_return_the_shared_holdings():
    raw = [_row("NVDA", 1_000, 180_000, cusip=_NVDA_CUSIP),
           _row("NVDA", 9_000, 1_620_000, cusip=_NVDA_CUSIP, put_call="Call"),
           _row("SPY", 5_000, 3_000_000, put_call="Put"),
           _row("MSFT", 400, 170_000), _row("MSFT", 100, 42_500)]
    svc = WhaleService.__new__(WhaleService)
    hyd = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    assert svc._build_holdings(raw) == hyd._build_13f_holdings(raw) == thirteen_f_holdings(raw)
    assert [h["ticker"] for h in svc._build_holdings(raw)] == ["MSFT", "NVDA"]


def test_the_diff_weights_use_the_share_books():
    # A put notional ~10x the stock book sat in both denominators: MOH's weights read
    # 2.14% / 6.11% instead of 61.54% / 61.29%.
    prev = [_row("MOH", 100_000, 19_000_000), _row("SLM", 500_000, 12_000_000),
            _row("SPY", 500_000, 280_000_000, put_call="Put")]
    curr = [_row("MOH", 125_000, 20_000_000), _row("SLM", 480_000, 12_500_000),
            _row("PLTR", 5_000_000, 900_000_000, put_call="Put")]
    total = sum(h["value"] for h in thirteen_f_holdings(curr))     # what both callers pass
    for group in _both_diffs(curr, prev, total):
        moh = _trades_by_ticker(group)["MOH"]
        assert (moh["previous_allocation"], moh["new_allocation"]) == (61.29, 61.54)
        assert {h["ticker"]: h["allocation"] for h in thirteen_f_holdings(curr)}["MOH"] == 61.54


def test_both_writers_store_the_same_change_percent():
    # The live writer kept the LAST previous-quarter row per symbol (here a call row stood in
    # for the stock, and one manager's slice for MSFT) over a denominator that also counted a
    # symbol-less row; the nightly writer summed. Whichever derived a quarter first was kept.
    prev = [_row("NVDA", 1_080, 1_080_000), _row("NVDA", 400, 400_000, put_call="Call"),
            _row("MSFT", 500, 2_000_000), _row("MSFT", 300, 1_200_000),
            {"symbol": None, "tickercusip": "", "value": 50_000, "sharesNumber": 1}]
    curr = [_row("NVDA", 1_200, 1_200_000), _row("NVDA", 500, 500_000, put_call="Call"),
            _row("MSFT", 900, 3_600_000)]
    svc = WhaleService.__new__(WhaleService)
    hyd = hw.WhaleHydrator.__new__(hw.WhaleHydrator)
    live = svc._apply_change_percent(svc._build_holdings(curr), prev)
    nightly = hyd._calculate_change_percent(
        hyd._build_13f_holdings(curr), hyd._build_13f_holdings(prev)
    )
    pairs = lambda hs: [(h["ticker"], h["change_percent"]) for h in hs]
    # NVDA: 25.00% of the $4.8M share book now, 25.23% of $4.28M before.
    assert pairs(live) == pairs(nightly) == [("MSFT", 0.23), ("NVDA", -0.23)]


def test_the_holdings_change_bumped_the_derivation_version():
    # The nightly sweep re-derives a quarter only when its hash changes, so without the bump
    # every stored snapshot would keep its option-inflated holdings until the next filing.
    assert THIRTEEN_F_DIFF_VERSION >= 3
