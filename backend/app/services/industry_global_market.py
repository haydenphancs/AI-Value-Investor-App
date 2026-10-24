"""Published GLOBAL market estimates for the Moat card's market row (chips, chip equipment,
advertising, digital advertising).

Owner decisions of 2026-10-24: for these industries the card shows the worldwide market instead
of US industry revenue ("Replace with global"), with the global rate in its growth column
("Global rate"), from 2026 ("$1T in 2026") to 2031, extending the source's last rate past its
final year without a mark ("option 2, but no need to mark or note").

Only figures a government document states in its own words, in a document licensed for
commercial re-use (EUR-Lex, a CC BY 4.0 JRC report, a public-domain US ITC report) — pinned in
`backend/data/industry_global_markets.json` with the quote, the licence sentence and the firms
the document itself cites. value(Y) is geometric between the stated points and, past the last
stated year, the last segment's rate for at most `max_extension_years` (beyond that the row
falls back to the US figure, WARNING once — the yearly source re-check in OWNER_TASKS.md moves
the table forward).

The result is the `industry_tam_projection.IndustryTamProjection` shape with basis
`global_estimate`, plus the row's own header and scope line. Pure and synchronous (no upstream
call, so no cache-aside); one lazy JSON load per process. Every failure degrades to "no global
row" (the US row stays) and logs — a missing or invalid table at ERROR once, a refused row at
WARNING once per (industry, reason).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

from app.services.industry_tam_projection import IndustryTamProjection, parse_table_text
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "industry_global_markets.json"

BASIS_GLOBAL = "global_estimate"

# A global market growing outside this band points at a transcription error, not at an industry.
_MIN_RATE_PCT = -10.0
_MAX_RATE_PCT = 25.0
# A segment that states BOTH a rate and its end value must agree up to the source's rounding.
_CHECK_TOLERANCE = 0.02
# The card header and scope line are short, single-line strings on a phone.
_LABEL_MAX = 32
_SCOPE_MAX = 100
_SOURCE_LABEL_MAX = 240
# The largest |rate| a stored row may carry and still be drawn — the PDF's growth-cell bound
# (`pdf_report_service._GROWTH_PCT_MAX_ABS`) and iOS `MarketDynamics.globalRate`, pinned equal.
GLOBAL_RATE_MAX_ABS = 1000.0
# Past this the table is a year behind its sources' yearly editions (ERROR once per process).
_STALE_AFTER_DAYS = 548

_TOP_KEYS = frozenset({
    "_about", "schema_version", "reviewed_on", "horizon_years", "max_extension_years",
    "sources", "industries",
})
_SOURCE_KEYS = frozenset({
    "publisher", "document", "date", "url", "licence", "licence_url", "licence_quote", "cites",
    "credit",
})
_INDUSTRY_KEYS = frozenset({"label", "scope_note", "source", "anchor", "segments", "pdf_note"})
_ANCHOR_KEYS = frozenset({"year", "value_b", "kind", "quote", "derivation"})
_SEGMENT_KEYS = frozenset({
    "to_year", "end_value_b", "rate_pct", "check_end_value_b", "kind", "quote", "derivation",
})
_KINDS = frozenset({"stated", "derived"})


@dataclass(frozen=True)
class GlobalMarketRow:
    """The card's global market row: its header, scope line and projected pair."""
    market_size_label: str            # "Global chip market"
    scope_note: str                   # "Worldwide semiconductor sales, all regions."
    projection: IndustryTamProjection  # basis "global_estimate"

    def response_fields(self) -> Dict[str, Any]:
        """`market_size_label`, `scope_note` and all nine `projection_*` keys."""
        return {
            "market_size_label": self.market_size_label,
            "scope_note": self.scope_note,
            **self.projection.response_fields(),
        }


