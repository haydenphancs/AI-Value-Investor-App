"""The whale FOLLOW LIMIT on the WRITE path — the paid cap nothing was pinning.

WHY THIS FILE EXISTS
--------------------
Entitlements say a Free account may track exactly ONE whale (the designated free whale),
Pro ten, Max unlimited. `tests/test_whale_entitlement.py` is 450 lines and its docstring
says it covers tracking — but what it exercises is `_apply_follow_locks`, the DISPLAY lock
that greys out a Follow button, plus the pure `whale_follow_limit` table. The thing that
actually refuses a write, `WhaleService._assert_may_follow`, appeared in ZERO test files —
as did `toggle_follow`, `_compensate_if_over_limit`, `_is_following` and
`WhaleFollowLockedException`.

Found by mutation: rewriting `_assert_may_follow` to a bare `return False` ("allowed, not
already following") hands every Free account unlimited follows of any whale, and the whole
suite stays green. A greyed-out button is not a gate; anyone who can send
`POST /whales/{id}/follow` — a replayed request, a stale client, a second tap that beat the
lock — gets the follow, and the activity feed then serves that whale's trades.

The write path has three pieces, and each has its own way of failing OPEN:

  • `_assert_may_follow`        — the gate. Free: identity of the ONE designated whale,
                                   resolved by name and failing CLOSED when unresolvable.
                                   Pro: a count against the caller's roster with an
                                   idempotent-refollow escape hatch. Unknown tier → Free.
  • `_compensate_if_over_limit` — the gate is SELECT-then-INSERT, so two taps at 9/10 both
                                   pass. This re-counts AFTER the write and rolls back THIS
                                   row only. Gutting it to `return` reopens the race.
  • `_is_following`             — the escape hatch's existence check. Must never raise,
                                   and must fall to False (= do NOT skip the check) on a
                                   fault.

Pure module: a small STATEFUL fake `sb` that applies `.eq` / `.ilike` filters and records
every write, so "nothing was written" is asserted on state, not inferred from a return
value. No network, no Supabase.
"""
from __future__ import annotations

import json
import uuid
from typing import Callable, Iterable, Optional

import pytest

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import whales as whales_endpoint
from app.schemas.whale import FollowResponse
from app.services import whale_service as wsvc
from app.services.entitlements import (
    FREE_TIER_WHALE_NAME,
    TIER_FREE,
    TIER_MAX,
    TIER_ORDER,
    TIER_PRO,
)
from app.services.whale_service import WhaleFollowLockedException, WhaleService

_USER = "u1"
_OTHER_USER = "u2"
_FREE_WHALE_ID = "11111111-1111-1111-1111-111111111111"
_OTHER_WHALE_ID = "22222222-2222-2222-2222-222222222222"
_RACING_WHALE_ID = "33333333-3333-3333-3333-333333333333"

# The designated free whale is deliberately NOT first: `free_tier_whale_id` resolves it by
# `.ilike("name", ...)`, and a fake whose ilike were a no-op would hand back row[0]. With
# the wrong whale first, a broken filter makes the Free tests fail for the RIGHT reason.
_WHALES = [
    {"id": _OTHER_WHALE_ID, "name": "Someone Else", "followers_count": 3},
    {"id": _FREE_WHALE_ID, "name": FREE_TIER_WHALE_NAME, "followers_count": 7},
]


def _pro_roster(n: int, user: str = _USER) -> list[tuple[str, str]]:
    """`n` distinct followed whales for `user`, none of them the ids the tests target."""
    return [(user, f"aaaaaaaa-0000-0000-0000-{i:012d}") for i in range(n)]


