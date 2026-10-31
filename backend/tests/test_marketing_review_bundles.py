"""
Review BUNDLES (drop 1, 2026-10-09; contract C10): two Telegram decisions a posting day instead of one
per post — `review_service` (sweep + webhook), `run_service.review_bundle` / `bundle_posts` and
`telegram.send_photo`.

Hermetic: Telegram is the `FakeTelegram` MockTransport from `test_marketing_review_bot.py` (wrapped to
remember each answer's message id), the ledger is the in-memory PostgREST fake from
`test_marketing_run_service.py` — with its `json->>key` text path widened to the nested
`metadata->review_bundle->>id` path the bundle fence filters on (as PostgREST does).

What must never regress:
  * nothing is offered before every post of the run exists — the run is `media_ready` OR at the
    `assets_ready` checkpoint (written only after create_posts answered; 2026-10-15: a run that closed
    `failed` there is offered, one still at `rendered` is held); a run's posts become at most TWO bundles
    (video; image/text), each: media → one message per DISTINCT caption → ONE decision message;
  * only the decision messages ring (2026-10-15): the media, their link fallbacks and the caption messages
    carry `disable_notification`; the decision message never does;
  * the decision message names each member that posts text instead of the day's picture, in the owner
    words of IMAGE_FALLBACK_REASONS (an unknown value → a generic phrase, never the raw value), and the
    members that get a first comment (exactly what the outlet sends: the code-owned text on a
    FIRST_COMMENT_PLATFORMS platform — `outlet_upload_post.first_comment_of`), worded as a REQUEST ("First
    comment requested on: …", review CTA-2: no outcome comes back); a bug in those notes costs only the
    notes (one ⚠️ line), never the decision;
  * every member carries its bundle stamp BEFORE any button exists; a stamp that does not land sends
    nothing; a member is stamped notified only while it still carries that bundle;
  * a decision is ONE conditional UPDATE fenced on status AND the bundle id — a member decided
    elsewhere, re-sent in a newer bundle, or whose text changed, is never flipped; a replay decides
    nothing; previews (unwired platforms) are never members;
  * ❌ Reject all offers the reasons once and a reason lands on every rejected member; ✂ drop rejects one
    member and keeps the rest open on the same message;
  * MARKETING_REVIEW_BUNDLES off → the per-post flow (the whole of test_marketing_review_bot.py runs
    with it off and is unchanged);
  * "DRY RUN" is read from the POSTS (every member a rehearsal row — the publisher's own rule), never the
    run row, whose flag every re-claim rewrites (2026-10-16); when the run's flag disagrees with the posts,
    or the members disagree among themselves, a ⚠️ line under the decision's header says so.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest

import test_marketing_run_service as fake_db
from app.config import settings
from app.integrations import telegram
from app.schemas.marketing import (
    FIRST_COMMENT_PLATFORMS,
    IMAGE_FALLBACK_REASONS,
    POST_METADATA_FIRST_COMMENT,
    POST_METADATA_IMAGE_FALLBACK,
    POST_PLATFORMS,
)
from app.services.marketing import outlet_upload_post
from app.services.marketing import publisher_service as pub
from app.services.marketing import review_service as rs
from app.services.marketing import run_service as mrs
from app.services.marketing.post_copy import SEARCH_LINE
from test_marketing_review_bot import (  # noqa: F401  (fixtures are used by name)
    OWNER,
    RUN_A,
    VIDEO,
    FakeTelegram,
    _post,
    _post_hook,
    _rows,
    _seed_post,
    _seed_run,
    client,
    configured,
    ledger,
    wakes,
)

IMAGE = "44444444-4444-4444-8444-444444444444"
VIDEO_PLATFORMS = ("tiktok", "youtube", "instagram")
POST_PLATFORMS_TEXT = ("x", "bluesky", "threads", "facebook", "linkedin")
SHORT = "Short caption for X, Bluesky and Threads."
LONG = "A longer caption for Facebook and LinkedIn.\n\nWith a second paragraph."


# ── fakes / fixtures ──────────────────────────────────────────────────────────


class IdTelegram(FakeTelegram):
    """FakeTelegram that also remembers the message id of each answer (None for a non-message)."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: List[Optional[int]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        resp = super().handler(request)
        try:
            body = json.loads(resp.content or b"{}")
            result = body.get("result") if isinstance(body, dict) else None
            self.ids.append(result.get("message_id") if isinstance(result, dict) else None)
        except ValueError:
            self.ids.append(None)
        return resp


@pytest.fixture(autouse=True)
def _fresh_post_clock(monkeypatch):
    """`_seed_post` stamps created_at from a SESSION-wide counter whose minutes wrap every 3,600 posts;
    a bundle's member order follows created_at, so each test here restarts it (monotonic within a test)."""
    import test_marketing_review_bot as review_bot_tests
    monkeypatch.setattr(review_bot_tests, "_SEQ", iter(range(10, 3600)))


@pytest.fixture
def tg(monkeypatch):
    fake = IdTelegram()
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def _col_nested(row: Dict[str, Any], col: str) -> Any:
    """`_col` with PostgREST's nested JSON path (`a->b->>c`), as the bundle fence filters on."""
    if "->>" not in col:
        return row.get(col)
    path, key = col.rsplit("->>", 1)
    parts = path.split("->")
    doc: Any = row.get(parts[0])
    for part in parts[1:]:
        doc = doc.get(part) if isinstance(doc, dict) else None
    value = doc.get(key) if isinstance(doc, dict) else None
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


@pytest.fixture
def bundles(monkeypatch, ledger):
    monkeypatch.setattr(fake_db, "_col", _col_nested)
    monkeypatch.setattr(settings, "MARKETING_REVIEW_BUNDLES", True)
    # Batch 3: the three new switches at their declared defaults, whatever this machine's environment says.
    monkeypatch.setattr(settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "")
    monkeypatch.setattr(settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    monkeypatch.setattr(settings, "MARKETING_INSTAGRAM_TRIAL_MODE", "off")
    return ledger


def _seed_image(svc, *, run_id=RUN_A, image_id=IMAGE, size=400_000, role="post_image", status="ready"):
    _rows(svc, mrs.ASSETS).append({
        "id": image_id, "run_id": run_id, "kind": "card", "status": status,
        "storage_path": f"2026-09-28/card-{'b' * 16}.jpg", "content_type": "image/jpeg", "bytes": size,
        "metadata": {"image_role": role, "onscreen_text": ["Title", "One.", "Two.", "Footer"]}})
    run = next(r for r in _rows(svc, mrs.RUNS) if r["id"] == run_id)
    run["metadata"] = {**run["metadata"], "image_asset_id": image_id}


def _seed_day(svc, *, image: bool = True, x_fmt: str = "text", run_status: str = "media_ready",
              dry_run: bool = False) -> Dict[str, str]:
    """A posting day as create_posts leaves it: 3 video posts (distinct captions) + 5 text-platform
    posts (two distinct captions), image posts when `image`."""
    _seed_run(svc, dry_run=dry_run)
    next(r for r in _rows(svc, mrs.RUNS) if r["id"] == RUN_A)["status"] = run_status
    if image:
        _seed_image(svc)
    ids: Dict[str, str] = {}
    for p in VIDEO_PLATFORMS:
        ids[p] = _seed_post(svc, RUN_A, p, "video", caption=f"{p} caption", title="YT title" if p == "youtube" else None,
                            asset_ids=[VIDEO])
    for p in POST_PLATFORMS_TEXT:
        fmt = x_fmt if p == "x" else ("image" if image else "text")
        ids[p] = _seed_post(svc, RUN_A, p, fmt, caption=SHORT if p in ("x", "bluesky", "threads") else LONG,
                            title=None, asset_ids=[IMAGE] if fmt == "image" else [])
    return ids


def _meta(svc, pid) -> Dict[str, Any]:
    return _post(svc, pid)["metadata"]


def _decision_messages(tg) -> List[Dict[str, Any]]:
    """Every decision message sent: its payload plus the message id Telegram answered with."""
    out = []
    for (method, payload), mid in zip(tg.calls, tg.ids):
        markup = payload.get("reply_markup") or {}
        datas = [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]
        if method == "sendMessage" and any(d.startswith("g:") for d in datas):
            out.append({**payload, "message_id": mid})
    return out


def _bundle_id(decision: Dict[str, Any]) -> str:
    data = decision["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert data.startswith("g:")
    return data[2:]


def _update(target: str, verb: str, *, message: Optional[Dict[str, Any]] = None, from_id: Any = OWNER,
            chat_id: Any = OWNER) -> Dict[str, Any]:
    msg = {"message_id": 777, "date": 1759000000, "chat": {"id": chat_id, "type": "private"},
           "text": "DECISION · POSTS · run 2026-09-28"}
    if message is not None:
        msg = {"date": 1759000000, "chat": {"id": chat_id, "type": "private"}, **message}
    return {"update_id": 1, "callback_query": {"id": "cbq-b", "from": {"id": from_id, "is_bot": False},
                                               "message": msg, "chat_instance": "ci",
                                               "data": f"{verb}:{target}"}}


def _tap_on(decision: Dict[str, Any], verb: str, target: Optional[str] = None) -> Dict[str, Any]:
    """A tap on a decision message as Telegram returns it (text and keyboard included)."""
    return _update(target or _bundle_id(decision), verb,
                   message={"message_id": decision["message_id"], "text": decision["text"],
                            "reply_markup": decision["reply_markup"]})


def _stamp_bundle(svc, pids: List[str], *, kind: str = "post", bundle_id: Optional[str] = None) -> str:
    """Members as the sweep leaves them: stamped with one bundle and notified."""
    bid = bundle_id or str(uuid.uuid4())
    for pid in pids:
        row = _post(svc, pid)
        row["metadata"] = {**row["metadata"],
                           "review_bundle": {"id": bid, "kind": kind, "members": list(pids),
                                             "caption_sha": mrs.review_caption_sha(row)},
                           "review_notified_at": "2026-09-28T20:00:00+00:00", "review_message_id": 777}
    return bid


def _answers(tg) -> List[str]:
    return [a.get("text") for a in tg.of("answerCallbackQuery")]


# ── telegram.send_photo ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_photo_is_plain_text_by_url(tg, configured):
    await telegram.send_photo(OWNER, "https://example.invalid/a.jpg", caption="IMAGE · X · run 2026-09-28")
    (method, payload), = tg.calls
    assert method == "sendPhoto"
    assert payload == {"chat_id": OWNER, "photo": "https://example.invalid/a.jpg",
                       "caption": "IMAGE · X · run 2026-09-28"}
    assert "parse_mode" not in payload
    await telegram.send_photo(OWNER, "https://example.invalid/b.jpg")
    assert "caption" not in tg.calls[-1][1]


@pytest.mark.asyncio
async def test_send_photo_maps_failures_like_every_method(tg, configured):
    tg.script["sendPhoto"] = [(400, {"ok": False, "description": "Bad Request: wrong file identifier"}),
                              (429, {"ok": False, "parameters": {"retry_after": 7}})]
    with pytest.raises(telegram.TelegramRequestError):
        await telegram.send_photo(OWNER, "https://example.invalid/a.jpg")
    with pytest.raises(telegram.TelegramRateLimitException) as exc:
        await telegram.send_photo(OWNER, "https://example.invalid/a.jpg")
    assert exc.value.retry_after == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_a_photo_transport_error_never_carries_the_token(tg, configured, error):
    from test_marketing_review_bot import _BOT_SECRET
    tg.script["sendPhoto"] = [lambda request, _p: error(f"boom {request.url}", request=request)]
    with pytest.raises(telegram.TelegramUnavailableException) as exc:
        await telegram.send_photo(OWNER, "https://example.invalid/a.jpg")
    assert _BOT_SECRET not in str(exc.value) and exc.value.__cause__ is None


@pytest.mark.asyncio
async def test_send_photo_without_a_token_makes_no_request(tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    with pytest.raises(telegram.TelegramNotConfiguredException):
        await telegram.send_photo(OWNER, "https://example.invalid/a.jpg")
    assert tg.calls == []


# ── verbs and keyboards ───────────────────────────────────────────────────────

_PID = "0f8fad5b-d9cb-469f-a165-70867728950e"
_BUNDLE_VERBS = [("g", "bundle_approve"), ("j", "bundle_reject"), ("s", "bundle_drop"),
                 ("h", "bundle_reason_tone"), ("i", "bundle_reason_accuracy"), ("m", "bundle_reason_compliance"),
                 ("u", "bundle_reason_weak"), ("v", "bundle_reason_other")]


@pytest.mark.parametrize("verb, decision", _BUNDLE_VERBS)
def test_every_bundle_verb_parses_and_round_trips_within_64_bytes(verb, decision):
    data = rs.callback_data(decision, _PID)
    assert data == f"{verb}:{_PID}" and len(data.encode()) <= 64
    assert rs.parse_callback_data(data) == (decision, _PID)
    assert rs.parse_callback_data(f"{verb.upper()}:{_PID}") is None
    assert rs.parse_callback_data(f"{verb}:{_PID.upper()}") is None
    assert rs.parse_callback_data(f"{verb}:{_PID}\n") is None


def test_the_verb_table_stays_single_letters_and_keeps_the_pinned_holes():
    assert all(len(v) == 1 and v.isascii() and v.islower() and v.isalpha() for v in rs._VERBS)
    assert len(set(rs._VERBS.values())) == len(rs._VERBS)
    for hole in ("x", "b", "e", "q"):
        assert hole not in rs._VERBS
    # the bundle reasons are exactly the per-post reasons, under their own verbs
    assert sorted(v[len("bundle_reason_"):] for v in rs._VERBS.values() if v.startswith("bundle_reason_")) \
        == sorted(rs.REJECT_REASONS)


def test_the_bundle_keyboard_offers_drops_only_for_two_or_more_members():
    a = {"id": str(uuid.uuid4()), "platform": "x"}
    b = {"id": str(uuid.uuid4()), "platform": "bluesky"}
    one = rs.bundle_keyboard(_PID, [a])
    assert one == {"inline_keyboard": [[{"text": "✅ Approve all (1)", "callback_data": f"g:{_PID}"}],
                                       [{"text": "❌ Reject all", "callback_data": f"j:{_PID}"}]]}
    two = rs.bundle_keyboard(_PID, [a, b])
    assert two["inline_keyboard"][0][0]["text"] == "✅ Approve all (2)"
    assert two["inline_keyboard"][2:] == [[{"text": "✂ drop X", "callback_data": f"s:{a['id']}"}],
                                          [{"text": "✂ drop BLUESKY", "callback_data": f"s:{b['id']}"}]]
    for row in two["inline_keyboard"]:
        for button in row:
            assert rs.parse_callback_data(button["callback_data"]) is not None
    with pytest.raises(ValueError):
        rs.bundle_keyboard("not-a-uuid", [a])
    with pytest.raises(ValueError):
        rs.bundle_keyboard(_PID, [a, {"id": "nope", "platform": "x"}])


def test_the_bundle_reason_keyboard_is_one_button_per_reason():
    kb = rs.bundle_reason_keyboard(_PID)
    buttons = [row[0] for row in kb["inline_keyboard"]]
    assert [b["text"] for b in buttons] == list(rs.REJECT_REASONS.values())
    assert [rs.parse_callback_data(b["callback_data"]) for b in buttons] == [
        (f"bundle_reason_{code}", _PID) for code in rs.REJECT_REASONS]


# ── pure helpers ──────────────────────────────────────────────────────────────


def _row(platform="x", fmt="text", **kw) -> Dict[str, Any]:
    return {"id": str(uuid.uuid4()), "platform": platform, "format": fmt, "title": None, "caption": SHORT,
            "metadata": {"dry_run": False}, "status": "pending_review", **kw}


def test_review_caption_sha_covers_exactly_what_is_shown():
    a = _row()
    assert mrs.review_caption_sha(a) == mrs.review_caption_sha(dict(a, id="other", platform="bluesky"))
    assert mrs.review_caption_sha(a) != mrs.review_caption_sha(dict(a, caption=SHORT + " "))
    assert mrs.review_caption_sha(a) != mrs.review_caption_sha(dict(a, title="A title"))
    assert mrs.review_caption_sha(dict(a, title=None)) == mrs.review_caption_sha(dict(a, title=123))
    # a title/caption split is not the same text moved around
    assert mrs.review_caption_sha(dict(a, title="ab", caption="c")) != mrs.review_caption_sha(dict(a, title="a",
                                                                                                caption="bc"))


@pytest.mark.parametrize("stamp", [
    None, "junk", [], {"id": _PID},
    {"id": _PID.upper(), "kind": "post", "members": [], "caption_sha": "x"},
    {"id": "not-a-uuid", "kind": "post", "members": [], "caption_sha": "x"},
    {"id": _PID, "kind": "carousel", "members": [], "caption_sha": "x"},
    {"id": _PID, "kind": "post", "members": "all", "caption_sha": "x"},
    {"id": _PID, "kind": "post", "members": [_PID.upper()], "caption_sha": "x"},
    {"id": _PID, "kind": "post", "members": [123], "caption_sha": "x"},
    {"id": _PID, "kind": "post", "members": [], "caption_sha": None},
])
def test_a_stamp_that_does_not_read_back_is_no_bundle(stamp):
    assert mrs.review_bundle_of({"metadata": {"review_bundle": stamp}}) is None


def test_a_well_formed_stamp_reads_back():
    stamp = {"id": _PID, "kind": "video", "members": [_PID], "caption_sha": "abc", "extra": 1}
    assert mrs.review_bundle_of({"metadata": {"review_bundle": stamp}}) == {
        "id": _PID, "kind": "video", "members": [_PID], "caption_sha": "abc"}
    assert mrs.review_bundle_of({"metadata": "junk"}) is None and mrs.review_bundle_of(None) is None


def test_the_media_caption_names_its_platforms_and_fits_1024(configured):
    text = rs.compose_bundle_media_caption("video", [_row("tiktok", "video")], [_row("youtube", "video")],
                                           "2026-09-28", True)
    assert text == ("VIDEO · TIKTOK, YOUTUBE (preview) · run 2026-09-28 · DRY RUN\n"
                    "The captions and your decision follow.")
    many = [_row("x" * 300, "image") for _ in range(10)]
    assert rs.utf16_len(rs.compose_bundle_media_caption("post", many, [], "2026-09-28", False)) <= 1024


def test_a_caption_message_lists_its_platforms_and_previews(configured):
    a, b = _row("x", "text"), _row("threads", "image")
    text = rs.compose_bundle_caption_text([a, b], {b["id"]}, "2026-09-28", False)
    assert text == (f"CAPTION · X (text), THREADS (image) · run 2026-09-28\n\n"
                    f"Preview only — THREADS not wired yet: never sent\n\n{SHORT}")
    titled = rs.compose_bundle_caption_text([_row("youtube", "video", title="  The title  ")], set(),
                                            "2026-09-28", True)
    assert titled == f"CAPTION · YOUTUBE (video) · run 2026-09-28 · DRY RUN\n\nThe title\n\n{SHORT}"
    assert rs.compose_bundle_caption_text([_row(caption="   ")], set(), "d", False).endswith("(no caption)")
    # a junk format is never echoed
    assert "(?)" in rs.compose_bundle_caption_text([_row(fmt="<b>video</b>")], set(), "d", False)


def test_the_decision_text_says_what_is_sent_what_is_not_and_why(configured, monkeypatch):
    members = [_row("x", "text"), _row("bluesky", "image")]
    previews = [_row("threads", "image")]
    text = rs.compose_bundle_decision_text("post", members, previews, "2026-09-28", False, ["⚠️ media note"])
    assert text.split("\n") == [
        "DECISION · POSTS · run 2026-09-28",
        "Approve all sends: X (text), BLUESKY (image)",
        "Not sent: THREADS (preview — not wired yet)",
        "⚠️ media note",
        "✂ drop = reject that one platform and keep the rest (optional).",
    ]
    monkeypatch.setattr(settings, "MARKETING_DRY_RUN", True)
    text = rs.compose_bundle_decision_text("video", members[:1], [], "2026-09-28", False, [])
    assert text.split("\n") == ["DECISION · VIDEO · run 2026-09-28", "Approve all sends: X (text)",
                                "⚠️ the web is in DRY RUN: approving will not send X"]


def test_result_lines_cover_every_outcome(configured, monkeypatch):
    rows = {o: _row(p) for o, p in (("approved", "x"), ("rejected", "bluesky"), ("already_approved", "threads"),
                                     ("moved", "facebook"), ("changed", "linkedin"), ("already_<b>", "x"))}
    results = [{"post_id": r["id"], "outcome": o, "row": r} for o, r in rows.items()]
    results.append({"post_id": "12345678-dead", "outcome": "not_found", "row": None})
    lines, blocked = rs.bundle_result_lines(results)
    assert lines == [
        "• X (text): approved",
        "• BLUESKY (text): rejected",
        "• THREADS (text): already approved",
        "• FACEBOOK (text): not decided here — it was re-sent in a newer message; decide it there",
        "• LINKEDIN (text): not decided — its text changed after it was shown",
        "• X (text): already decided",
        "• post 12345678: not found",
    ]
    assert blocked is False
    monkeypatch.setattr(settings, "MARKETING_ENABLED", False)
    lines, blocked = rs.bundle_result_lines(results[:1])
    assert blocked and lines == ["• X (text): approved — ⚠️ publishing is OFF on the web: NOT sent (it expires "
                                 "after its day)"]


@pytest.mark.parametrize("reoffered, tail", [
    (True, "; it will be offered again"),
    (False, ""),          # a release that did not land, or a newer bundle stamped it: no promise
    ("yes", ""),          # only a real True promises it
    (None, ""),
])
def test_a_changed_member_is_promised_another_offer_only_when_it_was_released(configured, reoffered, tail):
    """RB-3: the owner is told a hand-edited member comes back only when `review_bundle` cleared its
    review stamps (`reoffered` True) — otherwise the line keeps saying just that it was not decided."""
    row = _row("linkedin")
    (line,), blocked = rs.bundle_result_lines([{"post_id": row["id"], "outcome": "changed", "row": row,
                                               "reoffered": reoffered}])
    assert line == "• LINKEDIN (text): not decided — its text changed after it was shown" + tail
    assert blocked is False


# ── run_service.review_bundle ─────────────────────────────────────────────────


@pytest.fixture
def ops(monkeypatch):
    """The ledger operations run_service performs (its `_exec` op names), in order."""
    seen: List[str] = []
    real = mrs._exec

    async def recording(query, *, op, **ids):
        seen.append(op)
        return await real(query, op=op, **ids)

    monkeypatch.setattr(mrs, "_exec", recording)
    return seen


def _three(svc) -> List[str]:
    _seed_run(svc, video_id=None)
    return [_seed_post(svc, RUN_A, p, "text", caption=SHORT, title=None) for p in ("x", "bluesky", "threads")]


@pytest.mark.asyncio
async def test_approve_all_decides_every_pending_member_in_one_update(bundles, ops):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    outcome, results = await bundles.review_bundle(bid, "approve", reviewed_by="telegram:1")
    assert outcome == "decided"
    assert [r["outcome"] for r in results] == ["approved"] * 3
    assert [r["platform"] for r in results] == ["x", "bluesky", "threads"]
    assert ops.count("review_bundle") == 1                       # ONE conditional UPDATE decides them all
    for pid in pids:
        row = _post(bundles, pid)
        assert row["status"] == "approved" and row["approved_by"] == "telegram:1" and row["approved_at"]
        review = row["metadata"]["review"]
        assert review == {"decision": "approved", "by": "telegram:1", "at": review["at"], "bundle_id": bid}
        assert row["metadata"]["dry_run"] is False and row["metadata"]["review_bundle"]["id"] == bid


@pytest.mark.asyncio
async def test_reject_all_rejects_and_never_sets_approved_columns(bundles):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    outcome, results = await bundles.review_bundle(bid, "reject", reviewed_by="telegram:1")
    assert outcome == "decided" and {r["outcome"] for r in results} == {"rejected"}
    for pid in pids:
        row = _post(bundles, pid)
        assert row["status"] == "rejected" and row.get("approved_by") is None
        assert row["metadata"]["review"]["decision"] == "rejected"


@pytest.mark.asyncio
async def test_only_members_still_pending_still_stamped_and_unchanged_are_decided(bundles):
    _seed_run(bundles, video_id=None)
    ok, decided, moved, changed, gone = (_seed_post(bundles, RUN_A, p, "text", caption=SHORT, title=None)
                                         for p in ("x", "bluesky", "threads", "facebook", "linkedin"))
    bid = _stamp_bundle(bundles, [ok, decided, moved, changed, gone])
    _post(bundles, decided).update({"status": "rejected"})                      # its own ✂ drop
    _stamp_bundle(bundles, [moved])                                             # re-sent in a newer bundle
    _post(bundles, changed)["caption"] = "edited after it was shown"
    _rows(bundles, mrs.POSTS).remove(_post(bundles, gone))
    outcome, results = await bundles.review_bundle(bid, "approve", reviewed_by="telegram:1")
    assert outcome == "decided"
    assert [(r["platform"], r["outcome"]) for r in results] == [
        ("x", "approved"), ("bluesky", "already_rejected"), ("threads", "moved"), ("facebook", "changed"),
        (None, "not_found")]
    assert _post(bundles, moved)["status"] == "pending_review"
    assert _post(bundles, changed)["status"] == "pending_review"
    assert _post(bundles, decided)["status"] == "rejected"


@pytest.mark.asyncio
async def test_a_replayed_decision_decides_nothing_and_writes_nothing(bundles, ops):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    await bundles.review_bundle(bid, "approve", reviewed_by="telegram:1")
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    ops.clear()
    outcome, results = await bundles.review_bundle(bid, "reject", reviewed_by="telegram:1")
    assert outcome == "nothing" and {r["outcome"] for r in results} == {"already_approved"}
    assert _rows(bundles, mrs.POSTS) == before
    assert "review_bundle" not in ops                              # no UPDATE when nothing is eligible


@pytest.mark.asyncio
async def test_the_update_itself_is_fenced_on_the_bundle_id_and_the_status(bundles, monkeypatch):
    """The read can be stale: a member re-stamped (or decided) between `bundle_posts` and the UPDATE must
    still not be flipped — the fence is IN the UPDATE, not only in the read."""
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    real = bundles.bundle_posts

    async def stale_read(bundle_id):
        rows = copy.deepcopy(await real(bundle_id))
        _stamp_bundle(bundles, [pids[1]])                          # re-sent meanwhile
        _post(bundles, pids[2]).update({"status": "approved"})     # its per-post button meanwhile
        return rows

    monkeypatch.setattr(bundles, "bundle_posts", stale_read)
    outcome, results = await bundles.review_bundle(bid, "reject", reviewed_by="telegram:1")
    assert [r["outcome"] for r in results] == ["rejected", "moved", "already_approved"]
    assert [_post(bundles, p)["status"] for p in pids] == ["rejected", "pending_review", "approved"]


@pytest.mark.asyncio
async def test_an_unknown_bundle_is_not_found(bundles):
    _three(bundles)
    assert await bundles.review_bundle(str(uuid.uuid4()), "approve", reviewed_by="t") == ("not_found", [])


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle_id, decision", [(_PID, "maybe"), (_PID.upper(), "approve"), ("nope", "approve"),
                                                 (None, "approve")])
async def test_bad_inputs_raise_before_any_read(bundles, ops, bundle_id, decision):
    with pytest.raises(ValueError):
        await bundles.review_bundle(bundle_id, decision, reviewed_by="t")
    assert ops == []


@pytest.mark.asyncio
async def test_a_failed_update_raises_and_decides_nothing(bundles):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    bundles.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    with pytest.raises(mrs.MarketingRunError):
        await bundles.review_bundle(bid, "approve", reviewed_by="t")
    assert {_post(bundles, p)["status"] for p in pids} == {"pending_review"}


@pytest.mark.asyncio
@pytest.mark.parametrize("merge", ["raises", "lost"])
async def test_the_review_record_is_best_effort_and_the_decision_stands(bundles, monkeypatch, caplog, merge):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)

    async def broken(*_a, **_k):
        if merge == "raises":
            raise mrs.MarketingRunError("transition_post failed: 520")
        return None   # the publisher claimed it in between

    monkeypatch.setattr(bundles, "transition_post", broken)
    caplog.set_level(logging.WARNING)
    outcome, results = await bundles.review_bundle(bid, "approve", reviewed_by="t")
    assert outcome == "decided" and [r["outcome"] for r in results] == ["approved"] * 3
    for pid in pids:
        assert _post(bundles, pid)["status"] == "approved" and "review" not in _meta(bundles, pid)
    assert sum("review record" in r.getMessage() for r in caplog.records) == 3


