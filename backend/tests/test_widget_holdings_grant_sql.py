"""Source scan of `216_widget_holdings_grants.sql` — the Holdings widget grant's revocation list.

The migration is applied by the owner, never by the suite, so these are the guards that stop a
plausible edit from quietly turning a security property off:

* the trigger on `auth_refresh_families` that kills a session's grants at logout, refresh reuse and
  sign-out-all — and that can NEVER fail on data (a failing trigger would roll back 214's reuse
  revocation: fail-OPEN for reuse detection);
* ONE liveness predicate, used by the check, push registration and push targeting — never a
  hand-copied eviction rule;
* every UPDATE keyed by `grant_id =` where the spec says so;
* service-role-only grants, the FK cascade, no FK on `sid`;
* the Python callers' RPC argument names equal to the SQL signatures (a drift is a PGRST202 that
  the service would read as "schema missing" — fail closed, but every tile goes dark);
* no Postgres regex repetition count above 255 (`{32,400}` compiles in Python and raises in
  Postgres: found while validating this migration on a scratch cluster).

Comments are stripped before every assertion (the header quotes the statements it describes),
and each guard is mutation-checked in-file: `_MUTATIONS` applies a realistic regression to an
in-memory copy and asserts the scan turns red (testing.md §3).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_SQL = _BACKEND / "database" / "migrations" / "216_widget_holdings_grants.sql"
_SQL_214 = _BACKEND / "database" / "migrations" / "214_auth_refresh_tokens.sql"
_SERVICE = _BACKEND / "app" / "services" / "widget_holdings_grant_service.py"

_FUNCTIONS = (
    "widget_holdings_grant_is_live", "mint_widget_holdings_grant", "check_widget_holdings_grant",
    "revoke_widget_holdings_grant", "note_widget_holdings_head", "widget_push_register",
    "widget_push_targets", "widget_push_settle", "_revoke_widget_grants_for_session",
)


def _strip(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _code(text: str | None = None) -> str:
    return _strip(text if text is not None else _SQL.read_text(encoding="utf-8"))


def _function(code: str, name: str) -> str:
    """`CREATE … FUNCTION public.<name>(` through the end of its `$$ … $$` body."""
    m = re.search(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.{name}\s*\(", code)
    assert m, f"{name} is not defined"
    start = code.index("$$", m.end())
    end = code.index("$$", start + 2)
    return code[m.start():end + 2]


def _signature_params(code: str, name: str) -> list[str]:
    m = re.search(rf"FUNCTION\s+public\.{name}\s*\((.*?)\)\s*RETURNS", code, re.S)
    assert m, name
    return [p.split()[0] for p in m.group(1).split(",") if p.strip()]


def _table(code: str) -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS public\.widget_holdings_grants \((.*?)\n\);",
                  code, re.S)
    assert m, "table definition not found"
    return m.group(1)


# ── the checks, each a function of the SQL text so the mutations can drive it ───────────────

def _check_fk(code: str) -> list[str]:
    t = _table(code)
    bad = []
    if not re.search(r"user_id\s+UUID NOT NULL REFERENCES public\.users\(id\) ON DELETE CASCADE", t):
        bad.append("user_id must cascade from public.users")
    sid = re.search(r"^\s*sid\s+[^\n]*$", t, re.M)
    if not sid or "REFERENCES" in sid.group(0):
        bad.append("sid must have NO FK (families are pruned)")
    if re.search(r"^\s*(key|token|grant_token|raw_key)\s", t, re.M):
        bad.append("a raw key column exists")
    return bad


def _check_constraints(code: str) -> list[str]:
    t = _table(code)
    bad = []
    if "UNIQUE (key_hash)" not in t or "key_hash ~ '^[0-9a-f]{64}$'" not in t:
        bad.append("key_hash unique + 64-hex check")
    if "expires_at <= created_at + interval '31 days'" not in t:
        bad.append("31-day life check")
    if "(revoked_at IS NULL) = (revoke_reason IS NULL)" not in t:
        bad.append("revoked pair check")
    if "(push_token IS NULL) = (push_environment IS NULL)" not in t:
        bad.append("push pair check")
    return bad


def _grant_reasons(code: str) -> set[str]:
    m = re.search(r"revoke_reason IN \((.*?)\)\)", _table(code), re.S)
    return set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()


def _family_reasons() -> set[str]:
    m = re.search(r"auth_refresh_families_revoke_reason_check\s+CHECK \(revoke_reason IS NULL OR "
                  r"revoke_reason IN \((.*?)\)\)", _strip(_SQL_214.read_text()), re.S)
    assert m, "214's family reason CHECK not found"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def _check_trigger(code: str) -> list[str]:
    bad = []
    drop = code.find("DROP TRIGGER IF EXISTS trg_auth_refresh_families_revoke_widget_grants")
    create = code.find("CREATE TRIGGER trg_auth_refresh_families_revoke_widget_grants")
    if drop == -1 or create == -1 or drop > create:
        bad.append("DROP TRIGGER IF EXISTS must precede CREATE TRIGGER")
    trig = code[create:code.index(";", create)] if create != -1 else ""
    if not re.search(r"AFTER UPDATE OF revoked_at ON public\.auth_refresh_families", trig):
        bad.append("AFTER UPDATE OF revoked_at")
    if "FOR EACH ROW" not in trig:
        bad.append("FOR EACH ROW")
    if not re.search(r"WHEN \(OLD\.revoked_at IS NULL AND NEW\.revoked_at IS NOT NULL\)", trig):
        bad.append("the WHEN clause")
    body = _function(code, "_revoke_widget_grants_for_session")
    if re.search(r"\bEXCEPTION\b", body):
        bad.append("the trigger function must have NO EXCEPTION block")
    if not re.search(r"ELSE\s+'session_ended'", body):
        bad.append("CASE … ELSE 'session_ended'")
    if not re.search(r"WHERE g\.sid = NEW\.family_id\s+AND g\.user_id = NEW\.user_id", body):
        bad.append("the trigger must be keyed by the family AND its user")
    if not all(f"{c} = NULL" in body for c in ("push_token", "push_environment", "last_head_ticker")):
        bad.append("the trigger must null the push columns and the head ticker")
    return bad


def _check_predicate_use(code: str) -> list[str]:
    bad = []
    for fn in ("check_widget_holdings_grant", "widget_push_register", "widget_push_targets"):
        if "widget_holdings_grant_is_live(" not in _function(code, fn):
            bad.append(f"{fn} must call the liveness predicate")
    for fn in ("widget_push_register", "widget_push_targets"):
        body = _function(code, fn)
        if re.search(r"password_changed_at|sessions_revoked_at", body):
            bad.append(f"{fn} restates the eviction rule by hand")
    pred = _function(code, "widget_holdings_grant_is_live")
    if "date_trunc('second', GREATEST(u.password_changed_at, u.sessions_revoked_at))" not in pred:
        bad.append("the predicate must floor the later stamp to whole seconds")
    if "to_timestamp(g.minted_iat)" not in pred:
        bad.append("the predicate must compare minted_iat, never created_at")
    if "g.revoked_at IS NULL" not in pred or "g.expires_at > now()" not in pred:
        bad.append("the predicate must check revoked_at and expires_at")
    targets = _function(code, "widget_push_targets")
    if "g.superseded_by IS NULL" not in targets:
        bad.append("superseded grants are never push targets")
    return bad


def _check_keyed_updates(code: str) -> list[str]:
    bad = []
    revoke = _function(code, "revoke_widget_holdings_grant")
    if not re.search(r"WHERE g\.grant_id = p_grant_id\s+AND g\.user_id = p_user_id\s+"
                     r"AND g\.revoked_at IS NULL", revoke):
        bad.append("revoke must be keyed by grant_id AND user_id")
    head = _function(code, "note_widget_holdings_head")
    if not re.search(r"WHERE g\.grant_id = p_grant_id\s+AND g\.revoked_at IS NULL", head):
        bad.append("note_head must be keyed by grant_id")
    settle = _function(code, "widget_push_settle")
    updates = re.findall(r"UPDATE public\.widget_holdings_grants g.*?;", settle, re.S)
    if not updates or not all("g.grant_id = v_gid AND g.push_token = v_tok" in u for u in updates):
        bad.append("every settle UPDATE must be keyed by the grant AND the token sent")
    if "jsonb_array_length(p_outcomes) > 100" not in settle:
        bad.append("settle must refuse more than 100 outcomes")
    check = _function(code, "check_widget_holdings_grant")
    if not re.search(r"WHERE g\.superseded_by = v\.grant_id\s+AND g\.user_id = v\.user_id", check):
        bad.append("the successor's first use revokes only ITS predecessors")
    return bad


def _check_mint(code: str) -> list[str]:
    bad = []
    mint = _function(code, "mint_widget_holdings_grant")
    if "PERFORM public._lock_refresh_sessions(p_user_id);" not in mint:
        bad.append("mint must take 214's per-user lock")
    if "to_timestamp(p_minted_iat) < date_trunc('second', v_stamp)" not in mint:
        bad.append("mint must refuse an evicted iat (whole seconds)")
    if "v_fam.revoked_at IS NOT NULL" not in mint:
        bad.append("mint must refuse a revoked session")
    if not re.search(r"LIMIT 500\s+FOR UPDATE SKIP LOCKED", mint):
        bad.append("the cross-user prune must be bounded and never wait")
    if "LEAST(g.expires_at, v_now + interval '24 hours')" not in mint:
        bad.append("a superseded grant lives at most 24 h more")
    if "g.grant_id <> v_new" not in mint:
        bad.append("the new grant is never capped")
    bad += _check_eviction_sweep(mint)
    return bad


def _null_push(stmt: str) -> bool:
    return all(f"{c} = NULL" in stmt for c in ("push_token", "push_environment", "last_head_ticker"))


def _check_eviction_sweep(mint: str) -> list[str]:
    """D4 (review 2026-11-27): an evicted grant never keeps a holding or a push token. The mint
    revokes every evicted grant of the account, under the lock, before its own evicted RETURN."""
    bad = []
    sweep = next((u for u in re.findall(r"UPDATE public\.widget_holdings_grants g.*?;", mint, re.S)
                  if "date_trunc('second', v_stamp)" in u), None)
    if sweep is None:
        return ["mint must revoke the account's evicted grants"]
    if not re.search(r"WHERE g\.user_id = p_user_id\s+AND g\.revoked_at IS NULL\s+"
                     r"AND to_timestamp\(g\.minted_iat\) < date_trunc\('second', v_stamp\)", sweep):
        bad.append("the eviction sweep must be keyed by the account and compare minted_iat in "
                   "whole seconds")
    if "revoke_reason = 'session_ended'" not in sweep or not _null_push(sweep):
        bad.append("the eviction sweep must revoke 'session_ended' and null the push columns")
    lock = mint.find("PERFORM public._lock_refresh_sessions(p_user_id);")
    at = mint.find(sweep)
    evicted_return = mint.find("RETURN jsonb_build_object('status', 'evicted')")
    if not (0 <= lock < at < evicted_return):
        bad.append("the eviction sweep runs under the lock and before the evicted RETURN")
    return bad


def _check_check_evicts(code: str) -> list[str]:
    """The check revokes an evicted grant the first time it is presented, keyed by grant_id."""
    check = _function(code, "check_widget_holdings_grant")
    m = re.search(r"to_timestamp\(v\.minted_iat\) < date_trunc\('second', v_stamp\) THEN"
                  r"(.*?)RETURN jsonb_build_object\('status', 'evicted'\);", check, re.S)
    if not m:
        return ["the check's evicted branch was not found"]
    branch = m.group(1)
    upd = re.search(r"UPDATE public\.widget_holdings_grants g.*?;", branch, re.S)
    if not upd:
        return ["the check must REVOKE an evicted grant before answering 'evicted'"]
    bad = []
    if not re.search(r"WHERE g\.grant_id = v\.grant_id\s+AND g\.revoked_at IS NULL", upd.group(0)):
        bad.append("the evicted revoke must be keyed by that grant_id")
    if "revoke_reason = 'session_ended'" not in upd.group(0) or not _null_push(upd.group(0)):
        bad.append("the evicted revoke must use 'session_ended' and null the push columns")
    return bad


def _check_settle(code: str) -> list[str]:
    """widget_push_settle's semantics, pinned on the SQL (the lifecycle tests run on the fake)."""
    settle = re.sub(r"\s+", " ", _function(code, "widget_push_settle"))
    bad = []
    if "v_today date := (now() AT TIME ZONE 'America/New_York')::date;" not in settle:
        bad.append("the daily count is keyed by the ET calendar day")
    gone = re.search(r"ELSIF v_out = 'gone' THEN.*?UPDATE public\.widget_holdings_grants g(.*?);",
                     settle)
    if not gone or ("AND (g.push_token_updated_at IS NULL OR g.push_token_updated_at <= v_ts)"
                    not in gone.group(1)):
        bad.append("a 410 clears only a token registered at or before APNs' timestamp")
    if "AND g.push_bad_since < now() - interval '24 hours'" not in settle:
        bad.append("a bad token is cleared only after 24 hours")
    if "WHEN NOT v_count THEN g.push_sent_count" not in settle:
        bad.append("a dry-run accept (count false) stamps without counting")
    if "WHEN g.push_sent_day = v_today THEN g.push_sent_count + 1" not in settle:
        bad.append("the count resets on a new ET day")
    return bad


