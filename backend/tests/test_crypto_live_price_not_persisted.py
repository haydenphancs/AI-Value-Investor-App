"""`price_service.py` invariant #2 — never persist a live price — for the crypto path.

`crypto_fundamentals_cache` holds a `/coins/{id}` payload for `_DB_CACHE_TTL_HOURS` (12h),
and that payload's `market_data` carries the LIVE price, 24h change, 24h high/low, market cap
and volume. Before this guard, a crypto header whose 5-minute memory entry had expired
painted a 12-hour-old price as the current one — with the Key Statistics column agreeing, so
nothing on screen read as stale.

The shape of the fix, and what each test pins:
  • the WRITE persists the durable half only;
  • the READ strips too, so rows written before the fix cannot keep serving a price;
  • a DB hit re-hydrates from one live `/coins/markets` row;
  • an unavailable live row degrades to UNKNOWN, never to the persisted number.

Hermetic: the CoinGecko client and Supabase are both stubbed. Never touches the network.
"""
from __future__ import annotations

import pytest

from app.services import crypto_service as cs
from app.services.crypto_service import (
    _VOLATILE_CURRENCY_KEYED,
    _VOLATILE_MARKET_DATA_FIELDS,
    strip_volatile_market_data,
)


def _coin_payload(price=79_000.0):
    """A `/coins/{id}` payload shaped the way CoinGecko really answers: the currency-keyed
    fields are `{usd: …}` dicts, `price_change_24h` and `max_supply` are BARE floats."""
    return {
        "id": "bitcoin",
        "name": "Bitcoin",
        "market_data": {
            "current_price": {"usd": price},
            "market_cap": {"usd": 1.5e12},
            "total_volume": {"usd": 4.2e10},
            "high_24h": {"usd": price * 1.02},
            "low_24h": {"usd": price * 0.98},
            "price_change_24h": 1_200.0,
            "price_change_percentage_24h": 1.54,
            "price_change_percentage_30d": 8.1,
            "price_change_percentage_1y": 44.0,
            "circulating_supply": 19_800_000.0,
            "total_supply": 21_000_000.0,
            "max_supply": 21_000_000.0,
            "last_updated": "2026-09-08T00:00:00Z",
        },
        "description": {"en": "durable prose"},
        "genesis_date": "2009-01-03",
    }


def _markets_row(price=81_500.0):
    """A `/coins/markets` row — every volatile field, all BARE floats."""
    return {
        "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
        "current_price": price,
        "market_cap": 1.55e12,
        "total_volume": 4.4e10,
        "high_24h": price * 1.01,
        "low_24h": price * 0.99,
        "price_change_24h": 900.0,
        "price_change_percentage_24h": 1.12,
        "last_updated": "2026-09-09T12:00:00Z",
    }


# ── the strip ───────────────────────────────────────────────────────────────

def test_the_persisted_payload_carries_no_price():
    out = strip_volatile_market_data(_coin_payload())
    md = out["market_data"]
    for field in _VOLATILE_MARKET_DATA_FIELDS:
        assert field not in md, f"{field} would be persisted for 12h"
    assert out["_volatile_stripped"] is True


def test_the_strip_keeps_everything_durable():
    """Supply, description and the genesis date do not move — stripping them would turn a
    cache hit into a half-empty screen for no benefit."""
    out = strip_volatile_market_data(_coin_payload())
    md = out["market_data"]
    assert md["circulating_supply"] == 19_800_000.0
    assert md["total_supply"] == 21_000_000.0
    assert md["max_supply"] == 21_000_000.0
    # A rolling 30-day return moves every minute — it is NOT durable. Stripped, so a DB
    # hit recomputes it from live history instead of serving a 12-hour-old figure.
    assert "price_change_percentage_30d" not in md
    assert "price_change_percentage_1y" not in md
    assert out["description"]["en"] == "durable prose"
    assert out["genesis_date"] == "2009-01-03"


def test_the_strip_does_not_mutate_its_argument():
    """The argument is ALSO the in-memory tier's object. An in-place pop would blank the
    header for every caller until the next upstream fetch."""
    payload = _coin_payload()
    strip_volatile_market_data(payload)
    assert payload["market_data"]["current_price"] == {"usd": 79_000.0}


@pytest.mark.parametrize("value", [None, "", 0, [], "not a dict", {"market_data": None}])
def test_the_strip_tolerates_a_malformed_payload(value):
    """A degraded `/coins/{id}` answer must not 500 the write path."""
    strip_volatile_market_data(value)


