"""Report-side cleanups found in wave 1 (2026-10-08).

1. "Insider Ownership" — the Insiders & Ownership snapshot's WIRE name — is 100% minus the free
   float: insiders AND strategic holders (a parent company, a founder's trust, a government
   stake), not what insiders own. The report's two model-facing metric renderers — the CARD
   VALUES block of the collector's financial context and the thesis / critical-factors digest —
   read every metric name through ONE helper, `narrative_prompts.report_model_metric_name`,
   which peer-words it and renames that metric for the MODEL only. The four report cards do
   not carry it today; this pins that, should one ever arrive, the model reads the honest label
   while the wire label (what iOS decodes, what stored reports hold) stays "Insider Ownership".
   Same words as chat (`chat_service._CHAT_INSIDER_OWNERSHIP_LABEL`, read here by AST so a
   half-edited chat module cannot break this file).
2. The Revenue Engine names the currency its figures are reported in
   (`_engine_reporting_currency` + `_currency_code`): the edge cases live here, the wire
   contract (present / absent / garbage through `_build_sections` + `assemble_report`) in
   `test_ticker_report_schema_parity.py`, whose autouse fixture stubs the benchmark lookups.
   Fix round (2026-10-08): `_currency_code` follows the overview's rule (trim, 3 ASCII
   letters, upper-cased — pinned equal on one corpus); a malformed row and contradicting
   duplicate rows for one year log WARNING, a currency history that changed stays INFO; the
   collector imports narrative_prompts lazily only.

Hermetic: pure builders over in-memory inputs, no network, no Supabase.
"""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.schemas.ticker_report import RevenueEngineResponse
from app.services.agents import narrative_prompts as NP
from app.services.agents import ticker_report_data_collector as C

LABEL = "Held outside the public float (insiders + strategic holders)"
_BACKEND = Path(__file__).resolve().parents[1]


# ═══════════════════════════════════════════════════════════════════════════
# 1. "Insider Ownership" is renamed for the REPORT MODEL only
# ═══════════════════════════════════════════════════════════════════════════


def _m(name, peer_level=None):
    return SimpleNamespace(name=name, peer_level=peer_level)


@pytest.mark.parametrize("name", [
    "Insider Ownership", " Insider Ownership ", "insider ownership", "INSIDER  OWNERSHIP",
    "Insider\tOwnership\n",
])
def test_the_float_complement_is_renamed_for_the_model(name):
    assert NP.report_model_metric_name(_m(name)) == LABEL
    # The peer level never changes an ownership row's rename.
    assert NP.report_model_metric_name(_m(name, "industry")) == LABEL


@pytest.mark.parametrize("name", [
    "Institutional Ownership", "Insider Buying", "Top 10 Holders", "ROE",
    "Insider Ownership Change", "Insider Ownership (12 mo)", "Ownership",
])
def test_every_other_metric_keeps_its_name(name):
    assert NP.report_model_metric_name(_m(name)) == name


def test_the_helper_still_peer_words_an_industry_median():
    assert NP.report_model_metric_name(_m("P/E (1.30x sector avg 22.4)", "industry")) \
        == "P/E (1.30x industry avg 22.4)"
    assert NP.report_model_metric_name(_m("P/E (1.30x sector avg 22.4)", "sector")) \
        == "P/E (1.30x sector avg 22.4)"


@pytest.mark.parametrize("metric,expected", [
    (_m(None), ""), (_m(42), "42"), (_m(""), ""), (object(), ""),
    (_m(float("nan")), "nan"), (_m(True), "True"),
])
def test_an_odd_name_never_raises(metric, expected):
    assert NP.report_model_metric_name(metric) == expected


def test_a_huge_name_passes_through_unchanged():
    huge = "x" * 1_000_000
    assert NP.report_model_metric_name(_m(huge)) == huge


def test_the_label_matches_chat_and_names_no_vendor():
    tree = ast.parse((_BACKEND / "app/services/chat_service.py").read_text())
    chat_label = next(
        node.value.value for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_CHAT_INSIDER_OWNERSHIP_LABEL"
                for t in node.targets)
        and isinstance(node.value, ast.Constant)
    )
    assert NP.REPORT_INSIDER_OWNERSHIP_LABEL == chat_label == LABEL
    low = LABEL.lower()
    for vendor in ("fmp", "financial modeling prep", "gemini", "google", "brave", "openai"):
        assert vendor not in low


