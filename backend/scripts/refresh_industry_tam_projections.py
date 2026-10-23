#!/usr/bin/env python3
"""
Refresh the Moat card's projection table (`backend/data/industry_tam_projections.json`) — OWNER-RUN.
====================================================================================================

The Moat card's market row scales each industry's MEASURED US Census AIES revenue to the current
year and +5 along a public-domain level path (owner decisions 2026-10-22;
`app/services/industry_tam_projection.py`). This script rebuilds the paths from the agencies' own
files and re-runs the tier rule. It writes nothing unless asked.

Sources — download them by hand (bls.gov refuses scripted downloads), each into its own folder:

* BLS Employment Projections, industry output and employment —
  https://www.bls.gov/emp/data/industry-out-and-emp.htm → `industry.zip`, unzipped
  (`ind20YY.csv`, `proj20ZZ.csv`, `SectorPlan20ZZ.xlsx`). Series 1 = domestic industry output,
  millions of current dollars.
* BLS aggregate-economy tables — https://www.bls.gov/emp/tables.htm → the workbook holding
  "Table 4.3 Nominal gross domestic product by major demand category".
* CMS National Health Expenditure projections —
  https://www.cms.gov/data-research/statistics-trends-and-reports/national-health-expenditure-data/projected
  → the tables zip, unzipped (Table 01, Table 03, Table 11 workbooks).

The tier rule (`tier_rule` in the table): for each industry, the most specific source whose
2017–24 growth stayed within 2 pts/yr of Census's — BLS sector output, then CMS (matched 2018–24
window), then US nominal GDP — else measured only. Census figures come from the verified fixtures
under `tests/fixtures/industry_tam/`. A tier change, or an owner exception the data no longer
needs, is an owner decision: the script refuses `--write` unless `--accept-tier-changes`.

The curated `industries` block (tier, NAICS 2017 codes, BLS sectors, kind, CMS series,
exceptions) is KEPT from the current file; the paths, sources and `built_on` are rebuilt, and each
composite is RE-WEIGHTED from the newest verified Census year covering all its codes (when AIES
publishes a new year, first add its verified figures to `tests/fixtures/industry_tam/` — the
runtime projects a composite only when its weights are for the dossier's measured year).
`--seed-industries FILE` supplies the block for a first build.

GDP path checks: BLS Table 4.3 and CMS Table 1 must print identical figures for every year both
print (one BEA vintage), and CMS's base-year figure must be within 3% of BLS's (a unit or column
change); either failing refuses the build.

Usage (from backend/):
    ./venv/bin/python -m scripts.refresh_industry_tam_projections \
        --bls-dir ~/Downloads/bls_industry --bls-agg ~/Downloads/aggregate-economy-tables.xlsx \
        --cms-dir ~/Downloads/nhe_tables --bls-released 2027-08-26 --cms-released 2027-06-18   # dry run
    … --write                                                                                   # replace
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from app.services.industry_tam_projection import (
    BASIS_BLS,
    BASIS_CMS,
    BASIS_GDP,
    TIER_MEASURED,
    parse_table_text,
    project_with_table,
    validate_table,
)

logger = logging.getLogger("refresh_industry_tam_projections")

_BACKEND = Path(__file__).resolve().parents[1]
TABLE_PATH = _BACKEND / "data" / "industry_tam_projections.json"
FIXTURE_DIR = _BACKEND / "tests" / "fixtures" / "industry_tam"

RULE_WINDOW = (2017, 2024)        # Census 2017 Economic Census → AIES 2024
CMS_RULE_WINDOW = (2018, 2024)    # CMS NHE tables start in 2018
MAX_GAP_PTS = 2.0
# The GDP path splices CMS Table 1 history onto BLS Table 4.3's base year. Both print BEA data of
# the same vintage when they agree on every year they both print (exact to the printed digit), and
# CMS's own figure for the base year is within a few percent of BLS's — a unit change (trillions)
# or a wrong column (a percent distribution) is off by orders of magnitude.
GDP_OVERLAP_TOLERANCE = 0.001
GDP_SPLICE_TOLERANCE = 0.03

BLS_PAGE_URL = "https://www.bls.gov/emp/data/industry-out-and-emp.htm"
BLS_GDP_URL = "https://www.bls.gov/emp/tables/nominal-gdp-major-demand-category.htm"
CMS_URL = ("https://www.cms.gov/data-research/statistics-trends-and-reports/"
           "national-health-expenditure-data/projected")

# The CMS series the industries may use: key → (table file prefix, column title, display title).
CMS_SERIES = {
    "rx_retail": ("Table11", "Total", "retail prescription drug spending"),
    "health_insurance": ("Table03", "Health Insurance — Total", "health insurance spending"),
}

ABOUT = (
    "Public-domain projections that move the Moat card's MEASURED US Census AIES industry revenue "
    "to the current year and +5 (owner decisions 2026-10-22). Census gives the measured value; "
    "these series only scale it: value(Y) = measured × S(Y)/S(measured year). Tier rule: the most "
    "specific source whose 2017–24 growth tracked Census within 2 pts/yr (BLS sector output → CMS "
    "health spending → US nominal GDP → measured only). Refresh yearly with "
    "backend/scripts/refresh_industry_tam_projections.py (owner-run). US government works, public "
    "domain. Source: U.S. Bureau of Labor Statistics; Centers for Medicare & Medicaid Services "
    "(Office of the Actuary); GDP history: U.S. Bureau of Economic Analysis via CMS."
)


class RefreshError(RuntimeError):
    """The sources cannot be turned into a valid table; nothing is written."""


# --------------------------------------------------------------------------------------
# A minimal .xlsx reader (stdlib only: zipfile + ElementTree). openpyxl is not a backend
# dependency and the runtime never reads a workbook.
# --------------------------------------------------------------------------------------

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path: Path) -> Dict[str, List[List[Any]]]:
    """Every sheet of `path` as a list of rows (cell values: str, float, or None)."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise RefreshError(f"{path}: not a readable .xlsx ({type(e).__name__}: {e})") from e
    with zf:
        names = set(zf.namelist())
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        out: Dict[str, List[List[Any]]] = {}
        sheets = wb.find("m:sheets", _NS)
        for sheet in (list(sheets) if sheets is not None else []):
            target = targets.get(sheet.get(_REL_NS)) or ""
            member = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if member not in names:
                raise RefreshError(f"{path}: sheet {sheet.get('name')!r} points at a missing part")
            out[sheet.get("name")] = _read_sheet(zf.read(member), shared)
        return out


