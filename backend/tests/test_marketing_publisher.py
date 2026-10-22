"""
The marketing PUBLISHER state machine (design doc §12.10, rules/marketing.md §2) —
`app/services/marketing/publisher_service.py` end to end over the in-memory PostgREST fake
(`FakeSupabase`, test_marketing_run_service.py) with a scriptable `FakeAdapter` registered in
`outlets.ADAPTERS`. No platform client is ever reached: the real X / Bluesky adapters are swapped
out, and conftest blocks sockets anyway.

What must never regress:
  * nothing is written before the CLAIM unless it is a terminal guard refusal: a dry run (switch or
    row), a stale or auto-approved row, a back-off, an unavailable adapter, the X cap and a lost
    fence all leave the row byte-identical (except the once-a-month cap alert);
  * the claim IS the write-ahead — visible inside `send()` (queued, `publish.state = sending`,
    attempts + 1, the attempt's charge journaled) — so a crash mid-call can never lead to a resend;
  * outcome matrix: PUBLISHED → published (review metadata kept); NOT_SENT → approved with a
    back-off, failed at MARKETING_PUBLISH_MAX_ATTEMPTS; REFUSED → failed + alert; AMBIGUOUS or an
    adapter bug → stays queued for reconcile; a ledger failure after a publish leaves it queued and
    logs every id;
  * the X monthly cap: `spent + reserve <= budget` (exactly at the limit is allowed), last month's
    charges excluded, an unreadable spend pauses X, one cap alert a month;
  * reconcile: found → published, absent → resent ONLY by a resend-safe adapter while fresh and
    live, X-like absent walks its schedule then escalates (never resent), the cap escalates,
    escalated rows are left alone;
  * retract runs with publishing switched off, retries a bounded number of times, then hands the
    owner a manual alert;
  * expiry closes stale approved / pending_review rows (00:00 ET boundary) and finished runs;
  * the late steps (measure → run health → weekly digest) run after the feed, each behind its own gate
    (both new switches fail-closed), import their module only when they run (a broken one fails its
    step alone), and — the real functions behind the real tick — do each day's work once, in order,
    without calling a platform;
  * the /go EARLY window (`publish_clock`, read by `smart_link.record_hit`): `record_outcome` opens the
    post's campaign window BEFORE its ledger write — PUBLISHED or AMBIGUOUS for 240 s, SUBMITTED for
    300 s — only for a LIVE row whose caption carries its OWN /go link (`post_copy.carries_go_link`).
    "Link in bio.", a link-free X caption, another platform's link, `/go/xyz` for x, NOT_SENT, REFUSED,
    a dry run and a reconcile FOUND never open one; a reconcile resend does (it is a send); a ledger
    failure after the send still does; a later post of the campaign extends the window, never shortens
    it; a clock that raises costs only a WARNING, never the publish.

The tick ORDER across all 16 switch combinations and step isolation are pinned by
tests/test_marketing_review_bot.py with every step stubbed; the late steps' wiring is pinned here.
Dates are computed relative to `run_service.run_date_et()` / the real clock (no freezegun); the
00:00 ET boundary is driven by patching `publisher_service._now`.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import inspect
import logging
import re
import sys
import textwrap
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.integrations import x_api
from app.schemas.marketing import POST_PLATFORMS
from app.services.marketing import outlet_base, outlet_x, outlets, post_copy, publish_clock
from app.services.marketing import publisher_service as pub
from app.services.marketing import run_service as mrs
from app.services.marketing.outlet_base import (
    ABSENT,
    AMBIGUOUS,
    FOUND,
    MANUAL,
    NOT_SENT,
    PUBLISHED,
    REFUSED,
    RETRACTED,
    RETRY,
    POST_IMAGE_KEY,
    UNKNOWN,
    Adapter,
    MarketingPublishRefused,
    MediaProblem,
    Outcome,
    PostImage,
    Prepared,
    ReconcileResult,
    RetractResult,
    backoff_seconds,
    text_sha256,
)
from test_marketing_run_service import FakeSupabase, _Table

PUB_LOGGER = pub.__name__
OWNER = "telegram:424242"
REVIEW_AT = "2026-09-30T13:00:00+00:00"
X_POST = 15_000          # what the fake X adapter reserves per create (outlet_x.POST_MICROS)
X_READ = 5_000           # reserved before one reconcile read (5 × $0.001)
X_DELETE = 10_000        # charged per delete (outlet_x.DELETE_MICROS)
_DEFAULT: Any = object()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: Any) -> datetime:
    parsed = mrs._parse_ts(value)
    assert parsed is not None, f"not a timestamp: {value!r}"
    return parsed


def _review_meta(**extra: Any) -> Dict[str, Any]:
    """What `create_posts` + `review_post` + the review sweep leave on an approved row."""
    return {"dry_run": False, "review": {"decision": "approved", "by": OWNER, "at": REVIEW_AT},
            "review_notified_at": REVIEW_AT, "review_message_id": 501, **extra}


def _assert_review_kept(row: Dict[str, Any]) -> None:
    meta = row["metadata"]
    assert meta["review"] == {"decision": "approved", "by": OWNER, "at": REVIEW_AT}
    assert meta["review_notified_at"] == REVIEW_AT and meta["review_message_id"] == 501
    assert meta["dry_run"] is False


def _journal(row: Dict[str, Any]) -> List[tuple]:
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return [(c["op"], c["micros"]) for c in meta.get("charges") or []]


def _assert_journal_balances(row: Dict[str, Any]) -> None:
    """The cost journal is the ledger of record: its sum is always `cost_micros`."""
    assert sum(m for _, m in _journal(row)) == int(row.get("cost_micros") or 0), row


def _messages(caplog, needle: str, level: int = logging.DEBUG) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= level and needle in r.getMessage()]


# ── the scriptable adapter ────────────────────────────────────────────────────


class FakeAdapter(Adapter):
    """An outlet whose every answer is scripted. FIFO lists hold an Outcome / ReconcileResult /
    RetractResult or an exception to RAISE (CancelledError included); an empty list answers
    PUBLISHED / UNKNOWN / RETRACTED. Every call is recorded with a deep copy of the row it saw."""

    def __init__(self, platform: str, *, reserve: int = 0, resend_safe: bool = False,
                 retractable: bool = True, reconcile_reserve: int = 0, retract_cost: int = 0,
                 schedule: tuple = (600, 1800, 5400, 14400)) -> None:
        self.platform = platform
        self.reserve = reserve
        self.resend_safe = resend_safe
        self.retractable = retractable
        self.reconcile_reserve_micros = reconcile_reserve
        self.retract_cost_micros = retract_cost
        self.reconcile_schedule = tuple(schedule)
        self.is_configured = True
        self.retract_configured = True
        self.is_available = True
        self.refuse: Optional[MarketingPublishRefused] = None
        #: The write-ahead charges the publisher journals after the claim, in ONE write (an X image post:
        #: its upload and its alt text) — `Prepared.pre_send_charges`.
        self.pre_send: tuple = ()
        self.prepare_posts: List[Dict[str, Any]] = []
        self.on_prepare: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_send: Optional[Callable[[Dict[str, Any]], None]] = None
        self.outcomes: List[Any] = []
        self.reconcile_results: List[Any] = []
        self.retract_results: List[Any] = []
        self.prepare_ids: List[str] = []
        self.prepared: List[Prepared] = []
        self.sends: List[tuple] = []
        self.reconciles: List[Dict[str, Any]] = []
        self.retracts: List[Dict[str, Any]] = []

    def configured(self) -> bool:
        return self.is_configured

    def configured_for_retract(self) -> bool:
        return self.retract_configured

    def available(self) -> bool:
        return self.is_available

    def prepare(self, post: Dict[str, Any]) -> Prepared:
        self.prepare_ids.append(str(post.get("id")))
        self.prepare_posts.append(post)
        if self.on_prepare is not None:
            self.on_prepare(post)
        if self.refuse is not None:
            raise self.refuse
        text = post.get("caption") if isinstance(post.get("caption"), str) else ""
        prepared = Prepared(
            payload={"text": text}, text_sha256=text_sha256(text), reserve_micros=self.reserve,
            publish_meta={self.platform: {"rkey": f"rk-{post.get('id')}"}},
            summary=f"{self.platform} fake payload chars={len(text)}",
            pre_send_charges=self.pre_send,
        )
        self.prepared.append(prepared)
        return prepared

    async def send(self, post: Dict[str, Any], prepared: Prepared) -> Outcome:
        self.sends.append((copy.deepcopy(post), prepared))
        if self.on_send is not None:
            self.on_send(post)
        n = len(self.sends)
        item = self.outcomes.pop(0) if self.outcomes else Outcome(
            PUBLISHED, external_id=f"{self.platform}-ext-{n}", external_url=f"https://{self.platform}.example/p/{n}")
        if isinstance(item, BaseException):
            raise item
        return item

    async def reconcile(self, post: Dict[str, Any]) -> ReconcileResult:
        self.reconciles.append(copy.deepcopy(post))
        item = self.reconcile_results.pop(0) if self.reconcile_results else ReconcileResult(UNKNOWN, error="no answer")
        if isinstance(item, BaseException):
            raise item
        return item

    async def retract(self, post: Dict[str, Any]) -> RetractResult:
        self.retracts.append(copy.deepcopy(post))
        item = self.retract_results.pop(0) if self.retract_results else RetractResult(
            RETRACTED, cost_micros=self.retract_cost_micros)
        if isinstance(item, BaseException):
            raise item
        return item


# ── the ledger + fixture ──────────────────────────────────────────────────────


class Env:
    def __init__(self) -> None:
        self.fake = FakeSupabase()
        self.svc = mrs.MarketingRunService(supabase=self.fake)
        self.enabled: List[str] = ["x", "bluesky"]
        self.budget = 2_000_000
        self.x = FakeAdapter("x", reserve=X_POST, reconcile_reserve=X_READ, retract_cost=X_DELETE)
        self.bsky = FakeAdapter("bluesky", resend_safe=True, schedule=(600, 1800, 7200, 21600))
        self._n = 0

    @property
    def posts(self):
        return self.fake.tables[mrs.POSTS]

    @property
    def runs(self):
        return self.fake.tables[mrs.RUNS]

    def raw(self, pid: str) -> Dict[str, Any]:
        return next(r for r in self.posts.rows if r["id"] == pid)

    def row(self, pid: str) -> Dict[str, Any]:
        return copy.deepcopy(self.raw(pid))

    def snapshot(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.posts.rows)

    def seed(self, *, platform: str = "x", status: str = "approved", day: Optional[date] = None,
             key: Optional[str] = None, meta: Any = _DEFAULT, approved_by: Optional[str] = OWNER,
             attempts: int = 0, updated_at: Optional[str] = None, created_at: Optional[str] = None,
             caption: Optional[str] = None, **cols: Any) -> str:
        self._n += 1
        now = _now()
        day = mrs.run_date_et() if day is None else day
        row = {
            "id": str(uuid.uuid4()),
            "run_id": str(uuid.uuid4()),
            "platform": platform,
            "format": "text",
            "status": status,
            "caption": caption if caption is not None else f"Lesson {self._n}: time in the market compounds.",
            "idempotency_key": key if key is not None else f"{day.isoformat()}:{platform}:text",
            "metadata": _review_meta() if meta is _DEFAULT else meta,
            "approved_by": approved_by,
            "attempts": attempts,
            "cost_micros": 0,
            "created_at": created_at or (now - timedelta(hours=3) + timedelta(seconds=self._n)).isoformat(),
            "updated_at": updated_at or (now - timedelta(minutes=30)).isoformat(),
        }
        row.update(cols)
        self.posts.rows.append(row)
        return row["id"]

    def seed_queued(self, *, platform: str = "x", started_ago: float = 3600.0, state: str = "unknown",
                    publish: Any = _DEFAULT, day: Optional[date] = None, **kw: Any) -> str:
        """A row the claim moved to `queued` whose outcome is not known yet."""
        started = _now() - timedelta(seconds=started_ago)
        caption = kw.pop("caption", None) or f"Queued {self._n + 1}: diversification lowers risk."
        if publish is _DEFAULT:
            publish = {"attempt": 1, "state": state, "started_at": started.isoformat(),
                       "text_sha256": text_sha256(caption)}
            if state == "escalated":
                publish["escalated_at"] = started.isoformat()
        meta = _review_meta(publish=publish)
        if state == "escalated":
            # `_escalate` writes it at the top too: the reconcile scan filters escalated rows out in
            # the query by it.
            meta["escalated_at"] = started.isoformat()
        return self.seed(platform=platform, status="queued", day=day, meta=meta,
                         attempts=1, caption=caption, claimed_at=started.isoformat(), **kw)

    def seed_published(self, *, platform: str = "x", **kw: Any) -> str:
        n = self._n + 1
        return self.seed(platform=platform, status="published", meta=_review_meta(publish={"state": "published"}),
                         external_id=f"{1840000000000000000 + n}", external_url=f"https://{platform}.example/p/{n}",
                         published_at=(_now() - timedelta(hours=1)).isoformat(), **kw)

    def spend_row(self, micros: int, *, at: Optional[str] = None) -> str:
        """An X post that already spent `micros` this month (journal + cost)."""
        return self.seed(status="published", meta={"dry_run": False, "charges": [
            {"at": at or _now().isoformat(), "op": "x_create", "micros": micros}]},
            cost_micros=micros, updated_at=_now().isoformat())


@pytest.fixture
def env(monkeypatch, caplog):
    e = Env()
    caplog.set_level(logging.INFO, logger=PUB_LOGGER)
    monkeypatch.setattr(pub, "get_marketing_run_service", lambda: e.svc)
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", True)
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", False)
    monkeypatch.setattr(pub.settings, "MARKETING_PUBLISH_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(pub.settings, "MARKETING_PUBLISH_RECONCILE_AFTER_SECONDS", 600)
    monkeypatch.setattr(pub.settings, "MARKETING_X_ACCESS_TOKEN", None)
    monkeypatch.setattr(pub.settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    # Batch 3: the three new switches at their declared defaults, whatever this machine's environment says.
    monkeypatch.setattr(pub.settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "")
    monkeypatch.setattr(pub.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    monkeypatch.setattr(pub.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", "off")
    monkeypatch.setattr(pub, "_escalation_logged", {})
    monkeypatch.setitem(outlets.ADAPTERS, "x", e.x)
    monkeypatch.setitem(outlets.ADAPTERS, "bluesky", e.bsky)
    monkeypatch.setattr(outlets, "enabled_platforms", lambda: list(e.enabled))
    monkeypatch.setattr(outlet_x, "budget_micros", lambda: e.budget)
    # The /go early-window clock is module state: a window a publish here opens (real time, ~2026-10)
    # must never leak into another test — smart_link's tests read it on a fixed clock near 2026-09-21,
    # where any leaked window would still be open and relabel their taps.
    publish_clock.clear()
    yield e
    publish_clock.clear()


# ── publish: what is decided BEFORE the claim ─────────────────────────────────


@pytest.mark.asyncio
async def test_no_enabled_platform_only_observes(env, caplog):
    env.enabled = []
    env.seed(platform="x")
    env.seed(platform="bluesky")
    before = env.snapshot()
    counters = await pub.publish_cycle()
    assert counters == {"approved_waiting": 2, "published": 0, "submitted": 0, "failed": 0, "skipped": 0, "retry": 0,
                        "unknown": 0, "capped": 0}
    assert env.posts.rows == before
    assert env.x.prepare_ids == [] and env.bsky.prepare_ids == [] and env.x.sends == []
    assert _messages(caplog, "no platform enabled")


@pytest.mark.asyncio
async def test_the_dry_run_switch_is_decided_before_the_claim(env, monkeypatch, caplog):
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    live = env.seed(platform="x")
    rehearsal = env.seed(platform="bluesky", meta=_review_meta(dry_run=True))
    before = env.snapshot()
    counters = await pub.publish_cycle()
    # Not one byte written: status, updated_at, attempts, metadata — and no X spend read either.
    assert env.posts.rows == before
    assert env.x.sends == [] and env.bsky.sends == []
    assert env.x.prepare_ids == [live] and env.bsky.prepare_ids == [rehearsal]
    assert counters["skipped"] == 2 and counters["published"] == 0 and counters["capped"] == 0
    lines = _messages(caplog, "DRY_RUN: would publish")
    assert len(lines) == 2
    assert any(live in m and "x fake payload" in m for m in lines)


@pytest.mark.asyncio
async def test_a_live_switch_never_sends_a_rehearsal_row(env, caplog):
    """`metadata.dry_run` must be exactly False. The query filter (`metadata->>dry_run = 'false'`)
    drops a flagged, a missing and a malformed one; a STRING "false" passes the text filter, and
    the row check behind it still stops it (defence in depth)."""
    env.seed(meta=_review_meta(dry_run=True))
    env.seed(meta={"review": {"decision": "approved", "by": OWNER, "at": REVIEW_AT}})
    env.seed(meta=None)
    env.seed(meta=["not", "a", "dict"])
    string_false = env.seed(meta=_review_meta(dry_run="false"))
    before = env.snapshot()
    counters = await pub.publish_cycle()
    assert env.posts.rows == before and env.x.sends == []
    assert env.x.prepare_ids == [string_false]
    assert counters["approved_waiting"] == 1 and counters["skipped"] == 1
    assert _messages(caplog, "DRY_RUN: would publish")


@pytest.mark.asyncio
async def test_a_dry_run_refusal_is_only_logged(env, monkeypatch, caplog):
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    env.x.refuse = MarketingPublishRefused("x: 2 cashtags — X refuses (and bills) more than one per API post")
    pid = env.seed()
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert env.row(pid) == before and env.x.sends == []
    assert counters["skipped"] == 1 and counters["failed"] == 0
    assert any(pid in m and "cashtags" in m for m in _messages(caplog, "would REFUSE"))


@pytest.mark.asyncio
async def test_the_window_is_not_starved_by_unsendable_rows(env):
    """200 OLDER approved rows the publisher can never send (a platform with no adapter, a
    rehearsal) must not fill the 100-row window ahead of the one sendable post."""
    for _ in range(100):
        env.seed(platform="tiktok")
    for _ in range(100):
        env.seed(platform="x", meta=_review_meta(dry_run=True))
    real = env.seed(platform="x")
    counters = await pub.publish_cycle()
    assert env.x.prepare_ids == [real] and len(env.x.sends) == 1
    assert env.row(real)["status"] == "published" and counters["published"] == 1


@pytest.mark.asyncio
async def test_only_a_fresh_row_is_sent(env):
    today = mrs.run_date_et()
    stale = env.seed(day=today - timedelta(days=2))
    future = env.seed(day=today + timedelta(days=1))
    malformed = env.seed(key="not-a-date:x:text")
    empty = env.seed(key="")
    yesterday = env.seed(day=today - timedelta(days=1))
    before = {pid: env.row(pid) for pid in (stale, future, malformed, empty)}
    counters = await pub.publish_cycle()
    assert env.x.prepare_ids == [yesterday]
    assert counters["skipped"] == 4 and counters["published"] == 1
    for pid, row in before.items():
        assert env.row(pid) == row
    assert env.row(yesterday)["status"] == "published"


@pytest.mark.asyncio
async def test_an_auto_approved_row_is_refused_with_a_warning(env, caplog):
    pid = env.seed(approved_by="auto")
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert env.row(pid) == before
    assert env.x.prepare_ids == [] and env.x.sends == []
    assert counters["skipped"] == 1
    assert any(pid in m for m in _messages(caplog, "AUTO-approved", logging.WARNING))


@pytest.mark.asyncio
async def test_a_back_off_in_the_future_waits_and_a_passed_one_sends(env):
    waiting = env.seed(attempts=1, meta=_review_meta(publish={
        "state": "not_sent", "next_attempt_at": (_now() + timedelta(minutes=5)).isoformat()}))
    due = env.seed(attempts=1, meta=_review_meta(publish={
        "state": "not_sent", "next_attempt_at": (_now() - timedelta(seconds=1)).isoformat()}))
    before = env.row(waiting)
    counters = await pub.publish_cycle()
    assert env.row(waiting) == before
    assert env.x.prepare_ids == [due]
    row = env.row(due)
    assert row["status"] == "published" and row["attempts"] == 2
    assert row["metadata"]["publish"]["attempt"] == 2 and row["metadata"]["publish"]["next_attempt_at"] is None
    assert counters["retry"] == 1 and counters["published"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("offset, sent", [(timedelta(0), True), (timedelta(microseconds=1), False)])
async def test_the_back_off_boundary_is_inclusive(env, monkeypatch, offset, sent):
    """`next_attempt_at == now` is due; one microsecond later is not."""
    fixed = _now()
    monkeypatch.setattr(pub, "_now", lambda: fixed)
    pid = env.seed(platform="bluesky", attempts=1, meta=_review_meta(publish={
        "state": "not_sent", "next_attempt_at": (fixed + offset).isoformat()}))
    await pub.publish_cycle()
    assert (len(env.bsky.sends) == 1) is sent
    assert env.row(pid)["status"] == ("published" if sent else "approved")


@pytest.mark.asyncio
async def test_an_unavailable_adapter_is_skipped_untouched(env):
    env.x.is_available = False
    pid = env.seed()
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert env.row(pid) == before and env.x.prepare_ids == [] and env.x.sends == []
    assert counters["retry"] == 1


@pytest.mark.asyncio
async def test_malformed_publish_metadata_does_not_block_a_send(env):
    """A garbage `publish` / `charges` value is replaced by the claim, never crashes it; an
    unreadable `next_attempt_at` is no back-off."""
    pid = env.seed(meta=_review_meta(publish="garbage", charges="garbage"))
    other = env.seed(meta=_review_meta(publish={"next_attempt_at": "not-a-time"}))
    counters = await pub.publish_cycle()
    assert counters["published"] == 2
    row = env.row(pid)
    assert row["status"] == "published" and row["metadata"]["publish"]["state"] == "published"
    assert _journal(row) == [("x_create", X_POST)]
    _assert_journal_balances(row)
    assert env.row(other)["status"] == "published"


@pytest.mark.asyncio
async def test_an_approved_row_without_updated_at_is_still_claimed(env):
    pid = env.seed()
    env.raw(pid)["updated_at"] = None
    await pub.publish_cycle()
    assert env.row(pid)["status"] == "published" and len(env.x.sends) == 1


@pytest.mark.asyncio
async def test_a_guard_refusal_fails_with_an_alert_and_is_never_claimed(env, caplog):
    env.x.refuse = MarketingPublishRefused("x: an @mention — X blocks unsolicited mentions from API posts")
    pid = env.seed(meta=_review_meta(alert_kind="auth", alert_notified_at=REVIEW_AT))
    counters = await pub.publish_cycle()
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "failed" and counters["failed"] == 1
    assert row["attempts"] == 0 and "claimed_at" not in row          # never claimed
    assert env.x.sends == []
    assert meta["publish"]["state"] == "refused" and meta["publish"]["category"] == "guard"
    assert "@mention" in meta["publish"]["error"] and "@mention" in row["last_error"]
    assert meta["alert_kind"] == "failed" and "@mention" in meta["alert_text"]
    assert "alert_notified_at" not in meta                            # the feed sends the new alert
    assert row["cost_micros"] == 0 and "charges" not in meta          # no spend: nothing was sent
    _assert_review_kept(row)
    assert any(pid in m for m in _messages(caplog, "REFUSED BY GUARD", logging.ERROR))


@pytest.mark.asyncio
async def test_a_prepare_bug_on_one_row_does_not_starve_the_others(env, caplog):
    poison = env.seed(platform="x")
    good = env.seed(platform="bluesky")

    def bug(post):
        if post["id"] == poison:
            raise KeyError("salt")

    env.x.on_prepare = bug
    await pub._step("publish", pub.publish_cycle)
    assert env.row(good)["status"] == "published"
    row = env.row(poison)
    assert row["attempts"] == 0 and env.x.sends == []              # never claimed: nothing left
    assert any(poison in m for m in _messages(caplog, "", logging.ERROR))


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["touched", "claimed_elsewhere"])
async def test_a_claim_lost_to_the_fence_is_skipped_and_never_sent(env, race):
    """Between the list and the claim another writer moved the row: the review sweep stamped it
    (updated_at changed) or another container claimed it. The fenced UPDATE matches nothing."""
    pid = env.seed()

    def other_writer(_post):
        raw = env.raw(pid)
        raw["updated_at"] = (_now() + timedelta(seconds=1)).isoformat()
        if race == "claimed_elsewhere":
            raw["status"] = "queued"

    env.x.on_prepare = other_writer
    counters = await pub.publish_cycle()
    assert env.x.sends == [] and counters["skipped"] == 1 and counters["published"] == 0
    row = env.row(pid)
    assert row["attempts"] == 0 and row["cost_micros"] == 0
    assert "publish" not in row["metadata"] and "charges" not in row["metadata"]
    if race == "touched":
        assert row["status"] == "approved"
        env.x.on_prepare = None
        await pub.publish_cycle()                         # the next tick claims the fresh row
        assert env.row(pid)["status"] == "published" and len(env.x.sends) == 1
    else:
        assert row["status"] == "queued"


@pytest.mark.asyncio
async def test_a_ledger_failure_on_the_claim_never_sends(env, caplog):
    pid = env.seed()
    env.posts.fail_updates.append(RuntimeError("PostgREST 503 on the claim"))
    await pub._step("publish", pub.publish_cycle)
    assert env.x.sends == []
    row = env.row(pid)
    assert row["status"] == "approved" and row["attempts"] == 0 and "publish" not in row["metadata"]
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── publish: the claim and the outcome matrix ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_write_ahead_is_visible_inside_send(env):
    pid = env.seed()
    caption = env.raw(pid)["caption"]
    seen: Dict[str, Any] = {}

    def inspect(post):
        seen["arg"] = copy.deepcopy(post)
        seen["stored"] = env.row(pid)

    env.x.on_send = inspect
    t0 = _now()
    await pub.publish_cycle()
    stored = seen["stored"]
    assert stored["status"] == "queued" and stored["attempts"] == 1
    assert _ts(stored["claimed_at"]) >= t0 - timedelta(seconds=1)
    p = stored["metadata"]["publish"]
    assert p["state"] == "sending" and p["attempt"] == 1
    assert _ts(p["started_at"]) >= t0 - timedelta(seconds=1)
    assert p["text_sha256"] == text_sha256(caption)
    assert p["next_attempt_at"] is None and p["reconcile"] is None
    assert p["x"] == {"rkey": f"rk-{pid}"}                                # prepared.publish_meta merged
    assert _journal(stored) == [("x_create", X_POST)] and stored["cost_micros"] == X_POST
    _assert_review_kept(stored)
    assert seen["arg"]["status"] == "queued" and seen["arg"]["metadata"]["publish"]["state"] == "sending"


@pytest.mark.asyncio
async def test_published_records_the_post_and_keeps_the_review_metadata(env):
    pid = env.seed()
    env.x.outcomes = [Outcome(PUBLISHED, external_id="1840000000000000001",
                              external_url="https://x.com/i/web/status/1840000000000000001",
                              published_at="2026-09-30T14:00:00+00:00")]
    counters = await pub.publish_cycle()
    row = env.row(pid)
    assert counters["published"] == 1
    assert row["status"] == "published" and row["external_id"] == "1840000000000000001"
    assert row["external_url"] == "https://x.com/i/web/status/1840000000000000001"
    assert row["published_at"] == "2026-09-30T14:00:00+00:00" and row["last_error"] is None
    p = row["metadata"]["publish"]
    assert p["state"] == "published" and p["history"][-1]["kind"] == PUBLISHED and p["history"][-1]["attempt"] == 1
    _assert_review_kept(row)
    assert row["cost_micros"] == X_POST
    _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_a_free_platform_claim_journals_no_charge(env):
    pid = env.seed(platform="bluesky")
    env.bsky.outcomes = [Outcome(PUBLISHED, external_id="at://did:plc:abc/app.bsky.feed.post/3k")]
    await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "published" and row["cost_micros"] == 0 and "charges" not in row["metadata"]
    assert _ts(row["published_at"]) <= _now()          # no platform time → the publisher's clock


@pytest.mark.asyncio
async def test_not_sent_walks_the_back_off_then_fails_at_the_cap(env, caplog):
    pid = env.seed()
    for _ in range(3):
        env.x.outcomes.append(Outcome(NOT_SENT, "transport", error="ConnectError: connection refused"))

    for attempt in (1, 2):
        t0 = _now()
        counters = await pub.publish_cycle()
        row = env.row(pid)
        assert row["status"] == "approved" and row["attempts"] == attempt and counters["retry"] == 1
        p = row["metadata"]["publish"]
        assert p["state"] == "not_sent" and p["category"] == "transport"
        assert "connection refused" in row["last_error"]
        wait = (_ts(p["next_attempt_at"]) - t0).total_seconds()
        assert abs(wait - backoff_seconds(attempt)) < 5, wait
        # The same tick again: the back-off holds it.
        await pub.publish_cycle()
        assert len(env.x.sends) == attempt
        env.raw(pid)["metadata"]["publish"]["next_attempt_at"] = (_now() - timedelta(seconds=1)).isoformat()

    counters = await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "failed" and row["attempts"] == 3 and counters["failed"] == 1
    assert row["metadata"]["alert_kind"] == "failed" and "after 3 attempts" in row["metadata"]["alert_text"]
    assert len(env.x.sends) == 3
    assert any(pid in m for m in _messages(caplog, "attempts exhausted", logging.ERROR))
    _assert_review_kept(row)
    _assert_journal_balances(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior, expected", [(0, "approved"), (1, "approved"), (2, "failed"), (7, "failed")])
async def test_not_sent_fails_exactly_at_max_attempts(env, prior, expected):
    pid = env.seed(attempts=prior)
    env.x.outcomes = [Outcome(NOT_SENT, "transport", error="ConnectTimeout")]
    counters = await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == expected and row["attempts"] == prior + 1
    if expected == "failed":
        assert counters["failed"] == 1 and row["metadata"]["alert_kind"] == "failed"
    else:
        assert counters["retry"] == 1 and "alert_kind" not in row["metadata"]
        assert row["metadata"]["publish"]["next_attempt_at"]


@pytest.mark.asyncio
async def test_an_auth_not_sent_honours_retry_at_and_raises_an_auth_alert(env):
    retry_at = _now() + timedelta(hours=1)
    pid = env.seed(meta=_review_meta(alert_kind="failed", alert_notified_at=REVIEW_AT))
    env.x.outcomes = [Outcome(NOT_SENT, "auth", error="x: 401 Unauthorized", retry_at=retry_at, alert="auth")]
    await pub.publish_cycle()
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "approved"
    assert _ts(meta["publish"]["next_attempt_at"]) == retry_at
    assert meta["alert_kind"] == "auth" and "not sent (auth)" in meta["alert_text"]
    assert "alert_notified_at" not in meta
    _assert_review_kept(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("category, journal", [
    ("transport", [("x_create", X_POST), ("refund_not_sent", -X_POST)]),   # provably never left: not billed
    ("rate_limited", [("x_create", X_POST)]),                              # unknown billing: over-count
    ("auth", [("x_create", X_POST)]),
])
async def test_only_a_transport_not_sent_refunds_the_x_reserve(env, category, journal):
    pid = env.seed()
    env.x.outcomes = [Outcome(NOT_SENT, category, error=f"x: {category}")]
    await pub.publish_cycle()
    row = env.row(pid)
    assert _journal(row) == journal
    _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_a_free_platform_not_sent_journals_nothing(env):
    pid = env.seed(platform="bluesky")
    env.bsky.outcomes = [Outcome(NOT_SENT, "transport", error="ConnectError")]
    await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "approved" and row["cost_micros"] == 0 and "charges" not in row["metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, journal", [
    (Outcome(REFUSED, "forbidden", error="403 You are not permitted to perform this action", alert="failed"),
     [("x_create", X_POST)]),                                              # a refused 403 keeps its charge
    (Outcome(REFUSED, "credits", error="402 credits depleted", refund_micros=-X_POST, alert="failed"),
     [("x_create", X_POST), ("refund", -X_POST)]),                         # a 402 is refunded
    (Outcome(REFUSED, "invalid", error="400 invalid request"),             # no alert named → "failed"
     [("x_create", X_POST)]),
])
async def test_a_refusal_fails_with_an_alert(env, caplog, outcome, journal):
    pid = env.seed()
    env.x.outcomes = [outcome]
    counters = await pub.publish_cycle()
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "failed" and counters["failed"] == 1
    assert meta["publish"]["state"] == "refused" and meta["publish"]["category"] == outcome.category
    assert meta["alert_kind"] == "failed" and "refused the post" in meta["alert_text"]
    assert row["last_error"] == outcome.error
    assert _journal(row) == journal
    _assert_journal_balances(row)
    _assert_review_kept(row)
    assert any(pid in m for m in _messages(caplog, "REFUSED", logging.ERROR))


@pytest.mark.asyncio
async def test_an_ambiguous_outcome_stays_queued_for_reconcile(env):
    pid = env.seed()
    env.x.outcomes = [Outcome(AMBIGUOUS, "duplicate", error="403 duplicate content")]
    counters = await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "queued" and counters["unknown"] == 1
    p = row["metadata"]["publish"]
    assert p["state"] == "unknown" and p["category"] == "duplicate" and p["started_at"]
    assert _journal(row) == [("x_create", X_POST)]                 # it may have been billed
    _assert_review_kept(row)


@pytest.mark.asyncio
async def test_an_adapter_that_raises_is_read_as_ambiguous(env, caplog):
    pid = env.seed()
    env.x.outcomes = [ValueError("our own bug")]
    counters = await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "queued" and counters["unknown"] == 1
    p = row["metadata"]["publish"]
    assert p["state"] == "unknown" and p["category"] == "bug" and "ValueError" in p["error"]
    assert any(pid in m for m in _messages(caplog, "adapter BUG", logging.ERROR))


@pytest.mark.asyncio
async def test_a_ledger_failure_after_publish_leaves_it_queued_and_reconcile_finds_it(env, caplog):
    pid = env.seed()
    ext = "1840000000000000777"
    env.x.outcomes = [Outcome(PUBLISHED, external_id=ext, external_url=f"https://x.com/i/web/status/{ext}")]
    env.x.on_send = lambda _post: env.posts.fail_updates.append(RuntimeError("PostgREST 520"))
    counters = await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "sending"
    assert row.get("external_id") is None and counters["failed"] == 0
    lines = _messages(caplog, "PUBLISHED BUT LEDGER WRITE FAILED", logging.ERROR)
    assert len(lines) == 1
    for ident in (pid, "platform=x", row["idempotency_key"], ext):
        assert ident in lines[0]
    # The next publish tick never re-sends it — it is not approved …
    await pub.publish_cycle()
    assert len(env.x.sends) == 1
    # … and reconcile, once the delay has passed, finds it.
    env.raw(pid)["metadata"]["publish"]["started_at"] = (_now() - timedelta(minutes=11)).isoformat()
    env.x.reconcile_results = [ReconcileResult(FOUND, external_id=ext, external_url=f"https://x.com/i/web/status/{ext}",
                                               cost_micros=1_000)]
    await pub.reconcile_cycle()
    row = env.row(pid)
    assert row["status"] == "published" and row["external_id"] == ext and len(env.x.sends) == 1
    _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_a_concurrent_stamp_between_claim_and_outcome_is_merged_not_lost(env):
    """The review sweep / feed stamps the row while the platform call is in flight: the outcome
    write loses its fence once, re-reads and merges — the stamp survives, the outcome lands."""
    pid = env.seed()

    def stamp(_post):
        raw = env.raw(pid)
        raw["metadata"] = {**raw["metadata"], "feed_probe": 1}
        raw["updated_at"] = (_now() + timedelta(seconds=2)).isoformat()

    env.x.on_send = stamp
    await pub.publish_cycle()
    row = env.row(pid)
    assert row["status"] == "published" and row["metadata"]["feed_probe"] == 1
    assert row["metadata"]["publish"]["state"] == "published"
    _assert_review_kept(row)


@pytest.mark.asyncio
async def test_a_crash_mid_fan_out_never_sends_anything_twice(env):
    """The container dies inside the second send. After the restart the X post (published) and the
    Bluesky post (queued, `sending`) are never re-sent by the publish step; only reconcile may
    resend — and only the resend-safe outlet."""
    xid = env.seed(platform="x")
    bid = env.seed(platform="bluesky")
    env.bsky.outcomes = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await pub.publish_cycle()
    assert env.row(xid)["status"] == "published"
    b = env.row(bid)
    assert b["status"] == "queued" and b["metadata"]["publish"]["state"] == "sending" and b["attempts"] == 1

    await pub.publish_cycle()                                 # the restarted loop's first tick
    assert len(env.x.sends) == 1 and len(env.bsky.sends) == 1

    env.raw(bid)["metadata"]["publish"]["started_at"] = (_now() - timedelta(minutes=11)).isoformat()
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["resent"] == 1
    assert env.row(bid)["status"] == "published"
    assert len(env.x.sends) == 1 and len(env.bsky.sends) == 2


@pytest.mark.asyncio
async def test_a_crash_inside_the_x_call_is_never_retried_by_publish(env):
    pid = env.seed()
    env.x.outcomes = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await pub.publish_cycle()
    for _ in range(3):
        await pub.publish_cycle()
    assert len(env.x.sends) == 1
    row = env.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "sending"
    assert _journal(row) == [("x_create", X_POST)]


# ── publish: the X monthly cap ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("budget, sent", [(30_000, True), (29_999, False)])
async def test_the_x_cap_allows_exactly_the_budget_and_not_one_micro_more(env, budget, sent):
    env.budget = budget
    env.spend_row(X_POST)
    pid = env.seed()
    counters = await pub.publish_cycle()
    row = env.row(pid)
    if sent:
        assert row["status"] == "published" and counters["capped"] == 0
    else:
        assert row["status"] == "approved" and row["attempts"] == 0 and "claimed_at" not in row
        assert "publish" not in row["metadata"] and "charges" not in row["metadata"]
        assert counters["capped"] == 1 and env.x.sends == []


@pytest.mark.asyncio
async def test_the_cap_keeps_a_running_total_within_one_cycle(env, monkeypatch):
    env.budget = 2 * X_POST
    reads: List[str] = []
    real = env.svc.spend_since

    async def counting(platform, since):
        reads.append(platform)
        return await real(platform, since)

    monkeypatch.setattr(env.svc, "spend_since", counting)
    ids = [env.seed() for _ in range(3)]
    counters = await pub.publish_cycle()
    assert [env.row(i)["status"] for i in ids] == ["published", "published", "approved"]
    assert counters["published"] == 2 and counters["capped"] == 1
    assert reads == ["x"]                                   # the month is read once per cycle


@pytest.mark.asyncio
async def test_a_blocked_x_post_raises_one_cap_alert_a_month(env):
    env.budget = 29_999
    env.spend_row(X_POST)
    first = env.seed()
    second = env.seed()
    c1 = await pub.publish_cycle()
    assert c1["capped"] == 2 and env.x.sends == []
    month = mrs.month_start_utc().strftime("%Y-%m")
    m1 = env.row(first)["metadata"]
    assert m1["alert_kind"] == "x_budget" and m1["x_cap_alert_month"] == month
    assert "monthly cap" in m1["alert_text"] and env.row(first)["status"] == "approved"
    _assert_review_kept(env.row(first))
    assert "alert_kind" not in env.row(second)["metadata"]          # one alert, not one per post
    # The feed sends it and stamps it; the next cycle must write nothing at all.
    env.raw(first)["metadata"]["alert_notified_at"] = _now().isoformat()
    snapshot = env.snapshot()
    c2 = await pub.publish_cycle()
    assert c2["capped"] == 2 and env.posts.rows == snapshot


@pytest.mark.asyncio
async def test_last_months_cap_alert_does_not_silence_this_month(env):
    env.budget = 29_999
    env.spend_row(X_POST)
    previous = (mrs.month_start_utc() - timedelta(days=1)).strftime("%Y-%m")
    env.seed(status="skipped", meta={"dry_run": False, "x_cap_alert_month": previous})
    pid = env.seed()
    await pub.publish_cycle()
    assert env.row(pid)["metadata"]["x_cap_alert_month"] == mrs.month_start_utc().strftime("%Y-%m")


@pytest.mark.asyncio
@pytest.mark.parametrize("budget, sent", [(30_000, True), (29_999, False)])
async def test_the_cap_window_starts_at_00_00_utc_on_the_first(env, budget, sent):
    start = mrs.month_start_utc()
    env.seed(status="published", meta={"dry_run": False, "charges": [
        {"at": (start - timedelta(microseconds=1)).isoformat(), "op": "x_create", "micros": 1_000_000},  # last month
        {"at": start.isoformat(), "op": "x_create", "micros": X_POST},                                  # this month
    ]}, cost_micros=1_000_000 + X_POST, updated_at=_now().isoformat())
    # A row last touched before the month began holds no charge of this month and is not read.
    env.seed(status="published", meta={"dry_run": False, "charges": [
        {"at": "garbage", "op": "x_create", "micros": 5_000_000}]},
        updated_at=(start - timedelta(seconds=1)).isoformat())
    env.budget = budget
    pid = env.seed()
    await pub.publish_cycle()
    assert env.row(pid)["status"] == ("published" if sent else "approved")


@pytest.mark.asyncio
async def test_a_charge_with_an_unreadable_time_is_counted(env):
    env.budget = 30_000
    env.seed(status="published", meta={"dry_run": False, "charges": [
        {"at": "not-a-time", "op": "x_create", "micros": X_POST + 1}]}, updated_at=_now().isoformat())
    pid = env.seed()
    counters = await pub.publish_cycle()
    assert env.row(pid)["status"] == "approved" and counters["capped"] == 1


@pytest.mark.asyncio
async def test_malformed_journal_entries_are_ignored_not_fatal(env):
    env.budget = 30_000
    env.seed(status="published", meta={"dry_run": False, "charges": [
        "x", None, {"micros": "abc", "at": _now().isoformat()}, {"at": _now().isoformat()}]},
        updated_at=_now().isoformat())
    env.seed(status="published", meta={"dry_run": False, "charges": "not-a-list"}, updated_at=_now().isoformat())
    env.seed(status="published", meta=None, updated_at=_now().isoformat())
    pid = env.seed()
    await pub.publish_cycle()
    assert env.row(pid)["status"] == "published"


@pytest.mark.asyncio
async def test_an_unreadable_spend_pauses_x_for_the_cycle_without_an_alert(env, monkeypatch, caplog):
    reads: List[str] = []

    async def broken(platform, since):
        reads.append(platform)
        raise mrs.MarketingRunError("spend_since failed (platform=x): APIError: 503")

    monkeypatch.setattr(env.svc, "spend_since", broken)
    x1, x2 = env.seed(), env.seed()
    b = env.seed(platform="bluesky")
    before = {pid: env.row(pid) for pid in (x1, x2)}
    counters = await pub.publish_cycle()
    assert reads == ["x"] and counters["capped"] == 2 and env.x.sends == []
    for pid, row in before.items():
        assert env.row(pid) == row                       # no alert: the cap was not reached
    assert env.row(b)["status"] == "published"           # only X is paused
    assert _messages(caplog, "spend could not be read", logging.ERROR)


# ── reconcile ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("after, started_ago, checked", [
    (600, 599, False), (600, 601, True), (1200, 900, False), (1200, 1201, True)])
async def test_reconcile_waits_for_the_reconcile_delay(env, monkeypatch, after, started_ago, checked):
    monkeypatch.setattr(pub.settings, "MARKETING_PUBLISH_RECONCILE_AFTER_SECONDS", after)
    pid = env.seed_queued(started_ago=started_ago)
    before = env.row(pid)
    counters = await pub.reconcile_cycle()
    assert (len(env.x.reconciles) == 1) is checked and counters["checked"] == int(checked)
    if not checked:
        assert env.row(pid) == before


@pytest.mark.asyncio
async def test_reconcile_found_publishes_and_corrects_the_read_charge(env):
    pid = env.seed_queued(started_ago=700)
    ext = "1840000000000000999"
    env.x.reconcile_results = [ReconcileResult(FOUND, external_id=ext, external_url=f"https://x.com/i/web/status/{ext}",
                                               published_at="2026-09-30T15:00:00Z", cost_micros=3_000)]
    counters = await pub.reconcile_cycle()
    assert counters["found"] == 1 and counters["checked"] == 1
    # Charged AHEAD: the row the adapter saw already carried the check and its worst-case cost.
    seen = env.x.reconciles[0]
    assert seen["metadata"]["publish"]["reconcile"]["n"] == 1 and _journal(seen) == [("x_read", X_READ)]
    row = env.row(pid)
    assert row["status"] == "published" and row["external_id"] == ext
    assert row["published_at"] == "2026-09-30T15:00:00Z" and row["last_error"] is None
    p = row["metadata"]["publish"]
    assert p["state"] == "published" and p["reconcile"]["n"] == 1 and p["reconcile"]["last_result"] == FOUND
    assert _journal(row) == [("x_read", X_READ), ("x_read_correction", 3_000 - X_READ)]
    _assert_journal_balances(row)
    _assert_review_kept(row)
    assert env.x.sends == []


@pytest.mark.asyncio
async def test_reconcile_absent_on_a_resend_safe_adapter_resends_the_same_record(env):
    pid = env.seed_queued(platform="bluesky", started_ago=700)
    raw = env.raw(pid)
    raw["metadata"]["publish"]["bluesky"] = {"rkey": f"rk-{pid}"}
    stored_sha = raw["metadata"]["publish"]["text_sha256"]
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["resent"] == 1
    assert env.bsky.prepare_ids == [pid] and len(env.bsky.sends) == 1
    sent_post, sent_prepared = env.bsky.sends[0]
    assert sent_prepared is env.bsky.prepared[0]                     # exactly what prepare built
    assert sent_prepared.text_sha256 == stored_sha                   # the write-ahead's text
    assert sent_post["status"] == "queued"
    assert sent_post["metadata"]["publish"]["state"] == "sending" and sent_post["metadata"]["publish"]["resends"] == 1
    row = env.row(pid)
    assert row["status"] == "published" and row["metadata"]["publish"]["bluesky"] == {"rkey": f"rk-{pid}"}
    assert row["attempts"] == 1                                       # a resend is not a new attempt
    _assert_review_kept(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["dry_switch", "publishing_off", "not_enabled", "rehearsal_row", "resends_spent",
                                 "adapter_not_resend_safe"])
async def test_reconcile_absent_is_not_resent_unless_everything_is_live(env, monkeypatch, why):
    pid = env.seed_queued(platform="bluesky", started_ago=700)
    raw = env.raw(pid)
    if why == "dry_switch":
        monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    elif why == "publishing_off":
        monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", False)
    elif why == "not_enabled":
        env.enabled = ["x"]
    elif why == "rehearsal_row":
        raw["metadata"]["dry_run"] = True
    elif why == "resends_spent":
        raw["metadata"]["publish"]["resends"] = pub.MAX_RESENDS
    else:
        env.bsky.resend_safe = False
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert env.bsky.sends == [] and counters["resent"] == 0 and counters["absent"] == 1
    row = env.row(pid)
    assert row["status"] == "queued"
    assert row["metadata"]["publish"]["reconcile"]["n"] == 1
    assert row["metadata"]["publish"]["reconcile"]["last_result"] == ABSENT


@pytest.mark.asyncio
async def test_reconcile_absent_and_expired_is_skipped_never_resent(env):
    pid = env.seed_queued(platform="bluesky", started_ago=700, day=mrs.run_date_et() - timedelta(days=2))
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    row = env.row(pid)
    assert counters["expired"] == 1 and env.bsky.sends == [] and env.bsky.prepare_ids == []
    assert row["status"] == "skipped" and row["metadata"]["skip_reason"] == "expired"
    assert row["metadata"]["publish"]["state"] == "absent_expired" and "absent" in row["last_error"]
    _assert_review_kept(row)
    # The status it expired FROM, as `expire_stale_posts` records it: an approved post the platform never
    # got, which the digest and the run's close summary must not count as "the owner never looked".
    assert row["metadata"]["expired_from"] == "queued"
    assert mrs.expired_unreviewed(row) is False


@pytest.mark.asyncio
async def test_an_x_absent_walks_the_schedule_then_escalates_and_is_never_resent(env, monkeypatch, caplog):
    monkeypatch.setattr(pub.settings, "MARKETING_X_ACCESS_TOKEN", "1234567890-NotARealTokenSecretPart")
    pid = env.seed_queued(started_ago=700)
    env.x.reconcile_results = [ReconcileResult(ABSENT, cost_micros=1_000) for _ in range(6)]

    await pub.reconcile_cycle()                         # check 1 at +600 s
    await pub.reconcile_cycle()                         # check 2 is due only at +1800 s
    assert len(env.x.reconciles) == 1
    assert env.row(pid)["metadata"]["publish"]["reconcile"]["n"] == 1

    env.raw(pid)["metadata"]["publish"]["started_at"] = (_now() - timedelta(seconds=20_000)).isoformat()
    for n in (2, 3, 4):
        await pub.reconcile_cycle()
        assert len(env.x.reconciles) == n
        assert env.row(pid)["metadata"]["publish"]["reconcile"]["n"] == n

    counters = await pub.reconcile_cycle()              # the schedule is spent → the owner decides
    assert counters["escalated"] == 1 and len(env.x.reconciles) == 4
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "queued"
    assert meta["publish"]["state"] == "escalated" and meta["publish"]["escalated_at"]
    assert meta["alert_kind"] == "unknown" and "UNKNOWN" in meta["alert_text"]
    assert "NOT be resent" in meta["alert_text"] and "https://x.com/i/user/1234567890" in meta["alert_text"]
    assert "NotARealTokenSecretPart" not in meta["alert_text"]
    assert env.x.sends == [] and env.x.prepare_ids == []
    _assert_journal_balances(row)
    _assert_review_kept(row)
    assert any(pid in m for m in _messages(caplog, "ESCALATED to the owner", logging.ERROR))

    snapshot = env.row(pid)
    await pub.reconcile_cycle()                         # escalated: never re-checked, nothing written
    assert len(env.x.reconciles) == 4 and env.row(pid) == snapshot


@pytest.mark.asyncio
async def test_a_reconcile_read_blocked_by_the_cap_escalates_without_reading(env):
    env.budget = X_READ - 1
    pid = env.seed_queued(started_ago=700)
    counters = await pub.reconcile_cycle()
    row = env.row(pid)
    assert counters["escalated"] == 1 and env.x.reconciles == [] and env.x.sends == []
    assert row["metadata"]["publish"]["state"] == "escalated" and row["metadata"]["alert_kind"] == "unknown"
    assert "cap" in row["metadata"]["alert_text"] and _journal(row) == []


@pytest.mark.asyncio
async def test_escalated_rows_are_not_rechecked_and_logged_once_a_day(env, caplog):
    pid = env.seed_queued(started_ago=90_000, state="escalated")
    before = env.row(pid)
    await pub.reconcile_cycle()
    await pub.reconcile_cycle()
    assert env.x.reconciles == [] and env.row(pid) == before
    lines = _messages(caplog, "still ESCALATED", logging.ERROR)
    assert len(lines) == 1 and pid in lines[0]


@pytest.mark.asyncio
async def test_a_reconcile_bug_keeps_the_row_queued_on_its_schedule(env, caplog):
    pid = env.seed_queued(started_ago=700)
    env.x.reconcile_results = [RuntimeError("parser bug")]
    counters = await pub.reconcile_cycle()
    row = env.row(pid)
    assert counters["checked"] == 1 and row["status"] == "queued"
    assert row["metadata"]["publish"]["reconcile"]["n"] == 1
    assert _journal(row) == [("x_read", X_READ)]                     # the worst case stays counted
    assert any(pid in m for m in _messages(caplog, "reconcile BUG", logging.ERROR))
    await pub.reconcile_cycle()                                       # next check only at +1800 s
    assert len(env.x.reconciles) == 1


@pytest.mark.asyncio
async def test_reconcile_unknown_records_the_check_and_waits(env):
    pid = env.seed_queued(started_ago=700)
    env.x.reconcile_results = [ReconcileResult(UNKNOWN, error="x: 503", cost_micros=X_READ)]
    await pub.reconcile_cycle()
    row = env.row(pid)
    rec = row["metadata"]["publish"]["reconcile"]
    assert row["status"] == "queued" and rec["n"] == 1 and rec["last_result"] == UNKNOWN and rec["error"] == "x: 503"
    assert _journal(row) == [("x_read", X_READ)]                     # cost == reserve → no correction
    _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_reconcile_survives_a_malformed_publish_block(env):
    """`metadata.publish` is not a dict: the age comes from `claimed_at`, and the outcome still lands."""
    pid = env.seed_queued(started_ago=700, publish="garbage")
    env.x.reconcile_results = [ReconcileResult(FOUND, external_id="42", cost_micros=X_READ)]
    await pub.reconcile_cycle()
    row = env.row(pid)
    assert row["status"] == "published" and row["external_id"] == "42"
    assert row["metadata"]["publish"]["state"] == "published"


@pytest.mark.asyncio
async def test_escalated_rows_cannot_starve_a_new_unknown_outcome(env):
    for i in range(pub.RECONCILE_SCAN_LIMIT):
        env.seed_queued(started_ago=86_400 * 3 + i, state="escalated")
    fresh = env.seed_queued(started_ago=700)
    env.x.reconcile_results = [ReconcileResult(FOUND, external_id="7", cost_micros=X_READ)]
    await pub.reconcile_cycle()
    assert env.row(fresh)["status"] == "published"


# ── retract ───────────────────────────────────────────────────────────────────


async def _request(env: Env, pid: str) -> None:
    status, _ = await env.svc.request_retract(pid, by=OWNER)
    assert status == "requested"


@pytest.mark.asyncio
async def test_a_confirmed_retract_is_deleted_even_with_publishing_off(env, monkeypatch):
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", False)
    env.enabled = []
    pid = env.seed_published()
    untouched = env.seed_published()
    await _request(env, pid)
    before_untouched = env.row(untouched)
    await pub.publisher_tick()
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "retracted" and meta["retract_done_at"]
    assert meta["retract"]["state"] == "done" and meta["retract"]["attempts"] == 1 and meta["retract"]["by"] == OWNER
    assert _journal(row) == [("x_delete", X_DELETE)]
    _assert_journal_balances(row)
    _assert_review_kept(row)
    assert [r["id"] for r in env.x.retracts] == [pid]
    assert env.row(untouched) == before_untouched                 # no request → never deleted
    assert env.x.sends == [] and env.x.reconciles == []           # publishing stayed off


@pytest.mark.asyncio
async def test_a_retract_cost_correction_is_journaled(env):
    pid = env.seed_published()
    await _request(env, pid)
    env.x.retract_results = [RetractResult(RETRACTED, cost_micros=0)]
    counters = await pub.retract_cycle()
    row = env.row(pid)
    assert counters["retracted"] == 1 and row["status"] == "retracted"
    assert _journal(row) == [("x_delete", X_DELETE), ("x_delete_correction", -X_DELETE)]
    _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_a_retract_retries_then_gives_up_with_an_alert(env, caplog):
    pid = env.seed_published()
    await _request(env, pid)
    env.x.retract_results = [RetractResult(RETRY, error="x: read timeout", cost_micros=X_DELETE) for _ in range(3)]

    for attempt in (1, 2):
        t0 = _now()
        counters = await pub.retract_cycle()
        row = env.row(pid)
        r = row["metadata"]["retract"]
        assert counters["retry"] == 1 and row["status"] == "published"
        assert r["state"] == "requested" and r["attempts"] == attempt and r["error"] == "x: read timeout"
        wait = (_ts(r["next_at"]) - t0).total_seconds()
        assert abs(wait - pub.RETRACT_BACKOFF_SECONDS * attempt) < 5, wait
        await pub.retract_cycle()                                     # held by next_at
        assert len(env.x.retracts) == attempt
        env.raw(pid)["metadata"]["retract"]["next_at"] = (_now() - timedelta(seconds=1)).isoformat()

    counters = await pub.retract_cycle()
    row = env.row(pid)
    meta = row["metadata"]
    assert counters["manual"] == 1 and row["status"] == "published"
    assert meta["retract"]["state"] == "gave_up" and meta["retract"]["attempts"] == 3
    assert meta["alert_kind"] == "retract_failed" and "by hand" in meta["alert_text"]
    assert meta["retract_closed_at"] and "alert_notified_at" not in meta
    assert _journal(row) == [("x_delete", X_DELETE)] * 3
    _assert_journal_balances(row)
    assert any(pid in m for m in _messages(caplog, "GAVE UP", logging.ERROR))
    await pub.retract_cycle()                                         # closed: never tried again
    assert len(env.x.retracts) == 3


@pytest.mark.asyncio
async def test_a_manual_retract_result_hands_it_to_the_owner(env):
    pid = env.seed_published()
    await _request(env, pid)
    env.x.retract_results = [RetractResult(MANUAL, error="x: no delete API")]
    counters = await pub.retract_cycle()
    meta = env.row(pid)["metadata"]
    assert counters["manual"] == 1 and meta["retract"]["state"] == MANUAL
    assert meta["alert_kind"] == "retract_failed" and meta["retract_closed_at"]


@pytest.mark.asyncio
async def test_a_platform_without_a_delete_api_is_never_called(env):
    env.x.retractable = False
    pid = env.seed_published()
    await _request(env, pid)
    counters = await pub.retract_cycle()
    row = env.row(pid)
    meta = row["metadata"]
    assert env.x.retracts == [] and counters["manual"] == 1 and row["status"] == "published"
    assert meta["retract"]["state"] == MANUAL and meta["alert_kind"] == "retract_manual"
    assert row["external_url"] in meta["alert_text"] and meta["retract_closed_at"]
    await pub.retract_cycle()
    assert env.x.retracts == [] and env.row(pid) == row


@pytest.mark.asyncio
async def test_a_retract_waits_while_the_credentials_are_missing(env, caplog):
    env.x.retract_configured = False
    pid = env.seed_published()
    await _request(env, pid)
    before = env.row(pid)
    await pub.retract_cycle()
    assert env.x.retracts == [] and env.row(pid) == before
    assert any(pid in m for m in _messages(caplog, "credentials are not set", logging.WARNING))


@pytest.mark.asyncio
async def test_a_retract_bug_is_a_retry_with_the_cost_kept(env, caplog):
    pid = env.seed_published()
    await _request(env, pid)
    env.x.retract_results = [RuntimeError("our delete bug")]
    counters = await pub.retract_cycle()
    row = env.row(pid)
    r = row["metadata"]["retract"]
    assert counters["retry"] == 1 and row["status"] == "published"
    assert r["state"] == "requested" and r["attempts"] == 1 and "RuntimeError" in r["error"]
    assert _journal(row) == [("x_delete", X_DELETE)]
    assert _messages(caplog, "retract BUG", logging.ERROR)


@pytest.mark.asyncio
async def test_closed_retracts_do_not_starve_a_new_request(env):
    """Requests that ended by hand (gave up / no API) stay `published`; they must not fill the
    10-row retract window ahead of a new confirmed request."""
    old = (_now() - timedelta(days=3)).isoformat()
    for _ in range(pub.RETRACT_SCAN_LIMIT):
        env.seed(status="published", updated_at=old, external_id="1", meta=_review_meta(
            retract_requested_at=old, retract_closed_at=old,
            retract={"state": "gave_up", "attempts": 3, "requested_at": old}))
    pid = env.seed_published()
    await _request(env, pid)
    await pub.retract_cycle()
    assert [r["id"] for r in env.x.retracts] == [pid]
    assert env.row(pid)["status"] == "retracted"


# ── expiry and the run lifecycle ──────────────────────────────────────────────


def _run(env: Env, day: date, status: str = "media_ready") -> str:
    rid = str(uuid.uuid4())
    env.runs.rows.append({"id": rid, "run_date": day.isoformat(), "status": status, "stage": "media",
                          "attempts": 1, "metadata": {}, "updated_at": (_now() - timedelta(hours=5)).isoformat()})
    return rid


@pytest.mark.asyncio
async def test_the_expire_step_closes_stale_open_posts_and_finished_runs(env):
    today = mrs.run_date_et()
    d2, d3, d4, d1 = (today - timedelta(days=n) for n in (2, 3, 4, 1))

    run_mixed = _run(env, d3)
    old_published = env.seed(status="published", day=d3, run_id=run_mixed)
    stale_approved = env.seed(status="approved", day=d3, run_id=run_mixed)

    run_pending = _run(env, d2)
    stale_pending = env.seed(status="pending_review", day=d2, run_id=run_pending)

    run_open = _run(env, d2)
    stale_queued = env.seed(status="queued", day=d2, run_id=run_open)

    run_retracted = _run(env, d4)
    env.seed(status="retracted", day=d4, run_id=run_retracted)

    run_yesterday = _run(env, d1)
    env.seed(status="failed", day=d1, run_id=run_yesterday)

    run_failed = _run(env, d3, status="failed")
    fresh_approved = env.seed(status="approved", day=d1)
    fresh_pending = env.seed(status="pending_review", day=today)

    before = {pid: env.row(pid) for pid in (old_published, stale_queued, fresh_approved, fresh_pending)}
    counters = await pub._expire_step()
    assert counters == {"auto_demoted": 0, "expired": 2, "runs_closed": 3}

    for pid in (stale_approved, stale_pending):
        row = env.row(pid)
        assert row["status"] == "skipped" and row["metadata"]["skip_reason"] == "expired"
        assert row["metadata"]["expired_at"]
        _assert_review_kept(row)
    for pid, row in before.items():
        assert env.row(pid) == row                     # queued is reconcile's; fresh rows wait

    status = {r["id"]: r["status"] for r in env.runs.rows}
    assert status[run_mixed] == "published"           # a post reached a platform
    assert status[run_pending] == "skipped"           # nothing went out (its post expired just now)
    assert status[run_retracted] == "published"       # retracted still reached a platform
    assert status[run_open] == "media_ready"          # a queued post is still open
    assert status[run_yesterday] == "media_ready"     # inside the claim window: never closed
    assert status[run_failed] == "failed"
    assert all(r.get("finished_at") for r in env.runs.rows if r["id"] in (run_mixed, run_pending, run_retracted))


# 00:00 ET, two days after the run day, both sides — in summer (EDT, UTC-4) and winter (EST, UTC-5).
_BOUNDARY = [
    (date(2026, 9, 30), datetime(2026, 10, 2, 3, 59, 59, 999999, tzinfo=timezone.utc), True),
    (date(2026, 9, 30), datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc), False),
    (date(2026, 11, 30), datetime(2026, 12, 2, 4, 59, 59, 999999, tzinfo=timezone.utc), True),
    (date(2026, 11, 30), datetime(2026, 12, 2, 5, 0, tzinfo=timezone.utc), False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("day, now, fresh", _BOUNDARY)
async def test_expiry_flips_at_00_00_et(env, day, now, fresh):
    approved = env.seed(status="approved", day=day)
    pending = env.seed(status="pending_review", day=day)
    closed = await env.svc.expire_stale_posts(mrs.run_date_et(now))
    assert closed == (0 if fresh else 2)
    for pid in (approved, pending):
        assert (env.row(pid)["status"] == "skipped") is not fresh


@pytest.mark.asyncio
@pytest.mark.parametrize("day, now, fresh", _BOUNDARY)
async def test_publish_stops_at_00_00_et(env, monkeypatch, day, now, fresh):
    monkeypatch.setattr(pub, "_now", lambda: now)
    pid = env.seed(platform="bluesky", day=day)
    await pub.publish_cycle()
    assert (len(env.bsky.sends) == 1) is fresh
    assert env.row(pid)["status"] == ("published" if fresh else "approved")


# ── adversarial review 2026-09-30: fixes pinned ───────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_with_an_unreadable_spend_waits_and_never_escalates(env, monkeypatch):
    """A ledger blip while reading the X spend is NOT the cap: the row waits for the next tick,
    untouched — it used to be escalated with a false "monthly cap" reason, for good."""
    async def broken(platform, since):
        raise mrs.MarketingRunError("spend_since failed: 503")

    monkeypatch.setattr(env.svc, "spend_since", broken)
    pid = env.seed_queued(started_ago=700)
    before = env.row(pid)
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 0 and counters["waiting"] == 1 and env.x.reconciles == []
    assert env.row(pid) == before


@pytest.mark.asyncio
async def test_reconcile_with_an_unreadable_spend_for_the_whole_window_goes_to_the_owner(env, monkeypatch):
    async def broken(platform, since):
        raise mrs.MarketingRunError("spend_since failed: 503")

    monkeypatch.setattr(env.svc, "spend_since", broken)
    pid = env.seed_queued(started_ago=15_000)          # past the last scheduled check (14 400 s)
    counters = await pub.reconcile_cycle()
    row = env.row(pid)
    assert counters["escalated"] == 1 and row["metadata"]["escalated_at"]
    assert "could not be read" in row["metadata"]["alert_text"]


@pytest.mark.asyncio
async def test_reconcile_with_the_x_budget_at_zero_escalates_with_its_own_reason(env):
    env.budget = 0
    pid = env.seed_queued(started_ago=700)
    await pub.reconcile_cycle()
    text = env.row(pid)["metadata"]["alert_text"]
    assert "X checks are switched off" in text and "monthly X cap" not in text


@pytest.mark.asyncio
async def test_auto_approved_rows_go_back_to_review_in_the_housekeeping_step(env):
    pid = env.seed(approved_by="auto", approved_at=_now().isoformat())
    counters = await pub._expire_step()
    row = env.row(pid)
    assert counters["auto_demoted"] == 1
    assert row["status"] == "pending_review" and row["approved_by"] is None and row["approved_at"] is None
    assert row["metadata"]["auto_demoted_at"] and row["metadata"]["review"]["by"] == OWNER   # kept


@pytest.mark.asyncio
async def test_an_approved_live_post_that_expires_raises_an_alert(env):
    stale = mrs.run_date_et() - timedelta(days=3)
    approved = env.seed(day=stale)
    pending = env.seed(day=stale, status="pending_review", approved_by=None)
    rehearsal = env.seed(day=stale, meta=_review_meta(dry_run=True))
    await pub._expire_step()
    a = env.row(approved)["metadata"]
    assert env.row(approved)["status"] == "skipped" and a["alert_kind"] == "expired"
    assert "expired UNPUBLISHED" in a["alert_text"] and "alert_notified_at" not in a
    assert "alert_kind" not in env.row(pending)["metadata"]          # nobody approved it
    assert "alert_kind" not in env.row(rehearsal)["metadata"]        # a rehearsal is never live


@pytest.mark.asyncio
async def test_a_refund_is_dated_at_the_charge_it_reverses(env):
    """Claimed at 23:59:55 UTC on the last day of a month, refused (402) after 00:00 UTC: the refund
    must land in the SAME month as the charge, or the new month starts below zero."""
    last_month = (mrs.month_start_utc() - timedelta(seconds=5)).isoformat()
    pid = env.seed_queued(started_ago=10)
    raw = env.raw(pid)
    raw["metadata"]["charges"] = [{"at": last_month, "op": "x_create", "micros": X_POST}]
    raw["cost_micros"] = X_POST
    await pub.record_outcome(env.svc, env.x, env.row(pid),
                             Outcome(REFUSED, "credits", error="402", refund_micros=-X_POST),
                             reserve_micros=X_POST)
    row = env.row(pid)
    assert _journal(row) == [("x_create", X_POST), ("refund", -X_POST)]
    assert row["metadata"]["charges"][-1]["at"] == last_month
    assert mrs.charges_since(row, mrs.month_start_utc()) == 0       # not −15 000


@pytest.mark.asyncio
async def test_a_retract_waiting_on_missing_credentials_goes_to_the_owner_after_an_hour(env):
    env.x.retract_configured = False
    pid = env.seed_published()
    await _request(env, pid)
    two_hours_ago = (_now() - timedelta(hours=2)).isoformat()
    env.raw(pid)["metadata"]["retract"]["requested_at"] = two_hours_ago
    counters = await pub.retract_cycle()
    meta = env.row(pid)["metadata"]
    assert counters["manual"] == 1 and env.x.retracts == []
    assert meta["retract_closed_at"] and meta["alert_kind"] == "retract_manual"
    assert "credentials are not set" in meta["alert_text"]


@pytest.mark.asyncio
async def test_one_row_whose_claim_raises_never_stops_the_rows_behind_it(env, monkeypatch, caplog):
    first, second = env.seed(), env.seed()
    original = env.svc.claim_post

    async def flaky(post_id, **kw):
        if post_id == first:
            raise mrs.MarketingRunError("claim_post failed: 520")
        return await original(post_id, **kw)

    monkeypatch.setattr(env.svc, "claim_post", flaky)
    counters = await pub.publish_cycle()
    assert env.row(second)["status"] == "published" and counters["published"] == 1
    assert env.row(first)["status"] == "approved"
    assert any(first in m for m in _messages(caplog, "claim FAILED", logging.ERROR))


@pytest.mark.asyncio
async def test_one_row_whose_reconcile_write_raises_never_stops_the_rows_behind_it(env, monkeypatch, caplog):
    a = env.seed_queued(started_ago=700)
    b = env.seed_queued(started_ago=700)
    original = env.svc.transition_post

    async def flaky(post_id, **kw):
        if post_id == a:
            raise mrs.MarketingRunError("transition_post failed: 520")
        return await original(post_id, **kw)

    monkeypatch.setattr(env.svc, "transition_post", flaky)
    env.x.reconcile_results = [ReconcileResult(FOUND, external_id="99", cost_micros=X_READ)]
    counters = await pub.reconcile_cycle()
    assert env.row(b)["status"] == "published" and counters["found"] == 1
    assert any(a in m for m in _messages(caplog, "reconcile FAILED", logging.ERROR))


@pytest.mark.asyncio
async def test_an_absent_post_whose_day_has_passed_closes_with_an_alert(env):
    stale = mrs.run_date_et() - timedelta(days=3)
    pid = env.seed_queued(platform="bluesky", started_ago=700, day=stale)
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    await pub.reconcile_cycle()
    row = env.row(pid)
    assert row["status"] == "skipped" and row["metadata"]["alert_kind"] == "expired"
    assert env.bsky.sends == []


# ── Stage 2 (Upload-Post): SUBMITTED / PENDING / FAILED and the same-request-id resend ───────────
#
# Upload-Post is a MIDDLEMAN: its 200 only means it accepted the job — SUBMITTED, the row stays
# `queued` with `publish.state = submitted`. Reconcile then polls the job and ends it FOUND
# (published), FAILED (failed + alert), PENDING (ask again; after the schedule, the owner) or ABSENT
# inside the idempotency window (resent with the SAME request id, which is the Idempotency-Key).
# These drive the publisher with the scriptable FakeAdapter registered as an Upload-Post platform;
# where the request id matters, `prepare` is the REAL Upload-Post adapter's, so the id a resend
# reuses is exactly the one production would send.

from app.services.marketing import outlet_upload_post  # noqa: E402
from app.services.marketing.outlet_base import FAILED, PENDING, SUBMITTED  # noqa: E402

UP_SCHEDULE = (600, 1200, 1800)


class _RealPrepareFake(FakeAdapter):
    """Scripted send / reconcile; `prepare` delegates to the real Upload-Post adapter."""

    def prepare(self, post: Dict[str, Any]) -> Prepared:
        self.prepare_ids.append(str(post.get("id")))
        prepared = outlet_upload_post.UploadPostAdapter(self.platform).prepare(post)
        self.prepared.append(prepared)
        return prepared


def _up_adapter(env: Env, monkeypatch, platform: str = "tiktok", *, cls: type = FakeAdapter,
                schedule: tuple = UP_SCHEDULE) -> FakeAdapter:
    adapter = cls(platform, resend_safe=True, retractable=False, schedule=schedule)
    monkeypatch.setitem(outlets.ADAPTERS, platform, adapter)
    env.enabled = [*env.enabled, platform]
    return adapter


def _backdate(env: Env, pid: str, seconds: float) -> None:
    env.raw(pid)["metadata"]["publish"]["started_at"] = (_now() - timedelta(seconds=seconds)).isoformat()


def _up_meta(**extra: Any) -> Dict[str, Any]:
    return {"request_id": "2026-10-01:tiktok:video:a1", "first_sent_at": "2026-10-01T13:00:00+00:00",
            "usage_before": {"count": 3, "limit": 10}, "usage_after": {"count": 4, "limit": 10}, **extra}


@pytest.mark.asyncio
async def test_a_submitted_outcome_stays_queued_and_is_never_resent_by_publish(env, monkeypatch, caplog):
    up = _up_adapter(env, monkeypatch)
    pid = env.seed(platform="tiktok", meta=_review_meta(alert_kind="failed", alert_notified_at=REVIEW_AT))
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    counters = await pub.publish_cycle()
    assert counters["submitted"] == 1
    assert counters["published"] == counters["unknown"] == counters["failed"] == counters["retry"] == 0
    row = env.row(pid)
    assert row["status"] == "queued" and row["attempts"] == 1
    assert row.get("external_id") is None and row.get("published_at") is None and row["last_error"] is None
    p = row["metadata"]["publish"]
    assert p["state"] == "submitted" and p["category"] == "" and p["error"] is None
    assert p["upload_post"] == _up_meta() and p["started_at"]
    assert p["history"][-1]["kind"] == SUBMITTED and p["history"][-1]["attempt"] == 1
    assert row["metadata"]["alert_notified_at"] == REVIEW_AT       # no new alert: nothing went wrong
    _assert_review_kept(row)
    assert any(pid in m for m in _messages(caplog, "SUBMITTED", logging.INFO))

    for _ in range(2):                                             # publish never touches it again
        await pub.publish_cycle()
    assert len(up.sends) == 1 and env.row(pid)["metadata"]["publish"]["state"] == "submitted"

    await pub.reconcile_cycle()                                    # not before the reconcile delay
    assert up.reconciles == []


@pytest.mark.asyncio
async def test_a_submitted_post_is_published_when_reconcile_finds_it(env, monkeypatch):
    up = _up_adapter(env, monkeypatch)
    pid = env.seed(platform="tiktok")
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    await pub.publish_cycle()
    _backdate(env, pid, 660)
    url = "https://www.tiktok.com/@caydex/video/7412000000000000001"
    up.reconcile_results = [ReconcileResult(
        FOUND, external_id="7412000000000000001", external_url=url, published_at="2026-10-01T14:03:00Z",
        publish_meta={"upload_post": _up_meta(platform_post_id="7412000000000000001")})]
    counters = await pub.reconcile_cycle()
    assert counters["checked"] == 1 and counters["found"] == 1
    # The adapter polled the row as the SUBMITTED write left it.
    assert up.reconciles[0]["metadata"]["publish"]["state"] == "submitted"
    row = env.row(pid)
    assert row["status"] == "published" and row["external_id"] == "7412000000000000001"
    assert row["external_url"] == url and row["published_at"] == "2026-10-01T14:03:00Z"
    p = row["metadata"]["publish"]
    assert p["state"] == "published" and p["reconcile"]["last_result"] == FOUND
    assert p["upload_post"]["platform_post_id"] == "7412000000000000001"
    assert p["upload_post"]["request_id"] == "2026-10-01:tiktok:video:a1"
    assert row["last_error"] is None and len(up.sends) == 1
    _assert_review_kept(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("error, last_error, in_alert", [
    ("tiktok: reconnect the account in Upload-Post (account_reauth_required) — token expired",
     "tiktok: reconnect the account in Upload-Post (account_reauth_required) — token expired",
     "reconnect the account in Upload-Post"),
    (None, "the platform reported a failure", "no reason given"),
])
async def test_reconcile_failed_closes_the_post_with_an_alert(env, monkeypatch, caplog, error, last_error, in_alert):
    up = _up_adapter(env, monkeypatch)
    pid = env.seed_queued(platform="tiktok", started_ago=700, state="submitted")
    env.raw(pid)["metadata"]["alert_notified_at"] = REVIEW_AT       # an earlier alert was already sent
    up.reconcile_results = [ReconcileResult(FAILED, error=error)]
    counters = await pub.reconcile_cycle()
    assert counters["checked"] == 1 and counters["failed"] == 1 and counters.get("escalated", 0) == 0
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "failed" and row["last_error"] == last_error
    assert meta["publish"]["state"] == "failed_on_platform"
    assert meta["publish"]["reconcile"]["last_result"] == FAILED and meta["publish"]["reconcile"]["error"] == error
    assert meta["alert_kind"] == "failed" and "TIKTOK did not publish the post" in meta["alert_text"]
    assert in_alert in meta["alert_text"]
    assert "alert_notified_at" not in meta                          # the feed sends the new alert
    assert up.sends == [] and up.prepare_ids == []                  # a failure is never resent
    _assert_review_kept(row)
    assert any(pid in m for m in _messages(caplog, "FAILED ON PLATFORM", logging.ERROR))
    await pub.reconcile_cycle()
    assert len(up.reconciles) == 1                                  # closed: never polled again


@pytest.mark.asyncio
async def test_reconcile_pending_records_each_check_then_escalates_after_the_schedule(env, monkeypatch, caplog):
    up = _up_adapter(env, monkeypatch)                               # schedule 600 / 1200 / 1800 s
    pid = env.seed_queued(platform="tiktok", started_ago=700, state="submitted")
    up.reconcile_results = [ReconcileResult(PENDING) for _ in range(5)]

    counters = await pub.reconcile_cycle()
    assert counters["checked"] == 1 and counters["pending"] == 1 and counters["escalated"] == 0
    row = env.row(pid)
    rec = row["metadata"]["publish"]["reconcile"]
    assert row["status"] == "queued" and rec["n"] == 1 and rec["last_result"] == PENDING
    assert row["metadata"]["publish"]["state"] == "submitted"       # still waiting on Upload-Post
    await pub.reconcile_cycle()                                      # check 2 is due only at +1200 s
    assert len(up.reconciles) == 1

    _backdate(env, pid, 2000)
    for n in (2, 3):
        counters = await pub.reconcile_cycle()
        assert counters["pending"] == 1 and len(up.reconciles) == n
        assert env.row(pid)["metadata"]["publish"]["reconcile"]["n"] == n
        assert env.row(pid)["status"] == "queued"

    # The schedule is spent but the job is still PROCESSING: no escalation yet (review 2026-10-01 —
    # "Not posted" on a job that later publishes would record a live post as failed).
    counters = await pub.reconcile_cycle()
    assert counters.get("escalated", 0) == 0 and len(up.reconciles) == 3
    env.raw(pid)["metadata"]["publish"]["reconcile"]["last_at"] = (
        _now() - pub.PENDING_REPOLL - timedelta(minutes=1)).isoformat()
    counters = await pub.reconcile_cycle()                           # one more free poll, 2 h later
    assert counters["pending"] == 1 and len(up.reconciles) == 4
    _backdate(env, pid, pub.PENDING_CEILING.total_seconds() + 60)    # past the 24 h ceiling → the owner
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 1 and len(up.reconciles) == 4
    row = env.row(pid)
    meta = row["metadata"]
    assert row["status"] == "queued" and meta["publish"]["state"] == "escalated" and meta["escalated_at"]
    assert meta["alert_kind"] == "unknown" and "still processing" in meta["alert_text"]
    assert up.sends == [] and up.prepare_ids == []
    _assert_review_kept(row)
    snapshot = env.row(pid)
    await pub.reconcile_cycle()                                      # escalated: left alone
    assert len(up.reconciles) == 4 and env.row(pid) == snapshot


@pytest.mark.asyncio
async def test_a_schedule_spent_on_unknown_results_escalates_with_the_last_reason(env, monkeypatch):
    up = _up_adapter(env, monkeypatch)
    pid = env.seed_queued(platform="tiktok", started_ago=5000, state="submitted")
    rec = {"n": 3, "last_at": _now().isoformat(), "last_result": UNKNOWN, "error": "tiktok: job status completed with no platform result yet"}
    env.raw(pid)["metadata"]["publish"]["reconcile"] = rec
    counters = await pub.reconcile_cycle()
    meta = env.row(pid)["metadata"]
    assert counters["escalated"] == 1 and up.reconciles == []
    assert "3 checks could not confirm it" in meta["alert_text"] and "no platform result yet" in meta["alert_text"]


@pytest.mark.asyncio
async def test_a_resend_safe_absent_resends_with_the_same_request_id(env, monkeypatch):
    up = _up_adapter(env, monkeypatch, "threads", cls=_RealPrepareFake)
    pid = env.seed(platform="threads")
    key = env.raw(pid)["idempotency_key"]
    up.outcomes = [Outcome(AMBIGUOUS, "server", error="upload-post upload_text: HTTP 503")]
    counters = await pub.publish_cycle()
    assert counters["unknown"] == 1
    written = env.row(pid)["metadata"]["publish"]["upload_post"]     # the claim's write-ahead
    assert written["request_id"] == f"{key}:a1"

    def submitted(_post):
        # What the real adapter returns on an async ack: the prepared meta plus the submit time.
        meta = dict(up.prepared[-1].publish_meta["upload_post"])
        up.outcomes.append(Outcome(SUBMITTED, publish_meta={"upload_post": {**meta, "submitted_at": _now().isoformat()}}))

    up.on_send = submitted
    _backdate(env, pid, 660)
    up.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["checked"] == 1 and counters["resent"] == 1

    first, again = up.prepared
    assert first.payload["request_id"] == again.payload["request_id"] == f"{key}:a1"
    assert again.publish_meta["upload_post"]["first_sent_at"] == first.publish_meta["upload_post"]["first_sent_at"]
    assert again.text_sha256 == first.text_sha256
    sent_post, sent_prepared = up.sends[1]
    assert sent_prepared is again                                    # exactly what prepare built
    assert sent_post["status"] == "queued"
    sp = sent_post["metadata"]["publish"]
    assert sp["state"] == "sending" and sp["resends"] == 1 and sp["upload_post"]["request_id"] == f"{key}:a1"

    row = env.row(pid)
    p = row["metadata"]["publish"]
    assert row["status"] == "queued" and p["state"] == "submitted" and p["resends"] == 1
    assert p["upload_post"]["request_id"] == f"{key}:a1"
    assert p["upload_post"]["first_sent_at"] == written["first_sent_at"]   # the window never slides
    assert row["attempts"] == 1                                      # a resend is not a new attempt
    _assert_review_kept(row)
    await pub.publish_cycle()                                        # still queued: publish never resends
    assert len(up.sends) == 2


@pytest.mark.asyncio
async def test_an_absent_job_outside_the_window_is_not_resent(env, monkeypatch):
    """The adapter offers a resend only inside 20 h (`resend_safe=True`); an ABSENT without it is
    re-checked on the schedule like any other, never resent — even on a resend-safe outlet."""
    up = _up_adapter(env, monkeypatch, "threads", cls=_RealPrepareFake)
    pid = env.seed_queued(platform="threads", started_ago=700, state="unknown")
    up.reconcile_results = [ReconcileResult(ABSENT, resend_safe=False)]
    counters = await pub.reconcile_cycle()
    assert counters["absent"] == 1 and counters["resent"] == 0
    assert up.sends == [] and up.prepare_ids == []
    row = env.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["reconcile"]["last_result"] == ABSENT


@pytest.mark.asyncio
async def test_a_lost_submitted_write_leaves_the_request_id_for_reconcile(env, monkeypatch, caplog):
    """The ledger fails while recording SUBMITTED: the claim's write-ahead already holds the request
    id, so reconcile can still poll the job — the row is never lost and never resent blindly."""
    up = _up_adapter(env, monkeypatch, "threads", cls=_RealPrepareFake)
    pid = env.seed(platform="threads")
    key = env.raw(pid)["idempotency_key"]
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": {"request_id": f"{key}:a1"}})]
    up.on_send = lambda _post: env.posts.fail_updates.append(RuntimeError("PostgREST 520"))
    counters = await pub.publish_cycle()
    assert counters["submitted"] == 0 and counters["unknown"] == 1
    row = env.row(pid)
    p = row["metadata"]["publish"]
    assert row["status"] == "queued" and p["state"] == "sending" and p["upload_post"]["request_id"] == f"{key}:a1"
    assert any(pid in m for m in _messages(caplog, "outcome submitted NOT RECORDED", logging.ERROR))
    await pub.publish_cycle()
    assert len(up.sends) == 1


# ── the /go early window (publish_clock) ──────────────────────────────────────
#
# Link scanners (Meta's fetchers, preview and security services) fetch a post's /go link within
# seconds to a few minutes of it going out — production 2026-10: within ~200 s, many with an ordinary
# browser user agent — so `smart_link.record_hit` counts a tap apart, under `<campaign>_early`, while
# the campaign's window is open. The publisher opens it in `record_outcome`, BEFORE the ledger write,
# and only for a LIVE row whose caption carries its OWN /go link: PUBLISHED or AMBIGUOUS (it may be
# live) for 240 s, an Upload-Post SUBMITTED job (live seconds later) for 300 s. Windows are checked
# against the wall clock read around the call: a stamp made between t0 and t1 closes in
# [t0 + window, t1 + window].

#: The code-owned value line (store state "live", as production runs since 2026-10-05) and the short
#: disclaimer, as `post_copy.compose` writes them for the computed-budget fields (x/threads/bluesky).
_VALUE_LINE = "Caydex: AI research on public companies — on the App Store."
_SHORT_DISCLAIMER = "Educational only, not investment advice. AI-assisted. Caydex"


def _linked(platform: str, *, slug: Optional[str] = None) -> str:
    """A link-bearing caption shaped as `post_copy.compose` writes it: the body, then the value line
    and `https://caydexinvest.com/go/<platform>` (or `<slug>` — another link), then the disclaimer."""
    return (f"Three habits that quietly compound.\n\n{_VALUE_LINE} Learn more: "
            f"https://caydexinvest.com/go/{platform if slug is None else slug}\n\n{_SHORT_DISCLAIMER}")


def _linked_before_the_value_line(platform: str) -> str:
    """A caption composed BEFORE the value line existed (2026-10-05): a row approved before that deploy
    and published after it ends '\\n\\nLearn more: <its /go link>\\n\\n<disclaimer>'."""
    return (f"Three habits that quietly compound.\n\nLearn more: https://caydexinvest.com/go/{platform}"
            f"\n\n{_SHORT_DISCLAIMER}")


#: X composed WITH allow_x_url (the bare link after the value line) and WITHOUT it (no link at all).
_X_WITH_LINK = (f"Three habits that quietly compound.\n\n#businessstrategy\n\n{_VALUE_LINE} "
                f"https://caydexinvest.com/go/x\n\n{_SHORT_DISCLAIMER}")
_X_WITHOUT_LINK = f"Three habits that quietly compound.\n\n#businessstrategy\n\n{_VALUE_LINE}\n\n{_SHORT_DISCLAIMER}"
#: TikTok / Instagram: captions are not clickable there.
_IN_BIO = ("Three habits that quietly compound.\n\n#businessstrategy #investing #financialliteracy\n\n"
           f"{_VALUE_LINE} Link in bio.\n\nCaydex · Educational, impersonal information — not investment advice.")


def _windows() -> Dict[str, float]:
    """The clock's whole state: campaign → the epoch second its early window closes."""
    return dict(publish_clock._until)


