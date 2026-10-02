"""
The marketing MEASURE step — engagement counts for the weekly digest (design doc §12.11,
rules/marketing.md §2).

One step of the publisher loop (`publisher_service.publisher_tick`, gated on MARKETING_ENABLED and
MARKETING_METRICS_ENABLED and run LAST, so it never delays a post or a review message) — the loop is
the only code that calls a platform. `measure_cycle` does the day's work once per ET day, at the first
tick at or after 06:00 ET (`run_day_job` over the `marketing_metrics_daily` day claim), for the
`published` posts younger than 30 days: never a retracted or `queued` row (the query reads
`published` only), never one whose retract was requested, and — for the free daily reads — never one
published less than an hour ago (an AppView that has not indexed it yet would make it `missing`).
Every result is merged into `marketing_posts.metrics` by `run_service.merge_post_metrics`, the
column's ONE writer: fenced on the status and `metrics->>rev`, it never touches `metadata` or
`updated_at` (the publisher's fence).

* **Bluesky** — free and daily, from the PUBLIC AppView (`bluesky.get_posts`, 25 URIs a call, no
  credential ever), keyed by the post's at:// URI. A batch the AppView refuses (400) is asked again
  one URI at a time; a post it leaves out is `missing` (deleted by hand), never zeros. Followers
  from `bluesky.get_profile` on the newest post's DID.
* **X** — BILLED: $0.001 per post a timeline read returns, $0.010 for the account read. A post is
  read only when it has crossed a checkpoint (1 / 3 / 7 / 28 days after it went out) not yet in its
  `cps` — once, for the largest crossed — from our own timeline, ten minutes either side of its
  publish time, matched on our external id. Each read is charged BEFORE the call through
  `transition_post` (`x_metrics_read`, a five-post reserve — the write-ahead), corrected afterwards
  to what X returned (`x_metrics_read_correction`, dated at the reserve), and refunded only when the
  error proves nothing was billed (not sent, not configured, rate limited, credits depleted). A read
  starts only while the month's journal leaves the reserve PLUS `outlet_x.METRICS_HEADROOM_POSTS`
  posts of headroom under MARKETING_X_MONTHLY_BUDGET_USD, priced at what a post would reserve right
  now (`outlet_x.metrics_headroom_micros()`: $0.015 a post, $0.20 while MARKETING_X_ALLOW_URLS is
  on) — posting always wins — else the post records `capped`, with no read and no charge. The
  account (`get_me`, `x_account_read`) is read on Mondays, or when the stored snapshot is more than
  eight days old. Paid reads never run under MARKETING_DRY_RUN.
  A DEFINITE refusal of a post read (400 / 401 / 403 / 404 — any definite 4xx refusal but 402; a
  429, a 408 or a duplicate-content 403 is not one) keeps its reserve, records `unavailable` on that
  post for seven days (what the digest counts), and pauses EVERY X read (posts and the account)
  until then: one reserve a week, not one a day. The pause is also kept on the NEWEST X post
  (`x_reads_refused_until`), and the latest date in force on either one rules: the refused post is
  usually the OLDEST due one (one at its 28-day checkpoint — the listing is oldest first), which leaves
  the 30-day listing within a day or two, and a pause kept only on it went with it (re-review
  2026-10-02: a persistent refusal cost a reserve every ~2 days).
  A refused account read gets its OWN seven-day back-off (`x_account_refused_until`, also on the
  newest X post) and never pauses the post reads. Both markers sit beside — never in — `status` and
  `account` (`apply_backoff`). A 200 carrying only `errors` (x_api's
  `problem`: a suspended or protected account) records `error` with NO checkpoint — the post stays
  due, the read cost nothing — and stops X for the day.
* **Upload-Post** — best-effort, only when configured: at most ten posts a day, by the job id the
  publisher kept (`poll_id`, else `request_id`). A plan refusal (402/403) records `unavailable` for
  seven days and pauses every Upload-Post read until then (one call a week) — the pause kept, as on
  X, on the newest Upload-Post post (`upload_post_plan_refused_until`); a 404 is `missing`; an
  unknown outcome for one post (a 5xx, an odd 2xx, a timeout — or a bug of ours) records `error` on
  it, so it moves to the back of tomorrow's queue and one failing post never starves the others; a
  follower count whose stored snapshot is dated more than a day before the read is not stored. No
  Upload-Post failure ever holds the day open.

The day stays OPEN — a later tick retries, at most three claimed attempts per ET day per process —
after a transient X or Bluesky failure (429, 5xx, transport), an unreadable X spend, a lost fence, a
ledger error (after three in one run nothing more is read or written that tick: a paid read whose
result cannot be stored is money for nothing), or when the 120-second read budget of one tick is
spent. A refusal that will not pass by itself stops its platform instead, without holding the day:
X's 402 (no credits — refunded), a definite Bluesky AppView refusal, or an Upload-Post auth or quota
refusal stops it for the rest of the ET day; a definite X refusal (400 / 401 / 403 / 404 — any
status-bearing refusal but 402) backs off seven days X-wide, a refused `get_me` has its own
seven-day back-off, and an Upload-Post plan refusal backs off seven days (all above). A row already
measured today is never read again, so a second run the same day (another instance, a retried claim)
reads only what is still missing. Every stop and pause is NAMED in the job's `last_error` — on a day
that succeeds too (`finish_scheduled_job` stores it either way; the digest shows it as a note), so a
refused platform never looks like a clean day.

`marketing_posts.metrics` (v1)::

    {"v": 1, "rev": n, "measured_day": "YYYY-MM-DD",
     "status": "ok" | "missing" | "error" | "unavailable" | "capped" | "no_external_id",
     "note": str | null, "unavailable_until": "YYYY-MM-DD" | null,
     "cps": [1, 3, …]                               # X only: the checkpoints already measured
     "last": {"day", "at", "age_h", "cp", <counts>},
     "history": [<same as last>, …],                # one per ET day (a rerun replaces that day's);
                                                    # at most 30, at most 4 on X
     "account": {"followers", "following", "posts", "at", "source"},
     # the platform-wide back-offs (BACKOFF_KEYS), each on the post that was the NEWEST of its platform:
     "x_reads_refused_until": "YYYY-MM-DD",            # X: a refused post read pauses every X read
     "x_account_refused_until": "YYYY-MM-DD",          # X: the account read's own back-off
     "upload_post_plan_refused_until": "YYYY-MM-DD"}   # Upload-Post: a plan refusal pauses its reads

`<counts>` are likes, reposts, replies, quotes, bookmarks, impressions, views, shares, saves and
reach — each ONLY when the platform reported it: a count that is missing, None, negative, a bool,
NaN/inf, a non-integer float, or a string that is not 1-15 ASCII digits is omitted, never 0. A read
with no count at all records `error` / `no_metrics` and adds no history. A non-`ok` status keeps
the last good snapshot. `account` (its counts omitted the same way; `source` is `x_api`,
`bluesky_appview` or `upload_post`) is written only on the NEWEST post of its platform; older posts
keep the snapshot they got while they were the newest — the digest's follower history.

`run_day_job` is the day-claim runner shared with `digest_service` (run health, weekly digest).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.config import settings
from app.integrations import bluesky, upload_post, x_api
from app.services import notification_jobs
from app.services.marketing import outlet_upload_post, outlet_x
from app.services.marketing.outlet_base import scrub
from app.services.marketing.run_service import _parse_ts, get_marketing_run_service, run_date_et
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── the day-keyed jobs ─────────────────────────────────────────────────────────────────────────

#: `notification_job_state.job` of the measure step (also read by the digest's "last state" line).
JOB_METRICS = "marketing_metrics_daily"
#: Every marketing day job is claimed on the ET calendar, like `run_service.run_date_et`.
JOB_TIMEZONE = "America/New_York"
#: The measure step's first tick of the day is at or after this ET hour.
METRICS_HOUR_ET = 6
#: A claim held longer than this is presumed dead (a SIGKILLed instance): well above one run (120 s
#: of reads plus its ledger writes), and the NOTIFICATION_JOB_STALE_SECONDS default.
METRICS_STALE_SECONDS = 900
#: Claimed attempts per job per ET day in ONE process; then the job waits for tomorrow.
MAX_DAY_ATTEMPTS = 3
#: A job its `enabled` flag switched off is re-read this often (not every 10-minute tick).
DISABLED_RECHECK_SECONDS = 1800

#: `run_day_job` outcomes.
NOT_DUE = "not_due"          # before its hour / not its weekday — no I/O at all
DONE = "done"                # already succeeded today (this process, or per the ledger)
EXHAUSTED = "exhausted"      # MAX_DAY_ATTEMPTS failed attempts today in this process — no I/O
DISABLED = "disabled"        # notification_job_state.enabled is false
UNREADABLE = "unreadable"    # the job state could not be read — skip this tick (fail closed)
BUSY = "busy"                # the claim was refused (another instance holds it, or it just finished)
OK = "ok"                    # the work ran and succeeded: the day is recorded done
FAILED = "failed"            # the work ran and did not finish: the day stays open

# ── the measure step ───────────────────────────────────────────────────────────────────────────

METRICS_VERSION = 1
MEASURE_MAX_AGE = timedelta(days=30)
#: ~8 platforms × 4 posting days a week × 30 days ≈ 140 rows; a full read is logged.
MEASURE_SCAN_LIMIT = 300
#: No new platform read starts after this many seconds in one tick.
READ_BUDGET_SECONDS = 120.0
#: After this many ledger failures in one run, no read starts and no write is attempted until a later
#: tick: a paid X read whose result cannot be stored is money for nothing (and is read again tomorrow).
LEDGER_FAILURES_MAX = 3
#: The daily reads (Bluesky, Upload-Post) skip a post younger than this: an AppView that has not indexed
#: a minutes-old post yet would make it `missing`. (X reads start at the 1-day checkpoint.)
MIN_MEASURE_AGE = timedelta(hours=1)
HISTORY_MAX = 30
X_HISTORY_MAX = len(outlet_x.METRICS_CHECKPOINT_DAYS)
UPLOAD_POST_DAILY_READS = 10
#: A definite refusal pauses its platform's reads (Upload-Post's plan, X's refused read — and, apart,
#: X's refused account read) for this many days: one call a week, not one a day. A stored back-off date
#: further ahead than this cannot be ours (we write today + 7) and is ignored, never obeyed.
UNAVAILABLE_DAYS = 7
#: The metrics-document keys of the platform-wide back-offs (ISO dates), each written on the post that
#: was the NEWEST of its platform when the refusal came — beside `status` / `account`, never inside them
#: (`apply_backoff`). Never only on the refused post: that is usually the OLDEST due one (an X post at
#: its 28-day checkpoint — the listing is oldest first), which leaves the 30-day listing within a day or
#: two and took a pause kept only on it along (re-review 2026-10-02: a persistent X refusal cost a
#: reserve every ~2 days, not one a week). The newest post stays listed for 30 days, past any pause.
X_ACCOUNT_REFUSED_KEY = "x_account_refused_until"            # X's refused account read: get_me only
X_READS_REFUSED_KEY = "x_reads_refused_until"                # X's refused post read: EVERY X read
UPLOAD_POST_PLAN_REFUSED_KEY = "upload_post_plan_refused_until"   # Upload-Post's plan: every Upload-Post read
BACKOFF_KEYS = (X_ACCOUNT_REFUSED_KEY, X_READS_REFUSED_KEY, UPLOAD_POST_PLAN_REFUSED_KEY)
X_ACCOUNT_STALE = timedelta(days=8)
#: A follower count from an Upload-Post snapshot dated more than this before the read is not stored:
#: the digest dates a snapshot by when we READ it, so a stale one would pass for today's.
UPLOAD_POST_FOLLOWERS_MAX_AGE = timedelta(days=1)
#: The timeline page of one X metrics read (the endpoint's minimum) — what the reserve covers.
X_PAGE_SIZE = 5
NOTE_MAX = 200
#: The largest count kept: 15 digits, the same bound as a count sent as a string.
COUNT_MAX = 999_999_999_999_999

STATUSES = ("ok", "missing", "error", "unavailable", "capped", "no_external_id")
COUNT_KEYS = ("likes", "reposts", "replies", "quotes", "bookmarks", "impressions", "views", "shares",
              "saves", "reach")
ACCOUNT_KEYS = ("followers", "following", "posts")

#: Journal ops (`metadata.charges`), summed by the X cap.
OP_X_READ = "x_metrics_read"
OP_X_READ_CORRECTION = "x_metrics_read_correction"
OP_X_ACCOUNT = "x_account_read"
OP_X_ACCOUNT_CORRECTION = "x_account_read_correction"

#: Snapshot key → the platform's field names, first readable one wins. X's generated reference now
#: prints `repost_count` / `post_count` where the Fields guide and every example say `retweet_count` /
#: `tweet_count` (x_api's docstring) — both are read.
X_FIELDS: Dict[str, Tuple[str, ...]] = {
    "likes": ("like_count",), "reposts": ("retweet_count", "repost_count"), "replies": ("reply_count",),
    "quotes": ("quote_count",), "bookmarks": ("bookmark_count",), "impressions": ("impression_count",),
}
X_ACCOUNT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "followers": ("followers_count",), "following": ("following_count",),
    "posts": ("tweet_count", "post_count"),
}
BLUESKY_FIELDS: Dict[str, Tuple[str, ...]] = {
    "likes": ("likeCount",), "reposts": ("repostCount",), "replies": ("replyCount",),
    "quotes": ("quoteCount",), "bookmarks": ("bookmarkCount",),
}
BLUESKY_ACCOUNT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "followers": ("followersCount",), "following": ("followsCount",), "posts": ("postsCount",),
}
#: Upload-Post's `post_metrics` (docs.upload-post.com/api/get-analytics, 2026-10-01): `comments` are
#: replies; TikTok's `favorites` (saved to favourites) are bookmarks; Instagram's `impressions`
#: aliases its `views`. A field the page does not list for a platform is simply absent.
UPLOAD_POST_FIELDS: Dict[str, Tuple[str, ...]] = {
    "likes": ("likes",), "reposts": ("reposts",), "replies": ("comments", "replies"), "quotes": ("quotes",),
    "bookmarks": ("favorites", "bookmarks"), "impressions": ("impressions",), "views": ("views",),
    "shares": ("shares",), "saves": ("saves",), "reach": ("reach",),
}
UPLOAD_POST_ACCOUNT_FIELDS: Dict[str, Tuple[str, ...]] = {"followers": ("followers",)}

_BLUESKY, _X, _UPLOAD_POST = "bluesky", "x", "upload_post"

#: A platform step's verdict: carry on, stop this platform for this tick (the day stays open), or
#: stop it for the rest of the ET day (a definite refusal).
_GO, _STOP_TICK, _STOP_TODAY = "go", "stop_tick", "stop_today"
_RETRY_SINGLY = "retry_singly"

_STATUS_COUNTER = {"ok": "measured", "missing": "missing", "error": "errors", "unavailable": "unavailable",
                   "capped": "capped", "no_external_id": "no_external_id"}
_COUNTER_KEYS = ("ran", "posts", "measured", "missing", "errors", "capped", "unavailable", "no_external_id",
                 "accounts", "written", "not_written", "x_reads", "bluesky_calls", "upload_post_calls",
                 "would_read", "incomplete")

_COUNT_TEXT_RE = re.compile(r"[0-9]{1,15}")
_X_ID_RE = re.compile(r"[0-9]{1,25}")
#: X post ids are snowflakes: (id >> 22) is milliseconds since this instant (2010-11-04 01:42:54.657 UTC).
_X_EPOCH_MS = 1_288_834_974_657
#: The snowflake time is trusted over `published_at` only when the two are this close (a hand-edited
#: id is not a snowflake).
_X_TIME_TRUST = timedelta(days=1)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")

# ── in-process state (ONE web worker; the publisher loop is sequential) ────────────────────────

#: job → {"day", "attempts", "done", "disabled_until"} for the CURRENT ET day.
_DAY_JOBS: Dict[str, Dict[str, Any]] = {}
#: platform → (the ET day a definite refusal stopped it for, the note that names the stop — repeated
#: by every later run that day, so the job's `last_error` keeps saying why).
_STOPPED: Dict[str, Tuple[date, str]] = {}
#: message key → the ET day it was last logged (once-a-day lines).
_LOGGED: Dict[str, date] = {}
#: The last measure run in this process (`last_measure`).
_LAST: Dict[str, Any] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clock() -> float:
    """Monotonic seconds — the read budget's clock. A seam for tests: never patch `time.monotonic`
    itself, the event loop runs on it."""
    return time.monotonic()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _err(e: BaseException) -> str:
    """`Type: message`, secrets scrubbed and folded onto ONE line: the message may carry upstream text
    (an X problem title, an Upload-Post reason) whose line break would forge a log line of its own."""
    return " ".join(_CONTROL_RE.sub(" ", scrub(f"{type(e).__name__}: {e}")).split())


def _log_once(today: date, key: str, level: int, msg: str, *args: Any) -> None:
    if _LOGGED.get(key) != today:
        _LOGGED[key] = today
        logger.log(level, msg, *args)


# ── run_day_job: one claimed run per ET day ────────────────────────────────────────────────────


def _state_day(value: Any) -> Optional[date]:
    """`notification_job_state.run_day` (a DATE) as a date; anything unreadable → None."""
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


def _work_result(result: Any) -> Tuple[bool, int, Optional[str]]:
    """(ok, items, error) from what a job's work returned: `True` / `False`, `(ok, items)` or
    `(ok, items, error)`. Anything but an explicit True is a failure — the day stays open."""
    items, error = 0, None
    if isinstance(result, tuple) and result:
        ok = result[0] is True
        if len(result) > 1 and type(result[1]) is int and result[1] >= 0:
            items = result[1]
        if len(result) > 2 and result[2] is not None:
            error = scrub(result[2])
    else:
        ok = result is True
    if not ok and error is None:
        error = "the work did not finish — retried on a later tick"
    return ok, items, error


async def run_day_job(
    job: str,
    work: Callable[[], Awaitable[Any]],
    *,
    due: Callable[[datetime], bool],
    stale_seconds: int,
    now: Optional[datetime] = None,
    owed_since: Optional[Callable[[date], date]] = None,
) -> str:
    """Run `work` at most once per ET day under the cross-instance day claim
    (`notification_jobs.claimed_scheduled_job`, America/New_York), and record the day done only when
    it succeeded — so a failure is retried on a later tick. Returns one of NOT_DUE, DONE, EXHAUSTED,
    DISABLED, UNREADABLE, BUSY, OK, FAILED.

    * `due(now)` — the hour / weekday test. It runs FIRST, before any I/O and before the claim (a
      tick just after midnight must never claim and settle the day early).
    * `work()` — returns True when the day's work is complete (it may return `(ok, items)` or
      `(ok, items, error)`); False, anything else, or an exception leaves the day open. Exceptions are
      logged with the stack, never raised (CancelledError is: the claim is released as a failure).
      An `error` returned WITH success is kept as the job's `last_error` — a note on a day that
      succeeded (`finish_scheduled_job` stores it either way, migration 147), e.g. the platform the
      measure step stopped or paused — so a degraded day never reads as a clean one.
    * `owed_since(today)` — the earliest ET day whose recorded success also covers today (default:
      today). The weekly digest passes this week's Monday, so its Tuesday catch-up runs only when
      Monday's never succeeded.

    No I/O at all when not due, when this process already finished the job today, or after
    MAX_DAY_ATTEMPTS claimed attempts today. Otherwise one read of the job's state:
    `enabled = false` skips it without a deploy (re-read every DISABLED_RECHECK_SECONDS), an
    unreadable state skips the tick, and a recorded success covering today ends the day here."""
    at = _aware(now) if now is not None else _now()
    if not due(at):
        return NOT_DUE
    today = run_date_et(at)
    slot = _DAY_JOBS.get(job)
    if slot is None or slot.get("day") != today:
        slot = {"day": today, "attempts": 0, "done": False, "disabled_until": 0.0}
        _DAY_JOBS[job] = slot
    if slot["done"]:
        return DONE
    if slot["attempts"] >= MAX_DAY_ATTEMPTS:
        return EXHAUSTED
    if slot["disabled_until"] and _clock() < slot["disabled_until"]:
        return DISABLED
    state = await asyncio.to_thread(notification_jobs.scheduled_job_state, job)
    if state is None:
        logger.warning("marketing job %s: its state could not be read — skipped this tick (fail closed)", job)
        return UNREADABLE
    if not state.get("enabled", True):
        slot["disabled_until"] = _clock() + DISABLED_RECHECK_SECONDS
        logger.info("marketing job %s: disabled (notification_job_state.enabled) — skipped", job)
        return DISABLED
    slot["disabled_until"] = 0.0
    owed = owed_since(today) if owed_since is not None else today
    last = _state_day(state.get("run_day"))
    if last is not None and last >= owed:
        slot["done"] = True
        return DONE
    ok = False
    async with notification_jobs.claimed_scheduled_job(job, timezone_name=JOB_TIMEZONE,
                                                       stale_seconds=stale_seconds) as run:
        if run is None:
            return BUSY
        slot["attempts"] += 1
        try:
            result = await work()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("marketing job %s: attempt %d/%d FAILED (%s: %s) — retried on a later tick", job,
                         slot["attempts"], MAX_DAY_ATTEMPTS, type(e).__name__, e, exc_info=True)
            result = (False, 0, f"{type(e).__name__}: {e}")
        ok, items, error = _work_result(result)
        # The error rides on a success too: there it is a note (a platform stopped or paused today).
        run.items, run.success, run.error = items, ok, error
    if ok:
        slot["done"] = True
        return OK
    if slot["attempts"] >= MAX_DAY_ATTEMPTS:
        logger.error("marketing job %s: all %d attempts for %s failed — it waits for the next ET day",
                     job, MAX_DAY_ATTEMPTS, today.isoformat())
    return FAILED


# ── pure helpers: counts, snapshots, the metrics document ───────────────────────────────────────


def normalize_count(value: Any) -> Optional[int]:
    """A platform count as a non-negative int of at most 15 digits, or None (OMITTED — never 0).

    Kept: a non-bool int, an integral finite float (12.0 → 12), a string of 1-15 ASCII digits
    exactly ("12", "007"). Omitted: None, a bool, a negative, NaN / inf, a fractional float, a
    larger number, any other string (" 12", "1e3", "", "١٢"), any other type."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= COUNT_MAX else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        n = int(value)
        return n if 0 <= n <= COUNT_MAX else None
    if isinstance(value, str):
        return int(value) if len(value) <= 15 and _COUNT_TEXT_RE.fullmatch(value) else None
    return None


