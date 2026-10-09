"""GET /portfolios reads the groups and their items IN PARALLEL (2026-10-08).

The first read of `list_portfolios` used to be two serial Supabase hops (`portfolios`,
then `portfolio_items .in_(ids)`), on the launch-critical Holdings gate. It is now
`_fetch_user_portfolios_concurrent`: the `portfolios` read ‖ ONE joined, PAGED
`portfolio_items` read that needs no group ids —
`select(..., portfolios!inner(user_id)).eq("portfolios.user_id", uid)`.

What must hold, and why each is pinned here:

  * the two reads really overlap (a `threading.Barrier(2)` both must reach);
  * the joined read stays PAGED past PostgREST's ~1,000-row clamp — iOS adopts this GET as
    the truth and its whole-list `PUT /tickers` deletes the rows it never saw — and the
    items keep their `position` order;
  * `!inner` has no precedent in this repo and cannot be proven against PostgREST
    hermetically, so every page is OWNER-CHECKED (null-embed-safe, case-insensitive); a
    page that is not provably the caller's aborts the read on that page and the hard
    serial fallback serves today's answer;
  * the fallback REUSES the `portfolios` rows already read (one extra hop, not two);
  * an item whose group is not in the rows (a create/delete between the two reads) is
    skipped with one WARNING — it used to be a KeyError → 500;
  * a group in the rows with NO item in the joined read is re-read serially (the TOP-UP):
    `claim-guest-data` moving a group to this user between the two snapshots otherwise
    served it EMPTY, and iOS's next whole-list `PUT /tickers` deleted its real items. Only
    those groups are re-read (no duplicates), and not at all when every group got items;
  * a failed `portfolios` read RAISES — never [], which would seed a duplicate group;
  * no groups + a failed joined read → still LOGGED (ERROR for an unapplied `!inner`)
    before the seed; a BaseException from the joined read never reads as "no groups";
  * no cache: every call reads both tables.

Behavioural tests drive a fake Supabase that clamps pages like PostgREST and models the
embed (inner / left join, an ignored filter). The MUTATIONS table at the bottom applies
each regression to the module source IN MEMORY, executes it as a throwaway module and
requires the named scenario to fail on it.
"""
from __future__ import annotations

import asyncio
import copy
import itertools
import logging
import sys
import threading
import types
from pathlib import Path

import pytest

import app.api.v1.endpoints.portfolios as pf
from app.utils.postgrest_paging import PAGE_SIZE

UID = "8f3c2b1a-1111-4c2d-9e8f-0123456789ab"
OTHER = "0a1b2c3d-2222-4e5f-8a9b-ba9876543210"
GUEST = "5e6f7a8b-3333-4c4d-8e9f-abcdefabcdef"
_TS = "2026-10-01T00:00:00+00:00"
_TS_EDITED = "2026-10-02T00:00:00+00:00"


# ───────────────────────────────────────────────────────────── the fake


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """A PostgREST-shaped builder over the fake store. Pages clamp at PAGE_SIZE."""

    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self.select_str = None
        self.eqs = []
        self.in_filter = None
        self.order_col = None
        self.rng = None

    def select(self, cols, *a, **k):
        self.select_str = cols
        return self

    def eq(self, col, val):
        self.eqs.append((col, val))
        return self

    def in_(self, col, vals):
        self.in_filter = (col, list(vals))
        return self

    def order(self, col, desc=False):
        self.order_col = (col, desc)
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def limit(self, n):
        self.rng = (0, n - 1)
        return self

    # ── execution ──

    def _kind(self):
        if self.table == "portfolios":
            return "portfolios"
        if self.table == "portfolio_items" and "portfolios" in (self.select_str or ""):
            return "items_joined"
        if self.table == "portfolio_items" and self.in_filter is not None:
            return "items_in"
        return f"other:{self.table}"

    def execute(self):
        kind = self._kind()
        with self.sb.lock:
            self.sb.log.append({
                "kind": kind, "select": self.select_str, "eqs": list(self.eqs),
                "in": self.in_filter, "order": self.order_col, "range": self.rng,
            })
            first_of_kind = kind not in self.sb.seen_kinds
            self.sb.seen_kinds.add(kind)
            self.sb.started += 1
        if self.sb.barrier is not None and first_of_kind and kind in ("portfolios", "items_joined"):
            self.sb.barrier.wait()          # BrokenBarrierError when the reads are serial
        if self.sb.block is not None:
            self.sb.block.wait(5)
        exc = self.sb.fail.get(kind)
        if exc is not None:
            raise exc
        if kind == "portfolios":
            data = self._portfolios()
        elif kind == "items_joined":
            data = self._items_joined()
        elif kind == "items_in":
            col, vals = self.in_filter
            data = [dict(r) for r in self.sb.items if r.get(col) in vals]
        else:
            data = []
        if self.order_col:
            col, desc = self.order_col
            data.sort(key=lambda r: str(r.get(col)), reverse=desc)
        if self.rng is None:
            page = data[:PAGE_SIZE]
        else:
            a, b = self.rng
            page = data[a:min(b, a + PAGE_SIZE - 1) + 1]
        return _Resp(page)

    def _portfolios(self):
        want = {c: str(v).lower() for c, v in self.eqs}
        return [
            dict(r) for r in self.sb.groups
            if all(str(r.get(c)).lower() == v for c, v in want.items())
        ]

    def _items_joined(self):
        """Models PostgREST: `!inner` + a filter on the embed filters the ITEMS; a LEFT
        embed + the same filter only nulls the embed; an ignored filter returns all."""
        inner = "portfolios!inner(" in (self.select_str or "") and not self.sb.drop_inner
        owner_filter = next((v for c, v in self.eqs if c == "portfolios.user_id"), None)
        if not self.sb.honour_owner_filter:
            owner_filter = None
        owner_of = {g["id"]: g["user_id"] for g in self.sb.groups}
        owner_of.update(self.sb.extra_owners)
        out = []
        for r in self.sb.items + self.sb.joined_extra:
            owner = owner_of.get(r["portfolio_id"])
            row = dict(r)
            matches = owner_filter is None or str(owner).lower() == str(owner_filter).lower()
            if matches:
                row["portfolios"] = {"user_id": owner}
            elif inner:
                continue
            else:
                row["portfolios"] = None
            out.append(row)
        return out


