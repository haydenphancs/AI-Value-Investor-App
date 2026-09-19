"""Sender reads that used to be a single clamped page.

PostgREST clamps every answer to ~1,000 rows whatever `.limit()` asks for, and a
`.order(desc)` makes the loss deterministic. Two readers in the notification senders still
assumed one page was the whole answer after the paging sweep:

* `smart_money_sender._run_whale_phase` read the NEWEST 1,000 `whale_trades` since its
  cursor and advanced the cursor to the newest stamp — on a 13F deadline day the ~500 rows
  written first were never evaluated and never re-read.
* `profile_match_sender._tiers_for` handed every consented reader (now up to 5,000 after
  `_load_profiles` was paged) to ONE `.in_("id", …)`: past ~1,000 the clamp read readers
  as "free" (skipped), past the URL limit the whole pass 414'd and nobody was notified.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.notification_senders import profile_match_sender as pm
from app.services.notification_senders import smart_money_sender as sm
from app.utils.postgrest_paging import PAGE_SIZE


# ─────────────────────────────────────────────── whale phase pages ascending


class _Rows:
    """A whale_trades table that clamps every page to PAGE_SIZE, like PostgREST."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def table(self, name):
        assert name == "whale_trades"
        return _Q(self)


class _Q:
    def __init__(self, store):
        self.store = store
        self.since = None
        self.order_by = None
        self.desc = False
        self.rng = None

    def select(self, *a, **k): return self
    def gt(self, col, val): self.since = val; return self
    def order(self, col, desc=False): self.order_by, self.desc = col, desc; return self
    def range(self, a, b): self.rng = (a, b); return self
    def limit(self, n): self.rng = (0, n - 1); return self

    def execute(self):
        self.store.calls.append((self.order_by, self.desc, self.rng))
        rows = [r for r in self.store.rows if r["created_at"] > self.since]
        rows.sort(key=lambda r: r["created_at"], reverse=self.desc)
        a, b = self.rng
        b = min(b, a + PAGE_SIZE - 1)          # the server clamp
        class _R: pass
        r = _R(); r.data = rows[a:b + 1]; return r


def _rows(n, start):
    return [{"ticker": f"T{i}", "action": "buy", "amount": 1_000_000, "date": "2026-11-14",
             "created_at": (start + timedelta(seconds=i)).isoformat(), "whale_id": f"w{i % 7}",
             "whales": {"name": "Fund", "firm_name": "Fund", "data_source": "edgar_13f"}}
            for i in range(n)]


def test_the_whale_read_is_paged_on_the_unique_id_past_the_clamp():
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    store = _Rows(_rows(1500, start))
    since = start - timedelta(hours=1)
    import inspect
    src = inspect.getsource(sm._run_whale_phase)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))  # strip comments
    assert "fetch_all_rows(" in code and 'order_by="id"' in code
    assert "desc=True" not in code and ".limit(" not in code

    from app.utils.postgrest_paging import fetch_all_rows
    got = fetch_all_rows(
        lambda: store.table("whale_trades").select("x").gt("created_at", since.isoformat()),
        order_by="id", what="t", max_pages=sm.WHALE_PHASE_MAX_PAGES,
    )
    assert len(got) == 1500                       # nothing dropped past the clamp
    assert len(store.calls) == 2                  # two pages


@pytest.mark.asyncio
async def test_a_capped_whale_read_advances_to_just_below_the_last_stamp(monkeypatch):
    """The read is ordered by `created_at` first (then `id`), so a capped page set is the
    OLDEST rows since the cursor — and rows beyond the cap may share the LAST stamp read
    (`created_at` is per-transaction, so one bulk upsert stamps up to 600 rows alike).

    This test used to pin the opposite: "hold the cursor". Holding it PARKED the cursor
    forever — every run re-read the same oldest cap, nothing past it was ever reached and
    the 'remainder next run' the comment promised never happened (F17-2). Advancing to the
    highest stamp strictly BELOW the last one re-reads only the boundary tie group (the
    dedup claim makes that harmless) and never skips a row."""
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    since = start - timedelta(hours=1)
    full = _rows(sm.WHALE_PHASE_MAX_PAGES * PAGE_SIZE, start)
    monkeypatch.setattr(sm, "fetch_all_rows", lambda *a, **k: list(full))
    monkeypatch.setattr(sm, "_recent_whale_rows", lambda raw, cutoff_date: [])
    monkeypatch.setattr(sm, "get_supabase", lambda: SimpleNamespace(table=lambda name: None))
    sent, cursor = await sm._run_whale_phase(now=start + timedelta(hours=6), cursor=since)
    stamps = sorted({datetime.fromisoformat(r["created_at"]) for r in full})
    assert cursor == stamps[-2], "resume just below the last stamp read, not at it"
    assert since < cursor < stamps[-1]


@pytest.mark.asyncio
async def test_an_uncapped_whale_read_advances_to_the_newest_stamp(monkeypatch):
    start = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
    since = start - timedelta(hours=1)
    partial = _rows(37, start)
    monkeypatch.setattr(sm, "fetch_all_rows", lambda *a, **k: list(partial))
    monkeypatch.setattr(sm, "_recent_whale_rows", lambda raw, cutoff_date: [])
    monkeypatch.setattr(sm, "get_supabase", lambda: SimpleNamespace(table=lambda name: None))
    sent, cursor = await sm._run_whale_phase(now=start + timedelta(hours=6), cursor=since)
    assert cursor == datetime.fromisoformat(partial[-1]["created_at"])


# ─────────────────────────────────────────────── tier read is chunked


def test_tiers_are_read_in_url_safe_chunks_and_merged():
    seen = []

    class _Q:
        def __init__(self, ids): self.ids = ids
        def select(self, *a): return self
        def in_(self, col, ids): seen.append(list(ids)); return self
        def execute(self):
            class _R: pass
            r = _R()
            r.data = [{"id": i, "tier": ("pro" if int(i[1:]) % 2 else "free")} for i in seen[-1]]
            return r

    class _SB:
        def table(self, name): return _Q(None)

    ids = [f"u{i}" for i in range(1201)] + ["u0", ""]   # a duplicate and a blank
    with patch.object(pm, "get_supabase", lambda: _SB()):
        tiers = pm._tiers_for(ids)
    assert len(seen) == 7 and all(len(c) <= pm._TIER_BATCH for c in seen)
    assert sum(len(c) for c in seen) == 1201          # deduped, blank dropped
    assert len(tiers) == 1201
    assert tiers["u1"] == "pro" and tiers["u1200"] == "free"


def test_a_failed_chunk_only_defaults_its_own_users_to_free():
    class _Q:
        def __init__(self): self.ids = None
        def select(self, *a): return self
        def in_(self, col, ids): self.ids = list(ids); return self
        def execute(self):
            if "u250" in self.ids:
                raise RuntimeError("414 URI too long")
            class _R: pass
            r = _R(); r.data = [{"id": i, "tier": "pro"} for i in self.ids]; return r

    class _SB:
        def table(self, name): return _Q()

    ids = [f"u{i}" for i in range(400)]
    with patch.object(pm, "get_supabase", lambda: _SB()):
        tiers = pm._tiers_for(ids)
    assert tiers.get("u0") == "pro" and tiers.get("u199") == "pro"
    assert "u250" not in tiers                         # that chunk fell closed…
    assert len(tiers) == 200                           # …and only that chunk


def test_empty_input_makes_no_read():
    with patch.object(pm, "get_supabase", lambda: (_ for _ in ()).throw(AssertionError("read"))):
        assert pm._tiers_for([]) == {}
        assert pm._tiers_for(["", None]) == {}
