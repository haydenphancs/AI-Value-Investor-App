"""FRED industry CAGR: measured over the whole fetched window, from the dates.

`fred_tam_for_series` used `obs[5]` — five years back from the latest point. With
2025 data that is 2020, the COVID trough, so every BEA series showed inflated
growth: Restaurants 12.4% (≈6.3% from 2018), mining 14.5% (≈3.5%), all of US
manufacturing 6.3% (≈3.8%). Owner decision 2026-10-01: measure from the oldest
valid observation of the 8 fetched (2018 → 2025, seven years — the Census tier's
2017→2024 span is seven too), with the span taken from the observation dates.

Hermetic: the FRED client is faked at the binding `industry_tam_service` uses.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional, Tuple

import pytest

import app.services.industry_tam_service as its

# USFOODDPNGSP (food services & drinking places, $M), live 2026-10-01, newest first.
RESTAURANTS = [
    ("2025", 708914), ("2024", 687264), ("2023", 656864), ("2022", 588234),
    ("2021", 518949), ("2020", 395898), ("2019", 490182), ("2018", 461627),
]


def _install(monkeypatch, rows: List[Tuple[Optional[str], float]]):
    class _FRED:
        is_configured = True

        async def get_observations(self, series_id, *, limit=8):
            return [SimpleNamespace(date=(f"{y}-01-01" if y else None), value=float(v))
                    for y, v in rows[:limit]]

    monkeypatch.setattr(its, "get_fred_client", lambda: _FRED())


@pytest.mark.asyncio
async def test_cagr_runs_from_the_oldest_observation_not_the_2020_trough(monkeypatch):
    _install(monkeypatch, RESTAURANTS)
    tam = await its.fred_tam_for_series("USFOODDPNGSP")
    assert tam.current_tam == 708.9
    assert tam.cagr_5y_pct == 6.3                # (708914/461627)^(1/7) − 1
    assert tam.cagr_5y_pct != 12.4               # the old 2020-based figure
    assert tam.future_tam == pytest.approx(708.9 * 1.0632 ** 5, rel=1e-3)
    assert (tam.current_year, tam.future_year) == ("2025", "2030")


@pytest.mark.asyncio
async def test_span_comes_from_the_dates_when_a_year_is_missing(monkeypatch):
    rows = [("2025", 200.0), ("2024", 190.0), ("2022", 170.0), ("2019", 100.0)]  # gaps
    _install(monkeypatch, rows)
    tam = await its.fred_tam_for_series("X")
    assert tam.cagr_5y_pct == round(((200 / 100) ** (1 / 6) - 1) * 100, 1)   # 6 years, not 3


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0.0, -50.0, float("nan"), float("inf")])
async def test_an_unusable_oldest_point_falls_back_to_the_next_oldest(monkeypatch, bad):
    rows = RESTAURANTS[:-1] + [("2018", bad)]
    _install(monkeypatch, rows)
    tam = await its.fred_tam_for_series("USFOODDPNGSP")
    assert tam.cagr_5y_pct == round(((708914 / 490182) ** (1 / 6) - 1) * 100, 1)  # from 2019


@pytest.mark.asyncio
async def test_an_undated_oldest_point_is_skipped(monkeypatch):
    rows = RESTAURANTS[:-1] + [(None, 461627)]
    _install(monkeypatch, rows)
    tam = await its.fred_tam_for_series("USFOODDPNGSP")
    assert tam.cagr_5y_pct == round(((708914 / 490182) ** (1 / 6) - 1) * 100, 1)


@pytest.mark.asyncio
async def test_no_usable_base_gives_no_cagr_rather_than_a_fake_zero(monkeypatch):
    _install(monkeypatch, [("2025", 708914), ("2024", 0.0), ("2023", float("nan"))])
    tam = await its.fred_tam_for_series("USFOODDPNGSP")
    assert tam.current_tam == 708.9
    assert tam.cagr_5y_pct is None
    assert tam.cagr_start_year is None           # no window without a rate
    assert tam.future_tam == tam.current_tam     # no projection without a rate


@pytest.mark.asyncio
async def test_two_points_give_a_one_year_rate(monkeypatch):
    _install(monkeypatch, [("2025", 110.0), ("2024", 100.0)])
    tam = await its.fred_tam_for_series("X")
    assert tam.cagr_5y_pct == 10.0


@pytest.mark.asyncio
async def test_a_negative_cagr_survives(monkeypatch):
    _install(monkeypatch, [("2025", 398662), ("2024", 411637), ("2018", 450000)])
    tam = await its.fred_tam_for_series("USMINNGSP")
    assert tam.cagr_5y_pct < 0


# ── The growth window travels with the rate (owner decision 2026-10-22) ──────


@pytest.mark.asyncio
async def test_fred_tam_reports_its_base_year(monkeypatch):
    """The card labels the rate with its real span ("Avg/yr 2018-25"), so the tier
    must say which observation it measured from."""
    _install(monkeypatch, RESTAURANTS)
    tam = await its.fred_tam_for_series("USFOODDPNGSP")
    assert tam.cagr_start_year == 2018 and tam.current_year == "2025"


@pytest.mark.asyncio
async def test_an_unusable_oldest_point_moves_the_base_year(monkeypatch):
    _install(monkeypatch, RESTAURANTS[:-1] + [("2018", 0.0)])
    tam = await its.fred_tam_for_series("USFOODDPNGSP")
    assert tam.cagr_start_year == 2019


@pytest.mark.asyncio
async def test_a_gapped_series_reports_the_dated_base_year(monkeypatch):
    _install(monkeypatch, [("2025", 200.0), ("2024", 190.0), ("2022", 170.0), ("2019", 100.0)])
    tam = await its.fred_tam_for_series("X")
    assert tam.cagr_start_year == 2019


@pytest.mark.asyncio
async def test_a_near_zero_fred_rate_is_positive_zero(monkeypatch):
    import math as _math
    _install(monkeypatch, [("2025", 999.0), ("2024", 999.2)])     # −0.02%/yr → −0.0 rounded
    tam = await its.fred_tam_for_series("X")
    assert tam.cagr_5y_pct == 0.0 and _math.copysign(1.0, tam.cagr_5y_pct) == 1.0
    assert tam.cagr_start_year == 2024
