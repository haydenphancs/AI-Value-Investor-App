"""The Telegram review of the Wiser weekly pieces (`app/services/learn_weekly/review.py`, plan §1.9) and
its one seam in the marketing bot (`marketing.review_service.handle_update` → `y:` taps).

Hermetic: the REAL ledger, review and publisher against the in-memory fakes of
`test_learn_weekly_publish.py` (PostgREST + Storage) and an `httpx.MockTransport` Telegram.

What must never regress:
  * `y:` taps are dispatched only AFTER the marketing owner allow-list (a stranger's tap decides nothing),
    marketing's invalid letters stay invalid, and a Learn failure never breaks the webhook;
  * every decision is fenced on the piece status AND the tapped message being the one the ledger recorded
    — a stale button (an older bundle, another piece's message, a re-sent card) decides nothing; double
    taps report "Already …";
  * Approve needs the hold open for APPROVE_HOLD_MARGIN_SECONDS more ("too close" inside it, "too late"
    after it, and the ledger's own CAS demands the same margin) — an approval the publisher cannot reach
    before the hold is refused, never accepted-then-expired;
  * 🗑 on an APPROVED piece withdraws the approval (asks first; one CAS fenced on round + draft, racing the
    publisher's own swap — whichever lands first wins, and the owner is told which);
  * Reject cancels open narration jobs and alerts; reasons are recorded once and belong to ONE round
    (Reject clears the old one; ♻️ from review clears it; ♻️ from owner_rejected carries this draft's);
    Regenerate / Next topic are bounded by the writer caps and the engine cut-off, go back to `planned`
    with `review_round + 1`, and cancel open jobs only AFTER their swap won;
  * the header's worst narration block reads the checks shape `narration.complete` really stores; the
    decision opens with ⚠️ when the judge flagged a directive / valuation verdict; statistic arrows show;
  * Retry narration opens round max + 1 (a failed enqueue goes back to review);
  * the sweep: one loud topic card per waiting Deep Dive; a bundle = silent header, silent audio (a SIGNED
    URL, never logged), silent ≤ 4096-unit body chunks, ONE loud decision; at least once (a lost stamp
    re-sends, the old buttons go stale); the shared flood gate stops it; PACER.wait() before every send.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from app.services.learn_weekly import alerts, ledger, publisher, review, schedule
from app.services.learn_weekly import contracts as c
from app.services.marketing import review_service as rs
from app.services.marketing import telegram_pacing
from test_learn_weekly_publish import (  # noqa: F401 — fixtures are used by name
    AFTER,
    DECISION_ID,
    HOLD,
    KEY,
    OWNER,
    PUBLISH,
    SHA2,
    TITLE,
    LearnFake,
    _content_cache_restored,
    configured,
    draft,
    fake,
    make_piece,
    no_narration_module,
    piece_row,
    run,
    tg,
    too_many,
    topic,
)

UTC = timezone.utc
NOW = PUBLISH - timedelta(hours=14)          # Fri morning ET: inside the hold, before every cut-off
CARD_ID = 7001


def tap(fake_tg, pid: str, code: str, *, message_id: int = DECISION_ID, now: datetime = NOW,
        text: str = "Decide: …") -> bool:
    return run(review.handle_learn_callback(cq_id="cq-1", chat_id=OWNER, from_id=OWNER,
                                            message={"message_id": message_id, "chat": {"id": OWNER}, "text": text},
                                            data=f"y:{code}:{pid}", now=now))


def update(data: str, *, from_id: int = OWNER, chat_id: int = OWNER, message_id: int = DECISION_ID) -> Dict[str, Any]:
    return {"update_id": 1, "callback_query": {"id": "cq-9", "from": {"id": from_id}, "data": data,
                                               "message": {"message_id": message_id, "chat": {"id": chat_id},
                                                           "text": "Decide: …"}}}


@pytest.fixture
def alerts_seen(monkeypatch):
    seen: List[tuple] = []

    async def spy(piece, *, status, reason, stage=None):
        seen.append((piece.get("id"), status, reason, stage))
    monkeypatch.setattr(alerts, "notify_terminal", spy)
    return seen


# ── the seam in the marketing bot ─────────────────────────────────────────────────────────────


def test_y_is_free_of_every_marketing_verb_and_x_b_e_q_stay_invalid():
    assert "y" not in rs._VERBS and "z" not in rs._VERBS
    assert rs.LEARN_CALLBACK_PREFIX == review.CALLBACK_PREFIX == "y:"
    pid = str(uuid.uuid4())
    for letter in "xbeqyz":
        assert rs.parse_callback_data(f"{letter}:{pid}") is None
    assert rs.parse_callback_data(f"y:a:{pid}") is None                 # marketing never parses Learn data


def test_a_strangers_y_tap_is_refused_before_learn_sees_it(fake, tg, configured, monkeypatch):
    called = []

    async def spy(**kw):
        called.append(kw)
        return True
    monkeypatch.setattr(review, "handle_learn_callback", spy)
    p = make_piece(fake, c.IN_REVIEW)
    for upd in (update(f"y:a:{p['id']}", from_id=OWNER + 1), update(f"y:a:{p['id']}", chat_id=OWNER + 1),
                update(f"y:a:{p['id']}", from_id=str(OWNER))):
        assert run(rs.handle_update(upd)) == {"ok": True, "refused": "not_allowed"}
    assert called == []
    assert tg.answers() == ["Not allowed"] * 3
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


def test_the_owners_y_tap_reaches_learn_and_decides(fake, tg, configured):
    now = datetime.now(UTC)
    p = make_piece(fake, c.IN_REVIEW, sched=now - timedelta(hours=1), hold=now + timedelta(hours=40))
    out = run(rs.handle_update(update(f"y:a:{p['id']}")))
    assert out == {"ok": True, "learn": True, "decided": True}
    assert piece_row(fake, p["id"])["status"] == c.APPROVED
    assert piece_row(fake, p["id"])["reviewed_by"] == f"telegram:{OWNER}"


def test_unknown_y_data_is_answered_and_decides_nothing(fake, tg, configured):
    pid = make_piece(fake, c.IN_REVIEW)["id"]
    for data in (f"y:{pid}", f"y:zz:{pid}", f"y:r7:{pid}", f"y:t4:{pid}", f"y:a:{pid.upper()}", f"y:a:{pid}\n",
                 f"y:ucx:{pid}", "y:a:", "y:"):
        assert run(rs.handle_update(update(data))) == {"ok": True, "learn": True, "decided": False}
    assert set(tg.answers()) == {"Unknown action"}
    assert piece_row(fake, pid)["status"] == c.IN_REVIEW


def test_a_learn_crash_is_answered_and_never_breaks_the_webhook(fake, tg, configured, monkeypatch, caplog):
    async def boom(**kw):
        raise RuntimeError("learn exploded")
    monkeypatch.setattr(review, "handle_learn_callback", boom)
    out = run(rs.handle_update(update(f"y:a:{uuid.uuid4()}")))
    assert out == {"ok": True, "outcome": "error", "learn": True}
    assert tg.answers() == ["Could not record that — tap again"]
    assert "the Learn review tap FAILED" in caplog.text


def test_marketing_taps_still_go_to_marketing(fake, tg, configured, monkeypatch):
    called = []

    async def spy(**kw):
        called.append(kw)
        return True
    monkeypatch.setattr(review, "handle_learn_callback", spy)
    out = run(rs.handle_update(update(f"x:{uuid.uuid4()}")))
    assert out == {"ok": True, "refused": "unknown_action"} and called == []


# ── the callback grammar ──────────────────────────────────────────────────────────────────────


def test_every_button_round_trips_and_fits_telegrams_64_bytes():
    pid = str(uuid.uuid4())
    boards = [review.decision_keyboard(pid), review.reasons_keyboard(pid, with_rebuild=True),
              review.unpublish_keyboard(pid), review.confirm_unpublish_keyboard(pid), review.topic_keyboard(pid, 3),
              review.approved_keyboard(pid), review.confirm_withdraw_keyboard(pid)]
    codes = set()
    for board in boards:
        for row in board["inline_keyboard"]:
            for button in row:
                data = button["callback_data"]
                assert len(data.encode()) <= 41
                code, got = review.parse_callback_data(data)
                assert got == pid
                codes.add(code)
    assert codes == set(review.VERBS)
    with pytest.raises(ValueError):
        review.callback_data("a", pid.upper())
    assert review.parse_callback_data(None) is None
    assert review.parse_callback_data(f"y:a:{pid}" + "0" * 40) is None
    assert review.topic_keyboard(pid, 2)["inline_keyboard"] == [[
        {"text": "1", "callback_data": f"y:t1:{pid}"}, {"text": "2", "callback_data": f"y:t2:{pid}"}]]


# ── approve ───────────────────────────────────────────────────────────────────────────────────


def test_approve_records_the_owner_and_offers_withdraw_or_unpublish(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "a") is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.APPROVED and row["reviewed_by"] == f"telegram:{OWNER}"
    assert ledger._parse_ts(row["reviewed_at"]) == NOW
    assert tg.answers() == ["Approved ✅"]
    edit = tg.of("editMessageText")[-1]
    assert edit["message_id"] == DECISION_ID and "✅ Approved" in edit["text"] and "goes live" in edit["text"]
    assert "withdraws the approval" in edit["text"]
    # The same `u` verb the publisher's "Published" reply uses: it withdraws before, unpublishes after.
    assert edit["reply_markup"] == review.approved_keyboard(p["id"])
    assert edit["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"y:u:{p['id']}"


def test_a_dry_run_approve_says_so_and_offers_no_unpublish(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW, dry_run=True)
    assert tap(tg, p["id"], "a") is True
    assert tg.answers() == ["Approved ✅ (DRY RUN — rehearsal only)"]
    assert tg.of("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}


def test_a_double_approve_reports_already(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "a") is True
    assert tap(tg, p["id"], "a") is False
    assert tg.answers() == ["Approved ✅", "Already approved"]


@pytest.mark.parametrize("message_id", [DECISION_ID + 1, None])
def test_a_stale_button_decides_nothing(fake, tg, configured, message_id):
    p = make_piece(fake, c.IN_REVIEW)
    assert run(review.handle_learn_callback(cq_id="cq", chat_id=OWNER, from_id=OWNER,
                                            message={"message_id": message_id}, data=f"y:a:{p['id']}",
                                            now=NOW)) is False
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tg.answers() == ["This button is out of date — use the latest message"]


def test_another_pieces_message_is_stale_for_this_piece(fake, tg, configured):
    a = make_piece(fake, c.IN_REVIEW, review_ids=[111])
    b = make_piece(fake, c.IN_REVIEW, key="2026-W44:deep_dive", slug="other-2026-10-30", title="Other",
                   review_ids=[222])
    assert tap(tg, a["id"], "a", message_id=222) is False
    assert piece_row(fake, a["id"])["status"] == c.IN_REVIEW and piece_row(fake, b["id"])["status"] == c.IN_REVIEW


@pytest.mark.parametrize("status", [c.BUILDING, c.READY_FOR_REVIEW, c.MEDIA_PENDING, c.EXPIRED, c.PUBLISHED])
def test_approve_in_the_wrong_status_decides_nothing(fake, tg, configured, status):
    p = make_piece(fake, status)
    assert tap(tg, p["id"], "a") is False
    assert piece_row(fake, p["id"])["status"] == status
    assert tg.answers()[0].startswith("Not approved — it is ")


def test_approve_after_the_hold_is_too_late(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "a", now=HOLD) is False
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tg.answers() == ["Too late — the review hold ended; it expires unpublished"]
    assert "Too late" in tg.of("editMessageText")[-1]["text"]


def test_the_ledger_refuses_an_approve_that_crosses_the_hold_between_read_and_write(fake, tg, configured,
                                                                                    monkeypatch):
    """Even when the review's own pre-check passes, the ledger's CAS (`hold_until > now`) is the gate."""
    p = make_piece(fake, c.IN_REVIEW)
    original = ledger.transition

    async def late(*a, **kw):
        kw["within_hold_at"] = HOLD                 # the clock moved past the hold mid-tap
        return await original(*a, **kw)
    monkeypatch.setattr(ledger, "transition", late)
    assert tap(tg, p["id"], "a") is False
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tg.answers()[0].startswith("Not approved")


MARGIN = timedelta(seconds=review.APPROVE_HOLD_MARGIN_SECONDS)


@pytest.mark.parametrize("before_hold", [timedelta(seconds=1), timedelta(minutes=2), timedelta(minutes=9),
                                         MARGIN], ids=["1s", "2min", "9min", "exactly-the-margin"])
def test_an_approve_too_close_to_the_hold_end_is_refused_honestly(fake, tg, configured, alerts_seen, before_hold):
    """LW-CC-5: the publisher runs once a tick and refuses a piece whose hold has ended, so an approval in
    the hold's last minutes was accepted ("goes live within minutes") and then EXPIRED. Refused instead,
    with the way on named."""
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "a", now=HOLD - before_hold) is False
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tg.answers() == [review._TOO_CLOSE_TOAST]
    assert "--publish-at" in tg.answers()[0] and len(tg.answers()[0]) <= 200
    edit = tg.of("editMessageText")[-1]["text"]
    assert "Too close" in edit and f"learn_weekly_rebuild.py --period {KEY} --publish-at" in edit


def test_an_approve_just_outside_the_margin_is_accepted(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "a", now=HOLD - MARGIN - timedelta(seconds=1)) is True
    assert piece_row(fake, p["id"])["status"] == c.APPROVED


def test_the_approve_cas_itself_demands_the_margin(fake, tg, configured, monkeypatch):
    """The pre-check reads the hold once; the ledger's conditional UPDATE is the gate. A hold that moved
    to inside the margin between the read and the write is refused by the CAS (`hold_until > now +
    margin`), and the toast says why."""
    # Publish time already passed (a late review), so a hold 5 minutes out is still a legal row.
    p = make_piece(fake, c.IN_REVIEW, sched=NOW - timedelta(hours=1), hold=NOW + timedelta(hours=40))
    seen = []
    original = ledger.transition

    async def spy(*a, **kw):
        seen.append(kw.get("within_hold_at"))
        return await original(*a, **kw)
    monkeypatch.setattr(ledger, "transition", spy)

    def hold_moves_in():
        piece_row(fake, p["id"])["hold_until"] = ledger._iso(NOW + timedelta(minutes=5))
    fake.before_update[ledger.PIECES] = [hold_moves_in]
    assert tap(tg, p["id"], "a") is False
    assert seen == [NOW + MARGIN]
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tg.answers() == [review._TOO_CLOSE_TOAST]


def test_the_margin_covers_two_scheduler_ticks_and_the_decision_says_the_deadline():
    from app.services.learn_weekly import scheduler

    assert review.APPROVE_HOLD_MARGIN_SECONDS >= 2 * scheduler.TICK_SECONDS
    piece = {"kind": c.KIND_DEEP_DIVE, "period_key": KEY, "review_round": 1,
             "scheduled_publish_at": ledger._iso(PUBLISH), "hold_until": ledger._iso(HOLD)}
    text = review.decision_text(piece, TITLE)
    assert f"Approve by {review._et(HOLD - MARGIN)}" in text and f"the hold ends {review._et(HOLD)}" in text


def test_an_approve_whose_draft_changed_under_it_decides_nothing(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)

    def new_draft():
        piece_row(fake, p["id"])["draft_sha256"] = SHA2      # timings merged after the owner opened it
    fake.before_update[ledger.PIECES] = [new_draft]
    assert tap(tg, p["id"], "a") is False
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


def test_a_tap_on_a_vanished_piece_is_answered(fake, tg, configured):
    assert tap(tg, str(uuid.uuid4()), "a") is False
    assert tg.answers() == ["Piece not found"]


def test_a_ledger_failure_during_a_tap_is_answered_once(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    fake.fail_next(ledger.PIECES, "update", RuntimeError("PostgREST 503"))
    assert tap(tg, p["id"], "a") is False
    assert tg.answers() == ["Could not record that — tap again"]
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


# ── reject and reasons ────────────────────────────────────────────────────────────────────────


def test_reject_cancels_open_jobs_alerts_and_offers_reasons(fake, tg, configured, alerts_seen):
    p = make_piece(fake, c.IN_REVIEW)
    j2 = ledger.enqueue_job_sync(p["id"], round=2, script=[{"i": 0}], script_sha256=SHA2, engine="kokoro",
                                 voice="am_michael", pipeline_version="p1", sb=fake)
    assert tap(tg, p["id"], "r") is True
    assert piece_row(fake, p["id"])["status"] == c.OWNER_REJECTED
    assert fake.row(ledger.JOBS, j2["id"])["status"] == c.JOB_CANCELLED
    assert alerts_seen == [(p["id"], c.OWNER_REJECTED, "owner_rejected", "review")]
    edit = tg.of("editMessageText")[-1]
    assert edit["reply_markup"] == review.reasons_keyboard(p["id"], with_rebuild=True)
    assert tap(tg, p["id"], "r") is False and tg.answers()[-1] == "Already rejected"


def test_a_reason_is_recorded_once_and_a_later_one_wins(fake, tg, configured, alerts_seen):
    p = make_piece(fake, c.OWNER_REJECTED)
    assert tap(tg, p["id"], "r2") is True
    assert piece_row(fake, p["id"])["reject_reason"] == "accuracy"
    assert tap(tg, p["id"], "r2") is False and tg.answers()[-1] == "Reason already saved: Accuracy"
    assert tap(tg, p["id"], "r1") is True and piece_row(fake, p["id"])["reject_reason"] == "tone"


def test_no_reason_on_a_piece_that_was_not_turned_down(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "r3") is False
    assert piece_row(fake, p["id"])["reject_reason"] is None
    assert tg.answers() == ["No reason recorded — it is in review"]


def test_a_reason_on_a_stale_message_is_refused(fake, tg, configured):
    p = make_piece(fake, c.OWNER_REJECTED)
    assert tap(tg, p["id"], "r6", message_id=4) is False
    assert piece_row(fake, p["id"])["reject_reason"] is None


# ── regenerate / next topic ───────────────────────────────────────────────────────────────────


@pytest.fixture
def ledger_allows_reopen(monkeypatch):
    """The ledger with contract 4's rule (in_review / topic_pending → planned) — what this module asks the
    ledger owner for. Without it, the fallback path runs (tested separately)."""
    allowed = dict(ledger._ONLY_FROM)
    allowed[c.PLANNED] = frozenset(allowed[c.PLANNED] | {c.IN_REVIEW, c.READY_FOR_REVIEW, c.TOPIC_PENDING})
    monkeypatch.setattr(ledger, "_ONLY_FROM", allowed)


@pytest.mark.parametrize("direct", [False, True], ids=["two-step-fallback", "direct-ledger-rule"])
def test_regenerate_from_review_reopens_and_clears_an_older_rounds_reason(fake, tg, configured, monkeypatch, direct):
    """F8: the writer reads `reject_reason` on every round it builds, so round 1's "tone" must not keep
    steering round 3 when the owner tapped ♻️ on round 2 without a reason — on both reopen paths (the
    two-step one leaves `owner_rejected` in between, which must already carry the cleared reason)."""
    allowed = dict(ledger._ONLY_FROM)
    allowed[c.PLANNED] = (frozenset(allowed[c.PLANNED] | {c.IN_REVIEW}) if direct
                          else frozenset(allowed[c.PLANNED] - {c.IN_REVIEW}))
    monkeypatch.setattr(ledger, "_ONLY_FROM", allowed)
    p = make_piece(fake, c.IN_REVIEW, reject_reason="tone", job="queued")
    statuses = []
    original = ledger.transition

    async def spy(*a, **kw):
        statuses.append((kw["to_status"], (kw.get("fields") or {}).get("reject_reason", "absent")))
        return await original(*a, **kw)
    monkeypatch.setattr(ledger, "transition", spy)
    assert tap(tg, p["id"], "g") is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.PLANNED and row["review_round"] == 2 and row["reject_reason"] is None
    assert statuses[:2] == ([(c.PLANNED, None)] if direct else [(c.OWNER_REJECTED, None), (c.PLANNED, None)])
    assert row["topic"]["chosen_index"] == 0
    assert fake.row(ledger.JOBS, p["narration_job_id"])["status"] == c.JOB_CANCELLED
    assert tg.answers() == ["Regenerating"]
    edit = tg.of("editMessageText")[-1]
    # The reasons keyboard (without rebuild buttons) is offered for the rewrite, and its timing limit said.
    assert edit["reply_markup"] == review.reasons_keyboard(p["id"], with_rebuild=False)
    assert "before the build starts" in edit["text"]
    # A reason tapped while the piece is still `planned` is recorded (it feeds this rewrite).
    assert tap(tg, p["id"], "r4") is True and piece_row(fake, p["id"])["reject_reason"] == "weak"
    assert tap(tg, p["id"], "g") is False                      # planned now: a second tap decides nothing


def test_regenerate_from_a_rejected_piece_carries_this_drafts_reason(fake, tg, configured):
    p = make_piece(fake, c.OWNER_REJECTED, reject_reason="accuracy")
    assert tap(tg, p["id"], "g") is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.PLANNED and row["review_round"] == 2 and row["reject_reason"] == "accuracy"
    assert "Your reason (accuracy) feeds the rewrite" in tg.of("editMessageText")[-1]["text"]


def test_reject_clears_the_older_rounds_reason_so_only_this_drafts_reason_is_carried(fake, tg, configured,
                                                                                      alerts_seen):
    """F8: round 2 is in review still carrying round 1's "tone". ❌ then ♻️ with NO new reason must rebuild
    with none; ❌, "Accuracy", ♻️ must rebuild with "accuracy"."""
    p = make_piece(fake, c.IN_REVIEW, reject_reason="tone", review_round=2)
    assert tap(tg, p["id"], "r") is True
    assert piece_row(fake, p["id"])["reject_reason"] is None
    assert tap(tg, p["id"], "g") is True
    assert piece_row(fake, p["id"])["reject_reason"] is None and piece_row(fake, p["id"])["review_round"] == 3

    q = make_piece(fake, c.IN_REVIEW, key="2026-W44:deep_dive", slug="q-2026-10-30", title="Q",
                   reject_reason="tone", review_round=2)
    assert tap(tg, q["id"], "r") is True and tap(tg, q["id"], "r2") is True
    assert tap(tg, q["id"], "g") is True
    row = piece_row(fake, q["id"])
    assert row["status"] == c.PLANNED and row["reject_reason"] == "accuracy" and row["review_round"] == 3


def test_reopen_takes_a_reason_given_in_the_same_action(fake):
    p = make_piece(fake, c.IN_REVIEW, reject_reason="tone")
    row = run(review.reopen(piece_row(fake, p["id"]), fields={"reject_reason": "compliance"}))
    assert row["status"] == c.PLANNED and row["reject_reason"] == "compliance"


def test_a_regenerate_that_loses_to_a_concurrent_retry_narration_cancels_nothing(fake, tg, configured):
    """LW-CC-2: 🔁 and ♻️ handled concurrently. Retry narration queues job J and wins in_review →
    media_pending (pointer J) AFTER Regenerate read the piece but BEFORE any of Regenerate's writes.
    Cancelling first would cancel J and leave the piece media_pending on a job that never runs (the week
    lost at the hold); swapping first loses cleanly and cancels nothing."""
    p = make_piece(fake, c.IN_REVIEW)
    landed: Dict[str, Any] = {}

    def retry_lands():
        if landed:
            return
        job = ledger.enqueue_job_sync(p["id"], round=2, script=[{"i": 0}], script_sha256=SHA2, engine="chatterbox",
                                      voice="caydex_v2", pipeline_version="p1", sb=fake)
        piece_row(fake, p["id"]).update(status=c.MEDIA_PENDING, narration_job_id=job["id"])
        landed["job"] = job["id"]
    # Whichever of Regenerate's writes comes FIRST (the job cancel or the piece swap) sees Retry's write.
    fake.before_update[ledger.JOBS] = [retry_lands]
    fake.before_update[ledger.PIECES] = [retry_lands]
    assert tap(tg, p["id"], "g") is False
    row = piece_row(fake, p["id"])
    assert row["status"] == c.MEDIA_PENDING and row["narration_job_id"] == landed["job"]
    assert fake.row(ledger.JOBS, landed["job"])["status"] == c.JOB_QUEUED          # the new render still runs
    assert tg.answers() == ["Not regenerated — it is being narrated now"]


def test_a_regenerate_cancels_open_jobs_only_after_its_swap_won(fake, tg, configured, monkeypatch):
    p = make_piece(fake, c.IN_REVIEW, job="queued")
    order: List[str] = []
    transition, cancel = ledger.transition, ledger.cancel_open_jobs

    async def t_spy(*a, **kw):
        order.append(f"transition→{kw['to_status']}")
        return await transition(*a, **kw)

    async def c_spy(*a, **kw):
        order.append("cancel_open_jobs")
        return await cancel(*a, **kw)
    monkeypatch.setattr(ledger, "transition", t_spy)
    monkeypatch.setattr(ledger, "cancel_open_jobs", c_spy)
    assert tap(tg, p["id"], "n") is True
    assert order[:2] == [f"transition→{c.PLANNED}", "cancel_open_jobs"]
    assert fake.row(ledger.JOBS, p["narration_job_id"])["status"] == c.JOB_CANCELLED


def test_next_topic_moves_to_the_runner_up_and_copies_its_category_and_subjects(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "n") is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.PLANNED and row["topic"]["chosen_index"] == 1 and row["review_round"] == 2
    assert row["category"] == c.CATEGORIES[1]
    assert row["subjects"] == [{"name": "Company 1 Inc.", "cik": "0000000002", "ticker": "AVGO"}]
    assert tg.answers() == ["Next topic: Topic 2 label"]


def test_next_topic_after_the_last_candidate_is_refused(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW, with_topic=topic(chosen=2))
    assert tap(tg, p["id"], "n") is False
    assert tg.answers() == ["No next topic — this was the last candidate"]
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


def test_a_rebuild_past_the_engine_cut_off_is_refused(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    cutoff = schedule.catch_up_cutoff(HOLD, "chatterbox", c.DEEP_DIVE_WORDS[1])
    for code in ("g", "n"):
        assert tap(tg, p["id"], code, now=cutoff) is False
    assert all(a.startswith("Too late to rebuild") for a in tg.answers())
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tap(tg, p["id"], "g", now=cutoff - timedelta(seconds=1)) is True


@pytest.mark.parametrize("column,value", [("content_rejections", 2), ("writer_failures", 3)])
def test_a_rebuild_at_the_writer_caps_is_refused(fake, tg, configured, column, value):
    p = make_piece(fake, c.IN_REVIEW, **{column: value})
    assert tap(tg, p["id"], "g") is False
    assert tg.answers()[0].startswith("Writer cap reached")
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


def test_rebuild_problem_fails_closed_on_an_unreadable_hold():
    assert review.rebuild_problem({"hold_until": None}, NOW, next_topic=False).startswith("Cannot rebuild")


# ── retry narration ───────────────────────────────────────────────────────────────────────────


class _Narration:
    def __init__(self, fake: LearnFake, fail: bool = False) -> None:
        self.fake, self.fail, self.calls = fake, fail, []

    async def enqueue_for_piece(self, piece, content, *, engine, round):
        self.calls.append((piece["id"], engine, round, content.get("title")))
        if self.fail:
            raise RuntimeError("narration queue down")
        return ledger.enqueue_job_sync(piece["id"], round=round, script=[{"i": 0}], script_sha256=SHA2,
                                       engine=engine, voice="v", pipeline_version="p2", sb=self.fake)


def test_retry_narration_opens_the_next_round(fake, tg, configured, monkeypatch):
    stub = _Narration(fake)
    monkeypatch.setattr(review, "_narration", lambda: stub)
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "m") is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.MEDIA_PENDING
    assert stub.calls == [(p["id"], "chatterbox", 2, TITLE)]
    new_job = [j for j in fake.tables[ledger.JOBS].rows if j["round"] == 2][0]
    assert row["narration_job_id"] == new_job["id"]
    assert tg.answers() == ["Narration re-queued 🔁"]


def test_a_failed_enqueue_changes_nothing_and_the_buttons_stay(fake, tg, configured, monkeypatch, caplog):
    monkeypatch.setattr(review, "_narration", lambda: _Narration(fake, fail=True))
    p = make_piece(fake, c.IN_REVIEW)
    before = dict(piece_row(fake, p["id"]))
    assert tap(tg, p["id"], "m") is False
    assert piece_row(fake, p["id"]) == before                       # not even updated_at moved
    assert tg.answers() == ["Could not queue the narration — tap again"]
    assert "queueing narration for piece" in caplog.text
    assert tg.of("editMessageText") == []                           # the decision buttons stay


def test_retry_narration_moves_status_and_job_pointer_together(fake, tg, configured, monkeypatch):
    """The narration completion handler accepts only the job `narration_job_id` names: the swap that
    moves the piece to media_pending carries the new pointer in the SAME write."""
    stub = _Narration(fake)
    monkeypatch.setattr(review, "_narration", lambda: stub)
    p = make_piece(fake, c.IN_REVIEW)
    writes = []
    original = ledger.transition

    async def spy(*a, **kw):
        writes.append((kw.get("to_status"), dict(kw.get("fields") or {})))
        return await original(*a, **kw)
    monkeypatch.setattr(ledger, "transition", spy)
    assert tap(tg, p["id"], "m") is True
    assert [w[0] for w in writes] == [c.MEDIA_PENDING]
    assert writes[0][1]["narration_job_id"] == piece_row(fake, p["id"])["narration_job_id"] != p["narration_job_id"]


def test_a_retry_whose_swap_loses_leaves_the_piece_as_it_moved(fake, tg, configured, monkeypatch):
    monkeypatch.setattr(review, "_narration", lambda: _Narration(fake))
    p = make_piece(fake, c.IN_REVIEW)

    def approved_meanwhile():
        piece_row(fake, p["id"])["status"] = c.APPROVED
    fake.before_update[ledger.PIECES] = [approved_meanwhile]
    assert tap(tg, p["id"], "m") is False
    row = piece_row(fake, p["id"])
    assert row["status"] == c.APPROVED and row["narration_job_id"] == p["narration_job_id"]
    assert tg.answers() == ["Not re-narrated — it is approved now"]


def test_retry_narration_without_the_narration_module_is_refused(fake, tg, configured, monkeypatch):
    monkeypatch.setattr(review, "_narration", lambda: None)
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "m") is False
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW
    assert tg.answers() == ["Narration retry is unavailable — see the logs"]


def test_retry_narration_past_its_cut_off_is_refused(fake, tg, configured, monkeypatch):
    monkeypatch.setattr(review, "_narration", lambda: _Narration(fake))
    p = make_piece(fake, c.IN_REVIEW)
    cutoff = schedule.catch_up_cutoff(HOLD, "chatterbox", c.DEEP_DIVE_WORDS[1], build_seconds=0)
    assert tap(tg, p["id"], "m", now=cutoff) is False
    assert tg.answers() == ["Too late to re-narrate before the hold ends"]


# ── topic pick ────────────────────────────────────────────────────────────────────────────────


def test_a_pick_on_a_planned_piece_chooses_and_stays_planned(fake, tg, configured):
    p = make_piece(fake, c.PLANNED, with_topic=topic(chosen=None, cards=[CARD_ID]))
    assert tap(tg, p["id"], "t2", message_id=CARD_ID) is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.PLANNED
    assert row["topic"]["chosen_index"] == 1 and row["topic"]["owner_pick"] == 1
    assert row["topic"]["auto_picked"] is False and row["category"] == c.CATEGORIES[1]
    assert tg.answers() == ["Picked #2: Topic 2 label"]
    assert row["reviewed_by"] is None                      # a pick is not a draft review


def test_a_pick_on_topic_pending_moves_to_planned_under_the_contract_ledger(fake, tg, configured,
                                                                          ledger_allows_reopen):
    p = make_piece(fake, c.TOPIC_PENDING, with_topic=topic(chosen=None, cards=[CARD_ID]))
    assert tap(tg, p["id"], "t1", message_id=CARD_ID) is True
    assert piece_row(fake, p["id"])["status"] == c.PLANNED


def test_a_pick_on_topic_pending_moves_the_piece_into_the_build_queue(fake, tg, configured, caplog):
    p = make_piece(fake, c.TOPIC_PENDING, with_topic=topic(chosen=None, cards=[CARD_ID]))
    assert tap(tg, p["id"], "t1", message_id=CARD_ID) is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.PLANNED and row["topic"]["chosen_index"] == 0
    assert "the ledger refuses topic_pending → planned" not in caplog.text


def test_a_pick_from_a_stale_card_or_out_of_range_or_twice(fake, tg, configured):
    p = make_piece(fake, c.PLANNED, with_topic=topic(chosen=None, n=2, cards=[CARD_ID]))
    assert tap(tg, p["id"], "t1", message_id=CARD_ID - 1) is False        # an older card
    assert tap(tg, p["id"], "t3", message_id=CARD_ID) is False            # only two candidates
    assert tap(tg, p["id"], "t2", message_id=CARD_ID) is True
    assert tap(tg, p["id"], "t1", message_id=CARD_ID) is False
    assert tg.answers() == ["This button is out of date — use the latest message", "No such candidate",
                            "Picked #2: Topic 2 label", "Already picked #2"]
    assert piece_row(fake, p["id"])["topic"]["chosen_index"] == 1


# ── auto-pick ─────────────────────────────────────────────────────────────────────────────────


def test_auto_pick_waits_until_friday_7_then_picks_number_one(fake, tg, configured):
    p = make_piece(fake, c.PLANNED, with_topic=topic(chosen=None, cards=[CARD_ID]))
    dd = schedule.times_for_period_key(KEY)
    assert run(review.auto_pick(dd.auto_pick_at - timedelta(seconds=1)))["waiting"] == 1
    assert piece_row(fake, p["id"])["topic"]["chosen_index"] is None
    out = run(review.auto_pick(dd.auto_pick_at))
    assert out["picked"] == 1
    t = piece_row(fake, p["id"])["topic"]
    assert t["chosen_index"] == 0 and t["auto_picked"] is True and t["owner_pick"] is None
    note = tg.of("sendMessage")[-1]
    assert "auto-picked" in note["text"] and note["disable_notification"] is True
    assert note["reply_parameters"]["message_id"] == CARD_ID
    assert run(review.auto_pick(dd.auto_pick_at + timedelta(hours=1)))["picked"] == 0     # never twice


def test_auto_pick_never_overrides_the_owners_pick(fake, tg, configured):
    p = make_piece(fake, c.PLANNED, with_topic=topic(chosen=2, cards=[CARD_ID]))
    assert run(review.auto_pick(PUBLISH))["picked"] == 0
    assert piece_row(fake, p["id"])["topic"]["chosen_index"] == 2


def test_auto_pick_skips_an_unreadable_period_key(fake, tg, configured, caplog):
    p = make_piece(fake, c.PLANNED, with_topic=topic(chosen=None))
    piece_row(fake, p["id"])["period_key"] = "garbage"
    assert run(review.auto_pick(PUBLISH))["failed"] == 1
    assert "not auto-picked" in caplog.text


# ── unpublish (two steps) ─────────────────────────────────────────────────────────────────────


def _live(fake) -> Dict[str, Any]:
    p = make_piece(fake, c.PUBLISHED)
    row, problem = publisher.build_article_row(piece_row(fake, p["id"]), fake.row(ledger.JOBS, p["narration_job_id"]),
                                               now=AFTER)
    assert problem is None
    fake.tables[publisher.ARTICLES].rows.append(row)
    return p


def test_unpublish_asks_then_confirm_removes_it(fake, tg, configured, no_narration_module):
    p = _live(fake)
    assert tap(tg, p["id"], "u", now=AFTER) is True
    assert tg.of("editMessageText")[-1]["reply_markup"] == review.confirm_unpublish_keyboard(p["id"])
    assert "next session" in tg.of("editMessageText")[-1]["text"]
    assert fake.tables[publisher.ARTICLES].rows                             # nothing removed yet
    assert tap(tg, p["id"], "uc", now=AFTER) is True
    assert fake.tables[publisher.ARTICLES].rows == []
    assert piece_row(fake, p["id"])["status"] == c.UNPUBLISHED
    assert tg.answers() == ["Confirm the unpublish", "Unpublished 🗑"]
    assert tap(tg, p["id"], "uc", now=AFTER) is False and tg.answers()[-1] == "Already unpublished"


def test_keep_restores_the_unpublish_button(fake, tg, configured, no_narration_module):
    p = _live(fake)
    assert tap(tg, p["id"], "uk", now=AFTER) is True
    assert tg.of("editMessageText")[-1]["reply_markup"] == review.unpublish_keyboard(p["id"])
    assert piece_row(fake, p["id"])["status"] == c.PUBLISHED


def test_unpublish_on_a_piece_that_is_neither_approved_nor_live(fake, tg, configured):
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "u") is False and tap(tg, p["id"], "uc") is False
    d = make_piece(fake, c.REHEARSED, dry_run=True, key="2026-W44:deep_dive", slug="d-2026-10-30", title="D")
    assert tap(tg, d["id"], "u") is False
    a = make_piece(fake, c.APPROVED, dry_run=True, key="2026-W45:deep_dive", slug="a-2026-11-06", title="A")
    assert tap(tg, a["id"], "u") is False and tap(tg, a["id"], "uc") is False
    assert tg.answers() == ["Nothing to unpublish — it is in review", "Not unpublished — it is in review",
                            "DRY RUN pieces never go live — nothing to unpublish",
                            "DRY RUN pieces never go live — nothing to unpublish", "Not unpublished — it is approved"]
    assert piece_row(fake, a["id"])["status"] == c.APPROVED and piece_row(fake, p["id"])["status"] == c.IN_REVIEW


# ── withdraw an approval (🗑 before it goes live) ─────────────────────────────────────────────


def test_withdraw_asks_then_stops_the_publish_cancels_jobs_and_alerts(fake, tg, configured, alerts_seen,
                                                                     no_narration_module):
    """F1: approved at noon, an error spotted at 13:00 — 🗑 must stop the 23:00 publish. Before, the
    only button left answered "Nothing to unpublish — it is approved" and the piece went live."""
    p = make_piece(fake, c.IN_REVIEW)
    stray = ledger.enqueue_job_sync(p["id"], round=2, script=[{"i": 0}], script_sha256=SHA2, engine="chatterbox",
                                    voice="caydex_v2", pipeline_version="p1", sb=fake)
    assert tap(tg, p["id"], "a") is True
    assert tap(tg, p["id"], "u", now=NOW + timedelta(hours=1)) is True
    ask = tg.of("editMessageText")[-1]
    assert ask["reply_markup"] == review.confirm_withdraw_keyboard(p["id"]) and "Withdraw the approval?" in ask["text"]
    assert piece_row(fake, p["id"])["status"] == c.APPROVED                       # nothing decided yet
    assert tap(tg, p["id"], "uc", now=NOW + timedelta(hours=1)) is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.OWNER_REJECTED and row["reviewed_by"] == f"telegram:{OWNER}"
    assert row["reject_reason"] is None
    assert fake.row(ledger.JOBS, stray["id"])["status"] == c.JOB_CANCELLED
    assert alerts_seen == [(p["id"], c.OWNER_REJECTED, "approval_withdrawn", "review")]
    assert tg.answers() == ["Approved ✅", "Confirm the withdrawal", "Approval withdrawn — it will not go live"]
    assert tg.of("editMessageText")[-1]["reply_markup"] == review.reasons_keyboard(p["id"], with_rebuild=True)
    # The publish tick at 23:00 finds nothing to publish.
    out = run(publisher.publish_due(AFTER))
    assert out.get("published") is None and fake.articles() == []
    assert piece_row(fake, p["id"])["status"] == c.OWNER_REJECTED


def test_a_withdraw_that_lands_first_makes_the_publishers_stale_swap_lose(fake, tg, configured, alerts_seen,
                                                                         no_narration_module):
    """The publisher read the piece approved; the withdraw lands before its approved → publishing swap. The
    swap is fenced on status + round + draft, so it loses and nothing goes live."""
    p = make_piece(fake, c.APPROVED)
    stale = dict(piece_row(fake, p["id"]))
    assert tap(tg, p["id"], "u", now=AFTER) is True and tap(tg, p["id"], "uc", now=AFTER) is True
    assert run(publisher._publish_approved(stale, AFTER)) == "lost"
    assert fake.articles() == [] and piece_row(fake, p["id"])["status"] == c.OWNER_REJECTED


@pytest.mark.parametrize("winner", [c.PUBLISHING, c.PUBLISHED])
def test_a_withdraw_that_loses_to_the_publisher_says_so_and_offers_unpublish(fake, tg, configured, alerts_seen,
                                                                            winner):
    p = make_piece(fake, c.APPROVED)
    assert tap(tg, p["id"], "u", now=AFTER) is True

    def publisher_first():
        piece_row(fake, p["id"])["status"] = winner
    fake.before_update[ledger.PIECES] = [publisher_first]
    assert tap(tg, p["id"], "uc", now=AFTER) is False
    assert piece_row(fake, p["id"])["status"] == winner and alerts_seen == []
    assert tg.answers()[-1] == "Too late — it went live first; use 🗑 Unpublish"
    edit = tg.of("editMessageText")[-1]
    assert "Not withdrawn" in edit["text"] and edit["reply_markup"] == review.unpublish_keyboard(p["id"])
    assert ("tap again in a minute" in edit["text"]) is (winner == c.PUBLISHING)


@pytest.mark.parametrize("column,value", [("review_round", 2), ("draft_sha256", SHA2)])
def test_a_withdraw_is_fenced_on_the_round_and_draft_that_were_approved(fake, tg, configured, alerts_seen,
                                                                       column, value):
    p = make_piece(fake, c.APPROVED)

    def moved():
        piece_row(fake, p["id"])[column] = value
    fake.before_update[ledger.PIECES] = [moved]
    assert tap(tg, p["id"], "uc") is False
    assert piece_row(fake, p["id"])["status"] == c.APPROVED and alerts_seen == []
    assert tg.answers() == ["Not withdrawn — it is approved now"]


def test_keep_on_an_approved_piece_keeps_it_approved(fake, tg, configured):
    p = make_piece(fake, c.APPROVED)
    assert tap(tg, p["id"], "uk") is True
    assert piece_row(fake, p["id"])["status"] == c.APPROVED
    edit = tg.of("editMessageText")[-1]
    assert edit["reply_markup"] == review.approved_keyboard(p["id"]) and "still approved" in edit["text"]


def test_a_withdraw_from_a_stale_message_decides_nothing(fake, tg, configured):
    p = make_piece(fake, c.APPROVED)
    assert tap(tg, p["id"], "uc", message_id=DECISION_ID + 7) is False
    assert piece_row(fake, p["id"])["status"] == c.APPROVED
    assert tg.answers() == ["This button is out of date — use the latest message"]


def test_a_withdrawn_piece_can_be_regenerated_with_the_reason_given_after(fake, tg, configured, alerts_seen):
    p = make_piece(fake, c.APPROVED, reject_reason="tone")
    assert tap(tg, p["id"], "uc") is True
    assert tap(tg, p["id"], "r2") is True and tap(tg, p["id"], "g") is True
    row = piece_row(fake, p["id"])
    assert row["status"] == c.PLANNED and row["reject_reason"] == "accuracy" and row["review_round"] == 2


def test_a_confirm_offered_on_a_new_message_is_recorded_so_its_buttons_work(fake, tg, configured,
                                                                            no_narration_module):
    p = _live(fake)
    tg.script["editMessageText"] = [(400, {"ok": False, "error_code": 400,
                                           "description": "Bad Request: message can't be edited"})]
    assert tap(tg, p["id"], "u", now=AFTER) is True
    ids = piece_row(fake, p["id"])["review_message_ids"]
    new_ids = [i for i in ids if i != DECISION_ID]
    assert len(new_ids) == 1 and tg.of("sendMessage")[-1]["reply_markup"] == review.confirm_unpublish_keyboard(p["id"])
    new_id = new_ids[0]
    assert tap(tg, p["id"], "uc", message_id=new_id, now=AFTER) is True
    assert piece_row(fake, p["id"])["status"] == c.UNPUBLISHED


# ── the sweep: topic cards ────────────────────────────────────────────────────────────────────


def test_a_topic_card_is_sent_once_loud_with_one_button_per_candidate(fake, tg, configured):
    p = make_piece(fake, c.TOPIC_PENDING, with_topic=topic(chosen=None))
    out = run(review.review_cycle(NOW))
    assert out["topic_cards"] == 1 and out["stamped"] == 1
    card = tg.of("sendMessage")[-1]
    assert "disable_notification" not in card
    assert [b["callback_data"] for b in card["reply_markup"]["inline_keyboard"][0]] == [
        f"y:t{i}:{p['id']}" for i in (1, 2, 3)]
    for i in range(3):
        assert f"{i + 1}. Topic {i + 1} label" in card["text"]
        assert f"https://www.sec.gov/Archives/edgar/data/{i + 1}/x-index.htm" in card["text"]
    assert "Context only" in card["text"] and "NVDA" in card["text"]
    stored = piece_row(fake, p["id"])["topic"]
    assert stored["card_message_ids"] == [tg._next_id] and stored["chosen_index"] is None
    run(review.review_cycle(NOW))
    assert len(tg.of("sendMessage")) == 1                                    # never twice


def test_no_card_for_a_chosen_topic_a_slot_or_a_closed_hold(fake, tg, configured):
    make_piece(fake, c.PLANNED, with_topic=topic(chosen=0))
    make_piece(fake, c.PLANNED, key="2026-W44:deep_dive", with_topic=topic(chosen=None), slug="b-2026-10-30",
               title="B", sched=NOW - timedelta(hours=1), hold=NOW)
    when = schedule.slot_times_for(datetime(2026, 10, 26).date())
    make_piece(fake, c.TOPIC_PENDING, key=when.period_key, sched=when.publish_at, hold=when.hold_until,
               with_topic=topic(chosen=None), slug="s-2026-10-26", title="S")
    assert run(review.review_cycle(NOW))["topic_cards"] == 0
    assert tg.of("sendMessage") == []


def test_an_unreadable_topic_fails_the_piece_loudly_but_a_missing_one_waits(fake, tg, configured, alerts_seen):
    bad = make_piece(fake, c.TOPIC_PENDING, with_topic={"version": 1, "candidates": "nope"})
    waiting = make_piece(fake, c.TOPIC_PENDING, key="2026-W44:deep_dive", with_topic=False, slug="w-2026-10-30",
                         title="W")
    run(review.review_cycle(NOW))
    assert piece_row(fake, bad["id"])["status"] == c.FAILED
    assert alerts_seen == [(bad["id"], c.FAILED, "topic_unreadable", "topic")]
    assert piece_row(fake, waiting["id"])["status"] == c.TOPIC_PENDING


def test_a_lost_card_stamp_re_sends_and_the_old_card_goes_stale(fake, tg, configured):
    p = make_piece(fake, c.PLANNED, with_topic=topic(chosen=None))
    fake.fail_next(ledger.PIECES, "update", RuntimeError("PostgREST 503"))
    out = run(review.review_cycle(NOW))
    assert out["topic_cards"] == 1 and out["failed"] == 1
    first_card_id = tg._next_id                      # the card was the sweep's only Telegram call
    review._bundle_retry_at.clear()                                          # skip the back-off
    run(review.review_cycle(NOW))
    ids = piece_row(fake, p["id"])["topic"]["card_message_ids"]
    assert len(tg.of("sendMessage")) == 2 and len(ids) == 1 and ids != [first_card_id]
    assert tap(tg, p["id"], "t1", message_id=first_card_id) is False          # the first card is stale
    assert tap(tg, p["id"], "t1", message_id=ids[0]) is True


# ── the sweep: review bundles ─────────────────────────────────────────────────────────────────


def test_a_bundle_is_header_audio_body_then_one_loud_decision(fake, tg, configured):
    p = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    out = run(review.review_cycle(NOW))
    assert out["bundles"] == 1 and out["stamped"] == 1, out
    methods = tg.methods()
    assert methods[0] == "sendMessage" and methods[1] == "sendAudio" and methods[-1] == "sendMessage"
    header = tg.of("sendMessage")[0]
    assert header["disable_notification"] is True
    for needle in ("Deep Dive 2026-W43", "blueprints", "round 1", TITLE, "Publishes", "the hold ends",
                   "Topic: Topic 1 label", "https://www.sec.gov/", "Shadow judge (shadow): 1 flag(s)",
                   "judge_directive", "narration 11:32", "worst block: #3 coverage 0.91", "Writer tokens: 48,210"):
        assert needle in header["text"], needle
    audio = tg.of("sendAudio")[0]
    assert audio["disable_notification"] is True and audio["duration"] == 692
    assert audio["audio"].startswith("https://x.supabase.co/storage/v1/object/sign/money-moves-media/audio/weekly/")
    assert fake.storage.signed[0][0] == "money-moves-media"
    body = tg.of("sendMessage")[1:-1]
    assert body and all(m["disable_notification"] is True for m in body)
    assert TITLE in body[0]["text"]
    decision = tg.of("sendMessage")[-1]
    assert "disable_notification" not in decision                           # the one loud message
    assert decision["reply_markup"] == review.decision_keyboard(p["id"])
    row = piece_row(fake, p["id"])
    assert row["status"] == c.IN_REVIEW and row["review_message_ids"] == [tg._next_id]


def test_dry_run_is_labelled_on_the_header_and_the_decision(fake, tg, configured):
    make_piece(fake, c.READY_FOR_REVIEW, dry_run=True, review_ids=[])
    run(review.review_cycle(NOW))
    assert "DRY RUN" in tg.of("sendMessage")[0]["text"] and "DRY RUN" in tg.of("sendMessage")[-1]["text"]


def test_a_long_article_is_chunked_within_telegrams_limit(fake, tg, configured):
    big = draft(paragraphs=14, words=70)
    big["sections"] = big["sections"] * 4
    make_piece(fake, c.READY_FOR_REVIEW, the_draft=big, review_ids=[])
    run(review.review_cycle(NOW))
    body = tg.of("sendMessage")[1:-1]
    assert len(body) >= 3
    assert all(rs.utf16_len(m["text"]) <= 4096 for m in tg.of("sendMessage"))
    sent_words = sum(m["text"].count("capacity") for m in body)
    assert sent_words == review.article_words(big) - sum(
        len(i.split()) for s in big["sections"] for b in s["content"] for i in b.get("items", [])) - sum(
        len(b["text"].split()) for s in big["sections"] for b in s["content"] if b.get("type") == "callout")


def test_an_audio_telegram_cannot_attach_becomes_a_link_and_the_url_is_never_logged(fake, tg, configured, caplog):
    caplog.set_level(logging.DEBUG)
    make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    tg.script["sendAudio"] = [(400, {"ok": False, "error_code": 400,
                                     "description": "Bad Request: failed to get HTTP URL content"})]
    run(review.review_cycle(NOW))
    link = [m for m in tg.of("sendMessage") if "Narration (Telegram could not attach it)" in m["text"]]
    assert len(link) == 1 and "SIGNEDTOKEN123" in link[0]["text"] and link[0]["disable_notification"] is True
    assert "SIGNEDTOKEN123" not in caplog.text


def test_an_oversized_audio_is_never_requested_and_becomes_a_link(fake, tg, configured):
    p = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    fake.row(ledger.JOBS, p["narration_job_id"])["audio_bytes"] = 25 * 1024 * 1024
    run(review.review_cycle(NOW))
    assert tg.of("sendAudio") == []
    assert any("could not attach" in m["text"] for m in tg.of("sendMessage"))
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


def test_an_unsignable_audio_is_said_and_the_bundle_still_goes(fake, tg, configured):
    p = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    fake.storage.fail_sign = RuntimeError("storage down")
    run(review.review_cycle(NOW))
    assert tg.of("sendAudio") == []
    assert any("could not be signed" in m["text"] for m in tg.of("sendMessage"))
    assert piece_row(fake, p["id"])["status"] == c.IN_REVIEW


def test_a_lost_bundle_stamp_re_sends_it_and_the_first_decision_goes_stale(fake, tg, configured):
    p = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    fake.fail_next(ledger.PIECES, "update", RuntimeError("crash between send and stamp"))
    out = run(review.review_cycle(NOW))
    assert out["bundles"] == 1 and out["failed"] == 1 and out["stamped"] == 0
    first_decision = tg.of("sendMessage")[-1]
    first_decision_id = tg._next_id                  # the decision was the sweep's last Telegram call
    assert piece_row(fake, p["id"])["status"] == c.READY_FOR_REVIEW
    run(review.review_cycle(NOW))
    assert len([m for m in tg.of("sendMessage") if "reply_markup" in m]) == 1     # backed off: not re-sent yet
    review._bundle_retry_at.clear()
    run(review.review_cycle(NOW))
    row = piece_row(fake, p["id"])
    assert row["status"] == c.IN_REVIEW
    decisions = [m for m in tg.of("sendMessage") if "reply_markup" in m]
    assert len(decisions) == 2
    assert row["review_message_ids"] != [first_decision_id]
    assert tap(tg, p["id"], "a", message_id=first_decision_id) is False      # the first bundle's button
    assert tap(tg, p["id"], "a", message_id=row["review_message_ids"][0]) is True
    assert first_decision["reply_markup"] == decisions[1]["reply_markup"]


def test_a_bundle_whose_draft_changed_while_sending_is_not_stamped(fake, tg, configured):
    p = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])

    def remerge():
        piece_row(fake, p["id"])["draft_sha256"] = SHA2
    fake.before_update[ledger.PIECES] = [remerge]
    out = run(review.review_cycle(NOW))
    assert out["bundles"] == 1 and out["stamped"] == 0
    assert piece_row(fake, p["id"])["status"] == c.READY_FOR_REVIEW


def test_no_bundle_after_the_hold(fake, tg, configured):
    make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    assert run(review.review_cycle(HOLD))["bundles"] == 0 and tg.calls == []


@pytest.mark.parametrize("break_it,reason", [
    (lambda fake, p: piece_row(fake, p["id"]).update(draft=None), "draft_missing"),
    (lambda fake, p: fake.row(ledger.JOBS, p["narration_job_id"]).update(status="failed"), "narration_missing"),
])
def test_a_bundle_that_can_never_be_shown_fails_the_piece_loudly(fake, tg, configured, alerts_seen, break_it, reason):
    p = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    break_it(fake, p)
    run(review.review_cycle(NOW))
    assert piece_row(fake, p["id"])["status"] == c.FAILED
    assert alerts_seen == [(p["id"], c.FAILED, reason, "review")]
    assert tg.of("sendMessage") == []


# ── the shared flood gate and pacer ───────────────────────────────────────────────────────────


def test_a_closed_chat_stops_the_sweep_before_any_read(fake, tg, configured, monkeypatch):
    make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    telegram_pacing.note_rate_limit(30)
    calls = len(fake.calls)
    assert run(review.review_cycle(NOW))["rate_limited"] == 1
    assert tg.calls == [] and len(fake.calls) == calls


def test_a_429_mid_bundle_stops_the_sweep_and_closes_the_chat_for_everyone(fake, tg, configured):
    a = make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    b = make_piece(fake, c.READY_FOR_REVIEW, key="2026-W44:deep_dive", slug="b-2026-10-30", title="B", review_ids=[])
    tg.script["sendAudio"] = [too_many(42)]
    out = run(review.review_cycle(NOW))
    assert out["rate_limited"] == 1 and out["bundles"] == 0
    assert telegram_pacing.is_rate_limited() and telegram_pacing.seconds_left() > 30
    assert tg.methods() == ["sendMessage", "sendAudio"]                        # b was never started
    assert {piece_row(fake, x["id"])["status"] for x in (a, b)} == {c.READY_FOR_REVIEW}


def test_every_send_waits_its_turn_on_the_shared_pacer(fake, tg, configured, monkeypatch):
    order: List[str] = []

    class Recorder:
        async def wait(self):
            order.append("wait")
    monkeypatch.setattr(telegram_pacing, "PACER", Recorder())
    original = tg.handler

    def handler(request):
        order.append(request.url.path.rsplit("/", 1)[-1])
        return original(request)
    tg.handler = handler
    import httpx
    from app.integrations import telegram
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    assert run(review.review_cycle(NOW))["stamped"] == 1
    sends = [o for o in order if o != "wait"]
    assert len(sends) >= 4                                         # header, audio, body, decision
    for k, item in enumerate(order):
        if item != "wait":
            assert k > 0 and order[k - 1] == "wait", order          # every send waited its turn


def test_not_configured_sends_nothing(fake, tg, monkeypatch):
    monkeypatch.setattr(rs, "is_configured", lambda: False)
    make_piece(fake, c.READY_FOR_REVIEW, review_ids=[])
    assert run(review.review_cycle(NOW)).get("not_configured") == 1
    assert tg.calls == []


# ── pure text helpers ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("checks,expected", [
    (None, "no checks recorded"), ({}, "no per-block checks"), ({"blocks": "x"}, "no per-block checks"),
    ({"blocks": [{"i": 1, "coverage": float("nan")}, {"i": 2}]}, "no per-block scores"),
    ({"blocks": [{"i": 1, "wps": 3.9}, {"i": 2, "wps": 2.5}]}, "#1 3.90 words/s"),
    ({"blocks": [{"i": 4, "confidence": 0.5}, {"i": 5, "confidence": True}]}, "#4 confidence 0.50"),
    ({"worst_block": "#12 rate 4.1"}, "#12 rate 4.1"),
])
def test_worst_block_reads_any_shape(checks, expected):
    assert review.worst_block(checks) == expected


_SERVER = {"blocks": 9, "sentences": 30, "min_spw": 0.21, "min_spw_block": 4, "max_spw": 0.62,
           "max_spw_block": 6, "bytes_per_second": 12_000.0, "pipeline_version": "p"}


@pytest.mark.parametrize("checks,expected", [
    # the STORED shape (narration.complete): {"worker": <the worker's checks>, "server": timing_stats + …}
    ({"worker": {"blocks": [{"i": 0, "align_coverage": 1.0, "confidence": 0.8, "words_per_sec": 2.7},
                            {"i": 3, "align_coverage": 0.75, "confidence": 0.9, "words_per_sec": 2.6}]},
      "server": _SERVER}, "#3 coverage 0.75"),
    ({"worker": {"blocks": [{"i": 0, "align_coverage": 1.0, "confidence": 0.8},
                            {"i": 5, "align_coverage": 1.0, "confidence": 0.41}]}, "server": _SERVER},
     "#5 confidence 0.41"),
    # Kokoro: exact word spans (coverage 1.0, no aligner confidence) → the pace farthest from the band middle
    ({"worker": {"blocks": [{"i": 0, "align_coverage": 1.0, "words_per_sec": 2.9},
                            {"i": 2, "align_coverage": 1.0, "words_per_sec": 1.9}]}, "server": _SERVER},
     "#2 1.90 words/s"),
    # bounded_checks DROPPED the per-block list to fit the size cap → the server's sentence-pace extremes
    ({"worker": {"blocks": [], "blocks_dropped": 9, "summary": {"min_align_coverage": 0.7}}, "server": _SERVER},
     "#4 fastest 0.21 s/word · #6 slowest 0.62 s/word"),
    ({"worker": {}, "server": _SERVER}, "#4 fastest 0.21 s/word · #6 slowest 0.62 s/word"),  # worker sent none
    ({"worker": {}, "server": {"blocks": 2, "sentences": 0}}, "no per-block checks"),
    ({"worker": {}, "server": {**_SERVER, "max_spw": float("inf")}}, "#4 fastest 0.21 s/word"),
    # the worker's raw payload carries its own nested "worker" (versions) beside "blocks" — still read
    ({"blocks": [{"i": 1, "align_coverage": 0.5}], "worker": {"worker_version": "learn-narrator/1"}},
     "#1 coverage 0.50"),
    ({"worker": {"blocks": [{"i": 0, "align_coverage": 1.0}, {"i": 1, "align_coverage": 1.0}]}}, "#0 coverage 1.00"),
    ({"worker": {"blocks": [{"i": 0, "align_coverage": True, "confidence": float("nan")}]}}, "no per-block scores"),
])
def test_worst_block_reads_what_narration_complete_stores(checks, expected):
    assert review.worst_block(checks) == expected


def test_a_json_integer_too_big_for_a_float_never_breaks_the_header():
    """`json.loads` keeps 10**400 as an int; `float()` / `math.isfinite()` on it raise OverflowError. A raise
    in the header fails the bundle every retry and the piece expires unseen — it must read as no score."""
    huge = 10 ** 400
    checks = {"worker": {"blocks": [{"i": 0, "align_coverage": huge, "confidence": huge, "words_per_sec": huge},
                                    {"i": 1, "align_coverage": 0.9}]},
              "server": {**_SERVER, "min_spw": huge}}
    assert review.worst_block(checks) == "#1 coverage 0.90"
    assert review.worst_block({"worker": {"blocks": [{"i": 0, "wps": huge}]}, "server": {**_SERVER, "min_spw": huge}}) \
        == "#6 slowest 0.62 s/word"
    piece = {"kind": "deep_dive", "period_key": KEY, "review_round": 1, "hold_until": None,
             "topic": {"version": 1, "chosen_index": 0, "candidates": [{"label": "T", "score": huge}]}}
    text = review.review_header_text(piece, {"duration_seconds": huge, "checks": checks}, {"sections": []})
    assert "narration ?" in text and "worst block: #1 coverage 0.90" in text
    assert "score" not in review.topic_card_text(piece, piece["topic"])


def _worker_functions() -> Dict[str, Any]:
    """`summary` and `bounded_checks` from the REAL narrator worker, AST-extracted (never imported: its
    import configures the root logger), plus the per-block row keys its `build_timings` / `block_spans`
    write (dict-literal keys from the AST — comments cannot satisfy it)."""
    import __future__
    import ast
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "learn_narrator" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wanted = {"summary", "bounded_checks"}
    body, keys = [], {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MAX_CHECKS_CHARS"
                                                  for t in node.targets):
            body.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in ("build_timings", "block_spans"):
            keys[node.name] = {k.value for d in ast.walk(node) if isinstance(d, ast.Dict)
                               for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == wanted and set(keys) == {
        "build_timings", "block_spans"}, "learn_narrator/main.py changed shape — re-read it"
    ns: Dict[str, Any] = {"json": json}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec",
                 flags=__future__.annotations.compiler_flag, dont_inherit=True), ns)
    ns["row_keys"] = keys
    return ns


def test_the_keys_worst_block_reads_are_the_ones_the_worker_writes():
    keys = _worker_functions()["row_keys"]
    assert {"i", "align_coverage", "words_per_sec"} <= keys["build_timings"]
    assert "confidence" in keys["block_spans"]


@pytest.mark.parametrize("scenario", ["unaligned_sentence", "low_confidence", "blocks_dropped"])
def test_worst_block_parity_with_the_checks_narration_complete_actually_stores(monkeypatch, scenario):
    """F7 parity: the worker's checks (built by its REAL `summary` / `bounded_checks`, rows with the keys
    its `build_timings` writes) go through the REAL `narration.complete`, and `worst_block` reads the
    job row it stored. Before the fix this always said "no per-block checks"."""
    import importlib

    from app.config import settings as app_settings
    from app.services.learn_weekly import narration

    nt = importlib.import_module("test_learn_weekly_narration")
    client = nt.FakeClient()
    monkeypatch.setattr(ledger, "get_supabase", lambda: client)
    monkeypatch.setattr(narration, "get_supabase", lambda: client)
    monkeypatch.setattr(app_settings, "SUPABASE_URL", nt.SUPABASE_URL)
    monkeypatch.setattr(app_settings, "LEARN_NARRATOR_ENGINE", "chatterbox")
    worker = _worker_functions()
    blocks = nt._blocks()
    assert len(blocks) >= 3
    rows = [{"i": b.i, "raw_seconds": 9.5, "words_per_sec": 2.75, "align_coverage": 1.0, "rerendered": False,
             "confidence": 0.93, "word_coverage": 1.0, "start_error": 0.02} for b in blocks]
    target = blocks[2].i
    if scenario == "unaligned_sentence":
        rows[2]["align_coverage"] = 0.8
    else:
        rows[2]["confidence"] = 0.41
    if scenario == "blocks_dropped":
        worker["MAX_CHECKS_CHARS"] = 50                 # the worker's own size cap drops the per-block list
    payload = worker["bounded_checks"]({
        "blocks": rows, "file": {"lufs": -16.0}, "summary": worker["summary"](rows),
        "worker": {"worker_version": "learn-narrator/1", "voice": "caydex_v2"}})
    assert (payload["blocks"] == []) is (scenario == "blocks_dropped")

    async def flow():
        piece, job = nt._media_pending(client)
        wire, claim, kw = await nt._rendered(client)
        await narration.complete(wire["id"], claim, now=nt.NOW, **{**kw, "checks": payload})
        return job

    job = run(flow())
    stored = client.row(ledger.JOBS, job["id"])["checks"]
    assert set(stored) == {"worker", "server"}
    got = review.worst_block(stored)
    if scenario == "unaligned_sentence":
        assert got == f"#{target} coverage 0.80"
    elif scenario == "low_confidence":
        assert got == f"#{target} confidence 0.41"
    else:
        server = stored["server"]
        assert got.startswith(f"#{server['min_spw_block']} fastest {server['min_spw']:.2f} s/word")
        assert f"#{server['max_spw_block']} slowest" in got


# ── the judge warning (safety F2) and the statistic arrows (safety F6) ────────────────────────


def test_the_warned_judge_rules_are_real_judge_rule_codes():
    from app.services.learn_weekly import judge

    assert set(review.WARN_JUDGE_RULES) <= set(judge.RULE_CODES)
    assert set(review.WARN_JUDGE_RULES) == set(review._WARN_JUDGE_WORDS)


def _judge_record(*rules: str, mode: str = "shadow") -> Dict[str, Any]:
    """`validation.judge` exactly as the writer stores it: `JudgeRecord.as_dict()`."""
    from app.services.learn_weekly import judge
    from app.services.marketing import judge as mj

    # A finished judge: the rubric's call and the support check both ran (support=None means the
    # support check did not finish, which warns on its own).
    return judge.JudgeRecord(mode=mode, verdicts=[mj.Verdict(f"sections[{i}].content[0]", r, "the quote", "why", True)
                                                  for i, r in enumerate(rules)],
                             support={"calls": 1, "sentences": 10, "flagged": 0}).as_dict()


@pytest.mark.parametrize("rules,warned", [
    (("learn_directive",), ["learn_directive"]),
    (("learn_superlative", "learn_valuation_verdict"), ["learn_valuation_verdict"]),
    (("learn_valuation_verdict", "learn_directive", "learn_directive"), ["learn_directive", "learn_valuation_verdict"]),
    (("learn_superlative", "learn_forecast"), []),
    ((), []),
])
def test_a_directive_or_valuation_flag_opens_the_decision_message_with_a_warning(fake, tg, configured, rules, warned):
    make_piece(fake, c.READY_FOR_REVIEW, review_ids=[], validation={"judge": _judge_record(*rules)})
    assert run(review.review_cycle(NOW))["stamped"] == 1
    decision = tg.of("sendMessage")[-1]
    assert "disable_notification" not in decision
    first = decision["text"].split("\n", 1)[0]
    if warned:
        assert first.startswith("⚠️ The shadow judge flagged") and all(r in first for r in warned)
        assert ("buy/sell directive" in first) is ("learn_directive" in warned)
        assert ("valuation verdict" in first) is ("learn_valuation_verdict" in warned)
    else:
        assert first.startswith("Decide:") and "⚠️" not in decision["text"]
    header = tg.of("sendMessage")[0]["text"]
    for r in rules:
        assert r in header                     # every verdict is listed in the header, with where it is
    if rules:
        assert "(sections[0].content[0])" in header


@pytest.mark.parametrize("validation,expected", [
    ({"judge": {"flags": [{"code": "learn_directive"}]}}, ["learn_directive"]),
    ({"judge": ["learn_valuation_verdict: cheap", 3, None]}, ["learn_valuation_verdict"]),
    ({"judge": {"verdicts": "nope"}}, []), ({"judge": None}, []), (None, []), ({"judge": {"verdicts": [{}]}}, []),
])
def test_judge_flagged_rules_reads_any_shape(validation, expected):
    assert review.judge_flagged_rules(validation) == expected


def test_the_body_text_prints_each_statistics_trend_arrow():
    d = draft()
    d["statistics"] = [
        {"value": "$1.2 billion", "label": "revenue", "trend": "up"},
        {"value": "14%", "label": "margin", "trend": "down", "trendValue": "-3 pts"},
        {"value": "3", "label": "plants", "trend": "neutral"},
        {"value": "$9 million", "label": "capex"},
        {"value": "7", "label": "sites", "trend": "sideways"},
        {"value": "2", "label": "lines", "trend": None, "trendValue": "+1"},
    ]
    lines = review.article_body_text(d).split("\n")
    assert "• $1.2 billion — revenue (↑ up)" in lines
    assert "• 14% — margin (↓ down, -3 pts)" in lines
    assert "• 3 — plants (→ neutral)" in lines
    assert "• $9 million — capex" in lines
    assert "• 7 — sites (trend 'sideways' — the app draws no arrow)" in lines
    assert "• 2 — lines (+1)" in lines


@pytest.mark.parametrize("validation,first", [
    (None, "Shadow judge: not run"), ({}, "Shadow judge: not run"),
    ({"judge": {"mode": "shadow", "flags": []}}, "Shadow judge (shadow): no flags"),
    ({"judge": {"status": "unavailable"}}, "Shadow judge: unavailable"),
    ({"judge": ["judge_directive: buy"] * 7}, "Shadow judge: 7 flag(s)"),
    ({"judge": 3}, "Shadow judge: unreadable verdict"),
])
def test_judge_lines_read_any_shape(validation, first):
    lines = review.judge_lines(validation)
    assert lines[0] == first and len(lines) <= 7


def test_support_flags_get_a_count_line_and_come_after_the_rubrics():
    sup = [{"label": f"sections[1].content[{i}]", "rule": review.SUPPORT_RULE, "quote": f"claim {i}"}
           for i in range(9)]
    rubric = {"label": "sections[2].content[0]", "rule": "learn_forecast", "quote": "will dominate"}
    judge = {"mode": "shadow", "verdicts": sup[:3] + [rubric] + sup[3:],
             "support": {"calls": 3, "sentences": 131, "flagged": 9}}
    lines = review.judge_lines({"judge": judge})
    assert lines[0] == "Shadow judge (shadow): 10 flag(s)"
    assert lines[1] == "Support check: 9 of 131 sentence(s) say things the fact sheet does not"
    assert lines[2].startswith("• learn_forecast") and lines[-1] == "• … 6 more"
    assert len(lines) <= 7
    # A hostile or missing sentence count drops only the "of N".
    for bad in (None, True, "131", -1, {"sentences": 0}):
        judge["support"] = bad if not isinstance(bad, dict) else bad
        assert review.judge_lines({"judge": judge})[1] == \
            "Support check: 9 sentence(s) say things the fact sheet does not"


def test_a_failed_support_check_never_reads_as_a_clean_article():
    """Review 2026-10-24: a support check that failed in shadow left `verdicts: []`, and the header
    said "no flags" — the same words as a clean article, while the owner's tap is the only gate."""
    from app.services.learn_weekly import judge

    rec = judge.JudgeRecord(mode="shadow", error="support: LearnJudgeUnavailable: support answer cut off")
    lines = review.judge_lines({"judge": rec.as_dict()})
    assert lines[0] == "Shadow judge (shadow): no flags"
    assert lines[1].startswith("⚠️ Judge problem: support: LearnJudgeUnavailable")
    assert lines[2] == "⚠️ Support check: did not finish — read the facts yourself"
    both = judge.JudgeRecord(mode="shadow", error="RuntimeError: judge 503; support: RuntimeError: 503")
    assert any(line.startswith("⚠️ Judge problem: RuntimeError") for line in review.judge_lines(
        {"judge": both.as_dict()}))


