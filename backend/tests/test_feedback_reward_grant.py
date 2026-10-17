"""The issue report reward is granted BY HAND, once per report, and only by the owner.

Profile → Help Us Improve now reads "Report an issue · earn free credits". The app promises
"If we can confirm the issue, we'll add credits to your account", which is true only while the
one path that moves credits is the owner running `scripts/grant_feedback_reward.py --apply`
after confirming a report. Three things are pinned here:

  1. The SQL (migration 199, the LATEST definition of `grant_feedback_reward`): security
     header, guest refusal, the order lock → replay lookup → write, a write that touches only
     the never-expiring pool, an honest ledger split, no revenue row, no client identity.
  2. The ACL: EXECUTE for service_role and nobody else. The function mints credits; open to
     anon it is "200 free credits per invented report code" for anyone with the shipped key.
  3. MANUAL ONLY: no code in app/, no other script and no Swift file can reach it. The day a
     backend route or an iOS endpoint names it, credits can move without the owner's review,
     and the copy becomes false.

Plus the constants the script, the SQL, the backend and the iOS app must agree on.

Source scans, per .claude/rules/testing.md: comments are stripped before every assertion (the
migration's own header and the service's comment block name the function on purpose), and every
scan has an anti-vacuity sentinel that fails first if the scanner stops matching.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

from scripts import grant_feedback_reward as gfr

_BACKEND = Path(__file__).resolve().parents[1]
_MIGRATIONS = _BACKEND / "database" / "migrations"
_M199 = _MIGRATIONS / "199_feedback_reward_credits.sql"
_IOS = _BACKEND.parent / "frontend" / "ios" / "ios"
_FEEDBACK_VIEW = _IOS / "Views" / "Screens" / "FeedbackView.swift"
_APP_INFO = _IOS / "Core" / "Utilities" / "AppInfo.swift"
_SCRIPT_REL = "scripts/grant_feedback_reward.py"
_SERVICE_REL = "app/services/credit_history_service.py"


# ── helpers ──────────────────────────────────────────────────────────────────


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _migration_sources() -> list[tuple[str, str]]:
    files = sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    assert len(files) > 100, f"the migration glob found only {len(files)} files — it has rotted"
    return [(p.name, _strip_sql_comments(p.read_text(encoding="utf-8"))) for p in files]


_FUNC = re.compile(
    r"CREATE\s+(?P<replace>OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?grant_feedback_reward\s*\("
    r"(?P<args>[^)]*)\)(?P<header>.*?)AS\s+(?P<tag>\$\w*\$)(?P<body>.*?)(?P=tag)",
    re.I | re.S,
)


def _latest() -> dict:
    """The LAST definition of grant_feedback_reward in apply order (comments stripped)."""
    found = []
    for name, text in _migration_sources():
        for m in _FUNC.finditer(text):
            found.append({"file": name, "replace": bool(m.group("replace")), "args": m.group("args"),
                          "header": m.group("header"), "body": m.group("body"), "text": text})
    assert found, "no definition of grant_feedback_reward in any migration"
    return found[-1]


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _split_top_level(text: str) -> list[str]:
    """Split on commas outside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _sql_max(body: str) -> int:
    m = re.search(r"_MAX\s+CONSTANT\s+INTEGER\s*:=\s*(\d+)\s*;", body, re.I)
    assert m, "grant_feedback_reward no longer declares `_MAX CONSTANT INTEGER := <n>;`"
    return int(m.group(1))