# ── The fake ─────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """One chained call. Filters are APPLIED (unlike most fakes in this tree) because the
    per-user scoping of the roster read is itself under test — a fake that ignored
    `.eq("user_id", …)` could not tell "10 follows" from "10 follows by somebody else"."""

    def __init__(self, db: "_FakeDB", table: str):
        self._db = db
        self._table = table
        self._filters: list[tuple[str, str]] = []
        self._ilike: Optional[tuple[str, str]] = None
        self._op = "select"
        self._payload = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, column, value):
        self._filters.append((column, str(value)))
        return self

    def ilike(self, column, pattern):
        self._ilike = (column, str(pattern).strip().casefold())
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, payload, **_k):
        self._op = "upsert"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        return self._db._execute(self)


class _FakeDB:
    """Two tables, held as live state.

    `follows` is a set of `(user_id, whale_id)`; `whales` a list of rows. `fail` is a set
    of `(table, op)` pairs whose `execute()` raises — the transport-fault branches.
    `swallow_upserts` models an upsert that RLS filters out (the DB answers 200, nothing
    lands), and `on_upsert` runs right after this request's row lands, which is where a
    concurrent tap's row is injected to reproduce the TOCTOU race.
    """

    def __init__(
        self,
        follows: Iterable[tuple[str, str]] = (),
        whales: Optional[list] = None,
        *,
        fail: Iterable[tuple[str, str]] = (),
        swallow_upserts: bool = False,
        on_upsert: Optional[Callable[["_FakeDB"], None]] = None,
    ):
        self.follows = {(str(u), str(w)) for u, w in follows}
        self.whales = list(_WHALES if whales is None else whales)
        self.fail = set(fail)
        self.swallow_upserts = swallow_upserts
        self.on_upsert = on_upsert
        self.upserts: list[dict] = []
        self.deletes: list[tuple[str, str]] = []

    def table(self, name):
        return _FakeQuery(self, name)

    # -- dispatch -----------------------------------------------------------------

    def _execute(self, q: _FakeQuery):
        if (q._table, q._op) in self.fail:
            raise RuntimeError(f"supabase down: {q._table}.{q._op}")
        if q._table == "whale_follows":
            return self._follows_op(q)
        if q._table == "whales":
            return self._whales_op(q)
        raise AssertionError(f"unexpected table {q._table!r}")

    @staticmethod
    def _apply(rows, q: _FakeQuery):
        out = [r for r in rows if all(str(r.get(c)) == v for c, v in q._filters)]
        if q._ilike is not None:
            col, needle = q._ilike
            out = [r for r in out if str(r.get(col, "")).strip().casefold() == needle]
        return out

    def _follows_op(self, q: _FakeQuery):
        rows = [{"user_id": u, "whale_id": w} for u, w in sorted(self.follows)]
        if q._op == "select":
            return _Result(self._apply(rows, q))
        if q._op == "delete":
            hit = self._apply(rows, q)
            for r in hit:
                self.follows.discard((r["user_id"], r["whale_id"]))
                self.deletes.append((r["user_id"], r["whale_id"]))
            return _Result(hit)
        if q._op == "upsert":
            p = q._payload
            self.upserts.append(dict(p))
            if not self.swallow_upserts:
                self.follows.add((str(p["user_id"]), str(p["whale_id"])))
            if self.on_upsert is not None:
                self.on_upsert(self)
            return _Result([p])
        raise AssertionError(f"unexpected op {q._op!r} on whale_follows")

    def _whales_op(self, q: _FakeQuery):
        assert q._op == "select", f"tests never write `whales` (got {q._op!r})"
        return _Result(self._apply(self.whales, q))


@pytest.fixture(autouse=True)
def _reset_memo():
    """`free_tier_whale_id` memoizes into a module global for the process lifetime, so
    without this the first test to resolve it decides the answer for every later one —
    and the fail-closed cases below would pass vacuously off a warm cache."""
    wsvc.reset_free_whale_cache()
    yield
    wsvc.reset_free_whale_cache()


@pytest.fixture
def svc() -> WhaleService:
    return WhaleService()


def _gate(svc, db, whale_id, tier, user=_USER):
    return svc._assert_may_follow(db, user, whale_id, tier)


# ── 0. The exception carries what the endpoint reads ────────────────────────

def test_exception_carries_the_numbers_the_endpoint_reads():
    """`follow_whale` reads `.tier_required`, `.limit` and `.reason` off the exception
    to build `details`. Renaming any of them breaks the paywall silently — the route
    would raise AttributeError and the client would see a bare 500 instead."""
    e = WhaleFollowLockedException(TIER_PRO, 10, "Follow limit reached (10/10)")
    assert (e.tier_required, e.limit, e.reason) == (TIER_PRO, 10, "Follow limit reached (10/10)")
    assert str(e) == e.reason
    # Max carries None for "unlimited"; the route maps that to -1, never to a nested value.
    assert WhaleFollowLockedException(TIER_MAX, None, "x").limit is None


# ── 1. `_assert_may_follow` — the gate itself ────────────────────────────────

def test_max_is_unlimited(svc):
    """Max never consults the roster: 50 existing follows and a 51st is fine."""
    db = _FakeDB(_pro_roster(50))
    assert _gate(svc, db, _OTHER_WHALE_ID, TIER_MAX) is False


def test_max_is_unlimited_even_when_the_roster_is_unreadable(svc):
    """Max has no count to check, so a transport fault on the roster must not be able to
    refuse it — the allowance does not depend on a read that has nothing to decide."""
    db = _FakeDB(_pro_roster(50), fail={("whale_follows", "select")})
    assert _gate(svc, db, _OTHER_WHALE_ID, TIER_MAX) is False


@pytest.mark.parametrize("existing", [0, 1, 9])
def test_pro_under_the_limit_is_allowed(svc, existing):
    db = _FakeDB(_pro_roster(existing))
    assert _gate(svc, db, _OTHER_WHALE_ID, TIER_PRO) is False


def test_pro_boundary_the_tenth_follow_is_offered_and_the_eleventh_is_not(svc):
    """`>=` at the cap: 9 followed → the 10th goes through; 10 followed → refused. The
    display lock (`_apply_follow_locks`) promises exactly this, and the write path must
    agree or the button and the server disagree about the same tap."""
    assert _gate(svc, _FakeDB(_pro_roster(9)), _OTHER_WHALE_ID, TIER_PRO) is False
    with pytest.raises(WhaleFollowLockedException) as ei:
        _gate(svc, _FakeDB(_pro_roster(10)), _OTHER_WHALE_ID, TIER_PRO)
    assert ei.value.limit == 10
    # Not pinned to a specific tier: `required_tier_for_whales("pro")` is None (Pro already
    # unlocks detail) and the code falls back to TIER_PRO, which names the caller's OWN
    # plan as the upsell. iOS only needs a real tier string here.
    assert ei.value.tier_required in TIER_ORDER


def test_pro_over_the_limit_is_refused(svc):
    """Post-downgrade state (a Max user holding 12, now on Pro) must not be able to ADD."""
    with pytest.raises(WhaleFollowLockedException):
        _gate(svc, _FakeDB(_pro_roster(12)), _OTHER_WHALE_ID, TIER_PRO)


def test_pro_refollow_at_the_cap_is_allowed_and_reported(svc):
    """The idempotent-refollow escape hatch. At 10/10, re-following one of the ten must
    NOT paywall — the upsert is a no-op, and a double-tap would otherwise show the user a
    paywall for a whale they already track. The True return is what lets `toggle_follow`
    skip the post-write count."""
    db = _FakeDB(_pro_roster(9) + [(_USER, _OTHER_WHALE_ID)])
    assert _gate(svc, db, _OTHER_WHALE_ID, TIER_PRO) is True


def test_pro_escape_hatch_needs_the_callers_own_row(svc):
    """The hatch keys on THIS user's row. Somebody else following the whale does not
    open it — at the cap, that is a refusal, not an idempotent re-follow."""
    db = _FakeDB(_pro_roster(10) + [(_OTHER_USER, _OTHER_WHALE_ID)])
    with pytest.raises(WhaleFollowLockedException):
        _gate(svc, db, _OTHER_WHALE_ID, TIER_PRO)


def test_pro_count_is_scoped_to_the_caller(svc):
    """The roster read must filter on `user_id`. Drop that `.eq` and every account shares
    one global count — ten follows anywhere in the table lock everyone out."""
    db = _FakeDB(_pro_roster(10, user=_OTHER_USER))
    assert _gate(svc, db, _OTHER_WHALE_ID, TIER_PRO) is False


def test_pro_refollow_detection_is_string_based(svc):
    """Supabase hands back the id as `str`; a caller may hold a `uuid.UUID`. The code
    stringifies both sides — pin that rather than the accident of two equal `str`s."""
    db = _FakeDB(_pro_roster(9) + [(_USER, _OTHER_WHALE_ID)])
    assert _gate(svc, db, uuid.UUID(_OTHER_WHALE_ID), TIER_PRO) is True


def test_pro_roster_read_fault_does_not_fall_open(svc):
    """A transport failure on the count must NOT resolve to "allowed". The exception
    propagates (toggle_follow logs and re-raises it) — the follow is simply not written."""
    db = _FakeDB(_pro_roster(0), fail={("whale_follows", "select")})
    with pytest.raises(RuntimeError):
        _gate(svc, db, _OTHER_WHALE_ID, TIER_PRO)


def test_free_may_follow_the_designated_whale(svc):
    """The packaging argument: one investor tracked in full, so the feature demonstrates
    itself. Not yet following → False (the post-write check is a no-op for Free anyway)."""
    assert _gate(svc, _FakeDB(), _FREE_WHALE_ID, TIER_FREE) is False


def test_free_is_refused_any_other_whale(svc):
    """🔴 The money assertion. A bare `return False` here passed the whole suite."""
    with pytest.raises(WhaleFollowLockedException) as ei:
        _gate(svc, _FakeDB(), _OTHER_WHALE_ID, TIER_FREE)
    assert ei.value.limit == 1
    assert ei.value.tier_required == TIER_PRO


def test_free_is_refused_even_with_an_empty_roster(svc):
    """Free's allowance is one SPECIFIC whale, not "one of the user's choosing". Zero
    follows does not buy a slot for an arbitrary whale — a count-based Free gate would
    be a different (weaker) product than the one entitlements.py describes."""
    with pytest.raises(WhaleFollowLockedException):
        _gate(svc, _FakeDB(follows=()), _OTHER_WHALE_ID, TIER_FREE)


def test_free_refollow_of_the_designated_whale_reports_the_existing_row(svc):
    db = _FakeDB([(_USER, _FREE_WHALE_ID)])
    assert _gate(svc, db, _FREE_WHALE_ID, TIER_FREE) is True


@pytest.mark.parametrize(
    "db",
    [
        _FakeDB(whales=[]),                              # registry sync has not run
        _FakeDB(fail={("whales", "select")}),            # transport fault on the lookup
        _FakeDB(whales=[{"id": _OTHER_WHALE_ID, "name": "Someone Else"}]),  # renamed away
    ],
    ids=["no-rows", "lookup-raises", "name-missing"],
)
def test_free_fails_closed_when_the_free_whale_is_unresolvable(svc, db):
    """`free_tier_whale_id` returns None on a cold table, a fault, or a rename. The gate
    must refuse EVERY whale then — including the id that would have been the free one —
    because `None == whale_id` can never be the comparison that grants a follow."""
    with pytest.raises(WhaleFollowLockedException):
        _gate(svc, db, _OTHER_WHALE_ID, TIER_FREE)
    with pytest.raises(WhaleFollowLockedException):
        _gate(svc, db, _FREE_WHALE_ID, TIER_FREE)


@pytest.mark.parametrize("tier", [None, "", "guest", "nonsense", "FREE_TRIAL", "max", 42])
def test_an_unrecognised_tier_falls_closed_onto_free(svc, tier):
    """Unknown must land on the LEAST privileged allowance, not merely "some refusal":
    `limit == 1` pins that it is Free's rule being applied, not Pro's. Note "max" is not
    the Max tier (that is spelled "premium"), so a plausible misspelling unlocks nothing,
    and a non-string (42) must not raise its way past the gate either."""
    with pytest.raises(WhaleFollowLockedException) as ei:
        _gate(svc, _FakeDB(), _OTHER_WHALE_ID, tier)
    assert ei.value.limit == 1


def test_an_unrecognised_tier_still_gets_the_free_whale(svc):
    """The flip side of falling closed: unknown ≡ Free, and Free may follow the one whale.
    Falling closed means "least privilege", not "no privilege"."""
    assert _gate(svc, _FakeDB(), _FREE_WHALE_ID, "nonsense") is False


def test_tier_spelling_is_normalised(svc):
    """`normalize_tier` strips and lower-cases: " PRO " is Pro (10), "Premium" is Max."""
    assert _gate(svc, _FakeDB(_pro_roster(9)), _OTHER_WHALE_ID, " PRO ") is False
    with pytest.raises(WhaleFollowLockedException):
        _gate(svc, _FakeDB(_pro_roster(10)), _OTHER_WHALE_ID, " PRO ")
    assert _gate(svc, _FakeDB(_pro_roster(50)), _OTHER_WHALE_ID, "Premium") is False


# ── 2. `_is_following` — the escape hatch's existence check ─────────────────

def test_is_following_true_when_the_row_exists():
    db = _FakeDB([(_USER, _OTHER_WHALE_ID)])
    assert WhaleService._is_following(db, _USER, _OTHER_WHALE_ID) is True


def test_is_following_false_when_absent():
    """Anti-vacuity pair with the test above: a constant True or a constant False fails
    one of the two."""
    assert WhaleService._is_following(_FakeDB(), _USER, _OTHER_WHALE_ID) is False


def test_is_following_is_scoped_to_the_caller():
    db = _FakeDB([(_OTHER_USER, _OTHER_WHALE_ID)])
    assert WhaleService._is_following(db, _USER, _OTHER_WHALE_ID) is False


def test_is_following_falls_to_false_on_a_fault():
    """Documented contract: "never raises". And the degraded answer must be False — True
    would tell `toggle_follow` to SKIP the post-write limit check on the strength of a
    read that never happened."""
    db = _FakeDB([(_USER, _OTHER_WHALE_ID)], fail={("whale_follows", "select")})
    assert WhaleService._is_following(db, _USER, _OTHER_WHALE_ID) is False


# ── 3. `_compensate_if_over_limit` — the race repair ────────────────────────

def _compensate(svc, db, tier, whale_id=_OTHER_WHALE_ID):
    return svc._compensate_if_over_limit(db, _USER, whale_id, tier)


def test_compensation_is_a_no_op_for_max(svc):
    db = _FakeDB(_pro_roster(50) + [(_USER, _OTHER_WHALE_ID)])
    _compensate(svc, db, TIER_MAX)
    assert db.deletes == []


def test_compensation_is_a_no_op_for_free(svc):
    """Free's gate is identity, not a count, and the docstring's rule is "truncate, never
    destroy": a grandfathered Free account holding three follows keeps all three."""
    db = _FakeDB([(_USER, _FREE_WHALE_ID), (_USER, _OTHER_WHALE_ID), (_USER, _RACING_WHALE_ID)])
    _compensate(svc, db, TIER_FREE, whale_id=_OTHER_WHALE_ID)
    assert db.deletes == [] and len(db.follows) == 3


