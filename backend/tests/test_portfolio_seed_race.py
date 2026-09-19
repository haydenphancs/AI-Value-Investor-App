"""`_seed_default_portfolio` must survive a concurrent seed instead of 500-ing.

`GET /portfolios` seeds a portfolio literally named "Holdings" the first time a user
has none. `ContentView` mounts every tab at launch, so `TrackingViewModel.init` and
the Home fetch can both hit that route inside the same window — and
`POST /users/me/claim-guest-data` can move a guest "Holdings" over at the same moment.
`portfolios_user_id_name_key UNIQUE (user_id, name)` then makes the loser's bare
`.insert(...).execute().data[0]` raise 23505, which surfaced as an unhandled 500 on a
read-only endpoint.

The fix adopts the winner. What it must NOT become is a generic error-swallower, so
both re-raise paths are pinned here, and so is the fact that the dropped items are
recovered by the pre-existing `_backfill_lone_empty_portfolio` heal rather than lost.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import app.api.v1.endpoints.portfolios as pf


class _Violation(Exception):
    """Shaped like postgrest's unique-violation APIError."""

    def __init__(self, constraint="portfolios_user_id_name_key"):
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.code = "23505"


class _Q:
    def __init__(self, sb, table):
        self.sb, self.table = sb, table
        self._op = "select"
        self._payload = None
        self._eq = {}

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, payload, *a, **k):
        self._op = "insert"
        self._payload = payload
        return self

    def upsert(self, payload, *a, **k):
        self._op = "upsert"
        self._payload = payload
        self._kwargs = dict(k)
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, *a, **k):
        return self

    def range(self, *a, **k):   # the paged reads go through fetch_all_rows
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self.sb.ops.append((self.table, self._op))
        if self._op == "insert":
            self.sb.inserts.append((self.table, self._payload))
            exc = self.sb.insert_errors.get(self.table)
            if exc is not None:
                self.sb.insert_errors[self.table] = None  # fire once
                raise exc
        if self._op == "upsert":
            self.sb.upserts.append((self.table, self._payload, getattr(self, "_kwargs", {})))
            # Models ON CONFLICT: a plain upsert (DO UPDATE) on a row that already exists
            # in `existing_keys` still succeeds, and `ignore_duplicates=True` (DO NOTHING)
            # succeeds too — only a plain INSERT raises 23505. The seed's items write is
            # what this distinction pins (F15-4).
        return type("R", (), {"data": self.sb.rows.get((self.table, self._op), [])})()


class _SB:
    def __init__(self, rows=None, insert_errors=None):
        self.rows = rows or {}
        self.insert_errors = dict(insert_errors or {})
        self.ops: list[tuple[str, str]] = []
        self.inserts: list[tuple[str, object]] = []
        self.upserts: list[tuple[str, object, dict]] = []

    def table(self, name):
        return _Q(self, name)

    def count(self, table, op):
        return sum(1 for t, o in self.ops if t == table and o == op)


_WATCHLIST = [{"ticker": "NVDA", "added_at": "2026-08-01", "shares": 3,
               "market_value": 100.0}]


# ── non-vacuity ──────────────────────────────────────────────────────────────

def test_the_fake_really_raises_on_the_seed_insert():
    """If the injected error stopped firing, every test below would pass for free."""
    sb = _SB(insert_errors={"portfolios": _Violation()})
    with pytest.raises(_Violation):
        sb.table("portfolios").insert({"name": "Holdings"}).execute()


# ── the race ─────────────────────────────────────────────────────────────────

def test_seed_adopts_the_winner_instead_of_raising():
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [{"id": "winner"}],
        },
        insert_errors={"portfolios": _Violation()},
    )

    pf._seed_default_portfolio(sb, "u1")  # must not raise

    assert sb.count("portfolios", "select") == 1, "did not re-read to find the winner"


def test_seed_writes_no_items_when_it_loses_the_race():
    """The winner owns the items.

    Writing them here would collide on portfolio_items_portfolio_id_ticker_key, and
    if the winner seeded an EMPTY group the pre-existing
    `_backfill_lone_empty_portfolio` heal fills it on the next GET /portfolios — so
    there is no second repair path to maintain.
    """
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [{"id": "winner"}],
        },
        insert_errors={"portfolios": _Violation()},
    )

    pf._seed_default_portfolio(sb, "u1")

    assert sb.count("portfolio_items", "insert") == 0
    assert sb.count("portfolio_items", "upsert") == 0