def _py_code(source: str) -> str:
    """Python source with every `#` comment removed (strings and docstrings kept — stricter)."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type != tokenize.COMMENT:
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source
    return " ".join(out)


def _py_files(root: str) -> list[Path]:
    return sorted((_BACKEND / root).rglob("*.py"))


def _code_only(src: str) -> str:
    """Whole-line `//` comments blanked (tests/test_ios_feedback_flow.py's convention)."""
    return "\n".join("" if line.strip().startswith("//") else line for line in src.splitlines())


def _swift_code(src: str) -> str:
    """Swift with `/* */` blocks, whole-line `//` comments and trailing `//` comments removed.

    A trailing `//` counts as a comment only when an EVEN number of quotes precede it on the
    line, so the `//` inside "https://…" is kept as part of its literal.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in _code_only(src).splitlines():
        for m in re.finditer(r"//", line):
            if line[:m.start()].count('"') % 2 == 0:
                line = line[:m.start()]
                break
        out.append(line)
    return "\n".join(out)


_SWIFT_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


# ── 1. The SQL ───────────────────────────────────────────────────────────────


def test_the_latest_definition_is_199_or_later_and_replaces_in_place():
    latest = _latest()
    assert int(latest["file"][:3]) >= 199, latest["file"]
    assert latest["replace"], "CREATE OR REPLACE only: a plain CREATE after a DROP discards the ACL"
    for name, text in _migration_sources():
        assert not re.search(r"DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?(?:public\.)?grant_feedback_reward",
                             text, re.I), f"{name} drops grant_feedback_reward (the ACL goes with it)"


def test_the_signature_is_uuid_integer_text():
    types = [p.split()[-1].upper() for p in _split_top_level(_latest()["args"])]
    assert types == ["UUID", "INTEGER", "TEXT"]


def test_the_security_header():
    header = _latest()["header"]
    assert re.search(r"RETURNS\s+JSONB", header, re.I)
    assert re.search(r"SECURITY\s+DEFINER", header, re.I)
    assert re.search(r"SET\s+search_path\s*=\s*public\s*,\s*pg_temp", header, re.I)
    assert re.search(r"SET\s+row_security\s*=\s*off", header, re.I)


def test_the_guest_sentinel_and_null_user_are_refused_first():
    body = _latest()["body"]
    assert re.search(r"_GUEST\s+CONSTANT\s+UUID\s*:=\s*'00000000-0000-4000-8000-00000000dead'", body, re.I)
    guard = re.search(r"IF\s+p_user_id\s+IS\s+NULL\s+OR\s+p_user_id\s*=\s*_GUEST\s+THEN\s+RETURN", body, re.I)
    assert guard, "the guest / NULL user refusal is gone"
    assert guard.start() < body.index("p_credits"), "the guest refusal must come before anything else"


def test_the_checks_run_in_the_documented_order():
    """guest → amount → ref → unknown user → lock → replay lookup → write."""
    body = _latest()["body"]
    marks = [
        ("guest", r"p_user_id\s*=\s*_GUEST"),
        ("amount", r"p_credits\s*<\s*1\s+OR\s+p_credits\s*>\s*_MAX"),
        ("ref", r"length\(v_ref\)\s*>\s*64"),
        ("unknown user", r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+public\.users\s+WHERE\s+id\s*=\s*p_user_id"),
        ("lock", r"PERFORM\s+public\.ensure_credit_period\(\s*p_user_id\s*\)"),
        ("replay lookup", r"FROM\s+public\.credit_transactions\s+WHERE\s+user_id\s*=\s*p_user_id\s+"
                          r"AND\s+reason\s*=\s*'feedback_reward'\s+AND\s+ref_id\s*=\s*v_ref"),
        ("write", r"UPDATE\s+public\.user_credits"),
        ("ledger", r"INSERT\s+INTO\s+public\.credit_transactions"),
    ]
    positions = []
    for label, pattern in marks:
        m = re.search(pattern, body, re.I)
        assert m, f"the {label} step is missing from grant_feedback_reward"
        positions.append((m.start(), label))
    assert [label for _p, label in sorted(positions)] == [label for label, _ in marks], (
        "the lookup must run AFTER ensure_credit_period takes the row lock, or two concurrent "
        f"grants of one report both find nothing: {sorted(positions)}"
    )


def test_the_ref_is_trimmed_and_bounded():
    body = _latest()["body"]
    assert re.search(r"v_ref\s*:=\s*NULLIF\(\s*btrim\(\s*p_ref_id\s*\)\s*,\s*''\s*\)", body, re.I)
    assert re.search(r"v_ref\s+IS\s+NULL\s+OR\s+length\(v_ref\)\s*>\s*64", body, re.I)


def test_the_cap_matches_the_script():
    assert _sql_max(_latest()["body"]) == gfr.MAX_CREDITS == 200


def test_the_update_touches_only_the_purchased_total_and_updated_at():
    body = _latest()["body"]
    updates = re.findall(r"UPDATE\s+(?:public\.)?(\w+)\s+SET\s+(.*?)\s+WHERE", body, re.I | re.S)
    assert len(updates) == 1, f"expected exactly one UPDATE, found {len(updates)}"
    table, assignments = updates[0]
    assert table.lower() == "user_credits"
    parsed = {a.split("=", 1)[0].strip().lower(): _norm(a.split("=", 1)[1]) for a in _split_top_level(assignments)}
    assert set(parsed) == {"purchased_total", "updated_at"}, (
        f"the reward may move only the never-expiring pool: {sorted(parsed)}"
    )
    assert parsed["purchased_total"] == "purchased_total + p_credits"


def test_the_ledger_row_records_the_split_with_nothing_granted():
    body = _latest()["body"]
    inserts = re.findall(
        r"INSERT\s+INTO\s+public\.credit_transactions\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*;", body, re.I | re.S)
    assert len(inserts) == 1, f"expected exactly one ledger row, found {len(inserts)}"
    cols = [c.strip().lower() for c in inserts[0][0].split(",")]
    vals = [_norm(v) for v in _split_top_level(inserts[0][1])]
    row = dict(zip(cols, vals))
    assert len(cols) == len(vals)
    assert row["granted_delta"] == "0"
    assert row["purchased_delta"] == "p_credits"
    assert row["delta"] == "p_credits"
    assert row["reason"] == f"'{gfr.REASON}'"
    assert row["ref_id"] == "v_ref"


def test_no_revenue_row_and_no_client_identity():
    body = _latest()["body"]
    assert "credit_purchases" not in body, "a reward is not revenue: no credit_purchases row"
    assert not re.search(r"auth\.uid\s*\(", body, re.I), "service_role only: never a client identity"


def test_the_outcomes_are_granted_replay_invalid():
    body = _latest()["body"]
    outcomes = set(re.findall(r"'outcome'\s*,\s*'(\w+)'", body))
    assert outcomes == {"granted", "replay", "invalid"}, outcomes
    # Every refusal says why.
    for m in re.finditer(r"'outcome'\s*,\s*'invalid'\s*(.)", body):
        assert m.group(1) == ",", "an 'invalid' outcome without a 'reason'"


def test_the_partial_unique_index_backs_the_replay_lookup():
    found = []
    for name, text in _migration_sources():
        for m in re.finditer(
            r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?idx_credit_transactions_feedback_reward_ref\s+"
            r"ON\s+public\.credit_transactions\s*\(\s*user_id\s*,\s*ref_id\s*\)\s*WHERE\s+reason\s*=\s*'feedback_reward'",
            text, re.I):
            found.append(name)
    assert found, "the (user_id, ref_id) partial unique index on feedback_reward rows is gone"


def test_the_sql_detectors_are_not_vacuous():
    """Each structural guard above must reject a hand-broken body."""
    body = _latest()["body"]
    # UPDATE widened to the granted pool.
    widened = body.replace("updated_at      = NOW()", "updated_at = NOW(), total = total + p_credits", 1)
    assert widened != body
    updates = re.findall(r"UPDATE\s+(?:public\.)?(\w+)\s+SET\s+(.*?)\s+WHERE", widened, re.I | re.S)
    assert {a.split("=", 1)[0].strip().lower() for a in _split_top_level(updates[0][1])} != {
        "purchased_total", "updated_at"}
    # The comment stripper really removes a commented-out statement.
    assert "credit_purchases" not in _strip_sql_comments("-- INSERT INTO public.credit_purchases\nSELECT 1;")


# ── 2. The ACL ───────────────────────────────────────────────────────────────


def _acl_statements(kind: str) -> list[tuple[str, str, str]]:
    """(file, arg types, roles) for every GRANT/REVOKE on grant_feedback_reward."""
    verb = "GRANT" if kind == "grant" else "REVOKE"
    joiner = "TO" if kind == "grant" else "FROM"
    pattern = re.compile(
        rf"{verb}\s+[A-Z ,]+?\s+ON\s+FUNCTION\s+(?:public\.)?grant_feedback_reward\s*\(([^)]*)\)\s+{joiner}\s+([^;]+);",
        re.I,
    )
    return [(name, _norm(m.group(1)).upper().replace(" ", ""), _norm(m.group(2)).lower())
            for name, text in _migration_sources() for m in pattern.finditer(text)]


def test_execute_is_granted_to_service_role_and_nobody_else():
    grants = _acl_statements("grant")
    assert grants, "no GRANT on grant_feedback_reward — service_role could not call it"
    for name, args, roles in grants:
        assert args == "UUID,INTEGER,TEXT", f"{name}: GRANT on the wrong signature ({args})"
        assert [r.strip() for r in roles.split(",")] == ["service_role"], (
            f"{name}: grant_feedback_reward granted to {roles!r}; it mints credits, service_role only"
        )


def test_it_is_revoked_from_public_anon_and_authenticated():
    revoked = set()
    for _name, args, roles in _acl_statements("revoke"):
        assert args == "UUID,INTEGER,TEXT", f"REVOKE on the wrong signature ({args})"
        revoked |= {r.strip() for r in roles.split(",")}
    assert {"public", "anon", "authenticated"} <= revoked, revoked


def test_the_acl_detector_is_not_vacuous():
    sample = ("GRANT EXECUTE ON FUNCTION public.grant_feedback_reward(UUID, INTEGER, TEXT) TO anon;\n"
              "-- GRANT EXECUTE ON FUNCTION public.grant_feedback_reward(UUID, INTEGER, TEXT) TO authenticated;\n")
    pattern = re.compile(
        r"GRANT\s+[A-Z ,]+?\s+ON\s+FUNCTION\s+(?:public\.)?grant_feedback_reward\s*\(([^)]*)\)\s+TO\s+([^;]+);", re.I)
    found = [m.group(2).strip() for m in pattern.finditer(_strip_sql_comments(sample))]
    assert found == ["anon"], "the GRANT detector must see a live grant and ignore a commented one"


# ── 3. Manual only ───────────────────────────────────────────────────────────


def _python_mentions(roots: tuple[str, ...], needle: str) -> dict[str, int]:
    """{relative path: count} of `needle` in comment-stripped Python under `roots`."""
    hits = {}
    for root in roots:
        for path in _py_files(root):
            code = _py_code(path.read_text(encoding="utf-8", errors="replace"))
            if needle in code:
                hits[str(path.relative_to(_BACKEND))] = code.count(needle)
    return hits


def test_the_rpc_is_named_only_by_the_owner_script():
    hits = _python_mentions(("app", "scripts"), gfr.RPC_NAME)
    assert _SCRIPT_REL in hits, "anti-vacuity: the scan no longer sees the script's own RPC name"
    assert set(hits) == {_SCRIPT_REL}, (
        f"{sorted(set(hits) - {_SCRIPT_REL})} name {gfr.RPC_NAME}. Credits for an issue report move "
        "ONLY when the owner runs scripts/grant_feedback_reward.py --apply after confirming it"
    )


def test_the_reward_reason_appears_in_app_only_as_its_statement_label():
    hits = _python_mentions(("app",), gfr.REASON)
    assert _SERVICE_REL in hits, "anti-vacuity: the scan no longer sees the statement label"
    assert set(hits) == {_SERVICE_REL}, (
        f"{sorted(set(hits) - {_SERVICE_REL})} mention {gfr.REASON!r}: app/ may only LABEL the row"
    )


def _direct_writes(tree: ast.AST) -> list[str]:
    """PostgREST writes: chained off a builder call (`db.table("x").update(...)`), which is what
    separates one from `sys.path.insert(...)`."""
    return [n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Call)
            and n.func.attr in ("insert", "update", "upsert", "delete")]


def test_the_script_makes_exactly_one_rpc_call_and_it_is_this_one():
    """No other RPC (and no direct balance write) hides in the owner's tool."""
    tree = ast.parse((_BACKEND / _SCRIPT_REL).read_text(encoding="utf-8"))
    rpc_calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "rpc"]
    assert len(rpc_calls) == 1, f"expected one .rpc( call, found {len(rpc_calls)}"
    assert isinstance(rpc_calls[0].args[0], ast.Name) and rpc_calls[0].args[0].id == "RPC_NAME"
    writes = _direct_writes(tree)
    assert writes == [], f"the script writes tables directly ({writes}); only the RPC may move credits"
    # Anti-vacuity: the seed script DOES write user_credits / credit_transactions directly.
    seed = ast.parse((_BACKEND / "scripts" / "seed_testflight_testers.py").read_text(encoding="utf-8"))
    assert "update" in _direct_writes(seed) and "insert" in _direct_writes(seed)


