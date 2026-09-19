"""A capped `whale_trades` read must make PROGRESS, not park the cursor forever.

`_run_whale_phase` pages `whale_trades` since its cursor, at most `WHALE_PHASE_MAX_PAGES`
pages. The first paged version ordered on `id` alone and, when every page filled, held
the cursor at `since` — logging that "the remainder is evaluated next run". But
`whale_trades.id` is `gen_random_uuid()`: a random order unrelated to time. The next run
issued the identical query from the identical `since`, got the identical first 10,000
rows, and the rows beyond the cap were never reached. Nothing deletes `whale_trades`, so
once >10,000 rows sat past the cursor (a held job across a 13F season, a registry
backfill) whale / congress notifications silently stopped for good — while the ERROR log
promised the opposite (found 2026-09-16, F17-2).

Fixed by reading in `created_at` order (id as the tiebreak) and, on a capped read,
advancing the cursor to the highest stamp STRICTLY BELOW the last one read — so the
boundary tie group (one bulk upsert = one `created_at`, per-transaction `now()`) is
re-read next run rather than half-skipped, and everything after it is reached.

`test_sender_reads_are_paged.py::test_a_capped_whale_read_does_not_advance_the_cursor`
pins the OLD behaviour (`cursor == since`) and must be inverted with this change. Its
fake store also sorts by `created_at` whatever order key is requested, so it could not
have seen the uuid problem; the store below sorts by the keys the query actually asks for.
"""

from __future__ import annotations

import inspect
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.notification_senders import smart_money_sender as sm
from app.utils.postgrest_paging import PAGE_SIZE


# ── a whale_trades table that behaves like PostgREST ───────────────────────────────


class _Store:
    """Clamps every page to PAGE_SIZE and sorts by the ORDER keys the query asked for."""

    def __init__(self, rows):
        self.rows = rows
        self.orders = []          # one entry per executed page: [(col, desc), …]

    def table(self, name):
        assert name == "whale_trades"
        return _Q(self)


class _Q:
    def __init__(self, store):
        self.store = store
        self.since = None
        self.order_keys = []
        self.rng = None

    def select(self, *a, **k): return self
    def gt(self, col, val): assert col == "created_at"; self.since = val; return self
    def range(self, a, b): self.rng = (a, b); return self

    def order(self, col, desc=False):
        # postgrest-py APPENDS: `.order(a).order(b)` → `order=a.asc,b.asc`.
        self.order_keys.append((col, desc))
        return self

    def execute(self):
        self.store.orders.append(list(self.order_keys))
        rows = [r for r in self.store.rows if r["created_at"] > self.since]
        for col, desc in reversed(self.order_keys):     # stable multi-key sort
            rows.sort(key=lambda r: r[col], reverse=desc)
        a, b = self.rng
        b = min(b, a + PAGE_SIZE - 1)                   # the server clamp
        return SimpleNamespace(data=rows[a:b + 1])