@pytest.mark.parametrize("record", [
    {"mode": "shadow", "verdicts": [], "error": "LearnJudgeUnavailable: judge answer cut off", "support": None},
    {"mode": "shadow", "verdicts": [], "error": None, "support": None},
    {"mode": "shadow", "verdicts": [], "error": "RuntimeError: judge 503",
     "support": {"calls": 3, "sentences": 120, "flagged": 0}},
])
def test_the_loud_decision_message_warns_when_a_judge_stage_did_not_finish(record):
    """Re-review 2026-10-24: the "did not finish" lines sat only in the SILENT header; the loud
    message that carries the Approve button said nothing."""
    line = review.judge_warning({"judge": record})
    assert line is not None and "did not finish" in line and line.startswith("⚠️")


@pytest.mark.parametrize("record", [
    {"mode": "shadow", "verdicts": [], "error": None, "support": {"calls": 3, "sentences": 9, "flagged": 0}},
    {"mode": "shadow", "verdicts": []},                       # a record from before the support check
    None, "junk",
])
def test_a_finished_judge_adds_no_warning(record):
    assert review.judge_warning({"judge": record}) is None


def test_a_flag_warning_wins_over_the_unfinished_one():
    rec = {"mode": "shadow", "error": "support: boom", "support": None,
           "verdicts": [{"label": "title", "rule": "learn_directive", "quote": "buy"}]}
    assert "flagged a buy/sell directive" in review.judge_warning({"judge": rec})


