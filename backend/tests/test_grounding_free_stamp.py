"""The post-retirement provenance stamp on cached collections and cross-user reports.

Gemini "Grounding with Google Search" was retired on 2026-10-02 (test_no_google_search_grounding.py).
Migration 188, which purges what it stored, was applied BEFORE the deploy, so production kept
running the grounded code (git HEAD) — refilling the grounded caches and writing
`ticker_data_cache` / `ticker_report_cache` / `research_reports` rows built from grounded
research AFTER `CACHE_SCHEMA_FLOOR`. Railway also overlaps the old and new deployments, so there
is no clean deploy instant and any clock-based rule leaks.

The fix is a POSITIVE stamp written only by the new code and required by every reader that
serves a cached collection or another user's report:

  * `CollectedTickerData.grounding_free` (default False — the UNSAFE value, so a HEAD row, which
    lacks the key, never reads as stamped), set True only by `_collect_fresh`; checked on the
    RAW payload by `ticker_data_cache.get_cached_collection` before deserializing.
  * `report_degradation.GROUNDING_FREE_KEY` on every report `assemble_report` returns, copied
    from the collection; required by `ticker_report_cache.get_cached_report`,
    `research_service._lookup_shared_cache` and the direct door's `_check_legacy_report_cache`;
    `upsert_cached_report` refuses a report without it.

The other half of the contract matters as much: a NEW report missing the stamp would make every
read a miss, and the direct door pre-charges 20 credits on a miss — a paid regenerate loop. The
chain tests below drive the real collect → store → read → assemble → upsert → read path to show
the stamp survives every writer.

Hermetic: every Supabase call goes to an in-memory fake (`_FakeDB`) or a MagicMock chain.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.ticker_data_cache as tdc
import app.services.ticker_report_cache as trc
from app.services.agents.narrative_prompts import stage_a_fallback
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    TickerReportDataCollector,
)
from app.services.report_degradation import GROUNDING_FREE_KEY, report_is_grounding_free

# The schema-parity fixture builder, and its autouse fixture that stubs every live benchmark /
# moat lookup `_build_sections` and `assemble_report` reach (importing an autouse fixture
# activates it in this module too).
from test_ticker_report_schema_parity import (  # noqa: F401
    _make_collected_data,
    _no_live_benchmark_lookup,
)

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "database" / "migrations" / "189_purge_pre_retirement_caches.sql"
MIGRATION_188 = BACKEND / "database" / "migrations" / "188_purge_grounded_search_content.sql"
SNAPSHOT = BACKEND / "database" / "schema_snapshot.sql"

_FIELD_NAMES = {f.name for f in dataclasses.fields(CollectedTickerData)}


# ── In-memory PostgREST fake ─────────────────────────────────────────────────


class _FakeDB:
    """Multi-table fake. `upsert` stores a JSON round-trip of the row (like JSONB, so a
    leading-underscore key survives exactly as it would in Postgres); `select().eq()…` returns
    the selected columns of the first row matching every `eq` filter."""

    def __init__(self):
        self.tables: dict = {}
        self.upserts: list = []

    def table(self, name):
        return _FakeQuery(self, name)

    def put(self, name, row):
        self.tables.setdefault(name, []).append(json.loads(json.dumps(row)))


class _FakeQuery:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._upsert = None
        self._cols: list = []
        self._filters: dict = {}

    def upsert(self, row, on_conflict="", **_kw):
        keys = tuple(c.strip() for c in on_conflict.split(",") if c.strip())
        self._upsert = (json.loads(json.dumps(row)), keys)
        return self

    def select(self, cols):
        self._cols = [c.strip() for c in cols.split(",")]
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self._upsert is not None:
            row, keys = self._upsert
            rows[:] = [r for r in rows if any(r.get(k) != row.get(k) for k in keys)]
            rows.append(row)
            self.db.upserts.append(self.name)
            return SimpleNamespace(data=[])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        return SimpleNamespace(data=[{c: r.get(c) for c in self._cols} for r in hit[:1]])


@pytest.fixture
def fake_db(monkeypatch):
    """Both cache modules on one fake; the close-cycle clock pinned fresh (the stamp is what
    these tests are about, not the clock — tests/test_cache_freshness.py owns that)."""
    db = _FakeDB()
    monkeypatch.setattr(tdc, "get_supabase", lambda: db)
    monkeypatch.setattr(trc, "get_supabase", lambda: db)
    monkeypatch.setattr(tdc, "is_cache_fresh", lambda *_a, **_k: True)
    monkeypatch.setattr(trc, "is_cache_fresh", lambda *_a, **_k: True)
    return db


_NOW = "2026-10-03T03:00:00+00:00"


def _chain(rows):
    """A MagicMock PostgREST chain whose `.execute()` returns `rows`."""
    q = MagicMock()
    for m in ("table", "select", "eq", "gte", "not_", "is_", "order", "limit"):
        getattr(q, m).return_value = q
    q.not_ = q
    q.execute.return_value = MagicMock(data=rows)
    return q


# ── Builders ─────────────────────────────────────────────────────────────────


def _stamped_collection(ticker: str = "AAPL") -> CollectedTickerData:
    out = _make_collected_data(ticker=ticker)
    out.grounding_free = True
    return out


def _head_row(variant: str = "missing") -> dict:
    """The `collected_data` the PRE-retirement code (git HEAD) wrote: everything a current row
    has, minus the stamp, plus the grounded fields HEAD's CollectedTickerData carried and the
    grounded catalyst it baked into `price_action_partial`."""
    raw = tdc._serialize(_stamped_collection())
    assert raw is not None and raw.get("grounding_free") is True
    raw.pop("grounding_free")
    raw.update({
        "peer_source": "intel",
        "peer_details": {"MSFT": "competes in personal computing and cloud services"},
        "moat_grounded_pillars": [
            {"name": "Network Effects", "score": 8.0, "source": "grounded",
             "evidence": "research says the ecosystem locks users in"},
        ],
        "price_catalyst_grounded": {
            "reason": "Shares rose after a report of a new supplier agreement.",
            "sources": [{"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"}],
        },
        "geopolitical_factors": [
            {"category": "Trade", "title": "Tariff escalation", "severity": "high"},
        ],
    })
    pa = dict(raw.get("price_action_partial") or {})
    pa["_grounded_reason"] = "Shares rose after a report of a new supplier agreement."
    raw["price_action_partial"] = pa
    if variant == "false":
        raw["grounding_free"] = False
    elif variant == "string_true":
        raw["grounding_free"] = "true"
    elif variant == "one":
        raw["grounding_free"] = 1
    elif variant == "null":
        raw["grounding_free"] = None
    return raw


def _stamped_report(ticker: str = "AAPL") -> dict:
    report = TickerReportDataCollector().assemble_report(
        _stamped_collection(ticker), stage_a_fallback(),
    )
    assert report[GROUNDING_FREE_KEY] is True
    return report


# ── The pure helpers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, ok", [
    ({"grounding_free": True}, True),
    ({}, False),
    ({"grounding_free": False}, False),
    ({"grounding_free": "true"}, False),
    ({"grounding_free": 1}, False),
    ({"grounding_free": None}, False),
    (None, False),
    ([{"grounding_free": True}], False),
    ("grounding_free", False),
])
def test_collection_stamp_is_exactly_json_true(raw, ok):
    assert tdc.collection_is_grounding_free(raw) is ok


@pytest.mark.parametrize("report, ok", [
    ({GROUNDING_FREE_KEY: True}, True),
    ({}, False),
    ({GROUNDING_FREE_KEY: False}, False),
    ({GROUNDING_FREE_KEY: "true"}, False),
    ({GROUNDING_FREE_KEY: 1}, False),
    ({"grounding_free": True}, False),        # the COLLECTION key is not the report key
    (None, False),
    ("x", False),
])
def test_report_stamp_is_exactly_json_true(report, ok):
    assert report_is_grounding_free(report) is ok


def test_the_field_defaults_to_the_unsafe_value():
    """A HEAD row has no `grounding_free` key, so `_deserialize` leaves the DEFAULT. Were the
    default True, every pre-retirement collection would deserialize as stamped."""
    (f,) = [f for f in dataclasses.fields(CollectedTickerData) if f.name == "grounding_free"]
    assert f.default is False
    assert CollectedTickerData(ticker="X", persona_key="warren_buffett").grounding_free is False
    assert tdc.GROUNDING_FREE_FIELD == "grounding_free" == f.name


def test_report_key_is_internal_to_the_contract():
    from app.schemas.ticker_report import TickerReportResponse

    assert GROUNDING_FREE_KEY.startswith("_")
    assert GROUNDING_FREE_KEY not in TickerReportResponse.model_fields


# ── Collection reader: get_cached_collection ─────────────────────────────────


@pytest.mark.parametrize("variant", ["missing", "false", "string_true", "one", "null"])
def test_a_head_written_collection_is_a_miss(fake_db, caplog, variant):
    raw = _head_row(variant)
    # Anti-vacuity: WITHOUT the stamp gate this row would be served — it deserializes (the
    # removed grounded fields just drop) and was built under the current DCF setting.
    rebuilt = tdc._deserialize(raw, _FIELD_NAMES)
    assert rebuilt is not None and tdc._built_under_current_dcf(rebuilt)
    assert rebuilt.price_action_partial.get("_grounded_reason"), "the grounded catalyst rides along"

    fake_db.put(tdc.TABLE_NAME, {"ticker": "AAPL", "collected_data": raw, "cached_at": _NOW})
    with caplog.at_level(logging.WARNING, logger=tdc.__name__):
        assert asyncio.run(tdc.get_cached_collection("aapl")) is None
    assert "AAPL" in caplog.text and "pre-retirement" in caplog.text


def test_a_stamped_collection_is_served(fake_db):
    raw = tdc._serialize(_stamped_collection())
    fake_db.put(tdc.TABLE_NAME, {"ticker": "AAPL", "collected_data": raw, "cached_at": _NOW})
    hit = asyncio.run(tdc.get_cached_collection("AAPL"))
    assert hit is not None and hit.grounding_free is True


def test_an_unstamped_current_collection_is_a_miss(fake_db):
    """The default-False field on a CURRENT-shape collection (no HEAD fields at all): still a
    miss — the reader keys on the stamp, not on the presence of grounded fields."""
    out = _make_collected_data()
    assert out.grounding_free is False
    raw = tdc._serialize(out)
    assert raw is not None and raw["grounding_free"] is False
    fake_db.put(tdc.TABLE_NAME, {"ticker": "AAPL", "collected_data": raw, "cached_at": _NOW})
    assert asyncio.run(tdc.get_cached_collection("AAPL")) is None


# ── Collection writer: store_collection ──────────────────────────────────────


def test_store_refuses_an_unstamped_collection(fake_db, caplog):
    """`_serialize_readable` reads the payload back exactly as the reader will — and the
    reader's first check is the stamp, so an unstamped row would be fresh-but-unreadable."""
    with caplog.at_level(logging.ERROR, logger=tdc.__name__):
        asyncio.run(tdc.store_collection("AAPL", _make_collected_data()))
    assert fake_db.upserts == []
    assert "grounding_free stamp" in caplog.text


