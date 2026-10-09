"""Model-facing Revenue Engine money carries the filer's reporting currency (2026-10-09).

TSM reports in TWD, and `revenue_engine.reporting_currency` has said so since the R1 round —
but every model-facing renderer still prefixed the segment figures with "$", so the model read
TWD 2.16T of revenue as $2.16T (≈30× too large). The rule, in every place a segment amount is
written for a model:
  * a known NON-USD code replaces the "$" with the code ("TWD 2.16T") and the block names the
    currency once, saying it is not converted;
  * USD and an UNKNOWN currency keep the "$" (what these renderers always printed).

Places: `narrative_prompts._digest_revenue_engine` and `_revenue_engine_analysis_note_prompt`
(through `_fmt_millions_usd`), `ticker_report_data_collector.build_financial_context`'s
"Revenue Segments" block (through `_format_money_compact`), and the PDF's revenue-engine
section, which prints shares only — pinned so an amount added later cannot slip a "$" in.

Hermetic: pure builders over in-memory inputs.
"""

from __future__ import annotations

import re

import pytest

from app.services.agents import narrative_prompts as NP
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.ticker_report_data_collector import (
    CollectedTickerData,
    _build_revenue_engine,
)

_SEGMENTS = [  # TSM-like, in the filer's own currency units (not millions yet)
    {"name": "High Performance Computing", "current_revenue": 1.24e12,
     "previous_revenue": 0.93e12, "total_revenue": 2.16e12},
    {"name": "Smartphone", "current_revenue": 0.75e12, "previous_revenue": 0.69e12,
     "total_revenue": 2.16e12},
    {"name": "IoT", "current_revenue": 1.3e11, "previous_revenue": 0.0,
     "total_revenue": 2.16e12},
]


def _engine(currency, *, eliminations=None):
    return _build_revenue_engine(
        [dict(s) for s in _SEGMENTS], fiscal_year="2025", total_revenue=2.16e12,
        intersegment_eliminations=eliminations, reporting_currency=currency,
    )


def _persona():
    from app.services.agents.persona_config import get_persona_config

    return get_persona_config("warren_buffett")


def _context(engine) -> str:
    out = CollectedTickerData(ticker="TSM", persona_key="warren_buffett")
    out.profile = {"companyName": "Taiwan Semiconductor", "sector": "Technology"}
    out.revenue_engine_partial = engine
    text = C.build_financial_context(out)
    return text.split("Revenue Segments", 1)[1].split("\n\n", 1)[0]


_DOLLAR_AMOUNT = re.compile(r"\$\s?\d")


# ── the formatters ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,cur,expected", [
    (2_160_000.0, "TWD", "TWD 2.16T"), (1_240_000.0, "twd", "TWD 1.24T"),
    (512.0, "JPY", "JPY 512M"), (0.25, "EUR", "EUR 250,000"), (-394.0, "TWD", "-TWD 394M"),
    (209586.0, "USD", "$209.6B"), (209586.0, None, "$209.6B"), (209586.0, "", "$209.6B"),
    (209586.0, "US$", "$209.6B"), (209586.0, "ßU", "$209.6B"), (209586.0, 840, "$209.6B"),
    (None, "TWD", None), ("x", "TWD", None), (float("nan"), "TWD", None),
    (float("inf"), "TWD", None), (True, "TWD", None),
])
def test_fmt_millions_with_a_currency(v, cur, expected):
    assert NP._fmt_millions_usd(v, cur) == expected


@pytest.mark.parametrize("v,cur,expected", [
    (2.16e12, "TWD", "TWD 2.2T"), (-3.94e8, "TWD", "-TWD 394M"), (0, "TWD", "TWD 0"),
    (250_000, "EUR", "EUR 250,000"), (1.2e9, "USD", "$1.2B"), (1.2e9, None, "$1.2B"),
    (0, None, "$0"), (None, "TWD", "N/A"), ("x", "TWD", "N/A"),
])
def test_format_money_compact_with_a_currency(v, cur, expected):
    assert C._format_money_compact(v, cur) == expected


def test_format_money_compact_default_is_unchanged_for_every_other_caller():
    for v in (-3.94e8, 0, 1.2e9, 2.1e12, 250_000):
        assert C._format_money_compact(v) == C._format_money_compact(v, None)
        assert C._format_money_compact(v).startswith(("$", "-$"))


