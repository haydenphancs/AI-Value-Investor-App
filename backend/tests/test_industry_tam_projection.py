"""The Moat row's projected US industry revenue (owner decisions 2026-10-22).

`app/services/industry_tam_projection.py` scales a MEASURED Census value along a pinned public
path (BLS output / CMS health spending / US nominal GDP). These tests pin the arithmetic with
hand-computed values from the committed table, every guard that must return None (never raise),
and the fail-closed load of a missing or bad table.
"""

from __future__ import annotations

import copy
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.services.industry_tam_projection as proj
from app.services.industry_tam_service import (
    INDUSTRY_TO_CENSUS,
    _census_source_label,
    census_label_codes,
)

_AD = "Aerospace & Defense"
_AD_LABEL = ("US Census AIES — Aerospace & defense manufacturing (NAICS 3364, 334511, 336992, "
             "332992, 332993, 332994)")
_TABLE = Path(proj._DATA_PATH)


@pytest.fixture(autouse=True)
def _fresh_table():
    proj.reset_projection_table()
    yield
    proj.reset_projection_table()


def _label(industry: str) -> str:
    """The Census label the dossier stores for `industry` (same builder as production)."""
    mapping = INDUSTRY_TO_CENSUS[industry]
    codes = proj.census_codes(mapping)
    return _census_source_label(", ".join(codes), "label")


def _project(industry: str, tam: float, year=2024, start=2026, label=None):
    return proj.project_industry_tam(
        industry=industry, measured_tam_b=tam, measured_year=year,
        source_label=_label(industry) if label is None else label, start_year=start,
    )


# ── Hand-computed values (start 2026, measured 2024) ─────────────────────────────────

@pytest.mark.parametrize("industry, tam, start_b, target_b, rate, basis", [
    # 6 codes, each on its own BLS sector (71/62/74/49), weighted by 2024 Census shares
    (_AD, 342.8, 400.0, 494.6, 4.3, "bls_output"),
    # 871.894 × (BLS83 2025/2024) × g^1, g^6
    ("Discount Stores", 871.9, 934.8, 1109.0, 3.5, "bls_output"),
    ("Software - Infrastructure", 567.8, 626.8, 769.3, 4.2, "bls_output"),
    # CMS Table 3 health insurance: ×4476.2/3892.9, ×5810.7/3892.9
    ("Medical - Healthcare Plans", 1566.5, 1801.2, 2338.2, 5.4, "cms_nhe"),
    # CMS Table 11 retail Rx: ×561.5/467.0, ×715.3/467.0
    ("Medical - Distribution", 1556.7, 1871.7, 2384.4, 5.0, "cms_nhe"),
    # US nominal GDP: ×30779/29298 × 1.043131^1 and ^6
    ("Restaurants", 864.9, 947.8, 1170.6, 4.3, "gdp_path"),
])
def test_projection_matches_hand_computed_values(industry, tam, start_b, target_b, rate, basis):
    p = _project(industry, tam)
    assert p is not None
    assert (p.start_year, p.target_year) == (2026, 2031)
    assert p.start_tam_b == pytest.approx(start_b, abs=0.05)
    assert p.target_tam_b == pytest.approx(target_b, abs=0.05)
    assert p.rate_pct == rate and p.basis == basis
    assert p.source_label.startswith("Projection: ") and f"+{rate:.1f}%/yr" in p.source_label
    assert len(p.source_label) <= 120


def test_formulas_by_hand_from_the_committed_table():
    """Re-derive three tiers from the raw table numbers, not from the module."""
    doc = json.loads(_TABLE.read_text(encoding="utf-8"))
    hi = doc["cms_series"]["health_insurance"]["values"]
    assert _project("Medical - Healthcare Plans", 1566.5).start_tam_b == round(
        1566.5 * hi["2026"] / hi["2024"], 1)
    g = doc["gdp"]
    rate = (g["projection_value"] / g["base_value"]) ** 0.1
    want = 864.9 * g["base_value"] / g["history"]["2024"] * rate ** 6
    assert _project("Restaurants", 864.9).target_tam_b == round(want, 1)
    sec = doc["bls_sectors"]["94"]["output"]
    r94 = (sec["2035"] / sec["2025"]) ** 0.1
    assert _project("Software - Infrastructure", 567.8).start_tam_b == round(
        567.8 * sec["2025"] / sec["2024"] * r94, 1)


