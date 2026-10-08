"""
The in-memory PUBLISH CLOCK behind the /go "early" bucket (SYSTEM_DESIGN_GUIDELINES §12.6).

Link scanners — Meta's fetchers, preview services, security scanners — fetch a post's /go link within
seconds to a few minutes of the post going out, and many send an ordinary desktop browser user agent
that no deny list can tell from a person (production, 2026-10-03/05: every "tap" counted in the first
week was one of them, all 1-200 s after a post went out). So a tap that arrives inside a short window
after its campaign's post went out is counted APART, under the server-constant key `<campaign>_early`
(`smart_link.EARLY_KEYS`): nothing is discarded, and the digest prints both numbers.

* Writer: `publisher_service.record_outcome` (`stamp`), when a post whose caption carries its OWN /go
  link (`post_copy.carries_go_link`) is PUBLISHED — or its send is AMBIGUOUS, so it may be live — or
  SUBMITTED to Upload-Post (which publishes it seconds later).
* Reader: `smart_link.record_hit` (`is_early`) — a dict read and a compare on the request path: no I/O,
  no logging, nothing that can raise for a well-typed campaign.
* One process: the web service runs ONE uvicorn worker (`main.py` logs ERROR at boot when
  UVICORN_WORKERS or WEB_CONCURRENCY asks for more), so the publisher and /go share this dict.
* A restart loses the open windows: a scan that follows a deploy within minutes of a post counts as
  people. That costs accuracy only — the digest labels the /go number approximate.

Stdlib only (a test pins it), so the request path imports nothing else through it.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Optional

#: The post is live now: X / Bluesky / an Upload-Post synchronous answer, or an AMBIGUOUS send.
PUBLISHED = "published"
#: Upload-Post accepted the job; it goes live seconds later (2-41 s in production, 2026-10-05).
SUBMITTED = "submitted"

#: Window length per stamp kind. Production 2026-10: X/Bluesky scanners within ~200 s of the post;
#: Upload-Post platforms live within ~41 s of the submit and scanned within seconds of going live.
WINDOW_SECONDS: Dict[str, float] = {PUBLISHED: 240.0, SUBMITTED: 300.0}
#: A stamp more than this far in the future (clock skew, a hand-edited time) opens nothing.
MAX_SKEW_SECONDS = 60.0
#: Campaigns are a closed set (one per post platform, 14); the cap only bounds a misuse.
_MAX_CAMPAIGNS = 32
_CAMPAIGN_MAX_CHARS = 40

#: campaign → the epoch second its early window closes.
_until: Dict[str, float] = {}


def _wall() -> float:
    return time.time()


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def stamp(campaign: Any, kind: Any, *, at: Any = None, now: Any = None) -> bool:
    """Open (or extend) `campaign`'s early window: from `at` (epoch seconds; default `now`) for
    `WINDOW_SECONDS[kind]`. Returns True when the window was opened or extended. Changes nothing and
    returns False for an unknown kind, a campaign that is not a short non-empty string, a non-finite
    time, a start more than MAX_SKEW_SECONDS in the future, a window already closed at `now`, or one
    that would end before the window already open (a window is never shortened). Validates instead of
    catching: it never raises."""
    now_s = _wall() if now is None else now
    if not isinstance(campaign, str) or not campaign or len(campaign) > _CAMPAIGN_MAX_CHARS:
        return False
    if not isinstance(kind, str) or kind not in WINDOW_SECONDS:
        return False
    start = now_s if at is None else at
    if not _finite(now_s) or not _finite(start) or start > now_s + MAX_SKEW_SECONDS:
        return False
    until = start + WINDOW_SECONDS[kind]
    if until <= now_s:
        return False
    current = _until.get(campaign)
    if current is not None and current >= until:
        return False
    if current is None and len(_until) >= _MAX_CAMPAIGNS:
        for closed in [c for c, u in _until.items() if u <= now_s]:
            del _until[closed]
        if len(_until) >= _MAX_CAMPAIGNS:
            return False
    _until[campaign] = until
    return True


def is_early(campaign: str, now: Optional[float] = None) -> bool:
    """Is `campaign`'s early window open at `now` (epoch seconds; default the wall clock)? The window
    is half-open: at exactly its end the tap is ordinary again."""
    until = _until.get(campaign)
    if until is None:
        return False
    return (_wall() if now is None else now) < until


def open_until(campaign: str) -> Optional[float]:
    """When `campaign`'s window closes (epoch seconds), or None — for logs and tests."""
    return _until.get(campaign)


def clear() -> None:
    """Forget every window (tests)."""
    _until.clear()
