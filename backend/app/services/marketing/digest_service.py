"""
The marketing WEEKLY DIGEST and the nightly RUN-HEALTH alert (design doc §12.11): two Telegram reports
built ONLY from our own database. Neither calls a platform (rules/marketing.md §2 — only the publisher's
own steps may), so neither costs anything.

Both are the LAST steps of the publisher tick (`publisher_service.publisher_tick`), after the review
sweep and the feed, so they never delay a post or a review message. Both are day-keyed jobs on the ET
calendar run through `metrics_service.run_day_job` (the claim is
`notification_jobs.claimed_scheduled_job`; at most three claimed attempts per ET day per process; no
database read at all while a job is not due or after this process finished it today — but for the one
read-only look at the records `_final_owed` takes, once per process, on a day after a rest day: below):

* `health_cycle()` — two day jobs, each at most one message per ET day. Their hours follow the
  worker's run hour, MARKETING_RUN_HOUR_ET: a worker variable this process cannot read, MIRRORED by
  the web setting of the same name (the owner sets both; a test pins the two defaults equal), read at
  call time (`run_hour_et`):
  - `marketing_run_health`, the first tick at/after `health_hour_et()` on a POSTING day
    (`selection.is_posting_day`: MARKETING_POST_WEEKDAYS, read at call time — Batch 3; the final word
    below follows it the same way): the run hour plus MARKETING_MAX_RUN_ATTEMPTS, capped at 23 — 22:00
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
    would mark that day's own nightly check done. A PAST day is judged a posting day by its own record
    too (B3PD-2, `judged_posting_day`): when today's MARKETING_POST_WEEKDAYS no longer lists yesterday
    but its nightly check succeeded for it, or its run row shows a posting day's work, the final word is
    still due (`_final_owed`: one read-only look at those two records, once per process on a day after a
    rest day; under an unchanged calendar they always say no, so nothing is claimed or sent).
* `digest_cycle()` — job `marketing_digest_weekly` (MARKETING_DIGEST_ENABLED, checked here as well as
  in the tick), Monday at/after 09:00 ET with a Tuesday catch-up
  when Monday's never succeeded (`owed_since` = this week's Monday). It covers the previous
  Monday–Sunday in ET dates: `gather_digest` reads the ledger, the pure `summarize_week` turns the rows
  into numbers and the pure `compose_digest` writes ONE plain-text message within Telegram's 4,096
  UTF-16 units (rows are capped first; a hard cut at a line break is the last resort). Right under the
  date line sits the weekly COST line (`weekly_cost`, `_cost_lines`), also from our database only and in
  integer micro-dollars: X exact to our charges journal (each entry by its own time, so a metrics read
  on an old post lands in the week it was billed), Gemini / the worker / the Upload-Post plan fee as
  labelled estimates (≈), the week before beside it, and a ⚠️ line when last week's usage (X + Gemini +
  worker) passed MARKETING_WEEKLY_COST_WARN_USD. Each part degrades alone to "unreadable" and the total
  then reads "≥" — never a wrong number, never a silent 0. In place of the old this-week engagement
  block sits the follows-first 4-WEEK SCORECARD (`scorecard`, 2026-10-15): one row per (platform,
  format) over the published posts of the 4 ET weeks ending that Sunday — posts; followers and their
  change against the snapshot closest to 4 weeks earlier (dated); TikTok's follows and profile visits
  from posts (a floor, "≥", when only some posts gave them); the median and best views / impressions
  over posts whose latest snapshot was taken at 24 h or older; a video row's median watch time and
  share watched; the posts not measured `ok` — sorted by follower gain, then median reach, with "n/a"
  (never 0) for a figure no post gave and ONE "Top post this week" link line under it. It is fed by the
  same 35-day read as the follower line, read in pages oldest first (`metrics_service.list_published_posts`,
  MD-7) so the newest posts are never the ones a limit cuts; a read that still stops early is said under
  the scorecard with the count it took in. The posts section also names the days whose posts were made
  as text where the picture should have gone (`metadata.image_fallback`), each reason in the owner's
  words.

Delivery follows `publish_feed.feed_cycle`: the review bot configured + its review chat, the shared
Telegram flood back-off (`review_service._rate_limited_until` — while it is open no claim is taken, and
a 429 here holds the review sweep off too), the ONE pacer every sender to the review chat shares
(`telegram_pacing.PACER`, 2026-10-22), plain text only (no `parse_mode`). A job is marked done only
after its message went out, so a failure is retried on a later
tick (a 429 inside the send spends that claimed attempt — `run_day_job` counts every claim — but
while the back-off is open no attempt is claimed at all). Neither cycle raises (except
CancelledError).

What may reach a message: counts, dates, statuses, platform names, post formats (video / image / text —
a "Formats:" line under the per-platform counts, and the format closing each rejected / failed row;
drop 1), measures (seconds, percentages) re-validated against the measure step's bounds, fixed owner
words for a code (a picture fallback, a series outcome — never the stored string), links to our own
posts, and
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
from typing import Any, Awaitable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from app.config import settings
from app.integrations import telegram
from app.integrations.telegram import MAX_MESSAGE_CHARS, TelegramException, TelegramRateLimitException
from app.schemas.marketing import (
    IMAGE_FALLBACK_REASONS,
    POST_FORMATS,
    POST_METADATA_IMAGE_FALLBACK,
    RUN_STAGES,
    parse_content_classes,
)
from app.services.marketing import (
    metrics_service,
    outlets,
    review_service,
    selection,
    smart_link,
    telegram_pacing,
)
from app.services.marketing.outlet_base import scrub
from app.services.marketing.outlet_upload_post import PLATFORMS as UPLOAD_POST_PLATFORMS
from app.services.marketing.review_service import REJECT_REASONS, _truncate_utf16, utf16_len
from app.services.marketing.run_service import (
    _exec,
    _parse_ts,
    _touched,
    charges_between,
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
#: The published posts of this many days back are read ONCE and feed both the follower snapshots
#: (`metrics.account`) and the 4-week scorecard (2026-10-15: 21 → 35 days): the scorecard's 28 days plus
#: room for the post holding the snapshot ~4 weeks before the newest — a snapshot lives on the post that
#: was the newest of its platform when it was taken, published up to days before it.
FOLLOWER_LOOKBACK = timedelta(days=35)

#: The scorecard covers the 4 ET weeks (these many days) ending on the digest week's Sunday.
SCORECARD_DAYS = 28
#: The scorecard's follower change is measured against the snapshot CLOSEST to this long before the newest
#: (among those at least FOLLOWER_BASELINE_AGE older; the older one on a tie) and dated by its own day, so
#: a sparse history (X reads its account weekly) or a younger account never claims a full 4 weeks.
FOLLOWER_CHANGE_TARGET = timedelta(days=28)
#: A follower count older than this at the digest carries its own date ("as of MM-DD") in the scorecard.
FOLLOWER_STALE = timedelta(days=2)
#: Views, impressions, watch time and % come only from a latest snapshot taken when its post was at least
#: this many hours old (`age_h`): a post read at 2 h would pull a median down for nothing.
SCORE_MIN_AGE_H = 24.0

#: Bounded reads (the digest runs on the single uvicorn worker).
WEEK_POST_LIMIT = 500
#: The 35-day published-posts read, in pages of this many rows (`metrics_service.list_published_posts`,
#: MD-7, 2026-10-16): it answers oldest first, so ONE capped read lost the NEWEST posts — the scorecard's
#: latest week. 8 platforms × 4 posting days × 5 weeks ≈ 160 today; at Batch 3's seven days with its three
#: extra videos (Facebook, LinkedIn, Threads) ~11 posts a day × 7 × 5 weeks ≈ 385 — more than one page.
MEASURED_POST_LIMIT = 300
#: At most this many pages: 37 days of runs × 16 (platform, format) pairs (the 13 of POST_FORMATS_BY_PLATFORM
#: plus Batch 3's 3 of EXTRA_POST_FORMATS_BY_PLATFORM) = 592 published posts at the most (one run a day, one
#: post per pair, published on its run day or the next), against 1,200 — a test derives the bound from the
#: two maps. Past it the scorecard says the newest may be missing (never silent).
MEASURED_POST_PAGES = 4
#: The most published posts the 35-day read takes in.
MEASURED_POST_MAX = MEASURED_POST_LIMIT * MEASURED_POST_PAGES
ESCALATED_LIMIT = 20
#: The X journal read behind the cost line (`list_charge_rows_since` RAISES past it: never a partial
#: sum). About 20-40 X rows are touched in two weeks at four posts a week, about double at Batch 3's seven
#: (X never posts a second format, so the extra videos add none). Its probe (limit + 1) must
#: fit in one PostgREST response (max-rows ≈1,000): at 1,000 the probe row could never arrive.
X_CHARGE_ROW_LIMIT = 500

#: The weekly COST line (`weekly_cost`) — integer micro-dollars throughout, so it can never be NaN or
#: inf. X is exact to OUR ledger; the rest are labelled estimates (≈):
#: * Gemini: every token the writer and the judge spent (`marketing_scripts.tokens_used`) at ONE
#:   blended, upper-side rate of $1.50 per 1M (writer gemini-2.5-flash $0.30 in / $2.50 out; judge
#:   gemini-3.8-flash $0.75 / $3.75 through 2026-12-31, DOUBLED from 2027-01-01 — revisit then,
#:   documents/OWNER_TASKS.md §2.5).
#: * worker: Railway list prices ($10 per GB-month, $20 per vCPU-month, over 30 days, in nano-dollars
#:   per second) for every second a run's stages took, at its highest memory peak and 4 busy vCPUs —
#:   upper-side (Railway meters actual use). The hourly no-op ticks and the web service (shared with
#:   the whole app) are not counted.
#: * Upload-Post: the plan's fixed monthly fee (MARKETING_UPLOAD_POST_MONTHLY_USD) × 7 / 30.4375.
GEMINI_MICROS_PER_MILLION_TOKENS = 1_500_000
RAILWAY_NANO_PER_GB_SECOND = 3_860
RAILWAY_NANO_PER_VCPU_SECOND = 7_720
#: The marketing-worker service: 4 GB (marketing/railway.toml) and at least 4 vCPUs (its render runs
#: four x264 threads from the cgroup quota) — an ASSUMPTION, labelled ≈.
WORKER_VCPUS = 4
WORKER_MEMORY_MB = 4096
#: A stage time beyond this is not a measurement (a worker tick's deadline is 30 minutes).
STAGE_SECONDS_MAX = 3600
#: The run timings that are wall seconds of the worker's own work (marketing/main.py: `preflight_s`
#: and one `<stage>_s` per media stage); the memory keys and any unknown key are not billed.
_BILLED_TIMINGS = ("preflight_s",) + tuple(f"{s}_s" for s in RUN_STAGES[1:])
#: One month of the fee, in days × 10,000 (365.25 / 12 = 30.4375).
_MONTH_DAYS_X10000 = 304_375
#: A cost part beyond ±$1,000,000 is not a week of this engine: it reads "unreadable".
_MICROS_BOUND = 10 ** 12

#: Telegram's message limit, in UTF-16 code units.
MAX_DIGEST_UNITS = MAX_MESSAGE_CHARS
#: Per-section row caps tried in order until the digest fits; then a hard cut.
_ROW_CAPS = (8, 4, 2, 0)
#: The scorecard's row caps (None = every row): it is the digest's headline, so it stays whole until the
#: other list sections are down to their counts, and only then shrinks (2026-10-15).
_SCORECARD_CAPS: Tuple[Optional[int], ...] = (None, 8, 4, 2)
#: Every (other sections' cap, scorecard's cap) pass `compose_digest` tries, in order.
_PASSES: Tuple[Tuple[int, Optional[int]], ...] = (
    tuple((cap, _SCORECARD_CAPS[0]) for cap in _ROW_CAPS)
    + tuple((_ROW_CAPS[-1], score_cap) for score_cap in _SCORECARD_CAPS[1:]))
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
    "unrenderable_text": "the render stage could not draw the script or the post image (a glyph Inter lacks, "
                         "e.g. an emoji, or text that cannot fit) — check the render stage",
    # Drop 1: the post image stayed over its byte cap at every JPEG quality step (marketing/render.py).
    "image_too_large": "the post image stayed over its byte cap at every JPEG quality — check the render "
                       "stage in the worker logs",
    # Drop 2: create_posts answered 409 MARKETING_TEMPLATE_REFUSED (marketing/render.py stage_posts). Every
    # switch a `MarketingTemplateRefused` raise names is named here too (pinned by test_marketing_metrics.py):
    # the class switch (drop 2a) and the per-series switch (drop 2b).
    "template_refused": "the day's company post failed the template re-check, or MARKETING_CONTENT_CLASSES / "
                        "MARKETING_NEWS_SERIES no longer lists its class or series (or this deploy no longer "
                        "ships the series) — read the web log 'create_posts TEMPLATE REFUSED' for this run",
}

#: A fallback step's outcome (`script_service.SERIES_OUTCOMES`) → the words the owner reads in
#: `_series_note`. An outcome missing here is shown as its sanitised code.
_SERIES_OUTCOME_WORDS: Dict[str, str] = {
    "no_candidates": "nothing qualified",
    "all_recent": "everything was already posted",
    "all_refused": "every candidate failed the template checks",
    "unavailable": "its data source was unavailable",
    "timeout": "its data source timed out",
    "error": "an error",
    "budget": "out of time",
}
#: The fallback outcomes that are about the data source or the machinery, not about the week's content
#: (OBS-1, 2026-10-15): `_series_note` never lets one hide behind a content outcome ("nothing qualified").
_INFRA_OUTCOMES = frozenset({"unavailable", "timeout", "error", "budget"})
#: At most this many fallback steps are named in a series note; the rest are counted ("and N more").
_SERIES_NOTE_STEPS = 3

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


def _series_name(series: Any) -> Optional[str]:
    """A series id's operator label (`selection.SERIES` names; "lesson" → "Lesson"), or None for a value
    that is not a known id — a stored string is never shown verbatim."""
    if series == selection.LESSON:
        return "Lesson"
    known = selection.SERIES_BY_ID.get(series) if isinstance(series, str) else None
    return known.name if known is not None else None


def _step_words(outcome: Any) -> str:
    """A fallback step's outcome in the owner's words; an outcome missing from _SERIES_OUTCOME_WORDS is its
    sanitised code (`_key`), anything else fixed words."""
    words = _SERIES_OUTCOME_WORDS.get(outcome) if isinstance(outcome, str) else None
    return words or _key(outcome) or "it did not post"


def _series_note(run: Any) -> str:
    """What the day posted, from the server-owned run metadata the selection mirror writes (drop 2,
    contract D13): "Money Map", or "Money Map (fell back from CEO Buys: nothing qualified)" when the day's
    first series did not post. "" when the run records no known series (a lesson-only plan, or a run from
    before drop 2). Pure; only operator labels and fixed words reach the text — never a stored string —
    so it is safe in the digest day line and in a review-bundle message.

    Every step that fell back is named, in the trail's order, each known series once — at most
    _SERIES_NOTE_STEPS, then "and N more". When there are more than that, the INFRASTRUCTURE outcomes
    (_INFRA_OUTCOMES: a data source unavailable or timed out, an error, the time budget) are kept ahead of
    the content ones (OBS-1, 2026-10-15): naming only the first step hid a later series' outage behind
    "CEO Buys: nothing qualified", and a series that never heads a chain (Insider Buys) could fail every
    week with nothing in Telegram."""
    meta = run.get("metadata") if isinstance(run, dict) and isinstance(run.get("metadata"), dict) else {}
    series = meta.get("series")
    name = _series_name(series)
    if name is None:
        return ""
    trail = meta.get("series_trail") if isinstance(meta.get("series_trail"), list) else []
    steps: List[Dict[str, Any]] = []
    seen: set = set()
    for t in trail:
        if not isinstance(t, dict) or t.get("outcome") == "chosen" or t.get("series") == series:
            continue
        step_name = _series_name(t.get("series"))
        if step_name is None or step_name in seen:   # an unknown id is never shown; a repeat once
            continue
        seen.add(step_name)
        steps.append(t)
    if not steps:
        return name
    # isinstance first: a hand-edited outcome may be a list, and a list in a frozenset raises TypeError.
    infra = [i for i, t in enumerate(steps) if isinstance(t.get("outcome"), str) and t["outcome"] in _INFRA_OUTCOMES]
    content = [i for i in range(len(steps)) if i not in infra]
    shown = sorted((infra + content)[:_SERIES_NOTE_STEPS])   # infrastructure first, then shown in trail order
    parts = [f"{_series_name(steps[i].get('series'))}: {_step_words(steps[i].get('outcome'))}" for i in shown]
    more = len(steps) - len(shown)
    tail = f"; and {_n(more)} more" if more else ""
    return f"{name} (fell back from {'; '.join(parts)}{tail})"


def _platform(row: Dict[str, Any]) -> Optional[str]:
    return _key(row.get("platform"))


def _label(platform: Optional[str]) -> str:
    return platform.upper() if platform else "?"


def _n(value: int) -> str:
    return f"{value:,}"


def _usd(micros: int) -> str:
    """Micro-dollars as "$0.123". Total: anything but an int within ±$1,000,000,000 reads "unreadable"
    (a 400-digit hand-edited amount used to raise OverflowError and take the whole digest down)."""
    if type(micros) is not int or abs(micros) > 10 ** 15:
        return "unreadable"
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


def _usd_setting_micros(name: str, value: Any) -> Optional[int]:
    """A USD setting (0-10,000) in micro-dollars, or None when it is not one. Settings refuses anything
    else at boot (Field ge/le/allow_inf_nan), so None is reachable only through a test's monkeypatch —
    and it is logged, never a crash in the tick."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        ok = False
    else:
        ok = math.isfinite(value) and 0 <= value <= 10_000
    if not ok:
        logger.warning("marketing digest: %s=%r is not a USD amount between 0 and 10,000", name, value)
        return None
    return int(round(value * 1_000_000))


