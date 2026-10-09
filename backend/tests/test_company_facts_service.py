"""`company_facts_service.get_company_facts` — tiers, the shared-row merge, degradation.

The row it writes (`company_profile_cache.profile_json`) is SHARED with the Overview's
formatted writer and whale_service's raw writer, each of which replaces it whole. The merge
tests are the load-bearing ones: this writer must never drop a key either of them wrote, and
must never re-date a daily or price figure by re-stamping the row.

Hermetic: an in-memory Supabase double, a fake FMP client, the REAL Overview read helper and
the REAL Overview profile builder (so "same format as the Overview" is checked, not assumed).
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

from app.services import company_facts_service as cfs
from app.services.stock_overview_service import StockOverviewService


# ── doubles ───────────────────────────────────────────────────────────────────

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
        if self.db.fail_reads and self._upsert is None:
            raise RuntimeError("supabase read down")
        if self._upsert is not None:
            if self.db.fail_writes:
                raise RuntimeError("supabase write down")
            self.db.rows[self._upsert["ticker"]] = self._upsert
            self.db.writes.append(copy.deepcopy(self._upsert))
            return _Result([self._upsert])
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
        "marketCap": 3.8e12,
    }
    raw.update(over)
    return raw


_EXECS = [
    {"name": "Timothy D. Cook", "title": "Chief Executive Officer", "yearBorn": 1960,
     "active": True, "pay": 16000000},
    {"name": "Kevan Parekh", "title": "Chief Financial Officer", "yearBorn": None},
]


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
    yield db, fmp, service
    await _settle()
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._pending_writes.clear()


async def _settle():
    for _ in range(5):
        if cfs._pending_writes:
            await asyncio.gather(*list(cfs._pending_writes), return_exceptions=True)
        await asyncio.sleep(0)


# ── the cold path ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_miss_fetches_profile_and_executives_and_writes_one_superset_row(env):
    db, fmp, service = env
    out = await cfs.get_company_facts("aapl")
    assert out["available"] is True
    assert out["name"] == "Apple Inc."
    assert out["ceo"] == "Timothy D. Cook"
    assert out["employees"] == 164000
    assert out["hq"] == {"city": "Cupertino", "state": "CA", "country": "US"}
    assert out["ipo_date"] == "1980-12-12" and "listing" in out["ipo_date_label"]
    assert out["website"] == "www.apple.com"
    assert out["is_adr"] is False and out["is_etf"] is False
    assert out["executives"][0] == {"name": "Timothy D. Cook",
                                    "title": "Chief Executive Officer",
                                    "year_born": 1960, "active": True}
    assert "pay" not in json.dumps(out["executives"]), "compensation is not part of the facts"
    assert "price" not in out and "marketCap" not in out, "facts are price-free"
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == ["AAPL"]

    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    # The Overview's own keys, in the Overview's own format (its builder, not a copy).
    built = service._build_company_profile(_raw_profile())
    for key in ("description", "ceo", "founded", "employees", "headquarters", "website",
                "sector", "industry"):
        assert row[key] == getattr(built, key), key
    assert row["country"] == "US" and row["is_adr"] is False
    assert row["isEtf"] is False and row["isFund"] is False
    assert row["facts"]["v"] == cfs.FACTS_VERSION
    assert row["facts"]["company_name"] == "Apple Inc."
    assert row["key_executives"]["rows"][0]["name"] == "Timothy D. Cook"
    assert "price" not in row and "marketCap" not in row


@pytest.mark.asyncio
async def test_the_merge_never_drops_a_key_the_overview_wrote(env):
    db, fmp, _ = env
    overview_row = {
        "description": "Old text", "ceo": "Timothy D. Cook", "founded": "1980-12-12",
        "employees": 164000, "headquarters": "Cupertino, CA", "website": "apple.com",
        "sector": "Technology", "industry": "Consumer Electronics",
        "sector_performance": 1.23, "industry_rank": "#2 of 9", "isEtf": False,
        "country": "US", "some_future_key": {"x": 1},
    }
    db.put("AAPL", overview_row, age=timedelta(hours=2))
    await cfs.get_company_facts("AAPL")
    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    for key in overview_row:
        assert key in row, f"the merge dropped the Overview's {key!r}"
    assert row["sector_performance"] == 1.23 and row["industry_rank"] == "#2 of 9"
    assert row["some_future_key"] == {"x": 1}
    assert "facts" in row and "key_executives" in row


@pytest.mark.asyncio
async def test_a_stale_base_loses_its_daily_keys_and_raw_prices_but_nothing_else(env):
    """Re-stamping must not re-date one session's sector move or a day-old price."""
    db, _, _ = env
    db.put("AAPL", {"sector_performance": -0.8, "industry_rank": "#5 of 9",
                    "image": "https://logo", "companyName": "Apple Inc.", "price": 199.0,
                    "custom": True}, age=timedelta(hours=30))
    await cfs.get_company_facts("AAPL")
    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    assert "sector_performance" not in row and "industry_rank" not in row
    assert "price" not in row
    assert row["image"] == "https://logo" and row["companyName"] == "Apple Inc."
    assert row["custom"] is True