# --------------------------------------------------------------------------------------
# Table validation
# --------------------------------------------------------------------------------------

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_year(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 1990 <= v <= 2100


def _text(v: Any, max_len: Optional[int] = None) -> bool:
    return isinstance(v, str) and bool(v.strip()) and (max_len is None or len(v) <= max_len)


def validate_global_table(doc: Any) -> List[str]:
    """Every problem with a global-markets table; [] means usable. Fail-closed: the runtime
    treats any problem as "no global rows at all" (a partly wrong file is suspect as a whole)."""
    if not isinstance(doc, dict):
        return ["table is not a JSON object"]
    problems: List[str] = []
    unknown, missing = set(doc) - _TOP_KEYS, _TOP_KEYS - set(doc)
    if unknown:
        problems.append(f"unknown top-level keys {sorted(unknown)}")
    if missing:
        return problems + [f"missing top-level keys {sorted(missing)}"]
    if doc["schema_version"] != 1:
        problems.append(f"schema_version {doc['schema_version']!r} is not 1")
    if _review_date(doc) is None:
        problems.append(f"reviewed_on {doc['reviewed_on']!r} is not a YYYY-MM-DD date")
    horizon, ext = doc["horizon_years"], doc["max_extension_years"]
    if not isinstance(horizon, int) or isinstance(horizon, bool) or not 1 <= horizon <= 10:
        problems.append(f"horizon_years {horizon!r} must be an int 1..10")
    if not isinstance(ext, int) or isinstance(ext, bool) or not 0 <= ext <= 5:
        problems.append(f"max_extension_years {ext!r} must be an int 0..5")

    sources = doc["sources"]
    if not isinstance(sources, dict) or not sources:
        return problems + ["sources must be a non-empty object"]
    for name, src in sources.items():
        where = f"sources.{name}"
        if not isinstance(src, dict):
            problems.append(f"{where}: not an object")
            continue
        if set(src) != _SOURCE_KEYS:
            problems.append(f"{where}: keys must be exactly {sorted(_SOURCE_KEYS)}")
            continue
        for k in sorted(_SOURCE_KEYS):
            if not _text(src[k]):
                problems.append(f"{where}.{k}: needs non-empty text")
        if _text(src["url"]) and not src["url"].startswith("https://"):
            problems.append(f"{where}.url: must be an https URL")

    industries = doc["industries"]
    if not isinstance(industries, dict) or not industries:
        return problems + ["industries must be a non-empty object"]
    for name, ind in industries.items():
        _validate_industry(name, ind, sources, problems)
    if not problems:
        ext = doc["max_extension_years"]
        for name, ind in industries.items():
            longest = _longest_source_label(doc, ind, ext)
            if longest > _SOURCE_LABEL_MAX:
                problems.append(f"industries[{name!r}]: its PDF source line can reach {longest} "
                                f"characters (cap {_SOURCE_LABEL_MAX}) — the change note would be cut")
    return problems


def _longest_source_label(doc: Mapping[str, Any], ind: Mapping[str, Any], max_ext: int) -> int:
    """The longest source line `estimate_with_table` can write for `ind` (the widest rate text,
    its note, the longest extension note), so a credit edited at the yearly re-check can never
    push the CC BY change note past the cap."""
    years = [ind["anchor"]["year"]] + [s["to_year"] for s in ind["segments"]]
    seg_from, last = years[-2], years[-1]
    parts = [f"Global estimate: {doc['sources'][ind['source']]['credit'].strip()}, "
             f"{_rate_text(-max(abs(_MIN_RATE_PCT), abs(_MAX_RATE_PCT)))}"]
    if isinstance(ind.get("pdf_note"), str):
        parts.append(ind["pdf_note"].strip())
    if max_ext > 0:
        parts.append(_extension_text(last + max_ext, last, seg_from))
    return len("; ".join(parts))


def _validate_industry(name: str, ind: Any, sources: Mapping[str, Any], problems: List[str]) -> None:
    where = f"industries[{name!r}]"
    if not isinstance(ind, dict):
        problems.append(f"{where}: not an object")
        return
    unknown = set(ind) - _INDUSTRY_KEYS
    missing = (_INDUSTRY_KEYS - {"pdf_note"}) - set(ind)
    if unknown:
        problems.append(f"{where}: unknown keys {sorted(unknown)}")
    if missing:
        problems.append(f"{where}: missing keys {sorted(missing)}")
        return
    if not _text(ind["label"], _LABEL_MAX):
        problems.append(f"{where}.label: needs text of at most {_LABEL_MAX} characters")
    if not _text(ind["scope_note"], _SCOPE_MAX):
        problems.append(f"{where}.scope_note: needs text of at most {_SCOPE_MAX} characters")
    if "pdf_note" in ind and not _text(ind["pdf_note"], 120):
        problems.append(f"{where}.pdf_note: needs text of at most 120 characters")
    if ind["source"] not in sources:
        problems.append(f"{where}.source {ind['source']!r} is not in sources")

    anchor = ind["anchor"]
    if not isinstance(anchor, dict) or set(anchor) - _ANCHOR_KEYS:
        problems.append(f"{where}.anchor: must be an object with keys from {sorted(_ANCHOR_KEYS)}")
        return
    if not _is_year(anchor.get("year")) or not _is_num(anchor.get("value_b")) or anchor["value_b"] <= 0:
        problems.append(f"{where}.anchor: needs a year and a positive finite value_b")
        return
    _check_provenance(f"{where}.anchor", anchor, problems)

    segments = ind["segments"]
    if not isinstance(segments, list) or not segments:
        problems.append(f"{where}.segments: must be a non-empty list")
        return
    prev_year, prev_value = anchor["year"], anchor["value_b"]
    for i, seg in enumerate(segments):
        w = f"{where}.segments[{i}]"
        if not isinstance(seg, dict) or set(seg) - _SEGMENT_KEYS:
            problems.append(f"{w}: must be an object with keys from {sorted(_SEGMENT_KEYS)}")
            return
        to_year = seg.get("to_year")
        if not _is_year(to_year) or to_year <= prev_year:
            problems.append(f"{w}.to_year: must be a year after {prev_year}")
            return
        has_end, has_rate = "end_value_b" in seg, "rate_pct" in seg
        if has_end == has_rate:
            problems.append(f"{w}: needs exactly one of end_value_b / rate_pct")
            return
        if has_end:
            if not _is_num(seg["end_value_b"]) or seg["end_value_b"] <= 0:
                problems.append(f"{w}.end_value_b: not a positive finite number")
                return
            if "check_end_value_b" in seg:
                problems.append(f"{w}: check_end_value_b only goes with a rate_pct segment")
            end = float(seg["end_value_b"])
            rate = ((end / prev_value) ** (1.0 / (to_year - prev_year)) - 1.0) * 100.0
        else:
            if not _is_num(seg["rate_pct"]):
                problems.append(f"{w}.rate_pct: not a finite number")
                return
            rate = float(seg["rate_pct"])
            end = prev_value * (1.0 + rate / 100.0) ** (to_year - prev_year)
            check = seg.get("check_end_value_b")
            if check is not None:
                if not _is_num(check) or check <= 0:
                    problems.append(f"{w}.check_end_value_b: not a positive finite number")
                elif abs(end - check) > _CHECK_TOLERANCE * check:
                    problems.append(f"{w}: the stated rate gives {end:.1f} in {to_year} but the "
                                    f"source states {check} (more than {_CHECK_TOLERANCE:.0%} apart)")
        if not _MIN_RATE_PCT <= rate <= _MAX_RATE_PCT:
            problems.append(f"{w}: implies {rate:.1f}%/yr (outside {_MIN_RATE_PCT}..{_MAX_RATE_PCT})")
        _check_provenance(w, seg, problems)
        prev_year, prev_value = to_year, end


def _check_provenance(where: str, block: Mapping[str, Any], problems: List[str]) -> None:
    """Every figure carries its source's own words; a derived one also says how it was derived."""
    kind = block.get("kind")
    if kind not in _KINDS:
        problems.append(f"{where}.kind {kind!r} is not one of {sorted(_KINDS)}")
    if not _text(block.get("quote")):
        problems.append(f"{where}.quote: needs the source's own words")
    if kind == "derived" and not _text(block.get("derivation")):
        problems.append(f"{where}: a derived figure needs its derivation")
    if kind == "stated" and "derivation" in block:
        problems.append(f"{where}: a stated figure carries no derivation")


def _review_date(doc: Mapping[str, Any]) -> Optional[date]:
    try:
        return datetime.strptime(str(doc.get("reviewed_on")), "%Y-%m-%d").date()
    except ValueError:
        return None


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


def reset_global_table() -> None:
    """Forget the loaded table and the warn-once memo (tests; a deploy reloads anyway)."""
    global _state
    _state = _UNLOADED
    _warned.clear()


def _load(now: Optional[datetime] = None) -> Optional[_Loaded]:
    global _state
    if _state is not _UNLOADED:
        return _state
    try:
        doc = parse_table_text(_DATA_PATH.read_text(encoding="utf-8"))
        problems = validate_global_table(doc)
        if problems:
            raise ValueError(f"{len(problems)} problem(s): " + "; ".join(problems[:5]))
    except Exception as e:  # noqa: BLE001 — any failure means no global rows, loudly
        logger.error(
            "INDUSTRY GLOBAL MARKETS DISABLED: %s is unusable (%s: %s) — chips, chip equipment, "
            "advertising and internet cards show their US row until the table is fixed",
            _DATA_PATH, type(e).__name__, e,
        )
        _state = None
        return None

    # An industry name the report never produces would silently never match (a typo, or an
    # industry renamed upstream): refuse it loudly instead.
    from app.services.industry_tam_service import INDUSTRY_TO_CENSUS

    disabled = {name for name in doc["industries"] if name not in INDUSTRY_TO_CENSUS}
    for name in sorted(disabled):
        logger.error("INDUSTRY GLOBAL MARKET DISABLED for %r: not an industry INDUSTRY_TO_CENSUS "
                     "knows — fix the name in %s", name, _DATA_PATH.name)
    reviewed = _review_date(doc)
    today = (now or datetime.now(ET)).astimezone(ET).date()
    if reviewed is not None and (today - reviewed).days > _STALE_AFTER_DAYS:
        logger.error("INDUSTRY GLOBAL MARKETS STALE: last reviewed %s — re-check every source for "
                     "a newer edition (documents/OWNER_TASKS.md, yearly) and set reviewed_on", reviewed)
    elif reviewed is not None and (reviewed - today).days > 1:
        # A typo'd year ("2062") would switch the staleness alarm off for good.
        logger.error("INDUSTRY GLOBAL MARKETS: reviewed_on %s is in the future — fix the date in %s "
                     "(the staleness check cannot fire until then)", reviewed, _DATA_PATH.name)
    logger.info("industry global markets loaded: reviewed_on=%s, %d industries, %d disabled",
                doc["reviewed_on"], len(doc["industries"]), len(disabled))
    _state = _Loaded(doc, disabled)
    return _state


def _warn_once(industry: str, reason: str, msg: str, *args: Any) -> None:
    key = (industry, reason)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args)


