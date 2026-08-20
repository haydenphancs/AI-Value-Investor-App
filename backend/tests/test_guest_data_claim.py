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
        "watchlist_items": 0, "portfolios": 0, "portfolios_merged": 0,
        "learn_progress": 0, "research_reports": 0, "chat_sessions": 0,
        "investor_profile": 0,
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

    # `failed` joins the optional extras: it accompanies `error` on a partial claim and names
    # which table steps did not run. Swift ignores unknown keys, so this is additive.
    assert set(res) <= {"claimed", "skipped", "error", "failed"}, \
        "unexpected top-level key for the iOS decoder"
    assert "claimed" in res
    assert {"watchlist_items", "portfolios"} <= set(res["claimed"]), \
        "the iOS DTO declares these non-optional — never drop one"
    assert set(res["claimed"]) == {
        "watchlist_items", "portfolios", "portfolios_merged",
        "learn_progress", "research_reports", "chat_sessions",
        "investor_profile",
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
        "watchlist_items": 1, "portfolios": 0, "portfolios_merged": 0,
        "learn_progress": 0, "research_reports": 0, "chat_sessions": 0,
        "investor_profile": 0,
    }


# ── The portfolio name collision ─────────────────────────────────────────────
#
# `test_portfolios_move_too` above passes VACUOUSLY: `_SB` models no constraints, so it
# cannot see that `portfolios_user_id_name_key UNIQUE (user_id, name)` exists. The real
# table has it, and `_seed_default_portfolio` inserts a portfolio literally named
# "Holdings" for EVERY identity — the guest bucket included. So the unconditional
# `UPDATE ... SET user_id` raised 23505 for a large class of users, and because all five
# steps shared one `try`, the three steps AFTER portfolios never ran: Learn progress,
# research reports and chat sessions were silently left in the guest partition while the
# endpoint answered 200.
#
# These tests use a fake that DOES model the constraints, so they fail against the old code.


class _ConstraintViolation(Exception):
    """Shaped like postgrest's unique-violation APIError."""

    def __init__(self, constraint: str):
        super().__init__(
            f'duplicate key value violates unique constraint "{constraint}"'
        )
        self.code = "23505"


class _ConstrainedQ(_Q):
    """`_Q` plus the UNIQUE constraints the live schema actually declares.

    Only UPDATE is checked: it is the only op in `claim_guest_data` that can move a row
    into a colliding key.
    """

    _UNIQUE = {
        "portfolios": ("user_id", "name"),
        "portfolio_items": ("portfolio_id", "ticker"),
        "watchlist_items": ("user_id", "ticker"),
        "user_learn_progress": ("user_id", "content_type", "item_key"),
    }

    def execute(self):
        cols = self._UNIQUE.get(self.table)
        if self._op == "update" and cols:
            rows = self.store.setdefault(self.table, [])
            kind, col, val = self._filter
            match = (
                (lambda r: r.get(col) == val) if kind == "eq"
                else (lambda r: r.get(col) in val)
            )
            hit = [r for r in rows if match(r)]
            seen = {tuple(r.get(c) for c in cols) for r in rows if not match(r)}
            for r in hit:
                key = tuple({**r, **self._payload}.get(c) for c in cols)
                if key in seen:
                    raise _ConstraintViolation(
                        f"{self.table}_{'_'.join(cols)}_key"
                    )
                seen.add(key)
        return super().execute()


class _ConstrainedSB(_SB):
    def table(self, name):
        return _ConstrainedQ(self.store, self.log, name)


def _collision_store(bucket):
    """A guest and an account that BOTH have the default "Holdings" portfolio."""
    return {
        "watchlist_items": [],
        "portfolios": [
            {"id": "g1", "user_id": bucket, "name": "Holdings"},
            {"id": "a1", "user_id": _USER["id"], "name": "Holdings"},
        ],
        "portfolio_items": [
            {"id": "gi1", "portfolio_id": "g1", "ticker": "NVDA"},
            {"id": "gi2", "portfolio_id": "g1", "ticker": "AAPL"},
            {"id": "ai1", "portfolio_id": "a1", "ticker": "AAPL"},
        ],
        "user_learn_progress": [
            {"id": 1, "user_id": bucket, "content_type": "money_move", "item_key": "m1"},
        ],
        "research_reports": [{"id": "r1", "user_id": bucket}],
        "chat_sessions": [{"id": "c1", "user_id": bucket}],
    }


