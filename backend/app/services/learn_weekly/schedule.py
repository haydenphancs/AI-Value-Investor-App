"""When each Wiser weekly piece happens, in America/New_York (plan §1.3-§1.4, §1.10).

Pure and deterministic: no I/O, no clock read (every function takes `now` or a date), and the
`LEARN_*` hours are read from `settings` INSIDE each function, at call time, so the settings object
in force (a redeploy's, a test's monkeypatch) always decides — nothing derived from them is frozen
at import. Every datetime this module takes is timezone-aware (any zone; a naive one raises,
because a naive "23:00" is exactly the ambiguity this module exists to remove), and every instant it
RETURNS is in UTC — calendar dates are ET, instants are UTC. Why not ET: two aware datetimes that
share a tzinfo compare and subtract by WALL time, ignoring `fold` and the offset (so an ET
`hold_until - now` is wall-clock arithmetic, and 01:30 EDT sorts after 01:00 EST), and PEP 495
makes `==` between an ambiguous-hour ET instant and a UTC one False even when they are the same
instant. UTC instants have neither trap; display them with `.astimezone(ET)`.

**Feature Deep Dive** (`DeepDiveTimes`, one per ISO week, keyed by its FRIDAY's ISO week):

    Thu LEARN_TOPIC_HOUR_ET:00          topic card (top 3, buttons 1 / 2 / 3)
    Fri 07:00                           no tap → #1 (AUTO_PICK_HOUR_ET)
    Fri LEARN_DEEP_DIVE_BUILD_HOUR_ET   build: fact sheet → writer → narration job
    Fri 15:00                           deadman: "no Deep Dive in review yet" (DEADMAN_HOUR_ET)
    Fri LEARN_DEEP_DIVE_PUBLISH_HOUR_ET publish if approved
    publish + LEARN_REVIEW_HOLD_HOURS   hold ends → expired

**Category slot** (`SlotTimes`, keyed by its ET publish date, on LEARN_SLOT_WEEKDAYS): publish at
LEARN_SLOT_PUBLISH_HOUR_ET, build LEARN_SLOT_BUILD_LEAD_HOURS before it, hold like the Deep Dive.

DST: a WALL-CLOCK instant ("Fri 23:00 ET") is built on the ET calendar, so it lands on 23:00 local
on both sides of a change; a DURATION (the review hold, the slot's build lead) is added in UTC, so
it is exactly that many hours long — across the 2026-11-01 fall-back the 48 h hold of a Fri 23:00
EDT publish ends Sun 22:00 EST, not 23:00. Durations are budgets (the catch-up cut-off and the
narrator's render time are absolute), which is why they are never wall-clock arithmetic: Python's
aware `datetime + timedelta` IS wall-clock arithmetic, and would make a hold 47 or 49 h long twice a
year. A wall time inside a gap (spring-forward 02:xx) resolves with fold=0 to the instant after the
gap (02:30 → 03:30 EDT); an ambiguous one (fall-back 01:xx) to its FIRST occurrence (EDT). Neither
happens at the default hours (Thu/Fri events; 05:00 slots).

Holiday Fridays (2026-12-25, 2027-01-01) are computed like any other Friday: publish-or-skip is an
open owner decision (plan, "Open owner decisions") and belongs to the scheduler, not to this maths.

`sort_order` (plan §1.10): a Deep Dive is `-(iso_year*100 + iso_week)` — always negative, newer is
lower, and ISO week 53 → next week 01 stays monotonic (-202653 > -202701); a slot piece is
`100000 + days since 2026-01-01` — above the bundle's 0..N-1, newer is higher.
"""

from __future__ import annotations

import functools
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, List, Optional, Tuple, Union

from app.config import Settings, settings
from app.services.learn_weekly import contracts
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

_UTC = timezone.utc

# ── Fixed Friday hours (plan §1.4) — not settings: nothing in the plan asks to move them ─────
AUTO_PICK_HOUR_ET = 7       # no owner tap on the topic card by now → the #1 topic
DEADMAN_HOUR_ET = 15        # "No Deep Dive in review yet" alert

