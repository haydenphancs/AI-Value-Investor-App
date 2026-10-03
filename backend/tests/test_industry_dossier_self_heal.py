"""Industry dossier read path: a stored zero placeholder must not blank the
Moat card's TAM / CAGR for a whole quarter (TestFlight feedback, PLUG, 2026-09-28).

Production held 138 of 158 `industry_dossier` rows at the July "No public data
available" placeholder (TAM 0, CAGR NULL) because the July run had no FRED/Census
keys, and `get_or_compute_dossier` served every stored row as a hit. These tests
pin the repair:

  * a placeholder row is a MISS → live compute, merged over the stored row in
    memory (concentration kept), never written to Supabase;
  * a real row (US or Phase-B global) is returned untouched with no live call;
  * failure / timeout / unusable live results keep the placeholder, are logged,
    and are negatively memoized; concurrency is deduped and never hangs;
  * `_compute_one` labels a whole-sector FRED stand-in as grain 'sector';
  * `recompute_all` never replaces a Phase-B global row or a real TAM with a zero.

Hermetic: Supabase, FRED and Census are all faked; tier counters (not raising
stubs, which the live path would swallow) prove which calls happened.
"""

from __future__ import annotations

import asyncio
import logging
import math
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import app.services.industry_dossier_service as ids
import app.services.industry_tam_service as its
from app.services.industry_dossier_service import IndustryDossier, IndustryDossierService
from app.services.industry_tam_service import IndustryTAM

LOGGER = "app.services.industry_dossier_service"
PLUG_INDUSTRY = "Electrical Equipment & Parts"
PLACEHOLDER_LABEL = "No public data available — FRED/Census unreachable at compute time"


# ── Fakes ────────────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, sb: "_FakeSB") -> None:
        self.sb = sb
        self.op = "select"
        self.filters: Dict[str, Any] = {}
        self.payload: Any = None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.filters[col] = set(vals)
        return self

    def limit(self, _n):
        return self

    def _write(self, op, payload):
        self.op = op
        self.payload = payload
        return self

    def upsert(self, rows, on_conflict=None):
        return self._write("upsert", rows)

    def update(self, data):
        return self._write("update", data)

    def insert(self, rows):
        return self._write("insert", rows)

    def delete(self):
        return self._write("delete", None)

    def execute(self):
        if self.op != "select":
            self.sb.writes.append((self.op, self.payload, dict(self.filters)))
            return SimpleNamespace(data=[])
        if self.sb.fail_reads:
            raise RuntimeError("supabase unreachable")
        if self.sb.read_gate is not None:          # runs in sb_exec's worker thread
            self.sb.in_read.set()
            self.sb.read_gate.wait(5)
        self.sb.reads += 1
        rows = self.sb.rows
        for col, val in self.filters.items():
            if isinstance(val, set):
                rows = [r for r in rows if r.get(col) in val]
            else:
                rows = [r for r in rows if r.get(col) == val]
        return SimpleNamespace(data=[dict(r) for r in rows])


class _FakeSB:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = rows or []
        self.reads = 0
        self.writes: List[tuple] = []
        self.fail_reads = False
        self.read_gate = None      # threading.Event: block reads until set
        self.in_read = None        # threading.Event: set when a read is blocked

    def table(self, _name):
        return _FakeQuery(self)


def _row(industry: str = PLUG_INDUSTRY, **over) -> Dict[str, Any]:
    """A DB row shaped like production's July placeholder for PLUG's industry."""
    row = {
        "industry": industry, "sector": "Industrials",
        "current_tam_b": 0.0, "future_tam_b": 0.0,
        "current_year": "2026", "future_year": "2031",
        "cagr_5y_pct": None, "lifecycle_phase": "mature",
        "hhi": 1834.5, "top1_share_pct": 31.2, "top2_share_pct": 44.9,
        "concentration_label": "oligopoly", "constituent_count": 56,
        "source_grain": "all_industry", "source_label": PLACEHOLDER_LABEL,
        "tam_scope": "us",
    }
    row.update(over)
    return row


def _census_335() -> IndustryTAM:
    return IndustryTAM(
        current_tam=195.0, future_tam=270.9, current_year="2024", future_year="2029",
        source_label="US Census AIES — Electrical equipment, appliance, and component manufacturing (NAICS 335)",
        cagr_5y_pct=6.8,
    )


def _live(**over) -> IndustryDossier:
    kw = dict(
        current_tam=195.0, future_tam=270.9, current_year="2024", future_year="2029",
        source_label="US Census AIES (NAICS 335)", cagr_5y_pct=6.8,
        industry=PLUG_INDUSTRY, sector="Industrials",
        concentration_label="fragmented", source_grain="industry",
    )
    kw.update(over)
    return IndustryDossier(**kw)