@pytest.mark.asyncio
async def test_the_constrained_fake_really_models_the_unique_index():
    """NON-VACUITY. If this passes without raising, every test below is meaningless."""
    bucket = guest_user_id_for("install-A")
    sb = _ConstrainedSB(_collision_store(bucket))

    # Exactly what the OLD code did: move every guest portfolio unconditionally.
    with pytest.raises(_ConstraintViolation):
        sb.table("portfolios").update({"user_id": _USER["id"]}).in_("id", ["g1"]).execute()


@pytest.mark.asyncio
async def test_a_portfolio_name_collision_does_not_abort_the_later_steps():
    """THE regression. Learn / reports / chats sit AFTER portfolios in the claim order."""
    bucket = guest_user_id_for("install-A")
    store = _collision_store(bucket)

    res = await _claim(_ConstrainedSB(store), "install-A")

    assert res["claimed"]["learn_progress"] == 1, "collision ate the Learn step"
    assert res["claimed"]["research_reports"] == 1, "collision ate the reports step"
    assert res["claimed"]["chat_sessions"] == 1, "collision ate the chat step"
    assert "error" not in res, "a handled collision must not report a failure"


@pytest.mark.asyncio
async def test_guest_holdings_are_merged_into_the_accounts_same_named_portfolio():
    bucket = guest_user_id_for("install-A")
    store = _collision_store(bucket)

    res = await _claim(_ConstrainedSB(store), "install-A")

    # The guest portfolio is gone; the account's survives.
    assert [p["id"] for p in store["portfolios"]] == ["a1"]
    # Its unique ticker came across; the duplicate kept the ACCOUNT's row and the guest
    # copy was dropped rather than stranded.
    by_ticker = {i["ticker"]: i for i in store["portfolio_items"]}
    assert by_ticker["NVDA"]["portfolio_id"] == "a1", "guest holding was not carried over"
    assert by_ticker["AAPL"]["id"] == "ai1", "duplicate ticker should keep the account's row"
    assert len(store["portfolio_items"]) == 2, "a duplicate item was stranded"
    # Counted as a merge, not a move: the row was deleted, not re-pointed.
    assert res["claimed"]["portfolios"] == 0
    assert res["claimed"]["portfolios_merged"] == 1


@pytest.mark.asyncio
async def test_a_non_colliding_portfolio_still_moves_wholesale():
    bucket = guest_user_id_for("install-A")
    store = _collision_store(bucket)
    store["portfolios"].append({"id": "g2", "user_id": bucket, "name": "Watchlist Ideas"})

    res = await _claim(_ConstrainedSB(store), "install-A")

    moved = [p for p in store["portfolios"] if p["id"] == "g2"][0]
    assert moved["user_id"] == _USER["id"]
    assert res["claimed"]["portfolios"] == 1
    assert res["claimed"]["portfolios_merged"] == 1


# ── The CONCURRENT-seed race (Race B) ────────────────────────────────────────
#
# The collision tests above cover the DETERMINISTIC case: the account already owns
# "Holdings" when the claim starts, so the name is in `owned_pf` and the row takes the
# merge path. They cannot see the race that actually kept the Sentry issue alive:
# `_claim_portfolios` reads the account's names, and only THEN issues the UPDATE.
# `_seed_default_portfolio` can insert "Holdings" inside that window — iOS fires
# GET /portfolios from `TrackingViewModel.init` while `AppState.onAuthenticated`
# fires the claim — so `movable` is stale by the time it is used and the UPDATE
# raises 23505 under POST /users/me/claim-guest-data.
#
# The fake below injects exactly that interleaving.


class _SeedRacingSB(_ConstrainedSB):
    """Seeds a colliding account row DURING the UPDATE — i.e. inside the real window.

    The seed has to land after the owned-names read and before the write, otherwise
    the read simply sees it and takes the merge path with no race at all. Injecting
    at execute-time reproduces the true interleaving: `movable` was computed from a
    now-stale view, so the UPDATE raises 23505 on portfolios_user_id_name_key.
    """

    def __init__(self, store, *, seed_before=1):
        super().__init__(store)
        self.remaining = seed_before
        self.update_attempts = 0

    def table(self, name):
        q = super().table(name)
        if name != "portfolios":
            return q
        outer = self

        class _Racing(type(q)):  # noqa: N801 — local shim
            def execute(self):
                if self._op == "update":
                    outer.update_attempts += 1
                    if outer.remaining > 0:
                        # The concurrent GET /portfolios → _seed_default_portfolio
                        # commits right here, between our read and our write.
                        outer.remaining -= 1
                        outer.store.setdefault("portfolios", []).append(
                            {"id": f"seed{outer.remaining}",
                             "user_id": _USER["id"], "name": "Holdings"}
                        )
                return super().execute()

        q.__class__ = _Racing
        return q


