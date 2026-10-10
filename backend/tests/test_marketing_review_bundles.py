"""
Review BUNDLES (drop 1, 2026-10-09; contract C10): two Telegram decisions a posting day instead of one
per post — `review_service` (sweep + webhook), `run_service.review_bundle` / `bundle_posts` and
`telegram.send_photo`.

Hermetic: Telegram is the `FakeTelegram` MockTransport from `test_marketing_review_bot.py` (wrapped to
remember each answer's message id), the ledger is the in-memory PostgREST fake from
`test_marketing_run_service.py` — with its `json->>key` text path widened to the nested
`metadata->review_bundle->>id` path the bundle fence filters on (as PostgREST does).

What must never regress:
  * nothing is offered before the run reached `media_ready`; a run's posts become at most TWO bundles
    (video; image/text), each: media → one message per DISTINCT caption → ONE decision message;
  * every member carries its bundle stamp BEFORE any button exists; a stamp that does not land sends
    nothing; a member is stamped notified only while it still carries that bundle;
  * a decision is ONE conditional UPDATE fenced on status AND the bundle id — a member decided
    elsewhere, re-sent in a newer bundle, or whose text changed, is never flipped; a replay decides
    nothing; previews (unwired platforms) are never members;
  * ❌ Reject all offers the reasons once and a reason lands on every rejected member; ✂ drop rejects one
    member and keeps the rest open on the same message;
  * MARKETING_REVIEW_BUNDLES off → the per-post flow (the whole of test_marketing_review_bot.py runs
    with it off and is unchanged).
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
from app.schemas.marketing import POST_PLATFORMS
from app.services.marketing import review_service as rs
from app.services.marketing import run_service as mrs
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
@pytest.mark.parametrize("status", ["in_progress", "failed", "planned"])
async def test_nothing_is_offered_before_the_run_is_media_ready(bundles, tg, status):
    ids = _seed_day(bundles, run_status=status)
    counters = await rs.review_cycle()
    assert counters["held"] == 8 and counters["sent"] == 0 and tg.calls == []
    assert all("review_bundle" not in _meta(bundles, pid) for pid in ids.values())
    next(r for r in _rows(bundles, mrs.RUNS) if r["id"] == RUN_A)["status"] = "media_ready"
    assert (await rs.review_cycle())["bundles"] == 2


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


@pytest.mark.asyncio
async def test_the_dry_run_and_the_web_blockers_reach_every_bundle_message(bundles, tg, monkeypatch):
    _seed_day(bundles, dry_run=True)
    monkeypatch.setattr(settings, "MARKETING_ENABLED", False)
    await rs.review_cycle()
    assert "· DRY RUN" in tg.of("sendVideo")[0]["caption"] and "· DRY RUN" in tg.of("sendPhoto")[0]["caption"]
    assert all("· DRY RUN" in m["text"].split("\n")[0] for m in tg.of("sendMessage"))
    # rows of a dry run are still live rows here ({"dry_run": False}), so the web switch warns
    assert "⚠️ publishing is OFF on the web: approving will not send TIKTOK, YOUTUBE, INSTAGRAM" in \
        _decision_messages(tg)[0]["text"]


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
    labels — never the stored strings themselves."""
    ids = _seed_day(bundles)
    run = next(r for r in _rows(bundles, mrs.RUNS) if r["id"] == RUN_A)
    run["metadata"]["series"] = "money_map"
    run["metadata"]["series_trail"] = [
        {"series": "ceo_buys", "outcome": "no_candidates", "reason": "<b>raw</b>"},
        {"series": "insider_buys", "outcome": "unavailable", "reason": "insider_feed_empty"},
        {"series": "money_map", "outcome": "chosen"}]
    assert (await rs.review_cycle())["bundles"] == 2
    for decision in _decision_messages(tg):
        lines = decision["text"].split("\n")
        assert lines[1] == "Series: Money Map (fell back from CEO Buys: nothing qualified)", lines
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
