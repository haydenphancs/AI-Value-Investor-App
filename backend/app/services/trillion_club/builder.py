"""Build one ``trillion_club_filings`` row from FMP's 13F extract.

The steps, in order (each has a failure mode that has already bitten this codebase):

1. **Fetch strictly.** Quarter N and, when the adjacent quarter exists, N-1 — both LIVE,
   never a stored copy — through ``get_institutional_holdings(..., strict=True)``. The
   default wrapper returns ``[]`` on any error, which is byte-identical to "no holdings":
   a 429 on N-1 would book NVIDIA's whole book as newly reported. A failure, or an empty
   extract for a quarter the caller says is listed, raises :class:`FilingUnavailable`
   and the caller writes NOTHING.
2. **Refuse big books.** More than :data:`MAX_ROWS` rows raises :class:`FilingRefused`.
   A 13F is not automatically "the company's bets": JPMorgan's is 7,720 rows of client
   assets. (13F ingestion is also an explicit owner opt-in, ``use_13f``.)
3. **Normalise per accession** (:func:`normalize_rows`). FMP folds a 13F-HR/A into the
   ORIGINAL quarter and each row keeps its own ``link`` — Berkshire's 2023-Q3 Chubb
   position arrived 228 days later in its own accession. Rows are summed only WITHIN an
   accession; across accessions the latest accession's row wins per CUSIP, so a
   confidential-treatment amendment ADDS rows and a restatement REPLACES them (never
   double-counts). Put/call rows, non-``SH`` rows (``PRN`` = bond principal) and rows
   with a non-finite or non-positive value are excluded and counted.
4. **Resolve symbols.** FMP's own symbol when present; else ``search-isin`` on the
   derived US ISIN for a digit-first CUSIP, then ``search-cusip`` (the only route for a
   CINS number such as Nebius ``N97284108``, and for a Canadian issuer whose ISIN is
   ``CA…``). Several hits -> the actively-trading one whose name matches the issuer. A
   row that never resolves is kept with ``symbol=None`` and recorded in ``unresolved``
   with its first-seen date; after :data:`UNRESOLVED_RETRY_DAYS` it is terminal and stops
   degrading the build (a daily rebuild would otherwise retry it forever). A symbol-less
   CUSIP that is only in the PREVIOUS quarter (it names a ``no_longer_reported`` row) is
   recorded the same way, so a lookup that keeps failing for it also goes terminal.
5. **Profiles** in chunks of 50 (``get_company_profiles_batch`` silently truncates at 50
   and drops failed symbols). Any requested symbol without a profile marks the build
   ``degraded``: its ``ipo_date`` is unknown, and a newly reported row with no known
   listing date must never be dressed up with ``newly_listed``.
6. **Splits** via the shared ``thirteen_f_splits.resolve_13f_split_adjustments``; a
   non-empty ``lookup_failed`` marks the build ``degraded``.
7. **Diff** by CUSIP with ``_whale_common.diff_13f_positions`` — share counts only.
8. **Hash** (:func:`raw_hash_of`) every raw row's accession, CUSIP, shares, value, FMP
   symbol, issuer name, title of class, period and CIK, order-independent, so an
   amendment, a restatement, an FMP symbol re-map, or a corrected period / CIK (which
   flips a row between excluded and included) all change it.

Everything that leaves :meth:`BuiltFiling.as_row` is JSON-safe with finite floats only —
a NaN in a JSONB upsert fails the write and quietly leaves the old row in place.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.schemas.trillion_club import (
    COMPARISON_FIRST_FILING,
    COMPARISON_GAP,
    COMPARISON_QUARTER,
)
from app.services._whale_common import diff_13f_positions
from app.services.thirteen_f_splits import resolve_13f_split_adjustments
from app.services.trillion_club import rules

logger = logging.getLogger(__name__)

#: A filing with more rows than this is refused (JPMorgan's client-asset 13F: 7,720).
MAX_ROWS = 200
#: ``get_company_profiles_batch`` fetches at most 50 symbols per call.
PROFILE_CHUNK = 50
#: Days a symbol-less CUSIP keeps being looked up (and keeps the build ``degraded``).
UNRESOLVED_RETRY_DAYS = 7
#: ``is_small`` below this fraction of the filing's reported value.
SMALL_WEIGHT = 0.01
#: Exchanges a routable U.S. symbol trades on (FMP profile ``exchange``).
US_EXCHANGES = frozenset({"NYSE", "NASDAQ", "AMEX"})
#: Bumped when the normalisation changes, so every stored hash stops matching once.
#: v2 (2026-09-24): the hash now covers each row's period, CIK and title of class.
HASH_VERSION = "tc13f-v2"

BUILD_COMPLETE = "complete"
BUILD_DEGRADED = "degraded"
SOURCE_FMP = "fmp"

#: Always used with ``fullmatch``: with ``match``, ``$`` also accepts a trailing newline,
#: which migration 175's ``cik ~ '^[0-9]{10}$'`` CHECK rejects.
_CIK_RE = re.compile(r"[0-9]{10}")
#: ``period ~ '^[0-9]{4}-Q[1-4]$'`` — on the value as written (no strip).
_PERIOD_CHECK_RE = re.compile(r"[0-9]{4}-Q[1-4]")


class FilingRefused(Exception):
    """The filing is too large to be a company's own investments (> ``MAX_ROWS`` rows)."""


