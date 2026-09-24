"""Characterisation of the NIGHTLY HYDRATOR's 13F split block, pinned BEFORE it was swapped.

`scripts/hydrate_whales.py::WhaleHydrator._process_13f` carried its own inline copy of the
split block that `whale_service` handed to `app/services/thirteen_f_splits.py` on
2026-09-24 (`resolve_13f_split_adjustments`). `_whale_common` records what a second copy
costs: three 13F diff copies drifted apart, and the annual-return formula did it again.

These tests were written first and run GREEN against the inline block, then the block was
replaced with a call to the shared helper and they were re-run unchanged. Like
`test_thirteen_f_splits_characterisation.py` (the whale-path twin) they drive the REAL
`_process_13f` end to end — fake FMP, fake corporate-actions seam
(`hydrator.corporate_actions`) — and observe only what the rest of the hydrator sees:

* the `split_ratios` / `unclassified_tickers` handed to `_diff_quarters`,
* the corporate-action calls made (tickers, fetch window, diffed period),
* whether the snapshot keeps its `raw_hash` (a failed lookup must leave it `None`),
* that every corporate-action call runs under the hydrator's `FMP_SEMAPHORE` — the
  nightly sweep is the one caller that throttles this fan-out, and the swap must not
  quietly drop that.

Nothing here depends on where the split code lives.

Hermetic: no network, no Supabase (`WhaleHydrator.__new__` skips `get_supabase()`, and
`_process_13f` never touches `self.sb`).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

import scripts.hydrate_whales as hw

PREV_END, CURR_END, FETCH_FROM = "2026-03-31", "2026-06-30", "2026-03-21"
# The size of the stand-in semaphore each test installs as `hw.FMP_SEMAPHORE`.
SEM_LIMIT = 5


# ── fakes ─────────────────────────────────────────────────────────────────────────────


class _FMP:
    """The four 13F reads `_process_13f` makes. `prev=None` = a first filing."""

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
    """The `corporate_actions` seam. `splits[t]` is the derived split-row shape, `None`
    (a degraded derivation) or an Exception to raise; `flags[t]` likewise for the gate.

    Tracks how many calls are in flight at once, so the throttle is observable.
    """

    def __init__(self, splits=None, flags=None):
        self.splits: Dict[str, Any] = splits or {}
        self.flags: Dict[str, Any] = flags or {}
        self.split_calls: List[tuple] = []
        self.flag_calls: List[tuple] = []
        self.in_flight = 0
        self.peak = 0

    async def _enter(self):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0)          # yield, so an unthrottled sibling would start too
        self.in_flight -= 1

    async def get_split_rows(self, t, from_date=None, to_date=None):
        self.split_calls.append((t, from_date, to_date))
        await self._enter()
        v = self.splits.get(t, [])
        if isinstance(v, BaseException):
            raise v
        return v

    async def has_unclassified_adjustment(self, t, from_date=None, to_date=None, *,
                                          effective_from=None, effective_to=None):
        self.flag_calls.append((t, from_date, to_date, effective_from, effective_to))
        await self._enter()
        v = self.flags.get(t, False)
        if isinstance(v, BaseException):
            raise v
        return v


class _SyncRaisingActions(_Actions):
    """A seam whose call raises BEFORE any await — the batch-level failure."""

    def get_split_rows(self, t, from_date=None, to_date=None):  # not async on purpose
        raise RuntimeError("corporate actions backend exploded")


def _row(sym, value, shares):
    # FMP 13F `extract` shape — the hydrator reads `sharesNumber`.
    return {"symbol": sym, "securityName": f"{sym} Inc", "value": value, "sharesNumber": shares}


TEN_TO_ONE = [{"date": "2026-06-10", "numerator": 10, "denominator": 1}]
ONE_FOR_TEN = [{"date": "2026-05-04", "numerator": 1, "denominator": 10}]


def _run(monkeypatch, curr, prev, actions):
    """Drive `_process_13f`; return (split_ratios, unclassified, result).

    `split_ratios` / `unclassified` are `None` when `_diff_quarters` was never reached.
    """
    seen: Dict[str, Any] = {}
    original = hw.WhaleHydrator._diff_quarters

    def _spy(self, current_raw, previous_raw, filing_date, total, split_ratios=None,
             unclassified_tickers=None):
        seen["split_ratios"] = dict(split_ratios or {})
        seen["unclassified"] = set(unclassified_tickers or set())
        return original(self, current_raw, previous_raw, filing_date, total,
                        split_ratios, unclassified_tickers)

    monkeypatch.setattr(hw.WhaleHydrator, "_diff_quarters", _spy)
    # A fresh semaphore per test: an asyncio primitive binds to the first loop that
    # contends on it, and each test runs its own `asyncio.run`.
    monkeypatch.setattr(hw, "FMP_SEMAPHORE", asyncio.Semaphore(SEM_LIMIT))

    h = hw.WhaleHydrator.__new__(hw.WhaleHydrator)   # skip get_supabase()
    h.fmp = _FMP(curr, prev)
    h.corporate_actions = actions
    out = asyncio.run(h._process_13f("whale-1", "0000000001"))
    return seen.get("split_ratios"), seen.get("unclassified"), out


# A clean 10:1: 100k sh at $100 -> 1M sh at $10. AAPL is untouched throughout.
PREV = [_row("NVDA", 10_000_000, 100_000), _row("AAPL", 5_000_000, 25_000)]
CURR_FWD = [_row("NVDA", 10_000_000, 1_000_000), _row("AAPL", 5_000_000, 25_000)]


# ── the fixtures the swap must not move ───────────────────────────────────────────────


def test_forward_split_restates_and_the_snapshot_is_final(monkeypatch):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {"NVDA": 10.0}
    assert unclassified == set()
    assert out["raw_hash"] is not None
    # One lookup per suspect, over the lead-padded fetch window; the gate is asked about
    # the diffed period only.
    assert acts.split_calls == [("NVDA", FETCH_FROM, CURR_END)]
    assert acts.flag_calls == [("NVDA", FETCH_FROM, CURR_END, PREV_END, CURR_END)]


def test_forward_split_is_not_booked_as_a_purchase(monkeypatch):
    """Anti-vacuity for the spy: the ratio actually reaches the diff. A held-through 10:1
    with no restatement is a 900,000-share BOUGHT; restated, NVDA has no trade at all."""
    def _nvda_actions(splits):
        _r, _u, out = _run(monkeypatch, CURR_FWD, PREV, _Actions(splits={"NVDA": splits}))
        return [t["action"] for t in (out["trade_group"] or {}).get("trades", [])
                if t.get("ticker") == "NVDA"]

    # Control: with no split found, the raw diff books the fabricated purchase — so the
    # assertion below can fail.
    assert _nvda_actions([]) == ["BOUGHT"]
    assert _nvda_actions(TEN_TO_ONE) == []


def test_reverse_split_restates(monkeypatch):
    prev = [_row("KLAC", 10_000_000, 1_000_000)]
    curr = [_row("KLAC", 10_000_000, 100_000)]
    ratios, unclassified, out = _run(monkeypatch, curr, prev, _Actions(splits={"KLAC": ONE_FOR_TEN}))
    assert ratios == {"KLAC": pytest.approx(0.1)}
    assert unclassified == set()
    assert out["raw_hash"] is not None


def test_a_split_before_the_period_is_ignored(monkeypatch):
    """Inside the 10-day fetch lead but not after `prev_end` -> ratio 1.0 -> no entry."""
    early = [{"date": "2026-03-25", "numerator": 10, "denominator": 1}]
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, PREV, _Actions(splits={"NVDA": early}))
    assert ratios == {}
    assert unclassified == set()
    assert out["raw_hash"] is not None


def test_an_unclassified_adjustment_arms_the_backstop_without_degrading(monkeypatch):
    acts = _Actions(splits={"NVDA": []}, flags={"NVDA": True})
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {}
    assert unclassified == {"NVDA"}
    assert out["raw_hash"] is not None, "a classified-as-unnameable event is an answer, not a failure"


def test_a_failed_split_lookup_arms_the_backstop_and_degrades(monkeypatch):
    """`None` = the derivation could not look. Never read as "no split"."""
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, PREV, _Actions(splits={"NVDA": None}))
    assert ratios == {}
    assert unclassified == {"NVDA"}
    assert out["raw_hash"] is None


def test_a_raising_split_lookup_arms_the_backstop_and_degrades(monkeypatch):
    acts = _Actions(splits={"NVDA": RuntimeError("429")})
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {}
    assert unclassified == {"NVDA"}
    assert out["raw_hash"] is None


def test_a_raising_probe_keeps_the_ratio_but_degrades(monkeypatch):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE}, flags={"NVDA": RuntimeError("timeout")})
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, PREV, acts)
    assert ratios == {"NVDA": 10.0}
    assert unclassified == {"NVDA"}
    assert out["raw_hash"] is None


def test_one_failed_lookup_does_not_take_the_others_down(monkeypatch):
    """Per-ticker fail-closed: KLAC's failure degrades the snapshot but NVDA's 10:1 is
    still restated and NVDA is not flagged."""
    prev = PREV + [_row("KLAC", 10_000_000, 1_000_000)]
    curr = CURR_FWD + [_row("KLAC", 10_000_000, 100_000)]
    acts = _Actions(splits={"NVDA": TEN_TO_ONE, "KLAC": None})
    ratios, unclassified, out = _run(monkeypatch, curr, prev, acts)
    assert ratios == {"NVDA": 10.0}
    assert unclassified == {"KLAC"}
    assert out["raw_hash"] is None


def test_a_batch_failure_fails_closed_for_every_suspect(monkeypatch):
    prev = PREV + [_row("KLAC", 10_000_000, 1_000_000)]
    curr = CURR_FWD + [_row("KLAC", 10_000_000, 100_000)]
    acts = _SyncRaisingActions()
    ratios, unclassified, out = _run(monkeypatch, curr, prev, acts)
    assert ratios == {}
    assert unclassified == {"NVDA", "KLAC"}
    assert out["raw_hash"] is None
    # The raise happens while the lookups are being BUILT, so the gate is never asked.
    assert acts.flag_calls == []


def test_suspects_over_the_cap_keep_their_raw_diff(monkeypatch):
    """30 clean 10:1 suspects: only the first 25 (strong, then alphabetical) are looked up;
    the overflow gets neither a ratio nor a flag, and the snapshot stays final."""
    names = [f"T{i:02d}" for i in range(30)]
    prev = [_row(n, 1_000_000, 10_000) for n in names]
    curr = [_row(n, 1_000_000, 100_000) for n in names]
    acts = _Actions(splits={n: TEN_TO_ONE for n in names})
    ratios, unclassified, out = _run(monkeypatch, curr, prev, acts)
    assert sorted(ratios) == names[:25]
    assert [c[0] for c in acts.split_calls] == names[:25]
    assert [c[0] for c in acts.flag_calls] == names[:25]
    assert unclassified == set()
    assert out["raw_hash"] is not None


def test_every_corporate_action_call_runs_under_the_fmp_semaphore(monkeypatch):
    """The nightly sweep walks every whale; 25 suspects fan out 50 corporate-action calls
    (each a pair of price-series fetches on a cold cache). The hydrator bounds that with
    `FMP_SEMAPHORE`. Never more than its limit in flight — and more than one, or the
    fixture could not tell a throttle from a serial loop."""
    names = [f"T{i:02d}" for i in range(25)]
    prev = [_row(n, 1_000_000, 10_000) for n in names]
    curr = [_row(n, 1_000_000, 100_000) for n in names]
    acts = _Actions(splits={n: TEN_TO_ONE for n in names})
    ratios, _u, _out = _run(monkeypatch, curr, prev, acts)
    assert len(ratios) == 25, "precondition: every suspect was looked up"
    assert acts.peak == SEM_LIMIT


def test_a_first_filing_looks_nothing_up(monkeypatch):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, out = _run(monkeypatch, CURR_FWD, None, acts)
    assert (ratios, unclassified) == ({}, set())
    assert acts.split_calls == [] and acts.flag_calls == []
    assert out["raw_hash"] is not None


def test_a_plain_buy_is_not_a_suspect(monkeypatch):
    """Price flat, shares up 30% -> not split-shaped -> no lookup at all."""
    curr = [_row("NVDA", 13_000_000, 130_000), _row("AAPL", 5_000_000, 25_000)]
    acts = _Actions(splits={"NVDA": TEN_TO_ONE})
    ratios, unclassified, _out = _run(monkeypatch, curr, PREV, acts)
    assert (ratios, unclassified) == ({}, set())
    assert acts.split_calls == [] and acts.flag_calls == []


@pytest.mark.parametrize("curr, prev", [
    # NaN value, zero shares, a blank and a `--` symbol: skipped, never looked up.
    ([_row("NVDA", float("nan"), 1_000_000)], [_row("NVDA", 10_000_000, 100_000)]),
    ([_row("NVDA", 10_000_000, 0), _row("AAPL", 5_000_000, 25_000)],
     [_row("NVDA", 10_000_000, 100_000), _row("AAPL", 5_000_000, 25_000)]),
    ([_row("", 10_000_000, 1_000_000), _row("AAPL", 5_000_000, 25_000)],
     [_row("", 10_000_000, 100_000), _row("AAPL", 5_000_000, 25_000)]),
    ([_row("--", 10_000_000, 1_000_000), _row("AAPL", 5_000_000, 25_000)],
     [_row("--", 10_000_000, 100_000), _row("AAPL", 5_000_000, 25_000)]),
])
def test_degenerate_rows_look_nothing_up(monkeypatch, curr, prev):
    acts = _Actions(splits={"NVDA": TEN_TO_ONE, "": TEN_TO_ONE, "--": TEN_TO_ONE})
    ratios, unclassified, _out = _run(monkeypatch, curr, prev, acts)
    assert acts.split_calls == [] and acts.flag_calls == []
    assert ratios in ({}, None) and unclassified in (set(), None)


# ── the swap itself (added AFTER the tests above went green on both implementations) ──

import inspect  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

_HYDRATOR = Path(__file__).resolve().parents[1] / "scripts" / "hydrate_whales.py"


def _code(src: str) -> str:
    """Source with docstrings and comments removed — the explanatory prose next to the
    call names every token these guards look for (`.claude/rules/testing.md` §3)."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(l.split("#", 1)[0] for l in src.splitlines() if l.split("#", 1)[0].strip())


