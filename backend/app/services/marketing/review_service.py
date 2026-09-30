"""
Marketing REVIEW BOT — the owner approves / rejects each day's posts from Telegram (design doc §12.9).

Why Telegram: a human approves every post while the semantic judge misses its calibration gate
(rules/marketing.md §7a), and the owner reviews from a phone. marketing.md §8 forbids rendering
model text on caydexinvest.com (the passkey domain), so there is no review page of ours: the chat
app renders the text and plays the video.

Two halves, both in the WEB process (the only one holding the bot token; the worker never sees it):

* `review_cycle()` — run by the publisher loop every cycle, independently of MARKETING_ENABLED.
  It finds the `pending_review` posts nobody has been told about (no `metadata.review_notified_at`),
  groups them by run, sends the run's verified video first (by URL, or a link when Telegram cannot
  fetch it), then ONE message per post — "<PLATFORM> · <format> · run <date>" (+ "· DRY RUN"), the
  title and the exact caption — with ✅ Approve / ❌ Reject buttons, and stamps the post notified
  with a conditional UPDATE. Delivery is AT LEAST ONCE: a failure between the send and the stamp
  sends the post again next cycle (logged WARNING); it can never be lost silently.
* `handle_update(update)` — the webhook body after `app/main.py` has verified Telegram's
  secret-token header. Only callback queries; only from the allow-listed owner, in the owner's
  own chat; strict `a|r:<uuid>` data. It calls `run_service.review_post` (a conditional UPDATE on
  `status = pending_review`, so a double tap or a Telegram retry can never flip a decided post),
  answers the tap with a toast and edits the message to show the verdict, keyboard removed.
  Telegram failures while answering / editing are logged and never change the decision.

The bot is OFF unless all three MARKETING_TELEGRAM_* settings are set (`is_configured`).

Plain text only: no `parse_mode` anywhere, so a caption (model text) can never be read as markup.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.integrations import telegram
from app.integrations.telegram import (
    MAX_CALLBACK_DATA_BYTES,
    MAX_CAPTION_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_URL_SEND_BYTES,
    TelegramException,
    TelegramRateLimitException,
    TelegramRequestError,
)
from app.services.marketing.run_service import (
    POSTS,
    _exec,
    _now_iso,
    _one,
    _parse_ts,
    _ts_filter,
    get_marketing_run_service,
)
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

#: The root route Telegram delivers button taps to (registered in app/main.py).
WEBHOOK_PATH = "/marketing/telegram/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
#: A callback-query update is a few KB (the message it came from is ≤ 4096 chars). Anything
#: larger is not Telegram talking to this bot.
MAX_WEBHOOK_BODY_BYTES = 64 * 1024
#: Unnotified pending posts read per cycle, oldest first. The "not yet notified" filter runs IN
#: the query, before the LIMIT: a filter applied afterwards would let 50 already-notified posts
#: the owner has not decided yet fill the window, and every newer post would never be announced
#: (the publisher's starvation bug, `list_posts` docstring).
SCAN_LIMIT = 50
#: Telegram FAQ: "In a single chat, avoid sending more than one message per second".
SEND_SPACING_SECONDS = 1.1
_WEBHOOK_REGISTER_TIMEOUT_SECONDS = 20.0

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CALLBACK_RE = re.compile(rf"(a|r):({_UUID})", re.ASCII)
_STATUS_WORD_RE = re.compile(r"[a-z_]{1,32}", re.ASCII)
_KEYBOARD_REMOVED: Dict[str, Any] = {"inline_keyboard": []}

#: Monotonic time before which the sweep does not call Telegram (set by a 429's retry_after).
_rate_limited_until = 0.0


# ── configuration ──────────────────────────────────────────────────────────────


def _is_int(value: Any) -> bool:
    """A real int — `True` is an int to Python and `"123"` equals nothing; both are refused."""
    return type(value) is int


def review_chat_id() -> Optional[int]:
    value = settings.MARKETING_TELEGRAM_REVIEW_CHAT_ID
    return value if _is_int(value) else None


def is_configured() -> bool:
    """All three settings present AND usable: a webhook secret Telegram accepts ([A-Za-z0-9_-],
    1-256) and an https public base URL. Anything less and the bot is OFF (fail-closed) — it used
    to send review messages whose buttons could never reach us when setWebhook had refused the
    secret (review 2026-09-29). Misconfiguration is logged by `register_webhook` at startup."""
    return (
        bool(settings.MARKETING_TELEGRAM_BOT_TOKEN)
        and review_chat_id() is not None
        and telegram.is_valid_secret_token(settings.MARKETING_TELEGRAM_WEBHOOK_SECRET)
        and str(settings.MARKETING_PUBLIC_BASE_URL or "").startswith("https://")
    )


# ── pure helpers (tested directly) ─────────────────────────────────────────────


def utf16_len(text: str) -> int:
    """Telegram counts message length in UTF-16 code units (an emoji outside the BMP is 2)."""
    return len(text.encode("utf-16-le")) // 2


def _truncate_utf16(text: str, limit: int) -> str:
    """The longest prefix of `text` within `limit` UTF-16 units, never splitting a character."""
    units = 0
    for idx, ch in enumerate(text):
        width = 2 if ord(ch) > 0xFFFF else 1
        if units + width > limit:
            return text[:idx]
        units += width
    return text


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> List[str]:
    """Chunks of ≤ `limit` UTF-16 units. Prefers a newline, then a space, in the second half of the
    window; otherwise a hard cut (on a character boundary). Whitespace-only chunks are dropped
    (Telegram refuses an empty message)."""
    chunks: List[str] = []
    rest = text
    while utf16_len(rest) > limit:
        window = _truncate_utf16(rest, limit)
        if not window:  # defensive: limit < 2 with a wide character first
            window = rest[:1]
        half = len(window) // 2
        soft = window.rfind("\n")
        if soft < half:
            soft = window.rfind(" ")
        if soft >= half and soft > 0:
            chunks.append(rest[:soft])
            rest = rest[soft + 1:]
        else:
            chunks.append(window)
            rest = rest[len(window):]
    chunks.append(rest)
    return [c for c in chunks if c.strip()]


def canonical_post_id(value: Any) -> Optional[str]:
    """The canonical lowercase uuid string, or None. Every button's data is built from this,
    so a tap always parses back to the same id."""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def callback_data(decision: str, post_id: str) -> str:
    verb = {"approve": "a", "reject": "r"}[decision]
    data = f"{verb}:{post_id}"
    if parse_callback_data(data) != (decision, post_id):
        # Only a canonical uuid round-trips; a button that could never be parsed back is refused
        # here rather than shipped as a dead button.
        raise ValueError(f"callback data for post {post_id!r} would not round-trip")
    return data


def parse_callback_data(data: Any) -> Optional[Tuple[str, str]]:
    """`a:<uuid>` → ("approve", uuid); `r:<uuid>` → ("reject", uuid); anything else → None.
    Strict: canonical lowercase uuid only, ≤ 64 bytes, a FULL match (no trailing newline)."""
    if not isinstance(data, str) or len(data.encode("utf-8", "replace")) > MAX_CALLBACK_DATA_BYTES:
        return None
    match = _CALLBACK_RE.fullmatch(data)
    if match is None:
        return None
    return ("approve" if match.group(1) == "a" else "reject"), match.group(2)


def review_keyboard(post_id: str) -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "✅ Approve", "callback_data": callback_data("approve", post_id)}],
        [{"text": "❌ Reject", "callback_data": callback_data("reject", post_id)}],
    ]}


def is_rehearsal(post: Dict[str, Any]) -> bool:
    """The publisher's own rule: a row is live only when `metadata.dry_run` is exactly False
    (`create_posts` writes it on every row; a row without it is never sent)."""
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    return meta.get("dry_run") is not False


def _notified(post: Dict[str, Any]) -> bool:
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    return bool(meta.get("review_notified_at"))


def _run_date(run: Optional[Dict[str, Any]], post: Dict[str, Any]) -> str:
    if run and run.get("run_date"):
        return str(run["run_date"])[:10]
    key = str(post.get("idempotency_key") or "")
    return key.split(":", 1)[0][:10] if ":" in key else "?"


def compose_post_text(post: Dict[str, Any], run_date: str, media: List[Tuple[str, str]]) -> str:
    """Header, blank line, title (if any), the EXACT caption, then the post's media links."""
    header = f"{str(post.get('platform') or '?').upper()} · {post.get('format') or '?'} · run {run_date}"
    if is_rehearsal(post):
        header += " · DRY RUN"
    parts = [header]
    title = post.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())
    caption = post.get("caption")
    parts.append(caption if isinstance(caption, str) and caption.strip() else "(no caption)")
    if media:
        parts.append("Media:\n" + "\n".join(f"{kind}: {url}" for kind, url in media))
    return "\n\n".join(parts)