class FilingUnavailable(Exception):
    """FMP could not give a complete answer: a strict fetch failed, or a quarter the
    caller says is listed came back empty. Write nothing; keep the stored row."""


# ── Normalisation (pure) ──────────────────────────────────────────────────────────────


@dataclass
class NormalizedFiling:
    """One quarter's extract after per-accession normalisation.

    ``rows`` — one per CUSIP, largest value first: ``cusip``, ``symbol`` (FMP's, or
    ``None``), ``name`` (the SEC issuer name), ``title_of_class``, ``shares``, ``value``,
    ``accession``. ``accessions`` are in filing order (oldest first).
    """

    rows: List[Dict[str, Any]]
    accessions: List[str]
    filed_on: Optional[date]
    amended_on: Optional[date]
    excluded_rows: int
    raw_row_count: int
    raw_hash: str


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_symbol(value: Any) -> Optional[str]:
    s = _clean_str(value).upper()
    return s if s and s != "--" else None


def _positive_finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):   # float(10**400) overflows: garbage
        return None
    return f if math.isfinite(f) and f > 0 else None


def _iso_date(value: Any) -> Optional[date]:
    s = _clean_str(value)
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _num_key(value: Any) -> str:
    """Hash key for a number: ``100`` and ``100.0`` agree; garbage is kept verbatim."""
    if isinstance(value, bool):
        return f"bool:{value}"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return f"raw:{value!r}"
    except OverflowError:                            # a 400-digit JSON integer
        return f"nonfinite:{value!r}"
    return repr(f) if math.isfinite(f) else f"nonfinite:{value!r}"


def _cik_key(value: Any) -> str:
    """Hash key for a row's CIK, read and zero-padded exactly as :func:`normalize_rows`
    reads it (a non-string CIK is ignored there, so it is ignored here too)."""
    s = _clean_str(value)
    return s.zfill(10) if s else ""


def raw_hash_of(raw: Sequence[Any]) -> str:
    """Order-independent fingerprint of an FMP 13F extract.

    Covers every row — excluded ones too — by accession, CUSIP, shares, value, FMP
    symbol, issuer name, title of class, put/call, share type, filing date, and the two
    fields :func:`normalize_rows` EXCLUDES on: the row's period (``date``) and ``cik``.
    Reordering the rows never changes it; an amendment, a restatement, FMP re-mapping a
    symbol, or FMP correcting a mis-dated / mis-CIK'd row (which flips it between excluded
    and included) always does — the daily job skips a rebuild on an unchanged hash.
    """
    keys: List[Tuple[str, ...]] = []
    for r in raw or ():
        if not isinstance(r, dict):
            keys.append(("<non-dict>", type(r).__name__))
            continue
        period = _iso_date(r.get("date"))
        keys.append((
            rules.accession_from_link(r.get("link")) or rules.accession_from_link(r.get("finalLink")) or "",
            _clean_str(r.get("securityCusip")).upper(),
            _num_key(r.get("shares")),
            _num_key(r.get("value")),
            _clean_str(r.get("symbol")).upper(),
            _clean_str(r.get("nameOfIssuer")),
            _clean_str(r.get("titleOfClass")),
            _clean_str(r.get("putCallShare")).lower(),
            _clean_str(r.get("sharesType")).upper(),
            _clean_str(r.get("filingDate"))[:10],
            period.isoformat() if period else "",
            _cik_key(r.get("cik")),
        ))
    payload = json.dumps(sorted(keys), separators=(",", ":"))
    return f"{HASH_VERSION}:" + hashlib.sha256(payload.encode()).hexdigest()