class _Tiers:
    """Counting stubs for the three tier functions `_compute_one` calls through
    its module-level bindings."""

    def __init__(self, census=None, fred=None, series=None) -> None:
        self.census_ret, self.fred_ret, self.series_ret = census, fred, series
        self.calls: List[tuple] = []

    async def census(self, industry):
        self.calls.append(("census", industry))
        return self.census_ret

    async def fred(self, industry):
        self.calls.append(("fred", industry))
        return self.fred_ret

    async def series(self, series_id, source_label=None):
        self.calls.append(("series", series_id))
        return self.series_ret


@pytest.fixture(autouse=True)
def _reset_service_state():
    def _clear():
        IndustryDossierService._cache.clear()
        IndustryDossierService._heal_failed_at.clear()
        IndustryDossierService._live_inflight.clear()
        IndustryDossierService._heal_logged.clear()
    _clear()
    yield
    _clear()


@pytest.fixture
def sb(monkeypatch):
    fake = _FakeSB([_row()])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)
    return fake


def _install_tiers(monkeypatch, tiers: _Tiers) -> _Tiers:
    monkeypatch.setattr(ids, "_try_census_tam", tiers.census)
    monkeypatch.setattr(ids, "_try_fred_tam", tiers.fred)
    monkeypatch.setattr(ids, "fred_tam_for_series", tiers.series)
    return tiers


def _stub_compute(monkeypatch, fn):
    """Replace `_compute_one` for edge-value / concurrency cases."""
    monkeypatch.setattr(IndustryDossierService, "_compute_one", fn)


def _is_joining(task: "asyncio.Task") -> bool:
    """True once `task` is parked on another caller's live compute — walks the
    task's await chain to `_shared_live_compute` and checks it took the joiner
    branch. Deterministic, unlike sleeping and hoping."""
    coro = task.get_coro()
    while coro is not None:
        frame = getattr(coro, "cr_frame", None)
        if frame is not None and frame.f_code.co_name == "_shared_live_compute":
            return frame.f_locals.get("inflight") is not None
        coro = getattr(coro, "cr_await", None)
    return False


async def _until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


# ── The PLUG case ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_placeholder_row_heals_to_census_335_and_keeps_concentration(sb, monkeypatch, caplog):
    tiers = _install_tiers(monkeypatch, _Tiers(census=_census_335()))
    svc = IndustryDossierService()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        d = await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")

    assert d is not None
    assert d.current_tam == 195.0 and d.future_tam == 270.9
    assert (d.current_year, d.future_year) == ("2024", "2029")
    assert d.cagr_5y_pct == 6.8
    assert d.source_grain == "industry"
    assert d.tam_scope == "us"
    assert d.source_label.startswith("US Census AIES")
    # Concentration fields are the STORED row's (56 constituents), not the
    # live compute's no-constituents defaults.
    assert d.concentration_label == "oligopoly"
    assert d.constituent_count == 56
    assert d.hhi == 1834.5 and d.top1_share_pct == 31.2 and d.top2_share_pct == 44.9
    assert d.lifecycle_phase == "mature"
    assert tiers.calls == [("census", PLUG_INDUSTRY)]
    assert sb.writes == []                      # never written back
    assert "self-heal" in caplog.text and PLUG_INDUSTRY in caplog.text


@pytest.mark.asyncio
async def test_healed_result_is_memoized_and_the_cached_row_is_not_mutated(sb, monkeypatch):
    tiers = _install_tiers(monkeypatch, _Tiers(census=_census_335()))
    svc = IndustryDossierService()
    stored, _ = await svc._read_dossier(PLUG_INDUSTRY)
    first = await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    second = await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert first is second
    assert len(tiers.calls) == 1
    assert sb.reads == 1
    assert stored.current_tam == 0.0          # the cached placeholder object itself was not mutated


# ── End-to-end through the real tier functions (mapping + Census + FRED) ─


class _FakeCensus:
    def __init__(self, configured: bool) -> None:
        self.is_configured = configured
        self.naics_calls: List[str] = []

    async def get_industry_revenue_snapshot(self, naics):
        from app.integrations.census import CensusRevenueSnapshot

        self.naics_calls.append(naics)
        if naics != "335":
            return None
        return CensusRevenueSnapshot(
            revenue_usd=195_000_604_000.0, year=2024, naics="335",
            naics_label="Electrical equipment, appliance, and component manufacturing",
            revenue_usd_baseline=122_950_000_000.0, baseline_year=2017,
        )


class _FakeFRED:
    """Different data per series, so the test proves WHICH series was read."""

    is_configured = True
    SERIES = {
        # 2025 → 2018, live values (2026-10-01); FRED returns newest first.
        "USELCEQAPMANNGSP": [87100, 82300, 78500, 69600, 61200, 57700, 60100, 59751],
        "USMANNGSP": [2930097, 2882537, 2797562, 2668223, 2417675, 2156754, 2268790, 2261819],
    }

    def __init__(self) -> None:
        self.series_calls: List[str] = []

    async def get_observations(self, series_id, *, limit=8):
        self.series_calls.append(series_id)
        values = self.SERIES.get(series_id, [])
        return [
            SimpleNamespace(date=f"{2025 - i}-01-01", value=float(v))
            for i, v in enumerate(values[:limit])
        ]


