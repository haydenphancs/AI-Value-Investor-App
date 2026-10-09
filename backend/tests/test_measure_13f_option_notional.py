"""`scripts/measure_13f_option_notional.py` — the read-only measurement behind the owner
decision on option rows in the whale 13F HOLDINGS (2026-10-09, option A: share positions only).

Until then both holdings builders (`WhaleService._build_holdings`,
`WhaleHydrator._build_13f_holdings`) summed every extract row, so put/call notional was in the
"13F Equity Portfolio" figure, the Current Holdings and every allocation derived from them. Both
now return `_whale_common.thirteen_f_holdings`. The script puts the old every-row rule (BEFORE,
frozen in the script) beside the PRODUCTION builders (NOW), so these tests pin both what the
change removed on each archetype and that production now reads share positions only.

Hermetic: the synthetic books in `tests/fixtures/thirteen_f_options/` (invented filers, real
FMP row shape), the recorded FMP extracts in `tests/fixtures/trillion_club/` as option-free
controls, and an in-memory fake FMP for the live path. Nothing here reaches FMP or Supabase.
"""

import ast
import asyncio
import json
import logging
import re
from pathlib import Path

import pytest

from scripts import measure_13f_option_notional as m

_BACKEND = Path(__file__).resolve().parents[1]
_SYNTHETIC = _BACKEND / "tests" / "fixtures" / "thirteen_f_options" / "synthetic_option_books.json"
_RECORDED = [
    _BACKEND / "tests" / "fixtures" / "trillion_club" / "extracts_2026.json",
    _BACKEND / "tests" / "fixtures" / "trillion_club" / "extracts_berkshire_2023.json",
]


def _books():
    return {e["cik"]: e for e in m.load_fixture(str(_SYNTHETIC))}


def _measure(entry):
    return m.measure_filing(
        entry["current"], entry["previous"],
        period_end=entry["period_end"], performance=entry.get("performance"),
    )


@pytest.fixture(scope="module")
def measured():
    return {cik: _measure(e) for cik, e in _books().items()}


# ── 1. Read-only guarantees ────────────────────────────────────────────────────────────


def test_the_tripwire_refuses_every_supabase_use(monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    m.install_supabase_tripwire()
    client = database._supabase_client
    for name in ("table", "rpc", "storage", "from_", "auth"):
        with pytest.raises(m.SupabaseRefused):
            getattr(client, name)


def test_the_script_has_no_write_path():
    # AST, so a comment or docstring can neither trip nor satisfy it. Every Supabase read or
    # write starts at `.table(` / `.rpc(` / `.storage`, so banning those attribute accesses
    # (plus the client getters and the writers' persistence helpers) covers every write path.
    tree = ast.parse((_BACKEND / "scripts" / "measure_13f_option_notional.py").read_text())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not attrs & {"table", "rpc", "storage", "from_", "_persist", "_sync_to_whale_tables",
                        "_upsert_trades", "_prune_stale_13f_trades"}
    assert not names & {"get_supabase", "sb_exec", "retry_idempotent_sync", "retry_idempotent_async",
                        "fetch_all_rows"}
    # Anti-vacuity: the scan does see the script's real attribute accesses.
    assert {"get_institutional_holdings", "_build_holdings", "_build_13f_holdings"} <= attrs


def test_the_key_scrub_filter_redacts_the_literal_key_and_the_query_param():
    key = "abcdef1234567890"
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1,
                            "GET /stable/x?apikey=%s failed (%s)", (key, key), None)
    m._KeyScrubFilter(key).filter(rec)
    assert key not in rec.getMessage()


# ── 2. The effect, on invented books (numbers pinned) ──────────────────────────────────


def test_a_concentrated_options_book_was_mostly_notional(measured):
    s1 = measured["0009999901"]
    v = s1["value"]
    # BEFORE (the every-row rule): option notional counted as holdings.
    assert v["before_total"] == pytest.approx(1_357_500_000)
    assert v["put"] == pytest.approx(1_080_000_000) and v["call"] == pytest.approx(210_000_000)
    assert v["option_pct"] == pytest.approx(95.03, abs=0.01)
    # NOW (both production builders): the five stock positions only.
    assert v["now_total"] == pytest.approx(67_500_000)
    assert v["change_pct"] == pytest.approx(-95.03, abs=0.01)
    th = s1["top_holding"]
    assert (th["before"]["ticker"], th["before"]["allocation"]) == ("PLTR", 66.3)
    assert th["before"]["mix"]["put"] == 100.0         # a put WAS the #1 "holding"
    assert (th["now"]["ticker"], th["now"]["allocation"]) == ("MOH", 29.63)
    assert th["changes"] is True
    assert [o["ticker"] for o in s1["option_only_in_top30"]] == ["PLTR", "NVDA", "PFE", "HAL"]
    assert s1["top30_left"] == ["PLTR", "NVDA", "PFE", "HAL"] and s1["top30_entered"] == []