def test_compensation_leaves_exactly_the_limit_alone(svc):
    """Boundary: `total <= limit` is fine. 10 rows on a 10-plan is the serial happy path
    (9 + this one), and rolling that back would refuse the 10th follow the gate offered."""
    db = _FakeDB(_pro_roster(9) + [(_USER, _OTHER_WHALE_ID)])
    _compensate(svc, db, TIER_PRO)
    assert db.deletes == [] and (_USER, _OTHER_WHALE_ID) in db.follows


def test_compensation_rolls_back_only_this_row_and_raises(svc):
    """🔴 Anti-vacuity for the repair: gut this to `return` and an 11/10 roster survives.
    The rollback must be scoped to the row THIS request wrote — deleting an older follow
    to make room would destroy state the user never asked to lose."""
    older = _pro_roster(10)
    db = _FakeDB(older + [(_USER, _OTHER_WHALE_ID)])
    with pytest.raises(WhaleFollowLockedException) as ei:
        _compensate(svc, db, TIER_PRO)
    assert ei.value.limit == 10
    assert db.deletes == [(_USER, _OTHER_WHALE_ID)]
    assert (_USER, _OTHER_WHALE_ID) not in db.follows
    assert all(row in db.follows for row in older), "an OLDER follow was destroyed"


def test_compensation_count_is_scoped_to_the_caller(svc):
    db = _FakeDB(_pro_roster(11, user=_OTHER_USER) + [(_USER, _OTHER_WHALE_ID)])
    _compensate(svc, db, TIER_PRO)
    assert db.deletes == []


