"""
The marketing MEASURE step and the shared day-job runner (`app/services/marketing/metrics_service.py`,
design doc §12.11) — and, under their own banner below, the weekly digest and the run-health alert
(`digest_service.py`).

Hermetic. The ledger is the in-memory PostgREST fake (`FakeSupabase`, test_marketing_run_service.py)
behind the real `MarketingRunService`; the day-claim RPCs of `notification_jobs` are `FakeJobs` (below)
and, for the digest's cycles, `DigestJobs` (migration 147's claim/finish SQL on a simulated clock);
Telegram is the `FakeTelegram` MockTransport from test_marketing_review_bot.py; every platform read is a
scripted fake patched onto the integration module the service calls through — except the wire tests,
which drive the REAL clients over the client suites' own `httpx.MockTransport` fakes (imported, not
copied). conftest blocks sockets anyway. Dates are frozen by passing `now=`.

What must never regress (measure):
  * a count is stored only when it is an honest non-negative integer — anything else is OMITTED, never
    0; a read with no count at all is `error` / `no_metrics` and leaves the history alone;
  * metrics are written only through `merge_post_metrics`: the publisher's `updated_at` fence and the
    row's `metadata` stay byte-identical on a free read;
  * X is paid: read only at an uncrossed checkpoint, charged BEFORE the call, corrected after, refunded
    only when nothing was billed, never inside the cap's headroom (four posts at TODAY's post price —
    $0.20 each while MARKETING_X_ALLOW_URLS is on), never under DRY_RUN; the prices are pinned as literals;
  * a transient failure leaves the day open, a definite refusal stops that platform for the day, and a
    second run the same day reads nothing that is already measured;
  * a definite X refusal (400/401/403/404) costs ONE reserve a week, not one a day — also at the real
    posting cadence, where the refused post is the oldest due one and leaves the 30-day listing within
    days: the post records `unavailable`, the pause rides on the NEWEST X post (`x_reads_refused_until`)
    and every X read waits for the back-off's day; a refused account read has its own back-off and never
    pauses the post reads; an errors-only 200 never uses up a checkpoint;
  * every stop or pause is named in the job's `last_error`, also on a day that succeeds — and no read is
    paid inside a pause a note has named;
  * Upload-Post is best-effort: at most 10 a day, a plan refusal pauses it for a week (one call a week at
    the real cadence: the pause rides on the newest Upload-Post post), one post whose read keeps failing
    is recorded and rotates to the back (it never starves the others), a stale follower snapshot is not
    stored, and nothing it does holds the day open.

What must never regress (digest and run health):
  * a value that cannot be read is OMITTED or says "unreadable" — never shown as 0 (a junk count, a
    snapshot with no time, a spend read that failed);
  * no caption, title or other model text ever reaches a message; every server string is scrubbed
    (`outlet_base.scrub`), folded onto one line and length-capped;
  * the digest is ONE plain-text message within 4,096 UTF-16 units (rows capped first, a hard cut at a
    line break last), about the previous Monday–Sunday in ET dates — DST included;
  * the schedule: digest Monday from 09:00 ET with a Tuesday catch-up only when Monday's never
    succeeded; health on posting days only, from the worker's run hour + MARKETING_MAX_RUN_ATTEMPTS
    (capped at 23; never with run hour 23), its final word from the run hour the day after a posting
    day — 22:00 and 16:00 ET with the defaults; the web's MARKETING_RUN_HOUR_ET default equals the
    worker's; the hour/weekday test runs BEFORE any claim;
  * a job is marked done only after its Telegram send succeeded; a 429 sets the shared back-off;
  * at most one message per run-health job per ET day, and a run-health message says only what the
    worker's own claim rules make true (the attempts cap, the run's liveness, the last COMPLETED stage);
  * the final word is judged against WHEN the nightly check judged the run (its own job state's
    `last_run_at`), never a guessed 22:00: a verdict that check delivered is not repeated, a run it saw
    finished is not "recovered", and a check that never went out (three Telegram 500s) leaves a failed
    day to the final word — never silence; an unreadable job state skips the tick.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import settings
from app.integrations import bluesky, telegram, upload_post, x_api
from app.services import notification_jobs
from app.services.marketing import digest_service as ds
from app.services.marketing import metrics_service as ms
from app.services.marketing import outlet_x
from app.services.marketing import review_service as rs
from app.services.marketing import run_service as mrs
from app.services.marketing import selection
from app.services.marketing.review_service import REJECT_REASONS, utf16_len
from test_marketing_bluesky import FakeBluesky as WireBluesky
from test_marketing_review_bot import OWNER, SECRET, TOKEN, FakeTelegram
from test_marketing_run_service import FakeSupabase, _Table
from test_marketing_x_api import _CREDS as X_CREDS
from test_marketing_x_api import _answer as x_wire_answer
from test_marketing_x_api import _install as x_wire_install

ET = ZoneInfo("America/New_York")
MS_LOGGER = ms.__name__
DID = "did:plc:caydexmeasure01"
X_USER = "1234567890"            # the numeric prefix of test_marketing_x_api's access token
BUDGET = 2_000_000
#: The X prices the measure step pays, as LITERAL micro-dollars — never bound to the constants they
#: check (review 2026-10-01 #12: `RESERVE = outlet_x.METRICS_READ_RESERVE_MICROS` let a mutated price
#: pass every test). `test_the_x_measure_prices_are_pinned_in_literal_micros` pins the constants to these.
RESERVE = 5_000            # one metrics read's write-ahead reserve: a 5-post page at $0.001
HEADROOM = 60_000          # 4 posts x $0.015 — MARKETING_X_ALLOW_URLS off (the env fixture pins it off)
URL_HEADROOM = 800_000     # 4 posts x $0.20 — MARKETING_X_ALLOW_URLS on
USER_READ = 10_000         # GET /2/users/me, billed as a User read

#: The real clients, captured before any test patches them (the wire tests put them back).
REAL_X_METRICS = x_api.list_user_posts_metrics
REAL_GET_POSTS = bluesky.get_posts
REAL_GET_PROFILE = bluesky.get_profile

_AUTO: Any = object()


def et(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    """An ET wall-clock time as an aware UTC datetime (zoneinfo applies DST)."""
    return datetime(y, mo, d, h, mi, s, tzinfo=ET).astimezone(timezone.utc)


#: Thursday 2026-10-08, 12:00 EDT (16:00 UTC) — a posting day that is not a Monday.
THU = et(2026, 10, 8, 12)
#: Monday 2026-10-05, 09:00 EDT.
MON = et(2026, 10, 5, 9)


def snowflake(at: datetime, seq: int = 0) -> str:
    """A real-shaped X post id created at `at` (X ids are snowflakes)."""
    return str(((int(at.timestamp() * 1000) - 1_288_834_974_657) << 22) | seq)


def _real_now() -> datetime:
    return datetime.now(timezone.utc)


# ── the day-claim ledger (notification_job_state) ───────────────────────────────────────────────


class FakeJobs:
    """`notification_jobs.scheduled_job_state` / `claim_scheduled` / `finish_scheduled` in memory, with
    migration 147's rules: one successful run per ET day (`run_day`), a held claim refuses another,
    `enabled = false` refuses. "Today" is the ET day of `self.now` (the test's frozen clock)."""

    def __init__(self) -> None:
        self.now: datetime = _real_now()
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.reads: List[str] = []
        self.claims: List[str] = []
        self.finishes: List[Dict[str, Any]] = []
        self.unreadable = False
        #: Jobs another instance holds right now (their claim is refused).
        self.held_by_other: set = set()

    def row(self, job: str) -> Dict[str, Any]:
        return self.rows.setdefault(job, {"run_day": None, "claim_at": None, "enabled": True,
                                          "last_error": None, "items_written": 0})

    def today(self) -> date:
        return self.now.astimezone(ET).date()

    def state(self, job: str) -> Optional[Dict[str, Any]]:
        self.reads.append(job)
        if self.unreadable:
            return None
        r = self.row(job)
        return {"job": job, "run_day": r["run_day"], "claim_at": r["claim_at"], "enabled": r["enabled"]}

    def claim(self, job: str, *, timezone_name: str = "UTC", now: Any = None, stale_seconds: Any = None) -> bool:
        self.claims.append(job)
        assert timezone_name == "America/New_York", timezone_name
        r = self.row(job)
        if (job in self.held_by_other or not r["enabled"] or r["claim_at"]
                or r["run_day"] == self.today().isoformat()):
            return False
        r["claim_at"] = self.now.isoformat()
        return True

    def finish(self, job: str, *, success: bool, items: int = 0, error: Optional[str] = None,
               timezone_name: str = "UTC", now: Any = None) -> None:
        r = self.row(job)
        r["claim_at"] = None
        if success:
            r["run_day"] = self.today().isoformat()
        r["last_error"], r["items_written"] = error, items
        self.finishes.append({"job": job, "success": success, "items": items, "error": error})


@pytest.fixture
def jobs(monkeypatch):
    fake = FakeJobs()
    monkeypatch.setattr(notification_jobs, "scheduled_job_state", fake.state)
    monkeypatch.setattr(notification_jobs, "claim_scheduled", fake.claim)
    monkeypatch.setattr(notification_jobs, "finish_scheduled", fake.finish)
    return fake


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch):
    """Every test starts as a freshly booted process: no day-job memo, no platform stopped for the day."""
    monkeypatch.setattr(ms, "_DAY_JOBS", {})
    monkeypatch.setattr(ms, "_STOPPED", {})
    monkeypatch.setattr(ms, "_LOGGED", {})
    monkeypatch.setattr(ms, "_LAST", {})


# ── the post ledger ─────────────────────────────────────────────────────────────────────────────


def bsky_uri(n: int) -> str:
    return f"at://{DID}/app.bsky.feed.post/3lbmeasure{n:04d}"


class Ledger:
    """marketing_posts on the PostgREST fake, behind the real service."""

    def __init__(self) -> None:
        self.fake = FakeSupabase()
        self.svc = mrs.MarketingRunService(supabase=self.fake)
        self._n = 0

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return self.fake.tables[mrs.POSTS].rows

    def raw(self, pid: str) -> Dict[str, Any]:
        return next(r for r in self.rows if r["id"] == pid)

    def row(self, pid: str) -> Dict[str, Any]:
        return copy.deepcopy(self.raw(pid))

    def metrics(self, pid: str) -> Dict[str, Any]:
        return self.row(pid).get("metrics") or {}

    def journal(self, pid: str) -> List[tuple]:
        meta = self.row(pid).get("metadata") or {}
        return [(c["op"], c["micros"]) for c in meta.get("charges") or []]

    def post(self, platform: str = "bluesky", *, published_at: datetime, status: str = "published",
             external_id: Any = _AUTO, metrics: Any = None, meta: Any = None, **cols: Any) -> str:
        """A published post. X ids are snowflakes of `published_at`; Bluesky ids are at:// URIs;
        Upload-Post posts carry a job id in `metadata.publish.upload_post`."""
        self._n += 1
        n = self._n
        pid = str(uuid.uuid4())
        day = published_at.astimezone(ET).date()
        if external_id is _AUTO:
            external_id = (snowflake(published_at, n) if platform == "x" else
                           bsky_uri(n) if platform == "bluesky" else f"{platform}-post-{n}")
        if meta is None:
            meta = {"dry_run": False, "publish": {"state": "published"}}
            if platform not in ("x", "bluesky"):
                meta["publish"]["upload_post"] = {"request_id": f"{day.isoformat()}:{platform}:video:a1",
                                                  "poll_id": f"up-job-{n:04d}"}
        row = {
            "id": pid, "run_id": str(uuid.uuid4()), "platform": platform, "format": "text", "status": status,
            "caption": f"Lesson {n}: time in the market compounds.",
            "idempotency_key": f"{day.isoformat()}:{platform}:text:{n}",
            "metadata": meta, "attempts": 1, "cost_micros": 0, "external_id": external_id,
            "external_url": f"https://{platform}.example/p/{n}",
            "published_at": published_at.isoformat(),
            "created_at": (published_at - timedelta(hours=4)).isoformat(),
            "updated_at": (published_at + timedelta(minutes=1)).isoformat(),
            "metrics": metrics if metrics is not None else {},
        }
        row.update(cols)
        self.rows.append(row)
        return pid

    def spend(self, micros: int) -> str:
        """An X post that already spent `micros` this (real) month — what the cap reads."""
        now = _real_now().isoformat()
        return self.post("x", published_at=THU - timedelta(days=40), cost_micros=micros, updated_at=now,
                         meta={"dry_run": False, "charges": [{"at": now, "op": "x_create", "micros": micros}]})


@pytest.fixture
def ledger(monkeypatch):
    led = Ledger()
    monkeypatch.setattr(ms, "get_marketing_run_service", lambda: led.svc)
    return led


def _messages(caplog, needle: str, level: int = logging.DEBUG) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= level and needle in r.getMessage()]


# ── run_day_job (shared by the measure step, the run-health alert and the weekly digest) ──────────


def _due_from(hour: int) -> Callable[[datetime], bool]:
    return lambda now: now.astimezone(ET).hour >= hour


def _work(results: List[Any], calls: List[int]):
    async def work():
        calls.append(1)
        item = results.pop(0) if results else True
        if isinstance(item, BaseException):
            raise item
        return item
    return work


@pytest.mark.asyncio
async def test_run_day_job_runs_once_a_day_and_does_no_io_before_its_hour(jobs):
    calls: List[int] = []
    work = _work([], calls)
    jobs.now = et(2026, 10, 8, 5, 59, 59)
    assert await ms.run_day_job("job_a", work, due=_due_from(6), stale_seconds=900, now=jobs.now) == ms.NOT_DUE
    assert jobs.reads == [] and jobs.claims == [] and calls == []

    jobs.now = et(2026, 10, 8, 6)
    assert await ms.run_day_job("job_a", work, due=_due_from(6), stale_seconds=900, now=jobs.now) == ms.OK
    assert calls == [1] and jobs.rows["job_a"]["run_day"] == "2026-10-08"
    assert jobs.finishes == [{"job": "job_a", "success": True, "items": 0, "error": None}]

    # The rest of the ET day: no read, no claim, no work.
    reads, claims = len(jobs.reads), len(jobs.claims)
    for later in (et(2026, 10, 8, 6, 10), et(2026, 10, 8, 23, 59, 59)):
        jobs.now = later
        assert await ms.run_day_job("job_a", work, due=_due_from(6), stale_seconds=900, now=later) == ms.DONE
    assert (len(jobs.reads), len(jobs.claims), calls) == (reads, claims, [1])

    # The next ET day runs again.
    jobs.now = et(2026, 10, 9, 6, 0, 1)
    assert await ms.run_day_job("job_a", work, due=_due_from(6), stale_seconds=900, now=jobs.now) == ms.OK
    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_a_failed_day_is_retried_at_most_three_claimed_attempts_per_process(jobs, caplog):
    caplog.set_level(logging.INFO, logger=MS_LOGGER)
    calls: List[int] = []
    work = _work([False, (False, 0, "x: HTTP 503"), RuntimeError("boom"), True], calls)
    start = et(2026, 10, 8, 7)
    outcomes = []
    for i in range(5):
        jobs.now = start + timedelta(minutes=10 * i)
        outcomes.append(await ms.run_day_job("job_b", work, due=_due_from(6), stale_seconds=900, now=jobs.now))
    assert outcomes == [ms.FAILED, ms.FAILED, ms.FAILED, ms.EXHAUSTED, ms.EXHAUSTED]
    assert calls == [1, 1, 1] and len(jobs.claims) == 3 and len(jobs.reads) == 3   # exhausted: no I/O
    assert [f["success"] for f in jobs.finishes] == [False, False, False]
    assert "did not finish" in jobs.finishes[0]["error"]
    assert jobs.finishes[1]["error"] == "x: HTTP 503"
    assert "RuntimeError: boom" in jobs.finishes[2]["error"]
    assert jobs.rows["job_b"]["run_day"] is None
    raised = [r for r in caplog.records if "attempt 3/3 FAILED" in r.getMessage()]
    assert raised and raised[0].exc_info and raised[0].levelno == logging.ERROR
    assert _messages(caplog, "all 3 attempts", logging.ERROR)

    # A new ET day resets the count.
    jobs.now = et(2026, 10, 9, 6, 30)
    assert await ms.run_day_job("job_b", work, due=_due_from(6), stale_seconds=900, now=jobs.now) == ms.OK
    assert jobs.rows["job_b"]["run_day"] == "2026-10-09"


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, 1, "yes", (True,), (1, 2), (), [True], {"ok": True}])
async def test_only_an_explicit_true_marks_the_day_done(jobs, result):
    jobs.now = THU

    async def work():
        return result

    outcome = await ms.run_day_job("job_c", work, due=_due_from(6), stale_seconds=900, now=THU)
    if result == (True,):
        assert outcome == ms.OK and jobs.finishes[-1]["success"] is True
    else:
        assert outcome == ms.FAILED and jobs.finishes[-1]["success"] is False
        assert jobs.rows["job_c"]["run_day"] is None


@pytest.mark.parametrize("result,expected", [
    (True, (True, 0, None)), ((True, 3), (True, 3, None)), ((True, -1), (True, 0, None)),
    ((True, True), (True, 0, None)), ((True, 2.0), (True, 0, None)), ((False, 4, "why"), (False, 4, "why")),
    ((True, 1, "ignored"), (True, 1, "ignored")),
])
def test_work_result_reads_items_and_errors_defensively(result, expected):
    ok, items, error = ms._work_result(result)
    assert (ok, items) == expected[:2]
    if expected[0]:
        assert error == expected[2]
    else:
        assert error == expected[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("result,error", [
    ((True, 2, "x paused until 2026-10-15: X refused the read"), "x paused until 2026-10-15: X refused the read"),
    ((True, 2, None), None),
    # Scrubbed like any job error: a credential never reaches notification_job_state.last_error.
    ((True, 1, "x refused: Bearer abcdefghijklmnop0123456789 token=supersecretvalue"), _AUTO),
])
async def test_a_successful_day_keeps_its_note_as_the_jobs_last_error(jobs, result, error):
    """Review 2026-10-01 #3: run_day_job dropped the error of a SUCCESSFUL day, so a platform the measure
    step stopped read as a clean day. Migration 147's finish stores last_error either way."""
    jobs.now = THU

    async def work():
        return result

    assert await ms.run_day_job("job_note", work, due=_due_from(6), stale_seconds=900, now=THU) == ms.OK
    fin = jobs.finishes[-1]
    assert fin["success"] is True and fin["items"] == result[1] and jobs.rows["job_note"]["run_day"] == "2026-10-08"
    if error is _AUTO:
        assert fin["error"].startswith("x refused:")
        assert "supersecretvalue" not in fin["error"] and "abcdefghijklmnop0123456789" not in fin["error"]
    else:
        assert fin["error"] == error and jobs.rows["job_note"]["last_error"] == error


@pytest.mark.asyncio
async def test_an_unreadable_state_skips_the_tick_without_claiming(jobs, caplog):
    caplog.set_level(logging.WARNING, logger=MS_LOGGER)
    calls: List[int] = []
    jobs.now, jobs.unreadable = THU, True
    out = await ms.run_day_job("job_d", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU)
    assert out == ms.UNREADABLE and jobs.claims == [] and calls == []
    assert _messages(caplog, "could not be read", logging.WARNING)
    jobs.unreadable = False   # the next tick reads again and runs
    assert await ms.run_day_job("job_d", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.OK


@pytest.mark.asyncio
async def test_a_disabled_job_is_skipped_and_re_read_only_after_the_recheck(jobs, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ms, "_clock", lambda: clock[0])
    calls: List[int] = []
    jobs.now = THU
    jobs.row("job_e")["enabled"] = False
    assert await ms.run_day_job("job_e", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.DISABLED
    assert (len(jobs.reads), jobs.claims, calls) == (1, [], [])
    clock[0] += ms.DISABLED_RECHECK_SECONDS - 1
    assert await ms.run_day_job("job_e", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.DISABLED
    assert len(jobs.reads) == 1               # not re-read inside the recheck window
    jobs.row("job_e")["enabled"] = True       # the operator flips it back
    clock[0] += 2
    assert await ms.run_day_job("job_e", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.OK
    assert calls == [1]


@pytest.mark.asyncio
async def test_a_day_another_instance_finished_is_done_without_a_claim(jobs):
    calls: List[int] = []
    jobs.now = THU
    jobs.row("job_f")["run_day"] = "2026-10-08"
    assert await ms.run_day_job("job_f", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.DONE
    assert await ms.run_day_job("job_f", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.DONE
    assert jobs.claims == [] and len(jobs.reads) == 1 and calls == []
    # A garbage run_day is "never ran", not a crash.
    jobs.row("job_g")["run_day"] = "not-a-date"
    assert await ms.run_day_job("job_g", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.OK


@pytest.mark.asyncio
async def test_a_claim_held_elsewhere_is_busy_and_spends_no_attempt(jobs):
    calls: List[int] = []
    jobs.now = THU
    jobs.held_by_other.add("job_h")
    for _ in range(5):
        assert await ms.run_day_job("job_h", _work([], calls), due=_due_from(6), stale_seconds=900,
                                    now=THU) == ms.BUSY
    assert calls == [] and jobs.finishes == []
    jobs.held_by_other.clear()
    assert await ms.run_day_job("job_h", _work([], calls), due=_due_from(6), stale_seconds=900, now=THU) == ms.OK


@pytest.mark.asyncio
async def test_a_cancelled_run_releases_the_claim_as_a_failure(jobs):
    calls: List[int] = []
    jobs.now = THU
    with pytest.raises(asyncio.CancelledError):
        await ms.run_day_job("job_i", _work([asyncio.CancelledError()], calls), due=_due_from(6),
                             stale_seconds=900, now=THU)
    assert jobs.finishes == [{"job": "job_i", "success": False, "items": 0, "error": "cancelled (shutdown)"}]
    assert jobs.rows["job_i"]["run_day"] is None and jobs.rows["job_i"]["claim_at"] is None


@pytest.mark.asyncio
async def test_owed_since_gives_the_weekly_digest_its_tuesday_catch_up(jobs):
    """Monday ≥ 09:00 ET, or Tuesday; a success recorded on or after this week's Monday covers it."""
    def due(now: datetime) -> bool:
        local = now.astimezone(ET)
        return (local.weekday() == 0 and local.hour >= 9) or local.weekday() == 1

    def monday(today: date) -> date:
        return today - timedelta(days=today.weekday())

    calls: List[int] = []
    jobs.now = et(2026, 10, 6, 9)                    # Tuesday
    jobs.row("digest")["run_day"] = "2026-10-05"     # Monday's succeeded
    assert await ms.run_day_job("digest", _work([], calls), due=due, stale_seconds=900, now=jobs.now,
                                owed_since=monday) == ms.DONE
    assert jobs.claims == [] and calls == []

    ms._DAY_JOBS.clear()                             # another process; Monday's never succeeded
    jobs.row("digest")["run_day"] = "2026-09-28"
    assert await ms.run_day_job("digest", _work([], calls), due=due, stale_seconds=900, now=jobs.now,
                                owed_since=monday) == ms.OK
    assert calls == [1] and jobs.rows["digest"]["run_day"] == "2026-10-06"
    # Wednesday is not due at all; next Monday 08:59 is not due, 09:00 is.
    for when, expected in ((et(2026, 10, 7, 12), ms.NOT_DUE), (et(2026, 10, 12, 8, 59), ms.NOT_DUE),
                           (et(2026, 10, 12, 9), ms.OK)):
        jobs.now = when
        assert await ms.run_day_job("digest", _work([], calls), due=due, stale_seconds=900, now=when,
                                    owed_since=monday) == expected


# ── measure ─────────────────────────────────────────────────────────────────────────────────────


def bview(uri: str, **counts: Any) -> Dict[str, Any]:
    """An AppView post view as `bluesky.get_posts` returns it (POST_VIEW_KEYS only)."""
    names = {"likes": "likeCount", "reposts": "repostCount", "replies": "replyCount", "quotes": "quoteCount",
             "bookmarks": "bookmarkCount"}
    view: Dict[str, Any] = {"uri": uri, "cid": "bafyreimeasure", "indexedAt": "2026-10-01T00:00:00.000Z"}
    view.update({names[k]: v for k, v in counts.items()})
    return view


class FakeBlueskyReads:
    """`bluesky.get_posts` / `get_profile`, scripted. `views` answers every URI it holds (a URI it does
    not hold is absent — a deleted post); `errors` raise in order (None = answer normally); a call that
    includes a URI in `refuse` raises a 400."""

    def __init__(self) -> None:
        self.views: Dict[str, Dict[str, Any]] = {}
        self.calls: List[List[str]] = []
        self.errors: List[Any] = []
        self.refuse: set = set()
        self.answer_override: Any = _AUTO
        self.on_call: Optional[Callable[[], None]] = None
        self.profile: Any = {"did": DID, "handle": "caydex.bsky.social", "followersCount": 120,
                             "followsCount": 5, "postsCount": 9}
        self.profile_calls: List[str] = []

    async def get_posts(self, uris, *, host: str = bluesky.APPVIEW_URL):
        assert host == bluesky.APPVIEW_URL
        uris = list(uris)
        self.calls.append(uris)
        if self.on_call is not None:
            self.on_call()
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        if self.refuse & set(uris):
            raise bluesky.BlueskyRefusedError("bluesky app.bsky.feed.getPosts: HTTP 400 InvalidRequest",
                                              method="app.bsky.feed.getPosts", status=400)
        if self.answer_override is not _AUTO:
            return self.answer_override
        return [copy.deepcopy(self.views[u]) for u in uris if u in self.views]

    async def get_profile(self, actor, *, host: str = bluesky.APPVIEW_URL):
        assert host == bluesky.APPVIEW_URL
        self.profile_calls.append(actor)
        if isinstance(self.profile, BaseException):
            raise self.profile
        return copy.deepcopy(self.profile)


def xpost(ext: str, **public_metrics: Any) -> Dict[str, Any]:
    return {"id": ext, "created_at": None, "public_metrics": public_metrics}


class FakeXReads:
    """`x_api.list_user_posts_metrics` / `get_me`, scripted: the window returns every `timeline` post
    whose snowflake time is inside it, plus `extra` unrelated posts. `errors` raise in order. `problem`
    (when set) answers the client's errors-only-200 shape instead: no posts, a `problem` line."""

    def __init__(self) -> None:
        self.timeline: Dict[str, Dict[str, Any]] = {}
        self.reads: List[Dict[str, Any]] = []
        self.errors: List[Any] = []
        self.extra = 0
        self.next_token: Optional[str] = None
        self.problem: Any = None
        self.on_read: Optional[Callable[[], None]] = None
        self.me: Any = {"id": X_USER, "username": "caydex",
                        "public_metrics": {"followers_count": 42, "following_count": 7, "tweet_count": 19}}
        self.me_calls = 0

    async def list_user_posts_metrics(self, user_id, *, start_time, end_time, max_results=5):
        self.reads.append({"user_id": user_id, "start": start_time, "end": end_time, "max_results": max_results})
        if self.on_read is not None:
            self.on_read()
        if self.errors:
            err = self.errors.pop(0)
            if err is not None:
                raise err
        if self.problem is not None:
            return {"posts": [], "result_count": 0, "next_token": None, "problem": self.problem}
        inside = [copy.deepcopy(p) for p in self.timeline.values()
                  if start_time <= ms.x_post_time(p["id"]) <= end_time]
        inside += [xpost(str(9_000 + i), like_count=1) for i in range(self.extra)]
        return {"posts": inside, "result_count": len(inside), "next_token": self.next_token}

    async def get_me(self):
        self.me_calls += 1
        if isinstance(self.me, BaseException):
            raise self.me
        return copy.deepcopy(self.me)


class FakeUploadPostReads:
    """`upload_post.get_post_analytics`, scripted per job id (`default` otherwise): an answer dict, or an
    exception to raise; nothing scripted is a 404."""

    def __init__(self) -> None:
        self.answers: Dict[str, Any] = {}
        self.default: Any = None
        self.calls: List[tuple] = []

    async def get_post_analytics(self, request_id, *, platform=None):
        self.calls.append((request_id, platform))
        answer = self.answers.get(request_id, self.default)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise upload_post.UploadPostRefusedError(
                "upload-post get_post_analytics: HTTP 404: No post found with the given request ID",
                method="get_post_analytics", status=404)
        return copy.deepcopy(answer)


def up_answer(platform: str, *, metrics: Any = _AUTO, error: Optional[str] = None,
              followers: Optional[int] = None, followers_date: Any = None) -> Dict[str, Any]:
    """`upload_post.get_post_analytics`'s answer for one platform, every key the client returns:
    `followers_date` is the snapshot's own "YYYY-MM-DD", or None (absent / unreadable)."""
    entry = {"post_metrics": {"views": 5200, "likes": 120, "comments": 8, "favorites": 3}
             if metrics is _AUTO else metrics, "post_metrics_error": error, "followers": followers,
             "followers_date": followers_date}
    return {"platforms": {platform: entry}}


class Env:
    def __init__(self, ledger: Ledger, jobs: FakeJobs) -> None:
        self.ledger, self.jobs = ledger, jobs
        self.bsky, self.x, self.up = FakeBlueskyReads(), FakeXReads(), FakeUploadPostReads()
        self.budget = BUDGET
        self.up_configured = False

    async def measure(self, now: datetime) -> Dict[str, int]:
        self.jobs.now = now
        return await ms.measure_cycle(now=now)

    def x_account_holder(self, now: datetime) -> str:
        """An older X post whose checkpoints are all measured and which holds a FRESH account snapshot
        (taken earlier the same ET day) — so the account read is not due and only post reads happen."""
        return self.ledger.post("x", published_at=now - timedelta(days=20), metrics={
            "v": 1, "rev": 1, "cps": [1, 3, 7],
            "account": {"followers": 40, "at": (now - timedelta(minutes=30)).isoformat(), "source": "x_api"}})


@pytest.fixture
def env(monkeypatch, caplog, ledger, jobs):
    e = Env(ledger, jobs)
    caplog.set_level(logging.INFO, logger=MS_LOGGER)
    monkeypatch.setattr(ms.settings, "MARKETING_ENABLED", True)
    monkeypatch.setattr(ms.settings, "MARKETING_METRICS_ENABLED", True)
    monkeypatch.setattr(ms.settings, "MARKETING_DRY_RUN", False)
    # The headroom is priced by this switch (`outlet_x.metrics_headroom_micros`): pinned, never the .env's.
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", False)
    monkeypatch.setattr(x_api, "configured", lambda: True)
    monkeypatch.setattr(x_api, "user_id_from_access_token", lambda: X_USER)
    monkeypatch.setattr(outlet_x, "budget_micros", lambda: e.budget)
    monkeypatch.setattr(upload_post, "configured", lambda: e.up_configured)
    monkeypatch.setattr(bluesky, "get_posts", e.bsky.get_posts)
    monkeypatch.setattr(bluesky, "get_profile", e.bsky.get_profile)
    monkeypatch.setattr(x_api, "list_user_posts_metrics", e.x.list_user_posts_metrics)
    monkeypatch.setattr(x_api, "get_me", e.x.get_me)
    monkeypatch.setattr(upload_post, "get_post_analytics", e.up.get_post_analytics)
    return e


def _no_platform_call(env: Env) -> bool:
    return (env.bsky.calls == [] and env.bsky.profile_calls == [] and env.x.reads == [] and env.x.me_calls == 0
            and env.up.calls == [])


# pure helpers ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (None, None), (-1, None), ("12", 12), ("1e3", None), (12.5, None), (12.0, 12), (math.nan, None),
    (math.inf, None), (-math.inf, None), (True, None), (False, None), (10**16, None), ("", None),
    (" 12", None), ("12 ", None), ("١٢", None), ("12\n", None), ("+12", None), ("-12", None), ("0x10", None),
    ("007", 7), ("0", 0), (0, 0), (-0.0, 0), (ms.COUNT_MAX, ms.COUNT_MAX), (ms.COUNT_MAX + 1, None),
    ("9" * 15, int("9" * 15)), ("9" * 16, None), (1e15, None), (1e20, None), ([12], None), ({"n": 12}, None),
    (b"12", None),
])
def test_normalize_count_keeps_only_honest_non_negative_integers(raw, expected):
    got = ms.normalize_count(raw)
    assert got == expected
    if got is not None:
        assert type(got) is int and got >= 0


def test_a_snapshot_omits_every_unreadable_count_and_never_writes_zero():
    outliers = {"likes": None, "reposts": -1, "replies": "1e3", "quotes": 12.5, "bookmarks": math.nan,
                "impressions": math.inf, "views": True, "shares": 10**16, "saves": " 12", "reach": "١٢"}
    snap = ms.build_snapshot(outliers, now=THU, published_at=THU - timedelta(hours=30))
    assert snap == {"day": "2026-10-08", "at": THU.isoformat(), "age_h": 30.0, "cp": None}
    assert not ms.snapshot_has_counts(snap)
    honest = ms.build_snapshot({"likes": "12", "reposts": 12.0, "replies": 0, "junk_key": 5}, now=THU,
                               published_at=None, cp=7)
    assert honest == {"day": "2026-10-08", "at": THU.isoformat(), "age_h": None, "cp": 7, "likes": 12,
                      "reposts": 12, "replies": 0}
    assert ms.build_snapshot({}, now=THU, published_at=THU, cp=2)["cp"] is None        # not a checkpoint
    assert ms.build_snapshot({}, now=THU, published_at=THU, cp=True)["cp"] is None
    assert ms.build_snapshot({}, now=THU, published_at=THU + timedelta(hours=1))["age_h"] is None   # in the future
    assert ms.build_snapshot("not a mapping", now=THU, published_at=THU)["age_h"] == 0.0


def test_extract_counts_reads_either_x_spelling_and_skips_a_junk_one():
    assert ms.extract_counts({"retweet_count": 3, "repost_count": 4}, ms.X_FIELDS) == {"reposts": 3}
    assert ms.extract_counts({"retweet_count": "lots", "repost_count": 4}, ms.X_FIELDS) == {"reposts": 4}
    assert ms.extract_counts({"tweet_count": None, "post_count": "19"}, ms.X_ACCOUNT_FIELDS) == {"posts": 19}
    assert ms.extract_counts({"comments": 8, "favorites": 3, "reach": -1}, ms.UPLOAD_POST_FIELDS) == {
        "replies": 8, "bookmarks": 3}
    assert ms.extract_counts(None, ms.X_FIELDS) == {} and ms.extract_counts(["like_count"], ms.X_FIELDS) == {}


def _entry(day: str, **counts: Any) -> Dict[str, Any]:
    return {"day": day, "at": f"{day}T10:00:00+00:00", "age_h": 1.0, "cp": None, **counts}


def test_apply_measurement_keeps_history_bounded_and_one_entry_per_day():
    days = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    old = {"v": 1, "rev": 9, "history": [_entry(d, likes=i) for i, d in enumerate(days)] + ["junk", 3]}
    snap = ms.build_snapshot({"likes": 99}, now=THU, published_at=THU - timedelta(days=2))
    doc = ms.apply_measurement(old, platform="bluesky", today=THU.date(), status="ok", snapshot=snap)
    assert len(doc["history"]) == ms.HISTORY_MAX == 30
    assert doc["history"][0]["day"] == days[1] and doc["history"][-1] == doc["last"] == snap
    assert doc["rev"] == 9                  # the writer's to bump, untouched here
    assert old["history"][0]["day"] == days[0]   # pure: the input is not mutated
    # A rerun the same day REPLACES that day's entry.
    again = ms.apply_measurement(doc, platform="bluesky", today=THU.date(), status="ok",
                                 snapshot={**snap, "likes": 100})
    assert len(again["history"]) == 30 and again["history"][-1]["likes"] == 100
    assert [h["day"] for h in again["history"]].count("2026-10-08") == 1
    # X keeps four.
    xdoc = {"v": 1, "history": [_entry(d) | {"likes": 1} for d in days[:4]], "cps": [1, 3, 7]}
    xsnap = ms.build_snapshot({"likes": 5}, now=THU, published_at=THU - timedelta(days=28), cp=28)
    out = ms.apply_measurement(xdoc, platform="x", today=THU.date(), status="ok", snapshot=xsnap, cps=[1, 3, 7, 28])
    assert len(out["history"]) == ms.X_HISTORY_MAX == 4 and out["history"][-1]["cp"] == 28
    assert out["cps"] == [1, 3, 7, 28]


def test_apply_measurement_without_counts_is_an_error_that_keeps_the_last_good_snapshot():
    last = _entry("2026-10-07", likes=4)
    old = {"v": 1, "rev": 2, "status": "ok", "last": last, "history": [last]}
    empty = ms.build_snapshot({"likes": -3}, now=THU, published_at=THU - timedelta(days=1))
    doc = ms.apply_measurement(old, platform="bluesky", today=THU.date(), status="ok", snapshot=empty)
    assert doc["status"] == "error" and doc["note"] == "no_metrics"
    assert doc["last"] == last and doc["history"] == [last] and doc["measured_day"] == "2026-10-08"
    missing = ms.apply_measurement(old, platform="bluesky", today=THU.date(), status="missing", note="gone")
    assert missing["status"] == "missing" and missing["last"] == last and missing["history"] == [last]


def test_apply_measurement_fields_and_refusals():
    until = THU.date() + timedelta(days=7)
    doc = ms.apply_measurement("not a dict", platform="tiktok", today=THU.date(), status="unavailable",
                               note="plan\nrefused\x00 here", unavailable_until=until)
    assert doc == {"v": 1, "measured_day": "2026-10-08", "status": "unavailable", "note": "plan refused here",
                   "unavailable_until": "2026-10-15"}
    # `unavailable_until` only rides on `unavailable`; a datetime is not a date.
    assert ms.apply_measurement({}, platform="x", today=THU.date(), status="capped",
                                unavailable_until=until)["unavailable_until"] is None
    assert ms.apply_measurement({}, platform="tiktok", today=THU.date(), status="unavailable",
                                unavailable_until=THU)["unavailable_until"] is None
    # cps: only real checkpoints, merged, sorted, X only.
    x = ms.apply_measurement({"cps": [7, "3", True, 7.0, 2, None, 1]}, platform="x", today=THU.date(),
                             status="missing", cps=[3, 28, 99, False])
    assert x["cps"] == [1, 3, 7, 28]
    assert "cps" not in ms.apply_measurement({}, platform="bluesky", today=THU.date(), status="ok", cps=[1])
    assert len(ms.apply_measurement({}, platform="x", today=THU.date(), status="error",
                                    note="n" * 5000)["note"]) == ms.NOTE_MAX
    with pytest.raises(ValueError):
        ms.apply_measurement({}, platform="x", today=THU.date(), status="great")


def test_a_note_never_carries_a_secret_or_a_line_break():
    note = ms.clean_note("x read: HTTP 401 - Bearer abcdefghijklmnop0123456789 token=supersecretvalue\r\nnext")
    assert "supersecretvalue" not in note and "abcdefghijklmnop0123456789" not in note
    assert "\n" not in note and "\r" not in note
    assert ms.clean_note(None) is None and ms.clean_note("   ") is None
    # Unicode line / paragraph separators and NEL break a Telegram or log line too (built with chr() so
    # no editor or tool can turn them into a literal line break in this file).
    seps = [chr(0x2028), chr(0x2029), chr(0x85), chr(0), chr(0x7F), "\t"]
    assert ms.clean_note("a" + "".join(seps) + "b" + seps[0] + "  c") == "a b c"
    # The pattern itself is plain ASCII in the source: a literal separator there is a trap.
    import inspect
    source = inspect.getsource(ms)
    assert not any(ch in source for ch in (chr(0x2028), chr(0x2029), chr(0x85)))


@pytest.mark.parametrize("age,cps,expected", [
    (timedelta(hours=23, minutes=59, seconds=59), [], None),     # none crossed
    (timedelta(days=1), [], 1),                                   # exactly at 1 day
    (timedelta(days=2, hours=23), [1], None),                     # 1 measured, 3 not crossed yet
    (timedelta(days=3), [1], 3),
    (timedelta(days=8), [], 7),                                   # several crossed: ONE read, for 7
    (timedelta(days=8), [1, 3, 7], None),
    (timedelta(days=8), ["7", True, 7.0, None], 7),               # junk is not "measured"
    (timedelta(days=29), [1, 3, 7], 28),
    (timedelta(days=29), [1, 3, 7, 28], None),
    (timedelta(days=-1), [], None),                               # a published_at in the future
])
def test_x_due_checkpoint(age, cps, expected):
    assert ms.x_due_checkpoint(THU - age, THU, cps) == expected


def test_x_due_checkpoint_without_a_publish_time_is_never_due():
    assert ms.x_due_checkpoint(None, THU, []) is None and ms.crossed_checkpoints(None, THU) == []


def test_the_x_read_window_is_centred_on_xs_own_creation_time_when_it_agrees():
    made = THU - timedelta(days=3, minutes=7)
    ext = snowflake(made, 5)
    assert abs(ms.x_post_time(ext) - made) < timedelta(milliseconds=2)
    # A post reconcile found late (published_at an hour after X created it): the snowflake wins.
    assert abs(ms.x_read_center(ext, made + timedelta(hours=1)) - made) < timedelta(milliseconds=2)
    # A hand-edited id that is not a snowflake of anything near: published_at.
    assert ms.x_read_center("12345", made) == made
    assert ms.x_read_center(snowflake(made - timedelta(days=5)), made) == made
    for junk in (None, "", "12a", "9" * 26, 12345, "1" * 25):
        assert ms.x_post_time(junk) is None or junk == "1" * 25
    assert ms.x_read_center(ext, None) is None


# the step: switches, schedule, the day ───────────────────────────────────────


def test_both_switches_default_off():
    from app.config import Settings
    assert Settings.model_fields["MARKETING_METRICS_ENABLED"].default is False
    assert Settings.model_fields["MARKETING_DIGEST_ENABLED"].default is False


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,metrics", [(False, True), (True, False), (False, False)])
async def test_the_step_is_fail_closed(env, monkeypatch, enabled, metrics):
    monkeypatch.setattr(ms.settings, "MARKETING_ENABLED", enabled)
    monkeypatch.setattr(ms.settings, "MARKETING_METRICS_ENABLED", metrics)
    env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    counters = await env.measure(THU)
    assert not any(counters.values())
    assert env.jobs.reads == [] and env.jobs.claims == [] and _no_platform_call(env)


@pytest.mark.asyncio
async def test_an_empty_ledger_settles_the_day_without_a_platform_call(env):
    counters = await env.measure(THU)
    assert counters["ran"] == 1 and counters["posts"] == 0 and counters["incomplete"] == 0
    assert env.jobs.finishes == [{"job": ms.JOB_METRICS, "success": True, "items": 0, "error": None}]
    assert _no_platform_call(env)
    # Later ticks the same day: nothing at all — not even a state read.
    again = await env.measure(THU + timedelta(hours=3))
    assert not any(again.values()) and env.jobs.reads == [ms.JOB_METRICS]
    assert ms.last_measure()["outcome"] == ms.OK and ms.last_measure()["day"] == "2026-10-08"


@pytest.mark.asyncio
async def test_before_six_et_nothing_is_claimed_and_the_dst_week_is_read_on_et_hours(env):
    # Monday 2026-11-02 — the first weekday after DST ended (Sunday 2026-11-01): 06:00 EST is 11:00 UTC.
    monday = et(2026, 11, 2, 6)
    assert monday == datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc)
    published = et(2026, 10, 31, 16, 15)            # Saturday 16:15 EDT = 20:15 UTC
    pid = env.ledger.post("x", published_at=published)
    env.x.timeline = {env.ledger.raw(pid)["external_id"]: xpost(env.ledger.raw(pid)["external_id"], like_count=3)}
    counters = await env.measure(monday - timedelta(minutes=1))          # 05:59 EST = 10:59 UTC
    assert not any(counters.values()) and env.jobs.reads == [] and _no_platform_call(env)

    counters = await env.measure(monday)
    assert counters["ran"] == 1 and len(env.x.reads) == 1 and env.x.me_calls == 1      # Monday: get_me too
    m = env.ledger.metrics(pid)
    # The age is ABSOLUTE time (38 h 45 min across the DST change, not the 37 h 45 min of ET wall clocks).
    assert m["measured_day"] == "2026-11-02" and m["last"]["age_h"] == 38.8 and m["last"]["cp"] == 1
    assert m["cps"] == [1]
    assert m["account"] == {"followers": 42, "following": 7, "posts": 19, "at": monday.isoformat(),
                            "source": "x_api"}


@pytest.mark.asyncio
async def test_the_second_run_of_a_day_reads_nothing_already_measured(env, monkeypatch):
    env.up_configured = True
    b = env.ledger.post("bluesky", published_at=THU - timedelta(days=2))
    x = env.ledger.post("x", published_at=THU - timedelta(days=3, hours=1))
    up = env.ledger.post("tiktok", published_at=THU - timedelta(days=4))
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=2)}
    ext = env.ledger.raw(x)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=5)}
    env.up.default = up_answer("tiktok", followers=900)
    first = await env.measure(THU)
    assert first["measured"] == 3 and env.jobs.finishes[-1]["success"] is True
    calls = (copy.deepcopy(env.bsky.calls), len(env.bsky.profile_calls), len(env.x.reads), env.x.me_calls,
             len(env.up.calls))
    journal = env.ledger.journal(x)
    revs = {pid: env.ledger.metrics(pid)["rev"] for pid in (b, x, up)}

    # Another process (a deploy overlap, a lost finish): its own memo is empty and the ledger did not
    # record the day — it claims and runs, and must read nothing.
    monkeypatch.setattr(ms, "_DAY_JOBS", {})
    env.jobs.rows[ms.JOB_METRICS]["run_day"] = None
    second = await env.measure(THU + timedelta(hours=2))
    assert second["ran"] == 1 and second["written"] == 0
    assert (env.bsky.calls, len(env.bsky.profile_calls), len(env.x.reads), env.x.me_calls,
            len(env.up.calls)) == calls
    assert env.ledger.journal(x) == journal
    assert {pid: env.ledger.metrics(pid)["rev"] for pid in (b, x, up)} == revs


@pytest.mark.asyncio
async def test_only_published_posts_younger_than_30_days_without_a_retract_request_are_read(env):
    fresh = env.ledger.post("bluesky", published_at=THU - timedelta(days=29, hours=23))
    old = env.ledger.post("bluesky", published_at=THU - timedelta(days=30, minutes=1))
    retracting = env.ledger.post("bluesky", published_at=THU - timedelta(days=1), meta={
        "dry_run": False, "retract_requested_at": "2026-10-08T10:00:00+00:00"})
    queued = env.ledger.post("bluesky", published_at=THU - timedelta(days=1), status="queued")
    retracted = env.ledger.post("bluesky", published_at=THU - timedelta(days=1), status="retracted")
    unknown = env.ledger.post("pinterest", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(i): bview(bsky_uri(i), likes=1) for i in range(1, 7)}
    counters = await env.measure(THU)
    assert env.bsky.calls == [[bsky_uri(1)]] and counters["posts"] == 1       # the retract request is not counted
    assert env.ledger.metrics(fresh)["status"] == "ok"
    for pid in (old, retracting, queued, retracted, unknown):
        assert env.ledger.metrics(pid) == {}


# Bluesky ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bluesky_counts_are_merged_through_the_one_writer_and_the_fence_is_untouched(env):
    old = env.ledger.post("bluesky", published_at=THU - timedelta(days=3))
    new = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=5, reposts=2, replies=1, quotes=0, bookmarks=3),
                      bsky_uri(2): bview(bsky_uri(2), likes=9)}
    before = {pid: env.ledger.row(pid) for pid in (old, new)}
    counters = await env.measure(THU)
    assert env.bsky.calls == [[bsky_uri(1), bsky_uri(2)]]            # one call, oldest first
    assert env.bsky.profile_calls == [DID]
    m = env.ledger.metrics(old)
    assert m == {"v": 1, "rev": 1, "measured_day": "2026-10-08", "status": "ok", "note": None,
                 "unavailable_until": None,
                 "last": {"day": "2026-10-08", "at": THU.isoformat(), "age_h": 72.0, "cp": None, "likes": 5,
                          "reposts": 2, "replies": 1, "quotes": 0, "bookmarks": 3},
                 "history": [m["last"]]}
    n = env.ledger.metrics(new)
    assert n["last"]["likes"] == 9 and "reposts" not in n["last"]
    # The account goes on the NEWEST post only.
    assert n["account"] == {"followers": 120, "following": 5, "posts": 9, "at": THU.isoformat(),
                            "source": "bluesky_appview"}
    assert "account" not in m and n["rev"] == 2
    for pid in (old, new):
        row = env.ledger.row(pid)
        assert row["updated_at"] == before[pid]["updated_at"] and row["metadata"] == before[pid]["metadata"]
        assert row["cost_micros"] == 0 and row["status"] == "published"
    assert counters["measured"] == 2 and counters["accounts"] == 1 and counters["written"] == 3
    # The job's items are POSTS (the digest says "N posts written"): the newest post's counts and its
    # account snapshot are two writes but one post.
    assert env.jobs.finishes[-1] == {"job": ms.JOB_METRICS, "success": True, "items": 2, "error": None}


