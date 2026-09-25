"""The complimentary tier floor (`users.comp_tier`, migration 177).

WHY. The App Review demo account sits on Max with no subscription behind it (Pro/Max
narration answers the 1.0 (9) Guideline 2.5.4 rejection). `reconcile_user_tier` mirrors
`users.tier` from the `subscriptions` rows, so the reviewer's own sandbox purchase re-tiered the
account and the sandbox expiry (~1 hour) dropped it to Free — locking the feature under review.
Found by the 2026-09-24 pre-resubmission audit (payments-2).

These run the REAL `apply_transaction` / `reconcile_user_tier` against the credit-modelling
fake from test_iap_entitlement.py, so a floor that reached `users.tier` but not the credit
allocation (or clawed credits back on a refund) would fail here, not in production.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest

from app.services import iap_service as svc

_spec = importlib.util.spec_from_file_location(
    "iap_entitlement_fakes", Path(__file__).with_name("test_iap_entitlement.py")
)
_fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fx)

FakeSupabase, _service, _txn, _sub = _fx.FakeSupabase, _fx._service, _fx._txn, _fx._sub
_USER, _PRO, _MAX = _fx._USER, _fx._PRO, _fx._MAX


def _with_comp(sb: FakeSupabase, comp, tier=None) -> FakeSupabase:
    row = sb.db["users"][0]
    row["comp_tier"] = comp
    if tier:
        row["tier"] = tier
    return sb


def _users_tier(sb: FakeSupabase) -> str:
    return sb.db["users"][0].get("tier")


# ── the demo-account scenarios ──────────────────────────────────────────────────────────


def test_a_floor_with_no_subscription_holds_the_tier_and_grants_its_allocation():
    sb = _with_comp(FakeSupabase(credits={"total": 50, "used": 50}), "premium", tier="premium")
    assert _service(sb).reconcile_user_tier(_USER) == "premium"
    assert _users_tier(sb) == "premium"
    assert sb.credits["total"] == 4000, "the floor must deliver Max's allocation, not Free's 50"


def test_reviewer_buying_pro_in_sandbox_does_not_demote_a_max_floor():
    sb = _with_comp(FakeSupabase(), "premium", tier="premium")
    out = _service(sb).apply_transaction(_USER, _txn(product=_PRO))
    assert out["tier"] == "pro"               # what THIS transaction was
    assert out["winning_tier"] == "premium"   # what the account is
    assert _users_tier(sb) == "premium"


def test_sandbox_expiry_does_not_drop_a_floored_account_to_free():
    """The exact failure: the accelerated sandbox subscription lapses ~1 hour later."""
    sb = _with_comp(FakeSupabase(subscriptions=[_sub("premium", status="expired", days=-1)]),
                    "premium", tier="free")
    assert _service(sb).reconcile_user_tier(_USER) == "premium"
    assert _users_tier(sb) == "premium"


def test_a_refund_on_a_floored_account_claws_back_nothing():
    """The clawback is gated on the reconciled tier being free; a floored account never is."""
    sb = _with_comp(FakeSupabase(credits={"total": 4000, "used": 100}), "premium", tier="premium")
    _service(sb).apply_transaction(
        _USER, _txn(product=_PRO, revocationDate=_fx._ms(_fx.datetime.now(_fx.timezone.utc)))
    )
    assert _users_tier(sb) == "premium"
    assert sb.credits["total"] == 4000
    assert not any(name == "revoke_tier_credits" for name, _ in sb.rpcs)


# ── floor semantics ─────────────────────────────────────────────────────────────────────


def test_it_is_a_floor_not_a_ceiling():
    sb = _with_comp(FakeSupabase(subscriptions=[_sub("premium")]), "pro")
    assert _service(sb).effective_tier(_USER) == "premium"


def test_a_lapsed_paid_tier_returns_to_the_floor_not_free():
    sb = _with_comp(FakeSupabase(subscriptions=[_sub("premium", status="expired", days=-1)]), "pro")
    assert _service(sb).effective_tier(_USER) == "pro"


@pytest.mark.parametrize("comp", [None, "", "free"])
def test_no_floor_changes_nothing(comp):
    sb = _with_comp(FakeSupabase(subscriptions=[_sub("pro")]), comp)
    assert _service(sb).effective_tier(_USER) == "pro"
    sb2 = _with_comp(FakeSupabase(), comp)
    assert _service(sb2).effective_tier(_USER) == "free"


def test_an_unknown_comp_value_is_ignored_and_warned(caplog):
    sb = _with_comp(FakeSupabase(subscriptions=[_sub("pro")]), "platinum")
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        assert _service(sb).effective_tier(_USER) == "pro"
    assert "unknown comp_tier" in caplog.text


# ── failure modes ───────────────────────────────────────────────────────────────────────


class _CodedError(Exception):
    def __init__(self, code):
        super().__init__(f"postgrest error {code}")
        self.code = code


class _CompReadFails(FakeSupabase):
    """`users` SELECT of comp_tier raises; everything else behaves."""

    def __init__(self, exc, **kw):
        super().__init__(**kw)
        self._exc = exc

    def table(self, name):
        q = super().table(name)
        if name != "users":
            return q
        outer = self
        real_select = q.select

        def select(*cols, **kw):
            if cols and "comp_tier" in cols[0]:
                class _Boom:
                    def eq(self, *_a): return self
                    def limit(self, *_a): return self
                    def execute(self): raise outer._exc
                return _Boom()
            return real_select(*cols, **kw)

        q.select = select
        return q


@pytest.mark.parametrize("code", sorted(svc._MISSING_COLUMN_CODES))
def test_a_missing_column_means_no_floor_so_deploy_before_migrate_is_safe(code, caplog):
    sb = _CompReadFails(_CodedError(code), subscriptions=[_sub("pro")])
    with caplog.at_level(logging.WARNING, logger=svc.logger.name):
        assert _service(sb).reconcile_user_tier(_USER) == "pro"
    assert "Apply migration 177" in caplog.text
    assert _users_tier(sb) == "pro"


def test_any_other_read_failure_refuses_rather_than_guessing_no_floor():
    """Guessing 'no floor' on a transient error would demote the demo account — the bug."""
    sb = _CompReadFails(_CodedError("08006"))
    _with_comp(sb, "premium", tier="premium")
    with pytest.raises(svc.IAPError):
        _service(sb).reconcile_user_tier(_USER)
    assert _users_tier(sb) == "premium", "users.tier must be left alone on a read failure"


def test_an_uncoded_exception_also_refuses():
    sb = _CompReadFails(RuntimeError("connection reset"))
    with pytest.raises(svc.IAPError):
        _service(sb).effective_tier(_USER)


def test_the_migration_adds_a_nullable_enum_column():
    sql = (Path(__file__).resolve().parents[1] / "database" / "migrations"
           / "177_users_comp_tier.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS comp_tier public.user_tier;" in sql
    assert "NOT NULL" not in sql.split("ALTER TABLE public.users", 1)[1].split(";", 1)[0]


# MUTATION_LOG (hand-run 2026-09-24, each reverted):
#  1. reconcile_user_tier back to `self.winning_tier(user_id)` -> 7 FAILED ✅
#  2. comp_tier's non-missing-column failure `return "free"` instead of raising -> 2 FAILED ✅


# ── scripts/set_comp_tier.py ────────────────────────────────────────────────────────────

_sct_spec = importlib.util.spec_from_file_location(
    "set_comp_tier", Path(__file__).resolve().parents[1] / "scripts" / "set_comp_tier.py")
_sct = importlib.util.module_from_spec(_sct_spec)
_sct_spec.loader.exec_module(_sct)


@pytest.mark.parametrize(
    "current, requested, expected",
    [
        # the demo account today: Max by hand, no floor -> add the floor, tier already Max
        ({"tier": "premium", "comp_tier": None}, "premium", {"comp_tier": "premium"}),
        # already floored -> nothing to write (the RPCs still run to fix the allocation)
        ({"tier": "premium", "comp_tier": "premium"}, "premium", {}),
        # a Free account given a Pro floor -> tier raised to the floor
        ({"tier": "free", "comp_tier": None}, "pro", {"comp_tier": "pro", "tier": "pro"}),
        # a paying Max subscriber given a Pro floor -> never lowered
        ({"tier": "premium", "comp_tier": None}, "pro", {"comp_tier": "pro"}),
        # removing a floor writes only the floor; main() then runs reconcile_user_tier
        ({"tier": "premium", "comp_tier": "premium"}, None, {"comp_tier": None}),
    ],
)
def test_set_comp_tier_plan(current, requested, expected):
    assert _sct.plan_change(current, requested) == expected


def test_removing_a_floor_runs_the_reconciler():
    """Reviewer finding: without this, `--tier none` left the account on Max forever (nothing
    else ever reconciles an account with no subscription rows)."""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "set_comp_tier.py").read_text()
    main = src[src.index("def main()"):]
    branch = main[main.index("if requested is None:"):]
    assert "IAPService().reconcile_user_tier(user[\"id\"])" in branch.split("else:", 1)[0]
