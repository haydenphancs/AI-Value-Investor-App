"""The stake finder's store (``app/services/trillion_club/stake_store.py``) against the in-memory
database (``tests/_trillion_club_stake_fakes.py``), and that database against migration 205.

WHY THIS FILE EXISTS
--------------------
The store is the finder's and the taps' ONLY Supabase I/O, and the fake is what the finder's
flow tests (crash points, races, reconcile) run on. Each has a way to make a broken write
order look green:

* a fence the store drops (``published=false``, ``withdrawn_at IS NULL`` …) re-publishes a row
  the owner withdrew — so every fence is pinned twice: the exact filter set each statement
  sends, and a row state where that fence alone refuses the write;
* a write retried after a lost response reads as a false conflict or a lost race — so writes
  are proven single-shot (source AST + the call log) and every write is run through "applied,
  then the answer was lost" (→ success) and "refused before it ran" (→ StakeStoreError);
* PostgREST answers at most ~1,000 rows whatever ``.limit()`` asks — so table reads are proven
  to page past a server clamp and to raise at their ceiling instead of returning a sample;
* a fake that accepts what Postgres refuses — so the fake replays migration 205's own probe
  catalogue (the one ``test_trillion_club_stake_migration.py`` runs in Postgres) and must end
  every probe the way Postgres does, and the store's pre-I/O rule functions must agree with
  the migration's Python model on every insert probe.

Hermetic: no Supabase, no network. ``READ_BACKOFF_SECONDS`` is zeroed per test.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from postgrest.exceptions import APIError

import _trillion_club_stake_fakes as F
import test_trillion_club_stake_migration as M
from _migration_sql import set_items
from _trillion_club_stake_fakes import COMPANIES, FILINGS, FINDINGS, STAKES, FakeMisuse, FakeSupabase
from app.services.trillion_club import stake_contracts as c
from app.services.trillion_club import stake_store as SS
from app.services.trillion_club.stake_store import StakeConflict, StakeStore, StakeStoreError
from app.utils import supabase_errors

BACKEND = Path(__file__).resolve().parents[1]
STORE_SRC = Path(SS.__file__)

NOW = datetime(2026, 10, 28, 22, 30, 0, 123456, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=7)
TODAY = date(2026, 10, 28)
SEC_URL = ("https://www.sec.gov/Archives/edgar/data/1045810/000104581026000099/"
           "xslSCHEDULE_13G_X01/primary_doc.xml")
ACC = "0001045810-26-000099"
NVIDIA_CIK = "0001045810"


def acc(n: int) -> str:
    return f"0001045810-26-{n:06d}"


# ── fixtures and builders ────────────────────────────────────────────────────────────────

_BASE: Dict[str, Any] = {}


def _base_snapshot() -> Dict[str, List[Dict[str, Any]]]:
    """The real registry + the real 132 seed stakes, loaded once and restored per test."""
    if "snap" not in _BASE:
        _BASE["snap"] = F.make_fake().snapshot()
    return _BASE["snap"]


@pytest.fixture(autouse=True)
def _fast_reads(monkeypatch):
    monkeypatch.setattr(SS, "READ_BACKOFF_SECONDS", 0.0)


@pytest.fixture
def fake() -> FakeSupabase:
    f = FakeSupabase()
    f.restore(_base_snapshot())
    yield f
    assert f.misuse == [], f"the store used something the fake does not model: {f.misuse}"


@pytest.fixture
def store(fake) -> StakeStore:
    return StakeStore(fake)


def finding_row(**over: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "company_slug": "nvidia", "source": c.SOURCE_SCH13, "accession": ACC, "filer_cik": NVIDIA_CIK,
        "investee_cik": "0009999901", "form": "SCHEDULE 13G", "filed_on": "2026-10-05",
        "document": "primary_doc.xml", "source_url": SEC_URL, "tier": c.TIER_13DG,
        "outcome": c.OUTCOME_AUTO, "reason": "plain_13dg",
        "proposed_row": {"investee_name": "Probe Holdings"}, "fact_sha": "a" * 64,
    }
    row.update(over)
    return row


def stake_row(finding_id: str, **over: Any) -> Dict[str, Any]:
    """A valid unpublished phase-1 auto row (migration 205's own probe row), as insert_stake takes it."""
    base = M._stake(finding_id=finding_id)
    row = {k: base[k] for k in (*SS.STAKE_COLUMNS, "origin", "finding_id", "investee_cik")}
    row.update(over)
    return row


async def new_finding(store: StakeStore, **over: Any) -> Dict[str, Any]:
    row, created = await store.insert_finding(finding_row(**over), now=NOW)
    assert created
    return row


async def new_stake(store: StakeStore, finding: Dict[str, Any], **over: Any) -> Dict[str, Any]:
    return await store.insert_stake(stake_row(finding["id"], **over), now=NOW)


async def live_auto(store: StakeStore, fake: FakeSupabase, **over: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    f = await new_finding(store, accession=over.pop("accession", ACC))
    s = await new_stake(store, f, **over)
    assert await store.publish_stake(s["id"], f["id"], now=NOW)
    return f, fake.stake(s["id"])


def seed_stake(fake: FakeSupabase, slug: str = "nvidia") -> Dict[str, Any]:
    return next(r for r in fake.rows(STAKES) if r["company_slug"] == slug and r["origin"] == "seed")


def last_write(fake: FakeSupabase, table: str, op: str) -> F.FakeCall:
    return fake.writes(table, op)[-1]


def filter_set(call: F.FakeCall) -> set:
    return {(o, col, json.dumps(v, sort_keys=True, default=str)) for o, col, v in call.filters}


# ═════════════════════════════════════════════════════════════════════════════════════════
# 1. Conventions: table constants, no .rpc(), reads retried, writes single-shot
# ═════════════════════════════════════════════════════════════════════════════════════════


def _tree() -> ast.Module:
    return ast.parse(STORE_SRC.read_text(encoding="utf-8"))


def _name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _through_table(call: ast.Call) -> bool:
    cur = call.func.value if isinstance(call.func, ast.Attribute) else None
    while isinstance(cur, (ast.Call, ast.Attribute)):
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute) and cur.func.attr == "table":
            return True
        cur = cur.func if isinstance(cur, ast.Call) else cur.value
    return False


def test_table_constants_are_plain_assignments_and_every_table_call_names_one():
    tree = _tree()
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
              for t in n.targets if isinstance(t, ast.Name)}
    assert {k: consts[k] for k in ("STAKES_TABLE", "FINDINGS_TABLE", "FILINGS_TABLE")} == {
        "STAKES_TABLE": STAKES, "FINDINGS_TABLE": FINDINGS, "FILINGS_TABLE": FILINGS}
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "table":
            assert len(n.args) == 1 and isinstance(n.args[0], ast.Name), ast.unparse(n)
            assert consts.get(n.args[0].id, "").startswith("trillion_club_"), ast.unparse(n)
            used.add(consts[n.args[0].id])
        assert not (isinstance(n, ast.Attribute) and n.attr == "rpc"), "the store must never call .rpc()"
    # Companies are read by TrillionClubStore, never here.
    assert used == {STAKES, FINDINGS, FILINGS}


def _isolation_scan():
    """The package's real isolation scan, lifted from test_trillion_club_jobs.py by AST (that
    file is edited by another agent; importing it would also import the 13F preview script)."""
    names = {"_isolation_problems", "_FORBIDDEN_MODULE_PREFIXES", "_FORBIDDEN_MODULE_WORDS",
             "_ALLOWED_FROM_NOTIFICATION_JOBS", "_FORBIDDEN_TABLE_WORDS"}
    tree = ast.parse((BACKEND / "tests" / "test_trillion_club_jobs.py").read_text(encoding="utf-8"))
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    found = {n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id for n in keep}
    assert found == names, f"test_trillion_club_jobs.py no longer defines {sorted(names - found)}"
    module = ast.fix_missing_locations(ast.Module(body=[ast.Import(names=[ast.alias("ast")]), *keep],
                                                  type_ignores=[]))
    ns: Dict[str, Any] = {}
    exec(compile(module, "test_trillion_club_jobs.py", "exec"), ns)
    return ns["_isolation_problems"]


def test_the_packages_isolation_scan_passes_on_the_store_and_is_not_vacuous_here():
    scan = _isolation_scan()
    src = STORE_SRC.read_text(encoding="utf-8")
    assert scan(src, "stake_store.py") == []
    for mutant in (src.replace(".table(STAKES_TABLE)", ".table(name)", 1),
                   src.replace('STAKES_TABLE = "trillion_club_stakes"', 'STAKES_TABLE = "stakes"'),
                   src.replace("self._db().table(FINDINGS_TABLE).insert(payload)",
                               "self._db().rpc('x', {}).insert(payload)")):
        assert mutant != src and scan(mutant, "mutant.py"), "the scan accepted a mutant"


def test_reads_are_retried_and_writes_are_single_shot_in_the_source():
    tree = _tree()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    owners = {}
    for fname, f in funcs.items():
        for n in ast.walk(f):
            owners.setdefault(id(n), fname)
    retry = [owners[id(n)] for n in ast.walk(tree) if isinstance(n, ast.Call) and _name(n.func) == "retry_idempotent_async"]
    assert retry == ["_read"], "the retry helper may only wrap the one SELECT runner"
    executes = [owners[id(n)] for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "execute"]
    assert executes == ["_read"], "every write goes through sb_exec, never a bare .execute()"
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("_read", "_read_all"):
            bad = [m.attr for a in n.args for m in ast.walk(a)
                   if isinstance(m, ast.Attribute) and m.attr in ("insert", "update", "delete", "upsert")]
            assert bad == [], f"a write handed to the retried read path: {ast.unparse(n)[:120]}"
    def sends(f: ast.AST, name: str) -> bool:
        return any(isinstance(x, ast.Call) and _name(x.func) == name for x in ast.walk(f))

    writers = []
    for fname, f in funcs.items():
        builds = [n for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ("insert", "update", "delete", "upsert") and _through_table(n)]
        if builds:
            writers.append(fname)
            # the statement goes out through sb_exec, directly or via the one stake-write runner
            assert sends(f, "sb_exec") or sends(f, "_stake_write"), fname
            assert not sends(f, "retry_idempotent_async"), fname
            assert not any(_name(b.func) in ("upsert", "delete") for b in builds), fname
    assert sends(funcs["_stake_write"], "sb_exec") and not sends(funcs["_stake_write"], "retry_idempotent_async")
    assert len(writers) == 11, sorted(writers)


@pytest.mark.asyncio
async def test_reads_run_inside_the_retry_helper_and_every_write_runs_once_through_sb_exec(fake, monkeypatch):
    retried: List[str] = []
    sent: List[str] = []
    real_retry, real_exec = SS.retry_idempotent_async, SS.sb_exec

    async def retry_spy(op, **kw):
        retried.append(kw["what"])
        return await real_retry(op, **kw)

    async def exec_spy(query):
        sent.append(query._stmt.op)
        return await real_exec(query)

    monkeypatch.setattr(SS, "retry_idempotent_async", retry_spy)
    monkeypatch.setattr(SS, "sb_exec", exec_spy)
    store = StakeStore(fake)
    f, s = await live_auto(store, fake)
    await store.update_finding(f["id"], expected_status="new", changes={"status": "published"}, now=NOW)
    await store.keep_stake(s["id"], now=NOW)
    await store.bump_verified_on(s["id"], f["id"], TODAY, now=NOW)
    await store.unpublish_auto(s["id"], now=NOW)
    await store.withdraw_stakes_for_finding(f["id"], now=NOW)
    await store.read_stakes()
    await store.read_findings_by_status(["published"])
    assert sent == [call.op for call in fake.writes()] and len(sent) == 8
    assert "select" not in sent
    assert len(retried) == len(fake.reads()) >= 4


