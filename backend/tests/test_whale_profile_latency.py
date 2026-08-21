"""Cold-build cost of a whale profile: bulk writes, schema floor, and the pre-warm.

A single production profile fetch took 37 seconds. The mechanism, measured:

  | path                            | Supabase RTTs | FMP | wall  |
  |---------------------------------|---------------|-----|-------|
  | Tier-1 (in-process)             | 1             | 0   | 0.2s  |
  | Tier-2 (whale_profile_cache)    | 2             | 0   | 1.2s  |
  | cold, snapshot exists           | 8             | 0   | 1.2s  |
  | cold, NO snapshot -> FMP path   | ~136          | ~37 | 20-40s|

The postgrest client is SYNCHRONOUS (`database.py`), so every `.execute()` blocks the
event loop — four of them sat inside `for` loops and accounted for ~110 of those ~136
round-trips. Bulking them is the fix; these tests pin that the loops stay gone.

They count OPERATIONS, not seconds: a timing assertion would be flaky, and the round-trip
count IS the cost on a 150-250ms link.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services import whale_service as wsvc
from app.services.whale_service import (
    WHALE_PROFILE_SCHEMA_FLOOR,
    WhaleService,
    _bulk_write_trades,
    warm_whale_profile,
)


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table):
        self._sb, self._table, self._verb = sb, table, "select"
        self._payload = None

    def insert(self, payload, *a, **k):
        self._verb, self._payload = "insert", payload
        return self

    def upsert(self, payload, *a, **k):
        self._verb, self._payload = "upsert", payload
        return self

    def update(self, payload, *a, **k):
        self._verb, self._payload = "update", payload
        return self

    def delete(self, *a, **k):
        self._verb = "delete"
        return self

    def select(self, *a, **k):
        self._verb = "select"
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        self._sb.ops.append((self._table, self._verb))
        if self._payload is not None:
            self._sb.payloads.append(((self._table, self._verb), self._payload))
        if self._sb.faults.get((self._table, self._verb)):
            raise self._sb.faults[(self._table, self._verb)].pop(0)
        return _Resp(self._sb.returns.get((self._table, self._verb), [{"id": "row-1"}]))


class _FakeSB:
    def __init__(self, returns=None, faults=None):
        self.ops, self.payloads = [], []
        self.returns = returns or {}
        self.faults = {k: list(v) for k, v in (faults or {}).items()}

    def table(self, name):
        return _Query(self, name)

    def count(self, table, verb):
        return sum(1 for t, v in self.ops if t == table and v == verb)


def _svc():
    return WhaleService.__new__(WhaleService)


# ── The four writers that were the 37 seconds ────────────────────────────────


def _sync(sb, *, holdings=None, sectors=None, groups=None):
    svc = _svc()
    asyncio.run(
        svc._sync_to_whale_tables(
            whale_id="w1",
            holdings=holdings or [],
            sectors=sectors or [],
            trade_groups=groups or [],
            behavior={},
            sentiment="",
            total_value=1.0,
            perf_data=[],
            whale={"id": "w1", "data_source": "13f"},
        )
    )
    return sb


def test_thirty_holdings_cost_one_write_not_thirty(monkeypatch):
    sb = _FakeSB()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)
    holdings = [
        {"ticker": f"T{i}", "company_name": f"Co{i}", "allocation": 1.0,
         "change_percent": 0.0}
        for i in range(30)
    ]
    _sync(sb, holdings=holdings)
    assert sb.count("whale_holdings", "insert") == 1, (
        f"expected ONE bulk insert, got {sb.count('whale_holdings', 'insert')}"
    )
    # The DELETE-then-write shape must survive: a partial failure has to replay from the
    # DELETE, never resume mid-way (whale_holdings_whale_id_ticker_key).
    assert sb.count("whale_holdings", "delete") == 1
    payload = [p for op, p in sb.payloads if op == ("whale_holdings", "insert")][0]
    assert isinstance(payload, list) and len(payload) == 30


def test_sectors_cost_one_write(monkeypatch):
    sb = _FakeSB()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)
    _sync(sb, sectors=[{"name": f"S{i}", "allocation": 5.0} for i in range(11)])
    assert sb.count("whale_sector_allocations", "insert") == 1
    assert sb.count("whale_sector_allocations", "delete") == 1


def test_fifty_trades_cost_one_write_per_group(monkeypatch):
    """This loop was the single largest contributor: 50 blocking round-trips PER GROUP."""
    sb = _FakeSB()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)
    group = {
        "date": "2026-06-30", "trade_count": 50, "net_action": "BOUGHT",
        "net_amount": 1.0, "summary": "s", "insights": [],
        "trades": [
            {"ticker": f"T{i}", "action": "BOUGHT", "trade_type": "Increased",
             "amount": 1.0, "date": "2026-06-30"}
            for i in range(50)
        ],
    }
    _sync(sb, groups=[group])
    assert sb.count("whale_trades", "upsert") + sb.count("whale_trades", "insert") == 1
    assert sb.count("whale_trades", "insert") == 0, "should upsert, not plain insert"


def test_the_trade_group_write_is_an_upsert_not_check_then_act(monkeypatch):
    """The old select-then-insert let a concurrent writer strand a filing's trades."""
    sb = _FakeSB()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)
    _sync(sb, groups=[{"date": "2026-06-30", "trade_count": 1, "net_action": "BOUGHT",
                       "net_amount": 1.0, "trades": []}])
    assert sb.count("whale_trade_groups", "upsert") == 1
    assert sb.count("whale_trade_groups", "insert") == 0