def test_a_later_start_year_moves_along_the_path():
    p = _project(_AD, 342.8, start=2027)                  # 417.3498… by hand
    assert (p.start_year, p.target_year, p.start_tam_b, p.target_tam_b) == (2027, 2032, 417.3, 516.0)
    p = _project(_AD, 342.8, start=2030)
    assert (p.start_tam_b, p.target_tam_b) == (474.0, 586.1)


def test_a_target_past_the_source_horizon_is_no_projection(caplog):
    caplog.set_level(logging.WARNING, logger=proj.__name__)
    assert _project("Medical - Healthcare Plans", 1566.5, start=2030) is None   # 2035 > CMS 2034
    assert _project("Medical - Healthcare Plans", 1566.5, start=2030) is None
    assert sum("outside the cms_nhe path" in r.message for r in caplog.records) == 1  # warn once
    assert _project(_AD, 342.8, start=2031) is None                            # 2036 > BLS 2035
    assert _project("Restaurants", 864.9, start=2031) is None


def test_a_single_code_measured_in_2023_still_projects():
    p = _project("Software - Infrastructure", 500.0, year=2023)
    doc = json.loads(_TABLE.read_text(encoding="utf-8"))
    sec = doc["bls_sectors"]["94"]["output"]
    r94 = (sec["2035"] / sec["2025"]) ** 0.1
    assert p.start_tam_b == round(500.0 * sec["2025"] / sec["2023"] * r94, 1)


def test_a_composite_measured_in_another_year_is_no_projection(caplog):
    caplog.set_level(logging.WARNING, logger=proj.__name__)
    assert _project(_AD, 329.0, year=2023) is None
    assert any("composite weights are for 2024" in r.message for r in caplog.records)


def test_a_composite_whose_total_drifted_is_no_projection(caplog):
    caplog.set_level(logging.WARNING, logger=proj.__name__)
    assert _project(_AD, 342.8 * 1.004) is not None       # inside the 0.5% rounding band
    assert _project(_AD, 360.0) is None
    assert any("differs from the pinned composite total" in r.message for r in caplog.records)


def test_start_not_after_the_measured_year_is_no_projection():
    assert _project(_AD, 342.8, start=2024) is None
    assert _project(_AD, 342.8, start=2023) is None


@pytest.mark.parametrize("tam", [0, 0.0, -5, float("nan"), float("inf"), float("-inf"), None, "big",
                                 True, False, "342.8", [342.8]])
def test_a_bad_tam_is_no_projection(tam):
    assert _project(_AD, tam) is None


@pytest.mark.parametrize("year", [None, "FY24", 2569, True, 2024.5, "", 1989, [2024]])
def test_a_bad_measured_year_is_no_projection(year):
    assert _project(_AD, 342.8, year=year) is None


def test_a_digit_string_year_is_read_as_the_year():
    assert _project(_AD, 342.8, year="2024") == _project(_AD, 342.8, year=2024)


@pytest.mark.parametrize("start", [None, "2026", 2026.0, True, 1900, 2200])
def test_a_bad_start_year_is_no_projection(start):
    assert _project(_AD, 342.8, start=start) is None


@pytest.mark.parametrize("industry", ["Banks", "Banks - Diversified", "Department Stores", "Steel",
                                      "Internet Retail", "Publishing", "Insurance - Reinsurance"])
def test_measured_only_industries_never_project(industry):
    assert proj.projection_tier(industry) == "measured_only"
    assert _project(industry, 500.0) is None


@pytest.mark.parametrize("industry", ["Railroads", "Gold", "", None, 42])
def test_an_unpinned_industry_never_projects(industry):
    assert proj.project_industry_tam(industry=industry, measured_tam_b=50.0, measured_year=2024,
                                     source_label="US Census AIES (NAICS 482)", start_year=2026) is None


