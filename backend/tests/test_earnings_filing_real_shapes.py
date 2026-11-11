"""What real earnings releases print, read through `verify_extraction`.

Every string here is one row or one sentence of a public SEC filing (EX-99.1, third quarter 2026),
as `html_to_text` leaves it: the shapes a model really quotes, which no hand-written sentence
predicted. A test that needs what stands around a row joins a few rows or a header in front of it.
A figure must keep passing on these, and the row beside each refusal is the mistake the verifier
exists to stop: XOM's release has no revenue sentence, and the model filled the slot with its
earnings. Hermetic: no release, no model answer and no network.
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


# ── a quote is read where the release prints it ──────────────────────────────
#
# A quote can stop anywhere, and a label or a scale it left behind still decides what its number
# is. The piece must name the figure on its own, and the row it came from must not say otherwise.


def _cut_from(field: str, printed: str, quote: str, value: Any, unit: Optional[str] = None,
              header: Optional[str] = None):
    """`quote`, a piece of the row or sentence `printed`, answered for `field`."""
    node: Dict[str, Any] = {"value": value, "quote": quote}
    if unit is not None:
        node["unit"] = unit
    return _verify(_in_a_release(printed, header), **{field: node})


@pytest.mark.parametrize("printed,quote,value", [
    ("Net pre-tax charges of $56 million, or $0.06 per share, for the quarter",
     "or $0.06 per share", 0.06),                                                   # MCD
    ("\u25a0Returning capital directly to shareholders through $7.3 billion of cash dividends, or "
     "$1.29 per share of common stock.", "or $1.29 per share of common stock.", 1.29),  # PFE
    ("Adjusted earnings were $17.5 billion, or $4.23 per share.", "or $4.23 per share.", 4.23),
    ("\u25aaThird-quarter earnings per share (EPS) attributable to Intel was $0.60; non-GAAP EPS "
     "attributable to Intel was $0.52.", "EPS attributable to Intel was $0.52", 0.52),  # INTC
    ("Adjusted(3) Diluted EPS 0.87 0.87 \u2014 2.39 2.56 (7%)",
     "Diluted EPS 0.87 0.87 \u2014 2.39 2.56 (7%)", 0.87),                            # PFE
    ("Adjusted(3) Diluted EPS $2.95 to $3.05 (previously $2.80 to $3.00)",
     "Diluted EPS $2.95 to $3.05", 2.95),                                           # PFE
])
def test_a_quote_cut_behind_the_words_that_change_it_is_no_gaap_eps(printed, quote, value):
    figures, reasons = _cut_from("eps_gaap_diluted", printed, quote, value)
    assert figures is None and reasons["eps_gaap_diluted"] != "ok"


@pytest.mark.parametrize("field,printed,quote,value,unit", [
    ("revenue", "\u2022Total revenue (GAAP) of $14.1B, +16%; adjusted revenue* $13.3B, +18%;",
     "\u2022Total revenue (GAAP) of $14.1", 14.1, "million"),                         # GE
    ("revenue", "Revenue was $92.3 billion and increased 19%", "Revenue was $92.3", 92.3,
     "million"),                                                                    # MSFT
    ("eps_gaap_diluted", "NET INCOME OF $15.3 BILLION ($5.57 PER SHARE), NET INCOME OF $15.8 "
     "BILLION ($5.75 PER SHARE) EXCLUDING A SIGNIFICANT ITEM",
     "PER SHARE), NET INCOME OF $15.8", 15.8, None),                                # JPM
    ("eps_gaap_diluted", "Net pre-tax charges of $56 million, or $0.06 per share, for the quarter "
     "and $155 million, or $0.17 per share, for the nine months",
     "per share, for the quarter and $155", 155, None),                             # MCD
    ("eps_gaap_diluted", "Net income decreased to $20.7 billion in the third quarter, or $1.89 per "
     "diluted share, compared with $21.2 billion, or $1.95 per diluted share, in third quarter "
     "2025.", "per diluted share, compared with $21.2", 21.2, None),                # AMZN
])
def test_a_quote_stopped_before_its_scale_word_is_refused(field, printed, quote, value, unit):
    figures, reasons = _cut_from(field, printed, quote, value, unit)
    assert figures is None and reasons[field] != "ok"


def test_a_quote_stopped_inside_a_range_is_refused():
    figures, reasons = _cut_from("eps_gaap_diluted", "Net income per share $1.55 - $1.56 $1.13",
                                 "Net income per share $1.55", 1.55)            # O
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


def test_a_quote_that_lost_only_words_with_no_say_still_stands():
    figures, reasons = _cut_from("eps_gaap_diluted", "Bank of America Reports 3Q26 Net Income of $6.9 "
                                 "Billion; EPS of $0.91 3Q26 Revenue Up 3% YoY to $30.0 Billion",
                                 "EPS of $0.91", 0.91)
    assert figures.eps_gaap == 0.91 and reasons["eps_gaap_diluted"] == "ok"


# ── what is no figure of the quarter ─────────────────────────────────────────


@pytest.mark.parametrize("field,quote,value", [
    ("eps_adjusted_diluted",
     "\u25aaForecasting fourth-quarter 2026 revenue of $15.0 billion to $16.0 billion; forecasting "
     "fourth-quarter EPS attributable to Intel of $0.10 and non-GAAP EPS attributable to Intel of "
     "$0.25.", 0.25),                                                               # INTC
    ("eps_adjusted_diluted", "\u2022Non-GAAP earnings per share of $3.15 to $3.25, including "
     "slightly more than ten cents related to the 53rd week; and", 3.15),           # SBUX
    ("eps_adjusted_diluted", "\u2022Non-GAAP earnings per share of $3.15 to $3.25, including "
     "slightly more than ten cents related to the 53rd week; and", 3.25),           # SBUX
    ("eps_adjusted_diluted", "Adjusted Earnings of At Least $20.50 Per Share", 20.5),   # UNH
    ("eps_gaap_diluted", "\u2022 Updated Full Year 2026 Earnings Outlook to At Least $19.50 Per "
     "Share", 19.5),                                                                # UNH
    ("eps_adjusted_diluted", "an increase of $0.18 per share to reported EPS from $5.57 to $5.75",
     5.57),                                                                         # JPM
])
def test_a_forecast_or_one_end_of_a_range_is_no_eps_of_the_quarter(field, quote, value):
    figures, reasons = _eps(field, quote, value)
    assert figures is None and reasons[field] != "ok"


@pytest.mark.parametrize("value", [62.0, 64.0])
def test_either_end_of_a_range_is_no_revenue(value):
    figures, reasons = _revenue("Revenues $62.0 to $64.0 billion (previously $60.5 to $62.5 "
                                "billion)", value, "billion")                       # PFE
    assert figures is None and reasons["revenue"] != "ok"


_PELOTON_HEADER = "Financial Results (dollars in millions) Q2 FY26 Q1 FY27 Low High Y/Y Q/Q"
_PELOTON_ROW = "Total Revenue $656.5 $559.2 $625.0 $650.0 (3)% 14%"


@pytest.mark.parametrize("value", [625.0, 650.0])
def test_a_cell_of_the_guides_columns_is_no_revenue(value):
    figures, reasons = _revenue(_PELOTON_ROW, value, "million", _PELOTON_HEADER)
    assert figures is None and reasons["revenue"] == "wrong_metric"


def test_the_cells_in_front_of_the_guides_columns_still_stand():
    figures, reasons = _revenue(_PELOTON_ROW, 559.2, "million", _PELOTON_HEADER)
    assert figures.revenue == 559.2e6 and reasons["revenue"] == "ok"


@pytest.mark.parametrize("quote,value", [
    ("The impact of tax benefits related to employee share-based awards was a reduction to "
     "provision for taxes for the first nine months of 2026 of approximately $975 million, which "
     "increased diluted EPS by approximately $3.20 and annualized ROE by 1.1 percentage points.",
     3.2),                                                                          # GS
    ("For Q3 2026, the net effect of the loss on equity securities of $8.7 billion decreased the "
     "provision for income tax, net income, and diluted net income per common share by $1.9 "
     "billion, $6.7 billion, and $0.55, respectively.", 0.55),                      # GOOGL
    ("which resulted in an increase in net income and diluted earnings per share of $207 million "
     "and $0.03, respectively.", 0.03),                                             # MSFT
    ("\u2022Diluted EPS of $3.83 would have increased by $3.57 to $7.40.", 7.4),      # META
])
def test_what_a_figure_changed_by_or_would_have_been_is_no_eps(quote, value):
    figures, reasons = _eps("eps_gaap_diluted", quote, value)
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


def test_the_figure_a_what_if_starts_from_still_stands():
    figures, reasons = _eps("eps_gaap_diluted",
                            "\u2022Diluted EPS of $3.83 would have increased by $3.57 to $7.40.",
                            3.83)                                                   # META
    assert figures.eps_gaap == 3.83 and reasons["eps_gaap_diluted"] == "ok"


def test_the_first_of_two_bullets_flattened_together_names_nothing_in_the_second():
    quote = ("\u2022 Windows OEM and Devices revenue decreased 22% Microsoft returned $10.2 billion "
             "to shareholders")                                                     # MSFT
    figures, reasons = _revenue(quote, 10.2, "billion")
    assert figures is None and reasons["revenue"] == "wrong_metric"


@pytest.mark.parametrize("value", [408.0, 15.3])
def test_what_a_total_is_said_to_include_is_no_revenue(value):
    quote = ("\u25aaTotal revenue of $423.3 million, up 15.2% versus 2025, including $408.0 million "
             "of Shack sales and $15.3 million of Licensing revenue.")              # SHAK
    figures, reasons = _revenue(quote, value, "million")
    assert figures is None and reasons["revenue"] == "wrong_metric"


@pytest.mark.parametrize("field,quote,value", [
    ("eps_gaap_diluted", "Digital assets unrealized (gain) loss per share, net of tax (0.02) 0.07 "
     "0.05 0.02 (0.06)", -0.06),                                                    # TSLA
    ("eps_adjusted_diluted", "Currency impact to Core EPS $ (0.01)", -0.01),        # PG
])
def test_a_reconciling_row_is_no_eps(field, quote, value):
    figures, reasons = _eps(field, quote, value)
    assert figures is None and reasons[field] == "wrong_metric"


@pytest.mark.parametrize("quote,value,unit,header", [
    ("Sales of investments 10,470 3,262", 10470, "million", "(In millions)"),       # MSFT
    ("Sales and maturities of investments 219,112 227,563", 219112, "thousand",
     "(in thousands)"),                                                             # ETSY
    ("Maturities and sales of marketable securities 20,371 21,820 61,301 88,516", 20371, "million",
     "(In millions)"),                                                              # GOOGL
])
def test_a_cash_flow_row_of_sales_is_no_revenue(quote, value, unit, header):
    figures, reasons = _revenue(quote, value, unit, header)
    assert figures is None and reasons["revenue"] == "wrong_metric"


# ── a row of a segment or of another period ──────────────────────────────────


_GE_SEGMENTS = ("(Dollars in millions) Orders $15,265 $9,645 58 % $45,528 $29,617 54 % Revenue "
                "10,342 8,521 21 % 28,992 22,830 27 % Orders $4,269 $3,607 18 % $14,581 $10,979 "
                "33 % Revenue 3,469 3,251 7 % 10,126 8,927 13 %")


def test_a_bare_row_the_release_prints_with_another_figure_is_a_segments():
    row = "Revenue 10,342 8,521 21 % 28,992 22,830 27 %"                            # GE
    figures, reasons = _verify(f"{_AROUND} {_GE_SEGMENTS}",
                               revenue={"value": 10342, "unit": "million", "quote": row})
    assert figures is None and reasons["revenue"] != "ok"


def test_the_bare_rows_of_a_segment_table_are_refused_by_their_own_label():
    row = "Net revenues $7,328.4 $6,901.5 6%"                                       # SBUX
    text = (f"{_AROUND} ($ in millions) Coffeehouse Count (2)(3) 18,176 18,311 (1)% {row} Operating "
            "Income $898.4 $308.5 191% Coffeehouse Count (2) 23,431 22,679 3% Net revenues $1,305.8 "
            "$2,070.9 (37)%")
    figures, reasons = _verify(text, revenue={"value": 7328.4, "unit": "million", "quote": row})
    assert figures is None and reasons["revenue"] != "ok"


def test_a_bare_row_with_the_same_figure_printed_twice_stands():
    row = "Revenues $ 7,157 $ 7,078 1 % 1 %"                                        # MCD
    text = f"{_AROUND} Dollars in millions, except per share data 54 % {row} 12 % {row}"
    figures, reasons = _verify(text, revenue={"value": 7157, "unit": "million", "quote": row})
    assert figures.revenue == 7.157e9 and reasons["revenue"] == "ok"


def test_a_row_naming_a_segment_is_no_revenue_of_the_company():
    figures, reasons = _revenue("Agents and Infra Revenue $76,795 $61,672", 76795, "million",
                                "(In millions)")                                    # MSFT
    assert figures is None and reasons["revenue"] != "ok"


@pytest.mark.parametrize("value", [6.13, 4.8])
def test_a_cell_behind_a_rate_cell_is_another_group_of_columns(value):
    figures, reasons = _eps("eps_adjusted_diluted", "Adjusted EPS 2.25 1.66 36 % 6.13 4.80 28 %",
                            value)                                                  # GE
    assert figures is None and reasons["eps_adjusted_diluted"] == "wrong_metric"


def test_the_cells_in_front_of_a_rate_cell_still_stand():
    figures, reasons = _eps("eps_adjusted_diluted", "Adjusted EPS 2.25 1.66 36 % 6.13 4.80 28 %",
                            2.25)                                                   # GE
    assert figures.eps_adjusted == 2.25 and reasons["eps_adjusted_diluted"] == "ok"


# ── prose that is no header ──────────────────────────────────────────────────


@pytest.mark.parametrize("row,value,prose", [
    ("Revenue $ 64,601 $ 51,242 26 %", 64601,
     "WhatsApp further empowered billions around the world."),                      # META
    ("Total net sales 180,169 201,944 503,538 584,069", 201944,
     "Announced that Amazon helped U.S. customers save billions\u2014averaging over $1,000 per "
     "customer each year"),                                                         # AMZN
    ("Total net sales 180,169 201,944 503,538 584,069", 201944,
     "Some amounts within this report are rounded to the millions or billions and may not add."),
])
def test_a_scale_word_in_prose_leaves_a_release_with_its_one_header(row, value, prose):
    text = f"{_AROUND} {prose} (in millions, except per share data) {row}"
    figures, reasons = _verify(text, revenue={"value": value, "unit": "million", "quote": row})
    assert figures.revenue == value * 1e6 and reasons["revenue"] == "ok"


# ── rows that kept their label ───────────────────────────────────────────────


@pytest.mark.parametrize("quote,value", [
    ("Diluted earnings per share1 $0.91 ($0.17) $1.08", 0.91),                      # BAC
    ("Diluted Net Income Per Share1 $ 0.93 $ 0.86 8", 0.93),                        # KO
    ("EARNINGS PER COMMON SHARE Basic3 $ 17.24 $ 21.27 $ 12.42 (19) % 39 % Diluted1 $ 17.01 $ "
     "20.98 $ 12.25 (19) 39", 17.01),                                               # GS
    ("Net income per share of common stock attributable to common stockholders Basic $ 0.43 $ 0.26 "
     "$ 0.15 $ 0.34 $ 0.24 Diluted $ 0.39 $ 0.24 $ 0.13 $ 0.32 $ 0.22", 0.22),      # TSLA
])
def test_a_row_keeps_its_label_past_a_footnote_and_across_its_cells(quote, value):
    figures, reasons = _eps("eps_gaap_diluted", quote, value)
    assert figures.eps_gaap == value and reasons["eps_gaap_diluted"] == "ok"


@pytest.mark.parametrize("quote,value", [
    ("(dollars in millions, except per share data): Three months ended September 30, Nine months "
     "ended September 30, 2026 2025 2026 2025 Total revenue $ 1,621.6 $ 1,470.6 $ 4,718.0 $ "
     "4,261.4", 1621.6),                                                            # O
    ("dollars in millions, except per share/unit data Three Months Ended September 30, Nine Months "
     "Ended September 30, 2026 2025 2026 2025 Rental and other revenues $ 2,224 $ 2,064 $ 6,544 $ "
     "6,098 Strategic capital revenues 164 150 567 439 Total revenues 2,388 2,214 7,111 6,537",
     2388),                                                                         # PLD
])
def test_a_table_row_with_its_header_in_the_quote_needs_no_one_scale_in_the_release(quote, value):
    text = f"{_AROUND} {quote} Consolidated Statements (in thousands, except per share data)"
    figures, reasons = _verify(text, revenue={"value": value, "unit": "million", "quote": quote})
    assert figures.revenue == value * 1e6 and reasons["revenue"] == "ok"


def test_a_tables_dash_between_two_falling_cells_is_no_range():
    row = "Revenue $92,279 - $77,673 - 19% - $(309) 19% -"                          # MSFT
    figures, reasons = _revenue(row, 92279, "million", "($ in millions, except per share amounts)")
    assert figures.revenue == 92.279e9 and reasons["revenue"] == "ok"


def test_the_first_sentence_of_a_release_opens_at_its_dateline():
    """Its headlines above have no full stop, and they are full of forward words."""
    sentence = ("MAINTAINS FISCAL YEAR ORGANIC SALES, EPS GROWTH AND CASH RETURN GUIDANCE CINCINNATI, "
                "October 22, 2026 - The Procter & Gamble Company (NYSE:PG) reported first quarter "
                "fiscal year 2027 net sales of $23.3 billion, an increase of four percent versus the "
                "prior year.")                                                      # PG
    quote = "net sales of $23.3 billion, an increase of four percent versus the prior year."
    figures, reasons = _cut_from("revenue", sentence, quote, 23.3, "billion")
    assert figures.revenue == 23.3e9 and reasons["revenue"] == "ok"


@pytest.mark.parametrize("quote,value", [
    ("net sales increased 13% compared with third quarter 2025.", 2025),            # AMZN
    ("Y/Y net sales growth: North America 11 % 6 % 10 % 11 % International 14 5 12 13", 14),  # AMZN
    ("Net sales mix: North America 59 % 56 % 59 % 57 % International 23 21 22 21", 23),    # AMZN
    ("sales $ 74,058 $ 75,296 $ 206,274 $ 224,202", 74058),                         # AMZN
])
def test_a_number_only_the_header_scales_must_be_an_amount_in_a_whole_label(quote, value):
    figures, reasons = _revenue(quote, value, "million", "(in millions)")
    assert figures is None and reasons["revenue"] != "ok"


def test_a_note_under_a_table_speaks_for_no_row():
    quote = ("Earnings per share attributable to Intel\u2014diluted $0.60 $0.90 down 33% $0.52 $0.23 up "
             "126% Full reconciliations between GAAP and non-GAAP measures are provided below.")
    figures, reasons = _eps("eps_gaap_diluted", quote, 0.6)
    assert figures.eps_gaap == 0.6 and reasons["eps_gaap_diluted"] == "ok"


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


@pytest.mark.parametrize("quote", [
    "\u2022Non-GAAP earnings per share of $3.15 to $3.25, including slightly more than ten cents "
    "related to the 53rd week; and",                                                # SBUX
    "\u2022North America comparable coffeehouse sales growth of approximately 4.0%;",  # SBUX
])
def test_a_line_that_only_a_heading_makes_an_outlook_is_refused(quote):
    figures, reasons = _verify(f"Fiscal Year 2027 Guidance {quote} \u2022Acme makes things.",
                               guidance={"quote": quote})
    assert figures is None and reasons["guidance"] == "not_outlook"


def test_a_sentence_that_only_introduces_the_guidance_stays_a_lead_in():
    figures, reasons = _outlook(
        "The company introduces the following fiscal 2027 guidance for the full year.")     # SBUX
    assert figures is None and reasons["guidance"] == "lead_in"


# ── a real row that names no such figure ─────────────────────────────────────

#: Bank of America's whole sentence: this quarter, the quarter before and the year-ago quarter.
_BAC_FTE = ("Revenue, net of interest expense, on an FTE basis, was $30.1 billion, $31.7 billion and "
            "$29.2 billion for the three months ended September 30, 2026, June 30, 2026 and "
            "September 30, 2025, respectively.")
#: A note under Shake Shack's tables: the quarter and the 39 weeks.
_SHAK_NOTE = ("Total revenue, which was $423.3 million and $1,207.6 million for the thirteen and "
              "thirty-nine weeks ended September 30, 2026, respectively")


@pytest.mark.parametrize("quote,value,unit", [
    (_BAC_FTE, 30.1, "billion"), (_BAC_FTE, 31.7, "billion"), (_BAC_FTE, 29.2, "billion"),
    (_SHAK_NOTE, 423.3, "million"), (_SHAK_NOTE, 1207.6, "million"),
])
def test_a_list_only_respectively_pairs_is_no_revenue(quote, value, unit):
    figures, reasons = _revenue(quote, value, unit)
    assert figures is None and reasons["revenue"] == "wrong_metric"


@pytest.mark.parametrize("header,row,value", [
    ("($ in millions, except per share data)", "Revenues: Product revenues $ 13,255 $ 13,920 (5)",
     13255),                                                                        # PFE
    ("Dollars and shares in millions, except per share data",
     "Revenues Revenues from franchised restaurants $ 4,487 $ 4,363 $ 124 3 %", 4487),  # MCD
    ("Dollars and shares in millions, except per share data",
     "Sales by Company-owned and operated restaurants 2,480 2,563 (83) (3)", 2480),  # MCD
    ("(in millions)", "Revenues Automotive $ 1,142 $ 839 $ 908", 1142),             # RIVN
    ("(in millions, except share and per share amounts)",
     "Revenue: Connected Fitness Products $ 148.5 $ 152.4", 148.5),                 # PTON
    ("(in millions, except employee data)",
     "Sales Online stores (1) $ 61,485 $ 67,407 $ 82,988", 61485),                  # AMZN
    ("($ in millions)", "Revenue excluding foreign exchange effect $ 64,714 $ 179,392", 64714),  # META
    ("(in millions)", "Revenues of Financial Products 1,028 912 2,932 2,678", 1028),  # CAT
    ("(in millions)", "Total Alliance revenues included above $ 2,851 $ 2,298", 2851),  # PFE
    ("(in millions)", "WW net sales -- TTM $ 670,038", 670038),                     # AMZN
])
def test_a_row_that_names_a_part_behind_its_revenue_word_is_no_revenue(header, row, value):
    figures, reasons = _revenue(row, value, "million", header)
    assert figures is None and reasons["revenue"] == "value_mismatch"


@pytest.mark.parametrize("header,row,value,expected", [
    ("Dollars in millions", "Revenues $ 7,157 $ 7,078 1 % 1 %", 7157, 7.157e9),     # MCD
    ("(in millions)", "TOTAL REVENUES 7,157 7,078 79 1", 7157, 7.157e9),            # MCD
    ("(in millions)", "Total revenue 559.2 550.8", 559.2, 559.2e6),                 # PTON
    ("(in millions)", "Revenues: Total revenues $ 16,583 $ 16,653 (0)", 16583, 16.583e9),
    ("(in millions)", "Total sales and revenues 20,682 17,638", 20682, 20.682e9),   # CAT
    ("($ in billions)", "Total revenue, net of interest expense $30.0 $29.1", 30.0, 30e9),  # BAC
    ("(in millions)", "Total revenue (GAAP) 14,100 12,200", 14100, 14.1e9),
    ("(in millions)", "Total Revenue(a) 1,621.6 1,470.6", 1621.6, 1.6216e9),
    ("(in millions)", "Net revenues (unaudited) 1,621.6 1,470.6", 1621.6, 1.6216e9),
    ("(in millions)", "Total net revenue (1) 1,502.0 1,210.6", 1502.0, 1.502e9),
])
def test_a_row_of_the_whole_company_keeps_passing_with_its_notes_and_its_heading(
        header, row, value, expected):
    unit = "billion" if "billions" in header else "million"
    figures, reasons = _revenue(row, value, unit, header)
    assert figures is not None and figures.revenue == expected, reasons
    assert reasons["revenue"] == "ok"


def test_a_total_named_behind_a_heading_is_the_companys_row():
    """The heading's word is the first revenue word, and a bare one the release prints with another
    figure may be a segment's: the word "Total" behind it is what says this row sums the company."""
    header = "(in millions) Revenues $ 1,000 $ 900 Revenues $ 2,000 $ 1,800"
    figures, reasons = _revenue("Revenues: Total revenues $ 16,583 $ 16,653", 16583, "million",
                                header)
    assert figures is not None and figures.revenue == 16.583e9, reasons


