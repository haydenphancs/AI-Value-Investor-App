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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional, Sequence

from app.services.daily_rotation import pick_for_day

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
        "Open with a common misconception the source corrects, then walk through what the "
        "source actually shows. Cards: the myth, then the facts that answer it.",
        frozenset({"money_moves", "journey"}),
    ),
    Template(
        "three_takeaways", "Three takeaways",
        "Distil the source into exactly three lessons a reader can reuse. Cards: one lesson "
        "each, plus an opening card.",
        frozenset({"money_moves", "journey"}),
    ),
    Template(
        "case_story", "Case story",
        "Tell the business history as a short story with a beginning, a turning point and the "
        "lesson it teaches about how businesses win or lose. Past tense for every dated fact.",
        frozenset({"money_moves"}),
    ),
    Template(
        "question_hook", "Question hook",
        "Open with one question a curious beginner would ask, answer it step by step from the "
        "source, and end on the principle, never on a verdict about any company.",
        frozenset({"money_moves", "journey"}),
    ),
    Template(
        "checklist", "Checklist",
        "Turn the source into a short checklist of things to understand or look for. It is a "
        "learning checklist, never a list of what to buy or sell.",
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


def choose_template(kind: str, ordinal: int) -> str:
    eligible = [t.id for t in TEMPLATES if kind in t.kinds]
    return _pick(eligible, ordinal, TEMPLATE_SALT) or eligible[0]


def choose(pool: Iterable[str], run_date: date, recent: Iterable[str] = ()) -> Selection:
    """The selection for one ET run date. Pure; `recent` is supplied by the caller."""
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
        rest_day=False, source_ref=key, template_id=choose_template(kind, ordinal),
        posting_ordinal=ordinal,
    )