@pytest.mark.asyncio
async def test_a_fresh_base_keeps_its_daily_keys(env):
    db, _, _ = env
    db.put("AAPL", {"sector_performance": 0.0, "industry_rank": "#1 of 3"},
           age=timedelta(hours=23))
    await cfs.get_company_facts("AAPL")
    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    assert row["sector_performance"] == 0.0 and row["industry_rank"] == "#1 of 3"


@pytest.mark.asyncio
async def test_the_write_rereads_the_row_at_write_time(env):
    """The Overview may write between this call's read and its write: its newer keys win."""
    db, fmp, _ = env
    fmp.gate = asyncio.Event()
    task = asyncio.ensure_future(cfs.get_company_facts("AAPL"))
    await asyncio.sleep(0.01)
    db.put("AAPL", {"sector_performance": 2.5, "industry_rank": "#1 of 4"})
    fmp.gate.set()
    await task
    await _settle()
    assert db.rows["AAPL"]["profile_json"]["sector_performance"] == 2.5


# ── the warm paths ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_complete_cached_row_answers_without_any_upstream_call(env):
    db, fmp, _ = env
    await cfs.get_company_facts("AAPL")
    await _settle()
    cfs.clear_memory()
    fmp.profile_calls.clear()
    fmp.exec_calls.clear()
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and out["executives"]
    assert fmp.profile_calls == [] and fmp.exec_calls == []
    reads = db.reads
    again = await cfs.get_company_facts("AAPL")
    assert again == out and db.reads == reads, "the memory tier answers the repeat"


@pytest.mark.asyncio
async def test_the_full_read_refreshes_the_overviews_formatted_row(env):
    """The Overview's formatted row has no company name, exchange or trading currency, so the
    FULL read (the profile tool) refreshes the profile — together with the executives it needs
    anyway. The profile-only read is answered by that row (`test_company_facts_modes.py`)."""
    db, fmp, _ = env
    db.put("AAPL", {"description": "x", "ceo": "Timothy D. Cook", "sector": "Technology",
                    "headquarters": "Cupertino, CA"})
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc."
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == ["AAPL"]


@pytest.mark.asyncio
async def test_a_raw_profile_row_answers_and_only_the_executives_are_fetched(env):
    db, fmp, _ = env
    raw = _raw_profile(price=199.0)
    db.put("AAPL", raw, age=timedelta(hours=5))
    before = db.rows["AAPL"]["cached_at"]
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and out["employees"] == 164000
    assert "as_of_note" in out and "as_of" not in out
    assert fmp.profile_calls == [] and fmp.exec_calls == ["AAPL"]
    await _settle()
    row = db.rows["AAPL"]
    assert row["cached_at"] == before, "an executives-only write keeps the row's own stamp"
    assert row["profile_json"]["price"] == 199.0, "nothing re-dated, so nothing dropped"
    assert row["profile_json"]["key_executives"]["rows"]


