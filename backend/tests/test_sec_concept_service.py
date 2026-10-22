"""`app/services/sec_concept_service.py` — the two-tier cache over SEC XBRL concept facts.

Hermetic: a fake SEC client and an in-memory fake Supabase whose
`table(...).select/eq/in_/upsert/execute` chain records every call (it runs in a worker
thread, as the real SDK does). One end-to-end case drives the REAL `SECEdgarClient` over an
`httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest

from app.config import settings
from app.integrations import sec_edgar
from app.integrations.sec_edgar import (
    SECConfigException,
    SECException,
    SECRateLimitException,
    SECUnavailableException,
)
from app.services import _buyback_verification as bv
from app.services import sec_concept_service as scs
from app.services.sec_concept_service import SecConcepts, SecConceptService

UA = "Caydex Test sec-contact-test@example.com"
CIK = "0000050863"  # Intel
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
TAGS = bv.SEC_BUYBACK_TAGS
REPURCHASE, WITHHOLDING, PREF_A, PREF_B, PREF_C = TAGS


def _payload(tag: str, *, newest_end: str = "2026-06-27") -> Dict[str, Any]:
    """A companyconcept payload as SEC serves it: every key, every form."""
    return {
        "cik": 50863, "taxonomy": "us-gaap", "tag": tag, "label": "x", "description": "y",
        "entityName": "INTEL CORP",
        "units": {
            "USD": [
                {"start": "2025-12-28", "end": newest_end, "val": 617_000_000, "accn": "a1",
                 "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-31",
                 "frame": "CY2026Q2"},
                {"start": "2025-03-30", "end": "2025-06-28", "val": 181_000_000, "accn": "a2",
                 "fy": 2025, "fp": "Q2", "form": "10-Q/A", "filed": "2025-08-01"},
                {"start": "2024-12-29", "end": "2025-12-27", "val": 900_000_000, "accn": "a3",
                 "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-01-30"},
                # not statements FMP reads: dropped by the trim
                {"start": "2025-12-28", "end": "2026-06-27", "val": 5, "accn": "a4",
                 "form": "8-K", "filed": "2026-07-20"},
                {"start": "2025-12-28", "end": "2026-06-27", "val": 6, "form": "20-F",
                 "filed": "2026-07-20"},
                "not a row",
            ],
            "shares": [{"end": "2026-06-27", "val": 1, "form": "10-Q"}],
        },
    }


# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, db: "_FakeDB", table: str) -> None:
        self.db, self.table = db, table
        self.op: Optional[str] = None
        self.filters: List[tuple] = []
        self.rows: List[Dict[str, Any]] = []
        self.on_conflict: Optional[str] = None

    def select(self, cols: str) -> "_FakeQuery":
        self.op, self.cols = "select", cols
        return self

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col: str, vals: Any) -> "_FakeQuery":
        self.filters.append(("in", col, list(vals)))
        return self

    def upsert(self, rows: Any, on_conflict: Optional[str] = None) -> "_FakeQuery":
        self.op, self.rows, self.on_conflict = "upsert", list(rows), on_conflict
        return self

    def execute(self) -> SimpleNamespace:
        self.db.threads.add(threading.get_ident())
        self.db.calls.append(self)
        if self.op == "select":
            if self.db.fail_read:
                raise RuntimeError("PGRST205: could not find the table 'public.sec_concept_cache'")
            cik = next(v for op, c, v in self.filters if op == "eq" and c == "cik")
            concepts = next(v for op, c, v in self.filters if op == "in" and c == "concept")
            return SimpleNamespace(data=[
                dict(row) for (row_cik, concept), row in self.db.rows.items()
                if row_cik == cik and concept in concepts
            ])
        if self.op == "upsert":
            if self.db.fail_write:
                raise RuntimeError('relation "public.sec_concept_cache" does not exist')
            for row in self.rows:
                self.db.rows[(row["cik"], row["concept"])] = dict(row)
            return SimpleNamespace(data=self.rows)
        raise AssertionError(f"unexpected op {self.op}")


class _FakeDB:
    def __init__(self) -> None:
        self.rows: Dict[tuple, Dict[str, Any]] = {}
        self.calls: List[_FakeQuery] = []
        self.threads: set = set()
        self.fail_read = False
        self.fail_write = False

    def table(self, name: str) -> _FakeQuery:
        assert name == "sec_concept_cache", name
        return _FakeQuery(self, name)

    def ops(self, op: str) -> List[_FakeQuery]:
        return [q for q in self.calls if q.op == op]

    def seed(self, tag: str, *, found: bool = True, age: timedelta = timedelta(hours=1),
             newest_end: Optional[str] = "2026-06-27", facts: Any = "default") -> None:
        if facts == "default":
            facts = scs._trim_payload(_payload(tag))[0] if found else None
        self.rows[(CIK, tag)] = {
            "cik": CIK, "concept": tag, "found": found, "facts": facts,
            "newest_end": newest_end if found else None,
            "fetched_at": (NOW - age).isoformat(),
        }


class _FakeSEC:
    def __init__(self, answers: Dict[str, Any]) -> None:
        self.answers = answers
        self.calls: List[tuple] = []
        self.gate: Optional[asyncio.Event] = None

    async def get_company_concept(self, cik: str, tag: str, taxonomy: str = "us-gaap"):
        self.calls.append((cik, tag))
        if self.gate is not None:
            await self.gate.wait()
        answer = self.answers[tag]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _all_found() -> Dict[str, Any]:
    return {REPURCHASE: _payload(REPURCHASE), WITHHOLDING: _payload(WITHHOLDING),
            PREF_A: None, PREF_B: None, PREF_C: None}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    monkeypatch.setattr(scs, "_cache", {})
    monkeypatch.setattr(scs, "_inflight", {})
    monkeypatch.setattr(scs, "_pending_writes", set())
    monkeypatch.setattr(scs, "_utcnow", lambda: NOW)


async def _drain() -> None:
    """Wait for the best-effort writes the service fired off."""
    for _ in range(200):
        if not scs._pending_writes:
            return
        await asyncio.gather(*list(scs._pending_writes))
        await asyncio.sleep(0)
    raise AssertionError("writes never finished")


def _svc(db: _FakeDB, sec: Any) -> SecConceptService:
    return SecConceptService(client=sec, supabase=db)


# ── Tier 1 / Tier 2 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_cold_call_fetches_every_tag_and_a_second_call_is_a_memory_hit():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    svc = _svc(db, sec)
    first = await svc.get_concepts(CIK, TAGS)
    await _drain()
    assert isinstance(first, SecConcepts) and first.cik == CIK and first.stale is False
    assert list(first.concepts) == list(TAGS)
    assert first.concepts[PREF_A] is None and first.concepts[PREF_B] is None
    assert sorted(t for _, t in sec.calls) == sorted(TAGS)
    assert len(db.ops("select")) == 1

    second = await svc.get_concepts(CIK, TAGS)
    assert second == first
    assert len(sec.calls) == len(TAGS), "tier-1 hit: no SEC call"
    assert len(db.ops("select")) == 1, "tier-1 hit: no Supabase read"


@pytest.mark.asyncio
async def test_the_supabase_read_is_one_query_filtered_on_cik_and_the_missing_tags():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    await _svc(db, sec).get_concepts(CIK, TAGS)
    (read,) = db.ops("select")
    assert ("eq", "cik", CIK) in read.filters
    assert ("in", "concept", list(TAGS)) in read.filters
    for col in ("concept", "found", "facts", "newest_end", "fetched_at"):
        assert col in read.cols


@pytest.mark.asyncio
async def test_a_fresh_supabase_row_is_served_with_no_fetch_and_lands_in_memory():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.seed(REPURCHASE, age=timedelta(hours=23))
    db.seed(WITHHOLDING, age=timedelta(minutes=5))
    db.seed(PREF_A, found=False, age=timedelta(hours=2))
    db.seed(PREF_B, found=False, age=timedelta(hours=2))
    db.seed(PREF_C, found=False, age=timedelta(hours=2))
    svc = _svc(db, sec)
    got = await svc.get_concepts(CIK, TAGS)
    assert sec.calls == [] and db.ops("upsert") == []
    assert got.stale is False
    assert got.concepts[REPURCHASE] == db.rows[(CIK, REPURCHASE)]["facts"]
    assert got.concepts[PREF_A] is None

    await svc.get_concepts(CIK, TAGS)
    assert len(db.ops("select")) == 1, "the tier-2 hit populated tier 1"


@pytest.mark.asyncio
async def test_a_row_24_hours_old_is_refetched():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.seed(REPURCHASE, age=timedelta(hours=24))
    got = await _svc(db, sec).get_concepts(CIK, [REPURCHASE])
    assert sec.calls == [(CIK, REPURCHASE)]
    assert got.stale is False


@pytest.mark.asyncio
async def test_a_found_false_row_is_a_cached_never_used():
    db, sec = _FakeDB(), _FakeSEC({PREF_A: None})
    first = await _svc(db, sec).get_concepts(CIK, [PREF_A])
    await _drain()
    assert first.concepts == {PREF_A: None}
    row = db.rows[(CIK, PREF_A)]
    assert row["found"] is False and row["facts"] is None and row["newest_end"] is None

    scs._cache.clear()
    sec2 = _FakeSEC({PREF_A: AssertionError("must not be fetched")})
    again = await _svc(db, sec2).get_concepts(CIK, [PREF_A], newest_period_end="2026-09-27")
    assert again.concepts == {PREF_A: None} and sec2.calls == []


@pytest.mark.asyncio
async def test_a_coverage_miss_refetches_a_found_row_older_than_an_hour():
    # The row's newest fact ends 2026-03-28; FMP charts 2026-06-27 (> 7 days later): a 10-Q
    # was filed since the row was written.
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: _payload(REPURCHASE)})
    db.seed(REPURCHASE, age=timedelta(hours=2), newest_end="2026-03-28")
    got = await _svc(db, sec).get_concepts(CIK, [REPURCHASE], newest_period_end="2026-06-27")
    await _drain()
    assert sec.calls == [(CIK, REPURCHASE)]
    assert db.rows[(CIK, REPURCHASE)]["newest_end"] == "2026-06-27"
    assert got.stale is False


@pytest.mark.asyncio
@pytest.mark.parametrize("age, newest_end, period_end, refetch", [
    (timedelta(minutes=30), "2026-03-28", "2026-06-27", False),  # asked within the hour
    (timedelta(hours=2), "2026-06-21", "2026-06-27", False),     # within the 7-day slack
    (timedelta(hours=2), "2026-06-19", "2026-06-27", True),      # 8 days behind
    (timedelta(hours=2), "2026-03-28", None, False),             # no period given
    (timedelta(hours=2), None, "2026-06-27", True),              # no statement row at all
    (timedelta(hours=2), "2026-03-28", "garbage", False),        # unreadable → check skipped
])
async def test_the_coverage_rule(age, newest_end, period_end, refetch):
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: _payload(REPURCHASE)})
    db.seed(REPURCHASE, age=age, newest_end=newest_end)
    await _svc(db, sec).get_concepts(CIK, [REPURCHASE], newest_period_end=period_end)
    assert bool(sec.calls) is refetch


@pytest.mark.asyncio
async def test_a_memory_entry_is_also_held_to_the_coverage_rule():
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: _payload(REPURCHASE)})
    db.seed(REPURCHASE, age=timedelta(hours=3), newest_end="2026-03-28")
    svc = _svc(db, sec)
    await svc.get_concepts(CIK, [REPURCHASE])          # no period: served from Supabase
    assert sec.calls == []
    await svc.get_concepts(CIK, [REPURCHASE], newest_period_end="2026-06-27")
    assert sec.calls == [(CIK, REPURCHASE)], "the tier-1 copy of a 3 h old row must not hide the miss"


@pytest.mark.asyncio
@pytest.mark.parametrize("row_patch", [
    {"fetched_at": "not a time"},
    {"fetched_at": None},
    {"fetched_at": (NOW + timedelta(days=365)).isoformat()},   # corrupt, never fresh forever
    {"found": "yes"},
    {"facts": None},                                          # found=true needs facts
    {"facts": ["not", "a", "mapping"]},
])
async def test_an_unusable_row_is_refetched(row_patch):
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: _payload(REPURCHASE)})
    db.seed(REPURCHASE, age=timedelta(minutes=1))
    db.rows[(CIK, REPURCHASE)].update(row_patch)
    got = await _svc(db, sec).get_concepts(CIK, [REPURCHASE])
    assert sec.calls == [(CIK, REPURCHASE)]
    assert got.concepts[REPURCHASE]["units"]["USD"]


@pytest.mark.asyncio
async def test_a_naive_or_z_suffixed_timestamp_is_read_as_utc():
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: AssertionError("must not be fetched"),
                                   WITHHOLDING: AssertionError("must not be fetched")})
    db.seed(REPURCHASE)
    db.seed(WITHHOLDING)
    db.rows[(CIK, REPURCHASE)]["fetched_at"] = "2026-10-22T14:00:00"
    db.rows[(CIK, WITHHOLDING)]["fetched_at"] = "2026-10-22T14:00:00.12345Z"
    await _svc(db, sec).get_concepts(CIK, [REPURCHASE, WITHHOLDING])
    assert sec.calls == []


@pytest.mark.parametrize("raw, expected", [
    # Exact instants: a naive value read in any other zone (UTC+7 is still inside the 24 h
    # window, so the freshness check above cannot see it) moves the parsed time.
    ("2026-10-22T14:00:00", datetime(2026, 10, 22, 14, tzinfo=timezone.utc)),
    ("2026-10-22T14:00:00Z", datetime(2026, 10, 22, 14, tzinfo=timezone.utc)),
    ("2026-10-22T14:00:00.12345Z",
     datetime(2026, 10, 22, 14, 0, 0, 123450, tzinfo=timezone.utc)),
    ("2026-10-22T21:00:00+07:00", datetime(2026, 10, 22, 14, tzinfo=timezone.utc)),
    (datetime(2026, 10, 22, 14), datetime(2026, 10, 22, 14, tzinfo=timezone.utc)),
    ("not a time", None),
    ("", None),
    (None, None),
    (1729605600, None),
])
def test_parse_timestamp_reads_a_naive_value_as_utc_exactly(raw, expected):
    # `==` between an aware and a naive datetime is False, so this also pins awareness.
    assert scs._parse_timestamp(raw) == expected


@pytest.mark.asyncio
async def test_only_the_tags_without_a_fresh_entry_are_fetched():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.seed(REPURCHASE)
    db.seed(PREF_A, found=False)
    await _svc(db, sec).get_concepts(CIK, TAGS)
    assert sorted(t for _, t in sec.calls) == sorted([WITHHOLDING, PREF_B, PREF_C])


# ── Writes ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fetch_writes_a_trimmed_payload_that_the_rule_still_parses():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    got = await _svc(db, sec).get_concepts(CIK, TAGS)
    await _drain()
    (write,) = db.ops("upsert")
    assert write.on_conflict == "cik,concept"
    by_tag = {r["concept"]: r for r in write.rows}
    assert set(by_tag) == set(TAGS)

    row = by_tag[REPURCHASE]
    assert row["cik"] == CIK and row["found"] is True
    assert row["fetched_at"] == NOW.isoformat()
    assert row["newest_end"] == "2026-06-27"
    assert set(row["facts"]) == {"units"} and set(row["facts"]["units"]) == {"USD"}
    rows = row["facts"]["units"]["USD"]
    assert [r["form"] for r in rows] == ["10-Q", "10-Q/A", "10-K"]
    for r in rows:
        assert set(r) <= {"start", "end", "val", "filed", "form"}
    # The rule reads the trimmed payload exactly as it reads SEC's own.
    assert bv.facts_from_concept(row["facts"]) == bv.facts_from_concept(_payload(REPURCHASE))
    assert got.concepts[REPURCHASE] == row["facts"]

    for tag in (PREF_A, PREF_B, PREF_C):
        assert by_tag[tag]["found"] is False and by_tag[tag]["facts"] is None
        assert by_tag[tag]["newest_end"] is None

    # Off the event loop, like every Supabase call.
    assert threading.main_thread().ident not in db.threads


def test_the_trim_keeps_the_same_forms_the_rule_reads():
    assert scs._STATEMENT_FORMS == bv._STATEMENT_FORMS


@pytest.mark.parametrize("payload, expected_facts, expected_end", [
    ({"units": {"shares": [{"end": "2026-01-01", "val": 1, "form": "10-Q"}]}}, None, None),
    ({"units": "garbage"}, None, None),
    ({}, None, None),
    ({"units": {"USD": []}}, [], None),
    ({"units": {"USD": [{"start": "2026-01-01", "end": "2026-03-31", "val": float("nan"),
                         "form": "10-Q", "filed": "2026-04-30"}]}}, [], None),
])
def test_the_trim_on_odd_payloads(payload, expected_facts, expected_end):
    trimmed, newest = scs._trim_payload(payload)
    # No USD unit stays "no USD unit" (None to the rule), never an empty list.
    assert bv.facts_from_concept(trimmed) == expected_facts
    assert newest == expected_end


@pytest.mark.asyncio
async def test_a_failed_supabase_write_only_logs(caplog):
    caplog.set_level(logging.WARNING, logger="app.services.sec_concept_service")
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.fail_write = True
    got = await _svc(db, sec).get_concepts(CIK, TAGS)
    await _drain()
    assert got.stale is False and got.concepts[REPURCHASE]
    failed = [r.getMessage() for r in caplog.records
              if "[sec-concept-cache-write-failed]" in r.getMessage()]
    assert len(failed) == 1, failed
    assert CIK in failed[0] and REPURCHASE in failed[0] and "RuntimeError" in failed[0]


@pytest.mark.asyncio
async def test_a_failed_supabase_read_falls_through_to_sec(caplog):
    caplog.set_level(logging.WARNING, logger="app.services.sec_concept_service")
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.fail_read = True
    got = await _svc(db, sec).get_concepts(CIK, TAGS)
    assert len(sec.calls) == len(TAGS) and got.stale is False
    assert any("[sec-concept-cache-read-failed]" in r.getMessage() for r in caplog.records)


# ── SEC failures ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    SECRateLimitException("SEC EDGAR: HTTP 429", status_code=429, retry_after=None),
    SECUnavailableException("SEC EDGAR: HTTP 503", status_code=503),
    SECException("SEC EDGAR: HTTP 400", status_code=400),
])
async def test_a_failed_fetch_serves_a_row_up_to_30_days_old_as_stale(exc, caplog):
    caplog.set_level(logging.WARNING, logger="app.services.sec_concept_service")
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: exc, WITHHOLDING: _payload(WITHHOLDING)})
    db.seed(REPURCHASE, age=timedelta(days=30))
    svc = _svc(db, sec)
    got = await svc.get_concepts(CIK, [REPURCHASE, WITHHOLDING])
    assert got.stale is True
    assert got.concepts[REPURCHASE] == db.rows[(CIK, REPURCHASE)]["facts"]
    assert got.concepts[WITHHOLDING]["units"]["USD"]
    served = [r.getMessage() for r in caplog.records if "[sec-concept-stale-served]" in r.getMessage()]
    assert len(served) == 1 and CIK in served[0] and REPURCHASE in served[0]
    assert type(exc).__name__ in served[0]

    # Held briefly in memory, still flagged stale: the outage is not re-asked on every call.
    again = await svc.get_concepts(CIK, [REPURCHASE, WITHHOLDING])
    assert again.stale is True
    assert len(db.ops("select")) == 1 and len(sec.calls) == 2


@pytest.mark.asyncio
async def test_a_failed_fetch_with_a_row_older_than_30_days_raises_that_exception(caplog):
    caplog.set_level(logging.WARNING, logger="app.services.sec_concept_service")
    boom = SECUnavailableException("SEC EDGAR: HTTP 503", status_code=503)
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: boom})
    db.seed(REPURCHASE, age=timedelta(days=31))
    with pytest.raises(SECUnavailableException) as info:
        await _svc(db, sec).get_concepts(CIK, [REPURCHASE])
    assert info.value is boom
    failed = [r.getMessage() for r in caplog.records if "[sec-concept-fetch-failed]" in r.getMessage()]
    assert failed and CIK in failed[0] and REPURCHASE in failed[0]
    assert "SECUnavailableException: SEC EDGAR: HTTP 503" in failed[0]


@pytest.mark.asyncio
async def test_a_failed_fetch_with_no_row_raises_and_keeps_what_did_load():
    boom = SECRateLimitException("SEC EDGAR: circuit open", retry_after=500.0)
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: _payload(REPURCHASE), WITHHOLDING: boom,
                                   PREF_A: None, PREF_B: None, PREF_C: None})
    svc = _svc(db, sec)
    with pytest.raises(SECRateLimitException) as info:
        await svc.get_concepts(CIK, TAGS)
    assert info.value is boom
    await _drain()
    # The answers that did load were cached in both tiers ...
    assert {k[1] for k in db.rows} == {REPURCHASE, PREF_A, PREF_B, PREF_C}
    assert scs._inflight == {}
    # ... so a retry asks SEC for the failed tag only.
    sec.answers[WITHHOLDING] = _payload(WITHHOLDING)
    got = await svc.get_concepts(CIK, TAGS)
    assert got.stale is False
    assert [t for _, t in sec.calls].count(WITHHOLDING) == 2
    assert [t for _, t in sec.calls].count(REPURCHASE) == 1


@pytest.mark.asyncio
async def test_an_unexpected_client_error_is_logged_loudly_and_still_falls_back(caplog):
    caplog.set_level(logging.WARNING, logger="app.services.sec_concept_service")
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: KeyError("bug")})
    db.seed(REPURCHASE, age=timedelta(days=2))
    got = await _svc(db, sec).get_concepts(CIK, [REPURCHASE])
    assert got.stale is True
    unexpected = [r for r in caplog.records if "[sec-concept-unexpected]" in r.getMessage()]
    assert unexpected and unexpected[0].levelno == logging.ERROR and unexpected[0].exc_info


@pytest.mark.asyncio
async def test_a_client_returning_a_non_dict_is_a_failure_not_an_answer():
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: ["not", "a", "dict"]})
    with pytest.raises(TypeError):
        await _svc(db, sec).get_concepts(CIK, [REPURCHASE])
    await _drain()
    assert db.rows == {}


# ── The configuration gate and the inputs ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [
    "",
    "   ",
    # Unsendable values count as unset: the service shares the client's predicate
    # (`sec_edgar.user_agent_configured`), so it refuses before serving a cached row instead
    # of letting each fetch hit httpx's untyped UnicodeEncodeError / h11's header error.
    "Caydex \u2014 sec-contact-test@example.com",
    "Caydex\nsec-contact-test@example.com",
])
async def test_a_blank_user_agent_raises_before_any_read(monkeypatch, blank):
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.seed(REPURCHASE)
    svc = _svc(db, sec)
    await svc.get_concepts(CIK, [WITHHOLDING])  # warm tier 1 for one tag
    db.calls.clear()
    sec.calls.clear()

    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", blank)
    for tags in ([REPURCHASE], [WITHHOLDING], TAGS):
        with pytest.raises(SECConfigException):
            await svc.get_concepts(CIK, tags)
    assert db.calls == [] and sec.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cik", [None, "", "abc", "0", 0, True, "12345678901"])
async def test_an_invalid_cik_is_a_value_error(cik):
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    with pytest.raises(ValueError):
        await _svc(db, sec).get_concepts(cik, TAGS)
    assert db.calls == [] and sec.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [[], REPURCHASE, ["../x"], [REPURCHASE, None], ["a b"]])
async def test_invalid_tags_are_a_value_error(tags):
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    with pytest.raises(ValueError):
        await _svc(db, sec).get_concepts(CIK, tags)
    assert db.calls == [] and sec.calls == []


@pytest.mark.asyncio
async def test_the_cik_is_normalised_and_duplicate_tags_are_asked_once():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    got = await _svc(db, sec).get_concepts("50863", [REPURCHASE, REPURCHASE, PREF_A])
    assert got.cik == CIK
    assert list(got.concepts) == [REPURCHASE, PREF_A]
    assert sorted(sec.calls) == sorted([(CIK, REPURCHASE), (CIK, PREF_A)])


# ── In-flight dedup ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_read_and_one_fetch():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    sec.gate = asyncio.Event()
    svc = _svc(db, sec)
    leader = asyncio.create_task(svc.get_concepts(CIK, TAGS))
    for _ in range(200):
        if len(sec.calls) == len(TAGS):
            break
        await asyncio.sleep(0.005)
    assert len(sec.calls) == len(TAGS)
    joiner = asyncio.create_task(svc.get_concepts(CIK, list(reversed(TAGS))))
    await asyncio.sleep(0.02)
    assert len(scs._inflight) == 1
    sec.gate.set()
    a, b = await asyncio.gather(leader, joiner)
    assert a.concepts == b.concepts and a.stale == b.stale is False
    assert list(b.concepts) == list(reversed(TAGS)), "each caller gets its own tag order"
    assert len(sec.calls) == len(TAGS), "the joiner fetched nothing"
    assert len(db.ops("select")) == 1, "the joiner read nothing"
    assert scs._inflight == {}


@pytest.mark.asyncio
async def test_a_joiner_receives_the_leaders_exception():
    boom = SECUnavailableException("SEC EDGAR: HTTP 503", status_code=503)
    db, sec = _FakeDB(), _FakeSEC({REPURCHASE: boom})
    sec.gate = asyncio.Event()
    svc = _svc(db, sec)
    leader = asyncio.create_task(svc.get_concepts(CIK, [REPURCHASE]))
    while not sec.calls:
        await asyncio.sleep(0.005)
    joiner = asyncio.create_task(svc.get_concepts(CIK, [REPURCHASE]))
    await asyncio.sleep(0.02)
    sec.gate.set()
    for task in (leader, joiner):
        with pytest.raises(SECUnavailableException):
            await task
    assert len(sec.calls) == 1 and scs._inflight == {}


@pytest.mark.asyncio
async def test_a_cancelled_leader_releases_its_joiners():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    sec.gate = asyncio.Event()  # never set: the fetch hangs until cancelled
    svc = _svc(db, sec)
    leader = asyncio.create_task(svc.get_concepts(CIK, TAGS))
    while len(sec.calls) < len(TAGS):
        await asyncio.sleep(0.005)
    joiner = asyncio.create_task(svc.get_concepts(CIK, TAGS))
    await asyncio.sleep(0.02)

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(RuntimeError, match="cancelled"):
        await asyncio.wait_for(joiner, timeout=2.0)
    assert scs._inflight == {}

    # Nothing stuck: the next caller leads a fresh fetch.
    sec.gate.set()
    got = await svc.get_concepts(CIK, TAGS)
    assert got.stale is False


@pytest.mark.asyncio
async def test_a_cancelled_joiner_leaves_the_leader_and_the_other_joiners_alone():
    # Without `asyncio.shield`, the cancelled joiner cancels the SHARED future and every
    # other joiner gets a CancelledError it never asked for.
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    sec.gate = asyncio.Event()
    svc = _svc(db, sec)
    leader = asyncio.create_task(svc.get_concepts(CIK, TAGS))
    while len(sec.calls) < len(TAGS):
        await asyncio.sleep(0.005)
    quitter = asyncio.create_task(svc.get_concepts(CIK, TAGS))
    stayer = asyncio.create_task(svc.get_concepts(CIK, TAGS))
    await asyncio.sleep(0.02)
    quitter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await quitter
    sec.gate.set()
    got = await leader
    assert got.concepts[REPURCHASE]["units"]["USD"]
    assert (await asyncio.wait_for(stayer, timeout=2.0)).concepts == got.concepts
    assert len(sec.calls) == len(TAGS)


# ── End to end over the real client ─────────────────────────────────────


@pytest.mark.asyncio
async def test_end_to_end_through_the_real_client(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_BASE_URL", "https://data.sec.gov")
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_next_request_at", 0.0)
    seen: List[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        tag = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        if tag in (REPURCHASE, WITHHOLDING):
            return httpx.Response(200, json=_payload(tag))
        return httpx.Response(404, text="Not Found")

    db = _FakeDB()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        got = await SecConceptService(client=sec_edgar.SECEdgarClient(), supabase=db).get_concepts(
            "50863", TAGS, newest_period_end="2026-06-27")
        await _drain()
    assert len(seen) == len(TAGS) and all(r.headers["user-agent"] == UA for r in seen)
    assert all(f"/CIK{CIK}/us-gaap/" in r.url.path for r in seen)
    facts = bv.SecBuybackFacts.from_concepts(got.concepts)
    assert facts.repurchase and facts.withholding and facts.preferred == {}
    assert {k[1] for k in db.rows} == set(TAGS)


def test_get_sec_concept_service_is_a_singleton_that_touches_nothing_at_build(monkeypatch):
    monkeypatch.setattr(scs, "_service", None)
    monkeypatch.setattr(scs, "get_supabase", lambda: (_ for _ in ()).throw(AssertionError("no")))
    first = scs.get_sec_concept_service()
    assert first is scs.get_sec_concept_service()


# ── The cache-only read (a timed-out Signal of Confidence check falls back to it) ──


def _seed_all(db: _FakeDB, age: timedelta) -> None:
    db.seed(REPURCHASE, age=age)
    db.seed(WITHHOLDING, age=age)
    for tag in (PREF_A, PREF_B, PREF_C):
        db.seed(tag, found=False, age=age)


@pytest.mark.asyncio
async def test_the_cache_only_read_serves_rows_up_to_thirty_days_and_never_calls_sec():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    _seed_all(db, timedelta(days=29))
    got = await _svc(db, sec).get_cached_concepts(CIK, TAGS)
    assert sec.calls == [] and got is not None and got.stale is True
    assert got.concepts[REPURCHASE] == db.rows[(CIK, REPURCHASE)]["facts"]
    assert got.concepts[PREF_C] is None
    assert threading.main_thread().ident not in db.threads


@pytest.mark.asyncio
async def test_the_cache_only_read_needs_every_tag():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    _seed_all(db, timedelta(days=1))
    del db.rows[(CIK, PREF_C)]
    assert await _svc(db, sec).get_cached_concepts(CIK, TAGS) is None
    _seed_all(db, timedelta(days=31))
    assert await _svc(db, sec).get_cached_concepts(CIK, TAGS) is None, "past 30 days"
    assert sec.calls == []


@pytest.mark.asyncio
async def test_the_cache_only_read_uses_the_memory_tier_first():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    svc = _svc(db, sec)
    await svc.get_concepts(CIK, TAGS)
    await _drain()
    reads = len(db.ops("select"))
    got = await svc.get_cached_concepts(CIK, TAGS)
    assert got is not None and len(db.ops("select")) == reads, "no Supabase read"


@pytest.mark.asyncio
async def test_the_cache_only_read_refuses_an_unusable_user_agent(monkeypatch):
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    _seed_all(db, timedelta(days=1))
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex — ops@example.com")
    with pytest.raises(SECConfigException):
        await _svc(db, sec).get_cached_concepts(CIK, TAGS)
    assert db.calls == []


@pytest.mark.asyncio
async def test_the_cache_only_read_survives_a_failed_supabase_read():
    db, sec = _FakeDB(), _FakeSEC(_all_found())
    db.fail_read = True
    assert await _svc(db, sec).get_cached_concepts(CIK, TAGS) is None