def test_the_existing_backfill_heal_recovers_the_dropped_items():
    """Proves the items are RECOVERABLE, not lost — the premise of the test above."""
    class _Empty:
        id = "winner"
        # `items`, not `tickers`. The production guard read `getattr(only, "tickers", ...)`
        # against a model whose field is `items`, so it was dead — and every stub that
        # spelled it the buggy way kept that invisible (fixed 2026-09-12).
        items: list = []
        # NEVER EDITED — the seed inserts `created_at` and `updated_at` from the same
        # `now()`. The heal is scoped to exactly that state now, because a group the user
        # deliberately emptied must NOT be re-seeded from the watchlist on every launch.
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    sb = _SB(rows={("watchlist_items", "select"): _WATCHLIST})
    captured = {}

    def _refetch(_sb, _uid):
        captured["refetched"] = True
        return ["healed"]

    original = pf._fetch_user_portfolios
    pf._fetch_user_portfolios = _refetch
    try:
        out = pf._backfill_lone_empty_portfolio(sb, "u1", [_Empty()])
    finally:
        pf._fetch_user_portfolios = original

    assert captured.get("refetched"), "the heal did not run"
    assert out == ["healed"]
    assert sb.count("portfolio_items", "upsert") == 1, \
        "the watchlist ticker was not written into the empty group"


def test_the_happy_path_is_unchanged():
    """Negative control: no race → the seed still creates the group AND its items."""
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "insert"): [{"id": "new"}],
        }
    )

    pf._seed_default_portfolio(sb, "u1")

    assert sb.count("portfolios", "insert") == 1
    # `upsert`, not `insert` — see the F15-4 block below.
    assert sb.count("portfolio_items", "upsert") == 1
    assert sb.count("portfolio_items", "insert") == 0
    assert sb.count("portfolios", "select") == 0, "no need to re-read when we won"


# ── the two re-raise controls ────────────────────────────────────────────────

def test_a_non_unique_error_is_re_raised():
    """This must not become a generic swallower."""
    sb = _SB(
        rows={("watchlist_items", "select"): _WATCHLIST},
        insert_errors={"portfolios": RuntimeError("relation is being vacuumed")},
    )

    with pytest.raises(RuntimeError):
        pf._seed_default_portfolio(sb, "u1")


def test_a_23505_with_no_holdings_row_is_re_raised():
    """The violation was on some OTHER constraint — swallowing it would hide a bug."""
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [],  # no winner to adopt
        },
        insert_errors={"portfolios": _Violation("some_other_constraint")},
    )

    with pytest.raises(Exception) as excinfo:
        pf._seed_default_portfolio(sb, "u1")
    assert "portfolios_user_id_name_key" in str(excinfo.value)


def test_an_empty_insert_result_also_adopts_rather_than_indexerrors():
    """The bare `.data[0]` also IndexError'd when PostgREST returned no row.

    Same 500, different trigger; the `if not inserted:` guard covers both.
    """
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "insert"): [],       # insert succeeded, returned nothing
            ("portfolios", "select"): [{"id": "winner"}],
        }
    )

    pf._seed_default_portfolio(sb, "u1")  # must not raise
    assert sb.count("portfolio_items", "insert") == 0
    assert sb.count("portfolio_items", "upsert") == 0


# ── F15-4: the seed's ITEMS write must survive a concurrent mirror ───────────
#
# The group is inserted ACTIVE and is visible to `POST /watchlist` immediately, so a star
# tapped on a detail screen in the same second is mirrored into it by
# `_write_through_to_active_portfolio` BEFORE the seed writes its rows. A plain multi-row
# `.insert` then hit `portfolio_items_portfolio_id_ticker_key` on the mirrored ticker, the
# whole statement was rejected, GET /portfolios answered 500 — and because the group now
# held the one mirrored row, `_backfill_lone_empty_portfolio` (`if only.items: return`)
# never healed the other tickers. They stayed watchlist-only and invisible everywhere.

_THREE = [
    {"ticker": "AAPL", "added_at": "2026-08-03", "shares": 1, "market_value": 10.0},
    {"ticker": "MSFT", "added_at": "2026-08-02", "shares": None, "market_value": None},
    {"ticker": "NVDA", "added_at": "2026-08-01", "shares": 3, "market_value": 100.0},
]


def test_the_fake_really_raises_on_a_plain_items_insert():
    """Anti-vacuity for the block: the injected 23505 fires on INSERT only. If the seed
    still used `.insert`, the test below would raise; if it uses `.upsert`, it must not."""
    sb = _SB(insert_errors={"portfolio_items": _Violation("portfolio_items_portfolio_id_ticker_key")})
    with pytest.raises(_Violation):
        sb.table("portfolio_items").insert([{"ticker": "NVDA"}]).execute()


