"""
Alternative.me API client — Crypto Fear & Greed Index.

Free API, no auth key required. Updates once daily.
Endpoint: https://api.alternative.me/fng/?limit=30

In-memory cache with 15-minute TTL (index only changes daily).
"""

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class FearGreedUnavailableException(Exception):
    """No reading is available (upstream failed with a cold cache, or answered nothing).

    Distinct from a bad reading on purpose: the endpoint maps it to 502 and iOS hides the
    gauge, which is the honest render. A fabricated 50 / "Neutral" is byte-identical to a
    real reading and used to ship for 15 minutes after any upstream blip.
    """


def _readable(entry: Any) -> bool:
    """An entry whose `value` parses as an int in 0..100."""
    if not isinstance(entry, dict):
        return False
    try:
        v = int(str(entry.get("value", "")).strip())
    except (TypeError, ValueError):
        return False
    return 0 <= v <= 100

_BASE_URL = "https://api.alternative.me/fng/"
_CACHE_TTL = 900  # 15 minutes

_cache: Optional[List[Dict[str, Any]]] = None
_cache_ts: float = 0


async def get_fear_greed_index(limit: int = 30) -> List[Dict[str, Any]]:
    """
    Fetch Crypto Fear & Greed Index data from Alternative.me.

    Returns list of dicts: [{"value": "40", "value_classification": "Fear",
                             "timestamp": "1551157200"}, ...]
    Ordered newest-first.
    """
    global _cache, _cache_ts

    if _cache is not None and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache[:limit]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_BASE_URL, params={"limit": limit})
            resp.raise_for_status()
            data = resp.json()

        entries = [e for e in (data.get("data") or []) if _readable(e)]
        if not entries:
            # An empty answer is NOT a reading. Caching it pinned "no data" for
            # `_CACHE_TTL` and the summary below turned it into a confident Neutral 50.
            logger.warning("Fear & Greed Index: upstream returned no readable entries")
            if _cache is not None:
                return _cache[:limit]
            raise FearGreedUnavailableException("Fear & Greed Index returned no entries")
        _cache = entries
        _cache_ts = time.time()

        logger.info(f"Fear & Greed Index: fetched {len(entries)} entries")
        return entries[:limit]

    except FearGreedUnavailableException:
        raise
    except Exception as e:
        logger.warning(f"Fear & Greed Index fetch failed: {type(e).__name__}: {e}")
        if _cache is not None:
            return _cache[:limit]
        # Cold cache and a failed fetch: there is no reading to give. Raising lets the
        # endpoint answer 502 and iOS hide the gauge — not paint a fabricated "Neutral".
        raise FearGreedUnavailableException(
            f"Fear & Greed Index unavailable: {type(e).__name__}: {e}"
        ) from e


def compute_fear_greed_summary(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute current, 7D average, and 30D average from raw entries.

    Returns:
        {
            "value": 40, "classification": "Fear",
            "value_7d": 35, "classification_7d": "Fear",
            "value_30d": 52, "classification_30d": "Neutral",
            "history": [{"value": 40, "classification": "Fear", "timestamp": "..."}, ...]
        }
    """
    entries = [e for e in (entries or []) if _readable(e)]
    if not entries:
        # 50 is a CLAIM ("Neutral"), not an absence. Byte-identical to a real reading,
        # so it can never be a fallback: refuse, and let the caller degrade.
        raise FearGreedUnavailableException("no Fear & Greed entries to summarise")

    def _classify(score: int) -> str:
        if score <= 20:
            return "Extreme Fear"
        elif score <= 40:
            return "Fear"
        elif score <= 60:
            return "Neutral"
        elif score <= 80:
            return "Greed"
        else:
            return "Extreme Greed"

    def _avg(items: List[Dict]) -> int:
        total = sum(int(e["value"]) for e in items)
        return round(total / len(items))

    current_val = int(entries[0]["value"])
    current_class = entries[0].get("value_classification", _classify(current_val))

    avg_7d = _avg(entries[:7])
    avg_30d = _avg(entries[:30])

    history = [
        {
            "value": int(e["value"]),
            "classification": e.get("value_classification", "Neutral"),
            "timestamp": e.get("timestamp", ""),
        }
        for e in entries
    ]

    return {
        "value": current_val,
        "classification": current_class,
        "value_7d": avg_7d,
        "classification_7d": _classify(avg_7d),
        "value_30d": avg_30d,
        "classification_30d": _classify(avg_30d),
        "history": history,
    }
