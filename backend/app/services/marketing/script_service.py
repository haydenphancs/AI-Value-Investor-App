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
falls through to the next (recorded in `fact_sheet.selection.trail`); a MAIN chain always ends in the
lesson.

The spotlight lane (migration 210, 2026-11-15): a SPOTLIGHT run (`marketing_runs.lane`) reads its own calendar
(`selection.spotlight_plan_for`), gated by the same classes and per-series switch, and its chain ends in a SKIP,
never the lesson: a spotlight day where nothing qualifies (a holder that cannot draw a template included) writes
ONE `rejected` script, reason `nothing_qualified`, carrying its selection trail, and the run closes `skipped`. Both
lanes' template builds keep away from what the OTHER lane took (`_cross_lane`): its same-day ledger refs, the
series it used that day (trail outcome `other_lane`) and the LEAD company it drew in the last
CROSS_LANE_COMPANY_DAYS ET days (a spotlight checks both lanes' rows, the main lane only spotlight rows — so with
no spotlight row the main lane's day is byte for byte what it was). An exclusion is never a template refusal: it
never reaches the source's refusal memo (`_report_template_refusal`).

The fix round C0 (2026-11-16, the second lane review): the spotlight's chain never holds a series the MAIN lane's
chain holds that day (`selection.spotlight_chain` — a spotlight Money Map used to push the main lane's same-day Money
Map into a second weekly lesson), and a day left with no spotlight series is no spotlight day (the claim gate, the
kick and the run-health checks share `selection.spotlight_day_problem`); rule (b) compares a lead by its issuer root
and display name (`lead_keys`), and a main run's cross-lane read reaches run_date + 1. The insider spotlight's checked
Form 4 window rides on its trail entry (`TRAIL_WINDOW_KEY`) and anchors the next window
(`runs.last_checked_window` → ``spotlight_checked_through``).

Per-post art (workstream C, owner decisions 2026-11-15 — "Google draws, Claude checks"): with MARKETING_ART_ENABLED
on, a template build makes ONE checked picture for its chosen record BEFORE its INSERT (`_attach_art` →
`post_art.make_post_art`), under min(MARKETING_ART_BUDGET_SECONDS, what is left of TEMPLATE_DAY_BUDGET_SECONDS), gated
on the monthly budget (the ET month's `output.art.usage`, two runs a day), the single-company record (decision 4), a
picture or video post that day and the holder's `post_art` (read from the freeze's one holder read). The record and
its spend ride in `output.art` (tokens_used stays 0); the read-back carries `art` only while the switch is on. Art
never refuses, skips or fails a day.

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
import dataclasses
import copy
import functools
import json
import logging
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from collections import OrderedDict
from typing import Any, Collection, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.config import settings
from app.schemas.marketing import (
    CONTENT_CLASSES,
    EXTRA_FORMATS_KEY,
    EXTRA_VIDEO_PLATFORMS,
    IMAGE_FALLBACK_REASONS,
    IMAGE_PLATFORMS,
    LESSON_VIDEO_FEATURES,
    POST_FORMATS_BY_PLATFORM,
    READBACK_LOOK,
    RUN_LANE_MAIN,
    RUN_LANE_SPOTLIGHT,
    SCRIPT_REJECT_REASONS,
    TEMPLATE_AUTHORSHIP,
    TEMPLATE_VIDEO_FEATURES,
    THREADS_VIDEO_CAPTION_MAX_BYTES,
    VIDEO_END_CARD_CTA,
    VIDEO_FEATURE_DATA_CARD,
    VIDEO_FEATURE_HOOK_CARD,
    VIDEO_FEATURES,
    VIDEO_FEATURES_KEY,
    VIDEO_LAYOUT_PER_LINE,
    VIDEO_LEGACY_CTA,
    VIDEO_POSTS_KEY,
    WORKER_CAPABILITY_LAYOUT_FEATURE,
    WORKER_CAPABILITY_LAYOUTS_2B,
    WORKER_CAPABILITY_LOOK_V23,
    WORKER_CAPABILITY_NEWS_TEMPLATES,
    WORKER_CAPABILITY_POST_ART,
    WORKER_CAPABILITY_POST_IMAGE,
    WORKER_CAPABILITY_THEME_ANGLES,
    WORKER_CAPABILITY_VIDEO_V2,
    WORKER_LAYOUTS_2B,
    WORKER_LAYOUTS_FEATURE,
    image_post_problem,
    layout_capabilities,
    normalize_image_post,
    parse_content_classes,
)
from app.services.marketing import content_pool, post_copy, selection, smart_link
# Company Weekly (drop 2): the pure halves of the template day — records + fact sheet, the templates,
# the on-screen allow-list and the logo header check. FMP-free (PURE_MODULES). The ONE FMP door, the
# adapter, is imported lazily inside `MarketingScriptService._news_source_fn` and nowhere else.
from app.services.marketing import company_news_rules as news_rules
from app.services.marketing import logo_check, news_templates, template_onscreen
# Per-post art (workstream C, 2026-11-16): the art step of a template day (`_attach_art`). Model-free and FMP-free
# at import (its Claude / Gemini clients load inside its functions).
from app.services.marketing import post_art
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
    MarketingRequestInvalid,
    claim_problem,
    data_card_strings,
    frozen_post_formats,
    frozen_video_features,
    get_marketing_run_service,
    held_problem,
    hook_card_problem,
    hook_card_text,
    run_date_et,
    run_lane,
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
    return attempts * per_attempt + backoff + claude_first_seconds()


def claude_first_seconds() -> float:
    """What asking Claude FIRST adds to one model call (llm_provider, 2026-10-30): one attempt to
    CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS before the Gemini fallback — 0 while no marketing role is on Claude
    (switch and key), so the default lease and owner-life numbers are unchanged. Read at import, like
    the Gemini settings above: a marketing switch takes effect with the deploy that sets it."""
    from app.services.llm_provider import any_marketing_role_on_claude

    if not any_marketing_role_on_claude():
        return 0.0
    return max(float(settings.CAYDEX_CLAUDE_REQUEST_TIMEOUT_SECONDS), 0.0)


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
#: when the script row carries no run_date; `_frozen_output` reads the run once more on the ACCEPTED
#: path (`_read_holder`) while MARKETING_IMAGE_POSTS is on (drop 1) or a MARKETING_VIDEO_FEATURES feature
#: applies (Batch 2) — ONE read serving both capability decisions — after the last model call and before
#: the terminal write, so it counts on every generation's worst case (it was missing: 4577 s against a
#: real 4697 s, past OWNER_ALIVE_SECONDS' 4637 s). A second read here would need this to be 2.
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
    call, each at its worst) + the holder-capability read of an accepted package (image posts or a
    video feature on — one read for both) + one terminal write at its worst. 4697 s at defaults with four model calls —
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
#: Per-post art (workstream C, decision 11): the build AND its art step, from the build's start. The art step gets
#: min(MARKETING_ART_BUDGET_SECONDS, what is left of this) and is not started with less than ART_MIN_START_SECONDS
#: (no art: `timeout`, nothing spent). Why a shared ceiling rather than the art's own budget on top of the build's:
#: the worker's render may START only until WORKER_DEADLINE − STAGE_START_MARGINS["rendered"] (1800 − 840 = 960 s
#: into its tick), and before it come this build, the kicks that collect it (2 × TEMPLATE_KICK_WAIT_SECONDS) and the
#: voice stage at its worst (`marketing.voice.worst_case_seconds`: 630 s) — 280 + 40 + 630 = 950 ≤ 960, pinned by
#: tests/test_marketing_template_flow.py. The two budgets on top of each other (240 + 240 + 40 + 630) would defer a
#: slow day's render to the next hourly tick, an attempt spent on art: a build that used its whole 240 s simply makes
#: no art. The art's own ceiling still bounds the poll: TEMPLATE_BUILD_BUDGET_SECONDS + le(MARKETING_ART_BUDGET_SECONDS)
#: + 2 × TEMPLATE_KICK_WAIT_SECONDS < SCRIPT_POLL_BUDGET_SECONDS (pinned too).
TEMPLATE_DAY_BUDGET_SECONDS = 280.0
#: The least time the art step starts with: the minimums of post_art's happy path (brief, image, normalise, check,
#: store — 55 s), so a started run can always reach its store.
ART_MIN_START_SECONDS = sum(post_art.STEP_SECONDS[s][0] for s in post_art.HAPPY_PATH)
#: The process-local art memo's size (`_attach_art`, keyed (run_id, ref)): a rebuild of the same candidate in this
#: process (a build that crashed after its art) reuses the record — failures included — and never pays twice.
ART_MEMO_MAX = 32
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
#:   theme_explainer 120 s — news-v23 phase 3 (the weekly theme post, Wednesday's lead): ~10-30 s normally (≤ 2 themes
#:                           × 1 profile batch, then angles until one has 4 companies: breakdowns, ONE market-value batch,
#:                           annual statements, per-issuer Form 4 reads or the Congress walk). Fix round 2026-11-20 (five
#:                           angles, ≤ 5 attempts — the old 60 s / "~47 s worst" counted two angles): each angle runs under
#:                           its OWN share (`company_news_adapter.THEME_ANGLE_BUDGET_SECONDS`: 21 / 8 / 16 / 28 / 32 s —
#:                           phase 4, 2026-11-20: "how they make money" reads every member, two waves of 12 and one of
#:                           older-years reads, the market-value batch beside them), so a slow source loses its angle's
#:                           turn, never the series; the worst before a record is
#:                           `company_news_adapter.theme_worst_seconds()` ≈ 118 s (2 profile rounds + the five shares).
#:                           Wednesday's chain then still leaves earnings and Money Map their whole 45 s each (122 + 47 +
#:                           45 ≤ 240 — pinned by a test). Owner decisions 2026-11-20 (the quiet-week fallback): it also
#:                           runs right AFTER the insider series — Monday's ceo_buys → insider_buys (47 + 47 at their
#:                           worst) and the spotlight's insider list (62) — and starts there with its WHOLE 120 s (pinned:
#:                           94 + 122 ≤ 240, 62 + 122 ≤ 240); what follows it takes what is left at the worst (Monday's
#:                           Money Map ≥ 24 s, an in-season spotlight's 13F spotlight ≥ 56 s — pinned). The one chain
#:                           where the theme cannot have its whole 120 s is the 13F-LEAD spotlight day (13F spotlight 122
#:                           + insider list 62): it starts with ≥ 56 s there (pinned), its angles' own shares cut short
#:                           by the series' deadline ("a slow source loses its angle's turn").
#: The spotlight lane (2026-11-15), declared with their collectors and shipped with their templates (news-v22):
#:   insider_spotlight     60 s — ceo_buys' path: ≤ 2 walks (the second only when the feed is a day behind) of
#:                           ≤ 3 pages, 1 profile batch, ≤ 11 per-issuer reads (memoized, one per issuer however
#:                           many people; owner decisions 2026-11-20: the insider LIST keeps up to 8 issuers). The
#:                           one-issuer post's revenue breakdowns are gone with it.
#:   thirteen_f_spotlight 120 s — the roundup's walkers WITHOUT Berkshire (reserved for the main lane): the club's
#:                           stored builds, then ≤ 2 live registry builds. On the 13F-lead spotlight day it runs FIRST
#:                           with its whole 120 s (pinned); since the theme follows the insider list (owner decisions
#:                           2026-11-20) Money Map is reached on that day only with what the three before it left.
SERIES_BUDGET_SECONDS: Dict[str, float] = {
    "thirteen_f": 150.0,
    "congress_count": 60.0,
    "company_stakes": 45.0,
    "earnings": 45.0,
    "theme_explainer": 120.0,
    "insider_spotlight": 60.0,
    "thirteen_f_spotlight": 120.0,
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
#: The ledger read a news day makes, at the DEFAULT cadence: every posting day of the recent window (news refs
#: included, so the adapter's ledger check sees them); the lesson rotation filters it with `selection.lesson_refs`.
#: Since Batch 3 (2026-10-20) the posting days are a switch, so `_select` and `_build_template_day` read
#: `selection.news_recent_limit()` at call time (RECENT_WINDOW × the posting days a week: 240 at the default
#: four, 420 at seven); this constant stays that default's value (240).
NEWS_RECENT_LIMIT = RECENT_LIMIT * len(selection.POST_WEEKDAYS)
#: A series' outcome in the day's fallback trail (`fact_sheet.selection.trail`, run `series_trail`). `other_lane`
#: (the spotlight lane, 2026-11-15): the other lane already used the series that day, or drew the lead company of
#: every candidate it offered (`_cross_lane`) — an exclusion, never a refusal.
SERIES_OUTCOMES = ("chosen", "no_candidates", "all_recent", "all_refused", "unavailable", "timeout",
                   "error", "budget", "other_lane", "week_cap")
#: Owner decision 2026-11-27 ("1 backup theme a week (Recommended)": "At most one quiet-week theme per week across both
#: daily posts, so Wednesday almost always keeps a theme."): the trail outcome of a theme BACKUP step (any theme step but
#: the main lane's Wednesday lead, `selection.theme_is_fallback`) in an ET week whose backup was already made, in either
#: lane (`theme_week_fallbacks`) — the series is never asked, so nothing reaches the source's refusal memo (a content
#: outcome: a spotlight day that skips behind it is a quiet day). Its reason is THEME_FALLBACK_WEEK_USED.
THEME_WEEK_CAP = "week_cap"
THEME_FALLBACK_WEEK_USED = "theme_fallback_week_used"
#: The trail outcome of a series the cross-lane rules kept out (`SERIES_OUTCOMES`).
OTHER_LANE = "other_lane"
#: Its reason when the series ran but the other lane had drawn the lead company of every candidate it offered.
OTHER_LANE_COMPANY = "other_lane_company"
#: Its reason when the other lane already used the series that day (the series is never started).
OTHER_LANE_SERIES = "posted by the other lane today"
#: Rule (b) of the cross-lane exclusion (the spotlight lane, 2026-11-15): a candidate whose LEAD company
#: (`lead_company`, compared by `lead_keys` — its issuer root and display name, so GOOGL and GOOG are one company: C0,
#: 2026-11-16) the other lane drew on a run dated within this many ET days before the run date — or on it —
#: is not offered: [run_date − 2, run_date] covers at least 48 h between two posts about one company (Upload-Post's
#: "similar content within 48 h" refusal). A spotlight run checks both lanes' rows (its own earlier days included);
#: the main lane checks only spotlight rows (through run_date + 1), so with no spotlight row its day is unchanged.
CROSS_LANE_COMPANY_DAYS = 2
#: How many trail entries reach the run mirror and each post (`series_trail`) — run_service's bound.
SERIES_TRAIL_MAX = RUN_SERIES_TRAIL_MAX
#: The one series whose `candidates()` call takes the spotlight lane's ledger inputs (B decisions 5-6, 2026-11-15:
#: `_spotlight_source_kw`): the insider spotlight's window start and issuer cooldown. Every other series — the 13F
#: spotlight included, whose one-post-per-filer rule reads the `exclude` refs — is asked exactly as before.
SPOTLIGHT_CONTEXT_SERIES = "insider_spotlight"
#: The one series whose `candidates()` call takes the weekly theme post's inputs (news-v23 phase 3, `_theme_source_kw`):
#: the build's own theme ledger read and the angles the run's worker can draw.
THEME_CONTEXT_SERIES = "theme_explainer"
#: The angle a holder without WORKER_CAPABILITY_THEME_ANGLES can draw: "how they make money", on the `grid` every
#: drop-2b worker draws (sent without the new keys, `_without_theme_angles`).
THEME_ANGLES_WITHOUT_CAPABILITY: Tuple[str, ...] = ("make_money",)
#: The key of the CHECKED Form 4 window on that series' trail entry (C0, 2026-11-16: ``{"start", "end"}`` ISO dates,
#: recorded whenever its collector checked a window — chosen, refused or nothing qualified — from
#: `Candidates.checked_window`); the next insider spotlight's window starts the day after its end
#: (`runs.last_checked_window`). Pinned == run_service.TRAIL_WINDOW_KEY. Never in a run / post `series_trail` mirror.
TRAIL_WINDOW_KEY = "window"
#: The series whose post image is a drop-2b layout (WORKER_LAYOUTS_2B: theme_explainer `grid`; company_stakes drew
#: the `pair` until news-v23, a `rows` list since), derived from the templates' own specs. Only a holder that declared
#: WORKER_CAPABILITY_LAYOUTS_2B draws them: `_select` drops them from the chain for any other holder.
LAYOUTS_2B_SERIES: FrozenSet[str] = frozenset(
    sid for sid, spec in news_templates.SERIES_SPECS.items() if spec.layout in WORKER_LAYOUTS_2B)
#: The series whose post image is the `feature` layout (WORKER_LAYOUTS_FEATURE, 2026-11-15: the spotlight
#: series' art-band picture), derived from the templates' own specs exactly like LAYOUTS_2B_SERIES — empty until
#: a SeriesSpec names that layout. Only a holder that declared WORKER_CAPABILITY_LAYOUT_FEATURE draws them:
#: `_select` / `_select_spotlight` drop them from the chain for any other holder (WARNING; the chain falls
#: through to its next series, as a refused series does — a spotlight chain ends in its SKIP, never the lesson).
FEATURE_LAYOUT_SERIES: FrozenSet[str] = frozenset(
    sid for sid, spec in news_templates.SERIES_SPECS.items() if spec.layout in WORKER_LAYOUTS_FEATURE)

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
#: A SPOTLIGHT day whose chain reached its SKIP terminal (migration 210): nothing qualified — never the lesson.
REASON_NOTHING_QUALIFIED = "nothing_qualified"

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
#: `writer_service.CLAUDE_USAGE_ATTR` (same contract): the Claude part of those tokens, by model.
_CLAUDE_USAGE_ATTR = "marketing_claude_usage"


def _claude_patch(row: Dict[str, Any], usage: Any, run_id: str, gen_id: str) -> Dict[str, Any]:
    """`{"claude_usage": <the row's value + this generation's>}` when this generation billed Claude
    tokens, else {} — so a generation with no Claude call writes exactly what it wrote before the
    column existed (migration 206). The weekly cost line prices these tokens at Claude's rates and the
    rest of `tokens_used` at Gemini's. A stored value that cannot be read is left as it is (WARNING):
    rewriting it would drop its tokens, and the digest reads that row's Claude part as unreadable."""
    if not usage:
        return {}
    from app.services.llm_provider import merge_claude_usage, parse_claude_usage

    new, prior = parse_claude_usage(usage), parse_claude_usage(row.get("claude_usage"))
    if new is None or prior is None:
        logger.warning("marketing script: run_id=%s generation=%s has an unreadable claude_usage (%s) — "
                       "this generation's Claude tokens are not added to it (they stay in tokens_used)",
                       run_id, gen_id, "the generation's" if new is None else "the stored one")
        return {}
    return {"claude_usage": merge_claude_usage(prior, new)} if new else {}


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


def worker_script(output: Dict[str, Any], *, run_id: Optional[str] = None,
                  capabilities: Optional[Collection[str]] = None) -> Dict[str, Any]:
    """The subset the WORKER needs to voice and render (Phases 3-4). Captions are not in it:
    they are server-authored and reach the ledger only through `create_posts`.

    Drop 1 adds the run's frozen `post_formats`, its `image_post` and the image's `image_footer` —
    all three None for an output accepted before image posts existed (the worker then falls back to
    its own POST_FORMAT, exactly as before). Frozen formats that do not read back (a hand-edited row)
    are sent as None too, logged: `create_posts` refuses every post of such a run, so the day fails
    loudly there rather than on a guess here.

    Batch 2 adds `video_features` and `end_card_cta` (`worker_video_fields`) — on EVERY read-back, even
    as [] (their presence tells a Batch 2 worker this web knows the new end card) — and `data_card_slot`
    (CARDS-B2-1): which of `cards` the data card replaces, None unless `video_features` carries data_card.

    news-v23 (the restyle, 2026-11-19) adds `look` (READBACK_LOOK, on every read-back: the look's own strings are
    allowed), sends a LESSON's `image_footer` as the code-owned IMAGE_FOOTER_HEAD (never the stored copy: one stored
    before the restyle is the long footer), and — given the holder's `capabilities` (None: unknown, the spec as
    stored) — strips the look's ticker chips (and, owner decision 2026-11-20, a Money Map loss year's costs card) from
    the picture spec of a holder without WORKER_CAPABILITY_LOOK_V23 (`_without_look_chips`), and its costs cover
    (`_without_costs_cover`), its list cover (`_without_list_cover`) and its figure label with the figure it explains
    (`_without_figure_label`) from the opening card."""
    try:
        formats = frozen_post_formats(output)
    except ValueError as e:
        logger.error("marketing script: the accepted output's post_formats do not read back (%s) — "
                     "the worker gets none; create_posts will refuse this run's posts", e)
        formats = None
    footer = output.get("image_footer")
    lesson = output.get("authorship", post_copy.AUTHORSHIP_AI) == post_copy.AUTHORSHIP_AI
    if isinstance(footer, str) and footer and lesson:
        # news-v23: a LESSON's picture footer is the code-owned constant (`run_service._check_post_image_text` checks
        # this one, never the stored copy) — a lesson accepted before the restyle stored the long footer
        footer = post_copy.IMAGE_FOOTER_HEAD
    script = {
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
        "look": READBACK_LOOK,
    }
    if capabilities is not None and WORKER_CAPABILITY_LOOK_V23 not in capabilities:
        script["image_spec"] = _without_look_chips(script.get("image_spec"), run_id)
        script["opening_card"] = _without_figure_label(
            _without_list_cover(_without_costs_cover(script.get("opening_card"), run_id), run_id), run_id)
    if capabilities is not None and WORKER_CAPABILITY_THEME_ANGLES not in capabilities:
        script["image_spec"], script["opening_card"] = _without_theme_angles(
            script.get("image_spec"), script.get("opening_card"), output.get("series"), run_id)
    script.update(worker_video_fields(output, script, run_id=run_id))
    return script


#: The most rows an older worker's `rows` walker takes in one section (news-v22's cards.MAX_SECTION_ROWS; news-v23's
#: 13F list draws six). Review of 2026-11-26: the cut itself moved to `template_onscreen` (pure), so the publisher can
#: tell which picture a worker drew (`news_templates.picture_alt`); these names are its aliases.
PRE_V23_MAX_SECTION_ROWS = template_onscreen.PRE_V23_MAX_SECTION_ROWS
_PRE_V23_ROW_DROPS = template_onscreen.PRE_V23_ROW_DROPS
_PRE_V23_SECTION_DROPS = template_onscreen.PRE_V23_SECTION_DROPS
_pre_v23_section = template_onscreen.pre_v23_section


def _without_look_chips(spec: Any, run_id: Optional[str]) -> Any:
    """`spec` (the read-back's `image_spec`) without the news-v23 chips an older worker's CLOSED walker refuses as
    unknown keys — every `rows` row's `chip` and a `bars` header's `chip` — for a holder that did not declare
    WORKER_CAPABILITY_LOOK_V23, and a `rows` section of the 13F list cut to the rows that walker takes
    (`template_onscreen.pre_v23_section`), and a Money Map loss year's costs card (owner decision 2026-11-20 — such a
    worker draws today's flow card and callout, still in the spec). The cut is `template_onscreen.pre_v23_image_spec`
    (pure; this logs). The picture it draws then holds a subset of the stored spec's strings (the server's
    allow-list), so it still registers, and its alt text is the stored `image_post_pre_v23` when the output carries one
    (`news_templates.picture_alt`, review of 2026-11-26). Never cut: a stake row's basis `label` and an earnings row's
    `table` (final review of 2026-11-26 — such a worker re-claiming a run a newer one built gets them, its walker
    refuses the picture and the render fails loudly, never a bare amount or a figureless name; PRE_V23_ROW_DROPS).
    A copy; anything else is returned as it is."""
    out = template_onscreen.pre_v23_image_spec(spec)
    if out is not spec and out != spec:
        logger.warning("marketing script run_id=%s: the run's holder did not declare %s — its %s picture is sent "
                       "without the ticker chips, a loss year's costs card (and rows past %d in a section) its walker "
                       "would refuse", run_id, WORKER_CAPABILITY_LOOK_V23, spec.get("layout"),
                       PRE_V23_MAX_SECTION_ROWS)
    return out


def _without_costs_cover(card: Any, run_id: Optional[str]) -> Any:
    """`card` (the read-back's `opening_card`) without a Money Map loss year's COSTS cover (owner decision 2026-11-20:
    its `costs` card and the `subtitle` drawn above it), which an older worker's CLOSED opening-card walker refuses as
    unknown keys, for a holder that did not declare WORKER_CAPABILITY_LOOK_V23 — such a worker then draws today's opening
    card (`_without_figure_label` takes the figure and its label too): a subset of the stored card's strings, so the
    video still registers. A copy; anything else as it is."""
    if not isinstance(card, dict) or not ({"costs", "subtitle"} & set(card)):
        return card
    logger.warning("marketing script run_id=%s: the run's holder did not declare %s — its opening card is sent without "
                   "the loss year's costs cover its walker would refuse (today's opening card)", run_id,
                   WORKER_CAPABILITY_LOOK_V23)
    return {k: v for k, v in card.items() if k not in ("costs", "subtitle")}


def _without_list_cover(card: Any, run_id: Optional[str]) -> Any:
    """`card` (the read-back's `opening_card`) without the news-v23 LIST cover (`list`: the 13F list, the Congress
    Count, a Form 4 roundup's list — earnings and company stakes reach such a holder only by its re-claim of a run a
    newer worker built, and then their picture fails its render loudly, `_without_look_chips`), which an older worker's
    CLOSED opening-card walker refuses as an unknown key, for a holder that did not declare WORKER_CAPABILITY_LOOK_V23:
    it then draws today's opening card — the kicker, the logo, the chip, the headline — whose strings are a subset of
    the stored card's (the server's allow-list), so the video still registers. A copy; anything else as it is."""
    if not isinstance(card, dict) or "list" not in card:
        return card
    logger.warning("marketing script run_id=%s: the run's holder did not declare %s — its list cover (13F / Congress "
                   "Count / Form 4 roundup) is sent without the list its walker would refuse (today's opening card)",
                   run_id,
                   WORKER_CAPABILITY_LOOK_V23)
    return {k: v for k, v in card.items() if k != "list"}


def _without_figure_label(card: Any, run_id: Optional[str]) -> Any:
    """`card` (the read-back's `opening_card`) without the news-v23 `figure_label` (the Money Map cover's "of every $100
    of revenue was net income") — which an older worker's CLOSED opening-card walker refuses as an unknown key — AND
    without the figure it explains, for a holder that did not declare WORKER_CAPABILITY_LOOK_V23: a bare "$2.94" beside
    "How Costco makes money" would read as something else. Such a worker then draws the kicker, the logo, the chip and
    the headline, a subset of the stored card's strings (the server's allow-list), so the video still registers. A
    copy; anything else as it is."""
    if not isinstance(card, dict) or "figure_label" not in card:
        return card
    logger.warning("marketing script run_id=%s: the run's holder did not declare %s — its opening card is sent without "
                   "the figure and the label its walker would refuse (the headline, the logo and the chip)", run_id,
                   WORKER_CAPABILITY_LOOK_V23)
    return {k: v for k, v in card.items() if k not in ("figure_label", "figure")}


#: news-v23 phase 3: the grid keys a worker without WORKER_CAPABILITY_THEME_ANGLES refuses (its closed walker): a tile's
#: ticker `chip`, the spec's `icon` and its `sub_break` — none of them a string it would otherwise draw.
_THEME_GRID_DROPS = frozenset({"icon", "sub_break"})
_THEME_TILE_DROPS = frozenset({"chip"})


def _without_theme_angles(spec: Any, card: Any, series: Any, run_id: Optional[str]) -> Tuple[Any, Any]:
    """The read-back's picture spec and opening card for a holder that did not declare WORKER_CAPABILITY_THEME_ANGLES
    (news-v23 phase 3): a theme `grid` without its tiles' chips, its icon and its sub-line break, and a THEME post's
    opening card without its list cover (today's opening card: the kicker, the logo, the count, the title) — each a
    subset of the stored strings (the server's allow-list), so it still registers. A `ranked` / `figures` / `ledger` /
    `tally` picture never reaches such a holder at BUILD time: the adapter is told which angles it can draw, and one
    built while such a holder holds the run is frozen as TEXT at write time (`_frozen_output`, `layout_capabilities`).
    One built and frozen by a newer worker and then re-claimed by an older one (a worker rollback mid-run — final
    review of 2026-11-26) is sent UNCHANGED: that worker's walker refuses the layout and its render fails loudly
    (retried, then closed failed at the attempts cap) — never a guessed picture. Copies; anything else as it is."""
    out_spec, out_card = spec, card
    if isinstance(spec, dict) and spec.get("layout") == "grid" and isinstance(spec.get("tiles"), list):
        out_spec = {k: v for k, v in spec.items() if k not in _THEME_GRID_DROPS}
        out_spec["tiles"] = [{k: v for k, v in t.items() if k not in _THEME_TILE_DROPS} if isinstance(t, dict) else t
                             for t in spec["tiles"]]
    if series == THEME_CONTEXT_SERIES and isinstance(card, dict) and "list" in card:
        out_card = {k: v for k, v in card.items() if k != "list"}
    if out_spec != spec or out_card != card:
        logger.warning("marketing script run_id=%s: the run's holder did not declare %s — its theme picture is sent "
                       "without the tiles' chips, icon and sub-line break, and its video without the list cover",
                       run_id, WORKER_CAPABILITY_THEME_ANGLES)
    return out_spec, out_card


def end_card_cta(output: Any) -> str:
    """The CTA the video's end card draws (owner decision 6, 2026-10-15): VIDEO_END_CARD_CTA ("Search Caydex
    on the App Store") only while the accepted output's store state — the one its captions were worded
    with, frozen at write time — says the app is on the store (live or preorder: `post_copy.search_line`,
    the same predicate as the YouTube description's search line and the first comment); VIDEO_LEGACY_CTA
    otherwise (prelaunch — now a mistyped store URL — or an output without a store state). Pure."""
    state = output.get("store_state") if isinstance(output, dict) else None
    return VIDEO_END_CARD_CTA if post_copy.search_line(state) is not None else VIDEO_LEGACY_CTA


def worker_video_fields(output: Dict[str, Any], sent: Mapping[str, Any], *,
                        run_id: Optional[str] = None) -> Dict[str, Any]:
    """Batch 2's read-back fields: `video_features` — the run's frozen features
    (`run_service.frozen_video_features`, read with the output's own kind), minus any the read-back
    `sent` cannot carry: a hook card without a hook that can stand alone (`hook_card_text`: not one drawable
    string, or compliant only beside its first narration line — B2-COMPL-1), a data card without the
    post image it draws (`data_card_strings`, and the `image_spec` / `image_footer` this read-back actually
    sends — a logo entry the worker's copy drops can leave the spec unsent) or without a content card it may
    replace — each drop logged ERROR (a hand-edited row, or one frozen before that check: the freeze never
    stores one now); `end_card_cta` (`end_card_cta`); and `data_card_slot` (CARDS-B2-1, 2026-10-20) — the
    0-based index into `sent["cards"]` of the ONE card the data card replaces
    (`template_onscreen.data_card_slot` on the read-back's own series and cards and the picture's strings: a
    fact card the picture already shows, never a hedge card), set exactly when `video_features` carries
    data_card, else None. Never raises for a dict output."""
    template = output.get("authorship") == TEMPLATE_AUTHORSHIP
    features = list(frozen_video_features(output, template=template, run_id=run_id))
    if VIDEO_FEATURE_HOOK_CARD in features and hook_card_text(output) is None:
        logger.error("marketing script run_id=%s: hook_card is frozen but the accepted hook %s — the worker is "
                     "not sent it (today's opening)", run_id, hook_card_problem(output))
        features.remove(VIDEO_FEATURE_HOOK_CARD)
    picture = data_card_strings(output) if VIDEO_FEATURE_DATA_CARD in features else None
    if VIDEO_FEATURE_DATA_CARD in features and (
            picture is None or sent.get("image_spec") is None or sent.get("image_footer") is None):
        logger.error("marketing script run_id=%s: data_card is frozen but this read-back cannot carry the post "
                     "image it draws — the worker is not sent it (today's text cards)", run_id)
        features.remove(VIDEO_FEATURE_DATA_CARD)
    slot: Optional[int] = None
    if VIDEO_FEATURE_DATA_CARD in features:
        slot = template_onscreen.data_card_slot(sent.get("series"), sent.get("cards"), picture)
        if slot is None:
            logger.error("marketing script run_id=%s: data_card is frozen but none of the read-back's cards is one "
                         "a data card may replace (series %s) — the worker is not sent it (today's text cards)",
                         run_id, str(sent.get("series"))[:40])
            features.remove(VIDEO_FEATURE_DATA_CARD)
    return {"video_features": features, "end_card_cta": end_card_cta(output), "data_card_slot": slot}


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
        "art": _worker_art(output),
    }