def extract_counts(raw: Any, fields: Mapping[str, Sequence[str]]) -> Dict[str, int]:
    """{snapshot key: count} from a platform's metrics object: for each key the first of its field
    names holding a readable count (`normalize_count`); a key with none is absent."""
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, int] = {}
    for key, aliases in fields.items():
        for alias in aliases:
            n = normalize_count(raw.get(alias))
            if n is not None:
                out[key] = n
                break
    return out


def age_hours(published_at: Optional[datetime], now: datetime) -> Optional[float]:
    """Hours from `published_at` to `now`, one decimal; None when unknown or negative."""
    if published_at is None:
        return None
    hours = round((_aware(now) - _aware(published_at)).total_seconds() / 3600.0, 1)
    if hours < 0:
        return None
    return 0.0 if hours == 0 else hours


def _valid_cp(value: Any) -> Optional[int]:
    return value if type(value) is int and value in outlet_x.METRICS_CHECKPOINT_DAYS else None


def build_snapshot(counts: Mapping[str, Any], *, now: datetime, published_at: Optional[datetime],
                   cp: Optional[int] = None) -> Dict[str, Any]:
    """One `last` / `history` entry: the ET day and UTC time measured, the post's age in hours, the X
    checkpoint (None elsewhere) and every readable count — an unreadable one is omitted."""
    at = _aware(now)
    snap: Dict[str, Any] = {"day": run_date_et(at).isoformat(), "at": at.astimezone(timezone.utc).isoformat(),
                            "age_h": age_hours(published_at, at), "cp": _valid_cp(cp)}
    source = counts if isinstance(counts, Mapping) else {}
    for key in COUNT_KEYS:
        n = normalize_count(source.get(key))
        if n is not None:
            snap[key] = n
    return snap