def test_every_stripped_field_exists_on_a_markets_row():
    """The correspondence that makes the strip safe: a field removed from the persisted
    payload has to be re-obtainable, or it becomes permanently absent instead of live."""
    row = _markets_row()
    missing = [f for f in _VOLATILE_MARKET_DATA_FIELDS if f not in row]
    assert missing == [], f"stripped but not re-hydratable: {missing}"


def test_the_currency_keyed_set_is_a_subset_of_the_stripped_set():
    assert _VOLATILE_CURRENCY_KEYED <= set(_VOLATILE_MARKET_DATA_FIELDS)


# ── the re-hydration ────────────────────────────────────────────────────────

class _FakeCG:
    """Minimal stand-in for `CoinGeckoClient`. `fail=True` reproduces an outage."""

    def __init__(self, row=None, fail=False):
        self._row = row
        self.fail = fail
        self.markets_calls: list[list[str]] = []

    async def resolve_coin_id(self, base):
        if self.fail:
            raise RuntimeError("coingecko down")
        return "bitcoin" if base.upper() == "BTC" else None

    async def get_markets(self, bases):
        self.markets_calls.append(list(bases))
        if self.fail:
            raise RuntimeError("coingecko down")
        return [self._row] if self._row else []


@pytest.fixture
def svc(monkeypatch):
    """A service instance with the module memo cleared and Supabase never constructed."""
    cs._cache.clear()
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_coingecko_client", lambda: _FakeCG(), raising=True)
    s = cs.CryptoService()
    yield s
    cs._cache.clear()


@pytest.mark.asyncio
async def test_a_db_hit_serves_the_live_price_not_the_persisted_one(svc):
    """The headline case: a 12h-old row + a live market row → the LIVE number."""
    svc.coingecko = _FakeCG(_markets_row(price=81_500.0))
    durable = strip_volatile_market_data(_coin_payload(price=79_000.0))

    out = await svc._rehydrate_volatile("BTC", durable)
    md = out["market_data"]

    assert md["current_price"] == {"usd": 81_500.0}, "served the persisted price"
    assert md["price_change_24h"] == 900.0
    assert md["price_change_percentage_24h"] == 1.12
    assert md["market_cap"] == {"usd": 1.55e12}
    assert md["high_24h"] == {"usd": 81_500.0 * 1.01}
    assert "_volatile_stripped" not in out


@pytest.mark.asyncio
async def test_the_currency_keyed_fields_are_re_wrapped_as_usd_dicts(svc):
    """`/coins/markets` answers bare floats where `/coins/{id}` answers `{usd: …}`. Writing
    the bare float back would hit `_usd`'s `not isinstance(sub, dict)` arm and read as a
    silent 0 — the same trap that once put `previousClose` exactly on the last tick."""
    svc.coingecko = _FakeCG(_markets_row())
    out = await svc._rehydrate_volatile("BTC", strip_volatile_market_data(_coin_payload()))
    md = out["market_data"]
    for field in _VOLATILE_MARKET_DATA_FIELDS:
        if field in _VOLATILE_CURRENCY_KEYED:
            assert isinstance(md[field], dict) and "usd" in md[field], field
        else:
            assert not isinstance(md[field], dict), field


@pytest.mark.asyncio
async def test_an_outage_degrades_to_unknown_never_to_the_stale_price(svc):
    """The whole point. With no live row the price must be ABSENT, so `_usd_opt` renders
    "—" and `get_crypto_core` raises into a skeleton — not a plausible 12-hour-old number."""
    svc.coingecko = _FakeCG(fail=True)
    out = await svc._rehydrate_volatile("BTC", strip_volatile_market_data(_coin_payload()))
    assert "current_price" not in out["market_data"]
    assert "price_change_24h" not in out["market_data"]
    # ...and the durable half survived, so supply/description still render.
    assert out["market_data"]["circulating_supply"] == 19_800_000.0


@pytest.mark.asyncio
async def test_a_null_field_on_the_live_row_stays_absent(svc):
    """CoinGecko sends JSON null for a field it has no value for. Copying it through as 0
    would be the fabrication; skipping it leaves the consumer's None degrade intact."""
    row = _markets_row()
    row["high_24h"] = None
    row["price_change_24h"] = None
    svc.coingecko = _FakeCG(row)
    md = (await svc._rehydrate_volatile("BTC", strip_volatile_market_data(_coin_payload())))["market_data"]
    assert "high_24h" not in md
    assert "price_change_24h" not in md
    assert md["current_price"] == {"usd": 81_500.0}


