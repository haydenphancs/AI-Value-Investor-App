"""`POST /users/me/claim-guest-data` — moving an install's guest data onto a new account.

Migration 108 partitions guests by install, which creates a funnel trap: a user adds
tickers during first-run onboarding, signs up, and their watchlist is EMPTY, because a
real account keys off its user id. Signing in would cost them data — the opposite of
the upgrade the pricing model promises.

The dangerous case is the shared legacy bucket: `guest_user_id_for(None)` returns
GUEST_USER_ID, where every pre-migration guest row still lives. Claiming that would
pull OTHER people's tickers into one account — the very leak 108 closes.
"""

import pytest

import app.api.v1.endpoints.users as users_ep
from app.dependencies import GUEST_USER_ID, guest_user_id_for

_USER = {"id": "11111111-2222-4333-8444-555555555555"}


class _Q:
    def __init__(self, store, log, table):
        self.store, self.log, self.table = store, log, table
        self._filter = None
        self._op = None
        self._payload = None

    def select(self, *a): self._op = "select"; return self
    def update(self, payload): self._op = "update"; self._payload = payload; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, col, val): self._filter = ("eq", col, val); return self
    def in_(self, col, vals): self._filter = ("in", col, list(vals)); return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        kind, col, val = self._filter
        match = (
            (lambda r: r.get(col) == val) if kind == "eq"
            else (lambda r: r.get(col) in val)
        )
        hit = [r for r in rows if match(r)]
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
            self.log.append(("update", self.table, len(hit)))
        elif self._op == "delete":
            self.store[self.table] = [r for r in rows if not match(r)]
            self.log.append(("delete", self.table, len(hit)))
        return type("R", (), {"data": [dict(r) for r in hit]})()


class _SB:
    def __init__(self, store):
        self.store, self.log = store, []

    def table(self, name):
        return _Q(self.store, self.log, name)


async def _claim(sb, guest_id):
    return await users_ep.claim_guest_data(user=_USER, x_guest_id=guest_id, supabase=sb)


@pytest.mark.asyncio
async def test_claims_this_installs_watchlist_onto_the_account():
    bucket = guest_user_id_for("install-A")
    store = {"watchlist_items": [
        {"id": 1, "ticker": "AAPL", "user_id": bucket},
        {"id": 2, "ticker": "NVDA", "user_id": bucket},
    ], "portfolios": []}
    sb = _SB(store)

    res = await _claim(sb, "install-A")

    assert res["claimed"]["watchlist_items"] == 2
    assert {r["user_id"] for r in store["watchlist_items"]} == {_USER["id"]}


@pytest.mark.asyncio
async def test_never_claims_the_shared_legacy_bucket():
    """THE security case. Those rows belong to other people."""
    store = {"watchlist_items": [
        {"id": 1, "ticker": "SOMEONE_ELSES", "user_id": GUEST_USER_ID},
    ], "portfolios": []}
    sb = _SB(store)

    res = await _claim(sb, None)          # no header → shared sentinel

    assert res["claimed"]["watchlist_items"] == 0
    assert store["watchlist_items"][0]["user_id"] == GUEST_USER_ID, "stole a shared row"


@pytest.mark.asyncio
async def test_blank_guest_id_is_also_refused():
    store = {"watchlist_items": [{"id": 1, "ticker": "X", "user_id": GUEST_USER_ID}],
             "portfolios": []}
    sb = _SB(store)
    res = await _claim(sb, "   ")
    assert res["claimed"]["watchlist_items"] == 0


@pytest.mark.asyncio
async def test_duplicate_tickers_are_dropped_not_collided():
    """watchlist_items has UNIQUE(user_id, ticker); moving a ticker the account
    already holds would violate it and abort the whole claim."""
    bucket = guest_user_id_for("install-A")
    store = {"watchlist_items": [
        {"id": 1, "ticker": "AAPL", "user_id": bucket},     # account already has it
        {"id": 2, "ticker": "NVDA", "user_id": bucket},     # genuinely new
        {"id": 9, "ticker": "AAPL", "user_id": _USER["id"]},
    ], "portfolios": []}
    sb = _SB(store)

    res = await _claim(sb, "install-A")

    assert res["claimed"]["watchlist_items"] == 1           # only NVDA moved
    tickers = sorted(r["ticker"] for r in store["watchlist_items"])
    assert tickers == ["AAPL", "NVDA"]                      # no duplicate row left
    assert all(r["user_id"] == _USER["id"] for r in store["watchlist_items"])


@pytest.mark.asyncio
async def test_is_idempotent():
    bucket = guest_user_id_for("install-A")
    store = {"watchlist_items": [{"id": 1, "ticker": "AAPL", "user_id": bucket}],
             "portfolios": []}
    sb = _SB(store)

    first = await _claim(sb, "install-A")
    second = await _claim(sb, "install-A")

    assert first["claimed"]["watchlist_items"] == 1
    assert second["claimed"]["watchlist_items"] == 0        # nothing left to move
    assert len(store["watchlist_items"]) == 1               # and nothing duplicated


@pytest.mark.asyncio
async def test_portfolios_move_too():
    bucket = guest_user_id_for("install-A")
    store = {"watchlist_items": [], "portfolios": [{"id": "p1", "user_id": bucket}]}
    sb = _SB(store)

    res = await _claim(sb, "install-A")

    assert res["claimed"]["portfolios"] == 1
    assert store["portfolios"][0]["user_id"] == _USER["id"]


@pytest.mark.asyncio
async def test_a_failure_never_blocks_sign_in():
    """The user is ALREADY authenticated when this runs. A 500 here would turn a
    successful sign-in into a visible error."""
    class _Boom:
        def table(self, name):
            raise RuntimeError("supabase down")

    res = await _claim(_Boom(), "install-A")
    assert "error" in res and res["claimed"]["watchlist_items"] == 0


@pytest.mark.asyncio
async def test_claiming_does_not_touch_another_installs_rows():
    bucket_a = guest_user_id_for("install-A")
    bucket_b = guest_user_id_for("install-B")
    store = {"watchlist_items": [
        {"id": 1, "ticker": "AAPL", "user_id": bucket_a},
        {"id": 2, "ticker": "TSLA", "user_id": bucket_b},
    ], "portfolios": []}
    sb = _SB(store)

    await _claim(sb, "install-A")

    other = [r for r in store["watchlist_items"] if r["id"] == 2][0]
    assert other["user_id"] == bucket_b, "claimed another install's data"