@pytest.mark.asyncio
async def test_executives_older_than_seven_days_are_refetched(env):
    db, fmp, _ = env
    await cfs.get_company_facts("AAPL")
    await _settle()
    row = db.rows["AAPL"]["profile_json"]
    row["key_executives"]["fetched_at"] = (
        datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    cfs.clear_memory()
    fmp.profile_calls.clear()
    fmp.exec_calls.clear()
    await cfs.get_company_facts("AAPL")
    assert fmp.profile_calls == [] and fmp.exec_calls == ["AAPL"]


@pytest.mark.asyncio
async def test_executives_survive_on_an_older_row_inside_their_seven_days(env):
    """The 24 h helper misses an old row, but its executives are still within 7 days."""
    db, fmp, _ = env
    stamp = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    db.put("AAPL", {"key_executives": {"fetched_at": stamp, "rows": _EXECS}},
           age=timedelta(days=3))
    out = await cfs.get_company_facts("AAPL")
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == []
    assert out["executives"][0]["name"] == "Timothy D. Cook"
    assert out["executives_as_of"].startswith(stamp[:10])


@pytest.mark.asyncio
async def test_stored_executives_are_recleaned_never_trusted(env):
    db, fmp, _ = env
    stamp = datetime.now(timezone.utc).isoformat()
    db.put("AAPL", {
        "facts": {"v": cfs.FACTS_VERSION, "company_name": "Apple Inc.", "fetched_at": stamp},
        "key_executives": {"fetched_at": stamp, "rows": [
            {"name": "N/A", "title": "CEO"}, {"name": "A" * 5000, "title": "Chair"},
            "junk", {"name": "B", "title": "CTO", "year_born": True, "active": "yes"},
        ]},
    })
    out = await cfs.get_company_facts("AAPL")
    names = [e["name"] for e in out["executives"]]
    assert "N/A" not in names
    assert all(len(n) <= 100 for n in names)
    b = next(e for e in out["executives"] if e["name"] == "B")
    assert "year_born" not in b and "active" not in b


# ── degradation ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_profile_outage_is_an_upstream_error_briefly_memoised_and_never_written(env):
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    fmp.profiles["AAPL"] = FMPUnavailableException("503 x3")
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is False and out["upstream"] is True
    assert out["error"] == "company profile could not be loaded right now"
    assert "fmp" not in json.dumps(out).lower(), "the vendor's class name stays in the log"
    await _settle()
    assert db.writes == []
    await cfs.get_company_facts("AAPL")
    assert fmp.profile_calls == ["AAPL"], "the failure memo holds the herd back"
    assert cfs._mem["AAPL"][1] == cfs._FAILURE_TTL, "and only for the short failure TTL"


@pytest.mark.asyncio
async def test_a_profile_outage_with_an_older_row_serves_it_labelled_stale(env):
    from app.integrations.fmp import FMPRateLimitException

    db, fmp, _ = env
    old = datetime.now(timezone.utc) - timedelta(days=2)
    db.put("AAPL", {"ceo": "Timothy D. Cook", "facts": {
        "v": cfs.FACTS_VERSION, "company_name": "Apple Inc.", "fetched_at": old.isoformat()}},
        age=timedelta(days=2))
    fmp.profiles["AAPL"] = FMPRateLimitException("429")
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["name"] == "Apple Inc."
    assert "stale_note" in out and out["as_of"].startswith(old.date().isoformat())
    assert "as_of_note" not in out
    await _settle()
    assert db.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [timedelta(days=5), timedelta(days=200)])
async def test_a_stale_raw_row_is_dated_by_its_own_stamp_never_called_recent(env, age):
    """A raw whale_service row has no facts block (no stamp of its own). During an outage it is
    served dated by the ROW's cached_at — never undated beside the "24 hours" note."""
    from app.integrations.fmp import FMPRateLimitException

    db, fmp, _ = env
    db.put("AAPL", _raw_profile(), age=age)
    fmp.profiles["AAPL"] = FMPRateLimitException("429")
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["name"] == "Apple Inc."
    row_day = (datetime.now(timezone.utc) - age).date().isoformat()
    assert out["as_of"].startswith(row_day)
    assert "24 hours" not in json.dumps(out)
    assert "(see as_of)" in out["stale_note"]
    blob = json.dumps(out).lower()
    assert "fmp" not in blob and "ratelimit" not in blob


