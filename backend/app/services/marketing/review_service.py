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

Phase 5 (design doc §12.10): a post whose platform cannot publish yet (`outlets.enabled_platforms()`
does not include it) arrives as a READ-ONLY PREVIEW — the same text, labelled "preview — <platform>
not wired yet", with no buttons (stamped `metadata.review_preview_at`); it gets its buttons on the
next sweep after its platform is enabled. Taps other than Approve/Reject — Retract (two steps:
🗑 → Confirm / Keep), "It's live" / "Not posted" on an escalated unknown outcome — only RECORD the
decision (`run_service.request_retract` / `resolve_unknown`) and wake the publisher; the webhook never
calls a platform (rules/marketing.md §2). The "Posted …" feed and the alerts are `publish_feed.py`.

Reject reasons (2026-10-01): ❌ Reject swaps the buttons for a reason keyboard (REJECT_REASONS — Tone,
Accuracy, Compliance, Weak / boring, Other; optional). A tap records `metadata.review.reason` on the
rejected post (`run_service.record_reject_reason`: fenced, a repeat writes nothing, a later different
reason wins) and closes the keyboard. The weekly digest lists them; the judge round calibrates on them.

Review BUNDLES (drop 1, 2026-10-09; MARKETING_REVIEW_BUNDLES, read every cycle — off = everything
above, unchanged): two decisions a posting day instead of one per post. A run is offered only once it
reached `media_ready` (all its posts exist), as at most two bundles — "video" (its video posts) and
"post" (its image / text posts):
  1. every member is stamped `metadata.review_bundle` = {id, kind, members, caption_sha} with the
     fenced, merging `transition_post` BEFORE anything is sent (any stamp that does not land holds the
     whole bundle for the next cycle — nothing has been sent yet);
  2. the media (the video by sendVideo, the image card by sendPhoto — a link when Telegram cannot
     fetch it), then ONE message per DISTINCT caption naming its platforms, then ONE decision message:
     ✅ Approve all / ❌ Reject all (+ ✂ drop <platform>, an optional per-post reject, when there are
     two or more members);
  3. each member is stamped notified (`review_notified_at`, `review_message_id` = the decision
     message) only while it still carries THAT bundle's id — at least once: a failure anywhere re-sends
     the bundle next cycle under a NEW id, and the old message's buttons then decide nothing.
A tap is `run_service.review_bundle`: one conditional UPDATE on the members still pending AND still
stamped with that bundle id (AND still showing the text that was shown); the decision message is then
edited (or, when it cannot be, answered) with one line per platform. ❌ Reject all offers the reason
keyboard once; a reason is recorded on every rejected member. A ✂ drop offers the per-post reasons on
a small message of its own, threaded under the decision message. A video bundle shows ONE video: a
member that does not carry exactly that video is left out of it (logged at ERROR, a ⚠️ line on the
decision message), so Approve all never covers a video the owner was not shown. Platforms that cannot publish yet ride
along as "not sent" previews (stamped `review_preview_at`) and are never part of a decision. Bundle
taps are honoured whatever the switch says now: they decide only what the owner was shown.

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
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from app.config import settings
from app.integrations import telegram
from app.integrations.telegram import (
    MAX_CALLBACK_DATA_BYTES,
    MAX_CAPTION_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_URL_PHOTO_BYTES,
    MAX_URL_SEND_BYTES,
    TelegramException,
    TelegramRateLimitException,
    TelegramRequestError,
)
from app.schemas.marketing import IMAGE_ROLE_POST, POST_FORMATS
from app.services.marketing import outlets, publisher_wake
from app.services.marketing.run_service import (
    POSTS,
    REVIEW_BUNDLE_KINDS,
    _exec,
    _now_iso,
    _one,
    _parse_ts,
    _ts_filter,
    get_marketing_run_service,
    review_bundle_of,
    review_caption_sha,
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
#: Callback verbs (one letter + ":" + uuid = 38 bytes, inside Telegram's 64). SINGLE lowercase
#: letters only, so `_CALLBACK_RE` stays a character class (a test pins it). Never `x` (a test pins
#: `x:<uuid>` as invalid).
_VERBS = {
    "a": "approve", "r": "reject",                                   # review
    "d": "retract", "k": "confirm_retract", "c": "cancel_retract",   # published post
    "l": "live", "n": "not_posted",                                  # escalated unknown outcome
    # why a post was rejected (the reason keyboard after ❌; `run_service.record_reject_reason`)
    "t": "reason_tone", "f": "reason_accuracy", "p": "reason_compliance", "w": "reason_weak",
    "o": "reason_other",
    # review BUNDLES (drop 1): the uuid is the BUNDLE id for g / j and the bundle reasons, the POST id
    # for s (drop one member). Never `b` or `e` (tests pin both as invalid), never `q` (a test borrows it).
    "g": "bundle_approve", "j": "bundle_reject", "s": "bundle_drop",
    "h": "bundle_reason_tone", "i": "bundle_reason_accuracy", "m": "bundle_reason_compliance",
    "u": "bundle_reason_weak", "v": "bundle_reason_other",
}
_VERB_LETTER = {name: letter for letter, name in _VERBS.items()}
_CALLBACK_RE = re.compile(rf"([{''.join(_VERBS)}]):({_UUID})", re.ASCII)
_REASON_VERB_PREFIX = "reason_"
_BUNDLE_REASON_PREFIX = "bundle_reason_"
_BUNDLE_DECISIONS = ("bundle_approve", "bundle_reject", "bundle_drop")

#: Reject reasons → their button label (the weekly digest imports this too). The keys are exactly
#: `run_service.REJECT_REASON_CODES` (pinned by a test); each has a `reason_<code>` verb above.
REJECT_REASONS: Dict[str, str] = {
    "tone": "Tone", "accuracy": "Accuracy", "compliance": "Compliance", "weak": "Weak / boring",
    "other": "Other",
}
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
    verb = _VERB_LETTER[decision]
    data = f"{verb}:{post_id}"
    if parse_callback_data(data) != (decision, post_id):
        # Only a canonical uuid round-trips; a button that could never be parsed back is refused
        # here rather than shipped as a dead button.
        raise ValueError(f"callback data for post {post_id!r} would not round-trip")
    return data


def parse_callback_data(data: Any) -> Optional[Tuple[str, str]]:
    """`a:<uuid>` → ("approve", uuid); `r:` reject; `d:` retract; `k:` confirm_retract;
    `c:` cancel_retract; `l:` live; `n:` not_posted; `t:` / `f:` / `p:` / `w:` / `o:` the reject
    reasons (reason_tone / _accuracy / _compliance / _weak / _other); the review bundles' `g:`
    bundle_approve, `j:` bundle_reject, `s:` bundle_drop (a post id) and `h:` / `i:` / `m:` / `u:` /
    `v:` the bundle reasons (bundle_reason_tone / … / _other); anything else → None.
    Strict: canonical lowercase uuid only, ≤ 64 bytes, a FULL match (no trailing newline)."""
    if not isinstance(data, str) or len(data.encode("utf-8", "replace")) > MAX_CALLBACK_DATA_BYTES:
        return None
    match = _CALLBACK_RE.fullmatch(data)
    if match is None:
        return None
    return _VERBS[match.group(1)], match.group(2)


def review_keyboard(post_id: str) -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "✅ Approve", "callback_data": callback_data("approve", post_id)}],
        [{"text": "❌ Reject", "callback_data": callback_data("reject", post_id)}],
    ]}


def retract_keyboard(post_id: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": "🗑 Retract", "callback_data": callback_data("retract", post_id)}]]}


def confirm_retract_keyboard(post_id: str) -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "🗑 Yes, delete it", "callback_data": callback_data("confirm_retract", post_id)}],
        [{"text": "Keep it", "callback_data": callback_data("cancel_retract", post_id)}],
    ]}


def unknown_outcome_keyboard(post_id: str) -> Dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "✅ It's live", "callback_data": callback_data("live", post_id)}],
        [{"text": "❌ Not posted", "callback_data": callback_data("not_posted", post_id)}],
    ]}


def reject_reason_keyboard(post_id: str) -> Dict[str, Any]:
    """One button per REJECT_REASONS entry, in its order — offered after ❌ Reject (optional to tap)."""
    return {"inline_keyboard": [
        [{"text": label, "callback_data": callback_data(f"{_REASON_VERB_PREFIX}{code}", post_id)}]
        for code, label in REJECT_REASONS.items()
    ]}


