#!/usr/bin/env python3
"""Reward ONE confirmed Help Us Improve report with free credits (migration 199).

THE ONLY WAY A REPORT REWARD IS EVER GRANTED. The app says "We review every report. If we can
confirm the issue, we'll add credits to your account", so nothing in app/ or the iOS app grants
anything: a report arrives by email, the owner reproduces it, and only then runs this with
`--apply`. Not every report qualifies (duplicates, issues we can't reproduce), and the reward
is never tied to App Store ratings (Guideline 3.2.2).

WHO. The report email carries "Account ID: <uuid>" (signed-in reports only) and
"Report: R-XXXXXX". Use the Account ID: the sender address is often a Hide My Email relay and
matches no account. `--email` is there for the case where the user wrote from their account
address.

WHAT. `grant_feedback_reward` adds the credits to the NEVER-EXPIRING purchased pool (the
granted pool is overwritten at every monthly reset) and writes one `feedback_reward` ledger
row, which Credit History shows as a green "Issue report reward". The report code is the
idempotency key: running this twice for the same (account, code) grants once and reports
'replay' the second time. No credit_purchases row is written (a reward is not revenue).

DEFAULTS TO A DRY RUN. It prints the target Supabase project, the account, its tier and
balances, and its earlier rewards (warning if this report code is already among them). It
makes no RPC call. `--apply` makes exactly one.

    ./venv/bin/python scripts/grant_feedback_reward.py --user-id <Account ID> --ref R-7K2Q9M
    ./venv/bin/python scripts/grant_feedback_reward.py --user-id <Account ID> --ref R-7K2Q9M --apply
    ./venv/bin/python scripts/grant_feedback_reward.py --email someone@x.com --ref R-7K2Q9M --credits 40 --apply

After a grant, reply to the user's email. Do not run scripts/seed_testflight_testers.py at the
same time: its unlocked read-then-overwrite of purchased_total can drop a reward that lands
in between.

Requires migration 199. Without it `--apply` exits 1 with "apply migration 199 first".
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_CREDITS = 20
# Mirrors `_MAX` in grant_feedback_reward() (migration 199); tests/test_feedback_reward_grant.py
# holds the two together. The cap catches a typo (2000 for 20), not abuse.
MAX_CREDITS = 200
REASON = "feedback_reward"
RPC_NAME = "grant_feedback_reward"
# app.dependencies.GUEST_USER_ID: the id every signed-out install shares. Never rewardable.
GUEST_USER_ID = "00000000-0000-4000-8000-00000000dead"
# The code the iOS app puts in the report's subject and body (AppInfo.newReportCode). The
# alphabet leaves out 0, O, 1, I and L so a code read off an email cannot be misread as a
# different one; tests/test_feedback_reward_grant.py pins it to the Swift literal.
REPORT_CODE_PREFIX = "R-"
REPORT_CODE_LENGTH = 6
REPORT_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
# The column is TEXT, but the SQL refuses a longer ref (migration 199).
MAX_REF_LENGTH = 64
# How many earlier rewards the dry run lists.
PRIOR_LIMIT = 50

# The function is missing: PostgREST's "no such function in the schema cache" (PGRST202), or
# Postgres's own undefined_function (42883) when the cache is fresh but the function is not.
_MISSING_FUNCTION_CODES = ("PGRST202", "42883")

# A ref for a report that arrived without a code (an older build, a hand-written email). It must
# say so explicitly: anything else that is not a recognisable code is refused, so a code copied
# in an unexpected shape can never become a second idempotency key for the same report.
MANUAL_REF_PREFIX = "manual:"

_CODE_CHARS = f"[{REPORT_CODE_ALPHABET}]{{{REPORT_CODE_LENGTH}}}"
# The whole input is one code, however it was copied: "R-7K2Q9M", "R7K2Q9M", "R 7K2Q9M", or
# "7K2Q9M" (what a double-click selects in Mail, where the hyphen ends the word).
_BARE_CODE_RE = re.compile(rf"^(?:R[-\s]?)?({_CODE_CHARS})$")
# A code inside a longer line: "Report: R-7K2Q9M", or the subject "Caydex feedback · R-7K2Q9M".
# The hyphen is required here, or "CAYDEX" in the subject would read as a code.
_CODE_IN_TEXT_RE = re.compile(rf"(?<![A-Z0-9])R-({_CODE_CHARS})(?![A-Z0-9])")


# ── pure helpers (no database; unit-tested) ──────────────────────────────────


def parse_user_id(value: Any) -> str:
    """An Account ID → its canonical lowercase UUID string. ValueError on anything else.

    The guest sentinel is refused here as well as in the SQL: crediting it would hand the
    reward to every signed-out install at once.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("an Account ID is required")
    try:
        parsed = str(uuid.UUID(value.strip()))
    except ValueError:
        raise ValueError(f"{value!r} is not an Account ID (expected a UUID)") from None
    if parsed == GUEST_USER_ID:
        raise ValueError("that is the shared guest id, not an account; guests cannot be rewarded")
    return parsed