@pytest.mark.parametrize("industry, label", [
    ("Restaurants", "BEA Food Services GDP (via FRED)"),
    ("Railroads", "BEA Rail Transportation GDP (via FRED)"),
    (_AD, "US Census AIES — Aerospace product and parts manufacturing (NAICS 3364)"),  # stale prod row
    (_AD, "Research figure withdrawn — pending recompute"),
    (_AD, None), (_AD, ""), (_AD, 3364),
    ("Restaurants", "US Census AIES — Restaurants (NAICS 7225, 7224)"),
])
def test_a_figure_that_is_not_the_backtested_census_figure_never_projects(industry, label, caplog):
    caplog.set_level(logging.WARNING, logger=proj.__name__)
    assert proj.project_industry_tam(industry=industry, measured_tam_b=342.8, measured_year=2024,
                                     source_label=label, start_year=2026) is None


def test_results_are_finite_and_never_negative_zero():
    for industry in INDUSTRY_TO_CENSUS:
        p = _project(industry, 100.0)
        if p is None:
            continue
        for v in (p.start_tam_b, p.target_tam_b, p.rate_pct):
            assert math.isfinite(v)
        assert p.start_tam_b > 0 and p.target_tam_b > 0
        assert math.copysign(1.0, p.rate_pct) == 1.0 or p.rate_pct < 0
        fields = p.response_fields()
        assert set(fields) == {
            "projection_basis", "projection_year", "projection_tam", "projection_target_year",
            "projection_target_tam", "projection_rate_pct", "projection_source_label",
            "projection_year_label", "projection_target_year_label"}
        # Marked as projected (owner 2026-10-22: "the years as 2026e → 2031e").
        assert fields["projection_year_label"] == f"{p.start_year}e"
        assert fields["projection_target_year_label"] == f"{p.target_year}e"


def test_a_falling_path_prints_a_minus_sign():
    p = _project("Auto - Parts", 290.7)
    assert p.rate_pct < 0 and p.target_tam_b < p.start_tam_b
    assert "−1.5%/yr" in p.source_label


# ── current_year_et ───────────────────────────────────────────────────────────────

def test_the_current_year_is_eastern():
    assert proj.current_year_et(datetime(2027, 1, 1, 4, 30, tzinfo=timezone.utc)) == 2026
    assert proj.current_year_et(datetime(2027, 1, 1, 5, 30, tzinfo=timezone.utc)) == 2027


# ── census_label_codes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("codes", [("4523",), ("3364", "334511", "336992", "332992", "332993", "332994")])
def test_census_label_codes_round_trips_the_label_builder(codes):
    joined = ", ".join(codes)
    assert census_label_codes(_census_source_label(joined, "Some label (with parens)")) == codes
    assert census_label_codes(_census_source_label(joined)) == codes


@pytest.mark.parametrize("label", [None, 42, "", "BEA Rail Transportation GDP (via FRED)",
                                   "US Census AIES — x (NAICS )", "US Census AIES — x (NAICS 4523) trailing",
                                   "Census AIES — x (NAICS 4523)", "US Census AIES — x (NAICS 4523a)",
                                   "US Census AIES — " + "x" * 500 + " (NAICS 4523)"])
def test_census_label_codes_rejects_everything_else(label):
    assert census_label_codes(label) is None


# ── The table: fail closed ────────────────────────────────────────────────────────

def _point_at(monkeypatch, tmp_path, text: str) -> None:
    path = tmp_path / "industry_tam_projections.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(proj, "_DATA_PATH", path)
    proj.reset_projection_table()


def _committed() -> dict:
    return json.loads(_TABLE.read_text(encoding="utf-8"))


def test_the_committed_table_is_valid():
    assert proj.validate_table(_committed()) == []


