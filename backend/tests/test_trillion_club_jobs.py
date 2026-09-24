"""Trillion-Dollar Club jobs — store, daily/weekly runs, scheduler, the lifespan spawn, the
isolation guard and the read-only preview script. HERMETIC: every FMP and Supabase call goes
to an in-memory fake; nothing reaches the network (the conftest blocks it anyway).

What is pinned, and why each one matters:

* the loops idle unless ``TRILLION_CLUB_JOBS_ENABLED`` (default False) — the tables do not
  exist until the owner applies migration 175;
* at most 3 claimed attempts per ET day (the ledger cannot count failures) and
  ``run.success`` only when EVERY stage succeeded;
* membership fails CLOSED (stored state kept, ``membership_checked_at`` not advanced) and a
  published company not refreshed for 3 days logs ``trillion club STALE`` at ERROR;
* an FMP-sized company the owner FORCED in or out is stamped only when a usable close was
  read (``evaluate_fmp_sized``), and FMP answering for 2+ companies with no usable close at
  all FAILS the stage (a same-day retry for an upstream soft outage);
* on an older 13F quarter only "FMP does not have it" / "blocked by a refused next quarter"
  wait for the next run; a failed Supabase write or a build error fails the run;
* discovery judges "unknown" on the RAW registry rows and folds share classes of one
  company into ONE row with ``symbol_aliases``;
* a hash-unchanged ``complete`` 13F is skipped; a changed quarter rebuilds the NEXT stored
  quarter first and is not written if that fails; ``FilingRefused`` / ``FilingUnavailable``
  write nothing;
* discovery inserts only UNPUBLISHED rows; the new-filer probe never enables ``use_13f``;
* nothing under ``app/services/trillion_club`` imports a notification sender / push / whale
  module or names a table other than ``trillion_club_*``;
* the preview never writes and never touches Supabase, and reproduces the M1 acceptance
  figures from the recorded fixtures.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import copy
import json
import logging
import math
import re
import subprocess
import sys
import types
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.database as database
from app.config import Settings, settings
from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException
from app.services import notification_jobs
from app.services.notification_jobs import ScheduledJobResult
from app.services.trillion_club import jobs as J
from app.services.trillion_club import rules
from app.services.trillion_club import scheduler as S
from app.services.trillion_club import store as ST
from app.services.trillion_club.builder import (
    BUILD_COMPLETE,
    BUILD_DEGRADED,
    MAX_ROWS,
    BuiltFiling,
    raw_hash_of,
)
from app.services.trillion_club.rules import MembershipState
from app.utils.market_hours import ET

BACKEND = Path(__file__).resolve().parents[1]
PKG = BACKEND / "app" / "services" / "trillion_club"
FIX = BACKEND / "tests" / "fixtures" / "trillion_club"
MIGRATION_175 = BACKEND / "database" / "migrations" / "175_trillion_club.sql"

NOW = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)          # 07:00 EDT, a Thursday
TODAY = date(2026, 9, 24)
LAST_CLOSE = date(2026, 9, 23)
CIK = "0000000001"
T = 1_000_000_000_000


# ── Builders for fake data ─────────────────────────────────────────────────────────────


def history(symbol, cap, n=260, end=LAST_CLOSE):
    """FMP-shaped dated closes, newest first, weekdays only."""
    rows, d = [], end
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append({"symbol": symbol, "date": d.isoformat(), "marketCap": cap})
        d -= timedelta(days=1)
    return rows


def company(slug, *, cap_symbol=None, cap_source="fmp_us", mode="auto", use_13f=False, ciks=(),
            card_kind="no_thirteen_f", published=True, is_member=False, checked_at=None,
            member_since=None, country="US", aliases=(), manual_as_of=None, display_name=None):
    return {
        "slug": slug, "display_name": display_name or slug.title(), "ciks": list(ciks),
        "card_kind": card_kind, "use_13f": use_13f,
        "cap_symbol": cap_symbol if cap_source != "manual" else None,
        "symbol_aliases": list(aliases), "detail_symbol": cap_symbol, "logo_symbol": cap_symbol,
        "home_country": country, "cap_source": cap_source,
        "manual_cap_usd": 1.5e12 if cap_source == "manual" else None,
        "manual_cap_as_of": manual_as_of, "membership_mode": mode, "is_member": is_member,
        "member_since": member_since, "last_market_cap": None, "last_cap_date": None,
        "closes_at_or_above": 0, "closes_below": 0,
        "membership_checked_at": checked_at.isoformat() if checked_at else None,
        "link_whale": card_kind == "whale_link", "published": published,
    }


def filer(slug="filer", cik=CIK, **kw):
    kw.setdefault("cap_symbol", "FILR")
    return company(slug, use_13f=True, ciks=(cik,), card_kind="thirteen_f", **kw)


def xrow(y, q, cusip, symbol, shares, value, *, cik=CIK, acc=None, filed=None, name=None):
    period_end = rules.quarter_end(y, q)
    filed = filed or (period_end + timedelta(days=44)).isoformat()
    acc = acc or f"{cik}-{str(y)[2:]}-{q:06d}"
    folder = acc.replace("-", "")
    return {
        "cik": cik, "date": period_end.isoformat(), "filingDate": filed, "acceptedDate": filed,
        "link": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{acc}-index.htm",
        "nameOfIssuer": name or f"{symbol} CORP", "putCallShare": "", "securityCusip": cusip,
        "shares": shares, "sharesType": "SH", "symbol": symbol, "titleOfClass": "COM",
        "value": value,
    }


def book(y, q, *, extra=(), cik=CIK):
    """A small, valid two-position 13F for (y, q)."""
    return [xrow(y, q, "111111111", "AAA", 1000, 5_000_000, cik=cik),
            xrow(y, q, "222222222", "BBB", 2000, 3_000_000, cik=cik), *extra]


def dates_for(*periods):
    return [{"date": rules.quarter_end(y, q).isoformat(), "year": y, "quarter": q}
            for (y, q) in periods]


class FakeFMP:
    def __init__(self):
        self.history = {}                    # symbol -> rows | Exception
        self.dates = {}                      # cik -> rows | Exception
        self.extracts = {}                   # (cik, "YYYY-Qn") -> rows | Exception
        self.screener = []                   # rows | Exception
        self.batch = {}                      # symbol -> cap, or Exception
        self.missing_profiles = set()
        self.calls = defaultdict(list)

    async def get_historical_market_cap(self, symbol, from_date=None, to_date=None, limit=500):
        self.calls["history"].append((symbol, limit))
        self.calls["history_window"].append((from_date, to_date))
        v = self.history.get(symbol, [])
        if isinstance(v, BaseException):
            raise v
        rows = [r for r in v if (not from_date or r["date"] >= from_date)
                and (not to_date or r["date"] <= to_date)]
        # Real FMP: with no `from`, about three months comes back WHATEVER the limit
        # (probed 2026-09-24: AAPL limit=260 -> 64 rows).
        return copy.deepcopy(rows[:limit] if from_date else rows[:64])

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        assert strict is True, "the jobs must ask for 13F dates strictly"
        self.calls["dates"].append(cik)
        v = self.dates.get(cik, [])
        if isinstance(v, BaseException):
            raise v
        return copy.deepcopy(v)

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        assert strict is True, "the jobs must fetch 13F extracts strictly"
        key = f"{year}-Q{quarter}"
        self.calls["extract"].append((cik, key))
        v = self.extracts.get((cik, key), [])
        if isinstance(v, BaseException):
            raise v
        return copy.deepcopy(v)

    async def get_company_profiles_batch(self, symbols):
        self.calls["profiles"].append(list(symbols))
        return [{"symbol": s, "companyName": f"{s} Corp", "exchange": "NASDAQ",
                 "ipoDate": "2001-01-02", "sector": "Technology", "isActivelyTrading": True}
                for s in symbols if s not in self.missing_profiles]

    async def search_isin(self, isin):
        self.calls["isin"].append(isin)
        return []

    async def search_cusip(self, cusip):
        self.calls["cusip"].append(cusip)
        return []

    async def get_company_screener(self, **kw):
        self.calls["screener"].append(kw)
        if isinstance(self.screener, BaseException):
            raise self.screener
        return copy.deepcopy(self.screener)

    async def get_market_cap_batch(self, symbols):
        self.calls["batch"].append(list(symbols))
        if isinstance(self.batch, BaseException):
            raise self.batch
        return [{"symbol": s, "date": "2026-09-24", "marketCap": self.batch[s]}
                for s in symbols if s in self.batch]


class FakeActions:
    async def get_split_rows(self, t, from_date=None, to_date=None):
        return []

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, **kw):
        return False


class FakeStore:
    """Stand-in for TrillionClubStore (same method names), recording every write."""

    def __init__(self, companies=(), filings=None):
        self.companies = [dict(c) for c in companies]
        self.filings = {cik: {p: dict(r) for p, r in rows.items()} for cik, rows in (filings or {}).items()}
        self.membership_writes = []
        self.filing_writes = []
        self.inserted = []
        self.fail = {}                        # op -> Exception; ("upsert", cik, period) too

    async def read_companies(self):
        if "read_companies" in self.fail:
            raise self.fail["read_companies"]
        return copy.deepcopy(self.companies)

    async def update_membership(self, slug, state, *, checked_at):
        if ("update", slug) in self.fail:
            raise self.fail[("update", slug)]
        ST.membership_payload(state, checked_at=checked_at)      # the real validation
        self.membership_writes.append((slug, state, checked_at))

    async def read_filings(self, cik):
        if ("read_filings", cik) in self.fail:
            raise self.fail[("read_filings", cik)]
        return copy.deepcopy(self.filings.get(cik, {}))

    async def upsert_filing(self, row, *, built_at):
        key = ("upsert", row["cik"], row["period"])
        if key in self.fail:
            raise self.fail[key]
        json.dumps(row, allow_nan=False)                          # JSON-safe or it raises
        self.filing_writes.append(copy.deepcopy(row))
        self.filings.setdefault(row["cik"], {})[row["period"]] = {
            "raw_hash": row["raw_hash"], "build_status": row["build_status"],
            "unresolved": row["unresolved"], "built_at": built_at.isoformat()}

    async def insert_discovered(self, rows):
        if "insert" in self.fail:
            raise self.fail["insert"]
        self.inserted.extend(copy.deepcopy(list(rows)))
        return [r["slug"] for r in rows]


def written_periods(store):
    return [(r["cik"], r["period"]) for r in store.filing_writes]


def run(coro):
    return asyncio.run(coro)


def daily(fmp, store, now=NOW):
    return run(J.run_daily(now, fmp=fmp, db=store, actions=FakeActions()))


def weekly(fmp, store, now=NOW):
    return run(J.run_weekly(now, fmp=fmp, db=store, actions=FakeActions()))


@pytest.fixture(autouse=True)
def _no_read_cache_side_effects(monkeypatch):
    """The jobs invalidate the API service's Tier-1 cache after a write; keep that off the
    real class state of this test process and count the calls."""
    calls = []
    monkeypatch.setattr("app.services.trillion_club_service.invalidate", lambda: calls.append(1))
    return calls


# ═════════════════════════════════════════════════════════════════════════════════════
# 1. Flags
# ═════════════════════════════════════════════════════════════════════════════════════


def test_both_flags_default_off():
    assert Settings.model_fields["TRILLION_CLUB_JOBS_ENABLED"].default is False
    assert Settings.model_fields["TRILLION_CLUB_ENABLED"].default is False


@pytest.mark.asyncio
async def test_ticks_touch_nothing_while_the_jobs_flag_is_off(monkeypatch):
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", False)

    def boom(*a, **k):
        raise AssertionError("the ledger was touched with the flag off")

    monkeypatch.setattr(notification_jobs, "scheduled_job_state", boom)
    monkeypatch.setattr(notification_jobs, "claimed_scheduled_job", boom)
    monkeypatch.setattr(J, "run_daily", boom)
    monkeypatch.setattr(J, "run_weekly", boom)
    monday_9am = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
    assert await S._daily_tick(monday_9am) is None
    assert await S._weekly_tick(monday_9am) is None


# ═════════════════════════════════════════════════════════════════════════════════════
# 2. Scheduler — claim, kill switch, attempt cap, success
# ═════════════════════════════════════════════════════════════════════════════════════


class FakeLedger:
    def __init__(self, *, grant=True, enabled=True, unreadable=False):
        self.grant, self.enabled, self.unreadable = grant, enabled, unreadable
        self.run_day = None
        self.state_reads, self.claims, self.finished = 0, [], []

    def scheduled_job_state(self, job):
        self.state_reads += 1
        if self.unreadable:
            return None
        return {"job": job, "run_day": self.run_day, "claim_at": None, "enabled": self.enabled}

    def claimed_scheduled_job(self, job, *, timezone_name="UTC", stale_seconds=None):
        ledger = self

        @contextlib.asynccontextmanager
        async def cm():
            ledger.claims.append((job, timezone_name, stale_seconds))
            if not ledger.grant:
                yield None
                return
            res = ScheduledJobResult()
            try:
                yield res
            except BaseException as e:
                res.success, res.error = False, f"{type(e).__name__}: {e}"
                raise
            finally:
                ledger.finished.append((job, res.success, res.items, res.error))
                if res.success:
                    ledger.run_day = "2026-09-24"

        return cm()


@pytest.fixture
def ledger(monkeypatch):
    lg = FakeLedger()
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", True)
    monkeypatch.setattr(notification_jobs, "scheduled_job_state", lg.scheduled_job_state)
    monkeypatch.setattr(notification_jobs, "claimed_scheduled_job", lg.claimed_scheduled_job)
    monkeypatch.setattr(S, "_daily_attempts", {})
    monkeypatch.setattr(S, "_weekly_attempts", {})
    return lg


def _scripted_run(monkeypatch, name, outcomes):
    calls = []

    async def fake(now, **kw):
        calls.append(now)
        out = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(J, name, fake)
    return calls


@pytest.mark.asyncio
async def test_daily_success_records_items_and_ends_the_day(ledger, monkeypatch):
    calls = _scripted_run(monkeypatch, "run_daily", [{"ok": True, "items": 7, "failures": []}])
    assert await S._daily_tick(NOW) is None
    assert ledger.claims == [(S.JOB_TRILLION_CLUB_DAILY, "America/New_York", S._DAILY_STALE_SECONDS)]
    assert ledger.finished == [(S.JOB_TRILLION_CLUB_DAILY, True, 7, None)]
    # the ledger now says it ran today: the next wake neither claims nor runs
    assert await S._daily_tick(NOW + timedelta(hours=1)) is None
    assert len(ledger.claims) == 1 and len(calls) == 1


@pytest.mark.parametrize("summary", [
    {"ok": False, "items": 3, "failures": ["filings nvidia: unavailable"]},
    {"items": 3},                                  # no verdict at all
    {"ok": "yes", "items": 3},                     # truthy is not True
    None,
])
@pytest.mark.asyncio
async def test_success_needs_every_stage_ok(ledger, monkeypatch, summary):
    _scripted_run(monkeypatch, "run_daily", [summary])
    assert await S._daily_tick(NOW) == S.RETRY_SECONDS
    [(job, success, items, error)] = ledger.finished
    assert success is False and error, "a partial run must leave run_day unset, with a reason"
    if isinstance(summary, dict) and summary.get("failures"):
        assert "unavailable" in error


@pytest.mark.asyncio
async def test_attempt_cap_is_three_per_et_day(ledger, monkeypatch, caplog):
    calls = _scripted_run(monkeypatch, "run_daily", [{"ok": False, "failures": ["x"]}])
    waits = [await S._daily_tick(NOW + timedelta(hours=h)) for h in range(3)]
    assert waits == [S.RETRY_SECONDS, S.RETRY_SECONDS, None]
    assert any("all 3 attempts" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)
    # attempt 4 the same ET day does nothing at all
    assert await S._daily_tick(NOW + timedelta(hours=5)) is None
    assert len(calls) == 3 and len(ledger.claims) == 3
    # the next ET day starts fresh
    assert await S._daily_tick(NOW + timedelta(days=1)) == S.RETRY_SECONDS
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_a_raising_run_still_consumes_an_attempt(ledger, monkeypatch):
    calls = _scripted_run(monkeypatch, "run_daily", [RuntimeError("boom")])
    for h in range(3):
        with pytest.raises(RuntimeError):
            await S._daily_tick(NOW + timedelta(hours=h))
    assert await S._daily_tick(NOW + timedelta(hours=4)) is None
    assert len(calls) == 3
    assert [f[1] for f in ledger.finished] == [False, False, False]


@pytest.mark.asyncio
async def test_kill_switch_skips_without_claiming(ledger, monkeypatch):
    ledger.enabled = False
    calls = _scripted_run(monkeypatch, "run_daily", [{"ok": True}])
    assert await S._daily_tick(NOW) is None
    assert ledger.claims == [] and calls == []


@pytest.mark.asyncio
async def test_unreadable_state_fails_closed(ledger, monkeypatch):
    ledger.unreadable = True
    calls = _scripted_run(monkeypatch, "run_daily", [{"ok": True}])
    assert await S._daily_tick(NOW) == S.RETRY_SECONDS
    assert ledger.claims == [] and calls == []


@pytest.mark.asyncio
async def test_claim_held_elsewhere_does_not_use_an_attempt(ledger, monkeypatch):
    ledger.grant = False
    calls = _scripted_run(monkeypatch, "run_daily", [{"ok": True}])
    for h in range(5):
        assert await S._daily_tick(NOW + timedelta(minutes=h)) == S.RETRY_SECONDS
    assert calls == [] and S._daily_attempts.get(TODAY, 0) == 0


@pytest.mark.asyncio
async def test_not_due_before_seven_et(ledger, monkeypatch):
    calls = _scripted_run(monkeypatch, "run_daily", [{"ok": True}])
    assert await S._daily_tick(datetime(2026, 9, 24, 10, 59, tzinfo=timezone.utc)) is None
    assert ledger.state_reads == 0 and calls == []


@pytest.mark.asyncio
async def test_weekly_runs_on_monday_only(ledger, monkeypatch):
    calls = _scripted_run(monkeypatch, "run_weekly", [{"ok": True, "items": 1}])
    tuesday = datetime(2026, 9, 29, 13, 0, tzinfo=timezone.utc)
    monday_early = datetime(2026, 9, 28, 11, 59, tzinfo=timezone.utc)      # 07:59 EDT
    monday = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)             # 08:00 EDT
    assert await S._weekly_tick(tuesday) is None
    assert await S._weekly_tick(monday_early) is None
    assert calls == []
    assert await S._weekly_tick(monday) is None
    assert len(calls) == 1
    assert ledger.claims == [(S.JOB_TRILLION_CLUB_WEEKLY, "America/New_York", S._WEEKLY_STALE_SECONDS)]


def test_schedule_math():
    et = lambda *a: datetime(*a, tzinfo=ET)                              # noqa: E731
    assert S.next_daily_run(et(2026, 9, 24, 6, 0)) == et(2026, 9, 24, 7, 0)
    assert S.next_daily_run(et(2026, 9, 24, 7, 0)) == et(2026, 9, 25, 7, 0), "strictly after"
    # DST ends 2026-11-01: 07:00 EST is 12:00 UTC, not 11:00
    assert S.next_daily_run(et(2026, 10, 31, 8, 0)).astimezone(timezone.utc) == \
        datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert S.next_weekly_run(et(2026, 9, 24, 12, 0)) == et(2026, 9, 28, 8, 0)
    assert S.next_weekly_run(et(2026, 9, 28, 8, 0)) == et(2026, 10, 5, 8, 0)
    assert S.next_weekly_run(et(2026, 9, 27, 23, 0)) == et(2026, 9, 28, 8, 0)
    assert S.daily_due(et(2026, 9, 24, 7, 0)) and not S.daily_due(et(2026, 9, 24, 6, 59))
    assert S.weekly_due(et(2026, 9, 28, 8, 0)) and not S.weekly_due(et(2026, 9, 29, 8, 0))
    assert S._sleep_until(et(2026, 9, 25, 7, 0), et(2026, 9, 24, 7, 0)) == S.MAX_IDLE_SLEEP_SECONDS
    assert S._sleep_until(et(2026, 9, 24, 7, 0), et(2026, 9, 24, 7, 0)) == 60.0


class _StopLoop(Exception):
    pass


def _asyncio_proxy(sleeps, stop_after):
    class Proxy:
        CancelledError = asyncio.CancelledError

        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, seconds):
            sleeps.append(seconds)
            if len(sleeps) >= stop_after:
                raise _StopLoop()

    return Proxy()


def _frozen(fixed):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    return Frozen


@pytest.mark.asyncio
async def test_daily_loop_contains_errors_and_sleeps_until_the_next_slot(monkeypatch, caplog):
    fixed = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)             # 08:00 EDT
    sleeps = []
    monkeypatch.setattr(S, "asyncio", _asyncio_proxy(sleeps, stop_after=4))
    monkeypatch.setattr(S, "datetime", _frozen(fixed))
    script = [RuntimeError("boom"), 42.0, None]

    async def fake_tick(now):
        assert now == fixed
        step = script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(S, "_daily_tick", fake_tick)
    with pytest.raises(_StopLoop):
        await S.run_trillion_club_daily_loop()
    assert sleeps == [S.DAILY_BOOT_DELAY_SECONDS, S.RETRY_SECONDS, 42.0, float(S.MAX_IDLE_SLEEP_SECONDS)]
    [err] = [r for r in caplog.records if "daily loop: tick failed" in r.getMessage()]
    assert err.levelno == logging.ERROR and err.exc_info is not None


@pytest.mark.asyncio
async def test_weekly_loop_lets_cancellation_through(monkeypatch, caplog):
    sleeps = []
    monkeypatch.setattr(S, "asyncio", _asyncio_proxy(sleeps, stop_after=99))

    async def fake_tick(now):
        raise asyncio.CancelledError()

    monkeypatch.setattr(S, "_weekly_tick", fake_tick)
    with pytest.raises(asyncio.CancelledError):
        await S.run_trillion_club_weekly_loop()
    assert sleeps == [S.WEEKLY_BOOT_DELAY_SECONDS]
    assert not [r for r in caplog.records if "tick failed" in r.getMessage()]


def test_job_names_are_the_rows_migration_175_seeds():
    sql = MIGRATION_175.read_text()
    insert = re.search(r"INSERT INTO public\.notification_job_state.*?;", sql, re.S).group(0)
    for job in (S.JOB_TRILLION_CLUB_DAILY, S.JOB_TRILLION_CLUB_WEEKLY):
        assert f"'{job}'" in insert, f"{job} has no notification_job_state row in 175"


# ═════════════════════════════════════════════════════════════════════════════════════
# 3. Membership
# ═════════════════════════════════════════════════════════════════════════════════════


def _membership_fixture():
    fmp = FakeFMP()
    fmp.history = {"BIG": history("BIG", 2 * T), "SMALL": history("SMALL", 0.4 * T)}
    store = FakeStore([company("big", cap_symbol="BIG"),
                       company("small", cap_symbol="SMALL", is_member=True, member_since="2025-01-02")])
    return fmp, store


def test_membership_writes_every_fmp_company_and_stamps_checked_at():
    fmp, store = _membership_fixture()
    s = daily(fmp, store)
    assert s["ok"] is True and s["membership"]["ok"] is True
    got = {slug: (state, at) for slug, state, at in store.membership_writes}
    assert set(got) == {"big", "small"}
    big, at = got["big"]
    assert big.is_member and big.last_cap == 2 * T and big.last_cap_date == LAST_CLOSE and at == NOW
    assert got["small"][0].is_member is False, "260 closes below the line leaves the club"
    assert [c["slug"] for c in s["membership"]["changed"]] == ["big", "small"]
    assert fmp.calls["history"] == [("BIG", 260), ("SMALL", 260)]


def test_membership_asks_fmp_for_a_full_year_window():
    """`limit` alone gets ~3 months from FMP: the replay then starts in July and a company
    that has been over $1T for years would be stamped 'member since' July."""
    fmp, store = _membership_fixture()
    daily(fmp, store)
    for frm, to in fmp.calls["history_window"]:
        assert frm is not None and date.fromisoformat(frm) <= TODAY - timedelta(days=380)
        assert to == TODAY.isoformat()
    big = next(st for slug, st, _ in store.membership_writes if slug == "big")
    assert big.closes_at_or_above == 260 and big.member_since < date(2026, 1, 1)


def test_membership_fail_closed_keeps_the_stored_state(caplog):
    fmp, store = _membership_fixture()
    fmp.history["BIG"] = history("BIG", 2 * T, n=5)                       # < 20 rows
    s = daily(fmp, store)
    assert [w[0] for w in store.membership_writes] == ["small"]
    assert s["membership"]["kept"] == ["big"]
    assert s["ok"] is True, "a fail-closed answer is not a failed run (retrying cannot fix it)"
    assert any("KEPT" in r.getMessage() and "slug=big" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [FMPRateLimitException("429"), FMPUnavailableException("5xx")])
def test_membership_fetch_error_fails_the_stage_and_writes_nothing(exc):
    fmp, store = _membership_fixture()
    fmp.history["BIG"] = exc
    s = daily(fmp, store)
    assert [w[0] for w in store.membership_writes] == ["small"]
    assert s["ok"] is False and s["membership"]["ok"] is False
    assert any("big" in f for f in s["failures"])


def test_membership_write_failure_fails_the_stage():
    fmp, store = _membership_fixture()
    store.fail[("update", "big")] = ST.TrillionClubStoreError("update", RuntimeError("520"))
    s = daily(fmp, store)
    assert s["ok"] is False and "big" not in [w[0] for w in store.membership_writes]


def test_rows_for_another_symbol_are_dropped(caplog):
    fmp, store = _membership_fixture()
    fmp.history["BIG"] = history("BIG", 0.5 * T, n=30) + history("OTHER", 3 * T, n=260)
    daily(fmp, store)
    big = next(st for slug, st, _ in store.membership_writes if slug == "big")
    assert big.is_member is False and big.last_cap == 0.5 * T
    assert any("another symbol" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("as_of, warned", [(None, True), ("2026-08-01", True), ("2026-09-10", False)])
def test_manual_cap_company_keeps_its_forced_mode_without_an_fmp_call(as_of, warned, caplog):
    fmp = FakeFMP()
    store = FakeStore([company("aramco", cap_source="manual", mode="force_in", country="SA",
                               manual_as_of=as_of)])
    s = daily(fmp, store)
    [(slug, state, _)] = store.membership_writes
    assert slug == "aramco" and state.is_member is True and state.member_since == TODAY
    assert fmp.calls["history"] == [], "a hand-sized company must never be sized by FMP"
    stale_logged = any("hand-entered cap" in r.getMessage() and r.levelno == logging.WARNING
                       for r in caplog.records)
    assert stale_logged is warned
    assert (s["membership"]["manual_cap_stale"] == ["aramco"]) is warned


def test_force_out_manual_company_stays_out():
    fmp = FakeFMP()
    store = FakeStore([company("sk-hynix", cap_source="manual", mode="force_out", country="KR",
                               manual_as_of="2026-09-20", published=False)])
    daily(fmp, store)
    [(_, state, _)] = store.membership_writes
    assert state.is_member is False


def test_malformed_registry_rows_are_skipped_loudly(caplog):
    fmp, store = _membership_fixture()
    store.companies.append({"slug": "Bad Slug!", "cap_source": "fmp_us", "cap_symbol": "X"})
    store.companies.append(company("weird", cap_symbol="W", mode="sometimes"))
    store.companies.append(dict(store.companies[0]))                      # duplicate slug
    s = daily(fmp, store)
    assert len(s["malformed_companies"]) == 3
    assert sorted(w[0] for w in store.membership_writes) == ["big", "small"]
    assert sum(r.levelno == logging.ERROR and "registry row skipped" in r.getMessage()
               for r in caplog.records) == 2


def test_registry_read_failure_does_nothing_and_fails(caplog):
    fmp, store = _membership_fixture()
    store.fail["read_companies"] = ST.TrillionClubStoreError("read companies", RuntimeError("42P01"))
    s = daily(fmp, store)
    assert s["ok"] is False and store.membership_writes == [] and fmp.calls == {}
    assert "membership" not in s and "filings" not in s, "no stage may run on a failed read"
    assert any(r.levelno == logging.ERROR and "registry read failed" in r.getMessage()
               for r in caplog.records)


def test_naive_now_is_a_programming_error():
    with pytest.raises(ValueError):
        run(J.run_daily(datetime(2026, 9, 24, 7, 0), fmp=FakeFMP(), db=FakeStore()))


@pytest.mark.parametrize("answer", [None, {"slug": "x"}, "rows"])
def test_a_registry_read_that_is_not_a_list_fails_the_run(answer, caplog):
    fmp, store = _membership_fixture()

    async def odd():
        return answer

    store.read_companies = odd
    s = daily(fmp, store)
    assert s["ok"] is False and store.membership_writes == [] and fmp.calls == {}
    assert "membership" not in s and J.failure_text(s).startswith("registry read: got ")


# ── The stamp rule for FMP-sized companies (resilience-1, jobs half) ─────────────────────

_ET_NOW = NOW.astimezone(ET)


def _closes(kind):
    """``(date, cap)`` pairs as ``_closes_from`` hands them to the rule."""
    def pairs(rows):
        return [(r["date"], r["marketCap"]) for r in rows]
    return {
        "empty": [],
        "stale": pairs(history("X", 2 * T, end=date(2026, 8, 1))),
        "garbage": [(LAST_CLOSE.isoformat(), float("nan"))] * 30 + [("not a date", 2 * T)],
        "intraday_only": [(TODAY.isoformat(), 2 * T)],           # 07:00 ET: not a close yet
        # newest first, as FMP orders them (so a 260-row limit keeps the conflicting row)
        "conflicting_newest": [(LAST_CLOSE.isoformat(), 0.5 * T)] + pairs(history("X", 2 * T)),
        "few": pairs(history("X", 0.9 * T, n=5)),
        "full_above": pairs(history("X", 2 * T)),
        "full_below": pairs(history("X", 0.4 * T)),
    }[kind]


#: close sets from which at least one usable close is read
_READS_A_CLOSE = {"few", "full_above", "full_below"}
_PRIORS = {
    "none": None,
    "outsider": rules.NOT_A_MEMBER,
    "long_member": MembershipState(True, date(2020, 1, 2), 300, 0, 3 * T, date(2026, 9, 1)),
    "dropped_out": MembershipState(False, None, 0, 40, 0.5 * T, date(2026, 9, 1)),
}


@pytest.mark.parametrize("mode", ["auto", "force_in", "force_out"])
@pytest.mark.parametrize("prior", sorted(_PRIORS))
@pytest.mark.parametrize("kind", ["empty", "stale", "garbage", "intraday_only",
                                  "conflicting_newest", "few", "full_above", "full_below"])
def test_evaluate_fmp_sized_is_the_rule_whenever_a_close_was_read_and_none_otherwise(mode, prior, kind):
    """The stamp rule asks the rule with the prior's last-cap facts blanked. This pins the
    two things that trick relies on, over every mode: a usable answer is EXACTLY the rule's
    answer with the real prior, and "no usable close" is None even though a forced mode
    never fails closed (it rebuilds its facts from the prior)."""
    closes, p = _closes(kind), _PRIORS[prior]
    got = J.evaluate_fmp_sized(closes, mode=mode, prior=p, today_et=TODAY, now_et=_ET_NOW)
    ref = rules.evaluate_membership(closes, mode=mode, prior=p, today_et=TODAY, now_et=_ET_NOW)
    usable = kind in _READS_A_CLOSE and (mode != "auto" or kind != "few")   # auto needs 20
    if usable:
        assert got is not None and got == ref
        assert got.last_cap_date == LAST_CLOSE
    else:
        assert got is None
        if mode != "auto":
            assert ref is not None, "precondition: a forced mode never fails closed by itself"


@pytest.mark.parametrize("mode", ["force_in", "force_out"])
@pytest.mark.parametrize("kind", ["empty", "stale", "garbage", "conflicting_newest"])
def test_a_forced_fmp_sized_company_is_kept_unstamped_without_a_usable_close(mode, kind, caplog):
    fmp = FakeFMP()
    fmp.history = {"BIG": history("BIG", 2 * T),
                   "LLY": [{"symbol": "LLY", "date": d, "marketCap": c} for d, c in _closes(kind)]}
    lly = company("eli-lilly", cap_symbol="LLY", mode=mode, is_member=mode == "force_in",
                  checked_at=NOW - timedelta(days=2))
    lly |= {"last_market_cap": 1.1 * T, "last_cap_date": "2026-09-21"}
    store = FakeStore([company("big", cap_symbol="BIG"), lly])
    s = daily(fmp, store)
    assert [w[0] for w in store.membership_writes] == ["big"], "no close read -> no write, no stamp"
    assert s["membership"]["kept"] == ["eli-lilly"]
    assert s["ok"] is True, "one usable company answered: not an outage"
    assert any("slug=eli-lilly" in r.getMessage() and "KEPT" in r.getMessage()
               and "override still decides" in r.getMessage() for r in caplog.records)


def test_a_forced_fmp_sized_company_with_fresh_closes_is_stamped_from_the_data():
    fmp = FakeFMP()
    fmp.history = {"LLY": history("LLY", 0.9 * T, n=5)}               # few, but fresh
    lly = company("eli-lilly", cap_symbol="LLY", mode="force_in", is_member=True,
                  member_since="2026-01-05")
    lly |= {"last_market_cap": 1.1 * T, "last_cap_date": "2026-09-01"}
    store = FakeStore([lly])
    s = daily(fmp, store)
    [(slug, state, at)] = store.membership_writes
    assert slug == "eli-lilly" and at == NOW and s["ok"] is True
    assert state.is_member is True and state.member_since == date(2026, 1, 5)
    assert (state.last_cap, state.last_cap_date, state.closes_below) == (0.9 * T, LAST_CLOSE, 5), \
        "the facts come from the closes read today, not from the stored row"


# ── An FMP soft outage for every company fails the stage (resilience-3) ─────────────────


def _outage_registry():
    return [company("big", cap_symbol="BIG", is_member=True),
            company("small", cap_symbol="SMALL", is_member=True, member_since="2025-01-02"),
            company("eli-lilly", cap_symbol="LLY", mode="force_in", is_member=True),
            company("aramco", cap_source="manual", mode="force_in", country="SA",
                    manual_as_of="2026-09-10")]


@pytest.mark.parametrize("kind", ["empty", "stale", "garbage", "intraday_only"])
def test_no_usable_close_for_any_fmp_sized_company_fails_the_stage(kind, caplog):
    fmp = FakeFMP()
    fmp.history = {s: [{"symbol": s, "date": d, "marketCap": c} for d, c in _closes(kind)]
                   for s in ("BIG", "SMALL", "LLY")}
    store = FakeStore(_outage_registry())
    s = daily(fmp, store)
    assert s["ok"] is False and s["membership"]["ok"] is False
    assert s["membership"]["outage"] == ["big", "small", "eli-lilly"]
    assert [w[0] for w in store.membership_writes] == ["aramco"], \
        "a hand-sized company has no FMP data to wait for; nothing FMP-sized is stamped"
    text = J.failure_text(s)
    assert "no usable close for any of the 3" in text and "eli-lilly" in text
    assert any(r.levelno == logging.ERROR and "soft outage" in r.getMessage() for r in caplog.records)


def test_one_fmp_sized_company_failing_closed_alone_is_not_an_outage():
    """A single structural fail-closed (a delisted or renamed symbol) must not turn the
    ledger red and burn three attempts a day forever — only two or more answers can."""
    fmp = FakeFMP()
    fmp.history = {"OLD": history("OLD", 2 * T, n=5)}
    store = FakeStore([company("old", cap_symbol="OLD"),
                       company("aramco", cap_source="manual", mode="force_in", country="SA",
                               manual_as_of="2026-09-10")])
    s = daily(fmp, store)
    assert s["ok"] is True and s["membership"]["kept"] == ["old"] and s["membership"]["outage"] == []


def test_one_usable_answer_means_no_outage():
    fmp = FakeFMP()
    fmp.history = {"BIG": history("BIG", 2 * T), "SMALL": [], "LLY": []}
    s = daily(fmp, FakeStore(_outage_registry()))
    assert s["ok"] is True and sorted(s["membership"]["kept"]) == ["eli-lilly", "small"]
    assert s["membership"]["outage"] == []


def test_a_fetch_that_raised_is_not_counted_as_an_answer():
    """Two fetch errors + one empty answer: the stage fails for the ERRORS (already a retry);
    one answer is below the outage floor, so no outage is claimed on top."""
    fmp = FakeFMP()
    fmp.history = {"BIG": FMPUnavailableException("5xx"), "SMALL": [],
                   "LLY": FMPRateLimitException("429")}
    s = daily(fmp, FakeStore(_outage_registry()))
    assert s["ok"] is False and s["membership"]["outage"] == []
    assert [f for f in s["failures"] if "no usable close" in f] == []
    assert len([f for f in s["failures"] if "history fetch" in f]) == 2


# ═════════════════════════════════════════════════════════════════════════════════════
# 4. STALE
# ═════════════════════════════════════════════════════════════════════════════════════


def _stale_messages(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.ERROR and "trillion club STALE" in r.getMessage()]


def test_stale_error_names_a_published_company_not_refreshed_for_3_days(caplog):
    fmp = FakeFMP()
    fmp.history = {"OLD": history("OLD", 2 * T, n=5), "NEW": history("NEW", 2 * T)}
    store = FakeStore([
        company("old", cap_symbol="OLD", checked_at=NOW - timedelta(days=4)),
        company("new", cap_symbol="NEW", checked_at=NOW - timedelta(days=9)),     # refreshed now
        company("never", cap_symbol="NEVER"),                                     # no history, never checked
        company("hidden", cap_symbol="HID", published=False, checked_at=NOW - timedelta(days=30)),
    ])
    s = daily(fmp, store)
    [msg] = _stale_messages(caplog)
    text = msg.getMessage()
    assert "old (" in text and "never (never)" in text and "hidden (" not in text and "new (" not in text
    assert [x.split(" ")[0] for x in s["stale"]] == ["old", "never"]


def test_no_stale_error_at_under_3_days(caplog):
    fmp = FakeFMP()
    fmp.history = {"OLD": history("OLD", 2 * T, n=5)}
    store = FakeStore([company("old", cap_symbol="OLD", checked_at=NOW - timedelta(days=2, hours=23))])
    daily(fmp, store)
    assert _stale_messages(caplog) == []


# ═════════════════════════════════════════════════════════════════════════════════════
# 5. 13F filings
# ═════════════════════════════════════════════════════════════════════════════════════

Q2, Q1, Q4, Q3, Q2_25 = (2026, 2), (2026, 1), (2025, 4), (2025, 3), (2025, 2)


def _filings_fixture(periods=(Q2, Q1), **company_kw):
    fmp = FakeFMP()
    fmp.history = {"FILR": history("FILR", 2 * T)}
    fmp.dates[CIK] = dates_for(*periods)
    for (y, q) in periods:
        fmp.extracts[(CIK, f"{y}-Q{q}")] = book(y, q)
    store = FakeStore([filer(**company_kw)])
    return fmp, store


def _stored(fmp, store, periods, *, status=BUILD_COMPLETE):
    """Mark `periods` stored with the hash of what FMP returns now."""
    for (y, q) in periods:
        label = f"{y}-Q{q}"
        store.filings.setdefault(CIK, {})[label] = {
            "raw_hash": raw_hash_of(fmp.extracts[(CIK, label)]), "build_status": status,
            "unresolved": {}, "built_at": "2026-09-01T11:00:00+00:00"}


def test_first_run_builds_the_newest_then_backfills_up_to_four_quarters():
    fmp, store = _filings_fixture(periods=(Q2, Q1, Q4, Q3, Q2_25))
    s = daily(fmp, store)
    assert s["ok"] is True, s["failures"]
    assert written_periods(store) == [(CIK, "2026-Q2"), (CIK, "2026-Q1"), (CIK, "2025-Q4"), (CIK, "2025-Q3")]
    # the fifth quarter is only fetched as 2025-Q3's live N-1, never built
    assert (CIK, "2025-Q2") not in written_periods(store)
    # each quarter's extract is fetched ONCE per run although it is used twice
    counts = defaultdict(int)
    for key in fmp.calls["extract"]:
        counts[key] += 1
    assert set(counts.values()) == {1}, counts
    assert s["filings"]["cascaded"] == [], "nothing stored before this run: nothing to cascade"
    q2 = store.filing_writes[0]
    assert q2["changes"]["comparison"] == "quarter" and q2["changes"]["prev_period"] == "2026-Q1"
    oldest = store.filing_writes[-1]
    assert oldest["changes"]["comparison"] == "quarter"


def test_hash_unchanged_complete_build_is_skipped():
    fmp, store = _filings_fixture()
    _stored(fmp, store, (Q2, Q1))
    s = daily(fmp, store)
    assert store.filing_writes == [] and s["filings"]["unchanged"] == [f"{CIK}:2026-Q2"]
    assert fmp.calls["profiles"] == [], "a skipped quarter makes no profile / symbol calls"
    assert s["ok"] is True


def test_hash_unchanged_degraded_build_is_retried():
    fmp, store = _filings_fixture()
    _stored(fmp, store, (Q2, Q1), status=BUILD_DEGRADED)
    daily(fmp, store)
    assert written_periods(store) == [(CIK, "2026-Q2"), (CIK, "2026-Q1")]


def test_changed_newest_is_rebuilt_and_older_complete_is_left_alone_daily():
    fmp, store = _filings_fixture()
    _stored(fmp, store, (Q2, Q1))
    fmp.extracts[(CIK, "2026-Q2")].append(xrow(2026, 2, "333333333", "CCC", 10, 1_000_000))
    daily(fmp, store)
    assert written_periods(store) == [(CIK, "2026-Q2")]
    assert store.filing_writes[0]["changes"]["counts"]["newly_reported"] == 1


def test_weekly_rehash_of_an_amended_quarter_rebuilds_the_next_stored_quarter_first():
    fmp, store = _filings_fixture()
    # Q2 holds CCC; Q1 as first stored did not (so Q2's stored diff said "newly reported")
    fmp.extracts[(CIK, "2026-Q2")].append(xrow(2026, 2, "333333333", "CCC", 10, 1_000_000))
    _stored(fmp, store, (Q2, Q1))
    # a late 13F-HR/A adds CCC to Q1 under its own accession
    fmp.extracts[(CIK, "2026-Q1")].append(
        xrow(2026, 1, "333333333", "CCC", 10, 900_000, acc=f"{CIK}-26-900001", filed="2026-09-01"))
    s = weekly(fmp, store)
    assert s["rehash"]["cascaded"] == [f"{CIK}:2026-Q2"]
    # Q2 first (the dependent), THEN the amended Q1 — a failed cascade must not strand Q1's hash
    assert written_periods(store) == [(CIK, "2026-Q2"), (CIK, "2026-Q1")]
    q2 = store.filing_writes[0]
    assert q2["changes"]["counts"]["newly_reported"] == 0, "CCC is no longer 'newly reported'"
    assert len(store.filing_writes[1]["accessions"]) == 2 and store.filing_writes[1]["amended_on"]


def test_a_failed_cascade_leaves_the_changed_quarter_unwritten():
    fmp, store = _filings_fixture()
    _stored(fmp, store, (Q2, Q1))
    fmp.extracts[(CIK, "2026-Q1")].append(
        xrow(2026, 1, "333333333", "CCC", 10, 900_000, acc=f"{CIK}-26-900001", filed="2026-09-01"))
    store.fail[("upsert", CIK, "2026-Q2")] = ST.TrillionClubStoreError("upsert", RuntimeError("520"))
    s = weekly(fmp, store)
    assert store.filing_writes == [], "Q1 must keep its OLD hash so the next run cascades again"
    assert s["ok"] is False and s["rehash"]["cascade_blocked"]


def test_no_cascade_into_a_quarter_built_this_run():
    fmp, store = _filings_fixture()
    s = daily(fmp, store)
    assert s["filings"]["cascaded"] == [] and len(store.filing_writes) == 2


def test_weekly_rehash_stops_twelve_months_after_quarter_end():
    fmp, store = _filings_fixture(periods=(Q2, Q1, Q4, Q3))
    _stored(fmp, store, (Q2, Q1, Q4, Q3))
    later = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)             # 2025-Q3 ended 370 days ago
    s = weekly(fmp, store, now=later)
    checked = {k.split(":")[1] for k in s["rehash"]["unchanged"]}
    assert checked == {"2026-Q2", "2026-Q1", "2025-Q4"}


def test_select_targets():
    listed = {Q2, Q1, Q4, Q3, Q2_25}
    stored = {"2026-Q2": {"build_status": "complete"}, "2026-Q1": {"build_status": "degraded"},
              "2025-Q4": {"build_status": "complete"}}
    assert J.select_targets(listed, stored, J.DAILY, TODAY) == [Q2, Q1, Q3]
    assert J.select_targets(listed, stored, J.WEEKLY, TODAY) == [Q2, Q1, Q4, Q3]
    assert J.select_targets(set(), {}, J.DAILY, TODAY) == []
    assert J.listed_periods([{"year": "2026", "quarter": 2}, {"year": 2026, "quarter": 5},
                             {"year": True, "quarter": 1}, "junk", {"quarter": 1},
                             {"year": 2026.0, "quarter": "3"}]) == {(2026, 2), (2026, 3)}


def test_filing_refused_writes_nothing_and_is_not_a_run_failure(caplog):
    fmp, store = _filings_fixture()
    fmp.extracts[(CIK, "2026-Q2")] = [
        xrow(2026, 2, f"{i:09d}", f"S{i}", 10, 1000 + i) for i in range(1, MAX_ROWS + 2)]
    s = daily(fmp, store)
    assert (CIK, "2026-Q2") not in written_periods(store)
    assert s["filings"]["refused"] == [f"filer {CIK} 2026-Q2"]
    assert s["filings"]["ok"] is True
    assert any("REFUSED" in r.getMessage() and "use_13f OFF" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("broken", [FMPRateLimitException("429"), []])
def test_unavailable_newest_quarter_writes_nothing_and_fails_the_run(broken):
    fmp, store = _filings_fixture()
    fmp.extracts[(CIK, "2026-Q2")] = broken                                # raise, or listed-but-empty
    s = daily(fmp, store)
    assert (CIK, "2026-Q2") not in written_periods(store)
    assert s["ok"] is False and s["filings"]["unavailable"]


def test_an_unavailable_older_backfill_quarter_is_reported_not_failed(caplog):
    fmp, store = _filings_fixture(periods=(Q2, Q1, Q4))
    fmp.extracts[(CIK, "2025-Q4")] = FMPUnavailableException("5xx")
    s = daily(fmp, store)
    assert written_periods(store) == [(CIK, "2026-Q2")]
    assert s["filings"]["ok"] is True and len(s["filings"]["unavailable"]) == 2
    assert any("older quarter not built" in r.getMessage() for r in caplog.records)


def test_a_supabase_write_failure_on_an_older_quarter_fails_the_run_and_converges():
    """store.py's contract: a failed write fails the run (same-day retry). It used to be
    downgraded to "older quarter not built this run" with ok=True for any quarter but the
    newest, so the ledger recorded success."""
    fmp, store = _filings_fixture(periods=(Q2, Q1))
    store.fail[("upsert", CIK, "2026-Q1")] = ST.TrillionClubStoreError(
        "upsert filing", RuntimeError("57014 statement timeout"))
    s = daily(fmp, store)
    assert written_periods(store) == [(CIK, "2026-Q2")]
    assert s["ok"] is False and s["filings"]["ok"] is False
    assert any("2026-Q1" in f and "write TrillionClubStoreError" in f for f in s["failures"])
    del store.fail[("upsert", CIK, "2026-Q1")]
    store.filing_writes.clear()
    s2 = daily(fmp, store)
    assert s2["ok"] is True and (CIK, "2026-Q1") in written_periods(store)
    assert store.filings[CIK]["2026-Q1"]["raw_hash"] == raw_hash_of(fmp.extracts[(CIK, "2026-Q1")])


def test_an_unexpected_build_error_on_an_older_quarter_fails_the_run(monkeypatch, caplog):
    real = J.build_filing

    async def flaky(fmp, cik, year, quarter, **kw):
        if (year, quarter) == Q1:
            raise RuntimeError("builder bug")
        return await real(fmp, cik, year, quarter, **kw)

    monkeypatch.setattr(J, "build_filing", flaky)
    fmp, store = _filings_fixture(periods=(Q2, Q1))
    s = daily(fmp, store)
    assert written_periods(store) == [(CIK, "2026-Q2")]
    assert s["ok"] is False and any("2026-Q1" in f and "build RuntimeError" in f for f in s["failures"])
    assert not any("older quarter not built" in r.getMessage() for r in caplog.records)


def test_a_cascade_blocked_by_a_refused_next_quarter_waits_instead_of_failing(caplog):
    """The one deferred kind besides "unavailable": the next quarter's refusal is permanent
    (a client-asset book) and never a failure itself, so the older quarter it blocks must not
    turn every run red either."""
    fmp, store = _filings_fixture(periods=(Q2, Q1))
    _stored(fmp, store, (Q2, Q1))
    fmp.extracts[(CIK, "2026-Q2")] = [
        xrow(2026, 2, f"{i:09d}", f"S{i}", 10, 1000 + i) for i in range(1, MAX_ROWS + 2)]
    fmp.extracts[(CIK, "2026-Q1")].append(
        xrow(2026, 1, "333333333", "CCC", 10, 900_000, acc=f"{CIK}-26-900001", filed="2026-09-01"))
    s = weekly(fmp, store)
    assert store.filing_writes == []
    assert s["rehash"]["refused"] and s["rehash"]["cascade_blocked"]
    assert s["rehash"]["ok"] is True
    assert any("older quarter not built" in r.getMessage() and "cascade_blocked" in r.getMessage()
               for r in caplog.records)


def test_dates_failure_writes_nothing_for_that_cik_and_fails():
    fmp, store = _filings_fixture()
    fmp.dates[CIK] = FMPRateLimitException("429")
    s = daily(fmp, store)
    assert store.filing_writes == [] and fmp.calls["extract"] == [] and s["ok"] is False


def test_use_13f_company_with_no_listed_quarter_warns_and_keeps_rows(caplog):
    fmp, store = _filings_fixture()
    fmp.dates[CIK] = []
    s = daily(fmp, store)
    assert store.filing_writes == [] and s["filings"]["no_filings"] == [f"filer {CIK}"]
    assert s["ok"] is True
    assert any("lists no 13F" in r.getMessage() for r in caplog.records)


def test_companies_without_use_13f_are_never_fetched():
    fmp, store = _filings_fixture()
    store.companies = [company("jpmorgan", cap_symbol="JPM", ciks=("0000019617",),
                               card_kind="thirteen_f", use_13f=False, published=False)]
    fmp.history["JPM"] = history("JPM", 0.9 * T)
    daily(fmp, store)
    assert fmp.calls["dates"] == [] and fmp.calls["extract"] == []


def test_stored_unresolved_map_is_handed_to_the_builder(monkeypatch):
    fmp, store = _filings_fixture()
    _stored(fmp, store, (Q2,), status=BUILD_DEGRADED)
    store.filings[CIK]["2026-Q2"]["unresolved"] = {"N97284108": "2026-09-01"}
    seen = {}

    async def fake_build(fmp_, cik, y, q, **kw):
        seen[(y, q)] = kw
        raise J.FilingUnavailable("stop here")

    monkeypatch.setattr(J, "build_filing", fake_build)
    daily(fmp, store)
    kw = seen[(2026, 2)]
    assert kw["stored_unresolved"] == {"N97284108": "2026-09-01"}
    assert kw["prev_quarter_available"] is True and kw["older_filing_exists"] is True
    assert kw["today"] == TODAY


def test_a_built_row_that_is_not_json_safe_is_never_written(monkeypatch, caplog):
    fmp, store = _filings_fixture()

    async def nan_build(fmp_, cik, y, q, **kw):
        return BuiltFiling(cik=cik, period=f"{y}-Q{q}", period_end=rules.quarter_end(y, q),
                           filed_on=None, amended_on=None, accessions=[], total_value=float("nan"),
                           position_count=0, holdings=[], changes={}, excluded_rows=0,
                           unresolved={}, raw_hash="h", build_status="complete")

    monkeypatch.setattr(J, "build_filing", nan_build)
    s = daily(fmp, store)
    assert store.filing_writes == [] and s["ok"] is False
    assert any(r.levelno == logging.ERROR and "refused before the write" in r.getMessage()
               for r in caplog.records)


def test_degraded_build_is_written_and_reported(caplog):
    fmp, store = _filings_fixture()
    fmp.missing_profiles = {"AAA"}
    s = daily(fmp, store)
    assert store.filing_writes[0]["build_status"] == BUILD_DEGRADED
    assert s["filings"]["degraded"][0]["reasons"] == ["profiles_missing:AAA"]
    assert s["ok"] is True, "a degraded build is written and retried; it is not a failed run"


def test_the_read_cache_is_invalidated_only_after_a_write(_no_read_cache_side_effects):
    fmp, store = _filings_fixture()
    _stored(fmp, store, (Q2, Q1))
    fmp.history = {}                        # membership fails closed: nothing written at all
    daily(fmp, store)
    assert _no_read_cache_side_effects == []
    fmp.history = {"FILR": history("FILR", 2 * T)}
    daily(fmp, store)
    assert _no_read_cache_side_effects == [1]


def test_invalidation_failure_is_a_warning_not_a_failure(monkeypatch, caplog):
    def boom():
        raise RuntimeError("cache gone")

    monkeypatch.setattr("app.services.trillion_club_service.invalidate", boom)
    fmp, store = _membership_fixture()
    s = daily(fmp, store)
    assert s["ok"] is True
    assert any("invalidation" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════════════
# 6. Weekly probe and discovery
# ═════════════════════════════════════════════════════════════════════════════════════


def _probe_fixture(*companies_):
    fmp = FakeFMP()
    fmp.screener = [{"symbol": "NVDA", "exchangeShortName": "NASDAQ", "marketCap": 4 * T}]
    store = FakeStore(companies_)
    return fmp, store


def test_a_non_filer_that_starts_filing_logs_a_warning_and_is_never_enabled(caplog):
    tesla = company("tesla", cap_symbol="TSLA", ciks=("0001318605",), is_member=True)
    fmp, store = _probe_fixture(tesla, company("nvidia", cap_symbol="NVDA", is_member=True))
    fmp.dates["0001318605"] = dates_for((2026, 4), (2026, 3))
    s = weekly(fmp, store, now=datetime(2027, 2, 22, 13, 0, tzinfo=timezone.utc))
    [w] = [r for r in caplog.records if "NEW 13F FILER" in r.getMessage()]
    assert w.levelno == logging.WARNING and "use_13f stays OFF" in w.getMessage()
    assert s["probe"]["new_filers"] == [{"slug": "tesla", "cik": "0001318605", "newest": "2026-Q4"}]
    # never enabled: no registry write of any kind
    assert store.membership_writes == [] and store.inserted == []
    assert store.companies[0]["use_13f"] is False


@pytest.mark.parametrize("row, why", [
    (company("berkshire", cap_symbol="BRK-B", ciks=("0001067983",), card_kind="whale_link",
             is_member=True), "a known filer"),
    (company("walmart", cap_symbol="WMT", ciks=("0000104169",), card_kind="thirteen_f",
             is_member=True), "a known filer with use_13f off"),
    (company("apple", cap_symbol="AAPL", ciks=("0000320193",), is_member=False), "not a member"),
    (company("apple", cap_symbol="AAPL", ciks=("0000320193",), is_member=True, mode="force_out"),
     "forced out"),
    (company("tsmc", cap_symbol="TSM", ciks=("0001046179",), card_kind="non_us", country="TW",
             is_member=True), "not US"),
])
def test_probe_does_not_warn(row, why, caplog):
    fmp, store = _probe_fixture(row)
    for cik in row["ciks"]:
        fmp.dates[cik] = dates_for((2026, 2))
    weekly(fmp, store)
    assert not [r for r in caplog.records if "NEW 13F FILER" in r.getMessage()], why


def test_probe_ignores_only_ancient_13fs(caplog):
    samsung = company("samsung", cap_source="manual", mode="force_in", ciks=("0000879316",),
                      card_kind="non_us", country="US", manual_as_of="2026-09-20")
    fmp, store = _probe_fixture(samsung)
    fmp.dates["0000879316"] = dates_for((2015, 1))
    weekly(fmp, store)
    assert not [r for r in caplog.records if "NEW 13F FILER" in r.getMessage()]


def test_probe_failure_fails_the_weekly_run():
    apple = company("apple", cap_symbol="AAPL", ciks=("0000320193",), is_member=True)
    fmp, store = _probe_fixture(apple)
    fmp.dates["0000320193"] = FMPRateLimitException("429")
    s = weekly(fmp, store)
    assert s["ok"] is False and s["probe"]["ok"] is False


def _discovery_fixture():
    registry = [
        company("nvidia", cap_symbol="NVDA", is_member=True),
        company("alphabet", cap_symbol="GOOGL", aliases=("GOOG",), is_member=True),
        company("berkshire", cap_symbol="BRK-B", aliases=("BRK-A",), card_kind="whale_link", is_member=True),
        company("newco", cap_symbol="OLDNEWCO"),                            # slug collision bait
    ]
    fmp = FakeFMP()
    fmp.screener = [
        {"symbol": "NVDA", "exchangeShortName": "NASDAQ", "marketCap": 4 * T},
        {"symbol": "GOOG", "exchangeShortName": "NASDAQ", "marketCap": 3 * T},        # alias
        {"symbol": "BRK.B", "exchangeShortName": "NYSE", "marketCap": 1.05 * T},     # dot form
        {"symbol": "NEWCO", "companyName": "New Co Holdings", "exchangeShortName": "NYSE",
         "marketCap": 0.95 * T, "country": "US"},
        {"symbol": "ADRX", "companyName": "Foreign ADR plc", "exchangeShortName": "NASDAQ",
         "marketCap": 1.2 * T, "country": "GB"},
        {"symbol": "VTI", "exchangeShortName": "NYSE", "marketCap": 2 * T, "isEtf": True},
        {"symbol": "FXAIX", "exchangeShortName": "NASDAQ", "marketCap": 2 * T, "isFund": True},
        {"symbol": "MU.TO", "exchangeShortName": "TSX", "marketCap": 1.5 * T},
        {"symbol": "DIPPED", "exchangeShortName": "NYSE", "marketCap": 0.95 * T},
        {"symbol": "NANCAP", "exchangeShortName": "NYSE", "marketCap": float("nan")},
        {"symbol": "LOW", "exchangeShortName": "NYSE", "marketCap": 0.5 * T},
        "junk",
    ]
    fmp.batch = {"NEWCO": 0.96 * T, "ADRX": 1.1 * T, "DIPPED": 0.85 * T}
    return fmp, FakeStore(registry)


def test_discovery_inserts_unknown_confirmed_symbols_unpublished(caplog):
    fmp, store = _discovery_fixture()
    s = weekly(fmp, store)
    assert fmp.calls["batch"] == [["ADRX", "DIPPED", "NEWCO"]]
    by_symbol = {r["cap_symbol"]: r for r in store.inserted}
    assert set(by_symbol) == {"NEWCO", "ADRX"}
    for r in store.inserted:
        assert r["published"] is False and r["use_13f"] is False and r["card_kind"] == "no_thirteen_f"
        assert r["membership_mode"] == "auto" and r["link_whale"] is False
        assert set(r) == ST.DISCOVERED_ROW_KEYS
    assert by_symbol["NEWCO"]["slug"] == "newco-2", "an existing slug is never reused"
    assert by_symbol["ADRX"]["cap_source"] == "fmp_adr" and by_symbol["ADRX"]["home_country"] == "GB"
    assert s["discovery"]["unconfirmed"] == ["DIPPED"]
    warned = [r for r in caplog.records if "trillion club DISCOVERED" in r.getMessage()]
    assert len(warned) == 2 and all("UNPUBLISHED" in r.getMessage() for r in warned)
    assert s["ok"] is True and fmp.calls["screener"][0]["market_cap_more_than"] == 900_000_000_000
    assert fmp.calls["screener"][0]["is_etf"] is False and fmp.calls["screener"][0]["is_fund"] is False


def test_discovery_batch_failure_inserts_nothing_and_fails():
    fmp, store = _discovery_fixture()
    fmp.batch = FMPRateLimitException("429")
    s = weekly(fmp, store)
    assert store.inserted == [] and s["ok"] is False and s["discovery"]["ok"] is False


@pytest.mark.parametrize("screen", [[], FMPUnavailableException("5xx")])
def test_an_empty_or_failed_screen_is_a_failure(screen):
    fmp, store = _discovery_fixture()
    fmp.screener = screen
    s = weekly(fmp, store)
    assert s["ok"] is False and store.inserted == [] and fmp.calls["batch"] == []


def test_discovered_row_satisfies_migration_175_checks():
    taken = {"x"}
    for cand in ({"symbol": "X", "name": None, "country": None},
                 {"symbol": "A" * 50, "name": "N" * 90, "country": "usa"},
                 {"symbol": "BRK-B", "name": " Berkshire ", "country": "US"}):
        row = J.discovered_row(cand, taken)
        taken.add(row["slug"])
        assert re.fullmatch(r"[a-z0-9-]{1,40}", row["slug"])
        assert 1 <= len(row["display_name"]) <= 60
        assert re.fullmatch(r"[A-Z]{2}", row["home_country"])
        assert row["cap_symbol"] and row["cap_source"] in ("fmp_us", "fmp_adr")
    assert J.discovered_row({"symbol": "X"}, {"x", "x-2"})["slug"] == "x-3"


def test_discovered_row_aliases_are_normalised_and_never_the_primary():
    row = J.discovered_row({"symbol": "BRK-B", "name": "Berkshire"}, set(),
                           aliases=("brk.a", "BRK-B", "BRK.B", "BRK-A", "", None))
    assert row["symbol_aliases"] == ["BRK-A"] and row["cap_symbol"] == "BRK-B"
    assert J.discovered_row({"symbol": "X"}, set())["symbol_aliases"] == []


@pytest.mark.parametrize("a, b, same", [
    ("Alphabet Inc.", "Alphabet Inc. Class C", True),
    ("Berkshire Hathaway Inc.", "BERKSHIRE  HATHAWAY INC", True),
    ("Dual Holdings Inc", "Dual Holdings Inc, Series A", True),
    ("Fox Corp Class A", "Fox Corp - Class B", True),
    ("Ab C", "Ab-C", False),                  # inner punctuation is kept: no false merge
    ("Apple Inc.", "Apple Hospitality REIT, Inc.", False),
])
def test_company_key_ignores_case_spacing_and_the_class_suffix_only(a, b, same):
    assert (J.company_key(a) == J.company_key(b)) is same
    assert J.company_key(a) is not None


@pytest.mark.parametrize("name", [None, "", "   ", " . ,", 42])
def test_company_key_is_none_without_a_name(name):
    assert J.company_key(name) is None


def _screen_row(symbol, name, cap, **kw):
    return {"symbol": symbol, "companyName": name, "exchangeShortName": "NYSE",
            "marketCap": cap, "country": "US", **kw}


def test_share_classes_screened_as_separate_rows_become_one_row_with_aliases(caplog):
    """FMP's screener lists each class at the company's total cap and carries no CIK; one
    company must be ONE unpublished row, even when the batch confirms only one class."""
    fmp = FakeFMP()
    fmp.screener = [_screen_row("DUAL-A", "Dual Holdings Inc. Class A", 1.1 * T),
                    _screen_row("DUAL.B", "DUAL HOLDINGS INC", 1.1 * T),
                    _screen_row("SOLO", "Solo Corp", 0.95 * T)]
    fmp.batch = {"DUAL-B": 1.1 * T, "SOLO": 0.95 * T}                 # DUAL-A unconfirmed
    store = FakeStore([company("nvidia", cap_symbol="NVDA", is_member=True)])
    s = weekly(fmp, store)
    by_symbol = {r["cap_symbol"]: r for r in store.inserted}
    assert set(by_symbol) == {"DUAL-B", "SOLO"}
    assert by_symbol["DUAL-B"]["symbol_aliases"] == ["DUAL-A"]
    assert by_symbol["SOLO"]["symbol_aliases"] == []
    assert s["discovery"]["unconfirmed"] == []
    assert s["discovery"]["share_classes"] == [{"cap_symbol": "DUAL-B", "aliases": ["DUAL-A"]}]
    assert any("DISCOVERED DUAL-B" in r.getMessage() and "symbol_aliases: DUAL-A" in r.getMessage()
               for r in caplog.records)
    # the next Monday the registry knows both classes: nothing is asked, nothing inserted
    store.companies += copy.deepcopy(store.inserted)
    store.inserted.clear()
    fmp.calls.clear()
    weekly(fmp, store)
    assert store.inserted == [] and fmp.calls["batch"] == []


def test_a_class_whose_whole_company_is_unconfirmed_is_not_inserted():
    fmp = FakeFMP()
    fmp.screener = [_screen_row("DUAL-A", "Dual Holdings Inc.", 1.1 * T),
                    _screen_row("DUAL-B", "Dual Holdings Inc.", 1.1 * T)]
    fmp.batch = {"DUAL-A": 0.8 * T}
    store = FakeStore([company("nvidia", cap_symbol="NVDA", is_member=True)])
    s = weekly(fmp, store)
    assert store.inserted == [] and s["discovery"]["unconfirmed"] == ["DUAL-A", "DUAL-B"]


def test_another_class_of_a_company_the_registry_holds_is_not_discovered(caplog):
    """The registry row lacks the alias (an owner edit, or an old row): the screener's
    matching companyName ties the new class to it. Without this, deleting the duplicate a
    past run inserted never sticks."""
    fmp = FakeFMP()
    fmp.screener = [_screen_row("BRK-B", "Berkshire Hathaway Inc.", 1.1 * T),
                    _screen_row("BRK-A", "Berkshire Hathaway Inc.", 1.1 * T)]
    fmp.batch = {"BRK-A": 1.1 * T}
    store = FakeStore([company("berkshire", cap_symbol="BRK-B", is_member=True)])
    s = weekly(fmp, store)
    assert store.inserted == [] and fmp.calls["batch"] == []
    assert s["discovery"]["known_share_classes"] == [{"symbol": "BRK-A", "held_as": "BRK-B"}]
    assert any(r.levelno == logging.WARNING and "Add BRK-A to that row's symbol_aliases"
               in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("column", ["cap_symbol", "detail_symbol", "logo_symbol", "symbol_aliases"])
def test_discovery_knows_every_symbol_of_a_row_the_parser_rejected(column):
    broken = company("jpm", cap_symbol="ZZZ", mode="sometimes")        # unparseable mode
    broken |= {"cap_symbol": "QQQQ", "detail_symbol": None, "logo_symbol": None,
               "symbol_aliases": []}
    broken[column] = ["jpm"] if column == "symbol_aliases" else "jpm"
    fmp = FakeFMP()
    fmp.screener = [_screen_row("JPM", "JPMorgan Chase & Co.", 0.95 * T)]
    fmp.batch = {"JPM": 0.94 * T}
    store = FakeStore([company("nvidia", cap_symbol="NVDA", is_member=True), broken])
    s = weekly(fmp, store)
    assert s["malformed_companies"], "precondition: the row did not parse"
    assert store.inserted == [] and fmp.calls["batch"] == [], "JPM is already in the registry"


def test_a_malformed_rows_slug_is_never_reused():
    broken = company("newco", cap_symbol="OLD", mode="sometimes")
    fmp = FakeFMP()
    fmp.screener = [_screen_row("NEWCO", "New Co", 1.1 * T)]
    fmp.batch = {"NEWCO": 1.1 * T}
    store = FakeStore([company("nvidia", cap_symbol="NVDA", is_member=True), broken])
    weekly(fmp, store)
    assert [r["slug"] for r in store.inserted] == ["newco-2"]


def test_a_confirmed_candidate_dropped_by_on_conflict_is_warned_about(caplog):
    fmp = FakeFMP()
    fmp.screener = [_screen_row("AAA", "Aaa Corp", 1.1 * T), _screen_row("BBB", "Bbb Corp", 1.1 * T)]
    fmp.batch = {"AAA": 1.1 * T, "BBB": 1.1 * T}
    store = FakeStore([company("nvidia", cap_symbol="NVDA", is_member=True)])

    async def raced(rows):                    # "bbb" was created between the read and the insert
        store.inserted.extend(r for r in rows if r["slug"] != "bbb")
        return [r["slug"] for r in rows if r["slug"] != "bbb"]

    store.insert_discovered = raced
    s = weekly(fmp, store)
    assert s["discovery"]["inserted"] == ["aaa"] and s["discovery"]["not_inserted"] == ["bbb"]
    assert s["items"] == 1
    assert any(r.levelno == logging.WARNING and "BBB" in r.getMessage()
               and "NOT inserted" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════════════
# 7. The store, over a fake Supabase client
# ═════════════════════════════════════════════════════════════════════════════════════


class FakeQuery:
    def __init__(self, sb, table):
        self.sb, self.table_name, self.ops = sb, table, []

    def __getattr__(self, name):
        def method(*a, **k):
            self.ops.append((name, a, k))
            return self
        return method

    def execute(self):
        self.sb.executed.append((self.table_name, list(self.ops)))
        out = self.sb.responder(self.table_name, self.ops)
        if isinstance(out, BaseException):
            raise out
        return types.SimpleNamespace(data=out)


class FakeSB:
    def __init__(self, responder):
        self.responder, self.executed = responder, []

    def table(self, name):
        return FakeQuery(self, name)


@pytest.mark.asyncio
async def test_store_wraps_a_read_error_with_the_operation():
    store = ST.TrillionClubStore(FakeSB(lambda t, ops: ValueError("42P01 relation does not exist")))
    with pytest.raises(ST.TrillionClubStoreError) as e:
        await store.read_companies()
    assert "read companies" in str(e.value) and "42P01" in str(e.value)


@pytest.mark.parametrize("data", [None, {"x": 1}, "rows"])
@pytest.mark.asyncio
async def test_store_refuses_a_non_list_read(data):
    store = ST.TrillionClubStore(FakeSB(lambda t, ops: data))
    with pytest.raises(ST.TrillionClubStoreError):
        await store.read_companies()
    with pytest.raises(ST.TrillionClubStoreError):
        await store.read_filings(CIK)


@pytest.mark.asyncio
async def test_store_update_membership_payload_and_no_row_is_an_error():
    sb = FakeSB(lambda t, ops: [{"slug": "nvidia"}])
    store = ST.TrillionClubStore(sb)
    state = MembershipState(True, date(2025, 6, 1), 30, 0, 4.4e12, LAST_CLOSE)
    await store.update_membership("nvidia", state, checked_at=NOW)
    [(table, ops)] = sb.executed
    assert table == "trillion_club_companies"
    update = next(a[0] for n, a, k in ops if n == "update")
    assert update == {"is_member": True, "member_since": "2025-06-01", "closes_at_or_above": 30,
                      "closes_below": 0, "last_market_cap": 4.4e12, "last_cap_date": "2026-09-23",
                      "membership_checked_at": NOW.isoformat(), "updated_at": NOW.isoformat()}
    assert ("eq", ("slug", "nvidia"), {}) in ops
    empty = ST.TrillionClubStore(FakeSB(lambda t, ops: []))
    with pytest.raises(ST.TrillionClubStoreError, match="no row matched"):
        await empty.update_membership("nvidia", state, checked_at=NOW)


@pytest.mark.parametrize("state, at", [
    (MembershipState(True, None, 1, 0, float("nan"), LAST_CLOSE), NOW),
    (MembershipState(True, None, 1, 0, 0.0, LAST_CLOSE), NOW),
    (MembershipState(True, None, -1, 0, None, None), NOW),
    (MembershipState(True, None, True, 0, None, None), NOW),
    (MembershipState(True, "2026-01-01", 1, 0, None, None), NOW),
    (MembershipState(True, None, 1, 0, None, None), datetime(2026, 9, 24, 7, 0)),   # naive
])
def test_membership_payload_refuses_values_the_table_would_reject(state, at):
    with pytest.raises(ValueError):
        ST.membership_payload(state, checked_at=at)


@pytest.mark.asyncio
async def test_store_insert_discovered_forces_unpublished_and_on_conflict_do_nothing():
    sb = FakeSB(lambda t, ops: [next(a[0] for n, a, k in ops if n == "upsert")[0]])
    store = ST.TrillionClubStore(sb)
    row = J.discovered_row({"symbol": "NEWCO", "name": "New", "country": "US"}, set())
    row["published"] = True                          # a caller bug must not publish
    row["use_13f"] = True
    assert await store.insert_discovered([row]) == ["newco"]
    [(_, ops)] = sb.executed
    name, args, kwargs = next(o for o in ops if o[0] == "upsert")
    assert args[0][0]["published"] is False and args[0][0]["use_13f"] is False
    assert kwargs == {"on_conflict": "slug", "ignore_duplicates": True}
    with pytest.raises(ValueError):
        await store.insert_discovered([{"slug": "Bad!", **{k: None for k in ST.DISCOVERED_ROW_KEYS if k != "slug"}}])
    with pytest.raises(ValueError):
        await store.insert_discovered([{"slug": "ok"}])
    assert await store.insert_discovered([]) == []


@pytest.mark.asyncio
async def test_store_read_filings_indexes_by_period_and_skips_malformed_rows(caplog):
    rows = [{"cik": CIK, "period": "2026-Q2", "raw_hash": "h2", "build_status": "complete",
             "unresolved": {"X": "2026-09-01"}, "built_at": "t"},
            {"cik": CIK, "period": "Q2-2026", "raw_hash": "bad"},
            {"cik": "0000000999", "period": "2026-Q1", "raw_hash": "other cik"},
            {"cik": CIK, "period": "2026-Q1", "raw_hash": None, "build_status": "degraded",
             "unresolved": "junk"}, "junk"]
    store = ST.TrillionClubStore(FakeSB(lambda t, ops: rows))
    got = await store.read_filings(CIK)
    assert set(got) == {"2026-Q2", "2026-Q1"}
    assert got["2026-Q2"]["unresolved"] == {"X": "2026-09-01"} and got["2026-Q1"]["unresolved"] == {}
    assert sum("malformed" in r.getMessage() for r in caplog.records) == 3
    with pytest.raises(ValueError):
        await store.read_filings("1045810")


@pytest.mark.asyncio
async def test_store_upsert_filing_stamps_built_at_on_the_primary_key():
    sb = FakeSB(lambda t, ops: None)
    store = ST.TrillionClubStore(sb)
    row = {k: None for k in ST._FILING_ROW_KEYS} | {"cik": CIK, "period": "2026-Q2"}
    await store.upsert_filing(row, built_at=NOW)
    [(table, ops)] = sb.executed
    name, args, kwargs = next(o for o in ops if o[0] == "upsert")
    assert table == "trillion_club_filings" and args[0]["built_at"] == NOW.isoformat()
    assert kwargs["on_conflict"] == "cik,period"
    with pytest.raises(ValueError):
        await store.upsert_filing({"cik": CIK}, built_at=NOW)
    with pytest.raises(ValueError):
        await store.upsert_filing(row, built_at=datetime(2026, 9, 24))


def test_run_daily_through_the_real_store_writes_json_safe_rows():
    """End to end: jobs -> TrillionClubStore -> a Supabase-shaped client."""
    fmp, _ = _filings_fixture()
    registry = [filer()]

    def responder(table, ops):
        verb = ops[0][0]
        if table == "trillion_club_companies" and verb == "select":
            return copy.deepcopy(registry)
        if verb == "update":
            return [{"slug": "filer"}]
        if table == "trillion_club_filings" and verb == "select":
            return []
        if verb == "upsert":
            return None
        raise AssertionError(f"unexpected {table} {verb}")

    sb = FakeSB(responder)
    s = run(J.run_daily(NOW, fmp=fmp, db=sb, actions=FakeActions()))
    assert s["ok"] is True, s["failures"]
    upserts = [next(a[0] for n, a, k in ops if n == "upsert") for t, ops in sb.executed
               if t == "trillion_club_filings" and ops[0][0] == "upsert"]
    assert [u["period"] for u in upserts] == ["2026-Q2", "2026-Q1"]
    for u in upserts:
        json.dumps(u, allow_nan=False)
        assert set(u) == ST._FILING_ROW_KEYS | {"built_at"}
    assert {t for t, _ in sb.executed} <= {"trillion_club_companies", "trillion_club_filings"}


# ═════════════════════════════════════════════════════════════════════════════════════
# 8. Isolation: no notification / push / whale code, no table outside trillion_club_*
# ═════════════════════════════════════════════════════════════════════════════════════

_FORBIDDEN_MODULE_PREFIXES = (
    "app.services.notification_senders", "app.services.push_service",
    "app.services.push_dispatch_service", "app.services.notification_inbox_service",
    "app.services.price_alert_engine", "app.services.price_alert_service",
    "app.services.whale_service", "app.services.notification_kinds",
)
_FORBIDDEN_MODULE_WORDS = ("whale_alert", "push", "apns", "notification_sender")
_ALLOWED_FROM_NOTIFICATION_JOBS = {"claimed_scheduled_job", "scheduled_job_state"}
_FORBIDDEN_TABLE_WORDS = ("whales", "whale_alerts", "whale_trades", "whale_follows",
                          "whale_holdings", "notification_events", "device_tokens",
                          "user_notifications", "notification_inbox")


def _isolation_problems(src: str, name: str = "<src>") -> list:
    tree = ast.parse(src)
    problems = []
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = n.value.value
    for n in ast.walk(tree):
        modules = []
        if isinstance(n, ast.Import):
            modules = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            modules = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            if n.module == "app.services.notification_jobs":
                extra = {a.name for a in n.names} - _ALLOWED_FROM_NOTIFICATION_JOBS
                if extra:
                    problems.append(f"{name}: imports {sorted(extra)} from notification_jobs")
            if n.module == "app.services" and any(a.name in ("whale_service", "push_service")
                                                  for a in n.names):
                problems.append(f"{name}: imports a forbidden service module")
        for m in modules:
            if m.startswith(_FORBIDDEN_MODULE_PREFIXES) or any(w in m for w in _FORBIDDEN_MODULE_WORDS):
                problems.append(f"{name}: imports {m}")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr == "rpc":
                problems.append(f"{name}:{n.lineno}: calls .rpc()")
            if n.func.attr == "table":
                arg = n.args[0] if n.args else None
                value = (arg.value if isinstance(arg, ast.Constant) else
                         consts.get(arg.id) if isinstance(arg, ast.Name) else None)
                if not (isinstance(value, str) and value.startswith("trillion_club_")):
                    problems.append(f"{name}:{n.lineno}: .table() on {ast.unparse(arg) if arg else '?'}")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _FORBIDDEN_TABLE_WORDS:
            problems.append(f"{name}:{n.lineno}: names the {n.value!r} table")
    return problems


def test_nothing_under_trillion_club_touches_notifications_push_or_whales():
    files = sorted(PKG.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "rules.py", "builder.py", "store.py",
                                       "jobs.py", "scheduler.py"}
    problems = [p for f in files for p in _isolation_problems(f.read_text(), f.name)]
    assert problems == []
    # and the store really does name its tables through the scanned constants
    assert "trillion_club_companies" in (PKG / "store.py").read_text()


@pytest.mark.parametrize("bad", [
    "from app.services.whale_service import WhaleService\n",
    "import app.services.push_service\n",
    "from app.services.notification_senders.smart_money_sender import send\n",
    "from app.services.notification_jobs import claimed_job\n",
    "from app.services import whale_service\n",
    "def f(sb):\n    return sb.table('whales').insert({})\n",
    "def f(sb):\n    return sb.table('trillion_club_filings').rpc('x')\n",
    "def f(sb, t):\n    return sb.table(t).select('*')\n",
    "TABLE = 'whale_alerts'\n",
    "from app.services.whale_alerts import fire\n",
])
def test_the_isolation_scan_is_not_vacuous(bad):
    assert _isolation_problems(bad), f"the scan accepted: {bad!r}"


def test_the_isolation_scan_accepts_what_the_package_does():
    ok = ("from app.services.notification_jobs import claimed_scheduled_job, scheduled_job_state\n"
          "COMPANIES_TABLE = 'trillion_club_companies'\n"
          "def f(sb):\n    return sb.table(COMPANIES_TABLE).select('*')\n")
    assert _isolation_problems(ok) == []


def test_importing_the_jobs_pulls_in_no_notification_or_whale_module():
    code = (
        "import sys\n"
        "import app.services.trillion_club.jobs, app.services.trillion_club.scheduler, "
        "app.services.trillion_club.store\n"
        f"bad = [m for m in sys.modules if m.startswith({_FORBIDDEN_MODULE_PREFIXES!r})]\n"
        "print('BAD=' + ','.join(sorted(bad)))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "BAD=\n" in out.stdout or out.stdout.strip().endswith("BAD="), out.stdout


# ═════════════════════════════════════════════════════════════════════════════════════
# 9. main.lifespan spawns both loops ONLY in the Railway branch
# ═════════════════════════════════════════════════════════════════════════════════════

_LOOPS = ("run_trillion_club_daily_loop", "run_trillion_club_weekly_loop")


def _spawned(nodes):
    out = []
    for top in nodes:
        for n in ast.walk(top):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_spawn"
                    and n.args and isinstance(n.args[0], ast.Call)
                    and isinstance(n.args[0].func, ast.Name)):
                out.append(n.args[0].func.id)
    return out


def _spawn_problems(src: str) -> list:
    tree = ast.parse(src)
    lifespans = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    if len(lifespans) != 1:
        return [f"expected one lifespan, found {len(lifespans)}"]
    body = lifespans[0].body
    branches = [n for n in body if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                and n.test.id == "is_local_dev"]
    if len(branches) != 1:
        return [f"expected one top-level `if is_local_dev:`, found {len(branches)}"]
    dev_check = any(
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "is_local_dev" for t in n.targets)
        and isinstance(n.value, ast.Compare) and isinstance(n.value.left, ast.Attribute)
        and n.value.left.attr == "ENVIRONMENT" and isinstance(n.value.comparators[0], ast.Constant)
        and n.value.comparators[0].value == "development" for n in body)
    problems = [] if dev_check else ["is_local_dev is not the development check"]
    railway, local, everywhere = _spawned(branches[0].orelse), _spawned(branches[0].body), _spawned(body)
    for loop in _LOOPS:
        if railway.count(loop) != 1:
            problems.append(f"{loop} spawned {railway.count(loop)}x in the Railway branch")
        if loop in local:
            problems.append(f"{loop} spawned in the local-dev branch")
        if everywhere.count(loop) != railway.count(loop):
            problems.append(f"{loop} spawned outside the Railway branch")
    imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and n.module == "app.services.trillion_club.scheduler" for a in n.names}
    problems += [f"{loop} not imported from the scheduler" for loop in _LOOPS if loop not in imported]
    return problems


_GOOD = '''
async def lifespan(app):
    is_local_dev = settings.ENVIRONMENT == "development"
    if is_local_dev:
        logger.info("skip")
    else:
        from app.services.trillion_club.scheduler import (
            run_trillion_club_daily_loop,
            run_trillion_club_weekly_loop,
        )
        _spawn(run_trillion_club_daily_loop(), "trillion_club_daily")
        _spawn(run_trillion_club_weekly_loop(), "trillion_club_weekly")
    yield
'''


@pytest.mark.parametrize("mutant", [
    _GOOD.replace('        logger.info("skip")',
                  '        _spawn(run_trillion_club_daily_loop(), "x")\n        logger.info("skip")'),
    _GOOD.replace('        _spawn(run_trillion_club_daily_loop(), "trillion_club_daily")',
                  '        # _spawn(run_trillion_club_daily_loop(), "trillion_club_daily")'),
    _GOOD.replace('_spawn(run_trillion_club_weekly_loop(), "trillion_club_weekly")',
                  '_spawn(run_trillion_club_weekly_loop, "trillion_club_weekly")'),
    _GOOD.replace("    yield", "    if run_notification_jobs:\n"
                  "        _spawn(run_trillion_club_weekly_loop(), 'again')\n    yield"),
    _GOOD.replace('settings.ENVIRONMENT == "development"', "False"),
])
def test_spawn_scan_is_not_vacuous(mutant):
    assert _spawn_problems(_GOOD) == []
    assert _spawn_problems(mutant)


def test_main_spawns_both_trillion_club_loops_only_on_railway():
    src = (BACKEND / "app" / "main.py").read_text()
    assert _spawn_problems(src) == []
    commented = src.replace('_spawn(run_trillion_club_weekly_loop(), "trillion_club_weekly")',
                            '# _spawn(run_trillion_club_weekly_loop(), "trillion_club_weekly")')
    assert commented != src and _spawn_problems(commented)


# ═════════════════════════════════════════════════════════════════════════════════════
# 10. The read-only preview script
# ═════════════════════════════════════════════════════════════════════════════════════

import scripts.preview_trillion_club as P  # noqa: E402


def test_preview_fallback_list_is_the_seeds_sixteen_members():
    seed = {c["slug"]: c for c in json.loads(P.SEED_PATH.read_text())["companies"]}
    fallback = P.fallback_company_rows()
    assert len(fallback) == 16 and len({r["slug"] for r in fallback}) == 16
    for r in fallback:
        s = seed[r["slug"]]
        for key in ("ciks", "card_kind", "use_13f", "cap_source", "cap_symbol", "membership_mode",
                    "home_country"):
            assert s[key] == r[key], (r["slug"], key)
        assert sorted(s["symbol_aliases"]) == sorted(r["symbol_aliases"]), r["slug"]
    rows, _, source = P.load_seed(BACKEND / "data" / "no_such_seed.json")
    assert len(rows) == 16 and "built-in" in source


def test_preview_tripwire_refuses_every_supabase_use(monkeypatch):
    monkeypatch.setattr(database, "_supabase_client", database._supabase_client)
    P.install_supabase_tripwire()
    client = database.get_supabase()
    with pytest.raises(P.SupabaseRefused):
        client.table("trillion_club_companies")
    with pytest.raises(P.SupabaseRefused):
        client.rpc("claim_scheduled_job", {})


@pytest.mark.asyncio
async def test_preview_split_derivation_never_touches_its_supabase_cache(monkeypatch):
    # The base class SWALLOWS a cache failure, so "it did not raise" proves nothing: count
    # every attempt to reach Supabase instead.
    reached = []
    monkeypatch.setattr(database, "get_supabase", lambda: reached.append(1) or P._SupabaseTripwire())
    actions = P.ReadOnlyCorporateActions()
    assert await actions._db_get("NVDA", "split", "2026-03-31", "2026-06-30") is None
    assert await actions._db_put("NVDA", "split", "2026-03-31", "2026-06-30", []) is None
    assert reached == []
    # control: the production class does reach for it (so the count above is not vacuous)
    from app.services.corporate_actions_service import CorporateActionsService
    await CorporateActionsService()._db_put("NVDA", "split", "2026-03-31", "2026-06-30", [])
    assert reached == [1]


def test_preview_memory_db_is_read_only():
    db = P.MemoryDB({"t": [{"a": 2, "b": "x"}, {"a": 1, "b": "y"}, {"a": None, "b": "z"}]})
    got = db.table("t").select("a").in_("b", ["x", "y", "z"]).order("a").limit(2).execute().data
    assert got == [{"a": 1}, {"a": 2}]
    assert db.table("t").select("*").eq("b", "z").execute().data == [{"a": None, "b": "z"}]
    for verb in ("insert", "update", "upsert", "delete"):
        with pytest.raises(P.SupabaseRefused):
            getattr(db.table("t"), verb)({})
    with pytest.raises(P.SupabaseRefused):
        db.rpc("x")
    with pytest.raises(P.SupabaseRefused):
        db.table("t").select("*").gte("a", 1)


def _db_write_calls(src: str) -> list:
    """`.insert/.upsert/.update/.delete(...)` whose call chain goes through `.table(...)`,
    plus any `.rpc(...)` call — a dict's `.update()` or `sys.path.insert()` is not a write."""
    hits = []
    for n in ast.walk(ast.parse(src)):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr == "rpc":
            hits.append((n.lineno, "rpc"))
            continue
        if n.func.attr not in ("insert", "upsert", "update", "delete"):
            continue
        cur = n.func.value
        while isinstance(cur, (ast.Call, ast.Attribute)):
            if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute) and cur.func.attr == "table":
                hits.append((n.lineno, n.func.attr))
                break
            cur = cur.func if isinstance(cur, ast.Call) else cur.value
    return hits