def _check_grants(code: str) -> list[str]:
    bad = []
    if not re.search(r"ALTER TABLE public\.widget_holdings_grants ENABLE ROW LEVEL SECURITY", code):
        bad.append("RLS")
    if "REVOKE ALL ON public.widget_holdings_grants FROM anon, authenticated;" not in code:
        bad.append("table REVOKE")
    if "GRANT ALL ON public.widget_holdings_grants TO service_role;" not in code:
        bad.append("table GRANT to service_role")
    for fn in _FUNCTIONS:
        body = _function(code, fn)
        if "SECURITY DEFINER" not in body or "SET search_path TO 'public', 'pg_temp'" not in body:
            bad.append(f"{fn}: SECURITY DEFINER + pinned search_path")
        for role in ("PUBLIC", "anon", "authenticated"):
            if not re.search(rf"REVOKE ALL ON FUNCTION public\.{fn}\([^)]*\) FROM {role};", code):
                bad.append(f"{fn}: REVOKE FROM {role}")
        granted = re.search(rf"GRANT EXECUTE ON FUNCTION public\.{fn}\([^)]*\) TO service_role;",
                            code)
        if fn == "_revoke_widget_grants_for_session":
            if granted:
                bad.append("the trigger function gets NO grant")
        elif not granted:
            bad.append(f"{fn}: GRANT EXECUTE TO service_role")
    if not re.search(r"^BEGIN;", code, re.M) or not re.search(r"^COMMIT;", code, re.M):
        bad.append("one transaction")
    return bad


