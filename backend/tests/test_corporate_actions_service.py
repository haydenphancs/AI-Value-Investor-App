"""Guard: the service around the split classifier — entitlement, dedup, caching, seam.

`test_split_derivation.py` covers the pure math. This covers everything the service adds,
and three of these are the load-bearing ones:

* **Blocked symbols never reach FMP.** ⚠️ FMP itself SERVES `/non-split-adjusted` and
  `/dividend-adjusted` for `^GSPC`, `GCUSD`, `BTCUSD` and `EURUSD` — measured live, real
  values — while `/full` and `/light` correctly answer 402 for the same symbol. That is FMP
  failing to enforce its own packages, not an entitlement: Indexes, Commodities and Crypto
  are not on the Order Form. Our guard is deliberately STRICTER than theirs, and this file
  is what keeps it that way.
* **One leg is never enough.** The derivation is a ratio between two series. If only one
  arrives, there is no answer — and the code must not manufacture one.
* **Only CLOSED windows are persisted.** A window touching today can still gain an event.

Hermetic: FMP and Supabase are both stubbed.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.services import corporate_actions_service as mod
from app.services.corporate_actions_service import (
    CorporateActionsService,
    corporate_actions_source,
    get_corporate_actions_service,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    mod._cache.clear()
    mod._inflight.clear()
    yield
    mod._cache.clear()
    mod._inflight.clear()


def _bars(rows):
    """(full, raw) FMP-shaped payloads from [(date, adjusted, raw), ...]."""
    return (
        [{"symbol": "AAA", "date": d, "close": a} for d, a, _ in rows],
        [{"symbol": "AAA", "date": d, "adjClose": r} for d, _, r in rows],
    )


TEN_TO_ONE = [
    ("2024-05-01", 100.0, 1000.0),
    ("2024-05-02", 101.0, 1010.0),
    ("2024-06-10", 102.0, 102.0),
    ("2024-06-11", 103.0, 103.0),
]


class _FMP:
    """Records every call so 'did it reach upstream?' is assertable, not assumed."""

    def __init__(self, full=None, raw=None, full_exc=None, raw_exc=None):
        self._full, self._raw = full, raw
        self._full_exc, self._raw_exc = full_exc, raw_exc
        self.calls = []

    async def get_historical_prices(self, t, f=None, to=None):
        self.calls.append(("full", t, f, to))
        if self._full_exc:
            raise self._full_exc
        return self._full

    async def get_historical_prices_non_split_adjusted(self, t, f=None, to=None):
        self.calls.append(("nsa", t, f, to))
        if self._raw_exc:
            raise self._raw_exc
        return self._raw

    async def get_historical_prices_dividend_adjusted(self, t, f=None, to=None):
        self.calls.append(("div", t, f, to))
        if self._raw_exc:
            raise self._raw_exc
        return self._raw


@pytest.fixture
def svc(monkeypatch):
    """A service wired to a recording fake, with the Supabase tier disabled."""
    def _make(full=None, raw=None, **kw):
        if full is None and raw is None:
            full, raw = _bars(TEN_TO_ONE)
        fake = _FMP(full, raw, **kw)
        monkeypatch.setattr(mod, "get_fmp_client", lambda: fake)
        s = CorporateActionsService()
        # Default the DB tier off; the tests that care patch it explicitly.
        monkeypatch.setattr(s, "_db_get", lambda *a, **k: _none())
        monkeypatch.setattr(s, "_db_put", lambda *a, **k: _none())
        return s, fake
    return _make


async def _none():
    return None


# ── Entitlement ─────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["^GSPC", "^VIX", "GCUSD", "CLUSD", "BTCUSD", "EURUSD"])
@pytest.mark.parametrize("kind", ["split", "dividend"])
async def test_a_blocked_symbol_never_reaches_fmp(svc, symbol, kind):
    """FMP serves these on the raw endpoints even though they are unlicensed (measured).

    So this is not belt-and-braces over an upstream 402 — for two of the four paths the
    upstream 402 does not exist, and this check is the only thing preventing an unlicensed
    call. Deleting it would silently start pulling index and crypto data we did not buy.
    """
    s, fake = svc()
    assert await s.get_adjustment_events(symbol, "2024-01-01", "2024-06-30", kind=kind) == []
    assert fake.calls == [], f"{symbol} reached upstream on kind={kind}"


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["AAPL", "SHOP.TO", "BRK-B"])
async def test_an_ordinary_equity_is_allowed(svc, symbol):
    s, fake = svc()
    await s.get_adjustment_events(symbol, "2024-01-01", "2024-06-30")
    assert [c[0] for c in fake.calls] == ["full", "nsa"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["", "   ", None])
async def test_an_empty_symbol_is_not_a_request(svc, symbol):
    s, fake = svc()
    assert await s.get_adjustment_events(symbol, "2024-01-01", "2024-06-30") == []
    assert fake.calls == []


# ── Both legs or nothing ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["full_exc", "raw_exc"])
async def test_one_failed_leg_yields_no_events_rather_than_half_an_answer(svc, which):
    full, raw = _bars(TEN_TO_ONE)
    s, _ = svc(full, raw, **{which: RuntimeError("upstream down")})
    assert await s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30") == []


@pytest.mark.asyncio
async def test_rows_for_a_different_symbol_are_refused(svc):
    """A caller bug must not derive a split from another company's prices."""
    full, raw = _bars(TEN_TO_ONE)
    for row in full:
        row["symbol"] = "ZZZZ"
    s, _ = svc(full, raw)
    assert await s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30") == []


