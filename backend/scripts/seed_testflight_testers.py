"""
Seed / repair the TestFlight tester accounts, through GoTrue itself.

The safer sibling of the raw-SQL seed. `auth.admin.create_user` makes GoTrue write the
`auth.users` + `auth.identities` rows, so this path cannot drift from GoTrue's internal
schema the way hand-written INSERTs can. The credit half is identical to the SQL.

THE CREDENTIALS FILE IS THE CONTROL PANEL. Edit scripts/testflight_testers.local.json and
re-run; `credits` and `tier` are per-tester and optional:

    [
      {"email": "a@x.com", "password": "...", "display_name": "A"},
      {"email": "b@x.com", "password": "...", "display_name": "B",
       "credits": 5000, "tier": "premium"}
    ]

What a re-run DOES change: password, display_name, tier, and the purchased balance (upward).
What it does NOT change: the email. The lookup is BY email, so editing one creates a NEW
account and leaves the old one live — the orphan scan reports that.

Per tester this produces:
  auth.users / auth.identities   via GoTrue (email_confirm=True — /auth/login hard-blocks
                                 with EMAIL_NOT_CONFIRMED otherwise)
  public.users                   created by the on_auth_user_created trigger; VERIFIED here,
                                 because that trigger catches WHEN OTHERS and only warns —
                                 a missing mirror lets /auth/login succeed while every other
                                 route 401s AUTH_ACCOUNT_NOT_FOUND
  public.user_credits            tier allocation via grant_tier_upgrade(), plus a
                                 never-expiring purchased top-up
  credit_transactions            one 'tester_grant' row per actual grant

WHY THE PURCHASED POOL. `ensure_credit_period` hard-overwrites `total`/`used` at each ET
month boundary, so credits parked in the granted pool are destroyed on the tester's next
credits read. `purchased_total`/`purchased_used` are never touched by ensure_credit_period,
grant_tier_upgrade or revoke_tier_credits (App Store 3.1.1 forbids expiring paid credits),
which is exactly the durability we want. We deliberately do NOT call `add_purchased_credits`:
its first act is an INSERT into `credit_purchases`, which would fabricate a $0 row in the
revenue table.

THE PURCHASED BALANCE ONLY EVER GOES UP. `max(purchased_total, purchased_used + target)` is
what stops a re-run from clawing back a pack a tester really bought, so lowering `credits`
in the JSON is reported and ignored rather than applied. Lower one with direct SQL.

Accounts are stamped `app_metadata.caydex_tester = true`, the same marker the SQL scripts
gate on — so the two paths interoperate and either teardown finds these accounts.

Idempotent. NOT atomic (one round trip per tester); on a partial failure, fix and re-run.

Prerequisites:
  - backend/.env with SUPABASE service-role credentials.
  - scripts/testflight_testers.local.json — gitignored, holds the credentials.

Usage (from backend/):
    ./venv/bin/python scripts/seed_testflight_testers.py --dry-run
    ./venv/bin/python scripts/seed_testflight_testers.py
    ./venv/bin/python scripts/seed_testflight_testers.py --verify-only
"""
import argparse
import json
import sys
from pathlib import Path

# Make `app` importable when run from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import get_admin_client, get_supabase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]                    # backend/
CREDENTIALS_PATH = ROOT / "scripts" / "testflight_testers.local.json"

BATCH = "testflight-2026-08"        # ledger ref_id + the marker's batch tag
DEFAULT_TIER = "pro"                # per-tester override: "tier"
DEFAULT_CREDITS = 1000              # per-tester override: "credits"
VALID_TIERS = ("free", "pro", "premium")   # mirrors the public.user_tier enum

# Mirrors backend/app/schemas/auth.py::_validate_password_strength. Enforced here too because
# GoTrue's own policy is configured per-project and can be relaxed — a password that fails the
# BACKEND policy can still sign in, but can never be changed in-app, which strands the tester.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
BCRYPT_MAX_BYTES = 72

MAX_ORPHAN_PAGES = 20               # 20 x 200 = 4000 accounts scanned for stale testers
ORPHAN_PAGE_SIZE = 200


def password_problem(password: str) -> str | None:
    """Return a human-readable reason the password is unusable, or None if it is fine."""
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return f"must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters"
    if password.strip() != password:
        return "has leading or trailing whitespace"
    if not any(c.isupper() for c in password):
        return "needs an uppercase letter"
    if not any(c.islower() for c in password):
        return "needs a lowercase letter"
    if not any(c.isdigit() for c in password):
        return "needs a digit"
    if not any(not c.isalnum() for c in password):
        return "needs a symbol"
    # bcrypt hashes only the first 72 bytes and silently ignores the rest.
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return f"is {len(password.encode('utf-8'))} bytes; bcrypt truncates at {BCRYPT_MAX_BYTES}"
    return None


