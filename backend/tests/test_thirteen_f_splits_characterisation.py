"""Characterisation of the whale 13F split block, pinned BEFORE it was extracted.

`whale_service._process_13f_path` carried ~125 lines that turn a 13F quarter pair into
three outputs — split ratios, "unclassified" tickers (the magnitude-backstop gate) and
failed-lookup tickers (which keep `raw_hash` off a degraded snapshot). The Trillion-Dollar
Club builder needs the same logic, so it moved to `app/services/thirteen_f_splits.py`.

These tests were written first and run GREEN against the in-line code, then the block was
extracted and they were re-run unchanged. They drive the REAL request path end to end —
fake FMP, fake corporate-actions seam (`svc.corporate_actions`), fake Supabase — and
observe only what the rest of the whale pipeline sees: the `split_ratios` /
`unclassified_tickers` handed to `_diff_quarters`, the corporate-action calls made, and
whether the snapshot carries a `raw_hash`. Nothing here depends on where the code lives.

Hermetic: no network, no Supabase.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from app.services import whale_service as wsvc
from app.services.whale_service import WhaleService

PREV_END, CURR_END, FETCH_FROM = "2026-03-31", "2026-06-30", "2026-03-21"


# ── fakes ─────────────────────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __getattr__(self, _name):
        return lambda *a, **k: self


class _SB:
    def table(self, _name):
        return _Query()


async def _sb_exec(_query, *a, **k):
    return _Resp([])


class _FMP:
    def __init__(self, curr: List[Dict], prev: Optional[List[Dict]]):
        self.curr, self.prev = curr, prev

    async def get_institutional_filing_dates(self, cik):
        dates = [{"date": CURR_END, "year": 2026, "quarter": 2}]
        if self.prev is not None:
            dates.append({"date": PREV_END, "year": 2026, "quarter": 1})
        return dates

    async def get_institutional_holdings(self, cik, year, quarter):
        return self.curr if (year, quarter) == (2026, 2) else (self.prev or [])

    async def get_institutional_industry_breakdown(self, cik, year=0, quarter=0):
        return []

    async def get_institutional_performance(self, cik):
        return []


class _Actions:
    """The `corporate_actions` seam. `splits[t]` is FMP's /splits row shape, `None` (a
    degraded derivation) or an Exception to raise; `flags[t]` likewise for the gate."""

    def __init__(self, splits=None, flags=None):
        self.splits: Dict[str, Any] = splits or {}
        self.flags: Dict[str, Any] = flags or {}
        self.split_calls: List[tuple] = []
        self.flag_calls: List[tuple] = []

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.split_calls.append((t, from_date, to_date))
        v = self.splits.get(t, [])
        if isinstance(v, BaseException):
            raise v
        return v

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, *,
                                          effective_from=None, effective_to=None):
        self.flag_calls.append((t, from_date, to_date, effective_from, effective_to))
        v = self.flags.get(t, False)
        if isinstance(v, BaseException):
            raise v
        return v


class _SyncRaisingActions(_Actions):
    """A seam whose call raises BEFORE any await — the batch-level failure."""

    def get_split_rows(self, t, from_date=None, to_date=None):  # not async on purpose
        raise RuntimeError("corporate actions backend exploded")


def _row(sym, value, shares):
    return {"symbol": sym, "value": value, "shares": shares}


TEN_TO_ONE = [{"date": "2026-06-10", "numerator": 10, "denominator": 1}]
ONE_FOR_TEN = [{"date": "2026-05-04", "numerator": 1, "denominator": 10}]


def _run(monkeypatch, curr, prev, actions):
    """Drive `_process_13f_path`; return (split_ratios, unclassified, snapshot).

    `split_ratios` / `unclassified` are `None` when `_diff_quarters` was never reached (a
    quarter with no adjacent previous quarter is not compared)."""
    seen: Dict[str, Any] = {}
    original = WhaleService._diff_quarters

    def _spy(self, current_raw, previous_raw, filing_date, total, split_ratios=None,
             unclassified_tickers=None):
        seen["split_ratios"] = dict(split_ratios or {})
        seen["unclassified"] = set(unclassified_tickers or set())
        return original(self, current_raw, previous_raw, filing_date, total,
                        split_ratios, unclassified_tickers)

    async def _enrich(self, holdings, need_sectors=False):
        return holdings, []

    async def _sync(self, *a, **k):
        return None

    monkeypatch.setattr(WhaleService, "_diff_quarters", _spy)
    monkeypatch.setattr(WhaleService, "_enrich_from_profiles", _enrich)
    monkeypatch.setattr(WhaleService, "_sync_to_whale_tables", _sync)
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _SB())
    monkeypatch.setattr(wsvc, "sb_exec", _sb_exec)
    wsvc._filing_dates_cache.clear()

    svc = WhaleService.__new__(WhaleService)
    svc.fmp = _FMP(curr, prev)
    svc.corporate_actions = actions
    snap = asyncio.run(svc._process_13f_path("whale-1", "0000000001"))
    wsvc._filing_dates_cache.clear()
    return seen.get("split_ratios"), seen.get("unclassified"), snap


# A clean 10:1: 100k sh at $100 -> 1M sh at $10. AAPL is untouched throughout.
PREV = [_row("NVDA", 10_000_000, 100_000), _row("AAPL", 5_000_000, 25_000)]
CURR_FWD = [_row("NVDA", 10_000_000, 1_000_000), _row("AAPL", 5_000_000, 25_000)]


def test_forward_split_restates_and_the_snapshot_is_final(monkeypatch):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {"NVDA": 10.0}
    assert unclassified == set()
    assert snap["raw_hash"] is not None
    # One lookup per suspect, over the lead-padded fetch window; the gate is asked about
    # the diffed period only.
    assert acts.split_calls == [("NVDA", FETCH_FROM, CURR_END)]
    assert acts.flag_calls == [("NVDA", FETCH_FROM, CURR_END, PREV_END, CURR_END)]


def test_reverse_split_restates(monkeypatch):
    prev = [_row("KLAC", 10_000_000, 1_000_000)]
    curr = [_row("KLAC", 10_000_000, 100_000)]
    ratios, unclassified, snap = _run(monkeypatch, curr, prev, _Actions(splits={"KLAC": ONE_FOR_TEN}))
    assert ratios == {"KLAC": pytest.approx(0.1)}
    assert unclassified == set()
    assert snap["raw_hash"] is not None


def test_a_split_before_the_period_is_ignored(monkeypatch):
    """Inside the 10-day fetch lead but not after `prev_end` -> ratio 1.0 -> no entry."""
    early = [{"date": "2026-03-25", "numerator": 10, "denominator": 1}]
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, PREV, _Actions(splits={"NVDA": early}))
    assert ratios == {}
    assert unclassified == set()
    assert snap["raw_hash"] is not None


def test_an_unclassified_adjustment_arms_the_backstop_without_degrading(monkeypatch):
    acts = _Actions(splits={"NVDA": []}, flags={"NVDA": True})
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {}
    assert unclassified == {"NVDA"}
    assert snap["raw_hash"] is not None, "a classified-as-unnameable event is an answer, not a failure"


def test_a_failed_split_lookup_arms_the_backstop_and_degrades(monkeypatch):
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, PREV, _Actions(splits={"NVDA": None}))
    assert ratios == {}
    assert unclassified == {"NVDA"}
    assert snap["raw_hash"] is None


def test_a_raising_split_lookup_arms_the_backstop_and_degrades(monkeypatch):
    acts = _Actions(splits={"NVDA": RuntimeError("429")})
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {}
    assert unclassified == {"NVDA"}
    assert snap["raw_hash"] is None


def test_a_raising_probe_keeps_the_ratio_but_degrades(monkeypatch):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE}, flags={"NVDA": RuntimeError("timeout")})
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {"NVDA": 10.0}
    assert unclassified == {"NVDA"}
    assert snap["raw_hash"] is None


def test_a_batch_failure_fails_closed_for_every_suspect(monkeypatch):
    prev = PREV + [_row("KLAC", 10_000_000, 1_000_000)]
    curr = CURR_FWD + [_row("KLAC", 10_000_000, 100_000)]
    ratios, unclassified, snap = _run(monkeypatch, curr, prev, _SyncRaisingActions())
    assert ratios == {}
    assert unclassified == {"NVDA", "KLAC"}
    assert snap["raw_hash"] is None


def test_suspects_over_the_cap_keep_their_raw_diff(monkeypatch):
    """30 clean 10:1 suspects: only the first 25 (strong, then alphabetical) are looked up;
    the overflow gets neither a ratio nor a flag."""
    names = [f"T{i:02d}" for i in range(30)]
    prev = [_row(n, 1_000_000, 10_000) for n in names]
    curr = [_row(n, 1_000_000, 100_000) for n in names]
    acts = _Actions(splits={n: TEN_TO_ONE for n in names})
    ratios, unclassified, snap = _run(monkeypatch, curr, prev, acts)
    assert sorted(ratios) == names[:25]
    assert [c[0] for c in acts.split_calls] == names[:25]
    assert len(acts.flag_calls) == 25
    assert unclassified == set()
    assert snap["raw_hash"] is not None


def test_a_first_filing_looks_nothing_up(monkeypatch):
    # Since 2026-10-09 a first filing is NOT COMPARED (owner decision; the Trillion-Dollar
    # Club's `first_filing`): the differ is never reached, so there is nothing to look up
    # and no trade group (`tests/test_thirteen_f_gap_quarter.py`).
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, None, acts)
    assert (ratios, unclassified) == (None, None)
    assert acts.split_calls == [] and acts.flag_calls == []
    assert snap["trade_group"] is None
    assert snap["raw_hash"] is not None


def test_a_plain_buy_is_not_a_suspect(monkeypatch):
    """Price flat, shares up 30% -> not split-shaped -> no lookup at all."""
    curr = [_row("NVDA", 13_000_000, 130_000), _row("AAPL", 5_000_000, 25_000)]
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, _snap = _run(monkeypatch, curr, PREV, acts)
    assert (ratios, unclassified) == ({}, set())
    assert acts.split_calls == []


# ── the extracted helper, called directly (club-shaped rows) ─────────────────────────

import inspect  # noqa: E402
from pathlib import Path  # noqa: E402

from app.services import thirteen_f_splits as tfs  # noqa: E402


def _resolve(curr, prev, actions, prev_end=PREV_END):
    return asyncio.run(tfs.resolve_13f_split_adjustments(
        curr, prev, prev_end, CURR_END, actions=actions, log_ctx="cik=0000000001 period=2026-Q2",
    ))


def test_the_helper_reads_club_shaped_rows():
    """The club builder passes normalised holdings (`shares`, not `sharesNumber`)."""
    prev = [{"cusip": "67066G104", "symbol": "NVDA", "value": 10_000_000.0, "shares": 100_000.0}]
    curr = [{"cusip": "67066G104", "symbol": "NVDA", "value": 10_000_000.0, "shares": 1_000_000.0}]
    assert _resolve(curr, prev, _Actions(splits={"NVDA": TEN_TO_ONE})) == ({"NVDA": 10.0}, set(), set())


@pytest.mark.parametrize("curr, prev", [
    ([], []), (None, None), ([_row("NVDA", 1, 1)], None), (None, [_row("NVDA", 1, 1)]),
    ([_row("NVDA", float("nan"), 1_000_000)], [_row("NVDA", 10_000_000, 100_000)]),
    ([_row("NVDA", 10_000_000, 0)], [_row("NVDA", 10_000_000, 100_000)]),
    ([_row("", 10_000_000, 1_000_000)], [_row("", 10_000_000, 100_000)]),
    ([_row("--", 10_000_000, 1_000_000)], [_row("--", 10_000_000, 100_000)]),
    ([{"cusip": "X", "value": 1, "shares": 1}], [{"cusip": "X", "value": 1, "shares": 1}]),
])
def test_empty_and_degenerate_inputs_look_nothing_up(curr, prev):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    assert _resolve(curr, prev, acts) == ({}, set(), set())
    assert acts.split_calls == []


def test_the_helper_never_reaches_for_the_singleton():
    """`actions` is injected; the helper must not bypass it (tests could not fake it)."""
    import ast

    tree = ast.parse(Path(tfs.__file__).read_text())      # AST: docstrings/comments can't match
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None))
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "get_split_rows" in called and "has_unclassified_adjustment" in called, "anti-vacuity"
    assert not called & {"get_corporate_actions_service", "corporate_actions_source",
                         "get_stock_splits"}, "bypasses the injected seam (or hits the 402 /splits)"


def test_the_whale_path_passes_the_seam_and_its_ids():
    src = inspect.getsource(WhaleService._process_13f_path)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = code.index("await resolve_13f_split_adjustments(")
    call = code[i:code.index(")", code.index("log_ctx=", i)) + 1]
    assert "actions=corporate_actions_source(self)" in call
    assert "whale_id=" in call and "cik=" in call


def test_the_old_whale_names_are_the_shared_implementation():
    """`scripts/hydrate_whales.py` imports these from `whale_service`; they must be the
    one implementation, not a second copy."""
    assert wsvc._split_ratio_in_window is tfs.split_ratio_in_window
    rows_c = [_row("NVDA", 10_000_000, 1_000_000)]
    rows_p = [_row("NVDA", 10_000_000, 100_000)]
    assert WhaleService._suspicious_split_tickers(rows_c, rows_p) == tfs.suspicious_split_tickers(rows_c, rows_p) == ["NVDA"]
    assert "SPLIT_PRICE_FACTOR = " not in Path(wsvc.__file__).read_text()


# ── Batch 1 (2026-10-15): the suspects the cap trims are reported through `capped` ───────────────
#
# The cap still fails OPEN for the overflow (the app's screens keep their raw diff — pinned above by
# `test_suspects_over_the_cap_keep_their_raw_diff`); the new keyword-only `capped` set only REPORTS the
# trimmed tail, so a public post (the marketing adapter) can refuse to print an unchecked share change.


def _capped_run(curr, prev, actions, capped):
    return asyncio.run(tfs.resolve_13f_split_adjustments(
        curr, prev, PREV_END, CURR_END, actions=actions, log_ctx="cik=0000000001 period=2026-Q2", capped=capped,
    ))


def _split_book(names):
    """Clean 10:1 suspects: 10,000 sh at $100 -> 100,000 sh at $10."""
    return ([_row(n, 1_000_000, 100_000) for n in names], [_row(n, 1_000_000, 10_000) for n in names])


def test_capped_receives_exactly_the_trimmed_suspects_and_the_return_is_unchanged():
    names = [f"T{i:02d}" for i in range(30)]
    curr, prev = _split_book(names)
    acts = _Actions(splits={n: TEN_TO_ONE for n in names})
    capped: set = set()
    ratios, unclassified, failed = _capped_run(curr, prev, acts, capped)
    assert capped == set(names[25:])
    assert sorted(ratios) == names[:25] and unclassified == set() and failed == set()
    assert [c[0] for c in acts.split_calls] == names[:25]          # the trimmed tail was never looked up
    assert len(acts.flag_calls) == 25
    assert tfs.split_suspects_over_cap(curr, prev) == names[25:]   # the pure twin names the same tail


@pytest.mark.parametrize("n", [0, 1, 24, 25])
def test_nothing_is_capped_at_or_under_the_cap(n):
    names = [f"T{i:02d}" for i in range(n)]
    curr, prev = _split_book(names)
    capped: set = set()
    _capped_run(curr, prev, _Actions(splits={x: TEN_TO_ONE for x in names}), capped)
    assert capped == set() and tfs.split_suspects_over_cap(curr, prev) == []


def test_the_cap_trims_the_price_only_tail_first():
    """Strongest first (`suspicious_split_tickers`): 24 clean 10:1 rows, then three price-only rows
    (price -30%, shares flat) — the cap keeps the 24 and the first price-only row."""
    strong = [f"S{i:02d}" for i in range(24)]
    weak = ["WA", "WB", "WC"]
    curr = [_row(n, 1_000_000, 100_000) for n in strong] + [_row(n, 700_000, 10_000) for n in weak]
    prev = [_row(n, 1_000_000, 10_000) for n in strong + weak]
    capped: set = set()
    _capped_run(curr, prev, _Actions(), capped)
    assert capped == {"WB", "WC"}
    assert tfs.split_suspects_over_cap(curr, prev) == ["WB", "WC"]


def test_a_batch_failure_after_the_cap_still_reports_the_overflow():
    """The batch arm arms the LOOKED-UP suspects (fail closed); the trimmed ones were never armed
    (the cap fails open) — and must still be reported, or a caller would read them as checked."""
    names = [f"T{i:02d}" for i in range(27)]
    curr, prev = _split_book(names)
    capped: set = set()
    ratios, unclassified, failed = _capped_run(curr, prev, _SyncRaisingActions(), capped)
    assert (ratios, unclassified, failed) == ({}, set(names[:25]), set(names[:25]))
    assert capped == {"T25", "T26"}


def test_capped_is_added_to_never_cleared():
    names = [f"T{i:02d}" for i in range(26)]
    curr, prev = _split_book(names)
    capped = {"EARLIER"}
    _capped_run(curr, prev, _Actions(), capped)
    assert capped == {"EARLIER", "T25"}


@pytest.mark.parametrize("bad", [[], frozenset(), {}, "T00", ("T00",)])
def test_a_capped_that_is_not_a_set_is_a_type_error_before_any_lookup(bad):
    names = [f"T{i:02d}" for i in range(26)]
    curr, prev = _split_book(names)
    acts = _Actions()
    with pytest.raises(TypeError, match="capped must be a set"):
        _capped_run(curr, prev, acts, bad)
    assert acts.split_calls == [] and acts.flag_calls == []


def test_capped_is_keyword_only_and_optional_so_every_caller_keeps_working():
    """whale_service and scripts/hydrate_whales.py call without it (their end-to-end tests above)."""
    p = inspect.signature(tfs.resolve_13f_split_adjustments).parameters["capped"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is None
    names = [f"T{i:02d}" for i in range(26)]
    curr, prev = _split_book(names)
    assert _resolve(curr, prev, _Actions())[1:] == (set(), set())   # no `capped`: the same three-tuple


# ── `split_windows` (the review of 2026-10-16, F1) ─────────────────────────────────────
#
# The suspect filter above is a COST bound: a split whose per-share price moved under 30% (a 5:4 split at a flat
# price, a 3:2 split after a 15% rally) is never looked up. `split_windows` lets a caller that must not publish a
# share change over a split (the marketing adapter) ask about ANY symbol — through the same seam, fetch window and
# effective filter, so a derivation the suspect lookups made is a cache hit. No differ calls it.


def _windows(symbols, actions, **kw):
    return asyncio.run(tfs.split_windows(symbols, PREV_END, CURR_END, actions=actions, log_ctx="cik=1 2026-Q2", **kw))


def test_split_windows_reads_the_suspect_lookups_own_window_and_filter():
    acts = _Actions(splits={"NVDA": TEN_TO_ONE, "KO": ONE_FOR_TEN})
    got = _windows(["NVDA", "KO", "AAPL"], acts)
    assert got == {"NVDA": tfs.SplitWindow(10.0, False), "KO": tfs.SplitWindow(0.1, False),
                   "AAPL": tfs.SplitWindow(1.0, False)}
    assert [c for c in acts.split_calls] == [(s, FETCH_FROM, CURR_END) for s in ("NVDA", "KO", "AAPL")]
    assert [c for c in acts.flag_calls] == [(s, FETCH_FROM, CURR_END, PREV_END, CURR_END) for s in ("NVDA", "KO", "AAPL")]
    assert [w.clean for w in got.values()] == [False, False, True]


def test_split_windows_keeps_only_the_quarters_own_splits():
    edge = [{"date": PREV_END, "numerator": 2, "denominator": 1},           # on the previous end: excluded
            {"date": "2026-04-01", "numerator": 3, "denominator": 2},       # inside
            {"date": "2026-07-01", "numerator": 5, "denominator": 1},       # after the quarter: excluded
            {"date": "2026-05-01", "numerator": 0, "denominator": 1},       # a ratio of 0 is dropped
            {"date": "2026-05-02", "numerator": "x", "denominator": 1}]     # garbage is dropped
    assert _windows(["AAA"], _Actions(splits={"AAA": edge}))["AAA"] == tfs.SplitWindow(1.5, False)


@pytest.mark.parametrize("split, flag, expected", [
    (None, False, tfs.SplitWindow(None, None)),                       # could not derive: the probe is not spent
    (RuntimeError("down"), False, tfs.SplitWindow(None, None)),
    ([], True, tfs.SplitWindow(1.0, True)),                           # an unnameable adjustment
    ([], RuntimeError("probe down"), tfs.SplitWindow(1.0, None)),
    ([], 1, tfs.SplitWindow(1.0, None)),                              # a probe answer that is not a bool
    ([], None, tfs.SplitWindow(1.0, None)),
], ids=["none", "raised", "adjustment", "probe_raised", "probe_int", "probe_none"])
def test_split_windows_fails_closed_per_symbol(split, flag, expected, caplog):
    acts = _Actions(splits={"AAA": split}, flags={"AAA": flag})
    got = _windows(["AAA", "BBB"], acts)
    assert got["AAA"] == expected and not got["AAA"].clean
    assert got["BBB"] == tfs.SplitWindow(1.0, False) and got["BBB"].clean      # one symbol never sinks another
    if split is None or isinstance(split, BaseException):
        assert [c[0] for c in acts.flag_calls] == ["BBB"]
    if expected != tfs.SplitWindow(1.0, True):            # an ANSWERED adjustment is no failure: nothing logged
        assert "fail closed" in caplog.text and "cik=1 2026-Q2" in caplog.text
    else:
        assert "fail closed" not in caplog.text


def test_split_windows_keys_symbols_as_the_cache_does_and_skips_junk():
    acts = _Actions()
    got = _windows([" nvda", "NVDA", "", "  ", None, 7, "ko"], acts)
    assert sorted(got) == ["KO", "NVDA"] and [c[0] for c in acts.split_calls] == ["NVDA", "KO"]
    assert _windows([], _Actions()) == {} and _windows([None], _Actions()) == {}


def test_split_windows_bounds_its_concurrency():
    class _Slow(_Actions):
        def __init__(self):
            super().__init__()
            self.live = self.peak = 0

        async def get_split_rows(self, t, from_date=None, to_date=None):
            self.live += 1
            self.peak = max(self.peak, self.live)
            await asyncio.sleep(0.01)
            self.live -= 1
            return []

    acts = _Slow()
    got = _windows([f"S{i}" for i in range(20)], acts, concurrency=3)
    assert len(got) == 20 and acts.peak == 3
    assert tfs.SPLIT_WINDOW_CONCURRENCY == 6


@pytest.mark.parametrize("prev_end, curr_end", [(None, CURR_END), (PREV_END, None), (20260331, CURR_END)])
def test_split_windows_needs_both_quarter_ends(prev_end, curr_end):
    acts = _Actions()
    with pytest.raises(TypeError, match="ISO date strings"):
        asyncio.run(tfs.split_windows(["AAA"], prev_end, curr_end, actions=acts, log_ctx="x"))
    assert acts.split_calls == []


def test_split_windows_takes_an_injected_seam_only():
    """Like `resolve_13f_split_adjustments`, the shared helper never reaches for the singleton (the AST guard
    above); the builder's `split_windows` resolves the production default, as `build_filing` does."""
    with pytest.raises(TypeError, match="pass the corporate-actions seam"):
        asyncio.run(tfs.split_windows(["AAA"], PREV_END, CURR_END, actions=None, log_ctx="x"))
    assert inspect.signature(tfs.split_windows).parameters["actions"].default is inspect.Parameter.empty


def test_split_windows_is_called_by_no_differ():
    """The whale path and the club builder never call it (the whale request path is untouched)."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    for rel in ("app/services/whale_service.py", "scripts/hydrate_whales.py", "app/services/holders_service.py"):
        src = (backend / rel).read_text(encoding="utf-8")
        assert "split_windows" not in src, rel
    import ast

    tree = ast.parse((backend / "app/services/trillion_club/builder.py").read_text(encoding="utf-8"))
    (build,) = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "build_filing"]
    names = {getattr(n, "id", None) or getattr(n, "attr", None) for n in ast.walk(build)}
    assert "resolve_13f_split_adjustments" in names, "anti-vacuity: the build's own split check"
    assert not names & {"split_windows", "_split_windows"}
