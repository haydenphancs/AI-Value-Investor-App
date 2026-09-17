"""
Marketing run ledger — persistence for migration 170 (SYSTEM_DESIGN_GUIDELINES §12).

Who calls this
--------------
* `app/api/v1/endpoints/marketing_internal.py` — the token-gated API the MEDIA WORKER
  (`marketing/main.py`, a Railway cron service holding NO Supabase key) uses to
  claim the day, checkpoint stages, register artefacts and hand over the day's posts.
* `app/services/marketing/publisher_service.py` — the PUBLISHER loop in the web lifespan,
  which claims `approved` posts one at a time before any external call.

The two processes never share a clock or a filesystem; every "have I already done this"
question is answered by a row here, never by inference from the boot time (the lesson of
migration 147).

Idempotency shapes, all enforced by the database, not by check-then-act:
* `marketing_runs.run_date` UNIQUE — the worker's daily claim is the INSERT; a second
  worker (or the same one after a Railway-skipped slot) gets 23505 and reads the row.
* `marketing_assets.storage_path` UNIQUE — content-addressed, so re-registering the same
  bytes on a resumed run returns the existing row instead of a second object.
* `marketing_posts (run_id, platform, format)` UNIQUE and `idempotency_key` UNIQUE — one
  ledger row per outlet, and the key the outlets themselves see.
* `claim_post`: `UPDATE … WHERE status = 'approved'` returning the row — atomic, so two
  publisher ticks cannot both take the same post.

Supabase is reached through `sb_exec` (never a bare `.execute()` on the loop —
`app/utils/supabase_async.py` explains why that matters on one uvicorn worker).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.schemas.marketing import (
    ASSET_EXTENSIONS,
    ASSET_KINDS,
    POST_FORMATS,
    POST_PLATFORMS,
    POST_STATUSES,
    RUN_STAGES,
    RUN_STATUSES,
)
from app.utils.market_hours import ET
from app.utils.supabase_async import sb_exec
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

RUNS = "marketing_runs"
ASSETS = "marketing_assets"
POSTS = "marketing_posts"

# Claim reasons — the worker branches on these strings, so they are part of the wire contract.
CLAIMED = "claimed"
ALREADY_DONE = "already_done"
IN_PROGRESS = "in_progress"
MEDIA_READY = "media_ready"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
NO_RUN = "no_run"

_TERMINAL_RUN_STATUSES = frozenset({"published", "skipped"})

# Columns the publisher may write through `mark_post`. A whitelist, so an adapter result dict
# can never smuggle a column (or a typo that PostgREST would 400 on) into the ledger.
_POST_WRITABLE = frozenset({
    "external_id", "external_url", "attempts", "last_error", "cost_micros", "metrics",
    "metadata", "claimed_at", "published_at", "approved_at", "approved_by",
})


class MarketingRunError(Exception):
    """A ledger operation could not be completed. Carries the operation and ids so the log
    line is diagnosable without a repro."""


class MarketingRunNotFound(MarketingRunError):
    pass


class MarketingAssetNotFound(MarketingRunError):
    pass


class MarketingAssetMissingInStorage(MarketingRunError):
    """The worker said it uploaded, but the object is not there. Refuse to mark it ready:
    a `ready` asset that 404s would publish a broken post."""


# ── pure helpers (unit-tested, no I/O) ─────────────────────────────────────────


def run_date_et(now: Optional[datetime] = None) -> date:
    """The ET calendar day a run belongs to. Marketing days are wall-clock days in New York,
    matching every other daily boundary in the app (`market_hours.ET`)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def next_stage(stage: str) -> Optional[str]:
    """The stage after `stage`, or None when the pipeline is complete."""
    if stage not in RUN_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    i = RUN_STAGES.index(stage)
    return RUN_STAGES[i + 1] if i + 1 < len(RUN_STAGES) else None