@pytest.mark.asyncio
async def test_the_live_row_is_memoised_so_a_burst_costs_one_credit(svc):
    """100,000 calls/month is 2.3/minute sustained — the budget is the binding constraint,
    so the DB-hit overlay must not be a per-request call."""
    fake = _FakeCG(_markets_row())
    svc.coingecko = fake
    durable = strip_volatile_market_data(_coin_payload())
    for _ in range(5):
        await svc._rehydrate_volatile("BTC", durable)
    assert len(fake.markets_calls) == 1, fake.markets_calls


@pytest.mark.asyncio
async def test_an_unresolvable_symbol_does_not_call_markets(svc):
    fake = _FakeCG(_markets_row())
    svc.coingecko = fake
    out = await svc._rehydrate_volatile("NOTACOIN", strip_volatile_market_data(_coin_payload()))
    assert fake.markets_calls == []
    assert "current_price" not in out["market_data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [True, False])
async def test_a_legacy_unstripped_row_never_leaks_its_price(svc, fail):
    """Rows written BEFORE this fix still hold a live price and no `_volatile_stripped`
    marker, and they stay readable for up to `_DB_CACHE_TTL_HOURS`. Re-hydration must strip
    its own input, so the guarantee holds even if the read-side strip is removed — and it
    must hold on the OUTAGE branch too, which is where a `return durable` would leak."""
    svc.coingecko = _FakeCG(None if fail else _markets_row(), fail=fail)
    legacy = _coin_payload(price=79_000.0)          # NOT stripped — as persisted pre-fix
    md = (await svc._rehydrate_volatile("BTC", legacy))["market_data"]
    if fail:
        assert "current_price" not in md
    else:
        assert md["current_price"] == {"usd": 81_500.0}
    assert md.get("current_price") != {"usd": 79_000.0}


# ── the two DB boundaries ───────────────────────────────────────────────────

class _FakeTable:
    """Records the upsert payload; answers a select with `row`."""

    def __init__(self, row=None):
        self.row = row
        self.upserted: list[dict] = []

    def upsert(self, payload, on_conflict=None):
        self.upserted.append(payload)
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        class _R:
            pass
        r = _R()
        r.data = [self.row] if self.row else []
        return r


class _FakeSupabase:
    def __init__(self, row=None):
        self.tbl = _FakeTable(row)

    def table(self, _name):
        return self.tbl


def test_the_write_boundary_never_sends_a_price_to_supabase(svc):
    """M1: the strip has to be AT the upsert. Anywhere else and a row already in the table
    keeps serving a live price for 12h after the fix deploys."""
    fake = _FakeSupabase()
    svc.supabase = fake
    svc._upsert_crypto_cache_db("BTC", _coin_payload())

    assert len(fake.tbl.upserted) == 1
    persisted = fake.tbl.upserted[0]["response_json"]["market_data"]
    for field in _VOLATILE_MARKET_DATA_FIELDS:
        assert field not in persisted, f"{field} reached Supabase"
    assert persisted["circulating_supply"] == 19_800_000.0


def test_the_read_boundary_strips_a_legacy_row(svc):
    """M2: rows written before the fix are still live for `_DB_CACHE_TTL_HOURS`. The read
    strips so they cannot serve their persisted price even for that window."""
    from datetime import datetime, timezone

    svc.supabase = _FakeSupabase({
        "response_json": _coin_payload(price=79_000.0),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    })
    got = svc._check_crypto_cache_db("BTC")
    assert got is not None, "a fresh row must still be a cache hit"
    for field in _VOLATILE_MARKET_DATA_FIELDS:
        assert field not in got["market_data"]
    assert got["market_data"]["max_supply"] == 21_000_000.0


def test_an_expired_row_is_still_a_miss(svc):
    """The strip must not turn the TTL check into a no-op."""
    from datetime import datetime, timedelta, timezone

    stale = datetime.now(timezone.utc) - timedelta(hours=cs._DB_CACHE_TTL_HOURS + 1)
    svc.supabase = _FakeSupabase({
        "response_json": _coin_payload(), "cached_at": stale.isoformat(),
    })
    assert svc._check_crypto_cache_db("BTC") is None
