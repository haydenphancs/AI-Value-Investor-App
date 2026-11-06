"""The live-earnings fast path: the company's own 8-K Item 2.02 release, read from SEC.

The one property everything else rests on: A FIGURE THE RELEASE DOES NOT STATE CANNOT REACH
A USER. Gemini returns each figure with a quote; a figure survives only when its quote is a
verbatim span of the release AND the value rounds to the number that quote prints, and that
printed number is what is kept. Below, hallucinated numbers, paraphrased quotes, a wrong scale
word, a flipped sign, a GAAP figure called adjusted and a foreign currency are each refused.

The SEC side is pinned too, because its limiter and 600 s throttle circuit are SHARED with
Learn, the buyback check and the Trillion stake finder: the per-day cap, the release-window
cadence, the stop once a filing is found, one extraction per accession, and parking every
symbol on a throttle.

So is the round's timing, because the card reads the record while a round runs: a figure is
handed over the moment it verifies, every symbol of a round comes due again a minute after that
round started, and a filing waiting on its next attempt rests a cadence from the end of the last
round that spent one.
Hermetic: fake SEC client and fake Gemini; `asyncio.sleep` is injected.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.earnings_filing_service as efs
import app.services.earnings_live_service as els
import app.services.learn_weekly.signals as signals
from app.config import settings
from app.integrations.sec_edgar import SECException, SECRateLimitException
from app.utils.market_hours import ET

REPORT = date(2026, 10, 29)
ACC = "0000050863-26-000123"

RELEASE = (
    "SANTA CLARA, Calif., Oct. 29, 2026 -- Intel Corporation today reported third-quarter "
    "2026 financial results. Third-quarter revenue was $17.1 billion, up 25% year-over-year. "
    "Third-quarter GAAP earnings per share (EPS) attributable to Intel was a loss of $(0.12); "
    "non-GAAP EPS attributable to Intel was $0.44. For the fourth quarter of 2026, Intel "
    "expects revenue of $17.5 billion to $18.5 billion and non-GAAP EPS of $0.48."
)


def _q(start: str, length: int = 0) -> str:
    """A verbatim span of RELEASE that starts at `start` and is at least 40 characters."""
    i = RELEASE.index(start)
    end = i + max(len(start), length or 0, 40)
    return RELEASE[i:min(end, len(RELEASE))]


def _good_answer(**overrides) -> Dict[str, Any]:
    answer = {
        "is_quarterly_results": True,
        "fiscal_period": "Q3 2026",
        "currency": "USD",
        "revenue": {"value": 17.1, "unit": "billion",
                    "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."},
        "eps_adjusted_diluted": {"value": 0.44,
                                 "quote": "non-GAAP EPS attributable to Intel was $0.44."},
        "eps_gaap_diluted": {"value": -0.12,
                             "quote": "attributable to Intel was a loss of $(0.12);"},
        "guidance": {
            "text": "Intel expects fourth-quarter revenue of $17.5 billion to $18.5 billion.",
            "quote": "Intel expects revenue of $17.5 billion to $18.5 billion and non-GAAP EPS of $0.48.",
        },
    }
    answer.update(overrides)
    return answer


def _verify(answer):
    return efs.verify_extraction(answer, RELEASE, accession=ACC, accepted_at=None, url="u")


# ── verification ─────────────────────────────────────────────────────────────


def test_a_correct_answer_survives_whole():
    figures, reasons = _verify(_good_answer())
    assert (figures.revenue, figures.eps_adjusted, figures.eps_gaap) == (17.1e9, 0.44, -0.12)
    assert figures.guidance.startswith("Intel expects")
    assert set(reasons.values()) == {"ok"}


def test_a_hallucinated_number_with_a_real_quote_is_refused():
    answer = _good_answer(revenue={"value": 17.4, "unit": "billion",
                                   "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."})
    figures, reasons = _verify(answer)
    assert figures.revenue is None and reasons["revenue"] == "value_mismatch"


def test_a_paraphrased_quote_is_refused_even_with_the_right_number():
    answer = _good_answer(revenue={"value": 17.1, "unit": "billion",
                                   "quote": "Revenue for the third quarter came in at $17.1 billion overall."})
    figures, reasons = _verify(answer)
    assert figures.revenue is None and reasons["revenue"] == "not_verbatim"


def test_a_scale_word_the_quote_does_not_print_is_refused():
    """The scale is what a model most often gets wrong — a 1,000x error is a fabricated miss."""
    answer = _good_answer(revenue={"value": 17.1, "unit": "million",
                                   "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."})
    figures, reasons = _verify(answer)
    assert figures.revenue is None and reasons["revenue"] == "value_mismatch"


def test_a_flipped_sign_is_refused():
    answer = _good_answer(eps_gaap_diluted={"value": 0.12,
                                            "quote": "attributable to Intel was a loss of $(0.12);"})
    figures, reasons = _verify(answer)
    assert figures.eps_gaap is None and reasons["eps_gaap_diluted"] == "value_mismatch"


def test_a_loss_must_read_as_a_loss_in_its_quote():
    assert efs.eps_from_quote("non-GAAP EPS attributable to Intel was $0.44.", -0.44) is None
    assert efs.eps_from_quote("a net loss of $0.46 per diluted share", -0.46) == -0.46
    assert efs.eps_from_quote("diluted EPS of $(0.46)", -0.46) == -0.46


def test_a_quote_that_cuts_a_number_is_refused():
    """`verify_peg_span` refuses "$1" quoted out of "$17.1 billion"."""
    quote = "Third-quarter revenue was $1"
    assert not efs.verify_peg_span(quote + " " * 20, RELEASE)


def test_guidance_with_a_number_its_quote_does_not_print_is_refused():
    answer = _good_answer(guidance={
        "text": "Intel expects fourth-quarter revenue of about $19 billion.",
        "quote": "Intel expects revenue of $17.5 billion to $18.5 billion and non-GAAP EPS of $0.48.",
    })
    figures, reasons = _verify(answer)
    assert figures.guidance is None and reasons["guidance"] == "value_mismatch"


def test_a_foreign_currency_release_is_dropped_whole():
    figures, reasons = _verify(_good_answer(currency="EUR"))
    assert figures is None and reasons == {"answer": "currency_eur"}


def test_a_document_that_is_not_a_results_release_yields_nothing():
    figures, reasons = _verify({"is_quarterly_results": False})
    assert figures is None and reasons == {"answer": "not_results"}


def test_an_off_schema_answer_yields_nothing():
    assert _verify("not a dict") == (None, {"answer": "off_schema"})
    assert efs.unwrap_json({"text": "{not json"}) is None
    assert efs.unwrap_json({"text": json.dumps({"a": 1})}) == {"a": 1}


def test_a_short_suffix_scale_is_read():
    assert efs.revenue_from_quote("revenue of $13.7B in the quarter, up 3% from a year ago", 13.7,
                                  "billion") == 13.7e9


# ── the printed number: its sign, its digits, its scale, its basis ───────────
#
# What is stored is what the release prints, never what the model said it printed. These run
# on the shapes a real EX-99.1 takes once `html_to_text` has flattened it: a table cell that
# closes a parenthesis on its own ("$ (0.46 )"), a header far above its row, Intel's
# "earnings (loss) per share".


def _verify_in(text: str, **answer):
    full = {"is_quarterly_results": True, "currency": "USD", **answer}
    return efs.verify_extraction(full, text, accession=ACC, accepted_at=None, url="u")


def _revenue_of(quote: str, value: Any, unit: Any = "billion", text: Optional[str] = None):
    node = {"value": value, "unit": unit, "quote": quote}
    return _verify_in(text if text is not None else f"Acme reported results. {quote}", revenue=node)


_INTEL_BOTH = ("Third-quarter GAAP EPS attributable to Intel was $(0.19); non-GAAP EPS "
               "attributable to Intel was $0.13.")
_EARNINGS_LOSS_LABEL = "GAAP earnings (loss) per share attributable to Intel was $0.46 in Q3"
_LOSS_LABEL = "GAAP net loss per diluted share was $0.46 in the third quarter"
_LOSS_TOTAL_FIRST = "net loss of $120 million, or $0.46 per diluted share"
_INCOME_TOTAL_FIRST = "net income of $120 million ($0.46 per diluted share)"
_CUT_LOSS = "attributable to Intel was a loss of $(0.46"
_CUT_NO_WORD = "attributable to Intel, diluted EPS, was $(0.46"
_FOOTNOTE = "EPS(1) for the third quarter of the year was $1.00"


@pytest.mark.parametrize("quote,value,expected", [
    # The number the model picked decides, never another figure of the same quote.
    (_INTEL_BOTH, -0.13, None),
    (_INTEL_BOTH, 0.13, 0.13),
    (_INTEL_BOTH, -0.19, -0.19),
    (_INTEL_BOTH, 0.19, None),
    # "(loss)" in a label says the line can go either way; it does not say this one did.
    (_EARNINGS_LOSS_LABEL, 0.46, 0.46),
    (_EARNINGS_LOSS_LABEL, -0.46, None),
    (_LOSS_LABEL, 0.46, None),
    (_LOSS_LABEL, -0.46, -0.46),
    # A "$" between the word and the number breaks nothing.
    (_LOSS_TOTAL_FIRST, 0.46, None),
    (_LOSS_TOTAL_FIRST, -0.46, -0.46),
    # Parentheses around a phrase are no accounting negative.
    (_INCOME_TOTAL_FIRST, 0.46, 0.46),
    (_INCOME_TOTAL_FIRST, -0.46, None),
    # A quote cut before its ")" proves no sign on its own, and words can still prove one.
    (_CUT_LOSS, 0.46, None),
    (_CUT_LOSS, -0.46, -0.46),
    (_CUT_NO_WORD, 0.46, None),
    (_CUT_NO_WORD, -0.46, None),
    (_CUT_NO_WORD + "  ", 0.46, None),
    # A footnote marker is no figure, and no sign for the figure beside it.
    (_FOOTNOTE, -1.0, None),
    (_FOOTNOTE, 1.0, 1.0),
    # Directly after the number.
    ("$0.25 loss per share in the third quarter", 0.25, None),
    ("$0.25 loss per share in the third quarter", -0.25, -0.25),
    ("$0.25 per diluted share loss in the third quarter", -0.25, -0.25),
    ("$0.25 per diluted share loss in the third quarter", 0.25, None),
    ("Intel reported a $0.25 a share loss in the third quarter", -0.25, -0.25),
    # Labels and asides.
    ("Net (loss) income per share was $(0.46) in the quarter", -0.46, -0.46),
    ("Net (loss) income per share was $0.46 in the quarter", 0.46, 0.46),
    ("Net (loss) per share of $0.46 in the quarter", -0.46, -0.46),
    ("Net (loss) per share of $0.46 in the quarter", 0.46, None),
    ("Net loss, excluding special items, was $0.46 per share", -0.46, -0.46),
    ("EPS, including a $1,000 loss on a sale, was $0.46 per share", 0.46, 0.46),
    ("EPS, which includes a loss on a sale, was $0.46 per share", 0.46, 0.46),
    ("Net income, excluding a loss on a sale, of $0.46 per share", 0.46, 0.46),
    # Income tax is no income.
    ("Net loss after income tax of $0.46 per share", -0.46, -0.46),
    ("Net loss after income tax of $0.46 per share", 0.46, None),
    ("Loss before income taxes of $0.46 per share", 0.46, None),
    # The other spellings of a loss.
    ("Negative EPS of $0.46 for the third quarter", 0.46, None),
    ("Negative EPS of $0.46 for the third quarter", -0.46, -0.46),
    ("Intel lost $0.46 per share in the third quarter", -0.46, -0.46),
    # Another figure's loss word sits before a semicolon, or before the measure's own name.
    ("GAAP net loss was $0.12 per share; non-GAAP EPS was $0.44", 0.44, 0.44),
    ("GAAP net loss was $0.12 per share; $0.44 on a non-GAAP basis", 0.44, 0.44),
    ("GAAP net loss of $0.12 per share and adjusted EPS of $0.44", 0.44, 0.44),
    ("adjusted EPS of $0.44 compared with a GAAP net loss of $0.12", 0.44, 0.44),
    # A table flattened by `html_to_text`: the ")" sits in a cell of its own.
    ("Net loss per share, diluted $ (0.46 ) $ (0.31 )", -0.46, -0.46),
    ("Net loss per share, diluted $ (0.46 ) $ (0.31 )", 0.46, None),
    ("Diluted earnings per share $ ( 0.46 ) in the quarter", -0.46, -0.46),
    # Dashes are read by their shape: a minus touches its number.
    ("Diluted EPS - $0.46 for the third quarter", 0.46, 0.46),
    ("Diluted EPS - $0.46 for the third quarter", -0.46, None),
    ("Diluted EPS of –$0.25 for the third quarter", 0.25, None),
    ("Diluted EPS of –$0.25 for the third quarter", -0.25, -0.25),
    ("Diluted EPS of \u2212$0.25 for the third quarter", -0.25, -0.25),
    ("Diluted EPS of -$0.25 for the third quarter", 0.25, None),
    ("Diluted EPS of $-0.25 for the third quarter", -0.25, -0.25),
    ("Diluted EPS of $ -0.25 for the third quarter", 0.25, None),
    ("Diluted EPS of -0.25 for the third quarter", -0.25, -0.25),
    ("-$0.25 diluted EPS for the third quarter", -0.25, -0.25),
    ("a range of $0.40-$0.44 per diluted share", 0.44, 0.44),
    ("a range of $0.40-$0.44 per diluted share", -0.44, None),
    ("a range of $0.40–$0.44 per diluted share", 0.44, 0.44),
    ("a range of $0.40 - $0.44 per diluted share", 0.44, 0.44),
    ("EPS rose 5%-$0.25 was the gain", 0.25, 0.25),
    ("EPS(1)-$0.25 for the third quarter", 0.25, 0.25),
])
def test_the_sign_is_read_from_the_matched_number(quote, value, expected):
    assert efs.eps_from_quote(quote, value) == expected


def test_a_loss_word_nearest_the_number_refuses_a_figure_for_another_measure():
    """Accepted over-refusal, pinned so it is a choice: the field drops and FMP fills it."""
    quote = "GAAP net loss was $0.12 per share, $0.44 on a non-GAAP basis"
    assert efs.eps_from_quote(quote, 0.44) is None
    assert efs.eps_from_quote(quote, -0.12) == -0.12


@pytest.mark.parametrize("quote,value", [
    ("diluted EPS of $(0.00) for the quarter", 0.0),
    ("diluted EPS of $(0.00) for the quarter", -0.0),
    ("a net loss of $0.00 per diluted share", 0.0),
    ("a net loss of $0.00 per diluted share", -0.004),
])
def test_a_breakeven_is_never_a_negative_zero(quote, value):
    kept = efs.eps_from_quote(quote, value)
    assert kept == 0.0 and math.copysign(1.0, kept) == 1.0


def test_a_scaled_or_unmarked_number_is_never_read_as_eps():
    assert efs.eps_from_quote("net income of $0.44 billion in the quarter", 0.44) is None
    assert efs.eps_from_quote("Q3 results for fiscal 2026 and the quarter ended", 3) is None
    assert efs.eps_from_quote("diluted EPS of $1,234.56", 1234.56) is None


def test_a_printed_negative_is_never_revenue():
    assert efs.revenue_from_quote("revenue of $(17.1) billion for the period", 17.1,
                                  "billion") is None
    assert efs.revenue_from_quote("revenue of $(17.1 billion) for the period", 17.1,
                                  "billion") is None
    assert efs.revenue_from_quote("revenue of -$17.1 billion for the period", 17.1,
                                  "billion") is None
    assert efs.revenue_from_quote("revenue for the period of $(17.1 billion", 17.1,
                                  "billion") is None
    assert efs.revenue_from_quote("Revenue - $13.7 billion for the period", 13.7,
                                  "billion") == 13.7e9


def test_a_revenue_printed_as_zero_is_never_kept():
    """0.3 rounds to the printed "$0 billion"; a zero is no revenue a filing can state."""
    assert efs.revenue_from_quote("revenue of $0 billion for the period", 0.3, "billion") is None


@pytest.mark.parametrize("value", [
    True, "0.46", None, float("nan"), float("inf"), float("-inf"), 10 ** 400, -(10 ** 400), 1e300,
])
def test_a_value_no_figure_could_be_is_refused(value):
    assert efs.eps_from_quote("diluted EPS of $0.46 for the third quarter", value) is None
    assert efs.revenue_from_quote("revenue of $17.1 billion for the third quarter", value,
                                  "billion") is None


@pytest.mark.parametrize("unit", ["euros", "dozen", 5, ["million"], {"unit": "million"}])
def test_a_unit_that_is_no_scale_is_refused(unit):
    assert efs.revenue_from_quote("revenue of $17.1 billion for the quarter", 17.1, unit) is None


@pytest.mark.parametrize("value", [0, -17.1])
def test_a_revenue_value_at_or_below_zero_is_refused(value):
    assert efs.revenue_from_quote("revenue of $17.1 billion for the quarter", value,
                                  "billion") is None


def test_an_empty_quote_prints_nothing():
    assert efs.eps_from_quote("", 0.46) is None
    assert efs.eps_from_quote("", 0.0) is None
    assert efs.revenue_from_quote("", 17.1, "billion", release_text="") is None


def test_the_eps_cap_is_exactly_a_thousand_dollars():
    assert efs.eps_from_quote("diluted EPS of $1,000.00 for the quarter", 1000) == 1000.0
    assert efs.eps_from_quote("diluted EPS of $1,000.01 for the quarter", 1000.01) is None


def test_a_huge_print_with_its_huge_value_never_raises():
    digits = "1" + "0" * 300
    assert efs.revenue_from_quote(f"revenue of ${digits} billion", float(digits), "billion") is None


def test_a_figure_off_by_a_printed_digit_is_refused():
    assert efs.eps_from_quote("diluted EPS of $12.25 for the quarter", 12.30) is None
    assert efs.eps_from_quote("diluted EPS of $2.95 for the quarter", 2.96) is None
    assert efs.eps_from_quote("diluted EPS of $6.43 for the quarter", 6.46) is None
    assert efs.eps_from_quote("diluted EPS of $4.00 for the quarter", 4.02) is None
    assert efs.revenue_from_quote("revenue of $17.1 billion in the quarter", 17.18,
                                  "billion") is None
    assert efs.revenue_from_quote("revenue of $13.65 billion in the quarter", 13.70,
                                  "billion") is None


def test_the_number_kept_is_the_printed_one():
    assert efs.revenue_from_quote("revenue of $13.7 billion in the quarter", 13.653,
                                  "billion") == 13.7e9
    assert efs.eps_from_quote("diluted EPS of $0.46 for the quarter", 0.4649) == 0.46
    assert efs.eps_from_quote("a net loss of $0.46 per share", -0.4649) == -0.46
    figures, reasons = _revenue_of("Third-quarter revenue was $13.7 billion, up from a year ago.",
                                   13.653)
    assert figures.revenue == 13.7e9 and reasons["revenue"] == "ok"


@pytest.mark.parametrize("quote,value,expected", [
    # Rounding lets 1.20 reach the "$1" dividend too: the figure itself must win.
    ("Acme paid a $1 special dividend and diluted EPS was $1.20 in the quarter", 1.2, 1.2),
    ("Diluted EPS was $1.20 in the quarter and Acme paid a $1 special dividend", 1.2, 1.2),
    # Nothing prints the value: the coarser print next to it is no stand-in.
    ("net income of $1.20 billion in the quarter, and a $1 dividend", 1.2, None),
    ("GAAP loss of $(1.20); adjusted EPS of $1 in the quarter", 1.2, None),
    # Two prints of one number: the sign chooses between them.
    ("GAAP EPS of $(0.46); non-GAAP EPS of $0.46 in the quarter", 0.46, 0.46),
    ("GAAP EPS of $(0.46); non-GAAP EPS of $0.46 in the quarter", -0.46, -0.46),
])
def test_the_closest_print_is_the_figure_and_a_coarser_one_never_stands_in(quote, value, expected):
    assert efs.eps_from_quote(quote, value) == expected


def test_the_closest_revenue_print_is_the_figure():
    guided = "Against guidance of $17 billion, third-quarter revenue was $17.1 billion in all"
    assert efs.revenue_from_quote(guided, 17.1, "billion") == 17.1e9
    assert efs.revenue_from_quote(guided, 17.0, "billion") == 17e9
    figures, _ = _revenue_of(guided, 17.1)
    assert figures.revenue_rounding == 5e7
    # The closest print carries another scale: the "$17 million" beside it is no stand-in.
    assert efs.revenue_from_quote("revenue of $17.1 billion, with $17 million of it in Asia", 17.1,
                                  "million") is None


def test_a_leading_dot_figure_is_read_as_printed():
    quote = "Diluted EPS for the quarter was $.46 a share, up a cent."
    assert efs.eps_from_quote(quote, 0.46) == 0.46
    assert efs.eps_from_quote(quote, 46) is None
    assert efs.eps_from_quote("Diluted EPS for the quarter was ($.46) a share", -0.46) == -0.46
    assert efs.eps_from_quote("Diluted EPS for the quarter was ($.46) a share", 0.46) is None
    assert efs.revenue_from_quote("Revenue for the quarter was $.9 billion in total", 0.9,
                                  "billion") == 9e8
    figures, _ = _revenue_of("Revenue for the quarter was $.9 billion in total, up 5%.", 0.9)
    assert figures.revenue_rounding == 5e7


_THOUSANDS_TABLE = "(In thousands, except per share amounts) Three Months Ended Revenue $ 48,250 $ 41,120"
_FAR_HEADER = (
    "Acme reported results. CONDENSED STATEMENTS (In thousands, except per share amounts) "
    "Three Months Ended September 30, 2026 2025. Net sales $ 10,181,045 $ 9,870,212 in total."
)
_FAR_ROW = "Net sales $ 10,181,045 $ 9,870,212 in total for the quarter"


@pytest.mark.parametrize("unit", ["units", None, ""])
def test_a_table_figure_is_never_read_as_whole_dollars(unit):
    figures, reasons = _revenue_of(_THOUSANDS_TABLE, 48250, unit)
    assert figures is None and reasons["revenue"] == "value_mismatch"
    text = _FAR_HEADER + " " + _FAR_ROW
    figures, reasons = _revenue_of(_FAR_ROW, 10181045, unit, text=text)
    assert figures is None and reasons["revenue"] == "value_mismatch"


def test_a_table_figure_scaled_by_a_header_in_its_quote_is_read():
    figures, reasons = _revenue_of(_THOUSANDS_TABLE, 48250, "thousand")
    assert figures.revenue == 48250000.0 and reasons["revenue"] == "ok"
    assert figures.revenue_rounding == 500.0


def test_a_table_row_flattened_from_html_reads_like_its_text():
    html = ("<html><body><p>(In thousands, except per share amounts)</p><table><tr><td>Revenue</td>"
            "<td>$</td><td>48,250</td><td>$</td><td>41,120</td></tr><tr><td>Diluted EPS</td>"
            "<td>$</td><td>(0.46</td><td>)</td><td>$</td><td>(0.31</td><td>)</td></tr></table>"
            "</body></html>")
    text = efs.html_to_text(html, document="ex99.htm", max_chars=40_000, log_tag="earnings-live")
    quote = "(In thousands, except per share amounts) Revenue $ 48,250 $ 41,120"
    gaap_quote = "Revenue $ 48,250 $ 41,120 Diluted EPS $ (0.46 ) $ (0.31 )"
    figures, reasons = _verify_in(
        text, revenue={"value": 48250, "unit": "thousand", "quote": quote},
        eps_gaap_diluted={"value": -0.46, "quote": gaap_quote})
    assert (figures.revenue, figures.eps_gaap) == (48250000.0, -0.46), reasons


@pytest.mark.parametrize("quote,value,unit,expected", [
    ("(In millions, except per share data) Total revenue $ 13,653 $ 12,000", 13653, "million",
     13.653e9),
    ("(Dollars in millions) Total net revenue 13,653 12,000 for the quarter", 13653, "million",
     13.653e9),
    ("Millions of dollars, except per-share amounts. Total revenue 13,653 12,000", 13653,
     "million", 13.653e9),
    ("($ in millions, except per share amounts) Net sales $ 13,653 $ 12,000", 13653, "million",
     13.653e9),
    ("In millions of USD or shares as applicable. Total revenues $ 13,653", 13653, "million",
     13.653e9),
    ("(in thousands of U.S. dollars) Total revenues $ 48,250 $ 41,120 for Q3", 48250, "thousand",
     48250000.0),
    ("(dollars in million) Total net revenue 13,653 12,000 for the quarter", 13653, "million",
     13.653e9),
    ("($000's) Total revenue for the three months ended 48,250 41,120", 48250, "thousand",
     48250000.0),
    ("(000's omitted) Total revenue for the three months ended 48,250 41,120", 48250, "thousand",
     48250000.0),
    ("($000) Total revenue for the three months ended $ 48,250 $ 41,120", 48250, "thousand",
     48250000.0),
    ("Revenue ($B) $17.1 $13.7, Gross margin 39.2% 38.0% for the quarter", 17.1, "billion",
     17.1e9),
    ("Revenue ($M) 13,653 12,000, gross margin 39.2% 38.0% for the quarter", 13653, "million",
     13.653e9),
])
def test_the_scale_shapes_of_a_real_table_header_are_read(quote, value, unit, expected):
    figures, reasons = _revenue_of(quote, value, unit)
    assert figures is not None and figures.revenue == expected, reasons


@pytest.mark.parametrize("quote", [
    "(In millions, except shares in thousands) Total revenue $ 48,250 $ 41,120",
    "(In millions, except number of shares, which are reflected in thousands) Net sales $ 48,250",
])
@pytest.mark.parametrize("unit", ["million", "thousand", "units"])
def test_two_scales_in_one_quote_prove_neither(quote, unit):
    figures, reasons = _revenue_of(quote, 48250, unit)
    assert figures is None and reasons["revenue"] == "value_mismatch"


@pytest.mark.parametrize("quote", [
    "Revenue of $48,250 came from millions of customers in the quarter",
    "Revenue of $48,250 came from hundreds of millions of dollars in orders",
])
def test_a_plural_scale_word_in_prose_names_no_table(quote):
    figures, reasons = _revenue_of(quote, 48250, "million")
    assert figures is None and reasons["revenue"] == "value_mismatch"


def test_the_scale_beside_the_number_outranks_a_header():
    quote = "(In millions, except per share data) Net sales were $17.1 billion for the period"
    assert efs.revenue_from_quote(quote, 17.1, "billion") == 17.1e9
    assert efs.revenue_from_quote(quote, 17.1, "million") is None


def test_a_full_dollar_amount_needs_a_release_with_no_scale_header():
    quote = "Total revenue for the third quarter was $48,250,123, up 12% from a year ago."
    figures, reasons = _revenue_of(quote, 48250123, "units")
    assert figures.revenue == 48250123.0 and reasons["revenue"] == "ok"
    assert figures.revenue_rounding == 0.5


@pytest.mark.parametrize("header", [
    "(In thousands, except per share amounts)", "(in millions)", "(Billions)",
    "(in 000's)", "(000s)", "($000)", "($000s)", "(000)", "(in thousand)", "($M)", "($ MM)",
    "($K)", "(US$M)",
])
def test_a_scale_header_anywhere_in_the_release_refuses_whole_dollars(header):
    quote = "Total revenue for the third quarter was $48,250,123, up 12% from a year ago."
    text = f"Acme reported results. {quote} STATEMENTS OF OPERATIONS {header} Revenue 1 2 3"
    figures, reasons = _revenue_of(quote, 48250123, "units", text=text)
    assert figures is None and reasons["revenue"] == "value_mismatch"


def test_whole_dollars_are_refused_with_no_release_to_read():
    quote = "Total revenue for the third quarter was $48,250,123, up 12% from a year ago."
    assert efs.revenue_from_quote(quote, 48250123, "units") is None
    assert efs.revenue_from_quote(quote, 48250123, "units", release_text=quote) == 48250123.0


def test_a_small_or_unmarked_whole_number_never_passes_as_dollars():
    assert efs.revenue_from_quote("Total revenue for the quarter was $999,999 in all", 999999,
                                  "units", release_text="plain") is None
    assert efs.revenue_from_quote("Total revenue for the quarter was 48,250,123 in all", 48250123,
                                  "units", release_text="plain") is None
    # Intel's bare table cell: a scale header it never said out loud.
    assert efs.revenue_from_quote("Revenue ($B) $17.1 for the third quarter", 17.1, "units",
                                  release_text="Revenue ($B) $17.1 for the third quarter") is None


_GAAP_ONLY_SENTENCES = [
    "Diluted earnings per share for the third quarter were $0.46 compared with $0.31 a year ago.",
    "Diluted earnings per share were $0.46, compared with $0.31 in the comparable quarter last year.",
    "Diluted EPS of $0.46 included a charge of $0.10 per share from special items.",
    "Comparable sales rose 4% and diluted earnings per share were $0.46 in the quarter.",
    "Our core business grew 4% and diluted earnings per share were $0.46 in the quarter.",
    "Diluted EPS of $0.46, adjusted for the two-for-one stock split in August.",
    "Diluted earnings per share, adjusted for the two-for-one stock split, were $0.46.",
    "Diluted EPS, as adjusted to reflect the two-for-one stock split, was $0.46.",
    "Adjusted EBITDA rose 4% and diluted earnings per share were $0.46 in the quarter.",
    "Net income before income taxes rose 4% and diluted EPS was $0.46 in the quarter.",
]
_ADJUSTED_LABELS = [
    "non-GAAP EPS attributable to Intel was $0.46.",
    "Non\u2011GAAP diluted EPS of $0.46, up 12% from a year ago, the company said.",
    "Non–GAAP earnings per diluted share were $0.46 in the quarter.",
    "Non GAAP earnings per diluted share were $0.46 in the quarter.",
    "Adjusted EPS of $0.46 was up 12% from a year ago, the company said.",
    "Adj. EPS of $0.46 was up 12% from a year ago, the company said.",
    "Adj EPS of $0.46 was up 12% from a year ago, the company said.",
    "Adjusted(3) diluted EPS of $0.46 was up 12% from a year ago.",
    "Adjusted (3) diluted EPS of $0.46 was up 12% from a year ago.",
    "Adjusted3 diluted EPS of $0.46 was up 12% from a year ago.",
    "EPS-diluted-adjusted $0.46 up 12% from a year ago in the quarter",
    "Adjusted diluted earnings per share were $0.46 in the quarter.",
    "Adjusted earnings per diluted share were $0.46 in the quarter.",
    "Adjusted net income per diluted share was $0.46 in the quarter.",
    "Adjusted per-share earnings were $0.46 in the third quarter.",
    "Core earnings per share were $0.46, up 12% from a year ago.",
    "Comparable EPS of $0.46 was up 12% from a year ago, the company said.",
    "Underlying EPS of $0.46 was up 12% from a year ago, the company said.",
    "Normalized EPS of $0.46 was up 12% from a year ago, the company said.",
    "Pro forma diluted EPS of $0.46 was up 12% from a year ago.",
    "Pro-forma diluted EPS of $0.46 was up 12% from a year ago.",
    "Operating earnings per share of $0.46 were up 12% from a year ago.",
    "Diluted EPS, as adjusted, of $0.46 was up 12% from a year ago.",
    "Diluted EPS - adjusted $0.46 up 12% from a year ago in the quarter",
    "Diluted EPS (adjusted) of $0.46 was up 12% from a year ago.",
    "Excluding special items, diluted EPS was $0.46 in the quarter.",
    "Diluted EPS, excluding a $0.10 per share restructuring charge, was $0.46.",
    "Diluted EPS, excluding the effect of the tax law change, was $0.46.",
    "EPS before special items was $0.46, up 12% from a year ago.",
    "On an adjusted basis, diluted EPS was $0.46 in the quarter.",
]


@pytest.mark.parametrize("quote", _GAAP_ONLY_SENTENCES)
def test_an_adjusted_figure_must_say_it_is_adjusted(quote):
    node = {"value": 0.46, "quote": quote}
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted=node, eps_gaap_diluted=dict(node))
    assert reasons["eps_adjusted_diluted"] == "not_adjusted"
    assert (figures.eps_adjusted, figures.eps_gaap) == (None, 0.46)


def test_a_refused_adjusted_figure_is_never_promoted_to_gaap():
    quote = _GAAP_ONLY_SENTENCES[0]
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": 0.46, "quote": quote})
    assert figures is None and reasons["eps_adjusted_diluted"] == "not_adjusted"


@pytest.mark.parametrize("quote", _ADJUSTED_LABELS)
def test_an_adjusted_figure_that_names_its_measure_stands(quote):
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": 0.46, "quote": quote})
    assert figures.eps_adjusted == 0.46 and reasons["eps_adjusted_diluted"] == "ok"


@pytest.mark.parametrize("quote", [
    "Adjusted net loss per share of $0.46 narrowed from a year ago.",
    "Adjusted loss per share of $0.46 narrowed from a year ago.",
    "Non-GAAP net loss per share was $(0.46) in the third quarter.",
])
def test_an_adjusted_loss_names_its_measure_too(quote):
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": -0.46, "quote": quote})
    assert figures.eps_adjusted == -0.46 and reasons["eps_adjusted_diluted"] == "ok"


def test_the_value_is_checked_before_the_basis():
    quote = _GAAP_ONLY_SENTENCES[0]
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": 0.99, "quote": quote})
    assert figures is None and reasons["eps_adjusted_diluted"] == "value_mismatch"


def test_a_figure_refused_as_gaap_shows_uncoloured_only_when_the_gaap_one_verified():
    quote = _GAAP_ONLY_SENTENCES[0]
    node = {"value": 0.46, "quote": quote}
    figures, _ = _verify_in(f"Acme reported results. {quote}", eps_adjusted_diluted=node,
                            eps_gaap_diluted=dict(node))
    rec = els._Record("ACME", REPORT, eps_estimate=0.40, filing=figures)
    shown = els.eps_metric(rec)
    assert (shown.actual, shown.basis, shown.surprise_pct) == (0.46, els.BASIS_GAAP, None)


def test_a_rounded_print_gets_no_verdict_inside_its_own_rounding():
    figures, _ = _verify(_good_answer())   # "$17.1 billion"
    assert figures.revenue_rounding == 5e7

    def metric(est, **kw):
        return els.revenue_metric(els._Record("INTC", REPORT, revenue_estimate=est,
                                              filing=figures, **kw))

    assert [metric(e).surprise_pct for e in (17.05e9, 17.08e9, 17.1e9, 17.13e9, 17.15e9)] == [None] * 5
    assert metric(17.08e9).actual == 17.1e9
    assert metric(17.08e9).source == els.SOURCE_FILING
    assert metric(17.04e9).surprise_pct == 0.35
    assert metric(17.16e9).surprise_pct == -0.35
    assert metric(16.6e9).surprise_pct == 3.01
    assert metric(17.08e9, fmp_revenue=17.06e9).surprise_pct == -0.12


def test_the_guards_still_run_before_the_rounding_band():
    figures, _ = _verify(_good_answer())
    rec = els._Record("INTC", REPORT, revenue_estimate=5e9, filing=figures)
    assert els.revenue_metric(rec).checking is True


@pytest.mark.parametrize("quote,value,unit,half", [
    ("Third-quarter revenue was $17.1 billion, up 25% year-over-year.", 17.1, "billion", 5e7),
    ("Third-quarter revenue was $13.7B, up 25% year-over-year, it said.", 13.7, "billion", 5e7),
    ("Third-quarter revenue was $13.65 billion, up 25% year-over-year.", 13.65, "billion", 5e6),
    ("Third-quarter revenue was $13,653 million, up 25% year-over-year.", 13653, "million", 5e5),
    ("Third-quarter revenue was $2 billion, up 25% from a year ago.", 2, "billion", 5e8),
])
def test_the_rounding_is_half_the_last_printed_digit(quote, value, unit, half):
    figures, reasons = _revenue_of(quote, value, unit)
    assert figures.revenue_rounding == half, reasons


def test_a_refused_revenue_carries_no_rounding():
    answer = _good_answer(revenue={"value": 17.4, "unit": "billion",
                                   "quote": "Third-quarter revenue was $17.1 billion, up 25% year-over-year."})
    figures, _ = _verify(answer)
    assert figures.revenue is None and figures.revenue_rounding is None


def test_a_rounded_print_is_announced_without_a_verdict():
    from app.services.notification_senders import earnings_live_sender as sender

    figures, _ = _verify(_good_answer())
    rec = els._Record("INTC", REPORT, revenue_estimate=17.08e9, eps_estimate=0.41, filing=figures)
    snap = els.snapshot_of(rec, REPORT, datetime(2026, 10, 29, 16, 5, tzinfo=ET))
    _, body = sender.live_copy(snap)
    assert "Revenue $17.1B ·" in body
    assert "Revenue $17.1B (" not in body, "no beat or miss inside the print's own rounding"
    assert els.card_fields(snap, REPORT)["revenue_surprise_pct"] is None


@pytest.mark.parametrize("printed", [
    "€1.2 billion", "£1.2 billion", "¥1.2 billion", "₹1.2 billion", "C$1.2 billion",
    "CA$1.2 billion", "A$1.2 billion", "HK$1.2 billion", "NT$1.2 billion", "CAD$1.2 billion",
    "EUR 1.2 billion", "EUR1.2 billion", "GBP 1.2 billion", "JPY 1.2 billion", "CAD 1.2 billion",
    "AUD 1.2 billion", "RMB1.2 billion", "$1.2 billion CAD", "1.2 billion EUR", "1.2 billion euros",
    "$1.2 billion Canadian dollars",
])
def test_a_figure_printed_in_another_currency_is_refused(printed):
    quote = f"Revenue for the third quarter was {printed}, up from a year ago in all regions."
    figures, reasons = _revenue_of(quote, 1.2)
    assert figures is None and reasons["revenue"] == "foreign_currency"


@pytest.mark.parametrize("printed", [
    "$1.2 billion", "US$1.2 billion", "U.S.$1.2 billion", "USD 1.2 billion", "USD1.2 billion",
    "$1.2 billion USD", "$1.2 billion U.S. dollars",
])
def test_a_figure_printed_in_us_dollars_stands(printed):
    quote = f"Revenue for the third quarter was {printed}, up from a year ago in all regions."
    figures, reasons = _revenue_of(quote, 1.2)
    assert figures.revenue == 1.2e9 and reasons["revenue"] == "ok"


def test_a_foreign_mark_refuses_an_eps_and_a_share_class_letter_does_not():
    quote = "Adjusted EPS was C$0.46 for the third quarter of the year."
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": 0.46, "quote": quote})
    assert figures is None and reasons["eps_adjusted_diluted"] == "foreign_currency"
    quote = "Adjusted EPS for Class A $1.20 and for Class C $1.21 in the third quarter."
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": 1.2, "quote": quote})
    assert figures.eps_adjusted == 1.2 and reasons["eps_adjusted_diluted"] == "ok"


def test_a_label_glued_to_its_dollar_sign_reads_as_dollars():
    quote = "Adjusted Diluted EPS$0.46 for the third quarter of the year."
    figures, reasons = _verify_in(f"Acme reported results. {quote}",
                                  eps_adjusted_diluted={"value": 0.46, "quote": quote})
    assert figures.eps_adjusted == 0.46 and reasons["eps_adjusted_diluted"] == "ok"


def test_a_guidance_quote_in_another_currency_never_shows_as_dollars():
    quote = "For the fourth quarter Acme expects revenue of €1.5 billion, it said."
    text = f"Acme reported results. {quote}"
    figures, reasons = _verify_in(text, guidance={"text": "Acme expects revenue of $1.5 billion.",
                                                  "quote": quote})
    assert figures is None and reasons["guidance"] == "foreign_currency"


@pytest.mark.parametrize("currency", ["", "   ", None])
def test_a_blank_currency_is_refused_whole(currency):
    figures, reasons = _verify(_good_answer(currency=currency))
    assert figures is None and reasons == {"answer": "currency_blank"}


def test_a_missing_currency_is_refused_whole_and_the_schema_requires_it():
    answer = _good_answer()
    del answer["currency"]
    figures, reasons = _verify(answer)
    assert figures is None and reasons == {"answer": "currency_blank"}
    assert "currency" in efs.EXTRACTION_SCHEMA["required"]


@pytest.mark.parametrize("rule", [
    "closing one included",                     # the sign
    "names it as a loss",
    "the digits in that quote",                 # the printed digits
    "the table's header",                       # the scale
    "that header must be inside the quote",
    "a full dollar amount printed with all its digits",
    "prefer the sentence that prints the scale word",
    "words naming the measure",                 # the basis
    "`currency` is always given",               # the currency
    "another currency's mark",
    "with its $ sign or decimal point",         # what an EPS print looks like
])
def test_the_prompt_tells_the_model_every_rule_the_verifier_enforces(rule):
    assert rule in efs._SYSTEM


# ── the SEC record ───────────────────────────────────────────────────────────


def _submissions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cols = {k: [] for k in ("accessionNumber", "filingDate", "form", "items", "primaryDocument",
                            "acceptanceDateTime")}
    for r in rows:
        for k in cols:
            cols[k].append(r.get(k, ""))
    return {"filings": {"recent": cols}}


_8K = {"accessionNumber": ACC, "filingDate": "2026-10-29", "form": "8-K", "items": "2.02,9.01",
       "primaryDocument": "intc-20261029.htm", "acceptanceDateTime": "2026-10-29T16:02:11.000Z"}


def test_an_item_202_8k_on_the_report_date_is_found_with_its_et_acceptance_time():
    (f,) = efs.find_results_filings(_submissions([_8K]), REPORT)
    assert f.accession == ACC
    assert f.accepted_at.astimezone(ET).strftime("%H:%M") == "16:02"


def test_a_late_evening_filing_dated_the_next_business_day_still_counts():
    late = dict(_8K, filingDate="2026-10-30")
    assert efs.find_results_filings(_submissions([late]), REPORT)


@pytest.mark.parametrize("change", [
    {"form": "10-Q"}, {"items": "7.01,9.01"}, {"filingDate": "2026-10-27"}, {"form": "8-K/A"},
])
def test_other_filings_are_not_results(change):
    assert efs.find_results_filings(_submissions([dict(_8K, **change)]), REPORT) == []


def test_columns_of_different_lengths_read_as_no_filing():
    data = _submissions([_8K])
    data["filings"]["recent"]["items"].append("2.02")
    assert efs.find_results_filings(data, REPORT) == []


_INDEX = """<table class="tableFile">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/50863/000005086326000123/intc-20261029.htm">intc-20261029.htm</a></td><td>8-K</td><td>30 KB</td></tr>
<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/50863/000005086326000123/q3-26earnings.htm">q3-26earnings.htm</a></td><td>EX-99.1</td><td>90 KB</td></tr>
</table>"""


def test_the_ex991_document_is_located_from_the_index_page():
    assert efs.find_ex99_document(_INDEX) == "q3-26earnings.htm"
    assert efs.find_ex99_document("<html>no table</html>") is None


@pytest.mark.parametrize("hm,expected", [
    ((5, 59), None), ((6, 0), 60), ((9, 34), 60), ((12, 0), 300), ((16, 5), 60),
    ((18, 30), 300), ((22, 0), None),
])
def test_the_poll_cadence_follows_the_release_windows(hm, expected):
    now = datetime(2026, 10, 29, *hm, tzinfo=ET)
    assert efs.poll_interval_seconds(now) == expected


# ── the watcher ──────────────────────────────────────────────────────────────


class _SEC:
    def __init__(self, *, filings: Optional[Dict[str, List[dict]]] = None,
                 throttle_on: Optional[str] = None):
        self.filings = filings or {}
        self.throttle_on = throttle_on
        self.calls: List[str] = []

    async def get_submissions(self, cik):
        self.calls.append(f"sub:{cik}")
        if self.throttle_on == cik:
            raise SECRateLimitException("429", status_code=429, retry_after=120.0)
        return _submissions(self.filings.get(cik, []))

    async def get_filing_document(self, cik, acc, document):
        self.calls.append(f"doc:{document}")
        if document.endswith("-index.htm"):
            return _INDEX
        return f"<html><body><p>{RELEASE}</p></body></html>"


class _Gemini:
    def __init__(self, answer=None, fail: int = 0):
        self.answer = answer if answer is not None else _good_answer()
        self.fail = fail
        self.calls = 0
        self.kwargs: Dict[str, Any] = {}

    async def generate_json(self, prompt, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("gemini 503")
        return {"text": json.dumps(self.answer), "model": "m", "tokens_used": 10}


async def _no_sleep(_s):
    return None


@pytest.fixture
def watcher(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex test@example.com")
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", True)
    w = efs.EarningsFilingWatcher()
    return w


_CIKS = {"INTC": "0000050863", "AAPL": "0000320193", "MSFT": "0000789019"}
_AT_16 = datetime(2026, 10, 29, 16, 3, tzinfo=ET).astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_a_filing_is_found_extracted_once_and_then_never_polled_again(watcher):
    sec = _SEC(filings={"0000050863": [_8K]})
    gem = _Gemini()
    found = await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem,
                                   cik_map=_CIKS, sleep=_no_sleep)
    assert found["INTC"].revenue == 17.1e9 and found["INTC"].eps_adjusted == 0.44
    assert gem.calls == 1
    assert gem.kwargs["temperature"] == 0 and gem.kwargs["cache"] is False
    later = datetime(2026, 10, 29, 16, 30, tzinfo=ET).astimezone(timezone.utc)
    again = await watcher.run_once(later, ["INTC"], REPORT, sec=sec, gemini=gem,
                                   cik_map=_CIKS, sleep=_no_sleep)
    assert again == {} and gem.calls == 1
    assert sec.calls.count("sub:0000050863") == 1, "found → stop polling"


@pytest.mark.asyncio
async def test_no_filing_yet_is_checked_again_only_after_the_window_cadence(watcher):
    sec = _SEC()
    await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == ["sub:0000050863"], "inside the 60 s cadence nothing is re-read"


@pytest.mark.asyncio
async def test_the_daily_cap_keeps_the_most_watched_first(watcher, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_SEC_MAX_SYMBOLS", 2)
    sec = _SEC()
    await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                           sleep=_no_sleep)
    assert sec.calls == ["sub:0000320193", "sub:0000789019"]


@pytest.mark.asyncio
async def test_a_throttle_parks_every_symbol_and_stops_the_round(watcher):
    sec = _SEC(throttle_on="0000320193")
    await watcher.run_once(_AT_16, ["AAPL", "MSFT"], REPORT, sec=sec, cik_map=_CIKS,
                           sleep=_no_sleep)
    assert sec.calls == ["sub:0000320193"], "MSFT is never asked after the throttle"
    later = datetime(2026, 10, 29, 16, 5, tzinfo=ET).astimezone(timezone.utc)
    assert await watcher.run_once(later, ["MSFT"], REPORT, sec=sec, cik_map=_CIKS,
                                  sleep=_no_sleep) == {}
    assert sec.calls == ["sub:0000320193"], "parked for SEC's Retry-After"


@pytest.mark.asyncio
async def test_our_calls_are_spaced_never_burst(watcher):
    slept = []

    async def _sleep(s):
        slept.append(s)

    sec = _SEC()
    await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                           sleep=_sleep)
    assert slept == [efs._CALL_SPACING_SECONDS] * 2


@pytest.mark.asyncio
async def test_a_gemini_outage_is_retried_but_a_verification_miss_is_final(watcher):
    sec = _SEC(filings={"0000050863": [_8K]})
    gem = _Gemini(fail=1)
    assert await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem,
                                  cik_map=_CIKS, sleep=_no_sleep) == {}
    later = datetime(2026, 10, 29, 16, 5, tzinfo=ET).astimezone(timezone.utc)
    watcher._watch[("INTC", REPORT)].checked_mono = 0.0      # the 60 s cadence has passed
    found = await watcher.run_once(later, ["INTC"], REPORT, sec=sec, gemini=gem,
                                   cik_map=_CIKS, sleep=_no_sleep)
    assert found["INTC"].eps_adjusted == 0.44 and gem.calls == 2

    w2 = efs.EarningsFilingWatcher()
    bad = _Gemini(answer={"is_quarterly_results": False})
    sec2 = _SEC(filings={"0000050863": [_8K]})
    assert await w2.run_once(_AT_16, ["INTC"], REPORT, sec=sec2, gemini=bad, cik_map=_CIKS,
                             sleep=_no_sleep) == {}
    w2._watch[("INTC", REPORT)].checked_mono = 0.0
    assert await w2.run_once(later, ["INTC"], REPORT, sec=sec2, gemini=bad, cik_map=_CIKS,
                             sleep=_no_sleep) == {}
    assert bad.calls == 1, "a verification outcome is final for that accession"


@pytest.mark.asyncio
async def test_a_ticker_sec_does_not_list_falls_back_to_fmp_without_a_call(watcher):
    sec = _SEC()
    await watcher.run_once(_AT_16, ["ASML"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == []


@pytest.mark.asyncio
async def test_the_watcher_is_inert_without_a_user_agent(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")
    sec = _SEC()
    w = efs.EarningsFilingWatcher()
    assert await w.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS,
                            sleep=_no_sleep) == {}
    assert sec.calls == []


@pytest.mark.asyncio
async def test_the_watcher_is_off_with_its_flag(watcher, monkeypatch):
    monkeypatch.setattr(settings, "EARNINGS_LIVE_FILINGS_ENABLED", False)
    sec = _SEC()
    assert await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, cik_map=_CIKS,
                                  sleep=_no_sleep) == {}
    assert sec.calls == []


@pytest.mark.asyncio
async def test_nothing_is_read_outside_edgar_hours(watcher):
    sec = _SEC()
    night = datetime(2026, 10, 29, 23, 0, tzinfo=ET).astimezone(timezone.utc)
    await watcher.run_once(night, ["INTC"], REPORT, sec=sec, cik_map=_CIKS, sleep=_no_sleep)
    assert sec.calls == []


# ── the round's timing ───────────────────────────────────────────────────────


class _Clock:
    """A `time` stand-in for the watcher alone: asyncio keeps its own clock."""

    def __init__(self):
        self.at = 1_000_000.0

    def monotonic(self) -> float:
        return self.at


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    held = _Clock()
    monkeypatch.setattr(efs, "time", SimpleNamespace(monotonic=held.monotonic))
    return held


def _later_filing(cik: str) -> dict:
    return dict(_8K, accessionNumber=f"{cik}-26-000001")


@pytest.mark.asyncio
async def test_each_figure_is_delivered_the_moment_it_verifies(watcher):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    delivered: List[str] = []
    seen: List[List[str]] = []

    class _Gem(_Gemini):
        async def generate_json(self, prompt, **kwargs):
            if self.calls == 1:
                seen.append(list(delivered))
            return await super().generate_json(prompt, **kwargs)

    found = await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Gem(),
                                   cik_map=_CIKS, sleep=_no_sleep,
                                   on_figures=lambda sym, fig: delivered.append(sym))
    assert seen == [["INTC"]], "the second read starts with the first figure already out"
    assert delivered == ["INTC", "AAPL"]
    assert set(found) == {"INTC", "AAPL"}, "the round still reports what it found"


@pytest.mark.asyncio
async def test_a_delivery_that_raises_stays_inside_the_round_and_says_which_report(watcher,
                                                                                  caplog):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    gem = _Gemini()
    calls: List[str] = []

    def _raises(symbol, figures):
        calls.append(symbol)
        raise ValueError("record refused")

    with caplog.at_level(logging.WARNING, logger=efs.__name__):
        found = await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                                       cik_map=_CIKS, sleep=_no_sleep, on_figures=_raises)
    assert calls == ["INTC", "AAPL"], "one bad delivery must not cost the next symbol its read"
    assert set(found) == {"INTC", "AAPL"} and gem.calls == 2
    rows = [r for r in caplog.records if "not delivered" in r.getMessage()]
    assert len(rows) == 2 and rows[0].exc_info is not None
    assert "INTC" in rows[0].getMessage() and REPORT.isoformat() in rows[0].getMessage()
    assert "ValueError" in rows[0].getMessage() and "record refused" in rows[0].getMessage()


@pytest.mark.asyncio
async def test_a_failed_read_delivers_nothing_and_a_later_one_delivers_once(watcher):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    gem = _Gemini(fail=1)
    delivered: List[str] = []
    assert await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                                  sleep=_no_sleep,
                                  on_figures=lambda sym, fig: delivered.append(sym)) == {}
    assert delivered == []
    watcher._watch[("INTC", REPORT)].checked_mono = 0.0      # the 60 s cadence has passed
    for _ in range(2):
        await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                               sleep=_no_sleep,
                               on_figures=lambda sym, fig: delivered.append(sym))
    assert delivered == ["INTC"]


@pytest.mark.asyncio
async def test_a_refused_release_delivers_nothing(watcher):
    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    delivered: List[str] = []
    await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec,
                           gemini=_Gemini(answer={"is_quarterly_results": False}), cik_map=_CIKS,
                           sleep=_no_sleep, on_figures=lambda sym, fig: delivered.append(sym))
    assert delivered == []


@pytest.mark.asyncio
async def test_a_second_ticker_of_one_filing_is_delivered_without_a_second_extraction(watcher):
    """GOOGL and GOOG are one issuer, so one accession: the second ticker's figures come from
    the watcher's own memory, and must still reach its record."""
    ciks = {"GOOGL": "0001652044", "GOOG": "0001652044"}
    sec = _SEC(filings={"0001652044": [_8K]})
    gem = _Gemini()
    delivered: List[str] = []
    await watcher.run_once(_AT_16, ["GOOGL", "GOOG"], REPORT, sec=sec, gemini=gem, cik_map=ciks,
                           sleep=_no_sleep, on_figures=lambda sym, fig: delivered.append(sym))
    assert delivered == ["GOOGL", "GOOG"] and gem.calls == 1


