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

        entries = data.get("data", [])
        _cache = entries
        _cache_ts = time.time()

        logger.info(f"Fear & Greed Index: fetched {len(entries)} entries")
        return entries[:limit]

    except Exception as e:
        logger.error(f"Fear & Greed Index fetch failed: {e}")
        if _cache is not None:
            return _cache[:limit]
        return []


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
    if not entries:
        return {
            "value": 50, "classification": "Neutral",
            "value_7d": 50, "classification_7d": "Neutral",
            "value_30d": 50, "classification_30d": "Neutral",
            "history": [],
        }

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
        if not items:
            return 50
        total = sum(int(e.get("value", 50)) for e in items)
        return round(total / len(items))

    current_val = int(entries[0].get("value", 50))
    current_class = entries[0].get("value_classification", _classify(current_val))

    avg_7d = _avg(entries[:7])
    avg_30d = _avg(entries[:30])

    history = [
        {
            "value": int(e.get("value", 50)),
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