def append_verdict(original: Any, verdict: str) -> str:
    """The message as it was plus the verdict, within Telegram's 4096 (the original is trimmed
    with an ellipsis if the two would not fit)."""
    base = original if isinstance(original, str) else ""
    tail = f"\n\n{verdict}" if base else verdict
    budget = MAX_MESSAGE_CHARS - utf16_len(tail)
    if utf16_len(base) > budget:
        base = _truncate_utf16(base, max(budget - 1, 0)) + "…"
    return base + tail


def _et_clock(when: Optional[datetime] = None) -> str:
    return (when or datetime.now(ET)).astimezone(ET).strftime("%H:%M ET")


def _verdict(outcome: str, row: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(toast, line appended to the message) for a `review_post` outcome."""
    if outcome == "approved":
        return "Approved ✅", f"✅ Approved {_et_clock()}"
    if outcome == "rejected":
        return "Rejected ❌", f"❌ Rejected {_et_clock()}"
    if outcome == "not_found":
        return "Post not found", "Post not found"
    status = outcome[len("already_"):] if outcome.startswith("already_") else ""
    if not _STATUS_WORD_RE.fullmatch(status):
        status = "decided"
    meta = (row or {}).get("metadata") if isinstance((row or {}).get("metadata"), dict) else {}
    review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
    at = _parse_ts(review.get("at"))
    line = f"Already {status}" + (f" ({_et_clock(at)})" if at else "")
    return line, line


def _message_id(envelope: Any) -> Optional[int]:
    result = envelope.get("result") if isinstance(envelope, dict) else None
    mid = result.get("message_id") if isinstance(result, dict) else None
    return mid if _is_int(mid) else None


# ── the sweep ──────────────────────────────────────────────────────────────────


class _Pacer:
    """Spaces consecutive sends to the one chat (Telegram: ≤ 1 message per second)."""

    def __init__(self) -> None:
        self._last: Optional[float] = None

    async def wait(self) -> None:
        if self._last is not None:
            delay = SEND_SPACING_SECONDS - (time.monotonic() - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
        self._last = time.monotonic()


async def _pending_unnotified(svc: Any) -> List[Dict[str, Any]]:
    query = (
        svc.sb.table(POSTS).select("*")
        .eq("status", "pending_review")
        .is_("metadata->>review_notified_at", "null")
        .order("created_at")
        .limit(SCAN_LIMIT)
    )
    res = await _exec(query, op="review_pending_posts", status="pending_review")
    rows = list(getattr(res, "data", None) or [])
    # Belt and braces behind the query filter (a row stamped between the read and now).
    return [r for r in rows if isinstance(r, dict) and not _notified(r)]


def _pick_video(run_id: str, run: Dict[str, Any], assets: List[Dict[str, Any]],
                posts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The run's verified video: a READY video asset OF THIS RUN named by one of the posts'
    asset_ids, else by the run's `metadata.video_asset_id`. Anything else is not shown."""
    ready = {
        str(a.get("id")): a for a in assets
        if a.get("kind") == "video" and a.get("status") == "ready" and a.get("storage_path")
        and str(a.get("run_id") or run_id) == str(run_id)
    }
    candidates = [str(aid) for p in posts for aid in (p.get("asset_ids") or [])]
    meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    if meta.get("video_asset_id"):
        candidates.append(str(meta["video_asset_id"]))
    return next((ready[c] for c in candidates if c in ready), None)


def _post_media(svc: Any, post: Dict[str, Any], assets: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str]]:
    media = []
    for aid in post.get("asset_ids") or []:
        asset = assets.get(str(aid))
        if asset and asset.get("status") == "ready" and asset.get("storage_path"):
            media.append((str(asset.get("kind") or "file"), svc.public_url(str(asset["storage_path"]))))
    return media


