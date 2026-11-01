"""The Wiser weekly ledger (`app/services/learn_weekly/ledger.py`) and its migration (204).

Hermetic: every test runs the REAL ledger against an in-memory PostgREST fake (the approach of
`test_marketing_run_service.py`) that also enforces migration 204's UNIQUE keys, its foreign key and
every named CHECK — so a ledger write that production would refuse with a 23514 fails HERE first.
`test_the_fake_models_every_named_check_in_the_migration` keeps the two in step.

What is pinned, and why it matters:
  * the migration's CHECK lists equal `contracts.PIECE_STATUSES / JOB_STATUSES / CATEGORIES /
    ENGINES` and its column lists equal the ledger's (a drift = a prod-only 23514 / PGRST204);
  * every compare-and-swap loses cleanly (None, row untouched) to a concurrent writer;
  * first-write-wins on the period and on (piece, round), with frozen fields never rewritten;
  * a dry-run piece never reaches `publishing` / `published`; Approve and publish need the hold open;
  * the narration claim fence, the two caps (worker failures, lease re-claims), cancel and replay.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
from postgrest.exceptions import APIError

from _migration_sql import strip_sql_comments
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import ledger
from app.services.learn_weekly.ledger import LearnLedgerConflict, LearnLedgerError

_BACKEND = Path(__file__).resolve().parents[1]
_MIGRATION = _BACKEND / "database" / "migrations" / "204_learn_weekly_pipeline.sql"

UTC = timezone.utc
T0 = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)          # Fri 2026-10-23 23:00 ET
HOLD = T0 + timedelta(hours=48)
KEY = "2026-W43:deep_dive"
SHA = "a" * 64


# ── the migration, read once ────────────────────────────────────────────────────────────────


def _sql() -> str:
    return strip_sql_comments(_MIGRATION.read_text(encoding="utf-8"))


def _quoted(group: str) -> tuple:
    return tuple(re.findall(r"'([^']*)'", group))


def _check_list(sql: str, pattern: str) -> tuple:
    m = re.search(pattern, sql, re.S | re.I)
    assert m, f"migration 204 no longer has the CHECK matching {pattern!r}"
    return _quoted(m.group(1))


def _table_body(sql: str, table: str) -> str:
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", sql, re.S)
    assert m, f"migration 204 no longer creates public.{table}"
    return m.group(1)


_TYPES = r"(?:UUID|TEXT|TIMESTAMPTZ|BOOLEAN|INTEGER|JSONB|BIGINT|DOUBLE PRECISION)"


def _columns(sql: str, table: str) -> set:
    return set(re.findall(rf"^\s{{4}}([a-z_0-9]+)\s+{_TYPES}\b", _table_body(sql, table), re.M))


def _sql_period_regexes(sql: str) -> Dict[str, "re.Pattern[str]"]:
    """The CHECK's two POSIX regexes, translated for Python: Postgres `$` matches only at the end of
    the string, Python's also before a trailing newline — so `$` becomes `\\Z`."""
    out = {}
    for kind in c.KINDS:
        m = re.search(rf"kind = '{kind}'\s+AND period_key ~ '([^']+)'", sql)
        assert m, f"the period_key CHECK lost its {kind} branch"
        pat = m.group(1)
        assert pat.endswith("$")
        out[kind] = re.compile(pat[:-1] + r"\Z")
    return out


_SQL_PERIOD = _sql_period_regexes(_sql())


# ── an in-memory PostgREST fake that enforces 204's constraints ─────────────────────────────


def _api_error(code: str, message: str) -> APIError:
    # What postgrest-py raises for a constraint violation (a str code on the PostgREST path).
    return APIError({"code": code, "message": message, "details": None, "hint": None})


def _ts(value: Any) -> Optional[datetime]:
    return ledger._parse_ts(value) if isinstance(value, str) and "T" in value else None


def _eq(stored: Any, wanted: Any) -> bool:
    if isinstance(stored, bool):
        return str(wanted).lower() == ("true" if stored else "false")
    if isinstance(stored, (dict, list)):
        try:
            return json.loads(wanted) == stored
        except (TypeError, ValueError):
            return False
    a, b = _ts(stored), _ts(wanted)
    if a is not None and b is not None:
        return a == b
    return str(stored) == str(wanted)


def _cmp(stored: Any, wanted: Any, op: str) -> bool:
    if stored is None:  # SQL: NULL matches no comparison
        return False
    a, b = _ts(stored), _ts(wanted)
    if a is None or b is None:
        try:
            a, b = float(stored), float(wanted)
        except (TypeError, ValueError):
            a, b = str(stored), str(wanted)
    return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]


_HEX64 = re.compile(r"[0-9a-f]{64}")


def _len_ok(v: Any, lo: int, hi: int) -> bool:
    return v is None or lo <= len(v) <= hi


def _piece_violations(r: Dict[str, Any]) -> List[str]:
    out = []
    if r["kind"] not in ("deep_dive", "slot"):
        out.append("learn_weekly_pieces_kind_check")
    rx = _SQL_PERIOD.get(r["kind"])
    if rx is None or not rx.match(r["period_key"] or ""):
        out.append("learn_weekly_pieces_period_key_check")
    if r["category"] is not None and r["category"] not in ("blueprints", "valueTraps", "battles"):
        out.append("learn_weekly_pieces_category_check")
    if r["status"] not in c.PIECE_STATUSES:
        out.append("learn_weekly_pieces_status_check")
    if ledger._parse_ts(r["hold_until"]) < ledger._parse_ts(r["scheduled_publish_at"]):
        out.append("learn_weekly_pieces_hold_after_publish")
    if r["dry_run"] and r["status"] in ("publishing", "published"):
        out.append("learn_weekly_pieces_dry_run_never_publishes")
    if any(r[k] < 0 for k in ("generations", "content_rejections", "writer_failures",
                              "build_attempts", "tokens_used", "review_round")):
        out.append("learn_weekly_pieces_counts_check")
    if not (isinstance(r["progress"], dict) and isinstance(r["subjects"], list)
            and isinstance(r["review_message_ids"], list)
            and all(r[k] is None or isinstance(r[k], dict)
                    for k in ("topic", "fact_sheet", "draft", "validation"))):
        out.append("learn_weekly_pieces_json_shapes")
    if any(r[k] is not None and not _HEX64.fullmatch(r[k]) for k in ("fact_sheet_sha256", "draft_sha256")):
        out.append("learn_weekly_pieces_sha_check")
    if r["slug"] is not None and not (re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", r["slug"]) and len(r["slug"]) <= 160):
        out.append("learn_weekly_pieces_slug_check")
    if not (_len_ok(r["title"], 1, 300) and _len_ok(r["reviewed_by"], 0, 200)
            and _len_ok(r["reject_reason"], 0, 2000) and _len_ok(r["last_error"], 0, 2000)
            and _len_ok(r["last_stage"], 0, 100)):
        out.append("learn_weekly_pieces_text_caps")
    return out


def _job_violations(r: Dict[str, Any]) -> List[str]:
    out = []
    if r["round"] < 1:
        out.append("learn_narration_jobs_round_check")
    if r["status"] not in c.JOB_STATUSES:
        out.append("learn_narration_jobs_status_check")
    if r["engine"] not in ("chatterbox", "kokoro"):
        out.append("learn_narration_jobs_engine_check")
    if min(r["attempts"], r["worker_failures"], r["lease_reclaims"]) < 0:
        out.append("learn_narration_jobs_counts_check")
    if not (isinstance(r["script"], (list, dict)) and _HEX64.fullmatch(r["script_sha256"] or "")):
        out.append("learn_narration_jobs_script_check")
    if not (1 <= len(r["voice"]) <= 80 and 1 <= len(r["pipeline_version"]) <= 80
            and (r["claim_nonce"] is None or re.fullmatch(r"[0-9a-f]{16}", r["claim_nonce"]))
            and _len_ok(r["error"], 0, 2000)):
        out.append("learn_narration_jobs_text_caps")
    if r["status"] == "in_progress" and (r["lease_until"] is None or r["claim_nonce"] is None):
        out.append("learn_narration_jobs_claimed_has_lease")
    if r["status"] == "ready" and None in (r["audio_path"], r["audio_bytes"], r["duration_seconds"]):
        out.append("learn_narration_jobs_ready_has_audio")
    if (r["audio_bytes"] is not None and r["audio_bytes"] <= 0) or (
            r["duration_seconds"] is not None and r["duration_seconds"] <= 0):
        out.append("learn_narration_jobs_result_ranges")
    return out


#: Every named CHECK the fake models — pinned equal to the migration's named constraints.
FAKE_CHECKS = frozenset({
    "learn_weekly_pieces_kind_check", "learn_weekly_pieces_period_key_check",
    "learn_weekly_pieces_category_check", "learn_weekly_pieces_status_check",
    "learn_weekly_pieces_hold_after_publish", "learn_weekly_pieces_dry_run_never_publishes",
    "learn_weekly_pieces_counts_check", "learn_weekly_pieces_json_shapes",
    "learn_weekly_pieces_sha_check", "learn_weekly_pieces_slug_check",
    "learn_weekly_pieces_text_caps",
    "learn_narration_jobs_round_check", "learn_narration_jobs_status_check",
    "learn_narration_jobs_engine_check", "learn_narration_jobs_counts_check",
    "learn_narration_jobs_script_check", "learn_narration_jobs_text_caps",
    "learn_narration_jobs_claimed_has_lease", "learn_narration_jobs_ready_has_audio",
    "learn_narration_jobs_result_ranges",
})


class _Table:
    def __init__(self, fake: "FakeSupabase", name: str, columns, defaults, unique, check):
        self.fake, self.name = fake, name
        self.columns, self.defaults, self.unique, self.check = columns, defaults, unique, check
        self.rows: List[Dict[str, Any]] = []

    def select(self, *_):
        return _Query(self, "select")

    def insert(self, payload):
        return _Query(self, "insert", payload)

    def upsert(self, payload, *, on_conflict="", ignore_duplicates=False, **_):
        q = _Query(self, "upsert", payload)
        q.on_conflict = tuple(x.strip() for x in on_conflict.split(",") if x.strip())
        q.ignore = ignore_duplicates
        return q

    def update(self, payload):
        return _Query(self, "update", payload)


class _Query:
    def __init__(self, table: _Table, op: str, payload=None):
        self.t, self.op, self.payload = table, op, payload
        self.filters: List[Callable[[Dict[str, Any]], bool]] = []
        self.order_by: List[tuple] = []
        self.n: Optional[int] = None
        self.neg = False
        self.on_conflict: tuple = ()
        self.ignore = False

    def select(self, *_):
        return self

    @property
    def not_(self):
        self.neg = True
        return self

    def _plain(self):
        assert not self.neg, "the fake models not_ only in front of is_ and in_"

    def eq(self, col, val):
        self._plain()
        assert col in self.t.columns, f"{self.t.name} has no column {col}"
        self.filters.append(lambda r: r.get(col) is not None and _eq(r[col], val))
        return self

    def in_(self, col, values):
        assert col in self.t.columns, col
        neg, self.neg = self.neg, False
        wanted = [str(v) for v in values]
        self.filters.append(lambda r: r.get(col) is not None and ((str(r[col]) in wanted) != neg))
        return self

    def is_(self, col, val):
        assert val == "null" and col in self.t.columns, (col, val)
        neg, self.neg = self.neg, False
        self.filters.append(lambda r: (r.get(col) is None) != neg)
        return self

    def _op(self, op, col, val):
        self._plain()
        assert col in self.t.columns, col
        self.filters.append(lambda r: _cmp(r.get(col), val, op))
        return self

    def lt(self, col, val):
        return self._op("lt", col, val)

    def lte(self, col, val):
        return self._op("lte", col, val)

    def gt(self, col, val):
        return self._op("gt", col, val)

    def gte(self, col, val):
        return self._op("gte", col, val)

    def order(self, col, *, desc=False, **_):
        self.order_by.append((col, desc))
        return self

    def limit(self, n):
        self.n = n
        return self

    def execute(self):
        return self.t.fake.execute(self)


class _Result:
    def __init__(self, data):
        self.data = data


def _wire(row):
    """A copy as the wire would carry it (no shared mutable state between the fake and the caller)."""
    return copy.deepcopy(row)


class FakeSupabase:
    def __init__(self):
        self.lock = threading.Lock()
        self._tick = 0
        self.failures: List[tuple] = []           # (table, op, exception): raised by the next match
        self.before_update: Dict[str, List[Callable[[], None]]] = {}
        self.select_barrier: Optional[threading.Barrier] = None
        self.calls: List[tuple] = []
        piece_defaults = {
            "status": "planned", "dry_run": True, "generations": 0, "content_rejections": 0,
            "writer_failures": 0, "build_attempts": 0, "progress": dict, "subjects": list,
            "tokens_used": 0, "review_round": 0, "review_message_ids": list,
        }
        job_defaults = {"status": "queued", "attempts": 0, "worker_failures": 0, "lease_reclaims": 0}
        self.tables = {
            ledger.PIECES: _Table(self, ledger.PIECES, ledger.PIECE_COLUMNS, piece_defaults,
                                  [("period_key",), ("slug",)], _piece_violations),
            ledger.JOBS: _Table(self, ledger.JOBS, ledger.JOB_COLUMNS, job_defaults,
                                [("piece_id", "round")], _job_violations),
        }

    def table(self, name):
        return self.tables[name]

    # helpers ---------------------------------------------------------------------------------
    def _now_text(self) -> str:
        self._tick += 1
        return ledger._iso(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=self._tick))

    def row(self, table: str, row_id: str) -> Dict[str, Any]:
        return next(r for r in self.tables[table].rows if r["id"] == row_id)

    def fail_next(self, table: str, op: str, exc: Exception) -> None:
        self.failures.append((table, op, exc))

    def _validate(self, t: _Table, row: Dict[str, Any], others: List[Dict[str, Any]]) -> None:
        unknown = set(row) - set(t.columns)
        assert not unknown, f"PGRST204: {t.name} has no column(s) {sorted(unknown)}"
        for cols in t.unique:
            key = tuple(row.get(k) for k in cols)
            if None in key:
                continue  # SQL: NULLs are distinct
            if any(tuple(o.get(k) for k in cols) == key for o in others):
                raise _api_error("23505", f"duplicate key value violates unique constraint {cols}")
        if t.name == ledger.JOBS and not any(p["id"] == row["piece_id"] for p in self.tables[ledger.PIECES].rows):
            raise _api_error("23503", "insert or update violates foreign key constraint learn_narration_jobs_piece_id_fkey")
        bad = t.check(row)
        if bad:
            raise _api_error("23514", f'new row violates check constraint "{bad[0]}"')

    # execution -------------------------------------------------------------------------------
    def execute(self, q: _Query):
        for i, (tname, op, exc) in enumerate(self.failures):
            if tname == q.t.name and op in (q.op, "*"):
                del self.failures[i]
                raise exc
        if q.op == "select" and self.select_barrier is not None:
            self.select_barrier.wait(timeout=5)
        if q.op == "update":
            hooks = self.before_update.get(q.t.name) or []
            if hooks:
                hooks.pop(0)()
        with self.lock:
            self.calls.append((q.t.name, q.op))
            return getattr(self, "_" + q.op)(q)

    def _select(self, q: _Query):
        rows = [r for r in q.t.rows if all(f(r) for f in q.filters)]
        for col, desc in reversed(q.order_by):
            present = sorted((r for r in rows if r.get(col) is not None),
                             key=lambda r: (_ts(r[col]) or r[col]) if isinstance(r[col], str) else r[col],
                             reverse=desc)
            absent = [r for r in rows if r.get(col) is None]
            rows = absent + present if desc else present + absent  # ASC NULLS LAST, DESC NULLS FIRST
        rows = rows if q.n is None else rows[: q.n]
        return _Result([_wire(r) for r in rows])

    def _new_row(self, t: _Table, payload: Dict[str, Any]) -> Dict[str, Any]:
        row = {col: None for col in t.columns}
        for k, v in t.defaults.items():
            row[k] = v() if callable(v) else v
        row["id"] = str(uuid.uuid4())
        row["created_at"] = row["updated_at"] = self._now_text()
        row.update(copy.deepcopy(payload))
        return row

    def _insert(self, q: _Query):
        row = self._new_row(q.t, q.payload)
        self._validate(q.t, row, q.t.rows)
        q.t.rows.append(row)
        return _Result([_wire(row)])

    def _upsert(self, q: _Query):
        assert q.ignore, "the ledger only ever upserts with ignore_duplicates"
        row = self._new_row(q.t, q.payload)
        key = tuple(row.get(k) for k in q.on_conflict)
        if any(tuple(o.get(k) for k in q.on_conflict) == key for o in q.t.rows):
            return _Result([])  # ON CONFLICT (…) DO NOTHING: the ignored row is not returned
        self._validate(q.t, row, q.t.rows)
        q.t.rows.append(row)
        return _Result([_wire(row)])

    def _update(self, q: _Query):
        matched = [r for r in q.t.rows if all(f(r) for f in q.filters)]
        staged = []
        for r in matched:
            new = dict(r)
            new.update(copy.deepcopy(q.payload))
            self._validate(q.t, new, [o for o in q.t.rows if o is not r])
            staged.append((r, new))
        for r, new in staged:  # all or nothing, like one UPDATE statement
            r.clear()
            r.update(new)
        return _Result([_wire(r) for r, _ in staged])