def snapshot_has_counts(snapshot: Any) -> bool:
    return isinstance(snapshot, Mapping) and any(normalize_count(snapshot.get(k)) is not None for k in COUNT_KEYS)


def _clean_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """A snapshot re-validated key by key (apply_measurement trusts nothing it is handed)."""
    age = snapshot.get("age_h")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age < 0:
        age = None
    snap: Dict[str, Any] = {
        "day": snapshot.get("day") if isinstance(snapshot.get("day"), str) else None,
        "at": snapshot.get("at") if isinstance(snapshot.get("at"), str) else None,
        "age_h": age, "cp": _valid_cp(snapshot.get("cp")),
    }
    for key in COUNT_KEYS:
        n = normalize_count(snapshot.get(key))
        if n is not None:
            snap[key] = n
    return snap


def clean_note(note: Any) -> Optional[str]:
    """A note fit for the ledger and a Telegram line: secrets scrubbed, one line, at most NOTE_MAX."""
    if note is None:
        return None
    text = " ".join(_CONTROL_RE.sub(" ", scrub(note)).split())
    return text[:NOTE_MAX] or None


def valid_checkpoints(value: Any) -> List[int]:
    """The real checkpoints in a stored `cps`, sorted and unique; anything else is dropped."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({c for c in value if _valid_cp(c) is not None})


def crossed_checkpoints(published_at: Optional[datetime], now: datetime) -> List[int]:
    """The checkpoints (days) a post published at `published_at` has reached by `now`."""
    if published_at is None:
        return []
    age = _aware(now) - _aware(published_at)
    return [cp for cp in outlet_x.METRICS_CHECKPOINT_DAYS if age >= timedelta(days=cp)]


def x_due_checkpoint(published_at: Optional[datetime], now: datetime, cps: Any) -> Optional[int]:
    """The checkpoint an X post is due to be read for — the LARGEST one crossed, when it is not yet
    in `cps` — or None. A post 8 days old with nothing measured is read ONCE, for 7."""
    crossed = crossed_checkpoints(published_at, now)
    if not crossed:
        return None
    largest = max(crossed)
    return None if largest in valid_checkpoints(cps) else largest


def x_post_time(external_id: Any) -> Optional[datetime]:
    """When X created a post, from its snowflake id; None for anything that is not one."""
    if not isinstance(external_id, str) or not _X_ID_RE.fullmatch(external_id):
        return None
    try:
        return datetime.fromtimestamp(((int(external_id) >> 22) + _X_EPOCH_MS) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def x_read_center(external_id: Any, published_at: Optional[datetime]) -> Optional[datetime]:
    """The instant the read window is centred on: X's own creation time (the snowflake) when it
    agrees with `published_at` to within a day — a post found by reconcile may carry a late
    `published_at` — else `published_at`."""
    if published_at is None:
        return None
    made = x_post_time(external_id)
    if made is not None and abs(made - _aware(published_at)) <= _X_TIME_TRUST:
        return made
    return _aware(published_at)


def apply_measurement(old: Any, *, platform: str, today: date, status: str, note: Any = None,
                      snapshot: Optional[Mapping[str, Any]] = None, cps: Iterable[Any] = (),
                      unavailable_until: Optional[date] = None) -> Dict[str, Any]:
    """The metrics document after one measurement. Pure (a deep copy of `old`).

    Sets `v`, `measured_day`, `status`, `note` and `unavailable_until` (kept only on `unavailable`).
    A snapshot WITH counts becomes `last` and replaces today's `history` entry (bounded: 30, 4 on
    X); a snapshot without any count turns the status into `error` / `no_metrics` and adds nothing.
    On X, `cps` is merged into the stored checkpoints. A non-`ok` result keeps the last good
    snapshot. `rev` is the writer's (`merge_post_metrics`)."""
    if status not in STATUSES:
        raise ValueError(f"unknown metrics status {status!r}")
    doc = copy.deepcopy(old) if isinstance(old, dict) else {}
    snap: Optional[Dict[str, Any]] = None
    if snapshot is not None:
        if snapshot_has_counts(snapshot):
            snap = _clean_snapshot(snapshot)
        else:
            status, note = "error", "no_metrics"
    doc["v"] = METRICS_VERSION
    doc["measured_day"] = today.isoformat()
    doc["status"] = status
    doc["note"] = clean_note(note)
    keep_until = (status == "unavailable" and isinstance(unavailable_until, date)
                  and not isinstance(unavailable_until, datetime))
    doc["unavailable_until"] = unavailable_until.isoformat() if keep_until else None
    if platform == _X:
        doc["cps"] = sorted(set(valid_checkpoints(doc.get("cps"))) | set(valid_checkpoints(list(cps))))
    if snap is not None:
        history = doc.get("history") if isinstance(doc.get("history"), list) else []
        kept = [h for h in history if isinstance(h, dict) and h.get("day") != snap["day"]]
        kept.append(snap)
        doc["history"] = kept[-(X_HISTORY_MAX if platform == _X else HISTORY_MAX):]
        doc["last"] = snap
    return doc