def test_the_write_finder_is_not_vacuous():
    assert _db_write_calls("sb.table('x').insert({}).execute()") == [(1, "insert")]
    assert _db_write_calls("get_supabase().table(T).update(p).eq('a', 1)") == [(1, "update")]
    assert _db_write_calls("db.rpc('f', {})") == [(1, "rpc")]
    assert _db_write_calls("row.update({'a': 1}); sys.path.insert(0, 'x'); d.delete(1)") == []


def test_preview_source_never_writes_and_never_imports_the_job_store():
    src = (BACKEND / "scripts" / "preview_trillion_club.py").read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom):
            assert n.module != "app.services.trillion_club.store"
            if n.module == "app.services.trillion_club.jobs":
                # evaluate_fmp_sized is PURE (the rule + the stamp decision): no FMP, no store.
                assert {a.name for a in n.names} <= {"HISTORY_LIMIT", "HISTORY_LOOKBACK_DAYS",
                                                     "evaluate_fmp_sized"}, \
                    "the preview may borrow the job's constants and pure rule, never its runs"
            assert not (n.module == "app.database" and any(a.name == "get_supabase" for a in n.names))
    assert _db_write_calls(src) == []
    assert "--apply" not in src and "--write" not in src


def test_preview_log_filter_scrubs_the_key():
    record = logging.LogRecord("x", logging.WARNING, __file__, 1,
                               "GET https://fmp/stable/x?symbol=A&apikey=%s failed for SECRETKEY123",
                               ("SECRETKEY123",), None)
    P._KeyScrubFilter("SECRETKEY123").filter(record)
    assert "SECRETKEY123" not in record.getMessage()
    assert P.safe("boom apikey=abc123&x=1", "zzzzzzzzzz") == "boom apikey=***&x=1"