@pytest.mark.asyncio
async def test_bluesky_outlier_counts_are_omitted_never_zero(env):
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes="12", reposts=-1, replies=True, quotes=1.5,
                                         bookmarks="١٢")}
    env.bsky.profile = {"did": DID, "handle": "caydex.bsky.social", "followersCount": "lots",
                        "followsCount": -2, "postsCount": None}
    await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert {k: v for k, v in m["last"].items() if k in ms.COUNT_KEYS} == {"likes": 12}
    assert "account" not in m          # a profile with no readable count stores nothing


@pytest.mark.asyncio
async def test_a_view_without_any_count_is_an_error_and_adds_no_history(env):
    last = _entry("2026-10-07", likes=4)
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=2),
                          metrics={"v": 1, "rev": 1, "status": "ok", "last": last, "history": [last]})
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1))}
    counters = await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert m["status"] == "error" and m["note"] == "no_metrics" and m["history"] == [last] and m["last"] == last
    assert counters["errors"] == 1 and counters["measured"] == 0


@pytest.mark.asyncio
async def test_a_post_the_appview_omits_is_missing_and_keeps_its_last_counts(env):
    last = _entry("2026-10-07", likes=4)
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=2),
                          metrics={"v": 1, "rev": 3, "status": "ok", "last": last, "history": [last]})
    env.bsky.views = {}
    counters = await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert m["status"] == "missing" and m["last"] == last and m["history"] == [last]
    assert "deleted" in m["note"] and counters["missing"] == 1
    # Two writes: the measurement, then the account (it is the newest Bluesky post).
    assert m["rev"] == 5 and m["account"]["followers"] == 120


@pytest.mark.asyncio
async def test_a_batch_400_is_retried_one_uri_at_a_time(env):
    pids = [env.ledger.post("bluesky", published_at=THU - timedelta(days=3 - i)) for i in range(3)]
    uris = [bsky_uri(i) for i in (1, 2, 3)]
    env.bsky.views = {u: bview(u, likes=7) for u in uris}
    env.bsky.refuse = {uris[1]}
    counters = await env.measure(THU)
    assert env.bsky.calls == [uris, [uris[0]], [uris[1]], [uris[2]]]
    assert env.ledger.metrics(pids[0])["status"] == "ok" and env.ledger.metrics(pids[2])["status"] == "ok"
    bad = env.ledger.metrics(pids[1])
    assert bad["status"] == "error" and "refused" in bad["note"] and "last" not in bad
    assert counters["errors"] == 1 and env.jobs.finishes[-1]["success"] is True     # a definite answer


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    bluesky.BlueskyRateLimitError("bluesky getPosts: HTTP 429", method="app.bsky.feed.getPosts", status=429),
    bluesky.BlueskyAmbiguousError("bluesky getPosts: HTTP 502", method="app.bsky.feed.getPosts", status=502),
    bluesky.BlueskyNotSentError("bluesky getPosts: not sent (ConnectError)", method="app.bsky.feed.getPosts"),
    bluesky.BlueskyAmbiguousError("bluesky getPosts: outcome unknown (ReadTimeout)", method="x"),
])
async def test_a_transient_bluesky_failure_leaves_the_day_open(env, error):
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=2)}
    env.bsky.errors = [error]
    counters = await env.measure(THU)
    assert counters["incomplete"] == 1 and env.ledger.metrics(pid) == {}
    assert env.jobs.finishes[-1]["success"] is False and "bluesky" in env.jobs.finishes[-1]["error"]
    assert env.bsky.profile_calls == []
    # The next tick finishes the day.
    counters = await env.measure(THU + timedelta(minutes=10))
    assert env.ledger.metrics(pid)["status"] == "ok" and env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_a_definite_appview_refusal_stops_bluesky_for_the_day(env, caplog):
    env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    x = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.x_account_holder(THU)
    env.x.errors = [x_api.XApiAmbiguousError("x: HTTP 503", method="list_user_posts_metrics", status=503)]
    env.bsky.errors = [bluesky.BlueskyRefusedError("bluesky getPosts: HTTP 403", method="m", status=403)]
    await env.measure(THU)
    assert len(env.bsky.calls) == 1 and _messages(caplog, "bluesky reads STOPPED", logging.ERROR)
    assert env.jobs.finishes[-1]["success"] is False          # X's 503 holds the day open
    ext = env.ledger.raw(x)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}
    await env.measure(THU + timedelta(minutes=10))
    assert len(env.bsky.calls) == 1                           # Bluesky stays stopped today
    assert env.ledger.metrics(x)["status"] == "ok" and env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_more_than_25_bluesky_posts_are_read_in_batches_of_25(env):
    pids = [env.ledger.post("bluesky", published_at=THU - timedelta(hours=40 + i)) for i in range(26)]
    env.bsky.views = {bsky_uri(i): bview(bsky_uri(i), likes=i) for i in range(1, 27)}
    await env.measure(THU)
    assert [len(c) for c in env.bsky.calls] == [25, 1]
    assert all(env.ledger.metrics(p)["status"] == "ok" for p in pids)


@pytest.mark.asyncio
async def test_the_bluesky_uri_falls_back_to_what_the_publish_recorded(env):
    stored = env.ledger.post("bluesky", published_at=THU - timedelta(days=2), external_id="https://bsky.app/x",
                             meta={"dry_run": False, "publish": {"bluesky": {"uri": bsky_uri(901)}}})
    rebuilt = env.ledger.post("bluesky", published_at=THU - timedelta(days=2), external_id=None,
                              meta={"dry_run": False, "publish": {"bluesky": {"repo": DID, "rkey": "3lbmeasure0902"}}})
    none = env.ledger.post("bluesky", published_at=THU - timedelta(days=2), external_id="at://caydex.bsky.social/"
                           "app.bsky.feed.post/3lbmeasure0903",       # handle-based: not a post URI
                           meta={"dry_run": False, "publish": {"bluesky": {"repo": "caydex.bsky.social",
                                                                          "rkey": "3lbmeasure0903"}}})
    env.bsky.views = {u: bview(u, likes=1) for u in (bsky_uri(901), bsky_uri(902))}
    counters = await env.measure(THU)
    assert env.bsky.calls == [[bsky_uri(901), bsky_uri(902)]]
    assert env.ledger.metrics(stored)["status"] == "ok" and env.ledger.metrics(rebuilt)["status"] == "ok"
    assert env.ledger.metrics(none)["status"] == "no_external_id" and counters["no_external_id"] == 1
    # The next day the unchanged `no_external_id` is not written again (its measured day stays).
    await env.measure(THU + timedelta(days=1))
    assert env.ledger.metrics(none)["measured_day"] == "2026-10-08"
    assert env.ledger.metrics(stored)["measured_day"] == "2026-10-09"


@pytest.mark.asyncio
async def test_an_unreadable_get_posts_answer_holds_the_day_and_never_reads_as_deleted(env, caplog):
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.answer_override = {"posts": []}            # a dict, not the list the client returns
    counters = await env.measure(THU)
    assert env.ledger.metrics(pid) == {} and counters["incomplete"] == 1
    assert _messages(caplog, "not a list", logging.ERROR)


@pytest.mark.asyncio
async def test_a_post_younger_than_an_hour_waits_for_tomorrow(env):
    env.up_configured = True
    fresh_b = env.ledger.post("bluesky", published_at=THU - timedelta(minutes=59))
    fresh_up = env.ledger.post("tiktok", published_at=THU - timedelta(minutes=10))
    ripe_b = env.ledger.post("bluesky", published_at=THU - timedelta(hours=1))      # exactly an hour: read
    env.bsky.views = {bsky_uri(i): bview(bsky_uri(i), likes=1) for i in (1, 3)}
    env.up.default = up_answer("tiktok")
    await env.measure(THU)
    assert env.bsky.calls == [[bsky_uri(3)]] and env.up.calls == []
    assert "measured_day" not in env.ledger.metrics(fresh_b) and env.ledger.metrics(fresh_up) == {}
    assert env.ledger.metrics(ripe_b)["status"] == "ok"
    # The account still goes on the newest post (an account read is not about the post).
    assert env.ledger.metrics(fresh_b)["account"]["followers"] == 120
    await env.measure(THU + timedelta(days=1))
    assert env.ledger.metrics(fresh_b)["status"] == "ok" and env.ledger.metrics(fresh_up)["status"] == "ok"


@pytest.mark.asyncio
async def test_bluesky_followers_failure_never_holds_the_day(env, caplog):
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=1)}
    env.bsky.profile = bluesky.BlueskyAmbiguousError("bluesky getProfile: HTTP 502", method="m", status=502)
    await env.measure(THU)
    assert "account" not in env.ledger.metrics(pid) and env.jobs.finishes[-1]["success"] is True
    assert _messages(caplog, "followers not read", logging.WARNING)


# X ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_x_reads_once_per_checkpoint_for_the_largest_crossed(env):
    env.x_account_holder(THU)
    young = env.ledger.post("x", published_at=THU - timedelta(hours=23, minutes=59))
    one = env.ledger.post("x", published_at=THU - timedelta(days=1))
    eight = env.ledger.post("x", published_at=THU - timedelta(days=8))
    done = env.ledger.post("x", published_at=THU - timedelta(days=8), metrics={"v": 1, "rev": 5, "cps": [1, 3, 7]})
    for pid in (young, one, eight, done):
        ext = env.ledger.raw(pid)["external_id"]
        env.x.timeline[ext] = xpost(ext, like_count=4, retweet_count=1, impression_count=300)
    counters = await env.measure(THU)
    assert len(env.x.reads) == 2 and counters["x_reads"] == 2 and env.x.me_calls == 0
    m1, m8 = env.ledger.metrics(one), env.ledger.metrics(eight)
    assert m1["cps"] == [1] and m1["last"]["cp"] == 1 and m1["last"]["age_h"] == 24.0
    assert m8["cps"] == [1, 3, 7] and m8["last"]["cp"] == 7 and len(m8["history"]) == 1
    assert m8["last"]["likes"] == 4 and m8["last"]["reposts"] == 1 and m8["last"]["impressions"] == 300
    assert env.ledger.metrics(young) == {} and env.ledger.journal(young) == []
    assert env.ledger.metrics(done)["rev"] == 5 and env.ledger.journal(done) == []


@pytest.mark.asyncio
async def test_an_x_read_is_charged_before_the_call_and_corrected_after(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=2))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=4)}
    env.x.extra = 2                       # two unrelated posts in the window: X bills three
    seen: List[List[tuple]] = []
    env.x.on_read = lambda: seen.append(env.ledger.journal(pid))
    await env.measure(THU)
    assert seen == [[("x_metrics_read", RESERVE)]]          # the write-ahead landed BEFORE the call
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE), ("x_metrics_read_correction", 3_000 - RESERVE)]
    charges = env.ledger.row(pid)["metadata"]["charges"]
    assert charges[1]["at"] == charges[0]["at"]             # dated at the reserve (same month)
    assert env.ledger.row(pid)["cost_micros"] == 3_000
    read = env.x.reads[0]
    published = mrs._parse_ts(env.ledger.raw(pid)["published_at"])
    assert read["user_id"] == X_USER and read["max_results"] == 5
    assert abs(read["start"] - (published - timedelta(minutes=10))) < timedelta(milliseconds=2)
    assert abs(read["end"] - (published + timedelta(minutes=10))) < timedelta(milliseconds=2)


@pytest.mark.asyncio
async def test_a_deleted_x_post_is_missing_its_checkpoint_counts_and_the_charge_is_corrected_to_zero(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=3, hours=2))
    counters = await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert m["status"] == "missing" and m["cps"] == [1, 3] and "last" not in m
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE), ("x_metrics_read_correction", -RESERVE)]
    assert env.ledger.row(pid)["cost_micros"] == 0 and counters["missing"] == 1
    # Not paid for again tomorrow — the checkpoint counts as measured.
    await env.measure(THU + timedelta(days=1))
    assert len(env.x.reads) == 1


@pytest.mark.asyncio
async def test_an_x_window_with_more_pages_is_an_error_not_a_deletion(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.x.extra, env.x.next_token = 5, "7140dibdnow9c7btw423x9h8b8ay4b2v6xrg4n4fwakv5"
    await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert m["status"] == "error" and "more than one page" in m["note"] and m["cps"] == [1]


@pytest.mark.asyncio
async def test_x_outlier_public_metrics_are_omitted(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count="7", repost_count=3, retweet_count=None, impression_count=-1,
                                 reply_count=True, quote_count=1.5, bookmark_count=math.nan)}
    await env.measure(THU)
    last = env.ledger.metrics(pid)["last"]
    assert {k: v for k, v in last.items() if k in ms.COUNT_KEYS} == {"likes": 7, "reposts": 3}


def test_the_x_measure_prices_are_pinned_in_literal_micros(monkeypatch):
    """Review 2026-10-01 #12: every boundary below is written in these literals, so a mutated price (a
    headroom of 0, a $0.001 User read) fails here instead of moving every assertion with it."""
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", False)
    assert (outlet_x.USER_READ_MICROS, outlet_x.METRICS_READ_RESERVE_MICROS,
            outlet_x.metrics_headroom_micros()) == (10_000, 5_000, 60_000) == (USER_READ, RESERVE, HEADROOM)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", True)
    assert outlet_x.metrics_headroom_micros() == 800_000 == URL_HEADROOM
    assert outlet_x.METRICS_HEADROOM_POSTS == 4
    # The text-post figure is retired: the measure step prices the headroom at call time only.
    assert not hasattr(outlet_x, "METRICS_HEADROOM_MICROS")


#: (MARKETING_X_ALLOW_URLS, the spend at which a read is still allowed on the $2 cap, the note's dollars).
_HEADROOM_EDGES = [(False, 2_000_000 - 65_000, "$0.060"), (True, 2_000_000 - 805_000, "$0.800")]


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_urls,edge,dollars", _HEADROOM_EDGES)
async def test_x_reads_stop_at_the_cap_headroom_with_no_read_and_no_charge(env, caplog, monkeypatch, allow_urls,
                                                                          edge, dollars):
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.ledger.spend(edge + 1)                                 # one micro inside the headroom
    counters = await env.measure(THU)
    assert env.x.reads == [] and env.ledger.journal(pid) == []
    m = env.ledger.metrics(pid)
    assert m["status"] == "capped" and "cps" in m and m["cps"] == []
    assert m["note"] == f"the monthly X cap keeps 4 posts of headroom ({dollars}) — not read"
    assert counters["capped"] == 1 and env.jobs.finishes[-1]["success"] is True
    assert len(_messages(caplog, "X reads paused for 2026-10-08", logging.INFO)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_urls,edge,dollars", _HEADROOM_EDGES)
async def test_an_x_read_exactly_at_the_headroom_limit_is_allowed(env, monkeypatch, allow_urls, edge, dollars):
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}
    env.ledger.spend(edge)
    await env.measure(THU)
    assert len(env.x.reads) == 1 and env.ledger.metrics(pid)["status"] == "ok"


@pytest.mark.asyncio
async def test_with_urls_on_metric_reads_never_take_the_money_the_next_url_post_needs(env, monkeypatch):
    """Review 2026-10-01 #1: the headroom was priced as $0.015 text posts, so with MARKETING_X_ALLOW_URLS
    on (every X post then reserves $0.20) two reads at $1.799 spent left $0.199 — one micro short of
    the evening's URL post, which the cap then refused. Posting always wins."""
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", True)
    env.x_account_holder(THU)
    pids = [env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1 + i)) for i in range(2)]
    for pid in pids:
        ext = env.ledger.raw(pid)["external_id"]
        env.x.timeline[ext] = xpost(ext, like_count=1)
    env.ledger.spend(1_799_000)
    await env.measure(THU)
    assert env.x.reads == [] and all(env.ledger.journal(p) == [] for p in pids)
    assert all(env.ledger.metrics(p)["status"] == "capped" for p in pids)
    from app.services.marketing import publisher_service as pub
    budget = pub._budgets(env.ledger.svc)["x"]
    assert await budget.allows(outlet_x.URL_POST_MICROS) and budget.spent == 1_799_000
    # The switch is read at CALL time: the same spend with it off leaves room for the reads.
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", False)
    await env.measure(THU + timedelta(days=2))
    assert len(env.x.reads) == 2 and all(env.ledger.metrics(p)["status"] == "ok" for p in pids)


@pytest.mark.asyncio
async def test_a_zero_x_budget_records_capped_and_never_reads(env):
    env.budget = 0
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    await env.measure(THU)
    assert env.x.reads == [] and env.x.me_calls == 0 and env.ledger.journal(pid) == []
    assert env.ledger.metrics(pid)["status"] == "capped" and "X is off" in env.ledger.metrics(pid)["note"]


@pytest.mark.asyncio
async def test_an_unreadable_x_spend_reads_nothing_charges_nothing_and_leaves_the_day_open(env, monkeypatch):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))

    async def broken(*_a, **_k):
        raise mrs.MarketingRunError("spend_since failed (platform=x): RuntimeError: PostgREST 503")

    monkeypatch.setattr(env.ledger.svc, "spend_since", broken)
    counters = await env.measure(THU)
    assert env.x.reads == [] and env.ledger.journal(pid) == [] and env.ledger.metrics(pid) == {}
    assert counters["incomplete"] == 1 and env.jobs.finishes[-1]["success"] is False


def _x_errors() -> List[tuple]:
    """(exception, reserve refunded?, verdict): `tick` = X stops for this tick and the day stays open;
    `today` = X stops for the rest of the ET day; `backoff` = a definite refusal: X stops for the day, the
    post records `unavailable` and every X read waits a week; `post` = this post records an error, the
    next is read."""
    m = "list_user_posts_metrics"
    return [
        (x_api.XApiNotSentError("x: not sent (ConnectError)", method=m), True, "tick"),
        (x_api.XApiRateLimitError("x: HTTP 429", method=m, status=429), True, "tick"),
        (x_api.XApiCreditsDepletedError("x: HTTP 402 credits depleted", method=m, status=402), True, "today"),
        (x_api.XApiNotConfiguredError("x: not configured (MARKETING_X_ACCESS_TOKEN unset)", method=m), True,
         "today"),
        (x_api.XApiAmbiguousError("x: HTTP 503", method=m, status=503), False, "tick"),
        (x_api.XApiAmbiguousError("x: outcome unknown (ReadTimeout)", method=m), False, "tick"),
        (x_api.XApiAmbiguousError("x: HTTP 408", method=m, status=408), False, "tick"),
        (x_api.XApiDuplicateContentError("x: HTTP 403 duplicate content", method=m, status=403), False, "tick"),
        (x_api.XApiAuthError("x: HTTP 401 Unauthorized", method=m, status=401), False, "backoff"),
        (x_api.XApiForbiddenError("x: HTTP 403 Forbidden", method=m, status=403), False, "backoff"),
        (x_api.XApiRefusedError("x: HTTP 400 Invalid Request", method=m, status=400), False, "backoff"),
        (x_api.XApiRefusedError("x: HTTP 404", method=m, status=404), False, "backoff"),
        (x_api.XApiRefusedError("x: HTTP 410 Gone", method=m, status=410), False, "backoff"),
        (x_api.XApiRefusedError("x: end_time must be after start_time — not sent", method=m), True, "post"),
        (x_api.XApiException("x: something new", method=m), False, "tick"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("error,refunded,verdict", _x_errors(), ids=lambda v: type(v).__name__
                         if isinstance(v, BaseException) else str(v))
async def test_every_x_error_class_settles_the_reserve_and_the_day(env, error, refunded, verdict):
    env.x_account_holder(THU)
    first = env.ledger.post("x", published_at=THU - timedelta(days=3, hours=2))
    second = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=2))
    # A Bluesky post whose first read fails keeps the day open, so a second attempt runs the same day.
    env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(4): bview(bsky_uri(4), likes=1)}
    env.bsky.errors = [bluesky.BlueskyRateLimitError("bluesky: HTTP 429", method="m", status=429)]
    for pid in (first, second):
        ext = env.ledger.raw(pid)["external_id"]
        env.x.timeline[ext] = xpost(ext, like_count=2)
    env.x.errors = [error]
    await env.measure(THU)

    expected = [("x_metrics_read", RESERVE)] + ([("x_metrics_read_correction", -RESERVE)] if refunded else [])
    assert env.ledger.journal(first) == expected
    assert env.ledger.row(first)["cost_micros"] == (0 if refunded else RESERVE)
    # Only a definite refusal leaves the X-wide pause — on the NEWEST X post (`second`), never the refused
    # one, and beside its (absent) measurement: no status, no checkpoint, no follower snapshot.
    assert ("x_reads_refused_until" in env.ledger.metrics(second)) == (verdict == "backoff")
    assert "x_reads_refused_until" not in env.ledger.metrics(first)
    if verdict == "backoff":
        assert env.ledger.metrics(second) == {"v": 1, "rev": 1, "x_reads_refused_until": "2026-10-15"}
    if verdict == "post":
        assert env.ledger.metrics(first)["status"] == "error" and len(env.x.reads) == 2
        assert env.ledger.metrics(second)["status"] == "ok"
    elif verdict == "backoff":
        m = env.ledger.metrics(first)
        assert m["status"] == "unavailable" and m["unavailable_until"] == "2026-10-15" and m["cps"] == []
        assert m["note"].startswith(f"X refused the read: {type(error).__name__}")
        assert len(env.x.reads) == 1 and env.ledger.journal(second) == []
    else:
        assert env.ledger.metrics(first) == {} and len(env.x.reads) == 1 and env.ledger.journal(second) == []

    reads = len(env.x.reads)
    await env.measure(THU + timedelta(minutes=10))          # the second attempt of the same ET day
    if verdict in ("today", "backoff"):
        assert len(env.x.reads) == reads                    # X stays stopped for the day
        if verdict == "today":
            assert env.ledger.metrics(first) == {}
    elif verdict == "tick":
        assert len(env.x.reads) == reads + 2                # retried: both posts read now
        assert env.ledger.metrics(first)["status"] == "ok" and env.ledger.metrics(second)["status"] == "ok"
    else:
        assert len(env.x.reads) == reads                    # nothing left to read today
    assert env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_a_corrupt_result_count_never_journals_more_than_the_page(env, monkeypatch):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}

    async def inflated(*a, **k):
        res = await env.x.list_user_posts_metrics(*a, **k)
        return {**res, "result_count": 10**12}

    monkeypatch.setattr(x_api, "list_user_posts_metrics", inflated)
    await env.measure(THU)
    # At most the page asked for (5 posts = the reserve), so no positive correction at all.
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE)]
    assert env.ledger.metrics(pid)["status"] == "ok"


@pytest.mark.asyncio
async def test_a_refund_that_cannot_be_journaled_never_moves_the_running_total(env, monkeypatch):
    env.x_account_holder(THU)
    first = env.ledger.post("x", published_at=THU - timedelta(days=3, hours=2))
    second = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=2))
    ext = env.ledger.raw(second)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=2)}
    # The first read is refused locally (refund + carry on); its refund write hits a ledger failure.
    env.x.errors = [x_api.XApiRefusedError("x: end_time must be after start_time — not sent", method="m")]
    env.x.on_read = lambda: (env.ledger.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 503"))
                             if len(env.x.reads) == 1 else None)
    added: List[int] = []
    from app.services.marketing import publisher_service as pub
    real_budgets = pub._budgets

    def recording(svc):
        budgets = real_budgets(svc)
        x_budget = budgets["x"]
        real_add = x_budget.add

        def add(micros):
            added.append(micros)
            real_add(micros)

        x_budget.add = add
        return budgets

    monkeypatch.setattr(pub, "_budgets", recording)
    await env.measure(THU)
    assert env.ledger.journal(first) == [("x_metrics_read", RESERVE)]          # the refund did not land…
    assert added == [RESERVE, RESERVE, 1_000 - RESERVE]                       # …so the total kept the reserve
    assert env.ledger.metrics(first)["status"] == "error" and env.ledger.metrics(second)["status"] == "ok"


@pytest.mark.asyncio
async def test_a_429_leaves_the_day_open_and_a_403_pauses_x_for_a_week(env, caplog):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}
    env.x.errors = [x_api.XApiRateLimitError("x: HTTP 429", method="m", status=429)]
    await env.measure(THU)
    assert env.jobs.finishes[-1]["success"] is False                       # 429: the day stays open
    env.x.errors = [x_api.XApiForbiddenError("x: HTTP 403 Forbidden", method="m", status=403)]
    await env.measure(THU + timedelta(minutes=10))
    assert env.jobs.finishes[-1]["success"] is True                        # 403: X is done for today …
    assert _messages(caplog, "x reads STOPPED for 2026-10-08", logging.ERROR)
    reads = len(env.x.reads)
    await env.measure(THU + timedelta(minutes=20))
    assert len(env.x.reads) == reads
    m = env.ledger.metrics(pid)                                            # … and for a week (review #2)
    assert m["status"] == "unavailable" and m["unavailable_until"] == "2026-10-15" and m["cps"] == []
    # Tomorrow X is NOT read again: the same refusal would be paid for every day.
    await env.measure(THU + timedelta(days=1))
    assert len(env.x.reads) == reads and env.ledger.metrics(pid)["status"] == "unavailable"
    # The back-off's day: read again.
    await env.measure(THU + timedelta(days=7))
    assert len(env.x.reads) == reads + 1 and env.ledger.metrics(pid)["status"] == "ok"
    assert env.ledger.metrics(pid)["unavailable_until"] is None


def _forbidden(method: str = "list_user_posts_metrics") -> x_api.XApiForbiddenError:
    return x_api.XApiForbiddenError(f"x {method}: HTTP 403 Forbidden", method=method, status=403)


@pytest.mark.asyncio
async def test_a_definite_refusal_costs_one_reserve_then_nothing_for_a_week_then_one_retry(env):
    """Review 2026-10-01 #2/#3: a refused read (a 403 for the app's access level, a 400 after a field
    rename) charged the same oldest post's reserve EVERY day, and the follower read never ran."""
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    later = env.ledger.post("x", published_at=THU - timedelta(hours=12))        # due from Friday
    for p in (pid, later):
        ext = env.ledger.raw(p)["external_id"]
        env.x.timeline[ext] = xpost(ext, like_count=2)
    # No account snapshot anywhere: without the pause the account read would be due — and paid — daily.
    env.x.errors = [_forbidden()]
    await env.measure(THU)
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE)]            # kept: X may have billed it
    assert len(env.x.reads) == 1 and env.x.me_calls == 0
    m = env.ledger.metrics(pid)
    assert m["status"] == "unavailable" and m["unavailable_until"] == "2026-10-15" and m["cps"] == []
    assert m["note"] == "X refused the read: XApiForbiddenError: x list_user_posts_metrics: HTTP 403 Forbidden"
    for day in range(1, 7):                                  # Fri 10-09 … Wed 10-14: nothing read or charged
        await env.measure(THU + timedelta(days=day))
        fin = env.jobs.finishes[-1]
        assert fin["success"] is True and fin["error"] == f"x paused until 2026-10-15: {m['note']}"
    assert len(env.x.reads) == 1 and env.x.me_calls == 0
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE)] and env.ledger.journal(later) == []
    # Thu 10-15, the back-off's day: each due post is read once, and the account.
    await env.measure(THU + timedelta(days=7))
    assert len(env.x.reads) == 3 and env.x.me_calls == 1
    assert env.ledger.metrics(pid)["status"] == "ok" and env.ledger.metrics(pid)["cps"] == [1, 3, 7]
    assert env.ledger.metrics(later)["status"] == "ok" and env.ledger.metrics(later)["account"]["followers"] == 42
    assert env.jobs.finishes[-1]["success"] is True and env.jobs.finishes[-1]["error"] is None


