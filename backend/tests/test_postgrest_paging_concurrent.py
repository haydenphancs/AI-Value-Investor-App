"""`fetch_all_rows_concurrent` — a parallel paged read that is complete or raises.

The movers close map is a ~74-page `market_close_snapshot` sweep. Serially it took 11-14 s
across regions, past Home's 8 s scanner guard, so the first dashboard after every deploy
waited 8 s. Pages in flight make it ~3 s — but parallel OFFSET paging loses the serial
helper's only proof of completeness ("a short page means done"), because page 40 can
finish before page 3. These pin the replacement proof: an exact count first, a sentinel
page, assembly in PAGE order, and `PagedReadIncomplete` — never a partial list — for every
way the read can come back short.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.utils.postgrest_paging import (
    PAGE_SIZE,
    PagedReadIncomplete,
    fetch_all_rows_concurrent,
)

_UNSET = object()


class _Table:
    """An ordered PostgREST table: at most `cap` rows per request, an exact count on the
    count query, per-page latency, and hooks that change the table mid-sweep."""

    def __init__(self, n=0, *, cap=PAGE_SIZE, delay=None, count=_UNSET,
                 after_count=None, before_page=None, after_page=None, truncate=None):
        self.rows = [{"symbol": f"S{i:05d}", "i": i} for i in range(n)]
        self.cap = cap
        self.delay = delay or (lambda start: 0.0)
        self.count = count
        self.after_count = after_count
        self.before_page = before_page
        self.after_page = after_page
        self.truncate = truncate or {}            # {start offset: rows served}
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.started: list = []                   # page offsets, in start order
        self.completed: list = []                 # page offsets, in completion order
        self.count_calls = 0

    def grow(self, k):
        base = len(self.rows)
        self.rows.extend({"symbol": f"S{base + j:05d}", "i": base + j} for j in range(k))

    def query(self):
        return _Q(self, counting=False)

    def counter(self):
        return _Q(self, counting=True)


class _Q:
    def __init__(self, t, counting):
        self.t, self.counting = t, counting
        self._order = None
        self._range = (0, 10 ** 9)

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def execute(self):
        t = self.t
        if self.counting:
            t.count_calls += 1
            count = len(t.rows) if t.count is _UNSET else t.count
            if t.after_count:
                t.after_count(t)
            return SimpleNamespace(data=[], count=count)
        assert self._order is not None, "an unordered paged read can overlap or skip rows"
        a, b = self._range
        with t.lock:
            t.active += 1
            t.max_active = max(t.max_active, t.active)
            t.started.append(a)
        try:
            if t.before_page:
                t.before_page(t, a)
            time.sleep(t.delay(a))
            col, desc = self._order
            ordered = sorted(t.rows, key=lambda r: r[col], reverse=desc)
            page = ordered[a:b + 1][: t.cap][: t.truncate.get(a, t.cap)]
            if t.after_page:
                t.after_page(t, a)
            with t.lock:
                t.completed.append(a)
            return SimpleNamespace(data=page, count=None)
        finally:
            with t.lock:
                t.active -= 1


def _read(t, **kw):
    kw.setdefault("order_by", "symbol")
    kw.setdefault("what", "test")
    return fetch_all_rows_concurrent(t.query, count_query=t.counter, **kw)


# ── order and concurrency ──────────────────────────────────────────────────────────


def test_pages_finishing_out_of_order_are_assembled_in_page_order():
    # Earlier pages are SLOWER, so completion order is roughly the reverse of page order.
    t = _Table(95, delay=lambda a: (100 - a) / 100 * 0.04)
    out = _read(t, page_size=10, workers=4)
    assert [r["i"] for r in out] == list(range(95)), "pages were assembled out of order"
    assert t.completed != sorted(t.completed), (
        "anti-vacuity: the pages did not actually finish out of order, so this test "
        "proves nothing about the assembly"
    )


def test_no_more_than_workers_pages_are_in_flight():
    t = _Table(95, delay=lambda a: 0.01)
    assert len(_read(t, page_size=5, workers=3)) == 95
    assert t.max_active <= 3, f"{t.max_active} pages in flight with workers=3"
    assert t.max_active >= 2, "anti-vacuity: the read never ran pages concurrently"


def test_the_count_is_taken_once_and_bounds_the_page_fan_out():
    t = _Table(95)
    _read(t, page_size=10)
    assert t.count_calls == 1
    # ceil(95 / 10) = 10 data pages + 1 sentinel page, and nothing beyond.
    assert sorted(t.started) == [i * 10 for i in range(11)]


def test_desc_is_passed_through_to_every_page():
    t = _Table(25)
    out = _read(t, page_size=10, desc=True)
    assert [r["i"] for r in out] == list(range(24, -1, -1))


# ── boundaries ─────────────────────────────────────────────────────────────────────


def test_an_empty_table_reads_one_sentinel_page_and_returns_nothing():
    t = _Table(0)
    assert _read(t, page_size=10) == []
    assert t.started == [0]


def test_an_exactly_full_last_page_is_followed_by_an_empty_sentinel():
    t = _Table(100)
    out = _read(t, page_size=10)
    assert len(out) == 100
    assert sorted(t.started) == [i * 10 for i in range(11)]


def test_a_page_size_above_the_server_cap_is_clamped():
    t = _Table(2_500)
    assert len(_read(t, page_size=5_000)) == 2_500
    assert all(a % PAGE_SIZE == 0 for a in t.started)


@pytest.mark.parametrize("kw", [{"page_size": 0}, {"page_size": -5}, {"workers": 0}])
def test_nonsense_parameters_are_rejected(kw):
    with pytest.raises(ValueError):
        _read(_Table(10), **kw)


# ── fail closed ────────────────────────────────────────────────────────────────────


def test_a_raising_page_raises_and_returns_nothing():
    def _boom(t, a):
        if a == 30:
            raise RuntimeError("PostgREST 520 on page 3")

    t = _Table(95, before_page=_boom)
    with pytest.raises(RuntimeError, match="page 3"):
        _read(t, page_size=10, workers=4)


def test_a_raising_page_stops_the_pages_not_yet_started():
    def _boom(t, a):
        if a == 0:
            raise RuntimeError("first page failed")

    t = _Table(95, before_page=_boom, delay=lambda a: 0.01)
    with pytest.raises(RuntimeError):
        _read(t, page_size=10, workers=1)
    # With one worker, page 0 fails; at most the page the worker had already picked up
    # runs after it. The other nine are cancelled, not fetched and thrown away.
    assert len(t.started) <= 2, t.started


def test_a_short_page_in_the_middle_raises():
    """A page that comes back short while a later one has rows = rows moved under the
    sweep, and something between them was skipped."""
    t = _Table(95, truncate={30: 6})
    with pytest.raises(PagedReadIncomplete, match="page 3"):
        _read(t, page_size=10)


def test_a_server_cap_below_the_page_size_raises_instead_of_truncating():
    t = _Table(95, cap=7)
    with pytest.raises(PagedReadIncomplete):
        _read(t, page_size=10)


def test_fewer_rows_than_the_count_raises():
    # The count saw 95 rows; 5 were deleted before the pages ran.
    t = _Table(95, after_count=lambda t: t.rows.__delitem__(slice(0, 5)))
    with pytest.raises(PagedReadIncomplete, match="exact count was 95"):
        _read(t, page_size=10)


@pytest.mark.parametrize("count", [None, True, -1, "95", 9.5])
def test_a_missing_or_malformed_count_raises(count):
    t = _Table(95, count=count)
    with pytest.raises(PagedReadIncomplete):
        _read(t, page_size=10)
    assert t.started == [], "no page may be fetched without a count to bound the read"


def test_a_count_past_the_backstop_raises_before_any_page():
    t = _Table(0, count=10 * 10 + 1)        # needs 11 data pages + 1 sentinel
    with pytest.raises(PagedReadIncomplete, match="backstop"):
        _read(t, page_size=10, max_pages=11)
    assert t.started == []


# ── growth during the sweep ────────────────────────────────────────────────────────


def test_rows_added_after_the_count_are_caught_by_the_sentinel():
    t = _Table(95, after_count=lambda t: t.grow(8))     # count 95, table 103
    out = _read(t, page_size=10)
    assert [r["i"] for r in out] == list(range(103))


def test_growth_past_a_full_sentinel_keeps_paging_serially():
    t = _Table(95, after_count=lambda t: t.grow(25))    # count 95, table 120
    out = _read(t, page_size=10)
    assert [r["i"] for r in out] == list(range(120))
    assert 120 in t.started, "the empty page after the growth was never probed"


def test_growth_between_the_last_page_and_the_sentinel_raises():
    """Page 9 (rows 90-94) is read BEFORE 8 rows land, the sentinel AFTER: rows 95-99 sit
    between the two reads and were seen by neither. One worker makes the order exact."""
    def _grow_after_page_9(t, a):
        if a == 90:
            t.grow(8)

    t = _Table(95, after_page=_grow_after_page_9)
    with pytest.raises(PagedReadIncomplete, match="page 9"):
        _read(t, page_size=10, workers=1)


def test_still_growing_past_the_backstop_raises():
    def _always_grow(t, a):
        t.grow(10)

    t = _Table(20, after_page=_always_grow)
    with pytest.raises(PagedReadIncomplete, match="still growing"):
        _read(t, page_size=10, workers=1, max_pages=6)