def test_a_stored_stamped_collection_reads_back(fake_db):
    asyncio.run(tdc.store_collection("aapl", _stamped_collection()))
    assert fake_db.upserts == [tdc.TABLE_NAME]
    stored = fake_db.tables[tdc.TABLE_NAME][0]["collected_data"]
    assert stored["grounding_free"] is True       # survived the JSONB round-trip as a bool
    hit = asyncio.run(tdc.get_cached_collection("AAPL"))
    assert hit is not None and hit.grounding_free is True


# ── The producer: _collect_fresh, and collect()'s per-persona copy ──────────


def _driven_collector(monkeypatch, fetches: list) -> TickerReportDataCollector:
    """A real collector whose FMP fan-out is replaced by a copy of the parity fixture's data.
    `_collect_fresh` itself — where the stamp is set — runs for real."""
    prebuilt = _make_collected_data()
    coll = TickerReportDataCollector.__new__(TickerReportDataCollector)

    async def _fetch_all(out):
        fetches.append(out.ticker)
        for f in dataclasses.fields(prebuilt):
            if f.name not in ("ticker", "persona_key", "grounding_free"):
                setattr(out, f.name, copy.deepcopy(getattr(prebuilt, f.name)))

    async def _noop(out):
        return None

    monkeypatch.setattr(coll, "_fetch_all", _fetch_all)
    monkeypatch.setattr(coll, "_compute_metrics", lambda out: None)
    monkeypatch.setattr(coll, "_build_sections", lambda out: None)
    monkeypatch.setattr(coll, "_apply_intraday_chart", _noop)
    return coll