def _assert_window(campaign: str, t0: float, t1: float, seconds: float) -> float:
    until = publish_clock.open_until(campaign)
    assert until is not None, f"{campaign}: no early window was opened ({_windows()})"
    assert t0 + seconds <= until <= t1 + seconds, (campaign, seconds, until - t0, until - t1)
    return until


def _window_lines(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == PUB_LOGGER and r.levelno == logging.INFO and "early window open" in r.getMessage()]


def _clock_warnings(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == PUB_LOGGER and r.levelno == logging.WARNING and "publish clock" in r.getMessage()]


def _adapter(env: Env, monkeypatch, platform: str) -> FakeAdapter:
    """The Env's X / Bluesky fake, or a fresh Upload-Post-shaped fake registered (and enabled) for
    `platform`."""
    if platform == "x":
        return env.x
    if platform == "bluesky":
        return env.bsky
    return _up_adapter(env, monkeypatch, platform)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["with-the-value-line", "before-the-value-line"])
async def test_a_published_link_bearing_post_opens_its_campaigns_early_window(env, caplog, shape):
    caption = _linked("bluesky") if shape == "with-the-value-line" else _linked_before_the_value_line("bluesky")
    assert caption.endswith(f"Learn more: https://caydexinvest.com/go/bluesky\n\n{_SHORT_DISCLAIMER}")
    pid = env.seed(platform="bluesky", caption=caption)
    env.bsky.outcomes = [Outcome(PUBLISHED, external_id="at://did:plc:abc/app.bsky.feed.post/3k")]
    assert _windows() == {}
    t0 = time.time()
    counters = await pub.publish_cycle()
    t1 = time.time()
    assert counters["published"] == 1 and env.row(pid)["status"] == "published"
    until = _assert_window("bluesky", t0, t1, 240)
    assert abs(until - (time.time() + 240)) < 5           # the spec's tolerance, on top of the bracket
    assert set(_windows()) == {"bluesky"}                 # its own campaign, and nothing else
    assert publish_clock.is_early("bluesky")              # what smart_link.record_hit reads
    iso = datetime.fromtimestamp(until, timezone.utc).isoformat()
    assert _window_lines(caplog) == [
        f"marketing publish clock: campaign=bluesky early window open until {iso} (published) post_id={pid}"]
    assert _clock_warnings(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, caption, kind", [
    ("x", None, PUBLISHED),                                      # the Env's default, link-free caption
    ("x", _X_WITHOUT_LINK, PUBLISHED),                           # composed without allow_x_url
    ("threads", _linked("threads", slug="bluesky"), PUBLISHED),  # ANOTHER platform's link
    ("bluesky", _linked("bluesky", slug="bluesky_early"), PUBLISHED),  # a longer slug is not its link
    ("tiktok", _IN_BIO, SUBMITTED),
    ("instagram", _IN_BIO, PUBLISHED),
], ids=["x-default", "x-no-url", "threads-carrying-bluesky", "bluesky-longer-slug", "tiktok-in-bio",
        "instagram-in-bio"])
