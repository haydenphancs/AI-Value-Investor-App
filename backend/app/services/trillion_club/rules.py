"""Pure rules for the Trillion-Dollar Club: membership, 13F calendar, identifiers, notices.

No I/O, no FMP, no Supabase — every function here is deterministic given its inputs, so
the jobs and the request path can share one definition and the tests can pin it exactly.

Membership
----------
A company is in the club after ``JOIN_CLOSES`` (10) consecutive DATED closes with a market
cap at or above ``THRESHOLD_USD`` ($1.00T), and leaves after ``LEAVE_CLOSES`` (20)
consecutive closes below. The buffer exists because names near the line flip back and
forth: MU and LLY each crossed $1T nine times since 2025, and AMD sat $2B above it on its
first three closes (2026-09-21..23). The owner can override with ``force_in`` /
``force_out`` (a hand-entered cap — Aramco, Samsung — is only ever used that way).

⚠️ Known property of the leave rule, pinned by a test rather than hidden: "20 STRAIGHT
closes below" resets on one close above, so a member that alternates 19 below / 1 above
stays a member while spending 95% of its closes under the line. That was the owner's
choice (2026-09-24); ``force_out`` is the remedy.

13F calendar
------------
Form 13F-HR is due 45 days after the calendar quarter ends; a due date on a weekend or a
FEDERAL holiday rolls to the next business day (SEC Rule 0-3). That is the federal
calendar, not the NYSE one in ``app.utils.market_hours``: Columbus Day and Veterans Day
close the SEC but not the exchange, and Good Friday closes the exchange but not the SEC.
The holidays are computed by rule (5 U.S.C. 6103), so the calendar never expires.

⚠️ The MEMBERSHIP rule's close cutoff is the other calendar: ``last_completed_close`` reads
the NYSE tables in ``app.utils.market_hours``, which are hand-kept and DO expire (past the
last listed year every NYSE holiday reads as a session that closed at 16:00).
``tests/test_trillion_club_rules.py`` fails once they cover less than 12 months ahead.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from app.utils.market_hours import ET, last_completed_close

logger = logging.getLogger(__name__)

# ── Membership ──────────────────────────────────────────────────────────────────────

THRESHOLD_USD = 1_000_000_000_000
JOIN_CLOSES = 10
LEAVE_CLOSES = 20
MIN_ROWS = 20
#: About one trading year. Longer histories are trimmed to their newest rows.
MAX_REPLAY_ROWS = 260
#: The newest usable close may be at most this many calendar days older than the last
#: completed session. An older series (a symbol FMP stopped updating, a delisting) must
#: not keep re-asserting membership as if it were today's answer.
MAX_CLOSE_STALENESS_DAYS = 7

MODE_AUTO = "auto"
MODE_FORCE_IN = "force_in"
MODE_FORCE_OUT = "force_out"
MEMBERSHIP_MODES = (MODE_AUTO, MODE_FORCE_IN, MODE_FORCE_OUT)


@dataclass(frozen=True)
class MembershipState:
    """What the daily job writes to ``trillion_club_companies`` for one company."""

    is_member: bool
    member_since: Optional[date]
    closes_at_or_above: int
    closes_below: int
    last_cap: Optional[float]
    last_cap_date: Optional[date]


NOT_A_MEMBER = MembershipState(False, None, 0, 0, None, None)


def _as_date(value: Any) -> Optional[date]:
    """A ``date`` from a date, datetime or ``YYYY-MM-DD…`` string; ``None`` otherwise."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_positive_finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):   # float(10**400) overflows: malformed
        return None
    return f if math.isfinite(f) and f > 0 else None