@pytest.mark.asyncio
async def test_a_stale_row_with_no_readable_date_says_its_date_is_unknown(env):
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    db.put("AAPL", _raw_profile(), age=timedelta(days=5))
    db.rows["AAPL"]["cached_at"] = "not-a-date"
    fmp.profiles["AAPL"] = FMPUnavailableException("503")
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and "as_of" not in out
    assert "date is not known" in out["as_of_note"]
    assert "24 hours" not in json.dumps(out)
    assert "see as_of" not in out["stale_note"]


def test_public_never_pairs_the_24_hour_note_with_stale():
    fields = cfs._fields_from_raw(_raw_profile())
    for as_of in (None, datetime.now(timezone.utc) - timedelta(days=9)):
        out = cfs._public("AAPL", fields, None, executives_status="ok", as_of=as_of,
                          stale=True)
        assert "24 hours" not in json.dumps(out), as_of
    fresh = cfs._public("AAPL", fields, None, executives_status="ok", as_of=None)
    assert "24 hours" in fresh["as_of_note"] and "stale_note" not in fresh


@pytest.mark.asyncio
async def test_no_profile_is_not_found_and_never_written(env):
    db, fmp, _ = env
    fmp.profiles = {}
    out = await cfs.get_company_facts("ZZZZ")
    assert out["available"] is False and out["not_found"] is True
    assert "upstream" not in out
    await _settle()
    assert db.writes == []


@pytest.mark.asyncio
async def test_a_profile_for_another_symbol_is_refused(env):
    _, fmp, _ = env
    fmp.profiles["MSFT"] = _raw_profile("AAPL")
    out = await cfs.get_company_facts("MSFT")
    assert out["available"] is False and out.get("not_found")


@pytest.mark.asyncio
async def test_a_dotted_share_class_falls_back_to_the_dash_spelling(env):
    _, fmp, _ = env
    fmp.profiles["BRK-B"] = _raw_profile("BRK-B", companyName="Berkshire Hathaway Inc.")
    out = await cfs.get_company_facts("BRK.B")
    assert out["name"] == "Berkshire Hathaway Inc." and out["ticker"] == "BRK-B"
    assert fmp.profile_calls == ["BRK.B", "BRK-B"]
    # A real dotted listing is asked for as typed and never rewritten.
    fmp.profiles["SHOP.TO"] = _raw_profile("SHOP.TO", companyName="Shopify Inc.")
    out = await cfs.get_company_facts("SHOP.TO")
    assert out["name"] == "Shopify Inc." and "SHOP-TO" not in fmp.profile_calls


@pytest.mark.asyncio
async def test_an_executives_outage_degrades_only_the_executives(env):
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    fmp.executives["AAPL"] = FMPUnavailableException("down")
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["name"] == "Apple Inc."
    assert "executives" not in out
    assert "could not be loaded" in out["executives_note"]
    await _settle()
    assert "key_executives" not in db.rows["AAPL"]["profile_json"], \
        "a failure is never stored as an executive list"
    assert cfs._mem["AAPL"][1] == cfs._FAILURE_TTL, \
        "a failed executives read is held only for the failure TTL, not the hot tier's"


@pytest.mark.asyncio
async def test_a_failed_executives_read_is_retried_after_the_failure_ttl(env, monkeypatch):
    """One blip must not answer "could not be loaded" for five minutes."""
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    clock = [1000.0]
    monkeypatch.setattr(cfs.time, "monotonic", lambda: clock[0])
    fmp.executives["AAPL"] = FMPUnavailableException("blip")
    first = await cfs.get_company_facts("AAPL")
    assert "executives" not in first
    await _settle()
    fmp.executives["AAPL"] = _EXECS
    clock[0] += cfs._FAILURE_TTL - 1
    held = await cfs.get_company_facts("AAPL")
    assert "executives" not in held and fmp.exec_calls == ["AAPL"], "the herd guard holds"
    clock[0] += 2      # past the failure TTL, well inside the 300 s hot tier
    again = await cfs.get_company_facts("AAPL")
    assert fmp.exec_calls == ["AAPL", "AAPL"], "re-fetched after the failure TTL"
    assert again["executives"][0]["name"] == "Timothy D. Cook"
    assert cfs._mem["AAPL"][1] == cfs._MEM_TTL