def test_the_python_scanner_is_not_vacuous():
    assert "grant_feedback_reward" in _py_code('db.rpc("grant_feedback_reward", {})\n')
    assert "grant_feedback_reward" not in _py_code("x = 1  # grant_feedback_reward\n")


def _swift_files() -> list[Path]:
    files = sorted(_IOS.rglob("*.swift"))
    if not files:
        pytest.skip(f"{_IOS} not present")
    assert len(files) > 100, f"the Swift glob found only {len(files)} files — it has rotted"
    return files


def test_no_swift_file_names_the_rpc_or_the_reason():
    """The iOS app never grants: no endpoint, no RPC, no ledger reason. It shows the row the
    server labels, like every other grant."""
    offenders = []
    for path in _swift_files():
        code = _swift_code(path.read_text(encoding="utf-8", errors="replace"))
        if "feedback_reward" in code:            # also covers grant_feedback_reward
            offenders.append(str(path.relative_to(_IOS)))
    assert offenders == [], f"Swift code names the reward RPC or reason: {offenders}"


def test_the_swift_stripper_is_not_vacuous():
    sample = ('let a = "grant_feedback_reward"\n'
              '// grant_feedback_reward in a comment\n'
              'let u = "https://x.test/feedback_reward" // trailing feedback_reward\n'
              '/* block\n feedback_reward */\n')
    code = _swift_code(sample)
    assert code.count("feedback_reward") == 2, code    # the two literals survive, comments do not
    endpoint = (_IOS / "Core" / "Services" / "APIEndpoint.swift")
    if endpoint.exists():
        assert "/api/v1/users/me/credits/history" in _swift_code(endpoint.read_text(encoding="utf-8"))


