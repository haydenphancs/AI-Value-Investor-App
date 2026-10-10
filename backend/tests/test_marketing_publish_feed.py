"""
The marketing publisher's Telegram FEED (`app/services/marketing/publish_feed.py`, design doc §12.10):
the "Posted …" message (a reply to the review message, with a 🗑 Retract button when the platform
can delete), the retract confirmation (an edit of that message, or a new one), and the alerts the
publisher writes onto a post (`metadata.alert_kind` / `alert_text`).

Hermetic: Telegram is the `FakeTelegram` MockTransport from `test_marketing_review_bot.py`, the
ledger is the in-memory PostgREST fake from `test_marketing_run_service.py`; X / Bluesky are
MockTransports where a test needs them.

What must never regress:
  * plain text only (no `parse_mode`), the reply threads under the review message
    (`allow_sending_without_reply`), the Retract button only when the platform can delete AND the
    post has an id;
  * AT-LEAST-ONCE: a message is stamped only after it went out; a stamp that fails, or a post that
    changed status meanwhile, is sent again next cycle — never lost, never stamped unsent;
  * a Telegram failure NEVER changes a post's status (the publisher owns statuses);
  * ≤ FEED_SEND_LIMIT sends a tick; a 429 stops the feed and holds off the review sweep too;
  * an unconfigured bot is a no-op that does not even read the ledger.
"""

from __future__ import annotations

import itertools
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import pytest

from app.config import settings
from app.integrations import bluesky, telegram, x_api
from app.services.marketing import outlets
from app.services.marketing import publish_feed as feed
from app.services.marketing import publisher_service as pub
from app.services.marketing import review_service as rs
from app.services.marketing import run_service as mrs
from test_marketing_review_bot import OWNER, SECRET, TOKEN, FakeTelegram
from test_marketing_run_service import FakeSupabase

X_ID = "1840000000000000001"
X_URL = f"https://x.com/i/web/status/{X_ID}"
BSKY_RKEY = "3l6oveex3ii2l"
BSKY_DID = "did:plc:abcdefghijklmnopqrstuvwx"
BSKY_URI = f"at://{BSKY_DID}/app.bsky.feed.post/{BSKY_RKEY}"
BSKY_URL = f"https://bsky.app/profile/{BSKY_DID}/post/{BSKY_RKEY}"
REVIEW_MID = 500
_RETRACTED_RE = r"🗑 Retracted \d\d:\d\d ET"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", OWNER)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.0)
    monkeypatch.setattr(rs, "_rate_limited_until", 0.0)
    # Both platforms can delete through their API (the REAL `outlets.retract_capable` predicate
    # decides the Retract button). Fake values; X's access token carries the "<user id>-" prefix.
    monkeypatch.setattr(settings, "MARKETING_X_CONSUMER_KEY", "test-consumer-key")
    monkeypatch.setattr(settings, "MARKETING_X_CONSUMER_SECRET", "test-consumer-secret")
    monkeypatch.setattr(settings, "MARKETING_X_ACCESS_TOKEN", "1234567890-testaccesstoken")
    monkeypatch.setattr(settings, "MARKETING_X_ACCESS_TOKEN_SECRET", "test-access-token-secret")
    monkeypatch.setattr(settings, "MARKETING_X_MONTHLY_BUDGET_USD", 2.0)
    monkeypatch.setattr(settings, "MARKETING_BLUESKY_HANDLE", "caydex.bsky.social")
    monkeypatch.setattr(settings, "MARKETING_BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")


@pytest.fixture
def ledger(monkeypatch, configured):
    svc = mrs.MarketingRunService(supabase=FakeSupabase())
    svc.fake = svc._sb  # type: ignore[attr-defined]
    monkeypatch.setattr(feed, "get_marketing_run_service", lambda: svc)
    monkeypatch.setattr(rs, "get_marketing_run_service", lambda: svc)
    return svc


@pytest.fixture
def platforms(monkeypatch):
    """X and Bluesky MockTransports that RECORD every request (the feed must never call either)."""
    seen: Dict[str, List[httpx.Request]] = {"x": [], "bluesky": []}

    def recorder(name):
        def handler(request: httpx.Request) -> httpx.Response:
            seen[name].append(request)
            return httpx.Response(500, json={"error": "the test did not expect this call"})
        return handler

    monkeypatch.setattr(x_api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(recorder("x"))))
    monkeypatch.setattr(bluesky, "_client", httpx.AsyncClient(transport=httpx.MockTransport(recorder("bluesky"))))
    return seen


_N = itertools.count(1)


def _rows(svc) -> List[Dict[str, Any]]:
    return svc.fake.tables[mrs.POSTS].rows


def _post(svc, pid) -> Dict[str, Any]:
    return next(r for r in _rows(svc) if r["id"] == pid)


def _snapshot(svc, pid) -> Dict[str, Any]:
    import copy
    return copy.deepcopy(_post(svc, pid))