def upload_post_fee_micros() -> Optional[int]:
    """The Upload-Post plan's monthly fee (MARKETING_UPLOAD_POST_MONTHLY_USD; 0 = Free) in micro-dollars,
    read at call time — or None, which makes the cost line's Upload-Post part "unreadable" (never a
    guessed $0)."""
    return _usd_setting_micros("MARKETING_UPLOAD_POST_MONTHLY_USD", settings.MARKETING_UPLOAD_POST_MONTHLY_USD)


def weekly_cost_warn_micros() -> int:
    """The weekly usage alert line (MARKETING_WEEKLY_COST_WARN_USD; 0 = off) in micro-dollars, read at
    call time; an unreadable value is the declared default (logged)."""
    micros = _usd_setting_micros("MARKETING_WEEKLY_COST_WARN_USD", settings.MARKETING_WEEKLY_COST_WARN_USD)
    if micros is None:
        micros = int(round(type(settings).model_fields["MARKETING_WEEKLY_COST_WARN_USD"].default * 1_000_000))
    return micros


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
    """The day after a posting day (ET), at/after `final_hour_et()` — a posting day on the posting days in
    force NOW. Pure (it runs before any I/O). The day after one they no longer list, but whose own record says
    it was one, is `_final_owed`'s (B3PD-2)."""
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


#: What the run-health texts say, instead of "this day's posts will not go out", about a run that FAILED
#: after its posts were recorded: its last completed stage is the last stage, `assets_ready` — the
#: worker checkpoints it only after `create_posts` answered, so every post of the day exists, and the
#: review bot offers a failed run's posts at that stage just as it offers a `media_ready` run's.
#: Worded to stay TRUE however the owner has acted since (MD-1, 2026-10-16): the bot offers those posts as
#: soon as the checkpoint lands (`review_service.bundle_offerable`; the per-post review never waited for the
#: run at all), so by the nightly check — and surely by the next day's final word — they may already be
#: approved and published, or rejected. Neither this text nor the recovered line may say they are still
#: waiting: the health jobs read the run row only, never its posts.
_POSTS_RECORDED = ("Its posts were recorded before it failed, so they go to the review chat like any day's; "
                   "anything still undecided waits there.")
#: The same for a run ABANDONED at that stage (still `in_progress` / `planned`, its liveness stale — a tick
#: that died between the `assets_ready` checkpoint and the `media_ready` write): the review bot offers a run's
#: posts at that stage whatever its status (`review_service.bundle_offerable`), so "the day is lost" is false.
_POSTS_RECORDED_STOPPED = ("Its posts were recorded before it stopped, so they go to the review chat like any "
                           "day's; anything still undecided waits there.")
#: What the "recovered" final word says about a `media_ready` run's posts — the same truth (MD-1): never
#: "waiting for review", which was false the moment the owner decided them.
_RECOVERED_POSTS = "its posts go to the review chat like any day's; anything still undecided waits there"


def _recorded_none(run: Dict[str, Any]) -> bool:
    """True when the run's `assets_ready` checkpoint says it recorded NO post: the worker's `stage_posts`
    answers {"posts_recorded": 0} when the accepted script names no outlet it posts to, and still
    checkpoints — that dict rides in the checkpoint PATCH into the run's `metadata` (marketing/main.py
    `run_pipeline`, merged by `update_run`). Only that exact count says so: a missing key (a run from
    before the count existed), a junk value or a positive count is never "none"."""
    return _count(_meta(run).get("posts_recorded")) == 0