@pytest.mark.asyncio
async def test_bundle_posts_reads_only_well_formed_carriers(bundles):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    _meta(bundles, pids[2])["review_bundle"]["kind"] = "junk"      # no longer reads back
    assert [r["id"] for r in await bundles.bundle_posts(bid)] == pids[:2]
    with pytest.raises(ValueError):
        await bundles.bundle_posts(bid.upper())


# ── the sweep ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_day_becomes_two_bundles_media_then_distinct_captions_then_one_decision(bundles, tg):
    ids = _seed_day(bundles)
    counters = await rs.review_cycle()
    assert counters == {"pending": 8, "sent": 8, "stamped": 8, "videos": 1, "failed": 0, "rate_limited": 0,
                        "bundles": 2, "images": 1, "held": 0}
    assert [m for m, _ in tg.calls] == ["sendVideo", "sendMessage", "sendMessage", "sendMessage", "sendMessage",
                                        "sendPhoto", "sendMessage", "sendMessage", "sendMessage"]
    assert all("parse_mode" not in p for _m, p in tg.calls)
    video = tg.of("sendVideo")[0]
    assert video["caption"] == ("VIDEO · TIKTOK, YOUTUBE, INSTAGRAM · run 2026-09-28\n"
                                "The captions and your decision follow.")
    photo = tg.of("sendPhoto")[0]
    assert photo["photo"].endswith(f"/2026-09-28/card-{'b' * 16}.jpg")
    assert photo["caption"].startswith("IMAGE · BLUESKY, THREADS, FACEBOOK, LINKEDIN · run 2026-09-28")
    msgs = tg.of("sendMessage")
    assert msgs[0]["text"] == "CAPTION · TIKTOK (video) · run 2026-09-28\n\ntiktok caption"
    assert msgs[1]["text"] == "CAPTION · YOUTUBE (video) · run 2026-09-28\n\nYT title\n\nyoutube caption"
    assert msgs[4]["text"] == f"CAPTION · X (text), BLUESKY (image), THREADS (image) · run 2026-09-28\n\n{SHORT}"
    assert msgs[5]["text"] == f"CAPTION · FACEBOOK (image), LINKEDIN (image) · run 2026-09-28\n\n{LONG}"
    assert all("reply_markup" not in m for i, m in enumerate(msgs) if i not in (3, 6))
    video_decision, post_decision = _decision_messages(tg)
    assert video_decision["text"].startswith("DECISION · VIDEO · run 2026-09-28\nApprove all sends: TIKTOK (video), "
                                             "YOUTUBE (video), INSTAGRAM (video)")
    assert post_decision["text"].startswith("DECISION · POSTS · run 2026-09-28\nApprove all sends: X (text), "
                                            "BLUESKY (image), THREADS (image), FACEBOOK (image), LINKEDIN (image)")
    for decision, platforms, kind in ((video_decision, VIDEO_PLATFORMS, "video"),
                                      (post_decision, POST_PLATFORMS_TEXT, "post")):
        bid = _bundle_id(decision)
        assert decision["reply_markup"] == rs.bundle_keyboard(bid, [_post(bundles, ids[p]) for p in platforms])
        for p in platforms:
            meta = _meta(bundles, ids[p])
            assert meta["review_bundle"] == {"id": bid, "kind": kind, "members": [ids[q] for q in platforms],
                                             "caption_sha": mrs.review_caption_sha(_post(bundles, ids[p]))}
            assert meta["review_notified_at"] and meta["review_message_id"] == decision["message_id"]
            assert meta["dry_run"] is False and _post(bundles, ids[p])["status"] == "pending_review"
    assert video_decision["message_id"] != post_decision["message_id"]
    tg.calls.clear()
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []        # never offered twice


@pytest.mark.asyncio
async def test_a_text_day_sends_no_image_and_keeps_x_text(bundles, tg):
    _seed_day(bundles, image=False)
    counters = await rs.review_cycle()
    assert counters["images"] == 0 and counters["bundles"] == 2 and counters["failed"] == 0
    assert "sendPhoto" not in [m for m, _ in tg.calls]
    assert _decision_messages(tg)[1]["text"].startswith(
        "DECISION · POSTS · run 2026-09-28\nApprove all sends: X (text), BLUESKY (text), THREADS (text)")


@pytest.mark.asyncio
@pytest.mark.parametrize("status, stage", [
    ("in_progress", "rendered"), ("failed", "rendered"), ("planned", "planned"), ("failed", "voiced"),
    ("skipped", "rendered"), ("failed", None),
    # only the exact checkpoint name counts — never a near miss or another type
    ("failed", "ASSETS_READY"), ("failed", "assets_ready "), ("failed", ["assets_ready"]),
])
async def test_nothing_is_offered_before_every_post_of_the_run_exists(bundles, tg, status, stage):
    """Re-pinned 2026-10-15 (plan Batch 1 item 10): a run is held until it is `media_ready` OR its stage
    checkpoint is `assets_ready` (written only after create_posts answered). A run that closed `failed`
    still at `rendered` stays held — create_posts may never have answered, so its posts are not known to
    be complete."""
    ids = _seed_day(bundles, run_status=status)
    run = next(r for r in _rows(bundles, mrs.RUNS) if r["id"] == RUN_A)
    run["stage"] = stage
    counters = await rs.review_cycle()
    assert counters["held"] == 8 and counters["sent"] == 0 and tg.calls == []
    assert all("review_bundle" not in _meta(bundles, pid) for pid in ids.values())
    run["status"] = "media_ready"
    assert (await rs.review_cycle())["bundles"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "in_progress"])
async def test_a_run_past_the_assets_ready_checkpoint_is_offered_whatever_its_status(bundles, tg, caplog, status):
    """The tick died after the `assets_ready` checkpoint (every post recorded) but before `media_ready`:
    the day's bundles go out exactly as on a media_ready day instead of letting its finished posts expire
    unseen — and the log says why the run was offered anyway."""
    ids = _seed_day(bundles, run_status=status)          # `_seed_run` leaves the stage at assets_ready
    caplog.set_level(logging.WARNING)
    counters = await rs.review_cycle()
    assert counters == {"pending": 8, "sent": 8, "stamped": 8, "videos": 1, "failed": 0, "rate_limited": 0,
                        "bundles": 2, "images": 1, "held": 0}
    video_decision, post_decision = _decision_messages(tg)
    assert video_decision["text"].startswith("DECISION · VIDEO · run 2026-09-28\nApprove all sends: TIKTOK (video)")
    assert post_decision["text"].startswith("DECISION · POSTS · run 2026-09-28\nApprove all sends: X (text)")
    for pid in ids.values():
        meta = _meta(bundles, pid)
        assert meta["review_notified_at"] and meta["review_bundle"]["id"]
    assert any(f"run_id={RUN_A} is '{status}' but reached the assets_ready checkpoint" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    tg.calls.clear()
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []       # never offered twice


@pytest.mark.asyncio
async def test_a_media_ready_run_is_offered_with_no_warning(bundles, tg, caplog):
    """The status half of the rule is unchanged: a media_ready run is offered whatever its stage reads, and
    a normal day logs no "offered anyway" warning."""
    _seed_day(bundles)
    next(r for r in _rows(bundles, mrs.RUNS) if r["id"] == RUN_A)["stage"] = "rendered"
    caplog.set_level(logging.WARNING)
    assert (await rs.review_cycle())["bundles"] == 2
    assert not any("offering its" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("run, offered", [
    ({"status": "media_ready", "stage": "assets_ready"}, True),
    ({"status": "media_ready", "stage": "rendered"}, True),        # the status alone suffices (unchanged)
    ({"status": "media_ready"}, True),
    ({"status": "failed", "stage": "assets_ready"}, True),
    ({"status": "in_progress", "stage": "assets_ready"}, True),
    ({"stage": "assets_ready"}, True),
    ({"status": "failed", "stage": "rendered"}, False),
    ({"status": "in_progress", "stage": "rendered"}, False),
    ({"status": "skipped", "stage": "voiced"}, False),
    ({"status": "failed"}, False),
    ({"status": "MEDIA_READY", "stage": "ASSETS_READY"}, False),
    ({"status": ["media_ready"], "stage": ("assets_ready",)}, False),
    ({"status": None, "stage": None}, False),
    ({}, False), (None, False), ("media_ready", False), (["assets_ready"], False),
])
def test_bundle_offerable_is_the_status_or_the_checkpoint_and_nothing_else(run, offered):
    assert rs.bundle_offerable(run) is offered


def _worker_function_calls(path: str, func: str) -> List[str]:
    """Dotted names of every call inside `func` in a worker source file (AST; comments never count)."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "marketing" / path).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            out.append(f"{node.func.value.id}.{node.func.attr}")
    return out


def test_the_assets_ready_checkpoint_is_the_workers_create_posts_stage():
    """`bundle_offerable` trusts `assets_ready` ONLY because the worker writes that checkpoint after the stage
    that calls create_posts answered. Pin the chain from the worker's own source: MEDIA_STAGES ends with
    ("assets_ready", stage_posts), that function hands off to `render.stage_posts`, which calls
    `api.create_posts` — and the name is the schema's last RUN_STAGES value."""
    import ast
    from pathlib import Path

    from app.schemas.marketing import RUN_STAGES

    tree = ast.parse((Path(__file__).resolve().parents[1] / "marketing" / "main.py").read_text(encoding="utf-8"))
    stages = None
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == "MEDIA_STAGES":
            stages = [(elt.elts[0].value, elt.elts[1].id) for elt in node.value.elts]
    assert stages, "MEDIA_STAGES not found in backend/marketing/main.py"
    assert stages[-1] == (rs._BUNDLE_RUN_STAGE, "stage_posts")
    assert [name for name, _fn in stages].count(rs._BUNDLE_RUN_STAGE) == 1
    assert rs._BUNDLE_RUN_STAGE == RUN_STAGES[-1]
    assert "render.stage_posts" in _worker_function_calls("main.py", "stage_posts")
    assert "api.create_posts" in _worker_function_calls("render.py", "stage_posts")


@pytest.mark.asyncio
async def test_a_missing_run_holds_its_posts(bundles, tg):
    _seed_post(bundles, "99999999-9999-4999-8999-999999999999", "x", "text")
    counters = await rs.review_cycle()
    assert counters["held"] == 1 and tg.calls == []


@pytest.mark.asyncio
async def test_the_switch_off_is_the_per_post_flow(ledger, tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_REVIEW_BUNDLES", False)
    ids = _seed_day(ledger)
    counters = await rs.review_cycle()
    assert counters == {"pending": 8, "sent": 8, "stamped": 8, "videos": 1, "failed": 0, "rate_limited": 0}
    assert [m for m, _ in tg.calls] == ["sendVideo"] + ["sendMessage"] * 8
    assert all("review_bundle" not in _meta(ledger, pid) for pid in ids.values())
    assert all(rs.parse_callback_data(m["reply_markup"]["inline_keyboard"][0][0]["callback_data"])[0] == "approve"
               for m in tg.of("sendMessage"))


@pytest.mark.asyncio
async def test_previews_ride_along_never_as_members(bundles, tg, monkeypatch):
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [p for p in POST_PLATFORMS if p != "threads"])
    ids = _seed_day(bundles)
    counters = await rs.review_cycle()
    assert counters["sent"] == 8 and counters["bundles"] == 2 and counters["stamped"] == 8
    photo = tg.of("sendPhoto")[0]
    assert photo["caption"].startswith("IMAGE · BLUESKY, FACEBOOK, LINKEDIN, THREADS (preview) · run")
    shared = next(m for m in tg.of("sendMessage") if m["text"].startswith("CAPTION · X"))
    assert "Preview only — THREADS not wired yet: never sent" in shared["text"]
    post_decision = _decision_messages(tg)[1]
    assert "Not sent: THREADS (preview — not wired yet)" in post_decision["text"]
    assert f"s:{ids['threads']}" not in json.dumps(post_decision["reply_markup"])
    threads = _meta(bundles, ids["threads"])
    assert threads["review_preview_at"] and "review_bundle" not in threads and "review_notified_at" not in threads
    assert ids["threads"] not in _meta(bundles, ids["x"])["review_bundle"]["members"]
    tg.calls.clear()
    assert (await rs.review_cycle())["sent"] == 0 and tg.calls == []
    # Once Threads is wired it is offered on its own — one member, so no drop button.
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: list(POST_PLATFORMS))
    counters = await rs.review_cycle()
    assert counters["bundles"] == 1 and counters["sent"] == 1
    (decision,) = _decision_messages(tg)
    assert decision["text"].startswith("DECISION · POSTS · run 2026-09-28\nApprove all sends: THREADS (image)")
    assert len(decision["reply_markup"]["inline_keyboard"]) == 2


@pytest.mark.asyncio
async def test_a_bundle_stamp_that_does_not_land_sends_nothing(bundles, tg, monkeypatch, caplog):
    ids = _seed_day(bundles)
    real = bundles.transition_post
    calls = {"n": 0}

    async def flaky(post_id, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise mrs.MarketingRunError("transition_post failed: 520")
        return await real(post_id, **kw)

    monkeypatch.setattr(bundles, "transition_post", flaky)
    caplog.set_level(logging.WARNING)
    counters = await rs.review_cycle()
    # The video bundle never went out (its 2nd stamp failed); the post bundle did.
    assert counters["bundles"] == 1 and counters["failed"] == 3 and counters["videos"] == 0
    assert "sendVideo" not in [m for m, _ in tg.calls]
    assert any("bundle stamp FAILED" in r.getMessage() for r in caplog.records)
    assert all("review_notified_at" not in _meta(bundles, ids[p]) for p in VIDEO_PLATFORMS)
    monkeypatch.setattr(bundles, "transition_post", real)
    tg.calls.clear()
    counters = await rs.review_cycle()
    assert counters["bundles"] == 1 and counters["videos"] == 1
    bid = _bundle_id(_decision_messages(tg)[0])
    assert {_meta(bundles, ids[p])["review_bundle"]["id"] for p in VIDEO_PLATFORMS} == {bid}


@pytest.mark.asyncio
async def test_a_failed_send_is_offered_again_under_a_new_id_and_the_old_buttons_decide_nothing(bundles, tg, wakes):
    ids = _seed_day(bundles, image=False)
    # The video bundle's decision message (the 4th sendMessage) answers 502: nothing is notified.
    tg.script["sendMessage"] = [None, None, None, (502, b"")]
    counters = await rs.review_cycle()
    assert counters["failed"] == 3 and counters["bundles"] == 1
    old = _meta(bundles, ids["tiktok"])["review_bundle"]["id"]
    assert all("review_notified_at" not in _meta(bundles, ids[p]) for p in VIDEO_PLATFORMS)
    tg.calls.clear()
    tg.ids.clear()
    await rs.review_cycle()
    (fresh,) = _decision_messages(tg)
    assert _bundle_id(fresh) != old
    # A tap on the old bundle (had its message gone out) decides nothing.
    res = await rs.handle_update(_update(old, "g"))
    assert res["outcome"] == "not_found" and wakes == []
    assert {_post(bundles, ids[p])["status"] for p in VIDEO_PLATFORMS} == {"pending_review"}


@pytest.mark.asyncio
async def test_a_429_stops_the_sweep_and_notifies_nothing_unsent(bundles, tg):
    ids = _seed_day(bundles)
    tg.script["sendMessage"] = [(429, {"ok": False, "parameters": {"retry_after": 30}})]
    counters = await rs.review_cycle()
    assert counters["rate_limited"] == 1 and counters["bundles"] == 0
    assert all("review_notified_at" not in _meta(bundles, pid) for pid in ids.values())
    assert rs._rate_limited_until > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [(400, {"ok": False, "description": "Bad Request: failed to get HTTP URL content"}),
                                    (502, b"")])
async def test_a_photo_telegram_cannot_take_becomes_a_link(bundles, tg, answer):
    _seed_day(bundles)
    tg.script["sendPhoto"] = [answer]
    counters = await rs.review_cycle()
    assert counters["bundles"] == 2 and counters["failed"] == 0
    link = next(m for m in tg.of("sendMessage") if m["text"].startswith("IMAGE · "))
    assert link["text"].endswith(f"/2026-09-28/card-{'b' * 16}.jpg")


@pytest.mark.asyncio
async def test_a_photo_over_5_mb_is_sent_as_a_link(bundles, tg):
    _seed_day(bundles, image=False)
    _seed_image(bundles, size=6 * 1024 * 1024)
    for p in ("bluesky", "facebook"):
        row = next(r for r in _rows(bundles, mrs.POSTS) if r["platform"] == p)
        row.update({"format": "image", "asset_ids": [IMAGE]})
    await rs.review_cycle()
    assert tg.of("sendPhoto") == []
    assert any(m["text"].startswith("IMAGE · BLUESKY, FACEBOOK · run") for m in tg.of("sendMessage"))


@pytest.mark.asyncio
async def test_when_neither_the_photo_nor_its_link_goes_out_the_bundle_waits(bundles, tg):
    ids = _seed_day(bundles)
    tg.script["sendPhoto"] = [(502, b"")]
    # sendMessage #4 is the video decision; #5 is the image link — fail that one.
    tg.script["sendMessage"] = [None, None, None, None, (502, b"")]
    counters = await rs.review_cycle()
    assert counters["bundles"] == 1 and counters["failed"] == 5
    assert all("review_notified_at" not in _meta(bundles, ids[p]) for p in POST_PLATFORMS_TEXT)


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"status": "pending_upload"}, {"metadata": {"image_role": "other"}},
                                   {"run_id": "22222222-2222-4222-8222-222222222222"}, {"kind": "video"}])
async def test_only_a_verified_image_card_of_this_run_is_shown(bundles, tg, patch):
    _seed_day(bundles)
    next(a for a in _rows(bundles, mrs.ASSETS) if a["id"] == IMAGE).update(patch)
    await rs.review_cycle()
    assert tg.of("sendPhoto") == []
    post_decision = _decision_messages(tg)[1]
    assert "⚠️ No verified image card was found for the image posts — do not approve them." in post_decision["text"]


def _rehearse(svc, ids) -> None:
    """Record every post of the day as a rehearsal, as create_posts does on a dry-run run."""
    for pid in ids.values():
        _meta(svc, pid)["dry_run"] = True


@pytest.mark.asyncio
async def test_the_dry_run_label_reaches_every_bundle_message(bundles, tg, monkeypatch):
    """Re-pinned 2026-10-16 (Batch 1 review): the label is the POSTS' — a dry-run run whose posts were recorded
    as a rehearsal reads "DRY RUN" on every message, with no ⚠️ line (run and posts agree) and no web blocker
    (a rehearsal row is never sent, whatever the web switches say)."""
    _rehearse(bundles, _seed_day(bundles, dry_run=True))
    monkeypatch.setattr(settings, "MARKETING_ENABLED", False)
    await rs.review_cycle()
    assert "· DRY RUN" in tg.of("sendVideo")[0]["caption"] and "· DRY RUN" in tg.of("sendPhoto")[0]["caption"]
    assert all("· DRY RUN" in m["text"].split("\n")[0] for m in tg.of("sendMessage"))
    for decision in _decision_messages(tg):
        assert "⚠️" not in decision["text"] and "The run is now marked" not in decision["text"]


#: The F2 line when every member is live and nothing on the web holds one back.
_LIVE_UNDER_A_DRY_RUN = ("⚠️ The run is now marked dry-run, but these posts were recorded LIVE (not a rehearsal): "
                         "approving publishes them.")