# ── LEARN_SLOT_WEEKDAYS (a copy of `marketing.selection.parse_post_weekdays`' semantics) ──────
#: Tokens indexed by `date.weekday()` (0 = Monday). Pinned equal to marketing's WEEKDAY_TOKENS by
#: tests/test_learn_weekly_schedule.py — the two settings are documented as the same vocabulary.
WEEKDAY_TOKENS: Tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: Monday only: the plan's first category slot (Mon 05:00 ET) and config's declared default.
DEFAULT_SLOT_WEEKDAYS: Tuple[int, ...] = (0,)

# ── Hour settings: the bounds config's `Field(ge=…, le=…)` declares, pinned equal by a test ────
#: A value outside them can only arrive by assignment (pydantic validates at load, not on
#: `setattr`), so it falls back to config's DEFAULT with an ERROR instead of building a schedule
#: on, say, `True` (bool is an int) or 30 o'clock.
_SETTING_BOUNDS = {
    "LEARN_TOPIC_HOUR_ET": (0, 23),
    "LEARN_DEEP_DIVE_BUILD_HOUR_ET": (0, 23),
    "LEARN_DEEP_DIVE_PUBLISH_HOUR_ET": (0, 23),
    "LEARN_SLOT_PUBLISH_HOUR_ET": (0, 23),
    "LEARN_SLOT_BUILD_LEAD_HOURS": (6, 96),
    "LEARN_REVIEW_HOLD_HOURS": (1, 72),
}

# How far around today `due_slots` looks for publish dates. A slot is due from publish − lead
# (≤ 96 h = 4 days ahead) to publish + hold (≤ 72 h = 3 days behind); one spare day each way covers
# the ET-date rounding. A test pins these against the bounds above.
_SLOT_SEARCH_DAYS_AHEAD = 8
_SLOT_SEARCH_DAYS_BEHIND = 4

# `slot_sort_order`'s epoch and base (plan §1.10).
SLOT_SORT_EPOCH = date(2026, 1, 1)
SLOT_SORT_BASE = 100000

# `catch_up_cutoff` treats a word count above this as junk-sized (a timedelta of that many render
# seconds would overflow long before it means anything) and clamps it, WARNING.
_MAX_CUTOFF_WORDS = 100_000


# ── Settings readers ──────────────────────────────────────────────────────────────────────────


def _int_setting(name: str) -> int:
    """`settings.<name>` at call time, or config's declared default (ERROR, once per value) when it
    is not a real int inside config's own bounds."""
    raw = getattr(settings, name, None)
    lo, hi = _SETTING_BOUNDS[name]
    if isinstance(raw, int) and not isinstance(raw, bool) and lo <= raw <= hi:
        return raw
    default = Settings.model_fields[name].default
    _log_bad_setting(name, type(raw).__name__, repr(raw)[:40], lo, hi, default)
    return default


@functools.lru_cache(maxsize=64)
def _log_bad_setting(name: str, type_name: str, shown: str, lo: int, hi: int, default: Any) -> None:
    """Cached on its arguments, so a bad live value logs once per process, not on every 5-min tick."""
    logger.error("learn_weekly.schedule: %s=%s (%s) is not an int in [%d, %d] — using the default %s",
                 name, shown, type_name, lo, hi, default)


def parse_slot_weekdays(raw: Any) -> Tuple[int, ...]:
    """The category-slot publish days a LEARN_SLOT_WEEKDAYS value names, as sorted
    `date.weekday()` numbers (0 = Monday). Pure, never raises, never empty.

    Same rules as `marketing.selection.parse_post_weekdays`: three-letter tokens only
    (WEEKDAY_TOKENS), comma-separated, each stripped and lower-cased, empty tokens skipped,
    duplicates collapsed. An unknown token is DROPPED (ERROR); an empty value, one with no known day
    left, or a non-string is DEFAULT_SLOT_WEEKDAYS (ERROR) — a typo can never silently turn the slot
    off (that is LEARN_SLOT_ENABLED's job). Memoised on the raw string, so a bad live value logs
    once, not on every read; every logged token is bounded."""
    if not isinstance(raw, str):
        logger.error("LEARN_SLOT_WEEKDAYS: not a string (%s) — using the default %s",
                     type(raw).__name__, _weekday_text(DEFAULT_SLOT_WEEKDAYS))
        return DEFAULT_SLOT_WEEKDAYS
    return _parse_slot_weekdays_text(raw)


