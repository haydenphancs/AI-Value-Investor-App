"""Guest research reports are partitioned per install (migration 110).

THE LEAK. `/research/*` resolved identity through `get_current_user_or_guest`, which returns the
shared `GUEST_USER_ID` for every signed-out caller, and `GET /research/reports` filters
`.eq("user_id", user["id"])`. So guest A generated a deep-research report on TSLA and guest B —
a different person, a different device — opened the Reports tab and saw it: ticker, executive
summary, overall score, fair-value estimate. B could open it, rate it, and DELETE it. The ticker
someone researches discloses intent, so this is cross-user data exposure, not untidy state.

Same defect migration 108 fixed for watchlists and 066/067 for Learn progress. It was never
extended here because `research_reports.user_id` had an FK to `public.users` and a synthetic
per-install uuid5 has no such row.

Three things must hold together or this is worse than before, and each is pinned below:
  1. the routes resolve a PER-INSTALL identity,
  2. account deletion purges `research_reports` EXPLICITLY (the dropped FK was the cascade that
     used to do it) — the generic guard cannot see this, see the comment on that test,
  3. sign-up CLAIMS the install's reports, so a guest does not spend their one free report and
     then lose it by creating an account.

No network / Supabase.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.api.v1.endpoints.users as users_ep
from app.dependencies import GUEST_USER_ID, get_research_identity, guest_user_id_for

_REPO = Path(__file__).resolve().parents[2]
_USER = {"id": "11111111-2222-4333-8444-555555555555"}


# ---------------------------------------------------------------------------
# 1. Per-install identity
# ---------------------------------------------------------------------------


class _Users:
    def select(self, *_a): return self
    def eq(self, *_a): return self
    def limit(self, *_a): return self
    def execute(self): return type("R", (), {"data": []})()


class _NoUserSB:
    def table(self, _n): return _Users()


@pytest.mark.asyncio
async def test_two_installs_get_different_identities():
    """THE leak, at its source: distinct installs must never resolve to one bucket."""
    a = await get_research_identity(authorization=None, x_guest_id="install-A", supabase=_NoUserSB())
    b = await get_research_identity(authorization=None, x_guest_id="install-B", supabase=_NoUserSB())
    assert a["id"] != b["id"], "two guests still share one research bucket"
    assert a["id"] != GUEST_USER_ID and b["id"] != GUEST_USER_ID


@pytest.mark.asyncio
async def test_identity_is_stable_across_requests():
    """It is a uuid5 of the install id, so a user's own reports survive a relaunch."""
    first = await get_research_identity(authorization=None, x_guest_id="install-A", supabase=_NoUserSB())
    second = await get_research_identity(authorization=None, x_guest_id="install-A", supabase=_NoUserSB())
    assert first["id"] == second["id"] == guest_user_id_for("install-A")


@pytest.mark.asyncio
async def test_guests_are_flagged_so_the_sentinel_comparison_is_not_needed():
    """`user["id"] == GUEST_USER_ID` can no longer detect a guest — a per-install id never
    equals the sentinel. Without an explicit flag every guest would be classified as signed-in
    and sent into `precharge` against a `user_credits` row that does not exist (402)."""
    guest = await get_research_identity(authorization=None, x_guest_id="install-A", supabase=_NoUserSB())
    assert guest["is_guest"] is True


@pytest.mark.asyncio
async def test_a_client_sending_no_install_id_still_gets_the_shared_sentinel():
    """Already-shipped app versions send no header; their behaviour must be unchanged."""
    out = await get_research_identity(authorization=None, x_guest_id=None, supabase=_NoUserSB())
    assert out["id"] == GUEST_USER_ID


def test_every_user_scoped_research_route_uses_the_partitioned_identity():
    src = (_REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "research.py").read_text()
    assert "get_current_user_or_guest" not in src, (
        "a /research route still resolves the SHARED guest sentinel — that route leaks "
        "one guest's reports to every other guest"
    )
    assert src.count("Depends(get_research_identity)") >= 9


def test_the_guest_test_does_not_rely_on_the_sentinel_comparison():
    src = (_REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "research.py").read_text()
    assert 'user.get("is_guest"' in src, (
        "is_guest still compares against GUEST_USER_ID, which a per-install id never matches — "
        "every guest would be treated as a paying account"
    )


# ---------------------------------------------------------------------------
# 2. Account deletion
# ---------------------------------------------------------------------------


def test_research_reports_is_purged_explicitly_on_account_deletion():
    """EXPLICIT, because the generic guard in test_account_deletion_completeness.py cannot see
    this one: it classifies tables by regexing `FOREIGN KEY (user_id)` out of
    schema_snapshot.sql, and the snapshot still predates migration 110 — so research_reports
    reads as FK-linked and is skipped. Removing it from the purge list breaks no other test.

    Without the purge, a deleted account's research reports — ticker, thesis, fair value —
    survive, which the privacy policy says they do not.
    """
    assert "research_reports" in users_ep._UNLINKED_USER_TABLES