@pytest.mark.asyncio
async def test_a_refusal_that_never_ends_costs_one_reserve_a_week_never_one_a_day(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.x.errors = [_forbidden() for _ in range(30)]
    for day in range(21):
        await env.measure(THU + timedelta(days=day))
        assert env.jobs.finishes[-1]["success"] is True
    # Days 0, 7 and 14 — three reserves in three weeks, where the old code paid twenty-one.
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE)] * 3 and len(env.x.reads) == 3
    assert env.x.me_calls == 0 and env.ledger.metrics(pid)["unavailable_until"] == "2026-10-29"


@pytest.mark.asyncio
async def test_the_x_pause_outlives_the_refused_post_leaving_the_30_day_listing(env):
    """Re-review 2026-10-02 r1.0 / r2.1, the mechanism: the refused post is the OLDEST due one — here at
    its 28-day checkpoint — and leaves the 30-day listing two days into its own pause. A pause kept only on
    it ended there, and the newer post was paid for. It now also rides on the NEWEST X post, beside its
    measurement (never in its status, checkpoints or history), and holds the whole week."""
    env.x_account_holder(THU)
    old = env.ledger.post("x", published_at=THU - timedelta(days=28, hours=1), metrics={
        "v": 1, "rev": 1, "status": "ok", "measured_day": "2026-09-18", "cps": [1, 3, 7]})
    measured = {"v": 1, "rev": 4, "status": "ok", "measured_day": "2026-10-07", "note": None,
                "unavailable_until": None, "cps": [1], "last": _entry("2026-10-07", likes=2),
                "history": [_entry("2026-10-07", likes=2)]}
    newest = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=20), metrics=measured)
    ext = env.ledger.raw(newest)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=9)}
    env.x.errors = [_forbidden()]
    await env.measure(THU)
    assert len(env.x.reads) == 1 and env.ledger.journal(old) == [("x_metrics_read", RESERVE)]
    refused = env.ledger.metrics(old)
    assert refused["status"] == "unavailable" and refused["unavailable_until"] == "2026-10-15"
    assert refused["cps"] == [1, 3, 7] and "x_reads_refused_until" not in refused
    assert env.ledger.metrics(newest) == {**measured, "rev": 5, "x_reads_refused_until": "2026-10-15"}
    named = "x paused until 2026-10-15: X refused the read: XApiForbiddenError: x list_user_posts_metrics: " \
            "HTTP 403 Forbidden"
    assert env.jobs.finishes[-1]["error"] == named
    for day in range(1, 7):
        now = THU + timedelta(days=day)
        listed = await env.ledger.svc.list_measurable_posts(since=now - ms.MEASURE_MAX_AGE, limit=300)
        assert (old in {r["id"] for r in listed}) == (day == 1)           # gone from Sat 10-10
        await env.measure(now)                                           # the newer post is due from 10-10
        fin = env.jobs.finishes[-1]
        # While the refused post is listed its note names the refusal; after, the marker alone holds the
        # pause and the note says so plainly — never a read, never a charge.
        assert fin["success"] is True and fin["error"] == (named if day == 1 else
                                                           "x paused until 2026-10-15: X refused a read")
    assert len(env.x.reads) == 1 and env.ledger.journal(newest) == []
    # Thu 10-15, the back-off's day: read again, and the note is clean.
    await env.measure(THU + timedelta(days=7))
    assert len(env.x.reads) == 2 and env.ledger.metrics(newest)["status"] == "ok"
    assert env.ledger.metrics(newest)["cps"] == [1, 3, 7] and env.jobs.finishes[-1]["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["marker", "post"])
async def test_a_back_off_write_that_fails_is_logged_and_the_one_that_landed_still_pauses_x(env, monkeypatch,
                                                                                             caplog, lost):
    """Degraded: one of the two back-off writes hits a ledger error. It is logged at ERROR with the post
    and never raised; neither write holds the day (a retry would only pay for the same refusal again); and
    the write that landed still pauses X the next day."""
    env.x_account_holder(THU)
    refused = env.ledger.post("x", published_at=THU - timedelta(days=28, hours=1), metrics={
        "v": 1, "rev": 1, "cps": [1, 3, 7]})
    newest = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=20), metrics={
        "v": 1, "rev": 1, "cps": [1]})
    failing = newest if lost == "marker" else refused
    real = env.ledger.svc.merge_post_metrics

    async def flaky(post_id, **kw):
        if post_id == failing:
            raise mrs.MarketingRunError(f"merge_post_metrics failed (post_id={post_id}): RuntimeError: PostgREST 503")
        return await real(post_id, **kw)

    monkeypatch.setattr(env.ledger.svc, "merge_post_metrics", flaky)
    env.x.errors = [_forbidden() for _ in range(3)]
    counters = await env.measure(THU)
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True and counters["incomplete"] == 0
    assert fin["error"].startswith("x paused until 2026-10-15: X refused the read: XApiForbiddenError")
    what = "back-off (x_reads_refused_until)" if lost == "marker" else "metrics (unavailable)"
    (line,) = _messages(caplog, f"{what} NOT written post_id={failing}", logging.ERROR)
    assert "PostgREST 503" in line
    if lost == "marker":
        assert "x_reads_refused_until" not in env.ledger.metrics(newest)
        assert env.ledger.metrics(refused)["status"] == "unavailable"
    else:
        assert env.ledger.metrics(newest)["x_reads_refused_until"] == "2026-10-15"
        assert env.ledger.metrics(refused) == {"v": 1, "rev": 1, "cps": [1, 3, 7]}
    monkeypatch.setattr(env.ledger.svc, "merge_post_metrics", real)
    await env.measure(THU + timedelta(days=1))                 # the refused post is still listed today
    assert len(env.x.reads) == 1 and env.ledger.journal(newest) == []
    assert env.jobs.finishes[-1]["error"].startswith("x paused until 2026-10-15: X refused")
    if lost == "post":
        # The marker alone carries the pause past the refused post's last listed day.
        for day in range(2, 7):
            await env.measure(THU + timedelta(days=day))
        assert len(env.x.reads) == 1 and env.jobs.finishes[-1]["error"] == "x paused until 2026-10-15: X refused a read"


@pytest.mark.asyncio
@pytest.mark.parametrize("error,refunded,verdict", _x_errors(), ids=lambda v: type(v).__name__
                         if isinstance(v, BaseException) else str(v))
async def test_only_a_definite_refusal_pauses_every_x_read_for_a_week(env, error, refunded, verdict):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}
    env.x.errors = [error]
    await env.measure(THU)
    for day in range(1, 7):
        await env.measure(THU + timedelta(days=day))
    m = env.ledger.metrics(pid)
    if verdict == "backoff":
        assert len(env.x.reads) == 1 and m["status"] == "unavailable" and m["unavailable_until"] == "2026-10-15"
        await env.measure(THU + timedelta(days=7))
        assert len(env.x.reads) == 2 and env.ledger.metrics(pid)["status"] == "ok"
    else:
        # Not a definite refusal (rate limit, no credits, 5xx, not sent, refused before sending …): the
        # next day reads it, nothing pauses X, and the week's later checkpoints are read on their days.
        assert m["status"] == "ok" and m["unavailable_until"] is None and m["cps"] == [1, 3, 7]
        assert len(env.x.reads) == 4       # the failed read, then the 1/3/7-day checkpoints on days 1, 2, 6


@pytest.mark.asyncio
async def test_a_refused_account_read_has_its_own_back_off_and_the_post_reads_go_on(env, caplog):
    """Review K6 / #2: get_me refused on every day it was due and was paid for each time. Its back-off
    lives BESIDE status and account on the newest post — and a User-read refusal never pauses the
    post reads (another endpoint)."""
    d0 = et(2026, 10, 12, 12)                               # Monday: the account read is due
    stale = {"followers": 30, "at": et(2026, 10, 1, 12).isoformat(), "source": "x_api"}
    older = env.ledger.post("x", published_at=et(2026, 10, 1, 9), metrics={
        "v": 1, "rev": 3, "status": "ok", "measured_day": "2026-10-09", "cps": [1, 3, 7], "account": stale})
    newest = env.ledger.post("x", published_at=et(2026, 10, 10, 16), metrics={
        "v": 1, "rev": 2, "status": "ok", "measured_day": "2026-10-11", "note": None, "unavailable_until": None,
        "cps": [1], "last": _entry("2026-10-11", likes=3), "history": [_entry("2026-10-11", likes=3)]})
    newer = None
    for p in (older, newest):
        ext = env.ledger.raw(p)["external_id"]
        env.x.timeline[ext] = xpost(ext, like_count=5)
    env.x.me = _forbidden("get_me")
    before = env.ledger.metrics(newest)
    await env.measure(d0)
    assert env.x.me_calls == 1 and env.ledger.journal(newest) == [("x_account_read", USER_READ)]   # kept
    m = env.ledger.metrics(newest)
    assert m["x_account_refused_until"] == "2026-10-19"
    assert {k: v for k, v in m.items() if k not in ("x_account_refused_until", "rev")} == \
        {k: v for k, v in before.items() if k != "rev"}       # status, measured_day, last, cps: untouched
    assert env.ledger.metrics(older)["account"] == stale      # the last follower snapshot survives
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True
    assert fin["error"] == ("x account read paused until 2026-10-19: X refused it: XApiForbiddenError: "
                            "x get_me: HTTP 403 Forbidden")
    assert not _messages(caplog, "x reads STOPPED")           # X itself was never stopped
    # The rest of the week: the snapshot is stale (the read is due every day) but paused — no charge —
    # while every post read still happens on its day, even after the newest post changes.
    reads_on: Dict[str, int] = {}
    for day in range(1, 7):
        now = d0 + timedelta(days=day)
        if day == 5:                                          # Sat 10-17: a new newest post, no marker
            newer = env.ledger.post("x", published_at=et(2026, 10, 16, 16))
            ext = env.ledger.raw(newer)["external_id"]
            env.x.timeline[ext] = xpost(ext, like_count=1)
        before_reads = len(env.x.reads)
        await env.measure(now)
        reads_on[now.astimezone(ET).date().isoformat()] = len(env.x.reads) - before_reads
        fin = env.jobs.finishes[-1]
        assert fin["success"] is True and fin["error"] == "x account read paused until 2026-10-19 (X refused it)"
    assert env.x.me_calls == 1
    assert reads_on == {"2026-10-13": 0, "2026-10-14": 1, "2026-10-15": 0, "2026-10-16": 0, "2026-10-17": 0,
                        "2026-10-18": 2}
    m = env.ledger.metrics(newest)
    assert m["cps"] == [1, 3, 7] and m["x_account_refused_until"] == "2026-10-19"   # survives the measurements
    assert env.ledger.journal(newest).count(("x_account_read", USER_READ)) == 1
    # Mon 10-19, the back-off's day: the account is read again — onto the post that is newest NOW.
    env.x.me = FakeXReads().me
    await env.measure(d0 + timedelta(days=7))
    assert env.x.me_calls == 2 and env.ledger.metrics(newer)["account"]["followers"] == 42
    assert env.jobs.finishes[-1]["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error,refunded,backoff", [
    (_forbidden("get_me"), False, True),
    (x_api.XApiAuthError("x get_me: HTTP 401 Unauthorized", method="get_me", status=401), False, True),
    (x_api.XApiRefusedError("x get_me: HTTP 400", method="get_me", status=400), False, True),
    (x_api.XApiRefusedError("x get_me: HTTP 404", method="get_me", status=404), False, True),
    (x_api.XApiCreditsDepletedError("x get_me: HTTP 402", method="get_me", status=402), True, False),
    (x_api.XApiRateLimitError("x get_me: HTTP 429", method="get_me", status=429), True, False),
    (x_api.XApiNotSentError("x get_me: not sent (ConnectError)", method="get_me"), True, False),
    (x_api.XApiRefusedError("x get_me: refused before sending", method="get_me"), True, False),
    (x_api.XApiAmbiguousError("x get_me: HTTP 503", method="get_me", status=503), False, False),
    (x_api.XApiDuplicateContentError("x get_me: HTTP 403 duplicate", method="get_me", status=403), False, False),
], ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else str(v))
async def test_only_a_definite_refusal_of_the_account_read_backs_it_off(env, error, refunded, backoff):
    newest = env.ledger.post("x", published_at=THU - timedelta(hours=5))
    env.x.me = error
    await env.measure(THU)
    assert env.ledger.journal(newest) == [("x_account_read", USER_READ)] + (
        [("x_account_read_correction", -USER_READ)] if refunded else [])
    m = env.ledger.metrics(newest)
    assert m.get("x_account_refused_until") == ("2026-10-15" if backoff else None)
    assert "status" not in m and "account" not in m
    # The next day the account read is due again (no snapshot) — and runs unless it was backed off.
    env.x.me = FakeXReads().me
    await env.measure(THU + timedelta(days=1))
    assert env.x.me_calls == (1 if backoff else 2)


def test_the_account_back_off_never_touches_status_or_the_follower_snapshot():
    account = {"followers": 30, "at": "2026-10-01T16:00:00+00:00", "source": "x_api"}
    old = {"v": 1, "rev": 7, "status": "ok", "measured_day": "2026-10-07", "note": None, "cps": [1, 3],
           "last": _entry("2026-10-07", likes=4), "history": [_entry("2026-10-07", likes=4)], "account": account}
    doc = ms.apply_account_refusal(old, date(2026, 10, 15))
    assert doc == {**old, "x_account_refused_until": "2026-10-15"}
    assert "x_account_refused_until" not in old                                  # pure
    assert ms.apply_account_refusal(None, date(2026, 10, 15)) == {"v": 1, "x_account_refused_until": "2026-10-15"}
    # Every later write keeps it: a measurement, and an account snapshot (which replaces `account` only).
    later = ms.apply_measurement(doc, platform="x", today=date(2026, 10, 9), status="missing", note="gone", cps=[1])
    assert later["x_account_refused_until"] == "2026-10-15" and later["account"] == account
    assert ms.apply_account(doc, {"followers": 31})["x_account_refused_until"] == "2026-10-15"


@pytest.mark.parametrize("key", ["x_account_refused_until", "x_reads_refused_until", "upload_post_plan_refused_until"])
def test_every_back_off_marker_is_written_beside_the_measurement_never_in_it(key):
    """The three platform-wide back-offs share one pure writer. The stored names are pinned as literals:
    renaming one would orphan every pause in force (and pay for the refused read again)."""
    assert ms.BACKOFF_KEYS == ("x_account_refused_until", "x_reads_refused_until", "upload_post_plan_refused_until")
    account = {"followers": 30, "at": "2026-10-01T16:00:00+00:00", "source": "upload_post"}
    old = {"v": 1, "rev": 7, "status": "unavailable", "measured_day": "2026-10-07", "note": "plan: 403",
           "unavailable_until": "2026-10-14", "cps": [1, 3], "last": _entry("2026-10-07", likes=4),
           "history": [_entry("2026-10-07", likes=4)], "account": account}
    doc = ms.apply_backoff(old, key, date(2026, 10, 15))
    assert doc == {**old, key: "2026-10-15"} and key not in old                  # pure
    assert ms.apply_backoff(None, key, date(2026, 10, 15)) == {"v": 1, key: "2026-10-15"}
    assert ms.apply_backoff(["junk"], key, date(2026, 10, 15)) == {"v": 1, key: "2026-10-15"}
    # Every later write keeps it: a measurement (any status), and an account snapshot.
    for status in ms.STATUSES:
        assert ms.apply_measurement(doc, platform="x", today=date(2026, 10, 9), status=status)[key] == "2026-10-15"
    assert ms.apply_account(doc, {"followers": 31})[key] == "2026-10-15"
    # A later back-off of the same kind moves the date; another kind sits beside it.
    assert ms.apply_backoff(doc, key, date(2026, 10, 22))[key] == "2026-10-22"
    for other in ms.BACKOFF_KEYS:
        if other != key:
            both = ms.apply_backoff(doc, other, date(2026, 10, 20))
            assert both == {**doc, other: "2026-10-20"} and both[key] == "2026-10-15"


@pytest.mark.parametrize("bad", ["status", "account", "unavailable_until", "measured_day", "note", "cps", "last",
                                 "history", "rev", "v", "", "X_READS_REFUSED_UNTIL", "x_reads_refused_until ",
                                 None, 7, ("x_reads_refused_until",)])
def test_the_back_off_writer_refuses_any_other_key(bad):
    """It can never overwrite a measurement field, whatever it is handed."""
    with pytest.raises(ValueError):
        ms.apply_backoff({"v": 1, "status": "ok"}, bad, date(2026, 10, 15))


#: Placeholders in the table below: the platform's OWN marker key, and the OTHER platform's.
_OWN, _OTHER = "<own marker>", "<other marker>"
_UNAV = {"status": "unavailable"}


@pytest.mark.parametrize("own,other", [("x_reads_refused_until", "upload_post_plan_refused_until"),
                                       ("upload_post_plan_refused_until", "x_reads_refused_until")])
@pytest.mark.parametrize("docs,expected", [
    ([], None),
    ([None, ["junk"], "text"], None),                                                   # no readable document
    # The marker alone (the refused post has left the listing): its date, no note — the caller says why.
    ([{_OWN: "2026-10-12"}], (date(2026, 10, 12), "")),
    # The refused post alone (a row written before the marker existed): its date and its note.
    ([{**_UNAV, "unavailable_until": "2026-10-12", "note": "refused: 403"}], (date(2026, 10, 12), "refused: 403")),
    # Both on the same date — in either order: the refused post's note names it.
    ([{**_UNAV, "unavailable_until": "2026-10-12", "note": "n"}, {_OWN: "2026-10-12"}], (date(2026, 10, 12), "n")),
    ([{_OWN: "2026-10-12"}, {**_UNAV, "unavailable_until": "2026-10-12", "note": "n"}], (date(2026, 10, 12), "n")),
    # The LATEST date in force wins, on either side; an older pause's note never names a newer one.
    ([{**_UNAV, "unavailable_until": "2026-10-10", "note": "old"}, {_OWN: "2026-10-15"}], (date(2026, 10, 15), "")),
    ([{**_UNAV, "unavailable_until": "2026-10-15", "note": "new"}, {_OWN: "2026-10-10"}], (date(2026, 10, 15), "new")),
    ([{_OWN: "2026-10-10"}, {_OWN: "2026-10-14"}, {_OWN: "2026-10-09"}], (date(2026, 10, 14), "")),
    # Junk never pauses — today, past, more than a week out, unreadable — and never hides a real pause.
    ([{_OWN: "2026-10-08"}], None),
    ([{_OWN: "2026-10-07"}], None),
    ([{_OWN: "2026-10-16"}], None),
    ([{_OWN: 20261012}], None),
    ([{_OWN: ["2026-10-12"]}], None),
    ([{_OWN: "2026-10-12T23:59:59-11:00"}], (date(2026, 10, 12), "")),                 # its date part is read
    ([{_OWN: "2099-01-01"}, {**_UNAV, "unavailable_until": "2026-10-12", "note": "n"}], (date(2026, 10, 12), "n")),
    ([{**_UNAV, "unavailable_until": "9999-12-31", "note": "n"}, {_OWN: "2026-10-09"}], (date(2026, 10, 9), "")),
    # Another back-off's key is not this pause; neither is an `unavailable_until` without `unavailable`.
    ([{"x_account_refused_until": "2026-10-12", _OTHER: "2026-10-12"}], None),
    ([{"status": "error", "unavailable_until": "2026-10-12", "note": "n"}], None),
    # A note that is not text is no note; an empty one gives way to a readable one on the same date.
    ([{**_UNAV, "unavailable_until": "2026-10-12", "note": None}], (date(2026, 10, 12), "")),
    ([{**_UNAV, "unavailable_until": "2026-10-12", "note": {"x": 1}}], (date(2026, 10, 12), "")),
    ([{**_UNAV, "unavailable_until": "2026-10-12", "note": ""},
      {**_UNAV, "unavailable_until": "2026-10-12", "note": "second"}], (date(2026, 10, 12), "second")),
])
def test_the_pause_is_the_latest_date_in_force_across_the_marker_and_the_refused_post(docs, expected, own, other):
    def real(doc: Any) -> Any:
        if not isinstance(doc, dict):
            return doc
        return {{_OWN: own, _OTHER: other}.get(k, k): v for k, v in doc.items()}

    rows = [{"id": str(i), "metrics": real(d)} for i, d in enumerate(docs)]
    assert ms._paused_until(rows, date(2026, 10, 8), own) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("until,paused", [
    ("2026-10-09", True), ("2026-10-15", True),                           # in force: tomorrow … a week out
    ("2026-10-08", False), ("2026-10-01", False),                         # its own day, or past: read again
    ("2026-10-16", False), ("2099-01-01", False), ("9999-12-31", False),  # further than we ever write: junk
    ("soon", False), ("", False), ("2026-13-40", False), (20261015, False), (None, False), (["2026-10-15"], False),
])
@pytest.mark.parametrize("kind", ["x", "x_account", "upload_post", "x_reads_marker", "upload_post_marker"])
async def test_only_a_back_off_date_in_force_pauses_and_junk_is_never_obeyed(env, kind, until, paused):
    """A hand-edited or corrupt back-off date further than a week out could pause a platform for years:
    only a date our writer could have stored (after today, at most UNAVAILABLE_DAYS ahead) is obeyed —
    on a refused post, and on the platform-wide markers kept on the newest post."""
    if kind in ("x", "x_reads_marker"):
        env.x_account_holder(THU)
        pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1), metrics={
            "v": 1, "rev": 1, "status": "unavailable", "unavailable_until": until, "cps": []} if kind == "x" else None)
        ext = env.ledger.raw(pid)["external_id"]
        env.x.timeline = {ext: xpost(ext, like_count=1)}
        if kind == "x_reads_marker":       # the newest X post: no checkpoint crossed, never read itself
            env.ledger.post("x", published_at=THU - timedelta(hours=5),
                            metrics={"v": 1, "x_reads_refused_until": until})
    elif kind == "x_account":
        env.ledger.post("x", published_at=THU - timedelta(hours=5), metrics={"v": 1, "x_account_refused_until": until})
    else:
        env.up_configured = True
        env.ledger.post("tiktok", published_at=THU - timedelta(days=2), metrics={
            "v": 1, "rev": 1, "status": "unavailable", "unavailable_until": until} if kind == "upload_post" else None)
        env.up.default = up_answer("tiktok")
        if kind == "upload_post_marker":   # the newest Upload-Post post: under an hour old, never read itself
            env.ledger.post("instagram", published_at=THU - timedelta(minutes=30), metrics={
                "v": 1, "upload_post_plan_refused_until": until})
    await env.measure(THU)
    calls = {"x": len(env.x.reads), "x_account": env.x.me_calls, "upload_post": len(env.up.calls),
             "x_reads_marker": len(env.x.reads), "upload_post_marker": len(env.up.calls)}[kind]
    assert calls == (0 if paused else 1)
    assert env.jobs.finishes[-1]["success"] is True
    assert (env.jobs.finishes[-1]["error"] is not None) == paused     # a pause in force is always named


@pytest.mark.asyncio
async def test_an_errors_only_200_never_uses_up_a_checkpoint_and_never_reads_as_a_deletion(env):
    """Review 2026-10-01 #10: X's partial-error shape (HTTP 200, only `errors`) used to read as an empty
    window — the post `missing`, its checkpoints used up, never measured again."""
    env.x_account_holder(THU)
    first = env.ledger.post("x", published_at=THU - timedelta(days=3, hours=2))
    second = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=2))
    for p in (first, second):
        ext = env.ledger.raw(p)["external_id"]
        env.x.timeline[ext] = xpost(ext, like_count=3)
    problem = ("x list_user_posts_metrics: HTTP 200 Forbidden - User has been suspended: [1234567890]. "
               "(no data; 1 error object(s))")
    env.x.problem = problem
    counters = await env.measure(THU)
    m = env.ledger.metrics(first)
    assert m["status"] == "error" and m["note"] == problem and m["cps"] == [] and "last" not in m
    assert env.ledger.journal(first) == [("x_metrics_read", RESERVE), ("x_metrics_read_correction", -RESERVE)]
    assert env.ledger.row(first)["cost_micros"] == 0                          # nothing returned, nothing billed
    assert len(env.x.reads) == 1 and env.ledger.metrics(second) == {}       # X stops for the day
    assert counters["errors"] == 1 and counters["missing"] == 0
    assert env.jobs.finishes[-1] == {"job": ms.JOB_METRICS, "success": True, "items": 1,
                                     "error": f"x stopped for 2026-10-08: {problem}"}
    # The checkpoint was never used up: once the account answers again, the post is read.
    env.x.problem = None
    await env.measure(THU + timedelta(days=1))
    m = env.ledger.metrics(first)
    assert m["status"] == "ok" and m["cps"] == [1, 3] and m["last"]["cp"] == 3 and m["note"] is None
    assert env.ledger.metrics(second)["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("junk_problem", [{"detail": "suspended"}, ["suspended"], 7, True])
async def test_an_errors_only_answer_with_an_odd_problem_is_still_an_error_never_missing(env, junk_problem):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=2))
    env.x.problem = junk_problem
    await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert m["status"] == "error" and m["note"] == "X answered the read with errors only" and m["cps"] == []


@pytest.mark.asyncio
async def test_a_post_found_late_by_reconcile_is_read_around_xs_own_creation_time(env):
    """Review 2026-10-01 #17: reconcile may store a `published_at` hours after X created the post. The
    window is centred on the snowflake's time; centred on `published_at` it would miss the post and
    record it `missing`, its checkpoints used up and the read paid for."""
    env.x_account_holder(THU)
    made = THU - timedelta(days=1, hours=3)
    ext = snowflake(made, 7)
    pid = env.ledger.post("x", published_at=made + timedelta(hours=2), external_id=ext)
    env.x.timeline = {ext: xpost(ext, like_count=6)}
    await env.measure(THU)
    (read,) = env.x.reads
    assert abs(read["start"] - (made - timedelta(minutes=10))) < timedelta(milliseconds=2)
    assert abs(read["end"] - (made + timedelta(minutes=10))) < timedelta(milliseconds=2)
    m = env.ledger.metrics(pid)
    assert m["status"] == "ok" and m["last"]["likes"] == 6 and m["cps"] == [1]


@pytest.mark.asyncio
async def test_a_post_retracted_mid_read_refuses_the_metrics_but_keeps_the_correction(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}

    def retract():
        row = env.ledger.raw(pid)
        row["status"], row["updated_at"] = "retracted", _real_now().isoformat()

    env.x.on_read = retract
    counters = await env.measure(THU)
    row = env.ledger.row(pid)
    assert row["status"] == "retracted" and row.get("metrics") == {}
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE), ("x_metrics_read_correction", 1_000 - RESERVE)]
    assert row["cost_micros"] == 1_000 and counters["not_written"] == 1


@pytest.mark.asyncio
async def test_a_row_that_moved_before_the_charge_is_not_read(env, monkeypatch):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    original = env.ledger.svc.list_measurable_posts

    async def stale_list(**kw):
        rows = await original(**kw)
        env.ledger.raw(pid)["updated_at"] = _real_now().isoformat()      # a retract request lands meanwhile
        return rows

    monkeypatch.setattr(env.ledger.svc, "list_measurable_posts", stale_list)
    counters = await env.measure(THU)
    assert env.x.reads == [] and env.ledger.journal(pid) == [] and counters["incomplete"] == 1


@pytest.mark.asyncio
async def test_dry_run_never_pays_for_a_read_but_still_reads_bluesky(env, monkeypatch, caplog):
    monkeypatch.setattr(ms.settings, "MARKETING_DRY_RUN", True)
    x = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    b = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(2): bview(bsky_uri(2), likes=1)}
    counters = await env.measure(THU)
    assert env.x.reads == [] and env.x.me_calls == 0
    assert env.ledger.journal(x) == [] and env.ledger.metrics(x) == {}
    assert counters["would_read"] == 2 and len(_messages(caplog, "would read")) == 2
    assert env.ledger.metrics(b)["status"] == "ok"


@pytest.mark.asyncio
async def test_an_x_post_without_an_id_is_recorded_once_and_never_read(env):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1), external_id=None)
    counters = await env.measure(THU)
    assert env.x.reads == [] and env.ledger.journal(pid) == []
    assert env.ledger.metrics(pid)["status"] == "no_external_id" and counters["no_external_id"] == 1


@pytest.mark.asyncio
async def test_x_history_keeps_four_entries(env):
    env.x_account_holder(THU)
    days = ["2026-09-11", "2026-09-13", "2026-09-17", "2026-09-30"]
    pid = env.ledger.post("x", published_at=THU - timedelta(days=28, hours=1), metrics={
        "v": 1, "rev": 4, "cps": [1, 3, 7], "history": [_entry(d, likes=1) for d in days]})
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=9)}
    await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert [h["day"] for h in m["history"]] == days[1:] + ["2026-10-08"] and m["cps"] == [1, 3, 7, 28]


# the X account (get_me) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("now,account_age,expected", [
    (MON, None, True),                                    # Monday, never read
    (THU, None, True),                                    # no snapshot at all: read it
    (THU, timedelta(days=3), False),
    (THU, timedelta(days=8), False),                      # exactly eight days: not yet
    (THU, timedelta(days=8, minutes=1), True),            # older than eight days
    (MON, timedelta(days=7), True),                       # last Monday's
    (MON, timedelta(minutes=30), False),                  # already read this Monday
])
async def test_the_x_account_is_read_on_mondays_or_when_stale(env, now, account_age, expected):
    older = env.ledger.post("x", published_at=now - timedelta(days=12), metrics={
        "v": 1, "cps": [1, 3, 7],
        **({"account": {"followers": 30, "at": (now - account_age).isoformat(), "source": "x_api"}}
           if account_age is not None else {})})
    newest = env.ledger.post("x", published_at=now - timedelta(hours=5))
    before = env.ledger.metrics(older)
    await env.measure(now)
    assert env.x.me_calls == (1 if expected else 0)
    if expected:
        assert env.ledger.journal(newest) == [("x_account_read", USER_READ)]
        account = env.ledger.metrics(newest)["account"]
        assert account == {"followers": 42, "following": 7, "posts": 19, "at": now.isoformat(), "source": "x_api"}
        assert "measured_day" not in env.ledger.metrics(newest)        # an account read measures no post
    else:
        assert env.ledger.journal(newest) == []
    assert env.ledger.metrics(older) == before                          # older posts keep their snapshot


@pytest.mark.asyncio
async def test_a_refused_account_read_is_refunded_only_when_nothing_was_billed(env):
    newest = env.ledger.post("x", published_at=THU - timedelta(hours=5))
    env.x.me = x_api.XApiRateLimitError("x: HTTP 429", method="get_me", status=429)
    await env.measure(THU)
    assert env.ledger.journal(newest) == [("x_account_read", USER_READ), ("x_account_read_correction", -USER_READ)]
    assert env.jobs.finishes[-1]["success"] is False                    # retried on a later tick
    env.x.me = x_api.XApiAmbiguousError("x: HTTP 500", method="get_me", status=500)
    await env.measure(THU + timedelta(minutes=10))
    assert env.ledger.journal(newest)[-1] == ("x_account_read", USER_READ)     # kept: X may have billed it


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_urls,edge", [(False, 2_000_000 - 70_000), (True, 2_000_000 - 810_000)])
async def test_the_account_read_respects_the_headroom_too(env, monkeypatch, allow_urls, edge):
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
    newest = env.ledger.post("x", published_at=THU - timedelta(hours=5))
    spender = env.ledger.spend(edge + 1)                        # one micro inside: no read, no charge
    await env.measure(THU)
    assert env.x.me_calls == 0 and env.ledger.journal(newest) == []
    assert env.jobs.finishes[-1]["success"] is True
    # Exactly at the edge (a $0.010 User read plus the headroom fits to the micro): read. Run again the
    # same day as another process would (the post is not due yet, so only the account is read).
    env.ledger.raw(spender)["metadata"]["charges"][0]["micros"] = edge
    monkeypatch.setattr(ms, "_DAY_JOBS", {})
    env.jobs.rows[ms.JOB_METRICS]["run_day"] = None
    await env.measure(THU + timedelta(hours=1))
    assert env.x.me_calls == 1 and env.ledger.journal(newest) == [("x_account_read", 10_000)]


@pytest.mark.asyncio
async def test_an_account_answer_without_counts_stores_no_snapshot(env):
    newest = env.ledger.post("x", published_at=THU - timedelta(hours=5))
    env.x.me = {"id": X_USER, "username": None, "public_metrics": {"followers_count": "many", "tweet_count": -1}}
    await env.measure(THU)
    assert "account" not in env.ledger.metrics(newest)
    assert env.ledger.journal(newest) == [("x_account_read", USER_READ)]       # it was read: paid


# Upload-Post ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_post_counts_map_and_followers_land_on_the_newest_post_only(env):
    env.up_configured = True
    old = env.ledger.post("tiktok", published_at=THU - timedelta(days=5))
    new = env.ledger.post("tiktok", published_at=THU - timedelta(days=1))
    env.up.default = up_answer("tiktok", metrics={"views": 5200, "likes": 120, "comments": 8, "favorites": 3,
                                                  "shares": "4", "reach": -1, "retention": [{"second": 1}]},
                               followers=950)
    await env.measure(THU)
    assert sorted(env.up.calls) == sorted([("up-job-0001", "tiktok"), ("up-job-0002", "tiktok")])
    last = env.ledger.metrics(old)["last"]
    assert {k: v for k, v in last.items() if k in ms.COUNT_KEYS} == {"views": 5200, "likes": 120, "replies": 8,
                                                                     "bookmarks": 3, "shares": 4}
    assert "account" not in env.ledger.metrics(old)
    assert env.ledger.metrics(new)["account"] == {"followers": 950, "at": THU.isoformat(), "source": "upload_post"}


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_day,stored", [
    (None, True),                                    # no date: stored as read today, as before
    ("2026-10-08", True), ("2026-10-07", True),      # today, or the day before: fresh enough
    ("2026-10-09", True),                            # a day ahead (UTC vs ET — the client allows it)
    ("2026-10-06", False), ("2026-02-20", False),    # more than a day before the read
    ("not-a-date", True), ("", True), (20261006, True),   # unreadable = no date (the client never sends these)
])
async def test_an_upload_post_follower_snapshot_more_than_a_day_old_is_not_stored(env, caplog, snapshot_day, stored):
    """Review 2026-10-01 #11: Upload-Post's follower count is a STORED snapshot with its own date; the
    digest dates a snapshot by our read, so a days-old count would pass for today's."""
    env.up_configured = True
    old = env.ledger.post("tiktok", published_at=THU - timedelta(days=5))
    new = env.ledger.post("tiktok", published_at=THU - timedelta(days=1))
    env.up.default = up_answer("tiktok", followers=950, followers_date=snapshot_day)
    await env.measure(THU)
    m = env.ledger.metrics(new)
    assert m["status"] == "ok" and m["last"]["views"] == 5200           # the post's own counts land either way
    stale_lines = _messages(caplog, "follower count is a snapshot of", logging.INFO)
    if stored:
        assert m["account"] == {"followers": 950, "at": THU.isoformat(), "source": "upload_post"}
        assert stale_lines == []
    else:
        assert "account" not in m and len(stale_lines) == 1 and snapshot_day in stale_lines[0]
    assert "account" not in env.ledger.metrics(old) and env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_a_stale_date_without_a_follower_count_stores_nothing_and_says_nothing(env, caplog):
    env.up_configured = True
    pid = env.ledger.post("tiktok", published_at=THU - timedelta(days=1))
    env.up.default = up_answer("tiktok", followers=None, followers_date="2026-09-01")
    await env.measure(THU)
    assert "account" not in env.ledger.metrics(pid)
    assert _messages(caplog, "follower count is a snapshot of") == []


@pytest.mark.asyncio
async def test_a_stale_follower_snapshot_is_logged_once_per_platform_and_never_stored(env, caplog):
    env.up_configured = True
    for platform in ("tiktok", "instagram"):
        for age in (3, 2, 1):
            env.ledger.post(platform, published_at=THU - timedelta(days=age))
    env.up.answers = {f"up-job-{n:04d}": up_answer("tiktok" if n <= 3 else "instagram", followers=9,
                                                   followers_date="2026-09-30") for n in range(1, 7)}
    await env.measure(THU)
    lines = _messages(caplog, "follower count is a snapshot of", logging.INFO)
    assert len(lines) == 2 and {"tiktok", "instagram"} == {ln.split("Upload-Post's ")[1].split(" ")[0] for ln in lines}
    assert all("account" not in env.ledger.metrics(r["id"]) for r in env.ledger.rows)


@pytest.mark.asyncio
async def test_upload_post_metrics_errors_and_missing_ids(env):
    env.up_configured = True
    err = env.ledger.post("instagram", published_at=THU - timedelta(days=2))
    gone = env.ledger.post("youtube", published_at=THU - timedelta(days=2))
    noid = env.ledger.post("threads", published_at=THU - timedelta(days=2),
                           meta={"dry_run": False, "publish": {"upload_post": {}}})
    other = env.ledger.post("facebook", published_at=THU - timedelta(days=2))
    env.up.answers = {"up-job-0001": up_answer("instagram", metrics=None, error="Token expired\nreconnect"),
                      "up-job-0004": up_answer("linkedin")}         # answers without our platform
    await env.measure(THU)
    assert env.ledger.metrics(err)["status"] == "error" and env.ledger.metrics(err)["note"] == \
        "upload-post: Token expired reconnect"
    assert env.ledger.metrics(gone)["status"] == "missing"            # a 404
    assert env.ledger.metrics(noid)["status"] == "no_external_id"
    assert env.ledger.metrics(other)["status"] == "error" and "without facebook" in env.ledger.metrics(other)["note"]
    assert env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [
    upload_post.UploadPostPlanError("upload-post get_post_analytics: HTTP 403", method="m", status=403),
    upload_post.UploadPostRefusedError("upload-post get_post_analytics: HTTP 402", method="m", status=402),
])
async def test_a_plan_refusal_pauses_upload_post_for_a_week_one_call_a_week(env, refusal):
    env.up_configured = True
    first = env.ledger.post("tiktok", published_at=THU - timedelta(days=6))
    second = env.ledger.post("facebook", published_at=THU - timedelta(days=6))
    env.up.default = refusal
    await env.measure(THU)
    assert len(env.up.calls) == 1 and env.jobs.finishes[-1]["success"] is True
    statuses = [env.ledger.metrics(p).get("status") for p in (first, second)]
    assert sorted(statuses, key=str) == [None, "unavailable"]             # one refused; the other never measured
    m = env.ledger.metrics((first, second)[statuses.index("unavailable")])
    assert m["status"] == "unavailable" and m["unavailable_until"] == "2026-10-15" and m["note"].startswith("plan")
    # The Upload-Post-wide pause rides on the newest Upload-Post post (one of the two: a tie).
    marks = [env.ledger.metrics(p).get("upload_post_plan_refused_until") for p in (first, second)]
    assert marks.count("2026-10-15") == 1 and marks.count(None) == 1
    for day in range(1, 7):                                            # paused: not one call
        await env.measure(THU + timedelta(days=day))
    assert len(env.up.calls) == 1
    await env.measure(THU + timedelta(days=7))                         # a week later: ONE call
    assert len(env.up.calls) == 2
    await env.measure(THU + timedelta(days=8))
    assert len(env.up.calls) == 2


