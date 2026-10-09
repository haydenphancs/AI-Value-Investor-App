"""`get_company_facts` — the profile-only mode, the Overview's formatted row, the merge it now
shares with the Overview's writer, and the chat's STOCK profile line (2026-10-09).

What these pin, in the words of the change:
  * ``need_executives=False`` never calls key-executives, and ANY usable cached row answers it —
    the Overview's own formatted row included — so "open a ticker, then ask Cay AI" costs no
    upstream call;
  * the full read refreshes the formatted row's missing fields (name, exchange, currency) and,
    if that refresh fails, still answers from the row — fresh, never stale;
  * an older formatted row is the outage fallback, dated by the row's real ``cached_at``;
  * failures are memoised only for the short failure TTL; a profile-only memo never answers a
    full read;
  * `merge_profile_row` (the Overview's writer calls it) keeps every other writer's keys.

Hermetic: an in-memory Supabase double, a fake FMP client, the REAL Overview read helper and
profile builder, the REAL chat formatter.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

from app.services import company_facts_service as cfs
from app.services.stock_overview_service import StockOverviewService


# ── doubles (same shape as test_company_facts_service.py) ─────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table_name = db, table
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
        assert self.table_name == "company_profile_cache"
        if self._upsert is not None:
            if self.db.fail_writes:
                raise RuntimeError("supabase write down")
            self.db.rows[self._upsert["ticker"]] = self._upsert
            self.db.writes.append(copy.deepcopy(self._upsert))
            return _Result([self._upsert])
        if self.db.fail_reads:
            raise RuntimeError("supabase read down")
        self.db.reads += 1
        row = self.db.rows.get(self._ticker)
        return _Result([copy.deepcopy(row)] if row else [])


class _FakeDB:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.writes: List[Dict[str, Any]] = []
        self.reads = 0
        self.fail_reads = False
        self.fail_writes = False

    def table(self, name):
        return _Query(self, name)

    def put(self, ticker, profile_json, age: timedelta = timedelta(0)):
        self.rows[ticker] = {
            "ticker": ticker, "profile_json": copy.deepcopy(profile_json),
            "cached_at": (datetime.now(timezone.utc) - age).isoformat(),
        }


class _FakeFMP:
    def __init__(self, profiles=None, executives=None):
        self.profiles: Dict[str, Any] = profiles or {}
        self.executives: Dict[str, Any] = executives or {}
        self.profile_calls: List[str] = []
        self.exec_calls: List[str] = []
        self.gate: Optional[asyncio.Event] = None

    async def get_company_profile(self, sym):
        self.profile_calls.append(sym)
        if self.gate is not None:
            await self.gate.wait()
        value = self.profiles.get(sym, {})
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    async def get_key_executives(self, sym):
        self.exec_calls.append(sym)
        if self.gate is not None:
            await self.gate.wait()
        value = self.executives.get(sym, [])
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


def _raw_profile(sym="AAPL", **over):
    raw = {
        "symbol": sym, "companyName": "Apple Inc.", "ceo": "Timothy D. Cook",
        "sector": "Technology", "industry": "Consumer Electronics",
        "fullTimeEmployees": "164000", "city": "Cupertino", "state": "CA",
        "country": "US", "ipoDate": "1980-12-12", "website": "https://www.apple.com",
        "exchange": "NASDAQ", "currency": "USD", "isAdr": False, "isEtf": False,
        "isFund": False, "description": "Apple designs smartphones.", "price": 255.1,
    }
    raw.update(over)
    return raw


_EXECS = [{"name": "Timothy D. Cook", "title": "Chief Executive Officer", "active": True}]

#: Exactly what `StockOverviewService.get_overview` writes for AAPL (the 10 formatted keys,
#: the fund flags and A4's country / ADR fields).
_OVERVIEW_ROW = {
    "description": "Apple designs smartphones.", "ceo": "Timothy D. Cook",
    "founded": "1980-12-12", "employees": 164000, "headquarters": "Cupertino, CA",
    "website": "www.apple.com", "sector": "Technology", "industry": "Consumer Electronics",
    "sector_performance": 0.84, "industry_rank": "#2 of 9",
    "isEtf": False, "isFund": False, "country": "US", "is_adr": False,
}


class _NoUpstream(_FakeFMP):
    """FMP that fails the test if it is called at all."""

    async def get_company_profile(self, sym):
        raise AssertionError(f"an upstream profile call was made for {sym}")

    async def get_key_executives(self, sym):
        raise AssertionError(f"an upstream key-executives call was made for {sym}")


@pytest_asyncio.fixture
async def env(monkeypatch):
    cfs._pending_writes.clear()
    db = _FakeDB()
    fmp = _FakeFMP(profiles={"AAPL": _raw_profile()}, executives={"AAPL": _EXECS})
    service = object.__new__(StockOverviewService)
    service.supabase = db
    monkeypatch.setattr(cfs, "_overview_service", lambda: service)
    monkeypatch.setattr(cfs, "_db", lambda: db)
    monkeypatch.setattr(cfs, "_fmp", lambda: fmp)
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._profile_inflight.clear()
    yield db, fmp, service
    await _settle()
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._profile_inflight.clear()
    cfs._pending_writes.clear()


async def _settle():
    for _ in range(5):
        if cfs._pending_writes:
            await asyncio.gather(*list(cfs._pending_writes), return_exceptions=True)
        await asyncio.sleep(0)


# ── profile-only on the Overview's row ────────────────────────────────────────

@pytest.mark.asyncio
async def test_profile_only_is_answered_by_the_overviews_row_with_no_upstream_call(env, monkeypatch):
    db, _, _ = env
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    db.put("AAPL", _OVERVIEW_ROW, age=timedelta(hours=3))
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["available"] is True
    assert out["ceo"] == "Timothy D. Cook" and out["sector"] == "Technology"
    assert out["industry"] == "Consumer Electronics" and out["employees"] == 164000
    assert out["hq"] == {"location": "Cupertino, CA", "country": "US"}
    assert out["ipo_date"] == "1980-12-12" and out["website"] == "www.apple.com"
    assert out["is_adr"] is False and out["is_etf"] is False and out["is_fund"] is False
    assert out["description"] == "Apple designs smartphones."
    assert "name" not in out and "exchange" not in out and "currency" not in out, \
        "fields the row does not carry are omitted, never guessed"
    assert "executives" not in out and "not loaded" in out["executives_note"]
    assert "24 hours" in out["as_of_note"] and "stale_note" not in out
    for daily in ("sector_performance", "industry_rank"):
        assert daily not in json.dumps(out), "the day's sector move is not a company fact"
    await _settle()
    assert db.writes == [], "a hit writes nothing"


@pytest.mark.asyncio
async def test_profile_only_on_a_facts_row_and_a_raw_row_is_a_hit_too(env, monkeypatch):
    db, _, _ = env
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    db.put("AAPL", _raw_profile(), age=timedelta(hours=2))
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["name"] == "Apple Inc." and out["hq"]["city"] == "Cupertino"
    cfs.clear_memory()
    stamp = datetime.now(timezone.utc).isoformat()
    db.put("MSFT", dict(_OVERVIEW_ROW, ceo="Satya Nadella", facts={
        "v": cfs.FACTS_VERSION, "company_name": "Microsoft Corporation",
        "fetched_at": stamp}))
    out = await cfs.get_company_facts("MSFT", need_executives=False)
    assert out["name"] == "Microsoft Corporation" and out["ceo"] == "Satya Nadella"


@pytest.mark.asyncio
async def test_profile_only_miss_fetches_the_profile_alone_and_keeps_the_rows_executives(env):
    db, fmp, _ = env
    stamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    db.put("AAPL", {"key_executives": {"fetched_at": stamp, "rows": _EXECS},
                    "image": "https://logo"}, age=timedelta(days=2))
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["name"] == "Apple Inc."
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == [], \
        "a profile-only caller never triggers the key-executives call"
    assert "executives" not in out
    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    assert row["facts"]["company_name"] == "Apple Inc."
    assert row["key_executives"]["fetched_at"] == stamp, "the merge kept the stored block"
    assert row["image"] == "https://logo"


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    {"description": "No description available.", "ceo": "N/A", "founded": "N/A",
     "employees": 0, "headquarters": "N/A", "website": "N/A", "sector": "N/A",
     "industry": "N/A", "sector_performance": 0.0, "industry_rank": None},
    {"sector_performance": 1.2, "industry_rank": "#1 of 3"},
    {"headquarters": "Cupertino, CA", "website": "apple.com"},     # no core field
    {"description": "Only a description."},
])
async def test_an_overview_row_with_no_core_field_is_a_miss_in_both_modes(env, row):
    db, fmp, _ = env
    db.put("AAPL", row)
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["name"] == "Apple Inc." and fmp.profile_calls == ["AAPL"]


@pytest.mark.asyncio
async def test_a_country_only_headquarters_is_not_repeated_as_a_location(env, monkeypatch):
    db, _, _ = env
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    db.put("TM", dict(_OVERVIEW_ROW, headquarters="JP", country="JP", ceo="Koji Sato"))
    out = await cfs.get_company_facts("TM", need_executives=False)
    assert out["hq"] == {"country": "JP"}
    db.put("SAP", dict(_OVERVIEW_ROW, headquarters="Walldorf, DE", country=None))
    out = await cfs.get_company_facts("SAP", need_executives=False)
    assert out["hq"] == {"location": "Walldorf, DE"}


@pytest.mark.asyncio
@pytest.mark.parametrize("employees", [float("nan"), True, -3, 0, "n/a", 1e15])
async def test_an_overview_rows_junk_head_count_is_omitted(env, monkeypatch, employees):
    db, _, _ = env
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    db.put("AAPL", dict(_OVERVIEW_ROW, employees=employees))
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert "employees" not in out
    json.dumps(out, allow_nan=False)


# ── the full read on the Overview's row ───────────────────────────────────────

@pytest.mark.asyncio
async def test_full_read_refreshes_the_row_once_then_both_modes_hit(env):
    db, fmp, _ = env
    db.put("AAPL", _OVERVIEW_ROW, age=timedelta(hours=1))
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and out["executives"][0]["name"] == "Timothy D. Cook"
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == ["AAPL"]
    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    assert row["facts"]["company_name"] == "Apple Inc." and row["key_executives"]["rows"]
    assert row["sector_performance"] == 0.84, "a fresh base keeps the Overview's daily keys"
    cfs.clear_memory()
    fmp.profile_calls.clear()
    fmp.exec_calls.clear()
    assert (await cfs.get_company_facts("AAPL"))["name"] == "Apple Inc."
    cfs.clear_memory()
    assert (await cfs.get_company_facts("AAPL", need_executives=False))["name"] == "Apple Inc."
    assert fmp.profile_calls == [] and fmp.exec_calls == []


@pytest.mark.asyncio
async def test_full_read_with_fresh_executives_refreshes_only_the_profile(env):
    db, fmp, _ = env
    stamp = datetime.now(timezone.utc).isoformat()
    db.put("AAPL", dict(_OVERVIEW_ROW, key_executives={"fetched_at": stamp, "rows": _EXECS}))
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and out["executives"]
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == []


@pytest.mark.asyncio
async def test_a_failed_refresh_answers_from_the_row_fresh_never_stale(env):
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    db.put("AAPL", _OVERVIEW_ROW, age=timedelta(hours=2))
    before = db.rows["AAPL"]["cached_at"]
    fmp.profiles["AAPL"] = FMPUnavailableException("503")
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["ceo"] == "Timothy D. Cook"
    assert "name" not in out and "stale_note" not in out
    assert out["executives"][0]["name"] == "Timothy D. Cook"
    assert cfs._mem["AAPL"][1] == cfs._FAILURE_TTL, "a failed refresh is retried soon"
    assert "fmp" not in json.dumps(out).lower()
    await _settle()
    row = db.rows["AAPL"]
    assert row["cached_at"] == before, "an executives-only write keeps the row's own stamp"
    assert "facts" not in row["profile_json"] and row["profile_json"]["key_executives"]["rows"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [{}, _raw_profile("MSFT")])
async def test_a_refresh_that_finds_no_matching_profile_answers_from_the_row(env, answer):
    db, fmp, _ = env
    db.put("AAPL", _OVERVIEW_ROW)
    fmp.profiles["AAPL"] = answer
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["ceo"] == "Timothy D. Cook"
    assert "name" not in out and out.get("name") != "Apple Inc."
    assert cfs._mem["AAPL"][1] == cfs._MEM_TTL


@pytest.mark.asyncio
async def test_a_facts_block_past_its_30_days_is_refreshed_by_the_full_read_only(env, monkeypatch):
    db, fmp, _ = env
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    db.put("META", dict(_OVERVIEW_ROW, ceo="Mark Zuckerberg", facts={
        "v": cfs.FACTS_VERSION, "company_name": "Facebook, Inc.", "fetched_at": old},
        key_executives={"fetched_at": fresh, "rows": _EXECS}))
    fmp.profiles["META"] = _raw_profile("META", companyName="Meta Platforms, Inc.")
    profile_only = await cfs.get_company_facts("META", need_executives=False)
    assert profile_only["ceo"] == "Mark Zuckerberg" and fmp.profile_calls == []
    assert profile_only["as_of"].startswith(old[:10]), "dated by the old block, never younger"
    full = await cfs.get_company_facts("META")
    assert full["name"] == "Meta Platforms, Inc." and fmp.profile_calls == ["META"]
    assert fmp.exec_calls == [], "the executives were still fresh"


# ── outage fallback ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("need", [True, False])
@pytest.mark.parametrize("age", [timedelta(hours=30), timedelta(days=90)])
async def test_an_older_overview_row_is_the_outage_fallback_dated_by_its_cached_at(env, need, age):
    from app.integrations.fmp import FMPRateLimitException

    db, fmp, _ = env
    db.put("AAPL", _OVERVIEW_ROW, age=age)
    fmp.profiles["AAPL"] = FMPRateLimitException("429")
    out = await cfs.get_company_facts("AAPL", need_executives=need)
    assert out["available"] is True and out["ceo"] == "Timothy D. Cook"
    row_day = (datetime.now(timezone.utc) - age).date().isoformat()
    assert out["as_of"].startswith(row_day), "the row's REAL cached_at"
    assert "(see as_of)" in out["stale_note"] and "24 hours" not in json.dumps(out)
    assert cfs._mem["AAPL"][1] == cfs._FAILURE_TTL
    blob = json.dumps(out).lower()
    assert "fmp" not in blob and "ratelimit" not in blob
    await _settle()
    assert db.writes == []
    if not need:
        assert fmp.exec_calls == []


@pytest.mark.asyncio
async def test_an_outage_with_only_a_placeholder_row_is_an_upstream_error(env):
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    db.put("AAPL", {"ceo": "N/A", "sector": "N/A", "headquarters": "N/A"},
           age=timedelta(days=3))
    fmp.profiles["AAPL"] = FMPUnavailableException("503")
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["available"] is False and out["upstream"] is True
    assert cfs._mem["AAPL"][1] == cfs._FAILURE_TTL


# ── the memo and the in-flight map across modes ───────────────────────────────

@pytest.mark.asyncio
async def test_a_profile_only_memo_never_answers_a_full_read(env):
    db, fmp, _ = env
    db.put("AAPL", _OVERVIEW_ROW)
    await cfs.get_company_facts("AAPL", need_executives=False)
    assert fmp.exec_calls == [] and cfs._mem["AAPL"][3] is False
    full = await cfs.get_company_facts("AAPL")
    assert full["executives"] and fmp.exec_calls == ["AAPL"]
    assert cfs._mem["AAPL"][3] is True
    calls = (list(fmp.profile_calls), list(fmp.exec_calls))
    again = await cfs.get_company_facts("AAPL", need_executives=False)
    assert again["name"] == "Apple Inc.", "a full memo answers the profile-only read"
    assert (fmp.profile_calls, fmp.exec_calls) == calls


@pytest.mark.asyncio
async def test_a_late_profile_only_result_never_downgrades_a_full_memo():
    cfs.clear_memory()
    full = {"ticker": "AAPL", "available": True, "executives": []}
    cfs._mem_set("AAPL", full, cfs._MEM_TTL, with_execs=True)
    cfs._mem_set("AAPL", {"ticker": "AAPL", "available": True}, cfs._MEM_TTL, with_execs=False)
    assert cfs._mem_get("AAPL", True) is full
    # …but an unavailable result does replace it, and answers both modes.
    err = {"ticker": "AAPL", "available": False, "upstream": True}
    cfs._mem_set("AAPL", err, cfs._FAILURE_TTL, with_execs=False)
    assert cfs._mem_get("AAPL", True) is err and cfs._mem_get("AAPL", False) is err
    cfs.clear_memory()


@pytest.mark.asyncio
async def test_a_profile_only_caller_joins_a_running_full_load(env):
    _, fmp, _ = env
    fmp.gate = asyncio.Event()
    full = asyncio.ensure_future(cfs.get_company_facts("AAPL"))
    await asyncio.sleep(0.01)
    lite = asyncio.ensure_future(cfs.get_company_facts("AAPL", need_executives=False))
    await asyncio.sleep(0.01)
    fmp.gate.set()
    a, b = await asyncio.gather(full, lite)
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == ["AAPL"]
    assert a["name"] == b["name"] == "Apple Inc."
    b["name"] = "mutated"
    assert a["name"] == "Apple Inc.", "each caller gets its own copy"


@pytest.mark.asyncio
async def test_a_full_caller_never_joins_a_profile_only_load(env):
    _, fmp, _ = env
    fmp.gate = asyncio.Event()
    lite = asyncio.ensure_future(cfs.get_company_facts("AAPL", need_executives=False))
    await asyncio.sleep(0.01)
    full = asyncio.ensure_future(cfs.get_company_facts("AAPL"))
    await asyncio.sleep(0.01)
    fmp.gate.set()
    b, a = await asyncio.gather(lite, full)
    assert "executives" in a and "executives" not in b
    assert fmp.exec_calls == ["AAPL"]


@pytest.mark.asyncio
async def test_a_cancelled_profile_only_caller_does_not_cancel_the_load(env):
    _, fmp, _ = env
    fmp.gate = asyncio.Event()
    task = asyncio.ensure_future(cfs.get_company_facts("AAPL", need_executives=False))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fmp.gate.set()
    await asyncio.sleep(0.02)
    await _settle()
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["name"] == "Apple Inc." and fmp.profile_calls == ["AAPL"]


@pytest.mark.asyncio
async def test_a_load_left_by_a_closed_loop_is_never_joined(env):
    _, fmp, _ = env

    class _OtherLoopTask:
        def get_loop(self):
            return object()

    cfs._inflight["AAPL"] = _OtherLoopTask()   # type: ignore[assignment]
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and fmp.profile_calls == ["AAPL"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "no", 0, 1])
async def test_a_non_bool_need_executives_is_the_full_read(env, flag):
    _, fmp, _ = env
    out = await cfs.get_company_facts("AAPL", need_executives=flag)  # type: ignore[arg-type]
    assert "executives" in out and fmp.exec_calls == ["AAPL"]


# ── merge_profile_row (the Overview's writer calls it) ────────────────────────

def test_merge_profile_row_keeps_every_other_writers_keys():
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    base = {"profile_json": {
        "facts": {"v": 1, "company_name": "Apple Inc."},
        "key_executives": {"fetched_at": stamp, "rows": _EXECS},
        "isEtf": False, "isFund": False, "image": "https://logo", "companyName": "Apple Inc.",
        "price": 199.0, "marketCap": 3e12, "ceo": "Old CEO", "sector_performance": -9.9,
    }, "cached_at": (now - timedelta(hours=2)).isoformat()}
    update = {"ceo": "Timothy D. Cook", "sector_performance": 0.84, "country": "US",
              "is_adr": False}
    merged = cfs.merge_profile_row(base, update, now=now)
    assert merged["facts"] == base["profile_json"]["facts"]
    assert merged["key_executives"] == base["profile_json"]["key_executives"]
    assert merged["isEtf"] is False and merged["image"] == "https://logo"
    assert merged["ceo"] == "Timothy D. Cook" and merged["sector_performance"] == 0.84
    assert merged["country"] == "US" and merged["is_adr"] is False
    assert "price" not in merged and "marketCap" not in merged, "never re-dated"
    assert base["profile_json"]["price"] == 199.0, "the input row is not mutated"


def test_merge_profile_row_drops_a_stale_bases_daily_keys_it_does_not_replace():
    now = datetime.now(timezone.utc)
    base = {"profile_json": {"sector_performance": 1.0, "industry_rank": "#1", "ceo": "X"},
            "cached_at": (now - timedelta(hours=30)).isoformat()}
    merged = cfs.merge_profile_row(base, {"description": "d"}, now=now)
    assert "sector_performance" not in merged and "industry_rank" not in merged
    assert merged["ceo"] == "X"


@pytest.mark.parametrize("row", [None, "junk", 7, [], {}, {"profile_json": "x"},
                                 {"profile_json": ["a"], "cached_at": "nope"},
                                 {"profile_json": {"a": 1}, "cached_at": None}])
def test_merge_profile_row_never_raises_on_a_malformed_row(row):
    merged = cfs.merge_profile_row(row, {"ceo": "Y"})
    assert merged["ceo"] == "Y"
    if isinstance(row, dict) and isinstance(row.get("profile_json"), dict):
        assert merged["a"] == 1


# ── the chat's STOCK profile line ─────────────────────────────────────────────

def _chat():
    from app.services.chat_service import ChatService

    return object.__new__(ChatService)


@pytest.mark.asyncio
async def test_the_chat_profile_line_is_a_cache_hit_on_the_overviews_row(env, monkeypatch):
    """Open a ticker (the Overview writes its row), then ask Cay AI: the STOCK enrichment's
    profile line costs no upstream call."""
    import app.services.stock_overview_service as sos

    db, _, service = env
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    monkeypatch.setattr(sos, "get_stock_overview_service", lambda: service)
    db.put("AAPL", _OVERVIEW_ROW, age=timedelta(hours=1))
    line = await _chat()._get_company_profile_summary("AAPL")
    assert line.startswith("Company Profile for AAPL:")
    assert "CEO: Timothy D. Cook" in line and "Employees: 164,000" in line
    assert "HQ: Cupertino, CA, US" in line and "IPO Date: 1980-12-12" in line


@pytest.mark.asyncio
async def test_the_profile_only_read_gives_the_chat_the_same_line_as_the_row(env, monkeypatch):
    """Parity: the formatter fed by `get_company_facts(..., need_executives=False)` on the
    Overview's row prints the SAME line as the formatter fed by the row itself — so a caller
    can read through the accessor without changing what the model sees, and with no upstream
    call either way."""
    from app.services.chat_service import ChatService

    db, _, _ = env
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    db.put("AAPL", _OVERVIEW_ROW)
    facts = await cfs.get_company_facts("AAPL", need_executives=False)
    via_facts = ChatService._format_company_profile("AAPL", cfs.facts_as_profile_row(facts))
    via_row = ChatService._format_company_profile(
        "AAPL", ChatService._cached_row_as_profile(dict(_OVERVIEW_ROW)))
    assert via_facts == via_row
    for hq, country in (("JP", "JP"), ("Walldorf, DE", None), ("Toronto, CA", "CA")):
        row = dict(_OVERVIEW_ROW, headquarters=hq, country=country)
        db.put("X", row)
        cfs.clear_memory()
        facts = await cfs.get_company_facts("X", need_executives=False)
        assert ChatService._format_company_profile("X", cfs.facts_as_profile_row(facts)) == \
            ChatService._format_company_profile("X", ChatService._cached_row_as_profile(row)), hq