async def test_a_post_without_its_own_go_link_never_stamps(env, monkeypatch, caplog, platform, caption, kind):
    adapter = _adapter(env, monkeypatch, platform)
    pid = env.seed(platform=platform, caption=caption)
    adapter.outcomes = [Outcome(PUBLISHED, external_id=f"{platform}-ext-1") if kind == PUBLISHED
                        else Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    counters = await pub.publish_cycle()
    # The outcome WAS recorded (record_outcome ran) — it just opened no window, for any campaign: a
    # caption carrying bluesky's link must not open bluesky's window from a Threads post either.
    assert counters["published" if kind == PUBLISHED else "submitted"] == 1 and len(adapter.sends) == 1
    assert env.row(pid)["status"] == ("published" if kind == PUBLISHED else "queued")
    assert _windows() == {}
    assert _window_lines(caplog) == [] and _clock_warnings(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("caption, stamped", [
    (_X_WITH_LINK, True),                                                        # composed with allow_x_url
    ("Three habits that quietly compound. https://caydexinvest.com/go/x", True),  # the caption's last token
    ("Read more at https://caydexinvest.com/go/x. Educational only.", True),     # punctuation ends a slug
    ("https://caydexinvest.com/go/xyz first, then https://caydexinvest.com/go/x\n\nEducational only.", True),
    (_X_WITH_LINK.replace("/go/x", "/go/xyz"), False),                           # another campaign's slug
    (_X_WITH_LINK.replace("/go/x", "/go/x_early"), False),
    (_X_WITH_LINK.replace("/go/x", "/go/x-2"), False),
    (_X_WITHOUT_LINK, False),
], ids=["with-url", "at-the-end", "before-a-period", "after-a-longer-slug", "go-xyz", "go-x-early", "go-x-2",
        "no-url"])
@pytest.mark.parametrize("allow_urls_now", [False, True], ids=["urls-off-now", "urls-on-now"])
async def test_an_x_post_stamps_only_when_its_caption_carries_go_x(env, monkeypatch, caption, stamped,
                                                                   allow_urls_now):
    """X carries a link only when its caption was COMPOSED with allow_x_url, and the flag can differ by
    publish time — so the caption decides, whatever MARKETING_X_ALLOW_URLS says when it goes out (the
    fake adapter sends either way; the real one would refuse a link while the flag is off)."""
    monkeypatch.setattr(pub.settings, "MARKETING_X_ALLOW_URLS", allow_urls_now)
    pid = env.seed(platform="x", caption=caption)
    t0 = time.time()
    counters = await pub.publish_cycle()
    t1 = time.time()
    assert counters["published"] == 1 and env.row(pid)["status"] == "published"
    if stamped:
        _assert_window("x", t0, t1, 240)
        assert set(_windows()) == {"x"}
    else:
        assert _windows() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, kind, seconds, status", [
    ("linkedin", SUBMITTED, 300, "queued"),     # an async Upload-Post job: live seconds later
    # A YouTube DESCRIPTION written before Batch 1 (2026-10-15) carries /go/youtube; one composed since
    # carries the App Store search line instead and opens nothing (the composed-captions test below).
    ("youtube", SUBMITTED, 300, "queued"),
    ("facebook", PUBLISHED, 240, "published"),  # a synchronous Upload-Post answer: live now
])
async def test_an_upload_post_job_opens_its_window_by_how_it_answered(env, monkeypatch, caplog, platform, kind,
                                                                       seconds, status):
    up = _adapter(env, monkeypatch, platform)
    pid = env.seed(platform=platform, caption=_linked(platform))
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()}) if kind == SUBMITTED
                   else Outcome(PUBLISHED, external_id=f"{platform}-ext-1")]
    t0 = time.time()
    await pub.publish_cycle()
    t1 = time.time()
    assert env.row(pid)["status"] == status
    _assert_window(platform, t0, t1, seconds)
    assert set(_windows()) == {platform}
    lines = _window_lines(caplog)
    assert len(lines) == 1 and f"campaign={platform} " in lines[0] and f"post_id={pid}" in lines[0]
    assert f"({publish_clock.SUBMITTED if kind == SUBMITTED else publish_clock.PUBLISHED})" in lines[0]