@pytest.mark.asyncio
async def test_every_symbol_in_a_round_shares_the_round_start_stamp(watcher, clock):
    async def _spaced(seconds):
        clock.at += seconds

    await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=_SEC(), cik_map=_CIKS,
                           sleep=_spaced)
    assert {w.checked_mono for w in watcher._watch.values()} == {1_000_000.0}


@pytest.mark.asyncio
async def test_a_quiet_round_reads_every_symbol_again_a_minute_after_it_started(watcher, clock):
    """Rounds start a minute apart while the reads inside one are spaced out. A stamp per read
    left all but the first symbol a few seconds short at the next round, so each was read
    every other minute."""
    async def _spaced(seconds):
        clock.at += seconds

    sec = _SEC()
    for _ in range(3):
        started = clock.at
        await watcher.run_once(_AT_16, ["AAPL", "MSFT", "INTC"], REPORT, sec=sec, cik_map=_CIKS,
                               sleep=_spaced)
        clock.at = started + 60
    assert [sec.calls.count(f"sub:{cik}") for cik in _CIKS.values()] == [3, 3, 3]


@pytest.mark.parametrize("hm,cadence", [((16, 3), 60), ((12, 0), 300)], ids=["fast", "slow"])
def test_a_round_a_moment_early_still_reads_the_symbol(watcher, hm, cadence):
    """A round that wakes a few seconds short must not cost the symbol a whole cadence."""
    now_et = datetime(2026, 10, 29, *hm, tzinfo=ET)
    key = ("INTC", REPORT)
    watcher._watch[key] = efs._Watch(checked_mono=1000.0)
    on_time = 1000.0 + cadence - efs._DUE_SLACK_SECONDS
    assert watcher._due(key, now_et, on_time)
    assert not watcher._due(key, now_et, on_time - 0.001)


