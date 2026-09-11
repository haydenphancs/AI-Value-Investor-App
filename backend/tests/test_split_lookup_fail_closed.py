"""A split lookup that could not look must ARM the backstop, never clear it.

Two paths in the 13F writers (and the Holders tab) could still fabricate a split as a
multi-million-dollar BOUGHT after the earlier fail-closed pass:

1. `get_split_rows` collapsed a FAILED derivation into `[]` — byte-identical to "no split"
   — while `has_unclassified_adjustment` ran its OWN derivation. A failure is deliberately
   cached in neither tier, so the first could degrade (one 429 in a 25-ticker burst) and
   the second, a fresh re-derive, succeed: ratio 1.0 (no restatement) AND gate False (a
   cleanly classified 10:1 is not "unclassified"). Neither fail-closed handler fired,
   because nothing raised. `get_split_rows` now returns `None` for "could not look" and
   every caller treats it as a failed probe.

2. `_suspicious_split_tickers` required the holder's share ratio (split × real trade) to
   agree with the implied price ratio (split alone) within 35%, so a holder who sold >26%
   or bought >54% THROUGH a split was never a suspect, never looked up, never restated:
   half a position sold rendered as a $4.0M BOUGHT. Suspects are now flagged on the price
   ratio alone (strongest first, so the lookup cap trims the price-only tail).

Hermetic: FMP is a recording fake; the DB tier is off.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.services import corporate_actions_service as mod
from app.services.corporate_actions_service import CorporateActionsService
from app.services.whale_service import WhaleService, _split_ratio_in_window


@pytest.fixture(autouse=True)
def _clear():
    mod._cache.clear()
    mod._inflight.clear()
    yield
    mod._cache.clear()
    mod._inflight.clear()


class _FMP:
    def __init__(self, full, raw, fail_first_full=False):
        self._full, self._raw = full, raw
        self.fail_first_full = fail_first_full
        self.full_calls = 0

    async def get_historical_prices(self, t, f=None, to=None):
        self.full_calls += 1
        if self.fail_first_full and self.full_calls == 1:
            raise RuntimeError("429 burst")
        return self._full

    async def get_historical_prices_non_split_adjusted(self, t, f=None, to=None):
        return self._raw


def _ten_to_one():
    rows = [("2026-06-10", 100.0, 1000.0), ("2026-06-11", 101.0, 1010.0),
            ("2026-06-12", 102.0, 102.0), ("2026-06-15", 103.0, 103.0)]
    return ([{"symbol": "KLAC", "date": d, "close": a} for d, a, _ in rows],
            [{"symbol": "KLAC", "date": d, "adjClose": r} for d, _, r in rows])


async def _none():
    return None


def _svc(monkeypatch, fake):
    monkeypatch.setattr(mod, "get_fmp_client", lambda: fake)
    s = CorporateActionsService()
    monkeypatch.setattr(s, "_db_get", lambda *a, **k: _none())
    monkeypatch.setattr(s, "_db_put", lambda *a, **k: _none())
    return s


# ── 1. None, not [] ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_derivation_is_none_not_an_empty_list(monkeypatch):
    s = _svc(monkeypatch, _FMP(*_ten_to_one(), fail_first_full=True))
    assert await s.get_split_rows("KLAC", "2026-03-31", "2026-06-30") is None


@pytest.mark.asyncio
async def test_a_clean_derivation_is_still_a_list(monkeypatch):
    s = _svc(monkeypatch, _FMP(*_ten_to_one()))
    rows = await s.get_split_rows("KLAC", "2026-03-31", "2026-06-30")
    assert rows and rows[0]["numerator"] == 10 and rows[0]["denominator"] == 1


@pytest.mark.asyncio
async def test_the_finding_replayed_first_leg_fails_second_succeeds(monkeypatch):
    """Exactly the divergence: ratio path degraded, gate path clean. The ratio helper
    now sees None (not 1.0-from-[]), which every caller arms the backstop on."""
    s = _svc(monkeypatch, _FMP(*_ten_to_one(), fail_first_full=True))
    rows = await s.get_split_rows("KLAC", "2026-03-31", "2026-06-30")
    assert rows is None
    flagged = await s.has_unclassified_adjustment(
        "KLAC", "2026-03-31", "2026-06-30", effective_from="2026-03-31", effective_to="2026-06-30",
    )
    assert flagged is False, "precondition: the second derivation is clean"
    # The OLD collapse would have produced this — a 1.0 with nothing to say it was unknown.
    assert _split_ratio_in_window(rows or [], "2026-03-31", "2026-06-30") == 1.0


@pytest.mark.parametrize("path, needle", [
    ("app/services/whale_service.py", r"if sl is None or isinstance\(sl, BaseException\):"),
    ("scripts/hydrate_whales.py", r"if sl is None or isinstance\(sl, BaseException\):"),
    ("app/services/holders_service.py", r"split_lookup_failed = stock_splits is None"),
])
def test_every_caller_treats_none_as_a_failed_lookup(path, needle):
    src = (Path(__file__).resolve().parents[1] / path).read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(needle, code), f"{path}: a None split list is not failed closed"


def test_the_whale_loop_arms_the_backstop_on_none():
    """Brace-bound to the loop: `unclassified_tickers.add(t)` must sit inside the
    `sl is None` arm, not merely somewhere in the function."""
    src = inspect.getsource(WhaleService._process_13f_path)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = code.index("if sl is None or isinstance(sl, BaseException):")
    j = code.index("continue", i)
    assert "unclassified_tickers.add(t)" in code[i:j]


def test_the_holders_build_ors_the_failed_lookup_into_the_gate():
    src = inspect.getsource(__import__("app.services.holders_service", fromlist=["x"]).HoldersService)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(r"if split_lookup_failed:\s*\n\s*inst_unclassified = True", code)


# ── 2. suspects through a trade ──────────────────────────────────────────────

PREV = [{"symbol": "NVDA", "value": 10_000_000, "sharesNumber": 100_000}]      # $100/sh


def _curr(value, shares):
    return [{"symbol": "NVDA", "value": value, "sharesNumber": shares}]


def test_a_holder_who_sold_half_through_a_ten_to_one_is_a_suspect():
    assert WhaleService._suspicious_split_tickers(_curr(5_000_000, 500_000), PREV) == ["NVDA"]


def test_a_holder_who_bought_through_a_split_is_a_suspect():
    # 10:1, then bought 80% more: 1,800,000 sh at $10 = $18M.
    assert WhaleService._suspicious_split_tickers(_curr(18_000_000, 1_800_000), PREV) == ["NVDA"]


def test_a_pure_split_is_a_suspect():
    assert WhaleService._suspicious_split_tickers(_curr(10_000_000, 1_000_000), PREV) == ["NVDA"]


def test_a_plain_buy_at_a_flat_price_is_not_a_suspect():
    assert WhaleService._suspicious_split_tickers(_curr(13_000_000, 130_000), PREV) == []


def test_a_modest_price_move_is_not_a_suspect():
    # +20%: 100,000 sh at $120.
    assert WhaleService._suspicious_split_tickers(_curr(12_000_000, 100_000), PREV) == []


def test_strong_signals_rank_before_price_only_ones():
    prev = [{"symbol": "AAA", "value": 10_000_000, "sharesNumber": 100_000},
            {"symbol": "BBB", "value": 10_000_000, "sharesNumber": 100_000}]
    curr = [{"symbol": "AAA", "value": 7_000_000, "sharesNumber": 100_000},     # -30%, price only
            {"symbol": "BBB", "value": 10_000_000, "sharesNumber": 1_000_000}]  # clean 10:1
    assert WhaleService._suspicious_split_tickers(curr, prev) == ["BBB", "AAA"]


def test_degenerate_rows_are_skipped():
    assert WhaleService._suspicious_split_tickers(_curr(0, 0), PREV) == []
    assert WhaleService._suspicious_split_tickers(_curr(float("nan"), 100), PREV) == []


# ── 3. a FAILED lookup never produces a FINAL snapshot ──────────────────────
#
# Caught by the regression review of the fail-closed arm above: arming the backstop on
# a transient 429 withheld the real rows, then the snapshot was upserted with a
# `raw_hash` over the raw filing. The hydrator skips on an equal hash and
# `_process_13f_path` serves an existing row as-is, so a one-second hiccup became a
# PERMANENT hole in that whale's trades that only `--force` could heal. Both writers
# now persist a degraded snapshot WITHOUT a hash ("re-derive me").

from datetime import datetime, timedelta, timezone

from app.services import whale_service as ws


def _arm_block(source_fn):
    src = inspect.getsource(source_fn)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def test_the_hydrator_loop_arms_the_backstop_on_none():
    """The `scripts/hydrate_whales.py` twin, brace-bound like the whale_service one —
    the `if` line alone was pinned before, so deleting the `.add(t)` inside it kept the
    guard green while the batch writer went back to ratio-1.0-with-gate-cleared."""
    src = (Path(__file__).resolve().parents[1] / "scripts/hydrate_whales.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = code.index("if sl is None or isinstance(sl, BaseException):")
    j = code.index("continue", i)
    assert "unclassified_tickers.add(t)" in code[i:j]
    assert "lookup_failed_tickers.add(t)" in code[i:j], "the hydrator does not mark the snapshot degraded"


@pytest.mark.parametrize("fn_src", [
    lambda: _arm_block(WhaleService._process_13f_path),
    lambda: (Path(__file__).resolve().parents[1] / "scripts/hydrate_whales.py").read_text(),
])
def test_both_writers_drop_the_hash_when_a_lookup_failed(fn_src):
    code = "\n".join(l for l in fn_src().splitlines() if not l.lstrip().startswith("#"))
    i = code.index("if lookup_failed_tickers:")
    j = code.index("raw_hash = None", i)
    assert j - i < 1200, "the degraded arm no longer nulls raw_hash"
    # and the batch-level fail-closed arm marks every suspect as failed, too
    assert "lookup_failed_tickers = set(suspects or [])" in code


def test_the_hydrator_never_skips_on_a_null_hash():
    src = (Path(__file__).resolve().parents[1] / "scripts/hydrate_whales.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(
        r'if raw_hash is not None and existing\.data\[0\]\.get\("raw_hash"\) == raw_hash:', code
    ), "a degraded (NULL) hash compared equal to a degraded existing row → skipped forever"


def test_a_degraded_snapshot_is_retried_only_after_the_window():
    now = datetime.now(timezone.utc)
    fresh = {"raw_hash": None, "processed_at": (now - timedelta(seconds=60)).isoformat()}
    old = {"raw_hash": None, "processed_at": (now - timedelta(seconds=ws._DEGRADED_SNAPSHOT_RETRY_SECONDS + 5)).isoformat()}
    assert WhaleService._degraded_snapshot_is_due(fresh) is False
    assert WhaleService._degraded_snapshot_is_due(old) is True
    # a Z-suffixed / naive / missing timestamp never blocks the retry forever
    assert WhaleService._degraded_snapshot_is_due({"processed_at": "2020-01-01T00:00:00Z"}) is True
    assert WhaleService._degraded_snapshot_is_due({"processed_at": "2020-01-01T00:00:00"}) is True
    assert WhaleService._degraded_snapshot_is_due({}) is True
    assert WhaleService._degraded_snapshot_is_due({"processed_at": "garbage"}) is True


def test_the_app_path_reads_a_hashed_snapshot_as_final_and_a_stale_degraded_one_as_a_miss():
    code = _arm_block(WhaleService._process_13f_path)
    i = code.index("if existing.data:")
    block = code[i:i + 600]
    assert 'row.get("raw_hash") is not None or not self._degraded_snapshot_is_due(row)' in block
    assert "return row" in block


# ── 4. the hedge-fund QUARTERS path (the "or []" the fix left load-bearing) ──
#
# `_build_hedge_fund_smart_money` used `get_split_rows(...) or []`, so a FAILED lookup
# became ratio 1.0 for every missing quarter, the quarter was computed from raw 13F
# counts, PERSISTED to `hedge_fund_quarters`, and reused as `existing` thereafter: a
# 10:1 split booked institutions as ~9x buyers, permanently. Same rule as the whale
# writers now — withhold, log, persist nothing. The flow hydrator mirrors it.

from app.services import holders_service as hs


@pytest.mark.asyncio
async def test_a_failed_split_lookup_withholds_the_missing_quarters_and_persists_nothing(monkeypatch):
    svc = hs.HoldersService.__new__(hs.HoldersService)
    fetched, saved = [], []

    class _FMP:
        async def get_institutional_ownership_for_quarter(self, ticker, y, q):
            fetched.append((y, q))
            return {"date": f"{y}-{q * 3:02d}-30", "numberOf13Fshares": 1_000_000_000,
                    "numberOf13FsharesChange": 900_000_000, "investorsHolding": 10,
                    "numberOfNewInvestors": 5, "numberOfInvestorsSold": 1}

    class _Actions:
        async def get_split_rows(self, ticker, from_date, to_date):
            return None                                   # "could not look"

    svc.fmp = _FMP()
    monkeypatch.setattr(hs, "corporate_actions_source", lambda owner=None: _Actions())
    monkeypatch.setattr(svc, "_load_existing_quarters", lambda ticker, pairs: {})
    monkeypatch.setattr(svc, "_save_quarters", lambda ticker, rows: saved.append(rows))
    monkeypatch.setattr(svc, "_build_quarterly_price_data", lambda daily, pairs: [])

    out = await svc._build_hedge_fund_smart_money("NVDA", daily_prices=[])
    assert fetched == [], "quarters were computed on ratio 1.0 through an unknown split"
    assert saved == [], "a degraded quarter was persisted"
    assert all(not p.has_activity for p in out.flow_data)


@pytest.mark.asyncio
async def test_a_clean_split_lookup_still_computes_and_persists(monkeypatch):
    """Anti-vacuity control: the healthy path is unchanged."""
    import asyncio as _asyncio
    svc = hs.HoldersService.__new__(hs.HoldersService)
    saved = []

    class _FMP:
        async def get_institutional_ownership_for_quarter(self, ticker, y, q):
            return {"date": f"{y}-{q * 3:02d}-30", "numberOf13Fshares": 1_000_000,
                    "numberOf13FsharesChange": 100_000, "investorsHolding": 10,
                    "numberOfNewInvestors": 5, "numberOfInvestorsSold": 1}

    class _Actions:
        async def get_split_rows(self, ticker, from_date, to_date):
            return []

    svc.fmp = _FMP()
    monkeypatch.setattr(hs, "corporate_actions_source", lambda owner=None: _Actions())
    monkeypatch.setattr(svc, "_load_existing_quarters", lambda ticker, pairs: {})
    monkeypatch.setattr(svc, "_save_quarters", lambda ticker, rows: saved.append(rows))
    monkeypatch.setattr(svc, "_build_quarterly_price_data", lambda daily, pairs: [])
    out = await svc._build_hedge_fund_smart_money("NVDA", daily_prices=[])
    await _asyncio.sleep(0.05)                             # the save runs in a thread
    assert any(p.has_activity for p in out.flow_data)
    assert saved and len(saved[0]) == 8


def test_the_flow_hydrator_refuses_a_none_split_lookup():
    src = (Path(__file__).resolve().parents[1] / "scripts/hydrate_hedge_fund_flow.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = code.index("if splits is None:")
    j = code.index("split_ratios = HoldersService._quarter_split_ratios(splits, to_fetch)")
    assert i < j, "the refusal must come before the ratios are built"
    assert "return" in code[i:j], "a None lookup must abort this ticker, not fall through"