def test_compensation_count_fault_leaves_the_follow_in_place(svc):
    """The documented degrade: if the post-write count cannot be read, the follow stays
    and the user is NOT shown a paywall for a write that succeeded. This is a deliberate
    choice (a failed read must not delete data); the log line is the only trace."""
    db = _FakeDB(_pro_roster(10) + [(_USER, _OTHER_WHALE_ID)], fail={("whale_follows", "select")})
    _compensate(svc, db, TIER_PRO)          # must not raise
    assert db.deletes == [] and (_USER, _OTHER_WHALE_ID) in db.follows


def test_compensation_rollback_fault_still_raises_the_paywall(svc):
    """The other degrade branch: the DELETE fails. The account is now genuinely over its
    cap (logged at error), but the CALLER must still see the refusal — swallowing it here
    would report a successful 11th follow on a 10-plan."""
    db = _FakeDB(_pro_roster(10) + [(_USER, _OTHER_WHALE_ID)], fail={("whale_follows", "delete")})
    with pytest.raises(WhaleFollowLockedException):
        _compensate(svc, db, TIER_PRO)
    assert (_USER, _OTHER_WHALE_ID) in db.follows       # rollback did not happen


# ── 4. `toggle_follow` — the entry point, end to end ────────────────────────
#
# The tests above prove each piece is correct. These prove the pieces are REACHED from the
# one method the endpoints call — a correct gate nothing calls is precisely the bug the
# sibling trade-group file was written for.