@pytest.mark.asyncio
async def test_a_symbol_a_throttle_left_unread_keeps_its_place(watcher, clock):
    watcher._watch[("MSFT", REPORT)] = efs._Watch(checked_mono=999_000.0)
    await watcher.run_once(_AT_16, ["AAPL", "MSFT"], REPORT, sec=_SEC(throttle_on=_CIKS["AAPL"]),
                           cik_map=_CIKS, sleep=_no_sleep)
    assert watcher._watch[("MSFT", REPORT)].checked_mono == 999_000.0, \
        "it was never read, so the park must not count as a read"


@pytest.mark.asyncio
async def test_a_failed_read_waits_its_cadence_from_where_it_ended(watcher, clock):
    """A stalled model ends a round minutes late and the next one starts a second after. Its
    attempts must go no faster for that: a filing out of attempts is never read again."""
    class _Stalls(_Gemini):
        async def generate_json(self, prompt, **kwargs):
            clock.at += 90
            return await super().generate_json(prompt, **kwargs)

    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    gem = _Stalls(fail=9)

    async def _round() -> None:
        await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                               sleep=_no_sleep)

    await _round()
    clock.at += 1
    await _round()
    assert gem.calls == 1, "a second after the stall ended is no new attempt"
    clock.at += 60
    await _round()
    assert gem.calls == 2