@pytest.fixture
def fake():
    return FakeSupabase()


def _piece(fake, key=KEY, *, dry_run=False, kind=None, sched=T0, hold=HOLD, category=None):
    kind = kind or ("deep_dive" if key.endswith("deep_dive") else "slot")
    row = ledger.create_piece_sync(key, kind, scheduled_publish_at=sched, hold_until=hold,
                                   dry_run=dry_run, category=category, sb=fake)
    assert row is not None
    return row


def _walk(fake, pid, path, **kw):
    """Drive a piece along `path` (each hop one transition), asserting each one lands."""
    row = None
    for frm, to in zip(path, path[1:]):
        extra = {}
        if to in (c.APPROVED, c.PUBLISHING):
            extra["within_hold_at"] = T0
        if to == c.PUBLISHING:
            extra["due_at"] = T0
        row = ledger.transition_sync(pid, from_statuses=[frm], to_status=to, sb=fake, **extra, **kw)
        assert row is not None and row["status"] == to, (frm, to)
    return row


MAIN_PATH = [c.PLANNED, c.TOPIC_PENDING, c.BUILDING, c.DRAFTED, c.MEDIA_PENDING,
             c.READY_FOR_REVIEW, c.IN_REVIEW, c.APPROVED]


# ── the migration ↔ contracts ↔ ledger ↔ fake ─────────────────────────────────────────────────


def test_the_migration_check_lists_equal_the_contracts():
    sql = _sql()
    assert _check_list(sql, r"CONSTRAINT\s+learn_weekly_pieces_status_check\s+CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)") \
        == c.PIECE_STATUSES
    assert _check_list(sql, r"CONSTRAINT\s+learn_weekly_pieces_kind_check\s+CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)") \
        == c.KINDS
    assert _check_list(sql, r"CONSTRAINT\s+learn_weekly_pieces_category_check\s+CHECK\s*\(\s*category\s+IS\s+NULL\s+"
                            r"OR\s+category\s+IN\s*\(([^)]*)\)") == c.CATEGORIES
    assert _check_list(sql, r"CONSTRAINT\s+learn_narration_jobs_status_check\s+CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)") \
        == c.JOB_STATUSES
    assert _check_list(sql, r"CONSTRAINT\s+learn_narration_jobs_engine_check\s+CHECK\s*\(\s*engine\s+IN\s*\(([^)]*)\)") \
        == c.ENGINES
    assert _check_list(sql, r"ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'bundle'\s+CHECK\s*\(\s*source\s+IN\s*"
                            r"\(([^)]*)\)") == ("bundle", "weekly")


def test_the_migration_columns_equal_the_ledgers():
    """A column the ledger writes that the table lacks is a PostgREST PGRST204 in production; a
    column the table has that the ledger does not know cannot be written or fenced."""
    sql = _sql()
    assert _columns(sql, "learn_weekly_pieces") == set(ledger.PIECE_COLUMNS)
    assert _columns(sql, "learn_narration_jobs") == set(ledger.JOB_COLUMNS)


def test_the_migration_carries_the_hold_dry_run_and_index_rules():
    sql = _sql()
    assert re.search(r"CONSTRAINT\s+learn_weekly_pieces_hold_after_publish\s+CHECK\s*\(\s*hold_until\s*>=\s*"
                     r"scheduled_publish_at\s*\)", sql)
    assert re.search(r"CHECK\s*\(\s*NOT\s*\(\s*dry_run\s+AND\s+status\s+IN\s*\(\s*'publishing',\s*'published'\s*\)", sql)
    assert re.search(r"ON public\.learn_weekly_pieces \(status, scheduled_publish_at\);", sql)
    assert re.search(r"ON public\.learn_weekly_pieces \(kind, published_at DESC\);", sql)
    assert re.search(r"ON public\.learn_narration_jobs \(status, lease_until\);", sql)
    assert re.search(r"CONSTRAINT learn_narration_jobs_piece_round UNIQUE \(piece_id, round\)", sql)
    assert re.search(r"piece_id\s+UUID\s+NOT NULL REFERENCES public\.learn_weekly_pieces \(id\) ON DELETE CASCADE", sql)
    assert re.search(r"period_key\s+TEXT\s+NOT NULL UNIQUE", sql)
    assert re.search(r"slug\s+TEXT\s+UNIQUE", sql)
    assert sql.lstrip().startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")


def test_the_migration_closes_money_move_articles_and_guards_duplicate_titles():
    sql = _sql()
    assert re.search(r"DROP POLICY IF EXISTS \"money_moves_select_all\" ON public\.money_move_articles;", sql)
    assert re.search(r"REVOKE ALL ON public\.money_move_articles FROM anon, authenticated;", sql)
    assert re.search(r"GRANT ALL ON public\.money_move_articles TO service_role;", sql)
    assert re.search(r"CREATE POLICY \"money_moves_service_all\" ON public\.money_move_articles\s+FOR ALL TO "
                     r"service_role USING \(true\) WITH CHECK \(true\);", sql)
    # The duplicate guard RAISEs, and runs BEFORE the unique index it protects.
    guard = re.search(r"HAVING count\(\*\) > 1.*?RAISE EXCEPTION", sql, re.S)
    index = re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS idx_money_move_articles_title_lower\s+ON "
                      r"public\.money_move_articles \(lower\(title\)\);", sql)
    assert guard and index and guard.start() < index.start()
    for t in ("learn_weekly_pieces", "learn_narration_jobs"):
        assert re.search(rf"ALTER TABLE public\.{t}\s+ENABLE ROW LEVEL SECURITY;", sql)
        assert re.search(rf"REVOKE ALL ON public\.{t}\s+FROM anon, authenticated;", sql)
        assert re.search(rf"GRANT ALL ON public\.{t}\s+TO service_role;", sql)
        assert re.search(rf"CREATE POLICY \"{t}_service_all\" ON public\.{t}\s+FOR ALL TO service_role", sql)
        assert re.search(rf"COMMENT ON TABLE public\.{t} IS", sql)


