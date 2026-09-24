"""Trillion-Dollar Club jobs — ADVERSARIAL tests (independent of test_trillion_club_jobs.py).

Scope: ``app/services/trillion_club/{store,jobs,scheduler}.py``, the ``main.lifespan`` spawn /
shutdown path, and ``scripts/preview_trillion_club.py``. HERMETIC: FMP is an in-memory fake,
and Supabase is either an in-memory store or an in-memory PostgREST emulator reached through
the REAL ``postgrest`` SDK over ``httpx.MockTransport`` (no socket is ever opened).

The emulator (``Wire``) implements, from migrations 147 and 175:

* ``claim_scheduled_job`` / ``finish_scheduled_job`` exactly as written in SQL (timezone
  day, ``enabled`` kill switch, ``run_day`` only on success, stale-claim takeover), so the
  scheduler is exercised through the REAL ``notification_jobs.claimed_scheduled_job``;
* GET / PATCH / POST (upsert with ``resolution=ignore|merge-duplicates``) over the three
  ``trillion_club_*`` tables plus ``notification_job_state``, with the table CHECKs that a
  job-written row can hit, and JSON that refuses NaN like Postgres does.

Tests named ``test_BUG_*`` expose a real defect and are EXPECTED TO FAIL until it is fixed;
once fixed they are renamed ``test_regression_*`` and keep guarding it.
"""
from __future__ import annotations

import asyncio
import ast
import contextlib
import copy
import functools
import json
import logging
import re
import subprocess
import sys
import threading
import types
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from postgrest import SyncPostgrestClient

import app.database as database
from app.config import settings
from app.integrations.fmp import FMPUnavailableException
from app.services import notification_jobs
from app.services.trillion_club import jobs as J
from app.services.trillion_club import rules
from app.services.trillion_club import scheduler as S
from app.services.trillion_club import store as ST
from app.services.trillion_club.builder import BUILD_COMPLETE, BUILD_DEGRADED, raw_hash_of
from app.utils.market_hours import ET

BACKEND = Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures" / "trillion_club"

NOW = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)            # Thu 07:00 EDT
TODAY = date(2026, 9, 24)
LAST_CLOSE = date(2026, 9, 23)
MONDAY_8 = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)       # Mon 08:00 EDT
T = 1_000_000_000_000
CIK = "0000000001"
Q2, Q1, Q4, Q3, Q2_25 = (2026, 2), (2026, 1), (2025, 4), (2025, 3), (2025, 2)
NY = "America/New_York"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def et(*a):
    return datetime(*a, tzinfo=ET)


# ═════════════════════════════════════════════════════════════════════════════════════
# Fake data
# ═════════════════════════════════════════════════════════════════════════════════════


def history(symbol, cap, n=260, end=LAST_CLOSE):
    rows, d = [], end
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append({"symbol": symbol, "date": d.isoformat(), "marketCap": cap})
        d -= timedelta(days=1)
    return rows


def company(slug, *, cap_symbol=None, cap_source="fmp_us", mode="auto", use_13f=False, ciks=(),
            card_kind="no_thirteen_f", published=True, is_member=False, checked_at=None,
            country="US", aliases=(), manual_as_of=None, display_name=None):
    return {
        "slug": slug, "display_name": display_name or slug.title(), "ciks": list(ciks),
        "card_kind": card_kind, "use_13f": use_13f,
        "cap_symbol": cap_symbol if cap_source != "manual" else None,
        "symbol_aliases": list(aliases), "detail_symbol": cap_symbol, "logo_symbol": cap_symbol,
        "home_country": country, "cap_source": cap_source,
        "manual_cap_usd": 1.5e12 if cap_source == "manual" else None,
        "manual_cap_as_of": manual_as_of, "membership_mode": mode, "is_member": is_member,
        "member_since": None, "last_market_cap": None, "last_cap_date": None,
        "closes_at_or_above": 0, "closes_below": 0,
        "membership_checked_at": (checked_at.isoformat() if isinstance(checked_at, datetime)
                                  else checked_at),
        "link_whale": card_kind == "whale_link", "published": published,
    }


def filer(slug="filer", cik=CIK, symbol="FILR", **kw):
    return company(slug, cap_symbol=symbol, use_13f=True, ciks=(cik,), card_kind="thirteen_f", **kw)


def xrow(y, q, cusip, symbol, shares, value, *, cik=CIK, acc=None, filed=None):
    period_end = rules.quarter_end(y, q)
    filed = filed or (period_end + timedelta(days=44)).isoformat()
    acc = acc or f"{cik}-{str(y)[2:]}-{q:06d}"
    folder = acc.replace("-", "")
    return {
        "cik": cik, "date": period_end.isoformat(), "filingDate": filed, "acceptedDate": filed,
        "link": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{acc}-index.htm",
        "nameOfIssuer": f"{symbol} CORP", "putCallShare": "", "securityCusip": cusip,
        "shares": shares, "sharesType": "SH", "symbol": symbol, "titleOfClass": "COM",
        "value": value,
    }


def book(y, q, *, cik=CIK, extra=()):
    return [xrow(y, q, "111111111", "AAA", 1000, 5_000_000, cik=cik),
            xrow(y, q, "222222222", "BBB", 2000, 3_000_000, cik=cik), *extra]


def dates_for(*periods):
    return [{"date": rules.quarter_end(y, q).isoformat(), "year": y, "quarter": q}
            for (y, q) in periods]


def label(yq):
    return rules.period_label(*yq)


class FMP:
    """FMP fake. Any value may be an Exception (raised) — or, for history, a coroutine
    function (awaited) so a test can block a call mid-run."""

    def __init__(self):
        self.history, self.dates, self.extracts = {}, {}, {}
        self.screener = [{"symbol": "NVDA", "exchangeShortName": "NASDAQ", "marketCap": 4 * T}]
        self.batch = {}
        self.calls = defaultdict(list)

    async def get_historical_market_cap(self, symbol, from_date=None, to_date=None, limit=500):
        self.calls["history"].append((symbol, from_date, to_date, limit))
        v = self.history.get(symbol, [])
        if callable(v):
            v = await v()
        if isinstance(v, BaseException):
            raise v
        rows = [r for r in v if (not from_date or r["date"] >= from_date)
                and (not to_date or r["date"] <= to_date)]
        return copy.deepcopy(rows[:limit])

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        assert strict is True
        self.calls["dates"].append(cik)
        v = self.dates.get(cik, [])
        if isinstance(v, BaseException):
            raise v
        return copy.deepcopy(v)

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        assert strict is True
        self.calls["extract"].append((cik, f"{year}-Q{quarter}"))
        v = self.extracts.get((cik, f"{year}-Q{quarter}"), [])
        if isinstance(v, BaseException):
            raise v
        return copy.deepcopy(v)

    async def get_company_profiles_batch(self, symbols):
        self.calls["profiles"].append(list(symbols))
        return [{"symbol": s, "companyName": f"{s} Corp", "exchange": "NASDAQ",
                 "ipoDate": "2001-01-02", "sector": "Technology", "isActivelyTrading": True}
                for s in symbols]

    async def search_isin(self, isin):
        return []

    async def search_cusip(self, cusip):
        return []

    async def get_company_screener(self, **kw):
        self.calls["screener"].append(kw)
        if isinstance(self.screener, BaseException):
            raise self.screener
        return copy.deepcopy(self.screener)

    async def get_market_cap_batch(self, symbols):
        self.calls["batch"].append(list(symbols))
        return [{"symbol": s, "date": "2026-09-24", "marketCap": self.batch[s]}
                for s in symbols if s in self.batch]


class Actions:
    async def get_split_rows(self, t, from_date=None, to_date=None):
        return []

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, **kw):
        return False


class Store:
    """TrillionClubStore stand-in with the DB's semantics where they matter:
    ``insert_discovered`` is ON CONFLICT (slug) DO NOTHING and the inserted row joins the
    registry. ``hooks[key]`` runs before an operation (raise, or return a coroutine)."""

    def __init__(self, companies=(), filings=None):
        self.companies = [dict(c) for c in companies]
        self.filings = {cik: {p: dict(r) for p, r in rows.items()}
                        for cik, rows in (filings or {}).items()}
        self.rows = {}                      # (cik, period) -> full upserted row
        self.membership_writes, self.filing_writes, self.inserted = [], [], []
        self.hooks = {}

    async def _hook(self, key):
        h = self.hooks.get(key)
        if h is not None:
            r = h()
            if asyncio.iscoroutine(r):
                await r

    async def read_companies(self):
        await self._hook("read_companies")
        return copy.deepcopy(self.companies)

    async def update_membership(self, slug, state, *, checked_at):
        await self._hook(("update", slug))
        payload = ST.membership_payload(state, checked_at=checked_at)
        self.membership_writes.append((slug, state, checked_at))
        for c in self.companies:
            if c["slug"] == slug:
                c.update(payload)

    async def read_filings(self, cik):
        await self._hook(("read_filings", cik))
        return copy.deepcopy(self.filings.get(cik, {}))

    async def upsert_filing(self, row, *, built_at):
        await self._hook(("upsert", row["cik"], row["period"]))
        json.dumps(row, allow_nan=False)
        self.filing_writes.append(copy.deepcopy(row))
        self.rows[(row["cik"], row["period"])] = copy.deepcopy(row)
        self.filings.setdefault(row["cik"], {})[row["period"]] = {
            "raw_hash": row["raw_hash"], "build_status": row["build_status"],
            "unresolved": row["unresolved"], "built_at": built_at.isoformat()}

    async def insert_discovered(self, rows):
        await self._hook("insert")
        taken = {c["slug"] for c in self.companies}
        out = []
        for r in rows:
            if r["slug"] in taken:
                continue
            row = dict(r) | {"published": False, "use_13f": False}
            self.inserted.append(copy.deepcopy(row))
            self.companies.append(company(row["slug"], cap_symbol=row["cap_symbol"],
                                          published=False) | row)
            taken.add(r["slug"])
            out.append(r["slug"])
        return out


def written(store):
    return [(r["cik"], r["period"]) for r in store.filing_writes]


def daily(fmp, store, now=NOW):
    return asyncio.run(J.run_daily(now, fmp=fmp, db=store, actions=Actions()))


def weekly(fmp, store, now=NOW):
    return asyncio.run(J.run_weekly(now, fmp=fmp, db=store, actions=Actions()))


