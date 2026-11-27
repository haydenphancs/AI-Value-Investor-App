"""
Which Learn item, in which template, on which day (SYSTEM_DESIGN_GUIDELINES §12.5).

Pure, given the posting days (a setting read at call time, below). Two decisions, both a function of
the ET run date and the set of eligible items:

* **Cadence.** The plan's reach research says 3-5 varied posts a week beat seven identical ones,
  so only the posting days post — `POST_WEEKDAYS` (Mon/Tue/Thu/Sat) by default, the web setting
  MARKETING_POST_WEEKDAYS since Batch 3 (seven varied days once Company Weekly runs); the rest are rest
  days (the run closes `skipped`).
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

Batch 3 (2026-10-20, owner decision 3) makes the cadence a switch: the web setting
MARKETING_POST_WEEKDAYS, parsed by `parse_post_weekdays` and read at CALL time by `post_weekdays()`
(its default is `POST_WEEKDAYS`, kept as the DEFAULT_POST_WEEKDAYS). `is_posting_day`,
`posting_ordinal` and `plan_for` read it (their `weekdays` argument defaults to it; `choose` reads it
once per call), and so do the digest and the run-health timing. `plan_for` gives Wednesday and Friday
Thursday's calendar (earnings in season, then the Money Map tail) and Sunday the Money Map tail alone —
never earnings, which leads Wed/Thu/Fri only; Saturday stays the one lesson-only day. With the default
every calendar answer is today's, byte for byte. `news_recent_limit()` sizes the ledger read the script
service makes from it: RECENT_WINDOW posting days of history — 240 rows at four days a week, 420 at seven.

Why a switch mid-week can neither repeat nor skip a lesson: the switch renumbers the posting ordinal,
which moves the rotation's OWN pick — but `choose` skips the last pool − 1 lessons it is given (a pool
of up to RECENT_WINDOW + 1), and once the read holds that many, that rule alone decides every pick (the
one lesson not among them), whatever the ordinal says. `news_recent_limit()` scales the read with the
cadence so it holds them: RECENT_WINDOW weeks of history even when Saturday is the week's only lesson
(420 rows = 60 Saturdays at seven days).
One gap is accepted: right after a switch from seven days back to four the read is 240 rows, about 34
weeks of seven-day history — every lesson of today's 34-item pool, but not of a pool above ~35 in weeks
whose only lesson is Saturday's. A week without "sat" posts a lesson only when a news day falls through
to it (the parse warns). tests/test_marketing_selection.py simulates the switches.

The SPOTLIGHT lane (owner decision 2026-11-15, "Add a second daily post" on "Tue, Thu, Sat") is a second run
on some days, claimed as its own row (migration 210: UNIQUE (run_date, lane)). Everything about it here is
read at CALL time and is inert while its switch is off: `spotlight_on()` (MARKETING_SPOTLIGHT_ENABLED),
`spotlight_weekdays()` (MARKETING_SPOTLIGHT_WEEKDAYS — fail-closed: an unreadable value is NO day, never the
default), `spotlight_lane_open()` (the switch, a day listed, the hour rule spotlight hour +
MARKETING_MAX_RUN_ATTEMPTS <= MARKETING_RUN_HOUR_ET, and a news class enabled — a closed lane is logged at
ERROR once) and `is_spotlight_day(d)`. `spotlight_plan_for(d)` is its calendar: the spotlight series (the insider
LIST and the 13F list since the owner decisions of 2026-11-20 — never one company — with the theme right after the
insider list), then Money Map, ending in the terminal SKIP — never the lesson (the main lane posts the week's), and
never the weekly Form 4 roundups (their window and ledger key roll with the run date, so a spotlight day would
re-post Monday's roundup). Both lanes are gated by the same per-series switch (`parse_news_series`) and class
switch through `enabled_chain`, which keeps SKIP as a terminal exactly like LESSON. `news_recent_limit()` sizes
the ledger read for both lanes' rows while the lane is open; closed, it is today's, byte for byte.

C0 (2026-11-16): the spotlight never competes for a series the MAIN lane would use that day. `spotlight_chain(d)`
drops every series of the main lane's enabled chain that day (`main_lane_series`) from the spotlight's, and a date
whose spotlight chain is left with no series is NOT a spotlight day (`spotlight_day_problem` /
`is_spotlight_day`, the one predicate the claim gate, the kick and the digest share): no run row, no alert. Before,
a spotlight Money Map sent the main lane's same-day Money Map to the lesson — a second lesson that week.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, FrozenSet, Iterable, List, Optional, Sequence, Tuple

# FMP-free (the settings object only, as smart_link reads it): the posting days are read at call time.
from app.config import settings
# Pure (pydantic only): the class switch's parse, read by the spotlight lane's open check; the replaceable series
# (migration 215, `replacement_chain`).
from app.schemas.marketing import NEWS_CLASSES, REPLACEABLE_SERIES, parse_content_classes
from app.services.daily_rotation import pick_for_day
# Pure (no FMP, no I/O): the SEC's 13F due dates on the federal business-day calendar.
from app.services.trillion_club.rules import previous_quarter, sec_13f_due_date

logger = logging.getLogger(__name__)

#: Monday, Tuesday, Thursday, Saturday — four posting days a week: the DEFAULT. Since Batch 3 the posting
#: days are the web setting MARKETING_POST_WEEKDAYS, read at call time (`post_weekdays()`); this tuple is its
#: declared default, parsed (pinned), and what every golden pick in the tests was cut against.
POST_WEEKDAYS = (0, 1, 3, 5)
DEFAULT_POST_WEEKDAYS: Tuple[int, ...] = POST_WEEKDAYS
#: MARKETING_POST_WEEKDAYS' tokens, indexed by `date.weekday()` (0 = Monday). Pinned == config's inline literal.
WEEKDAY_TOKENS: Tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

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


# ── Batch 3 (2026-10-20): the posting days are a switch ────────────────────────


def parse_post_weekdays(raw: Any) -> Tuple[int, ...]:
    """The posting days a MARKETING_POST_WEEKDAYS value names, as sorted `date.weekday()` numbers
    (0 = Monday). Pure, never raises, never empty.

    Three-letter tokens only (WEEKDAY_TOKENS), comma-separated; each stripped and lower-cased, empty
    tokens skipped, duplicates collapsed. An unknown token is DROPPED (ERROR); an empty value, one with
    no known day left, or a non-string is DEFAULT_POST_WEEKDAYS (ERROR) — a typo can never leave a
    week without a posting day. A week without "sat" (Saturday is the one lesson-only day, `plan_for`)
    is WARNED, not forced. Memoised on the raw string, so a bad live value logs once, not on every
    read; every logged token is bounded."""
    if not isinstance(raw, str):
        logger.error("MARKETING_POST_WEEKDAYS: not a string (%s) — using the default %s", type(raw).__name__,
                     _weekday_text(DEFAULT_POST_WEEKDAYS))
        return DEFAULT_POST_WEEKDAYS
    return _parse_post_weekdays_text(raw)


@functools.lru_cache(maxsize=16)
def _parse_post_weekdays_text(raw: str) -> Tuple[int, ...]:
    """`parse_post_weekdays` for a string: cached, so its logging happens once per distinct value."""
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    unknown = sorted(tokens - set(WEEKDAY_TOKENS))
    if unknown:
        logger.error("MARKETING_POST_WEEKDAYS: ignored unknown day(s) %s — the tokens are %s",
                     ",".join(t[:24] for t in unknown[:10]), ",".join(WEEKDAY_TOKENS))
    days = tuple(i for i, token in enumerate(WEEKDAY_TOKENS) if token in tokens)
    if not days:
        logger.error("MARKETING_POST_WEEKDAYS: no posting day left — using the default %s",
                     _weekday_text(DEFAULT_POST_WEEKDAYS))
        return DEFAULT_POST_WEEKDAYS
    if WEEKDAY_TOKENS.index("sat") not in days:
        logger.warning("MARKETING_POST_WEEKDAYS: %s has no sat — Saturday is the week's one lesson-only day; "
                       "without it a news week posts the lesson only when a news day falls through to it",
                       _weekday_text(days))
    return days


def _weekday_text(days: Iterable[int]) -> str:
    return ",".join(WEEKDAY_TOKENS[d] for d in days)


def post_weekdays() -> Tuple[int, ...]:
    """The posting days NOW: the web setting MARKETING_POST_WEEKDAYS, read from the settings object at
    CALL time — nothing derived from it is frozen at import, so the object in force (a redeploy's, a
    test's patch) always decides — and parsed by `parse_post_weekdays`."""
    return parse_post_weekdays(settings.MARKETING_POST_WEEKDAYS)


def news_recent_limit() -> int:
    """How many ledger rows a day's selection reads (`script_service`: the lesson rotation and the
    template build's exclude set): RECENT_WINDOW run days of history at the CURRENT cadence — the main
    lane's posting days, plus the spotlight lane's days while that lane is open (`spotlight_lane_open`),
    since each of its runs writes a ledger row too. 240 at the default four days a week with the lane
    closed (today's value, byte for byte), 420 at seven, 420 at four + three spotlight days, and at most
    840 (seven + seven) — under PostgREST's ~1,000-row page. Read at call time, like `post_weekdays()`."""
    days = len(post_weekdays())
    if spotlight_lane_open():
        days += len(spotlight_weekdays())
    return RECENT_WINDOW * days


def _calendar(weekdays: Optional[Iterable[int]]) -> Tuple[int, ...]:
    """The posting days a calendar call works on: `weekdays` as sorted, distinct `date.weekday()` numbers —
    or, when None, the setting NOW (`post_weekdays()`). An explicit value that is not weekday numbers (a
    bool, 7, "mon") is a caller's bug: ValueError, never a silently different calendar."""
    if weekdays is None:
        return post_weekdays()
    days = set(weekdays)
    if not all(type(d) is int and 0 <= d <= 6 for d in days):
        raise ValueError(f"posting weekdays must be date.weekday() numbers 0..6, not {sorted(map(repr, days))[:7]}")
    return tuple(sorted(days))


def is_posting_day(run_date: date, weekdays: Optional[Iterable[int]] = None) -> bool:
    """`run_date`'s weekday is a posting day — of `weekdays`, by default the setting read NOW."""
    return run_date.weekday() in _calendar(weekdays)


def posting_ordinal(run_date: date, weekdays: Optional[Iterable[int]] = None) -> int:
    """How many posting days lie between `EPOCH` (inclusive) and `run_date` (exclusive), on the calendar of
    `weekdays` (by default the setting read NOW). Negative before the epoch; floor division keeps it
    consistent across the boundary. A cadence switch renumbers every day (the ordinal counts the NEW
    calendar's days since EPOCH): it moves the rotation's own pick, never the recent-window rule (`choose`)."""
    days = _calendar(weekdays)
    span = (run_date - EPOCH).days
    weeks, offset = divmod(span, 7)  # EPOCH is a Monday, so `offset` is the weekday
    return weeks * len(days) + sum(1 for w in days if w < offset)


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
    rotation (`choose_template`'s `allowed`); None = every eligible template, exactly as before.
    The posting days are the setting read NOW, once, so the day test and the ordinal agree."""
    days = post_weekdays()
    if not is_posting_day(run_date, days):
        return Selection(rest_day=True)
    ordinal = posting_ordinal(run_date, days)
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
#: The chain step that ends every SPOTLIGHT day (`spotlight_plan_for`, 2026-11-15): nothing qualified, so
#: the run skips — `script_service` writes a `rejected` script with reject_reason `nothing_qualified` and the
#: run closes `skipped`. A terminal like LESSON (`enabled_chain` keeps it whatever the classes say and keeps
#: nothing after it), never a series id, never a template id, and never followed by the lesson.
SKIP = "skip"
#: The chain terminals: a main day ends in LESSON, a spotlight day in SKIP.
CHAIN_TERMINALS: FrozenSet[str] = frozenset({LESSON, SKIP})
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
    # The spotlight lane's series (2026-11-15; since the owner decisions of 2026-11-20 both are LISTS — the insider
    # spotlight 3-5 issuers, the 13F spotlight 2-8 holdings — never one company per post). Registered
    # here first (so `spotlight_plan_for`, the config's id literal and `content_class_of` knew them) and
    # SHIPPED with their collectors and templates (news-v22). The per-series switch keeps both OFF until the
    # owner lists them in MARKETING_NEWS_SERIES; until then a spotlight day's chain falls through them.
    Series("insider_spotlight", "C", "Insider Spotlight"),
    Series("thirteen_f_spotlight", "C", "13F Spotlight"),
)
SERIES_BY_ID = {s.id: s for s in SERIES}
#: The series only the spotlight lane runs (`spotlight_plan_for`); `plan_for`, the main lane, never names one.
SPOTLIGHT_SERIES: FrozenSet[str] = frozenset({"insider_spotlight", "thirteen_f_spotlight"})
#: The weekly Form 4 roundups: their window ([run_date − 7, run_date − 1]) and ledger key
#: (news:<series>:<window start>) roll with the run date, so the same week's roundup posted on another day
#: never matches the main lane's key. A spotlight chain must never hold one (it would re-post Monday's).
WEEKLY_ROUNDUP_SERIES: FrozenSet[str] = frozenset({"ceo_buys", "insider_buys"})
#: The series whose collector, template and image layout exist. A series missing here is skipped by
#: `enabled_chain` and its day falls through to the next step of the chain. Drop 2a shipped the first
#: four; drop 2b (2026-10-10) ships the other four with their `pair` / `grid` layouts — in CODE only:
#: production runs a series only while the per-series switch lists it too (`parse_news_series`; the
#: web setting MARKETING_NEWS_SERIES defaults to the 2a four, so the 2b series stay off until the
#: owner lists them). Pinned == `company_news_adapter.COLLECTORS` and ⊆ `news_templates.SERIES_SPECS`. The two
#: spotlight series (2026-11-15) ship with their collectors and templates (news-v22) — in CODE only, like 2b: the
#: switch's default leaves them off, they run only on the spotlight lane (`spotlight_plan_for`), and their
#: `feature` picture only for a holder that declared `layout_feature` (`script_service.FEATURE_LAYOUT_SERIES`).
SHIPPED_SERIES: FrozenSet[str] = frozenset({
    "ceo_buys", "insider_buys", "thirteen_f", "money_map",                       # drop 2a
    "congress_count", "company_stakes", "earnings", "theme_explainer",          # drop 2b
    "insider_spotlight", "thirteen_f_spotlight",                                # the spotlight lane (2026-11-15)
})


def parse_news_series(raw: Any) -> FrozenSet[str]:
    """The news series the per-series switch (the web setting MARKETING_NEWS_SERIES) leaves on NOW:
    the setting's ids ∩ SHIPPED_SERIES, the shipped set read at call time (like `enabled_chain`'s
    default). Pass it to `enabled_chain(..., shipped=...)`, and `run_service.create_posts` re-reads it
    before recording a template's posts. It gates BOTH lanes (2026-11-15): a series pulled here stops in
    the main lane and the spotlight lane alike — there is no separate spotlight series switch. Pure, never
    raises.

    Comma-separated; each token stripped and lower-cased; empty tokens skipped; duplicates collapse.
    ("Not shipped" below is a series registered ahead of its code — as the two spotlight series were until
    news-v22 — or a rollback that withdraws one.)
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
    #: Series ids in fallback order, ending in a terminal: LESSON on a main day (`plan_for`), SKIP on a
    #: spotlight day (`spotlight_plan_for`); () on a rest day.
    chain: Tuple[str, ...] = ()
    #: rest | monday | tuesday[_congress][_13f] | {wednesday,thursday,friday}_{earnings,off} | saturday | sunday
    #: (lesson_only: `plan_for`'s guard, unreachable while every weekday has a branch there)
    #: | spotlight | spotlight_13f | spotlight_13f_lead (`spotlight_plan_for`)
    #: | replacement (REPLACEMENT_PLAN_REASON: a replacement run's own plan, `replacement_chain` — migration 215)
    reason: str = "rest"


#: Owner decisions 2026-11-20 ("i pick option 1: 3-5, but if it's a quite week - if we don't have enough data, then use
#: different topic like posting the theme (cyber war, AI chip,....)"): the THEME post comes right AFTER the insider
#: series in every chain that holds them — a week with fewer than three qualifying companies posts a theme instead.
_MONDAY: Tuple[str, ...] = ("ceo_buys", "insider_buys", "theme_explainer", "money_map", LESSON)
_TUESDAY_TAIL: Tuple[str, ...] = ("company_stakes", "money_map", LESSON)
_THURSDAY_TAIL: Tuple[str, ...] = ("money_map", LESSON)
#: Batch 3: the tail of every day that has no series of its own — Thursday's, shared by Wednesday, Friday, Sunday.
_MONEY_MAP_TAIL: Tuple[str, ...] = _THURSDAY_TAIL
#: The weekdays that lead with earnings while in season (owner decision 3, 2026-10-15), by `date.weekday()`. news-v23
#: phase 3 (owner 2026-11-19, the weekly theme post): Wednesday LEADS with the theme and keeps earnings right after it
#: (a fallback when no theme qualifies), so earnings LEADS on Thursday and Friday only.
_EARNINGS_DAYS = {2: "wednesday", 3: "thursday", 4: "friday"}
#: news-v23 phase 3 (owner decision 2026-11-19, recorded in the proposed rules): the day a theme post LEADS the main
#: chain. Owner decisions 2026-11-20 (the quiet-week fallback): it also runs right after the insider series — Monday's
#: ceo_buys → insider_buys → theme_explainer, and the spotlight lane's insider_spotlight → theme_explainer — so a theme
#: may post on a Monday or a spotlight day too; the per-theme 28-day rule (`company_news_rules.THEME_REPEAT_DAYS`, read
#: over BOTH lanes' theme keys: `run_service.theme_scripts_between`) keeps any one theme to one post in four weeks, and
#: the weekly backup cap (THEME_FALLBACKS_PER_WEEK, owner 2026-11-27) keeps the quiet-week fallbacks to one a week.
THEME_WEEKDAY = 2
#: Owner decision 2026-11-27 ("1 backup theme a week (Recommended)": "At most one quiet-week theme per week across both
#: daily posts, so Wednesday almost always keeps a theme."): every theme post but the MAIN lane's Wednesday lead is a
#: quiet-week BACKUP (`theme_is_fallback`), and one ET week (`theme_week`, Monday..Sunday) holds at most this many
#: across BOTH lanes — a later backup step of that week falls back `week_cap` before the source is asked
#: (`script_service.theme_week_fallbacks`). The Wednesday lead is never capped.
THEME_FALLBACKS_PER_WEEK = 1


def theme_is_fallback(d: date, *, main_lane: bool) -> bool:
    """Is a theme post dated `d` a quiet-week BACKUP (owner decision 2026-11-27)? Every theme post is, except the MAIN
    lane's Wednesday lead (THEME_WEEKDAY: `plan_for` puts the theme first there). Pure."""
    return not (main_lane and d.weekday() == THEME_WEEKDAY)


def theme_week(d: date) -> Tuple[date, date]:
    """(Monday, Sunday) of the week holding `d` — the backup cap's window (owner decision 2026-11-27). Run dates are ET
    days, so this is the ET week; both ends inclusive. Pure."""
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def earnings_leads(d: date, weekdays: Optional[Iterable[int]] = None) -> bool:
    """Does an earnings post LEAD `d`'s chain? A Thursday or Friday in earnings season — and, news-v23 phase 3, the
    Thursday right after a season that ended on its Wednesday while Wednesday is a posting day of the calendar: that
    Wednesday now posts the theme first, and Thursday's widened window (`company_news_rules.earnings_window_days`, 8
    days) takes the reports it used to cover. Wednesday never leads with earnings (its theme does)."""
    weekday = d.weekday()
    if weekday not in (3, 4):
        return False
    if in_earnings_season(d):
        return True
    return weekday == 3 and THEME_WEEKDAY in _calendar(weekdays) and in_earnings_season(d - timedelta(days=1))


def plan_for(d: date, weekdays: Optional[Iterable[int]] = None) -> DayPlan:
    """The day's series chain (before the class switch and the shipped set are applied), on the calendar
    of `weekdays` — by default the setting read NOW (MARKETING_POST_WEEKDAYS: a day it leaves out rests).

    Mon: CEO buys → insider buys → the THEME post (owner 2026-11-20: a quiet Form 4 week posts a theme) → Money Map →
         lesson.
    Tue: [Congress Count on its Tuesday] + [13F Season while in season] + company stakes → Money Map → lesson.
    Wed: the THEME post (news-v23 phase 3, owner 2026-11-19: weekly theme posts) → [earnings while in season] →
         Money Map → lesson.
    Thu, Fri: [earnings while in season — `earnings_leads`] + Money Map → lesson (Fri posts only when the setting
         lists it).
    Sat: the lesson — the week's one lesson-only day.
    Sun: Money Map → lesson; never earnings (it leads Thu/Fri only).
    The theme explainer LEADS on Wednesday (THEME_WEEKDAY) and is Monday's fallback after the insider series (owner
    2026-11-20); the spotlight lane's chains hold it too (`spotlight_plan_for`). The chains stay as they are: the build
    caps the BACKUPS at THEME_FALLBACKS_PER_WEEK a week across both lanes (owner 2026-11-27)."""
    if not is_posting_day(d, weekdays):
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
    if weekday == THEME_WEEKDAY:
        if in_earnings_season(d):
            return DayPlan(False, ("theme_explainer", "earnings") + _MONEY_MAP_TAIL, "wednesday_earnings")
        return DayPlan(False, ("theme_explainer",) + _MONEY_MAP_TAIL, "wednesday_off")
    if weekday in _EARNINGS_DAYS:
        name = _EARNINGS_DAYS[weekday]
        if earnings_leads(d, weekdays):
            return DayPlan(False, ("earnings",) + _MONEY_MAP_TAIL, f"{name}_earnings")
        return DayPlan(False, _MONEY_MAP_TAIL, f"{name}_off")
    if weekday == 5:
        return DayPlan(False, (LESSON,), "saturday")
    if weekday == 6:
        return DayPlan(False, _MONEY_MAP_TAIL, "sunday")
    # The guard: a posting weekday with no series calendar (unreachable while every weekday has a branch above).
    logger.warning("marketing selection: posting weekday %d has no series calendar — lesson only", weekday)
    return DayPlan(False, (LESSON,), "lesson_only")


def enabled_chain(chain: Iterable[str], classes: FrozenSet[str],
                  shipped: Optional[FrozenSet[str]] = None) -> Tuple[str, ...]:
    """`chain` with every series dropped whose class is not in `classes`, that has not shipped
    (`shipped`, default SHIPPED_SERIES read at call time; script_service passes the per-series
    switch, `parse_news_series(settings.MARKETING_NEWS_SERIES)`), or that is unknown; the TERMINALS —
    LESSON (a main day) and SKIP (a spotlight day) — are kept whatever `classes` says, duplicates are
    removed in order and nothing after the first terminal is kept (it could never run — so a LESSON can
    never follow a SKIP). With classes {"A"} a main plan's chain is `(LESSON,)`, a spotlight plan's `(SKIP,)`."""
    if shipped is None:
        shipped = SHIPPED_SERIES
    out: List[str] = []
    for step in chain:
        if step in CHAIN_TERMINALS:
            out.append(step)
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


# ── The spotlight lane (owner decision 2026-11-15: "Add a second daily post" on "Tue, Thu, Sat") ─────────
# A second run on a spotlight day, claimed as its own row (migration 210: UNIQUE (run_date, lane)). Every
# setting below is read at CALL time; while MARKETING_SPOTLIGHT_ENABLED is off nothing here is read past the
# switch and nothing is logged, so the main lane is byte for byte today's.

#: The spotlight chains (`spotlight_plan_for`). Money Map is the one main-lane series a spotlight day may
#: take: its ledger key is per (symbol, fiscal year), so the two lanes can never post the same one; the
#: cross-lane exclusion (script_service) keeps them apart on the same company. Every chain ends in SKIP. Since C0
#: (2026-11-16) the run walks `spotlight_chain`: Money Map only on a day the MAIN lane's chain does not hold it.
#: Owner decisions 2026-11-20 (the quiet-week fallback): the THEME post right after the insider list — before the 13F
#: spotlight (in season), Money Map and the SKIP; the 13F-lead day keeps the 13F spotlight first.
_SPOTLIGHT_13F_LEAD: Tuple[str, ...] = ("thirteen_f_spotlight", "insider_spotlight", "theme_explainer", "money_map",
                                        SKIP)
_SPOTLIGHT_IN_SEASON: Tuple[str, ...] = ("insider_spotlight", "theme_explainer", "thirteen_f_spotlight", "money_map",
                                         SKIP)
_SPOTLIGHT_OFF_SEASON: Tuple[str, ...] = ("insider_spotlight", "theme_explainer", "money_map", SKIP)

#: A bounded log value: a pasted blob can never flood the log.
_LOG_CAP = 300


def spotlight_on() -> bool:
    """The spotlight lane's master switch NOW: True only when MARKETING_SPOTLIGHT_ENABLED is exactly True
    (a pydantic bool in production; anything else — a patched string, None — is off). Read at call time."""
    return settings.MARKETING_SPOTLIGHT_ENABLED is True


def parse_spotlight_weekdays(raw: Any) -> Tuple[int, ...]:
    """The spotlight days a MARKETING_SPOTLIGHT_WEEKDAYS value names, as sorted `date.weekday()` numbers
    (0 = Monday). Pure, never raises — and FAIL-CLOSED, unlike `parse_post_weekdays`: it may return () (no
    spotlight day, the lane closed) and never falls back to a default.

    The same three-letter tokens (WEEKDAY_TOKENS), comma-separated; each stripped and lower-cased, empty
    tokens skipped, duplicates collapsed. An unknown token is DROPPED (ERROR); a value with tokens but no known
    day left is () (ERROR); a blank value is () silently (a deliberate "no day" — `spotlight_lane_open` still
    names it at ERROR while the switch is on); a non-string is () (ERROR). Memoised on the raw string, so a bad
    live value logs once; every logged token is bounded."""
    if not isinstance(raw, str):
        logger.error("MARKETING_SPOTLIGHT_WEEKDAYS: not a string (%s) — no spotlight day, the lane is closed",
                     type(raw).__name__)
        return ()
    return _parse_spotlight_weekdays_text(raw)


@functools.lru_cache(maxsize=16)
def _parse_spotlight_weekdays_text(raw: str) -> Tuple[int, ...]:
    """`parse_spotlight_weekdays` for a string: cached, so its logging happens once per distinct value."""
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    unknown = sorted(tokens - set(WEEKDAY_TOKENS))
    if unknown:
        logger.error("MARKETING_SPOTLIGHT_WEEKDAYS: ignored unknown day(s) %s — the tokens are %s",
                     ",".join(t[:24] for t in unknown[:10]), ",".join(WEEKDAY_TOKENS))
    days = tuple(i for i, token in enumerate(WEEKDAY_TOKENS) if token in tokens)
    if not days and tokens:
        logger.error("MARKETING_SPOTLIGHT_WEEKDAYS: no known day left — no spotlight day, the lane is closed")
    return days


def spotlight_weekdays() -> Tuple[int, ...]:
    """The spotlight days NOW: the web setting MARKETING_SPOTLIGHT_WEEKDAYS read from the settings object at
    CALL time and parsed by `parse_spotlight_weekdays` — possibly () (no spotlight day). The switch is not
    consulted here; `spotlight_lane_open` / `is_spotlight_day` combine the two."""
    return parse_spotlight_weekdays(settings.MARKETING_SPOTLIGHT_WEEKDAYS)


def spotlight_hours_problem(spotlight_hour: Any, max_attempts: Any, run_hour: Any) -> Optional[str]:
    """Why the spotlight lane's hours cannot run beside the main lane's, or None. Pure, never raises.

    The rule: `spotlight_hour + max_attempts <= run_hour` (MARKETING_SPOTLIGHT_HOUR_ET,
    MARKETING_MAX_RUN_ATTEMPTS, MARKETING_RUN_HOUR_ET; 10 + 6 <= 16 with the defaults). The worker ticks hourly
    at :15 and a run gets at most `max_attempts` claims, one per tick from its lane's hour, each ending inside
    its hour (MARKETING_RUN_STALE_SECONDS is below the cron period) — so the spotlight's last attempt has ended
    by `spotlight_hour + max_attempts` o'clock, before the main run's first tick. That keeps the two lanes'
    builds apart (each reads the other's same-day ledger rows to exclude them) and lets the run-health checks,
    timed from the main hour, judge both lanes. Every value must be a plain int (never a bool), each hour
    0-23, and at least one attempt."""
    named = (("MARKETING_SPOTLIGHT_HOUR_ET", spotlight_hour), ("MARKETING_MAX_RUN_ATTEMPTS", max_attempts),
             ("MARKETING_RUN_HOUR_ET", run_hour))
    for name, value in named:
        if type(value) is not int:
            return f"{name} is not an integer ({type(value).__name__})"
    if not (0 <= spotlight_hour <= 23 and 0 <= run_hour <= 23):
        return (f"an hour outside 0-23 (MARKETING_SPOTLIGHT_HOUR_ET {spotlight_hour}, "
                f"MARKETING_RUN_HOUR_ET {run_hour})")
    if max_attempts < 1:
        return f"MARKETING_MAX_RUN_ATTEMPTS {max_attempts} is below 1"
    if spotlight_hour + max_attempts > run_hour:
        latest = run_hour - max_attempts
        fix = (f"set the spotlight hour to {latest} or earlier, on the web and the worker together" if latest >= 0
               else "no spotlight hour fits before the main run with this many attempts")
        return (f"MARKETING_SPOTLIGHT_HOUR_ET {spotlight_hour} + MARKETING_MAX_RUN_ATTEMPTS {max_attempts} = "
                f"{spotlight_hour + max_attempts} is after MARKETING_RUN_HOUR_ET {run_hour} — the spotlight's last "
                f"attempts would run into the main run's ({fix})")
    return None


@functools.lru_cache(maxsize=32)
def _log_lane_closed(problem: str) -> None:
    """ERROR once per process per distinct reason (memoised): `spotlight_lane_open` runs on every kick, claim and
    ledger-read sizing, and a standing misconfiguration must be loud without flooding the log."""
    logger.error("marketing spotlight lane CLOSED although MARKETING_SPOTLIGHT_ENABLED is on: %s", problem[:_LOG_CAP])


def spotlight_lane_open() -> bool:
    """The spotlight lane may run NOW. Read at call time, never raises. Open only when ALL hold:

    * MARKETING_SPOTLIGHT_ENABLED is on (`spotlight_on`) — off, nothing else is read and nothing is logged;
    * MARKETING_SPOTLIGHT_WEEKDAYS names at least one day (`spotlight_weekdays`);
    * the hour rule holds (`spotlight_hours_problem`: spotlight hour + MARKETING_MAX_RUN_ATTEMPTS <=
      MARKETING_RUN_HOUR_ET);
    * MARKETING_CONTENT_CLASSES holds a news class (C or F) — every spotlight series is one, so with the
      lesson alone the day could only ever skip.

    A lane closed while its switch is on is a misconfiguration: logged at ERROR once per distinct reason. The
    claim gate answers `not_scheduled` on it, so the lane never claims a day it cannot post."""
    if not spotlight_on():
        return False
    if not spotlight_weekdays():
        problem: Optional[str] = "MARKETING_SPOTLIGHT_WEEKDAYS names no day"
    else:
        problem = spotlight_hours_problem(settings.MARKETING_SPOTLIGHT_HOUR_ET, settings.MARKETING_MAX_RUN_ATTEMPTS,
                                          settings.MARKETING_RUN_HOUR_ET)
    if problem is None and not parse_content_classes(settings.MARKETING_CONTENT_CLASSES) & frozenset(NEWS_CLASSES):
        problem = ("MARKETING_CONTENT_CLASSES holds no news class (C or F) — every spotlight series is one, so the "
                   "day could only skip")
    if problem is not None:
        _log_lane_closed(problem)
        return False
    return True


#: Why `spotlight_day_problem` says a date is not a spotlight day (log words, never public copy).
SPOTLIGHT_LANE_CLOSED = "lane_closed"
SPOTLIGHT_WEEKDAY_NOT_LISTED = "weekday_not_listed"
SPOTLIGHT_NO_SERIES = "no_spotlight_series"


def spotlight_day_problem(run_date: date) -> Optional[str]:
    """Why the spotlight lane does NOT run on `run_date`, or None when it does — THE one predicate the claim gate
    (run_service), the kick (script_service) and the run-health checks / digest (digest_service) share, through
    `is_spotlight_day`, so they always agree. Read at call time — like `is_posting_day`, a change of a setting
    re-judges every date, past ones included. Never raises.

    * SPOTLIGHT_LANE_CLOSED — the lane is closed NOW (`spotlight_lane_open`: its switch, a weekday listed, the hour
      rule, a news class);
    * SPOTLIGHT_WEEKDAY_NOT_LISTED — the date's weekday is not one of MARKETING_SPOTLIGHT_WEEKDAYS;
    * SPOTLIGHT_NO_SERIES — its chain (`spotlight_chain_now`: the calendar, the class and per-series switches, and
      the C0 fix of 2026-11-16 — no series the MAIN lane's chain holds that day) has no series left, only its SKIP.
      Such a day could only ever skip, so the lane is not scheduled at all: no run row, no alert, no expected run."""
    if not spotlight_lane_open():
        return SPOTLIGHT_LANE_CLOSED
    if run_date.weekday() not in spotlight_weekdays():
        return SPOTLIGHT_WEEKDAY_NOT_LISTED
    if not spotlight_chain_now(run_date).has_series:
        return SPOTLIGHT_NO_SERIES
    return None


def is_spotlight_day(run_date: date) -> bool:
    """The spotlight lane runs on `run_date`: `spotlight_day_problem` has nothing against it — the lane is open
    NOW, the date's weekday is one of MARKETING_SPOTLIGHT_WEEKDAYS, and its chain holds a series once the main
    lane's are dropped (`spotlight_chain_now`). An empty weekday set is no spotlight day. Read at call time."""
    return spotlight_day_problem(run_date) is None


def _spotlight_calendar(weekdays: Optional[Iterable[int]]) -> Tuple[int, ...]:
    """The spotlight days a calendar call works on: `weekdays` as sorted, distinct `date.weekday()` numbers — or,
    when None, the setting NOW (`spotlight_weekdays()`, possibly ()). An explicit value that is not weekday
    numbers is a caller's bug: ValueError, never a silently different calendar. An explicit EMPTY set is allowed
    (no spotlight day), unlike the posting calendar."""
    if weekdays is None:
        return spotlight_weekdays()
    days = set(weekdays)
    if not all(type(d) is int and 0 <= d <= 6 for d in days):
        raise ValueError(f"spotlight weekdays must be date.weekday() numbers 0..6, not {sorted(map(repr, days))[:7]}")
    return tuple(sorted(days))


def spotlight_plan_for(d: date, weekdays: Optional[Iterable[int]] = None) -> DayPlan:
    """The spotlight day's series chain (before the class switch and the per-series switch MARKETING_NEWS_SERIES
    are applied by `enabled_chain`), on the calendar of `weekdays` — by default MARKETING_SPOTLIGHT_WEEKDAYS read
    NOW (an empty set: every day rests). A pure CALENDAR: whether the lane is open (its switch, its hours, the
    classes) is the caller's — `is_spotlight_day`, the claim gate, the kick.

    In the 13F season (`thirteen_f_season`), on the week's LAST spotlight day (the latest listed weekday, the
    week running Monday to Sunday): 13F spotlight → insider list → theme → Money Map → SKIP ("spotlight_13f_lead").
    Every other spotlight day: insider list → theme → [13F spotlight, in season] → Money Map → SKIP ("spotlight_13f"
    in season, "spotlight" off it). The theme (owner decisions 2026-11-20: a quiet Form 4 window posts a theme) is
    dropped like any series the main lane's chain holds that day (`spotlight_chain`: a Monday or a Wednesday).

    Never the lesson — the main lane posts the week's, so a spotlight day with nothing to post SKIPS — and never
    the weekly Form 4 roundups (WEEKLY_ROUNDUP_SERIES): their window and ledger key roll with the run date, so a
    spotlight day would re-post Monday's roundup under a new key. The chain a run WALKS is `spotlight_chain`'s: this
    one without the switched-off series and without any series the main lane's chain holds that day (C0)."""
    days = _spotlight_calendar(weekdays)
    weekday = d.weekday()
    if weekday not in days:
        return DayPlan(rest_day=True)
    if thirteen_f_season(d) is None:
        return DayPlan(False, _SPOTLIGHT_OFF_SEASON, "spotlight")
    if weekday == days[-1]:
        return DayPlan(False, _SPOTLIGHT_13F_LEAD, "spotlight_13f_lead")
    return DayPlan(False, _SPOTLIGHT_IN_SEASON, "spotlight_13f")


# ── C0 (2026-11-16): the spotlight never competes for a series the MAIN lane would use that day ─────────────
# Rule (c) of the cross-lane exclusion (script_service) let a spotlight Money Map push the main lane's same-day
# Money Map — on an off-season Thursday its only series — into the LESSON: a second lesson that week, against the
# owner's "One Learn lesson a week" (marketing.md §1). So the spotlight's chain is narrowed BEFORE it runs: every
# series the main lane's enabled chain holds that day leaves it (`spotlight_chain`), and a spotlight day left with no
# series is no spotlight day at all (`spotlight_day_problem`). Rule (c) stays as the guard for anything else.


@dataclass(frozen=True)
class SpotlightChain:
    #: The spotlight calendar's plan (`spotlight_plan_for`); a rest day carries no chain.
    plan: DayPlan
    #: The chain the spotlight run walks: the plan's, narrowed by the class switch and the per-series switch
    #: (`enabled_chain`) and without the main lane's series — it ends in SKIP; () on a rest day.
    chain: Tuple[str, ...]
    #: The spotlight series left out because the MAIN lane's enabled chain that day holds them, in chain order.
    main_lane: Tuple[str, ...] = ()

    @property
    def has_series(self) -> bool:
        """The chain holds at least one series (not only its terminal)."""
        return any(s not in CHAIN_TERMINALS for s in self.chain)


def main_lane_series(d: date, classes: FrozenSet[str], news_series: FrozenSet[str]) -> FrozenSet[str]:
    """The series (never a terminal) the MAIN lane's chain on `d` holds once the class switch and the per-series
    switch are applied — `plan_for(d)` through `enabled_chain`, the narrowing `script_service._select` applies
    (before its worker-capability drops, which no calendar can know). Empty on a main rest day. Pure but for the
    posting days (MARKETING_POST_WEEKDAYS), read at call time like `plan_for`."""
    plan = plan_for(d)
    if plan.rest_day:
        return frozenset()
    return frozenset(s for s in enabled_chain(plan.chain, classes, shipped=news_series) if s not in CHAIN_TERMINALS)


def spotlight_chain(d: date, *, classes: FrozenSet[str], news_series: FrozenSet[str],
                    weekdays: Optional[Iterable[int]] = None) -> SpotlightChain:
    """The spotlight day's chain (C0, 2026-11-16): `spotlight_plan_for(d, weekdays)` narrowed by `enabled_chain` to
    `classes` and `news_series` (the same switches as the main lane), then WITHOUT every series the main lane's
    enabled chain holds that day (`main_lane_series`) — the spotlight never competes for one. Money Map is the only
    series both calendars name: it stays on a spotlight day only when the main lane would not reach it (Saturday's
    lesson-only main day, a main rest day, or a main chain the switches empty of it). The terminal SKIP always stays.
    Pure but for the calendars' settings (the posting days; the spotlight days when `weekdays` is None)."""
    plan = spotlight_plan_for(d, weekdays)
    if plan.rest_day:
        return SpotlightChain(plan, ())
    chain = enabled_chain(plan.chain, classes, shipped=news_series)
    main = main_lane_series(d, classes, news_series)
    dropped = tuple(s for s in chain if s not in CHAIN_TERMINALS and s in main)
    return SpotlightChain(plan, tuple(s for s in chain if s not in dropped), dropped)


@functools.lru_cache(maxsize=16)
def _news_series_once(raw: str, shipped: FrozenSet[str]) -> FrozenSet[str]:
    """`parse_news_series` for the PREDICATE's reads (`spotlight_chain_now`), memoised on the value and the shipped set
    read at call time — the claim gate, the digest and the run-health checks ask it for many dates, and a bad
    MARKETING_NEWS_SERIES token must not log once per date (the kick's own read, `script_service`, logs every kick
    as before)."""
    return parse_news_series(raw)


def spotlight_chain_now(d: date) -> SpotlightChain:
    """`spotlight_chain` on the settings in force NOW: MARKETING_CONTENT_CLASSES and MARKETING_NEWS_SERIES (the latter
    read only while a news class is on, exactly as the kick reads it), the posting days and the spotlight days.
    Never raises."""
    classes = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
    raw = settings.MARKETING_NEWS_SERIES
    if classes == frozenset({"A"}):
        news_series: FrozenSet[str] = frozenset()
    elif isinstance(raw, str):
        news_series = _news_series_once(raw, SHIPPED_SERIES)
    else:
        news_series = parse_news_series(raw)
    return spotlight_chain(d, classes=classes, news_series=news_series)


# ── Replacement runs (migration 215, owner decision 2026-11-27: "Full replacement … Max 2 replacements a day. Money
# Map posts only … off until you switch it on") ─────────────────────────────────────────────────────────────────────


#: The `DayPlan.reason` of a replacement run's selection (`script_service._select_replacement`): the run builds
#: `replacement_chain` — never the day's calendar (`plan_for` / `spotlight_plan_for`), never the lesson.
REPLACEMENT_PLAN_REASON = "replacement"


def replacement_on() -> bool:
    """The "🔄 Next company" master switch NOW: True only when MARKETING_REPLACE_ENABLED is exactly True (a pydantic bool
    in production; anything else — a patched string, None — is off). Read at call time by the review bot's offer and
    tap, the claim of a replacement that has not started (`run_service.replacement_claim_problem`), the web's expiry
    step and `run_service.create_posts` of a replacement run. Restoring the original ("↩ Post {SYM} after all") never
    reads it."""
    return settings.MARKETING_REPLACE_ENABLED is True


def replacement_chain(classes: FrozenSet[str], news_series: FrozenSet[str]) -> Tuple[str, ...]:
    """A replacement run's chain: the replaceable series (Money Map) narrowed by `enabled_chain` to `classes` and the
    per-series switch `news_series`, then SKIP — ``("money_map", SKIP)``, or ``(SKIP,)`` when Money Map is switched off
    (class F off, or money_map not in MARKETING_NEWS_SERIES ∩ the shipped series). Never the lesson and never the day's
    calendar: a replacement only ever posts Money Map, and finds nothing rather than falling to a lesson. Pure."""
    return enabled_chain(REPLACEABLE_SERIES + (SKIP,), classes, shipped=news_series)


def replacement_chain_now() -> Tuple[str, ...]:
    """`replacement_chain` on the switches in force NOW (MARKETING_CONTENT_CLASSES, and MARKETING_NEWS_SERIES read only
    while a news class is on, exactly as the kick reads it). ``(SKIP,)`` means a replacement could never post: the
    tap is refused before the request (`run_service.request_replacement`, outcome `money_map_off`). Never raises."""
    classes = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
    news_series = frozenset() if classes == frozenset({"A"}) else parse_news_series(settings.MARKETING_NEWS_SERIES)
    return replacement_chain(classes, news_series)