@pytest.mark.asyncio
async def test_end_to_end_census_configured_uses_naics_335(sb, monkeypatch):
    census, fred = _FakeCensus(configured=True), _FakeFRED()
    monkeypatch.setattr(its, "get_census_client", lambda: census)
    monkeypatch.setattr(its, "get_fred_client", lambda: fred)
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert census.naics_calls == ["335"]
    assert fred.series_calls == []
    assert d.current_tam == 195.0
    assert d.cagr_5y_pct == pytest.approx(6.8, abs=0.05)   # 7-year 2017→2024
    assert d.source_grain == "industry"
    assert "NAICS 335" in d.source_label


@pytest.mark.asyncio
async def test_end_to_end_census_down_falls_back_to_bea_naics_335_not_all_manufacturing(sb, monkeypatch):
    census, fred = _FakeCensus(configured=False), _FakeFRED()
    monkeypatch.setattr(its, "get_census_client", lambda: census)
    monkeypatch.setattr(its, "get_fred_client", lambda: fred)
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert fred.series_calls == ["USELCEQAPMANNGSP"]
    assert d.current_tam == 87.1                 # not 2930.1 (all of US manufacturing)
    assert d.future_tam == pytest.approx(114.0, abs=0.1)
    assert d.cagr_5y_pct == 5.5                  # 2018→2025, not 8.6 from the 2020 trough
    assert d.source_grain == "industry"
    assert d.source_label.startswith("BEA ") and "via FRED" in d.source_label


# ── Rows that must be left alone ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    _row(current_tam_b=195.0, future_tam_b=270.9, cagr_5y_pct=6.8, source_grain="industry",
         source_label="US Census AIES (NAICS 335)", tam_scope="us"),
])
async def test_real_rows_are_returned_untouched_with_no_live_call(monkeypatch, row):
    fake = _FakeSB([row])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)
    tiers = _install_tiers(monkeypatch, _Tiers(census=_census_335(), fred=_census_335(), series=_census_335()))
    d = await IndustryDossierService().get_or_compute_dossier(row["industry"], row["sector"])
    assert d == IndustryDossier.from_db_row(row)
    assert tiers.calls == []
    assert fake.writes == []


# ── A grounded GLOBAL row is never served (Google Search grounding retired 2026-10-02) ──

_GROUNDED_GLOBAL = _row(
    industry="Semiconductors", sector="Technology", current_tam_b=702.44, future_tam_b=950.0,
    cagr_5y_pct=6.34, lifecycle_phase="secular_growth", source_grain="industry",
    source_label="SIA / WSTS global semiconductor sales", tam_scope="global",
)


@pytest.mark.asyncio
async def test_a_grounded_global_row_reads_as_a_placeholder_keeping_its_concentration(monkeypatch):
    fake = _FakeSB([dict(_GROUNDED_GLOBAL)])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)
    d = await IndustryDossierService().get_dossier("Semiconductors")
    assert (d.current_tam, d.future_tam, d.cagr_5y_pct) == (0.0, 0.0, None)
    assert d.tam_scope == "us" and d.lifecycle_phase == "mature"   # nothing grounded survives
    assert "SIA" not in d.source_label and "WSTS" not in d.source_label
    assert (d.hhi, d.concentration_label, d.constituent_count) == (1834.5, "oligopoly", 56)


@pytest.mark.asyncio
async def test_a_grounded_global_row_is_served_as_a_live_census_figure(monkeypatch):
    fake = _FakeSB([dict(_GROUNDED_GLOBAL)])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(industry=industry, sector=sector, current_tam=116.2,
                     source_label="US Census AIES (NAICS 3344)")
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier("Semiconductors", "Technology")
    assert d.current_tam == 116.2 and d.tam_scope == "us"
    assert d.source_label == "US Census AIES (NAICS 3344)"
    assert d.concentration_label == "oligopoly"          # the stored, ungrounded side is kept
    assert fake.writes == []                              # never persisted on the read path


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["us", None, "", "GLOBAL"])
async def test_only_the_exact_global_scope_is_withdrawn(monkeypatch, scope):
    """`tam_scope` has a CHECK constraint ('us' | 'global'); anything else read back is
    not a grounded row and is served as before."""
    fake = _FakeSB([dict(_GROUNDED_GLOBAL, tam_scope=scope)])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)
    d = await IndustryDossierService().get_dossier("Semiconductors")
    assert d.current_tam == 702.44


@pytest.mark.asyncio
async def test_zero_global_row_heals_as_us_scope(monkeypatch):
    fake = _FakeSB([_row(industry="Semiconductors", sector="Technology", tam_scope="global")])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(industry=industry, sector=sector)
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier("Semiconductors", "Technology")
    assert d.current_tam == 195.0
    assert d.tam_scope == "us"


