"""
Shared helpers for earnings timing.

Both ``earnings_service`` (TickerDetailView → Financials tab → Earnings
section's ``next_earnings_date``) and ``tracking_service`` (watchlist
Earnings Alert card) read FMP's ``earnings-calendar`` endpoint. Without
these helpers each call-site parsed FMP's free-form ``time`` field
differently, causing the same NVDA Feb-22-after-close event to show as
"after market close" in the alert but "Time Not Specified" in the
Financials tab.
"""

from typing import Optional


# ── Canonical timing tokens ────────────────────────────────────────

BEFORE_OPEN = "before_open"
AFTER_CLOSE = "after_close"
DURING_HOURS = "during_hours"
UNSPECIFIED = "unspecified"


def parse_fmp_timing(fmp_time: Optional[str]) -> str:
    """Normalize FMP's ``time`` field to one of the canonical tokens.

    FMP returns a free-form string like ``"bmo"``, ``"BMO"``,
    ``"before market"``, ``"amc"``, ``"AMC"``, ``"after market close"``,
    ``"dmh"``, or blank. We collapse every variant to one of four tokens
    so the alert and the Financials Earnings section always agree.
    """
    raw = (fmp_time or "").strip().lower()
    if not raw:
        return UNSPECIFIED
    if "bmo" in raw or "before" in raw:
        return BEFORE_OPEN
    if "amc" in raw or "after" in raw:
        return AFTER_CLOSE
    if "dmh" in raw or "during" in raw:
        return DURING_HOURS
    return UNSPECIFIED


# ── Display strings ────────────────────────────────────────────────

# iOS ``EarningsReportTiming`` enum rawValues — emitted by
# earnings_service.next_earnings_date.timing so iOS decodes cleanly.
_DISPLAY = {
    BEFORE_OPEN:  "Before Market Open",
    AFTER_CLOSE:  "After Market Close",
    DURING_HOURS: "During Market Hours",
    UNSPECIFIED:  "Time Not Specified",
}

# Human sentence fragment for alert description lines ("reports earnings
# {SENTENCE}"). ``None`` when unspecified so callers can omit the clause
# entirely rather than guess "after market close".
_SENTENCE = {
    BEFORE_OPEN:  "before market open",
    AFTER_CLOSE:  "after market close",
    DURING_HOURS: "during market hours",
}


def timing_display(token: str) -> str:
    """Return the iOS-compatible display string for a canonical token."""
    return _DISPLAY.get(token, _DISPLAY[UNSPECIFIED])


def timing_sentence(token: str) -> Optional[str]:
    """Human phrase for alert descriptions. ``None`` when unspecified so
    callers drop the timing clause instead of hallucinating a default.
    """
    return _SENTENCE.get(token)


# ── Alert DTO contract ─────────────────────────────────────────────
# tracking_service currently emits the narrower two-value token set
# ``"before_open"`` / ``"after_close"`` / ``None`` via the AlertResponse
# ``report_time`` field — iOS maps it through ``EarningsReportTime`` enum.
# Keep this function the single place that converts.

def alert_report_time(token: str) -> Optional[str]:
    """Return the token value expected by the iOS ``EarningsReportTime``
    enum (``"before_open"`` / ``"after_close"``) or ``None`` when the
    timing is unknown or intraday.
    """
    if token in (BEFORE_OPEN, AFTER_CLOSE):
        return token
    return None
