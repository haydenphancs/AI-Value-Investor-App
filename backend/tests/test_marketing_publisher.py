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
  * expiry closes stale approved / pending_review rows (00:00 ET boundary) and finished runs.

The tick ORDER and step isolation are pinned by tests/test_marketing_review_bot.py — not repeated here.
Dates are computed relative to `run_service.run_date_et()` / the real clock (no freezegun); the
00:00 ET boundary is driven by patching `publisher_service._now`.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import pytest

from app.services.marketing import outlet_x, outlets
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
    UNKNOWN,
    Adapter,
    MarketingPublishRefused,
    Outcome,
    Prepared,
    ReconcileResult,
    RetractResult,
    backoff_seconds,
    text_sha256,
)
from test_marketing_run_service import FakeSupabase

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
        if self.on_prepare is not None:
            self.on_prepare(post)
        if self.refuse is not None:
            raise self.refuse
        text = post.get("caption") if isinstance(post.get("caption"), str) else ""
        prepared = Prepared(
            payload={"text": text}, text_sha256=text_sha256(text), reserve_micros=self.reserve,
            publish_meta={self.platform: {"rkey": f"rk-{post.get('id')}"}},
            summary=f"{self.platform} fake payload chars={len(text)}",
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
    monkeypatch.setattr(pub, "_escalation_logged", {})
    monkeypatch.setitem(outlets.ADAPTERS, "x", e.x)
    monkeypatch.setitem(outlets.ADAPTERS, "bluesky", e.bsky)
    monkeypatch.setattr(outlets, "enabled_platforms", lambda: list(e.enabled))
    monkeypatch.setattr(outlet_x, "budget_micros", lambda: e.budget)
    return e


# ── publish: what is decided BEFORE the claim ─────────────────────────────────


@pytest.mark.asyncio
async def test_no_enabled_platform_only_observes(env, caplog):
    env.enabled = []
    env.seed(platform="x")
    env.seed(platform="bluesky")
    before = env.snapshot()
    counters = await pub.publish_cycle()
    assert counters == {"approved_waiting": 2, "published": 0, "failed": 0, "skipped": 0, "retry": 0,
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