def normalize_rows(
    raw: Sequence[Any],
    *,
    expected_period_end: Optional[date] = None,
    expected_cik: Optional[str] = None,
    log_ctx: str = "",
) -> NormalizedFiling:
    """Per-accession normalisation of one quarter's FMP 13F extract (pure).

    Excluded and counted: non-dict rows; ``putCallShare`` Put/Call; ``sharesType`` other
    than ``SH`` (``PRN`` is bond principal, and a missing type is not assumed to be
    shares); a malformed CUSIP; shares or value missing / non-finite / <= 0; a row dated
    for another period than ``expected_period_end`` or filed under another CIK.

    Within ONE accession, rows for the same CUSIP are summed (a filer reports a position
    once per manager / discretion). ACROSS accessions the latest accession's row wins per
    CUSIP: a confidential-treatment 13F-HR/A adds its rows, a restatement replaces them.

    ``accessions``, ``filed_on`` and ``amended_on`` come only from rows of THIS period and
    CIK (an other-period / other-CIK row never makes the filing look amended), but do
    include accessions whose rows were all put/call, non-``SH`` or bad numbers.
    """
    excluded = 0
    reasons: Dict[str, int] = {}
    # accession key -> {"order": sort key, "rows": {cusip: row}}
    groups: Dict[str, Dict[str, Any]] = {}
    accession_order: Dict[str, Tuple[str, str, str]] = {}
    filing_dates: List[date] = []
    cik_norm = expected_cik.zfill(10) if isinstance(expected_cik, str) and expected_cik else None

    def _exclude(reason: str) -> None:
        nonlocal excluded
        excluded += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    for r in raw or ():
        if not isinstance(r, dict):
            _exclude("non_dict")
            continue
        # A row for ANOTHER period or ANOTHER filer is not part of this filing at all, so
        # it is excluded BEFORE its accession and filing date are recorded — otherwise one
        # stray Q1 row makes a Q2 book "amended" and "filed" six weeks before Q2 ended.
        # (Put/call, non-SH and bad-number rows below DO belong to this filing: an
        # amendment made only of them is still an amendment of this period.)
        row_period = _iso_date(r.get("date"))
        if expected_period_end is not None and row_period is not None and row_period != expected_period_end:
            _exclude("other_period")
            continue
        row_cik = _clean_str(r.get("cik"))
        if cik_norm and row_cik and row_cik.zfill(10) != cik_norm:
            _exclude("other_cik")
            continue
        accession = rules.accession_from_link(r.get("link")) or rules.accession_from_link(r.get("finalLink"))
        filed = _iso_date(r.get("filingDate"))
        key = accession or f"~unknown:{filed.isoformat() if filed else ''}"
        order = (
            filed.isoformat() if filed else "",
            _clean_str(r.get("acceptedDate")),
            accession or "",
        )
        prev_order = accession_order.get(key)
        accession_order[key] = order if prev_order is None else min(prev_order, order)
        if filed is not None:
            filing_dates.append(filed)

        if _clean_str(r.get("putCallShare")).lower() in ("put", "call"):
            _exclude("put_call")
            continue
        if _clean_str(r.get("sharesType")).upper() != "SH":
            _exclude("not_sh")
            continue
        cusip = rules.normalize_cusip(r.get("securityCusip"))
        if cusip is None:
            _exclude("bad_cusip")
            continue
        shares = _positive_finite(r.get("shares"))
        value = _positive_finite(r.get("value"))
        if shares is None or value is None:
            _exclude("bad_number")
            continue

        bucket = groups.setdefault(key, {"rows": {}})["rows"]
        seen = bucket.get(cusip)
        if seen is None:
            bucket[cusip] = {
                "cusip": cusip,
                "symbol": _clean_symbol(r.get("symbol")),
                "name": _clean_str(r.get("nameOfIssuer")),
                "title_of_class": _clean_str(r.get("titleOfClass")) or None,
                "shares": shares,
                "value": value,
                "accession": accession,
            }
        else:
            seen["shares"] += shares
            seen["value"] += value
            seen["symbol"] = seen["symbol"] or _clean_symbol(r.get("symbol"))
            seen["name"] = seen["name"] or _clean_str(r.get("nameOfIssuer"))
            seen["title_of_class"] = seen["title_of_class"] or (_clean_str(r.get("titleOfClass")) or None)

    ordered_keys = sorted(accession_order, key=lambda k: accession_order[k])
    merged: Dict[str, Dict[str, Any]] = {}
    for key in ordered_keys:                      # oldest first: later accessions overwrite
        for cusip, row in groups.get(key, {}).get("rows", {}).items():
            earlier = merged.get(cusip)
            if earlier is not None:
                # The later accession's NUMBERS win; an identifier it left blank does not
                # erase the one the original filing carried.
                row["symbol"] = row["symbol"] or earlier["symbol"]
                row["name"] = row["name"] or earlier["name"]
                row["title_of_class"] = row["title_of_class"] or earlier["title_of_class"]
            merged[cusip] = row
    unknown = [k for k in ordered_keys if k.startswith("~unknown:")]
    if unknown:
        logger.warning(
            "trillion club 13F normalise (%s): %d row group(s) carry no parseable SEC "
            "accession — amendment handling for them falls back to the filing date",
            log_ctx, len(unknown),
        )
    if excluded:
        logger.info(
            "trillion club 13F normalise (%s): excluded %d of %d row(s) %s",
            log_ctx, excluded, len(raw or ()), dict(sorted(reasons.items())),
        )
    accessions = [k for k in ordered_keys if not k.startswith("~unknown:")]
    rows = sorted(merged.values(), key=lambda x: (-x["value"], x["cusip"]))
    filed_on = min(filing_dates) if filing_dates else None
    amended_on = max(filing_dates) if len(accessions) > 1 and filing_dates else None
    return NormalizedFiling(
        rows=rows,
        accessions=accessions,
        filed_on=filed_on,
        amended_on=amended_on,
        excluded_rows=excluded,
        raw_row_count=len(raw or ()),
        raw_hash=raw_hash_of(raw),
    )