# ── Failure paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_failure_keeps_placeholder_without_raising_and_memoizes(sb, monkeypatch, caplog):
    calls = {"n": 0}

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        calls["n"] += 1
        raise RuntimeError("FRED 503")
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        d = await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
        d2 = await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d.current_tam == 0.0 and d.source_label == PLACEHOLDER_LABEL
    assert d2.current_tam == 0.0
    assert calls["n"] == 1                       # memo: not retried inside the TTL
    assert PLUG_INDUSTRY in IndustryDossierService._heal_failed_at
    assert "FAILED" in caplog.text and PLUG_INDUSTRY in caplog.text and "RuntimeError" in caplog.text
    assert sb.writes == []


@pytest.mark.asyncio
async def test_live_timeout_keeps_placeholder_and_memoizes(sb, monkeypatch, caplog):
    monkeypatch.setattr(IndustryDossierService, "_LIVE_COMPUTE_TIMEOUT_SECONDS", 0.05)

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        await asyncio.sleep(5)
        return _live()
    _stub_compute(monkeypatch, compute)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        d = await asyncio.wait_for(
            IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"), 2,
        )
    assert d.current_tam == 0.0
    assert PLUG_INDUSTRY in IndustryDossierService._heal_failed_at
    assert "TIMED OUT" in caplog.text


@pytest.mark.asyncio
async def test_all_tiers_none_keeps_placeholder(sb, monkeypatch):
    tiers = _install_tiers(monkeypatch, _Tiers())   # every tier returns None
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d.current_tam == 0.0
    assert d.concentration_label == "oligopoly"
    assert ("census", PLUG_INDUSTRY) in tiers.calls
    assert PLUG_INDUSTRY in IndustryDossierService._heal_failed_at


_BAD = [0.0, -5.0, float("nan"), float("inf")]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["current_tam", "future_tam"])
@pytest.mark.parametrize("bad", _BAD)
async def test_unusable_live_tam_is_rejected(sb, monkeypatch, field, bad):
    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(**{field: bad})
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d.current_tam == 0.0                  # the stored placeholder, not the bad value
    assert d.source_label == PLACEHOLDER_LABEL
    assert PLUG_INDUSTRY in IndustryDossierService._heal_failed_at


@pytest.mark.asyncio
async def test_nan_live_cagr_becomes_none_but_tam_heals(sb, monkeypatch):
    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(cagr_5y_pct=float("nan"))
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d.current_tam == 195.0
    assert d.cagr_5y_pct is None
    assert d.lifecycle_phase == "mature"


@pytest.mark.asyncio
async def test_negative_live_cagr_is_kept_and_lifecycle_declines(sb, monkeypatch):
    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(cagr_5y_pct=-3.2)
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d.cagr_5y_pct == -3.2
    assert d.lifecycle_phase == "declining"


@pytest.mark.asyncio
async def test_small_constituent_count_stays_emerging(monkeypatch):
    fake = _FakeSB([_row(constituent_count=3, concentration_label="fragmented")])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(cagr_5y_pct=20.0)
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d.lifecycle_phase == "emerging"


