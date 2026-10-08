"""The statement-twin pass must fail LOUDLY when it proves nothing (2026-10-09 review).

A scan with no failed calls can still be a silent no-op: FMP renaming the five ratio fields
(every answer "uninformative") or answering [] for everyone leaves every twin voting twice
behind exit 0. The build now refuses (exit 1, nothing written) when, on a scan of at least
`_TWIN_SANITY_MIN_ROWS` rows, fewer than half the non-empty answers carried a fingerprint or
more than 10% answered []. The production build of 2026-10-09 fingerprinted 96.5% and saw
0.8% empty. Also: a shrink / missing-industry refusal is now decided BEFORE the scan (the
pass only removes rows), so a refused build spends no ratios-ttm call, and the payload's
`source` records how many rows were fingerprinted.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import scripts.build_benchmark_universe as bu
from tests.test_benchmark_universe_builder_twins_2026_10_09 import (
    FLOOR, SHARED, _FakeFMP, _row, _run, _syms,
)

_N = 60   # above _TWIN_SANITY_MIN_ROWS


def _scan(fp: int, uninformative: int, empty: int, planned: int) -> bu._FingerprintScan:
    fps = {f"F{i}": (0.1, 0.2, 0.3, 1.0, 0.5) for i in range(fp)}
    return bu._FingerprintScan(fps, [f"E{i}" for i in range(empty)],
                               [f"U{i}" for i in range(uninformative)], {}, [], planned, 1.0)


@pytest.mark.parametrize("fp,uninf,empty,planned,bad", [
    (2840, 102, 25, 2967, False),     # the real 2026-10-09 build
    (50, 50, 0, 100, False),          # exactly half fingerprinted: still sane
    (49, 51, 0, 100, True),           # under half
    (0, 100, 0, 100, True),           # renamed fields: nothing fingerprints
    (90, 0, 10, 100, False),          # exactly 10% empty: sane
    (89, 0, 11, 100, True),           # over 10% empty
    (0, 0, 100, 100, True),           # [] for everyone
    (0, 49, 0, 49, False),            # too few rows to judge
])
def test_the_sanity_table(fp, uninf, empty, planned, bad):
    assert (bu._twin_scan_problem(_scan(fp, uninf, empty, planned)) is not None) is bad


def test_the_problem_text_names_the_fields():
    msg = bu._twin_scan_problem(_scan(0, 100, 0, 100))
    assert "grossProfitMarginTTM" in msg and "0 of 100" in msg


def _market(n: int = _N):
    return {"Conglomerates": [_row(s, f"{s} Company Inc.") for s in _syms(n, "C")]}


@pytest.mark.asyncio
async def test_renamed_ratio_fields_fail_the_build(tmp_path, caplog):
    """Every answer carries the numbers under other keys: exit 1, nothing written, one
    ERROR naming the problem. Mutation: dropping the check writes the file with exit 0."""
    renamed = [{f"x_{k}": v for k, v in SHARED.items()}]
    market = _market()
    fmp = _FakeFMP(market, ratios={r["symbol"]: renamed for r in market["Conglomerates"]})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_BUILD_FAILED
    assert not out.exists()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "proved nothing" in errors[0]


@pytest.mark.asyncio
async def test_empty_answers_for_everyone_fail_the_build(tmp_path):
    market = _market()
    fmp = _FakeFMP(market, ratios={r["symbol"]: [] for r in market["Conglomerates"]})
    out = tmp_path / "u.json"
    assert await _run(fmp, out) == bu.EXIT_BUILD_FAILED
    assert not out.exists()


@pytest.mark.asyncio
async def test_a_healthy_scan_records_its_counts_in_source(tmp_path):
    market = _market()
    fmp = _FakeFMP(market)
    out = tmp_path / "u.json"
    assert await _run(fmp, out) == bu.EXIT_OK
    source = json.loads(out.read_text())["source"]
    assert f"{_N} of {_N} rows fingerprinted" in source


@pytest.mark.asyncio
async def test_a_skipped_scan_says_so_in_source(tmp_path):
    out = tmp_path / "u.json"
    assert await _run(_FakeFMP(_market()), out, skip_twin_scan=True) == bu.EXIT_OK
    assert "SKIPPED" in json.loads(out.read_text())["source"]


def _previous(out: Path, industries):
    payload = {"generated_at": "2026-10-08T00:00:00+00:00", "market_cap_floor": FLOOR,
               "industries": [{"industry": k, "sector": "Industrials", "tickers": v,
                               "market_caps": {t: 1e9 for t in v}} for k, v in industries.items()]}
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_shrink_refusal_spends_no_ratios_call(tmp_path, caplog):
    """100 → 60 (40%, over the default bar): refused before the scan — zero ratios-ttm
    calls, the previous file untouched, one REFUSED error. Mutation: deleting the pre-scan
    check spends 60 calls and only then refuses."""
    out = tmp_path / "u.json"
    before = _previous(out, {"Conglomerates": _syms(100, "P")})
    fmp = _FakeFMP(_market())
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_SHRINK_REFUSED
    assert fmp.ratio_calls == []
    assert out.read_text(encoding="utf-8") == before
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "REFUSED" in errors[0]
    assert any("no ratios-ttm call was spent" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_missing_industry_refusal_spends_no_ratios_call(tmp_path):
    out = tmp_path / "u.json"
    before = _previous(out, {"Conglomerates": _syms(_N, "C"), "Gone Industry": _syms(30, "G")})
    fmp = _FakeFMP(_market())
    assert await _run(fmp, out, allow_shrink=50) == bu.EXIT_SHRINK_REFUSED
    assert fmp.ratio_calls == [] and out.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_an_allowed_build_scans_and_writes(tmp_path):
    out = tmp_path / "u.json"
    _previous(out, {"Conglomerates": _syms(100, "P")})
    fmp = _FakeFMP(_market())
    assert await _run(fmp, out, allow_shrink=50) == bu.EXIT_OK
    assert len(fmp.ratio_calls) == _N


@pytest.mark.asyncio
async def test_the_quiet_check_restores_the_logger(tmp_path, caplog):
    out = tmp_path / "u.json"
    _previous(out, {"Conglomerates": _syms(_N, "C")})
    assert await _run(_FakeFMP(_market()), out) == bu.EXIT_OK
    assert bu.logger.disabled is False