# ── Shapes ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_split_rows_use_fmps_own_shape(svc):
    """The consumers parse this shape and are deliberately left unmodified."""
    s, _ = svc()
    rows = await s.get_split_rows("AAA", "2024-01-01", "2024-06-30")
    assert rows == [
        {"symbol": "AAA", "date": "2024-06-10", "numerator": 10, "denominator": 1}
    ]


@pytest.mark.asyncio
async def test_an_unclassified_action_is_absent_from_split_rows_but_present_in_events(svc):
    """A spin-off is NOT a split, so it must not appear in a list callers read as splits —
    but it must remain visible to anything that wants the full picture."""
    s, _ = svc(*_bars([
        ("2023-01-03", 52.89, 84.89),
        ("2023-01-04", 55.99, 70.16),      # GE HealthCare, measured 1.280866
    ]))
    assert await s.get_split_rows("AAA", "2022-12-01", "2023-03-31") == []
    mod._cache.clear()
    events = await s.get_adjustment_events("AAA", "2022-12-01", "2023-03-31")
    assert len(events) == 1 and events[0].is_split is False


@pytest.mark.asyncio
async def test_split_rows_are_newest_first(svc):
    s, _ = svc(*_bars([
        ("2024-01-02", 10.0, 400.0),
        ("2024-03-01", 11.0, 110.0),      # 4:1
        ("2024-06-10", 12.0, 12.0),       # 10:1
    ]))
    rows = await s.get_split_rows("AAA", "2024-01-01", "2024-06-30")
    assert [r["date"] for r in rows] == ["2024-06-10", "2024-03-01"]


@pytest.mark.asyncio
async def test_ex_dividend_dates_exclude_split_sized_steps(svc):
    """A split moves the dividend-adjusted series too; only sub-unity steps are dividends."""
    s, _ = svc(*_bars([
        ("2024-02-08", 100.0, 99.0),
        ("2024-02-09", 100.0, 99.87),     # ~0.13% step — a dividend
        ("2024-06-10", 100.0, 998.7),     # a 10x step — a split, not a dividend
    ]))
    assert await s.get_ex_dividend_dates("AAA", "2024-01-01", "2024-06-30") == ["2024-02-09"]