def test_no_api_endpoint_literal_mentions_a_reward():
    endpoints = sorted(_IOS.rglob("APIEndpoint.swift"))
    if not endpoints:
        pytest.skip("APIEndpoint.swift not present")
    literals = []
    for path in endpoints:
        literals += _SWIFT_LITERAL.findall(_swift_code(path.read_text(encoding="utf-8")))
    # Anti-vacuity: the extractor sees the credit statement's real path.
    assert "/api/v1/users/me/credits/history" in literals, "the literal extractor stopped matching"
    assert len(literals) > 50
    offenders = [lit for lit in literals if "reward" in lit.lower()]
    assert offenders == [], f"an iOS endpoint names a reward: {offenders}"


def test_the_feedback_screen_never_talks_to_the_backend():
    """The report is an email the USER sends. A network call from this screen is the first step
    toward an automatic grant, so none of the app's networking types may appear here."""
    if not _FEEDBACK_VIEW.exists():
        pytest.skip(f"{_FEEDBACK_VIEW} not present")
    code = _code_only(_FEEDBACK_VIEW.read_text(encoding="utf-8"))
    assert "MailComposeView" in code, "anti-vacuity: this is no longer the feedback screen's code"
    for forbidden in ("APIClient", "APIEndpoint", "URLSession"):
        assert forbidden not in code, f"FeedbackView uses {forbidden}"


