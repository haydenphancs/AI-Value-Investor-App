"""Publish, expire and unpublish Wiser weekly pieces (plan §1.10) — the ONLY code that writes a weekly
row into `money_move_articles`, the table the app's Money Moves catalog is served from.

The order of every operation is chosen so a crash or a lost answer at ANY point leaves something a
later tick can finish — never a live article the ledger does not know about, and never a ledger that
claims a publish that did not happen:

PUBLISH (`publish_due`, every tick)
  0. The piece is `approved`, due (`scheduled_publish_at <= now`) and inside its hold (`now <
     hold_until`). Everything the live row needs (the draft, slug, title, category, a READY narration
     job with a weekly audio object) is checked FIRST, while the piece is still `approved`: a piece that
     can never be published fails loudly here (`approved → failed` + alert) instead of entering
     `publishing` and waiting out its hold.
  1. A dry-run piece goes `approved → rehearsed` and NEVER touches `money_move_articles` (the ledger also
     fences every write into publishing/published on `dry_run = false`, and the migration's CHECK refuses
     it — three layers).
  2. `approved → publishing` — one compare-and-swap that also requires the hold open and the publish time
     reached (`ledger.transition(within_hold_at=now, due_at=now)`) and stamps `publish_lease_until`.
  3. INSERT the row with ON CONFLICT (slug) DO NOTHING (an upsert that ignores duplicates): replaying it
     after a lost answer writes nothing new. `id = uuid5(NS_WEEKLY, slug)`, `source = 'weekly'`, the audio
     URL in its PUBLIC form (the content service signs it per request, `sign_money_moves`), and
     `content.isFeatured` equal to the `is_featured` column (true for a Deep Dive). The row is read back
     and must be OURS (same id, source weekly): a slug or title held by another article ends the piece
     `expired` with an alert — it can never succeed.
  4. `MoneyMovesContentService.invalidate(...)` — this instance serves the new catalog at once; every
     other instance converges through `cache_sync` on its own tick.
  5. `publishing → published` (+ `published_at`), then a SILENT "Published" reply with 🗑 Unpublish.

RECOVERY of `publishing` (same tick): a row whose publish lease ran out was abandoned mid-publish. If its
article row exists (and is ours) → `published`; else, while the hold is open → retake the lease and redo
steps 3-5; else → `expired` with an alert. Recovery ignores `hold_until` for the first branch on purpose:
a row that is live must be recorded live whatever the clock says.

EXPIRY (`expire_due`): an EXPIRABLE piece (planned … approved) whose hold has ended → `expired`, its open
narration jobs cancelled, the owner alerted. NEVER `publishing` — its row may already be live; recovery
decides it by row existence.

UNPUBLISH (`unpublish`, the owner's 🗑 → Confirm): DELETE the row FIRST, then `published → unpublished`,
then delete the weekly audio object and invalidate. The featured rule (`money_moves_content_service`)
falls back to the previous Deep Dive, then the bundle hero, with no swap writes, and never re-features a
`NEVER_FEATURE` slug — so this module writes nothing to any other row.

Every Supabase call is synchronous in the SDK and runs inside `asyncio.to_thread` (CLAUDE.md #6); ledger
writes go through `ledger` (its compare-and-swap contract), never a bare UPDATE here.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations import telegram
from app.integrations.telegram import TelegramRateLimitException
from app.services.learn_audio_urls import MONEY_MOVES_BUCKET, parse_storage_url
from app.services.learn_weekly import alerts, ledger, schedule
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly.errors import LearnWeeklyError
from app.services.marketing import review_service, telegram_pacing
from app.services.money_moves_content_service import SOURCE_WEEKLY, MoneyMovesContentService
from app.utils.market_hours import ET
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

#: The uuid5 namespace of weekly article ids — FIXED forever: a changed namespace would give a replayed
#: publish a new id, and the slug's ON CONFLICT would then hide a row whose id no longer matches.
#: Different from `scripts/seed_money_moves.py`'s NS, so a weekly id can never equal a bundle id.
NS_WEEKLY = uuid.UUID("317adb60-0daf-5e34-b1ec-dcb9bb52e39a")
ARTICLES = "money_move_articles"
#: Every weekly narration object lives under this prefix of the private `money-moves-media` bucket
#: (`audio/weekly/<slug>-<sha12>.m4a`). Unpublish deletes ONLY under it — a bundle clip is never touched.
WEEKLY_AUDIO_PREFIX = "audio/weekly/"
#: How long a `publishing` row is held by the instance publishing it before recovery may redo it. The
#: whole publish is a few round trips; five minutes is one tick of the loop.
PUBLISH_LEASE_SECONDS = 300
#: Rows read per status per tick (a handful of pieces a week exist in any one status).
SCAN_LIMIT = 50
#: Words read per minute for `readTimeMinutes` when the draft carries no usable value.
_READ_WPM = 200

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_AUDIO_PATH = re.compile(r"audio/weekly/[A-Za-z0-9][A-Za-z0-9._-]{0,200}")


class LearnPublishError(LearnWeeklyError):
    """A publish step failed in a way a later tick may get past (the row could not be read back, an
    unexpected Supabase error). The piece stays `publishing`; recovery redoes it after the lease."""


class LearnPublishConflict(LearnPublishError):
    """The live row cannot be written as ours: another article holds the slug or the case-folded title
    (the migration-204 UNIQUE indexes). Permanent — the piece ends `expired` with an alert."""


# ── small helpers ─────────────────────────────────────────────────────────────────────────────


def _aware(now: Any) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be a timezone-aware datetime, got {now!r}")
    return now.astimezone(timezone.utc)


def _ts(value: Any) -> Optional[datetime]:
    return ledger._parse_ts(value)


def _iso(dt: datetime) -> str:
    return ledger._iso(dt)


def _et(dt: Optional[datetime]) -> str:
    return dt.astimezone(ET).strftime("%a %b %d %H:%M ET") if dt else "?"


def article_id(slug: str) -> str:
    """The `money_move_articles.id` of the weekly piece `slug` (deterministic: a replay is idempotent)."""
    return str(uuid.uuid5(NS_WEEKLY, slug))


def is_weekly_audio_path(path: Any) -> bool:
    """A relative object path under `audio/weekly/` with no traversal — the only audio this module ever
    publishes or deletes."""
    return isinstance(path, str) and ".." not in path and bool(_AUDIO_PATH.fullmatch(path))


def sort_order_for(piece: Mapping[str, Any]) -> int:
    """The row's `sort_order` (plan §1.10): a Deep Dive `-(iso_year*100 + iso_week)` (newer is lower, so
    the newest flagged Deep Dive wins the featured rule), a slot `100000 + days since 2026-01-01`.
    ValueError on an unreadable key."""
    key = ledger.validate_period_key(piece.get("period_key"), piece.get("kind"))
    if piece.get("kind") == c.KIND_DEEP_DIVE:
        return schedule.deep_dive_sort_order(int(key[0:4]), int(key[6:8]))
    return schedule.slot_sort_order(date.fromisoformat(key[:10]))


def _narration_module() -> Any:
    """`learn_weekly.narration` (built beside this module), or None while it does not exist."""
    try:
        return importlib.import_module("app.services.learn_weekly.narration")
    except ModuleNotFoundError as e:
        if e.name != "app.services.learn_weekly.narration":
            raise
        return None


def audio_public_url(path: str) -> str:
    """The PUBLIC-form URL of a weekly narration object — what the bundle rows store, and what
    `learn_audio_urls.parse_storage_url` turns into a signed URL per request (the bucket is private, so
    the public form is not itself playable). Delegates to `narration.audio_public_url` when that module
    provides it, so the two cannot disagree."""
    narration = _narration_module()
    fn = getattr(narration, "audio_public_url", None) if narration is not None else None
    if callable(fn):
        return fn(path)
    base = str(settings.SUPABASE_URL or "").rstrip("/")
    return f"{base}/storage/v1/object/public/{MONEY_MOVES_BUCKET}/{path}"


def _words(draft: Mapping[str, Any]) -> int:
    total = 0
    for section in draft.get("sections") or ():
        if not isinstance(section, Mapping):
            continue
        for block in section.get("content") or ():
            if not isinstance(block, Mapping):
                continue
            if isinstance(block.get("text"), str):
                total += len(block["text"].split())
            for item in block.get("items") or ():
                if isinstance(item, str):
                    total += len(item.split())
    return total


def _invalidate(reason: str) -> None:
    """Process-local cache invalidation (the other instances follow through `cache_sync`). Never raises:
    a failed invalidation costs at most the cache TTL, and must not undo a publish that landed."""
    try:
        MoneyMovesContentService.invalidate(reason)
    except Exception as e:  # noqa: BLE001
        logger.error("learn publish: cache invalidation failed (%s: %s) — %s; the catalog refreshes on its "
                     "TTL or the next cache_sync", type(e).__name__, e, reason, exc_info=True)


# ── the live row ──────────────────────────────────────────────────────────────────────────────


async def ready_narration_job(piece: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The READY narration job whose audio the owner reviewed: the piece's `narration_job_id`, else (that
    pointer missing or not ready) the newest ready round. A failed READ raises (transient — the caller
    retries next tick); None means there is no ready audio at all."""
    jid = piece.get("narration_job_id")
    if jid:
        job = await ledger.get_job(jid)
        if job and job.get("status") == c.JOB_READY and str(job.get("piece_id")) == str(piece.get("id")):
            return job
        logger.warning("learn publish: piece %s points at narration job %s, which is %s — looking for the "
                       "newest ready round", piece.get("id"), jid, (job or {}).get("status", "missing"))
    jobs = await ledger.list_jobs(piece.get("id"))
    ready = [j for j in jobs if j.get("status") == c.JOB_READY and isinstance(j.get("round"), int)]
    return max(ready, key=lambda j: j["round"]) if ready else None