@pytest.mark.asyncio
async def test_failed_heal_is_retried_after_the_ttl(sb, monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(ids, "time", SimpleNamespace(time=lambda: clock["t"]))
    outcomes = [RuntimeError("down"), _live()]
    calls = {"n": 0}

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        calls["n"] += 1
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    assert (await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")).current_tam == 0.0
    clock["t"] += IndustryDossierService._CACHE_TTL_SECONDS - 1
    assert (await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")).current_tam == 0.0
    assert calls["n"] == 1
    clock["t"] += 2
    assert (await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")).current_tam == 195.0
    assert calls["n"] == 2


# ── Concurrency ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_live_compute(sb, monkeypatch):
    gate = asyncio.Event()
    calls = {"n": 0}

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        calls["n"] += 1
        await gate.wait()
        return _live()
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    tasks = [asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")) for _ in range(5)]
    await _until(lambda: calls["n"] == 1 and sum(_is_joining(t) for t in tasks) == 4)
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert calls["n"] == 1
    assert all(r.current_tam == 195.0 and r.concentration_label == "oligopoly" for r in results)
    assert IndustryDossierService._live_inflight == {}


@pytest.mark.asyncio
async def test_cancelled_leader_does_not_hang_joiners_or_memoize(sb, monkeypatch):
    started = asyncio.Event()

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        started.set()
        await asyncio.sleep(30)
        return _live()
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    leader = asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    await asyncio.wait_for(started.wait(), 1)
    joiner = asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    await _until(lambda: _is_joining(joiner))
    leader.cancel()
    d = await asyncio.wait_for(joiner, 1)
    assert d.current_tam == 0.0                  # placeholder, promptly
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert IndustryDossierService._heal_failed_at == {}   # a cancel says nothing about FRED
    assert IndustryDossierService._live_inflight == {}


@pytest.mark.asyncio
async def test_reset_during_an_inflight_heal_does_not_recache_stale_data(sb, monkeypatch):
    gate = asyncio.Event()

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        await gate.wait()
        return _live()
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    task = asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    await _until(lambda: bool(IndustryDossierService._live_inflight))
    IndustryDossierService.reset_cache()        # a recompute just wrote fresh rows
    gate.set()
    d = await asyncio.wait_for(task, 2)
    assert d.current_tam == 195.0               # this request still gets its figure
    assert PLUG_INDUSTRY not in IndustryDossierService._cache


# ── Missing row / read failure ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_row_live_result_has_no_concentration(monkeypatch):
    fake = _FakeSB([])
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(industry=industry, sector=sector, concentration_label="fragmented")
    _stub_compute(monkeypatch, compute)
    d = await IndustryDossierService().get_or_compute_dossier("Brand New Industry", "Industrials")
    assert d.current_tam == 195.0
    assert d.concentration_label is None        # never the "fragmented" default
    assert fake.writes == []


@pytest.mark.asyncio
async def test_missing_row_unusable_live_result_returns_none(monkeypatch):
    monkeypatch.setattr(ids, "get_supabase", lambda: _FakeSB([]))

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(current_tam=0.0, future_tam=0.0)
    _stub_compute(monkeypatch, compute)
    assert await IndustryDossierService().get_or_compute_dossier("Brand New Industry", "Industrials") is None


@pytest.mark.asyncio
async def test_supabase_read_failure_returns_none_without_a_live_compute(sb, monkeypatch, caplog):
    sb.fail_reads = True
    tiers = _install_tiers(monkeypatch, _Tiers(census=_census_335()))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        d = await IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert d is None
    assert tiers.calls == []
    assert "read failed" in caplog.text


# ── Grain in _compute_one ────────────────────────────────────────────────


def _tam(label="x") -> IndustryTAM:
    return IndustryTAM(current_tam=100.0, future_tam=120.0, current_year="2025",
                       future_year="2030", source_label=label, cagr_5y_pct=4.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("industry,expected", [
    ("Computer Hardware", "sector"),            # → all of US manufacturing GDP
    ("Banks - Regional", "sector"),             # → all of finance & insurance GDP
    ("Restaurants", "industry"),                # → NAICS 722 itself
    (PLUG_INDUSTRY, "industry"),                # → NAICS 335 itself
])
async def test_compute_one_grain_for_fred_mapped_industries(monkeypatch, industry, expected):
    _install_tiers(monkeypatch, _Tiers(census=None, fred=_tam()))
    d = await IndustryDossierService()._compute_one(industry, "Industrials", [], {})
    assert d.source_grain == expected


@pytest.mark.asyncio
async def test_compute_one_census_is_industry_grain(monkeypatch):
    tiers = _install_tiers(monkeypatch, _Tiers(census=_census_335(), fred=_tam()))
    d = await IndustryDossierService()._compute_one(PLUG_INDUSTRY, "Industrials", [], {})
    assert d.source_grain == "industry"
    assert d.current_tam == 195.0
    assert ("fred", PLUG_INDUSTRY) not in tiers.calls


def test_grain_allow_list_and_new_mapping_are_consistent():
    assert its.FRED_SERIES_MATCHES_INDUSTRY <= set(its.INDUSTRY_TO_FRED_SERIES)
    assert its.INDUSTRY_TO_CENSUS[PLUG_INDUSTRY] == "335"
    assert its.INDUSTRY_TO_FRED_SERIES[PLUG_INDUSTRY] == "USELCEQAPMANNGSP"
    label = its._fred_source_label("USELCEQAPMANNGSP")
    # Migration 188's purge recognises a Phase A (US) source by these exact markers.
    assert label.startswith("BEA ") and "via FRED" in label
    assert its.fred_mapping_grain("Computer Hardware") == "sector"
    assert its.fred_mapping_grain(PLUG_INDUSTRY) == "industry"


# ── recompute_all: real TAMs are never zeroed; grounded global rows are replaced ──


@pytest.mark.asyncio
async def test_recompute_all_replaces_global_rows_and_never_zeroes_a_real_tam(monkeypatch):
    import app.integrations.census as census_mod
    import app.integrations.fred as fred_mod

    existing = [
        _row(industry="Semiconductors", sector="Technology", current_tam_b=702.44,
             source_label="SIA / WSTS", tam_scope="global", source_grain="industry"),
        _row(industry="Restaurants", sector="Consumer Cyclical", current_tam_b=708.9,
             source_label="BEA Food Services GDP (via FRED)", tam_scope="us",
             source_grain="industry"),
        _row(),  # PLUG's industry, the zero placeholder
    ]
    fake = _FakeSB(existing)
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)
    monkeypatch.setattr(ids, "_load_universe", lambda: [
        {"industry": "Semiconductors", "sector": "Technology", "tickers": []},
        {"industry": "Restaurants", "sector": "Consumer Cyclical", "tickers": []},
        {"industry": PLUG_INDUSTRY, "sector": "Industrials", "tickers": []},
    ])
    monkeypatch.setattr(fred_mod, "get_fred_client", lambda: SimpleNamespace(is_configured=True))
    monkeypatch.setattr(census_mod, "get_census_client", lambda: SimpleNamespace(is_configured=True))

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        if industry == "Restaurants":            # a transient miss this run
            return _live(industry=industry, sector=sector, current_tam=0.0, future_tam=0.0,
                         source_label=PLACEHOLDER_LABEL, source_grain="all_industry")
        if industry == "Semiconductors":         # Phase A's US figure replaces the grounded one
            return _live(industry=industry, sector=sector, current_tam=116.2,
                         source_label="US Census AIES (NAICS 3344)")
        return _live(industry=industry, sector=sector)
    _stub_compute(monkeypatch, compute)

    result = await IndustryDossierService().recompute_all()

    upserted = [row for op, batch, _f in fake.writes if op == "upsert" for row in batch]
    by_industry = {r["industry"]: r for r in upserted}
    assert by_industry["Semiconductors"]["current_tam_b"] == 116.2   # grounded row replaced
    assert by_industry["Semiconductors"]["tam_scope"] == "us"
    assert "Restaurants" not in by_industry             # real TAM never zeroed
    assert by_industry[PLUG_INDUSTRY]["current_tam_b"] == 195.0   # zero → real is written
    assert result["rows_upserted"] == 2
    assert "phase_b_summary" not in result
    assert not any(math.isnan(r["current_tam_b"]) for r in upserted)


# ── Hardening (review 2026-10-01) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_joiner_does_not_cancel_the_shared_compute(sb, monkeypatch):
    """`asyncio.shield`: one caller giving up must not cancel the live compute
    the leader and the other joiners are waiting on."""
    gate = asyncio.Event()
    calls = {"n": 0}

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        calls["n"] += 1
        await gate.wait()
        return _live()
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    leader = asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    await _until(lambda: calls["n"] == 1)
    quitter = asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    stayer = asyncio.create_task(svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    await _until(lambda: _is_joining(quitter) and _is_joining(stayer))
    quitter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await quitter
    gate.set()
    a, b = await asyncio.wait_for(asyncio.gather(leader, stayer), 2)
    assert a.current_tam == 195.0 and b.current_tam == 195.0
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_missing_row_failure_is_negatively_memoized(monkeypatch):
    monkeypatch.setattr(ids, "get_supabase", lambda: _FakeSB([]))
    calls = {"n": 0}

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        calls["n"] += 1
        raise RuntimeError("FRED down")
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    assert await svc.get_or_compute_dossier("Brand New Industry", "Industrials") is None
    assert await svc.get_or_compute_dossier("Brand New Industry", "Industrials") is None
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_reset_clears_the_heal_log_so_a_post_recompute_heal_logs_again(sb, monkeypatch, caplog):
    """The self-heal WARNING promises "if this keeps appearing after a recompute,
    it did not write" — so a reset (what recompute_all ends with) must re-arm it."""
    _install_tiers(monkeypatch, _Tiers(census=_census_335()))
    svc = IndustryDossierService()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
        IndustryDossierService.reset_cache()
        await svc.get_or_compute_dossier(PLUG_INDUSTRY, "Industrials")
    assert caplog.text.count("self-heal") == 2


@pytest.mark.asyncio
async def test_reset_during_the_supabase_read_caches_neither_placeholder_nor_heal(sb, monkeypatch):
    import threading

    sb.read_gate, sb.in_read = threading.Event(), threading.Event()

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live()
    _stub_compute(monkeypatch, compute)
    task = asyncio.create_task(IndustryDossierService().get_or_compute_dossier(PLUG_INDUSTRY, "Industrials"))
    await _until(sb.in_read.is_set)
    IndustryDossierService.reset_cache()       # a recompute wrote a row meanwhile
    sb.read_gate.set()
    d = await asyncio.wait_for(task, 2)
    assert d.current_tam == 195.0              # this request still gets a figure
    assert PLUG_INDUSTRY not in IndustryDossierService._cache


# ── The transient flag: a momentary hole must not be shared-cached ──────


async def _status(svc=None):
    return await (svc or IndustryDossierService()).get_or_compute_dossier_with_status(PLUG_INDUSTRY, "Industrials")


@pytest.mark.asyncio
async def test_status_real_row_and_successful_heal_are_not_transient(sb, monkeypatch):
    _install_tiers(monkeypatch, _Tiers(census=_census_335()))
    d, transient = await _status()
    assert d.current_tam == 195.0 and transient is False


@pytest.mark.asyncio
async def test_status_read_failure_is_transient(sb, monkeypatch):
    sb.fail_reads = True
    assert await _status() == (None, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "timeout"])
async def test_status_failed_live_compute_is_transient_and_memo_keeps_it(sb, monkeypatch, mode):
    monkeypatch.setattr(IndustryDossierService, "_LIVE_COMPUTE_TIMEOUT_SECONDS", 0.05)

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        if mode == "raise":
            raise RuntimeError("boom")
        await asyncio.sleep(5)
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    d, transient = await _status(svc)
    assert d.current_tam == 0.0 and transient is True
    d2, transient2 = await _status(svc)          # memo hit
    assert transient2 is True


@pytest.mark.asyncio
@pytest.mark.parametrize("fred_configured,expected", [(True, True), (False, False)])
async def test_status_every_fred_tier_failing_is_transient_only_when_fred_is_configured(
    sb, monkeypatch, fred_configured, expected,
):
    """`_compute_one` synthesizes the TAM-0 placeholder only when every FRED tier
    failed: an outage when FRED is configured, a permanent state when it is not
    (and then keeping every report out of the shared caches would be a cost bomb)."""
    import app.integrations.fred as fred_mod

    _install_tiers(monkeypatch, _Tiers())        # every tier → None → placeholder
    monkeypatch.setattr(fred_mod, "get_fred_client", lambda: SimpleNamespace(is_configured=fred_configured))
    d, transient = await _status()
    assert d.current_tam == 0.0
    assert transient is expected


@pytest.mark.asyncio
async def test_status_unusable_but_complete_answer_is_not_transient(sb, monkeypatch):
    async def compute(self, industry, sector, tickers, caps_by_ticker):
        return _live(future_tam=0.0)              # an answer, just not a usable pair
    _stub_compute(monkeypatch, compute)
    d, transient = await _status()
    assert d.current_tam == 0.0 and transient is False


@pytest.mark.asyncio
async def test_status_joiner_of_a_cancelled_leader_is_transient(sb, monkeypatch):
    started = asyncio.Event()

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        started.set()
        await asyncio.sleep(30)
    _stub_compute(monkeypatch, compute)
    svc = IndustryDossierService()
    leader = asyncio.create_task(_status(svc))
    await asyncio.wait_for(started.wait(), 1)
    joiner = asyncio.create_task(_status(svc))
    await _until(lambda: _is_joining(joiner))
    leader.cancel()
    d, transient = await asyncio.wait_for(joiner, 1)
    assert d.current_tam == 0.0 and transient is True
    with pytest.raises(asyncio.CancelledError):
        await leader


@pytest.mark.asyncio
async def test_census_blip_during_a_heal_is_transient_not_a_broader_figure(sb, monkeypatch):
    """A Census 5xx must not fall through to a broader FRED series (which the card
    would hide) — the heal fails, transient, and retries after the memo."""
    from app.integrations.census import CensusUnavailableException

    async def census(industry):
        raise CensusUnavailableException("Census HTTP 503")
    tiers = _install_tiers(monkeypatch, _Tiers(fred=_tam("BEA (via FRED)")))
    monkeypatch.setattr(ids, "_try_census_tam", census)
    d, transient = await _status()
    assert d.current_tam == 0.0 and transient is True
    assert ("fred", PLUG_INDUSTRY) not in tiers.calls


# ── The collector side: transient → degraded_sections (not shared-cached) ─


def _collector_out():
    return SimpleNamespace(industry_tam="unset", degraded_sections=[])


@pytest.mark.parametrize("result,tagged", [
    ((None, True), True),                          # read failure / timeout
    ((_live(current_tam=0.0, future_tam=0.0), True), True),
    ((_live(), False), False),                     # a real figure
    ((None, False), False),                        # a permanent gap
    (RuntimeError("gather leg raised"), True),
])
def test_collector_settles_the_dossier_and_tags_only_transient_holes(result, tagged):
    from app.services.agents.ticker_report_data_collector import (
        _INDUSTRY_TAM_DEGRADED, _settle_industry_tam,
    )

    out = _collector_out()
    _settle_industry_tam(out, result, "PLUG", PLUG_INDUSTRY)
    assert (out.degraded_sections == [_INDUSTRY_TAM_DEGRADED]) is tagged
    if isinstance(result, tuple):
        assert out.industry_tam is result[0]
    else:
        assert out.industry_tam is None


# ── recompute_all: the rest of its guards ────────────────────────────────


async def _run_recompute(monkeypatch, existing, universe, compute_by_industry):
    import app.integrations.census as census_mod
    import app.integrations.fred as fred_mod

    fake = _FakeSB(existing)
    monkeypatch.setattr(ids, "get_supabase", lambda: fake)
    monkeypatch.setattr(ids, "_load_universe", lambda: universe)
    monkeypatch.setattr(fred_mod, "get_fred_client", lambda: SimpleNamespace(is_configured=True))
    monkeypatch.setattr(census_mod, "get_census_client", lambda: SimpleNamespace(is_configured=True))

    async def compute(self, industry, sector, tickers, caps_by_ticker):
        out = compute_by_industry[industry]
        if isinstance(out, BaseException):
            raise out
        return out
    _stub_compute(monkeypatch, compute)
    result = await IndustryDossierService().recompute_all()
    upserted = {r["industry"]: r for op, batch, _f in fake.writes if op == "upsert" for r in batch}
    updates = [(payload, f) for op, payload, f in fake.writes if op == "update"]
    return result, upserted, updates


def _u(industry, sector="Technology"):
    return {"industry": industry, "sector": sector, "tickers": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("computed, expect_tam", [
    # A real Census figure replaces it, concentration and all, in ONE upsert.
    (_live(industry="Semiconductors", sector="Technology", current_tam=116.2,
           source_label="US Census AIES (NAICS 3344)", hhi=2400.0, constituent_count=42,
           concentration_label="oligopoly"), 116.2),
    # THE TRAP: Phase A resolving only a placeholder must STILL replace it. The zero-guard
    # protects a REAL figure; a grounded one is not real, and keeping it is the storage
    # the Grounding terms forbid.
    (_live(industry="Semiconductors", sector="Technology", current_tam=0.0, future_tam=0.0,
           source_label=PLACEHOLDER_LABEL, source_grain="all_industry"), 0.0),
    # ...and so must a broader (sector-grain) fallback: the grain guard protects only a
    # real industry-grain figure.
    (_live(industry="Semiconductors", sector="Technology", current_tam=3000.0,
           source_grain="sector", source_label="BEA Manufacturing GDP — broader than Semiconductors"),
     3000.0),
])
async def test_recompute_replaces_a_grounded_global_row_whatever_phase_a_resolves(
    monkeypatch, computed, expect_tam,
):
    existing = [_row(industry="Semiconductors", sector="Technology", current_tam_b=702.44,
                     source_label="SIA / WSTS", tam_scope="global", source_grain="industry")]
    _r, upserted, updates = await _run_recompute(
        monkeypatch, existing, [_u("Semiconductors")], {"Semiconductors": computed},
    )
    assert upserted["Semiconductors"]["current_tam_b"] == expect_tam
    assert upserted["Semiconductors"]["tam_scope"] == "us"
    assert updates == []                     # no concentration-only side write any more


@pytest.mark.asyncio
async def test_recompute_protects_no_global_row(monkeypatch):
    existing = [
        _row(industry="Semiconductors", sector="Technology", current_tam_b=0.0, tam_scope="global"),
        _row(industry="Uncurated Global Thing", sector="Technology", current_tam_b=500.0,
             tam_scope="global", source_label="Some research", source_grain="industry"),
    ]
    _r, upserted, _upd = await _run_recompute(
        monkeypatch, existing,
        [_u("Semiconductors"), _u("Uncurated Global Thing")],
        {"Semiconductors": _live(industry="Semiconductors", sector="Technology"),
         "Uncurated Global Thing": _live(industry="Uncurated Global Thing", sector="Technology")},
    )
    assert set(upserted) == {"Semiconductors", "Uncurated Global Thing"}


@pytest.mark.asyncio
async def test_recompute_keeps_an_industry_level_row_when_the_run_fell_back_to_a_broader_source(monkeypatch, caplog):
    existing = [_row(current_tam_b=195.0, source_grain="industry",
                     source_label="US Census AIES (NAICS 335)")]
    broader = _live(current_tam=2930.1, future_tam=3980.7, source_grain="sector",
                    source_label="BEA Manufacturing GDP — broader than Electrical Equipment & Parts")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _r, upserted, _upd = await _run_recompute(
            monkeypatch, existing, [_u(PLUG_INDUSTRY, "Industrials")], {PLUG_INDUSTRY: broader},
        )
    assert upserted == {}
    assert "fell back to a broader source" in caplog.text


@pytest.mark.asyncio
async def test_recompute_writes_a_grain_change_for_an_industry_not_mapped_to_industry_grain(monkeypatch):
    """A row stored at 'industry' grain for an industry that is no longer mapped to
    an industry-grain source (e.g. dropped from the allow-list) is a real change."""
    existing = [_row(industry="Construction", sector="Industrials", current_tam_b=1340.0,
                     source_grain="industry", source_label="BEA Construction GDP (via FRED)")]
    now_sector = _live(industry="Construction", sector="Industrials", current_tam=1340.0,
                       source_grain="sector", source_label="BEA Construction GDP (via FRED)")
    _r, upserted, _upd = await _run_recompute(
        monkeypatch, existing, [_u("Construction", "Industrials")], {"Construction": now_sector},
    )
    assert upserted["Construction"]["source_grain"] == "sector"


@pytest.mark.asyncio
async def test_recompute_keeps_the_stored_row_when_census_is_unavailable(monkeypatch, caplog):
    from app.integrations.census import CensusUnavailableException

    existing = [_row(current_tam_b=195.0, source_grain="industry")]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _r, upserted, _upd = await _run_recompute(
            monkeypatch, existing, [_u(PLUG_INDUSTRY, "Industrials")],
            {PLUG_INDUSTRY: CensusUnavailableException("Census HTTP 503")},
        )
    assert upserted == {}
    assert "Census unavailable" in caplog.text


def test_construction_industries_are_not_industry_grain():
    # "Construction Materials" left this list 2026-10-01: it now maps to NAICS 327
    # (Census, and BEA USNMMPMANNGSP), not to construction GDP — pinned in
    # test_industry_tam_narrow_sources.py.
    for industry in ("Construction", "Engineering & Construction", "Residential Construction"):
        assert its.fred_mapping_grain(industry) == "sector", industry
    assert its.expects_industry_grain(PLUG_INDUSTRY)
    assert its.expects_industry_grain("Restaurants")
    assert not its.expects_industry_grain("Construction")