def global_market_industries() -> FrozenSet[str]:
    """The industries that have a usable global row (empty when the table is unusable)."""
    loaded = _load()
    if loaded is None:
        return frozenset()
    return frozenset(set(loaded.doc["industries"]) - loaded.disabled)


# --------------------------------------------------------------------------------------
# The path
# --------------------------------------------------------------------------------------

def _path(ind: Mapping[str, Any], max_ext: int) -> Tuple[Dict[int, float], int, int]:
    """year → $B from the anchor through the last stated year + `max_ext`, plus the last
    segment's first year and the last stated year."""
    year, value = ind["anchor"]["year"], float(ind["anchor"]["value_b"])
    values: Dict[int, float] = {year: value}
    seg_from = year
    growth = 1.0
    for seg in ind["segments"]:
        seg_from, to_year = year, seg["to_year"]
        if "end_value_b" in seg:
            growth = (float(seg["end_value_b"]) / value) ** (1.0 / (to_year - year))
        else:
            growth = 1.0 + float(seg["rate_pct"]) / 100.0
        for y in range(year + 1, to_year + 1):
            values[y] = value * growth ** (y - year)
        year, value = to_year, values[to_year]
    last = year
    for y in range(last + 1, last + max_ext + 1):
        values[y] = value * growth ** (y - last)
    return values, seg_from, last