def build_article_row(piece: Mapping[str, Any], job: Optional[Mapping[str, Any]], *,
                      now: datetime) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(row, None) — the `money_move_articles` row for an approved piece — or (None, problem code).

    Pure. Every problem here is PERMANENT for this piece (no retry fixes a missing draft), so the caller
    fails the piece loudly rather than letting it wait out its hold."""
    now = _aware(now)
    kind = piece.get("kind")
    if kind not in c.KINDS:
        return None, "kind_unreadable"
    draft = piece.get("draft")
    if not isinstance(draft, Mapping) or not isinstance(draft.get("sections"), list) or not draft["sections"]:
        return None, "draft_missing"
    slug = piece.get("slug")
    if not (isinstance(slug, str) and len(slug) <= 160 and _SLUG.fullmatch(slug)):
        return None, "slug_missing"
    title = piece.get("title") or draft.get("title")
    if not (isinstance(title, str) and title.strip()):
        return None, "title_missing"
    category = piece.get("category") if piece.get("category") in c.CATEGORIES else draft.get("category")
    if category not in c.CATEGORIES:
        return None, "category_missing"
    try:
        sort_order = sort_order_for(piece)
    except (TypeError, ValueError) as e:
        logger.error("learn publish: piece %s has no usable sort order (%s: %s)", piece.get("id"),
                     type(e).__name__, e)
        return None, "period_key_unreadable"
    if job is None:
        return None, "narration_missing"
    path = job.get("audio_path")
    if not is_weekly_audio_path(path):
        logger.error("learn publish: piece %s job %s has audio_path %r outside %s", piece.get("id"),
                     job.get("id"), path, WEEKLY_AUDIO_PREFIX)
        return None, "narration_path_invalid"
    duration = job.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) \
            or duration <= 0:
        return None, "narration_duration_invalid"
    seconds = max(1, int(round(duration)))
    is_featured = kind == c.KIND_DEEP_DIVE
    try:
        audio_url = audio_public_url(path)
    except ValueError as e:      # `narration.audio_public_url` is stricter than our prefix check
        logger.error("learn publish: piece %s job %s audio path %r refused: %s", piece.get("id"), job.get("id"),
                     path, e)
        return None, "narration_path_invalid"

    content: Dict[str, Any] = copy.deepcopy(dict(draft))
    read_time = content.get("readTimeMinutes")
    if isinstance(read_time, bool) or not isinstance(read_time, int) or not 1 <= read_time <= 120:
        read_time = max(1, math.ceil(_words(content) / _READ_WPM))
    content.update({
        "slug": slug, "title": title, "category": category,
        # Equal to the column (a disagreement logs a WARNING on every catalog load).
        "isFeatured": is_featured, "sortOrder": sort_order, "publishedAt": _iso(now),
        "audioUrl": audio_url, "hasAudioVersion": True, "audioDurationSeconds": seconds,
        "readTimeMinutes": read_time,
    })
    author = content.get("author") if isinstance(content.get("author"), Mapping) else {}
    row = {
        "id": article_id(slug), "slug": slug, "title": title,
        "subtitle": content.get("subtitle") if isinstance(content.get("subtitle"), str) else None,
        "category": category,
        "author_name": author.get("name") if isinstance(author.get("name"), str) else None,
        "published_at": _iso(now), "read_time_minutes": read_time,
        "view_count": content.get("viewCount") if isinstance(content.get("viewCount"), str) else "",
        "is_featured": is_featured, "has_audio_version": True, "audio_url": audio_url,
        "audio_duration_seconds": seconds, "sort_order": sort_order, "source": SOURCE_WEEKLY,
        "content": content, "updated_at": _iso(now),
    }
    try:
        json.dumps(row, allow_nan=False)
    except (TypeError, ValueError) as e:
        logger.error("learn publish: piece %s draft is not storable JSON (%s: %s)", piece.get("id"),
                     type(e).__name__, e)
        return None, "draft_not_json"
    return row, None


def _read_article_sync(slug: str) -> Optional[Dict[str, Any]]:
    res = get_supabase().table(ARTICLES).select("id,slug,source,title,audio_url").eq("slug", slug).limit(1).execute()
    data = getattr(res, "data", None) or []
    if isinstance(data, dict):
        data = [data]
    return data[0] if data and isinstance(data[0], dict) else None


def _write_article_sync(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """INSERT … ON CONFLICT (slug) DO NOTHING, then read the slug back. A unique violation on another
    index (the case-folded title, or the id) raises LearnPublishConflict."""
    try:
        get_supabase().table(ARTICLES).upsert(row, on_conflict="slug", ignore_duplicates=True).execute()
    except Exception as e:
        if is_unique_violation(e):
            raise LearnPublishConflict(f"money_move_articles refused the row: {type(e).__name__}: {e}",
                                       slug=row.get("slug")) from e
        raise LearnPublishError(f"money_move_articles write failed: {type(e).__name__}: {e}",
                                slug=row.get("slug")) from e
    try:
        return _read_article_sync(row["slug"])
    except Exception as e:
        raise LearnPublishError(f"money_move_articles read-back failed: {type(e).__name__}: {e}",
                                slug=row.get("slug")) from e


def _ours(stored: Optional[Mapping[str, Any]], slug: str) -> bool:
    return bool(stored) and stored.get("source") == SOURCE_WEEKLY and str(stored.get("id")) == article_id(slug)


def _delete_article_sync(slug: str) -> int:
    # DESTRUCTIVE — removes the live weekly article (the owner's confirmed 🗑 Unpublish). Fenced on
    # `source = 'weekly'`: a bundle article can never be deleted from here whatever slug reaches it. The
    # approved draft stays in learn_weekly_pieces.draft, so a re-publish is a rebuild, not a data loss.
    res = get_supabase().table(ARTICLES).delete().eq("slug", slug).eq("source", SOURCE_WEEKLY).execute()
    return len(getattr(res, "data", None) or [])


def _remove_audio_sync(path: str) -> None:
    # DESTRUCTIVE — the unpublished piece's narration object. Only ever a path under audio/weekly/
    # (checked by the caller AND here): bundle narration lives beside it in the same bucket.
    if not is_weekly_audio_path(path):
        raise ValueError(f"refusing to delete {path!r}: not a weekly narration path")
    get_supabase().storage.from_(MONEY_MOVES_BUCKET).remove([path])


# ── publish ───────────────────────────────────────────────────────────────────────────────────


async def _end(piece: Mapping[str, Any], *, from_status: str, to_status: str, reason: str, stage: str,
               fence: Optional[Mapping[str, Any]] = None,
               extra: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Move a piece to a terminal status with its reason, then cancel its open jobs and alert."""
    fields = {"last_error": reason, "last_stage": stage, **(extra or {})}
    moved = await ledger.transition(piece.get("id"), from_statuses=[from_status], to_status=to_status,
                                    fields=fields, fence=fence)
    if moved is None:
        return None
    try:
        await ledger.cancel_open_jobs(moved["id"], reason=f"{to_status}: {reason}")
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the piece is terminal; a stray job only renders for nothing
        logger.error("learn publish: piece %s is %s but cancelling its open jobs failed (%s: %s)",
                     moved.get("id"), to_status, type(e).__name__, e, exc_info=True)
    await alerts.notify_terminal(moved, status=to_status, reason=reason, stage=stage)
    return moved