def test_the_digest_reads_the_honest_label_and_leaves_the_card_alone():
    metric = {"label": "Insider Ownership", "value": "12.4%"}
    report = {"fundamental_metrics": [{"title": "Ownership", "star_rating": 0, "metrics": [
        metric, {"label": "Institutional Ownership", "value": "61.0%"},
    ]}]}
    text = "\n".join(NP._digest_fundamentals(report))
    assert f"{LABEL} 12.4%" in text
    assert "Insider Ownership" not in text
    assert "Institutional Ownership 61.0%" in text
    assert metric["label"] == "Insider Ownership", "the stored card is never rewritten"


def test_the_digest_survives_a_missing_or_odd_label():
    report = {"fundamental_metrics": [{"title": "Health", "star_rating": 3, "metrics": [
        {"value": "1.2"}, {"label": None, "value": "3"}, {"label": 7, "value": "4"}, "junk",
    ]}]}
    text = "\n".join(NP._digest_fundamentals(report))
    assert text.startswith("FUNDAMENTALS & GROWTH: Health 3/5 [")


def _snap(*metrics, rating=3):
    return SnapshotItemResponse(
        category="Financial Health", rating=rating,
        metrics=[SnapshotMetricResponse(name=n, value=v) for n, v in metrics],
    )


def test_the_card_values_block_reads_the_honest_label_and_the_wire_name_is_untouched():
    snap = _snap(("Insider Ownership", "12.4%"), ("Current Ratio", "1.2"))
    out = SimpleNamespace(snap_profitability=None, snap_growth=None, snap_valuation=None,
                          snap_health=snap)
    text = C._format_snapshot_card_values(out)
    assert f"  {LABEL}: 12.4%" in text
    assert "Insider Ownership" not in text
    assert "  Current Ratio: 1.2" in text
    assert snap.metrics[0].name == "Insider Ownership", "the snapshot is never rewritten"


def test_the_wire_card_label_stays_insider_ownership():
    """What iOS decodes and stored reports hold: `_snapshot_to_card` keeps the wire name."""
    card = C._snapshot_to_card("Health", _snap(("Insider Ownership", "12.4%")))
    assert card["metrics"][0]["label"] == "Insider Ownership"


def _called_names(fn) -> set:
    """Names a function CALLS (AST: comments and docstrings can never satisfy it)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


@pytest.mark.parametrize("fn", [NP._digest_fundamentals, C._format_snapshot_card_values],
                         ids=["digest", "card_values"])
def test_both_report_model_renderers_go_through_the_one_helper(fn):
    names = _called_names(fn)
    assert "report_model_metric_name" in names
    assert "peer_worded_metric_name" not in names, "a bare peer-worded name skips the renames"


def _module_level_imports_of(tree: ast.Module, module: str) -> list:
    """Top-level `import` / `from … import` statements naming `module` (AST: a comment or a
    function-local lazy import can never satisfy or trip it)."""
    hits = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == module:
            hits.append(node.lineno)
        elif isinstance(node, ast.Import) and any(a.name == module for a in node.names):
            hits.append(node.lineno)
        elif isinstance(node, (ast.If, ast.Try)):   # `if TYPE_CHECKING:` / try-import blocks
            hits.extend(_module_level_imports_of(ast.Module(body=node.body, type_ignores=[]),
                                                 module))
    return hits


_NP_MODULE = "app.services.agents.narrative_prompts"


def test_the_collector_imports_narrative_prompts_lazily_only():
    """narrative_prompts is heavy (the Gemini client, persona_config): the collector reaches it
    only inside the functions that need it — the card-values block and the DCF line — as its
    own comments say (2026-10-08 R1 fix round)."""
    src = inspect.getsource(C)
    assert _module_level_imports_of(ast.parse(src), _NP_MODULE) == []
    lazy = ast.parse(textwrap.dedent(inspect.getsource(C._format_snapshot_card_values)))
    assert any(isinstance(n, ast.ImportFrom) and n.module == _NP_MODULE
               and any(a.name == "report_model_metric_name" for a in n.names)
               for n in ast.walk(lazy))
    # Non-vacuous: the guard sees a module-level import, and ignores one in a comment.
    assert _module_level_imports_of(
        ast.parse(f"import re\nfrom {_NP_MODULE} import report_model_metric_name\n"),
        _NP_MODULE) == [2]
    assert _module_level_imports_of(
        ast.parse(f"# from {_NP_MODULE} import x\ndef f():\n    from {_NP_MODULE} import x\n"),
        _NP_MODULE) == []


# ═══════════════════════════════════════════════════════════════════════════
# 2. The Revenue Engine's reporting currency
# ═══════════════════════════════════════════════════════════════════════════


_CURRENCY_CASES = [
    ("USD", "USD"), ("TWD", "TWD"), ("EUR", "EUR"), (" TWD\n", "TWD"),
    ("usd", "USD"), ("Usd", "USD"), (" twd\t", "TWD"),     # case: upper-cased, as the overview
    ("USD" + " " * 40, "USD"),                             # padding is trimmed, not a refusal
    ("US$", None), ("N/A", None), ("", None), ("   ", None), ("U S", None), ("U1D", None),
    ("TWDX", None), ("TW", None), ("T W", None), ("ÜSD", None), ("ＵＳＤ", None),
    ("U" * 1_000_000, None), (" " * 1_000_000, None),
    (None, None), (True, None), (False, None), (840, None), (840.0, None),
    (float("nan"), None), (float("inf"), None), (["USD"], None), ({"code": "USD"}, None),
    (b"USD", None),
]


@pytest.mark.parametrize("raw,expected", _CURRENCY_CASES)
def test_currency_code_accepts_only_three_ascii_letters_upper_cased(raw, expected):
    assert C._currency_code(raw) == expected


@pytest.mark.parametrize("raw", ["ßU", "ﬁX", "ﬀA", "ﬆD", "ǆA", "ŉU"])
def test_a_letter_that_upper_cases_into_ascii_is_never_a_code(raw):
    """"ß".upper() == "SS", "ﬁ".upper() == "FI": a 2-character non-ASCII value must not become
    a 3-letter code ("ßU" → "SSU"). The shared rule (`app.utils.currency.currency_code`, which
    the collector, the overview and the chat resolver all wrap since 2026-10-09) checks ASCII
    BEFORE upper-casing; the first four are the ones the overview's own helper used to accept."""
    assert C._currency_code(raw) is None