@pytest.mark.parametrize("sentence,quote", [
    ("Foreign exchange effect on 2026 revenue using 2025 rates 113 (2,320)",
     "Foreign exchange effect on 2026 revenue using 2025"),                         # META
    ("Third-quarter 2026 operational revenue reflected higher revenues",
     "Third-quarter 2026 operational revenue reflected"),                           # PFE
])
def test_a_year_is_no_figure_a_tables_header_can_scale(sentence, quote):
    figures, reasons = _verify(_in_a_release(sentence, "($ in millions)"),
                               revenue={"value": 2026, "unit": "million", "quote": quote})
    assert figures is None and reasons["revenue"] == "value_mismatch"


def test_a_row_with_no_revenue_word_in_front_of_its_number_stands_on_the_words_behind_it():
    figures, reasons = _revenue("$ 7,157 in revenue", 7157, "million", "(in millions)")
    assert figures is not None and figures.revenue == 7.157e9, reasons


def test_a_figure_in_front_of_a_capitalised_exclusion_is_no_gaap_eps():
    """JPM's second headline: the adjusted figure in the slot of the $5.57 the release reports."""
    headline = ("JPMORGANCHASE REPORTS THIRD-QUARTER 2026 NET INCOME OF $15.3 BILLION ($5.57 PER "
                "SHARE), NET INCOME OF $15.8 BILLION ($5.75 PER SHARE) EXCLUDING A SIGNIFICANT ITEM "
                "THIRD-QUARTER 2026 RESULTS")
    figures, reasons = _verify(_in_a_release(headline), eps_gaap_diluted={
        "value": 5.75, "quote": "NET INCOME OF $15.8 BILLION ($5.75 PER SHARE)"})
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"
    figures, reasons = _verify(_in_a_release(headline), eps_gaap_diluted={
        "value": 5.57, "quote": "NET INCOME OF $15.3 BILLION ($5.57 PER SHARE)"})
    assert figures is not None and figures.eps_gaap == 5.57, reasons


