"""
The marketing publisher's platform registry (design doc §12.10).

ONE predicate decides whether a platform publishes: `enabled_platforms()` — listed in
`MARKETING_PUBLISH_PLATFORMS` AND its adapter's credentials are complete (`configured()`). The
publisher's query and the Telegram review sweep both read it, so a platform that cannot publish
never gets Approve buttons (its posts arrive as a read-only preview) and a listed platform with a
missing credential is simply off — fail-closed, never half-on.

Stage 2 added the six Upload-Post platforms (`outlet_upload_post.ADAPTERS`).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.config import settings
from app.services.marketing.outlet_base import Adapter
from app.services.marketing.outlet_bluesky import ADAPTER as BLUESKY_ADAPTER
from app.services.marketing.outlet_upload_post import ADAPTERS as UPLOAD_POST_ADAPTERS
from app.services.marketing.outlet_x import ADAPTER as X_ADAPTER

logger = logging.getLogger(__name__)

#: platform → adapter. A platform missing here can never publish, whatever is listed.
ADAPTERS: Dict[str, Adapter] = {
    X_ADAPTER.platform: X_ADAPTER,
    BLUESKY_ADAPTER.platform: BLUESKY_ADAPTER,
    # Stage 2: TikTok, YouTube, Instagram (video) and Facebook, LinkedIn, Threads (text) through
    # Upload-Post — one adapter instance per platform.
    **UPLOAD_POST_ADAPTERS,
}

_unknown_logged: set = set()


def listed_platforms() -> List[str]:
    """`MARKETING_PUBLISH_PLATFORMS` parsed: comma-separated, case-insensitive, de-duplicated, in
    order. An unknown name is ignored with ONE error log per process (a typo must not be silent)."""
    out: List[str] = []
    for raw in str(settings.MARKETING_PUBLISH_PLATFORMS or "").split(","):
        name = raw.strip().lower()
        if not name or name in out:
            continue
        if name not in ADAPTERS:
            if name not in _unknown_logged:
                _unknown_logged.add(name)
                logger.error("marketing publisher: MARKETING_PUBLISH_PLATFORMS names %r, which has no "
                             "adapter (known: %s) — ignored", name, sorted(ADAPTERS))
            continue
        out.append(name)
    return out


def enabled_platforms() -> List[str]:
    """Listed AND configured — the platforms that publish (and get review buttons)."""
    return [p for p in listed_platforms() if ADAPTERS[p].configured()]


def adapter_for(platform: Optional[str]) -> Optional[Adapter]:
    return ADAPTERS.get(str(platform or ""))


def retract_capable(platform: Optional[str]) -> bool:
    """Does a published post on `platform` get a Retract button? (An API delete exists and the
    credentials to call it are set.)"""
    adapter = adapter_for(platform)
    return bool(adapter and adapter.retractable and adapter.configured_for_retract())
