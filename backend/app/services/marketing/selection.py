"""
Which Learn item, in which template, on which day (SYSTEM_DESIGN_GUIDELINES §12.5).

Pure. Two decisions, both a function of the ET run date and the set of eligible items:

* **Cadence.** The plan's reach research says 3-5 varied posts a week beat seven identical ones,
  so only `POST_WEEKDAYS` are posting days; the rest are rest days (the run closes `skipped`).
* **Rotation.** Posting days are numbered from `EPOCH` (the POSTING-DAY ordinal, so rest days do
  not burn picks), and `daily_rotation.pick_for_day` walks the pool in disjoint cycles — every
  eligible item once before any repeats. Its schedule reshuffles whenever the pool changes
  (`n` is in its seed), so `choose` also skips anything in `recent` (the `source_ref`s of the last
  runs, read by the caller): a corpus edit or a cadence change can never make the public feed
  repeat last week's item. Within one run, the pick is made once and frozen by the caller's
  first-write-wins INSERT, so this function never has to be stable across a `recent` change.

Drop 2 ("Company Weekly", owner decisions 2026-10-09) adds the weekly SERIES CALENDAR below:
`plan_for` maps a posting day to a fallback chain of news series that always ends in the Learn
lesson, `enabled_chain` keeps only the series whose content class is switched on and whose code has
shipped, and `content_class_of` derives a day's class from its frozen `template_id` (a lesson
template → "A", a series id → its class). With only class "A" enabled every posting day's chain is
`(LESSON,)` and `choose` runs exactly as before, byte for byte. Drop 2b adds a PER-SERIES switch on
top of the classes: `parse_news_series` reads the web setting MARKETING_NEWS_SERIES into the shipped
series it leaves on, which the caller passes to `enabled_chain` as its `shipped` set. Every series
has shipped since drop 2b; the switch's default (the 2a four) keeps the 2b series off in production.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from app.services.daily_rotation import pick_for_day
# Pure (no FMP, no I/O): the SEC's 13F due dates on the federal business-day calendar.
from app.services.trillion_club.rules import previous_quarter, sec_13f_due_date

logger = logging.getLogger(__name__)

#: Monday, Tuesday, Thursday, Saturday — four posting days a week.
POST_WEEKDAYS = (0, 1, 3, 5)

#: A fixed Monday. The posting-day ordinal counts from here; changing it reshuffles everything.
EPOCH = date(2026, 9, 21)

#: How many recent picks to avoid repeating (bounded by the pool size at the call site).
RECENT_WINDOW = 60

ITEM_SALT = "marketing"
TEMPLATE_SALT = "marketing-template"


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    instructions: str
    kinds: frozenset


TEMPLATES: Sequence[Template] = (
    Template(
        "myth_vs_fact", "Myth vs fact",
        "Open with a common misconception the source corrects - in the hook, labelled ('Myth: "
        "...') or reported ('Many believe ...'), never as a yes/no question - then walk through "
        "what the source actually shows. Cards: the myth, then the facts that answer it.",
        frozenset({"money_moves", "journey"}),
    ),
    Template(
        "three_takeaways", "Three takeaways",
        "Open the hook on the most surprising of the three lessons - a fact or a how, why or "
        "what question, never a count of lessons or an instruction to learn. Distil the source "
        "into exactly three lessons a reader can reuse, two script lines each. Cards: one lesson "
        "each, in the script's order.",
        frozenset({"money_moves", "journey"}),
    ),
    Template(
        "case_story", "Case story",
        "Open the hook on the turning point - the choice, bet or surprise that changed the "
        "business, naming the company - then tell the business history as a short story with a "
        "beginning, that turning point and the lesson it teaches about how businesses win or "
        "lose. Past tense for every dated fact. Cards: the setup, the turning point, the lesson.",
        frozenset({"money_moves"}),
    ),
    Template(
        "question_hook", "Question hook",
        "Open with one how, why or what question a curious beginner would ask - never a yes/no "
        "question - answer it step by step from the source, and end on the principle, never on "
        "a verdict about any company.",
        frozenset({"money_moves", "journey"}),
    ),
    Template(
        "checklist", "Checklist",
        "Open the hook on the costly mistake or the hidden question the checklist guards "
        "against - a fact or a how, why or what question, never 'Understand...', 'Learn...' or a "
        "count. Then turn the source into a short checklist of things to look for or ask, one "
        "check per card. It is a learning checklist, never a list of what to buy or sell.",
        frozenset({"money_moves", "journey"}),
    ),
)
TEMPLATES_BY_ID = {t.id: t for t in TEMPLATES}


@dataclass(frozen=True)
class Selection:
    rest_day: bool
    source_ref: Optional[str] = None
    template_id: Optional[str] = None
    posting_ordinal: Optional[int] = None


def is_posting_day(run_date: date) -> bool:
    return run_date.weekday() in POST_WEEKDAYS


def posting_ordinal(run_date: date) -> int:
    """How many posting days lie between `EPOCH` (inclusive) and `run_date` (exclusive).
    Negative before the epoch; floor division keeps it consistent across the boundary."""
    days = (run_date - EPOCH).days
    weeks, offset = divmod(days, 7)  # EPOCH is a Monday, so `offset` is the weekday
    return weeks * len(POST_WEEKDAYS) + sum(1 for w in POST_WEEKDAYS if w < offset)


def _synthetic_day(ordinal: int) -> str:
    """Posting ordinal → an ISO date `pick_for_day` can consume, one calendar day per posting
    day, so its disjoint-cycle guarantees apply to POSTING days rather than calendar days."""
    return date.fromordinal(max(1, EPOCH.toordinal() + ordinal)).isoformat()


def _pick(pool: Sequence[str], ordinal: int, salt: str) -> Optional[str]:
    got = pick_for_day(list(pool), 1, _synthetic_day(ordinal), salt=salt)
    return got[0] if got else None


def choose_item(pool: Iterable[str], ordinal: int, recent: Iterable[str] = ()) -> Optional[str]:
    """The item for posting day `ordinal`, skipping anything in `recent`. Walks forward through
    the rotation (the next posting days' picks) until an item is not recent; if EVERY item is
    recent (a pool smaller than the window), returns the rotation's own pick."""
    items = sorted(set(pool))
    if not items:
        return None
    avoid = set(recent)
    first = _pick(items, ordinal, ITEM_SALT)
    if first not in avoid:
        return first
    for step in range(1, 2 * len(items) + 1):
        cand = _pick(items, ordinal + step, ITEM_SALT)
        if cand is not None and cand not in avoid:
            return cand
    fresh = [i for i in items if i not in avoid]
    return fresh[0] if fresh else first


def choose_template(kind: str, ordinal: int, allowed: Optional[Sequence[str]] = None) -> str:
    """The template for a `kind` item on posting day `ordinal`. `allowed` (drop 2: the weekly
    lesson's `LESSON_TEMPLATE_IDS` while a news class is on) narrows the rotation to those ids; when
    it leaves nothing eligible for `kind`, the full eligible list is used (logged WARNING) — a
    lesson always gets a template that fits its kind. An unknown kind has none (IndexError)."""
    eligible = [t.id for t in TEMPLATES if kind in t.kinds]
    if allowed is not None:
        wanted = {allowed} if isinstance(allowed, str) else set(allowed)
        narrowed = [tid for tid in eligible if tid in wanted]
        if narrowed:
            eligible = narrowed
        elif eligible:
            logger.warning("marketing selection: no allowed template %s fits kind %r — using every "
                           "eligible template", sorted(wanted), kind)
    return _pick(eligible, ordinal, TEMPLATE_SALT) or eligible[0]


def choose(pool: Iterable[str], run_date: date, recent: Iterable[str] = (), *,
           templates: Optional[Sequence[str]] = None) -> Selection:
    """The selection for one ET run date. Pure; `recent` is supplied by the caller (lesson refs
    only once news posts share the ledger — `lesson_refs`). `templates` narrows the template
    rotation (`choose_template`'s `allowed`); None = every eligible template, exactly as before."""
    if not is_posting_day(run_date):
        return Selection(rest_day=True)
    ordinal = posting_ordinal(run_date)
    recent_list: List[str] = list(recent)
    pool_list = sorted(set(pool))
    window = max(0, min(len(pool_list) - 1, RECENT_WINDOW))
    key = choose_item(pool_list, ordinal, recent_list[:window])
    if key is None:
        return Selection(rest_day=False, posting_ordinal=ordinal)
    kind = key.split(":", 1)[0]
    return Selection(
        rest_day=False, source_ref=key, template_id=choose_template(kind, ordinal, templates),
        posting_ordinal=ordinal,
    )


# ── Drop 2: the weekly series calendar ("Company Weekly", owner decisions 2026-10-09) ─────────

#: The chain step that ends every posting day: the Learn lesson (class A, `choose` above).
LESSON = "lesson"
#: The `source_ref` prefixes of a lesson (pinned == content_pool.MONEY_MOVES / JOURNEY).
LESSON_KINDS: Tuple[str, ...] = ("money_moves", "journey")
#: The weekly lesson's templates while a news class is on (plan §3: myth-vs-fact / question-hook).
LESSON_TEMPLATE_IDS: Tuple[str, ...] = ("myth_vs_fact", "question_hook")


@dataclass(frozen=True)
class Series:
    id: str
    content_class: str
    #: Operator label (logs, digest, Telegram) — never public copy.
    name: str


#: Every news series, shipped or not. APPEND-ONLY: a stored `marketing_scripts.template_id` names
#: one of these (or a lesson template) for good, and `content_class_of` must keep resolving it, or
#: an accepted row becomes a post with no gate (tests/test_marketing_selection.py pins the ids).
SERIES: Tuple[Series, ...] = (
    Series("ceo_buys", "C", "CEO Buys"),
    Series("insider_buys", "C", "Insider Buys"),
    Series("thirteen_f", "C", "13F Season"),
    Series("congress_count", "C", "Congress Count"),
    Series("company_stakes", "F", "Big Company Bets"),
    Series("earnings", "F", "Earnings vs Estimates"),
    Series("money_map", "F", "Money Map"),
    Series("theme_explainer", "F", "Theme explainer"),
)
SERIES_BY_ID = {s.id: s for s in SERIES}
#: The series whose collector, template and image layout exist. A series missing here is skipped by
#: `enabled_chain` and its day falls through to the next step of the chain. Drop 2a shipped the first
#: four; drop 2b (2026-10-10) ships the other four with their `pair` / `grid` layouts — in CODE only:
#: production runs a series only while the per-series switch lists it too (`parse_news_series`; the
#: web setting MARKETING_NEWS_SERIES defaults to the 2a four, so the 2b series stay off until the
#: owner lists them). Pinned == `company_news_adapter.COLLECTORS` and ⊆ `news_templates.SERIES_SPECS`.
SHIPPED_SERIES: FrozenSet[str] = frozenset({
    "ceo_buys", "insider_buys", "thirteen_f", "money_map",                       # drop 2a
    "congress_count", "company_stakes", "earnings", "theme_explainer",          # drop 2b
})


def parse_news_series(raw: Any) -> FrozenSet[str]:
    """The news series the per-series switch (the web setting MARKETING_NEWS_SERIES) leaves on NOW:
    the setting's ids ∩ SHIPPED_SERIES, the shipped set read at call time (like `enabled_chain`'s
    default). Pass it to `enabled_chain(..., shipped=...)`, and `run_service.create_posts` re-reads it
    before recording a template's posts. Pure, never raises.

    Comma-separated; each token stripped and lower-cased; empty tokens skipped; duplicates collapse.
    (Every series has shipped since drop 2b; "not shipped" below is a rollback that withdraws one.)
    A token that names no series ("lesson" included: the lesson is no series and is always on) or a
    series that has not shipped yet (its collector and template do not exist) is DROPPED and logged at
    ERROR — an early id or a typo can only narrow the week, never open a series without its code and
    never fail a request; a listed series starts running the day its id joins SHIPPED_SERIES. An empty
    value means no news series at all (every chain is the lesson); a non-string reads as empty
    (ERROR). Every logged token is bounded: a pasted blob cannot flood the log."""
    if not isinstance(raw, str):
        logger.error("MARKETING_NEWS_SERIES: not a string (%s) — no news series", type(raw).__name__)
        return frozenset()
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    unknown = sorted(t for t in tokens if t not in SERIES_BY_ID)
    if unknown:
        logger.error("MARKETING_NEWS_SERIES: ignored unknown series %s",
                     ",".join(t[:24] for t in unknown[:10]))
    shipped = SHIPPED_SERIES
    unshipped = sorted(t for t in tokens if t in SERIES_BY_ID and t not in shipped)
    if unshipped:
        logger.error("MARKETING_NEWS_SERIES: ignored series not shipped yet %s — their days fall through "
                     "to the next step of the chain", ",".join(unshipped))
    return frozenset(t for t in tokens if t in SERIES_BY_ID and t in shipped)

#: A 13F season runs from the quarter's legal due date D to D + 41 (inclusive).
THIRTEEN_F_SEASON_DAYS = 42
#: Earnings season: these many days after the latest quarter end, both inclusive.
EARNINGS_SEASON_OFFSETS: Tuple[int, int] = (10, 56)
#: The Congress Count Tuesday: the one whose day of month is in this range (the first Tuesday on
#: or after the 8th — at least 7 days after the disclosure month ends).
CONGRESS_DAYS: Tuple[int, int] = (8, 14)

#: The years `trillion_club.rules` keeps a federal calendar for, with a quarter of margin each side.
_SEC_CALENDAR_YEARS: Tuple[int, int] = (1901, 9998)

_QUARTER_END_DAY = {3: 31, 6: 30, 9: 30, 12: 31}


def content_class_of(template_id: Optional[str]) -> Optional[str]:
    """The content class a frozen `marketing_scripts.template_id` decides: a lesson template → "A",
    a series id → that series' class, anything else (None, a typo, a retired id) → None. This is the
    class that gates a post; `marketing_runs.content_class` is only a mirror of it."""
    if not isinstance(template_id, str):
        return None
    if template_id in TEMPLATES_BY_ID:
        return "A"
    series = SERIES_BY_ID.get(template_id)
    return series.content_class if series is not None else None


def _latest_quarter_end(d: date) -> Optional[date]:
    """The latest calendar quarter end on or before `d` (None only before year 1's first one)."""
    end_month = ((d.month - 1) // 3 + 1) * 3
    end = date(d.year, end_month, _QUARTER_END_DAY[end_month])
    if end == d:
        return end
    prev_month = end_month - 3
    if prev_month == 0:
        return date(d.year - 1, 12, 31) if d.year > 1 else None
    return date(d.year, prev_month, _QUARTER_END_DAY[prev_month])


def thirteen_f_season(d: date) -> Optional[Tuple[int, int]]:
    """The `(year, quarter)` whose 13F wave is in season on `d` — its legal due date D
    (`trillion_club.rules.sec_13f_due_date`: quarter end + 45, rolled past weekends and federal
    holidays) satisfies D <= d < D + THIRTEEN_F_SEASON_DAYS — or None. Only the two quarters that
    ended before `d`'s own quarter can qualify (a quarter's due date is after its end)."""
    if not _SEC_CALENDAR_YEARS[0] <= d.year <= _SEC_CALENDAR_YEARS[1]:
        return None
    last = previous_quarter(d.year, (d.month - 1) // 3 + 1)
    for year_quarter in (last, previous_quarter(*last)):
        due = sec_13f_due_date(*year_quarter)
        if due <= d < due + timedelta(days=THIRTEEN_F_SEASON_DAYS):
            return year_quarter
    return None


def is_congress_count_day(d: date) -> bool:
    """The month's Congress Count Tuesday: a Tuesday whose day of month is 8..14."""
    return d.weekday() == 1 and CONGRESS_DAYS[0] <= d.day <= CONGRESS_DAYS[1]


def congress_disclosure_month(d: date) -> str:
    """"YYYY-MM" of the calendar month before `d` — the disclosure month a Congress Count names."""
    prev = d.replace(day=1) - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def in_earnings_season(d: date) -> bool:
    """True when `d` is EARNINGS_SEASON_OFFSETS[0]..[1] days (inclusive) after the latest calendar
    quarter end on or before it."""
    qe = _latest_quarter_end(d)
    if qe is None:
        return False
    lo, hi = EARNINGS_SEASON_OFFSETS
    return lo <= (d - qe).days <= hi


@dataclass(frozen=True)
class DayPlan:
    rest_day: bool
    #: Series ids in fallback order, always ending in LESSON; () on a rest day.
    chain: Tuple[str, ...] = ()
    #: rest | monday | tuesday[_congress][_13f] | thursday_earnings | thursday_off | saturday
    reason: str = "rest"


_MONDAY: Tuple[str, ...] = ("ceo_buys", "insider_buys", "money_map", "theme_explainer", LESSON)
_TUESDAY_TAIL: Tuple[str, ...] = ("company_stakes", "theme_explainer", "money_map", LESSON)
_THURSDAY_TAIL: Tuple[str, ...] = ("money_map", "theme_explainer", LESSON)


def plan_for(d: date) -> DayPlan:
    """The day's series chain (before the class switch and the shipped set are applied).

    Mon: CEO buys → insider buys → Money Map → theme explainer → lesson.
    Tue: [Congress Count on its Tuesday] + [13F Season while in season] + company stakes → theme
         explainer → Money Map → lesson.
    Thu: [earnings while in season] + Money Map → theme explainer → lesson.
    Sat: the lesson. Any other day: rest."""
    if not is_posting_day(d):
        return DayPlan(rest_day=True)
    weekday = d.weekday()
    if weekday == 0:
        return DayPlan(False, _MONDAY, "monday")
    if weekday == 1:
        congress = is_congress_count_day(d)
        season = thirteen_f_season(d) is not None
        head = (("congress_count",) if congress else ()) + (("thirteen_f",) if season else ())
        reason = "tuesday" + ("_congress" if congress else "") + ("_13f" if season else "")
        return DayPlan(False, head + _TUESDAY_TAIL, reason)
    if weekday == 3:
        if in_earnings_season(d):
            return DayPlan(False, ("earnings",) + _THURSDAY_TAIL, "thursday_earnings")
        return DayPlan(False, _THURSDAY_TAIL, "thursday_off")
    if weekday == 5:
        return DayPlan(False, (LESSON,), "saturday")
    # A posting weekday with no series calendar (POST_WEEKDAYS changed without this function).
    logger.warning("marketing selection: posting weekday %d has no series calendar — lesson only", weekday)
    return DayPlan(False, (LESSON,), "lesson_only")


def enabled_chain(chain: Iterable[str], classes: FrozenSet[str],
                  shipped: Optional[FrozenSet[str]] = None) -> Tuple[str, ...]:
    """`chain` with every series dropped whose class is not in `classes`, that has not shipped
    (`shipped`, default SHIPPED_SERIES read at call time; script_service passes the per-series
    switch, `parse_news_series(settings.MARKETING_NEWS_SERIES)`), or that is unknown; LESSON is kept
    whatever `classes` says, duplicates are removed in order and nothing after the first LESSON is
    kept (it could never run). With classes {"A"} a plan's chain is `(LESSON,)`."""
    if shipped is None:
        shipped = SHIPPED_SERIES
    out: List[str] = []
    for step in chain:
        if step == LESSON:
            out.append(LESSON)
            break
        series = SERIES_BY_ID.get(step)
        if series is None or series.content_class not in classes or step not in shipped or step in out:
            continue
        out.append(step)
    return tuple(out)


def lesson_refs(recent: Iterable[str]) -> List[str]:
    """The lesson `source_ref`s of `recent`, in order (prefix before ":" in LESSON_KINDS). News refs
    ("news:<series>:…") never reach `choose`: they would crowd its `recent_list[:window]` slice and
    let the lesson pool repeat after a handful of lessons."""
    out: List[str] = []
    for ref in recent:
        if not isinstance(ref, str):
            continue
        kind, sep, _ = ref.partition(":")
        if sep and kind in LESSON_KINDS:
            out.append(ref)
    return out