class FixtureFMP(FakeFMP):
    """The recorded 2026-09-24 FMP answers (the core engineer's fixtures)."""

    def __init__(self, *, drop=()):
        super().__init__()
        extracts = json.loads((FIX / "extracts_2026.json").read_text())
        extracts.pop("_meta", None)
        for cik, by_period in extracts.items():
            for period, rows in by_period.items():
                self.extracts[(cik, period)] = [r for r in rows if r.get("symbol") not in drop]
        dates = json.loads((FIX / "dates.json").read_text())
        dates.pop("_meta", None)
        self.dates.update(dates)
        self._profiles = json.loads((FIX / "profiles.json").read_text())
        search = json.loads((FIX / "search.json").read_text())
        self._isin, self._cusip = search["search_isin"], search["search_cusip"]
        seed = json.loads(P.SEED_PATH.read_text())["companies"]
        for c in seed:
            if c.get("cap_symbol"):
                self.history[c["cap_symbol"]] = history(c["cap_symbol"], 1.5 * T)

    async def get_company_profiles_batch(self, symbols):
        self.calls["profiles"].append(list(symbols))
        return [copy.deepcopy(self._profiles[s]) for s in symbols if self._profiles.get(s)]

    async def search_isin(self, isin):
        return copy.deepcopy(self._isin.get(isin, []))

    async def search_cusip(self, cusip):
        return copy.deepcopy(self._cusip.get(cusip, []))