def test_the_migration_that_dropped_the_cascade_exists():
    mig = _REPO / "backend" / "database" / "migrations" / "110_research_reports_guest_partition.sql"
    assert mig.exists(), "the code assumes the FK is gone; the migration must ship with it"
    sql = mig.read_text()
    assert "DROP CONSTRAINT IF EXISTS research_reports_user_id_fkey" in sql
    assert "COMMENT ON COLUMN public.research_reports.user_id" in sql, (
        "record the no-FK design in the schema itself, as migration 108 did"
    )


def test_the_migration_adds_no_duplicate_index():
    """`idx_reports_user (user_id, created_at DESC)` already exists; `IF NOT EXISTS` matches on
    NAME, so a differently-named copy would silently duplicate it."""
    sql = (
        _REPO / "backend" / "database" / "migrations" / "110_research_reports_guest_partition.sql"
    ).read_text()
    assert "CREATE INDEX" not in sql.upper().replace("CREATE INDEX IF NOT EXISTS", "")


# ---------------------------------------------------------------------------
# 3. The claim
# ---------------------------------------------------------------------------


class _Q:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self._op = None
        self._payload = None
        self._filter = None

    def select(self, *_a): self._op = "select"; return self
    def update(self, payload): self._op = "update"; self._payload = payload; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, col, val): self._filter = ("eq", col, val); return self
    def in_(self, col, vals): self._filter = ("in", col, list(vals)); return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        kind, col, val = self._filter
        match = (lambda r: r.get(col) == val) if kind == "eq" else (lambda r: r.get(col) in val)
        hit = [r for r in rows if match(r)]
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
        elif self._op == "delete":
            self.store[self.table] = [r for r in rows if not match(r)]
        return type("R", (), {"data": [dict(r) for r in hit]})()


class _SB:
    def __init__(self, store): self.store = store
    def table(self, name): return _Q(self.store, name)


async def _claim(store, guest_id="install-A"):
    return await users_ep.claim_guest_data(user=_USER, x_guest_id=guest_id, supabase=_SB(store))


def _empty_store(**extra):
    base = {"watchlist_items": [], "portfolios": [], "user_learn_progress": [], "research_reports": []}
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_signing_up_claims_this_installs_reports():
    """A guest spends their ONE free monthly report, then creates an account. Losing it there
    is the worst possible moment — they paid the allowance to get the thing they signed up for."""
    bucket = guest_user_id_for("install-A")
    store = _empty_store(research_reports=[{"id": "r1", "user_id": bucket}])
    res = await _claim(store)
    assert res["claimed"]["research_reports"] == 1
    assert store["research_reports"][0]["user_id"] == _USER["id"]


@pytest.mark.asyncio
async def test_claiming_resets_the_pdf_so_deletion_can_still_find_it():
    """`pdf_path` is stamped `reports/<user_id>/<id>.pdf` with the id the report had at
    GENERATION time. Re-pointing user_id without clearing it leaves the object under a prefix
    `_purge_research_pdfs` never lists — and once the row is deleted the path was the only
    handle that existed."""
    bucket = guest_user_id_for("install-A")
    store = _empty_store(research_reports=[{
        "id": "r1", "user_id": bucket,
        "pdf_path": f"reports/{bucket}/r1.pdf", "pdf_status": "ready",
        "pdf_generated_at": "2026-08-01T00:00:00+00:00",
    }])
    await _claim(store)
    row = store["research_reports"][0]
    assert row["pdf_path"] is None, "the PDF stays under the guest prefix and is orphaned"
    assert row["pdf_status"] == "pending"
    assert row["pdf_generated_at"] is None


@pytest.mark.asyncio
async def test_the_shared_legacy_bucket_is_still_never_claimed():
    """The security case: those reports belong to other people."""
    store = _empty_store(research_reports=[{"id": "r1", "user_id": GUEST_USER_ID}])
    res = await _claim(store, guest_id=None)
    assert res["claimed"]["research_reports"] == 0
    assert store["research_reports"][0]["user_id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_another_installs_reports_are_untouched():
    bucket_b = guest_user_id_for("install-B")
    store = _empty_store(research_reports=[
        {"id": "r1", "user_id": guest_user_id_for("install-A")},
        {"id": "r2", "user_id": bucket_b},
    ])
    await _claim(store)
    other = [r for r in store["research_reports"] if r["id"] == "r2"][0]
    assert other["user_id"] == bucket_b, "claimed another install's reports"


@pytest.mark.asyncio
async def test_the_claim_counter_is_reported():
    res = await _claim(_empty_store())
    assert "research_reports" in res["claimed"]


def test_the_claim_log_names_every_counter():
    """A claim that moved only reports used to log nothing at all."""
    src = (_REPO / "backend" / "app" / "api" / "v1" / "endpoints" / "users.py").read_text()
    block = re.search(r"if any\(claimed\.values\(\)\):(.*?)\n    return", src, re.DOTALL)
    assert block, "the claim success log changed shape"
    assert 'claimed["research_reports"]' in block.group(1)