# ── Symbol resolution ──────────────────────────────────────────────────────────────────

_NAME_NOISE = frozenset({
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC",
    "NV", "N", "V", "SA", "AG", "SE", "LLC", "LP", "HOLDINGS", "HLDGS", "HOLDING", "GROUP",
    "GRP", "THE", "CLASS", "CL", "COM", "SHS", "ORD", "ADR", "ADS", "NEW",
})


def _name_tokens(name: Any) -> List[str]:
    text = re.sub(r"[^A-Z0-9]+", " ", _clean_str(name).upper())
    return [t for t in text.split() if t not in _NAME_NOISE]


_CLASS_SUFFIX_RE = re.compile(
    r"[\s,]+(?:class\s+[a-z]\s+)?(?:common\s+stock|ordinary\s+shares|"
    r"subordinate\s+voting\s+shares|american\s+depositary\s+shares|depositary\s+shares)$",
    re.IGNORECASE,
)


def display_name(profile_name: Any, issuer_name: Any, fallback: str) -> str:
    """A holding's display name: FMP's company name without a trailing security-class
    phrase ("CoreWeave, Inc. Class A Common Stock" -> "CoreWeave, Inc."), else the SEC
    issuer name, else ``fallback`` (the symbol or CUSIP). Never empty."""
    name = _CLASS_SUFFIX_RE.sub("", _clean_str(profile_name)).strip()
    return name or _clean_str(issuer_name) or fallback


def names_match(issuer: Any, candidate: Any) -> bool:
    """Loose issuer-name match: the first significant word agrees, allowing the SEC's
    truncation ("SPACE EXPLORATION TECHN CORP" ~ "Space Exploration Technologies Corp.")."""
    a, b = _name_tokens(issuer), _name_tokens(candidate)
    if not a or not b:
        return False
    x, y = a[0], b[0]
    return x == y or (len(x) >= 4 and y.startswith(x)) or (len(y) >= 4 and x.startswith(y))


def _candidate_symbols(hits: Any) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for h in hits if isinstance(hits, list) else ():
        if not isinstance(h, dict):
            continue
        sym = _clean_symbol(h.get("symbol"))
        if sym and sym not in {s for s, _ in out}:
            out.append((sym, _clean_str(h.get("name")) or _clean_str(h.get("companyName"))))
    return out


async def _profiles_for(fmp: Any, symbols: Sequence[str], log_ctx: str) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
    """``(profiles by symbol, symbols with no profile)`` in chunks of ``PROFILE_CHUNK``."""
    wanted = sorted({s for s in symbols if s})
    found: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(wanted), PROFILE_CHUNK):
        chunk = wanted[i:i + PROFILE_CHUNK]
        try:
            got = await fmp.get_company_profiles_batch(chunk)
        except Exception as e:
            logger.warning(
                "trillion club build (%s): profile batch of %d failed (%s: %s)",
                log_ctx, len(chunk), type(e).__name__, e,
            )
            continue
        for p in got if isinstance(got, list) else ():
            if isinstance(p, dict):
                sym = _clean_symbol(p.get("symbol"))
                if sym in chunk:
                    found[sym] = p
    return found, set(wanted) - set(found)