def bundle_keyboard(bundle_id: str, members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The decision message's buttons: ✅ Approve all (n) / ❌ Reject all, then — when two or more
    members are still open — one ✂ drop <PLATFORM> per member (a per-post reject; optional)."""
    rows: List[List[Dict[str, str]]] = [
        [{"text": f"✅ Approve all ({len(members)})", "callback_data": callback_data("bundle_approve", bundle_id)}],
        [{"text": "❌ Reject all", "callback_data": callback_data("bundle_reject", bundle_id)}],
    ]
    if len(members) > 1:
        for post in members:
            pid = canonical_post_id(post.get("id"))
            if pid is None:
                raise ValueError(f"bundle {bundle_id}: member id {post.get('id')!r} is not a uuid")
            rows.append([{"text": f"✂ drop {_platform_name(post)}", "callback_data": callback_data("bundle_drop", pid)}])
    return {"inline_keyboard": rows}


def bundle_reason_keyboard(bundle_id: str) -> Dict[str, Any]:
    """REJECT_REASONS as bundle verbs — a tap records the reason on EVERY rejected member."""
    return {"inline_keyboard": [
        [{"text": label, "callback_data": callback_data(f"{_BUNDLE_REASON_PREFIX}{code}", bundle_id)}]
        for code, label in REJECT_REASONS.items()
    ]}


def _review_reason(row: Optional[Dict[str, Any]]) -> Optional[str]:
    """The reject reason already recorded on `row` (`metadata.review.reason`), or None."""
    meta = (row or {}).get("metadata") if isinstance((row or {}).get("metadata"), dict) else {}
    review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
    reason = review.get("reason")
    return reason if isinstance(reason, str) and reason in REJECT_REASONS else None


def _rejected_by_bundle(row: Optional[Dict[str, Any]], bundle_id: str) -> bool:
    """Is `row` rejected by THIS bundle's ❌ Reject all? `run_service.review_bundle` records
    `metadata.review.bundle_id`; a ✂ drop or a per-post button goes through `review_post`, whose record
    carries none — that post was rejected separately and keeps its own reason (drop-1 re-review)."""
    if not isinstance(row, dict) or row.get("status") != "rejected":
        return False
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
    return review.get("bundle_id") == bundle_id


def _bundle_rejected(row: Optional[Dict[str, Any]], bundle_id: str) -> bool:
    """Does a reason for THIS bundle's ❌ Reject all belong on `row`? Its record names the bundle
    (`_rejected_by_bundle`), or it is rejected with NO review record at all while carrying this bundle's
    stamp: `review_bundle`'s record merge is best effort, and only it (fenced on the stamp) and
    `review_post` (record in the same UPDATE) write `rejected` — so such a row is this Reject all's
    (drop-1 re-review r3). `record_reject_reason(bundle_id=…)` re-checks it on a fresh read."""
    if _rejected_by_bundle(row, bundle_id):
        return True
    if not isinstance(row, dict) or row.get("status") != "rejected":
        return False
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return meta.get("review") is None and (review_bundle_of(row) or {}).get("id") == bundle_id


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


class SendBlock(NamedTuple):
    """Why an approved LIVE post would not be sent right now (`reason`), and what then happens to it
    (`consequence`: the tail of an approval's result line, after "⚠️ <reason>: ")."""
    reason: str
    consequence: str


#: The consequence of every blocker but one: the post stays `approved`, unsent, until the publisher
#: expires it (outside its run day or the next) — turning the switch on that day still sends it.
_BLOCKED_STAYS_APPROVED = "NOT sent (it expires after its day)"
_X_IMAGES_OFF = "MARKETING_X_IMAGES is off (X image posts are refused)"
#: …but an X image post with the switch off is REFUSED by `outlet_x.prepare` on the very next publish
#: pass (the Approve wakes it) and `publisher_service._refuse` moves it to `failed` with an alert — it
#: never waits for its day, and turning the switch on afterwards does not bring it back (drop-1 re-review).
#: After the approval its result line states only that outcome: the remedy can no longer be followed.
_X_IMAGES_OFF_CONSEQUENCE = "NOT sent: it will be marked failed (MARKETING_X_IMAGES is off)"
#: …so the remedy is offered BEFORE the decision instead (the per-post header, the bundle decision message):
#: what approving would do, and what to do first. Every other blocker keeps "approving will not send …".
#: The remedy names only a control the message really carries (re-review r3): a bundle of two or more
#: members has "✂ drop"; the per-post message and a one-member bundle have only Approve / Reject.
_X_IMAGES_OFF_BEFORE_DROP = "approving will mark X failed — ✂ drop X, or turn MARKETING_X_IMAGES on first"
_X_IMAGES_OFF_BEFORE_REJECT = "approving will mark X failed — reject it, or turn MARKETING_X_IMAGES on first"


def _before_approval(blocker: str, what: str, *, can_drop: bool = False) -> str:
    """The tail of a pre-decision blocker line (after "⚠️ <blocker>: "): what approving `what` would do.
    `can_drop` is True only when the message carries "✂ drop" buttons."""
    if blocker == _X_IMAGES_OFF:
        return _X_IMAGES_OFF_BEFORE_DROP if can_drop else _X_IMAGES_OFF_BEFORE_REJECT
    return f"approving will not send {what}"


def web_send_block(post: Dict[str, Any]) -> Optional[SendBlock]:
    """Why an approved LIVE post would not be sent right now by the web publisher, and what then
    happens to it — or None. Read at notify time AND at tap time (the switches can change in
    between): an Approve must never look like "it will go out" when publishing is off, in web
    dry-run, or the platform is not enabled. An X post frozen as "image" while MARKETING_X_IMAGES is
    off counts too: `outlet_x.prepare` reads that switch at publish time and refuses the post (it is
    never downgraded to text), so its consequence is a failure, not an expiry."""
    if is_rehearsal(post):
        return None   # a rehearsal row says "DRY RUN" already
    if not settings.MARKETING_ENABLED:
        return SendBlock("publishing is OFF on the web", _BLOCKED_STAYS_APPROVED)
    if settings.MARKETING_DRY_RUN:
        return SendBlock("the web is in DRY RUN", _BLOCKED_STAYS_APPROVED)
    if str(post.get("platform")) not in outlets.enabled_platforms():
        return SendBlock(f"{post.get('platform')} is not enabled", _BLOCKED_STAYS_APPROVED)
    if post.get("platform") == "x" and post.get("format") == "image" and not settings.MARKETING_X_IMAGES:
        return SendBlock(_X_IMAGES_OFF, _X_IMAGES_OFF_CONSEQUENCE)
    return None


def web_send_blocker(post: Dict[str, Any]) -> Optional[str]:
    """`web_send_block`'s reason alone (the notify-time headers and the decision message), or None."""
    block = web_send_block(post)
    return block.reason if block is not None else None


def _blocked_tail(block: SendBlock) -> str:
    """The tail of an approval's result line when the approved post will not be sent right now."""
    return f" — ⚠️ {block.reason}: {block.consequence}"


def compose_post_text(post: Dict[str, Any], run_date: str, media: List[Tuple[str, str]],
                      *, preview: bool = False) -> str:
    """Header, blank line, title (if any), the EXACT caption, then the post's media links. A
    `preview` (its platform cannot publish yet) says so in the header and carries no buttons."""
    header = f"{str(post.get('platform') or '?').upper()} · {post.get('format') or '?'} · run {run_date}"
    if is_rehearsal(post):
        header += " · DRY RUN"
    if preview:
        header += f" · preview — {post.get('platform') or '?'} not wired yet"
    else:
        blocker = web_send_blocker(post)
        if blocker:
            header += f" · ⚠️ {blocker}: {_before_approval(blocker, 'it')}"
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


# ── review bundles: pure text helpers (tested directly) ───────────────────────


def _fmt(post: Dict[str, Any]) -> str:
    """The post's format when it is one the ledger knows, else "?" (never a raw column value)."""
    fmt = post.get("format")
    return fmt if isinstance(fmt, str) and fmt in POST_FORMATS else "?"


def _platform_name(post: Dict[str, Any]) -> str:
    return str(post.get("platform") or "?").upper()


def _platform_label(post: Dict[str, Any]) -> str:
    """"BLUESKY (image)" — the platform and the format it will post as."""
    return f"{_platform_name(post)} ({_fmt(post)})"


def bundle_kind(post: Dict[str, Any]) -> str:
    """Which bundle a post belongs to: "video" for a video post, "post" for anything else."""
    return "video" if post.get("format") == "video" else "post"


def _shown(post: Dict[str, Any]) -> Tuple[str, str]:
    """(title, caption) exactly as a caption message shows them — also the distinct-caption key."""
    title = post.get("title")
    caption = post.get("caption")
    return (title.strip() if isinstance(title, str) else "",
            caption if isinstance(caption, str) and caption.strip() else "(no caption)")


def compose_bundle_media_caption(kind: str, members: List[Dict[str, Any]], previews: List[Dict[str, Any]],
                                 run_date: str, dry: bool) -> str:
    """The video / image card's caption: what it is, the platforms that carry it (a preview marked),
    the run. Within Telegram's 1,024."""
    names = [_platform_name(p) for p in members] + [f"{_platform_name(p)} (preview)" for p in previews]
    text = (f"{'VIDEO' if kind == 'video' else 'IMAGE'} · {', '.join(names)} · run {run_date}"
            + (" · DRY RUN" if dry else "") + "\nThe captions and your decision follow.")
    return _truncate_utf16(text, MAX_CAPTION_CHARS)


def compose_bundle_caption_text(rows: List[Dict[str, Any]], preview_ids: set, run_date: str, dry: bool) -> str:
    """One DISTINCT caption: a header naming every platform that posts it (with its format), a line
    for the previews among them, then the title (if any) and the EXACT caption."""
    header = f"CAPTION · {', '.join(_platform_label(p) for p in rows)} · run {run_date}" + (" · DRY RUN" if dry else "")
    parts = [header]
    pv = [_platform_name(p) for p in rows if canonical_post_id(p.get("id")) in preview_ids]
    if pv:
        parts.append(f"Preview only — {', '.join(pv)} not wired yet: never sent")
    title, caption = _shown(rows[0]) if rows else ("", "(no caption)")
    if title:
        parts.append(title)
    parts.append(caption)
    return "\n\n".join(parts)


def _run_series_note(run: Any, run_id: str) -> str:
    """`digest_service._series_note(run)` for the decision message (contract D13), imported HERE because
    digest_service imports this module. Best effort: a failure costs only the line (WARNING), never the
    bundle."""
    try:
        from app.services.marketing import digest_service

        return digest_service._series_note(run)
    except Exception as e:  # noqa: BLE001 — an operator label must never hold a review bundle
        logger.warning("marketing review: no series note for run_id=%s (%s: %s) — the decision message "
                       "goes out without it", run_id, type(e).__name__, e)
        return ""


def compose_bundle_decision_text(kind: str, members: List[Dict[str, Any]], previews: List[Dict[str, Any]],
                                 run_date: str, dry: bool, warnings: List[str], *, series_note: str = "") -> str:
    """The decision message: what Approve all sends, what is not sent, why an approval would not go
    out (`web_send_blocker`, per blocker), and the media warnings. One message (it carries the keyboard).
    Drop 2: `series_note` (`digest_service._series_note`: operator labels and fixed words only) names the
    series the day posted and the fallback it took; "" — a lesson-only day — adds no line."""
    lines = [f"DECISION · {'VIDEO' if kind == 'video' else 'POSTS'} · run {run_date}" + (" · DRY RUN" if dry else "")]
    if series_note:
        lines.append(f"Series: {series_note}")
    lines.append("Approve all sends: " + ", ".join(_platform_label(p) for p in members))
    if previews:
        lines.append("Not sent: " + ", ".join(f"{_platform_name(p)} (preview — not wired yet)" for p in previews))
    blocked: "OrderedDict[str, List[str]]" = OrderedDict()
    for post in members:
        blocker = web_send_blocker(post)
        if blocker:
            blocked.setdefault(blocker, []).append(_platform_name(post))
    for blocker, names in blocked.items():
        lines.append(f"⚠️ {blocker}: {_before_approval(blocker, ', '.join(names), can_drop=len(members) > 1)}")
    lines += warnings
    if len(members) > 1:
        lines.append("✂ drop = reject that one platform and keep the rest (optional).")
    return _truncate_utf16("\n".join(lines), MAX_MESSAGE_CHARS)


def bundle_result_lines(results: List[Dict[str, Any]]) -> Tuple[List[str], bool]:
    """One line per member of a decided bundle (`run_service.review_bundle`'s results), and whether an
    approved member will NOT be sent right now (`web_send_block`: its reason AND its consequence)."""
    lines: List[str] = []
    blocked = False
    for result in results:
        row = result.get("row") if isinstance(result.get("row"), dict) else None
        name = _platform_label(row) if row else f"post {str(result.get('post_id') or '?')[:8]}"
        outcome = str(result.get("outcome") or "")
        if outcome == "approved":
            block = web_send_block(row or {})
            word = "approved" + (_blocked_tail(block) if block is not None else "")
            blocked = blocked or block is not None
        elif outcome == "rejected":
            word = "rejected"
        elif outcome == "moved":
            word = "not decided here — it was re-sent in a newer message; decide it there"
        elif outcome == "changed":
            # `reoffered` (run_service.review_bundle) is True only when its review stamps were
            # cleared, so the next sweep re-offers the new text. A False — a release that did not
            # land, or a newer bundle stamped it meanwhile — keeps the plain line: never promise it.
            word = "not decided — its text changed after it was shown"
            if result.get("reoffered") is True:
                word += "; it will be offered again"
        elif outcome == "not_found":
            word = "not found"
        else:
            status = outcome[len("already_"):] if outcome.startswith("already_") else ""
            word = f"already {status if _STATUS_WORD_RE.fullmatch(status) else 'decided'}"
        lines.append(f"• {name}: {word}")
    return lines, blocked


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
    """Two in-query reads (filters before the LIMIT, so neither kind can starve the other):
    pending posts of ENABLED platforms not yet sent with buttons, and pending posts of every other
    platform not yet sent as a preview. Oldest first."""
    enabled = outlets.enabled_platforms()
    buttons: List[Dict[str, Any]] = []
    if enabled:
        query = (
            svc.sb.table(POSTS).select("*")
            .eq("status", "pending_review")
            .is_("metadata->>review_notified_at", "null")
            .in_("platform", enabled)
            .order("created_at")
            .limit(SCAN_LIMIT)
        )
        res = await _exec(query, op="review_pending_posts", status="pending_review")
        buttons.extend(r for r in (getattr(res, "data", None) or []) if isinstance(r, dict) and not _notified(r))
    query = (
        svc.sb.table(POSTS).select("*")
        .eq("status", "pending_review")
        .is_("metadata->>review_notified_at", "null")
        .is_("metadata->>review_preview_at", "null")
    )
    if enabled:
        query = query.not_.in_("platform", enabled)
    res = await _exec(query.order("created_at").limit(SCAN_LIMIT), op="review_preview_posts",
                      status="pending_review")
    previews = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict) and not _notified(r)]
    # Posts that can be approved come FIRST; previews only fill the room left, so no number of
    # unwired posts can push an approvable one out of the sweep.
    rows = buttons[:SCAN_LIMIT] + previews[: max(SCAN_LIMIT - len(buttons), 0)]
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return rows


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