@pytest.mark.asyncio
async def test_an_executives_failure_on_a_cached_profile_is_also_short_lived(env):
    """The other branch: the profile came from the fresh row, only executives were fetched."""
    from app.integrations.fmp import FMPUnavailableException

    db, fmp, _ = env
    db.put("AAPL", _raw_profile(), age=timedelta(hours=2))
    fmp.executives["AAPL"] = FMPUnavailableException("blip")
    out = await cfs.get_company_facts("AAPL")
    assert fmp.profile_calls == [] and "executives" not in out
    assert cfs._mem["AAPL"][1] == cfs._FAILURE_TTL


@pytest.mark.asyncio
async def test_a_measured_empty_executive_list_is_said_and_stored(env):
    db, fmp, _ = env
    fmp.executives["AAPL"] = []
    out = await cfs.get_company_facts("AAPL")
    assert out["executives"] == [] and "No executive list" in out["executives_note"]
    await _settle()
    assert db.rows["AAPL"]["profile_json"]["key_executives"]["rows"] == []


@pytest.mark.asyncio
async def test_storage_failures_never_fail_the_answer(env):
    db, _, _ = env
    db.fail_reads = True
    db.fail_writes = True
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["name"] == "Apple Inc."
    await _settle()


@pytest.mark.asyncio
async def test_the_overview_service_failing_to_build_is_a_miss_not_a_crash(env, monkeypatch):
    def _boom():
        raise RuntimeError("service init failed")

    monkeypatch.setattr(cfs, "_overview_service", _boom)
    out = await cfs.get_company_facts("AAPL")
    assert out["available"] is True and out["name"] == "Apple Inc."
    await _settle()


# ── outliers in the profile itself ────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("employees", [float("nan"), float("inf"), True, 0, -5, "0", "n/a",
                                       "", None, 1e15, "12,000,000,000"])
async def test_an_unusable_head_count_is_omitted_never_zero(env, employees):
    _, fmp, _ = env
    fmp.profiles["AAPL"] = _raw_profile(fullTimeEmployees=employees)
    out = await cfs.get_company_facts("AAPL")
    assert "employees" not in out


@pytest.mark.asyncio
async def test_placeholders_are_dropped_and_huge_strings_bounded(env):
    _, fmp, _ = env
    fmp.profiles["AAPL"] = _raw_profile(
        ceo="N/A", sector="--", industry="", city="—", state=None, country="US",
        ipoDate="not a date", website="N/A", description="x" * 10_000, currency="usd$",
        companyName="C" * 900, isAdr="false")
    out = await cfs.get_company_facts("AAPL")
    for absent in ("ceo", "sector", "industry", "ipo_date", "website", "currency", "is_adr"):
        assert absent not in out, absent
    assert out["hq"] == {"country": "US"}
    assert len(out["description"]) == cfs._DESCRIPTION_MAX
    assert len(out["name"]) <= 160
    json.dumps(out, allow_nan=False)


@pytest.mark.asyncio
async def test_the_no_description_placeholder_is_not_a_description(env):
    _, fmp, _ = env
    fmp.profiles["AAPL"] = _raw_profile(description="No description available.")
    out = await cfs.get_company_facts("AAPL")
    assert "description" not in out