class _SB:
    def __init__(self, groups, items, *, barrier=False, fail=None, honour_owner_filter=True,
                 drop_inner=False, joined_extra=None, extra_owners=None, block=None):
        self.groups = groups
        self.items = items
        self.barrier = threading.Barrier(2, timeout=1.0) if barrier else None
        self.fail = dict(fail or {})
        self.honour_owner_filter = honour_owner_filter
        self.drop_inner = drop_inner
        self.joined_extra = list(joined_extra or [])
        self.extra_owners = dict(extra_owners or {})
        self.block = block
        self.lock = threading.Lock()
        self.log = []
        self.seen_kinds = set()
        self.started = 0

    def table(self, name):
        return _Query(self, name)

    def calls(self, kind):
        return [c for c in self.log if c["kind"] == kind]


def _group(gid, owner, name, order, active=False):
    return {"id": gid, "user_id": owner, "name": name, "sort_order": order,
            "is_active": active, "created_at": _TS, "updated_at": _TS_EDITED}


def _items(gid, n, prefix, *, id_base=0):
    """`n` items of one group. Ids run OPPOSITE to position, so only the Python
    position sort puts them in order (the read itself pages on id)."""
    return [
        {"id": f"i{id_base + (n - i):06d}", "portfolio_id": gid, "ticker": f"{prefix}{i:04d}",
         "position": i, "shares": float(i) if i % 3 == 0 else None,
         "market_value": None}
        for i in range(n)
    ]


def _store(n_a=4, n_b=2, n_foreign=3):
    groups = [
        _group("pb", UID, "Tech", 1),
        _group("pa", UID, "Holdings", 0, active=True),
        _group("px", OTHER, "Holdings", 0, active=True),
    ]
    items = (_items("pa", n_a, "A", id_base=0)
             + _items("pb", n_b, "B", id_base=100_000)
             + _items("px", n_foreign, "X", id_base=200_000))
    return groups, items


def _shape(portfolios):
    return [(p.id, p.name, [(i.ticker, i.shares, i.market_value) for i in p.items])
            for p in portfolios]


def _serial_shape(groups, items, user_id=UID):
    """What the unchanged serial read answers on the same data — the reference."""
    return _shape(pf._fetch_user_portfolios(_SB(copy.deepcopy(groups), copy.deepcopy(items)), user_id))


def _records(caplog, level, needle):
    return [r for r in caplog.records if r.levelno == level and needle in r.getMessage()]


def _info_on(mod, caplog):
    """Capture INFO from the module under test (a mutant copy logs under its own name)."""
    return caplog.at_level(logging.INFO, logger=mod.logger.name)


class _Abort(BaseException):
    """A non-Exception BaseException (the CancelledError class) from the joined read."""


async def _list(mod, sb, user_id=UID):
    result = await mod.list_portfolios(user={"id": user_id}, supabase=sb)
    return result.portfolios


# ───────────────────────────────────────────────────────────── scenarios
#
# Each takes the module under test, so the MUTATIONS table can run it against a
# regressed copy of portfolios.py.


async def _scenario_reads_overlap(mod, caplog, monkeypatch):
    groups, items = _store()
    sb = _SB(groups, items, barrier=True)
    with caplog.at_level(logging.WARNING):
        out = await _list(mod, sb)
    assert _shape(out) == _serial_shape(groups, items)
    assert len(sb.calls("portfolios")) == 1
    assert sb.calls("items_in") == [], "the parallel path must not need the .in_ read"
    assert not _records(caplog, logging.WARNING, "serial fallback")


