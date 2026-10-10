"""
The marketing publisher's Telegram FEED (design doc §12.9-§12.10): what happened after an Approve.

Three kinds of message to the owner's review chat, each sent AT LEAST ONCE and stamped on the post
with a fenced, merging write (`run_service.transition_post`), exactly like the review sweep's
`review_notified_at`:

* **Posted** — "✅ Posted on X · <format> · run <date>" + the link, as a reply to the post's review
  message (a review bundle's decision message, when the post was decided in one), with a 🗑 Retract
  button when the platform can delete through its API. Stamp: `metadata.posted_notified_at`
  (+ `posted_message_id`, which the retract confirmation edits). The format (video / image / text,
  drop 1) tells an image post from the same platform's text post.
* **Retracted** — the posted message is edited to say "🗑 Retracted HH:MM ET" (keyboard removed),
  or a new message when it cannot be edited. Stamp: `metadata.retract_notified_at`.
* **Alerts** — `metadata.alert_kind` / `alert_text` written by the publisher (a refusal, attempts
  exhausted, a bad credential, the X cap, a retract that must be done by hand, an escalated
  unknown outcome — the last with "It's live" / "Not posted" buttons). Stamp:
  `metadata.alert_notified_at`.

A Telegram failure here is logged and retried next cycle; it NEVER changes a post's status (the
publisher owns statuses). Plain text only, no `parse_mode`. Bounded per tick, paced with the review
sweep's spacing, and it shares the review sweep's flood-control back-off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.integrations import telegram
from app.integrations.telegram import TelegramException, TelegramRateLimitException
from app.schemas.marketing import POST_FORMATS
from app.services.marketing import outlets, review_service
from app.services.marketing.review_service import (
    _Pacer,
    _et_clock,
    _is_int,
    _message_id,
    append_verdict,
    canonical_post_id,
    retract_keyboard,
    split_message,
    unknown_outcome_keyboard,
)
from app.services.marketing.run_service import POSTS, _exec, _now_iso, get_marketing_run_service

logger = logging.getLogger(__name__)

#: Telegram sends per tick, all kinds together (the publisher tick must stay short).
FEED_SEND_LIMIT = 10
_SCAN = 20
_KEYBOARD_REMOVED: Dict[str, Any] = {"inline_keyboard": []}


def _meta(post: Dict[str, Any]) -> Dict[str, Any]:
    return post["metadata"] if isinstance(post.get("metadata"), dict) else {}


def _run_date(post: Dict[str, Any]) -> str:
    key = str(post.get("idempotency_key") or "")
    return key[:10] if len(key) >= 10 else "?"


def _format(post: Dict[str, Any]) -> str:
    """The post's format (video / image / text …) when it is one the ledger knows, else "?" — never a
    raw column value in a message."""
    fmt = post.get("format")
    return fmt if isinstance(fmt, str) and fmt in POST_FORMATS else "?"


def posted_text(post: Dict[str, Any]) -> str:
    adapter = outlets.adapter_for(post.get("platform"))
    url = (adapter.post_url(post) if adapter else None) or post.get("external_url") or "(no link — the platform gave none)"
    lines = [f"✅ Posted on {str(post.get('platform') or '?').upper()} · {_format(post)} · run {_run_date(post)}",
             str(url)]
    if not outlets.retract_capable(post.get("platform")) or not post.get("external_id"):
        lines.append("(no Retract button: remove it by hand on the platform if needed)")
    return "\n".join(lines)


async def _stamp(svc: Any, post: Dict[str, Any], values: Dict[str, Any], *, retries: int = 1) -> bool:
    """Merge `values` into the post's metadata while it is still in the status we read it in.
    `retries=0` for an alert: a retry re-reads the row, and a NEWER alert written meanwhile would be
    stamped as sent without ever being sent (the next cycle re-sends the old one instead — harmless)."""
    try:
        updated = await svc.transition_post(str(post["id"]), expect_status=str(post.get("status")),
                                            observed=post, meta=values, retries=retries)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing feed: post_id=%s was SENT but the stamp failed (%s: %s) — it will be sent "
                       "again next cycle (at-least-once)", post.get("id"), type(e).__name__, e)
        return False
    if updated is None:
        logger.warning("marketing feed: post_id=%s changed before the stamp — re-sent next cycle if still due",
                       post.get("id"))
        return False
    return True


async def _rows(svc: Any, query: Any, op: str) -> List[Dict[str, Any]]:
    res = await _exec(query.order("updated_at").limit(_SCAN), op=op)
    return [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]


async def _send(chat_id: int, text: str, pacer: _Pacer, *, markup: Optional[Dict[str, Any]] = None,
                reply_to: Optional[int] = None) -> Optional[int]:
    """Send `text` (split at 4096); the keyboard rides on the last chunk. Returns its message id."""
    last: Optional[int] = None
    chunks = split_message(text) or [text[:100] or "…"]
    for i, chunk in enumerate(chunks):
        await pacer.wait()
        envelope = await telegram.send_message(
            chat_id, chunk, reply_markup=markup if i == len(chunks) - 1 else None,
            reply_to_message_id=reply_to if i == 0 else None)
        last = _message_id(envelope)
    return last


async def feed_cycle() -> Dict[str, int]:
    """One pass. Never raises (except CancelledError); returns counters for the tick's log line."""
    counters = {"posted": 0, "retracted": 0, "alerts": 0, "failed": 0, "rate_limited": 0}
    chat_id = review_service.review_chat_id()
    if not review_service.is_configured() or chat_id is None:
        return counters
    if time.monotonic() < review_service._rate_limited_until:
        counters["rate_limited"] = 1
        return counters
    svc = get_marketing_run_service()
    pacer = _Pacer()
    budget = FEED_SEND_LIMIT
    try:
        # 1. posted
        posted = await _rows(svc, svc.sb.table(POSTS).select("*").eq("status", "published")
                             .is_("metadata->>posted_notified_at", "null"), "feed_posted")
        for post in posted:
            if budget <= 0:
                break
            post_id = canonical_post_id(post.get("id"))
            if post_id is None:
                continue
            budget -= 1
            reply_to = _meta(post).get("review_message_id")
            markup = retract_keyboard(post_id) if (outlets.retract_capable(post.get("platform"))
                                                   and post.get("external_id")) else None
            try:
                mid = await _send(chat_id, posted_text(post), pacer, markup=markup,
                                  reply_to=reply_to if _is_int(reply_to) else None)
            except TelegramRateLimitException:
                raise
            except TelegramException as e:
                logger.warning("marketing feed: posted message for post_id=%s not sent (%s: %s)", post_id,
                               type(e).__name__, e)
                counters["failed"] += 1
                continue
            if await _stamp(svc, post, {"posted_notified_at": _now_iso(), "posted_message_id": mid}):
                counters["posted"] += 1

        # 2. retracted
        retracted = await _rows(svc, svc.sb.table(POSTS).select("*").eq("status", "retracted")
                                .is_("metadata->>retract_notified_at", "null"), "feed_retracted")
        for post in retracted:
            if budget <= 0:
                break
            budget -= 1
            line = f"🗑 Retracted {_et_clock()}"
            mid = _meta(post).get("posted_message_id")
            done = False
            if _is_int(mid):
                try:
                    await pacer.wait()
                    await telegram.edit_message_text(chat_id, mid, append_verdict(posted_text(post), line),
                                                     reply_markup=_KEYBOARD_REMOVED)
                    done = True
                except TelegramRateLimitException:
                    raise
                except TelegramException as e:
                    logger.info("marketing feed: could not edit the posted message of post_id=%s (%s) — sending "
                                "a new one", post.get("id"), e)
            if not done:
                try:
                    await _send(chat_id, f"{line} · {str(post.get('platform')).upper()} · {_format(post)} · "
                                         f"run {_run_date(post)}", pacer)
                except TelegramRateLimitException:
                    raise
                except TelegramException as e:
                    logger.warning("marketing feed: retracted message for post_id=%s not sent (%s: %s)",
                                   post.get("id"), type(e).__name__, e)
                    counters["failed"] += 1
                    continue
            if await _stamp(svc, post, {"retract_notified_at": _now_iso()}):
                counters["retracted"] += 1

        # 3. alerts (any status)
        alerts = await _rows(svc, svc.sb.table(POSTS).select("*").not_.is_("metadata->>alert_kind", "null")
                             .is_("metadata->>alert_notified_at", "null"), "feed_alerts")
        for post in alerts:
            if budget <= 0:
                break
            post_id = canonical_post_id(post.get("id"))
            meta = _meta(post)
            kind = str(meta.get("alert_kind") or "")
            pub = meta.get("publish") if isinstance(meta.get("publish"), dict) else {}
            if kind == "unknown" and (post.get("status") != "queued" or pub.get("state") != "escalated"):
                # Already answered (the owner tapped, or reconcile settled it): a late "outcome
                # UNKNOWN" message would only confuse. Mark it handled without sending.
                await _stamp(svc, post, {"alert_notified_at": _now_iso(), "alert_skipped": "resolved"},
                             retries=0)
                continue
            budget -= 1
            text = (f"{meta.get('alert_text') or kind}\n"
                    f"({str(post.get('platform')).upper()} · {_format(post)} · run {_run_date(post)})")
            markup = None
            if kind == "unknown" and post_id and post.get("status") == "queued":
                markup = unknown_outcome_keyboard(post_id)
            reply_to = meta.get("review_message_id")
            try:
                mid = await _send(chat_id, text, pacer, markup=markup,
                                  reply_to=reply_to if _is_int(reply_to) else None)
            except TelegramRateLimitException:
                raise
            except TelegramException as e:
                logger.warning("marketing feed: alert %s for post_id=%s not sent (%s: %s)", kind, post.get("id"),
                               type(e).__name__, e)
                counters["failed"] += 1
                continue
            if await _stamp(svc, post, {"alert_notified_at": _now_iso(), "alert_message_id": mid}, retries=0):
                counters["alerts"] += 1
    except TelegramRateLimitException as e:
        wait = e.retry_after if e.retry_after is not None else 60
        review_service._rate_limited_until = time.monotonic() + wait
        counters["rate_limited"] = 1
        logger.warning("marketing feed: Telegram flood control (retry_after=%s s) — the rest go out later",
                       e.retry_after)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # A ledger outage (or a bug): logged here, never raised — the next cycle retries.
        counters["failed"] += 1
        logger.error("marketing feed: cycle failed (%s: %s) — retried next cycle", type(e).__name__, e,
                     exc_info=True)
    return counters