def test_the_migration_seeds_the_three_job_rows():
    sql = _sql()
    m = re.search(r"INSERT INTO public\.notification_job_state \(job, enabled, updated_at\)\s+VALUES(.*?)"
                  r"ON CONFLICT \(job\) DO NOTHING;", sql, re.S)
    assert m
    jobs = re.findall(r"\('([a-z_]+)',\s*TRUE,\s*NOW\(\)\)", m.group(1))
    assert jobs == ["learn_weekly_topic", "learn_weekly_build", "learn_weekly_deadman"]


def test_the_fake_models_every_named_check_in_the_migration():
    """The fake is only a guard if it refuses what production refuses — so its CHECK set must equal
    the migration's named CHECK constraints (the one UNIQUE constraint aside)."""
    sql = _sql()
    named = set(re.findall(r"CONSTRAINT\s+(learn_[a-z_]+)\s+CHECK\b", sql))
    assert named == FAKE_CHECKS


def test_every_key_the_ledger_accepts_passes_the_sql_check():
    """The ledger validates the calendar (stricter); the SQL CHECK is a coarse regex. Anything the
    ledger lets through must pass the CHECK, or it would 23514 in production."""
    for key in ("2026-W01:deep_dive", "2026-W43:deep_dive", "2026-W53:deep_dive", "2020-W53:deep_dive",
                "2026-10-26:slot", "2026-12-31:slot", "2028-02-29:slot"):
        ledger.validate_period_key(key)
        kind = "deep_dive" if key.endswith("deep_dive") else "slot"
        assert _SQL_PERIOD[kind].match(key), key
    for bad in ("2026-W00:deep_dive", "2026-W54:deep_dive", "2026-13-01:slot", "2026-10-32:slot",
                "2026-W43:deep_dive\n", "2026-10-26:slot\n"):
        kind = "deep_dive" if "deep_dive" in bad else "slot"
        assert not _SQL_PERIOD[kind].match(bad), bad
        with pytest.raises(ValueError):
            ledger.validate_period_key(bad)


def test_the_new_tables_are_declared_service_role_only_and_curated():
    import test_table_grants_service_role_only as grants

    service_only = {t for group in grants._SERVICE_ROLE_ONLY.values() for t in group}
    assert {"learn_weekly_pieces", "learn_narration_jobs", "money_move_articles"} <= service_only
    assert "money_move_articles" not in grants._CLIENT_ACCESS_BY_DESIGN
    import sys

    sys.path.insert(0, str(_BACKEND / "scripts"))
    try:
        import schema_curation as cur
    finally:
        sys.path.remove(str(_BACKEND / "scripts"))
    sql = _sql()
    for t in ("learn_weekly_pieces", "learn_narration_jobs"):
        doc = cur.CURATION[f"public.{t}"]
        assert doc.domain in cur.DOMAIN_ORDER and doc.purpose and "204" in doc.note
        assert doc.key and set(doc.key) <= _columns(sql, t), set(doc.key) - _columns(sql, t)


# ── period keys ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key,why", [
    ("2025-W53:deep_dive", "2025 has 52 ISO weeks"),
    ("2026-02-30:slot", "no such date"),
    ("0000-W01:deep_dive", "year 0"),
    ("٢٠٢٦-W43:deep_dive", "non-ASCII digits pass contracts' \\d"),
    ("2026-W43:deep_dive\n", "a trailing newline passes contracts' $"),
    ("2026-W43:slot", "a week key with the slot suffix"),
    ("", "empty"),
    (None, "not a string"),
    (202643, "not a string"),
])
def test_validate_period_key_refuses(key, why):
    with pytest.raises(ValueError):
        ledger.validate_period_key(key)


def test_validate_period_key_checks_the_kind():
    assert ledger.validate_period_key(KEY, "deep_dive") == KEY
    with pytest.raises(ValueError):
        ledger.validate_period_key(KEY, "slot")
    with pytest.raises(ValueError):
        ledger.validate_period_key("2026-10-26:slot", "deep_dive")


# ── create_piece: first write wins ──────────────────────────────────────────────────────────


def test_create_piece_creates_a_planned_row(fake):
    row = _piece(fake, dry_run=True)
    assert row["status"] == c.PLANNED and row["period_key"] == KEY and row["kind"] == "deep_dive"
    assert row["dry_run"] is True and row["generations"] == 0 and row["generation_id"] is None
    assert ledger._parse_ts(row["scheduled_publish_at"]) == T0 and ledger._parse_ts(row["hold_until"]) == HOLD


def test_create_piece_is_first_write_wins_and_never_rewrites_frozen_fields(fake, caplog):
    first = _piece(fake, dry_run=True)
    with caplog.at_level("WARNING"):
        second = ledger.create_piece_sync(KEY, "deep_dive", scheduled_publish_at=T0 + timedelta(hours=1),
                                          hold_until=HOLD + timedelta(hours=1), dry_run=False, sb=fake)
    assert second["id"] == first["id"]
    assert second["dry_run"] is True, "a second create must never flip a frozen dry run"
    assert ledger._parse_ts(second["scheduled_publish_at"]) == T0
    assert len(fake.tables[ledger.PIECES].rows) == 1
    assert "first write wins" in caplog.text and "dry_run" in caplog.text


def test_create_piece_adopts_silently_when_identical(fake, caplog):
    first = _piece(fake)
    with caplog.at_level("WARNING"):
        again = _piece(fake)
    assert again["id"] == first["id"] and "first write wins" not in caplog.text


def test_two_concurrent_creates_make_one_row(fake):
    results: List[Optional[dict]] = [None, None]

    def go(i):
        results[i] = ledger.create_piece_sync(KEY, "deep_dive", scheduled_publish_at=T0, hold_until=HOLD,
                                              dry_run=False, sb=fake)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results[0]["id"] == results[1]["id"]
    assert len(fake.tables[ledger.PIECES].rows) == 1


@pytest.mark.parametrize("kwargs,why", [
    (dict(scheduled_publish_at=T0.replace(tzinfo=None)), "naive datetime"),
    (dict(hold_until=T0 - timedelta(seconds=1)), "hold before publish"),
    (dict(dry_run=1), "an int is not a bool"),
    (dict(dry_run=None), "None is not a bool"),
    (dict(category="valuetraps"), "unknown category"),
    (dict(scheduled_publish_at="2026-10-24T03:00:00Z"), "a string is not a datetime"),
])
def test_create_piece_refuses_bad_input_before_any_io(fake, kwargs, why):
    args = dict(scheduled_publish_at=T0, hold_until=HOLD, dry_run=False)
    args.update(kwargs)
    with pytest.raises(ValueError):
        ledger.create_piece_sync(KEY, "deep_dive", sb=fake, **args)
    assert fake.calls == [], why


def test_create_piece_refuses_a_kind_that_does_not_match_the_key(fake):
    with pytest.raises(ValueError):
        ledger.create_piece_sync(KEY, "slot", scheduled_publish_at=T0, hold_until=HOLD, dry_run=False, sb=fake)
    assert fake.calls == []


def test_hold_equal_to_publish_is_allowed(fake):
    row = ledger.create_piece_sync("2026-10-26:slot", "slot", scheduled_publish_at=T0, hold_until=T0,
                                   dry_run=False, category="battles", sb=fake)
    assert row["category"] == "battles"


def test_a_create_whose_read_back_is_empty_is_none_and_loud(fake, caplog, monkeypatch):
    real = fake._select
    monkeypatch.setattr(fake, "_select", lambda q: _Result([]) if q.t.name == ledger.PIECES else real(q))
    with caplog.at_level("ERROR"):
        assert ledger.create_piece_sync(KEY, "deep_dive", scheduled_publish_at=T0, hold_until=HOLD,
                                        dry_run=False, sb=fake) is None
    assert "reads back as MISSING" in caplog.text


class _OddClient:
    """A client whose every statement answers `data` — an unreadable PostgREST response."""

    def __init__(self, data):
        self.data = data

    def table(self, _name):
        return self

    def __getattr__(self, _name):  # select / eq / limit / … all chain back to this object
        return lambda *a, **k: self

    def execute(self):
        return _Result(self.data)


@pytest.mark.parametrize("data", ["garbage", 42, [1, 2], [{"id": "x"}, "y"]])
def test_an_unreadable_answer_is_a_ledger_error_not_a_row(data):
    with pytest.raises(LearnLedgerError):
        ledger.get_piece_by_id_sync(str(uuid.uuid4()), sb=_OddClient(data))


def test_a_single_dict_answer_is_read_as_one_row():
    row = {"id": "x", "status": c.PLANNED}
    assert ledger.get_piece_by_id_sync(str(uuid.uuid4()), sb=_OddClient(row)) == row
    assert ledger.get_piece_by_id_sync(str(uuid.uuid4()), sb=_OddClient(None)) is None


def test_a_ledger_failure_is_typed_and_names_the_operation(fake):
    fake.fail_next(ledger.PIECES, "upsert", RuntimeError("edge 520"))
    with pytest.raises(LearnLedgerError) as e:
        _piece(fake)
    assert "create_piece" in str(e.value) and KEY in str(e.value) and "RuntimeError" in str(e.value)


# ── reads ────────────────────────────────────────────────────────────────────────────────────


def test_get_piece_by_id_with_a_malformed_id_is_none_without_a_query(fake, caplog):
    with caplog.at_level("WARNING"):
        assert ledger.get_piece_by_id_sync("not-a-uuid", sb=fake) is None
        assert ledger.get_piece_by_id_sync(None, sb=fake) is None
        assert ledger.get_piece_by_id_sync(12345, sb=fake) is None
    assert fake.calls == [] and "not a UUID" in caplog.text


def test_get_piece_refuses_a_malformed_key(fake):
    with pytest.raises(ValueError):
        ledger.get_piece_sync("2026-43:deep_dive", sb=fake)


def test_list_pieces_filters_and_orders_oldest_due_first(fake):
    late = _piece(fake, "2026-W44:deep_dive", sched=T0 + timedelta(days=7), hold=HOLD + timedelta(days=7))
    early = _piece(fake)
    slot = _piece(fake, "2026-10-26:slot", sched=T0 + timedelta(days=2), hold=HOLD + timedelta(days=2))
    assert [r["id"] for r in ledger.list_pieces_sync(sb=fake)] == [early["id"], slot["id"], late["id"]]
    assert [r["id"] for r in ledger.list_pieces_sync(kind="deep_dive", sb=fake)] == [early["id"], late["id"]]
    _walk(fake, early["id"], [c.PLANNED, c.TOPIC_PENDING])
    assert [r["id"] for r in ledger.list_pieces_sync(statuses=[c.TOPIC_PENDING], sb=fake)] == [early["id"]]
    assert [r["id"] for r in ledger.list_pieces_sync(statuses=c.PLANNED, limit=1, sb=fake)] == [slot["id"]]


@pytest.mark.parametrize("kwargs", [dict(limit=0), dict(limit=True), dict(limit=1001), dict(kind="weekly"),
                                    dict(statuses=["done"]), dict(statuses=[])])
def test_list_pieces_refuses_bad_arguments(fake, kwargs):
    with pytest.raises(ValueError):
        ledger.list_pieces_sync(sb=fake, **kwargs)


# ── transitions ──────────────────────────────────────────────────────────────────────────────


def test_the_whole_main_path_publishes_a_live_piece(fake):
    pid = _piece(fake)["id"]
    _walk(fake, pid, MAIN_PATH + [c.PUBLISHING])
    row = ledger.transition_sync(pid, from_statuses=[c.PUBLISHING], to_status=c.PUBLISHED,
                                 fields={"published_at": T0}, sb=fake)
    assert row["status"] == c.PUBLISHED and ledger._parse_ts(row["published_at"]) == T0
    gone = ledger.transition_sync(pid, from_statuses=[c.PUBLISHED], to_status=c.UNPUBLISHED,
                                  fields={"unpublished_at": T0 + timedelta(days=1)}, sb=fake)
    assert gone["status"] == c.UNPUBLISHED


