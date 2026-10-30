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
    day to the final word — never silence; an unreadable job state skips the tick;
  * the weekly COST line sits right under the date line (it survives the row caps and the hard cut):
    X exact to our charges journal by each entry's OWN time over the digest's [start, end) instants,
    Gemini / worker / the Upload-Post plan fee as labelled estimates, in integer micro-dollars. Each part
    degrades ALONE to "unreadable" and the total then reads "≥" and names it — never a guessed number,
    never a partial sum (a capped journal read is unreadable), never NaN, never a silent $0.000; undated
    charges are named and never counted into any week; the ⚠️ usage line compares X + Gemini + worker
    (never the plan fee) STRICTLY above the alert line;
  * /go taps are "(approximate)", and the server's own `<campaign>_early` rows (the first minutes after
    a post, mostly link scanners) are summed apart — never into a campaign or the total.
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
from app.schemas.marketing import IMAGE_FALLBACK_REASONS
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


@pytest.fixture(autouse=True)
def _declared_max_video_seconds(monkeypatch):
    """The average watch time bound is read from MARKETING_MAX_VIDEO_SECONDS at call time (MD-4): pin it to its
    DECLARED default (75 → a 150 s bound), so a developer's .env can never move an expected value. A test that
    wants another length sets its own."""
    monkeypatch.setattr(settings, "MARKETING_MAX_VIDEO_SECONDS",
                        type(settings).model_fields["MARKETING_MAX_VIDEO_SECONDS"].default)


@pytest.fixture(autouse=True)
def _declared_post_weekdays(monkeypatch):
    """Batch 3: the posting days (MARKETING_POST_WEEKDAYS) are read at call time by the run-health timing, the
    digest's Runs rows and the runway — pin them to their DECLARED default (Mon/Tue/Thu/Sat), so a developer's .env
    can never move a pinned date or hour. A test that wants another cadence sets its own (`_cadence`)."""
    monkeypatch.setattr(settings, "MARKETING_POST_WEEKDAYS",
                        type(settings).model_fields["MARKETING_POST_WEEKDAYS"].default)


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
    # The headroom is priced by these switches (`outlet_x.metrics_headroom_micros`): pinned, never the .env's.
    monkeypatch.setattr(ms.settings, "MARKETING_X_ALLOW_URLS", False)
    monkeypatch.setattr(ms.settings, "MARKETING_X_IMAGES", False)
    monkeypatch.setattr(ms.settings, "MARKETING_X_IMAGE_POST_MICROS", 200_000)
    monkeypatch.setattr(ms.settings, "MARKETING_X_MEDIA_UPLOAD_MICROS", 15_000)
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


# video measures (Batch 1 item 8a, 2026-10-15) ─────────────────────────────────

#: TikTok's documented per-post object (docs.upload-post.com/api/get-analytics, read 2026-10-15), every
#: field the page lists for it — `retention` one point per second, `second` a STRING.
TIKTOK_DOC_METRICS: Dict[str, Any] = {
    "views": 5200, "likes": 120, "comments": 8, "shares": 4, "favorites": 3, "reach": 4100,
    "new_followers": 128, "profile_views": 903, "average_time_watched": 7.4, "total_time_watched": 297613,
    "full_video_watched_rate": 0.184,
    "retention": [{"second": str(s), "percentage": round(1 - s / 40, 4)} for s in range(0, 38)],
    "impression_sources": [{"impression_source": "For You", "percentage": 0.977}],
    "audience_types": [{"audience_type": "New viewers", "percentage": 0.881}], "is_ai_generated": True,
}


@pytest.mark.parametrize("raw, lo, hi, expected", [
    (7.4, 0, 3600, 7.4), (0, 0, 1, 0.0), (1, 0, 1, 1.0), (0.184, 0, 1, 0.184), (3600, 0, 3600, 3600.0),
    (297613, 0, 1e10, 297613.0), (10 ** 10, 0, 1e10, 1e10), (0.123456789, 0, 1, 0.1235),
    (-0.0, 0, 1, 0.0), (5e-9, 0, 1, 0.0), (135.5, 0, 1000, 135.5),       # a looping Short passes 100
    # omitted, never 0 — and never clamped or rescaled
    (None, 0, 1, None), (True, 0, 1, None), (False, 0, 1, None), ("7.4", 0, 3600, None), ("1", 0, 1, None),
    ("", 0, 1, None), (math.nan, 0, 1, None), (math.inf, 0, 3600, None), (-math.inf, 0, 1, None),
    (-0.01, 0, 1, None), (-1, 0, 3600, None), (1.0001, 0, 1, None), (18.4, 0, 1, None),   # a % for a fraction
    (3600.5, 0, 3600, None), (10 ** 10 + 1, 0, 1e10, None),
    pytest.param(10 ** 400, 0, 1e10, None, id="int_beyond_any_float"),                   # no OverflowError
    ([7.4], 0, 3600, None), ({"v": 7.4}, 0, 3600, None), (b"7", 0, 3600, None),
])
def test_normalize_measure_keeps_only_honest_bounded_numbers(raw, lo, hi, expected):
    got = ms.normalize_measure(raw, lo, hi)
    assert got == expected
    if got is not None:
        assert type(got) is float and lo <= got <= hi and math.copysign(1.0, got) == 1.0


def test_retention_is_cut_to_fixed_seconds_from_the_documented_shape():
    curve = ms.retention_points(TIKTOK_DOC_METRICS["retention"])
    assert curve == {"1": 0.975, "3": 0.925, "5": 0.875, "10": 0.75, "15": 0.625, "30": 0.25}
    assert list(curve) == ["1", "3", "5", "10", "15", "30"]            # the fixed seconds, in order
    # an int second reads too; an unordered curve is still one curve; a short video keeps what it reaches
    assert ms.retention_points([{"second": 3, "percentage": 0.5}, {"second": "1", "percentage": 0.9}]) == {
        "1": 0.9, "3": 0.5}
    assert ms.retention_points([{"second": "2", "percentage": 0.5}]) == {}     # no kept second reached


@pytest.mark.parametrize("bad", [
    None, "not a list", {"1": 0.9}, [], [None], ["0.9"], [[1, 0.9]],
    [{"second": "1", "percentage": 0.9}, {"second": "1", "percentage": 0.8}],    # a second given twice
    [{"second": "1", "percentage": 0.9}, {"second": "3", "percentage": 75}],     # a percentage, not a fraction
    [{"second": "1", "percentage": 1.01}], [{"second": "1", "percentage": -0.1}],
    [{"second": "1", "percentage": "0.9"}], [{"second": "1", "percentage": None}],
    [{"second": "1", "percentage": True}], [{"second": "1", "percentage": math.nan}],
    [{"second": "1.5", "percentage": 0.9}], [{"second": 1.0, "percentage": 0.9}], [{"second": True, "percentage": 0.9}],
    [{"second": "-1", "percentage": 0.9}], [{"second": " 1", "percentage": 0.9}], [{"second": "", "percentage": 0.9}],
    [{"second": "123456", "percentage": 0.9}], [{"percentage": 0.9}], [{"second": "1"}],
    [{"second": str(s), "percentage": 0.5} for s in range(ms.RETENTION_MAX_POINTS + 1)],   # not one of our videos
])
def test_a_malformed_retention_curve_is_omitted_whole(bad):
    """One unreadable point and the curve is not trusted at all — a unit change upstream must leave
    nothing, never a wrong-scale survivor."""
    assert ms.retention_points(bad) == {}
    if isinstance(bad, list) and 0 < len(bad) < 5:
        # a good point beside the bad one does not rescue the curve
        assert ms.retention_points(bad + [{"second": "3", "percentage": 0.6}]) == {}


def test_extract_video_metrics_keeps_each_platforms_own_fields_and_omits_the_rest():
    tiktok = ms.extract_video_metrics(TIKTOK_DOC_METRICS, "tiktok")
    assert tiktok == {"new_followers": 128, "profile_views": 903, "average_time_watched": 7.4,
                      "total_time_watched": 297613.0, "full_video_watched_rate": 0.184,
                      "retention": {"1": 0.975, "3": 0.925, "5": 0.875, "10": 0.75, "15": 0.625, "30": 0.25}}
    # nothing beyond the documented fields, and nothing of another platform's
    youtube_like = {**TIKTOK_DOC_METRICS, "average_view_percentage": 64.2, "watch_time_minutes": 12}
    assert "average_view_percentage" not in ms.extract_video_metrics(youtube_like, "tiktok")
    assert ms.extract_video_metrics({"views": 109, "likes": 2, "comments": 0, "favorites": 0}, "youtube") == {}
    assert ms.extract_video_metrics({"views": 109, "average_view_duration_seconds": 11.25,
                                     "average_view_percentage": 135.5, "watch_time_minutes": 20.4},
                                    "youtube") == {"average_view_duration_seconds": 11.25,
                                                   "average_view_percentage": 135.5, "watch_time_minutes": 20.4}
    assert ms.extract_video_metrics({"views": 12, "crossposted_views": 21400, "facebook_views": None},
                                    "instagram") == {"crossposted_views": 21400}       # null: omitted, never 0
    assert ms.extract_video_metrics(TIKTOK_DOC_METRICS, "facebook") == {}              # no video fields there
    for junk_input, platform in ((None, "tiktok"), ([1], "tiktok"), ("x", "tiktok"), (TIKTOK_DOC_METRICS, None),
                                 (TIKTOK_DOC_METRICS, 7)):
        assert ms.extract_video_metrics(junk_input, platform) == {}


def test_a_missing_or_unreadable_video_field_is_omitted_never_zero():
    outliers = {"new_followers": -1, "profile_views": "lots", "average_time_watched": "7.4",
                "total_time_watched": math.inf, "full_video_watched_rate": 18.4, "retention": "flat"}
    assert ms.extract_video_metrics(outliers, "tiktok") == {}
    one = ms.extract_video_metrics({"new_followers": 0, "average_time_watched": True}, "tiktok")
    assert one == {"new_followers": 0}                     # a REPORTED 0 is a count; a bool is not a time


def test_video_measures_round_trip_through_the_snapshot_and_its_re_validation():
    video = ms.extract_video_metrics(TIKTOK_DOC_METRICS, "tiktok")
    snap = ms.build_snapshot(ms.extract_counts(TIKTOK_DOC_METRICS, ms.UPLOAD_POST_FIELDS), now=THU,
                             published_at=THU - timedelta(days=3, hours=1), cp=3, video=video)
    assert snap["video"] == video and snap["cp"] == 3 and snap["views"] == 5200
    assert ms._clean_snapshot(snap) == snap                               # idempotent: nothing lost, nothing added
    doc = ms.apply_measurement({}, platform="tiktok", today=THU.date(), status="ok", snapshot=snap)
    assert doc["last"]["video"] == video and doc["history"][-1]["video"] == video
    # a hand-edited snapshot: every unreadable video value is dropped on the way in, the rest kept
    tampered = copy.deepcopy(snap)
    tampered["video"].update({"new_followers": True, "average_time_watched": 99_999, "unknown_field": 5,
                              "full_video_watched_rate": "0.5"})
    tampered["video"]["retention"] = {"1": 0.9, "2": 0.8, "3": 1.5, "30": "0.2", "x": 0.1}
    clean = ms.apply_measurement({}, platform="tiktok", today=THU.date(), status="ok",
                                 snapshot=tampered)["last"]["video"]
    assert clean == {"profile_views": 903, "total_time_watched": 297613.0, "retention": {"1": 0.9}}
    # nothing readable: no `video` key at all
    assert "video" not in ms.build_snapshot({"views": 3}, now=THU, published_at=None, video={"x": 1})
    assert "video" not in ms.build_snapshot({"views": 3}, now=THU, published_at=None, video="junk")
    assert "video" not in ms._clean_snapshot({"views": 3, "video": {"retention": {"1": 7}}})


def test_video_measures_alone_are_not_counts():
    """`snapshot_has_counts` is unchanged: a read with video measures but no count is `error` /
    `no_metrics` and adds no history (never a snapshot of zeros)."""
    snap = ms.build_snapshot({}, now=THU, published_at=THU - timedelta(days=2), video={"new_followers": 3})
    assert snap["video"] == {"new_followers": 3} and not ms.snapshot_has_counts(snap)
    doc = ms.apply_measurement({}, platform="tiktok", today=THU.date(), status="ok", snapshot=snap)
    assert (doc["status"], doc["note"]) == ("error", "no_metrics") and "last" not in doc and "history" not in doc


def test_the_video_field_names_never_collide_and_every_bound_is_sane():
    names = [k for keys in ms.VIDEO_COUNT_FIELDS.values() for k in keys] + [
        k for fields in ms.VIDEO_MEASURE_FIELDS.values() for k in fields]
    assert len(names) == len(set(names)) == len(ms.VIDEO_KEY_BOUNDS)
    assert not set(names) & set(ms.COUNT_KEYS) and ms.RETENTION_KEY not in names
    for fields in ms.VIDEO_MEASURE_FIELDS.values():
        for lo, hi in fields.values():
            assert 0 <= lo < hi and math.isfinite(hi)
    assert ms.VIDEO_MEASURE_FIELDS["tiktok"]["full_video_watched_rate"] == (0, 1)        # a FRACTION
    assert ms.VIDEO_MEASURE_FIELDS["youtube"]["average_view_percentage"] == (0, 1000)    # loops pass 100
    assert ms.RETENTION_SECONDS == (1, 3, 5, 10, 15, 30)
    assert ms.AVERAGE_WATCH_KEYS <= set(ms.VIDEO_KEY_BOUNDS) and ms.WATCH_SECONDS_LOOPS == 2


def test_an_average_watch_time_is_kept_only_up_to_twice_the_longest_video(monkeypatch):
    """MD-4 (2026-10-16): the 3,600 s bound kept a watch time in MILLISECONDS (7,400 for 7.4 s) as seconds. No
    video of ours is longer than MARKETING_MAX_VIDEO_SECONDS, so an average past twice it is omitted — never
    rescaled or clamped. The bound is read at call time, and the digest re-validates with the same one."""
    assert ms.max_watch_seconds() == 150.0                                   # the declared 75 s, twice
    for key in ("average_time_watched", "average_view_duration_seconds"):
        assert ms.measure_bounds(key) == (0, 150.0)
    assert ms.measure_bounds("average_view_percentage") == (0, 1000)         # untouched
    assert ms.measure_bounds("total_time_watched") == (0, 1e10)
    assert ms.measure_bounds("watch_time_minutes") == (0, 1e9)
    for not_a_measure in ("new_followers", "crossposted_views", "retention", "nope", None, 7, True):
        assert ms.measure_bounds(not_a_measure) is None
    tiktok = {"views": 9, "average_time_watched": 7400, "full_video_watched_rate": 0.2}
    assert ms.extract_video_metrics(tiktok, "tiktok") == {"full_video_watched_rate": 0.2}          # ms: omitted
    for kept in (0, 7.4, 149.99, 150):
        assert ms.extract_video_metrics({"average_time_watched": kept}, "tiktok") == {
            "average_time_watched": float(kept)}
        assert ms.extract_video_metrics({"average_view_duration_seconds": kept}, "youtube") == {
            "average_view_duration_seconds": float(kept)}
    for dropped in (150.01, 151, 3599.9, 7400, 297613):
        assert ms.extract_video_metrics({"average_time_watched": dropped}, "tiktok") == {}
        assert ms.extract_video_metrics({"average_view_duration_seconds": dropped}, "youtube") == {}
    # the stored side (`_clean_video`) and the digest's re-validation use the same bound
    assert "average_time_watched" not in ms._clean_video({"average_time_watched": 3599.9, "new_followers": 2})
    assert ds._video_measure({"average_time_watched": 3599.9}, "average_time_watched") is None
    assert ds._video_measure({"average_time_watched": 149.9}, "average_time_watched") == 149.9
    # read at call time: a shorter longest video tightens it, for both sides
    monkeypatch.setattr(settings, "MARKETING_MAX_VIDEO_SECONDS", 60)
    assert ms.max_watch_seconds() == 120.0
    assert ms.extract_video_metrics({"average_time_watched": 130}, "tiktok") == {}
    assert ds._video_measure({"average_time_watched": 130}, "average_time_watched") is None
    assert ms.extract_video_metrics({"average_time_watched": 119}, "tiktok") == {"average_time_watched": 119.0}


@pytest.mark.parametrize("bad", [0, -5, None, "75", True, 75.0, 3601, 10 ** 6])
def test_an_unreadable_longest_video_setting_bounds_with_the_default_never_zero(monkeypatch, caplog, bad):
    """Settings declares no bound for MARKETING_MAX_VIDEO_SECONDS: a 0 must never omit every watch time, and a
    junk value never crashes the read — the declared default stands, logged once a day."""
    caplog.set_level(logging.WARNING, logger=MS_LOGGER)
    monkeypatch.setattr(settings, "MARKETING_MAX_VIDEO_SECONDS", bad)
    assert ms.max_watch_seconds() == 150.0 and ms.max_watch_seconds() == 150.0
    assert ms.extract_video_metrics({"average_time_watched": 7.4}, "tiktok") == {"average_time_watched": 7.4}
    assert len(_messages(caplog, "MARKETING_MAX_VIDEO_SECONDS", logging.WARNING)) == 1


#: YouTube's documented watch fields for a 58.4 s Short read at ~1.6 days: 22 s watched on average, 37.7%.
_YT_WATCH = {"views": 109, "average_view_duration_seconds": 22, "average_view_percentage": 37.7,
             "watch_time_minutes": 40}


@pytest.mark.parametrize("share, seconds, kept", [
    (37.7, 58.4, True),                 # 22 / 58.4 = 37.67%: agrees
    (37.0, 58.4, True), (42.6, 58.4, True),           # inside the larger of 5 points and half the expected
    (0.377, 58.4, False),               # a FRACTION for the percentage (Upload-Post's retention "percentage")
    (3.77, 58.4, False),                # ten times too small
    (377.0, 58.4, False),               # ten times too large
    (135.5, 16.236, True),              # a looping Short: 22 s over 16.2 s = 135.5%
    (37.7, None, True), (37.7, 0, True), (37.7, -58.4, True), (37.7, math.nan, True), (37.7, math.inf, True),
    (37.7, "58.4", True), (37.7, True, True), (37.7, 4000, True),     # the length unknown: kept as bounded
])
def test_youtube_view_share_is_checked_against_duration_over_the_videos_length(share, seconds, kept):
    """MD-4: a share on another scale (0.377 for 37.7%) sits inside 0-1000 — the bounds cannot see it. With the
    average duration and the video's own length known, a share that disagrees is omitted; the duration (its
    key names its unit, its bound is tight) is kept; with the length unknown both are kept as bounded."""
    video = ms.extract_video_metrics({**_YT_WATCH, "average_view_percentage": share}, "youtube")
    before = copy.deepcopy(video)
    out = ms.check_view_share(video, seconds)
    assert video == before                                                    # pure: the input is untouched
    assert ("average_view_percentage" in out) is kept
    assert out["average_view_duration_seconds"] == 22.0 and out["watch_time_minutes"] == 40.0
    if kept:
        assert out == video


def test_a_view_share_check_needs_both_watch_fields_and_never_invents_one():
    for video in ({}, {"average_view_percentage": 0.377}, {"average_view_duration_seconds": 22.0},
                  {"average_view_percentage": "0.377", "average_view_duration_seconds": 22.0},
                  {"average_view_percentage": 0.377, "average_view_duration_seconds": True}):
        assert ms.check_view_share(video, 58.4) == video
    assert ms.check_view_share("junk", 58.4) == {} and ms.check_view_share(None, 58.4) == {}
    # a zero duration with a small share is rounding (YouTube's whole seconds); with a large one it is not
    assert "average_view_percentage" in ms.check_view_share(
        {"average_view_duration_seconds": 0.0, "average_view_percentage": 0.9}, 30.0)
    assert "average_view_percentage" not in ms.check_view_share(
        {"average_view_duration_seconds": 0.0, "average_view_percentage": 45.0}, 30.0)
    # TikTok's measures never meet the check (only YouTube carries both keys)
    tiktok = ms.extract_video_metrics(TIKTOK_DOC_METRICS, "tiktok")
    assert ms.check_view_share(tiktok, 1.0) == tiktok


@pytest.mark.parametrize("age,cps,largest,video", [
    # (age, stored cps, the largest crossed checkpoint not yet used, what a VIDEO is due for: only within
    # a day of crossing it — MD-2, 2026-10-16: a 29-day-old video used to be read "for 7")
    (timedelta(hours=23, minutes=59), [], None, None),
    (timedelta(days=1), [], 1, 1),
    (timedelta(days=1, hours=23, minutes=59), [], 1, 1),               # the last minute of the window
    (timedelta(days=2), [], 1, None),                                  # a day late: a rotation read
    (timedelta(days=2), [1], None, None),
    (timedelta(days=3, hours=2), [1], 3, 3),
    (timedelta(days=3, hours=2), [], 3, 3),                            # 1 was missed; 3 is on time
    (timedelta(days=4), [1], 3, None),
    (timedelta(days=7, hours=14), [1, 3], 7, 7),                       # the 06:00 ET read of a 16:20 post
    (timedelta(days=8), [], 7, None),                                  # several crossed, all late
    (timedelta(days=8), [1, 3, 7], None, None),
    (timedelta(days=15), [], 7, None),                                 # the deploy day's backlog
    (timedelta(days=29), [1, 3, 7], None, None),                       # 28 is X's, never a video's
    (timedelta(days=29), [1, 3], 7, None),
])
def test_a_video_is_due_at_1_3_and_7_days_and_only_within_a_day(age, cps, largest, video):
    # the generic rule (X's, any `days`): the largest crossed, however late
    assert ms.due_checkpoint(THU - age, THU, cps, ms.VIDEO_CHECKPOINT_DAYS) == largest
    row = {"format": "video", "published_at": (THU - age).isoformat(), "metrics": {"cps": cps}}
    assert ms.video_due_checkpoint(row, THU) == video
    # only a VIDEO post has these checkpoints; X's default days are unchanged
    assert ms.video_due_checkpoint({**row, "format": "image"}, THU) is None
    assert ms.video_due_checkpoint({**row, "published_at": None}, THU) is None
    assert ms.x_due_checkpoint(THU - timedelta(days=29), THU, [1, 3, 7]) == 28
    assert ms.x_due_checkpoint(THU - timedelta(days=60), THU, [1, 3, 7]) == 28        # X: no tolerance
    assert ms.VIDEO_CHECKPOINT_TOLERANCE == timedelta(days=1)


@pytest.mark.parametrize("age,cps,passed", [
    (timedelta(hours=20), [], []),                       # nothing crossed
    (timedelta(days=1, hours=2), [], [1]),               # on time: uses 1
    (timedelta(days=2, hours=2), [1], []),               # all crossed are used: nothing to mark
    (timedelta(days=4, hours=12), [1], [1, 3]),          # read late for 3: 3 is used (and 1 again, harmless)
    (timedelta(days=15), [], [1, 3, 7]),                 # the deploy day's backlog: every one it crossed
    (timedelta(days=15), [1, 3, 7], []),
    (timedelta(days=15), ["7", True, 7.0, None], [1, 3, 7]),   # junk is not "used"
])
def test_a_video_read_uses_up_every_checkpoint_it_crossed(age, cps, passed):
    row = {"format": "video", "published_at": (THU - age).isoformat(), "metrics": {"cps": cps}}
    assert ms.video_passed_checkpoints(row, THU) == passed
    for other in ({**row, "format": "image"}, {**row, "format": "text"}, {**row, "published_at": None},
                  {**row, "published_at": "junk"}):
        assert ms.video_passed_checkpoints(other, THU) == []


def test_cps_are_tracked_only_where_asked():
    stored = {"cps": [1], "measured_day": "2026-10-07"}
    tracked = ms.apply_measurement(stored, platform="tiktok", today=THU.date(), status="error", cps=[1, 3, 99],
                                   track_cps=True)
    assert tracked["cps"] == [1, 3]
    # a non-checkpoint read leaves a stored `cps` exactly as it was — and adds none where there was none
    assert ms.apply_measurement(stored, platform="tiktok", today=THU.date(), status="ok", cps=[3])["cps"] == [1]
    assert "cps" not in ms.apply_measurement({}, platform="tiktok", today=THU.date(), status="ok", cps=[1])
    assert ms.valid_checkpoints([1, 3, 7, 28, 2, "3"]) == [1, 3, 7, 28]


def test_the_key_set_names_keys_only_never_values():
    line = ms._key_set({**TIKTOK_DOC_METRICS, "bad key\nforged": 1, "x" * 70: 2, "secret=Bearer abc": 3})
    assert line.startswith("?,") and "average_time_watched" in line and "retention" in line
    for value in ("5200", "0.184", "297613", "For You", "forged", "Bearer", "xxxxxxx"):
        assert value not in line
    assert ms._key_set({}) == ""
    many = ms._key_set({f"k{i:03d}": i for i in range(70)})
    assert many.endswith(",+10 more") and many.count(",") == ms._KEY_SET_MAX


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
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGES", False)
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


#: Drop 1: while MARKETING_X_IMAGES is on, a post is priced as an image post — MARKETING_X_IMAGE_POST_MICROS
#: ($0.20) plus its pre-send charges, the $0.015 media upload (MARKETING_X_MEDIA_UPLOAD_MICROS, Batch 1) and
#: the $0.005 alt text: four posts are $0.88.
IMAGE_HEADROOM = 4 * (200_000 + 15_000 + 5_000)


@pytest.mark.asyncio
@pytest.mark.parametrize("spent, reads", [(2_000_000 - RESERVE - IMAGE_HEADROOM, 1),
                                          (2_000_000 - RESERVE - IMAGE_HEADROOM + 1, 0)])
async def test_with_x_images_on_metric_reads_leave_four_image_posts_of_headroom(env, monkeypatch, spent, reads):
    monkeypatch.setattr(ms.settings, "MARKETING_X_IMAGES", True)
    assert outlet_x.metrics_headroom_micros() == IMAGE_HEADROOM == 880_000
    env.x_account_holder(THU)
    pid = env.ledger.post("x", published_at=THU - timedelta(days=1, hours=1))
    ext = env.ledger.raw(pid)["external_id"]
    env.x.timeline = {ext: xpost(ext, like_count=1)}
    env.ledger.spend(spent)
    await env.measure(THU)
    assert len(env.x.reads) == reads
    assert env.ledger.metrics(pid)["status"] == ("ok" if reads else "capped")
    if not reads:
        assert env.ledger.metrics(pid)["note"] == "the monthly X cap keeps 4 posts of headroom ($0.880) — not read"


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
async def test_upload_post_reads_at_most_forty_posts_a_day(env):
    assert ms.UPLOAD_POST_DAILY_READS == 40
    env.up_configured = True
    pids = [env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=i % 20, minutes=i))
            for i in range(42)]
    env.up.default = up_answer("tiktok")
    await env.measure(THU)
    assert len(env.up.calls) == 40
    unread = [p for p in pids if not env.ledger.metrics(p)]
    assert len(unread) == 2
    await env.measure(THU + timedelta(days=1))                         # the two never read come first
    assert len(env.up.calls) == 80
    assert all(env.ledger.metrics(p)["measured_day"] == "2026-10-09" for p in unread)


def _measured_doc(day: str, *, cps: Optional[List[int]] = None, views: int = 10) -> Dict[str, Any]:
    doc = {"v": 1, "rev": 1, "status": "ok", "measured_day": day, "note": None, "unavailable_until": None,
           "last": _entry(day, views=views), "history": [_entry(day, views=views)]}
    if cps is not None:
        doc["cps"] = cps
    return doc


@pytest.mark.asyncio
async def test_a_video_due_at_1_3_or_7_days_is_read_first_inside_the_cap(env):
    """45 never-measured picture posts would take all 40 reads in today's order; the three videos that
    crossed a fixed age go first anyway, each read for ITS checkpoint (`cp`), which joins its `cps`."""
    env.up_configured = True
    fillers = [env.ledger.post("facebook", published_at=THU - timedelta(days=2, minutes=i), format="image")
               for i in range(45)]
    day1 = env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=1), format="video")
    day3 = env.ledger.post("youtube", published_at=THU - timedelta(days=3, hours=1), format="video",
                           metrics=_measured_doc("2026-10-07", cps=[1]))
    day7 = env.ledger.post("instagram", published_at=THU - timedelta(days=7, hours=1), format="video",
                           metrics=_measured_doc("2026-10-07", cps=[1, 3]))
    not_due = env.ledger.post("tiktok", published_at=THU - timedelta(days=2, hours=1), format="video",
                              metrics=_measured_doc("2026-10-07", cps=[1]))
    env.up.default = up_answer("facebook")
    for pid, platform in ((day1, "tiktok"), (day3, "youtube"), (day7, "instagram"), (not_due, "tiktok")):
        job = env.ledger.row(pid)["metadata"]["publish"]["upload_post"]["poll_id"]
        env.up.answers[job] = up_answer(platform)
    await env.measure(THU)
    assert len(env.up.calls) == 40
    # first the due videos, each group in today's order (never measured first, then newest published)
    assert [c[1] for c in env.up.calls[:3]] == ["tiktok", "youtube", "instagram"]
    for pid, cp, cps in ((day1, 1, [1]), (day3, 3, [1, 3]), (day7, 7, [1, 3, 7])):
        m = env.ledger.metrics(pid)
        assert (m["status"], m["last"]["cp"], m["cps"]) == ("ok", cp, cps), pid
    assert env.ledger.metrics(not_due)["measured_day"] == "2026-10-07"     # not due: waits its turn
    assert sum(1 for p in fillers if env.ledger.metrics(p)) == 37
    assert env.jobs.finishes[-1]["success"] is True


@pytest.mark.asyncio
async def test_a_video_is_read_at_each_fixed_age_and_a_picture_post_never_gets_checkpoints(env):
    env.up_configured = True
    published = et(2026, 10, 8, 16, 20)                                        # Thu 16:20 ET, like a real run
    video = env.ledger.post("tiktok", published_at=published, format="video")
    image = env.ledger.post("threads", published_at=published, format="image")
    env.up.answers = {"up-job-0001": up_answer("tiktok"), "up-job-0002": up_answer("threads")}
    cps_seen: List[Optional[int]] = []
    for day in range(0, 9):                                                    # the 06:00 ET measure, daily
        await env.measure(et(2026, 10, 9 + day, 6, 5))
        cps_seen.append(env.ledger.metrics(video)["last"]["cp"])
    # read every day (forty reads leave room), but only the reads AT a fixed age carry it
    assert cps_seen == [None, 1, None, 3, None, None, None, 7, None]
    assert env.ledger.metrics(video)["cps"] == [1, 3, 7]
    assert [h["cp"] for h in env.ledger.metrics(video)["history"]].count(None) == 6
    assert "cps" not in env.ledger.metrics(image)
    assert all(h["cp"] is None for h in env.ledger.metrics(image)["history"])


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    upload_post.UploadPostAmbiguousError("upload-post get_post_analytics: HTTP 502", method="m", status=502),
    RuntimeError("a bug in our own code"),
], ids=["ambiguous", "bug"])
async def test_a_failing_checkpoint_read_uses_its_checkpoint_up_and_never_heads_the_queue_again(env, error):
    """A due video goes first, and an unknown outcome stops Upload-Post for the tick. If the failed read
    did not use its checkpoint up, the post would stay due — first in line every day, stopping every tick
    there — and no other Upload-Post post would be read for 30 days."""
    env.up_configured = True
    others = [env.ledger.post("linkedin", published_at=THU - timedelta(days=2, minutes=i), format="image")
              for i in range(3)]
    failing = env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=1), format="video")
    env.up.default = up_answer("linkedin")
    env.up.answers = {"up-job-0004": error}
    await env.measure(THU)
    assert env.up.calls == [("up-job-0004", "tiktok")]                        # first, and it stopped the tick
    bad = env.ledger.metrics(failing)
    assert (bad["status"], bad["cps"]) == ("error", [1]) and "last" not in bad
    assert all(env.ledger.metrics(p) == {} for p in others)
    await env.measure(THU + timedelta(days=1))                                 # no longer due: the others first
    assert [c[0] for c in env.up.calls[1:4]] == ["up-job-0001", "up-job-0002", "up-job-0003"]
    assert all(env.ledger.metrics(p)["status"] == "ok" for p in others)