@pytest.mark.asyncio
async def test_a_stalled_read_never_delays_the_symbols_behind_it_past_their_own_cadence(watcher,
                                                                                       clock):
    """Only the failed read moves its stamp: its neighbours stay on the round's."""
    class _Stalls(_Gemini):
        async def generate_json(self, prompt, **kwargs):
            clock.at += 90
            return await super().generate_json(prompt, **kwargs)

    sec = _SEC(filings={_CIKS["INTC"]: [_8K]})
    started = clock.at
    await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Stalls(fail=9),
                           cik_map=_CIKS, sleep=_no_sleep)
    assert watcher._watch[("AAPL", REPORT)].checked_mono == started
    assert watcher._watch[("INTC", REPORT)].checked_mono == clock.at


class _Model(_Gemini):
    """Takes 90 s on the stand-in clock and fails until the clock reaches `until`, then answers.
    `started` keeps every call as (symbol, when it began)."""

    def __init__(self, clock: _Clock, *, until: float = float("inf")):
        super().__init__()
        self.clock, self.until = clock, until
        self.started: List[Tuple[str, float]] = []

    async def generate_json(self, prompt, **kwargs):
        self.started.append((prompt.split("ticker: ")[1].split(".")[0], self.clock.at))
        if self.clock.at < self.until:
            self.clock.at += 90
            raise RuntimeError("gemini timed out")
        return await super().generate_json(prompt, **kwargs)

    def of(self, symbol: str) -> List[float]:
        return [at for sym, at in self.started if sym == symbol]