def _rows(n, start, *, tie=1):
    """`n` rows with RANDOM uuid ids and `created_at` advancing one second per `tie` rows
    (a tie group = one bulk upsert sharing a per-transaction `now()`)."""
    return [{
        "id": str(uuid.uuid4()), "ticker": f"T{i}", "action": "bought", "amount": 1_000_000,
        "date": "2026-11-14", "whale_id": f"w{i % 7}",
        "created_at": (start + timedelta(seconds=i // tie)).isoformat(),
        "whales": {"name": "Fund", "firm_name": "Fund", "data_source": "13f"},
    } for i in range(n)]


def _wire(monkeypatch, store, evaluated):
    monkeypatch.setattr(sm, "get_supabase", lambda: store)
    monkeypatch.setattr(sm, "WHALE_PHASE_MAX_PAGES", 2)          # cap = 2,000 rows

    def _seen(raw, cutoff_date):
        evaluated.append([r["id"] for r in raw])
        return []                                                # nothing to notify

    monkeypatch.setattr(sm, "_recent_whale_rows", _seen)


# ── the two-run contract ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_consecutive_capped_runs_reach_every_row(monkeypatch):
    """2,500 rows past the cursor, cap 2,000, random uuids: run 1 is capped and the cursor
    MOVES; run 2, fed run 1's cursor, reaches the rest. Under the old id-ordered read with
    a held cursor, run 2 re-read the same 2,000 and the last 500 were never evaluated."""
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    rows = _rows(2500, start, tie=7)
    store, evaluated = _Store(rows), []
    _wire(monkeypatch, store, evaluated)
    since = start - timedelta(hours=1)
    now = start + timedelta(hours=6)

    _sent, cursor1 = await sm._run_whale_phase(now=now, cursor=since)
    assert cursor1 > since, "a capped read parked the cursor — run 2 would repeat run 1"
    assert len(evaluated[0]) == 2000

    _sent, cursor2 = await sm._run_whale_phase(now=now, cursor=cursor1)
    reached = set(evaluated[0]) | set(evaluated[1])
    assert reached == {r["id"] for r in rows}, (
        f"{len({r['id'] for r in rows} - reached)} row(s) beyond the cap were never evaluated"
    )
    assert cursor2 == max(datetime.fromisoformat(r["created_at"]) for r in rows)


@pytest.mark.asyncio
async def test_the_read_is_ordered_by_created_at_then_id(monkeypatch):
    """The ORDER the query asks for, page by page: time first, the unique id as tiebreak.
    `id` alone is the bug; `created_at` alone can skip/duplicate a boundary row."""
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    store, evaluated = _Store(_rows(10, start)), []
    _wire(monkeypatch, store, evaluated)
    await sm._run_whale_phase(now=start + timedelta(hours=6), cursor=start - timedelta(hours=1))
    assert store.orders and all(o == [("created_at", False), ("id", False)] for o in store.orders), store.orders


@pytest.mark.asyncio
async def test_a_boundary_tie_group_split_by_the_cap_is_fully_evaluated(monkeypatch):
    """The cap lands INSIDE a tie group (one bulk upsert, one `created_at`). The unread
    half must be reached by run 2 — advancing to the last stamp itself, under a `gt`
    filter, would skip it forever."""
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    # 1,990 singleton rows, then one 30-row tie group straddling the 2,000 cap, then 20 more.
    rows = _rows(1990, start)
    group_stamp = (start + timedelta(seconds=5000)).isoformat()
    group = _rows(30, start)
    for r in group:
        r["created_at"] = group_stamp
    tail = _rows(20, start + timedelta(seconds=9000))
    rows += group + tail
    store, evaluated = _Store(rows), []
    _wire(monkeypatch, store, evaluated)
    since = start - timedelta(hours=1)
    now = start + timedelta(hours=6)

    _sent, cursor1 = await sm._run_whale_phase(now=now, cursor=since)
    assert cursor1 == datetime.fromisoformat(rows[1989]["created_at"]), (
        "the cursor must sit just BELOW the split tie group, not on it"
    )
    _sent, _c2 = await sm._run_whale_phase(now=now, cursor=cursor1)
    assert {r["id"] for r in group} <= set(evaluated[1]), "part of the tie group was skipped"
    assert {r["id"] for r in tail} <= set(evaluated[1])


@pytest.mark.asyncio
async def test_an_uncapped_read_still_advances_to_the_newest_stamp(monkeypatch):
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    rows = _rows(37, start, tie=3)
    store, evaluated = _Store(rows), []
    _wire(monkeypatch, store, evaluated)
    _sent, cursor = await sm._run_whale_phase(now=start + timedelta(hours=6), cursor=start - timedelta(hours=1))
    assert cursor == max(datetime.fromisoformat(r["created_at"]) for r in rows)


@pytest.mark.asyncio
async def test_a_capped_read_of_one_giant_tie_group_holds_and_says_so(monkeypatch, caplog):
    """Every row read shares one stamp: nothing strictly below it exists, so the cursor
    cannot move under a `gt` filter. That is logged by name rather than promised away."""
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    rows = _rows(2100, start, tie=10_000)
    store, evaluated = _Store(rows), []
    _wire(monkeypatch, store, evaluated)
    since = start - timedelta(hours=1)
    with caplog.at_level("ERROR"):
        _sent, cursor = await sm._run_whale_phase(now=start + timedelta(hours=6), cursor=since)
    assert cursor == since
    assert any("could NOT advance" in r.getMessage() for r in caplog.records), caplog.text


# ── `_capped_cursor` on strange input ───────────────────────────────────────────────


_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _r(stamp):
    return {"created_at": stamp}


def test_capped_cursor_is_the_highest_stamp_strictly_below_the_last():
    rows = [_r((_T0 + timedelta(seconds=s)).isoformat()) for s in (1, 2, 2, 3, 3, 3)]
    assert sm._capped_cursor(rows, _T0) == _T0 + timedelta(seconds=2)


def test_capped_cursor_does_not_depend_on_input_order():
    rows = [_r((_T0 + timedelta(seconds=s)).isoformat()) for s in (3, 1, 3, 2, 3, 2)]
    assert sm._capped_cursor(rows, _T0) == _T0 + timedelta(seconds=2)


@pytest.mark.parametrize("rows", [[], None, "not a list", [{}], [_r("")], [_r("garbage")]])
def test_capped_cursor_falls_back_when_nothing_is_parseable(rows):
    assert sm._capped_cursor(rows, _T0) == _T0


def test_capped_cursor_falls_back_when_every_row_shares_one_stamp():
    rows = [_r((_T0 + timedelta(seconds=5)).isoformat())] * 4
    assert sm._capped_cursor(rows, _T0) == _T0


def test_capped_cursor_with_a_single_row_falls_back():
    assert sm._capped_cursor([_r((_T0 + timedelta(seconds=5)).isoformat())], _T0) == _T0


def test_capped_cursor_never_moves_backwards():
    late = _T0 + timedelta(days=30)
    rows = [_r((_T0 + timedelta(seconds=s)).isoformat()) for s in (1, 2, 3)]
    assert sm._capped_cursor(rows, late) == late


def test_capped_cursor_skips_unparseable_rows_and_reads_naive_and_zulu_stamps():
    rows = [
        _r("2026-01-01T00:00:01"),              # naive → UTC
        _r("2026-01-01T00:00:02Z"),             # Zulu suffix
        _r("nonsense"), {}, {"created_at": None},
        _r("2026-01-01T00:00:03+00:00"),
    ]
    assert sm._capped_cursor(rows, _T0) == _T0 + timedelta(seconds=2)


def test_max_created_at_is_unchanged_by_the_refactor():
    rows = [_r((_T0 + timedelta(seconds=s)).isoformat()) for s in (3, 1, 2)] + [_r("bad"), {}]
    assert sm._max_created_at(rows, _T0) == _T0 + timedelta(seconds=3)
    assert sm._max_created_at([], _T0) == _T0
    assert sm._max_created_at(None, _T0) == _T0


# ── source guard ────────────────────────────────────────────────────────────────────


def test_the_whale_read_orders_by_time_and_the_capped_branch_advances():
    src = inspect.getsource(sm._run_whale_phase)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
    assert '.order("created_at")' in code, "the whale read must be in time order"
    assert code.index('.order("created_at")') < code.index('order_by="id"'), (
        "created_at must be the PRIMARY key; id is the tiebreak fetch_all_rows appends"
    )
    assert "next_cursor = since if capped" not in code.replace("  ", " "), (
        "a capped read holds the cursor — the id-ordered stall is back"
    )
    assert "_capped_cursor(raw, since)" in code