async def _send_video_link(chat_id: int, caption: str, url: str, pacer: _Pacer, *, run_id: str,
                           what: str = "video") -> bool:
    try:
        await pacer.wait()
        await telegram.send_message(chat_id, f"{caption}\n{url}")
        return True
    except TelegramRateLimitException:
        raise
    except TelegramException as e:
        logger.warning("marketing review: %s LINK for run_id=%s not sent (%s: %s) — the run's posts "
                       "wait for the next cycle", what, run_id, type(e).__name__, e)
        return False


async def _send_run_video(svc: Any, chat_id: int, run_id: str, run_date: str, video: Dict[str, Any],
                          dry: bool, pacer: _Pacer, *, caption: Optional[str] = None) -> bool:
    """Send the video (or, when Telegram cannot fetch it or the send is ambiguous, a link to it).
    False = neither went out; the run's posts are then held back a cycle — the reviewer must see
    the media first. `caption` replaces the per-post flow's caption (a review bundle names its
    platforms)."""
    url = svc.public_url(str(video["storage_path"]))
    if caption is None:
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


def _pick_image(run_id: str, run: Dict[str, Any], assets: List[Dict[str, Any]],
                posts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The run's verified image card: a READY `card` asset OF THIS RUN whose `metadata.image_role` is
    the post image, named by one of the image posts' asset_ids, else by the run's
    `metadata.image_asset_id`. Anything else is not shown (`_pick_video`'s rule)."""
    ready = {}
    for a in assets:
        meta = a.get("metadata") if isinstance(a.get("metadata"), dict) else {}
        if (a.get("kind") == "card" and a.get("status") == "ready" and a.get("storage_path")
                and meta.get("image_role") == IMAGE_ROLE_POST and str(a.get("run_id") or run_id) == str(run_id)):
            ready[str(a.get("id"))] = a
    candidates = [str(aid) for p in posts for aid in (p.get("asset_ids") or [])]
    meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    if meta.get("image_asset_id"):
        candidates.append(str(meta["image_asset_id"]))
    return next((ready[c] for c in candidates if c in ready), None)


async def _send_bundle_photo(svc: Any, chat_id: int, run_id: str, image: Dict[str, Any], caption: str,
                             pacer: _Pacer) -> bool:
    """Send the image card (or, when Telegram cannot fetch it or the send is ambiguous, a link to it) —
    `_send_run_video`'s rules. False = neither went out; the bundle is held back a cycle."""
    url = svc.public_url(str(image["storage_path"]))
    caption = _truncate_utf16(caption, MAX_CAPTION_CHARS)
    size = image.get("bytes")
    if _is_int(size) and size > MAX_URL_PHOTO_BYTES:
        logger.info("marketing review: image asset_id=%s of run_id=%s is %d bytes (> %d, Telegram's photo "
                    "URL limit) — sending a link instead", image.get("id"), run_id, size, MAX_URL_PHOTO_BYTES)
        return await _send_video_link(chat_id, caption, url, pacer, run_id=run_id, what="image")
    try:
        await pacer.wait()
        await telegram.send_photo(chat_id, url, caption=caption)
        return True
    except TelegramRateLimitException:
        raise
    except TelegramException as e:
        # Refused, or AMBIGUOUS (a timeout / 5xx may come after Telegram delivered it): a link either
        # way, so the bundle can follow; at worst the owner sees the image twice.
        logger.warning("marketing review: sendPhoto failed for asset_id=%s run_id=%s (%s: %s) — sending a "
                       "link instead", image.get("id"), run_id, type(e).__name__, e)
        return await _send_video_link(chat_id, caption, url, pacer, run_id=run_id, what="image")


async def _stamp_notified(svc: Any, post_id: str, message_ids: List[Optional[int]],
                          *, key: str = "review_notified_at") -> bool:
    """Record that the owner has been told — only while the post is STILL pending_review and
    unchanged since we read it (fenced on status AND updated_at), so a decision that landed
    meanwhile is never overwritten and no other metadata is lost. `key` is `review_preview_at` for a
    read-only preview (which must not count as "sent with buttons")."""
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
        meta[key] = now
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
    # Read at call time, like every switch: the owner flips it without a deploy of this code path.
    bundles = bool(settings.MARKETING_REVIEW_BUNDLES)
    if bundles:
        counters.update({"bundles": 0, "images": 0, "held": 0})
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
                if bundles:
                    await _review_run_bundles(svc, chat_id, run_id, posts, pacer, counters)
                else:
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
    preview = str(post.get("platform")) not in outlets.enabled_platforms()
    text = compose_post_text(post, run_date, _post_media(svc, post, assets), preview=preview)
    chunks = split_message(text)
    message_ids: List[Optional[int]] = []
    try:
        for i, chunk in enumerate(chunks):
            markup = review_keyboard(post_id) if (i == len(chunks) - 1 and not preview) else None
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
    logger.info("marketing review: notified post_id=%s platform=%s run_date=%s message_id=%s chunks=%d preview=%s",
                post_id, post.get("platform"), run_date, message_ids[-1] if message_ids else None, len(chunks),
                preview)
    if await _stamp_notified(svc, post_id, message_ids,
                             key="review_preview_at" if preview else "review_notified_at"):
        counters["stamped"] += 1


# ── the sweep, review bundles (MARKETING_REVIEW_BUNDLES) ───────────────────────

#: The run status a bundle waits for. The worker sets it right after `create_posts` answered, so every
#: post of the run exists: a bundle offered earlier could miss members recorded after it.
_BUNDLE_RUN_STATUS = "media_ready"


def _previewed(post: Dict[str, Any]) -> bool:
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    return bool(meta.get("review_preview_at"))


async def _run_posts(svc: Any, run_id: str) -> List[Dict[str, Any]]:
    """Every post of `run_id`, oldest first: a bundle is built from the whole run, never from the
    sweep's bounded window (which only says WHICH runs have something to offer)."""
    res = await _exec(svc.sb.table(POSTS).select("*").eq("run_id", run_id).order("created_at").limit(SCAN_LIMIT),
                      op="review_bundle_run_posts", run_id=run_id)
    return [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]


async def _review_run_bundles(svc: Any, chat_id: int, run_id: str, posts: List[Dict[str, Any]], pacer: _Pacer,
                              counters: Dict[str, int]) -> None:
    """A run's bundles — nothing until the run reached media_ready; then its video bundle, then its
    post bundle. Members are the open posts of ENABLED platforms; the rest ride along as previews."""
    try:
        run = await svc.get_run(run_id) or {}
        if run.get("status") != _BUNDLE_RUN_STATUS:
            logger.info("marketing review: run_id=%s is %r, not %s — its %d post(s) wait for it before they "
                        "are offered as a bundle", run_id, run.get("status") if run else "missing",
                        _BUNDLE_RUN_STATUS, len(posts))
            counters["held"] += len(posts)
            return
        rows = await _run_posts(svc, run_id)
        assets = await svc.list_assets(run_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review: could not read run_id=%s for its bundles (%s: %s) — its %d post(s) "
                       "wait for the next cycle", run_id, type(e).__name__, e, len(posts))
        counters["failed"] += len(posts)
        return
    run_date = _run_date(run, posts[0])
    by_id = {str(a.get("id")): a for a in assets if isinstance(a, dict)}
    enabled = set(outlets.enabled_platforms())
    open_rows: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "pending_review" or _notified(row):
            continue
        if canonical_post_id(row.get("id")) is None:
            logger.error("marketing review: post id %r is not a uuid — no button could name it; left out of its "
                         "bundle", row.get("id"))
            counters["failed"] += 1
            continue
        open_rows.append(row)
    for kind in REVIEW_BUNDLE_KINDS:
        of_kind = [r for r in open_rows if bundle_kind(r) == kind]
        members = [r for r in of_kind if str(r.get("platform")) in enabled]
        previews = [r for r in of_kind if str(r.get("platform")) not in enabled and not _previewed(r)]
        if members or previews:
            await _notify_bundle(svc, chat_id, run_id, run, run_date, kind, members, previews, by_id, pacer,
                                 counters)


async def _notify_bundle(svc: Any, chat_id: int, run_id: str, run: Dict[str, Any], run_date: str, kind: str,
                         members: List[Dict[str, Any]], previews: List[Dict[str, Any]],
                         assets: Dict[str, Dict[str, Any]], pacer: _Pacer, counters: Dict[str, int]) -> None:
    """One bundle: stamp the members → the media → one message per distinct caption → the decision
    message → stamp them notified (at least once; see the module docstring)."""
    asset_list = list(assets.values())
    warnings: List[str] = []
    # 0. A video bundle shows ONE video, and ✅ Approve all must never cover a video the owner was not
    #    shown: the server checks only that each video post's asset is a ready video of this run, so a
    #    member carrying anything but exactly [the shown video] is left out BEFORE the stamp (an
    #    unstamped post is never decided by this bundle's buttons) and logged at ERROR. It stays pending
    #    and unnotified, so a later sweep offers it again (on its own, with its own video, once the rest
    #    are notified).
    video: Optional[Dict[str, Any]] = None
    if kind == "video":
        video = _pick_video(run_id, run, asset_list, members + previews)
        if video is not None:
            shown = [str(video.get("id"))]
            kept: List[Dict[str, Any]] = []
            left_out: List[Dict[str, Any]] = []
            for post in members:
                ids = post.get("asset_ids")
                (kept if isinstance(ids, list) and [str(a) for a in ids] == shown else left_out).append(post)
            for post in left_out:
                logger.error("marketing review: post_id=%s run_id=%s platform=%s carries asset_ids=%r, not the video "
                             "the %s bundle shows (asset_id=%s) — LEFT OUT of the bundle, so Approve all cannot "
                             "approve a video the owner was not shown; it stays pending", post.get("id"), run_id,
                             post.get("platform"), post.get("asset_ids"), kind, shown[0])
            if left_out:
                counters["failed"] += len(left_out)
                names = ", ".join(_platform_name(p) for p in left_out)
                warnings.append(f"⚠️ Not in this decision: {names} — "
                                f"{'its post does' if len(left_out) == 1 else 'their posts do'} not carry the video "
                                f"shown above (logged). Approve all does not cover "
                                f"{'it' if len(left_out) == 1 else 'them'}.")
                members = kept
                if not members and not previews:
                    return
    everyone = members + previews
    bundle_id = str(uuid.uuid4())
    member_ids = [canonical_post_id(p.get("id")) for p in members]
    # 1. The bundle stamp, BEFORE anything is sent: every button that will ever exist then names posts
    #    that already carry its id. A stamp that does not land holds the whole bundle — nothing is sent;
    #    the stamped members are re-stamped under a new id next cycle.
    stamped: List[Dict[str, Any]] = []
    for post in members:
        pid = canonical_post_id(post.get("id"))
        stamp = {"id": bundle_id, "kind": kind, "members": member_ids, "caption_sha": review_caption_sha(post)}
        try:
            updated = await svc.transition_post(pid, expect_status="pending_review", observed=post,
                                                meta={"review_bundle": stamp}, retries=0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("marketing review: bundle stamp FAILED post_id=%s bundle_id=%s run_id=%s (%s: %s) — "
                           "the %s bundle waits for the next cycle (nothing sent)", pid, bundle_id, run_id,
                           type(e).__name__, e, kind)
            counters["failed"] += len(everyone)
            return
        if updated is None:
            logger.warning("marketing review: post_id=%s changed before its bundle stamp (bundle_id=%s run_id=%s) "
                           "— the %s bundle waits for the next cycle (nothing sent)", pid, bundle_id, run_id, kind)
            counters["failed"] += len(everyone)
            return
        stamped.append(updated)
    members = stamped
    everyone = members + previews
    dry = bool(run.get("dry_run")) or any(is_rehearsal(p) for p in everyone)

    # 2. The media first — the owner sees what is posted before the decision (the video picked in 0.).
    if kind == "video":
        if video is None:
            warnings.append("⚠️ No verified video was found for this run — check it before approving.")
        else:
            caption = compose_bundle_media_caption(kind, members, previews, run_date, dry)
            if not await _send_run_video(svc, chat_id, run_id, run_date, video, dry, pacer, caption=caption):
                counters["failed"] += len(everyone)
                return
            counters["videos"] += 1
    else:
        image_members = [p for p in members if _fmt(p) == "image"]
        image_previews = [p for p in previews if _fmt(p) == "image"]
        if image_members or image_previews:
            image = _pick_image(run_id, run, asset_list, image_members + image_previews)
            if image is None:
                warnings.append("⚠️ No verified image card was found for the image posts — do not approve them.")
            else:
                caption = compose_bundle_media_caption(kind, image_members, image_previews, run_date, dry)
                if not await _send_bundle_photo(svc, chat_id, run_id, image, caption, pacer):
                    counters["failed"] += len(everyone)
                    return
                counters["images"] += 1

    # 3. One message per DISTINCT caption, then 4. the decision message (members only).
    preview_ids = {canonical_post_id(p.get("id")) for p in previews}
    groups: "OrderedDict[Tuple[str, str], List[Dict[str, Any]]]" = OrderedDict()
    for post in everyone:
        groups.setdefault(_shown(post), []).append(post)
    shown_at: Dict[Optional[str], Optional[int]] = {}
    decision_mid: Optional[int] = None
    try:
        for group in groups.values():
            last: Optional[int] = None
            for chunk in split_message(compose_bundle_caption_text(group, preview_ids, run_date, dry)):
                await pacer.wait()
                last = _message_id(await telegram.send_message(chat_id, chunk))
            for post in group:
                shown_at[canonical_post_id(post.get("id"))] = last
        if members:
            text = compose_bundle_decision_text(kind, members, previews, run_date, dry, warnings,
                                                series_note=_run_series_note(run, run_id))
            await pacer.wait()
            decision_mid = _message_id(await telegram.send_message(chat_id, text,
                                                                   reply_markup=bundle_keyboard(bundle_id, members)))
    except (TelegramRateLimitException, asyncio.CancelledError):
        raise
    except TelegramException as e:
        logger.warning("marketing review: %s bundle bundle_id=%s run_id=%s was NOT fully sent (%s: %s) — it is "
                       "offered again next cycle under a new id", kind, bundle_id, run_id, type(e).__name__, e)
        counters["failed"] += len(everyone)
        return
    except Exception as e:
        logger.error("marketing review: %s bundle bundle_id=%s run_id=%s FAILED (%s: %s) — offered again next "
                     "cycle", kind, bundle_id, run_id, type(e).__name__, e, exc_info=True)
        counters["failed"] += len(everyone)
        return
    counters["sent"] += len(everyone)
    if members:
        counters["bundles"] += 1
    logger.info("marketing review: notified %s bundle bundle_id=%s run_id=%s run_date=%s members=%s previews=%s "
                "decision_message_id=%s", kind, bundle_id, run_id, run_date,
                [p.get("platform") for p in members], [p.get("platform") for p in previews], decision_mid)

    # 5. Notified — each member only while it still carries THIS bundle; previews as previews.
    for post in members:
        if await _stamp_bundle_notified(svc, canonical_post_id(post.get("id")) or "", bundle_id, decision_mid):
            counters["stamped"] += 1
    for post in previews:
        pid = canonical_post_id(post.get("id")) or ""
        if await _stamp_notified(svc, pid, [shown_at.get(pid)], key="review_preview_at"):
            counters["stamped"] += 1


async def _stamp_bundle_notified(svc: Any, post_id: str, bundle_id: str, message_id: Optional[int]) -> bool:
    """`review_notified_at` + `review_message_id` (the decision message: the publish feed threads its
    "Posted" replies under it) on a member that is STILL pending and STILL stamped with `bundle_id` —
    fenced on status and updated_at, merging. Anything else is logged and left for the next cycle,
    which re-sends the post under a new bundle id (at least once)."""
    try:
        fresh = await svc.get_post(post_id)
        if fresh is None:
            logger.warning("marketing review: post_id=%s vanished after its bundle %s was sent; not stamped",
                           post_id, bundle_id)
            return False
        if fresh.get("status") != "pending_review":
            logger.info("marketing review: post_id=%s was decided (%s) while bundle %s was being sent; not "
                        "stamped", post_id, fresh.get("status"), bundle_id)
            return False
        if (review_bundle_of(fresh) or {}).get("id") != bundle_id:
            logger.warning("marketing review: post_id=%s no longer carries bundle %s (re-stamped meanwhile); not "
                           "stamped", post_id, bundle_id)
            return False
        updated = await svc.transition_post(post_id, expect_status="pending_review", observed=fresh,
                                            meta={"review_notified_at": _now_iso(), "review_message_id": message_id},
                                            retries=0)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review: post_id=%s was SENT in bundle %s but the notified stamp failed (%s: %s) — "
                       "it is offered again next cycle (at-least-once)", post_id, bundle_id, type(e).__name__, e)
        return False
    if updated is None:
        logger.warning("marketing review: post_id=%s changed between the read and the bundle stamp; not stamped "
                       "(offered again next cycle if it is still pending)", post_id)
        return False
    return True


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
    if decision in _BUNDLE_DECISIONS or decision.startswith(_BUNDLE_REASON_PREFIX):
        # A review bundle's buttons — honoured whatever MARKETING_REVIEW_BUNDLES says now: they decide
        # only the posts that carry the bundle the owner was shown.
        return await _handle_bundle_action(decision, post_id, cq_id=cq_id, chat_id=chat_id, message=message,
                                           by=f"telegram:{from_id}")
    if decision.startswith(_REASON_VERB_PREFIX):
        return await _handle_reject_reason(decision[len(_REASON_VERB_PREFIX):], post_id, cq_id=cq_id,
                                           chat_id=chat_id, message=message, by=f"telegram:{from_id}")
    if decision not in ("approve", "reject"):
        return await _handle_post_action(decision, post_id, cq_id=cq_id, chat_id=chat_id, message=message,
                                         by=f"telegram:{from_id}")

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
    if outcome == "approved":
        publisher_wake.wake()   # publish within seconds, not at the next 10-minute tick
        block = web_send_block(row or {})
        if block is not None:
            toast = f"Approved — but {block.reason}: nothing will be sent"   # telegram caps a toast at 200
            line = f"{line}{_blocked_tail(block)}"
    await _answer(cq_id, toast, post_id=post_id)
    text = append_verdict(message.get("text"), line)
    # A reject (or a repeated reject tap — a Telegram replay — while no reason is recorded yet) swaps
    # the buttons for the reason keyboard instead of removing them; optional to tap.
    if decision == "reject" and (outcome == "rejected"
                                 or (outcome == "already_rejected" and _review_reason(row) is None)):
        platform = (row or {}).get("platform")
        what = f"the {str(platform).upper()} post" if platform else "this post"
        await _set_keyboard(chat_id, message, reject_reason_keyboard(post_id), post_id=post_id, text=text,
                            fallback_text=f"Why was {what} rejected? Tap a reason (optional).")
    else:
        await _edit(chat_id, message.get("message_id"), text, post_id=post_id)
    return {"ok": True, "outcome": outcome, "post_id": post_id}


async def _set_keyboard(chat_id: int, message: Dict[str, Any], keyboard: Dict[str, Any], *, post_id: str,
                        fallback_text: str, text: Optional[str] = None) -> None:
    """Swap the tapped message's keyboard — and its text too when `text` is given (one
    editMessageText); if it cannot be edited (too old, deleted), send the keyboard on a new message
    instead so the owner can still act."""
    message_id = message.get("message_id")
    try:
        if not _is_int(message_id):
            raise TelegramRequestError("no message_id to edit",
                                       method="editMessageText" if text is not None else "editMessageReplyMarkup")
        if text is not None:
            await telegram.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
        else:
            await telegram.edit_message_reply_markup(chat_id, message_id, keyboard)
        return
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        if isinstance(e, TelegramRequestError) and "message is not modified" in str(e.description or "").lower():
            # A replayed tap: the message already shows exactly this text and keyboard. A new message
            # would only repeat the buttons the owner is looking at.
            logger.info("marketing review: keyboard already in place post_id=%s (message is not modified)",
                        post_id)
            return
        logger.info("marketing review: keyboard edit failed post_id=%s (%s) — sending a new message", post_id, e)
    except Exception as e:
        # Not a Telegram answer (a bug): still offer the keyboard — the webhook never raises.
        logger.error("marketing review: keyboard edit raised post_id=%s (%s: %s) — sending a new message",
                     post_id, type(e).__name__, e, exc_info=True)
    try:
        await telegram.send_message(chat_id, fallback_text, reply_markup=keyboard)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review: could not offer the keyboard for post_id=%s (%s: %s)", post_id,
                       type(e).__name__, e)


async def _handle_reject_reason(reason: str, post_id: str, *, cq_id: str, chat_id: int,
                                message: Dict[str, Any], by: str) -> Dict[str, Any]:
    """A reason tap on a rejected post: RECORD it (`run_service.record_reject_reason`, fenced and
    idempotent — the same reason again writes nothing, a different later one wins), answer with a
    toast, and append "Reason: <label>" to the message with the keyboard removed. A post that is not
    rejected only gets a toast; a lost fence or a ledger error keeps the keyboard for another tap."""
    label = REJECT_REASONS.get(reason)
    if label is None:   # a verb with no label: the two tables drifted (a test pins them equal)
        logger.error("marketing review webhook: reject reason %r has no label; nothing recorded post_id=%s",
                     reason, post_id)
        await _answer(cq_id, "Unknown action", post_id=post_id)
        return {"ok": True, "refused": "unknown_action"}
    try:
        outcome = await get_marketing_run_service().record_reject_reason(post_id, reason, by=by)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review webhook: record_reject_reason FAILED post_id=%s reason=%s (%s: %s) — "
                     "the keyboard stays; the owner can tap again", post_id, reason, type(e).__name__, e,
                     exc_info=True)
        await _answer(cq_id, "Could not record that — tap again", post_id=post_id)
        return {"ok": True, "outcome": "error", "post_id": post_id}
    logger.info("marketing review webhook: reject reason post_id=%s reason=%s outcome=%s by=%s",
                post_id, reason, outcome, by)
    if outcome in ("recorded", "unchanged"):
        toast = f"Reason saved: {label}" if outcome == "recorded" else f"Reason already saved: {label}"
        await _answer(cq_id, toast, post_id=post_id)
        await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), f"Reason: {label}"),
                    post_id=post_id)
    elif outcome == "not_found":
        await _answer(cq_id, "Post not found", post_id=post_id)
        await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), "Post not found"),
                    post_id=post_id)
    elif outcome == "busy":
        await _answer(cq_id, "Could not record that — tap again", post_id=post_id)
    else:   # already_<status>: only a rejected post takes a reason
        status = outcome[len("already_"):] if outcome.startswith("already_") else ""
        if not _STATUS_WORD_RE.fullmatch(status):
            status = "not rejected"
        await _answer(cq_id, f"No reason recorded — the post is {status}", post_id=post_id)
    return {"ok": True, "outcome": outcome, "post_id": post_id}