@pytest.mark.asyncio
async def test_collect_fresh_stamps_the_collection(monkeypatch):
    fetches: list = []
    coll = _driven_collector(monkeypatch, fetches)
    out = await coll._collect_fresh("AAPL")
    assert fetches == ["AAPL"], "the fixture never ran; the test proves nothing"
    assert out.grounding_free is True


@pytest.mark.asyncio
async def test_collect_keeps_the_stamp_on_its_per_persona_copy(monkeypatch):
    base = _stamped_collection()

    async def _cached(ticker, fetch_fresh):
        return base

    # `collect()` imports get_or_collect from the SOURCE module at call time.
    monkeypatch.setattr(tdc, "get_or_collect", _cached)
    coll = TickerReportDataCollector.__new__(TickerReportDataCollector)
    got = await coll.collect("aapl", "cathie_wood")
    assert got is not base and got.persona_key == "cathie_wood"
    assert got.grounding_free is True


# ── assemble_report ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("stamp, expected", [
    (True, True), (False, False), (1, False), ("true", False), (None, False),
])
def test_assemble_report_copies_the_stamp_exactly(stamp, expected):
    out = _make_collected_data()
    out.grounding_free = stamp
    report = TickerReportDataCollector().assemble_report(out, stage_a_fallback())
    assert report[GROUNDING_FREE_KEY] is expected


def test_assemble_report_stamps_a_partial_report_too():
    """The report's only return also carries `_degraded_sections` when Financials were lost —
    the stamp must ride on that shape as well (it is still delivered to its buyer)."""
    out = _stamped_collection()
    out.degraded_sections = ["growth_chart:quarterly_income"]
    report = TickerReportDataCollector().assemble_report(out, stage_a_fallback())
    assert report[GROUNDING_FREE_KEY] is True
    assert report["_degraded_sections"] == ["growth_chart:quarterly_income"]


def test_a_model_dump_would_drop_the_stamp():
    """Why every writer must store the RAW dict: the response model ignores the key, so a
    writer that stored `model_dump()` would turn every new report into a miss (the paid
    regenerate loop). Both writers store the raw dict — pinned by the chain tests below."""
    from app.schemas.ticker_report import TickerReportResponse

    dumped = TickerReportResponse.model_validate(_stamped_report()).model_dump()
    assert GROUNDING_FREE_KEY not in dumped