def _toggle(monkeypatch, db, whale_id, follow, tier, user=_USER):
    # `whale_service.py` binds `get_supabase` with a MODULE-LEVEL import, so the name is
    # resolved once at import time and only that module's own binding is live at call time.
    monkeypatch.setattr(wsvc, "get_supabase", lambda: db)
    return WhaleService().toggle_follow(user, whale_id, follow=follow, tier=tier)


@pytest.mark.asyncio
async def test_toggle_free_designated_whale_is_written_and_confirmed(monkeypatch):
    db = _FakeDB()
    resp = await _toggle(monkeypatch, db, _FREE_WHALE_ID, True, TIER_FREE)
    assert isinstance(resp, FollowResponse)
    assert resp.is_following is True
    assert resp.followers_count == 7                     # read back from `whales`
    assert (_USER, _FREE_WHALE_ID) in db.follows


@pytest.mark.asyncio
async def test_toggle_free_other_whale_raises_and_writes_nothing(monkeypatch):
    """The refusal must happen BEFORE the upsert. A gate that raised after writing would
    leave the row in place and merely decorate it with an error."""
    db = _FakeDB()
    with pytest.raises(WhaleFollowLockedException):
        await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_FREE)
    assert db.upserts == [] and db.follows == set()


@pytest.mark.asyncio
async def test_toggle_pro_at_the_cap_raises_and_writes_nothing(monkeypatch):
    db = _FakeDB(_pro_roster(10))
    with pytest.raises(WhaleFollowLockedException):
        await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert db.upserts == [] and len(db.follows) == 10