async def _published_reply(piece: Mapping[str, Any], row: Mapping[str, Any], now: datetime) -> None:
    """The SILENT "Published" reply under the decision message, with 🗑 Unpublish (two-step). Best effort:
    the decision message already carries an Unpublish button since Approve, so a lost reply strands
    nothing. Its id joins `review_message_ids` so its own button passes the review fence."""
    from app.services.learn_weekly import review   # review imports this module at load

    chat_id = review_service.review_chat_id()
    if not review_service.is_configured() or chat_id is None or telegram_pacing.is_rate_limited():
        logger.warning("learn publish: piece %s published — the Telegram reply was not sent (bot not "
                       "configured or flood control); Unpublish stays on the decision message", piece.get("id"))
        return
    ids = [i for i in piece.get("review_message_ids") or [] if type(i) is int]
    text = (f"✅ Published {now.astimezone(ET).strftime('%H:%M ET')}: "
            f"{alerts.scrub(row.get('title'), 200)} ({alerts.kind_label(piece.get('kind'))} "
            f"{piece.get('period_key')}). It is in Money Moves now; open apps see it on their next load.")
    try:
        await telegram_pacing.PACER.wait()
        env = await telegram.send_message(chat_id, text, reply_markup=review.unpublish_keyboard(str(piece["id"])),
                                          reply_to_message_id=ids[0] if ids else None, disable_notification=True)
    except asyncio.CancelledError:
        raise
    except TelegramRateLimitException as e:
        telegram_pacing.note_rate_limit(e.retry_after)
        logger.warning("learn publish: piece %s published — reply hit flood control (%s)", piece.get("id"), e)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("learn publish: piece %s published — reply failed (%s: %s)", piece.get("id"),
                       type(e).__name__, e)
        return
    await review.remember_message_id(piece, review.message_id_of(env))