def _seed(svc, *, platform="x", status="published", fmt="text", external_id: Any = X_ID,
          external_url: Any = None, meta: Optional[Dict[str, Any]] = None, extra_meta=None,
          post_id: Optional[str] = None, run_date="2026-09-29") -> str:
    """A post row as the publisher leaves it (oldest seeded = oldest `updated_at`)."""
    pid = post_id or str(uuid.uuid4())
    n = next(_N)
    ts = (datetime(2026, 9, 29, 12, tzinfo=timezone.utc) + timedelta(seconds=n)).isoformat()
    metadata = ({"dry_run": False, "review_message_id": REVIEW_MID,
                 "review_notified_at": "2026-09-29T10:00:00+00:00",
                 "review": {"decision": "approved", "by": f"telegram:{OWNER}"},
                 "publish": {"state": "published", "attempt": 1}} if meta is None else meta)
    if extra_meta:
        metadata = {**metadata, **extra_meta}
    _rows(svc).append({
        "id": pid, "run_id": "11111111-1111-4111-8111-111111111111", "platform": platform,
        "format": fmt, "status": status, "title": None, "caption": "Hello *caption* _not markup_",
        "asset_ids": [], "idempotency_key": f"{run_date}:{platform}:{fmt}:{n}", "attempts": 1,
        "cost_micros": 0, "external_id": external_id, "external_url": external_url,
        "metadata": metadata, "created_at": ts, "updated_at": ts,
    })
    return pid


def _all_payloads(tg) -> List[Dict[str, Any]]:
    return [p for _m, p in tg.calls]


def _no_parse_mode(tg) -> None:
    assert all("parse_mode" not in p for p in _all_payloads(tg))


# ── the posted message ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_posted_message_replies_to_the_review_message_with_a_retract_button(ledger, tg):
    pid = _seed(ledger)
    before = _snapshot(ledger, pid)
    counters = await feed.feed_cycle()
    assert counters == {"posted": 1, "retracted": 0, "alerts": 0, "failed": 0, "rate_limited": 0}
    (msg,) = tg.of("sendMessage")
    assert [m for m, _ in tg.calls] == ["sendMessage"]
    assert msg["chat_id"] == OWNER
    assert msg["text"] == f"✅ Posted on X · text · run 2026-09-29\n{X_URL}"
    assert msg["reply_parameters"] == {"message_id": REVIEW_MID, "allow_sending_without_reply": True}
    assert msg["reply_markup"] == {"inline_keyboard": [[{"text": "🗑 Retract", "callback_data": f"d:{pid}"}]]}
    assert msg["link_preview_options"] == {"is_disabled": True}
    _no_parse_mode(tg)
    row = _post(ledger, pid)
    assert row["status"] == "published"
    meta = row["metadata"]
    assert meta["posted_notified_at"] and mrs._parse_ts(meta["posted_notified_at"]) is not None
    assert meta["posted_message_id"] == 101 and type(meta["posted_message_id"]) is int
    # The stamp MERGES: everything the reviewer and the publisher wrote is still there.
    for key in ("dry_run", "review_message_id", "review_notified_at", "review", "publish"):
        assert meta[key] == before["metadata"][key]
    for col in ("external_id", "external_url", "attempts", "cost_micros", "status"):
        assert row[col] == before[col]


@pytest.mark.asyncio
async def test_a_second_cycle_sends_nothing(ledger, tg):
    _seed(ledger)
    await feed.feed_cycle()
    tg.calls.clear()
    assert (await feed.feed_cycle())["posted"] == 0
    assert tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, external_id, external_url, unset, url_line, button", [
    ("x", X_ID, None, None, X_URL, True),
    ("x", X_ID, "https://x.com/caydex/status/1840000000000000001", None,
     "https://x.com/caydex/status/1840000000000000001", True),
    # A delete is still wanted after the budget was lowered to 0 to stop new posts.
    ("x", X_ID, None, "MARKETING_X_MONTHLY_BUDGET_USD", X_URL, True),
    # No X credentials → cannot delete through the API → no button, and the message says so.
    ("x", X_ID, None, "MARKETING_X_ACCESS_TOKEN", X_URL, False),
    # Published through the owner's "It's live" answer: no platform id → no button, no link.
    ("x", None, None, None, "(no link — the platform gave none)", False),
    ("x", "", None, None, "(no link — the platform gave none)", False),
    ("bluesky", BSKY_URI, BSKY_URL, None, BSKY_URL, True),
    ("bluesky", BSKY_URI, BSKY_URL, "MARKETING_BLUESKY_APP_PASSWORD", BSKY_URL, False),
    # A platform with no adapter (Stage 2's outlets) never gets a button.
    ("tiktok", "7300000000000000000", "https://www.tiktok.com/@caydex/video/7300000000000000000", None,
     "https://www.tiktok.com/@caydex/video/7300000000000000000", False),
])
async def test_the_retract_button_needs_a_delete_api_and_an_id(ledger, tg, monkeypatch, platform, external_id,
                                                              external_url, unset, url_line, button):
    if unset:
        monkeypatch.setattr(settings, unset, 0.0 if unset.endswith("_USD") else None)
    pid = _seed(ledger, platform=platform, external_id=external_id, external_url=external_url)
    await feed.feed_cycle()
    (msg,) = tg.of("sendMessage")
    lines = msg["text"].split("\n")
    assert lines[0] == f"✅ Posted on {platform.upper()} · text · run 2026-09-29"
    assert lines[1] == url_line
    if button:
        assert msg["reply_markup"] == rs.retract_keyboard(pid)
        assert len(lines) == 2
    else:
        assert "reply_markup" not in msg
        assert lines[2] == "(no Retract button: remove it by hand on the platform if needed)"
    assert _post(ledger, pid)["metadata"]["posted_notified_at"]