@pytest.mark.asyncio
async def test_a_later_post_of_the_campaign_extends_its_window_and_never_shortens_it(env, monkeypatch, caplog):
    """Two link-bearing posts of one campaign minutes apart (a second post, a resend): the window closes
    at the LATER of their two ends. Every send stamps — a window already open is no reason to skip one —
    and a send whose window would end sooner leaves the open one as it is (and logs nothing). The
    clock's wall time is driven here (whole seconds, so the sums are exact)."""
    clock = {"t": float(int(time.time()))}
    monkeypatch.setattr(publish_clock, "_wall", lambda: clock["t"])
    up = _adapter(env, monkeypatch, "linkedin")
    day = mrs.run_date_et().isoformat()
    start = clock["t"]

    first = env.seed(platform="linkedin", caption=_linked("linkedin"), key=f"{day}:linkedin:text")
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    await pub.publish_cycle()
    assert publish_clock.open_until("linkedin") == start + 300

    clock["t"] = start + 30            # a synchronous answer 30 s later: its 240 s would end at +270
    env.seed(platform="linkedin", caption=_linked("linkedin"), key=f"{day}:linkedin:image")
    up.outcomes = [Outcome(PUBLISHED, external_id="li-ext-2")]
    await pub.publish_cycle()
    assert publish_clock.open_until("linkedin") == start + 300

    clock["t"] = start + 100           # 100 s after the first: 240 s from now ends at +340, later
    third = env.seed(platform="linkedin", caption=_linked("linkedin"), key=f"{day}:linkedin:video")
    up.outcomes = [Outcome(PUBLISHED, external_id="li-ext-3")]
    await pub.publish_cycle()
    assert publish_clock.open_until("linkedin") == start + 340
    assert len(up.sends) == 3 and set(_windows()) == {"linkedin"}
    assert [line.rsplit("post_id=", 1)[1] for line in _window_lines(caplog)] == [first, third]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, attempts, status", [
    (Outcome(NOT_SENT, "transport", error="ConnectError: connection refused"), 0, "approved"),
    (Outcome(NOT_SENT, "transport", error="ConnectTimeout"), 2, "failed"),           # at the attempt cap
    (Outcome(REFUSED, "forbidden", error="403 not permitted", alert="failed"), 0, "failed"),
], ids=["not-sent", "not-sent-at-the-cap", "refused"])
async def test_not_sent_and_refused_never_stamp(env, caplog, outcome, attempts, status):
    """Nothing went live: a NOT_SENT provably never left, a REFUSED was a definite no."""
    pid = env.seed(platform="bluesky", caption=_linked("bluesky"), attempts=attempts)
    env.bsky.outcomes = [outcome]
    await pub.publish_cycle()
    assert len(env.bsky.sends) == 1 and env.row(pid)["status"] == status
    assert _windows() == {}
    assert _window_lines(caplog) == [] and _clock_warnings(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["bluesky", "linkedin"])
@pytest.mark.parametrize("answer, category", [
    ("ambiguous", "server"),
    ("adapter-raises", "bug"),          # an adapter that raises is read as AMBIGUOUS
])
async def test_an_ambiguous_send_stamps_the_published_window_and_stays_queued(env, monkeypatch, caplog, platform,
                                                                              answer, category):
    """An unknown outcome may already be live — and its scanners arrive on the post's schedule, not
    on reconcile's — so it opens the PUBLISHED window (240 s, on an Upload-Post outlet too: an unknown
    answer is never read as a job that is still to go live); the row still waits for reconcile."""
    adapter = _adapter(env, monkeypatch, platform)
    pid = env.seed(platform=platform, caption=_linked(platform))
    adapter.outcomes = [Outcome(AMBIGUOUS, "server", error=f"{platform}: HTTP 503") if answer == "ambiguous"
                        else ValueError("our own bug")]
    t0 = time.time()
    counters = await pub.publish_cycle()
    t1 = time.time()
    row = env.row(pid)
    assert counters["unknown"] == 1 and row["status"] == "queued" and len(adapter.sends) == 1
    assert row["metadata"]["publish"]["state"] == "unknown" and row["metadata"]["publish"]["category"] == category
    _assert_window(platform, t0, t1, 240)
    assert set(_windows()) == {platform}
    lines = _window_lines(caplog)
    assert len(lines) == 1 and lines[0].endswith(f"({publish_clock.PUBLISHED}) post_id={pid}"), lines


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, kind, seconds, log_needle", [
    ("x", PUBLISHED, 240, "PUBLISHED BUT LEDGER WRITE FAILED"),
    ("threads", SUBMITTED, 300, "outcome submitted NOT RECORDED"),
])
async def test_a_ledger_failure_after_the_send_still_stamps(env, monkeypatch, caplog, platform, kind, seconds,
                                                           log_needle):
    """The stamp runs BEFORE the ledger write: a post that went out but whose write failed is live all
    the same, and its scanners must still be counted apart."""
    adapter = _adapter(env, monkeypatch, platform)
    pid = env.seed(platform=platform, caption=_X_WITH_LINK if platform == "x" else _linked(platform))
    adapter.outcomes = [Outcome(PUBLISHED, external_id="1840000000000000777") if kind == PUBLISHED
                        else Outcome(SUBMITTED, publish_meta={"upload_post": {"request_id": "k:a1"}})]
    adapter.on_send = lambda _post: env.posts.fail_updates.append(RuntimeError("PostgREST 520"))
    t0 = time.time()
    await pub.publish_cycle()
    t1 = time.time()
    row = env.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "sending"   # the write was lost
    assert any(pid in m for m in _messages(caplog, log_needle, logging.ERROR))
    _assert_window(platform, t0, t1, seconds)
    assert set(_windows()) == {platform}


#: A wall-clock instant as this ledger writes one (`datetime.isoformat()`, UTC).
_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)")


def _comparable(row: Dict[str, Any]) -> Dict[str, Any]:
    """`row` as two posts recorded the same way must agree on it: its identity (its own id wherever it
    appears, its run id and key) and every wall-clock instant are blanked; everything else is kept."""
    pid = row["id"]

    def norm(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items()}
        if isinstance(value, list):
            return [norm(v) for v in value]
        if isinstance(value, str):
            value = value.replace(pid, "<id>")
            return "<instant>" if _INSTANT.fullmatch(value) else value
        return value

    return {**norm(row), "run_id": "<run>", "idempotency_key": "<key>"}


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, kind", [("bluesky", PUBLISHED), ("linkedin", SUBMITTED)])
async def test_a_raising_clock_never_costs_the_publish(env, monkeypatch, caplog, platform, kind):
    """The window is a measurement: a clock that raises is one WARNING naming the post, and the post
    is recorded exactly as it would be without the clock — compared field by field with a control
    post sent the same way with the real clock."""
    adapter = _adapter(env, monkeypatch, platform)

    def answer() -> Outcome:
        return (Outcome(PUBLISHED, external_id="at://did:plc:abc/app.bsky.feed.post/3k") if kind == PUBLISHED
                else Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()}))

    day = mrs.run_date_et().isoformat()     # a key is `<run day>:…` — anything else is never fresh
    control = env.seed(platform=platform, caption=_linked(platform), key=f"{day}:{platform}:text")
    adapter.outcomes = [answer()]
    control_counters = await pub.publish_cycle()
    assert len(adapter.sends) == 1 and publish_clock.open_until(platform) is not None   # the control stamped
    publish_clock.clear()
    caplog.clear()

    real_stamp = publish_clock.stamp
    calls: List[Dict[str, Any]] = []

    def boom(*args: Any, **kwargs: Any) -> bool:
        calls.append(dict(inspect.signature(real_stamp).bind(*args, **kwargs).arguments))
        raise RuntimeError("clock exploded")

    monkeypatch.setattr(publish_clock, "stamp", boom)
    pid = env.seed(platform=platform, caption=_linked(platform), key=f"{day}:{platform}:image")
    adapter.outcomes = [answer()]
    counters = await pub.publish_cycle()
    assert len(adapter.sends) == 2
    assert [(c["campaign"], c["kind"]) for c in calls] == [
        (platform, publish_clock.PUBLISHED if kind == PUBLISHED else publish_clock.SUBMITTED)]   # it WAS reached
    assert counters == control_counters
    assert _comparable(env.row(pid)) == _comparable(env.row(control))
    row = env.row(pid)
    p = row["metadata"]["publish"]
    if kind == PUBLISHED:
        assert counters["published"] == 1 and row["status"] == "published" and p["state"] == "published"
        assert row["external_id"] == "at://did:plc:abc/app.bsky.feed.post/3k" and row["published_at"]
    else:
        assert counters["submitted"] == 1 and row["status"] == "queued" and p["state"] == "submitted"
        assert p["upload_post"] == _up_meta()
    assert p["history"][-1]["kind"] == kind and row["last_error"] is None and row["attempts"] == 1
    _assert_review_kept(row)
    assert _windows() == {}
    warnings = _clock_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert f"post_id={pid}" in warnings[0] and "RuntimeError: clock exploded" in warnings[0]
    assert [r for r in caplog.records if r.name == PUB_LOGGER and r.levelno >= logging.ERROR] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, kind, state", [
    ("bluesky", PUBLISHED, "published"),
    ("bluesky", AMBIGUOUS, "unknown"),
    ("linkedin", SUBMITTED, "submitted"),
])
async def test_the_window_is_open_before_the_outcome_is_written(env, monkeypatch, platform, kind, state):
    """Scanners arrive within seconds of the post going live, and the outcome write may take a retry:
    the window is already open when `record_outcome` writes the outcome — never opened after it, and
    never before the send (the write-ahead claim finds it closed)."""
    adapter = _adapter(env, monkeypatch, platform)
    pid = env.seed(platform=platform, caption=_linked(platform))
    adapter.outcomes = [{PUBLISHED: Outcome(PUBLISHED, external_id=f"{platform}-ext-1"),
                         AMBIGUOUS: Outcome(AMBIGUOUS, "server", error=f"{platform}: HTTP 503"),
                         SUBMITTED: Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})}[kind]]
    writes: List[tuple] = []
    real = env.svc.transition_post

    async def spy(post_id: str, *args: Any, **kwargs: Any) -> Any:
        writes.append((post_id, (kwargs.get("publish") or {}).get("state"), len(adapter.sends),
                       publish_clock.open_until(platform)))
        return await real(post_id, *args, **kwargs)

    monkeypatch.setattr(env.svc, "transition_post", spy)
    await pub.publish_cycle()
    before_send = [w for w in writes if w[2] == 0]      # the claim's write-ahead, if it comes this way
    after_send = [w for w in writes if w[2] == 1]
    assert [w[3] for w in before_send] == [None] * len(before_send), writes
    assert [(p, s) for p, s, _n, _until in after_send] == [(pid, state)], writes   # the one outcome write
    assert after_send[0][3] is not None and after_send[0][3] == publish_clock.open_until(platform)


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["dry_switch", "rehearsal_row", "reconcile_dry_switch", "reconcile_rehearsal_row"])
async def test_a_dry_run_never_stamps(env, monkeypatch, caplog, why):
    """Neither send path sends a rehearsal: not the publish pass, and not reconcile's resend of a post
    confirmed absent (under the switch, or for a row whose `dry_run` is not exactly False)."""
    if why == "dry_switch":
        monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
        env.seed(platform="bluesky", caption=_linked("bluesky"))
        env.seed(platform="x", caption=_X_WITH_LINK)
    elif why == "rehearsal_row":
        env.seed(platform="bluesky", caption=_linked("bluesky"), meta=_review_meta(dry_run=True))
    else:
        pid = env.seed_queued(platform="bluesky", started_ago=700, caption=_linked("bluesky"))
        if why == "reconcile_dry_switch":
            monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
        else:
            env.raw(pid)["metadata"]["dry_run"] = True
        env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    if why.startswith("reconcile"):
        counters = await pub.reconcile_cycle()
        assert counters["absent"] == 1 and counters.get("resent", 0) == 0
        assert len(env.bsky.reconciles) == 1 and env.row(pid)["status"] == "queued"
    else:
        await pub.publish_cycle()
    assert env.bsky.sends == [] and env.x.sends == []
    assert _windows() == {}
    assert _window_lines(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, meta, caption, stamped", [
    ("bluesky", _review_meta(), _linked("bluesky"), True),                    # the live control
    ("bluesky", _review_meta(dry_run=True), _linked("bluesky"), False),       # a rehearsal row
    ("bluesky", _review_meta(dry_run="false"), _linked("bluesky"), False),    # live means exactly False
    ("bluesky", {"review": {"decision": "approved", "by": OWNER, "at": REVIEW_AT}}, _linked("bluesky"), False),
    ("myspace", _review_meta(), _linked("myspace"), False),                   # no post platform: no campaign
    ("bluesky", _review_meta(), None, False),                                 # no caption at all
    ("bluesky", _review_meta(), 123, False),                                  # a caption that is not text
], ids=["live", "rehearsal", "dry-run-string", "no-dry-run-key", "not-a-post-platform", "caption-none",
        "caption-not-str"])