@pytest.mark.asyncio
async def test_toggle_pro_tenth_follow_lands(monkeypatch):
    db = _FakeDB(_pro_roster(9))
    resp = await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert resp.is_following is True
    assert (_USER, _OTHER_WHALE_ID) in db.follows and db.deletes == []


@pytest.mark.asyncio
async def test_toggle_unknown_tier_falls_closed(monkeypatch):
    db = _FakeDB()
    with pytest.raises(WhaleFollowLockedException):
        await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, "nonsense")
    assert db.upserts == []


@pytest.mark.asyncio
async def test_toggle_race_past_the_cap_is_compensated(monkeypatch):
    """The TOCTOU the code comments describe. Two taps at 9/10 both read 9 and both pass;
    here the OTHER tap's row lands right after ours (`on_upsert`), so the post-write count
    is 11. The loser must have its OWN row removed and see the paywall — and the winner's
    row must be left alone."""
    def _other_tap_lands(fake: _FakeDB):
        fake.follows.add((_USER, _RACING_WHALE_ID))

    db = _FakeDB(_pro_roster(9), on_upsert=_other_tap_lands)
    with pytest.raises(WhaleFollowLockedException) as ei:
        await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert ei.value.limit == 10
    assert db.upserts and db.upserts[0]["whale_id"] == _OTHER_WHALE_ID   # we DID write
    assert db.deletes == [(_USER, _OTHER_WHALE_ID)]                       # …and undid it
    assert (_USER, _RACING_WHALE_ID) in db.follows                        # winner kept
    assert len(db.follows) == 10


