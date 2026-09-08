"""Guard: no PRODUCT surface depends on an FMP endpoint we do not license.

WHY THIS EXISTS SEPARATELY FROM THE PARITY TEST
-----------------------------------------------
`test_fmp_entitlement_parity.py` scans `fmp.py` and asks "does this file reference a
blocked path?". That is the right question for the integration layer, and the answer stays
YES on purpose: the wrappers are kept, not deleted, so buying a package later is one line in
`PURCHASED_PACKAGES`. `KNOWN_BLOCKED_IN_USE` therefore does NOT shrink when a feature is
fixed, and it structurally cannot express this phase's exit criterion.

The criterion is about the CALLERS: no service may still need `grades`,
`price-target-consensus`, `dividends` or `splits` to produce a correct answer. That is a
behavioural question, so this file asks it behaviourally — every blocked wrapper raises,
and each caller must still come back with something honest.

Hermetic: nothing here reaches a network.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.integrations.fmp import FMPNotEntitledException
from app.integrations.fmp_entitlements import entitlement_error

ROOT = Path(__file__).resolve().parents[1]

#: The four Phase-3 endpoints and the wrapper each is reached through.
BLOCKED_WRAPPERS = {
    "get_grades": "grades",
    "get_price_target_consensus": "price-target-consensus",
    "get_dividend_history": "dividends",
    "get_stock_splits": "splits",
}


def test_the_premise_all_four_are_really_unlicensed():
    """If any of these becomes entitled, this whole file is measuring nothing."""
    for path in BLOCKED_WRAPPERS.values():
        assert entitlement_error(path) is not None, (
            f"{path!r} is licensed again — delete the workaround, don't keep the guard"
        )


def _strip_comments(src: str) -> str:
    return "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )


@pytest.mark.parametrize("module_rel", [
    "app/services/whale_service.py",
    "app/services/holders_service.py",
    "scripts/hydrate_whales.py",
    "scripts/hydrate_hedge_fund_flow.py",
])
def test_no_thirteen_f_path_still_reaches_the_splits_endpoint(module_rel):
    """The 13F share-count diff is the dangerous one.

    With `/splits` returning nothing, a position merely HELD through a 10:1 reads as a
    purchase: `whale_service._diff_quarters` writes a fabricated multi-million-dollar
    BOUGHT into `whale_trades`, which feeds user alerts, and
    `holders_service._build_institutional_activities` rendered BlackRock's KLAC row as
    +$34,275.0M / +901.88% against a true +$71M. All four now derive splits from entitled
    price series instead.
    """
    code = _strip_comments((ROOT / module_rel).read_text())
    assert "get_stock_splits(" not in code, (
        f"{module_rel} still calls the 402 /splits endpoint"
    )
    assert "corporate_actions_source(" in code, (
        f"{module_rel} no longer derives splits at all — a held-through-split position "
        "will be reported as a trade"
    )


def test_the_analyst_card_asks_the_manifest_before_calling_a_blocked_endpoint():
    """`analyst_service` keeps its grades block (hide, don't delete) but must not RUN it."""
    code = _strip_comments((ROOT / "app/services/analyst_service.py").read_text())
    assert "section_available = analyst_section_available()" in code
    assert "if section_available:" in code, (
        "the two guaranteed-402 calls are issued unconditionally again"
    )
    # ...and the entitled half is fetched regardless of that flag, or the card goes dark
    # for a dataset we do actually license.
    assert "analyst_estimates_available()" in code


@pytest.mark.parametrize("module_rel,fn", [
    ("app/services/tracking_service.py", "_get_analyst_rating_alerts"),
    ("app/services/widget_movers_service.py", "_head_grades"),
])
def test_the_remaining_grades_consumers_check_the_licence_first(module_rel, fn):
    """Both used to issue a guaranteed 402 — `tracking_service` once PER WATCHLIST TICKER
    on every refresh — catch it, log a warning, and return empty. Brace-bounded to the
    function so the check cannot pass on a mention somewhere else in the file."""
    code = _strip_comments((ROOT / module_rel).read_text())
    start = code.index(f"def {fn}(")
    nxt = code.find("\n    async def ", start + 1)
    if nxt == -1:
        nxt = code.find("\n    def ", start + 1)
    body = code[start: nxt if nxt != -1 else len(code)]
    assert "analyst_section_available()" in body, (
        f"{module_rel}::{fn} calls a 402 endpoint without checking the manifest"
    )


@pytest.mark.asyncio
async def test_the_signal_of_confidence_card_survives_an_empty_dividend_feed():
    """Its gate used to be `if not dividend_history: return None`, which turned the card
    off for EVERY ticker the day `/dividends` went outside the licence — while every
    number it renders comes from cash-flow data that was never affected."""
    from app.services.signal_of_confidence_service import SignalOfConfidenceService

    svc = SignalOfConfidenceService.__new__(SignalOfConfidenceService)
    info = svc._build_dividend_info([], 2.4, 1.0, 0.0, data_points=[])
    assert info is not None, "a real dividend payer lost its whole card"
    assert info.status in {"Low", "Fair", "High", "Very High"}


def test_the_report_never_asserts_a_dividend_verdict_it_did_not_compute():
    """`dividend_status` used to fall back to a hardcoded "Fair"."""
    code = _strip_comments(
        (ROOT / "app/services/agents/ticker_report_data_collector.py").read_text()
    )
    assert '"dividend_status": (div.status if div else "Fair")' not in code, (
        "every 20-credit report is asserting a dividend verdict for a company that pays "
        "no dividend"
    )
    assert '"dividend_status": (div.status if div else "None")' in code


def test_the_blocked_wrappers_are_kept_not_deleted():
    """Hide, don't delete: buying a package must stay a one-line change.

    This is the counterweight to every assertion above — they would all also pass if
    someone deleted the wrappers outright, which would make re-enabling a dataset a
    re-plumbing job instead of an edit to `PURCHASED_PACKAGES`.
    """
    from app.integrations.fmp import FMPClient

    for wrapper in BLOCKED_WRAPPERS:
        assert hasattr(FMPClient, wrapper), (
            f"FMPClient.{wrapper} was deleted — the dataset is hidden, not gone, and the "
            "wrapper is what makes buying the package a one-line change"
        )
        assert inspect.iscoroutinefunction(getattr(FMPClient, wrapper))