@pytest.mark.asyncio
async def test_a_refusal_about_upload_post_itself_never_uses_a_checkpoint_up(env):
    env.up_configured = True
    pid = env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=1), format="video")
    env.up.default = upload_post.UploadPostRateLimitError("upload-post: HTTP 429", method="m", status=429)
    await env.measure(THU)
    assert env.ledger.metrics(pid) == {}                                      # nothing recorded: still due
    env.up.default = up_answer("tiktok")
    await env.measure(THU + timedelta(hours=20))                              # the next ET day
    assert env.ledger.metrics(pid)["last"]["cp"] == 1 and env.ledger.metrics(pid)["cps"] == [1]


@pytest.mark.asyncio
async def test_the_video_measures_land_through_the_one_writer_and_the_fence_is_untouched(env):
    env.up_configured = True
    pid = env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=2), format="video")
    before = env.ledger.row(pid)
    env.up.default = up_answer("tiktok", metrics=copy.deepcopy(TIKTOK_DOC_METRICS))
    await env.measure(THU)
    after = env.ledger.row(pid)
    assert after["metadata"] == before["metadata"] and after["updated_at"] == before["updated_at"]
    last = after["metrics"]["last"]
    assert last["video"] == ms.extract_video_metrics(TIKTOK_DOC_METRICS, "tiktok") and last["cp"] == 1
    assert last["views"] == 5200 and after["metrics"]["history"][-1] == last
    # a YouTube read whose live answer carries only the four counts stores no `video` at all
    yt = env.ledger.post("youtube", published_at=THU - timedelta(days=1, hours=2), format="video")
    env.up.answers = {"up-job-0002": up_answer("youtube", metrics={"views": 109, "likes": 2, "comments": 0,
                                                                     "favorites": 0})}
    await env.measure(THU + timedelta(days=1))
    assert "video" not in env.ledger.metrics(yt)["last"] and env.ledger.metrics(yt)["last"]["views"] == 109


@pytest.mark.asyncio
async def test_the_post_metrics_key_set_is_logged_once_a_day_per_platform_never_a_value(env, caplog):
    env.up_configured = True
    for platform, age in (("tiktok", 2), ("tiktok", 3), ("youtube", 2)):
        env.ledger.post(platform, published_at=THU - timedelta(days=age), format="video")
    hostile = {**TIKTOK_DOC_METRICS, "secret token=abc\nforged": 77_777}
    env.up.answers = {"up-job-0001": up_answer("tiktok", metrics=hostile), "up-job-0002": up_answer("tiktok"),
                      "up-job-0003": up_answer("youtube", metrics={"views": 109, "likes": 2, "comments": 0,
                                                                     "favorites": 0})}
    await env.measure(THU)
    lines = _messages(caplog, "post_metrics keys for", logging.INFO)
    assert len(lines) == 2
    by_platform = {ln.split("keys for ")[1].split(" ")[0]: ln for ln in lines}
    assert by_platform["youtube"].endswith(": comments,favorites,likes,views")
    assert "(1 read(s), aged 48.0-48.0 h;" in by_platform["youtube"]
    tiktok = by_platform["tiktok"]
    assert "average_time_watched" in tiktok and "retention" in tiktok and "?" in tiktok
    assert "(2 read(s), aged 48.0-72.0 h;" in tiktok                         # BOTH TikTok reads, one line
    for value in ("5200", "0.184", "77777", "77,777", "forged", "token", "For You"):
        assert value not in tiktok
    await env.measure(THU + timedelta(days=1))                                 # a new ET day: logged again
    assert len(_messages(caplog, "post_metrics keys for", logging.INFO)) == 4


@pytest.mark.asyncio
async def test_the_key_set_line_is_the_union_so_an_older_videos_watch_fields_show(env, caplog):
    """MD-5 (2026-10-16): only the FIRST read of a day was logged, and due videos go first — so a day's YouTube
    line was its youngest video's, at ~1.6 days, often before YouTube's Analytics report exists. The owner reads
    this line to decide whether to build the cached-route read: the union of every read must show the watch
    fields an older video carries."""
    env.up_configured = True
    young = env.ledger.post("youtube", published_at=THU - timedelta(days=1, hours=14), format="video")   # due: first
    old = env.ledger.post("youtube", published_at=THU - timedelta(days=12), format="video",
                          metrics=_measured_doc("2026-10-07", cps=[1, 3, 7]))
    env.up.answers = {
        "up-job-0001": up_answer("youtube", metrics={"views": 3, "likes": 0, "comments": 0, "favorites": 0}),
        "up-job-0002": up_answer("youtube", metrics={"views": 109, "likes": 2, "comments": 0, "favorites": 0,
                                                     "average_view_duration_seconds": 22, "watch_time_minutes": 40,
                                                     "average_view_percentage": 37.7}),
    }
    await env.measure(THU)
    assert [c[0] for c in env.up.calls] == ["up-job-0001", "up-job-0002"]   # the young one was read first
    (line,) = _messages(caplog, "post_metrics keys for youtube", logging.INFO)
    assert line.endswith(": average_view_duration_seconds,average_view_percentage,comments,favorites,likes,views,"
                         "watch_time_minutes")
    assert "(2 read(s), aged 38.0-288.0 h;" in line
    for value in ("109", "37.7", "22", "40"):
        assert value not in line.rsplit(": ", 1)[1]
    assert env.ledger.metrics(young)["status"] == "ok" and env.ledger.metrics(old)["status"] == "ok"


@pytest.mark.asyncio
async def test_the_union_is_logged_even_when_upload_post_stops_mid_run_and_stays_bounded(env, caplog):
    """The line comes from a `finally`: a read that stops the tick (an unknown outcome) still leaves the line
    for what was read. A hostile answer with thousands of plain keys cannot grow the union without bound,
    nor crowd out the fields this module reads (listed first)."""
    env.up_configured = True
    for i in range(3):
        env.ledger.post("tiktok", published_at=THU - timedelta(days=2, minutes=i))
    flood = {f"k{i:05d}": i for i in range(5_000)}
    env.up.answers = {"up-job-0001": up_answer("tiktok", metrics={**flood, "views": 5, "likes": 1}),
                      "up-job-0002": upload_post.UploadPostAmbiguousError("upload-post: HTTP 502", method="m",
                                                                           status=502)}
    await env.measure(THU)
    assert len(env.up.calls) == 2                                             # the 502 stopped the tick
    (line,) = _messages(caplog, "post_metrics keys for tiktok", logging.INFO)
    shown = line.rsplit(": ", 1)[1].split(",")
    assert shown[:2] == ["likes", "views"]                                   # the read fields first, never crowded out
    assert shown[-1].startswith("+") and shown[-1].endswith(" more") and len(shown) == ms._KEY_SET_MAX + 1
    assert len(line) < 1_000


@pytest.mark.asyncio
async def test_a_late_video_read_is_never_labelled_with_a_checkpoint_it_missed(env):
    """MD-2 (2026-10-16): on the deploy day no video had `cps`, so every video since launch was read FIRST and
    labelled with the largest checkpoint it had crossed — a 15-day-old video stored as a 7-day read, the input
    the week-4 review compares on. A video more than a day past its checkpoint now waits its turn in the
    rotation, is stored with `cp` None, and uses up every checkpoint it crossed; one inside its window goes
    first and keeps its label — and a late one is labelled again at its NEXT checkpoint, on time."""
    env.up_configured = True
    backlog = env.ledger.post("youtube", published_at=THU - timedelta(days=15), format="video",
                              metrics=_measured_doc("2026-10-05"))              # read before Batch 1: no cps
    four = env.ledger.post("tiktok", published_at=THU - timedelta(days=4, hours=12), format="video",
                           metrics=_measured_doc("2026-10-05"))
    fresh = env.ledger.post("instagram", published_at=THU - timedelta(days=1, hours=2), format="video")
    seven = env.ledger.post("youtube", published_at=THU - timedelta(days=7, hours=14), format="video",
                            metrics=_measured_doc("2026-10-05", cps=[1, 3]))
    for pid in (backlog, four, fresh, seven):
        row = env.ledger.row(pid)
        env.up.answers[row["metadata"]["publish"]["upload_post"]["poll_id"]] = up_answer(row["platform"])
    jobs = {pid: env.ledger.row(pid)["metadata"]["publish"]["upload_post"]["poll_id"]
            for pid in (backlog, four, fresh, seven)}
    await env.measure(THU)
    # the two inside their windows first (never measured first), then the late ones in the rotation
    assert [c[0] for c in env.up.calls] == [jobs[fresh], jobs[seven], jobs[four], jobs[backlog]]
    for pid, cp, cps in ((fresh, 1, [1]), (seven, 7, [1, 3, 7]), (four, None, [1, 3]), (backlog, None, [1, 3, 7])):
        m = env.ledger.metrics(pid)
        assert (m["status"], m["last"]["cp"], m["cps"]) == ("ok", cp, cps), pid
        assert m["history"][-1]["cp"] == cp
    # three days on, `four` is 7.5 days old: its 7-day read is on time and labelled
    await env.measure(THU + timedelta(days=3))
    m = env.ledger.metrics(four)
    assert (m["last"]["cp"], m["cps"]) == (7, [1, 3, 7])
    assert env.ledger.metrics(backlog)["cps"] == [1, 3, 7] and env.ledger.metrics(backlog)["last"]["cp"] is None


@pytest.mark.asyncio
async def test_a_checkpoint_missed_on_a_stopped_day_is_read_later_unlabelled(env):
    """A due read that Upload-Post refused for the day (a 429 records nothing) is a day late tomorrow: read in
    the rotation, stored with `cp` None — never as the 1-day read it was not — and the next one is on time."""
    env.up_configured = True
    pid = env.ledger.post("tiktok", published_at=et(2026, 10, 7, 16, 20), format="video")
    env.up.default = upload_post.UploadPostRateLimitError("upload-post: HTTP 429", method="m", status=429)
    await env.measure(et(2026, 10, 9, 6, 5))                    # 1.6 days: due for 1, but rate limited
    assert env.ledger.metrics(pid) == {}
    env.up.default = up_answer("tiktok")
    await env.measure(et(2026, 10, 10, 6, 5))                   # 2.6 days: a day late for 1
    m = env.ledger.metrics(pid)
    assert (m["status"], m["last"]["cp"], m["cps"]) == ("ok", None, [1])
    await env.measure(et(2026, 10, 11, 6, 5))                   # 3.6 days: the 3-day read, on time
    m = env.ledger.metrics(pid)
    assert (m["last"]["cp"], m["cps"]) == (3, [1, 3])
    assert [h["cp"] for h in m["history"]] == [None, 3]


def _up_job(env: Env, pid: str) -> str:
    return env.ledger.row(pid)["metadata"]["publish"]["upload_post"]["poll_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [
    upload_post.UploadPostReauthError("upload-post get_post_analytics: HTTP 400 [tiktok_reconnect_required]",
                                      method="m", status=400, error_code="tiktok_reconnect_required"),
    upload_post.UploadPostReauthError("upload-post get_post_analytics: HTTP 403 [account_checkpoint_required]",
                                      method="m", status=403, error_code="account_checkpoint_required"),
    upload_post.UploadPostRateLimitError("upload-post get_post_analytics: HTTP 403 [account_restricted] "
                                         "(retry_at=None)", method="m", status=403, error_code="account_restricted"),
    upload_post.UploadPostRateLimitError("upload-post get_post_analytics: HTTP 429 [Account_Restricted] "
                                         "(retry_at=None)", method="m", status=429, error_code=" Account_Restricted"),
    # the client's own text for a 400 `invalid_platforms` (`upload_post._raise_for`)
    upload_post.UploadPostNotConnectedError("upload-post get_post_analytics: HTTP 400: Some platforms are not "
                                            "connected (invalid_platforms: tiktok: Profile caydex has no Tiktok "
                                            "account configured)", method="m", status=400,
                                            detail="Some platforms are not connected"),
], ids=["reconnect", "meta_checkpoint", "restricted", "restricted_any_case", "not_connected"])
async def test_a_refused_social_account_skips_only_its_own_platform(env, refusal):
    """MD-3 (2026-10-16): a refusal about ONE social account stopped every Upload-Post read for the day — and
    the refused due video headed the queue again the next day, so a TikTok that needed reconnecting starved
    YouTube, Instagram and the rest of their 1/3/7-day reads until the owner acted. Now only that platform is
    skipped (its other posts without a call), the others are read, nothing is recorded on the refused posts
    (their checkpoints are not used), and the job's note names the account to fix. A platform not connected
    to the profile (400 `invalid_platforms`) is the same kind of refusal (2026-10-19): it used to record
    `error` on every post of that platform, one call each a day, using up their checkpoints."""
    env.up_configured = True
    tiktok = [env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=h), format="video") for h in (1, 2)]
    others = [env.ledger.post(p, published_at=THU - timedelta(days=1, hours=3), format=f)
              for p, f in (("youtube", "video"), ("instagram", "video"), ("facebook", "image"))]
    for pid in others:
        env.up.answers[_up_job(env, pid)] = up_answer(env.ledger.row(pid)["platform"])
    for pid in tiktok:
        env.up.answers[_up_job(env, pid)] = refusal
    await env.measure(THU)
    assert [c[1] for c in env.up.calls].count("tiktok") == 1                  # the second TikTok: no call
    assert all(env.ledger.metrics(p) == {} for p in tiktok)                   # nothing recorded, nothing used
    for pid in others:
        m = env.ledger.metrics(pid)
        assert m["status"] == "ok", pid
    assert env.ledger.metrics(others[0])["last"]["cp"] == 1 and env.ledger.metrics(others[1])["cps"] == [1]
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True
    # the platform and the refusal's class lead the note, so the NOTE_MAX cap never cuts what to fix
    assert fin["error"] == ms.clean_note(f"upload_post tiktok stopped for 2026-10-08: {type(refusal).__name__}: "
                                         f"{refusal}")
    assert fin["error"].startswith(f"upload_post tiktok stopped for 2026-10-08: {type(refusal).__name__}: ")
    # another attempt the same ET day (e.g. the day held open by X): TikTok is skipped WITHOUT a call, and the
    # note still names it
    env.jobs.rows[ms.JOB_METRICS]["run_day"] = None
    ms._DAY_JOBS.clear()
    calls = len(env.up.calls)
    await env.measure(THU + timedelta(hours=2))
    assert len(env.up.calls) == calls and env.jobs.finishes[-1]["error"] == fin["error"]
    # the next ET day TikTok is asked again — one call, then skipped again while the account is refused
    await env.measure(THU + timedelta(days=1))
    assert [c[1] for c in env.up.calls[calls:]].count("tiktok") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    upload_post.UploadPostAuthError("upload-post: HTTP 401", method="m", status=401),
    upload_post.UploadPostQuotaError("upload-post: HTTP 429 (usage 10/10)", method="m", status=429),
    upload_post.UploadPostRateLimitError("upload-post: HTTP 429 [rate_limited]", method="m", status=429,
                                         error_code="rate_limited"),
    upload_post.UploadPostRateLimitError("upload-post: HTTP 429", method="m", status=429),
], ids=["auth", "quota", "429_other_code", "429_no_code"])
async def test_a_refusal_about_upload_post_itself_still_stops_every_platform(env, error):
    """The other side of MD-3: a refused key, the monthly quota, or a plain 429 is Upload-Post-wide — after
    it, no other platform is called this run."""
    env.up_configured = True
    first = env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=1), format="video")
    env.ledger.post("youtube", published_at=THU - timedelta(days=1, hours=2), format="video")
    env.up.default = up_answer("youtube")
    env.up.answers = {_up_job(env, first): error}
    await env.measure(THU)
    assert len(env.up.calls) == 1 and env.ledger.metrics(first) == {}
    assert env.jobs.finishes[-1]["success"] is True


def test_the_parked_account_code_is_the_clients_own():
    assert ms._ACCOUNT_RESTRICTED == upload_post._RESTRICTED_CODE
    reauth = upload_post.UploadPostReauthError("x", method="m", status=400)
    assert ms._account_refusal(reauth)                                        # any reconnect, with or without a code
    assert ms._account_refusal(upload_post.UploadPostNotConnectedError("x", method="m", status=400))
    # a refusal the client did not type as one account's stays Upload-Post-wide (or about the post)
    for other in (upload_post.UploadPostRefusedError("x", method="m", status=400),
                  upload_post.UploadPostPlanError("x", method="m", status=403)):
        assert not ms._account_refusal(other)
    for code, expected in (("account_restricted", True), ("ACCOUNT_RESTRICTED ", True), ("rate_limited", False),
                           (None, False), (7, False), ("", False)):
        e = upload_post.UploadPostRateLimitError("x", method="m", status=429, error_code=code)
        assert ms._account_refusal(e) is expected, code
    # the code alone on another class is not a parked account (a refused credential stays Upload-Post-wide)
    for other in (upload_post.UploadPostAuthError("x", method="m", status=401, error_code="account_restricted"),
                  upload_post.UploadPostAmbiguousError("x", method="m", status=502, error_code="account_restricted"),
                  RuntimeError("account_restricted")):
        assert not ms._account_refusal(other)


def _youtube_video_post(env: Env, *, seconds: Any, share: Any, asset_status: str = "ready",
                        asset_ids: Any = _AUTO) -> str:
    """A YouTube video post whose run holds its video asset (`duration_seconds` = `seconds`), and an answer
    carrying both watch fields (22 s on average, `share` %)."""
    run_id, vid = str(uuid.uuid4()), str(uuid.uuid4())
    env.ledger.fake.tables[mrs.ASSETS].rows.append({"id": vid, "run_id": run_id, "kind": "video",
                                                    "status": asset_status, "duration_seconds": seconds,
                                                    "storage_path": f"x/{vid}.mp4", "metadata": {}})
    pid = env.ledger.post("youtube", published_at=THU - timedelta(days=1, hours=14), format="video", run_id=run_id,
                          asset_ids=[vid] if asset_ids is _AUTO else asset_ids)
    env.up.answers[_up_job(env, pid)] = up_answer("youtube", metrics={**_YT_WATCH, "average_view_percentage": share})
    return pid


@pytest.mark.asyncio
@pytest.mark.parametrize("share, kept", [(37.7, True), (0.377, False), (135.5, False)])
async def test_a_youtube_share_that_disagrees_with_the_videos_own_length_is_omitted(env, caplog, share, kept):
    """MD-4 at the measure step: the video's length comes from its run's ready video asset (one read of ours,
    only when both watch fields arrived); a share on another scale is omitted — and said so — while the
    duration and the counts land as read."""
    env.up_configured = True
    pid = _youtube_video_post(env, seconds=58.4, share=share)
    await env.measure(THU)
    video = env.ledger.metrics(pid)["last"]["video"]
    assert video["average_view_duration_seconds"] == 22.0 and env.ledger.metrics(pid)["last"]["views"] == 109
    assert ("average_view_percentage" in video) is kept
    warned = _messages(caplog, "disagrees with its average view duration", logging.WARNING)
    assert (len(warned) == 0) is kept
    if not kept:
        assert pid in warned[0] and str(share) in warned[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_asset_ids", "asset_not_ready", "length_unreadable", "two_videos",
                                  "read_fails"])
async def test_a_youtube_share_whose_video_length_is_unknown_is_kept_as_bounded(env, caplog, monkeypatch, case):
    """"Otherwise keep it": with no trustworthy length the share is stored as its bounds left it — and a
    failed length read is logged, never a failed day."""
    env.up_configured = True
    kw: Dict[str, Any] = {"seconds": 58.4, "share": 0.377}
    if case == "no_asset_ids":
        kw["asset_ids"] = []
    elif case == "asset_not_ready":
        kw["asset_status"] = "pending_upload"
    elif case == "length_unreadable":
        kw["seconds"] = None
    pid = _youtube_video_post(env, **kw)
    if case == "two_videos":
        row = env.ledger.raw(pid)
        second = str(uuid.uuid4())
        env.ledger.fake.tables[mrs.ASSETS].rows.append({"id": second, "run_id": row["run_id"], "kind": "video",
                                                        "status": "ready", "duration_seconds": 30.0,
                                                        "storage_path": "x/2.mp4", "metadata": {}})
        row["asset_ids"] = row["asset_ids"] + [second]
    if case == "read_fails":
        monkeypatch.setattr(env.ledger.svc, "list_assets",
                            _Exploding(mrs.MarketingRunError("list_assets: PostgREST 503")))
    await env.measure(THU)
    m = env.ledger.metrics(pid)
    assert m["status"] == "ok" and m["last"]["video"]["average_view_percentage"] == 0.377
    assert env.jobs.finishes[-1]["success"] is True
    if case == "read_fails":
        assert _messages(caplog, "could not be read (list_assets", logging.WARNING)


@pytest.mark.asyncio
async def test_the_video_length_is_read_only_for_a_youtube_answer_with_both_watch_fields(env, monkeypatch):
    env.up_configured = True
    reads: List[str] = []
    original = env.ledger.svc.list_assets

    async def counting(run_id):
        reads.append(run_id)
        return await original(run_id)

    monkeypatch.setattr(env.ledger.svc, "list_assets", counting)
    env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=2), format="video", asset_ids=["a"])
    env.up.answers["up-job-0001"] = up_answer("tiktok", metrics=copy.deepcopy(TIKTOK_DOC_METRICS))
    yt = env.ledger.post("youtube", published_at=THU - timedelta(days=1, hours=2), format="video", asset_ids=["b"])
    env.up.answers[_up_job(env, yt)] = up_answer("youtube", metrics={"views": 109, "likes": 2, "comments": 0,
                                                                     "favorites": 0, "average_view_percentage": 0.4})
    await env.measure(THU)
    assert reads == []                                    # TikTok, and a YouTube answer with one watch field
    assert env.ledger.metrics(yt)["last"]["video"] == {"average_view_percentage": 0.4}