def test_a_clean_support_run_says_so_with_its_count():
    from app.services.learn_weekly import judge

    rec = judge.JudgeRecord(mode="shadow", support={"calls": 3, "sentences": 131, "flagged": 0})
    assert review.judge_lines({"judge": rec.as_dict()}) == [
        "Shadow judge (shadow): no flags",
        "Support check: 0 of 131 sentence(s) say things the fact sheet does not"]


@pytest.mark.parametrize("n_rubric,n_support,error,support", [
    (0, 0, None, None), (9, 0, None, {"sentences": 5}), (0, 30, "x" * 500, {"sentences": 131}),
    (9, 30, "support: boom", None), (1, 1, "  ", {"sentences": "?"}),
])
def test_the_judge_block_never_exceeds_seven_lines(n_rubric, n_support, error, support):
    verdicts = ([{"label": "title", "rule": "learn_forecast", "quote": "q"}] * n_rubric
                + [{"label": "title", "rule": review.SUPPORT_RULE, "quote": "q"}] * n_support)
    lines = review.judge_lines({"judge": {"mode": "shadow", "verdicts": verdicts, "error": error,
                                          "support": support}})
    assert 1 <= len(lines) <= 7, lines
    total = n_rubric + n_support
    if total > len([ln for ln in lines if ln.startswith("• ") and "more" not in ln]):
        assert lines[-1].startswith("• … ")