class _AlwaysCollidesSB(_ConstrainedSB):
    """Every portfolio UPDATE raises 23505, and a re-read cannot resolve it.

    Faithful to a real shape: `idx_portfolios_one_active_per_user` (migration 126) is
    a partial unique index on `user_id WHERE is_active`, so it is a *different*
    constraint from the one the name re-read reasons about. If such a collision were
    retried without a bound, the claim would spin.
    """

    def __init__(self, store):
        super().__init__(store)
        self.update_attempts = 0

    def table(self, name):
        q = super().table(name)
        if name != "portfolios":
            return q
        outer = self

        class _Colliding(type(q)):  # noqa: N801
            def execute(self):
                if self._op == "update":
                    outer.update_attempts += 1
                    raise _ConstraintViolation("idx_portfolios_one_active_per_user")
                return super().execute()

        q.__class__ = _Colliding
        return q


@pytest.mark.asyncio
async def test_claim_portfolios_survives_a_concurrent_seed():
    """THE race regression. Fails against a single-shot read-then-update.

    The final clause is the point: a collision here must not eat the three steps
    queued behind portfolios, which is the failure the whole endpoint was rewritten
    for once already.
    """
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [],
        # Account owns NOTHING yet — so the first read says "Holdings" is movable,
        # and the seed lands before the UPDATE.
        "portfolios": [{"id": "g1", "user_id": bucket, "name": "Holdings"}],
        "portfolio_items": [{"id": "gi1", "portfolio_id": "g1", "ticker": "NVDA"}],
        "user_learn_progress": [
            {"id": 1, "user_id": bucket, "content_type": "money_move", "item_key": "m1"},
        ],
        "research_reports": [{"id": "r1", "user_id": bucket}],
        "chat_sessions": [{"id": "c1", "user_id": bucket}],
    }

    res = await _claim(_SeedRacingSB(store, seed_before=1), "install-A")

    assert "error" not in res, f"the race was not handled: {res.get('error')}"
    assert res["claimed"]["learn_progress"] == 1, "the race ate the Learn step"
    assert res["claimed"]["research_reports"] == 1, "the race ate the reports step"
    assert res["claimed"]["chat_sessions"] == 1, "the race ate the chat step"
    # The guest row took the MERGE path on the retry, once the re-read saw the seed.
    assert res["claimed"]["portfolios_merged"] == 1


@pytest.mark.asyncio
async def test_the_racing_fake_really_reproduces_the_23505():
    """NON-VACUITY for the race tests: without the retry, this shape DOES raise.

    Guards against the fake quietly becoming a no-op (the exact way the pre-existing
    `test_portfolios_move_too` was vacuous).
    """
    bucket = guest_user_id_for("install-A")
    store = {"portfolios": [{"id": "g1", "user_id": bucket, "name": "Holdings"}]}
    sb = _SeedRacingSB(store, seed_before=1)

    with pytest.raises(_ConstraintViolation):
        sb.table("portfolios").update(
            {"user_id": _USER["id"], "is_active": False}
        ).in_("id", ["g1"]).execute()


@pytest.mark.asyncio
async def test_claim_portfolios_gives_up_bounded_on_an_unresolvable_collision():
    """A collision a re-read CANNOT resolve must terminate, and stay isolated.

    The exact attempt count is asserted so the retry can never quietly become
    unbounded.
    """
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [],
        "portfolios": [{"id": "g1", "user_id": bucket, "name": "Holdings"}],
        "portfolio_items": [],
        "user_learn_progress": [],
        "research_reports": [{"id": "r1", "user_id": bucket}],
        "chat_sessions": [{"id": "c1", "user_id": bucket}],
    }
    sb = _AlwaysCollidesSB(store)

    res = await _claim(sb, "install-A")

    assert sb.update_attempts == users_ep._CLAIM_PORTFOLIO_ATTEMPTS
    assert res.get("error") == "PartialClaim"
    assert res.get("failed") == ["portfolios"]
    # Still isolated: the steps behind portfolios ran.
    assert res["claimed"]["research_reports"] == 1
    assert res["claimed"]["chat_sessions"] == 1