def test_a_mirrored_ticker_no_longer_rejects_the_whole_seed():
    """The race, modelled: the items statement would 23505 as a plain INSERT (NVDA was
    mirrored in between the group insert and this write). The seed must not raise, and
    must still carry EVERY watchlist ticker in the one conflict-tolerant write."""
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _THREE,
            ("portfolios", "insert"): [{"id": "new"}],
        },
        insert_errors={"portfolio_items": _Violation("portfolio_items_portfolio_id_ticker_key")},
    )

    pf._seed_default_portfolio(sb, "u1")  # must not raise

    assert sb.count("portfolio_items", "insert") == 0, "a plain INSERT is what 500'd the route"
    assert len(sb.upserts) == 1
    table, payload, kwargs = sb.upserts[0]
    assert table == "portfolio_items"
    assert [r["ticker"] for r in payload] == ["AAPL", "MSFT", "NVDA"], (
        "every seed ticker must be in the ONE write — dropping the mirrored one would "
        "leave the others to a heal that never runs on a non-empty group"
    )


def test_the_seed_upsert_is_conflict_tolerant_on_the_membership_key():
    """`on_conflict` must name the real unique key and `ignore_duplicates` must be True
    (DO NOTHING): DO UPDATE would move the mirrored row's position and would still fail on
    an intra-batch duplicate (two watchlist spellings collapsing under `.upper()`)."""
    sb = _SB(rows={("watchlist_items", "select"): _THREE,
                   ("portfolios", "insert"): [{"id": "new"}]})

    pf._seed_default_portfolio(sb, "u1")

    _t, _payload, kwargs = sb.upserts[0]
    assert kwargs.get("on_conflict") == "portfolio_id,ticker", kwargs
    assert kwargs.get("ignore_duplicates") is True, kwargs


def test_the_seed_upsert_carries_positions_and_holdings():
    """The write is the same rows the INSERT used to carry — seeded newest-first, each
    with the watchlist row's shares / market_value, and a blank ticker skipped."""
    # Blank rows are OLDEST so they sort last: `position` is the enumerate index over the
    # sorted seed rows (a pre-existing gap-tolerant rule, not something this fix changes).
    rows = _THREE + [{"ticker": "", "added_at": "2026-07-09", "shares": 9, "market_value": 9.0},
                     {"ticker": None, "added_at": "2026-07-08", "shares": None, "market_value": None}]
    sb = _SB(rows={("watchlist_items", "select"): rows,
                   ("portfolios", "insert"): [{"id": "new"}]})

    pf._seed_default_portfolio(sb, "u1")

    _t, payload, _k = sb.upserts[0]
    assert [(r["portfolio_id"], r["ticker"], r["position"], r["shares"], r["market_value"])
            for r in payload] == [
        ("new", "AAPL", 0, 1, 10.0),
        ("new", "MSFT", 1, None, None),
        ("new", "NVDA", 2, 3, 100.0),
    ]


def test_an_empty_watchlist_writes_no_items_at_all():
    """Boundary: nothing to seed → no items statement (an empty upsert is a wasted
    round trip and, on some PostgREST versions, a 400)."""
    sb = _SB(rows={("watchlist_items", "select"): [],
                   ("portfolios", "insert"): [{"id": "new"}]})

    pf._seed_default_portfolio(sb, "u1")

    assert sb.count("portfolios", "insert") == 1
    assert sb.upserts == [] and sb.count("portfolio_items", "insert") == 0


# ── end-to-end: the route must not 500 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_list_portfolios_does_not_500_on_a_seed_race(monkeypatch):
    """The user-visible symptom: a read-only endpoint returning 500.

    `list_portfolios` re-fetches after seeding, so adopting the winner also means
    the caller still gets the group back rather than an empty list.
    """
    sb = _SB(
        rows={
            ("watchlist_items", "select"): _WATCHLIST,
            ("portfolios", "select"): [{"id": "winner"}],
        },
        insert_errors={"portfolios": _Violation()},
    )

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    winner = pf.PortfolioResponse(
        id="winner", name="Holdings", sort_order=0, items=[],
        created_at=now, updated_at=now, is_active=True,
    )
    fetches = {"n": 0}

    def _fetch(_sb, _uid):
        fetches["n"] += 1
        return [] if fetches["n"] == 1 else [winner]   # empty, then the winner's row

    monkeypatch.setattr(pf, "_fetch_user_portfolios", _fetch)

    result = await pf.list_portfolios(user={"id": "u1"}, supabase=sb)

    assert [p.id for p in result.portfolios] == ["winner"]
    assert fetches["n"] == 2, "the route must re-fetch after seeding"
