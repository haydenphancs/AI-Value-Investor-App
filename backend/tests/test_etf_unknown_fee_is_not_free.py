"""An ETF whose expense ratio we never learned is not a free fund.

`expense_ratio` is a shipped non-Optional float and `0.0` means UNAVAILABLE — the
net-yield builder says so in as many words ("expense_ratio == 0 here means the value is
UNAVAILABLE … NOT a genuinely free fund") and already renders
"Expense ratio unavailable for this fund." Two things did not get the memo (found
2026-09-12):

* iOS printed `formattedExpenseRatio` as "0%" directly above that sentence.
* `_build_identity_rating` scored the unknown fee against `<= 0.05` and awarded the
  CHEAPEST-possible full point, inflating the star rating exactly when we know least.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.services.etf_service as etf
from app.schemas.etf import ETFNetYieldResponse

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"


def _rating(expense_ratio: float, **over):
    svc = etf.ETFService.__new__(etf.ETFService)
    kw = dict(total_assets=5e10, beta=1.0, expense_ratio=expense_ratio,
              inception_date="2005-01-01", holdings_count=500)
    kw.update(over)
    return svc._build_identity_rating(**kw)


# The rating is a 0-5 composite rounded to a 1-5 band, so a single fixture cannot separate
# both sides at once — 0.5 sits exactly between the bands. Two fixtures, each chosen so the
# other three components put the comparison across a rounding boundary.
_SEPARATES_CHEAP = dict(total_assets=2e10, inception_date="2021-06-01", holdings_count=100)
_SEPARATES_DEAR = dict(total_assets=5e9, inception_date="2021-06-01", holdings_count=100)


def test_an_unknown_fee_never_scores_as_well_as_a_known_cheap_one():
    assert _rating(0.0, **_SEPARATES_CHEAP).score < _rating(0.03, **_SEPARATES_CHEAP).score, (
        "a fund whose fee we never learned scored like the cheapest fund in the catalogue "
        "— the rating was most confident exactly where it knows least"
    )


def test_an_unknown_fee_is_not_punished_as_an_expensive_one_either():
    assert _rating(0.0, **_SEPARATES_DEAR).score > _rating(0.90, **_SEPARATES_DEAR).score


def test_an_unknown_fee_is_scored_as_average_across_every_shape():
    """The invariant behind both tests above, asserted without rounding luck."""
    for shape in (_SEPARATES_CHEAP, _SEPARATES_DEAR,
                  dict(total_assets=5e8, inception_date="2024-01-01", holdings_count=40)):
        cheap = _rating(0.03, **shape).score
        unknown = _rating(0.0, **shape).score
        dear = _rating(0.90, **shape).score
        assert dear <= unknown <= cheap, (shape, dear, unknown, cheap)


def test_the_score_stays_in_range_without_the_fee_component():
    for er in (0.0, 0.01, 0.5, 2.0):
        for holdings in (0, 30, 100, 500, 5000):
            s = _rating(er, holdings_count=holdings).score
            assert 1 <= s <= 5, (er, holdings, s)


def test_a_known_fee_still_moves_the_score():
    """Anti-vacuity: the fee must still matter when we DO know it."""
    assert _rating(0.03, **_SEPARATES_DEAR).score > _rating(0.90, **_SEPARATES_DEAR).score


def test_the_wire_carries_the_companion_flag():
    f = ETFNetYieldResponse.model_fields
    assert f["expense_ratio"].annotation is float, "the shipped field stays non-Optional"
    assert f["expense_ratio_known"].default is True, "absent must mean the old meaning"


def test_the_builder_derives_the_flag_from_the_value_not_a_literal():
    """A default of True is only honest if the builder actually computes it.

    Asserted on the AST of the keyword argument — a literal `True` there would ship
    "we know this fund's fee" for every fund whose fee is missing, and the schema default
    alone cannot tell the difference.
    """
    import ast

    src = Path(etf.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "ETFNetYieldResponse"]
    assert calls, "the net-yield response is no longer built here"
    kw = next((k for c in calls for k in c.keywords if k.arg == "expense_ratio_known"), None)
    assert kw is not None, "the builder does not set expense_ratio_known at all"
    assert isinstance(kw.value, ast.Compare), (
        f"expense_ratio_known is a literal ({ast.dump(kw.value)[:60]}…) — it must be "
        "derived from the value, e.g. `expense_ratio > 0`"
    )
    assert isinstance(kw.value.left, ast.Name) and kw.value.left.id == "expense_ratio"


def _brace_block(src: str, header: str) -> str:
    i = src.index(header)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    raise AssertionError("unbalanced braces")


def test_the_ios_renderer_has_a_neutral_state():
    """Brace-bound and comment-stripped: the doc comment beside the fix names every token
    this asserts, so an un-stripped scan would pass on prose after a revert."""
    src = (_IOS / "Models" / "ETFDetailModels.swift").read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())
    block = _brace_block(body, "struct ETFNetYield")
    assert "var expenseRatioKnown: Bool = true" in block, (
        "no companion on the client — 'Fee: 0%' still renders above 'Expense ratio "
        "unavailable for this fund.'"
    )
    fmt = _brace_block(block, "var formattedExpenseRatio")
    assert "expenseRatioKnown" in fmt and '"—"' in fmt


def test_the_ios_dto_decodes_the_flag_optionally():
    src = (_IOS / "Models" / "ETFDetailResponseModels.swift").read_text(encoding="utf-8")
    body = "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())
    assert "let expenseRatioKnown: Bool?" in body, (
        "a non-Optional new field named in CodingKeys throws keyNotFound against a backend "
        "that predates it — that blanks the whole ETF screen"
    )
    assert 'case expenseRatioKnown = "expense_ratio_known"' in body
    assert "expenseRatioKnown: netYield.expenseRatioKnown ?? true" in body
