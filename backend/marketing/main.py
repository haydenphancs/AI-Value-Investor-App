#!/usr/bin/env python3
"""
marketing/main.py — the MEDIA WORKER entrypoint of the marketing engine (design doc §12).
Run as `python -m marketing.main` from `backend/` (the Dockerfile's WORKDIR).

Runs as its own Railway cron service (`marketing/railway.toml`, image `marketing/Dockerfile`)
on an HOURLY schedule, and exits within seconds on every tick that has nothing to do. The
hourly cadence is the catch-up mechanism: Railway skips a cron tick whose predecessor is
still running and does no compute-first, so a killed or skipped slot would otherwise lose
the day. Each tick asks the backend to claim today's ET date; the UNIQUE `run_date` row is
the claim, and the row's `stage` checkpoint is where a resumed run continues from.

⚠️ STANDALONE BY DESIGN. Nothing under `backend/marketing/` imports `app.*`:
  * `app.config.Settings` requires SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY, and this
    container deliberately has neither — the worker holds NO Supabase key. It reaches the
    database only through the token-gated internal API and uploads media through signed
    URLs the API mints per object (least privilege: a compromised ML dependency here cannot
    read a user row or post to a brand account).
  * Importing `app.main` would start every lifespan loop a second time.
Keep it that way: stdlib + httpx only at module import; the media stages (Phases 3-4) load
their heavy imports lazily inside their stage functions.

Environment (all MARKETING_* so they never collide with the web service's variables):
  MARKETING_API_BASE_URL     https://<backend host>  (Railway private: http://<svc>.railway.internal:PORT)
  MARKETING_WORKER_TOKEN     shared secret, same value as the web service's setting
  MARKETING_RUN_HOUR_ET      first hour (ET, 0-23) a tick may start today's run; default 16
  MARKETING_WORKER_VERSION   free-form tag recorded on the run row; default "phase1"
  MARKETING_DRY_RUN          "true" (default) — recorded on the run; the publisher honours it
  MARKETING_FORCE            "1" bypasses the hour gate (manual runs, local testing)
  MARKETING_RUN_DATE         YYYY-MM-DD override of the ET date to claim
  SUPABASE_PUBLISHABLE_KEY   optional; sent as `apikey` on the signed-upload PUT if set. It is
                             the one Supabase value this container may hold: Supabase designs
                             the publishable key to be client-exposed (the app itself ships
                             none — it has no Supabase client). NEVER the service-role key.

Exit codes: 0 = done / nothing to do / not claimed; 1 = a stage failed (recorded on the run
as status=failed with last_error, so the first tick after MARKETING_RUN_STALE_SECONDS
re-claims and resumes, up to MARKETING_MAX_RUN_ATTEMPTS).

A tick BEFORE the window does one cheap thing before exiting: it asks the API to resume
YESTERDAY's run if — and only if — one exists and was left failed or abandoned (a container
killed after the last in-window tick). It never creates a run outside the window.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")

logging.basicConfig(
    level=(os.environ.get("MARKETING_LOG_LEVEL") or "INFO").strip().upper(),
    format="%(asctime)s %(levelname)s marketing_worker %(message)s",
)
# httpx logs every request line at INFO — including the signed-upload URL WITH its ?token=.
# That token is a live 2-hour upload credential; it must not land in Railway logs.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("marketing_worker")

# Mirrors RUN_STAGES in app/schemas/marketing.py. Duplicated on purpose (see the standalone
# note above); tests/test_marketing_worker.py asserts the two stay equal.
RUN_STAGES = ("planned", "selected", "scripted", "voiced", "rendered", "assets_ready")

_RETRYABLE_STATUS = {502, 503, 504}
_HTTP_ATTEMPTS = 3


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def should_run_now(now_et: datetime, run_hour_et: int, *, force: bool = False) -> bool:
    """Gate a tick on the ET wall clock. True from `run_hour_et:00` ET until midnight ET.

    The cron schedule is UTC and does not follow DST (Railway: "schedules are based on
    UTC"), so the ET decision is made HERE, on every hourly tick, and a tick before the
    window simply exits. `force` is for manual runs.
    """
    if force:
        return True
    if not 0 <= run_hour_et <= 23:
        raise ValueError(f"run_hour_et must be 0-23, got {run_hour_et}")
    return now_et.hour >= run_hour_et


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def next_stage(stage: str) -> Optional[str]:
    if stage not in RUN_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    i = RUN_STAGES.index(stage)
    return RUN_STAGES[i + 1] if i + 1 < len(RUN_STAGES) else None


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def ffmpeg_version() -> Optional[str]:
    """First line of `ffmpeg -version`, or None when ffmpeg is not on PATH."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (out.stdout or out.stderr or "").splitlines()
    return first[0].strip() if first else None


def build_manifest(
    *,
    worker_version: str,
    run_date: date,
    dry_run: bool,
    ffmpeg: Optional[str],
    fonts_dir: str,
) -> Dict[str, Any]:
    """What this image can do. Uploaded as the run's `manifest` asset so the first real
    cron tick proves the whole claim → register → signed PUT → complete path end to end,
    and so the Railway environment is inspectable from the database."""
    fonts: List[str] = []
    fonts_error: Optional[str] = None
    try:
        fonts = sorted(f for f in os.listdir(fonts_dir) if f.lower().endswith((".ttf", ".otf")))
    except OSError as e:
        # Loud, not silent: a missing/unreadable fonts dir means libass will fall back to
        # DejaVu in a published clip. The manifest carries the reason.
        fonts_error = f"{type(e).__name__}: {e}"
        logger.warning("fonts dir %s unreadable: %s", fonts_dir, fonts_error)
    # The manifest lands in a PUBLIC bucket, so it carries only what the pipeline needs to
    # know about itself (interpreter + ffmpeg versions, fonts) — no kernel/glibc fingerprint.
    return {
        "worker_version": worker_version,
        "run_date": run_date.isoformat(),
        "dry_run": dry_run,
        "python": platform.python_version(),
        "ffmpeg": ffmpeg,
        "fonts_dir": fonts_dir,
        "fonts": fonts,
        "fonts_error": fonts_error,
        "stages_implemented": [],  # filled in as Phases 2-4 land
        "generated_at": datetime.now(ET).isoformat(),
    }


# ── the API client ────────────────────────────────────────────────────────────


class WorkerAPIError(RuntimeError):
    pass


class BackendClient:
    """Thin client for `/api/v1/internal/marketing/*`. Every call is idempotent by contract,
    so transient 5xx / connection errors are retried a few times with a short backoff."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1/internal/marketing",
            headers={"X-Marketing-Worker-Token": token, "User-Agent": "caydex-marketing-worker"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        last: Optional[BaseException] = None
        for attempt in range(1, _HTTP_ATTEMPTS + 1):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TransportError as e:
                last = e
                logger.warning("backend %s %s transport error (attempt %d): %s", method, path, attempt, e)
                time.sleep(2 * attempt)
                continue
            if resp.status_code in _RETRYABLE_STATUS:
                last = WorkerAPIError(f"{method} {path} -> {resp.status_code}")
                logger.warning("backend %s %s -> %s (attempt %d)", method, path, resp.status_code, attempt)
                time.sleep(2 * attempt)
                continue
            if resp.status_code >= 400:
                # Structured error contract: surface error_code + message, never a bare status.
                try:
                    body = resp.json()
                except ValueError:
                    body = {"message": resp.text[:500]}
                raise WorkerAPIError(
                    f"{method} {path} -> {resp.status_code} "
                    f"{body.get('error_code', '?')}: {body.get('message', '')}"
                )
            return resp.json()
        raise WorkerAPIError(f"{method} {path} failed after {_HTTP_ATTEMPTS} attempts: {last}")

    def claim_run(
        self, run_date: date, worker_version: str, dry_run: bool, *,
        claim_nonce: Optional[str] = None, resume_only: bool = False,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "run_date": run_date.isoformat(), "worker_version": worker_version,
            "dry_run": dry_run, "resume_only": resume_only,
        }
        if claim_nonce:
            body["claim_nonce"] = claim_nonce
        return self._call("POST", "/runs/claim", json=body)

    def update_run(self, run_id: str, **fields: Any) -> Dict[str, Any]:
        return self._call("PATCH", f"/runs/{run_id}", json=fields)

    def register_asset(self, run_id: str, **fields: Any) -> Dict[str, Any]:
        return self._call("POST", f"/runs/{run_id}/assets", json=fields)

    def complete_asset(self, asset_id: str) -> Dict[str, Any]:
        return self._call("POST", f"/assets/{asset_id}/complete")

    def create_posts(self, run_id: str, posts: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._call("POST", f"/runs/{run_id}/posts", json={"posts": posts})


def upload_signed(upload: Dict[str, Any], data: bytes, *, apikey: Optional[str] = None) -> None:
    """PUT the bytes to the Storage signed-upload URL the API minted.

    Wire shape = supabase-py's `upload_to_signed_url`: multipart field `file` carrying
    (filename, bytes, content-type), `x-upsert: false` (paths are immutable — a second PUT to
    an existing key must fail, not overwrite). The `?token=` in the URL is the authorisation;
    the publishable `apikey` is added only if provided.
    """
    headers = {"x-upsert": "false"}
    if apikey:
        headers["apikey"] = apikey
    filename = upload["path"].rsplit("/", 1)[-1]
    with httpx.Client(timeout=120.0) as c:
        resp = c.put(
            upload["url"],
            files={"file": (filename, data, upload["content_type"])},
            headers=headers,
        )
    if resp.status_code == 409:
        # The key is immutable and the object is already there — a previous tick's PUT landed
        # but its `complete` never ran. Not an error: `complete_asset` HEAD-verifies next.
        logger.info("signed upload PUT %s -> 409 (already exists); continuing to complete", upload["path"])
        return
    if resp.status_code >= 400:
        raise WorkerAPIError(
            f"signed upload PUT {upload['path']} -> {resp.status_code}: {resp.text[:300]}"
        )


# ── stages ────────────────────────────────────────────────────────────────────


def stage_preflight(api: BackendClient, run: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    """Phase 1: prove the plumbing. Builds the image manifest, registers it as an asset,
    PUTs it through the signed URL, and asks the API to verify + mark it ready."""
    manifest = build_manifest(
        worker_version=ctx["worker_version"],
        run_date=date.fromisoformat(run["run_date"]),
        dry_run=ctx["dry_run"],
        ffmpeg=ffmpeg_version(),
        fonts_dir=ctx["fonts_dir"],
    )
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    reg = api.register_asset(
        run["id"],
        kind="manifest", ext="json", sha256=sha256_hex(payload), bytes=len(payload),
        metadata={"stage": "preflight"},
    )
    asset = reg["asset"]
    if reg.get("upload"):
        upload_signed(reg["upload"], payload, apikey=ctx.get("apikey"))
        api.complete_asset(asset["id"])
        logger.info("preflight manifest uploaded path=%s bytes=%d", asset["storage_path"], len(payload))
    else:
        logger.info("preflight manifest already ready path=%s", asset["storage_path"])
    api.update_run(
        run["id"],
        metadata={"preflight": {"ffmpeg": manifest["ffmpeg"], "fonts": manifest["fonts"],
                                "python": manifest["python"]}},
        timings={"preflight_s": round(time.monotonic() - ctx["t0"], 3)},
    )


# Ordered (completed-stage-name, fn). A stage runs when the run's checkpoint is BEFORE its
# name. Phase 1 has no real media stage, so the list is empty and the run is closed as
# `skipped` after preflight; Phases 2-4 append ("selected", stage_select), … here.
MEDIA_STAGES: List[Tuple[str, Callable[[BackendClient, Dict[str, Any], Dict[str, Any]], None]]] = []


def run_pipeline(api: BackendClient, run: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Execute the stages the run has not completed yet. Returns the final run status."""
    stage_preflight(api, run, ctx)

    if not MEDIA_STAGES:
        api.update_run(
            run["id"], status="skipped", finished=True,
            metadata={"skip_reason": "phase1_skeleton: no media stages implemented yet"},
        )
        return "skipped"

    completed = run.get("stage") or "planned"
    for name, fn in MEDIA_STAGES:
        if RUN_STAGES.index(name) <= RUN_STAGES.index(completed):
            logger.info("stage %s already complete — resuming past it", name)
            continue
        t = time.monotonic()
        logger.info("stage %s: start", name)
        fn(api, run, ctx)
        api.update_run(run["id"], stage=name, timings={f"{name}_s": round(time.monotonic() - t, 3)})
        completed = name
        logger.info("stage %s: done in %.1fs", name, time.monotonic() - t)

    api.update_run(run["id"], status="media_ready", finished=True)
    return "media_ready"


# ── main ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    base_url = os.environ.get("MARKETING_API_BASE_URL", "").strip()
    token = os.environ.get("MARKETING_WORKER_TOKEN", "").strip()
    if not base_url or not token:
        logger.error("MARKETING_API_BASE_URL and MARKETING_WORKER_TOKEN must be set")
        return 1

    run_hour = int(os.environ.get("MARKETING_RUN_HOUR_ET", "16"))
    worker_version = os.environ.get("MARKETING_WORKER_VERSION", "phase1").strip() or "phase1"
    dry_run = env_flag("MARKETING_DRY_RUN", True)
    force = env_flag("MARKETING_FORCE", False)
    now_et = datetime.now(ET)
    run_date = (
        date.fromisoformat(os.environ["MARKETING_RUN_DATE"])
        if os.environ.get("MARKETING_RUN_DATE")
        else now_et.date()
    )

    # One nonce per process: if the response to our own successful claim is lost, the retry
    # is recognised as ours instead of being told "in_progress, someone else has it".
    claim_nonce = uuid.uuid4().hex
    in_window = should_run_now(now_et, run_hour, force=force)
    resume_only = False
    if not in_window:
        # Outside the window the only legitimate work is finishing YESTERDAY's run if it was
        # killed after the last in-window tick. `resume_only` never creates a run.
        run_date = run_date - timedelta(days=1)
        resume_only = True
        logger.info(
            "tick before window: now_et=%s run_hour_et=%d — checking %s for a resumable run",
            now_et.strftime("%Y-%m-%d %H:%M"), run_hour, run_date,
        )

    ctx: Dict[str, Any] = {
        "worker_version": worker_version,
        "dry_run": dry_run,
        "fonts_dir": os.environ.get("MARKETING_FONTS_DIR", "/app/marketing/assets/fonts"),
        "apikey": os.environ.get("SUPABASE_PUBLISHABLE_KEY") or None,
        "t0": time.monotonic(),
    }

    api = BackendClient(base_url, token)
    try:
        claim = api.claim_run(
            run_date, worker_version, dry_run, claim_nonce=claim_nonce, resume_only=resume_only,
        )
        run = claim.get("run") or {}
        if not claim.get("claimed"):
            logger.info(
                "run_date=%s not claimed (reason=%s, status=%s, stage=%s) — exiting 0",
                run_date, claim.get("reason"), run.get("status"), run.get("stage"),
            )
            return 0
        logger.info(
            "run_date=%s CLAIMED run_id=%s attempt=%s resume_after=%s dry_run=%s",
            run_date, run["id"], run.get("attempts"), run.get("stage"), dry_run,
        )
        try:
            final = run_pipeline(api, run, ctx)
        except Exception as e:
            logger.error("pipeline FAILED run_id=%s: %s: %s", run["id"], type(e).__name__, e, exc_info=True)
            try:
                api.update_run(
                    run["id"], status="failed", finished=True,
                    last_error=f"{type(e).__name__}: {e}"[:2000],
                )
            except Exception as e2:  # the failure must still be visible somewhere
                logger.error("could not record failure on run %s: %s: %s", run["id"], type(e2).__name__, e2)
            return 1
        logger.info("run_date=%s finished status=%s in %.1fs", run_date, final, time.monotonic() - ctx["t0"])
        return 0
    except WorkerAPIError as e:
        logger.error("backend API error: %s", e)
        return 1
    finally:
        api.close()


if __name__ == "__main__":
    sys.exit(main())