async def _handle_post_action(decision: str, post_id: str, *, cq_id: str, chat_id: int,
                              message: Dict[str, Any], by: str) -> Dict[str, Any]:
    """Retract (two steps) and the answers to an escalated unknown outcome. Each RECORDS a decision
    (a conditional UPDATE) and wakes the publisher — the platform call happens in the publisher loop,
    never here (rules/marketing.md §2)."""
    svc = get_marketing_run_service()
    try:
        if decision in ("retract", "confirm_retract"):
            row = await svc.get_post(post_id)
            if row is not None and row.get("status") == "published" and not outlets.retract_capable(row.get("platform")):
                # No delete possible from here (no API, or its credentials are not set): never record a
                # request that would wait silently.
                text = (f"{str(row.get('platform')).upper()} cannot be deleted from here (no delete API or no "
                        f"credentials) — remove it by hand: {row.get('external_url') or '(no link)'}")
                await _answer(cq_id, "Remove it by hand", post_id=post_id)
                await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), text),
                            post_id=post_id)
                return {"ok": True, "outcome": "manual", "post_id": post_id}
        if decision == "retract":
            row = await svc.get_post(post_id)
            meta = (row or {}).get("metadata") if isinstance((row or {}).get("metadata"), dict) else {}
            if row is None:
                await _answer(cq_id, "Post not found", post_id=post_id)
                return {"ok": True, "outcome": "not_found", "post_id": post_id}
            pending = bool(meta.get("retract_requested_at")) and not meta.get("retract_closed_at")
            if row.get("status") != "published" or pending:
                state = "retract already requested" if pending else f"is {row.get('status')}"
                await _answer(cq_id, f"Nothing to do — the post {state}", post_id=post_id)
                return {"ok": True, "outcome": "noop", "post_id": post_id}
            await _set_keyboard(chat_id, message, confirm_retract_keyboard(post_id), post_id=post_id,
                                fallback_text=f"Delete the {str(row.get('platform')).upper()} post? "
                                              f"{row.get('external_url') or ''}".strip())
            await _answer(cq_id, "Confirm the delete", post_id=post_id)
            return {"ok": True, "outcome": "confirm_asked", "post_id": post_id}
        if decision == "cancel_retract":
            await _set_keyboard(chat_id, message, retract_keyboard(post_id), post_id=post_id,
                                fallback_text="Kept. Tap Retract to delete it later.")
            await _answer(cq_id, "Kept", post_id=post_id)
            return {"ok": True, "outcome": "kept", "post_id": post_id}
        if decision == "confirm_retract":
            outcome, _row = await svc.request_retract(post_id, by=by)
            if outcome == "requested":
                publisher_wake.wake()
            toast = {"requested": "Deleting…", "already_requested": "Already being deleted",
                     "not_found": "Post not found", "busy": "Could not record that — tap again"}.get(
                outcome, f"Not deleted — the post {outcome.replace('already_', 'is ')}")
            line = (f"🗑 Retract requested {_et_clock()}" if outcome == "requested" else toast)
            await _answer(cq_id, toast, post_id=post_id)
            if outcome != "busy":
                await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), line),
                            post_id=post_id)
            return {"ok": True, "outcome": outcome, "post_id": post_id}
        # live / not_posted
        outcome, _row = await svc.resolve_unknown(post_id, decision, by=by)
        if outcome in ("published", "failed"):
            publisher_wake.wake()
        line = {"published": f"✅ Marked live {_et_clock()}", "failed": f"❌ Marked not posted {_et_clock()}",
                "not_escalated": "Nothing to decide any more", "not_found": "Post not found"}.get(
            outcome, f"Already {outcome.replace('already_', '')}")
        await _answer(cq_id, line, post_id=post_id)
        await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), line), post_id=post_id)
        return {"ok": True, "outcome": outcome, "post_id": post_id}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review webhook: %s FAILED post_id=%s (%s: %s) — the buttons stay; tap again",
                     decision, post_id, type(e).__name__, e, exc_info=True)
        await _answer(cq_id, "Could not record that — tap again", post_id=post_id)
        return {"ok": True, "outcome": "error", "post_id": post_id}