# ── the report's financial context ────────────────────────────────────────────

def test_financial_context_segments_carry_a_non_usd_code():
    block = _context(_engine("TWD", eliminations=1.0e11))
    assert "(FY 2025; amounts in TWD, not converted)" in block
    assert "High Performance Computing: TWD 1.2T (prior year: TWD 930B)" in block
    assert "IoT: TWD 130B (prior year: n/a)" in block
    assert "Total revenue: TWD 2.2T" in block
    assert "Intersegment eliminations: -TWD 100B" in block
    assert not _DOLLAR_AMOUNT.search(block), block


def test_financial_context_usd_keeps_dollars():
    block = _context(_engine("USD"))
    assert "(FY 2025)" in block and "amounts in" not in block
    assert "High Performance Computing: $1.2T (prior year: $930B)" in block


@pytest.mark.parametrize("cur", [None, "garbage"])
def test_financial_context_unknown_currency_is_never_dressed_as_dollars(cur):
    """Final review 2026-10-09: an UNKNOWN engine currency (mixed codes, a failed income read, a
    collection cached before the field existed) printed "$" beside statement lines in the filer's
    own code. No symbol now, and the header says the currency is not confirmed."""
    engine = _engine("USD")
    engine["reporting_currency"] = cur          # a stored report may carry anything
    block = _context(engine)
    assert "amounts in the company's reporting currency, not confirmed — never assume US dollars" in block
    assert "High Performance Computing: 1.2T (prior year: 930B)" in block
    seg_block = block[block.index("High Performance Computing:"):]
    assert not _DOLLAR_AMOUNT.search(seg_block.split("\n\n", 1)[0]), block


# ── the narrative prompts ─────────────────────────────────────────────────────

def test_digest_names_and_prefixes_a_non_usd_currency():
    line = " ".join(NP._digest_revenue_engine({"revenue_engine": _engine("TWD")}))
    assert line.startswith("REVENUE ENGINE (amounts in TWD")
    assert "High Performance Computing TWD 1.24T (+33% YoY)" in line
    assert not _DOLLAR_AMOUNT.search(line), line


def test_digest_usd_is_unchanged():
    line = " ".join(NP._digest_revenue_engine({"revenue_engine": _engine("USD")}))
    assert line.startswith("REVENUE ENGINE: ")
    assert "High Performance Computing $1.24T (+33% YoY)" in line


@pytest.mark.parametrize("cur", [None, "usd$", 7])
def test_digest_unknown_currency_is_never_dressed_as_dollars(cur):
    engine = _engine("USD")
    engine["reporting_currency"] = cur
    line = " ".join(NP._digest_revenue_engine({"revenue_engine": engine}))
    assert line.startswith("REVENUE ENGINE (amounts in the company's reporting currency, not "
                           "confirmed — never assume US dollars): ")
    assert "High Performance Computing 1.24T (+33% YoY)" in line
    assert not _DOLLAR_AMOUNT.search(line), line


@pytest.mark.parametrize("cur", ["TWD", None, "garbage"])
def test_a_non_usd_or_unknown_analysis_note_states_no_money_amount(cur):
    """Final review 2026-10-09: the takeaway renders INSIDE the card whose rows every shipped iOS
    build prints with a hard-coded "$" — a "TWD 1.70T" note under a "$1.70T" row contradicted the
    card. Shares and YoY only, plus a rule that overrides the STYLE block's "cite a concrete
    number" (the cached evidence still lists amounts)."""
    engine = _engine("TWD", eliminations=1.0e11)
    engine["reporting_currency"] = cur
    prompt = NP._revenue_engine_analysis_note_prompt(_persona(), "EVIDENCE", {"revenue_engine": engine})
    seg_line = next(ln for ln in prompt.splitlines() if ln.startswith("SEGMENTS"))
    assert "High Performance Computing (" in seg_line and "% of revenue" in seg_line
    assert "1.24T" not in prompt and "TWD 1" not in prompt and "100.0B" not in prompt
    assert not _DOLLAR_AMOUNT.search(prompt), prompt
    assert NP._ENGINE_NO_AMOUNTS in prompt
    assert "GROSS of intersegment sales" in prompt, "the gross note survives without its amount"


