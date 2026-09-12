"""Run a synchronous PostgREST statement OFF the event loop.

WHY THIS EXISTS
---------------
`supabase-py` is synchronous. Railway runs exactly ONE uvicorn worker (pinned by
`tests/test_deploy_command_parity.py`, because every lifespan job in `app/main.py` is
unclaimed and safe only under that assumption). So a bare `.execute()` inside an
`async def` does not merely slow that request down — it suspends the whole process for the
round trip, and EVERY other in-flight request waits behind it.

That is not theoretical: it is `project_overview_event_loop_blocking` — three synchronous
Supabase calls on `/stocks/{t}/overview` were enough to stall the endpoint under ordinary
load. That incident was fixed at three call sites; 92 more were still on the loop when this
helper was written (2026-09-12), across the watchlist / portfolio / tracking / auth / whale
paths — i.e. the CRUD a user touches constantly.

USAGE
-----
Everything before `.execute()` is pure local object building — no I/O — so only the
execution has to move::

    rows = (await sb_exec(supabase.table("x").select("*").eq("id", i))).data or []

⚠️ KEEP THE OUTER PARENTHESES when you read an attribute off the result. `await` binds
LOOSER than attribute access, so `await sb_exec(q).data` parses as `await (sb_exec(q).data)`
and fails at runtime.

⚠️ This does NOT make a sequence of statements atomic, and it does not dedup. It moves one
round trip off the loop, nothing more. For a delete-then-insert that must be replayable,
use `retry_idempotent_async` from `app.utils.supabase_errors`.
"""

from __future__ import annotations

import asyncio
from typing import Any


async def sb_exec(query: Any) -> Any:
    """`query.execute()`, run in a worker thread.

    `query` is a built-but-unexecuted postgrest builder (or any object with a synchronous
    `.execute()`). Exceptions propagate unchanged, so every existing `except` around a call
    site keeps matching what it matched before.
    """
    return await asyncio.to_thread(query.execute)
