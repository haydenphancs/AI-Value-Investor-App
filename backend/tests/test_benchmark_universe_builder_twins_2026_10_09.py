"""One vote per set of statements: the builder's statement-twin pass (decision D3, 2026-10-08).

FMP serves a company's statements under listings that are not its common — a note (BNH), a
preferred (STRC), OP units (FISK), a re-pointed SPAC symbol (APXT), a tracking series
(FWONA) — so the same statements vote twice in a median. The builder now fetches each kept
row's `ratios-ttm` and treats rows whose five price-free ratios are EXACTLY equal (with at
least 3 informative non-zero values) as one company:

  * inside an industry the most liquid listing votes (`_vote_order`), each drop on its own
    INFO line as `same_statements`;
  * across industries every member is KEPT and the group named at WARNING (the owner's B2
    call: which class belongs to which industry is FMP's call);
  * a failed call keeps its row (WARNING); more than 1% failed fails the build (exit 1);
  * `[]` (no statements at FMP) is named in one WARNING; `--skip-twin-scan` opts out.

Hermetic: pure functions, and `main()` against a fake FMP client (nothing reaches FMP).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

import scripts.build_benchmark_universe as bu
from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException

FLOOR = 500_000_000
FAKE_KEY = "TESTKEY0123456789abcdefNOTREAL"

# A real company's ratios-ttm (CRBG, probe 2026-10-08): five informative values.
SHARED = {"grossProfitMarginTTM": 0.8671135227151825, "operatingProfitMarginTTM": 0.11469305245238855,
          "netProfitMarginTTM": 0.09649962761995957, "currentRatioTTM": 6.2536,
          "debtToEquityRatioTTM": 1.0205614496291429}


def _row(sym: str, name: Optional[str] = None, *, cap: Any = 5e9, price: Any = 50.0,
         avg_volume: Any = 1_000_000, sector: str = "Financial Services", **over: Any
         ) -> Dict[str, Any]:
    """A /stable company-screener row (FMP's field names); a liquid common by default."""
    row = {
        "symbol": sym, "companyName": name or f"{sym} Holdings Corp", "marketCap": cap,
        "sector": sector, "price": price, "volume": avg_volume, "avgVolume": avg_volume,
        "exchange": "NYSE", "exchangeShortName": "NYSE", "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _distinct(sym: str) -> List[Dict[str, Any]]:
    seed = zlib.crc32(sym.encode("utf-8")) + 1
    return [{"grossProfitMarginTTM": 0.3 + seed / 2**34, "operatingProfitMarginTTM": 0.1 + seed / 2**35,
             "netProfitMarginTTM": 0.05 + seed / 2**36, "currentRatioTTM": 1.5,
             "debtToEquityRatioTTM": 0.8}]


class _FakeFMP:
    """Screener rows per industry; `ratios` maps a symbol to its answer (a list, an
    exception to raise, or a callable) — a symbol not named gets ratios of its own."""

    def __init__(self, screener: Dict[str, List[Dict[str, Any]]],
                 ratios: Optional[Dict[str, Any]] = None):
        self.screener = screener
        self.ratios = ratios or {}
        self.ratio_calls: List[str] = []
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        self.calls += 1
        if endpoint == "available-industries":
            return [{"industry": n} for n in self.screener]
        if endpoint == "ratios-ttm":
            sym = params["symbol"]
            self.ratio_calls.append(sym)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                await asyncio.sleep(0)            # let the other workers in
                spec = self.ratios.get(sym, _distinct)
                if isinstance(spec, BaseException):
                    raise spec
                return spec(sym) if callable(spec) else spec
            finally:
                self.in_flight -= 1
        assert endpoint == "company-screener", endpoint
        rows = self.screener[params["industry"]]
        limit, page = int(params["limit"]), int(params.get("page", 0))
        return rows[page * limit:(page + 1) * limit]

    async def close(self) -> None:  # pragma: no cover — an injected client is not closed
        pass


async def _no_sleep(_delay: float) -> None:
    pass


async def _run(fmp: _FakeFMP, out: Path, **kw: Any) -> int:
    kw.setdefault("sleep", _no_sleep)
    return await bu.main(FLOOR, output=out, fmp=fmp, **kw)


def _written(out: Path) -> Dict[str, List[str]]:
    payload = json.loads(out.read_text(encoding="utf-8"))
    return {e["industry"]: e["tickers"] for e in payload["industries"]}


def _syms(n: int, prefix: str = "R") -> List[str]:
    """`n` distinct common-share-shaped symbols (letters only: a digit fails the shape)."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [prefix + letters[i // 26] + letters[i % 26] for i in range(n)]


def _filtered(market: Dict[str, List[Dict[str, Any]]]) -> bu._MarketFilter:
    return bu._filter_market(market, FLOOR)


def _fps(**ratios: Dict[str, Any]) -> Dict[str, bu.Fingerprint]:
    out = {}
    for sym, r in ratios.items():
        fp = bu._statement_fingerprint(r)
        assert fp is not None, sym
        out[sym] = fp
    return out


# ══ the fingerprint ═════════════════════════════════════════════════════════════════════


def test_five_informative_values_make_a_fingerprint():
    fp = bu._statement_fingerprint(SHARED)
    assert fp == tuple(SHARED[f] for f in bu._TWIN_RATIO_FIELDS)


@pytest.mark.parametrize("zeroed,expected_none", [
    ((), False),
    (("currentRatioTTM",), False),
    (("currentRatioTTM", "debtToEquityRatioTTM"), False),       # exactly 3 informative
    (("currentRatioTTM", "debtToEquityRatioTTM", "grossProfitMarginTTM"), True),   # 2
    (tuple(bu._TWIN_RATIO_FIELDS), True),
])
def test_at_least_three_informative_non_zero_values(zeroed, expected_none):
    row = {**SHARED, **{f: 0 for f in zeroed}}
    assert (bu._statement_fingerprint(row) is None) is expected_none


@pytest.mark.parametrize("junk", [None, "0.5", "", True, False, math.nan, math.inf, -math.inf,
                                  [0.5], {"v": 0.5}])
def test_junk_values_are_not_informative_and_never_coerced(junk):
    """A string or bool never counts (no `float("0.5")`, no `True` → 1.0): three junk slots
    leave two informative values — no fingerprint."""
    row = {**SHARED, "grossProfitMarginTTM": junk, "currentRatioTTM": junk,
           "debtToEquityRatioTTM": junk}
    assert bu._statement_fingerprint(row) is None
    one_junk = bu._statement_fingerprint({**SHARED, "currentRatioTTM": junk})
    assert one_junk is not None and one_junk[3] is None


@pytest.mark.parametrize("huge", [10**400, -(10**400), 2**1024])
def test_an_integer_too_large_for_a_float_is_not_informative_and_never_raises(huge):
    """`float(10**400)` raises OverflowError; the fingerprint runs outside each call's
    `try`, so one absurd value would abort the whole scan. Mutation: drop the guard →
    OverflowError."""
    assert bu._twin_slot(huge) is None
    fp = bu._statement_fingerprint({**SHARED, "currentRatioTTM": huge})
    assert fp is not None and fp[3] is None
    assert bu._statement_fingerprint({**SHARED, "grossProfitMarginTTM": huge,
                                      "currentRatioTTM": huge,
                                      "debtToEquityRatioTTM": huge}) is None
    assert bu._twin_slot(2**1023) == float(2**1023)      # the largest that fits still counts


@pytest.mark.asyncio
async def test_a_huge_integer_answer_keeps_the_scan_alive():
    fmp = _FakeFMP({}, ratios={"HUGE": [{**SHARED, "currentRatioTTM": 10**400}]})
    scan = await bu._fetch_statement_fingerprints(fmp, ["HUGE", "OKAY"], sleep=_no_sleep)
    assert sorted(scan.fingerprints) == ["HUGE", "OKAY"] and scan.failed == {}
    assert scan.fingerprints["HUGE"][3] is None


def test_a_previous_floor_too_large_for_a_float_is_unreadable_not_a_crash(caplog):
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert bu._floor_change_allowed({"market_cap_floor": 10**400}, FLOOR) is True
    assert "unreadable" in caplog.text


@pytest.mark.parametrize("row", [None, [], "x", 7, {}, {"symbol": "AAA"}])
def test_a_row_that_is_not_a_ratio_object_has_no_fingerprint(row):
    assert bu._statement_fingerprint(row) is None


def test_negative_values_are_informative():
    """A loss-maker's negative margins are real values, not missing ones."""
    row = {**SHARED, "operatingProfitMarginTTM": -0.42, "netProfitMarginTTM": -0.5}
    assert bu._statement_fingerprint(row)[1:3] == (-0.42, -0.5)


# ══ the pure pass: same industry drops, cross industry keeps ════════════════════════════


def test_a_same_industry_twin_drops_the_less_liquid_listing():
    """BNH-shaped: the note reports the bigger cap but trades a sliver — liquidity wins, so
    BN votes. Mutation: rank by cap → BNH kept."""
    market = {"Asset Management": [
        _row("BN", "Brookfield Corporation", cap=8.18e10, price=45.0, avg_volume=3e6),
        _row("BNH", "BNH Vehicle Ltd.", cap=8.97e10, price=20.0, avg_volume=4e4),
        _row("APO", "Apollo Global Management, Inc.", cap=7e10),
    ]}
    fps = _fps(BN=SHARED, BNH=SHARED, APO=_distinct("APO")[0])
    out = bu._one_vote_per_statement_set(_filtered(market), fps)
    assert [r["symbol"] for r in out.filtered.kept["Asset Management"]] == ["BN", "APO"]
    assert out.filtered.dropped == {"same_statements": 1}
    assert out.filtered.drops_by_industry == {"Asset Management": 1}
    assert out.filtered.examples["same_statements"] == ["BNH (kept BN)"]
    assert out.drops == ['BNH "BNH Vehicle Ltd." [Asset Management] — BN '
                         '"Brookfield Corporation" votes']
    assert out.cross_groups == []


def test_liquidity_wins_even_when_the_thin_listing_sorts_first():
    """The liquid common sorts AFTER the thin listing by symbol and by length, and reports
    the SMALLER cap — only `_vote_order`'s liquidity key keeps it. Mutation: rank by symbol
    (or by cap) → AAAX votes and ZZC is dropped."""
    market = {"Asset Management": [
        _row("AAAX", "Aaax Funding Vehicle LLC", cap=9e10, price=20.0, avg_volume=1e3),
        _row("ZZC", "Zzc Holdings Corp", cap=6e10, price=20.0, avg_volume=5e6),
    ]}
    out = bu._one_vote_per_statement_set(_filtered(market), _fps(AAAX=SHARED, ZZC=SHARED))
    assert [r["symbol"] for r in out.filtered.kept["Asset Management"]] == ["ZZC"]
    assert out.filtered.examples["same_statements"] == ["AAAX (kept ZZC)"]


def test_equal_to_the_last_bit_or_not_at_all():
    """Exact equality: a value different in the 12th decimal is another company (a 3-dp
    match paired CSIQ with TBBB on the 2026-10-08 scan)."""
    near = {**SHARED, "debtToEquityRatioTTM": SHARED["debtToEquityRatioTTM"] + 1e-12}
    market = {"Solar": [_row("AAA"), _row("BBB", avg_volume=10)]}
    out = bu._one_vote_per_statement_set(_filtered(market), _fps(AAA=SHARED, BBB=near))
    assert [r["symbol"] for r in out.filtered.kept["Solar"]] == ["AAA", "BBB"]
    assert out.drops == []


def test_four_equal_slots_and_one_missing_on_one_side_are_not_twins():
    other = {**SHARED, "currentRatioTTM": None}
    market = {"Solar": [_row("AAA"), _row("BBB", avg_volume=10)]}
    out = bu._one_vote_per_statement_set(_filtered(market), _fps(AAA=SHARED, BBB=other))
    assert out.drops == []


def test_identical_ratios_across_industries_keep_both_and_name_the_group():
    """B2 preserved: BBD / BBDO report bit-identical ratios (probe 2026-10-08), but FMP files
    them under two industries — both vote, the group is named. Mutation: drop across
    industries too → BBDO gone."""
    market = {"Banks": [_row("BBDO", "Banco Bradesco S.A.", price=3.0, avg_volume=2e6)],
              "Banks - Regional": [_row("BBD", "Banco Bradesco S.A.", price=3.0,
                                        avg_volume=4e7)]}
    filtered = _filtered(market)
    out = bu._one_vote_per_statement_set(filtered, _fps(BBD=SHARED, BBDO=SHARED))
    assert out.filtered.kept == filtered.kept
    assert out.filtered.dropped == filtered.dropped and out.drops == []
    assert out.cross_groups == ['BBDO [Banks] "Banco Bradesco S.A." + BBD [Banks - Regional] '
                                '"Banco Bradesco S.A."']


def test_a_mixed_group_drops_inside_each_industry_and_names_the_rest():
    market = {
        "Software - Application": [_row("AVPT", "AvePoint, Inc.", avg_volume=2e6),
                                   _row("AVX", "Avx Vehicle Co", avg_volume=1e3)],
        "Shell Companies": [_row("APXT", "Apex Blank Check Corp", avg_volume=5e4)],
    }
    fps = _fps(AVPT=SHARED, AVX=SHARED, APXT=SHARED)
    out = bu._one_vote_per_statement_set(_filtered(market), fps)
    assert [r["symbol"] for r in out.filtered.kept["Software - Application"]] == ["AVPT"]
    assert [r["symbol"] for r in out.filtered.kept["Shell Companies"]] == ["APXT"]
    assert out.filtered.dropped["same_statements"] == 1
    assert len(out.cross_groups) == 1
    assert "APXT [Shell Companies]" in out.cross_groups[0]
    assert "AVPT [Software - Application]" in out.cross_groups[0]
    # The same-industry loser is named as DROPPED, never among the kept members.
    kept_part, dropped_part = out.cross_groups[0].split(" (and ", 1)
    assert "AVX" not in kept_part
    assert dropped_part == ("1 same-industry listing(s) of the group dropped: "
                            "AVX [Software - Application])")


def test_the_mixed_group_warning_never_says_every_member_was_kept(caplog):
    """2026-10-09 review: the WARNING read "every member kept" while AVX had just been
    dropped. It now says each industry keeps its most liquid member and names AVX as
    dropped."""
    market = {
        "Software - Application": [_row("AVPT", "AvePoint, Inc.", avg_volume=2e6),
                                   _row("AVX", "Avx Vehicle Co", avg_volume=1e3)],
        "Shell Companies": [_row("APXT", "Apex Blank Check Corp", avg_volume=5e4)],
    }
    out = bu._one_vote_per_statement_set(_filtered(market),
                                         _fps(AVPT=SHARED, AVX=SHARED, APXT=SHARED))
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        bu._log_statement_twins(out)
    (warning,) = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "every member kept" not in warning
    assert "each industry keeps its most liquid member" in warning
    assert "dropped: AVX [Software - Application]" in warning


def test_one_symbol_in_two_industries_is_not_its_own_twin():
    market = {"A": [_row("XYZ")], "B": [_row("XYZ")]}
    out = bu._one_vote_per_statement_set(_filtered(market), _fps(XYZ=SHARED))
    assert out.cross_groups == [] and out.drops == []
    assert {i: [r["symbol"] for r in v] for i, v in out.filtered.kept.items()} == {
        "A": ["XYZ"], "B": ["XYZ"]}


def test_rows_without_a_fingerprint_are_untouched():
    market = {"Biotechnology": [_row("AAA"), _row("BBB"), _row("CCC")]}
    filtered = _filtered(market)
    out = bu._one_vote_per_statement_set(filtered, {})
    assert out.filtered.kept == filtered.kept and out.drops == [] and out.cross_groups == []


def test_the_input_is_not_mutated():
    market = {"X": [_row("AAA"), _row("BBB", avg_volume=10)]}
    filtered = _filtered(market)
    snapshot = (json.dumps(filtered.kept, sort_keys=True), dict(filtered.dropped),
                json.dumps(filtered.examples), dict(filtered.drops_by_industry))
    bu._one_vote_per_statement_set(filtered, _fps(AAA=SHARED, BBB=SHARED))
    assert snapshot == (json.dumps(filtered.kept, sort_keys=True), dict(filtered.dropped),
                        json.dumps(filtered.examples), dict(filtered.drops_by_industry))


def test_the_result_does_not_depend_on_fmp_order():
    market = {
        "A": [_row("AAA", avg_volume=5e6), _row("AAB", avg_volume=1e4), _row("AAC")],
        "B": [_row("BBA", avg_volume=2e6), _row("BBB", avg_volume=3e6)],
        "C": [_row("CCA")],
    }
    other = {**SHARED, "currentRatioTTM": 9.9}
    fps = _fps(AAA=SHARED, AAB=SHARED, BBA=SHARED, BBB=other, CCA=other, AAC=_distinct("AAC")[0])
    expected = bu._one_vote_per_statement_set(_filtered(market), fps)
    rng = random.Random(20261009)
    for _ in range(20):
        names = list(market)
        rng.shuffle(names)
        shuffled = {n: rng.sample(market[n], len(market[n])) for n in names}
        got = bu._one_vote_per_statement_set(_filtered(shuffled), fps)
        assert got.drops == expected.drops and got.cross_groups == expected.cross_groups
        assert {i: sorted(r["symbol"] for r in v) for i, v in got.filtered.kept.items()} == \
            {i: sorted(r["symbol"] for r in v) for i, v in expected.filtered.kept.items()}


# ══ the paced fetch ═════════════════════════════════════════════════════════════════════


class _Recorder:
    def __init__(self) -> None:
        self.delays: List[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.mark.asyncio
async def test_calls_start_at_most_five_a_second_and_four_at_a_time():
    """A frozen clock: every call after the first waits for its own slot, 0.2 s apart.
    Mutations: drop `pacer.wait()` → no delays; raise the semaphore → more in flight."""
    syms = _syms(12, "S")
    fmp = _FakeFMP({})
    sleep = _Recorder()
    scan = await bu._fetch_statement_fingerprints(fmp, syms, sleep=sleep, clock=lambda: 100.0)
    interval = 1.0 / bu._TWIN_CALLS_PER_SECOND
    assert sorted(sleep.delays) == pytest.approx([interval * k for k in range(1, 12)])
    assert sorted(fmp.ratio_calls) == syms and len(scan.fingerprints) == 12
    # The fake yields once per call, so exactly `_TWIN_CONCURRENCY` overlap — a serial scan
    # (concurrency 1) would take ~4x the logged runtime. Mutation: `_TWIN_CONCURRENCY = 1`.
    assert fmp.max_in_flight == bu._TWIN_CONCURRENCY == 4
    assert bu._TWIN_CALLS_PER_SECOND * 60 <= 300      # inside the shared key's budget


@pytest.mark.asyncio
async def test_a_moving_clock_needs_no_wait_once_the_slot_has_passed():
    now = [0.0]
    sleep = _Recorder()
    pacer = bu._Pacer(5.0, sleep=sleep, clock=lambda: now[0])
    await pacer.wait()
    now[0] = 1.0
    await pacer.wait()
    await pacer.wait()
    assert sleep.delays == pytest.approx([0.2])


@pytest.mark.parametrize("rate", [0, -1, math.nan, math.inf, True, "5", None])
def test_the_pacer_refuses_a_nonsense_rate(rate):
    with pytest.raises(ValueError):
        bu._Pacer(rate, sleep=_no_sleep, clock=lambda: 0.0)


@pytest.mark.asyncio
async def test_each_answer_shape_is_sorted_into_its_bucket(caplog):
    fmp = _FakeFMP({}, ratios={
        "EMPTY": [],
        "ZEROS": [{f: 0 for f in bu._TWIN_RATIO_FIELDS}],
        "DICT": {"Error Message": "Limit Reach"},
        "NOTDICT": ["x"],
        "BOOM": FMPUnavailableException("HTTP 503 after 4 attempts"),
    })
    good = _syms(400, "G")                 # 3 failures of 406 stay inside the 1% budget
    syms = ["EMPTY", "ZEROS", "DICT", "NOTDICT", "BOOM"] + good
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        scan = await bu._fetch_statement_fingerprints(fmp, syms + [good[0], "", None],
                                                      sleep=_no_sleep)
    assert scan.planned == 405 and sorted(fmp.ratio_calls) == sorted(syms)  # deduped, junk out
    assert scan.no_statements == ["EMPTY"] and scan.uninformative == ["ZEROS"]
    assert sorted(scan.fingerprints) == sorted(good) and scan.not_called == []
    assert sorted(scan.failed) == ["BOOM", "DICT", "NOTDICT"]
    assert "UnusableAnswerError" in scan.failed["DICT"]
    for sym in ("BOOM", "DICT", "NOTDICT"):
        assert any(sym in r.getMessage() and r.levelno == logging.WARNING
                   for r in caplog.records), sym


@pytest.mark.asyncio
async def test_an_outage_stops_calling_once_the_budget_is_spent():
    """300 symbols, every call failing: the fourth failure passes 1%, after which no new
    call starts (the in-flight ones finish). Mutation: drop the budget check → 300 calls."""
    syms = _syms(300, "S")
    fmp = _FakeFMP({}, ratios={s: FMPUnavailableException("down") for s in syms})
    scan = await bu._fetch_statement_fingerprints(fmp, syms, sleep=_no_sleep)
    assert bu._twin_failures_fatal(len(scan.failed), scan.planned)
    assert len(fmp.ratio_calls) <= 4 + bu._TWIN_CONCURRENCY
    assert len(scan.not_called) == 300 - len(fmp.ratio_calls)


@pytest.mark.parametrize("failed,planned,fatal", [
    (0, 0, False), (0, 5, False), (1, 100, False), (2, 100, True), (1, 99, True),
    (30, 3000, False), (31, 3000, True), (1, 1, True),
])
def test_the_failure_boundary_is_more_than_one_percent(failed, planned, fatal):
    assert bu._twin_failures_fatal(failed, planned) is fatal


# ══ end to end through main() ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_main_drops_same_industry_twins_and_names_each_on_its_own_line(tmp_path, caplog):
    """Six twins of one common (more than `_log_drops`' five examples): every drop has its
    own INFO line, the file holds the common only, and the industry line counts them."""
    notes = [f"NT{c}" for c in "ABCDEF"]
    rows = [_row("BIG", "Big Company Inc.", avg_volume=9e6)] + [
        _row(s, f"Funding Vehicle {s} LLC", avg_volume=1e3) for s in notes]
    rows.append(_row("OTHER", "Other Company Inc."))
    fmp = _FakeFMP({"Conglomerates": rows},
                   ratios={s: [SHARED] for s in ["BIG"] + notes})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_OK
    assert _written(out) == {"Conglomerates": ["BIG", "OTHER"]}
    lines = [r for r in caplog.records
             if r.getMessage().startswith("benchmark universe: same_statements — dropped")]
    assert sorted(n for n in notes if any(n in r.getMessage() for r in lines)) == notes
    assert len(lines) == 6 and all(r.levelno == logging.INFO for r in lines)
    assert any("Conglomerates" in r.getMessage() and "2 tickers (6 dropped)" in r.getMessage()
               for r in caplog.records)
    (summary,) = [r for r in caplog.records if "dropped 6 same_statements" in r.getMessage()]
    assert summary.levelno == logging.INFO
    assert "/stable/ratios-ttm" in json.loads(out.read_text())["source"]


@pytest.mark.asyncio
async def test_main_keeps_identical_cross_industry_classes_and_warns_twice(tmp_path, caplog):
    """B2 with IDENTICAL ratios (as FMP serves BBD / BBDO): both written, the twin group
    named, and the share-class pair still named by `_cross_industry_share_classes`."""
    fmp = _FakeFMP({
        "Banks": [_row("BBDO", "Banco Bradesco S.A.", price=3.0, avg_volume=2e6)],
        "Banks - Regional": [_row("BBD", "Banco Bradesco S.A.", price=3.0, avg_volume=4e7)],
    }, ratios={"BBD": [SHARED], "BBDO": [SHARED]})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_OK
    assert _written(out) == {"Banks": ["BBDO"], "Banks - Regional": ["BBD"]}
    (twin,) = [r for r in caplog.records if "SAME statements" in r.getMessage()]
    assert twin.levelno == logging.WARNING and "BBDO [Banks]" in twin.getMessage()
    assert "BBD [Banks - Regional]" in twin.getMessage()
    (pair,) = [r for r in caplog.records if "DIFFERENT industries" in r.getMessage()]
    assert pair.levelno == logging.WARNING
    assert "same_statements" not in caplog.text


@pytest.mark.asyncio
async def test_every_later_check_sees_the_post_twin_rows(tmp_path, caplog):
    """A thin-turnover note dropped as a twin is not named as a thin suspect (that list is
    read AFTER the pass), and the shrink guard counts what will be written: 10 → 8 is 20%.
    Mutation: run the pass after the logs/guards → THN named, and the build written."""
    out = tmp_path / "u.json"
    previous = [f"P{i}" for i in range(10)]
    out.write_text(json.dumps({"market_cap_floor": FLOOR, "industries": [
        {"industry": "Utilities", "sector": "Utilities", "tickers": previous,
         "market_caps": {t: 1e9 for t in previous}}]}), encoding="utf-8")
    before = out.read_text(encoding="utf-8")
    utils = _syms(8, "U")
    rows = [_row(u, f"Utility {u} Co") for u in utils]
    rows += [_row("THN", "Thin Vehicle Issuer LLC", cap=2e10, price=20.0, avg_volume=100),
             _row("NVB", "Vehicle B LLC", avg_volume=5)]
    fmp = _FakeFMP({"Utilities": rows},
                   ratios={utils[0]: [SHARED], "THN": [SHARED], "NVB": [SHARED]})
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_SHRINK_REFUSED
    assert out.read_text(encoding="utf-8") == before
    assert "10 → 8" in caplog.text
    thin = [r.getMessage() for r in caplog.records if "of their reported market" in r.getMessage()]
    assert all("THN" not in m for m in thin)
    assert any("THN" in r.getMessage() and "same_statements" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_only_kept_rows_are_fingerprinted(tmp_path):
    """Hand-checked, fund, ETF, below-floor and twin rows are never fetched."""
    fmp = _FakeFMP({"Asset Management": [
        _row("AMG", "Affiliated Managers Group, Inc."),
        _row("MGR", "Affiliated Managers Group, Inc.", avg_volume=10),    # hand-checked
        _row("SPY", isEtf=True), _row("GOLDX", isFund=True),
        _row("TINY", cap=1e8), _row("UTF", "Cohen & Steers Infrastructure Fund"),
        _row("TROW", "T. Rowe Price Group, Inc."),
    ]})
    assert await _run(fmp, tmp_path / "u.json") == bu.EXIT_OK
    assert sorted(fmp.ratio_calls) == ["AMG", "TROW"]


@pytest.mark.asyncio
async def test_a_failed_call_keeps_its_row_within_the_budget(tmp_path, caplog):
    """1 of 150 failing (0.67%): written, the row kept, named at WARNING."""
    syms = _syms(150)
    rows = [_row(s) for s in syms]
    bad = syms[7]
    fmp = _FakeFMP({"Banks - Regional": rows},
                   ratios={bad: FMPRateLimitException("429", retry_after="1")})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_OK
    assert bad in _written(out)["Banks - Regional"]
    named = [r for r in caplog.records if f" {bad} " in f" {r.getMessage()} ".replace(",", " ")]
    assert named and all(r.levelno == logging.WARNING for r in named)
    # The 429 was retried on the builder's schedule before it counted.
    assert fmp.ratio_calls.count(bad) == len(bu._RATE_LIMIT_BACKOFF_SECONDS) + 1


@pytest.mark.asyncio
async def test_more_than_one_percent_failed_fails_the_build(tmp_path, caplog):
    """2 of 150 (1.3%): an outage — exit 1, the previous file untouched."""
    syms = _syms(150)
    rows = [_row(s) for s in syms]
    bad = (syms[7], syms[99])
    fmp = _FakeFMP({"Banks - Regional": rows},
                   ratios={b: FMPUnavailableException("503") for b in bad})
    out = tmp_path / "u.json"
    out.write_text(json.dumps({"market_cap_floor": FLOOR, "industries": []}), encoding="utf-8")
    before = out.read_text(encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_BUILD_FAILED
    assert out.read_text(encoding="utf-8") == before
    assert not out.with_suffix(".json.part").exists()
    (err,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "2 of 150 ratios-ttm calls FAILED" in err.getMessage()
    assert all(b in err.getMessage() for b in bad)


@pytest.mark.asyncio
async def test_a_tiny_universe_with_one_failure_fails_closed(tmp_path):
    """One of three is 33%: the rule is a share, so a small build is strict."""
    fmp = _FakeFMP({"X": [_row("AAA"), _row("BBB"), _row("CCC")]},
                   ratios={"BBB": FMPUnavailableException("503")})
    out = tmp_path / "u.json"
    assert await _run(fmp, out) == bu.EXIT_BUILD_FAILED
    assert not out.exists()


def _http_error(status: int, symbol: str) -> httpx.HTTPStatusError:
    url = (f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={symbol}"
           f"&apikey={FAKE_KEY}")
    request = httpx.Request("GET", url)
    response = httpx.Response(status, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("unreachable")  # pragma: no cover


@pytest.mark.asyncio
@pytest.mark.parametrize("strip_filter", [False, True])
async def test_a_failed_call_never_logs_the_key(tmp_path, caplog, monkeypatch, strip_filter):
    """`FMPClient` re-raises a raw 400/403/52x whose message is the URL, key included.
    Proved with the module's redacting filter AND without it (the explicit `_describe`)."""
    if strip_filter:
        monkeypatch.setattr(bu.logger, "filters", [])
    rows = [_row(s) for s in ("AAA", "BBB", "CCC")]
    fmp = _FakeFMP({"X": rows}, ratios={"BBB": _http_error(403, "BBB")})
    with caplog.at_level(logging.DEBUG):
        assert await _run(fmp, tmp_path / "u.json") == bu.EXIT_BUILD_FAILED
    assert "BBB" in caplog.text and "HTTPStatusError" in caplog.text
    assert FAKE_KEY not in caplog.text


@pytest.mark.asyncio
async def test_no_statements_are_named_in_one_warning(tmp_path, caplog):
    """SPME / SWRD-shaped rows: ratios-ttm answers [] — kept, every one named once."""
    fmp = _FakeFMP({"Asset Management": [_row("AAA"), _row("EMPTA"), _row("EMPTB")]},
                   ratios={"EMPTA": [], "EMPTB": []})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_OK
    assert _written(out) == {"Asset Management": ["AAA", "EMPTA", "EMPTB"]}
    (line,) = [r for r in caplog.records if "NO statements at FMP" in r.getMessage()]
    assert line.levelno == logging.WARNING and "EMPTA, EMPTB" in line.getMessage()
    (runtime,) = [r for r in caplog.records if "statement-twin scan —" in r.getMessage()]
    assert "3 ratios-ttm call(s)" in runtime.getMessage()
    assert "2 without statements" in runtime.getMessage()


@pytest.mark.asyncio
async def test_skip_twin_scan_makes_no_call_and_says_so(tmp_path, caplog):
    fmp = _FakeFMP({"X": [_row("AAA"), _row("BBB", avg_volume=10)]},
                   ratios={"AAA": [SHARED], "BBB": [SHARED]})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await _run(fmp, out, skip_twin_scan=True) == bu.EXIT_OK
    assert fmp.ratio_calls == []
    assert _written(out) == {"X": ["AAA", "BBB"]}          # twins NOT merged: no evidence
    assert "statement-twin scan SKIPPED" in caplog.text
    assert "SKIPPED" in json.loads(out.read_text())["source"]


def test_the_cli_has_the_opt_out_and_main_receives_it():
    import ast
    args = bu._build_parser().parse_args(["--skip-twin-scan"])
    assert args.skip_twin_scan is True and args.allow_floor_change is False
    assert bu._build_parser().parse_args([]).skip_twin_scan is False
    tree = ast.parse(Path(bu.__file__).read_text(encoding="utf-8"))
    block = next(n for n in tree.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")
    (call,) = [n for n in ast.walk(block) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == "main"]
    keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert keywords["skip_twin_scan"] == "args.skip_twin_scan"
    assert keywords["allow_floor_change"] == "args.allow_floor_change"


def test_same_statements_is_an_expected_info_drop():
    assert "same_statements" in bu._EXPECTED_DROP_NOTES
    assert "same_statements" not in bu._UNEXPECTED_DROP_REASONS