async def _finish_publish(piece: Mapping[str, Any], now: datetime,
                          row: Optional[Dict[str, Any]] = None) -> str:
    """Steps 3-5 for a piece WE hold in `publishing`. Returns published | lost | expired."""
    pid, slug = piece.get("id"), piece.get("slug")
    if row is None:
        row, problem = build_article_row(piece, await ready_narration_job(piece), now=now)
        if problem:
            moved = await _end(piece, from_status=c.PUBLISHING, to_status=c.EXPIRED, reason=problem,
                               stage="publish", fence={"publish_lease_until": piece.get("publish_lease_until")},
                               extra={"publish_lease_until": None})
            return "expired" if moved else "lost"
    try:
        stored = await asyncio.to_thread(_write_article_sync, row)
    except LearnPublishConflict as e:
        logger.error("learn publish: piece %s (%s) can never be written: %s", pid, slug, e)
        moved = await _end(piece, from_status=c.PUBLISHING, to_status=c.EXPIRED, reason="title_or_slug_conflict",
                           stage="publish", fence={"publish_lease_until": piece.get("publish_lease_until")},
                           extra={"publish_lease_until": None})
        return "expired" if moved else "lost"
    if stored is None:
        raise LearnPublishError("the article row is missing right after its insert", piece_id=pid, slug=slug)
    if not _ours(stored, row["slug"]):
        logger.error("learn publish: slug %s is held by another article (id=%s source=%s) — piece %s can "
                     "never publish", row["slug"], stored.get("id"), stored.get("source"), pid)
        moved = await _end(piece, from_status=c.PUBLISHING, to_status=c.EXPIRED, reason="slug_conflict",
                           stage="publish", fence={"publish_lease_until": piece.get("publish_lease_until")},
                           extra={"publish_lease_until": None})
        return "expired" if moved else "lost"
    _invalidate(f"learn publish {row['slug']}")
    done = await ledger.transition(pid, from_statuses=[c.PUBLISHING], to_status=c.PUBLISHED,
                                   fields={"published_at": now, "publish_lease_until": None,
                                           "last_stage": "publish", "last_error": None})
    if done is None:
        logger.info("learn publish: piece %s row is live; another instance recorded the publish", pid)
        return "lost"
    logger.info("learn publish: piece %s (%s) PUBLISHED as %s (sort_order %s, featured %s)", pid,
                done.get("period_key"), row["slug"], row["sort_order"], row["is_featured"])
    await _published_reply(done, row, now)
    return "published"