def test_the_gaap_figure_in_front_of_a_dash_and_an_exclusion_keeps_passing():
    """BAC's headline bullet: what follows the dash is the next clause's, and its adjusted words
    never reach back over it."""
    bullet = ("Net income of $6.9 billion or EPS of $0.91 \u2013 Excluding the regulatory settlement "
              "charge, adjusted net income of $8.1 billion or adjusted EPS of $1.082")
    for quote in ("EPS of $0.91", bullet):
        figures, reasons = _verify(_in_a_release(bullet),
                                   eps_gaap_diluted={"value": 0.91, "quote": quote})
        assert figures is not None and figures.eps_gaap == 0.91, reasons
    figures, reasons = _verify(_in_a_release(bullet),
                               eps_adjusted_diluted={"value": 0.91, "quote": bullet})
    assert figures is None and reasons["eps_adjusted_diluted"] == "not_adjusted"


#: Goldman Sachs: the quarter, the year-ago quarter and the quarter before.
_GS_EPS = ("Diluted earnings per common share (EPS)1 was $17.01 for the third quarter of 2026 "
           "compared with $12.25 for the third quarter of 2025 and $20.98 for the second quarter of "
           "2026.")
#: Amazon: this quarter's total and tail, then the year-ago quarter's.
_AMZN_EPS = ("Net income decreased to $20.7 billion in the third quarter, or $1.89 per diluted share, "
             "compared with $21.2 billion, or $1.95 per diluted share, in third quarter 2025.")