async def test_record_outcome_stamps_only_a_live_row_of_a_post_platform(env, caplog, platform, meta, caption,
                                                                      stamped):
    """`record_outcome` re-checks what the send path already guarantees: rehearsal rows never reach a
    send (the publish query and the reconcile resend both refuse them), and every campaign key is a
    POST platform (smart_link's EARLY_KEYS) — so the clock's own guard is driven directly here."""
    pid = env.seed(platform=platform, status="queued", meta=copy.deepcopy(meta), attempts=1,
                   caption=caption if isinstance(caption, str) else "placeholder")
    if not isinstance(caption, str):
        env.raw(pid)["caption"] = caption
    t0 = time.time()
    state = await pub.record_outcome(env.svc, env.bsky, env.row(pid), Outcome(PUBLISHED, external_id="ext-1"))
    t1 = time.time()
    assert state == "published" and env.row(pid)["status"] == "published"   # the outcome itself always lands
    if stamped:
        _assert_window(platform, t0, t1, 240)
        assert set(_windows()) == {platform}
    else:
        assert _windows() == {}
    assert _clock_warnings(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, kind, seconds, status", [
    ("bluesky", PUBLISHED, 240, "published"),
    ("linkedin", SUBMITTED, 300, "queued"),     # Upload-Post resends with the same request id
])
async def test_a_reconcile_resend_stamps_through_record_outcome(env, monkeypatch, caplog, platform, kind, seconds,
                                                               status):
    """A resend IS a send: the post goes live now, so its window opens now — through `record_outcome`,
    like a first send, by how the platform answered."""
    adapter = _adapter(env, monkeypatch, platform)
    pid = env.seed_queued(platform=platform, started_ago=700, caption=_linked(platform))
    adapter.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    adapter.outcomes = [Outcome(PUBLISHED, external_id=f"{platform}-ext-1") if kind == PUBLISHED
                        else Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    assert _windows() == {}
    t0 = time.time()
    counters = await pub.reconcile_cycle()
    t1 = time.time()
    assert counters["resent"] == 1 and len(adapter.sends) == 1 and env.row(pid)["status"] == status
    _assert_window(platform, t0, t1, seconds)
    assert set(_windows()) == {platform}
    clock_kind = publish_clock.PUBLISHED if kind == PUBLISHED else publish_clock.SUBMITTED
    assert len([m for m in _window_lines(caplog) if m.endswith(f"({clock_kind}) post_id={pid}")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("published_at", [None, "garbage"])
async def test_a_reconcile_found_never_opens_a_window_at_now(env, published_at):
    """A FOUND arrives at least MARKETING_PUBLISH_RECONCILE_AFTER_SECONDS (600 s) after the send —
    after every scanner tail. Its time is unknown (Bluesky reports none) or unreadable here, and the
    publisher writes `now` into `published_at` for it: stamping that would count real taps ten minutes
    later as scanners. Only `record_outcome` (a send) stamps."""
    pid = env.seed_queued(platform="bluesky", started_ago=700, caption=_linked("bluesky"))
    env.bsky.reconcile_results = [ReconcileResult(FOUND, external_id="at://did:plc:abc/app.bsky.feed.post/3k",
                                                  published_at=published_at)]
    counters = await pub.reconcile_cycle()
    assert counters["found"] == 1 and env.row(pid)["status"] == "published" and env.bsky.sends == []
    assert _windows() == {}


#: Model-written bodies for every caption field `post_copy.compose` reads.
_COMPOSE_BODIES = {
    "tiktok": "Three habits that quietly compound over a decade.",
    "youtube_title": "Three habits that compound",
    "youtube_description": "A short lesson on habits that compound over time.",
    "instagram": "Three habits that quietly compound.",
    "facebook": "Three habits that quietly compound over a decade.",
    "x": "Three habits that quietly compound.",
    "threads": "Three habits that quietly compound.",
    "bluesky": "Three habits that quietly compound.",
    "linkedin": "Three habits that quietly compound over a decade.",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("store_state", post_copy.STORE_STATES)
@pytest.mark.parametrize("allow_x_url", [False, True])
async def test_the_composed_captions_open_exactly_the_link_bearing_campaigns(env, monkeypatch, caplog, allow_x_url,
                                                                             store_state):
    """End to end over the REAL composed copy, one post per outlet in one publish pass: bluesky,
    facebook, linkedin and threads carry their own /go link and open their window; TikTok and Instagram
    say "Link in bio."; X opens one only when its caption was composed with allow_x_url. YouTube's
    description carries NO link since Batch 1 (owner decision 6, 2026-10-15: "Search Caydex on the App
    Store." replaced the untappable link, and only while the app is on the store), so it opens none. The
    value line's store state changes nothing else."""
    for platform in ("tiktok", "youtube", "instagram", "facebook", "threads", "linkedin"):
        _up_adapter(env, monkeypatch, platform)
    for platform in post_copy.PLATFORMS:
        composed = post_copy.compose(platform, _COMPOSE_BODIES, category="blueprints", run_date=mrs.run_date_et(),
                                     allow_x_url=allow_x_url, store_state=store_state)
        env.seed(platform=platform, caption=composed.caption, title=composed.title)
        if platform == "youtube":
            assert "/go/" not in composed.caption and not post_copy.carries_go_link("youtube", composed.caption)
            assert (post_copy.SEARCH_LINE in composed.caption) is (store_state in ("preorder", "live"))
    t0 = time.time()
    counters = await pub.publish_cycle()
    t1 = time.time()
    assert counters["published"] == len(post_copy.PLATFORMS) == 8
    expected = {"bluesky", "facebook", "linkedin", "threads"} | ({"x"} if allow_x_url else set())
    assert set(_windows()) == expected
    for campaign in expected:
        _assert_window(campaign, t0, t1, 240)
    assert len(_window_lines(caplog)) == len(expected)
    assert set(_windows()) <= set(POST_PLATFORMS)


# ── the tick: the late steps (measure → run health → weekly digest) ───────────
#
# tests/test_marketing_review_bot.py pins the whole tick's order over all 16 switch combinations with
# every step stubbed. These pin the WIRING of the three late steps: the tick's own wrappers reach the
# module functions (looked up when the step runs), each step's gate and the fail-closed defaults, a
# module that cannot be imported failing its own step alone, and — the REAL measure / health / digest
# behind the real tick — each day's work done once, in order, with no platform call.

#: The early steps, in tick order (each stubbed below as a recorder).
_EARLY = ("expire", "retract", "reconcile", "publish", "review", "feed")
#: The late steps: step → (module under app.services.marketing, function the tick calls).
_LATE = {"measure": ("metrics_service", "measure_cycle"), "health": ("digest_service", "health_cycle"),
         "digest": ("digest_service", "digest_cycle")}
_NY = ZoneInfo("America/New_York")


def _et_time(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    """An America/New_York wall-clock time as an aware UTC datetime."""
    return datetime(y, mo, d, h, mi, tzinfo=_NY).astimezone(timezone.utc)


def _record_tick(monkeypatch, *, late: bool = True,
                 late_result: Optional[Dict[str, Dict[str, int]]] = None) -> List[tuple]:
    """Every step of the tick replaced by a recorder of (step, args, kwargs). The late steps are
    replaced on their MODULES, so the tick's own wrappers (`_measure_step` …) run for real; with
    `late=False` the late steps stay real."""
    calls: List[tuple] = []

    def make(name: str, result: Dict[str, int]):
        async def step(*args: Any, **kwargs: Any) -> Dict[str, int]:
            calls.append((name, args, kwargs))
            return dict(result)
        return step

    monkeypatch.setattr(pub, "_expire_step", make("expire", {"expired": 0}))
    monkeypatch.setattr(pub, "retract_cycle", make("retract", {"retracted": 0}))
    monkeypatch.setattr(pub, "reconcile_cycle", make("reconcile", {"checked": 0}))
    monkeypatch.setattr(pub, "publish_cycle", make("publish", {"published": 0}))
    monkeypatch.setattr(pub.review_service, "review_cycle",
                        make("review", {"pending": 0, "failed": 0, "rate_limited": 0}))
    monkeypatch.setattr(pub.publish_feed, "feed_cycle", make("feed", {"posted": 0}))
    if late:
        for name, (module, fn) in _LATE.items():
            monkeypatch.setattr(importlib.import_module(f"app.services.marketing.{module}"), fn,
                                make(name, (late_result or {}).get(name, {})))
    return calls


def _switches(monkeypatch, *, enabled: bool, bot: bool, metrics: bool, digest: bool) -> None:
    monkeypatch.setattr(pub.review_service, "is_configured", lambda: bot)
    monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", enabled)
    monkeypatch.setattr(pub.settings, "MARKETING_METRICS_ENABLED", metrics)
    monkeypatch.setattr(pub.settings, "MARKETING_DIGEST_ENABLED", digest)


@pytest.mark.asyncio
async def test_with_everything_on_the_late_steps_run_last_in_contract_order(monkeypatch, caplog):
    """measure → health → digest after the feed, so none of them ever delays a post or a review
    message; each is called with no argument (they read the clock themselves); the tick logs a late
    step's counters only when one of them moved."""
    calls = _record_tick(monkeypatch, late_result={"measure": {"ran": 1, "measured": 3},
                                                   "health": {"checked": 0, "sent": 0},
                                                   "digest": {"sent": 1, "failed": 0}})
    _switches(monkeypatch, enabled=True, bot=True, metrics=True, digest=True)
    caplog.set_level(logging.INFO, logger=PUB_LOGGER)
    await pub.publisher_tick()
    assert [name for name, _a, _k in calls] == [*_EARLY, "measure", "health", "digest"]
    assert [(a, k) for name, a, k in calls if name in _LATE] == [((), {})] * 3
    assert _messages(caplog, "marketing publisher measure: ") == [
        "marketing publisher measure: {'ran': 1, 'measured': 3}"]
    assert _messages(caplog, "marketing publisher digest: ") == [
        "marketing publisher digest: {'sent': 1, 'failed': 0}"]
    assert _messages(caplog, "marketing publisher health") == []      # all zero: nothing to say


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled, bot, metrics, digest, late", [
    (True, True, False, True, ["health", "digest"]),     # the metrics switch off: no measure
    (False, True, True, True, ["health", "digest"]),     # publishing off: no measure either
    (True, False, True, True, ["measure"]),              # no bot: no run health, no digest
    (True, True, True, False, ["measure", "health"]),    # the digest switch off
    (False, False, True, True, []),                      # housekeeping only
], ids=["metrics_off", "publishing_off", "bot_off", "digest_off", "all_off"])
async def test_each_late_step_is_skipped_when_its_own_gate_is_off(monkeypatch, enabled, bot, metrics,
                                                                  digest, late):
    calls = _record_tick(monkeypatch)
    _switches(monkeypatch, enabled=enabled, bot=bot, metrics=metrics, digest=digest)
    await pub.publisher_tick()
    early = ["expire", "retract", *(["reconcile", "publish"] if enabled else []),
             *(["review", "feed"] if bot else [])]
    assert [name for name, _a, _k in calls] == early + late


@pytest.mark.asyncio
@pytest.mark.parametrize("bot", [True, False])
async def test_at_the_settings_defaults_only_the_run_health_check_joins_the_tick(monkeypatch, bot):
    """Both new switches are fail-closed: at their DECLARED defaults a live tick adds only the run-health
    check, and only with the bot configured (it has no switch, like the feed's alerts)."""
    from app.config import Settings

    defaults = {name: Settings.model_fields[name].default
                for name in ("MARKETING_METRICS_ENABLED", "MARKETING_DIGEST_ENABLED")}
    assert defaults == {"MARKETING_METRICS_ENABLED": False, "MARKETING_DIGEST_ENABLED": False}
    calls = _record_tick(monkeypatch)
    _switches(monkeypatch, enabled=True, bot=bot, metrics=defaults["MARKETING_METRICS_ENABLED"],
              digest=defaults["MARKETING_DIGEST_ENABLED"])
    await pub.publisher_tick()
    expected = [*_EARLY, "health"] if bot else ["expire", "retract", "reconcile", "publish"]
    assert [name for name, _a, _k in calls] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("broken, failing", [("metrics_service", ("measure",)),
                                             ("digest_service", ("health", "digest"))])
async def test_a_late_step_whose_module_cannot_be_imported_fails_alone(monkeypatch, caplog, broken, failing):
    """A late step imports its module when it runs: a module that cannot be imported fails that step
    (logged with the stack) and never the steps after it."""
    import app.services.marketing as package

    calls = _record_tick(monkeypatch)
    _switches(monkeypatch, enabled=True, bot=True, metrics=True, digest=True)
    monkeypatch.delattr(package, broken)
    monkeypatch.setitem(sys.modules, f"app.services.marketing.{broken}", None)
    caplog.set_level(logging.ERROR, logger=PUB_LOGGER)
    await pub.publisher_tick()
    assert [name for name, _a, _k in calls] == [*_EARLY, *(s for s in _LATE if s not in failing)]
    for step in failing:
        assert [r for r in caplog.records if r.exc_info and r.getMessage().startswith(
            f"marketing publisher step {step} failed (ModuleNotFoundError: ")], step


def _imports_run_at_import_time(tree: ast.AST) -> List[ast.AST]:
    """Every import statement that runs when the module is imported: module level, inside a top-level
    `if` / `try` / class body — anything but a function or lambda body."""
    found: List[ast.AST] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                found.append(child)
            visit(child)

    visit(tree)
    return found


def test_the_publisher_imports_the_late_step_modules_only_when_a_step_runs():
    """`app/main.py` imports this module inside the lifespan without a guard, so an import-time import
    of the measure or digest module would let a broken one stop the whole web app from booting. Each
    late step imports its module when it runs instead (AST, so a comment or docstring cannot satisfy
    it)."""
    names = []
    for node in _imports_run_at_import_time(ast.parse(inspect.getsource(pub))):
        prefix = f"{node.module or ''}." if isinstance(node, ast.ImportFrom) else ""
        names += [f"{prefix}{alias.name}".split(".") for alias in node.names]
    assert names, "sentinel: the module's own imports were not found"
    for module in ("metrics_service", "digest_service"):
        assert not [n for n in names if module in n], module
    for step, module in ((pub._measure_step, "metrics_service"), (pub._health_step, "digest_service"),
                         (pub._digest_step, "digest_service")):
        body = ast.parse(textwrap.dedent(inspect.getsource(step)))
        assert [n for n in ast.walk(body) if isinstance(n, ast.ImportFrom)
                and n.module == "app.services.marketing" and [a.name for a in n.names] == [module]], step


@pytest.mark.asyncio
async def test_the_real_late_steps_do_each_days_work_once_in_order_and_call_no_platform(env, monkeypatch):
    """The REAL measure, run-health and digest steps behind the real tick on Monday 2026-10-05, a
    posting day (the early steps are recorders). Before 06:00 ET nothing is due: no job state is read,
    nothing is claimed or sent. At 23:00 ET the three day claims are taken in contract order: the
    measure step stores Saturday's Bluesky counts (the AppView faked at the client function, the
    publisher's `updated_at` fence untouched); the run-health alert (no run today) and the weekly digest
    go out as two plain-text messages, the digest already carrying the counts measured earlier in the
    SAME tick; no platform client is reached. A later tick that evening does no I/O at all."""
    from app.integrations import bluesky, telegram, upload_post, x_api
    from app.services import notification_jobs
    from app.services.marketing import content_pool
    from app.services.marketing import digest_service as ds
    from app.services.marketing import metrics_service as ms
    from test_marketing_metrics import DigestJobs
    from test_marketing_review_bot import OWNER as CHAT
    from test_marketing_review_bot import SECRET, TOKEN, FakeTelegram

    calls = _record_tick(monkeypatch, late=False)
    clock = {"now": _et_time(2026, 10, 5, 5, 59)}
    jobs = DigestJobs()
    env.fake.tables[ds._JOB_STATE_TABLE] = _Table([("job",)], {"enabled": True}, generated_id=False)
    state_rows = env.fake.tables[ds._JOB_STATE_TABLE].rows

    def finish_scheduled(job: str, **kw: Any) -> None:
        """Migration 147's finish RPC writes the very row the digest reads back: mirrored here."""
        jobs.finish_scheduled(job, **kw)
        r = jobs.rows[job]
        state_rows[:] = [x for x in state_rows if x["job"] != job] + [{
            "job": job, "enabled": r["enabled"], "run_day": r["run_day"] and r["run_day"].isoformat(),
            "claim_at": None, "last_run_at": r["last_run_at"].isoformat(), "last_error": r["last_error"],
            "items_written": r["items_written"]}]

    monkeypatch.setattr(notification_jobs, "claim_scheduled", jobs.claim_scheduled)
    monkeypatch.setattr(notification_jobs, "finish_scheduled", finish_scheduled)
    monkeypatch.setattr(notification_jobs, "scheduled_job_state", jobs.scheduled_job_state)
    for name in ("_DAY_JOBS", "_STOPPED", "_LOGGED", "_LAST"):
        monkeypatch.setattr(ms, name, {})
    for module in (ms, ds):
        monkeypatch.setattr(module, "_now", lambda: clock["now"])
        monkeypatch.setattr(module, "get_marketing_run_service", lambda: env.svc)
    monkeypatch.setattr(content_pool, "eligible_keys", lambda: [f"journey:{i}" for i in range(10)])
    for name, value in (("MARKETING_METRICS_ENABLED", True), ("MARKETING_DIGEST_ENABLED", True),
                        ("MARKETING_APP_STORE_URL", ""), ("MARKETING_TELEGRAM_BOT_TOKEN", TOKEN),
                        ("MARKETING_TELEGRAM_REVIEW_CHAT_ID", CHAT), ("MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET),
                        ("MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com"),
                        # the schedule this test walks (nightly check 22:00, final word 16:00) — pinned, so a
                        # developer .env with another run hour cannot move it
                        ("MARKETING_RUN_HOUR_ET", 16), ("MARKETING_MAX_RUN_ATTEMPTS", 6)):
        monkeypatch.setattr(pub.settings, name, value)
    monkeypatch.setattr(pub.review_service, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(pub.review_service, "_rate_limited_until", 0.0)
    monkeypatch.setattr(ds, "_PACER", pub.review_service._Pacer())
    tg = FakeTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(tg.handler)))
    reached: List[str] = []

    def forbidden(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        raise AssertionError(f"a platform was called: {request.url}")

    for client_module in (x_api, bluesky, upload_post):
        monkeypatch.setattr(client_module, "_client", httpx.AsyncClient(transport=httpx.MockTransport(forbidden)))
    did = "did:plc:caydextick0001"
    uri = f"at://{did}/app.bsky.feed.post/3lbticktest01"
    asked: List[Any] = []

    async def get_posts(uris, **_kw):
        asked.append(list(uris))
        return [{"uri": u, "likeCount": 7, "repostCount": 2, "replyCount": 1, "quoteCount": 0} for u in uris]

    async def get_profile(actor, **_kw):
        asked.append(actor)
        return {"did": did, "handle": "caydex.bsky.social", "followersCount": 42, "followsCount": 3,
                "postsCount": 9}

    monkeypatch.setattr(bluesky, "get_posts", get_posts)
    monkeypatch.setattr(bluesky, "get_profile", get_profile)
    pid = env.seed(platform="bluesky", status="published", meta={"dry_run": False, "publish": {"state": "published"}},
                   external_id=uri, external_url="https://bsky.app/profile/caydex.bsky.social/post/3lbticktest01",
                   created_at="2026-10-03T20:30:00+00:00", updated_at="2026-10-03T21:00:05+00:00",
                   published_at="2026-10-03T21:00:00+00:00", metrics={})
    before = env.row(pid)

    async def tick(at: datetime) -> None:
        clock["now"] = jobs.now = at
        calls.clear()
        await pub.publisher_tick()
        assert [name for name, _a, _k in calls] == list(_EARLY)

    await tick(_et_time(2026, 10, 5, 5, 59))
    assert jobs.state_reads == 0 and jobs.claims == [] and tg.calls == [] and asked == []

    await tick(_et_time(2026, 10, 5, 23, 0))
    monday = date(2026, 10, 5)
    assert jobs.claims == [(ms.JOB_METRICS, monday), (ds.JOB_HEALTH, monday), (ds.JOB_DIGEST, monday)]
    assert [(job, ok) for job, ok, *_ in jobs.finishes] == [(ms.JOB_METRICS, True), (ds.JOB_HEALTH, True),
                                                            (ds.JOB_DIGEST, True)]
    row = env.row(pid)
    assert row["metrics"]["status"] == "ok" and row["metrics"]["measured_day"] == "2026-10-05"
    assert {k: row["metrics"]["last"][k] for k in ("likes", "reposts", "replies", "quotes")} == {
        "likes": 7, "reposts": 2, "replies": 1, "quotes": 0}
    assert row["metrics"]["account"]["followers"] == 42
    assert {k: v for k, v in row.items() if k != "metrics"} == {k: v for k, v in before.items() if k != "metrics"}
    assert [uri] in asked and len(asked) == 2
    sends = tg.of("sendMessage")
    assert len(sends) == 2 and all(s["chat_id"] == CHAT and "parse_mode" not in s and "reply_markup" not in s
                                   for s in sends)
    health, digest = sends[0]["text"], sends[1]["text"]
    assert health.startswith("⚠️ Marketing: no run for Mon 2026-10-05, a posting day — the worker never claimed it.")
    assert digest.startswith("📊 Caydex marketing — weekly digest\nMon 2026-09-28 → Sun 2026-10-04 (ET)\n")
    # The counts AND the follower snapshot measured minutes earlier in the SAME tick reach the digest.
    # (Batch 1, 2026-10-15: the 4-week follows-first scorecard replaced the per-platform engagement
    # block; the week's top post keeps its counts.)
    digest_lines = digest.splitlines()
    assert f"Top post this week: {row['external_url']} (7 likes · 2 reposts · 1 reply · 0 quotes)" in digest_lines
    assert any(line.startswith("• BLUESKY text: 1 post · followers 42") for line in digest_lines)
    assert "\nFollowers: BLUESKY 42 (as of 10-05;" in digest
    # The job the measure step finished minutes earlier, read back by name: ONE post written (its counts
    # and its account snapshot are two writes).
    assert "\nMetrics job: last completed 2026-10-05 · last attempt 10-05 23:00 ET · 1 post written" in digest
    assert ms.last_measure()["outcome"] == ms.OK
    assert reached == []

    reads, n_calls = jobs.state_reads, len(tg.calls)
    await tick(_et_time(2026, 10, 5, 23, 10))
    assert jobs.state_reads == reads and len(jobs.claims) == 3 and len(tg.calls) == n_calls and len(asked) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [None, [], ["measured"], "measured", 0, 7, ("ran", 1)])
async def test_a_late_step_answering_junk_never_stops_the_steps_after_it(monkeypatch, caplog, junk):
    """A step result that is not a counters dict is not logged — and never raises between the steps
    (outside `_step`), so the reports behind it still run."""
    from app.services.marketing import metrics_service

    calls = _record_tick(monkeypatch)
    _switches(monkeypatch, enabled=True, bot=True, metrics=True, digest=True)

    async def measure(*_a: Any, **_k: Any) -> Any:
        calls.append(("measure", (), {}))
        return junk

    monkeypatch.setattr(metrics_service, "measure_cycle", measure)
    caplog.set_level(logging.INFO, logger=PUB_LOGGER)
    await pub.publisher_tick()
    assert [name for name, _a, _k in calls] == [*_EARLY, "measure", "health", "digest"]
    assert _messages(caplog, "marketing publisher measure") == []


@pytest.mark.asyncio
async def test_a_tick_that_raises_between_its_steps_never_ends_the_loop(monkeypatch, caplog):
    """`_step` isolates every step; a raise BETWEEN them (a gate, a log line) is caught by the loop,
    logged with the stack, and the next tick still runs — a dead publisher would never post, retract or
    send a review again. Cancellation still ends it."""
    ticks: List[int] = []
    waits: List[float] = []

    async def tick() -> None:
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("a gate exploded")
        if len(ticks) == 3:
            raise asyncio.CancelledError

    async def no_sleep(_s: float) -> None:
        return None

    async def no_wait(timeout: float) -> bool:
        waits.append(timeout)
        return False

    monkeypatch.setattr(pub, "publisher_tick", tick)
    monkeypatch.setattr(pub.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(pub.publisher_wake, "wait", no_wait)
    monkeypatch.setattr(pub.settings, "MARKETING_PUBLISHER_INTERVAL_SECONDS", 600)
    caplog.set_level(logging.ERROR, logger=PUB_LOGGER)
    with pytest.raises(asyncio.CancelledError):
        await pub.run_marketing_publisher_loop()
    assert len(ticks) == 3 and waits == [600, 600]
    assert [r for r in caplog.records if r.exc_info and r.getMessage().startswith(
        "marketing publisher tick FAILED (RuntimeError: a gate exploded)")]


# ── IMAGE posts and the pre-send charge (drop 1, contract C9) ────────────────────────────────────

X_ALT = 5_000
PIC = PostImage(asset_id="card-1", url="https://xyz.supabase.co/storage/v1/object/public/marketing-media/c.jpg",
                sha256="ab" * 32, size=1234, title="Title", paragraphs=("One.", "Two."))


@pytest.fixture
def pictures(monkeypatch):
    """`outlet_base.load_post_image` scripted: answers in order (a PostImage, a MediaProblem or an
    exception to raise); the default answer is PIC. Every call records the row it was given."""
    state: Dict[str, Any] = {"answers": [], "calls": []}

    async def load(post: Dict[str, Any]) -> Any:
        state["calls"].append(copy.deepcopy(post))
        item = state["answers"].pop(0) if state["answers"] else PIC
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(outlet_base, "load_post_image", load)
    return state


def _seed_image(env: Env, **kw: Any) -> str:
    day = mrs.run_date_et()
    platform = kw.pop("platform", "x")
    return env.seed(platform=platform, format="image", asset_ids=["card-1"],
                    key=f"{day.isoformat()}:{platform}:image", **kw)


@pytest.mark.asyncio
async def test_a_pre_send_charge_is_journaled_after_the_claim_and_before_the_send(env, pictures):
    env.x.pre_send = (("x_media_alt", X_ALT),)
    pid = _seed_image(env)
    seen: Dict[str, Any] = {}
    env.x.on_send = lambda post: seen.update(arg=copy.deepcopy(post), stored=env.row(pid))
    counters = await pub.publish_cycle()
    assert counters["published"] == 1
    # The write-ahead of the billed alt-text call is in the ledger BEFORE the send runs …
    assert _journal(seen["stored"]) == [("x_create", X_POST), ("x_media_alt", X_ALT)]
    assert seen["stored"]["status"] == "queued" and seen["stored"]["cost_micros"] == X_POST + X_ALT
    # … and send() got that fresh row (its fence), not the one the claim returned.
    assert seen["arg"]["updated_at"] == seen["stored"]["updated_at"]
    row = env.row(pid)
    assert row["status"] == "published" and row["attempts"] == 1
    _assert_journal_balances(row)
    _assert_review_kept(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("over, capped", [(0, False), (1, True)])
async def test_the_cap_counts_the_pre_send_charge_before_the_claim(env, pictures, over, capped):
    env.x.pre_send = (("x_media_alt", X_ALT),)
    env.spend_row(env.budget - X_POST - X_ALT + over)   # exactly at the limit is allowed; one micro over is not
    pid = _seed_image(env)
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert counters["capped"] == (1 if capped else 0)
    if capped:
        assert env.x.sends == []
        row = env.row(pid)
        assert row["status"] == "approved" and row["attempts"] == 0 and _journal(row) == []
        assert row["metadata"].get("x_cap_alert_month")       # the once-a-month alert, nothing else
        assert {k: v for k, v in row.items() if k not in ("metadata", "updated_at")} == \
            {k: v for k, v in before.items() if k not in ("metadata", "updated_at")}
    else:
        assert len(env.x.sends) == 1


@pytest.mark.asyncio
async def test_a_failed_pre_send_write_sends_nothing_and_refunds_the_claim(env, pictures, monkeypatch, caplog):
    env.x.pre_send = (("x_media_alt", X_ALT),)
    pid = _seed_image(env)
    real = env.svc.transition_post

    async def flaky(post_id, **kw):
        if kw.get("charges"):
            raise RuntimeError("ledger hiccup")
        return await real(post_id, **kw)

    monkeypatch.setattr(env.svc, "transition_post", flaky)
    counters = await pub.publish_cycle()
    assert env.x.sends == [] and counters["retry"] == 1
    row = env.row(pid)
    assert row["status"] == "approved" and row["attempts"] == 1
    assert row["metadata"]["publish"]["state"] == "not_sent" and row["metadata"]["publish"]["category"] == "ledger"
    assert _journal(row) == [("x_create", X_POST), ("refund_not_sent", -X_POST)] and row["cost_micros"] == 0
    assert _messages(caplog, "pre-send charge x_media_alt NOT journaled", logging.ERROR)


@pytest.mark.asyncio
async def test_a_row_moved_before_its_pre_send_charge_is_left_alone(env, pictures, monkeypatch, caplog):
    env.x.pre_send = (("x_media_alt", X_ALT),)
    pid = _seed_image(env)
    real = env.svc.transition_post

    async def moved(post_id, **kw):
        if kw.get("charges"):
            return None        # someone else moved the row out of `queued`
        return await real(post_id, **kw)

    monkeypatch.setattr(env.svc, "transition_post", moved)
    await pub.publish_cycle()
    assert env.x.sends == []
    row = env.row(pid)
    assert row["status"] == "queued" and _journal(row) == [("x_create", X_POST)]
    assert _messages(caplog, "left `queued` before its pre-send charge", logging.ERROR)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, journal, cost", [
    # Nothing billed (the upload failed): the claim and the alt text are both refunded.
    (Outcome(NOT_SENT, "media", error="upload failed", refund_micros=-(X_POST + X_ALT)),
     [("x_create", X_POST), ("x_media_alt", X_ALT), ("refund_not_sent", -(X_POST + X_ALT))], 0),
    # The create never left, the alt text did: only the post is refunded (not the default full refund).
    (Outcome(NOT_SENT, "transport", error="connect", refund_micros=-X_POST),
     [("x_create", X_POST), ("x_media_alt", X_ALT), ("refund_not_sent", -X_POST)], X_ALT),
    # A credits-depleted alt write: nothing billed, refused.
    (Outcome(REFUSED, "credits", error="402", refund_micros=-(X_POST + X_ALT), alert="failed"),
     [("x_create", X_POST), ("x_media_alt", X_ALT), ("refund", -(X_POST + X_ALT))], 0),
])
async def test_an_adapters_refund_replaces_the_default_not_sent_refund(env, pictures, outcome, journal, cost):
    env.x.pre_send = (("x_media_alt", X_ALT),)
    env.x.outcomes = [outcome]
    pid = _seed_image(env)
    await pub.publish_cycle()
    row = env.row(pid)
    assert _journal(row) == journal and row["cost_micros"] == cost
    assert row["status"] == ("failed" if outcome.kind == REFUSED else "approved")
    _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_a_text_not_sent_still_gets_the_default_transport_refund(env):
    env.x.outcomes = [Outcome(NOT_SENT, "transport", error="connect")]
    pid = env.seed()
    await pub.publish_cycle()
    assert _journal(env.row(pid)) == [("x_create", X_POST), ("refund_not_sent", -X_POST)]


@pytest.mark.asyncio
async def test_an_image_post_is_prepared_with_its_picture_and_claimed_as_read(env, pictures):
    pid = _seed_image(env, platform="bluesky")
    before = env.row(pid)
    await pub.publish_cycle()
    (seen,) = env.bsky.prepare_posts
    assert seen[POST_IMAGE_KEY] is PIC and seen["id"] == pid
    assert pictures["calls"][0]["id"] == pid and POST_IMAGE_KEY not in pictures["calls"][0]
    # The picture rides only on prepare's copy: the claimed row and the ledger never hold it.
    sent_post, _prepared = env.bsky.sends[0]
    assert POST_IMAGE_KEY not in sent_post
    row = env.row(pid)
    assert POST_IMAGE_KEY not in row and POST_IMAGE_KEY not in row["metadata"]
    assert row["status"] == "published" and before["status"] == "approved"


@pytest.mark.asyncio
async def test_a_text_post_never_loads_a_picture(env, pictures):
    env.seed()
    await pub.publish_cycle()
    assert pictures["calls"] == [] and POST_IMAGE_KEY not in env.x.prepare_posts[0]


@pytest.mark.asyncio
async def test_a_picture_that_cannot_be_read_this_tick_leaves_the_row_untouched(env, pictures, caplog):
    pictures["answers"] = [MediaProblem("ledger down")]
    pid = _seed_image(env, platform="bluesky")
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert env.row(pid) == before and env.bsky.prepare_ids == [] and env.bsky.sends == []
    assert counters["retry"] == 1
    assert _messages(caplog, "its picture could not be resolved this tick", logging.WARNING)


@pytest.mark.asyncio
async def test_a_picture_loader_bug_is_logged_and_retried(env, pictures, caplog):
    pictures["answers"] = [RuntimeError("boom")]
    pid = _seed_image(env, platform="bluesky")
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert env.row(pid) == before and counters["retry"] == 1
    bug = [r for r in caplog.records if "post image loader BUG" in r.getMessage()]
    assert bug and bug[0].exc_info is not None


@pytest.mark.asyncio
async def test_a_picture_that_can_never_be_right_refuses_the_post_unclaimed(env, pictures):
    pictures["answers"] = [MediaProblem("asset card-1 is not a usable post image: status 'failed'", definite=True)]
    pid = _seed_image(env, platform="bluesky")
    counters = await pub.publish_cycle()
    assert counters["failed"] == 1 and env.bsky.prepare_ids == [] and env.bsky.sends == []
    row = env.row(pid)
    assert row["status"] == "failed" and row["attempts"] == 0
    assert row["metadata"]["publish"]["category"] == "media"
    assert row["metadata"]["alert_kind"] == "failed" and "not a usable post image" in row["last_error"]


@pytest.mark.asyncio
async def test_a_dry_run_image_post_is_resolved_and_only_logged(env, pictures, monkeypatch, caplog):
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    pid = _seed_image(env, platform="bluesky")
    before = env.row(pid)
    await pub.publish_cycle()
    assert env.row(pid) == before and env.bsky.sends == [] and len(pictures["calls"]) == 1
    assert _messages(caplog, "DRY_RUN: would publish")


@pytest.mark.asyncio
async def test_a_reconcile_resend_of_an_image_post_carries_its_picture(env, pictures):
    pid = env.seed_queued(platform="bluesky", started_ago=700, format="image", asset_ids=["card-1"])
    env.raw(pid)["metadata"]["publish"]["bluesky"] = {"rkey": f"rk-{pid}"}
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["resent"] == 1 and env.bsky.prepare_posts[0][POST_IMAGE_KEY] is PIC
    assert env.row(pid)["status"] == "published"


@pytest.mark.asyncio
async def test_a_reconcile_resend_waits_when_the_picture_cannot_be_read(env, pictures):
    pictures["answers"] = [MediaProblem("ledger down")]
    pid = env.seed_queued(platform="bluesky", started_ago=700, format="image", asset_ids=["card-1"])
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["resent"] == 0 and counters["absent"] == 1 and env.bsky.sends == []
    row = env.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["reconcile"]["last_result"] == ABSENT


# ── a resend deferred only by its picture does not use up the check (review money:F2) ──


def _seed_last_check(env: Env) -> str:
    """A Bluesky image post whose LAST scheduled check (n=3 of 4) is due now."""
    pid = env.seed_queued(platform="bluesky", started_ago=30_000, format="image", asset_ids=["card-1"])
    env.raw(pid)["metadata"]["publish"]["reconcile"] = {
        "n": 3, "last_at": (_now() - timedelta(hours=2)).isoformat(), "last_result": "unknown", "error": "x"}
    return pid


def _rec(env: Env, pid: str) -> Dict[str, Any]:
    return env.row(pid)["metadata"]["publish"]["reconcile"]


def _age_deferral(env: Env, pid: str) -> None:
    """Move the deferral stamp past RESEND_DEFERRAL_RETRY (the check it did not count is due again)."""
    rec = env.raw(pid)["metadata"]["publish"]["reconcile"]
    rec["deferred_at"] = (_now() - pub.RESEND_DEFERRAL_RETRY - timedelta(seconds=1)).isoformat()


@pytest.mark.asyncio
async def test_a_resend_deferred_by_its_picture_keeps_the_check_and_waits_before_the_next(env, pictures, caplog):
    pictures["answers"] = [MediaProblem("ledger down")]
    pid = env.seed_queued(platform="bluesky", started_ago=700, format="image", asset_ids=["card-1"])
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["absent"] == 1 and counters["resent"] == 0 and env.bsky.sends == []
    rec = _rec(env, pid)
    # Not counted: n is still 0 (it was 1 before the fix), and the deferral is stamped with its reason.
    assert rec["n"] == 0 and rec["last_result"] == ABSENT and rec["resend_deferrals"] == 1
    assert rec["deferred_at"] and "ledger down" in rec["error"]
    assert _messages(caplog, "resend deferred (1 of 3)", logging.WARNING)
    _assert_journal_balances(env.row(pid))
    # The next tick inside RESEND_DEFERRAL_RETRY does not re-read the platform.
    snapshot = env.row(pid)
    await pub.reconcile_cycle()
    assert len(env.bsky.reconciles) == 1 and env.row(pid) == snapshot


@pytest.mark.asyncio
async def test_a_last_check_deferred_by_its_picture_resends_once_the_picture_reads(env, pictures):
    pictures["answers"] = [MediaProblem("ledger down")]        # then PIC (the default answer)
    pid = _seed_last_check(env)
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True), ReconcileResult(ABSENT, resend_safe=True)]
    await pub.reconcile_cycle()
    assert _rec(env, pid)["n"] == 3 and env.bsky.sends == []    # before the fix: n=4 → escalated next tick
    _age_deferral(env, pid)
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 0 and counters["resent"] == 1
    assert len(env.bsky.sends) == 1 and env.bsky.prepare_posts[-1][POST_IMAGE_KEY] is PIC
    row = env.row(pid)
    assert row["status"] == "published" and "alert_kind" not in row["metadata"]
    assert row["metadata"]["publish"]["resends"] == 1


@pytest.mark.asyncio
async def test_a_picture_that_never_reads_still_reaches_the_owner_after_the_bounded_deferrals(env, pictures):
    limit = pub.MAX_RESEND_DEFERRALS
    pictures["answers"] = [MediaProblem("ledger down") for _ in range(limit + 1)]
    pid = _seed_last_check(env)
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True) for _ in range(limit + 1)]
    for k in range(1, limit + 1):
        counters = await pub.reconcile_cycle()
        assert counters["escalated"] == 0
        rec = _rec(env, pid)
        assert rec["n"] == 3 and rec["resend_deferrals"] == k
        _age_deferral(env, pid)
    # The write-ahead carries the count, so a check that dies mid-way can never reset the bound.
    assert env.bsky.reconciles[-1]["metadata"]["publish"]["reconcile"]["resend_deferrals"] == limit - 1
    await pub.reconcile_cycle()                                   # one deferral past the bound: it counts
    rec = _rec(env, pid)
    assert rec["n"] == 4 and rec["resend_deferrals"] == limit + 1 and "deferred_at" not in rec
    counters = await pub.reconcile_cycle()                        # the schedule is spent → the owner
    assert counters["escalated"] == 1 and len(env.bsky.reconciles) == limit + 1 and env.bsky.sends == []
    row = env.row(pid)
    assert row["metadata"]["alert_kind"] == "unknown"
    assert "last check: absent" in row["metadata"]["alert_text"] and "ledger down" in row["metadata"]["alert_text"]
    _assert_journal_balances(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", ["2", -1, True, 2.5, [1]])
async def test_a_corrupt_deferral_count_reads_as_spent(env, pictures, junk):
    pictures["answers"] = [MediaProblem("ledger down")]
    pid = _seed_last_check(env)
    env.raw(pid)["metadata"]["publish"]["reconcile"]["resend_deferrals"] = junk
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    await pub.reconcile_cycle()
    rec = _rec(env, pid)
    assert rec["n"] == 4 and rec["resend_deferrals"] == pub.MAX_RESEND_DEFERRALS + 1 and "deferred_at" not in rec


@pytest.mark.asyncio
async def test_an_uncounted_deferral_past_the_schedule_is_checked_again_not_escalated(env, pictures):
    # A deferral on an extra check past the schedule (the PENDING re-poll) keeps n at the schedule's
    # length: the stamp, not the counter, says that check is still owed.
    pid = _seed_last_check(env)
    env.raw(pid)["metadata"]["publish"]["reconcile"].update(
        n=4, last_result=ABSENT, error="resend deferred, the picture could not be read: ledger down",
        resend_deferrals=1, deferred_at=(_now() - timedelta(minutes=11)).isoformat())
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 0 and counters["resent"] == 1 and env.row(pid)["status"] == "published"


@pytest.mark.asyncio
async def test_a_deferral_stamp_in_the_future_never_holds_the_post_back(env, pictures):
    pid = _seed_last_check(env)
    env.raw(pid)["metadata"]["publish"]["reconcile"]["deferred_at"] = (_now() + timedelta(days=30)).isoformat()
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["resent"] == 1 and env.row(pid)["status"] == "published"


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["definite_picture", "pre_send_charge"])
async def test_a_reconcile_resend_that_cannot_be_prepared_goes_to_the_owner(env, pictures, why):
    if why == "definite_picture":
        pictures["answers"] = [MediaProblem("no asset card-1 in the ledger", definite=True)]
    else:
        env.bsky.pre_send = (("bluesky_extra", 1),)
    pid = env.seed_queued(platform="bluesky", started_ago=700, format="image", asset_ids=["card-1"])
    env.bsky.reconcile_results = [ReconcileResult(ABSENT, resend_safe=True)]
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 1 and env.bsky.sends == []
    assert env.row(pid)["metadata"]["alert_kind"] == "unknown"


