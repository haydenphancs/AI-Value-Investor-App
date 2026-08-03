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
async def test_every_ticker_already_owned_claims_zero_and_leaves_no_orphans():
    """The re-install case: the account already holds everything this install collected.

    Nothing is movable, but the guest rows must still be reaped — left behind they sit on a
    bucket nothing reads, and the NEXT account created on this install would inherit them.
    """
    bucket = guest_user_id_for("install-A")
    store = {"watchlist_items": [
        {"id": 1, "ticker": "AAPL", "user_id": bucket},
        {"id": 2, "ticker": "NVDA", "user_id": bucket},
        {"id": 8, "ticker": "AAPL", "user_id": _USER["id"]},
        {"id": 9, "ticker": "NVDA", "user_id": _USER["id"]},
    ], "portfolios": []}
    sb = _SB(store)

    res = await _claim(sb, "install-A")

    assert res["claimed"]["watchlist_items"] == 0
    assert not [r for r in store["watchlist_items"] if r["user_id"] == bucket], \
        "guest rows left orphaned on the install bucket"
    assert len(store["watchlist_items"]) == 2


@pytest.mark.asyncio
async def test_a_failure_PART_WAY_THROUGH_still_returns_rather_than_raises():
    """`table()` failing outright is already covered. The nastier shape is the write itself
    failing after the reads succeeded — that path runs inside asyncio.to_thread, so an
    unhandled raise there surfaces as a 500 on an ALREADY-authenticated user."""
    bucket = guest_user_id_for("install-A")

    class _FailingUpdate(_SB):
        def table(self, name):
            q = super().table(name)
            real_update = q.update

            def _update(payload):
                real_update(payload)
                raise RuntimeError("write conflict")

            q.update = _update
            return q

    store = {"watchlist_items": [{"id": 1, "ticker": "AAPL", "user_id": bucket}],
             "portfolios": []}
    res = await _claim(_FailingUpdate(store), "install-A")

    assert "error" in res, "a mid-claim write failure must be reported, not raised"
    assert res["claimed"] == {
        "watchlist_items": 0, "portfolios": 0, "learn_progress": 0,
        "research_reports": 0, "chat_sessions": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_id", ["install-A", None])
async def test_response_shape_matches_the_ios_decoder(guest_id):
    """Pins the contract `ClaimGuestDataResult` decodes (Models/AccountSettingsModels.swift).

    `claimed` and its integer members must ALWAYS be present — the Swift DTO declares
    `watchlist_items`/`portfolios` non-optional, so an omitted key is a decode throw on the
    sign-in path. `learn_progress` was added later; Swift ignores unknown keys, so adding a
    COUNT is safe, but dropping either original one is not.
    """
    store = {"watchlist_items": [], "portfolios": [], "user_learn_progress": [], "chat_sessions": []}
    res = await _claim(_SB(store), guest_id)

    assert set(res) <= {"claimed", "skipped", "error"}, "unexpected top-level key for the iOS decoder"
    assert "claimed" in res
    assert {"watchlist_items", "portfolios"} <= set(res["claimed"]), \
        "the iOS DTO declares these non-optional — never drop one"
    assert set(res["claimed"]) == {
        "watchlist_items", "portfolios", "learn_progress", "research_reports", "chat_sessions",
    }
    assert all(isinstance(v, int) for v in res["claimed"].values())


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


# --- Learn progress (completions + book bookmarks) -----------------------------------------
#
# Learn is partitioned per install by `get_learn_identity`, exactly like the watchlist, so a
# guest who finished lessons/articles/cores and saved books used to lose ALL of it on sign-in:
# `_claim` moved watchlist_items and portfolios only. One table backs all four content types
# (book_core / journey_lesson / money_move / book_bookmark) under
# UNIQUE(user_id, content_type, item_key), so a key the account already holds must be SKIPPED —
# re-pointing it would violate that constraint.


def _lp(row_id, user_id, content_type, item_key):
    return {"id": row_id, "user_id": user_id, "content_type": content_type, "item_key": item_key}


@pytest.mark.asyncio
async def test_claims_learn_progress_for_every_content_type():
    """All four content types ride along — bookmarks included, since they share the table."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_learn_progress": [
            _lp(1, bucket, "book_core", "3-2"),
            _lp(2, bucket, "journey_lesson", "Compounding Basics"),
            _lp(3, bucket, "money_move", "the-future-of-digital-finance"),
            _lp(4, bucket, "book_bookmark", "The Intelligent Investor"),
        ],
    }

    res = await _claim(_SB(store), "install-A")

    assert res["claimed"]["learn_progress"] == 4
    assert {r["user_id"] for r in store["user_learn_progress"]} == {_USER["id"]}
    assert {r["content_type"] for r in store["user_learn_progress"]} == {
        "book_core", "journey_lesson", "money_move", "book_bookmark"
    }


@pytest.mark.asyncio
async def test_learn_keys_the_account_already_has_are_skipped_not_collided():
    """UNIQUE(user_id, content_type, item_key): re-pointing a duplicate would RAISE. It is
    dropped instead, and the account's own row (with its own completed_at) is the keeper."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_learn_progress": [
            _lp(1, bucket, "money_move", "already-read"),      # account has this one
            _lp(2, bucket, "money_move", "brand-new"),         # only the guest has this
            _lp(9, _USER["id"], "money_move", "already-read"),  # the account's existing row
        ],
    }

    res = await _claim(_SB(store), "install-A")

    assert res["claimed"]["learn_progress"] == 1, "only the non-duplicate moves"
    keys = sorted((r["content_type"], r["item_key"]) for r in store["user_learn_progress"])
    assert keys == [("money_move", "already-read"), ("money_move", "brand-new")], \
        "the duplicate must be deleted, not left stranded on a bucket nothing reads"
    assert all(r["user_id"] == _USER["id"] for r in store["user_learn_progress"])
    assert [r for r in store["user_learn_progress"] if r["item_key"] == "already-read"][0]["id"] == 9, \
        "the account's own row is the keeper"