@pytest.mark.asyncio
async def test_a_run_re_claimed_in_dry_run_never_labels_its_live_posts_dry_run(bundles, tg, wakes, caplog):
    """Batch 1 review F2 (2026-10-16). Attempt 1 recorded LIVE posts (`metadata.dry_run` False) and died at
    the assets_ready checkpoint; the owner set the worker to dry run; the re-claim rewrote the RUN's flag to
    True. The publisher sends a post on the POST's flag alone (`dry = MARKETING_DRY_RUN or is_rehearsal(post)`),
    so no message may read "DRY RUN", and each decision says, under its header, that approving publishes."""
    ids = _seed_day(bundles, run_status="failed", dry_run=True)      # the run row says dry run; every post is live
    caplog.set_level(logging.WARNING)
    assert (await rs.review_cycle())["bundles"] == 2
    assert "DRY RUN" not in tg.of("sendVideo")[0]["caption"] and "DRY RUN" not in tg.of("sendPhoto")[0]["caption"]
    assert not any("DRY RUN" in m["text"] for m in tg.of("sendMessage"))
    video_decision, post_decision = _decision_messages(tg)
    for decision, head in ((video_decision, "DECISION · VIDEO · run 2026-09-28"),
                           (post_decision, "DECISION · POSTS · run 2026-09-28")):
        lines = decision["text"].split("\n")
        assert lines[0] == head and lines[1] == _LIVE_UNDER_A_DRY_RUN and lines[2].startswith("Approve all sends: ")
    # one WARNING per bundle, greppable by run and bundle
    warned = [r.getMessage() for r in caplog.records if "dry-run flags differ" in r.getMessage()]
    assert len(warned) == 2 and all(f"run_id={RUN_A}" in m and "run row dry_run=True" in m for m in warned)
    assert any(f"bundle_id={_bundle_id(post_decision)}" in m and "rehearsal posts=[]" in m for m in warned)
    # …and the warning was true: Approve all leaves rows the publisher's own rule treats as LIVE.
    await rs.handle_update(_tap_on(post_decision, "g"))
    for p in POST_PLATFORMS_TEXT:
        row = _post(bundles, ids[p])
        assert row["status"] == "approved" and pub.is_rehearsal(row) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("switch, value, blocker", [
    ("MARKETING_ENABLED", False, "⚠️ publishing is OFF on the web: approving will not send TIKTOK, YOUTUBE, INSTAGRAM"),
    ("MARKETING_DRY_RUN", True, "⚠️ the web is in DRY RUN: approving will not send TIKTOK, YOUTUBE, INSTAGRAM"),
])
async def test_live_posts_the_web_holds_back_are_called_real_but_never_promised(bundles, tg, monkeypatch, switch,
                                                                              value, blocker):
    """Same day with the web unable to send: the line still says they are real (live) posts — if the switch
    flips on their day they go out — but never "approving publishes them", which the blocker line would
    contradict; the blocker line keeps its place and words."""
    _seed_day(bundles, dry_run=True)
    monkeypatch.setattr(settings, switch, value)
    await rs.review_cycle()
    lines = _decision_messages(tg)[0]["text"].split("\n")
    assert lines[1] == "⚠️ The run is now marked dry-run, but these posts were recorded LIVE (not a rehearsal)."
    assert blocker in lines and lines.index(blocker) > 2
    assert not any("approving publishes" in line for line in lines)
    assert "DRY RUN" not in lines[0]


@pytest.mark.asyncio
async def test_a_run_re_claimed_live_over_rehearsal_posts_reads_dry_run_and_says_nothing_is_sent(bundles, tg):
    """The reverse re-claim: the posts were recorded as a rehearsal, the run row now says live. The label stays
    "DRY RUN" (the posts are never sent) and the decision says why, for the owner who just turned it live."""
    _rehearse(bundles, _seed_day(bundles, dry_run=False))
    await rs.review_cycle()
    assert all("· DRY RUN" in m["text"].split("\n")[0] for m in tg.of("sendMessage"))
    for decision in _decision_messages(tg):
        assert decision["text"].split("\n")[1] == ("⚠️ The run is now marked live, but these posts were recorded as "
                                                   "a DRY RUN: approving will not send them.")


@pytest.mark.asyncio
async def test_a_long_caption_is_split_and_the_decision_stays_one_message(bundles, tg):
    _seed_run(bundles, video_id=None)
    caption = "\n".join(f"Paragraph {i}. " + "word " * 40 for i in range(60))
    _seed_post(bundles, RUN_A, "linkedin", "text", caption=caption, title=None)
    _seed_post(bundles, RUN_A, "facebook", "text", caption=caption, title=None)
    await rs.review_cycle()
    msgs = tg.of("sendMessage")
    assert len(msgs) >= 4 and all(rs.utf16_len(m["text"]) <= 4096 for m in msgs)
    assert [("reply_markup" in m) for m in msgs] == [False] * (len(msgs) - 1) + [True]
    assert msgs[0]["text"].startswith("CAPTION · LINKEDIN (text), FACEBOOK (text) · run")