def _read_sheet(xml: bytes, shared: Sequence[str]) -> List[List[Any]]:
    root = ET.fromstring(xml)
    rows: Dict[int, Dict[int, Any]] = {}
    for c in root.iter(f"{{{_NS['m']}}}c"):
        ref = _CELL_REF.fullmatch(c.get("r") or "")
        if ref is None:
            continue
        col, row = _col_index(ref.group(1)), int(ref.group(2)) - 1
        t = c.get("t")
        v = c.find("m:v", _NS)
        if t == "s" and v is not None:
            value: Any = shared[int(v.text)]
        elif t == "inlineStr":
            value = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
        elif t in ("str", "e") and v is not None:
            value = v.text
        elif t == "b" and v is not None:
            value = v.text == "1"
        elif v is not None and v.text is not None:
            value = float(v.text)
        else:
            value = None
        rows.setdefault(row, {})[col] = value
    if not rows:
        return []
    width = max(max(r) for r in rows.values()) + 1
    return [[rows.get(i, {}).get(j) for j in range(width)] for i in range(max(rows) + 1)]


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _year(v: Any) -> Optional[int]:
    n = _num(v)
    if n is not None and n.is_integer() and 1990 <= n <= 2100:
        return int(n)
    return None


def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


# --------------------------------------------------------------------------------------
# Source readers
# --------------------------------------------------------------------------------------

def _one(folder: Path, pattern: str) -> Path:
    hits = sorted(folder.glob(pattern))
    if len(hits) != 1:
        raise RefreshError(f"{folder}: expected exactly one {pattern!r}, found {[h.name for h in hits]}")
    return hits[0]