def test_index_etf_options_dominate_a_market_maker_book(measured):
    s2 = measured["0009999902"]
    assert s2["value"]["option_pct"] == pytest.approx(87.79, abs=0.01)
    assert s2["top_holding"]["before"]["ticker"] == "SPY"
    assert s2["top_holding"]["now"]["ticker"] == "AAPL"
    left = set(s2["top30_left"])
    assert {"QQQ", "IWM", "TSLA", "XLF", "GLD"} <= left        # held only through options
    # Each ticker held only through options frees a slot for a share position.
    assert len(s2["top30_left"]) == len(s2["top30_entered"]) >= 10
    heavy = {o["ticker"] for o in s2["option_heavy_in_top30"]}
    assert {"SPY", "NVDA", "AMD"} <= heavy                         # some shares, mostly options


def test_modest_calls_reorder_but_keep_the_top_holding(measured):
    s3 = measured["0009999903"]
    assert s3["value"]["option_pct"] == pytest.approx(8.51, abs=0.01)
    assert s3["top_holding"]["changes"] is False
    assert s3["max_allocation_shift"]["ticker"] == "NVDA"
    assert s3["max_allocation_shift"]["points"] == pytest.approx(-3.31, abs=0.01)


def test_a_restating_amendment_was_counted_twice(measured):
    # No options at all: the every-row rule summed both accessions' rows; production now
    # reads the latest accession per CUSIP once.
    s4 = measured["0009999904"]
    assert s4["value"]["option_notional"] == 0
    assert s4["rows"]["accessions"] == 2
    assert s4["value"]["before_total"] == pytest.approx(4_470_000_000)
    assert s4["value"]["now_total"] == pytest.approx(2_220_000_000)
    assert s4["value"]["amendment_overlap"] == pytest.approx(2_250_000_000)


def test_a_book_with_no_share_position_now_reads_empty(measured):
    s5 = measured["0009999905"]
    assert s5["value"]["now_total"] == 0
    assert s5["value"]["principal"] == pytest.approx(12_000_000)
    assert s5["top_holding"]["now"] is None and s5["top_holding"]["changes"] is True
    assert s5["top_now"] == []


def test_trade_weights_now_use_the_share_book_denominator(measured):
    ta = measured["0009999901"]["trade_allocations"]
    assert ta["denominator_ratio_current"] == pytest.approx(20.11, abs=0.01)
    w = ta["max_new_allocation_shift"]
    assert (w["ticker"], w["before"], w["now"]) == ("MOH", 1.47, 29.63)


# ── 3. Invariants of the measurement itself ────────────────────────────────────────────


def test_the_composition_adds_up_to_the_before_figure(measured):
    for cik, r in measured.items():
        v = r["value"]
        parts = v["share"] + v["put"] + v["call"] + v["principal"]
        assert parts == pytest.approx(v["before_total"]), cik


def test_the_two_production_builders_agree_on_every_book(measured):
    # The live and nightly writers build the SAME holdings; the script flags any drift.
    assert {cik: r["builders_disagree"] for cik, r in measured.items()} == {
        cik: [] for cik in measured
    }


def test_production_holdings_are_the_share_positions_only():
    from app.services._whale_common import thirteen_f_holdings, thirteen_f_share_positions

    for e in _books().values():
        live, nightly = m.production_holdings(e["current"])
        assert live == nightly == thirteen_f_holdings(e["current"])
        positions = thirteen_f_share_positions(e["current"])
        want = sum(p["value"] for p in positions.values() if p["value"] > 0)
        assert sum(h["value"] for h in live) == pytest.approx(want)
        assert {h["ticker"] for h in live} <= set(positions)         # no options-only ticker


@pytest.mark.parametrize("path", _RECORDED, ids=lambda p: p.name)
def test_option_free_recorded_filings_are_unchanged(path):
    # Real FMP extracts (Berkshire with its confidential-treatment 13F-HR/A, the 2026 club
    # filers): no put/call or PRN rows, no CUSIP in two accessions — the share-only reading
    # must change nothing at all.
    for e in m.load_fixture(str(path)):
        r = _measure(e)
        v = r["value"]
        assert v["option_notional"] == 0 and v["principal"] == 0, e["name"]
        assert v["now_total"] == pytest.approx(v["before_total"]), e["name"]
        assert v["amendment_overlap"] == pytest.approx(0.0, abs=0.5), e["name"]
        assert r["top30_left"] == r["top30_entered"] == [], e["name"]
        assert r["max_allocation_shift"]["points"] == 0.0, e["name"]
        assert [h["ticker"] for h in r["top_before"]] == [h["ticker"] for h in r["top_now"]]