def test_a_missing_table_disables_projections_with_one_error(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(proj, "_DATA_PATH", tmp_path / "missing.json")
    proj.reset_projection_table()
    caplog.set_level(logging.ERROR, logger=proj.__name__)
    assert _project(_AD, 342.8) is None and _project("Restaurants", 864.9) is None
    assert proj.projection_tier(_AD) is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "PROJECTIONS DISABLED" in errors[0].message


@pytest.mark.parametrize("text", [
    "{not json",
    json.dumps({"x": 1}),
    "[]",
])
def test_a_corrupt_table_disables_projections(monkeypatch, tmp_path, caplog, text):
    _point_at(monkeypatch, tmp_path, text)
    caplog.set_level(logging.ERROR, logger=proj.__name__)
    assert _project(_AD, 342.8) is None
    assert any("PROJECTIONS DISABLED" in r.message for r in caplog.records)


def test_a_nan_literal_or_a_duplicate_key_disables_projections(monkeypatch, tmp_path):
    raw = _TABLE.read_text(encoding="utf-8")
    _point_at(monkeypatch, tmp_path, raw.replace('"2035": 520593.7', '"2035": NaN', 1))
    assert _project(_AD, 342.8) is None
    _point_at(monkeypatch, tmp_path, raw.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1))
    assert _project(_AD, 342.8) is None


@pytest.mark.parametrize("mutate", [
    lambda d: d["industries"][_AD]["composite"]["codes"]["3364"].update(bls_sectors=[999]),
    lambda d: d["bls_sectors"]["71"]["output"].update({"2035": -1.0}),
    lambda d: d["bls_sectors"]["71"]["output"].pop("2021"),
    lambda d: d["cms_series"]["rx_retail"]["values"].pop("2031"),
    lambda d: d["industries"]["Restaurants"].update(tier="gdp"),
    lambda d: d["industries"]["Internet Retail"].update(tier="bls_output"),   # unmappable
    lambda d: d["industries"]["Medical - Distribution"].pop("cms_series"),
    lambda d: d["gdp"].update(base_value=float("inf")),
    lambda d: d.update(extra=1),
    lambda d: d["industries"]["Restaurants"].update(naics_2017=["7225", "7225"]),
    lambda d: d["industries"][_AD]["composite"].update(weights_year=True),
    lambda d: d["industries"]["Banks"].update(exception={"rule_tier": "x"}),
    lambda d: d["gdp"].update(cms_base_year_value=30.767),                 # a unit change
    lambda d: d["gdp"]["vintage_overlap"]["2015"].update(cms=18000.0),     # two BEA vintages
    lambda d: d["gdp"].pop("vintage_overlap"),
])
def test_an_invalid_table_disables_every_industry(monkeypatch, tmp_path, mutate):
    doc = copy.deepcopy(_committed())
    mutate(doc)
    assert proj.validate_table(doc), "the mutation must be caught by validate_table"
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    assert _project(_AD, 342.8) is None and _project("Software - Infrastructure", 567.8) is None


def test_a_mapping_drift_disables_only_that_industry(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=proj.__name__)
    monkeypatch.setitem(INDUSTRY_TO_CENSUS, "Discount Stores", "4529")
    proj.reset_projection_table()
    assert _project("Discount Stores", 871.9, label="US Census AIES — x (NAICS 4523)") is None
    assert proj.projection_tier("Discount Stores") is None
    assert _project("Software - Infrastructure", 567.8) is not None
    assert any("DISABLED for 'Discount Stores'" in r.message for r in caplog.records)


def test_an_implausible_rate_is_refused_as_bad_data(monkeypatch, tmp_path, caplog):
    doc = copy.deepcopy(_committed())
    doc["bls_sectors"]["94"]["output"]["2035"] *= 30          # ≈ +40%/yr
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    caplog.set_level(logging.ERROR, logger=proj.__name__)
    assert _project("Software - Infrastructure", 567.8) is None
    assert any("bad table data" in r.message for r in caplog.records)


def test_a_stale_table_logs_an_error_and_still_projects(monkeypatch, tmp_path, caplog):
    doc = _committed()
    doc["sources"]["bls_output"]["released"] = "2020-08-27"
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    caplog.set_level(logging.ERROR, logger=proj.__name__)
    assert _project(_AD, 342.8) is not None
    assert any("PROJECTIONS STALE" in r.message for r in caplog.records)


def test_the_committed_table_is_not_stale_today(caplog):
    caplog.set_level(logging.ERROR, logger=proj.__name__)
    assert _project(_AD, 342.8) is not None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