def read_bls_output(bls_dir: Path) -> Tuple[Dict[int, Dict[int, float]], int, int]:
    """Series 1 (domestic output, $M current) per sector: history years + the projection year.
    Returns (sector → {year → value}, base_year, projection_year)."""
    hist_path, proj_path = _one(bls_dir, "ind20[0-9][0-9].csv"), _one(bls_dir, "proj20[0-9][0-9].csv")
    out: Dict[int, Dict[int, float]] = {}
    with hist_path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if (row.get("SERIESNUMBER") or "").strip() != "1":
                continue
            sector = int(row["SECTORNUMBER"])
            out[sector] = {int(k): float(v) for k, v in row.items()
                           if k and k.strip().isdigit() and v not in (None, "")}
    if not out:
        raise RefreshError(f"{hist_path.name}: no series-1 rows (layout changed?)")
    base_year = max(max(v) for v in out.values())
    with proj_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        years = sorted(int(k) for k in (reader.fieldnames or []) if k and k.strip().isdigit())
        if len(years) != 2 or years[0] != base_year:
            raise RefreshError(f"{proj_path.name}: expected columns {base_year} and the projection "
                               f"year, found {years}")
        proj_year = years[1]
        for row in reader:
            if (row.get("SERIESNUMBER") or "").strip() != "1":
                continue
            sector = int(row["SECTORNUMBER"])
            if sector not in out:
                raise RefreshError(f"{proj_path.name}: sector {sector} has no history row")
            base_val, proj_val = float(row[str(base_year)]), float(row[str(proj_year)])
            if abs(base_val - out[sector][base_year]) > 0.05:
                raise RefreshError(f"sector {sector}: {base_year} differs between the history "
                                   f"({out[sector][base_year]}) and projection ({base_val}) files")
            out[sector][proj_year] = proj_val
    missing = [s for s, v in out.items() if proj_year not in v]
    if missing:
        raise RefreshError(f"sectors with no projection row: {missing[:10]}")
    return out, base_year, proj_year


def read_sector_titles(bls_dir: Path) -> Dict[int, Tuple[str, str]]:
    """Sector number → (NAICS 2022 codes, title) from the sector plan's "Stubs" sheet."""
    book = read_xlsx(_one(bls_dir, "SectorPlan20[0-9][0-9].xlsx"))
    rows = book.get("Stubs")
    if not rows:
        raise RefreshError("SectorPlan: no 'Stubs' sheet (layout changed?)")
    header = [_text(v) for v in rows[0]]
    try:
        i_num, i_naics, i_title = (header.index("Sector Number"), header.index("NAICS_2022"),
                                   header.index("Sector Title"))
    except ValueError as e:
        raise RefreshError(f"SectorPlan 'Stubs' header changed: {header}") from e
    out: Dict[int, Tuple[str, str]] = {}
    for row in rows[1:]:
        num = _num(row[i_num])
        if num is None or not num.is_integer():
            continue
        naics = row[i_naics]
        naics_txt = str(int(naics)) if isinstance(naics, float) and naics.is_integer() else _text(naics)
        out[int(num)] = (naics_txt, _text(row[i_title]))
    return out


_FOOTNOTE = re.compile(r"(?:\[\d+\]|\(\d+\)|\d+)$")


def _row_label(v: Any) -> str:
    """A row label without a trailing footnote marker ("Gross Domestic Product[1]")."""
    return _FOOTNOTE.sub("", _text(v)).strip().lower()


def read_bls_gdp(agg_path: Path) -> Tuple[int, float, int, float, Dict[int, float]]:
    """(base_year, nominal GDP $B, projection_year, nominal GDP $B, {every printed year: $B})
    from BLS Table 4.3 — the earlier columns are BEA history, used for the vintage check."""
    book = read_xlsx(agg_path)
    sheet = next((rows for name, rows in book.items() if name.strip() == "Table 4.3"), None)
    if sheet is None:
        raise RefreshError(f"{agg_path.name}: no 'Table 4.3' sheet")
    header = next((r for r in sheet if _text(r[0]) == "Category"), None)
    gdp_row = next((r for r in sheet if _row_label(r[0]) == "gross domestic product"), None)
    if header is None or gdp_row is None:
        raise RefreshError("Table 4.3: header or 'Gross Domestic Product' row not found")
    cols = [(j, _year(v)) for j, v in enumerate(header) if _year(v) is not None]
    if len(cols) < 2:
        raise RefreshError(f"Table 4.3: year columns not found in {header}")
    values = {y: _num(gdp_row[j]) for j, y in cols if j < len(gdp_row)}
    if any(v is None or v <= 0 for v in values.values()):
        raise RefreshError("Table 4.3: GDP values not positive numbers")
    (_, base), (_, proj) = cols[-2], cols[-1]
    return base, values[base], proj, values[proj], values   # type: ignore[return-value]


def _cms_book(cms_dir: Path, prefix: str) -> List[List[Any]]:
    book = read_xlsx(_one(cms_dir, f"{prefix}*.xlsx"))
    if len(book) != 1:
        raise RefreshError(f"{prefix}: expected one sheet, found {list(book)}")
    return next(iter(book.values()))