# ── Dedup and caching ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_callers_share_one_upstream_fetch(svc):
    s, fake = svc()
    out = await asyncio.gather(
        *[s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30") for _ in range(8)]
    )
    assert [c[0] for c in fake.calls] == ["full", "nsa"], "the fetch was not deduped"
    assert all(r == out[0] for r in out)


@pytest.mark.asyncio
async def test_a_second_call_is_served_from_cache(svc):
    s, fake = svc()
    await s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30")
    await s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30")
    assert len(fake.calls) == 2, "the second call re-fetched"


@pytest.mark.asyncio
async def test_the_inflight_entry_is_always_released(svc):
    s, _ = svc(full_exc=RuntimeError("boom"))
    await s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30")
    assert mod._inflight == {}, "a failed fetch leaked its in-flight future"


def test_window_closure_drives_the_ttl():
    today = date.today()
    assert mod._window_is_closed((today - timedelta(days=1)).isoformat()) is True
    assert mod._window_is_closed(today.isoformat()) is False
    assert mod._window_is_closed(None) is False
    assert mod._window_is_closed("not-a-date") is False
    assert mod._EVENTS_TTL_CLOSED > mod._EVENTS_TTL_OPEN


# ── Supabase tier ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_closed_window_is_read_from_the_db_instead_of_derived(svc, monkeypatch):
    s, fake = svc()
    stored = [mod.AdjustmentEvent("2024-06-10", 10.0, 10, 1)]

    async def _hit(*a, **k):
        return stored

    monkeypatch.setattr(s, "_db_get", _hit)
    rows = await s.get_split_rows("AAA", "2024-01-01", "2024-06-30")
    assert rows and rows[0]["numerator"] == 10
    assert fake.calls == [], "a DB hit must not also derive"


@pytest.mark.asyncio
async def test_an_open_window_is_never_written_to_the_db(svc, monkeypatch):
    """A window touching today can still gain an event, so it is not immutable."""
    writes = []
    s, _ = svc()
    monkeypatch.setattr(s, "_db_put", lambda *a, **k: (writes.append(a), _none())[1])
    await s.get_adjustment_events("AAA", "2024-01-01", date.today().isoformat())
    assert writes == []


@pytest.mark.asyncio
async def test_a_closed_window_is_written_to_the_db(svc, monkeypatch):
    writes = []
    s, _ = svc()
    monkeypatch.setattr(s, "_db_put", lambda *a, **k: (writes.append(a), _none())[1])
    await s.get_adjustment_events("AAA", "2024-01-01", "2024-06-30")
    assert len(writes) == 1, "a closed window should be persisted"


@pytest.mark.asyncio
async def test_a_dead_db_tier_still_returns_the_right_answer(svc, monkeypatch):
    """Migration 159 is applied by hand, so the table is absent for a while. Deploy order
    must not matter — the derivation simply runs."""
    async def _boom(*a, **k):
        raise RuntimeError("relation does not exist")

    s, _ = svc()
    monkeypatch.setattr(s, "_db_get", _boom)
    monkeypatch.setattr(s, "_db_put", _boom)
    rows = await s.get_split_rows("AAA", "2024-01-01", "2024-06-30")
    assert rows and rows[0]["numerator"] == 10


# ── The test seam ───────────────────────────────────────────────────────────────────────

def test_the_seam_prefers_an_injected_stand_in():
    class Owner:
        corporate_actions = "injected"

    assert corporate_actions_source(Owner()) == "injected"
    assert corporate_actions_source(None) is get_corporate_actions_service()
    assert corporate_actions_source(object()) is get_corporate_actions_service()


def test_the_consumers_go_through_the_seam():
    """Anti-vacuity for the five migrated call sites: bypassing the seam with a direct
    singleton call would leave them untestable and unfakeable."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("app/services/whale_service.py", "app/services/holders_service.py",
                "scripts/hydrate_whales.py", "scripts/hydrate_hedge_fund_flow.py"):
        src = root / rel
        code = "\n".join(
            l for l in src.read_text().splitlines() if not l.strip().startswith("#")
        )
        assert "corporate_actions_source(" in code, f"{rel} does not use the seam"
        assert "get_corporate_actions_service()" not in code, (
            f"{rel} calls the singleton directly, bypassing the injection seam"
        )
        assert "get_stock_splits(" not in code, (
            f"{rel} still calls the 402 endpoint /splits"
        )


# ── Dividend steps are an order of magnitude smaller than split steps ────────────────────

@pytest.mark.asyncio
async def test_a_small_dividend_is_not_dismissed_as_noise(svc):
    """REGRESSION. The split threshold was reused for dividends and swallowed most of them.

    A dividend moves the adjustment factor by about the yield of ONE payment, so the 0.5%
    `_FACTOR_EPS` tuned for splits discarded every payment below that. Live effect: KO's
    ~0.7% steps survived and AAPL's ~0.09% ones did not, so AAPL reported **no
    ex-dividend dates at all** while KO looked fine. Found by running it, not by a test —
    which is why the numbers below are the real measured magnitudes.
    """
    # AAPL-shaped: a $0.26 dividend on a ~$278 share is a 9.4e-04 step.
    s, _ = svc(*_bars([
        ("2026-02-06", 278.00, 277.74),
        ("2026-02-09", 278.00, 278.00),
        ("2026-02-10", 279.00, 279.00),
    ]))
    assert await s.get_ex_dividend_dates("AAA", "2026-01-01", "2026-03-31") == ["2026-02-09"]


@pytest.mark.asyncio
async def test_price_rounding_noise_is_still_rejected(svc):
    """The other side of that threshold. Measured background noise in the same series tops
    out at 1.51e-04 (KO); the threshold sits ~2.6x above it."""
    s, _ = svc(*_bars([
        ("2026-02-06", 278.00, 277.98),   # ~7e-05 — rounding, not a dividend
        ("2026-02-09", 278.00, 278.00),
        ("2026-02-10", 279.00, 279.00),
    ]))
    assert await s.get_ex_dividend_dates("AAA", "2026-01-01", "2026-03-31") == []


@pytest.mark.asyncio
async def test_a_dividend_is_never_labelled_a_one_for_one_split(svc):
    """A ~0.1% step sits inside `_SNAP_REL_TOL`, so classification would call every
    dividend a "1:1 split" — harmless downstream but false in the data."""
    s, _ = svc(*_bars([
        ("2026-02-06", 278.00, 277.74),
        ("2026-02-09", 278.00, 278.00),
        ("2026-02-10", 279.00, 279.00),
    ]))
    events = await s.get_adjustment_events("AAA", "2026-01-01", "2026-03-31", kind="dividend")
    assert len(events) == 1
    assert events[0].is_split is False, "a dividend is not a split"
    assert events[0].numerator is None and events[0].denominator is None


def test_the_two_thresholds_stay_an_order_of_magnitude_apart():
    """Anti-vacuity: collapsing them back into one is the bug, in either direction."""
    assert mod._DIVIDEND_FACTOR_EPS < mod._FACTOR_EPS / 5, (
        "the dividend threshold is too close to the split one — small dividends will be "
        "discarded as noise again"
    )
    # Above the measured worst noise (1.51e-04), below the smallest real payment (9.14e-04).
    assert 1.51e-4 < mod._DIVIDEND_FACTOR_EPS < 9.14e-4
