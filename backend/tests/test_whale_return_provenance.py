"""The whale annual-return contract: one implementation, one caption, one shape.

Three things are pinned here, each because of a defect that actually shipped.

1. ANTI-DIVERGENCE. `whale_service._compute_avg_annual_return` and
   `hydrate_whales._compute_ytd_return` were independent copies of one formula
   and drifted: different outlier floors (-100 vs -200) and different captions
   ("13F Portfolio CAGR" vs "13F Portfolio Avg.") for the same number — with the
   hydration copy being the one that runs in production. A source scan is the
   right instrument because the defect IS copy-paste drift; a behavioural test
   passes happily while a second copy rots beside it.

2. SCHEMA PARITY. `WhaleProfileResponse` had NO parity test, which is how a
   screen ended up captioning a 1-year number as a CAGR with nothing to catch it.

3. THE TWO FIELDS THAT MUST NEVER GO NULLABLE. `WhaleProfileDTO` declares
   `ytd_return` / `portfolio_value` as non-Optional `Double` with their keys in
   `CodingKeys`, so a JSON null throws `typeMismatch` and fails the WHOLE profile
   decode on every installed build. Making them `Optional[float]` here would look
   like a tidy improvement and would break every shipped client.

No network, no Supabase.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.schemas.whale import WhaleProfileResponse
from app.services import _whale_common as wc

_BACKEND = Path(__file__).resolve().parents[1]
_SERVICE = _BACKEND / "app" / "services" / "whale_service.py"
_HYDRATE = _BACKEND / "scripts" / "hydrate_whales.py"


def _code_only(path: Path) -> str:
    """Source with comments and docstring bodies stripped.

    Without this the scan is worthless in both directions: it trips on a comment
    that merely NAMES the old value, and it can be satisfied by prose. The
    stripping is crude (line comments + triple-quoted blocks) but the assertions
    below are about executable literals, which survive it.
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', '""', src)
    src = re.sub(r"'''(?:.|\n)*?'''", "''", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#(?!\!).*$", "", src)
    return src


# ── 1. Anti-divergence ───────────────────────────────────────────────


def test_neither_call_site_owns_a_second_copy_of_the_formula():
    """`math.prod` over year-end returns must exist in exactly ONE place."""
    for path in (_SERVICE, _HYDRATE):
        code = _code_only(path)
        assert "math.prod" not in code, (
            f"{path.name} has its own compounding loop again — the whole point of "
            f"_whale_common.compute_13f_cagr is that this formula exists once"
        )


def test_the_impossible_outlier_floor_is_gone_from_both_call_sites():
    """-200 admits yearly returns worse than a total loss; an EVEN count of them
    multiplies to a positive product and surfaces as a plausible CAGR."""
    for path in (_SERVICE, _HYDRATE):
        code = _code_only(path)
        assert "-200" not in code, f"{path.name} reintroduced the -200 floor"


def test_captions_are_produced_in_exactly_one_place():
    """Two files hardcoding caption strings is how one number acquired two names."""
    for path in (_SERVICE, _HYDRATE):
        code = _code_only(path)
        assert "13F Portfolio" not in code, (
            f"{path.name} hardcodes a return caption — use "
            f"_whale_common.return_label_for, the single producer"
        )
    assert wc.return_label_for(wc.SOURCE_13F) == "13F Portfolio CAGR"
    assert wc.return_label_for(wc.SOURCE_STOCK, "BRK-A") == "BRK-A CAGR"
    assert wc.return_label_for("", None) == ""


def test_the_one_year_fallback_is_not_reintroduced():
    """It is tempting to re-add as a "nice degradation". It is not one: it
    captions a single volatile year as a compound annual growth rate."""
    lone_year = [{"date": "2024-06-30", "performancePercentage1year": 42.0}]
    assert wc.compute_13f_cagr(lone_year).value is None


# ── 2. Schema parity with the iOS decoder ────────────────────────────

# Exact snake_case keys `WhaleProfileDTO.CodingKeys` declares
# (frontend/ios/ios/Models/WhaleDTOs.swift).
IOS_PROFILE_KEYS = {
    "id", "name", "title", "description", "firm_name", "avatar_url",
    "risk_profile", "portfolio_value", "ytd_return", "sector_exposure",
    "current_holdings", "recent_trade_groups", "recent_trades",
    "behavior_summary", "sentiment_summary", "is_following", "data_source",
    "return_source", "return_label",
    # Stat-tile provenance (migration 127).
    "return_status", "return_window_years", "portfolio_status",
    "portfolio_as_of", "filing_date",
}


def _minimal_profile_kwargs() -> dict:
    return {
        "id": "w1", "name": "X", "title": "t", "description": "d",
        "risk_profile": "r", "portfolio_value": 1.0, "ytd_return": 2.0,
        "behavior_summary": {
            "action": "a", "primary_focus": "b",
            "secondary_action": "c", "secondary_focus": "d",
        },
    }


def test_profile_response_keys_match_the_ios_dto():
    dumped = WhaleProfileResponse(**_minimal_profile_kwargs()).model_dump()
    assert set(dumped) == IOS_PROFILE_KEYS


def test_legacy_cached_json_still_replays_through_the_model():
    """`whale_profile_cache` feeds stored JSON back through this model
    (`WhaleProfileResponse(**row["profile_json"])`). A REQUIRED new field would
    500 every cached profile for up to 24h after deploy."""
    resp = WhaleProfileResponse(**_minimal_profile_kwargs())
    assert resp.return_status == ""
    assert resp.return_window_years is None
    assert resp.portfolio_as_of is None
    assert resp.filing_date is None


# ── 3. The two fields that must never become nullable ────────────────


def test_ytd_return_and_portfolio_value_stay_non_optional_floats():
    """iOS decodes both as non-Optional `Double`. `Optional[float]` here sends a
    JSON null, which throws `typeMismatch` and kills the WHOLE profile decode on
    every already-installed build. "Don't believe this number" travels via
    `return_status` / `portfolio_status` instead — that is what they are for."""
    fields = WhaleProfileResponse.model_fields
    assert fields["ytd_return"].annotation is float
    assert fields["portfolio_value"].annotation is float


def test_an_unbelievable_return_still_serializes_as_a_number():
    resp = WhaleProfileResponse(
        **{**_minimal_profile_kwargs(), "ytd_return": 0.0},
        return_status=wc.RETURN_INSUFFICIENT,
    )
    dumped = resp.model_dump()
    assert isinstance(dumped["ytd_return"], float)
    assert dumped["return_status"] == wc.RETURN_INSUFFICIENT


def test_unavailable_labels_never_claim_to_be_a_cagr():
    """Shipped clients render `return_label` verbatim under a green "+0.0%" they
    cannot be taught to suppress. The caption is the only thing we can fix for
    them, so it must not say "CAGR"."""
    for status in (wc.RETURN_INSUFFICIENT, wc.RETURN_UNAVAILABLE):
        assert "CAGR" not in wc.unavailable_return_label(status)
