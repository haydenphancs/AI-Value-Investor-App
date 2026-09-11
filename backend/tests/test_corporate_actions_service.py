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

from types import SimpleNamespace

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
    """A window is 'closed' only after `_SETTLE_DAYS`: a row persisted the evening a
    quarter ends can freeze a not-yet-restated split as 'no split' for a whole quarter."""
    today = date.today()
    assert mod._window_is_closed((today - timedelta(days=mod._SETTLE_DAYS + 1)).isoformat()) is True
    assert mod._window_is_closed((today - timedelta(days=1)).isoformat()) is False
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


# ── Cache rows are DATA, not a trusted structure ────────────────────────────────────


class _FakeTable:
    """Minimum of the PostgREST builder chain `_db_get` walks."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


def _stub_cache_rows(monkeypatch, events):
    """Make `_db_get` read exactly `events` back out of the cache table."""
    rows = [{"events": events}]
    monkeypatch.setattr(
        "app.database.get_supabase",
        lambda: SimpleNamespace(table=lambda _n: _FakeTable(rows)),
    )


def _cache_event(**over):
    ev = {"date": "2024-06-10", "observed": 10.0, "numerator": 10, "denominator": 1}
    ev.update(over)
    return ev


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"denominator": 0},                        # ZeroDivisionError in `.ratio`
    {"numerator": 0},
    {"denominator": -1},
    {"numerator": "10", "denominator": "1"},   # TypeError in `.ratio`
    {"numerator": 10.0, "denominator": 1.0},   # float terms are not a named ratio
    {"numerator": True, "denominator": True},  # bool is an int subclass
    {"numerator": 10, "denominator": None},    # half a ratio
    {"numerator": None, "denominator": 2},
])
async def test_an_unusable_cached_ratio_degrades_to_unclassified(monkeypatch, bad):
    """`observed` was validated through `_finite`; the split terms were forwarded raw.

    `AdjustmentEvent.is_split` is only "both are not None", so `{"numerator": 10,
    "denominator": 0}` read back as a CLASSIFIED split and `.ratio` raised
    ZeroDivisionError inside the 13F restatement math — uncaught, on a user request.
    A string pair raised TypeError the same way.

    Dropping the pair is the conservative degradation: an unclassified event ARMS the
    implausible-share-flow backstop instead of fabricating a share multiplier.
    """
    _stub_cache_rows(monkeypatch, [_cache_event(**bad)])
    svc = mod.CorporateActionsService()

    events = await svc._db_get("AAPL", "split", "2024-01-01", "2024-12-31")

    assert events is not None and len(events) == 1
    ev = events[0]
    assert ev.is_split is False, f"{bad} must not read as a classified split"
    assert ev.ratio is None
    assert ev.observed == 10.0, "the observed factor is still a real measurement"


@pytest.mark.asyncio
async def test_a_well_formed_cached_ratio_survives(monkeypatch):
    """Mutation guard: the validation must not reject everything."""
    _stub_cache_rows(monkeypatch, [_cache_event()])
    svc = mod.CorporateActionsService()

    events = await svc._db_get("AAPL", "split", "2024-01-01", "2024-12-31")

    assert events is not None and len(events) == 1
    assert events[0].is_split is True
    assert events[0].ratio == 10.0


# ── The gate itself — the single point of failure for all three 13F writers ──────────
#
# `has_unclassified_adjustment` is what decides whether the magnitude backstop runs in
# `holders_service`, `whale_service._diff_quarters` and `scripts/hydrate_whales`. It had
# ZERO test coverage: every test that touched the backstop passed `unclassified_action` in
# as a hand-set literal, so mutating this method's body to `return False` — or inverting
# it, or switching `kind="split"` to `kind="dividend"` — left the whole suite green while
# disabling the backstop in production everywhere at once.


SPINOFF = [
    ("2023-01-03", 52.89, 84.89),
    ("2023-01-04", 55.99, 70.16),   # GE HealthCare, measured factor 1.280866 — unnameable
]


@pytest.mark.asyncio
async def test_the_gate_is_true_for_an_adjustment_it_cannot_name(svc):
    s, _ = svc(*_bars(SPINOFF))
    assert await s.has_unclassified_adjustment("AAA", "2022-12-01", "2023-03-31") is True


@pytest.mark.asyncio
async def test_the_gate_is_false_for_a_cleanly_classified_split(svc):
    """The other direction, and the one a constant-`True` mutation would break.

    Asserting only the True case would survive `return True`, which arms the backstop
    everywhere and silently deletes ~10% of real 13F holder rows.
    """
    s, _ = svc()  # TEN_TO_ONE
    assert await s.has_unclassified_adjustment("AAA", "2024-01-01", "2024-06-30") is False


@pytest.mark.asyncio
async def test_the_gate_is_false_when_nothing_happened(svc):
    flat = [("2024-05-01", 100.0, 100.0), ("2024-05-02", 101.0, 101.0),
            ("2024-05-03", 102.0, 102.0)]
    s, _ = svc(*_bars(flat))
    assert await s.has_unclassified_adjustment("AAA", "2024-01-01", "2024-06-30") is False


@pytest.mark.asyncio
async def test_the_gate_asks_about_splits_not_dividends(svc):
    """Pinned because `kind="dividend"` is a one-word mutation that stays green elsewhere.

    The dividend derivation uses a ~4e-4 threshold, so on the split fixture it reports a
    stream of tiny adjustments and the gate would answer True for every ticker that has
    ever paid a dividend.
    """
    s, fake = svc(*_bars(SPINOFF))
    await s.has_unclassified_adjustment("AAA", "2022-12-01", "2023-03-31")
    mod._cache.clear()
    as_split = await s.get_adjustment_events("AAA", "2022-12-01", "2023-03-31", kind="split")
    assert len(as_split) == 1 and as_split[0].is_split is False


@pytest.mark.asyncio
async def test_the_gate_shares_the_split_rows_fetch(svc):
    """"costs no extra fetch" is a load-bearing claim — both writers ask for both."""
    s, fake = svc(*_bars(SPINOFF))
    await s.get_split_rows("AAA", "2022-12-01", "2023-03-31")
    calls_after_first = len(fake.calls)

    await s.has_unclassified_adjustment("AAA", "2022-12-01", "2023-03-31")

    assert len(fake.calls) == calls_after_first, "the gate re-fetched instead of reusing"


# ── Effective window: the fetch window is NOT the question being asked ───────────────


@pytest.mark.asyncio
async def test_an_event_outside_the_effective_window_does_not_arm_the_gate(svc):
    """The over-reach both 13F callers had.

    Holders fetched four quarters (375 days) while narrowing its split RATIO to the single
    data quarter (91), so T's WBD spin-off of 2025-07-01 kept the gate True for a full year
    of later quarters. Whale's fetch is 10 days wider than the diffed period, so an event
    in the previous quarter's last 10 days — ~11% of every diff — flagged this one.
    """
    s, _ = svc(*_bars(SPINOFF))  # the unnameable event is dated 2023-01-04

    armed = await s.has_unclassified_adjustment("AAA", "2022-12-01", "2023-03-31")
    assert armed is True, "control: it IS inside the fetch window"

    narrowed = await s.has_unclassified_adjustment(
        "AAA", "2022-12-01", "2023-03-31",
        effective_from="2023-01-31", effective_to="2023-03-31",
    )
    assert narrowed is False, "the event predates the period being diffed"


@pytest.mark.asyncio
async def test_the_effective_window_start_is_exclusive(svc):
    """`from_excl < date <= to_incl`, matching `_quarter_split_ratios` and
    `_split_ratio_in_window`. An off-by-one here re-attributes an event to the wrong
    quarter, which is the whole class of bug this parameter exists to close."""
    s, _ = svc(*_bars(SPINOFF))

    assert await s.has_unclassified_adjustment(
        "AAA", "2022-12-01", "2023-03-31",
        effective_from="2023-01-04", effective_to="2023-03-31",
    ) is False, "an event ON the exclusive start belongs to the PREVIOUS period"

    assert await s.has_unclassified_adjustment(
        "AAA", "2022-12-01", "2023-03-31",
        effective_from="2023-01-03", effective_to="2023-01-04",
    ) is True, "and ON the inclusive end it belongs to this one"


def test_the_quarter_effective_window_is_the_quarter_not_the_fetch():
    from app.services.corporate_actions_service import (
        effective_window_for_quarter,
        window_for_quarters,
    )

    assert effective_window_for_quarter(2026, 2) == ("2026-03-31", "2026-06-30")
    # The fetch window is deliberately wider — it needs a bar BEFORE the period starts.
    fetch = window_for_quarters([(2026, 2)])
    assert fetch is not None and fetch[0] < "2026-03-31"


# ── A FAILED derivation must never be persisted as "no corporate action" ────────────
#
# The blocker this section exists for. `_derive` degraded to `[]` on four paths, and
# `get_adjustment_events` then wrote that into `corporate_action_cache` for any CLOSED
# window. A stored `[]` is BYTE-IDENTICAL to the legitimate "no adjustment here" — the
# answer to the large majority of queries — so no later query, TTL or DELETE predicate
# could ever find it again. Migration 159 has no `expires_at` and `_db_get` applies no
# freshness predicate, so the row is authoritative forever.
#
# The damage is not cosmetic: a poisoned row makes `_split_ratio_in_window` yield 1.0 (no
# restatement) AND `has_unclassified_adjustment` yield False (magnitude backstop
# disarmed). Both fail OPEN, and every fail-closed handler in `whale_service` /
# `holders_service` keys on an EXCEPTION — a cached row raises nothing. That reproduces
# KLAC's 10:1 rendering BlackRock at +$34,275.0M / +901.88%, written into `whale_trades`,
# which feeds user alerts.


class _RecordingDB:
    def __init__(self):
        self.writes = []

    async def put(self, sym, kind, from_date, to_date, events):
        self.writes.append((sym, kind, from_date, to_date, list(events)))

    async def get(self, *a, **k):
        return None


def _degradable(monkeypatch, svc_factory, **fmp_kwargs):
    """A service whose FMP legs degrade in the requested way, recording every db write."""
    s, fake = svc_factory(**fmp_kwargs)
    db = _RecordingDB()
    monkeypatch.setattr(s, "_db_get", db.get)
    monkeypatch.setattr(s, "_db_put", db.put)
    return s, db


CLOSED = ("2024-01-01", "2024-06-30")   # ends in the past -> `closed` is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs, why", [
    ({"full_exc": RuntimeError("429 rate limited")}, "the /full leg rate-limited"),
    ({"raw_exc": RuntimeError("429 rate limited")}, "the /non-split-adjusted leg failed"),
])
async def test_a_failed_leg_writes_nothing_to_the_cache(monkeypatch, svc, kwargs, why):
    s, db = _degradable(monkeypatch, svc, **kwargs)

    events = await s.get_adjustment_events("AAA", *CLOSED)

    assert events == [], "the public contract still hands callers a list"
    assert db.writes == [], f"{why}: a degraded derivation was PERSISTED as 'no split'"


@pytest.mark.asyncio
async def test_an_empty_leg_writes_nothing_either(monkeypatch, svc):
    """The route the exception check structurally cannot see.

    `fmp.get_historical_prices_non_split_adjusted` coerces any non-list response to `[]`,
    so a 200-with-junk answers "successfully, with nothing" — no exception is raised.
    """
    s, db = _degradable(monkeypatch, svc, full=_bars(TEN_TO_ONE)[0], raw=[])

    assert await s.get_adjustment_events("AAA", *CLOSED) == []
    assert db.writes == []


@pytest.mark.asyncio
async def test_a_symbol_mismatch_writes_nothing_either(monkeypatch, svc):
    full, raw = _bars(TEN_TO_ONE)
    wrong = [dict(r, symbol="BBB") for r in raw]
    s, db = _degradable(monkeypatch, svc, full=full, raw=wrong)

    assert await s.get_adjustment_events("AAA", *CLOSED) == []
    assert db.writes == []


@pytest.mark.asyncio
async def test_a_genuine_empty_result_IS_still_persisted(monkeypatch, svc):
    """The anti-over-correction control, and the case the migration exists for.

    "No split in this window" is the answer to the large majority of queries and is worth
    storing forever. Refusing to cache it would defeat the whole table.
    """
    flat = [("2024-05-01", 100.0, 100.0), ("2024-05-02", 101.0, 101.0),
            ("2024-05-03", 102.0, 102.0)]
    full, raw = _bars(flat)
    s, db = _degradable(monkeypatch, svc, full=full, raw=raw)

    assert await s.get_adjustment_events("AAA", *CLOSED) == []
    assert len(db.writes) == 1
    assert db.writes[0][4] == [], "an empty list from a REAL derivation must be stored"


@pytest.mark.asyncio
async def test_a_degraded_derivation_is_not_cached_in_memory_either(monkeypatch, svc):
    """`_EVENTS_TTL_CLOSED` is 6h — long enough to matter on its own."""
    s, db = _degradable(monkeypatch, svc, full_exc=RuntimeError("429"))

    await s.get_adjustment_events("AAA", *CLOSED)
    key = f"ca:split:AAA:{CLOSED[0]}:{CLOSED[1]}"

    assert mod._cache_get(key, mod._EVENTS_TTL_CLOSED) is None, (
        "a failure was cached in-process and will be served as 'no split' for 6 hours"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"full_exc": RuntimeError("429")},
    {"raw_exc": RuntimeError("429")},
    {"full": _bars(TEN_TO_ONE)[0], "raw": []},
])
async def test_the_gate_fails_closed_when_the_derivation_degrades(monkeypatch, svc, kwargs):
    """`has_unclassified_adjustment` is the SINGLE gate on the 13F magnitude backstop.

    Answering False on a derivation failure disarms it in all three writers at once, and
    no fail-closed handler catches it because nothing raised.
    """
    s, _ = _degradable(monkeypatch, svc, **kwargs)

    assert await s.has_unclassified_adjustment("AAA", *CLOSED) is True


@pytest.mark.asyncio
async def test_the_gate_still_answers_false_on_a_real_clean_split(monkeypatch, svc):
    """Mutation guard: fail-closed must not become always-closed."""
    s, _ = _degradable(monkeypatch, svc)   # TEN_TO_ONE, a cleanly classified 10:1

    assert await s.has_unclassified_adjustment("AAA", *CLOSED) is False