@pytest.mark.asyncio
async def test_a_non_unique_error_on_the_move_is_not_retried():
    """Negative control — only a 23505 means "someone else won the race"."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [],
        "portfolios": [{"id": "g1", "user_id": bucket, "name": "Holdings"}],
        "portfolio_items": [], "user_learn_progress": [],
        "research_reports": [], "chat_sessions": [],
    }
    attempts = {"n": 0}

    class _BoomSB(_ConstrainedSB):
        def table(self, name):
            q = super().table(name)
            if name != "portfolios":
                return q

            class _Boom(type(q)):  # noqa: N801
                def execute(self):
                    if self._op == "update":
                        attempts["n"] += 1
                        raise RuntimeError("relation is being vacuumed")
                    return super().execute()

            q.__class__ = _Boom
            return q

    res = await _claim(_BoomSB(store), "install-A")

    assert attempts["n"] == 1, "a non-unique error must not be retried"
    assert res.get("failed") == ["portfolios"]


@pytest.mark.asyncio
async def test_one_broken_table_does_not_abort_the_others():
    """Step isolation, independent of the portfolio case."""
    bucket = guest_user_id_for("install-A")

    class _BreakLearn(_ConstrainedSB):
        def table(self, name):
            if name == "user_learn_progress":
                raise RuntimeError("relation is being vacuumed")
            return super().table(name)

    store = _collision_store(bucket)
    res = await _claim(_BreakLearn(store), "install-A")

    assert res["claimed"]["research_reports"] == 1, "a broken table aborted a later one"
    assert res["claimed"]["chat_sessions"] == 1
    assert res.get("error") == "PartialClaim"
    assert res.get("failed") == ["user_learn_progress"]


# ── Investor profile (migration 131) ─────────────────────────────────────────
#
# The profile is captured during FIRST-RUN onboarding, before an account exists, so
# without this step answering the questions and THEN signing up throws the answers
# away — the exact "signing in costs you data" failure this endpoint exists to prevent.
#
# `user_id` is the PRIMARY KEY here, so unlike the watchlist there is at most one row
# per side and the two cannot be merged: re-pointing onto an id that already has a row
# would raise a unique violation.

@pytest.mark.asyncio
async def test_investor_profile_moves_to_the_account():
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [
            {"user_id": bucket, "experience_level": "new", "topics": ["dividends"]},
        ],
    }
    sb = _SB(store)

    res = await _claim(sb, "install-A")

    assert res["claimed"]["investor_profile"] == 1
    rows = store["user_investor_profile"]
    assert len(rows) == 1 and rows[0]["user_id"] == _USER["id"]
    # The answers themselves must survive the move, not just the row.
    assert rows[0]["experience_level"] == "new" and rows[0]["topics"] == ["dividends"]


@pytest.mark.asyncio
async def test_the_accounts_own_profile_wins_and_the_guest_row_is_dropped():
    """The account's profile is the more deliberate artifact — edited in Settings after
    signup — so a first-run guess must not overwrite it. The guest row is DELETED rather
    than stranded on a bucket nothing reads (same disposal as the watchlist duplicates)."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [
            {"user_id": bucket, "experience_level": "new"},
            {"user_id": _USER["id"], "experience_level": "experienced"},
        ],
    }
    sb = _SB(store)

    res = await _claim(sb, "install-A")

    assert res["claimed"]["investor_profile"] == 0
    rows = store["user_investor_profile"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == _USER["id"]
    assert rows[0]["experience_level"] == "experienced", "the account's answer was overwritten"


@pytest.mark.asyncio
async def test_no_guest_profile_is_a_clean_zero():
    """A guest who skipped every preference question has no row at all."""
    store = {"watchlist_items": [], "portfolios": [], "user_investor_profile": []}
    res = await _claim(_SB(store), "install-A")
    assert res["claimed"]["investor_profile"] == 0
    assert "failed" not in res, "an absent profile is not a failure"


@pytest.mark.asyncio
async def test_claiming_the_profile_twice_is_idempotent():
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [{"user_id": bucket, "experience_level": "new"}],
    }
    sb = _SB(store)

    first = await _claim(sb, "install-A")
    second = await _claim(sb, "install-A")

    assert first["claimed"]["investor_profile"] == 1
    assert second["claimed"]["investor_profile"] == 0, "second pass must find nothing"
    assert len(store["user_investor_profile"]) == 1


