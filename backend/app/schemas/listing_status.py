"""The four "Delisted" wire fields, shared by every response that carries them.

Set together, by `app.services.listing_status_service`, only for a CONFIRMED delisting
(FMP's inactive flag + no trade in the latest US session + FMP's delisted-companies row dated
on or before today ET + no traded day after that row); otherwise every one is null, which
means "trading OR unknown" (fail open: exactly the behaviour before these fields existed).

    listing_status     "delisted" | null
    delisted_date      "YYYY-MM-DD" | null   FMP's delistedDate, a US Eastern calendar date
    delisted_exchange  "NASDAQ" | null        the exchange FMP's row names (null when it has none)
    last_trade_date    "YYYY-MM-DD" | null   the newest EOD session with volume > 0; null when
                                              FMP has no traded row in the window it reads

Carried on `StockOverviewCoreResponse`, `StockOverviewResponse`, `TrackedAssetResponse`,
`MarketPulseItemResponse`, `StockChartWidget` (declared on each, appended LAST, so shipped
builds that do not know them ignore them) and on the raw dicts of `GET /stocks/{t}` and
`GET /stocks/{t}/quote`. Existing fields keep their meaning: `price` is the last close,
`change_known` stays false, `market_status` stays the clock's.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple

LISTING_STATUS_DELISTED = "delisted"

LISTING_FIELDS: Tuple[str, str, str, str] = (
    "listing_status",
    "delisted_date",
    "delisted_exchange",
    "last_trade_date",
)


def null_listing_fields() -> Dict[str, Optional[str]]:
    """A FRESH all-null dict of the four fields (callers may mutate it)."""
    return {key: None for key in LISTING_FIELDS}


# Read-only: callers `update()` responses with it, and a shared mutable default would be one
# stray assignment away from labelling every ticker.
NULL_LISTING_FIELDS: Mapping[str, Optional[str]] = MappingProxyType(null_listing_fields())