@pytest.mark.asyncio
async def test_a_bluesky_url_is_built_from_the_recorded_record_key(ledger, tg):
    pid = _seed(ledger, platform="bluesky", external_id=BSKY_URI, external_url=None,
                extra_meta={"publish": {"state": "published", "bluesky": {"repo": BSKY_DID, "rkey": BSKY_RKEY}}})
    await feed.feed_cycle()
    (msg,) = tg.of("sendMessage")
    assert msg["text"].split("\n")[1] == BSKY_URL
    assert msg["reply_markup"] == rs.retract_keyboard(pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_mid", [None, "500", True, 5.0, [500], {"id": 500}])
async def test_only_a_real_int_review_message_id_is_replied_to(ledger, tg, review_mid):
    meta = {"dry_run": False, "review_notified_at": "2026-09-29T10:00:00+00:00"}
    if review_mid is not None:
        meta["review_message_id"] = review_mid
    pid = _seed(ledger, meta=meta)
    await feed.feed_cycle()
    (msg,) = tg.of("sendMessage")
    assert "reply_parameters" not in msg
    assert _post(ledger, pid)["metadata"]["posted_notified_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_meta", [None, [], "garbage", 7])
async def test_malformed_metadata_is_still_announced_and_stamped_as_a_dict(ledger, tg, bad_meta):
    pid = _seed(ledger, meta={})
    _post(ledger, pid)["metadata"] = bad_meta
    counters = await feed.feed_cycle()
    assert counters["posted"] == 1
    (msg,) = tg.of("sendMessage")
    assert "reply_parameters" not in msg
    meta = _post(ledger, pid)["metadata"]
    assert isinstance(meta, dict) and meta["posted_notified_at"] and meta["posted_message_id"] == 101
    assert _post(ledger, pid)["status"] == "published"


@pytest.mark.asyncio
async def test_a_row_whose_id_is_not_a_uuid_is_skipped_without_costing_a_send(ledger, tg):
    bad = _seed(ledger, post_id="not-a-uuid")
    good = _seed(ledger)
    counters = await feed.feed_cycle()
    assert counters["posted"] == 1
    (msg,) = tg.of("sendMessage")
    assert msg["reply_markup"] == rs.retract_keyboard(good)
    assert "posted_notified_at" not in _post(ledger, bad)["metadata"]


# ── at least once ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_stamp_is_logged_and_the_message_is_sent_again(ledger, tg, caplog):
    pid = _seed(ledger)
    before = _snapshot(ledger, pid)
    ledger.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    caplog.set_level(logging.WARNING)
    first = await feed.feed_cycle()
    assert first["posted"] == 0 and len(tg.of("sendMessage")) == 1
    assert any(f"post_id={pid} was SENT but the stamp failed" in r.getMessage() for r in caplog.records)
    assert _post(ledger, pid) == before          # nothing written, status untouched
    second = await feed.feed_cycle()
    assert second["posted"] == 1 and len(tg.of("sendMessage")) == 2   # sent again: at least once
    assert _post(ledger, pid)["metadata"]["posted_message_id"] == 102  # the SECOND message's id
    assert (await feed.feed_cycle())["posted"] == 0 and len(tg.of("sendMessage")) == 2


@pytest.mark.asyncio
async def test_a_post_that_changed_status_before_the_stamp_is_not_stamped(ledger, tg, caplog):
    """Published → retracted while the "Posted" message was in flight: the stamp's fence on the
    status refuses it (nothing marks a retracted row "posted"), and the retracted step of the SAME
    cycle tells the owner — as a NEW message, since no posted message id was recorded."""
    pid = _seed(ledger)

    def retract_meanwhile(request, payload):
        _post(ledger, pid).update({"status": "retracted", "updated_at": "2026-09-29T13:00:00+00:00"})
        return None  # then answer normally

    tg.script["sendMessage"] = [retract_meanwhile]
    caplog.set_level(logging.WARNING)
    counters = await feed.feed_cycle()
    assert counters["posted"] == 0 and counters["retracted"] == 1
    meta = _post(ledger, pid)["metadata"]
    assert "posted_notified_at" not in meta and "posted_message_id" not in meta
    assert meta["retract_notified_at"]
    assert any("changed before the stamp" in r.getMessage() for r in caplog.records)
    posted, retracted = tg.of("sendMessage")
    assert posted["text"].startswith("✅ Posted on X")
    assert re.fullmatch(_RETRACTED_RE + r" · X · text · run 2026-09-29", retracted["text"])
    assert tg.of("editMessageText") == []
    tg.calls.clear()
    assert await feed.feed_cycle() == {"posted": 0, "retracted": 0, "alerts": 0, "failed": 0, "rate_limited": 0}
    assert tg.calls == []


@pytest.mark.asyncio
async def test_a_concurrent_write_in_the_same_status_is_merged_not_lost(ledger, tg):
    """The owner confirms a Retract (the webhook's `request_retract`, status stays `published`)
    while the Posted message is being stamped: the stamp re-reads and MERGES — the retract
    request survives, and the message is not sent twice."""
    pid = _seed(ledger)

    def webhook_meanwhile(request, payload):
        row = _post(ledger, pid)
        row["metadata"] = {**row["metadata"], "retract_requested_at": "2026-09-29T13:00:00+00:00"}
        row["updated_at"] = "2026-09-29T13:00:00+00:00"
        return None

    tg.script["sendMessage"] = [webhook_meanwhile]
    assert (await feed.feed_cycle())["posted"] == 1
    meta = _post(ledger, pid)["metadata"]
    assert meta["retract_requested_at"] == "2026-09-29T13:00:00+00:00"
    assert meta["posted_notified_at"] and meta["posted_message_id"] == 101
    assert (await feed.feed_cycle())["posted"] == 0 and len(tg.of("sendMessage")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    (502, b"bad gateway"),
    (400, {"ok": False, "description": "Bad Request: chat not found"}),
    (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}),
    (200, b"not json"),
])
async def test_a_telegram_failure_never_changes_the_post(ledger, tg, answer):
    published = _seed(ledger)
    retracted = _seed(ledger, status="retracted", extra_meta={"posted_notified_at": "x"})
    alerted = _seed(ledger, status="failed", external_id=None,
                    extra_meta={"alert_kind": "failed", "alert_text": "❌ X refused the post (forbidden)"})
    before = {pid: _snapshot(ledger, pid) for pid in (published, retracted, alerted)}
    tg.script["sendMessage"] = [answer, answer, answer]
    counters = await feed.feed_cycle()
    assert counters == {"posted": 0, "retracted": 0, "alerts": 0, "failed": 3, "rate_limited": 0}
    for pid, row in before.items():
        assert _post(ledger, pid) == row, pid   # status AND metadata untouched
    # Retried next cycle, all three.
    counters = await feed.feed_cycle()
    assert counters == {"posted": 1, "retracted": 1, "alerts": 1, "failed": 0, "rate_limited": 0}
    assert [_post(ledger, p)["status"] for p in (published, retracted, alerted)] == ["published", "retracted", "failed"]