def _rate_text(rate_pct: float) -> str:
    return f"{'+' if rate_pct >= 0 else '−'}{abs(rate_pct):.1f}%/yr"


def _extension_text(target: int, last_stated: int, seg_from: int) -> str:
    """"2031 extends the 2026–30 rate" / "2030–31 extend the 2024–29 rate" — the PDF's note of
    the change CC BY asks us to indicate (the card itself carries no note, owner)."""
    span = f"{seg_from}–{str(last_stated)[2:]}"
    if target == last_stated + 1:
        return f"{target} extends the {span} rate"
    return f"{last_stated + 1}–{str(target)[2:]} extend the {span} rate"


# --------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------

def project_global_market(*, industry: Any, start_year: Any) -> Optional[GlobalMarketRow]:
    """The global market row for `industry` from `start_year` to +horizon, or None.

    None (never an exception) for: an industry with no global row (most of them — no free
    source), a disabled one, an unusable table, a bad year, a start before the source's first
    year, a target more than `max_extension_years` past its last stated year (WARNING once: the
    sources need their yearly re-check), or a non-finite / implausible result (ERROR)."""
    loaded = _load()
    if loaded is None or not isinstance(industry, str):
        return None
    if industry not in loaded.doc["industries"] or industry in loaded.disabled:
        return None
    return estimate_with_table(loaded.doc, industry=industry, start_year=start_year)


