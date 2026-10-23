"""Projected US industry revenue for the Moat card's market row: the current year and +5.

Owner decisions of 2026-10-22: the card shows the industry for "today year to the next 5 year",
in current dollars, from a DATA source, never a backward rate pushed forward. No free forecast
of industry SALES exists, so the measured US Census AIES revenue (the dossier's `current_tam` at
its `current_year`) is scaled along a pinned, public-domain level path S(y):

* `bls_output` — BLS Employment Projections 2025–35, domestic industry output (series 1, current
  dollars) of the BLS sector(s) the NAICS code sits in: history through the base year, then the
  constant compound rate to the projection year (BLS publishes no intermediate years).
* `cms_nhe` — CMS National Health Expenditure Projections 2025–34, an annual series as published
  (retail prescription drugs for drug wholesalers, health insurance for health plans).
* `gdp_path` — US nominal GDP: BEA history as printed in CMS NHE Table 1, then BLS's 2025 level
  and its 2025–35 rate (one vintage throughout; the yearly refresh moves it as a whole).

value(Y) = measured × S(Y) / S(measured_year). The Aerospace & Defense composite grows each of its
NAICS codes on its own sector, weighted by the pinned Census shares of the measured year.

Which path an industry may use is the 2017–24 backtest rule ("the most specific source whose
growth stayed within 2 pts/yr of Census"), pinned per industry in
`backend/data/industry_tam_projections.json` and re-run by
`tests/test_industry_tam_projection_tiers.py` and `scripts/refresh_industry_tam_projections.py`.
Industries no public path tracked stay measured-only.

Pure and synchronous: no upstream call (so no cache-aside), one lazy JSON load per process. Every
failure degrades to "no projection" (the card shows its measured value) and logs — a missing or
invalid table at ERROR once, a refused row at WARNING once per (industry, reason).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.industry_tam_service import INDUSTRY_TO_CENSUS, census_codes, census_label_codes
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "industry_tam_projections.json"

BASIS_BLS = "bls_output"
BASIS_CMS = "cms_nhe"
BASIS_GDP = "gdp_path"
TIER_MEASURED = "measured_only"
PROJECTED_TIERS: Tuple[str, ...] = (BASIS_BLS, BASIS_CMS, BASIS_GDP)
TIERS: Tuple[str, ...] = PROJECTED_TIERS + (TIER_MEASURED,)
KINDS = frozenset({"exact", "proxy", "margin", "imputed", "unmappable"})

# A projected rate outside this band points at bad data, not at an industry.
_MIN_RATE_PCT = -10.0
_MAX_RATE_PCT = 15.0
# A composite's measured total may differ from its pinned weights' total by rounding only; more
# means Census revised a component and the weights are stale.
_COMPOSITE_TOLERANCE = 0.005
_SOURCE_LABEL_MAX = 120
# The GDP path's two printings of BEA data must agree (same vintage), and CMS's figure for the
# base year must be near BLS's (a unit or column change is off by orders of magnitude).
_GDP_OVERLAP_TOLERANCE = 0.001
_GDP_SPLICE_TOLERANCE = 0.03
# BLS publishes each August; past this the table is a release behind (ERROR once per process).
_STALE_AFTER_DAYS = 456

_TOP_KEYS = frozenset({
    "_about", "schema_version", "built_on", "horizon_years", "tier_rule", "sources",
    "bls_sectors", "cms_series", "gdp", "industries",
})
_INDUSTRY_KEYS = frozenset({
    "tier", "naics_2017", "bls_sectors", "composite", "kind", "cms_series", "exception", "note",
})
_YEAR_RE = re.compile(r"\d{4}")
_CODE_RE = re.compile(r"\d{2,6}")


# The card marks a projected year (owner 2026-10-22: "mark it as projected on the card
# (server-side, no app release), e.g. the years as 2026e → 2031e"). Server-authored: iOS draws the
# label as-is inside "( )", so the mark can change here without an app release.
PROJECTED_YEAR_MARK = "e"


def projected_year_label(year: int) -> str:
    """"2026e" — a projected year as the card prints it."""
    return f"{year}{PROJECTED_YEAR_MARK}"


@dataclass(frozen=True)
class IndustryTamProjection:
    """The projected pair for one industry, in billions of current dollars."""
    basis: str            # bls_output | cms_nhe | gdp_path
    start_year: int       # the current year (US/Eastern)
    start_tam_b: float    # 1 dp
    target_year: int      # start_year + horizon
    target_tam_b: float   # 1 dp
    rate_pct: float       # compound start→target from the UNROUNDED values, 1 dp, never -0.0
    source_label: str     # "Projection: <source caption>, +4.3%/yr" — PDF only (no card caption)

    def response_fields(self) -> Dict[str, Any]:
        """The `MarketDynamicsResponse.projection_*` keys (all or none)."""
        return {
            "projection_basis": self.basis,
            "projection_year": self.start_year,
            "projection_year_label": projected_year_label(self.start_year),
            "projection_tam": self.start_tam_b,
            "projection_target_year": self.target_year,
            "projection_target_year_label": projected_year_label(self.target_year),
            "projection_target_tam": self.target_tam_b,
            "projection_rate_pct": self.rate_pct,
            "projection_source_label": self.source_label,
        }


def current_year_et(now: Optional[datetime] = None) -> int:
    """The calendar year in US/Eastern — the card's "today year" (the app's market clock)."""
    moment = now if now is not None else datetime.now(ET)
    return moment.astimezone(ET).year


# --------------------------------------------------------------------------------------
# Table validation (shared with scripts/refresh_industry_tam_projections.py)
# --------------------------------------------------------------------------------------

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_year(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 1990 <= v <= 2100


def _check_series(where: str, values: Any, first: int, last: int,
                  extra: Sequence[int], problems: List[str]) -> None:
    """`values` maps "YYYY" → a positive finite number for every year first..last and `extra`."""
    if not isinstance(values, dict) or not values:
        problems.append(f"{where}: values must be a non-empty object")
        return
    for k, v in values.items():
        if not isinstance(k, str) or not _YEAR_RE.fullmatch(k):
            problems.append(f"{where}: bad year key {k!r}")
        elif not _is_num(v) or v <= 0:
            problems.append(f"{where}[{k}]: not a positive finite number ({v!r})")
    for y in list(range(first, last + 1)) + list(extra):
        if str(y) not in values:
            problems.append(f"{where}: missing year {y}")


def validate_table(doc: Any) -> List[str]:
    """Every problem with a projections table; [] means usable. Fail-closed: the runtime treats
    any problem as "no projections at all" (a partly wrong file is suspect as a whole)."""
    problems: List[str] = []
    if not isinstance(doc, dict):
        return ["table is not a JSON object"]
    unknown = set(doc) - _TOP_KEYS
    missing = _TOP_KEYS - set(doc)
    if unknown:
        problems.append(f"unknown top-level keys {sorted(unknown)}")
    if missing:
        return problems + [f"missing top-level keys {sorted(missing)}"]
    if doc["schema_version"] != 1:
        problems.append(f"schema_version {doc['schema_version']!r} is not 1")
    horizon = doc["horizon_years"]
    if not isinstance(horizon, int) or isinstance(horizon, bool) or not 1 <= horizon <= 10:
        problems.append(f"horizon_years {horizon!r} must be an int 1..10")

    sources = doc["sources"]
    if not isinstance(sources, dict) or set(sources) != set(PROJECTED_TIERS):
        return problems + [f"sources must name exactly {list(PROJECTED_TIERS)}"]
    for name, src in sources.items():
        if not isinstance(src, dict) or not isinstance(src.get("caption"), str) or not src["caption"].strip():
            problems.append(f"sources.{name}: needs a non-empty caption")
    bls = sources[BASIS_BLS]
    cms = sources[BASIS_CMS]
    b_first, b_base, b_proj = (bls.get("history_first_year"), bls.get("base_year"),
                               bls.get("projection_year")) if isinstance(bls, dict) else (None,) * 3
    if not (_is_year(b_first) and _is_year(b_base) and _is_year(b_proj) and b_first < b_base < b_proj):
        return problems + ["sources.bls_output: history_first_year < base_year < projection_year required"]
    c_first, c_last = (cms.get("first_year"), cms.get("last_year")) if isinstance(cms, dict) else (None,) * 2
    if not (_is_year(c_first) and _is_year(c_last) and c_first < c_last):
        return problems + ["sources.cms_nhe: first_year < last_year required"]

    sectors = doc["bls_sectors"]
    if not isinstance(sectors, dict) or not sectors:
        return problems + ["bls_sectors must be a non-empty object"]
    for key, sec in sectors.items():
        where = f"bls_sectors.{key}"
        if not isinstance(key, str) or not key.isdigit():
            problems.append(f"{where}: key must be a sector number")
            continue
        if not isinstance(sec, dict) or not isinstance(sec.get("title"), str) or not sec["title"].strip():
            problems.append(f"{where}: needs a title")
            continue
        _check_series(where, sec.get("output"), b_first, b_base, [b_proj], problems)

    cms_series = doc["cms_series"]
    if not isinstance(cms_series, dict) or not cms_series:
        return problems + ["cms_series must be a non-empty object"]
    for key, ser in cms_series.items():
        where = f"cms_series.{key}"
        if not isinstance(ser, dict) or not isinstance(ser.get("title"), str) or not ser["title"].strip():
            problems.append(f"{where}: needs a title")
            continue
        _check_series(where, ser.get("values"), c_first, c_last, [], problems)

    gdp = doc["gdp"]
    if not isinstance(gdp, dict):
        return problems + ["gdp must be an object"]
    g_base, g_proj = gdp.get("base_year"), gdp.get("projection_year")
    if not (_is_year(g_base) and _is_year(g_proj) and g_base < g_proj):
        problems.append("gdp: base_year < projection_year required")
    else:
        _check_series("gdp.history", gdp.get("history"), b_first, g_base - 1, [], problems)
        for k in ("base_value", "projection_value", "cms_base_year_value"):
            if not _is_num(gdp.get(k)) or gdp[k] <= 0:
                problems.append(f"gdp.{k}: not a positive finite number")
        if all(_is_num(gdp.get(k)) and gdp[k] > 0 for k in ("base_value", "cms_base_year_value")):
            if abs(gdp["cms_base_year_value"] - gdp["base_value"]) > _GDP_SPLICE_TOLERANCE * gdp["base_value"]:
                problems.append("gdp: CMS and BLS disagree on the base year by more than "
                                f"{_GDP_SPLICE_TOLERANCE:.0%} (a unit or column change)")
        overlap = gdp.get("vintage_overlap")
        if not isinstance(overlap, dict) or not overlap:
            problems.append("gdp.vintage_overlap: needs the years both GDP tables print")
        else:
            for y, pair in overlap.items():
                if (not isinstance(pair, dict) or not _is_num(pair.get("bls")) or not _is_num(pair.get("cms"))
                        or pair["bls"] <= 0
                        or abs(pair["bls"] - pair["cms"]) > _GDP_OVERLAP_TOLERANCE * pair["bls"]):
                    problems.append(f"gdp.vintage_overlap[{y}]: the two tables carry different BEA vintages")

    industries = doc["industries"]
    if not isinstance(industries, dict) or not industries:
        return problems + ["industries must be a non-empty object"]
    for name, ind in industries.items():
        _validate_industry(name, ind, sectors, cms_series, problems)
    return problems


def _validate_industry(name: str, ind: Any, sectors: Mapping[str, Any],
                       cms_series: Mapping[str, Any], problems: List[str]) -> None:
    where = f"industries[{name!r}]"
    if not isinstance(ind, dict):
        problems.append(f"{where}: not an object")
        return
    unknown = set(ind) - _INDUSTRY_KEYS
    if unknown:
        problems.append(f"{where}: unknown keys {sorted(unknown)}")
    tier, kind, codes = ind.get("tier"), ind.get("kind"), ind.get("naics_2017")
    if tier not in TIERS:
        problems.append(f"{where}: tier {tier!r} is not one of {list(TIERS)}")
    if kind not in KINDS:
        problems.append(f"{where}: kind {kind!r} is not one of {sorted(KINDS)}")
    if tier == BASIS_BLS and kind == "unmappable":
        problems.append(f"{where}: an unmappable code can never use the BLS path")
    if (not isinstance(codes, list) or not codes
            or not all(isinstance(c, str) and _CODE_RE.fullmatch(c) for c in codes)
            or len(set(codes)) != len(codes)):
        problems.append(f"{where}: naics_2017 must be a list of distinct NAICS codes")
        return

    def _sector_list(where2: str, lst: Any) -> None:
        if (not isinstance(lst, list) or not lst
                or not all(isinstance(s, int) and not isinstance(s, bool) for s in lst)):
            problems.append(f"{where2}: bls_sectors must be a non-empty list of sector numbers")
            return
        for s in lst:
            if str(s) not in sectors:
                problems.append(f"{where2}: sector {s} is not in bls_sectors")

    composite = ind.get("composite")
    if composite is not None:
        if "bls_sectors" in ind:
            problems.append(f"{where}: a composite carries bls_sectors per code, not at the top")
        if not isinstance(composite, dict) or not _is_year(composite.get("weights_year")):
            problems.append(f"{where}.composite: needs a weights_year")
            return
        parts = composite.get("codes")
        if not isinstance(parts, dict) or set(parts) != set(codes) or len(codes) < 2:
            problems.append(f"{where}.composite: codes must be exactly naics_2017 (2+ codes)")
            return
        for code, part in parts.items():
            if not isinstance(part, dict) or not _is_num(part.get("census_thousands")) \
                    or part["census_thousands"] <= 0:
                problems.append(f"{where}.composite[{code}]: needs a positive census_thousands")
                continue
            _sector_list(f"{where}.composite[{code}]", part.get("bls_sectors"))
    else:
        if len(codes) != 1:
            problems.append(f"{where}: several codes need a composite block")
        _sector_list(where, ind.get("bls_sectors"))

    if tier == BASIS_CMS and ind.get("cms_series") not in cms_series:
        problems.append(f"{where}: cms tier needs a cms_series from cms_series")
    exc = ind.get("exception")
    if exc is not None:
        if (not isinstance(exc, dict) or exc.get("rule_tier") not in TIERS
                or not isinstance(exc.get("owner_decision"), str) or not exc["owner_decision"].strip()
                or not isinstance(exc.get("date"), str) or not exc["date"].strip()):
            problems.append(f"{where}.exception: needs rule_tier, owner_decision and date")


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

class _Loaded:
    __slots__ = ("doc", "disabled")

    def __init__(self, doc: Dict[str, Any], disabled: Set[str]) -> None:
        self.doc = doc
        self.disabled = disabled


_UNLOADED = object()
_state: Any = _UNLOADED            # _UNLOADED | None (unusable) | _Loaded
_warned: Set[Tuple[str, str]] = set()


def reset_projection_table() -> None:
    """Forget the loaded table and the warn-once memo (tests; a deploy reloads anyway)."""
    global _state
    _state = _UNLOADED
    _warned.clear()


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant {token}")


def _no_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate JSON key {k!r}")
        out[k] = v
    return out


def parse_table_text(raw: str) -> Any:
    """JSON with NaN/Infinity and duplicate keys refused (a silent last-wins key is a wrong path)."""
    return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys)


def _load() -> Optional[_Loaded]:
    global _state
    if _state is not _UNLOADED:
        return _state
    try:
        doc = parse_table_text(_DATA_PATH.read_text(encoding="utf-8"))
        problems = validate_table(doc)
        if problems:
            raise ValueError(f"{len(problems)} problem(s): " + "; ".join(problems[:5]))
    except Exception as e:  # noqa: BLE001 — any failure means no projections, loudly
        logger.error(
            "INDUSTRY TAM PROJECTIONS DISABLED: %s is unusable (%s: %s) — every Moat card shows "
            "its measured value only until the table is fixed", _DATA_PATH, type(e).__name__, e,
        )
        _state = None
        return None

    disabled: Set[str] = set()
    for name, ind in doc["industries"].items():
        mapping = INDUSTRY_TO_CENSUS.get(name)
        live = tuple(census_codes(mapping)) if mapping else None
        if live != tuple(ind["naics_2017"]):
            disabled.add(name)
            logger.error(
                "INDUSTRY TAM PROJECTION DISABLED for %r: the pinned table backtested NAICS %s but "
                "INDUSTRY_TO_CENSUS maps %s — refresh the table (scripts/refresh_industry_tam_projections.py)",
                name, ind["naics_2017"], list(live) if live else None,
            )
    try:
        released = datetime.strptime(str(doc["sources"][BASIS_BLS].get("released")), "%Y-%m-%d").date()
        if (datetime.now(ET).date() - released).days > _STALE_AFTER_DAYS:
            logger.error("INDUSTRY TAM PROJECTIONS STALE: built from the BLS release of %s — a newer "
                         "release exists; run scripts/refresh_industry_tam_projections.py "
                         "(documents/OWNER_TASKS.md, yearly)", released)
    except ValueError:
        logger.error("INDUSTRY TAM PROJECTIONS: sources.bls_output.released %r is not a date",
                     doc["sources"][BASIS_BLS].get("released"))
    unpinned = sorted(set(INDUSTRY_TO_CENSUS) - set(doc["industries"]))
    if unpinned:
        logger.error("INDUSTRY TAM PROJECTIONS: %d Census-mapped industries have no pinned tier "
                     "(measured only): %s", len(unpinned), unpinned[:10])
    counts: Dict[str, int] = {}
    for ind in doc["industries"].values():
        counts[ind["tier"]] = counts.get(ind["tier"], 0) + 1
    logger.info(
        "industry TAM projections loaded: built_on=%s, BLS %s, CMS %s; tiers %s; %d disabled",
        doc.get("built_on"), doc["sources"][BASIS_BLS].get("program"),
        doc["sources"][BASIS_CMS].get("program"), counts, len(disabled),
    )
    _state = _Loaded(doc, disabled)
    return _state


def _warn_once(industry: str, reason: str, msg: str, *args: Any) -> None:
    key = (industry, reason)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args)


def projection_tier(industry: str) -> Optional[str]:
    """The pinned tier for `industry` (including "measured_only"); None when unknown, disabled
    or the table is unusable."""
    loaded = _load()
    if loaded is None or industry in loaded.disabled:
        return None
    ind = loaded.doc["industries"].get(industry)
    return ind["tier"] if ind else None


# --------------------------------------------------------------------------------------
# Level paths
# --------------------------------------------------------------------------------------

def _bls_level(doc: Mapping[str, Any], sectors: Sequence[int], year: int) -> Optional[float]:
    src = doc["sources"][BASIS_BLS]
    first, base, proj = src["history_first_year"], src["base_year"], src["projection_year"]
    if not first <= year <= proj:
        return None
    total = 0.0
    for s in sectors:
        out = doc["bls_sectors"][str(s)]["output"]
        if year <= base:
            total += out[str(year)]
        else:
            b, p = out[str(base)], out[str(proj)]
            total += b * (p / b) ** ((year - base) / (proj - base))
    return total


def _cms_level(doc: Mapping[str, Any], series: str, year: int) -> Optional[float]:
    return doc["cms_series"][series]["values"].get(str(year))


def _gdp_level(doc: Mapping[str, Any], year: int) -> Optional[float]:
    gdp = doc["gdp"]
    base, proj = gdp["base_year"], gdp["projection_year"]
    if year < base:
        return gdp["history"].get(str(year))
    if year > proj:
        return None
    b, p = gdp["base_value"], gdp["projection_value"]
    return b * (p / b) ** ((year - base) / (proj - base))


def _level(doc: Mapping[str, Any], ind: Mapping[str, Any], year: int,
           sectors: Optional[Sequence[int]] = None) -> Optional[float]:
    tier = ind["tier"]
    if tier == BASIS_BLS:
        return _bls_level(doc, sectors if sectors is not None else ind["bls_sectors"], year)
    if tier == BASIS_CMS:
        return _cms_level(doc, ind["cms_series"], year)
    if tier == BASIS_GDP:
        return _gdp_level(doc, year)
    return None


def _source_caption(doc: Mapping[str, Any], ind: Mapping[str, Any]) -> str:
    caption = doc["sources"][ind["tier"]]["caption"].strip()
    if ind["tier"] == BASIS_CMS:
        caption = f"{caption}, {doc['cms_series'][ind['cms_series']]['title'].strip()}"
    return caption


def _rate_text(rate_pct: float) -> str:
    return f"{'+' if rate_pct >= 0 else '−'}{abs(rate_pct):.1f}%/yr"


def _year_or_none(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 1990 <= v <= 2100 else None
    if isinstance(v, str) and _YEAR_RE.fullmatch(v.strip()):
        return _year_or_none(int(v.strip()))
    return None


# --------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------

def project_industry_tam(
    *,
    industry: Any,
    measured_tam_b: Any,
    measured_year: Any,
    source_label: Any,
    start_year: Any,
) -> Optional[IndustryTamProjection]:
    """The current-year and +5 projection of one measured Census industry figure, or None.

    None (never an exception) for: an unknown, disabled or measured-only industry; a label that
    is not the Census label for the exact pinned codes (BEA backups, Railroads, a stale A&D row);
    a non-positive / non-finite / bool TAM; a bad year; a composite whose weights are for another
    year or whose total no longer matches; a start not after the measured year; a target past the
    source's horizon; a non-finite or implausible result (ERROR — bad data)."""
    loaded = _load()
    if loaded is None or not isinstance(industry, str):
        return None
    doc = loaded.doc
    ind = doc["industries"].get(industry)
    if ind is None or industry in loaded.disabled:
        return None
    if ind["tier"] == TIER_MEASURED:
        logger.debug("TAM projection: %r is measured-only by its pinned tier", industry)
        return None

    codes = census_label_codes(source_label)
    if codes is None:
        _warn_once(industry, "not_census",
                   "TAM projection skipped for %r: the measured figure is not a US Census AIES "
                   "row (%r) — no projection for a BEA/FRED or placeholder row", industry,
                   str(source_label)[:120])
        return None
    if codes != tuple(ind["naics_2017"]):
        _warn_once(industry, "codes",
                   "TAM projection skipped for %r: the dossier measured NAICS %s but the table "
                   "pins %s (a stale dossier row: refresh it)", industry, list(codes), ind["naics_2017"])
        return None

    return project_with_table(doc, industry=industry, measured_tam_b=measured_tam_b,
                              measured_year=measured_year, start_year=start_year)