@pytest.mark.parametrize("raw,expected", _CURRENCY_CASES)
def test_the_report_and_the_overview_read_one_feed_value_alike(raw, expected):
    """One rule for the same FMP `reportedCurrency`: the report's Revenue Engine code and the
    overview / financials currency (`stock_overview_service._currency_code`) never disagree
    on these inputs (2026-10-08 R1 fix round: the collector refused "usd" the overview read as
    "USD"). Since 2026-10-09 both wrap `app.utils.currency.currency_code`, so the case-folding
    expansions above are refused by the overview too (`tests/test_currency_utils.py`)."""
    from app.services import stock_overview_service as overview

    assert C._currency_code(raw) == overview._currency_code(raw) == expected


def _inc(year, currency, **extra):
    row = {"fiscalYear": year, "date": f"{year}-12-31", "revenue": 1.0e9}
    if currency is not _MISSING:
        row["reportedCurrency"] = currency
    row.update(extra)
    return row


_MISSING = object()


def test_the_engine_year_decides():
    rows = [_inc("2025", "TWD"), _inc("2024", "USD")]
    assert C._engine_reporting_currency(rows, "2025") == "TWD"
    assert C._engine_reporting_currency(rows, "2024") == "USD"
    assert C._engine_reporting_currency(list(reversed(rows)), "2025") == "TWD"  # order-free
    assert C._engine_reporting_currency(rows, " 2025 ") == "TWD"


def test_an_int_fiscal_year_matches_its_string():
    rows = [{"fiscalYear": 2025, "reportedCurrency": "TWD"},
            {"fiscalYear": 2024, "reportedCurrency": "USD"}]
    assert C._engine_reporting_currency(rows, "2025") == "TWD"


def _currency_records(caplog, needle):
    return [r for r in caplog.records
            if "[report-revenue-engine-currency]" in r.getMessage() and needle in r.getMessage()]


def test_with_no_year_match_only_a_unanimous_code_is_used(caplog):
    same = [_inc("2025", "TWD"), _inc("2024", "TWD")]
    mixed = [_inc("2025", "TWD"), _inc("2024", "USD")]
    for fy in (None, "", "2026"):
        assert C._engine_reporting_currency(same, fy) == "TWD"
        with caplog.at_level(logging.INFO):
            assert C._engine_reporting_currency(mixed, fy, "TSM") is None
    records = _currency_records(caplog, "mixed reporting currencies")
    assert records and all("TSM" in r.getMessage() for r in records)
    # A filer whose currency changed over the years is a real history, not bad data: INFO.
    assert {r.levelno for r in records} == {logging.INFO}