def _both_filed() -> Dict[str, List[dict]]:
    return {_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]}


@pytest.mark.asyncio
async def test_every_failed_read_of_a_round_waits_its_cadence_from_the_rounds_end(watcher, clock):
    """Two filings stall one after the other, so the first ended 90 s before the round did and
    the next round starts a second behind it. Its own end would leave that first filing due at
    once, and a filing out of attempts is never read again."""
    sec, gem = _SEC(filings=_both_filed()), _Model(clock)

    async def _round() -> None:
        await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                               cik_map=_CIKS, sleep=_no_sleep)

    await _round()
    ended = clock.at
    assert gem.of("INTC") == [1_000_000.0] and gem.of("AAPL") == [1_000_090.0]
    assert {w.checked_mono for w in watcher._watch.values()} == {ended}

    clock.at = ended + 1
    await _round()
    assert len(gem.started) == 2, "nothing starts right behind the stall"
    clock.at = ended + 60 - efs._DUE_SLACK_SECONDS - 0.001
    await _round()
    assert len(gem.started) == 2, "a moment short of the cadence and its slack"
    clock.at = ended + 60
    await _round()
    assert [sym for sym, _ in gem.started[2:]] == ["INTC", "AAPL"]


@pytest.mark.asyncio
async def test_a_filing_that_failed_earlier_rests_after_a_later_round_that_spent_an_attempt(
        watcher, clock):
    """Filings rarely land together. Counted from their own rounds, two that landed apart take
    turns, each round right behind the other, and neither ever gets a minute of rest."""
    sec, gem = _SEC(filings={_CIKS["INTC"]: [_8K]}), _Model(clock)

    async def _round() -> None:
        await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                               cik_map=_CIKS, sleep=_no_sleep)

    await _round()
    clock.at += 1
    sec.filings[_CIKS["AAPL"]] = [_later_filing(_CIKS["AAPL"])]
    await _round()
    ended = clock.at
    assert [sym for sym, _ in gem.started] == ["INTC", "AAPL"]
    clock.at = ended + 1
    await _round()
    assert len(gem.started) == 2, "INTC failed 92 s ago, but the model failed 1 s ago"
    clock.at = ended + 60
    await _round()
    assert [sym for sym, _ in gem.started[2:]] == ["INTC", "AAPL"]