def _check_regex_counts(code: str) -> list[str]:
    """Postgres (ARE) caps a bounded repetition at 255 (RE_DUP_MAX) — in EVERY bounded form:
    `{m}`, `{m,}` and `{m,n}`."""
    return [m.group(0) for m in re.finditer(r"\{(\d+)(?:,(\d*))?\}", code)
            if int(m.group(1)) > 255 or (m.group(2) and int(m.group(2)) > 255)]


_CHECKS = {
    "fk": _check_fk, "constraints": _check_constraints, "trigger": _check_trigger,
    "predicate": _check_predicate_use, "keyed": _check_keyed_updates, "mint": _check_mint,
    "grants": _check_grants, "regex": _check_regex_counts, "check_evicts": _check_check_evicts,
    "settle": _check_settle,
}


@pytest.mark.parametrize("name", sorted(_CHECKS))
def test_the_migration_holds(name):
    assert _CHECKS[name](_code()) == []


def test_every_214_family_reason_is_a_legal_grant_reason():
    """The trigger copies 214's reasons; one missing from 216's CHECK would make the trigger
    fail — and roll back reuse detection. The CASE's ELSE covers a FUTURE 214 reason."""
    family = _family_reasons()
    assert family == {"logout", "reuse", "signout_all"}, family
    assert family <= _grant_reasons(_code())
    assert {"session_ended", "renewed", "cap", "app_revoked", "widget_removed"} <= _grant_reasons(_code())