def build_account(raw: Any, fields: Mapping[str, Sequence[str]], *, now: datetime,
                  source: str) -> Optional[Dict[str, Any]]:
    """{"followers", "following", "posts", "at", "source"} from a profile object, each count only when
    readable; None when not one count is."""
    counts = extract_counts(raw, fields)
    if not counts:
        return None
    account: Dict[str, Any] = {k: counts[k] for k in ACCOUNT_KEYS if k in counts}
    account.update({"at": _aware(now).astimezone(timezone.utc).isoformat(), "source": source})
    return account


def apply_account(old: Any, account: Mapping[str, Any]) -> Dict[str, Any]:
    """The metrics document with a new account snapshot — nothing else changes (not `measured_day`:
    the post's own counts are not measured by an account read). Pure."""
    doc = copy.deepcopy(old) if isinstance(old, dict) else {}
    doc["v"] = METRICS_VERSION
    doc["account"] = dict(account)
    return doc


def apply_backoff(old: Any, key: str, until: date) -> Dict[str, Any]:
    """The metrics document with a platform-wide back-off (`key` = `until`) — nothing else changes: not
    `status`, `measured_day` or `unavailable_until` (no post was measured: the post carrying it is only
    the platform's newest) and not `account` (the last follower snapshot stays the digest's). Pure.

    `key` must be one of BACKOFF_KEYS (ValueError otherwise): this writer can never overwrite a
    measurement field, whatever it is handed."""
    if key not in BACKOFF_KEYS:
        raise ValueError(f"unknown back-off key {key!r}")
    doc = copy.deepcopy(old) if isinstance(old, dict) else {}
    doc["v"] = METRICS_VERSION
    doc[key] = until.isoformat()
    return doc


def apply_account_refusal(old: Any, until: date) -> Dict[str, Any]:
    """The metrics document with the X account read's back-off (`X_ACCOUNT_REFUSED_KEY` = `until`):
    `apply_backoff`. Pure."""
    return apply_backoff(old, X_ACCOUNT_REFUSED_KEY, until)


# ── row readers ────────────────────────────────────────────────────────────────────────────────


def _doc(row: Mapping[str, Any]) -> Dict[str, Any]:
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _meta(row: Mapping[str, Any]) -> Dict[str, Any]:
    meta = row.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _publish_meta(row: Mapping[str, Any], key: str) -> Dict[str, Any]:
    pub = _meta(row).get("publish")
    inner = pub.get(key) if isinstance(pub, dict) else None
    return inner if isinstance(inner, dict) else {}