@pytest.mark.asyncio
async def test_same_item_key_under_a_DIFFERENT_content_type_still_moves():
    """The dedupe key is the PAIR. A book titled the same as a money-move slug (or a title that
    is both bookmarked and completed) must not shadow each other."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_learn_progress": [
            _lp(1, bucket, "book_bookmark", "Deep Value"),
            _lp(9, _USER["id"], "money_move", "Deep Value"),   # same key, other type
        ],
    }

    res = await _claim(_SB(store), "install-A")

    assert res["claimed"]["learn_progress"] == 1
    assert len(store["user_learn_progress"]) == 2


@pytest.mark.asyncio
async def test_learn_claim_is_idempotent():
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_learn_progress": [_lp(1, bucket, "book_core", "1-1")],
    }
    sb = _SB(store)

    first = await _claim(sb, "install-A")
    second = await _claim(sb, "install-A")

    assert first["claimed"]["learn_progress"] == 1
    assert second["claimed"]["learn_progress"] == 0, "nothing left to move"
    assert len(store["user_learn_progress"]) == 1, "and nothing duplicated"


@pytest.mark.asyncio
async def test_learn_claim_refuses_the_shared_legacy_bucket():
    """Same leak the watchlist guard exists for: GUEST_USER_ID holds EVERY pre-migration
    guest's Learn rows, so claiming it would pull strangers' completions into this account."""
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_learn_progress": [_lp(1, GUEST_USER_ID, "money_move", "someone-elses-article")],
    }

    res = await _claim(_SB(store), None)

    assert res["claimed"]["learn_progress"] == 0
    assert store["user_learn_progress"][0]["user_id"] == GUEST_USER_ID, "stole a shared row"


@pytest.mark.asyncio
async def test_learn_claim_does_not_touch_another_installs_rows():
    bucket_a = guest_user_id_for("install-A")
    bucket_b = guest_user_id_for("install-B")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_learn_progress": [
            _lp(1, bucket_a, "money_move", "mine"),
            _lp(2, bucket_b, "money_move", "theirs"),
        ],
    }

    await _claim(_SB(store), "install-A")

    other = [r for r in store["user_learn_progress"] if r["id"] == 2][0]
    assert other["user_id"] == bucket_b, "claimed another install's Learn progress"


@pytest.mark.asyncio
async def test_no_guest_learn_rows_is_a_clean_zero():
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [{"id": 1, "ticker": "AAPL", "user_id": bucket}],
        "portfolios": [],
        "user_learn_progress": [],
    }

    res = await _claim(_SB(store), "install-A")

    assert res["claimed"] == {
        "watchlist_items": 1, "portfolios": 0, "learn_progress": 0,
        "research_reports": 0, "chat_sessions": 0,
    }