async def _resolve_one(
    fmp: Any, cusip: str, issuer: str, log_ctx: str
) -> Tuple[Optional[str], bool]:
    """``(symbol or None, lookup_failed)`` for one symbol-less CUSIP."""
    hits: List[Tuple[str, str]] = []
    try:
        isin = rules.cusip_to_us_isin(cusip)
        if isin:
            hits = _candidate_symbols(await fmp.search_isin(isin))
        if not hits:
            hits = _candidate_symbols(await fmp.search_cusip(cusip))
    except Exception as e:
        logger.warning(
            "trillion club build (%s): symbol lookup for CUSIP %s failed (%s: %s)",
            log_ctx, cusip, type(e).__name__, e,
        )
        return None, True
    if not hits:
        return None, False
    if len(hits) == 1:
        return hits[0][0], False

    profiles, _missing = await _profiles_for(fmp, [s for s, _ in hits], log_ctx)
    if not profiles:
        logger.warning(
            "trillion club build (%s): CUSIP %s has %d candidate symbols (%s) and no "
            "profile could be read to choose between them", log_ctx, cusip, len(hits),
            ", ".join(s for s, _ in hits),
        )
        return None, True
    active = [s for s, _ in hits if (profiles.get(s) or {}).get("isActivelyTrading") is True]
    matched = [s for s in active if names_match(issuer, profiles[s].get("companyName"))]
    if len(matched) == 1:
        return matched[0], False
    if not matched and len(active) == 1:
        # Every hit is THIS identifier; the one still trading is its live listing (a
        # rename such as Yandex N.V. -> Nebius Group N.V. keeps the CUSIP and ISIN).
        logger.info(
            "trillion club build (%s): CUSIP %s -> %s, the only actively-trading candidate "
            "(issuer %r did not name-match)", log_ctx, cusip, active[0], issuer,
        )
        return active[0], False
    logger.warning(
        "trillion club build (%s): CUSIP %s is ambiguous — candidates %s, actively "
        "trading %s, name-matched %s; left unresolved", log_ctx, cusip,
        [s for s, _ in hits], active, matched,
    )
    return None, False


async def _resolve_symbols(
    fmp: Any,
    issuers: Mapping[str, str],
    *,
    stored_unresolved: Mapping[str, str],
    today: date,
    log_ctx: str,
) -> Tuple[Dict[str, str], Set[str], bool]:
    """``(cusip -> symbol, terminal cusips skipped, any lookup failed)``."""
    resolved: Dict[str, str] = {}
    terminal: Set[str] = set()
    failed = False
    for cusip in sorted(issuers):
        first_seen = _iso_date((stored_unresolved or {}).get(cusip))
        if first_seen is not None and (today - first_seen).days > UNRESOLVED_RETRY_DAYS:
            terminal.add(cusip)
            continue
        sym, lookup_failed = await _resolve_one(fmp, cusip, issuers[cusip], log_ctx)
        failed = failed or lookup_failed
        if sym:
            resolved[cusip] = sym
    return resolved, terminal, failed


# ── The built row ──────────────────────────────────────────────────────────────────────


def _assert_json_safe(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"trillion club filing row: non-finite float at {path}")
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _assert_json_safe(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"trillion club filing row: non-string key {k!r} at {path}")
            _assert_json_safe(v, f"{path}.{k}")
        return
    raise ValueError(f"trillion club filing row: {type(value).__name__} is not JSON at {path}")


@dataclass
class BuiltFiling:
    """One ``trillion_club_filings`` row. ``degraded_reasons`` is NOT a column (logging)."""

    cik: str
    period: str
    period_end: date
    filed_on: Optional[date]
    amended_on: Optional[date]
    accessions: List[str]
    total_value: float
    position_count: int
    holdings: List[Dict[str, Any]]
    changes: Dict[str, Any]
    excluded_rows: int
    unresolved: Dict[str, str]
    raw_hash: str
    build_status: str
    source: str = SOURCE_FMP
    degraded_reasons: List[str] = field(default_factory=list)

    def as_row(self) -> Dict[str, Any]:
        """The upsert payload: JSON-safe, finite floats only, matching the table CHECKs.

        Raises ``ValueError`` (naming the field) rather than letting Postgres reject the
        upsert — a failed JSONB write quietly leaves the previous row in place. The checks
        mirror migration 175 exactly: Python's ``$`` also matches before a trailing newline
        and ``rules.parse_period`` strips whitespace, while the CHECK regexes do neither,
        so both are matched with ``fullmatch`` on the value as written.
        """
        if not isinstance(self.cik, str) or not _CIK_RE.fullmatch(self.cik):
            raise ValueError(f"trillion club filing row: bad cik {self.cik!r}")
        if not isinstance(self.period, str) or not _PERIOD_CHECK_RE.fullmatch(self.period):
            raise ValueError(f"trillion club filing row: bad period {self.period!r}")
        if isinstance(self.period_end, datetime) or not isinstance(self.period_end, date):
            raise ValueError(f"trillion club filing row: period_end must be a date, got {self.period_end!r}")
        for name in ("filed_on", "amended_on"):
            v = getattr(self, name)
            if v is not None and (isinstance(v, datetime) or not isinstance(v, date)):
                raise ValueError(f"trillion club filing row: {name} must be a date or None, got {v!r}")
        if not isinstance(self.accessions, list) or not all(isinstance(a, str) for a in self.accessions):
            raise ValueError(f"trillion club filing row: accessions must be a list of str, got {self.accessions!r}")
        if not isinstance(self.holdings, list):         # CHECK jsonb_typeof(holdings) = 'array'
            raise ValueError(f"trillion club filing row: holdings must be a list, got {type(self.holdings).__name__}")
        if not isinstance(self.changes, dict):          # CHECK jsonb_typeof(changes) = 'object'
            raise ValueError(f"trillion club filing row: changes must be a dict, got {type(self.changes).__name__}")
        if not isinstance(self.unresolved, Mapping):    # CHECK jsonb_typeof(unresolved) = 'object'
            raise ValueError(f"trillion club filing row: unresolved must be a mapping, got {type(self.unresolved).__name__}")
        if not isinstance(self.raw_hash, str) or not self.raw_hash:
            raise ValueError(f"trillion club filing row: raw_hash must be a non-empty str, got {self.raw_hash!r}")
        if self.build_status not in (BUILD_COMPLETE, BUILD_DEGRADED):
            raise ValueError(f"trillion club filing row: bad build_status {self.build_status!r}")
        if self.source not in ("fmp", "edgar"):
            raise ValueError(f"trillion club filing row: bad source {self.source!r}")
        row = {
            "cik": self.cik,
            "period": self.period,
            "period_end": self.period_end.isoformat(),
            "filed_on": self.filed_on.isoformat() if self.filed_on else None,
            "amended_on": self.amended_on.isoformat() if self.amended_on else None,
            "accessions": list(self.accessions),
            "total_value": float(self.total_value),
            "position_count": int(self.position_count),
            "holdings": self.holdings,
            "changes": self.changes,
            "excluded_rows": int(self.excluded_rows),
            "unresolved": dict(self.unresolved),
            "raw_hash": self.raw_hash,
            "build_status": self.build_status,
            "source": self.source,
        }
        _assert_json_safe(row, "row")
        if row["total_value"] < 0 or row["position_count"] < 0 or row["excluded_rows"] < 0:
            raise ValueError("trillion club filing row: negative total/count")
        return row


