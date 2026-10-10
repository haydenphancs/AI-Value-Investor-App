"""Form 4 open-market BUY extraction and ranking with an explicit filing window and roles.

A COPY — never a move — of signals_service's CEO Buys pipeline (`_CeoBuy`, `_extract_ceo_buys`,
`_rank_ceo_buys`, `_ceo_price_plausible`), generalised from "the CEO" to the roles the Company
Weekly series need (contract D5): `ceo_buys` asks for ``("ceo",)``, its fallback `insider_buys`
for ``("cfo", "director")``.

Why a copy: another session owns signals_service, and a move would couple a Pro card to the
marketing engine. `tests/test_insider_buys_common_parity.py` pins that, with ``window_start =
now.date() - W`` and ``window_end = now.date() + 2`` (exactly signals' ``-2 <= age <= W`` on a
midnight-UTC filing date, at every time of day), this module returns signals' buys as a
multiset and ranks them in signals' order. When signals_service imports this module in its own
change, delete the parity test.

Two divergences, both pinned by that test, both fail closed:

* a bool is never a number (signals' ``_finite_float(True) == 1.0``);
* `price_plausible(..., require_reference=True)` rejects a line with no usable reference price
  (signals keeps it: "an unverifiable row is not evidence of a unit error").

Pure: stdlib + `_insider_common` only (no FMP, no Supabase), so the marketing adapter and its
tests can import it without the signals stack.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, NamedTuple, Optional, Tuple

from app.services._insider_common import (
    _normalize_cik as normalize_cik,
    classify_insider_transaction,
    insider_reporter_key,
    is_ceo_role,
    is_common_stock,
    officer_title,
)

InsiderRole = Literal["ceo", "cfo", "director"]
ROLES: Tuple[str, ...] = ("ceo", "cfo", "director")

#: Copies of signals_service's CEO Buys constants (same names minus the `_CEO_` prefix).
MAX_FILING_LAG_DAYS = 30            # trade → filing lag beyond this is an old trade filed late
MAX_ROW_DOLLARS = 5_000_000_000.0   # GARBAGE bound only (a real ~$1B CEO buy exists)
PRICE_BAND = 10.0                   # a line's price within ref/10 … ref*10 of the reference
#: Our ticker grammar after `canonical_symbol` (BRK.B → BRK-B), as signals has it.
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,6}(-[A-Z0-9]{1,3})?$")
BAD_SYMBOLS = frozenset({"", "--", "N/A", "NA", "NONE"})


class InsiderBuy(NamedTuple):
    """One qualifying Form 4 line: an insider's open-market purchase of common stock."""

    symbol: str
    reporter: str          # identity key: reportingCik, else the normalised name
    name_raw: str
    title_raw: str
    filing_date: str       # YYYY-MM-DD (the window key)
    transaction_date: str  # YYYY-MM-DD, or "" when unparseable
    shares: float
    price: float
    dollars: float
    ownership: str         # "D" (direct) / "I" (indirect) / ""
    form_type: str         # "4" or "4/A"
    role: str              # "ceo" / "cfo" / "director"
    # Appended AFTER the parity-compared fields (signals' `_CeoBuy` has none): the line's ISSUER
    # CIK (`companyCik`, digits without leading zeros), or None. FMP keys the symbol on the
    # filing's EDGAR folder, so a public company filing as reporting owner of ANOTHER issuer
    # carries its own ticker with that issuer's CIK; the marketing adapter checks it against
    # the profile. Extraction never reads it (the filters and supersession are unchanged).
    company_cik: Optional[str] = None


def canonical_symbol(symbol: Any) -> str:
    """signals' join key: upper-case, "." → "-"; ``""`` for missing input."""
    return str(symbol or "").upper().replace(".", "-")