# ── Report readers ───────────────────────────────────────────────────────────


def _minimal_report(stamp) -> dict:
    report = {"symbol": "AAPL", "quality_score": 70.0}
    if stamp is not _ABSENT:
        report[GROUNDING_FREE_KEY] = stamp
    return report


_ABSENT = object()


@pytest.mark.parametrize("stamp, served", [(True, True), (_ABSENT, False), (False, False)])
def test_get_cached_report_requires_the_stamp(fake_db, monkeypatch, caplog, stamp, served):
    monkeypatch.setattr(trc, "report_dcf_source_matches", lambda _d: True)
    fake_db.put(trc.TABLE_NAME, {
        "ticker": "AAPL", "persona": "warren_buffett",
        "ticker_report_data": _minimal_report(stamp), "cached_at": _NOW,
    })
    with caplog.at_level(logging.WARNING, logger=trc.__name__):
        got = asyncio.run(trc.get_cached_report("AAPL", "warren_buffett"))
    assert (got is not None) is served
    if not served:
        assert "AAPL/warren_buffett" in caplog.text and "pre-retirement" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp, served", [(True, True), (_ABSENT, False), (False, False)])
async def test_shared_cache_lookup_requires_the_stamp(monkeypatch, caplog, stamp, served):
    import app.services.research_service as rs
    from app.services.research_service import ResearchService

    svc = object.__new__(ResearchService)
    svc.supabase = _chain([{"ticker_report_data": _minimal_report(stamp), "completed_at": _NOW}])
    monkeypatch.setattr(rs, "report_dcf_source_matches", lambda _b: True)
    with caplog.at_level(logging.WARNING, logger=rs.__name__):
        got = await svc._lookup_shared_cache("AAPL", "warren_buffett")
    assert (got is not None) is served
    if not served:
        assert "AAPL/warren_buffett" in caplog.text and "pre-retirement" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp, served", [(True, True), (_ABSENT, False), (False, False)])
async def test_direct_door_legacy_path_requires_the_stamp(monkeypatch, caplog, stamp, served):
    from app.api.v1.endpoints import ticker_report as ep

    q = _chain([{"ticker_report_data": _minimal_report(stamp), "completed_at": _NOW}])
    monkeypatch.setattr(ep, "get_supabase", lambda: q)
    monkeypatch.setattr(ep, "report_dcf_source_matches", lambda _r: True)
    with caplog.at_level(logging.WARNING, logger=ep.__name__):
        got = await ep._check_legacy_report_cache("AAPL", "warren_buffett")
    assert (got is not None) is served
    if not served:
        assert "AAPL/warren_buffett" in caplog.text


# ── Report writer gate: upsert_cached_report ─────────────────────────────────


@pytest.mark.parametrize("report", [
    {"symbol": "AAPL"},
    {"symbol": "AAPL", GROUNDING_FREE_KEY: False},
    {"symbol": "AAPL", GROUNDING_FREE_KEY: "true"},
    None,
])
def test_upsert_refuses_an_unstamped_report(fake_db, caplog, report):
    with caplog.at_level(logging.ERROR, logger=trc.__name__):
        asyncio.run(trc.upsert_cached_report("AAPL", "warren_buffett", report))
    assert fake_db.upserts == []
    assert "REFUSED" in caplog.text and "AAPL/warren_buffett" in caplog.text


def test_upsert_writes_a_stamped_report(fake_db):
    asyncio.run(trc.upsert_cached_report("aapl", "Warren_Buffett", {"symbol": "AAPL",
                                                                     GROUNDING_FREE_KEY: True}))
    assert fake_db.upserts == [trc.TABLE_NAME]
    assert fake_db.tables[trc.TABLE_NAME][0]["ticker_report_data"][GROUNDING_FREE_KEY] is True


# ── The stamp survives every writer: no paid regenerate loop ─────────────────


@pytest.mark.asyncio
async def test_new_code_end_to_end_stays_a_cache_hit(fake_db, monkeypatch):
    """collect (miss → `_collect_fresh` → store) → collect again (HIT, no second fan-out) →
    assemble → upsert → read. Every stage is the production code except the FMP fan-out."""
    fetches: list = []
    coll = _driven_collector(monkeypatch, fetches)

    first = await coll.collect("AAPL", "warren_buffett")
    second = await coll.collect("AAPL", "cathie_wood")
    assert fetches == ["AAPL"], "the second persona re-collected: the stored row read as a miss"
    assert first.grounding_free is True and second.grounding_free is True

    report = TickerReportDataCollector.assemble_report(coll, second, stage_a_fallback())
    assert report[GROUNDING_FREE_KEY] is True
    await trc.upsert_cached_report("AAPL", "cathie_wood", report)
    hit = await trc.get_cached_report("AAPL", "cathie_wood")
    assert hit is not None and hit[GROUNDING_FREE_KEY] is True