async def _send_video_link(chat_id: int, caption: str, url: str, pacer: _Pacer, *, run_id: str) -> bool:
    try:
        await pacer.wait()
        await telegram.send_message(chat_id, f"{caption}\n{url}")
        return True
    except TelegramRateLimitException:
        raise
    except TelegramException as e:
        logger.warning("marketing review: video LINK for run_id=%s not sent (%s: %s) — the run's posts "
                       "wait for the next cycle", run_id, type(e).__name__, e)
        return False


async def _send_run_video(svc: Any, chat_id: int, run_id: str, run_date: str, video: Dict[str, Any],
                          dry: bool, pacer: _Pacer) -> bool:
    """Send the video (or, when Telegram cannot fetch it or the send is ambiguous, a link to it).
    False = neither went out; the run's posts are then held back a cycle — the reviewer must see
    the media first."""
    url = svc.public_url(str(video["storage_path"]))
    caption = f"VIDEO · run {run_date}" + (" · DRY RUN" if dry else "") + "\nThe posts that carry it follow."
    caption = caption[:MAX_CAPTION_CHARS]
    size = video.get("bytes")
    if _is_int(size) and size > MAX_URL_SEND_BYTES:
        logger.info("marketing review: video asset_id=%s of run_id=%s is %d bytes (> %d, Telegram's "
                    "URL-send limit) — sending a link instead", video.get("id"), run_id, size, MAX_URL_SEND_BYTES)
        return await _send_video_link(chat_id, caption + f"\n({size / 1_048_576:.1f} MB — open the link)", url,
                                      pacer, run_id=run_id)
    try:
        await pacer.wait()
        await telegram.send_video(chat_id, url, caption=caption)
        return True
    except TelegramRateLimitException:
        raise
    except TelegramRequestError as e:
        logger.warning("marketing review: sendVideo refused for asset_id=%s run_id=%s (%s) — sending a link "
                       "instead", video.get("id"), run_id, e)
        return await _send_video_link(chat_id, caption, url, pacer, run_id=run_id)
    except TelegramException as e:
        # AMBIGUOUS: a timeout or a 5xx may come AFTER Telegram fetched and delivered the video.
        # Holding the run's posts here re-sent the same video every cycle while its posts never
        # went out (review 2026-09-29). So send the LINK (a plain message — cheap and quick) and let
        # the posts follow; at worst the owner sees the video twice. Only when the link also fails
        # are the posts held for the next cycle.
        logger.warning("marketing review: sendVideo failed for asset_id=%s run_id=%s (%s: %s) — sending "
                       "a link instead", video.get("id"), run_id, type(e).__name__, e)
        return await _send_video_link(chat_id, caption, url, pacer, run_id=run_id)