async def _scenario_pages_past_clamp_in_position_order(mod, caplog, monkeypatch):
    groups, items = _store(n_a=PAGE_SIZE + 30, n_b=20, n_foreign=5)
    assert len([i for i in items if i["portfolio_id"] in ("pa", "pb")]) == 1_050
    sb = _SB(groups, items)
    out = await _list(mod, sb)

    assert [p.id for p in out] == ["pa", "pb"], "groups keep sort_order"
    assert len(out[0].items) == PAGE_SIZE + 30, "the tail past the clamp was dropped"
    assert [i.ticker for i in out[0].items] == [f"A{i:04d}" for i in range(PAGE_SIZE + 30)]
    assert [i.ticker for i in out[1].items] == [f"B{i:04d}" for i in range(20)]
    assert out[0].items[3].shares == 3.0 and out[0].items[4].shares is None
    assert not any(i.ticker.startswith("X") for p in out for i in p.items)
    joined = sb.calls("items_joined")
    assert [c["range"] for c in joined] == [(0, PAGE_SIZE - 1), (PAGE_SIZE, 2 * PAGE_SIZE - 1)]
    assert all(c["order"] == ("id", False) for c in joined), "paging must order on the unique id"
    assert sb.calls("items_in") == []
    assert _shape(out) == _serial_shape(groups, items)


async def _scenario_wire_shape(mod, caplog, monkeypatch):
    groups, items = _store()
    sb = _SB(groups, items)
    await _list(mod, sb)
    joined = sb.calls("items_joined")
    assert joined, "no joined read was made"
    sel = joined[0]["select"].replace(" ", "")
    assert "portfolios!inner(user_id)" in sel, sel
    for col in ("id", "portfolio_id", "ticker", "position", "shares", "market_value"):
        assert col in sel.split(","), f"{col} missing from {sel}"
    assert ("portfolios.user_id", UID) in joined[0]["eqs"], joined[0]["eqs"]
    assert ("user_id", UID) in sb.calls("portfolios")[0]["eqs"]


async def _scenario_fallback_reuses_rows(mod, caplog, monkeypatch):
    groups, items = _store(n_a=PAGE_SIZE + 5)
    sb = _SB(groups, items, fail={"items_joined": RuntimeError("PGRST200 relationship not found")})
    with caplog.at_level(logging.WARNING):
        out = await _list(mod, sb)
    assert _shape(out) == _serial_shape(groups, items)
    warns = _records(caplog, logging.WARNING, "serial fallback")
    assert len(warns) == 1 and UID in warns[0].getMessage()
    assert "RuntimeError" in warns[0].getMessage()
    assert len(sb.calls("portfolios")) == 1, "the fallback re-read the portfolios table"
    in_reads = sb.calls("items_in")
    assert [c["range"] for c in in_reads] == [(0, PAGE_SIZE - 1), (PAGE_SIZE, 2 * PAGE_SIZE - 1)]
    assert sorted(in_reads[0]["in"][1]) == ["pa", "pb"], "the fallback must ask for the rows already read"


async def _scenario_foreign_rows_stop_on_page_0(mod, caplog, monkeypatch):
    # The filter is IGNORED: every user's items come back, owners embedded. Enough foreign
    # rows that an un-stopped read would page twice.
    groups, items = _store(n_a=5, n_b=3, n_foreign=PAGE_SIZE + 200)
    sb = _SB(groups, items, honour_owner_filter=False)
    with caplog.at_level(logging.WARNING):
        out = await _list(mod, sb)
    errors = _records(caplog, logging.ERROR, "!inner filter not applied")
    assert len(errors) == 1 and "serial fallback" in errors[0].getMessage()
    assert len(sb.calls("items_joined")) == 1, "a foreign page must stop the read on that page"
    assert not any(i.ticker.startswith("X") for p in out for i in p.items)
    assert _shape(out) == _serial_shape(groups, items)
    assert len(sb.calls("portfolios")) == 1


async def _scenario_null_embed_is_foreign(mod, caplog, monkeypatch):
    # `!inner` silently dropped → a LEFT embed: foreign rows come back with `portfolios: null`.
    groups, items = _store()
    sb = _SB(groups, items, drop_inner=True)
    with caplog.at_level(logging.WARNING):
        out = await _list(mod, sb)
    assert _records(caplog, logging.ERROR, "!inner filter not applied")
    assert sb.calls("items_in"), "the null embed was trusted instead of falling back"
    assert _shape(out) == _serial_shape(groups, items)