@pytest.mark.asyncio
async def test_toggle_refollow_at_the_cap_succeeds(monkeypatch):
    """The escape hatch, end to end: at 10/10, re-following one of the ten is a no-op
    upsert and must not paywall or roll anything back."""
    db = _FakeDB(_pro_roster(9) + [(_USER, _OTHER_WHALE_ID)])
    resp = await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert resp.is_following is True
    assert db.deletes == [] and len(db.follows) == 10


@pytest.mark.asyncio
async def test_toggle_refollow_over_the_cap_does_not_trigger_compensation(monkeypatch):
    """The escape hatch's return value is what SKIPS the post-write count. A Pro account
    holding 11 (post-downgrade) that re-follows one of its eleven has written nothing new;
    if `toggle_follow` ignored the True and ran the compensation anyway, it would find
    11 > 10, DELETE the row the user already had, and show a paywall for a whale they
    were tracking a second ago — "truncate, never destroy" violated on a no-op."""
    held = _pro_roster(10) + [(_USER, _OTHER_WHALE_ID)]          # 11 on a 10-plan
    db = _FakeDB(held)
    resp = await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert resp.is_following is True
    assert db.deletes == []
    assert len(db.follows) == 11 and (_USER, _OTHER_WHALE_ID) in db.follows


@pytest.mark.asyncio
async def test_toggle_roster_fault_writes_nothing(monkeypatch):
    """Transport fault on the gate's read: the error propagates (the endpoint maps it to
    a 500, iOS shows a retryable error) and — the part that matters — no row was written
    on the strength of a count that never happened."""
    db = _FakeDB(fail={("whale_follows", "select")})
    with pytest.raises(RuntimeError):
        await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert db.upserts == []


@pytest.mark.asyncio
async def test_toggle_post_write_count_fault_is_non_fatal(monkeypatch):
    """The other side of the degrade: the write LANDED and the confirm read saw it, then
    the `whales` follower-count read fails. That is non-fatal by design — the mutation
    succeeded and must be reported as such (with a zero count), not turned into a 500
    that makes iOS revert the pill. Note `is_following` here is OBSERVED (the confirm
    read on `whale_follows` succeeded); the intent-echo path is the test below."""
    db = _FakeDB(_pro_roster(3), fail={("whales", "select")})
    resp = await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_PRO)
    assert resp.is_following is True
    assert resp.followers_count == 0
    assert (_USER, _OTHER_WHALE_ID) in db.follows


@pytest.mark.asyncio
async def test_toggle_confirm_read_fault_reports_the_intent(monkeypatch):
    """The documented degrade for the CONFIRM read itself: "report the intent rather than
    failing the whole mutation". Only Max can reach it — every other tier's gate reads
    `whale_follows` first, so faulting that table would refuse before the write. Max
    never consults the roster, the upsert lands, and then the read-back raises.

    Found by mutation: initialising `is_following = False` instead of `= follow` passed
    every test in this file, yet it tells the client "not following" for a row that IS
    in the table — iOS reverts the pill, and the next screen load flips it back."""
    db = _FakeDB(_pro_roster(3), fail={("whale_follows", "select")})
    resp = await _toggle(monkeypatch, db, _OTHER_WHALE_ID, True, TIER_MAX)
    assert (_USER, _OTHER_WHALE_ID) in db.follows, "the write did not land — proves nothing"
    assert resp.is_following is True                     # the intent, since nothing was observed
    assert resp.followers_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [TIER_FREE, None, "nonsense"])
