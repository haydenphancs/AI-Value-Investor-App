"""`ChartTimeRange` is ONE enum shared by five detail screens — 2Y must reach only crypto.

This is the sequencing trap of the crypto migration. Adding `case twoYears = "2Y"` puts a
2Y pill on the equity, ETF, index and commodity pickers the instant it compiles, and every
one of those backends rejects "2Y" with a **400** (`^(1D|1W|3M|6M|1Y|5Y|ALL)$`, ten sites).
`ChartAssetContext.allowedRanges` is the only thing keeping the pill off those screens, so
the enum case and the gating property can never be changed apart.

The reverse direction matters just as much: crypto must NOT offer 5Y or ALL. CoinGecko
Basic caps history at two years, so those pills render a two-year series under a five-year
label — the same defect class as an empty chart, and harder to notice.

Source-scan guards go vacuous easily, so per `.claude/rules/testing.md` these are
comment-stripped, brace-bounded to the declaration under test, and hand mutation-tested.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_CHART_MODELS = _IOS / "Models" / "ChartModels.swift"
_DETAIL_MODELS = _IOS / "Models" / "TickerDetailModels.swift"
_CHART_VIEW = _IOS / "Views" / "Molecules" / "TickerChartView.swift"


def _strip_swift_comments(src: str) -> str:
    """Drop `//` lines and trailing comments.

    Mandatory: the prose above `allowedRanges` names every token this file greps for,
    so an unstripped scan passes on the explanation after the code is reverted.
    """
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def _src(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str) -> str:
    """The brace-bounded body of a declaration, so a match cannot come from elsewhere."""
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    start = src.find("{", i)
    assert start != -1, f"no opening brace after {header!r}"
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


# ── the case exists, and is spelled the way the backend expects ──────────────

def test_the_two_year_case_exists_with_the_wire_value_the_backend_accepts():
    body = _block(_src(_DETAIL_MODELS), "enum ChartTimeRange")
    assert 'case twoYears = "2Y"' in body, (
        'ChartTimeRange must carry `case twoYears = "2Y"` — the raw value IS the wire '
        "value sent as ?range=, and crypto.py's pattern accepts exactly \"2Y\""
    )


# ── crypto gets 2Y; nothing else does ────────────────────────────────────────

def test_allowed_ranges_exists_and_is_exhaustive_over_every_context():
    """No `default:` arm — a new asset class must be a compile error, not an assumption.

    Same reasoning as `APIEndpoint.authPolicy` in auth.md §1: a default arm silently
    decides for cases nobody thought about.
    """
    body = _block(_src(_CHART_MODELS), "var allowedRanges")
    assert "default:" not in body, (
        "allowedRanges must stay exhaustive so a new ChartAssetContext fails the build "
        "rather than silently inheriting crypto's or the equity list"
    )
    for ctx in ("crypto", "stock", "etf", "index", "commodity"):
        assert ctx in body, f"allowedRanges does not mention .{ctx}"


def test_only_crypto_is_offered_the_two_year_range():
    body = _block(_src(_CHART_MODELS), "var allowedRanges")
    crypto_arm, _, other_arm = body.partition("case .stock")
    assert ".twoYears" in crypto_arm, "crypto must offer 2Y"
    assert "twoYears" in other_arm and "filter" in other_arm, (
        "the non-crypto arm must EXCLUDE .twoYears explicitly; every other screen's "
        "backend answers 400 for range=2Y"
    )


def test_crypto_does_not_offer_five_year_or_all():
    """CoinGecko Basic caps at 2 years — a 5Y pill would draw 2 years under a 5Y label."""
    body = _block(_src(_CHART_MODELS), "var allowedRanges")
    crypto_arm = body.partition("case .stock")[0]
    for forbidden in (".fiveYears", ".all"):
        assert forbidden not in crypto_arm, (
            f"crypto must not offer {forbidden}: the source cannot serve that window, so "
            "the pill renders a shorter series than its label claims"
        )


# ── the picker must actually CONSUME the gate ────────────────────────────────

def test_the_range_picker_iterates_allowed_ranges_not_all_cases():
    """`allowedRanges` is inert unless the picker reads it.

    This is the half that actually ships the behaviour: the property can be perfect and
    every screen still shows a 2Y pill if the ForEach still walks `allCases`.
    """
    src = _src(_CHART_VIEW)
    assert "assetContext.allowedRanges" in src, (
        "TickerChartView's range picker must iterate assetContext.allowedRanges"
    )
    assert "ChartTimeRange.allCases" not in src, (
        "the picker still walks ChartTimeRange.allCases somewhere — that renders a 2Y "
        "pill on the equity/ETF/index/commodity screens, whose backends 400 it"
    )


# ── backend agreement, so the two halves cannot drift ────────────────────────

def test_only_the_crypto_endpoint_accepts_the_two_year_range():
    """The iOS gate and the backend patterns must describe the same world."""
    endpoints = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints"
    accepting = {
        p.name for p in endpoints.glob("*.py")
        if "2Y" in p.read_text(encoding="utf-8")
    }
    assert accepting == {"crypto.py"}, (
        f"exactly crypto.py may accept range=2Y, but these do: {sorted(accepting)}"
    )