def project_with_table(
    doc: Mapping[str, Any],
    *,
    industry: str,
    measured_tam_b: Any,
    measured_year: Any,
    start_year: Any,
) -> Optional[IndustryTamProjection]:
    """The projection arithmetic against an already-validated table `doc` (no load, no label
    check). The refresh script and the tier test call it on a candidate table."""
    ind = doc["industries"].get(industry)
    if ind is None or ind["tier"] == TIER_MEASURED:
        return None
    if (not _is_num(measured_tam_b)) or measured_tam_b <= 0:
        return None
    m_year = _year_or_none(measured_year)
    s_year = start_year if (_is_year(start_year)) else None
    if m_year is None or s_year is None:
        return None
    if s_year <= m_year:
        logger.debug("TAM projection: %r start %s is not after its measured year %s", industry, s_year, m_year)
        return None
    t_year = s_year + doc["horizon_years"]

    composite = ind.get("composite")
    try:
        if composite is not None:
            if composite["weights_year"] != m_year:
                _warn_once(industry, "weights_year",
                           "TAM projection skipped for %r: composite weights are for %s but the "
                           "measured year is %s — add the verified Census %s figures to "
                           "tests/fixtures/industry_tam and rerun scripts/refresh_industry_tam_projections.py",
                           industry, composite["weights_year"], m_year, m_year)
                return None
            parts = composite["codes"]
            total_k = sum(p["census_thousands"] for p in parts.values())
            if abs(measured_tam_b - total_k / 1e6) > _COMPOSITE_TOLERANCE * (total_k / 1e6):
                _warn_once(industry, "weights_total",
                           "TAM projection skipped for %r: measured $%.1fB differs from the pinned "
                           "composite total $%.1fB by more than %.1f%% (Census revised a component)",
                           industry, measured_tam_b, total_k / 1e6, _COMPOSITE_TOLERANCE * 100)
                return None
            start = target = 0.0
            for code, part in parts.items():
                share = measured_tam_b * part["census_thousands"] / total_k
                lv = [_level(doc, ind, y, part["bls_sectors"]) for y in (m_year, s_year, t_year)]
                if any(v is None or v <= 0 for v in lv):
                    _warn_once(industry, "horizon",
                               "TAM projection skipped for %r: %s–%s is outside the BLS path",
                               industry, m_year, t_year)
                    return None
                start += share * lv[1] / lv[0]
                target += share * lv[2] / lv[0]
        else:
            lv = [_level(doc, ind, y) for y in (m_year, s_year, t_year)]
            if any(v is None or v <= 0 for v in lv):
                _warn_once(industry, "horizon",
                           "TAM projection skipped for %r: %s–%s is outside the %s path",
                           industry, m_year, t_year, ind["tier"])
                return None
            start = measured_tam_b * lv[1] / lv[0]
            target = measured_tam_b * lv[2] / lv[0]
    except (KeyError, TypeError, ZeroDivisionError, OverflowError, ValueError) as e:
        logger.error("TAM projection failed for %r (%s: %s) — measured value only",
                     industry, type(e).__name__, e)
        return None

    if not (math.isfinite(start) and math.isfinite(target) and start > 0 and target > 0):
        logger.error("TAM projection for %r is not finite/positive (%r → %r) — measured value only",
                     industry, start, target)
        return None
    rate = ((target / start) ** (1.0 / doc["horizon_years"]) - 1.0) * 100.0
    if not (_MIN_RATE_PCT <= rate <= _MAX_RATE_PCT):
        logger.error("TAM projection for %r implies %.1f%%/yr (outside %s..%s) — bad table data; "
                     "measured value only", industry, rate, _MIN_RATE_PCT, _MAX_RATE_PCT)
        return None
    rate_1dp = round(rate, 1) + 0.0
    label = f"Projection: {_source_caption(doc, ind)}, {_rate_text(rate_1dp)}"
    return IndustryTamProjection(
        basis=ind["tier"],
        start_year=s_year,
        start_tam_b=round(start, 1),
        target_year=t_year,
        target_tam_b=round(target, 1),
        rate_pct=rate_1dp,
        source_label=label[:_SOURCE_LABEL_MAX],
    )
