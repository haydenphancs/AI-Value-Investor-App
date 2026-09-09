"""Degraded paths must be LOUD and must not take more with them than they have to.

Three defects, all "the failure is invisible or larger than it needs to be":

  1. `fmp.get_company_profiles_batch` had a bare `except Exception: pass` — the one thing
     the rulebook forbids outright. It fans out over up to 50 tickers, so an FMP 429 (or
     an expired key) failed EVERY symbol, returned `[]`, and left callers unable to tell
     "these tickers have no profiles" from "we could not reach FMP at all".

  2. `commodity_service._build_commodity_detail` rehydrated its persisted Tier-2 rows with
     a bare `PerformancePeriodResponse(**row)` comprehension. Those rows carry whatever
     shape the build that WROTE them used, so one non-dict / missing-required-field / extra
     key raises and 500s the whole commodity screen for every cached symbol until the TTL
     expires — adding a single required field to that model would do it on the next deploy.
     Every sibling cache already wraps its rehydration (see the payload-version note in
     `valuation_snapshot_service`); this one did not.

  3. `AppState.performCreditsRefresh` wrote `user.credits` with no identity guard, so a
     refresh in flight during sign-out wrote the ENDED session's balance back after
     `signOut()` had reset `user`. Credits are money-adjacent.
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import re

import pytest

from app.integrations.fmp import get_fmp_client


# ── 1. the FMP batch must never swallow silently ────────────────────────────

def test_the_profile_batch_has_no_bare_pass():
    src = inspect.getsource(get_fmp_client().__class__.get_company_profiles_batch)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    assert "pass" not in stripped.split("except Exception")[-1][:120], (
        "a bare `except: pass` is banned — an upstream outage must leave a trace"
    )
    assert "logger" in stripped, "the degraded batch must log"


@pytest.mark.asyncio
async def test_a_totally_failed_batch_logs_a_warning(caplog):
    """The systemic case: every symbol failed, so this is an outage, not thin coverage."""
    client = get_fmp_client()
    original = client._make_request

    async def _boom(*a, **k):
        raise RuntimeError("FMP 429")

    client._make_request = _boom
    try:
        with caplog.at_level("INFO"):
            out = await client.get_company_profiles_batch(["AAPL", "MSFT", "NVDA", "TSLA"])
    finally:
        client._make_request = original

    assert out == []
    rec = [r for r in caplog.records if "get_company_profiles_batch" in r.getMessage()]
    assert rec, "a fully-failed batch produced no log line at all"
    assert rec[0].levelname == "WARNING", (
        f"a batch where every symbol failed must WARN, got {rec[0].levelname}"
    )
    assert "4/4" in rec[0].getMessage()


@pytest.mark.asyncio
async def test_one_bad_symbol_is_info_and_keeps_the_good_rows(caplog):
    """Anti-vacuity: an ordinary unprofiled ticker must not be escalated to a warning."""
    client = get_fmp_client()
    original = client._make_request
    seen = {"n": 0}

    async def _flaky(endpoint, params=None, **k):
        seen["n"] += 1
        if seen["n"] % 4 == 0:
            raise RuntimeError("no profile for this one")
        return [{"symbol": params["symbol"], "companyName": "X"}]

    client._make_request = _flaky
    try:
        with caplog.at_level("INFO"):
            out = await client.get_company_profiles_batch(["A", "B", "C", "D"])
    finally:
        client._make_request = original

    assert len(out) == 3, "the good rows must survive one bad symbol"
    rec = [r for r in caplog.records if "get_company_profiles_batch" in r.getMessage()]
    assert rec and rec[0].levelname == "INFO", (
        "one unprofiled symbol in four is routine — INFO, not WARNING"
    )


# ── 2. a persisted row that no longer matches its model costs ONE row ───────

def test_commodity_performance_rehydration_is_per_row():
    from app.services import commodity_service

    src = inspect.getsource(commodity_service.CommodityService._build_commodity_detail)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    i = stripped.find("PerformancePeriodResponse(**row)")
    assert i != -1, "guard is stale — the rehydration moved"
    # A comprehension form has no `try` to protect it.
    assert "for row in (derived.get(\"performance_periods\") or [])\n        ]" not in stripped, (
        "the comprehension form raises out of the whole detail build on one bad row"
    )
    preceding = stripped[:i]
    assert "try:" in preceding[-200:], (
        "the rehydration must sit inside a try so one stale row cannot 500 the screen"
    )
    assert "logger.warning" in stripped[i:i + 900], (
        "a dropped row must be logged — an empty Performance section after a deploy is "
        "otherwise silent and undiagnosable"
    )


def test_the_sibling_caches_still_guard_their_rehydration():
    """Pins the convention this fix restores, so the commodity one is not an island."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    for name, symbol in [("growth_service.py", "GrowthResponse(**"),
                         ("profit_power_service.py", "ProfitPowerResponse(**")]:
        src = (root / name).read_text(encoding="utf-8")
        i = src.find(symbol)
        assert i != -1, f"guard is stale — {symbol} not found in {name}"
        # Bound to the ENCLOSING function rather than a fixed character window: the
        # `try:` legitimately sits a few hundred lines up, past any arbitrary cutoff.
        start = src.rfind("\n    def ", 0, i)
        start = max(start, src.rfind("\n    async def ", 0, i))
        assert start != -1, f"could not bound the function containing {symbol}"
        nxt = src.find("\n    def ", i)
        nxt2 = src.find("\n    async def ", i)
        end = min(x for x in (nxt, nxt2, len(src)) if x != -1)
        body = src[start:end]
        assert "try:" in body and "except" in body, (
            f"{name} rehydrates a persisted bundle outside any try/except — same hazard "
            "as the commodity one: a shape change 500s instead of forcing a rebuild"
        )