def load_testers(path: Path) -> list[dict]:
    """Parse + validate the credentials file. Raises SystemExit with a usable message."""
    if not path.exists():
        raise SystemExit(
            f"Credentials file not found: {path}\n"
            'Create it as: [{"email": "...", "password": "...", "display_name": "...",\n'
            '               "credits": 1000, "tier": "pro"}, ...]\n'
            '("credits" and "tier" are optional; they default to '
            f'{DEFAULT_CREDITS} and "{DEFAULT_TIER}".)\n'
            "It is gitignored on purpose — do not commit tester passwords."
        )
    try:
        testers = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON: {e}") from e

    if not isinstance(testers, list) or not testers:
        raise SystemExit(f"{path} must be a non-empty JSON array.")

    seen: set[str] = set()
    for i, t in enumerate(testers):
        if not isinstance(t, dict):
            raise SystemExit(f"{path}[{i}] must be an object, got {type(t).__name__}")
        missing = [k for k in ("email", "password", "display_name") if not t.get(k)]
        if missing:
            raise SystemExit(f"{path}[{i}] is missing {missing}")

        email = str(t["email"]).strip().lower()
        if email in seen:
            raise SystemExit(f"{path}[{i}] duplicates the email {email}")
        seen.add(email)
        t["email"] = email

        problem = password_problem(str(t["password"]))
        if problem:
            raise SystemExit(
                f"{path}[{i}] ({email}): password {problem}. It would fail "
                "backend/app/schemas/auth.py, so the tester could never change it in-app."
            )

        # `credits` — optional per-tester override. bool is an int subclass; reject it.
        credits = t.get("credits", DEFAULT_CREDITS)
        if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
            raise SystemExit(f'{path}[{i}] ({email}): "credits" must be a non-negative integer, '
                             f"got {credits!r}")
        t["credits"] = credits

        # `tier` — optional per-tester override.
        tier = str(t.get("tier", DEFAULT_TIER)).strip().lower()
        if tier not in VALID_TIERS:
            raise SystemExit(f'{path}[{i}] ({email}): "tier" must be one of {VALID_TIERS}, '
                             f"got {tier!r}")
        t["tier"] = tier
    return testers


def read_credits(db, user_id: str) -> dict | None:
    rows = db.table("user_credits").select("*").eq("user_id", user_id).limit(1).execute().data
    return rows[0] if rows else None


def describe(row: dict) -> str:
    granted = row["total"] - row["used"]
    purchased = row["purchased_total"] - row["purchased_used"]
    return f"spendable={granted + purchased} (granted {granted} + purchased {purchased})"


