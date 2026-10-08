"""What each insider HOLDS after their latest reported Form 4 transactions — pure, no I/O.

WHY THIS EXISTS (TestFlight 1.0 (11), 2026-10-05). In an Updates chat on CRWV, right after
a director's Form 4 sale, "how many shares does he own now?" got "Caydex does not have
information on Director Brian Venturo's current total ownership". It did have it: every
Form 4 line carries ``securitiesOwned`` — the filing's "Amount of Securities Beneficially
Owned Following Reported Transaction(s)" column — and the Holders build fetches those rows
on every build. It summed the trades and dropped the balances.

Three things make "the balance after the latest transaction" harder than "the last row",
all measured on the live CRWV / NVDA feeds (2026-10-05):

  * A filing's lines are NOT listed in the order they happened. Venturo's 2026-09-30
    filing lists +17,391 → 368,142, then +109,380 → 350,751, then −65,616 → 302,526: the
    vest that came FIRST is listed second. "The first row of the day" or "the last row" is
    a coin flip; the end-of-day balance is found by CHAINING each line's balance before it
    (balance after − change) onto another line's balance after (`_chain`).
  * Indirect holdings are several holdings. A row says direct or indirect but not WHICH
    trust, family member or entity, and one CRWV director holds Class B through at least
    four of them (4,990,542 and 2,871,000 after two same-day conversions, plus a 1,578,349
    gift between two others). Each indirect chain's last balance is reported on its own,
    with its own date — never summed into one number nothing vouches for. And a chain can
    BREAK where the feed is missing a row: NVDA's CEO's main trust reads 581,378,470 on
    2025-09-15 and 521,378,470 before its next row, a 60,000,000-share move no fetched row
    records — so a balance last reported before the line's newest indirect transaction is
    marked ``reported_earlier`` (a separate holding OR an older state of a listed one).
  * Rows carry gaps: Form 4 lines with no code, no ownership form and 0 shares (Magnetar,
    2026-08-13; NVDA's empty Form 3s) would read "holds 0"; NVDA's 2024 sales arrive with
    no direct/indirect flag at all.

Rules that keep it honest:
  * A balance is the reported number or nothing. Missing, null, negative, non-finite,
    boolean or non-numeric ``securitiesOwned`` is UNKNOWN (`holding_value` → None), never 0;
    an acquisition whose reported balance is smaller than the shares it acquired is a unit
    error and unknown too (the CEO-buys card's rule, `signals_service`).
  * A row that cannot be placed (no direct/indirect flag) never becomes a balance, but a
    LATER such row, or a later row on the line with no usable balance, marks the balances
    it may have changed (`changed_after`).
  * A same-day group whose end-of-day balance the rows cannot decide stays ambiguous
    (`possible_shares`) until a later row's balance-before resolves it.

The input is the Holders build's rows AFTER `_insider_common.prepare_insider_rows` (this
issuer only, equity lines only, Form 4/A supersession). Options, RSUs and other derivative
lines are therefore not here — and are not "shares owned".
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services._insider_common import (
    _filing_date,
    _finite_number,
    _form_type,
    _share_class_token,
    clean_role_title,
    insider_reporter_key,
    is_equity_line,
    normalize_insider_name,
)

# Shares. A chain links when one line's balance-before equals another's balance-after
# within half a share (FMP balances are whole shares; fractional DRIP lots round).
_TOLERANCE = 0.5
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A numeric STRING balance is accepted only as plain digits (an optional decimal part): "1e9",
# "-5", "1,234" and "NaN" are not balances a filing reports.
_PLAIN_NUMBER_RE = re.compile(r"^\d{1,15}(?:\.\d{1,6})?$")
_LABEL_MAX = 60

#: People kept per issuer (most recent filers first), indirect balances kept per line, the
#: trades summarised for the newest day, and the balances an ambiguous day may have ended on.
MAX_PEOPLE = 25
MAX_INDIRECT_BALANCES = 5
MAX_TRADES = 4
MAX_CANDIDATES = 8
#: A filer this much staler than the newest one has most likely left (see the public function).
INACTIVE_AFTER_DAYS = 730
#: People left out (by the cap or as stale) are NAMED, up to this many per list.
MAX_NAMED = 40
# A name that is an entity, not a person: `normalize_insider_name` reorders "LAST FIRST", which
# turns "Magnetar Financial LLC" into "Financial Llc Magnetar". (Not "co": a surname token.)
_ENTITY_RE = re.compile(
    r"\b(?:llc|l\.l\.c|lp|l\.p|llp|inc|corp|corporation|company|fund|funds|trust|partners"
    r"|partnership|holdings|capital|management|ltd|limited|group|advisors|advisers|investments"
    r"|associates|foundation|bank|plc|gmbh|n\.v|s\.a)\b",
    re.I,
)


def holding_value(value: Any) -> Optional[float]:
    """A reported share balance, or None when the field does not carry one — NEVER a coerced 0.

    None, booleans, negatives, NaN/±inf, and strings that are not plain digits are unknown.
    A real 0 (the person sold everything on that line) is kept: it is a reported number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not _PLAIN_NUMBER_RE.match(text):
            return None
        number = float(text)
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _shares_transacted(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return abs(number) if number is not None else None


def _positive_price(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


@dataclass
class _Row:
    order: int                 # position in the feed (FMP: newest filing first)
    reporter: str
    name: Any
    title: Any
    share_class: str           # "class-a" for "Class A Common Stock", "" for an unclassed line
    held: Optional[str]        # "direct" | "indirect" | None (the row did not say)
    security: str
    date: str                  # trade date, else filing date (YYYY-MM-DD)
    filed: str                 # filing date, "" when absent
    after: Optional[float]     # the reported balance after this line; None = unknown
    change: Optional[float]    # signed: + acquired, − disposed; None = unknown
    shares: Optional[float]
    price: Optional[float]
    code: str                  # FMP transactionType ("S-Sale"), "" when absent
    acquired: Optional[bool]
    # A separately reported LOT: a Form 4 line with no transaction (no code, no shares) that
    # only reports a balance, listed beside the lines that trade. (A Form 3 / Form 5 holding
    # line is not one — it states where the traded position stood; see `_parse`.)
    statement: bool = False

    @property
    def before(self) -> Optional[float]:
        if self.after is None or self.change is None:
            return None
        return self.after - self.change


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_day(text: Any) -> bool:
    """A real calendar date in ``YYYY-MM-DD`` form ("2026-02-30" matches the shape only)."""
    if not isinstance(text, str) or not _DATE_RE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _filed_day(row: Dict[str, Any], latest: str = "9999-12-31") -> str:
    """The filing date, or "" when it is absent, not a calendar date, or after `latest`."""
    filed = _filing_date(row)
    return filed if _is_day(filed) and filed <= latest else ""


# "Class A" and "Series A" both name a SECURITY (Liberty-style trackers file "Series A/C
# Common Stock"): two of them are two holdings. The holding line's key is the SAME token
# Form 4/A supersession groups on (`_insider_common._share_class_token`, "class-a" /
# "series-c"), so a line is one security in both places.
_line_class_token = _share_class_token


def _parse(order: int, row: Any, latest: str = "9999-12-31") -> Optional[_Row]:
    """One feed row as a `_Row`, or None when it carries nothing a holding can be read from.

    `latest` (``YYYY-MM-DD``) is the last date a row may carry: today, plus a day of slack."""
    if not isinstance(row, dict) or not is_equity_line(row.get("securityName")):
        # Options, RSUs, warrants: not shares owned. The Holders rows are already filtered
        # (`prepare_insider_rows`); this keeps a raw feed from ever reading as holdings.
        return None
    reporter = insider_reporter_key(row)
    # The trade date, else the filing date — `insider_row_date`'s order, except that a
    # malformed trade date ("2026-02-30") falls through to the filing date instead of
    # dropping the row. A Form 4 cannot report a trade AFTER its own filing (or after today):
    # such a date is a typo (2062 for 2026), and as the person's "newest" transaction it
    # would make every real filer read as two years stale. The filing date stands in for
    # it; with no usable filing date the row is not a balance at all.
    filed = _filed_day(row, latest)
    traded = row.get("transactionDate")
    traded = traded.strip()[:10] if isinstance(traded, str) else ""
    if _is_day(traded) and (traded > latest or (filed and traded > filed)):
        traded = ""
    day = traded if _is_day(traded) else filed
    if not reporter or not day:
        return None
    code = _text(row.get("transactionType"))
    shares = _shares_transacted(row.get("securitiesTransacted"))
    held = {"D": "direct", "I": "indirect"}.get(_text(row.get("directOrIndirect")).upper())
    security = _text(row.get("securityName"))[:_LABEL_MAX]
    if not code and not shares and held is None and not security:
        # An empty record: Form 4s whose only lines are derivatives, and "no securities owned"
        # Form 3s, arrive as one blank row with 0 shares. Its 0 is not a balance anyone filed.
        return None
    ad = _text(row.get("acquisitionOrDisposition")).upper()
    acquired = True if ad == "A" else False if ad == "D" else None
    after = holding_value(row.get("securitiesOwned"))
    if (acquired and after is not None and shares is not None
            and shares > after * 1.0001 + _TOLERANCE):
        # A holding smaller than the purchase that produced it is a unit error (the holding
        # typed into the shares column, or the reverse) — not a balance to repeat.
        after = None
    if not code and not shares:
        # A holdings line with no transaction ("Common Stock … I … By Trust" restated on a
        # Form 4, a Form 3 or Form 5 holding): the balance did not move on it.
        change: Optional[float] = 0.0
    elif shares is None or acquired is None:
        change = None
    else:
        change = shares if acquired else -shares
    # A separately reported LOT is a no-transaction line on a Form 4 (or 4/A) — listed beside
    # the lines that trade. A Form 3 (or Form 5) holding line states where a position STOOD
    # (AAPL's Borders: Form 3 39,130, his later chain starting from 39,162): it is a state of
    # the traded holding, not a second one.
    lot = not code and not shares and _form_type(row).startswith("4")
    return _Row(
        order=order, reporter=reporter, name=row.get("reportingName"),
        title=row.get("typeOfOwner"), share_class=_line_class_token(row.get("securityName")),
        held=held, security=security, date=day, filed=filed, after=after,
        change=change, shares=shares, price=_positive_price(row.get("price")), code=code,
        acquired=acquired, statement=lot,
    )


def _whole(value: float) -> int:
    """The nearest whole share — the key two balances are compared on. FMP balances are
    whole shares; a fractional (DRIP) chain still agrees with itself after rounding, since a
    balance-before is computed from the very numbers the previous line reported."""
    return int(math.floor(value + 0.5))


@dataclass
class _Tail:
    """The open end of one holding's chain: its latest known balance (or, when a same-day
    group could not be ordered, the balances it may have ended the day on)."""

    balance: Optional[float]
    date: str
    filed: str
    candidates: Tuple[float, ...] = ()
    # Opened by a holdings STATEMENT and never moved by a transaction since.
    statement: bool = False
    # Set when the chain OPENS and never cleared (review F1, 2026-10-07):
    #   lot — opened by a separately reported direct lot (`_Row.statement`). It stays a lot
    #     after it trades: listed beside the person's main holding, never competing with it.
    #     (It used to become an ordinary chain on its first trade — and, as the newest one,
    #     'the traded holding', so the main 5,000 shares vanished.)
    #   from_zero — opened by a line whose balance-before is 0: a position opened then, which
    #     continues no older chain.
    lot: bool = False
    from_zero: bool = False
    opened: str = ""                 # the date the chain opened
    # Lines on its last day that reported exactly this balance and were collapsed into it —
    # identical holdings lines (two trusts restated at 25,000, or one restated twice): 1
    # normally (review F2).
    lines: int = 1

    @property
    def anchored(self) -> bool:
        """Its history is known: it existed before its last day, or opened from 0 that day.
        An unanchored tail opened on its last day from a balance no row explains."""
        return self.opened < self.date or self.from_zero

    def keys(self) -> Tuple[int, ...]:
        if self.balance is not None:
            return (_whole(self.balance),)
        return tuple(_whole(c) for c in self.candidates)

    def move_to(self, row: _Row) -> None:
        self.balance, self.candidates = row.after, ()
        self.date, self.filed = row.date, row.filed
        self.lines = 1
        if not row.statement:
            self.statement = False


def _opened(row: _Row) -> _Tail:
    before = row.before
    return _Tail(
        row.after, row.date, row.filed, statement=row.statement, lot=row.statement,
        from_zero=not row.statement and before is not None and _whole(before) == 0,
        opened=row.date,
    )


class _Day:
    """One day's rows of one line, findable by balance in O(1).

    The first version rescanned every pending row (and every tail) for every row it
    placed: fine on real feeds (a five-page CRWV fetch in ~0.2 s) but cubic on adversarial
    input — 2,500 independent holdings took ~61 s, inside the async Holders build. Rows are
    now indexed by whole-share balance-before (a feed-ordered queue per balance, emptied
    lazily) and balance-after (a live count per balance), so linking a row is a lookup.
    """

    def __init__(self, rows: List[_Row]):
        self.rows = rows                              # feed order
        self.alive = [True] * len(rows)
        self.left = len(rows)
        self.by_before: Dict[int, deque] = {}
        self.after_alive: Dict[int, int] = {}
        for i, row in enumerate(rows):
            before = row.before
            if before is not None:
                self.by_before.setdefault(_whole(before), deque()).append(i)
            key = _whole(row.after)  # type: ignore[arg-type]
            self.after_alive[key] = self.after_alive.get(key, 0) + 1

    def take(self, i: int) -> _Row:
        self.alive[i] = False
        self.left -= 1
        self.after_alive[_whole(self.rows[i].after)] -= 1  # type: ignore[arg-type]
        return self.rows[i]

    def continuing(self, key: int) -> Optional[int]:
        """The first live row (feed order) whose balance-before is `key`."""
        queue = self.by_before.get(key)
        while queue and not self.alive[queue[0]]:
            queue.popleft()
        return queue[0] if queue else None

    def follow(self, key: int) -> Tuple[Optional[_Row], set]:
        """Where a chain starting at balance `key` would end today, WITHOUT taking a row:
        ``(the last row it would take — None if no live row continues it, the rows taken)``."""
        used: set = set()
        last: Optional[_Row] = None
        while True:
            nxt = next((i for i in self.by_before.get(key, ())
                        if self.alive[i] and i not in used), None)
            if nxt is None:
                return last, used
            used.add(nxt)
            last = self.rows[nxt]
            key = _whole(last.after)  # type: ignore[arg-type]

    def is_head(self, i: int) -> bool:
        """Its balance-before is unknown, or no OTHER live row ends on it (the holding's
        earlier history is outside the rows, or it starts from 0)."""
        row = self.rows[i]
        before = row.before
        if before is None:
            return True
        key = _whole(before)
        others = self.after_alive.get(key, 0) - (1 if _whole(row.after) == key else 0)  # type: ignore[arg-type]
        return others <= 0

    def live(self) -> List[int]:
        return [i for i, alive in enumerate(self.alive) if alive]


def _extend(day: _Day, tail: _Tail) -> None:
    """Follow `tail` through every live row that continues it.

    An AMBIGUOUS tail (the day it was last seen could not be ordered) resolves only when
    the rows say which balance it held: when exactly one of its candidates continues today,
    or when every candidate that continues ends the day on the same balance. When several
    continue to DIFFERENT ends — the same exercise-and-sell cycle repeated a month later —
    it stays ambiguous over those ends. It used to take whichever candidate's line the feed
    listed first, so one 10b5-1 month read 60,000 or 50,000 as a definite figure depending
    on the feed's order, which the rows cannot decide."""
    if tail.balance is None:
        paths = []
        for key in tail.keys():
            last, used = day.follow(key)
            if last is not None:
                paths.append((last, used))
        if not paths:
            return
        ends = {_whole(last.after) for last, _ in paths}  # type: ignore[arg-type]
        if len(paths) > 1 and len(ends) > 1:
            taken = set().union(*(used for _, used in paths))
            rows = [day.take(i) for i in sorted(taken)]
            tail.candidates = _cap(sorted({round(last.after, 4) for last, _ in paths}))  # type: ignore[arg-type]
            tail.date = max(r.date for r in rows)
            tail.filed = max(r.filed for r in rows)
            tail.lines = 1
            if not all(r.statement for r in rows):
                tail.statement = False
            return
        # One path — or several that agree: take the first path's rows in order.
        _, used = paths[0]
        key = next(k for k in tail.keys() if day.continuing(k) in used)
        i = day.continuing(key)
        while i is not None:
            row = day.take(i)
            tail.move_to(row)
            i = day.continuing(_whole(row.after))  # type: ignore[arg-type]
        return
    while True:
        i = day.continuing(_whole(tail.balance))
        if i is None:
            return
        tail.move_to(day.take(i))


def _cycle_breaker(pending: List[_Row]) -> Optional[_Row]:
    """When every pending row's balance-before is another's balance-after, the row the day's
    chains most plausibly start from — or None (genuinely ambiguous).

      * a TRANSFER pair — one line disposes of every share it held (after 0) and another
        acquires the same number from nothing (before 0) under the same code, CRWV's
        2026-08-13 gift between two of a director's trusts: two holdings, so the disposing
        line starts a chain with history outside these rows;
      * else a line OPENED from nothing (before 0) — CRWV's 2026-07-01 trust converted Class
        B into 61,532 and 15,380 Class A shares and sold them down to 80 and 0: a position
        opened that day, never the continuation of one that closed at 0 the same day.
    """
    zero = [r for r in pending if r.before is not None and _whole(r.before) == 0]
    receivers = {
        (r.code, _whole(r.shares)) for r in zero
        if r.acquired is True and r.code and r.shares is not None
    }
    for out in pending:
        if (out.acquired is False and out.code and out.after is not None
                and _whole(out.after) == 0 and out.shares is not None
                and (out.code, _whole(out.shares)) in receivers):
            return out
    return zero[0] if zero else None


def _chain(rows: Iterable[_Row]) -> List[_Tail]:
    """Every holding's chain on one line (one person, one share class, direct or indirect),
    walked day by day, and the open tail each one ends on.

    Within a day the rows are linked by balance, not by feed order: a row continues an open
    tail when its balance-before equals that tail's balance (the most recent resolved tail
    first); otherwise a row nothing else on the day ends on starts a new chain. When every
    remaining row's balance-before is another remaining row's balance-after,
    `_cycle_breaker` picks the start a filing most plausibly means; when it cannot (a day
    that nets to zero for a holding with no earlier balance here), the rows cannot say which
    balance ended the day — the tail is AMBIGUOUS and carries them all until a later row's
    balance-before names one."""
    by_date: Dict[str, List[_Row]] = {}
    for row in rows:
        if row.after is not None:
            by_date.setdefault(row.date, []).append(row)
    tails: List[_Tail] = []
    for date_key in sorted(by_date):
        day = _Day(sorted(by_date[date_key], key=lambda r: r.order))
        # 1) Earlier holdings move first: most recent resolved tail, then the ambiguous ones.
        for tail in sorted(sorted(tails, key=lambda t: t.date, reverse=True),
                           key=lambda t: t.balance is None):
            _extend(day, tail)
        # 2) New chains, in feed order, each followed as far as it goes. A pass can leave a
        #    row that a later chain's growth turned into a head, so repeat while it progresses.
        while day.left:
            progressed = False
            for i in day.live():
                if day.alive[i] and day.is_head(i):
                    tail = _opened(day.take(i))
                    tails.append(tail)
                    _extend(day, tail)
                    progressed = True
            if progressed:
                continue
            # 3) Only cycles are left.
            pending = [day.rows[i] for i in day.live()]
            start = _cycle_breaker(pending)
            if start is not None:
                tail = _opened(day.take(next(i for i in day.live() if day.rows[i] is start)))
                tails.append(tail)
                _extend(day, tail)
                continue
            candidates = _cap(sorted({round(r.after, 4) for r in pending}))  # type: ignore[arg-type]
            filed = max(r.filed for r in pending)
            statement = all(r.statement for r in pending)
            if len(candidates) > 1:
                tails.append(_Tail(None, date_key, filed, candidates, statement=statement,
                                   lot=statement, opened=date_key))
            else:
                # Every line left reports the same balance: identical holdings lines, which
                # may be ONE holding restated or several of that size — one tail that says how
                # many lines it stands for (review F2).
                tails.append(_Tail(candidates[0], date_key, filed, statement=statement,
                                   lot=statement, opened=date_key, lines=len(pending)))
            for i in day.live():
                day.take(i)
    return tails


def _cap(values: List[float]) -> Tuple[float, ...]:
    """At most `MAX_CANDIDATES` balances, always keeping the smallest and the largest."""
    if len(values) <= MAX_CANDIDATES:
        return tuple(values)
    half = MAX_CANDIDATES // 2
    return tuple(values[:half] + values[-half:])


def _number(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def _display_name(raw: Any) -> str:
    """A person's name as 'First Middle Last'; an entity's (LLC, fund, trust…) as filed."""
    if isinstance(raw, str) and _ENTITY_RE.search(raw):
        text = " ".join(raw.split())
        return text.title() if text.isupper() else text
    return normalize_insider_name(raw)


def _entry(
    security: str, held: str, tail: _Tail, stale_after: str, *, earlier: bool = False,
    lines: int = 1,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "security": security or "Shares",
        "held": held,
        "shares": _number(tail.balance),
        "as_of": tail.date,
        "filed": tail.filed or None,
        "reported_earlier": earlier,
    }
    if tail.balance is None:
        entry["possible_shares"] = [_number(c) for c in tail.candidates]
    if lines > 1:
        # Several lines that day ended on exactly this balance and nothing shows they are
        # separate holdings: one entry — which says it may stand for more than one (F2).
        entry["same_balance_lines"] = lines
    # ON the balance's own day too: a day's lines have no known order (the reason `_chain`
    # exists), so a same-day line that reported no usable balance — or no direct/indirect
    # flag — may be the day's LAST trade. Venturo's 09-30 sale with a null balance left the
    # pre-sale 368,142 unflagged beside that very sale.
    if stale_after and stale_after >= tail.date:
        entry["changed_after"] = stale_after
    return entry


def _merged(group: List[_Tail]) -> _Tail:
    """One day's chains as one tail: ambiguous over their ends when they differ."""
    if len(group) == 1:
        return group[0]
    values = sorted({v for t in group
                     for v in ((t.balance,) if t.balance is not None else t.candidates)})
    day, filed = group[0].date, max(t.filed for t in group)
    return _Tail(None, day, filed, _cap(values)) if len(values) > 1 else _Tail(values[0], day, filed)


def _emptied(tail: _Tail) -> bool:
    return tail.balance is not None and not tail.balance > 0


def _without_older_repeats(tails: List[_Tail]) -> List[_Tail]:
    """`tails` minus any that end on exactly the balance of one from a NEWER date — that same
    holding past a row the feed lacks, or a lot size the filer repeats (OKTA's director
    converted 65,000 Class A shares from 0 on five dates): listing it again adds nothing.
    Same-day equal balances are left to the caller. Order kept; O(n log n)."""
    newer: set = set()
    today: set = set()
    current = None
    dropped: set = set()
    for tail in sorted(tails, key=lambda t: t.date, reverse=True):
        if tail.date != current:
            newer |= today
            today, current = set(), tail.date
        if tail.balance is None:
            continue
        key = _whole(tail.balance)
        if key in newer:
            dropped.add(id(tail))
        else:
            today.add(key)
    return [t for t in tails if id(t) not in dropped]


def _direct_entries(
    security: str, tails: List[_Tail], stale_after: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """The direct line: the holding the person trades from, plus any lot reported on its own.

    * The TRADED holding is the transaction chain that moved last. Chains ending on that
      same day (a same-day chain broken by a missing or unreadable line) cannot be told
      apart — one ambiguous entry.
    * An OLDER chain is the traded holding's own earlier state past a row the feed lacks,
      and is not repeated — unless every chain after it OPENED FROM 0 (`from_zero`): a
      position opened from nothing continues no older chain, so the older one's last figure
      stays, as an earlier figure (review F1: a conversion sold down to 80 erased the 302,526
      held directly the day before). A position opened from 0 and emptied on that same day
      adds nothing beside it; one that ran across days is the running holding, and its 0 is
      the current figure (R5-1). The cap never cuts the chain the walk back ends on (R5-2).
    * A LOT is a chain opened by a separately footnoted 'Common Stock … D' line (an ESPP or
      restricted-stock lot) on a Form 4, and it stays one after it trades (`_Tail.lot`):
      listed beside the traded holding, never competing with it (review F1: one withheld lot
      share made the lot 'the traded holding' and the main 5,000 shares vanished — or, beside
      a restated main line, merged into a fake 'one of 2,000 or 5,000'). An emptied lot adds
      nothing; a holdings line that never traded and restates a shown balance is not a lot.
    * Anything last reported before the line's newest date is `reported_earlier` — the
      traded holding too, when a lot moved after it: the rows cannot say whether an older
      figure is a separate holding or a stale state of one listed. An older figure equal to
      a newer one is not repeated (`_without_older_repeats`, the indirect line's rule).
    """
    newest = max(t.date for t in tails)
    by_day: Dict[str, List[_Tail]] = {}
    for t in tails:
        if not t.lot:
            by_day.setdefault(t.date, []).append(t)
    lots = [t for t in tails if t.lot]
    picked: List[_Tail] = []
    fallback: Optional[_Tail] = None
    anchor: Optional[_Tail] = None
    for n, day in enumerate(sorted(by_day, reverse=True)):
        # A position opened from 0 and emptied, beside another chain that day: its 0 is that
        # position's own, never the day's figure (review R5-3: MGRX's CEO read 'one of 0 or
        # 3,305,000' after gifting an award lot into his main holding).
        group = [t for t in by_day[day] if not (t.from_zero and _emptied(t))] or by_day[day]
        tail = _merged(group)
        fallback = fallback or tail
        from_zero = all(t.from_zero for t in group)
        # Only a position opened AND emptied on its own last day adds nothing beside older
        # figures. A from-zero chain that ran across days is the person's running holding, and
        # its 0 is the reported current figure — always listed (review R5-1: a CAO's sale to 0
        # read as a 2025 figure).
        opened_and_closed = from_zero and all(t.opened == t.date for t in group)
        if not (_emptied(tail) and (n or opened_and_closed)):
            picked.append(tail)
        if not from_zero:
            anchor = tail if picked and picked[-1] is tail else None
            break
    seen = {k for t in picked for k in t.keys()}
    for lot in sorted(lots, key=lambda t: (t.date, t.balance or 0.0), reverse=True):
        keys = set(lot.keys())
        if _emptied(lot) or (lot.statement and keys & seen):
            continue
        seen.update(keys)
        picked.append(lot)
    if not picked:
        # Every holding on the line was emptied: the newest figure is the answer (0 shares).
        picked = [fallback or max(lots, key=lambda t: (t.date, t.filed))]
    picked = _without_older_repeats(picked)
    shown = picked[:1 + MAX_INDIRECT_BALANCES]
    if (anchor is not None and len(picked) > len(shown)
            and any(t is anchor for t in picked) and all(t is not anchor for t in shown)):
        # The walk back's last chain is the running holding it exists to keep: it takes the
        # last place, and the oldest from-zero figure before it is cut and counted instead
        # (review R5-2: six quarterly awards pushed a director's 14,953 out of the cap).
        shown = shown[:-1] + [anchor]
    entries = [_entry(security, "direct", t, stale_after, earlier=t.date < newest) for t in shown]
    return entries, len(picked) - len(shown)


def _merge_equal_balances(live: List[_Tail]) -> List[Tuple[_Tail, int]]:
    """Indirect tails (newest first) → ``(tail, lines it stands for)`` to list.

    * An OLDER tail ending on exactly the balance a newer one ends on is that same holding
      with a missing row between (NVDA's general counsel's trust read 2,687,660 on 08-31 and
      again on 09-21) — listing it twice would read as two trusts. Dropped quietly.
    * On the SAME day, two tails whose histories are known (`anchored`) are two holdings,
      however equal: twin trusts gifted 500,000 shares each and selling 10,000 each (review
      F2 — they used to read as ONE 490,000-share trust). A tail opened that day from a
      balance no row explains is folded into the day's entry for that balance instead — the
      filer who prints the end-of-day balance on every fill line (6,000 then 4,000 sold, both
      lines '490,000') must not read as two trusts — and the entry carries the number of
      lines it stands for, so it is never asserted to be exactly one holding.
    """
    order: List[Any] = []
    groups: Dict[Tuple[str, int], List[_Tail]] = {}
    newer: set = set()
    today: set = set()
    current = None
    for tail in live:
        if tail.date != current:
            newer |= today
            today, current = set(), tail.date
        if tail.balance is None:
            order.append(tail)
            continue
        key = _whole(tail.balance)
        if key in newer:
            continue
        today.add(key)
        group_key = (tail.date, key)
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append(tail)
    picked: List[Tuple[_Tail, int]] = []
    for item in order:
        if isinstance(item, _Tail):
            picked.append((item, item.lines))
            continue
        members = groups[item]
        anchored = [t for t in members if t.anchored]
        loose = [t for t in members if not t.anchored]
        heads = anchored or loose[:1]
        folded = sum(t.lines for t in (loose if anchored else loose[1:]))
        for n, tail in enumerate(heads):
            picked.append((tail, tail.lines + (folded if n == 0 else 0)))
    return picked


def _line_entries(
    rows: List[_Row], held: str, unplaced_dates: List[str],
) -> Tuple[List[Dict[str, Any]], int]:
    """The balance entries of one line, plus how many indirect balances were left out."""
    labelled = [r for r in rows if r.security]
    security = max(labelled, key=lambda r: (r.date, r.filed, -r.order)).security if labelled else ""
    tails = _chain(rows)
    # A later row that may have moved this line's balance without reporting a usable one:
    # this line's own rows with an unknown balance, and same-class rows with no ownership form.
    unknown = [r.date for r in rows if r.after is None] + unplaced_dates
    stale_after = max(unknown) if unknown else ""
    if not tails:
        return [], 0
    if held == "direct":
        return _direct_entries(security, tails, stale_after)
    # Indirect: every holding seen, newest first. An emptied holding (a reported 0) adds
    # nothing beside the others; it is kept only when it is all there is. A balance last
    # reported BEFORE the line's newest indirect transaction is `reported_earlier`: it may be a
    # separate holding that has not traded since, or an older state of a listed one whose
    # chain broke on a row the feed is missing — the two cannot be told apart, so it is never
    # presented as current beside the others, and nothing is summed.
    newest = max(t.date for t in tails)
    live = [t for t in tails if t.balance is None or t.balance > 0]
    if not live:
        live = [max(tails, key=lambda t: (t.date, t.filed))]
    live.sort(key=lambda t: (t.date, t.balance or 0.0), reverse=True)
    picked = _merge_equal_balances(live)
    shown = picked[:MAX_INDIRECT_BALANCES]
    return (
        [_entry(security, held, t, stale_after, earlier=t.date < newest, lines=lines)
         for t, lines in shown],
        len(picked) - len(shown),
    )


def _trades(rows: List[_Row]) -> List[Dict[str, Any]]:
    """The person's newest day of transactions, one entry per (code, direction)."""
    groups: Dict[Tuple[str, Optional[bool]], List[_Row]] = {}
    for row in rows:
        if row.code or row.shares:
            groups.setdefault((row.code, row.acquired), []).append(row)
    out: List[Dict[str, Any]] = []
    for (code, acquired), members in groups.items():
        counted = [r.shares for r in members if r.shares is not None]
        priced = [(r.shares, r.price) for r in members if r.shares and r.price]
        weight = sum(s for s, _ in priced)
        out.append({
            "transaction_type": code,
            "acquired": acquired,
            "shares": _number(sum(counted)) if counted else None,
            "average_price": round(sum(s * p for s, p in priced) / weight, 4) if weight else None,
        })
    out.sort(key=lambda t: -(t["shares"] or 0.0))
    return out[:MAX_TRADES]


def _person(rows: List[_Row]) -> Dict[str, Any]:
    newest_first = sorted(rows, key=lambda r: (r.date, r.filed, -r.order), reverse=True)
    latest_date = newest_first[0].date
    on_latest = [r for r in rows if r.date == latest_date]
    filed = max((r.filed for r in on_latest if r.filed), default="")
    # The newest row that names the person / their role; never an invented "Officer".
    name = next((r.name for r in newest_first if isinstance(r.name, str) and r.name.strip()), None)
    title = next((r.title for r in newest_first if isinstance(r.title, str) and r.title.strip()), None)
    role = clean_role_title(title).rstrip(":").strip() if title else "Insider"

    holdings: List[Dict[str, Any]] = []
    hidden = 0
    unplaced_by_class: Dict[str, List[str]] = {}
    for row in rows:
        if row.held is None:
            unplaced_by_class.setdefault(row.share_class, []).append(row.date)
    lines: Dict[Tuple[str, str], List[_Row]] = {}
    for row in rows:
        if row.held is not None:
            lines.setdefault((row.share_class, row.held), []).append(row)
    for (share_class, held) in sorted(lines, key=lambda k: (k[1] != "direct", k[0])):
        entries, omitted = _line_entries(
            lines[(share_class, held)], held, unplaced_by_class.get(share_class, []),
        )
        holdings.extend(entries)
        hidden += omitted
    return {
        "name": _display_name(name),
        "role": role or "Insider",
        "latest_transaction_date": latest_date,
        "latest_filing_date": filed or None,
        "latest_trades": _trades(on_latest),
        "holdings": holdings,
        # Left out by a line's cap — indirect holdings, and direct lots or earlier direct
        # figures (the direct line can list several since F1). It was named for indirect
        # holdings only, and the chat would have called a hidden direct lot "indirect".
        "holdings_not_shown": hidden,
    }


def _days_between(older: str, newer: str) -> int:
    return (date.fromisoformat(newer) - date.fromisoformat(older)).days


def insider_holdings_from_rows(
    rows: Any, *, max_people: int = MAX_PEOPLE, today: Optional[date] = None,
) -> Dict[str, Any]:
    """Per insider, the shares held right after their latest reported transaction on each
    holding line (direct; and each indirect holding separately), dated by that transaction.

    Returns ``{"covers_filings_since", "insiders", "insiders_not_shown",
    "insiders_not_shown_names", "inactive_not_shown", "inactive_not_shown_names",
    "rows_skipped"}``: people ordered most recent transaction first (at most ``max_people``;
    the rest NAMED, so a question about one of them is answered "not loaded", never "no
    filing"), and ``covers_filings_since`` the oldest filing date among the rows — a holding
    with no reported transaction since then is not visible here. A person whose newest
    FILING (on any of their rows; the trade date where a row has none) is more than
    `INACTIVE_AFTER_DAYS` older than the newest anyone filed is left out and named
    (`inactive_not_shown_names`): a light filer's page reaches back years (AAPL's reaches a
    director who left in 2019), and an officer or director files at least yearly. It used to
    read the TRADE date while the chat says "last filing": a Form 4 filed three weeks ago for
    a 2016 trade (filed late, or 2016 typed for 2026) hid a current director as "most likely
    no longer an insider" (review F5). `today` (UTC by default) bounds every date a row may
    carry. Never raises on malformed rows (they are skipped and counted)."""
    today = today or datetime.now(timezone.utc).date()
    latest = (today + timedelta(days=1)).isoformat()
    source = rows if isinstance(rows, list) else []
    parsed: List[_Row] = []
    skipped = 0
    for order, raw in enumerate(source):
        row = _parse(order, raw, latest)
        if row is None:
            skipped += 1
        else:
            parsed.append(row)
    filed_dates = [d for d in (_filed_day(r, latest) for r in source if isinstance(r, dict)) if d]
    by_person: Dict[str, List[_Row]] = {}
    for row in parsed:
        by_person.setdefault(row.reporter, []).append(row)
    filers = [(_person(members), max(r.filed or r.date for r in members))
              for members in by_person.values()]
    filers.sort(key=lambda pf: (pf[0]["latest_transaction_date"],
                                pf[0]["latest_filing_date"] or "", pf[0]["name"]), reverse=True)
    people: List[Dict[str, Any]] = []
    inactive: List[Dict[str, Any]] = []
    if filers:
        newest = max(last for _, last in filers)
        for person, last in filers:
            stale = _days_between(last, newest) > INACTIVE_AFTER_DAYS
            (inactive if stale else people).append(person)
    return {
        "covers_filings_since": min(filed_dates) if filed_dates else None,
        "insiders": people[:max_people],
        "insiders_not_shown": max(0, len(people) - max_people),
        "insiders_not_shown_names": [p["name"] for p in people[max_people:]][:MAX_NAMED],
        "inactive_not_shown": len(inactive),
        "inactive_not_shown_names": [p["name"] for p in inactive][:MAX_NAMED],
        "rows_skipped": skipped,
    }