def storage_path_for(run_date: date, kind: str, sha256: str, ext: str) -> str:
    """Content-addressed object key: `<run_date>/<kind>-<sha256[:16]>.<ext>`.

    Immutable by construction — the same bytes always land on the same key, different bytes
    never collide with it, and nothing is ever overwritten in place (podcast directories
    and Meta both cache by URL)."""
    if kind not in ASSET_KINDS:
        raise ValueError(f"unknown asset kind {kind!r}")
    ext = ext.lower().lstrip(".")
    if ext not in ASSET_EXTENSIONS:
        raise ValueError(f"unknown extension {ext!r}")
    if len(sha256) != 64:
        raise ValueError("sha256 must be 64 hex chars")
    return f"{run_date.isoformat()}/{kind}-{sha256[:16].lower()}.{ext}"


def idempotency_key_for(run_date: date, platform: str, fmt: str) -> str:
    if platform not in POST_PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    if fmt not in POST_FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    return f"{run_date.isoformat()}:{platform}:{fmt}"


def decide_claim(
    existing: Optional[Dict[str, Any]],
    *,
    now: datetime,
    stale_seconds: int,
    max_attempts: int = 0,
    claim_nonce: Optional[str] = None,
) -> str:
    """Given the row already holding today's `run_date` (or None), decide what the worker
    may do. Pure, so the whole matrix is unit-tested.

    * no row → CLAIMED (insert)
    * published / skipped → ALREADY_DONE
    * media_ready → MEDIA_READY (the worker's half is done; the publisher owns the rest)
    * in_progress carrying OUR `claim_nonce` → CLAIMED (our own claim whose response was
      lost; the retry must not conclude "someone else has it")
    * in_progress, last touched less than `stale_seconds` ago → IN_PROGRESS (another
      worker, or a manual run beside the cron — leave it alone). Liveness is the LATER of
      `started_at` and `updated_at`: every stage checkpoint bumps `updated_at`, so a long
      healthy run keeps itself alive, and a killed one goes quiet.
    * `attempts` ≥ `max_attempts` (when > 0) → ATTEMPTS_EXHAUSTED: a deterministically
      failing day must not re-run every hourly tick until midnight.
    * in_progress but stale, planned, or failed → CLAIMED (re-claim and resume)
    """
    if existing is None:
        return CLAIMED
    status = existing.get("status")
    if status in _TERMINAL_RUN_STATUSES:
        return ALREADY_DONE
    if status == "media_ready":
        return MEDIA_READY
    if status == "in_progress":
        meta = existing.get("metadata") or {}
        if claim_nonce and isinstance(meta, dict) and meta.get("claim_nonce") == claim_nonce:
            return CLAIMED
        touched = max(
            (t for t in (_parse_ts(existing.get("started_at")), _parse_ts(existing.get("updated_at")))
             if t is not None),
            default=None,
        )
        if touched is not None and now - touched < timedelta(seconds=max(stale_seconds, 0)):
            return IN_PROGRESS
    if max_attempts > 0 and int(existing.get("attempts") or 0) >= max_attempts:
        return ATTEMPTS_EXHAUSTED
    # stale in_progress, planned, failed, or an unknown value written by a newer migration:
    # let the worker take it.
    return CLAIMED


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _exec(query: Any, *, op: str, **ids: Any) -> Any:
    """`sb_exec` with every failure re-raised as `MarketingRunError` carrying the operation
    and ids — except a 23505, which is re-raised as-is so callers can adopt the winner.
    Without this a raw postgrest `APIError` (22P02 on a non-UUID id, a 520 from the edge)
    reaches the endpoint classifier's generic tail and is reported as a REPORT failure."""
    try:
        return await sb_exec(query)
    except Exception as e:
        if is_unique_violation(e):
            raise
        raise MarketingRunError(
            f"{op} failed ({', '.join(f'{k}={v}' for k, v in ids.items())}): "
            f"{type(e).__name__}: {e}"
        ) from e


def _one(result: Any) -> Optional[Dict[str, Any]]:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


# ── the service ────────────────────────────────────────────────────────────────


