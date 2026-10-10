"""Every earnings yield Cay AI reads is the inverse of the P/E printed beside it (2026-10-09).

THE BUG (post-deploy eval, case `follow-up-shape`): "Microsoft's current P/E ratio of 34.1 …
earnings yield, which is the inverse of the P/E, is 3.36%" — 1/34.1 is 2.93%. Chat holds P/E
figures on different bases: the Key Stats P/E (TTM) (the live price over EPS (TTM)), the Price
card's P/E (TTM, priced when the card was built), and whatever the screen's text says. The 3.36%
was the card's yield (1/29.73, the card's own P/E, recorded the same day) — and the card's yield
comes from another upstream endpoint than its P/E, so even the card's own pair is only inverse
while the two agree.

Pins:
  * `app.utils.earnings_yield`: the yield is derived from the DISPLAYED P/E (100 / P/E, two
    decimals; "negative (TTM loss)" for "Neg."; "N/A" with no P/E) — never relayed; the peer
    comparison printed in the row's name is recomputed with it; a failure drops the row (fail
    closed), never relays it.
  * the financials tool: Key Stats carries `earnings_yield` = 1 / its own P/E (TTM) (live, or
    the daily-close multiple for a two-currency filer — TSM, from the recorded FMP answers); the
    card's Earnings Yield is 1 / the card's P/E; the valuation basis names both bases and their
    as-of, and says never to pair across them.
  * the STOCK enrichment: the same derivation, and the Price basis label says so.
  * the trusted precedence rule forbids pairing a P/E with a yield from another source.
Outliers: negative and zero EPS, a missing P/E, a stale card whose yield drifted from its P/E,
legacy rows without `metric_key`, cached dict rows, malformed rows. Hermetic: no FMP, no
Supabase, no Gemini.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services import chat_financials_tool as cft
from app.services import stock_overview_service as sos
from app.services.chat_service import ChatService
from app.services.valuation_snapshot_service import _fmt_ratio
from app.utils import earnings_yield as ey

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "key_stats_pe" / "fmp_2026_10_09.json").read_text())


def _pe(value, name="P/E (1.13x sector avg 26.3)", level="industry", key="pe"):
    return SnapshotMetricResponse(name=name, value=value, metric_key=key, score=3,
                                  peer_level=level)


def _ey(value, name="Earnings Yield (1.20x sector avg 2.80%)", level="industry",
        key="earnings_yield"):
    return SnapshotMetricResponse(name=name, value=value, metric_key=key, score=None,
                                  peer_level=level)


def _card(metrics, computed_at="2026-10-09T13:00:00Z"):
    return SnapshotItemResponse(category="Price", rating=3, metrics=metrics,
                                computed_at=computed_at)


def _row(rows, key):
    return next(r for r in rows if getattr(r, "metric_key", None) == key)


def _pct(text):
    return float(text.rstrip("%"))


def _is_inverse(pe_text, ey_text) -> bool:
    """True when `ey_text` ("3.36%") is 100 / `pe_text` ("29.73") at two decimals."""
    return math.isclose(_pct(ey_text), round(100.0 / float(pe_text.replace(",", "")), 2),
                        abs_tol=1e-9)


# ── 1. the rule itself ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("29.73", 29.73), ("1,234.50", 1234.5), ("29.7x", 29.7), (" 34.10 ", 34.1), ("12", 12.0),
    ("100000", 100000.0),
])
def test_a_displayed_positive_multiple_is_read(text, expected):
    assert ey.parse_multiple(text) == expected


@pytest.mark.parametrize("text", [
    "—", "-", "N/A", "n/m", "", "Neg.", "-18.75", "0", "0.00", "nan", "inf", "1e400", "abc",
    "12,34.5", "29.73%", None, True, 29.73, ["29.73"],
])
def test_anything_else_is_not_a_multiple(text):
    assert ey.parse_multiple(text) is None


@pytest.mark.parametrize("pe,expected", [
    ("29.73", "3.36%"),     # MSFT's card P/E, recorded 2026-10-09
    ("34.1", "2.93%"),      # the eval's screen P/E: 2.93%, never 3.36%
    ("29.81", "3.35%"),     # MSFT's live Key Stats P/E the same day ($535.07 / 17.95)
    ("29.56", "3.38%"),     # TSM's one-currency multiple
    ("1,234.50", "0.08%"),
    ("0.50", "200.00%"),    # a tiny multiple: still exactly its inverse
    ("100000", "below 0.01%"),  # never "0.00%", which reads as zero earnings
])
def test_the_yield_is_the_inverse_of_the_displayed_pe(pe, expected):
    assert ey.earnings_yield_text(pe) == expected


@pytest.mark.parametrize("pe", ["Neg.", "neg", "NEG.", "negative", "-18.75", "-1,234.5x"])
def test_a_negative_pe_reads_as_a_loss_not_a_gap(pe):
    assert ey.earnings_yield_text(pe) == ey.NEGATIVE_EARNINGS == "negative (TTM loss)"


@pytest.mark.parametrize("pe", ["—", "N/A", "0", "0.00", "", None, 29.73, True, "nan", "abc"])
def test_no_readable_pe_means_no_yield(pe):
    # Zero EPS prints P/E "—" (`_fmt_ratio` treats 0 as absent), so it lands here too.
    assert ey.earnings_yield_text(pe) == ey.NOT_AVAILABLE


@pytest.mark.parametrize("value", [0.01, 0.5, 1.0, 7.25, 29.726, 99.999, 1234.567, 98765.4,
                                   -18.75, -0.01, 0.0, None])
def test_every_value_the_card_formatter_prints_is_read_back(value):
    text = _fmt_ratio(value)
    out = ey.earnings_yield_text(text)
    if value is None or value == 0:
        assert out == ey.NOT_AVAILABLE
    elif value < 0:
        assert out == ey.NEGATIVE_EARNINGS
    elif 100.0 / float(text) < 0.005:
        assert out == "below 0.01%", (text, out)
    else:
        assert _is_inverse(text, out), (text, out)


def test_the_negative_text_fits_the_tools_value_cap():
    # `chat_financials_tool._snapshot_metrics` caps a card value at 32 characters.
    assert len(ey.NEGATIVE_EARNINGS) <= 32 and len("below 0.01%") <= 32


# ── 2. card rows ──────────────────────────────────────────────────────────────────

def test_a_self_consistent_card_keeps_its_figures_and_its_other_rows():
    """MSFT, recorded 2026-10-09: ratios P/E 29.726, key-metrics yield 3.3647% (= 1/29.72)."""
    pb = SnapshotMetricResponse(name="P/B", value="9.10", metric_key="pb", score=3)
    rows = [_pe("29.73"), pb, _ey("3.36%")]
    out = ey.with_derived_earnings_yield(rows, "MSFT")
    assert out[0] is rows[0] and out[1] is pb, "only the yield row is rebuilt"
    assert (out[2].name, out[2].value) == ("Earnings Yield (1.20x sector avg 2.80%)", "3.36%")
    assert (out[2].metric_key, out[2].peer_level) == ("earnings_yield", "industry")


def test_a_stale_card_whose_yield_drifted_shows_the_inverse_of_its_own_pe(caplog):
    """The card's yield comes from another endpoint than its P/E: 4.50% beside a P/E of 25.00
    is not that P/E's inverse (4.00%). The peer multiple moves with the value."""
    rows = [_pe("25.00"), _ey("4.50%", name="Earnings Yield (1.50x sector avg 3.00%)")]
    with caplog.at_level(logging.INFO, logger="app.utils.earnings_yield"):
        out = ey.with_derived_earnings_yield(rows, "XYZ")
    row = _row(out, "earnings_yield")
    assert row.value == "4.00%"
    assert row.name == "Earnings Yield (1.33x sector avg 3.00%)"
    # 12.5% apart: the app's own card shows a pair that does not invert — loud, greppable.
    assert any(r.levelno == logging.WARNING and "[earnings-yield-drift] XYZ" in r.getMessage()
               and "4.50%" in r.getMessage() and "25.00" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("pe,card_yield,level,marker", [
    ("29.72", "3.37%", logging.INFO, "[earnings-yield-rebased]"),   # rounding: 1/29.72 = 3.36%
    ("—", "3.10%", logging.INFO, "[earnings-yield-rebased]"),       # dropped: no P/E to invert
    ("Neg.", "N/A", None, None),                                    # nothing was replaced
])
def test_how_a_rebase_is_logged(caplog, pe, card_yield, level, marker):
    with caplog.at_level(logging.INFO, logger="app.utils.earnings_yield"):
        ey.with_derived_earnings_yield([_pe(pe), _ey(card_yield)], "XYZ")
    records = [r for r in caplog.records if r.name == "app.utils.earnings_yield"]
    if marker is None:
        assert records == []
    else:
        assert [r.levelno for r in records] == [level] and marker in records[0].getMessage()


@pytest.mark.parametrize("card_yield,name,expected_name", [
    # The card's own form: no yield (≤ 0 prints N/A), only the median.
    ("N/A", "Earnings Yield (sector avg 3.00%)", "Earnings Yield (sector avg 3.00%)"),
    # The two endpoints disagree: a positive yield beside a negative P/E is not shown.
    ("2.10%", "Earnings Yield (0.70x sector avg 3.00%)", "Earnings Yield (sector avg 3.00%)"),
])
def test_a_loss_makers_card_reads_a_negative_yield(card_yield, name, expected_name):
    out = ey.with_derived_earnings_yield([_pe("Neg."), _ey(card_yield, name=name)], "MRNA")
    row = _row(out, "earnings_yield")
    assert row.value == "negative (TTM loss)" and row.name == expected_name


@pytest.mark.parametrize("rows", [
    # P/E unknown, the yield from the card's net income ÷ market cap fallback.
    [_pe("—"), _ey("3.10%", name="Earnings Yield (1.03x sector avg 3.00%)")],
    # No P/E row at all (a degraded or legacy card).
    [_ey("3.10%", name="Earnings Yield (1.03x sector avg 3.00%)")],
])
def test_no_pe_on_the_card_means_no_yield(rows):
    row = _row(ey.with_derived_earnings_yield(rows, "XYZ"), "earnings_yield")
    assert row.value == "N/A" and row.name == "Earnings Yield (sector avg 3.00%)"


def test_legacy_rows_without_a_metric_key_are_read_by_name():
    rows = [SnapshotMetricResponse(name="P/E (FWD)", value="10.00"),   # never the card's P/E
            SnapshotMetricResponse(name="P/E (1.13x sector avg 26.3)", value="25.00"),
            SnapshotMetricResponse(name="Earnings Yield (1.50x sector avg 3.00%)", value="4.50%")]
    out = ey.with_derived_earnings_yield(rows, "XYZ")
    assert out[2].value == "4.00%" and out[0] is rows[0] and out[1] is rows[1]


def test_a_metric_key_wins_over_a_lookalike_name():
    rows = [SnapshotMetricResponse(name="P/E", value="25.00", metric_key="pe"),
            SnapshotMetricResponse(name="Earnings Yield", value="9.99%", metric_key="fcf_yield")]
    out = ey.with_derived_earnings_yield(rows)
    assert out[1] is rows[1], "a row keyed as another metric is never rewritten"


def test_cached_dict_rows_are_read():
    rows = [{"name": "P/E", "value": "20.00", "metric_key": "pe"},
            {"name": "Earnings Yield", "value": "6.10%", "metric_key": "earnings_yield",
             "peer_level": None}]
    out = ey.with_derived_earnings_yield(rows)
    assert out[0] is rows[0] and out[1].value == "5.00%" and out[1].name == "Earnings Yield"


def test_a_card_with_no_yield_row_is_returned_as_it_came():
    rows = [SnapshotMetricResponse(name="Debt/Equity", value="1.50x", metric_key="debt_to_equity"),
            SnapshotMetricResponse(name="Current Ratio", value="0.87")]
    out = ey.with_derived_earnings_yield(rows)
    assert len(out) == 2 and all(a is b for a, b in zip(out, rows))


@pytest.mark.parametrize("metrics", [None, "29.73", 5, {"pe": "29.73"},
                                     [None, 5, {"name": None}, SimpleNamespace()]])
def test_malformed_input_never_raises(metrics):
    out = ey.with_derived_earnings_yield(metrics)
    assert isinstance(out, list)


def test_a_failure_drops_the_yield_row_and_never_relays_it(monkeypatch, caplog):
    def boom(_text):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(ey, "earnings_yield_text", boom)
    rows = [_pe("29.73"), _ey("3.36%")]
    with caplog.at_level(logging.WARNING, logger="app.utils.earnings_yield"):
        out = ey.with_derived_earnings_yield(rows, "MSFT")
    assert out == [rows[0]]
    assert any("left out" in r.getMessage() and "MSFT" in r.getMessage() for r in caplog.records)


# ── 3. the financials tool ───────────────────────────────────────────────────────

def _kf(**over):
    base = {"ticker": "MSFT", "company_name": "Microsoft Corporation",
            "rows": {"Market Cap": "$3.97T", "P/E (TTM)": "34.10", "P/E (FWD)": "28.40",
                     "EPS (TTM)": "15.69"},
            "unavailable": [], "live_price_ok": True, "statement_currency": "USD",
            "price_currency": "USD", "pe_basis": "live", "is_fund": False, "degraded": []}
    base.update(over)
    return base


def test_key_stats_carries_the_yield_of_its_own_pe():
    block = cft._key_stats_block(_kf(), cft._VALUATION_KEY_STATS)
    assert block["earnings_yield"] == "2.93%"
    assert _is_inverse(block["rows"]["P/E (TTM)"], block["earnings_yield"])
    assert block["basis"].endswith(cft._KEY_STATS_EARNINGS_YIELD)
    assert "live price over trailing-twelve-month GAAP diluted EPS" in block["basis"]
    assert "earnings_yield" not in block["rows"], "never presented as a Key Stats row"


def test_a_loss_makers_key_stats_yield_is_negative():
    block = cft._key_stats_block(_kf(rows={"P/E (TTM)": "Neg.", "EPS (TTM)": "-7.98"}),
                                 cft._VALUATION_KEY_STATS)
    assert block["earnings_yield"] == "negative (TTM loss)"


@pytest.mark.parametrize("over", [
    # Zero EPS: the builder prints "—" (a placeholder) — no P/E, so no yield.
    {"rows": {"P/E (TTM)": "—", "EPS (TTM)": "0.00", "Market Cap": "$1.0B"}},
    # The live price did not load: the P/E rows are withheld, so no yield either.
    {"rows": {"EPS (TTM)": "15.69"}, "unavailable": ["P/E (TTM)", "P/E (FWD)", "Market Cap"],
     "live_price_ok": False},
])
def test_no_pe_in_key_stats_means_no_yield(over):
    block = cft._key_stats_block(_kf(**over), cft._VALUATION_KEY_STATS)
    assert "earnings_yield" not in block
    assert cft._KEY_STATS_EARNINGS_YIELD not in block["basis"]


def test_the_valuation_basis_names_each_pe_basis_and_forbids_pairing_across_them():
    card = _card([_pe("29.73"), _ey("3.36%")])
    live = cft._valuation_block(card, _kf())["basis"]
    for phrase in ("priced when the card was built (as_of), not the live price",
                   "The card's Earnings Yield is 1 / the card's own P/E",
                   "The Key Stats P/E (TTM) and its earnings_yield use the live price",
                   "never pair a P/E from one with the yield from the other"):
        assert phrase in live, phrase
    assert "{" not in live and "}" not in live, "the slot is always filled"
    two_ccy = cft._valuation_block(card, _kf(pe_basis=cft._PE_BASIS_TWO_CURRENCIES))["basis"]
    assert "are priced at the latest daily close" in two_ccy and "use the live price" not in two_ccy
    # No key facts at all (that source failed): the default wording, never an unfilled slot.
    assert "use the live price" in cft._valuation_block(card, None)["basis"]


def test_a_stale_card_keeps_its_date_and_shows_its_own_pe_inverse():
    card = _card([_pe("25.00", name="P/E (0.95x sector avg 26.3)"),
                  _ey("4.50%", name="Earnings Yield (1.50x sector avg 3.00%)")],
                 computed_at="2026-10-01T12:00:00Z")
    block = cft._valuation_block(card, _kf())
    assert block["as_of"] == "2026-10-01T12:00:00Z"
    assert "Earnings Yield (1.33x industry avg 3.00%): 4.00%" in block["multiples"]
    assert "4.50%" not in json.dumps(block)


def test_a_card_with_no_pe_lists_its_yield_as_not_available():
    pb = SnapshotMetricResponse(name="P/B", value="9.10", metric_key="pb", score=3)
    block = cft._valuation_block(_card([_pe("—"), pb, _ey("3.10%")]), _kf())
    assert "3.10%" not in json.dumps(block)
    assert any(name.startswith("Earnings Yield") for name in block["not_available"])


# ── the whole tool result, as the model reads it ──

_SOURCES = ("key_facts", "growth", "margins", "health", "earnings", "estimates", "valuation",
            "segments", "dividends", "splits")


@pytest.fixture
def _tool_env(monkeypatch):
    cft._inflight.clear()
    cft._source_tasks.clear()
    monkeypatch.setattr(cft, "_BLOCK_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000)
    yield monkeypatch
    cft._inflight.clear()
    cft._source_tasks.clear()


def _install(monkeypatch, values):
    for name in _SOURCES:
        def make(spec=values.get(name)):
            async def fake(_sym):
                return spec
            return fake
        monkeypatch.setattr(cft, f"_load_{name}", make())


def _pairs(node, out):
    """Every (P/E text, yield text) pair a block puts side by side."""
    if isinstance(node, dict):
        if "earnings_yield" in node and isinstance(node.get("rows"), dict):
            out.append((node["rows"]["P/E (TTM)"], node["earnings_yield"]))
        multiples = node.get("multiples")
        if isinstance(multiples, list):
            pe = next((m.split(": ", 1)[1] for m in multiples if re.match(r"P/E\b", m)), None)
            for m in multiples:
                if m.startswith("Earnings Yield"):
                    out.append((pe, m.split(": ", 1)[1]))
        for value in node.values():
            _pairs(value, out)
    elif isinstance(node, list):
        for value in node:
            _pairs(value, out)
    return out


@pytest.mark.asyncio
async def test_the_evals_state_gives_each_pe_its_own_yield(_tool_env):
    """The eval's screen said P/E 34.1; the card said 29.73 / 3.36%. Each P/E now travels with
    its own inverse, and 3.36% never sits beside 34.10."""
    _install(_tool_env, {"key_facts": _kf(), "valuation": _card([_pe("29.73"), _ey("3.36%")])})
    out = await cft.fetch_company_financials("MSFT", "valuation")
    assert out["key_stats"]["earnings_yield"] == "2.93%"
    assert "Earnings Yield (1.20x industry avg 2.80%): 3.36%" in out["valuation"]["multiples"]
    pairs = _pairs(out, [])
    assert sorted(pairs) == [("29.73", "3.36%"), ("34.10", "2.93%")]
    assert all(_is_inverse(pe, y) for pe, y in pairs)


@pytest.mark.asyncio
async def test_the_summary_section_pairs_the_same_way(_tool_env):
    _install(_tool_env, {"key_facts": _kf(),
                         "valuation": _card([_pe("25.00"), _ey("4.50%")],
                                            computed_at="2026-10-01T12:00:00Z")})
    out = await cft.fetch_company_financials("MSFT", "summary")
    pairs = _pairs(out, [])
    assert ("34.10", "2.93%") in pairs and ("25.00", "4.00%") in pairs
    assert all(_is_inverse(pe, y) for pe, y in pairs)
    assert "4.50%" not in json.dumps(out)


# ── TSM: statements in TWD, shares in USD (the recorded FMP answers) ──

class _Price:
    def __init__(self, quote):
        self.quote = quote

    async def get_quote(self, _sym):
        return self.quote


@pytest.mark.asyncio
async def test_a_two_currency_filers_yield_is_the_inverse_of_its_daily_close_multiple(monkeypatch):
    rec = _FIXTURE["TSM"]
    monkeypatch.setattr(sos, "_utc_today_iso", lambda: "2026-10-09")
    bundle = {
        "profile": dict(rec["profile"], companyName="Taiwan Semiconductor Manufacturing"),
        "key_metrics": [], "income_quarterly": rec["income_quarterly"],
        "income_annual": rec["income_annual"], "balance_annual": [],
        "analyst_est": rec["analyst_est"],
        "ratios_ttm": sos._provider_pe_fields(rec["ratios_ttm"]),
        "shares_float": {}, "inst_ownership": [],
        # Present, so the bounded short-interest read (a network call) never runs.
        "short_interest": {"shares_short": 1.0e7, "settlement_date": "2026-09-30"},
    }
    svc = sos.StockOverviewService.__new__(sos.StockOverviewService)
    svc.fmp = svc.supabase = None
    svc.price = _Price({"price": rec["profile"]["price"], "currency": "USD"})

    async def _fund(_sym):
        return bundle
    monkeypatch.setattr(svc, "_get_fundamentals", _fund)

    facts = await svc.get_key_facts("TSM")
    assert facts["pe_basis"] == sos.PE_BASIS_PROVIDER == cft._PE_BASIS_TWO_CURRENCIES
    block = cft._key_stats_block(facts, cft._VALUATION_KEY_STATS)
    assert block["rows"]["P/E (TTM)"] == "29.56" and block["rows"]["EPS (TTM)"] == "TWD 434.95"
    assert block["earnings_yield"] == "3.38%"
    # Never the inverse of the USD price over the TWD EPS (452.69 / 434.95 = 1.04 → 96%).
    assert not block["earnings_yield"].startswith("96")
    assert block["basis"].startswith(cft._KEY_STATS_BASIS_TWO_CURRENCIES)
    assert block["basis"].endswith(cft._KEY_STATS_EARNINGS_YIELD)
    val = cft._valuation_block(_card([_pe("29.56"), _ey("3.38%")]), facts)
    assert "are priced at the latest daily close" in val["basis"]
    assert "use the live price" not in val["basis"]


# ── 4. the STOCK enrichment ───────────────────────────────────────────────────────

def _patch_price_card(monkeypatch, card):
    import app.services.growth_snapshot_service as gs
    import app.services.health_snapshot_service as hs
    import app.services.ownership_snapshot_service as os_
    import app.services.profitability_snapshot_service as ps
    import app.services.valuation_snapshot_service as vs

    def svc_for(method, value):
        async def _get(_t):
            return value
        return lambda: SimpleNamespace(**{method: _get})

    monkeypatch.setattr(vs, "get_valuation_snapshot_service",
                        svc_for("get_valuation_snapshot", card))
    for module, getter, method in (
        (ps, "get_profitability_snapshot_service", "get_profitability_snapshot"),
        (gs, "get_growth_snapshot_service", "get_growth_snapshot"),
        (hs, "get_health_snapshot_service", "get_health_snapshot"),
        (os_, "get_ownership_snapshot_service", "get_ownership_snapshot"),
    ):
        monkeypatch.setattr(module, getter, svc_for(method, None))


def _summary_svc():
    return ChatService.__new__(ChatService)


@pytest.mark.asyncio
async def test_the_enrichment_shows_the_card_yield_as_the_inverse_of_the_card_pe(monkeypatch):
    card = _card([_pe("25.00", name="P/E (0.95x sector avg 26.3)"),
                  _ey("4.50%", name="Earnings Yield (1.50x sector avg 3.00%)")],
                 computed_at="2026-10-01T16:00:00Z")
    _patch_price_card(monkeypatch, card)
    out = await _summary_svc()._get_snapshot_summary("XYZ")
    assert "P/E (0.95x industry avg 26.3): 25.00" in out
    assert "Earnings Yield (1.33x industry avg 3.00%): 4.00%" in out
    assert "4.50%" not in out
    assert ("(Basis: trailing-twelve-month multiples, priced when the card was built, not the "
            "live price; its Earnings Yield is 1 / its own P/E; as of Oct 1, 2026.)") in out
    # The wire object (what iOS shows) is untouched.
    assert card.metrics[1].value == "4.50%"


@pytest.mark.asyncio
@pytest.mark.parametrize("metrics,expected", [
    ([_pe("29.73"), _ey("3.36%")], "Earnings Yield (1.20x industry avg 2.80%): 3.36%"),
    ([_pe("Neg."), _ey("N/A", name="Earnings Yield (sector avg 3.00%)")],
     "Earnings Yield (industry avg 3.00%): negative (TTM loss)"),
    ([_pe("—"), _ey("3.10%", name="Earnings Yield", level=None)], "Earnings Yield: N/A"),
])
async def test_the_enrichment_covers_the_outliers(monkeypatch, metrics, expected):
    _patch_price_card(monkeypatch, _card(metrics))
    out = await _summary_svc()._get_snapshot_summary("XYZ")
    assert expected in out


# ── 5. the trusted rule ───────────────────────────────────────────────────────────

def test_the_precedence_rule_forbids_pairing_a_pe_with_another_sources_yield():
    rule = ChatService._DATA_PRECEDENCE_RULE
    assert ("An earnings yield is the inverse of the P/E it was computed from: pair each P/E "
            "only with its own yield, never with a yield from another source, date or price.") \
        in rule
    assert rule.index("name each basis rather than calling either one wrong") < \
        rule.index("An earnings yield is the inverse")


# ── 6. report chat: a STORED card's yield reaches the model only beside the P/E it inverts ──
#
# A research report stores its Valuation card as shown (label, value, history_key). One built
# before Price-card payload v8 can hold FMP's net income ÷ market cap beside the P/E (C: 13.75 and
# 8.09%, 1/13.75 = 7.27%). Report chat must not re-derive it (the report on screen says 8.09%)
# and must not relay the pair: the row is left out of the figures lead.

from app.services.chat_context_resolver import _report_figures_lead  # noqa: E402


def _report(pe, ey_value, *, keyed=True):
    def row(label, value, key):
        r = {"label": label, "value": value, "peer_level": "industry"}
        if keyed:
            r["history_key"] = key
        return r
    return {"symbol": "C", "fundamental_metrics": [{
        "title": "Valuation", "star_rating": 3, "quality_label": "Fair", "metrics": [
            row("P/E (0.95x sector avg 14.4)", pe, "pe"),
            row("P/B", "1.10", "pb"),
            row("Earnings Yield (1.10x sector avg 7.35%)", ey_value, "earnings_yield"),
        ]}]}


def _valuation_line(report):
    return next(line for line in _report_figures_lead(report) if line.startswith("Valuation card"))


@pytest.mark.parametrize("keyed", [True, False])
def test_report_chat_leaves_out_a_stored_yield_that_does_not_invert_its_pe(keyed, caplog):
    with caplog.at_level(logging.INFO, logger="app.utils.earnings_yield"):
        line = _valuation_line(_report("13.75", "8.09%", keyed=keyed))
    assert "8.09" not in line and "Earnings Yield" not in line
    assert "P/E 13.75" in line and "P/B 1.10" in line, "every other row is kept as stored"
    assert any("[earnings-yield-unpaired] C" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("pe,ey_value", [
    ("13.75", "7.27%"),      # a v8 card: exactly 1 / its P/E
    ("38.43", "2.61%"),      # pre-v8 AAPL: FMP's yield, 0.3% off 1/38.43 — within rounding
    ("29.73", "3.36%"),
])
def test_report_chat_keeps_a_stored_yield_that_inverts_its_pe(pe, ey_value):
    line = _valuation_line(_report(pe, ey_value))
    assert f"Earnings Yield {ey_value}" in line, "never re-derived: the stored figure, as shown"


@pytest.mark.parametrize("pe,ey_value,kept", [
    ("Neg.", "N/A", False),   # no figure to pair: nothing to relay (N/A is dropped as empty)
    ("—", "3.10%", False),    # a pre-v8 net income ÷ market cap yield beside no P/E
])
def test_report_chat_never_relays_a_yield_with_no_pe(pe, ey_value, kept):
    line = _valuation_line(_report(pe, ey_value))
    assert ("Earnings Yield" in line) is kept and "3.10" not in line


@pytest.mark.parametrize("pe,ey_value,expected", [
    ("29.73", "3.36%", True), ("38.43", "2.61%", True), ("70", "1.43%", True),
    ("13.75", "8.09%", False), ("71.30", "1.62%", False), ("34.1", "3.36%", False),
    ("Neg.", "3.36%", False), ("—", "3.36%", False), ("29.73", "N/A", False),
    (None, None, False), ("0.00", "1.00%", False),
])
def test_is_inverse_pair(pe, ey_value, expected):
    assert ey.is_inverse_pair(pe, ey_value) is expected


def test_the_stored_card_filter_fails_closed(monkeypatch):
    def boom(*_a):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(ey, "is_inverse_pair", boom)
    rows = [{"label": "P/E", "value": "13.75", "history_key": "pe"},
            {"label": "Earnings Yield", "value": "7.27%", "history_key": "earnings_yield"}]
    assert ey.without_unpaired_yield(rows, "C") == [rows[0]]