def test_executive_rows_are_deduped_current_first_and_capped():
    rows = [{"name": "Former Guy", "title": "CFO", "active": False}]
    rows += [{"name": f"P{i}", "title": "VP", "yearBorn": 1800 if i == 0 else float("nan")}
             for i in range(30)]
    rows += [{"name": "P1", "title": "VP"}, {"name": "", "title": "CEO"},
             {"name": "No Title"}, {"name": "Since", "title": "COO", "titleSince": 2019}]
    out = cfs._executive_rows(rows)
    assert len(out) == cfs._EXECUTIVES_MAX
    assert out[0]["name"] == "P0" and "year_born" not in out[0]
    assert "Former Guy" not in [r["name"] for r in out], \
        "current executives fill the cap before a former one"
    assert sum(1 for r in out if r["name"] == "P1") == 1


def test_executive_since_and_former_ordering():
    out = cfs._executive_rows([
        {"name": "Old", "title": "CEO", "active": False},
        {"name": "New", "title": "CEO", "active": True, "titleSince": "2021-05-01"},
    ])
    assert [r["name"] for r in out] == ["New", "Old"]
    assert out[0]["since"] == "2021-05-01" and out[1]["active"] is False


# ── concurrency ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_callers_share_one_fetch(env):
    _, fmp, _ = env
    fmp.gate = asyncio.Event()
    tasks = [asyncio.ensure_future(cfs.get_company_facts("AAPL")) for _ in range(5)]
    await asyncio.sleep(0.01)
    fmp.gate.set()
    results = await asyncio.gather(*tasks)
    assert fmp.profile_calls == ["AAPL"]
    assert all(r["name"] == "Apple Inc." for r in results)
    results[0]["name"] = "mutated"
    assert results[1]["name"] == "Apple Inc.", "each caller gets its own copy"


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_cancel_the_shared_fetch(env):
    _, fmp, _ = env
    fmp.gate = asyncio.Event()
    task = asyncio.ensure_future(cfs.get_company_facts("AAPL"))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fmp.gate.set()
    await asyncio.sleep(0.02)
    await _settle()
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and fmp.profile_calls == ["AAPL"], \
        "the abandoned fetch finished and warmed the cache"


@pytest.mark.asyncio
async def test_bad_input_never_raises():
    for bad in (None, "", "   ", 42, "A" * 40, "BAD TICKER"):
        out = await cfs.get_company_facts(bad)  # type: ignore[arg-type]
        assert out["available"] is False


# ── the adapter for the chat formatter ────────────────────────────────────────

def test_facts_as_profile_row_feeds_the_existing_formatter():
    from app.services.chat_service import ChatService

    facts = {"available": True, "name": "Apple Inc.", "ceo": "Timothy D. Cook",
             "sector": "Technology", "employees": 164000,
             "hq": {"city": "Cupertino", "state": "CA", "country": "US"},
             "ipo_date": "1980-12-12", "description": "Apple designs phones."}
    row = cfs.facts_as_profile_row(facts)
    assert row["headquarters"] == "Cupertino, CA" and row["country"] == "US"
    assert row["founded"] == "1980-12-12"
    text = ChatService._format_company_profile("AAPL", row)
    assert "Timothy D. Cook" in text and "Cupertino" in text
    assert cfs.facts_as_profile_row({"available": False}) is None
    assert cfs.facts_as_profile_row(None) is None


def _error_texts_built_from_exceptions(tree):
    """Nodes where an error string handed to a caller is BUILT (an f-string, or anything
    reading `__name__`): a dict value under the key "error", or an `_error_result` argument."""
    import ast

    def dynamic(node):
        return isinstance(node, ast.JoinedStr) or any(
            isinstance(n, ast.Attribute) and n.attr == "__name__" for n in ast.walk(node))

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "error" and dynamic(value):
                    hits.append(ast.unparse(value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_error_result":
            for arg in list(node.args[1:]) + [kw.value for kw in node.keywords]:
                if dynamic(arg):
                    hits.append(ast.unparse(arg))
    return hits


def test_no_error_text_is_built_from_an_exception():
    """AST over the whole module (comments cannot satisfy or trip it): the outage sentence is
    fixed, so a vendor's exception class can never reach the model."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cfs))
    assert _error_texts_built_from_exceptions(tree) == []