def test_a_wrong_from_status_loses_and_writes_nothing(fake, caplog):
    pid = _piece(fake)["id"]
    before = copy.deepcopy(fake.row(ledger.PIECES, pid))
    with caplog.at_level("INFO"):
        assert ledger.transition_sync(pid, from_statuses=[c.BUILDING], to_status=c.DRAFTED, sb=fake) is None
    assert fake.row(ledger.PIECES, pid) == before
    assert "lost the compare-and-swap" in caplog.text and "status=planned" in caplog.text


def test_a_fence_mismatch_loses(fake):
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    ledger.acquire_lease_sync(pid, generation_id=gen, lease_seconds=600, from_statuses=[c.PLANNED],
                              to_status=c.BUILDING, now=T0, sb=fake)
    stale = str(uuid.uuid4())
    assert ledger.transition_sync(pid, from_statuses=[c.BUILDING], to_status=c.DRAFTED,
                                  fence={"generation_id": stale}, sb=fake) is None
    row = ledger.transition_sync(pid, from_statuses=[c.BUILDING], to_status=c.DRAFTED,
                                 fence={"generation_id": gen}, fields={"lease_until": None}, sb=fake)
    assert row["status"] == c.DRAFTED and row["lease_until"] is None


def test_a_null_fence_means_is_null(fake):
    pid = _piece(fake)["id"]
    assert ledger.transition_sync(pid, from_statuses=[c.PLANNED], to_status=c.TOPIC_PENDING,
                                  fence={"generation_id": None, "slug": None}, sb=fake) is not None


def test_a_jsonb_fence_compares_the_document(fake):
    pid = _piece(fake)["id"]
    _walk(fake, pid, MAIN_PATH[:7])
    ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.IN_REVIEW,
                           fields={"review_message_ids": [101, 102], "review_round": 1}, sb=fake)
    assert ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED, within_hold_at=T0,
                                  fence={"review_message_ids": [999]}, sb=fake) is None
    assert ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED, within_hold_at=T0,
                                  fence={"review_message_ids": [101, 102], "review_round": 1},
                                  sb=fake)["status"] == c.APPROVED


@pytest.mark.parametrize("frm,to", [
    ([c.PLANNED], "done"), (["bogus"], c.BUILDING), ([], c.BUILDING), ([c.PLANNED], None),
])
def test_unknown_status_names_are_refused_before_any_io(fake, frm, to):
    pid = _piece(fake)["id"]
    n = len(fake.calls)
    with pytest.raises(ValueError):
        ledger.transition_sync(pid, from_statuses=frm, to_status=to, sb=fake)
    assert len(fake.calls) == n


@pytest.mark.parametrize("frm,to", [
    (c.APPROVED, c.PUBLISHED),            # published only from publishing
    (c.BUILDING, c.APPROVED),             # nothing skips the owner's tap
    (c.READY_FOR_REVIEW, c.APPROVED),
    (c.DRAFTED, c.PUBLISHING),
    (c.FAILED, c.BUILDING),               # a terminal piece is left only by a rebuild to planned
    (c.EXPIRED, c.IN_REVIEW),
    (c.REHEARSED, c.PLANNED),             # rehearsed is never left
    (c.REHEARSED, c.APPROVED),            # … through transition(): only promote_rehearsed_sync
    (c.UNPUBLISHED, c.PLANNED),
    (c.PUBLISHING, c.FAILED),             # its row may be live
    (c.PUBLISHED, c.EXPIRED),
    (c.PUBLISHED, c.BUILDING),
    (c.IN_REVIEW, c.REHEARSED),           # the dry-run end only from approved
    (c.APPROVED, c.PLANNED),              # an approved piece is never rewritten behind the tap
    (c.PUBLISHING, c.PLANNED),            # its row may be live
    (c.BUILDING, c.OWNER_REJECTED),
    (c.BUILDING, c.UNPUBLISHED),
])
def test_transition_rules_refuse(frm, to):
    assert ledger.transition_problem([frm], to) is not None
    with pytest.raises(ValueError):
        ledger.transition_sync(str(uuid.uuid4()), from_statuses=[frm], to_status=to,
                               within_hold_at=T0, due_at=T0, sb=FakeSupabase())


@pytest.mark.parametrize("frm,to", [
    (c.FAILED, c.PLANNED), (c.OWNER_REJECTED, c.PLANNED), (c.EXPIRED, c.PLANNED), (c.SKIPPED, c.PLANNED),
    (c.IN_REVIEW, c.BUILDING), (c.IN_REVIEW, c.MEDIA_PENDING), (c.MEDIA_PENDING, c.MEDIA_PENDING),
    (c.PUBLISHING, c.EXPIRED), (c.APPROVED, c.EXPIRED), (c.IN_REVIEW, c.OWNER_REJECTED),
    (c.FAILED, c.FAILED), (c.BUILDING, c.FAILED), (c.PLANNED, c.SKIPPED),
    # The build queue is re-entered by a topic pick, a build retry and Regenerate / Next topic.
    (c.TOPIC_PENDING, c.PLANNED), (c.BUILDING, c.PLANNED), (c.DRAFTED, c.PLANNED),
    (c.MEDIA_PENDING, c.PLANNED), (c.READY_FOR_REVIEW, c.PLANNED), (c.IN_REVIEW, c.PLANNED),
])
def test_transition_rules_allow(frm, to):
    assert ledger.transition_problem([frm], to) is None


def test_a_rebuild_reopens_a_failed_piece(fake):
    pid = _piece(fake)["id"]
    _walk(fake, pid, [c.PLANNED, c.BUILDING, c.FAILED])
    row = ledger.transition_sync(pid, from_statuses=list(c.REBUILDABLE_STATUSES), to_status=c.PLANNED,
                                 fields={"build_attempts": 0, "writer_failures": 0, "last_error": None}, sb=fake)
    assert row["status"] == c.PLANNED


def test_approve_requires_the_hold_open_and_refuses_at_its_boundary(fake):
    pid = _piece(fake)["id"]
    _walk(fake, pid, MAIN_PATH[:7])
    with pytest.raises(ValueError):
        ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED, sb=fake)
    # hold_until > now: AT the instant the hold ends, Approve is "too late".
    assert ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED,
                                  within_hold_at=HOLD, sb=fake) is None
    assert ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED,
                                  within_hold_at=HOLD + timedelta(days=3), sb=fake) is None
    row = ledger.transition_sync(pid, from_statuses=[c.IN_REVIEW], to_status=c.APPROVED,
                                 within_hold_at=HOLD - timedelta(microseconds=1),
                                 fields={"reviewed_by": "owner", "reviewed_at": HOLD - timedelta(seconds=1)}, sb=fake)
    assert row["status"] == c.APPROVED and row["reviewed_by"] == "owner"
    # An annotating write on an approved piece is not an approval: no hold needed.
    assert ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.APPROVED,
                                  fields={"last_stage": "approved"}, sb=fake) is not None


def test_publishing_needs_due_and_the_hold_open(fake):
    pid = _piece(fake)["id"]
    _walk(fake, pid, MAIN_PATH)
    with pytest.raises(ValueError):
        ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING, within_hold_at=T0, sb=fake)
    early = T0 - timedelta(seconds=1)
    assert ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                                  within_hold_at=early, due_at=early, sb=fake) is None
    assert ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                                  within_hold_at=HOLD, due_at=HOLD, sb=fake) is None
    row = ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                                 within_hold_at=T0, due_at=T0, fields={"publish_lease_until": T0 + timedelta(minutes=5)},
                                 sb=fake)
    assert row["status"] == c.PUBLISHING
    # Recovering `publishing` ignores the hold.
    assert ledger.transition_sync(pid, from_statuses=[c.PUBLISHING], to_status=c.PUBLISHED, sb=fake)["status"] \
        == c.PUBLISHED


def test_a_dry_run_piece_never_publishes_and_ends_rehearsed(fake):
    pid = _piece(fake, dry_run=True)["id"]
    _walk(fake, pid, MAIN_PATH)
    assert ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                                  within_hold_at=T0, due_at=T0, sb=fake) is None
    assert fake.row(ledger.PIECES, pid)["status"] == c.APPROVED
    with pytest.raises(ValueError):  # the caller cannot override the dry-run fence
        ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                               within_hold_at=T0, due_at=T0, fence={"dry_run": True}, sb=fake)
    assert ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.REHEARSED, sb=fake)["status"] \
        == c.REHEARSED


def test_a_live_piece_is_never_rehearsed(fake):
    pid = _piece(fake, dry_run=False)["id"]
    _walk(fake, pid, MAIN_PATH)
    assert ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.REHEARSED, sb=fake) is None


def test_the_database_refuses_a_dry_run_publish_even_if_the_fence_were_bypassed(fake):
    """The CHECK is the backstop for the ledger's own fence (the fake enforces it like Postgres)."""
    pid = _piece(fake, dry_run=True)["id"]
    q = fake.table(ledger.PIECES).update({"status": c.PUBLISHED}).eq("id", pid)
    with pytest.raises(APIError) as e:
        q.execute()
    assert "learn_weekly_pieces_dry_run_never_publishes" in str(e.value)


@pytest.mark.parametrize("fields", [
    {"dry_run": False},                                   # frozen at creation
    {"status": c.BUILDING},                               # status is to_status
    {"generation_id": str(uuid.uuid4())},                 # only acquire_lease takes a generation
    {"period_key": "2026-W44:deep_dive"},
    {"nonexistent": 1},
    {"validation": {"score": float("nan")}},              # NaN would be invalid JSON on the wire
    {"progress": {"x": float("inf")}},
    {"progress": None},
    {"progress": ["a"]},
    {"review_round": True},                               # a bool is not an int
    {"tokens_used": -1},
    {"tokens_used": 1.5},
    {"lease_until": T0.replace(tzinfo=None)},             # naive
    {"lease_until": "2026-10-24T03:00:00Z"},              # a string is not a datetime
    {"slug": "Has Spaces"},
    {"slug": "trailing-"},
    {"slug": "x" * 161},
    {"title": "two\nlines"},
    {"title": "   "},
    {"title": "t" * 301},
    {"draft_sha256": "A" * 64},
    {"fact_sheet_sha256": "a" * 63},
    {"category": "Battles"},
    {"subjects": [{"ticker": "NVDA"}, "AMD"]},
    {"review_message_ids": [1, True]},
    {"narration_job_id": "job-1"},
    {"reviewed_by": ""},
    {"last_stage": "x" * 101},
    {"scheduled_publish_at": HOLD, "hold_until": T0},     # hold before publish
])
def test_bad_fields_are_refused_before_any_io(fake, fields):
    pid = _piece(fake)["id"]
    n = len(fake.calls)
    with pytest.raises(ValueError):
        ledger.transition_sync(pid, from_statuses=[c.PLANNED], to_status=c.PLANNED, fields=fields, sb=fake)
    assert len(fake.calls) == n


@pytest.mark.parametrize("fence", [{"status": c.PLANNED}, {"nope": 1}, {"lease_until": "not a time"},
                                   {"tokens_used": float("nan")}, {"progress": object()}])
def test_bad_fences_are_refused(fake, fence):
    pid = _piece(fake)["id"]
    with pytest.raises(ValueError):
        ledger.transition_sync(pid, from_statuses=[c.PLANNED], to_status=c.PLANNED, fence=fence, sb=fake)