def test_the_trigger_copies_each_214_reason_explicitly():
    body = _function(_code(), "_revoke_widget_grants_for_session")
    for reason in _family_reasons():
        assert f"WHEN '{reason}' THEN '{reason}'" in body, reason


def test_the_table_is_not_an_unlinked_user_table():
    """It has an FK that cascades; listing it would double-handle deletion."""
    tree = ast.parse((_BACKEND / "app" / "api" / "v1" / "endpoints" / "users.py").read_text())
    tables = None
    for node in tree.body:
        targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
        if any(isinstance(t, ast.Name) and t.id == "_UNLINKED_USER_TABLES" for t in targets):
            tables = ast.literal_eval(node.value)
    assert tables and len(tables) >= 5, "the _UNLINKED_USER_TABLES scan found nothing"
    assert "widget_holdings_grants" not in tables


def test_the_header_records_the_teardown_order_and_the_trigger_dependency():
    raw = _SQL.read_text()
    assert ("switch off (WIDGET_HOLDINGS_GRANTS_ENABLED=false, restart) → revoke-all SQL →" in raw)
    assert "DROP TRIGGER trg_auth_refresh_families_revoke_widget_grants → DROP TABLE" in raw
    assert "REUSE DETECTION NOW RUNS THIS TRIGGER" in raw
    assert re.search(r"Own key, 30-day life \(Recommended\)", raw)