# ── the webhook, review bundles ────────────────────────────────────────────────


def _tapped_bundle_id(message: Dict[str, Any]) -> Optional[str]:
    """The bundle the TAPPED message decides — read from its own keyboard (Telegram returns the message,
    keyboard included, with every tap): the id behind its ✅ Approve all / ❌ Reject all buttons."""
    markup = message.get("reply_markup") if isinstance(message.get("reply_markup"), dict) else {}
    rows = markup.get("inline_keyboard") if isinstance(markup.get("inline_keyboard"), list) else []
    for row in rows:
        for button in row if isinstance(row, list) else []:
            parsed = parse_callback_data(button.get("callback_data")) if isinstance(button, dict) else None
            if parsed and parsed[0] in ("bundle_approve", "bundle_reject"):
                return parsed[1]
    return None


def _bundle_kind_word(results: List[Dict[str, Any]]) -> str:
    kinds = {(review_bundle_of(r.get("row")) or {}).get("kind") for r in results}
    return "video" if kinds == {"video"} else ("posts" if kinds == {"post"} else "")


async def _edit_or_reply(chat_id: int, message: Dict[str, Any], text: str, *, reply_text: str, ref: str) -> None:
    """Edit the tapped message to `text`, keyboard removed. When it cannot be edited (too old, deleted,
    an inaccessible message) send `reply_text` as a new message under it instead — the per-platform
    results reach the owner either way. Never raises (except CancelledError)."""
    message_id = message.get("message_id")
    if _is_int(message_id):
        try:
            await telegram.edit_message_text(chat_id, message_id, text, reply_markup=_KEYBOARD_REMOVED)
            return
        except asyncio.CancelledError:
            raise
        except TelegramException as e:
            if isinstance(e, TelegramRequestError) and "message is not modified" in str(e.description or "").lower():
                logger.info("marketing review: result already shown ref=%s (message is not modified)", ref)
                return
            logger.info("marketing review: result edit failed ref=%s (%s) — sending it as a new message", ref, e)
        except Exception as e:
            logger.error("marketing review: result edit raised ref=%s (%s: %s) — sending it as a new message", ref,
                         type(e).__name__, e, exc_info=True)
    try:
        for chunk in split_message(reply_text):
            await telegram.send_message(chat_id, chunk,
                                        reply_to_message_id=message_id if _is_int(message_id) else None)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("marketing review: could not show the result for ref=%s (%s: %s) — the decision stands",
                       ref, type(e).__name__, e)