# ── Build ──────────────────────────────────────────────────────────────────────────────


async def _fetch_extract(fmp: Any, cik: str, year: int, quarter: int, log_ctx: str) -> List[Dict[str, Any]]:
    label = rules.period_label(year, quarter)
    try:
        raw = await fmp.get_institutional_holdings(cik, year, quarter, strict=True)
    except Exception as e:
        raise FilingUnavailable(
            f"13F extract for cik={cik} {label} failed ({type(e).__name__}: {e})"
        ) from e
    if not isinstance(raw, list):
        raise FilingUnavailable(
            f"13F extract for cik={cik} {label} returned {type(raw).__name__}, not a list"
        )
    if not raw:
        raise FilingUnavailable(
            f"13F extract for cik={cik} {label} is EMPTY for a quarter FMP lists — "
            f"refusing to build (an empty book would read as 'sold everything')"
        )
    if len(raw) > MAX_ROWS:
        logger.warning(
            "trillion club build (%s): %s has %d rows > %d — refused (a book this size is "
            "client assets, not the company's own stakes)", log_ctx, label, len(raw), MAX_ROWS,
        )
        raise FilingRefused(f"13F for cik={cik} {label} has {len(raw)} rows (> {MAX_ROWS})")
    return raw


async def _older_filing(fmp: Any, cik: str, year: int, quarter: int) -> Optional[str]:
    """The newest period on file OLDER than ``(year, quarter)``, or ``None``.

    Raises :class:`FilingUnavailable` when the ``dates`` answer does not list
    ``(year, quarter)`` itself: the caller just fetched rows for that quarter, so an answer
    that omits it (``[]``, a non-list, unparseable rows, a lagging index) is not evidence
    that nothing older exists — reading it as "first filing" would stamp a COMPLETE build
    that the hash-skip then keeps.
    """
    try:
        dates = await fmp.get_institutional_filing_dates(cik, strict=True)
    except Exception as e:
        raise FilingUnavailable(
            f"13F filing dates for cik={cik} failed ({type(e).__name__}: {e}) — cannot "
            f"tell a first filing from a gap"
        ) from e
    listed: Set[Tuple[int, int]] = set()
    for d in dates if isinstance(dates, list) else ():
        try:
            y, q = d.get("year"), d.get("quarter")
            if isinstance(y, bool) or isinstance(q, bool):
                continue
            y, q = int(y), int(q)
        except (AttributeError, TypeError, ValueError):
            continue
        if q in (1, 2, 3, 4) and 1900 <= y <= 9998:
            listed.add((y, q))
    if (year, quarter) not in listed:
        raise FilingUnavailable(
            f"13F filing dates for cik={cik} do not list {rules.period_label(year, quarter)}, "
            f"the quarter being built ({len(listed)} usable period(s) of "
            f"{len(dates) if isinstance(dates, list) else type(dates).__name__}) — cannot "
            f"tell a first filing from a gap"
        )
    older = [yq for yq in listed if yq < (year, quarter)]
    return rules.period_label(*max(older)) if older else None