class MarketingRunService:
    """Stateless; every call is one or two PostgREST round trips off the loop."""

    def __init__(self, supabase=None) -> None:
        self._sb = supabase

    @property
    def sb(self):
        return self._sb or get_supabase()

    # runs ------------------------------------------------------------------

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(
                self.sb.table(RUNS).select("*").eq("id", run_id).limit(1),
                op="get_run", run_id=run_id,
            )
        )

    async def get_run_by_date(self, run_date: date) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(
                self.sb.table(RUNS).select("*").eq("run_date", run_date.isoformat()).limit(1),
                op="get_run_by_date", run_date=run_date,
            )
        )

    async def claim_run(
        self,
        run_date: date,
        *,
        worker_version: str,
        dry_run: bool,
        now: Optional[datetime] = None,
        claim_nonce: Optional[str] = None,
        resume_only: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """INSERT the day's row, or decide against the one that exists. Returns (row, reason).

        The INSERT is the claim: a UNIQUE violation means someone got there first and we
        fall through to the decision matrix on THEIR row. A re-claim of a stale/failed row is
        a compare-and-swap conditioned on the `attempts` value we observed — a column the
        re-claim itself increments — so two workers racing on the same abandoned row cannot
        both win (conditioning on `status` alone was a no-op: the observed and the new value
        were both `in_progress`).

        `resume_only` never inserts: it is the out-of-window tick finishing a run that was
        killed after the last in-window tick, and (None, NO_RUN) means there is nothing.
        """
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        nonce_meta = {"claim_nonce": claim_nonce} if claim_nonce else {}

        if not resume_only:
            fresh = {
                "run_date": run_date.isoformat(),
                "status": "in_progress",
                "stage": "planned",
                "worker_version": worker_version,
                "dry_run": bool(dry_run),
                "attempts": 1,
                "started_at": stamp,
                "updated_at": stamp,
                "metadata": nonce_meta,
            }
            try:
                inserted = _one(await _exec(self.sb.table(RUNS).insert(fresh), op="claim_run.insert", run_date=run_date))
                if inserted:
                    logger.info(
                        "marketing run CLAIMED (new) run_date=%s run_id=%s worker=%s dry_run=%s",
                        run_date, inserted.get("id"), worker_version, dry_run,
                    )
                    return inserted, CLAIMED
            except Exception as e:
                if not is_unique_violation(e):
                    raise

        existing = await self.get_run_by_date(run_date)
        if existing is None:
            if resume_only:
                return None, NO_RUN
            # the row vanished between the 23505 and the read — treat as busy
            raise MarketingRunError(f"claim_run: run for {run_date} raced away")

        reason = decide_claim(
            existing,
            now=now,
            stale_seconds=settings.MARKETING_RUN_STALE_SECONDS,
            max_attempts=settings.MARKETING_MAX_RUN_ATTEMPTS,
            claim_nonce=claim_nonce,
        )
        if reason != CLAIMED:
            logger.info(
                "marketing run NOT claimed run_date=%s run_id=%s status=%s attempts=%s reason=%s",
                run_date, existing.get("id"), existing.get("status"), existing.get("attempts"),
                reason,
            )
            return existing, reason

        meta = existing.get("metadata") or {}
        if claim_nonce and isinstance(meta, dict) and meta.get("claim_nonce") == claim_nonce \
                and existing.get("status") == "in_progress":
            # Our own claim; the response was lost. Nothing to re-claim.
            logger.info(
                "marketing run claim RECOVERED by nonce run_date=%s run_id=%s",
                run_date, existing.get("id"),
            )
            return existing, CLAIMED

        observed_status = existing.get("status")
        observed_attempts = int(existing.get("attempts") or 0)
        reclaim = {
            "status": "in_progress",
            "attempts": observed_attempts + 1,
            "started_at": stamp,
            "finished_at": None,
            "last_error": None,
            "worker_version": worker_version,
            "dry_run": bool(dry_run),
            "updated_at": stamp,
            "metadata": {**(meta if isinstance(meta, dict) else {}), **nonce_meta},
        }
        updated = _one(
            await _exec(
                self.sb.table(RUNS)
                .update(reclaim)
                .eq("id", existing["id"])
                .eq("status", observed_status)
                .eq("attempts", observed_attempts),
                op="claim_run.reclaim", run_id=existing["id"],
            )
        )
        if updated is None:
            # Lost the race: someone else re-claimed between our read and our write.
            current = await self.get_run(existing["id"]) or existing
            logger.info(
                "marketing run re-claim LOST race run_date=%s run_id=%s now status=%s",
                run_date, existing["id"], current.get("status"),
            )
            return current, IN_PROGRESS
        logger.info(
            "marketing run RE-CLAIMED run_date=%s run_id=%s from status=%s attempt=%s stage=%s",
            run_date, updated.get("id"), observed_status, updated.get("attempts"),
            updated.get("stage"),
        )
        return updated, CLAIMED

    async def update_run(
        self,
        run_id: str,
        *,
        stage: Optional[str] = None,
        status: Optional[str] = None,
        content_class: Optional[str] = None,
        template_id: Optional[str] = None,
        source_ref: Optional[str] = None,
        last_error: Optional[str] = None,
        timings: Optional[Dict[str, float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        finished: bool = False,
    ) -> Dict[str, Any]:
        """Write the fields that were given; merge `timings`/`metadata` into the JSONB
        rather than replacing it, so each stage reports only its own numbers."""
        if stage is not None and stage not in RUN_STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        if status is not None and status not in RUN_STATUSES:
            raise ValueError(f"unknown status {status!r}")

        current = await self.get_run(run_id)
        if current is None:
            raise MarketingRunNotFound(f"run {run_id} not found")

        patch: Dict[str, Any] = {"updated_at": _now_iso()}
        if stage is not None:
            patch["stage"] = stage
        if status is not None:
            patch["status"] = status
        if content_class is not None:
            patch["content_class"] = content_class
        if template_id is not None:
            patch["template_id"] = template_id
        if source_ref is not None:
            patch["source_ref"] = source_ref
        if last_error is not None:
            patch["last_error"] = last_error[:2000]
        if timings:
            merged = dict(current.get("timings") or {})
            merged.update({k: float(v) for k, v in timings.items()})
            patch["timings"] = merged
        if metadata:
            merged_md = dict(current.get("metadata") or {})
            merged_md.update(metadata)
            patch["metadata"] = merged_md
        if finished:
            patch["finished_at"] = _now_iso()

        updated = _one(
            await _exec(self.sb.table(RUNS).update(patch).eq("id", run_id), op="update_run", run_id=run_id)
        )
        if updated is None:
            raise MarketingRunNotFound(f"run {run_id} vanished during update")
        logger.info(
            "marketing run updated run_id=%s stage=%s status=%s finished=%s",
            run_id, updated.get("stage"), updated.get("status"), finished,
        )
        return updated

    # assets ----------------------------------------------------------------

    async def register_asset(
        self,
        run_id: str,
        *,
        kind: str,
        ext: str,
        sha256: str,
        size_bytes: int,
        duration_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Insert the asset row and mint a signed upload URL for it.

        Returns (row, upload) where `upload` is None if the row already exists and is
        `ready` — a resumed run re-registering identical bytes must NOT re-upload (the
        object is immutable and Spotify/Meta cache it by URL).
        """
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        run_date = date.fromisoformat(str(run["run_date"]))
        path = storage_path_for(run_date, kind, sha256, ext)
        content_type = ASSET_EXTENSIONS[ext.lower().lstrip(".")]

        row = {
            "run_id": run_id,
            "kind": kind,
            "storage_path": path,
            "content_type": content_type,
            "bytes": int(size_bytes),
            "sha256": sha256.lower(),
            "duration_seconds": duration_seconds,
            "status": "pending_upload",
            "metadata": metadata or {},
            "updated_at": _now_iso(),
        }
        asset: Optional[Dict[str, Any]] = None
        try:
            asset = _one(await _exec(self.sb.table(ASSETS).insert(row), op="register_asset.insert", run_id=run_id, path=path))
        except Exception as e:
            if not is_unique_violation(e):
                raise
            asset = _one(
                await _exec(
                    self.sb.table(ASSETS).select("*").eq("storage_path", path).limit(1),
                    op="register_asset.select", path=path,
                )
            )
        if asset is None:
            raise MarketingRunError(f"register_asset: no row for {path}")
        if asset.get("status") == "ready":
            logger.info(
                "marketing asset already READY, no re-upload run_id=%s kind=%s path=%s",
                run_id, kind, path,
            )
            return asset, None

        # The wedge this closes: the worker's PUT landed but the process died before
        # `complete_asset`. The object exists, the row says pending_upload, and every later
        # `x-upsert: false` PUT to the same immutable key would 409 forever. So check the
        # bucket BEFORE minting a URL and finish the row here if the bytes are already there.
        try:
            already_there = await self._object_exists(path)
        except MarketingRunError as e:
            # Not fatal: fall through to minting a URL; a real outage surfaces on the PUT.
            logger.warning(
                "register_asset: existence pre-check failed for %s (%s) — minting anyway", path, e,
            )
            already_there = False
        if already_there:
            ready = _one(
                await _exec(
                    self.sb.table(ASSETS)
                    .update({"status": "ready", "updated_at": _now_iso()})
                    .eq("id", asset["id"]),
                    op="register_asset.ready", asset_id=asset["id"],
                )
            ) or {**asset, "status": "ready"}
            logger.info(
                "marketing asset object already in bucket, marked READY without re-upload "
                "run_id=%s kind=%s path=%s", run_id, kind, path,
            )
            return ready, None

        try:
            signed = await sb_exec_storage(
                lambda: self.sb.storage.from_(settings.MARKETING_MEDIA_BUCKET)
                .create_signed_upload_url(path)
            )
        except Exception as e:
            raise MarketingRunError(
                f"register_asset: signed upload URL failed for {path}: {type(e).__name__}: {e}"
            ) from e
        upload = {
            "method": "PUT",
            "url": signed["signed_url"],
            "token": signed["token"],
            "bucket": settings.MARKETING_MEDIA_BUCKET,
            "path": path,
            "content_type": content_type,
        }
        logger.info(
            "marketing asset registered run_id=%s kind=%s path=%s bytes=%s",
            run_id, kind, path, size_bytes,
        )
        return asset, upload

    async def _object_exists(self, path: str) -> bool:
        """Is `path` in the media bucket? Distinguishes ABSENT from OUTAGE.

        storage3's `exists()` answers False for ANY non-200 HEAD (a 5xx included), which
        would turn a Storage outage into "the worker never uploaded". So a False is confirmed
        with a prefix LIST, which raises on an outage — and that raise becomes
        `MarketingRunError` (503 → the worker retries) instead of MARKETING_ASSET_MISSING.
        """
        bucket = settings.MARKETING_MEDIA_BUCKET
        try:
            if await sb_exec_storage(lambda: self.sb.storage.from_(bucket).exists(path)):
                return True
        except Exception as e:
            raise MarketingRunError(
                f"storage HEAD failed for {path}: {type(e).__name__}: {e}"
            ) from e
        prefix, _, name = path.rpartition("/")
        try:
            listing = await sb_exec_storage(
                lambda: self.sb.storage.from_(bucket).list(prefix, {"limit": 100, "search": name})
            )
        except Exception as e:
            raise MarketingRunError(
                f"storage LIST failed for {path}: {type(e).__name__}: {e}"
            ) from e
        return any((item or {}).get("name") == name for item in (listing or []))

    async def complete_asset(self, asset_id: str) -> Dict[str, Any]:
        """Flip `pending_upload` → `ready` ONLY after the object is verifiably in the bucket.
        The worker's word is not enough: a `ready` row whose object 404s would publish a
        broken post, and nothing downstream re-checks."""
        asset = _one(
            await _exec(
                self.sb.table(ASSETS).select("*").eq("id", asset_id).limit(1),
                op="complete_asset.select", asset_id=asset_id,
            )
        )
        if asset is None:
            raise MarketingAssetNotFound(f"asset {asset_id} not found")
        if asset.get("status") == "ready":
            return asset
        path = asset["storage_path"]
        if not await self._object_exists(path):
            raise MarketingAssetMissingInStorage(
                f"asset {asset_id} at {path} is not in bucket "
                f"{settings.MARKETING_MEDIA_BUCKET}"
            )
        updated = _one(
            await _exec(
                self.sb.table(ASSETS)
                .update({"status": "ready", "updated_at": _now_iso()})
                .eq("id", asset_id),
                op="complete_asset.ready", asset_id=asset_id,
            )
        )
        if updated is None:
            raise MarketingAssetNotFound(f"asset {asset_id} vanished during complete")
        logger.info("marketing asset READY id=%s path=%s", asset_id, path)
        return updated

    async def list_assets(self, run_id: str) -> List[Dict[str, Any]]:
        res = await _exec(self.sb.table(ASSETS).select("*").eq("run_id", run_id), op="list_assets", run_id=run_id)
        return list(getattr(res, "data", None) or [])

    # posts -----------------------------------------------------------------

    async def create_posts(
        self, run_id: str, specs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """One ledger row per (platform, format). Born `pending_review` unless
        MARKETING_AUTO_PUBLISH is on. Re-creating an existing pair returns the existing row
        untouched — a resumed run must never reset a post an admin already approved."""
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        run_date = date.fromisoformat(str(run["run_date"]))
        # A dry-run RUN never auto-approves, whatever the web process's switch says: the
        # worker's rehearsal must not become a real post because a different service flipped
        # MARKETING_AUTO_PUBLISH. The flag rides on every row so the publisher sees it too.
        run_dry = bool(run.get("dry_run"))
        initial = "approved" if (settings.MARKETING_AUTO_PUBLISH and not run_dry) else "pending_review"
        out: List[Dict[str, Any]] = []
        for spec in specs:
            platform, fmt = spec["platform"], spec["format"]
            key = idempotency_key_for(run_date, platform, fmt)
            row = {
                "run_id": run_id,
                "platform": platform,
                "format": fmt,
                "status": initial,
                "title": spec.get("title"),
                "caption": spec.get("caption") or "",
                "asset_ids": list(spec.get("asset_ids") or []),
                "idempotency_key": key,
                "metadata": {**(spec.get("metadata") or {}), "dry_run": run_dry},
                "approved_at": _now_iso() if initial == "approved" else None,
                "approved_by": "auto" if initial == "approved" else None,
                "updated_at": _now_iso(),
            }
            try:
                created = _one(await _exec(self.sb.table(POSTS).insert(row), op="create_posts.insert", key=key))
            except Exception as e:
                if not is_unique_violation(e):
                    raise
                created = _one(
                    await _exec(
                        self.sb.table(POSTS).select("*").eq("idempotency_key", key).limit(1),
                        op="create_posts.select", key=key,
                    )
                )
            if created is None:
                raise MarketingRunError(f"create_posts: no row for {key}")
            out.append(created)
        logger.info(
            "marketing posts recorded run_id=%s n=%d initial_status=%s",
            run_id, len(out), initial,
        )
        return out

    async def list_posts(self, status: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        res = await _exec(
            self.sb.table(POSTS)
            .select("*")
            .eq("status", status)
            .order("created_at")
            .limit(limit),
            op="list_posts", status=status,
        )
        return list(getattr(res, "data", None) or [])

    async def claim_post(self, post_id: str) -> Optional[Dict[str, Any]]:
        """approved → queued, atomically. None means another tick took it (or an admin
        rejected it between the list and the claim)."""
        now = _now_iso()
        return _one(
            await _exec(
                self.sb.table(POSTS)
                .update({"status": "queued", "claimed_at": now, "updated_at": now})
                .eq("id", post_id)
                .eq("status", "approved"),
                op="claim_post", post_id=post_id,
            )
        )

    async def mark_post(self, post_id: str, status: str, **fields: Any) -> Dict[str, Any]:
        if status not in POST_STATUSES:
            raise ValueError(f"unknown post status {status!r}")
        unknown = set(fields) - _POST_WRITABLE
        if unknown:
            raise ValueError(f"mark_post: not writable: {sorted(unknown)}")
        patch = {"status": status, "updated_at": _now_iso(), **fields}
        updated = _one(
            await _exec(self.sb.table(POSTS).update(patch).eq("id", post_id), op="mark_post", post_id=post_id)
        )
        if updated is None:
            raise MarketingRunError(f"mark_post: post {post_id} not found")
        return updated


async def sb_exec_storage(fn):
    """Storage calls are sync too; run them off the loop like `sb_exec` does for PostgREST."""
    import asyncio

    return await asyncio.to_thread(fn)


_service: Optional[MarketingRunService] = None


def get_marketing_run_service() -> MarketingRunService:
    global _service
    if _service is None:
        _service = MarketingRunService()
    return _service
