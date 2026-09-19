"""Two portfolio-endpoint guards from the 2026-09-12 widened deep-check.

1. `_fetch_user_portfolios`, the two watchlist seed reads and the Tracking feed's watchlist
   read were single-page: PostgREST clamps every answer to ~1,000 rows, `PortfolioStore`
   took the truncated GET as the truth, and the next whole-list `PUT /tickers` DELETED the
   rows the client never saw. They now page on the unique id.
2. `set_portfolio_holdings` validated only the SIGN before writing row by row; a value past
   the column precision (numeric(20,4) / numeric(20,2)) raised 22003 on ITS row after the
   earlier rows were persisted — the half-written state the pre-validation exists to prevent.
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import portfolios as pf
from app.utils.postgrest_paging import PAGE_SIZE


# ─────────────────────────────────────────────── 1. paged reads


class _Q:
    """A builder whose execute() clamps to PAGE_SIZE rows per range, like PostgREST."""

    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self.rng = None

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, n): self.rng = (0, n - 1); return self
    def range(self, a, b): self.rng = (a, b); return self

    def execute(self):
        rows = self.sb.rows[self.table]
        self.sb.calls.append((self.table, self.rng))
        if self.rng is None:
            page = rows[:PAGE_SIZE]
        else:
            a, b = self.rng
            page = rows[a:min(b, a + PAGE_SIZE - 1) + 1]

        class _R: pass
        r = _R(); r.data = page; return r


class _SB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def table(self, name):
        return _Q(self, name)


def test_fetch_user_portfolios_returns_every_item_past_the_clamp():
    items = [{"id": f"i{i:05d}", "portfolio_id": "p1", "ticker": f"T{i}", "position": i,
              "shares": None, "market_value": None} for i in range(PAGE_SIZE + 50)]
    sb = _SB({"portfolios": [{"id": "p1", "user_id": "u", "name": "Holdings", "sort_order": 0,
                              "created_at": "2026-09-12T00:00:00Z", "updated_at": "2026-09-12T00:00:00Z",
                              "is_active": True}],
              "portfolio_items": items})
    out = pf._fetch_user_portfolios(sb, "u")
    assert len(out) == 1
    assert len(out[0].items) == PAGE_SIZE + 50, "the tail past the clamp was dropped"
    # Ordered by position after the id-paged read.
    assert [it.ticker for it in out[0].items][:3] == ["T0", "T1", "T2"]
    assert [c for c in sb.calls if c[0] == "portfolio_items"][0][1] == (0, PAGE_SIZE - 1)


def test_watchlist_seed_rows_are_paged_and_newest_first():
    rows = [{"id": f"w{i:05d}", "ticker": f"T{i}", "added_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
             "shares": None, "market_value": None} for i in range(PAGE_SIZE + 7)]
    sb = _SB({"watchlist_items": rows})
    out = pf._read_watchlist_seed_rows(sb, "u")
    assert len(out) == PAGE_SIZE + 7
    stamps = [r["added_at"] for r in out]
    assert stamps == sorted(stamps, reverse=True)


# ─────────────────────────────────────────────── 2. holdings magnitude


class _Recorder:
    def __init__(self): self.updates = []
    def table(self, name): return self
    def update(self, values): self.updates.append(values); return self
    def eq(self, *a, **k): return self
    def select(self, *a, **k): return self
    def single(self): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def execute(self):
        class _R: pass
        r = _R()
        r.data = [{"id": "p1", "user_id": "u", "name": "Holdings", "sort_order": 0, "is_active": True,
                   "created_at": "2026-09-12T00:00:00Z", "updated_at": "2026-09-12T00:00:00Z",
                   "portfolio_id": "p1", "ticker": "AAPL", "position": 0, "shares": None,
                   "market_value": None}]
        return r


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"ticker": "MSFT", "shares": 1e16},
    {"ticker": "MSFT", "shares": 12345678901234567.0},
    {"ticker": "MSFT", "market_value": 1e18},
    {"ticker": "MSFT", "shares": float("nan")},
    {"ticker": "MSFT", "market_value": float("inf")},
    {"ticker": "MSFT", "shares": -1.0},
])
async def test_an_out_of_range_row_rejects_the_whole_payload_before_any_write(bad):
    sb = _Recorder()
    req = pf.SetPortfolioHoldingsRequest(items=[
        pf.HoldingItem(ticker="AAPL", shares=10.0), pf.HoldingItem(**bad),
    ])
    with patch.object(pf, "_get_portfolio_or_404", lambda *a, **k: {"id": "p1"}):
        with pytest.raises(HTTPException) as exc:
            await pf.set_portfolio_holdings("p1", req, user={"id": "u"}, supabase=sb)
    assert exc.value.status_code == 400
    assert sb.updates == [], "a row was written before the rejection"


@pytest.mark.asyncio
async def test_the_largest_representable_values_are_accepted():
    sb = _Recorder()
    req = pf.SetPortfolioHoldingsRequest(items=[
        pf.HoldingItem(ticker="AAPL", shares=9.9e15, market_value=9.9e17),
        pf.HoldingItem(ticker="MSFT", shares=0.0, market_value=None),
    ])
    with patch.object(pf, "_get_portfolio_or_404", lambda *a, **k: {"id": "p1"}), \
         patch.object(pf, "invalidate_feed_cache", lambda *a, **k: None):
        await pf.set_portfolio_holdings("p1", req, user={"id": "u"}, supabase=sb)
    assert len([u for u in sb.updates if "shares" in u]) == 2


# ─────────────────────────────────────────────── 3. the WRITE path is paged too (F22-1)
#
# The F16-7 fix paged `_fetch_user_portfolios` and the seed reads. `set_portfolio_tickers`
# still had THREE single-page reads, and iOS `PortfolioStore.syncTickers` adopts the PUT
# response as the truth, so each one lost rows silently with a 200:
#
#   * the watchlist-validity lookup (`.in_("ticker", lookup)`) — clamped to ~1,000, so
#     with >1,000 valid tickers the tail was logged as "dropped" and REMOVED from the
#     group by the delete+reinsert, on the first PUT;
#   * the `existing_items` snapshot — the only carrier of hand-entered `shares` across
#     the delete+reinsert, so kept tickers past the clamp came back with `shares: None`;
#   * `_fetch_portfolio_items` — every write response, so the client's local list shrank
#     and its NEXT whole-list PUT deleted the rows it never saw.

import ast
import asyncio
from pathlib import Path


class _StoreQ:
    """A store-backed builder whose reads clamp at PAGE_SIZE per request, like PostgREST.

    Reads honour `.range()` on top of the clamp so a paged caller gets every row and a
    single-page caller gets exactly the first PAGE_SIZE — which is the whole point.
    """

    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self._op, self._payload = "select", None
        self._filters, self._in, self._rng, self._order = {}, None, None, None

    def select(self, *a, **k): self._op = "select"; return self
    def insert(self, p): self._op, self._payload = "insert", p; return self
    def upsert(self, p, on_conflict=None, ignore_duplicates=False):
        # F15-8: `_replace_items` merges on (portfolio_id, ticker) now.
        self._op, self._payload = "upsert", p
        self._on_conflict, self._ignore_duplicates = on_conflict, ignore_duplicates
        return self
    def update(self, p): self._op, self._payload = "update", p; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, c, v): self._filters[c] = v; return self
    def in_(self, c, vals): self._in = (c, list(vals)); return self
    def order(self, col, desc=False): self._order = (col, desc); return self
    def limit(self, n): self._rng = (0, n - 1); return self
    def range(self, a, b): self._rng = (a, b); return self

    def _matched(self, rows):
        out = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._in:
            col, vals = self._in
            out = [r for r in out if r.get(col) in vals]
        return out

    def execute(self):
        rows = self.sb.rows.setdefault(self.table, [])
        if self._op == "insert":
            batch = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in batch:
                if self.table == "portfolio_items" and any(
                    r["portfolio_id"] == p["portfolio_id"] and r["ticker"] == p["ticker"]
                    for r in rows
                ):
                    raise RuntimeError(f"23505 duplicate key {p['ticker']}")
                row = dict(p)
                row.setdefault("id", f"n{self.sb.next_id:06d}")
                self.sb.next_id += 1
                rows.append(row)
            self.sb.log.append(("insert", self.table, len(batch)))
            return type("R", (), {"data": [dict(p) for p in batch]})()
        if self._op == "upsert":
            assert self._on_conflict == "portfolio_id,ticker", self._on_conflict
            batch = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in batch:
                existing = next((r for r in rows if r["portfolio_id"] == p["portfolio_id"]
                                 and r["ticker"] == p["ticker"]), None)
                if existing is None:
                    row = dict(p)
                    row.setdefault("id", f"n{self.sb.next_id:06d}")
                    self.sb.next_id += 1
                    rows.append(row)
                elif not self._ignore_duplicates:
                    existing.update(p)
            self.sb.log.append(("insert", self.table, len(batch)))
            return type("R", (), {"data": [dict(p) for p in batch]})()
        matched = self._matched(rows)
        if self._op == "delete":
            for r in matched:
                rows.remove(r)
            self.sb.log.append(("delete", self.table, len(matched)))
            return type("R", (), {"data": [dict(r) for r in matched]})()
        if self._op == "update":
            for r in matched:
                r.update(self._payload)
            return type("R", (), {"data": [dict(r) for r in matched]})()
        if self._order:
            col, desc = self._order
            matched = sorted(matched, key=lambda r: str(r.get(col)), reverse=desc)
        if self._rng is None:
            page = matched[:PAGE_SIZE]
        else:
            a, b = self._rng
            page = matched[a:min(b, a + PAGE_SIZE - 1) + 1]
        self.sb.log.append(("select", self.table, self._rng, len(page)))
        return type("R", (), {"data": [dict(r) for r in page]})()


class _StoreSB:
    def __init__(self, rows):
        self.rows, self.log, self.next_id = rows, [], 0

    def table(self, name):
        return _StoreQ(self, name)


_N = PAGE_SIZE + 50


def _big_group():
    """One group of PAGE_SIZE+50 tickers, every one on the watchlist, the last 50 with
    hand-entered shares — the rows a single-page read never sees."""
    tickers = [f"T{i:05d}" for i in range(_N)]
    return _StoreSB({
        "portfolios": [{"id": "p1", "user_id": "u", "name": "Holdings", "sort_order": 0,
                        "is_active": True, "created_at": "2026-09-12T00:00:00Z",
                        "updated_at": "2026-09-12T00:00:00Z"}],
        "watchlist_items": [{"id": f"w{i:05d}", "user_id": "u", "ticker": t,
                             "added_at": "2026-09-01T00:00:00Z", "shares": None,
                             "market_value": None} for i, t in enumerate(tickers)],
        "portfolio_items": [{"id": f"i{i:05d}", "portfolio_id": "p1", "ticker": t,
                             "position": i,
                             "shares": (float(i) if i >= PAGE_SIZE else None),
                             "market_value": None} for i, t in enumerate(tickers)],
    }), tickers


def _put(sb, tickers):
    with patch.object(pf, "_get_portfolio_or_404", lambda *a, **k: {"id": "p1"}), \
         patch.object(pf, "invalidate_feed_cache", lambda *a, **k: None):
        return asyncio.run(pf.set_portfolio_tickers(
            "p1", pf.SetTickersRequest(tickers=tickers), user={"id": "u"}, supabase=sb,
        ))


def test_the_fake_really_clamps_a_single_page_read():
    """Anti-vacuity: an unpaged `.select().eq()` on the big group returns PAGE_SIZE rows."""
    sb, _ = _big_group()
    got = sb.table("portfolio_items").select("*").eq("portfolio_id", "p1").execute().data
    assert len(got) == PAGE_SIZE


def test_put_tickers_keeps_every_row_and_every_share_past_the_clamp():
    sb, tickers = _big_group()
    new = tickers + ["NEW1"]
    sb.rows["watchlist_items"].append({"id": "wnew", "user_id": "u", "ticker": "NEW1",
                                       "added_at": "2026-09-02T00:00:00Z",
                                       "shares": None, "market_value": None})

    out = _put(sb, new)

    on_disk = {r["ticker"]: r for r in sb.rows["portfolio_items"]}
    assert len(on_disk) == _N + 1, "the tail past the clamp was dropped from the group"
    assert "NEW1" in on_disk
    # Every hand-entered share past row 1,000 survives the delete+reinsert.
    lost = [t for i, t in enumerate(tickers) if i >= PAGE_SIZE and on_disk[t]["shares"] != float(i)]
    assert not lost, f"{len(lost)} kept tickers lost their shares, e.g. {lost[:3]}"
    # And the RESPONSE — what the client adopts as truth — carries them all.
    assert len(out.items) == _N + 1
    assert [it.ticker for it in out.items] == new, "response must be in position order"
    assert out.items[PAGE_SIZE].shares == float(PAGE_SIZE)


def test_put_tickers_does_not_log_valid_tail_tickers_as_dropped(caplog):
    """The membership lookup was the FIRST clamp: valid tickers past 1,000 were reported
    as 'on neither spelling of the watchlist' and removed before the snapshot was read."""
    sb, tickers = _big_group()
    with caplog.at_level("WARNING"):
        _put(sb, tickers)
    assert not [r for r in caplog.records if "dropped" in r.getMessage()], (
        "a valid ticker past the clamp was treated as not on the watchlist"
    )
    assert len(sb.rows["portfolio_items"]) == _N


def test_a_genuinely_unknown_ticker_is_still_dropped_with_a_warning(caplog):
    """The paged lookup must not become 'accept everything'."""
    sb, tickers = _big_group()
    with caplog.at_level("WARNING"):
        out = _put(sb, tickers[:3] + ["NOTONLIST"])
    assert [it.ticker for it in out.items] == tickers[:3]
    assert any("NOTONLIST" in r.getMessage() for r in caplog.records)


def test_fetch_portfolio_items_returns_every_row_in_position_order():
    sb, tickers = _big_group()
    # Store them shuffled by id so the Python position sort is what orders the answer.
    sb.rows["portfolio_items"].reverse()
    out = pf._fetch_portfolio_items(sb, "p1")
    assert len(out) == _N
    assert [it.ticker for it in out] == tickers
    reads = [c for c in sb.log if c[0] == "select" and c[1] == "portfolio_items"]
    assert reads[0][2] == (0, PAGE_SIZE - 1), "the read must be paged on a range"


def test_fetch_portfolio_items_tolerates_a_missing_or_null_position():
    """Outlier: a row with `position: None` (or absent) must sort, not TypeError the
    response of every write."""
    sb = _StoreSB({"portfolio_items": [
        {"id": "a", "portfolio_id": "p1", "ticker": "B", "position": 1, "shares": None, "market_value": None},
        {"id": "b", "portfolio_id": "p1", "ticker": "A", "position": None, "shares": 2.0, "market_value": None},
        {"id": "c", "portfolio_id": "p1", "ticker": "C", "shares": None, "market_value": 5.0},
    ]})
    out = pf._fetch_portfolio_items(sb, "p1")
    assert [it.ticker for it in out] == ["A", "C", "B"]
    assert out[0].shares == 2.0 and out[1].market_value == 5.0


def test_fetch_portfolio_items_empty_group():
    sb = _StoreSB({"portfolio_items": []})
    assert pf._fetch_portfolio_items(sb, "p1") == []


# ── source guard: the three reads stay paged ─────────────────────────────────

def _calls_in(func_name: str) -> tuple[list[str], list[str]]:
    """(callee names invoked, attribute-method names invoked) inside one handler of
    portfolios.py — resolved through `asyncio.to_thread(fn, ...)` so the wrapper is
    transparent. AST, not a string grep, so a comment quoting the old shape cannot
    satisfy or fail it."""
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints"
           / "portfolios.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name)
    callees, methods = [], []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute):
            methods.append(f.attr)
            if f.attr == "to_thread" and n.args:
                callees.append(getattr(n.args[0], "id", None) or getattr(n.args[0], "attr", None))
        elif isinstance(f, ast.Name):
            callees.append(f.id)
    return callees, methods


def test_set_portfolio_tickers_pages_all_three_reads():
    callees, methods = _calls_in("set_portfolio_tickers")
    assert callees.count("fetch_all_rows") >= 1, "the existing_items snapshot is unpaged again"
    assert "_read_watchlist_seed_rows" in callees, (
        "the watchlist-validity lookup must use the paged seed read, not a clamped `.in_`"
    )
    assert "in_" not in methods, (
        "an `.in_(\"ticker\", lookup)` read is clamped to ~1,000 rows — the tail is dropped"
    )
    assert "_fetch_portfolio_items" in callees, "the response must come from the paged reader"


def test_fetch_portfolio_items_is_paged():
    callees, methods = _calls_in("_fetch_portfolio_items")
    assert "fetch_all_rows" in callees
    assert "range" not in methods and "limit" not in methods, "page through the helper only"


def test_the_ast_guard_sees_through_to_thread():
    """Anti-vacuity for `_calls_in`: it must resolve `await asyncio.to_thread(fn, ...)`."""
    callees, _ = _calls_in("list_portfolios")
    assert "_fetch_user_portfolios" in callees