@pytest.mark.asyncio
async def test_the_upload_post_plan_pause_outlives_the_refused_post_leaving_the_30_day_listing(env):
    """The X flaw's twin (it predates the X back-off): the refused post — first in line, never read — leaves
    the 30-day listing the next day, and a pause kept only on it ended there. It now rides on the newest
    Upload-Post post of any platform, beside that post's measurement, and holds the week."""
    env.up_configured = True
    old = env.ledger.post("tiktok", published_at=THU - timedelta(days=29, hours=1))      # never read: first in line
    measured = {"v": 1, "rev": 2, "status": "ok", "measured_day": "2026-10-07", "note": None,
                "unavailable_until": None, "last": _entry("2026-10-07", views=10),
                "history": [_entry("2026-10-07", views=10)]}
    newest = env.ledger.post("instagram", published_at=THU - timedelta(days=2), metrics=measured)
    env.up.default = upload_post.UploadPostPlanError("upload-post get_post_analytics: HTTP 403", method="m", status=403)
    await env.measure(THU)
    assert env.up.calls == [("up-job-0001", "tiktok")]
    refused = env.ledger.metrics(old)
    assert refused["status"] == "unavailable" and refused["unavailable_until"] == "2026-10-15"
    assert "upload_post_plan_refused_until" not in refused
    assert env.ledger.metrics(newest) == {**measured, "rev": 3, "upload_post_plan_refused_until": "2026-10-15"}
    for day in range(1, 7):                    # the refused post is no longer listed from Fri 10-09
        await env.measure(THU + timedelta(days=day))
        fin = env.jobs.finishes[-1]
        assert fin["success"] is True and fin["error"] == "upload_post paused until 2026-10-15: a plan refusal"
    assert len(env.up.calls) == 1
    env.up.default = up_answer("instagram")
    await env.measure(THU + timedelta(days=7))                         # the back-off's day: read again
    assert len(env.up.calls) == 2 and env.ledger.metrics(newest)["status"] == "ok"
    assert env.jobs.finishes[-1]["error"] is None


@pytest.mark.asyncio
async def test_upload_post_reads_at_most_ten_posts_a_day(env):
    env.up_configured = True
    pids = [env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=i)) for i in range(12)]
    env.up.default = up_answer("tiktok")
    await env.measure(THU)
    assert len(env.up.calls) == 10
    unread = [p for p in pids if not env.ledger.metrics(p)]
    assert len(unread) == 2
    await env.measure(THU + timedelta(days=1))                         # the two never read come first
    assert len(env.up.calls) == 20
    assert all(env.ledger.metrics(p)["measured_day"] == "2026-10-09" for p in unread)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,recorded", [
    # About Upload-Post itself, never this post: nothing is recorded on it.
    (upload_post.UploadPostRateLimitError("upload-post: HTTP 429", method="m", status=429), False),
    (upload_post.UploadPostNotSentError("upload-post: not sent", method="m"), False),
    (upload_post.UploadPostAuthError("upload-post: HTTP 401", method="m", status=401), False),
    (upload_post.UploadPostReauthError("upload-post: HTTP 400 [account_reauth_required]", method="m", status=400),
     False),
    # About THIS post's read (an unknown outcome, or a bug of ours): recorded, so it rotates to the back.
    (upload_post.UploadPostAmbiguousError("upload-post: HTTP 502", method="m", status=502), True),
    (upload_post.UploadPostAmbiguousError("upload-post: HTTP 200 with success: false", method="m", status=200),
     True),
    (upload_post.UploadPostAmbiguousError("upload-post: outcome unknown (ReadTimeout)", method="m"), True),
    (RuntimeError("a bug in the fake"), True),
])
async def test_no_upload_post_failure_holds_the_day_open(env, error, recorded):
    env.up_configured = True
    pid = env.ledger.post("linkedin", published_at=THU - timedelta(days=2))
    env.ledger.post("linkedin", published_at=THU - timedelta(days=3))
    env.up.default = error
    counters = await env.measure(THU)
    assert env.jobs.finishes[-1]["success"] is True and counters["incomplete"] == 0
    assert len(env.up.calls) == 1
    if recorded:
        m = env.ledger.metrics(pid)
        assert m["status"] == "error" and m["note"].startswith(f"{type(error).__name__}: ") and "last" not in m
        assert m["measured_day"] == "2026-10-08" and counters["errors"] == 1
    else:
        assert env.ledger.metrics(pid) == {}
    # A refused credential stops Upload-Post for the day, and the job's note says so; a passing failure
    # (a rate limit, a request that never left, one post's unknown outcome) is not a stop.
    stopped = isinstance(error, (upload_post.UploadPostAuthError, upload_post.UploadPostReauthError))
    note = env.jobs.finishes[-1]["error"]
    if stopped:
        assert note == f"upload_post stopped for 2026-10-08: {type(error).__name__}: {error}"
    else:
        assert note is None


def _measured_on(ledger: Ledger, pid: str, day: date) -> bool:
    m = ledger.metrics(pid)
    return m.get("status") == "ok" and m.get("measured_day") == day.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", [0, 1, 2], ids=["oldest_fails", "middle_fails", "newest_fails"])
@pytest.mark.parametrize("error", [
    upload_post.UploadPostAmbiguousError("upload-post get_post_analytics: HTTP 500", method="m", status=500),
    upload_post.UploadPostAmbiguousError("upload-post get_post_analytics: HTTP 200 with success: false (Analytics "
                                         "are unavailable for this post)", method="m", status=200),
    upload_post.UploadPostAmbiguousError("upload-post get_post_analytics: HTTP 200 for another request_id "
                                         "('up-job-9999', asked up-job-0002)", method="m", status=200),
    RuntimeError("a bug in our own code"),
], ids=["5xx", "success_false", "another_request_id", "bug"])
async def test_one_failing_upload_post_read_never_starves_the_others(env, failing, error):
    """Review 2026-10-01 #8/#14: the failing post recorded nothing, so it kept the oldest `measured_day`,
    sorted first every day and stopped the tick — every other Upload-Post post went unmeasured for up to
    30 days while each day finished `success`."""
    env.up_configured = True
    pids = [env.ledger.post("tiktok", published_at=THU - timedelta(days=3 - i)) for i in range(3)]   # oldest first
    env.up.default = up_answer("tiktok")
    env.up.answers = {f"up-job-{failing + 1:04d}": error}
    healthy = [p for i, p in enumerate(pids) if i != failing]
    measured: Dict[str, List[int]] = {p: [] for p in healthy}
    for day in range(6):
        today = (THU + timedelta(days=day)).astimezone(ET).date()
        await env.measure(THU + timedelta(days=day))
        assert env.jobs.finishes[-1]["success"] is True
        for p in healthy:
            if _measured_on(env.ledger, p, today):
                measured[p].append(day)
    for p in healthy:                                         # read at least every other day, from day 0 or 1
        days = measured[p]
        assert days and days[0] <= 1 and days[-1] >= 4 and all(b - a <= 2 for a, b in zip(days, days[1:])), days
    bad = env.ledger.metrics(pids[failing])
    assert bad["status"] == "error" and "last" not in bad


@pytest.mark.asyncio
async def test_upload_post_is_skipped_when_not_configured(env):
    env.ledger.post("tiktok", published_at=THU - timedelta(days=2))
    await env.measure(THU)
    assert env.up.calls == [] and env.jobs.finishes[-1]["success"] is True


# the back-offs at the real posting cadence ───────────────────────────────────

#: Four weeks of one refusal, from Thu 2026-10-08 12:00 ET (re-review 2026-10-02 r1.0 / r2.1).
CADENCE_DAYS = 28


def _posting_times(days: int) -> List[datetime]:
    """16:00 ET on every posting day (Mon/Tue/Thu/Sat — `selection.is_posting_day`) from 35 days before
    THU's day to `days` after it, oldest first."""
    first = THU.astimezone(ET).date()
    out, d = [], first - timedelta(days=35)
    while d <= first + timedelta(days=days):
        if selection.is_posting_day(d):
            out.append(et(d.year, d.month, d.day, 16))
        d += timedelta(days=1)
    return out


def _x_read_reserves(env: Env) -> int:
    return sum(1 for row in env.ledger.rows for c in (row.get("metadata") or {}).get("charges") or []
               if c.get("op") == "x_metrics_read")


def _pause_claims(note: Optional[str], platform: str) -> List[date]:
    """Every "<platform> paused until YYYY-MM-DD" a job note makes ("x account read paused …" is not X's)."""
    return [date.fromisoformat(d) for d in
            re.findall(rf"(?<![a-z_]){platform} paused until ([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})", note or "")]


def _assert_pauses_kept(log: List[tuple], platform: str) -> None:
    """Every pause a job note names is KEPT. `log` is (ET day, paid calls, the job's note) per day, and
    every paid call in it was refused. No paid call falls on a day before a pause an earlier note named;
    a paid day's note names only the back-off its own refusal began, a week out (the refusal day is the
    one day a note says "paused until" and a call was paid: it reports the pause it starts); and every
    day inside a pause names that same pause."""
    announced: Optional[date] = None
    for day, paid, note in log:
        claims = _pause_claims(note, platform)
        if paid:
            assert announced is None or day >= announced, \
                f"{platform} paid on {day}, inside the pause until {announced}"
            assert claims == [day + timedelta(days=7)], (day, note)
        elif announced is not None and day < announced:
            assert claims == [announced], (day, note)
        if claims:
            announced = max(claims)


@pytest.mark.asyncio
async def test_a_persistent_x_refusal_at_the_real_posting_cadence_costs_one_reserve_a_week(env):
    """Re-review 2026-10-02 r1.0 / r2.1. The X back-off lived only on the refused post — always the OLDEST
    due one (the listing is oldest first): a post at its 28-day checkpoint, gone from the 30-day listing a
    day or two later, with the pause. At the real cadence a persistent 403 paid 16 reserves in 28 days —
    one every ~1.75 days — while the job note said "x paused until" a week out. The pause now also rides on
    the NEWEST X post, which stays listed for 30 days."""
    env.x_account_holder(THU)                  # a fresh follower snapshot: the account read is not due on day 0
    pending = _posting_times(CADENCE_DAYS)
    log: List[tuple] = []
    for k in range(CADENCE_DAYS):
        now = THU + timedelta(days=k)           # X refuses every read from Thu 10-08 12:00 ET on
        while pending and pending[0] <= now:
            published = pending.pop(0)
            # A healthy history: every checkpoint a post had crossed before the refusals began was measured.
            cps = ms.crossed_checkpoints(published, THU - timedelta(hours=1)) if published < THU else []
            env.ledger.post("x", published_at=published, metrics={"v": 1, "rev": 1, "cps": cps} if cps else None)
        env.x.errors = [_forbidden() for _ in range(3)]
        before = _x_read_reserves(env)
        await env.measure(now)
        fin = env.jobs.finishes[-1]
        assert fin["success"] is True
        log.append((now.astimezone(ET).date(), _x_read_reserves(env) - before, fin["error"]))
    paid = [(day.isoformat(), n) for day, n, _note in log if n]
    # Nothing is due on Thu 10-08 (no checkpoint crosses in the hour before noon); from Fri 10-09, ONE
    # refused read a week — never more than ceil(28 / 7) + 1 in four weeks.
    assert paid == [("2026-10-09", 1), ("2026-10-16", 1), ("2026-10-23", 1), ("2026-10-30", 1)]
    assert sum(n for _day, n in paid) <= math.ceil(CADENCE_DAYS / 7) + 1
    assert len(env.x.reads) == 4 and env.x.me_calls == 0            # the account read waited too
    _assert_pauses_kept(log, "x")
    # Each refused post was the oldest due one, at its 28-day checkpoint (published 29 days before its
    # refusal, so it left the 30-day listing inside its own pause), and still records `unavailable` for the
    # digest …
    refused = sorted(r["published_at"][:10] for r in env.ledger.rows
                     if (r.get("metrics") or {}).get("status") == "unavailable")
    assert refused == ["2026-09-10", "2026-09-17", "2026-09-24", "2026-10-01"]
    # … while each pause rode on the post that was the newest that day, never on its status.
    marked = sorted((r["published_at"][:10], r["metrics"]["x_reads_refused_until"]) for r in env.ledger.rows
                    if "x_reads_refused_until" in (r.get("metrics") or {}))
    assert marked == [("2026-10-08", "2026-10-16"), ("2026-10-15", "2026-10-23"), ("2026-10-22", "2026-10-30"),
                      ("2026-10-29", "2026-11-06")]
    assert all("status" not in r["metrics"] for r in env.ledger.rows
               if "x_reads_refused_until" in (r.get("metrics") or {}))


@pytest.mark.asyncio
async def test_a_persistent_upload_post_plan_refusal_at_the_real_posting_cadence_is_one_call_a_week(env):
    """The same shape for Upload-Post's plan pause (older than the X back-off, with the same flaw): the
    refused post is the least recently measured — here the oldest, read once the morning after it went
    out — and left the listing the next day; the next call went out inside the week the note named."""
    env.up_configured = True
    env.up.default = upload_post.UploadPostPlanError("upload-post get_post_analytics: HTTP 403", method="m", status=403)
    pending = _posting_times(CADENCE_DAYS)
    log: List[tuple] = []
    for k in range(CADENCE_DAYS):
        now = THU + timedelta(days=k)           # Upload-Post refuses every analytics read from Thu 10-08 on
        while pending and pending[0] <= now:
            published = pending.pop(0)
            seen = published + timedelta(days=1)
            history = ({"v": 1, "rev": 1, "status": "ok", "measured_day": seen.astimezone(ET).date().isoformat()}
                       if seen < THU else None)
            for platform in ("tiktok", "instagram", "youtube"):
                env.ledger.post(platform, published_at=published, metrics=history)
        before = len(env.up.calls)
        await env.measure(now)
        fin = env.jobs.finishes[-1]
        assert fin["success"] is True
        log.append((now.astimezone(ET).date(), len(env.up.calls) - before, fin["error"]))
    calls = [(day.isoformat(), n) for day, n, _note in log if n]
    assert calls == [("2026-10-08", 1), ("2026-10-15", 1), ("2026-10-22", 1), ("2026-10-29", 1)]
    assert sum(n for _day, n in calls) <= math.ceil(CADENCE_DAYS / 7) + 1
    _assert_pauses_kept(log, "upload_post")
    first_refused = min(r["published_at"] for r in env.ledger.rows
                        if (r.get("metrics") or {}).get("status") == "unavailable")
    assert first_refused[:10] == "2026-09-08"     # 29 days 20 hours old on day 0: gone from the listing on day 1


# budget, ledger failures, containment ────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_read_starts_after_the_120_second_budget_and_the_day_stays_open(env, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ms, "_clock", lambda: clock[0])
    pids = [env.ledger.post("bluesky", published_at=THU - timedelta(hours=40 + i)) for i in range(26)]
    env.bsky.views = {bsky_uri(i): bview(bsky_uri(i), likes=i) for i in range(1, 27)}

    def slow():
        clock[0] += ms.READ_BUDGET_SECONDS + 1

    env.bsky.on_call = slow
    counters = await env.measure(THU)
    assert len(env.bsky.calls) == 1 and counters["incomplete"] == 1
    assert env.jobs.finishes[-1]["success"] is False and "read_budget" in env.jobs.finishes[-1]["error"]
    assert sum(1 for p in pids if env.ledger.metrics(p)) == 25
    env.bsky.on_call = None
    await env.measure(THU + timedelta(minutes=10))
    # Oldest first: the one left over is the newest post, and only it is read now.
    assert env.bsky.calls[-1] == [bsky_uri(1)] and env.jobs.finishes[-1]["success"] is True
    assert all(env.ledger.metrics(p)["status"] == "ok" for p in pids)


@pytest.mark.asyncio
async def test_the_read_budget_also_stops_the_best_effort_reads_without_holding_the_day(env, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ms, "_clock", lambda: clock[0])
    env.up_configured = True
    ups = [env.ledger.post("threads", published_at=THU - timedelta(days=2, hours=i)) for i in range(3)]
    real_read = env.up.get_post_analytics

    async def slow_read(*a, **k):
        clock[0] += ms.READ_BUDGET_SECONDS + 1
        return await real_read(*a, **k)

    monkeypatch.setattr(upload_post, "get_post_analytics", slow_read)
    env.up.default = up_answer("threads")
    counters = await env.measure(THU)
    assert len(env.up.calls) == 1 and sum(1 for p in ups if env.ledger.metrics(p)) == 1
    assert counters["incomplete"] == 0 and env.jobs.finishes[-1]["success"] is True
    # The Bluesky follower read is best-effort the same way: no time left, no profile call, no hold.
    clock[0] = 0.0
    b = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(4): bview(bsky_uri(4), likes=1)}
    env.bsky.on_call = lambda: clock.__setitem__(0, clock[0] + ms.READ_BUDGET_SECONDS + 1)
    await env.measure(THU + timedelta(days=1))
    assert env.ledger.metrics(b)["status"] == "ok" and env.bsky.profile_calls == []
    assert env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_a_ledger_failure_listing_posts_leaves_the_day_open(env, monkeypatch, caplog):
    env.ledger.post("bluesky", published_at=THU - timedelta(days=1))

    async def broken(**_kw):
        raise mrs.MarketingRunError("list_measurable_posts failed (since=…): RuntimeError: PostgREST 503")

    monkeypatch.setattr(env.ledger.svc, "list_measurable_posts", broken)
    counters = await env.measure(THU)
    assert counters["incomplete"] == 1 and _no_platform_call(env)
    assert env.jobs.finishes[-1]["success"] is False and "PostgREST 503" in env.jobs.finishes[-1]["error"]


@pytest.mark.asyncio
async def test_a_failed_metrics_write_holds_the_day_and_never_stops_the_rows_behind_it(env, caplog):
    first = env.ledger.post("bluesky", published_at=THU - timedelta(days=2))
    second = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {u: bview(u, likes=1) for u in (bsky_uri(1), bsky_uri(2))}
    env.ledger.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    counters = await env.measure(THU)
    assert env.ledger.metrics(first) == {} and env.ledger.metrics(second)["status"] == "ok"
    assert counters["incomplete"] == 1 and env.jobs.finishes[-1]["success"] is False
    assert _messages(caplog, "NOT written", logging.ERROR)
    await env.measure(THU + timedelta(minutes=10))                     # the next tick fills the gap
    assert env.ledger.metrics(first)["status"] == "ok" and len(env.bsky.calls) == 2
    assert env.bsky.calls[-1] == [bsky_uri(1)]


@pytest.mark.asyncio
async def test_a_failing_ledger_stops_paid_reads_and_bounds_the_errors(env, caplog):
    """Writes keep failing: after LEDGER_FAILURES_MAX the run stops — no X read is paid for a result
    that could not be stored, and the error log stays bounded. The next tick recovers everything."""
    caplog.set_level(logging.INFO, logger=MS_LOGGER)
    bsky = [env.ledger.post("bluesky", published_at=THU - timedelta(days=2, hours=i)) for i in range(6)]
    env.bsky.views = {bsky_uri(i): bview(bsky_uri(i), likes=i) for i in range(1, 7)}
    env.x_account_holder(THU)
    x = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(x)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=3)}
    env.ledger.fake.tables[mrs.POSTS].fail_updates.extend(RuntimeError("PostgREST 503") for _ in range(10))
    counters = await env.measure(THU)
    assert len(env.bsky.calls) == 1 and env.x.reads == [] and env.ledger.journal(x) == []
    assert len(_messages(caplog, "NOT written", logging.ERROR)) == ms.LEDGER_FAILURES_MAX
    assert len(_messages(caplog, "ledger writes failed in this run", logging.ERROR)) == 1
    assert counters["incomplete"] == 1 and env.jobs.finishes[-1]["success"] is False
    assert all(env.ledger.metrics(p) == {} for p in bsky)
    env.ledger.fake.tables[mrs.POSTS].fail_updates.clear()
    await env.measure(THU + timedelta(minutes=10))
    assert all(env.ledger.metrics(p)["status"] == "ok" for p in bsky) and env.ledger.metrics(x)["status"] == "ok"
    assert env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_a_failing_ledger_under_upload_post_alone_never_holds_the_day(env):
    env.up_configured = True
    ups = [env.ledger.post("facebook", published_at=THU - timedelta(days=2, hours=i)) for i in range(4)]
    env.up.default = up_answer("facebook")
    env.ledger.fake.tables[mrs.POSTS].fail_updates.extend(RuntimeError("PostgREST 503") for _ in range(10))
    counters = await env.measure(THU)
    assert env.jobs.finishes[-1]["success"] is True and counters["incomplete"] == 0
    assert len(env.up.calls) == ms.LEDGER_FAILURES_MAX               # the breaker stops further reads
    assert all(env.ledger.metrics(p) == {} for p in ups)


@pytest.mark.asyncio
async def test_a_bug_in_one_platform_step_never_stops_the_others_or_raises(env, monkeypatch, caplog):
    b = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=1)}
    env.x_account_holder(THU)
    x = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(x)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}

    async def explode(_run, _rows):
        raise KeyError("a bug")

    monkeypatch.setattr(ms, "_measure_bluesky", explode)
    counters = await env.measure(THU)
    assert env.ledger.metrics(b) == {} and env.ledger.metrics(x)["status"] == "ok"
    assert counters["incomplete"] == 1 and env.jobs.finishes[-1]["success"] is False
    assert any("bluesky step FAILED" in r.getMessage() and r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_the_step_never_raises_even_when_the_job_runner_breaks(env, monkeypatch, caplog):
    async def broken(*_a, **_k):
        raise RuntimeError("runner bug")

    monkeypatch.setattr(ms, "run_day_job", broken)
    counters = await env.measure(THU)
    assert not any(counters.values())
    assert any("the step FAILED" in r.getMessage() and r.exc_info for r in caplog.records)


# the job's note: every stop and pause is named ───────────────────────────────


@pytest.mark.asyncio
async def test_every_stop_and_pause_is_named_in_the_jobs_last_error_even_on_a_day_that_succeeds(env):
    """Review 2026-10-01 #3: a definite refusal was a clean success — last_error NULL — so the digest
    showed nothing while a platform went unmeasured for weeks."""
    env.up_configured = True
    env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.errors = [bluesky.BlueskyRefusedError("bluesky getPosts: HTTP 403", method="m", status=403)]
    env.x_account_holder(THU)
    env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.x.errors = [_forbidden()]
    env.ledger.post("tiktok", published_at=THU - timedelta(days=2))
    env.up.default = upload_post.UploadPostPlanError("upload-post get_post_analytics: HTTP 403", method="m",
                                                     status=403)
    await env.measure(THU)
    fin = env.jobs.finishes[-1]
    x_note = "x paused until 2026-10-15: X refused the read: XApiForbiddenError: x list_user_posts_metrics: HTTP 403 Forbidden"
    up_note = "upload_post paused until 2026-10-15: plan: UploadPostPlanError: upload-post get_post_analytics: HTTP 403"
    assert fin["success"] is True and fin["error"].split("; ") == [
        "bluesky stopped for 2026-10-08: the AppView refused: BlueskyRefusedError: bluesky getPosts: HTTP 403",
        x_note, up_note]
    assert env.jobs.rows[ms.JOB_METRICS]["last_error"] == fin["error"]
    # The next day Bluesky is read again (its stop was for one day); X and Upload-Post are still paused —
    # and still named, on another clean success.
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=1)}
    await env.measure(THU + timedelta(days=1))
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True and fin["error"].split("; ") == [x_note, up_note]
    # The digest shows it as a note (its own cap keeps the front of the line: what is paused, until when).
    line = ds._metrics_job_line({"run_day": "2026-10-09", "last_run_at": (THU + timedelta(days=1)).isoformat(),
                                 "last_error": fin["error"], "items_written": fin["items"], "enabled": True}, True)
    assert " · note: x paused until 2026-10-15: X refused the read: XApiForbiddenError" in line


@pytest.mark.asyncio
async def test_a_later_run_the_same_day_still_names_the_stop(env):
    """The day held open by another platform: the next run skips the stopped platform AND names it again,
    or that run's success would overwrite the note with NULL."""
    env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    env.bsky.errors = [bluesky.BlueskyRateLimitError("bluesky getPosts: HTTP 429", method="m", status=429)]
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.x.errors = [x_api.XApiCreditsDepletedError("x list_user_posts_metrics: HTTP 402", method="m", status=402)]
    await env.measure(THU)
    stop = (f"x stopped for 2026-10-08: read of post_id={pid}: XApiCreditsDepletedError: "
            "x list_user_posts_metrics: HTTP 402 (refunded)")
    fin = env.jobs.finishes[-1]
    assert fin["success"] is False and fin["error"] == f"incomplete: bluesky; {stop}"
    env.bsky.views = {bsky_uri(1): bview(bsky_uri(1), likes=1)}
    await env.measure(THU + timedelta(minutes=10))
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True and fin["error"] == stop and len(env.x.reads) == 1
    # A new ET day forgets it: no credits is no back-off, X is read again.
    await env.measure(THU + timedelta(days=1))
    assert len(env.x.reads) == 2 and env.jobs.finishes[-1]["error"] is None


@pytest.mark.asyncio
async def test_a_hostile_refusal_text_reaches_the_note_and_the_log_scrubbed_and_on_one_line(env, caplog):
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    env.x.errors = [x_api.XApiForbiddenError(
        "x list_user_posts_metrics: HTTP 403 Forbidden\r\nBearer abcdefghijklmnop0123456789 token=supersecretvalue"
        + chr(0x2028) + "forged line " + "y" * 600, method="m", status=403)]
    await env.measure(THU)
    logged = [r.getMessage() for r in caplog.records if r.name == MS_LOGGER]
    assert any("reads STOPPED" in line for line in logged)
    for text in (env.jobs.finishes[-1]["error"], env.ledger.metrics(pid)["note"], *logged):
        assert "supersecretvalue" not in text and "abcdefghijklmnop0123456789" not in text
        assert not any(ch in text for ch in ("\r", "\n", chr(0x2028)))
    assert len(env.ledger.metrics(pid)["note"]) <= ms.NOTE_MAX
    assert len(env.jobs.finishes[-1]["error"]) <= ms.NOTE_MAX


# the real clients on the wire ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_x_read_on_the_wire_asks_our_window_and_stores_the_counts(env, monkeypatch):
    for name, value in X_CREDS.items():
        monkeypatch.setattr(x_api.settings, name, value)
    monkeypatch.setattr(x_api, "list_user_posts_metrics", REAL_X_METRICS)
    env.x_account_holder(THU)
    published = THU - timedelta(days=7, hours=3)
    pid = env.ledger.post("x", published_at=published)
    ext = env.ledger.raw(pid)["external_id"]
    wire = x_wire_install(monkeypatch, x_wire_answer(200, {
        "data": [{"id": ext, "text": "Lesson", "created_at": published.isoformat(),
                  "public_metrics": {"retweet_count": 2, "reply_count": 1, "like_count": 11, "quote_count": 0,
                                     "bookmark_count": 4, "impression_count": 812}}],
        "meta": {"result_count": 1}}))
    await env.measure(THU)
    (req,) = wire.requests
    sent = dict(parse_qsl(req.url.query.decode()))
    center = ms.x_post_time(ext)
    assert req.url.path == f"/2/users/{X_USER}/tweets"
    assert sent["start_time"] == (center - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert sent["end_time"] == (center + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert sent["max_results"] == "5" and sent["tweet.fields"] == "created_at,public_metrics"
    last = env.ledger.metrics(pid)["last"]
    assert {k: v for k, v in last.items() if k in ms.COUNT_KEYS} == {
        "likes": 11, "reposts": 2, "replies": 1, "quotes": 0, "bookmarks": 4, "impressions": 812}
    assert last["cp"] == 7
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE), ("x_metrics_read_correction", 1_000 - RESERVE)]


@pytest.mark.asyncio
async def test_an_errors_only_200_on_the_wire_is_an_error_on_the_post_never_a_deletion(env, monkeypatch):
    """The real client (`x_api.list_user_posts_metrics`) and the measure step agree on the `problem` key:
    X's partial-error 200 never marks a post deleted or uses up its checkpoints."""
    for name, value in X_CREDS.items():
        monkeypatch.setattr(x_api.settings, name, value)
    monkeypatch.setattr(x_api, "list_user_posts_metrics", REAL_X_METRICS)
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=3, hours=2))
    wire = x_wire_install(monkeypatch, x_wire_answer(200, {"errors": [{
        "detail": "User has been suspended: [1234567890].\nnext line", "title": "Forbidden",
        "type": "https://api.twitter.com/2/problems/resource-not-found"}]}))
    await env.measure(THU)
    assert len(wire.requests) == 1
    m = env.ledger.metrics(pid)
    assert m["status"] == "error" and m["cps"] == [] and "last" not in m
    assert m["note"].startswith("x list_user_posts_metrics: HTTP 200 Forbidden") and "suspended" in m["note"]
    assert "\n" not in m["note"]
    assert env.ledger.journal(pid) == [("x_metrics_read", RESERVE), ("x_metrics_read_correction", -RESERVE)]
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True and fin["error"].startswith("x stopped for 2026-10-08: x list_user_posts_metrics")


@pytest.mark.asyncio
async def test_the_bluesky_read_on_the_wire_goes_to_the_public_appview_without_a_credential(env, monkeypatch):
    import httpx

    monkeypatch.setattr(bluesky, "get_posts", REAL_GET_POSTS)
    monkeypatch.setattr(bluesky, "get_profile", REAL_GET_PROFILE)
    monkeypatch.setattr(bluesky.settings, "MARKETING_BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")
    wire = WireBluesky()
    monkeypatch.setattr(bluesky, "_client", httpx.AsyncClient(transport=httpx.MockTransport(wire.handler)))
    pid = env.ledger.post("bluesky", published_at=THU - timedelta(days=1))
    wire.answers = [
        (200, {"posts": [{"uri": bsky_uri(1), "cid": "bafy", "author": {"did": DID}, "record": {"text": "x"},
                          "indexedAt": "2026-10-07T16:00:00.000Z", "likeCount": 6, "repostCount": 1}]}),
        (200, {"did": DID, "handle": "caydex.bsky.social", "followersCount": 77, "followsCount": 3,
               "postsCount": 12}),
    ]
    await env.measure(THU)
    assert [r.url.host for r in wire.requests] == ["public.api.bsky.app", "public.api.bsky.app"]
    assert all("authorization" not in r.headers for r in wire.requests)
    assert wire.requests[0].url.path == "/xrpc/app.bsky.feed.getPosts"
    assert parse_qsl(wire.requests[0].url.query.decode()) == [("uris", bsky_uri(1))]
    m = env.ledger.metrics(pid)
    assert {k: v for k, v in m["last"].items() if k in ms.COUNT_KEYS} == {"likes": 6, "reposts": 1}
    assert m["account"]["followers"] == 77 and m["account"]["source"] == "bluesky_appview"


# ── digest and run health ─────────────────────────────────────────────────────

SAT = date(2026, 10, 3)       # a posting day
FRI = date(2026, 10, 2)       # a rest day
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)   # Mon 10:00 EDT — the digest is due
WEEK_START, WEEK_END = date(2026, 9, 28), date(2026, 10, 4)
CAPTION = "CAPTION-SENTINEL 🚀 model text that must never reach a digest"
TITLE = "TITLE-SENTINEL"
#: Telegram-bot-token shaped (the redactor keeps the bot id and drops the secret half).
TOKEN_SECRET = "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
TOKEN_LIKE = f"1234567890:{TOKEN_SECRET}"
X_ID = "1840000000000000001"


def _et(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(timezone.utc)


def _post(platform: str = "x", status: str = "published", *, run_date: str = "2026-10-03",
          created: Optional[str] = None, meta: Optional[Dict[str, Any]] = None,
          metrics: Optional[Dict[str, Any]] = None, live: bool = True, **cols: Any) -> Dict[str, Any]:
    """A `marketing_posts` row as the ledger holds it (a live row unless `live=False`)."""
    metadata: Dict[str, Any] = {"dry_run": not live}
    metadata.update(meta or {})
    created = created or f"{run_date}T20:30:00+00:00"
    row: Dict[str, Any] = {
        "id": str(uuid.uuid4()), "run_id": "11111111-1111-4111-8111-111111111111", "platform": platform,
        "format": "text", "status": status, "title": TITLE, "caption": CAPTION, "asset_ids": [],
        "idempotency_key": f"{run_date}:{platform}:text", "external_id": None, "external_url": None,
        "attempts": 1, "last_error": None, "cost_micros": 0,
        "metrics": metrics if metrics is not None else {}, "metadata": metadata,
        "created_at": created, "updated_at": created, "published_at": None,
    }
    row.update(cols)
    return row


def _run(status: str, *, run_date: str = "2026-10-03", **kw: Any) -> Dict[str, Any]:
    return {"id": str(uuid.uuid4()), "run_date": run_date, "status": status,
            "stage": kw.pop("stage", "assets_ready"), "attempts": kw.pop("attempts", 1),
            "metadata": kw.pop("metadata", {}), "timings": kw.pop("timings", {}),
            "last_error": kw.pop("last_error", None), **kw}


#: The 22:00 ET check of SAT, and the production knobs it is judged with.
SAT_22 = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)      # Sat 2026-10-03 22:00 EDT
HEALTH_MAX_ATTEMPTS = 6
HEALTH_STALE_SECONDS = 2700


def _health(run, *, run_date: date = SAT, posting_day: bool = True, now: datetime = SAT_22,
            max_attempts: Any = HEALTH_MAX_ATTEMPTS, stale_seconds: Any = HEALTH_STALE_SECONDS) -> Optional[str]:
    return ds.evaluate_run_health(run, run_date=run_date, posting_day=posting_day, now=now,
                                  max_attempts=max_attempts, stale_seconds=stale_seconds)


@pytest.fixture(autouse=True)
def _declared_run_health_hours(monkeypatch):
    """The run-health hours are read from settings at call time (MARKETING_RUN_HOUR_ET, the worker's run
    hour mirrored on the web, and MARKETING_MAX_RUN_ATTEMPTS): pin both to their DECLARED defaults, so a
    developer's .env can never move every expected 22:00 / 16:00. A test that wants another hour sets its
    own (`_hours`). Neutral for the measure step, which reads neither."""
    fields = type(settings).model_fields
    for name in ("MARKETING_RUN_HOUR_ET", "MARKETING_MAX_RUN_ATTEMPTS"):
        monkeypatch.setattr(settings, name, fields[name].default)


def _final_word(run, *, run_date: date = SAT, posting_day: bool = True,
                now: datetime = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc),   # Sun 10-04 16:00 EDT
                max_attempts: Any = HEALTH_MAX_ATTEMPTS,
                stale_seconds: Any = HEALTH_STALE_SECONDS, checked_at: Any = SAT_22) -> Optional[str]:
    """The final word after a nightly check that judged the run at `checked_at` (default: 22:00:00 sharp
    on SAT; None = the check never went out that day)."""
    return ds.evaluate_run_final(run, run_date=run_date, posting_day=posting_day, now=now,
                                 max_attempts=max_attempts, stale_seconds=stale_seconds, checked_at=checked_at)


def _iso_of(when: datetime) -> str:
    return when.isoformat()


# ── run health (pure) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("run", [None, _run("failed"), _run("in_progress"),
                                 _run("skipped", metadata={"skip_reason": "writer_unavailable"}),
                                 _run("media_ready", timings={"rendered_cgroup_peak_mb": 3900.0})])
def test_a_rest_day_never_alerts_whatever_its_run(run):
    assert _health(run, run_date=FRI, posting_day=False) is None


def test_a_posting_day_with_no_run_says_the_worker_never_claimed_it():
    msg = _health(None)
    assert msg is not None and "no run for Sat 2026-10-03" in msg
    assert "never claimed" in msg and "cron" in msg


def test_a_failed_run_at_the_cap_names_the_last_completed_stage_and_says_the_day_is_lost():
    # The voice stage died (kokoro is the voice stage), so the LAST COMPLETED stage is `scripted`.
    msg = _health(_run("failed", stage="scripted", attempts=6, last_error="kokoro child exited 137"))
    assert msg == ("⚠️ Marketing run Sat 2026-10-03 FAILED after stage scripted (the last completed stage): all 6 "
                   "attempts used; this day's posts will not go out. Last error: kokoro child exited 137. Check the "
                   "marketing-worker logs.")
    assert "at stage" not in msg
    # past the cap (the cap was lowered after the attempts were spent) is still "all used"
    assert "all 6 attempts used" in _health(_run("failed", attempts=9))
    none = _health(_run("failed", stage="scripted", attempts=6, last_error=None))
    assert "Last error: (none recorded)." in none


@pytest.mark.parametrize("attempts", [1, 5])
def test_a_failed_run_below_the_cap_says_the_worker_retries_and_a_final_word_follows(attempts):
    msg = _health(_run("failed", stage="voiced", attempts=attempts, last_error="ffmpeg exited 137"))
    assert msg == (f"⚠️ Marketing run Sat 2026-10-03 FAILED after stage voiced (the last completed stage): attempt "
                   f"{attempts} of 6; the worker retries hourly until midnight ET and resumes it on tomorrow's early "
                   f"ticks — a final word comes tomorrow after 16:00 ET. Last error: ffmpeg exited 137. Check the "
                   f"marketing-worker logs.")
    assert "will not go out" not in msg