def test_the_success_log_reports_every_counter():
    """Every key in `claimed` must appear in the success log's args.

    This regression has now happened twice: the comment above the log line records that
    "a claim that moved only reports used to log nothing at all", and the investor-profile
    counter was then added to `claimed` without being added to the log — so a claim that
    moved ONLY a profile logged all-zeros and never mentioned it. Derived from the source
    rather than a hand-copied list, so the next counter cannot be forgotten either.

    Parses the FILE rather than `inspect.getsource`: that goes through linecache and the
    function's recorded first line, which desynchronises the moment the file is edited
    under a running interpreter — during mutation-testing it reported a bogus failure on
    the scan assertion instead of the real one.
    """
    import ast
    from pathlib import Path

    path = Path(users_ep.__file__)
    tree = ast.parse(path.read_text())
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "claim_guest_data"),
        None,
    )
    assert func is not None, "claim_guest_data not found — the scan would pass vacuously"

    # The keys `claimed` is seeded with (the largest literal listing them).
    seeded: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Dict) and node.keys:
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "watchlist_items" in keys and len(keys) > len(seeded):
                seeded = keys
    assert "investor_profile" in seeded, "scan failed to find the claimed dict"

    # Every `claimed["x"]` referenced in a logger.* call.
    logged: set[str] = set()
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"info", "warning", "error"}):
            for arg in node.args:
                for sub in ast.walk(arg):
                    if (isinstance(sub, ast.Subscript)
                            and isinstance(sub.value, ast.Name) and sub.value.id == "claimed"
                            and isinstance(sub.slice, ast.Constant)):
                        logged.add(sub.slice.value)
    assert logged, "found no claimed[...] references in any log call"

    missing = seeded - logged
    assert not missing, (
        f"counter(s) tracked in `claimed` but absent from the success log: {sorted(missing)} — "
        f"a claim that moved only those would log all-zeros"
    )


# ── The profile claim MERGES; it used to destroy ─────────────────────────────
#
# The tie-break was "does the account have a row", not "does the account row contain
# anything". A phantom row is trivially created (an empty PUT, or a consent-only write on
# a user with no row), and the costly case is the `.restoring` window: the client keeps
# sending X-Guest-Id with the token disarmed, so a CONSENT GRANT lands on the guest bucket
# and answers 200 — then the session heals and the claim deleted that grant. Result:
# `investor_profile: 0`, no error, no log, and a toggle that silently reads Off again.
#
# Every sibling step here merges. These pin that this one does too.

_CONSENT_EARLY = "2026-08-01T00:00:00+00:00"
_CONSENT_LATE = "2026-08-13T00:00:00+00:00"


def _defaults(user_id, **over):
    row = {
        "user_id": user_id,
        "experience_level": "learning", "explanation_style": "balanced",
        "answer_depth": "brief",
        "topics": [], "learning_goals": [], "follow_signals": [],
        "consented_at": None,
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_a_phantom_account_row_does_not_destroy_the_guests_answers():
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [
            _defaults(bucket, experience_level="new", topics=["dividends"]),
            _defaults(_USER["id"]),          # phantom: exists, states nothing
        ],
    }
    res = await _claim(_SB(store), "install-A")

    assert res["claimed"]["investor_profile"] == 1
    rows = [r for r in store["user_investor_profile"] if r["user_id"] == _USER["id"]]
    assert len(rows) == 1
    assert rows[0]["topics"] == ["dividends"], "the guest's answers were thrown away"
    assert rows[0]["experience_level"] == "new"


@pytest.mark.asyncio
async def test_a_guest_consent_grant_survives_the_claim():
    """THE regression. A grant written during `.restoring` lands on the guest bucket."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [
            _defaults(bucket, topics=["energy"], consented_at=_CONSENT_LATE),
            _defaults(_USER["id"], topics=["value"]),   # account stated topics, never consented
        ],
    }
    await _claim(_SB(store), "install-A")

    row = [r for r in store["user_investor_profile"] if r["user_id"] == _USER["id"]][0]
    assert row["consented_at"] == _CONSENT_LATE, "the consent grant was destroyed by the claim"
    assert row["topics"] == ["value"], "the account's own topics must still win"


@pytest.mark.asyncio
async def test_the_earlier_consent_timestamp_wins():
    """That is when the reader actually accepted."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [
            _defaults(bucket, consented_at=_CONSENT_EARLY, topics=["energy"]),
            _defaults(_USER["id"], consented_at=_CONSENT_LATE, topics=["value"]),
        ],
    }
    await _claim(_SB(store), "install-A")
    row = [r for r in store["user_investor_profile"] if r["user_id"] == _USER["id"]][0]
    assert row["consented_at"] == _CONSENT_EARLY