def test_the_two_writers_store_the_same_change_percent(measured):
    # Found by this measurement: `WhaleService._apply_change_percent` used to keep the LAST
    # previous-quarter row per symbol (and count symbol-less rows in its denominator) while
    # the nightly writer summed — S3's NVDA (a share row, then a call row) differed by ~10
    # pts, and whichever writer derived a quarter first was kept. Both read
    # `thirteen_f_holdings` for the previous quarter now.
    for cik, r in measured.items():
        if r["change_percent"]:
            assert r["change_percent"]["live_vs_nightly_now"]["points"] == 0.0, cik


def test_a_symbol_less_previous_row_no_longer_moves_the_live_change_percent():
    # Real Alphabet data (2026-Q1 carries a row with symbol None) moved every live-path
    # change_percent by 0.27 pts against the nightly writer.
    recorded = {e["cik"]: e for e in m.load_fixture(str(_RECORDED[0]))}
    r = _measure(recorded["0001652044"])
    assert r["change_percent"]["live_vs_nightly_now"]["points"] == 0.0


def test_malformed_rows_never_raise():
    rows = [
        "x", None, 7, {},
        {"symbol": "--", "shares": 1, "value": 1},
        {"symbol": "AAA", "shares": float("nan"), "value": float("inf")},
        {"symbol": "BBB", "shares": "12", "value": "1,000"},
        {"symbol": "CCC", "shares": 10, "value": 1_000, "putCallShare": "Put"},
        {"symbol": "DDD", "shares": 10, "value": 2_000, "sharesType": "PRN"},
        {"symbol": "EEE", "shares": 10, "value": 3_000},
    ]
    r = m.measure_filing(rows, rows, period_end="2026-06-30")
    assert r["rows"]["non_dict"] == 3
    assert r["value"]["before_total"] == pytest.approx(6_000)
    assert r["value"]["now_total"] == pytest.approx(3_000)
    assert m.measure_filing([], None)["value"]["before_total"] == 0


# ── 4. The holder-performance basis probe ──────────────────────────────────────────────


@pytest.mark.parametrize("mv,reported,share,expected", [
    (1_357_500_000, 1_357_500_000, 67_500_000, "includes_options"),
    (67_400_000, 1_357_500_000, 67_500_000, "shares_only"),
    (100_500_000, 101_000_000, 100_000_000, "indistinguishable"),   # totals < 2% apart
    (500_000_000, 1_357_500_000, 67_500_000, "neither"),
])
def test_the_basis_probe_names_the_total_fmp_reports(mv, reported, share, expected):
    perf = [{"date": "2026-03-31", "marketValue": 1.0},
            {"date": "2026-06-30", "marketValue": mv}]
    assert m.fmp_market_value_basis(perf, "2026-06-30", reported, share)["basis"] == expected


@pytest.mark.parametrize("perf", [None, [], [{"date": "2026-03-31", "marketValue": 5}],
                                  [{"date": "2026-06-30", "marketValue": float("nan")}],
                                  [{"date": "2026-06-30"}], "garbage"])
def test_the_basis_probe_never_guesses_from_another_quarter(perf):
    assert m.fmp_market_value_basis(perf, "2026-06-30", 10.0, 5.0) == {
        "basis": "no_row", "market_value": None,
    }


# ── 5. Sources: fixtures, registry, the live fetch ─────────────────────────────────────


def test_the_loader_reads_both_fixture_shapes():
    synthetic = m.load_fixture(str(_SYNTHETIC))
    assert len(synthetic) == 5 and all(e["period_end"] == "2026-06-30" for e in synthetic)
    recorded = {e["cik"]: e for e in m.load_fixture(str(_RECORDED[1]))}
    brk = recorded["0001067983"]
    assert (brk["period"], brk["previous_period"], brk["period_end"]) == ("2023-Q4", "2023-Q3", "2023-12-31")


def test_the_registry_lists_every_13f_filer_and_filters_by_cik():
    filers = m.registry_13f_filers()
    raw = json.loads(m.REGISTRY_PATH.read_text())
    assert len(filers) == sum(1 for r in raw if r.get("data_source") == "13f")
    only = m.registry_13f_filers(["1649339"])          # zero-padding tolerated
    assert [f["cik"] for f in only] == ["0001649339"]