def _amount_block(rows: List[List[Any]], where: str) -> List[List[Any]]:
    """The 'Amount in Billions' year rows (historical + projected) of a CMS source-of-funds table."""
    out: List[List[Any]] = []
    started = False
    for r in rows:
        label, second = _text(r[0]), _text(r[1]) if len(r) > 1 else ""
        if second.lower().startswith("amount in billions"):
            started = True
            continue
        if started and second.lower().startswith("per capita"):
            break
        if started and _year(r[0]) is not None:
            out.append(r)
    if not out:
        raise RefreshError(f"{where}: no 'Amount in Billions' rows (layout changed?)")
    return out


def read_cms_series(cms_dir: Path, key: str) -> Dict[int, float]:
    prefix, column, _title = CMS_SERIES[key]
    rows = _cms_book(cms_dir, prefix)
    group_row = next((r for r in rows[:6] if any(_text(v).startswith("Health Insurance") for v in r)), None)
    title_row = next((r for r in rows[:6] if _text(r[0]).lower().startswith("year")), None)
    if title_row is None:
        raise RefreshError(f"{prefix}: header row not found")
    if column == "Total":
        j = next((i for i, v in enumerate(title_row) if _text(v) == "Total"), None)
    else:   # "Health Insurance — Total": the Total under the "Health Insurance" group heading
        if group_row is None:
            raise RefreshError(f"{prefix}: 'Health Insurance' group heading not found")
        g = next(i for i, v in enumerate(group_row) if _text(v).startswith("Health Insurance"))
        j = g if _text(title_row[g]) == "Total" else None
    if j is None:
        raise RefreshError(f"{prefix}: column {column!r} not found in {title_row}")
    values = {_year(r[0]): _num(r[j]) for r in _amount_block(rows, prefix)}
    if any(v is None or v <= 0 for v in values.values()):
        raise RefreshError(f"{prefix}: non-positive or missing values in {column!r}")
    years = sorted(values)
    if years != list(range(years[0], years[-1] + 1)):
        raise RefreshError(f"{prefix}: years not contiguous: {years}")
    return values   # type: ignore[return-value]


def read_cms_gdp_history(cms_dir: Path) -> Dict[int, float]:
    """US nominal GDP ($B, BEA data) from CMS NHE Table 1's 'Amount in Billions' block."""
    rows = _cms_book(cms_dir, "Table01")
    header = next((r for r in rows[:6] if _text(r[0]) == "Item"), None)
    if header is None:
        raise RefreshError("Table01: 'Item' header row not found")
    cols = [(j, _year(v)) for j, v in enumerate(header) if _year(v) is not None]
    in_amounts = False
    for r in rows:
        label = _text(r[0])
        if label.lower().startswith("amount in billions"):
            in_amounts = True
            continue
        if in_amounts and label.lower() == "level":
            break
        if in_amounts and label == "Gross Domestic Product":
            out = {y: _num(r[j]) for j, y in cols}
            if any(v is None or v <= 0 for v in out.values()):
                raise RefreshError("Table01: a GDP value is not a positive number")
            return out   # type: ignore[return-value]
    raise RefreshError("Table01: GDP row not found in the 'Amount in Billions' block")


def _projected_years(cms_dir: Path) -> Tuple[int, int]:
    """(first projected year, last year) — the year after the last 'Historical Estimates' row."""
    rows = _cms_book(cms_dir, "Table03")
    hist_last = None
    for r in rows:
        if _text(r[0]).lower() == "projected":
            break
        y = _year(r[0])
        if y is not None:
            hist_last = y
    values = read_cms_series(cms_dir, "health_insurance")
    if hist_last is None:
        raise RefreshError("Table03: no historical rows before 'Projected'")
    return hist_last + 1, max(values)


# --------------------------------------------------------------------------------------
# Build + tier rule
# --------------------------------------------------------------------------------------

def _sectors_used(industries: Mapping[str, Any]) -> List[int]:
    used = set()
    for ind in industries.values():
        used.update(ind.get("bls_sectors") or [])
        for part in (ind.get("composite") or {}).get("codes", {}).values():
            used.update(part.get("bls_sectors") or [])
    return sorted(used)


