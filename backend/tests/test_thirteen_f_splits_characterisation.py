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
    """Drive `_process_13f_path`; return (split_ratios, unclassified, snapshot)."""
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
    return seen["split_ratios"], seen["unclassified"], snap


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
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, snap = _run(monkeypatch, CURR_FWD, None, acts)
    assert (ratios, unclassified) == ({}, set())
    assert acts.split_calls == [] and acts.flag_calls == []
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