def test_diagnostic_text_is_scrubbed_and_capped(fake):
    pid = _piece(fake)["id"]
    row = ledger.transition_sync(pid, from_statuses=[c.PLANNED], to_status=c.FAILED, sb=fake,
                                 fields={"last_error": "line one\nline\x00two\t" + "x" * 5000,
                                         "reject_reason": "tone\r\n  too salesy"})
    assert "\n" not in row["last_error"] and "\x00" not in row["last_error"]
    assert len(row["last_error"]) == ledger.TEXT_CAP and row["last_error"].endswith("…")
    assert row["reject_reason"] == "tone too salesy"


def test_a_slug_another_piece_holds_raises_a_conflict(fake):
    a = _piece(fake)["id"]
    b = _piece(fake, "2026-W44:deep_dive", sched=T0 + timedelta(days=7), hold=HOLD + timedelta(days=7))["id"]
    ledger.transition_sync(a, from_statuses=[c.PLANNED], to_status=c.PLANNED,
                           fields={"slug": "ai-power-2026-10-23"}, sb=fake)
    with pytest.raises(LearnLedgerConflict):
        ledger.transition_sync(b, from_statuses=[c.PLANNED], to_status=c.PLANNED,
                               fields={"slug": "ai-power-2026-10-23"}, sb=fake)


def test_a_malformed_piece_id_writes_nothing(fake, caplog):
    _piece(fake)
    n = len(fake.calls)
    with caplog.at_level("WARNING"):
        assert ledger.transition_sync("'; drop table", from_statuses=[c.PLANNED], to_status=c.SKIPPED,
                                      sb=fake) is None
    assert len(fake.calls) == n and "not a UUID" in caplog.text


def test_a_missing_piece_loses_cleanly(fake, caplog):
    with caplog.at_level("INFO"):
        assert ledger.transition_sync(str(uuid.uuid4()), from_statuses=[c.PLANNED], to_status=c.SKIPPED,
                                      sb=fake) is None
    assert "does not exist" in caplog.text


def test_a_failed_describing_read_does_not_mask_the_lost_cas(fake, caplog):
    pid = _piece(fake)["id"]
    fake.fail_next(ledger.PIECES, "select", RuntimeError("boom"))
    with caplog.at_level("INFO"):
        assert ledger.transition_sync(pid, from_statuses=[c.BUILDING], to_status=c.DRAFTED, sb=fake) is None
    assert "describing it failed" in caplog.text


def test_an_update_failure_is_a_typed_error(fake):
    pid = _piece(fake)["id"]
    fake.fail_next(ledger.PIECES, "update", ConnectionError("reset"))
    with pytest.raises(LearnLedgerError) as e:
        ledger.transition_sync(pid, from_statuses=[c.PLANNED], to_status=c.TOPIC_PENDING, sb=fake)
    assert "transition" in str(e.value) and pid in str(e.value)


# ── leases ───────────────────────────────────────────────────────────────────────────────────


def _acquire(fake, pid, gen, *, now=T0, frm=(c.PLANNED,), to=c.BUILDING, secs=600):
    return ledger.acquire_lease_sync(pid, generation_id=gen, lease_seconds=secs, from_statuses=list(frm),
                                     to_status=to, now=now, sb=fake)


def test_acquire_takes_the_piece_counts_the_generation_and_moves_it(fake):
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    row = _acquire(fake, pid, gen)
    assert row["status"] == c.BUILDING and row["generation_id"] == gen and row["generations"] == 1
    assert ledger._parse_ts(row["lease_until"]) == T0 + timedelta(seconds=600)


def test_a_second_generation_cannot_take_a_live_lease_but_takes_an_expired_one(fake):
    pid = _piece(fake)["id"]
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    _acquire(fake, pid, first)
    assert _acquire(fake, pid, second, now=T0 + timedelta(seconds=599), frm=(c.BUILDING,), to=None) is None
    taken = _acquire(fake, pid, second, now=T0 + timedelta(seconds=600), frm=(c.BUILDING,), to=None)
    assert taken["generation_id"] == second and taken["generations"] == 2
    # the superseded generation can no longer refresh or write
    assert ledger.refresh_lease_sync(pid, generation_id=first, lease_seconds=600, now=T0, sb=fake) is None
    assert ledger.transition_sync(pid, from_statuses=[c.BUILDING], to_status=c.DRAFTED,
                                  fence={"generation_id": first}, sb=fake) is None


def test_a_replayed_acquire_by_the_owner_returns_the_row_without_recounting(fake):
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    _acquire(fake, pid, gen)
    again = _acquire(fake, pid, gen, now=T0 + timedelta(seconds=10))
    assert again["generations"] == 1 and again["status"] == c.BUILDING


def test_acquire_is_fenced_on_the_observed_lease(fake):
    """An owner whose refresh lands between the taker's read and its write is alive."""
    pid = _piece(fake)["id"]
    owner, taker = str(uuid.uuid4()), str(uuid.uuid4())
    _acquire(fake, pid, owner)
    late = T0 + timedelta(seconds=700)

    def owner_refreshes():
        fake.row(ledger.PIECES, pid)["lease_until"] = ledger._iso(late + timedelta(seconds=600))

    fake.before_update[ledger.PIECES] = [owner_refreshes]
    assert _acquire(fake, pid, taker, now=late, frm=(c.BUILDING,), to=None) is None
    assert fake.row(ledger.PIECES, pid)["generation_id"] == owner


def test_acquire_refuses_the_wrong_status(fake):
    pid = _piece(fake)["id"]
    assert _acquire(fake, pid, str(uuid.uuid4()), frm=(c.TOPIC_PENDING,)) is None
    assert fake.row(ledger.PIECES, pid)["generations"] == 0


@pytest.mark.parametrize("kwargs", [
    dict(generation_id="g-1"), dict(lease_seconds=0), dict(lease_seconds=float("nan")),
    dict(lease_seconds=True), dict(lease_seconds=10 ** 9), dict(from_statuses=[c.FAILED]),
    dict(from_statuses=[c.PUBLISHED]), dict(to_status=c.PUBLISHING), dict(to_status=c.APPROVED),
    dict(now=T0.replace(tzinfo=None)),
])
def test_acquire_refuses_bad_arguments(fake, kwargs):
    pid = _piece(fake)["id"]
    args = dict(generation_id=str(uuid.uuid4()), lease_seconds=600, from_statuses=[c.PLANNED], to_status=None,
                now=T0)
    args.update(kwargs)
    with pytest.raises(ValueError):
        ledger.acquire_lease_sync(pid, sb=fake, **args)


def test_refresh_and_release(fake):
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    _acquire(fake, pid, gen)
    row = ledger.refresh_lease_sync(pid, generation_id=gen, lease_seconds=900, now=T0 + timedelta(seconds=60),
                                    sb=fake)
    assert ledger._parse_ts(row["lease_until"]) == T0 + timedelta(seconds=960)
    assert ledger.release_lease_sync(pid, generation_id=str(uuid.uuid4()), sb=fake) is None
    assert ledger.release_lease_sync(pid, generation_id=gen, sb=fake)["lease_until"] is None
    # a released lease cannot be refreshed back to life
    assert ledger.refresh_lease_sync(pid, generation_id=gen, lease_seconds=900, now=T0, sb=fake) is None


def test_refresh_stops_once_the_piece_left_the_build(fake):
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    _acquire(fake, pid, gen)
    ledger.transition_sync(pid, from_statuses=[c.BUILDING], to_status=c.EXPIRED, sb=fake)
    assert ledger.refresh_lease_sync(pid, generation_id=gen, lease_seconds=600, now=T0, sb=fake) is None


# ── bump ─────────────────────────────────────────────────────────────────────────────────────


def test_bump_counts_on_the_observed_value(fake):
    pid = _piece(fake)["id"]
    assert ledger.bump_sync(pid, "build_attempts", fence={}, sb=fake)["build_attempts"] == 1
    assert ledger.bump_sync(pid, "build_attempts", fence={}, statuses=[c.PLANNED], sb=fake)["build_attempts"] == 2
    assert ledger.bump_sync(pid, "build_attempts", fence={}, statuses=[c.BUILDING], sb=fake) is None
    assert fake.row(ledger.PIECES, pid)["build_attempts"] == 2


def test_bump_is_fenced(fake):
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    _acquire(fake, pid, gen)
    n = len(fake.calls)
    assert ledger.bump_sync(pid, "writer_failures", fence={"generation_id": str(uuid.uuid4())}, sb=fake) is None
    assert (ledger.PIECES, "update") not in fake.calls[n:], "a fence that already mismatches decides without a write"
    assert ledger.bump_sync(pid, "writer_failures", fence={"generation_id": gen}, sb=fake)["writer_failures"] == 1


def test_a_bump_whose_fence_moves_between_read_and_write_writes_nothing(fake):
    """The CAS itself carries the fence: a takeover landing after the read must not be bumped into."""
    pid = _piece(fake)["id"]
    gen = str(uuid.uuid4())
    _acquire(fake, pid, gen)

    def taken_over():
        fake.row(ledger.PIECES, pid)["generation_id"] = str(uuid.uuid4())

    fake.before_update[ledger.PIECES] = [taken_over]
    assert ledger.bump_sync(pid, "writer_failures", fence={"generation_id": gen}, sb=fake) is None
    assert fake.row(ledger.PIECES, pid)["writer_failures"] == 0


def test_a_racing_bump_is_retried_and_both_count(fake):
    pid = _piece(fake)["id"]

    def other_bump():
        fake.row(ledger.PIECES, pid)["content_rejections"] += 1

    fake.before_update[ledger.PIECES] = [other_bump]
    row = ledger.bump_sync(pid, "content_rejections", fence={}, sb=fake)
    assert row["content_rejections"] == 2


def test_a_bump_that_keeps_losing_gives_up(fake, caplog):
    pid = _piece(fake)["id"]

    def other_bump():
        fake.row(ledger.PIECES, pid)["generations"] += 1

    fake.before_update[ledger.PIECES] = [other_bump] * 3
    with caplog.at_level("WARNING"):
        assert ledger.bump_sync(pid, "generations", fence={}, retries=2, sb=fake) is None
    assert "gave up after 3 attempts" in caplog.text


@pytest.mark.parametrize("column,fence", [("tokens_used", {}), ("status", {}),
                                          ("build_attempts", {"build_attempts": 0}), ("generations", {"status": "x"})])
def test_bump_refuses_bad_arguments(fake, column, fence):
    pid = _piece(fake)["id"]
    with pytest.raises(ValueError):
        ledger.bump_sync(pid, column, fence=fence, sb=fake)


def test_bump_refuses_an_unreadable_stored_counter(fake):
    pid = _piece(fake)["id"]
    fake.row(ledger.PIECES, pid)["build_attempts"] = True
    with pytest.raises(LearnLedgerError):
        ledger.bump_sync(pid, "build_attempts", fence={}, sb=fake)


# ── recent subjects (the no-repeat windows) ─────────────────────────────────────────────────


def _published(fake, key, sched, *, theme=None, subjects=None, sheet_subjects=None, topic=None, status=c.PUBLISHED,
               kind=None):
    pid = _piece(fake, key, sched=sched, hold=sched + timedelta(hours=48), kind=kind)["id"]
    row = fake.row(ledger.PIECES, pid)
    row["status"] = status
    row["fact_sheet"] = None if theme is None and sheet_subjects is None else {
        "theme_slug": theme, "subjects": sheet_subjects or []}
    row["subjects"] = subjects if subjects is not None else []
    row["topic"] = topic
    return pid