@pytest.mark.parametrize("quote,value,expected", [
    (_GS_EPS, 17.01, 17.01), (_GS_EPS, 12.25, None), (_GS_EPS, 20.98, None),
    (_AMZN_EPS, 1.89, 1.89), (_AMZN_EPS, 1.95, None),
])
def test_only_the_figure_in_front_of_a_comparison_is_the_quarters(quote, value, expected):
    figures, reasons = _eps("eps_gaap_diluted", quote, value)
    assert (None if figures is None else figures.eps_gaap) == expected, reasons


@pytest.mark.parametrize("row,value", [
    ("Earnings per share: Basic $ 2.11 $ 1.85", 2.11),                              # AAPL
    ("Earnings per share: Basic $ 3.86 $ 1.08", 3.86),                              # META
    ("Earnings per share: Basic $ 0.22 $ 0.03", 0.22),                              # PTON
    ("Earnings (loss) per share attributable to Intel\u2014basic $ 0.63", 0.63),     # INTC
    ("Basic net income per common share $ 2.89 $ 2.14", 2.89),                      # GOOGL
    ("Net income per share \u2014 basic $ 1.23", 1.23),                              # ROKU
    ("EARNINGS PER COMMON SHARE Basic3 $ 17.24", 17.24),                            # GS
    ("NET EARNINGS PER COMMON SHARE (1) Basic $ 2.00", 2.0),                        # PG
])
def test_a_tables_basic_row_is_no_diluted_eps(row, value):
    figures, reasons = _eps("eps_gaap_diluted", row, value)
    assert figures is None and reasons["eps_gaap_diluted"] == "wrong_metric"


