"""The 55-ticker fallback must never reach an upsert.

`sp500-constituent` is a BLOCKED path under the current FMP entitlement, and
`FMPClient.get_sp500_constituents` swallows the refusal into `[]` — a warning, no exception.
`_get_sector_tickers` then returned `_FALLBACK_SECTOR_TICKERS`: 5 tickers x 11 sectors = 55,
against `MIN_SAMPLE_SIZE = 5`. Every sector therefore cleared the sample gate at EXACTLY the
boundary and upserted a 5-company median over the ~5,700-company rows that
`industry_benchmark_service` writes from `benchmark_universe.json`.

Thirteen services read `sector_benchmarks` — moat scoring, health check, the valuation and
growth snapshots, `stock_overview_service`, the AI report collector. And the trigger,
`POST /admin/refresh-sector-benchmarks`, dispatches via `asyncio.create_task`, so the caller
saw `200 {"status": "started"}` while the table was quietly degraded.

Hermetic: no network, no Supabase.
"""
from __future__ import annotations

import inspect

import pytest

from app.integrations.fmp import FMPUnavailableException
from app.services import sector_benchmark_service as sbs
from app.services.sector_benchmark_service import (
    CANONICAL_SECTORS,
    MIN_SAMPLE_SIZE,
    _FALLBACK_SECTOR_TICKERS,
    SectorBenchmarkService,
)


class _BlockedFMP:
    """Stands in for the real client: `sp500-constituent` is blocked, and the wrapper
    swallows that into an empty list rather than raising."""

    def __init__(self):
        self.calls = 0

    async def get_sp500_constituents(self):
        self.calls += 1
        return []


def _svc(monkeypatch, fmp):
    monkeypatch.setattr(sbs, "get_supabase", lambda: pytest.fail(
        "Supabase was touched — the degraded path must refuse BEFORE any write"
    ), raising=True)
    monkeypatch.setattr(sbs, "get_fmp_client", lambda: fmp, raising=True)
    s = SectorBenchmarkService.__new__(SectorBenchmarkService)
    s.fmp = fmp
    return s


# ── the arithmetic that made this dangerous ──────────────────────────────────

def test_the_fallback_is_exactly_at_the_sample_gate():
    """Pinning WHY this was invisible: every fallback sector has exactly MIN_SAMPLE_SIZE
    companies, so `len(values) < MIN_SAMPLE_SIZE` is False for all of them and nothing
    downstream rejects the median as thin."""
    sizes = {s: len(t) for s, t in _FALLBACK_SECTOR_TICKERS.items()}
    assert set(sizes.values()) == {MIN_SAMPLE_SIZE}, sizes
    assert sum(sizes.values()) == 55


# ── the guard ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_empty_constituent_list_refuses_instead_of_falling_back(monkeypatch):
    fmp = _BlockedFMP()
    svc = _svc(monkeypatch, fmp)
    with pytest.raises(FMPUnavailableException) as ei:
        await svc._get_sector_tickers()
    assert fmp.calls == 1
    msg = str(ei.value)
    assert "sp500-constituent" in msg
    assert "55" in msg, "the message should name the size of what it refused"


@pytest.mark.asyncio
async def test_compute_all_benchmarks_aborts_before_any_write(monkeypatch):
    """The end-to-end property: the degraded path cannot reach an upsert. `_svc` fails the
    test if `get_supabase` is called at all."""
    svc = _svc(monkeypatch, _BlockedFMP())
    monkeypatch.setattr(svc, "_benchmarks_are_fresh", lambda: False, raising=False)
    with pytest.raises(FMPUnavailableException):
        await svc.compute_all_benchmarks(force=True)


@pytest.mark.asyncio
async def test_real_constituents_still_compute(monkeypatch):
    """The refusal must not break the path it protects — if the endpoint is ever bought
    back, grouping still works."""
    class _OK(_BlockedFMP):
        async def get_sp500_constituents(self):
            self.calls += 1
            return [{"symbol": f"T{i}", "sector": "Technology"} for i in range(30)]

    svc = _svc(monkeypatch, _OK())
    grouped = await svc._get_sector_tickers()
    assert grouped["Technology"] and len(grouped["Technology"]) == 30


# ── the table is still needed for its NAMES ──────────────────────────────────

def test_the_fallback_table_is_still_the_source_of_canonical_sector_names():
    """It must not be deleted: the whole lookup layer keys on these 11 names."""
    assert CANONICAL_SECTORS == frozenset(_FALLBACK_SECTOR_TICKERS)
    assert len(CANONICAL_SECTORS) == 11


def test_the_fallback_is_not_returned_anywhere_in_the_module():
    """Source-scan with BOTH comments and DOCSTRINGS stripped.

    ⚠️ Stripping `#` alone is not enough and this test caught itself doing it: the new
    `_get_sector_tickers` docstring explains the bug using the exact phrase
    "Returning `_FALLBACK_SECTOR_TICKERS`", so a `#`-only scan fails on the explanation.
    The mirror-image failure is the dangerous one — prose that satisfies a scan would let a
    REVERTED fix pass. `ast.unparse` drops comments by construction; docstrings need
    removing explicitly.
    """
    import ast

    tree = ast.parse(inspect.getsource(sbs))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    src = ast.unparse(tree)
    assert "return dict(_FALLBACK_SECTOR_TICKERS)" not in src
    assert "return _FALLBACK_SECTOR_TICKERS" not in src
    # ...and the table itself must still be there, or the guard is vacuous.
    assert "_FALLBACK_SECTOR_TICKERS" in src


# ── the trigger no longer points at the degraded producer ────────────────────

def test_the_admin_route_uses_the_live_producer():
    from app.api.v1.endpoints import admin

    raw = inspect.getsource(admin.refresh_sector_benchmarks)
    src = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    # docstring too: it describes the retired call by name.
    tree = __import__("ast").parse(inspect.cleandoc(raw))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], __import__("ast").Expr)
            and isinstance(fn.body[0].value, __import__("ast").Constant)):
        fn.body = fn.body[1:]
    body = __import__("ast").unparse(fn)
    assert "recompute_all" in body
    assert "compute_all_benchmarks" not in body, (
        "the admin route still triggers the 55-ticker producer"
    )


@pytest.mark.asyncio
async def test_constituents_that_all_fail_sector_mapping_also_refuse(monkeypatch):
    """The SECOND fallback arm, found by the source-scan above rather than by reading.

    If FMP answers with rows whose sectors are all unrecognised, grouping yields `{}` — and
    that path returned the hardcoded tickers too. Same corruption, different trigger.
    """
    class _WeirdSectors(_BlockedFMP):
        async def get_sp500_constituents(self):
            self.calls += 1
            return [{"symbol": "AAPL", "sector": "Wingdings"},
                    {"symbol": "MSFT", "sector": "Nonexistent"}]

    svc = _svc(monkeypatch, _WeirdSectors())
    with pytest.raises(FMPUnavailableException) as ei:
        await svc._get_sector_tickers()
    assert "0 grouped" in str(ei.value)
