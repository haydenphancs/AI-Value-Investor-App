"""The owner-run refresh of the projection table (`scripts/refresh_industry_tam_projections.py`).

Hermetic: synthetic BLS CSVs, and BLS / CMS workbooks built here with `zipfile` (the script's
own stdlib .xlsx reader — openpyxl is not a backend dependency). Pins that a build from good
sources validates and keeps the curated industries, that a changed layout, a renumbered sector
or a moved tier is refused, and that a dry run writes nothing.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

import scripts.refresh_industry_tam_projections as refresh
from app.services.industry_tam_projection import parse_table_text, project_with_table, validate_table

# ── A minimal .xlsx writer (inline strings; shared strings for one cell to cover both) ──

def _col(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _sheet_xml(rows: List[List[Any]], shared: List[str]) -> str:
    cells = []
    for r, row in enumerate(rows):
        parts = []
        for c, v in enumerate(row):
            ref = f"{_col(c)}{r + 1}"
            if v is None:
                continue
            if isinstance(v, str) and v.startswith("@shared:"):
                shared.append(v[len("@shared:"):])
                parts.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            elif isinstance(v, str):
                parts.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                parts.append(f'<c r="{ref}"><v>{v}</v></c>')
        cells.append(f'<row r="{r + 1}">{"".join(parts)}</row>')
    return ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/'
            f'spreadsheetml/2006/main"><sheetData>{"".join(cells)}</sheetData></worksheet>')


def _xlsx(path: Path, sheets: Dict[str, List[List[Any]]]) -> Path:
    shared: List[str] = []
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        sheet_tags, rels = [], []
        for i, (name, rows) in enumerate(sheets.items(), start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, shared))
            sheet_tags.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" '
                        f'Type="{rel_ns}/worksheet"/>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel_ns}"><sheets>'
                                       f'{"".join(sheet_tags)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{"".join(rels)}</Relationships>')
        sst = "".join(f"<si><t>{s}</t></si>" for s in shared)
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">{sst}</sst>')
    return path


# ── Synthetic sources ─────────────────────────────────────────────────────────────

_YEARS = list(range(2017, 2026))


def _grow(base: float, rate: float, years=_YEARS) -> Dict[int, float]:
    return {y: round(base * (1 + rate) ** (y - years[0]), 1) for y in years}


_SECTORS = {94: (_grow(300_000, 0.10), "5132", "Software publishers"),
            80: (_grow(1_000_000, 0.05), "42", "Wholesale trade"),
            155: (_grow(400_000, 0.09), "722511", "Full-service restaurants"),
            156: (_grow(300_000, 0.09), "7223, 7224", "All other food and drinking places")}


def _bls_dir(tmp: Path, sectors=_SECTORS, proj_2025_drift: float = 0.0, titles=None) -> Path:
    d = tmp / "bls"
    d.mkdir(exist_ok=True)
    ind = ["SERIESNUMBER,SECTORNUMBER," + ",".join(map(str, _YEARS))]
    proj = ["SERIESNUMBER,SECTORNUMBER,2025,2035"]
    for s, (hist, _, _) in sectors.items():
        ind.append(f"1,{s}," + ",".join(str(hist[y]) for y in _YEARS))
        ind.append(f"2,{s}," + ",".join("1" for _ in _YEARS))           # chained $: ignored
        proj.append(f"1,{s},{hist[2025] + proj_2025_drift},{round(hist[2025] * 1.5, 1)}")
    (d / "ind2025.csv").write_text("\n".join(ind) + "\n")
    (d / "proj2035.csv").write_text("\n".join(proj) + "\n")
    stubs = [["Sector Number", "BLS_IO_Summary", "NAICS_2022", "@shared:Sector Title"]]
    for s, (_, naics, title) in sectors.items():
        stubs.append([s, "", naics, (titles or {}).get(s, title)])
    _xlsx(d / "SectorPlan2035.xlsx", {"Industry Commodity Sectors": [["x"]], "Stubs": stubs})
    return d


def _agg(tmp: Path, sheet="Table 4.3", label="Gross Domestic Product", scale=1.0, y2015=18295.0,
         y2025=30779.0) -> Path:
    path = tmp / "agg.xlsx"
    if path.exists():
        path.unlink()
    return _xlsx(path, {"Index": [["contents"]], sheet: [
        ["Table 4.3 Nominal gross domestic product"],
        ["Category", "2005", "2015", "2025", "2035"],
        [label, 13039.2 * scale, y2015 * scale, y2025 * scale, 46950.8 * scale],
        ["  Personal consumption expenditures", 1, 2, 3, 4],
    ]})


# BEA history as CMS prints it: 2015 equals Table 4.3's 2015 (one vintage), 2025 near BLS's.
_GDP = {y: round(18295.0 * 1.054 ** (y - 2015), 1) for y in range(2013, 2035)}


def _cms_table(title: str, total_col: int, values: Dict[int, float]) -> List[List[Any]]:
    rows: List[List[Any]] = [[title], ["", "", "", "Health Insurance1"],
                             ["Year ", "Total", "Out-of-Pocket Payments", "Total", "Private"],
                             ["Historical Estimates", "Amount in Billions"]]
    for y in range(2018, 2035):
        if y == 2025:
            rows.append(["Projected"])
        row = [y, 9.0, 1.0, 8.0, 4.0]
        row[total_col] = values[y]
        rows.append(row)
    rows.append(["Historical Estimates", "Per Capita Amount"])
    rows.append([2018, 1, 1, "—", "—"])
    return rows


def _cms_dir(tmp: Path, rx_gap_year: bool = False) -> Path:
    d = tmp / "cms"
    d.mkdir(exist_ok=True)
    t1 = [["Table 1"], [], ["Item"] + list(range(2013, 2035)), ["Amount in Billions"],
          ["National Health Expenditures"] + [1.0] * 22,
          ["Gross Domestic Product"] + [_GDP[y] for y in range(2013, 2035)], ["Level"],
          ["Gross Domestic Product"] + [-1.0] * 22]
    _xlsx(d / "Table01 National Health Expenditures.xlsx", {"Table 1": t1})
    hi = _grow(2627.7, 0.067, list(range(2018, 2035)))
    rx = _grow(321.3, 0.082, list(range(2018, 2035)))
    if rx_gap_year:
        rx.pop(2030)
        rx[2030] = None  # type: ignore[assignment]
    _xlsx(d / "Table03 Sources of Funds.xlsx", {"Table3": _cms_table("Table 3", 3, hi)})
    _xlsx(d / "Table11 Prescription Drug Expenditures.xlsx", {"Table11": _cms_table("Table 11", 1, rx)})
    return d


_INDUSTRIES = {
    "Software - Infrastructure": {"tier": "bls_output", "naics_2017": ["5112"], "bls_sectors": [94],
                                  "kind": "exact"},
    "Medical - Distribution": {"tier": "cms_nhe", "naics_2017": ["4242"], "bls_sectors": [80],
                               "kind": "margin", "cms_series": "rx_retail"},
    "Restaurants": {"tier": "gdp_path", "naics_2017": ["7225"], "bls_sectors": [155, 156],
                    "kind": "proxy"},
}


def _census_dir(tmp: Path) -> Path:
    d = tmp / "fixtures"
    d.mkdir(exist_ok=True)
    def k(base: float, rate: float) -> Dict[str, float]:
        return {"ecn_2017_thousands": base, "aies_2024_thousands": round(base * (1 + rate) ** 7)}
    doc = {"census": {"5112": k(250e6, 0.10),                # BLS 94 also +10%/yr → BLS
                      "4242": k(931e6, 0.076),               # BLS 80 +5% → no; CMS matched → yes
                      "7225": k(600e6, 0.054)},              # BLS +9% → no; GDP +5.4% → GDP
           "census_extra_years": {"4242": {"aies_2018_thousands": round(931e6 * 1.076)}}}
    (d / "verified_synthetic.json").write_text(json.dumps(doc))
    return d


def _build(tmp: Path, **over) -> dict:
    bls_dir = over.pop("bls_dir", None) or _bls_dir(tmp)
    cms_dir = over.pop("cms_dir", None) or _cms_dir(tmp)
    bls, base, proj = refresh.read_bls_output(bls_dir)
    first_proj, _ = refresh._projected_years(cms_dir)
    return refresh.build_table(
        industries=over.pop("industries", _INDUSTRIES), bls=bls, bls_base=base, bls_proj=proj,
        titles=refresh.read_sector_titles(bls_dir), gdp_bls=refresh.read_bls_gdp(_agg(tmp)),
        cms_gdp=refresh.read_cms_gdp_history(cms_dir),
        cms={k: refresh.read_cms_series(cms_dir, k) for k in refresh.CMS_SERIES},
        cms_first_projected=first_proj, bls_released="2026-08-27", cms_released="2026-06-24",
        built_on="2026-10-22", **over)


# ── Tests ─────────────────────────────────────────────────────────────────────────

def test_the_xlsx_reader_reads_inline_shared_and_number_cells(tmp_path):
    path = _xlsx(tmp_path / "t.xlsx", {"S": [["@shared:hello", "inline", 1.5, None, 7]]})
    assert refresh.read_xlsx(path) == {"S": [["hello", "inline", 1.5, None, 7.0]]}


def test_a_build_from_good_sources_validates_and_keeps_the_industries(tmp_path):
    doc = _build(tmp_path)
    assert validate_table(doc) == []
    assert doc["industries"] == _INDUSTRIES
    assert doc["sources"]["bls_output"]["program"] == "Employment Projections 2025–35"
    assert doc["sources"]["cms_nhe"]["program"] == "National Health Expenditure Projections 2025–34"
    assert doc["gdp"]["history"]["2024"] == _GDP[2024] and "2025" not in doc["gdp"]["history"]
    assert doc["gdp"]["base_value"] == 30779 and doc["gdp"]["projection_value"] == 46950.8
    assert doc["gdp"]["vintage_overlap"] == {"2015": {"bls": 18295.0, "cms": 18295.0}}
    assert set(doc["bls_sectors"]) == {"80", "94", "155", "156"}
    assert doc["cms_series"]["health_insurance"]["values"]["2018"] == 2627.7
    p = project_with_table(doc, industry="Software - Infrastructure", measured_tam_b=500.0,
                           measured_year=2024, start_year=2026)
    assert p is not None and p.rate_pct == pytest.approx(((1.5) ** 0.1 - 1) * 100, abs=0.05)
    # The rendered text round-trips through the strict parser.
    assert parse_table_text(refresh.render(doc)) == doc


def test_the_rule_on_synthetic_census(tmp_path):
    doc = _build(tmp_path)
    census = refresh.load_census_fixtures(_census_dir(tmp_path))
    assert {n: refresh.rule_for_industry(doc, n, census)[0] for n in _INDUSTRIES} == {
        "Software - Infrastructure": "bls_output", "Medical - Distribution": "cms_nhe",
        "Restaurants": "gdp_path"}
    assert refresh.tier_changes(doc, census) == []
    moved = json.loads(json.dumps(doc))
    moved["industries"]["Restaurants"]["tier"] = "bls_output"
    assert any("Restaurants" in c for c in refresh.tier_changes(moved, census))


def test_a_2025_mismatch_between_the_bls_files_is_refused(tmp_path):
    with pytest.raises(refresh.RefreshError, match="differs between the history"):
        refresh.read_bls_output(_bls_dir(tmp_path, proj_2025_drift=5.0))


def test_a_renumbered_sector_is_refused(tmp_path):
    with pytest.raises(refresh.RefreshError, match="renumbered"):
        _build(tmp_path, old_titles={"94": "Something else entirely"})


def test_a_missing_table_4_3_is_refused(tmp_path):
    with pytest.raises(refresh.RefreshError, match="Table 4.3"):
        refresh.read_bls_gdp(_agg(tmp_path, sheet="Table 9.9"))


def test_a_gap_in_a_cms_series_is_refused(tmp_path):
    with pytest.raises(refresh.RefreshError):
        refresh.read_cms_series(_cms_dir(tmp_path, rx_gap_year=True), "rx_retail")


def test_an_unknown_sector_is_refused(tmp_path):
    inds = {**_INDUSTRIES, "Extra": {"tier": "bls_output", "naics_2017": ["9999"],
                                     "bls_sectors": [777], "kind": "exact"}}
    with pytest.raises(refresh.RefreshError, match="777"):
        _build(tmp_path, industries=inds)


def _args(tmp: Path, *extra: str) -> List[str]:
    seed = tmp / "seed.json"
    seed.write_text(json.dumps(_INDUSTRIES))
    return ["--bls-dir", str(_bls_dir(tmp)), "--bls-agg", str(_agg(tmp)), "--cms-dir", str(_cms_dir(tmp)),
            "--bls-released", "2026-08-27", "--cms-released", "2026-06-24",
            "--seed-industries", str(seed), "--start-year", "2026", *extra]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    table = tmp_path / "out" / "industry_tam_projections.json"
    table.parent.mkdir(exist_ok=True)
    monkeypatch.setattr(refresh, "TABLE_PATH", table)
    monkeypatch.setattr(refresh, "FIXTURE_DIR", _census_dir(tmp_path))
    return table


def test_a_dry_run_writes_nothing(tmp_path, isolated):
    assert refresh.main(_args(tmp_path)) == 0
    assert not isolated.exists()


def test_write_replaces_the_table_atomically(tmp_path, isolated):
    assert refresh.main(_args(tmp_path, "--write")) == 0
    doc = parse_table_text(isolated.read_text(encoding="utf-8"))
    assert validate_table(doc) == [] and doc["industries"] == _INDUSTRIES
    assert not [p for p in isolated.parent.iterdir() if p.name.startswith(".industry_tam_projections.")]


def test_a_tier_change_is_refused_without_the_flag(tmp_path, isolated):
    seed = dict(_INDUSTRIES)
    seed["Restaurants"] = {**seed["Restaurants"], "tier": "measured_only"}
    args = _args(tmp_path, "--write")
    (tmp_path / "seed.json").write_text(json.dumps(seed))
    i = args.index("--seed-industries")
    args[i + 1] = str(tmp_path / "seed2.json")
    (tmp_path / "seed2.json").write_text(json.dumps(seed))
    assert refresh.main(args) == 2 and not isolated.exists()
    assert refresh.main(args + ["--accept-tier-changes"]) == 0
    assert parse_table_text(isolated.read_text())["industries"]["Restaurants"]["tier"] == "gdp_path"


def test_bad_inputs_fail_with_exit_1_and_write_nothing(tmp_path, isolated):
    args = _args(tmp_path, "--write")
    args[args.index("--bls-released") + 1] = "late August"
    assert refresh.main(args) == 1 and not isolated.exists()


def test_a_footnoted_gdp_label_is_read(tmp_path):
    """The 2024–34 release labelled the row 'Gross Domestic Product[1]' (review 2026-10-22)."""
    assert refresh.read_bls_gdp(_agg(tmp_path, label="Gross Domestic Product[1]"))[1] == 30779


@pytest.mark.parametrize("over, match", [
    ({"scale": 0.001}, "unit or column change|different BEA vintages"),   # Table 4.3 in trillions
    ({"y2025": 36000.0}, "unit or column change"),         # the base year alone is off (splice)
    ({"y2015": 18000.0}, "different BEA vintages"),        # the two tables disagree on history
])
def test_a_gdp_splice_that_does_not_line_up_is_refused(tmp_path, over, match):
    bls_dir, cms_dir = _bls_dir(tmp_path), _cms_dir(tmp_path)
    bls, base, proj = refresh.read_bls_output(bls_dir)
    with pytest.raises(refresh.RefreshError, match=match):
        refresh.build_table(
            industries=_INDUSTRIES, bls=bls, bls_base=base, bls_proj=proj,
            titles=refresh.read_sector_titles(bls_dir), gdp_bls=refresh.read_bls_gdp(_agg(tmp_path, **over)),
            cms_gdp=refresh.read_cms_gdp_history(cms_dir),
            cms={k: refresh.read_cms_series(cms_dir, k) for k in refresh.CMS_SERIES},
            cms_first_projected=2025, bls_released="2026-08-27", cms_released="2026-06-24",
            built_on="2026-10-22")


def test_cms_tables_that_no_longer_start_in_the_rule_window_are_refused(tmp_path):
    cms = {k: {y: v for y, v in vals.items() if y >= 2019}
           for k, vals in ((k, refresh.read_cms_series(_cms_dir(tmp_path), k)) for k in refresh.CMS_SERIES)}
    bls_dir = _bls_dir(tmp_path)
    bls, base, proj = refresh.read_bls_output(bls_dir)
    with pytest.raises(refresh.RefreshError, match="CMS_RULE_WINDOW"):
        refresh.build_table(
            industries=_INDUSTRIES, bls=bls, bls_base=base, bls_proj=proj,
            titles=refresh.read_sector_titles(bls_dir), gdp_bls=refresh.read_bls_gdp(_agg(tmp_path)),
            cms_gdp=refresh.read_cms_gdp_history(tmp_path / "cms"), cms=cms, cms_first_projected=2025,
            bls_released="2026-08-27", cms_released="2026-06-24", built_on="2026-10-22")


def test_composites_are_reweighted_from_the_newest_verified_year():
    inds = {"Combo": {"tier": "bls_output", "naics_2017": ["1111", "2222"], "kind": "proxy",
                      "composite": {"weights_year": 2024, "codes": {
                          "1111": {"census_thousands": 1.0, "bls_sectors": [94]},
                          "2222": {"census_thousands": 2.0, "bls_sectors": [80]}}}}}
    census = {"1111": {2017: 5.0, 2024: 10.0, 2025: 11.0}, "2222": {2017: 7.0, 2024: 20.0, 2025: 21.0}}
    out, notes = refresh.reweight_composites(inds, census)
    comp = out["Combo"]["composite"]
    assert comp["weights_year"] == 2025
    assert {c: p["census_thousands"] for c, p in comp["codes"].items()} == {"1111": 11, "2222": 21}
    assert notes == ["Combo: composite weights 2024 → 2025"]
    assert inds["Combo"]["composite"]["weights_year"] == 2024          # input untouched
    census["2222"].pop(2025)                                            # 2025 not complete
    assert refresh.reweight_composites(inds, census)[0]["Combo"]["composite"]["weights_year"] == 2024
    with pytest.raises(refresh.RefreshError):
        refresh.reweight_composites(inds, {"1111": {2024: 1.0}})


def test_the_preview_shows_the_accepted_tiers(tmp_path, isolated, caplog):
    seed = dict(_INDUSTRIES, Restaurants={**_INDUSTRIES["Restaurants"], "tier": "measured_only"})
    args = _args(tmp_path, "--accept-tier-changes")
    path = tmp_path / "seed_moved.json"
    path.write_text(json.dumps(seed))
    args[args.index("--seed-industries") + 1] = str(path)
    import logging
    caplog.set_level(logging.INFO, logger="refresh_industry_tam_projections")
    assert refresh.main(args) == 0 and not isolated.exists()          # dry run
    preview = [r.getMessage() for r in caplog.records if "Restaurants" in r.getMessage()
               and "measured" in r.getMessage() and "TIER" not in r.getMessage()]
    assert preview and "gdp_path" in preview[-1] and "new —" not in preview[-1]


def _existing_table(tmp_path, isolated, industries) -> bytes:
    """Write a valid table to the isolated TABLE_PATH (the owner's normal starting point)."""
    assert refresh.main(_args(tmp_path, "--write")) == 0
    doc = parse_table_text(isolated.read_text(encoding="utf-8"))
    doc["industries"] = industries
    isolated.write_text(refresh.render(doc), encoding="utf-8")
    return isolated.read_bytes()


def _no_seed(tmp_path, *extra):
    args = _args(tmp_path, *extra)
    i = args.index("--seed-industries")
    return args[:i] + args[i + 2:]


def test_the_owners_normal_run_refuses_a_renumbered_sector(tmp_path, isolated, monkeypatch):
    before = _existing_table(tmp_path, isolated, dict(_INDUSTRIES))
    doc = parse_table_text(before.decode())
    doc["bls_sectors"]["94"]["title"] = "Something else entirely"
    isolated.write_text(refresh.render(doc), encoding="utf-8")
    before = isolated.read_bytes()
    assert refresh.main(_no_seed(tmp_path, "--write")) == 1
    assert isolated.read_bytes() == before


@pytest.mark.parametrize("exception_tier, pinned", [
    ("measured_only", "bls_output"),   # the rule now gives gdp_path, not the exception's tier
    ("gdp_path", "gdp_path"),          # the exception is no longer needed (rule = pinned)
])
def test_a_stale_owner_exception_is_refused_even_with_the_flag(tmp_path, isolated, exception_tier, pinned):
    inds = dict(_INDUSTRIES, Restaurants={**_INDUSTRIES["Restaurants"], "tier": pinned, "exception": {
        "rule_tier": exception_tier, "owner_decision": "test", "date": "2026-10-22"}})
    before = _existing_table(tmp_path, isolated, inds)
    for extra in ((), ("--accept-tier-changes",)):
        assert refresh.main(_no_seed(tmp_path, "--write", *extra)) == 2
        assert isolated.read_bytes() == before