async def _rehearse(piece: Mapping[str, Any], now: datetime) -> str:
    """A dry-run piece at its publish time: `approved → rehearsed`, nothing written to the catalog."""
    moved = await ledger.transition(piece["id"], from_statuses=[c.APPROVED], to_status=c.REHEARSED,
                                    fields={"last_stage": "publish"},
                                    fence={"review_round": piece.get("review_round")})
    if moved is None:
        return "lost"
    logger.info("learn publish: DRY RUN piece %s (%s) rehearsed at %s — nothing written to %s", piece["id"],
                piece.get("period_key"), _iso(now), ARTICLES)
    await alerts.notify(f"🧪 DRY RUN rehearsed: “{alerts.scrub(piece.get('title') or piece.get('slug'), 160)}” "
                        f"({alerts.kind_label(piece.get('kind'))} {piece.get('period_key')}) would have been "
                        f"published now — nothing went live.", loud=False)
    return "rehearsed"


async def _publish_approved(piece: Mapping[str, Any], now: datetime) -> str:
    pid = piece.get("id")
    sched, hold = _ts(piece.get("scheduled_publish_at")), _ts(piece.get("hold_until"))
    if sched is None or hold is None:
        logger.error("learn publish: piece %s has unreadable times (publish %r, hold %r) — not published",
                     pid, piece.get("scheduled_publish_at"), piece.get("hold_until"))
        return "failed"
    if now < sched:
        return "waiting"
    if now >= hold:
        return "expiring"            # expire_due ends it; Approve after the hold is refused anyway
    dry = piece.get("dry_run")
    if dry is True:
        return await _rehearse(piece, now)
    if dry is not False:
        # NOT NULL in the schema; anything else is a corrupt row — fail CLOSED: never publish on a guess.
        logger.error("learn publish: piece %s has dry_run=%r — never published on an unreadable flag", pid, dry)
        return "failed"
    row, problem = build_article_row(piece, await ready_narration_job(piece), now=now)
    if problem:
        logger.error("learn publish: approved piece %s (%s) cannot be published: %s", pid,
                     piece.get("period_key"), problem)
        moved = await _end(piece, from_status=c.APPROVED, to_status=c.FAILED, reason=problem, stage="publish",
                           fence={"review_round": piece.get("review_round")})
        return "failed" if moved else "lost"
    moved = await ledger.transition(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                                    fields={"publish_lease_until": now + timedelta(seconds=PUBLISH_LEASE_SECONDS)},
                                    fence={"review_round": piece.get("review_round"),
                                           "draft_sha256": piece.get("draft_sha256")},
                                    within_hold_at=now, due_at=now)
    if moved is None:
        return "lost"
    return await _finish_publish(moved, now, row=row)


