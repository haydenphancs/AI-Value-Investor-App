"""
Marketing run ledger — persistence for migration 170 (SYSTEM_DESIGN_GUIDELINES §12).

Who calls this
--------------
* `app/api/v1/endpoints/marketing_internal.py` — the token-gated API the MEDIA WORKER
  (`marketing/main.py`, a Railway cron service holding NO Supabase key) uses to
  claim the day, checkpoint stages, register artefacts and hand over the day's posts.
* `app/services/marketing/publisher_service.py` — the PUBLISHER loop in the web lifespan,
  which claims `approved` posts one at a time before any external call.
* The review bot (`review_service.py`: verdicts, reject reasons, retract requests), the measure step
  (`metrics_service.py`: `merge_post_metrics`, the ONE writer of `marketing_posts.metrics`) and the
  weekly digest / run-health alert (`digest_service.py`: reads only) — all inside that same loop or
  its webhook.

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

import copy
import hashlib
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.schemas.marketing import (
    ASSET_EXTENSIONS,
    ASSET_KIND_EXTENSIONS,
    ASSET_KINDS,
    FROZEN_POST_FORMATS,
    IMAGE_ROLE_POST,
    MEDIA_REQUIRED_FORMATS,
    NEWS_CLASSES,
    POST_FORMATS,
    POST_FORMATS_BY_PLATFORM,
    POST_IMAGE_EXT,
    POST_IMAGE_MAX_BYTES,
    POST_MEDIA_KINDS,
    POST_PLATFORMS,
    POST_STATUSES,
    RUN_STAGES,
    RUN_STATUSES,
    RUN_WORKER_CAPABILITIES_KEY,
    SERVER_OWNED_RUN_METADATA,
    TEMPLATE_AUTHORSHIP,
    VIDEO_BRAND_TEXT,
    WORKER_CAPABILITIES,
    WORKER_RUN_STATUSES,
    normalize_image_post,
    parse_content_classes,
)
# Company Weekly (drop 2): the class a script's frozen template id decides, the template re-check and
# the on-screen allow-lists of a template's video and post image, the per-post AI flag, the logo path.
# All pure and FMP-free (tests/test_marketing_import_boundary.py PURE_MODULES).
from app.services.marketing import news_templates, post_copy, selection, template_onscreen
from app.services.marketing.logo_check import LogoInfo, logo_path
from app.utils.market_hours import ET
from app.utils.postgrest_paging import PAGE_SIZE as POSTGREST_MAX_ROWS
from app.utils.supabase_async import sb_exec
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

RUNS = "marketing_runs"
ASSETS = "marketing_assets"
POSTS = "marketing_posts"
SCRIPTS = "marketing_scripts"
#: `/go/{campaign}` taps per (campaign, ET day) — written by `smart_link` (migration 173); read here
#: only for the weekly digest.
LINK_HITS = "marketing_link_hits"

# Claim reasons — the worker branches on these strings, so they are part of the wire contract.
CLAIMED = "claimed"
ALREADY_DONE = "already_done"
IN_PROGRESS = "in_progress"
MEDIA_READY = "media_ready"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
NO_RUN = "no_run"

_TERMINAL_RUN_STATUSES = frozenset({"published", "skipped"})

#: Statuses the abandoned-run sweep may close. Never `media_ready` (the publisher owns it; closing
#: it would strand approved posts) and never a terminal or `failed` row.
_SWEEPABLE_RUN_STATUSES = ("in_progress", "planned")
#: Rows one sweep looks at, oldest run_date first. It runs inside the claim request on the single
#: uvicorn worker, so it is bounded; anything left over is closed by the next hourly claim. The
#: liveness check runs AFTER the limit, so an old row something touched recently still takes a
#: slot (and is skipped): the limit bounds rows READ, not rows closed — harmless at about one
#: abandoned run a day. Pinned (count and order) by
#: tests/test_marketing_run_service.py::test_one_sweep_closes_at_most_the_limit_and_the_oldest_first.
_SWEEP_LIMIT = 20

# Columns a post write may set (`transition_post` uses this minus `metadata`, which it only ever
# MERGES; the legacy `mark_post` takes it whole). A whitelist, so an adapter result dict
# can never smuggle a column (or a typo that PostgREST would 400 on) into the ledger.
# `metrics` is deliberately NOT here: its one writer is `merge_post_metrics`, fenced on its own
# `metrics->>rev` and never touching `updated_at` (the publisher's fence).
_POST_WRITABLE = frozenset({
    "external_id", "external_url", "attempts", "last_error", "cost_micros",
    "metadata", "claimed_at", "published_at", "approved_at", "approved_by",
})

#: The `metadata` keys that mark a post as already offered for review (the bundle stamp and the
#: review bot's notified stamps). `review_bundle` clears them from a member whose text changed after it
#: was shown, so the next sweep offers it again.
_REVIEW_OFFER_STAMPS: Tuple[str, ...] = ("review_bundle", "review_notified_at", "review_message_id",
                                         "review_message_ids")

#: Why the owner rejected a post (`metadata.review.reason`, written by `record_reject_reason` from the
#: review bot's reason keyboard). The bot's labels (`review_service.REJECT_REASONS`) are keyed by
#: exactly these codes — pinned equal by tests/test_marketing_review_bot.py.
REJECT_REASON_CODES = ("tone", "accuracy", "compliance", "weak", "other")

#: Why `close_finished_runs` closed a run (`metadata.closed.reason`, from `close_summary`).
CLOSE_REASONS = ("posted", "all_rejected", "expired_unreviewed", "approved_unsent", "failed", "no_posts",
                 "mixed")

#: `link_hits_between` reads at most this many (campaign, day) rows — ~15 campaigns × a month is ~465.
_LINK_HITS_LIMIT = 2000


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


class MarketingScriptNotReady(MarketingRunError):
    """`create_posts` before the run's script was accepted, or for an outlet the accepted
    script does not carry. The caption is SERVER-authored from the accepted script, so there is
    nothing to record until the writer is done (409, never retried as a 5xx)."""


class MarketingRunNotHeld(MarketingRunError):
    """A per-run write on a run the caller does not hold — not `in_progress`, outside the
    today/yesterday ET window, or a claim gone stale. 409 MARKETING_RUN_NOT_HELD: never retried;
    the next claim decides what happens to the day. Most importantly this is what stops a kick
    from starting writer spend for an arbitrary (old, closed) run id."""


class MarketingRequestInvalid(MarketingRunError):
    """The worker asked for something the contract forbids — a (platform, format) the server
    does not record, a media post with no media, a stage moving backwards, a status only the
    claim or the publisher may write — or `create_posts` met a script whose content class has no
    post gate (a template id `selection.content_class_of` does not know, or an output whose own
    class disagrees with it). 422
    MARKETING_REQUEST_INVALID: the same request can never succeed, so it must not be retried as a
    5xx."""


class MarketingJudgeNotEnforced(MarketingRunError):
    """`create_posts` for a class-A script the semantic judge did not check in `enforce` mode
    (`shadow` accepts drafts the judge flagged; `off` never asked it). Such a script may be voiced
    and rendered for inspection, but it never becomes a post — the judge is the gate that makes a
    reviewed, and later an auto-published, post safe (§12.5). 409 MARKETING_JUDGE_NOT_ENFORCED:
    deterministic for the run, so the worker closes the day `skipped` instead of retrying. The
    class-A branch only: a template class (C/F) has its own gate, `MarketingTemplateRefused`."""


class MarketingTemplateRefused(MarketingRunError):
    """`create_posts` for a Company Weekly TEMPLATE script (class C or F, drop 2) whose output no longer
    passes `news_templates.revalidate` against its stored fact sheet (a deploy changed the template
    version, a hand edit), whose class MARKETING_CONTENT_CLASSES no longer lists, or whose series the
    per-series switch no longer leaves on (MARKETING_NEWS_SERIES ∩ the shipped series). 409
    MARKETING_TEMPLATE_REFUSED: deterministic for the run, so the worker closes the day `skipped`
    (skip_reason template_refused) instead of retrying it six times. Raised before any asset read or
    INSERT: nothing is recorded."""


class MarketingAssetMismatch(MarketingRunError):
    """The object in the bucket is not what the worker registered (size or content type differ).
    The object has been DELETED and the row marked `failed`, so the immutable key is free again
    and a re-render re-uploads it; 409 MARKETING_ASSET_MISSING (the registered object is not
    there) — the next tick retries the stage."""


# ── pure helpers (unit-tested, no I/O) ─────────────────────────────────────────


def run_date_et(now: Optional[datetime] = None) -> date:
    """The ET calendar day a run belongs to. Marketing days are wall-clock days in New York,
    matching every other daily boundary in the app (`market_hours.ET`)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def claim_window_ok(run_date: date, today: date) -> bool:
    """A worker may claim only TODAY (ET) or YESTERDAY (the out-of-window resume). A future
    date would claim the real day before it arrives (`run_date` is UNIQUE, so that day would
    later be skipped as already done); an older one is unbounded writer spend on demand. The
    same window bounds which runs a kick may start writer spend for (`held_problem`)."""
    return today - timedelta(days=1) <= run_date <= today


def held_problem(
    run: Dict[str, Any], *, now: datetime, today: date, stale_seconds: int
) -> Optional[str]:
    """Why `run` is NOT held by a live worker claim, or None when it is. Pure.

    Held = `in_progress`, dated inside the claim window, and touched (the later of
    `started_at` / `updated_at`, exactly `decide_claim`'s liveness) less than `stale_seconds`
    ago. The shipped worker always kicks inside its own tick, which is capped
    (WORKER_DEADLINE_SECONDS) below MARKETING_RUN_STALE_SECONDS, and its out-of-window resume
    runs for YESTERDAY before the run hour — so every legitimate kick passes. A run that
    `decide_claim` would re-claim as stale is exactly one that is not held."""
    status = run.get("status")
    if status != "in_progress":
        return f"status is {status!r}, not 'in_progress'"
    try:
        run_date = date.fromisoformat(str(run.get("run_date"))[:10])
    except ValueError:
        return f"run_date {run.get('run_date')!r} is unreadable"
    if not claim_window_ok(run_date, today):
        return f"run_date {run_date} is outside the claim window ({today - timedelta(days=1)}..{today} ET)"
    touched = max(
        (t for t in (_parse_ts(run.get("started_at")), _parse_ts(run.get("updated_at"))) if t is not None),
        default=None,
    )
    if touched is None:
        return "the run carries no claim time"
    if now - touched >= timedelta(seconds=max(stale_seconds, 0)):
        return f"the claim went stale (last touched {touched.isoformat()})"
    return None


@dataclass(frozen=True)
class CallerClaim:
    """The claim a worker call says it holds: the run's `attempts` at its claim and the nonce it
    minted for that claim (`X-Marketing-Claim: <attempts>.<nonce>`, SYSTEM_DESIGN_GUIDELINES
    §12.2). Every successful claim yields a distinct `attempts` (a re-claim increments it with a
    compare-and-swap; nonce recovery returns the same row), and the nonce is per process — so
    the PAIR identifies the holder even across runs (attempts alone is 1-6 per run) and after a
    manual reset brings an old attempts value back."""
    attempts: int
    nonce: str

    _NONCE_RE = re.compile(r"^[0-9a-f]{16,64}$")

    @classmethod
    def parse(cls, header: Optional[str]) -> "CallerClaim":
        """`<attempts>.<nonce>` → CallerClaim, or ValueError naming what is wrong."""
        if not header:
            raise ValueError("missing claim")
        head, sep, nonce = header.strip().partition(".")
        if not sep or not head.isdigit() or len(head) > 6:
            raise ValueError("malformed claim (expected <attempts>.<nonce>)")
        attempts = int(head)
        nonce = nonce.lower()
        if attempts < 1 or not cls._NONCE_RE.match(nonce):
            raise ValueError("malformed claim (attempts ≥ 1, nonce 16-64 hex)")
        return cls(attempts, nonce)

    def header(self) -> str:
        return f"{self.attempts}.{self.nonce}"


def claim_problem(run: Dict[str, Any], claim: CallerClaim) -> Optional[str]:
    """Why `run` is not held by `claim`, or None. Pure. Compares the CALLER's claim with the
    row's — the old fence compared the row with itself (the `attempts` it had just read), so a
    zombie tick whose run was re-claimed before its read passed it and wrote over the new
    holder (rules marketing.md §2)."""
    meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    if int(run.get("attempts") or 0) != claim.attempts:
        return f"the run is at claim attempt {run.get('attempts')}, the caller holds {claim.attempts}"
    if meta.get("claim_nonce") != claim.nonce:
        return "the run's claim nonce is not the caller's"
    return None


_WORD_EDGE_PUNCT = "\"'“”‘’()[]{}.,;:!?…—–-"


def spoken_words(text: str) -> List[str]:
    """Whitespace words, edge punctuation stripped and case-folded: the unit the narration's
    word timings are compared in (a timing table is display words, one per whitespace word)."""
    out = []
    for tok in (text or "").split():
        w = tok.strip(_WORD_EDGE_PUNCT).casefold()
        if w:
            out.append(w)
    return out


def narration_words(script_output: Dict[str, Any]) -> List[str]:
    """What the voice stage narrates: the hook, then every script line, in order."""
    words = spoken_words(str(script_output.get("hook") or ""))
    for line in script_output.get("video_script") or []:
        words += spoken_words(str(line))
    return words


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
    if ext not in ASSET_KIND_EXTENSIONS.get(kind, ()):
        raise ValueError(f"a {kind!r} asset cannot be a .{ext}")
    if len(sha256) != 64:
        raise ValueError("sha256 must be 64 hex chars")
    return f"{run_date.isoformat()}/{kind}-{sha256[:16].lower()}.{ext}"


def frozen_post_formats(output: Any) -> Optional[Dict[str, str]]:
    """The accepted output's frozen `post_formats` (drop 1: `{platform: "video"|"image"|"text"}`,
    written once by `script_service.freeze_post_formats`), or None when it carries none — a script
    accepted before image posts existed, which validates exactly as before. Pure.

    Raises ValueError when they are present but do not read back — not a dict, a format outside
    FROZEN_POST_FORMATS or outside the platform's POST_FORMATS_BY_PLATFORM, or an "image" format
    without a usable `image_post` and `image_footer` beside it (a hand-edited row). Never guessed:
    the caller refuses the run's posts."""
    if not isinstance(output, dict):
        return None
    raw = output.get("post_formats")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"post_formats is a {type(raw).__name__}, not an object")
    out: Dict[str, str] = {}
    for platform, fmt in raw.items():
        if (not isinstance(platform, str) or not isinstance(fmt, str) or fmt not in FROZEN_POST_FORMATS
                or fmt not in POST_FORMATS_BY_PLATFORM.get(platform, ())):
            raise ValueError(f"post_formats[{str(platform)[:40]!r}] = {str(fmt)[:40]!r} is not a format "
                             "the server records for that platform")
        out[platform] = fmt
    if "image" in out.values():
        footer = output.get("image_footer")
        if normalize_image_post(output.get("image_post")) is None or not isinstance(footer, str) or not footer.strip():
            raise ValueError("post_formats names an image post but the output carries no usable "
                             "image_post and image_footer")
    return out


def _template_script(run_id: str, script: Dict[str, Any], output: Dict[str, Any], *, what: str) -> bool:
    """Is the accepted script a Company Weekly TEMPLATE (drop 2)? Decided by the SCRIPT's frozen
    `template_id` (`selection.content_class_of`: a news series → C/F), never by the run's mirror or the
    worker. A template script must carry template authorship, and a lesson script must not — either
    disagreement is a hand-edited row whose on-screen check has no honest allow-list (422)."""
    klass = selection.content_class_of(script.get("template_id"))
    authorship = output.get("authorship")
    if klass in NEWS_CLASSES:
        if authorship != TEMPLATE_AUTHORSHIP:
            raise MarketingRequestInvalid(
                f"run {run_id}: the accepted script is class {klass} (template {script.get('template_id')!r}) "
                f"but its output's authorship is {str(authorship)[:20]!r}; no {what} is checked against it")
        return True
    if authorship == TEMPLATE_AUTHORSHIP:
        raise MarketingRequestInvalid(
            f"run {run_id}: the accepted output claims template authorship under the non-template id "
            f"{str(script.get('template_id'))[:40]!r}; no {what} is checked against it")
    return False


def _series_trail(script: Dict[str, Any]) -> List[Dict[str, str]]:
    """The fallback trail the selection recorded in the script's fact sheet (`fact_sheet.selection.trail`,
    drop 2), as JSON-safe `{series, outcome, reason?}` string entries — at most 8, anything malformed
    skipped. [] for a script with no selection block (a lesson-only plan, or written before drop 2)."""
    sheet = script.get("fact_sheet") if isinstance(script.get("fact_sheet"), dict) else {}
    block = sheet.get("selection") if isinstance(sheet.get("selection"), dict) else {}
    out: List[Dict[str, str]] = []
    for entry in block.get("trail") if isinstance(block.get("trail"), list) else []:
        if not isinstance(entry, dict):
            continue
        kept = {k: str(entry[k])[:60] for k in ("series", "outcome", "reason")
                if isinstance(entry.get(k), str) and entry[k]}
        if "series" in kept and "outcome" in kept:
            out.append(kept)
        if len(out) >= SERIES_TRAIL_MAX:
            break
    return out