@pytest.mark.asyncio
async def test_a_round_that_spent_no_attempt_holds_no_retry_back(watcher, clock):
    """The rest is for a failing model. Once it answers, a waiting filing stays on its time."""
    sec, gem = _SEC(filings={_CIKS["INTC"]: [_8K]}), _Model(clock)

    async def _round() -> Dict[str, efs.FilingFigures]:
        return await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=gem,
                                      cik_map=_CIKS, sleep=_no_sleep)

    await _round()
    failed = clock.at
    gem.until = clock.at
    clock.at += 1
    sec.filings[_CIKS["AAPL"]] = [_later_filing(_CIKS["AAPL"])]
    assert set(await _round()) == {"AAPL"}
    assert watcher._watch[("INTC", REPORT)].checked_mono == failed
    clock.at = failed + 60
    assert set(await _round()) == {"INTC"}


@pytest.mark.asyncio
async def test_a_read_that_spent_no_attempt_keeps_the_rounds_start(watcher, clock):
    """Only what spent an attempt moves: the round's quiet symbols stay on the minute, and a
    filing that verified is done either way."""
    class _SecDown(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["MSFT"]:
                raise SECException("SEC 503")
            return await super().get_submissions(cik)

    started = clock.at
    sec = _SecDown(filings={_CIKS["INTC"]: [_8K]})
    await watcher.run_once(_AT_16, ["INTC", "AAPL", "MSFT"], REPORT, sec=sec, gemini=_Model(clock),
                           cik_map=_CIKS, sleep=_no_sleep)
    stamps = {key[0]: w.checked_mono for key, w in watcher._watch.items()}
    assert stamps == {"INTC": started + 90, "AAPL": started, "MSFT": started}


@pytest.mark.asyncio
async def test_a_throttle_that_ends_a_round_early_still_moves_its_failed_reads(watcher, clock):
    """The park can be shorter than the cadence, and the last stall still counts."""
    class _ThrottledLate(_SEC):
        async def get_submissions(self, cik):
            if cik == self.throttle_on:
                clock.at += 30
            return await super().get_submissions(cik)

    sec = _ThrottledLate(filings={_CIKS["INTC"]: [_8K]}, throttle_on=_CIKS["AAPL"])
    await watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Model(clock),
                           cik_map=_CIKS, sleep=_no_sleep)
    assert watcher._watch[("INTC", REPORT)].checked_mono == clock.at


