"""
Pydantic response schema for the Caydex Fair Value Estimate (model `dcf-v1`).

Methodology: documents/research/dcf-methodology-v1.md. Built by
app/services/dcf_fair_value_service.py.

`status` is the ONLY required field besides `symbol`. Everything else is Optional, so a refusal
decodes as a refusal and a future field never breaks a shipped iOS build. The value is the same
for every caller — nothing here is per-user (hard rule 1 in the spec).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel


class DcfFairValueResponse(BaseModel):
    symbol: str
    status: Literal["ok", "refused"]
    model_version: Optional[str] = None

    # Refusal (status == "refused"): a stable machine code + one short user-facing sentence.
    refusal_code: Optional[str] = None
    refusal_reason: Optional[str] = None

    # The estimate (status == "ok"), per share, in `currency`.
    fair_value: Optional[float] = None          # headline: earnings-based method (E)
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    alternative_value: Optional[float] = None   # revenue-based method (R), the cross-check
    currency: Optional[str] = None

    # Assumptions, shown in the assumptions sheet. Rates are percentages (8.04 = 8.04 %).
    method: Optional[str] = None
    discount_rate_pct: Optional[float] = None
    terminal_growth_pct: Optional[float] = None
    risk_free_pct: Optional[float] = None
    equity_risk_premium_pct: Optional[float] = None
    beta: Optional[float] = None
    analyst_years: Optional[int] = None
    analysts_min: Optional[int] = None
    terminal_share_pct: Optional[float] = None
    cash_conversion: Optional[float] = None     # method E: (FCF − SBC) / (NI + SBC)
    fcf_margin_pct: Optional[float] = None      # method R: (FCF − SBC) / revenue
    sbc_status: Optional[Literal["deducted", "not_reported"]] = None
    shares_diluted: Optional[float] = None

    # Dates (ISO-8601 strings, per backend-python.md).
    last_reported_fiscal_year_end: Optional[str] = None
    as_of: Optional[str] = None

    notes: Optional[List[str]] = None