async def _recover(piece: Mapping[str, Any], now: datetime) -> str:
    """A `publishing` row: in progress (lease running), recorded live (row exists), redone (inside the
    hold) or expired (after it)."""
    pid = piece.get("id")
    lease = _ts(piece.get("publish_lease_until"))
    if lease is not None and lease > now:
        return "in_progress"
    observed = {"publish_lease_until": piece.get("publish_lease_until")}
    slug = piece.get("slug")
    stored = None
    if isinstance(slug, str) and _SLUG.fullmatch(slug):
        try:
            stored = await asyncio.to_thread(_read_article_sync, slug)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise LearnPublishError(f"recovery could not read the article row: {type(e).__name__}: {e}",
                                    piece_id=pid, slug=slug) from e
    if stored is not None:
        if not _ours(stored, slug):
            moved = await _end(piece, from_status=c.PUBLISHING, to_status=c.EXPIRED, reason="slug_conflict",
                               stage="publish", fence=observed, extra={"publish_lease_until": None})
            return "expired" if moved else "lost"
        _invalidate(f"learn publish recovery {slug}")
        done = await ledger.transition(pid, from_statuses=[c.PUBLISHING], to_status=c.PUBLISHED, fence=observed,
                                       fields={"published_at": now, "publish_lease_until": None,
                                               "last_stage": "publish", "last_error": None})
        if done is None:
            return "lost"
        logger.warning("learn publish: piece %s was left publishing with its row live — recorded published", pid)
        await _published_reply(done, stored, now)
        return "recovered"
    hold = _ts(piece.get("hold_until"))
    if hold is not None and now < hold:
        taken = await ledger.transition(pid, from_statuses=[c.PUBLISHING], to_status=c.PUBLISHING, fence=observed,
                                        fields={"publish_lease_until": now + timedelta(seconds=PUBLISH_LEASE_SECONDS)})
        if taken is None:
            return "lost"
        logger.warning("learn publish: piece %s was left publishing with no row — redoing the publish", pid)
        result = await _finish_publish(taken, now)
        return "recovered" if result == "published" else result
    moved = await _end(piece, from_status=c.PUBLISHING, to_status=c.EXPIRED,
                       reason="publish_not_completed_before_hold_end", stage="publish", fence=observed,
                       extra={"publish_lease_until": None})
    return "expired" if moved else "lost"