# ── 4. One value, everywhere ─────────────────────────────────────────────────


def test_the_script_and_the_sql_agree_on_the_names():
    latest = _latest()
    assert gfr.RPC_NAME == "grant_feedback_reward"
    assert f"'{gfr.REASON}'" in latest["body"]
    assert gfr.DEFAULT_CREDITS == 20 and 1 <= gfr.DEFAULT_CREDITS <= gfr.MAX_CREDITS


def test_the_reason_is_labelled_on_the_statement():
    from app.services.credit_history_service import KIND_GRANT, KNOWN_REASONS, describe_reason

    assert gfr.REASON in KNOWN_REASONS
    assert describe_reason(gfr.REASON)[:2] == (KIND_GRANT, "Issue report reward")


def test_the_script_guest_sentinel_is_the_backends():
    from app.dependencies import GUEST_USER_ID

    assert gfr.GUEST_USER_ID == GUEST_USER_ID
    assert f"'{GUEST_USER_ID}'" in _latest()["body"], "the SQL's _GUEST is a different id"


def test_the_report_code_alphabet_is_the_apps():
    if not _APP_INFO.exists():
        pytest.skip(f"{_APP_INFO} not present")
    code = _code_only(_APP_INFO.read_text(encoding="utf-8"))
    m = re.search(r'reportCodeAlphabet\s*=\s*"([A-Z0-9]+)"', code)
    assert m, "AppInfo.swift no longer declares reportCodeAlphabet as one literal"
    assert m.group(1) == gfr.REPORT_CODE_ALPHABET, (
        "the script would refuse (or mis-normalise) codes the app generates"
    )
    assert '"R-"' in code, "the app's report code no longer starts with R-"
    assert gfr.REPORT_CODE_PREFIX == "R-" and gfr.REPORT_CODE_LENGTH == 6


def test_the_alphabet_leaves_out_every_character_that_reads_as_another():
    alphabet = gfr.REPORT_CODE_ALPHABET
    assert not set("0O1IL") & set(alphabet)
    assert len(set(alphabet)) == len(alphabet) == 31
    assert alphabet == "".join(sorted(alphabet, key=lambda c: (c.isdigit(), c)))