async def test_toggle_unfollow_is_never_gated(monkeypatch, tier):
    """A user over a limit (post-downgrade, or from before the gate) must always be able
    to get back under it. Free, no tier at all (the DELETE route passes none), and
    garbage all unfollow a NON-free whale without a paywall."""
    db = _FakeDB([(_USER, _FREE_WHALE_ID), (_USER, _OTHER_WHALE_ID), (_USER, _RACING_WHALE_ID)])
    resp = await _toggle(monkeypatch, db, _OTHER_WHALE_ID, False, tier)
    assert resp.is_following is False
    assert (_USER, _OTHER_WHALE_ID) not in db.follows
    assert len(db.follows) == 2                          # only the requested row went


@pytest.mark.asyncio
async def test_toggle_reports_observed_state_not_intent(monkeypatch):
    """An upsert RLS filters out answers 200 with nothing written. `is_following` used to
    echo the `follow` argument, so the client persisted a follow that never existed."""
    db = _FakeDB(swallow_upserts=True)
    resp = await _toggle(monkeypatch, db, _FREE_WHALE_ID, True, TIER_FREE)
    assert db.upserts, "the write was never attempted — this test proves nothing"
    assert resp.is_following is False


# ── 5. The endpoints map the exception to the typed error ───────────────────

@pytest.mark.asyncio
async def test_follow_route_maps_the_exception_to_the_typed_error(monkeypatch):
    """Invariant #3: the refusal reaches iOS as `WHALE_FOLLOW_LOCKED` with the numbers in
    `details` as FLAT scalars (iOS `AnyCodable` yields "" for anything nested)."""
    db = _FakeDB()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: db)
    resp = await whales_endpoint.follow_whale(
        _OTHER_WHALE_ID, user={"id": _USER, "tier": TIER_FREE}
    )
    assert not isinstance(resp, FollowResponse), "a Free caller was allowed through"
    assert resp.status_code == 403
    body = json.loads(resp.body)
    assert body["error_code"] == ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert body["user_message"] and body["action"] == "upgrade"
    assert body["details"] == {"tier_required": TIER_PRO, "limit": 1}
    assert all(isinstance(v, (str, int, bool)) for v in body["details"].values())
    assert db.upserts == []


@pytest.mark.asyncio
async def test_follow_route_threads_the_callers_tier_through(monkeypatch):
    """Drop `tier=user.get("tier")` from the route and every caller is gated as Free —
    closed, but wrong: a Max subscriber could follow nobody but the free whale."""
    db = _FakeDB(_pro_roster(50))
    monkeypatch.setattr(wsvc, "get_supabase", lambda: db)
    resp = await whales_endpoint.follow_whale(
        _OTHER_WHALE_ID, user={"id": _USER, "tier": TIER_MAX}
    )
    assert isinstance(resp, FollowResponse) and resp.is_following is True


@pytest.mark.asyncio
async def test_follow_route_with_no_tier_key_is_treated_as_free(monkeypatch):
    """`user.get("tier")` is None for a degraded identity dict. That must lock."""
    db = _FakeDB()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: db)
    resp = await whales_endpoint.follow_whale(_OTHER_WHALE_ID, user={"id": _USER})
    assert not isinstance(resp, FollowResponse) and resp.status_code == 403


@pytest.mark.asyncio
async def test_unfollow_route_is_never_gated(monkeypatch):
    """The DELETE route passes no tier on purpose. A Free caller unfollowing a NON-free
    whale (held from before the gate, or after a downgrade) must succeed — wiring the
    gate into this route would strand every over-limit account above its cap."""
    db = _FakeDB([(_USER, _OTHER_WHALE_ID), (_USER, _FREE_WHALE_ID)])
    monkeypatch.setattr(wsvc, "get_supabase", lambda: db)
    resp = await whales_endpoint.unfollow_whale(
        _OTHER_WHALE_ID, user={"id": _USER, "tier": TIER_FREE}
    )
    assert isinstance(resp, FollowResponse) and resp.is_following is False
    assert (_USER, _OTHER_WHALE_ID) not in db.follows
    assert (_USER, _FREE_WHALE_ID) in db.follows            # only the requested row went