@pytest.mark.asyncio
async def test_a_transient_write_error_is_never_replayed_but_a_transient_read_error_is(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    fake.clear_calls()
    fake.fail(STAKES, "update", when="before")     # a 520: transient for supabase_errors
    with pytest.raises(StakeStoreError) as info:
        await store.publish_stake(s["id"], f["id"], now=NOW)
    assert supabase_errors.is_transient_supabase_error(info.value.cause)
    assert [w.op for w in fake.writes()] == ["update"], "a write must be sent exactly once"
    assert fake.stake(s["id"])["published"] is False
    fake.clear_calls()
    fake.fail(STAKES, "select", when="before", times=2)
    assert (await store.read_stake(s["id"]))["id"] == s["id"]
    assert [c_.outcome for c_ in fake.reads()] == ["failed_before", "failed_before", "ok"]


def test_the_store_resolves_its_client_lazily(monkeypatch):
    import app.database as database

    asked: List[int] = []
    monkeypatch.setattr(database, "get_supabase", lambda: asked.append(1) or FakeSupabase())
    store = StakeStore()
    assert asked == []
    assert asyncio.run(store.read_finding(str(uuid.uuid4()))) is None
    assert asked == [1]
    asyncio.run(store.read_finding(str(uuid.uuid4())))
    assert asked == [1]


def test_build_complete_is_the_builders():
    from app.services.trillion_club.builder import BUILD_COMPLETE

    assert SS.BUILD_COMPLETE == BUILD_COMPLETE


# ═════════════════════════════════════════════════════════════════════════════════════════
# 2. Column lists and the fake's schema equal migrations 175 + 205
# ═════════════════════════════════════════════════════════════════════════════════════════

M175 = BACKEND / "database" / "migrations" / "175_trillion_club.sql"
_SQL_TYPES = {"TEXT": "text", "TEXT[]": "text[]", "UUID": "uuid", "BOOLEAN": "bool", "DOUBLE PRECISION": "float8",
              "INTEGER": "int4", "BIGINT": "int8", "DATE": "date", "TIMESTAMPTZ": "timestamptz", "JSONB": "jsonb"}
_COLUMN_RE = re.compile(
    r"^ {4}(\w+)\s+(TEXT\[\]|TEXT|UUID|BOOLEAN|DOUBLE\s+PRECISION|INTEGER|BIGINT|DATE|TIMESTAMPTZ|JSONB)(?![\w\[])"
    r"(.*?)(?=^ {4}\S|\Z)", re.M | re.S)


def _sql_default(text: Optional[str], col_type: str) -> Any:
    if text is None:
        return None
    text = text.rstrip(",")
    if text.upper() == "NOW()":
        return F._NOW_DEFAULT
    if text == "gen_random_uuid()":
        return F._UUID_DEFAULT
    if text.upper() in ("TRUE", "FALSE"):
        return text.upper() == "TRUE"
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    literal = re.fullmatch(r"'(.*)'(?:::jsonb)?", text)
    assert literal, text
    if col_type == "text[]":
        assert literal.group(1) == "{}", text
        return []
    if col_type == "jsonb":
        return json.loads(literal.group(1))
    return literal.group(1)


def _175_columns(table: str) -> List[Tuple[str, str, bool, Any]]:
    sql = M175.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", sql, re.S)
    assert m, table
    block = re.sub(r"--[^\n]*", "", m.group(1))
    out = []
    for name, typ, rest in _COLUMN_RE.findall(block):
        typ = _SQL_TYPES[" ".join(typ.split())]
        d = re.search(r"\bDEFAULT\s+(\S+)", rest)
        out.append((name, typ, "NOT NULL" in rest or "PRIMARY KEY" in rest, _sql_default(d.group(1) if d else None, typ)))
    return out


def _spec(col: F.Column) -> Tuple[str, str, bool, Any]:
    return col.name, col.type, col.not_null, col.default


def _205_spec(name: str, spec: Tuple[str, bool, Optional[str]]) -> Tuple[str, str, bool, Any]:
    typ = _SQL_TYPES[spec[0]]
    return name, typ, spec[1], _sql_default(spec[2], typ)


def test_the_store_column_lists_are_the_tables_columns():
    assert SS.STAKE_READ_COLUMNS == SS.STAKE_COLUMNS + SS.STAKE_EXTRA_COLUMNS
    assert len(set(SS.STAKE_READ_COLUMNS)) == len(SS.STAKE_READ_COLUMNS)
    stakes = [n for n, *_ in _175_columns(STAKES)] + list(M._STAKE_ADDS)
    assert set(SS.STAKE_READ_COLUMNS) == set(stakes), "a stakes column the store never reads (or a phantom)"
    assert SS.FINDING_COLUMNS == tuple(M.ledger_columns(M._sql())), "FINDING_COLUMNS is 205's list, in file order"


def test_the_fake_schema_is_the_migrations_column_for_column():
    for table in (COMPANIES, FILINGS):
        assert [_spec(col) for col in F.SCHEMA[table]] == _175_columns(table), table
    stakes = _175_columns(STAKES) + [_205_spec(n, s) for n, s in M._STAKE_ADDS.items()]
    assert [_spec(col) for col in F.SCHEMA[STAKES]] == stakes
    assert [_spec(col) for col in F.SCHEMA[FINDINGS]] == [_205_spec(n, s) for n, s in M._LEDGER.items()]
    # the parser is not vacuous
    assert len(_175_columns(COMPANIES)) == 29 and len(_175_columns(STAKES)) == 24 and len(_175_columns(FILINGS)) == 16


def test_the_fake_names_the_keys_and_foreign_keys_postgres_does():
    pg_named = {"trillion_club_stake_findings_company_slug_accession_key", "trillion_club_stake_findings_company_slug_fkey",
                "trillion_club_stake_findings_stake_id_fkey", "trillion_club_stake_findings_pkey"}
    fake_named = {n for n, _ in F.UNIQUES[FINDINGS]} | {fk[0] for fk in F.FOREIGN_KEYS[FINDINGS]}
    assert fake_named == pg_named
    assert ("trillion_club_stake_natural_key", ("company_slug", "investee_name", "kind")) in F.UNIQUES[STAKES]
    assert dict((fk[1], fk[4]) for fk in F.FOREIGN_KEYS[FINDINGS]) == {"company_slug": "cascade", "stake_id": "set null"}


# ═════════════════════════════════════════════════════════════════════════════════════════
# 3. The fake ends migration 205's probe catalogue the way Postgres does
# ═════════════════════════════════════════════════════════════════════════════════════════


def _probe_base() -> FakeSupabase:
    """175 → the real seed → 205 → the probe fixture rows (S1-S4 / F1-F4), as the pg fixture."""
    fake = F.make_fake()
    for stmt in M._fixture_rows().split(";\n"):
        stmt = stmt.strip()
        if stmt in ("", "BEGIN", "COMMIT;"):
            continue
        if stmt.startswith("INSERT"):
            fake.admin_insert(*M._insert_doc(stmt))
            continue
        m = re.fullmatch(r"UPDATE public\.(\w+) SET stake_id = '([^']+)' WHERE id = '([^']+)'", stmt)
        assert m, stmt
        fake.admin_update(m.group(1), {"id": m.group(3)}, {"stake_id": m.group(2)})
    return fake


def _sql_value(expr: str, fake: FakeSupabase) -> Any:
    expr = expr.strip()
    if expr == "NULL":
        return None
    if expr == "now()":
        return fake.now_text()
    if expr in ("true", "false"):
        return expr == "true"
    if expr.startswith("'"):
        return expr[1:-1].replace("''", "'")
    return float(expr)


def _run_probe(fake: FakeSupabase, stmt: str) -> Tuple:
    """A probe statement through the fake's PostgREST surface → its verdict."""
    try:
        parsed = M._insert_doc(stmt)
        if parsed is not None:
            return ("ACCEPT", len(fake.table(parsed[0]).insert(parsed[1]).execute().data))
        m = re.fullmatch(r"UPDATE public\.(\w+) SET (.*?) WHERE (.*)", stmt, re.S)
        assert m, stmt
        table, sets, where = m.groups()
        q = fake.table(table).update({k: _sql_value(v, fake) for k, v in set_items(sets).items()})
        if where == M._FIRST_SEED:
            first = sorted((r for r in fake.rows(STAKES) if r["origin"] == "seed" and r["published"]),
                           key=lambda r: (r["company_slug"], r["investee_name"], r["kind"]))[0]
            q = q.eq("id", first["id"])
        else:
            for clause in where.split(" AND "):
                eq = re.fullmatch(r"(\w+) = (.+)", clause)
                inn = re.fullmatch(r"(\w+) IN \((.*)\)", clause)
                assert eq or inn, clause
                q = q.eq(eq.group(1), _sql_value(eq.group(2), fake)) if eq else \
                    q.in_(inn.group(1), [_sql_value(v, fake) for v in inn.group(2).split(",")])
        return ("ACCEPT", len(q.execute().data))
    except APIError as e:
        return ("REJECT", e.code, F.error_constraint(e))


def test_the_fake_ends_every_probe_of_migration_205_the_way_postgres_does():
    base = _probe_base()
    snap = base.snapshot()
    wrong = []
    for tag, stmt, want in M._ALL_PROBES:
        base.restore(snap)
        got = _run_probe(base, stmt)
        expect = ("ACCEPT", want[1]) if want[0] == "ACCEPT" else ("REJECT", want[1], want[2])
        if got != expect:
            wrong.append(f"{tag}: expected {expect}, the fake said {got}")
    assert wrong == []
    kinds = {w[0] for _t, _s, w in M._ALL_PROBES} | {w[1] for _t, _s, w in M._ALL_PROBES if w[0] != "ACCEPT"}
    assert {"ACCEPT", "REJECT", "NOTNULL", "23514", "23505", "23503", "23502"} <= kinds
    assert len(M._ALL_PROBES) >= 190 and base.misuse == []


def test_the_probe_replay_is_not_vacuous(monkeypatch):
    """A fake that skips the 205 rules, or the stake_id FK, gets probes wrong."""
    base = _probe_base()
    snap = base.snapshot()
    by_tag = {t: (s, w) for t, s, w in M._ALL_PROBES}
    real = F.check_violations
    with monkeypatch.context() as mp:
        mp.setattr(F, "check_violations",
                   lambda table, row: [v for v in real(table, row) if not v.startswith("trillion_club_stake_")])
        base.restore(snap)
        assert _run_probe(base, by_tag["auto-background"][0]) == ("ACCEPT", 1)
    base.restore(snap)
    assert _run_probe(base, by_tag["auto-background"][0]) == ("REJECT", "23514", "trillion_club_stake_machine_shape")
    assert _run_probe(base, by_tag["finding-unknown-stake"][0]) == (
        "REJECT", "23503", "trillion_club_stake_findings_stake_id_fkey")


def test_the_store_rule_functions_agree_with_the_migrations_model_on_every_insert_probe():
    checked, wrong = 0, []
    for tag, stmt, _want in M._ALL_PROBES:
        parsed = M._insert_doc(stmt)
        if parsed is None:
            continue
        table, doc = parsed
        mine = SS.stake_check_violations(doc) if table == STAKES else SS.finding_check_violations(doc)
        checked += 1
        if set(mine) != M.model_violations(table, doc):
            wrong.append(f"{tag}: store {sorted(mine)} vs model {sorted(M.model_violations(table, doc))}")
    assert wrong == [] and checked >= 170


def test_the_store_refuses_before_io_exactly_what_205_refuses():
    """insert_finding's payload builder, on every finding probe: refused ⇔ 205 refuses. The ONE
    deliberate difference: an over-long last_error is clipped, never refused (a long exception
    must not make the write that records it fail)."""
    wrong = []
    for tag, stmt, _want in M._ALL_PROBES:
        parsed = M._insert_doc(stmt)
        if parsed is None or parsed[0] != FINDINGS:
            continue
        refused_by_205 = bool(M.model_violations(FINDINGS, parsed[1]))
        try:
            payload = SS._finding_insert_payload(parsed[1])
            refused = False
        except ValueError:
            refused = True
        if tag == "finding-error-long":
            assert refused_by_205 and not refused and len(payload["last_error"]) == 2000
            assert M.model_violations(FINDINGS, payload) == set()
            continue
        if refused != refused_by_205:
            wrong.append(f"{tag}: store refused={refused}, 205 refuses={refused_by_205}")
    assert wrong == []


@pytest.mark.parametrize("row, constraint", [
    ({"kind": "public"}, "trillion_club_stakes_kind_check"),
    ({"investee_name": ""}, "trillion_club_stakes_investee_name_check"),
    ({"investee_name": "N" * 61}, "trillion_club_stakes_investee_name_check"),
    ({"investee_cusip": "n97284108"}, "trillion_club_stakes_investee_cusip_check"),
    ({"ownership_pct": 0}, "trillion_club_stakes_ownership_pct_check"),
    ({"ownership_pct": 100.01}, "trillion_club_stakes_ownership_pct_check"),
    ({"disclosed_value_usd": -1.0, "value_basis": "invested"}, "trillion_club_stakes_disclosed_value_usd_check"),
    ({"value_basis": "book"}, "trillion_club_stakes_value_basis_check"),
    ({"source_title": "T" * 121}, "trillion_club_stakes_source_title_check"),
    ({"source_url": "http://www.sec.gov/x"}, "trillion_club_stakes_source_url_check"),
    ({"source_confidence": "rumour"}, "trillion_club_stakes_source_confidence_check"),
    ({"background": "B" * 91}, "trillion_club_stakes_background_check"),
    ({"disclosed_value_usd": 5e9, "value_basis": None}, "trillion_club_stake_value_has_basis"),
    ({"kind": "commitment", "investee_us_symbol": None, "disclosed_value_usd": 1e9, "value_basis": "invested"},
     "trillion_club_commitment_basis"),
    ({"published": True, "source_confidence": "secondary"}, "trillion_club_secondary_never_published"),
])
def test_the_fake_refuses_what_175_refuses(fake, row, constraint):
    doc = {**M._seed_style(investee_name="Probe 175"), **row}
    with pytest.raises(APIError) as info:
        fake.table(STAKES).insert(doc).execute()
    assert (info.value.code, F.error_constraint(info.value)) == ("23514", constraint)
    assert supabase_errors.is_check_violation(info.value, constraint)


def test_the_fake_refuses_175s_not_nulls_and_company_and_filing_checks(fake):
    with pytest.raises(APIError) as info:
        fake.table(STAKES).insert({**M._seed_style(investee_name="No date"), "as_of": None}).execute()
    assert (info.value.code, F.error_constraint(info.value)) == ("23502", "as_of")
    co = {**fake.get(COMPANIES, slug="apple"), "slug": "newco", "card_kind": "bogus"}
    with pytest.raises(APIError) as info:
        fake.admin_insert(COMPANIES, co)
    assert F.error_constraint(info.value) == "trillion_club_companies_card_kind_check"
    # Several broken CHECKs: Postgres names the alphabetically first (relcache sorts them by
    # name); the fake lists all of them in `details`.
    with pytest.raises(APIError) as info:
        fake.admin_insert(COMPANIES, {**fake.get(COMPANIES, slug="nvidia"), "slug": "newco2", "card_kind": "bogus"})
    assert F.error_constraint(info.value) == "trillion_club_13f_needs_cik"
    assert "trillion_club_companies_card_kind_check" in info.value.details
    with pytest.raises(APIError) as info:
        fake.add_filing(NVIDIA_CIK, "2026-Q2", "pending", [])
    assert F.error_constraint(info.value) == "trillion_club_filings_build_status_check"
    fake.add_filing(NVIDIA_CIK, "2026-Q2", "complete", [])
    with pytest.raises(APIError) as info:
        fake.add_filing(NVIDIA_CIK, "2026-Q2", "complete", [])
    assert (info.value.code, F.error_constraint(info.value)) == ("23505", "trillion_club_filings_pkey")


# ═════════════════════════════════════════════════════════════════════════════════════════
# 4. The fake answers like PostgREST, and cannot be misused silently
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_the_seed_helper_loads_the_real_seed_as_the_seed_script_leaves_it(fake):
    seed = json.loads(F.SEED_JSON.read_text(encoding="utf-8"))
    stakes = fake.rows(STAKES)
    assert len(fake.rows(COMPANIES)) == len(seed["companies"]) == 19 and len(stakes) == len(seed["stakes"]) == 132
    assert {r["origin"] for r in stakes} == {"seed"}
    assert all(r["finding_id"] is r["withdrawn_at"] is r["owner_kept_at"] is r["investee_cik"] is None for r in stakes)
    members = {r["slug"] for r in fake.rows(COMPANIES) if r["is_member"]}
    assert members == {co["slug"] for co in seed["companies"] if co["published"]}
    # test setup may move a company (the daily job's columns, a Studio unpublish) — checked
    assert fake.admin_update(COMPANIES, {"slug": "meta"}, {"published": False, "is_member": False})[0]["published"] is False
    with pytest.raises(APIError):
        fake.admin_update(COMPANIES, {"slug": "meta"}, {"closes_below": -1})
    only = F.FakeSupabase()
    only.seed_companies_and_stakes(members=["nvidia"], include_stakes=False)
    assert [r["slug"] for r in only.rows(COMPANIES) if r["is_member"]] == ["nvidia"] and only.rows(STAKES) == []


@pytest.mark.asyncio
async def test_the_club_store_reads_the_fakes_registry(fake):
    from app.services.trillion_club import jobs
    from app.services.trillion_club.store import TrillionClubStore

    rows = await TrillionClubStore(fake).read_companies()
    companies = [jobs.parse_company(r) for r in rows]
    assert [co.slug for co in companies] == sorted(co.slug for co in companies) and len(companies) == 19
    members = {co.slug for co in companies if co.effective_member}
    assert {"nvidia", "alphabet", "saudi-aramco", "samsung"} <= members and "sk-hynix" not in members


@pytest.mark.asyncio
async def test_values_come_back_the_way_postgrest_returns_them(store, fake):
    f = await new_finding(store, stake_id=None)
    assert f["created_at"] == "2026-10-28T22:30:00.123456+00:00"      # written as …Z
    assert f["filed_on"] == "2026-10-05" and f["verified_spans"] == [] and f["attempts"] == 0
    run = asyncio.to_thread                                            # the SDK is sync: off the loop
    with pytest.raises(APIError) as info:
        await run(lambda: fake.table(FINDINGS).select("id").eq("id", "not-a-uuid").execute())
    assert info.value.code == "22P02"
    got = await run(lambda: fake.table(FINDINGS).select("id").eq("id", f["id"].upper()).execute())
    assert got.data == [{"id": f["id"]}]
    upper = str(uuid.uuid4()).upper()
    row = fake.admin_insert(STAKES, {**M._stake(investee_name="Upper", finding_id=upper)})
    assert row["finding_id"] == upper.lower()
    got = await run(lambda: fake.table(STAKES).select("id, origin").eq("id", row["id"]).execute())
    assert got.data == [{"id": row["id"], "origin": "auto"}]


def test_the_fake_clamps_every_answer_to_max_rows_whatever_limit_asks():
    small = F.FakeSupabase(max_rows=3)
    small.restore(_base_snapshot())
    assert len(small.table(STAKES).select("id").limit(100).execute().data) == 3
    assert len(small.table(STAKES).select("id").limit(2).execute().data) == 2


def test_filters_have_sql_null_semantics_and_order_puts_nulls_where_postgres_does(fake):
    t = fake.table
    total = len(fake.rows(STAKES))
    with_symbol = [r for r in fake.rows(STAKES) if r["investee_us_symbol"] is not None]
    nvda_or_null = t(STAKES).select("id").neq("investee_us_symbol", "XXXX").execute().data
    assert len(nvda_or_null) == len(with_symbol) < total, "NULL never matches <>"
    assert t(STAKES).select("id").eq("investee_us_symbol", None).execute().data == [], "eq.None is the text 'None'"
    assert len(t(STAKES).select("id").is_("investee_us_symbol", "null").execute().data) == total - len(with_symbol)
    assert t(STAKES).select("id").in_("investee_us_symbol", []).execute().data == []
    asc = t(STAKES).select("investee_us_symbol").order("investee_us_symbol").execute().data
    desc = t(STAKES).select("investee_us_symbol").order("investee_us_symbol", desc=True).execute().data
    assert asc[-1]["investee_us_symbol"] is None and asc[0]["investee_us_symbol"] is not None   # ASC NULLS LAST
    assert desc[0]["investee_us_symbol"] is None                                                  # DESC NULLS FIRST
    assert len(t(STAKES).select("id").eq("published", False).execute().data) == \
        sum(1 for r in fake.rows(STAKES) if r["published"] is False)
    assert len(t(STAKES).select("id").is_("published", "true").execute().data) == \
        sum(1 for r in fake.rows(STAKES) if r["published"] is True)


def test_unfiltered_writes_and_client_side_json_errors_never_write(fake):
    before = fake.snapshot()
    with pytest.raises(APIError) as info:
        fake.table(STAKES).update({"published": False}).execute()
    assert info.value.code == "21000"
    with pytest.raises(ValueError):                    # httpx 0.28: allow_nan=False, client-side
        fake.table(FINDINGS).insert({**finding_row(), "proposed_row": {"pct": float("nan")}}).execute()
    with pytest.raises(TypeError):                     # a date object is not JSON
        fake.table(FINDINGS).insert({**finding_row(), "filed_on": date(2026, 10, 5)}).execute()
    assert fake.snapshot() == before
    assert [c_.outcome for c_ in fake.writes()] == ["error:21000", "client_error", "client_error"]


@pytest.mark.parametrize("use", [
    lambda f: f.rpc("claim_scheduled_job", {}),
    lambda f: f.table("whales"),
    lambda f: f.table(STAKES).upsert({}),
    lambda f: f.table(STAKES).select("id").range(0, 9),
    lambda f: f.table(STAKES).select("id").or_("a.eq.1"),
    lambda f: f.table(STAKES).select("id", count="exact"),
    lambda f: f.table(STAKES).insert({}).eq("id", "x"),
    lambda f: f.table(STAKES).update({"published": True}).limit(1),
    lambda f: f.table(FINDINGS).select("id").eq("proposed_row", "{}").execute(),
    lambda f: f.table(STAKES).select("id").is_("published", True),
    lambda f: f.table(COMPANIES).update({"is_member": False}).eq("slug", "nvidia").execute(),
])
def test_misuse_raises_a_base_exception_no_except_exception_can_swallow(fake, use):
    assert not issubclass(FakeMisuse, Exception)
    with pytest.raises(FakeMisuse):
        try:
            use(fake)
        except Exception:      # what a careless caller would write
            pytest.fail("FakeMisuse was caught by `except Exception`")
    assert fake.misuse
    fake.misuse.clear()


def test_the_fake_passes_for_a_client_where_job_code_checks_for_one(fake):
    from app.services.trillion_club import jobs
    from app.services.trillion_club.store import TrillionClubStore

    assert not hasattr(fake, "read_companies") and not hasattr(fake, "upsert_filing")
    assert isinstance(jobs._store_for(fake), TrillionClubStore)
    for name in ("auth", "storage", "from_"):
        with pytest.raises(FakeMisuse):
            getattr(fake, name)
    fake.misuse.clear()


@pytest.mark.asyncio
async def test_executing_on_the_event_loop_is_refused(fake):
    with pytest.raises(FakeMisuse):
        fake.table(STAKES).select("id").limit(1).execute()
    assert "event loop" in fake.misuse.pop()


def test_failure_injection_before_after_times_and_match(fake):
    f = fake.admin_insert(FINDINGS, finding_row())
    s = fake.admin_insert(STAKES, stake_row(f["id"]))
    fake.fail(STAKES, "update", when="after", match=lambda st: st.payload.get("published") is True)
    with pytest.raises(APIError):          # the raw fake: the write landed, then the answer was lost
        fake.table(STAKES).update({"published": True}).eq("id", s["id"]).execute()
    assert fake.stake(s["id"])["published"] is True
    fake.fail(FINDINGS, "*", when="before", times=2)
    for _ in range(2):
        with pytest.raises(APIError):
            fake.table(FINDINGS).update({"attempts": 1}).eq("id", f["id"]).execute()
    assert fake.finding(f["id"])["attempts"] == 0 and fake.pending_injections() == []
    fake.fail(STAKES, "select", exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        fake.table(STAKES).select("id").limit(1).execute()
    seen: List[str] = []
    fake.on_before(STAKES, "update", lambda db: seen.append(db.stake(s["id"])["origin"]))
    fake.rewrite(STAKES, "update", lambda data: [])
    assert fake.table(STAKES).update({"sort_order": 901}).eq("id", s["id"]).execute().data == []
    assert seen == ["auto"] and fake.stake(s["id"])["sort_order"] == 901 and fake.pending_injections() == []
    with pytest.raises(ValueError):
        fake.fail(STAKES, "upsert")


@pytest.mark.asyncio
async def test_a_hook_runs_before_the_write_and_its_own_writes_are_marked_injected(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    fake.clear_calls()

    def concurrent_withdraw(db: FakeSupabase) -> None:
        db.table(STAKES).update({"withdrawn_at": "2026-10-28T22:31:00Z"}).eq("id", s["id"]).execute()

    fake.on_before(STAKES, "update", concurrent_withdraw, match=lambda st: st.payload.get("published") is True)
    assert await store.publish_stake(s["id"], f["id"], now=NOW) is False
    assert [(w.injected, w.rows) for w in fake.writes(STAKES, "update", injected=None)] == [(True, 1), (False, 0)]
    assert fake.stake(s["id"])["published"] is False and fake.stake(s["id"])["withdrawn_at"] is not None


# ═════════════════════════════════════════════════════════════════════════════════════════
# 5. Reads
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_read_stakes_returns_every_row_in_id_order_with_every_column(store, fake):
    rows = await store.read_stakes()
    assert len(rows) == 132 and [r["id"] for r in rows] == sorted(r["id"] for r in fake.rows(STAKES))
    assert all(tuple(r) == SS.STAKE_READ_COLUMNS for r in rows)


@pytest.mark.asyncio
async def test_table_reads_page_past_a_server_clamp_below_the_page_size():
    small = F.FakeSupabase(max_rows=7)
    small.restore(_base_snapshot())
    rows = await StakeStore(small).read_stakes()
    assert len(rows) == 132 and len({r["id"] for r in rows}) == 132
    assert len(small.reads(STAKES)) == 132 // 7 + 2        # full pages, the short one, the empty one


@pytest.mark.asyncio
async def test_reaching_a_ceiling_raises_instead_of_returning_a_sample(store, fake, monkeypatch):
    monkeypatch.setattr(SS, "MAX_STAKE_ROWS", 132)
    with pytest.raises(StakeStoreError, match="ceiling"):
        await store.read_stakes()
    monkeypatch.setattr(SS, "MAX_STAKE_ROWS", 133)
    assert len(await store.read_stakes()) == 132
    monkeypatch.setattr(SS, "MAX_FINDING_ROWS", 2)
    for n in range(2):
        await new_finding(store, accession=acc(n))
    with pytest.raises(StakeStoreError, match="ceiling"):
        await store.read_findings_by_status(["new"])
    monkeypatch.setattr(SS, "MAX_STAKE_ROWS", 0)
    with pytest.raises(ValueError):
        await store.read_stakes()


@pytest.mark.parametrize("answer", [
    lambda data: None,
    lambda data: {"rows": data},
    lambda data: [*data[:1], "not a row"],
    lambda data: list(reversed(data)),                  # not in id order: a page boundary moved
    lambda data: [{k: v for k, v in r.items() if k != "id"} for r in data],
])
@pytest.mark.asyncio
async def test_a_read_answer_in_a_shape_postgrest_never_sends_raises(store, fake, answer):
    fake.rewrite(STAKES, "select", answer)
    with pytest.raises(StakeStoreError):
        await store.read_stakes()


@pytest.mark.asyncio
async def test_an_answer_larger_than_the_page_asked_for_raises(store, fake, monkeypatch):
    monkeypatch.setattr(SS, "PAGE_ROWS", 2)
    every = sorted(fake.rows(STAKES), key=lambda r: r["id"])
    fake.rewrite(STAKES, "select", lambda data: [{k: r[k] for k in SS.STAKE_READ_COLUMNS} for r in every[:3]])
    with pytest.raises(StakeStoreError, match="asked for 2"):
        await store.read_stakes()


@pytest.mark.asyncio
async def test_a_read_that_keeps_failing_raises_after_the_retry_budget(store, fake):
    fake.fail(STAKES, "select", times=3)
    with pytest.raises(StakeStoreError) as info:
        await store.read_stakes()
    assert info.value.op == "read stakes" and len(fake.reads(STAKES)) == 3
    fake.fail(STAKES, "select", exc=api_error_23514())
    with pytest.raises(StakeStoreError):
        await store.read_stakes()
    assert len(fake.reads(STAKES)) == 4, "a non-transient error is not retried"


def api_error_23514() -> APIError:
    return F.api_error("23514", 'new row violates check constraint "x"', details="Failing row contains (SENTINEL-VALUE).")


@pytest.mark.asyncio
async def test_single_row_reads(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    assert (await store.read_stake(s["id"]))["finding_id"] == f["id"]
    assert (await store.read_stake_by_finding(f["id"]))["id"] == s["id"]
    assert (await store.read_finding(f["id"]))["accession"] == ACC
    missing = str(uuid.uuid4())
    assert await store.read_stake(missing) is None and await store.read_finding(missing) is None
    assert await store.read_stake_by_finding(missing) is None
    assert (await store.read_stake(s["id"].upper()))["id"] == s["id"]
    fake.clear_calls()
    for bad in ("", "x", None, 7, f["id"] + "0"):
        for read in (store.read_stake, store.read_finding, store.read_stake_by_finding):
            with pytest.raises(ValueError):
                await read(bad)
    assert fake.calls == [], "a malformed id is refused before any I/O"
    # an answer that is not the row asked for is never taken for it
    fake.rewrite(STAKES, "select", lambda data: [{**data[0], "id": missing}])
    with pytest.raises(StakeStoreError):
        await store.read_stake(s["id"])
    fake.rewrite(STAKES, "select", lambda data: [{**data[0], "finding_id": missing}])
    with pytest.raises(StakeStoreError):
        await store.read_stake_by_finding(f["id"])
    fake.rewrite(FINDINGS, "select", lambda data: data + data)
    with pytest.raises(StakeStoreError):
        await store.read_finding(f["id"])


@pytest.mark.asyncio
async def test_two_stakes_claiming_one_finding_is_never_guessed_between(store, fake):
    f = await new_finding(store)
    await new_stake(store, f)
    await new_stake(store, f, investee_name="Probe Twin")
    with pytest.raises(StakeStoreError, match="2 stake rows"):
        await store.read_stake_by_finding(f["id"])


@pytest.mark.asyncio
async def test_latest_complete_holdings(store, fake):
    assert await store.read_latest_complete_holdings(NVIDIA_CIK) is None
    fake.add_filing(NVIDIA_CIK, "2026-Q1", "complete", [{"cusip": "N97284108", "symbol": "NBIS"}])
    fake.add_filing(NVIDIA_CIK, "2026-Q2", "complete", [
        {"cusip": "n97284108", "symbol": " nbis "},          # normalised
        {"cusip": "000000000", "symbol": "IONX"},            # the all-zero placeholder is never a key
        {"cusip": "N97284100", "symbol": "brk.b"},           # a bad check digit is dropped, the symbol kept
        {"cusip": None, "symbol": None},
        "not an object",
    ])
    fake.add_filing(NVIDIA_CIK, "2026-Q3", "degraded", [{"cusip": "037833100", "symbol": "AAPL"}])
    fake.add_filing("0000002488", "2026-Q4", "complete", [{"cusip": "037833100", "symbol": "AAPL"}])
    fake.clear_calls()
    got = await store.read_latest_complete_holdings(NVIDIA_CIK)
    assert got == {"period": "2026-Q2", "cusips": frozenset({"N97284108"}),
                   "symbols": frozenset({"NBIS", "IONX", "BRK-B"})}
    assert len(fake.reads(FILINGS)) == 1 and fake.reads(FILINGS)[0].rows == 1
    assert ("eq", "build_status", "complete") in fake.reads(FILINGS)[0].filters
    for bad in ("1045810", "0000000000", 1045810, "0001045810\n"):
        with pytest.raises(ValueError):
            await store.read_latest_complete_holdings(bad)


@pytest.mark.asyncio
async def test_unreadable_complete_holdings_fail_closed_never_read_as_not_on_the_13f(store, fake):
    fake.add_filing(NVIDIA_CIK, "2026-Q2", "complete", [])
    fake.rewrite(FILINGS, "select", lambda data: [{**data[0], "holdings": {"cusip": "N97284108"}}])
    with pytest.raises(StakeStoreError, match="holdings"):
        await store.read_latest_complete_holdings(NVIDIA_CIK)
    fake.rewrite(FILINGS, "select", lambda data: [{**data[0], "build_status": "degraded"}])
    with pytest.raises(StakeStoreError):
        await store.read_latest_complete_holdings(NVIDIA_CIK)


@pytest.mark.asyncio
async def test_findings_for_accessions_chunk_dedupe_and_stay_on_the_member(store, fake):
    for n in range(3):
        await new_finding(store, accession=acc(n))
    await new_finding(store, company_slug="amd", accession=acc(0))
    fake.clear_calls()
    wanted = [acc(n) for n in range(250)] + [acc(1)]
    got = await store.read_findings_for_accessions("nvidia", wanted)
    assert sorted(got) == [acc(0), acc(1), acc(2)] and {r["company_slug"] for r in got.values()} == {"nvidia"}
    assert len(fake.reads(FINDINGS)) == 3, "250 distinct accessions = 3 chunks of at most 100"
    in_lists = [next(v for o, col, v in r.filters if o == "in") for r in fake.reads(FINDINGS)]
    assert [len(x) for x in in_lists] == [100, 100, 50]
    fake.clear_calls()
    assert await store.read_findings_for_accessions("nvidia", []) == {} and fake.calls == []
    for bad in ("000104581026000099", "0001045810-26-000099\n", None):
        with pytest.raises(ValueError):
            await store.read_findings_for_accessions("nvidia", [bad])
    with pytest.raises(ValueError):
        await store.read_findings_for_accessions("NVIDIA", [ACC])
    with pytest.raises(ValueError):                       # a bare string is not a list of accessions
        await store.read_findings_for_accessions("nvidia", ACC)
    assert fake.calls == []
    fake.rewrite(FINDINGS, "select", lambda data: [*data, {**data[0], "company_slug": "amd"}])
    with pytest.raises(StakeStoreError):
        await store.read_findings_for_accessions("nvidia", [acc(0)])


@pytest.mark.asyncio
async def test_finding_lists_are_complete_and_oldest_first(store, fake):
    times = [NOW + timedelta(seconds=s) for s in (30, 10, 20, 10)]
    ids = []
    for n, at in enumerate(times):
        row, _ = await store.insert_finding(finding_row(accession=acc(n)), now=at)
        ids.append(row["id"])
    await store.update_finding(ids[0], expected_status="new", changes={"status": "proposed"}, now=times[0])
    rows = await store.read_findings_by_status(["new", "proposed"])
    tie = sorted([ids[1], ids[3]])
    assert [r["id"] for r in rows] == [*tie, ids[2], ids[0]], "created_at order, id breaks ties"
    assert [r["id"] for r in await store.read_findings_by_status("proposed")] == [ids[0]]
    assert [r["id"] for r in await store.read_findings_since(NOW + timedelta(seconds=20))] == [ids[2], ids[0]]
    for bad in ([], ["done"], [None]):
        with pytest.raises(ValueError):
            await store.read_findings_by_status(bad)
    with pytest.raises(ValueError):
        await store.read_findings_since(datetime(2026, 10, 28))


@pytest.mark.asyncio
async def test_findings_for_a_stake_and_decisions_since(store, fake):
    f1, s = await live_auto(store, fake)
    f2, _ = await store.insert_finding(finding_row(accession=acc(2)), now=NOW + timedelta(minutes=1))
    for f, at in ((f1, NOW + timedelta(hours=1)), (f2, NOW + timedelta(hours=2))):
        assert await store.update_finding(f["id"], expected_status="new", now=at, changes={
            "status": "withdrawn", "stake_id": s["id"], "owner_decision": "withdrawn", "decided_at": at})
    other = await new_finding(store, accession=acc(3))
    await store.update_finding(other["id"], expected_status="new", now=NOW, changes={
        "status": "kept", "owner_decision": "kept", "decided_at": NOW + timedelta(hours=3)})
    assert [r["id"] for r in await store.read_findings_for_stake(s["id"])] == [f1["id"], f2["id"]]
    since = await store.read_decisions_since("withdrawn", NOW + timedelta(hours=2))
    assert [r["id"] for r in since] == [f2["id"]], "decided_at >= since, inclusive"
    assert [r["id"] for r in await store.read_decisions_since("withdrawn", NOW)] == [f1["id"], f2["id"]]
    with pytest.raises(ValueError):
        await store.read_decisions_since("approved", NOW)
    with pytest.raises(ValueError):
        await store.read_findings_for_stake("nope")


@pytest.mark.asyncio
async def test_the_auto_volume_read_is_every_auto_in_flight_plus_every_auto_published_since(store, fake):
    """Gate 8's read (review B WO-1): never by created_at — the in-flight auto findings whatever their age,
    and the auto findings PUBLISHED (published_at) at or after `since`, whatever they became."""
    old = NOW - timedelta(days=40)
    rows = {}
    for n, (status, outcome, published) in enumerate([
        ("new", "auto", None), ("staged", "auto", None), ("noted", "auto", None),          # in flight
        ("published", "auto", NOW - timedelta(hours=1)), ("withdrawn", "auto", NOW - timedelta(days=6)),
        ("kept", "auto", NOW - timedelta(days=8)),                                          # before since
        ("published", "proposal", NOW - timedelta(hours=1)),                                # not an auto
        ("proposed", "proposal", None), ("reported", "heads_up", None), ("closed", "auto", None),
    ]):
        f, _ = await store.insert_finding(finding_row(accession=acc(n), outcome=outcome), now=old + timedelta(seconds=n))
        changes: Dict[str, Any] = {"status": status} if status != "new" else {"attempts": 0}
        if published is not None:
            changes["published_at"] = published
        assert await store.update_finding(f["id"], expected_status="new", changes=changes, now=old)
        rows[acc(n)] = f["id"]
    fake.clear_calls()
    got = await store.read_auto_volume(NOW - timedelta(days=7))
    assert [r["accession"] for r in got] == [acc(0), acc(1), acc(2), acc(3), acc(4)], "oldest created first"
    assert len(fake.reads(FINDINGS)) == 4, "two filtered queries, each paged to its empty page"
    filters = [filter_set(r) for r in fake.reads(FINDINGS)]
    assert all(("eq", "outcome", json.dumps("auto")) in f for f in filters)
    assert not any(col == "created_at" for r in fake.reads(FINDINGS) for _o, col, _v in r.filters)
    # a finding both queries return (in flight AND published inside the window) comes back ONCE
    assert await store.update_finding(rows[acc(3)], expected_status="published", changes={"status": "noted"}, now=NOW)
    again = await store.read_auto_volume(NOW - timedelta(days=7))
    assert [r["accession"] for r in again].count(acc(3)) == 1 and len(again) == 5
    with pytest.raises(ValueError):
        await store.read_auto_volume(datetime(2026, 10, 28))


@pytest.mark.asyncio
async def test_published_at_is_a_timestamp_column_the_store_writes_and_compares_as_an_instant(store, fake):
    f = await new_finding(store)
    assert await store.update_finding(f["id"], expected_status="new", now=NOW,
                                      changes={"status": "published", "published_at": NOW})
    assert fake.finding(f["id"])["published_at"] == "2026-10-28T22:30:00.123456+00:00"
    for bad in (datetime(2026, 10, 28), "2026-10-28T22:30:00", "yesterday", 7):
        with pytest.raises(ValueError):
            await store.update_finding(f["id"], expected_status="published", now=NOW, changes={"published_at": bad})
    # a lost answer is decided on the re-read: the stamp compared as an instant, not as text
    fake.fail(FINDINGS, "update", when="after")
    assert await store.update_finding(f["id"], expected_status="published", now=LATER,
                                      changes={"status": "kept", "published_at": NOW.astimezone(
                                          timezone(timedelta(hours=-4)))})


@pytest.mark.asyncio
async def test_the_pair_read_is_one_members_findings_of_one_investee_in_any_status(store, fake):
    a = await new_finding(store, accession=acc(1))
    b = await new_finding(store, accession=acc(2), outcome="heads_up", reason="exit_filing")
    assert await store.update_finding(b["id"], expected_status="new", changes={"status": "skipped"}, now=NOW)
    await new_finding(store, accession=acc(3), investee_cik="0009999902")
    await new_finding(store, accession=acc(4), company_slug="amd")
    fake.clear_calls()
    got = await store.read_findings_for_pair("nvidia", "0009999901")
    assert sorted(r["id"] for r in got) == sorted([a["id"], b["id"]]), "any status; another investee / member never"
    [first, *rest] = fake.reads(FINDINGS)
    assert {("eq", "company_slug", json.dumps("nvidia")), ("eq", "investee_cik", json.dumps("0009999901"))} <= \
        filter_set(first)
    for bad in (("nvidia", None), ("nvidia", "9999901"), ("NVIDIA", "0009999901"), ("nvidia", "0000000000")):
        with pytest.raises(ValueError):
            await store.read_findings_for_pair(*bad)


# ═════════════════════════════════════════════════════════════════════════════════════════
# 6. Ledger writes
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_insert_finding_creates_once_and_the_unique_claim_answers_with_the_existing_row(store, fake):
    first, created = await store.insert_finding(finding_row(), now=NOW)
    assert created and first["status"] == "new" and first["updated_at"] == first["created_at"]
    again, created = await store.insert_finding(finding_row(reason="t3_indication"), now=LATER)
    assert not created and again["id"] == first["id"] and again["reason"] == "plain_13dg"
    assert len([r for r in fake.rows(FINDINGS) if r["accession"] == ACC]) == 1
    other, created = await store.insert_finding(finding_row(company_slug="amd"), now=LATER)
    assert created and other["id"] != first["id"], "the claim is per (member, filing)"


@pytest.mark.asyncio
async def test_insert_finding_after_a_lost_answer(store, fake):
    fake.fail(FINDINGS, "insert", when="after")
    row, created = await store.insert_finding(finding_row(), now=NOW)
    assert created and row["accession"] == ACC, "applied, answer lost → ours (created_at is this call's now)"
    fake.fail(FINDINGS, "insert", when="before")
    with pytest.raises(StakeStoreError) as info:
        await store.insert_finding(finding_row(accession=acc(1)), now=NOW)
    assert supabase_errors.is_transient_supabase_error(info.value.cause)
    assert fake.get(FINDINGS, accession=acc(1)) is None

    def rival(db: FakeSupabase) -> None:
        db.admin_insert(FINDINGS, {**finding_row(accession=acc(2)), "created_at": "2026-10-28T20:00:00Z"})

    # Another run claims the filing first; OUR insert then fails ambiguously (a 520, not a 23505):
    # the row on file is not ours — its created_at is not this call's now.
    fake.on_before(FINDINGS, "insert", rival)
    fake.fail(FINDINGS, "insert", when="before")
    row, created = await store.insert_finding(finding_row(accession=acc(2)), now=NOW)
    assert not created and row["created_at"] == "2026-10-28T20:00:00+00:00"
    # ... and a plain 23505 against the rival is "not created" without any stamp reasoning
    fake.on_before(FINDINGS, "insert", lambda db: db.admin_insert(
        FINDINGS, {**finding_row(accession=acc(3)), "created_at": "2026-10-28T22:30:00.123456Z"}))
    row, created = await store.insert_finding(finding_row(accession=acc(3)), now=NOW)
    assert not created, "a unique violation is someone else's row even when the stamps collide"
    assert fake.pending_injections() == []


@pytest.mark.parametrize("over", [
    {"accession": "000104581026000099"}, {"accession": "0001045810-26-000099\n"}, {"filer_cik": "1045810"},
    {"filer_cik": "0000000000"}, {"investee_cik": "12345678901"}, {"company_slug": "NVIDIA"},
    {"status": "done"}, {"tier": "13d"}, {"outcome": "row"}, {"reason": "plain"}, {"source": "edgar"},
    {"form": ""}, {"document": "d" * 256}, {"source_url": "http://www.sec.gov/x"}, {"filed_on": "10/05/2026"},
    {"filed_on": datetime(2026, 10, 5, tzinfo=timezone.utc)}, {"proposed_row": []},
    {"proposed_row": {"pct": float("inf")}}, {"proposed_row": {1: "int key"}}, {"proposed_row": {"x": "a\x00b"}},
    {"proposed_row": {"x": "y" * 16400}}, {"verified_spans": None}, {"verified_spans": ["s"] * 101},
    {"verified_spans": "x"}, {"fact_sha": "A" * 64}, {"telegram_message_id": 0}, {"telegram_message_id": True},
    {"attempts": -1}, {"attempts": 1.0}, {"stake_id": "nope"}, {"notified_at": datetime(2026, 10, 28)},
    {"notified_at": "2026-10-28T22:30:00"}, {"owner_decision": "kept"}, {"decided_at": NOW},
    {"owner_decision": "approved", "decided_at": NOW}, {"id": str(uuid.uuid4())}, {"created_at": NOW},
    {"updated_at": NOW}, {"bogus": 1}, {"reason": None},
])
@pytest.mark.asyncio
async def test_insert_finding_refuses_what_205_would_refuse_before_any_io(store, fake, over):
    fake.clear_calls()
    with pytest.raises(ValueError):
        await store.insert_finding(finding_row(**over), now=NOW)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_insert_finding_needs_an_aware_now_and_keeps_last_error_storable(store, fake):
    with pytest.raises(ValueError):
        await store.insert_finding(finding_row(), now=datetime(2026, 10, 28, 22, 30))
    row, _ = await store.insert_finding(finding_row(status="error", attempts=1,
                                                    last_error="Boom\x00\n" + "e" * 3000), now=NOW)
    assert row["last_error"].startswith("Boom e") and len(row["last_error"]) == 2000 and "\x00" not in row["last_error"]


@pytest.mark.asyncio
async def test_update_finding_is_a_compare_and_swap_on_every_fence(store, fake):
    f = await new_finding(store)
    assert await store.update_finding(f["id"], expected_status="new", now=NOW,
                                      changes={"status": "proposed", "telegram_message_id": 41,
                                               "notified_at": NOW})
    stored = fake.finding(f["id"])
    assert stored["status"] == "proposed" and stored["notified_at"] == "2026-10-28T22:30:00.123456+00:00"
    assert stored["updated_at"] == stored["notified_at"]
    assert filter_set(last_write(fake, FINDINGS, "update")) == {
        ("eq", "id", json.dumps(f["id"])), ("eq", "status", '"new"')}
    # each fence alone refuses: the status, the message, the fact sha
    assert not await store.update_finding(f["id"], expected_status="new", changes={"status": "staged"}, now=LATER)
    assert not await store.update_finding(f["id"], expected_status="proposed", message_id=42,
                                          changes={"status": "publishing"}, now=LATER)
    assert not await store.update_finding(f["id"], expected_status="proposed", message_id=41, fact_sha="b" * 64,
                                          changes={"status": "publishing"}, now=LATER)
    assert fake.finding(f["id"])["status"] == "proposed"
    assert await store.update_finding(f["id"], expected_status="proposed", message_id=41, fact_sha="a" * 64,
                                      changes={"status": "publishing"}, now=LATER)
    assert filter_set(last_write(fake, FINDINGS, "update")) == {
        ("eq", "id", json.dumps(f["id"])), ("eq", "status", '"proposed"'),
        ("eq", "telegram_message_id", "41"), ("eq", "fact_sha", json.dumps("a" * 64))}
    assert not await store.update_finding(str(uuid.uuid4()), expected_status="new", changes={"attempts": 1}, now=NOW)


@pytest.mark.asyncio
async def test_update_finding_after_a_lost_answer(store, fake):
    f = await new_finding(store)
    fake.fail(FINDINGS, "update", when="after")
    assert await store.update_finding(f["id"], expected_status="new", changes={"status": "staged"}, now=NOW)
    fake.fail(FINDINGS, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.update_finding(f["id"], expected_status="staged", changes={"status": "noted"}, now=NOW)
    assert fake.finding(f["id"])["status"] == "staged"

    def rival(db: FakeSupabase) -> None:     # another writer makes the SAME change first, at its own time
        db.admin_update(FINDINGS, {"id": f["id"]}, {"status": "noted", "updated_at": "2026-10-28T23:00:00Z"})

    fake.on_before(FINDINGS, "update", rival)
    fake.fail(FINDINGS, "update", when="after")
    assert await store.update_finding(f["id"], expected_status="staged", changes={"status": "noted"},
                                      now=LATER) is False, "the same change by someone else is a LOST race"
    fake.fail(FINDINGS, "update", when="before", exc=api_error_23514())
    with pytest.raises(StakeStoreError):
        await store.update_finding(f["id"], expected_status="noted", changes={"status": "published"}, now=LATER)


@pytest.mark.parametrize("changes", [
    {}, {"id": str(uuid.uuid4())}, {"company_slug": "amd"}, {"accession": acc(9)}, {"created_at": NOW},
    {"updated_at": NOW}, {"owner_decision": "withdrawn"}, {"decided_at": NOW},
    {"owner_decision": None, "decided_at": NOW}, {"owner_decision": "kept", "decided_at": None},
    {"status": None}, {"status": "done"}, {"verified_spans": None}, {"attempts": -1}, {"nope": 1},
])
@pytest.mark.asyncio
async def test_update_finding_refuses_bad_changes_before_any_io(store, fake, changes):
    f = await new_finding(store)
    fake.clear_calls()
    with pytest.raises(ValueError):
        await store.update_finding(f["id"], expected_status="new", changes=changes, now=NOW)
    for kw in ({"message_id": 0}, {"fact_sha": "x"}, {"expected_status": "done"}):
        with pytest.raises(ValueError):
            await store.update_finding(f["id"], **{"expected_status": "new", **kw}, changes={"attempts": 1}, now=NOW)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_decision_is_written_with_its_time_and_can_be_cleared_together(store, fake):
    f = await new_finding(store)
    assert await store.update_finding(f["id"], expected_status="new", now=NOW, changes={
        "status": "withdrawn", "owner_decision": "withdrawn", "decided_at": NOW})
    assert fake.finding(f["id"])["decided_at"] == "2026-10-28T22:30:00.123456+00:00"
    assert await store.update_finding(f["id"], expected_status="withdrawn", now=LATER, changes={
        "owner_decision": None, "decided_at": None})


# ═════════════════════════════════════════════════════════════════════════════════════════
# 7. Stake writes
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_insert_stake_writes_an_unpublished_machine_row(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f, as_of=date(2026, 10, 1), verified_on=TODAY)
    assert s["published"] is False and s["origin"] == "auto" and s["finding_id"] == f["id"]
    assert s["as_of"] == "2026-10-01" and s["created_at"] == s["updated_at"] == "2026-10-28T22:30:00.123456+00:00"
    assert s["owner_kept_at"] is None and s["withdrawn_at"] is None
    p = await new_stake(store, await new_finding(store, accession=acc(1)), investee_name="Probe Commitment",
                        origin="proposal", kind="commitment", investee_us_symbol=None, ownership_pct=None,
                        ownership_basis=None, disclosed_value_usd=2e9, value_basis="committed_up_to", material=True)
    assert p["origin"] == "proposal" and p["disclosed_value_usd"] == 2e9


@pytest.mark.parametrize("over", [
    {"published": True}, {"published": None}, {"origin": "seed"}, {"origin": "manual"},
    {"background": "Disclosed in July."}, {"source_url": "https://nvidianews.nvidia.com/news/x"},
    {"source_url": "https://www.sec.gov.example.com/Archives/edgar/data/1/x.htm"}, {"tied_to_deal": True},
    {"source_confidence": "secondary"}, {"material": True}, {"kind": "private", "investee_us_symbol": None},
    {"disclosed_value_usd": 1e9, "value_basis": "invested"}, {"value_basis": "fair_value"},
    {"investee_cik": "1045810"}, {"investee_cik": "0000000000"}, {"investee_name": ""},
    {"ownership_pct": float("nan")}, {"ownership_pct": 0}, {"sort_order": 2 ** 31}, {"sort_order": True},
    {"as_of": "2026/10/01"}, {"as_of": datetime(2026, 10, 1, tzinfo=timezone.utc)}, {"investee_cusip": "abc"},
    {"material": "false"}, {"kind": "public"}, {"value_basis": None, "disclosed_value_usd": 5.0},
])
@pytest.mark.asyncio
async def test_insert_stake_refuses_before_any_io(store, fake, over):
    f = await new_finding(store)
    fake.clear_calls()
    with pytest.raises(ValueError):
        await store.insert_stake(stake_row(f["id"], **over), now=NOW)
    for broken in ({k: v for k, v in stake_row(f["id"]).items() if k != "finding_id"},
                   {k: v for k, v in stake_row(f["id"]).items() if k != "as_of"},
                   {**stake_row(f["id"]), "owner_kept_at": NOW}, {**stake_row(f["id"]), "id": str(uuid.uuid4())},
                   {**stake_row(f["id"]), "finding_id": "nope"}):
        with pytest.raises(ValueError):
            await store.insert_stake(broken, now=NOW)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_insert_stake_key_conflict_and_lost_answers(store, fake):
    seed = seed_stake(fake)
    f = await new_finding(store)
    clash = stake_row(f["id"], investee_name=seed["investee_name"], kind=seed["kind"],
                      investee_us_symbol=seed["investee_us_symbol"] or "PRBH")
    if clash["kind"] not in ("on_13f_note", "us_listed_off_13f"):
        clash.update(origin="proposal")
        if clash["kind"] == "private":
            clash["investee_us_symbol"] = None
    with pytest.raises(StakeConflict) as info:
        await store.insert_stake(clash, now=NOW)
    assert supabase_errors.is_unique_violation(info.value.cause) and fake.get(STAKES, finding_id=f["id"]) is None
    fake.fail(STAKES, "insert", when="after")
    s = await new_stake(store, f)
    assert s["finding_id"] == f["id"], "applied, answer lost → the row of this finding is returned"
    # ... and a retry of the same insert (the next run) finds it instead of conflicting
    again = await new_stake(store, f)
    assert again["id"] == s["id"] and len([r for r in fake.rows(STAKES) if r["finding_id"] == f["id"]]) == 1
    g = await new_finding(store, accession=acc(5))
    fake.fail(STAKES, "insert", when="before")
    with pytest.raises(StakeStoreError) as info:
        await new_stake(store, g, investee_name="Probe Other")
    assert not isinstance(info.value, StakeConflict) and fake.get(STAKES, finding_id=g["id"]) is None
    fake.rewrite(STAKES, "insert", lambda data: [])          # an answer without the row
    s2 = await new_stake(store, g, investee_name="Probe Other")
    assert s2["finding_id"] == g["id"]


@pytest.mark.asyncio
async def test_publish_stake_fences(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    assert not await store.publish_stake(s["id"], str(uuid.uuid4()), now=NOW), "another finding's row"
    assert not await store.publish_stake(str(uuid.uuid4()), f["id"], now=NOW), "no such row"
    fake.admin_update(STAKES, {"id": s["id"]}, {"withdrawn_at": "2026-10-28T22:00:00Z"})
    assert not await store.publish_stake(s["id"], f["id"], now=NOW), "a withdrawn row is never published"
    assert fake.stake(s["id"])["published"] is False
    # ... refused by the FENCE (0 rows), not by 205's CHECK backstop (which answers 23514)
    assert (last_write(fake, STAKES, "update").outcome, last_write(fake, STAKES, "update").rows) == ("ok", 0)
    fake.admin_update(STAKES, {"id": s["id"]}, {"withdrawn_at": None})
    assert await store.publish_stake(s["id"], f["id"], now=NOW)
    # idempotent: already live under this finding → True (a retry after an unknown outcome)
    assert await store.publish_stake(s["id"], f["id"], now=LATER) is True
    assert fake.stake(s["id"])["updated_at"] == "2026-10-28T22:30:00.123456+00:00"
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("eq", "finding_id", json.dumps(f["id"])),
        ("in", "origin", json.dumps(["auto", "proposal"])), ("eq", "published", "false"),
        ("is", "withdrawn_at", '"null"')}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["proposed", "publishing"])
@pytest.mark.parametrize("change", [{"fact_sha": "2" * 64}, {"proposed_row": {"investee_name": "Other"}}])
async def test_an_open_proposals_facts_never_change_in_place(store, fake, status, change):
    """Review A STORE-2: a Publish tap carries only (verb, finding id, message id) and fences on the
    fact_sha it READS — so the facts must never change while the message is out, or a stale message
    would publish facts the owner never saw. Leaving the open states with new facts is allowed."""
    f = await new_finding(store)
    fake.admin_update(FINDINGS, {"id": f["id"]}, {"status": status})
    before = dict(fake.finding(f["id"]))
    with pytest.raises(ValueError, match="never change in place"):
        await store.update_finding(f["id"], expected_status=status, changes=change, now=NOW)
    assert fake.finding(f["id"]) == before and fake.writes(FINDINGS, "update") == []
    # a move OUT of the open states may carry new facts (a heads-up holds only digest keys)
    assert await store.update_finding(f["id"], expected_status=status,
                                      changes={"status": "new", "outcome": "heads_up", **change}, now=NOW)


@pytest.mark.asyncio
async def test_a_flip_whose_answer_and_reread_were_lost_answers_true_on_the_retry(store, fake):
    """Review A STORE-1: the flip lands, its answer is lost and the re-read fails too → the store
    says 'not applied' (StakeStoreError). The retry the caller makes must answer True (the row is
    live under this finding), never False ('a Withdraw won') for a public row."""
    f = await new_finding(store)
    s = await new_stake(store, f)
    fake.fail(STAKES, "update", when="after")
    fake.fail(STAKES, "select", when="before", times=SS.READ_ATTEMPTS)
    with pytest.raises(StakeStoreError):
        await store.publish_stake(s["id"], f["id"], now=NOW)
    assert fake.stake(s["id"])["published"] is True
    assert await store.publish_stake(s["id"], f["id"], now=LATER) is True
    # a Withdraw that landed in between still wins
    fake.admin_update(STAKES, {"id": s["id"]}, {"published": False, "withdrawn_at": "2026-10-28T22:31:00Z"})
    assert await store.publish_stake(s["id"], f["id"], now=LATER) is False
    assert fake.stake(s["id"])["published"] is False


@pytest.mark.asyncio
async def test_an_adopt_whose_answer_and_reread_were_lost_answers_true_on_the_retry(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    fake.fail(STAKES, "update", when="after")
    fake.fail(STAKES, "select", when="before", times=SS.READ_ATTEMPTS)
    with pytest.raises(StakeStoreError):
        await store.adopt_as_proposal(s["id"], f["id"], now=NOW)
    assert fake.stake(s["id"])["origin"] == "proposal"
    assert await store.adopt_as_proposal(s["id"], f["id"], now=LATER) is True
    fake.admin_update(STAKES, {"id": s["id"]}, {"withdrawn_at": "2026-10-28T22:31:00Z"})
    assert await store.adopt_as_proposal(s["id"], f["id"], now=LATER) is False, "withdrawn: never adopted"


@pytest.mark.asyncio
async def test_publish_stake_after_a_lost_answer_and_a_withdraw_racing_it(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    fake.fail(STAKES, "update", when="after")
    assert await store.publish_stake(s["id"], f["id"], now=NOW)
    assert fake.stake(s["id"])["published"] is True
    f2 = await new_finding(store, accession=acc(2))
    s2 = await new_stake(store, f2, investee_name="Probe Two")
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.publish_stake(s2["id"], f2["id"], now=NOW)

    def withdraw(db: FakeSupabase) -> None:
        db.admin_update(STAKES, {"id": s2["id"]}, {"withdrawn_at": "2026-10-28T22:31:00Z"})

    fake.on_before(STAKES, "update", withdraw)
    fake.fail(STAKES, "update", when="before")
    assert await store.publish_stake(s2["id"], f2["id"], now=NOW) is False, "the owner's no wins"
    assert fake.stake(s2["id"])["published"] is False


@pytest.mark.asyncio
async def test_a_write_answer_that_is_not_the_row_sent_for_is_decided_from_the_table(store, fake):
    f = await new_finding(store)
    s = await new_stake(store, f)
    other = seed_stake(fake)
    fake.rewrite(STAKES, "update", lambda data: [{**data[0], "id": other["id"]}])
    assert await store.publish_stake(s["id"], f["id"], now=NOW), "the flip landed: the re-read says so"
    fake.rewrite(STAKES, "update", lambda data: [{"id": s["id"]}, {"id": s["id"]}])
    assert await store.unpublish_auto(s["id"], now=NOW) is True
    assert fake.stake(s["id"])["published"] is False and fake.pending_injections() == []
    # the ledger's compare-and-swap decides the same way
    fake.rewrite(FINDINGS, "update", lambda data: [{**data[0], "id": str(uuid.uuid4())}])
    assert await store.update_finding(f["id"], expected_status="new", changes={"status": "staged"}, now=NOW)
    fake.rewrite(FINDINGS, "update", lambda data: data + data)
    assert await store.update_finding(f["id"], expected_status="staged", changes={"status": "noted"}, now=NOW)
    fake.rewrite(FINDINGS, "update", lambda data: {"rows": data})
    fake.fail(FINDINGS, "select", when="before", exc=RuntimeError("the re-read failed too"))
    with pytest.raises(StakeStoreError) as info:
        await store.update_finding(f["id"], expected_status="noted", changes={"status": "published"}, now=NOW)
    assert isinstance(info.value.cause, SS._AmbiguousAnswer), "the write's own error is the cause"
    assert fake.pending_injections() == []


@pytest.mark.asyncio
async def test_withdraw_stake(store, fake):
    f, s = await live_auto(store, fake)
    assert await store.withdraw_stake(s["id"], now=NOW)
    row = fake.stake(s["id"])
    assert row["published"] is False and row["withdrawn_at"] == "2026-10-28T22:30:00.123456+00:00"
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("in", "origin", json.dumps(["auto", "proposal"])),
        ("is", "withdrawn_at", '"null"')}
    assert await store.withdraw_stake(s["id"], now=LATER), "already withdrawn → True (idempotent)"
    assert fake.stake(s["id"])["withdrawn_at"] == row["withdrawn_at"], "the first Withdraw's time stays"
    seed = seed_stake(fake)
    assert await store.withdraw_stake(seed["id"], now=NOW) is False
    assert fake.stake(seed["id"]) == seed, "a seed row is never touched"
    assert await store.withdraw_stake(str(uuid.uuid4()), now=NOW) is False
    kept_f, kept = await live_auto(store, fake, accession=acc(3), investee_name="Probe Kept")
    assert await store.keep_stake(kept["id"], now=NOW)
    assert await store.withdraw_stake(kept["id"], now=LATER), "Withdraw works after Keep"
    p = await new_stake(store, await new_finding(store, accession=acc(4)), investee_name="Probe Prop",
                        origin="proposal")
    assert await store.withdraw_stake(p["id"], now=NOW) and fake.stake(p["id"])["withdrawn_at"]


@pytest.mark.asyncio
async def test_withdraw_stake_after_a_lost_answer(store, fake):
    f, s = await live_auto(store, fake)
    fake.fail(STAKES, "update", when="after")
    assert await store.withdraw_stake(s["id"], now=NOW)
    f2, s2 = await live_auto(store, fake, accession=acc(2), investee_name="Probe Two")
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.withdraw_stake(s2["id"], now=NOW)
    assert fake.stake(s2["id"])["published"] is True
    # An inconsistent observation: the UPDATE matched nothing (someone withdrew first), yet the
    # re-read shows a live machine row (that withdrawal was undone in between). Never guessed.
    fake.on_before(STAKES, "update", lambda db: db.admin_update(
        STAKES, {"id": s2["id"]}, {"published": False, "withdrawn_at": "2026-10-28T22:31:00Z"}))
    fake.on_before(STAKES, "select", lambda db: db.admin_update(STAKES, {"id": s2["id"]}, {"withdrawn_at": None}))
    with pytest.raises(StakeStoreError, match="re-read shows"):
        await store.withdraw_stake(s2["id"], now=NOW)
    assert fake.pending_injections() == []


@pytest.mark.asyncio
async def test_keep_stake(store, fake):
    f, s = await live_auto(store, fake)
    assert await store.keep_stake(s["id"], now=NOW) and fake.stake(s["id"])["owner_kept_at"]
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("eq", "origin", '"auto"'), ("is", "withdrawn_at", '"null"')}
    p = await new_stake(store, await new_finding(store, accession=acc(2)), investee_name="P", origin="proposal")
    assert await store.keep_stake(p["id"], now=NOW) is False, "Keep is for auto rows only"
    assert (last_write(fake, STAKES, "update").outcome, last_write(fake, STAKES, "update").rows) == ("ok", 0), \
        "the fence refuses it, not kept_only_auto's 23514"
    await store.withdraw_stake(s["id"], now=NOW)
    assert await store.keep_stake(s["id"], now=LATER) is False, "never Keep a withdrawn row"
    assert await store.keep_stake(str(uuid.uuid4()), now=NOW) is False
    f3, s3 = await live_auto(store, fake, accession=acc(3), investee_name="Probe Three")
    fake.fail(STAKES, "update", when="after")
    assert await store.keep_stake(s3["id"], now=NOW)
    f4, s4 = await live_auto(store, fake, accession=acc(4), investee_name="Probe Four")
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.keep_stake(s4["id"], now=NOW)


@pytest.mark.asyncio
async def test_unpublish_auto_is_the_exit_and_never_a_tombstone(store, fake):
    f, s = await live_auto(store, fake)
    assert await store.unpublish_auto(s["id"], now=NOW)
    row = fake.stake(s["id"])
    assert row["published"] is False and row["withdrawn_at"] is None
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("eq", "origin", '"auto"')}
    assert await store.unpublish_auto(seed_stake(fake)["id"], now=NOW) is False
    p = await new_stake(store, await new_finding(store, accession=acc(2)), investee_name="P", origin="proposal")
    assert await store.unpublish_auto(p["id"], now=NOW) is False
    f3, s3 = await live_auto(store, fake, accession=acc(3), investee_name="Probe Three")
    fake.fail(STAKES, "update", when="after")
    assert await store.unpublish_auto(s3["id"], now=NOW)
    f4, s4 = await live_auto(store, fake, accession=acc(4), investee_name="Probe Four")
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.unpublish_auto(s4["id"], now=NOW)
    assert fake.stake(s4["id"])["published"] is True


@pytest.mark.asyncio
async def test_bump_verified_on(store, fake):
    f, s = await live_auto(store, fake, verified_on="2026-10-20")
    assert await store.bump_verified_on(s["id"], f["id"], TODAY, now=NOW)
    assert fake.stake(s["id"])["verified_on"] == "2026-10-28"
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("eq", "finding_id", json.dumps(f["id"])),
        ("in", "origin", json.dumps(["auto", "proposal"])), ("eq", "published", "true")}
    assert not await store.bump_verified_on(s["id"], str(uuid.uuid4()), TODAY, now=NOW)
    await store.unpublish_auto(s["id"], now=NOW)
    assert not await store.bump_verified_on(s["id"], f["id"], date(2026, 10, 29), now=NOW), "only a LIVE row"
    for bad in (datetime(2026, 10, 28, tzinfo=timezone.utc), "2026-10-28", None):
        with pytest.raises(ValueError):
            await store.bump_verified_on(s["id"], f["id"], bad, now=NOW)
    f2, s2 = await live_auto(store, fake, accession=acc(2), investee_name="Probe Two", verified_on="2026-10-20")
    fake.fail(STAKES, "update", when="after")
    assert await store.bump_verified_on(s2["id"], f2["id"], TODAY, now=NOW)
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.bump_verified_on(s2["id"], f2["id"], date(2026, 10, 29), now=NOW)


@pytest.mark.asyncio
async def test_adopt_as_proposal_clears_keep_in_the_same_write(store, fake):
    f, s = await live_auto(store, fake)
    assert await store.keep_stake(s["id"], now=NOW)
    assert not await store.adopt_as_proposal(s["id"], f["id"], now=NOW), "a LIVE row is never adopted"
    await store.unpublish_auto(s["id"], now=NOW)
    assert not await store.adopt_as_proposal(s["id"], str(uuid.uuid4()), now=NOW)
    assert await store.adopt_as_proposal(s["id"], f["id"], now=LATER)
    row = fake.stake(s["id"])
    assert row["origin"] == "proposal" and row["owner_kept_at"] is None and row["published"] is False
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("eq", "finding_id", json.dumps(f["id"])), ("eq", "origin", '"auto"'),
        ("eq", "published", "false"), ("is", "withdrawn_at", '"null"')}
    assert await store.adopt_as_proposal(s["id"], f["id"], now=LATER) is True, "already this finding's proposal"
    f2 = await new_finding(store, accession=acc(2))
    s2 = await new_stake(store, f2, investee_name="Probe Two")
    await store.withdraw_stake(s2["id"], now=NOW)
    assert not await store.adopt_as_proposal(s2["id"], f2["id"], now=NOW), "never a withdrawn row"
    f3 = await new_finding(store, accession=acc(3))
    s3 = await new_stake(store, f3, investee_name="Probe Three")
    fake.fail(STAKES, "update", when="after")
    assert await store.adopt_as_proposal(s3["id"], f3["id"], now=NOW)
    f4 = await new_finding(store, accession=acc(4))
    s4 = await new_stake(store, f4, investee_name="Probe Four")
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.adopt_as_proposal(s4["id"], f4["id"], now=NOW)


def _replacement(f: Dict[str, Any], **over: Any) -> Dict[str, Any]:
    row = {k: v for k, v in stake_row(f["id"]).items() if k not in ("origin", "finding_id")}
    row.update(ownership_pct=9.2, as_of="2026-10-20", source_title="NVIDIA Schedule 13G/A (Oct 22, 2026)",
               published=True)
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_replace_stake_replaces_the_whole_row_in_place(store, fake):
    f, s = await live_auto(store, fake)
    await store.keep_stake(s["id"], now=NOW)
    read = fake.stake(s["id"])
    upd = await new_finding(store, accession=acc(7), tier="update", outcome="proposal", reason="update")
    assert await store.replace_stake(s["id"], expected_updated_at=read["updated_at"], row=_replacement(upd),
                                     finding_id=upd["id"], now=LATER)
    row = fake.stake(s["id"])
    assert (row["ownership_pct"], row["as_of"], row["origin"], row["finding_id"], row["owner_kept_at"],
            row["published"]) == (9.2, "2026-10-20", "proposal", upd["id"], None, True)
    assert row["created_at"] == read["created_at"] and row["id"] == s["id"]
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "id", json.dumps(s["id"])), ("eq", "updated_at", json.dumps("2026-10-28T22:30:00.123456Z")),
        ("is", "withdrawn_at", '"null"'), ("in", "origin", json.dumps(["auto", "proposal"])),
        ("eq", "company_slug", '"nvidia"')}