def test_recent_subjects_reads_themes_tickers_and_ciks(fake, caplog):
    _published(fake, "2026-W40:deep_dive", T0 - timedelta(weeks=3), theme="AI-Chips",
               subjects=[{"name": "NVIDIA", "cik": "1045810", "ticker": "nvda"}],
               sheet_subjects=[{"name": "AMD", "cik": 2488, "ticker": "AMD"}])
    _published(fake, "2026-W41:deep_dive", T0 - timedelta(weeks=2), status=c.APPROVED,
               topic={"theme_slug": "power-for-ai"},
               subjects=[{"name": "Vistra", "cik": None, "ticker": "VST"}, "junk",
                         {"ticker": "lower case!", "cik": True}, {"ticker": "BRK.B", "cik": -5}])
    _published(fake, "2026-W42:deep_dive", T0 - timedelta(weeks=1), status=c.FAILED, theme="should-not-count",
               subjects=[{"ticker": "XOM"}])
    _published(fake, "2026-W30:deep_dive", T0 - timedelta(weeks=13), theme="too-old", subjects=[{"ticker": "OLD"}])
    _published(fake, "2026-10-19:slot", T0 - timedelta(days=5), theme="slot-theme", subjects=[{"ticker": "SLOT"}])
    with caplog.at_level("WARNING"):
        got = ledger.recent_subjects_sync("deep_dive", T0 - timedelta(weeks=12), sb=fake)
    assert got.themes == {"ai-chips", "power-for-ai"}
    assert got.tickers == {"NVDA", "AMD", "VST", "BRK.B"}
    assert got.ciks == {"0001045810", "0000002488"}
    assert set(got.period_keys) == {"2026-W40:deep_dive", "2026-W41:deep_dive"}
    assert "non-object subject" in caplog.text and "unreadable ticker" in caplog.text
    assert "unreadable cik True" in caplog.text and "unreadable cik -5" in caplog.text


def test_recent_subjects_survives_wrong_shapes(fake, caplog):
    _published(fake, "2026-W40:deep_dive", T0 - timedelta(weeks=1), theme=None, subjects=[])
    row = fake.tables[ledger.PIECES].rows[0]
    row["fact_sheet"] = {"theme_slug": 5, "subjects": "NVDA"}
    with caplog.at_level("WARNING"):
        got = ledger.recent_subjects_sync("deep_dive", T0 - timedelta(weeks=4), sb=fake)
    assert got.themes == frozenset() and got.tickers == frozenset()
    assert "unreadable theme 5" in caplog.text and "subjects of type str" in caplog.text


def test_recent_subjects_fails_closed_on_a_read_error(fake):
    fake.fail_next(ledger.PIECES, "select", RuntimeError("timeout"))
    with pytest.raises(LearnLedgerError):
        ledger.recent_subjects_sync("deep_dive", T0, sb=fake)


def test_recent_subjects_refuses_bad_arguments(fake):
    with pytest.raises(ValueError):
        ledger.recent_subjects_sync("weekly", T0, sb=fake)
    with pytest.raises(ValueError):
        ledger.recent_subjects_sync("deep_dive", T0.replace(tzinfo=None), sb=fake)


def test_subjects_json_is_what_recent_subjects_reads(fake):
    subjects = (c.Subject(name="CoreWeave", cik="0001769628", ticker="CRWV"),)
    pid = _piece(fake)["id"]
    ledger.transition_sync(pid, from_statuses=[c.PLANNED], to_status=c.PLANNED,
                           fields={"subjects": ledger.subjects_json(subjects)}, sb=fake)
    fake.row(ledger.PIECES, pid)["status"] = c.PUBLISHED
    got = ledger.recent_subjects_sync("deep_dive", T0 - timedelta(days=1), sb=fake)
    assert got.tickers == {"CRWV"} and got.ciks == {"0001769628"}


# ── narration jobs ───────────────────────────────────────────────────────────────────────────


SCRIPT = [{"i": 0, "text": "Title", "role": "context"}, {"i": 1, "text": "Body.", "role": "body"}]


def _job(fake, pid, *, round=1, engine="chatterbox", sha=SHA):
    row = ledger.enqueue_job_sync(pid, round=round, script=SCRIPT, script_sha256=sha, engine=engine,
                                  voice="caydex_voice_achird_v2", pipeline_version="learn-narrator/1", sb=fake)
    assert row is not None
    return row


def test_enqueue_is_first_write_wins_on_piece_and_round(fake):
    pid = _piece(fake)["id"]
    first = _job(fake, pid)
    assert first["status"] == c.JOB_QUEUED and first["attempts"] == 0
    assert _job(fake, pid)["id"] == first["id"]
    assert len(fake.tables[ledger.JOBS].rows) == 1


def test_enqueue_refuses_to_hand_back_a_job_for_another_script(fake, caplog):
    pid = _piece(fake)["id"]
    _job(fake, pid)
    with caplog.at_level("ERROR"):
        assert ledger.enqueue_job_sync(pid, round=1, script=SCRIPT, script_sha256="b" * 64, engine="chatterbox",
                                       voice="caydex_voice_achird_v2", pipeline_version="learn-narrator/1",
                                       sb=fake) is None
    assert "open a new round" in caplog.text
    assert _job(fake, pid, round=2, sha="b" * 64)["round"] == 2
    assert [j["round"] for j in ledger.list_jobs_sync(pid, sb=fake)] == [1, 2]


@pytest.mark.parametrize("kwargs", [
    dict(round=0), dict(round=-1), dict(round=True), dict(round=1.0), dict(script=[]), dict(script={}),
    dict(script="text"), dict(script=[{"t": float("nan")}]), dict(script_sha256="A" * 64),
    dict(script_sha256=None), dict(engine="piper"), dict(voice=""), dict(voice=" padded "),
    dict(voice="v" * 81), dict(pipeline_version="a\nb"), dict(piece_id="nope"),
])
def test_enqueue_refuses_bad_input_before_any_io(fake, kwargs):
    pid = _piece(fake)["id"]
    args = dict(piece_id=pid, round=1, script=SCRIPT, script_sha256=SHA, engine="kokoro", voice="am_michael",
                pipeline_version="learn-narrator/1")
    args.update(kwargs)
    n = len(fake.calls)
    with pytest.raises(ValueError):
        ledger.enqueue_job_sync(args.pop("piece_id"), sb=fake, **args)
    assert len(fake.calls) == n


def test_enqueue_for_a_missing_piece_is_a_ledger_error(fake):
    with pytest.raises(LearnLedgerError) as e:
        _job(fake, str(uuid.uuid4()))
    assert "enqueue_job" in str(e.value)


def test_claim_offers_only_the_engines_the_worker_declares(fake, caplog):
    pid = _piece(fake)["id"]
    job = _job(fake, pid, engine="chatterbox")
    assert ledger.claim_job_sync(["kokoro"], now=T0, sb=fake) is None
    with caplog.at_level("WARNING"):
        assert ledger.claim_job_sync(["piper", "", None], now=T0, sb=fake) is None
        assert ledger.claim_job_sync(42, now=T0, sb=fake) is None
    assert "unknown engine" in caplog.text
    got = ledger.claim_job_sync(["kokoro", "chatterbox"], now=T0, sb=fake)
    assert got["id"] == job["id"] and got["status"] == c.JOB_IN_PROGRESS and got["attempts"] == 1
    assert re.fullmatch(r"[0-9a-f]{16}", got["claim_nonce"])
    assert ledger._parse_ts(got["lease_until"]) == T0 + timedelta(seconds=c.JOB_LEASE_SECONDS)
    # a claimed job with a running lease is nobody else's
    assert ledger.claim_job_sync(["chatterbox"], now=T0 + timedelta(seconds=60), sb=fake) is None


def test_claim_accepts_a_single_engine_string(fake):
    pid = _piece(fake)["id"]
    job = _job(fake, pid, engine="kokoro")
    assert ledger.claim_job_sync("kokoro", now=T0, sb=fake)["id"] == job["id"]


def test_claim_takes_the_oldest_job_first(fake):
    a = _piece(fake)["id"]
    b = _piece(fake, "2026-W44:deep_dive", sched=T0 + timedelta(days=7), hold=HOLD + timedelta(days=7))["id"]
    old = _job(fake, a)
    new = _job(fake, b)
    assert ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)["id"] == old["id"]
    assert ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)["id"] == new["id"]


def test_two_concurrent_claimers_get_one_winner(fake):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    fake.select_barrier = threading.Barrier(2)  # both read the same queue before either writes
    results: List[Optional[dict]] = [None, None]

    def go(i):
        results[i] = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    winners = [r for r in results if r is not None]
    assert len(winners) == 1 and winners[0]["id"] == job["id"]
    assert fake.row(ledger.JOBS, job["id"])["attempts"] == 1


def test_the_claim_fence_and_heartbeat(fake):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    att, nonce = got["attempts"], got["claim_nonce"]
    assert ledger.job_claim_ok_sync(job["id"], att, nonce, sb=fake)["id"] == job["id"]
    for bad in ((att + 1, nonce), (att, "0" * 16), (True, nonce), (att, nonce.upper()), (att, None), (0, nonce)):
        assert ledger.job_claim_ok_sync(job["id"], *bad, sb=fake) is None, bad
    assert ledger.job_claim_ok_sync("not-a-uuid", att, nonce, sb=fake) is None
    beat = ledger.heartbeat_job_sync(job["id"], att, nonce, now=T0 + timedelta(seconds=300), sb=fake)
    assert ledger._parse_ts(beat["lease_until"]) == T0 + timedelta(seconds=300 + c.JOB_LEASE_SECONDS)
    assert ledger.heartbeat_job_sync(job["id"], att, "f" * 16, now=T0, sb=fake) is None


def test_an_expired_lease_is_reclaimed_and_the_zombie_writes_nothing(fake, caplog):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    first = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    later = T0 + timedelta(seconds=c.JOB_LEASE_SECONDS + 1)
    with caplog.at_level("WARNING"):
        second = ledger.claim_job_sync(["chatterbox"], now=later, sb=fake)
    assert second["id"] == job["id"] and second["attempts"] == 2 and second["lease_reclaims"] == 1
    assert second["claim_nonce"] != first["claim_nonce"]
    assert "RE-CLAIMED" in caplog.text
    zombie = (first["attempts"], first["claim_nonce"])
    assert ledger.heartbeat_job_sync(job["id"], *zombie, now=later, sb=fake) is None
    assert ledger.complete_job_sync(job["id"], *zombie, audio_path="audio/weekly/x.m4a", audio_bytes=10,
                                    duration_seconds=1.0, timings=[], checks={}, now=later, sb=fake) is None
    assert ledger.fail_job_sync(job["id"], *zombie, error="late", now=later, sb=fake) is None
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


def test_a_reclaim_is_fenced_on_the_observed_lease(fake):
    """A heartbeat that lands between the re-claimer's read and its write means the worker is alive."""
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    later = T0 + timedelta(seconds=c.JOB_LEASE_SECONDS + 1)

    def heartbeat_lands():
        fake.row(ledger.JOBS, job["id"])["lease_until"] = ledger._iso(later + timedelta(seconds=600))

    fake.before_update[ledger.JOBS] = [heartbeat_lands]
    assert ledger.claim_job_sync(["chatterbox"], now=later, sb=fake) is None
    assert fake.row(ledger.JOBS, job["id"])["attempts"] == 1


def test_the_lease_reclaim_cap_closes_the_job_failed(fake, caplog):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    now = T0
    for n in range(c.MAX_JOB_LEASE_RECLAIMS + 1):  # the first claim, then MAX re-claims
        got = ledger.claim_job_sync(["chatterbox"], now=now, sb=fake)
        assert got is not None and got["lease_reclaims"] == n
        now += timedelta(seconds=c.JOB_LEASE_SECONDS + 1)
    other = _job(fake, _piece(fake, "2026-W44:deep_dive", sched=T0 + timedelta(days=7),
                              hold=HOLD + timedelta(days=7))["id"])
    with caplog.at_level("ERROR"):
        got = ledger.claim_job_sync(["chatterbox"], now=now, sb=fake)
    assert got["id"] == other["id"], "the capped job is closed and the next one is offered"
    capped = fake.row(ledger.JOBS, job["id"])
    assert capped["status"] == c.JOB_FAILED and capped["lease_until"] is None
    assert capped["error"].startswith("lease_reclaims_exhausted") and capped["finished_at"]
    assert "FAILED" in caplog.text