def test_no_execute_survives_inside_a_for_loop():
    """Structural guard. Counting ops proves today's shape; this proves the SHAPE — a new
    per-row write added later would reintroduce ~110 blocking round-trips."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("app/services/whale_service.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "execute"):
                offenders.append(sub.lineno)
    # Two are legitimate and inherent: one write per TRADE GROUP (bounded at 12), plus
    # its id read-back. Anything beyond that is a per-ROW write and must be bulked.
    assert len(set(offenders)) <= 2, (
        f"per-row .execute() inside a for-loop at lines {sorted(set(offenders))} — "
        "bulk it; the postgrest client is synchronous and each call blocks the loop"
    )


def test_trades_fall_back_to_insert_before_migration_143(monkeypatch):
    """Ships safely against a database where the unique index does not exist yet."""
    class _E(Exception):
        pass
    sb = _FakeSB(faults={("whale_trades", "upsert"): [
        _E("42P10: no unique or exclusion constraint matching the ON CONFLICT")
    ]})
    _bulk_write_trades(sb, [{"ticker": "AAPL"}])
    assert sb.count("whale_trades", "upsert") == 1
    assert sb.count("whale_trades", "insert") == 1


def test_a_real_trade_write_error_is_not_swallowed():
    class _E(Exception):
        pass
    sb = _FakeSB(faults={("whale_trades", "upsert"): [_E("connection reset")]})
    with pytest.raises(_E):
        _bulk_write_trades(sb, [{"ticker": "AAPL"}])
    assert sb.count("whale_trades", "insert") == 0, "must not blind-retry a real failure"


# ── Schema floor replaces the blanket startup wipe ───────────────────────────


def test_the_floor_is_not_in_the_future():
    """A future-dated floor makes even freshly-written rows fail freshness and turns the
    cache permanently cold — the documented CACHE_SCHEMA_FLOOR failure mode."""
    assert WHALE_PROFILE_SCHEMA_FLOOR <= datetime.now(timezone.utc), (
        "WHALE_PROFILE_SCHEMA_FLOOR is in the future; every row will read as stale"
    )


def test_the_floor_is_timezone_aware():
    """It is compared against an aware `cached_at`; a naive one raises TypeError."""
    assert WHALE_PROFILE_SCHEMA_FLOOR.tzinfo is not None


def test_startup_no_longer_wipes_the_whole_profile_cache():
    """The wipe fired on ANY restart — OOM, health flap, instance rotation — leaving all
    56 whales cold for reasons unrelated to a deploy."""
    import re
    from pathlib import Path

    src = Path("app/main.py").read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#.*$", "", src)
    assert "whale_profile_cache" not in src, (
        "main.py still touches whale_profile_cache at startup; invalidation belongs to "
        "WHALE_PROFILE_SCHEMA_FLOOR"
    )


# ── The pre-warmer ───────────────────────────────────────────────────────────


def test_a_warm_entry_is_skipped_without_taking_a_slot(monkeypatch):
    """Steady state must cost nothing."""
    wsvc._whale_profile_cache.clear()
    wsvc._cache_set(wsvc._whale_profile_cache, "profile:w1", object())
    called = []
    monkeypatch.setattr(
        WhaleService, "_get_whale_profile_ungated",
        lambda self, **kw: called.append(kw) or None,
    )
    asyncio.run(warm_whale_profile("w1"))
    assert called == [], "a fresh entry must not trigger a rebuild"
    wsvc._whale_profile_cache.clear()


def test_the_warmer_never_raises(monkeypatch):
    """It runs in a lifespan task; an exception there kills warming for the whole
    process lifetime."""
    wsvc._whale_profile_cache.clear()

    async def _boom(self, **kw):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(WhaleService, "_get_whale_profile_ungated", _boom)
    monkeypatch.setattr(WhaleService, "__init__", lambda self: None)
    asyncio.run(warm_whale_profile("w1"))          # must not raise
    asyncio.run(warm_whale_profile(""))            # empty id
    asyncio.run(warm_whale_profile(None))          # type garbage


def test_the_warmer_caches_the_UNREDACTED_profile(monkeypatch):
    """It must call the UNGATED builder. Warming through the tier-gated wrapper would
    cache a Free-redacted profile and serve it to paying users for 24h."""
    import inspect
    src = inspect.getsource(warm_whale_profile)
    assert "_get_whale_profile_ungated" in src
    assert "user_id=None" in src, "the cached artifact must be follow-state-free"


# ── a degraded build must never be cached ────────────────────────────────────
#
# `_build_whale_profile` returns a valid, fully-shaped object even when the snapshot read
# FAILED — the sections are simply empty. So "not None" is not evidence that it has data.
# Caching one pins an empty book for 24h; reproduced, it survives a restart and is served
# verbatim with no rebuild attempted. This is the "degraded builds must not be pinned in
# cache" rule from project_holders_parity_review.


def test_a_failed_snapshot_read_on_a_hydrated_whale_is_not_cached(monkeypatch):
    svc = WhaleService.__new__(WhaleService)
    sb = _FakeSB(returns={
        ("whales", "select"): [{
            "id": "w1", "name": "Warren Buffett", "data_source": "13f",
            "last_hydrated_at": "2026-08-19T02:00:00+00:00",   # HAS been hydrated
        }],
    })
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)

    async def _no_snapshot(self, whale_id, whale):
        return None            # FMP 429 / Supabase blip

    monkeypatch.setattr(WhaleService, "_get_or_process_latest", _no_snapshot)
    profile = asyncio.run(svc._build_whale_profile("w1", None))

    assert profile is not None, "the caller should still get something to render"
    assert svc._last_build_degraded is True, (
        "a hydrated whale with no readable snapshot must be flagged degraded"
    )


def test_a_never_hydrated_whale_is_NOT_degraded(monkeypatch):
    """Negative control. Mark Kelly has never traded, so an empty profile is the TRUTH
    for him and caching it is correct — flagging it degraded would disable his cache
    forever and rebuild on every single request."""
    svc = WhaleService.__new__(WhaleService)
    sb = _FakeSB(returns={
        ("whales", "select"): [{
            "id": "w2", "name": "Mark Kelly",
            "data_source": "congressional_senate",
            "last_hydrated_at": None,          # never hydrated
        }],
    })
    monkeypatch.setattr(wsvc, "get_supabase", lambda: sb)

    async def _no_snapshot(self, whale_id, whale):
        return None

    monkeypatch.setattr(WhaleService, "_get_or_process_latest", _no_snapshot)
    asyncio.run(svc._build_whale_profile("w2", None))
    assert svc._last_build_degraded is False


def test_the_cache_write_is_gated_on_the_degraded_flag():
    """Structural: the write site must consult the flag, not just `is not None`."""
    import inspect
    src = inspect.getsource(WhaleService._get_whale_profile_ungated)
    assert "_last_build_degraded" in src, "the cache write does not check for a degraded build"
    assert "not _degraded" in src or "not degraded" in src


# ── the pre-warmer must not block the loop in one contiguous run ─────────────


def test_the_prewarmer_is_sequential_not_gathered():
    """MEASURED: a gather over 56 whales produced an 18.2s CONTIGUOUS event-loop stall,
    identical at concurrency 1, 3 and 56 — the semaphore is inert because the build path
    has no genuine suspension point. Sequential + an explicit yield bounds the stall to
    one build."""
    import inspect
    import app.main as m

    src = inspect.getsource(m._run_whale_profile_pre_warmer)
    # Strip comments and docstrings FIRST. The explanation next to this code names
    # `asyncio.gather` in prose, so an un-stripped scan matches the comment and passes
    # even after the code is reverted — the exact vacuity trap this repo documents.
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#.*$", "", src)
    assert "asyncio.gather" not in src, (
        "the pre-warmer gathers all 56 warms — that is an ~18s contiguous loop stall "
        "180s after every deploy, worse than the cold builds it exists to remove"
    )
    assert "await asyncio.sleep(0)" in src, (
        "no explicit yield between builds; the loop never gets to poll I/O"
    )
    assert "for r in rows" in src


# ── the whales re-read must key on "a build ran", not on last_hydrated_at ────
#
# `last_hydrated_at` is written ONLY by scripts/hydrate_whales.py, never by
# `_sync_to_whale_tables`. So it answers "has the nightly job ever run for this whale",
# not "did a sync just run" — and it was wrong for exactly the case the guard exists for:
# a hydrated whale whose snapshot read fails, rebuilds, syncs, then has its fresh
# portfolio_value / return columns discarded. Reachable via ?force_refresh=true, which
# deletes the snapshot rows first.


def test_get_or_process_latest_reports_whether_it_served_from_storage(monkeypatch):
    svc = WhaleService.__new__(WhaleService)

    async def _stored(self, whale_id):
        return {"filing_period": "2026-Q2"}

    monkeypatch.setattr(WhaleService, "_read_from_supabase", _stored)
    snap, served = asyncio.run(
        svc._get_or_process_latest("w1", {"last_hydrated_at": "2026-08-19", "data_source": "13f"})
    )
    assert snap is not None and served is True, "a stored snapshot must report served=True"


def test_a_rebuild_reports_served_from_storage_false(monkeypatch):
    """The case the old guard missed: hydrated whale, snapshot read returns nothing, so a
    full rebuild + sync runs and the whales row IS rewritten."""
    svc = WhaleService.__new__(WhaleService)

    async def _none(self, whale_id):
        return None

    async def _built(self, whale_id, cik, whale=None):
        return {"filing_period": "2026-Q2", "rebuilt": True}

    monkeypatch.setattr(WhaleService, "_read_from_supabase", _none)
    monkeypatch.setattr(WhaleService, "_process_13f_path", _built)
    snap, served = asyncio.run(
        svc._get_or_process_latest(
            "w1", {"last_hydrated_at": "2026-08-19", "data_source": "13f", "cik": "1"}
        )
    )
    assert snap is not None
    assert served is False, (
        "a rebuild must report served=False so the caller re-reads the synced whales row"
    )


def test_the_reread_guard_no_longer_keys_on_last_hydrated_at():
    """Structural. `snapshot is not None` is ALSO wrong here — it is true on the
    served-from-storage path and would undo the optimization entirely."""
    import inspect
    src = inspect.getsource(WhaleService._build_whale_profile)
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#.*$", "", src)
    assert "if not served_from_storage:" in src, "the re-read guard is not keyed on a build"
    assert "if not _hydrated_before" not in src, "the old last_hydrated_at proxy is back"


# ── scheduler: visible skip, explicit ordering ──────────────────────────────


def test_todays_run_is_tracked_durably_not_inferred_from_the_boot_clock():
    """SUPERSEDES the old `test_the_boot_seed_skip_is_logged`.

    That test pinned a VISIBILITY-only mitigation: a process booting after 02:00 INFERRED
    that today's run had already happened (`last_full_run_date = _boot.date() if
    _boot.hour >= 2`) and merely logged that the inference might be wrong. It is wrong in
    exactly the case that hurts — a redeploy or OOM at 02:07, mid-run, boots a process
    that skips the REST OF THE DAY, leaving un-swept whales on yesterday's data with
    nothing downstream able to compensate (`_get_or_process_latest` prefers the stored
    snapshot whenever `last_hydrated_at` is set and never rebuilds on age).

    Migration 147 replaced the inference with a durable cross-instance claim, so the
    inference must now be ABSENT rather than merely logged.

    ⚠️ Comments are stripped first. The block explaining this change necessarily names
    the very token the assertion greps for, so an un-stripped scan would pass on prose
    after a revert.
    """
    import inspect
    import app.main as m

    src = inspect.getsource(m._run_whale_hydration_job)
    src_nc = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src_nc = re.sub(r"(?m)^\s*#.*$", "", src_nc)
    src_nc = re.sub(r"(?m)\s+#.*$", "", src_nc)

    assert "last_full_run_date" not in src_nc, (
        "the clock-inferred boot seed is back — a mid-run restart will again silently "
        "skip the rest of the day"
    )
    assert "claimed_scheduled_job(JOB_WHALE_HYDRATION_FULL)" in src_nc, (
        "the full sweep no longer runs under the durable claim"
    )
    assert "run.success = True" in src_nc, (
        "success is never recorded, so run_day never advances and the sweep would "
        "re-run on every hourly wake"
    )
    assert "run.items" in src_nc, (
        "the written-whale count is not recorded — 'ran and wrote nothing' becomes "
        "indistinguishable from 'never ran'"
    )


def test_the_prewarmer_waits_on_an_event_not_a_magic_sleep():
    """`sleep(180)` claimed to order the two lifespan tasks and did not: the politician
    sweep STARTS at t=120s and is unbounded, so the pre-warmer woke 60s into it and any
    whale swept afterwards had its just-written warm thrown away."""
    import inspect
    import app.main as m

    src = inspect.getsource(m._run_whale_profile_pre_warmer)
    src_nc = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src_nc = re.sub(r"(?m)^\s*#.*$", "", src_nc)
    src_nc = re.sub(r"(?m)\s+#.*$", "", src_nc)
    assert "asyncio.sleep(180)" not in src_nc, "still ordering on a magic sleep"
    assert "_politician_sweep_done" in src_nc and "wait_for" in src_nc
    assert "TimeoutError" in src_nc, (
        "no timeout fallback — a hung sweep would disable warming for the process lifetime"
    )


def test_the_sweep_event_is_set_even_when_the_sweep_fails():
    """It is an ORDERING signal, not a success signal. Gating it on success would let one
    bad sweep suppress warming forever."""
    import inspect
    import app.main as m

    src = inspect.getsource(m._run_whale_hydration_job)
    idx = src.index("_politician_sweep_done.set()")
    preceding = src[:idx]
    assert "finally:" in preceding[-400:], (
        "the event is not set from a finally — a failed sweep would never release the warmer"
    )