async def _handle_bundle_action(decision: str, target: str, *, cq_id: str, chat_id: int,
                                message: Dict[str, Any], by: str) -> Dict[str, Any]:
    """✅ Approve all / ❌ Reject all on a bundle (`target` = the bundle id), ✂ drop (`target` = a post
    id) and the bundle reasons. Each RECORDS a decision; the publisher sends (rules/marketing.md §2)."""
    if decision == "bundle_drop":
        return await _handle_bundle_drop(target, cq_id=cq_id, chat_id=chat_id, message=message, by=by)
    if decision.startswith(_BUNDLE_REASON_PREFIX):
        return await _handle_bundle_reason(decision[len(_BUNDLE_REASON_PREFIX):], target, cq_id=cq_id,
                                           chat_id=chat_id, message=message, by=by)
    verdict = "approve" if decision == "bundle_approve" else "reject"
    try:
        outcome, results = await get_marketing_run_service().review_bundle(target, verdict, reviewed_by=by)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review webhook: review_bundle FAILED bundle_id=%s decision=%s (%s: %s) — the "
                     "buttons stay; the owner can tap again", target, verdict, type(e).__name__, e, exc_info=True)
        await _answer(cq_id, "Could not record that — tap again", post_id=target)
        return {"ok": True, "outcome": "error", "bundle_id": target}
    logger.info("marketing review webhook: bundle_id=%s decision=%s outcome=%s by=%s", target, verdict, outcome, by)
    if outcome == "not_found":
        line = "Nothing to decide here — its posts were re-sent in a newer message, or are gone"
        await _answer(cq_id, "Nothing to decide here", post_id=target)
        await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), line), post_id=target)
        return {"ok": True, "outcome": outcome, "bundle_id": target}

    decided = [r for r in results if r.get("outcome") in ("approved", "rejected")]
    if verdict == "approve" and decided:
        publisher_wake.wake()   # publish within seconds, not at the next 10-minute tick
    lines, blocked = bundle_result_lines(results)
    if decided:
        head = (f"{'✅ Approved' if verdict == 'approve' else '❌ Rejected'} {_et_clock()} — "
                f"{len(decided)} of {len(results)}:")
        toast = f"Approved {len(decided)} ✅" if verdict == "approve" else f"Rejected {len(decided)} ❌"
        if blocked:
            toast += " — some will NOT be sent (see the message)"
    else:
        head, toast = "Nothing left to decide:", "Nothing left to decide"
    await _answer(cq_id, toast, post_id=target)
    result = "\n".join([head, *lines])
    text = append_verdict(message.get("text"), result)
    # A reject — or a replayed one while a member IT rejected has no reason yet — offers the reasons once.
    # A member rejected separately (✂ drop) had its own offer and never takes the bundle's reason. The test
    # is `_handle_bundle_reason`'s own (`_bundle_rejected`), so the keyboard is offered only where a tap records.
    unreasoned = any(_review_reason(r.get("row")) is None
                     and (r.get("outcome") == "rejected"
                          or (r.get("outcome") == "already_rejected" and _bundle_rejected(r.get("row"), target)))
                     for r in results)
    if verdict == "reject" and unreasoned:
        kind = _bundle_kind_word(results)
        await _set_keyboard(chat_id, message, bundle_reason_keyboard(target), post_id=target, text=text,
                            fallback_text=f"{result}\n\nWhy was the {kind + ' ' if kind else ''}bundle rejected? Tap a "
                                          f"reason (optional) — it is saved on every post Reject all rejected.")
    else:
        await _edit_or_reply(chat_id, message, text, reply_text=result, ref=target)
    return {"ok": True, "outcome": outcome, "bundle_id": target, "decided": len(decided)}


