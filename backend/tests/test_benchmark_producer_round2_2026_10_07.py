"""Benchmark PRODUCER, review round 2 (2026-10-07) — `industry_benchmark_service` and its
helpers in `sector_benchmark_service`.

  F1  Every sync Supabase write (and the medians before it) ran ON the web process's one
      event loop: ~3,800 blocking PostgREST round-trips per quarterly run. Asserted by
      THREAD IDENTITY (like `test_sector_benchmark_off_the_loop.py`), never by grepping for
      `to_thread` — a source scan passes on a comment.
  F2  A sector that computed ZERO rows (its whole fetch failed) while another sector wrote
      settled the quarterly / weekly claim, so the same-day retry never covered it. It is
      now `IndustryBenchmarkRecomputeIncomplete` ("sectors empty"); "nothing written at all"
      stays the `...Skipped` path and is checked first.
  F3  The recompute always preferred the Storage copy of the universe, so an operator who
      validated a freshly built local file with `--dry-run` was shown the OLD bucket copy.
      An explicitly named file (`universe_file=`, the script's `--universe`, or
      `UNIVERSE_DATA_DIR`) now wins; a differing local copy is reported.
  HC-2 The Financial Services SECTOR aggregate for current ratio, quick ratio and interest
      coverage pooled ~95% banks and insurers — values the Health Check itself declares
      meaningless — and thin kept industries (exchanges, data vendors) were judged against
      it. Gated industries are now left out of the sector pool for those three metrics.
  F5  Newest-wins annual dedupe kept a fiscal-year-end change's 6-month transition stub over
      the full year sharing its key, and a stub base passed the 300-430-day YoY gap test.
  P3-1 (review round 3) An FMP outage that began or ended part-way through a sector left it
      pooled from what was fetched (Technology without its Software industries) and marked
      FRESH, so the same-day retry skipped it. A sector whose fetch FAILURES cross the line
      (> 10% of its tickers, or > 25% of an industry of >= MIN_SAMPLE_SIZE) now writes no
      aggregate and makes the run `IndustryBenchmarkRecomputeIncomplete` ("sectors lossy");
      EMPTY answers never count, so a structurally empty industry cannot hold a run open.

Hermetic: inline FMP-shaped rows, an in-memory `sector_benchmarks` table, a temp universe dir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set

import pytest

import app.services.industry_benchmark_service as ibs
import app.services.notification_jobs as nj
import app.services.sector_benchmark_service as sbs
from app import main as m
from app.integrations.fmp import FMPUnavailableException
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE

CQ = CALENDAR_QUARTER_PERIOD_TYPE
# The real Storage fetch, captured before any test stubs it (F3 tests drive it over a fake bucket).
_REAL_FETCH = ibs._fetch_benchmark_universe


# ── Fakes ───────────────────────────────────────────────────────────────────────────────


class _Table:
    """`sector_benchmarks` in memory. Upserts merge on the conflict key; the freshness probe
    answers the newest matching row. Every `execute()` records (kind, thread id), so a test
    can prove the sync SDK never ran on the event loop. `fail(batch)` may return an exception
    to raise."""

    def __init__(self, fail: Optional[Callable[[List[Dict[str, Any]]], Optional[Exception]]] = None):
        self.rows: Dict[tuple, Dict[str, Any]] = {}
        self.threads: List[tuple] = []
        self.fail = fail

    def table(self, _name: str):
        db = self

        class _Q:
            def __init__(self) -> None:
                self.batch: Optional[List[Dict[str, Any]]] = None
                self.eqs: Dict[str, Any] = {}

            def upsert(self, batch, on_conflict=None):
                self.batch = list(batch)
                return self

            def eq(self, col, val):
                self.eqs[col] = val
                return self

            def __getattr__(self, _attr):            # select / order / limit
                return lambda *a, **k: self

            def execute(self):
                kind = "upsert" if self.batch is not None else "select"
                db.threads.append((kind, threading.get_ident()))
                if self.batch is not None:
                    exc = db.fail(self.batch) if db.fail else None
                    if exc is not None:
                        raise exc
                    for r in self.batch:
                        key = (r["sector"], r["industry"], r["metric_name"],
                               r["period_type"], r["period_label"])
                        db.rows[key] = dict(r)
                    return SimpleNamespace(data=self.batch)
                hits = [r for r in db.rows.values()
                        if all(r.get(c) == v for c, v in self.eqs.items())]
                hits.sort(key=lambda r: r["computed_at"], reverse=True)
                return SimpleNamespace(data=hits[:1])

        return _Q()

    def group(self, sector: str, industry: str, period_type: str) -> Dict[tuple, Dict[str, Any]]:
        return {
            (r["metric_name"], r["period_label"]): r for r in self.rows.values()
            if r["sector"] == sector and r["industry"] == industry and r["period_type"] == period_type
        }


class _FMP:
    """Answers like FMP; tickers in `down` raise a 503 on every call, tickers in `empty`
    answer [] everywhere (a fund, a delisted name), tickers in `junk` answer an error object
    (counted as "refused" since review round 5). Records each call's ticker. Annual ratios: one complete year (2024)
    per company."""

    def __init__(self, down: Set[str] = frozenset(), empty: Set[str] = frozenset(),
                 junk: Set[str] = frozenset()) -> None:
        self.down = set(down)
        self.empty = set(empty)
        self.junk = set(junk)
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        if not name.startswith("get_"):
            raise AttributeError(name)

        async def call(ticker, *a, **k):
            self.calls.append(ticker)
            if ticker in self.down:
                raise FMPUnavailableException(f"{name}({ticker}): 503 Service Unavailable")
            if ticker in self.empty:
                return []
            if ticker in self.junk:
                return {"Error Message": "Limit Reach"}
            n = int(ticker[1:]) if ticker[1:].isdigit() else 0
            if name == "get_financial_ratios" and k.get("period") == "annual":
                return [{"date": "2024-12-31", "grossProfitMargin": 0.40 + n / 100,
                         "currentRatio": 1.0 + n / 10}]
            if name == "get_ratios_ttm":
                return [{"grossProfitMarginTTM": 0.40 + n / 100, "currentRatioTTM": 1.0 + n / 10}]
            return []

        return call


def _universe(groups: Dict[str, Dict[str, List[str]]]) -> List[Dict[str, Any]]:
    """{sector: {industry: [tickers]}} → universe entries."""
    return [
        {"industry": ind, "sector": sector,
         "market_caps": {t: 1.0e10 - i for i, t in enumerate(tickers)}}
        for sector, inds in groups.items() for ind, tickers in inds.items()
    ]


def _svc(monkeypatch, fmp: _FMP, universe: List[Dict[str, Any]], db: Optional[_Table] = None):
    """The REAL fiscal + TTM fetch and write paths over `fmp` and `db`."""
    db = db or _Table()
    sector_svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    sector_svc.fmp = fmp
    sector_svc.supabase = db
    sector_svc._fmp_semaphore = asyncio.Semaphore(10)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc.supabase = db
    svc._sb = sector_svc
    svc._fmp = fmp
    svc._calendar_quarter_blocked = False
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: [dict(e) for e in universe])
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "BATCH_DELAY_SECONDS", 0)
    monkeypatch.setattr(ibs, "_industry_benchmark_service", svc)
    monkeypatch.setattr(sbs, "_exhausted_in_a_row", 0)
    return svc, db


_TWO = {
    "Consumer Cyclical": {"Restaurants": [f"R{i}" for i in range(5)]},
    "Energy": {"Oil & Gas E&P": [f"E{i}" for i in range(5)]},
}
_ENERGY_DOWN = {f"E{i}" for i in range(5)}


def _ledger(monkeypatch) -> Dict[str, Any]:
    ledger: Dict[str, Any] = {}

    def _claim(job, *, timezone_name="UTC", now=None, stale_seconds=None):
        ledger["claimed"] = job
        return True

    def _finish(job, *, success, items=0, error=None, timezone_name="UTC", now=None):
        ledger["finished"] = {"job": job, "success": success, "error": error}

    monkeypatch.setattr(nj, "claim_scheduled", _claim)
    monkeypatch.setattr(nj, "finish_scheduled", _finish)
    return ledger


# ═══ F1 — no sync Supabase call, median or value derivation on the event loop ═══════════


def _record_threads(monkeypatch, svc, names: List[str]) -> List[tuple]:
    """Wrap each named sync method on the instance with a (name, thread id) recorder."""
    calls: List[tuple] = []
    for name in names:
        real = getattr(svc, name)

        def _make(real=real, name=name):
            def recorder(*a, **k):
                calls.append((name, threading.get_ident()))
                return real(*a, **k)
            return recorder

        monkeypatch.setattr(svc, name, _make())
    return calls


def _assert_off_loop(db: _Table, calls: List[tuple], loop_thread: int, expected: Set[str]) -> None:
    reached = {name for name, _ in calls} | {kind for kind, _ in db.threads}
    assert expected <= reached, f"never reached {expected - reached}: the test proves nothing"
    on_loop = [n for n, t in calls if t == loop_thread] + [k for k, t in db.threads if t == loop_thread]
    assert on_loop == [], f"sync work ran ON the event loop: {on_loop}"


@pytest.mark.asyncio
async def test_the_fiscal_sweep_writes_probes_and_derives_off_the_loop(monkeypatch):
    svc, db = _svc(monkeypatch, _FMP(), _universe(_TWO))
    calls = _record_threads(monkeypatch, svc, ["_value_lists_from", "_rows_from_values", "_load_universe"])
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["rows_upserted"] > 0
    _assert_off_loop(db, calls, threading.get_ident(),
                     {"upsert", "select", "_value_lists_from", "_rows_from_values", "_load_universe"})
    # industry rows AND both sector aggregates went through the off-loop writer
    assert db.group("Energy", "", "annual") and db.group("Energy", "Oil & Gas E&P", "annual")


@pytest.mark.asyncio
async def test_the_ttm_sweep_writes_and_probes_off_the_loop(monkeypatch):
    svc, db = _svc(monkeypatch, _FMP(), _universe(_TWO))
    calls = _record_threads(monkeypatch, svc, ["_ttm_rows", "_load_universe"])
    summary = await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    assert summary["rows_upserted"] > 0
    _assert_off_loop(db, calls, threading.get_ident(), {"upsert", "select", "_ttm_rows"})
    assert db.group("Energy", "", "ttm") and db.group("Energy", "Oil & Gas E&P", "ttm")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_the_industries_only_paths_write_off_the_loop(monkeypatch, mode):
    svc, db = _svc(monkeypatch, _FMP(), _universe(_TWO))
    calls = _record_threads(monkeypatch, svc, ["_emit"])
    fn = svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm
    summary = await fn(industries=["Restaurants"])
    assert summary["rows_upserted"] > 0
    _assert_off_loop(db, calls, threading.get_ident(), {"upsert", "_emit"})


# ═══ F2 — a sector that computed ZERO rows leaves the run unsettled ═══════════════════════


@pytest.mark.asyncio
async def test_a_sector_whose_whole_fetch_failed_makes_the_fiscal_run_incomplete(monkeypatch, caplog):
    fmp = _FMP(down=_ENERGY_DOWN)
    svc, db = _svc(monkeypatch, fmp, _universe(_TWO))

    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)

    exc = info.value
    assert exc.reason == "sectors empty"
    assert exc.empty_sectors == ["Energy"] and exc.failed_sectors == []
    assert exc.summary["sectors_empty"] == 1 and exc.summary["sectors_failed"] == 0
    assert exc.summary["sectors_done"] == 2 and exc.summary["rows_upserted"] > 0
    assert "INCOMPLETE (1 sector(s) computed zero rows: Energy)" in str(exc)
    assert [r for r in caplog.records if r.levelno == logging.WARNING
            and "Energy computed ZERO rows" in r.getMessage() and "will not settle" in r.getMessage()]
    assert [r for r in caplog.records if r.levelno == logging.ERROR
            and "recompute INCOMPLETE" in r.getMessage()]
    assert svc._sector_is_fresh("Consumer Cyclical", 24) is True
    assert svc._sector_is_fresh("Energy", 24) is False

    # The same-day retry, FMP back: only Energy is fetched, and the run settles.
    fmp.down.clear()
    fmp.calls.clear()
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert set(fmp.calls) == _ENERGY_DOWN
    assert summary["sectors_skipped_fresh"] == 1 and summary["sectors_empty"] == 0
    assert svc._sector_is_fresh("Energy", 24) is True


@pytest.mark.asyncio
async def test_a_sector_whose_whole_fetch_failed_makes_the_ttm_run_incomplete(monkeypatch):
    fmp = _FMP(down=_ENERGY_DOWN)
    svc, _ = _svc(monkeypatch, fmp, _universe(_TWO))
    with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
        await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    assert info.value.reason == "sectors empty" and info.value.empty_sectors == ["Energy"]
    assert "TTM" in str(info.value)
    assert svc._ttm_sector_is_fresh("Consumer Cyclical", 24) is True
    assert svc._ttm_sector_is_fresh("Energy", 24) is False

    fmp.down.clear()
    fmp.calls.clear()
    summary = await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    assert set(fmp.calls) == _ENERGY_DOWN and summary["sectors_skipped_fresh"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_an_empty_sector_leaves_the_scheduled_claim_unsettled(monkeypatch, mode):
    svc, _ = _svc(monkeypatch, _FMP(down=_ENERGY_DOWN), _universe(_TWO))
    ledger = _ledger(monkeypatch)

    async def body():   # main.py's `_benchmarks` / `_ttm` closures
        service = ibs.get_industry_benchmark_service()
        fn = service.recompute_all if mode == "fiscal" else service.recompute_all_ttm
        return await fn(skip_if_fresh_hours=24)

    job = m.JOB_INDUSTRY_BENCHMARK_QUARTERLY if mode == "fiscal" else m.JOB_TTM_BENCHMARK_WEEKLY
    assert await m._run_claimed_phase(job, "phase", body) is False
    assert ledger["finished"]["success"] is False
    assert ledger["finished"]["error"].startswith("IndustryBenchmarkRecomputeIncomplete:")
    assert "computed zero rows: Energy" in ledger["finished"]["error"]


@pytest.mark.asyncio
async def test_a_raised_and_an_empty_sector_are_both_named(monkeypatch):
    groups = dict(_TWO)
    groups["Utilities"] = {"Utilities - Regulated Electric": [f"U{i}" for i in range(5)]}
    db = _Table(lambda b: RuntimeError("Server disconnected")
                if b[0]["sector"] == "Utilities" else None)
    svc, _ = _svc(monkeypatch, _FMP(down=_ENERGY_DOWN), _universe(groups), db)
    with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    exc = info.value
    assert exc.reason == "sectors failed and empty"
    assert exc.failed_sectors == ["Utilities"] and exc.empty_sectors == ["Energy"]
    assert ("INCOMPLETE (1 sector(s) failed: Utilities; 1 sector(s) computed zero rows: "
            "Energy)") in str(exc)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_nothing_written_at_all_is_still_skipped_not_incomplete(monkeypatch, mode):
    svc, _ = _svc(monkeypatch, _FMP(down={f"R{i}" for i in range(5)} | _ENERGY_DOWN), _universe(_TWO))
    fn = svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm
    with pytest.raises(ibs.IndustryBenchmarkRecomputeSkipped) as info:
        await fn(skip_if_fresh_hours=24)
    assert info.value.reason == "nothing written"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_a_dry_run_with_an_empty_sector_still_returns(monkeypatch, mode):
    svc, db = _svc(monkeypatch, _FMP(down=_ENERGY_DOWN), _universe(_TWO))
    fn = svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm
    summary = await fn(dry_run=True)
    assert summary["dry_run"] is True and summary["sectors_empty"] == 0
    assert not [k for k, _ in db.threads if k == "upsert"]


# ═══ F3 — an explicitly named universe file wins; a differing local copy is reported ═════


_V_BUCKET = [{"industry": "Banks", "sector": "Financial Services", "market_caps": {"JPM": 6e11}}]
_V_LOCAL = [{"industry": "Software", "sector": "Technology", "market_caps": {"MSFT": 3e12}}]


def _universe_file(path, industries, generated_at="2026-10-07T12:00:00+00:00"):
    path.write_text(json.dumps({"generated_at": generated_at, "ticker_count": 1,
                                "industries": industries}), encoding="utf-8")
    return path


class _Storage:
    def __init__(self, blob: Optional[bytes]):
        self.blob = blob
        self.downloads: List[str] = []

    def client(self):
        storage = self

        class _Bucket:
            def __init__(self, _bucket):
                pass

            def download(self, name):
                storage.downloads.append(name)
                return storage.blob

        return SimpleNamespace(storage=SimpleNamespace(from_=_Bucket))


def _install_storage(monkeypatch, industries, generated_at="2026-06-24T00:00:00+00:00") -> _Storage:
    storage = _Storage(json.dumps({"generated_at": generated_at, "ticker_count": 1,
                                   "industries": industries}).encode())
    monkeypatch.setattr(ibs, "get_supabase", storage.client)
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "load_universe", lambda _f: pytest.fail("the boot memo was consulted"))
    return storage


def _bare() -> ibs.IndustryBenchmarkService:
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc._calendar_quarter_blocked = False
    return svc


def test_a_named_universe_file_wins_over_the_bucket_and_says_so(monkeypatch, tmp_path, caplog):
    storage = _install_storage(monkeypatch, _V_BUCKET)
    path = _universe_file(tmp_path / "fresh_build.json", _V_LOCAL)
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        assert [s for s, _ in _bare()._load_universe(str(path))] == ["Technology"]
    assert storage.downloads == []                       # Storage was never asked
    assert any("EXPLICITLY named universe file" in r.getMessage() and str(path) in r.getMessage()
               and r.levelno == logging.WARNING for r in caplog.records)
    # Mutation twin: no file named → the bucket copy, as the scheduled runs read it.
    assert [s for s, _ in _bare()._load_universe()] == ["Financial Services"]
    assert storage.downloads == [ibs.BENCHMARK_UNIVERSE]


@pytest.mark.parametrize("content", [
    None, "{not json", "[]", json.dumps({"industries": "x"}), json.dumps({"industries": []}),
])
def test_an_unreadable_named_file_stops_the_run_and_never_falls_back(monkeypatch, tmp_path, caplog, content):
    storage = _install_storage(monkeypatch, _V_BUCKET)
    path = tmp_path / "broken.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeSkipped) as info:
            _bare()._load_universe(str(path))
    assert info.value.reason == "universe file unreadable" and str(path) in str(info.value)
    assert storage.downloads == []
    assert [r for r in caplog.records if r.levelno == logging.ERROR and str(path) in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_recompute_passes_the_named_file_through(monkeypatch, tmp_path, mode):
    db = _Table()
    svc, _ = _svc(monkeypatch, _FMP(), [], db)
    # The REAL Storage fetch over a bucket holding the Banks universe: it must not be asked.
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", _REAL_FETCH)
    storage = _install_storage(monkeypatch, _V_BUCKET)
    path = _universe_file(tmp_path / "u.json", _universe({"Technology": {"Software": [f"S{i}" for i in range(5)]}}))
    fn = svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm
    await fn(skip_if_fresh_hours=24, universe_file=str(path))
    assert {r["sector"] for r in db.rows.values()} == {"Technology"}
    assert storage.downloads == []
    # …and the industries-only validation path too.
    db.rows.clear()
    await fn(industries=["Software"], universe_file=str(path), dry_run=True)
    with pytest.raises(ibs.IndustryBenchmarkRecomputeSkipped):
        await fn(industries=["Software"], universe_file=str(tmp_path / "missing.json"))


def _local_note(caplog) -> List[logging.LogRecord]:
    return [r for r in caplog.records if "NOT the different local copy" in r.getMessage()
            or "local benchmark_universe.json exists" in r.getMessage()]


def test_a_newer_local_copy_than_the_bucket_is_a_warning(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("UNIVERSE_DATA_DIR", str(tmp_path))
    _universe_file(tmp_path / ibs.BENCHMARK_UNIVERSE, _V_LOCAL, "2026-10-07T12:00:00+00:00")
    _install_storage(monkeypatch, _V_BUCKET, "2026-06-24T00:00:00+00:00")
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        assert [s for s, _ in _bare()._load_universe()] == ["Financial Services"]   # unchanged
    (note,) = _local_note(caplog)
    assert note.levelno == logging.WARNING
    msg = note.getMessage()
    assert "uses the universe-data bucket copy" in msg and str(tmp_path) in msg
    assert "--universe PATH" in msg and "generated 2026-10-07" in msg


@pytest.mark.parametrize("local_generated, level", [
    ("2026-06-01T00:00:00+00:00", logging.INFO),   # older: a deployed boot cache after an upload
    (None, logging.WARNING),                       # ages not comparable
    ("garbage", logging.WARNING),
])
def test_an_older_or_undated_local_copy(monkeypatch, tmp_path, caplog, local_generated, level):
    monkeypatch.setenv("UNIVERSE_DATA_DIR", str(tmp_path))
    _universe_file(tmp_path / ibs.BENCHMARK_UNIVERSE, _V_LOCAL, local_generated)
    _install_storage(monkeypatch, _V_BUCKET, "2026-06-24T00:00:00+00:00")
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        _bare()._load_universe()
    (note,) = _local_note(caplog)
    assert note.levelno == level


def test_an_identical_absent_or_unreadable_local_copy(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("UNIVERSE_DATA_DIR", str(tmp_path))
    _install_storage(monkeypatch, _V_BUCKET)
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        _bare()._load_universe()                                       # absent
        _universe_file(tmp_path / ibs.BENCHMARK_UNIVERSE, _V_BUCKET, "2027-01-01T00:00:00+00:00")
        _bare()._load_universe()                                       # same industries
    assert _local_note(caplog) == []

    (tmp_path / ibs.BENCHMARK_UNIVERSE).write_text("{oops", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        assert [s for s, _ in _bare()._load_universe()] == ["Financial Services"]
    (note,) = _local_note(caplog)
    assert note.levelno == logging.WARNING and "unreadable" in note.getMessage()


def test_the_script_resolves_an_explicit_file_or_none(monkeypatch, tmp_path):
    from scripts import recompute_industry_benchmarks as script

    monkeypatch.delenv("UNIVERSE_DATA_DIR", raising=False)
    assert script.resolve_universe_file(None) is None                  # the bucket
    assert script.resolve_universe_file("/x/u.json") == "/x/u.json"
    monkeypatch.setenv("UNIVERSE_DATA_DIR", str(tmp_path))
    assert script.resolve_universe_file(None) == str(tmp_path / ibs.BENCHMARK_UNIVERSE)
    assert script.resolve_universe_file("/x/u.json") == "/x/u.json"    # --universe wins


@pytest.mark.asyncio
@pytest.mark.parametrize("ttm", [False, True])
async def test_the_script_passes_the_file_to_the_service(monkeypatch, ttm, caplog):
    from scripts import recompute_industry_benchmarks as script

    seen: Dict[str, Any] = {}

    class _Svc:
        async def recompute_all(self, **kw):
            seen.update(kw, fn="fiscal")
            return {}

        async def recompute_all_ttm(self, **kw):
            seen.update(kw, fn="ttm")
            return {}

    monkeypatch.setattr(script, "get_industry_benchmark_service", lambda: _Svc())
    monkeypatch.delenv("UNIVERSE_DATA_DIR", raising=False)
    args = argparse.Namespace(skip_recent_hours=0, sector=None, industry=["X"], dry_run=False,
                              ttm=ttm, universe="/tmp/u.json")
    with caplog.at_level(logging.WARNING, logger="recompute_industry_benchmarks"):
        await script.main(args)
    assert seen["universe_file"] == "/tmp/u.json" and seen["fn"] == ("ttm" if ttm else "fiscal")
    assert any("NOT a dry run" in r.getMessage() for r in caplog.records)
    args.universe = None
    await script.main(args)
    assert seen["universe_file"] is None


# ═══ HC-2 — gated industries are left out of the SECTOR pool for the three ratios ═══════


_FS = "Financial Services"
_BANKS = "Banks - Regional"
_EXCH = "Financial - Data & Stock Exchanges"
_BROKERS = "Insurance - Brokers"

_BANK_CR = [0.5] * 20            # deposits as current liabilities: an artefact
_EXCH_CR = [1.1, 1.2, 1.3, 1.4, 1.5, 1.6]
_BANK_IC = [1.0] * 20            # funding cost as "interest": an artefact
_EXCH_IC = [15.0, 16.0, 17.0, 18.0, 19.0, 20.0]
_BROKER_IC = [6.0, 7.0]


def _fs_values() -> Dict[str, Dict[tuple, List[float]]]:
    def fiscal(cr, qr, ic, roe):
        return {("current_ratio", "annual", "2025"): list(cr),
                ("quick_ratio", "annual", "2025"): list(qr),
                ("interest_coverage", "annual", "2025"): list(ic),
                ("roe", "annual", "2025"): list(roe),
                ("current_ratio", CQ, "Q2'26"): list(cr)}
    return {
        "JPM": fiscal(_BANK_CR, _BANK_CR, _BANK_IC, [0.10] * 20),
        "SPGI": fiscal(_EXCH_CR, _EXCH_CR, _EXCH_IC, [0.30] * 6),
        "AON": fiscal([1.0, 1.1], [0.9, 1.0], _BROKER_IC, [0.20] * 2),
    }


def _fs_wired(monkeypatch, db: _Table):
    svc = _bare()
    svc.supabase = db
    by_first = _fs_values()

    async def values(ticker_caps, _al, _ql, **_k):
        return defaultdict(list, {k: list(v) for k, v in by_first[ticker_caps[0][0]].items() if v})

    monkeypatch.setattr(svc, "_industry_value_lists", values)
    return svc


_FS_INDUSTRIES = [(_BANKS, [("JPM", 1.0)]), (_EXCH, [("SPGI", 1.0)]), (_BROKERS, [("AON", 1.0)])]


@pytest.mark.asyncio
async def test_the_fs_sector_ratios_pool_only_the_kept_industries(monkeypatch, caplog):
    db = _Table()
    svc = _fs_wired(monkeypatch, db)
    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        await svc._compute_sector(_FS, _FS_INDUSTRIES, 1, 1)
    agg = db.group(_FS, "", "annual")

    cr = agg[("current_ratio", "2025")]
    assert cr["median_value"] == pytest.approx(1.35) and cr["sample_size"] == 6   # banks' 0.5 out
    assert agg[("quick_ratio", "2025")]["sample_size"] == 6
    ic = agg[("interest_coverage", "2025")]                                        # + the brokers
    assert ic["sample_size"] == 8 and ic["median_value"] == pytest.approx(16.5)
    assert agg[("roe", "2025")]["sample_size"] == 28                              # ungated: all
    assert db.group(_FS, "", CQ)[("current_ratio", "Q2'26")]["sample_size"] == 6
    # The banks' own industry rows are still written (harmless: never served for a bank).
    assert db.group(_FS, _BANKS, "annual")[("current_ratio", "2025")]["median_value"] == 0.5
    (line,) = [r.getMessage() for r in caplog.records if "pool only the industries" in r.getMessage()]
    assert "current_ratio, interest_coverage, quick_ratio" in line and _BANKS in line
    assert _EXCH not in line and "left out 2 industries" in line   # brokers: liquidity only


@pytest.mark.asyncio
async def test_a_sector_of_only_gated_industries_writes_no_ratio_comparator(monkeypatch):
    db = _Table()
    svc = _fs_wired(monkeypatch, db)
    await svc._compute_sector(_FS, [("Banks—Regional", [("JPM", 1.0)])], 1, 1)   # em dash
    agg = db.group(_FS, "", "annual")
    assert ("current_ratio", "2025") not in agg and ("interest_coverage", "2025") not in agg
    assert agg[("roe", "2025")]["sample_size"] == 20                              # the marker lands


@pytest.mark.asyncio
async def test_the_ttm_fs_sector_ratios_pool_only_the_kept_industries(monkeypatch):
    db = _Table()
    svc = _bare()
    svc.supabase = db
    ttm = {
        "JPM": {"current_ratio": _BANK_CR, "interest_coverage": _BANK_IC, "roe": [0.10] * 20},
        "SPGI": {"current_ratio": _EXCH_CR, "interest_coverage": _EXCH_IC, "roe": [0.30] * 6},
        "AON": {"interest_coverage": _BROKER_IC, "roe": [0.20] * 2},
    }

    async def values(tc, _sem, **_k):
        return defaultdict(list, {k: list(v) for k, v in ttm[tc[0][0]].items()})

    monkeypatch.setattr(svc, "_industry_ttm_values", values)
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: _universe(
        {_FS: {_BANKS: ["JPM"], _EXCH: ["SPGI"], _BROKERS: ["AON"]}}))
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    agg = db.group(_FS, "", "ttm")
    assert agg[("current_ratio", "TTM")]["sample_size"] == 6
    assert agg[("current_ratio", "TTM")]["median_value"] == pytest.approx(1.35)
    assert agg[("interest_coverage", "TTM")]["sample_size"] == 8
    assert agg[("roe", "TTM")]["sample_size"] == 28


# ═══ F5 — a transition stub never displaces a full year, and is never a YoY side ════════


# A June → December fiscal-year-end change, filed with a 6-month transition stub.
_JUN_TO_DEC = [
    {"date": "2023-06-30", "revenue": 100.0, "grossProfitMargin": 0.30, "freeCashFlow": 10.0},
    {"date": "2024-06-30", "revenue": 110.0, "grossProfitMargin": 0.31, "freeCashFlow": 11.0},
    {"date": "2024-12-31", "revenue": 56.0, "grossProfitMargin": 0.50, "freeCashFlow": 9.0},  # stub
    {"date": "2025-12-31", "revenue": 120.0, "grossProfitMargin": 0.32, "freeCashFlow": 12.0},
]


def test_the_full_year_keeps_its_key_over_a_stub():
    for rows in (_JUN_TO_DEC, list(reversed(_JUN_TO_DEC))):
        by_key = sbs._by_annual_key(rows)
        assert by_key["2024"]["date"] == "2024-06-30"
        assert by_key["2025"]["date"] == "2025-12-31" and by_key["2023"]["date"] == "2023-06-30"


def test_yoy_never_pairs_a_full_year_with_a_stub_and_keeps_the_true_prior_year():
    yoy = sbs._compute_yoy_for_records(_JUN_TO_DEC, "revenue", is_quarterly=False)
    # 2024: Jun-2024 vs Jun-2023 (+10%), dropped under newest-wins (Dec-2024 vs Jun-2023 =
    # 549 days). 2025: vs Jun-2024 is 549 days → no figure; newest-wins filed +114% (vs the stub).
    assert yoy == {"2024": 10.0}


def test_level_metrics_and_statement_joins_use_the_full_year():
    company = {"ratios_annual": _JUN_TO_DEC, "income_annual": _JUN_TO_DEC,
               "cashflow_annual": [r for r in _JUN_TO_DEC if r["date"] != "2024-12-31"]}
    svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    gm = next(mc for mc in sbs.METRIC_CONFIGS if mc["name"] == "gross_margin")
    assert svc._collect_metric_values([company], gm, "annual")["2024"] == [0.31]
    # Cash flow lacks the stub: income's 2024 must still be the June year, or FCF 11 would be
    # divided by the stub's half-year revenue 56 (0.196) instead of 110 (0.10).
    fcf = sbs._compute_ratio_values([company], "fcf_margin", "annual")
    assert fcf["2024"] == [pytest.approx(0.10)]


def test_a_lone_stub_is_never_a_yoy_base_or_subject():
    # December → June: the stub is alone under 2024, so no collision — the YoY check must
    # catch it (Jun-2025 vs the stub is 365 days apart and would file ~+100%).
    rows = [
        {"date": "2022-12-31", "revenue": 100.0},
        {"date": "2023-12-31", "revenue": 110.0},
        {"date": "2024-06-30", "revenue": 60.0},     # Jan-Jun 2024 stub
        {"date": "2025-06-30", "revenue": 125.0},
    ]
    assert sbs._compute_yoy_for_records(rows, "revenue", is_quarterly=False) == {"2023": 10.0}


def test_two_full_years_on_one_key_newest_wins_and_a_repeat_votes_once():
    full = [
        {"date": "2023-01-05", "grossProfitMargin": 0.10},
        {"date": "2024-01-08", "grossProfitMargin": 0.20},   # keyed 2024 (Jan 8 - 7 days)
        {"date": "2024-12-31", "grossProfitMargin": 0.30},   # keyed 2024, 358 days later: full
    ]
    assert sbs._by_annual_key(full)["2024"]["grossProfitMargin"] == 0.30
    repeat = [{"date": "2024-12-31", "grossProfitMargin": 0.6}] * 2 + [
        {"date": "2023-12-31", "grossProfitMargin": 0.1}]
    svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    gm = next(mc for mc in sbs.METRIC_CONFIGS if mc["name"] == "gross_margin")
    assert svc._collect_metric_values([{"ratios_annual": repeat}], gm, "annual") == {
        "2024": [0.6], "2023": [0.1]}


@pytest.mark.parametrize("prev, cur, short", [
    ("2024-01-01", "2024-10-26", True),      # 299 days
    ("2024-01-01", "2024-10-27", False),     # exactly 300 days
    ("2024-12-28", "2025-12-27", False),     # 52 weeks
    ("2023-12-30", "2025-01-04", False),     # 53 weeks
])
def test_short_means_under_the_yoy_floor(prev, cur, short):
    rows = [{"date": prev}, {"date": cur}]
    spans = sbs._annual_spans(rows)
    assert sbs._is_short_annual(rows[1], spans) is short
    assert sbs._is_short_annual(rows[0], spans) is False         # the oldest: span unknown


def test_undated_and_repeated_rows_are_never_short():
    rows = [{"calendarYear": "2024"}, {"date": "2024-12-31"}, {"date": "2024-12-31"}, None, "x"]
    spans = sbs._annual_spans([r for r in rows if isinstance(r, dict)])
    assert not any(sbs._is_short_annual(r, spans) for r in rows if isinstance(r, dict))
    # A dated full row still replaces an undated row with the same key.
    assert sbs._by_annual_key(rows)["2024"]["date"] == "2024-12-31"


# ═══ P3-1 (review round 3) — a sector that lost too many companies to FETCH FAILURES ═══════


_SOFTWARE = {f"S{i}" for i in range(5)}
_TECH_ENERGY = {
    "Technology": {
        "Computer Hardware": [f"H{i}" for i in range(5)],
        "Software - Application": sorted(_SOFTWARE),
    },
    "Energy": {"Oil & Gas E&P": [f"E{i}" for i in range(5)]},
}
_TECH_TICKERS = {f"H{i}" for i in range(5)} | _SOFTWARE
# The marker the readers' freshness probe reads, per mode.
_MARKER_TYPE = {"fiscal": "annual", "ttm": "ttm"}


def _fresh(svc, mode: str, sector: str) -> bool:
    probe = svc._sector_is_fresh if mode == "fiscal" else svc._ttm_sector_is_fresh
    return probe(sector, 24)


def _sweep(svc, mode: str):
    return svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_a_sector_that_lost_an_industry_to_an_outage_is_not_marked_fresh(monkeypatch, caplog, mode):
    """The review's repro: Software (5 of Technology's 10 tickers) 503s for the whole run while
    Hardware and Energy answer. Pre-fix: Technology's aggregate was rebuilt from Hardware alone
    (n=5) and marked FRESH, so the retry skipped it."""
    fmp = _FMP(down=_SOFTWARE)
    svc, db = _svc(monkeypatch, fmp, _universe(_TECH_ENERGY))
    marker = _MARKER_TYPE[mode]

    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await _sweep(svc, mode)(skip_if_fresh_hours=24)

    exc = info.value
    assert exc.reason == "sectors lossy"
    assert exc.lossy_sectors == ["Technology"] and exc.empty_sectors == [] and exc.failed_sectors == []
    assert exc.summary["sectors_lossy"] == 1 and exc.summary["lossy_sectors"] == ["Technology"]
    assert exc.summary["sectors_done"] == 2 and exc.summary["rows_upserted"] > 0
    assert "1 sector(s) lost too many companies to FMP fetch failures: Technology" in str(exc)
    # No Technology aggregate at all (no skewed median, no fresh marker); its Hardware rows land.
    assert db.group("Technology", "", marker) == {}
    assert db.group("Technology", "", CQ) == {}
    assert db.group("Technology", "Computer Hardware", marker)
    assert db.group("Energy", "", marker)
    assert _fresh(svc, mode, "Technology") is False and _fresh(svc, mode, "Energy") is True
    (warn,) = [r.getMessage() for r in caplog.records
               if "the sector aggregate is NOT written" in r.getMessage()]
    assert "Technology — the sector aggregate is NOT written: 5 of 10 tickers lost to transient " \
           "fetch failures (50%: 0 rate-limited, 5 unavailable); industries over the line: " \
           "Software - Application 5/5 (100%)" in warn
    assert "same-day retry recomputes the sector" in warn

    # The same-day retry, FMP back: only Technology is fetched, and the run settles with a
    # sector median over all ten companies.
    fmp.down.clear()
    fmp.calls.clear()
    summary = await _sweep(svc, mode)(skip_if_fresh_hours=24)
    assert set(fmp.calls) == _TECH_TICKERS
    assert summary["sectors_skipped_fresh"] == 1 and summary["sectors_lossy"] == 0
    assert summary["lossy_sectors"] == []
    label = "2024" if mode == "fiscal" else "TTM"
    assert db.group("Technology", "", marker)[("gross_margin", label)]["sample_size"] == 10
    assert _fresh(svc, mode, "Technology") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_empty_answers_never_hold_a_sector_open(monkeypatch, mode):
    """The twin: the same five companies answering EMPTY (funds, delisted names) is a universe
    property, not an outage — a retry would find them empty again, for ever. It settles."""
    fmp = _FMP(empty=_SOFTWARE)
    svc, db = _svc(monkeypatch, fmp, _universe(_TECH_ENERGY))
    summary = await _sweep(svc, mode)(skip_if_fresh_hours=24)
    assert summary["fetch_empty"] == 5 and summary["fetch_failures"] == 0
    assert summary["sectors_lossy"] == 0 and summary["lossy_sectors"] == []
    assert _fresh(svc, mode, "Technology") is True
    label = "2024" if mode == "fiscal" else "TTM"
    assert db.group("Technology", "", _MARKER_TYPE[mode])[("gross_margin", label)]["sample_size"] == 5


@pytest.mark.asyncio
async def test_every_transient_failure_kind_counts(monkeypatch, caplog):
    # Review round 5 (P4-2): only TRANSIENT failures count toward the line; an error object
    # (a 200 whose body is not a list) is a refusal and settles — see
    # test_benchmark_producer_round6_loss_line.py.
    svc, _ = _svc(monkeypatch, _FMP(down=_SOFTWARE), _universe(_TECH_ENERGY))
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)
    assert info.value.lossy_sectors == ["Technology"]
    assert any("0 rate-limited, 5 unavailable)" in r.getMessage() for r in caplog.records
               if "NOT written" in r.getMessage())


def _grid(industries: int, size: int = 5, small: int = 0) -> Dict[str, Dict[str, List[str]]]:
    """One Technology sector of `industries` industries of `size` tickers (prefix A, B, …),
    plus one `small` industry (prefix Z) when asked."""
    inds = {f"Industry {chr(65 + j)}": [f"{chr(65 + j)}{i}" for i in range(size)]
            for j in range(industries)}
    if small:
        inds["Tiny Industry"] = [f"Z{i}" for i in range(small)]
    return {"Technology": inds}


@pytest.mark.asyncio
@pytest.mark.parametrize("groups, down, lossy", [
    # Sector line (> 10% of the sector's tickers), each industry losing 1 of 5 (20%, under
    # the industry line): 2 of 20 = 10% settles, 3 of 20 = 15% does not.
    (_grid(4), {"A0", "B0"}, False),
    (_grid(4), {"A0", "B0", "C0"}, True),
    # Industry line (> 25% of an industry of >= MIN_SAMPLE_SIZE): 2 of 5 (40%) in a sector of
    # 40 (5%) does not settle; 1 of 5 (20%) does.
    (_grid(8), {"A0", "A1"}, True),
    (_grid(8), {"A0"}, False),
    # An industry below MIN_SAMPLE_SIZE never trips the industry line: all 4 of 4 lost in a
    # sector of 64 (6%) settles (it writes no industry row of its own anyway).
    (_grid(12, small=4), {"Z0", "Z1", "Z2", "Z3"}, False),
])
async def test_the_incomplete_line(monkeypatch, groups, down, lossy):
    svc, db = _svc(monkeypatch, _FMP(down=down), _universe(groups))
    if lossy:
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)
        assert info.value.lossy_sectors == ["Technology"]
        assert db.group("Technology", "", "annual") == {}
    else:
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
        assert summary["fetch_failures"] == len(down) and summary["sectors_lossy"] == 0
        assert db.group("Technology", "", "annual")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_a_dry_run_names_a_lossy_sector_and_still_returns(monkeypatch, caplog, mode):
    svc, db = _svc(monkeypatch, _FMP(down=_SOFTWARE), _universe(_TECH_ENERGY))
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        summary = await _sweep(svc, mode)(dry_run=True)
    assert summary["dry_run"] is True
    assert summary["lossy_sectors"] == ["Technology"] and summary["sectors_lossy"] == 1
    assert not [k for k, _ in db.threads if k == "upsert"]
    assert any("Technology — the sector aggregate is NOT written" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_failed_empty_and_lossy_are_all_named(monkeypatch):
    groups = dict(_TECH_ENERGY)
    groups["Utilities"] = {"Utilities - Regulated Electric": [f"U{i}" for i in range(5)]}
    groups["Consumer Cyclical"] = {"Restaurants": [f"R{i}" for i in range(5)]}
    db = _Table(lambda b: RuntimeError("Server disconnected")
                if b[0]["sector"] == "Utilities" else None)
    svc, _ = _svc(monkeypatch, _FMP(down=_SOFTWARE | _ENERGY_DOWN), _universe(groups), db)
    with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    exc = info.value
    assert exc.reason == "sectors failed, empty and lossy"
    assert (exc.failed_sectors, exc.empty_sectors, exc.lossy_sectors) == (
        ["Utilities"], ["Energy"], ["Technology"])
    assert ("1 sector(s) failed: Utilities; 1 sector(s) computed zero rows: Energy; 1 sector(s) "
            "lost too many companies to FMP fetch failures: Technology") in str(exc)
    assert svc._sector_is_fresh("Consumer Cyclical", 24) is True


@pytest.mark.parametrize("failed, empty, lossy, reason", [
    (["A"], [], [], "sectors failed"),
    ([], ["B"], [], "sectors empty"),
    ([], [], ["C"], "sectors lossy"),
    (["A"], ["B"], [], "sectors failed and empty"),
    (["A"], [], ["C"], "sectors failed and lossy"),
    ([], ["B"], ["C"], "sectors empty and lossy"),
    (["A"], ["B"], ["C"], "sectors failed, empty and lossy"),
])
def test_the_incomplete_reason_names_every_kind(failed, empty, lossy, reason):
    exc = ibs.IndustryBenchmarkRecomputeIncomplete(
        "fiscal", failed, {"rows_upserted": 9}, empty, lossy_sectors=lossy,
    )
    assert exc.reason == reason
    # The positional 4-argument form the admin tests use still builds.
    assert ibs.IndustryBenchmarkRecomputeIncomplete("fiscal", ["E"], {}).reason == "sectors failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_a_lossy_sector_leaves_the_scheduled_claim_unsettled(monkeypatch, mode):
    svc, _ = _svc(monkeypatch, _FMP(down=_SOFTWARE), _universe(_TECH_ENERGY))
    ledger = _ledger(monkeypatch)

    async def body():   # main.py's `_benchmarks` / `_ttm` closures
        service = ibs.get_industry_benchmark_service()
        fn = service.recompute_all if mode == "fiscal" else service.recompute_all_ttm
        return await fn(skip_if_fresh_hours=24)

    job = m.JOB_INDUSTRY_BENCHMARK_QUARTERLY if mode == "fiscal" else m.JOB_TTM_BENCHMARK_WEEKLY
    assert await m._run_claimed_phase(job, "phase", body) is False
    assert ledger["finished"]["success"] is False
    assert "lost too many companies to FMP fetch failures: Technology" in ledger["finished"]["error"]


def test_sector_loss_reads_failures_only():
    tally = ibs._FetchTally("fiscal")
    assert tally.sector_loss("Technology") is None                    # never seen
    c = tally.counts_for("Technology", "Funds")
    for _ in range(10):
        ibs._record_fetch_outcome(c, [], False)                         # 10 empty answers
    assert tally.sector_loss("Technology") is None
    ibs._record_fetch_outcome(tally.counts_for("Technology", "Funds"), ["rate_limited"], False)
    ibs._record_fetch_outcome(tally.counts_for("Technology", "Funds"), ["rate_limited"], False)
    # 2 failed of 12 (17%) — the empties count as tickers, never as losses.
    account = tally.sector_loss("Technology")
    assert account.startswith("2 of 12 tickers lost to transient fetch failures (17%: 2 rate-limited")
    assert "industries over the line" not in account                  # 2/12 per industry
    assert tally.sector_loss("Energy") is None