@pytest.mark.asyncio
async def test_the_guest_bucket_never_lingers_after_a_merge():
    """A row left on the per-install bucket is unclaimable and unreachable forever."""
    bucket = guest_user_id_for("install-A")
    store = {
        "watchlist_items": [], "portfolios": [],
        "user_investor_profile": [
            _defaults(bucket, topics=["energy"]),
            _defaults(_USER["id"], topics=["value"]),
        ],
    }
    await _claim(_SB(store), "install-A")
    assert not [r for r in store["user_investor_profile"] if r["user_id"] == bucket]


# ── The launch-time short circuit (migration 144) ──────────────────────────────
#
# This endpoint runs on EVERY cold launch of EVERY signed-in user — the client's transition
# key is process-scoped by design (AppState documents why a persisted latch would be wrong) —
# and its steady-state answer is "nothing to claim". Reaching that answer used to cost six
# sequential PostgREST round trips, all returning zero rows.


class _ProbeSB(_SB):
    """`_SB` plus the `guest_bucket_has_data` RPC from migration 144."""

    def __init__(self, store, answer):
        super().__init__(store)
        self.answer = answer
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        answer = self.answer
        outer = self

        class _R:
            def execute(self_inner):
                if isinstance(answer, Exception):
                    raise answer
                return type("R", (), {"data": answer})()

        return _R()


@pytest.mark.asyncio
async def test_an_empty_bucket_costs_one_round_trip_not_six():
    """The whole point of the probe. With nothing to claim, not a single table is touched."""
    bucket = guest_user_id_for("install-empty")
    sb = _ProbeSB({"watchlist_items": [], "portfolios": []}, answer=False)

    result = await _claim(sb, "install-empty")

    assert sb.rpc_calls == [("guest_bucket_has_data", {"p_bucket": bucket})]
    assert sb.log == [], (
        f"the six-step scan ran despite an empty bucket: {sb.log}"
    )
    assert result["claimed"] == {
        "watchlist_items": 0, "portfolios": 0, "portfolios_merged": 0,
        "learn_progress": 0, "research_reports": 0, "chat_sessions": 0,
        "investor_profile": 0,
    }


@pytest.mark.asyncio
async def test_a_non_empty_bucket_still_runs_the_full_claim():
    """The probe must never become a way to LOSE data — a true answer runs every step."""
    bucket = guest_user_id_for("install-B")
    store = {"watchlist_items": [{"id": 1, "ticker": "AAPL", "user_id": bucket}], "portfolios": []}
    sb = _ProbeSB(store, answer=True)

    result = await _claim(sb, "install-B")

    assert result["claimed"]["watchlist_items"] == 1
    assert store["watchlist_items"][0]["user_id"] == _USER["id"]


@pytest.mark.asyncio
async def test_the_probe_fails_open_when_the_function_is_missing():
    """Deploy-order safety. The backend may ship before migration 144 is applied by hand, and
    a 404 from the RPC must fall back to the six-step scan — never silently claim nothing."""
    bucket = guest_user_id_for("install-C")
    store = {"watchlist_items": [{"id": 9, "ticker": "MSFT", "user_id": bucket}], "portfolios": []}
    sb = _ProbeSB(store, answer=RuntimeError("function public.guest_bucket_has_data does not exist"))

    result = await _claim(sb, "install-C")

    assert result["claimed"]["watchlist_items"] == 1, (
        "a missing RPC swallowed the claim instead of falling back — this would silently lose "
        "every guest's data between deploy and migration"
    )
    assert store["watchlist_items"][0]["user_id"] == _USER["id"]


@pytest.mark.asyncio
async def test_an_unexpected_probe_answer_fails_open():
    """Only an explicit `False` short-circuits. A None/absent payload means we could not tell,
    and 'could not tell' must run the scan."""
    bucket = guest_user_id_for("install-D")
    store = {"watchlist_items": [{"id": 3, "ticker": "TSLA", "user_id": bucket}], "portfolios": []}
    sb = _ProbeSB(store, answer=None)

    result = await _claim(sb, "install-D")

    assert result["claimed"]["watchlist_items"] == 1