class _FakeFMP:
    def __init__(self, *, dates, extracts, perf=None, fail=None, perf_fail=False):
        self.dates, self.extracts, self.perf = dates, extracts, perf or []
        self.fail, self.perf_fail = fail, perf_fail
        self.calls = []

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        self.calls.append(("dates", cik, strict))
        return self.dates

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        self.calls.append(("extract", cik, year, quarter, strict))
        if self.fail == (year, quarter):
            raise RuntimeError("boom")
        return self.extracts.get((year, quarter), [])

    async def get_institutional_performance(self, cik):
        self.calls.append(("perf", cik))
        if self.perf_fail:
            raise RuntimeError("perf down")
        return self.perf


_DATES = [{"date": "2026-06-30", "year": 2026, "quarter": 2},
          {"date": "2026-03-31", "year": 2026, "quarter": 1}]


def test_the_live_fetch_picks_quarters_as_the_writers_do_and_fetches_strictly():
    s1 = _books()["0009999901"]
    fmp = _FakeFMP(dates=_DATES, extracts={(2026, 2): s1["current"], (2026, 1): s1["previous"]},
                   perf=[{"date": "2026-06-30", "marketValue": 1_357_500_000}])
    filing = asyncio.run(m.fetch_filing(fmp, "0009999901"))
    assert (filing["period"], filing["previous_period"], filing["period_end"]) == ("2026-Q2", "2026-Q1", "2026-06-30")
    assert ("extract", "0009999901", 2026, 2, True) in fmp.calls
    assert ("extract", "0009999901", 2026, 1, True) in fmp.calls
    assert ("dates", "0009999901", True) in fmp.calls
    r = m.measure_entry({"name": "x", "cik": "0009999901", **filing})
    assert r["measure"]["fmp_market_value"]["basis"] == "includes_options"


def test_a_failed_extract_fails_the_filer_but_a_failed_probe_does_not():
    s1 = _books()["0009999901"]
    extracts = {(2026, 2): s1["current"], (2026, 1): s1["previous"]}
    with pytest.raises(RuntimeError):
        asyncio.run(m.fetch_filing(_FakeFMP(dates=_DATES, extracts=extracts, fail=(2026, 1)), "c"))
    filing = asyncio.run(m.fetch_filing(_FakeFMP(dates=_DATES, extracts=extracts, perf_fail=True), "c"))
    assert filing["performance"] is None and filing["current"]
    assert asyncio.run(m.fetch_filing(_FakeFMP(dates=[], extracts={}), "c")) is None


# ── 6. The CLI, both modes ─────────────────────────────────────────────────────────────


def test_fixture_mode_end_to_end(tmp_path, monkeypatch, capsys):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    monkeypatch.setattr(m, "configure_logging", lambda secret: None)
    out = tmp_path / "m.json"
    code = asyncio.run(m.main(["--fixture", str(_SYNTHETIC), "--json", str(out), "--top", "3"]))
    assert code == 0
    assert isinstance(database._supabase_client, m._SupabaseTripwire)   # armed before any work
    text = capsys.readouterr().out
    assert "SYNTHETIC S1" in text and "SUMMARY — 5 filer(s) measured, 0 failed" in text
    assert re.search(r"#1 holding changes: 3\b", text)
    data = json.loads(out.read_text())
    assert [r["cik"] for r in data] == [f"000999990{i}" for i in range(1, 6)]


def test_live_mode_scrubs_the_key_from_the_report_and_the_json(tmp_path, monkeypatch, capsys):
    import app.database as database
    import app.integrations.fmp as fmp_mod
    from app.config import settings

    key = "live-test-key-0123456789"
    monkeypatch.setattr(settings, "FMP_API_KEY", key)
    monkeypatch.setattr(database, "_supabase_client", None)
    monkeypatch.setattr(m, "configure_logging", lambda secret: None)

    class _Leaky(_FakeFMP):
        async def get_institutional_filing_dates(self, cik, *, strict=False):
            raise RuntimeError(f"GET https://x/stable/institutional-ownership/dates?apikey={key} -> 500 ({key})")

    async def _close():
        return None

    monkeypatch.setattr(fmp_mod, "get_fmp_client", lambda: _Leaky(dates=[], extracts={}))
    monkeypatch.setattr(fmp_mod, "close_fmp_client", _close)
    out = tmp_path / "live.json"
    code = asyncio.run(m.main(["--cik", "0001649339", "--json", str(out)]))
    assert code == 1                                   # a failed filer is not a clean run
    printed = capsys.readouterr().out
    assert "ERROR: RuntimeError" in printed
    assert key not in printed and key not in out.read_text()