async def _scenario_owner_match_is_case_insensitive(mod, caplog, monkeypatch):
    groups, items = _store()
    sb = _SB(groups, items)
    with caplog.at_level(logging.WARNING):
        out = await _list(mod, sb, user_id=UID.upper())
    assert not _records(caplog, logging.ERROR, "!inner filter not applied")
    assert sb.calls("items_in") == [], "an upper-case owner forced a needless fallback"
    assert _shape(out) == _serial_shape(groups, items)


async def _scenario_orphans_skipped(mod, caplog, monkeypatch):
    # Two items of a group the caller owns but the `portfolios` read did not see (created
    # between the reads).
    groups, items = _store()
    orphans = _items("pnew", 2, "N", id_base=300_000)
    sb = _SB(groups, items, joined_extra=orphans, extra_owners={"pnew": UID})
    with caplog.at_level(logging.WARNING):
        out = await _list(mod, sb)
    warns = _records(caplog, logging.WARNING, "skipped 2 portfolio_items")
    assert len(warns) == 1 and UID in warns[0].getMessage()
    assert not any(i.ticker.startswith("N") for p in out for i in p.items)
    assert _shape(out) == _serial_shape(groups, items)


async def _scenario_rows_failure_never_seeds(mod, caplog, monkeypatch):
    groups, items = _store()
    seeded = []
    monkeypatch.setattr(mod, "_seed_default_portfolio", lambda *a, **k: seeded.append(a))
    sb = _SB(groups, items, fail={"portfolios": RuntimeError("Error 520")})
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="Error 520"):
            await _list(mod, sb)
    assert seeded == [], "a failed portfolios read must never read as 'no groups' and seed"
    assert _records(caplog, logging.WARNING, "portfolios read failed")


async def _scenario_claim_between_reads_is_topped_up(mod, caplog, monkeypatch):
    # The joined read's snapshot predates `claim-guest-data` moving group "pg" to this user
    # (it still sees GUEST as the owner, so `!inner` drops pg's items); the portfolios
    # read's snapshot follows it (pg is in the rows). Served as-is, pg is EMPTY — and the
    # client's next whole-list PUT deletes its three real items.
    groups, items = _store()
    groups.append(_group("pg", UID, "Guest picks", 2))
    items = items + _items("pg", 3, "G", id_base=400_000)
    sb = _SB(groups, items, extra_owners={"pg": GUEST})
    with _info_on(mod, caplog):
        out = await _list(mod, sb)
    by_id = {p.id: [i.ticker for i in p.items] for p in out}
    assert by_id["pg"] == ["G0000", "G0001", "G0002"], "the claimed group was served EMPTY"
    assert _shape(out) == _serial_shape(groups, items)
    assert [sorted(c["in"][1]) for c in sb.calls("items_in")] == [["pg"]], (
        "the top-up must re-read exactly the groups with no joined item"
    )
    infos = _records(caplog, logging.INFO, "topped up 1 group(s)")
    assert len(infos) == 1 and UID in infos[0].getMessage(), [r.getMessage() for r in caplog.records]
    assert "found 3 item(s)" in infos[0].getMessage()
    assert not _records(caplog, logging.WARNING, "fallback")
    assert len(sb.calls("portfolios")) == 1


async def _scenario_empty_group_served_empty_after_top_up(mod, caplog, monkeypatch):
    # A LEGITIMATELY empty group next to a full one: one extra hop for the empty group only,
    # served empty, and the full group's items are not duplicated by the top-up.
    groups, items = _store(n_a=4, n_b=0)
    sb = _SB(groups, items)
    with _info_on(mod, caplog):
        out = await _list(mod, sb)
    assert [(p.id, len(p.items)) for p in out] == [("pa", 4), ("pb", 0)]
    assert _shape(out) == _serial_shape(groups, items)
    assert [sorted(c["in"][1]) for c in sb.calls("items_in")] == [["pb"]]
    infos = _records(caplog, logging.INFO, "topped up 1 group(s)")
    assert len(infos) == 1 and "found 0 item(s)" in infos[0].getMessage()


async def _scenario_top_up_skipped_when_every_group_has_items(mod, caplog, monkeypatch):
    groups, items = _store()
    sb = _SB(groups, items)
    with _info_on(mod, caplog):
        out = await _list(mod, sb)
    assert sb.calls("items_in") == [], "a needless top-up read when every group had items"
    assert not _records(caplog, logging.INFO, "topped up")
    assert _shape(out) == _serial_shape(groups, items)


