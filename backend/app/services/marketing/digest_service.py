"""
The marketing WEEKLY DIGEST and the nightly RUN-HEALTH alert (design doc §12.11): two Telegram reports
built ONLY from our own database. Neither calls a platform (rules/marketing.md §2 — only the publisher's
own steps may), so neither costs anything.

Both are the LAST steps of the publisher tick (`publisher_service.publisher_tick`), after the review
sweep and the feed, so they never delay a post or a review message. Both are day-keyed jobs on the ET
calendar run through `metrics_service.run_day_job` (the claim is
`notification_jobs.claimed_scheduled_job`; at most three claimed attempts per ET day per process; no
database read at all while a job is not due or after this process finished it today):

* `health_cycle()` — two day jobs, each at most one message per ET day. Their hours follow the
  worker's run hour, MARKETING_RUN_HOUR_ET: a worker variable this process cannot read, MIRRORED by
  the web setting of the same name (the owner sets both; a test pins the two defaults equal), read at
  call time (`run_hour_et`):
  - `marketing_run_health`, the first tick at/after `health_hour_et()` on a POSTING day
    (`selection.is_posting_day`): the run hour plus MARKETING_MAX_RUN_ATTEMPTS, capped at 23 — 22:00
    ET with the defaults 16 and 6, when the six hourly attempts from 16:15 ET are spent (~21:30 ET).
    That holds ONLY for a day with no missed tick and an uncapped sum: a retry after a failure, or a
    sum past 23, carries attempts past the check, and the worker resumes a day's failed or abandoned
    run on the next day's ticks before its run hour. With run hour 23 there is no nightly check (it
    would come before the worker's first tick, 23:15, and could only say "no run"): the final word
    reports that day alone. It reads today's run and asks the pure `evaluate_run_health`. A message
    goes out only when something is wrong — no run, `failed`, `skipped` for any reason but
    `rest_day`, a run still unfinished, or a stage's cgroup memory peak above MEMORY_ALERT_MB — and it
    says whether the day can still recover (the attempts left against MARKETING_MAX_RUN_ATTEMPTS, the
    run's liveness against MARKETING_RUN_STALE_SECONDS).
  - `marketing_run_health_final`, the first tick at/after `final_hour_et()` (the run hour: 16:00 ET by
    default) on the day AFTER a posting day: the worker's last tick that may still touch yesterday's
    run is at (run hour − 1):15 ET. It first reads WHEN the nightly check judged that run — that
    check's own `notification_job_state` row (`nightly_check_time`: `run_day` and `last_run_at`, the
    claim instant of its successful attempt, which is when it read the run) — then re-reads the run
    and asks the pure `evaluate_run_final`. After a check that went out: a word only when the run
    recovered after it, changed after it, or was left where its message could not call final (a retry
    still due, a run still unfinished). After a check that never succeeded that day (every send
    failed, or the bot was not set up that night): every outcome but a good day, once — never a
    silent failed day. An unreadable job state skips the tick (fail closed: no word on a guess). Its
    own job key: `run_day_job` keys a day by the ET day it RUNS on, so sharing `marketing_run_health`
    would mark that day's own nightly check done.
* `digest_cycle()` — job `marketing_digest_weekly` (MARKETING_DIGEST_ENABLED, checked here as well as
  in the tick), Monday at/after 09:00 ET with a Tuesday catch-up
  when Monday's never succeeded (`owed_since` = this week's Monday). It covers the previous
  Monday–Sunday in ET dates: `gather_digest` reads the ledger, the pure `summarize_week` turns the rows
  into numbers and the pure `compose_digest` writes ONE plain-text message within Telegram's 4,096
  UTF-16 units (rows are capped first; a hard cut at a line break is the last resort).

Delivery follows `publish_feed.feed_cycle`: the review bot configured + its review chat, the shared
Telegram flood back-off (`review_service._rate_limited_until` — while it is open no claim is taken, and
a 429 here holds the review sweep off too), the review sweep's `_Pacer`, plain text only (no
`parse_mode`). A job is marked done only after its message went out, so a failure is retried on a later
tick (a 429 inside the send spends that claimed attempt — `run_day_job` counts every claim — but
while the back-off is open no attempt is claimed at all). Neither cycle raises (except
CancelledError).

What may reach a message: counts, dates, statuses, platform names, links to our own posts, and
SCRUBBED (`outlet_base.scrub`), single-line, length-capped server strings — never a caption, a title or
any other model text. A value that cannot be read is left out or reported "unreadable"; it is never
shown as 0.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import statistics
import time
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.integrations import telegram
from app.integrations.telegram import MAX_MESSAGE_CHARS, TelegramException, TelegramRateLimitException
from app.schemas.marketing import RUN_STAGES
from app.services.marketing import metrics_service, outlets, review_service, selection, smart_link
from app.services.marketing.outlet_base import scrub
from app.services.marketing.review_service import REJECT_REASONS, _Pacer, _truncate_utf16, utf16_len
from app.services.marketing.run_service import (
    _exec,
    _parse_ts,
    _touched,
    expired_unreviewed,
    get_marketing_run_service,
    month_start_utc,
    post_run_date,
)
from app.services.notification_jobs import JobStateUnreadable
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── jobs and schedule ─────────────────────────────────────────────────────────

JOB_DIGEST = "marketing_digest_weekly"
JOB_HEALTH = "marketing_run_health"
#: The next day's final word on a run (`evaluate_run_final`) — its own key, never JOB_HEALTH's.
JOB_HEALTH_FINAL = "marketing_run_health_final"
#: The measure step's day job — the digest reports its last state.
JOB_METRICS = metrics_service.JOB_METRICS
#: `notification_job_state` — the day-job ledger (migration 147), read for the metrics job's state (the
#: digest) and for the nightly check's (the final word: when, and whether, it judged the run).
_JOB_STATE_TABLE = "notification_job_state"

#: The digest is due Monday from 09:00 ET; Tuesday from 09:00 ET is the catch-up (only when Monday's
#: never succeeded — `_this_monday`, run_day_job's `owed_since`).
DIGEST_HOUR_ET = 9
DIGEST_WEEKDAYS = (0, 1)
#: The latest hour a run-health job may be due from: the nightly check when the worker's attempts are
#: not spent before midnight (a run hour + attempts past 23, or no attempts cap).
LAST_HOUR_ET = 23
#: A claim older than this is taken over (a process killed mid-run). Each report takes seconds.
JOB_STALE_SECONDS = 600

#: A stage's cgroup memory peak (MB) above which the run-health alert warns: the worker's limit is
#: 3,814 MiB and the 2026-10-01 production peak was 2,587 MB.
MEMORY_ALERT_MB = 3200
#: The followers change is measured against the latest snapshot at least this much older than now's.
FOLLOWER_BASELINE_AGE = timedelta(days=6)
#: Follower snapshots are read from posts published this recently (`metrics.account`).
FOLLOWER_LOOKBACK = timedelta(days=21)

#: Bounded reads (the digest runs on the single uvicorn worker).
WEEK_POST_LIMIT = 500
MEASURED_POST_LIMIT = 200
ESCALATED_LIMIT = 20

#: Telegram's message limit, in UTF-16 code units.
MAX_DIGEST_UNITS = MAX_MESSAGE_CHARS
#: Per-section row caps tried in order until the digest fits; then a hard cut.
_ROW_CAPS = (8, 4, 2, 0)
_CUT_MARKER = "\n… (cut to fit one Telegram message)"

_ERROR_CAP = 120          # a failed post's error, per digest row
_HEALTH_ERROR_CAP = 300   # a run's last_error in the health alert
_REASON_CAP = 60          # an unrecognised skip reason, shown as data
_URL_CAP = 200

#: The worker's skip reasons → where the owner looks (rules/marketing.md §2; marketing/main.py).
_SKIP_HINTS: Dict[str, str] = {
    "writer_unavailable": "the writer never produced a verdict — check the Gemini API key, quota and the "
                          "writer model on the web service",
    "content_rejected": "every draft was rejected — read the rejected draft in marketing_scripts for this run",
    "judge_not_enforced": "the judge was not enforcing — set MARKETING_JUDGE_MODE=enforce on the web service",
    "empty_pool": "the content pool is empty — check the Learn bundle and content_pool's exclusions",
    "source_ineligible": "the selected lesson left the content pool — check content_pool's eligibility",
    "empty_narration": "the video stage had nothing to narrate — check the voice stage in the worker logs",
    "narration_too_long": "the narration ran past MARKETING_MAX_VIDEO_SECONDS — check the voice stage",
    "unrenderable_text": "the video stage could not draw the script (a glyph Inter lacks, e.g. an emoji) — "
                         "check the render stage",
}

_STATUS_ORDER = ("published", "retracted", "rejected", "failed", "skipped", "queued", "approved",
                 "pending_review")
_COUNT_KEYS = ("impressions", "views", "reach", "likes", "reposts", "replies", "quotes", "bookmarks",
               "shares", "saves")
#: What makes a post the week's "top" post: interactions, not reach.
_ENGAGEMENT_KEYS = ("likes", "reposts", "replies", "quotes", "bookmarks", "shares", "saves")
_COUNT_WORDS = {
    "impressions": ("impression", "impressions"), "views": ("view", "views"), "reach": ("reached", "reached"),
    "likes": ("like", "likes"), "reposts": ("repost", "reposts"), "replies": ("reply", "replies"),
    "quotes": ("quote", "quotes"), "bookmarks": ("bookmark", "bookmarks"), "shares": ("share", "shares"),
    "saves": ("save", "saves"),
}
_MEASURE_STATUSES = ("ok", "missing", "error", "unavailable", "capped", "no_external_id")
_MAX_COUNT = 10 ** 15

_KEY_RE = re.compile(r"[a-z0-9_]{1,40}", re.ASCII)
_CAMPAIGN_RE = re.compile(r"[a-z0-9_-]{1,40}", re.ASCII)
_WS_RE = re.compile(r"\s+")


# ── small pure helpers ────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(now: Optional[datetime]) -> datetime:
    """`now` as an aware instant (a naive one is UTC); None → the current time."""
    now = now if now is not None else _now()
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _et_day(now: datetime) -> date:
    return _aware(now).astimezone(ET).date()


def _key(value: Any) -> Optional[str]:
    """A code-written identifier (a status, a reason, an op, a platform), or None for anything else."""
    return value if isinstance(value, str) and _KEY_RE.fullmatch(value) else None


def _clean(text: Any, cap: int) -> str:
    """A server or model string made safe for one Telegram line: secrets redacted
    (`outlet_base.scrub`, which also caps it at 500 characters), every line break and other
    non-printable character — control and format characters, lone surrogates — folded into a space,
    so a hostile string can never start a line of its own; then capped at `cap` ("…" when cut)."""
    s = scrub(text)
    s = "".join(ch if ch.isprintable() else " " for ch in s)
    s = _WS_RE.sub(" ", s).strip()
    if len(s) > cap:
        s = s[: max(cap - 1, 0)].rstrip() + "…"
    return s


def _count(value: Any) -> Optional[int]:
    """A stored count, or None: a non-negative int (never a bool) below 10**15. A missing, negative,
    fractional, text or otherwise unreadable value is OMITTED — never shown as 0."""
    return value if type(value) is int and 0 <= value < _MAX_COUNT else None


def _peak_mb(value: Any) -> Optional[float]:
    """A memory reading in MB (an int or a float, finite, 0..10**7), or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except (OverflowError, ValueError):
        return None
    return v if math.isfinite(v) and 0 <= v <= 10 ** 7 else None