async def _stamp_notified(svc: Any, post_id: str, message_ids: List[Optional[int]]) -> bool:
    """Record that the owner has been told — only while the post is STILL pending_review and
    unchanged since we read it (fenced on status AND updated_at), so a decision that landed
    meanwhile is never overwritten and no other metadata is lost."""
    try:
        fresh = await svc.get_post(post_id)
        if fresh is None:
            logger.warning("marketing review: post_id=%s vanished after it was sent; not stamped", post_id)
            return False
        if fresh.get("status") != "pending_review":
            logger.info("marketing review: post_id=%s was decided (%s) while it was being sent; not stamped",
                        post_id, fresh.get("status"))
            return False
        meta = dict(fresh["metadata"]) if isinstance(fresh.get("metadata"), dict) else {}
        now = _now_iso()
        meta["review_notified_at"] = now
        meta["review_message_id"] = message_ids[-1] if message_ids else None
        if len(message_ids) > 1:
            meta["review_message_ids"] = list(message_ids)
        query = (svc.sb.table(POSTS).update({"metadata": meta, "updated_at": now})
                 .eq("id", post_id).eq("status", "pending_review"))
        if fresh.get("updated_at"):
            query = query.eq("updated_at", _ts_filter(fresh["updated_at"]))
        updated = _one(await _exec(query, op="review_notify_stamp", post_id=post_id))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review: post_id=%s was SENT but the notified stamp failed (%s: %s) — it "
                       "will be sent again next cycle (at-least-once)", post_id, type(e).__name__, e)
        return False
    if updated is None:
        logger.warning("marketing review: post_id=%s changed between the read and the stamp; not stamped "
                       "(re-sent next cycle if it is still pending)", post_id)
        return False
    return True