@pytest.mark.asyncio
@pytest.mark.parametrize("error,recorded", [
    # About Upload-Post itself, never this post: nothing is recorded on it.
    (upload_post.UploadPostRateLimitError("upload-post: HTTP 429", method="m", status=429), False),
    (upload_post.UploadPostNotSentError("upload-post: not sent", method="m"), False),
    (upload_post.UploadPostAuthError("upload-post: HTTP 401", method="m", status=401), False),
    (upload_post.UploadPostReauthError("upload-post: HTTP 400 [account_reauth_required]", method="m", status=400),
     False),
    (upload_post.UploadPostNotConnectedError("upload-post: HTTP 400: none (invalid_platforms: linkedin: not "
                                             "connected)", method="m", status=400), False),
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
    # A refused credential stops Upload-Post for the day, and the job's note says so; a refused social ACCOUNT
    # (a reconnect, a platform not connected to the profile) stops only its own platform (MD-3) — the second
    # LinkedIn post is skipped without a call; a passing failure (a rate limit, a request that never left, one
    # post's unknown outcome) is not a stop.
    note = env.jobs.finishes[-1]["error"]
    if isinstance(error, upload_post.UploadPostAuthError):
        assert note == f"upload_post stopped for 2026-10-08: {type(error).__name__}: {error}"
    elif isinstance(error, (upload_post.UploadPostReauthError, upload_post.UploadPostNotConnectedError)):
        assert note == f"upload_post linkedin stopped for 2026-10-08: {type(error).__name__}: {error}"
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


# the published-posts read, in pages (MD-7, 2026-10-16) ───────────────────────


class _Pages:
    """Counts the pages a read takes through the REAL `list_measurable_posts` (oldest first, `published_at` at or
    after `since`, LIMIT) over the PostgREST fake."""

    def __init__(self, svc: Any) -> None:
        self.svc, self.calls = svc, []
        self.original = svc.list_measurable_posts

    async def list_measurable_posts(self, **kw: Any) -> List[Dict[str, Any]]:
        self.calls.append(kw)
        return await self.original(**kw)


def _published_rows(ledger: Ledger, times: List[datetime], platform: str = "bluesky") -> List[str]:
    return [ledger.post(platform, published_at=t) for t in times]


@pytest.mark.asyncio
@pytest.mark.parametrize("n, pages", [(0, 1), (1, 1), (299, 1), (300, 2), (301, 2), (700, 3)])
async def test_the_paged_read_returns_every_post_oldest_first_and_never_twice(ledger, n, pages):
    since = THU - timedelta(days=30)
    pids = _published_rows(ledger, [since + timedelta(minutes=5 * i) for i in range(n)])
    read = _Pages(ledger.svc)
    rows, capped = await ms.list_published_posts(read, since=since, page_limit=300, max_pages=4, what="test")
    assert [r["id"] for r in rows] == pids and capped is False                 # every one, oldest first, once
    assert len(read.calls) == pages and read.calls[0]["since"] == since


@pytest.mark.asyncio
async def test_posts_sharing_the_page_boundary_instant_are_neither_lost_nor_doubled(ledger):
    """A page ends inside a run of posts published at ONE instant: the next page starts AT that instant
    (inclusive), so the ones the limit cut are read, and the ones already read are kept once."""
    since = THU - timedelta(days=30)
    tied = since + timedelta(days=10)
    times = ([since + timedelta(minutes=i) for i in range(8)] + [tied] * 5
             + [tied + timedelta(minutes=i + 1) for i in range(8)])
    pids = _published_rows(ledger, times)
    read = _Pages(ledger.svc)
    rows, capped = await ms.list_published_posts(read, since=since, page_limit=10, max_pages=5, what="test")
    assert sorted(r["id"] for r in rows) == sorted(pids) and len(rows) == len(pids) == 21 and capped is False
    assert [mrs._parse_ts(r["published_at"]) for r in rows] == sorted(times)    # still oldest first
    assert [c["since"] for c in read.calls][1] == tied                          # page 2 starts AT the tie


@pytest.mark.asyncio
async def test_a_read_past_its_pages_is_capped_loudly_and_keeps_what_it_read(ledger, caplog):
    caplog.set_level(logging.ERROR, logger=MS_LOGGER)
    since = THU - timedelta(days=30)
    pids = _published_rows(ledger, [since + timedelta(minutes=i) for i in range(50)])
    rows, capped = await ms.list_published_posts(_Pages(ledger.svc), since=since, page_limit=10, max_pages=3,
                                                 what="test")
    assert capped is True and [r["id"] for r in rows] == pids[:28]             # 10 + 9 + 9: the newest missing
    assert _messages(caplog, "full pages of 10 published posts", logging.ERROR)


@pytest.mark.asyncio
async def test_a_full_page_at_one_instant_cannot_loop_forever(ledger, caplog):
    caplog.set_level(logging.ERROR, logger=MS_LOGGER)
    since = THU - timedelta(days=30)
    at = since + timedelta(days=1)
    _published_rows(ledger, [at] * 7)
    read = _Pages(ledger.svc)
    rows, capped = await ms.list_published_posts(read, since=since, page_limit=5, max_pages=50, what="test")
    assert capped is True and len(rows) == 5 and len(read.calls) == 2           # one retry AT the instant, then stop
    assert _messages(caplog, "cannot move the read on", logging.ERROR)


class _Scripted:
    """`list_measurable_posts` answering scripted pages (a list, an exception, or junk), in order."""

    def __init__(self, *answers: Any) -> None:
        self.answers, self.calls = list(answers), 0

    async def list_measurable_posts(self, **_kw: Any) -> Any:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.mark.asyncio
async def test_the_paged_read_fails_loudly_on_a_failed_or_junk_page_and_a_timeless_full_page_is_capped(caplog):
    since = THU - timedelta(days=30)
    late = (since + timedelta(days=1)).isoformat()
    first = [{"id": f"p{i}", "published_at": late} for i in range(2)]
    with pytest.raises(mrs.MarketingRunError):                                 # a failed page: the caller's path
        await ms.list_published_posts(_Scripted(first, mrs.MarketingRunError("503")), since=since,
                                      page_limit=2, max_pages=3, what="test")
    for junk in (None, {"rows": []}, "[]"):                                    # never read as "no more posts"
        with pytest.raises(TypeError):
            await ms.list_published_posts(_Scripted(junk), since=since, page_limit=2, max_pages=3, what="test")
    # a full page with no readable time cannot move the read on: capped, the rows it had kept
    timeless = [{"id": "a", "published_at": None}, {"id": "b", "published_at": "junk"}]
    rows, capped = await ms.list_published_posts(_Scripted(timeless), since=since, page_limit=2, max_pages=3,
                                                 what="test")
    assert capped is True and [r["id"] for r in rows] == ["a", "b"]
    # junk rows inside a short page are skipped, never a crash
    rows, capped = await ms.list_published_posts(_Scripted([None, "x", {"id": "c", "published_at": late}]),
                                                 since=since, page_limit=5, max_pages=3, what="test")
    assert [r["id"] for r in rows] == ["c"] and capped is False
    for bad in (0, -1, None, 2.0, True):
        with pytest.raises(ValueError):
            await ms.list_published_posts(_Scripted([]), since=since, page_limit=bad, max_pages=3, what="test")


@pytest.mark.asyncio
async def test_yesterdays_video_is_measured_when_the_window_holds_more_than_one_page(env, monkeypatch):
    """MD-7: `list_measurable_posts` answers OLDEST first, so the old single read of 300 dropped the NEWEST posts
    once 30 days held more — at Batch 3's cadence (~11 a day, every day) that is yesterday's posts and their
    1-day checkpoints. 320 older posts (already measured today) and yesterday's TikTok video: it is read, for 1."""
    today = {"v": 1, "rev": 1, "status": "ok", "measured_day": "2026-10-08", "note": None,
             "unavailable_until": None}
    for i in range(320):
        env.ledger.post("bluesky", published_at=THU - timedelta(days=29) + timedelta(minutes=100 * i),
                        metrics=dict(today))
    env.up_configured = True
    video = env.ledger.post("tiktok", published_at=THU - timedelta(days=1, hours=2), format="video")
    env.up.answers[_up_job(env, video)] = up_answer("tiktok")
    read = _Pages(env.ledger.svc)
    monkeypatch.setattr(env.ledger.svc, "list_measurable_posts", read.list_measurable_posts)
    await env.measure(THU)
    assert len(read.calls) == 2 and env.up.calls == [(_up_job(env, video), "tiktok")]
    m = env.ledger.metrics(video)
    assert (m["status"], m["last"]["cp"], m["cps"]) == ("ok", 1, [1])
    assert env.jobs.finishes[-1]["error"] is None                            # not capped: nothing to name


@pytest.mark.asyncio
async def test_a_capped_measure_read_is_named_in_the_jobs_note(env, monkeypatch):
    monkeypatch.setattr(ms, "MEASURE_SCAN_LIMIT", 5)
    monkeypatch.setattr(ms, "MEASURE_SCAN_PAGES", 2)
    for i in range(12):
        env.ledger.post("bluesky", published_at=THU - timedelta(days=20) + timedelta(hours=i))
    await env.measure(THU)
    fin = env.jobs.finishes[-1]
    assert fin["success"] is True
    assert fin["error"] == "the published-posts read stopped at 9 rows — the newest posts were not read"


def test_the_paged_reads_hold_every_post_the_schema_allows():
    """MD-7 — the record of why the paged reads cannot drop the NEWEST posts. One run a day
    (`marketing_runs.run_date` UNIQUE) records at most one post per (platform, format) (`marketing_posts`
    UNIQUE (run_id, platform, format) over POST_FORMATS_BY_PLATFORM plus, since Batch 3, the extra videos of
    EXTRA_POST_FORMATS_BY_PLATFORM: 13 + 3 = 16 pairs); a post goes out on its run day or the next (a week of
    slack covers a late reconcile). So a window of D days holds at most (D + 7) × pairs published posts — inside
    pages × page size for both reads, with room kept for one more pair (17). Widen the maps past it and this
    fails: raise the page count with it."""
    from app.schemas.marketing import EXTRA_POST_FORMATS_BY_PLATFORM, POST_FORMATS_BY_PLATFORM

    pairs = len({(platform, fmt) for formats_by in (POST_FORMATS_BY_PLATFORM, EXTRA_POST_FORMATS_BY_PLATFORM)
                 for platform, formats in formats_by.items() for fmt in formats})
    assert pairs >= 16                       # the extras are counted (a later pair only tightens the bound below)
    for days, page, pages in ((ms.MEASURE_MAX_AGE.days, ms.MEASURE_SCAN_LIMIT, ms.MEASURE_SCAN_PAGES),
                              (ds.FOLLOWER_LOOKBACK.days, ds.MEASURED_POST_LIMIT, ds.MEASURED_POST_PAGES)):
        assert (days + 7) * max(pairs, 17) <= page * pages, (days, pairs, page, pages)
        assert 0 < page < 1_000                  # under PostgREST's max-rows: a page it cut would pass for the last
    assert ds.MEASURED_POST_MAX == ds.MEASURED_POST_LIMIT * ds.MEASURED_POST_PAGES


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


#: Batch 1 item 10 (2026-10-15): what a run that failed AFTER its posts were recorded says instead — worded
#: (MD-1, 2026-10-16) to stay true however the owner acted since: the bot offers them at once, so by 22:00 they
#: may be approved and live, or rejected. Never "still offers them for your decision".
POSTS_RECORDED = ("Its posts were recorded before it failed, so they go to the review chat like any day's; anything "
                  "still undecided waits there.")
#: The phrases MD-1 retired: each claimed the posts were still undecided, from the run row alone.
STALE_POST_CLAIMS = ("still offers them for your decision", "waiting for review", "await your decision")


def test_a_run_that_failed_after_its_posts_were_recorded_says_the_review_bot_offers_them():
    """`assets_ready` is checkpointed only after `create_posts` answered: every post of the day exists and
    the review bot offers a failed run's posts at that stage — "will not go out" would be false."""
    at_cap = _health(_run("failed", stage="assets_ready", attempts=6, last_error="PATCH media_ready: HTTP 503"))
    assert at_cap == ("⚠️ Marketing run Sat 2026-10-03 FAILED after stage assets_ready (the last completed stage): all "
                      f"6 attempts used. {POSTS_RECORDED} Last error: PATCH media_ready: HTTP 503. Check the "
                      "marketing-worker logs.")
    below = _health(_run("failed", stage="assets_ready", attempts=2, last_error=None))
    assert below == ("⚠️ Marketing run Sat 2026-10-03 FAILED after stage assets_ready (the last completed stage): "
                     "attempt 2 of 6; the worker retries hourly until midnight ET and resumes it on tomorrow's early "
                     f"ticks — a final word comes tomorrow after 16:00 ET. {POSTS_RECORDED} Last error: (none "
                     "recorded). Check the marketing-worker logs.")
    unknown = _health(_run("failed", stage="assets_ready", attempts=None))
    assert "attempt ? of 6 — the attempt count is unreadable" in unknown and POSTS_RECORDED in unknown
    for msg in (at_cap, below, unknown):
        assert "will not go out" not in msg and "\n" not in msg
    # one stage earlier the posts were never recorded: the day is lost, said as before
    for stage in ("rendered", "voiced", "scripted", "selected", "planned", None, "assets_ready\n", ["assets_ready"]):
        msg = _health(_run("failed", stage=stage, attempts=6))
        assert "this day's posts will not go out" in msg and POSTS_RECORDED not in msg, stage
    # never on a run that did not fail: a live one at that stage is still running, a finished one is silent
    assert POSTS_RECORDED not in _health(_run("in_progress", stage="assets_ready", attempts=1,
                                              updated_at=_iso_of(SAT_22 - timedelta(minutes=1))))
    assert _health(_run("media_ready", stage="assets_ready")) is None


def test_the_final_word_on_a_run_that_failed_after_its_posts_were_recorded_never_says_they_will_not_go_out():
    ended = _final_word(_run("failed", stage="assets_ready", attempts=6, last_error="boom",
                             updated_at=_iso_of(SAT_22 + timedelta(minutes=80))))
    assert ended == ("⚠️ Marketing run Sat 2026-10-03 ended FAILED after stage assets_ready (the last completed "
                     f"stage): all 6 attempts used. {POSTS_RECORDED} Last error: boom.")
    unchanged = _final_word(_run("failed", stage="assets_ready", attempts=2, last_error="boom",
                                 updated_at=_iso_of(SAT_22 - timedelta(minutes=20))))
    assert unchanged == ("⚠️ Marketing run Sat 2026-10-03 is still FAILED after stage assets_ready (the last completed "
                         "stage), attempt 2 of 6: no retry happened — check the marketing-worker cron (hourly at :15) "
                         f"and its last logs. {POSTS_RECORDED} Last error: boom.")
    no_check = _final_word(_run("failed", stage="assets_ready", attempts=6,
                                updated_at=_iso_of(SAT_22 - timedelta(minutes=20))), checked_at=None)
    assert POSTS_RECORDED in no_check
    for msg in (ended, unchanged, no_check):
        assert "will not go out" not in msg
    # one stage earlier: as before
    assert "This day's posts will not go out." in _final_word(
        _run("failed", stage="rendered", attempts=6, updated_at=_iso_of(SAT_22 + timedelta(minutes=80))))


#: …and what a run ABANDONED at that stage says — a tick that died between the `assets_ready` checkpoint and
#: the `media_ready` write: the review bot offers a run's posts at that stage whatever its status
#: (`review_service.bundle_offerable`), so "the day is lost" would be false.
POSTS_RECORDED_STOPPED = ("Its posts were recorded before it stopped, so they go to the review chat like any day's; "
                          "anything still undecided waits there.")


@pytest.mark.parametrize("status", ["planned", "in_progress"])
def test_a_run_abandoned_after_its_posts_were_recorded_says_the_review_bot_offers_them(status):
    stale = _iso_of(SAT_22 - timedelta(hours=1))
    below = _health(_run(status, stage="assets_ready", attempts=3, updated_at=stale))
    assert below == (f"⚠️ Marketing run Sat 2026-10-03 is not finished yet: {status} after stage assets_ready (the "
                     f"last completed stage) — attempt 3 of 6 was abandoned; the worker retries hourly. "
                     f"{POSTS_RECORDED_STOPPED} A final word comes tomorrow after 16:00 ET.")
    at_cap = _health(_run(status, stage="assets_ready", attempts=6, last_error="PATCH media_ready: HTTP 503",
                          updated_at=stale))
    assert at_cap == (f"⚠️ Marketing run Sat 2026-10-03 is not finished yet: {status} after stage assets_ready (the "
                      f"last completed stage) — the last allowed attempt was abandoned; no retries remain — the day "
                      f"will close failed; last error: PATCH media_ready: HTTP 503. {POSTS_RECORDED_STOPPED} A final "
                      f"word comes tomorrow after 16:00 ET.")
    # still alive at that stage: it is just running — no word about its posts yet
    alive = _health(_run(status, stage="assets_ready", attempts=3, updated_at=_iso_of(SAT_22 - timedelta(minutes=2))))
    assert "still running (attempt 3 of 6)" in alive and POSTS_RECORDED_STOPPED not in alive
    # one stage earlier the posts were never recorded: exactly as before
    earlier = _health(_run(status, stage="rendered", attempts=3, updated_at=stale))
    assert earlier.endswith("— attempt 3 of 6 was abandoned; the worker retries hourly. A final word comes tomorrow "
                            "after 16:00 ET.")
    for msg in (below, at_cap, alive, earlier):
        assert POSTS_RECORDED not in msg and "\n" not in msg


@pytest.mark.parametrize("status", ["planned", "in_progress"])
def test_the_final_word_on_a_run_abandoned_after_its_posts_were_recorded_never_says_the_day_is_lost(status):
    changed = _final_word(_run(status, stage="assets_ready", attempts=3,
                               updated_at=_iso_of(SAT_22 + timedelta(hours=5, minutes=30))))     # Sun 03:30 EDT
    assert changed == (f"⚠️ Marketing run Sat 2026-10-03 never finished: {status} after stage assets_ready (the last "
                       f"completed stage), attempt 3 of 6 was abandoned and no retry is left (the worker resumes a day "
                       f"only until 16:00 ET the next day). {POSTS_RECORDED_STOPPED} Check the marketing-worker logs.")
    unchanged = _final_word(_run(status, stage="assets_ready", attempts=6,
                                 updated_at=_iso_of(SAT_22 - timedelta(minutes=20))))            # Sat 21:40 EDT
    assert unchanged == (f"⚠️ Marketing run Sat 2026-10-03 never finished: {status} after stage assets_ready (the last "
                         f"completed stage), attempt 6 of 6, untouched since 10-03 21:40 ET. {POSTS_RECORDED_STOPPED} "
                         f"Check the marketing-worker cron and its last logs.")
    no_check = _final_word(_run(status, stage="assets_ready", attempts=6,
                                updated_at=_iso_of(SAT_22 - timedelta(minutes=20))), checked_at=None)
    assert no_check.startswith(f"⚠️ Marketing run Sat 2026-10-03 never finished: {status} after stage assets_ready")
    assert POSTS_RECORDED_STOPPED in no_check
    for msg in (changed, unchanged, no_check):
        assert "the day is lost" not in msg and POSTS_RECORDED not in msg
    # one stage earlier, or a stage that only LOOKS like it: the day is lost, said as before
    for stage in ("rendered", None, "assets_ready ", ["assets_ready"]):
        msg = _final_word(_run(status, stage=stage, attempts=6, updated_at=_iso_of(SAT_22 - timedelta(minutes=20))))
        assert "never finished — the day is lost" in msg and POSTS_RECORDED_STOPPED not in msg, stage


@pytest.mark.parametrize("status", ["failed", "planned", "in_progress"])
@pytest.mark.parametrize("attempts", [2, 6, None])
def test_no_run_health_text_claims_a_recorded_runs_posts_still_await_a_decision(status, attempts):
    """MD-1 / review F1 (2026-10-16): the review bot offers an `assets_ready` run's posts as soon as the checkpoint
    lands, whatever the run's status — so by 22:00, and surely by the next day's 16:00, the owner may have
    approved them (and they went out) or rejected them. The health jobs read the run row only: no text may say
    the posts still wait. Every text that names them says only that they go to the review chat like any day's
    and that whatever is undecided waits there — true however the owner acted."""
    stale = _iso_of(SAT_22 - timedelta(hours=1))
    texts = [
        _health(_run(status, stage="assets_ready", attempts=attempts, updated_at=stale)),
        _final_word(_run(status, stage="assets_ready", attempts=attempts, updated_at=_iso_of(AFTER_22))),
        _final_word(_run(status, stage="assets_ready", attempts=attempts, updated_at=_iso_of(BEFORE_22))),
        _final_word(_run(status, stage="assets_ready", attempts=attempts, updated_at=stale), checked_at=None),
        # recovered after the check: its posts may have been decided overnight, too
        _final_word(_run("media_ready", stage="assets_ready", attempts=attempts, finished_at=_iso_of(AFTER_22))),
    ]
    spoken = [t for t in texts if t is not None]
    named = [t for t in spoken if "review chat" in t]
    assert named and texts[-1] in named                                      # the sweep is not vacuous
    for text in spoken:
        for claim in STALE_POST_CLAIMS:
            assert claim not in text, text
    for text in named:
        assert "anything still undecided waits there" in text, text


@pytest.mark.parametrize("status", ["failed", "planned", "in_progress"])
def test_a_checkpoint_that_recorded_no_post_is_said_like_an_earlier_stages_failure(status):
    """Review F1 (live regression): the worker's `stage_posts` checkpoints `assets_ready` with
    {"posts_recorded": 0} when the script names no outlet it posts to. Nothing is in the review chat and nothing
    will go out: the texts say what they say for a run that failed before its posts — never "its posts were
    recorded", never "go to the review chat"."""
    none = {"posts_recorded": 0}
    stale = _iso_of(SAT_22 - timedelta(hours=1))
    if status == "failed":
        texts = [_health(_run("failed", stage="assets_ready", attempts=6, metadata=none)),
                 _final_word(_run("failed", stage="assets_ready", attempts=6, metadata=none,
                                  updated_at=_iso_of(AFTER_22)))]
        assert "all 6 attempts used; this day's posts will not go out." in texts[0]
        assert "This day's posts will not go out." in texts[1]
        below = _health(_run("failed", stage="assets_ready", attempts=2, metadata=none))
        assert below == ("⚠️ Marketing run Sat 2026-10-03 FAILED after stage assets_ready (the last completed stage): "
                         "attempt 2 of 6; the worker retries hourly until midnight ET and resumes it on tomorrow's "
                         "early ticks — a final word comes tomorrow after 16:00 ET. Last error: (none recorded). "
                         "Check the marketing-worker logs.")
        texts.append(below)
    else:
        texts = [_health(_run(status, stage="assets_ready", attempts=3, metadata=none, updated_at=stale)),
                 _final_word(_run(status, stage="assets_ready", attempts=3, metadata=none,
                                  updated_at=_iso_of(AFTER_22)))]
        assert texts[0].endswith("— attempt 3 of 6 was abandoned; the worker retries hourly. A final word comes "
                                 "tomorrow after 16:00 ET.")
        assert "never finished — the day is lost" in texts[1]
    for text in texts:
        assert "review chat" not in text and "posts were recorded" not in text, text


def test_a_recovered_run_that_recorded_no_post_says_so():
    msg = _final_word(_run("media_ready", stage="assets_ready", metadata={"posts_recorded": 0},
                           finished_at=_iso_of(AFTER_22)))
    assert msg == ("✅ Marketing run Sat 2026-10-03 recovered at 10-03 23:20 ET — it recorded no posts, so nothing "
                   "will be posted for this day.")


@pytest.mark.parametrize("count", [None, 3, 1, True, False, "0", 0.0, -1, [0], {"n": 0}, "junk"])
def test_only_an_exact_zero_count_says_no_post_was_recorded(count):
    """`metadata.posts_recorded` is worker-written: only the exact int 0 means "none". A missing key (a run from
    before the count), a positive count or any junk keeps the recorded-posts wording — never a guess that
    there is nothing to review."""
    meta = {} if count is None else {"posts_recorded": count}
    failed = _health(_run("failed", stage="assets_ready", attempts=6, metadata=meta))
    assert POSTS_RECORDED in failed and "will not go out" not in failed
    recovered = _final_word(_run("media_ready", stage="assets_ready", metadata=meta, finished_at=_iso_of(AFTER_22)))
    assert recovered.endswith(f"— {RECOVERED_POSTS}.")
    # metadata that is not an object reads as no count at all
    assert POSTS_RECORDED in _health(_run("failed", stage="assets_ready", attempts=6, metadata="junk"))


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
    ("image_too_large", "post image"),
])
def test_each_known_skip_reason_says_where_to_look(reason, needle):
    msg = _health(_run("skipped", stage="selected", metadata={"skip_reason": reason}))
    assert msg is not None and f"SKIPPED ({reason})" in msg and needle in msg


def test_every_skip_reason_the_worker_raises_has_a_hint():
    """A new `SkipRun("…")` in the worker (drop 1 added `image_too_large`) must come with a digest
    hint, or the owner's alert reads "an unrecognised reason". AST scan of every worker module: the
    string literal passed to `SkipRun(...)` / `skip(...)` (render and voice take it injected)."""
    import ast
    from pathlib import Path

    from app.services.marketing import digest_service as ds

    pkg = Path(__file__).resolve().parents[1] / "marketing"
    raised = set()
    for path in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("SkipRun", "skip") and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                raised.add(node.args[0].value)
    assert {"unrenderable_text", "image_too_large", "rest_day"} <= raised   # the scan is not vacuous
    missing = sorted(raised - {"rest_day"} - set(ds._SKIP_HINTS))           # rest_day never alerts
    assert not missing, f"worker skip reasons with no digest hint: {missing}"


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


def _limited(limit: Any) -> Dict[str, Any]:
    """Run metadata whose preflight recorded `limit` the way the worker writes it (main.voice_readiness)."""
    return {"preflight": {"voice": {"ready": True, "memory_limit": limit}}}


@pytest.mark.parametrize("raw, mib", [
    ("7629 MiB", 7629), ("3814 MiB", 3814), ("1024 MiB", 1024),
    # unrecorded: no limit, the wrong shape, a container the worker could not run in, a non-string
    ("max", None), ("1023 MiB", None), ("0 MiB", None), ("7629MiB", None), (" 7629 MiB", None),
    ("7629 MiB\n", None), ("7629 MB", None), ("-7629 MiB", None), ("12345678 MiB", None), ("７６２９ MiB", None),
    (7629, None), (7629.0, None), (True, None), (None, None), (["7629 MiB"], None),
])
def test_the_recorded_memory_limit_is_read_only_in_the_workers_own_shape(raw, mib):
    assert ds._memory_limit(_run("published", metadata=_limited(raw))) == mib


@pytest.mark.parametrize("metadata", [None, [], "x", {"preflight": None}, {"preflight": []},
                                      {"preflight": {"voice": "7629 MiB"}}, {"preflight": {"render": {"memory_limit": "7629 MiB"}}}])
def test_a_run_without_a_readable_preflight_has_no_limit(metadata):
    run = _run("published", timings={"voiced_cgroup_peak_mb": 3345.0})
    run["metadata"] = metadata
    assert ds._memory_limit(run) is None and ds._memory_alert_mb(run) == ds.MEMORY_ALERT_MB


def test_the_alert_follows_the_recorded_limit():
    """2026-10-28 (earnings day): 3,344.9 MB in the old 4 GB container alerted; in the 8 GB one it is calm."""
    oct28 = {"voiced_cgroup_peak_mb": 3344.9, "rendered_cgroup_peak_mb": 3344.9, "voiced_children_maxrss_mb": 2206.1}
    msg = _health(_run("published", timings=oct28, metadata=_limited("3814 MiB")))
    assert msg == ("⚠️ Marketing worker memory: stage voiced peaked at 3,345 MB on Sat 2026-10-03 (alert above "
                   "3,241 MB; the worker's limit is ~3,814 MiB). Give the marketing-worker service more memory "
                   "before a stage is OOM-killed.")
    assert _health(_run("published", timings=oct28, metadata=_limited("7629 MiB"))) is None
    # 85% of 7,629 = 6,484: exactly that is not above it
    assert _health(_run("published", timings={"voiced_cgroup_peak_mb": 6484.0}, metadata=_limited("7629 MiB"))) is None
    hot = _health(_run("published", timings={"voiced_cgroup_peak_mb": 6600.0}, metadata=_limited("7629 MiB")))
    assert "stage voiced peaked at 6,600 MB" in hot and "alert above 6,484 MB; the worker's limit is ~7,629 MiB" in hot


@pytest.mark.parametrize("limit", ["max", "junk", 7629, None, "100 MiB"])
def test_an_unreadable_limit_keeps_the_old_line_and_says_so(limit):
    msg = _health(_run("published", timings={"voiced_cgroup_peak_mb": 3412.6}, metadata=_limited(limit)))
    assert "alert above 3,200 MB; the worker's limit was not recorded" in msg
    assert _health(_run("published", timings={"voiced_cgroup_peak_mb": 3200.0}, metadata=_limited(limit))) is None


def test_the_weekly_peak_is_flagged_against_its_own_runs_limit():
    calm = _digest([], runs=[_run("published", run_date="2026-09-28", timings={"voiced_cgroup_peak_mb": 3344.9},
                                  metadata=_limited("7629 MiB"))])
    assert "Worker memory peak: 3,345 MB (voiced, Mon 09-28)\n" in calm and "⚠️ above" not in calm
    hot = _digest([], runs=[_run("published", run_date="2026-09-28", timings={"voiced_cgroup_peak_mb": 3344.9},
                                 metadata=_limited("7629 MiB")),
                            _run("published", run_date="2026-09-29", timings={"voiced_cgroup_peak_mb": 7000.0},
                                 metadata=_limited("7629 MiB"))])
    assert "Worker memory peak: 7,000 MB (voiced, Tue 09-29) ⚠️ above 6,484 MB" in hot
    # the highest peak's OWN run decides: a 4 GB run's 3,300 is flagged even in a week of 8 GB runs
    mixed = _digest([], runs=[_run("published", run_date="2026-09-28", timings={"voiced_cgroup_peak_mb": 3300.0},
                                   metadata=_limited("3814 MiB")),
                              _run("published", run_date="2026-09-29", timings={"voiced_cgroup_peak_mb": 3000.0},
                                   metadata=_limited("7629 MiB"))])
    assert "Worker memory peak: 3,300 MB (voiced, Mon 09-28) ⚠️ above 3,241 MB" in mixed


@pytest.mark.parametrize("timings, limit, micros", [
    ({"preflight_s": 303.0, "voiced_cgroup_peak_mb": 9000.0}, "7629 MiB", 18_070),   # capped at the recorded limit
    ({"preflight_s": 303.0}, "7629 MiB", 18_070),                                    # no peak: the recorded limit
    ({"preflight_s": 303.0, "voiced_cgroup_peak_mb": 3345.0}, "7629 MiB", 13_177),   # the peak, not the limit
    ({"preflight_s": 303.0, "voiced_cgroup_peak_mb": 9000.0}, "max", 14_035),        # unrecorded: the 4,096 cap
    ({"preflight_s": 303.0}, "junk", 14_035),
])
def test_the_worker_cost_is_capped_at_the_recorded_limit(timings, limit, micros):
    assert ds.run_worker_micros(_run("published", timings=timings, metadata=_limited(limit))) == micros


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


#: What a recovered `media_ready` run's final word says about its posts (MD-1): true however the owner acted.
RECOVERED_POSTS = "its posts go to the review chat like any day's; anything still undecided waits there"


@pytest.mark.parametrize("status", ["media_ready", "published"])
def test_a_run_that_finished_after_the_check_recovered(status):
    msg = _final_word(_run(status, finished_at=_iso_of(AFTER_22), updated_at=_iso_of(AFTER_22)))
    what = RECOVERED_POSTS if status == "media_ready" else "its posts went out"
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
                                                             f"22:04 ET — {RECOVERED_POSTS}.")


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


# Company Weekly on (post-commit check of eba8ddcc): a lesson is certain only on a day whose enabled chain is
# the lesson alone, and a news day that falls back to one brings the repeat forward — so the date is an upper
# bound, flagged `no_later_than`.

#: MARKETING_NEWS_SERIES's default, spelled out so a developer's .env can never move a pinned date: with
#: classes A,C,F it makes Mon / Tue / Thu news days and leaves Saturday the lesson alone.
_DEFAULT_NEWS_SERIES = "ceo_buys,insider_buys,thirteen_f,money_map"
_NEWS_CLASSES = frozenset({"A", "C", "F"})
_NEWS_SERIES = selection.parse_news_series(_DEFAULT_NEWS_SERIES)


def test_runway_with_a_news_class_on_counts_only_the_lesson_only_days():
    pool = ["a", "b", "c"]
    news = dict(classes=_NEWS_CLASSES, news_series=_NEWS_SERIES)
    mon = date(2026, 10, 5)
    # Sat 10-10, 10-17, 10-24 take a, b, c — Sat 10-31 repeats (lessons only it is Sat 10-10, above)
    out = ds.pool_runway(pool, [], today=mon, today_picked=False, **news)
    assert out == {"pool": 3, "unused": 3, "first_repeat": date(2026, 10, 31), "no_later_than": True}
    # Monday is a news day: the count starts tomorrow whatever `today_picked` says
    assert ds.pool_runway(pool, [], today=mon, today_picked=True, **news) == out
    # a Saturday whose lesson is still to come counts itself; once made, the count starts next Saturday
    sat = date(2026, 10, 10)
    assert ds.pool_runway(pool, [], today=sat, today_picked=False, **news)["first_repeat"] == date(2026, 10, 31)
    assert ds.pool_runway(pool, ["a"], today=sat, today_picked=True, **news) == {
        "pool": 3, "unused": 2, "first_repeat": date(2026, 10, 31), "no_later_than": True}
    # the walk lands only on lesson-only days, whatever the start
    for offset in range(21):
        start = mon + timedelta(days=offset)
        day = ds.pool_runway(pool, ["b"], today=start, today_picked=False, **news)["first_repeat"]
        assert selection.enabled_chain(selection.plan_for(day).chain, _NEWS_CLASSES,
                                       shipped=_NEWS_SERIES) == (selection.LESSON,), day


def test_runway_follows_the_per_series_switch_and_the_season_calendar():
    """Only `earnings` listed (class F): Mon and Tue chains hold no listed series (the lesson alone), and a
    Thursday is news only in earnings season — 10..56 days after a quarter end: Thu 10-08 is day 8 (off),
    Thu 10-15 day 15 (in)."""
    out = ds.pool_runway([str(i) for i in range(6)], [], today=date(2026, 10, 5), today_picked=False,
                         classes=frozenset({"A", "F"}), news_series=frozenset({"earnings"}))
    # Mon 10-05, Tue 10-06, Thu 10-08, Sat 10-10, Mon 10-12, Tue 10-13 take the six; Thu 10-15 is news
    assert out == {"pool": 6, "unused": 6, "first_repeat": date(2026, 10, 17), "no_later_than": True}
    # a news class on with every series switched off: every chain is the lesson, every posting day counts
    # (the lessons-only date) — still flagged, the classes are what says news days may exist
    lessons_only = ds.pool_runway(["a", "b", "c"], [], today=date(2026, 10, 5), today_picked=False)
    assert ds.pool_runway(["a", "b", "c"], [], today=date(2026, 10, 5), today_picked=False,
                          classes=_NEWS_CLASSES, news_series=frozenset()) == {**lessons_only, "no_later_than": True}


def test_runway_with_the_classes_at_a_is_exactly_the_lessons_only_count():
    """Classes None / {"A"} (MARKETING_CONTENT_CLASSES unset): every posting day, the three keys only, and the
    per-series switch is never consulted."""
    pool = [str(i) for i in range(9)]
    for offset in range(21):
        today = date(2026, 10, 1) + timedelta(days=offset)
        for picked in (False, True):
            for used_n in (0, 4, 9):
                base = ds.pool_runway(pool, pool[:used_n], today=today, today_picked=picked)
                assert set(base) == {"pool", "unused", "first_repeat"}
                for classes in (frozenset({"A"}), {"A"}, ("A",)):
                    for series in (None, frozenset(), _NEWS_SERIES):
                        assert ds.pool_runway(pool, pool[:used_n], today=today, today_picked=picked,
                                              classes=classes, news_series=series) == base


def test_runway_without_a_lesson_only_day_in_reach_has_no_date(monkeypatch):
    """A calendar whose every posting day has a news series (POST_WEEKDAYS or the plan changed): no lesson is
    certain on any day, so no date — the walk stays bounded and the line names no date."""
    news_day = selection.DayPlan(False, ("money_map", selection.LESSON), "test")
    monkeypatch.setattr(selection, "plan_for",
                        lambda d: news_day if selection.is_posting_day(d) else selection.DayPlan(True))
    out = ds.pool_runway(["a", "b"], [], today=FRI, today_picked=False, classes=_NEWS_CLASSES,
                         news_series=_NEWS_SERIES)
    assert out == {"pool": 2, "unused": 2, "first_repeat": None, "no_later_than": True}
    assert "Content pool: 2 of 2 lessons unused" in _digest([], runway=out).splitlines()


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
    # The cost line's reads default to "read, and empty" with the Free plan and the declared $1.00 alert
    # line — a READABLE zero line, so a regression in it can never hide behind "unreadable".
    base: Dict[str, Any] = dict(now=NOW, week_start=WEEK_START, week_end=WEEK_END, posts=posts, runs=[],
                                measured=[], link_hits=[], spend={}, budget_micros=2_000_000, escalated=[],
                                runway={"pool": 34, "unused": 30, "first_repeat": date(2026, 11, 26)},
                                metrics_job={"job": ds.JOB_METRICS, "enabled": True, "run_day": "2026-10-05",
                                             "last_run_at": "2026-10-05T10:01:00+00:00", "items_written": 4,
                                             "last_error": None},
                                metrics_enabled=True, store="prelaunch",
                                charge_rows=[], prev_runs=[], script_rows=[], upload_post_fee_micros=0,
                                cost_warn_micros=1_000_000)
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
    # a week whose cost reads all succeeded and found nothing is a READABLE zero (≈, nothing named)
    assert text.splitlines()[2:5] == [
        "💵 Cost last week ≈ $0.000: X $0.000 (our ledger) · Gemini ≈ $0.000 · worker ≈ $0.000 · "
        "Upload-Post $0.000 (Free plan · 0 uploads)",
        "Usage (X + Gemini + worker): last week ≈ $0.000 · week before ≈ $0.000",
        "",
    ]
    assert "Posts: none this week" in text
    # every posting day of the week without a run says so
    for d in ("Mon 09-28", "Tue 09-29", "Thu 10-01", "Sat 10-03"):
        assert f"• {d} no run (a posting day)" in text
    assert "Fri 10-02" not in text and "Sun 10-04" not in text
    assert "Scorecard, 4 weeks (09-07 → 10-04): no published posts" in text.splitlines()
    assert "Top post this week" not in text and "Picture not used" not in text
    assert "/go taps (approximate): none" in text.splitlines() and "Review time: no decisions this week" in text
    assert "first minutes after posting" not in text and "⚠️ Usage" not in text
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


def test_one_top_post_line_names_the_weeks_best_post_by_interactions_never_its_text():
    s = _summary(_full_week())
    # X's 7 likes + 2 reposts beat Bluesky's 7 interactions; the retracted post (99 likes) and the rehearsal
    # row (1,000 likes) are not this week's posts; the junk-count post has no readable count
    assert s["top_post"] == {"score": 9, "url": f"https://x.com/i/web/status/{X_ID}",
                             "counts": {"likes": 7, "reposts": 2, "replies": 0}}
    text = ds.compose_digest(s)
    assert f"Top post this week: https://x.com/i/web/status/{X_ID} (7 likes · 2 reposts · 0 replies)" in text.splitlines()
    assert text.count("Top post") == 1 and "1000" not in text and "99 like" not in text and "12 like" not in text
    _assert_no_model_text(text)


def test_a_tie_for_top_post_goes_to_the_first_and_no_interaction_means_no_line():
    first = _post("bluesky", "published", external_url="https://bsky.app/profile/d/post/first",
                  metrics={"status": "ok", "last": {"likes": 3, "reposts": 2}})
    second = _post("x", "published", external_id=X_ID, metrics={"status": "ok", "last": {"likes": 5}})
    assert _summary([first, second])["top_post"]["url"] == "https://bsky.app/profile/d/post/first"
    quiet = [_post("x", "published", external_id=X_ID, metrics={"status": "ok", "last": {"likes": 0, "impressions": 3}}),
             _post("<b>x</b>", "published", metrics={"status": "ok", "last": {"likes": 9}})]      # no platform
    assert _summary(quiet)["top_post"] is None and "Top post" not in _digest(quiet)


def test_metrics_off_says_so_instead_of_a_scorecard():
    text = _digest(_full_week(), metrics_enabled=False, measured=_scorecard_week())
    assert "Scorecard: not measured — MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on" in text
    assert "Metrics job: off (MARKETING_ENABLED and MARKETING_METRICS_ENABLED must both be on)" in text
    assert "• TIKTOK video:" not in text and "Scorecard, 4 weeks" not in text


# ── the 4-week scorecard (Batch 1 item 8b, 2026-10-15) ────────────────────────

SCORE_HEADER = ("Scorecard, 4 weeks (09-07 → 10-04), by follower gain — views, watch and % are medians over "
                "posts read at 24 h or older:")


def _snap(age_h: Any = 48.0, **counts: Any) -> Dict[str, Any]:
    """A stored latest snapshot (`metrics.last`); a `video=` object rides in `counts`."""
    return {"day": "2026-10-04", "at": "2026-10-04T10:00:00+00:00", "age_h": age_h, "cp": None, **counts}


def _acct(followers: Any, at: str) -> Dict[str, Any]:
    return {"followers": followers, "at": at, "source": "test"}


def _scored(platform: str, fmt: str, published: str, *, last: Any = None, status: Any = "ok", account: Any = None,
            post_status: str = "published", **kw: Any) -> Dict[str, Any]:
    """A post as the digest's 35-day read returns it, published at `published` (UTC ISO)."""
    metrics: Dict[str, Any] = {"v": 1}
    if status is not None:
        metrics["status"] = status
    if last is not None:
        metrics["last"] = last
    if account is not None:
        metrics["account"] = account
    return _post(platform, post_status, run_date=published[:10], created=published, metrics=metrics,
                 format=fmt, published_at=published, **kw)