# ── the Python callers match the SQL signatures ─────────────────────────────────────────────

def _python_rpc_params() -> dict[str, set[str]]:
    """{rpc name: the dict-literal keys the service passes} for every `_call(sb, "name", {…})`."""
    tree = ast.parse(_SERVICE.read_text())
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_call" and len(node.args) >= 3
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[2], ast.Dict)):
            found[node.args[1].value] = {k.value for k in node.args[2].keys
                                         if isinstance(k, ast.Constant)}
    return found


#: The RPCs whose ONE Python caller is the push sweeper (`widget_push_service`), never the grant
#: service (review 2026-11-27: a second, drifting reader was removed from it).
_PUSH_RPCS = ("widget_push_targets", "widget_push_settle")
_RPCS = tuple(f for f in _FUNCTIONS
              if f not in ("widget_holdings_grant_is_live", "_revoke_widget_grants_for_session"))


def test_the_service_passes_exactly_each_functions_parameters():
    code = _code()
    py = _python_rpc_params()
    mine = [f for f in _RPCS if f not in _PUSH_RPCS]
    assert set(py) == set(mine), f"the scan found {sorted(py)}"
    for name in mine:
        assert py[name] == set(_signature_params(code, name)), name


def test_the_push_rpcs_have_exactly_one_caller_the_push_sweeper():
    callers: dict[str, set[str]] = {}
    for f, name, _keys in _every_python_rpc_call():
        callers.setdefault(name, set()).add(f)
    assert set(callers) == set(_RPCS), f"a 216 RPC nobody calls, or a new one: {sorted(callers)}"
    for name in _PUSH_RPCS:
        assert callers[name] == {"app/services/widget_push_service.py"}, (name, callers[name])


# ── mutations: each must turn its check red; the control must not ───────────────────────────

_SWEEP_BLOCK = (
    "    IF v_stamp IS NOT NULL THEN\n"
    "        UPDATE public.widget_holdings_grants g\n"
    "           SET revoked_at = v_now, revoke_reason = 'session_ended',\n"
    "               push_token = NULL, push_environment = NULL, last_head_ticker = NULL\n"
    "         WHERE g.user_id = p_user_id\n"
    "           AND g.revoked_at IS NULL\n"
    "           AND to_timestamp(g.minted_iat) < date_trunc('second', v_stamp);\n"
    "    END IF;\n"
)
_EVICTED_RETURN_BLOCK = (
    "    IF v_stamp IS NOT NULL AND to_timestamp(p_minted_iat) < date_trunc('second', v_stamp) THEN\n"
    "        RETURN jsonb_build_object('status', 'evicted');\n"
    "    END IF;\n"
)