def _clean_closes(
    closes: Iterable[Any], cutoff: date, log_ctx: str
) -> Optional[List[Tuple[date, Optional[float]]]]:
    """Validated, de-duplicated, ascending ``(date, cap)`` rows dated on/before ``cutoff``.

    Two rows for the SAME date that disagree (beyond ``rel_tol=1e-9``) are resolved by
    what the disagreement can change — never by voiding the whole series, which froze a
    company's membership for as long as the bad date stayed in the ~13-month fetch window:

    * on the NEWEST date -> ``None`` (fail closed): that close decides today's answer and
      is the cap shown on the card;
    * elsewhere, both values on the same side of ``THRESHOLD_USD`` -> the rule only reads
      that side, so the conflict changes nothing; the later-seen value is kept;
    * elsewhere, straddling ``THRESHOLD_USD`` -> the close is unknown to the rule: the row
      is kept with ``cap=None``, which :func:`_replay` treats as a streak BREAKER. That can
      only delay a join or a leave, never cause one.
    """
    by_date: dict = {}
    conflicts: dict = {}                  # date -> every value seen for it
    malformed = 0
    future = 0
    for item in closes or ():
        try:
            raw_date, raw_cap = item
        except (TypeError, ValueError):
            malformed += 1
            continue
        d = _as_date(raw_date)
        cap = _as_positive_finite(raw_cap)
        if d is None or cap is None:
            malformed += 1
            continue
        if d > cutoff:
            future += 1
            continue
        seen = by_date.get(d)
        if seen is not None and (d in conflicts or not math.isclose(seen, cap, rel_tol=1e-9)):
            conflicts.setdefault(d, [seen]).append(cap)
        by_date[d] = cap
    if conflicts:
        newest = max(by_date)
        for d in sorted(conflicts):
            values = conflicts[d]
            if d == newest:
                logger.warning(
                    "trillion club membership (%s): two different caps for the NEWEST close "
                    "%s (%s) — today's close is unknown; failing closed", log_ctx, d,
                    ", ".join(repr(v) for v in values),
                )
                return None
            straddles = min(values) < THRESHOLD_USD <= max(values)
            logger.warning(
                "trillion club membership (%s): two different caps for %s (%s) — %s", log_ctx,
                d, ", ".join(repr(v) for v in values),
                "they straddle $1T, so that close is unknown and breaks any streak across it"
                if straddles else "both on the same side of $1T, so the rule's answer is unaffected",
            )
            if straddles:
                by_date[d] = None
    if malformed:
        logger.warning(
            "trillion club membership (%s): dropped %d malformed close row(s) "
            "(bad date, or a cap that is missing / non-finite / <= 0)", log_ctx, malformed,
        )
    if future:
        logger.info(
            "trillion club membership (%s): dropped %d row(s) dated after the last "
            "completed close %s (an intraday figure is not a close)", log_ctx, future, cutoff,
        )
    return sorted(by_date.items())


def _replay(
    rows: Sequence[Tuple[date, Optional[float]]]
) -> Tuple[bool, Optional[date], int, int, int]:
    """Replay the join/leave rule from "not a member" over ascending ``rows``.

    Returns ``(is_member, member_since, at_or_above, below, longest_below)``.
    ``longest_below`` is the longest run of consecutive closes below the line anywhere in
    the window: shorter than ``LEAVE_CLOSES`` means the window shows no exit at all. A row
    whose cap is ``None`` (a close known only to straddle the line) breaks every streak.
    """
    is_member = False
    member_since: Optional[date] = None
    above = below = longest_below = 0
    for d, cap in rows:
        if cap is None:
            above = below = 0
            continue
        if cap >= THRESHOLD_USD:
            above += 1
            below = 0
        else:
            below += 1
            above = 0
            longest_below = max(longest_below, below)
        if not is_member and above >= JOIN_CLOSES:
            is_member, member_since = True, d
        elif is_member and below >= LEAVE_CLOSES:
            is_member, member_since = False, None
    return is_member, member_since, above, below, longest_below


