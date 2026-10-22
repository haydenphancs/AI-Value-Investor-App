"""Checks FMP's quarterly buyback figure against the company's own SEC filing.

FMP's ``commonStockRepurchased`` is not always a buyback. A survey of 15 issuers x 8
quarters against SEC XBRL (2026-10-22) found 11 of 120 rows that were something else:

* INTC Q2 2025 / Q2 2026 ($181M / $617M) — "Restricted stock unit withholdings"
  (``PaymentsRelatedToTaxWithholdingForShareBasedCompensation``), and each a HALF-YEAR
  total put into Q2. Intel's 10-Qs say "No shares were repurchased"; its last buyback was
  Q1 2021. The Signal of Confidence chart drew them as green Buybacks bars.
* META Q4 2024 (all withholding), AMD Q2 2026 (all withholding), AMD Q3 2024..Q2 2025 and
  NVDA Q1 FY27 (buybacks PLUS withholding), JPM Q4 2024 / Q1 2025 (buybacks PLUS
  preferred-stock redemptions).

Wherever a filing carries a non-zero repurchase line, FMP copies it exactly (67 of 67), so
the rule below only ever LOWERS a figure, and only when the filing explains the excess. It
never raises one (a restated quarter, a buyback FMP missed): over-correcting a real
repurchaser read the market's largest buyers as "Low" once already.

The decision for one FMP quarter, with B = FMP's buyback dollars (> 0):

1. The filing's repurchase for that QUARTER (R) is known — a 3-month fact, a year-to-date
   fact minus the one a quarter earlier with the same start, or a year-to-date fact of 0:
     * B ≈ R                                           → keep B ("verified")
     * B > R, and R ≈ 0 or B − R ≈ W, P or W + P         → R ("corrected")
     * R > B                                           → keep B ("sec_higher", logged)
     * otherwise                                       → keep B ("mismatch", logged)
2. R is unknown (no repurchase fact ending there, or only a year-to-date value whose
   quarter cannot be isolated):
     * no positive repurchase fact ends there, and B ≈ W, P or W + P → 0 ("corrected")
     * otherwise                                       → keep B ("unverified", logged)

W = employee tax withholding, P = preferred (or redeemable noncontrolling-interest)
redemptions; each is compared as the quarter's own value AND as every year-to-date value
ending on that date, because some issuers tag only the half year (INTC's Q1 10-Qs never
broke the line out) and FMP then puts the whole half year into Q2. "≈" is within
max($1,000, 0.1% of the LARGER figure compared): B against R, or the excess (or B)
against each withholding / preferred amount. FMP copies the XBRL dollars, so a real match
is exact, and a loose one would let an unrelated amount lower a real buyback.

A year-to-date difference is taken only between two spans on the SAME basis: when an
issuer re-files a year under a new presentation (TGT's 2024 10-K split withholding out of
FY2021's repurchase line), a restated full year minus a nine months filed only on the old
basis is not a quarter — it would lower a real buyback by the withholding.

Pure and synchronous: no I/O, no logging. The service logs the diagnostics it returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "REPURCHASE_TAG",
    "WITHHOLDING_TAG",
    "PREFERRED_TAGS",
    "SEC_BUYBACK_TAGS",
    "SecFact",
    "SecBuybackFacts",
    "BuybackCheck",
    "facts_from_concept",
    "check_quarter_buyback",
    "buyback_overrides",
]

REPURCHASE_TAG = "PaymentsForRepurchaseOfCommonStock"
WITHHOLDING_TAG = "PaymentsRelatedToTaxWithholdingForShareBasedCompensation"
# JPM tags its preferred redemptions with the first one (3,000M in Q1 2025). PNC tags its
# preferred redemptions under the noncontrolling-interest concept in its 10-Ks (1,500M /
# 1,000M / 500M in FY2022-24, each FMP's Q4 excess to the dollar). All three only ever
# EXPLAIN an excess, so a new one can lower a figure, never raise one.
PREFERRED_TAGS: Tuple[str, ...] = (
    "PaymentsForRepurchaseOfRedeemablePreferredStock",
    "PaymentsForRepurchaseOfPreferredStockAndPreferenceStock",
    "PaymentsForRepurchaseOfRedeemableNoncontrollingInterest",
)
#: Every concept the check reads, in fetch order.
SEC_BUYBACK_TAGS: Tuple[str, ...] = (REPURCHASE_TAG, WITHHOLDING_TAG, *PREFERRED_TAGS)

#: Annual and quarterly reports, and their amendments. An 8-K or a 20-F fact is not one of
#: the statements FMP's quarterly row is built from.
_STATEMENT_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A", "10-QT", "10-KT"})

#: A fiscal quarter is 12-14 weeks (84-98 days) — or 16-17 weeks (112-119) in the 16/12/12/12
#: and 12/12/16/12 retail calendars (KR's Q1, COST / AZO / PEP's Q4) and a 53rd week on top;
#: calendar quarters run 89-92 days. A half year is at least 168 days, so the band never
#: takes a year-to-date span for a quarter.
_QUARTER_DAYS = (80, 126)
#: FMP dates a row by the period end it read, which is the filing's own end date. The slack
#: absorbs a vendor that normalises a 52/53-week end to a month end.
MATCH_TOLERANCE_DAYS = 7

_ABS_TOLERANCE = 1_000.0
_REL_TOLERANCE = 0.001


@dataclass(frozen=True)
class SecFact:
    """One duration fact of a cash-flow concept, in USD."""

    start: date
    end: date
    val: float
    filed: str
    form: str
    #: The ``filed`` date of the first filing that changed this span's value from its
    #: original, or None when every filing reported the same figure.
    restated_on: Optional[str] = None

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass
class SecBuybackFacts:
    """The parsed concepts for one company. ``None`` for a concept means it was not
    fetched or the company never used it (a 404): never read as zero."""

    repurchase: Optional[List[SecFact]] = None
    withholding: Optional[List[SecFact]] = None
    preferred: Dict[str, List[SecFact]] = field(default_factory=dict)

    @classmethod
    def from_concepts(cls, concepts: Mapping[str, Any]) -> "SecBuybackFacts":
        """From ``{tag: companyconcept payload or None}`` (the service's answer)."""
        return cls(
            repurchase=facts_from_concept(concepts.get(REPURCHASE_TAG)),
            withholding=facts_from_concept(concepts.get(WITHHOLDING_TAG)),
            preferred={
                tag: facts
                for tag in PREFERRED_TAGS
                if (facts := facts_from_concept(concepts.get(tag))) is not None
            },
        )

    @property
    def is_empty(self) -> bool:
        return not (self.repurchase or self.withholding or any(self.preferred.values()))

    @property
    def newest_end(self) -> Optional[date]:
        """The latest period end among every concept's facts — how far this filing data
        reaches. None when there are no facts at all."""
        ends = [
            f.end
            for facts in (self.repurchase, self.withholding, *self.preferred.values())
            for f in (facts or [])
        ]
        return max(ends) if ends else None


@dataclass(frozen=True)
class BuybackCheck:
    """The figure to chart for one quarter and why."""

    dollars: float
    outcome: str  # verified | corrected | sec_higher | mismatch | unverified
    detail: str

    @property
    def changed(self) -> bool:
        return self.outcome == "corrected"


# ── Parsing ────────────────────────────────────────────────────────────


def _parse_day(value: Any) -> Optional[date]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def facts_from_concept(payload: Any) -> Optional[List[SecFact]]:
    """The USD duration facts of one ``companyconcept`` payload (or one concept of a
    ``companyfacts`` payload — the same ``{"units": {...}}`` shape).

    ``None`` when there is no payload (404, never fetched) or no USD unit at all, so the
    caller can tell "the company never used this concept" from "it used it and reported
    0". Instants, other forms, malformed dates and non-finite or negative values are
    dropped: a payment concept is reported as a positive outflow. When several filings
    report the SAME span (the 10-Q, then the next year's comparative column, then a
    restatement), the latest ``filed`` wins.
    """
    if not isinstance(payload, Mapping):
        return None
    units = payload.get("units")
    if not isinstance(units, Mapping):
        return None
    rows = units.get("USD")
    if not isinstance(rows, list):
        return None

    filings: Dict[Tuple[date, date], List[Tuple[str, float, str]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        form = row.get("form")
        if form not in _STATEMENT_FORMS:
            continue
        start, end = _parse_day(row.get("start")), _parse_day(row.get("end"))
        if start is None or end is None or end <= start:
            continue
        raw = row.get("val")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        val = float(raw)
        if not math.isfinite(val) or val < 0:
            continue
        filed = row.get("filed") if isinstance(row.get("filed"), str) else ""
        filings.setdefault((start, end), []).append((filed, val, form))

    facts: List[SecFact] = []
    for (start, end), filed_rows in filings.items():
        # Stable sort on the filing date: of two filings on one day, the later row wins,
        # as the dict overwrite it replaces did.
        ordered = sorted(filed_rows, key=lambda r: r[0])
        original = ordered[0][1]
        restated_on = next(
            (filed for filed, val, _ in ordered
             if not _approx(val, original, _tolerance(max(abs(val), abs(original))))),
            None,
        )
        filed, val, form = ordered[-1]
        facts.append(SecFact(start=start, end=end, val=val, filed=filed, form=form,
                             restated_on=restated_on))
    return sorted(facts, key=lambda f: (f.end, f.start))


# ── Quarters from year-to-date facts ───────────────────────────────────


def _is_quarter(days: int) -> bool:
    return _QUARTER_DAYS[0] <= days <= _QUARTER_DAYS[1]


def _near(a: date, b: date, days: int = MATCH_TOLERANCE_DAYS) -> bool:
    return abs((a - b).days) <= days


def _ending_near(facts: Sequence[SecFact], end: date) -> List[SecFact]:
    """Facts whose period ends within the match tolerance of ``end``, the closest end
    first (two filings can tag ends a few days apart for the same quarter)."""
    hits = [f for f in facts if _near(f.end, end)]
    if not hits:
        return []
    closest = min(abs((f.end - end).days) for f in hits)
    return [f for f in hits if abs((f.end - end).days) == closest]


def _quarter_value(facts: Sequence[SecFact], end: date) -> Optional[float]:
    """The concept's value for the fiscal QUARTER ending at ``end``, or None when it
    cannot be isolated.

    In order: a 3-month fact; a year-to-date fact minus the one a quarter earlier with the
    same start (Q4 = full year − 9 months); a year-to-date fact of 0 (no payment in any
    quarter of it). A difference below zero is a restatement between the two filings, not
    a payment, and is refused rather than clamped.
    """
    ending = _ending_near(facts, end)
    if not ending:
        return None
    direct = [f for f in ending if _is_quarter(f.days)]
    if direct:
        return direct[0].val
    for ytd in sorted(ending, key=lambda f: f.days):
        prior = [
            f for f in facts
            if f.start == ytd.start and f.end < ytd.end and _is_quarter((ytd.end - f.end).days)
        ]
        if prior:
            earlier = max(prior, key=lambda f: f.end)
            if not _same_basis(ytd, earlier):
                return None
            quarter = ytd.val - earlier.val
            if quarter >= -_ABS_TOLERANCE:
                return max(quarter, 0.0)
            return None
    if any(f.val <= _ABS_TOLERANCE for f in ending):
        return 0.0
    return None


def _same_basis(a: SecFact, b: SecFact) -> bool:
    """False when exactly ONE of the two spans was restated and the other was never filed
    again after that restatement: its figure is still on the old basis (TGT: FY2021 re-filed
    2024-03-13 without withholding; its nine months only ever filed with it). Both restated
    (DELL re-presented its nine months, then its year) or neither: the same basis."""
    if (a.restated_on is None) == (b.restated_on is None):
        return True
    restated, other = (a, b) if a.restated_on is not None else (b, a)
    return other.filed >= restated.restated_on


def _values_ending(facts: Optional[Sequence[SecFact]], end: date) -> List[float]:
    """Every value the concept reports for a period ending at ``end``: the isolated
    quarter, when it can be derived, and each year-to-date span as filed."""
    if not facts:
        return []
    out = [f.val for f in _ending_near(facts, end)]
    quarter = _quarter_value(facts, end)
    if quarter is not None:
        out.append(quarter)
    return out


# ── The rule ───────────────────────────────────────────────────────────


def _tolerance(amount: float) -> float:
    return max(_ABS_TOLERANCE, _REL_TOLERANCE * abs(amount))


def _approx(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _fmt(dollars: float) -> str:
    return f"{dollars / 1e6:,.1f}M"


def _explainers(sec: SecBuybackFacts, end: date) -> List[Tuple[str, float]]:
    """(label, dollars) amounts that are NOT common buybacks but that FMP has been seen to
    file under ``commonStockRepurchased``: withholding, preferred redemptions, and every
    sum of one withholding and one preferred amount."""
    withholding = [("tax withholding", v) for v in _values_ending(sec.withholding, end) if v > 0]
    preferred = [
        ("preferred / noncontrolling-interest redemptions", v)
        for facts in sec.preferred.values()
        for v in _values_ending(facts, end)
        if v > 0
    ]
    combined = [
        ("tax withholding + preferred / noncontrolling-interest redemptions", w + p)
        for _, w in withholding
        for _, p in preferred
    ]
    return withholding + preferred + combined


def check_quarter_buyback(
    fmp_buyback: float, period_end: date, sec: SecBuybackFacts
) -> BuybackCheck:
    """The figure to chart for one quarter (see the module docstring for the rule)."""
    b = float(fmp_buyback)
    if not math.isfinite(b) or b <= 0:
        return BuybackCheck(dollars=b if math.isfinite(b) else 0.0, outcome="verified",
                            detail="no buyback to check")
    tol = _tolerance(b)
    repurchase = sec.repurchase or []
    r_quarter = _quarter_value(repurchase, period_end) if repurchase else None

    if r_quarter is not None:
        if _approx(b, r_quarter, tol):
            return BuybackCheck(b, "verified", f"matches the filing's {_fmt(r_quarter)}")
        if r_quarter > b:
            return BuybackCheck(
                b, "sec_higher",
                f"the filing reports {_fmt(r_quarter)}, above FMP's {_fmt(b)} — kept "
                f"(the check never raises a figure)",
            )
        excess = b - r_quarter
        if r_quarter <= tol:
            return BuybackCheck(
                0.0, "corrected",
                f"the filing reports no repurchase this quarter; FMP's {_fmt(b)} is "
                f"another line",
            )
        for label, amount in _explainers(sec, period_end):
            if _approx(excess, amount, _tolerance(max(excess, amount))):
                return BuybackCheck(
                    r_quarter, "corrected",
                    f"FMP's {_fmt(b)} = the filing's {_fmt(r_quarter)} repurchase + "
                    f"{_fmt(amount)} {label}",
                )
        return BuybackCheck(
            b, "mismatch",
            f"the filing reports {_fmt(r_quarter)}; FMP's {_fmt(b)} is {_fmt(excess)} "
            f"higher for no reason the filing shows — kept",
        )

    # The quarter's repurchase cannot be isolated. Only when no POSITIVE repurchase ends
    # here can FMP's figure be another line: a year-to-date buyback that started in an
    # untagged Q1 may still hold some of this quarter's buybacks.
    positive_repurchase_here = any(f.val > tol for f in _ending_near(repurchase, period_end))
    if not positive_repurchase_here:
        for label, amount in _explainers(sec, period_end):
            if _approx(b, amount, _tolerance(max(b, amount))):
                return BuybackCheck(
                    0.0, "corrected",
                    f"no repurchase in the filing; FMP's {_fmt(b)} is the filing's "
                    f"{label}",
                )
    return BuybackCheck(
        b, "unverified",
        "the filing has no repurchase figure to compare for this quarter — kept",
    )


def _row_buyback(row: Mapping[str, Any]) -> Optional[float]:
    """FMP's buyback for a row as positive dollars, read the way the Signal of Confidence
    gate reads it (a numeric string counts, a bool never does), or None when there is
    none to check."""
    raw = row.get("commonStockRepurchased")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value >= 0:
        return None
    return -value


def buyback_overrides(
    cashflow_rows: Iterable[Mapping[str, Any]], sec: SecBuybackFacts
) -> Tuple[Dict[str, float], List[Tuple[str, BuybackCheck]]]:
    """Check every FMP quarterly row that carries a buyback.

    Returns ``({row day: corrected dollars}, [(row day, check) for every checked row])``,
    keyed by the row's ``YYYY-MM-DD`` — the key the Signal of Confidence gate looks a row
    up by. Two rows for one day: the FIRST wins, as in `_build_quarters` (a restatement is
    listed first), so a correction derived from a discarded duplicate can never lower the
    row that is charted. Only CORRECTED rows enter the first map; the second carries every
    outcome so the caller can log the kept-but-suspect ones (``mismatch``, ``sec_higher``,
    ``unverified``).
    """
    overrides: Dict[str, float] = {}
    checks: List[Tuple[str, BuybackCheck]] = []
    seen: set = set()
    for row in cashflow_rows:
        if not isinstance(row, Mapping):
            continue
        end = _parse_day(row.get("date"))
        if end is None:
            continue
        key = end.isoformat()
        if key in seen:
            continue
        seen.add(key)
        b = _row_buyback(row)
        if b is None:
            continue
        check = check_quarter_buyback(b, end, sec)
        checks.append((key, check))
        if check.changed:
            overrides[key] = check.dollars
    return overrides, checks