def _published(row: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_ts(row.get("published_at"))


def measured_today(row: Mapping[str, Any], today: date) -> bool:
    return _doc(row).get("measured_day") == today.isoformat()


def old_enough(row: Mapping[str, Any], now: datetime) -> bool:
    """Published at least MIN_MEASURE_AGE before `now` (an unreadable publish time is never)."""
    published = _published(row)
    return published is not None and _aware(now) - published >= MIN_MEASURE_AGE


def retract_requested(row: Mapping[str, Any]) -> bool:
    return bool(_meta(row).get("retract_requested_at"))


def _account_at(row: Mapping[str, Any]) -> Optional[datetime]:
    account = _doc(row).get("account")
    return _parse_ts(account.get("at")) if isinstance(account, dict) else None


def _newest(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The row published last (the later one on a tie)."""
    best, best_at = None, None
    for row in rows:
        at = _published(row)
        if at is not None and (best_at is None or at >= best_at):
            best, best_at = row, at
    return best


def bluesky_uri(row: Mapping[str, Any]) -> Optional[str]:
    """The post's at://did:…/app.bsky.feed.post/<rkey> URI: the external id, else the one the publish
    recorded, else rebuilt from the recorded repo DID and record key — None when none is one."""
    stored = _publish_meta(row, "bluesky")
    for candidate in (row.get("external_id"), stored.get("uri")):
        if isinstance(candidate, str) and bluesky.is_post_uri(candidate):
            return candidate
    repo, rkey = stored.get("repo"), stored.get("rkey")
    if isinstance(repo, str) and isinstance(rkey, str):
        uri = f"at://{repo}/{bluesky.POST_COLLECTION}/{rkey}"
        if bluesky.is_post_uri(uri):
            return uri
    return None


def _x_external_id(row: Mapping[str, Any]) -> Optional[str]:
    ext = row.get("external_id")
    return ext if isinstance(ext, str) and _X_ID_RE.fullmatch(ext) else None


def _upload_post_id(row: Mapping[str, Any]) -> Optional[str]:
    """The id Upload-Post answers for this job (`poll_id`), else our own `request_id`."""
    stored = _publish_meta(row, "upload_post")
    for key in ("poll_id", "request_id"):
        value = stored.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _backoff_date(value: Any, today: date) -> Optional[date]:
    """A stored back-off date still in force: after `today`, and no more than UNAVAILABLE_DAYS ahead
    (we only ever write today + UNAVAILABLE_DAYS, so a later one — a hand edit, a corrupt row — is
    ignored rather than obeyed: it could pause a platform for years). None otherwise."""
    until = _iso_date(value)
    if until is None or not today < until <= today + timedelta(days=UNAVAILABLE_DAYS):
        return None
    return until


def _backoff_in_force(rows: Sequence[Dict[str, Any]], key: str, today: date) -> Optional[date]:
    """The latest back-off date in force (`_backoff_date`) stored under `key` on ANY of `rows` — the post
    that was the newest when it was written may no longer be — else None."""
    dates = (_backoff_date(_doc(r).get(key), today) for r in rows)
    return max((d for d in dates if d is not None), default=None)


def _paused_until(rows: Sequence[Dict[str, Any]], today: date, key: str) -> Optional[Tuple[date, str]]:
    """A platform-wide back-off — X's refused post read (`X_READS_REFUSED_KEY`), Upload-Post's plan
    refusal (`UPLOAD_POST_PLAN_REFUSED_KEY`): the LATEST date in force (`_backoff_date`) across the
    platform's marker `key` on any of `rows` (kept on the newest post, so it outlives the refused one)
    and the `unavailable_until` of any of `rows` whose status is `unavailable` (the refused post itself,
    while it is still listed). With it, the note of a refused post carrying that date — for the job's
    `last_error` — else "" (the caller names a generic reason). None when nothing is in force. The
    platform is read again ON that date."""
    best = _backoff_in_force(rows, key, today)
    note = ""
    for row in rows:
        doc = _doc(row)
        if doc.get("status") != "unavailable":
            continue
        until = _backoff_date(doc.get("unavailable_until"), today)
        if until is None:
            continue
        text = doc.get("note") if isinstance(doc.get("note"), str) else ""
        if best is None or until > best:
            best, note = until, text
        elif until == best and not note:
            note = text
    return None if best is None else (best, note)


def _x_account_paused_until(rows: Sequence[Dict[str, Any]], today: date) -> Optional[date]:
    """The X account read's own back-off: the latest `X_ACCOUNT_REFUSED_KEY` in force on ANY X post (the
    one that was the newest when `get_me` was refused may no longer be), else None."""
    return _backoff_in_force(rows, X_ACCOUNT_REFUSED_KEY, today)


# ── one run ───────────────────────────────────────────────────────────────────────────────────


def _new_counters() -> Dict[str, int]:
    return {key: 0 for key in _COUNTER_KEYS}


def _publisher():
    """`publisher_service`, imported when used: it imports this module for its tick, and the X cap
    (`_budgets`) and charge dating (`_charge_at`) are reused from it, never copied."""
    from app.services.marketing import publisher_service

    return publisher_service


class _Run:
    """One measure run: the ledger, the frozen `now`, the counters, the read budget and the freshest
    copy of every row it has written (the next write's `observed`)."""

    def __init__(self, svc: Any, now: datetime, counters: Dict[str, int]) -> None:
        self.svc = svc
        self.now = now
        self.today = run_date_et(now)
        self.counters = counters
        self.deadline = _clock() + READ_BUDGET_SECONDS
        self.rows: Dict[str, Dict[str, Any]] = {}
        #: The posts whose metrics document this run wrote — the job's `items_written`, which the digest
        #: shows as "N posts written" (the `written` counter counts WRITES: counts + account = 2).
        self.written_ids: set = set()
        self.complete = True
        self.reasons: List[str] = []
        #: Every platform this run stopped or found paused, as one cleaned line each — the job's
        #: `last_error` even when the day succeeds (`_measure_day`).
        self.stops: List[str] = []
        self._timed_out = False
        self.ledger_failures = 0

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def note_stop(self, note: str) -> None:
        """Name a stop or a pause in the job's `last_error` (scrubbed, one line, capped — `clean_note`),
        once per run. A definite refusal does not fail the day, but it must never look like a clean one."""
        text = clean_note(note)
        if text and text not in self.stops:
            self.stops.append(text)

    def hold(self, reason: str) -> None:
        """Leave the day open: a later tick runs the measure again (what is done stays done)."""
        self.complete = False
        if reason not in self.reasons:
            self.reasons.append(reason)

    def ledger_down(self) -> bool:
        return self.ledger_failures >= LEDGER_FAILURES_MAX

    def ledger_failed(self, *, hold: bool = True) -> None:
        """Count a ledger failure toward the breaker; hold the day unless the write was best-effort."""
        self.ledger_failures += 1
        if hold:
            self.hold("ledger")
        if self.ledger_failures == LEDGER_FAILURES_MAX:
            logger.error("marketing measure: %d ledger writes failed in this run — no more reads or writes this "
                         "tick (a result that cannot be stored is a read for nothing)", LEDGER_FAILURES_MAX)

    def time_left(self) -> bool:
        """May a best-effort read start (no hold when it may not)?"""
        return _clock() < self.deadline and not self.ledger_down()

    def out_of_time(self) -> bool:
        """True once the tick's read budget is spent or the ledger keeps failing — and then the day
        stays open."""
        if self.ledger_down():
            self.hold("ledger")
            return True
        if _clock() < self.deadline:
            return False
        if not self._timed_out:
            self._timed_out = True
            logger.info("marketing measure: the %d-second read budget of this tick is spent — the rest "
                        "waits for a later tick", int(READ_BUDGET_SECONDS))
        self.hold("read_budget")
        return True

    def latest(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return self.rows.get(str(row.get("id")), dict(row))

    async def _write(self, row: Mapping[str, Any], merge: Callable[[Dict[str, Any]], Dict[str, Any]], *,
                     what: str, hold: bool) -> bool:
        pid = str(row.get("id"))
        if self.ledger_down():
            if hold:
                self.hold("ledger")
            return False
        try:
            updated = await self.svc.merge_post_metrics(pid, observed=self.latest(row), merge=merge,
                                                        expect_status="published", retries=1)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("marketing measure: %s NOT written post_id=%s platform=%s (%s: %s)%s", what, pid,
                         row.get("platform"), type(e).__name__, e, " — retried on a later tick" if hold else "",
                         exc_info=True)
            self.ledger_failed(hold=hold)
            return False
        if updated is None:
            self.bump("not_written")
            logger.info("marketing measure: %s not written post_id=%s platform=%s — the post left `published` "
                        "(retracted meanwhile), or another writer kept moving its metrics", what, pid,
                        row.get("platform"))
            return False
        self.rows[pid] = updated
        self.written_ids.add(pid)
        self.bump("written")
        return True

    async def record(self, row: Mapping[str, Any], *, status: str, note: Any = None,
                     snapshot: Optional[Mapping[str, Any]] = None, cps: Iterable[Any] = (),
                     unavailable_until: Optional[date] = None, hold: bool = True) -> bool:
        """Merge one measurement into the post's metrics (`apply_measurement`)."""
        platform = str(row.get("platform") or "")
        effective = "error" if snapshot is not None and not snapshot_has_counts(snapshot) else status
        cps = list(cps)

        def merge(old: Dict[str, Any]) -> Dict[str, Any]:
            return apply_measurement(old, platform=platform, today=self.today, status=status, note=note,
                                     snapshot=snapshot, cps=cps, unavailable_until=unavailable_until)

        written = await self._write(row, merge, what=f"metrics ({effective})", hold=hold)
        if written:
            self.bump(_STATUS_COUNTER[effective])
        return written

    async def record_account(self, row: Mapping[str, Any], account: Mapping[str, Any]) -> bool:
        """Store an account snapshot on `row` (the newest post of its platform). Never holds the day:
        a retry would only re-read — and on X re-pay — the same number."""
        written = await self._write(row, lambda old: apply_account(old, account), what="account", hold=False)
        if written:
            self.bump("accounts")
        return written

    async def record_backoff(self, row: Mapping[str, Any], key: str, until: date) -> bool:
        """Store a platform-wide back-off on `row` — the platform's NEWEST post (`apply_backoff`). Never
        holds the day: a retry would only pay for the same refusal again."""
        return await self._write(row, lambda old: apply_backoff(old, key, until), what=f"back-off ({key})",
                                 hold=False)

    async def record_account_refusal(self, row: Mapping[str, Any], until: date) -> bool:
        """Store the X account read's back-off on `row` (`record_backoff`, X_ACCOUNT_REFUSED_KEY)."""
        return await self.record_backoff(row, X_ACCOUNT_REFUSED_KEY, until)

    async def record_no_id(self, row: Mapping[str, Any], note: str, *, hold: bool = True) -> None:
        """`no_external_id`, written once — a row that already says so is left alone (no daily write)."""
        if _doc(row).get("status") == "no_external_id":
            return
        await self.record(row, status="no_external_id", note=note, hold=hold)


def _stopped(run: _Run, platform: str) -> bool:
    """True when a definite refusal stopped `platform` earlier this ET day (in this process) — and then
    this run names that stop again, so a later run the same day keeps it in the job's `last_error`."""
    entry = _STOPPED.get(platform)
    if not isinstance(entry, tuple) or len(entry) != 2 or entry[0] != run.today:
        return False
    run.note_stop(entry[1])
    return True


def _stop_for_today(run: _Run, platform: str, why: str, *, note: Optional[str] = None,
                    level: int = logging.ERROR) -> None:
    """Stop `platform` for the rest of the ET day: `why` is logged in full; `note` (default
    "<platform> stopped for <day>: <why>") is what the job's `last_error` says."""
    text = note or f"{platform} stopped for {run.today.isoformat()}: {why}"
    _STOPPED[platform] = (run.today, text)
    run.note_stop(text)
    logger.log(level, "marketing measure: %s reads STOPPED for %s — %s", platform, run.today.isoformat(), why)


# ── Bluesky ────────────────────────────────────────────────────────────────────────────────────


def _bluesky_verdict(e: BaseException) -> str:
    if isinstance(e, (bluesky.BlueskyAuthError, bluesky.BlueskyExpiredTokenError, bluesky.BlueskyInvalidSwapError,
                      bluesky.BlueskyNotConfiguredError)):
        return _STOP_TODAY        # the public AppView takes no credential: a definite, global refusal
    if isinstance(e, bluesky.BlueskyRefusedError):
        # A 400 (or our own pre-send refusal) on a batch may be one bad URI: ask one at a time.
        return _RETRY_SINGLY if getattr(e, "status", None) in (400, None) else _STOP_TODAY
    return _STOP_TICK             # 429, 5xx, transport, an unreadable answer — or a bug of ours


def _bluesky_stop(run: _Run, e: BaseException, verdict: str) -> str:
    if verdict == _STOP_TODAY:
        _stop_for_today(run, _BLUESKY, f"the AppView refused: {_err(e)}")
        return _STOP_TODAY
    run.hold("bluesky")
    if isinstance(e, bluesky.BlueskyException):
        logger.warning("marketing measure: Bluesky read failed (%s) — Bluesky waits for a later tick", _err(e))
    else:
        logger.error("marketing measure: Bluesky read BUG (%s) — retried on a later tick", _err(e),
                     exc_info=(type(e), e, e.__traceback__))
    return _STOP_TICK


async def _record_views(run: _Run, batch: Sequence[Tuple[Dict[str, Any], str]], views: Any) -> str:
    if not isinstance(views, list):
        run.hold("bluesky")
        logger.error("marketing measure: bluesky.get_posts answered %s, not a list — retried on a later tick",
                     type(views).__name__)
        return _STOP_TICK
    by_uri: Dict[str, Dict[str, Any]] = {}
    for view in views:
        if isinstance(view, dict) and isinstance(view.get("uri"), str):
            by_uri.setdefault(view["uri"], view)
    for row, uri in batch:
        view = by_uri.get(uri)
        if view is None:
            await run.record(row, status="missing", note="the Bluesky AppView does not return it (deleted or hidden)")
            continue
        snapshot = build_snapshot(extract_counts(view, BLUESKY_FIELDS), now=run.now, published_at=_published(row))
        await run.record(row, status="ok", snapshot=snapshot)
    return _GO


async def _bluesky_singly(run: _Run, batch: Sequence[Tuple[Dict[str, Any], str]]) -> str:
    for row, uri in batch:
        if run.out_of_time():
            return _STOP_TICK
        try:
            run.bump("bluesky_calls")
            views = await bluesky.get_posts([uri])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            verdict = _bluesky_verdict(e)
            if verdict == _RETRY_SINGLY:
                await run.record(row, status="error", note=f"the Bluesky AppView refused this post: {_err(e)}")
                continue
            return _bluesky_stop(run, e, verdict)
        step = await _record_views(run, [(row, uri)], views)
        if step != _GO:
            return step
    return _GO


async def _bluesky_batch(run: _Run, batch: Sequence[Tuple[Dict[str, Any], str]]) -> str:
    try:
        run.bump("bluesky_calls")
        views = await bluesky.get_posts([uri for _row, uri in batch])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        verdict = _bluesky_verdict(e)
        if verdict == _RETRY_SINGLY:
            logger.warning("marketing measure: the Bluesky AppView refused a batch of %d (%s) — asking one post "
                           "at a time", len(batch), _err(e))
            return await _bluesky_singly(run, batch)
        return _bluesky_stop(run, e, verdict)
    return await _record_views(run, batch, views)


async def _bluesky_account(run: _Run, rows: Sequence[Dict[str, Any]]) -> None:
    """Followers onto the newest Bluesky post, once a day (free). Best-effort: a failure is logged and
    never holds the day open."""
    newest = _newest(rows)
    if newest is None:
        return
    held = _account_at(newest)
    if held is not None and run_date_et(held) == run.today:
        return
    ordered = sorted((r for r in rows if _published(r) is not None), key=lambda r: _published(r), reverse=True)
    uri = next((u for u in (bluesky_uri(r) for r in ordered) if u), None)
    if uri is None or not run.time_left():
        return
    did = uri[len("at://"):].split("/", 1)[0]
    try:
        profile = await bluesky.get_profile(did)
    except asyncio.CancelledError:
        raise
    except bluesky.BlueskyException as e:
        logger.warning("marketing measure: Bluesky followers not read (%s) — tried again tomorrow", _err(e))
        return
    except Exception as e:
        logger.error("marketing measure: Bluesky profile read BUG (%s)", _err(e), exc_info=True)
        return
    account = build_account(profile, BLUESKY_ACCOUNT_FIELDS, now=run.now, source="bluesky_appview")
    if account is None:
        logger.warning("marketing measure: the Bluesky profile carried no readable count — no follower snapshot")
        return
    await run.record_account(newest, account)


async def _measure_bluesky(run: _Run, rows: List[Dict[str, Any]]) -> None:
    if not rows or _stopped(run, _BLUESKY):
        return
    due: List[Tuple[Dict[str, Any], str]] = []
    for row in rows:
        if measured_today(row, run.today) or not old_enough(row, run.now):
            continue
        uri = bluesky_uri(row)
        if uri is None:
            await run.record_no_id(row, "no at://did:…/app.bsky.feed.post/… URI recorded for this post")
            continue
        due.append((row, uri))
    for start in range(0, len(due), bluesky.MAX_GET_POSTS):
        if run.out_of_time():
            return
        if await _bluesky_batch(run, due[start:start + bluesky.MAX_GET_POSTS]) != _GO:
            return
    await _bluesky_account(run, rows)


# ── X ──────────────────────────────────────────────────────────────────────────────────────────


def _x_error_plan(e: BaseException) -> Tuple[bool, str]:
    """(refund the reserve?, verdict) for a failed X read. Refund only when the error PROVES nothing
    was billed; keep the reserve otherwise, as reconcile does (an over-count only pauses X early)."""
    if isinstance(e, x_api.XApiNotConfiguredError):
        return True, _STOP_TODAY
    if isinstance(e, x_api.XApiNotSentError):
        return True, _STOP_TICK
    if isinstance(e, x_api.XApiRateLimitError):
        return True, _STOP_TICK
    if isinstance(e, x_api.XApiCreditsDepletedError):           # 402 — before its RefusedError parent
        return True, _STOP_TODAY
    if isinstance(e, x_api.XApiAmbiguousError):                 # 5xx, 408, 3xx, a timeout, an odd 2xx
        return False, _STOP_TICK
    if isinstance(e, x_api.XApiRefusedError):
        if getattr(e, "status", None) is None:                  # refused locally: nothing was sent
            return True, _GO
        return False, _STOP_TODAY                               # 400 / 401 / 403 / 404: a definite refusal
    return False, _STOP_TICK


def _x_refused(e: BaseException) -> bool:
    """A DEFINITE refusal of an X read — X answered 400 / 401 / 403 / 404 (any status-bearing 4xx
    refusal but 402), so the same read is refused again tomorrow and its reserve stands: exactly the
    errors `_x_error_plan` answers with (keep the reserve, stop for today). These earn the seven-day
    back-off. Never 402 (no credits: nothing billed, refunded), never a refusal this process made
    before sending (status None), never a 429, a 5xx or a duplicate-content 403 (ambiguous)."""
    refund, verdict = _x_error_plan(e)
    return isinstance(e, x_api.XApiRefusedError) and not refund and verdict == _STOP_TODAY


def _headroom_note(headroom: int) -> str:
    """Why a post was not read: the cap's headroom, in posts (`outlet_x.METRICS_HEADROOM_POSTS`) and in
    dollars at today's post price (a URL post costs $0.20 while MARKETING_X_ALLOW_URLS is on)."""
    return (f"the monthly X cap keeps {outlet_x.METRICS_HEADROOM_POSTS} posts of headroom "
            f"(${headroom / 1e6:.3f}) — not read")


async def _journal(run: _Run, row: Mapping[str, Any], op: str, micros: int, *, reverses: str) -> bool:
    """A correction or refund, dated at the charge it reverses (`publisher_service._charge_at`), on the
    row in `published` OR `retracted` — a post retracted mid-read still gets its money right. True when
    it landed; False leaves the reserve in the journal (an over-count), and the caller's running total
    must then not move either."""
    pid = str(row.get("id"))
    at = _publisher()._charge_at(run.latest(row), reverses)
    try:
        updated = await run.svc.transition_post(pid, expect_status=("published", "retracted"),
                                                observed=run.latest(row), retries=2,
                                                charge=(op, int(micros), at))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing measure: %s %+d micros NOT journaled post_id=%s (%s: %s) — the journal keeps "
                       "the reserve (an over-count: the X cap can only pause early)", op, micros, pid,
                       type(e).__name__, e)
        run.ledger_failed()
        return False
    if updated is None:
        logger.warning("marketing measure: %s %+d micros NOT journaled post_id=%s — the row is gone or no longer "
                       "published/retracted", op, micros, pid)
        return False
    run.rows[pid] = updated
    return True


async def _x_charge(run: _Run, row: Mapping[str, Any], op: str, micros: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """The write-ahead charge of one read, fenced on the row as observed. (row, _GO) when charged;
    (None, _GO) when the row moved meanwhile (the day stays open); (None, _STOP_TICK) on a ledger
    error. Nothing is sent before this lands."""
    pid = str(row.get("id"))
    try:
        charged = await run.svc.transition_post(pid, expect_status="published", observed=run.latest(row),
                                                retries=0, charge=(op, int(micros)))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing measure: %s NOT charged post_id=%s (%s: %s) — nothing was sent; retried on a "
                     "later tick", op, pid, type(e).__name__, e, exc_info=True)
        run.ledger_failed()
        return None, _STOP_TICK
    if charged is None:
        logger.info("marketing measure: post_id=%s changed since it was listed — its %s waits for a later tick",
                    pid, op)
        run.hold("x_busy")
        return None, _GO
    run.rows[pid] = charged
    return charged, _GO


async def _x_failed(run: _Run, charged: Mapping[str, Any], e: BaseException, budget: Any, *, reserve: int,
                    op: str, reverses: str, what: str, paused_until: Optional[date] = None) -> str:
    """Settle the reserve of a failed read (`_x_error_plan`) and act on the verdict. `paused_until` (a
    post read's definite refusal) names the back-off the caller records in the stop's note."""
    refund, verdict = _x_error_plan(e)
    if refund and await _journal(run, charged, op, -reserve, reverses=reverses):
        budget.add(-reserve)
    kept = "refunded" if refund else "the reserve stands: X may have billed it"
    if verdict == _STOP_TODAY and paused_until is not None:
        until = paused_until.isoformat()
        _stop_for_today(run, _X, f"{what}: {_err(e)} ({kept}) — every X read is paused until {until}",
                        note=f"x paused until {until}: X refused the read: {_err(e)}")
    elif verdict == _STOP_TODAY:
        _stop_for_today(run, _X, f"{what}: {_err(e)} ({kept})")
    elif verdict == _STOP_TICK:
        run.hold("x")
        logger.warning("marketing measure: X %s failed (%s; %s) — X waits for a later tick", what, _err(e), kept)
    else:
        logger.warning("marketing measure: X %s refused before sending (%s; %s)", what, _err(e), kept)
    return verdict


async def _x_read_post(run: _Run, row: Dict[str, Any], cp: int, published: datetime, budget: Any,
                       user_id: str, *, newest: Optional[Dict[str, Any]]) -> str:
    """Read one X post (`newest`: the newest listed X post — where a definite refusal's X-wide back-off
    is kept)."""
    pid = str(row.get("id"))
    reserve = outlet_x.METRICS_READ_RESERVE_MICROS
    # Priced NOW (MARKETING_X_ALLOW_URLS may have changed): a URL post reserves $0.20, not $0.015.
    headroom = outlet_x.metrics_headroom_micros()
    if not await budget.allows(reserve + headroom):
        if budget.unreadable:
            # A ledger blip, not a cap: no read, no charge, nothing recorded — a later tick decides.
            run.hold("x_spend_unreadable")
            return _STOP_TICK
        _log_once(run.today, "x_capped", logging.INFO,
                  "marketing measure: X reads paused for %s — %s of %s micros spent this month, and a read needs "
                  "its %s reserve plus %s of headroom (%d posts)", run.today.isoformat(), budget.spent,
                  budget.budget, reserve, headroom, outlet_x.METRICS_HEADROOM_POSTS)
        await run.record(row, status="capped",
                         note=("X is off (MARKETING_X_MONTHLY_BUDGET_USD is 0)" if budget.budget <= 0 else
                               _headroom_note(headroom)))
        return _GO
    charged, step = await _x_charge(run, row, OP_X_READ, reserve)
    if charged is None:
        return step
    budget.add(reserve)
    run.bump("x_reads")
    center = x_read_center(_x_external_id(row), published) or published
    try:
        res = await x_api.list_user_posts_metrics(user_id, start_time=center - outlet_x.METRICS_WINDOW,
                                                  end_time=center + outlet_x.METRICS_WINDOW,
                                                  max_results=X_PAGE_SIZE)
    except asyncio.CancelledError:
        raise
    except x_api.XApiException as e:
        until = run.today + timedelta(days=UNAVAILABLE_DAYS) if _x_refused(e) else None
        verdict = await _x_failed(run, charged, e, budget, reserve=reserve, op=OP_X_READ_CORRECTION,
                                  reverses="_metrics_read", what=f"read of post_id={pid}", paused_until=until)
        if verdict == _GO:
            await run.record(row, status="error", note=f"X read refused before sending: {_err(e)}")
        elif until is not None:
            # A definite refusal is refused again tomorrow, and its reserve stands: without a back-off the
            # same refusal was paid for every day. The pause is the X-wide marker on the NEWEST X post —
            # written first, it is what `_measure_x` obeys: the refused post is usually the OLDEST due one
            # and leaves the 30-day listing within a day or two, so a pause kept only on it ended early.
            # The post itself records `unavailable` (no checkpoint: still due once the pause ends) for
            # the digest.
            await run.record_backoff(newest if newest is not None else row, X_READS_REFUSED_KEY, until)
            await run.record(row, status="unavailable", note=f"X refused the read: {_err(e)}",
                             unavailable_until=until, hold=False)
        return verdict
    except Exception as e:
        run.hold("x")
        logger.error("marketing measure: X read BUG post_id=%s (%s) — the reserve stands; retried on a later tick",
                     pid, _err(e), exc_info=True)
        return _STOP_TICK
    posts = res.get("posts") if isinstance(res, dict) else None
    if not isinstance(posts, list):
        run.hold("x")
        logger.error("marketing measure: X read of post_id=%s answered no posts list — the reserve stands", pid)
        return _STOP_TICK
    # X bills the posts a read returns. A `result_count` above the page we asked for is corrupt — it
    # must not journal a charge that pauses X for the month — but more posts than asked are billed.
    billed = max(len(posts), min(normalize_count(res.get("result_count")) or 0, X_PAGE_SIZE))
    real = billed * outlet_x.OWNED_READ_MICROS
    if real != reserve and await _journal(run, charged, OP_X_READ_CORRECTION, real - reserve,
                                          reverses="_metrics_read"):
        budget.add(real - reserve)
    problem = res.get("problem")
    if not posts and problem:
        # A 200 carrying only `errors` (a suspended or protected account, a resource X could not return)
        # is NOT an empty window: read as one, the post would be `missing` and its checkpoints used up.
        # Recorded as an error with NO checkpoint — it stays due, and this read cost nothing (the
        # correction above refunded it) — and X stops for today: the problem is the account's.
        note = problem if isinstance(problem, str) else "X answered the read with errors only"
        await run.record(row, status="error", note=note)
        _stop_for_today(run, _X, f"read of post_id={pid} answered with errors only: {note}",
                        note=f"x stopped for {run.today.isoformat()}: {note}")
        return _STOP_TODAY
    ext = _x_external_id(row)
    match = next((p for p in posts if isinstance(p, dict) and p.get("id") == ext), None)
    crossed = crossed_checkpoints(published, run.now)
    if match is None:
        # Read and paid for: the crossed checkpoints count as measured, so a deleted post is not paid
        # for again every day.
        if res.get("next_token"):
            await run.record(row, status="error", note="the X window holds more than one page of posts",
                             cps=crossed)
        else:
            await run.record(row, status="missing", note="not in the account's X timeline (deleted?)",
                             cps=crossed)
        return _GO
    snapshot = build_snapshot(extract_counts(match.get("public_metrics"), X_FIELDS), now=run.now,
                              published_at=published, cp=cp)
    await run.record(row, status="ok", snapshot=snapshot, cps=crossed)
    return _GO


def _x_account_due(rows: Sequence[Dict[str, Any]], now: datetime, today: date) -> bool:
    """On Mondays (ET) unless already read today, or when the newest stored X account snapshot is
    missing or more than eight days old — but never while the account read's own back-off is in force
    (`_x_account_paused_until`: X refused it)."""
    if _x_account_paused_until(rows, today) is not None:
        return False
    latest = max((at for at in (_account_at(r) for r in rows) if at is not None), default=None)
    if latest is None or now - latest > X_ACCOUNT_STALE:
        return True
    return now.astimezone(ET).weekday() == 0 and run_date_et(latest) != today


async def _x_account_refused(run: _Run, newest: Dict[str, Any], e: BaseException) -> None:
    """A DEFINITE refusal of the account read (`get_me`): its charge stands (X may bill a refused read)
    and it gets its OWN seven-day back-off — `X_ACCOUNT_REFUSED_KEY` on the newest X post, never its
    `status` or `account` — so it is not paid for again every day. It never stops X for the day: the
    post reads are another endpoint, and a User-read refusal must never pause them."""
    until = run.today + timedelta(days=UNAVAILABLE_DAYS)
    await run.record_account_refusal(newest, until)
    run.note_stop(f"x account read paused until {until.isoformat()}: X refused it: {_err(e)}")
    logger.error("marketing measure: X REFUSED the account read (get_me) (%s; the charge stands: X may have "
                 "billed it) — no account read before %s; the post reads go on", _err(e), until.isoformat())


async def _x_read_account(run: _Run, newest: Dict[str, Any], budget: Any) -> None:
    if run.out_of_time():
        return
    cost = outlet_x.USER_READ_MICROS
    headroom = outlet_x.metrics_headroom_micros()
    if not await budget.allows(cost + headroom):
        if budget.unreadable:
            run.hold("x_spend_unreadable")
        else:
            logger.info("marketing measure: X follower read skipped — %s", _headroom_note(headroom))
        return
    charged, _step = await _x_charge(run, newest, OP_X_ACCOUNT, cost)
    if charged is None:
        return
    budget.add(cost)
    run.bump("x_reads")
    try:
        res = await x_api.get_me()
    except asyncio.CancelledError:
        raise
    except x_api.XApiException as e:
        if _x_refused(e):
            await _x_account_refused(run, newest, e)
            return
        await _x_failed(run, charged, e, budget, reserve=cost, op=OP_X_ACCOUNT_CORRECTION,
                        reverses="_account_read", what="account read (get_me)")
        return
    except Exception as e:
        run.hold("x")
        logger.error("marketing measure: X account read BUG (%s) — the charge stands", _err(e), exc_info=True)
        return
    account = build_account(res.get("public_metrics") if isinstance(res, dict) else None, X_ACCOUNT_FIELDS,
                            now=run.now, source="x_api")
    if account is None:
        logger.warning("marketing measure: the X account answer carried no readable count — no follower snapshot")
        return
    await run.record_account(newest, account)


async def _measure_x(run: _Run, rows: List[Dict[str, Any]]) -> None:
    if not rows or _stopped(run, _X):
        return
    if not x_api.configured():
        _log_once(run.today, "x_not_configured", logging.INFO,
                  "marketing measure: X credentials are not set — %d X post(s) not measured", len(rows))
        return
    user_id = x_api.user_id_from_access_token()
    if not user_id:
        _log_once(run.today, "x_no_user", logging.ERROR,
                  "marketing measure: no user id in MARKETING_X_ACCESS_TOKEN — X posts are not measured")
        return
    paused = _paused_until(rows, run.today, X_READS_REFUSED_KEY)
    if paused is not None:
        # X refused a read: EVERY X read (posts and the account) waits for the back-off's day — no charge.
        until, why = paused
        _log_once(run.today, "x_paused", logging.WARNING,
                  "marketing measure: every X read is paused until %s (%s) — nothing read or charged today",
                  until.isoformat(), why or "X refused a read")
        run.note_stop(f"x paused until {until.isoformat()}: {why or 'X refused a read'}")
        return
    account_paused = _x_account_paused_until(rows, run.today)
    if account_paused is not None:
        run.note_stop(f"x account read paused until {account_paused.isoformat()} (X refused it)")
    newest = _newest(rows)
    due: List[Tuple[Dict[str, Any], int, datetime]] = []
    for row in rows:
        if measured_today(row, run.today):
            continue
        published = _published(row)
        cp = x_due_checkpoint(published, run.now, _doc(row).get("cps"))
        if cp is None or published is None:
            continue
        if _x_external_id(row) is None:
            await run.record_no_id(row, "no X post id recorded (an owner-confirmed post?)")
            continue
        due.append((row, cp, published))
    account_due = newest is not None and _x_account_due(rows, run.now, run.today)
    if not due and not account_due:
        return
    if settings.MARKETING_DRY_RUN:
        for row, cp, _published_at in due:
            logger.info("marketing measure DRY_RUN: would read X post_id=%s at its %d-day checkpoint (≤ $%.3f)",
                        row.get("id"), cp, outlet_x.METRICS_READ_RESERVE_MICROS / 1e6)
            run.bump("would_read")
        if account_due:
            logger.info("marketing measure DRY_RUN: would read the X account (get_me, $%.3f)",
                        outlet_x.USER_READ_MICROS / 1e6)
            run.bump("would_read")
        return
    budget = _publisher()._budgets(run.svc).get(_X)
    if budget is None:
        return
    for row, cp, published in due:
        if run.out_of_time():
            return
        step = await _x_read_post(run, row, cp, published, budget, user_id, newest=newest)
        if step in (_STOP_TICK, _STOP_TODAY):
            return
    if account_due and newest is not None:
        await _x_read_account(run, newest, budget)


# ── Upload-Post ────────────────────────────────────────────────────────────────────────────────


def _upload_post_order(row: Dict[str, Any]) -> Tuple[str, float]:
    """Never measured first, then the least recently measured; newest published first among equals."""
    day = _doc(row).get("measured_day")
    published = _published(row)
    return (day if isinstance(day, str) else "", -(published.timestamp() if published else 0.0))


async def _upload_post_unavailable(run: _Run, row: Dict[str, Any], e: BaseException,
                                   newest: Optional[Dict[str, Any]]) -> str:
    """A plan refusal: every Upload-Post read waits a week. As on X, the pause is a marker on the NEWEST
    Upload-Post post (`newest`, any platform), written first — the refused post may leave the 30-day
    listing during the week — and the refused post records `unavailable` for the digest."""
    until = run.today + timedelta(days=UNAVAILABLE_DAYS)
    await run.record_backoff(newest if newest is not None else row, UPLOAD_POST_PLAN_REFUSED_KEY, until)
    await run.record(row, status="unavailable", note=f"plan: {_err(e)}", unavailable_until=until, hold=False)
    _stop_for_today(run, _UPLOAD_POST, f"its plan refuses analytics ({_err(e)}) — next try on "
                    f"{until.isoformat()}", note=f"upload_post paused until {until.isoformat()}: plan: {_err(e)}",
                    level=logging.WARNING)
    return _STOP_TODAY


def _stale_followers(entry: Mapping[str, Any], today: date) -> Optional[date]:
    """The date of an Upload-Post follower snapshot (`followers_date`) when it is more than
    UPLOAD_POST_FOLLOWERS_MAX_AGE before `today` — a stored snapshot the digest would date by our read —
    else None. A missing or unreadable date is not stale (stored as read today, as before)."""
    snapshot_day = _iso_date(entry.get("followers_date"))
    if snapshot_day is None or snapshot_day >= today - UPLOAD_POST_FOLLOWERS_MAX_AGE:
        return None
    return snapshot_day


async def _upload_post_read(run: _Run, row: Dict[str, Any], rid: str, is_newest: bool, *,
                            newest_any: Optional[Dict[str, Any]]) -> str:
    """Read one Upload-Post post. `is_newest`: the newest post of ITS platform (where that platform's
    follower snapshot goes); `newest_any`: the newest Upload-Post post of any platform (where a plan
    refusal's pause is kept)."""
    platform = str(row.get("platform"))
    try:
        run.bump("upload_post_calls")
        res = await upload_post.get_post_analytics(rid, platform=platform)
    except asyncio.CancelledError:
        raise
    except (upload_post.UploadPostReauthError, upload_post.UploadPostAuthError, upload_post.UploadPostQuotaError,
            upload_post.UploadPostNotConfiguredError) as e:
        _stop_for_today(run, _UPLOAD_POST, _err(e), level=logging.WARNING)
        return _STOP_TODAY
    except upload_post.UploadPostPlanError as e:
        return await _upload_post_unavailable(run, row, e, newest_any)
    except upload_post.UploadPostNotConnectedError as e:
        await run.record(row, status="error", note=_err(e), hold=False)
        return _GO
    except upload_post.UploadPostRefusedError as e:
        status = getattr(e, "status", None)
        if status == 402:
            return await _upload_post_unavailable(run, row, e, newest_any)
        if status == 404:
            await run.record(row, status="missing", note="Upload-Post has no such post (404)", hold=False)
        elif status is None:
            await run.record(row, status="no_external_id", note=f"the stored job id was refused: {_err(e)}",
                             hold=False)
        else:
            await run.record(row, status="error", note=_err(e), hold=False)
        return _GO
    except upload_post.UploadPostAmbiguousError as e:   # 5xx, 408, an odd 2xx, a timeout — about THIS post
        # Recorded, so this post leaves the head of the queue (`_upload_post_order`: least recently
        # measured first): one post whose read keeps failing must never starve every other post for
        # 30 days. A non-ok status keeps its last good snapshot.
        logger.warning("marketing measure: Upload-Post read of post_id=%s failed (%s) — recorded as an error; "
                       "Upload-Post waits; it never holds the day open", row.get("id"), _err(e))
        await run.record(row, status="error", note=_err(e), hold=False)
        return _STOP_TICK
    except upload_post.UploadPostException as e:     # 429, not sent: about Upload-Post, never this post
        logger.warning("marketing measure: Upload-Post read failed (%s) — Upload-Post waits; it never holds "
                       "the day open", _err(e))
        return _STOP_TICK
    except Exception as e:
        logger.error("marketing measure: Upload-Post read BUG post_id=%s (%s)", row.get("id"), _err(e),
                     exc_info=True)
        # Recorded like an unknown outcome: a post that trips a bug must not stay first in line forever.
        await run.record(row, status="error", note=_err(e), hold=False)
        return _STOP_TICK
    platforms = res.get("platforms") if isinstance(res, dict) else None
    entry = platforms.get(platform) if isinstance(platforms, dict) else None
    if not isinstance(entry, dict):
        await run.record(row, status="error", note=f"Upload-Post answered without {platform}", hold=False)
        return _GO
    metrics = entry.get("post_metrics")
    if isinstance(metrics, dict):
        snapshot = build_snapshot(extract_counts(metrics, UPLOAD_POST_FIELDS), now=run.now,
                                  published_at=_published(row))
        await run.record(row, status="ok", snapshot=snapshot, hold=False)
    else:
        why = entry.get("post_metrics_error")
        await run.record(row, status="error", hold=False,
                         note=f"upload-post: {why}" if isinstance(why, str) and why.strip() else "no_metrics")
    if is_newest:
        account = build_account({"followers": entry.get("followers")}, UPLOAD_POST_ACCOUNT_FIELDS, now=run.now,
                                source="upload_post")
        stale = _stale_followers(entry, run.today) if account is not None else None
        if stale is not None:
            # The digest dates a snapshot by `at` (our read): a stored count days old would pass for today's.
            _log_once(run.today, f"upload_post_stale_followers:{platform}", logging.INFO,
                      "marketing measure: Upload-Post's %s follower count is a snapshot of %s — more than a day "
                      "before %s, so it is not stored", platform, stale.isoformat(), run.today.isoformat())
        elif account is not None:
            await run.record_account(row, account)
    return _GO


async def _measure_upload_post(run: _Run, rows: List[Dict[str, Any]]) -> None:
    """Best-effort: nothing here ever holds the day open."""
    if not rows or _stopped(run, _UPLOAD_POST):
        return
    if not upload_post.configured():
        _log_once(run.today, "upload_post_not_configured", logging.INFO,
                  "marketing measure: Upload-Post is not configured — %d post(s) not measured", len(rows))
        return
    paused = _paused_until(rows, run.today, UPLOAD_POST_PLAN_REFUSED_KEY)
    if paused is not None:
        until, why = paused
        _log_once(run.today, "upload_post_blocked", logging.INFO,
                  "marketing measure: Upload-Post analytics paused until %s (a plan refusal)", until.isoformat())
        run.note_stop(f"upload_post paused until {until.isoformat()}: {why or 'a plan refusal'}")
        return
    read_today = sum(1 for r in rows if measured_today(r, run.today) and _doc(r).get("status") != "no_external_id")
    left = UPLOAD_POST_DAILY_READS - read_today
    newest = {p: _newest([r for r in rows if r.get("platform") == p]) for p in {r.get("platform") for r in rows}}
    newest_any = _newest(rows)
    queue = (r for r in rows if not measured_today(r, run.today) and old_enough(r, run.now))
    for row in sorted(queue, key=_upload_post_order):
        if left <= 0 or not run.time_left():
            return
        rid = _upload_post_id(row)
        if rid is None:
            await run.record_no_id(row, "no Upload-Post job id recorded", hold=False)
            continue
        left -= 1
        if await _upload_post_read(run, row, rid, newest.get(row.get("platform")) is row,
                                   newest_any=newest_any) != _GO:
            return


# ── the step ───────────────────────────────────────────────────────────────────────────────────


def metrics_due(now: datetime) -> bool:
    """The measure step's hour test: at or after 06:00 ET (any day)."""
    return _aware(now).astimezone(ET).hour >= METRICS_HOUR_ET


async def _measure_day(counters: Dict[str, int], *, now: datetime) -> Tuple[bool, int, Optional[str]]:
    """The day's work. (complete, posts whose metrics were written — once each, however many writes —
    why it is incomplete)."""
    counters["ran"] = 1
    svc = get_marketing_run_service()
    run = _Run(svc, now, counters)
    try:
        rows = await svc.list_measurable_posts(since=now - MEASURE_MAX_AGE, limit=MEASURE_SCAN_LIMIT)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        counters["incomplete"] = 1
        logger.error("marketing measure: the published posts could not be listed (%s: %s) — retried on a later "
                     "tick", type(e).__name__, e, exc_info=True)
        return False, 0, f"list_measurable_posts: {type(e).__name__}: {e}"
    if len(rows) >= MEASURE_SCAN_LIMIT:
        logger.warning("marketing measure: %d published posts hit the scan limit — the newest may be missed today",
                       len(rows))
    groups: Dict[str, List[Dict[str, Any]]] = {_BLUESKY: [], _X: [], _UPLOAD_POST: []}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id") or retract_requested(row):
            continue
        platform = row.get("platform")
        group = (_BLUESKY if platform == "bluesky" else _X if platform == "x"
                 else _UPLOAD_POST if platform in outlet_upload_post.PLATFORMS else None)
        if group is None:
            continue
        groups[group].append(row)
        run.rows[str(row["id"])] = row
    counters["posts"] = sum(len(g) for g in groups.values())
    for name, step in ((_BLUESKY, _measure_bluesky), (_X, _measure_x), (_UPLOAD_POST, _measure_upload_post)):
        try:
            await step(run, groups[name])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A bug in one platform's step never stops the others; it holds the day open unless it is
            # Upload-Post's (best-effort).
            logger.error("marketing measure: the %s step FAILED (%s: %s)", name, type(e).__name__, e, exc_info=True)
            if name != _UPLOAD_POST:
                run.hold(f"{name}_bug")
    why = ", ".join(run.reasons)
    # Every platform stopped or paused today, named even on a complete day: `run_day_job` keeps it as
    # the job's `last_error` (the digest's "note:"), so a refused platform never reads as a clean day.
    stops = "; ".join(run.stops) or None
    if run.complete:
        logger.info("marketing measure %s: complete %s%s", run.today.isoformat(), counters,
                    f" — {stops}" if stops else "")
        return True, len(run.written_ids), stops
    counters["incomplete"] = 1
    logger.info("marketing measure %s: INCOMPLETE (%s) — retried on a later tick %s", run.today.isoformat(), why,
                counters)
    return False, len(run.written_ids), f"incomplete: {why}" + (f"; {stops}" if stops else "")


async def measure_cycle(*, now: Optional[datetime] = None) -> Dict[str, int]:
    """The publisher tick's measure step. Gated on MARKETING_ENABLED and MARKETING_METRICS_ENABLED
    (the tick checks them too); once per ET day from 06:00 (`run_day_job`). Returns the counters for
    the tick's log line — all zero when nothing ran. Never raises except CancelledError."""
    counters = _new_counters()
    if not (settings.MARKETING_ENABLED and settings.MARKETING_METRICS_ENABLED):
        return counters
    at = _aware(now) if now is not None else _now()
    try:
        outcome = await run_day_job(JOB_METRICS, lambda: _measure_day(counters, now=at), due=metrics_due,
                                    stale_seconds=METRICS_STALE_SECONDS, now=at)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing measure: the step FAILED (%s: %s)", type(e).__name__, e, exc_info=True)
        return counters
    if counters.get("ran"):
        _LAST.clear()
        _LAST.update({"at": at.isoformat(), "day": run_date_et(at).isoformat(), "outcome": outcome,
                      "counters": dict(counters)})
    return counters


def last_measure() -> Optional[Dict[str, Any]]:
    """The last measure run in THIS process — {"at", "day", "outcome", "counters"} — or None (none
    since the last restart; the durable record is the `marketing_metrics_daily` job state)."""
    return copy.deepcopy(_LAST) if _LAST else None