def build_table(
    *,
    industries: Mapping[str, Any],
    bls: Mapping[int, Mapping[int, float]],
    bls_base: int,
    bls_proj: int,
    titles: Mapping[int, Tuple[str, str]],
    gdp_bls: Tuple[int, float, int, float],
    cms_gdp: Mapping[int, float],
    cms: Mapping[str, Mapping[int, float]],
    cms_first_projected: int,
    bls_released: str,
    cms_released: str,
    built_on: str,
    old_titles: Optional[Mapping[str, str]] = None,
    horizon_years: int = 5,
) -> Dict[str, Any]:
    first = RULE_WINDOW[0]
    g_base, g_base_val, g_proj, g_proj_val, bls_gdp_years = gdp_bls
    if g_base != bls_base or g_proj != bls_proj:
        raise RefreshError(f"Table 4.3 years {g_base}/{g_proj} differ from the industry files' "
                           f"{bls_base}/{bls_proj}")
    sectors: Dict[str, Any] = {}
    for s in _sectors_used(industries):
        if s not in bls:
            raise RefreshError(f"BLS sector {s} (used by an industry) is not in the output files")
        if s not in titles:
            raise RefreshError(f"BLS sector {s} has no title in the sector plan")
        naics, title = titles[s]
        if old_titles and str(s) in old_titles and old_titles[str(s)] != title:
            raise RefreshError(f"BLS sector {s} is now titled {title!r} (was {old_titles[str(s)]!r}): "
                               "sectors were renumbered — re-map the industries by hand")
        series = bls[s]
        sectors[str(s)] = {
            "title": title,
            "naics_2022": naics,
            "output": {str(y): series[y] for y in list(range(first, bls_base + 1)) + [bls_proj]},
        }
    cms_last = min(max(v) for v in cms.values())
    cms_first = max(min(v) for v in cms.values())
    if cms_first > CMS_RULE_WINDOW[0]:
        raise RefreshError(
            f"the CMS tables now start in {cms_first}, after the tier rule's CMS window start "
            f"{CMS_RULE_WINDOW[0]}: move CMS_RULE_WINDOW to {cms_first}-{CMS_RULE_WINDOW[1]} and add "
            f"the verified Census {cms_first} figure for every CMS-tier code to "
            "tests/fixtures/industry_tam (ask Claude), then rerun")
    overlap = {y: (bls_gdp_years[y], cms_gdp[y]) for y in sorted(bls_gdp_years)
               if y < min(bls_base, cms_first_projected) and y in cms_gdp}
    if not overlap:
        raise RefreshError("no year printed by both BLS Table 4.3 and CMS Table 1: the GDP path's "
                           "vintage cannot be checked")
    for y, (b_val, c_val) in overlap.items():
        if abs(b_val - c_val) > GDP_OVERLAP_TOLERANCE * b_val:
            raise RefreshError(f"GDP {y}: BLS prints {b_val}, CMS {c_val} — the two tables carry "
                               "different BEA vintages; wait for both to catch up")
    cms_base = cms_gdp.get(g_base)
    if cms_base is None or abs(cms_base - g_base_val) > GDP_SPLICE_TOLERANCE * g_base_val:
        raise RefreshError(f"GDP {g_base}: BLS Table 4.3 says {g_base_val}, CMS Table 1 {cms_base} — "
                           "a unit or column change (the splice would scale every GDP-tier industry)")
    bls_prog = f"Employment Projections {bls_base}–{bls_proj % 100:02d}"
    cms_prog = f"National Health Expenditure Projections {cms_first_projected}–{cms_last % 100:02d}"
    # BEA history only: CMS's own projected GDP years never stand in for a measured year.
    gdp_hist = {str(y): cms_gdp[y] for y in range(first, min(bls_base, cms_first_projected)) if y in cms_gdp}
    doc: Dict[str, Any] = {
        "_about": ABOUT,
        "schema_version": 1,
        "built_on": built_on,
        "horizon_years": horizon_years,
        "tier_rule": {"window": list(RULE_WINDOW), "cms_window": list(CMS_RULE_WINDOW),
                      "max_gap_pts": MAX_GAP_PTS,
                      "order": [BASIS_BLS, BASIS_CMS, BASIS_GDP, TIER_MEASURED]},
        "sources": {
            BASIS_BLS: {
                "publisher": "U.S. Bureau of Labor Statistics", "program": bls_prog,
                "released": bls_released, "url": BLS_PAGE_URL,
                "series": "1 = domestic industry output, millions of current dollars",
                "history_first_year": first, "base_year": bls_base, "projection_year": bls_proj,
                "path": "history through base_year, then the constant compound rate to "
                        "projection_year (BLS publishes no intermediate years)",
                "caption": f"BLS {bls_prog}, industry output",
            },
            BASIS_CMS: {
                "publisher": "Centers for Medicare & Medicaid Services (Office of the Actuary)",
                "program": cms_prog, "released": cms_released, "url": CMS_URL,
                "unit": "billions of current dollars", "first_year": cms_first, "last_year": cms_last,
                "path": "annual values as published",
                "caption": f"CMS {cms_prog}",
            },
            BASIS_GDP: {
                "history": "US nominal GDP as printed in CMS NHE Table 1 (BEA data)",
                "base_and_rate": f"BLS {bls_prog}, Table 4.3", "url": BLS_GDP_URL,
                "note": "one vintage per path; a newer BEA release waits for the next refresh",
                "caption": f"US nominal GDP (BEA history via CMS), BLS {bls_prog}",
            },
        },
        "bls_sectors": sectors,
        "cms_series": {
            key: {"table": CMS_SERIES[key][0].replace("Table", "Table "),
                  "column": CMS_SERIES[key][1], "title": CMS_SERIES[key][2],
                  "values": {str(y): v for y, v in sorted(vals.items())}}
            for key, vals in cms.items()
        },
        "gdp": {
            "history": gdp_hist,
            "base_year": g_base, "base_value": g_base_val,
            "projection_year": g_proj, "projection_value": g_proj_val,
            "cms_base_year_value": cms_base,
            "vintage_overlap": {str(y): {"bls": b, "cms": c} for y, (b, c) in overlap.items()},
        },
        "industries": {k: dict(v) for k, v in industries.items()},
    }
    problems = validate_table(doc)
    if problems:
        raise RefreshError(f"built table is invalid: {problems[:5]}")
    return doc