def test_the_reviews_support_code_is_the_judges():
    from app.services.learn_weekly import judge

    assert review.SUPPORT_RULE == judge.SUPPORT_RULE
    assert review.SUPPORT_RULE not in review.WARN_JUDGE_RULES


def test_header_text_survives_hostile_values():
    piece = {"kind": "deep_dive", "period_key": KEY, "review_round": "x", "tokens_used": True,
             "title": "Line one\nINJECTED line", "topic": {"version": 1, "candidates": [{"label": None}],
                                                                 "chosen_index": 0},
             "scheduled_publish_at": "nope", "hold_until": None, "validation": {"judge": None}}
    text = review.review_header_text(piece, None, {"sections": []})
    assert "INJECTED" in text and "Line one INJECTED line" in text          # folded onto one line
    assert rs.utf16_len(text) <= 4096


def test_the_body_text_reads_every_block_type():
    text = review.article_body_text(draft())
    assert text.startswith(TITLE) and "KEY POINTS" in text and "BY THE NUMBERS" in text
    assert "• one point" in text and "[info] Educational only." in text and "== Part 0 ==" in text


def test_retry_narration_with_the_real_narration_module(fake, tg, configured):
    """The call shape this module uses (`enqueue_for_piece(piece, draft, engine=, round=)`) against the
    REAL narration builder: round 2 is queued with the frozen engine and the pointer moves with it."""
    p = make_piece(fake, c.IN_REVIEW)
    assert tap(tg, p["id"], "m") is True
    row = piece_row(fake, p["id"])
    job = fake.row(ledger.JOBS, row["narration_job_id"])
    assert row["status"] == c.MEDIA_PENDING and job["round"] == 2 and job["status"] == c.JOB_QUEUED
    assert job["engine"] == "chatterbox"


@pytest.mark.parametrize("owner", [True, False])
def test_a_pick_writes_exactly_what_the_topic_builders_contract_helper_says(fake, tg, configured, owner):
    """Contract 1 parity: the fields a pick writes equal `topic.apply_pick` (the topic builder's statement of
    the same contract) — owner tap and 07:00 auto-pick alike."""
    from app.services.learn_weekly import topic as topic_step

    before = topic(chosen=None, cards=[CARD_ID])
    assert topic_step.topic_problem(before) is None
    p = make_piece(fake, c.PLANNED, with_topic=before)
    if owner:
        assert tap(tg, p["id"], "t2", message_id=CARD_ID) is True
        expected = topic_step.apply_pick(before, 1, auto_picked=False)
    else:
        assert run(review.auto_pick(schedule.times_for_period_key(KEY).auto_pick_at))["picked"] == 1
        expected = topic_step.apply_pick(before, 0, auto_picked=True)
    row = piece_row(fake, p["id"])
    assert row["topic"] == expected["topic"]
    assert row["category"] == expected["category"] and row["subjects"] == expected["subjects"]
