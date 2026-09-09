"""An unmeasured macro backdrop must not be reported as a benign one.

`FRED_API_KEY` was set in `backend/.env` but never on Railway. Every unconfigured FRED path
returned empty with NO log line — despite `fred.py`'s own module docstring promising "logged
once per series" — so the authoritative macro tier (CPI, Core PCE, breakevens, Fed Funds,
DGS10, T10Y2Y, UNRATE, ICSA, HY OAS) was simply absent in production.

With that tier gone, an ordinary calm tape produces an empty `risk_factors`, and the report
printed:

    "Benign macro backdrop — no indicators tripping risk thresholds."

A confident all-clear derived from data the app never looked at. In a product that publishes
its own ratings on named securities, that is the most dangerous shape a fallback can take —
and it also inflated persona scores, since `_derive_macro_vital` maps an empty factor set to
8.0/10 "No Major Risks" at a 3-8% weight on every rating.

Same root cause zeroed 138 of 158 `industry_dossier` rows: with neither credential set, every
industry resolved to a zero-TAM placeholder and the quarterly job upserted it over the good
rows. Verified against the live table on 2026-08-07.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.services.agents.ticker_report_data_collector import (
    _fallback_macro_brief,
    _fallback_macro_headline,
)

_SRC = Path(__file__).resolve().parents[1] / "app"


# ── Unknown is not benign ────────────────────────────────────────────────────

def test_an_unmeasured_empty_factor_set_does_not_claim_benign():
    out = _fallback_macro_headline("low", [], measured=False)
    assert "benign" not in out.lower(), (
        "an all-clear derived from data that was never read"
    )
    assert "unavailable" in out.lower()


def test_a_measured_empty_factor_set_still_reads_benign():
    """The honest case must keep working — a genuinely calm tape IS benign, and turning every
    quiet report into 'unavailable' would be its own kind of wrong."""
    out = _fallback_macro_headline("low", [], measured=True)
    assert "benign" in out.lower()


def test_the_brief_agrees_with_the_headline():
    brief = _fallback_macro_brief("low", [], measured=False)
    assert "unknown" in brief.lower() or "could not be read" in brief.lower()
    assert "no macro indicators are currently tripping" not in brief.lower()


@pytest.mark.parametrize("measured", [True, False])
def test_factors_present_always_summarize_them(measured):
    """`measured` only governs the EMPTY case. If factors exist they were measured by
    definition, and the headline must describe them rather than claim unavailability."""
    out = _fallback_macro_headline(
        "high", [{"title": "CPI accelerating"}], measured=measured
    )
    assert "CPI accelerating" in out
    assert "unavailable" not in out.lower()


def test_the_default_stays_benign_for_existing_callers():
    """`measured` defaults True so no other call site changes behaviour silently."""
    sig = inspect.signature(_fallback_macro_headline)
    assert sig.parameters["measured"].default is True


# ── The degradation is now loud ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "module,symbol",
    [("integrations/fred.py", "_warn_unconfigured_once"),
     ("integrations/census.py", "_warn_unconfigured_once")],
)
def test_an_unconfigured_upstream_warns(module, symbol):
    src = (_SRC / module).read_text()
    assert symbol in src, f"{module} degrades silently when unconfigured"
    # And it must be CALLED, not merely defined.
    assert src.count(symbol) >= 2, f"{module} defines {symbol} but never calls it"


def test_fred_docstring_no_longer_promises_something_it_does_not_do():
    src = (_SRC / "integrations/fred.py").read_text()
    head = src[: src.index('"""', src.index('"""') + 3)]
    assert "logged once per series" not in head, (
        "the docstring promised per-series logging that did not exist — the whole reason "
        "this went unnoticed in production"
    )


# ── The quarterly job must not destroy good rows ─────────────────────────────

def test_recompute_refuses_to_run_with_no_upstream_credentials():
    from app.services.industry_dossier_service import IndustryDossierService

    src = inspect.getsource(IndustryDossierService.recompute_all)
    assert "no upstream credentials" in src
    # The check must precede the upsert, or it guards nothing.
    assert src.index("no upstream credentials") < src.index("upsert("), (
        "the credential check must come before the upsert"
    )


def test_recompute_never_overwrites_a_real_tam_with_a_placeholder():
    """The narrower case the credential check does not cover: one upstream configured, but a
    single industry resolving to a placeholder anyway (an AIES gap, a retired FRED series, a
    transient failure). Upserting that over a real number is permanent data loss."""
    from app.services.industry_dossier_service import IndustryDossierService

    src = inspect.getsource(IndustryDossierService.recompute_all)
    assert "has_real_tam" in src
    assert src.index("has_real_tam") < src.index("for batch in _chunked"), (
        "the per-row guard must be applied before the batches are written"
    )
    # And a failed pre-read must fail SAFE (skip), not fall through to a no-op filter.
    assert "rows = []" in src, (
        "if the pre-read fails we cannot tell which rows are good — skip rather than risk "
        "clobbering them"
    )


# ── The flag itself, not just the function that consumes it ──────────────────
#
# Everything above tests `_fallback_macro_headline` GIVEN a `measured` flag. Nothing
# tested how that flag is COMPUTED — and that gap is the whole reason the macro outage
# was invisible for the length of the enforcement window.
#
# `macro_measured` read `bool(out.fred_indicators)` alone. There are TWO deterministic
# tiers; the market-indicator one (oil / gold / volatility / rates / dollar) returned []
# on every report from 2026-09-03, and the FRED half vouched for it. The report then
# printed "Benign macro backdrop — no indicators tripping risk thresholds" over a tier it
# had never read: exactly the sentence this file exists to forbid, through the one door
# it did not cover.

def _macro_measured_expr() -> str:
    """The assignment line, comments stripped so the explanation cannot satisfy a scan."""
    import inspect

    from app.services.agents.ticker_report_data_collector import TickerReportDataCollector

    src = inspect.getsource(TickerReportDataCollector.assemble_report)
    for line in src.splitlines():
        code = line.split("#", 1)[0].strip()
        if code.startswith("macro_measured"):
            return code
    raise AssertionError("`macro_measured =` not found in assemble_report — scan drifted")


def test_measured_requires_BOTH_deterministic_tiers():
    expr = _macro_measured_expr()
    assert "fred_indicators" in expr, "the FRED tier is no longer consulted"
    assert "macro_indicators" in expr, (
        "the market-indicator tier does not gate `macro_measured` — a totally dead tier "
        "would again be reported as a benign backdrop"
    )
    assert " and " in expr, (
        f"the two tiers must both be required; `or` lets either vouch for the other: {expr!r}"
    )


def test_one_dead_tier_is_enough_to_stop_claiming_a_benign_backdrop():
    """The behavioural half — the source scan above cannot prove the semantics."""
    from app.services.agents.ticker_report_data_collector import _fallback_macro_headline

    for fred_rows, fmp_rows in (([], [{"symbol": "WTI"}]), ([{"series_id": "DGS10"}], []), ([], [])):
        measured = bool(fred_rows) and bool(fmp_rows)
        assert measured is False
        out = _fallback_macro_headline("low", [], measured=measured)
        assert "benign" not in out.lower(), (
            "an all-clear derived from a tier that was never read"
        )
    # ...and with both tiers alive, an empty factor set IS a real all-clear.
    both = _fallback_macro_headline("low", [], measured=True)
    assert "unavailable" not in both.lower()