def evaluate_membership(
    closes: Sequence[Tuple[Any, Any]],
    *,
    mode: str,
    prior: Optional[MembershipState],
    today_et: date,
    now_et: datetime,
    log_ctx: str = "",
) -> Optional[MembershipState]:
    """Club membership from up to ``MAX_REPLAY_ROWS`` dated market-cap closes.

    ``closes`` are ``(date, market_cap_usd)`` pairs in any order (FMP's
    ``historical-market-capitalization`` is newest first). A row dated after the last
    COMPLETED session close is dropped — FMP stamps an intraday figure with today's date,
    and one cap source must serve both the card and the rule.

    Returns ``None`` to FAIL CLOSED (the caller keeps the stored state) when, in ``auto``
    mode, there are fewer than ``MIN_ROWS`` usable rows, the newest one is stale, or the
    newest date carries two different caps. An older conflicting date does not void the
    series (see :func:`_clean_closes`). ``force_in`` / ``force_out`` always decide
    ``is_member``; the counters and last cap then come from the data when it is usable,
    else from ``prior``. An unknown ``mode`` also returns ``None`` (logged at ERROR).

    ``member_since`` is the date of the close that completed the joining streak. The
    window is only ~13 months, so the stored state carries what it cannot see: when the
    stored state says member since an OLDER date and the window holds no run of
    ``LEAVE_CLOSES`` closes below the line (so no exit happened inside it), the stored date
    is kept. Otherwise a long-standing member whose window happened to open in a short dip
    would have its date jump forward the day the window slid past the dip's leading edge.
    ``prior`` only ever changes ``member_since``, never who is a member or the counters.
    """
    if mode not in MEMBERSHIP_MODES:
        logger.error(
            "trillion club membership (%s): unknown membership_mode %r — failing closed",
            log_ctx, mode,
        )
        return None
    if prior is not None and not isinstance(prior, MembershipState):
        raise TypeError(
            f"evaluate_membership: prior must be a MembershipState or None, got "
            f"{type(prior).__name__} ({log_ctx})"
        )
    prior = prior or NOT_A_MEMBER
    last_close = last_completed_close(now_et).astimezone(ET).date()
    today = _as_date(today_et) or last_close          # a datetime is read as its date
    cutoff = min(last_close, today)

    rows = _clean_closes(closes, cutoff, log_ctx)
    # ``usable`` may hold a straddling-conflict row with cap None — never the newest one
    # (a conflict there returns None above), so usable[-1] always carries a real cap.
    usable: List[Tuple[date, Optional[float]]] = rows or []
    if usable and (cutoff - usable[-1][0]).days > MAX_CLOSE_STALENESS_DAYS:
        logger.warning(
            "trillion club membership (%s): newest close is %s, more than %d days before "
            "the last session (%s) — treating the series as stale", log_ctx,
            usable[-1][0], MAX_CLOSE_STALENESS_DAYS, cutoff,
        )
        usable = []
    usable = usable[-MAX_REPLAY_ROWS:]
    known = sum(1 for _d, cap in usable if cap is not None)

    replay = _replay(usable) if usable else None
    last_cap = usable[-1][1] if usable else None
    last_cap_date = usable[-1][0] if usable else None

    if mode == MODE_AUTO:
        if replay is None or known < MIN_ROWS:
            logger.warning(
                "trillion club membership (%s): %d usable close(s) < %d — failing closed, "
                "keeping the stored state", log_ctx, known, MIN_ROWS,
            )
            return None
        is_member, since, above, below, longest_below = replay
        if (is_member and prior.is_member and prior.member_since is not None
                and since is not None and prior.member_since <= since
                and longest_below < LEAVE_CLOSES):
            since = prior.member_since
        return MembershipState(is_member, since, above, below, last_cap, last_cap_date)

    # Owner override: the decision is the owner's; the data only fills the facts.
    if replay is not None:
        replay_member, replay_since, above, below, _longest_below = replay
    else:
        replay_member, replay_since = False, None
        above, below = prior.closes_at_or_above, prior.closes_below
        last_cap, last_cap_date = prior.last_cap, prior.last_cap_date
    if mode == MODE_FORCE_OUT:
        return MembershipState(False, None, above, below, last_cap, last_cap_date)
    if prior.is_member and prior.member_since is not None:
        since = prior.member_since
    elif replay_member and replay_since is not None:
        since = replay_since
    else:
        since = today
    return MembershipState(True, since, above, below, last_cap, last_cap_date)


# ── Quarters ─────────────────────────────────────────────────────────────────────────

_PERIOD_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_QUARTER_END_MD = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _check_quarter(year: int, quarter: int) -> None:
    if isinstance(year, bool) or isinstance(quarter, bool) or not isinstance(year, int) \
            or not isinstance(quarter, int) or quarter not in _QUARTER_END_MD \
            or not 1900 <= year <= 9998:
        raise ValueError(f"not a calendar quarter: year={year!r} quarter={quarter!r}")


def period_label(year: int, quarter: int) -> str:
    """``(2026, 2)`` -> ``"2026-Q2"`` (the ``trillion_club_filings.period`` format)."""
    _check_quarter(year, quarter)
    return f"{year}-Q{quarter}"


def parse_period(label: str) -> Tuple[int, int]:
    """``"2026-Q2"`` -> ``(2026, 2)``. Raises ``ValueError`` on anything else."""
    m = _PERIOD_RE.match(str(label or "").strip()) if isinstance(label, str) else None
    if not m:
        raise ValueError(f"not a 13F period label: {label!r}")
    return int(m.group(1)), int(m.group(2))


def quarter_end(year: int, quarter: int) -> date:
    """The calendar quarter's last day — the 13F's "holdings as of" date."""
    _check_quarter(year, quarter)
    month, day = _QUARTER_END_MD[quarter]
    return date(year, month, day)