async def publish_due(now: datetime) -> Dict[str, int]:
    """One publish sweep (every tick): recover abandoned `publishing` rows, then publish (or rehearse)
    every approved piece that is due. Per-piece failures are logged and counted, never raised."""
    now = _aware(now)
    out: Dict[str, int] = {}

    def count(key: str) -> None:
        out[key] = out.get(key, 0) + 1

    for status, handler in ((c.PUBLISHING, _recover), (c.APPROVED, _publish_approved)):
        try:
            pieces = await ledger.list_pieces(statuses=[status], limit=SCAN_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("learn publish: could not list %s pieces (%s: %s)", status, type(e).__name__, e,
                         exc_info=True)
            count("failed")
            continue
        for piece in pieces:
            try:
                count(await handler(piece, now))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — one piece never stops the sweep
                logger.error("learn publish: piece %s (%s, %s) failed this tick (%s: %s) — retried next tick",
                             piece.get("id"), piece.get("period_key"), status, type(e).__name__, e,
                             exc_info=True)
                count("failed")
    return out


async def expire_due(now: datetime) -> Dict[str, int]:
    """Every EXPIRABLE piece whose hold has ended → `expired` (open narration jobs cancelled, owner
    alerted). Never `publishing` — not in EXPIRABLE_STATUSES, and each move is fenced on the status read."""
    now = _aware(now)
    out = {"expired": 0, "lost": 0, "failed": 0}
    try:
        pieces = await ledger.list_pieces(statuses=sorted(c.EXPIRABLE_STATUSES), limit=SCAN_LIMIT * 2)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("learn expire: could not list pieces (%s: %s)", type(e).__name__, e, exc_info=True)
        out["failed"] += 1
        return out
    for piece in pieces:
        status = piece.get("status")
        if status not in c.EXPIRABLE_STATUSES:      # defensive: never trust the read for this rule
            continue
        hold = _ts(piece.get("hold_until"))
        if hold is None:
            logger.error("learn expire: piece %s has an unreadable hold_until %r", piece.get("id"),
                         piece.get("hold_until"))
            out["failed"] += 1
            continue
        if now < hold:
            continue
        try:
            moved = await _end(piece, from_status=status, to_status=c.EXPIRED, reason=f"hold_ended_in_{status}",
                               stage=piece.get("last_stage") or status, fence={"hold_until": piece.get("hold_until")})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("learn expire: piece %s could not be expired (%s: %s)", piece.get("id"),
                         type(e).__name__, e, exc_info=True)
            out["failed"] += 1
            continue
        out["expired" if moved else "lost"] += 1
    return out


# ── unpublish ─────────────────────────────────────────────────────────────────────────────────


async def unpublish(piece_id: Any, *, now: datetime) -> bool:
    """The owner's confirmed 🗑: take a published weekly piece out of Money Moves. True when the piece is
    `unpublished` afterwards (by this call or a concurrent one).

    DELETE first, ledger second: a crash between them leaves `published` with no row — the next tap
    finishes it (the DELETE is idempotent) — whereas the other order could leave a live article the
    ledger calls unpublished, which nothing would ever revisit. No other row is written: the featured
    rule falls back to the previous flagged Deep Dive (never a NEVER_FEATURE slug)."""
    now = _aware(now)
    piece = await ledger.get_piece_by_id(piece_id)
    if piece is None:
        logger.warning("learn unpublish: no piece %r", piece_id)
        return False
    if piece.get("status") != c.PUBLISHED:
        logger.info("learn unpublish: piece %s is %s, not published — nothing done", piece.get("id"),
                    piece.get("status"))
        return piece.get("status") == c.UNPUBLISHED
    slug = piece.get("slug")
    if not (isinstance(slug, str) and _SLUG.fullmatch(slug)):
        logger.error("learn unpublish: piece %s has an unusable slug %r — nothing deleted", piece.get("id"), slug)
        return False
    # The audio the LIVE row points at (read before the row goes), else the round publish would pick.
    live_path = None
    try:
        stored = await asyncio.to_thread(_read_article_sync, slug)
        parsed = parse_storage_url((stored or {}).get("audio_url"), allowed={MONEY_MOVES_BUCKET}) \
            if stored and stored.get("source") == SOURCE_WEEKLY else None
        live_path = parsed[1] if parsed else None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — only decides which object to clean up; the job is the fallback
        logger.warning("learn unpublish: piece %s live row unreadable before the delete (%s: %s)", piece["id"],
                       type(e).__name__, e)
    deleted = await asyncio.to_thread(_delete_article_sync, slug)
    _invalidate(f"learn unpublish {slug}")
    moved = await ledger.transition(piece["id"], from_statuses=[c.PUBLISHED], to_status=c.UNPUBLISHED,
                                    fields={"unpublished_at": now, "last_stage": "unpublish"})
    logger.warning("learn unpublish: piece %s (%s) slug %s — %d row(s) deleted, ledger %s", piece["id"],
                   piece.get("period_key"), slug, deleted, "unpublished" if moved else "already moved")
    if moved is None:
        fresh = await ledger.get_piece_by_id(piece["id"])
        return bool(fresh) and fresh.get("status") == c.UNPUBLISHED
    try:
        path = live_path or ((await ready_narration_job(piece)) or {}).get("audio_path")
        if is_weekly_audio_path(path):
            await asyncio.to_thread(_remove_audio_sync, path)
        elif path:
            logger.error("learn unpublish: piece %s audio path %r is not a weekly path — NOT deleted",
                         piece["id"], path)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the object is private; a leftover costs storage, not exposure
        logger.warning("learn unpublish: piece %s narration object not deleted (%s: %s)", piece["id"],
                       type(e).__name__, e)
    return True


# ── cross-instance cache sync ─────────────────────────────────────────────────────────────────


def _latest_change_sync() -> Optional[str]:
    """max(published_at, unpublished_at) over every piece — the last moment ANY instance changed what
    Money Moves serves. Reads the ledger table directly (two indexed ORDER BY … LIMIT 1 reads)."""
    sb = get_supabase()
    stamps: List[datetime] = []
    for col in ("published_at", "unpublished_at"):
        res = (sb.table(ledger.PIECES).select(col).not_.is_(col, "null")
               .order(col, desc=True).limit(1).execute())
        for r in getattr(res, "data", None) or []:
            ts = _ts(r.get(col)) if isinstance(r, dict) else None
            if ts is not None:
                stamps.append(ts)
    return _iso(max(stamps)) if stamps else None


async def cache_sync() -> Optional[bool]:
    """Each instance's tick: invalidate this instance's Money Moves cache when the ledger changed after it
    was built (a publish or unpublish on ANOTHER instance — Railway overlaps two on a deploy). Returns
    whether it invalidated; None when the ledger could not be read (logged; the cache TTL still bounds it)."""
    try:
        stamp = await asyncio.to_thread(_latest_change_sync)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("learn cache_sync: ledger stamp unreadable (%s: %s) — cache left as is", type(e).__name__, e)
        return None
    return MoneyMovesContentService.sync_with_stamp(stamp)