def test_the_cap_is_the_settings_value_and_a_disabled_cap_never_says_all_used():
    assert "attempt 5 of 8; the worker retries" in _health(_run("failed", attempts=5), max_attempts=8)
    assert "all 3 attempts used" in _health(_run("failed", attempts=5), max_attempts=3)
    for off in (0, -1, None, True, "6", 6.0):
        msg = _health(_run("failed", attempts=50), max_attempts=off)
        assert "attempt 50 (no attempts cap); the worker retries hourly" in msg and "will not go out" not in msg


def test_a_failure_before_any_stage_completed_says_so():
    msg = _health(_run("failed", stage="planned", attempts=6, last_error="preflight 503"))
    assert msg.startswith("⚠️ Marketing run Sat 2026-10-03 FAILED before any stage completed: all 6 attempts used")
    assert "after stage ? (the last completed stage)" in _health(_run("failed", stage="no\nsuch", attempts=6))


def test_a_hostile_last_error_is_scrubbed_folded_onto_one_line_and_capped():
    hostile = ("Traceback (most recent call last):\n  bot " + TOKEN_LIKE + "\r\nAuthorization: Bearer "
               + "b" * 40 + " \u2028forged line\u202e\x00\ud83d " + "Z" * 10_000)
    msg = _health(_run("failed", stage="rendered", attempts=2, last_error=hostile))
    assert TOKEN_SECRET not in msg and "b" * 40 not in msg
    for bad in ("\n", "\r", "\u2028", "\u202e", "\x00", "\ud83d"):
        assert bad not in msg
    assert "Z" * 400 not in msg and len(msg) < 600
    assert "Last error: Traceback (most recent call last): bot 1234567890:***" in msg
    msg.encode("utf-16-le")   # encodable: no lone surrogate survived


@pytest.mark.parametrize("reason, needle", [
    ("writer_unavailable", "Gemini API key"),
    ("content_rejected", "marketing_scripts"),
    ("judge_not_enforced", "MARKETING_JUDGE_MODE=enforce"),
    ("empty_pool", "content pool"),
    ("source_ineligible", "content_pool"),
    ("empty_narration", "voice stage"),
    ("narration_too_long", "voice stage"),
    ("unrenderable_text", "render stage"),
])
def test_each_known_skip_reason_says_where_to_look(reason, needle):
    msg = _health(_run("skipped", stage="selected", metadata={"skip_reason": reason}))
    assert msg is not None and f"SKIPPED ({reason})" in msg and needle in msg


def test_a_rejected_day_names_the_run_whose_draft_to_read():
    run_id = "6f1c2a9e-0b7d-4c43-9a51-2f8e4d7b1c00"
    msg = _health(_run("skipped", id=run_id.upper(), metadata={"skip_reason": "content_rejected"}))
    assert msg.endswith(f"in marketing_scripts for this run (run_id {run_id}).")
    hostile = _health(_run("skipped", id="x\n" + TOKEN_LIKE, metadata={"skip_reason": "content_rejected"}))
    assert hostile.endswith("in marketing_scripts for this run.") and TOKEN_SECRET not in hostile


def test_a_rest_day_skip_on_a_posting_day_is_not_an_alert():
    assert _health(_run("skipped", metadata={"skip_reason": "rest_day"})) is None


def test_an_unknown_skip_reason_is_said_plainly():
    msg = _health(_run("skipped", metadata={"skip_reason": "cosmic_rays"}, attempts=2, stage="selected"))
    assert msg == ("⚠️ Marketing run Sat 2026-10-03 was SKIPPED for an unrecognised reason: cosmic_rays — after "
                   "stage selected (the last completed stage), attempt 2 of 6. Check the marketing-worker logs.")


def test_a_hostile_skip_reason_is_shown_as_cleaned_data_only():
    msg = _health(_run("skipped", metadata={"skip_reason": "evil\nreason " + TOKEN_LIKE + " " + "q" * 500}))
    assert "\n" not in msg and TOKEN_SECRET not in msg and "q" * 100 not in msg
    assert "unrecognised reason: evil reason 1234567890:***" in msg


@pytest.mark.parametrize("meta", [{}, {"skip_reason": None}, {"skip_reason": ""}, "not a dict", None])
def test_a_skip_without_a_reason_says_so(meta):
    msg = _health(_run("skipped", metadata=meta))
    assert msg is not None and "no reason recorded" in msg


def test_a_skip_carries_its_last_error_when_there_is_one():
    msg = _health(_run("skipped", metadata={"skip_reason": "writer_unavailable"}, last_error="429 quota"))
    assert msg.endswith("Last error: 429 quota.")


@pytest.mark.parametrize("status", ["planned", "in_progress"])
def test_a_live_unfinished_run_is_still_running(status):
    """Touched 10 minutes before the check (a stage heartbeat): the attempt is alive."""
    msg = _health(_run(status, stage="scripted", attempts=4, last_error="writer deferred",
                       started_at=_iso_of(SAT_22 - timedelta(minutes=50)), updated_at=_iso_of(SAT_22 - timedelta(minutes=10))))
    assert msg == (f"⏳ Marketing run Sat 2026-10-03 is not finished yet: {status} after stage scripted (the last "
                   f"completed stage) — still running (attempt 4 of 6); last error: writer deferred. A final word "
                   f"comes tomorrow after 16:00 ET.")


@pytest.mark.parametrize("status", ["planned", "in_progress"])
def test_an_abandoned_run_below_the_cap_is_retried_hourly(status):
    msg = _health(_run(status, stage="voiced", attempts=3, updated_at=_iso_of(SAT_22 - timedelta(hours=1))))
    assert msg == (f"⚠️ Marketing run Sat 2026-10-03 is not finished yet: {status} after stage voiced (the last "
                   f"completed stage) — attempt 3 of 6 was abandoned; the worker retries hourly. A final word comes "
                   f"tomorrow after 16:00 ET.")


def test_an_abandoned_last_attempt_says_no_retries_remain():
    """#4: the 6th attempt, claimed at 21:15 and OOM-killed at ~21:40 — never 'keeps retrying'."""
    msg = _health(_run("in_progress", stage="voiced", attempts=6, started_at=_iso_of(SAT_22 - timedelta(minutes=45)),
                       updated_at=_iso_of(SAT_22 - timedelta(minutes=46))))
    assert ("— the last allowed attempt was abandoned; no retries remain — the day will close failed. A final "
            "word comes tomorrow after 16:00 ET.") in msg
    assert "retries hourly" not in msg and "keeps retrying" not in msg
    # …while the same last attempt, still alive, is just running
    alive = _health(_run("in_progress", stage="voiced", attempts=6, updated_at=_iso_of(SAT_22 - timedelta(minutes=2))))
    assert "still running (attempt 6 of 6)" in alive and "abandoned" not in alive


def test_liveness_is_decide_claims_the_later_of_started_and_updated_at_strictly_inside_the_window():
    window = timedelta(seconds=HEALTH_STALE_SECONDS)
    edge = _run("in_progress", attempts=2, updated_at=_iso_of(SAT_22 - window))
    assert "attempt 2 of 6 was abandoned" in _health(edge)                       # exactly the window: stale
    inside = _run("in_progress", attempts=2, updated_at=_iso_of(SAT_22 - window + timedelta(seconds=1)))
    assert "still running" in _health(inside)
    # a fresh re-claim (started_at) keeps a row whose updated_at is old alive, as in decide_claim
    reclaimed = _run("in_progress", attempts=2, started_at=_iso_of(SAT_22 - timedelta(minutes=5)),
                     updated_at=_iso_of(SAT_22 - timedelta(hours=3)))
    assert "still running" in _health(reclaimed)
    assert mrs.decide_claim(reclaimed, now=SAT_22, stale_seconds=HEALTH_STALE_SECONDS,
                            max_attempts=HEALTH_MAX_ATTEMPTS) == mrs.IN_PROGRESS
    assert mrs.decide_claim(edge, now=SAT_22, stale_seconds=HEALTH_STALE_SECONDS, max_attempts=HEALTH_MAX_ATTEMPTS) == mrs.CLAIMED


@pytest.mark.parametrize("touch", [None, "", "not a time", 12345, ["2026-10-03T21:59:00+00:00"]])
def test_an_unreadable_liveness_is_stale_as_in_decide_claim(touch):
    msg = _health(_run("in_progress", attempts=2, started_at=touch, updated_at=touch))
    assert "attempt 2 of 6 was abandoned; the worker retries hourly" in msg


def test_a_far_future_liveness_counts_as_alive_as_in_decide_claim():
    future = _run("in_progress", attempts=6, updated_at="9999-12-31T23:59:59-14:00")
    assert "still running (attempt 6 of 6)" in _health(future)
    assert mrs.decide_claim(future, now=SAT_22, stale_seconds=HEALTH_STALE_SECONDS,
                            max_attempts=HEALTH_MAX_ATTEMPTS) == mrs.IN_PROGRESS


@pytest.mark.parametrize("stale_seconds", [None, "2700", 2700.0, True])
def test_an_unreadable_stale_window_never_calls_a_run_alive(stale_seconds):
    msg = _health(_run("in_progress", attempts=2, updated_at=_iso_of(SAT_22 - timedelta(seconds=5))),
                  stale_seconds=stale_seconds)
    assert "was abandoned" in msg and "still running" not in msg


@pytest.mark.parametrize("status", ["media_ready", "published"])
def test_a_finished_run_is_silent_unless_a_stage_ran_out_of_memory_headroom(status):
    calm = _run(status, timings={"voiced_cgroup_peak_mb": 2587.3, "rendered_cgroup_peak_mb": 3200.0})
    assert _health(calm) is None   # exactly the threshold is not above it
    hot = _run(status, timings={"voiced_cgroup_peak_mb": 2587.3, "rendered_cgroup_peak_mb": 3412.6,
                                "rendered_self_maxrss_mb": 9999.0})
    msg = _health(hot)
    assert msg is not None and "\n" not in msg
    assert "stage rendered peaked at 3,413 MB" in msg and "above 3,200 MB" in msg


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "9999", -5, 10 ** 9,
                                   None, [3500], {"mb": 3500}])
def test_an_unreadable_memory_peak_never_alerts(value):
    assert _health(_run("media_ready", timings={"rendered_cgroup_peak_mb": value})) is None


@pytest.mark.parametrize("timings", [None, "3500", [], {"cgroup_peak_mb": 3500.0}, {3500: 3500.0}])
def test_malformed_timings_never_crash_or_alert(timings):
    assert _health(_run("media_ready", timings=timings)) is None


def test_a_failure_with_a_memory_peak_gets_the_memory_line_too():
    msg = _health(_run("failed", stage="voiced", attempts=6, last_error="OOM",
                       timings={"voiced_cgroup_peak_mb": 3790.0}))
    first, second = msg.split("\n")
    assert first.startswith("⚠️ Marketing run Sat 2026-10-03 FAILED") and "3,790 MB" in second


@pytest.mark.parametrize("attempts", [True, "6", -1, None, 2.0, float("nan"), 10 ** 15])
def test_unreadable_attempts_are_a_question_mark_and_promise_nothing(attempts):
    msg = _health(_run("failed", attempts=attempts))
    assert "attempt ? of 6 — the attempt count is unreadable, so whether the worker retries is unknown" in msg
    assert "will not go out" not in msg and "retries hourly" not in msg
    stale = _health(_run("in_progress", attempts=attempts))
    assert "attempt ? of 6 was abandoned; the attempt count is unreadable" in stale
    assert "no retries remain" not in stale and "retries hourly" not in stale


def test_an_unexpected_status_is_named_and_a_hostile_one_cleaned():
    assert "unexpected status: archived — after stage assets_ready (the last completed stage), attempt 1 of 6" in \
        _health(_run("archived"))
    msg = _health(_run("x\ny " + TOKEN_LIKE))
    assert "\n" not in msg and TOKEN_SECRET not in msg and "unexpected status" in msg
    assert "unexpected status: ? —" in _health(_run(None))      # never the string "None"


def test_a_run_that_is_not_an_object_reads_as_no_run():
    assert "no run" in _health(["not", "a", "run"])


@pytest.mark.parametrize("run_hour, attempts", [(18, 6), (9, 2)])
def test_the_messages_name_the_configured_hours(monkeypatch, run_hour, attempts):
    """Every hour a message names follows MARKETING_RUN_HOUR_ET (the defaults' 16:00 / 16:15 never leak)."""
    _hours(monkeypatch, run_hour, attempts)
    hour = f"{run_hour:02d}:00 ET"
    texts = [
        _health(None),
        _health(_run("failed", stage="voiced", attempts=1)),
        _health(_run("in_progress", attempts=1, updated_at=_iso_of(SAT_22 - timedelta(minutes=1)))),
        _final_word(_run("failed", attempts=1, updated_at=_iso_of(AFTER_22))),
        _final_word(None, checked_at=None),
    ]
    assert f"Check the marketing-worker cron on Railway (hourly from {run_hour:02d}:15 ET)" in texts[0]
    assert f"resumes it on tomorrow's early ticks — a final word comes tomorrow after {hour}" in texts[1]
    assert texts[2].endswith(f"A final word comes tomorrow after {hour}.")
    assert f"no retry is left (the worker resumes a day only until {hour} the next day)" in texts[3]
    assert f"(hourly from {run_hour:02d}:15 ET)" in texts[4]
    for text in texts:
        assert "16:00" not in text and "16:15" not in text, text


def test_with_run_hour_23_a_missing_run_never_blames_a_nightly_check_that_does_not_exist(monkeypatch):
    _hours(monkeypatch, 23, 6)
    msg = _final_word(None, checked_at=None, now=_et(2026, 10, 4, 23, 0))
    assert msg == ("⚠️ Marketing: no run for Sat 2026-10-03, a posting day — the worker never claimed it, so nothing "
                   "was posted for this day (run hour 23 has no nightly check). Check the marketing-worker cron on "
                   "Railway (hourly from 23:15 ET) and its last logs.")


def test_with_run_hour_0_no_message_promises_an_early_tick_tomorrow(monkeypatch):
    """Run hour 0: every tick of the day is in the worker's window, so nothing resumes a day after midnight."""
    _hours(monkeypatch, 0, 6)
    msg = _health(_run("failed", stage="voiced", attempts=1))
    assert ("attempt 1 of 6; the worker retries hourly until midnight ET — a final word comes tomorrow after "
            "00:00 ET") in msg
    assert "early ticks" not in msg


# ── the next day's final word (pure) ──────────────────────────────────────────
#
# SAT's run is judged again on Sunday from 16:00 ET, against WHEN the nightly check judged it
# (`checked_at`). Unless a test says otherwise that is Sat 22:00:00 EDT = 2026-10-04 02:00 UTC sharp.

BEFORE_22 = SAT_22 - timedelta(minutes=20)          # Sat 21:40 EDT
AFTER_22 = SAT_22 + timedelta(minutes=80)           # Sat 23:20 EDT
SUN_EARLY = datetime(2026, 10, 4, 7, 30, tzinfo=timezone.utc)    # Sun 03:30 EDT, a resume tick's write
SUN_16 = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)       # Sun 16:00 EDT


def test_the_nightly_window_opens_at_22_et_on_the_run_date_across_dst():
    """`health_opens_at` — the earliest the nightly check can have judged a run (the final word's check
    time only when its record says THAT it succeeded but not when) — follows the New York clock."""
    assert ds.health_opens_at(SAT) == SAT_22
    assert ds.health_opens_at(date(2026, 10, 31)) == datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)   # EDT
    assert ds.health_opens_at(date(2026, 11, 2)) == datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)    # EST


@pytest.mark.parametrize("status", ["media_ready", "published"])
def test_a_run_that_finished_after_the_check_recovered(status):
    msg = _final_word(_run(status, finished_at=_iso_of(AFTER_22), updated_at=_iso_of(AFTER_22)))
    what = "its posts are waiting for review" if status == "media_ready" else "its posts went out"
    assert msg == f"✅ Marketing run Sat 2026-10-03 recovered at 10-03 23:20 ET — {what}."


def test_a_recovery_on_the_next_mornings_resume_tick_is_dated_that_morning():
    assert "recovered at 10-04 03:30 ET" in _final_word(_run("media_ready", finished_at=_iso_of(SUN_EARLY)))


@pytest.mark.parametrize("finished", [BEFORE_22, SAT_22])
def test_a_run_that_was_fine_at_22_gets_no_final_word(finished):
    assert _final_word(_run("media_ready", finished_at=_iso_of(finished), updated_at=_iso_of(finished))) is None
    # a later write to the row (no finish) does not make it a recovery
    assert _final_word(_run("media_ready", finished_at=_iso_of(finished), updated_at=_iso_of(AFTER_22))) is None


def test_the_finish_time_falls_back_to_updated_at_and_is_never_guessed():
    assert "recovered at 10-03 23:20 ET" in _final_word(_run("media_ready", finished_at=None, updated_at=_iso_of(AFTER_22)))
    assert "recovered at 10-03 23:20 ET" in _final_word(_run("media_ready", finished_at="junk", updated_at=_iso_of(AFTER_22)))
    # a finish time in the future is a clock error: the fallback decides, and with none there is no word
    future = "2027-01-01T00:00:00+00:00"
    assert "recovered at 10-03 23:20 ET" in _final_word(_run("media_ready", finished_at=future, updated_at=_iso_of(AFTER_22)))
    assert _final_word(_run("media_ready", finished_at=future, updated_at=future)) is None
    assert _final_word(_run("media_ready", finished_at=None, updated_at=None)) is None


def test_a_recovered_run_carries_a_new_memory_peak():
    msg = _final_word(_run("media_ready", finished_at=_iso_of(AFTER_22), timings={"rendered_cgroup_peak_mb": 3500.0}))
    first, second = msg.split("\n")
    assert first.startswith("✅ Marketing run Sat 2026-10-03 recovered") and "3,500 MB" in second


def test_no_run_or_a_rest_day_has_no_final_word():
    assert _final_word(None) is None                      # the 22:00 "no run" alert was already final
    assert _final_word(["junk"]) is None
    assert _final_word(_run("failed", attempts=2), run_date=FRI, posting_day=False) is None


# changed after the 22:00 check: one terminal line on where it ended


def test_a_run_retried_after_the_check_that_ended_failed_at_the_cap():
    msg = _final_word(_run("failed", stage="voiced", attempts=6, last_error="ffmpeg exited 137",
                      updated_at=_iso_of(AFTER_22)))
    assert msg == ("⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage voiced (the last completed stage): all 6 "
                   "attempts used. This day's posts will not go out. Last error: ffmpeg exited 137.")


def test_a_run_that_ended_failed_below_the_cap_has_no_retry_left_either():
    msg = _final_word(_run("failed", stage="selected", attempts=4, last_error=None, updated_at=_iso_of(SUN_EARLY)))
    assert msg == ("⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage selected (the last completed stage): "
                   "attempt 4 of 6; no retry is left (the worker resumes a day only until 16:00 ET the next day). "
                   "This day's posts will not go out. Last error: (none recorded).")
    assert "retries hourly" not in msg


def test_a_run_that_ended_skipped_after_the_check_says_why():
    msg = _final_word(_run("skipped", metadata={"skip_reason": "content_rejected"}, updated_at=_iso_of(AFTER_22)))
    assert msg.startswith("⚠️ Marketing run Sat 2026-10-03 was SKIPPED (content_rejected): every draft was rejected")
    assert _final_word(_run("skipped", metadata={"skip_reason": "rest_day"}, updated_at=_iso_of(AFTER_22))) is None


def test_a_run_resumed_after_the_check_and_still_running():
    now = SUN_16 + timedelta(minutes=5)
    msg = _final_word(_run("in_progress", stage="voiced", attempts=5, updated_at=_iso_of(now - timedelta(minutes=3))),
                 now=now)
    assert msg == ("⏳ Marketing run Sat 2026-10-03 is still running after stage voiced (the last completed stage), "
                   "attempt 5 of 6, resumed after the 22:00 ET check — if this attempt fails, no retry is left (the "
                   "worker resumes a day only until 16:00 ET the next day).")


@pytest.mark.parametrize("status", ["planned", "in_progress"])
def test_a_run_abandoned_after_the_check_never_finished(status):
    msg = _final_word(_run(status, stage="rendered", attempts=3, updated_at=_iso_of(SUN_EARLY)))
    assert msg == (f"⚠️ Marketing run Sat 2026-10-03 never finished — the day is lost: {status} after stage rendered "
                   f"(the last completed stage), attempt 3 of 6 was abandoned and no retry is left (the worker "
                   f"resumes a day only until 16:00 ET the next day). Check the marketing-worker logs.")


def test_an_unexpected_status_after_the_check_is_named():
    assert "unexpected status: archived" in _final_word(_run("archived", updated_at=_iso_of(AFTER_22)))
    assert _final_word(_run("archived", updated_at=_iso_of(BEFORE_22))) is None


# unchanged since the 22:00 check: a word only where that verdict was not final


@pytest.mark.parametrize("attempts, shown", [(2, "attempt 2 of 6"), (None, "attempt ? of 6"), ("5", "attempt ? of 6")])
def test_a_failed_run_with_retries_left_that_nobody_retried_points_at_the_cron(attempts, shown):
    msg = _final_word(_run("failed", stage="voiced", attempts=attempts, last_error="boom", updated_at=_iso_of(BEFORE_22)))
    assert msg == (f"⚠️ Marketing run Sat 2026-10-03 is still FAILED after stage voiced (the last completed stage), "
                   f"{shown}: no retry happened — check the marketing-worker cron (hourly at :15) and its last logs. "
                   f"This day's posts will not go out. Last error: boom.")


@pytest.mark.parametrize("status", ["planned", "in_progress"])
def test_a_run_untouched_since_the_check_never_finished(status):
    msg = _final_word(_run(status, stage="scripted", attempts=6, updated_at=_iso_of(BEFORE_22)))
    assert msg == (f"⚠️ Marketing run Sat 2026-10-03 never finished — the day is lost: {status} after stage scripted "
                   f"(the last completed stage), attempt 6 of 6, untouched since 10-03 21:40 ET. Check the "
                   f"marketing-worker cron and its last logs.")
    assert "untouched since an unreadable time" in _final_word(_run(status, attempts=6, updated_at=None))


@pytest.mark.parametrize("run", [
    _run("failed", attempts=6, updated_at=_iso_of(BEFORE_22)),                                      # all attempts used
    _run("failed", attempts=9, updated_at=_iso_of(SAT_22)),                                         # exactly at the check
    _run("skipped", metadata={"skip_reason": "writer_unavailable"}, updated_at=_iso_of(BEFORE_22)),
    _run("skipped", metadata={}, updated_at=_iso_of(BEFORE_22)),
    _run("archived", updated_at=_iso_of(BEFORE_22)),
    _run("failed", attempts=6, updated_at=None, started_at=None),                               # unreadable: unchanged
])
def test_a_verdict_that_was_already_final_at_22_is_not_repeated(run):
    assert _final_word(run) is None


def test_an_unchanged_line_skips_the_memory_peak_the_22_message_already_carried():
    peak = {"voiced_cgroup_peak_mb": 3700.0}
    unchanged = _final_word(_run("failed", attempts=2, updated_at=_iso_of(BEFORE_22), timings=peak))
    assert "\n" not in unchanged and "MB" not in unchanged
    changed = _final_word(_run("failed", attempts=6, updated_at=_iso_of(AFTER_22), timings=peak))
    assert changed.split("\n")[1].startswith("⚠️ Marketing worker memory: stage voiced peaked at 3,700 MB")


def test_a_hostile_last_error_in_a_final_word_is_scrubbed_folded_and_capped():
    hostile = "Traceback\n  bot " + TOKEN_LIKE + "\r\n\u2028forged\u202e\x00\ud83d " + "Z" * 10_000
    for run in (_run("failed", attempts=6, last_error=hostile, updated_at=_iso_of(AFTER_22)),
                _run("failed", attempts=2, last_error=hostile, updated_at=_iso_of(BEFORE_22))):
        msg = _final_word(run)
        assert TOKEN_SECRET not in msg and "Z" * 400 not in msg and len(msg) < 700
        for bad in ("\n", "\r", "\u2028", "\u202e", "\x00", "\ud83d"):
            assert bad not in msg
        assert "Last error: Traceback bot 1234567890:***" in msg
        msg.encode("utf-16-le")


def test_the_final_word_follows_the_new_york_clock_across_the_november_change():
    """Sat 10-31's nightly check judged the run at 22:07:30 EDT (11-01 02:07:30 UTC: the first tick after the
    hour, never 22:00:00 sharp); Sunday 11-01 falls back to EST at 06:00 UTC. Everything is relative to
    that CHECK time — a write between 22:00 and the check was seen by it."""
    halloween = date(2026, 10, 31)
    sun_16_est = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)
    check = datetime(2026, 11, 1, 2, 7, 30, tzinfo=timezone.utc)   # Sat 22:07:30 EDT

    def final(run):
        return _final_word(run, run_date=halloween, now=sun_16_est, checked_at=check)

    seen = check - timedelta(minutes=6, seconds=30)                  # Sat 22:01 EDT: after 22:00, before the check
    after = check + timedelta(seconds=30)                            # Sat 22:08 EDT
    for finished in (seen, check):
        assert final(_run("media_ready", run_date="2026-10-31", finished_at=_iso_of(finished))) is None
    assert "recovered at 10-31 22:08 ET" in final(_run("media_ready", run_date="2026-10-31", finished_at=_iso_of(after)))
    # the repeated 01:30 is told apart by the clock, not by the wall time: both are after the check
    edt = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)        # 01:30 EDT
    est = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)        # 01:30 EST
    for t in (edt, est):
        assert "recovered at 11-01 01:30 ET" in final(_run("media_ready", run_date="2026-10-31", finished_at=_iso_of(t)))
    # a failure the check saw (22:01) is not news; one after it (22:08) ended the day
    assert final(_run("failed", run_date="2026-10-31", attempts=3, updated_at=_iso_of(seen))).startswith(
        "⚠️ Marketing run Sat 2026-10-31 is still FAILED")
    assert final(_run("failed", run_date="2026-10-31", attempts=6, updated_at=_iso_of(seen))) is None
    assert final(_run("failed", run_date="2026-10-31", attempts=3, updated_at=_iso_of(after))).startswith(
        "⚠️ Marketing run Sat 2026-10-31 ended FAILED")


#: The nightly check of SAT as it really runs: the first publisher tick after 22:00 (every 600 s).
SAT_2207 = SAT_22 + timedelta(minutes=7)


def test_a_verdict_the_check_delivered_after_22_is_never_repeated_or_called_a_recovery():
    """Re-review r2.0 (a)/(b), pure: what happened between 22:00 and the check's own time (22:07) is what
    the check SAW. A fixed 22:00 repeated the first and invented a recovery for the second."""
    failed_at_cap = _run("failed", stage="voiced", attempts=6, last_error="ffmpeg exited 137",
                         started_at=_iso_of(SAT_22 - timedelta(minutes=45)),
                         updated_at=_iso_of(SAT_22 + timedelta(minutes=3)))           # failed at 22:03
    finished_late = _run("media_ready", attempts=1, started_at=_iso_of(SAT_22 - timedelta(minutes=45)),
                         finished_at=_iso_of(SAT_22 + timedelta(minutes=4)),
                         updated_at=_iso_of(SAT_22 + timedelta(minutes=4)))           # media_ready at 22:04
    for run in (failed_at_cap, finished_late):
        assert _final_word(run, checked_at=SAT_2207) is None
        # …while a check that really ran at 22:00:00 would have missed both, and is told so
        assert _final_word(run, checked_at=SAT_22) is not None
    assert _final_word(finished_late, checked_at=SAT_22) == ("✅ Marketing run Sat 2026-10-03 recovered at 10-03 "
                                                             "22:04 ET — its posts are waiting for review.")


def test_a_write_at_the_check_instant_counts_as_seen_and_one_after_it_does_not():
    at = _iso_of(SAT_2207)
    assert _final_word(_run("failed", attempts=6, updated_at=at), checked_at=SAT_2207) is None
    assert _final_word(_run("media_ready", finished_at=at), checked_at=SAT_2207) is None
    later = _iso_of(SAT_2207 + timedelta(microseconds=1))
    assert "ended FAILED" in _final_word(_run("failed", attempts=6, updated_at=later), checked_at=SAT_2207)
    assert "recovered at 10-03 22:07 ET" in _final_word(_run("media_ready", finished_at=later), checked_at=SAT_2207)
    # the still-running line names the check's own time
    alive = _run("in_progress", stage="voiced", attempts=5, updated_at=_iso_of(SUN_16 - timedelta(minutes=3)))
    assert ", resumed after the 22:07 ET check — if this attempt fails" in _final_word(alive, checked_at=SAT_2207)


@pytest.mark.parametrize("run, expected", [
    (None, "⚠️ Marketing: no run for Sat 2026-10-03, a posting day — the worker never claimed it, so nothing was "
           "posted for this day (the nightly check never went out). Check the marketing-worker cron on Railway "
           "(hourly from 16:15 ET) and its last logs."),
    (["not", "a", "run"], "⚠️ Marketing: no run for Sat 2026-10-03"),
    # failed at the cap at 21:40 — before 22:00, so a check that went out would have said it already
    (_run("failed", stage="voiced", attempts=6, last_error="boom", updated_at=_iso_of(BEFORE_22)),
     "⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage voiced (the last completed stage): all 6 attempts "
     "used. This day's posts will not go out. Last error: boom."),
    (_run("failed", stage="selected", attempts=2, updated_at=_iso_of(BEFORE_22)),
     "⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage selected (the last completed stage): attempt 2 of 6; "
     "no retry is left (the worker resumes a day only until 16:00 ET the next day). This day's posts will not go "
     "out. Last error: (none recorded)."),
    (_run("failed", attempts=None, updated_at=None), "⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage "
                                                     "assets_ready (the last completed stage): attempt ? of 6; no "
                                                     "retry is left"),
    (_run("skipped", metadata={"skip_reason": "writer_unavailable"}, updated_at=_iso_of(BEFORE_22)),
     "⚠️ Marketing run Sat 2026-10-03 was SKIPPED (writer_unavailable): the writer never produced a verdict"),
    (_run("skipped", metadata={}, updated_at=_iso_of(BEFORE_22)),
     "⚠️ Marketing run Sat 2026-10-03 was SKIPPED with no reason recorded"),
    (_run("in_progress", stage="voiced", attempts=5, updated_at=_iso_of(SUN_16 - timedelta(minutes=3))),
     "⏳ Marketing run Sat 2026-10-03 is still running after stage voiced (the last completed stage), attempt 5 of "
     "6 — if this attempt fails, no retry is left (the worker resumes a day only until 16:00 ET the next day)."),
    (_run("planned", stage="planned", attempts=6, updated_at=_iso_of(BEFORE_22)),
     "⚠️ Marketing run Sat 2026-10-03 never finished — the day is lost: planned before any stage completed, "
     "attempt 6 of 6 was abandoned and no retry is left"),
    (_run("archived", updated_at=_iso_of(BEFORE_22)),
     "⚠️ Marketing run Sat 2026-10-03 has an unexpected status: archived"),
])
def test_a_day_whose_nightly_check_never_went_out_gets_a_terminal_word(run, expected):
    """Re-review r2.0 (c), pure: with no check (`checked_at` None) the owner heard nothing that night, so
    the final word reports every outcome but a good day — however long ago the run stopped changing."""
    msg = _final_word(run, checked_at=None)
    assert msg is not None and msg.startswith(expected), msg
    assert "resumed after" not in msg and "\n" not in msg


@pytest.mark.parametrize("run", [
    _run("media_ready", finished_at=_iso_of(BEFORE_22)), _run("published", finished_at=_iso_of(AFTER_22)),
    _run("media_ready", finished_at=None, updated_at=None),
    _run("skipped", metadata={"skip_reason": "rest_day"}, updated_at=_iso_of(BEFORE_22)),
])
def test_a_good_day_whose_nightly_check_never_went_out_says_nothing(run):
    assert _final_word(run, checked_at=None) is None


def test_with_no_check_a_good_days_memory_peak_is_the_only_word_and_a_rest_day_none():
    hot = _run("media_ready", finished_at=_iso_of(BEFORE_22), timings={"rendered_cgroup_peak_mb": 3500.0})
    assert _final_word(hot, checked_at=None).startswith("⚠️ Marketing worker memory: stage rendered peaked at 3,500 MB")
    assert _final_word(hot, checked_at=SAT_2207) is None          # the check's own message carried it
    assert _final_word(_run("failed", attempts=6), run_date=FRI, posting_day=False, checked_at=None) is None
    assert _final_word(None, run_date=FRI, posting_day=False, checked_at=None) is None


def test_a_hostile_last_error_with_no_check_is_scrubbed_folded_and_capped():
    hostile = "Traceback\n  bot " + TOKEN_LIKE + "\r\n forged‮\x00\ud83d " + "Z" * 10_000
    msg = _final_word(_run("failed", attempts=6, last_error=hostile, updated_at=_iso_of(BEFORE_22)), checked_at=None)
    assert TOKEN_SECRET not in msg and "Z" * 400 not in msg and len(msg) < 700
    for bad in ("\n", "\r", " ", "‮", "\x00", "\ud83d"):
        assert bad not in msg
    msg.encode("utf-16-le")


@pytest.mark.parametrize("checked_at", ["2026-10-04T02:07:00+00:00", 1759543620, object()])
def test_a_check_time_that_is_not_an_instant_is_no_check(checked_at):
    """Defence in depth: only `nightly_check_time` feeds `checked_at`, and it returns an instant or None.
    Anything else must never be compared — it is treated as no check (the noisy, never the silent side)."""
    run = _run("failed", attempts=6, updated_at=_iso_of(BEFORE_22))
    assert _final_word(run, checked_at=checked_at) == _final_word(run, checked_at=None)


# when the nightly check judged the run: its own day-job row (pure)

_SAT_CHECKED = "2026-10-04T02:07:12.345678+00:00"     # Sat 22:07:12 EDT, as PostgREST renders timestamptz


def _job_row(run_day: Any, last_run_at: Any = _SAT_CHECKED, **kw: Any) -> Dict[str, Any]:
    return {"job": ds.JOB_HEALTH, "enabled": True, "run_day": run_day, "claim_at": None, "last_run_at": last_run_at,
            "last_error": kw.pop("last_error", None), "items_written": kw.pop("items_written", 1), **kw}


@pytest.mark.parametrize("state, expected", [
    # never succeeded for SAT: no row yet, or every attempt failed (run_day kept an earlier day)
    ({"job": ds.JOB_HEALTH, "enabled": True, "run_day": None}, (None, ds.NIGHTLY_NEVER)),
    (_job_row(None, last_error="telegram: TelegramException: HTTP 500"), (None, ds.NIGHTLY_NEVER)),
    (_job_row("2026-10-01"), (None, ds.NIGHTLY_NEVER)),
    (_job_row("2026-10-01", last_run_at=None), (None, ds.NIGHTLY_NEVER)),
    # succeeded on SAT: the claim instant, in every shape the ledger can carry it
    (_job_row("2026-10-03"), (datetime(2026, 10, 4, 2, 7, 12, 345678, tzinfo=timezone.utc), ds.NIGHTLY_CHECKED)),
    (_job_row("2026-10-03", "2026-10-04T02:07:12Z"), (datetime(2026, 10, 4, 2, 7, 12, tzinfo=timezone.utc),
                                                      ds.NIGHTLY_CHECKED)),
    (_job_row(" 2026-10-03 "), (datetime(2026, 10, 4, 2, 7, 12, 345678, tzinfo=timezone.utc), ds.NIGHTLY_CHECKED)),
    (_job_row(date(2026, 10, 3), SAT_2207), (SAT_2207, ds.NIGHTLY_CHECKED)),
    # the last minute of SAT (ET) is still SAT's check; a run hour changed since (20:00) is the truth
    (_job_row("2026-10-03", "2026-10-04T03:59:59+00:00"), (datetime(2026, 10, 4, 3, 59, 59, tzinfo=timezone.utc),
                                                           ds.NIGHTLY_CHECKED)),
    (_job_row("2026-10-03", "2026-10-04T00:00:00+00:00"), (datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc),
                                                           ds.NIGHTLY_CHECKED)),
    # succeeded on SAT, but its time is gone: a later day's failed attempt overwrote it, or it is junk
    (_job_row("2026-10-03", "2026-10-06T02:00:00+00:00"), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),   # Mon 22:00 EDT
    (_job_row("2026-10-03", "2026-10-04T04:00:01+00:00"), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),   # Sun 00:00:01 EDT
    (_job_row("2026-10-03", "2026-10-03T03:59:59+00:00"), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),   # Fri 23:59:59 EDT
    (_job_row("2026-10-03", None), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),
    (_job_row("2026-10-03", "junk"), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),
    (_job_row("2026-10-03", 12345), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),
    (_job_row("2026-10-03", "0001-01-01T00:00:00+00:00"), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),   # ET overflow
    (_job_row("2026-10-03", "9999-12-31T23:59:59-14:00"), (SAT_22, ds.NIGHTLY_TIME_UNKNOWN)),
    # a LATER day's success hides SAT's: speak (may repeat), never stay silent
    (_job_row("2026-10-04", "2026-10-04T04:00:05+00:00"), (None, ds.NIGHTLY_LATER_DAY)),
    (_job_row("2026-10-05", "2026-10-06T02:04:00+00:00"), (None, ds.NIGHTLY_LATER_DAY)),
])
def test_nightly_check_time_reads_the_checks_own_job_state(state, expected):
    assert ds.nightly_check_time(state, SAT) == expected


@pytest.mark.parametrize("state", [
    None, [], "marketing_run_health", 7,
    _job_row("not-a-date"), _job_row(""), _job_row(20261003), _job_row(True), _job_row("2026-10-03T00:00:00"),
    _job_row(datetime(2026, 10, 3, 22, 0, tzinfo=ET)), _job_row({"day": "2026-10-03"}), _job_row(["2026-10-03"]),
    _job_row("2026-13-03"),
])
def test_an_unreadable_nightly_job_state_raises_never_reads_as_never_checked(state):
    """Fail closed: when the row cannot say WHETHER the check succeeded, there is no word on a guess."""
    with pytest.raises(notification_jobs.JobStateUnreadable):
        ds.nightly_check_time(state, SAT)