# ── 3. the credits refresh must not write an ended session's balance ────────

def test_credits_refresh_is_identity_guarded():
    ios = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
    src = (ios / "Core" / "State" / "AppState.swift").read_text(encoding="utf-8")
    stripped = "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in src.splitlines()
    )
    i = stripped.find("private func performCreditsRefresh")
    assert i != -1, "guard is stale — performCreditsRefresh moved"
    body = stripped[i:i + 900]
    assert "let identity = identityGeneration" in body, (
        "the identity must be captured BEFORE the request; a live read always matches"
    )
    write = body.find("user.credits =")
    guard = body.find("guard identity == identityGeneration")
    assert guard != -1 and guard < write, (
        "the guard must sit between the await and the write to user.credits — otherwise a "
        "refresh in flight during sign-out writes the ex-user's balance back"
    )


# ── 4. numeric input parsing must follow the device's locale ────────────────

def test_price_alert_threshold_parsing_is_locale_aware():
    """A hardcoded "," -> thousands rule is wrong for most of the world.

    The threshold field is a `.keyboardType(.decimalPad)`, which renders the LOCALE's own
    decimal separator. On a German/French/Spanish device the user types "1,5" meaning
    one-and-a-half; the old code stripped the comma to "15" and `Double("15")` parsed
    cleanly, so the alert was created at TEN TIMES the intended threshold and simply never
    fired where they expected. Silent, and the user has no way to see it.
    """
    ios = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
    src = (ios / "ViewModels" / "PriceAlertsViewModel.swift").read_text(encoding="utf-8")
    stripped = "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in src.splitlines()
    )
    i = stripped.find("var parsedThreshold")
    assert i != -1, "guard is stale — parsedThreshold moved"
    body = stripped[i:i + 1400]

    assert "Locale.current.decimalSeparator" in body, (
        "the decimal separator must come from the locale, not be assumed to be '.'"
    )
    assert "Locale.current.groupingSeparator" in body, (
        "the grouping separator must come from the locale, not be assumed to be ','"
    )
    assert '.replacingOccurrences(of: ",", with: "")' not in body, (
        "a hardcoded comma strip destroys the decimal separator on a comma-decimal locale"
    )


def test_the_threshold_field_is_still_a_decimal_pad():
    """The premise of the test above: the keyboard shows the locale's separator."""
    ios = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
    sheet = (ios / "Views" / "Screens" / "PriceAlertsSheet.swift").read_text(encoding="utf-8")
    assert ".decimalPad" in sheet, (
        "if the threshold field stopped being a decimalPad, re-check whether a comma can "
        "still reach parsedThreshold"
    )
