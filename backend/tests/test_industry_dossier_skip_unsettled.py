"""A dossier run that cannot start must leave the quarterly claim UNSETTLED.

Found 2026-10-01 during the PLUG TAM/CAGR fix: `IndustryDossierService.recompute_all`
RETURNED `{"status": "skipped"}` on an empty universe (`industry_universe.json` is not in
git, so a failed Supabase Storage download on Railway yields `[]`) and when neither FRED
nor Census was configured. `_run_claimed_phase` (main.py) treats any phase that returns as
settled, so the day-keyed claim recorded success, the quarter was consumed, and the
dossier stayed stale/zero until the next quarter — while the read path's in-memory
self-heal of zero rows hid it from every report.

Now both branches log at ERROR and raise `IndustryDossierRecomputeSkipped`, which the
claim helper records as a failure and the chain retries inside its catch-up window.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.integrations.census as census_mod
import app.integrations.fred as fred_mod
import app.services.industry_dossier_service as ids
import app.services.industry_override_service as ovr_mod
import app.services.notification_jobs as nj
from app import main as m
from app.services.industry_dossier_service import (
    IndustryDossier,
    IndustryDossierRecomputeSkipped,
    IndustryDossierService,
)

_UNIVERSE = [{"industry": "Restaurants", "sector": "Consumer Cyclical", "tickers": ["MCD"]}]


class _Query:
    def __init__(self, sb: "_RecordingSB", table: str) -> None:
        self._sb, self._table, self._op = sb, table, "select"

    def select(self, *_a, **_k):
        return self

    def upsert(self, batch, **_k):
        self._op = "upsert"
        self._sb.writes.append((self._table, list(batch)))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _RecordingSB:
    def __init__(self) -> None:
        self.tables: List[str] = []
        self.writes: List[Any] = []

    def table(self, name: str) -> _Query:
        self.tables.append(name)
        return _Query(self, name)


class _PhaseB:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh_all_overrides(self, **_kwargs):  # recompute_all passes phase_a_baseline=
        self.calls += 1
        return {"status": "stubbed"}


def _install(monkeypatch, *, universe, fred: bool, census: bool):
    """Wire every seam `recompute_all` touches; return the recorders."""
    sb = _RecordingSB()
    phase_b = _PhaseB()
    computed: List[str] = []

    monkeypatch.setattr(ids, "_load_universe", lambda: list(universe))
    monkeypatch.setattr(ids, "get_supabase", lambda: sb)
    monkeypatch.setattr(fred_mod, "get_fred_client", lambda: SimpleNamespace(is_configured=fred))
    monkeypatch.setattr(census_mod, "get_census_client", lambda: SimpleNamespace(is_configured=census))
    monkeypatch.setattr(ovr_mod, "get_industry_override_service", lambda: phase_b)

    async def _compute(self, industry, sector, tickers, caps_by_ticker):
        computed.append(industry)
        return IndustryDossier(
            current_tam=708.9, future_tam=900.0, current_year="2024", future_year="2029",
            source_label="BEA Food Services GDP (via FRED)", cagr_5y_pct=4.9,
            industry=industry, sector=sector,
        )

    monkeypatch.setattr(IndustryDossierService, "_compute_one", _compute)
    return sb, phase_b, computed


def _skip_errors(caplog) -> List[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == ids.__name__ and r.levelno >= logging.ERROR
        and "recompute SKIPPED" in r.getMessage()
    ]


# ── The service raises, logs at ERROR, and touches nothing ───────────────


@pytest.mark.asyncio
async def test_an_empty_universe_raises_logs_and_writes_nothing(monkeypatch, caplog):
    sb, phase_b, computed = _install(monkeypatch, universe=[], fred=True, census=True)

    with caplog.at_level(logging.ERROR, logger=ids.__name__):
        with pytest.raises(IndustryDossierRecomputeSkipped) as info:
            await IndustryDossierService().recompute_all()

    assert info.value.reason == "empty universe"
    assert "industry_universe.json" in str(info.value)
    assert len(_skip_errors(caplog)) == 1
    assert sb.tables == [] and sb.writes == []      # no pre-read, no upsert
    assert computed == []
    assert phase_b.calls == 0                        # no paid Gemini pass on a run that never started


@pytest.mark.asyncio
async def test_no_upstream_credentials_raises_logs_and_writes_nothing(monkeypatch, caplog):
    sb, phase_b, computed = _install(monkeypatch, universe=_UNIVERSE, fred=False, census=False)

    with caplog.at_level(logging.ERROR, logger=ids.__name__):
        with pytest.raises(IndustryDossierRecomputeSkipped) as info:
            await IndustryDossierService().recompute_all(force=True)

    assert info.value.reason == "no upstream credentials"
    assert "FRED_API_KEY" in str(info.value) and "CENSUS_API_KEY" in str(info.value)
    assert len(_skip_errors(caplog)) == 1
    assert sb.tables == [] and sb.writes == []
    assert computed == []                            # the zero-placeholder sweep never ran
    assert phase_b.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fred,census", [(True, False), (False, True)])
async def test_one_configured_upstream_is_enough_to_run(monkeypatch, caplog, fred, census):
    """The boundary: the credential gate is AND-of-missing, not OR — one key still runs."""
    sb, phase_b, computed = _install(monkeypatch, universe=_UNIVERSE, fred=fred, census=census)

    with caplog.at_level(logging.ERROR, logger=ids.__name__):
        result = await IndustryDossierService().recompute_all()

    assert result["status"] == "ok"
    assert result["rows_upserted"] == 1
    assert computed == ["Restaurants"]
    assert [t for t, _ in sb.writes] == ["industry_dossier"]
    assert phase_b.calls == 1
    assert _skip_errors(caplog) == []


def test_the_skip_exception_is_a_typed_runtime_error_carrying_its_reason():
    exc = IndustryDossierRecomputeSkipped("empty universe", "detail")
    assert isinstance(exc, RuntimeError)
    assert exc.reason == "empty universe"
    assert str(exc).startswith("industry_dossier recompute SKIPPED (empty universe)")


# ── Through the REAL claim helper: the quarter is not consumed ───────────


def _real_claim_ledger(monkeypatch) -> Dict[str, Any]:
    """Keep `claimed_scheduled_job` real; stub only its two ledger RPCs."""
    ledger: Dict[str, Any] = {}

    def _claim(job, *, timezone_name="UTC", now=None, stale_seconds=None):
        ledger["claimed"] = (job, stale_seconds)
        return True

    def _finish(job, *, success, items=0, error=None, timezone_name="UTC", now=None):
        ledger["finished"] = {"job": job, "success": success, "error": error}

    monkeypatch.setattr(nj, "claim_scheduled", _claim)
    monkeypatch.setattr(nj, "finish_scheduled", _finish)
    return ledger


async def _dossier_body():
    # Same shape as main.py's `_dossier` closure.
    return await ids.get_industry_dossier_service().recompute_all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "universe,configured,reason",
    [([], True, "empty universe"), (_UNIVERSE, False, "no upstream credentials")],
)
async def test_a_skipped_dossier_run_leaves_the_quarterly_claim_unsettled(
    monkeypatch, universe, configured, reason,
):
    _install(monkeypatch, universe=universe, fred=configured, census=configured)
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(
        m.JOB_INDUSTRY_DOSSIER_QUARTERLY, "Industry dossier job", _dossier_body,
    )

    assert settled is False, "a skipped run must be retried, not settle the quarter"
    assert ledger["claimed"] == (m.JOB_INDUSTRY_DOSSIER_QUARTERLY, m._CHAIN_PHASE_STALE_SECONDS)
    finished = ledger["finished"]
    assert finished["job"] == m.JOB_INDUSTRY_DOSSIER_QUARTERLY
    assert finished["success"] is False              # run_day stays unset → the day is retried
    assert finished["error"].startswith("IndustryDossierRecomputeSkipped:")
    assert f"({reason})" in finished["error"]       # greppable in the ledger row


@pytest.mark.asyncio
async def test_a_real_dossier_run_still_settles_the_claim(monkeypatch):
    """Mutation twin: the fix must not make a healthy run look unsettled."""
    _install(monkeypatch, universe=_UNIVERSE, fred=True, census=False)
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(
        m.JOB_INDUSTRY_DOSSIER_QUARTERLY, "Industry dossier job", _dossier_body,
    )

    assert settled is True
    assert ledger["finished"]["success"] is True
    assert ledger["finished"]["error"] is None


@pytest.fixture(autouse=True)
def _fresh_service_state():
    IndustryDossierService.reset_cache()
    yield
    IndustryDossierService.reset_cache()