@pytest.mark.asyncio
async def test_a_member_decided_while_its_bundle_was_sent_is_not_stamped_notified(bundles, tg, caplog):
    ids = _seed_day(bundles, image=False)

    def decide_meanwhile(request, payload):
        _post(bundles, ids["x"]).update({"status": "approved", "updated_at": "2026-09-28T23:00:00.000000+00:00"})
        return None

    # the post bundle's decision message is the 7th sendMessage (3 video captions + decision + 2 captions)
    tg.script["sendMessage"] = [None] * 6 + [decide_meanwhile]
    caplog.set_level(logging.INFO)
    counters = await rs.review_cycle()
    assert counters["stamped"] == 7
    assert "review_notified_at" not in _meta(bundles, ids["x"])
    assert any("was decided (approved) while bundle" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_member_re_stamped_while_its_bundle_was_sent_is_not_stamped_notified(bundles, tg, caplog):
    """Two containers overlapping in a redeploy can both offer a run: the member then carries the OTHER
    bundle's id, and this bundle must not mark it notified (its buttons no longer decide it)."""
    ids = _seed_day(bundles, image=False)
    other = str(uuid.uuid4())

    def restamp_meanwhile(request, payload):
        row = _post(bundles, ids["bluesky"])
        row["metadata"] = {**row["metadata"], "review_bundle": {**row["metadata"]["review_bundle"], "id": other}}
        return None

    tg.script["sendMessage"] = [None] * 6 + [restamp_meanwhile]
    caplog.set_level(logging.WARNING)
    counters = await rs.review_cycle()
    assert counters["stamped"] == 7
    assert "review_notified_at" not in _meta(bundles, ids["bluesky"])
    assert _meta(bundles, ids["x"])["review_notified_at"]
    assert any("no longer carries bundle" in r.getMessage() for r in caplog.records)


# ── the webhook ───────────────────────────────────────────────────────────────


async def _day_with_decisions(svc, tg, **kw):
    ids = _seed_day(svc, **kw)
    await rs.review_cycle()
    video, post = _decision_messages(tg)
    tg.calls.clear()
    tg.ids.clear()
    return ids, video, post


@pytest.mark.asyncio
async def test_approve_all_approves_every_member_wakes_once_and_shows_one_line_each(bundles, tg, wakes):
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    res = await rs.handle_update(_tap_on(video, "g"))
    assert res["outcome"] == "decided" and res["decided"] == 3
    assert {_post(bundles, ids[p])["status"] for p in VIDEO_PLATFORMS} == {"approved"}
    assert {_post(bundles, ids[p])["status"] for p in POST_PLATFORMS_TEXT} == {"pending_review"}
    assert wakes == [1]
    assert _answers(tg) == ["Approved 3 ✅"]
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == video["message_id"] and edit["reply_markup"] == {"inline_keyboard": []}
    tail = edit["text"][len(video["text"]):]
    assert re.fullmatch(r"\n\n✅ Approved \d\d:\d\d ET — 3 of 3:\n• TIKTOK \(video\): approved\n"
                        r"• YOUTUBE \(video\): approved\n• INSTAGRAM \(video\): approved", tail)
    assert "parse_mode" not in edit
    # A replay (a double tap, Telegram retrying the update) decides nothing.
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    res = await rs.handle_update(_tap_on(video, "g"))
    assert res["outcome"] == "nothing" and _rows(bundles, mrs.POSTS) == before
    assert _answers(tg)[-1] == "Nothing left to decide" and wakes == [1]
    assert tg.of("editMessageText")[-1]["text"].endswith("• INSTAGRAM (video): already approved")


@pytest.mark.asyncio
async def test_reject_all_offers_the_reasons_once_and_a_reason_lands_on_every_member(bundles, tg, wakes):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    await rs.handle_update(_tap_on(post, "j"))
    assert {_post(bundles, ids[p])["status"] for p in POST_PLATFORMS_TEXT} == {"rejected"}
    assert wakes == [] and _answers(tg) == ["Rejected 5 ❌"]
    (edit,) = tg.of("editMessageText")
    bid = _bundle_id(post)
    assert edit["reply_markup"] == rs.bundle_reason_keyboard(bid)
    assert re.search(r"❌ Rejected \d\d:\d\d ET — 5 of 5:\n• X \(text\): rejected", edit["text"])
    # A replayed ❌ re-offers the reasons while none is recorded…
    await rs.handle_update(_tap_on(post, "j"))
    assert tg.of("editMessageText")[-1]["reply_markup"] == rs.bundle_reason_keyboard(bid)
    # …a reason lands on every rejected member and closes the keyboard…
    res = await rs.handle_update(_update(bid, "u", message={"message_id": post["message_id"], "text": "t"}))
    assert res["outcome"] == "recorded"
    for p in POST_PLATFORMS_TEXT:
        review = _meta(bundles, ids[p])["review"]
        assert (review["decision"], review["reason"], review["reason_by"]) == ("rejected", "weak", f"telegram:{OWNER}")
    assert _answers(tg)[-1] == "Reason saved: Weak / boring"
    last = tg.of("editMessageText")[-1]
    assert last["text"] == "t\n\nReason: Weak / boring — on 5 rejected posts"
    assert last["reply_markup"] == {"inline_keyboard": []}
    # …a second tap writes nothing, and a replayed ❌ no longer offers them.
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    await rs.handle_update(_update(bid, "u", message={"message_id": post["message_id"], "text": "t"}))
    assert _answers(tg)[-1] == "Reason already saved: Weak / boring" and _rows(bundles, mrs.POSTS) == before
    await rs.handle_update(_tap_on(post, "j"))
    assert tg.of("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_drop_rejects_one_member_and_keeps_the_rest_open_on_the_same_message(bundles, tg, wakes):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    bid = _bundle_id(post)
    res = await rs.handle_update(_tap_on(post, "s", ids["threads"]))
    assert res["outcome"] == "rejected" and _post(bundles, ids["threads"])["status"] == "rejected"
    assert _answers(tg) == ["Dropped THREADS ✂"]
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == post["message_id"]
    assert re.search(r"\n\n✂ Dropped THREADS \d\d:\d\d ET — the rest stay open$", edit["text"])
    rest = [_post(bundles, ids[p]) for p in ("x", "bluesky", "facebook", "linkedin")]
    assert edit["reply_markup"] == rs.bundle_keyboard(bid, rest)
    assert edit["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ Approve all (4)"
    # Approve all then decides the four; the dropped one reads "already rejected".
    after_drop = {**post, "text": edit["text"], "reply_markup": edit["reply_markup"]}
    res = await rs.handle_update(_tap_on(after_drop, "g"))
    assert res["decided"] == 4 and wakes == [1]
    assert _post(bundles, ids["threads"])["status"] == "rejected"
    assert {_post(bundles, ids[p])["status"] for p in ("x", "bluesky", "facebook", "linkedin")} == {"approved"}
    assert "• THREADS (image): already rejected" in tg.of("editMessageText")[-1]["text"]


@pytest.mark.asyncio
async def test_dropping_down_to_one_member_leaves_no_drop_buttons_and_the_last_drop_closes_it(bundles, tg):
    _seed_run(bundles, video_id=None)
    a, b = (_seed_post(bundles, RUN_A, p, "text", caption=SHORT, title=None) for p in ("x", "bluesky"))
    await rs.review_cycle()
    (decision,) = _decision_messages(tg)
    await rs.handle_update(_tap_on(decision, "s", a))
    kb = tg.of("editMessageText")[-1]["reply_markup"]
    assert kb == rs.bundle_keyboard(_bundle_id(decision), [_post(bundles, b)]) and len(kb["inline_keyboard"]) == 2
    await rs.handle_update(_tap_on({**decision, "reply_markup": kb}, "s", b))
    assert tg.of("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}
    assert {_post(bundles, a)["status"], _post(bundles, b)["status"]} == {"rejected"}


@pytest.mark.asyncio
async def test_a_drop_replay_says_already_and_writes_nothing(bundles, tg):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    await rs.handle_update(_tap_on(post, "s", ids["x"]))
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    res = await rs.handle_update(_tap_on(post, "s", ids["x"]))
    assert res["outcome"] == "already_rejected" and _rows(bundles, mrs.POSTS) == before
    assert _answers(tg)[-1].startswith("Already rejected")


@pytest.mark.asyncio
async def test_a_member_re_sent_in_a_newer_bundle_is_decided_only_there(bundles, tg, wakes):
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    # TikTok's notified stamp was lost: the next sweep re-sends it alone, under a new bundle.
    _meta(bundles, ids["tiktok"]).pop("review_notified_at")
    await rs.review_cycle()
    (newer,) = _decision_messages(tg)
    tg.calls.clear()
    await rs.handle_update(_tap_on(video, "g"))
    assert _post(bundles, ids["tiktok"])["status"] == "pending_review"
    assert ("• TIKTOK (video): not decided here — it was re-sent in a newer message; decide it there"
            in tg.of("editMessageText")[-1]["text"])
    assert _answers(tg) == ["Approved 2 ✅"]
    await rs.handle_update(_tap_on(newer, "g"))
    assert _post(bundles, ids["tiktok"])["status"] == "approved"


@pytest.mark.asyncio
async def test_approving_while_the_web_cannot_send_says_so(bundles, tg, wakes, monkeypatch):
    _ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    monkeypatch.setattr(settings, "MARKETING_DRY_RUN", True)
    await rs.handle_update(_tap_on(video, "g"))
    assert _answers(tg) == ["Approved 3 ✅ — some will NOT be sent (see the message)"]
    assert ("• TIKTOK (video): approved — ⚠️ the web is in DRY RUN: NOT sent (it expires after its day)"
            in tg.of("editMessageText")[0]["text"])


@pytest.mark.asyncio
async def test_a_result_that_cannot_be_edited_in_is_sent_under_the_decision(bundles, tg, wakes):
    _ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    tg.script["editMessageText"] = [(400, {"ok": False, "description": "Bad Request: message to edit not found"})]
    await rs.handle_update(_tap_on(video, "g"))
    (msg,) = tg.of("sendMessage")
    assert msg["reply_parameters"] == {"message_id": video["message_id"], "allow_sending_without_reply": True}
    assert re.match(r"✅ Approved \d\d:\d\d ET — 3 of 3:\n• TIKTOK \(video\): approved", msg["text"])
    assert "reply_markup" not in msg


@pytest.mark.asyncio
async def test_a_ledger_failure_keeps_the_buttons_for_another_tap(bundles, tg, wakes):
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    bundles.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    res = await rs.handle_update(_tap_on(video, "g"))
    assert res["outcome"] == "error" and wakes == []
    assert _answers(tg) == ["Could not record that — tap again"] and tg.of("editMessageText") == []
    assert {_post(bundles, ids[p])["status"] for p in VIDEO_PLATFORMS} == {"pending_review"}
    await rs.handle_update(_tap_on(video, "g"))
    assert {_post(bundles, ids[p])["status"] for p in VIDEO_PLATFORMS} == {"approved"}


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"from_id": OWNER + 1}, {"chat_id": OWNER + 1}, {"from_id": str(OWNER)}])
@pytest.mark.parametrize("verb", ["g", "j", "s", "h", "v"])
async def test_bundle_verbs_respect_the_owner_allow_list(bundles, tg, wakes, kw, verb):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    target = ids["x"] if verb == "s" else _bundle_id(post)
    res = await rs.handle_update(_update(target, verb, **kw))
    assert res == {"ok": True, "refused": "not_allowed"}
    assert _rows(bundles, mrs.POSTS) == before and wakes == []
    assert _answers(tg) == ["Not allowed"] and tg.of("editMessageText") == []


@pytest.mark.asyncio
async def test_bundle_taps_are_honoured_with_the_switch_off(bundles, tg, wakes, monkeypatch):
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    monkeypatch.setattr(settings, "MARKETING_REVIEW_BUNDLES", False)
    await rs.handle_update(_tap_on(video, "g"))
    assert {_post(bundles, ids[p])["status"] for p in VIDEO_PLATFORMS} == {"approved"}


@pytest.mark.asyncio
async def test_an_unknown_bundle_decides_nothing_and_says_so(bundles, tg, wakes):
    _seed_day(bundles)
    res = await rs.handle_update(_update(str(uuid.uuid4()), "j"))
    assert res["outcome"] == "not_found" and _answers(tg) == ["Nothing to decide here"]
    assert tg.of("editMessageText")[0]["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_a_reason_on_a_bundle_with_nothing_rejected_records_nothing(bundles, tg):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    res = await rs.handle_update(_update(_bundle_id(post), "h"))
    assert res["outcome"] == "none_rejected" and _rows(bundles, mrs.POSTS) == before
    assert _answers(tg) == ["No reason recorded — nothing in this bundle is rejected"]


@pytest.mark.asyncio
async def test_a_busy_reason_keeps_the_keyboard_for_another_tap(bundles, tg, monkeypatch):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    await rs.handle_update(_tap_on(post, "j"))
    edits = len(tg.of("editMessageText"))

    async def busy(*_a, **_k):
        return "busy"

    monkeypatch.setattr(bundles, "record_reject_reason", busy)
    res = await rs.handle_update(_update(_bundle_id(post), "i"))
    assert res["outcome"] == "busy" and _answers(tg)[-1] == "Could not record that for every post — tap again"
    assert len(tg.of("editMessageText")) == edits


def test_approve_all_through_the_real_webhook_route(client, bundles, tg, wakes):
    _seed_run(bundles, video_id=None)
    pids = [_seed_post(bundles, RUN_A, p, "text", caption=SHORT, title=None) for p in ("x", "bluesky")]
    bid = _stamp_bundle(bundles, pids)
    r = _post_hook(client, _update(bid, "g"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert {_post(bundles, p)["status"] for p in pids} == {"approved"} and wakes == [1]


# ── RB-3: a member whose text changed after it was shown is offered again ──────


@pytest.mark.asyncio
async def test_a_changed_member_is_released_for_the_next_sweep_and_reported(bundles):
    """The owner fixed one caption by hand after the bundle was shown: the tap decides the others and
    clears that member's review stamps (everything else in its metadata kept), so it is not stranded
    as notified-but-undecidable until it expires."""
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    _post(bundles, pids[1])["caption"] = "edited after it was shown"
    outcome, results = await bundles.review_bundle(bid, "approve", reviewed_by="telegram:1")
    assert outcome == "decided"
    assert [(r["outcome"], r["reoffered"]) for r in results] == [
        ("approved", False), ("changed", True), ("approved", False)]
    changed = _post(bundles, pids[1])
    assert changed["status"] == "pending_review" and changed["caption"] == "edited after it was shown"
    for key in ("review_bundle", "review_notified_at", "review_message_id"):
        assert key not in changed["metadata"], key
    assert changed["metadata"]["dry_run"] is False


@pytest.mark.asyncio
async def test_a_release_that_fails_is_logged_and_reported_and_the_decision_stands(bundles, monkeypatch, caplog):
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    _post(bundles, pids[1])["caption"] = "edited after it was shown"
    real = bundles.transition_post

    async def failing_release(post_id, **kw):
        if "review_bundle" in (kw.get("unset") or ()):
            raise mrs.MarketingRunError("transition_post failed: 520")
        return await real(post_id, **kw)

    monkeypatch.setattr(bundles, "transition_post", failing_release)
    caplog.set_level(logging.WARNING)
    outcome, results = await bundles.review_bundle(bid, "approve", reviewed_by="t")
    assert outcome == "decided" and [(r["outcome"], r["reoffered"]) for r in results] == [
        ("approved", False), ("changed", False), ("approved", False)]
    assert _meta(bundles, pids[1])["review_bundle"]["id"] == bid      # left as it was
    assert any("were NOT cleared" in r.getMessage() and pids[1] in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_release_is_fenced_on_the_row_as_it_was_read(bundles, monkeypatch):
    """A member re-sent in a NEWER bundle between the read and the release keeps that newer stamp."""
    pids = _three(bundles)
    bid = _stamp_bundle(bundles, pids)
    _post(bundles, pids[1])["caption"] = "edited after it was shown"
    real = bundles.bundle_posts

    async def stale_read(bundle_id):
        rows = copy.deepcopy(await real(bundle_id))
        newer = _stamp_bundle(bundles, [pids[1]])                       # re-sent meanwhile…
        _post(bundles, pids[1])["updated_at"] = "2026-09-28T23:59:00.000000+00:00"   # …a fenced write
        stale_read.newer = newer
        return rows

    monkeypatch.setattr(bundles, "bundle_posts", stale_read)
    _outcome, results = await bundles.review_bundle(bid, "approve", reviewed_by="t")
    assert (results[1]["outcome"], results[1]["reoffered"]) == ("changed", False)
    assert _meta(bundles, pids[1])["review_bundle"]["id"] == stale_read.newer
    assert _meta(bundles, pids[1])["review_notified_at"]


@pytest.mark.asyncio
async def test_after_approve_all_a_hand_edited_member_comes_back_in_a_new_bundle(bundles, tg, wakes):
    """End to end: the next sweep offers the edited text under a new bundle id, LinkedIn alone."""
    ids, _video, post = await _day_with_decisions(bundles, tg)
    old_bid = _bundle_id(post)
    _post(bundles, ids["linkedin"])["caption"] = "Fixed by hand in Studio."
    res = await rs.handle_update(_tap_on(post, "g"))
    assert res["outcome"] == "decided" and res["decided"] == 4
    assert _post(bundles, ids["linkedin"])["status"] == "pending_review"
    assert ("• LINKEDIN (image): not decided — its text changed after it was shown; it will be offered again"
            in tg.of("editMessageText")[-1]["text"])          # the owner is told it comes back
    tg.calls.clear()
    tg.ids.clear()
    await rs.review_cycle()
    (again,) = _decision_messages(tg)
    assert _bundle_id(again) != old_bid
    stamp = _meta(bundles, ids["linkedin"])["review_bundle"]
    assert stamp["id"] == _bundle_id(again) and stamp["members"] == [ids["linkedin"]]
    assert _meta(bundles, ids["linkedin"])["review_notified_at"]
    sent = "\n".join(p.get("text") or "" for method, p in tg.calls if method == "sendMessage")
    assert "Fixed by hand in Studio." in sent


# ── drop 1 re-review (2026-10-09): a bundle reason lands only on what THIS Reject all rejected ────────
#
# A ✂ drop goes through `review_post` (its review record carries no bundle id) and offers its own
# reasons. A later ❌ Reject all + bundle reason used to overwrite the dropped post's own reason
# (`record_reject_reason`: a later, different reason wins). Mutation-checked by hand on 2026-10-09: recording
# on every rejected member again (the pre-fix selection) turned the four reason tests red; dropping the
# `_rejected_by_bundle` term from the re-offer check turned the replay assertion red. Restored.

_OTHERS = ("x", "bluesky", "facebook", "linkedin")


async def _drop_then_reject_all(svc, tg, *, drop_reason: Optional[str] = None):
    """✂ drop THREADS (and, with `drop_reason`, tap that per-post reason on its own prompt), then ❌ Reject
    all on the rest. Returns (ids, the post decision, its bundle id)."""
    ids, _video, post = await _day_with_decisions(svc, tg)
    bid = _bundle_id(post)
    await rs.handle_update(_tap_on(post, "s", ids["threads"]))
    drop_edit = tg.of("editMessageText")[-1]
    if drop_reason is not None:
        res = await rs.handle_update(_update(ids["threads"], drop_reason,
                                             message={"message_id": 901, "text": "Why was the THREADS post dropped?"}))
        assert res["outcome"] == "recorded"
    after_drop = {**post, "text": drop_edit["text"], "reply_markup": drop_edit["reply_markup"]}
    res = await rs.handle_update(_tap_on(after_drop, "j"))
    assert res["decided"] == 4
    assert {_post(svc, ids[p])["status"] for p in (*_OTHERS, "threads")} == {"rejected"}
    return ids, post, bid


@pytest.mark.asyncio
async def test_a_bundle_reason_after_reject_all_never_overwrites_a_dropped_posts_own_reason(bundles, tg, wakes):
    ids, post, bid = await _drop_then_reject_all(bundles, tg, drop_reason="f")       # Accuracy, on THREADS
    threads_review = copy.deepcopy(_meta(bundles, ids["threads"])["review"])
    assert threads_review["reason"] == "accuracy" and "bundle_id" not in threads_review
    res = await rs.handle_update(_update(bid, "h", message={"message_id": post["message_id"], "text": "t"}))  # Tone
    assert res["outcome"] == "recorded"
    assert _meta(bundles, ids["threads"])["review"] == threads_review                 # A kept, untouched
    for p in _OTHERS:
        review = _meta(bundles, ids[p])["review"]
        assert (review["bundle_id"], review["reason"]) == (bid, "tone")               # B on the others
    assert _answers(tg)[-1] == "Reason saved: Tone"
    last = tg.of("editMessageText")[-1]
    assert last["text"] == "t\n\nReason: Tone — on 4 rejected posts; not on THREADS (rejected separately)"
    assert last["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_a_dropped_post_with_no_reason_is_not_given_the_bundles_and_never_re_offers_it(bundles, tg, wakes):
    ids, post, bid = await _drop_then_reject_all(bundles, tg)                         # THREADS: no reason
    assert tg.of("editMessageText")[-1]["reply_markup"] == rs.bundle_reason_keyboard(bid)   # the others need one
    await rs.handle_update(_update(bid, "u", message={"message_id": post["message_id"], "text": "t"}))
    assert "reason" not in _meta(bundles, ids["threads"])["review"]
    assert {_meta(bundles, ids[p])["review"]["reason"] for p in _OTHERS} == {"weak"}
    assert tg.of("editMessageText")[-1]["text"] == ("t\n\nReason: Weak / boring — on 4 rejected posts; "
                                                    "not on THREADS (rejected separately)")
    # A replayed ❌ re-offers nothing: the only unreasoned post was dropped on its own (its own prompt asks).
    await rs.handle_update(_tap_on(post, "j"))
    assert tg.of("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_a_rejected_member_without_a_review_record_is_left_alone_counted_and_logged(bundles, tg, wakes,
                                                                                           caplog):
    """A `metadata.review` that is not an object (a hand edit) cannot be shown to be this bundle's, so the
    reason is not guessed onto it — the line says so and a WARNING names it. (r3: a member with NO review
    record at all and this bundle's stamp IS this Reject all's — its best-effort record write failed — and
    takes the reason: the tests after `_failing_record_merge` below.)"""
    ids, post, bid = await _drop_then_reject_all(bundles, tg)
    _meta(bundles, ids["linkedin"])["review"] = "hand-edited"
    caplog.set_level(logging.WARNING)
    await rs.handle_update(_update(bid, "h", message={"message_id": post["message_id"], "text": "t"}))
    assert _meta(bundles, ids["linkedin"])["review"] == "hand-edited"
    assert {_meta(bundles, ids[p])["review"]["reason"] for p in ("x", "bluesky", "facebook")} == {"tone"}
    assert tg.of("editMessageText")[-1]["text"] == (
        "t\n\nReason: Tone — on 3 rejected posts; not on THREADS (rejected separately); "
        "not on LINKEDIN (no review record — logged)")
    assert any(r.levelno == logging.WARNING and ids["linkedin"] in r.getMessage()
               and "no review record" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_bundle_reason_when_every_rejected_member_was_dropped_records_nothing(bundles, tg, wakes):
    ids, _video, post = await _day_with_decisions(bundles, tg)
    bid = _bundle_id(post)
    for p in (*_OTHERS, "threads"):
        await rs.handle_update(_tap_on(post, "s", ids[p]))
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    res = await rs.handle_update(_update(bid, "h"))
    assert res["outcome"] == "none_by_bundle" and _rows(bundles, mrs.POSTS) == before
    assert _answers(tg)[-1] == "No reason recorded — no post here was rejected by Reject all"


# ── drop 1 re-review r3 (2026-10-09): a Reject all whose review-record write FAILED keeps its reason ──
#
# `review_bundle` merges `metadata.review` best effort: when that write fails (a PostgREST 520) the member
# is `rejected` with NO review record. Only `review_bundle` (fenced on this bundle's stamp) and `review_post`
# (record in the same UPDATE) write `rejected`, so such a member that carries this bundle's stamp IS this
# Reject all's: the bundle reason lands on it, with `bundle_id` + decision written in the same update so a
# later reason still matches. Before the fix the toast said "no post here was rejected by Reject all" under
# a message saying it rejected all three, nothing was recorded and the keyboard stayed forever.
# Mutation-checked by hand on 2026-10-09 (each alone, originals restored): selecting `mine` by
# `_rejected_by_bundle` alone again, or rebuilding the record without `decision` / `bundle_id`, turned the two
# record-write-failed tests red; the old re-offer predicate turned the all-failed test's replay red;
# `record_reject_reason` adopting any review dict turned the drop guard red; answering "no post here was
# rejected by Reject all" whenever nothing was saved turned the lands-nowhere test red.


def _failing_record_merge(svc, monkeypatch, fail_on: Optional[set] = None) -> Dict[str, bool]:
    """`transition_post` raising for the members in `fail_on` (all when None) while `state["on"]` — during a
    Reject all its only caller is `review_bundle`'s best-effort record merge. Returns the switch."""
    real = svc.transition_post
    state = {"on": True}

    async def flaky(post_id, **kw):
        if state["on"] and (fail_on is None or post_id in fail_on):
            raise mrs.MarketingRunError("transition_post: PostgREST 520 (simulated)")
        return await real(post_id, **kw)

    monkeypatch.setattr(svc, "transition_post", flaky)
    return state


@pytest.mark.asyncio
async def test_a_reason_lands_on_every_member_when_reject_alls_record_write_failed(bundles, tg, wakes, monkeypatch):
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    bid = _bundle_id(video)
    state = _failing_record_merge(bundles, monkeypatch)
    await rs.handle_update(_tap_on(video, "j"))
    state["on"] = False
    for p in VIDEO_PLATFORMS:
        assert _post(bundles, ids[p])["status"] == "rejected" and "review" not in _meta(bundles, ids[p])
    (edit,) = tg.of("editMessageText")
    assert re.search(r"❌ Rejected \d\d:\d\d ET — 3 of 3:", edit["text"])
    assert edit["reply_markup"] == rs.bundle_reason_keyboard(bid)
    # A replayed ❌ re-offers the reasons: the same test as the reason tap's, so the offer is never empty.
    await rs.handle_update(_tap_on(video, "j"))
    assert tg.of("editMessageText")[-1]["reply_markup"] == rs.bundle_reason_keyboard(bid)

    res = await rs.handle_update(_update(bid, "h", message={"message_id": video["message_id"], "text": "t"}))
    assert res["outcome"] == "recorded"
    for p in VIDEO_PLATFORMS:
        review = _meta(bundles, ids[p])["review"]
        assert set(review) == {"decision", "bundle_id", "reason", "reason_at", "reason_by"}
        assert (review["decision"], review["bundle_id"], review["reason"], review["reason_by"]) == \
            ("rejected", bid, "tone", f"telegram:{OWNER}")
    assert _answers(tg)[-1] == "Reason saved: Tone"
    last = tg.of("editMessageText")[-1]
    assert last["text"] == "t\n\nReason: Tone — on 3 rejected posts" and last["reply_markup"] == {"inline_keyboard": []}

    # A later, different reason still matches (the rebuilt record names the bundle)…
    res = await rs.handle_update(_update(bid, "u", message={"message_id": video["message_id"], "text": "t"}))
    assert res["outcome"] == "recorded" and _answers(tg)[-1] == "Reason saved: Weak / boring"
    assert {_meta(bundles, ids[p])["review"]["reason"] for p in VIDEO_PLATFORMS} == {"weak"}
    assert tg.of("editMessageText")[-1]["text"] == "t\n\nReason: Weak / boring — on 3 rejected posts"
    # …and a replayed ❌ offers nothing any more: every member has its reason.
    await rs.handle_update(_tap_on(video, "j"))
    assert tg.of("editMessageText")[-1]["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_a_reason_lands_on_the_one_member_whose_record_write_failed_too(bundles, tg, wakes, monkeypatch):
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    bid = _bundle_id(video)
    state = _failing_record_merge(bundles, monkeypatch, fail_on={ids["youtube"]})
    await rs.handle_update(_tap_on(video, "j"))
    state["on"] = False
    assert "review" not in _meta(bundles, ids["youtube"])
    assert _meta(bundles, ids["tiktok"])["review"]["bundle_id"] == bid
    res = await rs.handle_update(_update(bid, "i", message={"message_id": video["message_id"], "text": "t"}))
    assert res["outcome"] == "recorded" and _answers(tg)[-1] == "Reason saved: Accuracy"
    for p in VIDEO_PLATFORMS:
        review = _meta(bundles, ids[p])["review"]
        assert (review["decision"], review["bundle_id"], review["reason"]) == ("rejected", bid, "accuracy")
    assert tg.of("editMessageText")[-1]["text"] == "t\n\nReason: Accuracy — on 3 rejected posts"


@pytest.mark.asyncio
async def test_a_bundle_reason_is_never_rebuilt_onto_a_dropped_post_or_another_bundles(bundles, tg, wakes):
    """The adoption is narrow: `record_reject_reason(bundle_id=…)` re-checks the FRESH row — a review record
    without this `bundle_id` (a ✂ drop), or no record on a row stamped with ANOTHER bundle, writes nothing."""
    ids, _post_decision, bid = await _drop_then_reject_all(bundles, tg)   # THREADS dropped: record, no bundle_id
    threads_before = copy.deepcopy(_post(bundles, ids["threads"]))
    assert await bundles.record_reject_reason(ids["threads"], "tone", by="telegram:1", bundle_id=bid) == \
        "not_this_bundle"
    assert _post(bundles, ids["threads"]) == threads_before
    _meta(bundles, ids["x"]).pop("review")                                 # no record, stamped with `bid`
    x_before = copy.deepcopy(_post(bundles, ids["x"]))
    assert await bundles.record_reject_reason(ids["x"], "tone", by="telegram:1",
                                              bundle_id=str(uuid.uuid4())) == "not_this_bundle"
    assert _post(bundles, ids["x"]) == x_before


@pytest.mark.asyncio
async def test_a_bundle_reason_that_lands_nowhere_says_so_truthfully_and_closes_the_keyboard(bundles, tg, wakes):
    """Every member Reject all rejected carries a hand-edited (non-object) review: nothing is guessed, and
    the toast never says "no post here was rejected by Reject all" — the line names each one instead."""
    ids, video, _post_decision = await _day_with_decisions(bundles, tg)
    bid = _bundle_id(video)
    await rs.handle_update(_tap_on(video, "j"))
    for p in VIDEO_PLATFORMS:
        _meta(bundles, ids[p])["review"] = "hand-edited"
    before = copy.deepcopy(_rows(bundles, mrs.POSTS))
    res = await rs.handle_update(_update(bid, "h", message={"message_id": video["message_id"], "text": "t"}))
    assert res["outcome"] == "not_recorded" and _rows(bundles, mrs.POSTS) == before
    assert _answers(tg)[-1] == "No reason recorded — see the message"
    last = tg.of("editMessageText")[-1]
    assert last["text"] == ("t\n\nReason: Tone — not recorded; not on TIKTOK, YOUTUBE, INSTAGRAM "
                            "(no review record — logged)")
    assert last["reply_markup"] == {"inline_keyboard": []}


# ── drop 2 (contract D13): the series note on the decision message ────────────


def test_the_decision_text_names_the_series_on_its_own_line_and_a_lesson_day_adds_none(configured):
    members = [_row("x", "text")]
    plain = rs.compose_bundle_decision_text("post", members, [], "2026-11-16", False, [])
    assert rs.compose_bundle_decision_text("post", members, [], "2026-11-16", False, [], series_note="") == plain
    noted = rs.compose_bundle_decision_text("post", members, [], "2026-11-16", False, [],
                                            series_note="Money Map (fell back from CEO Buys: nothing qualified)")
    assert noted.split("\n") == ["DECISION · POSTS · run 2026-11-16",
                                 "Series: Money Map (fell back from CEO Buys: nothing qualified)",
                                 *plain.split("\n")[1:]]


@pytest.mark.asyncio
async def test_both_decision_messages_say_which_series_the_day_posted(bundles, tg):
    """The run's server-owned `series` / `series_trail` (the selection mirror) reach the owner as operator
    labels — never the stored strings themselves. The note's WORDING is `digest_service._series_note`'s and
    is pinned by the digest's own tests (since OBS-1, 2026-10-15, it names every fallback step, an
    infrastructure outcome first — so this test no longer copies the wording): here, the decision message
    carries exactly that note, on its own line."""
    from app.services.marketing import digest_service

    ids = _seed_day(bundles)
    run = next(r for r in _rows(bundles, mrs.RUNS) if r["id"] == RUN_A)
    run["metadata"]["series"] = "money_map"
    run["metadata"]["series_trail"] = [
        {"series": "ceo_buys", "outcome": "no_candidates", "reason": "<b>raw</b>"},
        {"series": "insider_buys", "outcome": "unavailable", "reason": "insider_feed_empty"},
        {"series": "money_map", "outcome": "chosen"}]
    note = digest_service._series_note(run)
    assert note.startswith("Money Map (fell back from ") and "CEO Buys: nothing qualified" in note
    assert (await rs.review_cycle())["bundles"] == 2
    for decision in _decision_messages(tg):
        lines = decision["text"].split("\n")
        assert lines[1] == f"Series: {note}", lines
        assert "<b>" not in decision["text"] and "insider_feed_empty" not in decision["text"]
    assert all(_meta(bundles, pid)["review_bundle"] for pid in ids.values())


@pytest.mark.asyncio
async def test_an_unknown_series_and_a_broken_note_never_hold_the_bundle(bundles, tg, monkeypatch, caplog):
    _seed_day(bundles)
    run = next(r for r in _rows(bundles, mrs.RUNS) if r["id"] == RUN_A)
    run["metadata"]["series"] = "<b>not a series</b>"
    assert (await rs.review_cycle())["bundles"] == 2
    assert all(d["text"].split("\n")[1].startswith("Approve all sends:") for d in _decision_messages(tg))

    from app.services.marketing import digest_service

    def boom(_run):
        raise RuntimeError("note exploded")

    monkeypatch.setattr(digest_service, "_series_note", boom)
    with caplog.at_level(logging.WARNING, logger=rs.logger.name):
        assert rs._run_series_note({"metadata": {"series": "money_map"}}, RUN_A) == ""
    assert "no series note for run_id=" in caplog.text and "note exploded" in caplog.text


# ── 2026-10-15 (plan Batch 1 items 7 + 11): the decision message names the picture fallback and the ───
# first comments, from what create_posts stamped on the members ─────────────────────────────────────────
#
# Mutation-checked by hand on 2026-10-15 (and again by the resumed build): echoing the raw `image_fallback`
# value instead of the generic words, dropping the TEXT-only rule, replacing the sender's rule
# (`first_comment_of`) with a local "any non-blank stamp", narrowing the notes' safety net so a bug
# propagates, omitting the notes, and naming the previews in them each turned tests here red; restored.

#: The one comment text the outlet sends (`outlet_upload_post.FIRST_COMMENT_TEXTS`), as create_posts stamps it.
_COMMENT = SEARCH_LINE


def _stamped(row: Dict[str, Any], **meta) -> Dict[str, Any]:
    row["metadata"] = {**row["metadata"], **meta}
    return row


def _fallback(row: Dict[str, Any], code: Any) -> Dict[str, Any]:
    return _stamped(row, **{POST_METADATA_IMAGE_FALLBACK: code})


def _commented(row: Dict[str, Any], text: Any = _COMMENT) -> Dict[str, Any]:
    return _stamped(row, **{POST_METADATA_FIRST_COMMENT: text})


def test_the_contract_keys_are_the_ones_create_posts_stamps():
    """The decision lines read exactly the shared contract's keys (schemas/marketing.py), and the comment
    create_posts stamps is one the outlet sends."""
    assert POST_METADATA_IMAGE_FALLBACK == "image_fallback" and POST_METADATA_FIRST_COMMENT == "first_comment"
    assert "x" not in FIRST_COMMENT_PLATFORMS and "bluesky" not in FIRST_COMMENT_PLATFORMS
    assert _COMMENT in outlet_upload_post.FIRST_COMMENT_TEXTS


def test_the_decision_text_names_each_picture_fallback_in_owner_words_one_line_per_reason(configured):
    members = [_row("x", "text"),
               _fallback(_row("bluesky", "text"), "image_dropped"),
               _fallback(_row("threads", "text"), "threads_caption_long"),
               _fallback(_row("facebook", "text"), "image_dropped"),
               _row("linkedin", "image")]
    text = rs.compose_bundle_decision_text("post", members, [], "2026-10-15", False, [])
    assert text.split("\n") == [
        "DECISION · POSTS · run 2026-10-15",
        "Approve all sends: X (text), BLUESKY (text), THREADS (text), FACEBOOK (text), LINKEDIN (image)",
        f"Picture not used today: {IMAGE_FALLBACK_REASONS['image_dropped']} (BLUESKY, FACEBOOK)",
        f"Picture not used today: {IMAGE_FALLBACK_REASONS['threads_caption_long']} (THREADS)",
        "✂ drop = reject that one platform and keep the rest (optional).",
    ]


@pytest.mark.parametrize("code", sorted(IMAGE_FALLBACK_REASONS))
def test_every_fallback_code_reads_as_its_owner_words_never_the_code(configured, code):
    text = rs.compose_bundle_decision_text("post", [_fallback(_row("threads", "text"), code)], [], "d", False, [])
    assert f"Picture not used today: {IMAGE_FALLBACK_REASONS[code]} (THREADS)" in text.split("\n")
    assert code not in text


@pytest.mark.parametrize("value", ["image_droped", "IMAGE_DROPPED", "<b>raw</b>", "", "   ", 7, 0, True, False,
                                   1.5, ["image_dropped"], {"code": "image_dropped"}])
def test_an_unknown_fallback_value_is_named_generically_logged_and_never_echoed(configured, caplog, value):
    row = _fallback(_row("bluesky", "text"), value)
    caplog.set_level(logging.WARNING)
    text = rs.compose_bundle_decision_text("post", [row], [], "2026-10-15", False, [])
    assert text.split("\n") == ["DECISION · POSTS · run 2026-10-15", "Approve all sends: BLUESKY (text)",
                                f"Picture not used today: {rs.IMAGE_FALLBACK_UNKNOWN_WORDS} (BLUESKY)"]
    assert "<b>" not in text and "droped" not in text and "IMAGE_DROPPED" not in text
    assert any(f"post_id={row['id']}" in r.getMessage() and "not a code of IMAGE_FALLBACK_REASONS" in r.getMessage()
               for r in caplog.records)


def test_an_unknown_fallback_value_is_capped_in_the_log(configured, caplog):
    row = _fallback(_row("bluesky", "text"), "z" * 5000)
    caplog.set_level(logging.WARNING)
    rs.compose_bundle_decision_text("post", [row], [], "d", False, [])
    (record,) = [r for r in caplog.records if "IMAGE_FALLBACK_REASONS" in r.getMessage()]
    assert "z" * 100 not in record.getMessage()


@pytest.mark.parametrize("metadata", [{"dry_run": False}, {"dry_run": False, "image_fallback": None}, None, "junk",
                                      [], 7])
def test_no_fallback_stamp_means_no_picture_line(configured, metadata):
    row = _row("threads", "text")
    row["metadata"] = metadata
    text = rs.compose_bundle_decision_text("post", [row], [], "d", False, [])
    assert "Picture not used" not in text


@pytest.mark.parametrize("fmt", ["image", "video", "<b>text</b>", None])
def test_only_a_text_post_can_say_its_picture_was_not_used(configured, caplog, fmt):
    """The stamp contradicts a post that carries the picture (or a video, or a format the ledger does not
    know): the message must never say "Picture not used" next to "BLUESKY (image)". Logged at WARNING."""
    row = _fallback(_row("bluesky", fmt), "image_dropped")
    caplog.set_level(logging.WARNING)
    text = rs.compose_bundle_decision_text("post", [row], [], "d", False, [])
    assert "Picture not used" not in text
    assert any(f"post_id={row['id']}" in r.getMessage() and "does not call its picture unused" in r.getMessage()
               for r in caplog.records)


def test_a_previews_fallback_is_not_named_it_is_never_sent(configured):
    preview = _fallback(_row("threads", "text"), "threads_caption_long")
    text = rs.compose_bundle_decision_text("post", [_row("x", "text")], [preview], "d", False, [])
    assert "Picture not used" not in text and "Not sent: THREADS (preview — not wired yet)" in text


def test_the_decision_text_names_the_first_comment_platforms_on_either_bundle(configured):
    """"First comment REQUESTED on: …" (review CTA-2, 2026-10-16): the line is the stamp create_posts wrote,
    never an outcome — an async Upload-Post upload reports none, so the decision never says one was posted."""
    video = [_commented(_row("tiktok", "video")), _commented(_row("youtube", "video")), _row("instagram", "video")]
    text = rs.compose_bundle_decision_text("video", video, [], "2026-10-15", False, [])
    assert text.split("\n") == ["DECISION · VIDEO · run 2026-10-15",
                                "Approve all sends: TIKTOK (video), YOUTUBE (video), INSTAGRAM (video)",
                                "First comment requested on: TIKTOK, YOUTUBE",
                                "✂ drop = reject that one platform and keep the rest (optional)."]
    posts = [_row("x", "text"), _commented(_row("facebook", "image")), _commented(_row("linkedin", "image"))]
    text = rs.compose_bundle_decision_text("post", posts, [], "2026-10-15", False, [])
    assert "First comment requested on: FACEBOOK, LINKEDIN" in text.split("\n")


@pytest.mark.parametrize("value", [None, "", "   \n", 7, True, ["Search Caydex"], {"text": "Search Caydex"},
                                   SEARCH_LINE + " ", SEARCH_LINE.upper(), "Download Caydex now — free!"])
def test_only_the_code_owned_comment_text_counts(configured, value):
    """Anything but the exact code-owned text is sent WITHOUT a comment by the outlet — so it is never
    named either (a hand-edited stamp, a near miss, a non-string)."""
    row = _row("youtube", "video")
    if value is not None:
        _commented(row, value)
    assert "First comment" not in rs.compose_bundle_decision_text("video", [row], [], "d", False, [])


@pytest.mark.parametrize("platform", ["x", "bluesky", "<b>myspace</b>"])
def test_a_platform_that_never_sends_a_first_comment_is_never_named(configured, caplog, platform):
    """X and Bluesky publish directly and never send one: the message must not promise it (the outlet's
    rule logs the misplaced stamp)."""
    row = _commented(_row(platform, "text"))
    caplog.set_level(logging.WARNING)
    text = rs.compose_bundle_decision_text("post", [row, _commented(_row("threads", "text"))], [], "d", False, [])
    assert "First comment requested on: THREADS" in text.split("\n")
    assert any(row["id"] in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("platform", list(POST_PLATFORMS) + ["<b>myspace</b>"])
@pytest.mark.parametrize("value", [SEARCH_LINE, "other words", "", None, 7])
def test_the_first_comment_line_names_exactly_what_the_outlet_will_send(configured, platform, value):
    """Parity with the SENDER: a platform is named iff `outlet_upload_post.first_comment_of` — the function
    that builds the `<platform>_first_comment` field — would send its comment."""
    row = _row(platform, "video" if platform in VIDEO_PLATFORMS else "text")
    if value is not None:
        _commented(row, value)
    named = "First comment requested on: " in rs.compose_bundle_decision_text("post", [row], [], "d", False, [])
    assert named is (outlet_upload_post.first_comment_of(platform, row) is not None)
    assert named is (value == SEARCH_LINE and platform in FIRST_COMMENT_PLATFORMS)


def test_a_previews_first_comment_is_not_named(configured):
    text = rs.compose_bundle_decision_text("post", [_row("x", "text")], [_commented(_row("threads", "text"))], "d",
                                           False, [])
    assert "First comment" not in text


def test_both_notes_sit_after_what_is_sent_and_before_the_warnings(configured, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_DRY_RUN", True)
    members = [_commented(_fallback(_row("threads", "text"), "worker_capability")),
               _commented(_row("facebook", "image"))]
    text = rs.compose_bundle_decision_text("post", members, [_row("linkedin", "image")], "2026-10-15", False,
                                           ["⚠️ media note"], series_note="Money Map")
    assert text.split("\n") == [
        "DECISION · POSTS · run 2026-10-15",
        "Series: Money Map",
        "Approve all sends: THREADS (text), FACEBOOK (image)",
        "Not sent: LINKEDIN (preview — not wired yet)",
        f"Picture not used today: {IMAGE_FALLBACK_REASONS['worker_capability']} (THREADS)",
        "First comment requested on: THREADS, FACEBOOK",
        "⚠️ the web is in DRY RUN: approving will not send THREADS, FACEBOOK",
        "⚠️ media note",
        "✂ drop = reject that one platform and keep the rest (optional).",
    ]


def test_no_stamps_leaves_the_decision_text_byte_for_byte_as_before(configured):
    members = [_row("x", "text"), _row("bluesky", "image")]
    assert rs.bundle_extras_lines(members) == []
    assert rs.compose_bundle_decision_text("post", members, [], "2026-09-28", False, []).split("\n") == [
        "DECISION · POSTS · run 2026-09-28", "Approve all sends: X (text), BLUESKY (image)",
        "✂ drop = reject that one platform and keep the rest (optional)."]


def test_the_decision_text_stays_within_telegrams_4096_with_every_note(configured):
    members = [_commented(_fallback(_row("y" * 300, "text"), f"unknown-{i}")) for i in range(30)]
    members += [_commented(_fallback(_row(p, "text"), "image_dropped")) for p in FIRST_COMMENT_PLATFORMS]
    text = rs.compose_bundle_decision_text("post", members, [], "2026-10-15", False, ["⚠️ " + "w" * 3000])
    assert rs.utf16_len(text) <= 4096
    assert text.startswith("DECISION · POSTS · run 2026-10-15\nApprove all sends: ")


@pytest.mark.asyncio
async def test_the_sweeps_decision_messages_carry_the_stamps_create_posts_wrote(bundles, tg):
    """End to end through the sweep: the members the decision text reads are the re-read, bundle-stamped
    rows, so what create_posts stamped (and only that) reaches each decision message."""
    ids = _seed_day(bundles)
    _post(bundles, ids["threads"]).update({"format": "text", "asset_ids": []})
    _fallback(_post(bundles, ids["threads"]), "threads_caption_long")
    for p in ("youtube", "instagram", "tiktok", "facebook", "linkedin", "threads"):
        _commented(_post(bundles, ids[p]))
    assert (await rs.review_cycle())["bundles"] == 2
    video_decision, post_decision = _decision_messages(tg)
    assert "First comment requested on: TIKTOK, YOUTUBE, INSTAGRAM" in video_decision["text"].split("\n")
    assert "Picture not used" not in video_decision["text"]
    lines = post_decision["text"].split("\n")
    assert f"Picture not used today: {IMAGE_FALLBACK_REASONS['threads_caption_long']} (THREADS)" in lines
    assert "First comment requested on: THREADS, FACEBOOK, LINKEDIN" in lines
    # the stamps survive the bundle and notified stamps (merging writes)
    assert _meta(bundles, ids["threads"])[POST_METADATA_IMAGE_FALLBACK] == "threads_caption_long"
    assert _meta(bundles, ids["facebook"])[POST_METADATA_FIRST_COMMENT] == _COMMENT


def _note_bug(*_args, **_kwargs):
    raise RuntimeError("note exploded")


@pytest.mark.parametrize("broken", ["first_comment_of", "_image_fallback_words"])
def test_a_bug_in_the_notes_costs_only_the_notes_never_the_decision_text(configured, monkeypatch, caplog, broken):
    """The notes are best effort, like the series note: a raise inside them — here, or in the sender's rule
    they read from another module (`first_comment_of`) — leaves the rest of the decision message as it would
    be, with ONE ⚠️ line in the notes' place (never a half-built list), logged at ERROR with the stack."""
    monkeypatch.setattr(rs, broken, _note_bug)
    members = [_commented(_fallback(_row("threads", "text"), "image_dropped")), _row("x", "text")]
    caplog.set_level(logging.ERROR)
    text = rs.compose_bundle_decision_text("post", members, [], "2026-10-15", False, ["⚠️ media note"])
    assert text.split("\n") == [
        "DECISION · POSTS · run 2026-10-15",
        "Approve all sends: THREADS (text), X (text)",
        rs.BUNDLE_EXTRAS_UNAVAILABLE_LINE,
        "⚠️ media note",
        "✂ drop = reject that one platform and keep the rest (optional).",
    ]
    (record,) = [r for r in caplog.records if "decision notes" in r.getMessage()]
    assert record.levelno == logging.ERROR and record.exc_info is not None
    assert members[0]["id"] in record.getMessage() and "RuntimeError: note exploded" in record.getMessage()


@pytest.mark.asyncio
async def test_a_bug_in_the_notes_never_holds_the_bundle_nor_resends_it(bundles, tg, monkeypatch, caplog):
    """End to end: the decision is composed AFTER the media and captions went out, so a raise there used to
    fail the bundle, which the next cycle re-sent in full — never asking. With the notes broken, both
    decisions still go out (each with the ⚠️ line), every member is stamped notified, and the next cycle
    sends nothing."""
    ids = _seed_day(bundles)
    for p in ("youtube", "facebook"):
        _commented(_post(bundles, ids[p]))
    monkeypatch.setattr(rs, "first_comment_of", _note_bug)
    caplog.set_level(logging.ERROR)
    counters = await rs.review_cycle()
    assert (counters["bundles"], counters["sent"], counters["stamped"], counters["failed"]) == (2, 8, 8, 0)
    decisions = _decision_messages(tg)
    assert len(decisions) == 2
    assert all(rs.BUNDLE_EXTRAS_UNAVAILABLE_LINE in d["text"].split("\n") for d in decisions)
    assert all(_meta(bundles, pid)["review_notified_at"] for pid in ids.values())
    assert sum("decision notes" in r.getMessage() for r in caplog.records) == 2
    tg.calls.clear()
    assert (await rs.review_cycle())["pending"] == 0 and tg.calls == []


# ── 2026-10-15 (plan Batch 1 item 9): Quiet Telegram — only the decision rings ────────────────────────
#
# Mutation-checked by hand on 2026-10-15: sending the decision message with disable_notification=True,
# and dropping the flag from the caption messages, each turned a test here red; restored.


def _silent(payload: Dict[str, Any]) -> bool:
    """Is this payload sent silently? Only a JSON `true` counts; when the flag is not asked for it is absent."""
    if "disable_notification" in payload:
        assert payload["disable_notification"] is True, payload
        return True
    return False


@pytest.mark.asyncio
async def test_a_bundle_day_rings_only_for_its_two_decisions(bundles, tg):
    _seed_day(bundles)
    assert (await rs.review_cycle())["bundles"] == 2
    decisions = _decision_messages(tg)
    assert len(decisions) == 2 and not any(_silent(d) for d in decisions)
    loud = [(m, p) for m, p in tg.calls if not _silent(p)]
    assert [m for m, _p in loud] == ["sendMessage", "sendMessage"]
    assert all(rs.parse_callback_data(p["reply_markup"]["inline_keyboard"][0][0]["callback_data"])[0]
               == "bundle_approve" for _m, p in loud)
    # everything else — the video, the image card and every caption message — went out silently
    assert _silent(tg.of("sendVideo")[0]) and _silent(tg.of("sendPhoto")[0])
    captions = [p for m, p in tg.calls if m == "sendMessage" and "reply_markup" not in p]
    assert len(captions) == 5 and all(_silent(p) for p in captions)
    assert all(p["text"].startswith("CAPTION · ") for p in captions)


@pytest.mark.asyncio
async def test_a_preview_only_bundle_rings_for_nothing(bundles, tg, monkeypatch):
    """Previews carry no decision, so nothing about them rings."""
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [p for p in POST_PLATFORMS if p != "threads"])
    _seed_run(bundles, video_id=None)
    _seed_post(bundles, RUN_A, "threads", "text", caption=SHORT, title=None)
    counters = await rs.review_cycle()
    assert counters["bundles"] == 0 and counters["sent"] == 1
    (call,) = tg.calls
    assert call[0] == "sendMessage" and _silent(call[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [(400, {"ok": False, "description": "Bad Request: failed to get HTTP URL content"}),
                                    (502, b"")])
async def test_an_image_link_fallback_is_silent_like_the_image(bundles, tg, answer):
    _seed_day(bundles)
    tg.script["sendPhoto"] = [answer]
    assert (await rs.review_cycle())["bundles"] == 2
    assert _silent(tg.of("sendPhoto")[0])
    link = next(m for m in tg.of("sendMessage") if m["text"].startswith("IMAGE · "))
    assert _silent(link)
    assert not any(_silent(d) for d in _decision_messages(tg))


@pytest.mark.asyncio
async def test_an_oversized_bundle_video_becomes_a_silent_link(bundles, tg):
    _seed_day(bundles)
    next(a for a in _rows(bundles, mrs.ASSETS) if a["id"] == VIDEO)["bytes"] = 25 * 1024 * 1024
    assert (await rs.review_cycle())["bundles"] == 2
    assert tg.of("sendVideo") == []
    link = next(m for m in tg.of("sendMessage") if m["text"].startswith("VIDEO · "))
    assert "MB — open the link" in link["text"] and _silent(link)


# ── 2026-10-15 (plan Batch 1, integration): the cross-stream contract end to end ──────────────────────
#
# Every other test of the two notes starts from a hand-stamped row (`_fallback` / `_commented`). This one
# starts where production does — the write-time freeze — and ends on the wire, so a drift between the four
# streams that each own one hop (script_service, run_service, review_service, outlet_upload_post) fails here
# even while each stream's own tests stay green.
#
# Mutation-checked by hand on 2026-10-15 (each turned this test red; restored): `create_posts` reading the
# first-comment platforms from a constant instead of the setting; the freeze not recording Threads'
# fallback; the decision notes dropped from the POSTS message; the outlet sending the generic
# `first_comment` field instead of `<platform>_first_comment`.

#: Listed for the automatic first comment in this test: one video, one picture and one text platform (the
#: text one is ALSO the day's picture fallback); every other platform must get none.
_E2E_COMMENTED = ("youtube", "facebook", "threads")
#: Every platform a posting day publishes to (the format map's, not POST_PLATFORMS' wider list).
_E2E_PLATFORMS = VIDEO_PLATFORMS + POST_PLATFORMS_TEXT


@pytest.mark.asyncio
async def test_e2e_the_frozen_fallback_and_the_first_comments_travel_from_the_freeze_to_the_wire(
        bundles, tg, wakes, monkeypatch):
    """REAL pieces, no hand-written stamp: `script_service.freeze_post_formats` freezes Threads as TEXT
    (its caption is over 500 UTF-8 bytes: `image_fallback` = threads_caption_long) while Bluesky,
    Facebook and LinkedIn post the picture → the accepted output as stored → `run_service.create_posts`
    stamps that code on the Threads text post and the code-owned search line on the platforms
    MARKETING_FIRST_COMMENT_PLATFORMS lists (read at call time; the output's store state is live) → the
    sweep's VIDEO and POSTS decision messages each name exactly their members' notes, in owner words →
    after Approve all on both, `outlet_upload_post` puts `<platform>_first_comment` on the video, photo and
    text requests it sends for exactly those platforms, and on no other platform's request."""
    from datetime import date

    from app.integrations import upload_post
    from app.services.marketing import outlet_base, publisher_service, script_service
    from test_marketing_outlet_upload_post import IMG, IMG_SHA, UpServer, _ack, _parts

    svc = bundles
    day = date(2026, 9, 17)
    for name, value in (("MARKETING_RUN_STALE_SECONDS", 3600), ("MARKETING_MAX_RUN_ATTEMPTS", 6),
                        ("MARKETING_AUTO_PUBLISH", False),
                        ("MARKETING_IMAGE_PLATFORMS", "bluesky,facebook,linkedin,threads"),
                        ("MARKETING_FIRST_COMMENT_PLATFORMS", ",".join(_E2E_COMMENTED)),
                        ("SUPABASE_URL", "https://xyz.supabase.co"), ("MARKETING_MEDIA_BUCKET", "marketing-media"),
                        ("MARKETING_UPLOAD_POST_API_KEY", "UPkeyE2E0123456789abcdefghijKLMNOPqrs"),
                        ("MARKETING_UPLOAD_POST_USER", "caydex"),
                        ("MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID", "104857600123456"),
                        ("MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID", "86753090")):
        monkeypatch.setattr(settings, name, value)

    # 1. The write-time freeze of the accepted package (what `_frozen_output` stores).
    run, _ = await svc.claim_run(day, worker_version="t", dry_run=False, now=fake_db.NOW, claim_nonce=fake_db._n())
    rid = run["id"]
    long_threads = ("Save — invest — wait — repeat. " * 14).strip()
    assert len(long_threads) <= 500 < len(long_threads.encode("utf-8"))    # the text route takes it; a photo does not
    package = {"posts": {p: fake_db._server_copy(p) for p in _E2E_PLATFORMS}, "judge": {"mode": "enforce"},
               "cards": [dict(fake_db._CARD)], "disclaimer_card": fake_db._DISCLAIMER_CARD,
               "image_post": copy.deepcopy(fake_db._IMAGE_POST), "store_state": "live"}
    package["posts"]["threads"]["caption"] = long_threads
    output = script_service.freeze_post_formats(package, day, image_posts=True, x_images=False, run_id=rid,
                                                image_platforms=script_service.image_platforms_setting())
    assert output["post_formats"] == {"bluesky": "image", "facebook": "image", "linkedin": "image", "threads": "text",
                                      "x": "text", "tiktok": "video", "youtube": "video", "instagram": "video"}
    assert output[script_service.IMAGE_FALLBACK_KEY] == {"reason": "threads_caption_long", "platforms": ["threads"]}
    _script, ours = await svc.insert_script({"run_id": rid, "status": "accepted", "source_ref": "money_moves:test-item",
                                             "template_id": "checklist", "generation_id": str(uuid.uuid4()),
                                             "output": output})
    assert ours

    # 2. The worker's media and its create_posts call; the server decides both notes.
    video = await fake_db._ready_asset(svc, rid)
    await fake_db._point_video(svc, rid, video["id"])
    image = await fake_db._post_image(svc, rid, IMG_SHA, size=len(IMG))
    media = {"video": [video["id"]], "image": [image["id"]], "text": []}
    specs = [{"platform": p, "format": f, **({"asset_ids": media[f]} if media[f] else {})}
             for p, f in output["post_formats"].items()]
    created = await svc.create_posts(rid, specs, claim=fake_db._holder(svc, rid))
    notes = {p["platform"]: p["metadata"] for p in created}
    assert {p: m.get(POST_METADATA_FIRST_COMMENT) for p, m in notes.items()} == {
        p: (SEARCH_LINE if p in _E2E_COMMENTED else None) for p in _E2E_PLATFORMS}
    assert {p: m.get(POST_METADATA_IMAGE_FALLBACK) for p, m in notes.items()} == {
        p: ("threads_caption_long" if p == "threads" else None) for p in _E2E_PLATFORMS}
    next(r for r in _rows(svc, mrs.RUNS) if r["id"] == rid).update({"status": "media_ready", "stage": "assets_ready"})

    # 3. The sweep: two decisions, each naming exactly its own members' notes.
    counters = await rs.review_cycle()
    assert counters["bundles"] == 2 and counters["failed"] == 0
    video_decision, post_decision = _decision_messages(tg)

    def _named(decision: Dict[str, Any], label: str) -> List[str]:
        return [line[len(label):] for line in decision["text"].split("\n") if line.startswith(label)]

    assert video_decision["text"].startswith("DECISION · VIDEO · run 2026-09-17\n")
    assert _named(video_decision, "First comment requested on: ") == ["YOUTUBE"]
    assert _named(video_decision, "Picture not used today: ") == []
    assert post_decision["text"].startswith("DECISION · POSTS · run 2026-09-17\n")
    assert [sorted(names.split(", ")) for names in _named(post_decision, "First comment requested on: ")] == [
        ["FACEBOOK", "THREADS"]]
    assert _named(post_decision, "Picture not used today: ") == [
        f"{IMAGE_FALLBACK_REASONS['threads_caption_long']} (THREADS)"]
    assert "threads_caption_long" not in post_decision["text"]           # the owner's words, never the code

    # 4. Approve all on both decisions, then each Upload-Post outlet prepares and sends the approved row.
    for decision in (video_decision, post_decision):
        assert (await rs.handle_update(_tap_on(decision, "g")))["outcome"] == "decided"
    rows = {r["platform"]: r for r in _rows(svc, mrs.POSTS) if r["run_id"] == rid}
    assert {r["status"] for r in rows.values()} == {"approved"}
    up = UpServer()
    monkeypatch.setattr(upload_post, "_client", httpx.AsyncClient(transport=httpx.MockTransport(up.handler)))
    card_url = svc.public_url(str(image["storage_path"]))
    monkeypatch.setattr(outlet_base, "_fetch_transport", httpx.MockTransport(
        lambda request: httpx.Response(200, content=IMG) if str(request.url) == card_url else httpx.Response(404)))
    monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: svc)   # `_video_url` / `load_post_image`
    wire: Dict[str, Dict[str, str]] = {}
    for platform in outlet_upload_post.PLATFORMS:
        row, problem = await publisher_service._with_image(rows[platform])
        assert problem is None, problem
        adapter = outlet_upload_post.UploadPostAdapter(platform)
        prepared = adapter.prepare(row)
        fields = prepared.payload["fields"]
        assert fields.get(f"{platform}_first_comment") == (SEARCH_LINE if platform in _E2E_COMMENTED else None)
        assert "first_comment" not in fields                              # never the generic field
        if platform not in _E2E_COMMENTED:
            continue
        route = {"video": "/upload", "photo": "/upload_photos", "text": "/upload_text"}[prepared.payload["kind"]]
        up.answer(route, _ack(prepared.payload["request_id"]))
        sent = {k: v for k, v in row.items() if k != outlet_base.POST_IMAGE_KEY}
        outcome = await adapter.send({**sent, "status": "queued", "attempts": 1}, prepared)
        assert outcome.kind == outlet_base.SUBMITTED, outcome
        (request,) = up.calls(route)
        wire[platform] = dict(_parts(request))
    assert {p: parts.get(f"{p}_first_comment") for p, parts in wire.items()} == {p: SEARCH_LINE for p in _E2E_COMMENTED}
    assert sorted(p for p, parts in wire.items() for name in parts if name.endswith("first_comment")) == sorted(
        _E2E_COMMENTED)                                                    # one comment field per request, its own
    assert up.unexpected == []


# ── 2026-10-16 (Batch 1 review F2): the DRY RUN label is the POSTS', never the run row's ─────────────────
#
# A re-claim rewrites the run row's `dry_run` (the claiming worker's switch); every post keeps the flag
# create_posts stamped on it, and the publisher reads only that one (`dry = MARKETING_DRY_RUN or
# is_rehearsal(post)`). So the label is `dry_run_label(members)` — every member a rehearsal row — and when the
# run's flag disagrees, or the members do, `dry_run_lines` says so under the decision's header.
#
# Mutation-checked by hand on 2026-10-16 (each turned tests here red; restored): the bundle label read from the
# run row again (`bool(run["dry_run"]) or any(...)`); `all` → `any` in `dry_run_label`; the lines left out of
# the decision message; "approving publishes" promised while the web holds a member back; any truthy run flag
# counted as "marked dry-run"; the lines placed after "Approve all sends"; the per-post video's label read from
# the run row again (test_marketing_review_bot.py).
# Re-verified the same day by a second pass, IN MEMORY (the mutated source exec'd into the imported module, the
# file on disk never touched; a re-exec of the unchanged source as the control stayed all green): all of the
# above, plus the label read from members AND previews (`dry_run_label(everyone)` — it survived every test then;
# `test_a_live_preview_never_strips_…` closes it), from the previews first, the lines read from members and
# previews, the reverse line dropped, and an unreadable run flag read as "now marked live". The last test here
# drives the finding's own sequence through the REAL `claim_run` re-claim, not a hand-set run flag.

_P3 = ("x", "bluesky", "threads")


def _flag(platform: str, flag: Any, fmt: str = "text") -> Dict[str, Any]:
    """A member whose `metadata.dry_run` is `flag`."""
    return _row(platform, fmt, metadata={"dry_run": flag})


@pytest.mark.parametrize("run_flag, flags, label, lines", [
    # run row and posts agree: the label alone says it, no line
    (True, (True, True), True, []),
    (False, (False, False), False, []),
    (None, (False,), False, []),
    (None, (True,), True, []),
    # F2: the run row says dry run over LIVE posts
    (True, (False, False), False, ["⚠️ The run is now marked dry-run, but these posts were recorded LIVE (not a "
                                   "rehearsal): approving publishes them."]),
    (True, (False,), False, ["⚠️ The run is now marked dry-run, but this post was recorded LIVE (not a rehearsal): "
                             "approving publishes it."]),
    # the reverse re-claim
    (False, (True, True), True, ["⚠️ The run is now marked live, but these posts were recorded as a DRY RUN: "
                                 "approving will not send them."]),
    (False, (True,), True, ["⚠️ The run is now marked live, but this post was recorded as a DRY RUN: approving "
                            "will not send it."]),
    # a mixed set: never labelled; its rehearsal rows are named (and its live ones when the run says dry run)
    (None, (False, True), False, ["⚠️ BLUESKY was recorded as a DRY RUN: approving will not send it."]),
    (False, (False, True, False), False, ["⚠️ The run is now marked live, but BLUESKY was recorded as a DRY RUN: "
                                          "approving will not send it."]),
    (True, (False, True, True), False, [
        "⚠️ The run is now marked dry-run, but X was recorded LIVE (not a rehearsal): approving publishes it.",
        "⚠️ BLUESKY, THREADS were recorded as a DRY RUN: approving will not send them."]),
    (True, (False, False, True), False, [
        "⚠️ The run is now marked dry-run, but X, BLUESKY were recorded LIVE (not a rehearsal): approving "
        "publishes them.",
        "⚠️ THREADS was recorded as a DRY RUN: approving will not send it."]),
])
def test_the_label_and_the_lines_for_every_run_and_post_combination(configured, run_flag, flags, label, lines):
    members = [_flag(p, f) for p, f in zip(_P3, flags)]
    assert rs.dry_run_label(members) is label
    assert rs.dry_run_lines({"dry_run": run_flag}, members) == lines


@pytest.mark.parametrize("run", [{"dry_run": "true"}, {"dry_run": 1}, {"dry_run": "false"}, {"dry_run": 0},
                                 {"dry_run": None}, {"dry_run": [True]}, {}, None, "junk", [True]])
def test_only_a_real_bool_on_the_run_row_speaks_for_the_run(configured, run):
    """`claim_run` writes `bool(dry_run)`: anything else (a hand-edited row, a missing one) says nothing about
    the run — no line calls it "marked" anything (a truthy "false" string must never read as dry run). The
    members still speak for themselves: a mixed set is still named."""
    live = [_flag("x", False), _flag("bluesky", False)]
    rehearsal = [_flag("x", True), _flag("bluesky", True)]
    assert rs.dry_run_lines(run, live) == [] and rs.dry_run_lines(run, rehearsal) == []
    assert rs.dry_run_lines(run, [_flag("x", False), _flag("bluesky", True)]) == [
        "⚠️ BLUESKY was recorded as a DRY RUN: approving will not send it."]


@pytest.mark.parametrize("metadata", [{"dry_run": False}, {"dry_run": True}, {"dry_run": None}, {}, None, "junk", [],
                                      {"dry_run": "false"}, {"dry_run": "False"}, {"dry_run": 0}, {"dry_run": []}])
def test_a_member_counts_as_live_exactly_when_the_publisher_would_send_it(configured, metadata):
    """The label's rule IS the publisher's — `publisher_service.is_rehearsal` is this module's function — so only
    an exact False is live: a missing, null, string or 0 flag is a rehearsal the publisher never sends. Such a
    row may carry the label and is never called LIVE."""
    assert pub.is_rehearsal is rs.is_rehearsal
    row = _row("x", metadata=metadata)
    rehearsal = pub.is_rehearsal(row)
    assert rehearsal is (not (isinstance(metadata, dict) and metadata.get("dry_run") is False))
    assert rs.dry_run_label([row]) is rehearsal
    assert bool(rs.dry_run_lines({"dry_run": True}, [row])) is (not rehearsal)
    assert bool(rs.dry_run_lines({"dry_run": False}, [row])) is rehearsal


def test_nothing_to_label_is_no_label_and_no_line(configured):
    assert rs.dry_run_label([]) is False
    assert rs.dry_run_lines({"dry_run": True}, []) == [] and rs.dry_run_lines({"dry_run": False}, []) == []


@pytest.mark.parametrize("held", ["x_images_off", "platform_off"])
def test_one_held_back_live_member_withdraws_the_promise_for_all(configured, monkeypatch, held):
    """"approving publishes them" only while the web would send every live member now: one it holds back (X's
    picture refused, a platform switched off since) turns the line into "real, not a rehearsal", and the
    blocker line names what happens to that one."""
    members = [_flag("x", False, "image"), _flag("bluesky", False, "image")]
    if held == "x_images_off":
        monkeypatch.setattr(settings, "MARKETING_X_IMAGES", False)
    else:
        monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [p for p in POST_PLATFORMS if p != "bluesky"])
    (line,) = rs.dry_run_lines({"dry_run": True}, members)
    assert line == "⚠️ The run is now marked dry-run, but these posts were recorded LIVE (not a rehearsal)."
    text = rs.compose_bundle_decision_text("post", members, [], "d", rs.dry_run_label(members), [], dry_notes=[line])
    blocker = rs._X_IMAGES_OFF if held == "x_images_off" else "bluesky is not enabled"
    assert any(row.startswith(f"⚠️ {blocker}: ") for row in text.split("\n"))
    assert "approving publishes" not in text


def test_the_dry_run_lines_sit_right_under_the_header_and_survive_the_4096_cap(configured):
    members = [_flag("x", False), _flag("bluesky", True)]
    notes = rs.dry_run_lines({"dry_run": True}, members)
    assert len(notes) == 2
    text = rs.compose_bundle_decision_text("post", members, [], "2026-10-16", rs.dry_run_label(members),
                                           ["⚠️ " + "w" * 5000], series_note="Money Map", dry_notes=notes)
    assert rs.utf16_len(text) <= 4096
    assert text.split("\n")[:5] == ["DECISION · POSTS · run 2026-10-16", *notes, "Series: Money Map",
                                    "Approve all sends: X (text), BLUESKY (text)"]
    # no notes (or an empty list) leaves the message exactly as it was
    plain = rs.compose_bundle_decision_text("post", members, [], "2026-10-16", False, [])
    assert rs.compose_bundle_decision_text("post", members, [], "2026-10-16", False, [], dry_notes=[]) == plain
    assert "⚠️" not in plain


@pytest.mark.asyncio
async def test_a_mixed_bundle_is_never_labelled_and_names_the_rows_that_never_go_out(bundles, tg, wakes):
    """create_posts adopts an existing row WITH its flag, so a re-claim between two create_posts calls can leave
    one run holding both kinds. The POSTS bundle is then labelled nowhere (some of it goes out), its decision
    names both sides, and Approve all leaves exactly the named live rows for the publisher."""
    ids = _seed_day(bundles, dry_run=True)
    for p in ("threads", "linkedin"):
        _meta(bundles, ids[p])["dry_run"] = True
    await rs.review_cycle()
    _video_decision, post_decision = _decision_messages(tg)
    assert "DRY RUN" not in tg.of("sendPhoto")[0]["caption"]
    assert not any("DRY RUN" in m["text"].split("\n")[0] for m in tg.of("sendMessage"))
    assert post_decision["text"].split("\n")[1:3] == [
        "⚠️ The run is now marked dry-run, but X, BLUESKY, FACEBOOK were recorded LIVE (not a rehearsal): approving "
        "publishes them.",
        "⚠️ THREADS, LINKEDIN were recorded as a DRY RUN: approving will not send them."]
    await rs.handle_update(_tap_on(post_decision, "g"))
    assert {_post(bundles, ids[p])["status"] for p in POST_PLATFORMS_TEXT} == {"approved"}
    assert sorted(p for p in POST_PLATFORMS_TEXT if not pub.is_rehearsal(_post(bundles, ids[p]))) == [
        "bluesky", "facebook", "x"]


@pytest.mark.asyncio
async def test_a_rehearsal_preview_never_labels_nor_warns_a_bundle_of_live_members(bundles, tg, monkeypatch):
    """A preview is never sent and never decided: its flag moves neither the label nor the lines of a bundle
    that has members."""
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [p for p in POST_PLATFORMS if p != "threads"])
    ids = _seed_day(bundles)
    _meta(bundles, ids["threads"])["dry_run"] = True
    await rs.review_cycle()
    assert "DRY RUN" not in tg.of("sendPhoto")[0]["caption"]
    post_decision = _decision_messages(tg)[1]
    assert "DRY RUN" not in post_decision["text"] and "⚠️" not in post_decision["text"]


@pytest.mark.asyncio
async def test_a_live_preview_never_strips_the_dry_run_label_from_a_bundle_of_rehearsal_members(bundles, tg,
                                                                                            monkeypatch):
    """The mirror of the test above: the label is the MEMBERS' (what Approve all decides). A LIVE preview — its
    platform switched off since create_posts — is never sent either, so it neither drops "DRY RUN" from a
    bundle whose members are all rehearsal rows nor adds a ⚠️ line. (Read from members AND previews, the label
    vanished from the picture, every caption and the decision, and only this test went red: re-verification
    pass, 2026-10-16.)"""
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [p for p in POST_PLATFORMS if p != "threads"])
    ids = _seed_day(bundles, dry_run=True)
    _rehearse(bundles, {p: pid for p, pid in ids.items() if p != "threads"})      # threads stays LIVE: a preview
    await rs.review_cycle()
    photo_caption = tg.of("sendPhoto")[0]["caption"]
    assert "· DRY RUN" in photo_caption and "THREADS (preview)" in photo_caption
    assert all("· DRY RUN" in m["text"].split("\n")[0] for m in tg.of("sendMessage"))
    post_decision = _decision_messages(tg)[1]
    lines = post_decision["text"].split("\n")
    assert lines[0] == "DECISION · POSTS · run 2026-09-28 · DRY RUN"
    assert "Not sent: THREADS (preview — not wired yet)" in lines and "⚠️" not in post_decision["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("preview_meta, labelled", [({"dry_run": True}, True), ({"dry_run": False}, False),
                                                    ({"dry_run": None}, True), ({}, True)])
async def test_a_preview_only_bundle_is_labelled_from_its_previews_not_the_run(bundles, tg, monkeypatch,
                                                                             preview_meta, labelled):
    monkeypatch.setattr(rs.outlets, "enabled_platforms", lambda: [p for p in POST_PLATFORMS if p != "threads"])
    _seed_run(bundles, video_id=None, dry_run=not labelled)          # the run row says the opposite: never read
    _seed_post(bundles, RUN_A, "threads", "text", caption=SHORT, title=None, meta=preview_meta)
    counters = await rs.review_cycle()
    assert counters["bundles"] == 0 and counters["sent"] == 1
    (msg,) = tg.of("sendMessage")
    assert ("· DRY RUN" in msg["text"].split("\n")[0]) is labelled


@pytest.mark.asyncio
@pytest.mark.parametrize("posts_dry, worker_dry, line", [
    # F2 as reported: LIVE posts; the owner then set the worker to dry run, and the re-claim marked the RUN dry
    (False, True, _LIVE_UNDER_A_DRY_RUN),
    # the reverse: rehearsal posts; the owner then set the worker live, and the re-claim marked the RUN live
    (True, False, "⚠️ The run is now marked live, but these posts were recorded as a DRY RUN: approving will not "
                  "send them."),
], ids=["live_posts_run_re_claimed_dry", "rehearsal_posts_run_re_claimed_live"])
async def test_a_bundle_re_offered_after_a_real_re_claim_keeps_its_posts_label(bundles, tg, wakes, monkeypatch,
                                                                              posts_dry, worker_dry, line):
    """Batch 1 review F2 step by step, with the REAL writer of the run flag instead of a hand-set one (the tests
    above seed the run row): attempt 1 recorded the posts (each carrying the flag create_posts stamped) and the
    run closed `failed` at the assets_ready checkpoint; the first VIDEO offer's decision send failed (502), so
    that bundle is owed a re-offer; the owner then flipped the worker's MARKETING_DRY_RUN, and the next tick's
    `run_service.claim_run` RE-CLAIMED the run — rewriting the run row's `dry_run`, never a post's. The re-offer
    (a new bundle id, its stamp merged over the old one) takes its label from the posts, says under its header
    that the run changed, and Approve all leaves exactly the rows the publisher's own rule reads as
    `posts_dry` (`dry = MARKETING_DRY_RUN or is_rehearsal(post)`)."""
    from datetime import date, datetime, timezone

    monkeypatch.setattr(settings, "MARKETING_MAX_RUN_ATTEMPTS", 6)
    monkeypatch.setattr(settings, "MARKETING_RUN_STALE_SECONDS", 2700)
    ids = _seed_day(bundles, image=False, run_status="failed", dry_run=posts_dry)
    if posts_dry:
        _rehearse(bundles, ids)
    label = " · DRY RUN" if posts_dry else ""
    tg.script["sendMessage"] = [None, None, None, (502, b"")]        # the VIDEO decision (4th message) fails
    await rs.review_cycle()
    # attempt 1's two offers: the run row and the posts still agree — the label alone, no ⚠️ line
    first = _decision_messages(tg)
    assert [d["text"].split("\n")[0] for d in first] == [f"DECISION · VIDEO · run 2026-09-28{label}",
                                                         f"DECISION · POSTS · run 2026-09-28{label}"]
    assert not any("⚠️" in d["text"] for d in first)
    old = _meta(bundles, ids["tiktok"])["review_bundle"]["id"]
    assert all("review_notified_at" not in _meta(bundles, ids[p]) for p in VIDEO_PLATFORMS)

    run, reason = await bundles.claim_run(date(2026, 9, 28), worker_version="worker-2", dry_run=worker_dry,
                                          now=datetime(2026, 9, 28, 21, 30, tzinfo=timezone.utc),
                                          claim_nonce="nonce-2")
    assert reason == mrs.CLAIMED and run["id"] == RUN_A
    assert run["dry_run"] is worker_dry and run["status"] == "in_progress" and run["stage"] == "assets_ready"
    assert {pub.is_rehearsal(_post(bundles, ids[p])) for p in ids} == {posts_dry}     # every post keeps its own

    tg.calls.clear()
    tg.ids.clear()
    await rs.review_cycle()
    (fresh,) = _decision_messages(tg)
    assert _bundle_id(fresh) != old
    lines = fresh["text"].split("\n")
    assert lines[:3] == [f"DECISION · VIDEO · run 2026-09-28{label}", line,
                         "Approve all sends: TIKTOK (video), YOUTUBE (video), INSTAGRAM (video)"]
    assert ("DRY RUN" in tg.of("sendVideo")[0]["caption"]) is posts_dry
    assert all(("· DRY RUN" in m["text"].split("\n")[0]) is posts_dry for m in tg.of("sendMessage"))

    await rs.handle_update(_tap_on(fresh, "g"))
    for p in VIDEO_PLATFORMS:
        row = _post(bundles, ids[p])
        assert row["status"] == "approved" and pub.is_rehearsal(row) is posts_dry
    assert wakes                                                       # Approve all woke the publisher


# ── Batch 3 (2026-10-20): the VIDEO bundle carries the Facebook / LinkedIn video posts — still two decisions ──
#
# A run may give Facebook / LinkedIn / Threads a SECOND post: the day's video beside their picture/text post
# (`schemas.is_extra_format`). It is a video post, so it rides in the VIDEO bundle on the run's one video, and
# the VIDEO decision names it on one more note line — the publisher sends it only after its platform's
# picture/text post (primary first). A day without an extra reads exactly as before.

from app.schemas.marketing import EXTRA_VIDEO_PLATFORMS, is_extra_format  # noqa: E402

#: An extra's caption: its platform's accepted caption with the narrated-video disclaimer swapped in.
LONG_VIDEO = "A longer caption for Facebook and LinkedIn.\n\nWith a second paragraph.\n\nScript and narration generated with AI."
_WAIT = "Goes out after its picture/text post is decided: "
#: The ✂ drop line on a decision a member of which is on a platform the run gives two posts (round-1 fix
#: DEPLOY-B3-DROP-TEXT) — and the line of every other day, byte for byte as before.
_DROP_ONE_POST = "✂ drop = reject that one post and keep the rest (optional)."
_DROP_ONE_PLATFORM = "✂ drop = reject that one platform and keep the rest (optional)."


def _seed_extras(svc, platforms=("facebook", "linkedin"), *, caption: str = LONG_VIDEO) -> Dict[str, str]:
    """The run's EXTRA video posts, as create_posts appends them: after the primaries, on the run's video."""
    return {p: _seed_post(svc, RUN_A, p, "video", caption=caption, title=None, asset_ids=[VIDEO]) for p in platforms}


async def _day_with_extras(svc, tg) -> tuple:
    ids = _seed_day(svc)
    extras = _seed_extras(svc)
    counters = await rs.review_cycle()
    video, post = _decision_messages(tg)
    return ids, extras, video, post, counters


@pytest.mark.asyncio
async def test_batch3_the_video_bundle_gains_the_extra_video_posts_and_there_are_still_two_decisions(bundles, tg):
    ids, extras, video, post, counters = await _day_with_extras(bundles, tg)
    assert counters == {"pending": 10, "sent": 10, "stamped": 10, "videos": 1, "failed": 0, "rate_limited": 0,
                        "bundles": 2, "images": 1, "held": 0}
    assert len(_decision_messages(tg)) == 2                                # still two decisions
    assert [m for m, _ in tg.calls] == ["sendVideo", *["sendMessage"] * 5, "sendPhoto", *["sendMessage"] * 3]
    (shown,) = tg.of("sendVideo")                                          # ONE video, naming every member
    assert shown["caption"] == ("VIDEO · TIKTOK, YOUTUBE, INSTAGRAM, FACEBOOK, LINKEDIN · run 2026-09-28\n"
                                "The captions and your decision follow.")
    msgs = tg.of("sendMessage")
    # One message per DISTINCT caption: the two extras share theirs, apart from every primary's.
    assert msgs[3]["text"] == f"CAPTION · FACEBOOK (video), LINKEDIN (video) · run 2026-09-28\n\n{LONG_VIDEO}"
    assert msgs[6]["text"] == f"CAPTION · FACEBOOK (image), LINKEDIN (image) · run 2026-09-28\n\n{LONG}"
    members = [ids[p] for p in VIDEO_PLATFORMS] + [extras["facebook"], extras["linkedin"]]
    # Round-1 fix DEPLOY-B3-DROP-TEXT: Facebook and LinkedIn have two posts today, one in each decision, so a ✂ drop
    # rejects ONE post — never "that one platform" — and each decision says what it leaves alone.
    assert video["text"].split("\n") == [
        "DECISION · VIDEO · run 2026-09-28",
        "Approve all sends: TIKTOK (video), YOUTUBE (video), INSTAGRAM (video), FACEBOOK (video), LINKEDIN (video)",
        _WAIT + "FACEBOOK, LINKEDIN",
        _DROP_ONE_POST,
        "FACEBOOK, LINKEDIN also have a picture/text post: ✂ drop here rejects only the video.",
    ]
    bid = _bundle_id(video)
    assert video["reply_markup"] == rs.bundle_keyboard(bid, [_post(bundles, pid) for pid in members],
                                                       paired={"facebook", "linkedin"})
    drops = [b["text"] for row in video["reply_markup"]["inline_keyboard"][2:] for b in row]
    # a ✂ drop per member, extras too — theirs naming the format
    assert drops == ["✂ drop TIKTOK", "✂ drop YOUTUBE", "✂ drop INSTAGRAM", "✂ drop FACEBOOK video",
                     "✂ drop LINKEDIN video"]
    for pid in members:
        meta = _meta(bundles, pid)
        assert meta["review_bundle"] == {"id": bid, "kind": "video", "members": members,
                                         "caption_sha": mrs.review_caption_sha(_post(bundles, pid))}
        assert meta["review_message_id"] == video["message_id"] and _post(bundles, pid)["asset_ids"] == [VIDEO]
    # The POSTS decision: the same members and notes as without the extras; only what a drop does is said apart —
    # and (round-2 send fix) that ❌ Reject all releases the video as much as a drop does.
    assert post["text"].split("\n") == [
        "DECISION · POSTS · run 2026-09-28",
        "Approve all sends: X (text), BLUESKY (image), THREADS (image), FACEBOOK (image), LINKEDIN (image)",
        _DROP_ONE_POST,
        "FACEBOOK, LINKEDIN also have a video: ❌ Reject all or ✂ drop here rejects only the picture/text post — the "
        "video, once approved, then goes out alone.",
    ]
    assert [b["text"] for row in post["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop X", "✂ drop BLUESKY", "✂ drop THREADS", "✂ drop FACEBOOK image", "✂ drop LINKEDIN image"]
    assert _meta(bundles, ids["facebook"])["review_bundle"]["kind"] == "post"


def test_batch3_the_wait_note_names_only_extras_and_is_absent_without_them(configured):
    primaries = [_row(p, "video") for p in VIDEO_PLATFORMS]
    assert rs.bundle_extras_lines(primaries) == []
    # A day without an extra: the VIDEO decision byte for byte as before Batch 3.
    assert rs.compose_bundle_decision_text("video", primaries, [], "2026-09-28", False, []) == (
        "DECISION · VIDEO · run 2026-09-28\nApprove all sends: TIKTOK (video), YOUTUBE (video), INSTAGRAM (video)\n"
        "✂ drop = reject that one platform and keep the rest (optional).")
    extras = [_row(p, "video") for p in EXTRA_VIDEO_PLATFORMS]
    assert rs.bundle_extras_lines(primaries + extras) == [_WAIT + "FACEBOOK, LINKEDIN, THREADS"]
    # Only the pair makes an extra: never a picture/text post, never X or Bluesky, never a junk value.
    not_extras = [_row(p, f) for p in ("facebook", "linkedin", "threads") for f in ("text", "image")]
    not_extras += [_row("x", "video"), _row("bluesky", "video"), _row("FACEBOOK", "video"), _row("facebook", "VIDEO"),
                   _row(None, "video"), _row("facebook", None), _row(7, "video")]
    assert not any(is_extra_format(p.get("platform"), p.get("format")) for p in not_extras)
    assert rs.bundle_extras_lines(not_extras) == []
    # A preview is never sent, so never named; the note sits after the first-comment line.
    text = rs.compose_bundle_decision_text("video", [_commented(_row("tiktok", "video")),
                                                     _commented(_row("facebook", "video"))],
                                           [_row("linkedin", "video")], "2026-09-28", False, ["⚠️ media note"])
    assert text.split("\n") == [
        "DECISION · VIDEO · run 2026-09-28",
        "Approve all sends: TIKTOK (video), FACEBOOK (video)",
        "Not sent: LINKEDIN (preview — not wired yet)",
        "First comment requested on: TIKTOK, FACEBOOK",
        _WAIT + "FACEBOOK",
        "⚠️ media note",
        "✂ drop = reject that one platform and keep the rest (optional).",
    ]


@pytest.mark.asyncio
async def test_batch3_drop_one_extra_rejects_only_it(bundles, tg, wakes):
    ids, extras, video, _post_decision, _c = await _day_with_extras(bundles, tg)
    tg.calls.clear()
    res = await rs.handle_update(_tap_on(video, "s", extras["facebook"]))
    assert res["outcome"] == "rejected" and _post(bundles, extras["facebook"])["status"] == "rejected"
    # Named as its button named it (round-1 fix DEPLOY-B3-DROP-TEXT): the Facebook VIDEO went, not Facebook.
    assert _answers(tg) == ["Dropped FACEBOOK video ✂"]
    # Facebook's picture post (the other bundle) and every other video member stay open.
    assert _post(bundles, ids["facebook"])["status"] == "pending_review"
    rest = [ids[p] for p in VIDEO_PLATFORMS] + [extras["linkedin"]]
    assert {_post(bundles, pid)["status"] for pid in rest} == {"pending_review"}
    (edit,) = tg.of("editMessageText")
    assert edit["message_id"] == video["message_id"]
    assert re.search(r"\n\n✂ Dropped FACEBOOK video \d\d:\d\d ET — the rest stay open$", edit["text"])
    # The rebuilt keyboard keeps the labels the owner was shown: LinkedIn's still names its format.
    assert edit["reply_markup"] == rs.bundle_keyboard(_bundle_id(video), [_post(bundles, pid) for pid in rest],
                                                      paired={"linkedin"})
    assert [b["text"] for row in edit["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop TIKTOK", "✂ drop YOUTUBE", "✂ drop INSTAGRAM", "✂ drop LINKEDIN video"]
    (prompt,) = tg.of("sendMessage")                                       # the per-post reasons, under it
    assert prompt["text"] == "Why was the FACEBOOK video post dropped? Tap a reason (optional)."
    assert wakes == []
    # A second drop on the message as it now stands (Telegram returns the REBUILT keyboard) names its post the same
    # way: the naming survives every rebuild, with no read of the run.
    tg.calls.clear()
    again = {"message_id": video["message_id"], "text": edit["text"], "reply_markup": edit["reply_markup"]}
    assert (await rs.handle_update(_tap_on(again, "s", extras["linkedin"])))["outcome"] == "rejected"
    assert _answers(tg) == ["Dropped LINKEDIN video ✂"]
    (edit2,) = tg.of("editMessageText")
    assert [b["text"] for row in edit2["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop TIKTOK", "✂ drop YOUTUBE", "✂ drop INSTAGRAM"]
    assert _post(bundles, ids["linkedin"])["status"] == "pending_review"        # LinkedIn's picture post stays open


@pytest.mark.asyncio
async def test_batch3_approve_all_decides_the_extras_with_their_bundle(bundles, tg, wakes):
    ids, extras, video, _post_decision, _c = await _day_with_extras(bundles, tg)
    tg.calls.clear()
    res = await rs.handle_update(_tap_on(video, "g"))
    assert res["outcome"] == "decided" and res["decided"] == 5 and wakes == [1]
    assert _answers(tg) == ["Approved 5 ✅"]
    for pid in [ids[p] for p in VIDEO_PLATFORMS] + list(extras.values()):
        row = _post(bundles, pid)
        assert row["status"] == "approved" and row["approved_by"] == f"telegram:{OWNER}"
    # Each platform's picture/text post is the OTHER decision's: still open.
    assert {_post(bundles, ids[p])["status"] for p in POST_PLATFORMS_TEXT} == {"pending_review"}
    (edit,) = tg.of("editMessageText")
    assert edit["text"].endswith("• FACEBOOK (video): approved\n• LINKEDIN (video): approved")


# ── Round-1 fix DEPLOY-B3-DROP-TEXT (2026-10-20): on a platform the run gives two posts, ✂ drop rejects ONE post ──
#
# With extras on, "✂ drop = reject that one platform" was untrue: Facebook's picture post sits in the POSTS decision
# and its video in the VIDEO one, and dropping the picture RELEASES an approved video (primary first holds it only
# while the picture post is open), which then goes out alone — the owner read "no Facebook today". On such a
# platform (`paired_platforms`: an extra video AND another post of that platform in the run) the line reads "reject
# that one post", each decision says what a drop there leaves alone, and the button, its toast, its verdict line,
# its reason prompt and a keyboard rebuilt after a drop name the format. A day without an extra is byte-identical
# (the twin e2e below pins every message). Mutation-checked once by hand (scratch plugin): an unpaired check, a
# label or line that ignores `paired`, a sweep that never passes it, a rebuilt keyboard that forgets it, a
# non-exact keyboard read and a pairing without the primary each turn a test here red.


def test_batch3_paired_platforms_needs_both_posts_of_one_platform():
    """Paired = the run gives that platform an EXTRA video AND another post, in any status (a closed picture post
    still means the video is "only the video"). An extra alone — dropping it DOES leave the platform out — a primary
    alone, X / Bluesky (never an extra), today's day without extras and junk rows are not paired."""
    pic = {p: _row(p, "image") for p in ("facebook", "linkedin", "threads")}
    vid = {p: _row(p, "video") for p in ("facebook", "linkedin", "threads")}
    assert rs.paired_platforms([pic["facebook"], vid["facebook"]]) == frozenset({"facebook"})
    assert rs.paired_platforms([vid["facebook"], dict(pic["facebook"], status="rejected")]) == {"facebook"}
    assert rs.paired_platforms([dict(pic["linkedin"], format="text"), vid["linkedin"], vid["threads"]]) == {"linkedin"}
    # a sibling whose format cannot be read counts as the other post (the publisher's own fail-closed reading)
    assert rs.paired_platforms([vid["threads"], dict(pic["threads"], format=None)]) == {"threads"}
    assert rs.paired_platforms([vid["facebook"], vid["linkedin"]]) == frozenset()          # extras alone
    assert rs.paired_platforms(list(pic.values())) == frozenset()                           # primaries alone
    assert rs.paired_platforms([_row(p, f) for p in ("x", "bluesky") for f in ("text", "image", "video")]) == set()
    today = [_row(p, "video") for p in VIDEO_PLATFORMS] + [_row(p, "image") for p in POST_PLATFORMS_TEXT]
    assert rs.paired_platforms(today) == frozenset()
    junk = [None, "row", 7, ["facebook", "video"], {"platform": ["facebook"], "format": "video"},
            {"platform": {"a": 1}, "format": "video"}, {}]
    assert rs.paired_platforms(junk + [pic["threads"], vid["threads"]]) == {"threads"}
    for bad in (None, "rows", 7, {"platform": "facebook", "format": "video"}):
        assert rs.paired_platforms(bad) == frozenset()


def test_batch3_drop_lines_and_labels_say_one_post_only_on_a_paired_platform():
    tiktok, fb_vid, th_vid = _row("tiktok", "video"), _row("facebook", "video"), _row("threads", "video")
    x, fb_pic, li_txt = _row("x"), _row("facebook", "image"), _row("linkedin", "text")
    assert (rs.DROP_LINE, rs.DROP_LINE_PAIRED) == (_DROP_ONE_PLATFORM, _DROP_ONE_POST)
    # Nothing paired — every day without an extra: the line of before, and the plain labels.
    assert rs.drop_lines([tiktok, fb_vid]) == rs.drop_lines([tiktok, fb_vid], frozenset()) == [_DROP_ONE_PLATFORM]
    assert rs.drop_lines([x, fb_pic], {"linkedin"}) == [_DROP_ONE_PLATFORM]         # a pair no member is on
    # One paired member reads singular, two plural; each side gets its own line, video first.
    assert rs.drop_lines([tiktok, fb_vid], {"facebook"}) == [
        _DROP_ONE_POST, "FACEBOOK also has a picture/text post: ✂ drop here rejects only the video."]
    assert rs.drop_lines([x, fb_pic, li_txt], {"facebook", "linkedin"}) == [
        _DROP_ONE_POST, "FACEBOOK, LINKEDIN also have a video: ❌ Reject all or ✂ drop here rejects only the "
        "picture/text post — the video, once approved, then goes out alone."]
    assert rs.drop_lines([li_txt, fb_vid], {"facebook", "linkedin"}) == [
        _DROP_ONE_POST, "FACEBOOK also has a picture/text post: ✂ drop here rejects only the video.",
        "LINKEDIN also has a video: ❌ Reject all or ✂ drop here rejects only the picture/text post — the video, once "
        "approved, then goes out alone."]
    # The labels: the format only on a paired platform; a junk platform value never raises and is never paired.
    paired = {"facebook", "linkedin"}
    assert [rs.drop_label(p, paired) for p in (tiktok, fb_vid, li_txt, x, th_vid, fb_pic)] == [
        "✂ drop TIKTOK", "✂ drop FACEBOOK video", "✂ drop LINKEDIN text", "✂ drop X", "✂ drop THREADS",
        "✂ drop FACEBOOK image"]
    assert rs.drop_label(fb_vid) == "✂ drop FACEBOOK"
    for junk in (["facebook"], {"a": 1}, None, 7):
        assert not rs.drop_label(dict(fb_vid, platform=junk), paired).endswith(" video")
    # The keyboard carries the labels; one member still gets no drop at all; nothing paired = the keyboard of before.
    kb = rs.bundle_keyboard(_PID, [tiktok, fb_vid], paired={"facebook"})
    assert kb["inline_keyboard"][2:] == [[{"text": "✂ drop TIKTOK", "callback_data": f"s:{tiktok['id']}"}],
                                         [{"text": "✂ drop FACEBOOK video", "callback_data": f"s:{fb_vid['id']}"}]]
    assert rs.bundle_keyboard(_PID, [fb_vid], paired={"facebook"}) == rs.bundle_keyboard(_PID, [fb_vid])
    assert rs.bundle_keyboard(_PID, [tiktok, fb_vid]) == rs.bundle_keyboard(_PID, [tiktok, fb_vid], paired=set())
    # The decision text: the drop lines close it (after the warnings); a one-member decision says nothing of drops.
    text = rs.compose_bundle_decision_text("video", [tiktok, fb_vid], [], "2026-09-28", False, ["⚠️ media note"],
                                           paired={"facebook"})
    assert text.split("\n")[-3:] == ["⚠️ media note", _DROP_ONE_POST,
                                     "FACEBOOK also has a picture/text post: ✂ drop here rejects only the video."]
    assert "✂" not in rs.compose_bundle_decision_text("video", [fb_vid], [], "2026-09-28", False, [],
                                                      paired={"facebook"}).replace(_WAIT, "")


def test_batch3_shown_paired_reads_only_our_exact_paired_labels():
    """The rebuilt keyboard, the toast and the reason prompt take the naming from the TAPPED keyboard — only an exact
    match of our own paired label counts, and a keyboard that cannot be read means the plain naming."""
    tiktok, fb_vid, li_vid = _row("tiktok", "video"), _row("facebook", "video"), _row("linkedin", "video")
    kb = rs.bundle_keyboard(_PID, [tiktok, fb_vid, li_vid], paired={"facebook"})
    assert rs._shown_paired({"reply_markup": kb}, [tiktok, fb_vid, li_vid]) == frozenset({"facebook"})
    for text in ("✂ drop FACEBOOK video ", "✂ drop FACEBOOK", "✂ drop FACEBOOK image", None, 7):
        forged = copy.deepcopy(kb)
        forged["inline_keyboard"][3][0]["text"] = text
        assert rs._shown_paired({"reply_markup": forged}, [fb_vid]) == frozenset()
    # The label of ANOTHER post (LinkedIn's button read for the Facebook post) never pairs it.
    swapped = copy.deepcopy(kb)
    swapped["inline_keyboard"][3][0]["callback_data"] = f"s:{li_vid['id']}"
    assert rs._shown_paired({"reply_markup": swapped}, [li_vid]) == frozenset()
    for junk in ({}, {"reply_markup": None}, {"reply_markup": {"inline_keyboard": "x"}},
                 {"reply_markup": {"inline_keyboard": [None, ["x"], [{"callback_data": 7}], [{"text": "✂"}]]}}):
        assert rs._shown_paired(junk, [fb_vid]) == frozenset()
    assert rs._shown_paired({"reply_markup": kb}, [None, "x", dict(fb_vid, id="not-a-uuid")]) == frozenset()


@pytest.mark.asyncio
async def test_batch3_dropping_a_paired_picture_post_names_it_and_releases_only_its_video(bundles, tg, wakes):
    """✂ drop FACEBOOK image on the POSTS decision rejects the picture post only: the toast, the line and the reason
    prompt say "FACEBOOK image", the rebuilt keyboard keeps LinkedIn's "image", and — what the decision told the
    owner — Facebook's video, once approved, is no longer held by the publisher, while LinkedIn's still is."""
    ids, extras, _video, post, _c = await _day_with_extras(bundles, tg)
    tg.calls.clear()
    res = await rs.handle_update(_tap_on(post, "s", ids["facebook"]))
    assert res["outcome"] == "rejected" and _post(bundles, ids["facebook"])["status"] == "rejected"
    assert _answers(tg) == ["Dropped FACEBOOK image ✂"]
    assert _post(bundles, extras["facebook"])["status"] == "pending_review"      # its video is the other decision's
    (edit,) = tg.of("editMessageText")
    assert re.search(r"\n\n✂ Dropped FACEBOOK image \d\d:\d\d ET — the rest stay open$", edit["text"])
    assert [b["text"] for row in edit["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop X", "✂ drop BLUESKY", "✂ drop THREADS", "✂ drop LINKEDIN image"]
    (prompt,) = tg.of("sendMessage")
    assert prompt["text"] == "Why was the FACEBOOK image post dropped? Tap a reason (optional)."
    for pid in extras.values():
        _post(bundles, pid)["status"] = "approved"                                # the VIDEO decision approved
    assert await pub._extra_waits(bundles, _post(bundles, extras["facebook"])) is False   # goes out alone
    assert await pub._extra_waits(bundles, _post(bundles, extras["linkedin"])) is True    # its picture still open


@pytest.mark.asyncio
async def test_batch3_a_drop_tap_without_its_keyboard_degrades_to_the_plain_names(bundles, tg, wakes):
    """Telegram always returns the tapped message with its keyboard; when it cannot be read, the drop still lands
    (the bundle id comes from the post's own stamp) and the naming degrades to the plain "<PLATFORM>" — the decision
    text the owner holds still says a drop there rejects one post."""
    _ids, extras, video, _post_decision, _c = await _day_with_extras(bundles, tg)
    tg.calls.clear()
    tap = _tap_on(video, "s", extras["facebook"])
    del tap["callback_query"]["message"]["reply_markup"]
    assert (await rs.handle_update(tap))["outcome"] == "rejected"
    assert _post(bundles, extras["facebook"])["status"] == "rejected" and _answers(tg) == ["Dropped FACEBOOK ✂"]
    (edit,) = tg.of("editMessageText")
    assert [b["text"] for row in edit["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop TIKTOK", "✂ drop YOUTUBE", "✂ drop INSTAGRAM", "✂ drop LINKEDIN"]
    assert _DROP_ONE_POST in edit["text"].split("\n")


# ── Round-2 send fix (2026-10-20): EVERY way the POSTS decision rejects a paired picture post releases its video ──
#
# The round-1 note said what ✂ drop does — "✂ drop here rejects only the picture/text post — the video, once approved,
# then goes out alone" — but the publisher holds an extra video only while its platform's picture/text post is OPEN
# (`publisher_service._extra_waits`), so ❌ Reject all releases an approved video just as a drop does, and so does the
# ❌ Reject all of a one-member POSTS decision, which carries no ✂ drop and so carried no note at all. The note
# (`released_video_lines`) now names every reject button its message carries — "❌ Reject all or ✂ drop" with two or
# more members, "❌ Reject all" with one, never a button the message does not carry (re-review r3's rule) — and the
# taps below show that what it says is what happens. A VIDEO decision's reject releases nothing (no note), and a day
# without an extra is byte-identical (the one-member pins here; the twin e2e below pins a whole day).
# Mutation-checked once by hand (scratch plugin, the repo never edited): no note, a silent one-member decision, a
# one-member note naming ✂ drop, the old drop-only wording, the two wordings swapped, a note that ignores `paired`, a
# note on a video member, a drop line without the note, a one-member decision given the drop line, has/have swapped,
# a note without its consequence, a sweep that never passes `paired`, and a publisher that still holds the video once
# its picture post is rejected each turn a test here red.

#: The note on a one-member POSTS decision whose member is Facebook's picture post (its only reject: ❌ Reject all).
_RELEASE_ONE = ("FACEBOOK also has a video: ❌ Reject all here rejects only the picture/text post — the video, once "
                "approved, then goes out alone.")


def test_round2_the_release_note_names_every_reject_button_its_message_carries_and_no_other():
    fb_pic, li_txt, th_pic = _row("facebook", "image"), _row("linkedin", "text"), _row("threads", "image")
    fb_vid, li_vid, x = _row("facebook", "video"), _row("linkedin", "video"), _row("x")
    paired = {"facebook", "linkedin", "threads"}
    # The buttons as the keyboard reads them: ❌ Reject all on every decision, ✂ drop only with two or more members.
    assert rs.REJECT_BUTTONS_ONE == rs.bundle_keyboard(_PID, [fb_pic])["inline_keyboard"][1][0]["text"]
    assert rs.REJECT_BUTTONS_MANY == rs.REJECT_BUTTONS_ONE + " or ✂ drop"
    assert all(row[0]["text"].startswith("✂ drop ")
               for row in rs.bundle_keyboard(_PID, [x, fb_pic], paired=paired)["inline_keyboard"][2:])
    # One member names ❌ Reject all alone; two or more name both; one line for every paired picture/text member.
    assert rs.released_video_lines([fb_pic], paired, can_drop=False) == [_RELEASE_ONE]
    assert rs.released_video_lines([x, fb_pic, li_txt, th_pic], paired, can_drop=True) == [
        "FACEBOOK, LINKEDIN, THREADS also have a video: ❌ Reject all or ✂ drop here rejects only the picture/text "
        "post — the video, once approved, then goes out alone."]
    assert rs.released_video_lines([x, li_txt], paired, can_drop=True) == [
        "LINKEDIN also has a video: ❌ Reject all or ✂ drop here rejects only the picture/text post — the video, once "
        "approved, then goes out alone."]
    # A member whose format cannot be read is the picture/text post, as the publisher's hold reads it (it waits for
    # any sibling that is not the extra), so rejecting it releases the video too.
    assert rs.released_video_lines([dict(fb_pic, format=None)], paired, can_drop=False) == [_RELEASE_ONE]
    for can_drop in (False, True):
        # Nothing paired (every day without an extra), a pair no member is on, video members (a VIDEO decision:
        # rejecting a video releases nothing), X alone, junk platforms and no member at all: no line.
        assert rs.released_video_lines([fb_pic, li_txt], frozenset(), can_drop=can_drop) == []
        assert rs.released_video_lines([fb_pic, li_txt], can_drop=can_drop) == []
        assert rs.released_video_lines([fb_pic], {"linkedin"}, can_drop=can_drop) == []
        assert rs.released_video_lines([fb_vid, li_vid], paired, can_drop=can_drop) == []
        assert rs.released_video_lines([x], paired, can_drop=can_drop) == []
        for junk in (["facebook"], {"a": 1}, None, 7):
            assert rs.released_video_lines([dict(fb_pic, platform=junk)], paired, can_drop=can_drop) == []
        assert rs.released_video_lines([], paired, can_drop=can_drop) == []


def test_round2_a_one_member_posts_decision_says_what_its_reject_all_does_to_the_video(configured):
    fb_pic, fb_vid = _row("facebook", "image"), _row("facebook", "video")
    text = rs.compose_bundle_decision_text("post", [fb_pic], [], "2026-09-28", False, ["⚠️ media note"],
                                           paired={"facebook"})
    # The note closes the message (after the warnings, where a drop line would be) and names no ✂ drop: there is none.
    assert text.split("\n") == ["DECISION · POSTS · run 2026-09-28", "Approve all sends: FACEBOOK (image)",
                                "⚠️ media note", _RELEASE_ONE]
    assert "✂" not in text
    dry = rs.compose_bundle_decision_text("post", [dict(fb_pic, metadata={"dry_run": True})], [], "2026-09-28", True,
                                          [], paired={"facebook"})
    assert dry.split("\n") == ["DECISION · POSTS · run 2026-09-28 · DRY RUN", "Approve all sends: FACEBOOK (image)",
                               _RELEASE_ONE]
    # A day without an extra — or a pair this member is not on — reads exactly as before: no line.
    before = "DECISION · POSTS · run 2026-09-28\nApprove all sends: FACEBOOK (image)"
    assert rs.compose_bundle_decision_text("post", [fb_pic], [], "2026-09-28", False, []) == before
    for paired in (frozenset(), {"linkedin"}):
        assert rs.compose_bundle_decision_text("post", [fb_pic], [], "2026-09-28", False, [], paired=paired) == before
    # A one-member VIDEO decision: rejecting the video releases nothing, so only the wait line names the pair.
    assert rs.compose_bundle_decision_text("video", [fb_vid], [], "2026-09-28", False, [], paired={"facebook"}) == (
        "DECISION · VIDEO · run 2026-09-28\nApprove all sends: FACEBOOK (video)\n" + _WAIT + "FACEBOOK")


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [True, False], ids=["with_its_video", "day_without_an_extra"])
async def test_round2_reject_all_on_a_one_member_posts_decision_releases_the_video_its_note_names(
        bundles, tg, wakes, extra):
    """Facebook's picture post alone in the POSTS decision (no ✂ drop: Approve all / Reject all), its extra video in
    the VIDEO one. The decision says what its ❌ Reject all does, and that is what happens: the approved video, held
    while the picture post was open, is released to go out alone. Without the extra the run's two decisions read
    byte for byte as before."""
    _seed_run(bundles)
    _seed_image(bundles)
    _seed_post(bundles, RUN_A, "tiktok", "video", caption="tiktok caption", title=None, asset_ids=[VIDEO])
    fb_pic = _seed_post(bundles, RUN_A, "facebook", "image", caption=LONG, title=None, asset_ids=[IMAGE])
    fb_vid = _seed_post(bundles, RUN_A, "facebook", "video", caption=LONG_VIDEO, title=None,
                        asset_ids=[VIDEO]) if extra else None
    counters = await rs.review_cycle()
    assert counters["bundles"] == 2 and counters["failed"] == 0
    video, post = _decision_messages(tg)
    assert all("parse_mode" not in payload for _method, payload in tg.calls)          # plain text, every message
    assert post["reply_markup"] == rs.bundle_keyboard(_bundle_id(post), [_post(bundles, fb_pic)])   # no ✂ drop
    assert post["text"].split("\n") == ["DECISION · POSTS · run 2026-09-28", "Approve all sends: FACEBOOK (image)",
                                        *([_RELEASE_ONE] if extra else [])]
    if not extra:
        assert video["text"] == "DECISION · VIDEO · run 2026-09-28\nApprove all sends: TIKTOK (video)"
        assert post["text"] == "DECISION · POSTS · run 2026-09-28\nApprove all sends: FACEBOOK (image)"
        return
    assert video["text"].split("\n") == [
        "DECISION · VIDEO · run 2026-09-28", "Approve all sends: TIKTOK (video), FACEBOOK (video)", _WAIT + "FACEBOOK",
        _DROP_ONE_POST, "FACEBOOK also has a picture/text post: ✂ drop here rejects only the video."]
    assert (await rs.handle_update(_tap_on(video, "g")))["outcome"] == "decided"
    assert await pub._extra_waits(bundles, _post(bundles, fb_vid)) is True          # held: its picture post is open
    res = await rs.handle_update(_tap_on(post, "j"))                                 # the decision's only reject
    assert (res["outcome"], res["decided"], _answers(tg)[-1]) == ("decided", 1, "Rejected 1 ❌")
    assert (_post(bundles, fb_pic)["status"], _post(bundles, fb_vid)["status"]) == ("rejected", "approved")
    assert await pub._extra_waits(bundles, _post(bundles, fb_vid)) is False         # released: it goes out alone
    edit = tg.of("editMessageText")[-1]                                              # the note stays above the verdict
    assert edit["text"].startswith(post["text"] + "\n\n❌ Rejected ")
    assert edit["reply_markup"] == rs.bundle_reason_keyboard(_bundle_id(post))


@pytest.mark.asyncio
async def test_round2_reject_all_on_the_posts_decision_releases_every_approved_video_as_its_note_says(
        bundles, tg, wakes):
    """The Batch 3 day: Facebook and LinkedIn each have a picture post (POSTS) and a video (VIDEO). The POSTS note
    names ❌ Reject all beside ✂ drop, and Reject all does what it says: every picture post rejected, both approved
    videos released, each to go out alone."""
    ids, extras, video, post, _c = await _day_with_extras(bundles, tg)
    assert post["text"].split("\n")[-1] == (
        "FACEBOOK, LINKEDIN also have a video: ❌ Reject all or ✂ drop here rejects only the picture/text post — the "
        "video, once approved, then goes out alone.")
    assert (await rs.handle_update(_tap_on(video, "g")))["outcome"] == "decided"
    for pid in extras.values():
        assert await pub._extra_waits(bundles, _post(bundles, pid)) is True
    assert (await rs.handle_update(_tap_on(post, "j")))["decided"] == 5
    assert {_post(bundles, ids[p])["status"] for p in POST_PLATFORMS_TEXT} == {"rejected"}
    for pid in extras.values():
        assert _post(bundles, pid)["status"] == "approved"
        assert await pub._extra_waits(bundles, _post(bundles, pid)) is False


@pytest.mark.asyncio
async def test_round2_when_drops_leave_one_paired_picture_post_the_note_still_covers_its_reject_all(
        bundles, tg, wakes):
    """X's text post and Facebook's picture post: ✂ drop X leaves Facebook alone on a keyboard with no ✂ drop; the note
    the owner holds names ❌ Reject all as well, so the one reject button left is one it covers — and it releases the
    approved video exactly as said."""
    _seed_run(bundles)
    _seed_image(bundles)
    _seed_post(bundles, RUN_A, "tiktok", "video", caption="tiktok caption", title=None, asset_ids=[VIDEO])
    x = _seed_post(bundles, RUN_A, "x", "text", caption=SHORT, title=None)
    fb_pic = _seed_post(bundles, RUN_A, "facebook", "image", caption=LONG, title=None, asset_ids=[IMAGE])
    fb_vid = _seed_post(bundles, RUN_A, "facebook", "video", caption=LONG_VIDEO, title=None, asset_ids=[VIDEO])
    await rs.review_cycle()
    video, post = _decision_messages(tg)
    assert post["text"].split("\n")[-2:] == [
        _DROP_ONE_POST, "FACEBOOK also has a video: ❌ Reject all or ✂ drop here rejects only the picture/text post — "
        "the video, once approved, then goes out alone."]
    assert (await rs.handle_update(_tap_on(video, "g")))["outcome"] == "decided"
    assert (await rs.handle_update(_tap_on(post, "s", x)))["outcome"] == "rejected"
    rebuilt = tg.of("editMessageText")[-1]
    assert rebuilt["reply_markup"] == rs.bundle_keyboard(_bundle_id(post), [_post(bundles, fb_pic)])   # no ✂ left
    assert rebuilt["text"].startswith(post["text"] + "\n\n✂ Dropped X ")                              # the note stays
    assert await pub._extra_waits(bundles, _post(bundles, fb_vid)) is True
    after_drop = {"message_id": post["message_id"], "text": rebuilt["text"], "reply_markup": rebuilt["reply_markup"]}
    assert (await rs.handle_update(_tap_on(after_drop, "j")))["decided"] == 1
    assert (_post(bundles, fb_pic)["status"], _post(bundles, fb_vid)["status"]) == ("rejected", "approved")
    assert await pub._extra_waits(bundles, _post(bundles, fb_vid)) is False


# ── Batch 3 (2026-10-20, integration): two formats on Facebook and LinkedIn, from the freeze to the wire ──────────
#
# The Batch 1 e2e above, for the extras: every hop by its REAL owner and no hand-made row — the write-time freeze
# (`script_service.freeze_post_formats`, then `freeze_video_features` and `freeze_extra_formats`, each switch read
# NOW as `_frozen_output` reads it) → the accepted output as stored → `run_service.create_posts`, called with the
# unchanged worker's PRIMARY specs only, appends the Facebook / LinkedIn video posts itself → the sweep's TWO
# decisions, the VIDEO one naming the extras and the wait note → Approve all on both → the REAL publisher with the
# REAL Upload-Post adapters and client: pass 1 sends every primary post (the pictures to /upload_photos) and HOLDS
# each extra while its picture post is open; the pictures reconcile FOUND; pass 2 sends the two videos to /upload, a
# Reel and a LinkedIn video, never captioned through `title`. The twin turns the switch off with the Batch 1 e2e's
# own settings (its first comments, its Threads text fallback) and pins every Telegram message and every
# Upload-Post request byte for byte — and no Batch 3 machinery runs: no extra key, no extra row, no sibling read,
# nothing held. Today's date (ET): the publisher sends only a run day's posts.
#
# Mutation-checked on 2026-10-20 (scratch plugins, the repo never edited). The two-formats test goes red when the
# extra caption keeps the text disclaimer, a Facebook video sends `title`, the outlet ignores the post's format,
# the publisher skips the hold, an extra loses the video-features stamp or its first comment, rides in the POSTS
# bundle, is recorded before the worker's specs, or carries the picture. The twin goes red when the freeze ignores
# the switch, create_posts appends extras nothing froze, a primary post reads its siblings, or the wait note is
# always printed. The twin's pins also PASS against the pre-Batch-3 code (create_posts, the disclaimers, the outlet,
# publish_cycle and the decision notes as they were before the build): they are the bytes the day sent before.

from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_B3_UP_KEY = "UPkeyE2E0123456789abcdefghijKLMNOPqrs"
_B3_FB_PAGE = "104857600123456"
_B3_LI_PAGE = "86753090"
_B3_DROP = "✂ drop = reject that one platform and keep the rest (optional)."


def _b3_settings(monkeypatch, *, extras: str, commented, features: str) -> None:
    for name, value in (("MARKETING_RUN_STALE_SECONDS", 3600), ("MARKETING_MAX_RUN_ATTEMPTS", 6),
                        ("MARKETING_AUTO_PUBLISH", False),
                        ("MARKETING_IMAGE_PLATFORMS", "bluesky,facebook,linkedin,threads"),
                        ("MARKETING_FIRST_COMMENT_PLATFORMS", ",".join(commented)),
                        ("MARKETING_VIDEO_EXTRA_PLATFORMS", extras), ("MARKETING_VIDEO_FEATURES", features),
                        ("SUPABASE_URL", "https://xyz.supabase.co"), ("MARKETING_MEDIA_BUCKET", "marketing-media"),
                        ("MARKETING_UPLOAD_POST_API_KEY", _B3_UP_KEY), ("MARKETING_UPLOAD_POST_USER", "caydex"),
                        ("MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID", _B3_FB_PAGE),
                        ("MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID", _B3_LI_PAGE),
                        ("MARKETING_PUBLISH_MAX_ATTEMPTS", 3), ("MARKETING_PUBLISH_RECONCILE_AFTER_SECONDS", 600)):
        monkeypatch.setattr(settings, name, value)


async def _b3_record(svc, *, threads_caption: Optional[str] = None) -> SimpleNamespace:
    """Today's run through the REAL write-time freeze — the formats, the video features (the holder declared
    video_v2), the extras — of a lesson composed for today (REAL captions: an extra video caption re-derives only from
    one), then the worker's media and its create_posts call with the PRIMARY specs only, one per frozen format: the
    worker never names an extra. Leaves the run where the sweep offers it."""
    from app.services.marketing import script_service
    from test_marketing_outlet_upload_post import IMG, IMG_SHA

    day = mrs.run_date_et()
    run, _ = await svc.claim_run(day, worker_version="t", dry_run=False, now=datetime.now(timezone.utc),
                                 claim_nonce=fake_db._n())
    rid = run["id"]
    posts = fake_db._b3_posts(_E2E_PLATFORMS, run_date=day)                # the store is live
    if threads_caption is not None:
        posts["threads"]["caption"] = threads_caption
    package = {"posts": posts, "judge": {"mode": "enforce"}, "cards": [dict(fake_db._CARD)],
               "disclaimer_card": fake_db._DISCLAIMER_CARD, "image_post": copy.deepcopy(fake_db._IMAGE_POST),
               "store_state": "live"}
    output = script_service.freeze_post_formats(package, day, image_posts=True, x_images=False, run_id=rid,
                                                image_platforms=script_service.image_platforms_setting())
    features = script_service.freeze_video_features(output, switch=script_service.video_features_setting(),
                                                    holder_v2=True, run_id=rid)
    if features:
        output["video_features"] = features
    output.update(script_service.freeze_extra_formats(
        output, day, platforms=script_service.video_extra_platforms_setting(), run_id=rid))
    _script, ours = await svc.insert_script({"run_id": rid, "status": "accepted", "source_ref": "money_moves:test-item",
                                             "template_id": "checklist", "generation_id": str(uuid.uuid4()),
                                             "output": output})
    assert ours
    video = await fake_db._ready_asset(svc, rid)
    await fake_db._point_video(svc, rid, video["id"])
    drawn = [fake_db._IMAGE_POST["title"], *fake_db._IMAGE_POST["paragraphs"], output["image_footer"]]
    image = await fake_db._post_image(svc, rid, IMG_SHA, size=len(IMG),
                                      metadata={"onscreen_text": drawn, "image_role": "post_image"})
    media = {"video": [video["id"]], "image": [image["id"]], "text": []}
    specs = [{"platform": p, "format": f, **({"asset_ids": media[f]} if media[f] else {})}
             for p, f in output["post_formats"].items()]
    created = await svc.create_posts(rid, specs, claim=fake_db._holder(svc, rid))
    next(r for r in _rows(svc, mrs.RUNS) if r["id"] == rid).update({"status": "media_ready", "stage": "assets_ready"})
    return SimpleNamespace(day=day, rid=rid, output=output, video=video, image=image, specs=specs, created=created)


def _b3_ack(request: httpx.Request) -> httpx.Response:
    """Upload-Post's async ack, naming the request's OWN id (the Idempotency-Key it was sent with)."""
    from test_marketing_outlet_upload_post import _parts

    return httpx.Response(200, json={"success": True, "message": "Upload initiated successfully in background.",
                                     "request_id": dict(_parts(request))["request_id"], "total_platforms": 1})


def _b3_status(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"request_id": request.url.params["request_id"], "status": "completed",
                                     "completed": 1, "total": 1, "results": [], "last_update": "2026-10-20T20:30:00Z"})


def _b3_history(request: httpx.Request) -> httpx.Response:
    """The post is live, under the very request id (`<day>:<platform>:<format>:a<n>`) the read asks about."""
    rid = request.url.params["request_id"]
    platform = rid.split(":")[1]
    item = {"platform": platform, "request_id": rid, "success": True, "platform_post_id": f"{platform}-post-1",
            "post_url": f"https://{platform}.example/p/1", "error_message": None, "fallback_to_inbox": False,
            "upload_timestamp": "2026-10-20T20:30:00Z"}
    return httpx.Response(200, json={"history": [item], "in_progress": [], "total": 1, "page": 1, "limit": 20})


def _b3_publisher(svc, monkeypatch, image: Dict[str, Any]):
    """The REAL publisher over this ledger, with the REAL Upload-Post adapters and client: the six Upload-Post
    platforms enabled (X and Bluesky are other outlets, never called here); Upload-Post a recording MockTransport that
    acks every upload with its own request id and reads every job back as live; the run's picture served from its
    public URL. Returns the recording server."""
    from app.integrations import upload_post
    from app.services.marketing import outlet_base, outlets, publish_clock
    from test_marketing_outlet_upload_post import IMG, UpServer

    # The /go early-window clock is module state a live send stamps (real time): this test's windows live in a
    # dict of their own, never leaking into another test (smart_link's read the clock on a fixed day).
    monkeypatch.setattr(publish_clock, "_until", {})
    up = UpServer()
    for route in ("/upload", "/upload_photos", "/upload_text"):
        up.always(route, _b3_ack)
    up.always("/uploadposts/status", _b3_status)
    up.always("/uploadposts/history", _b3_history)
    monkeypatch.setattr(upload_post, "_client", httpx.AsyncClient(transport=httpx.MockTransport(up.handler)))
    card_url = svc.public_url(str(image["storage_path"]))
    monkeypatch.setattr(outlet_base, "_fetch_transport", httpx.MockTransport(
        lambda request: httpx.Response(200, content=IMG) if str(request.url) == card_url else httpx.Response(404)))
    monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: svc)     # `_video_url` / `load_post_image`
    monkeypatch.setattr(pub, "get_marketing_run_service", lambda: svc)
    monkeypatch.setattr(pub, "_escalation_logged", {})
    monkeypatch.setattr(outlets, "enabled_platforms", lambda: list(outlet_upload_post.PLATFORMS))
    return up


def _b3_wire(up) -> Dict[tuple, List[tuple]]:
    """Every upload Upload-Post received, by (route, platform): its multipart parts, in order."""
    from test_marketing_outlet_upload_post import _parts

    out: Dict[tuple, List[tuple]] = {}
    for request in up.uploads:
        parts = _parts(request)
        key = (request.url.path[len("/api"):], dict(parts)["platform[]"])
        assert key not in out, f"{key} was uploaded twice"
        out[key] = parts
    return out


def _b3_sibling_reads(monkeypatch) -> List[Dict[str, Any]]:
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
async def test_e2e_batch3_two_formats_on_facebook_and_linkedin_travel_from_the_freeze_to_the_wire(
        bundles, tg, wakes, monkeypatch, caplog):
    from app.services.marketing import post_copy

    svc = bundles
    _b3_settings(monkeypatch, extras="facebook,linkedin", commented=("facebook",), features="push_in")
    sibling_reads = _b3_sibling_reads(monkeypatch)

    # 1. The write-time freeze: Facebook and LinkedIn keep their PICTURE post and gain the day's video, captioned
    #    with their own accepted caption under the narrated-video disclaimer.
    rec = await _b3_record(svc)
    day, out = rec.day, rec.output
    d = day.isoformat()
    assert out["post_formats"] == {"bluesky": "image", "facebook": "image", "instagram": "video", "linkedin": "image",
                                   "threads": "image", "tiktok": "video", "x": "text", "youtube": "video"}
    assert out["extra_post_formats"] == {"facebook": "video", "linkedin": "video"}
    reel = {p: post_copy.extra_video_caption(p, out["posts"][p]["caption"], day) for p in ("facebook", "linkedin")}
    assert out["video_posts"] == {p: {"title": None, "caption": c} for p, c in reel.items()}
    for p, caption in reel.items():
        text, narrated = post_copy.disclaimer_for(p, day), post_copy.disclaimer_for(p, day, video=True)
        assert text != narrated and caption == out["posts"][p]["caption"][:-len(text)] + narrated
        assert "Script and narration generated with AI." in caption and "Written with AI" not in caption
    assert out["video_features"] == ["push_in"]

    # 2. create_posts, called with the worker's PRIMARY specs only, appends the two extras itself — on the run's one
    #    video, held for review, with the primary video posts' video-features stamp and Facebook's first comment.
    n = len(rec.specs)
    assert all(s["format"] == "image" for s in rec.specs if s["platform"] in ("facebook", "linkedin"))
    assert [(p["platform"], p["format"]) for p in rec.created] == [
        *((s["platform"], s["format"]) for s in rec.specs), ("facebook", "video"), ("linkedin", "video")]
    tiktok = next(p for p in rec.created if p["platform"] == "tiktok")
    extras = {p["platform"]: p for p in rec.created[n:]}
    for platform, p in extras.items():
        md = p["metadata"]
        assert (p["status"], p["title"], p["asset_ids"]) == ("pending_review", None, [rec.video["id"]])
        assert p["caption"] == reel[platform] and p["idempotency_key"] == f"{d}:{platform}:video"
        assert md["made_with_ai"] is True and POST_METADATA_IMAGE_FALLBACK not in md
        assert md["video_features"] == tiktok["metadata"]["video_features"] == ["push_in"]
        assert md.get(POST_METADATA_FIRST_COMMENT) == (SEARCH_LINE if platform == "facebook" else None)
    assert sorted(p["platform"] for p in rec.created if "video_features" in p["metadata"]) == [
        "facebook", "instagram", "linkedin", "tiktok", "youtube"]
    assert len(_rows(svc, mrs.POSTS)) == n + 2

    # 3. The sweep: still TWO decisions. The VIDEO one carries the extras and says they wait; the POSTS one is
    #    exactly what it would be without them.
    counters = await rs.review_cycle()
    assert counters["bundles"] == 2 and counters["failed"] == 0
    video_decision, post_decision = _decision_messages(tg)
    (shown,) = tg.of("sendVideo")                                           # ONE video, naming every member
    assert shown["caption"].split("\n")[0] == f"VIDEO · INSTAGRAM, TIKTOK, YOUTUBE, FACEBOOK, LINKEDIN · run {d}"
    assert video_decision["text"].split("\n") == [
        f"DECISION · VIDEO · run {d}",
        "Approve all sends: INSTAGRAM (video), TIKTOK (video), YOUTUBE (video), FACEBOOK (video), LINKEDIN (video)",
        "First comment requested on: FACEBOOK",
        _WAIT + "FACEBOOK, LINKEDIN",
        _DROP_ONE_POST,
        "FACEBOOK, LINKEDIN also have a picture/text post: ✂ drop here rejects only the video.",
    ]
    assert post_decision["text"].split("\n") == [
        f"DECISION · POSTS · run {d}",
        "Approve all sends: BLUESKY (image), FACEBOOK (image), LINKEDIN (image), THREADS (image), X (text)",
        "First comment requested on: FACEBOOK",
        _DROP_ONE_POST,
        "FACEBOOK, LINKEDIN also have a video: ❌ Reject all or ✂ drop here rejects only the picture/text post — the "
        "video, once approved, then goes out alone.",
    ]
    # Each ✂ drop on Facebook / LinkedIn names the ONE post it rejects (round-1 fix DEPLOY-B3-DROP-TEXT).
    assert [b["text"] for row in video_decision["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop INSTAGRAM", "✂ drop TIKTOK", "✂ drop YOUTUBE", "✂ drop FACEBOOK video", "✂ drop LINKEDIN video"]
    assert [b["text"] for row in post_decision["reply_markup"]["inline_keyboard"][2:] for b in row] == [
        "✂ drop BLUESKY", "✂ drop FACEBOOK image", "✂ drop LINKEDIN image", "✂ drop THREADS", "✂ drop X"]

    # 4. Approve all on both decisions.
    for decision in (video_decision, post_decision):
        assert (await rs.handle_update(_tap_on(decision, "g")))["outcome"] == "decided"
    assert {r["status"] for r in _rows(svc, mrs.POSTS)} == {"approved"}

    # 5. Publisher pass 1: every primary post goes, each extra is HELD while its picture post is open — unclaimed,
    #    nothing written.
    up = _b3_publisher(svc, monkeypatch, rec.image)
    with caplog.at_level(logging.INFO, logger=pub.logger.name):
        first = await pub.publish_cycle()
    assert (first["submitted"], first["retry"], first["published"], first["failed"]) == (6, 2, 0, 0)
    assert sorted(_b3_wire(up)) == [("/upload", "instagram"), ("/upload", "tiktok"), ("/upload", "youtube"),
                                    ("/upload_photos", "facebook"), ("/upload_photos", "linkedin"),
                                    ("/upload_photos", "threads")]
    assert sorted(r["post_id"] for r in sibling_reads) == sorted(p["id"] for p in extras.values())
    for platform, p in extras.items():
        row = _post(svc, p["id"])
        assert (row["status"], row["attempts"]) == ("approved", 0) and "publish" not in row["metadata"]
        assert any(f"post_id={p['id']} platform={platform} format=video HELD: waits for its {platform} picture/text "
                   "post" in r.getMessage() for r in caplog.records)

    # 6. The pictures reconcile FOUND; pass 2 sends the two videos to /upload — a Reel and a LinkedIn video, the
    #    caption in their description field, never `title`; the first comment on Facebook only.
    pictures = [p["id"] for p in rec.created[:n] if p["platform"] in extras]
    for pid in pictures:
        _post(svc, pid)["metadata"]["publish"]["started_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=660)).isoformat()
    assert (await pub.reconcile_cycle())["found"] == 2
    assert {_post(svc, pid)["status"] for pid in pictures} == {"published"}
    second = await pub.publish_cycle()
    assert (second["submitted"], second["retry"], second["failed"]) == (2, 0, 0)
    wire = _b3_wire(up)
    video_url = svc.public_url(str(rec.video["storage_path"]))

    def own(platform: str) -> List[tuple]:
        return [("user", "caydex"), ("platform[]", platform), ("video", video_url), ("async_upload", "true"),
                ("request_id", f"{d}:{platform}:video:a1"), ("external_id", extras[platform]["id"])]

    assert wire[("/upload", "facebook")] == own("facebook") + [
        ("facebook_page_id", _B3_FB_PAGE), ("facebook_media_type", "REELS"), ("facebook_description", reel["facebook"]),
        ("facebook_is_ai_generated", "true"), ("facebook_first_comment", SEARCH_LINE)]
    assert wire[("/upload", "linkedin")] == own("linkedin") + [
        ("target_linkedin_page_id", _B3_LI_PAGE), ("linkedin_description", reel["linkedin"]), ("visibility", "PUBLIC")]
    for platform, p in extras.items():
        assert not {"title", f"{platform}_title", "description"} & {name for name, _v in wire[("/upload", platform)]}
        row = _post(svc, p["id"])
        assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "submitted"
        assert row["metadata"]["publish"]["upload_post"]["request_id"] == f"{d}:{platform}:video:a1"
    assert up.unexpected == []


@pytest.mark.asyncio
async def test_e2e_batch3_switch_empty_keeps_the_batch1_e2e_byte_identical(bundles, tg, wakes, monkeypatch, caplog):
    """MARKETING_VIDEO_EXTRA_PLATFORMS empty — its default — with the Batch 1 e2e's own settings: the first comments on
    YouTube, Facebook and Threads, and a Threads caption over 500 UTF-8 bytes (so Threads posts TEXT,
    `threads_caption_long`). No Batch 3 key is frozen, create_posts records exactly the worker's specs, every
    Telegram message of the sweep and every Upload-Post request is pinned byte for byte, and the publisher never reads
    a sibling nor holds a post: nothing waits behind the day's posts."""
    svc = bundles
    _b3_settings(monkeypatch, extras="", commented=_E2E_COMMENTED, features="")
    sibling_reads = _b3_sibling_reads(monkeypatch)
    long_threads = ("Save — invest — wait — repeat. " * 14).strip()
    assert len(long_threads) <= 500 < len(long_threads.encode("utf-8"))

    # 1. The freeze and create_posts: the Batch 1 shape, no Batch 3 key, one row per worker spec.
    rec = await _b3_record(svc, threads_caption=long_threads)
    day, out = rec.day, rec.output
    d = day.isoformat()
    assert out["post_formats"] == {"bluesky": "image", "facebook": "image", "instagram": "video", "linkedin": "image",
                                   "threads": "text", "tiktok": "video", "x": "text", "youtube": "video"}
    assert out["image_fallback"] == {"reason": "threads_caption_long", "platforms": ["threads"]}
    assert sorted(out) == ["cards", "disclaimer_card", "image_fallback", "image_footer", "image_post", "judge",
                           "post_formats", "posts", "store_state"]
    assert [(p["platform"], p["format"]) for p in rec.created] == [(s["platform"], s["format"]) for s in rec.specs]
    assert len(_rows(svc, mrs.POSTS)) == len(rec.specs) == 8
    notes = {p["platform"]: p["metadata"] for p in rec.created}
    assert {p: m.get(POST_METADATA_FIRST_COMMENT) for p, m in notes.items()} == {
        p: (SEARCH_LINE if p in _E2E_COMMENTED else None) for p in _E2E_PLATFORMS}
    assert {p: m.get(POST_METADATA_IMAGE_FALLBACK) for p, m in notes.items()} == {
        p: ("threads_caption_long" if p == "threads" else None) for p in _E2E_PLATFORMS}
    assert not any("video_features" in m for m in notes.values())

    # 2. The sweep, byte for byte: the video, a caption message per platform, the VIDEO decision; the picture, a
    #    caption message per platform, the POSTS decision. No wait line anywhere.
    counters = await rs.review_cycle()
    assert counters == {"pending": 8, "sent": 8, "stamped": 8, "videos": 1, "failed": 0, "rate_limited": 0,
                        "bundles": 2, "images": 1, "held": 0}
    video_decision, post_decision = _decision_messages(tg)
    caption = {p: out["posts"][p]["caption"] for p in _E2E_PLATFORMS}
    ids = {(p["platform"], p["format"]): p["id"] for p in rec.created}
    video_url = svc.public_url(str(rec.video["storage_path"]))
    card_url = svc.public_url(str(rec.image["storage_path"]))
    quiet = {"chat_id": OWNER, "link_preview_options": {"is_disabled": True}, "disable_notification": True}

    def shown(platform: str, fmt: str, text: str) -> tuple:
        return "sendMessage", {**quiet, "text": f"CAPTION · {platform.upper()} ({fmt}) · run {d}\n\n{text}"}

    def decision(text: str, bundle: Dict[str, Any], members: List[tuple]) -> tuple:
        bid = _bundle_id(bundle)
        keyboard = [[{"text": f"✅ Approve all ({len(members)})", "callback_data": f"g:{bid}"}],
                    [{"text": "❌ Reject all", "callback_data": f"j:{bid}"}],
                    *[[{"text": f"✂ drop {p.upper()}", "callback_data": f"s:{ids[(p, f)]}"}] for p, f in members]]
        return "sendMessage", {"chat_id": OWNER, "text": text, "link_preview_options": {"is_disabled": True},
                               "reply_markup": {"inline_keyboard": keyboard}}

    video_members = [("instagram", "video"), ("tiktok", "video"), ("youtube", "video")]
    post_members = [("bluesky", "image"), ("facebook", "image"), ("linkedin", "image"), ("threads", "text"),
                    ("x", "text")]
    assert tg.calls[:12] == [
        ("sendVideo", {"chat_id": OWNER, "video": video_url, "supports_streaming": True,
                       "caption": f"VIDEO · INSTAGRAM, TIKTOK, YOUTUBE · run {d}\nThe captions and your decision follow.",
                       "disable_notification": True}),
        shown("instagram", "video", caption["instagram"]),
        shown("tiktok", "video", caption["tiktok"]),
        shown("youtube", "video", f"{out['posts']['youtube']['title']}\n\n{caption['youtube']}"),
        decision("\n".join([f"DECISION · VIDEO · run {d}",
                            "Approve all sends: INSTAGRAM (video), TIKTOK (video), YOUTUBE (video)",
                            "First comment requested on: YOUTUBE", _B3_DROP]), video_decision, video_members),
        ("sendPhoto", {"chat_id": OWNER, "photo": card_url,
                       "caption": f"IMAGE · BLUESKY, FACEBOOK, LINKEDIN · run {d}\nThe captions and your decision follow.",
                       "disable_notification": True}),
        *(shown(p, f, caption[p]) for p, f in post_members),
        decision("\n".join([f"DECISION · POSTS · run {d}",
                            "Approve all sends: BLUESKY (image), FACEBOOK (image), LINKEDIN (image), THREADS (text), "
                            "X (text)",
                            f"Picture not used today: {IMAGE_FALLBACK_REASONS['threads_caption_long']} (THREADS)",
                            "First comment requested on: FACEBOOK, THREADS", _B3_DROP]), post_decision, post_members),
    ]
    assert len(tg.calls) == 12

    # 3. Approve all on both, then the publisher: every Upload-Post post goes in ONE pass — nothing held, no sibling
    #    read — each request pinned byte for byte.
    for bundle in (video_decision, post_decision):
        assert (await rs.handle_update(_tap_on(bundle, "g")))["outcome"] == "decided"
    up = _b3_publisher(svc, monkeypatch, rec.image)
    with caplog.at_level(logging.INFO, logger=pub.logger.name):
        first = await pub.publish_cycle()
    assert first == {"approved_waiting": 6, "published": 0, "submitted": 6, "failed": 0, "skipped": 0, "retry": 0,
                     "unknown": 0, "capped": 0}
    assert sibling_reads == [] and not [r for r in caplog.records if "HELD" in r.getMessage()]

    def video(platform: str) -> List[tuple]:
        return [("user", "caydex"), ("platform[]", platform), ("video", video_url), ("async_upload", "true"),
                ("request_id", f"{d}:{platform}:video:a1"), ("external_id", ids[(platform, "video")])]

    def other(platform: str, fmt: str) -> List[tuple]:
        photo = [("photos[]", card_url)] if fmt == "image" else []
        return [("user", "caydex"), ("platform[]", platform), ("title", caption[platform]), *photo,
                ("async_upload", "true"), ("request_id", f"{d}:{platform}:{fmt}:a1"),
                ("external_id", ids[(platform, fmt)])]

    yt_title = out["posts"]["youtube"]["title"]
    alt = "\n\n".join([fake_db._IMAGE_POST["title"], *fake_db._IMAGE_POST["paragraphs"]])
    assert _b3_wire(up) == {
        ("/upload", "instagram"): video("instagram") + [
            ("title", caption["instagram"]), ("instagram_title", caption["instagram"]), ("media_type", "REELS"),
            ("share_to_feed", "true"), ("is_ai_generated", "true"), ("thumb_offset", "500")],
        ("/upload", "tiktok"): video("tiktok") + [
            ("title", caption["tiktok"]), ("tiktok_title", caption["tiktok"]), ("privacy_level", "PUBLIC_TO_EVERYONE"),
            ("post_mode", "DIRECT_POST"), ("disable_inbox_fallback", "true"), ("brand_organic_toggle", "true"),
            ("is_aigc", "true"), ("cover_timestamp", "500")],
        ("/upload", "youtube"): video("youtube") + [
            ("title", yt_title), ("youtube_title", yt_title), ("youtube_description", caption["youtube"]),
            ("privacyStatus", "public"), ("containsSyntheticMedia", "true"), ("categoryId", "27"),
            ("selfDeclaredMadeForKids", "false"), ("youtube_first_comment", SEARCH_LINE)],
        ("/upload_photos", "facebook"): other("facebook", "image") + [
            ("facebook_page_id", _B3_FB_PAGE), ("facebook_first_comment", SEARCH_LINE), ("facebook_alt_text", alt)],
        ("/upload_photos", "linkedin"): other("linkedin", "image") + [
            ("target_linkedin_page_id", _B3_LI_PAGE), ("linkedin_alt_text", alt)],
        ("/upload_text", "threads"): other("threads", "text") + [
            ("threads_long_text_as_post", "true"), ("threads_first_comment", SEARCH_LINE)],
    }

    # 4. Everything reconciles FOUND; a second pass has nothing left to send.
    for row in _rows(svc, mrs.POSTS):
        if row["status"] == "queued":
            row["metadata"]["publish"]["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=660)).isoformat()
    assert (await pub.reconcile_cycle())["found"] == 6
    second = await pub.publish_cycle()
    assert (second["submitted"], second["retry"]) == (0, 0) and len(up.uploads) == 6 and sibling_reads == []
    assert up.unexpected == []


# ── Claude as a second provider (llm_provider, 2026-10-30) ───────────────────────────────────


def test_the_decision_text_names_the_models_only_when_the_posts_carry_them(configured):
    from app.services.marketing.run_service import written_by_note

    note = written_by_note({"served_by": "claude:claude-sonnet-5-5\nINJECTED",
                            "judge": {"served_by": "gemini (Claude fallback: ClaudeRefusalException)"}})
    assert note == {"writer": "claude:claude-sonnet-5-5 INJECTED",
                    "judge": "gemini (Claude fallback: ClaudeRefusalException)"}
    for absent in (None, {}, {"served_by": " "}, {"judge": "x"}, {"judge": {"served_by": 3}}):
        assert written_by_note(absent) is None
    routed = _row("x", "text")
    routed["metadata"] = {**(routed.get("metadata") or {}), "written_by": note}
    text = rs.compose_bundle_decision_text("post", [routed], [], "2026-10-30", False, [])
    assert text.split("\n")[1] == ("Models: writer claude:claude-sonnet-5-5 INJECTED · "
                                   "judge gemini (Claude fallback: ClaudeRefusalException)")
    plain = rs.compose_bundle_decision_text("post", [_row("x", "text")], [], "2026-10-30", False, [])
    assert "Models:" not in plain