@pytest.mark.asyncio
async def test_replace_stake_fences(store, fake):
    f, s = await live_auto(store, fake)
    upd = await new_finding(store, accession=acc(7))
    read = fake.stake(s["id"])
    stale = (datetime.fromisoformat(read["updated_at"]) - timedelta(seconds=1)).isoformat()
    assert not await store.replace_stake(s["id"], expected_updated_at=stale, row=_replacement(upd),
                                         finding_id=upd["id"], now=LATER), "the row changed since it was read"
    assert not await store.replace_stake(s["id"], expected_updated_at=read["updated_at"],
                                         row=_replacement(upd, company_slug="amd"), finding_id=upd["id"],
                                         now=LATER), "never moved to another member"
    seed = seed_stake(fake)
    seed_like = {**_replacement(upd), **{k: seed[k] for k in ("company_slug", "kind", "investee_name")}}
    seed_like.update(investee_us_symbol=seed["investee_us_symbol"] or "PRBH")
    if seed_like["kind"] == "private":
        seed_like["investee_us_symbol"] = None
    if seed_like["kind"] in ("commitment",):
        seed_like.update(ownership_pct=None, ownership_basis=None, investee_us_symbol=None)
    assert not await store.replace_stake(seed["id"], expected_updated_at=seed["updated_at"], row=seed_like,
                                         finding_id=upd["id"], now=LATER), "a seed row is never replaced"
    assert fake.stake(seed["id"]) == seed
    await store.withdraw_stake(s["id"], now=NOW)
    gone = fake.stake(s["id"])
    for published in (True, False):     # unpublished: no CHECK would stop it — only the fence does
        assert not await store.replace_stake(s["id"], expected_updated_at=gone["updated_at"],
                                             row=_replacement(upd, published=published), finding_id=upd["id"],
                                             now=LATER), "never a withdrawn row"
        assert (last_write(fake, STAKES, "update").outcome, last_write(fake, STAKES, "update").rows) == ("ok", 0)
    assert fake.stake(s["id"]) == gone