_MUTATIONS = [
    ("trigger EXCEPTION block", "trigger",
     "    RETURN NULL;\nEND;", "    RETURN NULL;\nEXCEPTION WHEN OTHERS THEN RETURN NULL;\nEND;", True),
    ("trigger ELSE copies the raw reason", "trigger",
     "ELSE 'session_ended'", "ELSE NEW.revoke_reason", True),
    ("trigger WHEN clause dropped", "trigger",
     "    WHEN (OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL)\n", "", True),
    ("CREATE TRIGGER before its DROP", "trigger",
     "DROP TRIGGER IF EXISTS trg_auth_refresh_families_revoke_widget_grants ON "
     "public.auth_refresh_families;", "", True),
    ("revoke loses grant_id", "keyed",
     "     WHERE g.grant_id = p_grant_id\n       AND g.user_id = p_user_id\n"
     "       AND g.revoked_at IS NULL;\n    GET DIAGNOSTICS v_n = ROW_COUNT;\n\n    RETURN "
     "jsonb_build_object(\n        'status', CASE WHEN v_n > 0 THEN 'revoked'",
     "     WHERE g.user_id = p_user_id\n       AND g.revoked_at IS NULL;\n    GET DIAGNOSTICS "
     "v_n = ROW_COUNT;\n\n    RETURN jsonb_build_object(\n        'status', CASE WHEN v_n > 0 "
     "THEN 'revoked'", True),
    ("note_head loses grant_id", "keyed",
     "     WHERE g.grant_id = p_grant_id\n       AND g.revoked_at IS NULL\n"
     "       AND g.last_head_ticker IS DISTINCT FROM v_ticker;",
     "     WHERE g.revoked_at IS NULL\n       AND g.last_head_ticker IS DISTINCT FROM v_ticker;",
     True),
    ("targets stop calling the predicate", "predicate",
     "       AND public.widget_holdings_grant_is_live(g)\n       AND (p_after",
     "       AND g.revoked_at IS NULL\n       AND (p_after", True),
    ("predicate stops flooring to seconds", "predicate",
     ">= date_trunc('second', GREATEST(u.password_changed_at, u.sessions_revoked_at))",
     ">= GREATEST(u.password_changed_at, u.sessions_revoked_at)", True),
    ("mint skips the 214 lock", "mint",
     "    PERFORM public._lock_refresh_sessions(p_user_id);\n    -- Read AFTER",
     "    -- Read AFTER", True),
    ("an FK on sid", "fk", "    sid                   UUID NOT NULL,",
     "    sid                   UUID NOT NULL REFERENCES public.auth_refresh_families(family_id),",
     True),
    ("a grant to anon", "grants",
     "REVOKE ALL ON FUNCTION public.check_widget_holdings_grant(text) FROM anon;", "", True),
    ("the trigger function granted", "grants",
     "REVOKE ALL ON FUNCTION public._revoke_widget_grants_for_session() FROM authenticated;",
     "REVOKE ALL ON FUNCTION public._revoke_widget_grants_for_session() FROM authenticated;\n"
     "GRANT EXECUTE ON FUNCTION public._revoke_widget_grants_for_session() TO service_role;", True),
    ("a Postgres-illegal regex count", "regex",
     "push_token ~ '^[0-9a-f]+$'", "push_token ~ '^[0-9a-f]{32,400}$'", True),
    ("a Postgres-illegal exact regex count", "regex",
     "push_token ~ '^[0-9a-f]+$'", "push_token ~ '^[0-9a-f]{300}$'", True),
    ("a Postgres-illegal open regex count", "regex",
     "push_token ~ '^[0-9a-f]+$'", "push_token ~ '^[0-9a-f]{256,}$'", True),
    ("CONTROL: a legal exact regex count", "regex",
     "push_token ~ '^[0-9a-f]+$'", "push_token ~ '^[0-9a-f]{64}$'", False),
    ("mint stops sweeping evicted grants", "mint",
     "           AND to_timestamp(g.minted_iat) < date_trunc('second', v_stamp);",
     "           AND false;", True),
    ("the eviction sweep moves after the evicted RETURN", "mint",
     _SWEEP_BLOCK + "\n" + _EVICTED_RETURN_BLOCK, _EVICTED_RETURN_BLOCK + "\n" + _SWEEP_BLOCK, True),
    ("the eviction sweep leaves the head ticker", "mint",
     "           SET revoked_at = v_now, revoke_reason = 'session_ended',\n"
     "               push_token = NULL, push_environment = NULL, last_head_ticker = NULL",
     "           SET revoked_at = v_now, revoke_reason = 'session_ended',\n"
     "               push_token = NULL, push_environment = NULL", True),
    ("the check stops revoking an evicted grant", "check_evicts",
     "             WHERE g.grant_id = v.grant_id\n               AND g.revoked_at IS NULL;\n"
     "            RETURN jsonb_build_object('status', 'evicted');",
     "            RETURN jsonb_build_object('status', 'evicted');", True),
    ("settle loses the 410 fence", "settle",
     "\n               AND (g.push_token_updated_at IS NULL OR g.push_token_updated_at <= v_ts);",
     ";", True),
    ("settle counts UTC days", "settle",
     "(now() AT TIME ZONE 'America/New_York')::date", "(now() AT TIME ZONE 'UTC')::date", True),
    ("settle clears a bad token after an hour", "settle",
     "g.push_bad_since < now() - interval '24 hours'", "g.push_bad_since < now() - interval '1 hour'",
     True),
    ("settle counts a dry run", "settle",
     "                       WHEN NOT v_count THEN g.push_sent_count\n", "", True),
    ("CONTROL: prose in a comment", "trigger",
     "    RETURN NULL;\nEND;", "    RETURN NULL;\n    -- EXCEPTION WHEN OTHERS ELSE NEW.revoke_reason\nEND;",
     False),
]


