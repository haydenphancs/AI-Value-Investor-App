"""App-Exclusive Signals — the twice-a-day cadence, measured on CONTENT age.

The bug this guards (live until 2026-10-15): every memory stamp was the time a copy was
LOADED, not the time its content was built. The Home warmer refreshes the cards with
``get_signals(force=True)`` shortly before the memory tier expires, and a forced refresh
reloaded any unexpired Tier-2 ``signals_cache`` row and stamped it "now". The row's 24 h
expiry was the only thing that ever forced a rebuild, so each warm re-read the SAME row,
and the cards really changed about once every 24 h (≈24 h 40 min with the warm's lead),
while every age check reported them as minutes old.

The rules pinned here (``signals_service`` Caching / Degradation docstrings):
  • a Tier-2 row carries ``computed_at``; it is reused only while its content is younger
    than ``_SIGNALS_TIER2_REUSE_SECONDS`` (11.5 h), and memory keeps that stamp;
  • an older row goes into memory under its own stamp as the FALLBACK while the cards are
    rebuilt — never over newer good cards, and never when stamped in the future;
  • a rebuild that fails HOLDS the last good cards (unchanged, nothing persisted) and is
    retried no sooner than ``_SIGNALS_DEGRADED_TTL_SECONDS``, up to the 24 h content ceiling;
  • a healthy build writes Tier 2 with the same stamp memory holds.

Service tests with Tier 2 and ``_build`` stubbed on the instance — no network, no Supabase.
The Supabase parsing tests use a tiny chainable fake client.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.services.home_dashboard_service as hds
import app.services.signals_service as ssvc
from app.schemas.home_dashboard import SignalGroupResponse, SignalRowResponse, SignalsGroupResponse

KEY = ssvc._SIGNALS_CACHE_KEY
_LOGGER = "app.services.signals_service"


def _cards(sym, kind="congress"):
    return SignalsGroupResponse(**{
        kind: SignalGroupResponse(kind=kind, entries=[SignalRowResponse(rank=1, symbol=sym, value=3.0)])
    })


def _mem():
    return ssvc.SignalsService._cache.get(KEY)


def _seed(stamp, payload):
    ssvc.SignalsService._cache[KEY] = (stamp, payload)


def _reset():
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    ssvc.SignalsService._degraded_keys.clear()
    ssvc.SignalsService._held_until.clear()
    for task in list(ssvc.SignalsService._background):
        task.cancel()
    ssvc.SignalsService._background.clear()


async def _drain():
    """Let the background refreshes a stale-while-revalidate request kicked finish."""
    while ssvc.SignalsService._background:
        await asyncio.gather(*list(ssvc.SignalsService._background), return_exceptions=True)


class _Harness:
    """The service with its I/O replaced: Tier 2 answers ``row`` (None = miss), writes are
    recorded as ``(payload, computed_at)``, and ``_build`` runs ``build`` (healthy unless a
    test swaps it) and counts the calls."""

    def __init__(self, svc):
        self.svc = svc
        self.row = None
        self.reads = 0
        self.writes = []
        self.builds = 0
        self.fresh = _cards("FRESH")
        self.build = self._healthy

    async def _healthy(self):
        return self.fresh, frozenset()

    def read(self):
        self.reads += 1
        return self.row

    def write(self, result, computed_at):
        self.writes.append((result, computed_at))

    async def run_build(self):
        self.builds += 1
        return await self.build()


@pytest.fixture(autouse=True)
def harness(monkeypatch):
    _reset()
    svc = ssvc.SignalsService()
    h = _Harness(svc)
    monkeypatch.setattr(svc, "_read_supabase_cache", h.read)
    monkeypatch.setattr(svc, "_write_supabase_cache", h.write)
    monkeypatch.setattr(svc, "_build", h.run_build)
    yield h
    _reset()


async def _a_card_raised():
    return _cards("PARTIAL", kind="whale"), frozenset({"ceo"})


async def _the_build_raised():
    raise RuntimeError("FMP down")


async def _every_card_empty():
    return SignalsGroupResponse(), frozenset()


_FAILURES = {
    "a card raised": _a_card_raised,
    "the build raised": _the_build_raised,
    "every card empty": _every_card_empty,
}


def test_the_cadence_is_about_twice_a_day():
    assert ssvc._SIGNALS_MEM_TTL_SECONDS == 12 * 3600
    assert ssvc._SIGNALS_TIER2_REUSE_SECONDS == 11 * 3600 + 1800
    assert ssvc._SIGNALS_DEGRADED_TTL_SECONDS == 300
    assert ssvc._SIGNALS_STALE_SERVE_CEILING_SECONDS == 24 * 3600
    assert ssvc._SIGNALS_SUPABASE_TTL_HOURS == 24
    # A reused row is younger than memory's TTL, and nothing older than Tier 2 keeps is served.
    assert (ssvc._SIGNALS_TIER2_REUSE_SECONDS < ssvc._SIGNALS_MEM_TTL_SECONDS
            <= ssvc._SIGNALS_STALE_SERVE_CEILING_SECONDS <= ssvc._SIGNALS_SUPABASE_TTL_HOURS * 3600)


# ── 1. Tier 2 is reused only while its CONTENT is young ─────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("age", [3600, ssvc._SIGNALS_TIER2_REUSE_SECONDS - 60])
async def test_a_young_tier2_row_is_reused_under_its_own_stamp(harness, force, age):
    """The forced warm is the path that used to stamp a reloaded row "now"."""
    row, computed_at = _cards("ROW"), time.time() - age
    harness.row = (row, computed_at)
    r = await harness.svc.get_signals(force=force)
    assert r is row
    assert harness.builds == 0 and harness.writes == []
    stamp, cached = _mem()
    assert cached is row and stamp == computed_at, "memory must keep the row's computed_at, never now"


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [
    hds._SIGNALS_REFRESH_AHEAD_SECONDS,          # the warm's due age: the guard's fast path serves it
    ssvc._SIGNALS_MEM_TTL_SECONDS + 3600,        # past memory's TTL: the guard times out onto it
])
async def test_an_old_row_is_the_fallback_while_the_cards_are_rebuilt(harness, monkeypatch, caplog, age):
    """An older row is no longer reused (that was the once-a-day loop), but it is not thrown
    away either: until the rebuild lands, a dashboard request is served it, not a blank section."""
    monkeypatch.setattr(ssvc, "_SIGNALS_BUILD_TIMEOUT_SECONDS", 0.05)
    old, old_at = _cards("OLD"), time.time() - age
    harness.row = (old, old_at)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_build():
        started.set()
        await release.wait()
        return harness.fresh, frozenset()

    harness.build = slow_build
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        warm = asyncio.create_task(harness.svc.get_signals(force=True))
        await asyncio.wait_for(started.wait(), 1)
        stamp, cached = _mem()
        assert cached is old and stamp == old_at
        assert await harness.svc.get_signals_guarded() is old

        release.set()
        assert await warm is harness.fresh
        await asyncio.sleep(0)                   # let the guard's shielded joiner settle

    assert harness.builds == 1
    stamp, cached = _mem()
    assert cached is harness.fresh and stamp > old_at and time.time() - stamp < 60
    assert len(harness.writes) == 1
    written, computed_at = harness.writes[0]
    assert written is harness.fresh and computed_at == stamp
    assert any("kept as the fallback" in r.getMessage() for r in caplog.records)


# ── 2. A row never replaces newer cards; a row from the future is ignored ───────────

@pytest.mark.asyncio
async def test_a_stale_row_never_replaces_newer_good_cards(harness):
    now = time.time()
    a, a_at = _cards("A"), now - 13 * 3600       # past memory's TTL, so the warm rebuilds
    _seed(a_at, a)
    harness.row = (_cards("B"), now - 20 * 3600)
    seen = []

    async def failing():
        seen.append(_mem())
        raise RuntimeError("FMP down")

    harness.build = failing
    r = await harness.svc.get_signals(force=True)
    assert seen[0][0] == a_at and seen[0][1] is a, "the older row overwrote newer cards"
    assert r is a                                # and the HELD copy is the newer one
    assert _mem()[0] == a_at and _mem()[1] is a


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["degraded", "future-stamped"])
async def test_a_degraded_or_future_stamped_entry_gives_way_to_the_row(harness, entry):
    now = time.time()
    if entry == "degraded":
        _seed(now - 600, _cards("PARTIAL"))      # newer, but a degraded build is never "better"
        ssvc.SignalsService._degraded_keys.add(KEY)
    else:
        _seed(now + 3600, _cards("SKEW"))        # clock skew is never "newer"
    b, b_at = _cards("B"), now - 20 * 3600
    harness.row = (b, b_at)
    seen = []

    async def failing():
        seen.append(_mem())
        raise RuntimeError("FMP down")

    harness.build = failing
    r = await harness.svc.get_signals(force=True)
    assert seen[0][0] == b_at and seen[0][1] is b
    assert KEY not in ssvc.SignalsService._degraded_keys
    assert r is b                                # the adopted row is what the failed rebuild holds


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "the build raised"])
async def test_a_row_stamped_in_the_future_is_ignored_and_rebuilt(harness, caplog, failure):
    skewed_at = time.time() + 3600
    harness.row = (_cards("SKEW"), skewed_at)
    seen = []

    async def watching():
        seen.append(_mem())
        if failure:
            return await _FAILURES[failure]()
        return harness.fresh, frozenset()

    harness.build = watching
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        r = await harness.svc.get_signals(force=True)
    assert harness.builds == 1 and seen == [None], "a future-stamped row was adopted"
    if failure:
        # Not even as the fallback: with no good copy the section is empty, nothing cached.
        assert r == SignalsGroupResponse() and _mem() is None
    else:
        assert r is harness.fresh and _mem()[0] < skewed_at - 60
    assert any("in the future" in rec.getMessage() for rec in caplog.records)


# ── 3. A failed rebuild HOLDS the last good cards, retried every 300 s ──────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", list(_FAILURES))
@pytest.mark.parametrize("force", [False, True])
async def test_a_failed_rebuild_holds_the_last_good_cards(harness, caplog, force, failure):
    """With a 12 h TTL the old `_still_good_signals` (memory TTL only) would have swapped a
    partial build over cards that were still fine to show. They are held, as one payload."""
    good, good_at = _cards("GOOD"), time.time() - ssvc._SIGNALS_MEM_TTL_SECONDS - 60
    _seed(good_at, good)
    harness.build = _FAILURES[failure]

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        r = await harness.svc.get_signals(force=force)
        # A request is answered with the good copy at once; its refresh runs in the background.
        await _drain()
    assert r is good
    assert _mem()[0] == good_at and _mem()[1] is good, "the held entry must be left as it was"
    held = ssvc.SignalsService._held_until[KEY]
    assert abs(held - (time.time() + ssvc._SIGNALS_DEGRADED_TTL_SECONDS)) < 5
    assert KEY not in ssvc.SignalsService._degraded_keys
    assert harness.writes == [] and harness.builds == 1
    assert any("holding the last good cards" in rec.getMessage() for rec in caplog.records)

    # The retry gate: a request during the hold is served from memory — no build, no Tier-2 read.
    assert await harness.svc.get_signals() is good
    assert harness.builds == 1 and harness.reads == 1

    # Past the gate the next request is STILL answered with the held cards at once, and the
    # retry runs behind it (and, still failing, holds again).
    ssvc.SignalsService._held_until[KEY] = time.time() - 1
    assert await harness.svc.get_signals() is good
    await _drain()
    assert harness.builds == 2
    assert ssvc.SignalsService._held_until[KEY] > time.time()
    assert harness.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", list(_FAILURES))
@pytest.mark.parametrize("force", [False, True])
async def test_past_the_ceiling_nothing_is_held(harness, force, failure):
    old, old_at = _cards("OLD"), time.time() - ssvc._SIGNALS_STALE_SERVE_CEILING_SECONDS - 60
    _seed(old_at, old)
    harness.build = _FAILURES[failure]
    r = await harness.svc.get_signals(force=force)
    assert r is not old and KEY not in ssvc.SignalsService._held_until
    assert harness.writes == []
    if failure == "a card raised":
        # The other cards show, from memory only, marked degraded (5 min, never persisted).
        assert r.whale is not None and r.whale.entries[0].symbol == "PARTIAL"
        assert _mem()[1] is r and time.time() - _mem()[0] < 60
        assert KEY in ssvc.SignalsService._degraded_keys
    else:
        assert r == SignalsGroupResponse()
        assert _mem()[0] == old_at and _mem()[1] is old, "an empty result must cache nothing"
        assert KEY not in ssvc.SignalsService._degraded_keys


@pytest.mark.asyncio
@pytest.mark.parametrize("mark", ["held", "degraded"])
async def test_a_healthy_build_clears_the_hold_and_the_degraded_mark(harness, mark):
    if mark == "held":
        _seed(time.time() - ssvc._SIGNALS_MEM_TTL_SECONDS - 60, _cards("GOOD"))
        harness.build = _the_build_raised
        await harness.svc.get_signals()
        await _drain()
        assert KEY in ssvc.SignalsService._held_until
    else:
        harness.build = _a_card_raised
        await harness.svc.get_signals()
        assert KEY in ssvc.SignalsService._degraded_keys

    harness.build = harness._healthy
    r = await harness.svc.get_signals(force=True)  # the warmer retries a held/degraded section
    assert r is harness.fresh
    assert KEY not in ssvc.SignalsService._held_until
    assert KEY not in ssvc.SignalsService._degraded_keys
    stamp, cached = _mem()
    assert cached is harness.fresh
    assert len(harness.writes) == 1 and harness.writes[0][0] is harness.fresh
    assert harness.writes[0][1] == stamp


# ── 4. A future-stamped memory entry is never "fresh" ───────────────────────────────

@pytest.mark.asyncio
async def test_a_future_stamped_memory_entry_is_never_served(harness):
    svc = harness.svc
    skewed, at = _cards("SKEW"), time.time() + 3600
    _seed(at, skewed)
    tol = ssvc._SIGNALS_FUTURE_STAMP_TOLERANCE_SECONDS
    assert svc._servable_from_memory(at - tol) is None             # age == -5 s: skew
    assert svc._servable_from_memory(at - tol + 0.5) is skewed     # inside the tolerance
    ssvc.SignalsService._held_until[KEY] = at + 3600               # not even when "held"
    assert svc._servable_from_memory(at - tol) is None
    assert svc._good_copy(at - tol) is None                        # nor held by a failed rebuild

    r = await svc.get_signals()
    assert r is harness.fresh and harness.builds == 1
    assert _mem()[1] is harness.fresh and _mem()[0] < at - 60


# ── 5. Tier-2 parsing (a tiny chainable Supabase fake) ──────────────────────────────

class _FakeQuery:
    def __init__(self, sb):
        self._sb = sb

    def select(self, cols):
        self._sb.selects.append(cols)
        return self

    def eq(self, col, value):
        self._sb.eqs.append((col, value))
        return self

    def gt(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def upsert(self, row, on_conflict=None):
        self._sb.upserts.append((row, on_conflict))
        return self

    def execute(self):
        class _R:
            pass
        r = _R()
        r.data = list(self._sb.rows)
        return r


class _FakeSupabase:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.tables, self.selects, self.eqs, self.upserts = [], [], [], []

    def table(self, name):
        self.tables.append(name)
        return _FakeQuery(self)


_T = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc).timestamp()


def _row(data=None, **cols):
    return {"data": json.loads(_cards("ROW").model_dump_json()) if data is None else data, **cols}


@pytest.fixture
def fake_sb(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(ssvc, "get_supabase", lambda: fake)
    return fake


@pytest.mark.parametrize("computed_at", ["2026-10-15T08:00:00+00:00", "2026-10-15T08:00:00Z"])
def test_read_returns_the_payload_and_its_computed_at(fake_sb, computed_at):
    # expires_at is deliberately NOT computed_at + 24 h, so the stamp provably comes from
    # computed_at (the expires_at fallback would answer two hours later).
    fake_sb.rows = [_row(computed_at=computed_at, expires_at="2026-10-16T10:00:00+00:00")]
    payload, stamp = ssvc.SignalsService()._read_supabase_cache()
    assert payload == _cards("ROW")
    assert stamp == pytest.approx(_T, abs=1e-6)
    assert fake_sb.tables == ["signals_cache"]
    assert "computed_at" in fake_sb.selects[0]
    assert ("cache_key", "signals_v7") in fake_sb.eqs


def test_read_dates_an_unreadable_computed_at_from_expires_at(fake_sb, caplog):
    fake_sb.rows = [_row(computed_at="not-a-date", expires_at="2026-10-16T08:00:00+00:00")]
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        _, stamp = ssvc.SignalsService()._read_supabase_cache()
    assert stamp == pytest.approx(_T, abs=1e-6)          # expires_at − 24 h
    assert any("dated from expires_at" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("row", [
    _row(computed_at=None, expires_at="garbage"),         # neither stamp readable → miss
    _row(expires_at=None),                                # no computed_at column at all
    _row(data={"congress": 42}, computed_at="2026-10-15T08:00:00+00:00",
         expires_at="2026-10-16T08:00:00+00:00"),         # a payload that fails validation
])
def test_read_is_a_miss_rather_than_a_made_up_age(fake_sb, row):
    fake_sb.rows = [row]
    assert ssvc.SignalsService()._read_supabase_cache() is None


def test_read_with_no_unexpired_row_is_a_miss(fake_sb):
    assert ssvc.SignalsService()._read_supabase_cache() is None


def test_write_stamps_computed_at_and_expiry_from_the_build_time(fake_sb):
    built_at = _T + 0.123456
    payload = _cards("W", kind="ceo")
    ssvc.SignalsService()._write_supabase_cache(payload, built_at)
    assert fake_sb.tables == ["signals_cache"] and len(fake_sb.upserts) == 1
    row, on_conflict = fake_sb.upserts[0]
    assert on_conflict == "cache_key" and row["cache_key"] == "signals_v7"
    assert abs(ssvc._iso_epoch(row["computed_at"]) - built_at) < 1e-3
    assert (datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["computed_at"])
            == timedelta(hours=24))
    assert SignalsGroupResponse.model_validate(row["data"]) == payload

    # And it reads back as the same content under the same age (a restart / a second process).
    fake_sb.rows = [row]
    read, stamp = ssvc.SignalsService()._read_supabase_cache()
    assert read == payload and abs(stamp - built_at) < 1e-3


# ── 6. `_iso_epoch` — PostgREST timestamps as text ──────────────────────────────────

_NOON = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("value, expected", [
    ("2026-10-15T12:00:00+00:00", _NOON),
    ("2026-10-15T12:00:00Z", _NOON),
    ("2026-10-15T12:00:00z", _NOON),
    ("2026-10-15T12:00:00.123456+00:00", _NOON + 0.123456),
    ("2026-10-15T14:00:00+02:00", _NOON),                 # an offset is honoured
    ("2026-10-15T12:00:00", _NOON),                       # naive is UTC
    (" 2026-10-15T12:00:00Z ", _NOON),
    ("", None),
    (None, None),
    (123, None),
    ("garbage", None),
    ("2026-13-45T99:00:00Z", None),
])
def test_iso_epoch(value, expected):
    got = ssvc._iso_epoch(value)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, abs=1e-6)


# ── 4. A request never waits on a retry while it has good cards to show ────────────

@pytest.mark.asyncio
async def test_a_request_during_a_slow_retry_gets_the_held_cards_without_waiting(harness, monkeypatch):
    """The review's probe: held cards, retry gate lapsed, a rebuild that hangs on a slow FMP.
    Requests used to join that rebuild and sit out the whole 8 s guard before the guard's
    fallback handed them the same held cards — every ~300 s, for as long as the outage
    lasted. They are answered with the good copy at once; the retry runs behind them."""
    good, good_at = _cards("GOOD"), time.time() - ssvc._SIGNALS_MEM_TTL_SECONDS - 600
    _seed(good_at, good)
    ssvc.SignalsService._held_until[KEY] = time.time() - 1      # the retry gate has lapsed
    release = asyncio.Event()

    async def slow_failing_build():
        await release.wait()
        raise RuntimeError("FMP timed out")

    harness.build = slow_failing_build
    # Way under the guard: an answer that waited on the build could not make this.
    r = await asyncio.wait_for(harness.svc.get_signals(), 0.5)
    assert r is good
    for _ in range(5):
        await asyncio.sleep(0)
    assert harness.builds == 1 and KEY in ssvc.SignalsService._inflight, "the retry did not start"
    # More requests while it runs: answered at once too, and no second build.
    monkeypatch.setattr(ssvc, "_SIGNALS_BUILD_TIMEOUT_SECONDS", 5)
    assert await asyncio.wait_for(harness.svc.get_signals_guarded(), 0.5) is good
    assert await asyncio.wait_for(harness.svc.get_signals(), 0.5) is good
    assert harness.builds == 1

    release.set()
    await _drain()
    assert ssvc.SignalsService._held_until[KEY] > time.time(), "the failed retry re-arms the hold"
    assert _mem() == (good_at, good)


@pytest.mark.asyncio
async def test_a_stale_good_copy_is_served_while_a_healthy_refresh_runs_behind_it(harness):
    """Past the 12 h TTL (the warmer is off, or late) the request does not wait either: it
    gets the stale cards, and the next request gets the refreshed ones."""
    old, old_at = _cards("OLD"), time.time() - ssvc._SIGNALS_MEM_TTL_SECONDS - 60
    _seed(old_at, old)
    assert await harness.svc.get_signals() is old
    await _drain()
    assert harness.builds == 1 and _mem()[1] is harness.fresh
    assert await harness.svc.get_signals() is harness.fresh
    assert harness.builds == 1


@pytest.mark.asyncio
async def test_a_degraded_partial_entry_is_never_held_as_the_last_good_cards(harness):
    """Only a GOOD payload is held. A degraded one-card build in memory that outlived its
    300 s must not become "the last good cards" when the next rebuild fails — it would be
    served as the whole section for up to the 24 h ceiling."""
    partial, partial_at = _cards("PARTIAL", kind="whale"), time.time() - ssvc._SIGNALS_DEGRADED_TTL_SECONDS - 5
    _seed(partial_at, partial)
    ssvc.SignalsService._degraded_keys.add(KEY)
    harness.build = _the_build_raised

    r = await harness.svc.get_signals()
    await _drain()
    assert r is not partial and r == SignalsGroupResponse()
    assert KEY not in ssvc.SignalsService._held_until
    assert harness.builds == 1, "a degraded entry past its TTL must rebuild in the foreground"