@pytest.mark.asyncio
async def test_direct_door_writes_a_servable_row(fake_db, monkeypatch):
    """`TickerReportService._generate_uncontended` (the direct door's billable path) caches the
    RAW assembled dict, so the next request is a free hit — not a second 20-credit charge."""
    from app.services import ticker_report_service as trs

    svc = object.__new__(trs.TickerReportService)
    svc.collector = TickerReportDataCollector()
    svc.gemini = MagicMock()

    async def _collect(ticker, persona_key):
        return _stamped_collection(ticker)

    async def _stage_a(out, persona, evidence):
        return stage_a_fallback()           # untagged: a real (non-degraded) shell

    monkeypatch.setattr(svc.collector, "collect", _collect)
    svc._generate_stage_a = _stage_a
    monkeypatch.setattr(trs, "build_financial_context", lambda out: "evidence")
    monkeypatch.setattr(trs, "build_narrative_jobs", lambda *a, **k: [])
    monkeypatch.setattr(trs, "run_narrative_jobs", AsyncMock())
    monkeypatch.setattr(trs, "synthesize_core_thesis", AsyncMock())
    monkeypatch.setattr(trs, "synthesize_critical_factors", AsyncMock())

    report = await svc._generate_uncontended("AAPL", "warren_buffett")
    assert report[GROUNDING_FREE_KEY] is True
    assert fake_db.upserts == [trc.TABLE_NAME], "the direct door did not cache its report"
    hit = await trc.get_cached_report("AAPL", "warren_buffett")
    assert hit is not None and hit[GROUNDING_FREE_KEY] is True


def _deep_service(monkeypatch, shared):
    import app.services.research_service as rs
    from app.services.research_service import ResearchService

    q = MagicMock()
    for m in ("table", "update", "eq", "in_"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=[{"id": "rid"}])
    svc = object.__new__(ResearchService)
    svc.supabase = q
    svc.fmp = MagicMock()
    svc.gemini = MagicMock()
    monkeypatch.setattr(svc, "_update_status", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_lookup_shared_cache", AsyncMock(return_value=shared))
    monkeypatch.setattr(rs, "compute_quality_score", lambda persona, data: 70)
    monkeypatch.setattr(
        "app.services.push_dispatch_service.get_push_dispatch_service",
        lambda: MagicMock(notify_users=AsyncMock(return_value=1)),
    )
    return svc, q


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["fresh_agent_run", "shared_cache_copy"])
async def test_deep_door_stores_and_seeds_the_stamp(fake_db, monkeypatch, path):
    """Both deep-door outcomes write the raw dict to `research_reports.ticker_report_data` and
    seed `ticker_report_cache` with a row the direct door can serve."""
    import app.services.research_service as rs

    report = _stamped_report()
    if path == "shared_cache_copy":
        # What `_lookup_shared_cache` returns: another user's stored row, after JSONB.
        svc, q = _deep_service(monkeypatch, json.loads(json.dumps(report)))
    else:
        svc, q = _deep_service(monkeypatch, None)

        async def _deduped(ticker, persona_key, run_callable, **_kw):
            return report

        monkeypatch.setattr(rs, "_run_agent_deduped", _deduped)
        monkeypatch.setattr(rs, "ResearchAgent", lambda **_kw: SimpleNamespace(research_findings="x"))

    assert await svc.generate_report("rid", "AAPL", "warren_buffett", "u1") is True
    written = q.update.call_args_list[-1].args[0]
    assert written["status"] == "completed"
    assert written["ticker_report_data"][GROUNDING_FREE_KEY] is True
    assert fake_db.upserts == [trc.TABLE_NAME], "the deep door did not seed the shared cache"
    hit = await trc.get_cached_report("AAPL", "warren_buffett")
    assert hit is not None and hit[GROUNDING_FREE_KEY] is True


# ── No reader bypasses the gates ─────────────────────────────────────────────