async def review_cycle() -> Dict[str, int]:
    """One sweep. Never raises (except CancelledError); returns counters for the loop's log line."""
    global _rate_limited_until
    counters = {"pending": 0, "sent": 0, "stamped": 0, "videos": 0, "failed": 0, "rate_limited": 0}
    chat_id = review_chat_id()
    if not is_configured() or chat_id is None:
        return counters
    if time.monotonic() < _rate_limited_until:
        counters["rate_limited"] = 1
        logger.info("marketing review: Telegram flood control still in effect for %.0f s; sweep skipped",
                    _rate_limited_until - time.monotonic())
        return counters
    svc = get_marketing_run_service()
    try:
        pending = await _pending_unnotified(svc)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review: could not read pending posts (%s: %s)", type(e).__name__, e, exc_info=True)
        counters["failed"] += 1
        return counters
    counters["pending"] = len(pending)
    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for post in pending:
        groups.setdefault(str(post.get("run_id")), []).append(post)

    pacer = _Pacer()
    try:
        for run_id, posts in groups.items():
            try:
                await _review_run(svc, chat_id, run_id, posts, pacer, counters)
            except (TelegramRateLimitException, asyncio.CancelledError):
                raise
            except Exception as e:
                # A malformed row (asset_ids not a list, a storage_path that is not a string) must
                # cost its own run this cycle, never the whole sweep.
                logger.error("marketing review: run_id=%s could not be reviewed (%s: %s) — its post(s) wait "
                             "for the next cycle", run_id, type(e).__name__, e, exc_info=True)
                counters["failed"] += 1
    except TelegramRateLimitException as e:
        wait = e.retry_after if e.retry_after is not None else 60
        _rate_limited_until = time.monotonic() + wait
        counters["rate_limited"] = 1
        logger.warning("marketing review: Telegram flood control (retry_after=%s s) — sweep stopped; the rest "
                       "go out on a later cycle", e.retry_after)
    return counters


async def _review_run(svc: Any, chat_id: int, run_id: str, posts: List[Dict[str, Any]], pacer: _Pacer,
                      counters: Dict[str, int]) -> None:
    """One run's video (if it has a verified one), then its posts, oldest first."""
    try:
        run = await svc.get_run(run_id) or {}
        assets = await svc.list_assets(run_id) if run else []
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review: could not read run_id=%s (%s: %s) — its %d post(s) wait for "
                       "the next cycle", run_id, type(e).__name__, e, len(posts))
        counters["failed"] += len(posts)
        return
    run_date = _run_date(run, posts[0])
    by_id = {str(a.get("id")): a for a in assets if isinstance(a, dict)}
    video = _pick_video(run_id, run, list(by_id.values()), posts)
    if video is not None:
        dry = bool(run.get("dry_run")) or any(is_rehearsal(p) for p in posts)
        if not await _send_run_video(svc, chat_id, run_id, run_date, video, dry, pacer):
            counters["failed"] += len(posts)
            return
        counters["videos"] += 1
    for post in posts:
        await _notify_post(svc, chat_id, post, run_date, by_id, pacer, counters)


