"""Shared degradation marker for the two report-generation paths.

There are TWO doors into the same ~17-Gemini + ~20-FMP pipeline, and they cost the same
20 credits on a cache miss:

  * `GET  /stocks/{ticker}/report`  → `TickerReportService`   (the "direct" path)
  * `POST /research/generate`       → `ResearchAgent`         (the "deep" path)

When Gemini is unavailable — quota circuit open, 429s exhausted, a 5xx — or simply returns
something unparseable, Stage A degrades to `stage_a_fallback()`: empty bull/bear cases, blank
narrative slots, sentinel prose. The deterministic numerics still merge in, so the result
still VALIDATES against `TickerReportResponse` and still renders. That is precisely what made
this invisible.

Untagged, such a shell is:
  1. charged 20 credits and returned as a success (no refund path ever fires — the request
     did not raise, so `except Exception` never runs),
  2. written to `ticker_report_cache`, where it is served FREE to every other user for the
     rest of the close cycle, and
  3. handed to subsequent *paying* research users via the shared-cache lookup, charging them
     20 credits each for the same gutted report.

The direct path was fixed first and these helpers lived in `ticker_report_service`. The deep
path was never wired up, so the identical bug stayed open on the more expensive door. They
live here now so there is one marker with one home and a single import for both callers.

The key is deliberately kept OUT of the response schema: it rides on the internal Stage A
shell dict and on the returned report under a leading-underscore key, so it never has to be
added to `TickerReportResponse` and can never break the iOS decoder.
"""
from __future__ import annotations

# Set on the Stage A *shell* by the generators.
_DEGRADED_KEY = "_stage_a_degraded"

# Set on the *assembled report* by the orchestrators, for endpoint-level refund decisions.
REPORT_DEGRADED_KEY = "_degraded"


def _mark_degraded(shell: dict, reason: str) -> dict:
    """Tag a fallback shell so the caller knows this report is not real."""
    if isinstance(shell, dict):
        shell[_DEGRADED_KEY] = reason
    return shell


def _degraded_reason(shell) -> str | None:
    return shell.get(_DEGRADED_KEY) if isinstance(shell, dict) else None


class DegradedReportError(Exception):
    """Raised when a generated report is a degraded shell and must not be delivered.

    The deep path signals non-delivery by RAISING rather than by returning a marker, because
    that is the only thing `_run_research_task` reacts to: its refund runs inside
    `except Exception`, so a degraded report that merely *returns* is indistinguishable from
    a success and the 20-credit precharge is never given back. Raising also stops the
    completion write and the `upsert_cached_report` seed that would otherwise poison the
    shared cache for every other user for the rest of the close cycle.

    Maps to `ErrorCode.GEMINI_UNAVAILABLE`, matching the direct path's 503.
    """

    def __init__(self, reason: str, ticker: str = "", persona: str = ""):
        self.reason = reason
        self.ticker = ticker
        self.persona = persona
        super().__init__(
            f"report generation degraded for {ticker}/{persona}: {reason}"
            if ticker or persona
            else f"report generation degraded: {reason}"
        )


def report_degraded_reason(report) -> str | None:
    """Read the marker off an ASSEMBLED report (not a Stage A shell).

    `assemble_report` merges real data over the shell, so the shell-level key does not
    reliably survive into the report — the orchestrator copies it across explicitly. Callers
    deciding whether to refund should use this.
    """
    if not isinstance(report, dict):
        return None
    return report.get(REPORT_DEGRADED_KEY) or report.get(_DEGRADED_KEY)
