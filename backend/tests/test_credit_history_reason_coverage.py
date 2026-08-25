"""Anti-drift guard: every `reason` production writes must render as a readable row.

`credit_transactions.reason` is unconstrained text — no CHECK, no enum — written from
three different places (Python endpoints, a seeding script outside `app/`, and eight SQL
functions). The credit history screen maps it to user-facing copy. A reason with no entry
in that map renders as "Credit adjustment", which is not a crash and not a test failure
anywhere else: it is a row a user cannot interpret on the screen they opened *because*
they did not understand their balance.

So this test scans the repo for reason literals and fails when one is not covered.

⚠️ VACUITY IS THE FAILURE MODE HERE. A source scan that stops matching turns every
assertion below green. Three defences:
  1. The Python scan is AST-based, not regex — so it cannot match a reason inside a
     comment or a docstring, and it does not silently stop working when formatting
     changes. (`.claude/rules/testing.md`: strip comments before asserting.)
  2. The SQL scan is bounded to the text of an `INSERT INTO credit_transactions`
     statement, so a literal elsewhere in the file cannot satisfy it.
  3. `test_the_scanners_are_not_vacuous` pins one sentinel per scanner. If a scanner
     breaks, that test fails BEFORE the coverage assertion can pass on an empty set.

Mutation-tested by hand on 2026-08-24: adding `reason="totally_new_reason"` to a
`precharge` call failed `test_every_reason_written_in_python_is_mapped`; adding
`'brand_new_sql_reason'` to a SQL ledger INSERT failed the SQL twin. Both restored.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.services.credit_history_service import (
    KNOWN_REASON_PREFIXES,
    KNOWN_REASONS,
)

_BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: Functions that carry a ledger reason, and where the reason sits when passed
#: positionally (a name means "keyword only").
_REASON_CALLS = {
    "precharge": "reason",          # CreditService.precharge(..., reason=...)
    "refund_ledgered": "reason",    # CreditService.refund_ledgered(..., reason=...)
    "log_transaction": "reason",    # CreditService.log_transaction(..., reason=...)
    "refund_once": 0,               # _ChatQuota.refund_once(reason)  — positional
}

#: A ledger row written as a dict literal (the seeding script does this) is identified by
#: carrying BOTH keys. Requiring both keeps an unrelated dict with a "reason" key out.
_LEDGER_DICT_KEYS = {"reason", "delta"}

#: Shape of a reason, used to separate one from the other quoted literals inside a SQL
#: INSERT (notably `'YYYY-MM'`, the ET month stamp that lands in `ref_id`).
_REASON_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def _string_literal(node: ast.AST) -> str | None:
    """A plain string, or the leading literal run of an f-string.

    The f-string case is the point: `endpoints/chat.py` writes
    `f"chat_degraded_{stream_signals['degraded']}"`, so the only thing statically knowable
    is the prefix `chat_degraded_` — which is exactly what the mapping must cover.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value or None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                break  # stop at the first interpolation — the rest is not static
        return "".join(parts) or None
    return None