# ── retracted ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_retract_edits_the_posted_message_and_removes_its_keyboard(ledger, tg):
    pid = _seed(ledger, status="retracted",
                extra_meta={"posted_notified_at": "2026-09-29T12:05:00+00:00", "posted_message_id": 777,
                            "retract_done_at": "2026-09-29T13:00:00+00:00"})
    counters = await feed.feed_cycle()
    assert counters["retracted"] == 1 and counters["posted"] == 0
    assert tg.of("sendMessage") == []
    (edit,) = tg.of("editMessageText")
    assert edit["chat_id"] == OWNER and edit["message_id"] == 777
    assert edit["reply_markup"] == {"inline_keyboard": []}
    assert re.fullmatch(rf"✅ Posted on X · text · run 2026-09-29\n{re.escape(X_URL)}\n\n{_RETRACTED_RE}", edit["text"])
    _no_parse_mode(tg)
    row = _post(ledger, pid)
    assert row["status"] == "retracted" and row["metadata"]["retract_notified_at"]
    assert row["metadata"]["posted_message_id"] == 777
    tg.calls.clear()
    assert (await feed.feed_cycle())["retracted"] == 0 and tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("posted_mid", [None, "777", True])
async def test_a_retract_without_an_editable_message_is_a_new_message(ledger, tg, posted_mid):
    extra = {} if posted_mid is None else {"posted_message_id": posted_mid}
    pid = _seed(ledger, status="retracted", extra_meta=extra)
    assert (await feed.feed_cycle())["retracted"] == 1
    assert tg.of("editMessageText") == []
    (msg,) = tg.of("sendMessage")
    assert re.fullmatch(_RETRACTED_RE + r" · X · text · run 2026-09-29", msg["text"])
    assert "reply_markup" not in msg
    assert _post(ledger, pid)["metadata"]["retract_notified_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_answer", [
    (400, {"ok": False, "description": "Bad Request: message to edit not found"}),
    (502, b""),
])
async def test_a_failed_edit_falls_back_to_a_new_message(ledger, tg, edit_answer):
    pid = _seed(ledger, status="retracted", extra_meta={"posted_message_id": 777})
    tg.script["editMessageText"] = [edit_answer]
    assert (await feed.feed_cycle())["retracted"] == 1
    assert [m for m, _ in tg.calls] == ["editMessageText", "sendMessage"]
    assert re.fullmatch(_RETRACTED_RE + r" · X · text · run 2026-09-29", tg.of("sendMessage")[0]["text"])
    assert _post(ledger, pid)["metadata"]["retract_notified_at"]