async def _scenario_no_groups_failed_join_is_logged(mod, caplog, monkeypatch):
    seeded = []
    monkeypatch.setattr(mod, "_seed_default_portfolio", lambda sb, uid: seeded.append(uid))
    monkeypatch.setattr(mod, "_fetch_user_portfolios", lambda sb, uid: [])

    # (a) The owner filter is NOT honoured and the user has no group: the joined read sees
    # another user's items. That is the only trace of an unapplied `!inner` — one ERROR.
    sb = _SB([_group("px", OTHER, "Holdings", 0, active=True)], _items("px", 3, "X"),
             honour_owner_filter=False)
    with caplog.at_level(logging.WARNING):
        assert await _list(mod, sb) == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in caplog.records]
    msg = errors[0].getMessage()
    assert "!inner filter not applied" in msg and UID in msg, msg
    assert msg.endswith("— no groups, seeding"), msg
    assert seeded == [UID] and sb.calls("items_in") == []

    # (b) A plain failure with no groups: one WARNING, no ERROR, still seeds.
    caplog.clear()
    sb = _SB([], [], fail={"items_joined": RuntimeError("PGRST200 relationship not found")})
    with caplog.at_level(logging.WARNING):
        assert await _list(mod, sb) == []
    warns = _records(caplog, logging.WARNING, "joined items read failed")
    assert len(warns) == 1, [r.getMessage() for r in caplog.records]
    assert "RuntimeError" in warns[0].getMessage() and UID in warns[0].getMessage()
    assert warns[0].getMessage().endswith("— no groups, seeding")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert seeded == [UID, UID]

    # (c) No groups and a healthy joined read: nothing logged.
    caplog.clear()
    sb = _SB([], [])
    with caplog.at_level(logging.WARNING):
        assert await _list(mod, sb) == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def _scenario_base_exception_with_no_groups_raises(mod, caplog, monkeypatch):
    seeded = []
    monkeypatch.setattr(mod, "_seed_default_portfolio", lambda *a, **k: seeded.append(a))
    sb = _SB([], [], fail={"items_joined": _Abort("joined read aborted")})
    # Not `pytest.raises`: its DID-NOT-RAISE is a BaseException the mutation runner's
    # `except Exception` would not count as a kill.
    try:
        await _list(mod, sb)
    except _Abort:
        pass
    else:
        raise AssertionError("a BaseException from the joined read was taken as 'no groups'")
    assert seeded == [], "seeded on a BaseException"


# ───────────────────────────────────────────────────────────── the tests