def test_analysis_note_prompt_usd_is_unchanged():
    prompt = NP._revenue_engine_analysis_note_prompt(
        _persona(), "EVIDENCE", {"revenue_engine": _engine("USD")})
    assert "SEGMENTS: High Performance Computing $1.24T" in prompt
    assert "amounts in" not in prompt and NP._ENGINE_NO_AMOUNTS not in prompt


def test_analysis_note_prompt_with_no_segments_names_no_currency():
    prompt = NP._revenue_engine_analysis_note_prompt(
        _persona(), "EVIDENCE", {"revenue_engine": _engine("TWD") | {"segments": []}})
    assert "SEGMENTS: no segment breakdown available" in prompt


def test_a_malformed_engine_never_raises_in_the_currency_note():
    # An unreadable currency is UNKNOWN: the unconfirmed clause, never "" (which read as dollars).
    for engine in ({"reporting_currency": ["TWD"]}, None, {}):
        assert NP._engine_currency_note(engine) == NP._UNCONFIRMED_CURRENCY_NOTE   # type: ignore[arg-type]
    assert NP._engine_currency_note({"reporting_currency": "USD"}) == ""


# ── the PDF ───────────────────────────────────────────────────────────────────

def test_pdf_revenue_engine_carries_the_code_and_prints_no_dollar_amount():
    from app.services.pdf_report_service import build_context, render_html

    report = {"symbol": "TSM", "company_name": "Taiwan Semiconductor",
              "revenue_engine": _engine("twd")}
    ctx = build_context(report, None)
    assert ctx["revenue_engine"]["reporting_currency"] == "TWD"
    html = render_html(ctx)
    section = html.split("</span>The Revenue Engine", 1)[1].split("section-bar", 1)[0]
    assert "High Performance Computing" in section
    assert not _DOLLAR_AMOUNT.search(section), "the section prints shares, never an amount"
    assert build_context({"revenue_engine": _engine("garbage")}, None)[
        "revenue_engine"]["reporting_currency"] is None
    assert build_context({}, None)["revenue_engine"]["reporting_currency"] is None


# ── the statement lines (Revenue / Net Income / balance sheet / cash flow) ──────
#
# 2026-10-09 review: the segments block above printed "TWD 2.2T" while the statement lines a few
# rows earlier still printed TSM's TWD revenue as "$3.8T" — two currencies for one figure in the
# same Stage A context. Each statement row now carries its OWN `reportedCurrency`.

def _stmt_context(income=None, balance=None, cash_flow=None) -> str:
    out = CollectedTickerData(ticker="TSM", persona_key="warren_buffett")
    out.profile = {"companyName": "Taiwan Semiconductor", "sector": "Technology"}
    out.income = income or []
    out.balance = balance or []
    out.cash_flow = cash_flow or []
    return C.build_financial_context(out)


def _statements(text: str) -> str:
    """From the first statement line through the cash-flow block (the multiples follow)."""
    start = min(i for i in (text.find("\n["), text.find("\nTotal Assets:"),
                            text.find("\nOperating CF:")) if i >= 0)
    end = text.find("\nP/E:", start)
    return text[start:end if end >= 0 else None]


def _income(cur, year="2025", revenue=3.81e12, net=1.73e12):
    row = {"fiscalYear": year, "revenue": revenue, "netIncome": net}
    if cur is not ...:
        row["reportedCurrency"] = cur
    return row


_TWD_BALANCE = {"reportedCurrency": "TWD", "totalAssets": 7.0e12, "totalDebt": 9.5e11,
                "cashAndCashEquivalents": 2.1e12}
_TWD_CASH_FLOW = {"reportedCurrency": "TWD", "operatingCashFlow": 2.3e12,
                  "freeCashFlow": 1.0e12, "commonStockRepurchased": -3.94e8}


