"""A signup credit allocation must come from `plan_credits`, never from a literal.

`plan_credits` exists to be the single place a tier's monthly allocation is written down.
Two signup functions ignored it and hardcoded a pricing generation that no longer existed:

    create_user_credits()   CASE NEW.tier WHEN 'free' THEN 3 WHEN 'pro' THEN 25 ...
    handle_new_auth_user()  INSERT INTO user_credits VALUES (NEW.id, 50, 0)

against a live `plan_credits` of 50 / 1200 / 4000. The nested trigger ran first, so the value
that actually landed for a new free account was **3** — against a 20-credit report.

It survived for two pricing changes because `ensure_credit_period()` overwrites `total`
whenever `resets_at IS NULL`, which is exactly what the buggy insert produced. The wrong
number was corrected before almost anyone could read it. "Almost" is the problem:
`users.py` degrades to a RAW read of `user_credits` when the credits RPC raises
`CreditServiceUnavailable`, so one transient blip on a new account's first fetch shows the
user "3 credits". Migration 135 is the fix; this test is what makes the next regression fail
here instead of on someone's first screen.

Scope: the LATEST definition of each function across `backend/database/migrations/` — a
migration is a log, so only the highest-numbered definition describes the live database.

Non-vacuity: `test_detector_rejects_the_historical_buggy_bodies` feeds the two real
pre-135 bodies through the same detector and requires it to REJECT them. Without that, a
detector that silently matched nothing would pass every assertion below and guard nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"

_SEEDING_FUNCTIONS = ("create_user_credits", "handle_new_auth_user")

# `AS $tag$ … $tag$` with any dollar-quote tag: 044 uses `$$`, 135 uses `$function$`.
_FUNC_BODY = (
    r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(?:public\.)?{name}\s*\("
    r".*?AS\s+(?P<tag>\$\w*\$)(?P<body>.*?)(?P=tag)"
)

# The `total` column's value in `INSERT INTO … user_credits (user_id, total, …) VALUES (…)`.
# Second positional value; the first is always the user id.
_CREDITS_INSERT = re.compile(
    r"INSERT\s+INTO\s+(?:public\.)?user_credits\s*\((?P<cols>[^)]*)\)\s*"
    r"VALUES\s*\((?P<vals>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_sql_comments(sql: str) -> str:
    """Drop `-- …` lines and trailing comments.

    Load-bearing here, not hygiene: 135's own header QUOTES the buggy `CASE NEW.tier WHEN
    'free' THEN 3` it removes, and prints the numbers 3 / 25 / 100 / 50 in prose. A scan that
    reads documentation as code would fail on the very migration that fixes the bug.
    """
    out = []
    for raw in sql.splitlines():
        if raw.strip().startswith("--"):
            continue
        out.append(re.sub(r"--.*$", "", raw))
    return "\n".join(out)


def _split_values(vals: str) -> list[str]:
    """Split a VALUES list on top-level commas (nested calls may contain their own)."""
    parts, depth, cur = [], 0, ""
    for ch in vals:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _literal_total_violations(body: str) -> list[str]:
    """Every `user_credits` insert in `body` whose `total` is a hardcoded number.

    Returns reasons, not a bool, so a failure names the offending statement.
    """
    violations = []
    for m in _CREDITS_INSERT.finditer(body):
        cols = [c.strip().lower() for c in m.group("cols").split(",")]
        vals = _split_values(m.group("vals"))
        if "total" not in cols:
            continue
        idx = cols.index("total")
        if idx >= len(vals):
            violations.append(f"cannot align `total` with VALUES in: {m.group(0)[:120]}")
            continue
        total = vals[idx]
        # A bare integer, or a CASE that resolves to bare integers, is the bug.
        if re.fullmatch(r"-?\d+", total):
            violations.append(f"`total` is the literal {total}")
        elif re.search(r"\bTHEN\s+-?\d+\b", total, re.IGNORECASE):
            violations.append(f"`total` is a CASE over literals: {total[:120]}")
    return violations


def _latest_body(name: str) -> tuple[str, str]:
    """(migration filename, function body) for the highest-numbered definition of `name`."""
    pattern = re.compile(_FUNC_BODY.format(name=name), re.IGNORECASE | re.DOTALL)
    found: list[tuple[str, str]] = []
    for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        for m in pattern.finditer(_strip_sql_comments(path.read_text())):
            found.append((path.name, m.group("body")))
    assert found, (
        f"No CREATE OR REPLACE FUNCTION for {name}() found in any migration. Either it was "
        f"renamed (update _SEEDING_FUNCTIONS) or this test has stopped matching anything."
    )
    return found[-1]


@pytest.mark.parametrize("name", _SEEDING_FUNCTIONS)
def test_latest_definition_reads_plan_credits(name: str) -> None:
    source, body = _latest_body(name)
    assert "plan_credits" in body.lower(), (
        f"{name}() in {source} seeds credits without consulting plan_credits. Every allocation "
        f"must resolve from that table — it is the only place the numbers are maintained."
    )


@pytest.mark.parametrize("name", _SEEDING_FUNCTIONS)
def test_latest_definition_has_no_literal_total(name: str) -> None:
    source, body = _latest_body(name)
    violations = _literal_total_violations(body)
    assert not violations, (
        f"{name}() in {source} writes user_credits.total from a hardcoded value: "
        f"{violations}. This is the 3/25/100-vs-50/1200/4000 bug (migration 135)."
    )


def test_create_user_credits_sets_resets_at() -> None:
    """A NULL `resets_at` reads as due-for-reset, which is what masked the bug for months.

    `ensure_credit_period` then overwrites `total` and logs a `monthly_reset` on day one — so
    the ledger's opening entry claims a reset that never happened, and the wrong seed becomes
    invisible instead of loud.
    """
    source, body = _latest_body("create_user_credits")
    assert re.search(r"\bresets_at\b", body, re.IGNORECASE), (
        f"create_user_credits() in {source} does not set resets_at. A fresh row with a NULL "
        f"boundary is treated as due-for-reset by ensure_credit_period, which hides whatever "
        f"value was seeded and mislabels the first ledger row as a monthly_reset."
    )


def test_create_user_credits_skips_the_guest_sentinel() -> None:
    """The shared guest row carries a deliberately huge fixed balance."""
    source, body = _latest_body("create_user_credits")
    assert "00000000-0000-4000-8000-00000000dead" in body.lower(), (
        f"create_user_credits() in {source} does not guard the guest sentinel. "
        f"ensure_credit_period guards it; this must agree, or a re-insert of that users row "
        f"resets the guest bucket to the free-tier allocation."
    )


# ── Non-vacuity ──────────────────────────────────────────────────────────────────────
#
# The two bodies exactly as they were in the live database before migration 135, read from
# `pg_get_functiondef` on 2026-08-14. If the detector cannot see the bug in these, it cannot
# see it anywhere, and every assertion above is decoration.

_PRE_135_CREATE_USER_CREDITS = """
BEGIN
    INSERT INTO user_credits (user_id, total, used)
    VALUES (NEW.id, CASE NEW.tier
        WHEN 'free' THEN 3
        WHEN 'pro' THEN 25
        WHEN 'premium' THEN 100
    END, 0);
    RETURN NEW;
END;
"""

_PRE_135_HANDLE_NEW_AUTH_USER = """
BEGIN
    INSERT INTO public.users (id, email) VALUES (NEW.id, NEW.email)
    ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.user_credits (user_id, total, used)
    VALUES (NEW.id, 50, 0)
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END;
"""


@pytest.mark.parametrize(
    "label, body",
    [
        ("create_user_credits (CASE over literals)", _PRE_135_CREATE_USER_CREDITS),
        ("handle_new_auth_user (bare 50)", _PRE_135_HANDLE_NEW_AUTH_USER),
    ],
)
def test_detector_rejects_the_historical_buggy_bodies(label: str, body: str) -> None:
    assert _literal_total_violations(body), (
        f"The detector did NOT flag the real pre-135 body for {label}. The guard is vacuous — "
        f"fix _literal_total_violations before trusting any other test in this file."
    )
    assert "plan_credits" not in body.lower()