def previous_quarter(year: int, quarter: int) -> Tuple[int, int]:
    _check_quarter(year, quarter)
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def next_quarter(year: int, quarter: int) -> Tuple[int, int]:
    _check_quarter(year, quarter)
    return (year + 1, 1) if quarter == 4 else (year, quarter + 1)


def quarter_of(d: date) -> Tuple[int, int]:
    """The calendar quarter containing ``d``."""
    return d.year, (d.month - 1) // 3 + 1


# ── Federal business days (the SEC's calendar) ──────────────────────────────────────


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """5 U.S.C. 6103(b): Saturday -> the Friday before, Sunday -> the Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _actual_holidays(year: int) -> List[date]:
    days = [
        date(year, 1, 1),                  # New Year's Day
        _nth_weekday(year, 1, 0, 3),       # Birthday of Martin Luther King, Jr.
        _nth_weekday(year, 2, 0, 3),       # Washington's Birthday
        _last_weekday(year, 5, 0),         # Memorial Day
        date(year, 7, 4),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),       # Labor Day
        _nth_weekday(year, 10, 0, 2),      # Columbus Day
        date(year, 11, 11),                # Veterans Day
        _nth_weekday(year, 11, 3, 4),      # Thanksgiving Day
        date(year, 12, 25),                # Christmas Day
    ]
    if year >= 2021:
        days.append(date(year, 6, 19))     # Juneteenth National Independence Day
    return days


def federal_holidays(year: int) -> Set[date]:
    """OBSERVED federal holidays falling in ``year``.

    Includes the following year's New Year's Day when it falls on a Saturday and is
    therefore observed on December 31 of ``year`` (e.g. 2021-12-31 for 2022-01-01).
    """
    observed = {_observed(d) for d in _actual_holidays(year)}
    observed.add(_observed(date(year + 1, 1, 1)))
    return {d for d in observed if d.year == year}


def is_federal_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in federal_holidays(d.year)


def next_federal_business_day(d: date) -> date:
    """``d`` itself when it is a business day, else the next one."""
    probe = d
    for _ in range(15):          # the longest weekend + holiday run is 4 days
        if is_federal_business_day(probe):
            return probe
        probe += timedelta(days=1)
    raise RuntimeError(f"no federal business day within 15 days of {d}")  # unreachable


def add_federal_business_days(d: date, n: int) -> date:
    """The ``n``-th federal business day strictly after ``d`` (``n >= 0``)."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    probe = d
    for _ in range(n):
        probe = next_federal_business_day(probe + timedelta(days=1))
    return probe


# ── 13F due dates and card notices ────────────────────────────────────────────────────

FILING_DEADLINE_DAYS = 45
#: Business days after the legal due date before a missing filing is called missing
#: (filers land on different days, and FMP ingests after SEC acceptance).
NOTICE_GRACE_BUSINESS_DAYS = 5


def sec_13f_due_date(year: int, quarter: int) -> date:
    """The legal due date of the 13F-HR for ``(year, quarter)``.

    Quarter end + 45 days, rolled forward past weekends and FEDERAL holidays:
    2026-Q2 -> 2026-08-14 (Fri), 2026-Q3 -> 2026-11-16 (Nov 14 is a Saturday),
    2026-Q4 -> 2027-02-16 (Feb 14 is a Sunday and Feb 15 is Washington's Birthday),
    2027-Q1 -> 2027-05-17 (May 15 is a Saturday).
    """
    return next_federal_business_day(
        quarter_end(year, quarter) + timedelta(days=FILING_DEADLINE_DAYS)
    )


def next_due_date(latest_period: str) -> date:
    """The due date of the quarter AFTER ``latest_period`` — the next filing expected."""
    return sec_13f_due_date(*next_quarter(*parse_period(latest_period)))


def _grace_end(year: int, quarter: int) -> date:
    return add_federal_business_days(sec_13f_due_date(year, quarter), NOTICE_GRACE_BUSINESS_DAYS)


def latest_not_in_due(latest_period: str, today: date) -> bool:
    """True once the NEXT quarter's filing is overdue by more than the grace period.

    ``latest_period`` is the newest quarter on file. With "2026-Q2" on file the Q3 filing
    is due 2026-11-16; this turns True on 2026-11-24, the day after the 5th federal
    business day past the due date ("We haven't received the Q3 2026 filing yet").
    Raises ``ValueError`` on a malformed period.
    """
    y, q = next_quarter(*parse_period(latest_period))
    return today > _grace_end(y, q)