def _preview_args(tmp_path, **kw):
    return argparse.Namespace(json=str(tmp_path / "preview.json"), period=None, quarters=2,
                              check_m1=True, slug=None, **kw)


@pytest.mark.asyncio
async def test_preview_reproduces_the_m1_acceptance_figures_from_the_fixtures(tmp_path, capsys):
    code = await P.run_preview(_preview_args(tmp_path), fmp=FixtureFMP(), actions=FakeActions(),
                               secret="SECRETKEY123", now=NOW)
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.count("PASS") == len(P.M1_CHECKS) and "FAIL" not in out
    text = (tmp_path / "preview.json").read_text()
    assert "SECRETKEY123" not in text and "NaN" not in text
    payload = json.loads(text)
    assert payload["note"].startswith("READ-ONLY")
    group = payload["api"]["group"]
    slugs = [c["slug"] for c in group["companies"]]
    assert {"nvidia", "alphabet", "amazon", "amd"} <= set(slugs)
    nvidia = payload["api"]["details"]["nvidia"]
    assert len(nvidia["pro"]["holdings"]) == 8 and nvidia["free"]["is_locked"] is True
    assert len(nvidia["free"]["holdings"]) == 3 and nvidia["free"]["locked_holdings_count"] == 5
    assert "api_error" not in payload
    # the API assembly restored every global it touched
    from app.services import trillion_club_service as svc
    assert settings.TRILLION_CLUB_ENABLED is False
    assert svc.get_supabase is database.get_supabase