async def _notify_post(svc: Any, chat_id: int, post: Dict[str, Any], run_date: str,
                       assets: Dict[str, Dict[str, Any]], pacer: _Pacer, counters: Dict[str, int]) -> None:
    post_id = canonical_post_id(post.get("id"))
    if post_id is None:
        logger.error("marketing review: post id %r is not a uuid — no button could name it; skipped",
                     post.get("id"))
        counters["failed"] += 1
        return
    text = compose_post_text(post, run_date, _post_media(svc, post, assets))
    chunks = split_message(text)
    message_ids: List[Optional[int]] = []
    try:
        for i, chunk in enumerate(chunks):
            markup = review_keyboard(post_id) if i == len(chunks) - 1 else None
            await pacer.wait()
            message_ids.append(_message_id(await telegram.send_message(chat_id, chunk, reply_markup=markup)))
    except TelegramRateLimitException:
        raise
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        logger.warning("marketing review: notify FAILED post_id=%s platform=%s run_date=%s after %d/%d "
                       "chunk(s) (%s: %s) — retried next cycle", post_id, post.get("platform"), run_date,
                       len(message_ids), len(chunks), type(e).__name__, e)
        counters["failed"] += 1
        return
    except Exception as e:
        logger.error("marketing review: notify FAILED post_id=%s (%s: %s)", post_id, type(e).__name__, e,
                     exc_info=True)
        counters["failed"] += 1
        return
    counters["sent"] += 1
    logger.info("marketing review: notified post_id=%s platform=%s run_date=%s message_id=%s chunks=%d",
                post_id, post.get("platform"), run_date, message_ids[-1] if message_ids else None, len(chunks))
    if await _stamp_notified(svc, post_id, message_ids):
        counters["stamped"] += 1


# ── the webhook ────────────────────────────────────────────────────────────────


async def _answer(callback_query_id: str, text: str, *, post_id: Optional[str] = None) -> None:
    try:
        await telegram.answer_callback_query(callback_query_id, text)
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        logger.warning("marketing review: answerCallbackQuery failed post_id=%s (%s: %s)",
                       post_id, type(e).__name__, e)
    except Exception as e:
        logger.error("marketing review: answerCallbackQuery raised post_id=%s (%s: %s)",
                     post_id, type(e).__name__, e, exc_info=True)


async def _edit(chat_id: int, message_id: Any, text: str, *, post_id: str) -> None:
    if not _is_int(message_id):
        logger.warning("marketing review: no message_id to edit for post_id=%s", post_id)
        return
    try:
        await telegram.edit_message_text(chat_id, message_id, text, reply_markup=_KEYBOARD_REMOVED)
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        logger.warning("marketing review: editMessageText failed post_id=%s message_id=%s (%s: %s) — the "
                       "decision stands", post_id, message_id, type(e).__name__, e)
    except Exception as e:
        logger.error("marketing review: editMessageText raised post_id=%s (%s: %s)", post_id,
                     type(e).__name__, e, exc_info=True)