def _scorecard_week() -> List[Dict[str, Any]]:
    """Four platforms over the 4 weeks Mon 09-07 → Sun 10-04 — every row shape the scorecard draws."""
    return [
        _scored("tiktok", "video", "2026-09-08T20:30:00+00:00", account=_acct(10, "2026-09-09T10:00:00+00:00"),
                last=_snap(72.0, views=40, likes=1, video={"new_followers": 2, "profile_views": 9,
                                                           "average_time_watched": 6.0,
                                                           "full_video_watched_rate": 0.3})),
        _scored("tiktok", "video", "2026-09-22T20:30:00+00:00", account=_acct(20, "2026-09-23T10:00:00+00:00"),
                last=_snap(72.0, views=80, video={"new_followers": 3, "profile_views": 12,
                                                  "average_time_watched": 7.0, "full_video_watched_rate": 0.4})),
        _scored("tiktok", "video", "2026-10-03T20:30:00+00:00", account=_acct(30, "2026-10-05T10:00:00+00:00"),
                last=_snap(34.0, views=60, video={"new_followers": 0, "profile_views": 1,
                                                  "average_time_watched": 8.0, "full_video_watched_rate": 0.2,
                                                  "retention": {"1": 0.9}})),
        _scored("youtube", "video", "2026-09-15T20:30:00+00:00", last=_snap(48.0, views=31)),
        _scored("youtube", "video", "2026-09-29T20:30:00+00:00", last=_snap(48.0, views=109)),
        _scored("youtube", "video", "2026-10-01T20:30:00+00:00", status="error"),              # never read ok
        # Sat 09-05 is before the 4 weeks: not a scorecard post — but its follower snapshot is X's baseline
        _scored("x", "text", "2026-09-05T20:30:00+00:00", last=_snap(72.0, impressions=999),
                account=_acct(12, "2026-09-07T12:00:00+00:00")),
        _scored("x", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=4)),
        _scored("x", "text", "2026-10-01T20:30:00+00:00", last=_snap(72.0, impressions=21),
                account=_acct(12, "2026-10-05T10:00:00+00:00")),
        _scored("bluesky", "image", "2026-09-26T20:30:00+00:00", last=_snap(48.0, likes=2),
                account=_acct(5, "2026-09-28T10:00:00+00:00")),
        _scored("bluesky", "image", "2026-10-03T20:30:00+00:00", last=_snap(34.0, likes=1),
                account=_acct(7, "2026-10-05T10:00:00+00:00")),
    ]


def _score_lines(text: str) -> List[str]:
    lines = text.splitlines()
    i = lines.index(SCORE_HEADER)
    return lines[i + 1: next(j for j in range(i + 1, len(lines)) if not lines[j].startswith("• "))]


def test_the_scorecard_layout_is_pinned():
    s = _summary([], measured=_scorecard_week())
    text = ds.compose_digest(s)
    assert _score_lines(text) == [
        # +20 against 09-09, the snapshot closest to 4 weeks before the newest (10-05); 5 follows and 22
        # profile visits from the three posts; medians 60 views, 7 s, 30% (0.3 of a fraction)
        "• TIKTOK video: 3 posts · followers 30 (+20 since 09-09) · follows from posts 5 · profile visits 22 · "
        "views median 60, best 80 (3 at 24 h+) · avg watch 7 s · watched to the end 30%",
        "• BLUESKY image: 2 posts · followers 7 (+2 since 09-28) · no view counts",
        "• X text: 2 posts · followers 12 (±0 since 09-07) · impressions median 12.5, best 21 (2 at 24 h+)",
        # no follower snapshot and no watch fields on the live route: n/a, never 0 — sorted last
        "• YOUTUBE video: 3 posts · followers n/a · views median 70, best 109 (2 at 24 h+) · avg watch n/a · "
        "avg viewed n/a · reads: 1 error",
    ]
    lines = text.splitlines()
    i = lines.index(SCORE_HEADER)
    # the scorecard sits where the engagement block was: after the posts section, before the Followers line
    assert lines[i - 1] == "" and lines[i + 5].startswith("Followers: ")
    assert "Followers: BLUESKY 7 (+2 since 09-28; as of 10-05) · TIKTOK 30 (+10 since 09-23; as of 10-05) · " \
           "X 12 (±0 since 09-07; as of 10-05)" in lines
    (tiktok,) = [r for r in s["scorecard"]["rows"] if r["platform"] == "tiktok"]
    assert (tiktok["gain"], tiktok["sums"], tiktok["watch_s"], tiktok["reach_n"]) == (
        20, {"new_followers": 5, "profile_views": 22}, 7.0, 3)
    assert (s["scorecard"]["first"], s["scorecard"]["last"]) == (date(2026, 9, 7), date(2026, 10, 4))
    assert "999" not in text                      # the post before the 4 weeks is no scorecard post


def test_the_scorecard_splits_each_platform_by_format_and_shares_its_followers():
    measured = [
        _scored("facebook", "text", "2026-09-08T20:30:00+00:00", last=_snap(48.0, views=5),
                account=_acct(90, "2026-09-09T10:00:00+00:00")),
        _scored("facebook", "image", "2026-09-29T20:30:00+00:00", last=_snap(48.0, views=50)),
        _scored("facebook", "image", "2026-10-01T20:30:00+00:00", last=_snap(48.0, views=70),
                account=_acct(100, "2026-10-05T10:00:00+00:00")),
        _scored("linkedin", "image", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=30)),
        _scored("linkedin", "text", "2026-09-15T20:30:00+00:00", last=_snap(48.0, views=3)),
        _scored("linkedin", "text", "2026-09-16T20:30:00+00:00", last=_snap(48.0, impressions=9)),
    ]
    assert _score_lines(_digest([], measured=measured)) == [
        # one platform, two formats: the same followers on both rows; within it, by median reach
        "• FACEBOOK image: 2 posts · followers 100 (+10 since 09-09) · views median 60, best 70 (2 at 24 h+)",
        "• FACEBOOK text: 1 post · followers 100 (+10 since 09-09) · views median 5, best 5 (1 at 24 h+)",
        "• LINKEDIN image: 1 post · followers n/a · impressions median 30, best 30 (1 at 24 h+)",
        # each post's own count, named as the mix it is
        "• LINKEDIN text: 2 posts · followers n/a · views/impressions median 6, best 9 (2 at 24 h+)",
    ]


_NEWEST = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("snapshots, base, base_days", [
    # (days before the newest snapshot, followers); the newest is (0, 50)
    ([(0, 50), (28 - 1 / 24, 30), (28 + 2 / 24, 31), (20, 40), (5, 45)], 30, 28 - 1 / 24),   # closest to 4 weeks
    ([(0, 50), (28 - 1 / 24, 30), (28 + 1 / 24, 31)], 31, 28 + 1 / 24),                      # a tie: the older one
    ([(0, 50), (7, 44), (14, 40), (21, 35), (28 - 10 / 86400, 30)], 30, 28 - 10 / 86400),     # X weekly, 10 s late
    ([(0, 50), (33, 20)], 20, 33),                                       # only an older one: dated by its own day
    ([(0, 50), (10, 45)], 45, 10),                                       # a young account: its 10 days, dated
    ([(0, 50), (6, 46)], 46, 6),                                         # exactly 6 days older counts
    ([(0, 50), (6 - 1 / 86400, 46), (1, 49)], None, None),               # nothing 6+ days older: no change
    ([(0, 50)], None, None),
])
def test_the_scorecards_follower_change_is_against_the_snapshot_closest_to_four_weeks_earlier(snapshots, base,
                                                                                           base_days):
    posts = [_post("tiktok", "published", metrics={"v": 1, "account": _acct(n, (_NEWEST - timedelta(days=d)).isoformat())})
             for d, n in snapshots]
    f = ds._followers(posts, now=NOW)["tiktok"]
    assert (f["followers"], f["at"]) == (50, _NEWEST)
    assert f["four_week_base"] == base
    assert f["four_week_base_at"] == (None if base_days is None else _NEWEST - timedelta(days=base_days))
    # the Followers line keeps its own rule: the LATEST snapshot at least 6 days older
    older = [(d, n) for d, n in snapshots if d >= 6]
    assert f["baseline"] == (min(older)[1] if older else None)


def test_a_follower_count_without_a_baseline_or_a_fresh_read_says_so():
    measured = [_scored("x", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=4),
                        account=_acct(12, "2026-10-01T10:00:00+00:00"))]
    (line,) = _score_lines(_digest([], measured=measured))
    assert line == ("• X text: 1 post · followers 12 (change n/a, as of 10-01) · impressions median 4, best 4 "
                    "(1 at 24 h+)")
    # a loss is signed; a read under two days old carries no date
    measured = [_scored("x", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=4),
                        account=_acct(12, "2026-10-04T10:00:00+00:00")),
                _scored("x", "text", "2026-09-08T20:30:00+00:00", account=_acct(15, "2026-09-08T22:00:00+00:00"))]
    assert "followers 12 (-3 since 09-08) ·" in _score_lines(_digest([], measured=measured))[0]


def test_a_figure_no_post_gave_is_n_a_and_a_reported_zero_is_zero():
    measured = [
        _scored("tiktok", "video", "2026-10-04T20:30:00+00:00", last=_snap(3.0, views=500)),   # read at 3 h
        _scored("youtube", "video", "2026-10-02T20:30:00+00:00", status=None),                  # never read
    ]
    assert _score_lines(_digest([], measured=measured)) == [
        "• TIKTOK video: 1 post · followers n/a · follows from posts n/a · profile visits n/a · views n/a · "
        "avg watch n/a · watched to the end n/a",
        "• YOUTUBE video: 1 post · followers n/a · views n/a · avg watch n/a · avg viewed n/a · reads: 1 unread",
    ]
    zeros = [_scored("tiktok", "video", "2026-10-01T20:30:00+00:00", account=_acct(0, "2026-10-05T10:00:00+00:00"),
                     last=_snap(48.0, views=0, video={"new_followers": 0, "average_time_watched": 0.0,
                                                      "full_video_watched_rate": 0.0}))]
    assert _score_lines(_digest([], measured=zeros)) == [
        "• TIKTOK video: 1 post · followers 0 (change n/a) · follows from posts 0 · profile visits n/a · "
        "views median 0, best 0 (1 at 24 h+) · avg watch 0 s · watched to the end 0%"]


def test_a_sum_only_some_posts_gave_is_a_floor_never_the_total():
    """Three TikTok posts; the follows of only two are known (one never read, one read without the field —
    or with junk in it): the sum is a FLOOR, "≥ 5". Passing it off as the row's total would understate it."""
    measured = [
        _scored("tiktok", "video", "2026-09-29T20:30:00+00:00",
                last=_snap(48.0, views=10, video={"new_followers": 2, "profile_views": 4})),
        _scored("tiktok", "video", "2026-09-30T20:30:00+00:00",
                last=_snap(48.0, views=20, video={"new_followers": 3, "profile_views": 6})),
        _scored("tiktok", "video", "2026-10-01T20:30:00+00:00", status=None),               # never read
    ]
    (line,) = _score_lines(_digest([], measured=measured))
    assert line.startswith("• TIKTOK video: 3 posts · followers n/a · follows from posts ≥ 5 · profile visits ≥ 10 · ")
    (row,) = _summary([], measured=measured)["scorecard"]["rows"]
    assert (row["sums"], row["sum_n"]) == ({"new_followers": 5, "profile_views": 10},
                                           {"new_followers": 2, "profile_views": 2})
    # the third post read, but its follows unreadable (junk) and its profile visits known: one floor, one total
    measured[2] = _scored("tiktok", "video", "2026-10-01T20:30:00+00:00",
                          last=_snap(48.0, views=30, video={"new_followers": True, "profile_views": 1}))
    (line,) = _score_lines(_digest([], measured=measured))
    assert "follows from posts ≥ 5 · profile visits 11 · " in line
    # every post gave it: the plain total (a reported 0 counts as given)
    measured[2] = _scored("tiktok", "video", "2026-10-01T20:30:00+00:00",
                          last=_snap(48.0, views=30, video={"new_followers": 0, "profile_views": 1}))
    (line,) = _score_lines(_digest([], measured=measured))
    assert "follows from posts 5 · profile visits 11 · " in line and "≥" not in line
    # a hand-edited row (no `sum_n`) never claims a total
    assert ds._sum_text({"posts": 2, "sums": {"new_followers": 4}}, "new_followers") == "≥ 4"
    assert ds._sum_text({"posts": 2, "sums": {"new_followers": 4}, "sum_n": {"new_followers": "2"}},
                        "new_followers") == "≥ 4"
    assert ds._sum_text({"posts": 2, "sums": {"new_followers": "4"}, "sum_n": {"new_followers": 2}},
                        "new_followers") == "n/a"


@pytest.mark.parametrize("age", [23.9, None, True, "48", math.nan, math.inf, -5])
def test_only_a_snapshot_read_at_24_h_or_older_counts_toward_reach_and_watch(age):
    measured = [
        _scored("tiktok", "video", "2026-10-03T20:30:00+00:00",
                last=_snap(age, views=1000, video={"new_followers": 4, "average_time_watched": 30.0,
                                                   "full_video_watched_rate": 0.9})),
        _scored("tiktok", "video", "2026-09-29T20:30:00+00:00",
                last=_snap(48.0, views=10, video={"new_followers": 1, "average_time_watched": 5.0,
                                                  "full_video_watched_rate": 0.1})),
    ]
    (line,) = _score_lines(_digest([], measured=measured))
    # the young post's follows count (a follow is a follow); its reach and watch do not
    assert line == ("• TIKTOK video: 2 posts · followers n/a · follows from posts 5 · profile visits n/a · "
                    "views median 10, best 10 (1 at 24 h+) · avg watch 5 s · watched to the end 10%")


def test_the_scorecard_counts_only_live_published_posts_of_its_four_new_york_weeks():
    edges = {
        "2026-09-07T03:59:59+00:00": False,   # Sun 09-06 23:59:59 EDT
        "2026-09-07T04:00:00+00:00": True,    # Mon 09-07 00:00 EDT
        "2026-10-05T03:59:59+00:00": True,    # Sun 10-04 23:59:59 EDT
        "2026-10-05T04:00:00+00:00": False,   # Mon 10-05 00:00 EDT — this week
    }
    measured = [_scored("x", "text", at, last=_snap(48.0, impressions=1)) for at in edges]
    junk_time = _scored("x", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=1))
    junk_time["published_at"] = "yesterday"
    no_time = _scored("x", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=1))
    no_time["published_at"] = None
    measured += [junk_time, no_time, "junk", None,
                 _scored("x", "text", "2026-09-29T20:30:00+00:00", live=False, last=_snap(48.0, impressions=1)),
                 _scored("x", "text", "2026-09-29T20:30:00+00:00", post_status="retracted"),
                 _scored("<b>x</b>", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, impressions=1))]
    (row,) = _summary([], measured=measured)["scorecard"]["rows"]
    assert (row["platform"], row["posts"], row["reach_n"]) == ("x", sum(edges.values()), 2)
    # the November week: the window follows the New York clock (the extra hour is in)
    nov = [_scored("x", "text", "2026-11-02T04:59:59+00:00", last=_snap(48.0, impressions=1)),   # Sun 23:59:59 EST
           _scored("x", "text", "2026-11-02T05:00:00+00:00", last=_snap(48.0, impressions=1)),   # Mon 00:00 EST
           _scored("x", "text", "2026-10-05T03:59:59+00:00", last=_snap(48.0, impressions=1)),   # Sun 10-04 EDT
           _scored("x", "text", "2026-10-05T04:00:00+00:00", last=_snap(48.0, impressions=1))]   # Mon 10-05 EDT
    s = ds.summarize_week(now=datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc), week_start=date(2026, 10, 26),
                          week_end=date(2026, 11, 1), posts=[], measured=nov, metrics_enabled=True)
    assert s["scorecard"]["first"] == date(2026, 10, 5) and s["scorecard"]["rows"][0]["posts"] == 2


def test_junk_stored_measures_are_ignored_never_shown():
    measured = [
        _scored("tiktok", "video", "2026-09-29T20:30:00+00:00", last=_snap(48.0, views="12", video={
            "new_followers": -1, "profile_views": 1.5, "average_time_watched": True,
            "full_video_watched_rate": 18.4})),
        _scored("tiktok", "video", "2026-09-30T20:30:00+00:00", last=_snap(48.0, views=True, video=[1, 2])),
        _scored("youtube", "video", "2026-09-29T20:30:00+00:00", last=_snap(48.0, views=-1, video={
            "average_view_duration_seconds": "12", "average_view_percentage": math.nan})),
        _scored("youtube", "video", "2026-09-30T20:30:00+00:00", status=["ok"], last="not a snapshot"),
    ]
    text = _digest([], measured=measured)
    assert _score_lines(text) == [
        "• TIKTOK video: 2 posts · followers n/a · follows from posts n/a · profile visits n/a · views n/a · "
        "avg watch n/a · watched to the end n/a",
        "• YOUTUBE video: 2 posts · followers n/a · views n/a · avg watch n/a · avg viewed n/a · reads: 1 other",
    ]
    assert "1,840" not in text and "18.4" not in text


#: The ten rows of a busy 4 weeks (every platform, the picture platforms in two formats).
_TEN_ROWS = (("tiktok", "video"), ("youtube", "video"), ("instagram", "video"), ("facebook", "image"),
             ("facebook", "text"), ("linkedin", "image"), ("linkedin", "text"), ("threads", "image"),
             ("x", "text"), ("bluesky", "image"))


def _busy_scorecard(posts_per_row: int = 28, followers: int = 99_999, views: int = 999_999,
                    per_post: int = 9_999, failing: tuple = ("error",)) -> List[Dict[str, Any]]:
    """Every field of every row present and wide — a follower change on each platform, every video measure,
    a failed read on every row (`failing`: one post per status; None = never read, so no snapshot at all and
    TikTok's sums become floors, "≥") — at magnitudes far past today's (followers in single digits, views
    under 200 a post): `followers` per platform, `views` a post, `per_post` follows and visits a post."""
    out: List[Dict[str, Any]] = []
    first = datetime(2026, 9, 7, 20, 30, tzinfo=timezone.utc)
    for platform, fmt in _TEN_ROWS:
        for i in range(posts_per_row):
            published = (first + timedelta(days=i % 28, minutes=i)).isoformat()
            status = failing[i] if i < len(failing) else "ok"
            # The widest watch time the bound admits (MD-4: twice MARKETING_MAX_VIDEO_SECONDS, 150 s by default).
            video = {"new_followers": per_post, "profile_views": per_post, "average_time_watched": 149.9,
                     "full_video_watched_rate": 0.9999, "average_view_duration_seconds": 149.9,
                     "average_view_percentage": 999.9}
            account = None
            if i == 0:
                account = _acct(followers // 2, "2026-09-08T10:00:00+00:00")
            elif i == posts_per_row - 1:
                account = _acct(followers, "2026-10-05T10:00:00+00:00")
            last = None if status is None else _snap(48.0 + i, views=views - i, impressions=views - 2 * i, likes=i,
                                                     video=video)
            out.append(_scored(platform, fmt, published, status=status, account=account, last=last))
    return out


def test_a_busy_week_with_ten_scorecard_rows_fits_one_message_with_every_row_whole():
    """The plan's worst case: ten (platform, format) rows with every field at a wide value, in a week busy
    everywhere else too (every post status, rejections and failures, two fallback days, a fallback trail
    on three days, every /go campaign) — it fits ONE message, every scorecard row whole, nothing cut. When
    a week is busier still, the other lists shrink first (`_PASSES`)."""
    trail = [{"series": "ceo_buys", "outcome": "no_candidates"}, {"series": "insider_buys", "outcome": "unavailable"},
             {"series": "money_map", "outcome": "chosen"}]
    runs = [_run("published", run_date=d, metadata={"series": "money_map", "series_trail": trail})
            for d in ("2026-09-28", "2026-09-29", "2026-10-01")] + [_run("published", run_date="2026-10-03")]
    fallbacks = [_post(p, "published", run_date=d, meta={"image_fallback": code})
                 for d, code in (("2026-10-01", "image_dropped"), ("2026-10-03", "worker_capability"))
                 for p in ("facebook", "linkedin", "threads", "bluesky")]
    # one post per row whose read failed and one never read (so TikTok's sums are floors, "≥") — the widest rows
    s = _summary(_full_week() + fallbacks, runs=runs, measured=_busy_scorecard(failing=("error", None)),
                 link_hits=[{"campaign": c, "day": "2026-10-01", "hits": 9_999} for c in
                            ("x", "bluesky", "tiktok", "youtube", "instagram", "threads", "facebook", "linkedin")],
                 spend={"x_create": 450_000, "x_metrics_read": 60_000, "x_account_read": 10_000},
                 escalated=[_post("x", "queued", meta={"escalated_at": "2026-10-03T20:40:00+00:00"})])
    text = ds.compose_digest(s)
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS and ds._CUT_MARKER not in text
    rows = _score_lines(text)
    assert len(rows) == 10 and all("… and" not in r for r in rows)
    assert rows == [ds._score_line(r, s["now"]) for r in s["scorecard"]["rows"]]
    tiktok = next(r for r in rows if r.startswith("• TIKTOK"))
    # 27 of the 28 posts were read: their 27 × 9,999 follows are a floor; the median is over those 27
    assert tiktok == ("• TIKTOK video: 28 posts · followers 99,999 (+50,000 since 09-08) · follows from posts "
                      "≥ 269,973 · profile visits ≥ 269,973 · views median 999,985, best 999,999 (27 at 24 h+) · "
                      "avg watch 149.9 s · watched to the end 100% · reads: 1 unread, 1 error")
    assert "Failed 2:" in text.splitlines() and "Rejected 7: " in text     # the other sections keep their counts
    text.encode("utf-16-le")
    _assert_no_model_text(text)


@pytest.mark.parametrize("followers, views, per_post", [(99_999, 999_999, 9_999), (9_999_999, 99_999_999, 9_999_999),
                                                        (10 ** 15 - 1, 10 ** 15 - 1, 10 ** 13)])
def test_a_scorecard_beyond_any_real_week_is_trimmed_last_and_still_fits(followers, views, per_post):
    """Every row at absurd widths, every not-ok status on every row, in that busy week: the other lists
    shrink to their counts first, then the scorecard ("… and N more"); it is still ONE message, never cut
    mid-line."""
    s = _summary(_full_week(), measured=_busy_scorecard(followers=followers, views=views, per_post=per_post,
                                                        failing=("error", "unavailable", "missing", None)))
    text = ds.compose_digest(s)
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    lines = text.splitlines()
    i = lines.index(SCORE_HEADER)
    shown = [ln for ln in lines[i + 1:] if ln.startswith("• ")]
    assert shown and all(ln in [ds._score_line(r, s["now"]) for r in s["scorecard"]["rows"]] for ln in shown)
    if len(shown) < 10:
        assert lines[i + 1 + len(shown)] == f"… and {10 - len(shown)} more"
    text.encode("utf-16-le")


def test_an_absurd_scorecard_still_fits_and_never_cuts_a_line():
    """Sixty rows (a corrupt ledger of junk platforms): the scorecard is trimmed last and the message still
    fits, ending on a whole line."""
    measured = [_scored(f"p{i:03d}", "video", "2026-09-29T20:30:00+00:00",
                        last=_snap(48.0, views=10 ** 14 + i), account=_acct(10 ** 14, "2026-10-05T10:00:00+00:00"))
                for i in range(60)]
    s = _summary(_full_week(), measured=measured)
    text = ds.compose_digest(s)
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    assert SCORE_HEADER in text.splitlines()
    text.encode("utf-16-le")


def test_an_unreadable_or_capped_read_says_so_never_an_empty_scorecard():
    assert "Scorecard: unreadable (see the web logs)" in _digest([], measured=None).splitlines()
    assert "Followers: unreadable (see the web logs)" in _digest([], measured=None).splitlines()
    week = _scorecard_week()
    capped = _digest([], measured=week, measured_capped=True)
    # the count the read took in — never the page bound, which a read stuck on one instant never reaches
    assert (f"  (the published-posts read stopped after {len(week):,} posts — the newest may be missing)"
            in capped.splitlines())
    assert "read stopped after" not in _digest([], measured=_scorecard_week())
    # a hand-built report without the count still says it was capped, never a made-up number
    s = _summary([], measured=_scorecard_week(), measured_capped=True)
    for junk in (None, -1, True, "12", 3.0):
        s["scorecard"]["read"] = junk
        assert ("  (the published-posts read stopped after ? posts — the newest may be missing)"
                in ds.compose_digest(s).splitlines()), junk
    # paged (MD-7): 4 pages of 300, never one capped read of 300 that lost the newest week
    assert (ds.MEASURED_POST_LIMIT, ds.MEASURED_POST_PAGES, ds.MEASURED_POST_MAX) == (300, 4, 1_200)
    assert ds.FOLLOWER_LOOKBACK == timedelta(days=35)


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
    assert s["taps"]["total"] == 9 and s["taps"]["unreadable_rows"] == 4 and s["taps"]["early"] == 0
    text = ds.compose_digest(s)
    # the whole line: no early clause when there were no early taps
    assert "/go taps (approximate): (unreadable) 5 · tiktok 3 · bluesky 1 — 9 in total" in text.splitlines()
    assert "(4 unreadable row(s) not counted)" in text
    # the app is live since 2026-10-05: no store URL is a misconfiguration, warned like a broken one
    assert ("⚠️ MARKETING_APP_STORE_URL is unset — every /go tap lands on the landing page, which has no App "
            "Store link.") in text.splitlines()
    for store in ("live", "invalid", "prelaunch"):
        assert "Coming soon" not in _digest([], link_hits=hits, store=store)
    assert "MARKETING_APP_STORE_URL" not in _digest([], link_hits=hits, store="live")
    assert ("⚠️ MARKETING_APP_STORE_URL is set but invalid — /go falls back to the landing page, which has no "
            "App Store link.") in _digest([], link_hits=hits, store="invalid").splitlines()
    # a failed read keeps its own plain label — "approximate" describes a count, not a missing one
    assert "/go taps: unreadable (see the web logs)" in _digest([], link_hits=None).splitlines()


_EARLY_CLAUSE = " in the first minutes after posting (mostly link scanners)"


def test_go_taps_count_the_first_minutes_after_posting_apart():
    """The server-constant `<campaign>_early` rows (smart_link.EARLY_KEYS: taps inside a campaign's early
    window, mostly link scanners) are summed APART: never a campaign row of their own, never in the total."""
    hits = [{"campaign": "bluesky", "day": "2026-10-03", "hits": 3},
            {"campaign": "bluesky_early", "day": "2026-10-03", "hits": 7},
            {"campaign": "facebook_early", "day": "2026-10-04", "hits": 5},
            {"campaign": "tiktok", "day": "2026-10-01", "hits": 2}]
    s = _summary([], link_hits=hits)
    assert s["taps"] == {"rows": [("bluesky", 3), ("tiktok", 2)], "total": 5, "early": 12, "unreadable_rows": 0}
    text = ds.compose_digest(s)
    assert ("/go taps (approximate): bluesky 3 · tiktok 2 — 5 in total — plus 12" + _EARLY_CLAUSE
            in text.splitlines())
    assert "_early" not in text


@pytest.mark.parametrize("platform", ["tiktok", "youtube", "instagram", "facebook", "linkedin", "threads",
                                      "bluesky", "x", "pinterest", "mastodon", "podcast", "blog", "hashnode",
                                      "devto"])
def test_every_post_platforms_early_key_is_early(platform):
    """One early key per post platform (the /go campaigns are exactly the post platforms) — listed as
    literals here, so a renamed or dropped key is a failure, not a silently ordinary row."""
    s = _summary([], link_hits=[{"campaign": f"{platform}_early", "day": "2026-10-03", "hits": 2},
                                {"campaign": platform, "day": "2026-10-03", "hits": 1}])
    assert s["taps"] == {"rows": [(platform, 1)], "total": 1, "early": 2, "unreadable_rows": 0}


def test_only_the_servers_early_keys_are_counted_as_early():
    """Only `<post platform>_early` is a server key. A stray "foo_early" or "other_early" (bare /go and junk
    slugs count under `other`, which has no early twin) is an ordinary campaign row; a mis-cased or padded
    one is an unreadable NAME (still counted); an early row whose count cannot be read is an unreadable
    ROW — never added to the early number."""
    hits = [{"campaign": "foo_early", "day": "2026-10-01", "hits": 2},
            {"campaign": "other_early", "day": "2026-10-01", "hits": 1},
            {"campaign": "_early", "day": "2026-10-01", "hits": 1},
            {"campaign": "X_EARLY", "day": "2026-10-01", "hits": 9},
            {"campaign": "bluesky_early ", "day": "2026-10-01", "hits": 4},
            {"campaign": "x_early", "day": "2026-10-01", "hits": "4"},
            {"campaign": "bluesky_early", "day": "2026-10-01", "hits": True},
            {"campaign": "youtube_early", "day": "2026-10-01", "hits": -1},
            {"campaign": "x_early", "day": "2026-10-02", "hits": 3}]
    s = _summary([], link_hits=hits)
    assert s["taps"] == {"rows": [("(unreadable)", 13), ("foo_early", 2), ("_early", 1), ("other_early", 1)],
                         "total": 17, "early": 3, "unreadable_rows": 3}
    text = ds.compose_digest(s)
    assert ("/go taps (approximate): (unreadable) 13 · foo_early 2 · _early 1 · other_early 1 — 17 in total — "
            "plus 3" + _EARLY_CLAUSE in text.splitlines())
    assert "  (3 unreadable row(s) not counted)" in text.splitlines()


def test_a_week_of_only_early_taps_says_none_plus_the_early_count():
    hits = [{"campaign": "bluesky_early", "day": "2026-10-03", "hits": 3},
            {"campaign": "x_early", "day": "2026-10-03", "hits": 1}]
    text = _digest([], link_hits=hits)            # _digest also asserts the 4,096-unit cap
    assert "/go taps (approximate): none — plus 4" + _EARLY_CLAUSE in text.splitlines()
    assert " in total" not in text
    big = _digest([], link_hits=[{"campaign": "linkedin_early", "day": "2026-10-03", "hits": 12_345}])
    assert "/go taps (approximate): none — plus 12,345" + _EARLY_CLAUSE in big.splitlines()


def test_the_early_count_survives_the_row_cap_after_the_total():
    """300 campaign rows are capped to the first eight; the early clause still closes the line, AFTER the
    "… N more" tail and the total it is never part of. By hand: hits 1..300 → 45,150 in total; early 5 + 7."""
    hits = [{"campaign": f"c{i:03d}", "day": "2026-09-30", "hits": i + 1} for i in range(300)]
    hits += [{"campaign": "x_early", "day": "2026-10-03", "hits": 5},
             {"campaign": "tiktok_early", "day": "2026-10-01", "hits": 7}]
    s = _summary([], link_hits=hits)
    assert (len(s["taps"]["rows"]), s["taps"]["total"], s["taps"]["early"]) == (300, 45_150, 12)
    text = ds.compose_digest(s)
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    assert ("/go taps (approximate): c299 300 · c298 299 · c297 298 · c296 297 · c295 296 · c294 295 · c293 294 · "
            "c292 293 · … 292 more — 45,150 in total — plus 12" + _EARLY_CLAUSE) in text.splitlines()


def test_the_store_state_follows_the_setting(monkeypatch):
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "")
    assert ds._store_state() == "prelaunch"
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "https://apps.apple.com/app/id6759525689")
    assert ds._store_state() == "live"
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "http://evil.example/app")
    assert ds._store_state() == "invalid"


@pytest.mark.parametrize("preorder", [True, False])
def test_the_pre_order_flag_never_changes_where_the_digest_says_go_lands(monkeypatch, preorder):
    """MARKETING_APP_STORE_PREORDER changes WORDS (new captions, the landing button), never where /go lands
    (smart_link.store_state): with no valid URL every tap still lands on the landing page and the digest
    warns so; with a valid URL (production since the 2026-10-05 launch) it never does, whatever the flag."""
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_PREORDER", preorder)
    hits = [{"campaign": "bluesky", "day": "2026-10-03", "hits": 1}]
    note = ("⚠️ MARKETING_APP_STORE_URL is unset — every /go tap lands on the landing page, which has no App "
            "Store link.")
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "")
    assert ds._store_state() == "prelaunch"
    assert note in _digest([], link_hits=hits, store=ds._store_state()).splitlines()
    monkeypatch.setattr(ds.settings, "MARKETING_APP_STORE_URL", "https://apps.apple.com/app/id6759525689")
    assert ds._store_state() not in ("prelaunch", "invalid")
    assert "MARKETING_APP_STORE_URL" not in _digest([], link_hits=hits, store=ds._store_state())
    # the flag left on after the release words new captions "pre-order": the digest says so (2026-10-08)
    warning = ('⚠️ MARKETING_APP_STORE_PREORDER is on — new captions say "pre-order on the App Store" and the '
               'landing button "Pre-order"; the app is on sale, so turn it off.')
    assert ds._store_state() == ("preorder" if preorder else "live")
    assert (warning in _digest([], link_hits=hits, store=ds._store_state()).splitlines()) is preorder
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


