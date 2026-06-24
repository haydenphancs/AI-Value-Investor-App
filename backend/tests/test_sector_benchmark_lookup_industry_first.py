"""Phase 2 — industry-first benchmark lookup with per-(metric, period) sector
fallback.

`SectorBenchmarkLookup.get_benchmarks` must prefer the INDUSTRY-aggregate row
when one exists for a (metric, period) and otherwise fall back to the
SECTOR-aggregate row — PER CELL, not wholesale. These tests construct the row
batches inline and monkeypatch `_fetch_rows`, so they never touch Supabase.
"""

import time

import pytest

from app.services import sector_benchmark_lookup as sbl
from app.services.sector_benchmark_lookup import SectorBenchmarkLookup


def _row(metric, label, value, n):
    return {
        "metric_name": metric,
        "period_label": label,
        "median_value": value,
        "sample_size": n,
    }


def _make_lookup(industry_rows, sector_rows):
    """Build a lookup whose `_fetch_rows` returns canned batches:
    industry="" → sector_rows, industry=<name> → industry_rows."""
    lk = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)  # skip get_supabase()

    def fake_fetch(columns, sector, metrics, period_type, industry=""):
        return list(industry_rows if industry else sector_rows)

    lk._fetch_rows = fake_fetch
    return lk


@pytest.fixture(autouse=True)
def _clear_cache():
    sbl._cache.clear()
    yield
    sbl._cache.clear()


def test_industry_row_preferred_over_sector():
    lk = _make_lookup(
        industry_rows=[_row("pe_ratio", "2025", 28.0, 49)],
        sector_rows=[_row("pe_ratio", "2025", 22.0, 700)],
    )
    out = lk.get_benchmarks("Software - Infrastructure", "Technology", ["pe_ratio"], "annual")
    cell = out["pe_ratio"]["2025"]
    assert cell["value"] == 28.0
    assert cell["level"] == "industry"
    assert cell["peer_group_name"] == "Software - Infrastructure"
    assert cell["n"] == 49


def test_per_cell_fallback_mixed_levels():
    # Industry covers 2025 only; 2024 must fall back to the sector value.
    lk = _make_lookup(
        industry_rows=[_row("pe_ratio", "2025", 28.0, 49)],
        sector_rows=[
            _row("pe_ratio", "2025", 22.0, 700),
            _row("pe_ratio", "2024", 19.0, 690),
        ],
    )
    out = lk.get_benchmarks("Software - Infrastructure", "Technology", ["pe_ratio"], "annual")
    assert out["pe_ratio"]["2025"]["level"] == "industry"
    assert out["pe_ratio"]["2025"]["value"] == 28.0
    # 2024 has no industry row → sector fallback for THAT cell only
    assert out["pe_ratio"]["2024"]["level"] == "sector"
    assert out["pe_ratio"]["2024"]["value"] == 19.0
    assert out["pe_ratio"]["2024"]["peer_group_name"] == "Technology"


def test_thin_industry_all_sector_fallback():
    # No industry rows at all → every cell is the sector aggregate.
    lk = _make_lookup(
        industry_rows=[],
        sector_rows=[_row("roe", "2025", 0.12, 500)],
    )
    out = lk.get_benchmarks("Tiny Niche Industry", "Industrials", ["roe"], "annual")
    cell = out["roe"]["2025"]
    assert cell["level"] == "sector"
    assert cell["value"] == 0.12
    assert cell["peer_group_name"] == "Industrials"


def test_empty_industry_arg_is_pure_sector_lookup():
    lk = _make_lookup(
        industry_rows=[_row("pe_ratio", "2025", 28.0, 49)],  # must be ignored
        sector_rows=[_row("pe_ratio", "2025", 22.0, 700)],
    )
    out = lk.get_benchmarks("", "Technology", ["pe_ratio"], "annual")
    cell = out["pe_ratio"]["2025"]
    assert cell["level"] == "sector"
    assert cell["value"] == 22.0


def test_get_benchmark_values_flattens_industry_first():
    lk = _make_lookup(
        industry_rows=[_row("net_margin", "2025", 0.06, 76)],
        sector_rows=[
            _row("net_margin", "2025", 0.09, 700),
            _row("net_margin", "2024", 0.085, 690),
        ],
    )
    flat = lk.get_benchmark_values(
        "Software - Infrastructure", "Technology", ["net_margin"], "annual"
    )
    # Industry value wins for 2025, sector fills 2024 — flat shape {metric:{label:value}}
    assert flat == {"net_margin": {"2025": 0.06, "2024": 0.085}}


def test_unrequested_metric_returns_empty_dict():
    lk = _make_lookup(industry_rows=[], sector_rows=[])
    out = lk.get_benchmarks("X", "Technology", ["pe_ratio", "pb_ratio"], "annual")
    assert out == {"pe_ratio": {}, "pb_ratio": {}}