def test_an_unreadable_run_day_is_named_scrubbed_and_capped_in_the_error():
    with pytest.raises(notification_jobs.JobStateUnreadable) as e:
        ds.nightly_check_time(_job_row("x\n" + TOKEN_LIKE + "y" * 500), SAT)
    text = str(e.value)
    assert text.startswith("marketing_run_health: run_day ") and text.endswith(" is not a date")
    assert TOKEN_SECRET not in text and "\n" not in text and len(text) < 160


def test_the_nightly_check_time_follows_the_new_york_clock_across_the_november_change():
    """Sat 10-31's check ran at 23:30 EDT = 11-01 03:30 UTC — still 10-31 in New York."""
    row = _job_row("2026-10-31", "2026-11-01T03:30:00+00:00")
    assert ds.nightly_check_time(row, date(2026, 10, 31)) == (datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc),
                                                              ds.NIGHTLY_CHECKED)
    # Mon 11-02's at 22:05 EST = 11-03 03:05 UTC
    row = _job_row("2026-11-02", "2026-11-03T03:05:00+00:00")
    assert ds.nightly_check_time(row, date(2026, 11, 2))[1] == ds.NIGHTLY_CHECKED
    # time unknown → the window opens at 22:00 EST (03:00 UTC), not EDT
    row = _job_row("2026-11-02", "2026-11-04T03:00:00+00:00")
    assert ds.nightly_check_time(row, date(2026, 11, 2)) == (datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc),
                                                             ds.NIGHTLY_TIME_UNKNOWN)


# ── the pool's runway (pure) ──────────────────────────────────────────────────


def test_runway_matches_the_production_count_of_2026_10_01():
    """34 eligible, 2 used (09-29 and today's, 10-01): the last unused lesson posts Thu 11-26 and the
    first repeat is Sat 11-28 (the plan's own arithmetic)."""
    pool = [f"journey:{i}" for i in range(34)]
    out = ds.pool_runway(pool, ["journey:3", "journey:7"], today=date(2026, 10, 1), today_picked=True)
    assert out == {"pool": 34, "unused": 32, "first_repeat": date(2026, 11, 28)}


def test_runway_counts_today_when_its_pick_is_still_to_come():
    pool = ["a", "b", "c"]
    mon = date(2026, 10, 5)
    # Mon, Tue, Thu take a, b, c — Sat repeats
    assert ds.pool_runway(pool, [], today=mon, today_picked=False)["first_repeat"] == date(2026, 10, 10)
    # today's pick already made: Tue, Thu use the other two — Sat repeats as well, one lesson fewer
    picked = ds.pool_runway(pool, ["a"], today=mon, today_picked=True)
    assert picked == {"pool": 3, "unused": 2, "first_repeat": date(2026, 10, 10)}
    # on a rest day the count starts tomorrow
    assert ds.pool_runway(pool, [], today=FRI, today_picked=False)["first_repeat"] == date(2026, 10, 8)


def test_runway_of_a_used_up_pool_is_the_next_posting_day():
    out = ds.pool_runway(["a", "b"], ["b", "a"], today=FRI, today_picked=False)
    assert out == {"pool": 2, "unused": 0, "first_repeat": SAT}


def test_runway_of_an_empty_pool_has_no_date():
    assert ds.pool_runway([], ["a"], today=SAT, today_picked=False) == {"pool": 0, "unused": 0, "first_repeat": None}


def test_runway_ignores_junk_duplicates_and_picks_outside_the_pool():
    pool = ["a", "a", "b", None, 7, "", "c"]
    used = ["a", None, 3, "retired:item", "a"]
    out = ds.pool_runway(pool, used, today=FRI, today_picked=False)
    assert out["pool"] == 3 and out["unused"] == 2


def test_the_first_repeat_is_always_a_posting_day():
    pool = [str(i) for i in range(9)]
    for offset in range(14):
        today = date(2026, 10, 1) + timedelta(days=offset)
        for picked in (False, True):
            for used_n in range(0, 10, 3):
                out = ds.pool_runway(pool, pool[:used_n], today=today, today_picked=picked)
                assert selection.is_posting_day(out["first_repeat"])
                assert out["first_repeat"] >= today


def test_runway_of_a_huge_pool_is_quick_and_exact():
    pool = [str(i) for i in range(20_000)]
    out = ds.pool_runway(pool, [], today=FRI, today_picked=False)
    assert out["unused"] == 20_000
    # 20,001 posting days at 4 a week from Sat 2026-10-03
    assert out["first_repeat"] > date(2122, 1, 1) and selection.is_posting_day(out["first_repeat"])


# ── schedule ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("when, due", [
    (_et(2026, 10, 5, 8, 59), False),     # Monday before 09:00 ET
    (_et(2026, 10, 5, 9, 0), True),       # Monday 09:00 ET
    (_et(2026, 10, 5, 23, 59), True),
    (_et(2026, 10, 6, 8, 59), False),     # Tuesday catch-up starts at 09:00 too
    (_et(2026, 10, 6, 9, 0), True),
    (_et(2026, 10, 7, 12, 0), False),     # Wednesday
    (_et(2026, 10, 4, 12, 0), False),     # Sunday
    # DST: Monday 2026-11-02 is EST (UTC-5) — 09:00 ET is 14:00 UTC, not 13:00
    (datetime(2026, 11, 2, 13, 59, tzinfo=timezone.utc), False),
    (datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc), True),
    # …and Monday 2026-10-26 is still EDT (UTC-4): 09:00 ET is 13:00 UTC
    (datetime(2026, 10, 26, 12, 59, tzinfo=timezone.utc), False),
    (datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc), True),
    # a naive time is UTC: 13:30 UTC on 11-02 is 08:30 EST
    (datetime(2026, 11, 2, 13, 30), False),
])
def test_digest_due(when, due):
    assert ds.digest_due(when) is due


@pytest.mark.parametrize("when, due", [
    (_et(2026, 10, 3, 21, 59), False),    # Saturday before 22:00 ET
    (_et(2026, 10, 3, 22, 0), True),
    (_et(2026, 10, 3, 23, 59), True),
    (_et(2026, 10, 2, 22, 30), False),    # Friday is a rest day
    (_et(2026, 10, 4, 22, 30), False),    # Sunday too
    (datetime(2026, 11, 3, 2, 59, tzinfo=timezone.utc), False),   # Mon 11-02 21:59 EST
    (datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc), True),     # Mon 11-02 22:00 EST
])
def test_health_due(when, due):
    assert ds.health_due(when) is due


@pytest.mark.parametrize("when, due", [
    (_et(2026, 10, 4, 15, 59), False),    # Sunday (after Saturday, a posting day) before 16:00 ET
    (_et(2026, 10, 4, 16, 0), True),
    (_et(2026, 10, 4, 23, 59), True),
    (_et(2026, 10, 6, 16, 0), True),      # Tuesday: Monday posted
    (_et(2026, 10, 7, 16, 0), True),      # Wednesday: Tuesday posted
    (_et(2026, 10, 9, 16, 0), True),      # Friday: Thursday posted
    (_et(2026, 10, 5, 16, 0), False),     # Monday: Sunday is a rest day
    (_et(2026, 10, 8, 16, 0), False),     # Thursday: Wednesday is a rest day
    (_et(2026, 10, 3, 16, 0), False),     # Saturday: Friday is a rest day
    (_et(2026, 10, 4, 0, 30), False),     # just after midnight: never early
    # DST: Sunday 2026-11-01 is EST (UTC-5) from 02:00 — 16:00 ET is 21:00 UTC, not 20:00
    (datetime(2026, 11, 1, 20, 59, tzinfo=timezone.utc), False),
    (datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc), True),
    (datetime(2026, 11, 1, 21, 0), True),                          # a naive time is UTC
])
def test_final_due(when, due):
    assert ds.final_due(when) is due


def _hours(monkeypatch, run_hour: Any, attempts: Any = 6) -> None:
    monkeypatch.setattr(settings, "MARKETING_RUN_HOUR_ET", run_hour)
    monkeypatch.setattr(settings, "MARKETING_MAX_RUN_ATTEMPTS", attempts)


@pytest.mark.parametrize("run_hour, attempts, nightly, final", [
    (16, 6, 22, 16),            # the defaults: six attempts from 16:15 are spent by ~21:30
    (10, 3, 13, 10),
    (17, 6, 23, 17),            # 17 + 6 = 23
    (18, 6, 23, 18),            # 24, capped: the check comes while the 6th attempt is still due (23:15)
    (22, 1, 23, 22),
    (0, 6, 6, 0),
    (16, 0, 23, 16),            # no attempts cap (decide_claim applies none): attempts until midnight
    (16, -3, 23, 16),
    (16, 10 ** 9, 23, 16),
    (16, True, 23, 16),         # a bool is not a count
    (16, "6", 23, 16),
    (16, 6.0, 23, 16),
])
def test_the_run_health_hours_follow_the_run_hour_and_the_attempts_cap(monkeypatch, run_hour, attempts, nightly,
                                                                        final):
    _hours(monkeypatch, run_hour, attempts)
    assert (ds.run_hour_et(), ds.health_hour_et(), ds.final_hour_et()) == (run_hour, nightly, final)
    assert ds.health_opens_at(SAT) == datetime(2026, 10, 3, nightly, tzinfo=ET)
    # read at call time: the edges move with the setting (Saturday is a posting day, Sunday follows one)
    assert ds.health_due(_et(2026, 10, 3, nightly, 0)) is True
    if nightly > 0:
        assert ds.health_due(_et(2026, 10, 3, nightly - 1, 59)) is False
    assert ds.final_due(_et(2026, 10, 4, final, 0)) is True
    if final > 0:
        assert ds.final_due(_et(2026, 10, 4, final - 1, 59)) is False


@pytest.mark.parametrize("attempts", [6, 0, 1])
def test_with_run_hour_23_there_is_no_nightly_check_and_the_final_word_is_due_at_23(monkeypatch, attempts):
    """The worker's first tick is 23:15: a check at 23:00 could only report a run that cannot exist yet. The
    next day's final word (which then sees no successful check) reports the day alone."""
    _hours(monkeypatch, 23, attempts)
    assert ds.health_hour_et() == 23
    for when in (_et(2026, 10, 3, 23, 0), _et(2026, 10, 3, 23, 59), _et(2026, 10, 5, 23, 30)):
        assert ds.health_due(when) is False
    assert ds.final_due(_et(2026, 10, 4, 22, 59)) is False and ds.final_due(_et(2026, 10, 4, 23, 0)) is True


@pytest.mark.parametrize("bad", [24, -1, 99, "18", True, None, 16.5, 10 ** 6])
def test_an_unreadable_run_hour_is_the_default_and_is_logged(monkeypatch, caplog, bad):
    """Settings refuses these at boot; only a monkeypatch can get one here. The tick must not crash."""
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    _hours(monkeypatch, bad)
    assert (ds.run_hour_et(), ds.health_hour_et(), ds.final_hour_et()) == (16, 22, 16)
    assert any("MARKETING_RUN_HOUR_ET" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_the_run_hour_setting_refuses_anything_but_an_hour_at_boot(monkeypatch):
    """`Field(ge=0, le=23)`: a bad Railway value fails the web deploy loudly, like any other bad setting —
    never a run-health clock silently off by a day."""
    from pydantic import ValidationError

    from app.config import Settings

    for bad in ("24", "-1", "abc", "16.5", ""):
        monkeypatch.setenv("MARKETING_RUN_HOUR_ET", bad)
        with pytest.raises(ValidationError) as e:
            Settings()
        assert [err["loc"] for err in e.value.errors()] == [("MARKETING_RUN_HOUR_ET",)], bad
    for ok in ("0", "23", "9"):
        monkeypatch.setenv("MARKETING_RUN_HOUR_ET", ok)
        assert Settings().MARKETING_RUN_HOUR_ET == int(ok)


def _env_get_default(call: Any, name: str) -> Optional[str]:
    """The string default of `os.environ.get(name, "<n>")`, or None when `call` is not exactly that."""
    import ast

    f = call.func if isinstance(call, ast.Call) else None
    if not (isinstance(f, ast.Attribute) and f.attr == "get" and isinstance(f.value, ast.Attribute)
            and f.value.attr == "environ" and isinstance(f.value.value, ast.Name) and f.value.value.id == "os"):
        return None
    args = call.args
    if (len(args) != 2 or call.keywords or not isinstance(args[0], ast.Constant) or args[0].value != name
            or not isinstance(args[1], ast.Constant) or not isinstance(args[1].value, str)):
        return None
    return args[1].value


def _worker_run_hour_defaults(source: str) -> List[str]:
    """Every default the worker gives MARKETING_RUN_HOUR_ET, from its AST (so a comment or a docstring can
    never satisfy it). Every use of the name must be one `os.environ.get(name, "<n>")` — any other way
    to read it (os.environ[...], os.getenv, a variable name) is reported as None, never skipped."""
    import ast

    tree = ast.parse(source)
    calls = {id(n.args[0]): n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args}
    found: List[Any] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "MARKETING_RUN_HOUR_ET":
            call = calls.get(id(node))
            found.append(_env_get_default(call, "MARKETING_RUN_HOUR_ET") if call is not None else None)
    return found


def test_the_webs_run_hour_default_mirrors_the_workers():
    """The web cannot read the worker's environment: its MARKETING_RUN_HOUR_ET is a MIRROR, and the two
    defaults must agree — else an owner who sets neither runs the worker at one hour while the run-health
    checks time themselves from another. Read from the worker's source by AST (never imported: the worker
    tree must stay standalone), and never by a text search a comment could satisfy."""
    from pathlib import Path

    from app.config import Settings

    source = (Path(__file__).resolve().parents[1] / "marketing" / "main.py").read_text(encoding="utf-8")
    defaults = _worker_run_hour_defaults(source)
    assert defaults, "the worker no longer reads MARKETING_RUN_HOUR_ET with os.environ.get — update this guard"
    assert None not in defaults, f"the worker reads MARKETING_RUN_HOUR_ET some other way: {defaults}"
    web_default = Settings.model_fields["MARKETING_RUN_HOUR_ET"].default
    assert {int(d) for d in defaults} == {web_default}, (defaults, web_default)


@pytest.mark.parametrize("source, expected", [
    ('import os\nh = int(os.environ.get("MARKETING_RUN_HOUR_ET", "16"))\n', ["16"]),
    ('import os\nh = int(os.environ.get("MARKETING_RUN_HOUR_ET", "18"))\n', ["18"]),
    # the docstring and the comment name the variable as TEXT only — never a Constant equal to it
    ('"""MARKETING_RUN_HOUR_ET default 16"""\nimport os\n# os.environ.get("MARKETING_RUN_HOUR_ET", "16")\n'
     'h = int(os.environ.get("MARKETING_RUN_HOUR_ET", "9"))\n', ["9"]),
    ('import os\nh = int(os.environ["MARKETING_RUN_HOUR_ET"])\n', [None]),
    ('import os\nh = int(os.getenv("MARKETING_RUN_HOUR_ET", "16"))\n', [None]),
    ('import os\nh = int(os.environ.get("MARKETING_RUN_HOUR_ET", 16))\n', [None]),
    ('import os\nh = int(os.environ.get("MARKETING_RUN_HOUR_ET"))\n', [None]),
    ('import os\nNAME = "MARKETING_RUN_HOUR_ET"\nh = int(os.environ.get(NAME, "16"))\n', [None]),
    ('import os\n# os.environ.get("MARKETING_RUN_HOUR_ET", "16")\nh = 16\n', []),
])
def test_the_run_hour_parity_scan_sees_every_read_and_no_comment(source, expected):
    """The scan above, mutation-tested on hand-written sources: a changed default is seen, a comment or a
    docstring never counts, and any other way to read the variable is reported rather than skipped."""
    assert _worker_run_hour_defaults(source) == expected


def test_the_digest_week_is_the_previous_monday_to_sunday_whatever_the_day():
    for today in (date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 11)):
        assert ds.digest_week(today) == (WEEK_START, WEEK_END)
    assert ds.digest_week(date(2026, 10, 12)) == (date(2026, 10, 5), date(2026, 10, 11))


def test_the_week_bounds_follow_the_new_york_clock_across_the_november_change():
    start, end = ds.week_bounds(*ds.digest_week(date(2026, 11, 2)))
    assert start == datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)   # Mon 00:00 EDT
    assert end == datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)      # Mon 00:00 EST
    assert end - start == timedelta(hours=169)
    start, end = ds.week_bounds(*ds.digest_week(date(2027, 3, 15)))      # the March change
    assert end - start == timedelta(hours=167)


# ── the weekly digest (pure: summarize_week + compose_digest) ─────────────────


def _summary(posts: List[Dict[str, Any]], **kw: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = dict(now=NOW, week_start=WEEK_START, week_end=WEEK_END, posts=posts, runs=[],
                                measured=[], link_hits=[], spend={}, budget_micros=2_000_000, escalated=[],
                                runway={"pool": 34, "unused": 30, "first_repeat": date(2026, 11, 26)},
                                metrics_job={"job": ds.JOB_METRICS, "enabled": True, "run_day": "2026-10-05",
                                             "last_run_at": "2026-10-05T10:01:00+00:00", "items_written": 4,
                                             "last_error": None},
                                metrics_enabled=True, store="prelaunch")
    base.update(kw)
    return ds.summarize_week(**base)


def _digest(posts: List[Dict[str, Any]], **kw: Any) -> str:
    text = ds.compose_digest(_summary(posts, **kw))
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    return text


def _assert_no_model_text(text: str) -> None:
    assert "CAPTION-SENTINEL" not in text and TITLE not in text


def test_an_empty_week_is_one_short_honest_message():
    text = _digest([], runs=[])
    assert text.startswith("📊 Caydex marketing — weekly digest\nMon 2026-09-28 → Sun 2026-10-04 (ET)")
    assert "Posts: none this week" in text
    # every posting day of the week without a run says so
    for d in ("Mon 09-28", "Tue 09-29", "Thu 10-01", "Sat 10-03"):
        assert f"• {d} no run (a posting day)" in text
    assert "Fri 10-02" not in text and "Sun 10-04" not in text
    assert "Engagement: no live published posts this week" in text
    assert "/go taps: none" in text and "Review time: no decisions this week" in text
    assert "Outcome unknown: none waiting for your answer" in text
    assert re.search(r"\b0 (likes?|reposts?|impressions?)\b", text) is None


def _full_week() -> List[Dict[str, Any]]:
    asked = "2026-10-03T20:31:00+00:00"
    posts = [
        # published, measured — the X top post
        _post("x", "published", external_id=X_ID, published_at="2026-10-03T21:00:00+00:00",
              metrics={"v": 1, "rev": 4, "status": "ok", "measured_day": "2026-10-05",
                       "last": {"likes": 7, "reposts": 2, "replies": 0, "impressions": 450}},
              meta={"review_notified_at": asked,
                    "review": {"decision": "approved", "by": "telegram:1", "at": "2026-10-03T21:31:00+00:00"}}),
        # published, junk counts only — measured nothing, never a 0
        _post("x", "published", run_date="2026-10-01", external_id="1840000000000000002",
              metrics={"v": 1, "status": "error", "last": {"likes": "12", "reposts": -1, "replies": True,
                                                           "quotes": 1.5, "bookmarks": float("nan"),
                                                           "impressions": 10 ** 16}}),
        # published, not measured yet
        _post("bluesky", "published", run_date="2026-10-01", external_url="https://bsky.app/profile/d/post/r1",
              metrics={}),
        # published bluesky, measured, missing on the platform now
        _post("bluesky", "published", external_url="https://bsky.app/profile/d/post/r2",
              metrics={"v": 1, "status": "missing", "last": {"likes": 3, "reposts": 1, "replies": 2,
                                                              "quotes": 0, "bookmarks": 1}}),
        _post("x", "retracted", run_date="2026-09-29", metrics={"last": {"likes": 99}}),
        _post("bluesky", "queued", run_date="2026-10-03"),
        _post("x", "approved", run_date="2026-10-03"),
        _post("bluesky", "pending_review", run_date="2026-10-03"),
        # failed, with a hostile error
        _post("x", "failed", run_date="2026-09-29", last_error="X said no\n" + TOKEN_LIKE,
              meta={"publish": {"category": "forbidden"}}),
        _post("bluesky", "failed", run_date="2026-09-29", last_error=None, meta={"publish": "junk"}),
        # expired: approved-never-sent (expired_from both ways + a legacy row), asked, preview, unshown
        _post("x", "skipped", run_date="2026-09-28",
              meta={"skip_reason": "expired", "expired_from": "approved"}),
        _post("bluesky", "skipped", run_date="2026-09-28",
              meta={"skip_reason": "expired", "review": {"decision": "approved"}}),   # legacy: no expired_from
        _post("x", "skipped", run_date="2026-09-29",
              meta={"skip_reason": "expired", "expired_from": "pending_review", "review_notified_at": asked}),
        _post("tiktok", "skipped", run_date="2026-09-29",
              meta={"skip_reason": "expired", "expired_from": "pending_review", "review_preview_at": asked}),
        _post("youtube", "skipped", run_date="2026-09-29",
              meta={"skip_reason": "expired", "expired_from": "pending_review"}),
        # rehearsal rows never count
        _post("x", "published", live=False, metrics={"last": {"likes": 1000}}),
        _post("x", "rejected", meta={"dry_run": None}),
    ]
    # a rejection for every reason, plus one with none and one with a junk reason
    for code in REJECT_REASONS:
        posts.append(_post("bluesky", "rejected", run_date="2026-10-01",
                           meta={"review_notified_at": asked,
                                 "review": {"decision": "rejected", "at": "2026-10-03T20:41:00+00:00",
                                            "reason": code}}))
    posts.append(_post("x", "rejected", meta={"review": {"decision": "rejected"}}))
    posts.append(_post("x", "rejected", meta={"review": {"decision": "rejected", "reason": "vibes\n"}}))
    return posts


def test_a_full_week_counts_every_status_reason_and_expiry_kind():
    s = _summary(_full_week())
    assert s["live"] == 22 and s["rehearsal"] == 2
    assert s["status_totals"] == {"published": 4, "retracted": 1, "queued": 1, "approved": 1,
                                  "pending_review": 1, "failed": 2, "skipped": 5, "rejected": 7}
    assert s["expired"] == {"approved": 2, "asked": 1, "preview": 1, "unshown": 1}
    assert s["rejections"]["by_reason"] == {"tone": 1, "accuracy": 1, "compliance": 1, "weak": 1, "other": 1,
                                            "none": 2}
    assert s["by_platform"]["X"] == {"published": 2, "retracted": 1, "approved": 1, "failed": 1, "skipped": 2,
                                     "rejected": 2}
    text = ds.compose_digest(s)
    assert "Posts: 22 live — published 4 · retracted 1 · rejected 7 · failed 2 · skipped 5 · queued 1 · " \
           "approved 1 · pending_review 1" in text
    assert ("Expired: 2 approved but never sent · 1 not reviewed · 1 preview (platform not enabled) · "
            "1 never shown to you") in text
    assert "Rejected 7: Tone 1 · Accuracy 1 · Compliance 1 · Weak / boring 1 · Other 1 · no reason 2" in text
    assert "  ❌ BLUESKY · run 10-01 · Weak / boring" in text
    assert "(+2 rehearsal rows — dry run, never publishable; not counted)" in text
    _assert_no_model_text(text)


def test_failed_rows_carry_a_scrubbed_one_line_error_and_never_a_token():
    text = _digest(_full_week())
    assert "  ✖ X · run 09-29 · forbidden: X said no 1234567890:***" in text
    assert "  ✖ BLUESKY · run 09-29 · no error recorded" in text
    assert TOKEN_SECRET not in text


def test_engagement_sums_only_readable_counts_and_links_the_top_post():
    s = _summary(_full_week())
    x, bsky = s["engagement"]["x"], s["engagement"]["bluesky"]
    # the junk-count post is live but measured nothing; the retracted post is not engagement
    assert x == {"live": 2, "measured": 1, "totals": {"likes": 7, "reposts": 2, "replies": 0, "impressions": 450},
                 "statuses": {"ok": 1, "error": 1},
                 "top": {"score": 9, "url": f"https://x.com/i/web/status/{X_ID}",
                         "counts": {"likes": 7, "reposts": 2, "replies": 0}}}
    assert bsky["live"] == 2 and bsky["measured"] == 1 and bsky["statuses"] == {"missing": 1}
    assert bsky["top"]["url"] == "https://bsky.app/profile/d/post/r2"
    text = ds.compose_digest(s)
    assert "• X — 1 of 2 measured: 450 impressions · 7 likes · 2 reposts · 0 replies" in text
    assert f"  Top: https://x.com/i/web/status/{X_ID} (7 likes · 2 reposts · 0 replies)" in text
    assert "  Measure: error 1 · ok 1" in text
    assert "• BLUESKY — 1 of 2 measured: 3 likes · 1 repost · 2 replies · 0 quotes · 1 bookmark" in text
    assert "1000" not in text and "99 like" not in text and "12 like" not in text


def test_a_week_with_no_engagement_shows_no_top_post():
    posts = [_post("x", "published", external_id=X_ID,
                   metrics={"status": "ok", "last": {"likes": 0, "impressions": 3}})]
    text = _digest(posts)
    assert "• X — 1 of 1 measured: 3 impressions · 0 likes" in text and "Top:" not in text


def test_metrics_off_says_so_instead_of_an_empty_engagement_section():
    text = _digest(_full_week(), metrics_enabled=False)
    assert "Engagement: not measured — MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on" in text
    assert "Metrics job: off (MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on)" in text


def _snapshot_post(platform: str, at: Any, followers: Any) -> Dict[str, Any]:
    return _post(platform, "published", metrics={"v": 1, "account": {"followers": followers, "following": 3,
                                                                     "posts": 9, "at": at, "source": "test"}})


def test_followers_change_is_measured_against_a_snapshot_at_least_six_days_older():
    measured = [
        _snapshot_post("bluesky", "2026-10-05T10:00:00+00:00", 40),   # now
        _snapshot_post("bluesky", "2026-09-30T10:00:00+00:00", 35),   # 5 days older — too recent
        _snapshot_post("bluesky", "2026-09-29T10:00:00+00:00", 30),   # exactly 6 days — the baseline
        _snapshot_post("bluesky", "2026-09-20T10:00:00+00:00", 10),   # older than the baseline
        _snapshot_post("x", "2026-10-05T10:00:00+00:00", 12),
        _snapshot_post("x", "2026-10-01T10:00:00+00:00", 11),         # nothing 6+ days older
    ]
    s = _summary([], measured=measured)
    assert s["followers"]["bluesky"]["followers"] == 40 and s["followers"]["bluesky"]["baseline"] == 30
    assert s["followers"]["x"]["baseline"] is None
    text = ds.compose_digest(s)
    assert "BLUESKY 40 (+10 since 09-29; as of 10-05)" in text
    assert "X 12 (as of 10-05; no snapshot 6+ days older yet)" in text


def test_a_follower_loss_and_no_change_are_signed():
    measured = [_snapshot_post("bluesky", "2026-10-05T10:00:00+00:00", 28),
                _snapshot_post("bluesky", "2026-09-28T10:00:00+00:00", 30),
                _snapshot_post("x", "2026-10-05T10:00:00+00:00", 12),
                _snapshot_post("x", "2026-09-28T10:00:00+00:00", 12)]
    text = _digest([], measured=measured)
    assert "BLUESKY 28 (-2 since 09-28" in text and "X 12 (±0 since 09-28" in text


@pytest.mark.parametrize("at, followers", [
    (None, 40), ("not a time", 40), ("2026-10-05T10:00:00+00:00", "40"), ("2026-10-05T10:00:00+00:00", -1),
    ("2026-10-05T10:00:00+00:00", True), ("2026-10-05T10:00:00+00:00", 40.0), ("2026-10-05T10:00:00+00:00", None),
    ("2099-01-01T00:00:00+00:00", 40),   # a future-dated snapshot is not "now"
])
def test_an_unreadable_follower_snapshot_is_ignored_never_zero(at, followers):
    s = _summary([], measured=[_snapshot_post("bluesky", at, followers)])
    assert s["followers"] == {}
    assert "Followers: no snapshot yet" in ds.compose_digest(s)


def test_an_unreadable_follower_read_says_so():
    assert "Followers: unreadable (see the web logs)" in _digest([], measured=None)


def test_go_taps_per_campaign_and_the_pre_launch_note():
    hits = [{"campaign": "tiktok", "day": "2026-09-30", "hits": 2}, {"campaign": "tiktok", "day": "2026-10-01",
                                                                   "hits": 1},
            {"campaign": "bluesky", "day": "2026-10-03", "hits": 1},
            {"campaign": "bad campaign\n", "day": "2026-10-03", "hits": 5},
            {"campaign": "x", "day": "2026-10-03", "hits": -3}, {"campaign": "x", "day": "2026-10-03", "hits": "4"},
            {"campaign": "x", "day": "2026-10-03", "hits": True}, "junk"]
    s = _summary([], link_hits=hits)
    assert s["taps"]["rows"] == [("(unreadable)", 5), ("tiktok", 3), ("bluesky", 1)]
    assert s["taps"]["total"] == 9 and s["taps"]["unreadable_rows"] == 4
    text = ds.compose_digest(s)
    assert "/go taps: (unreadable) 5 · tiktok 3 · bluesky 1 — 9 in total" in text
    assert "(4 unreadable row(s) not counted)" in text
    assert "Pre-launch: every /go tap lands on the Coming soon page (MARKETING_APP_STORE_URL is unset)." in text
    assert "Coming soon" not in _digest([], link_hits=hits, store="live")
    assert "set but invalid" in _digest([], link_hits=hits, store="invalid")
    assert "/go taps: unreadable" in _digest([], link_hits=None)


def test_the_store_state_follows_the_setting(monkeypatch):
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "")
    assert ds._store_state() == "prelaunch"
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "https://apps.apple.com/app/id6759525689")
    assert ds._store_state() == "live"
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "http://evil.example/app")
    assert ds._store_state() == "invalid"


def test_x_spend_by_op_against_the_cap():
    spend = {"x_create": 45_000, "x_metrics_read": 5_000, "x_metrics_read_correction": -1_000,
             "refund": -15_000, "x_account_read": 10_000, "weird op!": 7, "unknown": 2, "x_read": "lots",
             "x_delete": True}
    s = _summary([], spend=spend)
    assert s["spend"] == {"month": "2026-10", "total": 44_009, "budget": 2_000_000, "by_op": [
        ("x_create", 45_000), ("x_account_read", 10_000), ("x_metrics_read", 5_000), ("unreadable_op", 7),
        ("unknown", 2), ("x_metrics_read_correction", -1_000), ("refund", -15_000)]}
    text = ds.compose_digest(s)
    assert "X spend 2026-10 (UTC, to date): $0.044 of the $2.00 cap (2%)" in text
    assert "x_create $0.045 · x_account_read $0.010" in text and "refund -$0.015" in text


def test_x_spend_with_a_zero_budget_says_x_is_off_and_unreadable_says_so():
    """#7: a 0 / unset budget is not "uncapped" — `outlet_x.configured()` needs a budget, so X neither
    posts nor reads. The total still shows (a retract delete is charged without the budget)."""
    off = "X is OFF: no posts or reads (MARKETING_X_MONTHLY_BUDGET_USD is 0 or unset)"
    text = _digest([], spend={"x_delete": 15_000}, budget_micros=0)
    assert f"X spend 2026-10 (UTC, to date): $0.015 — {off}" in text
    text = _digest([], spend={}, budget_micros=-5)
    assert off in text and "cap off" not in text and " cap (" not in text
    assert "X spend: unreadable (see the web logs)" in _digest([], spend=None)


@pytest.mark.parametrize("budget, configured", [(None, False), (0, False), (0.0, False), (-1, False), (2.0, True)])
def test_x_is_off_exactly_when_the_outlet_says_it_is_unconfigured(monkeypatch, budget, configured):
    """The digest's OFF line keys on `budget_micros() == 0`; pin that it is the very condition under which
    the X outlet is not configured (credentials present), so the line can never call a live X off."""
    from app.services.marketing import outlets

    monkeypatch.setattr(outlet_x.x_api, "configured", lambda: True)
    monkeypatch.setattr(settings, "MARKETING_X_MONTHLY_BUDGET_USD", budget)
    assert (outlet_x.budget_micros() > 0) is configured
    assert outlets.adapter_for("x").configured() is configured


def test_review_latency_median_and_longest_over_readable_decisions():
    def decided(asked, at, decision="approved"):
        return _post("x", decision, meta={"review_notified_at": asked,
                                           "review": {"decision": decision, "at": at}})
    posts = [decided("2026-10-03T20:00:00+00:00", "2026-10-03T20:30:00+00:00"),
             decided("2026-10-03T20:00:00+00:00", "2026-10-03T21:00:00+00:00", "rejected"),
             decided("2026-10-03T20:00:00+00:00", "2026-10-04T05:05:00+00:00"),
             decided("2026-10-03T20:00:00+00:00", "2026-10-03T19:00:00+00:00"),   # negative: unreadable
             decided(None, "2026-10-03T21:00:00+00:00"), decided("junk", "2026-10-03T21:00:00+00:00"),
             _post("x", "approved", meta={"review_notified_at": "2026-10-03T20:00:00+00:00",
                                          "review": {"decision": "maybe", "at": "2026-10-03T20:01:00+00:00"}})]
    s = _summary(posts)
    assert s["latency"] == {"n": 3, "median_s": 3600.0, "max_s": 9 * 3600 + 300.0}
    assert "Review time: median 1h 00m · longest 9h 05m (3 decisions)" in ds.compose_digest(s)


def test_the_runway_line_in_its_three_shapes():
    assert "Content pool: 30 of 34 lessons unused — the first repeat is due Thu 2026-11-26" in _digest([])
    assert "lessons repeat from Sat 2026-10-03" in _digest(
        [], runway={"pool": 34, "unused": 0, "first_repeat": SAT})
    assert "Content pool: EMPTY" in _digest([], runway={"pool": 0, "unused": 0, "first_repeat": None})
    assert "Content pool: unreadable" in _digest([], runway=None)


def test_posts_waiting_for_an_unknown_outcome_answer_are_listed_without_text():
    esc = [_post("x", "queued", meta={"escalated_at": "2026-10-03T20:40:00+00:00"}),
           _post("x", "queued", meta={"escalated_at": None}), "junk"]
    text = _digest([], escalated=esc)
    assert "⚠️ Waiting for your answer (outcome unknown): 2" in text
    assert "  X · run 10-03 · since 10-03 16:40 ET" in text and "  X · run 10-03 · since ?" in text
    _assert_no_model_text(text)
    assert "Outcome unknown: unreadable" in _digest([], escalated=None)


def test_the_metrics_job_line():
    assert ("Metrics job: last completed 2026-10-05 · last attempt 10-05 06:01 ET · 4 posts written"
            in _digest([]))
    never = _digest([], metrics_job={"job": ds.JOB_METRICS, "enabled": False, "run_day": None,
                                     "last_run_at": "2026-10-05T10:01:00+00:00", "items_written": 0,
                                     "last_error": "MarketingRunError: boom\n" + TOKEN_LIKE})
    assert ("Metrics job: never completed · last attempt 10-05 06:01 ET · 0 posts written · DISABLED in "
            "notification_job_state · last error: MarketingRunError: boom 1234567890:***") in never
    assert "Metrics job: state unreadable" in _digest([], metrics_job=None)
    assert "never completed" in _digest([], metrics_job={"run_day": "garbage", "items_written": "9"})


def _metrics_job_row(run_day: Any, last_run_at: Any, last_error: Any) -> Dict[str, Any]:
    return {"job": ds.JOB_METRICS, "enabled": True, "run_day": run_day, "last_run_at": last_run_at,
            "items_written": 3, "last_error": last_error}


def test_a_successful_metrics_day_shows_its_note_and_a_failed_one_its_error():
    """`finish_scheduled_job` sets run_day to the ET day of last_run_at only on SUCCESS: a non-empty
    last_error on that day is a note the successful run left (a platform paused, say); on any other
    day it is the last attempt's error."""
    note = _digest([], metrics_job=_metrics_job_row("2026-10-05", "2026-10-05T10:01:00+00:00",
                                        "X reads paused until 10-12: 403 Forbidden"))
    assert ("Metrics job: last completed 2026-10-05 · last attempt 10-05 06:01 ET · 3 posts written · "
            "note: X reads paused until 10-12: 403 Forbidden") in note
    assert "last error" not in note
    failed = _digest([], metrics_job=_metrics_job_row("2026-10-04", "2026-10-05T10:01:00+00:00", "MarketingRunError: 503"))
    assert ("Metrics job: last completed 2026-10-04 · last attempt 10-05 06:01 ET · 3 posts written · "
            "last error: MarketingRunError: 503") in failed
    assert "note:" not in failed
    never = _digest([], metrics_job=_metrics_job_row(None, "2026-10-05T10:01:00+00:00", "boom"))
    assert "never completed" in never and "last error: boom" in never


def test_the_note_rule_compares_new_york_days_not_utc_days():
    # 03:30 UTC on 10-06 is still 23:30 EDT on 10-05: the day that succeeded
    late = _digest([], metrics_job=_metrics_job_row("2026-10-05", "2026-10-06T03:30:00+00:00", "Bluesky stopped for today"))
    assert "last attempt 10-05 23:30 ET" in late and "note: Bluesky stopped for today" in late
    # 04:30 UTC on 10-06 is 00:30 EDT on 10-06: a later attempt that did not succeed
    next_day = _digest([], metrics_job=_metrics_job_row("2026-10-05", "2026-10-06T04:30:00+00:00", "boom"))
    assert "last error: boom" in next_day


@pytest.mark.parametrize("last_run_at", [None, "", "garbage", "0001-01-01T00:00:00+00:00", 7])
def test_an_unreadable_attempt_time_is_never_called_a_note(last_run_at):
    text = _digest([], metrics_job=_metrics_job_row("2026-10-05", last_run_at, "boom\n" + TOKEN_LIKE))
    assert "last error: boom 1234567890:***" in text and "note:" not in text
    assert "last attempt" not in text and "posts written" not in text and TOKEN_SECRET not in text