@pytest.mark.asyncio
async def test_a_round_cut_off_keeps_a_failed_read_on_its_own_end(watcher, clock):
    """The round's end never comes, and a cancel from outside leaves the loop running: without
    its own stamp the failed read would count from the round's start and go straight again."""
    reached = asyncio.Event()

    class _Hangs(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["AAPL"]:
                reached.set()
                await asyncio.Event().wait()
            return await super().get_submissions(cik)

    sec = _Hangs(filings={_CIKS["INTC"]: [_8K]})
    round_ = asyncio.create_task(
        watcher.run_once(_AT_16, ["INTC", "AAPL"], REPORT, sec=sec, gemini=_Model(clock),
                         cik_map=_CIKS, sleep=_no_sleep))
    await asyncio.wait_for(reached.wait(), 2.0)
    round_.cancel()
    with pytest.raises(asyncio.CancelledError):
        await round_
    assert watcher._watch[("INTC", REPORT)].checked_mono == 1_000_090.0


@pytest.mark.asyncio
async def test_a_filing_out_of_attempts_is_never_read_again(watcher, clock):
    sec, gem = _SEC(filings={_CIKS["INTC"]: [_8K]}), _Model(clock)
    for _ in range(5):
        await watcher.run_once(_AT_16, ["INTC"], REPORT, sec=sec, gemini=gem, cik_map=_CIKS,
                               sleep=_no_sleep)
        clock.at += 120
    assert len(gem.started) == efs._MAX_EXTRACT_ATTEMPTS
    assert watcher.figures_for("INTC", REPORT) is None


# ── the filing tick, on the real watcher ─────────────────────────────────────


def _pending(symbol: str) -> dict:
    return {"symbol": symbol, "date": REPORT.isoformat(), "epsActual": None, "epsEstimated": 0.41,
            "revenueActual": None, "revenueEstimated": 16.6e9,
            "lastUpdated": REPORT.isoformat()}


@pytest.fixture
def live(watcher, monkeypatch):
    """The real live service over the real watcher, with SEC and Gemini faked."""
    svc = els.get_earnings_live_service()
    svc.reset()
    monkeypatch.setattr(efs, "get_earnings_filing_watcher", lambda: watcher)
    monkeypatch.setattr(efs, "_CALL_SPACING_SECONDS", 0)

    async def _map(*, sec=None):
        return _CIKS

    # `run_once` imports this inside the function, so patch the SOURCE module.
    monkeypatch.setattr(signals, "ticker_cik_map", _map)
    yield svc
    svc.reset()


@pytest.mark.asyncio
async def test_a_figure_is_on_the_record_before_the_next_symbol_is_read(live):
    """The owner's rule: what comes out first is shown right away, not when the round ends."""
    live.observe_rows(REPORT, [_pending("INTC"), _pending("AAPL")], _AT_16)
    shown: List[Optional[float]] = []

    class _Reads(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["AAPL"]:
                shown.append(live.snapshot("INTC", REPORT, _AT_16).revenue.actual)
            return await super().get_submissions(cik)

    sec = _Reads(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    stats = await els.earnings_filing_tick(_AT_16, watched=["INTC", "AAPL"], sec=sec,
                                           gemini=_Gemini())
    assert shown == [17.1e9]
    assert stats["filings"] == 2
    assert live.snapshot("AAPL", REPORT, _AT_16).revenue.actual == 17.1e9


@pytest.mark.asyncio
async def test_a_round_cut_off_at_shutdown_keeps_the_figures_it_already_verified(live):
    """Cancellation lands mid-round on a redeploy: what was read must not go with it."""
    live.observe_rows(REPORT, [_pending("INTC"), _pending("AAPL")], _AT_16)
    reached = asyncio.Event()

    class _Hangs(_SEC):
        async def get_submissions(self, cik):
            if cik == _CIKS["AAPL"]:
                reached.set()
                await asyncio.Event().wait()
            return await super().get_submissions(cik)

    sec = _Hangs(filings={_CIKS["INTC"]: [_8K]})
    tick = asyncio.create_task(
        els.earnings_filing_tick(_AT_16, watched=["INTC", "AAPL"], sec=sec, gemini=_Gemini()))
    await asyncio.wait_for(reached.wait(), 2.0)
    tick.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tick
    assert live.snapshot("INTC", REPORT, _AT_16).revenue.actual == 17.1e9


@pytest.mark.asyncio
async def test_a_figure_the_round_could_not_store_is_logged_and_the_round_goes_on(live, caplog,
                                                                                 monkeypatch):
    live.observe_rows(REPORT, [_pending("INTC"), _pending("AAPL")], _AT_16)
    real = live.absorb_filing

    def _absorb(symbol, day, figures, now):
        if symbol == "INTC":
            raise RuntimeError("record refused")
        real(symbol, day, figures, now)

    monkeypatch.setattr(live, "absorb_filing", _absorb)
    sec = _SEC(filings={_CIKS["INTC"]: [_8K], _CIKS["AAPL"]: [_later_filing(_CIKS["AAPL"])]})
    with caplog.at_level(logging.WARNING, logger=efs.__name__):
        await els.earnings_filing_tick(_AT_16, watched=["INTC", "AAPL"], sec=sec,
                                       gemini=_Gemini())
    assert any("not delivered" in r.getMessage() and "INTC" in r.getMessage()
               for r in caplog.records)
    assert live.snapshot("AAPL", REPORT, _AT_16).revenue.actual == 17.1e9
    assert live.snapshot("INTC", REPORT, _AT_16).revenue.actual is None


# ── a stalled model on the real loop ─────────────────────────────────────────


class _StopLoop(BaseException):
    """Ends `_forever`, which outlives every `Exception`."""


async def _filing_loop_runs(monkeypatch, clock: _Clock, seconds: float, *, symbols: List[str],
                            sec: _SEC, gemini: _Model) -> List[float]:
    """The real `_forever` over the real filing tick, on the stand-in clock, for `seconds`.
    Returns when each round that called the model ended."""
    monkeypatch.setattr(els, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(settings, "EARNINGS_LIVE_ENABLED", True)
    stop_at = clock.at + seconds
    ended: List[float] = []

    async def earnings_filing_tick(_now):
        called = len(gemini.started)
        await els.earnings_filing_tick(_AT_16, watched=symbols, sec=sec, gemini=gemini)
        if len(gemini.started) > called:
            ended.append(clock.at)

    async def _rest(wait):
        clock.at += wait
        if clock.at >= stop_at:
            raise _StopLoop

    with pytest.raises(_StopLoop):
        await els._forever(earnings_filing_tick, 60, sleep=_rest)
    return ended


class _FiledOnTheClock(_SEC):
    """A filing is on the record from its own moment: `landing` maps a symbol to seconds after
    the start."""

    def __init__(self, clock: _Clock, landing: Dict[str, float]):
        super().__init__()
        self.clock, self.start = clock, clock.at
        self.landing = {_CIKS[symbol]: at for symbol, at in landing.items()}

    async def get_submissions(self, cik):
        if self.clock.at - self.start >= self.landing.get(cik, float("inf")):
            self.filings[cik] = [_later_filing(cik)]
        return await super().get_submissions(cik)


@pytest.mark.asyncio
@pytest.mark.parametrize("landing", [
    {"INTC": 0, "AAPL": 0, "MSFT": 0},
    {"INTC": 0, "AAPL": 100, "MSFT": 0},
    {"INTC": 0, "AAPL": 100, "MSFT": 200},
    {"INTC": 0, "AAPL": 300, "MSFT": 550},
], ids=["together", "two_then_one", "a_round_apart", "minutes_apart"])
async def test_a_retry_never_starts_within_a_minute_of_a_round_that_spent_an_attempt(
        live, clock, monkeypatch, landing):
    """The loop starts the next round a second behind a long one, so the rest between attempts
    has to come from the stamps, for every filing and however they landed."""
    live.observe_rows(REPORT, [_pending(s) for s in landing], _AT_16)
    gem = _Model(clock)
    ended = await _filing_loop_runs(monkeypatch, clock, 3000, symbols=list(landing),
                                    sec=_FiledOnTheClock(clock, landing), gemini=gem)
    for symbol in landing:
        reads = gem.of(symbol)
        assert len(reads) == efs._MAX_EXTRACT_ATTEMPTS, symbol
        for retry in reads[1:]:
            rests = [retry - at for at in ended if at <= retry]
            assert min(rests) >= 60, (symbol, reads, ended)


@pytest.mark.asyncio
@pytest.mark.parametrize("stall,landing", [
    (480, {"INTC": 0, "AAPL": 0}),
    (660, {"INTC": 0, "AAPL": 0, "MSFT": 0}),
    (480, {"AAPL": 50, "INTC": 0}),
], ids=["two_filings", "three_filings", "two_filings_in_rounds_of_their_own"])
async def test_a_stall_the_filings_can_outlast_loses_none(live, clock, monkeypatch, stall,
                                                          landing):
    """Each attempt spent early is a filing lost to a stall that ends a little later, and the
    first filing is the most-watched."""
    live.observe_rows(REPORT, [_pending(s) for s in landing], _AT_16)
    gem = _Model(clock, until=clock.at + stall)
    await _filing_loop_runs(monkeypatch, clock, 1500, symbols=list(landing),
                            sec=_FiledOnTheClock(clock, landing), gemini=gem)
    for symbol in landing:
        assert live.snapshot(symbol, REPORT, _AT_16).revenue.actual == 17.1e9, symbol