def _finite(value: Any) -> Optional[float]:
    """``float(value)`` when finite; None for missing, non-numeric, non-finite — and for a bool
    (pinned divergence: signals reads True as 1.0)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_day(value: Any) -> Optional[date]:
    """The ``YYYY-MM-DD`` prefix as a date (signals' `_parse_iso_date`, minus the time)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── roles ─────────────────────────────────────────────────────────────────────

_CFO_RE = re.compile(r"\bcfo\b|chief\s+financial", re.I)
# Not the SITTING CFO: the plain analogue of `_insider_common._NOT_SITTING_RE` (a former /
# retired / outgoing / -elect CFO still files Form 4s for a while). The Company Weekly adapter
# refuses any transitional word anywhere in a CEO / CFO row's raw role text on top of this
# (`role_uncertain`), so this stays the shared, Home-compatible rule.
_CFO_NOT_SITTING_RE = re.compile(
    r"\b(?:former|retired|previous|past|emeritus|outgoing|elect)\b|\bex[-\s]?(?:cfo|chief)\b", re.I
)
# Someone else's title that names the CFO, or a subordinate one ("Deputy CFO", "Vice CFO",
# "Assistant to the CFO"). Adjacency on purpose: "Vice President and CFO" IS the CFO.
_CFO_SUBORDINATE_RE = re.compile(
    r"\b(?:deputy|vice|assistant|associate|regional|divisional|division|segment)[\s-]+"
    r"(?:cfo|chief\s+financial)\b"
    r"|\b(?:to|of)\s+the\s+(?:cfo|chief\s+financial)\b|\bspouse\b",
    re.I,
)
# The CFO of a segment or subsidiary, not of the issuer ("CFO, Consumer Banking",
# "CFO - Europe", "CFO of Subsidiary Bank"); "of the Company" stays the company.
_CFO_SEGMENT_RE = re.compile(
    r"(?:\bcfo\b|chief\s+financial(?:\s+officer)?)\s*"
    r"(?:[-–—:]\s*\w"
    r"|,\s*(?:consumer|commercial|corporate|global|international|north\s+america|americas|europe"
    r"|emea|asia|apac|wealth|retail|investment|banking|operations|division|segment|group|unit"
    r"|business|subsidiary)\b"
    r"|\s+of\s+(?!the\s+company\b|company\b)\w)",
    re.I,
)
_CFO_REGION_TAIL_RE = re.compile(
    r"(?:\bcfo\b|chief\s+financial(?:\s+officer)?)\s+(?:[\w.&'-]+\s+)?"
    r"(?:north\s+america|americas|europe|emea|asia(?:\s+pacific)?|apac|latam|latin\s+america"
    r"|china|japan|india|uk)[\s.,;]*$",
    re.I,
)


def _is_cfo_role(type_of_owner: str) -> bool:
    title = officer_title(type_of_owner)
    return (
        bool(title)
        and bool(_CFO_RE.search(title))
        and not _CFO_NOT_SITTING_RE.search(title)
        and not _CFO_SUBORDINATE_RE.search(title)
        and not _CFO_SEGMENT_RE.search(title)
        and not _CFO_REGION_TAIL_RE.search(title)
    )


def _role_flags(type_of_owner: str) -> List[str]:
    """The comma-separated role FLAGS before any officer title or free-text ``other:`` field
    ("director, 10 percent owner, officer: …" → ["director", "10 percent owner", "officer"]).
    A title is never read as a flag: "officer: VP, Director of Sales" is not a director."""
    low = type_of_owner.lower()
    for marker in ("officer:", "other:"):
        at = low.find(marker)
        if at >= 0:
            low = low[:at] + marker.rstrip(":")
    return [part.strip() for part in low.split(",") if part.strip()]


def insider_role(type_of_owner: Any) -> Optional[InsiderRole]:
    """The reporting person's role for the Company Weekly series, or None.

    * ``"ceo"`` — `_insider_common.is_ceo_role` (the sitting issuer CEO / co-CEO; the CEO wins
      when one person holds both titles, "CEO/CFO");
    * ``"cfo"`` — the officer title matches ``\\bcfo\\b|chief financial``, excluding former,
      deputy, vice, assistant, division and segment CFOs;
    * ``"director"`` — "director" is one of the role flags and the person is neither.
    Strings only."""
    if not isinstance(type_of_owner, str):
        return None
    if is_ceo_role(type_of_owner):
        return "ceo"
    if _is_cfo_role(type_of_owner):
        return "cfo"
    if "director" in _role_flags(type_of_owner):
        return "director"
    return None


# ── extraction ────────────────────────────────────────────────────────────────

def _check_args(window_start: Any, window_end: Any, roles: Any) -> None:
    for name, d in (("window_start", window_start), ("window_end", window_end)):
        if not isinstance(d, date) or isinstance(d, datetime):
            raise ValueError(f"{name} must be a date")
    if window_start > window_end:
        raise ValueError("window_start is after window_end")
    if not isinstance(roles, tuple) or not roles or any(r not in ROLES for r in roles):
        raise ValueError(f"roles must be a non-empty tuple of {ROLES}")


def extract_insider_buys(
    rows: Any, *, window_start: date, window_end: date, roles: Tuple[str, ...]
) -> List[InsiderBuy]:
    """Filter FMP insider rows to open-market common-stock buys by ``roles``, FILED inside
    ``[window_start, window_end]`` (both inclusive), then de-duplicate. Pure; a malformed row is
    skipped, never fatal. The filters and the 4/A rules are signals_service's, in its order:

    a dict; ``transactionType`` a P; acquisition ``A`` and a Form 4 when present; a usable ticker
    (BRK.B → BRK-B); common/ordinary stock; a role in ``roles``; finite shares > 0 and price > 0
    with a sane dollar product (shares ≤ ``securitiesOwned`` when that is known); a parseable
    filing date in the window; a trade date (when parseable) no later than filing + 1 day and no
    more than 30 days before it; an identifiable reporter.

    De-duplication per (symbol, reporter, trade date, ownership): a 4/A SUPERSEDES (a full
    restatement replaces the day, a partial one replaces only the lines it corrects); the same
    (shares, price) line on two filings counts once (the earliest); identical lines on ONE
    filing are separate fills."""
    _check_args(window_start, window_end, roles)
    if not isinstance(rows, list):
        return []
    kept: List[InsiderBuy] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tx = row.get("transactionType")
        if not isinstance(tx, str) or classify_insider_transaction(tx) != "Informative Buy":
            continue
        acq = row.get("acquisitionOrDisposition")
        if acq not in (None, "") and str(acq).strip().upper() != "A":
            continue
        form = row.get("formType")
        form_type = str(form).strip().upper() if form not in (None, "") else "4"
        if not form_type.startswith("4"):
            continue
        raw_symbol = row.get("symbol")
        if not isinstance(raw_symbol, str):
            continue
        symbol = canonical_symbol(raw_symbol.strip())
        if symbol in BAD_SYMBOLS or not SYMBOL_RE.match(symbol):
            continue
        if not is_common_stock(row.get("securityName")):
            continue
        title = row.get("typeOfOwner")
        role = insider_role(title)
        if role is None or role not in roles:
            continue
        shares = _finite(row.get("securitiesTransacted"))
        price = _finite(row.get("price"))
        if shares is None or price is None or shares <= 0 or price <= 0:
            continue
        owned = _finite(row.get("securitiesOwned"))
        if owned is not None and owned > 0 and shares > owned * 1.0001:
            continue
        dollars = _finite(shares * price)
        if dollars is None or dollars > MAX_ROW_DOLLARS:
            continue
        filed = _parse_day(row.get("filingDate"))
        if filed is None or not (window_start <= filed <= window_end):
            continue
        traded = _parse_day(row.get("transactionDate"))
        if traded is not None:
            if traded > filed + timedelta(days=1):
                continue
            if (filed - traded).days > MAX_FILING_LAG_DAYS:
                continue
        reporter = insider_reporter_key(row)
        if not reporter:
            continue
        own = row.get("directOrIndirect")
        kept.append(InsiderBuy(
            symbol=symbol,
            reporter=reporter,
            name_raw=str(row.get("reportingName") or ""),
            title_raw=title,
            filing_date=filed.strftime("%Y-%m-%d"),
            transaction_date=traded.strftime("%Y-%m-%d") if traded else "",
            shares=shares,
            price=price,
            dollars=dollars,
            ownership=str(own).strip().upper() if isinstance(own, str) else "",
            form_type=form_type,
            role=role,
            company_cik=normalize_cik(row.get("companyCik")),
        ))

    groups: Dict[Tuple[str, str, str, str], List[InsiderBuy]] = {}
    for b in kept:
        key = (b.symbol, b.reporter, b.transaction_date or b.filing_date, b.ownership)
        groups.setdefault(key, []).append(b)

    out: List[InsiderBuy] = []
    for members in groups.values():
        amendments = [b for b in members if "/A" in b.form_type]
        if amendments:
            latest = max(b.filing_date for b in amendments)
            amended = [b for b in amendments if b.filing_date == latest]
            originals = [b for b in members if "/A" not in b.form_type]
            if len(amended) >= len(originals):
                members = amended
            else:
                def _corrected(o: InsiderBuy, amended: List[InsiderBuy] = amended) -> bool:
                    return any(
                        (round(a.shares, 4) == round(o.shares, 4))
                        != (round(a.price, 4) == round(o.price, 4))
                        for a in amended
                    )
                members = [o for o in originals if not _corrected(o)] + amended
        by_line: Dict[Tuple[float, float], List[InsiderBuy]] = {}
        for b in members:
            by_line.setdefault((round(b.shares, 4), round(b.price, 4)), []).append(b)
        for line in by_line.values():
            first_filing = min(b.filing_date for b in line)
            out.extend(b for b in line if b.filing_date == first_filing)
    return out


def rank_buy_symbols(buys: List[InsiderBuy], *, min_dollars: float) -> List[Tuple[str, float, str]]:
    """Σ dollars per symbol → ``[(symbol, total_dollars, latest_filing_date), ...]`` for the
    symbols whose finite total reaches ``min_dollars``, in signals' order: total desc, then the
    latest filing desc, then symbol asc (three stable sorts). Totals are NOT rounded."""
    totals: Dict[str, float] = {}
    latest: Dict[str, str] = {}
    for b in buys or []:
        totals[b.symbol] = totals.get(b.symbol, 0.0) + b.dollars
        if b.filing_date > latest.get(b.symbol, ""):
            latest[b.symbol] = b.filing_date
    qualifying = [s for s, t in totals.items() if math.isfinite(t) and t >= min_dollars]
    qualifying.sort()
    qualifying.sort(key=lambda s: latest[s], reverse=True)
    qualifying.sort(key=lambda s: totals[s], reverse=True)
    return [(s, totals[s], latest[s]) for s in qualifying]


def price_plausible(row_price: Any, ref_price: Any, band: float = PRICE_BAND, *,
                    require_reference: bool) -> bool:
    """Is a Form 4 line's price within ``ref/band … ref*band`` of the reference price?

    A missing / non-finite / non-positive row price is never plausible. With no usable
    reference: signals keeps the line (``require_reference=False``); the marketing series
    REJECT it (``require_reference=True``, pinned divergence)."""
    row = _finite(row_price)
    if row is None or row <= 0:
        return False
    ref = _finite(ref_price)
    if ref is None or ref <= 0:
        return not require_reference
    return ref / band <= row <= ref * band


__all__ = [
    "InsiderBuy", "InsiderRole", "ROLES", "MAX_FILING_LAG_DAYS", "MAX_ROW_DOLLARS", "PRICE_BAND",
    "SYMBOL_RE", "BAD_SYMBOLS", "canonical_symbol", "insider_role", "extract_insider_buys",
    "rank_buy_symbols", "price_plausible", "normalize_cik",
]