def _scan_python() -> dict[str, set[str]]:
    """reason literal → the files that write it. AST-based, so comments cannot match."""
    found: dict[str, set[str]] = {}

    def record(value: str | None, path: pathlib.Path) -> None:
        if value:
            found.setdefault(value, set()).add(str(path.relative_to(_BACKEND)))

    for root in ("app", "scripts"):
        for path in sorted((_BACKEND / root).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    spec = _REASON_CALLS.get(name)
                    if spec is not None:
                        value = None
                        for keyword in node.keywords:
                            if keyword.arg == "reason":
                                value = _string_literal(keyword.value)
                        if value is None and isinstance(spec, int) and len(node.args) > spec:
                            value = _string_literal(node.args[spec])
                        record(value, path)
                elif isinstance(node, ast.Dict):
                    keys = {
                        k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
                    if _LEDGER_DICT_KEYS <= keys:
                        for key, value in zip(node.keys, node.values):
                            if isinstance(key, ast.Constant) and key.value == "reason":
                                record(_string_literal(value), path)
    return found


def _scan_sql() -> dict[str, set[str]]:
    """reason literal → the SQL files that write it.

    Bounded to the text of an `INSERT INTO ... credit_transactions ... ;` statement, so a
    same-named literal elsewhere in the file (e.g. `jsonb_build_object('outcome','granted')`
    a few lines below) cannot satisfy this scan.
    """
    found: dict[str, set[str]] = {}
    sources = [_BACKEND / "database" / "schema_snapshot.sql"]
    sources += sorted((_BACKEND / "database" / "migrations").glob("*.sql"))

    statement = re.compile(r"INSERT\s+INTO\s+(?:public\.)?credit_transactions.*?;", re.S | re.I)
    literal = re.compile(r"'([^']*)'")

    for path in sources:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for stmt in statement.findall(text):
            for candidate in literal.findall(stmt):
                if _REASON_SHAPE.match(candidate):
                    found.setdefault(candidate, set()).add(str(path.relative_to(_BACKEND)))
    return found


def _is_mapped(reason: str) -> bool:
    return reason in KNOWN_REASONS or reason.startswith(KNOWN_REASON_PREFIXES)


# ── the guard ────────────────────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    """Runs FIRST in spirit: if a scanner breaks, coverage passes on an empty set.

    One sentinel per scanning path, so a break in any one of them is caught rather than
    masked by the other three.
    """
    python_found = _scan_python()
    sql_found = _scan_sql()

    # Python, keyword argument on a service call.
    assert "report_charge" in python_found, "the precharge/kwarg scan stopped matching"
    # Python, f-string prefix — the runtime-composed family.
    assert "chat_degraded_" in python_found, "the f-string prefix scan stopped matching"
    # Python, dict literal — and specifically one OUTSIDE app/, which is the easiest to lose.
    assert "tester_grant" in python_found, "the ledger-dict scan stopped matching"
    assert any(
        f.startswith("scripts/") for f in python_found["tester_grant"]
    ), "scripts/ is no longer being scanned"
    # SQL, a literal inside an INSERT statement.
    assert "pack_purchase" in sql_found, "the SQL INSERT scan stopped matching"

    total = set(python_found) | set(sql_found)
    assert len(total) >= 18, (
        f"only {len(total)} reasons found across the repo — the scanners have regressed; "
        "coverage below would pass vacuously"
    )


def test_every_reason_written_in_python_is_mapped():
    unmapped = {
        reason: sorted(files)
        for reason, files in _scan_python().items()
        if not _is_mapped(reason)
    }
    assert not unmapped, (
        "these ledger reasons are written but have no entry in credit_history_service — "
        f"they render to the user as an uninterpretable 'Credit adjustment': {unmapped}"
    )


def test_every_reason_written_in_sql_is_mapped():
    unmapped = {
        reason: sorted(files)
        for reason, files in _scan_sql().items()
        if not _is_mapped(reason)
    }
    assert not unmapped, (
        "these ledger reasons are written by a SQL function but have no entry in "
        f"credit_history_service: {unmapped}"
    )


def test_the_map_has_no_entries_nothing_writes():
    """The other direction: a mapping for a reason production no longer emits is dead
    copy. Not fatal, so this reports rather than being merged into the guard above — but
    it does fail, because a stale entry is how the map stops describing reality.
    """
    written = set(_scan_python()) | set(_scan_sql())
    # A prefix family is written as `chat_degraded_` + a runtime suffix, so the concrete
    # keys it covers never appear literally. Exclude anything a known prefix explains.
    orphans = {
        reason for reason in KNOWN_REASONS
        if reason not in written and not reason.startswith(KNOWN_REASON_PREFIXES)
    }
    assert not orphans, (
        f"credit_history_service maps reasons nothing writes any more: {sorted(orphans)}"
    )


@pytest.mark.parametrize("reason", sorted(KNOWN_REASONS))
def test_no_mapped_reason_is_shadowed_by_a_prefix_family(reason):
    """An exact key that also matches a prefix would be unreachable if lookup order ever
    flipped. Keeping them disjoint means the order is not load-bearing."""
    assert not reason.startswith(KNOWN_REASON_PREFIXES), (
        f"{reason!r} is both an exact key and prefix-matched — make them disjoint"
    )