#: How many fallback steps a run's / a post's `series_trail` keeps (the longest plan has 6 steps).
SERIES_TRAIL_MAX = 8


def run_worker_capabilities(run: Any) -> frozenset:
    """What the worker that HOLDS `run` declared it can render — `metadata.worker_capabilities`, written
    only by `claim_run` (server-owned) — limited to WORKER_CAPABILITIES. Empty for a run claimed by a
    worker that declared nothing (an older image), and for anything that does not read back. Pure."""
    meta = run.get("metadata") if isinstance(run, dict) and isinstance(run.get("metadata"), dict) else {}
    raw = meta.get(RUN_WORKER_CAPABILITIES_KEY)
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(c for c in raw if isinstance(c, str) and c in WORKER_CAPABILITIES)


def idempotency_key_for(run_date: date, platform: str, fmt: str) -> str:
    if platform not in POST_PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    if fmt not in POST_FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    return f"{run_date.isoformat()}:{platform}:{fmt}"


#: Post statuses that are not yet final — a run is never closed while one remains.
_OPEN_POST_STATUSES = frozenset({"pending_review", "approved", "queued"})


def post_run_date(post: Dict[str, Any]) -> Optional[date]:
    """The run day a post belongs to, from its idempotency key (`<run_date>:<platform>:<format>`,
    written by `create_posts`). None when the key is malformed — such a post is never fresh."""
    key = str(post.get("idempotency_key") or "")
    try:
        return date.fromisoformat(key[:10])
    except ValueError:
        return None


def is_fresh(post: Dict[str, Any], today: date) -> bool:
    """May this post still go out? Only on its run day or the next (ET) — the owner's expiry rule
    (2026-09-30), the same window a worker may claim (`claim_window_ok`)."""
    run_day = post_run_date(post)
    return run_day is not None and claim_window_ok(run_day, today)


def month_start_utc(now: Optional[datetime] = None) -> datetime:
    """00:00:00 UTC on the first day of `now`'s UTC month — the X spend cap's window."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


#: The op a journal entry is counted under when it carries none (or junk): it still counts, so the
#: per-op breakdown always sums to `charges_since`.
UNKNOWN_CHARGE_OP = "unknown"
_CHARGE_OP_MAX_CHARS = 64


#: One journal entry beyond ±$1,000 is a hand edit, not a charge (X's dearest op is $0.20). Only the
#: digest's weekly cost line applies this bound — it reads such an entry as UNREADABLE; the X cap
#: never does (it keeps counting every readable amount: fail-closed).
CHARGE_MICROS_BOUND = 10 ** 9


def _journal(post: Dict[str, Any]) -> List[Any]:
    """A post's `metadata.charges` journal, or [] when there is none (or it is not a list)."""
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    journal = meta.get("charges")
    return journal if isinstance(journal, list) else []


def _charge_entry(entry: Any) -> Optional[Tuple[str, Optional[int], Optional[datetime]]]:
    """(op, micros, at) of ONE journal entry — the reading rule shared by the X cap
    (`_dated_charges`) and the weekly cost line (`charges_between`). None for a non-dict entry.
    `micros` is None when the amount is missing or cannot be read (a bool, not a number, one too
    large for an int); `at` is None when the time cannot be read; a missing or junk op is
    `UNKNOWN_CHARGE_OP`."""
    if not isinstance(entry, dict):
        return None
    raw = entry.get("micros")
    micros: Optional[int] = None
    if raw is not None and not isinstance(raw, bool):
        try:
            micros = int(raw)
        except (TypeError, ValueError, OverflowError):
            micros = None
    op = entry.get("op")
    op = op[:_CHARGE_OP_MAX_CHARS] if isinstance(op, str) and op.strip() else UNKNOWN_CHARGE_OP
    return op, micros, _parse_ts(entry.get("at"))


def _dated_charges(post: Dict[str, Any], since: datetime) -> Iterator[Tuple[str, int]]:
    """(op, micros) of every `metadata.charges` entry of `post` dated at or after `since` — the ONE
    reading rule behind `charges_since` and `charges_by_op_since`. An entry whose time cannot be read
    is COUNTED (an over-count can only pause X early); one whose amount is missing or cannot be read
    (a bool, not a number, one too large for an int) is skipped — never an `op: 0` line in the
    digest; a missing or junk op is `UNKNOWN_CHARGE_OP`. Entries are parsed by `_charge_entry`, the
    rule the weekly cost line shares."""
    journal = _journal(post)
    if not journal:
        return
    if since.tzinfo is None:   # naive = UTC, as everywhere here (an aware/naive compare would raise)
        since = since.replace(tzinfo=timezone.utc)
    for entry in journal:
        parsed = _charge_entry(entry)
        if parsed is None or parsed[1] is None:
            continue
        op, micros, at = parsed
        if at is None or at >= since:
            yield op, micros


@dataclass(frozen=True)
class ChargeWindow:
    """What one post's journal holds for a half-open window: `micros` summed over the readable
    entries dated inside it, `undated` entries whose time cannot be read (never summed into ANY
    window — a weekly figure that counted them would count them every week), and `unreadable`
    entries dated inside it whose amount cannot be read or is beyond `CHARGE_MICROS_BOUND`."""

    micros: int = 0
    undated: int = 0
    unreadable: int = 0


def charges_between(post: Dict[str, Any], start: datetime, end: datetime) -> ChargeWindow:
    """A post's journal over [start, end) — the weekly cost line's reader. Pure and never raises;
    naive instants are UTC. Unlike the X cap's `_dated_charges` it has an upper bound, it NEVER
    counts an undated entry (it reports how many there were), and an in-window amount it cannot read
    makes the window unreadable instead of being skipped — the cost line says "unreadable", never a
    wrong number."""
    journal = _journal(post)
    if not journal:
        return ChargeWindow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    micros_sum = undated = unreadable = 0
    for entry in journal:
        parsed = _charge_entry(entry)
        if parsed is None:
            continue
        _op, micros, at = parsed
        if at is None:
            undated += 1
        elif start <= at < end:
            if micros is None or abs(micros) > CHARGE_MICROS_BOUND:
                unreadable += 1
            else:
                micros_sum += micros
    return ChargeWindow(micros=micros_sum, undated=undated, unreadable=unreadable)


def charges_since(post: Dict[str, Any], since: datetime) -> int:
    """Sum of a post's `metadata.charges` entries dated at or after `since` (micro-dollars). An
    entry whose time cannot be read is COUNTED — an over-count can only pause X early."""
    return sum(micros for _op, micros in _dated_charges(post, since))


def charges_by_op_since(post: Dict[str, Any], since: datetime) -> Dict[str, int]:
    """`charges_since` broken down by journal op (`x_create`, `x_metrics_read`, …). The same reading
    rule, so the values always sum to `charges_since(post, since)`."""
    out: Dict[str, int] = {}
    for op, micros in _dated_charges(post, since):
        out[op] = out.get(op, 0) + micros
    return out


def metrics_rev(metrics: Any) -> Tuple[Optional[str], int]:
    """(fence, next rev) for a write of `marketing_posts.metrics` whose stored value is `metrics`.

    `fence` is `metrics->>rev` exactly as PostgREST's `->>` renders the stored JSON value as text (a
    string as itself, anything else as its JSON), or None for a missing / JSON-null rev (`IS NULL`).
    The next rev is the stored one + 1 when it is a non-negative int, else 1: a hand-edited rev (a
    string, a bool, a float, a negative) is still fenced on exactly what is stored — or the write could
    never land — and the counter restarts. Pure."""
    if not isinstance(metrics, dict) or metrics.get("rev") is None:
        return None, 1
    rev = metrics["rev"]
    fence = rev if isinstance(rev, str) else json.dumps(rev)
    if type(rev) is int and rev >= 0:
        return fence, rev + 1
    return fence, 1


def expired_unreviewed(post: Dict[str, Any]) -> bool:
    """Did `post` expire WITHOUT a review decision? `metadata.expired_from` (the status it expired
    from, written by `expire_stale_posts` since 2026-10-01) decides; an older row without it is
    unreviewed when it carries no `metadata.review` decision. A post the publisher expired after an
    unknown outcome (queued → skipped) was approved, so it is not unreviewed."""
    if post.get("status") != "skipped":
        return False
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    if meta.get("skip_reason") != "expired":
        return False
    was = meta.get("expired_from")
    if was is not None:
        return was == "pending_review"
    review = meta.get("review")
    return not (isinstance(review, dict) and review.get("decision"))


#: The statuses an APPROVED post expires from without being sent: `approved` (never claimed —
#: the X cap, a dry run, an unwired platform; `expire_stale_posts`) and `queued` (an unknown
#: outcome the platform turned out not to hold, past its day; the publisher's reconcile).
_APPROVED_UNSENT_FROM = ("approved", "queued")


def expired_approved_unsent(post: Dict[str, Any]) -> bool:
    """Did `post` expire after the owner APPROVED it, without ever reaching a platform?
    `metadata.expired_from` in (`approved`, `queued`) decides; an older row without it counts when
    it carries a review decision `approved` (`expired_unreviewed`'s legacy rule, the other way
    round). A junk `expired_from` is neither this nor unreviewed. Pure."""
    if post.get("status") != "skipped":
        return False
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    if meta.get("skip_reason") != "expired":
        return False
    was = meta.get("expired_from")
    if was is not None:
        return was in _APPROVED_UNSENT_FROM
    review = meta.get("review")
    return isinstance(review, dict) and review.get("decision") == "approved"


_SUMMARY_KEY_RE = re.compile(r"[a-z0-9_]{1,40}", re.ASCII)


def _summary_key(value: Any) -> str:
    """A status / skip reason as a digest-safe key: code-written values pass, anything else (a hand
    edit, a newline, a non-string) is `unknown`."""
    return value if isinstance(value, str) and _SUMMARY_KEY_RE.fullmatch(value) else "unknown"


def _offered_for_review(post: Dict[str, Any]) -> bool:
    """Was the owner asked about `post` — sent it with buttons, or recorded a decision on it? A
    read-only preview (an unwired platform) never was."""
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    return bool(meta.get("review_notified_at")) or isinstance(meta.get("review"), dict)


def close_summary(posts: List[Dict[str, Any]], *, now: Optional[str] = None) -> Dict[str, Any]:
    """What `close_finished_runs` records as `metadata.closed` on a run it closes. Pure.

    `{"at", "reason", "posts": {status: n}, "skip_reasons": {reason: n}}`; `reason` is one of
    CLOSE_REASONS:

    * `no_posts` — the run recorded none;
    * `posted` — at least one post reached a platform (published, or published then retracted);
    * otherwise judged over the posts the owner was ASKED about (sent with buttons or decided; all of
      them when none was) — read-only previews of unwired platforms always expire unreviewed, and
      would otherwise turn every day into `mixed`:
      `all_rejected` (every one rejected), `failed` (every one failed), `expired_unreviewed` (every
      one expired without a decision — `expired_unreviewed()`), `approved_unsent` (every one was
      approved and expired unsent: the X cap, a dry run, an unwired platform, or an unknown outcome
      the platform did not hold — `expired_approved_unsent()`), else `mixed`."""
    rows = [p for p in (posts or []) if isinstance(p, dict)]
    statuses = Counter(_summary_key(p.get("status")) for p in rows)
    skip_reasons: Counter = Counter()
    for p in rows:
        if p.get("status") == "skipped":
            meta = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
            skip_reasons[_summary_key(meta.get("skip_reason"))] += 1
    if not rows:
        reason = "no_posts"
    elif any(p.get("status") in ("published", "retracted") for p in rows):
        reason = "posted"
    else:
        basis = [p for p in rows if _offered_for_review(p)] or rows
        if all(p.get("status") == "rejected" for p in basis):
            reason = "all_rejected"
        elif all(p.get("status") == "failed" for p in basis):
            reason = "failed"
        elif all(expired_unreviewed(p) for p in basis):
            reason = "expired_unreviewed"
        elif all(expired_approved_unsent(p) for p in basis):
            reason = "approved_unsent"
        else:
            reason = "mixed"
    return {"at": now or _now_iso(), "reason": reason, "posts": dict(statuses),
            "skip_reasons": dict(skip_reasons)}


def _as_date(value: Any, name: str) -> date:
    """A calendar date argument (ET days, as the ledger stores them): a `date` or an ISO string. A
    datetime is refused — which day it falls on depends on a time zone the caller did not say."""
    if isinstance(value, datetime):
        raise ValueError(f"{name} must be a calendar date, not a datetime ({value!r})")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{name} {value!r} is not an ISO date") from None
    raise ValueError(f"{name} must be a date, not {type(value).__name__}")


def _as_instant(value: Any, name: str) -> datetime:
    """An instant argument: an aware datetime (a naive one is UTC, as everywhere in this module) or
    an ISO timestamp string. A bare date is refused — midnight WHERE?"""
    if isinstance(value, datetime) or (isinstance(value, str) and "T" in value):
        parsed = _parse_ts(value)
        if parsed is not None:
            return parsed
    raise ValueError(f"{name} must be a datetime or an ISO timestamp, not {value!r}")


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