def upsert_account(admin, db, tester: dict) -> str | None:
    """Create or repair the GoTrue account. Returns the user id, or None on failure."""
    email, display_name = tester["email"], tester["display_name"]
    app_metadata = {"caydex_tester": True, "caydex_tester_batch": BATCH}
    try:
        created = admin.auth.admin.create_user({
            "email": email,
            "password": tester["password"],
            "email_confirm": True,          # sets email_confirmed_at; without it login is refused
            "user_metadata": {"display_name": display_name},
            "app_metadata": app_metadata,
        })
        print(f"  created  {email} -> {created.user.id}")
        return str(created.user.id)
    except Exception as e:                                   # noqa: BLE001 — reported, not swallowed
        if "already" not in str(e).lower():
            print(f"  FAILED   {email}: create_user: {type(e).__name__}: {e}", file=sys.stderr)
            return None

    # Already registered — repair it in place. Resolve the id via public.users rather than
    # paging auth.admin.list_users().
    rows = db.table("users").select("id").eq("email", email).limit(1).execute().data
    if not rows:
        print(f"  FAILED   {email}: exists in GoTrue but has no public.users row. The "
              "on_auth_user_created trigger swallowed an error; fix that first.", file=sys.stderr)
        return None
    user_id = str(rows[0]["id"])
    try:
        admin.auth.admin.update_user_by_id(user_id, {
            "password": tester["password"],
            "email_confirm": True,
            "user_metadata": {"display_name": display_name},
            "app_metadata": app_metadata,
        })
    except Exception as e:                                   # noqa: BLE001
        print(f"  FAILED   {email}: update_user_by_id: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    print(f"  repaired {email} -> {user_id}")
    return user_id


def sync_profile(db, user_id: str, tester: dict) -> str | None:
    """Push display_name into public.users — the copy the app actually renders.

    GoTrue's user_metadata is NOT what the client sees: `handle_new_auth_user` copies it into
    public.users once, at INSERT. Without this, renaming a tester in the JSON silently does
    nothing to an account that already exists.
    """
    try:
        rows = db.table("users").select("display_name").eq("id", user_id).limit(1).execute().data
        current = rows[0]["display_name"] if rows else None
        if current == tester["display_name"]:
            return None
        db.table("users").update({"display_name": tester["display_name"]}).eq("id", user_id).execute()
        print(f"  {tester['email']}: display_name {current!r} -> {tester['display_name']!r}")
    except Exception as e:                                   # noqa: BLE001
        return f"display_name sync: {type(e).__name__}: {e}"
    return None


def apply_credits(db, user_id: str, tester: dict) -> str | None:
    """Set the tier, grant its allocation, top the purchased pool up. Returns an error string."""
    email, tier, target = tester["email"], tester["tier"], tester["credits"]
    try:
        db.table("users").update({"tier": tier}).eq("id", user_id).execute()
        # Creates the user_credits row if absent and rolls a due period.
        db.rpc("ensure_credit_period", {"p_user_id": user_id}).execute()
        # Reuse the production RPC: total = GREATEST(alloc, total), tier_alloc stamped, ledger
        # row written, resets_at untouched, replay-guarded on tier_alloc.
        db.rpc("grant_tier_upgrade", {"p_user_id": user_id}).execute()
    except Exception as e:                                   # noqa: BLE001
        return f"tier grant: {type(e).__name__}: {e}"

    row = read_credits(db, user_id)
    if row is None:
        return "no user_credits row after ensure_credit_period"

    # Top up, never claw back — protects a tester's real IAP pack from a re-run.
    current = row["purchased_total"] - row["purchased_used"]
    new_total = max(row["purchased_total"], row["purchased_used"] + target)
    delta = new_total - row["purchased_total"]
    if delta <= 0:
        # Say WHY nothing happened. A silent no-op here reads as "the edit didn't take".
        note = (f"target {target} is at or below the current {current}; purchased balances are "
                "never lowered (that rule protects real IAP packs) — use SQL to reduce one"
                if target < current else f"already at target {target}")
        print(f"  {email}: unchanged · {note} · {describe(row)}")
        return None

    try:
        db.table("user_credits").update({"purchased_total": new_total}).eq("user_id", user_id).execute()
        after = read_credits(db, user_id) or {}
        spendable = ((after.get("total", 0) - after.get("used", 0))
                     + (after.get("purchased_total", 0) - after.get("purchased_used", 0)))
        # delta = granted_delta + purchased_delta is a documented invariant (migration 118).
        # refund_credits can never pair with this row: it only matches debits (delta < 0).
        db.table("credit_transactions").insert({
            "user_id": user_id,
            "delta": delta,
            "granted_delta": 0,
            "purchased_delta": delta,
            "reason": "tester_grant",
            "ref_id": BATCH,
            "balance_after": spendable,
        }).execute()
    except Exception as e:                                   # noqa: BLE001
        return f"purchased top-up: {type(e).__name__}: {e}"

    print(f"  {email}: +{delta} purchased · {describe(after)}")
    return None


def find_orphan_testers(admin, known_emails: set[str]) -> list[str]:
    """Marked tester accounts that are NOT in the credentials file.

    The one failure this catches: editing an email in the JSON does not rename anything. The
    lookup is by email, so the old account keeps existing — still signed in, still holding
    credits — while a brand-new one is created alongside it.
    """
    orphans: list[str] = []
    for page in range(1, MAX_ORPHAN_PAGES + 1):
        users = admin.auth.admin.list_users(page=page, per_page=ORPHAN_PAGE_SIZE)
        for u in users or []:
            meta = getattr(u, "app_metadata", None) or {}
            email = (getattr(u, "email", None) or "").lower()
            if meta.get("caydex_tester") and email and email not in known_emails:
                orphans.append(email)
        if not users or len(users) < ORPHAN_PAGE_SIZE:
            return orphans
    print(f"  NOTE: stopped after {MAX_ORPHAN_PAGES * ORPHAN_PAGE_SIZE} accounts; "
          "the orphan scan may be incomplete.", file=sys.stderr)
    return orphans


def report_orphans(admin, testers: list[dict]) -> None:
    known = {t["email"] for t in testers}
    try:
        orphans = find_orphan_testers(admin, known)
    except Exception as e:                                   # noqa: BLE001 — never fatal
        print(f"  (orphan scan skipped: {type(e).__name__}: {e})", file=sys.stderr)
        return
    if not orphans:
        return
    print(f"\n⚠️  {len(orphans)} marked tester account(s) are NOT in your credentials file:",
          file=sys.stderr)
    for email in sorted(orphans):
        print(f"      {email}", file=sys.stderr)
    print("    These are still live and still hold credits. If you renamed an email in the "
          "JSON, this is the old account — delete it with the teardown SQL.", file=sys.stderr)


def verify(db, testers: list[dict]) -> int:
    print("\n── verification ─────────────────────────────────────────────")
    bad = 0
    for t in testers:
        rows = db.table("users").select("id, email, tier, display_name") \
                 .eq("email", t["email"]).limit(1).execute().data
        if not rows:
            print(f"  FAIL  {t['email']}: no public.users row — every authenticated route "
                  "will 401 AUTH_ACCOUNT_NOT_FOUND")
            bad += 1
            continue
        row = read_credits(db, str(rows[0]["id"]))
        if row is None:
            print(f"  FAIL  {t['email']}: no user_credits row")
            bad += 1
            continue
        purchased = row["purchased_total"] - row["purchased_used"]
        if purchased < t["credits"]:
            print(f"  FAIL  {t['email']}: purchased_remaining {purchased} < {t['credits']}")
            bad += 1
            continue
        if rows[0]["tier"] != t["tier"]:
            print(f"  FAIL  {t['email']}: tier is {rows[0]['tier']}, expected {t['tier']}")
            bad += 1
            continue
        print(f"  OK    {t['email']:32} tier={rows[0]['tier']:8} {describe(row)}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--file", type=Path, default=CREDENTIALS_PATH,
                        help=f"credentials JSON (default: {CREDENTIALS_PATH})")
    parser.add_argument("--dry-run", action="store_true", help="validate and print, write nothing")
    parser.add_argument("--verify-only", action="store_true", help="report current state, write nothing")
    parser.add_argument("--no-check-orphans", action="store_true",
                        help="skip the scan for marked tester accounts missing from the file")
    parser.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    args = parser.parse_args()

    testers = load_testers(args.file)
    # Read from settings, not the process environment: app/config.py loads backend/.env
    # through pydantic's env_file, which never exports anything to the OS environment —
    # so reading it there would print '<unset>' and make this confirmation worthless.
    url = settings.SUPABASE_URL

    if args.dry_run:
        print(f"DRY RUN · {len(testers)} tester(s) · batch={BATCH} · would target {url}")
        for t in testers:
            print(f"  {t['email']:32} tier={t['tier']:8} credits={t['credits']:<6} "
                  f"({t['display_name']})")
        return 0

    db = get_supabase()
    if args.verify_only:
        # Name the project: a verify against the wrong one is silently misleading.
        print(f"VERIFY ONLY · {len(testers)} tester(s) · {url}")
        bad = verify(db, testers)
        if not args.no_check_orphans:
            report_orphans(get_admin_client(), testers)
        return 1 if bad else 0

    print(f"About to seed {len(testers)} tester account(s) into:\n    {url}")
    for t in testers:
        print(f"    {t['email']:32} tier={t['tier']:8} credits={t['credits']}")
    if not args.yes and input("Type YES to continue: ") != "YES":
        print("aborted")
        return 2

    admin = get_admin_client()          # auth.admin.* ONLY — never .table(), never a sign-in
    failures: list[str] = []
    for tester in testers:
        user_id = upsert_account(admin, db, tester)
        if user_id is None:
            failures.append(tester["email"])
            continue
        # The trigger catches WHEN OTHERS, so a missing mirror is silent. Check it.
        if not db.table("users").select("id").eq("id", user_id).limit(1).execute().data:
            print(f"  FAILED   {tester['email']}: public.users MIRROR MISSING — "
                  "handle_new_auth_user swallowed an error. Every organic signup on this "
                  "project is probably broken too.", file=sys.stderr)
            failures.append(tester["email"])
            continue
        error = sync_profile(db, user_id, tester) or apply_credits(db, user_id, tester)
        if error:
            print(f"  FAILED   {tester['email']}: {error}", file=sys.stderr)
            failures.append(tester["email"])

    bad = verify(db, testers)
    if not args.no_check_orphans:
        report_orphans(admin, testers)
    if failures:
        print(f"\n{len(failures)} account(s) failed: {', '.join(failures)} — this script is "
              "idempotent, so fix the cause and re-run.", file=sys.stderr)
    return 1 if (failures or bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
