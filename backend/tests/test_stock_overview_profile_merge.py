"""The Overview's `company_profile_cache` write MERGES into the shared row (2026-10-09).

It used to `upsert` its formatted dict over the row WHOLE, so every detail view dropped what
the other writers keep there — `company_facts_service`'s ``facts`` / ``key_executives`` blocks,
`whale_service`'s raw-profile logo and fund flags — and the next Ask Cay AI question about the
ticker re-fetched a profile and its executives upstream. Pinned here:

  * the write re-reads the row and lays its keys over it (`merge_profile_row`), keeping every
    other key, dropping a raw profile's price fields, replacing the daily keys;
  * a failed READ skips the write (a blind write would drop the blocks); a failed write logs;
  * A1/A4's ``country`` / ``is_adr`` and the fund flags still ride on the payload;
  * no write at all from an empty profile (every field would be "N/A");
  * `get_overview` runs it off the loop, overlapped with the related tickers, and a request
    cancelled mid-flight does not lose it;
  * end to end: Overview write → profile-only facts read is a hit; facts write → Overview
    write → full facts read is still a hit.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from app.services import company_facts_service as cfs
from app.services import stock_overview_service as sos
from app.services.stock_overview_service import StockOverviewService

from test_stock_overview_unknown_symbol import _stub_everything_empty
from test_stock_overview_event_loop import _service


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "_DB"):
        self.db = db
        self._ticker = None
        self._upsert = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, value):
        assert col == "ticker"
        self._ticker = value
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        assert on_conflict == "ticker"
        self._upsert = copy.deepcopy(row)
        return self

    def execute(self):
        if self._upsert is not None:
            if self.db.fail_writes:
                raise RuntimeError("write down")
            self.db.rows[self._upsert["ticker"]] = self._upsert
            self.db.writes.append(copy.deepcopy(self._upsert))
            self.db.write_threads.append(threading.get_ident())
            return _Result([self._upsert])
        if self.db.fail_reads:
            raise RuntimeError("read down")
        self.db.reads += 1
        row = self.db.rows.get(self._ticker)
        return _Result([copy.deepcopy(row)] if row else [])


class _DB:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.writes: List[Dict[str, Any]] = []
        self.write_threads: List[int] = []
        self.reads = 0
        self.fail_reads = False
        self.fail_writes = False
        self.tables: List[str] = []

    def table(self, name):
        self.tables.append(name)
        assert name == "company_profile_cache", name
        return _Query(self)

    def put(self, ticker, blob, age=timedelta(0), cached_at=None):
        self.rows[ticker] = {
            "ticker": ticker, "profile_json": copy.deepcopy(blob),
            "cached_at": cached_at if cached_at is not None
            else (datetime.now(timezone.utc) - age).isoformat(),
        }


def _svc(db: _DB) -> StockOverviewService:
    svc = object.__new__(StockOverviewService)
    svc.supabase = db
    return svc


_PAYLOAD = {
    "description": "Apple designs smartphones.", "ceo": "Timothy D. Cook",
    "founded": "1980-12-12", "employees": 164000, "headquarters": "Cupertino, CA",
    "website": "www.apple.com", "sector": "Technology", "industry": "Consumer Electronics",
    "sector_performance": 0.84, "industry_rank": "#2 of 9", "isEtf": False, "isFund": False,
    "country": "US", "is_adr": False,
}


# ── the write itself ──────────────────────────────────────────────────────────

def test_the_write_keeps_every_other_writers_keys():
    db = _DB()
    stamp = datetime.now(timezone.utc).isoformat()
    db.put("AAPL", {
        "facts": {"v": cfs.FACTS_VERSION, "company_name": "Apple Inc.", "fetched_at": stamp},
        "key_executives": {"fetched_at": stamp, "rows": [{"name": "T", "title": "CEO"}]},
        "image": "https://logo", "companyName": "Apple Inc.", "isEtf": False,
        "price": 199.0, "mktCap": 3e12, "ceo": "Old", "sector_performance": -3.0,
    }, age=timedelta(hours=3))
    before = datetime.now(timezone.utc)
    _svc(db)._upsert_company_profile_db("AAPL", dict(_PAYLOAD, isEtf=False))
    row = db.rows["AAPL"]
    blob = row["profile_json"]
    assert blob["facts"]["company_name"] == "Apple Inc."
    assert blob["key_executives"]["rows"][0]["name"] == "T"
    assert blob["image"] == "https://logo" and blob["companyName"] == "Apple Inc."
    assert blob["ceo"] == "Timothy D. Cook" and blob["sector_performance"] == 0.84
    assert blob["country"] == "US" and blob["is_adr"] is False
    assert "price" not in blob and "mktCap" not in blob, "a raw price is never re-dated"
    assert datetime.fromisoformat(row["cached_at"]) >= before, "the row is re-stamped"
    assert len(db.writes) == 1 and db.reads == 1


def test_fund_flags_another_writer_stored_survive_a_payload_without_them():
    """`fund_flags` copies only a real bool; a payload without them must not erase the row's
    (push routing and the Updates tab read them to tell a fund from a company)."""
    db = _DB()
    db.put("SPY", {"isEtf": True, "isFund": False})
    payload = {k: v for k, v in _PAYLOAD.items() if k not in ("isEtf", "isFund")}
    _svc(db)._upsert_company_profile_db("SPY", payload)
    assert db.rows["SPY"]["profile_json"]["isEtf"] is True


def test_no_existing_row_writes_the_payload_alone():
    db = _DB()
    _svc(db)._upsert_company_profile_db("AAPL", dict(_PAYLOAD))
    assert db.rows["AAPL"]["profile_json"] == _PAYLOAD


@pytest.mark.parametrize("junk", ["text", ["a"], 7, None])
def test_a_junk_profile_json_is_no_base(junk):
    db = _DB()
    db.put("AAPL", junk)
    _svc(db)._upsert_company_profile_db("AAPL", dict(_PAYLOAD))
    assert db.rows["AAPL"]["profile_json"] == _PAYLOAD


@pytest.mark.parametrize("cached_at", ["garbage", "", None])
def test_an_unreadable_cached_at_drops_the_bases_daily_keys_but_not_the_rest(cached_at):
    db = _DB()
    db.put("AAPL", {"industry_rank": "#9", "facts": {"v": 1}}, cached_at=cached_at or "")
    _svc(db)._upsert_company_profile_db("AAPL", {"ceo": "C"})
    blob = db.rows["AAPL"]["profile_json"]
    assert "industry_rank" not in blob and blob["facts"] == {"v": 1} and blob["ceo"] == "C"


def test_a_failed_read_skips_the_write_and_says_so(caplog):
    db = _DB()
    db.put("AAPL", {"facts": {"v": 1}})
    db.fail_reads = True
    with caplog.at_level(logging.WARNING):
        _svc(db)._upsert_company_profile_db("AAPL", dict(_PAYLOAD))
    assert db.writes == [], "a blind write would drop the blocks the merge exists to keep"
    assert db.rows["AAPL"]["profile_json"] == {"facts": {"v": 1}}
    assert any("NOT cached for AAPL" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records)


def test_a_failed_write_logs_and_never_raises(caplog):
    db = _DB()
    db.fail_writes = True
    with caplog.at_level(logging.WARNING):
        _svc(db)._upsert_company_profile_db("AAPL", dict(_PAYLOAD))
    assert any("upsert failed for AAPL" in r.getMessage() for r in caplog.records)


def test_a_broken_client_never_raises():
    class _Broken:
        def table(self, name):
            raise ConnectionError("no client")

    svc = object.__new__(StockOverviewService)
    svc.supabase = _Broken()
    svc._upsert_company_profile_db("AAPL", dict(_PAYLOAD))      # must not raise


# ── get_overview ──────────────────────────────────────────────────────────────

def _harness(monkeypatch, *, profile, related=None):
    svc = _service()
    writes = _stub_everything_empty(monkeypatch, svc)

    async def _priced(ticker, chart_range, interval, extended_hours, **kwargs):
        return {"quote": {"price": 300.0}, "chart_data": []}

    async def _fundamentals(ticker):
        return {"profile": copy.deepcopy(profile)}

    async def _no_ipo_prices(*_a, **_k):
        # A real profile carries `ipoDate`, and `get_overview` then fetches the IPO-era
        # prices — upstream. Stubbed so these tests stay hermetic.
        return []

    monkeypatch.setattr(svc, "_get_volatile", _priced)
    monkeypatch.setattr(svc, "_get_fundamentals", _fundamentals)
    monkeypatch.setattr(svc.fmp, "get_historical_prices", _no_ipo_prices)
    if related is not None:
        monkeypatch.setattr(svc, "_build_related_tickers", related)
    return svc, writes


_RAW = {"symbol": "AAPL", "companyName": "Apple Inc.", "sector": "Technology",
        "ceo": "Timothy D. Cook", "country": "US", "isAdr": False, "isEtf": False,
        "isFund": False, "city": "Cupertino", "state": "CA", "ipoDate": "1980-12-12"}


@pytest.mark.asyncio
async def test_the_payload_still_carries_country_adr_and_fund_flags(monkeypatch):
    seen: Dict[str, Any] = {}
    svc, _ = _harness(monkeypatch, profile=_RAW)
    monkeypatch.setattr(svc, "_upsert_company_profile_db",
                        lambda t, payload: seen.update(payload=payload,
                                                       thread=threading.get_ident()))
    await svc.get_overview("AAPL", "3M", "1day", False)
    payload = seen["payload"]
    assert payload["country"] == "US" and payload["is_adr"] is False
    assert payload["isEtf"] is False and payload["isFund"] is False
    assert payload["ceo"] == "Timothy D. Cook" and payload["headquarters"] == "Cupertino, CA"
    assert seen["thread"] != threading.get_ident(), "the write runs off the event loop"


@pytest.mark.asyncio
async def test_the_write_overlaps_the_related_tickers_fetch(monkeypatch):
    """The related-tickers fetch waits for the write to START: if the write were still awaited
    before the fetch, this would deadlock (bounded by the timeout)."""
    started = threading.Event()

    def _write(ticker, payload):
        started.set()

    async def _related(ticker):
        for _ in range(200):
            if started.is_set():
                return []
            await asyncio.sleep(0.005)
        raise AssertionError("the profile write had not started while related tickers ran")

    svc, _ = _harness(monkeypatch, profile=_RAW, related=_related)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", _write)
    await asyncio.wait_for(svc.get_overview("AAPL", "3M", "1day", False), timeout=5)
    assert started.is_set()


@pytest.mark.asyncio
async def test_a_request_cancelled_during_the_related_fetch_still_completes_its_write(monkeypatch):
    done = threading.Event()
    release = threading.Event()

    def _slow_write(ticker, payload):
        release.wait(2)
        done.set()

    parked = asyncio.Event()

    async def _related(ticker):
        parked.set()
        await asyncio.sleep(3600)

    svc, _ = _harness(monkeypatch, profile=_RAW, related=_related)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", _slow_write)
    task = asyncio.ensure_future(svc.get_overview("AAPL", "3M", "1day", False))
    await asyncio.wait_for(parked.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sos._profile_write_tasks, "the write is held strongly after the request is gone"
    release.set()
    for _ in range(200):
        if done.is_set() and not sos._profile_write_tasks:
            break
        await asyncio.sleep(0.01)
    assert done.is_set() and not sos._profile_write_tasks


@pytest.mark.asyncio
async def test_a_write_that_raises_never_fails_the_screen(monkeypatch, caplog):
    def _boom(ticker, payload):
        raise RuntimeError("unexpected")

    svc, _ = _harness(monkeypatch, profile=_RAW)
    monkeypatch.setattr(svc, "_upsert_company_profile_db", _boom)
    with caplog.at_level(logging.WARNING):
        resp = await svc.get_overview("AAPL", "3M", "1day", False)
    assert resp.current_price == 300.0
    assert any("Company profile write for AAPL raised" in r.getMessage()
               for r in caplog.records)


# ── end to end with company_facts_service ─────────────────────────────────────

class _NoUpstream:
    async def get_company_profile(self, sym):
        raise AssertionError(f"an upstream profile call was made for {sym}")

    async def get_key_executives(self, sym):
        raise AssertionError(f"an upstream key-executives call was made for {sym}")


@pytest.fixture
def shared(monkeypatch):
    db = _DB()
    svc = _svc(db)
    monkeypatch.setattr(cfs, "_overview_service", lambda: svc)
    monkeypatch.setattr(cfs, "_db", lambda: db)
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._profile_inflight.clear()
    cfs._pending_writes.clear()
    yield db, svc
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._profile_inflight.clear()
    cfs._pending_writes.clear()


@pytest.mark.asyncio
async def test_after_an_overview_write_the_profile_only_read_is_a_hit(shared):
    db, svc = shared
    svc._upsert_company_profile_db("AAPL", dict(_PAYLOAD))
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["ceo"] == "Timothy D. Cook" and out["employees"] == 164000


@pytest.mark.asyncio
async def test_an_overview_write_after_a_facts_write_keeps_the_full_read_a_hit(shared):
    """The defect: the Overview's whole-row write dropped ``facts`` / ``key_executives``, so the
    next full read (the profile tool) re-fetched both upstream. Now it is still a hit."""
    db, svc = shared
    stamp = datetime.now(timezone.utc).isoformat()
    db.put("AAPL", dict(_PAYLOAD, facts={
        "v": cfs.FACTS_VERSION, "company_name": "Apple Inc.", "city": "Cupertino",
        "state": "CA", "exchange": "NASDAQ", "currency": "USD", "fetched_at": stamp},
        key_executives={"fetched_at": stamp,
                        "rows": [{"name": "Timothy D. Cook", "title": "CEO"}]}))
    svc._upsert_company_profile_db("AAPL", dict(_PAYLOAD, ceo="Timothy D. Cook"))
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and out["exchange"] == "NASDAQ"
    assert out["executives"][0]["name"] == "Timothy D. Cook"