def no_newer_filing(latest_period: str, today: date) -> bool:
    """True once TWO due dates (each plus the grace period) have passed with nothing newer.

    Worded "No newer 13F found" on the card, never "stopped filing": FMP alone cannot
    prove a company stopped. Raises ``ValueError`` on a malformed period.
    """
    y, q = next_quarter(*next_quarter(*parse_period(latest_period)))
    return today > _grace_end(y, q)


# ── Identifiers ──────────────────────────────────────────────────────────────────────

_CUSIP_RE = re.compile(r"^[0-9A-Z]{9}$")
_ACCESSION_RE = re.compile(r"(?<!\d)(\d{10}-\d{2}-\d{6})(?!\d)")
_ACCESSION_FOLDER_RE = re.compile(r"/(\d{18})(?:/|$)")


def _char_value(c: str) -> int:
    return int(c) if c.isdigit() else ord(c) - ord("A") + 10


def cusip_check_digit(first8: str) -> int:
    """The CUSIP's own (9th-character) check digit for its first 8 characters."""
    total = 0
    for i, c in enumerate(first8):
        v = _char_value(c)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def normalize_cusip(value: Any) -> Optional[str]:
    """Upper-cased 9-character CUSIP / CINS, or ``None`` when malformed."""
    if not isinstance(value, str):
        return None
    c = value.strip().upper()
    return c if _CUSIP_RE.match(c) else None


def isin_check_digit(payload: str) -> int:
    """ISIN check digit (letters expanded to two digits, then Luhn) for an 11-char payload."""
    digits = "".join(str(_char_value(c)) for c in payload)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 0:           # the rightmost payload digit sits next to the check digit
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def cusip_to_us_isin(cusip: Any) -> Optional[str]:
    """``"29765A101"`` -> ``"US29765A1016"``; ``None`` for a CINS or malformed input.

    Only a CUSIP whose first character is a DIGIT is a U.S./Canadian issue that maps to a
    ``US`` ISIN. A CINS number (first character a letter — NVIDIA's Nebius stake is
    ``N97284108``, real ISIN ``NL0009805522``) never does: deriving ``USN972841084`` finds
    nothing. A CUSIP whose own check digit is wrong is malformed and also returns ``None``.

    ⚠️ A digit-first CUSIP is not always a ``US`` ISIN: a Canadian issuer's is ``CA…``
    (Xanadu, ``98390R102`` -> ``CA98390R1029``). The derived ``US`` form then simply finds
    nothing, which is why the builder falls back to ``search-cusip``.
    """
    c = normalize_cusip(cusip)
    if c is None or not c[0].isdigit():
        return None
    if not c[8].isdigit() or cusip_check_digit(c[:8]) != int(c[8]):
        return None
    payload = "US" + c
    return payload + str(isin_check_digit(payload))


def accession_from_link(link: Any) -> Optional[str]:
    """SEC accession number (``0001045810-26-000065``) from an EDGAR filing link.

    Reads the dashed form first (the ``-index.htm`` file name), then the 18-digit folder
    (``/000104581026000065/``, as in ``finalLink``). ``None`` when neither is present.
    """
    if not isinstance(link, str) or not link:
        return None
    m = _ACCESSION_RE.search(link)
    if m:
        return m.group(1)
    m = _ACCESSION_FOLDER_RE.search(link)
    if m:
        s = m.group(1)
        return f"{s[:10]}-{s[10:12]}-{s[12:]}"
    return None


__all__ = [
    "THRESHOLD_USD", "JOIN_CLOSES", "LEAVE_CLOSES", "MIN_ROWS", "MAX_REPLAY_ROWS",
    "MAX_CLOSE_STALENESS_DAYS", "MODE_AUTO", "MODE_FORCE_IN", "MODE_FORCE_OUT",
    "MEMBERSHIP_MODES", "MembershipState", "NOT_A_MEMBER", "evaluate_membership",
    "period_label", "parse_period", "quarter_end", "previous_quarter", "next_quarter",
    "quarter_of", "federal_holidays", "is_federal_business_day",
    "next_federal_business_day", "add_federal_business_days", "FILING_DEADLINE_DAYS",
    "NOTICE_GRACE_BUSINESS_DAYS", "sec_13f_due_date", "next_due_date", "latest_not_in_due",
    "no_newer_filing", "cusip_check_digit", "normalize_cusip", "isin_check_digit",
    "cusip_to_us_isin", "accession_from_link",
]