def _posts_recorded(run: Dict[str, Any]) -> bool:
    """True when the run's last COMPLETED stage is the last stage (`assets_ready`) — its posts exist — and
    its checkpoint does not say it recorded none (`_recorded_none`: with no post there is nothing in the
    review chat, and "this day's posts will not go out" is the truth, as for a run that failed earlier)."""
    return _key(run.get("stage")) == RUN_STAGES[-1] and not _recorded_none(run)


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
    `final_hour_et()` tomorrow (`evaluate_run_final`). A run that failed AFTER stage `assets_ready` (its
    posts were recorded) never says its posts will not go out: it says they go to the review chat like any
    day's (`_POSTS_RECORDED`), at the cap and below it — never that they still wait for a decision, which
    the owner may already have made (MD-1) — and a run abandoned there says the same
    (`_POSTS_RECORDED_STOPPED`); a checkpoint that recorded no post (`_recorded_none`) is said like an
    earlier stage's failure. `skipped` for any reason but `rest_day` → the
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
        recorded = _posts_recorded(run)
        if facts["exhausted"]:
            outlook = f"all {_n(facts['cap'])} attempts used"
            if not recorded:
                outlook += "; this day's posts will not go out"
        elif facts["unknown"]:
            outlook = (f"{attempt} — the attempt count is unreadable, so whether the worker retries is unknown; "
                       f"{final_word}")
        else:
            outlook = f"{attempt}; {_retries()} — {final_word}"
        posts = f" {_POSTS_RECORDED}" if recorded else ""
        text: Optional[str] = (f"⚠️ Marketing run {day} FAILED {where}: {outlook}.{posts} Last error: "
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
        text += "."
        if not facts["fresh"] and _posts_recorded(run):
            text += f" {_POSTS_RECORDED_STOPPED}"
        text += f" {final_word[0].upper()}{final_word[1:]}."
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


def ran_as_posting_day(run: Any) -> bool:
    """True when a run row ITSELF shows that its day was a posting day when it ran — on the posting days in
    force THEN, whatever MARKETING_POST_WEEKDAYS says now (B3PD-2, 2026-10-20). Pure.

    A rest day's run is closed at its first script kick — `skipped` with skip_reason `rest_day`, its stage
    still `planned` (marketing/main.py `stage_select`) — so a run that completed a stage (its `stage` past
    `planned`), reached `media_ready` / `published`, mirrors a selection (`source_ref`), or was skipped for
    any reason but `rest_day` ran as a posting day. False for everything that does not show it: a `rest_day`
    skip, no run, an unreadable row, and a run that stopped before its first kick decided anything (failed or
    abandoned at `planned` with nothing selected, or skipped with no reason) — on those the posting days in
    force now decide (`judged_posting_day`), exactly as before."""
    if not isinstance(run, dict):
        return False
    status = _key(run.get("status"))
    reason = _key(_meta(run).get("skip_reason"))
    if status == "skipped" and reason == "rest_day":
        return False
    if status in ("media_ready", "published") or _key(run.get("stage")) in RUN_STAGES[1:]:
        return True
    if status == "skipped" and reason is not None:
        return True
    ref = run.get("source_ref")
    return isinstance(ref, str) and bool(ref.strip())


def judged_posting_day(run_date: date, *, nightly: str, run: Any) -> bool:
    """Whether `run_date` — a PAST day — was a posting day, judged from its own record whenever today's posting
    days say it was not (B3PD-2, 2026-10-20). Pure but for the setting it reads.

    True when the posting days in force NOW list it (`selection.is_posting_day` — every answer of an unchanged
    calendar); else when its nightly check SUCCEEDED for it (`nightly` is NIGHTLY_CHECKED or
    NIGHTLY_TIME_UNKNOWN, `nightly_check_time`: that check runs only on a posting day — and it may have promised
    the final word); else when its run row shows a posting day's work (`ran_as_posting_day`). So a switch that
    drops a weekday (seven days to four, made the morning after a failed day — MARKETING_POST_WEEKDAYS' own
    advice) never cancels the final word that day's alert promised. A day with no record of its own (no
    successful check, no run row that shows it) is judged on today's calendar alone, as before."""
    return (selection.is_posting_day(run_date) or nightly in (NIGHTLY_CHECKED, NIGHTLY_TIME_UNKNOWN)
            or ran_as_posting_day(run))


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
      time). A `media_ready` run's posts "go to the review chat like any day's" (`_RECOVERED_POSTS`) —
      never "are waiting for review": by now the owner may have decided them (MD-1) — or it "recorded no
      posts" (`_recorded_none`).
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
    already. A `failed` run whose last completed stage is `assets_ready` never says "This day's posts will
    not go out": its posts were recorded and go to the review chat like any day's (`_POSTS_RECORDED`),
    exactly as the nightly check says; a run that never finished but stopped at that stage is never "the
    day is lost" (`_POSTS_RECORDED_STOPPED`) — unless its checkpoint recorded no post (`_recorded_none`)."""
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
    recorded = _posts_recorded(run)
    posts_fate = _POSTS_RECORDED if recorded else "This day's posts will not go out."
    # An abandoned run at `assets_ready` is not a lost day: its posts exist and the review bot offers them.
    lost, stopped_tail = ("", f" {_POSTS_RECORDED_STOPPED}") if recorded else (" — the day is lost", "")

    if status in ("media_ready", "published"):
        if checked is None:
            return memory
        horizon = now + _FUTURE_SKEW
        finished = next((t for t in (_parse_ts(run.get("finished_at")), _parse_ts(run.get("updated_at")))
                         if t is not None and t <= horizon), None)
        if finished is None or finished <= checked:
            return None
        if status == "published":
            what = "its posts went out"
        elif _recorded_none(run):
            what = "it recorded no posts, so nothing will be posted for this day"
        else:
            what = _RECOVERED_POSTS   # never "waiting for review": the owner may have decided them (MD-1)
        return _with_memory(f"✅ Marketing run {day} recovered at {_et_stamp(finished)} — {what}.", memory)

    touched = facts["touched"]
    if checked is None or (touched is not None and touched > checked):
        if status == "failed":
            outlook = (f"all {_n(facts['cap'])} attempts used" if facts["exhausted"]
                       else f"{attempt}; {no_retry}")
            text: Optional[str] = (f"⚠️ Marketing run {day} ended FAILED {where}: {outlook}. {posts_fate} "
                                   f"{error_tail}")
        elif status == "skipped":
            text = _skip_text(run, facts, day)
        elif status in ("planned", "in_progress") and facts["fresh"]:
            resumed = f", resumed after the {_check_stamp(checked)} check" if checked is not None else ""
            text = (f"⏳ Marketing run {day} is still running {where}, {attempt}{resumed} — if this attempt "
                    f"fails, {no_retry}.")
        elif status in ("planned", "in_progress"):
            text = (f"⚠️ Marketing run {day} never finished{lost}: {status} {where}, {attempt} was "
                    f"abandoned and {no_retry}.{stopped_tail} Check the marketing-worker logs.")
        else:
            text = _unexpected_text(facts, day)
        return _with_memory(text, memory)

    # Unchanged since the nightly check: speak only where that verdict could not be final.
    if status == "failed" and not facts["exhausted"]:
        return (f"⚠️ Marketing run {day} is still FAILED {where}, {attempt}: no retry happened — check the "
                f"marketing-worker cron (hourly at :15) and its last logs. {posts_fate} {error_tail}")
    if status in ("planned", "in_progress"):
        since = _et_stamp(touched) if touched is not None else "an unreadable time"
        return (f"⚠️ Marketing run {day} never finished{lost}: {status} {where}, {attempt}, "
                f"untouched since {since}.{stopped_tail} Check the marketing-worker cron and its last logs.")
    return None


# ── the pool's runway (pure) ──────────────────────────────────────────────────


def pool_runway(pool: Iterable[Any], used: Iterable[Any], *, today: date, today_picked: bool,
                classes: Optional[FrozenSet[str]] = None,
                news_series: Optional[FrozenSet[str]] = None) -> Dict[str, Any]:
    """How long the content pool lasts. `pool` = the eligible keys (`content_pool.eligible_keys()`);
    `used` = the LESSON picks made so far (`selection.lesson_refs` of the window selection reads, today's
    included when it was made); `today_picked` = today's lesson pick is among them. Pure.

    `unused` = pool keys never picked. Selection posts every unused lesson before any repeat (its
    disjoint rotation plus the recent-picks window), one per LESSON day, so the first repeat falls on
    the (unused + 1)-th lesson day from the next one without a pick: today when it is a lesson day
    whose pick is still to come, else tomorrow onward. `first_repeat` is None for an empty pool.

    Which days are lesson days follows the content classes, as `script_service._select` reads them, on the
    posting days in force NOW (MARKETING_POST_WEEKDAYS, `selection.post_weekdays()` — Batch 3):
    - `classes` None or {"A"} (MARKETING_CONTENT_CLASSES unset — lessons only): every posting day (seven a
      week at seven days). The result is exactly the three keys `pool` / `unused` / `first_repeat`, as
      before Company Weekly.
    - a news class on: only a day whose enabled chain is the lesson alone
      (`selection.enabled_chain(plan_for(day).chain, classes, news_series) == (LESSON,)` — Saturday under
      the default calendar, and still Saturday alone at seven days while Money Map is on (Wednesday,
      Friday and Sunday run its tail); `news_series` is the per-series switch, None = every shipped
      series). A news day uses a lesson only when its whole chain falls through, which brings the repeat
      FORWARD, so the date is an upper bound: the result also carries `"no_later_than": True` and the
      digest words it so. No lesson-only day in reach → `first_repeat` None."""
    keys = {k for k in pool if isinstance(k, str) and k}
    seen = {r for r in used if isinstance(r, str)}
    unused = len(keys - seen)
    out: Dict[str, Any] = {"pool": len(keys), "unused": unused, "first_repeat": None}
    enabled = frozenset({"A"}) if classes is None else frozenset(classes)
    news_on = enabled != frozenset({"A"})
    if news_on:
        out["no_later_than"] = True
    if not keys or not selection.post_weekdays():
        return out

    def lesson_day(d: date) -> bool:
        if not selection.is_posting_day(d):
            return False
        if not news_on:
            return True
        return selection.enabled_chain(selection.plan_for(d).chain, enabled, shipped=news_series) == (selection.LESSON,)

    day = today if (lesson_day(today) and not today_picked) else today + timedelta(days=1)
    wanted = unused + 1
    found = 0
    try:
        # every 7 days hold at least one posting day (`post_weekdays()` is never empty) — and, with a news
        # class on, one lesson-only day while the posting days keep Saturday (its chain is the lesson alone);
        # a calendar without one (no "sat", every other day a news day) leaves `first_repeat` None
        for _ in range(7 * wanted + 7):
            if lesson_day(day):
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