def _worker_art(output: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The read-back's `art` (workstream C, decision 11): `post_art.worker_art` — {url, sha256, bytes, width, height}
    of the accepted output's checked art, ONLY while MARKETING_ART_ENABLED is on, read at CALL time (a real kill
    switch: turned off, the next render draws none; the server accepts a missing declaration — art is never
    required), else None (a lesson, no art, a failed record). Never the models, the subject or the usage. A raise
    (a bug) is logged at ERROR and sent as None — the read-back never fails for art."""
    try:
        return post_art.worker_art(output)
    except Exception as e:  # noqa: BLE001 — art is optional: no art, never a failed read-back
        logger.error("marketing script: the accepted output's art could not be read for the worker (%s: %s) — sent "
                     "as None", type(e).__name__, e, exc_info=True)
        return None


def _art_cap_micros() -> Optional[int]:
    """MARKETING_ART_MONTHLY_BUDGET_USD in micro-USD, read now (0 = no art); None — no art, ERROR — for a value that
    is not a finite non-negative number (the Settings field refuses one at deploy; this is defence in depth)."""
    raw = settings.MARKETING_ART_MONTHLY_BUDGET_USD
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
        logger.error("marketing art: MARKETING_ART_MONTHLY_BUDGET_USD=%r is not a finite number of dollars — no art",
                     raw)
        return None
    return int(round(float(raw) * 1_000_000))


#: The primary formats a post takes WITHOUT frozen `post_formats` (a failed freeze): the video platforms post the
#: video, every other platform text (`run_service.frozen_post_formats` → None, the worker's own POST_FORMAT).
_UNFROZEN_VIDEO_PLATFORMS = frozenset(p for p, fmts in POST_FORMATS_BY_PLATFORM.items() if fmts == ("video",))


def _art_day_surfaces(output: Dict[str, Any], subject: "post_art.ArtSubject") -> Tuple[str, ...]:
    """The surfaces of `subject` (decision 4) the DAY can show (decision 11: "a picture or video format on the day"):
    the spotlight picture only when some post was frozen `image`, the cover only when some post is a `video` (frozen,
    or — formats unfrozen — a video platform the output carries copy for). () = a text-only day: no art. Pure."""
    try:
        formats = frozen_post_formats(output)
    except ValueError as e:     # create_posts refuses such a run anyway; no surface is promised for it
        logger.warning("marketing art: the output's post_formats do not read back (%s) — no art surface", e)
        return ()
    if formats is not None:
        taken = set(formats.values())
    else:
        posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
        taken = {"video"} if any(p in _UNFROZEN_VIDEO_PLATFORMS for p in posts) else set()
    need = {post_art.SURFACE_PICTURE: "image", post_art.SURFACE_COVER: "video"}
    return tuple(s for s in subject.surfaces if need.get(s) in taken)


#: A Threads IMAGE post is frozen only while its composed caption fits this many UTF-8 BYTES. Upload-Post
#: splits Threads text over 500 bytes into a thread by default; `threads_long_text_as_post` (sent by
#: `outlet_upload_post`) stops that on /upload_text, but is unverified on /upload_photos — and a split
#: image post would carry the value line, the /go link and the caption disclaimer in a reply. Characters
#: are not bytes: the live value line's em dash alone makes a 500-character caption 502+ bytes. A
#: longer caption keeps Threads on the text route (one post). Drop it once Upload-Post's OpenAPI confirms
#: the flag on the photo route.
THREADS_IMAGE_CAPTION_MAX_BYTES = 500

#: `output.image_fallback` (Batch 1, 2026-10-15): written by `freeze_post_formats` (or by `_frozen_output`
#: when the freeze itself raised) ONLY on a day that should have carried the picture and froze some of
#: those platforms as text instead — `{"reason": <schemas.IMAGE_FALLBACK_REASONS code>, "platforms":
#: [...]}`. `run_service.create_posts` stamps the code on those platforms' text posts; the POSTS decision
#: message and the weekly digest name it in the owner's words. Never read back by the worker.
IMAGE_FALLBACK_KEY = "image_fallback"


def parse_image_platforms(raw: Any) -> Tuple[str, ...]:
    """MARKETING_IMAGE_PLATFORMS as the platforms that may post the day's picture, in IMAGE_PLATFORMS
    order (Batch 1, 2026-10-15). Re-validated here: the Settings validator normalises a deployed value, but
    a value set on the live object (a console, a test) skips it — so "x" (X posts the picture only through
    MARKETING_X_IMAGES) and an unknown token are dropped with an ERROR here too, and a non-string is no
    platform at all (text posts: the fail-closed side). Never raises."""
    if not isinstance(raw, str):
        logger.error("marketing script: MARKETING_IMAGE_PLATFORMS is not a string (%s) — no platform posts the "
                     "picture", type(raw).__name__)
        return ()
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    if "x" in tokens:
        logger.error("marketing script: MARKETING_IMAGE_PLATFORMS lists x — dropped (X posts the picture only "
                     "through MARKETING_X_IMAGES)")
    unknown = sorted(tokens - set(IMAGE_PLATFORMS) - {"x"})
    if unknown:
        logger.error("marketing script: MARKETING_IMAGE_PLATFORMS ignored unknown platform(s) %s",
                     ",".join(t[:24] for t in unknown[:10]))
    return tuple(p for p in IMAGE_PLATFORMS if p in tokens)


def image_platforms_setting() -> Tuple[str, ...]:
    """MARKETING_IMAGE_PLATFORMS, read when called — at WRITE time, like MARKETING_X_IMAGES."""
    return parse_image_platforms(settings.MARKETING_IMAGE_PLATFORMS)


def _picture_platforms(output: Any, listed: FrozenSet[str], x_images: bool) -> List[str]:
    """The platforms the output carries copy for that should post the day's picture: an image-capable
    platform listed in `listed` (MARKETING_IMAGE_PLATFORMS), or X while `x_images` is on. Sorted; tolerant
    of a malformed `posts` (that may be why a freeze failed) — never raises for a dict output."""
    posts = output.get("posts") if isinstance(output, dict) and isinstance(output.get("posts"), dict) else {}
    return sorted(p for p in posts if isinstance(p, str) and "image" in POST_FORMATS_BY_PLATFORM.get(p, ())
                  and (bool(x_images) if p == "x" else p in listed))


def template_picture_outlets(image_posts: bool, x_images: bool, image_platforms: Iterable[str]) -> List[str]:
    """Review 2026-11-20: the platforms whose post will carry a TEMPLATE day's picture, from the switches as a build
    read them — none while image posts are off; else X while `x_images`, and every other picture platform listed in
    `image_platforms` (MARKETING_IMAGE_PLATFORMS). Sorted; `news_templates.compose`'s `picture_outlets`, the very
    predicate `_picture_platforms` applies at the freeze. Pure."""
    if not image_posts:
        return []
    listed = frozenset(p for p in image_platforms if isinstance(p, str) and p in IMAGE_PLATFORMS)
    return sorted(p for p in news_templates.PICTURE_OUTLETS if (bool(x_images) if p == "x" else p in listed))


def freeze_post_formats(output: Dict[str, Any], run_date: date, *, image_posts: bool, x_images: bool,
                        run_id: Optional[str] = None, image_platforms: Optional[Iterable[str]] = None,
                        image_blocked: Optional[str] = None) -> Dict[str, Any]:
    """The accepted output as it is stored (drop 1, contract C3): a COPY of the writer's package with
    the run's formats frozen into it, decided ONCE at write time like the store state — the worker
    learns them only from the script read-back, and `create_posts` records each platform only in its
    frozen format. Pure but for logging; never raises on a malformed `image_post`.

    * `post_formats`: for every platform the package carries copy for — the video platforms
      "video"; the others "image" iff `image_posts` and the platform may post the picture (in
      `image_platforms`; X: `x_images`) and nothing blocks it (`image_blocked`) and the package has a
      usable `image_post` (and, for Threads, its caption fits THREADS_IMAGE_CAPTION_MAX_BYTES); else
      "text".
    * `image_post`: the writer's `{title, paragraphs}` reduced to exactly those keys, or None when
      it is absent or unusable (logged; that day's posts are text — never a rejected package).
    * `image_footer`: `post_copy.image_footer(run_date)`, only when some format is "image".

    Drop 2 (contract D11): a TEMPLATE output (`authorship == "template"`) keeps its OWN `image_footer`
    — composed with the record's source and as-of date, and compared by `news_templates.revalidate` at
    create_posts, so it stays whatever the formats are (a missing one is a ValueError, never replaced)
    — and a platform is "image" only when its `image_spec` also validates against the output's logos
    and that footer (`template_onscreen.validate_image_spec`); otherwise that day's posts are text.

    Batch 1 (2026-10-15):
    * `image_platforms` — the platforms that may post the picture (MARKETING_IMAGE_PLATFORMS, read by
      `_frozen_output` at write time). Default None = every one of `schemas.IMAGE_PLATFORMS`, i.e.
      exactly the drop-1 decision, so a caller that does not pass it freezes the same formats as before.
      Only members of IMAGE_PLATFORMS count: X follows `x_images` alone, and any other name is ignored.
      A platform left off is TEXT on purpose — never a fallback. A string (not a collection of names)
      is a ValueError.
    * `image_blocked` — an IMAGE_FALLBACK_REASONS code the caller already knows stops every picture
      today (`_frozen_output`: the worker holding the run cannot draw it, or the run could not be read);
      every platform freezes as text with that reason. Any other value is a ValueError.
    * `image_fallback` (IMAGE_FALLBACK_KEY) — written ONLY when `image_posts` is on and a platform that
      should have posted the picture (`_picture_platforms`) froze as text anyway: `{"reason": code,
      "platforms": [...]}`, one reason, the first that applies — `image_blocked`; `image_dropped` (no
      usable `image_post`: the writer dropped it — its `dropped_image` says why — or it does not read);
      `image_spec_invalid` (a template whose `image_spec` does not validate); else
      `threads_caption_long` (Threads alone), then `caption_list_long` (review 2026-11-20: a template's
      `text_outlets` — a list post's X / Bluesky caption that could not keep the picture's "+N more"
      promise, written as a text post). Never carried over from the input. With image posts off, or with
      every listed platform posting the picture, there is none.
    * Review 2026-11-20: a TEMPLATE output's `picture_outlets` (the platforms its captions were written to
      ride the picture, as the build read the switches) bounds the picture platforms — one outside it is
      TEXT as if left off (no fallback: its caption is a text post's)."""
    if image_platforms is None:
        listed = frozenset(IMAGE_PLATFORMS)
    elif isinstance(image_platforms, (str, bytes)):
        raise ValueError("image_platforms is a collection of platform names, not one string")
    else:
        listed = frozenset(p for p in image_platforms if isinstance(p, str) and p in IMAGE_PLATFORMS)
    if image_blocked is not None and image_blocked not in IMAGE_FALLBACK_REASONS:
        raise ValueError(f"image_blocked {str(image_blocked)[:40]!r} is not an image-fallback reason "
                         f"(one of {sorted(IMAGE_FALLBACK_REASONS)})")
    out = dict(output)
    out.pop(IMAGE_FALLBACK_KEY, None)   # this freeze's own verdict, never a copied one
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
    spec_ok = True
    if template and image_post is not None:
        problem = template_onscreen.validate_image_spec(
            output.get("image_spec"), template_onscreen.logo_keys(output.get("logos")), footer=template_footer)
        if problem is not None:
            logger.warning("marketing script: the template's image_spec does not validate run_id=%s (%s) — "
                           "that day's image formats fall back to text", run_id, str(problem)[:200])
            spec_ok = False
    # What stops EVERY picture today, in the order the owner is told (one reason per day). None = the
    # picture can go out (or image posts are off), and only Threads' caption size can still keep one
    # platform on text.
    blocked: Optional[str] = None
    if image_posts:
        if image_blocked is not None:
            blocked = image_blocked
        elif image_post is None:
            blocked = "image_dropped"
        elif not spec_ok:
            blocked = "image_spec_invalid"
    pictured = frozenset(_picture_platforms(output, listed, x_images)) if image_posts else frozenset()
    # Review 2026-11-20 — a TEMPLATE output says which platforms its captions were written to ride the picture
    # (`picture_outlets`, as the build read the switches) and which of those could not keep the picture's "+N more"
    # promise (`text_outlets`, written as text posts): a platform outside the first never posts the picture (its
    # caption owes it nothing — as if left off), and one in the second freezes as TEXT (``caption_list_long``)
    frozen_pictured = output.get("picture_outlets") if template else None
    if isinstance(frozen_pictured, list):
        pictured = pictured & frozenset(p for p in frozen_pictured if isinstance(p, str))
    texted = output.get("text_outlets") if template else None
    list_text = frozenset(p for p in texted if isinstance(p, str)) if isinstance(texted, list) else frozenset()
    posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
    formats: Dict[str, str] = {}
    fallen: List[str] = []
    threads_long = False
    list_long = False
    for platform in sorted(posts):
        allowed = POST_FORMATS_BY_PLATFORM.get(platform, ())
        if "video" in allowed:
            formats[platform] = "video"
        elif platform in pictured and blocked is None:
            caption = posts[platform].get("caption") if isinstance(posts[platform], dict) else None
            size = len(caption.encode("utf-8")) if isinstance(caption, str) else 0
            if platform == "threads" and size > THREADS_IMAGE_CAPTION_MAX_BYTES:
                logger.info("marketing script: the Threads caption is %d UTF-8 bytes > %d run_id=%s — Threads "
                            "is frozen as TEXT (Upload-Post splits a longer post into a thread unless the "
                            "no-split flag works, and that flag is verified only on its text route)",
                            size, THREADS_IMAGE_CAPTION_MAX_BYTES, run_id)
                formats[platform] = "text"
                threads_long = True
            elif platform in list_text:
                logger.info("marketing script: the %s caption could not list the rows its picture leaves out run_id=%s "
                            "— it was written as a text post and is frozen as TEXT", platform, run_id)
                formats[platform] = "text"
                list_long = True
            else:
                formats[platform] = "image"
        elif "text" in allowed:
            formats[platform] = "text"
        else:
            # create_posts refuses a platform outside the format map anyway; say so once, here.
            logger.warning("marketing script: the accepted package carries copy for %r, which has no "
                           "recordable format run_id=%s — no format frozen for it", platform, run_id)
        if platform in pictured and formats.get(platform) != "image":
            fallen.append(platform)
    out["post_formats"] = formats
    if fallen:
        reason = (blocked if blocked is not None else "threads_caption_long" if threads_long else
                  "caption_list_long" if list_long else None)
        if reason is None:   # unreachable: a pictured platform is text only for a reason above
            logger.error("marketing script: %s froze as TEXT for no known reason run_id=%s — recorded as "
                         "freeze_failed", fallen, run_id)
            reason = "freeze_failed"
        out[IMAGE_FALLBACK_KEY] = {"reason": reason, "platforms": fallen}
        logger.info("marketing script: image posts are on, but %s froze as TEXT run_id=%s reason=%s — recorded "
                    "as image_fallback (stamped on those posts)", ",".join(fallen), run_id, reason)
    if template:
        out["image_footer"] = template_footer
    elif "image" in formats.values():
        out["image_footer"] = post_copy.image_footer(run_date)
    else:
        out.pop("image_footer", None)
    return out


# ── Batch 2 (2026-10-19, "first seconds"): the run's frozen VIDEO features ─────────────────────────────


def parse_video_features(raw: Any) -> Tuple[str, ...]:
    """MARKETING_VIDEO_FEATURES as the video features it switches on, in VIDEO_FEATURES order. Re-validated
    here, at call time: the Settings validator normalises a deployed value, but a value set on the live
    object (a console, a test) skips it — so an unknown token is dropped with an ERROR here too, and a
    non-string is no feature (today's video: the fail-closed side). Never raises; every logged token is
    bounded."""
    if not isinstance(raw, str):
        logger.error("marketing script: MARKETING_VIDEO_FEATURES is not a string (%s) — no video feature",
                     type(raw).__name__)
        return ()
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    unknown = sorted(tokens - set(VIDEO_FEATURES))
    if unknown:
        logger.error("marketing script: MARKETING_VIDEO_FEATURES ignored unknown feature(s) %s",
                     ",".join(t[:24] for t in unknown[:10]))
    return tuple(f for f in VIDEO_FEATURES if f in tokens)


def video_features_setting() -> Tuple[str, ...]:
    """MARKETING_VIDEO_FEATURES, read when called — at WRITE time, like MARKETING_IMAGE_PLATFORMS."""
    return parse_video_features(settings.MARKETING_VIDEO_FEATURES)


def freeze_video_features(output: Dict[str, Any], *, switch: Iterable[str], holder_v2: bool,
                          run_id: Optional[str] = None) -> List[str]:
    """`output.video_features` (VIDEO_FEATURES_KEY) as `_frozen_output` stores it, decided ONCE at write time
    from the stored output (formats already frozen): sorted(`switch` ∩ what applies to the output's kind) —
    a lesson LESSON_VIDEO_FEATURES (hook_card, push_in), a template (`authorship == "template"`)
    TEMPLATE_VIDEO_FEATURES (data_card, push_in) — and ONLY when `holder_v2` (the worker holding the run
    declared WORKER_CAPABILITY_VIDEO_V2 on its claim); [] otherwise, and for an empty switch. A card
    feature is frozen only when the stored output can draw it — `hook_card` needs a hook that can stand
    ALONE on a card (`run_service.hook_card_text`: one drawable string, and not compliant only beside its
    first narration line — B2-COMPL-1), `data_card` a post image the read-back carries
    (`run_service.data_card_strings`) and a content card it may replace (`template_onscreen.data_card_slot`,
    CARDS-B2-1) — else it is left out (WARNING, naming why) and the video keeps today's card.
    A switch entry outside VIDEO_FEATURES is ignored (the setting's readers drop it first). Pure but for
    logging; never raises for a dict output. A string `switch` (not a collection) is a ValueError."""
    if isinstance(switch, (str, bytes)):
        raise ValueError("switch is a collection of video feature names, not one string")
    wanted = frozenset(f for f in switch if isinstance(f, str) and f in VIDEO_FEATURES)
    if not wanted or not holder_v2:
        return []
    template = output.get("authorship") == TEMPLATE_AUTHORSHIP
    features = sorted(wanted & set(TEMPLATE_VIDEO_FEATURES if template else LESSON_VIDEO_FEATURES))
    if VIDEO_FEATURE_HOOK_CARD in features and hook_card_text(output) is None:
        logger.warning("marketing script run_id=%s: MARKETING_VIDEO_FEATURES lists hook_card, but the accepted "
                       "hook %s — no hook card this run (a hook card shows the hook alone; the video opens as "
                       "before)", run_id, hook_card_problem(output))
        features.remove(VIDEO_FEATURE_HOOK_CARD)
    picture = data_card_strings(output) if VIDEO_FEATURE_DATA_CARD in features else None
    if VIDEO_FEATURE_DATA_CARD in features and picture is None:
        logger.warning("marketing script run_id=%s: MARKETING_VIDEO_FEATURES lists data_card, but the template's "
                       "post image cannot be drawn from the read-back (its formats, footer or image_spec) — no "
                       "data card this run (the text cards stay)", run_id)
        features.remove(VIDEO_FEATURE_DATA_CARD)
    if (VIDEO_FEATURE_DATA_CARD in features
            and template_onscreen.data_card_slot(output.get("series"), output.get("cards"), picture) is None):
        logger.warning("marketing script run_id=%s: MARKETING_VIDEO_FEATURES lists data_card, but none of the "
                       "template's cards is one a data card may replace (series %s) — no data card this run "
                       "(the text cards stay)", run_id, str(output.get("series"))[:40])
        features.remove(VIDEO_FEATURE_DATA_CARD)
    return features


# ── Batch 3 (2026-10-20, owner decision 2 of 2026-10-15): the run's frozen EXTRA video posts ──────────────


def parse_video_extra_platforms(raw: Any) -> Tuple[str, ...]:
    """MARKETING_VIDEO_EXTRA_PLATFORMS as the platforms that also post the day's video as an EXTRA post, in
    EXTRA_VIDEO_PLATFORMS order. Re-validated here, at call time: the Settings validator normalises a deployed
    value, but a value set on the live object (a console, a test) skips it — so "x" (X never posts two
    formats), "bluesky" (its video is not built: deferred to the week-4 review) and an unknown token are
    dropped with an ERROR here too, and a non-string is no platform (no extra video: the fail-closed side).
    Never raises; every logged token is bounded."""
    if not isinstance(raw, str):
        logger.error("marketing script: MARKETING_VIDEO_EXTRA_PLATFORMS is not a string (%s) — no extra video",
                     type(raw).__name__)
        return ()
    tokens = {t.strip().lower() for t in raw.split(",") if t.strip()}
    if "x" in tokens:
        logger.error("marketing script: MARKETING_VIDEO_EXTRA_PLATFORMS lists x — dropped (X never posts two "
                     "formats)")
    if "bluesky" in tokens:
        logger.error("marketing script: MARKETING_VIDEO_EXTRA_PLATFORMS lists bluesky — dropped (Bluesky video is "
                     "not built: deferred to the week-4 review)")
    unknown = sorted(tokens - set(EXTRA_VIDEO_PLATFORMS) - {"x", "bluesky"})
    if unknown:
        logger.error("marketing script: MARKETING_VIDEO_EXTRA_PLATFORMS ignored unknown platform(s) %s",
                     ",".join(t[:24] for t in unknown[:10]))
    return tuple(p for p in EXTRA_VIDEO_PLATFORMS if p in tokens)


def video_extra_platforms_setting() -> Tuple[str, ...]:
    """MARKETING_VIDEO_EXTRA_PLATFORMS, read when called — at WRITE time, like MARKETING_IMAGE_PLATFORMS."""
    return parse_video_extra_platforms(settings.MARKETING_VIDEO_EXTRA_PLATFORMS)


def freeze_extra_formats(output: Dict[str, Any], run_date: date, *, platforms: Iterable[str],
                         run_id: Optional[str] = None, fact_sheet: Any = None) -> Dict[str, Any]:
    """The run's EXTRA video posts as `_frozen_output` stores them, decided ONCE at write time from the stored
    output (its formats already frozen): {} — nothing stored, the output keeps exactly today's keys — or
    `{EXTRA_FORMATS_KEY: {platform: "video"}, VIDEO_POSTS_KEY: {platform: {"title": None, "caption": str}}}`
    for the platforms kept, in EXTRA_VIDEO_PLATFORMS order. `create_posts` appends those posts itself, after
    re-deriving every caption; the worker never reads either key.

    `platforms` is MARKETING_VIDEO_EXTRA_PLATFORMS as read at write time (`video_extra_platforms_setting`);
    only members of EXTRA_VIDEO_PLATFORMS count (never x, never bluesky), and a string (not a collection of
    names) is a ValueError. A listed platform is kept only when:

    * the day's frozen PRIMARY formats hold a video — the unchanged worker renders it then (its
      `render.video_needed` reads `post_formats`); otherwise none is (INFO: no video today);
    * its own primary post is frozen as image or text — a platform the judge dropped has no format and no
      copy (INFO);
    * its caption re-derives (`post_copy.extra_video_caption`): the platform's OWN accepted caption with only
      its code-owned disclaimer swapped for the narrated-video one — no new model text, no judge call
      (WARNING naming why not; never the text);
    * Threads: that caption fits THREADS_VIDEO_CAPTION_MAX_BYTES UTF-8 bytes — the /upload route documents
      no long-text field, and a split post would put the disclaimer in a reply (INFO);
    * a TEMPLATE (`authorship == "template"`): the caption passes `news_templates.extra_video_problems` against
      the record's stored `fact_sheet` (None: WARNING, no extra) — told whether the video will draw the day's
      picture: the output's frozen `video_features` (read here, already frozen by `freeze_video_features`) hold
      `data_card`. A Form 4 roundup's caption speaks of the picture's rows (B3-LIVE-1, `picture_caption`), so
      without the data card it freezes no extra (WARNING).

    Pure but for logging; never raises for a dict output but the ValueError above."""
    if isinstance(platforms, (str, bytes)):
        raise ValueError("platforms is a collection of platform names, not one string")
    listed = frozenset(p for p in platforms if isinstance(p, str))
    wanted = [p for p in EXTRA_VIDEO_PLATFORMS if p in listed]
    if not wanted:
        return {}
    formats = output.get("post_formats")
    if not isinstance(formats, dict) or "video" not in formats.values():
        logger.info("marketing script run_id=%s: MARKETING_VIDEO_EXTRA_PLATFORMS lists %s, but there is no video "
                    "today (the frozen formats hold none) — no extra video post", run_id, ",".join(wanted))
        return {}
    authorship = output.get("authorship", post_copy.AUTHORSHIP_AI)
    if not isinstance(authorship, str) or authorship not in post_copy.AUTHORSHIPS:
        logger.error("marketing script run_id=%s: the accepted output's authorship %r is unknown — no extra video "
                     "post", run_id, str(authorship)[:20])
        return {}
    template = authorship == TEMPLATE_AUTHORSHIP
    # B3-LIVE-1: will the video draw the day's picture? Only a frozen data card says so (the worker may still keep
    # the text card: create_posts asks the verified video again).
    draws_picture = template and VIDEO_FEATURE_DATA_CARD in frozen_video_features(output, template=True,
                                                                                  run_id=run_id)
    posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
    kept: Dict[str, Dict[str, Any]] = {}
    for platform in wanted:
        if formats.get(platform) not in ("image", "text"):
            logger.info("marketing script run_id=%s: %s has no picture or text post today (the judge dropped it, "
                        "or it has no copy) — no extra video post for it", run_id, platform)
            continue
        post = posts.get(platform)
        accepted = post.get("caption") if isinstance(post, dict) else None
        try:
            caption = post_copy.extra_video_caption(platform, accepted, run_date, authorship)
        except ValueError as e:
            logger.warning("marketing script run_id=%s: the %s caption does not re-derive as a video caption (%s) — "
                           "no extra video post for it", run_id, platform, str(e)[:200])
            continue
        size = len(caption.encode("utf-8"))
        if platform == "threads" and size > THREADS_VIDEO_CAPTION_MAX_BYTES:
            logger.info("marketing script run_id=%s: the Threads video caption is %d UTF-8 bytes > %d — no extra "
                        "Threads video today (Upload-Post's video route documents no long-text field)", run_id,
                        size, THREADS_VIDEO_CAPTION_MAX_BYTES)
            continue
        if template:
            if fact_sheet is None:
                logger.warning("marketing script run_id=%s: a template's %s video caption cannot be re-checked "
                               "without its fact sheet — no extra video post for it", run_id, platform)
                continue
            problems = news_templates.extra_video_problems(output, fact_sheet=fact_sheet, run_date=run_date,
                                                           platform=platform, caption=caption,
                                                           video_draws_picture=draws_picture)
            if problems:
                logger.warning("marketing script run_id=%s: the template's %s video caption fails its re-check "
                               "codes=%s — no extra video post for it", run_id, platform,
                               sorted({str(p.get("code"))[:40] for p in problems}))
                continue
        kept[platform] = {"title": None, "caption": caption}
    if not kept:
        return {}
    logger.info("marketing script run_id=%s: extra video posts frozen for %s", run_id, ",".join(kept))
    return {EXTRA_FORMATS_KEY: {p: "video" for p in kept}, VIDEO_POSTS_KEY: kept}


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
    first SERIES_TRAIL_MAX steps. None when the script recorded no selection block.

    A trail that reached the SKIP terminal (a spotlight day where nothing qualified: its `nothing_qualified`
    row) mirrors `selection.SKIP` — never the lesson, which that day did not post. So does a
    `nothing_qualified` row whose trail lost its SKIP step."""
    steps = _trail_steps(row)
    if steps is None:
        return None
    chosen = next((s["series"] for s in reversed(steps) if s.get("outcome") == "chosen"), None)
    if chosen is None:
        if (any(s.get("series") == selection.SKIP for s in steps)
                or row.get("reject_reason") == REASON_NOTHING_QUALIFIED):
            chosen = selection.SKIP
        else:
            tid = row.get("template_id")
            chosen = tid if isinstance(tid, str) and tid in selection.SERIES_BY_ID else selection.LESSON
    return {"series": chosen, "series_trail": steps[:SERIES_TRAIL_MAX]}


def _symbol(value: Any) -> Optional[str]:
    """A logo key as a comparable company symbol (stripped, upper-cased), or None for anything that is not a
    non-empty string. Bounded: a stored value is never trusted to be short."""
    if not isinstance(value, str):
        return None
    sym = value.strip().upper()[:24]
    return sym or None


def lead_company(output: Any) -> Optional[str]:
    """The LEAD company a template output draws — the company the post is about — as a symbol, or None (a lesson,
    an output with no logo). Rule (b) of the cross-lane exclusion compares leads only, never every drawn row: a
    roundup lists up to a dozen companies, and blocking on any of them would drop a whole one-record series (the
    lane review of 2026-11-15).

    The header logo of the post image when it has one (the `spotlight` / `bars` layouts, a `rows` roundup with a
    header — and any future layout that keeps its company in a `header`: since news-v23 the company_stakes list's
    investor plate); the LEFT company of a `pair` (the investor whose stake a pre-news-v23 post reports); else the first of `logo_refs` (the image's logos in draw order, then
    the opening card's). news-v23 (the ONE 13F list): a 13F post leads with its FIRST row — the largest holding change,
    as before the restyle — never the listed filer's plate in its header (`_first_row_logo`). Pure, never raises: a
    stored row that does not read back has no lead."""
    if not isinstance(output, Mapping):
        return None
    spec = output.get("image_spec")
    if isinstance(spec, Mapping):
        if output.get("series") in _F13_LIST_SERIES and _first_row_logo(spec):
            return _first_row_logo(spec)
        header = spec.get("header")
        if isinstance(header, Mapping) and _symbol(header.get("logo")):
            return _symbol(header.get("logo"))
        left = spec.get("left")
        if spec.get("layout") == "pair" and isinstance(left, Mapping) and _symbol(left.get("logo")):
            return _symbol(left.get("logo"))
    refs = output.get("logo_refs")
    for ref in refs if isinstance(refs, list) else ():
        if isinstance(ref, Mapping) and _symbol(ref.get("key")):
            return _symbol(ref.get("key"))
    return None


#: The series whose picture is the news-v23 13F list (`news_templates._f13_list_parts`).
_F13_LIST_SERIES = frozenset({"thirteen_f", "thirteen_f_spotlight"})


def _first_row_logo(spec: Mapping[str, Any]) -> Optional[str]:
    """The logo symbol of a `rows` picture's first row, or None. Pure, never raises."""
    sections = spec.get("sections")
    if not isinstance(sections, list) or not sections or not isinstance(sections[0], Mapping):
        return None
    rows = sections[0].get("rows")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
        return None
    return _symbol(rows[0].get("logo"))


def _logo_ref_names(output: Any) -> Dict[str, str]:
    """{symbol: display name} of every logo ref a template output names (`logo_refs[].key` / `.name`, whitespace
    collapsed, at most 80 characters) — the insider list's cooldown names each of its companies by it. Pure, never
    raises: a stored row that does not read back names nothing."""
    out: Dict[str, str] = {}
    refs = output.get("logo_refs") if isinstance(output, Mapping) else None
    for ref in refs if isinstance(refs, list) else ():
        if not isinstance(ref, Mapping):
            continue
        key, name = _symbol(ref.get("key")), ref.get("name")
        if key and isinstance(name, str) and name.strip() and key not in out:
            out[key] = " ".join(name.split())[:80]
    return out


def lead_company_name(output: Any) -> Optional[str]:
    """The display NAME of the lead company a template output draws (`lead_company`), as its logo ref names it
    (`logo_refs[].name`, the template's wordmark), or None (a lesson, a lead with no named ref). Pure, never raises.
    The insider spotlight's cooldown matches a recent spotlight's company by it (`_spotlight_source_kw`)."""
    lead = lead_company(output)
    refs = output.get("logo_refs") if lead is not None and isinstance(output, Mapping) else None
    for ref in refs if isinstance(refs, list) else ():
        if isinstance(ref, Mapping) and _symbol(ref.get("key")) == lead:
            name = ref.get("name")
            if isinstance(name, str) and name.strip():
                return " ".join(name.split())[:80]
    return None


#: The prefixes of a `lead_keys` entry: the lead's issuer root symbol and its casefolded display name.
LEAD_KEY_ROOT = "root:"
LEAD_KEY_NAME = "name:"


def lead_keys(output: Any) -> FrozenSet[str]:
    """The keys rule (b) of the cross-lane exclusion compares for a template output's LEAD company (`lead_company`) — so
    one company drawn under two listings counts as ONE lead (the second review, 2026-11-16: Alphabet's Money Map leads
    with GOOGL, its earnings post with GOOG, and a raw-symbol compare let both out within 48 h):

    * ``root:<issuer root>`` — the lead symbol folded through the issuer family rule (`company_news_rules.issuer_root`:
      "GOOGL" → "GOOG", "BRK-B" → "BRK");
    * ``name:<display name>`` — the lead's logo-ref name (`lead_company_name`), casefolded and whitespace-collapsed.

    Two outputs share a lead when their key sets meet. Empty for a lesson or an output with no lead. Pure, never
    raises: a stored row that does not read back has no keys."""
    keys: Set[str] = set()
    root = news_rules.issuer_root(lead_company(output))
    if root:
        keys.add(LEAD_KEY_ROOT + root)
    name = lead_company_name(output)
    if name:
        keys.add(LEAD_KEY_NAME + name.casefold())
    return frozenset(keys)


def _record_lead_keys(company: Any) -> FrozenSet[str]:
    """`lead_keys` of a record's company row (its issuer root, its casefolded display name) — the keys the output's
    lead will carry when that company leads it. Pure, never raises."""
    keys: Set[str] = set()
    root = news_rules.issuer_root(getattr(company, "symbol", None))
    if root:
        keys.add(LEAD_KEY_ROOT + root)
    name = getattr(company, "name", None)
    if isinstance(name, str) and name.strip():
        keys.add(LEAD_KEY_NAME + " ".join(name.split())[:80].casefold())
    return frozenset(keys)


#: The several-company records whose LEAD (their first row) may be dropped for rule (b) of the cross-lane exclusion
#: (review 2026-11-20): the record type → (its rows' field, its fewest rows).
_LEAD_NARROWABLE: Dict[type, Tuple[str, int]] = {
    news_rules.EarningsDay: ("reports", news_rules.EARNINGS_DAY_COMPANIES[0]),
    news_rules.CongressMonth: ("counts", news_rules.CONGRESS_MONTH_COMPANIES[0]),
    # owner decisions 2026-11-20 ("3 - 5 companies at once"): both Form 4 lists — a weekly roundup's lead row and the
    # insider list's lead company drop as the earnings lead does; fewer than INSIDER_MIN_ROWS left is the exclusion
    news_rules.InsiderBuysWeek: ("rows", news_rules.INSIDER_MIN_ROWS),
    news_rules.InsiderSpotlight: ("companies", news_rules.INSIDER_MIN_ROWS),
}


def narrow_other_lane_lead(record: Any, avoid_leads: FrozenSet[str]) -> Tuple[Optional[Any], List[str]]:
    """Rule (b) for a SEVERAL-company post (review 2026-11-20, confirmed): an earnings day, a Congress month or — owner
    decisions 2026-11-20 — a Form 4 list (a weekly roundup, the insider list) is ONE record per call, its lead the first
    (largest) company — so excluding the whole record because the other lane drew
    its lead within CROSS_LANE_COMPANY_DAYS dropped every other company's results (or the month's Congress post) with
    it. Here only the lead is DROPPED, then the next (the new lead) is checked the same way, until the lead is clear:
    (the narrowed record, the symbols dropped). (None, dropped) when fewer than the post's minimum would be left — an
    EXCLUSION, never a template refusal. Every other record: (record, []) — its lead is its subject (a stakes post's
    investor plate, a 13F's largest row of ONE filer): the output check excludes it whole. Pure; the record is rebuilt
    with `dataclasses.replace` (its own validation re-runs)."""
    shape = _LEAD_NARROWABLE.get(type(record))
    if shape is None or not avoid_leads:
        return record, []
    field_, least = shape
    rows = list(getattr(record, field_))
    dropped: List[str] = []
    while rows and _record_lead_keys(rows[0].company) & avoid_leads:
        dropped.append(rows.pop(0).company.symbol)
    if not dropped:
        return record, []
    if len(rows) < least:
        return None, dropped
    return dataclasses.replace(record, **{field_: tuple(rows)}), dropped


def theme_week_fallbacks(rows: Iterable[Any], run_date: date, lane: str, *, run_id: str) -> List[Dict[str, str]]:
    """The quiet-week BACKUP theme posts the ledger already holds in `run_date`'s ET week (owner decision 2026-11-27:
    "1 backup theme a week (Recommended)" — "At most one quiet-week theme per week across both daily posts, so Wednesday
    almost always keeps a theme."), as `{run_date, lane, source_ref}`, oldest first — from `rows`, the build's theme
    read (`_theme_ledger`: every theme pick of BOTH lanes, any status, through run_date + 1 for a main run).

    A row counts when it is in the week (`selection.theme_week`, Monday..Sunday), is not this run's own (`run_id`), is
    dated on or before `run_date` — or after it in the OTHER lane only (a main run built the next day, after that
    day's spotlight: the post was made before this run, `_cross_lane`'s forward day) — and is a backup
    (`selection.theme_is_fallback`: anything but the MAIN lane's Wednesday lead; no lane is the main lane, a lane the
    code does not know is a backup). FAIL CLOSED: a row whose day does not read back counts as this week's backup
    (logged at ERROR) — the cap never lets a second backup through on an unreadable row. Pure but for that log."""
    week_start, week_end = selection.theme_week(run_date)
    out: List[Dict[str, str]] = []
    for r in rows:
        if not isinstance(r, Mapping) or str(r.get("run_id")) == run_id:
            continue
        row_lane = r.get("lane") or RUN_LANE_MAIN
        ref = r.get("source_ref") if isinstance(r.get("source_ref"), str) else ""
        raw_day = r.get("run_date")
        try:
            day = date.fromisoformat(str(raw_day)[:10])
        except ValueError:
            logger.error("marketing template day: a theme script's run_date %r does not read back (lane=%s source=%s) "
                         "run_id=%s — counted as this week's backup theme (fail closed)", str(raw_day)[:40],
                         str(row_lane)[:20], ref[:80], run_id)
            out.append({"run_date": str(raw_day)[:40], "lane": str(row_lane)[:20], "source_ref": ref})
            continue
        if not week_start <= day <= week_end or (day > run_date and row_lane == lane):
            continue
        if selection.theme_is_fallback(day, main_lane=row_lane == RUN_LANE_MAIN):
            out.append({"run_date": day.isoformat(), "lane": str(row_lane)[:20], "source_ref": ref})
    return out


def _without_feature_series(chain: Tuple[str, ...], run: Dict[str, Any], *, lane: str) -> Tuple[str, ...]:
    """`chain` without the series whose post image is the `feature` layout (FEATURE_LAYOUT_SERIES) when the worker
    holding `run` did not declare WORKER_CAPABILITY_LAYOUT_FEATURE on its claim — the LAYOUTS_2B rule, for the
    2026-11-15 layout: an image older than it refuses a feature picture loudly (a failed day), so those series
    leave the chain and it falls through to its next series exactly as a refused one does (a spotlight chain to
    its SKIP, never the lesson). One WARNING naming the worker, the capability and the series dropped; nothing
    logged, and `chain` returned as it is, when the chain holds no such series or the holder declared it. Pure
    but for logging."""
    cannot_draw = [s for s in chain if s in FEATURE_LAYOUT_SERIES]
    caps = run_worker_capabilities(run)
    if not cannot_draw or WORKER_CAPABILITY_LAYOUT_FEATURE in caps:
        return chain
    logger.warning(
        "marketing script%s: the day's chain %s has %s, whose post image is the %s layout, but the worker holding "
        "run_id=%s (worker_version=%r, attempt %s, declared %s) did not declare %r on its claim — %s dropped from "
        "the chain (deploy the feature-layout marketing worker)", " (spotlight)" if lane == RUN_LANE_SPOTLIGHT else "",
        list(chain), cannot_draw, "/".join(WORKER_LAYOUTS_FEATURE), run.get("id"),
        str(run.get("worker_version") or "")[:64], run.get("attempts"), sorted(caps) or "nothing",
        WORKER_CAPABILITY_LAYOUT_FEATURE, "it is" if len(cannot_draw) == 1 else "they are")
    return tuple(s for s in chain if s not in FEATURE_LAYOUT_SERIES)


#: news-v23 (the restyle, owner 2026-11-19): the series whose picture only a WORKER_CAPABILITY_LOOK_V23 worker can
#: draw at all — earnings, whose post is a TABLE of 2-4 companies (a `rows` section's `columns` and each row's `table`):
#: an older worker's closed walker refuses those keys, and without them the picture would be the companies' names
#: with no figure; and company stakes, whose list draws each stake's basis word as its row's `label` (an older walker
#: refuses the key, and without it an amount would stand without its basis). `_without_look_series` drops them from
#: the chain of any other holder.
#: Owner decisions 2026-11-20: the insider LIST too — each row's grey detail line (`detail`) and its "Total purchases"
#: column are news-v23 keys an older walker refuses, and without them the list would be names and totals with no shares
#: or filed cost.
LOOK_V23_SERIES: FrozenSet[str] = frozenset({"earnings", "company_stakes", "insider_spotlight"})


def _without_look_series(chain: Tuple[str, ...], run: Dict[str, Any], *, lane: str) -> Tuple[str, ...]:
    """`chain` without LOOK_V23_SERIES when the worker holding `run` did not declare WORKER_CAPABILITY_LOOK_V23 on its
    claim — `_without_feature_series`' rule for the news-v23 earnings table: the chain falls through to its next
    series exactly as a refused one does. One WARNING naming the worker, the capability and the series dropped;
    `chain` as it is when it holds none or the holder declared it. Pure but for logging."""
    cannot_draw = [s for s in chain if s in LOOK_V23_SERIES]
    caps = run_worker_capabilities(run)
    if not cannot_draw or WORKER_CAPABILITY_LOOK_V23 in caps:
        return chain
    logger.warning(
        "marketing script%s: the day's chain %s has %s, whose picture needs the news-v23 rows (the earnings table, a "
        "stake's basis label), but the worker holding "
        "run_id=%s (worker_version=%r, attempt %s, declared %s) did not declare %r on its claim — dropped from the "
        "chain (deploy the news-v23 marketing worker)", " (spotlight)" if lane == RUN_LANE_SPOTLIGHT else "",
        list(chain), cannot_draw, run.get("id"), str(run.get("worker_version") or "")[:64], run.get("attempts"),
        sorted(caps) or "nothing", WORKER_CAPABILITY_LOOK_V23)
    return tuple(s for s in chain if s not in LOOK_V23_SERIES)


#: Review of 2026-11-26: the composer refusals that leave the insider spotlight's window UNCHECKED (the trail entry
#: carries no `TRAIL_WINDOW_KEY`): a list the per-company gates cut below `INSIDER_MIN_ROWS` companies. Like the
#: adapter's own `insider_spotlight_too_few_companies` (which returns no checked window), its one or two companies
#: stay in reach, so the next spotlight's window reaches back over those days (`company_news_rules.spotlight_window`,
#: from the last RECORDED window, at most SPOTLIGHT_MAX_WINDOW_DAYS) and can make them a list of three. Nothing was
#: posted, so the ledger and the 14-day cooldown are untouched. Every other refusal still records its window.
SPOTLIGHT_UNCHECKED_REFUSALS: FrozenSet[str] = frozenset({"too_few_rows"})


def _checked_window_entry(got: Any) -> Dict[str, Any]:
    """The trail-entry part recording the Form 4 window a series' collector CHECKED (C0, 2026-11-16):
    ``{TRAIL_WINDOW_KEY: {"start", "end"}}`` from `Candidates.checked_window`, or {} when the answer carries none (any
    series but the insider spotlight, a source without the field, a malformed pair — logged). Pure but for logging."""
    window = getattr(got, "checked_window", None)
    if window is None:
        return {}
    if (isinstance(window, tuple) and len(window) == 2
            and all(isinstance(d, date) and not isinstance(d, datetime) for d in window) and window[0] <= window[1]):
        return {TRAIL_WINDOW_KEY: {"start": window[0].isoformat(), "end": window[1].isoformat()}}
    logger.error("marketing template day: the source answered a checked window that is not a (start, end) pair of "
                 "dates (%r) — not recorded; the next spotlight's window starts from the last recorded one",
                 str(window)[:80])
    return {}


def _trail_text(trail: List[Dict[str, str]]) -> str:
    """A trail for one log line: `ceo_buys:no_candidates(ceo_none_qualified) > insider_buys:unavailable(…)`."""
    return " > ".join(f"{t.get('series')}:{t.get('outcome')}" + (f"({t['reason']})" if t.get("reason") else "")
                      for t in trail) or "-"


def _extras_text(output: Any) -> str:
    """The ACCEPTED lines' `extras=` (Batch 3): the platforms whose extra video post was frozen, or "-"."""
    extras = output.get(EXTRA_FORMATS_KEY) if isinstance(output, dict) else None
    return ",".join(str(p)[:20] for p in extras) if isinstance(extras, dict) and extras else "-"


class MarketingScriptService:
    def __init__(self, runs: Optional[MarketingRunService] = None, *, writer=None, news=None,
                 art_gemini: Any = None, art_claude: Any = None) -> None:
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
        # Per-post art (workstream C): (run_id, ref) → the art record a build of this process made (failures too),
        # so a rebuild never pays twice (`_attach_art`; ART_MEMO_MAX, oldest dropped first). `art_gemini` /
        # `art_claude`: the image client and the Claude CLIENT post_art uses — None = the shared ones (tests inject).
        self._art_memo: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
        self._art_gemini = art_gemini
        self._art_claude = art_claude

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
        merge must not revert a claim or a worker PATCH that lands between its read and its write).

        A row with no pick mirrors nothing — except a SPOTLIGHT day where nothing qualified (`_heal_skip_mirror`)."""
        ref = row.get("source_ref")
        if not ref:
            await self._heal_skip_mirror(run, row)
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

    async def _heal_skip_mirror(self, run: Dict[str, Any], row: Dict[str, Any]) -> None:
        """The selection mirror of a row with NO pick. Only a trail that reached the SKIP terminal is mirrored — a
        spotlight `nothing_qualified` row (migration 210): `series` = `selection.SKIP` and its `series_trail`, never a
        source_ref, template or class (it has none). That trail is what lets the run-health texts and the digest
        tell a quiet day from a data source that timed out (`digest_service._series_note` and its skip texts read
        the RUN, never the script — OBS-1: a machinery failure must not hide behind "nothing qualified"). Every
        other row without a pick (a rest day, a lesson day's empty pool) mirrors nothing, exactly as before: a main
        run never reaches SKIP. The same rules as `_heal_mirror`: only on a HELD run, one fenced merge
        (`cas=True`), written only when something differs, a lost write logged and retried by the next kick."""
        meta_want = _selection_mirror(row)
        if meta_want is None or meta_want.get("series") != selection.SKIP:
            return
        run_meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        if all(run_meta.get(k) == v for k, v in meta_want.items()):
            return
        if self._held_problem(run) is not None:
            logger.info("marketing script: the skip trail was not mirrored onto run %s (not held); the script row is "
                        "the record", run.get("id"))
            return
        try:
            written = await self.runs.update_run(run["id"], metadata=meta_want, cas=True)
        except Exception as e:
            logger.warning("marketing script: could not mirror the skip trail onto run %s lane=%s (%s: %s) — the "
                           "next kick retries; until then its run-health check reads the skip as unexplained",
                           run.get("id"), run.get("lane") or RUN_LANE_MAIN, type(e).__name__, e)
            return
        if written is None:
            logger.info("marketing script: run %s changed under the skip-trail mirror — nothing written; the next "
                        "kick retries", run.get("id"))
            return
        run["metadata"] = {**run_meta, **meta_want}

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
        series whose image is a drop-2b layout (LAYOUTS_2B_SERIES, WARNING): the rest of the chain runs. One
        without WORKER_CAPABILITY_LAYOUT_FEATURE loses the `feature`-layout series (FEATURE_LAYOUT_SERIES) the
        same way (`_without_feature_series`).

        A SPOTLIGHT run (migration 210) is selected by `_select_spotlight` — its own calendar, ending in a skip,
        never the lesson. Everything below is the MAIN lane's, unchanged."""
        run_id = run["id"]
        run_date = date.fromisoformat(str(run["run_date"])[:10])
        if self._lane(run) == RUN_LANE_SPOTLIGHT:
            return await self._select_spotlight(run, run_date)
        plan = selection.plan_for(run_date)
        if plan.rest_day:
            row, ours = await self.runs.insert_script(
                {"run_id": run_id, "run_date": run_date.isoformat(), "status": REST_DAY}, run=run)
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
        chain = _without_feature_series(chain, run, lane=RUN_LANE_MAIN)
        chain = _without_look_series(chain, run, lane=RUN_LANE_MAIN)
        if chain == (selection.LESSON,) or not chain:
            # The lesson rotation keeps only lesson refs (`selection.lesson_refs`) and a spotlight run never picks a
            # lesson, so the other lane's same-day pick could never matter here: this read stays exactly today's.
            recent = await self.runs.recent_source_refs(run_date, selection.news_recent_limit())
            return await self._select_lesson(run, run_date, recent, classes=classes, selection_block=None)
        return await self._await_template_build(run, run_date, plan, chain, classes)

    @staticmethod
    def _lane(run: Dict[str, Any]) -> str:
        """The run's lane (`run_service.run_lane`: no lane is the main lane, the column default). A lane the code
        does not know is never guessed — keys, paths and the calendar all depend on it: MarketingRequestInvalid
        (a 422, deterministic for the run), logged at ERROR with the run id, before any selection or write."""
        try:
            return run_lane(run)
        except ValueError as e:
            logger.error("marketing script REFUSED run_id=%s run_date=%s: %s — no selection, nothing written",
                         run.get("id"), run.get("run_date"), e)
            raise MarketingRequestInvalid(f"run {run.get('id')}: {e}") from e

    async def _select_spotlight(self, run: Dict[str, Any], run_date: date) -> Optional[Dict[str, Any]]:
        """The SPOTLIGHT day's first selection (migration 210, owner decision 2026-11-15). The lane is news-only and
        never ends in the lesson (the main lane posts the week's):

        * the lane no longer runs on `run_date` (`selection.spotlight_day_problem`, the claim gate's own predicate: its
          switch, its weekdays, its hours, the news classes or the per-series switch changed after the claim, or no
          spotlight series is left once the main lane's are dropped) or the calendar rests that weekday → the
          rest_day INSERT;
        * the chain = `selection.spotlight_chain(run_date)`: `spotlight_plan_for` narrowed by `enabled_chain` to the
          classes and the SAME per-series switch as the main lane (MARKETING_NEWS_SERIES ∩ the shipped series, read
          now), WITHOUT every series the main lane's chain holds that day (C0, 2026-11-16: the spotlight never takes
          the main lane's Money Map, which sent the main day to a second weekly lesson) — it ends in `selection.SKIP`;
        * a holder that did not declare WORKER_CAPABILITY_NEWS_TEMPLATES cannot draw a template, and the lesson is no
          fallback here: the day skips `nothing_qualified` (WARNING naming the worker). A holder without
          WORKER_CAPABILITY_LAYOUTS_2B loses the drop-2b layout series, and one without
          WORKER_CAPABILITY_LAYOUT_FEATURE the `feature`-layout series (the spotlight series' picture), as on the
          main lane;
        * a chain with no series left → `nothing_qualified` now; any other chain → the background template build
          (`_await_template_build`), whose SKIP terminal writes the same `nothing_qualified` row."""
        run_id = run["id"]
        plan = selection.spotlight_plan_for(run_date)
        problem = selection.spotlight_day_problem(run_date) if not plan.rest_day else "rest_day"
        if problem is not None:
            row, ours = await self.runs.insert_script(
                {"run_id": run_id, "run_date": run_date.isoformat(), "status": REST_DAY}, run=run)
            logger.warning("marketing script %s run_id=%s run_date=%s lane=spotlight status=%s (the spotlight lane "
                           "does not run that day NOW — %s: MARKETING_SPOTLIGHT_ENABLED, MARKETING_SPOTLIGHT_WEEKDAYS, "
                           "the hours, the news classes or MARKETING_NEWS_SERIES changed after the claim, or no "
                           "spotlight series is left once the main lane's are dropped)",
                           "SELECTED" if ours else "selection ADOPTED (a concurrent kick won)", run_id, run_date,
                           row.get("status"), problem)
            return row
        classes = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
        news_series = (selection.parse_news_series(settings.MARKETING_NEWS_SERIES)
                       if classes != frozenset({"A"}) else frozenset())
        # C0 (2026-11-16): the chain is the calendar's narrowed by the switches AND without every series the main
        # lane's chain holds that day (`selection.spotlight_chain`) — the spotlight never takes the main lane's series.
        narrowed = selection.spotlight_chain(run_date, classes=classes, news_series=news_series)
        chain = narrowed.chain
        logger.info("marketing script (spotlight): content classes %s news_series=%s run_id=%s run_date=%s plan=%s "
                    "chain=%s main lane's series left out=%s", ",".join(sorted(classes)),
                    ",".join(sorted(news_series)) or "none", run_id, run_date, plan.reason, list(chain),
                    list(narrowed.main_lane) or "none")
        caps = run_worker_capabilities(run)
        series = [s for s in chain if s not in selection.CHAIN_TERMINALS]
        if series and WORKER_CAPABILITY_NEWS_TEMPLATES not in caps:
            logger.warning(
                "marketing script (spotlight): the day's chain %s has news series, but the worker holding run_id=%s "
                "(worker_version=%r, attempt %s, declared %s) did not declare %r on its claim — an older worker image "
                "cannot draw a template, and a spotlight day never falls to the lesson: the day SKIPS "
                "(nothing_qualified) — deploy the drop-2 marketing worker", list(chain), run_id,
                str(run.get("worker_version") or "")[:64], run.get("attempts"), sorted(caps) or "nothing",
                WORKER_CAPABILITY_NEWS_TEMPLATES)
            return await self._skip_nothing_qualified(run, run_date, {
                "plan": plan.reason, "chain": list(chain),
                "trail": [{"series": selection.SKIP, "outcome": "no_candidates",
                           "reason": "worker_without_news_templates"}]})
        cannot_draw = [s for s in series if s in LAYOUTS_2B_SERIES]
        if cannot_draw and WORKER_CAPABILITY_LAYOUTS_2B not in caps:
            logger.warning(
                "marketing script (spotlight): the day's chain %s has %s, whose post image is a drop-2b layout (%s), "
                "but the worker holding run_id=%s (worker_version=%r, attempt %s, declared %s) did not declare %r on "
                "its claim — dropped from the chain (deploy the drop-2b marketing worker)", list(chain), cannot_draw,
                "/".join(WORKER_LAYOUTS_2B), run_id, str(run.get("worker_version") or "")[:64], run.get("attempts"),
                sorted(caps) or "nothing", WORKER_CAPABILITY_LAYOUTS_2B)
            chain = tuple(s for s in chain if s not in LAYOUTS_2B_SERIES)
        chain = _without_feature_series(chain, run, lane=RUN_LANE_SPOTLIGHT)
        chain = _without_look_series(chain, run, lane=RUN_LANE_SPOTLIGHT)
        if not any(s not in selection.CHAIN_TERMINALS for s in chain):
            return await self._skip_nothing_qualified(run, run_date, {
                "plan": plan.reason, "chain": list(chain),
                "trail": [{"series": selection.SKIP, "outcome": "no_candidates"}]})
        return await self._await_template_build(run, run_date, plan, chain, classes)

    async def _skip_nothing_qualified(self, run: Dict[str, Any], run_date: date,
                                      selection_block: Dict[str, Any]) -> Dict[str, Any]:
        """A SPOTLIGHT day where nothing qualified: ONE first-write-wins INSERT of a `rejected` script, reject_reason
        `nothing_qualified` (migration 210's CHECK), carrying the selection trail in its fact sheet — so the day is
        VISIBLE (the run closes `skipped`, the trail says why), never a rest day and never the lesson. A concurrent
        writer's row is adopted. The lane comes from the run (`insert_script`)."""
        run_id = run["id"]
        row, ours = await self.runs.insert_script({
            "run_id": run_id, "run_date": run_date.isoformat(), "status": REJECTED,
            "reject_reason": REASON_NOTHING_QUALIFIED, "last_error": "spotlight: nothing qualified",
            "fact_sheet": {"selection": selection_block},
        }, run=run)
        trail = selection_block.get("trail") if isinstance(selection_block.get("trail"), list) else []
        logger.warning("marketing script %s run_id=%s run_date=%s lane=%s status=%s reason=%s plan=%s trail=%s — the "
                       "day skips, never the lesson",
                       "NOTHING QUALIFIED" if ours else "selection ADOPTED (a concurrent kick won)", run_id, run_date,
                       run.get("lane") or RUN_LANE_MAIN, row.get("status"), row.get("reject_reason"),
                       selection_block.get("plan"), _trail_text(trail))
        return row

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
        row, ours = await self.runs.insert_script(new, run=run)
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
        store it as the day's ACCEPTED script — or, at the chain's end, fall to the lesson (main) or skip
        `nothing_qualified` (spotlight). Always ends in
        exactly ONE `insert_script` (first write wins: a concurrent builder's row is adopted). No model,
        no lease, 0 tokens. Every fallback lands in the trail (`fact_sheet.selection`), logged.

        Each record is composed from its JSON ROUND TRIP (what the fact sheet stores and create_posts
        re-reads), so compose-time and re-check-time inputs are identical; the output is re-checked with
        `news_templates.revalidate` after the logos and the frozen formats are attached, as stored. A record
        the template refuses is reported back to the source (`_report_template_refusal`, the round-2 review of
        2026-10-20), so the adapter's walk stops offering it first every day.

        It starts by reading the day's row: a kick whose "no row yet" read raced a build that had just
        finished (its INSERT committed, its done-callback ran) starts a new build — which ADOPTS that row
        here instead of walking the adapter again (a series that timed out in the first build would
        otherwise hit FMP a second time, only to lose its INSERT with 23505). A new build is created only
        after the previous one's done-callback ran, so its INSERT is already visible to this read.

        The two lanes (migration 210): a MAIN chain ends in the lesson, a SPOTLIGHT chain in `selection.SKIP` — the
        `nothing_qualified` row (`_skip_nothing_qualified`), never the lesson. Either lane keeps away from the other
        lane's day (`_cross_lane`): the other lane's same-day ledger refs join the exclude set (`recent_source_refs`
        with the lane, which reads them first); a series the other lane used that day falls back `other_lane`
        before the source is asked; a candidate whose LEAD company (`lead_company`) the other lane drew within
        CROSS_LANE_COMPANY_DAYS is EXCLUDED — collected apart from the refusals, so it never reaches the source's
        refusal memo (a Money Map or a stake held back for weeks because of the other lane's post)."""
        run_id = str(run["id"])
        lane = self._lane(run)
        existing = await self.runs.get_script(run_id)
        if existing is not None:
            logger.info("marketing template day: run_id=%s already has its row (status=%s source=%s) — a "
                        "finished build's row ADOPTED, nothing rebuilt", run_id, existing.get("status"),
                        existing.get("source_ref"))
            return existing
        started = _mono()
        deadline = started + TEMPLATE_BUILD_BUDGET_SECONDS
        recent = await self.runs.recent_source_refs(run_date, selection.news_recent_limit(), lane=lane)
        same_day_refs, taken_today, avoid_leads = await self._cross_lane(run, run_date, lane)
        exclude = frozenset(r for r in recent if isinstance(r, str)) | same_day_refs
        # The insider spotlight's ledger inputs (B decisions 5-6) — read only when the day's chain holds it, so every
        # other build reads exactly what it always did. A failed read raises like the two reads above.
        spotlight_kw = (await self._spotlight_source_kw(run, run_date)
                        if SPOTLIGHT_CONTEXT_SERIES in chain else {})
        # news-v23 phase 3 (the weekly theme post): its rotation's own ledger read and the holder's drawable angles —
        # read only when the day's chain holds the theme series (Wednesday's lead, a quiet week's backup), so every
        # other build is unchanged. The same ONE read gives the week's backups (owner decision 2026-11-27: "1 backup
        # theme a week"): a backup step whose week already made one, in either lane, falls back `week_cap` unasked.
        theme_rows = (await self._theme_ledger(run, run_date, lane)
                      if THEME_CONTEXT_SERIES in chain else None)
        theme_kw = self._theme_source_kw(run, theme_rows) if theme_rows is not None else {}
        week_backups = (theme_week_fallbacks(theme_rows, run_date, lane, run_id=run_id)
                        if theme_rows is not None
                        and selection.theme_is_fallback(run_date, main_lane=lane == RUN_LANE_MAIN) else [])
        spotlight = lane == RUN_LANE_SPOTLIGHT
        end_of_chain = "the skip (nothing_qualified)" if spotlight else "the lesson"
        try:
            news = self._news_source_fn()
        except Exception as e:  # noqa: BLE001 — a broken adapter import must not cost the day its lesson
            logger.exception("marketing template day: the company-news source could not be loaded run_id=%s "
                             "(%s: %s) — every series falls through to %s", run_id, type(e).__name__, e,
                             end_of_chain)
            news = None
        unavailable_cls = getattr(news, "MarketingNewsUnavailable", None)
        store_state = smart_link.store_state()
        allow_x_url = bool(settings.MARKETING_X_ALLOW_URLS)
        # news-v22: the closed first-line prefixes, read ONCE for this build like allow_x_url and frozen into the
        # accepted output (`first_line_prefix_on`): create_posts re-composes from the frozen value, never today's switch
        first_line_prefix = settings.MARKETING_FIRST_LINE_PREFIX is True
        image_posts = bool(settings.MARKETING_IMAGE_POSTS)   # read ONCE for this build, like a generation
        # review 2026-11-20: the platforms whose post will carry the picture, read ONCE for this build with the switches
        # the freeze reads (MARKETING_X_IMAGES, MARKETING_IMAGE_PLATFORMS) — `compose` writes a list series' X /
        # Bluesky caption for its format and freezes them into the output (`picture_outlets`), which then BOUNDS the
        # freeze's own read (`freeze_post_formats`: a platform the composition did not picture never posts it)
        picture_outlets = template_picture_outlets(image_posts, bool(settings.MARKETING_X_IMAGES),
                                                   image_platforms_setting())
        # Per-post art (workstream C): read at the build's start so each candidate's ONE holder read also serves the
        # art gate (`art_holder`); `_attach_art` reads the switch again, at its own call (off then: no art, no key).
        art_on = settings.MARKETING_ART_ENABLED is True
        trail: List[Dict[str, str]] = []

        def block(extra: Dict[str, str]) -> Dict[str, Any]:
            return {"plan": plan.reason, "chain": list(chain), "trail": [*trail, extra]}

        for i, series in enumerate(chain):
            if series in selection.CHAIN_TERMINALS:
                if spotlight:
                    if series != selection.SKIP:   # unreachable: `spotlight_plan_for` never names the lesson
                        logger.error("marketing template day: the spotlight chain %s reached %r run_id=%s — a "
                                     "spotlight day never posts the lesson: it skips", list(chain), series, run_id)
                    return await self._skip_nothing_qualified(
                        run, run_date, block({"series": selection.SKIP, "outcome": "no_candidates"}))
                if series != selection.LESSON:     # unreachable: `plan_for` never names the skip
                    logger.error("marketing template day: the main chain %s reached %r run_id=%s — the main lane "
                                 "always ends in the lesson: selecting it", list(chain), series, run_id)
                row = await self._select_lesson(run, run_date, recent, classes=classes,
                                                selection_block=block({"series": selection.LESSON,
                                                                       "outcome": "chosen"}))
                logger.info("marketing template day fell to the LESSON run_id=%s trail=%s in %.1fs", run_id,
                            _trail_text(trail), _mono() - started)
                return row
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            if series in taken_today:
                # Rule (c): the other lane already used this series today — never asked, never a refusal.
                self._fell_back(run_id, trail, series, OTHER_LANE, OTHER_LANE_SERIES, nxt)
                continue
            if series == THEME_CONTEXT_SERIES and len(week_backups) >= selection.THEME_FALLBACKS_PER_WEEK:
                # Owner decision 2026-11-27: the week's one quiet-week backup theme is spent — never asked, never a
                # refusal (no memo), so a later week offers the same themes as before.
                logger.info("marketing template day: run_id=%s lane=%s run_date=%s — this week's backup theme was "
                            "already made (%s); the theme step falls back %s (owner 2026-11-27: one backup theme a "
                            "week, Wednesday's lead never capped)", run_id, lane, run_date,
                            "; ".join(f"{b['lane']} {b['run_date']} {b['source_ref'][:80]}" for b in week_backups[:3]),
                            THEME_WEEK_CAP)
                self._fell_back(run_id, trail, series, THEME_WEEK_CAP, THEME_FALLBACK_WEEK_USED, nxt)
                continue
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
                                    deadline=_mono() + cap,
                                    **(spotlight_kw if series == SPOTLIGHT_CONTEXT_SERIES else {}),
                                    **(theme_kw if series == THEME_CONTEXT_SERIES else {})),
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
            # C0: the Form 4 window the insider spotlight CHECKED rides on its trail entry, whatever became of it
            checked = _checked_window_entry(got) if series == SPOTLIGHT_CONTEXT_SERIES else {}
            refused: List[str] = []
            excluded: List[str] = []
            for rec in records:
                seen, kept_out = len(refused), len(excluded)
                art_holder: Optional[Dict[str, Any]] = {} if art_on else None
                built = await self._compose_candidate(
                    run, run_date, series, rec, rejections, block({"series": series, "outcome": "chosen", **checked}),
                    store_state=store_state, allow_x_url=allow_x_url, image_posts=image_posts,
                    deadline=deadline, news=news, refused=refused, excluded=excluded, avoid_leads=avoid_leads,
                    first_line_prefix=first_line_prefix, art_holder=art_holder, picture_outlets=picture_outlets)
                if built is None:
                    # Only a TEMPLATE refusal reaches the source's refusal memo; a cross-lane exclusion never does
                    # (the memo would hold back a Money Map for 28 days, a stake for its fingerprint's life).
                    if len(refused) > seen:
                        self._report_template_refusal(news, series, rec, refused[seen:], run_date=run_date,
                                                      run_id=run_id)
                    elif len(excluded) == kept_out:   # unreachable: every None names a refusal or an exclusion
                        logger.error("marketing series %s: a candidate was dropped with no refusal code and no "
                                     "exclusion run_id=%s — not reported to the source", series, run_id)
                    continue
                output, sheet, ref = built
                if art_on:
                    # The record is chosen, composed, re-checked as stored and frozen: the art step (its own budget,
                    # never a refusal — any failure is an art record saying why, and the day goes on) runs here,
                    # BEFORE the one INSERT, so its spend lands on the row it belongs to.
                    output = await self._attach_art(run, run_date, series, output, sheet, ref, rec=rec, news=news,
                                                    holder=art_holder, started=started)
                row, ours = await self.runs.insert_script({
                    "run_id": run_id, "run_date": run_date.isoformat(), "status": ACCEPTED,
                    "source_ref": ref, "template_id": series, "output": output, "violations": [],
                    "fact_sheet": sheet, "generation_id": str(uuid.uuid4()), "generations": 0,
                    "model": None, "prompt_version": news_templates.TEMPLATE_VERSION, "tokens_used": 0,
                }, run=run)
                if ours:
                    logger.info("marketing template ACCEPTED run_id=%s series=%s class=%s source=%s trail=%s "
                                "formats=%s video_features=%s extras=%s logos=%d/%d in %.1fs", run_id, series,
                                output.get("content_class"), ref, _trail_text(trail), output.get("post_formats"),
                                output.get(VIDEO_FEATURES_KEY) or [], _extras_text(output),
                                sum(1 for lg in output.get("logos") or () if lg.get("url")),
                                len(output.get("logos") or ()), _mono() - started)
                else:
                    logger.info("marketing template day ADOPTED a concurrent writer's row run_id=%s status=%s "
                                "source=%s (ours was %s)", run_id, row.get("status"), row.get("source_ref"), ref)
                    self._report_lost_art(run_id, output)
                return row
            if refused:
                logger.error("marketing series %s refused every candidate%s run_id=%s codes=%s", series,
                             (f" it did not exclude ({len(excluded)} excluded: the other lane drew their lead "
                              "company)") if excluded else "", run_id, sorted(set(refused)))
                # review of 2026-11-26: a list the composer cut below three companies (`too_few_rows`) or whose lead the
                # other lane drew leaves its window UNCHECKED — its companies stay in reach of the next spotlight
                # (`SPOTLIGHT_UNCHECKED_REFUSALS`); any other refusal is a checked window, as before
                unchecked = set(refused) <= SPOTLIGHT_UNCHECKED_REFUSALS
                self._fell_back(run_id, trail, series, "all_refused", sorted(set(refused))[0], nxt,
                                extra=None if unchecked else checked)
            elif excluded:
                # Rule (b): every candidate's lead company was drawn by the other lane within CROSS_LANE_COMPANY_DAYS.
                # Review of 2026-11-26: no post was made, so the window stays unchecked (the lead's 2-day hold ends and
                # the next spotlight's window reaches back over these days)
                self._fell_back(run_id, trail, series, OTHER_LANE, OTHER_LANE_COMPANY, nxt)
            elif records:   # unreachable: a record either composes, is refused or is excluded
                self._fell_back(run_id, trail, series, "all_refused", "unknown", nxt, extra=checked)
            elif rejections and set(k for k, n in rejections.items() if n) == {"already_posted"}:
                self._fell_back(run_id, trail, series, "all_recent", str(getattr(got, "skip_reason", "") or ""), nxt,
                                extra=checked)
            else:
                self._fell_back(run_id, trail, series, "no_candidates",
                                str(getattr(got, "skip_reason", "") or ""), nxt, extra=checked)
        # Unreachable while every chain ends in a terminal (`selection.enabled_chain`); never a silent day.
        if spotlight:
            logger.error("marketing template day: the spotlight chain %s ended without its skip run_id=%s — the day "
                         "skips (nothing_qualified)", list(chain), run_id)
            return await self._skip_nothing_qualified(
                run, run_date, block({"series": selection.SKIP, "outcome": "no_candidates"}))
        logger.error("marketing template day: the chain %s ended without the lesson run_id=%s — selecting the "
                     "lesson", list(chain), run_id)
        return await self._select_lesson(run, run_date, recent, classes=classes,
                                         selection_block=block({"series": selection.LESSON, "outcome": "chosen"}))

    async def _cross_lane(self, run: Dict[str, Any], run_date: date, lane: str
                          ) -> Tuple[FrozenSet[str], FrozenSet[str], FrozenSet[str]]:
        """What the OTHER lane took (the spotlight lane, migration 210), from the ACCEPTED scripts of every lane dated
        [run_date − CROSS_LANE_COMPANY_DAYS, run_date] — through run_date + 1 for a MAIN run (below) —
        (`runs.lane_scripts_between`, one bounded read), this run's own row left out:

        * (a) the other lane's ledger refs dated `run_date` (the `recent` read stops BEFORE the run date) — and, for a
          MAIN run, dated run_date + 1;
        * (c) the series (template ids) the other lane used on `run_date` (that day only: C0 keeps the spotlight off
          the main lane's series, and this stays the guard for anything the calendars cannot see);
        * (b) the LEAD companies to avoid, as `lead_keys` (the issuer root and the display name — GOOGL and GOOG are
          one lead): every lane's rows for a SPOTLIGHT run (its own earlier days included), only SPOTLIGHT rows for a
          MAIN run — so with no spotlight row the main lane's day is byte for byte what it was.

        The MAIN run's forward day (C0, 2026-11-16): a main run of D − 1 resumed on day D (its resume window runs until
        the main hour, after the spotlight's) may be built AFTER D's spotlight was accepted; the `recent` read stops
        before D − 1, so without it neither read sees D's pick and the two lanes could post one ledger key. The claim
        window holds only today and yesterday, so run_date + 1 is as far as a row can be newer.

        A row whose lane does not read back counts as the other lane's (keeping away from more is the safe side).
        A failed or over-bound read RAISES, like the `recent` read beside it: the build crashes into the kick, the
        worker retries, and nothing is posted on a guess about the other lane."""
        through = run_date + timedelta(days=1) if lane == RUN_LANE_MAIN else run_date
        rows = await self.runs.lane_scripts_between(run_date - timedelta(days=CROSS_LANE_COMPANY_DAYS), through)
        run_id, today, last = str(run["id"]), run_date.isoformat(), through.isoformat()
        refs: Set[str] = set()
        taken: Set[str] = set()
        leads: Set[str] = set()
        for r in rows:
            if not isinstance(r, dict) or str(r.get("run_id")) == run_id:
                continue
            row_lane = r.get("lane") or RUN_LANE_MAIN
            row_day = str(r.get("run_date") or "")[:10]
            if today <= row_day <= last and row_lane != lane:
                if isinstance(r.get("source_ref"), str) and r["source_ref"]:
                    refs.add(r["source_ref"])
                if row_day == today and isinstance(r.get("template_id"), str) and r["template_id"]:
                    taken.add(r["template_id"])
            if lane == RUN_LANE_SPOTLIGHT or row_lane != RUN_LANE_MAIN:
                leads.update(lead_keys(r.get("output")))
        if refs or taken or leads:
            logger.info("marketing template day: cross-lane run_id=%s lane=%s run_date=%s other lane's refs %s..%s=%s "
                        "series today=%s lead companies to avoid (last %d ET days)=%s", run_id, lane, run_date, today,
                        last, sorted(refs)[:10], sorted(taken)[:10], CROSS_LANE_COMPANY_DAYS, sorted(leads)[:20])
        return frozenset(refs), frozenset(taken), frozenset(leads)

    async def _theme_ledger(self, run: Dict[str, Any], run_date: date, lane: str) -> List[Dict[str, Any]]:
        """The theme scripts of BOTH lanes dated [run_date − THEME_HISTORY_DAYS, run_date] — through run_date + 1 for a
        MAIN run, as `_cross_lane` reads (a main run resumed the next day may be built after that day's spotlight) —
        `runs.theme_scripts_between`: the build's ONE theme read, the rotation's keys (`_theme_source_kw`) and the
        week's backups (`theme_week_fallbacks`, owner decision 2026-11-27). A failed or over-bound read RAISES (logged
        at ERROR with the run id), like the build's other ledger reads: the build crashes into the kick, the worker
        retries, and no theme is chosen — nor a backup allowed — on a guess about the ledger."""
        through = run_date + timedelta(days=1) if lane == RUN_LANE_MAIN else run_date
        try:
            return await self.runs.theme_scripts_between(run_date - timedelta(days=news_rules.THEME_HISTORY_DAYS),
                                                         through)
        except Exception as e:
            logger.error("marketing template day: the theme rotation's ledger read failed run_id=%s run_date=%s "
                         "(%s: %s) — the build stops; the next kick builds the day afresh", run.get("id"), run_date,
                         type(e).__name__, e)
            raise

    def _theme_source_kw(self, run: Dict[str, Any], rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        """The weekly theme post's inputs, for its `candidates()` call only (news-v23 phase 3), from `rows` (the build's
        `_theme_ledger` read):

        * ``theme_history`` — the `source_ref` of every theme script of `rows`, oldest first (its OWN bounded read, so
          the rotation sees twelve weeks of theme keys whatever the other series posted — the general `recent` read is
          sized by row count, not by days; a main run's next-day key is the other lane's, already in its `exclude`);
        * ``theme_angles`` — the angles the worker holding the run can draw: every angle with
          WORKER_CAPABILITY_THEME_ANGLES, else "how they make money" only (THEME_ANGLES_WITHOUT_CAPABILITY: the `grid`
          it already draws). The adapter skips the others (``theme_angle_unsupported``), never a picture the worker
          would refuse;
        * ``theme_classes`` (2026-11-20) — the content classes switched on NOW (MARKETING_CONTENT_CLASSES): an angle of
          filings (`news_rules.THEME_ANGLE_CLASSES`: the insider scorecard and Congress, class C) is skipped with no read
          while class C is off (``theme_angle_class_off``); create_posts re-checks it (`record_content_classes`).

        Pure but for its logging: the read (and its fail-closed raise) is `_theme_ledger`'s."""
        run_id = str(run["id"])
        refs = [str(r["source_ref"]) for r in rows if isinstance(r.get("source_ref"), str) and r["source_ref"]]
        caps = run_worker_capabilities(run)
        angles = (news_rules.THEME_ANGLES if WORKER_CAPABILITY_THEME_ANGLES in caps
                  else THEME_ANGLES_WITHOUT_CAPABILITY)
        if WORKER_CAPABILITY_THEME_ANGLES not in caps:
            logger.warning("marketing template day: the worker holding run_id=%s did not declare %r — the theme post "
                           "may take only the angle(s) %s (deploy the theme-angles marketing worker)", run_id,
                           WORKER_CAPABILITY_THEME_ANGLES, list(angles))
        classes = parse_content_classes(settings.MARKETING_CONTENT_CLASSES)
        off = sorted(a for a, k in news_rules.THEME_ANGLE_CLASSES.items() if k not in classes)
        if off:
            logger.info("marketing template day run_id=%s: class(es) %s off (MARKETING_CONTENT_CLASSES) — the theme "
                        "post skips the angle(s) %s", run_id,
                        sorted({news_rules.THEME_ANGLE_CLASSES[a] for a in off}), off)
        return {"theme_history": tuple(refs[-256:]), "theme_angles": tuple(angles),
                "theme_classes": tuple(sorted(classes))}

    async def _spotlight_source_kw(self, run: Dict[str, Any], run_date: date) -> Dict[str, Any]:
        """The insider spotlight's two ledger inputs, for its `candidates()` call only (the spotlight lane, B decisions 5
        and 6, 2026-11-15), from two bounded reads:

        * ``spotlight_checked_through`` — the END of the Form 4 window the newest SPOTLIGHT script dated in
          [run_date − `SPOTLIGHT_ISSUER_COOLDOWN_DAYS`, run_date) recorded as checked (`runs.last_checked_window`, C0,
          2026-11-16: from that script's trail entry, `TRAIL_WINDOW_KEY`), or None: the window starts the day after it
          (`company_news_rules.spotlight_window`, never earlier than a week back) — so a 13F-led day, which checks no
          Form 4 window, loses no filing, and a mid-week change of the spotlight days leaves no gap;
        * ``spotlight_recent`` — ``(symbol, display name)`` of EVERY company of every ACCEPTED insider list dated
          [run_date − `SPOTLIGHT_ISSUER_COOLDOWN_DAYS`, run_date] (`runs.lane_scripts_between`), this run's own row left
          out: the 14-day issuer cooldown, exact on the RUN date (a ledger key carries only its window's start). The
          symbols are the key's (`insider_spotlight_key_parts`: owner decisions 2026-11-20, each company of a list
          once), else the output's lead (`lead_company`); each name its logo ref's (`_logo_ref_names`). A row that
          names none is skipped with a WARNING — the adapter's own ledger rule still covers it.

        A failed or over-bound read RAISES (logged at ERROR with the run id), like the build's other reads: the build
        crashes into the kick, the worker retries, and no spotlight is chosen on a guess about its window or its
        cooldown."""
        run_id = str(run["id"])
        try:
            checked = await self.runs.last_checked_window(
                RUN_LANE_SPOTLIGHT, SPOTLIGHT_CONTEXT_SERIES, before=run_date,
                within_days=news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS)
            rows = await self.runs.lane_scripts_between(
                run_date - timedelta(days=news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS), run_date)
        except Exception as e:
            logger.error("marketing template day: the insider spotlight's ledger reads failed run_id=%s run_date=%s "
                         "(%s: %s) — the build stops; the next kick builds the day afresh", run_id, run_date,
                         type(e).__name__, e)
            raise
        recent: List[Tuple[str, str]] = []
        for r in rows:
            if (not isinstance(r, dict) or r.get("template_id") != SPOTLIGHT_CONTEXT_SERIES
                    or str(r.get("run_id")) == run_id):
                continue
            # owner decisions 2026-11-20 (the insider LIST): EVERY company of the list is on cooldown — each symbol of
            # its key, with the display name its logo ref carries; a key that does not read back: the output's lead
            parts = news_rules.insider_spotlight_key_parts(r.get("source_ref"))
            syms = list(parts[0]) if parts is not None else [lead_company(r.get("output"))]
            names = _logo_ref_names(r.get("output"))
            pairs: List[Tuple[str, str]] = []
            for sym in syms:
                ok = sym if sym is not None and news_rules.canonical_symbol(sym) == sym else ""
                name = names.get(ok, "") if ok else (lead_company_name(r.get("output")) or "")
                if ok or name:
                    pairs.append((ok, name))
            if not pairs:
                logger.warning("marketing template day: an accepted insider spotlight (run_date=%s source=%s) names no "
                               "company — the cooldown reads its ledger key alone run_id=%s",
                               str(r.get("run_date"))[:10], str(r.get("source_ref"))[:80], run_id)
                continue
            for pair in pairs:
                if pair not in recent:
                    recent.append(pair)
        through = checked[1] if checked is not None else None
        logger.info("marketing template day: insider spotlight inputs run_id=%s run_date=%s last checked window=%s "
                    "cooldown companies (last %d days)=%s", run_id, run_date,
                    f"{checked[0]}..{checked[1]}" if checked is not None else "none",
                    news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS, [s or n for s, n in recent][:20])
        return {"spotlight_checked_through": through, "spotlight_recent": tuple(recent)}

    @staticmethod
    def _report_template_refusal(news: Any, series: str, rec: Any, codes: List[str], *, run_date: date,
                                 run_id: str) -> None:
        """Tell the company-news source that the template refused a record it offered (the round-2 review of
        2026-10-20, RR2-MM-TEMPLATE-WALL): `company_news_adapter.remember_template_refusal` remembers a Money Map
        record's refusal for its fiscal year, so the walk tries that company AFTER every candidate that can
        publish instead of offering it first — and spending one of its three attempts on it — every day. Before,
        nothing fed a template refusal back: three never-posted companies the template refuses at the head of the
        pool ended the series every day (each day `all_refused`, the lesson), and no posted company's new fiscal
        year was reached. The round-3 review (RR3-STAKES-TEMPLATE-WALL): a company stake is remembered too, by
        its stake id, and offered after every other stake (five refused ones at the head of the newest-first walk
        ended that series every day). Best effort and synchronous (a memo write): a source without the hook (a
        test fake) is skipped, and one that raises is logged at WARNING — never a reason to lose the day's next
        candidate."""
        remember = getattr(news, "remember_template_refusal", None)
        if not callable(remember):
            return
        try:
            remember(rec, tuple(codes), run_date=run_date)
        except Exception as e:  # noqa: BLE001 — a memo write must never cost the day its next candidate
            # the record's company symbol (a Money Map), its stake id (a stake) or — news-v23 — the investor and the
            # stake ids of a several-stake post — never a person's name
            stakes = getattr(rec, "stakes", None) if isinstance(rec, news_rules.CompanyStakes) else None
            which = (getattr(getattr(rec, "company", None), "symbol", None) or getattr(rec, "stake_id", None)
                     or (f"{rec.investor.symbol}'s stakes " + ",".join(s.stake_id for s in stakes) if stakes else None)
                     or "a record")
            logger.warning("marketing series %s: the template refusal of %s (%s) could not be reported to the "
                           "company-news source run_id=%s (%s: %s) — the record may be offered first again",
                           series, which, ",".join(sorted(set(codes))) or "-", run_id,
                           type(e).__name__, e, exc_info=True)

    @staticmethod
    def _fell_back(run_id: str, trail: List[Dict[str, Any]], series: str, outcome: str, reason: str,
                   nxt: Optional[str], *, extra: Optional[Mapping[str, Any]] = None) -> None:
        """One fallback step on the trail: `{series, outcome, reason?}` plus `extra` (the insider spotlight's checked
        window, C0 — `_checked_window_entry`), logged at WARNING."""
        entry: Dict[str, Any] = {"series": series, "outcome": outcome}
        reason = str(reason or "")[:60]
        if reason:
            entry["reason"] = reason
        if extra:
            entry.update(extra)
        trail.append(entry)
        logger.warning("marketing series FELL BACK run_id=%s from=%s outcome=%s reason=%s next=%s%s", run_id, series,
                       outcome, reason or "-", nxt,
                       f" checked={entry[TRAIL_WINDOW_KEY]['start']}..{entry[TRAIL_WINDOW_KEY]['end']}"
                       if TRAIL_WINDOW_KEY in entry else "")

    async def _compose_candidate(
        self, run: Dict[str, Any], run_date: date, series: str, rec: Any, rejections: Dict[str, Any],
        selection_block: Dict[str, Any], *, store_state: str, allow_x_url: bool, image_posts: bool,
        deadline: float, news: Any, refused: List[str], excluded: List[str], avoid_leads: FrozenSet[str],
        first_line_prefix: bool = False, art_holder: Optional[Dict[str, Any]] = None,
        picture_outlets: Optional[Sequence[str]] = None,
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
        """(output, fact sheet, ledger key) of ONE candidate record, as stored — or None, its refusal code
        appended to `refused` (logged). Never raises but for a cancellation: a candidate the template
        cannot write is the next candidate's turn.

        A candidate that composes and passes its first re-check but whose LEAD company (`lead_keys`: its issuer root or
        its display name) is in `avoid_leads` (the other lane drew it within CROSS_LANE_COMPANY_DAYS, `_cross_lane`) is
        EXCLUDED: None, with
        OTHER_LANE_COMPANY appended to `excluded` — NOT to `refused`, so the caller never reports it to the source's
        refusal memo. Checked before any logo fetch (no network for a candidate that cannot post). A real refusal
        always wins: a candidate the template refuses is refused (and reported) whatever its company.

        `first_line_prefix` (news-v22): MARKETING_FIRST_LINE_PREFIX as THIS build read it, once — `compose` freezes it
        into the output (`first_line_prefix_on`), and both re-checks here and create_posts' re-compose from that.

        `art_holder` (workstream C): handed to `_frozen_output`, which records its one holder read there for the art
        step (`_attach_art`); None while the art switch is off.

        `picture_outlets` (review 2026-11-20): the platforms whose post will carry the picture, as THIS build read the
        switches once (`template_picture_outlets`) — `compose` freezes it into the output; None (a caller that does
        not say): every picture platform, `compose`'s default."""
        run_id = str(run["id"])
        try:
            if getattr(rec, "series", None) != series:
                raise news_templates.NewsTemplateRefused(
                    "record_invalid", f"a {getattr(rec, 'series', None)!r} record offered for {series}")
            # Compose from the JSON round trip of the record — exactly what the fact sheet stores and
            # create_posts' re-check reads back: no float / date / key-order drift between the two.
            rec = news_rules.record_from_dict(json.loads(json.dumps(news_rules.record_to_dict(rec), allow_nan=False)))
            # news-v23: a several-company post (earnings, Congress Count, company stakes) DROPS the companies its
            # template refuses one by one — before the fact sheet and the ledger key, so both name exactly the companies
            # the post draws (too few left: refused like any record)
            narrowed = news_templates.drop_refused_rows(rec, run_date)
            if isinstance(rec, news_rules.CompanyStakes) and narrowed is not rec:
                # RR3-STAKES-TEMPLATE-WALL, news-v23: each stake the template dropped from the post is reported to the
                # source's refusal memo like a refused record — it never wins its investor the head of the walk again
                kept_ids = {st.stake_id for st in narrowed.stakes}
                for st in rec.stakes:
                    if st.stake_id not in kept_ids:
                        self._report_template_refusal(news, series, st, ["template_row_refused"], run_date=run_date,
                                                      run_id=run_id)
            rec = narrowed
            # Rule (b), review 2026-11-20: a several-company post whose LEAD the other lane drew within
            # CROSS_LANE_COMPANY_DAYS drops that company (and a next lead it drew too) — before the fact sheet and the
            # ledger key, so both name only the companies the post draws — never the whole day's results
            lead_free, lead_dropped = narrow_other_lane_lead(rec, avoid_leads)
            if lead_dropped:
                if lead_free is None:
                    logger.info("marketing series %s candidate EXCLUDED run_id=%s: its lead(s) %s were drawn by the other "
                                "lane within %d ET days and too few companies are left (not a template refusal, not "
                                "reported to the source)", series, run_id, ",".join(lead_dropped),
                                CROSS_LANE_COMPANY_DAYS)
                    excluded.append(OTHER_LANE_COMPANY)
                    return None
                logger.info("marketing series %s run_id=%s: %s DROPPED from the post — drawn by the other lane within %d "
                            "ET days (rule b); the other companies stay", series, run_id, ",".join(lead_dropped),
                            CROSS_LANE_COMPANY_DAYS)
                rec = lead_free
            sheet = news_rules.fact_sheet(rec, rejections=rejections, selection=selection_block)
            sheet = json.loads(json.dumps(sheet, allow_nan=False))
            ref = news_rules.ledger_key(rec)
            out = news_templates.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url,
                                         first_line_prefix=first_line_prefix, picture_outlets=picture_outlets)
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
        if avoid_leads:
            hit = lead_keys(out) & avoid_leads
            if hit:
                logger.info("marketing series %s candidate %s EXCLUDED run_id=%s: its lead company %s (%s) was drawn by "
                            "the other lane within %d ET days (not a template refusal, not reported to the source)",
                            series, ref, run_id, lead_company(out), ",".join(sorted(hit)), CROSS_LANE_COMPANY_DAYS)
                excluded.append(OTHER_LANE_COMPANY)
                return None
        out["logos"] = await self._attach_logos(out, deadline=deadline, news=news, run_id=run_id)
        out = await self._frozen_output(out, run_date, run_id=run_id, gen_id=f"template:{series}",
                                        image_posts=image_posts, fact_sheet=sheet, art_holder=art_holder)
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
            if news_rules.canonical_symbol(key) != key:
                # a key that is no ticker (the 13F spotlight list's filer plate, `news_templates.F13S_FILER_LOGO_KEY`:
                # a fund with no listing of its own) is a wordmark by design — never fetched, never a warning
                logger.info("marketing logo: %s names no listing run_id=%s — its wordmark by design", key, run_id)
                return entry
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

    # ── per-post art (workstream C, owner decisions 2026-11-15) ──────────────

    async def _attach_art(self, run: Dict[str, Any], run_date: date, series: str, output: Dict[str, Any],
                          sheet: Any, ref: str, *, rec: Any, news: Any, holder: Optional[Dict[str, Any]],
                          started: float) -> Dict[str, Any]:
        """The accepted template output with its ART record (`output["art"]`), or the output UNCHANGED — the art step
        of a template day ("Google draws, Claude checks"), run by `_build_template_day` after the record is chosen,
        composed, re-checked as stored and frozen, and BEFORE its one INSERT. Never raises but for a cancellation, and
        never refuses, skips or fails the day: every way to no art is a record saying why (post_art.ART_REASONS), and
        the worker draws today's picture.

        No `art` key at all (the stored output byte for byte today's): MARKETING_ART_ENABLED off — read NOW —, a
        monthly budget of 0 (or unreadable), or a script row that already exists (a concurrent build's, in a deploy
        overlap: nothing is spent, the INSERT adopts that row). Otherwise, in this order, each before any spend:
        the post must picture ONE company (`post_art.art_subject_for`, decision 4 — `not_a_company_series`); the day
        must carry a surface for it (`_art_day_surfaces`: an image post for a spotlight picture, a video for the
        cover — `text_only_day`); the worker holding the run must have declared WORKER_CAPABILITY_POST_ART, read from
        the ONE holder read the freeze made (`holder["holder"]` — `no_capability`, WARNING naming the worker;
        `ledger_unreadable` when that read failed); a record this process already made for (run_id, ref) is reused
        (the memo, failures included); at least ART_MIN_START_SECONDS must be left of TEMPLATE_DAY_BUDGET_SECONDS
        (`timeout`); then the script row is read again (adopt-first), the Claude model must have a price
        (`claude_unpriced`, ONE ERROR per value — `post_art.claude_model_problem`), and the run's ET month's art spend
        (`runs.art_spend_between`, two runs a day included) plus this run's worst case (`post_art.run_worst_micros`)
        must fit the cap (`budget_cap`; an unreadable month is `budget_unreadable`). Both reads are cut at the art's
        start window (TEMPLATE_DAY_BUDGET_SECONDS − ART_MIN_START_SECONDS from the build's start — `timeout`), and the
        time left is measured AGAIN after them (`timeout` under ART_MIN_START_SECONDS), so a slow ledger never pushes
        the art past the template day's ceiling. Only then `post_art.make_post_art` (its budget:
        min(MARKETING_ART_BUDGET_SECONDS, what is left of TEMPLATE_DAY_BUDGET_SECONDS NOW)), the company's sector /
        industry fetched inside it through the news source's `company_facts` (best effort, ≤ 8 s); the record is
        memoised (ok or failed). Every record returned once the day's surfaces are known — made, reused or a gate's —
        records only those surfaces (`_on_the_day`); the memo keeps the subject's own.

        Template rows keep tokens_used 0 and claude_usage NULL: the art's spend lives only in `output.art.usage`."""
        run_id = str(run["id"])
        if settings.MARKETING_ART_ENABLED is not True:
            logger.info("marketing art: MARKETING_ART_ENABLED was turned off during the build run_id=%s — no art", run_id)
            return output
        cap = _art_cap_micros()
        if cap is None or cap <= 0:
            logger.info("marketing art: the monthly art budget is %s run_id=%s — no art",
                        "unreadable" if cap is None else "0", run_id)
            return output
        try:
            record_in = news_rules.record_from_fact_sheet(sheet)
        except Exception as e:  # noqa: BLE001 — the stored record is the preferred input; the offered one is equal
            logger.warning("marketing art: the stored record of %s does not read back run_id=%s (%s: %s) — the art "
                           "reads the offered one", ref, run_id, type(e).__name__, e)
            record_in = rec
        subject = post_art.art_subject_for(series, output, record_in)
        # The surfaces the DAY can show (None until known): every record returned once they are known is narrowed to
        # them (`_on_the_day`) — never the memo, which keeps the subject's own (a rebuild may freeze other formats).
        surfaces: Optional[Tuple[str, ...]] = None

        def no_art(reason: str, detail: str) -> Dict[str, Any]:
            record = post_art.gate_record(reason, run_id=run_id, ref=ref, series=series, subject=subject,
                                          detail=detail)
            return self._with_art(output, record if surfaces is None else self._on_the_day(record, surfaces),
                                  run_id)

        if subject is None:
            return no_art("not_a_company_series", "the post does not picture exactly one company (decision 4)")
        if post_art.cover_is_costs_card(output):
            # owner decision 2026-11-20: a Money Map loss year's cover is its costs card — no art is drawn on it
            return no_art("costs_cover", "a Money Map loss year's video cover is its costs card")
        surfaces = _art_day_surfaces(output, subject)
        if not surfaces:
            return no_art("text_only_day", "the day freezes no picture or video post that may carry this art")
        found = holder.get("holder") if isinstance(holder, dict) else None
        holder_run, error = found if isinstance(found, tuple) and len(found) == 2 else (None, None)
        if error is not None or holder_run is None:
            logger.warning("marketing art: the run's holder could not be read run_id=%s (%s) — no art", run_id,
                           f"{type(error).__name__}: {error}" if error is not None else
                           ("no holder read was made" if found is None else "the run was not found"))
            return no_art("ledger_unreadable", "the holder read failed")
        declared = run_worker_capabilities(holder_run)
        if WORKER_CAPABILITY_POST_ART not in declared:
            logger.warning("marketing art: MARKETING_ART_ENABLED is on, but the worker holding run_id=%s "
                           "(worker_version=%r, attempt %s, declared %s) did not declare %r on its claim — no art this "
                           "run (deploy the per-post art marketing worker)", run_id,
                           str(holder_run.get("worker_version") or "")[:64], holder_run.get("attempts"),
                           sorted(declared) or "nothing", WORKER_CAPABILITY_POST_ART)
            return no_art("no_capability", "the holder did not declare post_art")
        key = (run_id, str(ref))
        memo = self._art_memo.get(key)
        if memo is not None:
            logger.info("marketing art: run_id=%s ref=%s already has this process's art record (status=%s) — reused, "
                        "nothing spent", run_id, ref, memo.get("status"))
            return self._with_art(output, self._on_the_day(copy.deepcopy(memo), surfaces), run_id)
        def art_budget() -> Tuple[float, float]:
            """(what is left of TEMPLATE_DAY_BUDGET_SECONDS, the art's budget = min(the setting, that)), read NOW."""
            left_now = started + TEMPLATE_DAY_BUDGET_SECONDS - _mono()
            return left_now, min(float(settings.MARKETING_ART_BUDGET_SECONDS), left_now)

        def too_late(left_now: float, when: str) -> Dict[str, Any]:
            return no_art("timeout", f"{max(left_now, 0.0):.0f}s left of the template day's "
                                     f"{TEMPLATE_DAY_BUDGET_SECONDS:.0f}s {when} (the art needs "
                                     f"{ART_MIN_START_SECONDS:.0f}s)")

        left, budget = art_budget()
        if budget < ART_MIN_START_SECONDS:
            return too_late(left, "at the art step")
        # C-fix (review, 2026-11-16): the two ledger reads below sit INSIDE the template day's ceiling. Each is cut at
        # the art's start window (the latest instant the art could still start with ART_MIN_START_SECONDS), and the
        # budget is measured again after them — a slow Supabase must never push the art past started +
        # TEMPLATE_DAY_BUDGET_SECONDS, which the worker's render pin (280 + 40 + 630 ≤ 960) relies on.
        start_by = started + TEMPLATE_DAY_BUDGET_SECONDS - ART_MIN_START_SECONDS

        def read_window() -> float:
            return max(start_by - _mono(), 0.0)

        try:
            existing = await asyncio.wait_for(self.runs.get_script(run_id), timeout=read_window())
        except asyncio.TimeoutError:
            logger.warning("marketing art: the adopt-first read of run_id=%s outlasted the art's start window — no "
                           "art (the build's INSERT still adopts any concurrent row)", run_id)
            return too_late(art_budget()[0], "after the adopt-first read")
        except Exception as e:  # noqa: BLE001 — no spend on a guess about a concurrent row
            logger.warning("marketing art: the script row of run_id=%s could not be read before spending (%s: %s) — "
                           "no art", run_id, type(e).__name__, e)
            return no_art("ledger_unreadable", "the adopt-first read failed")
        if existing is not None:
            logger.info("marketing art: run_id=%s already has its script row (status=%s source=%s) — no art spent; "
                        "the build adopts that row", run_id, existing.get("status"), existing.get("source_ref"))
            return output
        # Decision 9: a blank or unpriced Claude model is no art and ONE ERROR per value (post_art's once-key, shared
        # with preflight_problem) — never an ERROR (a Sentry event) on every eligible build of both lanes. Checked
        # before the month's read: nothing to reserve, nothing to read.
        if post_art.claude_model_problem() is not None:
            return no_art("claude_unpriced", "no price for the Claude model")
        month_start = run_date.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        try:
            spent = await asyncio.wait_for(self.runs.art_spend_between(month_start, month_end), timeout=read_window())
        except asyncio.TimeoutError:
            logger.warning("marketing art: this month's art spend read of run_id=%s outlasted the art's start window "
                           "— no art", run_id)
            return too_late(art_budget()[0], "after the month's spend read")
        except Exception as e:  # noqa: BLE001 — a cap never sums a guess
            logger.warning("marketing art: this month's art spend could not be read run_id=%s (%s: %s) — no art",
                           run_id, type(e).__name__, e)
            return no_art("budget_unreadable", f"{type(e).__name__}")
        reserve = post_art.run_worst_micros()
        if reserve is None:   # the setting changed between the check above and this read: still no reserve
            logger.warning("marketing art: MARKETING_ART_CLAUDE_MODEL has no price — the cap cannot reserve this run "
                           "run_id=%s; no art", run_id)
            return no_art("claude_unpriced", "no reserve")
        if spent["micros"] + reserve > cap:
            logger.warning("marketing art: this month's art budget is used up run_id=%s (spent ≈ $%.2f over %d runs, "
                           "this run reserves ≈ $%.2f, cap $%.2f) — no art", run_id, spent["micros"] / 1e6,
                           spent["runs"], reserve / 1e6, cap / 1e6)
            return no_art("budget_cap", f"spent {spent['micros']} + reserve {reserve} > cap {cap}")
        left, budget = art_budget()     # measured again: the reads above took their time out of the same ceiling
        if budget < ART_MIN_START_SECONDS:
            logger.warning("marketing art: the art step's ledger reads left %.0fs of the template day run_id=%s — no "
                           "art", max(left, 0.0), run_id)
            return too_late(left, "after the ledger reads")
        facts = getattr(news, "company_facts", None)
        facts_fn = (functools.partial(facts, timeout=post_art.STEP_SECONDS["facts"][1]) if callable(facts) else None)
        try:
            record = await post_art.make_post_art(
                run_id=run_id, ref=ref, series=series, output=output, store=self.runs.store_art, facts_fn=facts_fn,
                record=record_in, gemini=self._art_gemini, claude=self._art_claude, budget_seconds=budget)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — a contract breach (it never raises): spend unknown, fail closed
            logger.exception("marketing art: make_post_art RAISED run_id=%s (%s: %s) — recorded with an unknown "
                             "spend, so this month's art cap reads unreadable", run_id, type(e).__name__, e)
            record = {"version": post_art.ART_VERSION, "status": post_art.STATUS_FAILED, "reason": "internal_error",
                      "usage": {"images": 0, "images_uncertain": 0, "estimated_micros": None}}
        self._art_memo[key] = copy.deepcopy(record)
        while len(self._art_memo) > ART_MEMO_MAX:
            self._art_memo.popitem(last=False)
        return self._with_art(output, self._on_the_day(record, surfaces), run_id)

    @staticmethod
    def _on_the_day(record: Any, surfaces: Tuple[str, ...]) -> Any:
        """`record` with its `surfaces` narrowed to the ones the DAY can show (`_art_day_surfaces`) — a new dict, in
        post_art.SURFACES order; anything that is not a record with a surfaces list comes back unchanged. Final review
        (2026-11-19): `make_post_art` / `gate_record` record the subject's decision-4 surfaces (("picture", "cover")
        for every spotlight series), so a spotlight day frozen with NO image post (image posts off, no picture
        platform, a picture that fell back to text) noted the picture as allowed-but-not-drawn, and the review line
        read "(not on the picture)" — the wording of a worker that skipped the art. Narrowed, `art_surfaces` also
        refuses a picture declaration on a day that renders no picture (none ever is). Pure."""
        if not isinstance(record, dict) or not isinstance(record.get("surfaces"), list):
            return record
        return {**record, "surfaces": [s for s in post_art.SURFACES if s in record["surfaces"] and s in surfaces]}

    @staticmethod
    def _with_art(output: Dict[str, Any], record: Any, run_id: str) -> Dict[str, Any]:
        """`output` + `art: record`, as JSONB will hold it. A record that is not JSON-safe (never, by construction) is
        replaced by a minimal failed one with an UNKNOWN spend (ERROR): the spend must not vanish from the ledger, so
        the month's cap then reads unreadable — fail closed. `art` sits outside `news_templates.COMPARED_FIELDS`,
        so the stored output still re-checks as it did."""
        try:
            return json.loads(json.dumps({**output, "art": record}, allow_nan=False))
        except (TypeError, ValueError) as e:
            logger.error("marketing art: the art record of run_id=%s is not JSON-safe (%s: %s) — stored as a failed "
                         "record with an unknown spend (this month's art cap now reads unreadable)", run_id,
                         type(e).__name__, e)
            minimal = {"version": post_art.ART_VERSION, "status": post_art.STATUS_FAILED, "reason": "internal_error",
                       "usage": {"images": 0, "images_uncertain": 0, "estimated_micros": None}}
            return {**output, "art": minimal}

    @staticmethod
    def _report_lost_art(run_id: str, output: Dict[str, Any]) -> None:
        """A build whose INSERT lost to a concurrent writer's row (a deploy overlap) after making art: that art's spend
        is in no ledger row, so the monthly cap under-counts it — said once, at WARNING, with the amount."""
        art = output.get("art") if isinstance(output, dict) else None
        usage = art.get("usage") if isinstance(art, dict) else None
        spent = usage.get("estimated_micros") if isinstance(usage, dict) else None
        if art is None or spent == 0:
            return
        logger.warning("marketing art: run_id=%s — this build's art (status=%s, ≈ %s micros) lost the INSERT to a "
                       "concurrent row; its spend is in no ledger row and the monthly cap does not count it", run_id,
                       art.get("status") if isinstance(art, dict) else "?", spent if spent is not None else "unknown")

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
            return {"status": ACCEPTED, **base, "script": worker_script(row["output"], run_id=run_id,
                                                                        capabilities=run_worker_capabilities(run))}
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
                **_claude_patch(row, getattr(result, "claude_usage", None), run_id, gen_id),
            })
            if outcome == WRITTEN:
                logger.info("marketing script ACCEPTED run_id=%s generation=%s source=%s outlets=%s "
                            "dropped=%s formats=%s video_features=%s extras=%s tokens=%d in %.1fs", run_id, gen_id,
                            source_ref, sorted((result.package.get("posts") or {}).keys()),
                            sorted((result.package.get("dropped_outlets") or {}).keys()),
                            output.get("post_formats"), output.get(VIDEO_FEATURES_KEY) or [],
                            _extras_text(output), _int(result.tokens_used), elapsed)
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
            **_claude_patch(row, getattr(result, "claude_usage", None), run_id, gen_id),
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
                             gen_id: str, image_posts: bool, fact_sheet: Any = None,
                             art_holder: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """`freeze_post_formats` at write time, once. `image_posts` is MARKETING_IMAGE_POSTS as the
        caller read it ONCE when the generation started — the very value the writer was given
        (drop-1 review, server:F4), so it is never read again here; MARKETING_X_IMAGES and the
        holder's capability are read NOW. An unexpected
        raise must not throw away a paid, accepted package: it is logged at ERROR and the package is
        stored WITHOUT `post_formats` — which reads back exactly as a script accepted before image
        posts existed (every post text or video, as before).

        With MARKETING_IMAGE_POSTS on, `image` is frozen only when the worker that HOLDS the run now
        declared it can render the post image (`_holder_image_block`): an older worker image sends
        text specs, and `create_posts` would refuse every post of the day — the videos included — if
        the formats said image. With the switch off the run is not read at all (as before). A TEMPLATE
        output's image is its `image_spec`, which only a holder that also declared
        WORKER_CAPABILITY_NEWS_TEMPLATES can draw (a re-claim by an older image during the build) — and,
        for a drop-2b layout (WORKER_LAYOUTS_2B: `pair`, `grid`), WORKER_CAPABILITY_LAYOUTS_2B too.

        Batch 1 (2026-10-15): MARKETING_IMAGE_PLATFORMS is read NOW too (`image_platforms_setting`), and a
        day that should have carried the picture never loses it silently: a holder that cannot draw it
        passes its reason to the freeze as `image_blocked` (with image posts still on), so the output
        records `image_fallback` — and a freeze that RAISES records `freeze_failed` (the holder's reason
        first, when it had one) beside the formats-free package, for every platform that would have posted
        the picture. `create_posts` stamps that code on those posts.

        Batch 2 (2026-10-19): MARKETING_VIDEO_FEATURES is read NOW too (`video_features_setting`), and the
        features that apply to the package's kind are frozen into whichever output is stored (the
        formats-free one of a failed freeze included) as `video_features` (`freeze_video_features`) — only
        when the worker holding the run can draw them (`_holder_video_features`: video_v2; a data card also
        needs what drawing the template's picture needs). A
        `video_features` key in the package is never carried over. ONE run read serves every capability
        decision (`_read_holder`; the generation's worst case counts it once, _HOLDER_STATEMENTS), made only
        while image posts are on or a listed feature applies — with both off, no read, as before.

        Batch 3 (2026-10-20): MARKETING_VIDEO_EXTRA_PLATFORMS is read NOW too (`video_extra_platforms_setting`),
        and only while it lists a platform are the run's EXTRA video posts frozen into whichever output is stored
        (`freeze_extra_formats`: `extra_post_formats` + `video_posts`; `fact_sheet` — the record's, for a
        template's re-check). With the switch empty (the default) nothing is frozen or added: the stored output
        is byte for byte what it was. Neither key in the package is ever carried over; no run read (the video
        they carry is one the worker renders anyway); a freeze that raises is logged at ERROR and costs the
        extras only, never the paid package.

        Per-post art (workstream C, decision 11): `art_holder` — a dict, passed by a TEMPLATE build whose art switch
        was on — receives this freeze's ONE holder read as `art_holder["holder"]` (`_read_holder`'s (run, error)),
        so `_attach_art` gates on the very capabilities the formats were frozen against, with no extra statement;
        the read is made for it too while image posts and video features are both off. None (every lesson, every
        build with the switch off): no change at all."""
        image_posts = bool(image_posts)
        x_images = bool(settings.MARKETING_X_IMAGES)
        platforms = image_platforms_setting()
        template = package.get("authorship") == TEMPLATE_AUTHORSHIP
        video_wanted = tuple(f for f in video_features_setting()
                             if f in (TEMPLATE_VIDEO_FEATURES if template else LESSON_VIDEO_FEATURES))
        art_wanted = template and isinstance(art_holder, dict)
        holder = await self._read_holder(run_id) if (image_posts or video_wanted or art_wanted) else None
        if art_wanted:
            art_holder["holder"] = holder
        spec = package.get("image_spec") if template else None
        image_layout = spec.get("layout") if isinstance(spec, Mapping) else None
        blocked: Optional[str] = None
        if image_posts:
            blocked = await self._holder_image_block(
                run_id, gen_id, template=template, image_layout=image_layout, holder=holder)
        video_can: Tuple[str, ...] = ()
        holder_v2 = False
        if video_wanted:
            video_can = self._holder_video_features(run_id, gen_id, holder, video_wanted, image_layout=image_layout)
            holder_v2 = bool(video_can)
        try:
            stored = freeze_post_formats(
                package, run_date, image_posts=image_posts, x_images=x_images, run_id=run_id,
                image_platforms=platforms, image_blocked=blocked,
            )
        except Exception as e:
            logger.error("marketing script: could not freeze the post formats run_id=%s generation=%s "
                         "(%s: %s) — storing the package without them (no image posts this run)",
                         run_id, gen_id, type(e).__name__, e, exc_info=True)
            # A template's own image_footer is part of what create_posts re-checks: never dropped here.
            drop = (("post_formats", IMAGE_FALLBACK_KEY) if template
                    else ("post_formats", "image_footer", IMAGE_FALLBACK_KEY))
            stored = {k: v for k, v in package.items() if k not in drop}
            fallen = _picture_platforms(package, frozenset(platforms), x_images) if image_posts else []
            if fallen:
                reason = blocked or "freeze_failed"
                stored[IMAGE_FALLBACK_KEY] = {"reason": reason, "platforms": fallen}
                logger.warning("marketing script: %s will post TEXT run_id=%s generation=%s reason=%s — recorded "
                               "as image_fallback", ",".join(fallen), run_id, gen_id, reason)
        stored.pop(VIDEO_FEATURES_KEY, None)   # this freeze's own decision, never a copied one
        try:
            features = freeze_video_features(stored, switch=video_can, holder_v2=holder_v2, run_id=run_id)
        except Exception as e:  # noqa: BLE001 — a video feature must never cost a paid, accepted package
            logger.error("marketing script: could not freeze the video features run_id=%s generation=%s (%s: %s) "
                         "— none this run (today's video)", run_id, gen_id, type(e).__name__, e, exc_info=True)
            features = []
        if features:
            stored[VIDEO_FEATURES_KEY] = features
            logger.info("marketing script: video features frozen run_id=%s generation=%s features=%s", run_id,
                        gen_id, ",".join(features))
        stored.pop(EXTRA_FORMATS_KEY, None)   # this freeze's own decision, never a copied one
        stored.pop(VIDEO_POSTS_KEY, None)
        extra_platforms = video_extra_platforms_setting()
        if extra_platforms:
            try:
                stored.update(freeze_extra_formats(stored, run_date, platforms=extra_platforms, run_id=run_id,
                                                   fact_sheet=fact_sheet))
            except Exception as e:  # noqa: BLE001 — an extra video post must never cost a paid, accepted package
                logger.error("marketing script: could not freeze the extra video posts run_id=%s generation=%s "
                             "(%s: %s) — none this run (the day's other posts are unchanged)", run_id, gen_id,
                             type(e).__name__, e, exc_info=True)
        return stored

    async def _read_holder(self, run_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Exception]]:
        """(the run as its holder's claim left it, None) — or (None, the error) when the read raised; (None,
        None) when there is no such run. ONE read at write time serves every capability decision of
        `_frozen_output` (`_holder_image_block`, `_holder_video_features`), each of which logs a failure in the
        words of what it then decides. Never raises but for a cancellation."""
        try:
            return await self.runs.get_run(run_id), None
        except Exception as e:  # noqa: BLE001 — the deciding caller logs it (WARNING) and fails closed
            return None, e

    def _holder_video_features(self, run_id: str, gen_id: str, holder: Any, wanted: Tuple[str, ...], *,
                               image_layout: Any = None) -> Tuple[str, ...]:
        """The features of `wanted` the worker that holds `run_id` can draw, from its claim's declaration (the
        server-owned `metadata.worker_capabilities`, replaced by every claim that takes the run), read at
        write time from `holder` (`_read_holder`): every one needs WORKER_CAPABILITY_VIDEO_V2, and a
        `data_card` — the template's post image drawn into the video — also what drawing that picture needs
        (WORKER_CAPABILITY_NEWS_TEMPLATES, plus `layout_capabilities(image_layout)`: WORKER_CAPABILITY_LAYOUTS_2B
        for a drop-2b layout, WORKER_CAPABILITY_LAYOUT_FEATURE for `feature` — which is never a data card anyway,
        `run_service.data_card_strings`), as the post image itself does (`_holder_image_block`). () — no video
        feature this run: the video stays
        today's, which every worker renders — when the holder lacks video_v2 or the run cannot be read; each
        loss logged WARNING naming the worker, or the read that failed (a ledger blip is never blamed on the
        worker)."""
        run, error = holder if isinstance(holder, tuple) and len(holder) == 2 else (None, None)
        listed = ",".join(wanted)
        if error is not None:
            logger.warning("marketing script: could not read run_id=%s generation=%s to check its worker can "
                           "render the video features %s (%s: %s) — no video feature this run", run_id, gen_id,
                           listed, type(error).__name__, error)
            return ()
        if run is None:
            logger.warning("marketing script: run_id=%s generation=%s not found when its video features were "
                           "frozen — no video feature this run", run_id, gen_id)
            return ()
        declared = run_worker_capabilities(run)
        if WORKER_CAPABILITY_VIDEO_V2 not in declared:
            logger.warning("marketing script: MARKETING_VIDEO_FEATURES lists %s, but the worker holding run_id=%s "
                           "(worker_version=%r, attempt %s) did not declare %r on its claim — an older worker image; "
                           "no video feature this run, today's video (deploy the Batch 2 marketing worker)", listed,
                           run_id, str(run.get("worker_version") or "")[:64], run.get("attempts"),
                           WORKER_CAPABILITY_VIDEO_V2)
            return ()
        can = tuple(wanted)
        if VIDEO_FEATURE_DATA_CARD in can:
            needed = [WORKER_CAPABILITY_NEWS_TEMPLATES, *layout_capabilities(image_layout)]
            missing = [c for c in needed if c not in declared]
            if missing:
                logger.warning("marketing script: the worker holding run_id=%s (worker_version=%r) declared %r but "
                               "not %s — it cannot draw this template's %r picture; no data card this run",
                               run_id, str(run.get("worker_version") or "")[:64], WORKER_CAPABILITY_VIDEO_V2,
                               " + ".join(repr(c) for c in missing), str(image_layout)[:20])
                can = tuple(f for f in can if f != VIDEO_FEATURE_DATA_CARD)
        return can

    async def _holder_image_block(self, run_id: str, gen_id: str, *, template: bool = False,
                                  image_layout: Any = None, holder: Any = None) -> Optional[str]:
        """None when the worker that holds `run_id` declared WORKER_CAPABILITY_POST_IMAGE on its claim (the
        server-owned `metadata.worker_capabilities`, replaced by every claim that takes the run) — and,
        for a `template` output, WORKER_CAPABILITY_NEWS_TEMPLATES too (only that worker draws an
        `image_spec`; an older one would draw the alt text, which the server refuses), plus
        WORKER_CAPABILITY_LAYOUTS_2B when its `image_layout` is a drop-2b layout (WORKER_LAYOUTS_2B: a
        drop-2a image refuses a `pair` / `grid` loudly, failing the day), or WORKER_CAPABILITY_LAYOUT_FEATURE
        when it is the `feature` layout (2026-11-15: an older image refuses it the same way) —
        `layout_capabilities`. Read at write time, so a re-claim by another worker during a slow generation
        is honoured.

        Otherwise the IMAGE_FALLBACK_REASONS code that freezes the run's posts as TEXT — the posts every
        worker makes — and that the freeze records (`image_fallback`), both logged WARNING:
        "worker_capability" when the holder did not declare what the picture needs, "freeze_failed" when
        the run cannot be read at all (a ledger blip is never blamed on the worker).

        `holder` (Batch 2): the `_read_holder` answer `_frozen_output` already has — its one write-time
        read serves this check and the video one; None (a direct call) reads the run here."""
        run, error = holder if isinstance(holder, tuple) and len(holder) == 2 else await self._read_holder(run_id)
        if error is not None:
            logger.warning("marketing script: could not read run_id=%s generation=%s to check its worker can "
                           "render the post image (%s: %s) — this run's posts are frozen as text",
                           run_id, gen_id, type(error).__name__, error)
            return "freeze_failed"
        if run is None:
            logger.warning("marketing script: run_id=%s generation=%s not found when its formats were frozen — "
                           "this run's posts are frozen as text", run_id, gen_id)
            return "freeze_failed"
        declared = run_worker_capabilities(run)
        needed = ([WORKER_CAPABILITY_POST_IMAGE] + ([WORKER_CAPABILITY_NEWS_TEMPLATES] if template else [])
                  + (list(layout_capabilities(image_layout)) if template else []))
        missing = [c for c in needed if c not in declared]
        if not missing:
            return None
        drop = ("drop-2b" if WORKER_CAPABILITY_LAYOUTS_2B in missing
                else "feature-layout" if WORKER_CAPABILITY_LAYOUT_FEATURE in missing
                # news-v23 phase 3: a theme's `ranked` picture (the market-value angle) built before a re-claim
                else "theme-angles" if WORKER_CAPABILITY_THEME_ANGLES in missing
                else "drop-2" if template else "drop-1")
        logger.warning("marketing script: MARKETING_IMAGE_POSTS is on, but the worker holding run_id=%s "
                       "(worker_version=%r, attempt %s) did not declare %s on its claim — an older worker "
                       "image; this run's posts are frozen as TEXT (deploy the %s marketing worker)",
                       run_id, str(run.get("worker_version") or "")[:64], run.get("attempts"),
                       " + ".join(repr(c) for c in missing), drop)
        return "worker_capability"

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
            **_claude_patch(row, getattr(e, _CLAUDE_USAGE_ATTR, None), run_id, gen_id),
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