def filings_fixture(periods=(Q2, Q1), *, cik=CIK, extra=None):
    fmp = FMP()
    fmp.history = {"FILR": history("FILR", 2 * T)}
    fmp.dates[cik] = dates_for(*periods)
    for (y, q) in periods:
        fmp.extracts[(cik, f"{y}-Q{q}")] = book(y, q, cik=cik, extra=(extra or {}).get((y, q), ()))
    return fmp, Store([filer(cik=cik)])


def mark_stored(fmp, store, periods, *, cik=CIK, status=BUILD_COMPLETE):
    for (y, q) in periods:
        lab = f"{y}-Q{q}"
        store.filings.setdefault(cik, {})[lab] = {
            "raw_hash": raw_hash_of(fmp.extracts[(cik, lab)]), "build_status": status,
            "unresolved": {}, "built_at": "2026-09-01T11:00:00+00:00"}


def comparable(rows):
    """Stored filing rows keyed by (cik, period), ignoring nothing but key order."""
    return {k: json.loads(json.dumps(v, sort_keys=True)) for k, v in rows.items()}


@pytest.fixture(autouse=True)
def _isolated_read_cache(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.trillion_club_service.invalidate", lambda: calls.append(1))
    return calls


# ═════════════════════════════════════════════════════════════════════════════════════
# A PostgREST emulator reached through the real SDK (no sockets)
# ═════════════════════════════════════════════════════════════════════════════════════

_BASE = "http://supabase.test/rest/v1"
_KEYS = {"trillion_club_companies": ("slug",), "trillion_club_filings": ("cik", "period"),
         "trillion_club_stakes": ("id",), "notification_job_state": ("job",)}
_COMPANY_DEFAULTS = {
    "ciks": [], "use_13f": False, "cap_symbol": None, "symbol_aliases": [], "detail_symbol": None,
    "logo_symbol": None, "home_country": "US", "manual_cap_usd": None, "manual_cap_as_of": None,
    "manual_cap_source_url": None, "manual_fx_rate": None, "manual_fx_source": None,
    "membership_mode": "auto", "is_member": False, "member_since": None, "last_market_cap": None,
    "last_cap_date": None, "closes_at_or_above": 0, "closes_below": 0,
    "membership_checked_at": None, "link_whale": False, "published": False, "reviewed_on": None,
}


def _no_nan(token):
    raise ValueError(f"invalid JSON token {token} (Postgres rejects it)")


def _company_check(row):
    """The migration-175 CHECKs a job-written company row can hit."""
    problems = []
    if not re.fullmatch(r"[a-z0-9-]{1,40}", str(row.get("slug"))):
        problems.append("slug")
    if not (isinstance(row.get("display_name"), str) and 1 <= len(row["display_name"]) <= 60):
        problems.append("display_name")
    if not re.fullmatch(r"([0-9]{10}(,[0-9]{10})*)?", ",".join(row.get("ciks") or [])):
        problems.append("ciks")
    if row.get("card_kind") not in ("thirteen_f", "no_thirteen_f", "non_us", "whale_link"):
        problems.append("card_kind")
    if not re.fullmatch(r"[A-Z]{2}", str(row.get("home_country"))):
        problems.append("home_country")
    if row.get("cap_source") not in ("fmp_us", "fmp_adr", "manual"):
        problems.append("cap_source")
    if row.get("membership_mode") not in ("auto", "force_in", "force_out"):
        problems.append("membership_mode")
    if row.get("cap_source") == "manual" and not (
            row.get("manual_cap_usd") and row.get("manual_cap_as_of")
            and row.get("manual_cap_source_url")
            and row.get("membership_mode") in ("force_in", "force_out")):
        problems.append("trillion_club_manual_cap_is_explicit")
    if row.get("cap_source") != "manual" and row.get("cap_symbol") is None:
        problems.append("trillion_club_fmp_cap_has_symbol")
    if row.get("use_13f") and not (row.get("card_kind") == "thirteen_f" and row.get("ciks")):
        problems.append("trillion_club_13f_needs_cik")
    if bool(row.get("link_whale")) != (row.get("card_kind") == "whale_link"):
        problems.append("trillion_club_whale_link_kind")
    for col in ("closes_at_or_above", "closes_below"):
        if not (isinstance(row.get(col), int) and row[col] >= 0):
            problems.append(col)
    return problems


def _filing_check(row):
    problems = []
    if not re.fullmatch(r"[0-9]{10}", str(row.get("cik"))):
        problems.append("cik")
    if not re.fullmatch(r"[0-9]{4}-Q[1-4]", str(row.get("period"))):
        problems.append("period")
    if row.get("build_status") not in ("complete", "degraded"):
        problems.append("build_status")
    if not isinstance(row.get("holdings"), list) or not isinstance(row.get("changes"), dict) \
            or not isinstance(row.get("unresolved"), dict):
        problems.append("jsonb types")
    tv = row.get("total_value")
    if tv is not None and not tv >= 0:
        problems.append("total_value")
    return problems


def _err(status, code, message):
    return httpx.Response(status, json={"code": code, "message": message, "details": None,
                                        "hint": None})


class Wire:
    def __init__(self):
        self.tables = {t: [] for t in _KEYS}
        self.log = []                      # (method, path, params, prefer)
        self.faults = []                   # fn(method, path, params, body) -> Response|None
        self.before_rpc = []               # fn(name, params) run inside the RPC "transaction"
        self.lock = threading.Lock()
        self.claim_calls = 0

    # -- helpers for tests -------------------------------------------------------------
    def ledger(self, job):
        return next(r for r in self.tables["notification_job_state"] if r["job"] == job)

    def seed_ledger(self, *jobs, enabled=True):
        for job in jobs:
            self.tables["notification_job_state"].append(
                {"job": job, "enabled": enabled, "run_day": None, "claim_at": None,
                 "runs_today": 0, "last_run_at": None, "items_written": 0, "last_error": None})

    def row(self, table, **key):
        return next(r for r in self.tables[table] if all(r.get(k) == v for k, v in key.items()))

    def requests(self, method=None, path=None):
        return [e for e in self.log if (method is None or e[0] == method)
                and (path is None or e[1] == path)]

    # -- transport -----------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/rest/v1/", 1)[1]
        params = dict(request.url.params)
        prefer = request.headers.get("prefer") or ""
        try:
            body = json.loads(request.content, parse_constant=_no_nan) if request.content else None
        except ValueError as e:
            return _err(400, "22P02", str(e))
        with self.lock:
            self.log.append((request.method, path, params, prefer))
            for fault in self.faults:
                resp = fault(request.method, path, params, body)
                if resp is not None:
                    return resp
            if path.startswith("rpc/"):
                return self._rpc(path[4:], body or {})
            if path not in self.tables:
                return _err(404, "42P01", f'relation "public.{path}" does not exist')
            if request.method == "GET":
                return self._select(path, params)
            if request.method == "PATCH":
                return self._update(path, params, body, prefer)
            if request.method == "POST":
                return self._insert(path, params, body, prefer)
            return _err(405, "PGRST", f"{request.method} not emulated")

    @staticmethod
    def _filters(params):
        out = []
        for k, v in params.items():
            if k in ("select", "order", "limit", "offset", "on_conflict", "columns"):
                continue
            op, _, val = v.partition(".")
            assert op == "eq", f"filter {k}={v} not emulated"
            out.append((k, val))
        return out

    @staticmethod
    def _text(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        return "null" if v is None else str(v)

    def _match(self, table, params):
        fs = self._filters(params)
        return [r for r in self.tables[table] if all(self._text(r.get(k)) == v for k, v in fs)]

    def _select(self, table, params):
        rows = self._match(table, params)
        for part in reversed([p for p in params.get("order", "").split(",") if p]):
            col, _, direction = part.partition(".")
            present = [r for r in rows if r.get(col) is not None]
            absent = [r for r in rows if r.get(col) is None]
            rows = sorted(present, key=lambda r: r[col], reverse=direction.startswith("desc")) + absent
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        cols = params.get("select", "*")
        if cols != "*":
            names = [c.strip() for c in cols.split(",")]
            rows = [{c: r.get(c) for c in names} for r in rows]
        return httpx.Response(200, json=copy.deepcopy(rows))

    def _checks(self, table, row):
        if table == "trillion_club_companies":
            return _company_check(row)
        if table == "trillion_club_filings":
            return _filing_check(row)
        return []

    def _update(self, table, params, body, prefer):
        rows = self._match(table, params)
        out = []
        for r in rows:
            candidate = dict(r) | body
            problems = self._checks(table, candidate)
            if problems:
                return _err(400, "23514", f"check violation on {table}: {problems}")
            r.update(body)
            out.append(copy.deepcopy(r))
        if "return=representation" in prefer:
            return httpx.Response(200, json=out)
        return httpx.Response(204)

    def _insert(self, table, params, body, prefer):
        rows = body if isinstance(body, list) else [body]
        keys = tuple(c for c in (params.get("on_conflict") or ",".join(_KEYS[table])).split(","))
        ignore = "resolution=ignore-duplicates" in prefer
        merge = "resolution=merge-duplicates" in prefer
        out = []
        for incoming in rows:
            existing = next((r for r in self.tables[table]
                             if all(r.get(k) == incoming.get(k) for k in keys)), None)
            if existing is not None:
                if ignore:
                    continue
                if not merge:
                    return _err(409, "23505", f"duplicate key on {table} {keys}")
                candidate = dict(existing) | incoming
                problems = self._checks(table, candidate)
                if problems:
                    return _err(400, "23514", f"check violation on {table}: {problems}")
                existing.update(incoming)
                out.append(copy.deepcopy(existing))
                continue
            new = (dict(_COMPANY_DEFAULTS) if table == "trillion_club_companies" else {}) | incoming
            problems = self._checks(table, new)
            if problems:
                return _err(400, "23514", f"check violation on {table}: {problems}")
            self.tables[table].append(new)
            out.append(copy.deepcopy(new))
        if "return=minimal" in prefer:
            return httpx.Response(201)
        return httpx.Response(201, json=out)

    # migrations/147_scheduled_job_state.sql, transcribed -------------------------------
    def _rpc(self, name, p):
        for hook in self.before_rpc:
            hook(name, p)
        if name == "claim_scheduled_job":
            self.claim_calls += 1
            now = datetime.fromisoformat(p["p_now"])
            today = now.astimezone(ZoneInfo(p["p_timezone"])).date().isoformat()
            rows = self.tables["notification_job_state"]
            row = next((r for r in rows if r["job"] == p["p_job"]), None)
            if row is None:
                row = {"job": p["p_job"], "enabled": True, "run_day": None, "claim_at": None,
                       "runs_today": 0, "last_run_at": None, "items_written": 0, "last_error": None}
                rows.append(row)
            stale = timedelta(seconds=max(int(p["p_stale_seconds"]), 0))
            ok = (row["enabled"] and row["run_day"] != today
                  and (row["claim_at"] is None
                       or datetime.fromisoformat(row["claim_at"]) <= now - stale))
            if ok:
                row["runs_today"] = 1 if row["run_day"] != today else row["runs_today"] + 1
                row["claim_at"] = now.isoformat()
            return httpx.Response(200, json=bool(ok))
        if name == "finish_scheduled_job":
            now = datetime.fromisoformat(p["p_now"])
            today = now.astimezone(ZoneInfo(p.get("p_timezone") or "UTC")).date().isoformat()
            for row in self.tables["notification_job_state"]:
                if row["job"] == p["p_job"]:
                    row["claim_at"] = None
                    if p["p_success"]:
                        row["run_day"] = today
                    row["last_run_at"] = now.isoformat()
                    row["items_written"] = p.get("p_items") or 0
                    row["last_error"] = p.get("p_error")
            return httpx.Response(204)
        return _err(404, "PGRST202", f"function {name} not found")


class WireSB:
    """What `get_supabase()` returns, minus auth: `.table()` and `.rpc()` over the emulator."""

    def __init__(self, wire: Wire):
        self._pg = SyncPostgrestClient(
            _BASE, http_client=httpx.Client(transport=httpx.MockTransport(wire.handle),
                                            base_url=_BASE))

    def table(self, name):
        return self._pg.from_(name)

    def rpc(self, fn, params=None):
        return self._pg.rpc(fn, params or {})


class Clock:
    def __init__(self, t):
        self.t = t


def frozen_datetime(clock):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.t if tz is None else clock.t.astimezone(tz)
    return Frozen


@pytest.fixture
def ledger(monkeypatch):
    """The REAL claimed_scheduled_job / scheduled_job_state over the emulated ledger."""
    wire = Wire()
    wire.seed_ledger(S.JOB_TRILLION_CLUB_DAILY, S.JOB_TRILLION_CLUB_WEEKLY)
    sb = WireSB(wire)
    clock = Clock(NOW)
    monkeypatch.setattr(notification_jobs, "_sb", lambda: sb)
    monkeypatch.setattr(notification_jobs, "datetime", frozen_datetime(clock))
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", True)
    monkeypatch.setattr(S, "_daily_attempts", {})
    monkeypatch.setattr(S, "_weekly_attempts", {})
    return types.SimpleNamespace(wire=wire, sb=sb, clock=clock)


async def tick_as(lg, counter, now, *, weekly_job=False):
    """One wake of ONE instance: `counter` is that process's attempt counter."""
    lg.clock.t = now
    if weekly_job:
        S._weekly_attempts = counter
        return await S._weekly_tick(now)
    S._daily_attempts = counter
    return await S._daily_tick(now)


def scripted(monkeypatch, name, outcomes, *, during=None):
    runs = []

    async def fake(now, **kw):
        runs.append(now)
        if during is not None:
            r = during()
            if asyncio.iscoroutine(r):
                await r
        out = outcomes[min(len(runs), len(outcomes)) - 1]
        if isinstance(out, BaseException):
            raise out
        return copy.deepcopy(out)

    monkeypatch.setattr(J, name, fake)
    return runs


OK = {"ok": True, "items": 3, "failures": []}
FAIL = {"ok": False, "items": 0, "failures": ["filings x: unavailable"]}


# ═════════════════════════════════════════════════════════════════════════════════════
# 1. Schedule math across DST and the ET midnight
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("now, expected", [
    (utc(2026, 11, 1, 5, 30), utc(2026, 11, 1, 12, 0)),      # 01:30 EDT, fall-back day -> 07:00 EST
    (utc(2026, 11, 1, 6, 30), utc(2026, 11, 1, 12, 0)),      # 01:30 EST (the repeated hour)
    (utc(2026, 11, 1, 12, 0), utc(2026, 11, 2, 12, 0)),      # exactly 07:00 EST -> strictly after
    (utc(2027, 3, 14, 6, 30), utc(2027, 3, 14, 11, 0)),      # 01:30 EST, spring-forward day -> 07:00 EDT
    (utc(2027, 3, 14, 10, 59), utc(2027, 3, 14, 11, 0)),
    (utc(2027, 3, 13, 11, 30), utc(2027, 3, 13, 12, 0)),     # 06:30 EST the day before
    (utc(2026, 9, 24, 3, 30), utc(2026, 9, 24, 11, 0)),      # 23:30 EDT Sep 23 (UTC already Sep 24)
    (utc(2026, 9, 23, 23, 59), utc(2026, 9, 24, 11, 0)),
])
def test_next_daily_run_is_seven_et_across_dst_and_midnight(now, expected):
    assert S.next_daily_run(now) == expected
    assert S.next_daily_run(now) > now


@pytest.mark.parametrize("now, due", [
    (utc(2027, 3, 13, 11, 59), False), (utc(2027, 3, 13, 12, 0), True),     # EST: 07:00 = 12:00Z
    (utc(2027, 3, 15, 10, 59), False), (utc(2027, 3, 15, 11, 0), True),     # EDT: 07:00 = 11:00Z
    (utc(2026, 9, 24, 3, 30), True),                                        # 23:30 ET on Sep 23
    (utc(2026, 9, 24, 4, 30), False),                                       # 00:30 ET on Sep 24
])
def test_daily_due_follows_the_new_york_wall_clock(now, due):
    assert S.daily_due(now) is due


@pytest.mark.parametrize("now, expected", [
    (utc(2026, 10, 31, 16, 0), utc(2026, 11, 2, 13, 0)),     # Sat -> Mon 08:00 EST (DST ended Sun)
    (utc(2026, 11, 1, 5, 30), utc(2026, 11, 2, 13, 0)),      # inside the repeated hour
    (utc(2027, 3, 13, 16, 0), utc(2027, 3, 15, 12, 0)),      # Sat -> Mon 08:00 EDT (DST began Sun)
    (utc(2026, 9, 29, 3, 30), utc(2026, 10, 5, 12, 0)),      # Mon 23:30 EDT (UTC says Tue)
    (utc(2026, 9, 28, 3, 30), utc(2026, 9, 28, 12, 0)),      # Sun 23:30 EDT (UTC says Mon)
])
def test_next_weekly_run_is_monday_eight_et_across_dst_and_midnight(now, expected):
    assert S.next_weekly_run(now) == expected


@pytest.mark.parametrize("now, due", [
    (utc(2026, 9, 29, 3, 30), True),       # Monday 23:30 EDT although UTC is Tuesday
    (utc(2026, 9, 28, 3, 30), False),      # Sunday 23:30 EDT although UTC is Monday
    (utc(2026, 11, 2, 12, 59), False),     # Monday 07:59 EST
    (utc(2026, 11, 2, 13, 0), True),       # Monday 08:00 EST
])
def test_weekly_due_uses_the_new_york_weekday(now, due):
    assert S.weekly_due(now) is due


# ═════════════════════════════════════════════════════════════════════════════════════
# 2. Exactly-once: the real claim helpers over migration 147's RPCs
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_instances_waking_together_run_the_daily_exactly_once(ledger, monkeypatch):
    held = []

    async def hold_the_claim():
        for _ in range(3000):                      # until the other instance has asked
            if ledger.wire.claim_calls >= 2:
                break
            await asyncio.sleep(0.001)
        held.append(ledger.wire.claim_calls)

    runs = scripted(monkeypatch, "run_daily", [OK], during=hold_the_claim)
    a, b = {}, {}
    results = await asyncio.gather(tick_as(ledger, a, NOW), tick_as(ledger, b, NOW))
    assert len(runs) == 1
    assert held == [2], "the second instance asked for the claim while the first held it"
    assert sorted(results, key=lambda x: (x is not None, x)) == [None, S.RETRY_SECONDS]
    assert sorted([a.get(TODAY, 0), b.get(TODAY, 0)]) == [0, 1], \
        "only the claimed run consumes an attempt"
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert row["run_day"] == "2026-09-24" and row["claim_at"] is None and row["items_written"] == 3
    # both wake again later that ET day: neither claims, neither runs
    calls = ledger.wire.claim_calls
    for h in (1, 5, 12):
        assert await tick_as(ledger, a, NOW + timedelta(hours=h)) is None
        assert await tick_as(ledger, b, NOW + timedelta(hours=h)) is None
    assert len(runs) == 1 and ledger.wire.claim_calls == calls


@pytest.mark.asyncio
async def test_a_failing_daily_is_bounded_per_instance_and_the_next_et_day_starts_fresh(
        ledger, monkeypatch, caplog):
    runs = scripted(monkeypatch, "run_daily", [FAIL])
    a, b = {}, {}
    for h in range(10):                                    # hourly wakes 07:00..16:00 ET
        now = NOW + timedelta(hours=h)
        await tick_as(ledger, a, now)
        await tick_as(ledger, b, now)
    assert len(runs) <= 2 * S.MAX_ATTEMPTS_PER_DAY
    assert a[TODAY] == S.MAX_ATTEMPTS_PER_DAY and b[TODAY] == S.MAX_ATTEMPTS_PER_DAY
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert row["run_day"] is None and row["claim_at"] is None and "unavailable" in row["last_error"]
    claims = ledger.wire.claim_calls
    assert await tick_as(ledger, a, NOW + timedelta(hours=15)) is None
    assert ledger.wire.claim_calls == claims, "a capped instance must not even ask for the claim"
    assert sum("all 3 attempts" in r.getMessage() and r.levelno == logging.ERROR
               for r in caplog.records) == 2
    before = len(runs)
    await tick_as(ledger, a, NOW + timedelta(days=1))
    assert len(runs) == before + 1, "the next ET day gets a fresh budget"


@pytest.mark.asyncio
async def test_a_dead_instances_daily_claim_is_taken_over_only_after_the_30_minute_window(
        ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_daily", [OK])
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    row["claim_at"] = NOW.isoformat()                  # claimed at 07:00, then the process died
    b = {}
    assert await tick_as(ledger, b, NOW + timedelta(minutes=20)) == S.RETRY_SECONDS
    assert await tick_as(ledger, b, NOW + timedelta(minutes=29, seconds=59)) == S.RETRY_SECONDS
    assert runs == [] and b.get(TODAY, 0) == 0
    assert await tick_as(ledger, b, NOW + timedelta(minutes=30)) is None
    assert len(runs) == 1 and row["run_day"] == "2026-09-24"


@pytest.mark.asyncio
async def test_a_dead_instances_weekly_claim_is_taken_over_only_after_the_hour(ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_weekly", [OK])
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_WEEKLY)
    row["claim_at"] = MONDAY_8.isoformat()
    b = {}
    assert await tick_as(ledger, b, MONDAY_8 + timedelta(minutes=50), weekly_job=True) == S.RETRY_SECONDS
    assert runs == []
    assert await tick_as(ledger, b, MONDAY_8 + timedelta(minutes=61), weekly_job=True) is None
    assert len(runs) == 1 and row["run_day"] == "2026-09-28"


@pytest.mark.asyncio
async def test_a_late_evening_success_does_not_swallow_the_next_et_days_run(ledger, monkeypatch):
    """23:30 EDT on Sep 23 is already Sep 24 in UTC. The ledger day must be the ET day, or the
    07:00 ET run on Sep 24 is skipped as 'already ran today'."""
    runs = scripted(monkeypatch, "run_daily", [OK])
    late = utc(2026, 9, 24, 3, 30)
    assert await tick_as(ledger, {}, late) is None
    assert ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)["run_day"] == "2026-09-23"
    assert await tick_as(ledger, {}, NOW) is None
    assert len(runs) == 2 and ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)["run_day"] == "2026-09-24"


@pytest.mark.asyncio
async def test_the_attempt_cap_belongs_to_the_et_day_even_after_utc_midnight(ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_daily", [FAIL])
    counter = {}
    for hour in (1, 2, 3):                                  # 21:00, 22:00, 23:00 EDT on Sep 23
        await tick_as(ledger, counter, utc(2026, 9, 24, hour, 0))
    assert len(runs) == 3 and counter == {date(2026, 9, 23): 3}
    reads = len(ledger.wire.requests("GET", "notification_job_state"))
    assert await tick_as(ledger, counter, utc(2026, 9, 24, 3, 50)) is None      # 23:50 EDT: capped
    assert await tick_as(ledger, counter, utc(2026, 9, 24, 4, 30)) is None      # 00:30 EDT: not due
    assert len(runs) == 3 and len(ledger.wire.requests("GET", "notification_job_state")) == reads
    assert await tick_as(ledger, counter, NOW) == S.RETRY_SECONDS                # 07:00 EDT Sep 24
    assert len(runs) == 4 and counter == {TODAY: 1}


@pytest.mark.asyncio
async def test_kill_switch_through_the_real_ledger(ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_daily", [OK])
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    row["enabled"] = False
    counter = {}
    assert await tick_as(ledger, counter, NOW) is None
    assert runs == [] and ledger.wire.claim_calls == 0 and counter == {}
    row["enabled"] = True                                   # operator re-enables at 09:00
    assert await tick_as(ledger, counter, NOW + timedelta(hours=2)) is None
    assert len(runs) == 1 and row["run_day"] == "2026-09-24"


@pytest.mark.asyncio
async def test_kill_switch_flipped_between_the_state_read_and_the_claim(ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_daily", [OK])
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)

    def operator_flips(name, params):
        if name == "claim_scheduled_job":
            row["enabled"] = False

    ledger.wire.before_rpc.append(operator_flips)
    counter = {}
    assert await tick_as(ledger, counter, NOW) == S.RETRY_SECONDS
    assert runs == [] and counter == {} and row["claim_at"] is None
    ledger.wire.before_rpc.clear()
    claims = ledger.wire.claim_calls
    assert await tick_as(ledger, counter, NOW + timedelta(hours=1)) is None
    assert runs == [] and ledger.wire.claim_calls == claims


@pytest.mark.asyncio
async def test_jobs_flag_turned_off_mid_run_still_settles_the_ledger_then_idles(ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_daily", [FAIL],
                    during=lambda: setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", False))
    counter = {}
    await tick_as(ledger, counter, NOW)
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert len(runs) == 1 and row["claim_at"] is None and row["last_error"]
    reads = len(ledger.wire.requests("GET", "notification_job_state"))
    assert await tick_as(ledger, counter, NOW + timedelta(hours=1)) is None
    assert len(runs) == 1
    assert len(ledger.wire.requests("GET", "notification_job_state")) == reads, \
        "with the flag off a wake must not touch the ledger at all"


@pytest.mark.asyncio
async def test_kill_switch_flipped_mid_run_keeps_the_finished_run_and_stops_the_next_day(
        ledger, monkeypatch):
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    runs = scripted(monkeypatch, "run_daily", [OK], during=lambda: row.update(enabled=False))
    counter = {}
    assert await tick_as(ledger, counter, NOW) is None
    assert row["run_day"] == "2026-09-24" and row["claim_at"] is None
    claims = ledger.wire.claim_calls
    assert await tick_as(ledger, counter, NOW + timedelta(days=1)) is None
    assert len(runs) == 1 and ledger.wire.claim_calls == claims


@pytest.mark.asyncio
async def test_a_raising_run_releases_the_claim_so_another_instance_need_not_wait(
        ledger, monkeypatch):
    runs = scripted(monkeypatch, "run_daily", [RuntimeError("boom"), OK])
    with pytest.raises(RuntimeError):
        await tick_as(ledger, {}, NOW)
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert row["claim_at"] is None and row["run_day"] is None and "RuntimeError" in row["last_error"]
    assert await tick_as(ledger, {}, NOW + timedelta(minutes=1)) is None     # a second instance
    assert len(runs) == 2 and row["run_day"] == "2026-09-24"


# ── main.lifespan: its shutdown cancels the spawned loop mid-run ─────────────────────


def _blocking_fmp():
    """Two FMP-sized companies; the second one's history blocks until cancelled."""
    fmp = FMP()
    reached = asyncio.Event()

    async def block():
        reached.set()
        await asyncio.Event().wait()

    fmp.history = {"AAA": history("AAA", 2 * T), "BBB": block}
    return fmp, reached


async def _spawn_and_cancel_mid_run(ledger, monkeypatch):
    wire = ledger.wire
    wire.tables["trillion_club_companies"] = [company("aaa", cap_symbol="AAA"),
                                              company("bbb", cap_symbol="BBB")]
    fmp, reached = _blocking_fmp()
    monkeypatch.setattr(J, "run_daily", functools.partial(J.run_daily, fmp=fmp, db=ledger.sb,
                                                          actions=Actions()))
    monkeypatch.setattr(S, "DAILY_BOOT_DELAY_SECONDS", 0)
    monkeypatch.setattr(S, "datetime", frozen_datetime(ledger.clock))
    ledger.clock.t = NOW
    task = asyncio.create_task(S.run_trillion_club_daily_loop(), name="trillion_club_daily")
    await asyncio.wait_for(reached.wait(), timeout=10)
    # what main.lifespan does on shutdown
    task.cancel()
    results = await asyncio.gather(task, return_exceptions=True)
    return results, wire


@pytest.mark.asyncio
async def test_shutdown_mid_run_settles_the_ledger_and_leaves_no_half_written_row(
        ledger, monkeypatch, caplog):
    results, wire = await _spawn_and_cancel_mid_run(ledger, monkeypatch)
    assert isinstance(results[0], asyncio.CancelledError)
    assert not [r for r in caplog.records if "tick failed" in r.getMessage()]
    row = wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert row["claim_at"] is None, "the claim is released, not parked for the stale window"
    assert row["run_day"] is None and row["last_error"] == "cancelled (shutdown)"
    assert S._daily_attempts == {TODAY: 1}
    aaa = wire.row("trillion_club_companies", slug="aaa")
    bbb = wire.row("trillion_club_companies", slug="bbb")
    assert aaa["membership_checked_at"] == NOW.isoformat() and aaa["is_member"] is True
    assert bbb["membership_checked_at"] is None and bbb["is_member"] is False
    assert [e[2].get("slug") for e in wire.requests("PATCH", "trillion_club_companies")] == ["eq.aaa"]
    # the next instance does not wait out the stale window
    runs = scripted(monkeypatch, "run_daily", [OK])
    assert await tick_as(ledger, {}, NOW + timedelta(minutes=5)) is None and len(runs) == 1


# ═════════════════════════════════════════════════════════════════════════════════════
# 3. Membership
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("now, today_et", [
    (utc(2026, 9, 24, 3, 30), date(2026, 9, 23)),       # 23:30 EDT: UTC is already the 24th
    (utc(2026, 11, 1, 4, 30), date(2026, 11, 1)),       # 00:30 EDT on the fall-back day
    (utc(2027, 3, 14, 4, 30), date(2027, 3, 13)),       # 23:30 EST the night before spring-forward
])
def test_history_window_is_400_days_ending_on_the_et_date(now, today_et):
    fmp = FMP()
    fmp.history = {"BIG": history("BIG", 2 * T, end=today_et - timedelta(days=1))}
    store = Store([company("big", cap_symbol="BIG")])
    daily(fmp, store, now=now)
    [(sym, frm, to, limit)] = fmp.calls["history"]
    assert to == today_et.isoformat()
    assert frm == (today_et - timedelta(days=J.HISTORY_LOOKBACK_DAYS)).isoformat()
    assert J.HISTORY_LOOKBACK_DAYS >= 380 and limit == J.HISTORY_LIMIT == 260


def _wire_registry(*rows):
    wire = Wire()
    wire.tables["trillion_club_companies"] = [dict(r) for r in rows]
    return wire, WireSB(wire)


def test_fail_closed_never_sends_an_update_and_never_advances_checked_at():
    stamp = "2026-09-20T11:00:00+00:00"
    wire, sb = _wire_registry(
        company("big", cap_symbol="BIG", is_member=True, checked_at=stamp),
        company("thin", cap_symbol="THIN", is_member=True, checked_at=stamp),      # < 20 rows
        company("stale", cap_symbol="STALE", is_member=True, checked_at=stamp),    # old series
        company("down", cap_symbol="DOWN", is_member=True, checked_at=stamp),      # FMP 5xx
        company("junk", cap_symbol="JUNK", is_member=True, checked_at=stamp),      # NaN/garbage
    )
    fmp = FMP()
    fmp.history = {
        "BIG": history("BIG", 2 * T), "THIN": history("THIN", 2 * T, n=5),
        "STALE": history("STALE", 2 * T, end=date(2026, 8, 1)),
        "DOWN": FMPUnavailableException("503"),
        "JUNK": [{"symbol": "JUNK", "date": "2026-09-23", "marketCap": float("nan")}] * 30,
    }
    s = asyncio.run(J.run_daily(NOW, fmp=fmp, db=sb, actions=Actions()))
    patched = [e[2]["slug"] for e in wire.requests("PATCH", "trillion_club_companies")]
    assert patched == ["eq.big"]
    for slug in ("thin", "stale", "down", "junk"):
        row = wire.row("trillion_club_companies", slug=slug)
        assert row["membership_checked_at"] == stamp and row["is_member"] is True, slug
    assert wire.row("trillion_club_companies", slug="big")["membership_checked_at"] == NOW.isoformat()
    assert s["ok"] is False and any("down" in f for f in s["failures"])
    assert sorted(s["membership"]["kept"]) == ["junk", "stale", "thin"]


def test_regression_a_forced_fmp_sized_company_is_stamped_fresh_when_fmp_returned_no_usable_close():
    """REGRESSION (fixed 2026-09-24). Was: jobs.py: "membership_checked_at is NOT advanced — the request path hides the section
    after 7 stale days, so a stamp on a row nobody refreshed would be a lie". For a force_in /
    force_out company that is FMP-sized, evaluate_membership never returns None: with no usable
    close it returns the PRIOR facts, and the job stamps the row as checked now. The request
    path hides the section only when the NEWEST stamp among FMP-sized companies is > 7 days
    old, so during an FMP soft outage (200 with no rows / a stale series) one forced company
    keeps vouching and every auto company's stale membership stays on Home indefinitely.
    (The plan's open issue invites forcing LLY in or out — exactly this shape.)"""
    stamp = "2026-09-20T11:00:00+00:00"
    wire, sb = _wire_registry(
        company("eli-lilly", cap_symbol="LLY", mode="force_in", is_member=True, checked_at=stamp),
        company("apple", cap_symbol="AAPL", is_member=True, checked_at=stamp))
    fmp = FMP()
    fmp.history = {"LLY": [], "AAPL": []}                  # FMP answered 200 with no rows
    s = asyncio.run(J.run_daily(NOW, fmp=fmp, db=sb, actions=Actions()))
    assert wire.row("trillion_club_companies", slug="apple")["membership_checked_at"] == stamp
    assert wire.row("trillion_club_companies", slug="eli-lilly")["membership_checked_at"] == stamp, \
        "no close was read for LLY, yet its membership is stamped as checked now"
    assert wire.requests("PATCH", "trillion_club_companies") == []
    assert s["ok"] is False and s["membership"]["outage"] == ["apple", "eli-lilly"], \
        "every FMP answer unusable is an outage (resilience-3), forced rows included"


@pytest.mark.asyncio
async def test_an_fmp_soft_outage_for_every_company_is_a_failed_daily_retried_the_same_day(
        ledger, monkeypatch):
    """resilience-3 through the REAL ledger. FMP answering 200 with nothing usable for every
    company used to be a SUCCESS: run_day set (no retry that day), last_error empty, and the
    only trace a STALE ERROR from day 4. Now it is a failed attempt naming the companies, and
    the same-day retry stamps them once FMP recovers."""
    stamp = (NOW - timedelta(days=2)).isoformat()
    store = Store([company("nvidia", cap_symbol="NVDA", is_member=True, checked_at=stamp),
                   company("apple", cap_symbol="AAPL", is_member=True, checked_at=stamp),
                   company("eli-lilly", cap_symbol="LLY", mode="force_in", is_member=True,
                           checked_at=stamp)])
    fmp = FMP()
    fmp.history = {"NVDA": [], "AAPL": history("AAPL", 3 * T, end=date(2026, 8, 3)), "LLY": []}
    real = J.run_daily
    monkeypatch.setattr(J, "run_daily",
                        lambda now: real(now, fmp=fmp, db=store, actions=Actions()))
    counter = {}
    assert await tick_as(ledger, counter, NOW) == S.RETRY_SECONDS
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert row["run_day"] is None and row["claim_at"] is None
    assert "no usable close for any of the 3" in row["last_error"]
    assert "eli-lilly" in row["last_error"] and store.membership_writes == []

    fmp.history = {sym: history(sym, 3 * T) for sym in ("NVDA", "AAPL", "LLY")}
    later = NOW + timedelta(hours=1)
    assert await tick_as(ledger, counter, later) is None
    row = ledger.wire.ledger(S.JOB_TRILLION_CLUB_DAILY)
    assert row["run_day"] == "2026-09-24" and row["last_error"] is None
    assert sorted(w[0] for w in store.membership_writes) == ["apple", "eli-lilly", "nvidia"]
    assert {w[2] for w in store.membership_writes} == {later}
    assert counter[TODAY] == 2


def test_a_membership_write_failing_midway_does_not_stop_or_miscount_the_others():
    fmp = FMP()
    fmp.history = {s: history(s, 2 * T) for s in ("AAA", "BBB", "CCC")}
    store = Store([company("aaa", cap_symbol="AAA"), company("bbb", cap_symbol="BBB",
                                                             checked_at=NOW - timedelta(days=5)),
                   company("ccc", cap_symbol="CCC")])

    def boom():
        raise ST.TrillionClubStoreError("update membership slug=bbb", RuntimeError("520"))

    store.hooks[("update", "bbb")] = boom
    s = daily(fmp, store)
    assert [w[0] for w in store.membership_writes] == ["aaa", "ccc"]
    assert s["items"] == 2 and s["ok"] is False and s["membership"]["written"] == ["aaa", "ccc"]
    assert [x.split(" ")[0] for x in s["stale"]] == ["bbb"], "its stored stamp is 5 days old"


# ═════════════════════════════════════════════════════════════════════════════════════
# 4. STALE thresholds
# ═════════════════════════════════════════════════════════════════════════════════════


def _stale_run(stamp, *, run=daily, published=True):
    fmp = FMP()
    fmp.history = {"OLD": history("OLD", 2 * T, n=5)}                  # fails closed: kept
    store = Store([company("old", cap_symbol="OLD", checked_at=stamp, published=published)])
    return run(fmp, store)["stale"]


@pytest.mark.parametrize("stamp, stale", [
    ((NOW - timedelta(days=3)).isoformat(), False),                    # exactly 3 days: not yet
    ((NOW - timedelta(days=3, seconds=1)).isoformat(), True),
    ("2026-09-21T11:00:00Z", False),                                   # 'Z' form, exactly 3 days
    ("2026-09-21T10:59:59Z", True),
    ("2026-09-23T11:00:00", False),                                    # naive -> read as UTC
    ("2026-09-20T11:00:00", True),
    ("not a timestamp", True),                                         # unparseable = never
    (None, True),
])
def test_stale_threshold_boundaries(stamp, stale):
    assert bool(_stale_run(stamp)) is stale


def test_weekly_reports_stale_from_the_stored_stamps_and_ignores_unpublished(caplog):
    assert _stale_run((NOW - timedelta(days=4)).isoformat(), run=weekly)
    assert _stale_run((NOW - timedelta(days=40)).isoformat(), run=weekly, published=False) == []
    assert [r for r in caplog.records if "trillion club STALE" in r.getMessage()
            and r.levelno == logging.ERROR]


# ═════════════════════════════════════════════════════════════════════════════════════
# 5. 13F filings: partial writes, cancellation, the cascade, backfill limits
# ═════════════════════════════════════════════════════════════════════════════════════

CIKS = ("0000000001", "0000000002", "0000000003")


def _three_filers():
    fmp = FMP()
    registry = []
    for i, cik in enumerate(CIKS):
        sym = f"F{i}"
        fmp.history[sym] = history(sym, 2 * T)
        fmp.dates[cik] = dates_for(Q2, Q1)
        for (y, q) in (Q2, Q1):
            fmp.extracts[(cik, f"{y}-Q{q}")] = book(y, q, cik=cik)
        registry.append(filer(f"filer-{i}", cik=cik, symbol=sym))
    return fmp, registry


def test_a_store_write_failing_after_other_filings_were_written_converges_on_the_next_run():
    fmp, registry = _three_filers()
    clean = Store(registry)
    daily(fmp, clean)

    store = Store(registry)

    def boom():
        raise ST.TrillionClubStoreError("upsert filing", RuntimeError("23514 check violation"))

    store.hooks[("upsert", CIKS[1], "2026-Q2")] = boom
    s = daily(fmp, store)
    assert (CIKS[1], "2026-Q2") not in written(store)
    assert {(c, p) for c in (CIKS[0], CIKS[2]) for p in ("2026-Q2", "2026-Q1")} <= set(written(store))
    assert s["ok"] is False and any(CIKS[1] in f and "2026-Q2" in f for f in s["failures"])
    assert s["items"] == 3 + len(written(store)), "3 membership rows + the filings that landed"

    store.hooks.clear()
    store.filing_writes.clear()
    s2 = daily(fmp, store)
    assert s2["ok"] is True and written(store) == [(CIKS[1], "2026-Q2")]
    assert comparable(store.rows) == comparable(clean.rows)


def _amended_q1():
    """Q2 and Q1 stored; Q2 holds CCC. A late 13F-HR/A adds CCC to Q1 under a new accession."""
    fmp, store = filings_fixture(extra={Q2: [xrow(2026, 2, "333333333", "CCC", 10, 1_000_000)]})
    daily(fmp, store)                                        # the original builds
    assert written(store) == [(CIK, "2026-Q2"), (CIK, "2026-Q1")]
    store.filing_writes.clear()
    fmp.extracts[(CIK, "2026-Q1")].append(
        xrow(2026, 1, "333333333", "CCC", 10, 900_000, acc=f"{CIK}-26-900001", filed="2026-09-01"))
    return fmp, store


def test_cancelled_between_the_cascade_write_and_the_amended_quarter_converges_next_week():
    fmp, clean = _amended_q1()
    weekly(fmp, clean, now=MONDAY_8)

    fmp, store = _amended_q1()
    old_q1_hash = store.filings[CIK]["2026-Q1"]["raw_hash"]

    def cancel():
        raise asyncio.CancelledError()

    store.hooks[("upsert", CIK, "2026-Q1")] = cancel
    with pytest.raises(asyncio.CancelledError):
        weekly(fmp, store, now=MONDAY_8)
    assert written(store) == [(CIK, "2026-Q2")]
    assert store.filings[CIK]["2026-Q1"]["raw_hash"] == old_q1_hash, \
        "the amended quarter keeps its OLD hash, so the next run sees the change again"
    store.hooks.clear()
    daily(fmp, store, now=MONDAY_8 + timedelta(days=1))      # a daily in between forgets nothing
    assert store.filings[CIK]["2026-Q1"]["raw_hash"] == old_q1_hash
    s = weekly(fmp, store, now=MONDAY_8 + timedelta(days=7))
    assert s["ok"] is True and s["rehash"]["cascaded"] == [f"{CIK}:2026-Q2"]
    assert comparable(store.rows) == comparable(clean.rows)


def test_two_amended_quarters_are_each_written_once_newest_dependent_first():
    extra = {Q2: [xrow(2026, 2, "333333333", "CCC", 10, 1_000_000)],
             Q1: [xrow(2026, 1, "444444444", "DDD", 10, 850_000)]}
    fmp, store = filings_fixture(periods=(Q2, Q1, Q4, Q3), extra=extra)
    daily(fmp, store)
    q1_before = store.rows[(CIK, "2026-Q1")]
    assert any(r["symbol"] == "DDD" and r["change"] == "newly_reported"
               for r in q1_before["changes"]["rows"])
    store.filing_writes.clear()
    # late amendments: DDD into 2025-Q4, CCC into 2026-Q1
    fmp.extracts[(CIK, "2025-Q4")].append(
        xrow(2025, 4, "444444444", "DDD", 10, 800_000, acc=f"{CIK}-25-900004", filed="2026-08-01"))
    fmp.extracts[(CIK, "2026-Q1")].append(
        xrow(2026, 1, "333333333", "CCC", 10, 900_000, acc=f"{CIK}-26-900001", filed="2026-09-01"))
    s = weekly(fmp, store, now=MONDAY_8)
    assert s["ok"] is True
    assert written(store) == [(CIK, "2026-Q2"), (CIK, "2026-Q1"), (CIK, "2025-Q4")]
    q2, q1 = store.rows[(CIK, "2026-Q2")], store.rows[(CIK, "2026-Q1")]
    assert not any(r["symbol"] == "CCC" and r["change"] == "newly_reported" for r in q2["changes"]["rows"])
    assert not any(r["symbol"] == "DDD" and r["change"] == "newly_reported" for r in q1["changes"]["rows"])
    assert any(r["symbol"] == "CCC" and r["change"] == "newly_reported" for r in q1["changes"]["rows"])
    assert store.filings[CIK]["2026-Q1"]["raw_hash"] == raw_hash_of(fmp.extracts[(CIK, "2026-Q1")])


def _cik_run(fmp, store, stored_periods):
    listed = J.listed_periods(fmp.dates[CIK])
    return J._CikRun(company=J.parse_company(store.companies[0]), cik=CIK, listed=listed,
                     stored={label(p): dict(store.filings[CIK][label(p)]) for p in stored_periods},
                     newest=max(listed))


@pytest.mark.parametrize("depth, cascades", [(J.MAX_CASCADE_DEPTH - 1, True), (J.MAX_CASCADE_DEPTH, False)])
def test_the_cascade_depth_guard(depth, cascades):
    fmp, store = filings_fixture(periods=(Q2, Q1, Q4))
    mark_stored(fmp, store, (Q2, Q1, Q4))
    fmp.extracts[(CIK, "2026-Q1")].append(
        xrow(2026, 1, "333333333", "CCC", 10, 900_000, acc=f"{CIK}-26-900001", filed="2026-09-01"))
    run = _cik_run(fmp, store, (Q2, Q1, Q4))
    summary = J._new_summary(J.WEEKLY, NOW)
    stage = {"ok": True, "written": [], "unchanged": [], "refused": [], "unavailable": [],
             "cascaded": [], "cascade_blocked": [], "degraded": [], "no_filings": [], "errors": []}
    ok = asyncio.run(J._process_quarter(
        run, Q1, force=False, depth=depth, fmp=J._RunFMP(fmp), store=store, actions=Actions(),
        today=TODAY, now=NOW, summary=summary, stage=stage))
    if cascades:
        assert ok is True and written(store) == [(CIK, "2026-Q2"), (CIK, "2026-Q1")]
    else:
        assert ok is False and store.filing_writes == []
        assert any("cascade depth" in e for e in stage["errors"])


def test_backfill_is_capped_at_four_quarters_even_with_forty_listed():
    periods = []
    y, q = 2026, 2
    for _ in range(40):
        periods.append((y, q))
        y, q = rules.previous_quarter(y, q)
    fmp, store = filings_fixture(periods=tuple(periods))
    store.filings[CIK] = {"2020-Q1": {"raw_hash": "x", "build_status": BUILD_DEGRADED,
                                      "unresolved": {}, "built_at": None}}
    s = daily(fmp, store)
    assert s["ok"] is True
    assert written(store) == [(CIK, label(p)) for p in periods[:4]]
    fetched = [k for _, k in fmp.calls["extract"]]
    assert sorted(set(fetched)) == sorted(label(p) for p in periods[:5])
    assert len(fetched) == 5, "each extract once per run (the 5th only as the 4th's N-1)"
    assert "2020-Q1" not in fetched, "a degraded quarter outside the window is not rebuilt"
    store.filing_writes.clear()
    fmp.calls.clear()
    daily(fmp, store)
    assert store.filing_writes == [] and len(fmp.calls["extract"]) == 1, \
        "the next day only the newest is hash-checked"


@pytest.mark.parametrize("now, q3_checked", [
    (utc(2026, 10, 1, 3, 30), True),       # Sep 30 23:30 EDT: 2025-Q3 ended exactly 365 days ago
    (utc(2026, 10, 1, 4, 30), False),      # Oct 1 00:30 EDT: 366 days
])
def test_weekly_rehash_window_boundary_is_365_days_on_the_et_date(now, q3_checked):
    fmp, store = filings_fixture(periods=(Q2, Q1, Q4, Q3))
    mark_stored(fmp, store, (Q2, Q1, Q4, Q3))
    s = weekly(fmp, store, now=now)
    checked = {k.split(":")[1] for k in s["rehash"]["unchanged"]}
    assert ("2025-Q3" in checked) is q3_checked
    assert {"2026-Q2", "2026-Q1", "2025-Q4"} <= checked


def test_the_real_store_round_trips_the_hash_so_a_second_run_writes_nothing():
    fmp, _ = filings_fixture()
    wire, sb = _wire_registry(filer())
    s1 = asyncio.run(J.run_daily(NOW, fmp=fmp, db=sb, actions=Actions()))
    assert s1["ok"] is True, s1["failures"]
    rows = wire.tables["trillion_club_filings"]
    assert sorted(r["period"] for r in rows) == ["2026-Q1", "2026-Q2"]
    for r in rows:
        assert r["built_at"] == NOW.isoformat() and r["build_status"] == BUILD_COMPLETE
    posts = len(wire.requests("POST", "trillion_club_filings"))
    s2 = asyncio.run(J.run_daily(NOW + timedelta(days=1), fmp=fmp, db=sb, actions=Actions()))
    assert s2["ok"] is True and len(wire.requests("POST", "trillion_club_filings")) == posts
    assert s2["filings"]["unchanged"] == [f"{CIK}:2026-Q2"]


def test_regression_failed_supabase_write_of_an_amended_quarter_is_recorded_as_a_successful_run():
    """REGRESSION (fixed 2026-09-24). Was: store.py promises "a failed write is reported and the run is marked unsuccessful
    (retried within the per-day attempt cap)". For any quarter that is not the newest, a
    Supabase write failure only logs "older quarter not built this run" and the run reports
    ok=True, so the ledger records success. In the weekly re-hash this strands the amended
    quarter for a WEEK (the daily never re-hashes a complete older quarter) while the
    rewritten next quarter already diffs against the amended rows."""
    fmp, store = _amended_q1()

    def boom():
        raise ST.TrillionClubStoreError("upsert filing cik=0000000001 period=2026-Q1",
                                        RuntimeError("57014 statement timeout"))

    store.hooks[("upsert", CIK, "2026-Q1")] = boom
    s = weekly(fmp, store, now=MONDAY_8)
    assert written(store) == [(CIK, "2026-Q2")]                       # the cascade landed
    assert s["ok"] is False, "a failed Supabase write must fail the run so it is retried today"


# ═════════════════════════════════════════════════════════════════════════════════════
# 6. Weekly new-filer probe
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("now, warns", [
    (utc(2026, 8, 4, 16, 0), True),        # 2025-Q2 ended 400 days before
    (utc(2026, 8, 5, 16, 0), False),       # 401 days
    (utc(2026, 8, 5, 3, 30), True),        # Aug 4 23:30 EDT: still 400 days on the ET date
])
def test_new_filer_probe_recent_window_boundary(now, warns, caplog):
    fmp = FMP()
    fmp.dates["0001318605"] = dates_for(Q2_25, (2025, 1))
    store = Store([company("tesla", cap_symbol="TSLA", ciks=("0001318605",), is_member=True)])
    s = weekly(fmp, store, now=now)
    assert bool(s["probe"]["new_filers"]) is warns
    assert bool([r for r in caplog.records if "NEW 13F FILER" in r.getMessage()]) is warns


def test_new_filer_probe_names_the_filing_cik_and_never_fetches_or_writes(caplog):
    fmp = FMP()
    fmp.dates["0000000011"] = []
    fmp.dates["0000000012"] = dates_for(Q2, Q1)
    tesla = company("tesla", cap_symbol="TSLA", ciks=("0000000011", "0000000012"), is_member=True)
    store = Store([tesla])
    s = weekly(fmp, store)
    assert s["probe"]["new_filers"] == [{"slug": "tesla", "cik": "0000000012", "newest": "2026-Q2"}]
    assert s["probe"]["probed"] == 2
    assert fmp.calls["extract"] == [] and store.filing_writes == []
    assert store.membership_writes == [] and store.inserted == []
    assert store.companies[0]["use_13f"] is False and store.companies[0]["card_kind"] == "no_thirteen_f"


# ═════════════════════════════════════════════════════════════════════════════════════
# 7. Discovery: duplicates and slugs
# ═════════════════════════════════════════════════════════════════════════════════════


def _screen(*rows):
    return [{"exchangeShortName": "NYSE", "country": "US", **r} for r in rows]


def test_discovery_is_idempotent_across_weeks():
    fmp = FMP()
    fmp.screener = _screen({"symbol": "NEWCO", "companyName": "New Co", "marketCap": 1.1 * T})
    fmp.batch = {"NEWCO": 1.05 * T}
    store = Store([company("nvidia", cap_symbol="NVDA", is_member=True)])
    weekly(fmp, store)
    weekly(fmp, store, now=MONDAY_8 + timedelta(days=7))
    assert [r["slug"] for r in store.inserted] == ["newco"]
    assert fmp.calls["batch"] == [["NEWCO"]], "the second week knows NEWCO and asks nothing"


def test_candidates_whose_slugs_collide_get_distinct_slugs_never_an_existing_one():
    fmp = FMP()
    fmp.screener = _screen({"symbol": "AB_C", "companyName": "Ab C", "marketCap": 1.1 * T},
                           {"symbol": "AB-C", "companyName": "Ab-C", "marketCap": 1.2 * T},
                           {"symbol": "HIDDEN", "companyName": "Hidden", "marketCap": 1.3 * T})
    fmp.batch = {"AB_C": 1.1 * T, "AB-C": 1.2 * T, "HIDDEN": 1.3 * T}
    registry = [company("ab-c", cap_symbol="ZZZ", published=False),        # unpublished owner row
                company("hidden-2", cap_symbol="YYY", published=False),
                company("hidden", cap_symbol="XXX", published=False)]
    store = Store(registry)
    weekly(fmp, store)
    slugs = [r["slug"] for r in store.inserted]
    assert len(slugs) == 3 and len(set(slugs)) == 3
    assert not set(slugs) & {"ab-c", "hidden", "hidden-2"}
    assert all(re.fullmatch(r"[a-z0-9-]{1,40}", s) for s in slugs)


def test_regression_discovery_inserts_a_second_registry_row_for_a_second_share_class():
    """REGRESSION (fixed 2026-09-24). Was: FMP's screener lists every share class as its own row with the company's total cap —
    the research probe saw BRK-A and BRK-B both at ~$1.1T ("Duplicate company"), and noted the
    rows carry no CIK to de-duplicate on. Discovery keys candidates by SYMBOL, so a dual-class
    newcomer is inserted as TWO registry rows (two history calls a day, two cards to review,
    and deleting one only brings it back next Monday unless it is added as an alias)."""
    fmp = FMP()
    fmp.screener = _screen(
        {"symbol": "DUAL-A", "companyName": "Dual Holdings Inc.", "marketCap": 1.1 * T},
        {"symbol": "DUAL-B", "companyName": "Dual Holdings Inc.", "marketCap": 1.1 * T})
    fmp.batch = {"DUAL-A": 1.1 * T, "DUAL-B": 1.1 * T}
    store = Store([company("nvidia", cap_symbol="NVDA", is_member=True)])
    weekly(fmp, store)
    assert len(store.inserted) == 1, \
        f"one company, two share classes -> one row; got {[r['slug'] for r in store.inserted]}"
    [row] = store.inserted
    assert (row["cap_symbol"], row["symbol_aliases"]) == ("DUAL-A", ["DUAL-B"])
    weekly(fmp, store, now=MONDAY_8 + timedelta(days=7))
    assert len(store.inserted) == 1 and fmp.calls["batch"] == [["DUAL-A", "DUAL-B"]], \
        "the next Monday both classes are known: nothing asked, nothing inserted"


def test_regression_discovery_duplicates_a_company_whose_registry_row_failed_to_parse():
    """REGRESSION (fixed 2026-09-24). Was: `known` symbols and `taken` slugs are built only from rows that PARSED. A row the DB
    accepts but parse_company rejects — e.g. cap_symbol '' after a Studio edit (the CHECK only
    demands NOT NULL) — is skipped with an ERROR, and then discovery re-inserts the same
    company under a new slug (or, on a slug clash, ON CONFLICT DO NOTHING drops it with no
    log line at all)."""
    broken = company("jp-morgan", cap_symbol="JPM", ciks=("0000019617",), card_kind="thirteen_f",
                     published=False)
    broken["cap_symbol"] = ""                               # DB-valid, parse-invalid
    fmp = FMP()
    fmp.screener = _screen({"symbol": "JPM", "companyName": "JPMorgan Chase & Co.",
                            "marketCap": 0.95 * T})
    fmp.batch = {"JPM": 0.94 * T}
    store = Store([company("nvidia", cap_symbol="NVDA", is_member=True), broken])
    s = weekly(fmp, store)
    assert s["malformed_companies"], "precondition: the row was skipped as malformed"
    assert store.inserted == [], \
        f"JPM is already in the registry (row 'jp-morgan'); inserted {[r['slug'] for r in store.inserted]}"


# ═════════════════════════════════════════════════════════════════════════════════════
# 8. The store over the real SDK: ON CONFLICT semantics, CHECKs, NaN
# ═════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_insert_discovered_never_overwrites_an_existing_company_row():
    owner_row = company("newco", cap_symbol="NEWCO", use_13f=True, ciks=(CIK,),
                        card_kind="thirteen_f", published=True, is_member=True)
    wire, sb = _wire_registry(owner_row)
    store = ST.TrillionClubStore(sb)
    fresh = J.discovered_row({"symbol": "NEWCO", "name": "Imposter", "country": "US"}, set())
    other = J.discovered_row({"symbol": "OTHER", "name": "Other", "country": "GB"}, set())
    inserted = await store.insert_discovered([fresh, other])
    assert inserted == ["other"]
    kept = wire.row("trillion_club_companies", slug="newco")
    assert kept == owner_row | {k: kept[k] for k in set(kept) - set(owner_row)}
    assert kept["published"] is True and kept["use_13f"] is True and kept["display_name"] == "Newco"
    [(method, path, params, prefer)] = wire.requests("POST", "trillion_club_companies")
    assert "resolution=ignore-duplicates" in prefer and params["on_conflict"] == "slug"
    assert wire.row("trillion_club_companies", slug="other")["published"] is False


@pytest.mark.asyncio
async def test_update_membership_for_a_slug_deleted_mid_run_raises_and_writes_nothing():
    wire, sb = _wire_registry(company("aaa", cap_symbol="AAA"))
    store = ST.TrillionClubStore(sb)
    state = rules.MembershipState(True, date(2025, 1, 2), 30, 0, 2e12, LAST_CLOSE)
    with pytest.raises(ST.TrillionClubStoreError, match="no row matched"):
        await store.update_membership("gone", state, checked_at=NOW)
    assert wire.row("trillion_club_companies", slug="aaa")["membership_checked_at"] is None


@pytest.mark.asyncio
async def test_a_postgrest_rejection_of_a_filing_row_is_a_store_error_and_writes_nothing():
    wire, sb = _wire_registry()
    store = ST.TrillionClubStore(sb)
    row = {k: None for k in ST._FILING_ROW_KEYS} | {
        "cik": CIK, "period": "2026-Q2", "holdings": [], "changes": {}, "unresolved": {},
        "build_status": "complete", "total_value": float("nan")}
    with pytest.raises(ST.TrillionClubStoreError, match="period=2026-Q2"):
        await store.upsert_filing(row, built_at=NOW)
    assert wire.tables["trillion_club_filings"] == []
    with pytest.raises(ST.TrillionClubStoreError, match="period=2026-Q3"):
        await store.upsert_filing(row | {"period": "2026-Q3", "total_value": 1.0,
                                         "build_status": "partial"}, built_at=NOW)
    assert wire.tables["trillion_club_filings"] == []


# ═════════════════════════════════════════════════════════════════════════════════════
# 9. Isolation at RUN time (not just imports): tables, verbs, modules
# ═════════════════════════════════════════════════════════════════════════════════════


def test_daily_and_weekly_through_the_real_store_touch_only_trillion_club_tables(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a job reached for the process-wide Supabase client")

    monkeypatch.setattr(database, "get_supabase", refuse)
    monkeypatch.setattr(database, "create_client", refuse)
    fmp, _ = filings_fixture()
    fmp.history["NVDA"] = history("NVDA", 4 * T)
    fmp.dates["0000000099"] = dates_for(Q2)
    fmp.screener = _screen({"symbol": "NEWCO", "companyName": "New Co", "marketCap": 1.1 * T})
    fmp.batch = {"NEWCO": 1.1 * T}
    wire, sb = _wire_registry(filer(), company("nvidia", cap_symbol="NVDA", is_member=True,
                                               ciks=("0000000099",)))
    s1 = asyncio.run(J.run_daily(NOW, fmp=fmp, db=sb, actions=Actions()))
    s2 = asyncio.run(J.run_weekly(MONDAY_8, fmp=fmp, db=sb, actions=Actions()))
    assert s1["ok"] is True and s2["ok"] is True, (s1["failures"], s2["failures"])
    assert {p for _, p, _, _ in wire.log} <= {"trillion_club_companies", "trillion_club_filings"}
    assert {m for m, _, _, _ in wire.log} <= {"GET", "PATCH", "POST"}
    assert all(p != "trillion_club_companies" or m != "POST" or "ignore-duplicates" in pref
               for m, p, _, pref in wire.log), "a company INSERT is always ON CONFLICT DO NOTHING"
    assert wire.row("trillion_club_companies", slug="newco")["published"] is False


_RUNTIME_IMPORT_PROBE = r'''
import asyncio, copy, json, socket, sys
from datetime import date, datetime, timedelta, timezone

def _deny(*a, **k):
    raise RuntimeError("network from the runtime-import probe")
socket.getaddrinfo = _deny
socket.socket.connect = _deny

import app.services.trillion_club.jobs as J
import app.services.trillion_club.scheduler  # noqa: F401
from app.services.trillion_club import rules

NOW = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)
CIK = "0000000001"

def hist(sym):
    rows, d = [], date(2026, 9, 23)
    while len(rows) < 260:
        if d.weekday() < 5:
            rows.append({"symbol": sym, "date": d.isoformat(), "marketCap": 2e12})
        d -= timedelta(days=1)
    return rows

def xrow(y, q, cusip, sym, shares, value):
    end = rules.quarter_end(y, q).isoformat()
    acc = f"{CIK}-{str(y)[2:]}-{q:06d}"
    return {"cik": CIK, "date": end, "filingDate": end, "acceptedDate": end,
            "link": f"https://www.sec.gov/Archives/edgar/data/1/{acc.replace('-', '')}/{acc}-index.htm",
            "nameOfIssuer": sym + " CORP", "putCallShare": "", "securityCusip": cusip,
            "shares": shares, "sharesType": "SH", "symbol": sym, "titleOfClass": "COM", "value": value}

class F:
    async def get_historical_market_cap(self, s, **k): return hist(s)
    async def get_institutional_filing_dates(self, cik, *, strict=False):
        return [{"year": 2026, "quarter": 2}, {"year": 2026, "quarter": 1}]
    async def get_institutional_holdings(self, cik, y, q, *, strict=False):
        return [xrow(y, q, "111111111", "AAA", 1000, 5e6), xrow(y, q, "222222222", "BBB", 2000, 3e6)]
    async def get_company_profiles_batch(self, syms):
        return [{"symbol": s, "companyName": s, "exchange": "NASDAQ", "ipoDate": "2001-01-02",
                 "sector": "Technology", "isActivelyTrading": True} for s in syms]
    async def search_isin(self, i): return []
    async def search_cusip(self, c): return []
    async def get_company_screener(self, **k):
        return [{"symbol": "NEWCO", "exchangeShortName": "NYSE", "marketCap": 1.1e12, "country": "US"}]
    async def get_market_cap_batch(self, syms): return [{"symbol": "NEWCO", "marketCap": 1.1e12}]

class A:
    async def get_split_rows(self, *a, **k): return []
    async def has_unclassified_adjustment(self, *a, **k): return False

class St:
    def __init__(self):
        self.f = {}
    async def read_companies(self):
        return [{"slug": "filer", "display_name": "Filer", "ciks": [CIK], "card_kind": "thirteen_f",
                 "use_13f": True, "cap_symbol": "FILR", "symbol_aliases": [], "detail_symbol": "FILR",
                 "logo_symbol": "FILR", "home_country": "US", "cap_source": "fmp_us",
                 "membership_mode": "auto", "is_member": True, "published": True}]
    async def update_membership(self, slug, state, *, checked_at): pass
    async def read_filings(self, cik): return copy.deepcopy(self.f)
    async def upsert_filing(self, row, *, built_at):
        self.f[row["period"]] = {"raw_hash": row["raw_hash"], "build_status": row["build_status"],
                                 "unresolved": row["unresolved"], "built_at": None}
    async def insert_discovered(self, rows): return [r["slug"] for r in rows]

st = St()
d = asyncio.run(J.run_daily(NOW, fmp=F(), db=st, actions=A()))
w = asyncio.run(J.run_weekly(NOW + timedelta(days=4), fmp=F(), db=st, actions=A()))
print("RESULT=" + json.dumps({"ok": [d["ok"], w["ok"]], "items": [d["items"], w["items"]],
                              "modules": sorted(m for m in sys.modules if m.startswith("app."))}))
'''

_FORBIDDEN_AT_RUNTIME = ("app.services.notification_senders", "app.services.push_service",
                         "app.services.push_dispatch_service", "app.services.notification_inbox_service",
                         "app.services.price_alert", "app.services.whale_service",
                         "app.services.notification_kinds", "app.services.whale_alert")


def test_running_both_jobs_loads_no_notification_push_or_whale_module():
    out = subprocess.run([sys.executable, "-c", _RUNTIME_IMPORT_PROBE], cwd=BACKEND,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-3000:]
    line = next(l for l in out.stdout.splitlines() if l.startswith("RESULT="))
    result = json.loads(line[len("RESULT="):])
    assert result["ok"] == [True, True] and result["items"][0] >= 3, result
    bad = [m for m in result["modules"] if m.startswith(_FORBIDDEN_AT_RUNTIME)
           or "apns" in m or ".push" in m]
    assert bad == []


# ═════════════════════════════════════════════════════════════════════════════════════
# 10. main.lifespan: loops cancelled before the FMP client closes
# ═════════════════════════════════════════════════════════════════════════════════════


def _shutdown_order_problems(src: str) -> list:
    tree = ast.parse(src)
    [life] = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    lines = {}
    for n in ast.walk(life):
        if isinstance(n, (ast.Yield, ast.Expr)) and isinstance(getattr(n, "value", n), ast.Yield):
            lines.setdefault("yield", n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_spawn" \
                and n.args and isinstance(n.args[0], ast.Call) and isinstance(n.args[0].func, ast.Name) \
                and n.args[0].func.id.startswith("run_trillion_club_"):
            lines.setdefault("spawns", []).append(n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "cancel" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "task":
            lines.setdefault("cancel", n.lineno)
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Name) and n.value.func.id == "close_fmp_client":
            lines.setdefault("close_fmp", n.lineno)
    problems = []
    if len(lines.get("spawns", [])) != 2:
        problems.append(f"expected 2 trillion club spawns, found {lines.get('spawns')}")
    if not all(k in lines for k in ("yield", "cancel", "close_fmp")):
        return problems + [f"missing landmarks: {lines}"]
    if any(s > lines["yield"] for s in lines.get("spawns", [])):
        problems.append("a trillion club loop is spawned after the lifespan yields")
    if not lines["yield"] < lines["cancel"] < lines["close_fmp"]:
        problems.append("background loops are not cancelled before close_fmp_client()")
    return problems


def test_main_cancels_the_spawned_loops_before_closing_the_fmp_client():
    src = (BACKEND / "app" / "main.py").read_text()
    assert _shutdown_order_problems(src) == []
    marker = "    pending = [t for t in background_tasks if not t.done()]"
    assert marker in src
    moved = src.replace("    await close_fmp_client()\n", "", 1).replace(
        marker, "    await close_fmp_client()\n" + marker, 1)
    assert moved != src and _shutdown_order_problems(moved), "the guard is not vacuous"


# ═════════════════════════════════════════════════════════════════════════════════════
# 11. The preview script can never reach Supabase
# ═════════════════════════════════════════════════════════════════════════════════════

import scripts.preview_trillion_club as P  # noqa: E402


class FixtureFMP(FMP):
    def __init__(self):
        super().__init__()
        extracts = json.loads((FIX / "extracts_2026.json").read_text())
        extracts.pop("_meta", None)
        for cik, by_period in extracts.items():
            for period, rows in by_period.items():
                self.extracts[(cik, period)] = rows
        dates = json.loads((FIX / "dates.json").read_text())
        dates.pop("_meta", None)
        self.dates.update(dates)
        self._profiles = json.loads((FIX / "profiles.json").read_text())
        search = json.loads((FIX / "search.json").read_text())
        self._isin, self._cusip = search["search_isin"], search["search_cusip"]
        for c in json.loads(P.SEED_PATH.read_text())["companies"]:
            if c.get("cap_symbol"):
                self.history[c["cap_symbol"]] = history(c["cap_symbol"], 1.5 * T)

    async def get_company_profiles_batch(self, symbols):
        return [copy.deepcopy(self._profiles[s]) for s in symbols if self._profiles.get(s)]

    async def search_isin(self, isin):
        return copy.deepcopy(self._isin.get(isin, []))

    async def search_cusip(self, cusip):
        return copy.deepcopy(self._cusip.get(cusip, []))


@pytest.mark.asyncio
async def test_a_full_preview_never_constructs_a_supabase_client_or_the_job_store(
        tmp_path, monkeypatch, capsys):
    created = []

    def no_client(*a, **k):
        created.append(a)
        raise AssertionError("the preview built a Supabase client")

    for name in ("_supabase_client", "_auth_client", "_admin_client"):
        monkeypatch.setattr(database, name, None)
    monkeypatch.setattr(database, "create_client", no_client)
    monkeypatch.setattr(ST.TrillionClubStore, "_db", lambda self: no_client())
    for name in ("run_daily", "run_weekly"):
        monkeypatch.setattr(J, name, no_client)
    args = types.SimpleNamespace(json=str(tmp_path / "p.json"), period=None, quarters=2,
                                 check_m1=True, slug=None)
    code = await P.run_preview(args, fmp=FixtureFMP(), actions=Actions(), secret=None, now=NOW)
    out = capsys.readouterr().out
    payload = json.loads((tmp_path / "p.json").read_text())
    assert code == 0, out
    assert created == [] and database._supabase_client is None
    assert payload.get("api") and "api_error" not in payload


@pytest.mark.asyncio
async def test_preview_main_arms_the_tripwire_before_anything_runs(monkeypatch):
    seen = {}

    async def capture(args, *, fmp, actions, secret, **kw):
        seen["client"] = database._supabase_client
        seen["actions"] = actions
        seen["fmp"] = fmp
        return 0

    closed = []

    async def close():
        closed.append(1)

    monkeypatch.setattr(database, "_supabase_client", None)
    monkeypatch.setattr(P, "run_preview", capture)
    monkeypatch.setattr("app.integrations.fmp.get_fmp_client", lambda: "FMP")
    monkeypatch.setattr("app.integrations.fmp.close_fmp_client", close)
    monkeypatch.setattr(logging, "basicConfig", lambda **k: None)
    assert await P.main(["--quarters", "1"]) == 0
    assert isinstance(seen["client"], P._SupabaseTripwire)
    assert isinstance(seen["actions"], P.ReadOnlyCorporateActions) and seen["fmp"] == "FMP"
    assert closed == [1]
    with pytest.raises(P.SupabaseRefused):
        database.get_supabase().table("trillion_club_companies")


@pytest.mark.parametrize("flag", ["--write", "--apply", "--commit", "--save-db"])
def test_preview_has_no_write_flag(flag):
    with pytest.raises(SystemExit):
        P.parse_args([flag])