@pytest.mark.asyncio
async def test_the_two_reads_overlap(caplog, monkeypatch):
    await _scenario_reads_overlap(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_the_joined_read_pages_1050_rows_in_position_order(caplog, monkeypatch):
    await _scenario_pages_past_clamp_in_position_order(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_the_joined_read_carries_inner_and_the_owner_filter(caplog, monkeypatch):
    await _scenario_wire_shape(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_failed_joined_read_falls_back_reusing_rows(caplog, monkeypatch):
    await _scenario_fallback_reuses_rows(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_an_ignored_owner_filter_stops_on_page_0_and_falls_back(caplog, monkeypatch):
    await _scenario_foreign_rows_stop_on_page_0(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_null_embed_counts_as_foreign(caplog, monkeypatch):
    await _scenario_null_embed_is_foreign(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_the_owner_match_is_case_insensitive(caplog, monkeypatch):
    await _scenario_owner_match_is_case_insensitive(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_unknown_group_items_are_skipped_with_one_warning(caplog, monkeypatch):
    await _scenario_orphans_skipped(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_failed_portfolios_read_raises_and_never_seeds(caplog, monkeypatch):
    await _scenario_rows_failure_never_seeds(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_group_claimed_between_the_reads_is_topped_up_not_served_empty(caplog, monkeypatch):
    await _scenario_claim_between_reads_is_topped_up(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_legitimately_empty_group_is_served_empty_after_one_top_up(caplog, monkeypatch):
    await _scenario_empty_group_served_empty_after_top_up(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_no_top_up_read_when_every_group_got_items(caplog, monkeypatch):
    await _scenario_top_up_skipped_when_every_group_has_items(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_no_groups_still_logs_a_failed_joined_read_before_seeding(caplog, monkeypatch):
    await _scenario_no_groups_failed_join_is_logged(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_base_exception_with_no_groups_raises_and_never_seeds(caplog, monkeypatch):
    await _scenario_base_exception_with_no_groups_raises(pf, caplog, monkeypatch)


@pytest.mark.asyncio
async def test_a_failed_top_up_read_raises_never_serves_the_group_empty(caplog, monkeypatch):
    """The top-up exists because an empty group is data loss downstream, so a failed top-up
    must fail the request (as the serial path's items read does), logged with the user."""
    groups, items = _store()
    groups.append(_group("pg", UID, "Guest picks", 2))
    items = items + _items("pg", 3, "G", id_base=400_000)
    sb = _SB(groups, items, extra_owners={"pg": GUEST},
             fail={"items_in": RuntimeError("Error 520 on top-up")})
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="Error 520 on top-up"):
            await _list(pf, sb)
    warns = _records(caplog, logging.WARNING, "top-up items read for 1 group(s) failed")
    assert len(warns) == 1 and UID in warns[0].getMessage()


@pytest.mark.asyncio
async def test_both_reads_failing_raises_the_portfolios_error(monkeypatch):
    groups, items = _store()
    seeded = []
    monkeypatch.setattr(pf, "_seed_default_portfolio", lambda *a, **k: seeded.append(a))
    sb = _SB(groups, items, fail={"portfolios": ValueError("rows down"),
                                  "items_joined": RuntimeError("join down")})
    with pytest.raises(ValueError, match="rows down"):
        await _list(pf, sb)
    assert seeded == [] and sb.calls("items_in") == []


@pytest.mark.asyncio
async def test_no_groups_still_seeds_then_refetches_serially(monkeypatch):
    """Boundary: rows [] → [] → the seed path runs exactly as before, and the re-fetch
    after seeding is the SERIAL read."""
    seeded, refetched = [], []
    monkeypatch.setattr(pf, "_seed_default_portfolio", lambda sb, uid: seeded.append(uid))

    def _refetch(sb, uid):
        refetched.append(uid)
        return []

    monkeypatch.setattr(pf, "_fetch_user_portfolios", _refetch)
    sb = _SB([_group("px", OTHER, "Holdings", 0, active=True)], _items("px", 3, "X"))
    out = await _list(pf, sb)
    assert out == []
    assert seeded == [UID] and refetched == [UID]


@pytest.mark.asyncio
async def test_a_user_with_groups_but_no_items_gets_empty_groups_after_one_top_up(caplog):
    """The joined read answering [] is not proof the groups are empty (a claim may have
    moved them between the snapshots), so ONE serial read of exactly those groups confirms
    it — the serial path's cost — and they are served empty. Not the fallback: no WARNING."""
    groups = [_group("pa", UID, "Holdings", 0, active=True), _group("pb", UID, "Tech", 1)]
    sb = _SB(groups, _items("px", 4, "X"))
    with _info_on(pf, caplog):
        out = await _list(pf, sb)
    assert [(p.id, p.items) for p in out] == [("pa", []), ("pb", [])]
    assert [sorted(c["in"][1]) for c in sb.calls("items_in")] == [["pa", "pb"]]
    assert not _records(caplog, logging.WARNING, "fallback")
    assert len(_records(caplog, logging.INFO, "topped up 2 group(s)")) == 1


@pytest.mark.asyncio
async def test_no_cache_every_call_reads_both_tables():
    groups, items = _store()
    sb = _SB(groups, items)
    first = await _list(pf, sb)
    sb.items.append({"id": "i999999", "portfolio_id": "pa", "ticker": "LATE", "position": 99,
                     "shares": None, "market_value": None})
    second = await _list(pf, sb)
    assert len(sb.calls("portfolios")) == 2 and len(sb.calls("items_joined")) == 2
    assert "LATE" not in [i.ticker for i in first[0].items]
    assert [i.ticker for i in second[0].items][-1] == "LATE"


@pytest.mark.asyncio
async def test_cancellation_propagates_without_a_fallback_or_a_seed(monkeypatch):
    """A client hang-up mid-read: CancelledError (a BaseException) must surface as itself —
    never be mistaken for a failed read to fall back on, nor for 'no groups' to seed."""
    seeded = []
    monkeypatch.setattr(pf, "_seed_default_portfolio", lambda *a, **k: seeded.append(a))
    groups, items = _store()
    release = threading.Event()
    sb = _SB(groups, items, block=release)
    task = asyncio.create_task(_list(pf, sb))
    try:
        for _ in range(500):
            if sb.started >= 2:
                break
            await asyncio.sleep(0.005)
        assert sb.started >= 2, "both reads should be in flight"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.sleep(0.05)       # let the worker threads drain
    assert seeded == [] and sb.calls("items_in") == []


@pytest.mark.parametrize("embed", [
    None, {}, [], [{"user_id": UID}], "x", {"user_id": None}, {"user_id": OTHER},
], ids=["null", "empty", "list", "list-of-one", "string", "null-owner", "foreign"])
def test_every_unprovable_embed_shape_fails_the_page(embed):
    rows = [{"id": "a", "portfolios": {"user_id": UID}}, {"id": "b", "portfolios": embed}]
    with pytest.raises(pf._JoinedItemsNotOwnerScoped):
        pf._check_joined_page_owner(rows, pf._owner_key(UID), "t", 0)


def test_a_page_without_the_embed_key_or_a_non_dict_row_fails():
    for rows in ([{"id": "a"}], ["not-a-row"], [None]):
        with pytest.raises(pf._JoinedItemsNotOwnerScoped):
            pf._check_joined_page_owner(rows, pf._owner_key(UID), "t", 0)


@pytest.mark.parametrize("batch", [None, []])
def test_an_empty_page_passes_the_owner_check(batch):
    pf._check_joined_page_owner(batch, pf._owner_key(UID), "t", 0)


def test_the_owner_check_ignores_case_and_whitespace():
    rows = [{"id": "a", "portfolios": {"user_id": f"  {UID.upper()} "}}]
    pf._check_joined_page_owner(rows, pf._owner_key(UID), "t", 0)


def test_assemble_keeps_the_position_sort_and_tolerates_a_null_position():
    rows = [_group("pa", UID, "Holdings", 0)]
    item_rows = [
        {"id": "3", "portfolio_id": "pa", "ticker": "C", "position": 2},
        {"id": "1", "portfolio_id": "pa", "ticker": "A", "position": None},
        {"id": "2", "portfolio_id": "pa", "ticker": "B", "position": 1},
    ]
    out = pf._assemble_portfolios(rows, item_rows, UID)
    assert [i.ticker for i in out[0].items] == ["A", "B", "C"]


def test_assemble_with_no_rows_and_orphans_only(caplog):
    with caplog.at_level(logging.WARNING):
        out = pf._assemble_portfolios([], [{"id": "1", "portfolio_id": "pz", "ticker": "Z"}], UID)
    assert out == []
    assert len(_records(caplog, logging.WARNING, "skipped 1 portfolio_items")) == 1


def test_the_real_postgrest_builder_puts_inner_and_the_filter_on_the_wire(monkeypatch):
    """The fake proves the logic; this proves the URL. `!inner` has no other caller in
    the repo, so the query string the real postgrest-py client builds is pinned here,
    offline (execute is replaced; nothing is sent)."""
    from postgrest import SyncPostgrestClient
    from postgrest._sync import request_builder as rb

    sent = []

    def _fake_execute(self):
        sent.append(str(self.params))
        return _Resp([])

    monkeypatch.setattr(rb.SyncQueryRequestBuilder, "execute", _fake_execute)
    with SyncPostgrestClient("http://127.0.0.1:9/rest/v1") as client:
        class _Real:
            def table(self, name):
                return client.from_(name)

        assert pf._read_user_item_rows_joined(_Real(), UID) == []
    assert len(sent) == 1
    q = sent[0]
    assert "portfolios%21inner%28user_id%29" in q, q
    assert f"portfolios.user_id=eq.{UID}" in q, q
    assert "order=id.asc" in q and "offset=0" in q and f"limit={PAGE_SIZE}" in q, q


# ───────────────────────────────────────────────────────────── mutations
#
# (name, old text in portfolios.py, replacement, the scenario that must fail on it).

_SRC_PATH = Path(pf.__file__)
_MUTANT_IDS = itertools.count()

MUTATIONS = [
    ("serial-first-read",
     'portfolios = await _fetch_user_portfolios_concurrent(supabase, user["id"])',
     'portfolios = (await asyncio.to_thread(_fetch_user_portfolios, supabase, user["id"]))',
     _scenario_reads_overlap),
    ("unpaged-joined-read",
     "        order_by=\"id\",\n        what=what,\n",
     "        order_by=\"id\",\n        what=what,\n        max_pages=1,\n",
     _scenario_pages_past_clamp_in_position_order),
    ("no-position-sort",
     "    ordered = sorted(\n"
     "        item_rows, key=lambda r: (str(r.get(\"portfolio_id\")), int(r.get(\"position\") or 0))\n"
     "    )\n",
     "    ordered = list(item_rows)\n",
     _scenario_pages_past_clamp_in_position_order),
    ("no-inner",
     '_JOINED_ITEMS_SELECT = "id,portfolio_id,ticker,position,shares,market_value,portfolios!inner(user_id)"',
     '_JOINED_ITEMS_SELECT = "id,portfolio_id,ticker,position,shares,market_value,portfolios(user_id)"',
     _scenario_wire_shape),
    ("no-owner-filter",
     '            .eq("portfolios.user_id", user_id),\n',
     "            ,\n",
     _scenario_wire_shape),
    ("no-fallback-on-error",
     "            \"serial fallback\",\n"
     "            user_id, type(joined_result).__name__, joined_result,\n"
     "        )\n",
     "            \"serial fallback\",\n"
     "            user_id, type(joined_result).__name__, joined_result,\n"
     "        )\n"
     "        raise joined_result\n",
     _scenario_fallback_reuses_rows),
    ("fallback-rereads-portfolios",
     "        item_rows = await asyncio.to_thread(\n"
     "            _read_items_for, supabase, user_id, [r[\"id\"] for r in rows]\n"
     "        )\n",
     "        return await asyncio.to_thread(_fetch_user_portfolios, supabase, user_id)\n",
     _scenario_fallback_reuses_rows),
    ("no-owner-check",
     "        _check_joined_page_owner(\n"
     "            getattr(response, \"data\", None), self._user_key, self._what, self._start\n"
     "        )\n",
     "",
     _scenario_foreign_rows_stop_on_page_0),
    ("null-embed-trusted",
     "        if owner is None:\n            unverifiable += 1\n",
     "        if owner is None:\n            continue\n",
     _scenario_null_embed_is_foreign),
    ("case-sensitive-owner",
     "    return str(user_id).strip().lower()\n",
     "    return str(user_id).strip()\n",
     _scenario_owner_match_is_case_insensitive),
    ("orphan-keyerror",
     '        bucket = by_portfolio.get(str(item.get("portfolio_id")))\n',
     '        bucket = by_portfolio[str(item.get("portfolio_id"))]\n',
     _scenario_orphans_skipped),
    ("rows-failure-as-empty",
     "        raise rows_result\n",
     "        return []\n",
     _scenario_rows_failure_never_seeds),
    ("no-top-up",
     "        item_rows = await _top_up_groups_without_items(supabase, user_id, rows, joined_result)\n",
     "        item_rows = joined_result\n",
     _scenario_claim_between_reads_is_topped_up),
    ("top-up-every-group",
     '    missing = [r["id"] for r in rows if str(r["id"]) not in with_items]\n',
     '    missing = [r["id"] for r in rows]\n',
     _scenario_empty_group_served_empty_after_top_up),
    ("top-up-always",
     "    if not missing:\n        return joined_rows\n",
     "",
     _scenario_top_up_skipped_when_every_group_has_items),
    ("no-groups-join-failure-unlogged",
     "    if not rows:\n        # Nothing to serve",
     "    if not rows:\n        return []\n        # Nothing to serve",
     _scenario_no_groups_failed_join_is_logged),
    ("no-groups-unscoped-as-warning",
     "            logger.error(\n"
     "                \"GET /portfolios: !inner filter not applied for user=%s (%s) — \"\n"
     "                \"no groups, seeding\",\n",
     "            logger.warning(\n"
     "                \"GET /portfolios: !inner filter not applied for user=%s (%s) — \"\n"
     "                \"no groups, seeding\",\n",
     _scenario_no_groups_failed_join_is_logged),
    ("base-exception-taken-as-no-groups",
     "    if isinstance(joined_result, BaseException) and not isinstance(joined_result, Exception):\n"
     "        raise joined_result\n",
     "",
     _scenario_base_exception_with_no_groups_raises),
]

_SCENARIOS = {
    _scenario_reads_overlap, _scenario_pages_past_clamp_in_position_order,
    _scenario_wire_shape, _scenario_fallback_reuses_rows,
    _scenario_foreign_rows_stop_on_page_0, _scenario_null_embed_is_foreign,
    _scenario_owner_match_is_case_insensitive, _scenario_orphans_skipped,
    _scenario_rows_failure_never_seeds,
    _scenario_claim_between_reads_is_topped_up,
    _scenario_empty_group_served_empty_after_top_up,
    _scenario_top_up_skipped_when_every_group_has_items,
    _scenario_no_groups_failed_join_is_logged,
    _scenario_base_exception_with_no_groups_raises,
}


def _load(src: str) -> types.ModuleType:
    """Execute `src` as a throwaway module. It is registered in `sys.modules` while it
    executes because pydantic resolves a model's field types through
    `sys.modules[cls.__module__]` — under the real module's name the copy's models would
    bind the REAL `PortfolioItemResponse` and reject the copy's instances."""
    name = f"_pf_mutant_{next(_MUTANT_IDS)}"
    mod = types.ModuleType(name)
    mod.__file__ = str(_SRC_PATH)
    sys.modules[name] = mod
    try:
        exec(compile(src, str(_SRC_PATH), "exec"), mod.__dict__)
    finally:
        del sys.modules[name]
    return mod


def test_every_scenario_has_a_mutation():
    assert {m[3] for m in MUTATIONS} == _SCENARIOS


@pytest.mark.parametrize("name,old,new,scenario", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_mutation_applies_to_exactly_one_site(name, old, new, scenario):
    assert _SRC_PATH.read_text(encoding="utf-8").count(old) == 1, (
        f"{name}: the mutation's anchor is gone or ambiguous — re-anchor it on the new code"
    )


@pytest.mark.asyncio
async def test_the_unmutated_source_passes_every_scenario(caplog, monkeypatch):
    """Control: the in-memory loader itself must not break the module."""
    mod = _load(_SRC_PATH.read_text(encoding="utf-8"))
    for scenario in _SCENARIOS:
        caplog.clear()
        await scenario(mod, caplog, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,old,new,scenario", MUTATIONS, ids=[m[0] for m in MUTATIONS])
async def test_every_mutation_is_killed(name, old, new, scenario, caplog, monkeypatch):
    src = _SRC_PATH.read_text(encoding="utf-8")
    assert src.count(old) == 1, name
    mod = _load(src.replace(old, new))
    caplog.clear()
    try:
        await scenario(mod, caplog, monkeypatch)
    except Exception:
        return
    pytest.fail(f"mutation {name!r} survived {scenario.__name__}")