@pytest.mark.asyncio
async def test_a_retract_that_reaches_nobody_is_not_stamped_and_retried(ledger, tg):
    pid = _seed(ledger, status="retracted", extra_meta={"posted_message_id": 777})
    before = _snapshot(ledger, pid)
    tg.script["editMessageText"] = [(400, {"ok": False, "description": "Bad Request: message to edit not found"})]
    tg.script["sendMessage"] = [(502, b"")]
    counters = await feed.feed_cycle()
    assert counters["retracted"] == 0 and counters["failed"] == 1
    assert _post(ledger, pid) == before
    assert (await feed.feed_cycle())["retracted"] == 1


# ── alerts ────────────────────────────────────────────────────────────────────


_ALERT_CASES = [
    # (kind, status, extra metadata keeping the row out of the posted / retracted queries)
    ("failed", "failed", {}),
    ("auth", "approved", {}),
    ("x_budget", "approved", {"x_cap_alert_month": "2026-09"}),
    ("retract_failed", "published", {"posted_notified_at": "t", "retract_closed_at": "t"}),
    ("retract_manual", "published", {"posted_notified_at": "t"}),
    ("unknown", "queued", {"publish": {"state": "escalated"}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("status, extra", [
    ("published", {"posted_notified_at": "t"}),             # the owner answered "It's live" / reconcile found it
    ("failed", {}),                                         # the owner answered "Not posted"
    ("queued", {"publish": {"state": "unknown"}}),          # no longer escalated
])
async def test_a_resolved_unknown_alert_is_stamped_but_never_sent(ledger, tg, status, extra):
    """An "outcome UNKNOWN" alert whose post is no longer queued-and-escalated has been answered: a
    late message would only confuse (and carry no buttons). It is marked handled, never sent."""
    pid = _seed(ledger, status=status, extra_meta={"alert_kind": "unknown", "alert_text": "⚠️ unknown",
                                                   "alert_at": "2026-09-29T12:30:00+00:00", **extra})
    counters = await feed.feed_cycle()
    assert counters["alerts"] == 0 and tg.of("sendMessage") == []
    meta = _post(ledger, pid)["metadata"]
    assert meta["alert_notified_at"] and meta["alert_skipped"] == "resolved"
    assert _post(ledger, pid)["status"] == status


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, status, extra", _ALERT_CASES)
async def test_every_alert_kind_is_sent_once_in_any_status(ledger, tg, kind, status, extra):
    text = f"⚠️ alert of kind {kind} — *plain* text"
    pid = _seed(ledger, status=status, extra_meta={"alert_kind": kind, "alert_text": text,
                                                   "alert_at": "2026-09-29T12:30:00+00:00", **extra})
    counters = await feed.feed_cycle()
    assert counters["alerts"] == 1 and counters["posted"] == 0 and counters["retracted"] == 0
    (msg,) = tg.of("sendMessage")
    assert msg["text"] == f"{text}\n(X · text · run 2026-09-29)"
    assert msg["reply_parameters"] == {"message_id": REVIEW_MID, "allow_sending_without_reply": True}
    if kind == "unknown" and status == "queued":
        assert msg["reply_markup"] == {"inline_keyboard": [
            [{"text": "✅ It's live", "callback_data": f"l:{pid}"}],
            [{"text": "❌ Not posted", "callback_data": f"n:{pid}"}]]}
    else:
        assert "reply_markup" not in msg
    _no_parse_mode(tg)
    row = _post(ledger, pid)
    assert row["status"] == status
    assert row["metadata"]["alert_notified_at"] and row["metadata"]["alert_message_id"] == 101
    assert row["metadata"]["alert_kind"] == kind and row["metadata"]["alert_text"] == text
    tg.calls.clear()
    assert (await feed.feed_cycle())["alerts"] == 0 and tg.calls == []


@pytest.mark.asyncio
async def test_an_alert_without_text_falls_back_to_its_kind(ledger, tg):
    _seed(ledger, status="failed", extra_meta={"alert_kind": "failed"})
    await feed.feed_cycle()
    assert tg.of("sendMessage")[0]["text"] == "failed\n(X · text · run 2026-09-29)"


@pytest.mark.asyncio
async def test_an_already_notified_alert_and_a_row_without_one_are_left_alone(ledger, tg):
    _seed(ledger, status="failed", extra_meta={"alert_kind": "failed", "alert_text": "t",
                                               "alert_notified_at": "2026-09-29T12:00:00+00:00"})
    _seed(ledger, status="failed")                       # no alert at all
    _seed(ledger, status="failed", extra_meta={"alert_kind": None, "alert_text": "t"})
    assert await feed.feed_cycle() == {"posted": 0, "retracted": 0, "alerts": 0, "failed": 0, "rate_limited": 0}
    assert tg.calls == []


@pytest.mark.asyncio
async def test_an_alert_whose_post_was_resolved_meanwhile_is_not_stamped(ledger, tg):
    """The owner answers "It's live" while the unknown-outcome alert is being stamped (the row moves
    queued → published): not stamped by the send — and the next cycle sees it resolved, marks it
    handled and sends nothing more."""
    pid = _seed(ledger, status="queued", extra_meta={"alert_kind": "unknown", "alert_text": "⚠️ unknown",
                                                     "publish": {"state": "escalated"}, "posted_notified_at": "t"})

    def resolved_meanwhile(request, payload):
        _post(ledger, pid).update({"status": "published", "updated_at": "2026-09-29T14:00:00+00:00"})
        return None

    tg.script["sendMessage"] = [resolved_meanwhile]
    assert (await feed.feed_cycle())["alerts"] == 0
    assert "alert_notified_at" not in _post(ledger, pid)["metadata"]
    # Next cycle: the alert is now resolved, so it is marked handled and NOT re-sent (a late
    # "outcome UNKNOWN" without buttons would only confuse).
    assert (await feed.feed_cycle())["alerts"] == 0
    (first,) = tg.of("sendMessage")
    assert "reply_markup" in first
    assert _post(ledger, pid)["metadata"]["alert_skipped"] == "resolved"


@pytest.mark.asyncio
async def test_a_newer_alert_written_meanwhile_is_not_marked_sent(ledger, tg):
    """Another writer (a second container during a redeploy overlap) replaces the alert while the
    old one is in flight — same status, new `alert_kind` (the publisher unsets
    `alert_notified_at` with every new alert). The new alert has NOT been sent: stamping it would
    lose it, and for `unknown` it is the owner's only It's live / Not posted buttons."""
    pid = _seed(ledger, status="queued", extra_meta={"alert_kind": "auth", "alert_text": "🔑 X auth failed",
                                                     "posted_notified_at": "t"})

    def escalated_meanwhile(request, payload):
        row = _post(ledger, pid)
        row["metadata"] = {**row["metadata"], "alert_kind": "unknown", "alert_text": "⚠️ outcome UNKNOWN",
                           "publish": {"state": "escalated"}}
        row["updated_at"] = "2026-09-29T14:00:00+00:00"
        return None

    tg.script["sendMessage"] = [escalated_meanwhile]
    await feed.feed_cycle()
    assert "alert_notified_at" not in _post(ledger, pid)["metadata"]
    await feed.feed_cycle()
    second = tg.of("sendMessage")[1]
    assert second["text"].startswith("⚠️ outcome UNKNOWN")
    assert second["reply_markup"] == rs.unknown_outcome_keyboard(pid)


@pytest.mark.asyncio
async def test_a_failed_alert_stamp_resends_the_alert(ledger, tg):
    pid = _seed(ledger, status="failed", extra_meta={"alert_kind": "failed", "alert_text": "❌"})
    ledger.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 503"))
    assert (await feed.feed_cycle())["alerts"] == 0
    assert "alert_notified_at" not in _post(ledger, pid)["metadata"]
    assert (await feed.feed_cycle())["alerts"] == 1
    assert len(tg.of("sendMessage")) == 2


@pytest.mark.asyncio
async def test_the_x_cap_alert_goes_out_once_a_month(ledger, tg, monkeypatch):
    """`publisher_service._cap_alert` marks ONE post a month (`x_cap_alert_month`); the feed then
    sends exactly one message for it. A new month raises a new one."""
    month = {"start": datetime(2026, 9, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr(pub, "month_start_utc", lambda now=None: month["start"])
    budget = pub._Budget(ledger, "x", 2_000_000)
    budget.spent = 1_995_000
    p1, p2, p3 = (_seed(ledger, status="approved", external_id=None, meta={"dry_run": False}) for _ in range(3))
    await pub._cap_alert(ledger, _post(ledger, p1), budget)
    await pub._cap_alert(ledger, _post(ledger, p2), budget)          # same month: nothing
    assert _post(ledger, p1)["metadata"]["alert_kind"] == "x_budget"
    assert "alert_kind" not in _post(ledger, p2)["metadata"]
    assert (await feed.feed_cycle())["alerts"] == 1
    (msg,) = tg.of("sendMessage")
    assert msg["text"].startswith("⚠️ X monthly cap reached: $1.995 of $2.00 spent in 2026-09.")
    assert msg["text"].endswith("\n(X · text · run 2026-09-29)")
    assert (await feed.feed_cycle())["alerts"] == 0
    month["start"] = datetime(2026, 10, 1, tzinfo=timezone.utc)      # the month boundary
    await pub._cap_alert(ledger, _post(ledger, p3), budget)
    assert _post(ledger, p3)["metadata"]["x_cap_alert_month"] == "2026-10"
    assert (await feed.feed_cycle())["alerts"] == 1 and len(tg.of("sendMessage")) == 2


# ── bounds, flood control, configuration ──────────────────────────────────────


@pytest.mark.asyncio
async def test_feed_send_limit_bounds_one_tick_across_every_kind(ledger, tg):
    assert feed.FEED_SEND_LIMIT == 10
    posted = [_seed(ledger) for _ in range(7)]
    retracted = [_seed(ledger, status="retracted") for _ in range(3)]
    alerts = [_seed(ledger, status="failed", extra_meta={"alert_kind": "failed", "alert_text": f"a{i}"})
              for i in range(5)]
    first = await feed.feed_cycle()
    assert first == {"posted": 7, "retracted": 3, "alerts": 0, "failed": 0, "rate_limited": 0}
    assert len(tg.calls) == 10
    # Oldest first.
    assert [m["reply_markup"]["inline_keyboard"][0][0]["callback_data"] for m in tg.of("sendMessage")[:7]] == \
        [f"d:{p}" for p in posted]
    tg.calls.clear()
    second = await feed.feed_cycle()
    assert second == {"posted": 0, "retracted": 0, "alerts": 5, "failed": 0, "rate_limited": 0}
    assert [m["text"].split("\n")[0] for m in tg.of("sendMessage")] == [f"a{i}" for i in range(5)]
    assert all(_post(ledger, p)["metadata"].get("alert_notified_at") for p in alerts)
    assert all(_post(ledger, p)["metadata"].get("retract_notified_at") for p in retracted)


@pytest.mark.asyncio
async def test_a_failed_send_still_counts_against_the_limit(ledger, tg):
    pids = [_seed(ledger) for _ in range(12)]
    tg.script["sendMessage"] = [(502, b""), (502, b"")]
    counters = await feed.feed_cycle()
    assert counters["failed"] == 2 and counters["posted"] == 8 and len(tg.calls) == 10
    assert [bool(_post(ledger, p)["metadata"].get("posted_notified_at")) for p in pids] == \
        [False, False] + [True] * 8 + [False, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("params, expected_wait", [({"retry_after": 30}, 30), ({}, 60)])
async def test_a_429_stops_the_feed_and_holds_off_the_review_sweep_too(ledger, tg, caplog, params, expected_wait):
    pids = [_seed(ledger) for _ in range(3)]
    tg.script["sendMessage"] = [(429, {"ok": False, "description": "Too Many Requests", "parameters": params})]
    caplog.set_level(logging.WARNING)
    t0 = time.monotonic()
    counters = await feed.feed_cycle()
    assert counters["rate_limited"] == 1 and counters["posted"] == 0
    assert len(tg.calls) == 1
    assert t0 + expected_wait - 1 <= rs._rate_limited_until <= time.monotonic() + expected_wait
    assert any("Telegram flood control" in r.getMessage() for r in caplog.records)
    assert all("posted_notified_at" not in _post(ledger, p)["metadata"] for p in pids)
    tg.calls.clear()
    assert (await feed.feed_cycle())["rate_limited"] == 1
    assert (await rs.review_cycle())["rate_limited"] == 1      # one back-off for the one chat
    assert tg.calls == []


@pytest.mark.asyncio
async def test_a_429_on_the_retract_edit_stops_without_a_fallback_message(ledger, tg):
    pid = _seed(ledger, status="retracted", extra_meta={"posted_message_id": 777})
    _seed(ledger, status="failed", extra_meta={"alert_kind": "failed", "alert_text": "x"})
    tg.script["editMessageText"] = [(429, {"ok": False, "parameters": {"retry_after": 5}})]
    counters = await feed.feed_cycle()
    assert counters["rate_limited"] == 1 and counters["retracted"] == 0
    assert [m for m, _ in tg.calls] == ["editMessageText"]
    assert "retract_notified_at" not in _post(ledger, pid)["metadata"]


@pytest.mark.asyncio
async def test_a_back_off_set_by_the_review_sweep_holds_the_feed(ledger, tg, monkeypatch):
    _seed(ledger)
    monkeypatch.setattr(rs, "_rate_limited_until", time.monotonic() + 120)

    def no_ledger():
        raise AssertionError("the feed read the ledger while Telegram was rate limited")

    monkeypatch.setattr(feed, "get_marketing_run_service", no_ledger)
    assert (await feed.feed_cycle())["rate_limited"] == 1
    assert tg.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name, value", [
    ("MARKETING_TELEGRAM_BOT_TOKEN", None),
    ("MARKETING_TELEGRAM_REVIEW_CHAT_ID", None),
    ("MARKETING_TELEGRAM_REVIEW_CHAT_ID", str(OWNER)),
    ("MARKETING_TELEGRAM_WEBHOOK_SECRET", None),
    ("MARKETING_TELEGRAM_WEBHOOK_SECRET", "not a valid secret!"),
    ("MARKETING_PUBLIC_BASE_URL", "http://caydexinvest.com"),
])
async def test_an_unconfigured_bot_is_a_no_op_that_reads_nothing(ledger, tg, monkeypatch, name, value):
    _seed(ledger)
    monkeypatch.setattr(settings, name, value)

    def no_ledger():
        raise AssertionError("an unconfigured feed read the ledger")

    monkeypatch.setattr(feed, "get_marketing_run_service", no_ledger)
    assert await feed.feed_cycle() == {"posted": 0, "retracted": 0, "alerts": 0, "failed": 0, "rate_limited": 0}
    assert tg.calls == []


@pytest.mark.asyncio
async def test_the_feed_never_calls_a_platform(ledger, tg, platforms):
    _seed(ledger)
    _seed(ledger, platform="bluesky", external_id=BSKY_URI, external_url=BSKY_URL)
    _seed(ledger, status="retracted", extra_meta={"posted_message_id": 777})
    _seed(ledger, status="queued", extra_meta={"alert_kind": "unknown", "alert_text": "?",
                                               "publish": {"state": "escalated"}})
    counters = await feed.feed_cycle()
    assert counters["posted"] == 2 and counters["retracted"] == 1 and counters["alerts"] == 1
    assert platforms == {"x": [], "bluesky": []}


@pytest.mark.asyncio
async def test_a_ledger_outage_is_logged_never_raised(ledger, tg, monkeypatch, caplog):
    async def broken(query, **kw):
        raise mrs.MarketingRunError("feed_posted failed: PostgREST 520")

    monkeypatch.setattr(feed, "_exec", broken)
    caplog.set_level(logging.ERROR)
    counters = await feed.feed_cycle()
    assert counters["failed"] >= 1 and tg.calls == []


# ── end to end: Posted → Retract → Confirm → deleted → "Retracted" ────────────


@pytest.mark.asyncio
async def test_the_retract_button_round_trips_to_a_deleted_post(ledger, tg, monkeypatch):
    """The feed's button data parses in the webhook; the webhook only RECORDS the request (no X
    call); the publisher's retract step deletes it; the next feed edits the Posted message."""
    monkeypatch.setattr(pub, "get_marketing_run_service", lambda: ledger)
    wakes: List[int] = []
    monkeypatch.setattr(rs.publisher_wake, "wake", lambda: wakes.append(1))
    x_calls: List[httpx.Request] = []

    def x_handler(request: httpx.Request) -> httpx.Response:
        x_calls.append(request)
        assert request.method == "DELETE" and request.url.path == f"/2/tweets/{X_ID}"
        return httpx.Response(200, json={"data": {"deleted": True}})

    monkeypatch.setattr(x_api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(x_handler)))
    pid = _seed(ledger)
    await feed.feed_cycle()
    (posted,) = tg.of("sendMessage")
    posted_mid = _post(ledger, pid)["metadata"]["posted_message_id"]

    def tap(data):
        return {"update_id": 1, "callback_query": {
            "id": "cbq", "from": {"id": OWNER}, "data": data,
            "message": {"message_id": posted_mid, "chat": {"id": OWNER}, "text": posted["text"]}}}

    retract_data = posted["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert (await rs.handle_update(tap(retract_data)))["outcome"] == "confirm_asked"
    (swap,) = tg.of("editMessageReplyMarkup")
    confirm_data = swap["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert (await rs.handle_update(tap(confirm_data)))["outcome"] == "requested"
    assert x_calls == [] and wakes == [1]
    assert _post(ledger, pid)["status"] == "published"

    assert (await pub.retract_cycle())["retracted"] == 1
    assert len(x_calls) == 1
    row = _post(ledger, pid)
    assert row["status"] == "retracted" and row["cost_micros"] == 10_000
    tg.calls.clear()
    assert (await feed.feed_cycle())["retracted"] == 1
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == posted_mid and re.search(_RETRACTED_RE + "$", edit["text"])
    assert edit["reply_markup"] == {"inline_keyboard": []}


def test_feed_uses_the_review_sweeps_shared_state():
    """One pacer spacing and one flood-control clock for the one chat (design §Telegram)."""
    assert feed._Pacer is rs._Pacer
    src = feed.__loader__.get_source(feed.__name__)  # type: ignore[union-attr]
    assert "review_service._rate_limited_until = " in src
    assert outlets.retract_capable is feed.outlets.retract_capable


# ── the format in every feed message (drop 1, contract C12) ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, fmt, shown", [
    ("bluesky", "image", "image"), ("x", "text", "text"), ("tiktok", "video", "video"),
    # a value outside the ledger's formats is never echoed into a message
    ("x", "<b>image</b>", "?"), ("x", None, "?"), ("x", 7, "?"),
])
async def test_the_posted_message_names_the_posts_format(ledger, tg, platform, fmt, shown):
    _seed(ledger, platform=platform, fmt=fmt, external_id="7300000000000000000",
          external_url="https://example.invalid/post/1")
    await feed.feed_cycle()
    (msg,) = tg.of("sendMessage")
    assert msg["text"].split("\n")[0] == f"✅ Posted on {platform.upper()} · {shown} · run 2026-09-29"
    if shown == "?":
        assert "<b>" not in msg["text"] and "None" not in msg["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt, shown", [("image", "image"), ("<script>", "?")])
async def test_alerts_and_retract_notes_name_the_format_too(ledger, tg, fmt, shown):
    _seed(ledger, status="failed", fmt=fmt, extra_meta={"alert_kind": "failed", "alert_text": "boom"})
    _seed(ledger, status="retracted", fmt=fmt)
    await feed.feed_cycle()
    texts = [m["text"] for m in tg.of("sendMessage")]
    assert re.fullmatch(_RETRACTED_RE + rf" · X · {re.escape(shown)} · run 2026-09-29", texts[0])
    assert texts[1] == f"boom\n(X · {shown} · run 2026-09-29)"