def _meta(row: Dict[str, Any]) -> Dict[str, Any]:
    return row["metadata"] if isinstance(row.get("metadata"), dict) else {}


def _metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    return row["metrics"] if isinstance(row.get("metrics"), dict) else {}


def _platform(row: Dict[str, Any]) -> Optional[str]:
    return _key(row.get("platform"))


def _label(platform: Optional[str]) -> str:
    return platform.upper() if platform else "?"


def _n(value: int) -> str:
    return f"{value:,}"


def _usd(micros: int) -> str:
    return f"{'-' if micros < 0 else ''}${abs(micros) / 1_000_000:.3f}"


def _day(d: date) -> str:
    return d.strftime("%a %m-%d")


def _et_local(dt: datetime) -> Optional[datetime]:
    """`dt` on the New York clock, or None when it cannot be: a hand-edited ledger time at the edge
    of the calendar (`0001-01-01T00:00:00+00:00`) overflows the conversion, and one such value must
    cost its own field, never the whole message."""
    try:
        return _aware(dt).astimezone(ET)
    except (OverflowError, ValueError):
        return None


def _et_stamp(dt: datetime) -> str:
    local = _et_local(dt)
    return local.strftime("%m-%d %H:%M ET") if local is not None else "?"


def _duration(seconds: float) -> str:
    total = int(round(seconds / 60.0))
    days, rest = divmod(total, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _plural(n: int, one: str, many: str) -> str:
    return f"{_n(n)} {one if n == 1 else many}"


def _join_counts(counts: Dict[str, int]) -> str:
    parts = []
    for k in _COUNT_KEYS:
        if k in counts:
            one, many = _COUNT_WORDS[k]
            parts.append(_plural(counts[k], one, many))
    return " · ".join(parts)


def _post_link(post: Dict[str, Any]) -> str:
    """Our own post's public link (never its text), or a placeholder."""
    url: Any = None
    try:
        adapter = outlets.adapter_for(post.get("platform"))
        url = adapter.post_url(post) if adapter else None
    except Exception as e:   # a malformed row must cost its link, never the digest
        logger.warning("marketing digest: no link for post_id=%s (%s: %s)", post.get("id"), type(e).__name__, e)
    url = url or post.get("external_url")
    cleaned = _clean(url, _URL_CAP) if isinstance(url, str) else ""
    return cleaned or "(no link)"


# ── schedule ──────────────────────────────────────────────────────────────────


def digest_due(now: datetime) -> bool:
    """Monday or Tuesday (the catch-up), at/after 09:00 ET."""
    et = _aware(now).astimezone(ET)
    return et.weekday() in DIGEST_WEEKDAYS and et.hour >= DIGEST_HOUR_ET


def digest_week(today: date) -> Tuple[date, date]:
    """(Monday, Sunday) of the ET week BEFORE the one `today` falls in."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def week_bounds(week_start: date, week_end: date) -> Tuple[datetime, datetime]:
    """[start, end) instants (UTC) of ET days `week_start`..`week_end` inclusive — DST-correct: the
    week holding the November change is 169 hours long, the March one 167."""
    start = datetime.combine(week_start, dtime(0), tzinfo=ET).astimezone(timezone.utc)
    end = datetime.combine(week_end + timedelta(days=1), dtime(0), tzinfo=ET).astimezone(timezone.utc)
    return start, end


def run_hour_et() -> int:
    """The worker's run hour (ET): MARKETING_RUN_HOUR_ET, the web's MIRROR of the worker's variable of
    the same name (marketing/main.py; this process cannot read the worker's environment, so the owner
    sets both — a test pins the two defaults equal). Read at call time. Settings refuses anything but
    0-23 at boot; a value that is still not one (only a test's monkeypatch can do that) is the default,
    logged — never a crash in the tick."""
    hour = settings.MARKETING_RUN_HOUR_ET
    if type(hour) is int and 0 <= hour <= LAST_HOUR_ET:
        return hour
    default = type(settings).model_fields["MARKETING_RUN_HOUR_ET"].default
    logger.warning("marketing run health: MARKETING_RUN_HOUR_ET=%r is not an hour 0-23 — using the default %s",
                   hour, default)
    return default


def health_hour_et() -> int:
    """The hour (ET) the nightly check is due from on a posting day: the run hour plus the attempts cap
    (MARKETING_MAX_RUN_ATTEMPTS). The worker's first attempt starts at :15 of its run hour and a failed or
    abandoned one is retried at the next hourly tick, so with no missed tick the last allowed attempt
    starts at run hour + cap − 1. Capped at LAST_HOUR_ET; no cap at all (a value that is not a positive
    int — `decide_claim` applies none then) is LAST_HOUR_ET too. The defaults 16 and 6 → 22:00 ET."""
    attempts = settings.MARKETING_MAX_RUN_ATTEMPTS
    if type(attempts) is not int or attempts <= 0:
        return LAST_HOUR_ET
    return min(run_hour_et() + attempts, LAST_HOUR_ET)


def final_hour_et() -> int:
    """The hour (ET) the next day's final word is due from: the run hour. The worker resumes yesterday's
    failed or abandoned run only on its ticks BEFORE that hour (marketing/main.py `resume_only`), so the
    last tick that may touch it starts at (run hour − 1):15 ET. The default 16 → 16:00 ET."""
    return run_hour_et()


def health_due(now: datetime) -> bool:
    """A posting day (ET), at/after `health_hour_et()` — and never when that hour is not AFTER the run
    hour (run hour 23): the check would then come before the worker's first tick of the day (23:15) and
    could only report a run that cannot exist yet. The next day's final word, which sees that the check
    never succeeded, reports that day alone."""
    et = _aware(now).astimezone(ET)
    hour = health_hour_et()
    return hour > run_hour_et() and et.hour >= hour and selection.is_posting_day(et.date())


def final_due(now: datetime) -> bool:
    """The day after a posting day (ET), at/after `final_hour_et()`."""
    et = _aware(now).astimezone(ET)
    return et.hour >= final_hour_et() and selection.is_posting_day(et.date() - timedelta(days=1))


def health_opens_at(run_date: date) -> datetime:
    """The first instant the nightly check of `run_date` is due: `health_hour_et()`:00 ET on that day
    (DST-correct). The earliest it can have judged that run — the final word's check time when the
    check's own record shows THAT it succeeded but not when (`nightly_check_time`)."""
    return datetime.combine(run_date, dtime(health_hour_et()), tzinfo=ET)


# ── run health (pure) ─────────────────────────────────────────────────────────


def _memory_peaks(run: Dict[str, Any]) -> List[Tuple[float, str]]:
    """(MB, stage) for every readable `<stage>_cgroup_peak_mb` in the run's timings."""
    timings = run.get("timings") if isinstance(run.get("timings"), dict) else {}
    out = []
    for key, value in timings.items():
        if not isinstance(key, str) or not key.endswith("_cgroup_peak_mb"):
            continue
        mb = _peak_mb(value)
        if mb is None:
            continue
        out.append((mb, _key(key[: -len("_cgroup_peak_mb")]) or "?"))
    return out


def _memory_line(run: Dict[str, Any], day: str) -> Optional[str]:
    over = [p for p in _memory_peaks(run) if p[0] > MEMORY_ALERT_MB]
    if not over:
        return None
    mb, stage = max(over)
    return (f"⚠️ Marketing worker memory: stage {stage} peaked at {_n(int(round(mb)))} MB on {day} (alert above "
            f"{_n(MEMORY_ALERT_MB)} MB; the worker's limit is ~3,814 MiB). Give the marketing-worker service more "
            f"memory before a stage is OOM-killed.")


def _no_retry() -> str:
    """The worker resumes a day's failed or abandoned run only on the next day's ticks BEFORE its run hour
    (marketing/main.py `resume_only`; 16:00 ET by default) — after that, nothing retries that day."""
    return f"no retry is left (the worker resumes a day only until {final_hour_et():02d}:00 ET the next day)"


def _final_word() -> str:
    """What the nightly check promises whenever its verdict is not final (`evaluate_run_final` keeps it)."""
    return f"a final word comes tomorrow after {final_hour_et():02d}:00 ET"


def _retries() -> str:
    """What the worker still does with a failed run below the attempts cap. With run hour 0 every tick of
    the day is in its window, so there is no early tick of tomorrow to resume it on."""
    if final_hour_et() == 0:
        return "the worker retries hourly until midnight ET"
    return "the worker retries hourly until midnight ET and resumes it on tomorrow's early ticks"


def _no_run_hint() -> str:
    return (f"Check the marketing-worker cron on Railway (hourly from {run_hour_et():02d}:15 ET) and its last "
            f"logs")


#: A finish time this far past `now` is a clock error or a hand edit, never a time to report.
_FUTURE_SKEW = timedelta(hours=1)


def _where(run: Dict[str, Any]) -> str:
    """Where the run stopped, in the ledger's own terms. `stage` is the LAST COMPLETED stage
    (migration 170): a failure happened in the stage after it — or outside any stage (the preflight
    of every tick, the accepted-script re-derivation, the final media_ready update) — so this is
    never "at stage X"."""
    stage = _key(run.get("stage"))
    if stage == RUN_STAGES[0]:   # `planned`, the claim's own value: nothing has completed yet
        return "before any stage completed"
    return f"after stage {stage or '?'} (the last completed stage)"


def _run_facts(run: Dict[str, Any], *, now: datetime, max_attempts: Any, stale_seconds: Any) -> Dict[str, Any]:
    """What both run-health judgements read from a run row, every field defensively. `attempts` must
    be a plain count (`_count`; anything else is "?" and decides nothing), the cap a positive int
    (`decide_claim` applies none otherwise), and liveness is `decide_claim`'s own: the later of
    `started_at` / `updated_at` (`run_service._touched`) younger than `stale_seconds` — an unreadable
    time is stale, as there."""
    n = _count(run.get("attempts"))
    cap = max_attempts if type(max_attempts) is int and max_attempts > 0 else None
    shown = "?" if n is None else _n(n)
    touched = _touched(run)
    window = timedelta(seconds=max(stale_seconds, 0)) if type(stale_seconds) is int else timedelta(0)
    raw_status = run.get("status")
    status = _key(raw_status)
    err = run.get("last_error")
    return {
        "status": status,
        "shown_status": status or (_clean(raw_status, _REASON_CAP) if raw_status not in (None, "") else "") or "?",
        "where": _where(run),
        "attempt": f"attempt {shown} of {_n(cap)}" if cap is not None else f"attempt {shown} (no attempts cap)",
        "cap": cap,
        "exhausted": cap is not None and n is not None and n >= cap,
        "unknown": cap is not None and n is None,
        "touched": touched,
        "fresh": touched is not None and now - touched < window,
        "last_error": _clean(err, _HEALTH_ERROR_CAP) if err not in (None, "") else "",
    }


def _skip_text(run: Dict[str, Any], facts: Dict[str, Any], day: str) -> Optional[str]:
    """The SKIPPED line, or None for a `rest_day` skip (never an alert)."""
    raw_reason = _meta(run).get("skip_reason")
    reason = _key(raw_reason)
    if reason == "rest_day":
        return None
    if reason is None and raw_reason in (None, ""):
        text = (f"⚠️ Marketing run {day} was SKIPPED with no reason recorded — {facts['where']}, "
                f"{facts['attempt']}.")
    elif reason in _SKIP_HINTS:
        text = f"⚠️ Marketing run {day} was SKIPPED ({reason}): {_SKIP_HINTS[reason]}."
        run_id = review_service.canonical_post_id(run.get("id"))   # any uuid, canonical form
        if reason == "content_rejected" and run_id:
            text = text[:-1] + f" (run_id {run_id})."
    else:
        shown = reason or _clean(raw_reason, _REASON_CAP) or "?"
        text = (f"⚠️ Marketing run {day} was SKIPPED for an unrecognised reason: {shown} — {facts['where']}, "
                f"{facts['attempt']}. Check the marketing-worker logs.")
    if facts["last_error"]:
        text += f" Last error: {facts['last_error']}."
    return text


def _unexpected_text(facts: Dict[str, Any], day: str) -> str:
    return (f"⚠️ Marketing run {day} has an unexpected status: {facts['shown_status']} — {facts['where']}, "
            f"{facts['attempt']}. Check the ledger.")


def _with_memory(text: Optional[str], memory: Optional[str]) -> Optional[str]:
    if text is None:
        return memory
    return f"{text}\n{memory}" if memory else text


def evaluate_run_health(
    run: Optional[Dict[str, Any]], *, run_date: date, posting_day: bool, now: datetime, max_attempts: Any,
    stale_seconds: Any,
) -> Optional[str]:
    """What is wrong with the run of `run_date`, as one Telegram message — or None when nothing is.
    Pure. Called from `health_hour_et()` (22:00 ET by default) with `max_attempts` =
    MARKETING_MAX_RUN_ATTEMPTS and `stale_seconds` = MARKETING_RUN_STALE_SECONDS: it says only what the
    worker's own rules (`decide_claim`) make true.

    Not a posting day → None. No run → the worker never claimed the day. The stage is always named
    as the LAST COMPLETED one ("after stage X"). `failed` → "all M attempts used; this day's posts will
    not go out" at the attempts cap, otherwise "attempt N of M" and that the worker retries hourly
    until midnight ET and resumes it on tomorrow's early ticks, with a final word after
    `final_hour_et()` tomorrow (`evaluate_run_final`). `skipped` for any reason but `rest_day` → the
    reason and where to look. `planned` / `in_progress` → still running while its liveness is fresh;
    once stale, abandoned — with no retries left at the cap (the day will close failed), else retried
    hourly. An unreadable attempt count is "?" and promises neither. `media_ready` / `published` →
    None, except one line when a stage's cgroup memory peak passed MEMORY_ALERT_MB (added to every
    other message too). Every server string is scrubbed and length-capped."""
    if not posting_day:
        return None
    day = run_date.strftime("%a %Y-%m-%d")
    if not isinstance(run, dict):
        return (f"⚠️ Marketing: no run for {day}, a posting day — the worker never claimed it. "
                f"{_no_run_hint()}; nothing will be posted for this day.")
    facts = _run_facts(run, now=_aware(now), max_attempts=max_attempts, stale_seconds=stale_seconds)
    status, where, attempt, last_error = facts["status"], facts["where"], facts["attempt"], facts["last_error"]
    memory = _memory_line(run, day)
    final_word = _final_word()

    if status in ("media_ready", "published"):
        return memory
    if status == "failed":
        if facts["exhausted"]:
            outlook = f"all {_n(facts['cap'])} attempts used; this day's posts will not go out"
        elif facts["unknown"]:
            outlook = (f"{attempt} — the attempt count is unreadable, so whether the worker retries is unknown; "
                       f"{final_word}")
        else:
            outlook = f"{attempt}; {_retries()} — {final_word}"
        text: Optional[str] = (f"⚠️ Marketing run {day} FAILED {where}: {outlook}. Last error: "
                               f"{last_error or '(none recorded)'}. Check the marketing-worker logs.")
    elif status == "skipped":
        text = _skip_text(run, facts, day)
    elif status in ("planned", "in_progress"):
        if facts["fresh"]:
            mark, outlook = "⏳", f"still running ({attempt})"
        elif facts["exhausted"]:
            mark, outlook = "⚠️", ("the last allowed attempt was abandoned; no retries remain — the day will "
                                   "close failed")
        elif facts["unknown"]:
            mark, outlook = "⚠️", (f"{attempt} was abandoned; the attempt count is unreadable, so whether the "
                                   f"worker retries is unknown")
        else:
            mark, outlook = "⚠️", f"{attempt} was abandoned; the worker retries hourly"
        text = f"{mark} Marketing run {day} is not finished yet: {status} {where} — {outlook}"
        if last_error:
            text += f"; last error: {last_error}"
        text += f". {final_word[0].upper()}{final_word[1:]}."
    else:
        text = _unexpected_text(facts, day)
    return _with_memory(text, memory)


#: `nightly_check_time` outcomes: how the final word knows when the nightly check judged the run.
NIGHTLY_CHECKED = "checked"              # it succeeded that day: `last_run_at`, the claim instant
NIGHTLY_TIME_UNKNOWN = "time_unknown"    # it succeeded that day, but its record no longer says when
NIGHTLY_NEVER = "never"                  # no success recorded for that day: every attempt failed, or none ran
NIGHTLY_LATER_DAY = "later_day"          # a later day's success hides whether that day's went out


def _ledger_date(value: Any) -> Optional[date]:
    """A `DATE` column as PostgREST answers it ("YYYY-MM-DD"), or a date object; anything else → None.
    A datetime is not a DATE (its day would depend on a timezone): None."""
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def nightly_check_time(state: Any, run_date: date) -> Tuple[Optional[datetime], str]:
    """(checked_at, how): WHEN the nightly check (`marketing_run_health`) judged the run of `run_date`, read
    from that job's own `notification_job_state` row — or None when it never succeeded for that day. Pure.

    Migration 147's `finish_scheduled_job` stamps `last_run_at` on EVERY attempt and advances `run_day` (the
    ET day of the claim) only on success; `claimed_scheduled_job` stamps both with the CLAIM's time, and
    the check reads the run right after its claim — so the successful attempt's `last_run_at` is when it
    judged the run. Once a day succeeded the claim refuses that day, so only a LATER day's attempt can
    overwrite `last_run_at`.

    * `run_day` == `run_date` → (`last_run_at`, NIGHTLY_CHECKED) when that falls on `run_date` (ET); else
      — a later day's failed attempt overwrote it, or it is missing or unreadable — (`health_opens_at`,
      NIGHTLY_TIME_UNKNOWN): the earliest the check can have run. A time too early may repeat that
      night's verdict; a time too late would hide a change the owner was promised a word on.
    * No row (the default state), `run_day` NULL or an earlier day → (None, NIGHTLY_NEVER): every attempt
      failed (three Telegram 500s), the bot was not set up that night, the job was disabled, or (run
      hour 23) the check never runs. The owner heard nothing that night.
    * `run_day` LATER than `run_date` → (None, NIGHTLY_LATER_DAY): the next posting day's check succeeded
      before this word could go out (or the check's claim straddled midnight), and the row no longer
      says whether `run_date`'s went out. Speaking may repeat an alert; staying silent could hide a
      failed day.

    Raises JobStateUnreadable when the row is not an object or its `run_day` is not a date: whether the
    check went out is then unknown, and the caller skips the tick (fail closed — no word on a guess)."""
    if not isinstance(state, dict):
        raise JobStateUnreadable(f"{JOB_HEALTH}: the job-state row is not an object ({type(state).__name__})")
    raw_day = state.get("run_day")
    if raw_day is None:
        return None, NIGHTLY_NEVER
    day = _ledger_date(raw_day)
    if day is None:
        raise JobStateUnreadable(f"{JOB_HEALTH}: run_day {_clean(repr(raw_day), 60)} is not a date")
    if day < run_date:
        return None, NIGHTLY_NEVER
    if day > run_date:
        return None, NIGHTLY_LATER_DAY
    at = _parse_ts(state.get("last_run_at"))
    local = _et_local(at) if at is not None else None
    if at is not None and local is not None and local.date() == run_date:
        return at, NIGHTLY_CHECKED
    return health_opens_at(run_date), NIGHTLY_TIME_UNKNOWN


def _check_stamp(checked: datetime) -> str:
    local = _et_local(checked)
    return f"{local:%H:%M} ET" if local is not None else "nightly"


def evaluate_run_final(
    run: Optional[Dict[str, Any]], *, run_date: date, posting_day: bool, now: datetime, max_attempts: Any,
    stale_seconds: Any, checked_at: Optional[datetime], nightly: str = NIGHTLY_NEVER,
) -> Optional[str]:
    """The final word on the run of `run_date`, judged from `final_hour_et()` the next day (`final_due`: the
    worker's last tick that may still touch it has run) — or None when the nightly check already said all
    there is to say. Pure. `checked_at` is WHEN that check judged the run (`nightly_check_time`: the claim
    instant of its successful attempt), or None when it never succeeded for `run_date` — the owner heard
    nothing that night, and this word stands in for it. Required: there is no default check time.

    After a check (`checked_at` set):
    * No run → None (the nightly "no run" alert was final; a run the worker created after the check is a
      row, judged below).
    * `media_ready` / `published` → "recovered at <ET time>" when it finished (`finished_at`, else
      `updated_at`; never a time more than an hour in the future) AFTER `checked_at`; None when it
      finished at or before it (the check saw it fine) or no finish time can be read (never a guessed
      time).
    * Changed after `checked_at` (its liveness, the later of `started_at` / `updated_at` — every run
      write moves `updated_at`) → one line on where it ended, in the nightly check's terms (the stage as
      the last completed one, attempt N of M, the scrubbed last error) and the truth that no retry of the
      day is left: ended FAILED, SKIPPED (any reason but `rest_day`), still running (resumed after the
      check — if this attempt fails, nothing retries it), never finished (abandoned), or an unexpected
      status. A write in the instant between the check's claim and its read of the run counts as after
      it: it may repeat that verdict, never hide one.
    * Unchanged since `checked_at` (or its time unreadable) → a line only when the nightly verdict was not
      final: `failed` below the attempts cap (or with an unreadable count) → "no retry happened — check
      the marketing-worker cron"; `planned` / `in_progress` → "never finished — the day is lost".
      Otherwise None: the nightly alert was already final.

    With no check (`checked_at` None) every outcome but a good day is reported, as one terminal line: no
    run → "no run"; `failed`, `skipped` (any reason but `rest_day`), unfinished or an unexpected status →
    the same line as a run changed after a check; `media_ready` / `published` → only the memory line, if
    any (a good day says nothing). `nightly` is WHY there is no check time (`nightly_check_time`'s
    second value): only NIGHTLY_NEVER may say the nightly check "never went out" — under
    NIGHTLY_LATER_DAY a later day's success overwrote the record, and that night's alert may well have
    gone out, so the line says the record no longer tells.

    A stage's memory peak above MEMORY_ALERT_MB rides along on the recovered and terminal lines (it may be
    new since the check, or the check never went out); an unchanged run's peak was in the nightly message
    already."""
    if not posting_day:
        return None
    now = _aware(now)
    day = run_date.strftime("%a %Y-%m-%d")
    checked = _aware(checked_at) if isinstance(checked_at, datetime) else None
    if not isinstance(run, dict):
        if checked is not None:
            return None
        if health_hour_et() <= run_hour_et():
            why = f"run hour {run_hour_et():02d} has no nightly check"
        elif nightly == NIGHTLY_LATER_DAY:
            why = "the nightly check's record no longer says whether its alert went out"
        else:
            why = "the nightly check never went out"
        return (f"⚠️ Marketing: no run for {day}, a posting day — the worker never claimed it, so nothing was "
                f"posted for this day ({why}). {_no_run_hint()}.")
    facts = _run_facts(run, now=now, max_attempts=max_attempts, stale_seconds=stale_seconds)
    status, where, attempt, last_error = facts["status"], facts["where"], facts["attempt"], facts["last_error"]
    memory = _memory_line(run, day)
    error_tail = f"Last error: {last_error or '(none recorded)'}."
    no_retry = _no_retry()

    if status in ("media_ready", "published"):
        if checked is None:
            return memory
        horizon = now + _FUTURE_SKEW
        finished = next((t for t in (_parse_ts(run.get("finished_at")), _parse_ts(run.get("updated_at")))
                         if t is not None and t <= horizon), None)
        if finished is None or finished <= checked:
            return None
        what = "its posts are waiting for review" if status == "media_ready" else "its posts went out"
        return _with_memory(f"✅ Marketing run {day} recovered at {_et_stamp(finished)} — {what}.", memory)

    touched = facts["touched"]
    if checked is None or (touched is not None and touched > checked):
        if status == "failed":
            outlook = (f"all {_n(facts['cap'])} attempts used" if facts["exhausted"]
                       else f"{attempt}; {no_retry}")
            text: Optional[str] = (f"⚠️ Marketing run {day} ended FAILED {where}: {outlook}. This day's posts "
                                   f"will not go out. {error_tail}")
        elif status == "skipped":
            text = _skip_text(run, facts, day)
        elif status in ("planned", "in_progress") and facts["fresh"]:
            resumed = f", resumed after the {_check_stamp(checked)} check" if checked is not None else ""
            text = (f"⏳ Marketing run {day} is still running {where}, {attempt}{resumed} — if this attempt "
                    f"fails, {no_retry}.")
        elif status in ("planned", "in_progress"):
            text = (f"⚠️ Marketing run {day} never finished — the day is lost: {status} {where}, {attempt} was "
                    f"abandoned and {no_retry}. Check the marketing-worker logs.")
        else:
            text = _unexpected_text(facts, day)
        return _with_memory(text, memory)

    # Unchanged since the nightly check: speak only where that verdict could not be final.
    if status == "failed" and not facts["exhausted"]:
        return (f"⚠️ Marketing run {day} is still FAILED {where}, {attempt}: no retry happened — check the "
                f"marketing-worker cron (hourly at :15) and its last logs. This day's posts will not go out. "
                f"{error_tail}")
    if status in ("planned", "in_progress"):
        since = _et_stamp(touched) if touched is not None else "an unreadable time"
        return (f"⚠️ Marketing run {day} never finished — the day is lost: {status} {where}, {attempt}, "
                f"untouched since {since}. Check the marketing-worker cron and its last logs.")
    return None


# ── the pool's runway (pure) ──────────────────────────────────────────────────


def pool_runway(pool: Iterable[Any], used: Iterable[Any], *, today: date, today_picked: bool) -> Dict[str, Any]:
    """How long the content pool lasts. `pool` = the eligible keys (`content_pool.eligible_keys()`);
    `used` = the picks made so far (`run_service.recent_source_refs`, today's included when it was made);
    `today_picked` = today's pick is among them. Pure.

    `unused` = pool keys never picked. Selection posts every unused lesson before any repeat (its
    disjoint rotation plus the recent-picks window), one per posting day, so the first repeat falls on
    the (unused + 1)-th posting day from the next one without a pick: today when it is a posting day
    whose pick is still to come, else tomorrow onward. `first_repeat` is None for an empty pool."""
    keys = {k for k in pool if isinstance(k, str) and k}
    seen = {r for r in used if isinstance(r, str)}
    unused = len(keys - seen)
    out: Dict[str, Any] = {"pool": len(keys), "unused": unused, "first_repeat": None}
    if not keys or not selection.POST_WEEKDAYS:
        return out
    day = today if (selection.is_posting_day(today) and not today_picked) else today + timedelta(days=1)
    wanted = unused + 1
    found = 0
    try:
        for _ in range(7 * wanted + 7):   # every 7 days hold at least one posting day
            if selection.is_posting_day(day):
                found += 1
                if found == wanted:
                    out["first_repeat"] = day
                    break
            day += timedelta(days=1)
    except OverflowError:   # a pool so large its runway passes year 9999
        out["first_repeat"] = None
    return out


# ── the weekly digest (pure) ──────────────────────────────────────────────────


def _status_counts(posts: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Counter = Counter()
    for p in posts:
        out[_key(p.get("status")) or "unknown"] += 1
    return dict(out)


def _expired_kind(post: Dict[str, Any]) -> Optional[str]:
    """For an expired post: `approved` (approved, never sent), `asked` (sent with buttons, never
    decided), `preview` (its platform was not enabled — a read-only preview), `unshown` (never reached
    the owner). None for anything else."""
    meta = _meta(post)
    if post.get("status") != "skipped" or meta.get("skip_reason") != "expired":
        return None
    if not expired_unreviewed(post):
        return "approved"
    if meta.get("review_notified_at"):
        return "asked"
    if meta.get("review_preview_at"):
        return "preview"
    return "unshown"


def _last_counts(post: Dict[str, Any]) -> Dict[str, int]:
    last = _metrics(post).get("last")
    if not isinstance(last, dict):
        return {}
    out = {}
    for k in _COUNT_KEYS:
        v = _count(last.get(k))
        if v is not None:
            out[k] = v
    return out


def _engagement(posts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per platform, over the week's live `published` posts: how many were measured, the summed latest
    counts (a count only over the posts that HAVE it), the measure statuses, and the top post by
    interactions (its link only)."""
    out: Dict[str, Dict[str, Any]] = {}
    for post in posts:
        if post.get("status") != "published":
            continue
        platform = _platform(post)
        if platform is None:
            continue
        entry = out.setdefault(platform, {"live": 0, "measured": 0, "totals": {}, "statuses": {}, "top": None})
        entry["live"] += 1
        status = _metrics(post).get("status")
        if status is not None:
            key = status if status in _MEASURE_STATUSES else "other"
            entry["statuses"][key] = entry["statuses"].get(key, 0) + 1
        counts = _last_counts(post)
        if not counts:
            continue
        entry["measured"] += 1
        for k, v in counts.items():
            entry["totals"][k] = entry["totals"].get(k, 0) + v
        score = sum(counts.get(k, 0) for k in _ENGAGEMENT_KEYS)
        top = entry["top"]
        if score > 0 and (top is None or score > top["score"]):
            entry["top"] = {"score": score, "url": _post_link(post),
                            "counts": {k: counts[k] for k in _ENGAGEMENT_KEYS if k in counts}}
    return out


def _followers(posts: List[Dict[str, Any]], *, now: datetime) -> Dict[str, Dict[str, Any]]:
    """Per platform: the newest `metrics.account` follower snapshot and the latest one at least
    FOLLOWER_BASELINE_AGE older (the measure step stores the account snapshot on the newest post of a
    platform, so the older posts keep the earlier ones). A snapshot without a readable time or count,
    dated in the future, or at a time the New York clock cannot show (`_et_local`), is ignored."""
    snaps: Dict[str, List[Tuple[datetime, int]]] = {}
    horizon = now + timedelta(hours=1)
    for post in posts:
        platform = _platform(post)
        account = _metrics(post).get("account")
        if platform is None or not isinstance(account, dict):
            continue
        at, followers = _parse_ts(account.get("at")), _count(account.get("followers"))
        if at is None or followers is None or at > horizon or _et_local(at) is None:
            continue
        snaps.setdefault(platform, []).append((at, followers))
    out: Dict[str, Dict[str, Any]] = {}
    for platform, rows in snaps.items():
        at, followers = max(rows)
        older = [r for r in rows if r[0] <= at - FOLLOWER_BASELINE_AGE]
        base = max(older) if older else None
        out[platform] = {"followers": followers, "at": at,
                         "baseline": base[1] if base else None, "baseline_at": base[0] if base else None}
    return out


def _taps(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by: Counter = Counter()
    unreadable = 0
    for r in rows:
        if not isinstance(r, dict):
            unreadable += 1
            continue
        hits = _count(r.get("hits"))
        if hits is None:
            unreadable += 1
            continue
        campaign = r.get("campaign")
        campaign = campaign if isinstance(campaign, str) and _CAMPAIGN_RE.fullmatch(campaign) else "(unreadable)"
        by[campaign] += hits
    ordered = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"rows": ordered, "total": sum(by.values()), "unreadable_rows": unreadable}


def _latency(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Seconds from the review message (`metadata.review_notified_at`) to the owner's decision
    (`metadata.review.at`), over the decided posts that carry both. A negative gap is unreadable."""
    gaps: List[float] = []
    for post in posts:
        meta = _meta(post)
        review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
        if review.get("decision") not in ("approved", "rejected"):
            continue
        asked, decided = _parse_ts(meta.get("review_notified_at")), _parse_ts(review.get("at"))
        if asked is None or decided is None or decided < asked:
            continue
        gaps.append((decided - asked).total_seconds())
    if not gaps:
        return {"n": 0, "median_s": None, "max_s": None}
    return {"n": len(gaps), "median_s": statistics.median(gaps), "max_s": max(gaps)}


def _run_rows(runs: List[Dict[str, Any]], week_start: date, week_end: date) -> Tuple[List[Tuple[date, str]], Optional[Tuple[float, date, str]]]:
    """One (day, text) per posting day and per rest day with something other than a rest-day skip;
    plus the week's highest stage memory peak (MB, day, stage)."""
    by_day: Dict[date, Dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        try:
            d = date.fromisoformat(str(run.get("run_date"))[:10])
        except ValueError:
            continue
        by_day[d] = run
    rows: List[Tuple[date, str]] = []
    peak: Optional[Tuple[float, date, str]] = None
    d = week_start
    while d <= week_end:
        run = by_day.get(d)
        posting = selection.is_posting_day(d)
        if run is None:
            if posting:
                rows.append((d, "no run (a posting day)"))
        else:
            status = _key(run.get("status")) or "unknown"
            meta = _meta(run)
            text = status
            if status == "skipped":
                reason = _key(meta.get("skip_reason"))
                closed = meta.get("closed") if isinstance(meta.get("closed"), dict) else {}
                why = reason or _key(closed.get("reason"))
                if reason == "rest_day" and not posting:
                    text = ""
                elif why:
                    text = f"skipped — {why}"
            elif status == "failed":
                # `stage` is the LAST COMPLETED stage (migration 170), never where it failed.
                stage = _key(run.get("stage"))
                text = ("failed before any stage completed" if stage == RUN_STAGES[0]
                        else f"failed after {stage or '?'}")
            if text:
                rows.append((d, text))
            for mb, stage in _memory_peaks(run):
                if peak is None or mb > peak[0]:
                    peak = (mb, d, stage)
        d += timedelta(days=1)
    return rows, peak


def _listed(value: Any) -> Optional[List[Any]]:
    """A section's rows as a list, or None (unreadable) for anything that is not a list or tuple."""
    return list(value) if isinstance(value, (list, tuple)) else None


def summarize_week(
    *, now: datetime, week_start: date, week_end: date, posts: List[Dict[str, Any]],
    runs: Optional[List[Dict[str, Any]]] = None, measured: Optional[List[Dict[str, Any]]] = None,
    link_hits: Optional[List[Dict[str, Any]]] = None, spend: Optional[Dict[str, int]] = None,
    budget_micros: int = 0, escalated: Optional[List[Dict[str, Any]]] = None,
    runway: Optional[Dict[str, Any]] = None, metrics_job: Optional[Dict[str, Any]] = None,
    metrics_enabled: bool = False, store: str = "prelaunch", posts_capped: bool = False,
) -> Dict[str, Any]:
    """The digest's numbers from the rows the gather read. Pure. A section whose rows could not be read
    is passed as None and stays None ("unreadable" in the message) — never an empty or zero section."""
    now = _aware(now)
    runs, measured, link_hits, escalated = (_listed(runs), _listed(measured), _listed(link_hits),
                                            _listed(escalated))
    spend = spend if isinstance(spend, dict) else None
    runway = runway if isinstance(runway, dict) else None
    metrics_job = metrics_job if isinstance(metrics_job, dict) else None
    rows = [p for p in (_listed(posts) or []) if isinstance(p, dict)]
    live = [p for p in rows if not review_service.is_rehearsal(p)]
    by_platform: Dict[str, Dict[str, int]] = {}
    for p in live:
        platform = _label(_platform(p))
        status = _key(p.get("status")) or "unknown"
        by_platform.setdefault(platform, {})
        by_platform[platform][status] = by_platform[platform].get(status, 0) + 1

    expired: Counter = Counter()
    reasons: Counter = Counter()
    rejected_rows: List[Tuple[str, str, str]] = []
    failed_rows: List[Tuple[str, str, str]] = []
    for p in live:
        kind = _expired_kind(p)
        if kind:
            expired[kind] += 1
        run_day = post_run_date(p)
        run_txt = run_day.strftime("%m-%d") if run_day else "?"
        if p.get("status") == "rejected":
            review = _meta(p).get("review") if isinstance(_meta(p).get("review"), dict) else {}
            reason = review.get("reason")
            code = reason if isinstance(reason, str) and reason in REJECT_REASONS else None
            reasons[code or "none"] += 1
            rejected_rows.append((_label(_platform(p)), run_txt, REJECT_REASONS[code] if code else "no reason"))
        elif p.get("status") == "failed":
            pub = _meta(p).get("publish") if isinstance(_meta(p).get("publish"), dict) else {}
            category = _key(pub.get("category"))
            err = p.get("last_error") or pub.get("error")
            detail = _clean(err, _ERROR_CAP) if err not in (None, "") else "no error recorded"
            failed_rows.append((_label(_platform(p)), run_txt, f"{category}: {detail}" if category else detail))

    escalated_rows = None
    if escalated is not None:
        escalated_rows = []
        for p in escalated:
            if not isinstance(p, dict):
                continue
            run_day = post_run_date(p)
            since = _parse_ts(_meta(p).get("escalated_at"))
            escalated_rows.append((_label(_platform(p)), run_day.strftime("%m-%d") if run_day else "?",
                                   _et_stamp(since) if since else "?"))

    spend_out = None
    if spend is not None:
        by_op: Dict[str, int] = {}
        for op, micros in spend.items():
            if type(micros) is not int:
                continue
            name = op if isinstance(op, str) and _KEY_RE.fullmatch(op) else "unreadable_op"
            by_op[name] = by_op.get(name, 0) + micros
        spend_out = {"month": month_start_utc(now).strftime("%Y-%m"), "total": sum(by_op.values()),
                     "by_op": sorted(by_op.items(), key=lambda kv: (-kv[1], kv[0])),
                     "budget": budget_micros if type(budget_micros) is int and budget_micros > 0 else 0}

    run_rows, peak = _run_rows(runs, week_start, week_end) if runs is not None else (None, None)
    return {
        "now": now, "week_start": week_start, "week_end": week_end, "posts_capped": bool(posts_capped),
        "rehearsal": len(rows) - len(live), "live": len(live), "status_totals": _status_counts(live),
        "by_platform": by_platform, "expired": dict(expired),
        "rejections": {"by_reason": dict(reasons), "rows": rejected_rows}, "failed_rows": failed_rows,
        "engagement": _engagement(live), "metrics_enabled": bool(metrics_enabled),
        "followers": _followers([p for p in measured if isinstance(p, dict)], now=now) if measured is not None else None,
        "taps": _taps(link_hits) if link_hits is not None else None, "store": store,
        "spend": spend_out, "latency": _latency(live), "runway": runway, "escalated": escalated_rows,
        "metrics_job": metrics_job, "runs": run_rows, "memory_peak": peak,
    }


def _capped(rows: List[str], cap: int, indent: str = "  ") -> List[str]:
    shown = [f"{indent}{r}" for r in rows[:cap]]
    if len(rows) > cap:
        shown.append(f"{indent}… and {_n(len(rows) - cap)} more")
    return shown


def _ordered_statuses(counts: Dict[str, int]) -> str:
    keys = [s for s in _STATUS_ORDER if counts.get(s)] + sorted(s for s in counts if s not in _STATUS_ORDER
                                                                and counts.get(s))
    return " · ".join(f"{s} {_n(counts[s])}" for s in keys)


def _render_digest(r: Dict[str, Any], cap: int) -> str:
    unreadable = "unreadable (see the web logs)"
    lines: List[str] = [
        "📊 Caydex marketing — weekly digest",
        f"{r['week_start'].strftime('%a %Y-%m-%d')} → {r['week_end'].strftime('%a %Y-%m-%d')} (ET)",
        "",
    ]
    # runs
    if r.get("runs") is None:
        lines.append(f"Runs: {unreadable}")
    else:
        lines.append("Runs:" if r["runs"] else "Runs: none")
        lines += [f"• {_day(d)} {text}" for d, text in r["runs"]]
        peak = r.get("memory_peak")
        if peak:
            mb, d, stage = peak
            flag = f" ⚠️ above {_n(MEMORY_ALERT_MB)} MB" if mb > MEMORY_ALERT_MB else ""
            lines.append(f"Worker memory peak: {_n(int(round(mb)))} MB ({stage}, {_day(d)}){flag}")
    lines.append("")

    # posts
    if not r["live"]:
        lines.append("Posts: none this week")
    else:
        lines.append(f"Posts: {_n(r['live'])} live — {_ordered_statuses(r['status_totals'])}")
        if r.get("posts_capped"):
            lines.append(f"  (only the first {_n(WEEK_POST_LIMIT)} posts were read)")
        lines += _capped([f"• {platform}: {_ordered_statuses(counts)}"
                          for platform, counts in sorted(r["by_platform"].items())], cap, indent="")
        exp = r.get("expired") or {}
        parts = [(exp.get("approved"), "approved but never sent", "approved but never sent"),
                 (exp.get("asked"), "not reviewed", "not reviewed"),
                 (exp.get("preview"), "preview (platform not enabled)", "previews (platform not enabled)"),
                 (exp.get("unshown"), "never shown to you", "never shown to you")]
        shown = [_plural(n, one, many) for n, one, many in parts if n]
        if shown:
            lines.append("Expired: " + " · ".join(shown))
        rej = r["rejections"]
        if rej["rows"]:
            by = rej["by_reason"]
            reason_parts = [f"{REJECT_REASONS[c]} {_n(by[c])}" for c in REJECT_REASONS if by.get(c)]
            if by.get("none"):
                reason_parts.append(f"no reason {_n(by['none'])}")
            lines.append(f"Rejected {_n(len(rej['rows']))}: " + " · ".join(reason_parts))
            lines += _capped([f"❌ {p} · run {d} · {why}" for p, d, why in rej["rows"]], cap)
        if r["failed_rows"]:
            lines.append(f"Failed {_n(len(r['failed_rows']))}:")
            lines += _capped([f"✖ {p} · run {d} · {why}" for p, d, why in r["failed_rows"]], cap)
    if r.get("rehearsal"):
        lines.append(f"(+{_n(r['rehearsal'])} rehearsal rows — dry run, never publishable; not counted)")
    lines.append("")

    # engagement + followers
    if not r.get("metrics_enabled"):
        lines.append("Engagement: not measured — MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on")
    elif not r["engagement"]:
        lines.append("Engagement: no live published posts this week")
    else:
        lines.append("Engagement (latest snapshot of this week's published posts):")
        platforms = sorted(r["engagement"].items())
        for platform, e in platforms[:cap]:
            totals = _join_counts(e["totals"])
            lines.append(f"• {_label(platform)} — {_n(e['measured'])} of {_n(e['live'])} measured"
                         + (f": {totals}" if totals else ""))
            if e["top"]:
                lines.append(f"  Top: {e['top']['url']} ({_join_counts(e['top']['counts'])})")
            if e["statuses"] and set(e["statuses"]) != {"ok"}:
                lines.append("  Measure: " + " · ".join(f"{k} {_n(v)}" for k, v in sorted(e["statuses"].items())))
        if len(platforms) > cap:
            lines.append(f"… and {_n(len(platforms) - cap)} more platform(s)")
    followers = r.get("followers")
    if followers is None:
        lines.append(f"Followers: {unreadable}")
    elif not followers:
        lines.append("Followers: no snapshot yet")
    else:
        parts = []
        for platform, f in sorted(followers.items()):
            text = f"{_label(platform)} {_n(f['followers'])}"
            if f["baseline"] is None:
                text += f" (as of {f['at'].astimezone(ET).strftime('%m-%d')}; no snapshot 6+ days older yet)"
            else:
                delta = f["followers"] - f["baseline"]
                sign = "+" if delta > 0 else ("±" if delta == 0 else "-")
                text += (f" ({sign}{_n(abs(delta))} since {f['baseline_at'].astimezone(ET).strftime('%m-%d')}; "
                         f"as of {f['at'].astimezone(ET).strftime('%m-%d')})")
            parts.append(text)
        lines.append("Followers: " + " · ".join(parts))
    lines.append("")

    # /go taps
    taps = r.get("taps")
    if taps is None:
        lines.append(f"/go taps: {unreadable}")
    else:
        if taps["rows"]:
            shown = taps["rows"][: max(cap, 1)]
            more = len(taps["rows"]) - len(shown)
            tail = f" · … {_n(more)} more" if more > 0 else ""
            lines.append("/go taps: " + " · ".join(f"{c} {_n(h)}" for c, h in shown) + tail
                         + f" — {_n(taps['total'])} in total")
        else:
            lines.append("/go taps: none")
        if taps.get("unreadable_rows"):
            lines.append(f"  ({_n(taps['unreadable_rows'])} unreadable row(s) not counted)")
    store = r.get("store")
    if store == "prelaunch":
        lines.append("Pre-launch: every /go tap lands on the Coming soon page (MARKETING_APP_STORE_URL is unset).")
    elif store == "invalid":
        lines.append("⚠️ MARKETING_APP_STORE_URL is set but invalid — /go falls back to the Coming soon page.")

    # X spend
    spend = r.get("spend")
    if spend is None:
        lines.append(f"X spend: {unreadable}")
    else:
        if spend["budget"]:
            pct = 100.0 * spend["total"] / spend["budget"]
            head = f"X spend {spend['month']} (UTC, to date): {_usd(spend['total'])} of the ${spend['budget'] / 1e6:.2f} cap ({pct:.0f}%)"
        else:
            # A zero budget is not "uncapped": `outlet_x.configured()` requires a budget > 0, so X is
            # not an enabled platform and nothing posts or reads there (a retract delete is still
            # charged, so the total can be non-zero).
            head = (f"X spend {spend['month']} (UTC, to date): {_usd(spend['total'])} — X is OFF: no posts or reads "
                    f"(MARKETING_X_MONTHLY_BUDGET_USD is 0 or unset)")
        lines.append(head)
        if spend["by_op"]:
            ops = [f"{op} {_usd(m)}" for op, m in spend["by_op"]]
            shown = ops[: max(cap, 1)]
            tail = f" · … {_n(len(ops) - len(shown))} more" if len(ops) > len(shown) else ""
            lines.append("  " + " · ".join(shown) + tail)
    lines.append("")

    # review latency, runway, waiting, metrics job
    lat = r.get("latency") or {}
    if lat.get("n"):
        lines.append(f"Review time: median {_duration(lat['median_s'])} · longest {_duration(lat['max_s'])} "
                     f"({_plural(lat['n'], 'decision', 'decisions')})")
    else:
        lines.append("Review time: no decisions this week")
    run = r.get("runway")
    if run is None:
        lines.append(f"Content pool: {unreadable}")
    elif not run.get("pool"):
        lines.append("⚠️ Content pool: EMPTY — every posting day will be skipped (empty_pool)")
    else:
        head = f"Content pool: {_n(run['unused'])} of {_n(run['pool'])} lessons unused"
        if run.get("first_repeat") is None:
            lines.append(head)
        elif run["unused"] == 0:
            lines.append(f"{head} — lessons repeat from {run['first_repeat'].strftime('%a %Y-%m-%d')}")
        else:
            lines.append(f"{head} — the first repeat is due {run['first_repeat'].strftime('%a %Y-%m-%d')}")
    esc = r.get("escalated")
    if esc is None:
        lines.append(f"Outcome unknown: {unreadable}")
    elif not esc:
        lines.append("Outcome unknown: none waiting for your answer")
    else:
        lines.append(f"⚠️ Waiting for your answer (outcome unknown): {_n(len(esc))}")
        lines += _capped([f"{p} · run {d} · since {since}" for p, d, since in esc], cap)
    lines.append(_metrics_job_line(r.get("metrics_job"), r.get("metrics_enabled")))
    return "\n".join(lines).strip()


def _metrics_job_line(job: Optional[Dict[str, Any]], enabled: Any) -> str:
    """The measure step's day-job state (`notification_job_state`). Its `last_error` is shown whenever
    it is non-empty: as a `note:` when the last run SUCCEEDED (`finish_scheduled_job` sets `run_day` to
    the ET day of `last_run_at` only on success — a successful day may still carry a note, e.g. a
    platform paused for the day), as a `last error:` otherwise (including when either time is
    unreadable — never a guess in the reassuring direction)."""
    if not enabled:
        return "Metrics job: off (MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on)"
    if job is None:
        return "Metrics job: state unreadable (see the web logs)"
    parts = []
    run_day = job.get("run_day")
    try:
        done = date.fromisoformat(str(run_day)[:10]) if run_day else None
    except ValueError:
        done = None
    parts.append(f"last completed {done.isoformat()}" if done else "never completed")
    attempt = _parse_ts(job.get("last_run_at"))
    attempt_et = _et_local(attempt) if attempt is not None else None
    if attempt_et is not None:
        parts.append(f"last attempt {attempt_et.strftime('%m-%d %H:%M ET')}")
    items = _count(job.get("items_written"))
    if items is not None and attempt_et is not None:
        parts.append(f"{_plural(items, 'post', 'posts')} written")
    if job.get("enabled") is False:
        parts.append("DISABLED in notification_job_state")
    err = job.get("last_error")
    if err not in (None, ""):
        succeeded = done is not None and attempt_et is not None and attempt_et.date() == done
        parts.append(f"{'note' if succeeded else 'last error'}: {_clean(err, _ERROR_CAP) or '(unreadable)'}")
    return "Metrics job: " + " · ".join(parts)


def compose_digest(report: Dict[str, Any]) -> str:
    """The digest as ONE plain-text Telegram message of at most MAX_DIGEST_UNITS UTF-16 units. Pure.
    Rows are capped first (each list section shows fewer rows, with "… and N more"); only if that is
    not enough is the text cut at a line break, with a marker saying so."""
    text = ""
    for cap in _ROW_CAPS:
        text = _render_digest(report, cap)
        # Defence in depth: a lone surrogate cannot be encoded (or sent); every dynamic string is
        # already cleaned, so this changes nothing unless a future section forgets to.
        text = "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)
        if utf16_len(text) <= MAX_DIGEST_UNITS:
            return text
    room = MAX_DIGEST_UNITS - utf16_len(_CUT_MARKER)
    cut = _truncate_utf16(text, room)
    newline = cut.rfind("\n")
    if newline >= len(cut) // 2:
        cut = cut[:newline]
    return cut + _CUT_MARKER


# ── the reads (I/O) ───────────────────────────────────────────────────────────


async def _optional(what: str, coro: Awaitable[Any]) -> Any:
    """A secondary read: its failure is logged and its section says "unreadable" — never zero."""
    try:
        return await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing digest: %s could not be read (%s: %s) — that section says unreadable",
                       what, type(e).__name__, e)
        return None


async def _runway(svc: Any, today: date) -> Dict[str, Any]:
    from app.services.marketing import content_pool

    # The corpus load is CPU work (cached per process after the first call): off the event loop.
    pool = await asyncio.to_thread(content_pool.eligible_keys)
    limit = len(pool) + selection.RECENT_WINDOW
    with_today = await svc.recent_source_refs(today + timedelta(days=1), limit)
    before_today = await svc.recent_source_refs(today, limit)
    return pool_runway(pool, with_today, today=today, today_picked=list(with_today) != list(before_today))


async def _job_state(svc: Any, job: str) -> Dict[str, Any]:
    """One day job's `notification_job_state` row — a direct, read-only select by its key — or its
    never-ran default when the row does not exist yet (the job's first claim creates it). Raises when the
    read fails (`MarketingRunError`) or answers anything but a list of row objects (JobStateUnreadable):
    an unreadable state is never read as "never ran"."""
    res = await _exec(
        svc.sb.table(_JOB_STATE_TABLE)
        .select("job,enabled,run_day,claim_at,last_run_at,last_error,items_written")
        .eq("job", job).limit(1),
        op="digest_job_state", job=job,
    )
    data = getattr(res, "data", None)
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise JobStateUnreadable(f"{job}: the job-state read answered {type(data).__name__}, not a list of rows")
    if not data:
        return {"job": job, "enabled": True, "run_day": None}
    if data[0].get("job") != job:   # another job's row: the key filter was lost — never report it as ours
        raise JobStateUnreadable(f"{job}: the job-state read answered another job's row")
    return data[0]


async def _spend(svc: Any, now: datetime) -> Dict[str, int]:
    return await svc.spend_by_op_since("x", month_start_utc(now))


def _store_state() -> str:
    if smart_link.store_url() is not None:
        return "live"
    return "invalid" if str(settings.MARKETING_APP_STORE_URL or "").strip() else "prelaunch"


async def gather_digest(svc: Any, now: datetime) -> Dict[str, Any]:
    """Read everything the digest needs and summarise it. The week's posts are the core: a failure there
    RAISES (the attempt fails and a later tick retries). Every other read degrades its own section to
    "unreadable" (logged WARNING) — never to an empty or zero section."""
    from app.services.marketing import outlet_x

    now = _aware(now)
    today = _et_day(now)
    week_start, week_end = digest_week(today)
    start, end = week_bounds(week_start, week_end)
    posts = await svc.list_posts_created_between(start, end, limit=WEEK_POST_LIMIT)
    runs = await _optional("the week's runs", svc.list_runs_between(week_start, week_end))
    measured = await _optional("follower snapshots",
                               svc.list_measurable_posts(since=now - FOLLOWER_LOOKBACK, limit=MEASURED_POST_LIMIT))
    if measured is not None and len(measured) >= MEASURED_POST_LIMIT:
        logger.warning("marketing digest: %d published posts in %s — the follower snapshots of the newest may "
                       "be missing", len(measured), FOLLOWER_LOOKBACK)
    link_hits = await _optional("/go taps", svc.link_hits_between(week_start, week_end))
    spend = await _optional("X spend", _spend(svc, now))
    escalated = await _optional("escalated posts", svc.list_posts_filtered(
        status="queued", order="claimed_at", limit=ESCALATED_LIMIT, not_null=("metadata->>escalated_at",)))
    runway = await _optional("the content pool's runway", _runway(svc, today))
    job = await _optional("the metrics job's state", _job_state(svc, JOB_METRICS))
    return summarize_week(
        now=now, week_start=week_start, week_end=week_end, posts=posts, runs=runs, measured=measured,
        link_hits=link_hits, spend=spend, budget_micros=outlet_x.budget_micros(), escalated=escalated,
        runway=runway, metrics_job=job,
        metrics_enabled=bool(settings.MARKETING_ENABLED and settings.MARKETING_METRICS_ENABLED),
        store=_store_state(), posts_capped=len(posts) >= WEEK_POST_LIMIT,
    )


# ── delivery ──────────────────────────────────────────────────────────────────


#: Seconds Telegram asked us to wait when its 429 named none.
_DEFAULT_RETRY_AFTER = 60
#: One pacer for both reports, so a health alert and a digest in the same tick are spaced like every
#: other message to the review chat (Telegram: about one message a second per chat).
_PACER = _Pacer()


async def _send_one(chat_id: int, text: str, *, what: str) -> Tuple[bool, Optional[str], bool]:
    """Send ONE plain-text message (never split, never a `parse_mode`). Returns (sent, why not,
    rate_limited). A 429 sets the SHARED flood back-off (`review_service._rate_limited_until`), so the
    review sweep and the feed hold off too and no report claims its day until it has passed; any other
    Telegram failure is logged and retried on a later tick."""
    if utf16_len(text) > MAX_DIGEST_UNITS:   # never: compose_digest fits — but never two messages either
        text = _truncate_utf16(text, MAX_DIGEST_UNITS - 1) + "…"
    try:
        await _PACER.wait()
        await telegram.send_message(chat_id, text)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        wait = e.retry_after if e.retry_after is not None else _DEFAULT_RETRY_AFTER
        review_service._rate_limited_until = time.monotonic() + wait
        logger.warning("marketing %s: Telegram flood control (retry_after=%s s) — not sent; retried after the "
                       "back-off", what, e.retry_after)
        return False, f"telegram flood control (retry_after={e.retry_after})", True
    except TelegramException as e:
        logger.warning("marketing %s: NOT sent (%s: %s) — retried on a later tick", what, type(e).__name__, e)
        return False, f"telegram: {type(e).__name__}: {e}", False
    logger.info("marketing %s SENT (%d UTF-16 units)", what, utf16_len(text))
    return True, None, False


def _ready(counters: Dict[str, int]) -> Optional[int]:
    """The review chat when the bot is configured and Telegram's flood back-off is not open; else None
    (the counters say why). Checked BEFORE any claim, so an open back-off takes no claim at all."""
    chat_id = review_service.review_chat_id()
    if not review_service.is_configured() or chat_id is None:
        return None
    if time.monotonic() < review_service._rate_limited_until:
        counters["rate_limited"] = 1
        return None
    return chat_id


def _tally(counters: Dict[str, int], sent: bool, rate_limited: bool) -> None:
    """Count one send's outcome (a health cycle may send two messages: the final word and the alert)."""
    if sent:
        counters["sent"] += 1
    elif rate_limited:
        counters["rate_limited"] += 1
    else:
        counters["failed"] += 1


async def _health_work(chat_id: int, at: datetime, counters: Dict[str, int]) -> Tuple[bool, int, Optional[str]]:
    """Today's run, judged by `evaluate_run_health`; one message when something is wrong. Done (True)
    when there is nothing to say or the message went out."""
    today = _et_day(at)
    run = await get_marketing_run_service().get_run_for_date(today)
    counters["checked"] += 1
    text = evaluate_run_health(run, run_date=today, posting_day=selection.is_posting_day(today), now=at,
                               max_attempts=settings.MARKETING_MAX_RUN_ATTEMPTS,
                               stale_seconds=settings.MARKETING_RUN_STALE_SECONDS)
    if text is None:
        logger.info("marketing run health %s: nothing to report (status=%s)", today,
                    run.get("status") if isinstance(run, dict) else None)
        return True, 0, None
    sent, why, limited = await _send_one(chat_id, text, what=f"run-health alert for {today}")
    _tally(counters, sent, limited)
    return (True, 1, None) if sent else (False, 0, why)


async def _final_work(chat_id: int, at: datetime, counters: Dict[str, int]) -> Tuple[bool, int, Optional[str]]:
    """YESTERDAY's run, judged by `evaluate_run_final` against WHEN the nightly check judged it — read
    first, from that check's own job state (`nightly_check_time`); one message when the check left
    something unsaid, or never went out. Done (True) when there is nothing to add or the message went
    out. An unreadable job state RAISES before the run is read: `run_day_job` logs it and leaves the day
    open, so a later tick retries — never a word that may repeat (or miss) one on a guess."""
    run_date = _et_day(at) - timedelta(days=1)
    svc = get_marketing_run_service()
    checked_at, how = nightly_check_time(await _job_state(svc, JOB_HEALTH), run_date)
    if how == NIGHTLY_TIME_UNKNOWN:
        logger.warning("marketing run health (final) %s: the nightly check succeeded that day but its record no "
                       "longer says when (a later attempt overwrote last_run_at) — judged from %s, the earliest it "
                       "could have run", run_date, _et_stamp(checked_at))
    elif how == NIGHTLY_LATER_DAY:
        logger.warning("marketing run health (final) %s: a later day's nightly check succeeded before this word "
                       "went out, so whether that day's own check did is unknown — every outcome but a good day "
                       "is reported (that night's alert may be repeated)", run_date)
    elif how == NIGHTLY_NEVER:
        logger.info("marketing run health (final) %s: no nightly check went out for that day (none succeeded) — "
                    "every outcome but a good day is reported", run_date)
    run = await svc.get_run_for_date(run_date)
    counters["checked"] += 1
    text = evaluate_run_final(run, run_date=run_date, posting_day=selection.is_posting_day(run_date), now=at,
                              max_attempts=settings.MARKETING_MAX_RUN_ATTEMPTS,
                              stale_seconds=settings.MARKETING_RUN_STALE_SECONDS, checked_at=checked_at,
                              nightly=how)
    if text is None:
        logger.info("marketing run health (final) %s: nothing to add to the nightly check of %s (status=%s)",
                    run_date, _et_stamp(checked_at) if checked_at is not None else "(none)",
                    run.get("status") if isinstance(run, dict) else None)
        return True, 0, None
    sent, why, limited = await _send_one(chat_id, text, what=f"final run-health word for {run_date}")
    _tally(counters, sent, limited)
    return (True, 1, None) if sent else (False, 0, why)


#: (job, due, work) of the two run-health jobs, in the order a tick runs them: yesterday's final word
#: before today's alert, so the review chat reads in day order.
_HEALTH_JOBS = (
    (JOB_HEALTH_FINAL, final_due, _final_work),
    (JOB_HEALTH, health_due, _health_work),
)


async def health_cycle(*, now: Optional[datetime] = None) -> Dict[str, int]:
    """The run-health jobs — `marketing_run_health_final` (the first tick at/after `final_hour_et()`, 16:00
    ET by default, on the day after a posting day: yesterday's run, `evaluate_run_final`) and
    `marketing_run_health` (the first tick at/after `health_hour_et()`, 22:00 ET by default, on a posting
    day: today's run, `evaluate_run_health`). Each is its own day job, so at most one message per job per
    ET day; one tick may run both (a web service down from 16:00 to 22:00), the final word FIRST — it
    reads the nightly check's job state before today's check can claim it. Each runs behind the Telegram
    back-off check, and a failure in one job never stops the other. Never raises (except
    CancelledError)."""
    counters = {"checked": 0, "sent": 0, "failed": 0, "rate_limited": 0}
    try:
        at = _aware(now)   # ONE instant for both jobs, so a tick cannot straddle an hour between them
    except Exception as e:
        counters["failed"] += 1
        logger.error("marketing run health: unreadable tick time %r (%s: %s) — skipped", now, type(e).__name__, e)
        return counters
    for job, due, work in _HEALTH_JOBS:
        try:
            if not due(at):
                continue
            chat_id = _ready(counters)
            if chat_id is None:
                break   # not configured, or the shared flood back-off is open: no claim at all
            failed, limited = counters["failed"], counters["rate_limited"]
            outcome = await metrics_service.run_day_job(
                job, lambda w=work, c=chat_id: w(c, at, counters), due=due,
                stale_seconds=JOB_STALE_SECONDS, now=at)
            if outcome == metrics_service.FAILED and (counters["failed"], counters["rate_limited"]) == (failed, limited):
                counters["failed"] += 1   # the work raised (a ledger error) — logged by run_day_job
        except asyncio.CancelledError:
            raise
        except Exception as e:
            counters["failed"] += 1
            logger.error("marketing run health: job %s failed (%s: %s) — retried on a later tick", job,
                         type(e).__name__, e, exc_info=True)
    return counters


def _this_monday(today: date) -> date:
    """The digest's `owed_since`: a success recorded on this week's Monday also covers the Tuesday
    catch-up, which therefore runs only when Monday's never succeeded."""
    return today - timedelta(days=today.weekday())


async def _digest_work(chat_id: int, at: datetime, counters: Dict[str, int]) -> Tuple[bool, int, Optional[str]]:
    report = await gather_digest(get_marketing_run_service(), at)
    text = compose_digest(report)
    sent, why, limited = await _send_one(
        chat_id, text, what=f"weekly digest {report['week_start']}..{report['week_end']}")
    _tally(counters, sent, limited)
    return (True, 1, None) if sent else (False, 0, why)


async def digest_cycle(*, now: Optional[datetime] = None) -> Dict[str, int]:
    """The weekly digest — job `marketing_digest_weekly`, Monday at/after 09:00 ET with a Tuesday
    catch-up when Monday's never succeeded. Nothing at all (no read, no claim) unless
    MARKETING_DIGEST_ENABLED — even when called directly, not only through the tick's gate. Never raises
    (except CancelledError)."""
    counters = {"sent": 0, "failed": 0, "rate_limited": 0}
    try:
        if not settings.MARKETING_DIGEST_ENABLED:
            return counters   # the switch is fail-closed here too, not only in the tick (no I/O at all)
        at = _aware(now)
        if not digest_due(at):
            return counters
        chat_id = _ready(counters)
        if chat_id is None:
            return counters
        outcome = await metrics_service.run_day_job(
            JOB_DIGEST, lambda: _digest_work(chat_id, at, counters), due=digest_due,
            stale_seconds=JOB_STALE_SECONDS, now=at, owed_since=_this_monday)
        if outcome == metrics_service.FAILED and not (counters["failed"] or counters["rate_limited"]):
            counters["failed"] = 1   # the work raised (a core ledger read) — logged by run_day_job
    except asyncio.CancelledError:
        raise
    except Exception as e:
        counters["failed"] += 1
        logger.error("marketing digest: the step failed (%s: %s) — retried on a later tick", type(e).__name__, e,
                     exc_info=True)
    return counters