async def _handle_bundle_drop(post_id: str, *, cq_id: str, chat_id: int, message: Dict[str, Any],
                              by: str) -> Dict[str, Any]:
    """✂ drop: reject ONE member (`review_post`'s conditional UPDATE — a replay writes nothing) and keep
    the rest open: the tapped message gets a line and its keyboard is rebuilt from the members of ITS
    bundle (the id its own buttons name) that are still pending."""
    svc = get_marketing_run_service()
    try:
        outcome, row = await svc.review_post(post_id, "reject", reviewed_by=by)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review webhook: bundle drop FAILED post_id=%s (%s: %s) — the buttons stay; the "
                     "owner can tap again", post_id, type(e).__name__, e, exc_info=True)
        await _answer(cq_id, "Could not record that — tap again", post_id=post_id)
        return {"ok": True, "outcome": "error", "post_id": post_id}
    name = _platform_name(row) if row else "The post"
    if outcome == "rejected":
        toast, line = f"Dropped {name} ✂", f"✂ Dropped {name} {_et_clock()} — the rest stay open"
    elif outcome == "not_found":
        toast = line = "Post not found"
    else:
        toast, line = _verdict(outcome, row)
        line = f"{name}: {line}"
    logger.info("marketing review webhook: bundle drop post_id=%s outcome=%s by=%s", post_id, outcome, by)
    # The per-post ❌ offers the reasons; so does a drop (or a replayed one while no reason is recorded) —
    # on a small message of its own, since the decision message keeps the rest of the bundle's buttons.
    offer_reason = outcome == "rejected" or (outcome == "already_rejected" and _review_reason(row) is None)
    bundle_id = _tapped_bundle_id(message) or (review_bundle_of(row) or {}).get("id")
    still_open: List[Dict[str, Any]] = []
    if bundle_id:
        try:
            still_open = [r for r in await svc.bundle_posts(bundle_id) if r.get("status") == "pending_review"]
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The drop is recorded; only the keyboard cannot be rebuilt — leave it as it is (a second tap on
            # the same drop answers "Already rejected").
            logger.warning("marketing review webhook: could not re-read bundle_id=%s after a drop (%s: %s) — "
                           "the buttons stay as they are", bundle_id, type(e).__name__, e)
            await _answer(cq_id, toast, post_id=post_id)
            if offer_reason:
                await _offer_drop_reasons(chat_id, message, post_id, row)
            return {"ok": True, "outcome": outcome, "post_id": post_id}
    await _answer(cq_id, toast, post_id=post_id)
    text = append_verdict(message.get("text"), line)
    if bundle_id and still_open:
        await _set_keyboard(chat_id, message, bundle_keyboard(bundle_id, still_open), post_id=post_id, text=text,
                            fallback_text=f"{line}. The rest of this bundle:")
    else:
        await _edit(chat_id, message.get("message_id"), text, post_id=post_id)
    if offer_reason:
        await _offer_drop_reasons(chat_id, message, post_id, row)
    return {"ok": True, "outcome": outcome, "post_id": post_id}


async def _offer_drop_reasons(chat_id: int, message: Dict[str, Any], post_id: str,
                              row: Optional[Dict[str, Any]]) -> None:
    """After a ✂ drop: ONE small message, threaded under the tapped decision message, carrying the per-post
    reason keyboard (`reject_reason_keyboard` — its `reason_*` verbs are recorded by `_handle_reject_reason`,
    which edits THIS message). Best effort: the drop stands whatever happens here; never raises (except
    CancelledError)."""
    platform = (row or {}).get("platform")
    what = f"the {str(platform).upper()} post" if platform else "this post"
    message_id = message.get("message_id")
    try:
        await telegram.send_message(chat_id, f"Why was {what} dropped? Tap a reason (optional).",
                                    reply_markup=reject_reason_keyboard(post_id),
                                    reply_to_message_id=message_id if _is_int(message_id) else None)
    except asyncio.CancelledError:
        raise
    except TelegramException as e:
        logger.warning("marketing review webhook: could not offer the reject reasons after a drop post_id=%s (%s: "
                       "%s) — the drop stands, with no reason", post_id, type(e).__name__, e)
    except Exception as e:
        logger.error("marketing review webhook: offering the reject reasons after a drop raised post_id=%s (%s: %s) "
                     "— the drop stands, with no reason", post_id, type(e).__name__, e, exc_info=True)


async def _handle_bundle_reason(reason: str, bundle_id: str, *, cq_id: str, chat_id: int,
                                message: Dict[str, Any], by: str) -> Dict[str, Any]:
    """A reason tap after ❌ Reject all: RECORD it on every member THIS bundle's Reject all rejected
    (`_bundle_rejected` — one whose best-effort review record never landed included, its record rebuilt
    with the bundle id in the same write; `record_reject_reason(bundle_id=…)`: fenced, idempotent — the
    same reason again writes nothing, a later different one wins), then append "Reason: …" with the
    keyboard removed. A member rejected separately (✂ drop, which offered its own reasons) is left as it
    is — a later bundle reason must not overwrite the one the owner gave it — and the line names it. A
    ledger error or a lost fence keeps the keyboard for another tap (the posts already done answer
    `unchanged` then)."""
    label = REJECT_REASONS.get(reason)
    if label is None:   # a verb with no label: the two tables drifted (a test pins them equal)
        logger.error("marketing review webhook: bundle reason %r has no label; nothing recorded bundle_id=%s",
                     reason, bundle_id)
        await _answer(cq_id, "Unknown action", post_id=bundle_id)
        return {"ok": True, "refused": "unknown_action"}
    svc = get_marketing_run_service()
    outcomes: List[str] = []
    try:
        rows = await svc.bundle_posts(bundle_id)
        rejected = [r for r in rows if r.get("status") == "rejected" and canonical_post_id(r.get("id"))]
        mine = [r for r in rejected if _bundle_rejected(r, bundle_id)]
        for row in mine:
            outcomes.append(await svc.record_reject_reason(canonical_post_id(row.get("id")) or "", reason, by=by,
                                                           bundle_id=bundle_id))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("marketing review webhook: bundle reason FAILED bundle_id=%s reason=%s after %d post(s) (%s: "
                     "%s) — the keyboard stays; the owner can tap again", bundle_id, reason, len(outcomes),
                     type(e).__name__, e, exc_info=True)
        await _answer(cq_id, "Could not record that — tap again", post_id=bundle_id)
        return {"ok": True, "outcome": "error", "bundle_id": bundle_id}
    logger.info("marketing review webhook: bundle reason bundle_id=%s reason=%s outcomes=%s by=%s", bundle_id,
                reason, outcomes, by)
    if not rows:
        await _answer(cq_id, "Nothing to decide here", post_id=bundle_id)
        await _edit(chat_id, message.get("message_id"),
                    append_verdict(message.get("text"), "Nothing to decide here — its posts are gone"),
                    post_id=bundle_id)
        return {"ok": True, "outcome": "not_found", "bundle_id": bundle_id}
    if not rejected:
        await _answer(cq_id, "No reason recorded — nothing in this bundle is rejected", post_id=bundle_id)
        return {"ok": True, "outcome": "none_rejected", "bundle_id": bundle_id}
    # Rejected, but not by this Reject all: a ✂ drop (its review record has no bundle id) keeps its own
    # reason — so does a member a fresh read showed to be someone else's (`not_this_bundle`). A
    # `metadata.review` that is not an object (a hand edit) is not guessed at either: left alone and logged.
    # (A member with NO record and this bundle's stamp is in `mine`: its record write had failed.)
    lost_race = {canonical_post_id(r.get("id")) for r, o in zip(mine, outcomes) if o == "not_this_bundle"}
    separate: List[Dict[str, Any]] = []
    unrecorded: List[Dict[str, Any]] = []
    for row in rejected:
        if _bundle_rejected(row, bundle_id) and canonical_post_id(row.get("id")) not in lost_race:
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if isinstance(meta.get("review"), dict) or canonical_post_id(row.get("id")) in lost_race:
            separate.append(row)
            logger.info("marketing review webhook: bundle reason %s NOT recorded on post_id=%s platform=%s — it was "
                        "rejected separately (its own reason %r stays) bundle_id=%s", reason, row.get("id"),
                        row.get("platform"), _review_reason(row), bundle_id)
        else:
            unrecorded.append(row)
            logger.warning("marketing review webhook: bundle reason %s NOT recorded on post_id=%s platform=%s — it "
                           "is rejected but carries no review record (metadata.review is a %s, not an object), so "
                           "this Reject all cannot be shown to have decided it bundle_id=%s", reason, row.get("id"),
                           row.get("platform"), type(meta.get("review")).__name__, bundle_id)
    if "busy" in outcomes:
        await _answer(cq_id, "Could not record that for every post — tap again", post_id=bundle_id)
        return {"ok": True, "outcome": "busy", "bundle_id": bundle_id}
    saved = sum(1 for o in outcomes if o in ("recorded", "unchanged"))
    if not saved and not unrecorded and all(o == "not_this_bundle" for o in outcomes):
        # Every rejected member here was decided separately and keeps its own record: true as said.
        await _answer(cq_id, "No reason recorded — no post here was rejected by Reject all", post_id=bundle_id)
        return {"ok": True, "outcome": "none_by_bundle", "bundle_id": bundle_id}
    if saved:
        toast = f"Reason saved: {label}" if "recorded" in outcomes else f"Reason already saved: {label}"
        line = f"Reason: {label} — on {saved} rejected post{'' if saved == 1 else 's'}"
    else:   # nothing took it, and the line says why per platform (never a toast that contradicts the message)
        toast, line = "No reason recorded — see the message", f"Reason: {label} — not recorded"
    await _answer(cq_id, toast, post_id=bundle_id)
    if separate:
        line += f"; not on {', '.join(_platform_name(r) for r in separate)} (rejected separately)"
    if unrecorded:
        line += f"; not on {', '.join(_platform_name(r) for r in unrecorded)} (no review record — logged)"
    await _edit(chat_id, message.get("message_id"), append_verdict(message.get("text"), line), post_id=bundle_id)
    outcome = "recorded" if "recorded" in outcomes else ("unchanged" if saved else "not_recorded")
    return {"ok": True, "outcome": outcome, "bundle_id": bundle_id}


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