def test_a_twd_filers_statement_lines_carry_twd_never_dollars():
    text = _stmt_context([_income("TWD"), _income("TWD", "2024", 2.89e12, 1.17e12)],
                         [_TWD_BALANCE], [_TWD_CASH_FLOW])
    block = _statements(text)
    assert "[2025] Revenue: TWD 3.8T | Net Income: TWD 1.7T" in block
    assert "[2024] Revenue: TWD 2.9T | Net Income: TWD 1.2T" in block
    assert "Total Assets: TWD 7T" in block and "Total Debt: TWD 950B" in block
    assert "Cash: TWD 2.1T" in block
    assert "Operating CF: TWD 2.3T" in block and "Free CF: TWD 1T" in block
    assert "Buybacks: -TWD 394M" in block
    assert "(Statement amounts above are in TWD as reported, not converted to US dollars.)" \
        in block
    assert not _DOLLAR_AMOUNT.search(block), block


@pytest.mark.parametrize("cur", ["USD", "usd", None, ..., "", "garbage", "US$", "ßU", 840,
                                 ["TWD"], True])
def test_usd_or_unknown_statement_currency_prints_exactly_as_before(cur):
    """USD, a missing key and every garbage value keep the "$" these lines always printed, and
    no currency note is added — a USD filer's prompt does not change by one byte."""
    balance = dict(_TWD_BALANCE)
    cash_flow = dict(_TWD_CASH_FLOW)
    for row in (balance, cash_flow):
        if cur is ...:
            row.pop("reportedCurrency")
        else:
            row["reportedCurrency"] = cur
    block = _statements(_stmt_context([_income(cur)], [balance], [cash_flow]))
    assert "[2025] Revenue: $3.8T | Net Income: $1.7T" in block
    assert "Total Assets: $7T" in block and "Buybacks: -$394M" in block
    assert "not converted" not in block and "TWD" not in block


def test_each_row_uses_its_own_currency_when_a_filer_changed_currency():
    """A filer that switched its reporting currency: each year in its own code, and the note
    names only the non-USD ones."""
    text = _stmt_context([_income("TWD"), _income("USD", "2023", 7.0e10, 2.0e10)],
                         [dict(_TWD_BALANCE, reportedCurrency="JPY")], [])
    block = _statements(text)
    assert "[2025] Revenue: TWD 3.8T" in block
    assert "[2023] Revenue: $70B | Net Income: $20B" in block
    assert "Total Assets: JPY 7T" in block
    assert "are in JPY, TWD as reported" in block


def test_only_the_printed_rows_decide_the_note():
    """The note follows the rows the context PRINTS (three income years, the latest balance
    sheet and cash flow) — an older TWD row that is not printed adds no note."""
    income = [_income("USD", y) for y in ("2025", "2024", "2023")] + [_income("TWD", "2022")]
    text = _stmt_context(income, [dict(_TWD_BALANCE, reportedCurrency="USD")],
                         [dict(_TWD_CASH_FLOW, reportedCurrency="USD"),
                          dict(_TWD_CASH_FLOW)])
    assert "not converted" not in _statements(text) and "TWD" not in text


def test_a_malformed_statement_value_is_na_never_nan():
    rows = [_income("TWD", revenue=float("nan"), net=float("inf")),
            _income("TWD", "2024", revenue=True, net="not-a-number")]
    block = _statements(_stmt_context(rows))
    assert "[2025] Revenue: N/A | Net Income: N/A" in block
    assert "[2024] Revenue: N/A | Net Income: N/A" in block
    assert "nan" not in block.lower() and "$" not in block


@pytest.mark.parametrize("rows,expected", [
    ([{"reportedCurrency": "twd"}, {"reportedCurrency": " TWD "}], ["TWD"]),
    ([{"reportedCurrency": "USD"}, {}, {"reportedCurrency": None}], []),
    ([{"reportedCurrency": "EUR"}, {"reportedCurrency": "JPY"}], ["EUR", "JPY"]),
    ([None, "x", 7, {"reportedCurrency": "TWD"}], ["TWD"]),
    (None, []), ("TWD", []), ({"reportedCurrency": "TWD"}, []), ([], []),
])
def test_non_usd_statement_codes(rows, expected):
    assert C._non_usd_statement_codes(rows) == expected


@pytest.mark.parametrize("v", [float("nan"), float("inf"), float("-inf"), True, False,
                               10 ** 400, [1], {"a": 1}])
@pytest.mark.parametrize("cur", [None, "USD", "TWD"])
def test_format_money_compact_refuses_non_finite_and_bools(v, cur):
    assert C._format_money_compact(v, cur) == "N/A"