def _string_constants(path: Path) -> list[str]:
    """Every string CONSTANT in the module's AST — comments never count, and an f-string's
    literal parts are Constant nodes too, so `.table(f"ticker_report_cache")` is caught (a
    token scan stripping only r/b/u prefixes missed it)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_only_the_cache_modules_name_their_tables():
    """`ticker_report_cache` is read only through `get_cached_report` and `ticker_data_cache`
    only through `get_cached_collection` (which hold the stamp gates); a module that queried
    either table by name would bypass them."""
    owners = {"ticker_report_cache": "ticker_report_cache.py",
              "ticker_data_cache": "ticker_data_cache.py"}
    found: dict = {name: [] for name in owners}
    for path in sorted((BACKEND / "app").rglob("*.py")):
        for s in _string_constants(path):
            if s in owners:
                found[s].append(path.name)
    for name, home in owners.items():
        assert found[name] == [home], f"{name} named outside {home}: {found[name]}"


# ── Migration 189 ────────────────────────────────────────────────────────────


def _executable_sql(path: Path) -> str:
    text = "\n".join(line.split("--", 1)[0] for line in path.read_text().splitlines())
    return re.sub(r"\s+", " ", text)


def test_migration_keys_equal_the_python_constants():
    sql = _executable_sql(MIGRATION)
    # Anti-vacuity for the comment stripper: an SQL literal containing an em dash survives.
    assert "Research figure withdrawn — pending recompute" in sql

    coll = re.search(
        r"DELETE FROM public\.ticker_data_cache WHERE \(collected_data -> '(\w+)'\) "
        r"IS DISTINCT FROM 'true'::jsonb;", sql)
    rep = re.search(
        r"DELETE FROM public\.ticker_report_cache WHERE \(ticker_report_data -> '(\w+)'\) "
        r"IS DISTINCT FROM 'true'::jsonb;", sql)
    assert coll and rep, "the two stamp DELETEs are missing or reshaped"
    assert coll.group(1) == tdc.GROUNDING_FREE_FIELD and coll.group(1) in _FIELD_NAMES
    assert rep.group(1) == GROUNDING_FREE_KEY

    snapshot = SNAPSHOT.read_text()
    assert re.search(r"CREATE TABLE public\.ticker_data_cache \([^;]*collected_data jsonb", snapshot)
    assert re.search(r"CREATE TABLE public\.ticker_report_cache \([^;]*ticker_report_data jsonb",
                     snapshot)


def test_migration_reruns_every_188_purge_and_spares_the_owner_tables():
    sql = _executable_sql(MIGRATION)
    old = _executable_sql(MIGRATION_188)
    stmts = [s.strip() + ";" for s in old.split(";")
             if re.match(r"\s*(DELETE|UPDATE)\b", s)]
    assert len(stmts) >= 11, "188's statements were not found; the check proves nothing"
    for stmt in stmts:
        if "cached_at <" in stmt:
            continue        # 188's clock-based report-cache DELETEs: superseded by the stamp rule
        assert stmt in sql, f"189 does not re-run 188's: {stmt[:90]}"

    assert sql.strip().startswith("BEGIN;") and sql.strip().endswith("COMMIT;")
    assert "DELETE FROM public.market_deep_dive_cache;" in sql
    assert "DELETE FROM public.chat_starter_answers;" in sql
    assert "research_reports" not in sql and "notification_events" not in sql
    snapshot = SNAPSHOT.read_text()
    for table in ("market_deep_dive_cache", "chat_starter_answers"):
        assert f"CREATE TABLE public.{table} (" in snapshot


# ── research_reports: cross-user reads go only through the two gated readers ──
#
# `_lookup_shared_cache` and `_check_legacy_report_cache` serve ANOTHER user's stored report and
# hold the stamp gate. Every other read of `ticker_report_data` from research_reports must be
# scoped to the caller's row (`user_id`) or one report (`id`). A new unscoped reader — a
# "popular reports" route, a share link — would serve pre-retirement grounded reports to users
# who never asked, with every other test green.

_GATED_CROSS_USER_READERS = {
    ("app/services/research_service.py", "_lookup_shared_cache"),
    ("app/api/v1/endpoints/ticker_report.py", "_check_legacy_report_cache"),
}


def _research_report_reads(tree: ast.AST):
    parents = {ch: node for node in ast.walk(tree) for ch in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "table" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "research_reports"):
            continue
        top = node                                   # climb to the end of the method chain
        while True:
            attr = parents.get(top)
            call = parents.get(attr)
            if isinstance(attr, ast.Attribute) and isinstance(call, ast.Call) and call.func is attr:
                top = call
            else:
                break
        steps, cur = [], top
        while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            steps.append((cur.func.attr, [a.value for a in cur.args
                                          if isinstance(a, ast.Constant) and isinstance(a.value, str)]))
            cur = cur.func.value
        enclosing, cur = [], node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing.append(cur.name)
        yield node.lineno, steps, enclosing


def _is_report_payload_read(steps) -> bool:
    selected = " ".join(" ".join(args) for m, args in steps if m == "select")
    return "ticker_report_data" in selected or "*" in selected


@pytest.mark.parametrize("src, unscoped", [
    ('def f(sb, t):\n    return sb.table("research_reports").select("ticker_report_data").eq("ticker", t).execute()', True),
    ('def f(sb, t):\n    return sb.table("research_reports").select("*").eq("ticker", t).execute()', True),
    ('def f(sb, u):\n    return sb.table("research_reports").select("*").eq("user_id", u).execute()', False),
    ('def f(sb, i):\n    return sb.table("research_reports").select("ticker_report_data").eq("id", i).execute()', False),
    ('def f(sb, t):\n    return sb.table("research_reports").select("id, status").eq("ticker", t).execute()', False),
])
def test_the_research_reports_guard_is_not_vacuous(src, unscoped):
    reads = [(steps, enc) for _l, steps, enc in _research_report_reads(ast.parse(src))]
    assert len(reads) == 1
    steps, _enc = reads[0]
    scoped = any(m == "eq" and args and args[0] in ("user_id", "id") for m, args in steps)
    assert (_is_report_payload_read(steps) and not scoped) is unscoped


def test_no_unscoped_cross_user_read_of_stored_reports():
    offenders, gated_seen = [], set()
    for path in sorted((BACKEND / "app").rglob("*.py")):
        rel = str(path.relative_to(BACKEND))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, steps, enclosing in _research_report_reads(tree):
            if not _is_report_payload_read(steps):
                continue
            if any(m == "eq" and args and args[0] in ("user_id", "id") for m, args in steps):
                continue
            gated = {(rel, fn) for fn in enclosing} & _GATED_CROSS_USER_READERS
            if gated:
                gated_seen |= gated
                continue
            offenders.append(f"{rel}:{line} (in {enclosing[:2]})")
    assert not offenders, (
        "a read of research_reports.ticker_report_data that is neither scoped to the caller "
        f"(user_id / id) nor one of the two stamp-gated cross-user readers: {offenders}"
    )
    # Anti-vacuity: both gated readers were actually found by the scan.
    assert gated_seen == _GATED_CROSS_USER_READERS, gated_seen


# ── Updates cards: prompt_version is the provenance stamp ────────────────────


def _card_row(**over):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    row = {
        "scope": "AAPL", "headline": "Something happened", "bullets": ["one", "two"],
        "sentiment": "Bullish", "article_count": 12, "generated_at": now.isoformat(),
        "soft_expires_at": (now + timedelta(minutes=15)).isoformat(),
        "trigger_reason": "new_articles", "prompt_version": 7,
    }
    row.update(over)
    return row


@pytest.mark.parametrize("version, served", [
    (7, True), (8, True), (6, False), (1, False), (0, False), (-7, False),
    (None, False), ("7", False), (True, False), (7.0, False), ("missing", False),
])
def test_a_card_below_the_servable_prompt_version_is_never_served(monkeypatch, version, served):
    import app.services.news_insight_service as nis

    monkeypatch.setattr(nis, "_unservable_logged", set())
    row = _card_row(prompt_version=version)
    if version == "missing":
        row.pop("prompt_version")
    card = object.__new__(nis.NewsInsightService)._row_to_card(row, market_active=False)
    assert (card is not None) is served


def test_an_unservable_card_is_logged_once_per_scope(monkeypatch, caplog):
    import app.services.news_insight_service as nis

    monkeypatch.setattr(nis, "_unservable_logged", set())
    svc = object.__new__(nis.NewsInsightService)
    with caplog.at_level(logging.WARNING, logger=nis.__name__):
        for _ in range(3):
            assert svc._row_to_card(_card_row(prompt_version=6), market_active=False) is None
        assert svc._row_to_card(_card_row(scope="MSFT", prompt_version=6), market_active=False) is None
    hits = [r for r in caplog.records if "pre-retirement" in r.getMessage()]
    assert len(hits) == 2, [r.getMessage() for r in hits]


def test_the_cards_the_current_code_writes_are_servable():
    """The floor must never pass the version the writer stamps — that would blank every card."""
    from app.services.news_insight_service import _MIN_SERVABLE_PROMPT_VERSION
    from app.services.updates_materiality import PROMPT_VERSION

    assert PROMPT_VERSION >= _MIN_SERVABLE_PROMPT_VERSION == 7


def test_migration_deletes_exactly_the_unservable_cards():
    from app.services.news_insight_service import _MIN_SERVABLE_PROMPT_VERSION

    m = re.search(r"DELETE FROM public\.ai_insight_cache\s+WHERE prompt_version < (\d+);",
                  _executable_sql(MIGRATION))
    assert m and int(m.group(1)) == _MIN_SERVABLE_PROMPT_VERSION


# ── The chat answer caches: versioned keys make every older row unreachable ──


def test_the_starter_answer_key_is_versioned():
    import hashlib
    import unicodedata

    import app.services.chat_starter_warm_service as warm

    q = "Why is NAVN down 22% today?"
    norm = " ".join(unicodedata.normalize("NFKC", q).casefold().split())
    pre_retirement = hashlib.sha256(norm.encode("utf-8")).hexdigest()
    assert warm.question_hash(q) != pre_retirement
    assert warm.question_hash(q) == hashlib.sha256(("v2\x00" + norm).encode("utf-8")).hexdigest()
    assert warm.question_hash("  why is navn DOWN 22% today? ") == warm.question_hash(q)


@pytest.mark.parametrize("asset_type", ["", "STOCK", "crypto"])
def test_the_deep_dive_key_is_versioned(asset_type):
    import hashlib

    import app.services.chat_service as cs

    msg = "Give me the AI Analyst deep dive"
    norm = " ".join(cs.normalize_text(msg).lower().split())
    kind = asset_type.strip().upper()
    pre_retirement = hashlib.md5(f"deep-dive\x00{kind}\x00{norm}".encode()).hexdigest()[:16]
    key = cs.ChatService._deep_dive_cache_key("ctx", msg, asset_type)
    assert key != pre_retirement
    assert key == hashlib.md5(f"deep-dive-v2\x00{kind}\x00{norm}".encode()).hexdigest()[:16]


# ── The deep door end to end: the REAL ResearchAgent.run and _run_agent_deduped ──


@pytest.mark.asyncio
async def test_the_real_deep_door_run_stores_and_seeds_the_stamp(fake_db, monkeypatch):
    """`test_deep_door_stores_and_seeds_the_stamp` stubs `_run_agent_deduped` with a report that
    is already stamped, so it cannot see a stamp lost INSIDE `ResearchAgent.run` (a model_dump,
    a rebuilt dict). This drives the real run — assemble, Stage B, the syntheses — through the
    real dedup, with a FOLLOWER joining the leader (its deepcopy must keep the stamp too)."""
    import app.services.research_service as rs
    from app.services.agents import research_agent as ra

    gate = asyncio.Event()
    runs = []

    async def _collect(self, ticker, persona_key):
        return _stamped_collection(ticker)

    async def _agentic(self, out, evidence):
        runs.append(out.ticker)
        await gate.wait()                     # hold the leader until the follower has joined
        return "findings"

    async def _stage_a(self, out, evidence, research_text):
        return stage_a_fallback()

    monkeypatch.setattr(TickerReportDataCollector, "collect", _collect)
    monkeypatch.setattr(ra.ResearchAgent, "_agentic_research", _agentic)
    monkeypatch.setattr(ra.ResearchAgent, "_generate_stage_a", _stage_a)
    monkeypatch.setattr(ra, "build_financial_context", lambda out: "evidence")
    monkeypatch.setattr(ra, "build_narrative_jobs", lambda *a, **k: [])
    monkeypatch.setattr(ra, "run_narrative_jobs", AsyncMock())
    monkeypatch.setattr(ra, "synthesize_core_thesis", AsyncMock())
    monkeypatch.setattr(ra, "synthesize_critical_factors", AsyncMock())

    svc, q = _deep_service(monkeypatch, None)
    monkeypatch.setattr(svc, "_mark_processing_started", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_update_status_async", AsyncMock())

    leader = asyncio.ensure_future(svc.generate_report("r1", "AAPL", "warren_buffett", "u1"))
    for _ in range(50):                       # wait until the leader is inside the agent run
        if runs:
            break
        await asyncio.sleep(0.01)
    follower = asyncio.ensure_future(svc.generate_report("r2", "AAPL", "warren_buffett", "u2"))
    await asyncio.sleep(0.05)
    gate.set()
    assert await leader is True and await follower is True
    assert runs == ["AAPL"], "the follower ran its own agent instead of joining the leader"

    completed = [c.args[0] for c in q.update.call_args_list
                 if isinstance(c.args[0], dict) and c.args[0].get("status") == "completed"]
    assert len(completed) == 2
    for written in completed:
        assert written["ticker_report_data"][GROUNDING_FREE_KEY] is True
    assert trc.TABLE_NAME in fake_db.upserts
    hit = await trc.get_cached_report("AAPL", "warren_buffett")
    assert hit is not None and hit[GROUNDING_FREE_KEY] is True


def test_outdated_starter_rows_do_not_use_up_the_daily_warm_cap(monkeypatch, caplog):
    """Rows the pre-retirement code wrote today (unversioned key) can never be served, so they
    must not count against `CHAT_STARTER_WARM_DAILY_CAP` either."""
    import hashlib

    import app.services.chat_starter_warm_service as warm

    q_new, q_old = "What tickers are hot today?", "Why is NAVN down 22% today?"
    rows = [
        {"question": q_new, "question_hash": warm.question_hash(q_new), "created_at": "t1"},
        {"question": q_old, "question_hash": hashlib.sha256(q_old.lower().encode()).hexdigest(),
         "created_at": "t0"},                                        # unversioned: orphaned
        {"question": None, "question_hash": "deadbeef", "created_at": "t0"},   # malformed
        {"question": q_old, "question_hash": None},                            # no key at all
    ]
    monkeypatch.setattr(warm, "get_supabase", lambda: _chain(rows))
    with caplog.at_level(logging.INFO, logger=warm.__name__):
        got = warm._warmed_hashes("2026-10-03")
    assert got == {warm.question_hash(q_new): "t1"}
    assert any("outdated key" in r.getMessage() and "2 row(s)" in r.getMessage()
               for r in caplog.records)


def test_migration_resets_the_fingerprint_of_card_less_scopes():
    sql = _executable_sql(MIGRATION)
    delete_at = sql.index("DELETE FROM public.ai_insight_cache")
    m = re.search(
        r"UPDATE public\.updates_insight_state s\s+SET last_inputset_id = NULL\s+"
        r"WHERE s\.last_inputset_id IS NOT NULL\s+AND NOT EXISTS \(\s*SELECT 1 FROM "
        r"public\.ai_insight_cache c\s+WHERE c\.scope = s\.scope AND c\.prompt_version >= (\d+)\s*\);",
        sql)
    from app.services.news_insight_service import _MIN_SERVABLE_PROMPT_VERSION

    assert m and int(m.group(1)) == _MIN_SERVABLE_PROMPT_VERSION
    assert m.start() > delete_at, "the reset must run after the card DELETE it depends on"
    assert re.search(r"CREATE TABLE public\.updates_insight_state \([^;]*last_inputset_id text",
                     SNAPSHOT.read_text())