@pytest.mark.asyncio
async def test_replace_stake_conflict_lost_answers_and_validation(store, fake):
    f, s = await live_auto(store, fake)
    f2, other = await live_auto(store, fake, accession=acc(2), investee_name="Probe Other")
    upd = await new_finding(store, accession=acc(7))
    read = fake.stake(s["id"])
    with pytest.raises(StakeConflict):
        await store.replace_stake(s["id"], expected_updated_at=read["updated_at"],
                                  row=_replacement(upd, investee_name="Probe Other"), finding_id=upd["id"], now=LATER)
    assert fake.stake(s["id"]) == read
    fake.fail(STAKES, "update", when="after")
    assert await store.replace_stake(s["id"], expected_updated_at=read["updated_at"], row=_replacement(upd),
                                     finding_id=upd["id"], now=LATER)
    read2 = fake.stake(s["id"])
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.replace_stake(s["id"], expected_updated_at=read2["updated_at"],
                                  row=_replacement(upd, ownership_pct=9.9), finding_id=upd["id"], now=NOW)
    fake.clear_calls()
    for bad in ({k: v for k, v in _replacement(upd).items() if k != "investee_cik"},
                _replacement(upd, background="x"), _replacement(upd, source_url="https://example.com/x"),
                {**_replacement(upd), "origin": "proposal"}):
        with pytest.raises(ValueError):
            await store.replace_stake(s["id"], expected_updated_at=read2["updated_at"], row=bad,
                                      finding_id=upd["id"], now=LATER)
    with pytest.raises(ValueError):
        await store.replace_stake(s["id"], expected_updated_at="yesterday", row=_replacement(upd),
                                  finding_id=upd["id"], now=LATER)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_withdraw_stakes_for_finding(store, fake):
    f = await new_finding(store, outcome="proposal", reason="no_us_listing")
    a = await new_stake(store, f, origin="proposal", kind="private", investee_us_symbol=None)
    b = await new_stake(store, f, origin="proposal", kind="private", investee_us_symbol=None, investee_name="P2")
    await store.withdraw_stake(b["id"], now=NOW)
    assert await store.withdraw_stakes_for_finding(f["id"], now=LATER) == 1
    assert fake.stake(a["id"])["withdrawn_at"] and fake.stake(b["id"])["withdrawn_at"] == "2026-10-28T22:30:00.123456+00:00"
    assert filter_set(last_write(fake, STAKES, "update")) == {
        ("eq", "finding_id", json.dumps(f["id"])), ("in", "origin", json.dumps(["auto", "proposal"])),
        ("is", "withdrawn_at", '"null"')}
    assert await store.withdraw_stakes_for_finding(f["id"], now=LATER) == 0
    assert await store.withdraw_stakes_for_finding(str(uuid.uuid4()), now=NOW) == 0
    g = await new_finding(store, accession=acc(2))
    s = await new_stake(store, g, investee_name="Probe G")
    earlier = await new_stake(store, g, investee_name="Probe G2")
    await store.withdraw_stake(earlier["id"], now=NOW - timedelta(days=1))
    fake.fail(STAKES, "update", when="after")
    assert await store.withdraw_stakes_for_finding(g["id"], now=NOW) == 1, \
        "after a lost answer, only the rows withdrawn at THIS call's now are this call's"
    h = await new_finding(store, accession=acc(3))
    await new_stake(store, h, investee_name="Probe H")
    fake.fail(STAKES, "update", when="before")
    with pytest.raises(StakeStoreError):
        await store.withdraw_stakes_for_finding(h["id"], now=NOW)
    assert fake.stake(s["id"])["withdrawn_at"]