@functools.lru_cache(maxsize=16)
def _parse_slot_weekdays_text(raw: str) -> Tuple[int, ...]:
    """`parse_slot_weekdays` for a string: cached, so its logging happens once per distinct value."""
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    unknown = sorted(tokens - set(WEEKDAY_TOKENS))
    if unknown:
        logger.error("LEARN_SLOT_WEEKDAYS: ignored unknown day(s) %s — the tokens are %s",
                     ",".join(t[:24] for t in unknown[:10]), ",".join(WEEKDAY_TOKENS))
    days = tuple(i for i, token in enumerate(WEEKDAY_TOKENS) if token in tokens)
    if not days:
        logger.error("LEARN_SLOT_WEEKDAYS: no slot day left — using the default %s",
                     _weekday_text(DEFAULT_SLOT_WEEKDAYS))
        return DEFAULT_SLOT_WEEKDAYS
    return days


def _weekday_text(days: Iterable[int]) -> str:
    return ",".join(WEEKDAY_TOKENS[d] for d in days)


def slot_weekdays() -> Tuple[int, ...]:
    """The slot days NOW: LEARN_SLOT_WEEKDAYS read at call time, parsed by `parse_slot_weekdays`."""
    return parse_slot_weekdays(settings.LEARN_SLOT_WEEKDAYS)


# ── Instant helpers ───────────────────────────────────────────────────────────────────────────


