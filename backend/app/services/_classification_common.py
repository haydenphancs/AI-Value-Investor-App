"""Shared FMP-profile → `watchlist_items` classification mapping.

One definition, because two write paths need the identical mapping and they drifted:
`POST /tracking/holdings` persisted sector/industry/country/market_cap/beta, while
`POST /api/v1/watchlist` read only companyName and image off the very same profile
response and discarded the rest. A ticker added from the watchlist star was therefore
never classified by anything — `PortfolioInsightsService._enrich_missing` only ever sees
tickers that already carry shares/market_value — so `GET /tracking/assets` reported
`"sector": null` for it forever.

THE OMISSION RULE IS LOAD-BEARING. This returns only keys it actually resolved, never a
key mapped to `None`. Callers splat it into an insert/upsert payload, so emitting a
resolved-to-None key would clobber good stored enrichment on a re-add whose FMP fetch
partially failed, and would defeat the `country` column's `'US'` default. That is the
"$0.00 / Other-sector after re-add" bug; keep the caller free to `data.update(...)`
without thinking about it.
"""

from typing import Any, Dict, Mapping, Optional

__all__ = ["classification_from_profile"]


def _clean_str(value: Any) -> Optional[str]:
    """Trimmed non-empty string, else None. FMP sends "" and "  " for unknowns."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_float(value: Any) -> Optional[float]:
    """Finite float, else None.

    Rejects NaN/Infinity explicitly: FMP has served both for thinly-traded names, and
    Postgres `numeric` refuses NaN on write while `float('inf')` serializes to invalid
    JSON — either way an unguarded cast turns one weird ticker into a 500 on a hot path.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def classification_from_profile(profile: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Map an FMP company profile onto the `watchlist_items` classification columns.

    Returns only the keys that resolved — see the omission rule in the module docstring.
    Accepts `None`/`{}`/a malformed row and returns `{}` rather than raising: every
    caller is on a request path where a bad profile must degrade, not fail.
    """
    if not profile or not isinstance(profile, Mapping):
        return {}

    out: Dict[str, Any] = {}

    if (sector := _clean_str(profile.get("sector"))) is not None:
        out["sector"] = sector
    if (industry := _clean_str(profile.get("industry"))) is not None:
        out["industry"] = industry
    if (country := _clean_str(profile.get("country"))) is not None:
        out["country"] = country

    # `marketCap` on /stable, `mktCap` on the legacy shape — some cached rows still
    # carry the old key, so read both rather than losing the value on those.
    if (market_cap := _clean_float(
        profile.get("marketCap") if profile.get("marketCap") is not None
        else profile.get("mktCap")
    )) is not None:
        out["market_cap"] = market_cap

    # Beta is genuinely 0.0 for some instruments, so test against None, not falsiness.
    if (beta := _clean_float(profile.get("beta"))) is not None:
        out["beta"] = beta

    return out