@pytest.mark.parametrize("call", [
    lambda st, sid, fid: st.publish_stake(sid, fid, now=datetime(2026, 10, 28)),
    lambda st, sid, fid: st.withdraw_stake(sid, now=None),
    lambda st, sid, fid: st.keep_stake(sid, now="2026-10-28T22:30:00Z"),
    lambda st, sid, fid: st.unpublish_auto("nope", now=NOW),
    lambda st, sid, fid: st.adopt_as_proposal(sid, None, now=NOW),
    lambda st, sid, fid: st.withdraw_stakes_for_finding(7, now=NOW),
])
@pytest.mark.asyncio
async def test_stake_writes_refuse_bad_ids_and_naive_times_before_any_io(store, fake, call):
    f = await new_finding(store)
    s = await new_stake(store, f)
    fake.clear_calls()
    with pytest.raises(ValueError):
        await call(store, s["id"], f["id"])
    assert fake.calls == []


# ═════════════════════════════════════════════════════════════════════════════════════════
# 8. Studio deletes, logging, and the auto write order end to end
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_studio_delete_always_succeeds_and_leaves_the_finding_with_no_stake(store, fake):
    f, s = await live_auto(store, fake)
    assert await store.update_finding(f["id"], expected_status="new", now=NOW,
                                      changes={"status": "published", "stake_id": s["id"]})
    assert fake.studio_delete_stake(s["id"]) and not fake.studio_delete_stake(s["id"])
    gone = await store.read_finding(f["id"])
    assert gone["status"] == "published" and gone["stake_id"] is None
    assert await store.read_stake_by_finding(f["id"]) is None
    with pytest.raises(StakeStoreError):          # a link to a deleted stake is refused by the FK
        await store.update_finding(f["id"], expected_status="published", changes={"stake_id": s["id"]}, now=LATER)


