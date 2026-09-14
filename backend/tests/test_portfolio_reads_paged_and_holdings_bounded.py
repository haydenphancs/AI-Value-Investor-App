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