@pytest.mark.parametrize("budget, configured", [(None, False), (0, False), (0.0, False), (-1, False), (2.0, True),
                                                # finite, but its micros overflow to inf: X off, never a raise
                                                (1e303, False)])
def test_x_is_off_exactly_when_the_outlet_says_it_is_unconfigured(monkeypatch, budget, configured):
    """The digest's OFF line keys on `budget_micros() == 0`; pin that it is the very condition under which
    the X outlet is not configured (credentials present), so the line can never call a live X off."""
    from app.services.marketing import outlets

    monkeypatch.setattr(outlet_x.x_api, "configured", lambda: True)
    monkeypatch.setattr(settings, "MARKETING_X_MONTHLY_BUDGET_USD", budget)
    assert (outlet_x.budget_micros() > 0) is configured
    assert outlets.adapter_for("x").configured() is configured


def test_the_x_spend_line_never_raises_on_an_absurd_amount(caplog):
    """A hand-edited 400-digit journal amount used to raise OverflowError in the % (and in `_usd`) and take
    the WHOLE digest down for all three attempts. Now only the X spend line says so; every other section
    renders. The bound is ±$1,000,000 for an op and for the month's total."""
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    text = _digest([], spend={"x_create": 10 ** 400})
    assert "X spend: unreadable (see the web logs)" in text.splitlines()
    assert "Posts: none this week" in text and text.splitlines()[2].startswith("💵 Cost last week ≈ ")
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "X spend" in r.getMessage()]
    assert warned and "x_create" in warned[0] and str(10 ** 400)[:30] not in warned[0]
    # the bound, on an op and on the total (a negative one too)
    for spend in ({"x_create": 10 ** 12 + 1}, {"refund": -(10 ** 12 + 1)}, {"a_op": 6 * 10 ** 11, "b_op": 6 * 10 ** 11}):
        assert _summary([], spend=spend)["spend"] is None, spend
    at_bound = _summary([], spend={"x_create": 10 ** 12, "refund": -1})["spend"]
    assert at_bound["total"] == 10 ** 12 - 1 and at_bound["by_op"] == [("x_create", 10 ** 12), ("refund", -1)]
    # an absurd CAP (the setting is not bounded at boot) gets its own head — no float overflow in the %
    absurd = _digest([], spend={"x_create": 44_000}, budget_micros=10 ** 13)
    assert ("X spend 2026-10 (UTC, to date): $0.044 — the cap MARKETING_X_MONTHLY_BUDGET_USD is over "
            "$1,000,000 (check it)") in absurd.splitlines()
    assert ("X spend 2026-10 (UTC, to date): $0.044 of the $1000000.00 cap (0%)"
            in _digest([], spend={"x_create": 44_000}, budget_micros=10 ** 12).splitlines())
    # `_usd` is total: anything but an int within ±$1,000,000,000 reads "unreadable"
    assert ds._usd(10 ** 400) == "unreadable" and ds._usd(-(10 ** 400)) == "unreadable"
    assert ds._usd(10 ** 15) == "$1000000000.000" and ds._usd(10 ** 15 + 1) == "unreadable"
    assert ds._usd(-(10 ** 15)) == "-$1000000000.000" and ds._usd(-(10 ** 15 + 1)) == "unreadable"
    for junk in (1.5, float("inf"), float("nan"), True, None, "15000"):
        assert ds._usd(junk) == "unreadable", junk
    assert ds._usd(0) == "$0.000" and ds._usd(-15_000) == "-$0.015" and ds._usd(3_679_671) == "$3.680"


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


def test_the_runway_line_with_company_weekly_on_is_an_upper_bound():
    tail = " (sooner when a news day falls back to a lesson)"
    lines = _digest([], runway={"pool": 34, "unused": 22, "first_repeat": date(2027, 3, 20),
                                "no_later_than": True}).splitlines()
    assert ("Content pool: 22 of 34 lessons unused — the first repeat is due no later than Sat 2027-03-20"
            + tail) in lines
    lines = _digest([], runway={"pool": 34, "unused": 0, "first_repeat": date(2027, 4, 24),
                                "no_later_than": True}).splitlines()
    assert "Content pool: 0 of 34 lessons unused — lessons repeat no later than Sat 2027-04-24" + tail in lines
    assert "⚠️ Content pool: EMPTY — every posting day will be skipped (empty_pool)" in _digest(
        [], runway={"pool": 0, "unused": 0, "first_repeat": None, "no_later_than": True}).splitlines()
    # without the flag (the classes at "A"): the exact date, never "no later than"
    text = _digest([], runway={"pool": 34, "unused": 22, "first_repeat": date(2027, 3, 20)})
    assert "Content pool: 22 of 34 lessons unused — the first repeat is due Sat 2027-03-20" in text.splitlines()
    assert "no later than" not in text and "falls back" not in text


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


@pytest.mark.parametrize("edge, local", [
    ("0001-01-01T06:00:00+00:00", "01-01"),   # in HEAD's 6-day window: `at - FOLLOWER_BASELINE_AGE` raised already
    ("0001-01-03T00:00:00+00:00", "01-02"),
    ("0001-01-20T00:00:00+00:00", "01-19"),   # Batch 1's 28-day window: `at - FOLLOWER_CHANGE_TARGET` raised
    ("0001-01-28T23:59:59-05:00", "01-29"),   # the window's last second, on a New York offset
])
def test_a_platforms_only_snapshot_in_the_first_weeks_of_year_one_never_breaks_the_digest(edge, local):
    """OPS-R2-1 (re-review 2026-10-19): the New York clock CAN show a hand-edited time in the first weeks of
    year 1, so `_et_local` keeps it — and as a platform's NEWEST (here its only) snapshot it raised
    OverflowError out of summarize_week through `at - FOLLOWER_CHANGE_TARGET` (the scorecard's 4-week base),
    on every Monday tick and the Tuesday catch-up: the week's digest never went out. The other test above
    always pairs the edge value with a newer one, so it never made it `at`. Now the value costs only the
    change it would anchor: its count shows with no change, and every other platform's figures are intact."""
    measured = [
        _scored("threads", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, views=7), account=_acct(99, edge)),
        _scored("bluesky", "image", "2026-10-01T20:30:00+00:00", last=_snap(48.0),
                account=_acct(40, "2026-10-05T10:00:00+00:00")),
        _scored("bluesky", "image", "2026-09-08T20:30:00+00:00", account=_acct(30, "2026-09-28T10:00:00+00:00")),
    ]
    s = _summary([], measured=measured)
    f = s["followers"]["threads"]
    assert (f["followers"], f["baseline"], f["four_week_base"]) == (99, None, None)
    text = _digest([], measured=measured)
    assert ("Followers: BLUESKY 40 (+10 since 09-28; as of 10-05) · "
            f"THREADS 99 (as of {local}; no snapshot 6+ days older yet)") in text.splitlines()
    assert _score_lines(text) == [
        "• BLUESKY image: 2 posts · followers 40 (+10 since 09-28) · no view counts",
        f"• THREADS text: 1 post · followers 99 (change n/a, as of {local}) · views median 7, best 7 (1 at 24 h+)",
    ]


def test_a_year_one_newest_snapshot_keeps_the_change_its_older_snapshots_give():
    """OPS-R2-1's fix measures distances from the newest snapshot instead of computing `at - span`, so a
    year-1 newest snapshot with an older one 10 days before it keeps both changes, exactly as any other
    week's — never a guess, never an OverflowError (Batch 1 raised here; HEAD showed the +9)."""
    measured = [_scored("threads", "text", "2026-09-29T20:30:00+00:00", last=_snap(48.0, views=7),
                        account=_acct(99, "0001-01-20T00:00:00+00:00")),
                _scored("threads", "text", "2026-09-28T20:30:00+00:00", last=_snap(48.0, views=3),
                        account=_acct(90, "0001-01-10T00:00:00+00:00"))]
    f = _summary([], measured=measured)["followers"]["threads"]
    assert (f["followers"], f["baseline"], f["four_week_base"]) == (99, 90, 90)
    assert f["baseline_at"] == f["four_week_base_at"] == datetime(1, 1, 10, tzinfo=timezone.utc)
    text = _digest([], measured=measured)
    assert "Followers: THREADS 99 (+9 since 01-09; as of 01-19)" in text.splitlines()
    assert _score_lines(text) == ["• THREADS text: 2 posts · followers 99 (+9 since 01-09, as of 01-19) · views "
                                  "median 5, best 7 (2 at 24 h+)"]


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


def test_a_runs_row_for_a_run_that_failed_after_its_posts_were_recorded_never_reads_as_a_lost_day():
    """Review F1 (live regression, 2026-10-16): the review bot offers an `assets_ready` run's posts whatever its
    status, so a day that ends `failed` there may well have posted — "failed after assets_ready" alone read as a
    lost day. The row says its posts were recorded, and nothing more (no review state is read here); a
    checkpoint that recorded NO post, or any earlier stage, keeps the plain row."""
    runs = [_run("failed", run_date="2026-09-28", stage="assets_ready"),
            _run("failed", run_date="2026-09-29", stage="assets_ready", metadata={"posts_recorded": 0}),
            _run("failed", run_date="2026-10-01", stage="rendered"),
            _run("failed", run_date="2026-10-03", stage="assets_ready", metadata={"posts_recorded": 4})]
    text = _digest([], runs=runs)
    assert ("Runs:\n• Mon 09-28 failed after assets_ready — its posts were recorded\n"
            "• Tue 09-29 failed after assets_ready\n• Thu 10-01 failed after rendered\n"
            "• Sat 10-03 failed after assets_ready — its posts were recorded\n") in text
    for claim in STALE_POST_CLAIMS + ("review chat", "lost"):
        assert claim not in text.split("Runs:\n", 1)[1].split("\n\n", 1)[0], claim


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
    # the cut keeps the top: the cost line under the date line survives it whole
    assert text.splitlines()[2:4] == [
        "💵 Cost last week ≈ $0.000: X $0.000 (our ledger) · Gemini ≈ $0.000 · worker ≈ $0.000 · "
        "Upload-Post $0.000 (Free plan · 0 uploads)",
        "Usage (X + Gemini + worker): last week ≈ $0.000 · week before ≈ $0.000",
    ]


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
    # the cost line's reads: each junk input is ITS part unreadable in both weeks, never $0.000
    s = _summary([], runs=junk, charge_rows=junk, prev_runs=junk, script_rows=junk)
    for week in ("last", "before"):
        part = s["cost"][week]
        assert (part["x"], part["gemini"], part["worker"]) == (None, None, None)
        assert part["usage_unreadable"] == ["X", "Gemini", "worker"] and part["usage_partial"] is True
    assert (s["cost"]["last"]["upload_post"], s["cost"]["last"]["unreadable"]) == (0, ["X", "Gemini", "worker"])
    assert ds.compose_digest(s).splitlines()[2:4] == [
        "💵 Cost last week ≥ $0.000 (X, Gemini and worker unreadable): X unreadable · Gemini unreadable · "
        "worker unreadable · Upload-Post $0.000 (Free plan · 0 uploads)",
        "Usage (X + Gemini + worker): last week ≥ $0.000 (X, Gemini and worker unreadable) · "
        "week before ≥ $0.000 (X, Gemini and worker unreadable)",
    ]
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


# ── the weekly cost line (pure: summarize_week → weekly_cost → compose_digest) ─
#
# The worked example (design 2026-10-05; the digest of Mon 2026-10-05 covers Mon 09-28 – Sun 10-04 ET =
# [09-28T04:00Z, 10-05T04:00Z); the week before starts 09-21T04:00Z). Every number is derived BY HAND
# from the published formulas, in integer micro-dollars — never read back from the code:
#   X       our journal, each entry by its own time: last week 4 × 15,000 + 5,000 − 4,000 = 61,000; the
#           week before 3 × 15,000 + 10,000 = 55,000.
#   Gemini  tokens × $1.50 per 1M, half-up: 82,800 → 124,200; 60,000 → 90,000.
#   worker  per second (MB × 3,860 + 4 vCPU × 7,720 × 1,024) / 1,024 nano-$, × whole ms, half-up to micros:
#           303.0 s at a 2,587 MB peak = 12,311; 2.6 s at the 4,096 MB fallback = 120 (46,320 nano-$ a
#           second); 250.0 s at 2,500 MB = 10,076. Last week 4 × 12,311 + 3 × 120 = 49,604; the week
#           before 4 × 10,076 + 3 × 120 = 40,664.
#   Upload-Post  $16 a month × 7 / 30.4375 = 3,679,671 (half-up).
#   Last week 61,000 + 124,200 + 49,604 + 3,679,671 = 3,914,475 ($3.914), of which usage (never the fee)
#   234,804 ($0.235); the week before is compared by USAGE alone — 55,000 + 90,000 + 40,664 = 185,664
#   ($0.186) — since the plan fee in force a week earlier is not recorded (today's fee would misprice it).

FEE_16 = 16_000_000                     # MARKETING_UPLOAD_POST_MONTHLY_USD = 16 (Basic, billed annually)
FEE_16_WEEK = 3_679_671
COST_LAST = {"x": 61_000, "gemini": 124_200, "worker": 49_604}
COST_BEFORE = {"x": 55_000, "gemini": 90_000, "worker": 40_664}
#: (a): the normal week at $16 a month, under the declared $1.00 alert line.
COST_A = ("💵 Cost last week ≈ $3.914: X $0.061 (our ledger) · Gemini ≈ $0.124 · worker ≈ $0.050 · "
          "Upload-Post $3.680 (plan fee · 3 uploads)")


def _usage(last: str = "≈ $0.235", before: str = "≈ $0.186") -> str:
    """The usage line (X + Gemini + worker, never the plan fee): the worked example's, either week's text
    replaceable."""
    return f"Usage (X + Gemini + worker): last week {last} · week before {before}"


USAGE_A = _usage()
#: (b): a usage part unreadable — each week's readable floor, by hand. X: 124,200 + 49,604 = 173,804 and
#: 90,000 + 40,664 = 130,664; Gemini: 61,000 + 49,604 = 110,604 and 55,000 + 40,664 = 95,664; worker:
#: 61,000 + 124,200 = 185,200 and 55,000 + 90,000 = 145,000.
X_FLOOR = ("≥ $0.174 (X unreadable)", "≥ $0.131 (X unreadable)")
GEMINI_FLOOR = ("≥ $0.111 (Gemini unreadable)", "≥ $0.096 (Gemini unreadable)")
WORKER_FLOOR = ("≥ $0.185 (worker unreadable)", "≥ $0.145 (worker unreadable)")
#: …the X read failed or was capped (both weeks come from ONE read) — the total is a floor and names what
#: it lacks.
COST_X_UNREADABLE = ["💵 Cost last week ≥ $3.853 (X unreadable): X unreadable · Gemini ≈ $0.124 · worker ≈ $0.050 · "
                     "Upload-Post $3.680 (plan fee · 3 uploads)",
                     _usage(*X_FLOOR)]
COST_GEMINI_UNREADABLE = ["💵 Cost last week ≥ $3.790 (Gemini unreadable): X $0.061 (our ledger) · Gemini unreadable · "
                          "worker ≈ $0.050 · Upload-Post $3.680 (plan fee · 3 uploads)",
                          _usage(*GEMINI_FLOOR)]
COST_WORKER_UNREADABLE = ["💵 Cost last week ≥ $3.865 (worker unreadable): X $0.061 (our ledger) · Gemini ≈ $0.124 · "
                          "worker unreadable · Upload-Post $3.680 (plan fee · 3 uploads)",
                          _usage(*WORKER_FLOOR)]

#: A posting run's billed seconds 4.2 + 1.1 + 38.5 + 95.0 + 160.3 + 3.9 = 303.0 at a 2,587.3 MB peak; the
#: maxrss reading is not a cgroup peak and is never priced.
_POSTING_TIMINGS = {"preflight_s": 4.2, "selected_s": 1.1, "scripted_s": 38.5, "voiced_s": 95.0, "rendered_s": 160.3,
                    "assets_ready_s": 3.9, "voiced_cgroup_peak_mb": 2587.3, "rendered_cgroup_peak_mb": 2401.0,
                    "rendered_self_maxrss_mb": 9999.0}
#: A rest day: the worker's preflight only, no memory peak recorded.
_REST_TIMINGS = {"preflight_s": 2.6}
#: The week before's posting run: 3.0 + 40.0 + 80.0 + 127.0 = 250.0 s at 2,500 MB.
_PREV_POSTING_TIMINGS = {"preflight_s": 3.0, "scripted_s": 40.0, "voiced_s": 80.0, "rendered_s": 127.0,
                         "voiced_cgroup_peak_mb": 2500.0}
#: marketing_scripts.tokens_used per ET day: the week before 4 × 15,000; last week 82,800 (a rest day's 0).
_SCRIPT_TOKENS = {"2026-09-21": 15_000, "2026-09-22": 15_000, "2026-09-24": 15_000, "2026-09-26": 15_000,
                  "2026-09-28": 21_000, "2026-09-29": 19_400, "2026-10-01": 22_100, "2026-10-02": 0,
                  "2026-10-03": 20_300}


def _charge(at: Any, op: str, micros: Any) -> Dict[str, Any]:
    return {"at": at, "op": op, "micros": micros}


def _x_post(run_date: str, *charges: Dict[str, Any], updated_at: str) -> Dict[str, Any]:
    """An X post carrying a `metadata.charges` journal; `updated_at` is its last touch (every journal write
    bumps it in production)."""
    return _post("x", "published", run_date=run_date, meta={"charges": [dict(c) for c in charges]},
                 updated_at=updated_at)


def _x_journal() -> List[Dict[str, Any]]:
    """The worked example's X posts, as `list_charge_rows_since("x", <the week before's start>)` returns them."""
    return [
        # the week before: three posts and a retract's delete
        _x_post("2026-09-22", _charge("2026-09-22T20:31:00+00:00", "x_create", 15_000),
                updated_at="2026-09-22T20:31:00+00:00"),
        _x_post("2026-09-24", _charge("2026-09-24T20:31:00+00:00", "x_create", 15_000),
                updated_at="2026-09-24T20:31:00+00:00"),
        _x_post("2026-09-26", _charge("2026-09-26T20:31:00+00:00", "x_create", 15_000),
                _charge("2026-09-27T15:00:00+00:00", "x_delete", 10_000), updated_at="2026-09-27T15:00:00+00:00"),
        # last week: four posts, one metrics read and its correction (dated at the read it corrects)
        _x_post("2026-09-28", _charge("2026-09-28T20:31:00+00:00", "x_create", 15_000),
                updated_at="2026-09-28T20:31:00+00:00"),
        _x_post("2026-09-29", _charge("2026-09-29T20:31:00+00:00", "x_create", 15_000),
                _charge("2026-09-29T23:00:00+00:00", "x_metrics_read", 5_000),
                _charge("2026-09-29T23:00:00+00:00", "x_metrics_read_correction", -4_000),
                updated_at="2026-09-29T23:00:00+00:00"),
        _x_post("2026-10-01", _charge("2026-10-01T20:31:00Z", "x_create", 15_000), updated_at="2026-10-01T20:31:00Z"),
        # …and a metrics read on Mon 10-05: THIS week's money, never last week's
        _x_post("2026-10-03", _charge("2026-10-03T20:31:00+00:00", "x_create", 15_000),
                _charge("2026-10-05T10:00:00+00:00", "x_metrics_read", 5_000), updated_at="2026-10-05T10:00:00+00:00"),
    ]


def _uploaded(platform: str, status: str = "published", *, at: Any = "2026-10-03T21:00:00+00:00",
              **kw: Any) -> Dict[str, Any]:
    """A post Upload-Post ACCEPTED (`metadata.publish.upload_post.submitted_at`)."""
    return _post(platform, status, meta={"publish": {"state": "submitted", "upload_post": {
        "request_id": f"req-{uuid.uuid4().hex[:8]}", "submitted_at": at}}}, **kw)