def _ts_filter(value: Any) -> str:
    """An observed timestamp re-rendered for an equality FILTER: the same instant (Postgres
    compares timestamptz by value, to the microsecond) in the UTC `Z` form — never `+00:00`,
    which PostgREST decodes to a space and answers 503 (the notifications-cursor incident)."""
    parsed = _parse_ts(value)
    if parsed is None:
        return str(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _touched(row: Dict[str, Any]) -> Optional[datetime]:
    """A run's liveness: the LATER of `started_at` / `updated_at` (what `decide_claim` and
    `held_problem` read)."""
    return max(
        (t for t in (_parse_ts(row.get("started_at")), _parse_ts(row.get("updated_at"))) if t is not None),
        default=None,
    )


def _is_worker_replay(row: Dict[str, Any], *, status: Optional[str], stage: Optional[str]) -> bool:
    """Does `row` already hold everything a worker PATCH that could not be applied asks for?
    Only a TERMINAL request can be a replay (`status` is always a WORKER_RUN_STATUSES value, never
    `in_progress`): a bare checkpoint on a run that left `in_progress` is a lost claim, and the
    worker must stop, not carry on against a closed day."""
    if status is None or row.get("status") != status:
        return False
    return stage is None or row.get("stage") == stage


def _replayed(run_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a replayed worker PATCH with the row as it is. Nothing is written — which is also
    why this says "already", not "recorded": a stale tick can confirm a status it did not write."""
    logger.info(
        "marketing update_run: run %s is already %r (stage=%s) — the worker's PATCH is a replay (a "
        "retried write whose first response was lost, or a stale tick's); nothing written",
        run_id, row.get("status"), row.get("stage"),
    )
    return row


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


# ── review bundles (drop 1, 2026-10-09; MARKETING_REVIEW_BUNDLES) ──────────────────────────────
# The review bot (`review_service`) may offer a run's posts as two BUNDLES — its video posts, and its
# image/text posts — each decided with ONE tap. Each member carries `metadata.review_bundle` =
# {id, kind, members, caption_sha}, written by the bot's fenced stamp BEFORE the decision message is
# sent; `review_bundle` below decides exactly the members that still carry that id.

#: `metadata.review_bundle.kind`: "video" = the run's video posts, "post" = its image / text posts.
REVIEW_BUNDLE_KINDS = ("video", "post")
#: Posts one bundle read returns. A bundle holds at most one post per platform (8 today).
_BUNDLE_READ_LIMIT = 50


def _canonical_uuid(value: Any) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def review_caption_sha(post: Dict[str, Any]) -> str:
    """sha256 (hex) of the title and caption the owner is shown for `post` — stamped into
    `metadata.review_bundle.caption_sha` when its bundle is sent, and compared again by
    `review_bundle`: a decision never applies to text the owner did not see. Pure."""
    title = post.get("title") if isinstance(post.get("title"), str) else ""
    caption = post.get("caption") if isinstance(post.get("caption"), str) else ""
    return hashlib.sha256(json.dumps([title, caption], ensure_ascii=False).encode("utf-8")).hexdigest()


def review_bundle_of(post: Any) -> Optional[Dict[str, Any]]:
    """`metadata.review_bundle` of `post` when it reads back — a canonical uuid `id`, a kind from
    REVIEW_BUNDLE_KINDS, `members` a list of canonical uuids, `caption_sha` a string — else None (a
    hand-edited or half-written stamp is no bundle at all). Pure."""
    meta = post.get("metadata") if isinstance(post, dict) and isinstance(post.get("metadata"), dict) else {}
    raw = meta.get("review_bundle")
    if not isinstance(raw, dict):
        return None
    bid, kind, members, sha = raw.get("id"), raw.get("kind"), raw.get("members"), raw.get("caption_sha")
    if not isinstance(bid, str) or _canonical_uuid(bid) != bid or kind not in REVIEW_BUNDLE_KINDS:
        return None
    if not isinstance(members, list) or not isinstance(sha, str):
        return None
    ids = [m for m in members if isinstance(m, str) and _canonical_uuid(m) == m]
    if len(ids) != len(members):
        return None
    return {"id": bid, "kind": kind, "members": ids, "caption_sha": sha}


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
        capabilities: Tuple[str, ...] = (),
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """INSERT the day's row, or decide against the one that exists. Returns (row, reason).

        `capabilities` (drop 1) is what the CLAIMING worker declared it can render
        (WORKER_CAPABILITIES). A claim that takes the run records it as the server-owned
        `metadata.worker_capabilities`, REPLACING the previous holder's (an older worker that
        declares nothing removes the key), so `run_worker_capabilities` always answers for the
        worker that holds the run. A worker that declares nothing leaves the row exactly as before.

        The INSERT is the claim: a UNIQUE violation means someone got there first and we
        fall through to the decision matrix on THEIR row. A re-claim of a stale/failed row is
        a compare-and-swap conditioned on the `attempts` value we observed — a column the
        re-claim itself increments — so two workers racing on the same abandoned row cannot
        both win (conditioning on `status` alone was a no-op: the observed and the new value
        were both `in_progress`).

        `resume_only` never inserts: it is the out-of-window tick finishing a run that was
        killed after the last in-window tick, and (None, NO_RUN) means there is nothing.

        Every claim — of any date, resume or not — first sweeps runs abandoned OUTSIDE the claim
        window (`_sweep_abandoned`): nothing else could ever close them.
        """
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        claim_meta: Dict[str, Any] = {"claim_nonce": claim_nonce} if claim_nonce else {}
        declared = sorted({c for c in capabilities if c in WORKER_CAPABILITIES})
        if declared:
            claim_meta[RUN_WORKER_CAPABILITIES_KEY] = declared
        await self._sweep_abandoned(now=now)

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
                "metadata": claim_meta,
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
        if reason == ATTEMPTS_EXHAUSTED and existing.get("status") != "failed":
            # A stale in_progress (or planned) row at the cap: its last worker was killed, so no
            # `failed` was ever written and the day would read "in_progress" forever.
            existing = await self._close_exhausted(existing, now=now)
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
            # The previous holder's capabilities never outlive its claim: this worker's replace them.
            "metadata": {**{k: v for k, v in (meta if isinstance(meta, dict) else {}).items()
                            if k != RUN_WORKER_CAPABILITIES_KEY}, **claim_meta},
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

    async def _close_failed_cas(
        self, existing: Dict[str, Any], *, now: datetime, last_error: str, op: str
    ) -> Optional[Dict[str, Any]]:
        """Close an ABANDONED run `failed`: one compare-and-swap on exactly the row that was
        judged abandoned — the observed (status, attempts), the same guard the re-claim uses and
        never `status` alone, plus the observed `updated_at` (the liveness the judgement read: a
        worker checkpoint landing in between proves it alive, and makes this miss). `attempts`
        is left as it is, so the cap still holds. Returns the closed row, or None when another
        writer moved the row first; a ledger error raises MarketingRunError."""
        run_id = existing.get("id")
        stamp = now.isoformat()
        query = (
            self.sb.table(RUNS)
            .update({"status": "failed", "finished_at": stamp, "updated_at": stamp,
                     "last_error": last_error[:2000]})
            .eq("id", run_id)
            .eq("status", existing.get("status"))
            .eq("attempts", int(existing.get("attempts") or 0))
        )
        observed_touch = existing.get("updated_at")
        query = (query.is_("updated_at", "null") if observed_touch is None
                 else query.eq("updated_at", _ts_filter(observed_touch)))
        return _one(await _exec(query, op=op, run_id=run_id))

    async def _sweep_abandoned(self, *, now: datetime) -> int:
        """Close runs abandoned OUTSIDE the claim window. `_close_exhausted` runs only from a
        claim of the run's own date, and the window stops admitting that date after yesterday
        ET — so a run killed on its last claimable tick (the D+1 resume, an interrupted manual
        FORCE run, one that crossed midnight) used to read `in_progress` forever with nothing
        ever logging it. Every claim (the hourly cron claims one date or another on every tick)
        sweeps them: an `in_progress`/`planned` run dated STRICTLY before yesterday ET — never
        yesterday, which is still resumable until the run hour — whose liveness is older than
        MARKETING_RUN_STALE_SECONDS. Each close is the same fenced CAS as the attempts-cap close;
        only the transition logs WARNING. Bounded (`_SWEEP_LIMIT`, on the (status, run_date)
        index) and best effort: a failure here never fails the claim. Returns how many closed."""
        today = run_date_et(now)
        cutoff = today - timedelta(days=1)
        stale = timedelta(seconds=max(int(settings.MARKETING_RUN_STALE_SECONDS), 0))
        try:
            res = await _exec(
                self.sb.table(RUNS).select("*")
                .in_("status", list(_SWEEPABLE_RUN_STATUSES))
                .lt("run_date", cutoff.isoformat())
                .order("run_date")
                .limit(_SWEEP_LIMIT),
                op="sweep_abandoned.select", before=cutoff,
            )
        except Exception as e:
            (logger.warning if isinstance(e, MarketingRunError) else logger.error)(
                "marketing run: abandoned-run sweep could not read (%s: %s) — the next claim retries",
                type(e).__name__, e, exc_info=not isinstance(e, MarketingRunError),
            )
            return 0
        closed = 0
        # The window and the status are decided by the query above ONLY (one guard, not two
        # that would each hide the other's regression); liveness is decided here.
        for row in list(getattr(res, "data", None) or []):
            run_id, run_date = row.get("id"), row.get("run_date")
            touched = _touched(row)
            if touched is not None and now - touched < stale:
                continue  # something touched it recently: not provably abandoned yet
            try:
                done = await self._close_failed_cas(
                    row, now=now, op="sweep_abandoned.close",
                    last_error=(f"abandoned outside the claim window: run_date {run_date} (today "
                                f"{today} ET) was last touched "
                                f"{touched.isoformat() if touched else 'never'} at "
                                f"stage={row.get('stage')} status={row.get('status')} "
                                f"attempts={row.get('attempts')}"),
                )
            except Exception as e:
                (logger.warning if isinstance(e, MarketingRunError) else logger.error)(
                    "marketing run: sweep could not close run_id=%s run_date=%s (%s: %s) — the next "
                    "claim retries", run_id, run_date, type(e).__name__, e,
                    exc_info=not isinstance(e, MarketingRunError),
                )
                continue
            if done is None:
                logger.info("marketing run: sweep of run_id=%s run_date=%s lost to a concurrent write "
                            "— left as it is now", run_id, run_date)
                continue
            closed += 1
            logger.warning(
                "marketing run ABANDONED outside the claim window run_date=%s run_id=%s attempts=%s "
                "stage=%s was=%s last_touched=%s — closed failed; the day needs a human",
                run_date, run_id, row.get("attempts"), row.get("stage"), row.get("status"),
                touched.isoformat() if touched else None,
            )
        return closed

    async def _close_exhausted(self, existing: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
        """Move an abandoned run at the attempts cap to `failed`, once (`_close_failed_cas`).
        Only the call that made the transition logs WARNING; every later tick sees `failed` and
        stays at INFO. Best effort: a ledger blip here must not turn the claim answer into a 503."""
        run_id = existing.get("id")
        observed_status = existing.get("status")
        observed_attempts = int(existing.get("attempts") or 0)
        try:
            closed = await self._close_failed_cas(
                existing, now=now, op="claim_run.close_exhausted",
                last_error=(f"attempts exhausted ({observed_attempts}): the last attempt was "
                            f"abandoned at stage={existing.get('stage')} status={observed_status}"),
            )
        except MarketingRunError as e:
            logger.warning(
                "marketing run: could not close an exhausted run run_id=%s run_date=%s (%s: %s) — "
                "the next tick retries", run_id, existing.get("run_date"), type(e).__name__, e,
            )
            return existing
        if closed is None:
            # Someone moved it first (a late worker's own `failed`); report what is there now.
            try:
                return await self.get_run(run_id) or existing
            except MarketingRunError as e:
                logger.warning("marketing run: re-read after a lost close failed run_id=%s (%s: %s)",
                               run_id, type(e).__name__, e)
                return existing
        logger.warning(
            "marketing run ABANDONED at the attempts cap run_date=%s run_id=%s attempts=%s "
            "stage=%s was=%s — closed failed; the day needs a human", existing.get("run_date"),
            run_id, observed_attempts, existing.get("stage"), observed_status,
        )
        return closed

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
        worker: bool = False,
        claim: Optional[CallerClaim] = None,
        cas: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Write the fields that were given; merge `timings`/`metadata` into the JSONB
        rather than replacing it, so each stage reports only its own numbers.

        `cas=True` (a SERVER write only — the selection mirror, drop 2) fences the UPDATE on the
        `updated_at` this call read: the metadata merge is read-then-write, and a claim or a worker
        PATCH landing in between would otherwise be overwritten with the stale copy (a reverted
        `claim_nonce` locks the run's new holder out). A miss writes nothing and returns None — the
        caller retries on its next pass; every other path returns the row or raises, as before.

        `worker=True` is the internal PATCH route: the least-trusted process in the engine
        may only write its OWN live run — `in_progress`, fenced in the UPDATE itself, not
        read-then-checked — may set only WORKER_RUN_STATUSES, may only move `stage` forward
        (also fenced on the observed stage), and may not write the server-owned metadata keys
        (SERVER_OWNED_RUN_METADATA: `claim_nonce` is trusted by `decide_claim` ahead of the
        attempts cap; `closed` is `close_finished_runs`' record of why it closed the run).

        The worker RETRIES every call after a transport error or a 502/503/504, so a worker
        write must be idempotent: a PATCH whose effect is already there — the run already holds
        the terminal `status` it asks for (and the `stage`, if it names one) — is a REPLAY of a
        write whose response was lost (or a stale tick's), and answers the row unchanged with
        200 instead of 409. It writes NOTHING: merging its last_error/metadata/timings into a
        closed run would reopen the hole the fence closes. A stage checkpoint is fenced on the
        observed stage OR the one it asks for, so a retry whose read ran before its own first
        attempt committed still lands; never "any stage ahead", which would let a stale tick
        write over a newer holder's progress."""
        if stage is not None and stage not in RUN_STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        if status is not None and status not in RUN_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        if worker:
            if claim is None:
                raise MarketingRequestInvalid(f"run {run_id}: a worker write must name its claim")
            if status is not None and status not in WORKER_RUN_STATUSES:
                raise MarketingRequestInvalid(
                    f"run {run_id}: the worker may set status to {WORKER_RUN_STATUSES}, not {status!r}"
                )
            if metadata:
                reserved = sorted(k for k in metadata if k in SERVER_OWNED_RUN_METADATA)
                if reserved:
                    logger.warning("marketing update_run: ignoring server-owned metadata key(s) %s "
                                   "from the worker run_id=%s", reserved, run_id)
                    metadata = {k: v for k, v in metadata.items() if k not in SERVER_OWNED_RUN_METADATA}

        current = await self.get_run(run_id)
        if current is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        observed_stage = current.get("stage")
        if worker:
            # The CALLER's claim first, whatever the status: a zombie tick is refused even for
            # a replay (it did not write the terminal state it would "confirm").
            problem = claim_problem(current, claim)
            if problem is not None:
                raise MarketingRunNotHeld(f"run {run_id} is not held by this caller: {problem}")
            if current.get("status") != "in_progress":
                if _is_worker_replay(current, status=status, stage=stage):
                    return _replayed(run_id, current)
                raise MarketingRunNotHeld(
                    f"run {run_id} is {current.get('status')!r}; the worker writes only an in_progress run"
                )
            if stage is not None:
                was = RUN_STAGES.index(observed_stage) if observed_stage in RUN_STAGES else -1
                if RUN_STAGES.index(stage) < was:
                    raise MarketingRequestInvalid(
                        f"run {run_id}: stage may only move forward ({observed_stage!r} → {stage!r})"
                    )

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

        query = self.sb.table(RUNS).update(patch).eq("id", run_id)
        if worker:
            # The fence lives in the UPDATE: a re-check after the read would let a claim or a
            # close that lands in between be overwritten. It is the CALLER's claim — attempts
            # and nonce — so a re-claim between the read and this write (it keeps `in_progress`,
            # and may already stand at the stage we ask for) matches nothing.
            query = query.eq("status", "in_progress")
            query = query.eq("attempts", claim.attempts).eq("metadata->>claim_nonce", claim.nonce)
            if stage is not None and observed_stage is not None:
                query = (query.eq("stage", stage) if observed_stage == stage
                         else query.in_("stage", [observed_stage, stage]))
        elif cas:
            observed_touch = current.get("updated_at")
            query = (query.is_("updated_at", "null") if observed_touch is None
                     else query.eq("updated_at", _ts_filter(observed_touch)))
        updated = _one(await _exec(query, op="update_run", run_id=run_id))
        if updated is None and cas and not worker:
            logger.info("marketing update_run: run %s changed between the read and the fenced write "
                        "(updated_at %s) — nothing written; the caller retries", run_id,
                        current.get("updated_at"))
            return None
        if updated is None:
            fresh = await self.get_run(run_id) if worker else None
            if fresh is not None:
                if claim_problem(fresh, claim) is None and _is_worker_replay(fresh, status=status, stage=stage):
                    return _replayed(run_id, fresh)
                raise MarketingRunNotHeld(f"run {run_id} changed under the worker's write; nothing written")
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
        claim: CallerClaim,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Insert the asset row and mint a signed upload URL for it.

        Returns (row, upload) where `upload` is None if the row already exists and is
        `ready` — a resumed run re-registering identical bytes must NOT re-upload (the
        object is immutable and Spotify/Meta cache it by URL).
        """
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        if run.get("status") != "in_progress":
            # Media is rendered inside the worker's live tick; a closed or never-claimed run
            # gets no new object in the PUBLIC bucket.
            raise MarketingRunNotHeld(f"run {run_id} is {run.get('status')!r}; assets register only on an in_progress run")
        # Check-then-insert (an INSERT cannot be fenced on another row): a zombie that loses the
        # claim between this read and its INSERT leaves an orphan pending_upload row, which is
        # harmless — completing it is fenced, and only `ready` rows are ever used.
        problem = claim_problem(run, claim)
        if problem is not None:
            raise MarketingRunNotHeld(f"run {run_id} is not held by this caller: {problem}")
        run_date = date.fromisoformat(str(run["run_date"]))
        if kind == "audio" and isinstance(metadata, dict) and metadata.get("words"):
            await self._check_timed_words(run_id, metadata["words"])
        if kind == "video":
            await self._check_onscreen_text(run, metadata if isinstance(metadata, dict) else {})
        if kind == "card" and isinstance(metadata, dict) and (
                "image_role" in metadata or "onscreen_text" in metadata):
            await self._check_post_image_text(run, metadata, ext=ext, size_bytes=size_bytes)
        try:
            path = storage_path_for(run_date, kind, sha256, ext)
        except ValueError as e:
            # The request schema checks the same things first; a ValueError here must still be a
            # 422, not the classifier's generic 502 (which the worker would retry).
            raise MarketingRequestInvalid(f"register_asset run {run_id}: {e}") from e
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
        # bucket BEFORE minting a URL and finish the row here if the bytes are already there —
        # through the SAME verification as `complete_asset` (size + content type), or this
        # branch would be a second path to `ready` that skips it.
        try:
            stat = await self._object_stat(path)
        except MarketingRunError as e:
            # Not fatal: fall through to minting a URL; a real outage surfaces on the PUT.
            logger.warning(
                "register_asset: existence pre-check failed for %s (%s) — minting anyway", path, e,
            )
            stat = None
        if stat is not None:
            await self._verify_object(asset, stat)
            ready = _one(
                await _exec(
                    self.sb.table(ASSETS)
                    .update({"status": "ready", "updated_at": _now_iso()})
                    .eq("id", asset["id"]),
                    op="register_asset.ready", asset_id=asset["id"],
                )
            ) or {**asset, "status": "ready"}
            logger.info(
                "marketing asset object already in bucket (verified), marked READY without "
                "re-upload run_id=%s kind=%s path=%s", run_id, kind, path,
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

    async def _check_timed_words(self, run_id: str, words: List[Dict[str, Any]]) -> None:
        """The first check of WHAT THE VIDEO SAYS: an audio asset's timing table (one entry per
        spoken display word) must be exactly the accepted script's hook + lines. The worker
        cannot choose the words it narrates any more than the captions it posts. The request
        schema already validated the table's shape (`validate_audio_words`)."""
        script = await self.get_script(run_id)
        output = (script or {}).get("output")
        if not script or script.get("status") != "accepted" or not isinstance(output, dict):
            raise MarketingScriptNotReady(f"run {run_id}: no accepted script to check the narration against")
        expected = narration_words(output)
        got = [w for entry in words for w in spoken_words(str(entry.get("w", "")))]
        if got != expected:
            at = next((i for i, (a, b) in enumerate(zip(got, expected)) if a != b), min(len(got), len(expected)))
            raise MarketingRequestInvalid(
                f"run {run_id}: the narration's timed words are not the accepted script "
                f"({len(got)} vs {len(expected)} words; first difference at word {at})")

    async def _check_onscreen_text(self, run: Dict[str, Any], metadata: Dict[str, Any]) -> None:
        """The check of WHAT A VIDEO DRAWS (§12.8), mirroring `_check_timed_words` for what it
        says. The worker declares every string it drew (`metadata.onscreen_text`); each must be one
        of the accepted script's card titles or bodies, its disclaimer card, or the code-owned end
        card (`VIDEO_BRAND_TEXT`) — and the disclaimer card MUST be among them. The burned
        captions are the narration's timed words, so the video must name a ready, checked audio
        asset of the run (`metadata.voice_asset_id`). The server still cannot read pixels; this bounds what a
        well-behaved worker can claim, and it is what a later media auto-publish rests on."""
        run_id = run["id"]
        script = await self.get_script(run_id)
        output = (script or {}).get("output")
        if not script or script.get("status") != "accepted" or not isinstance(output, dict):
            raise MarketingScriptNotReady(f"run {run_id}: no accepted script to check the video against")
        drawn = metadata.get("onscreen_text")
        if not isinstance(drawn, list) or not drawn:   # the request schema requires it; defence in depth
            raise MarketingRequestInvalid(f"run {run_id}: a video must declare metadata.onscreen_text")
        template = _template_script(run_id, script, output, what="video")
        allowed = set(VIDEO_BRAND_TEXT)
        for card in output.get("cards") or []:
            if isinstance(card, dict):
                allowed.update(str(card.get(k)) for k in ("title", "body") if card.get(k))
        disclaimer = str(output.get("disclaimer_card") or "")
        if disclaimer:
            allowed.add(disclaimer)
        opening: Dict[str, Any] = {}
        if template:
            # Drop 2 (contract D12): a template video also draws its OPENING card — the kicker, each
            # referenced logo's company name (its wordmark when the logo is missing), the chip, the
            # figure and the headline. Never a URL or a hash: they are not drawable. A card that does
            # not validate against the run's own logos allows nothing — fail closed, 422.
            opening = output.get("opening_card") if isinstance(output.get("opening_card"), dict) else {}
            problem = template_onscreen.validate_opening_card(
                output.get("opening_card"), template_onscreen.logo_keys(output.get("logos")))
            if problem is not None:
                logger.error("marketing video REFUSED run_id=%s: the accepted template's opening_card does not "
                             "validate (%s)", run_id, str(problem)[:200])
                raise MarketingRequestInvalid(
                    f"run {run_id}: the accepted template's opening_card does not validate "
                    f"({str(problem)[:120]}); no template video can be checked against it")
            allowed.update(template_onscreen.opening_strings(opening, output.get("logos")))
        extra = [t for t in drawn if t not in allowed]
        if extra:
            raise MarketingRequestInvalid(
                f"run {run_id}: the video declares {len(extra)} on-screen string(s) that are not the "
                f"accepted script's cards, its disclaimer card{', its opening card' if template else ''} or "
                f"the end card (first: {str(extra[0])[:80]!r})")
        if not disclaimer or disclaimer not in drawn:
            raise MarketingRequestInvalid(f"run {run_id}: the video does not draw the disclaimer card")
        if template:
            # The opening card MUST be drawn — its kicker and headline, the two strings it always draws
            # (a verified logo draws no wordmark name, so the whole `opening_strings` set is not
            # required). A worker that renders the template as a LESSON (an older image that ignores
            # `video_layout` / `opening_card`) opens on cards[0] — on a Form 4 day, the person's name,
            # which then becomes the platforms' cover frame. Refused: the day fails closed.
            missing = [opening[k] for k in ("kicker", "headline") if opening[k] not in drawn]
            if missing:
                logger.error("marketing video REFUSED run_id=%s: a template video that does not draw its opening "
                             "card (%d of its kicker/headline missing) — a worker that drew the template as a "
                             "lesson?", run_id, len(missing))
                raise MarketingRequestInvalid(
                    f"run {run_id}: the template video does not draw its opening card "
                    f"(first missing: {str(missing[0])[:80]!r})")
        # The captions it burns: a READY audio asset of THIS run carrying a timing table — which
        # `_check_timed_words` compared with the script when that asset registered. Not the run's
        # `metadata.voice_asset_id`: the worker writes that itself.
        voice_id = metadata.get("voice_asset_id")
        voice = next((a for a in await self.list_assets(run_id) if voice_id and a.get("id") == voice_id), None)
        voice_md = (voice or {}).get("metadata") if isinstance((voice or {}).get("metadata"), dict) else {}
        if voice is None or voice.get("kind") != "audio" or voice.get("status") != "ready" or not voice_md.get("words"):
            raise MarketingRequestInvalid(
                f"run {run_id}: the video's captions must come from a ready, checked narration of this "
                f"run (voice_asset_id={voice_id!r})")

    async def _check_post_image_text(self, run: Dict[str, Any], metadata: Dict[str, Any], *,
                                     ext: str, size_bytes: int) -> None:
        """The check of WHAT THE POST IMAGE DRAWS (drop 1, contract C6) — `_check_onscreen_text` for
        the one card every image post of the run carries. The worker declares every string it drew
        (`metadata.onscreen_text`); each must be the accepted output's `image_post` title or one of
        its paragraphs, or its code-owned `image_footer` — and the footer MUST be among them (it is
        the image's disclaimer). A card declaring text is only ever a post image. As for video, the
        server cannot read pixels: this bounds what a well-behaved worker can claim. The request
        schema checks the same shape first; this is the service's own fence (422, never retried)."""
        run_id = run["id"]
        if metadata.get("image_role") != IMAGE_ROLE_POST:
            raise MarketingRequestInvalid(
                f"run {run_id}: a card declares on-screen text only as the post image "
                f"(image_role {IMAGE_ROLE_POST!r}, got {str(metadata.get('image_role'))[:40]!r})")
        if str(ext).lower().lstrip(".") != POST_IMAGE_EXT or int(size_bytes) > POST_IMAGE_MAX_BYTES:
            raise MarketingRequestInvalid(
                f"run {run_id}: a post image must be a .{POST_IMAGE_EXT} of at most "
                f"{POST_IMAGE_MAX_BYTES} bytes (got .{ext}, {size_bytes} bytes)")
        script = await self.get_script(run_id)
        output = (script or {}).get("output")
        if not script or script.get("status") != "accepted" or not isinstance(output, dict):
            raise MarketingScriptNotReady(f"run {run_id}: no accepted script to check the post image against")
        drawn = metadata.get("onscreen_text")
        if not isinstance(drawn, list) or not drawn:
            raise MarketingRequestInvalid(f"run {run_id}: a post image must declare metadata.onscreen_text")
        if _template_script(run_id, script, output, what="post image"):
            self._check_template_image_text(run_id, output, drawn)
            return
        image_post = normalize_image_post(output.get("image_post"))
        footer = output.get("image_footer")
        if image_post is None or not isinstance(footer, str) or not footer.strip():
            raise MarketingRequestInvalid(
                f"run {run_id}: the accepted script carries no image post (image_post + image_footer) "
                "to check the post image against")
        allowed = {image_post["title"], *image_post["paragraphs"], footer}
        extra = [t for t in drawn if not isinstance(t, str) or t not in allowed]
        if extra:
            raise MarketingRequestInvalid(
                f"run {run_id}: the post image declares {len(extra)} on-screen string(s) that are not the "
                f"accepted image post's title, paragraphs or footer (first: {str(extra[0])[:80]!r})")
        if footer not in drawn:
            raise MarketingRequestInvalid(f"run {run_id}: the post image does not draw its footer")

    @staticmethod
    def _check_template_image_text(run_id: str, output: Dict[str, Any], drawn: List[Any]) -> None:
        """The template branch of `_check_post_image_text` (drop 2, contract D12). A template post image
        draws its closed `image_spec` (`template_onscreen`): the allowed strings are exactly that spec's
        drawable strings — each referenced logo's company name included, for its wordmark tile — plus
        the code-owned `image_footer`, which MUST be drawn. Never `image_post`: on a template that is the
        name-free ALT TEXT, which may describe more than the picture shows, so a Drop-1-shaped image
        (alt title + paragraphs drawn) is refused. A spec that does not validate against the run's own
        logos and footer allows nothing (422)."""
        footer = output.get("image_footer")
        if not isinstance(footer, str) or not footer.strip():
            raise MarketingRequestInvalid(
                f"run {run_id}: the accepted template script carries no image_footer to check the post "
                "image against")
        spec, logos = output.get("image_spec"), output.get("logos")
        problem = template_onscreen.validate_image_spec(spec, template_onscreen.logo_keys(logos), footer=footer)
        if problem is not None:
            logger.error("marketing post image REFUSED run_id=%s: the accepted template's image_spec does not "
                         "validate (%s)", run_id, str(problem)[:200])
            raise MarketingRequestInvalid(
                f"run {run_id}: the accepted template's image_spec does not validate ({str(problem)[:120]}); "
                "no post image can be checked against it")
        allowed = set(template_onscreen.image_strings(spec, logos)) | {footer}
        extra = [t for t in drawn if not isinstance(t, str) or t not in allowed]
        if extra:
            raise MarketingRequestInvalid(
                f"run {run_id}: the template post image declares {len(extra)} on-screen string(s) that are "
                f"not its image_spec's strings, its logos' names or its footer (first: {str(extra[0])[:80]!r})")
        if footer not in drawn:
            raise MarketingRequestInvalid(f"run {run_id}: the template post image does not draw its footer")

    @staticmethod
    def _verified_pointer(run: Dict[str, Any], ready: List[Dict[str, Any]], key: str, kind: str,
                          *, role: Optional[str] = None) -> Optional[str]:
        """`run.metadata[key]` if it names a `ready` asset of `kind` of THIS run — and, with `role`,
        one whose `metadata.image_role` is `role` — else None (logged)."""
        meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        pointer = meta.get(key)
        if not pointer:
            return None
        match = next((a for a in ready if a.get("id") == pointer), None)
        match_md = (match or {}).get("metadata") if isinstance((match or {}).get("metadata"), dict) else {}
        if match is not None and match.get("kind") == kind and (role is None or match_md.get("image_role") == role):
            return pointer
        logger.warning("marketing run %s: metadata.%s=%r is not a ready %s asset%s of this run — "
                       "ignored", run.get("id"), key, pointer, kind,
                       f" with image_role {role!r}" if role else "")
        return None

    async def read_back(self, run_id: str, *, claim: CallerClaim) -> Dict[str, Any]:
        """`{voice_asset_id, video_asset_id, image_asset_id, assets}` for the run's HOLDER — the
        read-back a resumed or re-claimed stage derives its media from (rules marketing.md §2). Each
        pointer comes from the run's metadata and is returned only if it names a `ready` asset of the
        right kind of THIS run (the image: a `card` whose image_role is the post image); anything
        else is logged and returned as None. `assets` are the run's ready rows with their public URL."""
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        problem = claim_problem(run, claim)
        if problem is not None:
            raise MarketingRunNotHeld(f"run {run_id} is not held by this caller: {problem}")
        ready = [a for a in await self.list_assets(run_id) if a.get("status") == "ready"]
        for a in ready:
            a["public_url"] = self.public_url(str(a.get("storage_path")))
        return {"voice_asset_id": self._verified_pointer(run, ready, "voice_asset_id", "audio"),
                "video_asset_id": self._verified_pointer(run, ready, "video_asset_id", "video"),
                "image_asset_id": self._verified_pointer(run, ready, "image_asset_id", "card",
                                                         role=IMAGE_ROLE_POST),
                "assets": ready}

    async def list_ready_assets(self, run_id: str, *, claim: CallerClaim) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """(voice_asset_id, ready assets with their public URL) — `read_back` without the video
        pointer, kept for its callers."""
        back = await self.read_back(run_id, claim=claim)
        return back["voice_asset_id"], back["assets"]

    @staticmethod
    def public_url(storage_path: str) -> str:
        """The unsigned public URL of an object in the media bucket (PUBLIC by design, §12.3)."""
        base = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{settings.MARKETING_MEDIA_BUCKET}"
        return f"{base}/{storage_path}"

    async def _object_stat(self, path: str) -> Optional[Dict[str, Any]]:
        """`{"size", "mimetype"}` of `path` as Storage recorded the upload, or None when the object
        is ABSENT. Read from a prefix LIST (the listing carries each object's `metadata`; a HEAD
        does not), which raises on an outage — that raise becomes `MarketingRunError` (503, the
        worker retries), never "absent". `size`/`mimetype` are None when the listing omits them."""
        bucket = settings.MARKETING_MEDIA_BUCKET
        prefix, _, name = path.rpartition("/")
        try:
            listing = await sb_exec_storage(
                lambda: self.sb.storage.from_(bucket).list(prefix, {"limit": 100, "search": name})
            )
        except Exception as e:
            raise MarketingRunError(f"storage LIST failed for {path}: {type(e).__name__}: {e}") from e
        for item in listing or []:
            if (item or {}).get("name") == name:
                md = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                size = md.get("size", md.get("contentLength"))
                return {"size": size, "mimetype": md.get("mimetype")}
        return None

    async def store_logo(self, data: bytes, info: LogoInfo) -> Optional[Dict[str, Any]]:
        """Store a company logo the template build fetched and `logo_check.inspect_logo` accepted (drop 2,
        contract D10): `{path, url, sha256, bytes, width, height}`, or None — the caller then draws the
        company's wordmark tile. Never raises for a logo (a logo never refuses a candidate).

        Content-addressed at `logos/<sha256[:32]>.<png|jpg>` in the PUBLIC media bucket, with NO asset
        row: one object serves every run that names the company. So it is never overwritten and never
        deleted — the object is shared and may already be in a published post. Stat first: the same size
        and content type → reuse it; anything else at that key → None + ERROR (left in place). Absent →
        upload with `x-upsert: false` and a one-year cache; a conflict (another process won the race) is
        stat'ed again under the same rule. The bytes are stored exactly as fetched."""
        if not isinstance(data, (bytes, bytearray)) or not data or not isinstance(info, LogoInfo):
            logger.warning("marketing logo: nothing storable (data %s, info %s) — wordmark",
                           type(data).__name__, type(info).__name__)
            return None
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        if digest != info.sha256:
            logger.error("marketing logo: the bytes' sha256 %s… is not the inspected logo's %s… — not stored, "
                         "wordmark", digest[:12], str(info.sha256)[:12])
            return None
        try:
            path, content_type = logo_path(info), info.content_type
            entry = {"path": path, "url": self.public_url(path), "sha256": digest, "bytes": len(data),
                     "width": int(info.width), "height": int(info.height)}
        except Exception as e:  # noqa: BLE001 — a malformed LogoInfo is a wordmark, never a 500
            logger.warning("marketing logo: no storage path for sha256 %s… (%s: %s) — wordmark",
                           digest[:12], type(e).__name__, e)
            return None

        async def _existing() -> Optional[bool]:
            """True: the object at `path` is these bytes (size + type); False: something else is there
            (ERROR); None: absent. Raises MarketingRunError when Storage cannot be read."""
            stat = await self._object_stat(path)
            if stat is None:
                return None
            size, mimetype = stat.get("size"), stat.get("mimetype")
            got_type = str(mimetype or "").split(";", 1)[0].strip().lower()
            try:
                same_size = size is not None and int(size) == len(data)
            except (TypeError, ValueError):
                same_size = False
            if same_size and got_type == content_type:
                return True
            logger.error("marketing logo: %s already holds another object (size %r, type %r; want %d bytes "
                         "of %s) — left in place (shared, maybe published), wordmark instead", path, size,
                         mimetype, len(data), content_type)
            return False

        bucket = settings.MARKETING_MEDIA_BUCKET
        try:
            there = await _existing()
            if there is True:
                logger.info("marketing logo: reusing %s (%d bytes)", path, len(data))
                return entry
            if there is False:
                return None
            try:
                await sb_exec_storage(lambda: self.sb.storage.from_(bucket).upload(
                    path, data, {"content-type": content_type, "cache-control": "31536000", "upsert": "false"}))
            except Exception as e:
                if not _is_storage_conflict(e):
                    logger.warning("marketing logo: upload of %s failed (%s: %s) — wordmark", path,
                                   type(e).__name__, str(e)[:200])
                    return None
                logger.info("marketing logo: %s was stored by a concurrent writer — checking it", path)
                there = await _existing()
                if there is not True:
                    if there is None:
                        logger.warning("marketing logo: %s answered a conflict but is not listed — wordmark", path)
                    return None
                return entry
        except MarketingRunError as e:
            logger.warning("marketing logo: Storage could not be read for %s (%s) — wordmark", path, e)
            return None
        logger.info("marketing logo: stored %s (%d bytes, %dx%d)", path, len(data), entry["width"], entry["height"])
        return entry

    async def _verify_object(self, asset: Dict[str, Any], stat: Dict[str, Any]) -> None:
        """The object in the bucket must be the one registered: same byte size, same content type
        (parameters such as `; charset` ignored). Shared by BOTH paths to `ready`.

        On a MISMATCH the object is deleted and the row marked `failed` before raising
        `MarketingAssetMismatch`: the key is immutable (`x-upsert: false`), so a wrong object left in
        place would block its own re-upload forever. When Storage does not report a size or type,
        nothing is deleted — an unknown is not a mismatch — and the ledger error is retried.

        sha256 is NOT re-computed here (decision 2026-09-29, §12.3): it would mean downloading every
        MP4 into the single uvicorn worker. Size + type + the worker's own ffprobe gate + the
        registration checks of what the video says and draws are the verification."""
        path = str(asset.get("storage_path"))
        size, mimetype = stat.get("size"), stat.get("mimetype")
        if size is None or not mimetype:
            raise MarketingRunError(
                f"storage reported no size/content type for {path} (size={size!r}, "
                f"mimetype={mimetype!r}); not marking it ready")
        want_size = asset.get("bytes")
        want_type = str(asset.get("content_type") or "").split(";", 1)[0].strip().lower()
        got_type = str(mimetype).split(";", 1)[0].strip().lower()
        try:
            got_size = int(size)
        except (TypeError, ValueError):
            raise MarketingRunError(f"storage reported a non-numeric size {size!r} for {path}") from None
        problems = []
        if want_size is not None and got_size != int(want_size):
            problems.append(f"size {got_size} != registered {want_size}")
        if got_type != want_type:
            problems.append(f"content type {got_type!r} != registered {want_type!r}")
        if not problems:
            return
        logger.error("marketing asset MISMATCH id=%s path=%s: %s — deleting the object and failing "
                     "the row", asset.get("id"), path, "; ".join(problems))
        bucket = settings.MARKETING_MEDIA_BUCKET
        try:
            await sb_exec_storage(lambda: self.sb.storage.from_(bucket).remove([path]))
        except Exception as e:
            raise MarketingRunError(
                f"asset {asset.get('id')} at {path} does not match ({'; '.join(problems)}) and could "
                f"not be deleted: {type(e).__name__}: {e}") from e
        await _exec(
            self.sb.table(ASSETS).update({"status": "failed", "updated_at": _now_iso()}).eq("id", asset.get("id")),
            op="verify_object.fail", asset_id=asset.get("id"),
        )
        raise MarketingAssetMismatch(
            f"asset {asset.get('id')} at {path} is not what was registered ({'; '.join(problems)}); "
            "object deleted, row failed")

    async def complete_asset(self, asset_id: str, *, claim: CallerClaim) -> Dict[str, Any]:
        """Flip `pending_upload` → `ready` ONLY after the object is verifiably in the bucket.
        The worker's word is not enough: a `ready` row whose object 404s would publish a
        broken post, and nothing downstream re-checks. Only the holder of the asset's RUN may
        complete it (the route carries no run id, so the asset's own run is the one checked)."""
        asset = _one(
            await _exec(
                self.sb.table(ASSETS).select("*").eq("id", asset_id).limit(1),
                op="complete_asset.select", asset_id=asset_id,
            )
        )
        if asset is None:
            raise MarketingAssetNotFound(f"asset {asset_id} not found")
        run = await self.get_run(str(asset.get("run_id")))
        if run is None:
            raise MarketingRunNotFound(f"asset {asset_id}: run {asset.get('run_id')} not found")
        problem = claim_problem(run, claim)
        if problem is not None:
            raise MarketingRunNotHeld(f"asset {asset_id}: run {run.get('id')} is not held by this caller: {problem}")
        if asset.get("status") == "ready":
            return asset
        if run.get("status") != "in_progress":
            raise MarketingRunNotHeld(
                f"asset {asset_id}: run {run.get('id')} is {run.get('status')!r}; assets complete "
                "only on an in_progress run")
        path = asset["storage_path"]
        stat = await self._object_stat(path)
        if stat is None:
            raise MarketingAssetMissingInStorage(
                f"asset {asset_id} at {path} is not in bucket "
                f"{settings.MARKETING_MEDIA_BUCKET}"
            )
        await self._verify_object(asset, stat)
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

    async def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """One asset row by id (the publisher resolves a video post's verified MP4 by it)."""
        return _one(
            await _exec(self.sb.table(ASSETS).select("*").eq("id", asset_id).limit(1),
                        op="get_asset", asset_id=asset_id)
        )

    async def list_assets(self, run_id: str) -> List[Dict[str, Any]]:
        res = await _exec(self.sb.table(ASSETS).select("*").eq("run_id", run_id), op="list_assets", run_id=run_id)
        return list(getattr(res, "data", None) or [])

    async def _recorded_formats(self, run_id: str) -> Dict[str, set]:
        """{platform: {formats}} of the posts already recorded for `run_id` (any status) — what
        `create_posts`' one-post-per-platform rule is checked against. A failed read raises
        (`MarketingRunError`, retried by the worker), never reads as "nothing recorded"."""
        res = await _exec(self.sb.table(POSTS).select("platform,format").eq("run_id", run_id),
                          op="create_posts.recorded", run_id=run_id)
        out: Dict[str, set] = {}
        for row in getattr(res, "data", None) or []:
            if isinstance(row, dict) and row.get("platform"):
                out.setdefault(str(row["platform"]), set()).add(str(row.get("format")))
        return out

    # posts -----------------------------------------------------------------

    async def create_posts(
        self, run_id: str, specs: List[Dict[str, Any]], *, claim: CallerClaim
    ) -> List[Dict[str, Any]]:
        """One ledger row per (platform, format). Born `pending_review` unless
        MARKETING_AUTO_PUBLISH is on. Re-creating an existing pair returns the existing row
        untouched — a resumed run must never reset a post an admin already approved.

        The worker names the OUTLET (platform, format, asset ids) and nothing else. The caption
        and title come from the run's ACCEPTED script (`marketing_scripts.output.posts`), which
        passed every server-side compliance check (§12.5); the worker's `caption`, `title` and
        `metadata` are ignored. That is what makes MARKETING_AUTO_PUBLISH safe to turn on later:
        the least-trusted process in the engine can no longer choose the words that get posted.
        Posts that carry media are always born `pending_review` — the server cannot yet verify
        what a worker-rendered video says (Phase 7 adds that check).

        The worker does not choose how many posts one caption becomes either: the (platform,
        format) pair must be in POST_FORMATS_BY_PLATFORM, a media format must carry a READY asset
        of a matching kind (POST_MEDIA_KINDS), and only a media-less `text` post can be born
        `approved`. EVERY spec is validated before the first INSERT, so a deterministic refusal
        (409/422, never retried) leaves no partial ledger behind — a later spec that is invalid
        used to leave the earlier ones recorded, and publishable, under a run that then failed.
        A transient failure mid-loop can still leave a prefix; the re-send adopts those rows
        through the idempotency key, so that heals itself.

        Drop 1 (contract C7): when the accepted output froze `post_formats`, each spec's format must
        be its platform's frozen one; an `image` post carries exactly the run's verified post image
        (`metadata.image_asset_id` → a ready card whose image_role is the post image) and is born
        `pending_review` like any media post; an output with no frozen formats takes no image post.
        A `video` post is pinned the same way, to exactly the run's verified `metadata.video_asset_id`
        (without frozen formats its video posts must at least all name the same assets): the review
        bundle shows ONE video and one tap approves every video post.
        At most one post per (run, platform), in the request and against the ledger (a read before
        the INSERTs — not atomic with them, but the claim fence makes the holder the only writer and
        the frozen formats leave each platform one valid format anyway).

        Drop 2 (contract D12): the post gate is dispatched on the SCRIPT's class (its frozen
        `template_id`): "A" → the judge gate; "C"/"F" (a Company Weekly template) → the class switch, the
        per-series switch (drop 2b), the template authorship and `news_templates.revalidate` (409
        `MarketingTemplateRefused` on a miss; 422 for the authorship);
        anything else → 422. A template post is never auto-approved. Every post's metadata records its
        `content_class`, `series`, `authorship`, `series_trail` and its `made_with_ai` flag."""
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        if run.get("status") not in ("in_progress", "media_ready"):
            raise MarketingRunNotHeld(
                f"run {run_id} is {run.get('status')!r}; posts are recorded only for an in_progress "
                "or media_ready run"
            )
        problem = claim_problem(run, claim)
        if problem is not None:
            raise MarketingRunNotHeld(f"run {run_id} is not held by this caller: {problem}")
        run_date = date.fromisoformat(str(run["run_date"]))
        script = await self.get_script(run_id)
        output = (script or {}).get("output")
        if not script or script.get("status") != "accepted" or not isinstance(output, dict):
            raise MarketingScriptNotReady(
                f"run {run_id} has no accepted script (status={(script or {}).get('status')})"
            )
        # Every content class needs its OWN post gate, dispatched explicitly — never "not A → pass".
        # Drop 2 (contract D12): the class is the SCRIPT's — `selection.content_class_of` of its frozen
        # `template_id` (a lesson template → "A", a news series → "C"/"F") — and the output must say
        # the same (`output.content_class`; a writer package carries none, which reads as "A" only for
        # a lesson id). `marketing_runs.content_class` is a MIRROR nothing gates on: a mismatch is
        # logged, never obeyed. Anything else (an unknown or retired id, a NULL, a class the output
        # contradicts) is refused before any asset read or INSERT.
        template_id = script.get("template_id")
        klass = selection.content_class_of(template_id)
        stored_class = output.get("content_class") or ("A" if klass == "A" else None)
        if klass is None or stored_class != klass:
            shown_id = str(template_id)[:40]
            shown_class = None if stored_class is None else str(stored_class)[:20]
            logger.error("marketing create_posts REFUSED run_id=%s: the script's template %r gives content "
                         "class %r and its output says %r — no post gate; nothing recorded",
                         run_id, shown_id, klass, shown_class)
            raise MarketingRequestInvalid(
                f"run {run_id}: the script's template {shown_id!r} (class {klass!r}, output class "
                f"{shown_class!r}) has no post gate; nothing recorded")
        if run.get("content_class") != klass:
            logger.warning("marketing create_posts run_id=%s: the run mirrors content_class %r but its script "
                           "is class %r (template %r) — the script decides", run_id,
                           str(run.get("content_class"))[:20], klass, str(template_id)[:40])
        authorship = post_copy.AUTHORSHIP_AI
        series = selection.LESSON
        if klass == "A":
            # The semantic judge (§12.5): `shadow` accepts drafts it flagged and `off` never asks it,
            # so a package it did not check in `enforce` mode never becomes a post — whatever
            # MARKETING_AUTO_PUBLISH or a reviewer later says. The writer records the mode IN the
            # package (`output.judge.mode`; absent means off).
            if output.get("authorship") not in (None, post_copy.AUTHORSHIP_AI):
                logger.error("marketing create_posts REFUSED run_id=%s: a lesson script whose output claims "
                             "authorship %r — nothing recorded", run_id, str(output.get("authorship"))[:20])
                raise MarketingRequestInvalid(
                    f"run {run_id}: a lesson script's output claims authorship "
                    f"{str(output.get('authorship'))[:20]!r}; nothing recorded")
            judge = output.get("judge") if isinstance(output.get("judge"), dict) else {}
            if judge.get("mode") != "enforce":
                raise MarketingJudgeNotEnforced(
                    f"run {run_id}: the accepted script was judged in mode {judge.get('mode') or 'off'!r}, "
                    "not 'enforce' — no post is recorded (set MARKETING_JUDGE_MODE=enforce on the web)")
        elif klass in NEWS_CLASSES:
            # A Company Weekly TEMPLATE (drop 2): never judged — composed by code from an as-filed record.
            # Its gate is the switch, the authorship and a full re-check: `news_templates.revalidate`
            # re-composes the stored output from the stored fact sheet on this run date and compares every
            # public field, then re-runs every template rule (placement, Congress names, the verb table,
            # banned words, the disclaimer, the footer). A template the current code would not write is
            # never recorded (409, the worker skips the day `template_refused`).
            enabled = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
            if klass not in enabled:
                logger.error("marketing create_posts TEMPLATE REFUSED run_id=%s series=%s: class %s is not in "
                             "MARKETING_CONTENT_CLASSES (%s) — nothing recorded", run_id, str(template_id)[:40],
                             klass, ",".join(sorted(enabled)))
                raise MarketingTemplateRefused(
                    f"run {run_id}: content class {klass} is switched off (MARKETING_CONTENT_CLASSES); "
                    "nothing recorded")
            # The per-series switch (drop 2b), read NOW like the class switch: the series must still be
            # listed in MARKETING_NEWS_SERIES AND shipped (`selection.parse_news_series` is that
            # intersection). A series switched off after its day was built — or one this deploy no longer
            # ships — is never recorded (409, the worker skips the day `template_refused`).
            series_on = selection.parse_news_series(settings.MARKETING_NEWS_SERIES)
            if template_id not in series_on:
                logger.error("marketing create_posts TEMPLATE REFUSED run_id=%s series=%s: the series is not "
                             "on (MARKETING_NEWS_SERIES ∩ shipped = %s) — nothing recorded", run_id,
                             str(template_id)[:40], ",".join(sorted(series_on)) or "none")
                raise MarketingTemplateRefused(
                    f"run {run_id}: series {str(template_id)[:40]} is switched off (MARKETING_NEWS_SERIES) "
                    "or not shipped; nothing recorded")
            if output.get("authorship") != TEMPLATE_AUTHORSHIP:
                logger.error("marketing create_posts REFUSED run_id=%s: a class-%s script whose output's "
                             "authorship is %r — nothing recorded", run_id, klass,
                             str(output.get("authorship"))[:20])
                raise MarketingRequestInvalid(
                    f"run {run_id}: a class-{klass} script must carry template authorship "
                    f"(got {str(output.get('authorship'))[:20]!r}); nothing recorded")
            if output.get("series") != template_id:
                codes = ["series_mismatch"]
            else:
                codes = sorted({str(v.get("code")) for v in news_templates.revalidate(
                    output, fact_sheet=script.get("fact_sheet"), run_date=run_date)})
            if codes:
                logger.error("create_posts TEMPLATE REFUSED run_id=%s series=%s codes=%s — nothing recorded",
                             run_id, str(template_id)[:40], codes)
                raise MarketingTemplateRefused(
                    f"run {run_id}: the {str(template_id)[:40]} template failed its re-check ({', '.join(codes)[:200]}); "
                    "nothing recorded")
            authorship = TEMPLATE_AUTHORSHIP
            series = str(template_id)
        else:   # unreachable: content_class_of answers "A", a NEWS_CLASSES value or None (refused above)
            logger.error("marketing create_posts REFUSED run_id=%s: content class %r has no post gate — "
                         "nothing recorded", run_id, klass)
            raise MarketingRequestInvalid(f"run {run_id}: content class {klass!r} has no post gate; nothing recorded")
        series_trail = _series_trail(script)
        copy_by_platform = output.get("posts") if isinstance(output.get("posts"), dict) else {}
        # Drop 1 (contract C7): the formats frozen into the accepted output at write time. None = a
        # script accepted before image posts existed: validated exactly as before, and never an
        # image post. Present but unreadable (a hand-edited row) = nothing is recorded.
        try:
            formats = frozen_post_formats(output)
        except ValueError as e:
            logger.error("marketing create_posts REFUSED run_id=%s: the accepted script's post_formats "
                         "do not read back (%s) — nothing recorded", run_id, e)
            raise MarketingRequestInvalid(
                f"run {run_id}: the accepted script's post_formats do not read back ({e}); nothing recorded"
            ) from e
        wants_assets = any(spec.get("asset_ids") for spec in specs)
        wants_image = any(spec.get("format") == "image" for spec in specs)
        wants_video = any(spec.get("format") == "video" for spec in specs)
        asset_rows = (await self.list_assets(run_id)
                      if (wants_assets or wants_image or (wants_video and formats is not None)) else [])
        assets = {a.get("id"): a for a in asset_rows}
        ready_rows = [a for a in asset_rows if a.get("status") == "ready"]
        # The ONE image every image post of the run carries: the run's verified post-image pointer
        # (`metadata.image_asset_id` → a ready card of this run whose image_role is the post image,
        # whose drawn text `_check_post_image_text` checked at registration).
        image_id = (self._verified_pointer(run, ready_rows, "image_asset_id", "card", role=IMAGE_ROLE_POST)
                    if wants_image and formats is not None else None)
        # The ONE video every video post of the run carries, pinned the same way: the run's verified
        # `metadata.video_asset_id` (a ready video of this run, whose on-screen text and voice
        # `_check_onscreen_text` checked). The review bundle shows the owner ONE video for all the
        # video posts and one "Approve all" decides them — the server cannot read pixels, so a second
        # ready video on another post would be approved unseen. A script accepted before drop 1 (no
        # frozen formats) is not pinned to the pointer, but its video posts must still agree.
        video_id = (self._verified_pointer(run, ready_rows, "video_asset_id", "video")
                    if wants_video and formats is not None else None)
        video_lists: set = set()
        # At most ONE post per (run, platform): the ledger's UNIQUE is (run, platform, format), so
        # a platform already recorded in another format would otherwise take a second post.
        recorded = await self._recorded_formats(run_id)

        # ── validate EVERYTHING first ──────────────────────────────────────────
        bad_pairs: List[str] = []
        no_copy: List[str] = []
        not_ready: List[str] = []
        bad_media: List[str] = []
        seen: Dict[Tuple[str, str], Tuple[str, ...]] = {}
        ai_flags: Dict[Tuple[str, str], bool] = {}
        formats_named: Dict[str, set] = {}
        planned: List[Tuple[str, str, Dict[str, Any], List[str]]] = []
        for spec in specs:
            platform, fmt = spec["platform"], spec["format"]
            allowed = POST_FORMATS_BY_PLATFORM.get(platform, ())
            copy = copy_by_platform.get(platform)
            has_copy = isinstance(copy, dict) and bool(copy.get("caption"))
            if fmt not in allowed:
                bad_pairs.append(f"{platform}/{fmt} (allowed: {', '.join(allowed) or 'none'})")
            elif formats is not None and (platform in formats or has_copy) and formats.get(platform) != fmt:
                # (An outlet the script dropped has neither copy nor a frozen format: that stays the
                # `no_copy` refusal below, exactly as before.)
                bad_pairs.append(f"{platform}/{fmt} (this run's format for {platform} is "
                                 f"{formats.get(platform) or 'none'})")
            elif formats is None and fmt == "image":
                bad_pairs.append(f"{platform}/{fmt} (the accepted script froze no post formats; an "
                                 "image post needs them)")
            if not has_copy:
                no_copy.append(platform)
            if fmt in allowed:
                # The post's platform "made with AI" flag (drop 2): an AI-written lesson always; a
                # template only as a narrated video. Decided here, before any INSERT, so a format no
                # template post takes is a refusal of the whole request — never a guessed flag.
                try:
                    ai_flags[(platform, fmt)] = post_copy.made_with_ai(authorship, fmt)
                except ValueError as e:
                    bad_pairs.append(f"{platform}/{fmt} ({e})")
            asset_ids = list(dict.fromkeys(spec.get("asset_ids") or []))
            previous = seen.setdefault((platform, fmt), tuple(asset_ids))
            if previous != tuple(asset_ids):
                bad_media.append(f"{platform}/{fmt} named twice with different assets")
            formats_named.setdefault(platform, set()).add(fmt)
            other = sorted(recorded.get(platform, set()) - {fmt})
            if other:
                bad_media.append(f"{platform}/{fmt}: this run already recorded {platform} as {other}")
            if fmt == "image" and formats is not None:
                if image_id is None:
                    bad_media.append(f"{platform}/image: the run has no verified post image")
                elif asset_ids != [image_id]:
                    bad_media.append(f"{platform}/image must carry exactly the run's post image ({image_id})")
            if fmt == "video" and formats is not None:
                if video_id is None:
                    bad_media.append(f"{platform}/video: the run has no verified video")
                elif asset_ids != [video_id]:
                    bad_media.append(f"{platform}/video must carry exactly the run's video ({video_id})")
            elif fmt == "video":
                video_lists.add(tuple(asset_ids))
            kinds = POST_MEDIA_KINDS.get(fmt, ())
            matching = 0
            for aid in asset_ids:
                asset = assets.get(aid)
                if asset is None or asset.get("status") != "ready":
                    not_ready.append(aid)
                elif asset.get("kind") not in kinds:
                    bad_media.append(f"{platform}/{fmt} cannot carry a {asset.get('kind')!r} asset ({aid})")
                else:
                    matching += 1
            if fmt in MEDIA_REQUIRED_FORMATS and not matching and not any(a in not_ready for a in asset_ids):
                bad_media.append(f"{platform}/{fmt} carries no ready {'/'.join(kinds)} asset")
            if isinstance(copy, dict):
                planned.append((platform, fmt, copy, asset_ids))
        for platform, named in formats_named.items():
            if len(named) > 1:
                bad_media.append(f"{platform} named in {len(named)} formats {sorted(named)} (one post per platform)")
        if len(video_lists) > 1:
            bad_media.append(f"the video posts name {len(video_lists)} different asset lists "
                             "(every video post of a run carries the same video)")
        if bad_pairs:
            raise MarketingRequestInvalid(
                f"run {run_id}: the server records no post for {sorted(set(bad_pairs))}"
            )
        if no_copy:
            raise MarketingScriptNotReady(
                f"run {run_id}: the accepted script carries no copy for {sorted(set(no_copy))}"
            )
        if not_ready:
            raise MarketingAssetMissingInStorage(
                f"run {run_id}: asset(s) {sorted(set(not_ready))} are not ready assets of this run"
            )
        if bad_media:
            raise MarketingRequestInvalid(f"run {run_id}: {sorted(set(bad_media))}")

        # A dry-run RUN never auto-approves, whatever the web process's switch says: the
        # worker's rehearsal must not become a real post because a different service flipped
        # MARKETING_AUTO_PUBLISH. The flag rides on every row so the publisher sees it too.
        run_dry = bool(run.get("dry_run"))
        auto = settings.MARKETING_AUTO_PUBLISH and not run_dry
        out: List[Dict[str, Any]] = []
        for platform, fmt, copy, asset_ids in planned:
            # Only a media-less TEXT post of a class-A (judged) script is born approved: the server
            # has verified every word of it, and the format map allows `text` only on text-native
            # outlets. A template post (C/F) is NEVER auto-approved — a human approves every one.
            initial = ("approved" if (auto and fmt == "text" and not asset_ids and klass == "A")
                       else "pending_review")
            key = idempotency_key_for(run_date, platform, fmt)
            row = {
                "run_id": run_id,
                "platform": platform,
                "format": fmt,
                "status": initial,
                "title": copy.get("title"),
                "caption": copy["caption"],
                "asset_ids": asset_ids,
                "idempotency_key": key,
                "metadata": {
                    "dry_run": run_dry,
                    "generation_id": script.get("generation_id"),
                    "source_ref": script.get("source_ref"),
                    "template_id": script.get("template_id"),
                    # Drop 2: what the post is (the script decides) and the platform AI flag it
                    # carries (`post_copy.made_with_ai`; the X outlet ANDs it with its setting).
                    "content_class": klass,
                    "series": series,
                    "authorship": authorship,
                    "series_trail": [dict(step) for step in series_trail],   # flat str entries
                    "made_with_ai": ai_flags[(platform, fmt)],
                },
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
            "marketing posts recorded run_id=%s n=%d auto_publish=%s class=%s series=%s authorship=%s",
            run_id, len(out), auto, klass, series, authorship,
        )
        return out

    async def list_posts(
        self, status: str, *, limit: int = 50, platforms: Optional[List[str]] = None,
        live_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Oldest first. `platforms` and `live_only` filter IN the query, before the LIMIT: the
        publisher used to fetch the 100 oldest rows and filter them afterwards, so 100 approved rows
        it could never send (a platform with no adapter, a rehearsal) starved every sendable post
        behind them. `live_only` keeps rows whose `metadata.dry_run` is exactly false (`create_posts`
        writes it on every row; a row without it is treated as a rehearsal — never sent)."""
        query = self.sb.table(POSTS).select("*").eq("status", status)
        if platforms is not None:
            if not platforms:
                return []
            query = query.in_("platform", list(platforms))
        if live_only:
            query = query.eq("metadata->>dry_run", "false")
        res = await _exec(query.order("created_at").limit(limit), op="list_posts", status=status)
        return list(getattr(res, "data", None) or [])

    async def get_post(self, post_id: str) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(self.sb.table(POSTS).select("*").eq("id", post_id).limit(1),
                        op="get_post", post_id=post_id)
        )

    async def review_post(self, post_id: str, decision: str, *, reviewed_by: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """A human's verdict on a `pending_review` post: `approve` → `approved` (approved_at /
        approved_by), `reject` → `rejected`. ONE conditional UPDATE on `status = pending_review`,
        so a double tap, a second reviewer or a race with anything else that moves the row can
        never flip a decided post (the publisher's writes are the fenced `transition_post`).

        Returns (outcome, row): `approved` / `rejected` when this call decided it,
        `already_<status>` when the post was no longer pending, `not_found` when it does not exist.
        The verdict is also kept in `metadata.review` ({decision, by, at})."""
        if decision not in ("approve", "reject"):
            raise ValueError(f"unknown review decision {decision!r}")
        current = await self.get_post(post_id)
        if current is None:
            return "not_found", None
        if current.get("status") != "pending_review":
            return f"already_{current.get('status')}", current
        now = _now_iso()
        status = "approved" if decision == "approve" else "rejected"
        meta = dict(current.get("metadata") or {}) if isinstance(current.get("metadata"), dict) else {}
        meta["review"] = {"decision": status, "by": reviewed_by, "at": now}
        patch: Dict[str, Any] = {"status": status, "metadata": meta, "updated_at": now}
        if status == "approved":
            patch.update({"approved_at": now, "approved_by": reviewed_by})
        updated = _one(
            await _exec(
                self.sb.table(POSTS).update(patch).eq("id", post_id).eq("status", "pending_review"),
                op="review_post", post_id=post_id,
            )
        )
        if updated is None:   # decided by someone else between the read and this write
            again = await self.get_post(post_id)
            return (f"already_{(again or {}).get('status')}" if again else "not_found"), again
        logger.info("marketing post %s post_id=%s platform=%s by=%s", status.upper(), post_id,
                    updated.get("platform"), reviewed_by)
        return status, updated

    async def bundle_posts(self, bundle_id: str) -> List[Dict[str, Any]]:
        """Every post stamped with review bundle `bundle_id` (`metadata->review_bundle->>id`, the
        filter IN the query), any status, oldest first — only rows whose stamp reads back
        (`review_bundle_of`). A bundle id that is not a canonical uuid raises ValueError before any
        read; a failed read raises MarketingRunError (never "no such bundle")."""
        bid = _canonical_uuid(bundle_id)
        if bid is None or bid != bundle_id:
            raise ValueError(f"bundle id {str(bundle_id)[:60]!r} is not a canonical uuid")
        res = await _exec(
            self.sb.table(POSTS).select("*").eq("metadata->review_bundle->>id", bid)
            .order("created_at").limit(_BUNDLE_READ_LIMIT),
            op="bundle_posts", bundle_id=bid,
        )
        return [r for r in (getattr(res, "data", None) or [])
                if isinstance(r, dict) and (review_bundle_of(r) or {}).get("id") == bid]

    async def _posts_by_id(self, ids: List[str], *, op: str, bundle_id: str) -> Dict[str, Dict[str, Any]]:
        if not ids:
            return {}
        res = await _exec(self.sb.table(POSTS).select("*").in_("id", list(ids)).limit(_BUNDLE_READ_LIMIT),
                          op=op, bundle_id=bundle_id)
        return {(_canonical_uuid(r.get("id")) or str(r.get("id"))): r
                for r in (getattr(res, "data", None) or []) if isinstance(r, dict)}

    @staticmethod
    def _bundle_member_outcome(row: Optional[Dict[str, Any]], bundle_id: str) -> str:
        """Why a member was NOT decided by this bundle's tap: `not_found`; `moved` (still pending but it
        carries no stamp or another bundle's — it was, or will be, offered in a newer message);
        `already_<status>`; `changed` (pending, this bundle's, but its text is no longer what was shown)."""
        if row is None:
            return "not_found"
        if row.get("status") != "pending_review":
            return f"already_{row.get('status')}"
        if (review_bundle_of(row) or {}).get("id") != bundle_id:
            return "moved"
        return "changed"

    async def review_bundle(self, bundle_id: str, decision: str, *,
                            reviewed_by: str) -> Tuple[str, List[Dict[str, Any]]]:
        """The owner's ONE verdict on a review bundle (the review bot's ✅ Approve all / ❌ Reject all):
        `approve` → `approved` (approved_at / approved_by), `reject` → `rejected`, for every member
        that is still `pending_review`, still carries THIS bundle's stamp and, when the members are
        read, still shows the text the owner was shown (its `caption_sha`).

        A member whose text CHANGED since it was shown (only a hand edit in Studio can do that: caption
        and title are not in `_POST_WRITABLE`) is not decided, and its review stamps are cleared
        (`_release_changed_member`) so the next sweep offers its new text as a new bundle — the tap's
        keyboard is gone, so without that it could never be approved and would expire unseen. Best
        effort: a release that does not land is logged and reported (`reoffered` False).
        Residual, accepted: the text is compared at READ time only. An edit landing between that read
        and the UPDATE below is decided unseen; an `updated_at` fence would not catch it either (a
        Studio edit does not bump `updated_at`, and `marketing_posts` must never get a trigger).

        The decision is ONE conditional UPDATE — `id IN (those members) AND status = pending_review AND
        metadata->review_bundle->>id = <bundle id>` — so a member decided meanwhile (its own ✂ drop, a
        per-post button), re-sent in a newer bundle, or expired, is never flipped, and a replayed tap
        decides nothing. That UPDATE writes columns only: PostgREST cannot MERGE a different metadata
        document into each row of one statement, and replacing it would drop each row's own
        `dry_run` / `review_bundle` / review stamps. The `metadata.review` record ({decision, by, at,
        bundle_id}) is then merged into each decided row by the fenced `transition_post` — best effort:
        a member the publisher claimed in between keeps its decision without the record (logged).

        Returns (outcome, results). outcome: `decided` (this call decided at least one member),
        `nothing` (no member was left to decide), `not_found` (no post carries the bundle). results:
        one per member, in the bundle's own order — {post_id, platform, format, outcome, row, reoffered}
        with outcome `approved` / `rejected` / `already_<status>` / `moved` / `changed` / `not_found`;
        `reoffered` is True only for a `changed` member whose review stamps were cleared.
        A failed read or UPDATE raises MarketingRunError (nothing was decided by a failed UPDATE)."""
        if decision not in ("approve", "reject"):
            raise ValueError(f"unknown review decision {decision!r}")
        carriers = await self.bundle_posts(bundle_id)
        bid = bundle_id
        if not carriers:
            return "not_found", []
        by_id = {(_canonical_uuid(r.get("id")) or str(r.get("id"))): r for r in carriers}
        order: List[str] = []
        for row in carriers:
            for pid in (review_bundle_of(row) or {}).get("members", []):
                if pid not in order:
                    order.append(pid)
        order += [pid for pid in by_id if pid not in order]
        others = await self._posts_by_id([pid for pid in order if pid not in by_id],
                                         op="review_bundle.members", bundle_id=bid)

        eligible: List[str] = []
        for pid in order:
            row = by_id.get(pid)
            if row is None or row.get("status") != "pending_review":
                continue
            if review_caption_sha(row) != (review_bundle_of(row) or {}).get("caption_sha"):
                logger.warning("marketing bundle %s: post_id=%s platform=%s changed since it was shown "
                               "(caption_sha differs) — not decided", bid, pid, row.get("platform"))
                continue
            eligible.append(pid)

        status = "approved" if decision == "approve" else "rejected"
        now = _now_iso()
        decided: Dict[str, Dict[str, Any]] = {}
        if eligible:
            patch: Dict[str, Any] = {"status": status, "updated_at": now}
            if status == "approved":
                patch.update({"approved_at": now, "approved_by": reviewed_by})
            res = await _exec(
                self.sb.table(POSTS).update(patch).in_("id", eligible).eq("status", "pending_review")
                .eq("metadata->review_bundle->>id", bid),
                op="review_bundle", bundle_id=bid,
            )
            decided = {(_canonical_uuid(r.get("id")) or str(r.get("id"))): r
                       for r in (getattr(res, "data", None) or []) if isinstance(r, dict)}
            review = {"decision": status, "by": reviewed_by, "at": now, "bundle_id": bid}
            for pid, row in list(decided.items()):
                try:
                    merged = await self.transition_post(pid, expect_status=status, observed=row,
                                                        meta={"review": review}, retries=1)
                except Exception as e:   # the decision stands; only its record is missing
                    logger.warning("marketing bundle %s: post_id=%s is %s but its review record was not "
                                   "written (%s: %s)", bid, pid, status, type(e).__name__, e)
                    continue
                if merged is None:
                    logger.warning("marketing bundle %s: post_id=%s is %s but moved on before its review "
                                   "record was written (the publisher took it) — record skipped", bid, pid, status)
                else:
                    decided[pid] = merged
        lost = [pid for pid in eligible if pid not in decided]
        fresh = await self._posts_by_id(lost, op="review_bundle.reread", bundle_id=bid)

        results: List[Dict[str, Any]] = []
        for pid in order:
            if pid in decided:
                row, outcome = decided[pid], status
            elif pid in lost:
                row = fresh.get(pid)
                outcome = self._bundle_member_outcome(row, bid)
            else:
                row = by_id.get(pid) or others.get(pid)
                outcome = self._bundle_member_outcome(row, bid)
            reoffered = False
            if outcome == "changed" and row is not None:
                released = await self._release_changed_member(pid, row, bid)
                if released is not None:
                    row, reoffered = released, True
            results.append({"post_id": pid, "platform": (row or {}).get("platform"),
                            "format": (row or {}).get("format"), "outcome": outcome, "row": row,
                            "reoffered": reoffered})
        logger.info("marketing bundle %s bundle_id=%s decided=%d of %d by=%s (%s)", status.upper(), bid,
                    len(decided), len(order), reviewed_by,
                    ", ".join(f"{r['platform']}={r['outcome']}" for r in results))
        return ("decided" if decided else "nothing"), results

    async def _release_changed_member(self, post_id: str, row: Dict[str, Any],
                                      bundle_id: str) -> Optional[Dict[str, Any]]:
        """Clear the review stamps of a bundle member that was NOT decided because its text changed
        after it was shown, so the next review sweep (which offers only posts with no
        `review_notified_at`) offers the new text under a new bundle. Fenced on the row as it was read
        (status AND updated_at, no retry): a member that moved meanwhile — decided, or re-stamped in a
        newer bundle — is left alone. Best effort: returns the released row, or None (logged WARNING)."""
        try:
            released = await self.transition_post(post_id, expect_status="pending_review", observed=row,
                                                  unset=_REVIEW_OFFER_STAMPS, retries=0)
        except Exception as e:
            logger.warning("marketing bundle %s: post_id=%s changed since it was shown and its review stamps "
                           "were NOT cleared (%s: %s) — it will not be offered again by itself", bundle_id,
                           post_id, type(e).__name__, e)
            return None
        if released is None:
            logger.warning("marketing bundle %s: post_id=%s changed since it was shown but moved on before its "
                           "review stamps were cleared — not re-offered from here", bundle_id, post_id)
            return None
        logger.info("marketing bundle %s: post_id=%s platform=%s changed since it was shown — review stamps "
                    "cleared; the next sweep offers its new text", bundle_id, post_id, released.get("platform"))
        return released

    async def claim_post(
        self, post_id: str, *, observed: Optional[Dict[str, Any]] = None,
        publish: Optional[Dict[str, Any]] = None, charge: Optional[Tuple[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """approved → queued, atomically. None means another tick took it (or an admin
        rejected it between the list and the claim).

        With `observed` (the publisher's path since Phase 5) the claim IS the write-ahead: ONE
        conditional UPDATE, fenced on `status = approved` AND the observed `updated_at`, that also
        bumps `attempts`, charges the attempt's cost (`charge`) and merges `publish` into
        `metadata.publish` (state `sending`, the text hash, a Bluesky record key …). So a crash
        after the claim leaves a row that SAYS a send may have started — reconcile treats it as
        an unknown outcome — and two containers can never both claim it."""
        if observed is None:
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
        return await self.transition_post(
            post_id, expect_status="approved", observed=observed, status="queued",
            publish=publish, charge=charge, retries=0,
            claimed_at=_now_iso(), attempts=int(observed.get("attempts") or 0) + 1,
        )

    async def mark_post(self, post_id: str, status: str, **fields: Any) -> Dict[str, Any]:
        """LEGACY — no caller since Phase 5. Unconditional, and a `metadata=` field REPLACES the whole
        document (the review record, the dry-run flag, the cost journal the X cap sums). Every
        publisher write goes through `transition_post`; do not use this for a publish outcome."""
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

    # publisher ledger (Phase 5, design doc §12.10) ---------------------------------------------
    # Every publisher write is a FENCED, MERGING transition: conditional on the status (and the
    # updated_at) it observed, and it merges into `metadata` instead of replacing it — the
    # unconditional, metadata-replacing `mark_post` dropped `review` / `review_notified_at` /
    # `dry_run` and could overwrite a concurrent transition.

    @staticmethod
    def _merged_post_patch(
        row: Dict[str, Any], *, status: Optional[str], meta: Optional[Dict[str, Any]],
        publish: Optional[Dict[str, Any]], unset: Tuple[str, ...], charge: Optional[Tuple[Any, ...]],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = _now_iso()
        merged = dict(row["metadata"]) if isinstance(row.get("metadata"), dict) else {}
        for key in unset:
            merged.pop(key, None)
        if meta:
            merged.update(meta)
        if publish:
            current = merged.get("publish")
            pub = dict(current) if isinstance(current, dict) else {}
            pub.update(publish)
            merged["publish"] = pub
        patch: Dict[str, Any] = {"metadata": merged, "updated_at": now, **fields}
        if charge is not None:
            # (op, micros) or (op, micros, at): a refund or correction is dated at the charge it
            # REVERSES, so a reversal written just after 00:00 UTC on the 1st lands in the same month
            # as its charge — otherwise the new month would start below zero.
            op, micros = charge[0], charge[1]
            at = charge[2] if len(charge) > 2 and charge[2] else now
            journal = merged.get("charges")
            journal = list(journal) if isinstance(journal, list) else []
            journal.append({"at": at, "op": str(op), "micros": int(micros)})
            merged["charges"] = journal
            patch["cost_micros"] = int(row.get("cost_micros") or 0) + int(micros)
        if status is not None:
            patch["status"] = status
        return patch

    async def transition_post(
        self, post_id: str, *, expect_status: Any, observed: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None, meta: Optional[Dict[str, Any]] = None,
        publish: Optional[Dict[str, Any]] = None, unset: Tuple[str, ...] = (),
        charge: Optional[Tuple[Any, ...]] = None, retries: int = 1, **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        """Move a post (or just annotate it) ONLY while it is still in `expect_status` (a status or
        a tuple of them) and unchanged since it was read: one UPDATE fenced on `status` AND the
        observed `updated_at`.

        * `meta` merges top-level keys into `metadata`; `publish` merges into `metadata.publish`;
          `unset` removes top-level keys first. Everything else in `metadata` is kept.
        * `charge=(op, micros)` appends `{at, op, micros}` to `metadata.charges` (the cost journal
          the X spend cap sums) and adds `micros` to `cost_micros` in the same write;
          `charge=(op, micros, at)` dates a reversal at the charge it reverses.
        * `fields` are columns from `_POST_WRITABLE`.

        Returns the updated row, or None when the post is gone or no longer in `expect_status`.
        A lost fence (someone wrote in between) re-reads and retries up to `retries` times —
        each retry re-derives the patch from the FRESH row. Ledger errors raise
        `MarketingRunError`."""
        # `metadata` is written ONLY through the merge (`meta` / `publish` / `charge`): a raw
        # `metadata=` field would replace the whole document — the review record, the dry-run flag
        # and the cost journal the X cap sums.
        if "metrics" in fields:
            # Its only writer is `merge_post_metrics` (fenced on `metrics->>rev`); a write from here
            # would race the measure step's merge and drop its history.
            raise ValueError("transition_post: `metrics` is written only by merge_post_metrics")
        unknown = set(fields) - (_POST_WRITABLE - {"metadata"})
        if unknown:
            raise ValueError(f"transition_post: not writable: {sorted(unknown)}")
        if status is not None and status not in POST_STATUSES:
            raise ValueError(f"unknown post status {status!r}")
        if charge is not None and "cost_micros" in fields:
            raise ValueError("transition_post: pass a charge OR cost_micros, not both")
        expected = (expect_status,) if isinstance(expect_status, str) else tuple(expect_status)
        row = observed
        for attempt in range(max(retries, 0) + 1):
            if row is None or attempt > 0:
                row = await self.get_post(post_id)
            if row is None or row.get("status") not in expected:
                return None
            patch = self._merged_post_patch(row, status=status, meta=meta, publish=publish,
                                            unset=tuple(unset), charge=charge, fields=fields)
            query = self.sb.table(POSTS).update(patch).eq("id", post_id).eq("status", row["status"])
            if row.get("updated_at"):
                query = query.eq("updated_at", _ts_filter(row["updated_at"]))
            else:
                query = query.is_("updated_at", "null")
            updated = _one(await _exec(query, op="transition_post", post_id=post_id,
                                       to=status or row.get("status")))
            if updated is not None:
                return updated
        return None

    async def spend_since(self, platform: str, since: datetime) -> int:
        """Micro-dollars charged to `platform` posts since `since` — the X spend cap's input.
        Sums the timestamped `metadata.charges` journal (not `cost_micros`, which also holds
        last month's charges). Any charge bumps the row's `updated_at`, so reading only rows
        touched since `since` loses nothing. A journal entry with an unreadable time counts
        (fail-closed: an over-count can only pause X early)."""
        res = await _exec(
            self.sb.table(POSTS).select("id,cost_micros,metadata,updated_at")
            .eq("platform", platform).gte("updated_at", _ts_filter(since)).limit(1000),
            op="spend_since", platform=platform,
        )
        return sum(charges_since(r, since) for r in (getattr(res, "data", None) or []) if isinstance(r, dict))

    async def any_post_with_meta(self, platform: str, key: str, value: str) -> bool:
        """Does any `platform` post carry `metadata.<key> = value`? (The once-a-month X cap alert
        marker survives restarts this way.)"""
        res = await _exec(
            self.sb.table(POSTS).select("id").eq("platform", platform)
            .eq(f"metadata->>{key}", value).limit(1),
            op="any_post_with_meta", platform=platform, key=key,
        )
        return bool(getattr(res, "data", None))

    async def expire_stale_posts(self, today: date, *, limit: int = 100) -> int:
        """Close `approved` / `pending_review` posts whose run day is neither today nor yesterday
        (ET) as `skipped` (`metadata.skip_reason = expired`, `metadata.expired_from` = the status it
        had) — the owner's rule: a post goes out
        on its day or the next, never as part of a backlog. Oldest first and bounded, so a large
        backlog is drained over a few ticks. `queued` rows are never expired (their outcome may
        be live on a platform — reconcile owns them). Returns how many were closed."""
        res = await _exec(
            self.sb.table(POSTS).select("*").in_("status", ["approved", "pending_review"])
            .order("created_at").limit(limit),
            op="expire_stale_posts.list",
        )
        closed = 0
        for row in getattr(res, "data", None) or []:
            if not isinstance(row, dict) or is_fresh(row, today):
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            # `expired_from`: the status it expired FROM — the digest tells "the owner never looked"
            # (pending_review) from "approved but never sent" (approved). The write is fenced on
            # exactly this status, so it is the status the post had when it closed.
            patch_meta: Dict[str, Any] = {"skip_reason": "expired", "expired_at": _now_iso(),
                                          "expired_from": row.get("status")}
            unset: Tuple[str, ...] = ()
            if row.get("status") == "approved" and meta.get("dry_run") is False:
                # A post the owner APPROVED that never went out: say so in Telegram (the publish feed
                # sends `alert_*`), never close it silently.
                pub = meta.get("publish") if isinstance(meta.get("publish"), dict) else {}
                why = pub.get("category") or row.get("last_error") or "publishing was off, dry-run, capped or not wired"
                patch_meta.update({
                    "alert_kind": "expired",
                    "alert_text": (f"⏰ {str(row.get('platform')).upper()} post expired UNPUBLISHED (run "
                                   f"{str(row.get('idempotency_key') or '')[:10]}) — it was approved but never "
                                   f"went out ({str(why)[:200]})."),
                    "alert_at": _now_iso(),
                })
                unset = ("alert_notified_at",)
            try:
                updated = await self.transition_post(
                    str(row["id"]), expect_status=("approved", "pending_review"), observed=row,
                    status="skipped", meta=patch_meta, unset=unset, retries=0,
                )
            except MarketingRunError as e:
                # One row's ledger error must not stop the rows behind it (the next tick retries).
                logger.error("marketing post expiry NOT recorded post_id=%s (%s)", row.get("id"), e)
                continue
            if updated is not None:
                closed += 1
                logger.info("marketing post EXPIRED post_id=%s platform=%s key=%s was=%s",
                            row["id"], row.get("platform"), row.get("idempotency_key"), row.get("status"))
        return closed

    async def demote_auto_approved(self, *, limit: int = 50) -> int:
        """Send every `approved_by = "auto"` post back to `pending_review` (approval cleared), so the
        review sweep shows it with buttons. While the semantic judge misses its gate a human approves
        every post; with MARKETING_AUTO_PUBLISH flipped by mistake, auto-approved text posts would
        otherwise never reach Telegram, be refused by the publisher and expire unseen."""
        res = await _exec(
            self.sb.table(POSTS).select("*").eq("status", "approved").eq("approved_by", "auto")
            .order("created_at").limit(limit),
            op="demote_auto_approved.list",
        )
        moved = 0
        for row in getattr(res, "data", None) or []:
            if not isinstance(row, dict):
                continue
            try:
                updated = await self.transition_post(
                    str(row["id"]), expect_status="approved", observed=row, status="pending_review",
                    meta={"auto_demoted_at": _now_iso()}, retries=0, approved_by=None, approved_at=None,
                )
            except MarketingRunError as e:
                logger.error("marketing post auto-demotion NOT recorded post_id=%s (%s)", row.get("id"), e)
                continue
            if updated is not None:
                moved += 1
                logger.warning("marketing post AUTO-APPROVED → back to review post_id=%s platform=%s "
                               "(human approval is required while the judge misses its gate)",
                               row.get("id"), row.get("platform"))
        return moved

    async def request_retract(self, post_id: str, *, by: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Record the owner's confirmed Retract on a `published` post — nothing else: the publisher
        loop performs the delete (rules/marketing.md §2: only the publisher calls a platform).
        Returns `requested`, `already_requested`, `already_<status>` or `not_found`."""
        for _ in range(2):
            row = await self.get_post(post_id)
            if row is None:
                return "not_found", None
            if row.get("status") != "published":
                return f"already_{row.get('status')}", row
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if meta.get("retract_requested_at") and not meta.get("retract_closed_at"):
                return "already_requested", row
            # A request that ENDED without a delete (gave up / by hand — `retract_closed_at`) may be
            # made again: the owner retrying after fixing what made the delete fail.
            now = _now_iso()
            updated = await self.transition_post(
                post_id, expect_status="published", observed=row, retries=0,
                meta={"retract_requested_at": now,
                      "retract": {"requested_at": now, "by": by, "attempts": 0, "state": "requested"}},
                unset=("retract_closed_at",),
            )
            if updated is not None:
                logger.info("marketing post RETRACT REQUESTED post_id=%s platform=%s by=%s",
                            post_id, row.get("platform"), by)
                return "requested", updated
        row = await self.get_post(post_id)
        return ("not_found", None) if row is None else ("busy", row)

    async def resolve_unknown(self, post_id: str, decision: str, *, by: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """The owner's answer to an ESCALATED unknown outcome (a post whose publish could not be
        confirmed or refuted automatically). `live` → `published` (its id may be unknown);
        `not_posted` → `failed`. There is deliberately no "retry": it is the one answer that could
        double-post. Returns the new status, `not_escalated`, `already_<status>` or `not_found`."""
        if decision not in ("live", "not_posted"):
            raise ValueError(f"unknown resolution {decision!r}")
        row = await self.get_post(post_id)
        if row is None:
            return "not_found", None
        if row.get("status") != "queued":
            return f"already_{row.get('status')}", row
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        publish = meta.get("publish") if isinstance(meta.get("publish"), dict) else {}
        if publish.get("state") != "escalated":
            return "not_escalated", row
        now = _now_iso()
        outcome = {"decision": decision, "by": by, "at": now}
        if decision == "live":
            updated = await self.transition_post(
                post_id, expect_status="queued", observed=row, status="published", retries=1,
                meta={"owner_outcome": outcome}, publish={"state": "published"},
                published_at=now, last_error="owner confirmed it is live; the platform id is unknown",
            )
        else:
            updated = await self.transition_post(
                post_id, expect_status="queued", observed=row, status="failed", retries=1,
                meta={"owner_outcome": outcome}, publish={"state": "owner_not_posted"},
                last_error="owner confirmed it was not posted",
            )
        if updated is None:
            again = await self.get_post(post_id)
            return (f"already_{(again or {}).get('status')}" if again else "not_found"), again
        logger.info("marketing post RESOLVED BY OWNER post_id=%s decision=%s by=%s", post_id, decision, by)
        return str(updated.get("status")), updated

    async def list_posts_filtered(
        self, *, status: str, order: str = "updated_at", limit: int = 20,
        not_null: Tuple[str, ...] = (), null: Tuple[str, ...] = (),
        platforms: Optional[List[str]] = None, not_platforms: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Posts in `status`, with JSON-path / column NULL filters applied IN the query (before
        the LIMIT — the starvation lesson of `list_posts`), oldest `order` first."""
        query = self.sb.table(POSTS).select("*").eq("status", status)
        for col in not_null:
            query = query.not_.is_(col, "null")
        for col in null:
            query = query.is_(col, "null")
        if platforms is not None:
            if not platforms:
                return []
            query = query.in_("platform", list(platforms))
        if not_platforms:
            query = query.not_.in_("platform", list(not_platforms))
        res = await _exec(query.order(order).limit(limit), op="list_posts_filtered", status=status)
        return [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]

    async def close_finished_runs(self, today: date, *, limit: int = 50) -> int:
        """Close `media_ready` runs dated BEFORE yesterday (ET) whose posts are all terminal:
        `published` if any post reached a platform (published or later retracted), otherwise
        `skipped`. Only outside the claim window, so it can never meet `decide_claim`; never
        `failed`; never while a post is still pending, approved or queued. One compare-and-swap
        per run on its status and updated_at.

        It also says WHY (`metadata.closed`, `close_summary`: the reason, the post counts by status
        and the skip reasons), merged into the metadata it observed — safe under the CAS, since
        every run write moves `updated_at`. `finished_at` is KEPT when the worker set it (the run's
        own wall time); it is filled only when NULL. The close time is `metadata.closed.at`."""
        res = await _exec(
            self.sb.table(RUNS).select("*").eq("status", "media_ready")
            .lt("run_date", (today - timedelta(days=1)).isoformat()).order("run_date").limit(limit),
            op="close_finished_runs.list",
        )
        closed = 0
        for run in getattr(res, "data", None) or []:
            if not isinstance(run, dict):
                continue
            posts = await _exec(self.sb.table(POSTS).select("status,metadata").eq("run_id", run["id"]),
                                op="close_finished_runs.posts", run_id=run["id"])
            rows = [p for p in (getattr(posts, "data", None) or []) if isinstance(p, dict)]
            statuses = [str(p.get("status")) for p in rows]
            if any(s in _OPEN_POST_STATUSES for s in statuses):
                continue
            final = "published" if any(s in ("published", "retracted") for s in statuses) else "skipped"
            now = _now_iso()
            summary = close_summary(rows, now=now)
            meta = dict(run["metadata"]) if isinstance(run.get("metadata"), dict) else {}
            meta["closed"] = summary
            patch: Dict[str, Any] = {"status": final, "updated_at": now, "metadata": meta}
            if not run.get("finished_at"):
                patch["finished_at"] = now
            query = self.sb.table(RUNS).update(patch).eq("id", run["id"]).eq("status", "media_ready")
            # The fence the metadata merge rests on: an observed NULL is fenced as NULL, never skipped.
            query = (query.eq("updated_at", _ts_filter(run["updated_at"])) if run.get("updated_at")
                     else query.is_("updated_at", "null"))
            if _one(await _exec(query, op="close_finished_runs.update", run_id=run["id"])) is not None:
                closed += 1
                logger.info("marketing run CLOSED run_id=%s run_date=%s status=%s reason=%s posts=%s "
                            "skip_reasons=%s", run["id"], run.get("run_date"), final, summary["reason"],
                            summary["posts"], summary["skip_reasons"])
        return closed

    # measurement + digest (2026-10-01) ------------------------------------------------------------
    # `merge_post_metrics` is the ONLY writer of `marketing_posts.metrics`; `record_reject_reason`
    # records the review bot's reason keyboard; everything else here is a bounded read for the weekly
    # digest and the run-health alert, every filter IN the query (before its LIMIT).

    async def merge_post_metrics(
        self, post_id: str, *, observed: Optional[Dict[str, Any]],
        merge: Callable[[Dict[str, Any]], Dict[str, Any]], expect_status: Any = "published",
        retries: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Write a post's `metrics` document — and nothing else: one UPDATE of the `metrics` column
        only (never `metadata`, never `updated_at`, so the publisher's `updated_at` fence on the same
        row is untouched — marketing_posts has no trigger, which tests/test_marketing_run_service.py
        pins against the schema snapshot and every migration), fenced on the status (`expect_status`, a
        status or a tuple) AND the observed `metrics->>rev` (IS NULL on the first write).

        `merge(old_metrics) -> new_metrics` is a pure function of the stored document (`{}` when it is
        missing or not an object); it gets a deep copy. The writer owns `rev`: the new document's
        `rev` is the old one + 1 (1 when there was none — `metrics_rev`). A lost fence re-reads the
        row and re-applies `merge` to the FRESH document, up to `retries` times.

        Returns the updated row, or None when the post is gone, not in `expect_status`, or the fence
        was lost every time (logged WARNING). A ledger error raises `MarketingRunError`; a `merge`
        that returns anything but a dict raises ValueError before any write."""
        expected = (expect_status,) if isinstance(expect_status, str) else tuple(expect_status)
        row = observed
        tries = max(retries, 0) + 1
        for attempt in range(tries):
            if row is None or attempt > 0:
                row = await self.get_post(post_id)
            if row is None or row.get("status") not in expected:
                return None
            old = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            fence, next_rev = metrics_rev(old)
            new = merge(copy.deepcopy(old))
            if not isinstance(new, dict):
                raise ValueError(f"merge_post_metrics: merge returned a {type(new).__name__}, not a dict "
                                 f"(post {post_id})")
            new = {**new, "rev": next_rev}
            query = self.sb.table(POSTS).update({"metrics": new}).eq("id", post_id).eq("status", row["status"])
            query = (query.is_("metrics->>rev", "null") if fence is None
                     else query.eq("metrics->>rev", fence))
            updated = _one(await _exec(query, op="merge_post_metrics", post_id=post_id, rev=next_rev))
            if updated is not None:
                return updated
        logger.warning("marketing post metrics NOT written post_id=%s: the rev fence was lost %d time(s) — "
                       "another writer kept moving it; the next measure retries", post_id, tries)
        return None

    async def list_measurable_posts(
        self, *, since: Any, platforms: Optional[List[str]] = None, limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """`published` posts with `published_at` at or after `since`, oldest published first. Every
        filter is in the query, before the LIMIT. `platforms=[]` is nothing (no read)."""
        since_at = _as_instant(since, "since")
        if limit <= 0:
            return []
        query = (self.sb.table(POSTS).select("*").eq("status", "published")
                 .gte("published_at", _ts_filter(since_at)))
        if platforms is not None:
            if not platforms:
                return []
            query = query.in_("platform", list(platforms))
        res = await _exec(query.order("published_at").limit(limit), op="list_measurable_posts",
                          since=_ts_filter(since_at))
        return [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]

    async def list_posts_created_between(self, start: Any, end: Any, *, limit: int = 500) -> List[Dict[str, Any]]:
        """Posts of any status created in [start, end) — instants, a naive one being UTC — oldest
        first. An empty or inverted window reads nothing."""
        start_at, end_at = _as_instant(start, "start"), _as_instant(end, "end")
        if limit <= 0 or start_at >= end_at:
            return []
        res = await _exec(
            self.sb.table(POSTS).select("*")
            .gte("created_at", _ts_filter(start_at)).lt("created_at", _ts_filter(end_at))
            .order("created_at").limit(limit),
            op="list_posts_created_between", start=_ts_filter(start_at), end=_ts_filter(end_at),
        )
        rows = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
        if len(rows) >= limit:
            logger.warning("marketing list_posts_created_between: %d rows hit the limit for %s..%s — the "
                           "newest may be missing", len(rows), _ts_filter(start_at), _ts_filter(end_at))
        return rows

    async def get_run_for_date(self, run_date: Any) -> Optional[Dict[str, Any]]:
        """The run of one ET day (`run_date` UNIQUE), or None — `get_run_by_date` with its argument
        checked (a `date` or an ISO date string; never a datetime)."""
        return await self.get_run_by_date(_as_date(run_date, "run_date"))

    async def list_runs_between(self, start_date: Any, end_date: Any) -> List[Dict[str, Any]]:
        """Runs dated `start_date`..`end_date` INCLUSIVE (ET days), oldest first. `run_date` is
        UNIQUE, so the window's day count bounds the read exactly; an inverted window reads nothing."""
        start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
        if start > end:
            return []
        res = await _exec(
            self.sb.table(RUNS).select("*")
            .gte("run_date", start.isoformat()).lte("run_date", end.isoformat())
            .order("run_date").limit((end - start).days + 1),
            op="list_runs_between", start=start, end=end,
        )
        return [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]

    async def spend_by_op_since(self, platform: str, since: Any) -> Dict[str, int]:
        """`spend_since` broken down by journal op ({op: micro-dollars}): the same read (rows of
        `platform` touched since `since` — any charge bumps `updated_at`) and the same entry rule
        (`charges_by_op_since`), so the values always sum to `spend_since(platform, since)`."""
        since_at = _as_instant(since, "since")
        res = await _exec(
            self.sb.table(POSTS).select("id,cost_micros,metadata,updated_at")
            .eq("platform", platform).gte("updated_at", _ts_filter(since_at)).limit(1000),
            op="spend_by_op_since", platform=platform,
        )
        out: Dict[str, int] = {}
        for r in getattr(res, "data", None) or []:
            if not isinstance(r, dict):
                continue
            for op, micros in charges_by_op_since(r, since_at).items():
                out[op] = out.get(op, 0) + micros
        return out

    async def list_charge_rows_since(self, platform: str, since: Any, *, limit: int = 500) -> List[Dict[str, Any]]:
        """`{id, metadata, updated_at}` of every `platform` post touched at or after `since` (an
        instant; a naive one is UTC), oldest touch first — the weekly cost line's charge read.
        Every journal write bumps `updated_at` in the same UPDATE (`_merged_post_patch`), and a
        reversal dated at the charge it reverses is still WRITTEN now, so every entry dated at or
        after `since` sits on a returned row. Unlike the cap's reads this one is never a partial
        sum: it asks for `limit + 1` rows and RAISES when more than `limit` come back. The probe
        must fit in ONE response: PostgREST cuts every answer at its max-rows (≈1,000 here,
        `postgrest_paging.PAGE_SIZE`) whatever `.limit()` asks, so at a limit of 1,000 the probe row
        could never arrive and a cut answer would read as complete — a limit whose probe does not
        fit is a ValueError, before any read."""
        since_at = _as_instant(since, "since")
        if type(limit) is not int or limit <= 0:
            raise ValueError(f"limit must be a positive int, not {limit!r}")
        if limit + 1 > POSTGREST_MAX_ROWS:
            raise ValueError(f"limit {limit} + 1 does not fit in one PostgREST response ({POSTGREST_MAX_ROWS} "
                             "rows) — the probe row could never arrive")
        res = await _exec(
            self.sb.table(POSTS).select("id,metadata,updated_at")
            .eq("platform", platform).gte("updated_at", _ts_filter(since_at))
            .order("updated_at").limit(limit + 1),
            op="list_charge_rows_since", platform=platform, since=_ts_filter(since_at),
        )
        rows = [r for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
        if len(rows) > limit:
            raise MarketingRunError(
                f"list_charge_rows_since: more than {limit} {platform} posts touched since "
                f"{_ts_filter(since_at)} — the cost line is never a partial sum")
        return rows

    async def link_hits_between(self, start_day: Any, end_day: Any) -> List[Dict[str, Any]]:
        """`/go` taps — `{campaign, day, hits}` rows of `marketing_link_hits` for ET days
        `start_day`..`end_day` INCLUSIVE, oldest day first. Raw values: the caller sanitises `hits`.
        An inverted window reads nothing."""
        start, end = _as_date(start_day, "start_day"), _as_date(end_day, "end_day")
        if start > end:
            return []
        res = await _exec(
            self.sb.table(LINK_HITS).select("campaign,day,hits")
            .gte("day", start.isoformat()).lte("day", end.isoformat())
            .order("day").order("campaign").limit(_LINK_HITS_LIMIT),
            op="link_hits_between", start=start, end=end,
        )
        rows = [{"campaign": r.get("campaign"), "day": r.get("day"), "hits": r.get("hits")}
                for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
        if len(rows) >= _LINK_HITS_LIMIT:
            logger.warning("marketing link_hits_between: %d rows hit the limit for %s..%s — counts may be "
                           "short", len(rows), start, end)
        return rows

    async def record_reject_reason(self, post_id: str, reason: str, *, by: str,
                                   bundle_id: Optional[str] = None) -> str:
        """The owner's reason for a rejection (the review bot's reason keyboard) — only on a
        `rejected` post. `metadata.review` is rebuilt from a FRESH read (its decision / by / at kept)
        with `reason`, `reason_at` and `reason_by`, written by the fenced, merging `transition_post`;
        two tries.

        With `bundle_id` (a bundle reason after ❌ Reject all) the FRESH row must be that bundle's
        rejection: a review record naming `bundle_id`, or NO review record at all on a row stamped with
        that bundle — `review_bundle`'s record merge is best effort, and only it (fenced on the stamp)
        and `review_post` (which writes the record in the same UPDATE) ever write `rejected`. Such a row
        gets `decision` rejected and `bundle_id` in the same write as the reason, so a later reason still
        matches it. Anything else (a ✂ drop's record, another bundle) answers `not_this_bundle`, writing
        nothing.

        Returns `recorded`, `unchanged` (that reason is already there — a double tap writes nothing),
        `already_<status>` (the post is not rejected; nothing written), `not_found`, `not_this_bundle`,
        or `busy` (the fence was lost twice). A different later reason wins. A reason outside
        REJECT_REASON_CODES raises ValueError before any read."""
        if reason not in REJECT_REASON_CODES:
            raise ValueError(f"unknown reject reason {reason!r} (expected one of {REJECT_REASON_CODES})")

        def _of_bundle(row: Dict[str, Any], meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            """The review record to build on for `bundle_id`, or None when the row is not its rejection."""
            if isinstance(meta.get("review"), dict):
                return dict(meta["review"]) if meta["review"].get("bundle_id") == bundle_id else None
            if meta.get("review") is None and (review_bundle_of(row) or {}).get("id") == bundle_id:
                return {"decision": "rejected", "bundle_id": bundle_id}
            return None

        for _ in range(2):
            row = await self.get_post(post_id)
            if row is None:
                return "not_found"
            if row.get("status") != "rejected":
                return f"already_{row.get('status')}"
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            review = dict(meta["review"]) if isinstance(meta.get("review"), dict) else {}
            if bundle_id is not None:
                owned = _of_bundle(row, meta)
                if owned is None:
                    return "not_this_bundle"
                review = owned
            was = review.get("reason")
            if was == reason:
                return "unchanged"
            review.update({"reason": reason, "reason_at": _now_iso(), "reason_by": str(by)[:64]})
            updated = await self.transition_post(post_id, expect_status="rejected", observed=row, retries=0,
                                                 meta={"review": review})
            if updated is not None:
                logger.info("marketing post REJECT REASON post_id=%s platform=%s reason=%s by=%s (was %r)",
                            post_id, row.get("platform"), reason, by, was)
                if bundle_id is not None and not isinstance(meta.get("review"), dict):
                    logger.warning("marketing post post_id=%s platform=%s: rejected by bundle %s with no review "
                                   "record (its best-effort write had failed) — rebuilt with the reason", post_id,
                                   row.get("platform"), bundle_id)
                return "recorded"
        row = await self.get_post(post_id)
        if row is None:
            return "not_found"
        if row.get("status") != "rejected":
            return f"already_{row.get('status')}"
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if bundle_id is not None and _of_bundle(row, meta) is None:
            return "not_this_bundle"
        review = meta.get("review") if isinstance(meta.get("review"), dict) else {}
        return "unchanged" if review.get("reason") == reason else "busy"


    # scripts (migration 173) --------------------------------------------------
    # Written ONLY by the web side (`script_service.py`). Every state change after the first
    # INSERT is a compare-and-swap on the observed columns, so two writers — a redeploy's
    # overlapping containers, a retried kick — can never both win.

    async def get_script(self, run_id: str) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(
                self.sb.table(SCRIPTS).select("*").eq("run_id", run_id).limit(1),
                op="get_script", run_id=run_id,
            )
        )

    async def insert_script(self, row: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """First write wins: (row, True) if ours, (existing row, False) on a 23505."""
        run_id = row["run_id"]
        try:
            created = _one(await _exec(self.sb.table(SCRIPTS).insert(row), op="insert_script",
                                       run_id=run_id))
            if created:
                return created, True
        except Exception as e:
            if not is_unique_violation(e):
                raise
        existing = await self.get_script(run_id)
        if existing is None:
            raise MarketingRunError(f"insert_script: row for run {run_id} raced away")
        return existing, False

    async def update_script_where(
        self, run_id: str, patch: Dict[str, Any], *, expect: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """UPDATE … WHERE run_id AND every `expect` column equals its value (None = IS NULL).
        Returns the new row, or None when another writer changed the row first."""
        query = self.sb.table(SCRIPTS).update({**patch, "updated_at": _now_iso()}).eq("run_id", run_id)
        for col, val in expect.items():
            query = query.is_(col, "null") if val is None else query.eq(col, val)
        return _one(await _exec(query, op="update_script", run_id=run_id,
                                expect=",".join(sorted(expect))))

    async def recent_source_refs(self, before: date, limit: int) -> List[str]:
        """`source_ref` of the days before `before`, newest first — what selection must not
        repeat. Read from `marketing_scripts` ITSELF (its `run_date` is written in the same
        first-write-wins INSERT as the selection), never from the best-effort mirror on
        `marketing_runs`: one lost mirror write used to drop that day's pick out of the window,
        and with the window at pool-1 the rotation then picked it again on the very next posting
        day. Rejected days still count (the pick was made); rest days (NULL) never do."""
        if limit <= 0:
            return []
        res = await _exec(
            self.sb.table(SCRIPTS).select("run_date,source_ref")
            .lt("run_date", before.isoformat())
            .not_.is_("source_ref", "null")
            .order("run_date", desc=True)
            .limit(limit),
            op="recent_source_refs", before=before,
        )
        rows = [r for r in (getattr(res, "data", None) or []) if r.get("source_ref")]
        rows.sort(key=lambda r: str(r.get("run_date") or ""), reverse=True)
        return [str(r["source_ref"]) for r in rows][:limit]

    async def script_tokens_between(self, start_date: Any, end_date: Any) -> List[Dict[str, Any]]:
        """`{run_date, tokens_used}` of the scripts dated `start_date`..`end_date` INCLUSIVE (ET days),
        oldest first — the weekly cost line's Gemini read. Raw values: the caller validates them. One
        script per run (PK run_id) and one run per day (UNIQUE run_date) bound the read by the day
        count; more rows than days, or two rows on one day (a hand edit — possible in a sparse window
        too), cannot be told apart from a double count, so it RAISES instead of returning a sum that
        might count one twice. An inverted window reads nothing."""
        start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
        if start > end:
            return []
        days = (end - start).days + 1
        res = await _exec(
            self.sb.table(SCRIPTS).select("run_date,tokens_used")
            .gte("run_date", start.isoformat()).lte("run_date", end.isoformat())
            .order("run_date").limit(days + 1),
            op="script_tokens_between", start=start, end=end,
        )
        rows = [{"run_date": r.get("run_date"), "tokens_used": r.get("tokens_used")}
                for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
        days_seen = [str(r["run_date"]) for r in rows]
        if len(rows) > days or len(set(days_seen)) != len(days_seen):
            raise MarketingRunError(
                f"script_tokens_between: {len(rows)} scripts for the {days} days {start}..{end} and a day "
                "holds two — the cost line refuses to sum them")
        return rows


def _is_storage_conflict(exc: BaseException) -> bool:
    """A Storage upload refused because the key already holds an object (`x-upsert: false`): storage3's
    `StorageApiError` with status 409 / error "Duplicate" (the status arrives as an int or a string)."""
    status = str(getattr(exc, "status", "") or getattr(exc, "statusCode", "") or "").strip()
    code = str(getattr(exc, "code", "") or "").lower()
    return status == "409" or code in ("duplicate", "409") or "'statuscode': 409" in str(exc).lower()


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