def estimate_with_table(doc: Mapping[str, Any], *, industry: str,
                        start_year: Any) -> Optional[GlobalMarketRow]:
    """The arithmetic against an already-validated table `doc` (no load). Tests call it on a
    candidate table."""
    ind = doc["industries"].get(industry)
    if ind is None:
        return None
    if not _is_year(start_year):
        # The collector always passes a calendar year: anything else is a caller bug that would
        # otherwise drop every global row without a trace.
        _warn_once(industry, "bad_start",
                   "Global market row skipped for %r: start_year %r is not a year — US row only",
                   industry, start_year)
        return None
    horizon, max_ext = doc["horizon_years"], doc["max_extension_years"]
    target_year = start_year + horizon
    try:
        values, seg_from, last_stated = _path(ind, max_ext)
        if start_year < ind["anchor"]["year"]:
            _warn_once(industry, "before_source",
                       "Global market row skipped for %r: start %s is before the source's first "
                       "year %s — US row only", industry, start_year, ind["anchor"]["year"])
            return None
        if start_year not in values or target_year not in values:
            _warn_once(industry, "extension_cap",
                       "Global market row skipped for %r: %s is more than %d years past the "
                       "source's last stated year %s — re-check the source for a newer edition "
                       "(documents/OWNER_TASKS.md); US row only",
                       industry, target_year, max_ext, last_stated)
            return None
        start, target = values[start_year], values[target_year]
        source = doc["sources"][ind["source"]]
    except (KeyError, TypeError, ZeroDivisionError, OverflowError, ValueError) as e:
        logger.error("Global market row failed for %r (%s: %s) — US row only",
                     industry, type(e).__name__, e)
        return None

    if not (math.isfinite(start) and math.isfinite(target) and start > 0 and target > 0):
        logger.error("Global market row for %r is not finite/positive (%r → %r) — US row only",
                     industry, start, target)
        return None
    rate = ((target / start) ** (1.0 / horizon) - 1.0) * 100.0
    if not (_MIN_RATE_PCT <= rate <= _MAX_RATE_PCT):
        logger.error("Global market row for %r implies %.1f%%/yr (outside %s..%s) — bad table "
                     "data; US row only", industry, rate, _MIN_RATE_PCT, _MAX_RATE_PCT)
        return None
    rate_1dp = round(rate, 1) + 0.0
    parts = [f"Global estimate: {source['credit'].strip()}, {_rate_text(rate_1dp)}"]
    if isinstance(ind.get("pdf_note"), str):
        parts.append(ind["pdf_note"].strip())
    if target_year > last_stated:
        parts.append(_extension_text(target_year, last_stated, seg_from))
    label = "; ".join(parts)
    if len(label) > _SOURCE_LABEL_MAX:   # validate_global_table bounds it; a backstop, loudly
        logger.error("Global market row for %r: source line of %d characters cut to %d — the "
                     "change note may be lost; shorten the credit in %s",
                     industry, len(label), _SOURCE_LABEL_MAX, _DATA_PATH.name)
    return GlobalMarketRow(
        market_size_label=ind["label"].strip(),
        scope_note=ind["scope_note"].strip(),
        projection=IndustryTamProjection(
            basis=BASIS_GLOBAL,
            start_year=start_year,
            start_tam_b=round(start, 1),
            target_year=target_year,
            target_tam_b=round(target, 1),
            rate_pct=rate_1dp,
            source_label=label[:_SOURCE_LABEL_MAX],
        ),
    )


def is_complete_global_row(md: Any) -> bool:
    """Whether a STORED market_dynamics dict is a complete global row — the one all-or-nothing
    test the PDF and chat use, mirrored field for field by iOS `MarketDynamics.showsGlobalRow`
    (`tests/test_ios_market_dynamics_measured_only.py` pins the two). A row that fails it is drawn
    as the measured US value under the legacy header, with no scope line — never half a world row."""
    if not isinstance(md, Mapping):
        return False
    if md.get("tam_basis") != "measured" or md.get("projection_basis") != BASIS_GLOBAL:
        return False
    start, target = md.get("projection_year"), md.get("projection_target_year")
    if not (_is_year(start) and _is_year(target) and start < target):
        return False
    for key in ("projection_tam", "projection_target_tam"):
        value = md.get(key)
        if not _is_num(value) or value <= 0:
            return False
    rate = md.get("projection_rate_pct")
    if not _is_num(rate) or abs(rate) > GLOBAL_RATE_MAX_ABS:
        return False
    return _text(md.get("market_size_label")) and _text(md.get("projection_source_label"))