# ── Batch 1 (2026-10-15): the X media upload is its own pre-send charge — several charges, ONE write ───
#
# An X image post makes two calls X may bill before the post itself: the media upload (journaled
# `x_media_upload` at MARKETING_X_MEDIA_UPLOAD_MICROS — X prices no upload, one forum report says it is
# billed) and the alt text (`x_media_alt`, $0.005). The publisher journals both in ONE fenced write after
# the claim and before the send (all or none), the X cap counts both before the claim, and the cycle's
# running total adds both.

X_UPLOAD = 15_000        # outlet_x's default MARKETING_X_MEDIA_UPLOAD_MICROS
X_IMAGE = 200_000        # the default MARKETING_X_IMAGE_POST_MICROS: what an X image post's claim reserves
UPLOAD_AND_ALT = (("x_media_upload", X_UPLOAD), ("x_media_alt", X_ALT))


def _spy_post_updates(env: Env, monkeypatch) -> List[Dict[str, Any]]:
    """Every UPDATE payload the ledger sends to marketing_posts, in order."""
    table = env.posts
    real = table.update
    calls: List[Dict[str, Any]] = []

    def spy(payload):
        calls.append(copy.deepcopy(payload))
        return real(payload)

    monkeypatch.setattr(table, "update", spy)
    return calls


@pytest.mark.asyncio
async def test_every_pre_send_charge_is_journaled_in_one_write_after_the_claim_and_before_the_send(
        env, pictures, monkeypatch):
    env.x.pre_send = UPLOAD_AND_ALT
    pid = _seed_image(env)
    seen: Dict[str, Any] = {}
    env.x.on_send = lambda post: seen.update(arg=copy.deepcopy(post), stored=env.row(pid))
    calls = _spy_post_updates(env, monkeypatch)
    counters = await pub.publish_cycle()
    assert counters["published"] == 1
    # The claim, ONE pre-send write carrying both charges, the outcome — never a write per charge.
    assert len(calls) == 3
    pre = calls[1]["metadata"]["charges"]
    assert [(c["op"], c["micros"]) for c in pre] == [("x_create", X_POST), *UPLOAD_AND_ALT]
    assert pre[1]["at"] == pre[2]["at"] == calls[1]["updated_at"]        # one write, one instant
    assert calls[1]["cost_micros"] == X_POST + X_UPLOAD + X_ALT
    # Both are in the ledger BEFORE the send runs, and send() got that row (its fence).
    assert _journal(seen["stored"]) == [("x_create", X_POST), *UPLOAD_AND_ALT]
    assert seen["stored"]["status"] == "queued" and seen["arg"]["updated_at"] == seen["stored"]["updated_at"]
    row = env.row(pid)
    assert row["status"] == "published" and row["cost_micros"] == X_POST + X_UPLOAD + X_ALT
    _assert_journal_balances(row)
    _assert_review_kept(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("room, capped", [
    (X_POST + X_UPLOAD + X_ALT, False),         # exactly the reserve plus BOTH charges: allowed
    (X_POST + X_UPLOAD + X_ALT - 1, True),      # one micro short
    (X_POST + X_UPLOAD, True),                  # room for the FIRST charge only (the legacy single view)
    (X_POST + X_ALT, True),                     # room for the drop-1 alt text only
])
async def test_the_cap_counts_every_pre_send_charge_before_the_claim(env, pictures, room, capped):
    env.x.pre_send = UPLOAD_AND_ALT
    env.spend_row(env.budget - room)
    pid = _seed_image(env)
    counters = await pub.publish_cycle()
    assert counters["capped"] == (1 if capped else 0)
    row = env.row(pid)
    if capped:
        assert env.x.sends == [] and row["status"] == "approved" and row["attempts"] == 0 and _journal(row) == []
    else:
        assert len(env.x.sends) == 1 and row["status"] == "published"
        _assert_journal_balances(row)


@pytest.mark.asyncio
async def test_the_cycles_running_total_adds_every_pre_send_charge(env, pictures):
    """Two X image posts in one cycle (yesterday's and today's are both fresh): the second is capped once
    the first's claim AND both its pre-send charges used the room. A running total that added only the
    claim — or only one charge — would let it through."""
    env.x.pre_send = UPLOAD_AND_ALT
    each = X_POST + X_UPLOAD + X_ALT
    env.spend_row(env.budget - 2 * each + 1)
    _seed_image(env)
    day = mrs.run_date_et() - timedelta(days=1)
    env.seed(format="image", asset_ids=["card-1"], day=day, key=f"{day.isoformat()}:x:image")
    counters = await pub.publish_cycle()
    assert (counters["published"], counters["capped"], len(env.x.sends)) == (1, 1, 1)


@pytest.mark.asyncio
async def test_a_failed_pre_send_write_journals_none_of_its_charges(env, pictures, monkeypatch, caplog):
    env.x.pre_send = UPLOAD_AND_ALT
    pid = _seed_image(env)
    real = env.svc.transition_post

    async def flaky(post_id, **kw):
        if kw.get("charges"):
            raise RuntimeError("ledger hiccup")
        return await real(post_id, **kw)

    monkeypatch.setattr(env.svc, "transition_post", flaky)
    counters = await pub.publish_cycle()
    assert env.x.sends == [] and counters["retry"] == 1
    row = env.row(pid)
    assert row["status"] == "approved" and row["metadata"]["publish"]["category"] == "ledger"
    assert _journal(row) == [("x_create", X_POST), ("refund_not_sent", -X_POST)] and row["cost_micros"] == 0
    assert "x_media_upload+x_media_alt" in row["last_error"]
    assert _messages(caplog, "pre-send charge x_media_upload+x_media_alt NOT journaled", logging.ERROR)


@pytest.mark.asyncio
async def test_an_adapter_with_a_malformed_pre_send_charge_is_a_prepare_bug_never_claimed(env, pictures, caplog):
    """The drop-1 shape — a bare (op, micros) pair — is refused by `Prepared` itself: a prepare() BUG,
    logged with its stack, the post skipped BEFORE any claim, charge or platform call."""
    env.x.pre_send = ("x_media_alt", X_ALT)
    pid = _seed_image(env)
    before = env.row(pid)
    counters = await pub.publish_cycle()
    assert counters["skipped"] == 1 and env.x.sends == [] and env.row(pid) == before
    bug = [r for r in caplog.records if "prepare() BUG" in r.getMessage()]
    assert bug and bug[0].exc_info is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, cost", [
    # Nothing billed (the upload never left / was turned away at the gate): everything refunded.
    (Outcome(NOT_SENT, "transport", error="connect", refund_micros=-(X_POST + X_UPLOAD + X_ALT)), 0),
    # An unknown or refused upload: its charge stays, the post and the alt text are refunded.
    (Outcome(NOT_SENT, "media", error="timeout", refund_micros=-(X_POST + X_ALT)), X_UPLOAD),
    # A failed alt-text write after the upload: both may be billed.
    (Outcome(NOT_SENT, "media", error="alt 503", refund_micros=-X_POST), X_UPLOAD + X_ALT),
    # 402 on the upload: nothing billed, refused.
    (Outcome(REFUSED, "credits", error="402", refund_micros=-(X_POST + X_UPLOAD + X_ALT), alert="failed"), 0),
])
async def test_an_image_posts_refund_leaves_exactly_the_calls_that_may_be_billed(env, pictures, outcome, cost):
    env.x.pre_send = UPLOAD_AND_ALT
    env.x.outcomes = [outcome]
    pid = _seed_image(env)
    await pub.publish_cycle()
    row = env.row(pid)
    op = "refund" if outcome.kind == REFUSED else "refund_not_sent"
    assert _journal(row) == [("x_create", X_POST), *UPLOAD_AND_ALT, (op, outcome.refund_micros)]
    assert row["cost_micros"] == cost and row["status"] == ("failed" if outcome.kind == REFUSED else "approved")
    _assert_journal_balances(row)