# ── Phase 3: mature-period picker (sample-size floor + hold-last-mature) ──


def _cell(value, level="industry", name="Software - Infrastructure", n=76):
    return {"value": value, "level": level, "peer_group_name": name, "n": n}


def test_pick_mature_holds_back_thin_latest_year():
    # 2026 is partially reported (n=8) → hold the last full year 2025 (n=76).
    cells = {
        "2026": _cell(48.7, n=8),
        "2025": _cell(38.1, n=47),
        "2024": _cell(31.0, n=70),
    }
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 38.1
    assert held_back is True


def test_pick_mature_uses_latest_when_it_meets_floor():
    cells = {"2025": _cell(38.1, n=47), "2024": _cell(31.0, n=70)}
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 38.1
    assert held_back is False


def test_pick_mature_falls_back_to_latest_when_none_meet_floor():
    # A very thin industry where even the best year is below the floor:
    # still surface the latest (not "held back" — there's no mature year).
    cells = {"2026": _cell(50.0, n=8), "2025": _cell(40.0, n=12)}
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 50.0
    assert held_back is False


def test_pick_mature_empty_is_none():
    assert sbl.pick_mature_benchmark({}, floor=20) == (None, False)


def test_mature_benchmark_value_convenience():
    cells = {"2026": _cell(48.7, n=8), "2025": _cell(38.1, n=47)}
    assert sbl.mature_benchmark_value(cells, floor=20) == 38.1
    assert sbl.mature_benchmark_value({}, floor=20) is None


def test_mature_picker_n_zero_treated_as_thin():
    # A None/0 sample_size must never be chosen over a real mature year.
    cells = {"2026": _cell(99.0, n=0), "2025": _cell(38.1, n=50)}
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 38.1
    assert held_back is True


# ── Outlier: quarterly period ordering (lexical sort is WRONG here) ──


def test_period_sort_key_annual_and_quarterly():
    # Annual
    assert sbl._period_sort_key("2025") == (2025, 0)
    assert sbl._period_sort_key("2026") == (2026, 0)
    # Quarterly — Q1'26 is LATER than Q4'25 (lexical sort would invert this)
    assert sbl._period_sort_key("Q4'25") == (2025, 4)
    assert sbl._period_sort_key("Q1'26") == (2026, 1)
    assert sbl._period_sort_key("Q1'26") > sbl._period_sort_key("Q4'25")
    # Malformed → sorts oldest, never crashes
    assert sbl._period_sort_key("garbage") == (0, 0)
    assert sbl._period_sort_key("Q'") == (0, 0)


def test_pick_mature_quarterly_latest_is_mature():
    # Both quarters mature; the chronological latest (Q1'26) must win.
    # A lexical sort picks "Q4'25" (wrong) → this asserts the VALUE, catching it.
    cells = {"Q4'25": _cell(10.0, n=80), "Q1'26": _cell(20.0, n=80)}
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 20.0      # Q1'26, the real latest
    assert held_back is False


def test_pick_mature_quarterly_holds_back_thin_latest():
    # Latest quarter (Q1'26) is thin → hold back to the mature Q4'25.
    cells = {"Q4'25": _cell(10.0, n=80), "Q1'26": _cell(99.0, n=8)}
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 10.0      # held back to mature Q4'25
    assert held_back is True          # the latest (Q1'26) was skipped


def test_pick_mature_quarterly_spans_year_boundary():
    # Three quarters across a year boundary, newest (Q1'26) thin.
    cells = {
        "Q3'25": _cell(1.0, n=70),
        "Q4'25": _cell(2.0, n=70),
        "Q1'26": _cell(3.0, n=5),
    }
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 2.0       # Q4'25 is the latest mature, not Q3'25
    assert held_back is True


def test_pick_mature_floor_is_inclusive_at_exactly_20():
    # Boundary: n == floor must COUNT as mature (>= , not >).
    cells = {"2026": _cell(50.0, n=19), "2025": _cell(40.0, n=20)}
    cell, held_back = sbl.pick_mature_benchmark(cells, floor=20)
    assert cell["value"] == 40.0      # 2025 (n=20) qualifies; 2026 (n=19) does not
    assert held_back is True


def test_cache_double_expiry_does_not_raise():
    # Two reads of an already-expired key must not raise KeyError (pop, not del)
    # — the lookup runs in asyncio.to_thread workers that can race here.
    key = "expiry-test-key"
    sbl._cache[key] = (time.time() - sbl._CACHE_TTL - 100, {"x": 1})
    assert sbl._cache_get(key) is None
    assert sbl._cache_get(key) is None  # second read: key already gone, no crash