async def build_filing(
    fmp: Any,
    cik: str,
    year: int,
    quarter: int,
    *,
    prev_quarter_available: bool,
    actions: Any,
    stored_unresolved: Mapping[str, str],
    today: date,
    older_filing_exists: Optional[bool] = None,
) -> BuiltFiling:
    """Build the ``trillion_club_filings`` row for ``(cik, year, quarter)``.

    ``prev_quarter_available`` — FMP's ``dates`` lists the ADJACENT previous quarter, so it
    is fetched live and diffed (``comparison='quarter'``). Otherwise the comparison is
    ``'gap'`` when an older filing exists and ``'first_filing'`` when none does; pass
    ``older_filing_exists`` if the caller already knows, else the builder asks ``dates``.

    ``actions`` is the corporate-actions primitive (``corporate_actions_source(...)``);
    ``stored_unresolved`` is the stored row's ``unresolved`` map; ``today`` dates the
    first sighting of a new unresolved CUSIP.

    Raises :class:`FilingUnavailable` (write nothing), :class:`FilingRefused` (too many
    rows) or ``ValueError`` (bad arguments).
    """
    if not isinstance(cik, str) or not _CIK_RE.fullmatch(cik):
        raise ValueError(f"build_filing: cik must be 10 digits, got {cik!r}")
    if isinstance(today, datetime):
        today = today.date()
    if not isinstance(today, date):
        raise ValueError(f"build_filing: today must be a date, got {today!r}")
    period = rules.period_label(year, quarter)
    period_end = rules.quarter_end(year, quarter)
    log_ctx = f"cik={cik} period={period}"

    prev_yq = rules.previous_quarter(year, quarter)
    if prev_quarter_available:
        results = await asyncio.gather(
            _fetch_extract(fmp, cik, year, quarter, log_ctx),
            _fetch_extract(fmp, cik, *prev_yq, log_ctx),
            return_exceptions=True,
        )
        for r in results:              # current first: its failure is the one to report
            if isinstance(r, BaseException):
                raise r
        raw, prev_raw = results
        comparison, prev_period = COMPARISON_QUARTER, rules.period_label(*prev_yq)
    else:
        raw = await _fetch_extract(fmp, cik, year, quarter, log_ctx)
        prev_raw = None
        if older_filing_exists is None:
            prev_period = await _older_filing(fmp, cik, year, quarter)
            older = prev_period is not None
        else:
            older, prev_period = bool(older_filing_exists), None
        comparison = COMPARISON_GAP if older else COMPARISON_FIRST_FILING
        if comparison == COMPARISON_FIRST_FILING:
            prev_period = None

    norm = normalize_rows(raw, expected_period_end=period_end, expected_cik=cik, log_ctx=log_ctx)
    if not norm.rows:
        raise FilingUnavailable(
            f"13F for {log_ctx}: all {norm.raw_row_count} row(s) were excluded "
            f"(put/call, non-SH, malformed) — nothing to build"
        )
    prev_norm = None
    if prev_raw is not None:
        prev_norm = normalize_rows(
            prev_raw, expected_period_end=rules.quarter_end(*prev_yq), expected_cik=cik,
            log_ctx=f"cik={cik} period={prev_period}",
        )
        if not prev_norm.rows:
            raise FilingUnavailable(
                f"13F for cik={cik} {prev_period}: every row excluded — cannot diff {period}"
            )

    degraded: List[str] = []

    # Symbols ------------------------------------------------------------------------
    issuers: Dict[str, str] = {}
    for row in norm.rows + (prev_norm.rows if prev_norm else []):
        if row["symbol"] is None:
            issuers.setdefault(row["cusip"], row["name"])
    resolved, terminal, lookup_failed = await _resolve_symbols(
        fmp, issuers, stored_unresolved=stored_unresolved or {}, today=today, log_ctx=log_ctx,
    )
    if lookup_failed:
        degraded.append("symbol_lookup_failed")
    for row in norm.rows + (prev_norm.rows if prev_norm else []):
        if row["symbol"] is None and row["cusip"] in resolved:
            row["symbol"] = resolved[row["cusip"]]

    unresolved: Dict[str, str] = {}
    pending: List[str] = []
    for row in norm.rows:
        if row["symbol"] is None:
            first = _iso_date((stored_unresolved or {}).get(row["cusip"]))
            unresolved[row["cusip"]] = (first or today).isoformat()
            if row["cusip"] not in terminal:
                pending.append(row["cusip"])
    if pending:
        degraded.append("unresolved_pending:" + ",".join(sorted(pending)))
    # A symbol-less CUSIP only in N-1 (an exited position, typically a delisted or acquired
    # issuer) is looked up too — it names a no_longer_reported row. It is recorded here
    # with its first-seen date so it reaches the same 7-day terminal state: otherwise a
    # lookup that keeps FAILING for it keeps N degraded, and the daily job rebuilds N
    # forever. Only a failed lookup degrades the build for it; "no match" does not.
    current_cusips = {row["cusip"] for row in norm.rows}
    prev_only: List[str] = []
    for row in prev_norm.rows if prev_norm else ():
        if row["symbol"] is None and row["cusip"] not in current_cusips:
            first = _iso_date((stored_unresolved or {}).get(row["cusip"]))
            unresolved[row["cusip"]] = (first or today).isoformat()
            prev_only.append(row["cusip"])
    if prev_only:
        logger.info(
            "trillion club build (%s): %d previous-quarter-only CUSIP(s) have no symbol (%s) "
            "— tracked in unresolved; lookups stop after %d days", log_ctx, len(prev_only),
            ",".join(sorted(prev_only)), UNRESOLVED_RETRY_DAYS,
        )

    # Profiles -------------------------------------------------------------------------
    profiles, missing = await _profiles_for(fmp, [r["symbol"] for r in norm.rows if r["symbol"]], log_ctx)
    if missing:
        degraded.append("profiles_missing:" + ",".join(sorted(missing)))

    total = math.fsum(r["value"] for r in norm.rows)
    if not (math.isfinite(total) and total > 0):
        raise FilingUnavailable(f"13F for {log_ctx}: reported total {total!r} is not usable")
    holdings: List[Dict[str, Any]] = []
    for r in norm.rows:
        p = profiles.get(r["symbol"]) if r["symbol"] else None
        exchange = _clean_str((p or {}).get("exchange")).upper() or None
        weight = r["value"] / total
        holdings.append({
            "cusip": r["cusip"],
            "symbol": r["symbol"],
            "name": display_name((p or {}).get("companyName"), r["name"], r["symbol"] or r["cusip"]),
            "title_of_class": r["title_of_class"],
            "shares": r["shares"],
            "value": r["value"],
            "weight": weight,
            "is_small": weight < SMALL_WEIGHT,
            "sector": _clean_str((p or {}).get("sector")) or None,
            "ipo_date": d.isoformat() if (d := _iso_date((p or {}).get("ipoDate"))) else None,
            "exchange": exchange,
            "routable": bool(
                r["symbol"] and p and exchange in US_EXCHANGES
                and (p or {}).get("isActivelyTrading") is not False
            ),
        })

    # Splits + diff -----------------------------------------------------------------------
    split_ratios: Dict[str, float] = {}
    unclassified: Set[str] = set()
    if comparison == COMPARISON_QUARTER and prev_norm is not None:
        if actions is None:
            from app.services.corporate_actions_service import corporate_actions_source
            actions = corporate_actions_source(None)
        split_ratios, unclassified, split_failed = await resolve_13f_split_adjustments(
            [h for h in holdings if h["symbol"]],
            [r for r in prev_norm.rows if r["symbol"]],
            rules.quarter_end(*prev_yq).isoformat(),
            period_end.isoformat(),
            actions=actions,
            log_ctx=log_ctx,
        )
        if split_failed:
            degraded.append("split_lookup_failed:" + ",".join(sorted(split_failed)))
    changes = diff_13f_positions(
        holdings,
        prev_norm.rows if prev_norm is not None else None,
        split_ratios=split_ratios,
        unclassified=unclassified,
        comparison=comparison,
        prev_ipo_cutoff=rules.quarter_end(*prev_yq),
        prev_period=prev_period,
    )

    status = BUILD_DEGRADED if degraded else BUILD_COMPLETE
    built = BuiltFiling(
        cik=cik,
        period=period,
        period_end=period_end,
        filed_on=norm.filed_on,
        amended_on=norm.amended_on,
        accessions=norm.accessions,
        total_value=total,
        position_count=len(holdings),
        holdings=holdings,
        changes=changes,
        excluded_rows=norm.excluded_rows,
        unresolved=unresolved,
        raw_hash=norm.raw_hash,
        build_status=status,
        source=SOURCE_FMP,
        degraded_reasons=degraded,
    )
    log = logger.warning if degraded else logger.info
    log(
        "trillion club build (%s): %s — %d position(s), $%.0f, %d accession(s), %d "
        "excluded, comparison=%s counts=%s%s", log_ctx, status, len(holdings), total,
        len(norm.accessions), norm.excluded_rows, comparison, changes["counts"],
        f", degraded: {'; '.join(degraded)}" if degraded else "",
    )
    return built


__all__ = [
    "MAX_ROWS", "PROFILE_CHUNK", "UNRESOLVED_RETRY_DAYS", "SMALL_WEIGHT", "US_EXCHANGES",
    "HASH_VERSION", "BUILD_COMPLETE", "BUILD_DEGRADED", "FilingRefused", "FilingUnavailable",
    "NormalizedFiling", "BuiltFiling", "normalize_rows", "raw_hash_of", "names_match",
    "display_name",
    "build_filing",
]