def test_complete_and_its_replay(fake):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    claim = (got["attempts"], got["claim_nonce"])
    done = ledger.complete_job_sync(job["id"], *claim, audio_path="audio/weekly/ai-power-2026-10-23-0123456789ab.m4a",
                                    audio_bytes=8_600_000, duration_seconds=712.4, timings=[{"i": 0, "s": [0.0]}],
                                    checks={"coverage": 0.99}, now=T0 + timedelta(hours=1), sb=fake)
    assert done["status"] == c.JOB_READY and done["audio_bytes"] == 8_600_000 and done["lease_until"] is None
    before = copy.deepcopy(fake.row(ledger.JOBS, job["id"]))
    replay = ledger.complete_job_sync(job["id"], *claim, audio_path="audio/weekly/other.m4a", audio_bytes=1,
                                      duration_seconds=1.0, timings=[], checks={}, now=T0 + timedelta(hours=2), sb=fake)
    assert replay == before == fake.row(ledger.JOBS, job["id"]), "a terminal replay writes nothing"
    assert ledger.complete_job_sync(job["id"], claim[0] + 1, claim[1], audio_path="a.m4a", audio_bytes=1,
                                    duration_seconds=1.0, timings=[], checks={}, sb=fake) is None


@pytest.mark.parametrize("kwargs", [
    dict(audio_path="/abs/x.m4a"), dict(audio_path="audio/../x.m4a"), dict(audio_path="a b.m4a"),
    dict(audio_path=""), dict(audio_bytes=0), dict(audio_bytes=True), dict(audio_bytes=-5), dict(audio_bytes=1.5),
    dict(duration_seconds=0), dict(duration_seconds=float("nan")), dict(duration_seconds=float("inf")),
    dict(duration_seconds=5 * 3600), dict(timings=None), dict(timings=[{"s": float("nan")}]),
    dict(checks=[]), dict(checks={"x": float("inf")}),
])
def test_complete_refuses_bad_results(fake, kwargs):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    args = dict(audio_path="audio/weekly/x.m4a", audio_bytes=10, duration_seconds=1.0, timings=[], checks={})
    args.update(kwargs)
    with pytest.raises(ValueError):
        ledger.complete_job_sync(job["id"], got["attempts"], got["claim_nonce"], sb=fake, **args)
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


def test_worker_failures_requeue_then_fail_at_the_cap(fake, caplog):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    first = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    with caplog.at_level("WARNING"):
        requeued = ledger.fail_job_sync(job["id"], first["attempts"], first["claim_nonce"],
                                        error="block 12 truncated\nat 39.6s", now=T0, sb=fake)
    assert requeued["status"] == c.JOB_QUEUED and requeued["worker_failures"] == 1
    assert requeued["error"] == "block 12 truncated at 39.6s" and requeued["lease_until"] is None
    assert "re-queued" in caplog.text
    # replay of the same failure: unchanged
    before = copy.deepcopy(fake.row(ledger.JOBS, job["id"]))
    assert ledger.fail_job_sync(job["id"], first["attempts"], first["claim_nonce"], error="block 12 truncated",
                                sb=fake) == before
    assert fake.row(ledger.JOBS, job["id"]) == before
    second = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    assert second["attempts"] == 2 and second["lease_reclaims"] == 0
    with caplog.at_level("ERROR"):
        failed = ledger.fail_job_sync(job["id"], second["attempts"], second["claim_nonce"], error="", now=T0, sb=fake)
    assert failed["status"] == c.JOB_FAILED and failed["worker_failures"] == c.MAX_JOB_WORKER_FAILURES
    assert failed["error"] == "unspecified worker failure" and failed["finished_at"]
    assert ledger.claim_job_sync(["chatterbox"], now=T0 + timedelta(days=1), sb=fake) is None


def test_a_final_failure_closes_the_job_at_the_first_report(fake, caplog):
    """`final` (a deterministic code: the same render would fail the same way) fails the job under the
    cap, still counting the failure, still fenced on the claim, and a replay changes nothing."""
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    with caplog.at_level("ERROR"):
        failed = ledger.fail_job_sync(job["id"], got["attempts"], got["claim_nonce"], final=True,
                                      error="voice_out_of_band: bytes_per_sec 12888.0", now=T0, sb=fake)
    assert failed["status"] == c.JOB_FAILED and failed["worker_failures"] == 1 < c.MAX_JOB_WORKER_FAILURES
    assert failed["finished_at"] and failed["lease_until"] is None
    assert "worker failure 1 of 2 (deterministic" in caplog.text
    before = copy.deepcopy(fake.row(ledger.JOBS, job["id"]))
    assert ledger.fail_job_sync(job["id"], got["attempts"], got["claim_nonce"], final=True, error="again",
                                sb=fake) == before
    assert ledger.fail_job_sync(job["id"], got["attempts"], "f" * 16, final=True, error="zombie", sb=fake) is None
    assert fake.row(ledger.JOBS, job["id"]) == before
    assert ledger.claim_job_sync(["chatterbox"], now=T0 + timedelta(days=1), sb=fake) is None


def test_fail_with_a_malformed_claim_is_refused(fake):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    for att, nonce in ((None, "a" * 16), ("1", "a" * 16), (1, "zz"), (1, 12)):
        assert ledger.fail_job_sync(job["id"], att, nonce, error="x", sb=fake) is None
    assert fake.row(ledger.JOBS, job["id"])["worker_failures"] == 0


def test_cancel_open_jobs_stops_the_worker(fake, caplog):
    pid = _piece(fake)["id"]
    running = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    queued = _job(fake, pid, round=2, sha="b" * 64)
    other_piece = _piece(fake, "2026-W44:deep_dive", sched=T0 + timedelta(days=7), hold=HOLD + timedelta(days=7))["id"]
    untouched = _job(fake, other_piece)
    with caplog.at_level("WARNING"):
        cancelled = ledger.cancel_open_jobs_sync(pid, reason="expired", now=T0, sb=fake)
    assert {r["id"] for r in cancelled} == {running["id"], queued["id"]}
    assert all(r["status"] == c.JOB_CANCELLED and r["lease_until"] is None for r in cancelled)
    assert fake.row(ledger.JOBS, untouched["id"])["status"] == c.JOB_QUEUED
    assert "cancelled 2 narration job(s)" in caplog.text
    claim = (got["attempts"], got["claim_nonce"])
    assert ledger.heartbeat_job_sync(running["id"], *claim, now=T0, sb=fake) is None
    before = copy.deepcopy(fake.row(ledger.JOBS, running["id"]))
    assert ledger.complete_job_sync(running["id"], *claim, audio_path="audio/weekly/x.m4a", audio_bytes=10,
                                    duration_seconds=1.0, timings=[], checks={}, sb=fake) is None
    assert fake.row(ledger.JOBS, running["id"]) == before, "a cancelled job is never completed"
    assert ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)["id"] == untouched["id"]
    assert ledger.cancel_open_jobs_sync(pid, sb=fake) == []
    assert ledger.cancel_open_jobs_sync("nope", sb=fake) == []


def test_a_ready_job_is_never_cancelled_or_reclaimed(fake):
    pid = _piece(fake)["id"]
    job = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    ledger.complete_job_sync(job["id"], got["attempts"], got["claim_nonce"], audio_path="audio/weekly/x.m4a",
                             audio_bytes=10, duration_seconds=1.0, timings=[], checks={}, now=T0, sb=fake)
    assert ledger.cancel_open_jobs_sync(pid, sb=fake) == []
    assert ledger.claim_job_sync(["chatterbox"], now=T0 + timedelta(days=1), sb=fake) is None
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_READY


def test_get_job_with_a_malformed_id(fake):
    assert ledger.get_job_sync("x", sb=fake) is None and ledger.list_jobs_sync(None, sb=fake) == []


def test_claim_refuses_a_naive_clock(fake):
    with pytest.raises(ValueError):
        ledger.claim_job_sync(["kokoro"], now=T0.replace(tzinfo=None), sb=fake)


# ── async wrappers ───────────────────────────────────────────────────────────────────────────


def _public_sync_functions() -> Dict[str, Any]:
    return {n[: -len("_sync")]: f for n, f in vars(ledger).items()
            if n.endswith("_sync") and not n.startswith("_") and inspect.isfunction(f)}


def test_every_sync_operation_has_an_async_twin_with_the_same_parameters():
    syncs = _public_sync_functions()
    assert set(syncs) == {
        "get_piece", "get_piece_by_id", "list_pieces", "recent_subjects", "create_piece", "transition",
        "acquire_lease", "refresh_lease", "release_lease", "bump",
        "get_job", "list_jobs", "enqueue_job", "claim_job", "job_claim_ok", "heartbeat_job", "complete_job",
        "fail_job", "cancel_open_jobs", "promote_rehearsed", "repoint_ready_audio",
    }
    for name, fn in syncs.items():
        twin = getattr(ledger, name, None)
        assert twin is not None and inspect.iscoroutinefunction(twin), name
        assert inspect.signature(twin).parameters.keys() == inspect.signature(fn).parameters.keys(), name


def test_every_async_twin_runs_its_sync_core_in_a_thread():
    """AST: each wrapper's body is `return await asyncio.to_thread(<name>_sync, …)` — never the
    Supabase SDK on the event loop (CLAUDE.md invariant 6)."""
    tree = ast.parse(Path(ledger.__file__).read_text(encoding="utf-8"))
    wrappers = {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    assert set(wrappers) == set(_public_sync_functions())
    for name, node in wrappers.items():
        (stmt,) = node.body
        call = stmt.value.value  # Return(Await(Call))
        assert isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Await), name
        assert ast.unparse(call.func) == "asyncio.to_thread", name
        assert ast.unparse(call.args[0]) == f"{name}_sync", name
        passed = {kw.arg for kw in call.keywords} | {ast.unparse(a) for a in call.args[1:]}
        assert passed == set(inspect.signature(getattr(ledger, name)).parameters), name


@pytest.mark.asyncio
async def test_the_async_path_end_to_end(fake, monkeypatch):
    # sb=None resolves the module's `get_supabase` binding — patched here, so nothing leaves the process.
    monkeypatch.setattr(ledger, "get_supabase", lambda: fake)
    row = await ledger.create_piece(KEY, "deep_dive", scheduled_publish_at=T0, hold_until=HOLD, dry_run=False)
    pid = row["id"]
    gen = str(uuid.uuid4())
    assert (await ledger.acquire_lease(pid, generation_id=gen, lease_seconds=600, from_statuses=[c.PLANNED],
                                       to_status=c.BUILDING, now=T0))["generations"] == 1
    assert (await ledger.refresh_lease(pid, generation_id=gen, lease_seconds=600, now=T0)) is not None
    assert (await ledger.bump(pid, "build_attempts", fence={"generation_id": gen}))["build_attempts"] == 1
    assert (await ledger.transition(pid, from_statuses=[c.BUILDING], to_status=c.DRAFTED,
                                    fence={"generation_id": gen}))["status"] == c.DRAFTED
    assert (await ledger.release_lease(pid, generation_id=gen))["lease_until"] is None
    job = await ledger.enqueue_job(pid, round=1, script=SCRIPT, script_sha256=SHA, engine="kokoro",
                                   voice="am_michael", pipeline_version="learn-narrator/1")
    got = await ledger.claim_job(["kokoro"], now=T0)
    claim = (got["attempts"], got["claim_nonce"])
    assert (await ledger.job_claim_ok(job["id"], *claim))["id"] == job["id"]
    assert await ledger.heartbeat_job(job["id"], *claim, now=T0) is not None
    assert (await ledger.complete_job(job["id"], *claim, audio_path="audio/weekly/x.m4a", audio_bytes=10,
                                      duration_seconds=2.0, timings=[], checks={}, now=T0))["status"] == c.JOB_READY
    assert (await ledger.get_job(job["id"]))["status"] == c.JOB_READY
    assert [j["id"] for j in await ledger.list_jobs(pid)] == [job["id"]]
    assert await ledger.fail_job(job["id"], *claim, error="x") is None
    assert await ledger.cancel_open_jobs(pid) == []
    assert (await ledger.get_piece(KEY))["id"] == pid
    assert (await ledger.get_piece_by_id(pid))["status"] == c.DRAFTED
    assert [r["id"] for r in await ledger.list_pieces(kind="deep_dive")] == [pid]
    assert (await ledger.recent_subjects("deep_dive", T0 - timedelta(days=30))).themes == frozenset()