async def handle_update(update: Any) -> Dict[str, Any]:
    """Apply one AUTHENTICATED webhook update. Never raises (except CancelledError): the route
    answers 200 whatever happens, because Telegram retries anything else forever."""
    if not isinstance(update, dict):
        logger.warning("marketing review webhook: update is a %s, not an object; ignored", type(update).__name__)
        return {"ok": True, "ignored": "not_an_object"}
    cq = update.get("callback_query")
    if cq is None:
        logger.info("marketing review webhook: update_id=%r carries no callback_query (keys=%s); ignored",
                    update.get("update_id"), sorted(str(k) for k in update)[:10])
        return {"ok": True, "ignored": "not_a_callback"}
    if not isinstance(cq, dict) or not isinstance(cq.get("id"), str) or not cq.get("id"):
        logger.warning("marketing review webhook: malformed callback_query in update_id=%r; ignored",
                       update.get("update_id"))
        return {"ok": True, "ignored": "malformed_callback"}
    cq_id = cq["id"]
    allowed = review_chat_id()
    if not is_configured() or allowed is None:
        logger.warning("marketing review webhook: a tap arrived but the bot is not fully configured "
                       "(MARKETING_TELEGRAM_*); nothing decided")
        return {"ok": True, "ignored": "not_configured"}

    sender = cq.get("from") if isinstance(cq.get("from"), dict) else {}
    message = cq.get("message") if isinstance(cq.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    from_id, chat_id = sender.get("id"), chat.get("id")
    if not (_is_int(from_id) and _is_int(chat_id) and from_id == allowed and chat_id == allowed):
        logger.warning("marketing review webhook: tap REFUSED — not the allow-listed owner "
                       "(from_id=%r chat_id=%r); nothing decided", from_id, chat_id)
        await _answer(cq_id, "Not allowed")
        return {"ok": True, "refused": "not_allowed"}

    parsed = parse_callback_data(cq.get("data"))
    if parsed is None:
        logger.warning("marketing review webhook: unknown callback data %r from from_id=%s; nothing decided",
                       str(cq.get("data"))[:80], from_id)
        await _answer(cq_id, "Unknown action")
        return {"ok": True, "refused": "unknown_action"}
    decision, post_id = parsed

    try:
        outcome, row = await get_marketing_run_service().review_post(
            post_id, decision, reviewed_by=f"telegram:{from_id}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review webhook: review_post FAILED post_id=%s decision=%s (%s: %s) — the "
                     "buttons stay; the owner can tap again", post_id, decision, type(e).__name__, e,
                     exc_info=True)
        await _answer(cq_id, "Could not record that — tap again", post_id=post_id)
        return {"ok": True, "outcome": "error", "post_id": post_id}

    toast, line = _verdict(outcome, row)
    logger.info("marketing review webhook: post_id=%s decision=%s outcome=%s by=telegram:%s",
                post_id, decision, outcome, from_id)
    await _answer(cq_id, toast, post_id=post_id)
    await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), line), post_id=post_id)
    return {"ok": True, "outcome": outcome, "post_id": post_id}


async def register_webhook() -> bool:
    """Best-effort `setWebhook` at startup (a one-shot task; never blocks or fails the boot).
    Idempotent on Telegram's side, so every restart and every instance may call it."""
    if not (settings.MARKETING_TELEGRAM_BOT_TOKEN and review_chat_id() is not None
            and settings.MARKETING_TELEGRAM_WEBHOOK_SECRET):
        logger.info("marketing review bot: not configured (MARKETING_TELEGRAM_*); webhook not registered")
        return False
    # Set but unusable is an ERROR, not "not configured": is_configured() also refuses these, so
    # the sweep stays off too — this line is the only place that says why.
    base = (settings.MARKETING_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base.startswith("https://"):
        logger.error("marketing review bot: MARKETING_PUBLIC_BASE_URL=%r is not https — Telegram delivers "
                     "webhooks over HTTPS only; NOT registered", base)
        return False
    secret = settings.MARKETING_TELEGRAM_WEBHOOK_SECRET
    if not telegram.is_valid_secret_token(secret):
        logger.error("marketing review bot: MARKETING_TELEGRAM_WEBHOOK_SECRET must be 1-256 characters of "
                     "[A-Za-z0-9_-] (Telegram refuses anything else); webhook NOT registered")
        return False
    url = f"{base}{WEBHOOK_PATH}"
    try:
        await asyncio.wait_for(telegram.set_webhook(url, secret, allowed_updates=("callback_query",)),
                               timeout=_WEBHOOK_REGISTER_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review bot: setWebhook FAILED (%s: %s) — button taps are not delivered "
                       "until a restart registers it", type(e).__name__, e)
        return False
    logger.info("marketing review bot: webhook registered at %s", url)
    return True
