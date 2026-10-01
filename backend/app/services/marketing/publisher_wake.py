"""
Wake the marketing publisher loop early (design doc §12.10).

The loop sleeps `MARKETING_PUBLISHER_INTERVAL_SECONDS` (10 min) between ticks. An Approve or a
confirmed Retract in Telegram (the webhook, `review_service.handle_update`) calls `wake()` so the
post goes out — or comes down — within seconds instead of up to ten minutes later. The webhook
only RECORDS the decision; the publisher loop does the platform call (rules/marketing.md §2).

In-process: the web service runs ONE uvicorn worker (pinned by tests/test_deploy_command_parity.py),
so the webhook and the loop share this module. A lost wake (another process, a restart) costs
nothing but the normal interval. Its own module so `review_service` and `publisher_service` can both
import it without a cycle.
"""

from __future__ import annotations

import asyncio
from typing import Optional

_event: Optional[asyncio.Event] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def _current_event() -> Optional[asyncio.Event]:
    """The Event of the RUNNING loop, re-created when the loop changes (each pytest test runs its
    own loop; an Event bound to a dead loop would never fire). None outside a running loop."""
    global _event, _loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _event is None or _loop is not loop:
        _event, _loop = asyncio.Event(), loop
    return _event


def wake() -> None:
    """Ask the loop to run its next tick now. A no-op outside a running event loop."""
    event = _current_event()
    if event is not None:
        event.set()


async def wait(timeout: float) -> bool:
    """Sleep up to `timeout` seconds, returning early (True) when `wake()` is called. Clears the
    signal, so one wake triggers one early tick."""
    event = _current_event()
    if event is None:  # pragma: no cover — always called from the loop
        await asyncio.sleep(timeout)
        return False
    try:
        await asyncio.wait_for(event.wait(), timeout=max(float(timeout), 0.0))
        woken = True
    except asyncio.TimeoutError:
        woken = False
    event.clear()
    return woken
