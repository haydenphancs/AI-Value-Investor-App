"""Report-side gate for the Caydex Fair Value Estimate (settings.DCF_ENABLED).

Two pure helpers, kept in a module that depends only on settings so every report read path can
import them without a cycle (the collector, the report caches, the endpoints, the chat resolver).

* `report_dcf_source_matches(payload)` — a report records which DCF it was BUILT with in
  `wall_street_consensus.dcf_source` ("caydex" | "fmp"; absent on older reports = "fmp"). A shared
  report cache must not hand a report built under one setting to a request made under the other:
  after enabling, an FMP-DCF report would sit beside the Caydex Analysis tab; after disabling
  (the kill switch), a report whose valuation was DERIVED from the estimate would be re-served and
  copied into new rows. A mismatch is a cache miss.

* `strip_caydex_if_disabled(payload)` — while the switch is off, the published estimate block is
  dropped from any STORED report on its way out (report endpoints, chat grounding). Stored rows
  are never rewritten: a user's saved report is a frozen snapshot, and a PDF file already
  generated is not re-rendered (documents/OWNER_TASKS.md §2.1 says so).
"""

from __future__ import annotations

from typing import Any

from app.config import settings

CAYDEX_SOURCE = "caydex"
FMP_SOURCE = "fmp"


def current_dcf_source() -> str:
    return CAYDEX_SOURCE if settings.DCF_ENABLED else FMP_SOURCE


def report_dcf_source_matches(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    ws = payload.get("wall_street_consensus")
    built = ws.get("dcf_source") if isinstance(ws, dict) else None
    return (built or FMP_SOURCE) == current_dcf_source()


def strip_caydex_if_disabled(payload: Any) -> Any:
    if settings.DCF_ENABLED or not isinstance(payload, dict):
        return payload
    ws = payload.get("wall_street_consensus")
    if not isinstance(ws, dict) or ws.get("caydex_fair_value") is None:
        return payload
    return {**payload, "wall_street_consensus": {**ws, "caydex_fair_value": None}}

