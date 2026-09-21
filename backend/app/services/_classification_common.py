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

__all__ = ["classification_from_profile", "is_placeholder_text", "PLACEHOLDER_TEXT"]

# Strings that MEAN "unknown" and must never be stored or bucketed as a value. FMP sends
# "" / "  " for an unknown sector; our own `stock_overview_service._build_sector_industry`
# renders an empty sector as the literal "N/A" for the detail screen, and that string
# travelled through `company_profile_cache.profile_json` into `watchlist_items.sector` via
# the feed's backfill — where the Diversification card then drew a legend row named "N/A"
# (TestFlight 1.0 (8)) and `score_holdings` counted it as a sector of its own.
#
# ⚠️ Not "unknown": `portfolio_insights_service` legitimately emits an "Unknown" size
# bucket for a holding without a market cap, and FMP never sends that word for a sector.
PLACEHOLDER_TEXT = frozenset({
    "", "n/a", "na", "n.a.", "none", "null", "nan", "-", "\u2014", "\u2013",
})


# `country` is an ISO-3166 alpha-2 code, and "NA" is Namibia. The placeholder rule for that
# column must not swallow it — so the two-letter spellings are exempt there.
_ISO_CODE_LOOKALIKES = frozenset({"na"})


def is_placeholder_text(value: Any, *, iso_code: bool = False) -> bool:
    """True when *value* is absent or one of the strings that mean "unknown".

    ``iso_code=True`` for a column that holds ISO-3166 alpha-2 codes (`country`): the
    two-letter placeholder spellings are real codes there ("NA" = Namibia).
    """
    if value is None:
        return True
    text = str(value).strip().lower()
    if iso_code and text in _ISO_CODE_LOOKALIKES:
        return False
    return text in PLACEHOLDER_TEXT


def _clean_str(value: Any, *, iso_code: bool = False) -> Optional[str]:
    """Trimmed non-placeholder string, else None. FMP sends "" and "  " for unknowns, and
    a formatted profile can carry "N/A" — the omission rule (module docstring) applies to
    both: a placeholder country is omitted and the column's 'US' default applies."""
    if is_placeholder_text(value, iso_code=iso_code):
        return None
    return str(value).strip()


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
    if (country := _clean_str(profile.get("country"), iso_code=True)) is not None:
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