def normalize_ref(value: Any) -> str:
    """The report code, as the idempotency key: always "R-XXXXXX". ValueError otherwise.

    The SQL guards one reward per (account, ref) by exact match, so every way of copying the
    same code must land on the SAME key — "7K2Q9M", "r-7k2q9m", the whole "Report: R-7K2Q9M"
    line and the subject line all become "R-7K2Q9M". Anything that looks like a code but is not
    one (wrong length, 0/O/1/I/L, two different codes) is refused rather than granted under a
    key no real report carries. A report that arrived without a code takes an explicit
    "manual:<anything>" ref, bounded by the SQL's 64-character limit.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("--ref is required: the report code from the email (R-XXXXXX)")
    ref = value.strip()
    upper = ref.upper()
    expected = (f"expected {REPORT_CODE_PREFIX} plus {REPORT_CODE_LENGTH} of "
                f"{REPORT_CODE_ALPHABET} (no 0, O, 1, I or L)")

    bare = _BARE_CODE_RE.match(upper)
    if bare:
        return REPORT_CODE_PREFIX + bare.group(1)

    found = set(_CODE_IN_TEXT_RE.findall(upper))
    if len(found) == 1:
        return REPORT_CODE_PREFIX + found.pop()
    if len(found) > 1:
        raise ValueError(f"{ref!r} holds more than one report code: pass the one being rewarded")

    if upper.startswith(MANUAL_REF_PREFIX.upper()):
        note = ref[len(MANUAL_REF_PREFIX):].strip()
        if not note:
            raise ValueError(f"{ref!r}: say which report after {MANUAL_REF_PREFIX!r}")
        manual = MANUAL_REF_PREFIX + note
        if len(manual) > MAX_REF_LENGTH:
            raise ValueError(f"--ref is {len(manual)} characters; the limit is {MAX_REF_LENGTH}")
        return manual

    if REPORT_CODE_PREFIX in upper:
        raise ValueError(f"{ref!r} is not a report code: {expected}")
    raise ValueError(
        f"{ref!r} is not a report code ({expected}). For a report that arrived without one, "
        f"pass --ref {MANUAL_REF_PREFIX}<a note naming it>"
    )


def validate_credits(value: Any) -> int:
    """1..MAX_CREDITS, the same range the SQL accepts. ValueError otherwise."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"--credits must be a whole number (got {value!r})")
    if not 1 <= value <= MAX_CREDITS:
        raise ValueError(f"--credits must be between 1 and {MAX_CREDITS} (got {value})")
    return value


def prior_reward_lines(rows: Optional[Iterable[Dict[str, Any]]], ref: str) -> Tuple[List[str], bool]:
    """Earlier `feedback_reward` rows → (printable lines, whether `ref` is already among them).

    The SQL's replay guard is what actually stops a double grant; this is so the owner sees it
    BEFORE choosing to `--apply`, along with how often this account has been rewarded.
    """
    lines: List[str] = []
    already = False
    for row in rows or []:
        row_ref = str(row.get("ref_id") or "")
        same = row_ref == ref
        already = already or same
        lines.append(
            f"    #{row.get('id')}  +{row.get('delta')}  {row_ref or '(no ref)'}  "
            f"{row.get('created_at') or ''}" + ("   ← THIS REPORT" if same else "")
        )
    return lines, already


def interpret_outcome(data: Any) -> Tuple[int, str]:
    """The RPC's JSONB result → (exit code, message). Anything unrecognised is a failure.

    'replay' exits 0: the report was rewarded once, which is the state the owner wanted.
    """
    raw, data = data, _unwrap(data)
    if data is None:
        return 1, f"✗ unexpected response from {RPC_NAME}: {raw!r}"
    outcome = data.get("outcome")
    if outcome == "granted":
        return 0, (f"✓ granted {data.get('credits')} credits (never expire) · "
                   f"spendable now {data.get('spendable')}")
    if outcome == "replay":
        return 0, (f"= already rewarded for this report ({data.get('credits')} credits, "
                   f"{data.get('granted_at') or 'earlier'}); nothing changed · "
                   f"spendable {data.get('spendable')}")
    if outcome == "invalid":
        return 1, f"✗ refused by {RPC_NAME}: {data.get('reason') or 'no reason given'}"
    return 1, f"✗ unexpected outcome from {RPC_NAME}: {data!r}"


def _unwrap(data: Any) -> Optional[Dict[str, Any]]:
    """PostgREST returns a JSONB function's result as the object itself; tolerate a one-row list."""
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    return data if isinstance(data, dict) else None