# ── the REAL X adapter behind the real publisher: what stays journaled after each outcome ──────────────


@pytest.fixture
def real_x(env, pictures, monkeypatch) -> Dict[str, Any]:
    """The real `outlet_x.ADAPTER` registered for X, X images on at the default prices, the picture
    download and the three X calls scripted at the client functions: `script[step]` (upload / alt /
    create) is an exception FACTORY to raise there, or None to succeed."""
    monkeypatch.setitem(outlets.ADAPTERS, "x", outlet_x.ADAPTER)
    for name, value in (("MARKETING_X_IMAGES", True), ("MARKETING_X_ALLOW_URLS", False),
                        ("MARKETING_X_MADE_WITH_AI", True), ("MARKETING_X_IMAGE_POST_MICROS", X_IMAGE),
                        ("MARKETING_X_MEDIA_UPLOAD_MICROS", X_UPLOAD)):
        monkeypatch.setattr(outlet_x.settings, name, value)
    script: Dict[str, Any] = {"upload": None, "alt": None, "create": None, "calls": []}

    def step(name: str, ok: Dict[str, Any]) -> Dict[str, Any]:
        script["calls"].append(name)
        if script[name] is not None:
            raise script[name]()
        return ok

    async def fetch(url, *, size, sha256):
        script["calls"].append("download")
        return b"\xff\xd8 the reviewed picture \xff\xd9"

    async def upload_media(data, **_kw):
        return step("upload", {"id": "1880000000000000001", "media_key": None, "size": len(data), "state": None})

    async def create_media_metadata(media_id, *, alt_text):
        return step("alt", {"id": media_id})

    async def create_post(text, *, made_with_ai=False, media_ids=None):
        return step("create", {"id": "1790000000000000001", "text": text, "errors": []})

    monkeypatch.setattr(outlet_base, "fetch_post_image", fetch)
    monkeypatch.setattr(x_api, "upload_media", upload_media)
    monkeypatch.setattr(x_api, "create_media_metadata", create_media_metadata)
    monkeypatch.setattr(x_api, "create_post", create_post)
    return script


def _xe(cls_name: str, status: Optional[int] = None) -> Callable[[], Exception]:
    """A factory for one x_api exception (built when raised, never shared between cases)."""
    def make() -> Exception:
        return getattr(x_api, cls_name)(f"x scripted {cls_name}", method="scripted", status=status)
    return make


_ALL_X = X_IMAGE + X_UPLOAD + X_ALT

#: (the failing step, its exception, the row's status after the tick, the calls made, what stays journaled)
_REAL_X_MONEY = [
    pytest.param(None, None, "published", ["download", "upload", "alt", "create"], _ALL_X, id="published"),
    pytest.param("upload", _xe("XApiNotSentError"), "approved", ["download", "upload"], 0, id="upload-not-sent"),
    pytest.param("upload", _xe("XApiRefusedError"), "approved", ["download", "upload"], 0,
                 id="upload-refused-before-sending"),
    pytest.param("upload", _xe("XApiRateLimitError", 429), "approved", ["download", "upload"], 0, id="upload-429"),
    pytest.param("upload", _xe("XApiAuthError", 401), "approved", ["download", "upload"], 0, id="upload-401"),
    pytest.param("upload", _xe("XApiCreditsDepletedError", 402), "failed", ["download", "upload"], 0,
                 id="upload-402"),
    pytest.param("upload", _xe("XApiAmbiguousError", 503), "approved", ["download", "upload"], X_UPLOAD,
                 id="upload-ambiguous"),
    pytest.param("upload", _xe("XApiRefusedError", 400), "approved", ["download", "upload"], X_UPLOAD,
                 id="upload-400"),
    pytest.param("upload", _xe("XApiForbiddenError", 403), "approved", ["download", "upload"], X_UPLOAD,
                 id="upload-403"),
    pytest.param("alt", _xe("XApiNotSentError"), "approved", ["download", "upload", "alt"], X_UPLOAD,
                 id="alt-not-sent"),
    pytest.param("alt", _xe("XApiCreditsDepletedError", 402), "failed", ["download", "upload", "alt"], X_UPLOAD,
                 id="alt-402"),
    pytest.param("alt", _xe("XApiAmbiguousError", 503), "approved", ["download", "upload", "alt"],
                 X_UPLOAD + X_ALT, id="alt-ambiguous"),
    pytest.param("create", _xe("XApiNotSentError"), "approved", ["download", "upload", "alt", "create"],
                 X_UPLOAD + X_ALT, id="create-not-sent"),
    pytest.param("create", _xe("XApiCreditsDepletedError", 402), "failed", ["download", "upload", "alt", "create"],
                 X_UPLOAD + X_ALT, id="create-402"),
    pytest.param("create", _xe("XApiRateLimitError", 429), "approved", ["download", "upload", "alt", "create"],
                 _ALL_X, id="create-429"),
    pytest.param("create", _xe("XApiAmbiguousError", 503), "queued", ["download", "upload", "alt", "create"],
                 _ALL_X, id="create-ambiguous"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("failing, error, status, calls, kept", _REAL_X_MONEY)
async def test_the_real_x_adapter_leaves_exactly_the_billable_calls_journaled(env, real_x, failing, error, status,
                                                                             calls, kept):
    """End to end over the real adapter and the real ledger: the claim journals the post ($0.20), the
    pre-send write the upload ($0.015) and the alt text ($0.005), and the outcome's refund leaves
    journaled exactly the calls that may have been billed — the X cap's ledger of record."""
    if failing is not None:
        real_x[failing] = error
    pid = _seed_image(env)
    await pub.publish_cycle()
    row = env.row(pid)
    assert real_x["calls"] == calls and row["status"] == status
    journal = _journal(row)
    assert journal[:3] == [("x_create", X_IMAGE), ("x_media_upload", X_UPLOAD), ("x_media_alt", X_ALT)]
    assert row["cost_micros"] == kept
    assert len(journal) == (3 if kept == _ALL_X else 4)        # one refund entry, only when one is due
    assert row["metadata"]["publish"]["x"]["upload_micros"] == X_UPLOAD
    _assert_journal_balances(row)


# ── run_service.transition_post(charges=…): several journal entries in ONE fenced write ──────────────


@pytest.mark.asyncio
async def test_transition_post_journals_several_charges_in_one_write_in_order(env, monkeypatch):
    t0 = "2026-10-01T00:00:00+00:00"
    pid = env.seed(status="queued", cost_micros=15_000,
                   meta=_review_meta(charges=[{"at": t0, "op": "x_create", "micros": 15_000}]))
    calls = _spy_post_updates(env, monkeypatch)
    out = await env.svc.transition_post(pid, expect_status="queued", observed=env.row(pid),
                                        charges=[("x_media_upload", 15_000), ("x_media_alt", 5_000),
                                                 ("x_read_correction", -2_000, t0)])
    assert len(calls) == 1
    assert _journal(out) == [("x_create", 15_000), ("x_media_upload", 15_000), ("x_media_alt", 5_000),
                             ("x_read_correction", -2_000)]
    entries = out["metadata"]["charges"]
    assert entries[1]["at"] == entries[2]["at"] == out["updated_at"]       # the write's own instant
    assert entries[3]["at"] == t0                                            # a reversal keeps its date
    assert out["cost_micros"] == 15_000 + 15_000 + 5_000 - 2_000
    _assert_journal_balances(out)
    _assert_review_kept(out)


@pytest.mark.asyncio
async def test_transition_post_charges_after_a_lost_fence_land_once_on_the_fresh_row(env, monkeypatch):
    pid = env.seed(status="queued")
    seen = env.row(pid)
    live = env.raw(pid)
    live["updated_at"] = _now().isoformat()                       # someone wrote in between …
    live["metadata"] = {**live["metadata"], "posted_notified_at": "stamped"}
    calls = _spy_post_updates(env, monkeypatch)
    out = await env.svc.transition_post(pid, expect_status="queued", observed=seen, retries=1,
                                        charges=UPLOAD_AND_ALT)
    assert len(calls) == 2                                        # the stale fence lost, the fresh one won
    assert _journal(out) == list(UPLOAD_AND_ALT) and out["cost_micros"] == X_UPLOAD + X_ALT
    assert out["metadata"]["posted_notified_at"] == "stamped"    # … and that write is kept


@pytest.mark.asyncio
@pytest.mark.parametrize("charges", [(), [], None])
async def test_transition_post_without_charges_journals_nothing_and_leaves_the_cost_alone(env, monkeypatch,
                                                                                          charges):
    pid = env.seed(status="queued", cost_micros=7)
    calls = _spy_post_updates(env, monkeypatch)
    out = await env.svc.transition_post(pid, expect_status="queued", observed=env.row(pid), charges=charges,
                                        meta={"note": 1})
    assert len(calls) == 1 and "cost_micros" not in calls[0]
    assert "charges" not in out["metadata"] and out["cost_micros"] == 7 and out["metadata"]["note"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs, match", [
    ({"charge": ("x_create", 15_000), "charges": [("x_media_alt", 5_000)]}, "charge OR charges"),
    ({"charges": [("x_media_alt", 5_000)], "cost_micros": 0}, "a charge OR cost_micros"),
    ({"charges": ("x_media_alt", 5_000)}, r"is not \(op, micros\)"),       # a bare pair, not a list of pairs
    ({"charges": "x_media_alt"}, "must be a list"),
    ({"charges": {"x_media_alt": 5_000}}, "must be a list"),
    ({"charges": 5_000}, "must be a list"),
    ({"charges": [("x_media_alt",)]}, r"is not \(op, micros\)"),
    ({"charges": [("x_media_alt", 5_000, None, "extra")]}, r"is not \(op, micros\)"),
    ({"charges": [("", 5_000)]}, "non-empty string"),
    ({"charges": [("   ", 5_000)]}, "non-empty string"),
    ({"charges": [(None, 5_000)]}, "non-empty string"),
    ({"charges": [("x" * 65, 5_000)]}, "non-empty string"),
    ({"charges": [("x_media_alt", True)]}, "not an int"),
    ({"charges": [("x_media_alt", 5_000.0)]}, "not an int"),
    ({"charges": [("x_media_alt", "5000")]}, "not an int"),
    ({"charges": [("x_media_alt", None)]}, "not an int"),
    ({"charges": [("x_media_upload", 15_000), ("x_media_alt", False)]}, "not an int"),   # one bad entry: none land
    # The single-charge path is checked by the same rule now (it used to journal int(True) == 1).
    ({"charge": ("x_create", True)}, "not an int"),
    ({"charge": ("x_create", 1.5)}, "not an int"),
    ({"charge": ()}, r"is not \(op, micros\)"),
])
async def test_transition_post_refuses_a_malformed_charge_before_any_io(env, monkeypatch, kwargs, match):
    pid = env.seed(status="queued")
    before = env.row(pid)
    calls = _spy_post_updates(env, monkeypatch)
    with pytest.raises(ValueError, match=match):
        await env.svc.transition_post(pid, expect_status="queued", observed=env.row(pid), **kwargs)
    assert calls == [] and env.row(pid) == before


def test_journal_entries_reads_one_charge_or_a_list_and_keeps_a_reversals_date():
    assert mrs.journal_entries(("x_create", 15_000)) == (("x_create", 15_000, None),)
    assert mrs.journal_entries(None, [["x_media_upload", 15_000], ("x_read_correction", -1, "t0")]) == (
        ("x_media_upload", 15_000, None), ("x_read_correction", -1, "t0"))
    assert mrs.journal_entries(("refund", -5, "")) == (("refund", -5, None),)          # empty = the write's time
    assert mrs.journal_entries(None, ()) == mrs.journal_entries() == mrs.journal_entries(None, None) == ()
    assert mrs.journal_entries(("x" * 64, 0)) == (("x" * 64, 0, None),)


# ── Batch 3 (2026-10-20, D8): PRIMARY FIRST — an EXTRA video waits for its platform's picture/text post ──────
#
# Facebook / LinkedIn / Threads may post the run's video as a SECOND post: the pair (platform, "video") alone
# makes it an extra (`schemas.is_extra_format`, no stamp). The publisher claims it only once that platform's
# PRIMARY post of the same run has left pending_review / approved / queued (`publisher_service._PRIMARY_OPEN`),
# read ONCE per extra at its own turn in the pass (so a primary sent earlier in the same pass counts as sent);
# a read that fails holds it too. A held extra counts as `retry`, is written nothing, and expires with its day.
# A primary post never makes the read.

from app.schemas.marketing import is_extra_format  # noqa: E402
from test_marketing_run_service import _Query  # noqa: E402

B3_VIDEO = "33333333-3333-4333-8333-333333333333"


def _held_lines(caplog, platform: str = "facebook") -> List[str]:
    return [r.getMessage() for r in caplog.records if r.name == PUB_LOGGER and r.levelno == logging.INFO
            and f"HELD: waits for its {platform} picture/text post" in r.getMessage()]


def _extra_pair(env: Env, monkeypatch, *, platform: str = "facebook", primary: Optional[str] = "approved",
                primary_format: str = "text", primary_publish: Optional[Dict[str, Any]] = None,
                adapter: Optional[FakeAdapter] = None, day: Optional[date] = None,
                primary_caption: Optional[str] = None, extra_caption: Optional[str] = None,
                extra_status: str = "approved") -> tuple:
    """(adapter, primary id or None, extra id, run id): the run's PRIMARY post on `platform` — in status
    `primary`, or none at all — seeded FIRST (older, as create_posts records it), then its EXTRA video on the run's
    video, both behind one Upload-Post-shaped fake adapter (registered and enabled)."""
    adapter = adapter or _up_adapter(env, monkeypatch, platform)
    run_id = str(uuid.uuid4())
    day_s = (day or mrs.run_date_et()).isoformat()
    pid = None
    if primary is not None:
        meta = _review_meta(publish=primary_publish) if primary_publish is not None else _review_meta()
        pid = env.seed(platform=platform, status=primary, format=primary_format, run_id=run_id, meta=meta,
                       key=f"{day_s}:{platform}:{primary_format}", caption=primary_caption,
                       asset_ids=["card-1"] if primary_format == "image" else [],
                       approved_by=None if primary == "pending_review" else OWNER)
    extra = env.seed(platform=platform, status=extra_status, format="video", run_id=run_id,
                     key=f"{day_s}:{platform}:video", asset_ids=[B3_VIDEO], caption=extra_caption,
                     approved_by=None if extra_status == "pending_review" else OWNER)
    return adapter, pid, extra, run_id


def _future(minutes: int = 60) -> str:
    return (_now() + timedelta(minutes=minutes)).isoformat()


# (primary status, its metadata.publish, what the primary's own send answers this pass, the extra is held)
_PAIR_CASES = {
    "pending_review": ("pending_review", None, None, True),
    "approved-then-submitted-this-pass": ("approved", None, "submitted", True),
    "approved-then-not-sent-this-pass": ("approved", None, "not_sent", True),
    "approved-backed-off": ("approved", {"next_attempt_at": "FUTURE"}, None, True),
    "queued-submitted": ("queued", {"state": "submitted"}, None, True),
    "queued-unknown": ("queued", {"state": "unknown"}, None, True),
    "queued-escalated": ("queued", {"state": "escalated"}, None, True),
    "approved-then-published-this-pass": ("approved", None, "published", False),
    "published": ("published", {"state": "published"}, None, False),
    "failed": ("failed", {"state": "refused"}, None, False),
    "rejected": ("rejected", None, None, False),
    "skipped-expired": ("skipped", None, None, False),
    "retracted": ("retracted", {"state": "published"}, None, False),
    "no-primary": (None, None, None, False),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_format", ["text", "image"])
@pytest.mark.parametrize("case", list(_PAIR_CASES))
async def test_batch3_an_extra_waits_for_its_primary(env, monkeypatch, caplog, pictures, case, primary_format):
    status, publish, answer, held = _PAIR_CASES[case]
    if publish and publish.get("next_attempt_at") == "FUTURE":
        publish = {"next_attempt_at": _future()}
    up, primary, extra, _run = _extra_pair(env, monkeypatch, primary=status, primary_format=primary_format,
                                           primary_publish=publish)
    if answer == "submitted":
        up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    elif answer == "not_sent":
        up.outcomes = [Outcome(NOT_SENT, "transport", error="ConnectError: connection refused")]
    before = env.row(extra)
    counters = await pub.publish_cycle()
    primary_sent = answer is not None
    assert [p for p in up.prepare_ids if p == primary] == ([primary] if primary_sent else [])
    if held:
        assert env.row(extra) == before                               # nothing written: no claim, no stamp
        assert extra not in up.prepare_ids and all(s[0]["id"] != extra for s in up.sends)
        held_lines = _held_lines(caplog)
        assert len(held_lines) == 1 and f"post_id={extra} " in held_lines[0]
        # one `retry` for the held extra (plus the primary's own, when it went back to approved or waits)
        assert counters["retry"] == 1 + (answer == "not_sent") + (case == "approved-backed-off")
    else:
        assert env.row(extra)["status"] == "published" and env.row(extra)["attempts"] == 1
        assert [s[0]["id"] for s in up.sends][-1] == extra             # the extra goes AFTER its primary
        assert _held_lines(caplog) == []
    if primary is not None and not primary_sent:
        assert primary not in up.prepare_ids                          # an untouched primary stays untouched


@pytest.mark.asyncio
async def test_batch3_only_its_own_run_and_platform_hold_an_extra(env, monkeypatch, caplog):
    """The read is the extra's own run on its own platform: an open primary of ANOTHER run on that platform, an
    open post of the same run on ANOTHER platform, and another extra of the run never hold it."""
    up, primary, extra, run_id = _extra_pair(env, monkeypatch, primary="published")
    day = mrs.run_date_et().isoformat()
    env.seed(platform="facebook", status="pending_review", run_id=str(uuid.uuid4()), key=f"{day}:facebook:text:x",
             approved_by=None)                                        # another run's open Facebook post
    env.seed(platform="linkedin", status="pending_review", run_id=run_id, key=f"{day}:linkedin:text",
             approved_by=None)                                        # the same run, another platform
    env.seed(platform="threads", status="pending_review", format="video", run_id=run_id, approved_by=None,
             key=f"{day}:threads:video")                              # the run's other (still open) extra
    counters = await pub.publish_cycle()
    assert counters["published"] == 1 and env.row(extra)["status"] == "published"
    assert _held_lines(caplog) == [] and up.prepare_ids == [extra]


@pytest.mark.asyncio
async def test_batch3_a_failed_sibling_read_holds_the_extra(env, monkeypatch, caplog):
    """Fail closed: a sibling read that fails holds the extra (WARNING with its ids) and writes nothing; the
    rest of the pass goes on, and the next tick that can read sends it."""
    up, primary, extra, run_id = _extra_pair(env, monkeypatch, primary="published")
    other = env.seed(platform="bluesky")                              # a primary elsewhere in the same pass
    real = pub._exec
    broken = {"on": True}

    async def flaky(query, *, op, **ids):
        if broken["on"]:
            raise mrs.MarketingRunError(f"{op} failed (run_id={ids.get('run_id')}): APIError: PostgREST 520")
        return await real(query, op=op, **ids)

    monkeypatch.setattr(pub, "_exec", flaky)
    before = env.row(extra)
    counters = await pub.publish_cycle()
    assert env.row(extra) == before and extra not in up.prepare_ids
    assert counters["retry"] == 1 and counters["published"] == 1 and env.row(other)["status"] == "published"
    warnings = [r.getMessage() for r in caplog.records if r.name == PUB_LOGGER and r.levelno == logging.WARNING
                and "could not be read" in r.getMessage()]
    assert len(warnings) == 1 and extra in warnings[0] and run_id in warnings[0] and "PostgREST 520" in warnings[0]
    assert len(_held_lines(caplog)) == 1
    broken["on"] = False
    counters = await pub.publish_cycle()
    assert counters["published"] == 1 and env.row(extra)["status"] == "published"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, {"id": "x", "format": "text", "status": "published"}, "rows"])