@pytest.mark.parametrize("label,check,anchor,replacement,must_fail", _MUTATIONS,
                         ids=[m[0] for m in _MUTATIONS])
def test_each_guard_catches_its_mutation(label, check, anchor, replacement, must_fail):
    raw = _SQL.read_text(encoding="utf-8")
    assert anchor in raw, f"mutation anchor drifted: {label}"
    mutated = _code(raw.replace(anchor, replacement, 1))
    assert bool(_CHECKS[check](mutated)) is must_fail, label


def _every_python_rpc_call() -> list[tuple[str, str, set[str]]]:
    """(file, rpc name, dict-literal keys) for EVERY call under app/ that names one of 216's
    functions as a string argument followed by a dict literal — `.rpc(name, {…})`,
    `_call(sb, name, {…})`, `self._rpc(name, {…})`, whoever owns the file."""
    names = set(_FUNCTIONS)
    out = []
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            args = list(node.args)
            for i, arg in enumerate(args[:-1]):
                if (isinstance(arg, ast.Constant) and arg.value in names
                        and isinstance(args[i + 1], ast.Dict)):
                    keys = {k.value for k in args[i + 1].keys if isinstance(k, ast.Constant)}
                    out.append((path.relative_to(_BACKEND).as_posix(), arg.value, keys))
    return out


def test_every_python_caller_of_a_216_function_matches_its_signature():
    """Not only this service: the push sweeper reads targets and settles outcomes too. A drifted
    parameter name is a PGRST202 at run time — read as "not applied", and every push stops."""
    code = _code()
    calls = _every_python_rpc_call()
    assert any(f.endswith("widget_holdings_grant_service.py") for f, _n, _k in calls)
    wrong = [(f, n, sorted(k), _signature_params(code, n)) for f, n, k in calls
             if k != set(_signature_params(code, n))]
    assert not wrong, wrong
