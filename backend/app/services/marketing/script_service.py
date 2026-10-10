"""
The day's script: select → generate → accept, driven by the worker's kick-and-poll
(SYSTEM_DESIGN_GUIDELINES §12.5).

The media worker cannot write copy (it holds no Gemini key and imports nothing from `app.*`), so
it asks the web side for the day's script with ONE idempotent call, repeated until it gets a
final answer: `POST /api/v1/internal/marketing/runs/{run_id}/script` → `kick(run_id)`.

    first kick ──▶ the run must be HELD (in_progress, today/yesterday ET, a live claim) —
                   nothing starts writer spend for an arbitrary run id
                   selection (pure: `selection.choose`, recent picks skipped)
                   one INSERT into marketing_scripts   ← first write wins (PK run_id)
                   rest day? ──▶ "rest_day" (final)
    any kick   ──▶ status "selected" (or "generating" whose lease expired)
                   → a cap reached? close it `rejected` with a CAS, whatever the status
                   → else spawn ONE background generation, answer "generating" at once
    generation ──▶ acquire the lease: conditional UPDATE on the observed (status, generations,
                   generation_id, lease_until); refresh it before every Gemini call; write the
                   outcome FENCED on generation_id. Only one generation can ever own a run.
                   accepted → immutable · content failure → next generation, or `rejected`
                   (reason `content`) at MAX_GENERATIONS · a generation that ends WITHOUT a
                   content verdict (Gemini failure, ledger blip, crash, cancellation, an owner
                   that died) → back to "selected", or `rejected` (reason
                   `writer_unavailable`) at MAX_WRITER_FAILURES

Company Weekly (drop 2, contract D11): the first kick reads the day's series calendar
(`selection.plan_for`) narrowed to MARKETING_CONTENT_CLASSES and the shipped series. A chain of just
the lesson is the selection above, byte for byte while the classes are "A". Any other chain is a
TEMPLATE BUILD, run as a background task the kick waits on for at most TEMPLATE_KICK_WAIT_SECONDS
(then `generating`; the next kick joins the same task): each series of the chain asks the company-news
adapter for candidates, each candidate is composed by `news_templates` from its JSON round trip,
re-checked as it will be stored, given its logos and its frozen formats, and the first that passes is
inserted `accepted` — no writer, no lease, 0 tokens, first write wins. A series that yields nothing
falls through to the next (recorded in `fact_sheet.selection.trail`); the chain always ends in the
lesson.

Why not a long request: a 20–90 s Gemini call inside the worker's 30 s HTTP timeout was retried
up to three times CONCURRENTLY, and uvicorn's 30 s graceful shutdown cut it on every deploy. A
short poll touches neither. Why a lease and not just the in-process task set: Railway runs the
old and new containers side by side during a deploy, so two processes can see the same row;
only the database can arbitrate that.

Why two caps: a Gemini outage is not a content verdict. Counting it against the content cap
reported a quota exhaustion as `content_rejected` and sent operators hunting a validator hole,
and burned the day's content attempts on calls that never produced a draft. Failures are
counted as `generations - content_rejections`, so an owner that died without writing anything
is counted too (there is no row to write when a container is SIGKILLed); bounded on its own
because a timeout still bills and a crash-looping container must not buy a generation per tick.

Everything here is logged with run_id / generation_id / source_ref, and every failure ends in a
row state the next kick understands — including a generation that dies at a cap, which the next
kick closes (the lease expiry is a real backstop at every count) — nothing is swallowed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

from app.config import settings
from app.schemas.marketing import (
    CONTENT_CLASSES,
    POST_FORMATS_BY_PLATFORM,
    SCRIPT_REJECT_REASONS,
    TEMPLATE_AUTHORSHIP,
    VIDEO_LAYOUT_PER_LINE,
    WORKER_CAPABILITY_LAYOUTS_2B,
    WORKER_CAPABILITY_NEWS_TEMPLATES,
    WORKER_CAPABILITY_POST_IMAGE,
    WORKER_LAYOUTS_2B,
    image_post_problem,
    normalize_image_post,
    parse_content_classes,
)
from app.services.marketing import content_pool, post_copy, selection, smart_link
# Company Weekly (drop 2): the pure halves of the template day — records + fact sheet, the templates,
# the on-screen allow-list and the logo header check. FMP-free (PURE_MODULES). The ONE FMP door, the
# adapter, is imported lazily inside `MarketingScriptService._news_source_fn` and nowhere else.
from app.services.marketing import company_news_rules as news_rules
from app.services.marketing import logo_check, news_templates, template_onscreen
from app.services.marketing.generation_budget import (
    LEDGER_STATEMENT_SECONDS,
    MODEL_CALLS_PER_GENERATION,
)
from app.services.marketing.run_service import SERIES_TRAIL_MAX as RUN_SERIES_TRAIL_MAX
from app.services.marketing.run_service import (
    CallerClaim,
    MarketingRunError,
    MarketingRunNotFound,
    MarketingRunNotHeld,
    MarketingRunService,
    claim_problem,
    frozen_post_formats,
    get_marketing_run_service,
    held_problem,
    run_date_et,
    run_worker_capabilities,
)

logger = logging.getLogger(__name__)

#: CONTENT generations (each at most MODEL_CALLS_PER_GENERATION model calls: draft, judge,
#: one repair, judge) the validators may reject before the run is `rejected` for the day with
#: reason `content`.
MAX_GENERATIONS = 4
#: Generations that may end WITHOUT a content verdict (Gemini failure, ledger blip, crash,
#: cancellation, an owner that died) before the run is `rejected` with reason
#: `writer_unavailable`. Counted as `generations - content_rejections`. Its own bound, NOT a
#: free pass: a timeout still bills, and a retired model or a crash-looping container would
#: otherwise buy a generation every tick. Together the caps bound a run at
#: MAX_GENERATIONS + MAX_WRITER_FAILURES - 1 generations of at most MODEL_CALLS_PER_GENERATION
#: model calls each — MAX_MODEL_CALLS_PER_RUN.
MAX_WRITER_FAILURES = 4
#: (4 + 4 - 1) × 4 = 28 model calls per run at most.
MAX_MODEL_CALLS_PER_RUN = (MAX_GENERATIONS + MAX_WRITER_FAILURES - 1) * MODEL_CALLS_PER_GENERATION

#: `GeminiClient.generate_json`'s generic-error budget — its decorator is
#: `@async_retry(max_attempts=2, delay=2.0)`, which is code, not settings, so it is mirrored
#: here; `tests/test_marketing_server_lease.py` reads the decorator from gemini.py and drives the
#: real `async_retry` to prove `worst_case_model_call_seconds` still bounds it.
_GENERATE_JSON_MAX_ATTEMPTS = 2
_GENERATE_JSON_DELAY_SECONDS = 2.0
#: Slack after the slowest possible model call: validation, the terminal write and its retries.
LEASE_MARGIN_SECONDS = 60


def worst_case_model_call_seconds() -> float:
    """The longest ONE `generate_json` call can take under `async_retry`: every budget
    exhausted (overload and quota: GEMINI_QUOTA_MAX_RETRIES retries each; timeout:
    GEMINI_TIMEOUT_MAX_RETRIES; generic: max_attempts-1), every attempt running to the full
    GEMINI_REQUEST_TIMEOUT_SECONDS, plus every linear backoff. The old comment said "~200 s"
    and counted only the timeout and generic budgets; the real figure is 572 s at defaults."""
    per_attempt = float(settings.GEMINI_REQUEST_TIMEOUT_SECONDS)
    quota = max(int(settings.GEMINI_QUOTA_MAX_RETRIES), 0)
    timeouts = max(int(settings.GEMINI_TIMEOUT_MAX_RETRIES), 0)
    step = float(settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS)
    generic = max(_GENERATE_JSON_MAX_ATTEMPTS - 1, 0)
    attempts = 1 + quota + quota + timeouts + generic
    backoff = (
        2 * step * quota * (quota + 1) / 2          # overload + quota: step*1 + … + step*q, each
        + step * timeouts * (timeouts + 1) / 2
        + _GENERATE_JSON_DELAY_SECONDS * generic * (generic + 1) / 2
    )
    return attempts * per_attempt + backoff


#: How long a generation owns the run between refreshes. The lease is refreshed before each
#: model call, so it must outlive the slowest single call: if it lapsed mid-call, a second
#: container (a deploy overlap) could take the run over and pay for a parallel generation. A
#: crashed owner blocks the run for at most this long — well inside the worker's 15-min poll
#: budget (tests/test_marketing_worker.py pins that).
LEASE_SECONDS = int(math.ceil(worst_case_model_call_seconds() + LEASE_MARGIN_SECONDS))

#: Attempts of a lease refresh (`_refresh_lease`) and of a terminal write (`_finish_attempts`),
#: and the linear back-off slept after each failed attempt but the last (refresh 0.5 s, 1 s;
#: terminal 1 s, 2 s). The loops use exactly these, and so does the worst-case arithmetic below.
_REFRESH_ATTEMPTS = 3
_FINISH_ATTEMPTS = 3
_REFRESH_BACKOFF_SECONDS = 0.5
_FINISH_BACKOFF_SECONDS = 1.0
#: Statements a generation's task issues outside the model calls, refreshes and terminal write:
#: `_acquire` reads the row and takes it with one conditional UPDATE; `_run_date_of` reads the run
#: when the script row carries no run_date; `_holder_renders_images` reads the run once more on the
#: ACCEPTED path while MARKETING_IMAGE_POSTS is on (drop 1) — after the last model call and before the
#: terminal write, so it counts on every generation's worst case (it was missing: 4577 s against a
#: real 4697 s, past OWNER_ALIVE_SECONDS' 4637 s).
_ACQUIRE_STATEMENTS = 2
_RUN_DATE_STATEMENTS = 1
_HOLDER_STATEMENTS = 1


def _linear_backoff_total(attempts: int, step: float) -> float:
    """Sleep of a loop that backs off `step * (attempt + 1)` after every attempt but the last."""
    n = max(int(attempts) - 1, 0)
    return float(step) * n * (n + 1) / 2


def worst_case_refresh_seconds() -> float:
    """The longest ONE `_refresh_lease` can take: every attempt runs its UPDATE to the PostgREST
    timeout (raising, or landing too late), plus every back-off. 361.5 s at defaults."""
    return (_REFRESH_ATTEMPTS * LEDGER_STATEMENT_SECONDS
            + _linear_backoff_total(_REFRESH_ATTEMPTS, _REFRESH_BACKOFF_SECONDS))


def worst_case_terminal_write_seconds() -> float:
    """The longest ONE terminal write (`_finish`) can take: every attempt to the PostgREST
    timeout, every back-off, and the one `_landed` re-read after a retry matched nothing
    (attempts 1-2 raise, attempt 3 matches nothing → re-read). 483 s at defaults."""
    return ((_FINISH_ATTEMPTS + 1) * LEDGER_STATEMENT_SECONDS
            + _linear_backoff_total(_FINISH_ATTEMPTS, _FINISH_BACKOFF_SECONDS))


def worst_case_generation_seconds() -> float:
    """The longest ONE generation task (`_generate`) can live: acquire (read + conditional
    UPDATE) + the run-date read + MODEL_CALLS_PER_GENERATION × (one lease refresh + one model
    call, each at its worst) + the holder-capability read of an accepted package (image posts on)
    + one terminal write at its worst. 4697 s at defaults with four model calls —
    (2 + 1 + 1) × 120 + 4 × (572 + 361.5) + 483 (it was ~2710 s with two calls, which
    OWNER_ALIVE_SECONDS = 3 × the lease = 1896 s did not cover). Every await in it is bounded by one
    of those terms; the cancel paths share the 5-s shutdown budget. A crash after a WRITTEN terminal
    write sends one more fenced write, but by then the row is final and the owner's age decides
    nothing."""
    return (
        (_ACQUIRE_STATEMENTS + _RUN_DATE_STATEMENTS + _HOLDER_STATEMENTS) * LEDGER_STATEMENT_SECONDS
        + MODEL_CALLS_PER_GENERATION * (worst_case_model_call_seconds() + worst_case_refresh_seconds())
        + worst_case_terminal_write_seconds()
    )


#: How long a generation task in THIS process counts as its run's live owner even after the
#: lease lapsed (`_owner_state`): the whole worst-case generation (above) plus the lease margin.
#: It used to be 3 × LEASE_SECONDS (1896 s), shorter than a real generation's worst case, so at a
#: cap a slow but LIVE owner was declared wedged, the day closed `writer_unavailable` under it, and
#: its paid, possibly publishable package was SUPERSEDED. Every await in a generation is bounded
#: (Gemini per attempt, PostgREST per statement, the hand-back), so a task older than this is
#: presumed wedged. What that CHANGES is only at a cap: the day is closed under it (its late
#: write is fenced out) instead of waiting on it. Below the caps there is nothing to take over —
#: the task is still this process's owner and `_spawn` will not start a second one (that would be
#: a parallel paid generation) — so the kick waits for it to end, or for the process to restart.
#: LEASE_SECONDS stays per model call: it is the takeover window ACROSS processes, refreshed
#: before every call; this is the in-process verdict only.
OWNER_ALIVE_SECONDS = int(math.ceil(worst_case_generation_seconds() + LEASE_MARGIN_SECONDS))
#: Back-off after a generation that ended without a verdict. Longer than one worker poll
#: session, shorter than the hourly cron period, so the next tick retries.
RETRY_AFTER_GEMINI_FAILURE = timedelta(minutes=30)
#: The cancellation hand-back must finish inside the lifespan's 5 s shutdown budget.
HAND_BACK_TIMEOUT_SECONDS = 3.0
#: How many recent picks selection must not repeat.
RECENT_LIMIT = selection.RECENT_WINDOW

# ── Company Weekly (drop 2, contract D11): the template day ──────────────────────────────────────
#: How long ONE kick waits for a running template build before answering `generating` (the worker
#: polls again). Pinned < marketing/main.py BACKEND_TIMEOUT_SECONDS − 5 by an AST read: a kick that
#: outlived the worker's HTTP timeout would be retried while still running.
TEMPLATE_KICK_WAIT_SECONDS = 20.0
#: The whole build — every series of the day's chain, its candidates, their logos — from its start.
#: Pinned < marketing/main.py SCRIPT_POLL_BUDGET_SECONDS: the worker polls at least this long.
TEMPLATE_BUILD_BUDGET_SECONDS = 240.0
#: Per-series caps inside the build budget, each above the adapter's measured worst case for one
#: `candidates()` call (the FMP client's own 5xx retries included). A series is started with
#: min(its cap, what is left) and waited for 2 s past it; a later series of the chain gets what the
#: earlier ones left. A series added here ships in the same change (it is in selection.SHIPPED_SERIES).
#:   thirteen_f      150 s — a live registry build (Berkshire) is the slow path (contract D11).
#:   congress_count   60 s — ~10 s normally (2 chambers × ~8 pages, the confirming re-read, 1 profile
#:                           batch); ~30 s worst (the 7500-row re-read and its confirm). On a
#:                           Congress Tuesday in the 13F season it runs FIRST and still leaves
#:                           thirteen_f its whole 150 s (pinned by a test).
#:   company_stakes   45 s — ~3-5 s (club group + rows, one stakes read, 1 profile batch).
#:   earnings         45 s — ~5-10 s (7 one-day calendar reads, 4 at a time, 1 profile batch); ~20 s worst.
#:   theme_explainer  45 s — ~10-25 s (≤ 2 themes × 1 profile batch + ≤ 12 breakdowns); ~40 s worst.
SERIES_BUDGET_SECONDS: Dict[str, float] = {
    "thirteen_f": 150.0,
    "congress_count": 60.0,
    "company_stakes": 45.0,
    "earnings": 45.0,
    "theme_explainer": 45.0,
}
DEFAULT_SERIES_BUDGET_SECONDS = 45.0
#: How long past its cap a series' `candidates()` is waited for (the adapter raises at its own
#: deadline; this only bounds a hung one).
SERIES_WAIT_SLACK_SECONDS = 2.0
#: A series is not started with less than this left of the build budget (it falls through: "budget").
SERIES_MIN_START_SECONDS = 5.0
#: How many ranked records one series offers (the candidate loop tries them in order).
MAX_CANDIDATES_PER_SERIES = 5
#: One logo fetch (inside what is left of the build budget).
LOGO_FETCH_TIMEOUT_SECONDS = 8.0
#: Logo fetches in flight at once.
LOGO_FETCH_CONCURRENCY = 4
#: The ledger read a news day makes: every posting day of the recent window (news refs included, so the
#: adapter's ledger check sees them); the lesson rotation filters it with `selection.lesson_refs`.
NEWS_RECENT_LIMIT = RECENT_LIMIT * len(selection.POST_WEEKDAYS)
#: A series' outcome in the day's fallback trail (`fact_sheet.selection.trail`, run `series_trail`).
SERIES_OUTCOMES = ("chosen", "no_candidates", "all_recent", "all_refused", "unavailable", "timeout",
                   "error", "budget")
#: How many trail entries reach the run mirror and each post (`series_trail`) — run_service's bound.
SERIES_TRAIL_MAX = RUN_SERIES_TRAIL_MAX
#: The series whose post image is a drop-2b layout (WORKER_LAYOUTS_2B: company_stakes `pair`,
#: theme_explainer `grid`), derived from the templates' own specs. Only a holder that declared
#: WORKER_CAPABILITY_LAYOUTS_2B draws them: `_select` drops them from the chain for any other holder.
LAYOUTS_2B_SERIES: FrozenSet[str] = frozenset(
    sid for sid, spec in news_templates.SERIES_SPECS.items() if spec.layout in WORKER_LAYOUTS_2B)

# Body states the worker branches on — part of the wire contract with backend/marketing/main.py.
REST_DAY = "rest_day"
GENERATING = "generating"
DEFERRED = "deferred"
ACCEPTED = "accepted"
REJECTED = "rejected"
SELECTED = "selected"  # row state only; never a body state

# `reason` of a rejected body (SCRIPT_REJECT_REASONS; the worker maps it to skip_reason).
REASON_CONTENT = "content"
REASON_WRITER_UNAVAILABLE = "writer_unavailable"
REASON_EMPTY_POOL = "empty_pool"
REASON_SOURCE_INELIGIBLE = "source_ineligible"

# `_finish` outcomes. Only WRITTEN may be logged as the outcome of a generation.
WRITTEN = "written"
SUPERSEDED = "superseded"
LOST = "lost"

# `_owner_state` verdicts.
_OWNER_NONE = "none"
_OWNER_ALIVE = "alive"
_OWNER_WEDGED = "wedged"

#: `writer_service.TOKENS_ATTR` (the pre-agreed contract: a re-raised model exception carries
#: the tokens its generation already spent). Not imported: writer_service loads the agents
#: package, and through it the FMP client, at import time.
_TOKENS_ATTR = "marketing_tokens_used"


class LeaseLost(Exception):
    """Another generation took the run over (our lease expired). Stop without writing."""


class _TerminalWrite:
    """ONE generation's terminal write while `_finish` runs it (never the hand-back): the patch,
    the current attempt's statement future — in flight, or answered — and whether an attempt
    raised. Read by `_generate_as`'s cancel handler, so a shutdown settles THIS write instead of
    racing it with a blind hand-back."""

    __slots__ = ("patch", "what", "fut", "raised")

    def __init__(self, patch: Dict[str, Any], what: str) -> None:
        self.patch = patch
        self.what = what
        self.fut: Optional["asyncio.Future"] = None
        self.raised = False


#: `_await_terminal`: the statement did not answer inside the shutdown budget.
_STILL_IN_FLIGHT = object()


def _unrecorded_level(slot: _TerminalWrite) -> int:
    """A terminal write a shutdown may have left unrecorded: ERROR when it carried an ACCEPTED
    package (paid, compliant — the normal path's "NOT recorded" line is ERROR too), else WARNING."""
    return logging.ERROR if slot.patch.get("status") == ACCEPTED else logging.WARNING


def _unrecorded_note(slot: _TerminalWrite) -> str:
    return (" — an ACCEPTED package: if it was not recorded, a later generation re-bills it"
            if slot.patch.get("status") == ACCEPTED else "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_et() -> date:
    return run_date_et(_now())


def _mono() -> float:
    """The template build's clock (the adapter's `deadline` is on `time.monotonic`). Indirected so a
    test can move it without touching the event loop's own clock."""
    return time.monotonic()


def _iso(dt: datetime) -> str:
    """UTC, microseconds, `Z` — never `+00:00`: these values also travel as PostgREST FILTER
    values (the takeover CAS fences on `lease_until`), where a `+` decodes to a space and the
    request 503s (the notifications-cursor incident)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ts_key(value: Any) -> Optional[str]:
    """An observed timestamp, re-rendered for use as an equality FILTER: the same instant
    (Postgres compares timestamptz by value, to the microsecond), in the `Z` form."""
    if value is None:
        return None
    dt = _parse(value)
    return _iso(dt) if dt is not None else str(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _failures(row: Dict[str, Any]) -> int:
    """Generations that ended without a content verdict — including one still in flight on a
    live row, and one whose owner died without writing anything."""
    return max(_int(row.get("generations")) - _int(row.get("content_rejections")), 0)


def _cap_verdict(row: Dict[str, Any]) -> Optional[str]:
    """The reject reason a cap forces on this row, or None while it may still generate."""
    if _int(row.get("content_rejections")) >= MAX_GENERATIONS:
        return REASON_CONTENT
    if _failures(row) >= MAX_WRITER_FAILURES:
        return REASON_WRITER_UNAVAILABLE
    return None


def _spent(exc: BaseException) -> int:
    try:
        return max(int(getattr(exc, _TOKENS_ATTR, 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def worker_script(output: Dict[str, Any]) -> Dict[str, Any]:
    """The subset the WORKER needs to voice and render (Phases 3-4). Captions are not in it:
    they are server-authored and reach the ledger only through `create_posts`.

    Drop 1 adds the run's frozen `post_formats`, its `image_post` and the image's `image_footer` —
    all three None for an output accepted before image posts existed (the worker then falls back to
    its own POST_FORMAT, exactly as before). Frozen formats that do not read back (a hand-edited row)
    are sent as None too, logged: `create_posts` refuses every post of such a run, so the day fails
    loudly there rather than on a guess here."""
    try:
        formats = frozen_post_formats(output)
    except ValueError as e:
        logger.error("marketing script: the accepted output's post_formats do not read back (%s) — "
                     "the worker gets none; create_posts will refuse this run's posts", e)
        formats = None
    footer = output.get("image_footer")
    return {
        "hook": output.get("hook") or "",
        "video_script": list(output.get("video_script") or []),
        "cards": list(output.get("cards") or []),
        "carousel_slides": list(output.get("carousel_slides") or []),
        "disclaimer_card": output.get("disclaimer_card") or "",
        "outlets": sorted((output.get("posts") or {}).keys()),
        "post_formats": formats,
        "image_post": normalize_image_post(output.get("image_post")) if formats is not None else None,
        "image_footer": footer if formats is not None and isinstance(footer, str) and footer else None,
        **_worker_template_fields(output),
    }


#: The keys of an `output.logos` entry the worker reads (`marketing/logos.py`), and nothing else.
_WORKER_LOGO_KEYS = ("key", "name", "url", "sha256", "bytes", "width", "height")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


def _worker_logos(raw: Any) -> List[Dict[str, Any]]:
    """The well-formed entries of `output.logos` (contract D10), cut to the keys the worker reads: a
    1..16-character key (first entry of a key wins), a drawable company name, a url that is None or an
    https string, a sha256 that is None or 64 lowercase hex, and None-or-int sizes — at most
    template_onscreen.MAX_LOGOS. A malformed entry is dropped (logged): its key then has no logo entry,
    and the worker refuses to draw a spec that references it rather than guess a name."""
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for i, entry in enumerate(raw if isinstance(raw, list) else []):
        key = entry.get("key") if isinstance(entry, dict) else None
        name = entry.get("name") if isinstance(entry, dict) else None
        url = entry.get("url") if isinstance(entry, dict) else None
        sha = entry.get("sha256") if isinstance(entry, dict) else None
        sizes = [entry.get(k) for k in ("bytes", "width", "height")] if isinstance(entry, dict) else [None]
        ok = (isinstance(key, str) and 1 <= len(key) <= template_onscreen.LOGO_KEY_MAX_CHARS and key not in seen
              and template_onscreen.drawable_problem(name) is None
              and (url is None or (isinstance(url, str) and url.startswith("https://") and len(url) <= 2048))
              and (sha is None or (isinstance(sha, str) and _SHA256_HEX_RE.fullmatch(sha) is not None))
              and all(v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0) for v in sizes))
        if not ok:
            logger.error("marketing script: output.logos[%d] is malformed — not sent to the worker", i)
            continue
        seen.add(key)
        out.append({k: entry.get(k) for k in _WORKER_LOGO_KEYS})
        if len(out) >= template_onscreen.MAX_LOGOS:
            break
    return out


def _worker_template_fields(output: Dict[str, Any]) -> Dict[str, Any]:
    """Drop 2's worker fields (`WorkerScript`): the script's class and authorship (defaults "A" / "ai" —
    a lesson, exactly as before), its series, the per-line video layout, and — each only when its
    validator passes against the run's own logos — the opening card and the post image's `image_spec`,
    plus the well-formed logos. Anything that does not validate is sent as None and logged ERROR: the
    worker then fails loudly on a template day (it never draws a template as a lesson), and the server's
    on-screen checks refuse anything else."""
    klass = output.get("content_class", "A")
    if klass not in CONTENT_CLASSES:
        logger.error("marketing script: the accepted output's content_class %r is unknown — sent as None",
                     str(klass)[:20])
        klass = None
    authorship = output.get("authorship", post_copy.AUTHORSHIP_AI)
    if authorship not in post_copy.AUTHORSHIPS:
        logger.error("marketing script: the accepted output's authorship %r is unknown — sent as None",
                     str(authorship)[:20])
        authorship = None
    series = output.get("series")
    if series is not None and (not isinstance(series, str) or series not in selection.SERIES_BY_ID):
        logger.error("marketing script: the accepted output's series %r is unknown — sent as None", str(series)[:40])
        series = None
    layout = output.get("video_layout")
    if layout is not None and layout != VIDEO_LAYOUT_PER_LINE:
        logger.error("marketing script: the accepted output's video_layout %r is unknown — sent as None",
                     str(layout)[:40])
        layout = None
    logos = _worker_logos(output.get("logos"))
    keys = template_onscreen.logo_keys(logos)
    opening = output.get("opening_card")
    if opening is not None:
        problem = template_onscreen.validate_opening_card(opening, keys)
        if problem is not None:
            logger.error("marketing script: the accepted output's opening_card does not validate (%s) — sent "
                         "as None", str(problem)[:200])
            opening = None
    spec = output.get("image_spec")
    if spec is not None:
        footer = output.get("image_footer")
        problem = template_onscreen.validate_image_spec(spec, keys, footer=footer if isinstance(footer, str) else None)
        if problem is not None:
            logger.error("marketing script: the accepted output's image_spec does not validate (%s) — sent as "
                         "None", str(problem)[:200])
            spec = None
    return {
        "content_class": klass,
        "authorship": authorship,
        "series": series,
        "video_layout": layout,
        "opening_card": copy.deepcopy(opening) if opening is not None else None,
        "image_spec": copy.deepcopy(spec) if spec is not None else None,
        "logos": logos,
    }


#: A Threads IMAGE post is frozen only while its composed caption fits this many UTF-8 BYTES. Upload-Post
#: splits Threads text over 500 bytes into a thread by default; `threads_long_text_as_post` (sent by
#: `outlet_upload_post`) stops that on /upload_text, but is unverified on /upload_photos — and a split
#: image post would carry the value line, the /go link and the caption disclaimer in a reply. Characters
#: are not bytes: the live value line's em dash alone makes a 500-character caption 502+ bytes. A
#: longer caption keeps Threads on the text route (one post). Drop it once Upload-Post's OpenAPI confirms
#: the flag on the photo route.
THREADS_IMAGE_CAPTION_MAX_BYTES = 500


def freeze_post_formats(output: Dict[str, Any], run_date: date, *, image_posts: bool, x_images: bool,
                        run_id: Optional[str] = None) -> Dict[str, Any]:
    """The accepted output as it is stored (drop 1, contract C3): a COPY of the writer's package with
    the run's formats frozen into it, decided ONCE at write time like the store state — the worker
    learns them only from the script read-back, and `create_posts` records each platform only in its
    frozen format. Pure but for logging; never raises on a malformed `image_post`.

    * `post_formats`: for every platform the package carries copy for — the video platforms
      "video"; the others "image" iff `image_posts` and (not X, or `x_images`) and the package has a
      usable `image_post` (and, for Threads, its caption fits THREADS_IMAGE_CAPTION_MAX_BYTES); else
      "text".
    * `image_post`: the writer's `{title, paragraphs}` reduced to exactly those keys, or None when
      it is absent or unusable (logged; that day's posts are text — never a rejected package).
    * `image_footer`: `post_copy.image_footer(run_date)`, only when some format is "image".

    Drop 2 (contract D11): a TEMPLATE output (`authorship == "template"`) keeps its OWN `image_footer`
    — composed with the record's source and as-of date, and compared by `news_templates.revalidate` at
    create_posts, so it stays whatever the formats are (a missing one is a ValueError, never replaced)
    — and a platform is "image" only when its `image_spec` also validates against the output's logos
    and that footer (`template_onscreen.validate_image_spec`); otherwise that day's posts are text."""
    out = dict(output)
    template = output.get("authorship") == TEMPLATE_AUTHORSHIP
    template_footer = output.get("image_footer")
    if template and (not isinstance(template_footer, str) or not template_footer.strip()):
        raise ValueError("a template output carries no image_footer (it is composed with the record's "
                         "source and as-of date, never replaced)")
    raw = output.get("image_post")
    image_post = normalize_image_post(raw)
    if raw is not None and image_post is None:
        logger.warning("marketing script: the accepted package's image_post is unusable run_id=%s (%s) — "
                       "stored as None; that day's image formats fall back to text", run_id,
                       image_post_problem(raw))
    out["image_post"] = image_post
    image_ok = image_post is not None
    if template and image_ok:
        problem = template_onscreen.validate_image_spec(
            output.get("image_spec"), template_onscreen.logo_keys(output.get("logos")), footer=template_footer)
        if problem is not None:
            logger.warning("marketing script: the template's image_spec does not validate run_id=%s (%s) — "
                           "that day's image formats fall back to text", run_id, str(problem)[:200])
            image_ok = False
    posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
    formats: Dict[str, str] = {}
    for platform in sorted(posts):
        allowed = POST_FORMATS_BY_PLATFORM.get(platform, ())
        if "video" in allowed:
            formats[platform] = "video"
        elif ("image" in allowed and image_posts and image_ok
              and (platform != "x" or x_images)):
            caption = posts[platform].get("caption") if isinstance(posts[platform], dict) else None
            size = len(caption.encode("utf-8")) if isinstance(caption, str) else 0
            if platform == "threads" and size > THREADS_IMAGE_CAPTION_MAX_BYTES:
                logger.info("marketing script: the Threads caption is %d UTF-8 bytes > %d run_id=%s — Threads "
                            "is frozen as TEXT (Upload-Post splits a longer post into a thread unless the "
                            "no-split flag works, and that flag is verified only on its text route)",
                            size, THREADS_IMAGE_CAPTION_MAX_BYTES, run_id)
                formats[platform] = "text"
            else:
                formats[platform] = "image"
        elif "text" in allowed:
            formats[platform] = "text"
        else:
            # create_posts refuses a platform outside the format map anyway; say so once, here.
            logger.warning("marketing script: the accepted package carries copy for %r, which has no "
                           "recordable format run_id=%s — no format frozen for it", platform, run_id)
    out["post_formats"] = formats
    if template:
        out["image_footer"] = template_footer
    elif "image" in formats.values():
        out["image_footer"] = post_copy.image_footer(run_date)
    else:
        out.pop("image_footer", None)
    return out


def _fact_sheet_snapshot(item: content_pool.ContentItem) -> Dict[str, Any]:
    """What the writer was shown. Written at selection, and REWRITTEN with the accepted package
    — each generation grounds against the LIVE bundle (a deploy may have cleaned a sentence
    since), so the audit copy must be the one the accepted output was actually checked against."""
    return {
        "key": item.key, "kind": item.kind, "title": item.title, "category": item.category,
        "sentences": list(item.fact_sentences), "word_count": item.word_count,
    }


def _trail_steps(row: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """The fallback trail a selection recorded in the script's fact sheet (`fact_sheet.selection.trail`,
    drop 2) as flat `{series, outcome, reason?}` string entries, or None when the script carries no
    selection block (a lesson-only plan, or a row written before drop 2)."""
    sheet = row.get("fact_sheet") if isinstance(row.get("fact_sheet"), dict) else {}
    block = sheet.get("selection")
    if not isinstance(block, dict):
        return None
    steps: List[Dict[str, str]] = []
    for entry in block.get("trail") if isinstance(block.get("trail"), list) else []:
        if not isinstance(entry, dict):
            continue
        kept = {k: str(entry[k])[:60] for k in ("series", "outcome", "reason")
                if isinstance(entry.get(k), str) and entry[k]}
        if "series" in kept and "outcome" in kept:
            steps.append(kept)
    return steps


def _selection_mirror(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The server-owned run metadata a selection mirrors (drop 2): `series` — the series the day took
    (the trail's "chosen" step; the template id when the trail does not say) — and `series_trail`, the
    first SERIES_TRAIL_MAX steps. None when the script recorded no selection block."""
    steps = _trail_steps(row)
    if steps is None:
        return None
    chosen = next((s["series"] for s in reversed(steps) if s.get("outcome") == "chosen"), None)
    if chosen is None:
        tid = row.get("template_id")
        chosen = tid if isinstance(tid, str) and tid in selection.SERIES_BY_ID else selection.LESSON
    return {"series": chosen, "series_trail": steps[:SERIES_TRAIL_MAX]}


def _trail_text(trail: List[Dict[str, str]]) -> str:
    """A trail for one log line: `ceo_buys:no_candidates(ceo_none_qualified) > insider_buys:unavailable(…)`."""
    return " > ".join(f"{t.get('series')}:{t.get('outcome')}" + (f"({t['reason']})" if t.get("reason") else "")
                      for t in trail) or "-"


class MarketingScriptService:
    def __init__(self, runs: Optional[MarketingRunService] = None, *, writer=None, news=None) -> None:
        self._runs = runs
        # Injected in tests; the real one is imported lazily because it loads the agents
        # package (and, through it, the FMP client) — keep that out of module import.
        self._writer = writer
        # Drop 2: the company-news source (`candidates`, `fetch_logo`, `MarketingNewsUnavailable`) —
        # the adapter module, imported lazily by `_news_source_fn` (the ONE FMP door); a fake in tests.
        self._news = news
        # run_id → its running template build (`_await_template_build`): a later kick JOINS it instead
        # of starting a second build. Removed when the task ends.
        self._builds: Dict[str, asyncio.Task] = {}
        self._tasks: Set[asyncio.Task] = set()
        self._running: Set[str] = set()
        # run_id → when its task was spawned (`_owner_state`), and generation_id → the last
        # lease_until that generation actually WROTE (`_refresh_lease` judges a failed refresh
        # against it). Both live only as long as the task.
        self._spawned_at: Dict[str, datetime] = {}
        self._leases: Dict[str, datetime] = {}
        # generation_id → its terminal write while `_finish` runs it (`_TerminalWrite`); run_ids
        # whose over-age owner was already reported below the caps (`_owner_state`). Both live
        # only as long as the task.
        self._terminal: Dict[str, _TerminalWrite] = {}
        self._wedge_reported: Set[str] = set()

    @property
    def runs(self) -> MarketingRunService:
        return self._runs or get_marketing_run_service()

    def _generate_fn(self):
        if self._writer is None:
            from app.services.marketing.writer_service import generate_package

            self._writer = generate_package
        return self._writer

    def _news_source_fn(self):
        """The company-news source of a template day: `company_news_adapter` (§1's ONE allow-listing FMP
        adapter, contract D6). Imported HERE, lazily, like the writer — this method is the adapter's only
        importer anywhere under app/ (tests/test_marketing_import_boundary.py pins the qualname), so a
        lesson-only process never loads the FMP client through the marketing engine."""
        if self._news is None:
            from app.services.marketing import company_news_adapter

            self._news = company_news_adapter
        return self._news

    # ── kick ─────────────────────────────────────────────────────────────────

    async def kick(self, run_id: str, *, claim: CallerClaim) -> Dict[str, Any]:
        """Idempotent; safe to call as often as the worker likes. Never raises for a CONTENT
        outcome — only for a ledger failure, or MarketingRunNotHeld when the kick would START
        writer spend (select, or spawn a generation) for a run no live claim holds. A final row
        (rest_day / accepted / rejected) answers idempotently whatever the run's STATUS — but only
        to the caller whose claim the run carries (`claim`): a zombie tick is refused before
        anything, `_heal_mirror` included (its write bumps `updated_at`, the claim's liveness)."""
        run = await self.runs.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        problem = claim_problem(run, claim)
        if problem is not None:
            raise MarketingRunNotHeld(f"run {run_id} is not held by this caller: {problem}")
        row = await self.runs.get_script(run_id)
        if row is None:
            self._require_held(run)
            row = await self._select(run)
            if row is None:
                # A template build (drop 2) is still running in the background: no row yet, nothing
                # chosen to name. The worker polls again; the next kick joins the same build.
                return {"status": GENERATING, "source_ref": None, "template_id": None}
        await self._heal_mirror(run, row)
        return await self._advance(run_id, row, run=run)

    def _held_problem(self, run: Dict[str, Any]) -> Optional[str]:
        return held_problem(run, now=_now(), today=_today_et(),
                            stale_seconds=int(settings.MARKETING_RUN_STALE_SECONDS))

    def _require_held(self, run: Dict[str, Any]) -> None:
        problem = self._held_problem(run)
        if problem is not None:
            raise MarketingRunNotHeld(
                f"run {run.get('id')} ({run.get('run_date')}) is not held by a live worker claim: "
                f"{problem}; no writer spend started"
            )

    async def _heal_mirror(self, run: Dict[str, Any], row: Dict[str, Any]) -> None:
        """Copy the selection onto `marketing_runs` for the ledger. Informational only —
        `recent` reads marketing_scripts — and self-healing: a lost write (or a lost INSERT
        response whose retry adopted the row) is repaired by the next poll. Only on a HELD run:
        `update_run` bumps `updated_at`, which is the claim's liveness.

        Drop 2 (contract D11): the mirror is `source_ref`, `template_id`, the class the script's
        template id decides (`selection.content_class_of`: "A" for a lesson, "C"/"F" for a series — an
        unknown id mirrors no class, WARNING) and, when the selection recorded a fallback trail
        (`fact_sheet.selection`), the server-owned run metadata `series` + `series_trail` (≤ 8). It is
        written only when something differs, as ONE fenced write (`update_run(cas=True)`: the metadata
        merge must not revert a claim or a worker PATCH that lands between its read and its write)."""
        ref = row.get("source_ref")
        if not ref:
            return
        template_id = row.get("template_id")
        klass = selection.content_class_of(template_id)
        meta_want = _selection_mirror(row)
        run_meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        if (run.get("source_ref") == ref and run.get("template_id") == template_id
                and (klass is None or run.get("content_class") == klass)
                and (meta_want is None or all(run_meta.get(k) == v for k, v in meta_want.items()))):
            return
        if self._held_problem(run) is not None:
            logger.info("marketing script: selection not mirrored onto run %s (not held); the "
                        "script row is the record", run.get("id"))
            return
        if run.get("source_ref") and run.get("source_ref") != ref:
            logger.warning("marketing script: run %s mirrors source_ref=%r but its script says %r — "
                           "rewriting the mirror from the script", run.get("id"), run.get("source_ref"), ref)
        if klass is None:
            logger.warning("marketing script: run %s's script names template %r, which has no content class "
                           "— no class mirrored (create_posts will refuse the day)", run.get("id"),
                           str(template_id)[:40])
        try:
            written = await self.runs.update_run(
                run["id"], source_ref=ref, template_id=template_id, content_class=klass,
                metadata=meta_want, cas=True,
            )
        except Exception as e:
            logger.warning("marketing script: could not mirror the selection onto run %s "
                           "source_ref=%s (%s: %s) — the next kick retries", run.get("id"), ref,
                           type(e).__name__, e)
            return
        if written is None:
            logger.info("marketing script: run %s changed under the selection mirror — nothing written; the "
                        "next kick retries", run.get("id"))
            return
        run["source_ref"] = ref

    async def _select(self, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The day's first selection (drop 2, contract D11). A rest day → the rest_day INSERT. Otherwise
        the series calendar (`selection.plan_for`), narrowed to the enabled classes and the shipped series
        (`enabled_chain`): a chain of just the lesson is selected HERE, synchronously — with the classes
        at "A" (the default) its writes are exactly today's — and any other chain becomes a background
        TEMPLATE BUILD (`_await_template_build`), whose row this returns if it lands inside
        TEMPLATE_KICK_WAIT_SECONDS, else None (the kick answers `generating`).

        A news chain is used ONLY when the worker HOLDING the run declared it can draw a template
        (WORKER_CAPABILITY_NEWS_TEMPLATES on its claim — `metadata.worker_capabilities`, server-owned).
        Any other holder (a drop-1 image after a rollback, or a web deployed before its worker) would
        draw the template as a lesson — the person's name on the video's first frame, the alt text on the
        image — so its day is the LESSON (WARNING naming the worker), never a failed day. A holder that
        declared `news_templates` but not WORKER_CAPABILITY_LAYOUTS_2B (a drop-2a image) loses only the
        series whose image is a drop-2b layout (LAYOUTS_2B_SERIES, WARNING): the rest of the chain runs."""
        run_id = run["id"]
        run_date = date.fromisoformat(str(run["run_date"])[:10])
        plan = selection.plan_for(run_date)
        if plan.rest_day:
            row, ours = await self.runs.insert_script(
                {"run_id": run_id, "run_date": run_date.isoformat(), "status": REST_DAY})
            logger.info("marketing script %s run_id=%s run_date=%s status=%s (rest day)",
                        "SELECTED" if ours else "selection ADOPTED (a concurrent kick won)", run_id, run_date,
                        row.get("status"))
            return row
        classes = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
        # The per-series switch (drop 2b): MARKETING_NEWS_SERIES ∩ the shipped series, read NOW — and only
        # while a news class is on. With the classes at "A" the chain is the lesson whatever the switch
        # says, so nothing about it is read or logged: that day stays byte for byte Drop 1's.
        news_series = (selection.parse_news_series(settings.MARKETING_NEWS_SERIES)
                       if classes != frozenset({"A"}) else frozenset())
        chain = selection.enabled_chain(plan.chain, classes, shipped=news_series)
        if classes != frozenset({"A"}):
            logger.info("marketing script: content classes enabled: %s news_series=%s run_id=%s run_date=%s "
                        "plan=%s chain=%s", ",".join(sorted(classes)), ",".join(sorted(news_series)) or "none",
                        run_id, run_date, plan.reason, list(chain))
        if chain and chain != (selection.LESSON,) and \
                WORKER_CAPABILITY_NEWS_TEMPLATES not in run_worker_capabilities(run):
            logger.warning(
                "marketing script: the day's chain %s has news series, but the worker holding run_id=%s "
                "(worker_version=%r, attempt %s, declared %s) did not declare %r on its claim — an older "
                "worker image cannot draw a template; the day is the LESSON (deploy the drop-2 marketing "
                "worker)", list(chain), run_id, str(run.get("worker_version") or "")[:64], run.get("attempts"),
                sorted(run_worker_capabilities(run)) or "nothing", WORKER_CAPABILITY_NEWS_TEMPLATES)
            chain = (selection.LESSON,)
        cannot_draw = [s for s in chain if s in LAYOUTS_2B_SERIES]
        if cannot_draw and WORKER_CAPABILITY_LAYOUTS_2B not in run_worker_capabilities(run):
            # A drop-2a image draws templates but refuses a `pair` / `grid` image (a failed day): those
            # series leave the chain, which falls through to its next series exactly as a refused one does.
            logger.warning(
                "marketing script: the day's chain %s has %s, whose post image is a drop-2b layout (%s), but "
                "the worker holding run_id=%s (worker_version=%r, attempt %s, declared %s) did not declare %r "
                "on its claim — %s dropped from the chain (deploy the drop-2b marketing worker)", list(chain),
                cannot_draw, "/".join(WORKER_LAYOUTS_2B), run_id, str(run.get("worker_version") or "")[:64],
                run.get("attempts"), sorted(run_worker_capabilities(run)) or "nothing",
                WORKER_CAPABILITY_LAYOUTS_2B, "it is" if len(cannot_draw) == 1 else "they are")
            chain = tuple(s for s in chain if s not in LAYOUTS_2B_SERIES)
        if chain == (selection.LESSON,) or not chain:
            recent = await self.runs.recent_source_refs(run_date, NEWS_RECENT_LIMIT)
            return await self._select_lesson(run, run_date, recent, classes=classes, selection_block=None)
        return await self._await_template_build(run, run_date, plan, chain, classes)

    async def _select_lesson(self, run: Dict[str, Any], run_date: date, recent: List[str], *,
                             classes: FrozenSet[str], selection_block: Optional[Dict[str, Any]]
                             ) -> Dict[str, Any]:
        """The Learn lesson of the day (class A, the writer) — today's selection, whatever the chain. Only
        the LESSON refs of `recent` reach the rotation (`selection.lesson_refs`: news refs would crowd its
        window and the 34-item pool would repeat), and while a news class is on the lesson's template is one
        of `LESSON_TEMPLATE_IDS` (plan §3). `selection_block` (the plan, the chain and the fallback trail)
        rides in the fact sheet only when the day's chain had news series; with the classes at "A" the
        INSERT is byte for byte today's."""
        run_id = run["id"]
        pool = content_pool.eligible_keys()
        lessons = selection.lesson_refs(recent)
        templates = selection.LESSON_TEMPLATE_IDS if classes != frozenset({"A"}) else None
        sel = selection.choose(pool, run_date, lessons, templates=templates)
        # run_date rides in the same first-write-wins INSERT: it is what `recent` reads.
        base = {"run_id": run_id, "run_date": run_date.isoformat()}
        if sel.rest_day:
            new = {**base, "status": REST_DAY}
        elif sel.source_ref is None:
            # Loud: an empty pool means the bundle is unreadable or everything got excluded.
            logger.error("marketing script: EMPTY content pool run_id=%s run_date=%s", run_id, run_date)
            new = {**base, "status": REJECTED, "reject_reason": REASON_EMPTY_POOL,
                   "last_error": "empty content pool"}
            if selection_block is not None:
                new["fact_sheet"] = {"selection": selection_block}
        else:
            item = content_pool.get_item(sel.source_ref)
            sheet = _fact_sheet_snapshot(item) if item else {}
            if selection_block is not None:
                sheet["selection"] = selection_block
            new = {
                **base, "status": SELECTED, "source_ref": sel.source_ref,
                "template_id": sel.template_id,
                "fact_sheet": sheet,
            }
        row, ours = await self.runs.insert_script(new)
        logger.info(
            "marketing script %s run_id=%s run_date=%s status=%s source_ref=%s template=%s "
            "pool=%d recent=%d", "SELECTED" if ours else "selection ADOPTED (a concurrent kick won)",
            run_id, run_date, row.get("status"), row.get("source_ref"), row.get("template_id"),
            len(pool), len(lessons),
        )
        return row

    # ── the template day (drop 2, contract D11) ───────────────────────────────

    async def _await_template_build(self, run: Dict[str, Any], run_date: date, plan: "selection.DayPlan",
                                    chain: Tuple[str, ...], classes: FrozenSet[str]) -> Optional[Dict[str, Any]]:
        """Start the day's template build as a BACKGROUND task (or join the one already running for this
        run) and wait at most TEMPLATE_KICK_WAIT_SECONDS for its row. None when it is still running —
        the kick answers `generating` and the next kick joins the same task. A build that CRASHED re-raises
        into the kick that awaits it (a 5xx: the worker retries, and the next kick builds afresh); one that
        crashed while nobody waited was logged by its done-callback and is simply rebuilt.

        Why a task: a 13F day may need a live registry build (150 s), far past the worker's 30 s HTTP
        timeout. The build holds no lease and writes ONE row, first write wins, so a second process
        building the same day in a deploy overlap is harmless — the loser adopts the winner's row."""
        run_id = str(run["id"])
        task = self._builds.get(run_id)
        if task is not None and task.done():
            self._builds.pop(run_id, None)
            if not task.cancelled() and task.exception() is None:
                return task.result()
            task = None
        if task is None:
            task = asyncio.create_task(self._build_template_day(run, run_date, plan, chain, classes),
                                       name=f"marketing_template:{run_id}")
            self._builds[run_id] = task
            self._tasks.add(task)
            task.add_done_callback(lambda t, rid=run_id: self._template_build_done(rid, t))
            logger.info("marketing script: template build STARTED run_id=%s run_date=%s plan=%s chain=%s",
                        run_id, run_date, plan.reason, list(chain))
        try:
            return await asyncio.wait_for(asyncio.shield(task), TEMPLATE_KICK_WAIT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("marketing script: template build of run %s still running after %.0fs — answering "
                        "generating", run_id, TEMPLATE_KICK_WAIT_SECONDS)
            return None

    def _template_build_done(self, run_id: str, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if self._builds.get(run_id) is task:
            self._builds.pop(run_id, None)
        if task.cancelled():
            logger.warning("marketing script: template build of run %s was CANCELLED (shutdown) — the next "
                           "kick builds the day afresh", run_id)
            return
        exc = task.exception()
        if exc is not None:
            logger.error("marketing script: template build CRASHED run_id=%s: %s: %s — the next kick builds "
                         "the day afresh", run_id, type(exc).__name__, exc, exc_info=exc)

    async def _build_template_day(self, run: Dict[str, Any], run_date: date, plan: "selection.DayPlan",
                                  chain: Tuple[str, ...], classes: FrozenSet[str]) -> Dict[str, Any]:
        """Walk the day's chain until a series yields a record its template composes and re-checks, and
        store it as the day's ACCEPTED script — or fall to the lesson at the chain's end. Always ends in
        exactly ONE `insert_script` (first write wins: a concurrent builder's row is adopted). No model,
        no lease, 0 tokens. Every fallback lands in the trail (`fact_sheet.selection`), logged.

        Each record is composed from its JSON ROUND TRIP (what the fact sheet stores and create_posts
        re-reads), so compose-time and re-check-time inputs are identical; the output is re-checked with
        `news_templates.revalidate` after the logos and the frozen formats are attached, as stored.

        It starts by reading the day's row: a kick whose "no row yet" read raced a build that had just
        finished (its INSERT committed, its done-callback ran) starts a new build — which ADOPTS that row
        here instead of walking the adapter again (a series that timed out in the first build would
        otherwise hit FMP a second time, only to lose its INSERT with 23505). A new build is created only
        after the previous one's done-callback ran, so its INSERT is already visible to this read."""
        run_id = str(run["id"])
        existing = await self.runs.get_script(run_id)
        if existing is not None:
            logger.info("marketing template day: run_id=%s already has its row (status=%s source=%s) — a "
                        "finished build's row ADOPTED, nothing rebuilt", run_id, existing.get("status"),
                        existing.get("source_ref"))
            return existing
        started = _mono()
        deadline = started + TEMPLATE_BUILD_BUDGET_SECONDS
        recent = await self.runs.recent_source_refs(run_date, NEWS_RECENT_LIMIT)
        exclude = frozenset(r for r in recent if isinstance(r, str))
        try:
            news = self._news_source_fn()
        except Exception as e:  # noqa: BLE001 — a broken adapter import must not cost the day its lesson
            logger.exception("marketing template day: the company-news source could not be loaded run_id=%s "
                             "(%s: %s) — every series falls through to the lesson", run_id, type(e).__name__, e)
            news = None
        unavailable_cls = getattr(news, "MarketingNewsUnavailable", None)
        store_state = smart_link.store_state()
        allow_x_url = bool(settings.MARKETING_X_ALLOW_URLS)
        image_posts = bool(settings.MARKETING_IMAGE_POSTS)   # read ONCE for this build, like a generation
        trail: List[Dict[str, str]] = []

        def block(extra: Dict[str, str]) -> Dict[str, Any]:
            return {"plan": plan.reason, "chain": list(chain), "trail": [*trail, extra]}

        for i, series in enumerate(chain):
            if series == selection.LESSON:
                row = await self._select_lesson(run, run_date, recent, classes=classes,
                                                selection_block=block({"series": selection.LESSON,
                                                                       "outcome": "chosen"}))
                logger.info("marketing template day fell to the LESSON run_id=%s trail=%s in %.1fs", run_id,
                            _trail_text(trail), _mono() - started)
                return row
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            if news is None:
                self._fell_back(run_id, trail, series, "error", "news_source_unavailable", nxt)
                continue
            left = deadline - _mono()
            if left < SERIES_MIN_START_SECONDS:
                self._fell_back(run_id, trail, series, "budget", f"{max(left, 0.0):.1f}s left", nxt)
                continue
            cap = min(left, SERIES_BUDGET_SECONDS.get(series, DEFAULT_SERIES_BUDGET_SECONDS))
            try:
                got = await asyncio.wait_for(
                    news.candidates(series, run_date=run_date, exclude=exclude, limit=MAX_CANDIDATES_PER_SERIES,
                                    deadline=_mono() + cap),
                    cap + SERIES_WAIT_SLACK_SECONDS)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self._fell_back(run_id, trail, series, "timeout", f"no answer in {cap:.0f}s", nxt)
                continue
            except Exception as e:  # noqa: BLE001 — one series' failure is the next series' turn
                if unavailable_cls is not None and isinstance(e, unavailable_cls):
                    self._fell_back(run_id, trail, series, "unavailable",
                                    str(getattr(e, "reason", "") or type(e).__name__), nxt)
                else:
                    logger.exception("marketing series %s ERROR run_id=%s (%s: %s)", series, run_id,
                                     type(e).__name__, e)
                    self._fell_back(run_id, trail, series, "error", type(e).__name__, nxt)
                continue
            records = tuple(getattr(got, "records", ()) or ())
            rejections = getattr(got, "rejections", None)
            rejections = dict(rejections) if isinstance(rejections, Mapping) else {}
            refused: List[str] = []
            for rec in records:
                built = await self._compose_candidate(
                    run, run_date, series, rec, rejections, block({"series": series, "outcome": "chosen"}),
                    store_state=store_state, allow_x_url=allow_x_url, image_posts=image_posts,
                    deadline=deadline, news=news, refused=refused)
                if built is None:
                    continue
                output, sheet, ref = built
                row, ours = await self.runs.insert_script({
                    "run_id": run_id, "run_date": run_date.isoformat(), "status": ACCEPTED,
                    "source_ref": ref, "template_id": series, "output": output, "violations": [],
                    "fact_sheet": sheet, "generation_id": str(uuid.uuid4()), "generations": 0,
                    "model": None, "prompt_version": news_templates.TEMPLATE_VERSION, "tokens_used": 0,
                })
                if ours:
                    logger.info("marketing template ACCEPTED run_id=%s series=%s class=%s source=%s trail=%s "
                                "formats=%s logos=%d/%d in %.1fs", run_id, series, output.get("content_class"),
                                ref, _trail_text(trail), output.get("post_formats"),
                                sum(1 for lg in output.get("logos") or () if lg.get("url")),
                                len(output.get("logos") or ()), _mono() - started)
                else:
                    logger.info("marketing template day ADOPTED a concurrent writer's row run_id=%s status=%s "
                                "source=%s (ours was %s)", run_id, row.get("status"), row.get("source_ref"), ref)
                return row
            if refused:
                logger.error("marketing series %s refused every candidate run_id=%s codes=%s", series, run_id,
                             sorted(set(refused)))
                self._fell_back(run_id, trail, series, "all_refused", sorted(set(refused))[0], nxt)
            elif records:   # unreachable: a record either composes or is refused
                self._fell_back(run_id, trail, series, "all_refused", "unknown", nxt)
            elif rejections and set(k for k, n in rejections.items() if n) == {"already_posted"}:
                self._fell_back(run_id, trail, series, "all_recent", str(getattr(got, "skip_reason", "") or ""), nxt)
            else:
                self._fell_back(run_id, trail, series, "no_candidates",
                                str(getattr(got, "skip_reason", "") or ""), nxt)
        # Unreachable while every chain ends in the lesson (`selection.enabled_chain`); never a silent day.
        logger.error("marketing template day: the chain %s ended without the lesson run_id=%s — selecting the "
                     "lesson", list(chain), run_id)
        return await self._select_lesson(run, run_date, recent, classes=classes,
                                         selection_block=block({"series": selection.LESSON, "outcome": "chosen"}))

    @staticmethod
    def _fell_back(run_id: str, trail: List[Dict[str, str]], series: str, outcome: str, reason: str,
                   nxt: Optional[str]) -> None:
        entry = {"series": series, "outcome": outcome}
        reason = str(reason or "")[:60]
        if reason:
            entry["reason"] = reason
        trail.append(entry)
        logger.warning("marketing series FELL BACK run_id=%s from=%s outcome=%s reason=%s next=%s", run_id, series,
                       outcome, reason or "-", nxt)

    async def _compose_candidate(
        self, run: Dict[str, Any], run_date: date, series: str, rec: Any, rejections: Dict[str, Any],
        selection_block: Dict[str, Any], *, store_state: str, allow_x_url: bool, image_posts: bool,
        deadline: float, news: Any, refused: List[str],
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
        """(output, fact sheet, ledger key) of ONE candidate record, as stored — or None, its refusal code
        appended to `refused` (logged). Never raises but for a cancellation: a candidate the template
        cannot write is the next candidate's turn."""
        run_id = str(run["id"])
        try:
            if getattr(rec, "series", None) != series:
                raise news_templates.NewsTemplateRefused(
                    "record_invalid", f"a {getattr(rec, 'series', None)!r} record offered for {series}")
            # Compose from the JSON round trip of the record — exactly what the fact sheet stores and
            # create_posts' re-check reads back: no float / date / key-order drift between the two.
            rec = news_rules.record_from_dict(json.loads(json.dumps(news_rules.record_to_dict(rec), allow_nan=False)))
            sheet = news_rules.fact_sheet(rec, rejections=rejections, selection=selection_block)
            sheet = json.loads(json.dumps(sheet, allow_nan=False))
            ref = news_rules.ledger_key(rec)
            out = news_templates.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url)
        except asyncio.CancelledError:
            raise
        except news_templates.NewsTemplateRefused as e:
            logger.info("marketing series %s candidate REFUSED run_id=%s code=%s (%s)", series, run_id, e.code,
                        str(getattr(e, "detail", ""))[:160])
            refused.append(str(e.code))
            return None
        except Exception as e:  # noqa: BLE001 — a malformed record is a refused candidate, never a crash
            logger.exception("marketing series %s candidate FAILED to compose run_id=%s (%s: %s)", series,
                             run_id, type(e).__name__, e)
            refused.append("internal_error")
            return None
        problems = news_templates.revalidate(out, fact_sheet=sheet, run_date=run_date)
        if problems:
            codes = sorted({str(p.get("code")) for p in problems})
            logger.error("marketing series %s candidate %s failed its own re-check run_id=%s codes=%s", series,
                         ref, run_id, codes)
            refused.extend(codes)
            return None
        out["logos"] = await self._attach_logos(out, deadline=deadline, news=news, run_id=run_id)
        out = await self._frozen_output(out, run_date, run_id=run_id, gen_id=f"template:{series}",
                                        image_posts=image_posts)
        try:
            stored = json.loads(json.dumps(out, allow_nan=False))   # what JSONB will hold
        except (TypeError, ValueError) as e:
            logger.error("marketing series %s candidate %s is not JSON-safe run_id=%s (%s: %s)", series, ref,
                         run_id, type(e).__name__, e)
            refused.append("schema")
            return None
        problems = news_templates.revalidate(stored, fact_sheet=sheet, run_date=run_date)
        if problems:
            codes = sorted({str(p.get("code")) for p in problems})
            logger.error("marketing series %s candidate %s failed the re-check AS STORED run_id=%s codes=%s",
                         series, ref, run_id, codes)
            refused.extend(codes)
            return None
        return stored, sheet, ref

    async def _attach_logos(self, output: Dict[str, Any], *, deadline: float, news: Any,
                            run_id: str) -> List[Dict[str, Any]]:
        """`output.logos` (contract D10): one entry per `news_templates.logo_refs(output)` — that key and
        that company name, in draw order — with the stored logo's `{url, sha256, bytes, width, height}`,
        or all None (the worker draws the company's wordmark tile). Fetched through the adapter
        (`fetch_logo`), checked by `logo_check.inspect_logo`, stored by `runs.store_logo`; bytes are never
        altered. A logo NEVER refuses a candidate: every failure is a wordmark + a WARNING."""
        refs = news_templates.logo_refs(output)
        sem = asyncio.Semaphore(LOGO_FETCH_CONCURRENCY)

        async def one(ref: Dict[str, str]) -> Dict[str, Any]:
            key, name = ref.get("key"), ref.get("name")
            entry: Dict[str, Any] = {"key": key, "name": name, "url": None, "sha256": None, "bytes": None,
                                     "width": None, "height": None}
            async with sem:
                left = deadline - _mono()
                if left < 1.0:
                    logger.warning("marketing logo: no time left for %s run_id=%s — wordmark", key, run_id)
                    return entry
                limit = min(LOGO_FETCH_TIMEOUT_SECONDS, left)
                try:
                    got = await asyncio.wait_for(
                        news.fetch_logo(key, max_bytes=logo_check.LOGO_MAX_BYTES, timeout=limit), limit + 2.0)
                    if got is None:
                        logger.warning("marketing logo: none fetched for %s run_id=%s — wordmark", key, run_id)
                        return entry
                    data, content_type = got
                    info = logo_check.inspect_logo(data, content_type)
                    stored = await self.runs.store_logo(data, info)
                except asyncio.CancelledError:
                    raise
                except logo_check.LogoRejected as e:
                    logger.warning("marketing logo: %s refused (%s) run_id=%s — wordmark", key,
                                   getattr(e, "reason", "?"), run_id)
                    return entry
                except Exception as e:  # noqa: BLE001 — a logo never refuses a candidate
                    logger.warning("marketing logo: %s failed run_id=%s (%s: %s) — wordmark", key, run_id,
                                   type(e).__name__, str(e)[:160])
                    return entry
            if not stored:
                return entry
            entry.update({k: stored.get(k) for k in ("url", "sha256", "bytes", "width", "height")})
            return entry

        return list(await asyncio.gather(*(one(r) for r in refs)))

    def _rejected_body(self, row: Dict[str, Any]) -> Dict[str, Any]:
        reason = row.get("reject_reason")
        if reason not in SCRIPT_REJECT_REASONS:
            logger.warning("marketing script: rejected row run_id=%s carries reject_reason=%r — "
                           "reported as %r", row.get("run_id"), reason, REASON_CONTENT)
            reason = REASON_CONTENT
        return {
            "status": REJECTED, "source_ref": row.get("source_ref"),
            "template_id": row.get("template_id"), "reason": reason,
            # Only a content verdict has violations to show; an outage's body must not cite
            # the codes of an earlier content round as the day's cause.
            "violations": _codes(row.get("violations")) if reason == REASON_CONTENT else [],
        }

    async def _advance(self, run_id: str, row: Dict[str, Any], *, run: Dict[str, Any],
                       _depth: int = 0) -> Dict[str, Any]:
        status = row.get("status")
        base = {"source_ref": row.get("source_ref"), "template_id": row.get("template_id")}
        if status == REST_DAY:
            return {"status": REST_DAY, **base}
        if status == ACCEPTED and isinstance(row.get("output"), dict):
            return {"status": ACCEPTED, **base, "script": worker_script(row["output"])}
        if status == REJECTED:
            return self._rejected_body(row)

        now = _now()
        verdict = _cap_verdict(row)
        if status == SELECTED:
            if verdict is not None:
                return await self._finalize_rejected(run_id, row, verdict, run=run, _depth=_depth)
            not_before = _parse(row.get("retry_not_before"))
            if not_before and not_before > now:
                return {"status": DEFERRED, **base,
                        "retry_after_seconds": int((not_before - now).total_seconds()) + 1}
            self._require_held(run)
            self._spawn(run_id)
            return {"status": GENERATING, **base}
        if status == GENERATING:
            lease = _parse(row.get("lease_until"))
            if lease is not None and lease > now:
                return {"status": GENERATING, **base}
            owner = self._owner_state(run_id, now)
            if owner == _OWNER_ALIVE:
                # The lease lapsed (a refresh that errored, or a slow round trip) but the owner is
                # a live task in THIS process: not dead, whatever the cap says. Closing the day here
                # fenced out the paid package it was about to write; spawning is a no-op anyway.
                logger.info("marketing script: lease of run %s generation %s lapsed at %s but its "
                            "owner is alive in this process — leaving it be", run_id,
                            row.get("generation_id"), row.get("lease_until"))
                return {"status": GENERATING, **base}
            if verdict is not None:
                if owner == _OWNER_WEDGED:
                    # The one place a wedged owner changes anything, so the one place that says so.
                    logger.warning("marketing script: the generation task for run %s has run since "
                                   "%s, past OWNER_ALIVE_SECONDS (%ds), at the cap — treating it as "
                                   "wedged: closing the day under it (its late write is fenced out)",
                                   run_id, self._spawned_at.get(run_id), OWNER_ALIVE_SECONDS)
                # The owner of the last allowed generation died without a terminal write. The
                # takeover below would be refused at the cap, so nothing else would ever close it.
                return await self._finalize_rejected(
                    run_id, row, verdict, run=run, _depth=_depth,
                    last_error=(f"generation {row.get('generation_id')} (#{row.get('generations')}) "
                                "lost its lease without a terminal write"),
                )
            if owner == _OWNER_WEDGED:
                # Below the caps an over-age task in THIS process is still the owner: `_spawn`
                # would be a no-op (it is in `_running`), and cancelling it would need the fenced
                # hand-back ordering. Nothing is taken over; said once per task, not per poll.
                self._report_wedged_below_cap(run_id)
                return {"status": GENERATING, **base}
            self._require_held(run)
            self._spawn(run_id)  # the owner died; the task's CAS takes the expired lease
            return {"status": GENERATING, **base}
        logger.error("marketing script: unknown status %r on run %s", status, run_id)
        return {"status": DEFERRED, **base, "retry_after_seconds": int(RETRY_AFTER_GEMINI_FAILURE.total_seconds())}

    async def _finalize_rejected(
        self, run_id: str, row: Dict[str, Any], reason: str, *, run: Optional[Dict[str, Any]] = None,
        last_error: Optional[str] = None, readvance: bool = True, _depth: int = 0,
    ) -> Dict[str, Any]:
        """Close the day `rejected` — a CAS on exactly the row we judged: (status, generations,
        generation_id, lease_until). A live owner that refreshed its lease, or landed its own
        terminal write, in the meantime makes this miss; then the row is re-read and advanced
        (once) instead of answering `rejected` for a day whose script may be `accepted`."""
        patch: Dict[str, Any] = {"status": REJECTED, "reject_reason": reason, "lease_until": None}
        if last_error:
            patch["last_error"] = last_error[:2000]
        done = await self.runs.update_script_where(
            run_id, patch,
            expect={"status": row.get("status"), "generations": _int(row.get("generations")),
                    "generation_id": row.get("generation_id"),
                    "lease_until": _ts_key(row.get("lease_until"))},
        )
        if done is None:
            fresh = await self.runs.get_script(run_id)
            if fresh is None:
                raise MarketingRunError(f"finalize: the script row of run {run_id} vanished")
            if fresh.get("status") == REJECTED:
                return self._rejected_body(fresh)
            logger.info("marketing script: finalize of run %s lost to a concurrent write (now %s, "
                        "generation %s) — re-reading", run_id, fresh.get("status"), fresh.get("generation_id"))
            if readvance and run is not None and _depth == 0:
                return await self._advance(run_id, fresh, run=run, _depth=1)
            return {"status": GENERATING, "source_ref": fresh.get("source_ref"),
                    "template_id": fresh.get("template_id")}
        logger.warning(
            "marketing script REJECTED for the day run_id=%s source_ref=%s reason=%s generations=%s "
            "content_rejections=%s violations=%s last_error=%s", run_id, done.get("source_ref"), reason,
            done.get("generations"), done.get("content_rejections"),
            _codes(done.get("violations")) if reason == REASON_CONTENT else [],
            (done.get("last_error") or "")[:300],
        )
        return self._rejected_body(done)

    # ── generation ───────────────────────────────────────────────────────────

    def _owner_state(self, run_id: str, now: datetime) -> str:
        """Is a generation task for `run_id` running in THIS process — young enough to be a live
        owner (`_OWNER_ALIVE`), or past OWNER_ALIVE_SECONDS (`_OWNER_WEDGED`)? `_OWNER_NONE` when
        no task here holds it. Silent: the caller logs on the path that acts on the verdict."""
        if run_id not in self._running:
            return _OWNER_NONE
        started = self._spawned_at.get(run_id)
        if started is not None and (now - started).total_seconds() < OWNER_ALIVE_SECONDS:
            return _OWNER_ALIVE
        return _OWNER_WEDGED

    def _report_wedged_below_cap(self, run_id: str) -> None:
        """ONE WARNING per task (cleared when it ends), not one per 15-s worker poll: an old task
        below the caps is presumed wedged, but nothing is taken over and the kick keeps answering
        `generating` until it ends or the process restarts."""
        if run_id in self._wedge_reported:
            return
        self._wedge_reported.add(run_id)
        logger.warning("marketing script: the generation task for run %s has run since %s, past "
                       "OWNER_ALIVE_SECONDS (%ds), below the caps — nothing to take over in this "
                       "process (it still owns the run; a second task would be a parallel paid "
                       "generation), so the day waits for it to end or for a restart",
                       run_id, self._spawned_at.get(run_id), OWNER_ALIVE_SECONDS)

    def _spawn(self, run_id: str) -> None:
        if run_id in self._running:
            return
        self._running.add(run_id)
        self._spawned_at[run_id] = _now()
        task = asyncio.create_task(self._generate(run_id), name=f"marketing_script:{run_id}")
        self._tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            self._running.discard(run_id)
            self._spawned_at.pop(run_id, None)
            self._wedge_reported.discard(run_id)
            if not t.cancelled() and t.exception() is not None:
                exc = t.exception()
                logger.error("marketing script task crashed run_id=%s: %s: %s", run_id,
                             type(exc).__name__, exc, exc_info=exc)

        task.add_done_callback(_done)

    async def _acquire(self, run_id: str, gen_id: str) -> Optional[Dict[str, Any]]:
        """Take the run for ONE generation (as `gen_id`), or return None if it is not ours to
        take. A row that reached a cap since the kick read it is closed here too."""
        row = await self.runs.get_script(run_id)
        if row is None:
            return None
        now = _now()
        status = row.get("status")
        if status == GENERATING:
            lease = _parse(row.get("lease_until"))
            if lease is not None and lease > now:
                return None
        elif status != SELECTED:
            return None
        verdict = _cap_verdict(row)
        if verdict is not None:
            await self._finalize_rejected(
                run_id, row, verdict, readvance=False,
                last_error=(f"generation {row.get('generation_id')} (#{row.get('generations')}) lost "
                            "its lease without a terminal write") if status == GENERATING else None,
            )
            return None
        if status == SELECTED:
            not_before = _parse(row.get("retry_not_before"))
            if not_before and not_before > now:
                return None
        generations = _int(row.get("generations"))
        until = now + timedelta(seconds=LEASE_SECONDS)
        # The statement runs in a worker thread (`sb_exec`), and cancelling this coroutine does
        # NOT stop it: an UPDATE still in flight can commit after a cancel. So the write is a task
        # of its own and a cancel waits for it to answer before handing the run back — a
        # hand-back sent while it is in flight can overtake it, match nothing, and leave a
        # 632-s lease with nobody behind it.
        write = asyncio.ensure_future(self.runs.update_script_where(
            run_id,
            {"status": GENERATING, "generation_id": gen_id, "generations": generations + 1,
             "lease_until": _iso(until), "retry_not_before": None},
            # lease_until too: an owner whose refresh landed between our read and this write
            # is alive, and must not be taken over on the strength of a stale read.
            expect={"status": status, "generations": generations,
                    "generation_id": row.get("generation_id"),
                    "lease_until": _ts_key(row.get("lease_until"))},
        ))
        try:
            taken = await asyncio.shield(write)
        except asyncio.CancelledError:
            await self._settle_cancelled_acquire(run_id, gen_id, write)
            raise
        if taken is not None:
            self._leases[gen_id] = until
        return taken

    async def _settle_cancelled_acquire(self, run_id: str, gen_id: str, write: "asyncio.Future") -> None:
        """A cancel (shutdown) arrived while the acquire UPDATE was in flight. Wait for the
        statement to ANSWER, then hand back only if it took the run — ordered after it, so the
        fenced hand-back can no longer run first and match nothing. The wait and the hand-back
        share HAND_BACK_TIMEOUT_SECONDS (the lifespan gives shutdown 5 s in all). If the statement
        does not answer in time the lease is the backstop: a hand-back now could overtake it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HAND_BACK_TIMEOUT_SECONDS
        why = "cancelled while acquiring the lease"
        try:
            taken = await asyncio.wait_for(asyncio.shield(write), HAND_BACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("marketing script: acquire of run %s by generation %s still in flight %.1fs "
                           "after the cancel — not handing back (a hand-back could overtake it); the "
                           "lease (%ds) is the backstop", run_id, gen_id, HAND_BACK_TIMEOUT_SECONDS,
                           LEASE_SECONDS)

            def _late(f: "asyncio.Future") -> None:
                exc = None if f.cancelled() else f.exception()  # retrieved: never "never retrieved"
                if exc is None and not f.cancelled() and f.result() is not None:
                    logger.warning("marketing script: the orphaned acquire of run %s by generation %s "
                                   "landed after the cancel — nobody owns that lease; it expires in "
                                   "%ds and the next kick takes the run", run_id, gen_id, LEASE_SECONDS)

            write.add_done_callback(_late)
            return
        except asyncio.CancelledError:
            logger.warning("marketing script: waiting for the in-flight acquire of run %s (generation "
                           "%s) was INTERRUPTED — the lease (%ds) is the backstop", run_id, gen_id,
                           LEASE_SECONDS)
            raise
        except Exception as e:
            # The statement answered with an error, so a hand-back is ordered after it; it may
            # still have committed (a response lost after commit), and the hand-back is fenced.
            logger.warning("marketing script: the acquire of run %s by generation %s failed during "
                           "a cancel (%s: %s) — handing back in case it landed", run_id, gen_id,
                           type(e).__name__, e)
            taken = e
        if taken is None:
            logger.info("marketing script: the cancelled acquire of run %s by generation %s took "
                        "nothing; nothing to hand back", run_id, gen_id)
            return
        await self._hand_back(run_id, gen_id, why, timeout=max(deadline - loop.time(), 0.0))

    def _lease_left(self, gen_id: str) -> Optional[float]:
        until = self._leases.get(gen_id)
        return None if until is None else (until - _now()).total_seconds()

    async def _refresh_lease(self, run_id: str, gen_id: str) -> bool:
        """Before every model call. A CAS miss = the run was taken over → LeaseLost (the
        generation stops without spending). A ledger ERROR is not a lost lease: retried a
        little, then judged against the lease this generation last WROTE — every terminal write
        is fenced on generation_id, so a result that did lose the run still writes nothing, and
        aborting would throw away a paid, publishable draft.

        Returns True when the lease on record still covers one worst-case model call, False when
        it does not (every attempt errored, or landed too late): starting that call could outlive
        the lease, and a second container could take the run and pay for a parallel generation.
        `before_call`'s contract for False: a writer that already holds a publishable candidate
        skips the call and keeps it (as it does when a repair call fails); with nothing to keep,
        the call is its only way to a package, and it may still make it — the terminal write is
        fenced either way. False never throws a draft away; a writer that ignores it simply
        makes the call, as before."""
        need = worst_case_model_call_seconds()
        last: Optional[BaseException] = None
        for attempt in range(_REFRESH_ATTEMPTS):
            until = _now() + timedelta(seconds=LEASE_SECONDS)
            try:
                ok = await self.runs.update_script_where(
                    run_id, {"lease_until": _iso(until)},
                    expect={"status": GENERATING, "generation_id": gen_id},
                )
            except Exception as e:
                last = e
                logger.warning("marketing script: lease refresh failed run_id=%s generation=%s "
                               "attempt=%d (%s: %s)", run_id, gen_id, attempt + 1, type(e).__name__, e)
                if attempt < _REFRESH_ATTEMPTS - 1:
                    await asyncio.sleep(_REFRESH_BACKOFF_SECONDS * (attempt + 1))
                continue
            if ok is None:
                raise LeaseLost(f"run {run_id} generation {gen_id} lost its lease")
            self._leases[gen_id] = until
            left = self._lease_left(gen_id)
            if left is not None and left >= need:
                return True
            # Written, but the round trip ate the margin: the lease starts before the call does.
            last = TimeoutError(f"the refresh landed with only {left:.0f}s of lease left")
            logger.warning("marketing script: lease refresh landed late run_id=%s generation=%s "
                           "attempt=%d (%.0fs left < %.0fs for one model call)", run_id, gen_id,
                           attempt + 1, left or 0.0, need)
        left = self._lease_left(gen_id)
        if left is None or left >= need:
            logger.warning("marketing script: lease NOT refreshed run_id=%s generation=%s (%s: %s) — "
                           "continuing: the lease on record (%s) still covers one model call; the "
                           "terminal write is fenced on the generation id", run_id, gen_id,
                           type(last).__name__, last,
                           "unknown" if left is None else f"{left:.0f}s left")
            return True
        logger.warning("marketing script: lease NOT refreshed run_id=%s generation=%s (%s: %s) and "
                       "only %.0fs of it is left, less than one worst-case model call (%.0fs) — "
                       "asking the writer to skip this call", run_id, gen_id, type(last).__name__,
                       last, left, need)
        return False

    async def _finish(self, run_id: str, gen_id: str, patch: Dict[str, Any], *,
                      what: str = "terminal write") -> str:
        """Write fenced on our generation id; WRITTEN / SUPERSEDED / LOST. Retried a little:
        losing this write after a paid Gemini call means paying again once the lease expires.
        A retry that matches nothing after an attempt RAISED may be our own write whose response
        was lost — re-read before calling it superseded.

        A TERMINAL write (anything but the hand-back) is registered in `_terminal` while it runs,
        and each attempt's statement is a future of its own awaited through a shield: `sb_exec`
        runs it in a worker thread a cancel cannot stop, and a shutdown hand-back sent while it
        was in flight could overtake it and discard a paid package or a content verdict. On a
        cancel the record is LEFT for `_generate_as`, which settles that statement first
        (`_settle_cancelled_terminal`). The hand-back is never registered — it would wait on
        itself."""
        if what == "hand-back":
            return await self._finish_attempts(run_id, gen_id, patch, what=what, slot=None)
        slot = self._terminal[gen_id] = _TerminalWrite(patch, what)
        try:
            outcome = await self._finish_attempts(run_id, gen_id, patch, what=what, slot=slot)
        except asyncio.CancelledError:
            raise  # the record stays: `_generate_as`'s cancel handler settles it
        except BaseException:
            self._forget_terminal(gen_id, slot)
            raise
        self._forget_terminal(gen_id, slot)
        return outcome

    def _forget_terminal(self, gen_id: str, slot: _TerminalWrite) -> None:
        if self._terminal.get(gen_id) is slot:
            del self._terminal[gen_id]

    async def _finish_attempts(self, run_id: str, gen_id: str, patch: Dict[str, Any], *,
                               what: str, slot: Optional[_TerminalWrite]) -> str:
        raised = False
        for attempt in range(_FINISH_ATTEMPTS):
            try:
                if slot is None:
                    done = await self.runs.update_script_where(
                        run_id, {**patch, "lease_until": None},
                        expect={"status": GENERATING, "generation_id": gen_id},
                    )
                else:
                    slot.fut = asyncio.ensure_future(self.runs.update_script_where(
                        run_id, {**patch, "lease_until": None},
                        expect={"status": GENERATING, "generation_id": gen_id},
                    ))
                    done = await asyncio.shield(slot.fut)
            except Exception as e:
                raised = True
                if slot is not None:
                    slot.raised = True
                logger.warning("marketing script: %s failed run_id=%s generation=%s attempt=%d "
                               "(%s: %s)", what, run_id, gen_id, attempt + 1, type(e).__name__, e)
                if attempt < _FINISH_ATTEMPTS - 1:
                    await asyncio.sleep(_FINISH_BACKOFF_SECONDS * (attempt + 1))
                continue
            if done is not None:
                return WRITTEN
            if raised and await self._landed(run_id, gen_id, patch):
                logger.info("marketing script: %s landed (an earlier response was lost) run_id=%s "
                            "generation=%s status=%s", what, run_id, gen_id, patch.get("status"))
                return WRITTEN
            if what == "hand-back":
                # Fenced on our id, so this is the normal answer for a run we never took (or had
                # already written): not evidence that anyone else holds it.
                logger.info("marketing script: hand-back of generation %s on run %s matched nothing "
                            "(it does not hold the run: never taken, already written, or taken "
                            "over); nothing written", gen_id, run_id)
            else:
                logger.warning(
                    "marketing script: %s of generation %s on run %s matched nothing (another "
                    "generation holds the run, or the row is final); nothing written", what, gen_id,
                    run_id,
                )
            return SUPERSEDED
        logger.error("marketing script: %s LOST run_id=%s generation=%s status=%s — the lease "
                     "(%ds) expires and the next kick decides", what, run_id, gen_id,
                     patch.get("status"), LEASE_SECONDS)
        return LOST

    #: What `_landed` compares between our patch and the re-read row: every scalar a terminal
    #: write carries whose value survives the round trip exactly (text / integer columns). A row
    #: finalized under our id, or rewritten by another generation between our attempts, differs in
    #: at least one of them; the JSONB and timestamp columns are not compared (their
    #: representation changes on the way back, which would call a landed write lost).
    _LANDED_FIELDS = ("status", "last_error", "reject_reason", "content_rejections", "tokens_used")

    async def _landed(self, run_id: str, gen_id: str, patch: Dict[str, Any]) -> bool:
        """After an attempt RAISED and a retry matched nothing: is the row exactly what OUR
        write would have made it — our generation_id and every `_LANDED_FIELDS` value the patch
        sets? Anything else (another generation's write, a finalize under our id) means our
        package was NOT recorded."""
        try:
            cur = await self.runs.get_script(run_id)
        except Exception as e:
            logger.warning("marketing script: re-read after a failed write failed run_id=%s "
                           "generation=%s (%s: %s)", run_id, gen_id, type(e).__name__, e)
            return False
        if not cur or str(cur.get("generation_id")) != str(gen_id):
            return False
        return all(cur.get(f) == patch.get(f) for f in self._LANDED_FIELDS if f in patch)

    async def _hand_back(self, run_id: str, gen_id: str, why: str, *,
                         timeout: Optional[float] = None) -> None:
        """Give the run back (`selected`) so the next container takes it at once instead of
        waiting out the lease. Fenced on our id, so it is a no-op if we never held it. Never
        swallows a cancellation; never silent. `timeout` defaults to HAND_BACK_TIMEOUT_SECONDS,
        read at CALL time (a default argument would freeze it at import)."""
        if timeout is None:
            timeout = HAND_BACK_TIMEOUT_SECONDS
        if timeout <= 0:
            logger.warning("marketing script: no time left to hand back run_id=%s generation=%s (%s) "
                           "— the lease (%ds) is the backstop", run_id, gen_id, why, LEASE_SECONDS)
            return
        try:
            outcome = await asyncio.wait_for(
                self._finish(run_id, gen_id, {"status": SELECTED, "last_error": why[:2000]},
                             what="hand-back"),
                timeout,
            )
        except asyncio.CancelledError:
            logger.warning("marketing script: hand-back INTERRUPTED run_id=%s generation=%s (%s) — "
                           "the lease (%ds) is the backstop", run_id, gen_id, why, LEASE_SECONDS)
            raise
        except Exception as e:  # incl. the wait_for timeout
            logger.warning("marketing script: hand-back FAILED run_id=%s generation=%s (%s) %s: %s — "
                           "the lease (%ds) is the backstop", run_id, gen_id, why, type(e).__name__, e,
                           LEASE_SECONDS)
            return
        if outcome == WRITTEN:
            logger.info("marketing script: run %s handed back by generation %s (%s)", run_id, gen_id, why)

    async def _generate(self, run_id: str) -> None:
        gen_id = str(uuid.uuid4())
        try:
            await self._generate_as(run_id, gen_id)
        finally:
            self._leases.pop(gen_id, None)
            self._terminal.pop(gen_id, None)

    async def _generate_as(self, run_id: str, gen_id: str) -> None:
        try:
            row = await self._acquire(run_id, gen_id)
        except asyncio.CancelledError:
            # `_acquire` already settled its own in-flight UPDATE (and handed back if it took the
            # run); a cancel during its reads or its at-cap finalize needs nothing undone.
            logger.info("marketing script: generation %s of run %s cancelled while acquiring",
                        gen_id, run_id)
            raise
        except Exception as e:
            logger.warning("marketing script: acquire FAILED run_id=%s generation=%s (%s: %s) — "
                           "the next kick retries", run_id, gen_id, type(e).__name__, e)
            # The statement ANSWERED (with an error), so this fenced hand-back is ordered after
            # it — and it may have committed before its response was lost.
            await self._hand_back(run_id, gen_id, f"acquire failed: {type(e).__name__}: {e}")
            return
        if row is None:
            return
        try:
            await self._generate_owned(run_id, row, gen_id)
        except asyncio.CancelledError:
            terminal = self._terminal.pop(gen_id, None)
            if terminal is not None:
                # The cancel caught a terminal write (in flight, or between its attempts): settle
                # THAT write first. A hand-back sent now could overtake it — both are fenced on
                # our id — and throw away a paid package or a content verdict with no log.
                await self._settle_cancelled_terminal(run_id, gen_id, terminal)
            else:
                # Shutdown anywhere else after the lease was taken: hand the run back so the next
                # container can take it at once instead of waiting LEASE_SECONDS for it to expire.
                await self._hand_back(run_id, gen_id, "generation cancelled (shutdown)")
            raise
        except Exception as e:
            logger.error("marketing script: generation CRASHED run_id=%s generation=%s (%s: %s) — "
                         "handing the run back", run_id, gen_id, type(e).__name__, e, exc_info=True)
            await self._finish(run_id, gen_id, {
                "status": SELECTED,
                "retry_not_before": _iso(_now() + RETRY_AFTER_GEMINI_FAILURE),
                "last_error": f"crashed: {type(e).__name__}: {e}"[:2000],
            })

    async def _settle_cancelled_terminal(self, run_id: str, gen_id: str,
                                         slot: _TerminalWrite) -> None:
        """A cancel (shutdown) arrived during a terminal write. Everything here shares ONE
        HAND_BACK_TIMEOUT_SECONDS budget (the lifespan gives shutdown 5 s in all):

        * the statement is waited for, never raced: it LANDED → recorded, nothing to hand back;
          it matched nothing → the row is not ours, nothing to hand back; it did not answer in
          time → nothing is sent (a hand-back could overtake it), the lease is the backstop and
          a late landing is logged;
        * it answered with an ERROR (in flight, or the cancel caught `_finish` between attempts)
          → the SAME fenced patch is re-sent once, ordered after it: it keeps the package / the
          verdict / the back-off that a hand-back would discard. Only if that fails too is the run
          handed back, in whatever budget is left."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HAND_BACK_TIMEOUT_SECONDS
        if slot.fut is not None:
            answer = await self._await_terminal(run_id, gen_id, slot, slot.fut, deadline)
            if answer is _STILL_IN_FLIGHT:
                return
            if not isinstance(answer, BaseException):
                self._log_settled_terminal(run_id, gen_id, slot, answer, "during the shutdown")
                return
        left = deadline - loop.time()
        if left <= 0:
            logger.log(_unrecorded_level(slot),
                       "marketing script: no time left to re-send the %s (status=%s) of run %s by "
                       "generation %s after the cancel — the lease (%ds) is the backstop%s",
                       slot.what, slot.patch.get("status"), run_id, gen_id, LEASE_SECONDS,
                       _unrecorded_note(slot))
            return
        logger.warning("marketing script: the %s (status=%s) of run %s by generation %s answered "
                       "with an error before the cancel settled it — re-sending it once inside "
                       "the shutdown budget", slot.what, slot.patch.get("status"), run_id, gen_id)
        resend = asyncio.ensure_future(self.runs.update_script_where(
            run_id, {**slot.patch, "lease_until": None},
            expect={"status": GENERATING, "generation_id": gen_id},
        ))
        answer = await self._await_terminal(run_id, gen_id, slot, resend, deadline)
        if answer is _STILL_IN_FLIGHT:
            return
        if not isinstance(answer, BaseException):
            self._log_settled_terminal(run_id, gen_id, slot, answer, "on its shutdown re-send",
                                       maybe_ours=True)
            return
        # Both answered with an error, so a fenced hand-back is ordered after them.
        logger.log(_unrecorded_level(slot),
                   "marketing script: the %s (status=%s) of run %s by generation %s failed again on "
                   "its shutdown re-send — handing the run back instead%s", slot.what,
                   slot.patch.get("status"), run_id, gen_id, _unrecorded_note(slot))
        await self._hand_back(run_id, gen_id, "generation cancelled (shutdown)",
                              timeout=max(deadline - loop.time(), 0.0))

    async def _await_terminal(self, run_id: str, gen_id: str, slot: _TerminalWrite,
                              fut: "asyncio.Future", deadline: float) -> Any:
        """The statement's answer (a row, None, or the exception it raised), or
        `_STILL_IN_FLIGHT` when it did not answer by `deadline` — then a done-callback logs
        where it landed, and the caller must send nothing that could overtake it."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(asyncio.shield(fut), max(deadline - loop.time(), 0.0))
        except asyncio.TimeoutError:
            logger.warning("marketing script: the %s (status=%s) of run %s by generation %s is still "
                           "in flight after the %.1fs shutdown budget — not handing back (a "
                           "hand-back could overtake it); the lease (%ds) is the backstop",
                           slot.what, slot.patch.get("status"), run_id, gen_id,
                           HAND_BACK_TIMEOUT_SECONDS, LEASE_SECONDS)
            fut.add_done_callback(lambda f: self._log_late_terminal(run_id, gen_id, slot, f))
            return _STILL_IN_FLIGHT
        except asyncio.CancelledError:
            logger.warning("marketing script: settling the %s (status=%s) of run %s by generation %s "
                           "was INTERRUPTED — the lease (%ds) is the backstop", slot.what,
                           slot.patch.get("status"), run_id, gen_id, LEASE_SECONDS)
            raise
        except Exception as e:
            logger.warning("marketing script: the %s (status=%s) of run %s by generation %s failed "
                           "(%s: %s)", slot.what, slot.patch.get("status"), run_id, gen_id,
                           type(e).__name__, e)
            return e

    def _log_settled_terminal(self, run_id: str, gen_id: str, slot: _TerminalWrite, answer: Any,
                              when: str, *, maybe_ours: bool = False) -> None:
        status = slot.patch.get("status")
        if answer is not None:
            logger.info("marketing script: the %s of run %s by generation %s LANDED %s (status=%s) "
                        "— recorded; nothing to hand back", slot.what, run_id, gen_id, when, status)
            return
        logger.log(_unrecorded_level(slot),
                   "marketing script: the %s (status=%s) of run %s by generation %s matched nothing "
                   "%s — %s; nothing to hand back%s", slot.what, status, run_id, gen_id, when,
                   "an earlier attempt may have landed before its response was lost, or the run "
                   "moved on" if (maybe_ours or slot.raised) else
                   "another generation holds the run, or the row is final",
                   _unrecorded_note(slot))

    def _log_late_terminal(self, run_id: str, gen_id: str, slot: _TerminalWrite,
                           f: "asyncio.Future") -> None:
        """Done-callback for a terminal statement that outlived the shutdown budget."""
        if f.cancelled():
            return
        exc = f.exception()  # retrieved: never "exception was never retrieved"
        status = slot.patch.get("status")
        if exc is not None:
            logger.warning("marketing script: the orphaned %s (status=%s) of run %s by generation %s "
                           "failed after the shutdown wait (%s: %s) — the lease (%ds) is the "
                           "backstop", slot.what, status, run_id, gen_id, type(exc).__name__, exc,
                           LEASE_SECONDS)
        elif f.result() is not None:
            logger.info("marketing script: the orphaned %s of run %s by generation %s landed after "
                        "the shutdown wait (status=%s) — recorded", slot.what, run_id, gen_id, status)
        else:
            logger.log(_unrecorded_level(slot),
                       "marketing script: the orphaned %s (status=%s) of run %s by generation %s "
                       "matched nothing after the shutdown wait%s", slot.what, status, run_id,
                       gen_id, _unrecorded_note(slot))

    async def _run_date_of(self, run_id: str, row: Dict[str, Any]) -> date:
        raw = row.get("run_date")
        if raw:
            return date.fromisoformat(str(raw)[:10])
        run = await self.runs.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        return date.fromisoformat(str(run["run_date"])[:10])

    async def _generate_owned(self, run_id: str, row: Dict[str, Any], gen_id: str) -> None:
        source_ref = row.get("source_ref")
        try:
            run_date = await self._run_date_of(run_id, row)
        except Exception as e:
            logger.warning("marketing script: ledger FAILED before the model call run_id=%s "
                           "generation=%s source=%s (%s: %s) — handing the run back", run_id, gen_id,
                           source_ref, type(e).__name__, e)
            await self._finish(run_id, gen_id, {
                "status": SELECTED,
                "retry_not_before": _iso(_now() + RETRY_AFTER_GEMINI_FAILURE),
                "last_error": f"ledger: {type(e).__name__}: {e}"[:2000],
            })
            return
        item = content_pool.get_item(source_ref) if source_ref else None
        template = selection.TEMPLATES_BY_ID.get(row.get("template_id") or "")
        if item is None or template is None or not item.eligible:
            # The bundle changed under a selected run (item removed/excluded by a deploy).
            outcome = await self._finish(run_id, gen_id, {
                "status": REJECTED, "reject_reason": REASON_SOURCE_INELIGIBLE,
                "last_error": f"source {source_ref!r} is no longer eligible",
            })
            logger.error("marketing script: selected source no longer eligible run_id=%s "
                         "generation=%s source=%s template=%s — rejected for the day (%s)",
                         run_id, gen_id, source_ref, row.get("template_id"), outcome)
            return
        generate = self._generate_fn()
        started = _now()
        logger.info("marketing script GENERATING run_id=%s generation=%s (#%d; content %d/%d, "
                    "failures %d/%d) source=%s template=%s", run_id, gen_id, _int(row.get("generations")),
                    _int(row.get("content_rejections")), MAX_GENERATIONS, _failures(row) - 1,
                    MAX_WRITER_FAILURES, source_ref, template.id)
        # The image-posts switch, read ONCE for this generation (drop-1 review, server:F4): the writer
        # is told it (off: an image problem alone buys no repair round and never decides which round
        # is kept),
        # and `_frozen_output` freezes the formats with the SAME value — a flip during a slow
        # generation lands on the next one, never half on this one.
        image_posts = bool(settings.MARKETING_IMAGE_POSTS)
        try:
            result = await generate(
                item, template, run_date, generation_id=gen_id,
                allow_x_url=bool(settings.MARKETING_X_ALLOW_URLS),
                image_posts=image_posts,
                # What the captions' code-owned value line may say about the app (nothing /
                # pre-order / on the App Store), read at WRITE time like allow_x_url: the accepted
                # package is immutable and create_posts copies its captions word for word. Inside
                # the try, so an unexpected raise is a recorded writer failure, never a crash.
                store_state=smart_link.store_state(),
                # The semantic compliance judge (judge.py). No default in the writer: the mode is
                # always stated here, and anything unrecognised means `enforce`.
                judge_mode=settings.MARKETING_JUDGE_MODE,
                before_call=lambda: self._refresh_lease(run_id, gen_id),
            )
        except LeaseLost as e:
            logger.warning("marketing script: %s — stopping without a write (tokens spent and not "
                           "recorded: %d)", e, _spent(e))
            return
        except Exception as e:
            await self._record_failure(run_id, row, gen_id, e)
            return

        elapsed = (_now() - started).total_seconds()
        prior_tokens = _int(row.get("tokens_used"))
        if result.status == ACCEPTED and result.package:
            sheet = _fact_sheet_snapshot(item)
            frozen = row.get("fact_sheet") if isinstance(row.get("fact_sheet"), dict) else {}
            if isinstance(frozen.get("selection"), dict):
                # Drop 2: the day's series plan + fallback trail, written at selection — kept, so the
                # run mirror and the posts can still say why the day became a lesson.
                sheet["selection"] = frozen["selection"]
            if frozen.get("sentences") is not None and frozen.get("sentences") != sheet["sentences"]:
                logger.warning("marketing script: the fact sheet of %s changed since selection "
                               "run_id=%s generation=%s — recording the one the package was grounded on",
                               source_ref, run_id, gen_id)
            output = await self._frozen_output(result.package, run_date, run_id=run_id, gen_id=gen_id,
                                               image_posts=image_posts)
            outcome = await self._finish(run_id, gen_id, {
                "status": ACCEPTED, "output": output, "violations": result.violations,
                "model": result.model, "prompt_version": result.prompt_version,
                "tokens_used": prior_tokens + _int(result.tokens_used),
                "fact_sheet": sheet, "last_error": None,
            })
            if outcome == WRITTEN:
                logger.info("marketing script ACCEPTED run_id=%s generation=%s source=%s outlets=%s "
                            "dropped=%s formats=%s tokens=%d in %.1fs", run_id, gen_id, source_ref,
                            sorted((result.package.get("posts") or {}).keys()),
                            sorted((result.package.get("dropped_outlets") or {}).keys()),
                            output.get("post_formats"), _int(result.tokens_used), elapsed)
            else:
                logger.error("marketing script: an ACCEPTED package was NOT recorded run_id=%s "
                             "generation=%s source=%s (%s) tokens=%d — a later generation re-bills it",
                             run_id, gen_id, source_ref, outcome, _int(result.tokens_used))
            return
        content_after = _int(row.get("content_rejections")) + 1
        final = content_after >= MAX_GENERATIONS
        patch: Dict[str, Any] = {
            "status": REJECTED if final else SELECTED,
            "violations": result.violations,
            "content_rejections": content_after,
            "tokens_used": prior_tokens + _int(result.tokens_used),
            "last_error": "content rejected: " + ", ".join(_codes(result.violations))[:1900],
        }
        if final:
            patch["reject_reason"] = REASON_CONTENT
        outcome = await self._finish(run_id, gen_id, patch)
        if outcome == WRITTEN:
            logger.warning("marketing script generation REJECTED run_id=%s generation=%s content=%d/%d "
                           "final=%s codes=%s", run_id, gen_id, content_after, MAX_GENERATIONS, final,
                           _codes(result.violations))
        else:
            logger.error("marketing script: a content rejection was NOT recorded run_id=%s "
                         "generation=%s (%s) codes=%s", run_id, gen_id, outcome, _codes(result.violations))

    async def _frozen_output(self, package: Dict[str, Any], run_date: date, *, run_id: str,
                             gen_id: str, image_posts: bool) -> Dict[str, Any]:
        """`freeze_post_formats` at write time, once. `image_posts` is MARKETING_IMAGE_POSTS as the
        caller read it ONCE when the generation started — the very value the writer was given
        (drop-1 review, server:F4), so it is never read again here; MARKETING_X_IMAGES and the
        holder's capability are read NOW. An unexpected
        raise must not throw away a paid, accepted package: it is logged at ERROR and the package is
        stored WITHOUT `post_formats` — which reads back exactly as a script accepted before image
        posts existed (every post text or video, as before).

        With MARKETING_IMAGE_POSTS on, `image` is frozen only when the worker that HOLDS the run now
        declared it can render the post image (`_holder_renders_images`): an older worker image sends
        text specs, and `create_posts` would refuse every post of the day — the videos included — if
        the formats said image. With the switch off the run is not read at all (as before). A TEMPLATE
        output's image is its `image_spec`, which only a holder that also declared
        WORKER_CAPABILITY_NEWS_TEMPLATES can draw (a re-claim by an older image during the build) — and,
        for a drop-2b layout (WORKER_LAYOUTS_2B: `pair`, `grid`), WORKER_CAPABILITY_LAYOUTS_2B too."""
        image_posts = bool(image_posts)
        if image_posts:
            template = package.get("authorship") == TEMPLATE_AUTHORSHIP
            spec = package.get("image_spec") if template else None
            image_posts = await self._holder_renders_images(
                run_id, gen_id, template=template,
                image_layout=spec.get("layout") if isinstance(spec, Mapping) else None)
        try:
            return freeze_post_formats(
                package, run_date, image_posts=image_posts,
                x_images=bool(settings.MARKETING_X_IMAGES), run_id=run_id,
            )
        except Exception as e:
            logger.error("marketing script: could not freeze the post formats run_id=%s generation=%s "
                         "(%s: %s) — storing the package without them (no image posts this run)",
                         run_id, gen_id, type(e).__name__, e, exc_info=True)
            # A template's own image_footer is part of what create_posts re-checks: never dropped here.
            drop = (("post_formats",) if package.get("authorship") == TEMPLATE_AUTHORSHIP
                    else ("post_formats", "image_footer"))
            return {k: v for k, v in package.items() if k not in drop}

    async def _holder_renders_images(self, run_id: str, gen_id: str, *, template: bool = False,
                                     image_layout: Any = None) -> bool:
        """Did the worker that holds `run_id` declare WORKER_CAPABILITY_POST_IMAGE on its claim (the
        server-owned `metadata.worker_capabilities`, replaced by every claim that takes the run) — and,
        for a `template` output, WORKER_CAPABILITY_NEWS_TEMPLATES too (only that worker draws an
        `image_spec`; an older one would draw the alt text, which the server refuses), plus
        WORKER_CAPABILITY_LAYOUTS_2B when its `image_layout` is a drop-2b layout (WORKER_LAYOUTS_2B: a
        drop-2a image refuses a `pair` / `grid` loudly, failing the day)? Read at write
        time, so a re-claim by another worker during a slow generation is honoured. False — text posts,
        which every worker makes — when it did not, or when the run cannot be read (both logged
        WARNING)."""
        try:
            run = await self.runs.get_run(run_id)
        except Exception as e:
            logger.warning("marketing script: could not read run_id=%s generation=%s to check its worker can "
                           "render the post image (%s: %s) — this run's posts are frozen as text",
                           run_id, gen_id, type(e).__name__, e)
            return False
        if run is None:
            logger.warning("marketing script: run_id=%s generation=%s not found when its formats were frozen — "
                           "this run's posts are frozen as text", run_id, gen_id)
            return False
        declared = run_worker_capabilities(run)
        layouts_2b = template and image_layout in WORKER_LAYOUTS_2B
        needed = ([WORKER_CAPABILITY_POST_IMAGE] + ([WORKER_CAPABILITY_NEWS_TEMPLATES] if template else [])
                  + ([WORKER_CAPABILITY_LAYOUTS_2B] if layouts_2b else []))
        missing = [c for c in needed if c not in declared]
        if not missing:
            return True
        drop = ("drop-2b" if WORKER_CAPABILITY_LAYOUTS_2B in missing else "drop-2" if template else "drop-1")
        logger.warning("marketing script: MARKETING_IMAGE_POSTS is on, but the worker holding run_id=%s "
                       "(worker_version=%r, attempt %s) did not declare %s on its claim — an older worker "
                       "image; this run's posts are frozen as TEXT (deploy the %s marketing worker)",
                       run_id, str(run.get("worker_version") or "")[:64], run.get("attempts"),
                       " + ".join(repr(c) for c in missing), drop)
        return False

    async def _record_failure(self, run_id: str, row: Dict[str, Any], gen_id: str, e: Exception) -> None:
        """A generation that ended without a content verdict. Counts against MAX_WRITER_FAILURES
        (never the content cap) and records the tokens it already spent (contract (2))."""
        spent = _spent(e)
        if isinstance(e, MarketingRunError):
            logger.warning("marketing script: ledger FAILED during generation run_id=%s generation=%s "
                           "(%s: %s) tokens=%d", run_id, gen_id, type(e).__name__, e, spent)
        else:
            from app.integrations.gemini import is_transient_gemini_error

            transient = is_transient_gemini_error(e)
            (logger.warning if transient else logger.error)(
                "marketing script: writer FAILED run_id=%s generation=%s transient=%s tokens=%d (%s: %s)",
                run_id, gen_id, transient, spent, type(e).__name__, e, exc_info=not transient,
            )
        failures = _failures(row)  # this generation included
        final = failures >= MAX_WRITER_FAILURES
        patch: Dict[str, Any] = {
            "status": REJECTED if final else SELECTED,
            "tokens_used": _int(row.get("tokens_used")) + spent,
            "last_error": f"{type(e).__name__}: {e}"[:2000],
        }
        if final:
            patch["reject_reason"] = REASON_WRITER_UNAVAILABLE
        else:
            patch["retry_not_before"] = _iso(_now() + RETRY_AFTER_GEMINI_FAILURE)
        outcome = await self._finish(run_id, gen_id, patch)
        if outcome != WRITTEN:
            logger.error("marketing script: a writer failure was NOT recorded run_id=%s generation=%s "
                         "(%s) tokens=%d — the lease expires and the next kick counts it",
                         run_id, gen_id, outcome, spent)
        elif final:
            logger.warning("marketing script REJECTED for the day run_id=%s reason=%s failures=%d "
                           "last_error=%s", run_id, REASON_WRITER_UNAVAILABLE, failures,
                           f"{type(e).__name__}: {e}"[:300])

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Lifespan teardown: cancel in-flight generations (each hands its run back)."""
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                logger.warning("marketing script: %s still running after the %.1fs shutdown wait — "
                               "its lease (%ds) is the backstop", t.get_name(), timeout, LEASE_SECONDS)


def _codes(violations: Any) -> List[str]:
    out = []
    for v in violations or []:
        code = v.get("code") if isinstance(v, dict) else None
        if code and code not in out:
            out.append(code)
    return out


_service: Optional[MarketingScriptService] = None


def get_marketing_script_service() -> MarketingScriptService:
    global _service
    if _service is None:
        _service = MarketingScriptService()
    return _service