#: The order formats are listed in (drop 1: a platform posts a video, an image card, or text);
#: any other ledger format follows, sorted.
_FORMAT_ORDER = ("video", "image", "text")


def _post_format(post: Dict[str, Any]) -> str:
    """The post's format when it is one the ledger knows (POST_FORMATS), else "unknown" — never a raw
    column value in the message."""
    fmt = post.get("format")
    return fmt if isinstance(fmt, str) and fmt in POST_FORMATS else "unknown"


def _format_counts(posts: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """{format: {status: n}} over `posts` (the live ones) — the digest's "Formats:" line."""
    out: Dict[str, Dict[str, int]] = {}
    for p in posts:
        counts = out.setdefault(_post_format(p), {})
        status = _key(p.get("status")) or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return out


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


def _top_post(posts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The week's ONE top post across every platform: the live `published` post whose latest snapshot has
    the most interactions (`_ENGAGEMENT_KEYS` — never reach), as its link and those counts (never its
    text). None when no post has an interaction; on a tie the first one read wins (deterministic)."""
    top: Optional[Dict[str, Any]] = None
    for post in posts:
        if post.get("status") != "published" or _platform(post) is None:
            continue
        counts = _last_counts(post)
        score = sum(counts.get(k, 0) for k in _ENGAGEMENT_KEYS)
        if score > 0 and (top is None or score > top["score"]):
            top = {"score": score, "url": _post_link(post),
                   "counts": {k: counts[k] for k in _ENGAGEMENT_KEYS if k in counts}}
    return top


def _followers(posts: List[Dict[str, Any]], *, now: datetime) -> Dict[str, Dict[str, Any]]:
    """Per platform: the newest `metrics.account` follower snapshot; the latest one at least
    FOLLOWER_BASELINE_AGE older (`baseline`, the Followers line's change); and the one CLOSEST to
    FOLLOWER_CHANGE_TARGET before the newest among those (`four_week_base`, the scorecard's change — the
    older one on a tie). The measure step stores the account snapshot on the newest post of a platform,
    so the older posts keep the earlier ones. A snapshot without a readable time or count, dated in the
    future, or at a time the New York clock cannot show (`_et_local`), is ignored."""
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
        # Distances from `at` (a timedelta), never `at - span`: a hand-edited newest time in the first weeks of
        # year 1 passes `_et_local` (the New York clock can show it), yet `at - FOLLOWER_CHANGE_TARGET` has no
        # date — it raised OverflowError out of the whole digest (OPS-R2-1). The same comparisons, no edge.
        older = [r for r in rows if at - r[0] >= FOLLOWER_BASELINE_AGE]
        base = max(older) if older else None
        four = min(older, key=lambda r: (abs(r[0] - at + FOLLOWER_CHANGE_TARGET), r[0])) if older else None
        out[platform] = {"followers": followers, "at": at,
                         "baseline": base[1] if base else None, "baseline_at": base[0] if base else None,
                         "four_week_base": four[1] if four else None, "four_week_base_at": four[0] if four else None}
    return out


# ── the 4-week scorecard (pure) ───────────────────────────────────────────────

#: The reach count a scorecard row reads from a post's latest snapshot, first readable one wins: X reports
#: impressions; Bluesky's AppView reports no view count at all ("no view counts"); Upload-Post's platforms
#: views, else impressions (Instagram's `impressions` aliases its views).
_REACH_KEYS: Dict[str, Tuple[str, ...]] = {"x": ("impressions",), "bluesky": ()}
_DEFAULT_REACH_KEYS: Tuple[str, ...] = ("views", "impressions")
#: The video rows (format `video` on these platforms) carry watch figures: the stored `video` key of the
#: average watch time in seconds, and of the share watched — (key, factor to a percentage, its label).
#: TikTok's completion rate is a FRACTION (0.184 = 18.4%); YouTube's average view percentage is already one.
_WATCH_KEYS: Dict[str, str] = {"tiktok": "average_time_watched", "youtube": "average_view_duration_seconds"}
_SHARE_KEYS: Dict[str, Tuple[str, float, str]] = {
    "tiktok": ("full_video_watched_rate", 100.0, "watched to the end"),
    "youtube": ("average_view_percentage", 1.0, "avg viewed"),
}
#: TikTok's per-post follows and profile visits, SUMMED over a row's posts (from every post's latest
#: snapshot, whatever its age: a young post's follows are follows) — (stored key, label). A sum over only
#: SOME of the row's posts (one never read, or read without the field) is a floor and reads "≥ N", like the
#: cost line's partial total — never passed off as the row's total.
_SUM_KEYS: Tuple[Tuple[str, str], ...] = (("new_followers", "follows from posts"), ("profile_views", "profile visits"))
#: A row's "reads:" — how many of its posts are not measured `ok` (never read yet first; "other" = a status
#: the digest does not know).
_READ_ORDER: Tuple[str, ...] = ("unread",) + tuple(s for s in _MEASURE_STATUSES if s != "ok") + ("other",)
#: An `image_fallback` code that is not in IMAGE_FALLBACK_REASONS, in fixed words: the stored value never
#: reaches the message.
_UNKNOWN_FALLBACK = "a reason this digest does not know"


def _read_at_24h(last: Dict[str, Any]) -> bool:
    """Was this snapshot taken when its post was at least SCORE_MIN_AGE_H old (`age_h`)? An unreadable age
    (missing, a bool, text, NaN) is not — never a guess in the flattering direction."""
    age = last.get("age_h")
    return (not isinstance(age, bool) and isinstance(age, (int, float)) and math.isfinite(age)
            and age >= SCORE_MIN_AGE_H)


def _video_measure(video: Dict[str, Any], key: Optional[str]) -> Optional[float]:
    """A stored video measure re-validated against the measure step's own bounds, as they are NOW
    (`metrics_service.measure_bounds`: an average watch time at most twice MARKETING_MAX_VIDEO_SECONDS) —
    the digest trusts the ledger no more than the measure step trusts a platform."""
    bounds = metrics_service.measure_bounds(key) if key else None
    return metrics_service.normalize_measure(video.get(key), *bounds) if bounds else None


def _reach_label(platform: str, used: set) -> Optional[str]:
    """What a row's reach is called: None for a platform that reports no view count (Bluesky); the one
    count its posts used ("views" / "impressions"), "views/impressions" when they mixed, else the
    platform's first choice."""
    keys = _REACH_KEYS.get(platform, _DEFAULT_REACH_KEYS)
    if not keys:
        return None
    if len(used) > 1:
        return "/".join(k for k in keys if k in used)
    return next(iter(used)) if used else keys[0]


def _format_rank(fmt: str) -> Tuple[int, str]:
    return (_FORMAT_ORDER.index(fmt), "") if fmt in _FORMAT_ORDER else (len(_FORMAT_ORDER), fmt)


def scorecard(posts: List[Dict[str, Any]], *, week_end: date,
              followers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The follows-first 4-week scorecard: one row per (platform, format) over the live `published` posts
    published in the SCORECARD_DAYS ET days ending `week_end` (`posts` = the 35-day read). Pure.

    Each row: its posts; the platform's followers and their change against `four_week_base` (`_followers`
    — a platform-wide figure, the same on each of its format rows); the median and best reach over the
    posts whose latest snapshot was taken at SCORE_MIN_AGE_H or older (`_read_at_24h`) and the number of
    those posts; on a TikTok / YouTube video row the median watch time and share watched over the same
    posts, and on TikTok the summed follows and profile visits from posts with the number of posts each
    sum covers (`sum_n`: below `posts`, the sum is a floor); and how many posts are not measured `ok`. A
    figure no post can give is None — "n/a" in the message, never 0. Rows sort by follower gain, then
    median reach (an unknown one last), then platform and format."""
    first = week_end - timedelta(days=SCORECARD_DAYS - 1)
    start, end = week_bounds(first, week_end)
    acc: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for post in posts:
        if not isinstance(post, dict) or post.get("status") != "published" or review_service.is_rehearsal(post):
            continue
        platform, published = _platform(post), _parse_ts(post.get("published_at"))
        if platform is None or published is None or not start <= published < end:
            continue
        fmt = _post_format(post)
        row = acc.setdefault((platform, fmt), {"posts": 0, "reach": [], "used": set(), "sums": {}, "sum_n": Counter(),
                                               "watch": [], "share": [], "reads": Counter()})
        row["posts"] += 1
        doc = _metrics(post)
        status = doc.get("status")
        if status is None:
            row["reads"]["unread"] += 1
        elif status != "ok":
            row["reads"][status if isinstance(status, str) and status in _MEASURE_STATUSES else "other"] += 1
        last = doc.get("last") if isinstance(doc.get("last"), dict) else {}
        video = last.get("video") if isinstance(last.get("video"), dict) else {}
        for key, _label_words in _SUM_KEYS:
            n = _count(video.get(key))
            if n is not None:
                row["sums"][key] = row["sums"].get(key, 0) + n
                row["sum_n"][key] += 1
        if not _read_at_24h(last):
            continue
        for key in _REACH_KEYS.get(platform, _DEFAULT_REACH_KEYS):
            n = _count(last.get(key))
            if n is not None:
                row["reach"].append(n)
                row["used"].add(key)
                break
        watch = _video_measure(video, _WATCH_KEYS.get(platform))
        if watch is not None:
            row["watch"].append(watch)
        share_key, factor, _share_words = _SHARE_KEYS.get(platform, (None, 1.0, ""))
        share = _video_measure(video, share_key)
        if share is not None:
            row["share"].append(share * factor)
    rows: List[Dict[str, Any]] = []
    for (platform, fmt), row in acc.items():
        f = followers.get(platform) if isinstance(followers, dict) else None
        count = f.get("followers") if isinstance(f, dict) else None
        base = f.get("four_week_base") if isinstance(f, dict) else None
        reach = row["reach"]
        rows.append({
            "platform": platform, "format": fmt, "posts": row["posts"],
            "followers": count, "followers_at": f.get("at") if isinstance(f, dict) else None,
            "base": base, "base_at": f.get("four_week_base_at") if isinstance(f, dict) else None,
            "gain": count - base if count is not None and base is not None else None,
            "reach_label": _reach_label(platform, row["used"]), "reach_n": len(reach),
            "reach_median": statistics.median(reach) if reach else None, "reach_best": max(reach) if reach else None,
            "sums": dict(row["sums"]), "sum_n": dict(row["sum_n"]),
            "watch_s": statistics.median(row["watch"]) if row["watch"] else None,
            "share_pct": statistics.median(row["share"]) if row["share"] else None,
            "reads": dict(row["reads"]),
        })
    rows.sort(key=lambda r: (r["gain"] is None, -(r["gain"] or 0), r["reach_median"] is None,
                             -(r["reach_median"] or 0), r["platform"], _format_rank(r["format"])))
    return {"first": first, "last": week_end, "rows": rows}


def _image_fallbacks(posts: List[Dict[str, Any]]) -> List[Tuple[Optional[date], List[Tuple[str, int]]]]:
    """The run days whose live posts carry `metadata.image_fallback` (POST_METADATA_IMAGE_FALLBACK — a code
    `create_posts` stamps on each post frozen as text where the day's picture should have gone), each with
    (the reason in the owner's words, its posts): IMAGE_FALLBACK_REASONS' words in that order, an unknown
    code as fixed words last — never the stored value. A post without a readable run day is under None,
    listed last. Pure."""
    by_day: Dict[Optional[date], Counter] = {}
    for post in posts:
        code = _meta(post).get(POST_METADATA_IMAGE_FALLBACK)
        if not code:
            continue
        known = code if isinstance(code, str) and code in IMAGE_FALLBACK_REASONS else None
        by_day.setdefault(post_run_date(post), Counter())[known] += 1
    out: List[Tuple[Optional[date], List[Tuple[str, int]]]] = []
    for day in sorted(by_day, key=lambda d: (d is None, d or date.min)):
        counts = by_day[day]
        reasons = [(IMAGE_FALLBACK_REASONS[c], counts[c]) for c in IMAGE_FALLBACK_REASONS if counts.get(c)]
        if counts.get(None):
            reasons.append((_UNKNOWN_FALLBACK, counts[None]))
        out.append((day, reasons))
    return out


def _taps(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """/go taps per campaign, with the EARLY rows — the server-constant `<campaign>_early` keys, taps in
    the first minutes after that campaign's post went out (`smart_link.EARLY_KEYS`), mostly link
    scanners — summed apart into `early` and never into `total`. Any other row is an ordinary campaign
    (a stray "foo_early" included); an unreadable count is counted as an unreadable row, never 0."""
    by: Counter = Counter()
    unreadable = early = 0
    for r in rows:
        if not isinstance(r, dict):
            unreadable += 1
            continue
        hits = _count(r.get("hits"))
        if hits is None:
            unreadable += 1
            continue
        campaign = r.get("campaign")
        if isinstance(campaign, str) and campaign in smart_link.EARLY_BASE:
            early += hits
            continue
        campaign = campaign if isinstance(campaign, str) and _CAMPAIGN_RE.fullmatch(campaign) else "(unreadable)"
        by[campaign] += hits
    ordered = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"rows": ordered, "total": sum(by.values()), "early": early, "unreadable_rows": unreadable}


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
    plus the week's highest stage memory peak (MB, day, stage). A run skipped `rest_day` is a rest day
    whatever TODAY's calendar says (Batch 3): the posting days are a switch, and a week that changed them
    must not list its old rest days as "skipped — rest_day" (under an unchanged calendar nothing moves —
    selection skips a day as a rest day only when it is one). Every other run row is listed whatever today's
    calendar says — its own record, as `judged_posting_day` reads one; only a day with NO run row is judged
    on today's calendar ("no run (a posting day)"), as nothing else records what that day was."""
    by_day: Dict[date, Dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        d = _run_day(run)
        if d is None:
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
                if reason == "rest_day":
                    text = ""
                elif why:
                    text = f"skipped — {why}"
            elif status == "failed":
                # `stage` is the LAST COMPLETED stage (migration 170), never where it failed.
                stage = _key(run.get("stage"))
                text = ("failed before any stage completed" if stage == RUN_STAGES[0]
                        else f"failed after {stage or '?'}")
                if _posts_recorded(run):
                    # Review F1 (live regression, 2026-10-16): its posts exist and were offered like any day's,
                    # so the day may well have posted — never let the row read as a lost day. Nothing more is
                    # claimed: the digest reads no post of the run here, and a run resumed after midnight
                    # records its posts on the next day (into next week's Posts section).
                    text += " — its posts were recorded"
            note = _series_note(run) if text else ""
            if note:
                # Drop 2: which series the day took, and what it fell back from.
                text = f"{text} · {note}"
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


def _run_day(run: Dict[str, Any]) -> Optional[date]:
    """The ET day a run row is for (`run_date`), or None when it cannot be read."""
    try:
        return date.fromisoformat(str(run.get("run_date"))[:10])
    except ValueError:
        return None


# ── the weekly cost line (pure, integer micro-dollars) ────────────────────────


def run_worker_micros(run: Dict[str, Any]) -> Optional[int]:
    """The worker's ≈ cost of ONE run in micro-dollars: the wall seconds of its billed timings
    (`_BILLED_TIMINGS`; a missing key adds 0, an unknown key is ignored) × (its highest readable cgroup
    memory peak, clamped to the 4 GB service — 4 GB when none was recorded — at the GB rate + 4 vCPUs at
    the vCPU rate). None when the timings cannot be priced: not a dict, or a billed value that is not a
    finite number of seconds in 0..STAGE_SECONDS_MAX (a bool, a string, NaN, a negative, a hand edit) —
    never a silent 0. Every key is rounded to milliseconds before the sum, so there is no float drift."""
    timings = run.get("timings")
    if not isinstance(timings, dict):
        return None
    ms = 0
    for key in _BILLED_TIMINGS:
        if key not in timings:
            continue
        value = timings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            seconds = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(seconds) or not 0 <= seconds <= STAGE_SECONDS_MAX:
            return None
        ms += int(round(seconds * 1000))
    peaks = [mb for mb, _stage in _memory_peaks(run)]
    mb = min(int(round(max(peaks))), WORKER_MEMORY_MB) if peaks else WORKER_MEMORY_MB
    # nano-$ per second = (mb / 1024) × GB rate + vCPUs × vCPU rate; × ms / 1000 → nano-$; / 1000 → micro-$.
    numerator = ms * (mb * RAILWAY_NANO_PER_GB_SECOND + WORKER_VCPUS * RAILWAY_NANO_PER_VCPU_SECOND * 1024)
    return (numerator + 512_000_000) // 1_024_000_000   # half-up


def _worker_week(runs: Optional[List[Any]], first: date, last: date) -> Optional[int]:
    """The worker ≈ cost of the runs dated `first`..`last` (ET), or None: the runs could not be read,
    or ONE run in the window cannot be priced (logged with its id) — never a partial sum."""
    if runs is None:
        return None
    total = 0
    for run in runs:
        if not isinstance(run, dict):
            continue
        day = _run_day(run)
        if day is None or not first <= day <= last:
            continue
        micros = run_worker_micros(run)
        if micros is None:
            logger.warning("marketing digest: run_id=%s (%s) has timings the cost line cannot price — the worker "
                           "part of the week %s..%s is unreadable", _clean(run.get("id"), 60), day, first, last)
            return None
        total += micros
    return total if total <= _MICROS_BOUND else None


def _gemini_week(rows: Optional[List[Any]], first: date, last: date) -> Optional[int]:
    """The Gemini ≈ cost of the scripts dated `first`..`last` (ET): their summed `tokens_used` at the one
    blended rate, rounded half-up. None when the rows could not be read, a row's day cannot be read (it
    cannot be placed in either week), or an in-window count is not a plain non-negative int."""
    if rows is None:
        return None
    tokens = 0
    for row in rows:
        day = _ledger_date(row.get("run_date")) if isinstance(row, dict) else None
        if day is None:
            logger.warning("marketing digest: a script row cannot be dated — the Gemini part of the week %s..%s "
                           "is unreadable", first, last)
            return None
        if not first <= day <= last:
            continue
        count = _count(row.get("tokens_used"))
        if count is None:
            logger.warning("marketing digest: the script of %s has an unreadable tokens_used — the Gemini part "
                           "of the week %s..%s is unreadable", day, first, last)
            return None
        tokens += count
    micros = (tokens * GEMINI_MICROS_PER_MILLION_TOKENS + 500_000) // 1_000_000
    return micros if micros <= _MICROS_BOUND else None


def _x_week(rows: Optional[List[Any]], start: datetime, end: datetime) -> Tuple[Optional[int], int]:
    """(X spend in [start, end) by each journal entry's OWN time — exact to our ledger — or None, and the
    number of undated entries seen). None when the rows could not be read, an in-window amount cannot
    be read (logged with the post id), or the sum is beyond ±$1,000,000. Undated entries are never
    counted into any week."""
    if rows is None:
        return None, 0
    total = undated = 0
    unreadable_posts: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        window = charges_between(row, start, end)
        undated += window.undated
        if window.unreadable:
            unreadable_posts.append(_clean(row.get("id"), 60))
        total += window.micros
    if unreadable_posts:
        logger.warning("marketing digest: X charges in %s..%s that cannot be read on post_id(s) %s — X is "
                       "unreadable for that week", start.isoformat(), end.isoformat(), ", ".join(unreadable_posts[:5]))
        return None, undated
    if abs(total) > _MICROS_BOUND:
        logger.warning("marketing digest: X charges in %s..%s sum beyond ±$1,000,000 — X is unreadable for that "
                       "week", start.isoformat(), end.isoformat())
        return None, undated
    return total, undated


#: The publish states of a `queued` Upload-Post post whose send may or may not have reached Upload-Post
#: (an ambiguous answer, its escalation, a crash mid-send), and of a `failed` one the owner marked "Not
#: posted" (it is not on the platform, but Upload-Post may still have taken the job). While one is in
#: the week, the upload count is a floor.
_UPLOAD_UNSURE_QUEUED = frozenset({"unknown", "escalated", "sending"})
_UPLOAD_UNSURE_FAILED = frozenset({"owner_not_posted"})


def _given(value: Any) -> bool:
    """An id Upload-Post or the platform answered with: a non-blank string or an int (never a bool)."""
    return (isinstance(value, str) and value.strip() != "") or type(value) is int


def _uploads(live: List[Dict[str, Any]]) -> Tuple[int, bool]:
    """(The live posts on an Upload-Post platform that Upload-Post TOOK — each an upload against the plan's
    quota, whatever happened to it next — and whether that count is only a floor.) Upload-Post took the
    job when it acknowledged it (`metadata.publish.upload_post.submitted_at`, `job_id` or `poll_id`), when
    the post was found on the platform (`platform_post_id`, or status `published` / `retracted`: an
    ambiguous send that reconcile later FOUND carries none of the first three), or when reconcile read the
    job's own failure from Upload-Post (`publish.state` `failed_on_platform`). A post whose send may or may
    not have reached it (`_UPLOAD_UNSURE_QUEUED`, `_UPLOAD_UNSURE_FAILED`) is not counted, and makes the
    count a floor ("≥")."""
    n, floor = 0, False
    for post in live:
        if _platform(post) not in UPLOAD_POST_PLATFORMS:
            continue
        pub = _meta(post).get("publish")
        pub = pub if isinstance(pub, dict) else {}
        upload = pub.get("upload_post")
        upload = upload if isinstance(upload, dict) else {}
        status = post.get("status")
        state = pub.get("state") if isinstance(pub.get("state"), str) else None   # a hand edit is no state
        took = (_parse_ts(upload.get("submitted_at")) is not None
                or any(_given(upload.get(key)) for key in ("job_id", "poll_id", "platform_post_id"))
                or status in ("published", "retracted") or state == "failed_on_platform")
        if took:
            n += 1
        elif ((status == "queued" and state in _UPLOAD_UNSURE_QUEUED)
              or (status == "failed" and state in _UPLOAD_UNSURE_FAILED)):
            floor = True
    return n, floor


def _fee_week(monthly_micros: int) -> int:
    """A week's share (7 / 30.4375) of a monthly fee, in micro-dollars, rounded half-up."""
    return (monthly_micros * 70_000 + _MONTH_DAYS_X10000 // 2) // _MONTH_DAYS_X10000


def _usage_part(x: Optional[int], gemini: Optional[int], worker: Optional[int]) -> Dict[str, Any]:
    """One week's USAGE — the parts that move with what the engine did (X, Gemini, the worker) — and the
    usage parts that could not be read (the sum then leaves them out: a floor)."""
    values = (("X", x), ("Gemini", gemini), ("worker", worker))
    unreadable = [name for name, v in values if v is None]
    return {"x": x, "gemini": gemini, "worker": worker, "usage": sum(v for _name, v in values if v is not None),
            "usage_unreadable": unreadable, "usage_partial": bool(unreadable)}


def _cost_part(x: Optional[int], gemini: Optional[int], worker: Optional[int],
               upload_post: Optional[int]) -> Dict[str, Any]:
    """Last week's whole cost: its usage (`_usage_part`) plus the plan fee's share."""
    part = _usage_part(x, gemini, worker)
    part.update(upload_post=upload_post, total=part["usage"] + (upload_post if upload_post is not None else 0),
                unreadable=part["usage_unreadable"] + ([] if upload_post is not None else ["Upload-Post"]))
    return part


def weekly_cost(
    *, week_start: date, week_end: date, live: List[Dict[str, Any]], posts_capped: bool,
    runs: Optional[List[Any]], prev_runs: Optional[List[Any]], charge_rows: Optional[List[Any]],
    script_rows: Optional[List[Any]], upload_post_fee_micros: Any, cost_warn_micros: Any,
) -> Dict[str, Any]:
    """Last week's cost part by part (`_cost_part`), the week before's USAGE (`_usage_part`), and the
    usage alert line. Pure. A part that cannot be read is None — the sum then leaves it out and says so —
    never a guess, never NaN. The X windows are the digest's own [start, end) instants (DST-correct); runs
    and scripts are placed by their ET `run_date`. The fee share is 7/30.4375 of a month for every week
    (the November week is 169 hours, still 7 days of the plan). The week before carries NO fee: the plan
    in force then is not recorded, and today's fee (MARKETING_UPLOAD_POST_MONTHLY_USD, read at call time)
    would misprice it by the whole fee the first Monday after a plan change — so the two weeks are
    compared by usage, like with like."""
    start, end = week_bounds(week_start, week_end)
    prev_first, prev_last = week_start - timedelta(days=7), week_start - timedelta(days=1)
    prev_start, _prev_end = week_bounds(prev_first, prev_last)   # _prev_end == start
    fee = (upload_post_fee_micros if type(upload_post_fee_micros) is int
           and 0 <= upload_post_fee_micros <= _MICROS_BOUND else None)
    warn = cost_warn_micros if type(cost_warn_micros) is int and 0 <= cost_warn_micros <= _MICROS_BOUND else 0
    fee_week = _fee_week(fee) if fee is not None else None

    x_last, undated = _x_week(charge_rows, start, end)
    x_prev, _undated_prev = _x_week(charge_rows, prev_start, start)
    last = _cost_part(x_last, _gemini_week(script_rows, week_start, week_end),
                      _worker_week(runs, week_start, week_end), fee_week)
    uploads, uploads_unsure = _uploads(live)
    last.update(undated=undated, uploads=uploads, uploads_floor=bool(posts_capped) or uploads_unsure,
                free=fee == 0)
    before = _usage_part(x_prev, _gemini_week(script_rows, prev_first, prev_last),
                         _worker_week(prev_runs, prev_first, prev_last))
    return {"last": last, "before": before, "warn": warn}


def summarize_week(
    *, now: datetime, week_start: date, week_end: date, posts: List[Dict[str, Any]],
    runs: Optional[List[Dict[str, Any]]] = None, measured: Optional[List[Dict[str, Any]]] = None,
    link_hits: Optional[List[Dict[str, Any]]] = None, spend: Optional[Dict[str, int]] = None,
    budget_micros: int = 0, escalated: Optional[List[Dict[str, Any]]] = None,
    runway: Optional[Dict[str, Any]] = None, metrics_job: Optional[Dict[str, Any]] = None,
    metrics_enabled: bool = False, store: str = "prelaunch", posts_capped: bool = False,
    charge_rows: Optional[List[Dict[str, Any]]] = None, prev_runs: Optional[List[Dict[str, Any]]] = None,
    script_rows: Optional[List[Dict[str, Any]]] = None, upload_post_fee_micros: Any = 0,
    cost_warn_micros: Any = 0, measured_capped: bool = False,
) -> Dict[str, Any]:
    """The digest's numbers from the rows the gather read. Pure. A section whose rows could not be read
    is passed as None and stays None ("unreadable" in the message) — never an empty or zero section.
    The cost line's inputs (`charge_rows`, `prev_runs`, `script_rows`) follow the same rule: None makes
    only their part of the cost "unreadable". `measured` (the 35-day published posts) feeds both the
    follower snapshots and the 4-week `scorecard`; `measured_capped` says that read hit its limit (its
    newest rows are the missing ones). The week's top post and the picture fallbacks come from `posts`."""
    now = _aware(now)
    runs, measured, link_hits, escalated = (_listed(runs), _listed(measured), _listed(link_hits),
                                            _listed(escalated))
    charge_rows, prev_runs, script_rows = _listed(charge_rows), _listed(prev_runs), _listed(script_rows)
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
    # (platform, run day, why, format) — the format (video / image / text) closes each row.
    rejected_rows: List[Tuple[str, str, str, str]] = []
    failed_rows: List[Tuple[str, str, str, str]] = []
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
            rejected_rows.append((_label(_platform(p)), run_txt, REJECT_REASONS[code] if code else "no reason",
                                  _post_format(p)))
        elif p.get("status") == "failed":
            pub = _meta(p).get("publish") if isinstance(_meta(p).get("publish"), dict) else {}
            category = _key(pub.get("category"))
            err = p.get("last_error") or pub.get("error")
            detail = _clean(err, _ERROR_CAP) if err not in (None, "") else "no error recorded"
            failed_rows.append((_label(_platform(p)), run_txt, f"{category}: {detail}" if category else detail,
                                _post_format(p)))

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
        total = sum(by_op.values())
        if abs(total) > _MICROS_BOUND or any(abs(m) > _MICROS_BOUND for m in by_op.values()):
            # A hand-edited 400-digit amount used to raise OverflowError in the % and take the whole
            # digest down; now only this line says "unreadable".
            logger.warning("marketing digest: the X spend by op (%s) is beyond ±$1,000,000 — the X spend line "
                           "is unreadable", ", ".join(sorted(by_op)))
        else:
            spend_out = {"month": month_start_utc(now).strftime("%Y-%m"), "total": total,
                         "by_op": sorted(by_op.items(), key=lambda kv: (-kv[1], kv[0])),
                         "budget": budget_micros if type(budget_micros) is int and budget_micros > 0 else 0}

    cost = weekly_cost(
        week_start=week_start, week_end=week_end, live=live, posts_capped=bool(posts_capped), runs=runs,
        prev_runs=prev_runs, charge_rows=charge_rows, script_rows=script_rows,
        upload_post_fee_micros=upload_post_fee_micros, cost_warn_micros=cost_warn_micros)

    run_rows, peak = _run_rows(runs, week_start, week_end) if runs is not None else (None, None)
    measured_rows = [p for p in measured if isinstance(p, dict)] if measured is not None else None
    followers = _followers(measured_rows, now=now) if measured_rows is not None else None
    score = None
    if measured_rows is not None and followers is not None:
        score = scorecard(measured_rows, week_end=week_end, followers=followers)
        score["capped"] = bool(measured_capped)
        # How many published posts that read took in: the capped line names THIS count, never the page
        # bound — a read stuck on a full page of one instant stops at 300, and boundary rows read twice are
        # kept once (`list_published_posts`), so "the first 1,200" could be false either way.
        score["read"] = len(measured_rows)
    return {
        "now": now, "week_start": week_start, "week_end": week_end, "posts_capped": bool(posts_capped),
        "rehearsal": len(rows) - len(live), "live": len(live), "status_totals": _status_counts(live),
        "by_platform": by_platform, "by_format": _format_counts(live), "expired": dict(expired),
        "rejections": {"by_reason": dict(reasons), "rows": rejected_rows}, "failed_rows": failed_rows,
        "image_fallback": _image_fallbacks(live), "scorecard": score, "top_post": _top_post(live),
        "metrics_enabled": bool(metrics_enabled), "followers": followers,
        "taps": _taps(link_hits) if link_hits is not None else None, "store": store,
        "spend": spend_out, "latency": _latency(live), "runway": runway, "escalated": escalated_rows,
        "metrics_job": metrics_job, "runs": run_rows, "memory_peak": peak, "cost": cost,
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


def _and(names: List[str]) -> str:
    """'X' · 'X and worker' · 'X, Gemini and worker'."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _usage_text(part: Dict[str, Any]) -> str:
    """"≈ $0.235", or "≥ $0.174 (X unreadable)" when a usage part could not be read; "unreadable" when the
    part itself cannot be read."""
    usage, unread = part.get("usage"), part.get("usage_unreadable")
    if type(usage) is not int or not isinstance(unread, list):
        return "unreadable"
    names = [str(name) for name in unread]
    return f"≥ {_usd(usage)} ({_and(names)} unreadable)" if names else f"≈ {_usd(usage)}"


def _cost_lines(cost: Any) -> List[str]:
    """The weekly cost block: last week's total and parts; then USAGE (X + Gemini + worker, never the plan
    fee) for last week beside the week before — like with like, as the fee in force a week earlier is not
    recorded; and the usage alert when last week's usage passed MARKETING_WEEKLY_COST_WARN_USD. It sits
    right under the date line, so the row caps never touch it and the last-resort cut (which keeps the
    top) never drops it. An unreadable part is named and its sum becomes a floor ("≥")."""
    if not isinstance(cost, dict) or not isinstance(cost.get("last"), dict) or not isinstance(cost.get("before"), dict):
        return ["💵 Cost last week: unreadable (see the web logs)"]
    last, before = cost["last"], cost["before"]

    def head(part: Dict[str, Any]) -> Tuple[str, str]:
        unread = part.get("unreadable") or []
        return ("≥", f" ({_and(unread)} unreadable)") if unread else ("≈", "")

    # Read with .get(): a part that is missing reads "unreadable" (`_usd` of a non-int), never a KeyError.
    x, gemini, worker, upload = (last.get("x"), last.get("gemini"), last.get("worker"),
                                 last.get("upload_post"))
    if type(x) is not int:
        x_txt = "X unreadable"
    elif last.get("undated"):
        x_txt = (f"X {_usd(x)} (our ledger; "
                 f"{_plural(int(last['undated']), 'undated charge', 'undated charges')} not counted)")
    else:
        x_txt = f"X {_usd(x)} (our ledger)"
    gemini_txt = f"Gemini ≈ {_usd(gemini)}" if type(gemini) is int else "Gemini unreadable"
    worker_txt = f"worker ≈ {_usd(worker)}" if type(worker) is int else "worker unreadable"
    if type(upload) is not int:
        upload_txt = "Upload-Post unreadable"
    else:
        plan = "Free plan" if last.get("free") else "plan fee"
        uploads = _plural(int(last.get("uploads") or 0), "upload", "uploads")
        upload_txt = (f"Upload-Post {_usd(upload)} "
                      f"({plan} · {'≥ ' if last.get('uploads_floor') else ''}{uploads})")
    approx, note = head(last)
    lines = [f"💵 Cost last week {approx} {_usd(last.get('total'))}{note}: "
             f"{x_txt} · {gemini_txt} · {worker_txt} · {upload_txt}",
             f"Usage (X + Gemini + worker): last week {_usage_text(last)} · week before {_usage_text(before)}"]
    warn = cost.get("warn") if type(cost.get("warn")) is int else 0
    usage = last.get("usage")
    if warn > 0 and type(usage) is int and usage > warn:
        lines.append(f"⚠️ Usage {'≥' if last.get('usage_partial') else '≈'} {_usd(usage)} passed your "
                     f"${warn / 1_000_000:.2f} alert line")
    return lines


def _num(value: float) -> str:
    """A median or a measure, as a count when whole after rounding ("1,234"), else with one decimal."""
    rounded = round(float(value), 1)
    return _n(int(rounded)) if rounded.is_integer() else f"{rounded:,.1f}"


def _mmdd(at: Any) -> str:
    local = _et_local(at) if isinstance(at, datetime) else None
    return local.strftime("%m-%d") if local is not None else "?"


def _follower_text(row: Dict[str, Any], now: datetime) -> str:
    """"followers 23 (+18 since 09-07)" — the change dated by its own baseline; "change n/a" without one;
    ", as of MM-DD" when the newest count is older than FOLLOWER_STALE; "followers n/a" without a count."""
    count = row.get("followers")
    if type(count) is not int:
        return "followers n/a"
    base = row.get("base")
    if type(base) is int:
        delta = count - base
        sign = "+" if delta > 0 else ("±" if delta == 0 else "-")
        notes = [f"{sign}{_n(abs(delta))} since {_mmdd(row.get('base_at'))}"]
    else:
        notes = ["change n/a"]
    at = row.get("followers_at")
    if isinstance(at, datetime) and now - at > FOLLOWER_STALE:
        notes.append(f"as of {_mmdd(at)}")
    return f"followers {_n(count)} ({', '.join(notes)})"


def _reach_text(row: Dict[str, Any]) -> str:
    label = row.get("reach_label")
    if label is None:
        return "no view counts"
    if row.get("reach_median") is None:
        return f"{label} n/a"
    return (f"{label} median {_num(row['reach_median'])}, best {_n(row['reach_best'])} "
            f"({_n(row['reach_n'])} at 24 h+)")


def _sum_text(row: Dict[str, Any], key: str) -> str:
    """A summed TikTok figure: "n/a" when no post gave it, "≥ N" when only some of the row's posts did (the
    others' share is unknown — a floor, never the total), else "N"."""
    sums, covered = row.get("sums") or {}, row.get("sum_n") or {}
    total = sums.get(key)
    if type(total) is not int:
        return "n/a"
    n = covered.get(key)
    return f"≥ {_n(total)}" if type(n) is not int or n < row["posts"] else _n(total)


def _score_line(row: Dict[str, Any], now: datetime) -> str:
    """One scorecard row: platform and format, posts, followers, TikTok's follows and profile visits from
    posts, reach, a video row's watch figures, and the posts not measured `ok`. A figure no post gave is
    "n/a" — never 0; a sum only some posts gave is a floor ("≥ N")."""
    platform, fmt = row["platform"], row["format"]
    parts = [_plural(row["posts"], "post", "posts"), _follower_text(row, now)]
    video = fmt == "video" and platform in _WATCH_KEYS
    if video and platform == "tiktok":
        parts += [f"{words} {_sum_text(row, key)}" for key, words in _SUM_KEYS]
    parts.append(_reach_text(row))
    if video:
        watch, share = row.get("watch_s"), row.get("share_pct")
        parts.append(f"avg watch {_num(watch) + ' s' if watch is not None else 'n/a'}")
        parts.append(f"{_SHARE_KEYS[platform][2]} {_num(share) + '%' if share is not None else 'n/a'}")
    reads = row.get("reads") or {}
    shown = [f"{_n(reads[s])} {s}" for s in _READ_ORDER if reads.get(s)]
    if shown:
        parts.append("reads: " + ", ".join(shown))
    return f"• {_label(platform)} {fmt}: " + " · ".join(parts)


def _scorecard_lines(r: Dict[str, Any], score_cap: Optional[int]) -> List[str]:
    """The scorecard block: its header (the 4 weeks, the sort, what the medians are over), its rows —
    every one while `score_cap` is None (`_PASSES` trims the other sections first), else capped — and the
    week's ONE top-post link line (never its text)."""
    unreadable = "unreadable (see the web logs)"
    score = r.get("scorecard")
    if not r.get("metrics_enabled"):
        lines = ["Scorecard: not measured — MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on"]
    elif not isinstance(score, dict):
        lines = [f"Scorecard: {unreadable}"]
    else:
        # Month-day only: a weekday here would read like one of the Runs rows ("• Sun 10-04 …").
        window = f"{score['first'].strftime('%m-%d')} → {score['last'].strftime('%m-%d')}"
        if not score["rows"]:
            lines = [f"Scorecard, 4 weeks ({window}): no published posts"]
        else:
            lines = [f"Scorecard, 4 weeks ({window}), by follower gain — views, watch and % are medians over "
                     f"posts read at 24 h or older:"]
            rows = [_score_line(row, r["now"]) for row in score["rows"]]
            lines += rows if score_cap is None else _capped(rows, score_cap, indent="")
        if score.get("capped"):
            read = score.get("read")
            shown = _n(read) if type(read) is int and read >= 0 else "?"
            lines.append(f"  (the published-posts read stopped after {shown} posts — the newest may be missing)")
    top = r.get("top_post")
    if top:
        lines.append(f"Top post this week: {top['url']} ({_join_counts(top['counts'])})")
    return lines


def _render_digest(r: Dict[str, Any], cap: int, score_cap: Optional[int] = None) -> str:
    unreadable = "unreadable (see the web logs)"
    lines: List[str] = [
        "📊 Caydex marketing — weekly digest",
        f"{r['week_start'].strftime('%a %Y-%m-%d')} → {r['week_end'].strftime('%a %Y-%m-%d')} (ET)",
        *_cost_lines(r.get("cost")),
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
        formats = r.get("by_format") or {}
        if formats:
            keys = [f for f in _FORMAT_ORDER if f in formats] + sorted(f for f in formats if f not in _FORMAT_ORDER)
            lines.append("Formats: " + " · ".join(
                f"{f} {_n(sum(formats[f].values()))} ({_ordered_statuses(formats[f])})" for f in keys))
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
            lines += _capped([f"❌ {p} · run {d} · {why} · {fmt}" for p, d, why, fmt in rej["rows"]], cap)
        if r["failed_rows"]:
            lines.append(f"Failed {_n(len(r['failed_rows']))}:")
            lines += _capped([f"✖ {p} · run {d} · {why} · {fmt}" for p, d, why, fmt in r["failed_rows"]], cap)
        fallbacks = r.get("image_fallback") or []
        if fallbacks:
            # Codes only, in the owner's words (IMAGE_FALLBACK_REASONS). "text posts instead", never "posted as
            # text": a member may have been rejected or expired, not posted.
            lines.append(f"🖼 Picture not used on {_plural(len(fallbacks), 'day', 'days')} (text posts instead):")
            lines += _capped([f"{_day(d) if d is not None else 'an unreadable day'}: "
                              + "; ".join(f"{words} ({_plural(n, 'post', 'posts')})" for words, n in reasons)
                              for d, reasons in fallbacks], cap)
    if r.get("rehearsal"):
        lines.append(f"(+{_n(r['rehearsal'])} rehearsal rows — dry run, never publishable; not counted)")
    lines.append("")

    # the 4-week scorecard (follows first) + this week's top post, then followers
    lines += _scorecard_lines(r, score_cap)
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
        # Approximate: crawlers that pass as browsers, at-least-once delivery (smart_link). Taps in the
        # first minutes after a post went out are shown apart — mostly link scanners.
        early = taps.get("early") or 0
        early_txt = f" — plus {_n(early)} in the first minutes after posting (mostly link scanners)" if early else ""
        if taps["rows"]:
            shown = taps["rows"][: max(cap, 1)]
            more = len(taps["rows"]) - len(shown)
            tail = f" · … {_n(more)} more" if more > 0 else ""
            lines.append("/go taps (approximate): " + " · ".join(f"{c} {_n(h)}" for c, h in shown) + tail
                         + f" — {_n(taps['total'])} in total" + early_txt)
        else:
            lines.append("/go taps (approximate): none" + early_txt)
        if taps.get("unreadable_rows"):
            lines.append(f"  ({_n(taps['unreadable_rows'])} unreadable row(s) not counted)")
    store = r.get("store")
    # The app is on the App Store since 2026-10-05, so a missing or broken store URL is a
    # misconfiguration either way: every /go tap lands on the landing page, which has no store link.
    if store == "prelaunch":
        lines.append("⚠️ MARKETING_APP_STORE_URL is unset — every /go tap lands on the landing page, "
                     "which has no App Store link.")
    elif store == "invalid":
        lines.append("⚠️ MARKETING_APP_STORE_URL is set but invalid — /go falls back to the landing page, "
                     "which has no App Store link.")
    elif store == "preorder":
        lines.append("⚠️ MARKETING_APP_STORE_PREORDER is on — new captions say \"pre-order on the App Store\" "
                     "and the landing button \"Pre-order\"; the app is on sale, so turn it off.")

    # X spend
    spend = r.get("spend")
    if spend is None:
        lines.append(f"X spend: {unreadable}")
    else:
        if spend["budget"] > _MICROS_BOUND:
            head = (f"X spend {spend['month']} (UTC, to date): {_usd(spend['total'])} — the cap "
                    f"MARKETING_X_MONTHLY_BUDGET_USD is over $1,000,000 (check it)")
        elif spend["budget"]:
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
        elif run.get("no_later_than"):
            # Company Weekly on: only the lesson-only days were counted, and a news day that falls back to
            # the lesson brings the repeat forward — the date is an upper bound (`pool_runway`).
            what = "lessons repeat" if run["unused"] == 0 else "the first repeat is due"
            lines.append(f"{head} — {what} no later than {run['first_repeat'].strftime('%a %Y-%m-%d')} "
                         f"(sooner when a news day falls back to a lesson)")
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
    Rows are capped first (each list section shows fewer rows, with "… and N more" — the scorecard last,
    `_PASSES`); only if that is not enough is the text cut at a line break, with a marker saying so."""
    text = ""
    for cap, score_cap in _PASSES:
        text = _render_digest(report, cap, score_cap)
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
    """The pool's runway, from the ledger read exactly as selection reads it (`script_service._select` /
    `_select_lesson`): the newest `selection.news_recent_limit()` refs — RECENT_WINDOW posting days of
    history at the posting days in force NOW (Batch 3: 240 at the default four a week, 420 at seven) — of
    which only the LESSON refs count (`selection.lesson_refs`), on the calendar of the posting days, the
    content classes and the per-series switch read NOW (the switch only while a news class is on, as
    there). An unfiltered `len(pool) + RECENT_WINDOW` read let Company Weekly's `news:` refs push older
    lessons out of the window: after 39 lesson picks from a pool of 34 it still said "10 of 34 lessons
    unused". With the classes at "A" and the default posting days the line is unchanged. Imports nothing
    from script_service: the limit is selection's, read once so both reads below take the same rows."""
    from app.services.marketing import content_pool

    # The corpus load is CPU work (cached per process after the first call): off the event loop.
    pool = await asyncio.to_thread(content_pool.eligible_keys)
    classes = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
    news_series = (selection.parse_news_series(settings.MARKETING_NEWS_SERIES)
                   if classes != frozenset({"A"}) else frozenset())
    limit = selection.news_recent_limit()
    with_today = list(await svc.recent_source_refs(today + timedelta(days=1), limit))
    before_today = list(await svc.recent_source_refs(today, limit))
    # Today has a row exactly when the two reads differ (the later bound shifts every row by one), and it
    # is the newest; it used a lesson only when that row is one — a news day's row never did. (Comparing
    # the two lesson-FILTERED lists instead would call a news day "picked" once the ledger outgrows the
    # read: the one row only `before_today` reaches may be a lesson.) Lessons only, this is the old test.
    today_picked = with_today != before_today and bool(selection.lesson_refs(with_today[:1]))
    return pool_runway(pool, selection.lesson_refs(with_today), today=today, today_picked=today_picked,
                       classes=classes, news_series=news_series)


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
    """What the digest says about the store: `live`, `preorder` (a valid URL with
    MARKETING_APP_STORE_PREORDER on — new captions then say "pre-order on the App Store"), `invalid` or
    `prelaunch` (unset). Since the 2026-10-05 release only `live` is right; the others get a ⚠️."""
    if smart_link.store_url() is not None:
        return "preorder" if smart_link.store_state() == smart_link.STORE_PREORDER else "live"
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
    prev_week_start, prev_week_end = week_start - timedelta(days=7), week_start - timedelta(days=1)
    prev_start = week_bounds(prev_week_start, prev_week_end)[0]
    posts = await svc.list_posts_created_between(start, end, limit=WEEK_POST_LIMIT)
    runs = await _optional("the week's runs", svc.list_runs_between(week_start, week_end))
    # ONE paged read for the follower snapshots AND the 4-week scorecard (oldest first, page by page, so the
    # NEWEST posts are never the ones a single limit cuts — MD-7; past MEASURED_POST_MAX the read logs an
    # ERROR and the scorecard says the newest may be missing).
    read = await _optional("the scorecard's published posts", metrics_service.list_published_posts(
        svc, since=now - FOLLOWER_LOOKBACK, page_limit=MEASURED_POST_LIMIT, max_pages=MEASURED_POST_PAGES,
        what="digest"))
    measured, measured_capped = read if read is not None else (None, False)
    link_hits = await _optional("/go taps", svc.link_hits_between(week_start, week_end))
    spend = await _optional("X spend", _spend(svc, now))
    # The cost line: X posts touched since the week before began (every charge — a metrics read on an
    # old post, a back-dated correction — bumps its row's updated_at), the week before's runs (this
    # week's are `runs`), and both weeks' script token counts. Each read fails alone.
    charge_rows = await _optional("the X charge journal",
                                  svc.list_charge_rows_since("x", prev_start, limit=X_CHARGE_ROW_LIMIT))
    prev_runs = await _optional("the week before's runs", svc.list_runs_between(prev_week_start, prev_week_end))
    script_rows = await _optional("the scripts' token counts", svc.script_tokens_between(prev_week_start, week_end))
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
        charge_rows=charge_rows, prev_runs=prev_runs, script_rows=script_rows,
        upload_post_fee_micros=upload_post_fee_micros(), cost_warn_micros=weekly_cost_warn_micros(),
        measured_capped=measured_capped,
    )


# ── delivery ──────────────────────────────────────────────────────────────────


#: Seconds Telegram asked us to wait when its 429 named none.
_DEFAULT_RETRY_AFTER = 60
#: The ONE pacer every sender to the review chat shares (`telegram_pacing`, 2026-10-22): a health alert and
#: a digest in the same tick are spaced like every other message to the chat — the review sweep's, the
#: feed's and the Learn sweep's included (Telegram: about one message a second per chat). A module name, so
#: a test can stand a recording pacer in its place.
_PACER = telegram_pacing.PACER


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
    # B3PD-2: a past day is a posting day by its own record too — never only by TODAY's posting days.
    text = evaluate_run_final(run, run_date=run_date, posting_day=judged_posting_day(run_date, nightly=how, run=run),
                              now=at, max_attempts=settings.MARKETING_MAX_RUN_ATTEMPTS,
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


#: B3PD-2 (2026-10-20): what `_final_owed` decided for the ET day it ran on — {"day", "owed": True/False}, or
#: after a failed read {"day", "owed": None, "retry_at": monotonic} — so a day after a rest day costs at most ONE
#: read of the records per process (two rows), never one per tick.
_FINAL_OWED: Dict[str, Any] = {}
#: How long a failed `_final_owed` read waits before it is tried again (a ledger outage is logged this often).
FINAL_OWED_RETRY_SECONDS = 600


async def _final_owed(at: datetime) -> bool:
    """True when YESTERDAY's final word is owed although `final_due` says no: yesterday is a rest day on the
    posting days in force NOW, but its own record says it was a posting day (`judged_posting_day`) —
    MARKETING_POST_WEEKDAYS dropped its weekday since (B3PD-2). A switch from seven days to four made the
    morning after a failed day (the setting's own advice: change it in the morning ET) would otherwise cancel
    the final word that day's nightly alert promised.

    No I/O at all before `final_hour_et()`, on a day `final_due` already covers, while the review bot is not
    configured or Telegram's flood back-off is open (the next tick asks again), or once this process decided
    the day (`_FINAL_OWED`). Otherwise ONE read of the records: the nightly check's job state, then — unless that
    already decides — yesterday's run. Under an unchanged calendar both say no (a rest day has no nightly check,
    and its run closes `skipped` `rest_day` at its first kick), so nothing is claimed or sent: the default days
    stay as before but for these reads. A failed read is logged (WARNING) and retried after
    FINAL_OWED_RETRY_SECONDS — never a word on a guess. Never raises but CancelledError."""
    et = _aware(at).astimezone(ET)
    if et.hour < final_hour_et():
        return False
    today = et.date()
    run_date = today - timedelta(days=1)
    if selection.is_posting_day(run_date):
        return False   # `final_due`'s own day
    memo = _FINAL_OWED
    if memo.get("day") == today:
        if memo.get("owed") is not None:
            return bool(memo["owed"])
        if time.monotonic() < memo.get("retry_at", 0.0):
            return False
    if (not review_service.is_configured() or review_service.review_chat_id() is None
            or time.monotonic() < review_service._rate_limited_until):
        return False   # `_ready` would take no claim: read nothing either
    try:
        svc = get_marketing_run_service()
        _checked_at, how = nightly_check_time(await _job_state(svc, JOB_HEALTH), run_date)
        owed = how in (NIGHTLY_CHECKED, NIGHTLY_TIME_UNKNOWN)
        run = None if owed else await svc.get_run_for_date(run_date)
        owed = owed or ran_as_posting_day(run)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _FINAL_OWED.clear()
        _FINAL_OWED.update(day=today, owed=None, retry_at=time.monotonic() + FINAL_OWED_RETRY_SECONDS)
        logger.warning("marketing run health (final) %s: not a posting day on today's MARKETING_POST_WEEKDAYS, and "
                       "whether its own record says it was one could not be read (%s: %s) — no final word for it "
                       "this tick; retried in %d s", run_date, type(e).__name__, e, FINAL_OWED_RETRY_SECONDS)
        return False
    _FINAL_OWED.clear()
    _FINAL_OWED.update(day=today, owed=owed)
    if owed:
        logger.info("marketing run health (final) %s: not a posting day on today's MARKETING_POST_WEEKDAYS, but its "
                    "own record says it was one (nightly check: %s; run status %s) — the posting days changed "
                    "since; its final word is due", run_date, how,
                    run.get("status") if isinstance(run, dict) else "not read")
    return owed


def _final_owed_due(now: datetime) -> bool:
    """`final_due`, or the day `_final_owed` found a final word owed on (read from its memo, no I/O): the `due`
    test the owed final word runs under — `run_day_job` calls it first, before its own claim."""
    if final_due(now):
        return True
    et = _aware(now).astimezone(ET)
    return et.hour >= final_hour_et() and _FINAL_OWED.get("day") == et.date() and _FINAL_OWED.get("owed") is True


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
    CancelledError).

    B3PD-2 (2026-10-20): the final word is also due — under the same job and claim — on the day after a day
    the posting days in force NOW call a rest day but whose own record says was a posting day (`_final_owed`,
    one read of the records per process on such a day): MARKETING_POST_WEEKDAYS dropped that weekday since."""
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
                # B3PD-2: the final word on a day the posting days no longer list, owed by its own record.
                if job != JOB_HEALTH_FINAL or not await _final_owed(at):
                    continue
                due = _final_owed_due
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
