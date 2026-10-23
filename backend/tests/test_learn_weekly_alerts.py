"""Loud alerts of the Wiser weekly engine (`app/services/learn_weekly/alerts.py`, plan §1.9).

Hermetic: Telegram is an `httpx.MockTransport` and the ledger is the in-memory fake (both from
`test_learn_weekly_publish.py`).

What must never regress:
  * every transition to failed / skipped / expired / owner_rejected that this wave owns alerts: one LOUD
    plain-text line (kind, period, status, last completed stage, reason code) plus an ERROR log with the ids;
  * an alert NEVER raises — not on a Telegram 500, a 429, a missing bot config, a hostile value or a
    non-row piece — and a line it could not send is still in the logs WITH its text;
  * it honours the shared flood gate (no send while closed; a 429 closes it for every sender);
  * values are folded onto one line and capped (no line or markup injection from a title or an error);
  * the Friday deadman: ok when the Deep Dive is in front of the owner; a loud "No Deep Dive in review yet:
    status / last_error" otherwise; FAIL CLOSED on an unreadable ledger row; "unsent" (day stays open)
    when the alert could not be delivered.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from app.services.learn_weekly import alerts, ledger, publisher, review
from app.services.learn_weekly import contracts as c
from app.services.marketing import review_service as rs
from app.services.marketing import telegram_pacing
from test_learn_weekly_publish import (  # noqa: F401 — fixtures are used by name
    AFTER,
    DD,
    DECISION_ID,
    HOLD,
    KEY,
    OWNER,
    PUBLISH,
    _content_cache_restored,
    configured,
    fake,
    make_piece,
    no_narration_module,
    piece_row,
    run,
    tg,
    too_many,
)

NOW = PUBLISH - timedelta(hours=14)


def _piece(**over):
    base = {"id": "9b1d0a52-6b36-4bd5-9f35-5a3f3d9c0c11", "kind": "deep_dive", "period_key": KEY,
            "status": "failed", "last_stage": "writer", "title": "How Data Centres Got Expensive",
            "review_round": 1, "dry_run": False}
    base.update(over)
    return base


# ── notify_terminal ───────────────────────────────────────────────────────────────────────────


def test_a_terminal_transition_is_one_loud_line_and_an_error_log(tg, configured, caplog):
    caplog.set_level(logging.ERROR)
    run(alerts.notify_terminal(_piece(), status=c.FAILED, reason="writer_failures_exhausted"))
    sent = tg.of("sendMessage")
    assert len(sent) == 1
    line = sent[0]["text"]
    for needle in ("Deep Dive", KEY, "failed", "after stage writer", "writer_failures_exhausted",
                   "How Data Centres Got Expensive"):
        assert needle in line, needle
    assert "\n" not in line and "disable_notification" not in sent[0] and "parse_mode" not in sent[0]
    assert "9b1d0a52-6b36-4bd5-9f35-5a3f3d9c0c11" in caplog.text and "writer_failures_exhausted" in caplog.text


def test_the_stage_argument_wins_over_the_rows_last_stage_and_dry_run_is_said(tg, configured):
    run(alerts.notify_terminal(_piece(dry_run=True), status=c.SKIPPED, reason="catch_up_cutoff_passed",
                               stage="build"))
    line = tg.of("sendMessage")[0]["text"]
    assert "after stage build" in line and "DRY RUN" in line and "skipped" in line


def test_hostile_values_are_folded_and_capped(tg, configured):
    run(alerts.notify_terminal(_piece(title="T\nINJECT\r\x00" + "x" * 5000, period_key="2026-W43\n:deep_dive"),
                               status=c.EXPIRED, reason="r\n" * 400, stage="s tage"))
    line = tg.of("sendMessage")[0]["text"]
    assert "\n" not in line and "\r" not in line and "\x00" not in line and " " not in line
    assert len(line) <= alerts.ALERT_TEXT_CAP


@pytest.mark.parametrize("piece", [None, "not a row", 7, {}])
def test_a_non_row_piece_still_alerts(tg, configured, piece):
    run(alerts.notify_terminal(piece, status=c.FAILED, reason="x"))
    assert len(tg.of("sendMessage")) == 1


def test_an_unexpected_status_still_alerts_with_a_warning(tg, configured, caplog):
    run(alerts.notify_terminal(_piece(), status=c.PUBLISHED, reason="odd"))
    assert len(tg.of("sendMessage")) == 1 and "not an alert status" in caplog.text


@pytest.mark.parametrize("answer", [
    (500, {"ok": False, "description": "Internal"}),
    (400, {"ok": False, "description": "Bad Request: chat not found"}),
    RuntimeError("transport bug"),
])
def test_notify_terminal_never_raises_when_telegram_fails(tg, configured, caplog, answer):
    tg.script["sendMessage"] = [answer]
    run(alerts.notify_terminal(_piece(), status=c.FAILED, reason="boom"))      # no exception
    assert "learn weekly alert NOT SENT" in caplog.text and "boom" in caplog.text


def test_notify_never_raises_when_the_bot_is_not_configured(tg, caplog):
    assert run(alerts.notify("hello", loud=True)) is False
    assert tg.calls == [] and "not configured" in caplog.text and "hello" in caplog.text


def test_notify_terminal_never_raises_even_if_notify_itself_breaks(tg, configured, monkeypatch, caplog):
    async def broken(*a, **kw):
        raise RuntimeError("notify bug")
    monkeypatch.setattr(alerts, "notify", broken)
    run(alerts.notify_terminal(_piece(), status=c.FAILED, reason="x"))
    assert "notify_terminal itself failed" in caplog.text


# ── the shared flood gate ─────────────────────────────────────────────────────────────────────


def test_no_send_while_the_chat_is_in_flood_control(tg, configured, caplog, monkeypatch):
    """Checked BEFORE taking a pacer turn (the pacer would refuse too — this pins the cheaper first layer)."""
    waited = []

    class Recorder:
        async def wait(self):
            waited.append(1)
    monkeypatch.setattr(telegram_pacing, "PACER", Recorder())
    telegram_pacing.note_rate_limit(60)
    assert run(alerts.notify("quiet please")) is False
    assert tg.calls == [] and waited == [] and "s more" in caplog.text and "quiet please" in caplog.text


def test_a_429_closes_the_chat_for_every_sender(tg, configured):
    tg.script["sendMessage"] = [too_many(33)]
    assert run(alerts.notify("x")) is False
    assert telegram_pacing.is_rate_limited() and rs._rate_limited_until > 0      # the marketing gate too


def test_notify_waits_its_turn_on_the_shared_pacer(tg, configured, monkeypatch):
    waited = []

    class Recorder:
        async def wait(self):
            waited.append(len(tg.calls))
    monkeypatch.setattr(telegram_pacing, "PACER", Recorder())
    assert run(alerts.notify("x", loud=False)) is True
    assert waited == [0] and tg.of("sendMessage")[0]["disable_notification"] is True


# ── deadman ───────────────────────────────────────────────────────────────────────────────────


def test_deadman_ok_for_every_status_in_front_of_the_owner(fake, tg, configured):
    for status in sorted(alerts.DEADMAN_OK_STATUSES):
        make_piece(fake, status)
        assert run(alerts.deadman_check(DD.deadman_at, DD)) == "ok", status
        fake.tables[ledger.PIECES].rows.clear()
    assert tg.calls == []


def test_deadman_alerts_on_a_missing_piece(fake, tg, configured):
    assert run(alerts.deadman_check(DD.deadman_at, DD)) == "alerted"
    line = tg.of("sendMessage")[0]["text"]
    assert line.startswith("⏰ No Deep Dive in review yet") and KEY in line and "no piece exists" in line
    assert "disable_notification" not in tg.of("sendMessage")[0]


@pytest.mark.parametrize("status", [c.PLANNED, c.TOPIC_PENDING, c.BUILDING, c.MEDIA_PENDING, c.READY_FOR_REVIEW,
                                    c.FAILED, c.SKIPPED])
def test_deadman_alerts_with_status_and_last_error(fake, tg, configured, status):
    make_piece(fake, status, last_error="writer_failures_exhausted\nsecond line")
    assert run(alerts.deadman_check(DD.deadman_at, DD)) == "alerted"
    line = tg.of("sendMessage")[0]["text"]
    assert f"status {status}" in line and "last_error writer_failures_exhausted second line" in line


def test_deadman_fails_closed_on_an_unreadable_row(fake, tg, configured, caplog):
    make_piece(fake, c.IN_REVIEW)                            # it IS in review — but the read fails
    fake.fail_next(ledger.PIECES, "select", RuntimeError("PostgREST 503"))
    assert run(alerts.deadman_check(DD.deadman_at, DD)) == "unreadable"
    line = tg.of("sendMessage")[0]["text"]
    assert "could not read" in line and "UNKNOWN" in line
    assert "failing closed" in caplog.text


def test_deadman_reports_an_undelivered_alert_so_the_day_stays_open(fake, tg, configured):
    tg.script["sendMessage"] = [(500, {"ok": False, "description": "down"})]
    assert run(alerts.deadman_check(DD.deadman_at, DD)) == "unsent"


# ── every terminal transition of this wave alerts (end to end, real Telegram fake) ────────────


@pytest.mark.parametrize("path", ["publish_failure", "review_failure", "reject", "expiry", "recovery_expiry"])
def test_each_terminal_path_alerts_loudly(fake, tg, configured, no_narration_module, path):
    if path == "publish_failure":
        p = make_piece(fake, c.APPROVED, the_draft={"title": "U", "sections": []})
        run(publisher.publish_due(AFTER))
        status, reason = c.FAILED, "draft_missing"
    elif path == "review_failure":
        p = make_piece(fake, c.READY_FOR_REVIEW, draft=None, review_ids=[])
        run(review.review_cycle(NOW))
        status, reason = c.FAILED, "draft_missing"
    elif path == "reject":
        p = make_piece(fake, c.IN_REVIEW)
        assert run(review.handle_learn_callback(cq_id="cq", chat_id=OWNER, from_id=OWNER,
                                                message={"message_id": DECISION_ID}, data=f"y:r:{p['id']}",
                                                now=NOW)) is True
        status, reason = c.OWNER_REJECTED, "owner_rejected"
    elif path == "expiry":
        p = make_piece(fake, c.MEDIA_PENDING)
        run(publisher.expire_due(HOLD))
        status, reason = c.EXPIRED, "hold_ended_in_media_pending"
    else:
        p = make_piece(fake, c.PUBLISHING, publish_lease_until=None)
        run(publisher.publish_due(HOLD + timedelta(minutes=1)))
        status, reason = c.EXPIRED, "publish_not_completed_before_hold_end"
    assert piece_row(fake, p["id"])["status"] == status
    loud = [m for m in tg.of("sendMessage") if "disable_notification" not in m and m["text"].startswith("🔴")]
    assert len(loud) == 1, [m["text"] for m in tg.of("sendMessage")]
    assert status in loud[0]["text"] and reason in loud[0]["text"] and KEY in loud[0]["text"]
