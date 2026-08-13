"""Shared classification + retry for transient Supabase/PostgREST failures.

Why this module exists
──────────────────────
Supabase sits behind Cloudflare. When the edge cannot get a clean answer from the
origin it returns an **HTML error page** (``520: Web server is returning an unknown
error``, also 502/504/525…). postgrest then fails to parse that body as PostgREST
error JSON and raises::

    postgrest.exceptions.APIError(
        message = "JSON could not be generated",
        code    = 520,                      # ← an INT: it is httpx Response.status_code
        details = "b'<!DOCTYPE html>…'",
    )

That single failure mode was observed hitting FIVE unrelated call sites in one
14-day window (whale hydration snapshot upsert, whale holdings sync, industry
benchmark lookup, the research-reconciliation credit-refund sweep, and the
``/tracking/assets`` watchlist read — the last one 503-ing real users). Every site
logged it at ERROR, so each one opened its own Sentry issue for what is a single
upstream blip.

``app/database.py::_force_http1_on_postgrest`` already fixed a *different* flavour
of ``APIError('JSON could not be generated')`` — the stale HTTP/2 pooled-connection
reuse race. This one comes from the Cloudflare edge instead, which is why that
mitigation did not close it.

The load-bearing invariant
──────────────────────────
``postgrest.exceptions.generate_default_error_message`` sets ``code`` to
``r.status_code`` (an **int**), and that function is called ONLY on the
"body was not PostgREST JSON" path. Every ordinary PostgREST error carries a
**str** code instead (``"23505"``, ``"PGRST116"``, ``"PGRST204"``). So:

    an int ``.code`` *definitionally means* "the origin/edge answered with
    something that is not a PostgREST response".

⚠️ **Never classify on ``str(exc)``.** postgrest 1.1.1 defines ``__repr__`` but not
``__str__``, so locally ``str(e)`` renders the raw dict while production renders
``"Error 520:\\nMessage: …"``. A substring check passes on one and silently fails on
the other. Everything here keys on structured fields.

Retry safety — read this before adding a call site
──────────────────────────────────────────────────
A 520 means *the edge could not parse what the origin said*. It carries **zero
information about whether Postgres committed**. So the test is NOT "is this a read",
it is:

    would replaying this exact unit of work against a database that may have
    already applied it converge to the same final state?

Safe: reads; ``upsert(on_conflict=…)``; a PK-keyed ``update`` with a fixed payload;
a whole delete-then-insert BLOCK (the leading DELETE wipes any partial commit).
Unsafe: a bare ``insert`` into a table with a unique key (a committed-but-unacked
row makes the retry raise 23505), an ``insert`` into a table WITHOUT one (the retry
silently duplicates), and anything guarded by a select-exists check that a partial
commit would flip.

That is why there is deliberately **no** ``retry_supabase()`` and **no**
``idempotent=True`` flag — a boolean has to default to something, and the wrong
default is this entire bug class. Both entry points are named ``retry_idempotent_*``
so the precondition is stated at every call site, and they take a **callable**, so
wrapping a delete-then-insert block forces you to hoist the DELETE into the same
closure. You cannot express "retry insert #17".
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

_module_logger = logging.getLogger(__name__)

#: How far to walk ``__cause__`` / ``__context__``. Bounded so a long implicit
#: chain cannot make an unrelated failure look transient by association.
MAX_CAUSE_DEPTH = 5

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.25

#: Gateway/edge statuses that mean "the request never got a clean answer".
#: 520-530 are the Cloudflare-specific family (520 is the one confirmed in prod).
#:
#: 429 is deliberately ABSENT: it is backpressure, and retrying after 0.25s makes
#: it worse. 4xx are absent because they are deterministic — a retry reproduces them.
TRANSIENT_HTTP_STATUS = frozenset(
    {
        408,  # request timeout
        500, 502, 503, 504,  # origin / gateway
        520, 521, 522, 523, 524, 525, 526, 527, 530,  # Cloudflare edge family
    }
)

#: Postgres SQLSTATEs worth retrying: connection-lost (class 08) and
#: "too many connections" (53300). Deliberately EXCLUDES 57014
#: (statement_timeout) — a retry reproduces the same slow query and doubles load.
TRANSIENT_SQLSTATES = frozenset({"08000", "08003", "08006", "08P01", "53300"})

#: Postgres unique-violation. Callers use this to tell "someone else won a race"
#: apart from a genuine failure. It must NEVER be transient — see below.
UNIQUE_VIOLATION_SQLSTATE = "23505"


def _status_from_code(code: Any) -> Optional[int]:
    """Normalize a postgrest ``.code`` into an HTTP status, or None.

    ``.code`` is an **int** on the "body was not PostgREST JSON" path and a **str**
    everywhere else, so both have to be handled. A str is only read as a status when
    it is exactly 3 digits — which is what keeps ``"23505"`` (5 digits) from ever
    being mistaken for one.
    """
    # bool is an int subclass; `True in {…}` would compare equal to 1. Reject first.
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        text = code.strip()
        if len(text) == 3 and text.isdigit():
            return int(text)
    return None


def _classify_one(exc: BaseException) -> bool:
    """Transient-ness of a SINGLE exception, ignoring its cause chain."""
    # 1. Structured postgrest/PostgREST code. A hard decision, not a fall-through:
    #    if the error carries a code, that code IS the answer.
    code = getattr(exc, "code", None)
    if code is not None:
        status = _status_from_code(code)
        if status is not None:
            return status in TRANSIENT_HTTP_STATUS
        if isinstance(code, str):
            # A SQLSTATE or a PGRST* code. 23505 / PGRST116 / PGRST204 all land
            # here and all correctly answer False.
            return code.strip().upper() in TRANSIENT_SQLSTATES

    # 2. httpx.HTTPStatusError carries the status on .response, not .code.
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int) and not isinstance(response_status, bool):
        return response_status in TRANSIENT_HTTP_STATUS

    # 3. httpx transport-level failures. (Ported verbatim from the original
    #    sector_benchmark_lookup._is_transient so its 12 tests keep passing.)
    try:
        import httpx

        if isinstance(
            exc,
            (
                httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError,
                httpx.WriteError, httpx.PoolTimeout, httpx.ConnectTimeout,
                httpx.ReadTimeout,
            ),
        ):
            return True
        # A stale HTTP/2 pooled connection that Supabase closed (GOAWAY / idle
        # timeout) surfaces on REUSE as LocalProtocolError('... in state
        # ConnectionState.CLOSED'). That is a connection-reuse race, not a
        # malformed-request bug. A GENUINE local protocol bug carries no "closed"
        # state, so it still surfaces as ERROR.
        if isinstance(exc, httpx.LocalProtocolError) and "closed" in str(exc).lower():
            return True
    except Exception:  # pragma: no cover — httpx is a hard dependency
        pass

    # 4. String fallbacks, for when the error arrives as a raw httpcore/h2 type
    #    rather than wrapped as an httpx exception.
    msg = str(exc).lower()
    if "server disconnected" in msg or "connectionstate.closed" in msg:
        return True

    # 5. Classify by ORIGIN. The same stale-HTTP/2 race can surface as a BARE
    #    KeyError(<stream_id>) from deep inside the transport stack (odd ints like
    #    307 / 431) — no httpx type, no telltale message. An exception raised from
    #    within httpcore/h2 internals is a connection-layer blip. A genuine
    #    KeyError in OUR code (e.g. row["metric_name"] schema drift) carries no
    #    transport frame, so it stays ERROR.
    tb = getattr(exc, "__traceback__", None)
    while tb is not None:
        top_pkg = tb.tb_frame.f_globals.get("__name__", "").split(".", 1)[0]
        if top_pkg in ("httpcore", "h2", "hpack", "hyperframe"):
            return True
        tb = tb.tb_next
    return False


def _walk_chain(exc: BaseException, predicate: Callable[[BaseException], bool]) -> bool:
    """Apply ``predicate`` to ``exc`` and its cause chain, bounded and cycle-safe.

    Unwrapping matters: ``tracking_service`` raises
    ``WatchlistUnavailableError(...) from api_error``, so without this the wrapper
    hides the postgrest code entirely and the whole classification misses.

    ``__cause__`` (explicit ``raise … from``) is preferred over ``__context__``
    (implicit, set by raising inside an ``except``). The depth bound keeps an
    unrelated bug that merely happened to be raised inside an ``except APIError:``
    from inheriting its transient-ness forever.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    for _ in range(MAX_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if predicate(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def is_transient_supabase_error(exc: BaseException) -> bool:
    """True for a Supabase/PostgREST failure worth retrying and logging at WARNING.

    The single classifier every Supabase call site should use, so the five sites
    that were each reporting the same Cloudflare 520 separately stay in sync.

    Explicitly NOT transient — these are deterministic, and retrying a constraint
    violation is a correctness hazard, not just wasted work:
    23505 (unique violation), PGRST116 (no rows), PGRST204 (unknown column /
    schema drift), every 4xx, and 429 (backpressure).
    """
    return _walk_chain(exc, _classify_one)


def _is_unique_violation_one(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    return isinstance(code, str) and code.strip() == UNIQUE_VIOLATION_SQLSTATE


def is_unique_violation(exc: BaseException) -> bool:
    """True for a Postgres 23505 unique-constraint violation.

    Deliberately disjoint from :func:`is_transient_supabase_error` — a 23505 means
    "someone else already wrote this row", which callers handle by adopting the
    winner, never by retrying the same write. Both classifiers are pinned against
    the same object in the tests so the two can never drift into agreement.
    """
    return _walk_chain(exc, _is_unique_violation_one)


def retry_idempotent_sync(
    op: Callable[[], T],
    *,
    what: str,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    logger: Optional[logging.Logger] = None,
) -> T:
    """Run ``op`` and retry it on a transient Supabase failure.

    ⚠️ ``op`` MUST be idempotent — see the module docstring. It is a callable rather
    than a statement precisely so that wrapping a delete-then-insert sequence forces
    the whole block (including its leading DELETE) into one replayable unit.

    Retries log at WARNING. The final failure is re-raised **untouched and unlogged**
    so the caller keeps ownership of the level decision (transient → WARNING,
    genuine bug → ERROR + stack).
    """
    log = logger or _module_logger
    last_attempt = max(1, attempts) - 1
    for attempt in range(max(1, attempts)):
        try:
            return op()
        except Exception as e:
            if attempt >= last_attempt or not is_transient_supabase_error(e):
                raise
            log.warning(
                "Supabase transient error on %s (attempt %d/%d): %s: %s — retrying",
                what, attempt + 1, attempts, type(e).__name__, e,
            )
            # Looked up on the MODULE, never captured as a default argument:
            # tests monkeypatch `supabase_errors.time.sleep`, and a def-time
            # capture would bypass the patch and really sleep.
            time.sleep(backoff_seconds * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover


async def retry_idempotent_async(
    op: Callable[[], T],
    *,
    what: str,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    logger: Optional[logging.Logger] = None,
) -> T:
    """Async form of :func:`retry_idempotent_sync`, for use from ``async def``.

    ``op`` is a **sync** callable (the Supabase SDK is synchronous) and is run via
    ``asyncio.to_thread``, so neither the call nor the backoff blocks the event loop
    — required by CLAUDE.md on request paths.

    Same idempotency precondition, same WARNING-on-retry / re-raise-untouched
    contract as the sync form.
    """
    log = logger or _module_logger
    last_attempt = max(1, attempts) - 1
    for attempt in range(max(1, attempts)):
        try:
            return await asyncio.to_thread(op)
        except Exception as e:
            if attempt >= last_attempt or not is_transient_supabase_error(e):
                raise
            log.warning(
                "Supabase transient error on %s (attempt %d/%d): %s: %s — retrying",
                what, attempt + 1, attempts, type(e).__name__, e,
            )
            await asyncio.sleep(backoff_seconds * (attempt + 1))
    raise AssertionError("unreachable")  # pragma: no cover