@pytest.mark.asyncio
async def test_preview_m1_check_fails_loudly_on_a_mismatch(tmp_path, capsys):
    args = _preview_args(tmp_path, )
    args.json = None
    code = await P.run_preview(args, fmp=FixtureFMP(drop=("NAUT",)), actions=FakeActions(),
                               secret=None, now=NOW)
    out = capsys.readouterr().out
    assert code == 1 and "FAIL  amazon    NAUT no longer reported" in out


@pytest.mark.asyncio
async def test_preview_membership_applies_the_jobs_stamp_rule_to_forced_fmp_companies():
    """The preview runs the job's code: a forced FMP-sized company with no usable close is
    what the job KEEPS unstamped — not a member row built from nothing."""
    fmp = FakeFMP()
    fmp.history = {"LLY": [], "NVDA": history("NVDA", 4 * T), "TSLA": history("TSLA", 1.2 * T, n=5)}
    companies = [company("eli-lilly", cap_symbol="LLY", mode="force_in"),
                 company("nvidia", cap_symbol="NVDA"),
                 company("tesla", cap_symbol="TSLA", mode="force_out"),
                 company("aramco", cap_source="manual", mode="force_in", country="SA")]
    rows = {r["slug"]: r for r in await P.preview_membership(companies, fmp, NOW, None)}
    assert rows["eli-lilly"]["state"] is None
    assert "keeps the stored row unstamped" in rows["eli-lilly"]["error"]
    assert rows["nvidia"]["state"]["is_member"] is True and rows["nvidia"]["error"] is None
    assert rows["tesla"]["state"]["is_member"] is False and rows["tesla"]["state"]["closes_at_or_above"] == 5
    assert rows["aramco"]["state"]["is_member"] is True, "a hand-sized company never needs a close"


def test_preview_rejects_a_malformed_period():
    with pytest.raises(ValueError):
        P.parse_args(["--period", "2026Q2"])
    assert P.parse_args(["--quarters", "9"]).quarters == 4
    assert P.parse_args(["--quarters", "0"]).quarters == 1
    with pytest.raises(SystemExit):                         # M1 is about 2026-Q2 only
        P.parse_args(["--check-m1", "--period", "2026-Q3"])
    assert P.parse_args(["--check-m1", "--period", "2026-Q2"]).check_m1 is True


def test_preview_load_seed_outside_backend_and_malformed(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"companies": [{"slug": "x"}, "junk"], "stakes": "junk"}))
    rows, stakes, source = P.load_seed(seed)
    assert rows == [{"slug": "x"}] and stakes == [] and source == str(seed)
    seed.write_text(json.dumps({"companies": []}))
    with pytest.raises(SystemExit):
        P.load_seed(seed)
