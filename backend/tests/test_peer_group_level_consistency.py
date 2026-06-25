"""Label/value consistency for the fundamental cards' peer-group level.

Confirmed bug: the collector derived `peer_group_level` (the "Industry Avg" vs
"Sector Avg" wording for the 4 cards) by voting on the ANNUAL benchmark rows, while the
card VALUES come from the TTM-first `get_current_benchmark_values`. TTM and annual rows
are populated by two independent jobs, so an industry can have annual industry rows
(→ label "industry") while its values fall back to the sector TTM — a card labeled
"Industry Avg" over sector numbers. The fix votes on `get_current_benchmarks` instead.

This pins the lookup-level mechanism the fix relies on: the level voted from the
current-snapshot lookup matches the level actually backing the displayed values, and
diverges from the old annual vote in exactly the mismatch scenario.

Also guards the backend↔iOS label coupling: the iOS strip regex only matches the literal
"sector", so the backend must keep emitting "sector"-worded suffixes until that regex is
broadened (deferred Phase-3 work).
"""

import pathlib

import pytest

from app.services import sector_benchmark_lookup as sbl
from app.services.sector_benchmark_lookup import SectorBenchmarkLookup


def _row(metric, label, value, n):
    return {"metric_name": metric, "period_label": label,
            "median_value": value, "sample_size": n}


def _make_lookup_by_pt(rows_by_pt_industry):
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)

    def fake_fetch(columns, sector, metrics, period_type, industry=""):
        return list(rows_by_pt_industry.get((period_type, bool(industry)), []))

    lk._fetch_rows = fake_fetch
    return lk


def _vote_level(cur):
    """Replicates the collector's (fixed) majority vote over current-snapshot cells."""
    levels = [cell.get("level") for cell in cur.values() if cell]
    if not levels:
        return None
    return "industry" if levels.count("industry") >= levels.count("sector") else "sector"


@pytest.fixture(autouse=True)
def _clear_cache():
    sbl._cache.clear()
    yield
    sbl._cache.clear()


def test_level_follows_value_when_values_are_sector_ttm():
    metrics = ["gross_margin", "net_margin"]
    lk = _make_lookup_by_pt({
        # Current snapshot: only SECTOR TTM rows → every displayed value is sector-level.
        ("ttm", False): [_row("gross_margin", "TTM", 0.40, 300),
                         _row("net_margin", "TTM", 0.10, 300)],
        # Annual INDUSTRY rows exist → the OLD annual vote would say "industry".
        ("annual", True): [_row("gross_margin", "2025", 0.42, 50),
                           _row("net_margin", "2025", 0.11, 50)],
        ("annual", False): [_row("gross_margin", "2025", 0.39, 300),
                            _row("net_margin", "2025", 0.09, 300)],
    })

    # OLD vote (annual) would mislabel as industry...
    annual_rich = lk.get_benchmarks("Ind", "Tech", metrics, "annual")
    old_levels = [c.get("level") for p in annual_rich.values() for c in p.values()]
    assert old_levels.count("industry") >= 1

    # NEW vote (current snapshot) matches the values: sector.
    cur = lk.get_current_benchmarks("Ind", "Tech", metrics)
    assert _vote_level(cur) == "sector"
    vals = lk.get_current_benchmark_values("Ind", "Tech", metrics)
    assert vals["gross_margin"] == 0.40 and vals["net_margin"] == 0.10


def test_level_is_industry_when_values_are_industry_ttm():
    metrics = ["gross_margin", "net_margin"]
    lk = _make_lookup_by_pt({
        ("ttm", True): [_row("gross_margin", "TTM", 0.45, 40),
                        _row("net_margin", "TTM", 0.12, 40)],
        ("ttm", False): [_row("gross_margin", "TTM", 0.40, 300),
                         _row("net_margin", "TTM", 0.10, 300)],
    })
    cur = lk.get_current_benchmarks("Ind", "Tech", metrics)
    assert _vote_level(cur) == "industry"
    vals = lk.get_current_benchmark_values("Ind", "Tech", metrics)
    assert vals["gross_margin"] == 0.45      # industry TTM value


def test_vote_skips_none_cells():
    # A metric with no coverage returns a None cell — the vote must skip it, not crash.
    lk = _make_lookup_by_pt({
        ("ttm", False): [_row("gross_margin", "TTM", 0.40, 300)],
        # net_margin: no ttm, no annual → None
    })
    cur = lk.get_current_benchmarks("Ind", "Tech", ["gross_margin", "net_margin"])
    assert cur["net_margin"] is None
    assert _vote_level(cur) == "sector"      # voted only on the present (gross_margin) cell


def test_backend_labels_still_use_sector_wording():
    # The iOS displayLabel strip regex (#"\s*\([^)]*sector[^)]*\)"#) only matches the
    # literal "sector". The backend value-suffix labels must keep the "sector" wording
    # until that regex is broadened to an "avg" anchor (deferred) — else the suffix
    # renders raw and the "*" footnote breaks. Guard: no "industry avg" label text.
    services = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    for fn in ("valuation_snapshot_service.py", "profitability_snapshot_service.py",
               "health_snapshot_service.py"):
        src = (services / fn).read_text().lower()
        assert "industry avg" not in src, f"{fn} emits an 'industry avg' label — iOS regex only strips 'sector'"
