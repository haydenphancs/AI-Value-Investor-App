"""Typed exceptions of the Wiser weekly content engine (plan 2026-10-22, §1.1 / §1.8).

Two families, kept apart on purpose — a broad `except LearnWeeklyError` in the scheduler must never
swallow a narrator-API refusal, and the reverse:

* `LearnNarratorError` — raised behind the INTERNAL narrator API (`/api/v1/internal/learn/*`), whose
  only caller is the standalone `learn_narrator` worker (never iOS). `classify_exception` in
  `app/api/error_response.py` turns each into a worker-facing `LEARN_NARRATOR_*` code:

      LearnNarratorNotFound        → LEARN_NARRATOR_NOT_FOUND       404  a job / piece id the ledger does not know
      LearnNarratorJobNotHeld      → LEARN_NARRATOR_JOB_NOT_HELD    409  the caller's claim (`<attempts>.<nonce>`)
                                                                         no longer holds the job — re-claimed,
                                                                         cancelled, or its lease ran out
      LearnNarratorRequestInvalid  → LEARN_NARRATOR_REQUEST_INVALID 422  the request breaks the contract (timing
                                                                         table, engine, status) — never retried
      LearnNarratorAssetMismatch   → LEARN_NARRATOR_ASSET_MISMATCH  409  the uploaded object's size / type / bytes-
                                                                         per-second band is not what was registered
      LearnNarratorAssetMissing    → LEARN_NARRATOR_ASSET_MISSING   409  the worker said "uploaded"; the bucket has
                                                                         no such object
      LearnNarratorLedgerError     → LEARN_NARRATOR_LEDGER_ERROR    503  a Supabase / Storage read or write failed —
      LearnNarratorError (the base)                                     the one code the worker RETRIES

  404/409/422 are terminal for the worker's current call (it records the failure and the next cron run
  re-claims); 503 is retried — exactly marketing's worker contract (`MARKETING_*`, rules/marketing.md §2).

* `LearnWeeklyError` — the pipeline's own failures that never cross an HTTP boundary (scheduler, ledger
  transitions, topic, fact sheet, writer, review, publish). Each stage catches, logs with its identifiers
  and records `last_error` / `last_stage` on the piece. Should one ever reach a door anyway, the
  classifier answers LEARN_NARRATOR_LEDGER_ERROR 503 ("the server could not do it now"), never a
  heuristic guess: a message saying "timeout" or "429" would otherwise be filed as an FMP outage.

Classification is by CLASS NAME along the MRO, restricted to classes defined in THIS module
(`error_response._LEARN_EXCEPTION_CODES`): a subclass declared anywhere — `class WriterCapsReached(
LearnWeeklyError)` in `writer.py` — inherits its parent's code with no new branch, and a same-named class
elsewhere is never mistaken for one of these. `tests/test_learn_error_codes_walk.py` pins every class
here to its (code, status); a new class in this file must be added there deliberately.

Every class takes an optional set of IDENTIFIERS (`ids`), appended to the message so a log line or a
worker-side error body is greppable without a repro (CLAUDE.md "Errors carry context + identifiers").
Only the keys in CONTEXT_KEYS are rendered — never a token, a claim nonce, a signed URL or model text; an
unknown key is left out with a WARNING (never a leak, and never a TypeError in place of the typed error).

Pure: stdlib only; nothing here imports FMP, Supabase, Gemini or `app.api` (the classifier imports
nothing from here either — it matches names, so this module stays free to import from anywhere).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

#: The identifiers an exception may carry into its message. Identifiers only — see the module docstring.
#: `catalog_slug` (migration 207) names the hand-written Money Moves article a catalog narration job
#: narrates (a job with no piece): an owner-authored kebab-case slug, never a secret.
CONTEXT_KEYS = frozenset({
    "job_id", "piece_id", "catalog_slug", "period_key", "round", "slug", "stage", "step", "engine", "status",
    "op",
})
#: Each identifier's rendered length cap (one log line; an id is far shorter).
_ID_VALUE_CAP = 80


def _render_ids(ids: Dict[str, Any]) -> str:
    parts = []
    for key in sorted(ids):
        text = " ".join(str(ids[key]).split())   # folded onto one line: no log-line injection
        parts.append(f"{key}={text[:_ID_VALUE_CAP]}")
    return " ".join(parts)


def _identified(exc: Exception, message: Any, ids: Dict[str, Any]) -> str:
    """The shared constructor body of both bases: `Cls("what failed", job_id=…, piece_id=…)` →
    "what failed [job_id=… piece_id=…]". `None` identifiers are dropped; `exc.ids` keeps the rest for a
    caller that logs them as fields. An identifier key outside CONTEXT_KEYS is DROPPED with a WARNING —
    never rendered (it may be a secret), and never a TypeError that would replace the typed exception the
    caller meant to raise on what is usually its failure path."""
    unknown = sorted(set(ids) - CONTEXT_KEYS)
    if unknown:
        logger.warning("%s: identifier key(s) %s are not in CONTEXT_KEYS — left out of the message",
                       type(exc).__name__, unknown)
    exc.ids = {k: v for k, v in ids.items() if k in CONTEXT_KEYS and v is not None}
    rendered = _render_ids(exc.ids)
    text = str(message)
    return f"{text} [{rendered}]" if rendered else text


# ── the internal narrator API (worker-facing) ──────────────────────────────────────────────────


class LearnNarratorError(Exception):
    """Base of the narrator-API family. Raised as itself it means "the server could not complete it"
    (LEARN_NARRATOR_LEDGER_ERROR, 503 — the worker retries)."""

    ids: Dict[str, Any]

    def __init__(self, message: Any = "", **ids: Any) -> None:
        super().__init__(_identified(self, message, ids))


class LearnNarratorNotFound(LearnNarratorError):
    """A narration job (or its piece) id the ledger does not know. 404 — a worker bug or a deleted row;
    the worker drops the job."""


class LearnNarratorJobNotHeld(LearnNarratorError):
    """A heartbeat / upload / complete / fail on a job the caller's claim no longer holds: it was
    re-claimed (its lease ran out), cancelled (the piece expired or was rejected), or finished. 409,
    never retried — the worker stops working on that job."""


class LearnNarratorRequestInvalid(LearnNarratorError):
    """The worker asked for something the contract forbids — an engine it did not declare, a timing table
    that is not one entry per block / not monotonic / not the server's sentence split / outside the
    seconds-per-word band, a status it may not set. 422: the same request can never succeed."""


class LearnNarratorAssetMismatch(LearnNarratorError):
    """The uploaded object is not what the job registered (size, content type, or a bytes-per-second rate
    outside the engine's band). The server deletes it and fails the round. 409."""


class LearnNarratorAssetMissing(LearnNarratorError):
    """The worker reported an upload, but the bucket has no such object. 409 — re-upload, never "ready"
    on the worker's word."""


class LearnNarratorLedgerError(LearnNarratorError):
    """A Supabase or Storage read/write behind the narrator API failed. 503: transient, retried."""


# ── the pipeline (never crosses an HTTP boundary) ──────────────────────────────────────────────


class LearnWeeklyError(Exception):
    """Base of the pipeline's own failures (scheduler, ledger, topic, fact sheet, writer, review,
    publish). Subclass it per stage; each stage logs and records it on the piece. If one ever reaches
    a door: LEARN_NARRATOR_LEDGER_ERROR, 503 (module docstring)."""

    ids: Dict[str, Any]

    def __init__(self, message: Any = "", **ids: Any) -> None:
        super().__init__(_identified(self, message, ids))