async def test_batch3_a_sibling_read_of_an_unexpected_shape_holds_the_extra(env, monkeypatch, caplog, data):
    """A read that answers anything but a list of rows is unreadable, never "no sibling": the extra is held."""
    _up, _primary, extra, _run = _extra_pair(env, monkeypatch, primary=None)

    class _Odd:
        def __init__(self, value: Any) -> None:
            self.data = value

    async def odd(query, *, op, **ids):
        return _Odd(data)

    monkeypatch.setattr(pub, "_exec", odd)
    before = env.row(extra)
    counters = await pub.publish_cycle()
    assert env.row(extra) == before and counters["retry"] == 1 and len(_held_lines(caplog)) == 1
    assert any("not a list of rows" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", [None, ""])
async def test_batch3_an_extra_that_names_no_run_is_held(env, monkeypatch, caplog, run_id):
    _up, _primary, extra, _run = _extra_pair(env, monkeypatch, primary=None)
    env.raw(extra)["run_id"] = run_id                                 # a hand-edited row: nothing to read by
    before = env.row(extra)
    counters = await pub.publish_cycle()
    assert env.row(extra) == before and counters["retry"] == 1
    assert any("names no run" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


@pytest.mark.asyncio
async def test_batch3_the_sibling_read_reads_its_rows_fail_closed(env):
    """`_extra_waits` directly: the post itself never counts; another EXTRA never holds it; a sibling whose format
    cannot be read is taken for a primary (it holds while open); a non-dict row is skipped."""
    run_id = str(uuid.uuid4())
    day = mrs.run_date_et().isoformat()
    extra = env.seed(platform="linkedin", format="video", run_id=run_id, key=f"{day}:linkedin:video")
    row = env.row(extra)
    assert await pub._extra_waits(env.svc, row) is False             # alone: its own open row never holds it
    junk = env.seed(platform="linkedin", status="queued", format=None, run_id=run_id, key=f"{day}:linkedin:?")
    assert await pub._extra_waits(env.svc, row) is True               # unreadable format: a primary, open
    env.raw(junk)["status"] = "failed"
    assert await pub._extra_waits(env.svc, row) is False
    for status in pub._PRIMARY_OPEN:
        env.raw(junk)["status"] = status
        assert await pub._extra_waits(env.svc, row) is True
    assert pub._PRIMARY_OPEN == ("pending_review", "approved", "queued")
    assert is_extra_format("linkedin", "video") and not is_extra_format("linkedin", "text")


def _spy_ledger_ops(env: Env, monkeypatch) -> List[tuple]:
    """Every query the in-memory ledger executes, in order, as (table, op, columns read, filters, rows) —
    nothing per-row (ids, times), so two runs of the same day compare equal."""
    names = {id(t): name for name, t in env.fake.tables.items()}
    log: List[tuple] = []
    real_select, real_execute = _Table.select, _Query.execute

    def select(self, *cols):
        query = real_select(self, *cols)
        query._b3_cols = cols
        return query

    def execute(self):
        res = real_execute(self)
        log.append((names.get(id(self.t)), self.op, getattr(self, "_b3_cols", None), len(self.filters),
                    len(res.data) if isinstance(res.data, list) else None))
        return res

    monkeypatch.setattr(_Table, "select", select)
    monkeypatch.setattr(_Query, "execute", execute)
    return log


async def _a_primary_only_day(monkeypatch, *, today: bool) -> tuple:
    """One publish pass over a PRIMARY-ONLY day (no extra anywhere) in a fresh ledger: the real publisher, or —
    `today` — one that knows of no extra format (the publisher as it was before Batch 3). Returns the ledger's op
    log and the publisher's own `_exec` calls."""
    e = Env()
    calls: List[str] = []
    real = pub._exec

    async def recording(query, *, op, **ids):
        calls.append(op)
        return await real(query, op=op, **ids)

    with monkeypatch.context() as m:
        m.setattr(pub, "get_marketing_run_service", lambda: e.svc)
        m.setattr(pub, "_exec", recording)
        ups = {p: FakeAdapter(p, resend_safe=True, retractable=False, schedule=UP_SCHEDULE)
               for p in ("facebook", "linkedin", "threads", "tiktok", "youtube", "instagram")}
        for platform, adapter in {"x": e.x, "bluesky": e.bsky, **ups}.items():
            m.setitem(outlets.ADAPTERS, platform, adapter)
        m.setattr(outlets, "enabled_platforms", lambda: ["x", "bluesky", *ups])
        if today:
            m.setattr(pub, "is_extra_format", lambda *_a: False)
        log = _spy_ledger_ops(e, m)
        run_id = str(uuid.uuid4())
        day = mrs.run_date_et().isoformat()
        for platform in ("x", "bluesky", "facebook", "linkedin", "threads"):
            e.seed(platform=platform, run_id=run_id, key=f"{day}:{platform}:text")
        for platform in ("tiktok", "youtube", "instagram"):
            e.seed(platform=platform, format="video", run_id=run_id, key=f"{day}:{platform}:video",
                   asset_ids=[B3_VIDEO])
        ups["linkedin"].outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
        counters = await pub.publish_cycle()
    return log, calls, counters


@pytest.mark.asyncio
async def test_batch3_primary_posts_never_read_siblings(env, monkeypatch):
    """A day without an extra makes exactly the ledger calls it made before Batch 3: the same op log as a
    publisher that knows of no extra format, and not one sibling read."""
    now_log, now_calls, now_counters = await _a_primary_only_day(monkeypatch, today=False)
    then_log, then_calls, then_counters = await _a_primary_only_day(monkeypatch, today=True)
    assert now_counters == then_counters and now_counters["published"] == 7 and now_counters["submitted"] == 1
    assert now_log == then_log and len(now_log) > 10
    assert now_calls == then_calls == []
    assert not [entry for entry in now_log if entry[2] == ("id,format,status",)]


@pytest.mark.asyncio
async def test_batch3_two_facebook_posts_have_their_own_claims_keys_and_outcomes(env, monkeypatch, caplog):
    """The picture/text post and the video of one run on Facebook: two rows, two claims, two Idempotency-Keys
    (the REAL Upload-Post prepare derives each from its own key), two outcomes — the video only after the
    picture/text post was published, and neither ever writes on the other."""
    up = _up_adapter(env, monkeypatch, "facebook", cls=_RealPrepareFake)
    _up, primary, extra, _run = _extra_pair(env, monkeypatch, adapter=up, primary="approved")
    day = mrs.run_date_et().isoformat()
    up.on_send = lambda post: up.outcomes.append(Outcome(SUBMITTED, publish_meta=up.sends[-1][1].publish_meta))

    counters = await pub.publish_cycle()                               # pass 1: the primary only
    assert counters["submitted"] == 1 and counters["retry"] == 1 and up.prepare_ids == [primary]
    first = env.row(primary)
    assert first["status"] == "queued" and first["attempts"] == 1
    assert first["metadata"]["publish"]["upload_post"]["request_id"] == f"{day}:facebook:text:a1"
    assert env.row(extra)["status"] == "approved" and env.row(extra)["attempts"] == 0

    assert (await pub.publish_cycle())["retry"] == 1                  # still queued: still held
    _backdate(env, primary, 660)
    up.reconcile_results = [ReconcileResult(FOUND, external_id="fb-pic-1", external_url="https://fb.example/p/1")]
    assert (await pub.reconcile_cycle())["found"] == 1
    published_primary = env.row(primary)
    assert published_primary["status"] == "published"

    counters = await pub.publish_cycle()                               # pass 3: the video goes
    assert counters["submitted"] == 1 and counters["retry"] == 0 and up.prepare_ids[-1] == extra
    video = env.row(extra)
    assert video["status"] == "queued" and video["attempts"] == 1
    assert video["metadata"]["publish"]["upload_post"]["request_id"] == f"{day}:facebook:video:a1"
    assert video["idempotency_key"] == f"{day}:facebook:video" != published_primary["idempotency_key"]
    assert [h["kind"] for h in video["metadata"]["publish"]["history"]] == [SUBMITTED]
    assert env.row(primary) == published_primary                      # the video's claim wrote nothing on it

    _backdate(env, extra, 660)
    up.reconcile_results = [ReconcileResult(FOUND, external_id="fb-reel-1", external_url="https://fb.example/r/1")]
    assert (await pub.reconcile_cycle())["found"] == 1
    assert (env.row(primary)["external_id"], env.row(extra)["external_id"]) == ("fb-pic-1", "fb-reel-1")
    assert len(up.sends) == 2 and [s[0]["id"] for s in up.sends] == [primary, extra]
    _assert_review_kept(env.row(extra))


@pytest.mark.asyncio
async def test_batch3_expiry_closes_both(env, monkeypatch):
    """A held extra never outlives its day: the expiry step closes it with its primary — approved or still
    pending — like any post, and the publish pass skips it once its day is over (it never reads the siblings)."""
    two_days_ago = mrs.run_date_et() - timedelta(days=2)
    _up, approved_primary, approved_extra, _r1 = _extra_pair(env, monkeypatch, day=two_days_ago)
    up = outlets.ADAPTERS["facebook"]
    _u, pending_primary, pending_extra, _r2 = _extra_pair(env, monkeypatch, adapter=up, day=two_days_ago,
                                                         primary="pending_review", extra_status="pending_review")
    reads: List[str] = []
    real = pub._exec

    async def recording(query, *, op, **ids):
        reads.append(op)
        return await real(query, op=op, **ids)

    monkeypatch.setattr(pub, "_exec", recording)
    counters = await pub.publish_cycle()
    assert counters["skipped"] == 2 and up.prepare_ids == [] and reads == []
    assert (await pub._expire_step())["expired"] == 4
    for pid in (approved_primary, approved_extra, pending_primary, pending_extra):
        row = env.row(pid)
        assert row["status"] == "skipped" and row["metadata"]["skip_reason"] == "expired"
    # B3S-2, through the real publisher step: the approved extra's alert names what it waited for — its primary,
    # approved but never sent, expired first (the older row) — never the generic configuration reason, which its
    # primary keeps. The pair the owner never decided alerts nothing.
    key = env.row(approved_extra)["idempotency_key"]
    assert env.row(approved_extra)["metadata"]["alert_text"] == (
        f"⏰ FACEBOOK post expired UNPUBLISHED (run {key[:10]}) — it was approved but never went out (it waited for "
        "its FACEBOOK picture/text post, which goes first and was still approved but not yet sent when the day "
        "ended).")
    assert env.row(approved_primary)["metadata"]["alert_text"].endswith(
        "(publishing was off, dry-run, capped or not wired).")
    assert {"alert_kind", "alert_text"}.isdisjoint(env.row(pending_primary)["metadata"])
    assert {"alert_kind", "alert_text"}.isdisjoint(env.row(pending_extra)["metadata"])


@pytest.mark.asyncio
async def test_batch3_both_posts_open_or_extend_the_go_early_window(env, monkeypatch, caplog):
    """Both Facebook posts carry /go/facebook: the picture/text post opens the campaign's early window when it is
    sent, and the video — sent later, once that post is published — extends it (never shortens it)."""
    clock = {"t": float(int(time.time()))}
    monkeypatch.setattr(publish_clock, "_wall", lambda: clock["t"])
    start = clock["t"]
    up, primary, extra, _run = _extra_pair(env, monkeypatch, primary_caption=_linked("facebook"),
                                           extra_caption=_linked("facebook"))
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    await pub.publish_cycle()
    assert publish_clock.open_until("facebook") == start + 300 and env.row(extra)["status"] == "approved"

    clock["t"] = start + 100
    _backdate(env, primary, 660)
    up.reconcile_results = [ReconcileResult(FOUND, external_id="fb-pic-1")]
    await pub.reconcile_cycle()                                       # a reconcile FOUND opens nothing
    assert publish_clock.open_until("facebook") == start + 300

    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    await pub.publish_cycle()
    assert env.row(extra)["status"] == "queued"
    assert publish_clock.open_until("facebook") == start + 400 and set(_windows()) == {"facebook"}
    assert [line.rsplit("post_id=", 1)[1] for line in _window_lines(caplog)] == [primary, extra]


# Round-1 fix B3S-3 (2026-10-20): a REHEARSAL is never held. A dry pass sends nothing, so there is no order to keep —
# and its primary, never written, stays `approved`, so a hold there kept the extra HELD on every tick until expiry:
# the rehearsal never prepared it, and its prepare-time guards (one video asset, the Threads 500-byte cap, the
# format route) first ran on the live day. Now a rehearsal extra is prepared like any post, makes no sibling read,
# and its "would publish" line ends with the live rule; the primary's line is unchanged, and nothing is written.


def _sibling_reads(monkeypatch) -> List[Dict[str, Any]]:
    """Every `publish_extra_siblings` read the publisher makes (its own `_exec` binding)."""
    reads: List[Dict[str, Any]] = []
    real = pub._exec

    async def spy(query, *, op, **ids):
        if op == "publish_extra_siblings":
            reads.append(ids)
        return await real(query, op=op, **ids)

    monkeypatch.setattr(pub, "_exec", spy)
    return reads


@pytest.mark.asyncio
@pytest.mark.parametrize("rehearsal", ["dry-run switch", "rehearsal row, live switch"])
async def test_batch3_a_rehearsal_extra_is_prepared_never_held_and_writes_nothing(env, monkeypatch, caplog,
                                                                                  rehearsal):
    """In DRY RUN — or for a rehearsal ROW under a live switch (a string "false" passes the query filter;
    `is_rehearsal` stops it) — an extra whose primary is still open is NOT held: it is prepared, logs "would publish"
    with DRY_RUN_EXTRA_NOTE, makes no sibling read, counts as `skipped` (a rehearsal), and nothing is written."""
    reads = _sibling_reads(monkeypatch)
    if rehearsal == "dry-run switch":
        monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
        up, primary, extra, _run = _extra_pair(env, monkeypatch, primary="approved")
        expected_prepared = [primary, extra]
    else:
        # The primary is still pending review: live, the extra would be HELD — as a rehearsal row it never is.
        up, primary, extra, _run = _extra_pair(env, monkeypatch, primary="pending_review")
        env.raw(extra)["metadata"] = _review_meta(dry_run="false")
        expected_prepared = [extra]
    before = env.snapshot()
    counters = await pub.publish_cycle()
    assert env.posts.rows == before and up.sends == [] and reads == []
    assert up.prepare_ids == expected_prepared and _held_lines(caplog) == []
    assert counters["skipped"] == len(expected_prepared) and counters["retry"] == 0
    lines = _messages(caplog, "DRY_RUN: would publish")
    assert len(lines) == len(expected_prepared)
    (extra_line,) = [m for m in lines if f"post_id={extra} " in m]
    note = pub.DRY_RUN_EXTRA_NOTE.format(platform="facebook")
    assert note == " (live: held until its facebook picture/text post is published or closed)"
    assert extra_line.endswith("facebook fake payload chars=" + str(len(env.raw(extra)["caption"])) + note)
    # The primary's line is the line of before, byte for byte: no note on a post that is not an extra.
    others = [m for m in lines if f"post_id={extra} " not in m]
    assert len(others) == len(expected_prepared) - 1
    for line in others:
        assert f"post_id={primary} " in line
        assert line.endswith(f"facebook fake payload chars={len(env.raw(primary)['caption'])}")


@pytest.mark.asyncio
async def test_batch3_the_live_twin_of_a_rehearsal_extra_is_still_held(env, monkeypatch, caplog):
    """The fix moved nothing in a live pass: the same pair under a live switch, both rows live, holds the extra
    (one sibling read, HELD, `retry`) and sends only the primary."""
    reads = _sibling_reads(monkeypatch)
    up, primary, extra, run_id = _extra_pair(env, monkeypatch, primary="approved")
    up.outcomes = [Outcome(SUBMITTED, publish_meta={"upload_post": _up_meta()})]
    before = env.row(extra)
    counters = await pub.publish_cycle()
    assert env.row(extra) == before and up.prepare_ids == [primary] and [s[0]["id"] for s in up.sends] == [primary]
    assert len(_held_lines(caplog)) == 1 and counters["retry"] == 1 and counters["submitted"] == 1
    assert reads == [{"post_id": extra, "run_id": run_id}]
    assert _messages(caplog, "DRY_RUN") == []


@pytest.mark.asyncio
async def test_batch3_a_dry_run_rehearses_each_extra_through_the_real_prepare(env, monkeypatch, caplog):
    """What the rehearsal is for (B3S-3): with the REAL Upload-Post prepare, a dry pass logs the Facebook video's
    own request (kind=video, its own key and request id) with the live rule, and REFUSES a Threads video whose
    caption is over 500 UTF-8 bytes — on the rehearsal day, not on the live one. Nothing is written or sent."""
    monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    reads = _sibling_reads(monkeypatch)
    fb = _up_adapter(env, monkeypatch, "facebook", cls=_RealPrepareFake)
    th = _up_adapter(env, monkeypatch, "threads", cls=_RealPrepareFake)
    _f, fb_primary, fb_extra, _r1 = _extra_pair(env, monkeypatch, adapter=fb, platform="facebook")
    _t, th_primary, th_extra, _r2 = _extra_pair(env, monkeypatch, adapter=th, platform="threads",
                                                extra_caption="é" * 251)     # 251 characters, 502 UTF-8 bytes
    before = env.snapshot()
    counters = await pub.publish_cycle()
    assert env.posts.rows == before and fb.sends == th.sends == [] and reads == []
    assert fb.prepare_ids == [fb_primary, fb_extra] and th.prepare_ids == [th_primary, th_extra]
    assert counters["skipped"] == 4 and counters["retry"] == counters["failed"] == 0
    assert _held_lines(caplog) == _held_lines(caplog, "threads") == []
    day = mrs.run_date_et().isoformat()
    (fb_line,) = [m for m in _messages(caplog, "DRY_RUN: would publish") if f"post_id={fb_extra} " in m]
    assert f"facebook via upload-post kind=video request_id={day}:facebook:video:a1 " in fb_line
    assert fb_line.endswith(pub.DRY_RUN_EXTRA_NOTE.format(platform="facebook"))
    (refused,) = _messages(caplog, "DRY_RUN: would REFUSE")
    assert f"post_id={th_extra} platform=threads" in refused and "502 UTF-8 bytes > 500" in refused


# Round 2 (2026-10-20), RR2-DRY-EXPIRY-ALERT — the seam between B3S-3 and B3S-2: the publisher holds an extra only in
# its LIVE pass (never in web dry run, and with publishing off there is no pass at all), so an extra that expires
# unsent then was never held, and its expiry alert must not say it waited for its picture/text post. The live twin is
# test_batch3_expiry_closes_both. The gate is in `run_service.expire_stale_posts`.


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["web dry run", "publishing off"])
async def test_batch3_an_extra_the_publisher_never_held_expires_with_the_generic_reason(env, monkeypatch, caplog,
                                                                                        switch):
    """LIVE rows the owner approved, never sent: in web dry run the day's pass prepares the extra and logs "would
    publish" (B3S-3, never HELD, nothing written); with publishing off no pass runs. Two days later both expire, and
    the extra's alert gives the reason its primary gives — never "it waited for its FACEBOOK picture/text post", which
    no hold made true — with no sibling read for it."""
    day = mrs.run_date_et()
    if switch == "web dry run":
        monkeypatch.setattr(pub.settings, "MARKETING_DRY_RUN", True)
    else:
        monkeypatch.setattr(pub.settings, "MARKETING_ENABLED", False)
    up, primary, extra, _run = _extra_pair(env, monkeypatch, day=day)
    if switch == "web dry run":
        before = env.snapshot()
        counters = await pub.publish_cycle()
        assert env.posts.rows == before and up.sends == [] and up.prepare_ids == [primary, extra]
        assert counters["retry"] == 0 and _held_lines(caplog) == []
        assert any(f"post_id={extra} " in m for m in _messages(caplog, "DRY_RUN: would publish"))
    ops: List[str] = []
    real = mrs._exec

    async def spy(query, *, op, **ids):
        ops.append(op)
        return await real(query, op=op, **ids)

    monkeypatch.setattr(mrs, "_exec", spy)
    monkeypatch.setattr(pub, "run_date_et", lambda *_a, **_k: day + timedelta(days=2))
    assert (await pub._expire_step())["expired"] == 2
    for pid in (primary, extra):
        row = env.row(pid)
        assert row["status"] == "skipped" and row["metadata"]["skip_reason"] == "expired"
        assert row["metadata"]["alert_kind"] == "expired"
    generic = "(publishing was off, dry-run, capped or not wired)."
    key = env.row(extra)["idempotency_key"]
    assert env.row(extra)["metadata"]["alert_text"] == (
        f"⏰ FACEBOOK post expired UNPUBLISHED (run {key[:10]}) — it was approved but never went out {generic}")
    assert env.row(primary)["metadata"]["alert_text"].endswith(generic)
    assert "expire_extra_siblings" not in ops


# Round-1 integration (2026-10-20), B3S-2's second half: when reconcile ESCALATES a platform's PRIMARY post and the
# run has an APPROVED, live extra video on that platform, the "outcome UNKNOWN" alert ends with
# ESCALATION_EXTRA_NOTE — `_extra_waits` holds that video while the primary is queued, so the owner's answer is what
# lets it go (if the day ends first, the expiry alert names its primary: B3S-2's first half). One read, only for a
# primary on a platform that can carry an extra; a read that fails costs the line (WARNING), never the escalation;
# every other escalation reads exactly as before.


def _escalation_reads(monkeypatch) -> List[Dict[str, Any]]:
    """Every `escalate_extra_siblings` read the publisher makes (its own `_exec` binding)."""
    reads: List[Dict[str, Any]] = []
    real = pub._exec

    async def spy(query, *, op, **ids):
        if op == "escalate_extra_siblings":
            reads.append(ids)
        return await real(query, op=op, **ids)

    monkeypatch.setattr(pub, "_exec", spy)
    return reads


_SPENT_ERROR = "no platform result yet"


def _escalating(env: Env, platform: str, *, run_id: Optional[str] = None, fmt: str = "text",
                error: str = _SPENT_ERROR) -> str:
    """A queued `platform` post of run `run_id` whose whole check schedule is spent on unknown results: the next
    reconcile escalates it ("<n> checks could not confirm it; last check: unknown — <error>")."""
    run_id = run_id or str(uuid.uuid4())
    pid = env.seed_queued(platform=platform, started_ago=90_000, state="submitted", run_id=run_id, format=fmt,
                          key=f"{mrs.run_date_et().isoformat()}:{platform}:{fmt}:{run_id[:8]}")
    env.raw(pid)["metadata"]["publish"]["reconcile"] = {
        "n": len(outlets.ADAPTERS[platform].reconcile_schedule), "last_at": _now().isoformat(),
        "last_result": UNKNOWN, "error": error}
    return pid


def _old_escalation(platform: str, *, error: str = _SPENT_ERROR) -> str:
    """The "outcome UNKNOWN" alert as `_escalate` wrote it before this change, byte for byte."""
    n = len(outlets.ADAPTERS[platform].reconcile_schedule)
    return (f"⚠️ {platform.upper()} post — outcome UNKNOWN ({n} checks could not confirm it; last check: unknown — "
            f"{error}). It may or may not be live.\n"
            "Tap “It's live” if you can see it, or “Not posted” if it is not there. "
            "It will NOT be resent automatically.")


def _seed_extra(env: Env, platform: str, run_id: str, *, status: str = "approved", meta: Any = _DEFAULT) -> str:
    return env.seed(platform=platform, status=status, format="video", run_id=run_id, asset_ids=[B3_VIDEO],
                    key=f"{mrs.run_date_et().isoformat()}:{platform}:video:{run_id[:8]}", meta=meta)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["facebook", "linkedin", "threads"])
async def test_r1_an_escalated_picture_post_says_its_approved_video_waits_for_the_answer(env, monkeypatch, platform):
    """The primary of a run whose extra video is approved and live: its alert is the old text plus
    "\\nIts <PLATFORM> video waits for this answer." after ONE sibling read. Its twin in the same pass — a run with
    no extra — reads exactly as before, and the extra itself is untouched (still approved, still held)."""
    _up_adapter(env, monkeypatch, platform)
    reads = _escalation_reads(monkeypatch)
    run_id = str(uuid.uuid4())
    primary = _escalating(env, platform, run_id=run_id)
    extra = _seed_extra(env, platform, run_id)
    lone = _escalating(env, platform)
    extra_before = env.row(extra)
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 2
    note = pub.ESCALATION_EXTRA_NOTE.format(platform=platform.upper())
    assert note == f"\nIts {platform.upper()} video waits for this answer."
    for pid, text in ((primary, _old_escalation(platform) + note), (lone, _old_escalation(platform))):
        meta = env.row(pid)["metadata"]
        assert meta["alert_kind"] == "unknown" and meta["alert_text"] == text
        assert env.row(pid)["status"] == "queued" and meta["publish"]["state"] == "escalated"
    assert env.row(extra) == extra_before
    assert sorted(r["post_id"] for r in reads) == sorted([primary, lone])


@pytest.mark.asyncio
@pytest.mark.parametrize("status, meta", [
    ("pending_review", None),            # it still waits for its own review too: the line would say too little
    ("published", None), ("failed", None), ("rejected", None), ("skipped", None), ("retracted", None),
    ("approved", {"dry_run": True}),     # a rehearsal row is never held (B3S-3)
    ("approved", {"dry_run": "false"}),  # nor is anything that is not exactly False
], ids=["pending_review", "published", "failed", "rejected", "skipped", "retracted", "rehearsal",
        "dry_run-string"])
async def test_r1_no_waiting_line_unless_the_extra_is_approved_and_live(env, monkeypatch, status, meta):
    """The line is said only of an extra the publisher would actually hold: approved and live."""
    _up_adapter(env, monkeypatch, "facebook")
    reads = _escalation_reads(monkeypatch)
    run_id = str(uuid.uuid4())
    primary = _escalating(env, "facebook", run_id=run_id)
    _seed_extra(env, "facebook", run_id, status=status, meta=_review_meta(**meta) if meta else _DEFAULT)
    assert (await pub.reconcile_cycle())["escalated"] == 1
    assert env.row(primary)["metadata"]["alert_text"] == _old_escalation("facebook")
    assert [r["post_id"] for r in reads] == [primary]


@pytest.mark.asyncio
async def test_r1_another_platforms_video_of_the_run_never_counts(env, monkeypatch):
    """The read is the run's posts on the escalated post's OWN platform: the run's approved TikTok video (a primary
    there) and its approved LinkedIn extra video wait for nothing on Facebook — no line on Facebook's alert."""
    _up_adapter(env, monkeypatch, "facebook")
    reads = _escalation_reads(monkeypatch)
    run_id = str(uuid.uuid4())
    primary = _escalating(env, "facebook", run_id=run_id)
    env.seed(platform="tiktok", status="approved", format="video", run_id=run_id, asset_ids=[B3_VIDEO],
             key=f"{mrs.run_date_et().isoformat()}:tiktok:video:{run_id[:8]}")
    _seed_extra(env, "linkedin", run_id)
    assert (await pub.reconcile_cycle())["escalated"] == 1
    assert env.row(primary)["metadata"]["alert_text"] == _old_escalation("facebook")
    assert [r["post_id"] for r in reads] == [primary]


@pytest.mark.asyncio
async def test_r1_only_a_primary_on_an_extra_platform_reads_its_siblings(env, monkeypatch):
    """X, Bluesky, TikTok, Instagram and YouTube carry no extra, and an escalated EXTRA has no video waiting behind
    it (its primary is not one): none of them makes the read, each beside an approved video row of its own run, and
    each alert reads exactly as before."""
    for platform in ("tiktok", "instagram", "youtube", "facebook"):
        _up_adapter(env, monkeypatch, platform)
    reads = _escalation_reads(monkeypatch)
    plain = {}
    for platform in ("x", "bluesky", "tiktok", "instagram", "youtube"):
        run_id = str(uuid.uuid4())
        plain[_escalating(env, platform, run_id=run_id, fmt="video" if platform in ("tiktok", "instagram",
                                                                                    "youtube") else "text")] = platform
        env.seed(platform=platform, status="approved", format="video", run_id=run_id, asset_ids=[B3_VIDEO],
                 key=f"{mrs.run_date_et().isoformat()}:{platform}:video-twin:{run_id[:8]}")
    run_id = str(uuid.uuid4())
    env.seed(platform="facebook", status="approved", format="text", run_id=run_id,
             key=f"{mrs.run_date_et().isoformat()}:facebook:text:{run_id[:8]}")
    escalated_extra = _escalating(env, "facebook", run_id=run_id, fmt="video")
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 6 and reads == []
    for pid, platform in plain.items():
        assert env.row(pid)["metadata"]["alert_text"] == _old_escalation(platform)
    assert env.row(escalated_extra)["metadata"]["alert_text"] == _old_escalation("facebook")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raises", "no list"])
async def test_r1_a_sibling_read_that_fails_costs_only_the_line(env, monkeypatch, caplog, failure):
    """A read that raises (a ledger blip) or answers no list: the escalation is recorded as before, without the
    line, and a WARNING names the post — never an escalation lost to a courtesy line."""
    _up_adapter(env, monkeypatch, "facebook")
    run_id = str(uuid.uuid4())
    primary = _escalating(env, "facebook", run_id=run_id)
    _seed_extra(env, "facebook", run_id)
    real = pub._exec

    class _NoRows:
        data = None

    async def broken(query, *, op, **ids):
        if op == "escalate_extra_siblings":
            if failure == "raises":
                raise mrs.MarketingRunError("ledger down")
            return _NoRows()
        return await real(query, op=op, **ids)

    monkeypatch.setattr(pub, "_exec", broken)
    assert (await pub.reconcile_cycle())["escalated"] == 1
    row = env.row(primary)
    assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "escalated"
    assert row["metadata"]["alert_text"] == _old_escalation("facebook")
    (warning,) = _messages(caplog, "its extra video post could not be read", logging.WARNING)
    assert f"post_id={primary} platform=facebook run_id={run_id}" in warning


@pytest.mark.asyncio
async def test_r1_the_line_is_left_out_rather_than_cut_by_the_alerts_cap(env, monkeypatch):
    """`scrub` caps an alert at ERROR_TEXT_MAX characters. When the text fits but the line would not, the line is
    left out — never half of it — and the text is the old one, whole."""
    _up_adapter(env, monkeypatch, "facebook")
    room = outlet_base.ERROR_TEXT_MAX - len(_old_escalation("facebook", error=""))
    error = "e" * (room - 10)                      # 10 characters to spare: less than the line's 45
    assert len(pub.ESCALATION_EXTRA_NOTE.format(platform="FACEBOOK")) > 10
    run_id = str(uuid.uuid4())
    primary = _escalating(env, "facebook", run_id=run_id, error=error)
    _seed_extra(env, "facebook", run_id)
    assert (await pub.reconcile_cycle())["escalated"] == 1
    text = env.row(primary)["metadata"]["alert_text"]
    assert text == _old_escalation("facebook", error=error) and len(text) == outlet_base.ERROR_TEXT_MAX - 10