@pytest.mark.asyncio
async def test_log_lines_and_errors_never_carry_row_values(store, fake, caplog):
    f, s = await live_auto(store, fake)
    caplog.set_level(logging.DEBUG, logger=SS.__name__)
    fake.fail(STAKES, "update", when="before", exc=api_error_23514())
    with pytest.raises(StakeStoreError) as info:
        await store.unpublish_auto(s["id"], now=NOW)
    seen = caplog.text + str(info.value)
    assert "SENTINEL-VALUE" not in seen and "Probe Holdings" not in seen
    assert s["id"] in caplog.text and "[tc-stakes-store]" in caplog.text
    assert "code='23514'" in str(info.value)
    # a REAL refusal: Postgres' 23505 details name the key values ("Probe Holdings")
    g = await new_finding(store, accession=acc(8))
    with pytest.raises(StakeConflict) as info:
        await new_stake(store, g)
    assert "Probe Holdings" in (info.value.cause.details or ""), "the fake's details carry the row (non-vacuity)"
    assert "Probe Holdings" not in caplog.text + str(info.value)


@pytest.mark.asyncio
async def test_the_auto_write_order_never_republishes_a_row_withdrawn_mid_flight(store, fake):
    """write-ahead finding → unpublished INSERT → staged → noted → flip → published, with the
    owner's Withdraw landing between the flip's read and its UPDATE."""
    f, created = await store.insert_finding(finding_row(), now=NOW)
    assert created and f["status"] == "new"
    s = await store.insert_stake(stake_row(f["id"]), now=NOW)
    assert await store.update_finding(f["id"], expected_status="new", now=NOW,
                                      changes={"status": "staged", "stake_id": s["id"]})
    assert await store.update_finding(f["id"], expected_status="staged", now=NOW,
                                      changes={"status": "noted", "telegram_message_id": 99, "notified_at": NOW})
    current = await store.read_stake_by_finding(f["id"])
    assert current["published"] is False and current["withdrawn_at"] is None   # the flip's read

    async def owner_withdraws_now() -> None:
        assert await store.withdraw_stake(s["id"], now=NOW)

    def tap(db: FakeSupabase) -> None:
        db.admin_update(STAKES, {"id": s["id"]}, {"published": False, "withdrawn_at": "2026-10-28T22:30:01Z"})

    fake.on_before(STAKES, "update", tap, match=lambda st: st.payload.get("published") is True)
    assert await store.publish_stake(s["id"], f["id"], now=NOW) is False
    assert fake.stake(s["id"])["published"] is False
    # a reconcile pass that tries again never revives it, lost answer or not
    fake.fail(STAKES, "update", when="after")
    assert await store.publish_stake(s["id"], f["id"], now=LATER) is False
    assert fake.stake(s["id"])["published"] is False and fake.stake(s["id"])["withdrawn_at"]
    await owner_withdraws_now()                   # idempotent: the owner's second tap is fine
    assert fake.pending_injections() == []