def test_a_garbage_code_on_the_year_row_falls_back_to_the_unanimous_one():
    rows = [_inc("2025", "n/a"), _inc("2024", "TWD"), _inc("2023", "TWD")]
    assert C._engine_reporting_currency(rows, "2025") == "TWD"
    rows = [_inc("2025", _MISSING), _inc("2024", "TWD"), _inc("2023", "JPY")]
    assert C._engine_reporting_currency(rows, "2025") is None


def test_duplicate_year_rows_that_disagree_are_unknown(caplog):
    rows = [_inc("2025", "TWD"), _inc("2025", "USD"), _inc("2024", "TWD")]
    with caplog.at_level(logging.INFO):
        assert C._engine_reporting_currency(rows, "2025", "TSM") is None
    records = _currency_records(caplog, "disagree")
    assert len(records) == 1
    # Contradicting rows for ONE fiscal year are bad upstream data that drop the label: WARNING.
    assert records[0].levelno == logging.WARNING
    assert "TSM" in records[0].getMessage() and "FY2025" in records[0].getMessage()
    assert "TWD" in records[0].getMessage() and "USD" in records[0].getMessage()
    agree = [_inc("2025", "TWD"), _inc("2025", "TWD"), _inc("2024", "USD")]
    assert C._engine_reporting_currency(agree, "2025") == "TWD"


@pytest.mark.parametrize("income", [
    None, [], (), {}, "TWD", 7, {"fiscalYear": "2025", "reportedCurrency": "TWD"},
    [None, "row", 5, ["TWD"]],
])
def test_no_usable_rows_is_unknown(income):
    assert C._engine_reporting_currency(income, "2025") is None


def test_malformed_rows_are_skipped_never_raised(caplog):
    rows = [None, "junk", 5,
            {"date": 20250101, "reportedCurrency": "TWD"},     # int date, no fiscalYear
            {"date": None, "reportedCurrency": "TWD"},
            {"reportedCurrency": "TWD"}]
    with caplog.at_level(logging.INFO):
        assert C._engine_reporting_currency(rows, "2025", "TSM") == "TWD"  # unanimous fallback
    # The unreadable year is a malformed upstream row: one WARNING naming ticker, FY, error.
    records = _currency_records(caplog, "year unreadable")
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    msg = records[0].getMessage()
    assert "TSM" in msg and "FY2025" in msg and "TypeError" in msg
    caplog.clear()
    with caplog.at_level(logging.INFO):
        assert C._engine_reporting_currency(rows, None, "TSM") == "TWD"
    assert _currency_records(caplog, "year unreadable") == [], "no year asked, nothing to match"


def test_many_rows_stay_linear_and_correct():
    rows = [_inc(str(3000 + i), "TWD") for i in range(20_000)] + [_inc("2025", "USD")]
    assert C._engine_reporting_currency(rows, "2025") == "USD"
    assert C._engine_reporting_currency(rows, "99999") is None   # mixed, no match


def _segs():
    return [{"name": "A", "current_revenue": 3e9, "previous_revenue": 2e9, "total_revenue": 4e9},
            {"name": "B", "current_revenue": 1e9, "previous_revenue": 1e9, "total_revenue": 4e9}]


@pytest.mark.parametrize("segments", [_segs(), []], ids=["segments", "empty"])
def test_the_engine_carries_the_code_and_re_guards_it(segments):
    assert C._build_revenue_engine(segments, reporting_currency="TWD")["reporting_currency"] \
        == "TWD"
    for junk in ("US$", 5, True, float("nan"), "Z" * 10_000, "ßU", None):
        engine = C._build_revenue_engine(segments, reporting_currency=junk)
        assert engine["reporting_currency"] is None
        RevenueEngineResponse.model_validate(engine)
    # A non-canonical caller value is normalised on the way to the wire, never passed raw.
    assert C._build_revenue_engine(segments, reporting_currency=" twd ")["reporting_currency"] \
        == "TWD"
    # A caller that never passes it still gets the key, as None (back-compatible signature).
    assert C._build_revenue_engine(segments)["reporting_currency"] is None