@pytest.mark.parametrize("err, shown", [("   ", "(unreadable)"), ("\n\u2028", "(unreadable)"), (500, "500"),
                                        ({"a": 1}, "{'a': 1}")])
def test_a_non_empty_job_error_is_always_shown(err, shown):
    assert f"note: {shown}" in _digest([], metrics_job=_metrics_job_row("2026-10-05", "2026-10-05T10:01:00+00:00", err))


@pytest.mark.parametrize("empty", [None, ""])
def test_an_empty_job_error_shows_neither_label(empty):
    text = _digest([], metrics_job=_metrics_job_row("2026-10-05", "2026-10-05T10:01:00+00:00", empty))
    assert "note:" not in text and "last error" not in text


def test_a_ledger_time_the_new_york_clock_cannot_show_never_breaks_the_digest():
    """A hand-edited time at the edge of the calendar used to raise OverflowError out of compose_digest,
    so the week's digest failed all three attempts. Each such value now costs only its own field."""
    edge = "0001-01-01T00:00:00+00:00"
    esc = [_post("x", "queued", meta={"escalated_at": edge}),
           _post("x", "queued", meta={"escalated_at": "2026-10-03T20:40:00+00:00"})]
    measured = [_snapshot_post("bluesky", edge, 99), _snapshot_post("bluesky", "2026-10-05T10:00:00+00:00", 40)]
    text = _digest([], escalated=esc, measured=measured,
                   metrics_job=_metrics_job_row("2026-10-05", edge, None))
    assert "  X · run 10-03 · since ?" in text and "  X · run 10-03 · since 10-03 16:40 ET" in text
    assert "Followers: BLUESKY 40 (as of 10-05; no snapshot 6+ days older yet)" in text   # the edge snapshot ignored
    assert "Metrics job: last completed 2026-10-05" in text and "last attempt" not in text


def test_runs_of_the_week_with_skip_reasons_and_the_memory_peak():
    runs = [_run("published", run_date="2026-09-28", timings={"voiced_cgroup_peak_mb": 2587.3}),
            _run("skipped", run_date="2026-09-29", metadata={"skip_reason": "writer_unavailable"}),
            _run("failed", run_date="2026-10-01", stage="rendered", timings={"rendered_cgroup_peak_mb": 3301.0}),
            _run("skipped", run_date="2026-10-02", metadata={"skip_reason": "rest_day"}),
            _run("skipped", run_date="2026-10-03", metadata={"closed": {"reason": "all_rejected"}}),
            _run("failed", run_date="2026-10-04", stage="selected"),
            _run("published", run_date="not a date"), "junk"]
    text = _digest([], runs=runs)
    # `stage` is the LAST COMPLETED stage (migration 170): "failed after", never "failed at"
    assert ("Runs:\n• Mon 09-28 published\n• Tue 09-29 skipped — writer_unavailable\n• Thu 10-01 failed after "
            "rendered\n• Sat 10-03 skipped — all_rejected\n• Sun 10-04 failed after selected\n"
            "Worker memory peak: 3,301 MB (rendered, Thu 10-01) ⚠️ above 3,200 MB") in text
    assert "Fri 10-02" not in text and "failed at" not in text
    assert "Runs: unreadable" in _digest([], runs=None)
    early = _digest([], runs=[_run("failed", run_date="2026-09-28", stage="planned"),
                              _run("failed", run_date="2026-09-29", stage=None),
                              _run("skipped", run_date="2026-10-01", metadata={"closed": {"reason": "approved_unsent"}})])
    assert ("• Mon 09-28 failed before any stage completed\n• Tue 09-29 failed after ?\n"
            "• Thu 10-01 skipped — approved_unsent") in early


def test_a_300_post_week_full_of_emoji_still_fits_one_message():
    emoji_error = "🔥" * 400 + " " + TOKEN_LIKE
    posts = []
    for i in range(300):
        day = f"2026-09-{28 + i % 3}"
        kind = i % 3
        if kind == 0:
            posts.append(_post("x", "failed", run_date=day, last_error=emoji_error,
                               meta={"publish": {"category": "server"}}))
        elif kind == 1:
            posts.append(_post("bluesky", "rejected", run_date=day,
                               meta={"review": {"decision": "rejected", "reason": "tone"}}))
        else:
            posts.append(_post(f"p{i:03d}", "published", run_date=day,
                               metrics={"status": "ok", "last": {"likes": i, "reposts": 1}},
                               external_url="https://example.invalid/" + "📈" * 300))
    escalated = [_post("x", "queued", meta={"escalated_at": "2026-10-03T20:40:00+00:00"}) for _ in range(20)]
    spend = {f"op_{i:03d}": i for i in range(300)}
    hits = [{"campaign": f"c{i:03d}", "day": "2026-09-30", "hits": i} for i in range(300)]
    s = _summary(posts, escalated=escalated, spend=spend, link_hits=hits,
                 measured=[_snapshot_post(f"p{i:03d}", "2026-10-05T10:00:00+00:00", i) for i in range(300)])
    text = ds.compose_digest(s)
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    text.encode("utf-16-le")   # never a half emoji
    assert TOKEN_SECRET not in text
    _assert_no_model_text(text)
    # rows were capped before anything was cut
    assert "… and" in text


def test_a_report_that_cannot_be_capped_is_cut_at_a_line_break_within_the_limit():
    s = _summary([])
    s["runs"] = [(date(2026, 9, 28) + timedelta(days=i % 7), "🔥" * 30 + f" |row {i}|") for i in range(2000)]
    text = ds.compose_digest(s)
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    assert text.endswith(ds._CUT_MARKER)
    body = text[: -len(ds._CUT_MARKER)]
    assert re.search(r" \|row \d+\|$", body)    # it ends on a whole line, never half of one
    assert utf16_len(body) > ds.MAX_DIGEST_UNITS // 2
    text.encode("utf-16-le")


def test_compose_is_pure_and_deterministic():
    s = _summary(_full_week())
    before = copy.deepcopy(s)
    assert ds.compose_digest(s) == ds.compose_digest(s)
    assert s == before


@pytest.mark.parametrize("bad", [None, 7, "x", [1, 2]])
def test_junk_rows_in_the_post_list_are_skipped(bad):
    s = _summary([bad, _post("x", "published")])
    assert s["live"] == 1


@pytest.mark.parametrize("reason", [["tone"], {"tone": 1}, {"a"}, 7, None, "Tone", "tone\n"])
def test_a_hand_edited_reject_reason_counts_as_no_reason_and_never_crashes(reason):
    s = _summary([_post("x", "rejected", meta={"review": {"decision": "rejected", "reason": reason}})])
    assert s["rejections"]["by_reason"] == {"none": 1}
    assert "Rejected 1: no reason 1" in ds.compose_digest(s)


@pytest.mark.parametrize("junk", [7, "rows", {"a": 1}, object()])
def test_a_section_of_the_wrong_type_reads_as_unreadable(junk):
    s = _summary([], runs=junk, measured=junk, link_hits=junk, escalated=junk)
    assert (s["runs"], s["followers"], s["taps"], s["escalated"]) == (None, None, None, None)
    s = _summary([], spend=[1, 2], runway="soon", metrics_job=["x"])
    assert (s["spend"], s["runway"], s["metrics_job"]) == (None, None, None)
    text = ds.compose_digest(s)
    assert "X spend: unreadable" in text and "Content pool: unreadable" in text
    assert "Metrics job: state unreadable" in text
    assert ds.summarize_week(now=NOW, week_start=WEEK_START, week_end=WEEK_END, posts=None)["live"] == 0


def test_count_reader_omits_everything_that_is_not_a_plain_count():
    for bad in (None, -1, True, False, 1.0, 1.5, float("nan"), float("inf"), "12", "", [3], {"n": 3}, 10 ** 15):
        assert ds._count(bad) is None
    for good in (0, 1, 10 ** 15 - 1):
        assert ds._count(good) == good
    assert math.isnan(float("nan"))


# ── gather: the reads, against the in-memory ledger ───────────────────────────


class _Exploding:
    """A ledger read that fails (a PostgREST 5xx) — wraps one service method."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def __call__(self, *_a: Any, **_k: Any) -> Any:
        raise self.exc


@pytest.fixture
def platforms_forbidden(monkeypatch):
    """The digest and the health check call NO platform: every client fails the test if used."""
    import httpx

    from app.integrations import bluesky, upload_post, x_api

    calls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError(f"a platform was called: {request.url}")

    for mod in (x_api, bluesky):
        monkeypatch.setattr(mod, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    if hasattr(upload_post, "_client"):
        monkeypatch.setattr(upload_post, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return calls


@pytest.fixture
def digest_ledger(monkeypatch, platforms_forbidden):
    fake = FakeSupabase()
    fake.tables[ds._JOB_STATE_TABLE] = _Table([("job",)], {"enabled": True}, generated_id=False)
    svc = mrs.MarketingRunService(supabase=fake)
    svc.fake = fake  # type: ignore[attr-defined]
    monkeypatch.setattr(ds, "get_marketing_run_service", lambda: svc)
    monkeypatch.setattr(settings, "MARKETING_X_MONTHLY_BUDGET_USD", 2.0)
    monkeypatch.setattr(settings, "MARKETING_ENABLED", True)
    monkeypatch.setattr(settings, "MARKETING_METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", "")
    from app.services.marketing import content_pool
    monkeypatch.setattr(content_pool, "eligible_keys", lambda: [f"journey:{i}" for i in range(10)])
    return svc


def _seed_week(svc) -> Dict[str, Any]:
    t = svc.fake.tables
    inside = _post("x", "published", external_id=X_ID, created="2026-10-03T20:30:00+00:00",
                   published_at="2026-10-03T21:00:00+00:00",
                   metrics={"v": 1, "status": "ok", "last": {"likes": 4, "impressions": 90},
                            "account": {"followers": 12, "at": "2026-10-05T10:00:00+00:00"}},
                   meta={"charges": [{"at": "2026-10-03T20:59:00+00:00", "op": "x_create", "micros": 15_000},
                                     {"at": "2026-10-05T10:00:00+00:00", "op": "x_metrics_read", "micros": 5_000},
                                     {"at": "2026-09-30T10:00:00+00:00", "op": "x_create", "micros": 15_000}]},
                   updated_at="2026-10-05T10:00:00+00:00")
    before = _post("bluesky", "published", run_date="2026-09-26", created="2026-09-26T20:30:00+00:00",
                   published_at="2026-09-26T21:00:00+00:00",
                   metrics={"account": {"followers": 7, "at": "2026-09-28T10:00:00+00:00"}})
    after = _post("x", "pending_review", run_date="2026-10-05", created="2026-10-05T04:00:00+00:00")
    escalated = _post("x", "queued", created="2026-10-03T20:30:01+00:00",
                      meta={"escalated_at": "2026-10-03T20:40:00+00:00"})
    # #15: a queued post that is NOT waiting for the owner (an Upload-Post job still processing). It is
    # dated OUTSIDE the digest week, so only the escalated query's own `escalated_at IS NOT NULL` filter
    # (that read has no date bound) keeps it out of "waiting for your answer" — no week count moves.
    processing = _post("youtube", "queued", run_date="2026-09-26", created="2026-09-26T20:30:00+00:00",
                       meta={"publish": {"state": "submitted", "upload_post": {"request_id": "r-1"}}},
                       claimed_at="2026-09-26T21:00:00+00:00")
    t[mrs.POSTS].rows += [inside, before, after, escalated, processing]
    t[mrs.RUNS].rows += [_run("media_ready", run_date="2026-10-03", timings={"voiced_cgroup_peak_mb": 2500.0}),
                         _run("skipped", run_date="2026-10-02", metadata={"skip_reason": "rest_day"}),
                         _run("published", run_date="2026-09-27")]           # last week: not in this digest
    t[mrs.SCRIPTS].rows += [{"run_id": str(uuid.uuid4()), "run_date": "2026-10-01", "source_ref": "journey:1"},
                            {"run_id": str(uuid.uuid4()), "run_date": "2026-10-03", "source_ref": "journey:2"},
                            {"run_id": str(uuid.uuid4()), "run_date": "2026-10-02", "source_ref": None}]
    t[mrs.LINK_HITS].rows += [{"campaign": "bluesky", "day": "2026-10-03", "hits": 2},
                              {"campaign": "x", "day": "2026-09-27", "hits": 50}]   # last week
    # #16: the day-job ledger holds every scheduled job — another job's row comes FIRST, so a read
    # that lost its `job = marketing_metrics_daily` filter would report it.
    t[ds._JOB_STATE_TABLE].rows.append(_WHALE_JOB_ROW.copy())
    t[ds._JOB_STATE_TABLE].rows.append({"job": ds.JOB_METRICS, "enabled": True, "run_day": "2026-10-05",
                                        "claim_at": None, "last_run_at": "2026-10-05T10:00:00+00:00",
                                        "last_error": None, "items_written": 2})
    return {"inside": inside, "before": before, "after": after, "escalated": escalated,
            "processing": processing}


#: Another scheduled job's state row (whale hydration), different in every field the digest prints.
_WHALE_JOB_ROW = {"job": "whale_hydration_full", "enabled": True, "run_day": "2026-09-01", "claim_at": None,
                  "last_run_at": "2026-09-01T07:00:00+00:00", "last_error": "WHALE-SENTINEL boom",
                  "items_written": 77}


@pytest.mark.asyncio
async def test_gather_reads_only_our_ledger_for_the_previous_week(digest_ledger, platforms_forbidden):
    _seed_week(digest_ledger)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert (report["week_start"], report["week_end"]) == (WEEK_START, WEEK_END)
    # the week's posts only: the escalated row was created inside it too; last week's and today's are out
    assert report["live"] == 2 and report["status_totals"] == {"published": 1, "queued": 1}
    assert report["engagement"]["x"]["totals"] == {"likes": 4, "impressions": 90}
    assert report["followers"]["x"]["followers"] == 12 and report["followers"]["bluesky"]["followers"] == 7
    assert report["taps"]["rows"] == [("bluesky", 2)]
    # spend: this UTC month only, by op (the 09-30 charge is last month)
    assert report["spend"]["by_op"] == [("x_create", 15_000), ("x_metrics_read", 5_000)]
    assert report["spend"]["budget"] == 2_000_000
    # only the escalated post waits for an answer — never the Upload-Post job still processing (#15)
    assert report["escalated"] == [("X", "10-03", "10-03 16:40 ET")]
    # runway: 10 eligible, 2 picked (rest days never count), today (Mon) not picked yet — the 8 unused
    # go out Mon 10-05 … Sat 10-17 and the 9th posting day repeats
    assert report["runway"] == {"pool": 10, "unused": 8, "first_repeat": date(2026, 10, 19)}
    assert report["metrics_job"]["run_day"] == "2026-10-05" and report["metrics_enabled"] is True
    # the metrics job's OWN row, though another job's row comes first in the table (#16)
    assert report["metrics_job"]["job"] == ds.JOB_METRICS and report["metrics_job"]["items_written"] == 2
    assert report["runs"] == [(date(2026, 9, 28), "no run (a posting day)"),
                              (date(2026, 9, 29), "no run (a posting day)"),
                              (date(2026, 10, 1), "no run (a posting day)"), (date(2026, 10, 3), "media_ready")]
    assert report["store"] == "prelaunch"
    text = ds.compose_digest(report)
    _assert_no_model_text(text)
    assert "\nMetrics job: last completed 2026-10-05 · last attempt 10-05 06:00 ET · 2 posts written" in text
    assert "WHALE-SENTINEL" not in text and "77 posts" not in text and "2026-09-01" not in text
    assert "⚠️ Waiting for your answer (outcome unknown): 1\n  X · run 10-03 · since 10-03 16:40 ET" in text
    assert "YOUTUBE · run 09-26" not in text
    assert platforms_forbidden == []


@pytest.mark.asyncio
async def test_gather_counts_todays_pick_once_it_is_made(digest_ledger):
    _seed_week(digest_ledger)
    digest_ledger.fake.tables[mrs.SCRIPTS].rows.append(
        {"run_id": str(uuid.uuid4()), "run_date": "2026-10-05", "source_ref": "journey:3"})
    report = await ds.gather_digest(digest_ledger, NOW)
    # 7 unused from Tue 10-06: Tue, Thu, Sat, Mon, Tue, Thu, Sat — the 8th posting day repeats
    assert report["runway"] == {"pool": 10, "unused": 7, "first_repeat": date(2026, 10, 19)}


@pytest.mark.asyncio
async def test_gather_follows_the_new_york_week_across_the_november_change(digest_ledger):
    t = digest_ledger.fake.tables[mrs.POSTS].rows
    edges = {
        "2026-10-26T03:59:59+00:00": False,   # Sun 10-25 23:59:59 EDT — the week before
        "2026-10-26T04:00:00+00:00": True,    # Mon 10-26 00:00 EDT
        "2026-11-02T04:59:59+00:00": True,    # Sun 11-01 23:59:59 EST — the extra hour is in
        "2026-11-02T05:00:00+00:00": False,   # Mon 11-02 00:00 EST — this week
    }
    for created in edges:
        t.append(_post("x", "published", run_date=created[:10], created=created))
    report = await ds.gather_digest(digest_ledger, datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc))
    assert (report["week_start"], report["week_end"]) == (date(2026, 10, 26), date(2026, 11, 1))
    assert report["live"] == sum(edges.values()) == 2


@pytest.mark.asyncio
async def test_a_core_read_failure_fails_the_attempt(digest_ledger, monkeypatch):
    monkeypatch.setattr(digest_ledger, "list_posts_created_between", _Exploding(mrs.MarketingRunError("posts 503")))
    with pytest.raises(mrs.MarketingRunError):
        await ds.gather_digest(digest_ledger, NOW)
    monkeypatch.undo()


@pytest.mark.asyncio
@pytest.mark.parametrize("method, section", [
    ("link_hits_between", "taps"), ("spend_by_op_since", "spend"), ("list_posts_filtered", "escalated"),
    ("recent_source_refs", "runway"), ("list_measurable_posts", "followers"), ("list_runs_between", "runs"),
])
async def test_a_secondary_read_failure_says_unreadable_never_zero(digest_ledger, monkeypatch, caplog, method, section):
    _seed_week(digest_ledger)
    monkeypatch.setattr(digest_ledger, method, _Exploding(mrs.MarketingRunError(f"{method} 503")))
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report[section] is None
    assert report["live"] == 2   # the rest of the digest is intact
    assert "unreadable" in ds.compose_digest(report)
    assert any(f"{method} 503" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.asyncio
async def test_an_unreadable_metrics_job_state_and_a_failing_pool_degrade(digest_ledger, monkeypatch):
    _seed_week(digest_ledger)
    del digest_ledger.fake.tables[ds._JOB_STATE_TABLE]
    from app.services.marketing import content_pool

    def broken():
        raise RuntimeError("corpus unreadable")

    monkeypatch.setattr(content_pool, "eligible_keys", broken)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["metrics_job"] is None and report["runway"] is None
    text = ds.compose_digest(report)
    assert "Metrics job: state unreadable" in text and "Content pool: unreadable" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled, metrics, on", [(True, True, True), (False, True, False), (True, False, False)])
async def test_metrics_count_as_on_only_with_both_switches(digest_ledger, monkeypatch, enabled, metrics, on):
    monkeypatch.setattr(settings, "MARKETING_ENABLED", enabled)
    monkeypatch.setattr(settings, "MARKETING_METRICS_ENABLED", metrics)
    assert (await ds.gather_digest(digest_ledger, NOW))["metrics_enabled"] is on


@pytest.mark.asyncio
@pytest.mark.parametrize("others", [[], [_WHALE_JOB_ROW]])
async def test_a_metrics_job_that_never_ran_has_a_default_state(digest_ledger, others):
    """Its row exists only after its first due tick; the table holding OTHER jobs' rows (#16) must not
    stand in for it."""
    digest_ledger.fake.tables[ds._JOB_STATE_TABLE].rows.extend(dict(r) for r in others)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["metrics_job"] == {"job": ds.JOB_METRICS, "enabled": True, "run_day": None}
    text = ds.compose_digest(report)
    assert "Metrics job: never completed" in text and "WHALE-SENTINEL" not in text


# ── the cycles: the real run_day_job, an in-memory day-job ledger, a fake Telegram ──


class DigestJobs:
    """`notification_job_state` with migration 147's claim / finish semantics, on the SIMULATED clock
    (`now` — the real `claimed_scheduled_job` stamps the wall clock, which a test cannot move, and stamps
    `last_run_at` with the CLAIM's time: here the tick's `now`).

    `mirror`, when set, is the PostgREST fake's `notification_job_state` rows: every claim and finish is
    written there too, in the shape PostgREST answers (ISO strings), because the final word reads the
    nightly check's row with a direct select — the very row migration 147's RPCs write in production."""

    def __init__(self) -> None:
        self.now: datetime = NOW
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.claims: List[tuple] = []
        self.finishes: List[tuple] = []
        self.state_reads = 0
        self.unreadable = False
        self.mirror: Optional[List[Dict[str, Any]]] = None

    def _today(self, tz: str) -> date:
        return self.now.astimezone(ZoneInfo(tz)).date()

    def _row(self, job: str) -> Dict[str, Any]:
        return self.rows.setdefault(job, {"enabled": True, "run_day": None, "claim_at": None, "last_error": None,
                                          "items_written": 0, "last_run_at": None})

    def _sync(self, job: str) -> None:
        if self.mirror is None:
            return
        r = self.rows[job]

        def iso(v: Any) -> Any:
            return v.isoformat() if isinstance(v, (date, datetime)) else v

        self.mirror[:] = [x for x in self.mirror if x.get("job") != job] + [{
            "job": job, "enabled": r.get("enabled", True), "run_day": iso(r.get("run_day")),
            "claim_at": iso(r.get("claim_at")), "last_run_at": iso(r.get("last_run_at")),
            "last_error": r.get("last_error"), "items_written": r.get("items_written", 0)}]

    def claim_scheduled(self, job, *, timezone_name="UTC", now=None, stale_seconds=None) -> bool:
        row, today = self._row(job), self._today(timezone_name)
        stale = timedelta(seconds=900 if stale_seconds is None else int(stale_seconds))
        if (not row["enabled"] or row["run_day"] == today
                or (row["claim_at"] is not None and row["claim_at"] > self.now - stale)):
            return False
        row["claim_at"] = self.now
        self.claims.append((job, today))
        self._sync(job)
        return True

    def finish_scheduled(self, job, *, success, items=0, error=None, timezone_name="UTC", now=None) -> None:
        row = self._row(job)
        row.update({"claim_at": None, "last_error": error, "items_written": items, "last_run_at": self.now})
        if success:
            row["run_day"] = self._today(timezone_name)
        self.finishes.append((job, success, items, error))
        self._sync(job)

    def scheduled_job_state(self, job) -> Optional[Dict[str, Any]]:
        self.state_reads += 1
        if self.unreadable:
            return None
        row = self.rows.get(job)
        if row is None:
            return {"job": job, "run_day": None, "claim_at": None, "enabled": True}
        return {"job": job, "run_day": row["run_day"], "claim_at": row["claim_at"], "enabled": row["enabled"]}


@pytest.fixture
def digest_jobs(monkeypatch):
    fake = DigestJobs()
    monkeypatch.setattr(notification_jobs, "claim_scheduled", fake.claim_scheduled)
    monkeypatch.setattr(notification_jobs, "finish_scheduled", fake.finish_scheduled)
    monkeypatch.setattr(notification_jobs, "scheduled_job_state", fake.scheduled_job_state)
    monkeypatch.setattr(ms, "_DAY_JOBS", {})
    return fake


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def bot(monkeypatch, tg, digest_jobs, digest_ledger):
    # The day-job claims and finishes land in the ledger's notification_job_state, where the final word
    # reads the nightly check's row (one table in production).
    digest_jobs.mirror = digest_ledger.fake.tables[ds._JOB_STATE_TABLE].rows
    # The digest's own switch (fail-closed, default off) — checked inside digest_cycle too (K4).
    monkeypatch.setattr(settings, "MARKETING_DIGEST_ENABLED", True)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", OWNER)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(rs, "_rate_limited_until", 0.0)
    monkeypatch.setattr(ds, "_PACER", rs._Pacer())
    return tg


_TG_429 = (429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7",
                 "parameters": {"retry_after": 7}})
_TG_400 = (400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
_TG_500 = (500, {"ok": False, "error_code": 500, "description": "Internal Server Error"})


async def _digest_at(digest_jobs: DigestJobs, at: datetime) -> Dict[str, int]:
    digest_jobs.now = at
    return await ds.digest_cycle(now=at)


async def _health_at(digest_jobs: DigestJobs, at: datetime) -> Dict[str, int]:
    digest_jobs.now = at
    return await ds.health_cycle(now=at)


def _sends(tg: FakeTelegram) -> List[Dict[str, Any]]:
    return tg.of("sendMessage")


@pytest.mark.asyncio
async def test_an_unconfigured_bot_claims_nothing_and_reads_nothing(monkeypatch, tg, digest_jobs, digest_ledger):
    monkeypatch.setattr(settings, "MARKETING_DIGEST_ENABLED", True)     # so the bot check is what stops it
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    assert await _digest_at(digest_jobs, NOW) == {"sent": 0, "failed": 0, "rate_limited": 0}
    assert await _health_at(digest_jobs, _et(2026, 10, 3, 22, 30)) == {"checked": 0, "sent": 0, "failed": 0,
                                                                "rate_limited": 0}
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 30)) == {"checked": 0, "sent": 0, "failed": 0,
                                                                "rate_limited": 0}   # the final word too
    assert digest_jobs.state_reads == 0 and digest_jobs.claims == [] and tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("off", [False, None, 0])
async def test_the_digest_switch_off_means_no_io_even_when_called_directly(bot, digest_jobs, digest_ledger, monkeypatch,
                                                                           off):
    """K4: the publisher tick gates the digest on MARKETING_DIGEST_ENABLED, and so does digest_cycle itself —
    a direct call at a due time with the bot configured reads, claims and sends nothing."""
    _seed_week(digest_ledger)
    monkeypatch.setattr(settings, "MARKETING_DIGEST_ENABLED", off)

    def no_read(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the ledger was read with the digest switched off")

    monkeypatch.setattr(digest_ledger, "list_posts_created_between", no_read)
    for at in (_et(2026, 10, 5, 9, 0), _et(2026, 10, 6, 9, 0)):
        assert await _digest_at(digest_jobs, at) == {"sent": 0, "failed": 0, "rate_limited": 0}
    assert digest_jobs.state_reads == 0 and digest_jobs.claims == [] and _sends(bot) == []
    assert ds.JOB_DIGEST not in ms._DAY_JOBS          # not even an in-process attempt slot


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [_et(2026, 10, 5, 8, 59), _et(2026, 10, 7, 9, 30), _et(2026, 10, 4, 23, 0),
                                datetime(2026, 11, 2, 13, 59, tzinfo=timezone.utc)])
async def test_the_digest_takes_no_claim_and_reads_nothing_when_not_due(bot, digest_jobs, at):
    assert await _digest_at(digest_jobs, at) == {"sent": 0, "failed": 0, "rate_limited": 0}
    assert digest_jobs.state_reads == 0 and digest_jobs.claims == [] and bot.calls == []


@pytest.mark.asyncio
async def test_the_digest_goes_out_once_on_monday_as_one_plain_text_message(bot, digest_jobs, digest_ledger, platforms_forbidden):
    _seed_week(digest_ledger)
    assert await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 0)) == {"sent": 1, "failed": 0, "rate_limited": 0}
    (msg,) = _sends(bot)
    assert msg["chat_id"] == OWNER and "parse_mode" not in msg and "reply_markup" not in msg
    assert msg["text"].startswith("📊 Caydex marketing — weekly digest\nMon 2026-09-28 → Sun 2026-10-04 (ET)")
    assert utf16_len(msg["text"]) <= ds.MAX_DIGEST_UNITS
    _assert_no_model_text(msg["text"])
    assert digest_jobs.finishes == [(ds.JOB_DIGEST, True, 1, None)]
    assert digest_jobs.rows[ds.JOB_DIGEST]["run_day"] == date(2026, 10, 5)
    # the rest of Monday: no I/O at all (done in this process)
    reads = digest_jobs.state_reads
    for minutes in (10, 20, 600):
        assert (await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 0) + timedelta(minutes=minutes)))["sent"] == 0
    assert digest_jobs.state_reads == reads and len(_sends(bot)) == 1
    # Tuesday's catch-up sees Monday's success and stays quiet (one state read, no claim)
    assert (await _digest_at(digest_jobs, _et(2026, 10, 6, 9, 0)))["sent"] == 0
    assert digest_jobs.state_reads == reads + 1 and len(digest_jobs.claims) == 1 and len(_sends(bot)) == 1
    assert platforms_forbidden == []


@pytest.mark.asyncio
async def test_a_failed_monday_is_caught_up_on_tuesday(bot, digest_jobs):
    bot.script["sendMessage"] = [_TG_400] * 3
    for minutes in (0, 10, 20, 30, 40):
        await _digest_at(digest_jobs, _et(2026, 10, 5, 9, minutes))
    # three claimed attempts, each left unsucceeded, then Monday is spent in this process
    assert [c[0] for c in digest_jobs.claims] == [ds.JOB_DIGEST] * 3
    assert all(success is False and "chat not found" in error for _j, success, _i, error in digest_jobs.finishes)
    assert digest_jobs.rows[ds.JOB_DIGEST]["run_day"] is None
    assert (await _digest_at(digest_jobs, _et(2026, 10, 6, 9, 0)))["sent"] == 1
    assert digest_jobs.rows[ds.JOB_DIGEST]["run_day"] == date(2026, 10, 6)
    assert _sends(bot)[-1]["text"].splitlines()[1] == "Mon 2026-09-28 → Sun 2026-10-04 (ET)"
    # …and Tuesday's success covers Wednesday too (not due anyway)
    assert (await _digest_at(digest_jobs, _et(2026, 10, 7, 9, 0)))["sent"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [_TG_500, httpx.ConnectError("down")])
async def test_a_send_failure_leaves_the_claim_unsucceeded_and_a_later_tick_retries(bot, digest_jobs, failure):
    bot.script["sendMessage"] = [failure]
    assert await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 0)) == {"sent": 0, "failed": 1, "rate_limited": 0}
    assert digest_jobs.finishes[-1][:2] == (ds.JOB_DIGEST, False) and digest_jobs.rows[ds.JOB_DIGEST]["run_day"] is None
    assert digest_jobs.rows[ds.JOB_DIGEST]["claim_at"] is None          # released, not parked
    assert (await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 10)))["sent"] == 1
    assert digest_jobs.finishes[-1] == (ds.JOB_DIGEST, True, 1, None)
    assert len(_sends(bot)) == 2   # the failed one and the one that went out


@pytest.mark.asyncio
async def test_a_telegram_429_sets_the_shared_back_off_and_no_claim_is_taken_while_it_is_open(bot, digest_jobs):
    bot.script["sendMessage"] = [_TG_429]
    before = time.monotonic()
    assert await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 0)) == {"sent": 0, "failed": 0, "rate_limited": 1}
    assert rs._rate_limited_until >= before + 7          # the SHARED back-off: the review sweep holds off too
    assert digest_jobs.finishes[-1][:2] == (ds.JOB_DIGEST, False) and "flood control" in digest_jobs.finishes[-1][3]
    claims, reads = len(digest_jobs.claims), digest_jobs.state_reads
    # while it is open: no claim, no state read, no send — for the digest AND the health check
    assert await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 10)) == {"sent": 0, "failed": 0, "rate_limited": 1}
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["rate_limited"] == 1
    assert (len(digest_jobs.claims), digest_jobs.state_reads, len(_sends(bot))) == (claims, reads, 1)
    rs._rate_limited_until = 0.0   # the back-off has passed
    assert (await _digest_at(digest_jobs, _et(2026, 10, 5, 9, 20)))["sent"] == 1


@pytest.mark.asyncio
async def test_the_digest_over_the_november_change(bot, digest_jobs, digest_ledger):
    t = digest_ledger.fake.tables[mrs.POSTS].rows
    t.append(_post("x", "published", run_date="2026-11-01", created="2026-11-02T04:30:00+00:00"))  # Sun 23:30 EST
    t.append(_post("x", "rejected", run_date="2026-11-02", created="2026-11-02T05:00:00+00:00"))   # Mon 00:00 EST
    assert (await _digest_at(digest_jobs, datetime(2026, 11, 2, 13, 59, tzinfo=timezone.utc)))["sent"] == 0
    assert digest_jobs.state_reads == 0
    assert (await _digest_at(digest_jobs, datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)))["sent"] == 1
    text = _sends(bot)[-1]["text"]
    assert text.splitlines()[1] == "Mon 2026-10-26 → Sun 2026-11-01 (ET)"
    assert "Posts: 1 live — published 1" in text and "rejected" not in text.split("Engagement")[0]
    assert digest_jobs.rows[ds.JOB_DIGEST]["run_day"] == date(2026, 11, 2)


@pytest.mark.asyncio
async def test_a_300_post_week_goes_out_as_exactly_one_message(bot, digest_jobs, digest_ledger):
    rows = digest_ledger.fake.tables[mrs.POSTS].rows
    for i in range(300):
        rows.append(_post(("x", "bluesky")[i % 2], ("failed", "rejected", "published")[i % 3],
                          run_date="2026-10-01", created=f"2026-10-01T20:{i % 60:02d}:00+00:00",
                          last_error="🔥" * 300 + " " + TOKEN_LIKE,
                          meta={"review": {"decision": "rejected", "reason": "weak"}},
                          metrics={"status": "ok", "last": {"likes": i, "reposts": 2}}))
    assert (await _digest_at(digest_jobs, NOW))["sent"] == 1
    (msg,) = _sends(bot)
    assert utf16_len(msg["text"]) <= ds.MAX_DIGEST_UNITS and "… and" in msg["text"]
    assert TOKEN_SECRET not in msg["text"]
    _assert_no_model_text(msg["text"])


@pytest.mark.asyncio
async def test_a_core_ledger_failure_fails_the_attempt_without_raising(bot, digest_jobs, digest_ledger, monkeypatch, caplog):
    monkeypatch.setattr(digest_ledger, "list_posts_created_between", _Exploding(mrs.MarketingRunError("posts 503")))
    caplog.set_level(logging.ERROR)
    assert await _digest_at(digest_jobs, NOW) == {"sent": 0, "failed": 1, "rate_limited": 0}
    assert _sends(bot) == [] and digest_jobs.finishes[-1][:2] == (ds.JOB_DIGEST, False)
    assert "posts 503" in digest_jobs.finishes[-1][3]
    assert any("marketing_digest_weekly" in r.getMessage() and r.exc_info for r in caplog.records)
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_a_disabled_or_unreadable_job_state_takes_no_claim(bot, digest_jobs):
    digest_jobs.rows[ds.JOB_DIGEST] = {"enabled": False, "run_day": None, "claim_at": None}
    assert (await _digest_at(digest_jobs, NOW))["sent"] == 0 and digest_jobs.claims == []
    digest_jobs.unreadable = True
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 0 and digest_jobs.claims == []
    assert _sends(bot) == []


@pytest.mark.asyncio
async def test_a_job_held_by_another_instance_is_left_alone(bot, digest_jobs):
    digest_jobs.rows[ds.JOB_DIGEST] = {"enabled": True, "run_day": None, "claim_at": NOW - timedelta(seconds=60)}
    assert (await _digest_at(digest_jobs, NOW))["sent"] == 0 and _sends(bot) == []


@pytest.mark.asyncio
async def test_the_cycles_never_raise(bot, digest_jobs, monkeypatch, caplog):
    async def boom(*_a, **_k):
        raise RuntimeError("runner exploded")

    monkeypatch.setattr(ms, "run_day_job", boom)
    caplog.set_level(logging.ERROR)
    assert (await _digest_at(digest_jobs, NOW))["failed"] == 1
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["failed"] == 1
    assert sum("runner exploded" in r.getMessage() for r in caplog.records) == 2


@pytest.mark.asyncio
async def test_cancellation_is_never_swallowed(bot, digest_jobs, monkeypatch):
    async def cancelled(*_a, **_k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(ds, "gather_digest", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await _digest_at(digest_jobs, NOW)
    # the claim was released as a failure on the way out (claimed_scheduled_job's shielded finish)
    assert digest_jobs.finishes[-1][:2] == (ds.JOB_DIGEST, False) and digest_jobs.rows[ds.JOB_DIGEST]["claim_at"] is None


# ── the run-health cycle ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("at", [
    _et(2026, 10, 3, 21, 59),     # Sat: before 22:00, and Friday (yesterday) was a rest day
    _et(2026, 10, 2, 15, 59),     # Fri, a rest day: Thursday's final word is not due before 16:00
    _et(2026, 10, 4, 15, 59),     # Sun: the same for Saturday's
    _et(2026, 10, 5, 21, 59),     # Mon: before 22:00, and Sunday was a rest day
    _et(2026, 10, 8, 21, 59),     # Thu: before 22:00, and Wednesday was a rest day
])
async def test_health_takes_no_claim_when_neither_job_is_due(bot, digest_jobs, at):
    assert (await _health_at(digest_jobs, at))["checked"] == 0
    assert digest_jobs.state_reads == 0 and digest_jobs.claims == [] and bot.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("at, day", [(_et(2026, 10, 2, 22, 30), date(2026, 10, 2)),     # Fri, a rest day
                                     (_et(2026, 10, 4, 23, 30), date(2026, 10, 4))])    # Sun, a rest day
async def test_a_rest_day_claims_only_the_final_word_never_the_nightly_check(bot, digest_jobs, at, day):
    await _health_at(digest_jobs, at)
    assert digest_jobs.claims == [(ds.JOB_HEALTH_FINAL, day)]


@pytest.mark.asyncio
async def test_health_alerts_once_per_et_day_about_a_failed_run(bot, digest_jobs, digest_ledger, platforms_forbidden):
    # A consistent state (#5): the voice stage completed and recorded its peak; ffmpeg (the render
    # stage) was killed, so `stage` stays at the LAST COMPLETED one — `voiced`.
    digest_ledger.fake.tables[mrs.RUNS].rows.append(
        _run("failed", run_date="2026-10-03", stage="voiced", attempts=6,
             last_error="ffmpeg exited 137\n" + TOKEN_LIKE + " " + "y" * 5000,
             timings={"voiced_cgroup_peak_mb": 3790.4}))
    assert await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0)) == {"checked": 1, "sent": 1, "failed": 0,
                                                               "rate_limited": 0}
    for at in (_et(2026, 10, 3, 22, 10), _et(2026, 10, 3, 23, 0), _et(2026, 10, 3, 23, 59)):
        assert (await _health_at(digest_jobs, at))["sent"] == 0
    (msg,) = _sends(bot)
    assert "parse_mode" not in msg and "reply_markup" not in msg
    first, second = msg["text"].split("\n")
    assert first.startswith("⚠️ Marketing run Sat 2026-10-03 FAILED after stage voiced (the last completed stage): "
                            "all 6 attempts used; this day's posts will not go out. Last error: ffmpeg exited 137 "
                            "1234567890:***")
    assert "stage voiced peaked at 3,790 MB" in second and TOKEN_SECRET not in msg["text"]
    assert len(msg["text"]) < 900
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] == date(2026, 10, 3)
    assert ds.JOB_HEALTH_FINAL not in digest_jobs.rows        # not due on a Saturday
    assert platforms_forbidden == []


@pytest.mark.asyncio
async def test_the_health_check_reads_the_attempts_cap_and_stale_window_from_settings(bot, digest_jobs, digest_ledger,
                                                                                      monkeypatch):
    digest_ledger.fake.tables[mrs.RUNS].rows.append(
        _run("in_progress", run_date="2026-10-03", stage="voiced", attempts=3,
             updated_at=_iso_of(_et(2026, 10, 3, 21, 0))))
    monkeypatch.setattr(settings, "MARKETING_MAX_RUN_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "MARKETING_RUN_STALE_SECONDS", 7200)
    await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0))
    assert "still running (attempt 3 of 3)" in _sends(bot)[0]["text"]       # 60 min < the 2 h window


# ── the final word, the next day from 16:00 ET ────────────────────────────────


def _seed_health_run(ledger_svc, run: Dict[str, Any]) -> Dict[str, Any]:
    ledger_svc.fake.tables[mrs.RUNS].rows.append(run)
    return run


@pytest.mark.asyncio
async def test_the_final_word_goes_out_once_the_next_day_from_16_et(bot, digest_jobs, digest_ledger,
                                                                    platforms_forbidden):
    """Saturday's run failed below the cap; the 22:04 check said a retry was due and a final word would
    follow; no retry ever touched it. Sunday from 16:00 ET the final word says so — once — under its OWN
    day claim."""
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-03", stage="voiced", attempts=2,
                                  last_error="writer 429\n" + TOKEN_LIKE, updated_at=_iso_of(_et(2026, 10, 3, 21, 40))))
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 22, 4)))["sent"] == 1          # the nightly check
    assert "attempt 2 of 6; the worker retries hourly" in _sends(bot)[0]["text"]
    sat, sun = date(2026, 10, 3), date(2026, 10, 4)
    assert digest_jobs.claims == [(ds.JOB_HEALTH, sat)]
    reads = digest_jobs.state_reads
    assert (await _health_at(digest_jobs, _et(2026, 10, 4, 15, 59)))["checked"] == 0
    assert digest_jobs.state_reads == reads and len(digest_jobs.claims) == 1
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 0)) == {"checked": 1, "sent": 1, "failed": 0,
                                                               "rate_limited": 0}
    msg = _sends(bot)[1]
    assert msg["text"].startswith("⚠️ Marketing run Sat 2026-10-03 is still FAILED after stage voiced (the last "
                                  "completed stage), attempt 2 of 6: no retry happened — check the marketing-worker "
                                  "cron")
    assert "parse_mode" not in msg and TOKEN_SECRET not in msg["text"] and "\n" not in msg["text"]
    assert digest_jobs.claims == [(ds.JOB_HEALTH, sat), (ds.JOB_HEALTH_FINAL, sun)]
    assert digest_jobs.finishes[-1] == (ds.JOB_HEALTH_FINAL, True, 1, None)
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] == sat         # Sunday is not a posting day: no check
    # never again the same ET day — no read, no claim, no send
    reads = digest_jobs.state_reads
    for at in (_et(2026, 10, 4, 16, 10), _et(2026, 10, 4, 20, 0), _et(2026, 10, 4, 23, 59)):
        assert await _health_at(digest_jobs, at) == {"checked": 0, "sent": 0, "failed": 0, "rate_limited": 0}
    assert digest_jobs.state_reads == reads and len(digest_jobs.claims) == 2 and len(_sends(bot)) == 2
    assert platforms_forbidden == []


@pytest.mark.asyncio
async def test_a_run_fine_at_22_gets_no_final_word_and_the_day_is_settled(bot, digest_jobs, digest_ledger):
    _seed_health_run(digest_ledger, _run("media_ready", run_date="2026-10-03", finished_at=_iso_of(_et(2026, 10, 3, 17, 5)),
                                  updated_at=_iso_of(_et(2026, 10, 3, 17, 5))))
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0)))["sent"] == 0          # the check saw it fine
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 0)) == {"checked": 1, "sent": 0, "failed": 0,
                                                               "rate_limited": 0}
    assert digest_jobs.finishes == [(ds.JOB_HEALTH, True, 0, None), (ds.JOB_HEALTH_FINAL, True, 0, None)]
    assert _sends(bot) == []
    await _health_at(digest_jobs, _et(2026, 10, 4, 17, 0))
    assert len(digest_jobs.claims) == 2


@pytest.mark.asyncio
async def test_a_recovery_after_the_22_alert_is_reported_the_next_day(bot, digest_jobs, digest_ledger):
    """The whole story: 22:00 says the 4th attempt was abandoned and the worker retries; the 23:15 tick
    finishes the run; Sunday 16:00 says it recovered and its posts wait for review."""
    run = _seed_health_run(digest_ledger, _run("in_progress", run_date="2026-10-03", stage="voiced", attempts=4,
                                        updated_at=_iso_of(_et(2026, 10, 3, 21, 0))))
    await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0))
    assert "attempt 4 of 6 was abandoned; the worker retries hourly" in _sends(bot)[0]["text"]
    run.update(status="media_ready", stage="assets_ready", attempts=5, finished_at=_iso_of(_et(2026, 10, 3, 23, 41)),
               updated_at=_iso_of(_et(2026, 10, 3, 23, 41)))
    await _health_at(digest_jobs, _et(2026, 10, 4, 16, 0))
    assert _sends(bot)[1]["text"] == ("✅ Marketing run Sat 2026-10-03 recovered at 10-03 23:41 ET — its posts are "
                                      "waiting for review.")
    assert [c[0] for c in digest_jobs.claims] == [ds.JOB_HEALTH, ds.JOB_HEALTH_FINAL]


@pytest.mark.asyncio
async def test_the_final_word_and_the_nightly_alert_in_one_tick_go_out_in_day_order(bot, digest_jobs, digest_ledger):
    """Monday's 22:00 check reported a failure with a retry due. Tuesday 22:00 with the web down since before
    16:00: Monday's final word and Tuesday's alert are both due — two messages, each under its own claim
    for the same ET day, yesterday's first. The final word reads Monday's check BEFORE Tuesday's claims
    the same row: the other order would make it "a later day's check" and repeat Monday's verdict."""
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-05", stage="selected", attempts=3,
                                  updated_at=_iso_of(_et(2026, 10, 5, 21, 30))))
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 1          # Monday's check
    counters = await _health_at(digest_jobs, _et(2026, 10, 6, 22, 0))
    assert counters == {"checked": 2, "sent": 2, "failed": 0, "rate_limited": 0}
    first, second = (m["text"] for m in _sends(bot)[1:])
    assert first.startswith("⚠️ Marketing run Mon 2026-10-05 is still FAILED")
    assert second.startswith("⚠️ Marketing: no run for Tue 2026-10-06")
    mon, tue = date(2026, 10, 5), date(2026, 10, 6)
    assert digest_jobs.claims == [(ds.JOB_HEALTH, mon), (ds.JOB_HEALTH_FINAL, tue), (ds.JOB_HEALTH, tue)]
    assert digest_jobs.rows[ds.JOB_HEALTH_FINAL]["run_day"] == tue and digest_jobs.rows[ds.JOB_HEALTH]["run_day"] == tue
    # neither repeats that day
    await _health_at(digest_jobs, _et(2026, 10, 6, 23, 0))
    assert len(_sends(bot)) == 3 and len(digest_jobs.claims) == 3


