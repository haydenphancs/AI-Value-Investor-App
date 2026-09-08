"""Guard: the ETF net-yield card never asserts a payout it did not measure.

WHY
---
`/dividends` went outside the signed FMP Order Form on 2026-09-03, so the per-payment feed
is permanently empty. Two defects followed, and both shipped:

1. **`dividend_yield_known` guarded only the NUMBER.** It was added so a fund whose price we
   could not fetch renders `—` instead of a fabricated `0.00%`. But the two prose strings on
   the same card were still selected by `elif dividend_yield <= 0`, so the card read
   `Yield: —` directly above *"This fund doesn't currently pay a dividend."* — a confident
   assertion of the very thing the flag exists to avoid claiming.

2. **The ex-dividend derivation was wired into the DETAIL path only.** `GET /etfs/{s}/dividends`
   still read the empty feed, so SPY's detail card said "Pays Quarterly" while the history
   screen said "—" and showed nothing.

⚠️ A zero yield is NOT always wrong: ARKK, GLD, SLV and USO genuinely distribute nothing and
report `lastDividend == 0`, so "0.00%" and "doesn't currently pay a dividend" are CORRECT for
them. The distinction this file protects is measured-zero vs unknown.

Hermetic — source-scan only where the logic lives inside a 700-line builder.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.etf_service import dividend_yield_from_profile

SRC = Path(__file__).resolve().parents[1] / "app/services/etf_service.py"


def _code() -> str:
    """etf_service source with comments stripped — the prose this file asserts on also
    appears verbatim in the comments explaining it (testing.md §3)."""
    return "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in SRC.read_text().splitlines()
    )


def test_unknown_yield_is_checked_before_the_zero_payout_claim():
    """Branch ORDER is the whole fix: `not dividend_yield_known` must win over `<= 0`."""
    code = _code()
    unknown = code.find("elif not dividend_yield_known:")
    zero = code.find("elif dividend_yield <= 0:")
    assert unknown != -1, (
        "the unknown-yield branch is gone — a fund whose price failed to fetch will again "
        "be described as paying no dividend"
    )
    assert zero != -1, "the genuine non-payer branch was removed (ARKK/GLD/SLV/USO need it)"
    assert unknown < zero, (
        "`dividend_yield <= 0` is tested first, so an UNKNOWN yield falls into the "
        "'doesn't currently pay a dividend' arm — unknown is not zero"
    )


def test_the_unknown_branch_makes_no_claim_about_the_payout():
    code = _code()
    i = code.index("elif not dividend_yield_known:")
    branch = code[i: code.index("elif dividend_yield <= 0:", i)]
    assert "couldn't confirm" in branch, "the unknown arm should say so plainly"
    for claim in ("doesn't currently pay", "You earn ~$"):
        assert claim not in branch, (
            f"the unknown-yield arm asserts {claim!r}, which is a measurement it does not have"
        )


def test_the_yield_context_is_also_gated():
    """`yield_context` is a separate string built at the response, and it was the second
    half of the same bug: "You earn ~$0 per year" is a claim, not an absence."""
    code = _code()
    i = code.index("yield_context=")
    # ⚠️ Bounded to the yield_context EXPRESSION, not a fixed character window. A 400-char
    # window ran past the closing paren into `dividend_yield_known=dividend_yield_known`
    # on the very next response field, so the assertion passed with the gate removed —
    # caught by mutation-testing.
    block = code[i: code.index("\n            verdict=", i)]
    assert "dividend_yield_known" in block, (
        "yield_context still renders 'You earn ~$0 per year' for an unknown yield"
    )
    assert "You earn" in block, "test setup: the wrong block was captured"


def test_both_screens_derive_pay_frequency_from_the_same_source():
    """The detail card and `GET /etfs/{symbol}/dividends` must not contradict each other."""
    code = _code()
    assert code.count("get_ex_dividend_dates(") >= 2, (
        "only one of the two ETF pay-frequency paths derives its dates, so the detail card "
        "and the dividend-history screen can disagree — SPY said 'Pays Quarterly' on one "
        "and '—' on the other"
    )
    # ...and the endpoint's empty-feed early return must be the one that derives.
    i = code.index("total_dividends=0")
    early_return = code[max(0, i - 1600): i]
    assert "get_ex_dividend_dates(" in early_return, (
        "the dividends endpoint returns '—' without attempting the entitled derivation"
    )


def test_the_measured_zero_case_is_preserved():
    """Anti-over-correction: the non-payer arm must still exist and still be reachable.

    Was parametrized over GLD/SPY while the body ignored BOTH parameters — two identical
    source scans wearing ticker labels. Behaviour is asserted in
    `test_the_three_dividend_states_are_distinguishable` below; this one keeps its
    narrower job (the branch still exists) and no longer pretends to be per-ticker.
    """
    code = _code()
    assert "elif dividend_yield <= 0:" in code


# ── Behaviour, not source text ──────────────────────────────────────────────────────
#
# Every guard above is a source scan, and a source scan cannot see a TAUTOLOGY. The
# first `dividend_yield_known` read `_finite_num(x) is not None` — and `_finite_num`
# returns its default (0.0) on failure, never None — so the flag collapsed to
# `price > 0` and the whole honesty mechanism was inert for the case it was written for
# (a profile leg that 402s or times out). Every scan above stayed green over it.


@pytest.mark.parametrize("profile,price,expected_yield,expected_known,why", [
    ({"lastDividend": 1.06}, 250.0, 0.42, True,
     "a real payer yields lastDividend/price"),
    ({"lastDividend": 0}, 250.0, 0.0, True,
     "GLD/ARKK/SLV/USO report exactly 0 — a MEASURED zero, correctly rendered 0.00%"),
    ({"lastDividend": 0.0}, 250.0, 0.0, True,
     "float zero is the same measured zero"),
    ({}, 250.0, 0.0, False,
     "the profile leg failed — unknown, NOT a zero payout"),
    ({"lastDividend": None}, 250.0, 0.0, False,
     "an explicit null is absent, not zero"),
    ({"lastDividend": float("nan")}, 250.0, 0.0, False,
     "NaN is not a measurement"),
    ({"lastDiv": 2.0}, 100.0, 2.0, True,
     "the legacy key still works"),
    ({"lastDividend": 0, "lastDiv": 9.9}, 100.0, 0.0, True,
     "a GENUINE zero must not fall through to the legacy key — `0 or x` did exactly that"),
    ({"lastDividend": 1.06}, None, 0.0, False, "no price"),
    ({"lastDividend": 1.06}, 0.0, 0.0, False, "price 0"),
    ({"lastDividend": 1.06}, float("nan"), 0.0, False, "price NaN"),
])
def test_the_three_dividend_states_are_distinguishable(
    profile, price, expected_yield, expected_known, why
):
    yield_pct, known = dividend_yield_from_profile(profile, price)

    assert known is expected_known, why
    assert yield_pct == pytest.approx(expected_yield), why


def test_an_unfetchable_profile_is_never_reported_as_a_non_payer():
    """The end the whole flag exists for, stated once as a single assertion.

    `known=False` is what routes the card to "We couldn't confirm this fund's dividend
    yield" instead of "This fund doesn't currently pay a dividend", and what makes iOS
    render `—` instead of `0.00%`.
    """
    _, known = dividend_yield_from_profile({}, 250.0)
    assert known is False

    # ...and the control, or the flag could simply be False always.
    _, known_ok = dividend_yield_from_profile({"lastDividend": 0}, 250.0)
    assert known_ok is True