def _is_missing_function(error: Exception) -> bool:
    code = getattr(error, "code", None)
    return code in _MISSING_FUNCTION_CODES or any(c in str(error) for c in _MISSING_FUNCTION_CODES)


# ── entry point ──────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--user-id", help="the Account ID from the report email")
    who.add_argument("--email", help="the account's email (only if the user wrote from it)")
    ap.add_argument("--ref", required=True,
                    help="the report code from the email, e.g. R-7K2Q9M "
                         f"({MANUAL_REF_PREFIX}<note> for a report without one)")
    ap.add_argument("--credits", type=int, default=DEFAULT_CREDITS,
                    help=f"credits to grant (default {DEFAULT_CREDITS}, max {MAX_CREDITS})")
    ap.add_argument("--apply", action="store_true", help="grant (default: dry run, no RPC call)")
    return ap


def main(argv: Optional[List[str]] = None, *, db: Any = None) -> int:
    args = _parser().parse_args(argv)
    try:
        credits = validate_credits(args.credits)
        ref = normalize_ref(args.ref)
        user_id = parse_user_id(args.user_id) if args.user_id is not None else None
    except ValueError as e:
        print(f"✗ {e}")
        return 1
    email = args.email.strip().lower() if args.email is not None else None
    if email == "":
        print("✗ --email is blank")
        return 1

    if db is None:
        # Lazy: importing either needs backend/.env, and the helpers above must not.
        from app.config import settings  # noqa: PLC0415
        from app.database import get_supabase  # noqa: PLC0415

        # Which project this writes to — the one line that tells production from staging.
        print(f"  target: {settings.SUPABASE_URL}")
        db = get_supabase()

    try:
        query = db.table("users").select("id,email,tier,comp_tier")
        query = query.eq("id", user_id) if user_id is not None else query.eq("email", email)
        rows = query.limit(1).execute().data or []
    except Exception as e:  # noqa: BLE001
        print(f"✗ users read failed: {type(e).__name__}: {e}")
        return 1
    if not rows:
        print(f"✗ no public.users row for {user_id or email}")
        return 1
    user = rows[0]
    uid = str(user.get("id") or "")
    if uid == GUEST_USER_ID:
        print("✗ that is the shared guest account; guests cannot be rewarded")
        return 1

    try:
        credit_rows = (db.table("user_credits")
                       .select("total,used,purchased_total,purchased_used")
                       .eq("user_id", uid).limit(1).execute().data or [])
        prior = (db.table("credit_transactions")
                 .select("id,delta,ref_id,created_at")
                 .eq("user_id", uid).eq("reason", REASON)
                 .order("id", desc=True).limit(PRIOR_LIMIT).execute().data or [])
    except Exception as e:  # noqa: BLE001
        print(f"✗ balance / ledger read failed: {type(e).__name__}: {e}")
        return 1

    bal = credit_rows[0] if credit_rows else {}
    monthly = (bal.get("total") or 0) - (bal.get("used") or 0)
    purchased = (bal.get("purchased_total") or 0) - (bal.get("purchased_used") or 0)
    print(f"  account: {user.get('email')} ({uid})")
    print(f"  tier={user.get('tier')} comp_tier={user.get('comp_tier')} · "
          f"monthly={monthly} never-expiring={purchased} spendable={monthly + purchased}"
          + ("" if credit_rows else " (no balance row yet; the grant creates it)"))

    lines, already = prior_reward_lines(prior, ref)
    if lines:
        print(f"  earlier issue report rewards ({len(lines)}, newest first, up to {PRIOR_LIMIT}):")
        print("\n".join(lines))
    else:
        print("  earlier issue report rewards: none")
    if already:
        print(f"  ⚠ {ref} was already rewarded for this account: --apply will report 'replay' "
              "and change nothing")

    print(f"  grant: +{credits} credits for report {ref} (never-expiring pool)")
    if not args.apply:
        print("\nDry run only. Confirm the issue is real, then re-run with --apply to grant.")
        return 0

    try:
        result = db.rpc(RPC_NAME, {"p_user_id": uid, "p_credits": credits, "p_ref_id": ref}).execute()
    except Exception as e:  # noqa: BLE001
        if _is_missing_function(e):
            print(f"✗ {RPC_NAME} does not exist: apply migration 199 first")
        else:
            print(f"✗ {RPC_NAME} failed: {type(e).__name__}: {e}")
        return 1

    data = getattr(result, "data", None)
    code, message = interpret_outcome(data)
    print(f"  {message}")
    if (_unwrap(data) or {}).get("outcome") == "granted":
        print("\n  Now reply to the user's report email: confirm the issue and that the credits are"
              "\n  in their account (Credit History shows them as \"Issue report reward\").")
    return code


if __name__ == "__main__":
    sys.exit(main())