@pytest.mark.asyncio
async def test_a_final_job_that_raises_never_stops_the_nightly_alert(bot, digest_jobs, digest_ledger, monkeypatch,
                                                                     caplog):
    real = digest_ledger.get_run_for_date

    async def flaky(run_date: Any) -> Any:
        if run_date == date(2026, 10, 5):
            raise mrs.MarketingRunError("runs 503")
        return await real(run_date)

    monkeypatch.setattr(digest_ledger, "get_run_for_date", flaky)
    caplog.set_level(logging.ERROR)
    counters = await _health_at(digest_jobs, _et(2026, 10, 6, 22, 0))
    assert counters == {"checked": 1, "sent": 1, "failed": 1, "rate_limited": 0}
    (msg,) = _sends(bot)
    assert msg["text"].startswith("⚠️ Marketing: no run for Tue 2026-10-06")
    assert digest_jobs.finishes[0][:2] == (ds.JOB_HEALTH_FINAL, False) and "runs 503" in digest_jobs.finishes[0][3]
    assert digest_jobs.finishes[1] == (ds.JOB_HEALTH, True, 1, None)
    assert any("marketing_run_health_final" in r.getMessage() and r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_a_429_on_the_final_word_holds_the_nightly_alert_off_without_a_claim(bot, digest_jobs, digest_ledger):
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-05", attempts=3, updated_at=_iso_of(_et(2026, 10, 5, 21, 0))))
    bot.script["sendMessage"] = [_TG_429]
    counters = await _health_at(digest_jobs, _et(2026, 10, 6, 22, 0))
    assert counters == {"checked": 1, "sent": 0, "failed": 0, "rate_limited": 1}
    assert digest_jobs.claims == [(ds.JOB_HEALTH_FINAL, date(2026, 10, 6))]      # the alert took no claim
    rs._rate_limited_until = 0.0
    counters = await _health_at(digest_jobs, _et(2026, 10, 6, 22, 10))
    assert counters["sent"] == 2 and len(_sends(bot)) == 3


@pytest.mark.asyncio
async def test_the_final_word_follows_the_et_day_across_the_november_change(bot, digest_jobs, digest_ledger):
    _seed_health_run(digest_ledger, _run("in_progress", run_date="2026-10-31", stage="scripted", attempts=6,
                                  updated_at=_iso_of(datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc))))  # Sat 21:30 EDT
    # Sat 22:00 EDT: the last allowed attempt is still alive (30 min < the 45-min window)
    assert (await _health_at(digest_jobs, datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)))["sent"] == 1
    assert "still running (attempt 6 of 6)" in _sends(bot)[0]["text"]
    assert (await _health_at(digest_jobs, datetime(2026, 11, 1, 20, 59, tzinfo=timezone.utc)))["checked"] == 0
    assert (await _health_at(digest_jobs, datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)))["sent"] == 1
    assert _sends(bot)[1]["text"] == ("⚠️ Marketing run Sat 2026-10-31 never finished — the day is lost: in_progress "
                                      "after stage scripted (the last completed stage), attempt 6 of 6, untouched "
                                      "since 10-31 21:30 ET. Check the marketing-worker cron and its last logs.")
    assert digest_jobs.rows[ds.JOB_HEALTH_FINAL]["run_day"] == date(2026, 11, 1)


@pytest.mark.asyncio
async def test_the_health_cycle_never_raises_on_an_unreadable_tick_time(bot, digest_jobs, caplog):
    caplog.set_level(logging.ERROR)
    assert await ds.health_cycle(now="22:00") == {"checked": 0, "sent": 0, "failed": 1, "rate_limited": 0}
    assert digest_jobs.claims == [] and digest_jobs.state_reads == 0 and _sends(bot) == []
    assert any("unreadable tick time" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_no_final_word_after_a_rest_day_or_without_a_run(bot, digest_jobs, digest_ledger):
    for at in (_et(2026, 10, 5, 16, 30), _et(2026, 10, 3, 16, 30), _et(2026, 10, 8, 18, 0)):   # Mon, Sat, Thu
        assert (await _health_at(digest_jobs, at))["checked"] == 0
    assert digest_jobs.claims == [] and digest_jobs.state_reads == 0
    # Saturday 22:00 says "no run"; Sunday's final word has nothing to add — that alert was final
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0)))["sent"] == 1
    assert _sends(bot)[0]["text"].startswith("⚠️ Marketing: no run for Sat 2026-10-03, a posting day")
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 0)) == {"checked": 1, "sent": 0, "failed": 0,
                                                               "rate_limited": 0}
    assert len(_sends(bot)) == 1 and digest_jobs.finishes[-1] == (ds.JOB_HEALTH_FINAL, True, 0, None)


# re-review r2.0: the final word is judged against the nightly check's OWN time, read from its job state


@pytest.mark.asyncio
async def test_r20a_a_failure_at_22_03_judged_at_22_07_is_not_repeated(bot, digest_jobs, digest_ledger):
    """Saturday's 6th attempt, claimed at 21:15, fails at 22:03; the first tick after 22:00 is 22:07 and its
    check reports the day lost. Sunday's final word must not say it again."""
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-03", stage="voiced", attempts=6,
                                         started_at=_iso_of(_et(2026, 10, 3, 21, 15)),
                                         updated_at=_iso_of(_et(2026, 10, 3, 22, 3)), last_error="ffmpeg exited 137"))
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 22, 7)))["sent"] == 1
    assert "all 6 attempts used; this day's posts will not go out" in _sends(bot)[0]["text"]
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 5)) == {"checked": 1, "sent": 0, "failed": 0,
                                                                  "rate_limited": 0}
    assert len(_sends(bot)) == 1
    assert digest_jobs.finishes[-1] == (ds.JOB_HEALTH_FINAL, True, 0, None)


@pytest.mark.asyncio
async def test_r20b_a_run_ready_at_22_04_and_judged_fine_at_22_07_is_not_a_recovery(bot, digest_jobs, digest_ledger):
    """A late run reaches media_ready at 22:04; the 22:07 check finds nothing wrong and says nothing — so the
    owner was never told the day was in trouble, and Sunday must not announce a recovery."""
    _seed_health_run(digest_ledger, _run("media_ready", run_date="2026-10-03", stage="assets_ready", attempts=1,
                                         started_at=_iso_of(_et(2026, 10, 3, 21, 15)),
                                         finished_at=_iso_of(_et(2026, 10, 3, 22, 4)),
                                         updated_at=_iso_of(_et(2026, 10, 3, 22, 4))))
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 22, 7)))["sent"] == 0
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 5)) == {"checked": 1, "sent": 0, "failed": 0,
                                                                  "rate_limited": 0}
    assert _sends(bot) == []


@pytest.mark.asyncio
async def test_r20c_a_nightly_check_that_never_went_out_leaves_the_failed_day_to_the_final_word(
        bot, digest_jobs, digest_ledger, caplog):
    """Telegram answers 500 to all three nightly attempts (22:00, 22:10, 22:20): the check never succeeds,
    so its run_day stays empty. The run failed at the cap at 21:40 and never changes. Sunday's final word
    reports it — the owner hears about the failed posting day once, never not at all."""
    caplog.set_level(logging.INFO, logger=ds.__name__)
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-03", stage="voiced", attempts=6,
                                         updated_at=_iso_of(_et(2026, 10, 3, 21, 40)), last_error="boom"))
    bot.script["sendMessage"] = [_TG_500] * 3
    for minutes in (0, 10, 20, 30):
        await _health_at(digest_jobs, _et(2026, 10, 3, 22, minutes))
    assert len(_sends(bot)) == 3 and digest_jobs.rows[ds.JOB_HEALTH]["run_day"] is None
    assert await _health_at(digest_jobs, _et(2026, 10, 4, 16, 5)) == {"checked": 1, "sent": 1, "failed": 0,
                                                                  "rate_limited": 0}
    assert _sends(bot)[3]["text"] == ("⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage voiced (the last "
                                      "completed stage): all 6 attempts used. This day's posts will not go out. "
                                      "Last error: boom.")
    assert any("no nightly check went out" in r.getMessage() for r in caplog.records)
    # once: the rest of Sunday is quiet
    await _health_at(digest_jobs, _et(2026, 10, 4, 16, 15))
    assert len(_sends(bot)) == 4


@pytest.mark.asyncio
async def test_a_day_with_no_run_whose_nightly_check_never_went_out_is_reported_by_the_final_word(
        bot, digest_jobs, digest_ledger):
    bot.script["sendMessage"] = [_TG_500] * 3
    for minutes in (0, 10, 20):
        await _health_at(digest_jobs, _et(2026, 10, 3, 22, minutes))
    assert all(m["text"].startswith("⚠️ Marketing: no run for Sat 2026-10-03") for m in _sends(bot))
    assert (await _health_at(digest_jobs, _et(2026, 10, 4, 16, 0)))["sent"] == 1
    assert _sends(bot)[3]["text"].startswith("⚠️ Marketing: no run for Sat 2026-10-03, a posting day — the worker "
                                             "never claimed it, so nothing was posted for this day (the nightly "
                                             "check never went out).")


@pytest.mark.parametrize("nightly, said", [
    (ds.NIGHTLY_NEVER, "(the nightly check never went out)"),
    (ds.NIGHTLY_LATER_DAY, "(the nightly check's record no longer says whether its alert went out)"),
])
def test_a_no_run_final_word_claims_only_what_the_nightly_record_proves(monkeypatch, nightly, said):
    """Round-3 re-review r1.0: with no check time the final word reports a missing run either way, but only
    NIGHTLY_NEVER (no success recorded for that day) may say the check never went out. Under
    NIGHTLY_LATER_DAY a later day's success overwrote the record, and that night's alert may have gone out."""
    _hours(monkeypatch, 16, 6)
    msg = ds.evaluate_run_final(None, run_date=SAT, posting_day=True, now=SUN_16, max_attempts=6,
                                stale_seconds=HEALTH_STALE_SECONDS, checked_at=None, nightly=nightly)
    assert msg.startswith("⚠️ Marketing: no run for Sat 2026-10-03, a posting day")
    assert said in msg
    if nightly == ds.NIGHTLY_LATER_DAY:
        assert "never went out" not in msg


@pytest.mark.asyncio
async def test_a_final_word_retried_after_the_next_nights_check_never_says_that_check_never_went_out(
        bot, digest_jobs, digest_ledger, monkeypatch):
    """Round-3 re-review r1.0, its exact sequence. Monday's nightly check sends "no run" and succeeds.
    Tuesday the web is down until 22:05; in that tick the final word runs first and its read of the nightly
    job state fails once, then Tuesday's own check succeeds (run_day = Tuesday). The 22:15 retry of the
    final word sees a LATER day's record: it still reports Monday (silence could hide a failed day) but must
    not claim Monday's check never went out — the owner got that alert."""
    _hours(monkeypatch, 16, 6)
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 5)))["sent"] == 1          # Mon: no run
    assert _sends(bot)[0]["text"].startswith("⚠️ Marketing: no run for Mon 2026-10-05, a posting day")
    real, reads = ds._job_state, []

    async def flaky(svc, job):
        reads.append(job)
        if len(reads) == 1:
            raise mrs.MarketingRunError("digest_job_state failed (job=marketing_run_health): RuntimeError: "
                                        "PostgREST 503")
        return await real(svc, job)

    monkeypatch.setattr(ds, "_job_state", flaky)
    await _health_at(digest_jobs, _et(2026, 10, 6, 22, 5))           # Tue, the first tick back: the word fails
    assert len(_sends(bot)) == 2
    assert _sends(bot)[1]["text"].startswith("⚠️ Marketing: no run for Tue 2026-10-06, a posting day")
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] == date(2026, 10, 6)
    assert (await _health_at(digest_jobs, _et(2026, 10, 6, 22, 15)))["sent"] == 1     # the word's retry
    word = _sends(bot)[2]["text"]
    assert word.startswith("⚠️ Marketing: no run for Mon 2026-10-05, a posting day")
    assert "(the nightly check's record no longer says whether its alert went out)" in word
    assert "never went out" not in word
    assert reads == [ds.JOB_HEALTH, ds.JOB_HEALTH]


@pytest.mark.asyncio
async def test_a_check_overwritten_by_a_later_failed_attempt_judges_from_the_earliest_time(
        bot, digest_jobs, digest_ledger, caplog):
    """Monday's 22:07 check said a retry was due; the 22:15 retry recovered the run at 22:41. Tuesday the web
    is down until 22:00 and Telegram fails that tick: the final word fails, and so does Tuesday's own check
    — which overwrites last_run_at with Tuesday 22:00 while run_day stays Monday. The 22:10 retry of the
    final word must judge from Monday 22:00 (the earliest the check can have run) and report the recovery
    it promised; judged from the overwritten time it would stay silent."""
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    run = _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-05", stage="voiced", attempts=3,
                                               updated_at=_iso_of(_et(2026, 10, 5, 21, 40)), last_error="ffmpeg 137"))
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 7)))["sent"] == 1
    assert "a final word comes tomorrow after 16:00 ET" in _sends(bot)[0]["text"]
    run.update(status="media_ready", stage="assets_ready", attempts=4, finished_at=_iso_of(_et(2026, 10, 5, 22, 41)),
               updated_at=_iso_of(_et(2026, 10, 5, 22, 41)))
    bot.script["sendMessage"] = [_TG_500, _TG_500]
    assert (await _health_at(digest_jobs, _et(2026, 10, 6, 22, 0)))["failed"] == 2
    state = digest_jobs.rows[ds.JOB_HEALTH]
    assert state["run_day"] == date(2026, 10, 5) and state["last_run_at"] == _et(2026, 10, 6, 22, 0)
    assert (await _health_at(digest_jobs, _et(2026, 10, 6, 22, 10)))["sent"] == 2
    assert _sends(bot)[3]["text"] == ("✅ Marketing run Mon 2026-10-05 recovered at 10-05 22:41 ET — its posts are "
                                      "waiting for review.")
    assert _sends(bot)[4]["text"].startswith("⚠️ Marketing: no run for Tue 2026-10-06")
    assert any("no longer says when" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_a_later_days_check_never_hides_a_failed_day_whose_own_check_never_went_out(
        bot, digest_jobs, digest_ledger, caplog):
    """Monday's check never succeeds (three 500s). Tuesday 22:00 the final word fails to send, while Tuesday's
    check — a good day, nothing to say — succeeds and moves run_day to Tuesday. The final word's retry can
    no longer tell whether Monday's check went out, so it speaks: Monday's failure is reported."""
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-05", stage="voiced", attempts=6,
                                         updated_at=_iso_of(_et(2026, 10, 5, 21, 40)), last_error="boom"))
    _seed_health_run(digest_ledger, _run("media_ready", run_date="2026-10-06", finished_at=_iso_of(_et(2026, 10, 6, 17, 0)),
                                         updated_at=_iso_of(_et(2026, 10, 6, 17, 0))))
    bot.script["sendMessage"] = [_TG_500] * 4
    for minutes in (0, 10, 20):
        await _health_at(digest_jobs, _et(2026, 10, 5, 22, minutes))
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] is None
    counters = await _health_at(digest_jobs, _et(2026, 10, 6, 22, 0))
    assert counters == {"checked": 2, "sent": 0, "failed": 1, "rate_limited": 0}
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] == date(2026, 10, 6)
    assert (await _health_at(digest_jobs, _et(2026, 10, 6, 22, 10)))["sent"] == 1
    assert _sends(bot)[-1]["text"].startswith("⚠️ Marketing run Mon 2026-10-05 ended FAILED after stage voiced")
    assert any("a later day's nightly check succeeded" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


class _JobStateTable:
    """`notification_job_state` answering every select with `answer` (a PostgREST shape drift) or raising
    `exc` (a 5xx) — the direct read the final word and the digest make."""

    def __init__(self, *, answer: Any = None, exc: Optional[Exception] = None) -> None:
        self.answer, self.exc, self.selects = answer, exc, 0

    def select(self, *_a: Any) -> "_JobStateTable":
        self.selects += 1
        return self

    def eq(self, *_a: Any) -> "_JobStateTable":
        return self

    def limit(self, *_a: Any) -> "_JobStateTable":
        return self

    def execute(self) -> Any:
        if self.exc is not None:
            raise self.exc
        return type("Answer", (), {"data": self.answer})()


_UNREADABLE_STATES = [
    _JobStateTable(exc=RuntimeError("PostgREST 503")),
    _JobStateTable(answer={"job": ds.JOB_HEALTH, "run_day": "2026-10-05"}),      # an object, not a list
    _JobStateTable(answer=None),
    _JobStateTable(answer=["2026-10-05"]),                                       # a row that is not an object
    _JobStateTable(answer=[{"job": ds.JOB_METRICS, "run_day": "2026-10-05"}]),    # another job's row
    _JobStateTable(answer=[{"job": ds.JOB_HEALTH, "run_day": "garbage"}]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("table", _UNREADABLE_STATES)
async def test_an_unreadable_nightly_state_fails_the_final_word_closed_and_a_later_tick_retries(
        bot, digest_jobs, digest_ledger, monkeypatch, caplog, table):
    """Fail closed: no read of the run, no message on a guess (it could repeat or miss the nightly verdict);
    the attempt fails, logged with its stack, and the day stays open. When the state reads again, the word
    is judged against the check that really went out (Monday 22:00, the run unchanged since): nothing to
    add."""
    caplog.set_level(logging.ERROR)
    table = _JobStateTable(answer=table.answer, exc=table.exc)     # a fresh select counter per run
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-05", stage="voiced", attempts=6,
                                         updated_at=_iso_of(_et(2026, 10, 5, 21, 40))))
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 1      # Monday's check: all used
    real_table = digest_ledger.fake.tables[ds._JOB_STATE_TABLE]
    digest_ledger.fake.tables[ds._JOB_STATE_TABLE] = table
    read: List[date] = []
    real_get = digest_ledger.get_run_for_date

    async def recording(run_date: Any) -> Any:
        read.append(run_date)
        return await real_get(run_date)

    monkeypatch.setattr(digest_ledger, "get_run_for_date", recording)
    assert await _health_at(digest_jobs, _et(2026, 10, 6, 16, 0)) == {"checked": 0, "sent": 0, "failed": 1,
                                                                  "rate_limited": 0}
    assert table.selects == 1 and read == [] and len(_sends(bot)) == 1      # no run read, no word on a guess
    assert digest_jobs.finishes[-1][:3] == (ds.JOB_HEALTH_FINAL, False, 0) and digest_jobs.finishes[-1][3]
    assert digest_jobs.rows[ds.JOB_HEALTH_FINAL]["run_day"] is None
    assert any("marketing_run_health_final" in r.getMessage() and r.exc_info for r in caplog.records)
    digest_ledger.fake.tables[ds._JOB_STATE_TABLE] = real_table
    assert await _health_at(digest_jobs, _et(2026, 10, 6, 16, 10)) == {"checked": 1, "sent": 0, "failed": 0,
                                                                   "rate_limited": 0}
    assert read == [date(2026, 10, 5)] and len(_sends(bot)) == 1
    assert digest_jobs.rows[ds.JOB_HEALTH_FINAL]["run_day"] == date(2026, 10, 6)


@pytest.mark.asyncio
async def test_an_unreadable_nightly_state_never_stops_that_evenings_own_check(bot, digest_jobs, digest_ledger):
    """Tuesday 22:00 with both jobs due and the state read failing: the final word fails closed, and
    Tuesday's own check still goes out."""
    digest_ledger.fake.tables[ds._JOB_STATE_TABLE] = _JobStateTable(exc=RuntimeError("PostgREST 503"))
    counters = await _health_at(digest_jobs, _et(2026, 10, 6, 22, 0))
    assert counters == {"checked": 1, "sent": 1, "failed": 1, "rate_limited": 0}
    (msg,) = _sends(bot)
    assert msg["text"].startswith("⚠️ Marketing: no run for Tue 2026-10-06")
    assert [f[:2] for f in digest_jobs.finishes] == [(ds.JOB_HEALTH_FINAL, False), (ds.JOB_HEALTH, True)]
    assert "PostgREST 503" in digest_jobs.finishes[0][3]


@pytest.mark.asyncio
async def test_with_run_hour_18_the_checks_move_with_the_setting(bot, digest_jobs, digest_ledger, monkeypatch):
    """MARKETING_RUN_HOUR_ET=18 (on the worker AND the web): six attempts from 18:15 run to 23:15, so the
    nightly check waits for 23:00 (18 + 6, capped) and the final word for 18:00 the next day."""
    _hours(monkeypatch, 18, 6)
    run = _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-03", stage="voiced", attempts=5,
                                               updated_at=_iso_of(_et(2026, 10, 3, 22, 40))))
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 22, 50)))["checked"] == 0
    assert digest_jobs.claims == []
    assert (await _health_at(digest_jobs, _et(2026, 10, 3, 23, 0)))["sent"] == 1
    assert "a final word comes tomorrow after 18:00 ET" in _sends(bot)[0]["text"]
    run.update(status="failed", attempts=6, updated_at=_iso_of(_et(2026, 10, 3, 23, 40)))   # the 23:15 retry failed
    assert (await _health_at(digest_jobs, _et(2026, 10, 4, 17, 59)))["checked"] == 0
    assert (await _health_at(digest_jobs, _et(2026, 10, 4, 18, 0)))["sent"] == 1
    assert _sends(bot)[1]["text"].startswith("⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage voiced (the "
                                             "last completed stage): all 6 attempts used.")


@pytest.mark.asyncio
async def test_with_run_hour_23_only_the_final_word_reports_the_day(bot, digest_jobs, digest_ledger, monkeypatch):
    """No nightly check (it would precede the worker's first tick): the day's run failed at the cap after
    midnight's resumes, and the final word at 23:00 the next day — seeing no check — reports it."""
    _hours(monkeypatch, 23, 6)
    _seed_health_run(digest_ledger, _run("failed", run_date="2026-10-03", stage="voiced", attempts=6,
                                         updated_at=_iso_of(_et(2026, 10, 4, 4, 40)), last_error="boom"))
    for at in (_et(2026, 10, 3, 23, 0), _et(2026, 10, 3, 23, 50), _et(2026, 10, 4, 22, 59)):
        assert (await _health_at(digest_jobs, at))["checked"] == 0
    assert digest_jobs.claims == [] and digest_jobs.state_reads == 0
    assert (await _health_at(digest_jobs, _et(2026, 10, 4, 23, 0)))["sent"] == 1
    assert _sends(bot)[0]["text"].startswith("⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage voiced (the "
                                             "last completed stage): all 6 attempts used.")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [{"job": ds.JOB_METRICS}, None, ["row"],
                                    [{"job": "whale_hydration_full", "run_day": "2026-09-01"}]])
async def test_a_garbage_metrics_job_state_reads_as_unreadable_never_as_never_ran(digest_ledger, answer):
    """The digest's read of the metrics job is the same strict read: a shape drift (or another job's row —
    a lost key filter) says "state unreadable", never "never completed" or another job's numbers."""
    _seed_week(digest_ledger)
    digest_ledger.fake.tables[ds._JOB_STATE_TABLE] = _JobStateTable(answer=answer)
    with pytest.raises(notification_jobs.JobStateUnreadable):
        await ds._job_state(digest_ledger, ds.JOB_METRICS)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["metrics_job"] is None
    text = ds.compose_digest(report)
    assert "Metrics job: state unreadable" in text and "never completed" not in text
    assert "whale" not in text.lower()


@pytest.mark.asyncio
async def test_the_job_state_read_is_by_key_and_a_missing_row_is_never_ran(digest_ledger):
    t = digest_ledger.fake.tables[ds._JOB_STATE_TABLE].rows
    t.append(dict(_WHALE_JOB_ROW))
    assert await ds._job_state(digest_ledger, ds.JOB_HEALTH) == {"job": ds.JOB_HEALTH, "enabled": True,
                                                                 "run_day": None}
    t.append(_job_row("2026-10-03"))
    row = await ds._job_state(digest_ledger, ds.JOB_HEALTH)
    assert row["job"] == ds.JOB_HEALTH and row["run_day"] == "2026-10-03" and row["last_run_at"] == _SAT_CHECKED


@pytest.mark.asyncio
async def test_health_says_nothing_about_a_good_run_and_is_done_for_the_day(bot, digest_jobs, digest_ledger):
    digest_ledger.fake.tables[mrs.RUNS].rows.append(
        _run("media_ready", run_date="2026-10-03", timings={"rendered_cgroup_peak_mb": 2587.0}))
    assert await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0)) == {"checked": 1, "sent": 0, "failed": 0,
                                                               "rate_limited": 0}
    assert digest_jobs.finishes == [(ds.JOB_HEALTH, True, 0, None)] and bot.calls == []
    reads = digest_jobs.state_reads
    await _health_at(digest_jobs, _et(2026, 10, 3, 22, 30))
    assert digest_jobs.state_reads == reads and len(digest_jobs.claims) == 1


@pytest.mark.asyncio
async def test_health_alerts_when_the_worker_never_claimed_the_day(bot, digest_jobs):
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 1
    assert "no run for Mon 2026-10-05" in _sends(bot)[0]["text"]


@pytest.mark.asyncio
async def test_health_retries_a_failed_send_at_most_three_times_a_day(bot, digest_jobs):
    bot.script["sendMessage"] = [_TG_500] * 3
    for minutes in (0, 10, 20, 30, 40, 50):
        await _health_at(digest_jobs, _et(2026, 10, 3, 22, minutes))
    assert len(_sends(bot)) == 3 and len(digest_jobs.claims) == 3
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] is None
    # the next posting day starts afresh
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 1


@pytest.mark.asyncio
async def test_health_follows_the_et_day_across_the_november_change(bot, digest_jobs, digest_ledger):
    digest_ledger.fake.tables[mrs.RUNS].rows.append(_run("skipped", run_date="2026-11-02",
                                                  metadata={"skip_reason": "writer_unavailable"}))
    assert (await _health_at(digest_jobs, datetime(2026, 11, 3, 2, 59, tzinfo=timezone.utc)))["checked"] == 0  # 21:59 EST
    assert (await _health_at(digest_jobs, datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)))["sent"] == 1     # 22:00 EST
    assert "Marketing run Mon 2026-11-02 was SKIPPED (writer_unavailable)" in _sends(bot)[0]["text"]
    assert digest_jobs.rows[ds.JOB_HEALTH]["run_day"] == date(2026, 11, 2)


@pytest.mark.asyncio
async def test_health_and_digest_jobs_are_independent(bot, digest_jobs, digest_ledger):
    """A Monday whose digest failed all day and whose run failed: both reports go out at 22:00, each
    under its own day claim."""
    digest_ledger.fake.tables[mrs.RUNS].rows.append(_run("failed", run_date="2026-10-05", stage="voiced"))
    assert (await _digest_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 1
    assert (await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0)))["sent"] == 1
    assert {c[0] for c in digest_jobs.claims} == {ds.JOB_DIGEST, ds.JOB_HEALTH}
    assert len(_sends(bot)) == 2


def test_the_job_names_are_the_contract_and_metrics_is_the_measure_steps(monkeypatch):
    assert (ds.JOB_DIGEST, ds.JOB_HEALTH, ds.JOB_HEALTH_FINAL) == (
        "marketing_digest_weekly", "marketing_run_health", "marketing_run_health_final")
    assert ds.JOB_METRICS == ms.JOB_METRICS == "marketing_metrics_daily"
    # one key per job: run_day_job keys a day by the ET day the job RUNS on
    assert len({ds.JOB_DIGEST, ds.JOB_HEALTH, ds.JOB_HEALTH_FINAL, ds.JOB_METRICS}) == 4
    # the DECLARED defaults (run hour 16, six attempts) keep the hours the owner knows: 22:00 and 16:00 ET
    fields = type(settings).model_fields
    assert (fields["MARKETING_RUN_HOUR_ET"].default, fields["MARKETING_MAX_RUN_ATTEMPTS"].default) == (16, 6)
    for name in ("MARKETING_RUN_HOUR_ET", "MARKETING_MAX_RUN_ATTEMPTS"):
        monkeypatch.setattr(settings, name, fields[name].default)
    assert (ds.run_hour_et(), ds.health_hour_et(), ds.final_hour_et()) == (16, 22, 16)


@pytest.mark.asyncio
async def test_a_ledger_failure_inside_the_health_check_is_retried_never_raised(bot, digest_jobs, digest_ledger, monkeypatch,
                                                                                caplog):
    monkeypatch.setattr(digest_ledger, "get_run_for_date", _Exploding(mrs.MarketingRunError("runs 503")))
    caplog.set_level(logging.ERROR)
    assert await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0)) == {"checked": 0, "sent": 0, "failed": 1,
                                                               "rate_limited": 0}
    assert _sends(bot) == [] and digest_jobs.finishes[-1][:2] == (ds.JOB_HEALTH, False)
    assert any("marketing_run_health" in r.getMessage() and r.exc_info for r in caplog.records)
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_both_reports_share_one_pacer_for_the_review_chat(bot, digest_jobs, digest_ledger, monkeypatch):
    """A health alert and a digest in the same tick are spaced like every other message to the chat."""
    waits: List[str] = []

    class RecordingPacer:
        async def wait(self) -> None:
            waits.append("wait")

    monkeypatch.setattr(ds, "_PACER", RecordingPacer())
    digest_ledger.fake.tables[mrs.RUNS].rows.append(_run("failed", run_date="2026-10-05"))
    await _health_at(digest_jobs, _et(2026, 10, 5, 22, 0))
    await _digest_at(digest_jobs, _et(2026, 10, 5, 22, 0))
    assert waits == ["wait", "wait"] and len(_sends(bot)) == 2


@pytest.mark.asyncio
async def test_the_cycles_run_with_no_arguments_as_the_tick_calls_them(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    assert await ds.health_cycle() == {"checked": 0, "sent": 0, "failed": 0, "rate_limited": 0}
    assert await ds.digest_cycle() == {"sent": 0, "failed": 0, "rate_limited": 0}

