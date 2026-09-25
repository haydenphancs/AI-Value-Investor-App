#!/usr/bin/env python3
"""Give ONE account a complimentary tier floor (`users.comp_tier`, migration 177).

WHY. The App Review demo account sits on Max with no subscription behind it, so the reviewer's
own sandbox purchase used to re-tier it and the sandbox expiry dropped it to Free — locking the
Pro/Max narration that answers the 1.0 (9) Guideline 2.5.4 rejection. It also showed Max with
Free's 50 monthly credits, because its tier was written by hand and the Max allocation was
never granted. This sets the floor AND runs the same two credit RPCs a real purchase runs.

The account is found by EMAIL, and no password is involved. Use it for accounts that are not
in `testflight_testers.local.json` (that file's seed script sets the floor itself).

DEFAULTS TO A DRY RUN. It prints the account's current tier, floor and credits, then what
would change.

    ./venv/bin/python scripts/set_comp_tier.py --email appreview@caydexinvest.com --tier premium
    ./venv/bin/python scripts/set_comp_tier.py --email appreview@caydexinvest.com --tier premium --apply
    ./venv/bin/python scripts/set_comp_tier.py --email someone@x.com --tier none --apply   # remove

Requires migration 177. Refuses (exit 1) if the column is missing rather than half-applying.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VALID = ("free", "pro", "premium")
_MISSING_COLUMN_CODES = ("42703", "PGRST204")


def plan_change(current: Dict[str, Any], requested: Optional[str]) -> Dict[str, Any]:
    """The `users` columns to write. Pure, so it is tested without a database.

    `tier` is raised to the floor but never lowered HERE: lowering is the reconciler's job,
    because only it knows about real subscriptions. `main` runs it explicitly when a floor is
    removed — an account with no subscription rows is never reconciled on its own (no verify,
    no notification, and the expiry sweep only visits active rows), so without that call a
    removed Max floor would leave the account on Max, granted 4,000 credits every month.
    """
    rank = {t: i for i, t in enumerate(VALID)}
    change: Dict[str, Any] = {}
    if (current.get("comp_tier") or None) != requested:
        change["comp_tier"] = requested
    cur_tier = (current.get("tier") or "free").lower()
    if requested and rank[requested] > rank.get(cur_tier, 0):
        change["tier"] = requested
    return change


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True)
    ap.add_argument("--tier", required=True, help="pro | premium | none")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    requested = None if args.tier.lower() in ("none", "free") else args.tier.lower()
    if requested is not None and requested not in VALID:
        print(f"✗ --tier must be pro, premium or none (got {args.tier!r})")
        return 1

    from app.database import get_supabase  # noqa: PLC0415 — needs backend/.env

    db = get_supabase()
    email = args.email.strip().lower()
    try:
        rows = db.table("users").select("id,tier,comp_tier").eq("email", email).limit(1).execute().data
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) in _MISSING_COLUMN_CODES:
            print("✗ users.comp_tier does not exist — apply migration 177 first")
            return 1
        print(f"✗ read failed: {type(e).__name__}: {e}")
        return 1
    if not rows:
        print(f"✗ no public.users row for {email}")
        return 1
    user = rows[0]
    credits = (db.table("user_credits").select("total,used,purchased_total,purchased_used,tier_alloc")
               .eq("user_id", user["id"]).limit(1).execute().data or [{}])[0]
    print(f"  {email}: tier={user.get('tier')} comp_tier={user.get('comp_tier')} "
          f"monthly={credits.get('used')}/{credits.get('total')} (alloc {credits.get('tier_alloc')}) "
          f"purchased={(credits.get('purchased_total') or 0) - (credits.get('purchased_used') or 0)}")

    change = plan_change(user, requested)
    print(f"  change: {change or 'none to users'} · then ensure_credit_period + grant_tier_upgrade")
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
        return 0

    if change:
        db.table("users").update(change).eq("id", user["id"]).execute()
    if requested is None:
        # Floor removed: let the real reconciler recompute the tier from the subscriptions
        # (which may lower it) and run the credit RPCs. See `plan_change`.
        from app.services.iap_service import IAPService  # noqa: PLC0415
        IAPService().reconcile_user_tier(user["id"])
    else:
        # Same order as iap_service.reconcile_user_tier: roll the period, then lift the
        # allocation to the (now floored) tier. grant_tier_upgrade is idempotent, never claws back.
        db.rpc("ensure_credit_period", {"p_user_id": user["id"]}).execute()
        db.rpc("grant_tier_upgrade", {"p_user_id": user["id"]}).execute()
    after_u = db.table("users").select("tier,comp_tier").eq("id", user["id"]).limit(1).execute().data[0]
    after_c = (db.table("user_credits").select("total,used,tier_alloc")
               .eq("user_id", user["id"]).limit(1).execute().data or [{}])[0]
    print(f"  ✓ now tier={after_u.get('tier')} comp_tier={after_u.get('comp_tier')} "
          f"monthly={after_c.get('used')}/{after_c.get('total')} (alloc {after_c.get('tier_alloc')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