# ── promoting a rehearsal (owner decision 2026-11-01: publish W44) ───────────────────────────

_OLD_AUDIO = "audio/weekly/ai-power-2026-10-23-0123456789ab.m4a"
_NEW_AUDIO = "audio/weekly/ai-power-2026-10-23-ba9876543210.m4a"
_NOW = T0 + timedelta(days=9)


def _rehearsed(fake):
    """A dry-run piece that went the whole way to `rehearsed`, with a ready narration job."""
    pid = _piece(fake, dry_run=True)["id"]
    job = _job(fake, pid)
    got = ledger.claim_job_sync(["chatterbox"], now=T0, sb=fake)
    job = ledger.complete_job_sync(job["id"], got["attempts"], got["claim_nonce"], audio_path=_OLD_AUDIO,
                                   audio_bytes=9_000_000, duration_seconds=700.0, timings=[{"i": 0}],
                                   checks={"server": {"ok": True}}, now=T0, sb=fake)
    _walk(fake, pid, MAIN_PATH, fields={"draft": {"title": "x"}, "draft_sha256": SHA,
                                         "narration_job_id": job["id"]})
    row = ledger.transition_sync(pid, from_statuses=[c.APPROVED], to_status=c.REHEARSED, sb=fake)
    assert row["status"] == c.REHEARSED and row["dry_run"] is True
    return row, job


def _promote(fake, piece, job, **over):
    args = dict(review_round=piece["review_round"], old_draft_sha256=SHA, narration_job_id=job["id"],
                observed_updated_at=piece["updated_at"], draft={"title": "x", "author": {"title": "Editorial"}},
                draft_sha256="b" * 64, scheduled_publish_at=_NOW + timedelta(minutes=15),
                hold_until=_NOW + timedelta(hours=48), validation={"promote": {"by": "owner"}}, now=_NOW, sb=fake)
    args.update(over)
    return ledger.promote_rehearsed_sync(piece["id"], **args)


def test_promote_takes_a_rehearsal_live_and_then_the_ordinary_publish_path_accepts_it(fake):
    piece, job = _rehearsed(fake)
    row = _promote(fake, piece, job)
    assert row["status"] == c.APPROVED and row["dry_run"] is False
    assert row["draft_sha256"] == "b" * 64 and row["last_stage"] == "promote"
    assert row["validation"] == {"promote": {"by": "owner"}} and row["publish_lease_until"] is None
    assert not _piece_violations(fake.row(ledger.PIECES, piece["id"]))
    # The publisher's own transition now works (it fences on dry_run = false).
    live = ledger.transition_sync(piece["id"], from_statuses=[c.APPROVED], to_status=c.PUBLISHING,
                                  within_hold_at=_NOW + timedelta(minutes=16), due_at=_NOW + timedelta(minutes=16),
                                  fence={"draft_sha256": "b" * 64}, sb=fake)
    assert live is not None and live["status"] == c.PUBLISHING


@pytest.mark.parametrize("over", [
    {"review_round": 1}, {"old_draft_sha256": "c" * 64}, {"narration_job_id": str(uuid.uuid4())},
    {"observed_updated_at": T0 - timedelta(days=1)},
])
def test_promote_is_fenced_on_everything_the_script_observed(fake, over):
    piece, job = _rehearsed(fake)
    before = copy.deepcopy(fake.row(ledger.PIECES, piece["id"]))
    assert _promote(fake, piece, job, **over) is None
    assert fake.row(ledger.PIECES, piece["id"]) == before


def test_promote_refuses_anything_but_a_dry_run_rehearsal(fake):
    piece, job = _rehearsed(fake)
    assert _promote(fake, piece, job) is not None
    again = fake.row(ledger.PIECES, piece["id"])
    assert _promote(fake, again, job, old_draft_sha256="b" * 64, draft_sha256="d" * 64) is None, \
        "an approved (already promoted) piece is not promoted twice"
    live_pid = _piece(fake, "2026-W45:deep_dive", dry_run=False)["id"]
    live = _walk(fake, live_pid, MAIN_PATH, fields={"draft_sha256": SHA, "narration_job_id": job["id"]})
    assert _promote(fake, live, job) is None, "a live piece never takes the promote path"


@pytest.mark.parametrize("over,match", [
    ({"scheduled_publish_at": _NOW}, "after now"),
    ({"hold_until": _NOW}, "before scheduled_publish_at"),
    ({"draft_sha256": SHA}, "hashes the same"),
    ({"old_draft_sha256": "NOT-HEX"}, "old_draft_sha256"),
    ({"narration_job_id": "nope"}, "narration_job_id"),
    ({"observed_updated_at": "yesterday"}, "observed_updated_at"),
    ({"review_round": -1}, "review_round"),
    ({"draft": "text"}, "draft"),
])
def test_promote_validates_its_arguments_before_any_io(fake, over, match):
    piece, job = _rehearsed(fake)
    n = len(fake.calls)
    with pytest.raises(ValueError, match=match):
        _promote(fake, piece, job, **over)
    assert len(fake.calls) == n


def test_repoint_moves_a_ready_job_to_the_trimmed_file_and_a_replay_writes_nothing(fake):
    piece, job = _rehearsed(fake)
    row = ledger.repoint_ready_audio_sync(job["id"], piece_id=piece["id"], old_audio_path=_OLD_AUDIO,
                                          new_audio_path=_NEW_AUDIO, new_audio_bytes=8_900_000,
                                          new_duration_seconds=693.5, note={"cut_at": 693.5}, sb=fake)
    assert row["audio_path"] == _NEW_AUDIO and row["audio_bytes"] == 8_900_000
    assert row["duration_seconds"] == 693.5 and row["status"] == c.JOB_READY
    assert row["checks"] == {"server": {"ok": True}, "promote": {"cut_at": 693.5}}
    assert row["timings"] == [{"i": 0}] and row["script"] == SCRIPT, "the render's record is kept"
    assert not _job_violations(fake.row(ledger.JOBS, job["id"]))
    before = copy.deepcopy(fake.row(ledger.JOBS, job["id"]))
    replay = ledger.repoint_ready_audio_sync(job["id"], piece_id=piece["id"], old_audio_path=_OLD_AUDIO,
                                             new_audio_path=_NEW_AUDIO, new_audio_bytes=8_900_000,
                                             new_duration_seconds=693.5, note={"cut_at": 693.5}, sb=fake)
    assert replay == before == fake.row(ledger.JOBS, job["id"])


def test_repoint_refuses_a_job_on_another_path_of_another_piece_or_not_ready(fake):
    piece, job = _rehearsed(fake)
    kw = dict(new_audio_path=_NEW_AUDIO, new_audio_bytes=1, new_duration_seconds=1.0, note={}, sb=fake)
    assert ledger.repoint_ready_audio_sync(job["id"], piece_id=piece["id"],
                                           old_audio_path="audio/weekly/third-0123456789ab.m4a", **kw) is None
    assert ledger.repoint_ready_audio_sync(job["id"], piece_id=str(uuid.uuid4()), old_audio_path=_OLD_AUDIO,
                                           **kw) is None
    other = _job(fake, piece["id"], round=2)          # queued, never rendered
    assert ledger.repoint_ready_audio_sync(other["id"], piece_id=piece["id"], old_audio_path=_OLD_AUDIO,
                                           **kw) is None
    assert fake.row(ledger.JOBS, job["id"])["audio_path"] == _OLD_AUDIO


@pytest.mark.parametrize("over,match", [
    ({"new_audio_path": "audio/other/x.m4a"}, "new_audio_path"),
    ({"new_audio_path": "audio/weekly/../secrets.m4a"}, "new_audio_path"),
    ({"old_audio_path": "/abs/x.m4a"}, "old_audio_path"),
    ({"new_audio_path": _OLD_AUDIO}, "equals the old one"),
    ({"new_audio_bytes": 0}, "new_audio_bytes"),
    ({"new_audio_bytes": True}, "new_audio_bytes"),
    ({"new_duration_seconds": float("nan")}, "new_duration_seconds"),
    ({"note": "text"}, "note"),
])
def test_repoint_validates_its_arguments_before_any_io(fake, over, match):
    piece, job = _rehearsed(fake)
    args = dict(piece_id=piece["id"], old_audio_path=_OLD_AUDIO, new_audio_path=_NEW_AUDIO,
                new_audio_bytes=10, new_duration_seconds=5.0, note={}, sb=fake)
    args.update(over)
    n = len(fake.calls)
    with pytest.raises(ValueError, match=match):
        ledger.repoint_ready_audio_sync(job["id"], **args)
    assert len(fake.calls) == n


@pytest.mark.parametrize("change", ["audio_path", "updated_at", "status"])
def test_repoint_is_fenced_in_the_update_itself_not_only_in_the_read(fake, change):
    piece, job = _rehearsed(fake)

    def another_writer():                      # lands between the read and the UPDATE
        row = fake.row(ledger.JOBS, job["id"])
        if change == "audio_path":
            row["audio_path"] = "audio/weekly/someone-else-0123456789ab.m4a"
        elif change == "updated_at":
            row["updated_at"] = ledger._iso(T0 + timedelta(days=20))
        else:
            row["status"] = c.JOB_CANCELLED

    fake.before_update[ledger.JOBS] = [another_writer]
    assert ledger.repoint_ready_audio_sync(job["id"], piece_id=piece["id"], old_audio_path=_OLD_AUDIO,
                                           new_audio_path=_NEW_AUDIO, new_audio_bytes=8_900_000,
                                           new_duration_seconds=693.5, note={}, sb=fake) is None
    assert fake.row(ledger.JOBS, job["id"]).get("checks", {}).get("promote") is None


def test_promote_refuses_a_dry_run_piece_that_has_not_rehearsed_yet(fake):
    # W44 sat in `approved` (dry run) until its rehearsal tick: the status fence alone must refuse it.
    pid = _piece(fake, "2026-W45:deep_dive", dry_run=True)["id"]
    job = _job(fake, pid)
    approved = _walk(fake, pid, MAIN_PATH, fields={"draft_sha256": SHA, "narration_job_id": job["id"]})
    assert approved["status"] == c.APPROVED and approved["dry_run"] is True
    before = copy.deepcopy(fake.row(ledger.PIECES, pid))
    assert _promote(fake, approved, job) is None
    assert fake.row(ledger.PIECES, pid) == before


def test_promote_is_fenced_in_the_update_on_status(fake):
    piece, job = _rehearsed(fake)

    def owner_moves_it():
        fake.row(ledger.PIECES, piece["id"])["status"] = c.UNPUBLISHED   # any other status

    fake.before_update[ledger.PIECES] = [owner_moves_it]
    assert _promote(fake, piece, job) is None
    assert fake.row(ledger.PIECES, piece["id"])["dry_run"] is True