@pytest.mark.parametrize("field,quote,value", [
    ("eps_gaap_diluted", "Currency-neutral EPS $ 1.99", 1.99),                      # PG
    ("eps_adjusted_diluted", "Currency-neutral EPS $ 1.99", 1.99),
    ("eps_gaap_diluted", "Adjusted Operational EPS (Diluted) $ 2.80 $ 2.59", 2.8),  # JNJ
    ("eps_gaap_diluted", "Core loss per share ($0.34) ($7.47)", -7.47),             # BA
    ("eps_adjusted_diluted", "Adjusted Operational EPS (Diluted)2,4 / Mid-point Change vs. Prior "
     "Year / Mid-point $10.95 \u2013 $11.05 / $11.00", 11.0),                         # JNJ
])
def test_a_row_that_is_no_gaap_eps_stands_in_no_slot_it_cannot_prove(field, quote, value):
    figures, reasons = _eps(field, quote, value)
    assert figures is None and reasons[field] == "wrong_metric"


@pytest.mark.parametrize("quote,value", [
    ("Diluted net income per common share $ 2.87 $ 2.12 (1) Non-GAAP measure.", 2.87),  # GOOGL
    ("Diluted net income per common share $ 2.87 $ 2.12 (1) Non-GAAP measure.", 2.12),
])
def test_the_note_under_a_table_names_no_basis_for_its_last_row(quote, value):
    figures, reasons = _eps("eps_adjusted_diluted", quote, value)
    assert figures is None and reasons["eps_adjusted_diluted"] == "not_adjusted"


def test_every_row_here_fits_the_size_of_a_quote():
    """One row or sentence each, as the note on top of this file says."""
    rows: Dict[str, Any] = dict.fromkeys(q for q, _ in _GAAP_ROWS + _ADJUSTED_ROWS)
    rows.update(dict.fromkeys(_OUTLOOKS + _NOT_OUTLOOKS))
    assert all(len(row) <= efs.PEG_SPAN_MAX_CHARS for row in rows)