def test_the_hydrator_runs_the_shared_block_with_its_throttle_and_ids():
    """Brace-bound to `_process_13f`: the call, the THROTTLED seam, the caller's ids, and
    all three outputs taken from it (the third keeps `raw_hash` off a degraded row)."""
    code = _code(inspect.getsource(hw.WhaleHydrator._process_13f))
    assert re.search(
        r"split_ratios,\s*unclassified_tickers,\s*lookup_failed_tickers,?\s*\)\s*=\s*"
        r"await resolve_13f_split_adjustments\(", code,
    ), "the hydrator no longer runs the shared split block"
    i = code.index("await resolve_13f_split_adjustments(")
    call = code[i:code.index("\n        )", i)]
    assert "actions=_ThrottledCorporateActions(corporate_actions_source(self), _throttled)" in call
    assert "whale_id=" in call and "cik=" in call and "period=" in call


def test_the_throttle_wraps_both_seam_calls():
    code = _code(inspect.getsource(hw._ThrottledCorporateActions))
    for method in ("get_split_rows", "has_unclassified_adjustment"):
        assert re.search(
            rf"def {method}\(self, \*args: Any, \*\*kwargs: Any\)[^:]*:\s*\n\s*"
            rf"return self\._throttle\(self\._inner\.{method}\(\*args, \*\*kwargs\)\)", code,
        ), f"{method} is not awaited under the hydrator's FMP semaphore"


def test_no_inline_copy_of_the_split_block_came_back():
    """A second copy is how the 13F writers drifted (`_whale_common`). These are the
    block's own statements, not names the adapter or the diff legitimately use."""
    code = _code(_HYDRATOR.read_text())
    for token in (
        "suspicious_split_tickers(", "split_ratio_in_window(", "window_for_range(",
        "suspects[:", "MAX_SPLIT_LOOKUPS", "if sl is None", "if flagged is True",
        "set(suspects or [])",
    ):
        assert token not in code, f"hydrate_whales.py re-implements the split block ({token!r})"
