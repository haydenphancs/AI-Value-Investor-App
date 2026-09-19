"""`GET /watchlist` pages to completion (F15-10).

PostgREST clamps a single page at ~1,000 rows. This is the reader the iOS star state comes
from, so a >1,000-item user saw their OLDEST stars unfilled (the read was ordered newest
first) and could re-add them — the exact truncation the 2026-09-13 paging sweep fixed on
the Tracking feed's read of the same table, and left here.
"""

from __future__ import annotations

import inspect

import pytest

from app.api.v1.endpoints import watchlist as wl
from app.utils.postgrest_paging import PAGE_SIZE


class _Q:
    def __init__(self, rows):
        self._rows, self._rng, self._order = rows, None, None

    def select(self, *a, **k): return self
    def eq(self, *a): return self
    def order(self, col, desc=False): self._order = (col, desc); return self
    def range(self, a, b): self._rng = (a, b); return self
    def limit(self, n): self._rng = (0, n - 1); return self

    def execute(self):
        rows = sorted(self._rows, key=lambda r: r[self._order[0]], reverse=self._order[1]) \
            if self._order else list(self._rows)
        # PostgREST's server clamp: never more than PAGE_SIZE rows per answer.
        if self._rng:
            a, b = self._rng
            rows = rows[a:min(b + 1, a + PAGE_SIZE)]
        else:
            rows = rows[:PAGE_SIZE]
        return type("R", (), {"data": [dict(r) for r in rows]})()


class _SB:
    def __init__(self, rows): self.rows = rows
    def table(self, name): return _Q(self.rows)


@pytest.mark.asyncio
async def test_a_watchlist_past_the_server_clamp_is_read_whole_newest_first():
    n = PAGE_SIZE + 250
    rows = [{"id": f"{i:06d}", "user_id": "u1", "ticker": f"T{i}",
             "added_at": f"2026-01-01T00:00:{i % 60:02d}.{i:06d}+00:00"} for i in range(n)]
    out = await wl.get_watchlist(user={"id": "u1"}, supabase=_SB(rows))
    assert len(out) == n, f"{len(out)} rows — the read is still a single clamped page"
    assert out == sorted(out, key=lambda r: r["added_at"], reverse=True)


def test_the_read_walks_the_unique_id_and_runs_off_the_loop():
    src = inspect.getsource(wl.get_watchlist)
    assert "fetch_all_rows(" in src and 'order_by="id"' in src
    assert "asyncio.to_thread(" in src
    assert ".order(\"added_at\", desc=True)" not in src, "the single clamped page is back"
