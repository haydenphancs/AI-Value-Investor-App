"""What real earnings releases print, read through `verify_extraction`.

Every string here is one row or one sentence of a public SEC filing (EX-99.1, third quarter 2026),
as `html_to_text` leaves it: the shapes a model really quotes, which no hand-written sentence
predicted. A figure must keep passing on these, and the row beside each refusal is the mistake
the verifier exists to stop: XOM's release has no revenue sentence, and the model filled the slot
with its earnings. Hermetic: no release, no model answer and no network.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

import app.services.earnings_filing_service as efs

ACC = "0000000000-26-000001"
#: What each row sits in: the release prints dollars somewhere, as every real one does.
_AROUND = "Acme reported results of $1 per unit sold."


def _verify(text: str, **answer):
    full = {"is_quarterly_results": True, "currency": "USD", **answer}
    return efs.verify_extraction(full, text, accession=ACC, accepted_at=None, url="u")


def _in_a_release(quote: str, header: Optional[str] = None) -> str:
    return " ".join(part for part in (_AROUND, header, quote) if part)


def _revenue(quote: str, value: Any, unit: str, header: Optional[str] = None):
    return _verify(_in_a_release(quote, header),
                   revenue={"value": value, "unit": unit, "quote": quote})


def _eps(field: str, quote: str, value: Any):
    return _verify(_in_a_release(quote), **{field: {"value": value, "quote": quote}})


# ── revenue ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("quote,value,unit,expected", [
    ("The Company posted quarterly revenue of $114.5 billion, up 12 percent year over year.",
     114.5, "billion", 114.5e9),                                                    # AAPL
    ("Net sales increased 12% to $201.9 billion in the third quarter, compared with $180.2 "
     "billion in third quarter 2025.", 201.9, "billion", 201.9e9),                  # AMZN
    ("The Boeing Company [NYSE: BA] recorded third quarter revenue of $24.2 billion, primarily "
     "reflecting higher defense and services volume and less favorable commercial delivery mix.",
     24.188, "billion", 24.2e9),                                                    # BA
    ("3Q26 Revenue Up 3% YoY to $30.0 Billion", 30, "billion", 30e9),               # BAC
    ("● Third-quarter 2026 sales and revenues increased 17% to $20.7 billion",
     20.7, "billion", 20.7e9),                                                      # CAT
    ("Total revenue reached $692.5 million, with Marketplace and Services revenue both "
     "contributing to growth, increasing 9.1% and 15.0% respectively.",
     692.5, "million", 692.5e6),                                                    # ETSY
    ("•Total revenue (GAAP) of $14.1B, +16%; adjusted revenue* $13.3B, +18%;",
     14.1, "billion", 14.1e9),                                                      # GE
    ("Consolidated Alphabet revenues increased 25% to $127.6 billion, reflecting strong "
     "performance across the business and our 13th consecutive quarter of double-digit revenue "
     "growth.", 127.6, "billion", 127.6e9),                                         # GOOGL
    ("The Goldman Sachs Group, Inc. (NYSE: GS) today reported net revenues of $18.68 billion and "
     "net earnings of $5.29 billion for the third quarter ended September 30, 2026.",
     18.68, "billion", 18.68e9),                                                    # GS
    ("▪Third-quarter revenue was $16.5 billion, up 21% year-over-year (YoY).",
     16.5, "billion", 16.5e9),                                                      # INTC
    ("•2026 Third-Quarter reported sales growth of 5.9% to $25.4 Billion with operational "
     "growth of 6.0%* and adjusted operational growth of 6.1%*", 25.4, "billion", 25.4e9),  # JNJ
    ("n Reported revenue of $50.2 billion and managed revenue2 of $50.9 billion",
     50.2, "billion", 50.2e9),                                                      # JPM
    ("Net revenues grew 7% to $13.4 billion, and organic revenues (non-GAAP) grew 7%, driven by "
     "a 7% increase in concentrate sales and 1% growth in price/mix.",
     13.4, "billion", 13.4e9),                                                      # KO
    ("•Revenue – Revenue was $64.60 billion, an increase of 26% year-over-year on both "
     "a reported and constant currency basis.", 64.6, "billion", 64.6e9),           # META
    ("Revenue was $92.3 billion and increased 19%", 92.3, "billion", 92.3e9),       # MSFT
    ("■Third-Quarter 2026 Revenues of $16.6 Billion, Relatively Flat Year-over-Year "
     "Operationally", 16.6, "billion", 16.6e9),                                     # PFE
    ("The Procter & Gamble Company (NYSE:PG) reported first quarter fiscal year 2027 net sales "
     "of $23.3 billion, an increase of four percent versus the prior year.",
     23.3, "billion", 23.3e9),                                                      # PG
    ("•Generated $2.155 billion in revenue, a 38 percent increase year-over-year",
     2.155, "billion", 2.155e9),                                                    # RIVN
    ("•Total net revenue was $1.5 billion, up 24% YoY", 1.5, "billion", 1.5e9),  # ROKU
    ("Q4 Consolidated Net Revenues Down 4% to $9.1 billion, Reflecting the Starbucks China "
     "Transaction", 9.1, "billion", 9.1e9),                                         # SBUX
    ("▪Total revenue of $423.3 million, up 15.2% versus 2025, including $408.0 million of "
     "Shack sales and $15.3 million of Licensing revenue.", 423.3, "million", 423.3e6),  # SHAK
    ("Third quarter revenue increased 14% year-over-year to $1,722 million",
     1722, "million", 1.722e9),                                                     # SNAP
    ("• Third Quarter 2026 Revenues of $112.7 Billion; Earnings from Operations of $6.6 "
     "Billion", 112.7, "billion", 112.7e9),                                         # UNH
])
def test_a_revenue_sentence_keeps_passing(quote, value, unit, expected):
    figures, reasons = _revenue(quote, value, unit)
    assert figures is not None and figures.revenue == expected, reasons
    assert reasons["revenue"] == "ok"


@pytest.mark.parametrize("header,row,value,expected", [
    ("Dollars in millions, except per share data", "Revenues $ 7,157 $ 7,078 1 % 1 %",
     7157, 7.157e9),                                                                # MCD
    ("Summary Financials ($ in millions) Q3 25 Q4 25 Q1 26 Q2 26 Q3 26 Y/Y",
     "Total net revenue 1,210.6 1,394.9 1,248.9 1,354.7 1,502.0 24 %", 1502.0, 1.502e9),  # ROKU
    ("(dollars in millions, except per share data):",
     "Total revenue $ 1,621.6 $ 1,470.6 $ 4,718.0 $ 4,261.4", 1621.6, 1.6216e9),    # O
    ("(dollars in millions) Q2 FY26 Q1 FY27 Low High Y/Y Q/Q",
     "Total Revenue $656.5 $559.2 $625.0 $650.0 (3)% 14%", 559.2, 559.2e6),         # PTON
    ("($ in millions, except percentages and per share data)",
     "Total revenues 28,095 24,901 22,387 28,236 28,046", 28046, 28.046e9),         # TSLA
    ("dollars in millions, except per share/unit data",
     "Total revenues 2,388 2,214 7,111 6,537", 2388, 2.388e9),                      # PLD
])
def test_a_table_row_takes_the_one_header_its_release_prints(header, row, value, expected):
    figures, reasons = _revenue(row, value, "million", header)
    assert figures is not None and figures.revenue == expected, reasons
    assert reasons["revenue"] == "ok"


@pytest.mark.parametrize("headers,row,value", [
    # O's and ROKU's whole releases: a summary in millions, the statements in thousands.
    ("(dollars in millions, except per share data): (in thousands, except per share amounts)",
     "Total revenue $ 1,621.6 $ 1,470.6 $ 4,718.0 $ 4,261.4", 1621.6),
    ("($ in millions) Q3 25 (in thousands, except per share data)",
     "Total net revenue 1,210.6 1,394.9 1,248.9 1,354.7 1,502.0 24 %", 1502.0),
])
def test_a_row_of_a_release_printing_two_scales_waits_for_fmp(headers, row, value):
    figures, reasons = _revenue(row, value, "million", headers)
    assert figures is None and reasons["revenue"] == "value_mismatch"


def test_the_revenue_slot_filled_with_earnings_is_refused():
    """XOM prints no revenue, and a wrong one passes the consensus band whenever a company earns
    more than half of what it sells."""
    quote = ("ExxonMobil Holdings Corporation today announced third-quarter 2026 earnings of $15.4 "
             "billion, or $3.72 per share.")
    figures, reasons = _revenue(quote, 15.4, "billion")
    assert figures is None and reasons["revenue"] == "wrong_metric"


@pytest.mark.parametrize("quote,value", [
    ("Third-quarter revenue of $17.1 billion, net income of $1.2 billion", 1.2),
    ("• Third Quarter 2026 Revenues of $112.7 Billion; Earnings from Operations of $6.6 "
     "Billion", 6.6),                                                               # UNH
    ("The Goldman Sachs Group, Inc. (NYSE: GS) today reported net revenues of $18.68 billion and "
     "net earnings of $5.29 billion for the third quarter ended September 30, 2026.", 5.29),
    ("We are raising our 2026 guidance across the board, and we now expect 2027 commercial "
     "services to grow low-double-digits supported by our over $170 billion commercial services "
     "backlog.", 170),                                                              # GE
])
def test_another_metric_in_a_revenue_sentence_is_refused(quote, value):
    figures, reasons = _revenue(quote, value, "billion")
    assert figures is None and reasons["revenue"] == "wrong_metric"


@pytest.mark.parametrize("quote,value,unit", [
    ("System-wide sales of $644.8 million, up 12.8% versus 2025.", 644.8, "million"),   # SHAK
    ("Global Systemwide sales* increased 4% to over $38 billion for the quarter", 38,
     "billion"),                                                                    # MCD
    ("The increase was primarily due to higher sales volume of $879 million and favorable price "
     "realization of $216 million.", 879, "million"),                               # CAT
])
def test_sales_that_are_no_revenue_are_refused(quote, value, unit):
    figures, reasons = _revenue(quote, value, unit)
    assert figures is None and reasons["revenue"] == "wrong_metric"


def test_a_count_of_sales_is_no_revenue():
    quote = "2.3 million digitally-enabled sales, representing 71% of total sales"  # BAC
    figures, reasons = _revenue(quote, 2.3, "million")
    assert figures is None and reasons["revenue"] == "no_dollar_mark"


def test_a_prior_year_figure_past_the_clause_is_refused():
    quote = ("Net sales increased 12% to $201.9 billion in the third quarter, compared with $180.2 "
             "billion in third quarter 2025.")
    figures, reasons = _revenue(quote, 180.2, "billion")
    assert figures is None and reasons["revenue"] == "wrong_metric"


# ── EPS ──────────────────────────────────────────────────────────────────────


_GAAP_ROWS = [
    ("Diluted earnings per share was $2.10, up 14 percent year over year, and included a "
     "favorable impact of $0.05 from tariff refunds.", 2.1),                        # AAPL
    ("Net income decreased to $20.7 billion in the third quarter, or $1.89 per diluted share, "
     "compared with $21.2 billion, or $1.95 per diluted share, in third quarter 2025.", 1.89),
    ("The company reported GAAP loss per share of ($0.26) and core loss per share (non-GAAP)* of "
     "($0.34).", -0.26),                                                            # BA
    ("EPS of $0.91", 0.91),                                                         # BAC
    ("Third-quarter 2026 profit per share was $7.13, compared with third-quarter 2025 profit per "
     "share of $4.88.", 7.13),                                                      # CAT
    ("Diluted net income from continuing operations per share attributable to common "
     "stockholders $1.00", 1.0),                                                    # ETSY
    ("•Continuing EPS (GAAP) of $2.32, +14%; adjusted EPS* $2.25, +36%;", 2.32),   # GE
    ("Net income available to common stockholders was $26.2 billion and EPS was $2.12.", 2.12),
    ("Diluted earnings per common share (EPS)1 was $17.01 for the third quarter of 2026 compared "
     "with $12.25 for the third quarter of 2025 and $20.98 for the second quarter of 2026, and "
     "was $55.56 for the first nine months of 2026 compared with $37.33 for the first nine "
     "months of 2025.", 17.01),                                                     # GS
    ("▪Third-quarter earnings per share (EPS) attributable to Intel was $0.60; non-GAAP EPS "
     "attributable to Intel was $0.52.", 0.6),                                      # INTC
    ("•2026 Third-Quarter earnings per share (EPS) of $0.09 reflects a one-time special "
     "charge for the proposed talc litigation resolution, the acquisition of Firefly Bio, Inc., "
     "and the strategic collaboration with Sail Biomedicines; adjusted EPS* of $2.59 also "
     "reflects the impact of these M&A deals", 0.09),                               # JNJ
    ("JPMORGANCHASE REPORTS THIRD-QUARTER 2026 NET INCOME OF $15.3 BILLION ($5.57 PER SHARE)",
     5.57),                                                                         # JPM
    ("EPS Grew 8% to $0.93; Comparable EPS (Non-GAAP) Grew 11% to $0.91", 0.93),    # KO
    ("Diluted earnings per share was $3.51, an increase of 10% (11% in constant currencies).",
     3.51),                                                                         # MCD
    ("Diluted earnings per share (EPS) $ 3.83", 3.83),                              # META
    ("Diluted earnings per share was $4.92 and increased 32% on a GAAP basis", 4.92),   # MSFT
    ("Net income per common share, basic and diluted $ 0.43 $ 0.35 $ 1.13 $ 0.84", 0.43),   # O
    ("■Third-Quarter 2026 Reported(4) Diluted EPS of $0.56, and Adjusted(3) Diluted EPS of "
     "$0.87", 0.56),                                                                # PFE
    ("Diluted net earnings per share were $1.97, an increase of one percent versus prior year as "
     "the increase in net sales was partially offset by an increase in operating costs.", 1.97),
    ("Net earnings per share attributable to common stockholders - Diluted $ 0.86 $ 0.82 $ 3.04 "
     "$ 2.06", 0.86),                                                               # PLD
    ("Earnings per share: Basic $ 0.22 $ 0.03 Diluted $ 0.19 $ 0.03", 0.19),        # PTON
    ("Net income per share — diluted $ 1.20", 1.2),                            # ROKU
    ("Q4 GAAP EPS $0.59, Up 392%", 0.59),                                           # SBUX
    ("▪Net income attributable to Shake Shack Inc. of $16.5 million, or earnings of $0.39 "
     "per diluted share.", 0.39),                                                   # SHAK
    ("Diluted net income (loss) per share $ 0.03", 0.03),                           # SNAP
    ("EPS attributable to common stockholders, diluted (GAAP) 0.39 0.24 0.13 0.32 0.22", 0.22),
    ("• Earnings of $4.56 Per Share and Adjusted Earnings of $4.69 Per Share", 4.56),  # UNH
    ("ExxonMobil Holdings Corporation today announced third-quarter 2026 earnings of $15.4 "
     "billion, or $3.72 per share.", 3.72),                                         # XOM
    ("Reported EPS of $3.72, or $4.23 adjusted EPS1", 3.72),                        # XOM
    ("NET INCOME OF $15.3 BILLION ($5.57 PER SHARE), NET INCOME OF $15.8 BILLION ($5.75 PER "
     "SHARE) EXCLUDING A SIGNIFICANT ITEM", 5.57),                                  # JPM
    ("Earnings per share: EPS grew 8% to $0.93, and comparable EPS (non-GAAP) grew 11% to $0.91.",
     0.93),                                                                         # KO
]

_ADJUSTED_ROWS = [
    ("The company reported GAAP loss per share of ($0.26) and core loss per share (non-GAAP)* of "
     "($0.34).", -0.34),                                                            # BA
    ("Third-quarter 2026 profit per share of $7.13; adjusted profit per share of $7.20", 7.2),
    ("•Continuing EPS (GAAP) of $2.32, +14%; adjusted EPS* $2.25, +36%;", 2.25),   # GE
    ("▪Third-quarter earnings per share (EPS) attributable to Intel was $0.60; non-GAAP EPS "
     "attributable to Intel was $0.52.", 0.52),                                     # INTC
    ("•2026 Third-Quarter earnings per share (EPS) of $0.09 reflects a one-time special "
     "charge for the proposed talc litigation resolution, the acquisition of Firefly Bio, Inc., "
     "and the strategic collaboration with Sail Biomedicines; adjusted EPS* of $2.59 also "
     "reflects the impact of these M&A deals", 2.59),                               # JNJ
    ("NET INCOME OF $15.8 BILLION ($5.75 PER SHARE) EXCLUDING A SIGNIFICANT ITEM", 5.75),   # JPM
    ("EPS Grew 8% to $0.93; Comparable EPS (Non-GAAP) Grew 11% to $0.91", 0.91),    # KO
    ("Excluding the current year charges described above of $0.06 per share, diluted earnings "
     "per share was $3.57, an increase of 11% (11% in constant currencies) when also excluding "
     "prior year charges.**", 3.57),                                                # MCD
    ("Diluted earnings per share was $4.92 and increased 32% on a GAAP basis, and was $4.89 and "
     "increased 18% (up 19% in constant currency) on a non-GAAP basis", 4.89),      # MSFT
    ("■Third-Quarter 2026 Reported(4) Diluted EPS of $0.56, and Adjusted(3) Diluted EPS of "
     "$0.87", 0.87),                                                                # PFE
    ("Core earnings per share were $2.00, an increase of one percent versus the prior year.", 2.0),
    ("•Adjusted net loss per share for the third quarter was $(0.48) compared to $(0.65) for "
     "the same quarter of the prior year.", -0.48),                                 # RIVN
    ("Non-GAAP EPS $0.80, Up 54%", 0.8),                                            # SBUX
    ("•Adjusted pro forma net income(1) of $17.8 million, or earnings of $0.40 per fully "
     "exchanged and diluted share.", 0.4),                                          # SHAK
    ("EPS attributable to common stockholders, diluted (non-GAAP) 0.50 0.50 0.41 0.33 0.48", 0.48),
    ("• Earnings of $4.56 Per Share and Adjusted Earnings of $4.69 Per Share", 4.69),  # UNH
    ("Adjusted earnings were $17.5 billion, or $4.23 per share.", 4.23),            # XOM
    ("Reported EPS of $3.72, or $4.23 adjusted EPS1", 4.23),                        # XOM
    ("NET INCOME OF $15.3 BILLION ($5.57 PER SHARE), NET INCOME OF $15.8 BILLION ($5.75 PER "
     "SHARE) EXCLUDING A SIGNIFICANT ITEM", 5.75),                                  # JPM
    ("Earnings per share: EPS grew 8% to $0.93, and comparable EPS (non-GAAP) grew 11% to $0.91.",
     0.91),                                                                         # KO
]


@pytest.mark.parametrize("quote,value", _GAAP_ROWS)
def test_a_gaap_eps_keeps_passing(quote, value):
    figures, reasons = _eps("eps_gaap_diluted", quote, value)
    assert figures is not None and figures.eps_gaap == value, reasons
    assert reasons["eps_gaap_diluted"] == "ok"


@pytest.mark.parametrize("quote,value", _ADJUSTED_ROWS)
def test_an_adjusted_eps_keeps_passing(quote, value):
    figures, reasons = _eps("eps_adjusted_diluted", quote, value)
    assert figures is not None and figures.eps_adjusted == value, reasons
    assert reasons["eps_adjusted_diluted"] == "ok"


@pytest.mark.parametrize("quote,value", _ADJUSTED_ROWS)
def test_the_gaap_slot_given_the_adjusted_number_is_refused(quote, value):
    figures, reasons = _eps("eps_gaap_diluted", quote, value)
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


@pytest.mark.parametrize("quote,value", [row for row in _GAAP_ROWS if "djusted" in row[0]
                                         or "non-GAAP" in row[0] or "Non-GAAP" in row[0]])
def test_the_adjusted_slot_given_the_gaap_number_is_refused(quote, value):
    """The adjusted word sits in the quote, but on the other figure. This one is the worse way
    round: an adjusted EPS is the figure that gets a beat or a miss."""
    figures, reasons = _eps("eps_adjusted_diluted", quote, value)
    assert figures is None and reasons["eps_adjusted_diluted"] == "not_adjusted"


@pytest.mark.parametrize("quote,value", [
    ("AFFO per share $ 1.12 $ 1.08 $ 3.35 $ 3.19", 1.12),                           # O
    ("Core FFO attributable to common stockholders/unitholders* 1.60 1.49 4.73 4.37", 1.6),  # PLD
    ("Diluted $ 0.19", 0.19),                                                       # PTON: no label
])
@pytest.mark.parametrize("field", ["eps_adjusted_diluted", "eps_gaap_diluted"])
def test_a_per_share_row_of_another_measure_is_refused_in_both_slots(field, quote, value):
    figures, reasons = _eps(field, quote, value)
    assert figures is None and reasons[field] == "wrong_metric"


@pytest.mark.parametrize("quote,value", [
    ("Earnings of $1.00 per share and a dividend of $0.52 per share", 0.52),
    ("Excluding the current year charges described above of $0.06 per share, diluted earnings "
     "per share was $3.57, an increase of 11% (11% in constant currencies) when also excluding "
     "prior year charges.**", 0.06),                                                # MCD
    ("Diluted earnings per share was $2.10, up 14 percent year over year, and included a "
     "favorable impact of $0.05 from tariff refunds.", 0.05),
])
def test_another_amount_in_an_eps_sentence_is_refused(quote, value):
    figures, reasons = _eps("eps_gaap_diluted", quote, value)
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


def test_an_amount_that_is_a_charge_stands_nowhere():
    quote = ("Net pre-tax charges of $56 million, or $0.06 per share, for the quarter and $155 "
             "million, or $0.17 per share, for the nine months, primarily related to restructuring "
             "charges associated with the Company's internal effort to modernize ways of working "
             "(Accelerating the Organization)")                                     # MCD
    for value in (0.06, 0.17):
        figures, reasons = _eps("eps_gaap_diluted", quote, value)
        assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


def test_a_prior_year_eps_past_the_comparison_is_refused():
    quote = ("\u2022Adjusted net loss per share for the third quarter was $(0.48) compared to "
             "$(0.65) for the same quarter of the prior year.")                     # RIVN
    for field in ("eps_adjusted_diluted", "eps_gaap_diluted"):
        figures, reasons = _eps(field, quote, -0.65)
        assert figures is None and reasons[field] == "wrong_metric"


def test_a_figure_with_no_share_in_its_label_never_stands_as_gaap():
    quote = "•Diluted EPS of $3.83 would have increased by $3.57 to $7.40."     # META
    figures, reasons = _eps("eps_gaap_diluted", quote, 3.57)
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


# ── a table with no dollar mark of its own ───────────────────────────────────


@pytest.mark.parametrize("field,quote,value", [
    ("eps_gaap_diluted",
     "EPS attributable to common stockholders, diluted (GAAP) 0.39 0.24 0.13 0.32 0.22", 0.22),
    ("eps_adjusted_diluted",
     "EPS attributable to common stockholders, diluted (non-GAAP) 0.50 0.50 0.41 0.33 0.48", 0.48),
])
def test_a_row_with_no_mark_stands_in_a_release_that_reads_in_dollars(field, quote, value):
    figures, reasons = _eps(field, quote, value)
    assert figures is not None and reasons[field] == "ok"


def test_a_row_with_no_mark_waits_in_a_release_that_quotes_a_foreign_amount():
    """O and PLD: real money in euros beside the results, so a bare row proves nothing."""
    row = "EPS attributable to common stockholders, diluted (GAAP) 0.39 0.24 0.13 0.32 0.22"
    text = f"{_AROUND} In July 2026, we issued €600.0 million of senior notes. {row}"
    figures, reasons = _verify(text, eps_gaap_diluted={"value": 0.22, "quote": row})
    assert figures is None and reasons["eps_gaap_diluted"] == "no_dollar_mark"


# ── the outlook ──────────────────────────────────────────────────────────────


_OUTLOOKS = [
    "Net sales are expected to be between $240.0 billion and $247.0 billion, or to grow between "
    "12% and 16% compared with fourth quarter 2025.",                               # AMZN
    "We are raising our 2026 guidance across the board, and we now expect 2027 commercial "
    "services to grow low-double-digits supported by our over $170 billion commercial services "
    "backlog.",                                                                     # GE
    "▪Forecasting fourth-quarter 2026 revenue of $15.0 billion to $16.0 billion; forecasting "
    "fourth-quarter EPS attributable to Intel of $0.10 and non-GAAP EPS attributable to Intel of "
    "$0.25.",                                                                       # INTC
    "•Strong operational performance results in the Company increasing 2026 guidance with "
    "estimated reported sales of $101.2 Billion or 7.4% at the midpoint and increasing adjusted "
    "EPS* guidance by $0.06 to $11.10 or 2.9% at the midpoint",                     # JNJ
    "We expect fourth quarter 2026 total revenue to be in the range of $73-76 billion.",    # META
    "“As a result, we are raising our 2026 AFFO per share guidance to $4.45–$4.46 while "
    "reaffirming our $10.0 billion investment volume target, which is supported by a "
    "well-funded balance sheet.”",                                             # O
    "■Raises full-year 2026 Revenue guidance(2) by $1.5 billion at the midpoint to a range "
    "of $62.0 to $64.0 billion from $60.5 to $62.5 billion previously.",            # PFE
    "P&G updated its guidance range for fiscal 2027 all-in sales growth to be in the range of "
    "two to four percent versus the prior year, reflecting the acquisition of Thorne as of "
    "October 1, 2026.",                                                             # PG
    "•Total Revenue is expected to be in the range of $625 million to $650 million, "
    "representing a decrease of $19 million or 3% year-over-year at the midpoint.",  # PTON
    "The company now expects full year 2026 adjusted net earnings of at least $20.50 per share, "
    "incorporating experience through the third quarter 2026.",                     # UNH
]

_NOT_OUTLOOKS = [
    "“Our team continues to execute our strategic priorities in a dynamic environment, "
    "positioning us for a strong year while delivering long-term value for our customers and "
    "shareholders.”",                                                          # CAT: praise
    "We had a strong quarter and are now on a half-trillion dollar revenue run rate.",  # GOOGL
    "These forces may be manageable, but we remain prepared for a wide range of outcomes.",  # JPM
    "In the quarter, the 777X program continued to progress on 777-9 certification flight testing "
    "and remains on track for first delivery in 2027.",                             # BA: a program
    "Volume We are focused on maximum capacity utilization at our factories.",      # TSLA
    "Microsoft will provide forward-looking guidance in connection with this quarterly earnings "
    "announcement on its earnings conference call and webcast.",                    # MSFT: a pointer
    "Snap Inc. will discuss its Q4 2026 outlook during its Q3 2026 Earnings Call (details below) "
    "and in its investor letter available at investor.snap.com.",                   # SNAP: a pointer
    "In light of the pending transaction, we will not host an earnings call and will not provide "
    "a financial outlook.",                                                         # ROKU
    "•Full year delivery guidance is improving from 65,000 - 70,000 units to 67,000 - 70,000 "
    "units as a result of the production and delivery outlook for the fourth quarter of 2026.",
]


def _outlook(quote: str):
    text = f"Acme reported results. {quote} •Acme makes things."
    return _verify(text, guidance={"quote": quote})


@pytest.mark.parametrize("quote", _OUTLOOKS)
def test_an_outlook_that_looks_ahead_and_states_a_figure_stands(quote):
    figures, reasons = _outlook(quote)
    assert reasons["guidance"] == "ok"
    assert figures.guidance == quote.strip(f"{efs._LIST_GLYPHS} ")


@pytest.mark.parametrize("quote", _NOT_OUTLOOKS)
def test_commentary_praise_and_pointers_are_no_outlook(quote):
    figures, reasons = _outlook(quote)
    assert figures is None and reasons["guidance"] == "not_outlook"


def test_a_sentence_that_only_introduces_the_guidance_stays_a_lead_in():
    figures, reasons = _outlook(
        "The company introduces the following fiscal 2027 guidance for the full year.")     # SBUX
    assert figures is None and reasons["guidance"] == "lead_in"


def test_every_row_here_fits_the_size_of_a_quote():
    """One row or sentence each, as the note on top of this file says."""
    rows: Dict[str, Any] = dict.fromkeys(q for q, _ in _GAAP_ROWS + _ADJUSTED_ROWS)
    rows.update(dict.fromkeys(_OUTLOOKS + _NOT_OUTLOOKS))
    assert all(len(row) <= efs.PEG_SPAN_MAX_CHARS for row in rows)
