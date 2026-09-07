"""The quote dict's contract, and the `.get(key, default)` trap that broke it.

WHY THIS FILE EXISTS. The FMP entitlement rebuild replaced `FMPClient.get_stock_price_quote`
with `price_service`, and the two have a subtly different contract:

    old (FMP /stable/quote) — a field it had no value for was simply ABSENT
    new (price_service._shape) — the key is PRESENT and holds None

`dict.get(key, default)` only reaches its default for an ABSENT key. So every
`quote.get("price", 0)` in the codebase — all of which looked, and had been, safe — was
silently disarmed. `agents/ticker_report_data_collector.py` then did
`f"${quote.get('price', 0):.2f}"` and raised
`TypeError: unsupported format string passed to NoneType.__format__` on any symbol whose
profile carries no usable price. That is a crash in the 20-credit paid report path, and the
whole 8,832-test suite was green through it.

⚠️ The present-with-None contract is CORRECT and is not what this file argues against.
`test_price_service.py::test_batch_without_stored_closes_reports_unknown_change_not_zero`
pins it deliberately — for `change`/`changePercentage`, None means "unknown" and a 0.0 there
would be a fabricated flat day. An earlier attempt at this fix omitted every None key and broke
exactly that invariant. The rule the two contracts have to satisfy together is:

    a numeric a caller will FORMAT must be either real or absent — never present-as-None

so `yearLow`/`yearHigh` (formatted with `:.2f`) are omitted when unknown, while
`change`/`changePercentage` (always read through an `or`/`is None` guard) stay present-as-None.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    build_financial_context,
)
from app.services.price_service import PriceService, parse_range_band

_REPO = Path(__file__).resolve().parents[1]


# ── the 52-week band, which /stable folds into a "low-high" string ───────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("164.08-260.10", (164.08, 260.10)),
        ("  164.08 - 260.10  ", (164.08, 260.10)),
        ("260.10-164.08", (164.08, 260.10)),   # reversed input is normalised, not trusted
        ("0-0", (0.0, 0.0)),                   # a real, if degenerate, band
        (None, (None, None)),
        ("", (None, None)),
        ("abc", (None, None)),
        ("164.08", (None, None)),              # no separator
        ("1-2-3", (None, None)),               # a negative low would split into 3 parts
        ("nan-260.10", (None, None)),
        ("inf-260.10", (None, None)),
        (123.45, (None, None)),                # not a string at all
        ({"low": 1}, (None, None)),
    ],
)
def test_range_band_parses_or_refuses(raw, expected):
    """Unparseable must be (None, None), never a half-band or a zero.

    A half-band is worse than none: the caller would render "52W Range: $164.08 - $0.00",
    which reads as a real number rather than as missing data.
    """
    assert parse_range_band(raw) == expected


def test_a_real_profile_yields_the_band_so_consumers_stop_reading_zero():
    q = PriceService._from_profile({
        "symbol": "AAPL", "companyName": "Apple Inc.", "price": 319.97,
        "change": -8.24, "changePercentage": -2.51, "range": "164.08-260.10",
    })
    assert q["yearLow"] == 164.08 and q["yearHigh"] == 260.10


# ── the invariant that kills the whole bug class ─────────────────────────────────────

# The band keys are the ones a caller FORMATS with `:.2f`, so they are the ones that must
# never be present-as-None. `price` / `previousClose` / `marketCap` stay present-as-None
# deliberately — every consumer reads them through a falsy guard
# (`if quote and quote.get("price")`, `pc or 0.0`), and making them absent would let a
# `.get(k, 0.0)` default fabricate a zero, which is the very thing
# `test_price_service.py::test_batch_without_stored_closes_reports_unknown_change_not_zero`
# exists to prevent. The general protection for those is
# `test_no_quote_lookup_formats_a_numeric_default` at the bottom of this file.
_MUST_BE_REAL_OR_ABSENT = ("yearLow", "yearHigh")


@pytest.mark.parametrize(
    "profile, why",
    [
        ({"symbol": "ZZZZ"}, "a profile with nothing but a symbol"),
        ({"symbol": "ZZZZ", "price": None}, "an explicit null price"),
        ({"symbol": "ZZZZ", "price": float("nan")}, "FMP's NaN for a thin symbol"),
        ({"symbol": "ZZZZ", "price": float("inf")}, "FMP's Inf"),
        ({"symbol": "ZZZZ", "price": "x"}, "a non-numeric price"),
        ({"symbol": "ZZZZ", "price": 10.0, "range": "garbage"}, "an unparseable band"),
        ({"symbol": "ZZZZ", "price": 10.0, "range": None}, "no band at all"),
        ({"symbol": "ZZZZ", "price": 10.0, "range": "nan-260.10"}, "a NaN inside the band"),
        ({"symbol": "ZZZZ", "price": 10.0, "range": "164.08"}, "a band with no separator"),
    ],
)
def test_the_band_keys_are_real_or_absent_never_present_as_none(profile, why):
    """The rule that kills the crash class, stated over exactly the keys it applies to.

    `dict.get(k, 0)` returns a PRESENT None instead of the default, so a present-as-None
    `yearHigh` disarms every caller's guard and reaches `f"{None:.2f}"`. Absent keeps the
    default reachable, and the caller renders "N/A".
    """
    q = PriceService._from_profile(profile)
    assert q is not None, "a row with a symbol must still shape"
    for key in _MUST_BE_REAL_OR_ABSENT:
        if key in q:
            v = q[key]
            assert isinstance(v, (int, float)) and math.isfinite(v), (
                f"{why}: {key!r} is present as {v!r}. `quote.get({key!r}, 0)` returns that "
                "value, NOT the default, so `:.2f` raises TypeError."
            )


def test_the_unknown_change_contract_is_not_collateral_damage():
    """Guards the OTHER direction, because the first fix attempt broke it.

    Omitting every None-valued key looked like the tidy fix and would have made
    `change`/`changePercentage` absent — at which point `quote.get("changePercentage", 0.0)`
    yields 0.0, a fabricated flat day on every tile. These three must stay PRESENT and None.
    """
    q = PriceService._from_profile({"symbol": "ZZZZ", "price": 10.0})
    for key in ("change", "changePercentage", "changesPercentage", "previousClose"):
        assert key in q, f"{key} must stay present — absent lets a .get default fabricate 0.0"
        assert q[key] is None


# ── the call site that actually crashed ──────────────────────────────────────────────

def _context_for(quote: dict, profile: dict | None = None) -> str:
    out = CollectedTickerData(ticker="ZZZZ", persona_key="value")
    out.profile = profile or {"companyName": "Thin Co"}
    out.quote = quote
    return build_financial_context(out)


@pytest.mark.parametrize(
    "quote, why",
    [
        (PriceService._from_profile({"symbol": "ZZZZ", "price": None}), "priceless profile"),
        (PriceService._from_profile({"symbol": "ZZZZ", "price": float("nan")}), "NaN price"),
        ({"symbol": "ZZZZ", "price": None}, "a hand-built present-as-None price"),
        ({"symbol": "ZZZZ"}, "no price key at all"),
        ({"symbol": "ZZZZ", "price": float("inf"), "yearLow": float("nan")}, "non-finite pair"),
    ],
)
def test_the_report_prompt_never_crashes_and_never_invents_a_price(quote, why):
    """`build_financial_context` feeds the Stage A/B prompt for a 20-credit report.

    Two assertions, and the second is the one that matters most: not crashing is table stakes,
    but rendering `$0.00` would be worse than crashing — the model cannot tell a real zero from
    a missing number, and it reasons from what it is given ("trading at the top of its
    52-week range").
    """
    text = _context_for(quote)                       # must not raise
    assert "$0.00" not in text, (
        f"{why}: a missing number rendered as $0.00 — the model will treat it as real"
    )
    assert "None" not in text, f"{why}: a literal None reached the prompt"
    assert "nan" not in text.lower(), f"{why}: a NaN reached the prompt"


def test_a_real_quote_still_renders_its_numbers():
    """Anti-vacuity: the test above passes trivially if the block stops emitting anything."""
    q = PriceService._from_profile({
        "symbol": "AAPL", "companyName": "Apple Inc.",
        "price": 319.97, "range": "164.08-260.10",
    })
    text = _context_for(q, profile={"companyName": "Apple Inc."})
    assert "$319.97" in text
    assert "$164.08" in text and "$260.10" in text


# ── stop the trap being rewritten ────────────────────────────────────────────────────

def test_no_quote_lookup_formats_a_numeric_default():
    """`quote.get(k, <number>)` immediately `:.2f`-formatted is the exact defect. Ban it.

    Source-scanned because it is a SHAPE of code, not a value: the next person to add a field
    to this prompt will reach for `quote.get('x', 0)` because it looks safe, and it is safe
    right up until the key exists holding None.
    """
    offenders = []
    for path in sorted((_REPO / "app").rglob("*.py")):
        src = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        for m in re.finditer(
            r"""\bquote\.get\(\s*['"](\w+)['"]\s*,\s*[-\d][\d.]*\s*\)\s*:\s*[.,]?\d*f""", src
        ):
            offenders.append(f"{path.relative_to(_REPO)}: quote.get({m.group(1)!r}, <number>) formatted")
    assert not offenders, (
        "these format a numeric default straight out of a quote dict:\n  "
        + "\n  ".join(offenders)
        + "\n\n`dict.get` returns a PRESENT None instead of the default, so this raises "
        "TypeError. Use `_fmt_money_or_na(quote.get(k))`."
    )


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-07 (testing.md §3 rule 3). Each mutation applied, the file run, reverted.
#
#  1. Collector price line back to `f"${quote.get('price', 0):.2f}"` — the original crash.
#       -> 5 FAILED  ✅
#  2. Collector 52W line back to `${quote.get('yearLow', 0):.2f}` — the original fabrication.
#       -> 6 FAILED  ✅
#  3. `_from_profile` stops parsing `profile.range` (band always unknown).
#       -> 2 FAILED  ✅ — and note only 2, because the prompt still renders "N/A" honestly.
#          That is the correct split: losing the DATA is a smaller defect than PRINTING $0.00.
#  4. `_shape` emits the band as present-with-None instead of omitting it.
#       -> 9 FAILED  ✅ — the widest blast radius, which is the right signal: this is the
#          exact shape of the original bug.
#  5. `changePercentage` omitted when None — i.e. the over-broad "just drop every None key"
#     fix that I wrote first and had to revert when it broke
#     `test_price_service.py::test_batch_without_stored_closes_reports_unknown_change_not_zero`.
#       -> test_the_unknown_change_contract_is_not_collateral_damage FAILED  ✅
#
# 5 is the one worth keeping: the two invariants pull in OPPOSITE directions (a formatted key
# must be absent when unknown; a guarded key must be present-as-None), and a fix that satisfies
# only one of them looks completely reasonable in review.