def _week_runs(posting: tuple, rest: tuple, timings: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ([_run("published", run_date=d, timings=copy.deepcopy(timings)) for d in posting]
            + [_run("skipped", run_date=d, metadata={"skip_reason": "rest_day"}, timings=dict(_REST_TIMINGS))
               for d in rest])


def _cost_inputs() -> Dict[str, Any]:
    """The worked example as `summarize_week` keyword arguments: the week's posts (three Upload-Post
    submissions — one of them refused AFTER the submit, still an upload), this week's and the week before's
    runs (Mon/Tue/Thu/Sat posting, the rest rest days), the X journal, both weeks' script token counts and
    the $16 plan."""
    return dict(
        posts=[_uploaded("youtube", at="2026-09-29T21:00:00+00:00", run_date="2026-09-29"),
               _uploaded("instagram", "failed", at="2026-10-01T21:00:00Z", run_date="2026-10-01"),
               _uploaded("linkedin")],
        runs=_week_runs(("2026-09-28", "2026-09-29", "2026-10-01", "2026-10-03"),
                        ("2026-09-30", "2026-10-02", "2026-10-04"), _POSTING_TIMINGS),
        prev_runs=_week_runs(("2026-09-21", "2026-09-22", "2026-09-24", "2026-09-26"),
                             ("2026-09-23", "2026-09-25", "2026-09-27"), _PREV_POSTING_TIMINGS),
        charge_rows=_x_journal(),
        script_rows=[{"run_date": d, "tokens_used": n} for d, n in _SCRIPT_TOKENS.items()],
        upload_post_fee_micros=FEE_16,
    )


def _cost_summary(**overrides: Any) -> Dict[str, Any]:
    kw = _cost_inputs()
    kw.update(overrides)
    return _summary(kw.pop("posts"), **kw)


def _worked_lines(**overrides: Any) -> List[str]:
    """The worked example's digest, as lines (the 4,096-unit cap asserted)."""
    text = ds.compose_digest(_cost_summary(**overrides))
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS
    return text.splitlines()


#: The keys of each week's part the digest renders from (the design's contract; extra keys are free).
_LAST_KEYS = ("x", "gemini", "worker", "upload_post", "total", "unreadable", "usage", "usage_unreadable",
              "usage_partial", "undated", "uploads", "uploads_floor", "free")
_BEFORE_KEYS = ("x", "gemini", "worker", "usage", "usage_unreadable", "usage_partial")
EXPECTED_LAST = {"x": 61_000, "gemini": 124_200, "worker": 49_604, "upload_post": FEE_16_WEEK, "total": 3_914_475,
                 "unreadable": [], "usage": 234_804, "usage_unreadable": [], "usage_partial": False, "undated": 0,
                 "uploads": 3, "uploads_floor": False, "free": False}
EXPECTED_BEFORE = {"x": 55_000, "gemini": 90_000, "worker": 40_664, "usage": 185_664, "usage_unreadable": [],
                   "usage_partial": False}


def test_the_cost_line_sits_under_the_date_line_and_prices_a_normal_week():
    s = _cost_summary()
    last, before = s["cost"]["last"], s["cost"]["before"]
    assert {k: last[k] for k in _LAST_KEYS} == EXPECTED_LAST
    assert {k: before[k] for k in _BEFORE_KEYS} == EXPECTED_BEFORE
    # the week before has no fee and no total: today's plan fee is not that week's
    assert not {"upload_post", "total", "unreadable", "uploads"} & set(before)
    assert s["cost"]["warn"] == 1_000_000
    lines = ds.compose_digest(s).splitlines()
    # right under the date line, then the blank line — no ⚠️ line ($0.235 of usage is under $1.00)
    assert lines[:5] == ["📊 Caydex marketing — weekly digest", "Mon 2026-09-28 → Sun 2026-10-04 (ET)", COST_A, USAGE_A,
                         ""]
    assert sum(line.startswith("💵") for line in lines) == 1


def test_an_unreadable_part_makes_the_total_a_floor_and_names_it():
    assert _worked_lines(charge_rows=None)[2:4] == COST_X_UNREADABLE
    # by hand: the worker alone, 49,604 and 40,664
    assert _worked_lines(charge_rows=None, script_rows=None)[2:4] == [
        "💵 Cost last week ≥ $3.729 (X and Gemini unreadable): X unreadable · Gemini unreadable · worker ≈ $0.050 · "
        "Upload-Post $3.680 (plan fee · 3 uploads)",
        _usage("≥ $0.050 (X and Gemini unreadable)", "≥ $0.041 (X and Gemini unreadable)")]
    none_read = _usage("≥ $0.000 (X, Gemini and worker unreadable)", "≥ $0.000 (X, Gemini and worker unreadable)")
    three = _worked_lines(charge_rows=None, script_rows=None, runs=None, prev_runs=None)
    assert three[2:4] == [
        "💵 Cost last week ≥ $3.680 (X, Gemini and worker unreadable): X unreadable · Gemini unreadable · "
        "worker unreadable · Upload-Post $3.680 (plan fee · 3 uploads)", none_read]
    # the fee is not usage: an unreadable fee leaves the usage line as it was
    four = _worked_lines(charge_rows=None, script_rows=None, runs=None, prev_runs=None, upload_post_fee_micros=None)
    assert four[2:4] == [
        "💵 Cost last week ≥ $0.000 (X, Gemini, worker and Upload-Post unreadable): X unreadable · Gemini unreadable · "
        "worker unreadable · Upload-Post unreadable", none_read]
    # an unreadable part is never shown as a number
    for lines in (three, four):
        assert re.search(r"(X|Gemini ≈|worker ≈|Upload-Post) \$0\.000", "\n".join(lines[2:4])) is None
    # each week's runs are their own read: one failing leaves the other week whole
    assert _worked_lines(runs=None)[2:4] == [
        "💵 Cost last week ≥ $3.865 (worker unreadable): X $0.061 (our ledger) · Gemini ≈ $0.124 · worker unreadable · "
        "Upload-Post $3.680 (plan fee · 3 uploads)", _usage(last=WORKER_FLOOR[0])]
    assert _worked_lines(prev_runs=None)[2:4] == [COST_A, _usage(before=WORKER_FLOOR[1])]


@pytest.mark.parametrize("cost", [None, "x", {}, {"last": {}, "before": "x"}, {"last": [], "before": {}}])
def test_a_cost_section_that_is_not_a_cost_says_unreadable(cost):
    s = _summary([])
    s["cost"] = cost
    assert ds.compose_digest(s).splitlines()[2:4] == ["💵 Cost last week: unreadable (see the web logs)", ""]


@pytest.mark.parametrize("at", [None, "", "garbage", 7, {}, "2026-13-01T00:00:00+00:00"])
def test_undated_x_charges_are_named_never_counted(at):
    """An entry whose time cannot be read belongs to no week: it is never summed into one (a weekly figure
    that counted it would count it EVERY week) — it is named, once, whatever its amount."""
    one = _x_journal() + [_x_post("2026-10-01", _charge(at, "x_create", 15_000), updated_at="2026-10-01T20:31:00Z")]
    s = _cost_summary(charge_rows=one)
    assert (s["cost"]["last"]["x"], s["cost"]["before"]["x"], s["cost"]["last"]["undated"]) == (61_000, 55_000, 1)
    lines = ds.compose_digest(s).splitlines()
    assert lines[2:4] == [COST_A.replace("X $0.061 (our ledger)", "X $0.061 (our ledger; 1 undated charge not counted)"),
                          USAGE_A]
    # two of them — one with a thousand-dollar amount, one with no readable amount at all: still only named
    two = _x_journal() + [_x_post("2026-09-29", _charge(at, "x_create", 10 ** 12), _charge("garbage", "x_read", "abc"),
                                  updated_at="2026-09-29T20:31:00+00:00")]
    lines = _worked_lines(charge_rows=two)
    assert lines[2:4] == [COST_A.replace("X $0.061 (our ledger)", "X $0.061 (our ledger; 2 undated charges not counted)"),
                          USAGE_A]


@pytest.mark.parametrize("bad", [{"micros": 10 ** 9 + 1}, {"micros": -(10 ** 9 + 1)}, {"micros": "abc"}, {"micros": None},
                                 {}, {"micros": True}, {"micros": float("nan")}, {"micros": float("inf")},
                                 {"micros": [1]}, {"micros": 10 ** 400}])
def test_a_thousand_dollar_or_unpriceable_x_entry_makes_only_its_week_unreadable(caplog, bad):
    caplog.set_level(logging.WARNING, logger=ds.__name__)

    def journal_with(at: str) -> tuple:
        post = _x_post("2026-09-29", {"at": at, "op": "x_create", **bad}, updated_at="2026-10-02T12:00:00+00:00")
        return post, _x_journal() + [post]

    def warned(post_id: str) -> bool:
        return any(post_id in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)

    post, rows = journal_with("2026-10-01T12:00:00+00:00")                 # last week
    assert _worked_lines(charge_rows=rows)[2:4] == [COST_X_UNREADABLE[0], _usage(last=X_FLOOR[0])]
    assert warned(post["id"])
    caplog.clear()
    post, rows = journal_with("2026-09-23T12:00:00+00:00")                 # the week before
    assert _worked_lines(charge_rows=rows)[2:4] == [COST_A, _usage(before=X_FLOOR[1])]
    assert warned(post["id"])
    caplog.clear()
    for at in ("2026-10-05T04:00:00+00:00", "2026-09-21T03:59:59+00:00"):  # in neither week: nothing to read
        post, rows = journal_with(at)
        assert _worked_lines(charge_rows=rows)[2:4] == [COST_A, USAGE_A]
        assert not warned(post["id"])


def test_the_x_entry_rule_reads_numbers_and_numeric_text_up_to_a_thousand_dollars():
    """The cost line shares the X cap's entry rule (`_charge_entry`): numeric text and a float read; one entry
    of exactly $1,000 is still a reading (and passes the alert line); a cent more is a hand edit."""
    extra = [_x_post("2026-10-01", _charge("2026-10-01T12:00:00+00:00", "x_create", "15000"),
                     _charge("2026-10-01T12:00:01+00:00", "x_create", 15000.7), updated_at="2026-10-01T12:00:01+00:00")]
    assert _cost_summary(charge_rows=_x_journal() + extra)["cost"]["last"]["x"] == 91_000
    thousand = [_x_post("2026-10-01", _charge("2026-10-01T12:00:00+00:00", "x_create", 10 ** 9),
                        updated_at="2026-10-01T12:00:00+00:00")]
    lines = _worked_lines(charge_rows=_x_journal() + thousand)
    assert lines[2:5] == [
        "💵 Cost last week ≈ $1003.914: X $1000.061 (our ledger) · Gemini ≈ $0.124 · worker ≈ $0.050 · "
        "Upload-Post $3.680 (plan fee · 3 uploads)", _usage(last="≈ $1000.235"),
        "⚠️ Usage ≈ $1000.235 passed your $1.00 alert line"]
    # a net-negative week (a refund dated in it) is shown signed, never clamped
    refund = [_x_post("2026-10-01", _charge("2026-10-01T12:00:00+00:00", "refund", -100_000),
                      updated_at="2026-10-01T12:00:00+00:00")]
    s = _cost_summary(charge_rows=_x_journal() + refund)
    assert s["cost"]["last"]["x"] == -39_000
    assert "X -$0.039 (our ledger)" in ds.compose_digest(s).splitlines()[2]


def test_an_x_charge_on_a_week_boundary_counts_once():
    """Both windows are half-open on the digest's own instants: [the week before's start, start) and
    [start, end) — an entry at a boundary lands in exactly one week, whatever offset it was written in."""
    entries = [("2026-09-21T03:59:59+00:00", 1),          # before the week before: nowhere
               ("2026-09-21T00:00:00-04:00", 20),         # = 09-21T04:00Z, the week before's first instant
               ("2026-09-28T03:59:59.999999Z", 300),      # the week before's last microsecond
               ("2026-09-28T04:00:00Z", 4_000),           # last week's first instant
               ("2026-10-05T03:59:59+00:00", 50_000),     # last week's last second
               ("2026-10-05T00:00:00-04:00", 600_000)]    # = 10-05T04:00Z, the end: this week's
    rows = [_x_post("2026-09-28", *[_charge(at, "x_create", m) for at, m in entries], updated_at="2026-10-05T04:00:00Z")]
    s = _summary([], charge_rows=rows)
    assert (s["cost"]["last"]["x"], s["cost"]["before"]["x"]) == (54_000, 320)


def test_a_reversal_nets_out_in_the_week_of_the_charge_it_reverses():
    """A refund or correction is dated at the charge it reverses (run_service), however late it is WRITTEN:
    it nets out inside that charge's own week — and one reversing a charge from before the week before
    began belongs to neither week. By hand: last week 61,000 − 15,000 = 46,000 (total 3,899,475, usage
    219,804); the week before 55,000 − 15,000 = 40,000 (usage 170,664)."""
    older = [_x_post("2026-09-19", _charge("2026-09-19T20:31:00+00:00", "refund", -15_000),
                     updated_at="2026-10-02T09:00:00+00:00")]
    assert _worked_lines(charge_rows=_x_journal() + older)[2:4] == [COST_A, USAGE_A]
    both = [_x_post("2026-10-01", _charge("2026-10-01T20:31:00Z", "refund", -15_000),
                    _charge("2026-09-24T20:31:00+00:00", "x_metrics_read_correction", -15_000),
                    updated_at="2026-10-04T12:00:00+00:00")]
    s = _cost_summary(charge_rows=_x_journal() + both)
    assert (s["cost"]["last"]["x"], s["cost"]["before"]["x"]) == (46_000, 40_000)
    assert ds.compose_digest(s).splitlines()[2:5] == [
        "💵 Cost last week ≈ $3.899: X $0.046 (our ledger) · Gemini ≈ $0.124 · worker ≈ $0.050 · "
        "Upload-Post $3.680 (plan fee · 3 uploads)",
        _usage("≈ $0.220", "≈ $0.171"), ""]


def test_a_malformed_journal_row_or_entry_adds_nothing_and_keeps_x_readable():
    """Only an IN-WINDOW entry whose amount cannot be read makes X unreadable. A row that is not an object,
    metadata that is not an object, a journal that is not a list and an entry that is not an object hold no
    charge at all: they add nothing, name nothing, and never cost the part its reading."""
    in_week = _charge("2026-10-01T12:00:00+00:00", "x_create", 5)
    junk = ["junk", None, 7, [in_week],
            {"id": "meta-none", "metadata": None},
            {"id": "meta-text", "metadata": "charges"},
            {"id": "no-journal", "metadata": {"dry_run": False}},
            {"id": "journal-text", "metadata": {"charges": "15000"}},
            {"id": "journal-object", "metadata": {"charges": in_week}},
            {"id": "entries-junk", "metadata": {"charges": ["x_create", 15_000, None,
                                                             ["2026-10-01T12:00:00+00:00", "x_create", 5]]}}]
    s = _cost_summary(charge_rows=_x_journal() + junk)
    assert (s["cost"]["last"]["x"], s["cost"]["before"]["x"], s["cost"]["last"]["undated"]) == (61_000, 55_000, 0)
    assert ds.compose_digest(s).splitlines()[2:4] == [COST_A, USAGE_A]


@pytest.mark.parametrize("warn, alert", [
    (0, None),                    # 0 = no alert line at all
    (1_000_000, None),            # the declared $1.00: $3.914 in all, but only $0.235 of it is USAGE
    (234_804, None),              # exactly at the line: not above it
    (234_803, "⚠️ Usage ≈ $0.235 passed your $0.23 alert line"),
    (200_000, "⚠️ Usage ≈ $0.235 passed your $0.20 alert line"),
    # unreadable alert values (only a monkeypatch reaches the pure function with one): no line, no crash
    (-1, None), (True, None), (0.2, None), ("200000", None), (None, None), (10 ** 13, None),
])
def test_the_usage_alert_line(warn, alert):
    lines = _worked_lines(cost_warn_micros=warn)
    assert lines[2:4] == [COST_A, USAGE_A]
    assert lines[4] == (alert or "")
    assert sum("⚠️ Usage" in line for line in lines) == (1 if alert else 0)


def test_the_usage_alert_compares_only_what_could_be_read():
    # X unreadable: the readable floor (Gemini + worker = $0.174) already passes $0.15 — said as a floor
    lines = _worked_lines(charge_rows=None, cost_warn_micros=150_000)
    assert lines[2:5] == COST_X_UNREADABLE + ["⚠️ Usage ≥ $0.174 passed your $0.15 alert line"]
    # …but a floor under the line says nothing: the unread part might or might not pass it
    assert _worked_lines(charge_rows=None, cost_warn_micros=200_000)[4] == ""
    assert _worked_lines(charge_rows=None, script_rows=None, runs=None, prev_runs=None, cost_warn_micros=1)[4] == ""
    # the Free plan changes nothing about usage
    assert _worked_lines(upload_post_fee_micros=0, cost_warn_micros=200_000)[4] == (
        "⚠️ Usage ≈ $0.235 passed your $0.20 alert line")


def test_the_upload_post_part_free_vs_plan_and_what_counts_as_an_upload():
    # (e) the Free plan: no fee, the uploads still counted
    assert _worked_lines(upload_post_fee_micros=0)[2:4] == [
        "💵 Cost last week ≈ $0.235: X $0.061 (our ledger) · Gemini ≈ $0.124 · worker ≈ $0.050 · "
        "Upload-Post $0.000 (Free plan · 3 uploads)",
        USAGE_A]
    assert _worked_lines()[2] == COST_A                                       # $16 a month: the plan fee
    # a capped post read may hide uploads: a floor
    assert _worked_lines(posts_capped=True)[2] == COST_A.replace("(plan fee · 3 uploads)", "(plan fee · ≥ 3 uploads)")
    assert _worked_lines(posts=[_uploaded("youtube")])[2].endswith("Upload-Post $3.680 (plan fee · 1 upload)")
    assert _worked_lines(posts=[])[2].endswith("Upload-Post $3.680 (plan fee · 0 uploads)")
    # every Upload-Post platform counts; status does not matter once Upload-Post took the job (refused after
    # the submit = an upload)
    counted = [_uploaded(p) for p in ("tiktok", "youtube", "instagram", "facebook", "linkedin", "threads")]
    counted += [_uploaded("instagram", "failed"), _uploaded("youtube", "retracted"), _uploaded("facebook", "queued"),
                _uploaded("threads", at="2026-10-03T21:00:00Z")]
    # …and the posts Upload-Post took although no submit was recorded (an AMBIGUOUS send carries no
    # submitted_at): reconcile FOUND it (platform_post_id), the owner tapped "It's live" (published),
    # reconcile read its failure from Upload-Post (failed_on_platform), or Upload-Post answered with its own
    # job or poll id
    sent = {"request_id": "r-9", "first_sent_at": "2026-10-03T20:31:00+00:00"}
    counted += [
        _post("youtube", meta={"publish": {"state": "published", "upload_post": {**sent, "platform_post_id": "yt-1"}}}),
        _post("linkedin", meta={"publish": {"state": "published", "upload_post": dict(sent)}}),
        _post("threads", "failed", meta={"publish": {"state": "failed_on_platform", "upload_post": dict(sent)}}),
        _post("tiktok", "failed", meta={"publish": {"state": "refused", "upload_post": {**sent, "job_id": "job-1"}}}),
        _post("instagram", "failed", meta={"publish": {"state": "refused", "upload_post": {**sent, "job_id": 42}}}),
        _post("facebook", "queued", meta={"publish": {"state": "submitted", "upload_post": {**sent, "poll_id": "up-7"}}}),
        _post("youtube", "failed", meta={"publish": {"state": "refused",
                                                     "upload_post": {**sent, "platform_post_id": "yt-2"}}}),
    ]
    not_counted = [
        _uploaded("youtube", live=False),                                   # a rehearsal row
        _uploaded("x"), _uploaded("bluesky"),                               # not an Upload-Post platform
        _uploaded("YouTube"),                                               # not a platform at all
        # refused before Upload-Post took it: no readable submit, no id, not on the platform
        _uploaded("tiktok", "failed", at=None), _uploaded("tiktok", "failed", at=""),
        _uploaded("facebook", "failed", at="not a time"), _uploaded("threads", "failed", at=7),
        _post("linkedin", "failed", meta={"publish": {"state": "refused", "upload_post": {"request_id": "r-1"}}}),
        _post("tiktok", "failed", meta={"publish": {"state": "refused", "upload_post": {
            "job_id": "  ", "poll_id": True, "platform_post_id": None}}}),   # blank, bool, null ids
        _post("youtube", "failed", meta={"publish": "submitted"}),          # publish not an object
        _post("instagram", "failed", meta={"publish": {"upload_post": "r-1"}}),   # upload_post not an object
        _post("facebook", "failed", meta={"upload_post": {"submitted_at": "2026-10-03T21:00:00+00:00"}}),   # not under publish
        _post("threads", "approved", meta={"publish": {"state": "not_sent", "upload_post": dict(sent)}}),   # never left
        _post("youtube", "skipped", meta={"publish": {"state": "absent_expired", "upload_post": dict(sent)}}),
    ]
    s = _cost_summary(posts=counted + not_counted)
    assert (s["cost"]["last"]["uploads"], s["cost"]["last"]["uploads_floor"]) == (17, False)
    assert ds.compose_digest(s).splitlines()[2].endswith("Upload-Post $3.680 (plan fee · 17 uploads)")
    # a fee the pure function cannot read is that part unreadable — never a guessed $0 Free plan
    for junk in (None, -1, True, 1.5, "16000000", 10 ** 13):
        assert _worked_lines(upload_post_fee_micros=junk)[2:4] == [
            "💵 Cost last week ≥ $0.235 (Upload-Post unreadable): X $0.061 (our ledger) · Gemini ≈ $0.124 · "
            "worker ≈ $0.050 · Upload-Post unreadable",
            USAGE_A], junk


def test_the_week_before_is_compared_by_usage_whatever_todays_plan_fee():
    """Review 2026-10-07: the week before used to be priced at TODAY's fee, so the first Monday after a plan
    change hid the change — Free → $16 showed a $0.186 week as "Week before ≈ $3.865" beside "$3.914". The
    plan in force a week earlier is not recorded, so that week is compared by usage alone: the same line at
    any fee, and never a total that includes one."""
    lines = {fee: _worked_lines(upload_post_fee_micros=fee)[3] for fee in (0, FEE_16, 24_000_000, None, "junk")}
    assert set(lines.values()) == {USAGE_A}
    assert "3.865" not in "\n".join(_worked_lines())             # the fee-inclusive week-before total is gone


def test_the_x_journal_probe_fits_in_one_postgrest_response():
    """PostgREST cuts every answer at its max-rows (≈1,000) whatever `.limit()` asks: at a limit of 1,000
    the probe row (limit + 1) could never arrive, and a cut answer read as complete (review 2026-10-07)."""
    from app.utils.postgrest_paging import PAGE_SIZE
    assert 0 < ds.X_CHARGE_ROW_LIMIT and ds.X_CHARGE_ROW_LIMIT + 1 <= PAGE_SIZE


def test_an_upload_post_send_of_unknown_outcome_makes_the_upload_count_a_floor():
    """A send that may or may not have reached Upload-Post — an ambiguous answer still queued, its
    escalation, a crash mid-send, or a post the owner marked "Not posted" (absent from the platform, but the
    job may still have been taken) — is not counted, and the count says "≥". One that has since resolved
    either way is exact again."""
    base = _cost_inputs()["posts"]                                           # 3 uploads
    sent = {"request_id": "r-9", "first_sent_at": "2026-10-03T20:31:00+00:00"}
    unsure = [_post("threads", "queued", meta={"publish": {"state": "unknown", "upload_post": dict(sent)}}),
              _post("youtube", "queued", meta={"publish": {"state": "escalated", "escalated_at": "2026-10-03T22:00:00Z",
                                                           "upload_post": dict(sent)}}),
              _post("tiktok", "queued", meta={"publish": {"state": "sending", "upload_post": dict(sent)}}),
              _post("linkedin", "failed", meta={"publish": {"state": "owner_not_posted", "upload_post": dict(sent)}})]
    for post in unsure:
        s = _cost_summary(posts=base + [post])
        assert (s["cost"]["last"]["uploads"], s["cost"]["last"]["uploads_floor"]) == (3, True), post["platform"]
        assert ds.compose_digest(s).splitlines()[2] == COST_A.replace("(plan fee · 3 uploads)",
                                                                      "(plan fee · ≥ 3 uploads)")
    # not a floor: the same states on an X / Bluesky / rehearsal row, an acknowledged submit still queued,
    # a send that provably never left
    exact = [_post("x", "queued", meta={"publish": {"state": "unknown"}}),
             _post("bluesky", "queued", meta={"publish": {"state": "escalated"}}),
             _post("threads", "queued", live=False, meta={"publish": {"state": "unknown", "upload_post": dict(sent)}}),
             _post("threads", "approved", meta={"publish": {"state": "not_sent", "upload_post": dict(sent)}}),
             _post("tiktok", "failed", meta={"publish": {"state": "not_sent"}}),
             _post("facebook", "failed", meta={"publish": {"state": "refused", "upload_post": dict(sent)}})]
    s = _cost_summary(posts=base + exact)
    assert (s["cost"]["last"]["uploads"], s["cost"]["last"]["uploads_floor"]) == (3, False)
    assert ds.compose_digest(s).splitlines()[2] == COST_A
    # a hand-edited state or status of any JSON type is no state: never a crash (an unhashable list in a
    # frozenset lookup used to raise TypeError and take the whole digest down), never counted, never a floor
    for junk in (["unknown"], {"unknown": 1}, 7, None, True):
        odd = [_post("threads", "queued", meta={"publish": {"state": junk, "upload_post": dict(sent)}}),
               _post("linkedin", "failed", meta={"publish": {"state": junk, "upload_post": dict(sent)}}),
               _post("youtube", junk if not isinstance(junk, (list, dict)) else "queued",
                     meta={"publish": {"state": junk}})]
        s = _cost_summary(posts=base + odd)
        assert (s["cost"]["last"]["uploads"], s["cost"]["last"]["uploads_floor"]) == (3, False), junk
    # the ambiguous send that reconcile later FOUND is an exact upload again
    found = _post("threads", meta={"publish": {"state": "published", "upload_post": {**sent, "platform_post_id": "t-1"}}})
    s = _cost_summary(posts=base + [found])
    assert (s["cost"]["last"]["uploads"], s["cost"]["last"]["uploads_floor"]) == (4, False)


def test_the_fee_share_is_seven_days_of_a_month_rounded_half_up():
    assert ds._fee_week(FEE_16) == FEE_16_WEEK                 # 16,000,000 × 70,000 / 304,375 = 3,679,671.25
    assert ds._fee_week(24_000_000) == 5_519_507               # 24 a month (Basic, monthly): …507.19
    assert ds._fee_week(0) == 0
    assert ds._fee_week(304_375) == 70_000                     # exact
    assert ds._fee_week(2) == 0 and ds._fee_week(3) == 1        # 0.46 → 0; 0.69 → 1


@pytest.mark.parametrize("timings, micros", [
    ({}, 0),                                                                # nothing billed: a READABLE 0
    (_POSTING_TIMINGS, 12_311),
    (_REST_TIMINGS, 120),                                                   # no peak recorded: the 4,096 MB service
    ({"preflight_s": 2.6, "voiced_cgroup_peak_mb": 9000.0}, 120),           # a peak above the service: 4,096
    ({"preflight_s": 303.0, "voiced_cgroup_peak_mb": 9000.0}, 14_035),
    ({"preflight_s": 303.0}, 14_035),
    ({"preflight_s": 303.0, "voiced_cgroup_peak_mb": 0}, 9_357),            # a 0 MB peak: only the vCPU seconds
    ({"preflight_s": 3600}, 166_752),                                       # the bound is still a measurement
    (_PREV_POSTING_TIMINGS, 10_076),
    ({"preflight_s": 10.0}, 463),
    # keys that are not the worker's own billed seconds are ignored; junk peaks are not peaks
    ({"preflight_s": 2.6, "download_s": 99_999, "rendered_self_maxrss_mb": 9_999.0, "planned_s": 50.0}, 120),
    ({"preflight_s": 2.6, "voiced_cgroup_peak_mb": "2587", "rendered_cgroup_peak_mb": float("nan")}, 120),
    # a billed value that is not seconds: unreadable, never a silent 0
    ({"preflight_s": 3600.001}, None), ({"rendered_s": -1}, None), ({"voiced_s": True}, None),
    ({"scripted_s": "12"}, None), ({"selected_s": None}, None), ({"assets_ready_s": float("nan")}, None),
    ({"preflight_s": float("inf")}, None), ({"preflight_s": 2.6, "rendered_s": 10 ** 400}, None),
    ({"preflight_s": [2.6]}, None),
    # timings that are not an object
    ([], None), (None, None), ("303", None),
])
def test_worker_cost_per_run_is_integer_bounded_and_never_a_silent_zero(timings, micros):
    assert ds.run_worker_micros(_run("published", timings=copy.deepcopy(timings))) == micros


def test_one_unpriceable_run_makes_only_its_weeks_worker_part_unreadable(caplog):
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    base = _cost_inputs()
    bad = _run("published", run_date="2026-10-01", timings={**_POSTING_TIMINGS, "rendered_s": "160.3"})
    runs = [r for r in base["runs"] if r["run_date"] != "2026-10-01"] + [bad]
    assert _worked_lines(runs=runs)[2:4] == [COST_WORKER_UNREADABLE[0], _usage(last=WORKER_FLOOR[0])]
    assert any(bad["id"] in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    # the week before's: only the week before
    prev_bad = _run("published", run_date="2026-09-22", timings={"voiced_s": -5})
    prev = [r for r in base["prev_runs"] if r["run_date"] != "2026-09-22"] + [prev_bad]
    assert _worked_lines(prev_runs=prev)[2:4] == [COST_A, _usage(before=WORKER_FLOOR[1])]
    # a run the cost line never prices — another week's, an unreadable date, not an object — changes nothing
    stray = [_run("published", run_date="2026-10-05", timings={"rendered_s": "x"}),
             _run("published", run_date="2026-09-27", timings=None),
             _run("published", run_date="not a date", timings=None), "junk", None]
    assert _worked_lines(runs=base["runs"] + stray)[2:4] == [COST_A, USAGE_A]


@pytest.mark.parametrize("tokens, micros", [(0, 0), (1, 2), (2, 3), (3, 5), (82_800, 124_200), (60_000, 90_000),
                                            (2 ** 31 - 1, 3_221_225_471),
                                            (666_666_666_666, 999_999_999_999),   # $999,999.999…: still read
                                            (666_666_666_667, None)])            # past ±$1,000,000: unreadable
def test_gemini_is_tokens_at_one_blended_rate(tokens, micros):
    assert ds._gemini_week([{"run_date": "2026-10-01", "tokens_used": tokens}], WEEK_START, WEEK_END) == micros


def test_a_junk_token_count_or_an_undatable_script_makes_gemini_unreadable(caplog):
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    base = _cost_inputs()["script_rows"]
    for junk in (None, -1, True, 1.5, "12", 10 ** 15, float("nan")):
        rows = [dict(r, tokens_used=junk) if r["run_date"] == "2026-10-01" else r for r in base]
        assert _worked_lines(script_rows=rows)[2:4] == [COST_GEMINI_UNREADABLE[0], _usage(last=GEMINI_FLOOR[0])], junk
        rows = [dict(r, tokens_used=junk) if r["run_date"] == "2026-09-24" else r for r in base]
        assert _worked_lines(script_rows=rows)[2:4] == [COST_A, _usage(before=GEMINI_FLOOR[1])], junk
    assert any("tokens_used" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    # a row that cannot be placed in EITHER week makes both unreadable — it might belong to either
    for bad_day in (None, "garbage", "2026-10-01T00:00:00+00:00", 20261001,
                    datetime(2026, 10, 1, tzinfo=timezone.utc)):
        rows = base + [{"run_date": bad_day, "tokens_used": 5}]
        assert _worked_lines(script_rows=rows)[2:4] == COST_GEMINI_UNREADABLE, bad_day
    assert _worked_lines(script_rows=base + ["junk"])[2:4] == COST_GEMINI_UNREADABLE
    # a junk count on a day OUTSIDE both weeks is never read
    stray = [{"run_date": "2026-10-05", "tokens_used": "junk"}, {"run_date": "2026-09-20", "tokens_used": -1}]
    assert _worked_lines(script_rows=base + stray)[2:4] == [COST_A, USAGE_A]
    # a date object is a date, as PostgREST's string is
    assert ds._gemini_week([{"run_date": date(2026, 10, 1), "tokens_used": 2}], WEEK_START, WEEK_END) == 3


def test_a_300_post_week_keeps_the_cost_block_whole_under_the_date_line():
    """Rows are capped and, at worst, the text is cut from the bottom — the cost block at the top survives
    every cap, with its ⚠️ line."""
    emoji_error = "🔥" * 400 + " " + TOKEN_LIKE
    posts = []
    for i in range(300):
        if i % 3 == 0:
            posts.append(_uploaded("youtube", "failed", last_error=emoji_error))
        elif i % 3 == 1:
            posts.append(_post("bluesky", "rejected", meta={"review": {"decision": "rejected", "reason": "tone"}}))
        else:
            posts.append(_post(f"p{i:03d}", "published", metrics={"status": "ok", "last": {"likes": i}},
                               external_url="https://example.invalid/" + "📈" * 300))
    text = ds.compose_digest(_cost_summary(
        posts=posts, posts_capped=True, cost_warn_micros=200_000,
        link_hits=[{"campaign": f"c{i:03d}", "day": "2026-09-30", "hits": i} for i in range(300)],
        spend={f"op_{i:03d}": i for i in range(300)},
        escalated=[_post("x", "queued", meta={"escalated_at": "2026-10-03T20:40:00+00:00"}) for _ in range(20)]))
    assert utf16_len(text) <= ds.MAX_DIGEST_UNITS and "… and" in text
    assert TOKEN_SECRET not in text
    assert text.splitlines()[2:6] == [COST_A.replace("(plan fee · 3 uploads)", "(plan fee · ≥ 100 uploads)"), USAGE_A,
                                      "⚠️ Usage ≈ $0.235 passed your $0.20 alert line", ""]


@pytest.mark.parametrize("name, default", [("MARKETING_UPLOAD_POST_MONTHLY_USD", 0.0),
                                           ("MARKETING_WEEKLY_COST_WARN_USD", 1.0)])
def test_the_cost_settings_refuse_a_bad_value_at_boot(monkeypatch, name, default):
    """`Field(ge=0, le=10_000, allow_inf_nan=False)`: a bad Railway value fails the web deploy loudly (Railway
    keeps the old deployment) — never a cost line silently priced on nonsense."""
    from pydantic import ValidationError

    from app.config import Settings

    assert type(settings).model_fields[name].default == default
    for bad in ("-1", "-0.01", "nan", "inf", "-inf", "1e309", "10001", "10000.01", "1e300", "abc", ""):
        monkeypatch.setenv(name, bad)
        with pytest.raises(ValidationError) as e:
            Settings()
        assert [err["loc"] for err in e.value.errors()] == [(name,)], bad
    for ok, value in (("0", 0.0), ("16", 16.0), ("16.0", 16.0), ("1.5", 1.5), ("-0", 0.0), ("10000", 10_000.0)):
        monkeypatch.setenv(name, ok)
        assert getattr(Settings(), name) == value, ok


@pytest.mark.parametrize("usd, micros", [(0.0, 0), (-0.0, 0), (16.0, FEE_16), (16, FEE_16), (24.0, 24_000_000),
                                         (1.5, 1_500_000), (10_000, 10_000_000_000), (0.0000004, 0)])
def test_the_cost_settings_are_read_at_call_time_in_micro_dollars(monkeypatch, caplog, usd, micros):
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", usd)
    monkeypatch.setattr(settings, "MARKETING_WEEKLY_COST_WARN_USD", usd)
    assert (ds.upload_post_fee_micros(), ds.weekly_cost_warn_micros()) == (micros, micros)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("bad", ["16", True, -1, -0.01, float("nan"), float("inf"), 1e9, 10_000.01, None, [16]])
def test_an_unreadable_cost_setting_is_logged_and_never_guessed(monkeypatch, caplog, bad):
    """Settings refuses these at boot; only a monkeypatch reaches the readers with one. The fee is then
    UNREADABLE (never a guessed $0 Free plan); the alert line falls back to its declared $1.00. Both logged."""
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", bad)
    monkeypatch.setattr(settings, "MARKETING_WEEKLY_COST_WARN_USD", bad)
    assert ds.upload_post_fee_micros() is None
    assert ds.weekly_cost_warn_micros() == 1_000_000
    for name in ("MARKETING_UPLOAD_POST_MONTHLY_USD", "MARKETING_WEEKLY_COST_WARN_USD"):
        assert any(name in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records), name


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
    # The cost line's two settings are read at call time: pin them (the Free plan, the $1.00 alert line) so
    # a developer's .env can never move an expected cost line. A test that wants the $16 plan sets its own.
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", 0.0)
    monkeypatch.setattr(settings, "MARKETING_WEEKLY_COST_WARN_USD", 1.0)
    # The runway's calendar follows the content classes and the per-series switch, read at call time: pin
    # production's (lessons only; the switch's default) — a test that turns Company Weekly on sets its own.
    monkeypatch.setattr(settings, "MARKETING_CONTENT_CLASSES", "A")
    monkeypatch.setattr(settings, "MARKETING_NEWS_SERIES", _DEFAULT_NEWS_SERIES)
    from app.services.marketing import content_pool
    monkeypatch.setattr(content_pool, "eligible_keys", lambda: [f"journey:{i}" for i in range(10)])
    return svc


def _seed_week(svc) -> Dict[str, Any]:
    t = svc.fake.tables
    inside = _post("x", "published", external_id=X_ID, created="2026-10-03T20:30:00+00:00",
                   published_at="2026-10-03T21:00:00+00:00",
                   # read Mon 10-05 06:00 ET, 37 h after it went out
                   metrics={"v": 1, "status": "ok", "last": {"likes": 4, "impressions": 90, "age_h": 37.0},
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
    # `tokens_used` is NOT NULL DEFAULT 0 (migration 173): a seeded row carries it like a real one does
    t[mrs.SCRIPTS].rows += [{"run_id": str(uuid.uuid4()), "run_date": "2026-10-01", "source_ref": "journey:1",
                             "tokens_used": 21_000},
                            {"run_id": str(uuid.uuid4()), "run_date": "2026-10-03", "source_ref": "journey:2",
                             "tokens_used": 19_400},
                            {"run_id": str(uuid.uuid4()), "run_date": "2026-10-02", "source_ref": None,
                             "tokens_used": 0}]
    t[mrs.LINK_HITS].rows += [{"campaign": "bluesky", "day": "2026-10-03", "hits": 2},
                              {"campaign": "bluesky_early", "day": "2026-10-03", "hits": 4},   # the first minutes
                              {"campaign": "x", "day": "2026-09-27", "hits": 50},              # last week
                              {"campaign": "x_early", "day": "2026-09-27", "hits": 30}]        # last week
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

#: `_seed_week`'s cost block, by hand: X 15,000 (10-03) + 15,000 (09-30) — the Mon 10-05 read is this week's;
#: Gemini (21,000 + 19,400 + 0) tokens × 1.5 = 60,600; no billed run seconds (a peak alone bills nothing);
#: the Free plan (the fixture's pin) and no Upload-Post upload inside the week. The week before: nothing.
_SEED_WEEK_COST = ["💵 Cost last week ≈ $0.091: X $0.030 (our ledger) · Gemini ≈ $0.061 · worker ≈ $0.000 · "
                   "Upload-Post $0.000 (Free plan · 0 uploads)",
                   "Usage (X + Gemini + worker): last week ≈ $0.091 · week before ≈ $0.000"]


@pytest.mark.asyncio
async def test_gather_reads_only_our_ledger_for_the_previous_week(digest_ledger, platforms_forbidden):
    _seed_week(digest_ledger)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert (report["week_start"], report["week_end"]) == (WEEK_START, WEEK_END)
    # the week's posts only: the escalated row was created inside it too; last week's and today's are out
    assert report["live"] == 2 and report["status_totals"] == {"published": 1, "queued": 1}
    assert report["top_post"] == {"score": 4, "url": f"https://x.com/i/web/status/{X_ID}", "counts": {"likes": 4}}
    # the scorecard comes from the SAME 35-day read as the followers: both published posts are in its 4 weeks
    rows = {(r["platform"], r["format"]): r for r in report["scorecard"]["rows"]}
    assert set(rows) == {("x", "text"), ("bluesky", "text")}
    assert (rows[("x", "text")]["reach_median"], rows[("bluesky", "text")]["reads"]) == (90, {"unread": 1})
    assert report["scorecard"]["capped"] is False and report["image_fallback"] == []
    assert report["followers"]["x"]["followers"] == 12 and report["followers"]["bluesky"]["followers"] == 7
    # the early row is summed apart (and last week's early row is not this digest's)
    assert report["taps"]["rows"] == [("bluesky", 2)]
    assert (report["taps"]["total"], report["taps"]["early"]) == (2, 4)
    assert report["cost"]["last"]["unreadable"] == [] and report["cost"]["before"]["usage_unreadable"] == []
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
    assert text.splitlines()[2:4] == _SEED_WEEK_COST
    assert ("/go taps (approximate): bluesky 2 — 2 in total — plus 4" + _EARLY_CLAUSE) in text.splitlines()
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


# ── the runway against a real-shaped ledger (post-commit check of eba8ddcc) ──
# The digest read `len(pool) + RECENT_WINDOW` (94) refs of ALL kinds and counted every posting day; once
# Company Weekly stores `news:` refs on Mon/Tue/Thu, those rows pushed older lessons out of the read and
# most counted days were news days: after 39 lesson picks from a pool of 34 it still said "10 of 34 lessons
# unused". It now reads what selection reads (NEWS_RECENT_LIMIT refs, lesson refs only) and counts only the
# lesson-only days. Lessons only (production today) its text must not move by a byte.

#: The production pool's size at the check, as synthetic keys so a new bundled lesson cannot move a date.
_POOL_34 = [f"journey:{i}" for i in range(34)]
#: The day Company Weekly turns on in the mixed ledger: lessons every posting day before it (12 picks),
#: then news on Mon/Tue/Thu and the lesson on Saturday.
_SWITCH_DAY = date(2026, 10, 12)


def _ledger_rows(pool: List[str], last: date, *, news_from: Optional[date] = None) -> List[tuple]:
    """(run_date, source_ref) for every ET day from selection.EPOCH through `last`, picked the way production
    picks: a rest day stores NULL; from `news_from` on, a posting day whose chain (classes A,C,F, the default
    series) still holds a series stores a `news:` ref (its series found a candidate — no fall-back); every other
    posting day stores the lesson `script_service._select_lesson` picks — `selection.choose` over the lesson refs
    of the newest NEWS_RECENT_LIMIT refs before it. Causal: a longer run's prefix is a shorter run."""
    from app.services.marketing.script_service import NEWS_RECENT_LIMIT

    rows: List[tuple] = []
    picks: List[str] = []
    day = selection.EPOCH
    while day <= last:
        ref: Optional[str] = None
        if selection.is_posting_day(day):
            chain = selection.enabled_chain(selection.plan_for(day).chain, _NEWS_CLASSES, shipped=_NEWS_SERIES)
            if news_from is not None and day >= news_from and chain != (selection.LESSON,):
                ref = f"news:money_map:T{day.toordinal()}:2026"
            else:
                ref = selection.choose(pool, day, selection.lesson_refs(picks[::-1][:NEWS_RECENT_LIMIT])).source_ref
            picks.append(ref)
        rows.append((day, ref))
        day += timedelta(days=1)
    return rows


def _seed_ledger(svc, pool: List[str], today: date, *, made: bool,
                 news_from: Optional[date] = None) -> Optional[date]:
    """Seed the scripts through yesterday (through today when `made`) and return the date of the first pick
    from today on that repeats an earlier one — what selection really does, simulated past today."""
    rows = _ledger_rows(pool, today + timedelta(days=400), news_from=news_from)
    last = today if made else today - timedelta(days=1)
    svc.fake.tables[mrs.SCRIPTS].rows += [
        {"run_id": str(uuid.uuid4()), "run_date": d.isoformat(), "source_ref": ref, "tokens_used": 0}
        for d, ref in rows if d <= last]
    used = {ref for d, ref in rows if d <= last and ref}
    return next((d for d, ref in rows
                 if d > last and ref and selection.lesson_refs([ref]) and ref in used), None)


def _pool_line(text: str) -> str:
    (line,) = [ln for ln in text.splitlines() if ln.startswith(("Content pool", "⚠️ Content pool"))]
    return line


@pytest.mark.asyncio
@pytest.mark.parametrize("today, made, unused, first_repeat", [
    # the switch day, 12 lessons so far: it said Thu 2026-11-19 (22 of the posting days in between are news)
    (date(2026, 10, 12), False, 22, date(2027, 3, 20)),
    (date(2027, 2, 15), False, 4, date(2027, 3, 20)),     # it said Mon 2027-02-22 — a news day
    (date(2027, 2, 15), True, 4, date(2027, 3, 20)),      # Monday's news row is in: it used no lesson
    (date(2027, 2, 20), False, 4, date(2027, 3, 20)),     # a Saturday still to come counts itself …
    (date(2027, 2, 20), True, 3, date(2027, 3, 20)),      # … and once made, the count starts next week
    (date(2027, 4, 19), False, 0, date(2027, 4, 24)),     # 39 picks from 34: it said "10 of 34 lessons unused"
    (date(2027, 7, 19), False, 0, date(2027, 7, 24)),     # 52 picks: it still said 10 of 34
    (date(2028, 1, 17), True, 0, date(2028, 1, 22)),      # more rows than the 240 selection reads
])
async def test_the_runway_reads_a_mixed_news_and_lesson_ledger_as_selection_does(
        digest_ledger, monkeypatch, today, made, unused, first_repeat):
    from app.services.marketing import content_pool

    monkeypatch.setattr(settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(content_pool, "eligible_keys", lambda: list(_POOL_34))
    actual = _seed_ledger(digest_ledger, _POOL_34, today, made=made, news_from=_SWITCH_DAY)
    report = await ds.gather_digest(digest_ledger, datetime(today.year, today.month, today.day, 15,
                                                            tzinfo=timezone.utc))
    assert report["runway"] == {"pool": 34, "unused": unused, "first_repeat": first_repeat, "no_later_than": True}
    # the digest's date is the repeat selection really makes when no news day falls back to a lesson
    assert actual == first_repeat
    what = "lessons repeat" if unused == 0 else "the first repeat is due"
    assert _pool_line(ds.compose_digest(report)) == (
        f"Content pool: {unused} of 34 lessons unused — {what} no later than "
        f"{first_repeat.strftime('%a %Y-%m-%d')} (sooner when a news day falls back to a lesson)")


#: `compose_digest` of `_seed_week` at NOW as eba8ddcc (deployed, lessons only) printed it, byte for byte.
_SEED_WEEK_TEXT_AT_EBA8DDCC = "\n".join([
    "📊 Caydex marketing — weekly digest",
    "Mon 2026-09-28 → Sun 2026-10-04 (ET)",
    "💵 Cost last week ≈ $0.091: X $0.030 (our ledger) · Gemini ≈ $0.061 · worker ≈ $0.000 · "
    "Upload-Post $0.000 (Free plan · 0 uploads)",
    "Usage (X + Gemini + worker): last week ≈ $0.091 · week before ≈ $0.000",
    "",
    "Runs:",
    "• Mon 09-28 no run (a posting day)",
    "• Tue 09-29 no run (a posting day)",
    "• Thu 10-01 no run (a posting day)",
    "• Sat 10-03 media_ready",
    "Worker memory peak: 2,500 MB (voiced, Sat 10-03)",
    "",
    "Posts: 2 live — published 1 · queued 1",
    "• X: published 1 · queued 1",
    "Formats: text 2 (published 1 · queued 1)",
    "",
    "Engagement (latest snapshot of this week's published posts):",
    "• X — 1 of 1 measured: 90 impressions · 4 likes",
    f"  Top: https://x.com/i/web/status/{X_ID} (4 likes)",
    "Followers: BLUESKY 7 (as of 09-28; no snapshot 6+ days older yet) · "
    "X 12 (as of 10-05; no snapshot 6+ days older yet)",
    "",
    "/go taps (approximate): bluesky 2 — 2 in total — plus 4 in the first minutes after posting "
    "(mostly link scanners)",
    "⚠️ MARKETING_APP_STORE_URL is unset — every /go tap lands on the landing page, which has no App Store link.",
    "X spend 2026-10 (UTC, to date): $0.020 of the $2.00 cap (1%)",
    "  x_create $0.015 · x_metrics_read $0.005",
    "",
    "Review time: no decisions this week",
    "Content pool: 8 of 10 lessons unused — the first repeat is due Mon 2026-10-19",
    "⚠️ Waiting for your answer (outcome unknown): 1",
    "  X · run 10-03 · since 10-03 16:40 ET",
    "Metrics job: last completed 2026-10-05 · last attempt 10-05 06:00 ET · 2 posts written",
])

#: eba8ddcc's this-week engagement block, and the 4-week scorecard that replaced it (Batch 1 item 8b,
#: 2026-10-15) — the ONLY lines of the lessons-only digest that moved.
_EBA8DDCC_ENGAGEMENT = [
    "Engagement (latest snapshot of this week's published posts):",
    "• X — 1 of 1 measured: 90 impressions · 4 likes",
    f"  Top: https://x.com/i/web/status/{X_ID} (4 likes)",
]
_SEED_WEEK_SCORECARD = [
    SCORE_HEADER,
    "• X text: 1 post · followers 12 (change n/a) · impressions median 90, best 90 (1 at 24 h+)",
    "• BLUESKY text: 1 post · followers 7 (change n/a, as of 09-28) · no view counts · reads: 1 unread",
    f"Top post this week: https://x.com/i/web/status/{X_ID} (4 likes)",
]


def _swap_block(text: str, old: List[str], new: List[str]) -> str:
    lines = text.split("\n")
    i = lines.index(old[0])
    assert lines[i:i + len(old)] == old
    return "\n".join(lines[:i] + new + lines[i + len(old):])


#: `compose_digest` of `_seed_week` at NOW today: eba8ddcc's text with only the engagement block swapped.
_SEED_WEEK_TEXT = _swap_block(_SEED_WEEK_TEXT_AT_EBA8DDCC, _EBA8DDCC_ENGAGEMENT, _SEED_WEEK_SCORECARD)


@pytest.mark.asyncio
@pytest.mark.parametrize("classes", ["A", "", " a ,"])
async def test_lessons_only_the_digest_text_is_pinned_byte_for_byte(digest_ledger, monkeypatch, caplog, classes):
    """MARKETING_CONTENT_CLASSES unset in production (its default "A"; any spelling that parses to A): the whole
    message is pinned, and every line but the scorecard block is what the deployed digest (eba8ddcc) sent. The
    per-series switch is not even read (junk in it logs nothing), exactly as `script_service._select` leaves it
    alone while the classes are A."""
    monkeypatch.setattr(settings, "MARKETING_CONTENT_CLASSES", classes)
    monkeypatch.setattr(settings, "MARKETING_NEWS_SERIES", "junk,not_a_series")
    _seed_week(digest_ledger)
    with caplog.at_level(logging.DEBUG):
        text = ds.compose_digest(await ds.gather_digest(digest_ledger, NOW))
    assert text == _SEED_WEEK_TEXT
    assert _swap_block(text, _SEED_WEEK_SCORECARD, _EBA8DDCC_ENGAGEMENT) == _SEED_WEEK_TEXT_AT_EBA8DDCC
    assert not [r for r in caplog.records if "MARKETING_NEWS_SERIES" in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("today, made, runway, line", [
    # values eba8ddcc printed for the same lesson-only ledgers (the production pool's size, picks since EPOCH)
    (date(2026, 10, 12), False, {"pool": 34, "unused": 22, "first_repeat": date(2026, 11, 19)},
     "Content pool: 22 of 34 lessons unused — the first repeat is due Thu 2026-11-19"),
    (date(2026, 10, 17), True, {"pool": 34, "unused": 18, "first_repeat": date(2026, 11, 19)},
     "Content pool: 18 of 34 lessons unused — the first repeat is due Thu 2026-11-19"),
    (date(2027, 4, 19), False, {"pool": 34, "unused": 0, "first_repeat": date(2027, 4, 19)},
     "Content pool: 0 of 34 lessons unused — lessons repeat from Mon 2027-04-19"),
    # more rows than both the old read (94) and selection's (240)
    (date(2027, 11, 22), False, {"pool": 34, "unused": 0, "first_repeat": date(2027, 11, 22)},
     "Content pool: 0 of 34 lessons unused — lessons repeat from Mon 2027-11-22"),
    (date(2027, 11, 20), True, {"pool": 34, "unused": 0, "first_repeat": date(2027, 11, 22)},
     "Content pool: 0 of 34 lessons unused — lessons repeat from Mon 2027-11-22"),
])
async def test_lessons_only_the_runway_of_a_long_ledger_is_byte_identical_to_eba8ddcc(
        digest_ledger, monkeypatch, today, made, runway, line):
    from app.services.marketing import content_pool

    monkeypatch.setattr(content_pool, "eligible_keys", lambda: list(_POOL_34))
    actual = _seed_ledger(digest_ledger, _POOL_34, today, made=made)
    report = await ds.gather_digest(digest_ledger, datetime(today.year, today.month, today.day, 15,
                                                            tzinfo=timezone.utc))
    assert report["runway"] == runway
    assert _pool_line(ds.compose_digest(report)) == line
    # and it is the truth: selection's own first repeat from today
    assert actual == runway["first_repeat"]


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
async def test_the_digest_reads_past_one_page_so_the_newest_week_is_never_the_one_missing(digest_ledger):
    """MD-7 (2026-10-16): the 35-day read answered OLDEST first under one LIMIT of 300 — once the window held
    more (Batch 3: ~11 posts a day, every day), the scorecard lost its NEWEST posts and the follower line its
    newest snapshot. Read in pages, both are there, and nothing is called capped."""
    rows = digest_ledger.fake.tables[mrs.POSTS].rows
    start = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(330):
        rows.append(_scored("x", "text", (start + timedelta(minutes=90 * i)).isoformat(),
                            last=_snap(48.0, impressions=1)))
    rows.append(_scored("bluesky", "image", "2026-10-03T20:30:00+00:00", last=_snap(34.0, likes=1),
                        account=_acct(77, "2026-10-05T10:00:00+00:00")))         # the NEWEST published post
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["followers"]["bluesky"]["followers"] == 77
    assert [r["platform"] for r in report["scorecard"]["rows"]].count("bluesky") == 1
    assert report["scorecard"]["capped"] is False and report["scorecard"]["read"] == 331
    assert "read stopped after" not in ds.compose_digest(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("stuck", [False, True], ids=["past_its_pages", "a_full_page_at_one_instant"])
async def test_a_capped_digest_read_names_the_count_it_really_read(digest_ledger, monkeypatch, stuck):
    """The capped line names how many posts the read took in, never the page bound: past its pages that is
    5 + 4 (the page boundary's row is read twice and kept once), and a read stuck on a full page of posts at
    ONE instant stops at its first page — "the first 1,200" would be false both ways."""
    monkeypatch.setattr(ds, "MEASURED_POST_LIMIT", 5)
    monkeypatch.setattr(ds, "MEASURED_POST_PAGES", 2)
    rows = digest_ledger.fake.tables[mrs.POSTS].rows
    start = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    for i in range(12):
        at = start if stuck else start + timedelta(hours=i)
        rows.append(_scored("x", "text", at.isoformat(), last=_snap(48.0, impressions=1)))
    report = await ds.gather_digest(digest_ledger, NOW)
    read = 5 if stuck else 9
    assert report["scorecard"]["capped"] is True and report["scorecard"]["read"] == read
    assert (f"  (the published-posts read stopped after {read} posts — the newest may be missing)"
            in ds.compose_digest(report).splitlines())


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


# ── gather: the cost line's three reads ───────────────────────────────────────


def _seed_costs(svc) -> Dict[str, Any]:
    """The worked example (`_cost_inputs`) in the ledger, plus a row each cost read must LEAVE OUT: an X post
    last touched one second before the week before began, yet carrying an in-week entry (impossible in
    production, where every charge bumps `updated_at` — it proves the journal read is bounded by the touch),
    another platform's journal, and runs and scripts (with junk values) of today and of 09-20."""
    t = svc.fake.tables
    kw = _cost_inputs()
    stale = _x_post("2026-09-19", _charge("2026-09-30T12:00:00+00:00", "x_create", 700_000),
                    updated_at="2026-09-21T03:59:59+00:00")
    other = _post("bluesky", "published", run_date="2026-09-30",
                  meta={"charges": [_charge("2026-09-30T12:00:00+00:00", "x_create", 800_000)]},
                  updated_at="2026-09-30T12:00:00+00:00")
    t[mrs.POSTS].rows += kw["charge_rows"] + [stale, other] + kw["posts"]
    t[mrs.RUNS].rows += kw["runs"] + kw["prev_runs"] + [
        _run("published", run_date=d, timings={"rendered_s": "junk"}) for d in ("2026-09-20", "2026-10-05")]
    t[mrs.SCRIPTS].rows += [{"run_id": str(uuid.uuid4()), "source_ref": None, **r} for r in kw["script_rows"]]
    t[mrs.SCRIPTS].rows += [{"run_id": str(uuid.uuid4()), "run_date": d, "source_ref": None, "tokens_used": "junk"}
                            for d in ("2026-09-20", "2026-10-05")]
    return {"stale": stale, "other": other}


#: The week's live posts `_seed_costs` leaves: four X posts, the other platform's one, three submissions.
_SEED_COSTS_LIVE = 8


@pytest.mark.asyncio
async def test_gather_prices_last_week_and_the_week_before_from_our_ledger(digest_ledger, platforms_forbidden,
                                                                           monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", 16.0)
    _seed_costs(digest_ledger)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert {k: report["cost"]["last"][k] for k in _LAST_KEYS} == EXPECTED_LAST
    assert {k: report["cost"]["before"][k] for k in _BEFORE_KEYS} == EXPECTED_BEFORE
    assert report["cost"]["warn"] == 1_000_000 and report["live"] == _SEED_COSTS_LIVE
    lines = ds.compose_digest(report).splitlines()
    assert lines[1:5] == ["Mon 2026-09-28 → Sun 2026-10-04 (ET)", COST_A, USAGE_A, ""]
    assert platforms_forbidden == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method, part, name, expected", [
    ("list_charge_rows_since", "x", "X", COST_X_UNREADABLE),
    ("script_tokens_between", "gemini", "Gemini", COST_GEMINI_UNREADABLE),
    ("list_runs_between", "worker", "worker", COST_WORKER_UNREADABLE),   # both weeks' runs: one method
])
async def test_a_failed_cost_read_degrades_only_its_part(digest_ledger, monkeypatch, caplog, method, part, name,
                                                         expected):
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", 16.0)
    _seed_costs(digest_ledger)
    monkeypatch.setattr(digest_ledger, method, _Exploding(mrs.MarketingRunError(f"{method} 503")))
    report = await ds.gather_digest(digest_ledger, NOW)
    for week, readable in (("last", COST_LAST), ("before", COST_BEFORE)):
        got = report["cost"][week]
        assert got[part] is None and got["usage_unreadable"] == [name] and got["usage_partial"] is True
        assert {k: got[k] for k in readable if k != part} == {k: v for k, v in readable.items() if k != part}
    assert report["cost"]["last"]["unreadable"] == [name] and report["cost"]["last"]["upload_post"] == FEE_16_WEEK
    lines = ds.compose_digest(report).splitlines()
    assert lines[2:4] == expected
    assert report["live"] == _SEED_COSTS_LIVE                     # the rest of the digest is intact
    assert ("Runs: unreadable (see the web logs)" in lines) is (method == "list_runs_between")
    assert any(f"{method} 503" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_a_capped_x_journal_read_is_unreadable_never_a_partial_sum(digest_ledger, monkeypatch, caplog):
    """The journal read asks for one row more than its limit and RAISES when it gets it: a sum over the first
    N rows would be a wrong number that looks right."""
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", 16.0)
    _seed_costs(digest_ledger)                     # 7 X posts touched since the week before began
    monkeypatch.setattr(ds, "X_CHARGE_ROW_LIMIT", 7)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert (report["cost"]["last"]["x"], report["cost"]["before"]["x"]) == (61_000, 55_000)   # at the limit: whole
    for limit in (6, 1):
        caplog.clear()
        monkeypatch.setattr(ds, "X_CHARGE_ROW_LIMIT", limit)
        report = await ds.gather_digest(digest_ledger, NOW)
        assert (report["cost"]["last"]["x"], report["cost"]["before"]["x"]) == (None, None), limit
        assert ds.compose_digest(report).splitlines()[2:4] == COST_X_UNREADABLE
        assert report["live"] == _SEED_COSTS_LIVE and report["spend"] is not None   # the month's line reads apart
        assert any("partial" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records), limit


@pytest.mark.asyncio
async def test_gather_prices_the_november_week_on_the_new_york_clock(digest_ledger, monkeypatch):
    """(f) Mon 2026-11-02: last week is the 169-hour [10-26T04:00Z, 11-02T05:00Z) — its extra EST hour
    included — and the week before is [10-19T04:00Z, 10-26T04:00Z); runs and scripts go by their ET day.
    The plan fee is still 7/30.4375 of a month, never 169 hours' worth. Preflight-only runs of 10.0 s at the
    4,096 MB fallback are 463 each; X / Gemini by hand: last 20,000 + 250,000 and 4,000 tokens, the week
    before 1,000 and 1,000 tokens."""
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", 16.0)
    t = digest_ledger.fake.tables
    t[mrs.POSTS].rows += [
        _x_post("2026-10-17",
                _charge("2026-10-19T03:59:59+00:00", "x_create", 30_000),        # before the week before
                _charge("2026-10-26T03:59:59+00:00", "x_metrics_read", 1_000),   # Sun 10-25 23:59:59 EDT
                _charge("2026-10-26T04:00:00+00:00", "x_metrics_read", 20_000),  # Mon 10-26 00:00 EDT
                updated_at="2026-10-26T04:00:00+00:00"),
        _x_post("2026-10-31",
                _charge("2026-11-02T04:59:59+00:00", "x_create", 250_000),       # Sun 11-01 23:59:59 EST
                _charge("2026-11-02T05:00:00+00:00", "x_metrics_read", 4_000),   # Mon 11-02 00:00 EST: this week
                updated_at="2026-11-02T05:00:00+00:00")]
    for day, tokens in (("2026-10-25", 1_000), ("2026-10-26", 2_000), ("2026-11-01", 2_000), ("2026-11-02", 7_000)):
        t[mrs.RUNS].rows.append(_run("published", run_date=day, timings={"preflight_s": 10.0}))
        t[mrs.SCRIPTS].rows.append({"run_id": str(uuid.uuid4()), "run_date": day, "source_ref": None,
                                    "tokens_used": tokens})
    report = await ds.gather_digest(digest_ledger, datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc))
    last, before = report["cost"]["last"], report["cost"]["before"]
    assert (last["x"], last["gemini"], last["worker"], last["upload_post"]) == (270_000, 6_000, 926, FEE_16_WEEK)
    assert (before["x"], before["gemini"], before["worker"], before["usage"]) == (1_000, 1_500, 463, 2_963)
    assert ds.compose_digest(report).splitlines()[1:4] == [
        "Mon 2026-10-26 → Sun 2026-11-01 (ET)",
        "💵 Cost last week ≈ $3.957: X $0.270 (our ledger) · Gemini ≈ $0.006 · worker ≈ $0.001 · "
        "Upload-Post $3.680 (plan fee · 0 uploads)",
        "Usage (X + Gemini + worker): last week ≈ $0.277 · week before ≈ $0.003"]


@pytest.mark.asyncio
async def test_an_unreadable_cost_setting_degrades_only_its_part_of_the_digest(digest_ledger, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    _seed_costs(digest_ledger)
    monkeypatch.setattr(settings, "MARKETING_UPLOAD_POST_MONTHLY_USD", "16")     # only a monkeypatch can
    monkeypatch.setattr(settings, "MARKETING_WEEKLY_COST_WARN_USD", float("nan"))
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["cost"]["warn"] == 1_000_000                                # the declared $1.00
    assert ds.compose_digest(report).splitlines()[2:5] == [
        "💵 Cost last week ≥ $0.235 (Upload-Post unreadable): X $0.061 (our ledger) · Gemini ≈ $0.124 · "
        "worker ≈ $0.050 · Upload-Post unreadable",
        USAGE_A, ""]
    # the alert line is read at call time too
    monkeypatch.setattr(settings, "MARKETING_WEEKLY_COST_WARN_USD", 0.2)
    lines = ds.compose_digest(await ds.gather_digest(digest_ledger, NOW)).splitlines()
    assert lines[4] == "⚠️ Usage ≈ $0.235 passed your $0.20 alert line"


@pytest.mark.asyncio
async def test_an_absurd_x_budget_never_takes_the_digest_down(digest_ledger, monkeypatch):
    """`outlet_x.budget_micros()` is called OUTSIDE `_optional`: a budget whose micro-dollars overflow to inf
    used to raise there and fail every attempt of the digest. X now reads as off (fail-closed); the rest
    renders."""
    _seed_week(digest_ledger)
    monkeypatch.setattr(settings, "MARKETING_X_MONTHLY_BUDGET_USD", 1e303)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["spend"]["budget"] == 0
    lines = ds.compose_digest(report).splitlines()
    assert ("X spend 2026-10 (UTC, to date): $0.020 — X is OFF: no posts or reads "
            "(MARKETING_X_MONTHLY_BUDGET_USD is 0 or unset)") in lines
    assert lines[2:4] == _SEED_WEEK_COST


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
    # B3PD-2: the in-process memo of the owed-final-word look (`_final_owed`) is per test too.
    monkeypatch.setattr(ds, "_FINAL_OWED", {})
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
    # the cost block rides right under the date line of the message the cycle SENDS (its three reads wired
    # through the real cycle), and the first minutes after posting are counted apart there too
    assert msg["text"].splitlines()[2:4] == _SEED_WEEK_COST
    assert ("/go taps (approximate): bluesky 2 — 2 in total — plus 4" + _EARLY_CLAUSE) in msg["text"].splitlines()
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
    assert "Scorecard" in text
    assert "Posts: 1 live — published 1" in text and "rejected" not in text.split("Scorecard")[0]
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
    finishes the run; Sunday 16:00 says it recovered and its posts went to the review chat — never that they
    still wait: the owner may have decided them overnight (MD-1)."""
    run = _seed_health_run(digest_ledger, _run("in_progress", run_date="2026-10-03", stage="voiced", attempts=4,
                                        updated_at=_iso_of(_et(2026, 10, 3, 21, 0))))
    await _health_at(digest_jobs, _et(2026, 10, 3, 22, 0))
    assert "attempt 4 of 6 was abandoned; the worker retries hourly" in _sends(bot)[0]["text"]
    run.update(status="media_ready", stage="assets_ready", attempts=5, finished_at=_iso_of(_et(2026, 10, 3, 23, 41)),
               updated_at=_iso_of(_et(2026, 10, 3, 23, 41)))
    await _health_at(digest_jobs, _et(2026, 10, 4, 16, 0))
    assert _sends(bot)[1]["text"] == f"✅ Marketing run Sat 2026-10-03 recovered at 10-03 23:41 ET — {RECOVERED_POSTS}."
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
    assert _sends(bot)[3]["text"] == f"✅ Marketing run Mon 2026-10-05 recovered at 10-05 22:41 ET — {RECOVERED_POSTS}."
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



# ── drop 1 (contract C12): each post's format in the digest ───────────────────


def test_the_digest_shows_each_posts_format():
    posts = [
        _post("tiktok", "published", format="video"),
        _post("bluesky", "published", format="image"),
        _post("x", "published"),                                                     # text
        _post("threads", "rejected", run_date="2026-10-01", format="image",
              meta={"review": {"decision": "rejected", "reason": "tone"}}),
        _post("linkedin", "failed", run_date="2026-09-29", format="image", last_error="refused",
              meta={"publish": {"category": "forbidden"}}),
        _post("x", "failed", run_date="2026-09-29", last_error=None),                # text
        # a format outside the ledger's is "unknown", never echoed
        _post("facebook", "rejected", run_date="2026-10-01", format="<b>gif</b>",
              meta={"review": {"decision": "rejected"}}),
        _post("youtube", "published", format="video", live=False),                    # rehearsal: not counted
    ]
    s = _summary(posts)
    assert s["by_format"] == {"video": {"published": 1}, "image": {"published": 1, "rejected": 1, "failed": 1},
                              "text": {"published": 1, "failed": 1}, "unknown": {"rejected": 1}}
    text = ds.compose_digest(s)
    assert ("Formats: video 1 (published 1) · image 3 (published 1 · rejected 1 · failed 1) · "
            "text 2 (published 1 · failed 1) · unknown 1 (rejected 1)") in text.splitlines()
    assert "  ❌ THREADS · run 10-01 · Tone · image" in text.splitlines()
    assert "  ❌ FACEBOOK · run 10-01 · no reason · unknown" in text.splitlines()
    assert "  ✖ LINKEDIN · run 09-29 · forbidden: refused · image" in text.splitlines()
    assert "  ✖ X · run 09-29 · no error recorded · text" in text.splitlines()
    assert "<b>" not in text and "gif" not in text
    _assert_no_model_text(text)
    # the Formats line sits under the per-platform counts, above Expired / Rejected / Failed
    lines = text.splitlines()
    assert lines.index(next(line for line in lines if line.startswith("Formats: "))) \
        < lines.index(next(line for line in lines if line.startswith("Rejected ")))


def test_a_week_without_live_posts_has_no_formats_line():
    text = _digest([_post("x", "published", live=False)])
    assert "Formats:" not in text and "Posts: none this week" in text


# ── Batch 1 item 11 (2026-10-15): the days whose picture was not used ─────────


def test_the_digest_names_the_days_whose_picture_was_not_used_in_the_owners_words():
    posts = [
        _post("facebook", "published", run_date="2026-10-01", meta={"image_fallback": "image_dropped"}),
        _post("linkedin", "rejected", run_date="2026-10-01", meta={"image_fallback": "image_dropped"}),
        _post("threads", "published", run_date="2026-10-01", meta={"image_fallback": "threads_caption_long"}),
        _post("bluesky", "published", run_date="2026-09-28", meta={"image_fallback": "worker_capability"}),
        # a code this digest does not know, and one that is not a string: fixed words, never the value
        _post("facebook", "published", run_date="2026-10-03", meta={"image_fallback": "<b>new_code</b>\nforged"}),
        _post("linkedin", "published", run_date="2026-10-03", meta={"image_fallback": {"code": "image_dropped"}}),
        # no fallback at all
        _post("threads", "published", run_date="2026-10-03", meta={"image_fallback": ""}),
        _post("bluesky", "published", run_date="2026-10-03", meta={"image_fallback": None}),
        _post("x", "published", run_date="2026-10-03", meta={"image_fallback": False}),
        _post("x", "published", run_date="2026-10-03"),
        # a rehearsal row never counts
        _post("facebook", "published", run_date="2026-10-02", live=False, meta={"image_fallback": "freeze_failed"}),
    ]
    s = _summary(posts)
    unknown = "a reason this digest does not know"
    assert s["image_fallback"] == [
        (date(2026, 9, 28), [("the worker that ran the day cannot draw this picture", 1)]),
        (date(2026, 10, 1), [("the picture text did not pass the checks", 2),
                             ("the Threads caption is too long for a picture post", 1)]),
        (date(2026, 10, 3), [(unknown, 2)]),
    ]
    text = ds.compose_digest(s)
    lines = text.splitlines()
    # "text posts instead", never "posted as text": the LinkedIn member of 10-01 was rejected, not posted
    i = lines.index("🖼 Picture not used on 3 days (text posts instead):")
    assert lines[i + 1:i + 4] == [
        "  Mon 09-28: the worker that ran the day cannot draw this picture (1 post)",
        "  Thu 10-01: the picture text did not pass the checks (2 posts); the Threads caption is too long for a "
        "picture post (1 post)",
        f"  Sat 10-03: {unknown} (2 posts)",
    ]
    # it closes the posts section, above the scorecard
    assert i < lines.index("Scorecard, 4 weeks (09-07 → 10-04): no published posts")
    for raw in ("<b>", "new_code", "forged", "image_fallback", "the picture could not be prepared", "Fri 10-02"):
        assert raw not in text
    _assert_no_model_text(text)


@pytest.mark.parametrize("code", list(IMAGE_FALLBACK_REASONS))
def test_every_picture_fallback_code_reads_in_its_own_words(code):
    text = _digest([_post("facebook", "published", run_date="2026-10-01", meta={"image_fallback": code})])
    assert f"  Thu 10-01: {IMAGE_FALLBACK_REASONS[code]} (1 post)" in text.splitlines()
    assert code not in text


def test_a_picture_fallback_without_a_readable_run_day_is_still_named_and_none_says_nothing():
    post = _post("threads", "published", meta={"image_fallback": "image_spec_invalid"}, idempotency_key="garbage")
    lines = _digest([post]).splitlines()
    assert lines[lines.index("🖼 Picture not used on 1 day (text posts instead):") + 1] == (
        "  an unreadable day: the picture layout did not validate (1 post)")
    assert "Picture not used" not in _digest(_full_week())
    assert ds.POST_METADATA_IMAGE_FALLBACK == "image_fallback"


# ══ Drop 2 (contract D13): the template refusal's hint, the series note ═════════════════════════════


def test_the_template_refusal_says_where_to_look():
    msg = _health(_run("skipped", stage="assets_ready", metadata={"skip_reason": "template_refused"}))
    assert msg is not None and "SKIPPED (template_refused)" in msg
    assert "create_posts TEMPLATE REFUSED" in msg and "MARKETING_CONTENT_CLASSES" in msg


def _template_refused_switches() -> List[str]:
    """Every `MARKETING_*` setting a `raise MarketingTemplateRefused(…)` in run_service names in its
    message (AST: the raise's own string constants — never a comment, never another exception)."""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "services" / "marketing" / "run_service.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    names: List[str] = []
    raises = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", None) == "MarketingTemplateRefused"):
            continue
        raises += 1
        for sub in ast.walk(node.exc):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                names.extend(re.findall(r"MARKETING_[A-Z_]+", sub.value))
    assert raises >= 3, raises            # the class switch, the series switch, the re-check
    return sorted(set(names))


def test_the_template_refusal_hint_names_every_switch_create_posts_refuses_on():
    """Review R9 (low): the per-series switch (drop 2b) also answers `template_refused`; the owner's alert
    must name MARKETING_NEWS_SERIES too, not only the class switch — or it points at the wrong setting."""
    switches = _template_refused_switches()
    assert {"MARKETING_CONTENT_CLASSES", "MARKETING_NEWS_SERIES"} <= set(switches), switches
    msg = _health(_run("skipped", stage="assets_ready", metadata={"skip_reason": "template_refused"}))
    assert msg is not None
    missing = [s for s in switches if s not in msg]
    assert not missing, (missing, msg)


def _worker_rejection_skip_reasons() -> Dict[str, str]:
    """`REJECTION_SKIP_REASONS` of marketing/main.py, read by AST (the worker is never imported here)."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "marketing" / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == "REJECTION_SKIP_REASONS":
            return ast.literal_eval(node.value)
    raise AssertionError("marketing/main.py defines no REJECTION_SKIP_REASONS")


def test_every_rejection_skip_reason_the_worker_records_has_a_hint():
    """A `rejected` kick body becomes a skip reason through the worker's REJECTION_SKIP_REASONS (not a
    `SkipRun("…")` literal, so the scan above cannot see them): each value needs a digest hint too, or the
    owner's alert reads "an unrecognised reason"."""
    reasons = _worker_rejection_skip_reasons()
    assert {"content_rejected", "writer_unavailable", "empty_pool", "source_ineligible"} <= set(reasons.values())
    missing = sorted(set(reasons.values()) - {"rest_day"} - set(ds._SKIP_HINTS))
    assert not missing, f"rejection skip reasons with no digest hint: {missing}"


def _with_series(series: Any, trail: Any) -> Dict[str, Any]:
    return _run("published", metadata={"series": series, "series_trail": trail})


@pytest.mark.parametrize("series, trail, note", [
    ("money_map", [{"series": "money_map", "outcome": "chosen"}], "Money Map"),
    # OBS-1 (2026-10-15): the later series' outage is named too — it used to hide behind the first step
    ("money_map", [{"series": "ceo_buys", "outcome": "no_candidates", "reason": "ceo_none_qualified"},
                   {"series": "insider_buys", "outcome": "unavailable", "reason": "insider_feed_unavailable"},
                   {"series": "money_map", "outcome": "chosen"}],
     "Money Map (fell back from CEO Buys: nothing qualified; Insider Buys: its data source was unavailable)"),
    ("lesson", [{"series": "thirteen_f", "outcome": "timeout"}, {"series": "lesson", "outcome": "chosen"}],
     "Lesson (fell back from 13F Season: its data source timed out)"),
    ("ceo_buys", [], "CEO Buys"),
    ("ceo_buys", None, "CEO Buys"),
    ("thirteen_f", [{"series": "ceo_buys", "outcome": "budget"}, {"series": "thirteen_f", "outcome": "chosen"}],
     "13F Season (fell back from CEO Buys: out of time)"),
    ("money_map", [{"series": "ceo_buys", "outcome": "all_refused", "reason": "x"}],
     "Money Map (fell back from CEO Buys: every candidate failed the template checks)"),
    ("money_map", [{"series": "ceo_buys", "outcome": "brand_new_outcome"}],
     "Money Map (fell back from CEO Buys: brand_new_outcome)"),
    ("money_map", [{"series": "ceo_buys", "outcome": "<b>bold\nline</b>"}],
     "Money Map (fell back from CEO Buys: it did not post)"),
    # an unknown first step is skipped, never shown: only operator labels reach the text
    ("money_map", [{"series": "<script>", "outcome": "error"}, "junk", {"series": "insider_buys", "outcome": "error"}],
     "Money Map (fell back from Insider Buys: an error)"),
    ("not_a_series", [{"series": "ceo_buys", "outcome": "error"}], ""),
    (None, None, ""),
    (["money_map"], None, ""),
])
def test_the_series_note_names_only_known_series_and_fixed_words(series, trail, note):
    assert ds._series_note(_with_series(series, trail)) == note


@pytest.mark.parametrize("run", [None, "x", {}, {"metadata": None}, {"metadata": "x"}])
def test_the_series_note_of_a_run_without_metadata_is_empty(run):
    assert ds._series_note(run) == ""


_TUESDAY_13F = ("congress_count", "thirteen_f", "company_stakes", "money_map")


@pytest.mark.parametrize("outcomes, note", [
    # four steps, one outage at the END of the chain: it is named, the last content step is counted
    (("no_candidates", "all_recent", "no_candidates", "timeout"),
     "Lesson (fell back from Congress Count: nothing qualified; 13F Season: everything was already posted; "
     "Money Map: its data source timed out; and 1 more)"),
    # every infrastructure outcome outranks every content one
    (("no_candidates", "budget", "all_refused", "error"),
     "Lesson (fell back from Congress Count: nothing qualified; 13F Season: out of time; Money Map: an error; "
     "and 1 more)"),
    # four outages: the first three in trail order
    (("unavailable", "timeout", "error", "budget"),
     "Lesson (fell back from Congress Count: its data source was unavailable; 13F Season: its data source timed "
     "out; Big Company Bets: an error; and 1 more)"),
    # content only: the first three
    (("no_candidates", "all_recent", "all_refused", "no_candidates"),
     "Lesson (fell back from Congress Count: nothing qualified; 13F Season: everything was already posted; "
     "Big Company Bets: every candidate failed the template checks; and 1 more)"),
])
def test_the_series_note_names_an_infrastructure_failure_ahead_of_content(outcomes, note):
    trail = [{"series": s, "outcome": o} for s, o in zip(_TUESDAY_13F, outcomes)]
    assert ds._series_note(_with_series("lesson", trail + [{"series": "lesson", "outcome": "chosen"}])) == note


def test_the_series_note_survives_hand_edited_trails():
    def note(trail: Any) -> str:
        return ds._series_note(_with_series("money_map", trail))

    # a series twice is named once (its first entry); exactly three steps need no "more"
    assert note([{"series": "ceo_buys", "outcome": "no_candidates"}, {"series": "ceo_buys", "outcome": "error"}]) == \
        "Money Map (fell back from CEO Buys: nothing qualified)"
    three = [{"series": s, "outcome": "timeout"} for s in ("ceo_buys", "insider_buys", "thirteen_f")]
    assert note(three).count(": its data source timed out") == 3 and "more" not in note(three)
    # an unhashable or odd outcome never raises and never shows: fixed words
    for odd in (["error"], {"error": 1}, None, 7, True, "<b>x</b>"):
        assert note([{"series": "ceo_buys", "outcome": odd}]) == "Money Map (fell back from CEO Buys: it did not post)"
    # every outcome selection can write has owner words, and the infrastructure set is inside it
    from app.services.marketing.script_service import SERIES_OUTCOMES

    assert set(SERIES_OUTCOMES) - {"chosen"} == set(ds._SERIES_OUTCOME_WORDS)
    assert ds._INFRA_OUTCOMES <= set(SERIES_OUTCOMES)


def test_the_digest_day_line_carries_the_series_note():
    mon, tue = date(2026, 11, 16), date(2026, 11, 17)
    runs = [
        _run("published", run_date=mon.isoformat(), metadata={
            "series": "money_map", "series_trail": [{"series": "ceo_buys", "outcome": "no_candidates"},
                                                    {"series": "money_map", "outcome": "chosen"}]}),
        _run("skipped", run_date=tue.isoformat(), metadata={"skip_reason": "template_refused", "series": "thirteen_f",
                                                            "series_trail": [{"series": "thirteen_f",
                                                                              "outcome": "chosen"}]}),
    ]
    rows, _peak = ds._run_rows(runs, mon, tue)
    assert rows == [(mon, "published · Money Map (fell back from CEO Buys: nothing qualified)"),
                    (tue, "skipped — template_refused · 13F Season")]
    # a run from before drop 2 (no series) keeps exactly its old line
    rows, _peak = ds._run_rows([_run("published", run_date=mon.isoformat())], mon, mon)
    assert rows == [(mon, "published")]


# ── Batch 3 (2026-10-20, WP-C): the posting days are a switch ────────────────
#
# MARKETING_POST_WEEKDAYS (default mon,tue,thu,sat; pinned above by `_declared_post_weekdays`) is read at CALL time by
# `selection.is_posting_day` / `plan_for` / `news_recent_limit`, so the run-health timing, the digest's Runs rows and
# the pool's runway follow it with no code of their own — and stay today's at the default.

_SEVEN_DAYS = "mon,tue,wed,thu,fri,sat,sun"
WED, FRI_10_09, SUN = date(2026, 10, 7), date(2026, 10, 9), date(2026, 10, 11)


def _cadence(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(settings, "MARKETING_POST_WEEKDAYS", raw)


@pytest.mark.parametrize("day", [WED, FRI_10_09, SUN], ids=["wed", "fri", "sun"])
def test_batch3_run_health_on_new_posting_days(monkeypatch, day):
    """At seven days a Wednesday, Friday or Sunday is a posting day for run health too: its nightly check is due from
    22:00 ET that day and its final word from 16:00 ET the next — at the default cadence neither is (rest days), and
    switching back restores that."""
    nxt = day + timedelta(days=1)
    for cadence, due in (("mon,tue,thu,sat", False), (_SEVEN_DAYS, True), ("mon,tue,thu,sat", False)):
        _cadence(monkeypatch, cadence)
        assert ds.health_due(_et(day.year, day.month, day.day, 21, 59)) is False
        assert ds.health_due(_et(day.year, day.month, day.day, 22, 0)) is due
        assert ds.health_due(_et(day.year, day.month, day.day, 23, 59)) is due
        assert ds.final_due(_et(nxt.year, nxt.month, nxt.day, 15, 59)) is False
        assert ds.final_due(_et(nxt.year, nxt.month, nxt.day, 16, 0)) is due
        assert ds.health_opens_at(day) == datetime(day.year, day.month, day.day, 22, tzinfo=ET)


@pytest.mark.asyncio
@pytest.mark.parametrize("cadence, jobs", [("mon,tue,thu,sat", [ds.JOB_HEALTH_FINAL]),
                                           (_SEVEN_DAYS, [ds.JOB_HEALTH_FINAL, ds.JOB_HEALTH])],
                         ids=["default", "seven_days"])
async def test_batch3_the_nightly_check_follows_the_posting_days(bot, digest_jobs, digest_ledger, monkeypatch,
                                                                cadence, jobs):
    """Through the real `health_cycle` at Wed 22:30 ET with no run: the default claims only Tuesday's final word (a
    rest day never gets a nightly check); at seven days Wednesday's own check is claimed too and says "no run"."""
    _cadence(monkeypatch, cadence)
    await _health_at(digest_jobs, _et(2026, 10, 7, 22, 30))
    assert digest_jobs.claims == [(job, WED) for job in jobs]
    nightly = [m["text"] for m in _sends(bot) if m["text"].startswith("⚠️ Marketing: no run for Wed 2026-10-07, a posting day")]
    assert len(nightly) == (ds.JOB_HEALTH in jobs)


def test_batch3_a_rest_day_skip_reads_as_rest_after_a_flip(monkeypatch):
    """D18: a run skipped `rest_day` is a rest day in the Runs rows whatever TODAY's calendar says. The owner moved to
    seven days on Thursday morning: Monday-Wednesday ran on the old calendar (Wednesday skipped as a rest day), Thursday
    on the new one; the digest reads the week on today's seven days — no "skipped — rest_day" row, while a posting day
    with no run at all still says so and any other skip is still named. Under an unchanged calendar the rows are
    exactly today's."""
    week = [date(2026, 10, 5) + timedelta(days=i) for i in range(7)]
    rest = {"skip_reason": "rest_day"}
    runs = [_run("published", run_date=week[0].isoformat()), _run("published", run_date=week[1].isoformat()),
            _run("skipped", run_date=week[2].isoformat(), metadata=rest),
            _run("published", run_date=week[3].isoformat()),
            _run("skipped", run_date=week[5].isoformat(), metadata={"skip_reason": "template_refused"})]
    _cadence(monkeypatch, _SEVEN_DAYS)
    rows, _peak = ds._run_rows(runs, week[0], week[-1])
    assert rows == [(week[0], "published"), (week[1], "published"), (week[3], "published"),
                    (week[4], "no run (a posting day)"), (week[5], "skipped — template_refused"),
                    (week[6], "no run (a posting day)")]
    assert not any("rest_day" in text for _d, text in rows)
    # the way back (7 → 4 on Thursday): Wednesday ran on seven days, Friday and Sunday rested on four
    runs = [_run("published", run_date=week[2].isoformat()), _run("published", run_date=week[3].isoformat()),
            _run("skipped", run_date=week[4].isoformat(), metadata=rest),
            _run("skipped", run_date=week[6].isoformat(), metadata=rest)]
    _cadence(monkeypatch, "mon,tue,thu,sat")
    rows, _peak = ds._run_rows(runs, week[0], week[-1])
    assert rows == [(week[0], "no run (a posting day)"), (week[1], "no run (a posting day)"),
                    (week[2], "published"), (week[3], "published"), (week[5], "no run (a posting day)")]
    # an unchanged default calendar: the rows as before Batch 3 (rest-day skips fall only on rest days)
    runs = [_run("published", run_date=d.isoformat()) if d.weekday() in selection.POST_WEEKDAYS
            else _run("skipped", run_date=d.isoformat(), metadata=rest) for d in week]
    rows, _peak = ds._run_rows(runs, week[0], week[-1])
    assert rows == [(d, "published") for d in week if d.weekday() in selection.POST_WEEKDAYS]


# ── B3PD-2 (2026-10-20): a PAST day is judged a posting day by its own record, not only by today's calendar ──

_REST = {"skip_reason": "rest_day"}
WED_11_04, THU_11_05 = date(2026, 11, 4), date(2026, 11, 5)


@pytest.mark.parametrize("run, shown", [
    (None, False),
    ("not a row", False),
    ({}, False),
    (_run("skipped", stage="planned", metadata=_REST), False),                  # a rest day's run, as it closes
    (_run("skipped", stage="planned", metadata=_REST, source_ref="x"), False),  # rest_day decides, whatever else
    (_run("failed", stage="planned"), False),                 # stopped before its first kick decided anything
    (_run("in_progress", stage="planned"), False),
    (_run("skipped", stage="planned"), False),                # skipped with no reason recorded
    (_run("skipped", stage="planned", metadata={"skip_reason": "Rest_Day"}), False),   # not a code: no reason
    (_run("failed", stage="planned", source_ref="   "), False),
    (_run("failed", stage="PLANNED"), False),
    (_run("failed", stage="selected"), True),                 # a rest day never completes a stage
    (_run("failed", stage="scripted"), True),
    (_run("in_progress", stage="voiced"), True),
    (_run("failed", stage="assets_ready"), True),
    (_run("media_ready", stage="?"), True),
    (_run("published", stage=None), True),
    (_run("skipped", stage="planned", metadata={"skip_reason": "content_rejected"}), True),   # the kick selected
    (_run("skipped", stage="planned", metadata={"skip_reason": "template_refused"}), True),
    (_run("in_progress", stage="planned", source_ref="journey:7"), True),   # the selection mirror
])
def test_b3pd2_ran_as_posting_day_reads_only_what_the_row_shows(run, shown):
    assert ds.ran_as_posting_day(run) is shown


def test_b3pd2_judged_posting_day_takes_todays_calendar_then_the_days_own_record(monkeypatch):
    """Today's calendar first (an unchanged one decides every day as before); a day it no longer lists is a posting
    day only when its nightly check succeeded for it (that check runs on posting days alone) or its run row shows a
    posting day's work — never on a guess (no check, a rest-day skip, or nothing at all)."""
    rest_run = _run("skipped", run_date=WED_11_04.isoformat(), stage="planned", metadata=_REST)
    worked = _run("failed", run_date=WED_11_04.isoformat(), stage="scripted")
    _cadence(monkeypatch, _SEVEN_DAYS)
    for nightly in (ds.NIGHTLY_CHECKED, ds.NIGHTLY_TIME_UNKNOWN, ds.NIGHTLY_NEVER, ds.NIGHTLY_LATER_DAY):
        for run in (None, rest_run, worked):
            assert ds.judged_posting_day(WED_11_04, nightly=nightly, run=run) is True
    _cadence(monkeypatch, "mon,tue,thu,sat")
    for nightly in (ds.NIGHTLY_CHECKED, ds.NIGHTLY_TIME_UNKNOWN):
        assert ds.judged_posting_day(WED_11_04, nightly=nightly, run=None) is True
    for nightly in (ds.NIGHTLY_NEVER, ds.NIGHTLY_LATER_DAY):
        assert ds.judged_posting_day(WED_11_04, nightly=nightly, run=worked) is True
        assert ds.judged_posting_day(WED_11_04, nightly=nightly, run=rest_run) is False
        assert ds.judged_posting_day(WED_11_04, nightly=nightly, run=None) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stage, where", [
    ("scripted", "after stage scripted (the last completed stage)"),
    # stopped before any stage completed: the run row cannot show what the day was — the nightly check's record does
    ("planned", "before any stage completed"),
])
async def test_b3pd2_a_morning_switch_from_seven_days_to_four_keeps_the_promised_final_word(bot, digest_jobs,
                                                                                         digest_ledger, monkeypatch,
                                                                                         stage, where):
    """The finding: at seven days Wednesday 2026-11-04's run fails on attempt 2 of 6, and its 22:00 check promises a
    final word tomorrow after 16:00 ET. Thursday morning the owner switches to Mon/Tue/Thu/Sat — MARKETING_POST_WEEKDAYS'
    own advice is to change it in the morning ET. Wednesday is a rest day on TODAY's calendar, so `final_due` alone
    says no; but the nightly check's own record (its job state: run_day = Wednesday) says Wednesday was a posting day,
    so the promised word still goes out at 16:00 — once, under the final word's own claim."""
    _cadence(monkeypatch, _SEVEN_DAYS)
    # Tuesday posted fine (at seven days Wednesday 22:00 also owes Tuesday's final word — nothing to add to it)
    _seed_health_run(digest_ledger, _run("media_ready", run_date="2026-11-03",
                                         finished_at=_iso_of(_et(2026, 11, 3, 17, 5)),
                                         updated_at=_iso_of(_et(2026, 11, 3, 17, 5))))
    _seed_health_run(digest_ledger, _run("failed", run_date=WED_11_04.isoformat(), stage=stage, attempts=2,
                                         last_error="writer 503", updated_at=_iso_of(_et(2026, 11, 4, 21, 40))))
    assert (await _health_at(digest_jobs, _et(2026, 11, 4, 22, 0)))["sent"] == 1
    assert "a final word comes tomorrow after 16:00 ET" in _sends(bot)[0]["text"]
    _cadence(monkeypatch, "mon,tue,thu,sat")                   # Thursday 09:00 ET
    at = _et(2026, 11, 5, 16, 0)
    assert ds.final_due(at) is False                           # the calendar alone would cancel the promise
    assert await _health_at(digest_jobs, at) == {"checked": 1, "sent": 1, "failed": 0, "rate_limited": 0}
    assert _sends(bot)[1]["text"] == (
        f"⚠️ Marketing run Wed 2026-11-04 is still FAILED {where}, attempt 2 of 6: no retry happened — check the "
        "marketing-worker cron (hourly at :15) and its last logs. This day's posts will not go out. Last error: "
        "writer 503.")
    assert digest_jobs.claims == [(ds.JOB_HEALTH_FINAL, WED_11_04), (ds.JOB_HEALTH, WED_11_04),
                                  (ds.JOB_HEALTH_FINAL, THU_11_05)]
    for later in (_et(2026, 11, 5, 16, 10), _et(2026, 11, 5, 21, 59)):    # once a day, like any final word
        assert await _health_at(digest_jobs, later) == {"checked": 0, "sent": 0, "failed": 0, "rate_limited": 0}
    assert len(_sends(bot)) == 2 and len(digest_jobs.claims) == 3


@pytest.mark.asyncio
async def test_b3pd2_with_no_nightly_check_the_run_row_is_the_days_record(bot, digest_jobs, digest_ledger,
                                                                          monkeypatch):
    """The same switch, but Wednesday's nightly check never went out (nothing in its job state). Wednesday's run row
    is its own record: it completed stage `scripted`, which a rest day's run never does (it closes `rest_day` at its
    first kick) — so Thursday's final word reports the day, like any final word with no check before it."""
    _cadence(monkeypatch, "mon,tue,thu,sat")
    _seed_health_run(digest_ledger, _run("failed", run_date=WED_11_04.isoformat(), stage="scripted", attempts=6,
                                         last_error="ffmpeg exited 137", updated_at=_iso_of(_et(2026, 11, 4, 21, 40))))
    assert await _health_at(digest_jobs, _et(2026, 11, 5, 16, 0)) == {"checked": 1, "sent": 1, "failed": 0,
                                                                      "rate_limited": 0}
    assert _sends(bot)[0]["text"] == (
        "⚠️ Marketing run Wed 2026-11-04 ended FAILED after stage scripted (the last completed stage): all 6 attempts "
        "used. This day's posts will not go out. Last error: ffmpeg exited 137.")
    assert digest_jobs.claims == [(ds.JOB_HEALTH_FINAL, THU_11_05)]


@pytest.mark.asyncio
async def test_b3pd2_an_unchanged_calendar_looks_once_claims_nothing_and_sends_nothing(bot, digest_jobs, digest_ledger,
                                                                                       monkeypatch):
    """At the default calendar the day after a rest day (Thursday 2026-10-08, after Wednesday's `rest_day` skip) is
    exactly as before — no claim, no message, `checked` 0 — and the look at its records costs ONE job-state read and
    ONE run read per process per day: none before 16:00 ET, none on a later tick that day."""
    _seed_health_run(digest_ledger, _run("skipped", run_date="2026-10-07", stage="planned", metadata=_REST))
    state_reads: List[str] = []
    run_reads: List[Any] = []
    real_state, real_run = ds._job_state, digest_ledger.get_run_for_date

    async def state_spy(svc: Any, job: str) -> Dict[str, Any]:
        state_reads.append(job)
        return await real_state(svc, job)

    async def run_spy(run_date: Any) -> Any:
        run_reads.append(run_date)
        return await real_run(run_date)

    monkeypatch.setattr(ds, "_job_state", state_spy)
    monkeypatch.setattr(digest_ledger, "get_run_for_date", run_spy)
    assert (await _health_at(digest_jobs, _et(2026, 10, 8, 15, 59)))["checked"] == 0
    assert state_reads == [] and run_reads == []
    for at in (_et(2026, 10, 8, 16, 0), _et(2026, 10, 8, 16, 10), _et(2026, 10, 8, 21, 59)):
        assert await _health_at(digest_jobs, at) == {"checked": 0, "sent": 0, "failed": 0, "rate_limited": 0}
    assert state_reads == [ds.JOB_HEALTH] and run_reads == [date(2026, 10, 7)]
    assert digest_jobs.claims == [] and digest_jobs.state_reads == 0 and _sends(bot) == []


@pytest.mark.asyncio
async def test_b3pd2_the_look_reads_nothing_while_the_bot_is_off_or_telegram_holds_us_off(bot, digest_jobs,
                                                                                         digest_ledger, monkeypatch):
    """`_ready` would take no claim then — so the look reads nothing either (the next tick asks again)."""
    def no_read(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the records were read")

    monkeypatch.setattr(ds, "_job_state", no_read)
    monkeypatch.setattr(digest_ledger, "get_run_for_date", no_read)
    monkeypatch.setattr(rs, "_rate_limited_until", time.monotonic() + 600)
    assert (await _health_at(digest_jobs, _et(2026, 10, 8, 16, 0)))["checked"] == 0
    monkeypatch.setattr(rs, "_rate_limited_until", 0.0)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    assert (await _health_at(digest_jobs, _et(2026, 10, 8, 16, 5)))["checked"] == 0
    assert ds._FINAL_OWED == {} and digest_jobs.claims == [] and _sends(bot) == []


@pytest.mark.asyncio
async def test_b3pd2_a_failed_look_is_logged_and_retried_never_a_guess(bot, digest_jobs, digest_ledger, monkeypatch,
                                                                       caplog):
    """A ledger error while looking: no word on a guess (WARNING naming the day), no claim, and the next try waits
    FINAL_OWED_RETRY_SECONDS — then the look runs again and the promised word goes out."""
    _cadence(monkeypatch, "mon,tue,thu,sat")
    _seed_health_run(digest_ledger, _run("failed", run_date=WED_11_04.isoformat(), stage="scripted", attempts=6,
                                         updated_at=_iso_of(_et(2026, 11, 4, 21, 40))))
    real_run = digest_ledger.get_run_for_date
    fail = {"on": True}

    async def flaky(run_date: Any) -> Any:
        if fail["on"]:
            raise mrs.MarketingRunError("runs 503")
        return await real_run(run_date)

    monkeypatch.setattr(digest_ledger, "get_run_for_date", flaky)
    caplog.set_level(logging.WARNING, logger=ds.__name__)
    assert (await _health_at(digest_jobs, _et(2026, 11, 5, 16, 0)))["checked"] == 0
    assert any("2026-11-04" in r.getMessage() and "runs 503" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    fail["on"] = False
    assert (await _health_at(digest_jobs, _et(2026, 11, 5, 16, 5)))["checked"] == 0    # waits out the retry
    assert digest_jobs.claims == [] and _sends(bot) == []
    ds._FINAL_OWED["retry_at"] = 0.0                                                # the wait is over
    assert (await _health_at(digest_jobs, _et(2026, 11, 5, 16, 15)))["sent"] == 1
    assert _sends(bot)[0]["text"].startswith("⚠️ Marketing run Wed 2026-11-04 ended FAILED after stage scripted")


@pytest.mark.asyncio
async def test_b3pd2_a_switch_to_seven_days_reads_a_rest_day_skip_as_rest(bot, digest_jobs, digest_ledger, monkeypatch):
    """The other direction (D18's final-word twin): at four days Wednesday rested (`rest_day`); Thursday morning the
    owner moves to seven. Thursday's final word is due on today's calendar, finds no nightly check and a rest-day
    skip — and says nothing."""
    _seed_health_run(digest_ledger, _run("skipped", run_date=WED_11_04.isoformat(), stage="planned", metadata=_REST))
    _cadence(monkeypatch, _SEVEN_DAYS)
    assert await _health_at(digest_jobs, _et(2026, 11, 5, 16, 0)) == {"checked": 1, "sent": 0, "failed": 0,
                                                                      "rate_limited": 0}
    assert digest_jobs.claims == [(ds.JOB_HEALTH_FINAL, THU_11_05)] and _sends(bot) == []


@pytest.mark.asyncio
async def test_b3pd2_the_default_calendar_claims_and_says_exactly_what_it_did_for_two_weeks(bot, digest_jobs,
                                                                                           digest_ledger):
    """Two weeks at the default calendar, every rest day closed `rest_day` and every posting day fine, ticked at 16:00
    and 22:00 ET each day: the final word is claimed only on the day after a posting day and the nightly check only
    on a posting day — the claims before B3PD-2 — and nothing is sent."""
    start = date(2026, 10, 5)
    days = [start + timedelta(days=i) for i in range(14)]
    for d in days:
        if d.weekday() in selection.POST_WEEKDAYS:
            _seed_health_run(digest_ledger, _run("media_ready", run_date=d.isoformat(),
                                                 finished_at=_iso_of(_et(d.year, d.month, d.day, 17, 5)),
                                                 updated_at=_iso_of(_et(d.year, d.month, d.day, 17, 5))))
        else:
            _seed_health_run(digest_ledger, _run("skipped", run_date=d.isoformat(), stage="planned", metadata=_REST))
    for d in days:
        for hh in (16, 22):
            await _health_at(digest_jobs, _et(d.year, d.month, d.day, hh, 0))
    expected = []
    for d in days:
        if (d - timedelta(days=1)).weekday() in selection.POST_WEEKDAYS:
            expected.append((ds.JOB_HEALTH_FINAL, d))
        if d.weekday() in selection.POST_WEEKDAYS:
            expected.append((ds.JOB_HEALTH, d))
    assert digest_jobs.claims == expected and _sends(bot) == []


def test_batch3_pool_runway_follows_the_switch(monkeypatch):
    """The runway counts the lesson days of the posting days in force NOW. Lessons only (classes A): every day at seven
    days. Company Weekly on: still Saturday alone, as Money Map runs Wednesday, Friday and Sunday — unless its class
    is off, when those days are lessons too. At the default the dates are today's, before and after a switch."""
    mon = date(2026, 10, 5)
    pool = [str(i) for i in range(10)]
    news = dict(classes=_NEWS_CLASSES, news_series=_NEWS_SERIES)
    default = ds.pool_runway(pool, [], today=mon, today_picked=False)
    # Mon, Tue, Thu, Sat ×2, then Mon and Tue take the ten — the next Thursday repeats
    assert default == {"pool": 10, "unused": 10, "first_repeat": date(2026, 10, 22)}
    default_news = ds.pool_runway(["a", "b", "c"], [], today=mon, today_picked=False, **news)
    _cadence(monkeypatch, _SEVEN_DAYS)
    # Mon 10-05 .. Wed 10-14 take the ten: Thu 10-15 repeats
    assert ds.pool_runway(pool, [], today=mon, today_picked=False) == {
        "pool": 10, "unused": 10, "first_repeat": date(2026, 10, 15)}
    # Sat 10-10, 10-17, 10-24 take a, b, c — Sat 10-31 repeats: the seven-day news week has one lesson day too
    assert ds.pool_runway(["a", "b", "c"], [], today=mon, today_picked=False, **news) == default_news == {
        "pool": 3, "unused": 3, "first_repeat": date(2026, 10, 31), "no_later_than": True}
    for offset in range(21):
        day = ds.pool_runway(["a", "b", "c"], ["b"], today=mon + timedelta(days=offset), today_picked=False,
                             **news)["first_repeat"]
        assert day.weekday() == 5, day
    # class F off: Wednesday-Sunday's chains are the lesson alone (Monday stays CEO / insider buys; Tuesday 10-06 is
    # out of the 13F season, so it is the lesson too) — Tue 10-06, Wed, Thu take a, b, c and Fri 10-09 repeats
    assert ds.pool_runway(["a", "b", "c"], [], today=mon, today_picked=False, classes=frozenset({"A", "C"}),
                          news_series=_NEWS_SERIES)["first_repeat"] == date(2026, 10, 9)
    # a week without Saturday has no lesson-only day while Money Map is on: no date, never a guessed one
    _cadence(monkeypatch, "mon,tue,wed,thu,fri")
    assert ds.pool_runway(["a", "b", "c"], [], today=mon, today_picked=False, **news) == {
        "pool": 3, "unused": 3, "first_repeat": None, "no_later_than": True}
    _cadence(monkeypatch, "mon,tue,thu,sat")
    assert ds.pool_runway(pool, [], today=mon, today_picked=False) == default
    assert ds.pool_runway([f"journey:{i}" for i in range(34)], ["journey:3", "journey:7"], today=date(2026, 10, 1),
                          today_picked=True) == {"pool": 34, "unused": 32, "first_repeat": date(2026, 11, 28)}


@pytest.mark.asyncio
@pytest.mark.parametrize("cadence, limit", [("mon,tue,thu,sat", 240), (_SEVEN_DAYS, 420)], ids=["default", "seven"])
async def test_batch3_the_runway_reads_news_recent_limit_rows(digest_ledger, monkeypatch, cadence, limit):
    """D17: the digest's runway reads what selection reads — `selection.news_recent_limit()` rows, at the posting days
    in force NOW (240 at four a week, 420 at seven), read once for both of its reads — never script_service's constant."""
    _cadence(monkeypatch, cadence)
    reads: List[tuple] = []
    real = digest_ledger.recent_source_refs

    async def spy(before, n):
        reads.append((before, n))
        return await real(before, n)

    monkeypatch.setattr(digest_ledger, "recent_source_refs", spy)
    report = await ds.gather_digest(digest_ledger, NOW)
    today = NOW.astimezone(ET).date()
    assert reads == [(today + timedelta(days=1), limit), (today, limit)]
    assert report["runway"]["pool"] == 10


@pytest.mark.asyncio
async def test_batch3_at_seven_days_the_runway_sees_every_lesson_of_a_long_news_ledger(digest_ledger, monkeypatch):
    """Why the read must scale: 70 weeks at seven days with Company Weekly on — six news rows and one Saturday lesson a
    week — and a pool of 45 that every lesson has used. 420 rows hold 60 Saturdays, so the runway says what is true:
    no lesson unused. 240 rows (the four-day read) would hold 35 and report 10 of 45 unused."""
    from app.services.marketing import content_pool

    pool = [f"journey:{i:02d}" for i in range(45)]
    monkeypatch.setattr(settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(content_pool, "eligible_keys", lambda: list(pool))
    _cadence(monkeypatch, _SEVEN_DAYS)
    today = NOW.astimezone(ET).date()
    rows = digest_ledger.fake.tables[mrs.SCRIPTS].rows
    day, lessons = today - timedelta(days=70 * 7), 0
    while day < today:
        ref = pool[lessons % 45] if day.weekday() == 5 else f"news:money_map:T{day.toordinal()}:2026"
        lessons += day.weekday() == 5
        rows.append({"run_id": str(uuid.uuid4()), "run_date": day.isoformat(), "source_ref": ref, "tokens_used": 0})
        day += timedelta(days=1)
    report = await ds.gather_digest(digest_ledger, NOW)
    assert report["runway"]["pool"] == 45 and report["runway"]["unused"] == 0
    seen_by_240 = selection.lesson_refs([ref for _d, ref in sorted(((r["run_date"], r["source_ref"]) for r in rows),
                                                                   reverse=True)][:240])
    assert 45 - len(set(seen_by_240)) == 10