def _require_aware(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a timezone-aware datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got the naive {value.isoformat()}")
    return value


def _require_date(value: Any, name: str) -> date:
    # `datetime` IS a `date`: refused, because its date part is the date of whatever zone it is
    # in (a UTC 03:00 Saturday is an ET Friday) — pass `now` to the instant-taking functions.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{name} must be a date (not a datetime), got {type(value).__name__}")
    return value


def _et_wall(d: date, hour: int) -> datetime:
    """The instant (UTC) of `hour`:00 on the ET calendar date `d` (a gap time moves past the gap;
    an ambiguous one is its first occurrence — fold=0)."""
    return datetime.combine(d, time(hour), tzinfo=ET).astimezone(_UTC)


def _plus_hours(instant: datetime, hours: float) -> datetime:
    """`instant` + `hours` of ELAPSED time, in UTC."""
    return instant.astimezone(_UTC) + timedelta(hours=hours)


def et_date(now: datetime) -> date:
    """The America/New_York calendar date of an aware instant."""
    return _require_aware(now, "now").astimezone(ET).date()


# ── Feature Deep Dive ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeepDiveTimes:
    """Every instant of one week's Deep Dive (aware, UTC — see the module docstring). `period_key`
    and the ISO fields come from the FRIDAY (`contracts.deep_dive_period_key`); `hold_until` =
    `publish_at` + the review hold in elapsed hours."""

    friday: date
    period_key: str
    iso_year: int
    iso_week: int
    topic_at: datetime
    auto_pick_at: datetime
    build_at: datetime
    deadman_at: datetime
    publish_at: datetime
    hold_until: datetime

    @property
    def sort_order(self) -> int:
        return deep_dive_sort_order(self.iso_year, self.iso_week)

    def window_contains(self, now: datetime) -> bool:
        """`topic_at ≤ now ≤ hold_until` (both ends inclusive: at exactly `hold_until` the piece is
        still this week's, and the ledger's `now ≥ hold_until` expiry decides what it becomes)."""
        return self.topic_at <= _require_aware(now, "now").astimezone(_UTC) <= self.hold_until


def deep_dive_times_for(d: date) -> DeepDiveTimes:
    """The Deep Dive of the ISO week containing the ET calendar date `d` (Mon..Sun)."""
    d = _require_date(d, "d")
    iso = d.isocalendar()
    friday = date.fromisocalendar(iso[0], iso[1], 5)
    thursday = friday - timedelta(days=1)
    f_iso = friday.isocalendar()   # == iso's year/week by construction; read from the Friday anyway

    topic_hour = _int_setting("LEARN_TOPIC_HOUR_ET")
    build_hour = _int_setting("LEARN_DEEP_DIVE_BUILD_HOUR_ET")
    publish_hour = _int_setting("LEARN_DEEP_DIVE_PUBLISH_HOUR_ET")
    hold_hours = _int_setting("LEARN_REVIEW_HOLD_HOURS")
    _warn_if_out_of_order(topic_hour, build_hour, publish_hour)

    publish_at = _et_wall(friday, publish_hour)
    return DeepDiveTimes(
        friday=friday,
        period_key=contracts.deep_dive_period_key(f_iso[0], f_iso[1]),
        iso_year=f_iso[0],
        iso_week=f_iso[1],
        topic_at=_et_wall(thursday, topic_hour),
        auto_pick_at=_et_wall(friday, AUTO_PICK_HOUR_ET),
        build_at=_et_wall(friday, build_hour),
        deadman_at=_et_wall(friday, DEADMAN_HOUR_ET),
        publish_at=publish_at,
        hold_until=_plus_hours(publish_at, hold_hours),
    )


@functools.lru_cache(maxsize=16)
def _warn_if_out_of_order(topic_hour: int, build_hour: int, publish_hour: int) -> None:
    """The plan's Friday reads auto-pick (07) ≤ build < deadman (15) ≤ publish. Another order is
    computed as configured — never clamped, the times are what the owner set — but WARNED once per
    configuration: a build before the auto-pick has no topic yet, and a publish before the deadman
    makes its "nothing in review" alert arrive after the publish slot."""
    del topic_hour  # Thursday's hour (0..23) is always before every Friday instant.
    if not (AUTO_PICK_HOUR_ET <= build_hour < DEADMAN_HOUR_ET <= publish_hour):
        logger.warning(
            "learn_weekly.schedule: Friday order is auto-pick %02d:00, build %02d:00, deadman %02d:00, "
            "publish %02d:00 ET — expected auto-pick ≤ build < deadman ≤ publish (LEARN_DEEP_DIVE_BUILD_HOUR_ET "
            "/ LEARN_DEEP_DIVE_PUBLISH_HOUR_ET)", AUTO_PICK_HOUR_ET, build_hour, DEADMAN_HOUR_ET, publish_hour)


def current_deep_dive(now: datetime) -> Optional[DeepDiveTimes]:
    """The Deep Dive whose window [topic_at, hold_until] contains `now`, else None.

    A window runs from Thursday of its ISO week to at most Fri 23:00 + 72 h = Monday 23:00 of the
    NEXT ISO week (the bounds), so only this ET week's and last ET week's can contain `now` — and
    never both: the next Thursday is always later than that Monday."""
    d = et_date(now)
    for candidate in (deep_dive_times_for(d), deep_dive_times_for(d - timedelta(days=7))):
        if candidate.window_contains(now):
            return candidate
    return None


# ── Category slot ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotTimes:
    """One category slot (aware, UTC instants): publish at LEARN_SLOT_PUBLISH_HOUR_ET ET on
    `publish_date`, build LEARN_SLOT_BUILD_LEAD_HOURS (elapsed) before it, hold
    LEARN_REVIEW_HOLD_HOURS after."""

    publish_date: date
    period_key: str
    build_at: datetime
    publish_at: datetime
    hold_until: datetime

    @property
    def sort_order(self) -> int:
        return slot_sort_order(self.publish_date)

    def window_contains(self, now: datetime) -> bool:
        """`build_at ≤ now ≤ hold_until`, both ends inclusive (see DeepDiveTimes.window_contains)."""
        return self.build_at <= _require_aware(now, "now").astimezone(_UTC) <= self.hold_until


def slot_times_for(publish_date: date) -> SlotTimes:
    """The slot that publishes on the ET date `publish_date` — whatever its weekday: the period key
    is the identity, and LEARN_SLOT_WEEKDAYS only decides which dates `due_slots` offers."""
    publish_date = _require_date(publish_date, "publish_date")
    publish_at = _et_wall(publish_date, _int_setting("LEARN_SLOT_PUBLISH_HOUR_ET"))
    return SlotTimes(
        publish_date=publish_date,
        period_key=contracts.slot_period_key(publish_date),
        build_at=_plus_hours(publish_at, -_int_setting("LEARN_SLOT_BUILD_LEAD_HOURS")),
        publish_at=publish_at,
        hold_until=_plus_hours(publish_at, _int_setting("LEARN_REVIEW_HOLD_HOURS")),
    )


def due_slots(now: datetime, weekdays: Optional[Iterable[int]] = None) -> List[SlotTimes]:
    """Every slot whose window [build_at, hold_until] contains `now`, oldest publish first.

    `weekdays` = `date.weekday()` numbers; None reads LEARN_SLOT_WEEKDAYS now (`slot_weekdays()`).
    An entry that is not an int 0..6 (a bool included) is dropped with an ERROR; an explicitly empty
    collection means no slot day (the caller decided), so nothing is due. Two slots can be due at
    once (e.g. "mon,tue": Monday's hold overlaps Tuesday's build) — each is its own period."""
    _require_aware(now, "now")
    days = slot_weekdays() if weekdays is None else _clean_weekdays(weekdays)
    today = et_date(now)
    due: List[SlotTimes] = []
    for offset in range(-_SLOT_SEARCH_DAYS_BEHIND, _SLOT_SEARCH_DAYS_AHEAD + 1):
        d = today + timedelta(days=offset)
        if d.weekday() not in days:
            continue
        slot = slot_times_for(d)
        if slot.window_contains(now):
            due.append(slot)
    return due


def _clean_weekdays(weekdays: Iterable[int]) -> Tuple[int, ...]:
    if isinstance(weekdays, (str, bytes)):
        # A raw setting string passed by mistake: parse it like the setting instead of iterating
        # its characters (none of which is a weekday number).
        return parse_slot_weekdays(weekdays if isinstance(weekdays, str) else None)
    kept, junk = set(), []
    for value in weekdays:
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6:
            kept.add(value)
        else:
            junk.append(repr(value)[:24])
    if junk:
        logger.error("learn_weekly.schedule.due_slots: ignored weekday value(s) %s — expected ints 0..6",
                     ",".join(junk[:10]))
    return tuple(sorted(kept))


# ── Period keys back to times ─────────────────────────────────────────────────────────────────


def times_for_period_key(period_key: str) -> Union[DeepDiveTimes, SlotTimes]:
    """The times of a stored `learn_weekly_pieces.period_key`, at TODAY's settings (a row's own
    `scheduled_publish_at` / `hold_until` are frozen at build time and win over this). Raises
    ValueError on anything `contracts.is_period_key` refuses or that names no real week/date."""
    if not contracts.is_period_key(period_key):
        raise ValueError(f"not a learn_weekly period key: {str(period_key)[:40]!r}")
    if contracts.DEEP_DIVE_KEY_RE.fullmatch(period_key):
        iso_year, iso_week = int(period_key[0:4]), int(period_key[6:8])
        # fromisocalendar raises ValueError on W00, W54+ and a W53 its year does not have.
        return deep_dive_times_for(date.fromisocalendar(iso_year, iso_week, 5))
    return slot_times_for(date.fromisoformat(period_key[:10]))


# ── Catch-up cut-off ──────────────────────────────────────────────────────────────────────────


def catch_up_cutoff(
    hold_until: datetime,
    engine: str,
    words: Union[int, float],
    *,
    build_seconds: float = 900,
    cron_seconds: float = 1200,
    lease_seconds: float = 600,
    min_review_seconds: float = 7200,
) -> datetime:
    """The last instant a (re)build may START and still reach the owner before the hold ends
    (plan §1.4): `hold_until − (build + 2 × (cron + lease + render) + review)`, where render is the
    narration's worst-case render time, `words / NARRATION_WPM × 60 × RENDER_FACTOR[engine]` — two
    narrator rounds, because one may be lost to a killed worker (lease re-claim) and re-run.

    Fail-safe on junk, because a wrong cut-off starts a build that cannot finish in time:
    * an unknown engine uses the LARGEST render factor (WARNING);
    * `words` that are not a finite number > 0 (a bool, None, NaN, ±inf, 0, negative, a string)
      use the Deep Dive's maximum, `contracts.DEEP_DIVE_WORDS[1]` (WARNING);
    * `words` above `_MAX_CUTOFF_WORDS` are clamped to it (WARNING) — the cut-off is then days
      before the hold, so no catch-up starts, which is the right answer for such a piece.
    The keyword budgets are code constants: a negative, non-finite or non-number one is a
    programming error and raises ValueError. Returns an aware datetime in UTC."""
    _require_aware(hold_until, "hold_until")
    budgets = {"build_seconds": build_seconds, "cron_seconds": cron_seconds,
               "lease_seconds": lease_seconds, "min_review_seconds": min_review_seconds}
    for name, value in budgets.items():
        if not _is_finite_number(value) or value < 0:
            raise ValueError(f"catch_up_cutoff: {name} must be a finite number ≥ 0, got {value!r}")

    factor = contracts.RENDER_FACTOR.get(engine) if isinstance(engine, str) else None
    if factor is None:
        factor = max(contracts.RENDER_FACTOR.values())
        _log_cutoff_fallback("engine", repr(engine)[:40], f"the largest render factor {factor}")

    n_words = _cutoff_words(words)
    render_seconds = n_words / contracts.NARRATION_WPM * 60.0 * factor
    needed = build_seconds + 2.0 * (cron_seconds + lease_seconds + render_seconds) + min_review_seconds
    return hold_until.astimezone(_UTC) - timedelta(seconds=needed)


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _cutoff_words(words: Any) -> float:
    max_words = float(contracts.DEEP_DIVE_WORDS[1])
    if not _is_finite_number(words) or words <= 0:
        _log_cutoff_fallback("words", repr(words)[:40], f"the Deep Dive maximum {max_words:g}")
        return max_words
    if words > _MAX_CUTOFF_WORDS:
        _log_cutoff_fallback("words", repr(words)[:40], f"the clamp {_MAX_CUTOFF_WORDS}")
        return float(_MAX_CUTOFF_WORDS)
    return float(words)


@functools.lru_cache(maxsize=64)
def _log_cutoff_fallback(what: str, shown: str, used: str) -> None:
    """Once per distinct junk value per process: the cut-off is recomputed on every tick."""
    logger.warning("learn_weekly.schedule.catch_up_cutoff: %s=%s is unusable — using %s (fail-safe: "
                   "an earlier cut-off)", what, shown, used)


# ── sort_order (plan §1.10) ───────────────────────────────────────────────────────────────────


def deep_dive_sort_order(iso_year: int, iso_week: int) -> int:
    """`-(iso_year*100 + iso_week)`: always negative, newer is lower, and W53 → next W01 stays
    monotonic. TypeError for a bool/non-int; ValueError for a year outside 1..9999 or a week its
    ISO year does not have (W00, W54, a W53 in a 52-week year)."""
    for name, value in (("iso_year", iso_year), ("iso_week", iso_week)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"deep_dive_sort_order: {name} must be an int, got {value!r}")
    try:
        date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError(f"deep_dive_sort_order: no ISO week {iso_year}-W{iso_week:02d}: {exc}") from exc
    return -(iso_year * 100 + iso_week)


def slot_sort_order(publish_date: date) -> int:
    """`100000 + days since 2026-01-01`: above the bundle's 0..N-1, newer is higher. ValueError for
    a date before 2026-01-01 (it would collide with the bundle's range), TypeError for a non-date."""
    publish_date = _require_date(publish_date, "publish_date")
    if publish_date < SLOT_SORT_EPOCH:
        raise ValueError(f"slot_sort_order: {publish_date.isoformat()} is before {SLOT_SORT_EPOCH.isoformat()}")
    return SLOT_SORT_BASE + (publish_date - SLOT_SORT_EPOCH).days
