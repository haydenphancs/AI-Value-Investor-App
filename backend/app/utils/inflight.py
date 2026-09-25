"""Helpers for the in-flight dedup futures every cache-aside service shares.

The leader of a dedup group creates an ``asyncio.Future``, parks it in an ``_inflight``
dict so concurrent callers can ``await`` (or ``asyncio.shield``) it, and resolves it when
its own fetch finishes. On failure the leader stores the exception on the future AND
re-raises it on its own frame.

When no joiner ever attached, which is the common case for a single cold request, nobody
reads that stored exception. Once the future is garbage-collected asyncio's default
handler logs "Future exception was never retrieved" with a traceback at ERROR, and the
Sentry logging integration turns each one into a second event for a failure the leader
already reported (5 per 2 h on Railway, 2026-09-11). ``fail_shared_future`` stores the
exception and marks it retrieved in one step. Joiners are unaffected: their ``await``
still raises the stored exception.
"""

from __future__ import annotations

import asyncio

__all__ = ["fail_shared_future"]


def fail_shared_future(fut: "asyncio.Future", exc: BaseException) -> bool:
    """Fail a shared in-flight future with ``exc`` and mark the exception retrieved.

    A no-op when ``fut`` is already done (a result, an exception or a cancel landed first),
    so it can replace the ``if not fut.done(): fut.set_exception(exc)`` idiom one for one.
    Returns True when this call resolved the future.

    ``exc`` is stored as given. Most leaders turn their own cancel into a ``RuntimeError``
    first, so joiners fail through their normal error path; the ``except BaseException``
    leaders hand a ``CancelledError`` on as-is. That choice stays with each leader.
    """
    if fut.done():
        return False
    fut.set_exception(exc)
    # Mark retrieved. The leader re-raises `exc` itself; see the module docstring.
    fut.exception()
    return True