def load_census_fixtures(folder: Optional[Path] = None) -> Dict[str, Any]:
    """{code: {year: thousands}} from the verified fixtures (ECN 2017 recorded as 2017).
    `folder` defaults to `FIXTURE_DIR`, read at CALL time."""
    census: Dict[str, Dict[int, float]] = {}
    for path in sorted((folder if folder is not None else FIXTURE_DIR).glob("verified_*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for code, row in (doc.get("census") or {}).items():
            rec = census.setdefault(code, {})
            for k, v in row.items():
                m = re.fullmatch(r"(aies|ecn)_(\d{4})_thousands", k)
                if m and v:
                    rec[int(m.group(2))] = float(v)
        for code, row in (doc.get("census_extra_years") or {}).items():
            rec = census.setdefault(code, {})
            for k, v in row.items():
                m = re.fullmatch(r"aies_(\d{4})_thousands", k)
                if m and v:
                    rec[int(m.group(1))] = float(v)
    return census


def newest_census_year(census: Mapping[str, Mapping[int, float]], codes: Sequence[str]) -> Optional[int]:
    """The newest year every one of `codes` has a verified Census figure for."""
    years = None
    for c in codes:
        have = set((census.get(c) or {}).keys())
        years = have if years is None else years & have
    return max(years) if years else None


def _whole(v: float) -> Any:
    """Census $1,000s as recorded: an int when it is one."""
    return int(v) if float(v).is_integer() else v


def reweight_composites(industries: Mapping[str, Any], census: Mapping[str, Mapping[int, float]]
                        ) -> Tuple[Dict[str, Any], List[str]]:
    """The industries with every composite's weights taken from the NEWEST verified Census year
    covering all its codes (the dossiers move to a new AIES vintage each September; the runtime
    projects a composite only when its weights are for the measured year). Returns (industries,
    notes on what changed)."""
    out: Dict[str, Any] = {}
    notes: List[str] = []
    for name, ind in industries.items():
        ind = json.loads(json.dumps(ind))
        comp = ind.get("composite")
        if comp:
            year = newest_census_year(census, ind["naics_2017"])
            if year is None:
                raise RefreshError(f"{name}: no Census year covers every composite code {ind['naics_2017']}")
            new_codes = {c: {**part, "census_thousands": _whole(census[c][year])}
                         for c, part in comp["codes"].items()}
            if year != comp["weights_year"] or new_codes != comp["codes"]:
                notes.append(f"{name}: composite weights {comp['weights_year']} → {year}")
            comp["weights_year"], comp["codes"] = year, new_codes
        out[name] = ind
    return out, notes


def _cagr(a: float, b: float, years: int) -> float:
    return ((b / a) ** (1.0 / years) - 1.0) * 100.0


def rule_for_industry(doc: Mapping[str, Any], name: str, census: Mapping[str, Mapping[int, float]]
                      ) -> Tuple[str, Dict[str, Optional[float]]]:
    """(the tier the 2-pt rule gives, the gaps it measured) for one pinned industry."""
    ind = doc["industries"][name]
    y0, y1 = RULE_WINDOW
    n = y1 - y0
    codes = ind["naics_2017"]
    try:
        c0 = sum(census[c][y0] for c in codes)
        c1 = sum(census[c][y1] for c in codes)
    except KeyError as e:
        raise RefreshError(f"{name}: no verified Census {e} figure in the fixtures") from e
    census_rate = _cagr(c0, c1, n)

    def level(sectors: Sequence[int], year: int) -> float:
        return sum(doc["bls_sectors"][str(s)]["output"][str(year)] for s in sectors)

    if ind.get("composite"):
        parts = ind["composite"]["codes"]
        grown = sum(census[c][y0] * level(parts[c]["bls_sectors"], y1) / level(parts[c]["bls_sectors"], y0)
                    for c in codes)
        bls_rate = _cagr(c0, grown, n)
    else:
        bls_rate = _cagr(level(ind["bls_sectors"], y0), level(ind["bls_sectors"], y1), n)
    gaps: Dict[str, Optional[float]] = {"census": census_rate, "bls": bls_rate - census_rate,
                                        "cms": None, "gdp": None}
    hist = doc["gdp"]["history"]
    gaps["gdp"] = _cagr(hist[str(y0)], hist[str(y1)], n) - census_rate
    series = ind.get("cms_series")
    if series:
        a, b = CMS_RULE_WINDOW
        vals = doc["cms_series"][series]["values"]
        if str(a) not in vals or str(b) not in vals:
            raise RefreshError(f"{name}: CMS series {series!r} has no {a}/{b} value — move "
                               "CMS_RULE_WINDOW and add the verified Census figure for its first year")
        try:
            ca = sum(census[c][a] for c in codes)
        except KeyError as e:
            raise RefreshError(f"{name}: no verified Census {a} figure for the CMS window") from e
        gaps["cms"] = _cagr(vals[str(a)], vals[str(b)], b - a) - _cagr(ca, c1, b - a)

    if abs(gaps["bls"]) <= MAX_GAP_PTS and ind["kind"] != "unmappable":
        return BASIS_BLS, gaps
    if gaps["cms"] is not None and abs(gaps["cms"]) <= MAX_GAP_PTS:
        return BASIS_CMS, gaps
    if abs(gaps["gdp"]) <= MAX_GAP_PTS:
        return BASIS_GDP, gaps
    return TIER_MEASURED, gaps


def tier_changes(doc: Mapping[str, Any], census: Mapping[str, Mapping[int, float]]) -> List[str]:
    """Each place the pinned tiers disagree with the rule (an owner decision either way)."""
    changes: List[str] = []
    for name, ind in doc["industries"].items():
        rule, gaps = rule_for_industry(doc, name, census)
        exc = ind.get("exception")
        if exc:
            if rule != exc["rule_tier"]:
                changes.append(f"{name}: the rule now gives {rule!r}, not the {exc['rule_tier']!r} "
                               f"the owner exception overrides (pinned {ind['tier']!r})")
            elif rule == ind["tier"]:
                changes.append(f"{name}: the owner exception is no longer needed (rule = pinned)")
        elif rule != ind["tier"]:
            changes.append(f"{name}: pinned {ind['tier']!r}, rule gives {rule!r} "
                           f"(gaps: {', '.join(f'{k} {v:+.2f}' for k, v in gaps.items() if v is not None)})")
    return changes


def apply_rule_tiers(doc: Dict[str, Any], census: Mapping[str, Mapping[int, float]]) -> None:
    for name, ind in doc["industries"].items():
        if not ind.get("exception"):
            ind["tier"] = rule_for_industry(doc, name, census)[0]


def render(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def write_table(doc: Mapping[str, Any], path: Optional[Path] = None) -> None:
    """Validate, then replace `path` (default `TABLE_PATH`, read at CALL time) atomically."""
    path = path if path is not None else TABLE_PATH
    problems = validate_table(doc)
    if problems:
        raise RefreshError(f"refusing to write an invalid table: {problems[:5]}")
    fd, tmp = tempfile.mkstemp(prefix=".industry_tam_projections.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render(doc))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _measured(census: Mapping[str, Mapping[int, float]], codes: Sequence[str]) -> Tuple[Optional[float], Optional[int]]:
    """(the verified Census value in $B, its year) at the newest year covering every code — the
    year the dossiers measure, so a composite previews against its own weights."""
    year = newest_census_year(census, codes)
    if year is None:
        return None, None
    return round(sum(census[c][year] for c in codes) / 1e6, 1), year


def _preview(doc: Mapping[str, Any], old: Mapping[str, Any], census: Mapping[str, Mapping[int, float]],
             start: int) -> None:
    def _fmt(p: Any) -> str:
        return f"{p.start_tam_b:,.1f} → {p.target_tam_b:,.1f} ({p.rate_pct:+.1f}%/yr)" if p else "—"
    old_ok = bool(old) and not validate_table(old)
    counts: Dict[str, int] = {}
    for name, ind in sorted(doc["industries"].items()):
        counts[ind["tier"]] = counts.get(ind["tier"], 0) + 1
        measured, year = _measured(census, ind["naics_2017"])
        new = project_with_table(doc, industry=name, measured_tam_b=measured, measured_year=year, start_year=start)
        prev = project_with_table(old, industry=name, measured_tam_b=measured, measured_year=year,
                                  start_year=start) if old_ok else None
        logger.info("  %-44s %-13s measured %s (%s) · new %s · old %s", name, ind["tier"], measured, year,
                    _fmt(new), _fmt(prev))
    logger.info("tiers: %s · %s · %s", counts, doc["sources"][BASIS_BLS]["program"],
                doc["sources"][BASIS_CMS]["program"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh backend/data/industry_tam_projections.json (owner-run).")
    parser.add_argument("--bls-dir", type=Path, required=True, help="unzipped BLS industry.zip")
    parser.add_argument("--bls-agg", type=Path, required=True, help="BLS aggregate-economy tables .xlsx")
    parser.add_argument("--cms-dir", type=Path, required=True, help="unzipped CMS NHE projection tables")
    parser.add_argument("--bls-released", required=True, help="BLS release date, YYYY-MM-DD")
    parser.add_argument("--cms-released", required=True, help="CMS release date, YYYY-MM-DD")
    parser.add_argument("--seed-industries", type=Path, default=None,
                        help="JSON object of curated industries (first build only)")
    parser.add_argument("--accept-tier-changes", action="store_true",
                        help="apply the rule's tiers where they changed (an owner decision)")
    parser.add_argument("--write", action="store_true", help="replace the table (default: dry run)")
    parser.add_argument("--start-year", type=int, default=None, help="preview start year (default: this year)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    try:
        for d in (args.bls_released, args.cms_released):
            date.fromisoformat(d)
        old: Dict[str, Any] = {}
        if TABLE_PATH.exists():
            old = parse_table_text(TABLE_PATH.read_text(encoding="utf-8"))
        if args.seed_industries is not None:
            industries = parse_table_text(args.seed_industries.read_text(encoding="utf-8"))
        elif old:
            industries = old["industries"]
        else:
            raise RefreshError("no current table and no --seed-industries: nothing to keep")
        census = load_census_fixtures()
        industries, weight_notes = reweight_composites(industries, census)
        bls, bls_base, bls_proj = read_bls_output(args.bls_dir)
        titles = read_sector_titles(args.bls_dir)
        cms = {key: read_cms_series(args.cms_dir, key) for key in CMS_SERIES}
        first_proj, _ = _projected_years(args.cms_dir)
        doc = build_table(
            industries=industries, bls=bls, bls_base=bls_base, bls_proj=bls_proj, titles=titles,
            gdp_bls=read_bls_gdp(args.bls_agg), cms_gdp=read_cms_gdp_history(args.cms_dir), cms=cms,
            cms_first_projected=first_proj, bls_released=args.bls_released,
            cms_released=args.cms_released, built_on=date.today().isoformat(),
            old_titles={k: v["title"] for k, v in (old.get("bls_sectors") or {}).items()},
        )
        changes = tier_changes(doc, census)
    except (RefreshError, OSError, ValueError, KeyError) as e:
        logger.error("projection table refresh FAILED, nothing written: %s: %s", type(e).__name__, e)
        return 1

    start = args.start_year or date.today().year
    for note in weight_notes:
        logger.info("RE-WEIGHTED: %s", note)
    for c in changes:
        logger.warning("TIER CHANGE: %s", c)
    if changes and not args.accept_tier_changes:
        _preview(doc, old, census, start)
        logger.error("%d tier change(s): nothing written. Each is an owner decision — rerun with "
                     "--accept-tier-changes once agreed (exceptions are edited by hand).", len(changes))
        return 2
    if changes:
        apply_rule_tiers(doc, census)
        stale = tier_changes(doc, census)
        if stale:
            logger.error("exceptions need a hand edit first: %s", stale)
            return 2
    # The preview is of the table that would be written (accepted tiers applied).
    _preview(doc, old, census, start)
    if not args.write:
        logger.info("dry run: nothing written (pass --write to replace %s)", TABLE_PATH)
        return 0
    write_table(doc, TABLE_PATH)
    logger.info("wrote %s", TABLE_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
