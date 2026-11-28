"""An in-memory model of migration 215's two functions, for the hermetic suite.

Not a test module (no `test_` prefix, like `_refresh_session_fake.py`). Every test that needs the replacement request
or the restore — the run service, the review bot, the end-to-end tick — uses THIS model, so they all exercise the same
state machine.

It mirrors `backend/database/migrations/215_marketing_replacement_runs.sql` statement by statement:
`marketing_request_replacement` and `marketing_restore_replaced`, their checks in the SQL's order, the per-ET-day
advisory lock, the row locks, the writes and the payloads (the replay read-backs, the typed numeric decision message
ids that skip previews, the review-offer stamps the restore removes). Every statement records itself in `trace`
under the same name `tests/test_marketing_replacement_sql.py` reads off the SQL text (`lock`, `lock:<table>`, each
`RETURN`'s outcome, `insert:<table>`, `update:<table>`), and that test pins the two orders equal. It cannot prove the
SQL itself is right — a local Supabase E2E does that — but a divergence between the two is a bug in this file, never a
reason to relax a test.

It works over plain lists of row dicts, so a later test can hand it the run-service fake's own tables
(`fake.tables[mrs.RUNS].rows`, …): the writes land where the service reads. A row without `lane` / `replacement`
reads as the column default ('main' / 0), as the real columns do. No table defaults beyond what the INSERT names are
materialised (a missing nullable column reads as None through `.get`).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

#: Pinned equal to the SQL's constants and to app/schemas/marketing.py by tests/test_marketing_replacement_sql.py.
C_PER_DAY = 2
C_SERIES = "money_map"
C_MAX = 2
C_MAX_ATTEMPTS = 2
C_OFFER_STAMPS = ("review_bundle", "review_notified_at", "review_message_id", "review_message_ids")
BLOCKING = ("approved", "queued", "published", "failed", "retracted")
FINAL_RUN_STATUSES = ("skipped", "published", "media_ready")
_ET = ZoneInfo("America/New_York")
REQUEST = "marketing_request_replacement"
RESTORE = "marketing_restore_replaced"

# ── what each SQL branch decides on, pinned literally beside the Python that models it ─────────────────────────────
# The statement-order pin (tests/test_marketing_replacement_sql.py) sees only WHICH statement comes next; these tables
# pin WHAT each one tests, so a flipped comparison, a dropped conjunct, a reversed ORDER BY or a filter left off an
# UPDATE in the SQL fails the build even though every fake-driven test stays green (review 2026-11-27: fifteen such
# mutants passed the old checks). Whitespace-normalised (`_migration_sql.norm_sql`), comments stripped, in text order.
# A change to the SQL changes the matching row here AND the branch of this model that mirrors it, in one edit.

#: Every `IF <condition> THEN RETURN jsonb_build_object('outcome', '<marker>' …` (and the argument check's RAISE).
REQUEST_SQL_BRANCHES = (
    ("raise", "p_run_id IS NULL OR p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 64 OR p_not_after IS NULL"),
    ("not_found", "NOT FOUND"),
    ("not_found", "NOT FOUND"),
    ("requested:replayed", "v_newest.replaces_run_id = v_run.id AND v_newest.replacement = v_run.replacement + 1 "
                           "AND NOT (v_run.metadata ? 'restored_from')"),
    ("too_late", "v_now >= p_not_after OR (v_now AT TIME ZONE c_zone)::date <> v_run.run_date"),
    ("already_replaced", "v_newest.replacement > v_run.replacement"),
    ("not_ready", "v_run.status <> 'media_ready'"),
    ("not_money_map", "NOT FOUND"),
    ("not_money_map", "v_script.status <> 'accepted' OR v_script.template_id IS DISTINCT FROM c_series"),
    ("member_decided", "v_blocking IS NOT NULL"),
    ("nothing_pending", "v_pending = 0"),
    ("cap_reached", "v_today >= c_per_day OR v_run.replacement >= c_max"),
)
#: Every WHERE clause (to the end of its statement or sub-select, ORDER BY / LIMIT / FOR UPDATE included), by table.
REQUEST_SQL_WHERES = (
    ("marketing_runs", "WHERE id = p_run_id"),
    ("marketing_runs", "WHERE id = p_run_id FOR UPDATE"),
    ("marketing_runs", "WHERE r.run_date = v_run.run_date AND r.lane = v_run.lane ORDER BY r.replacement DESC LIMIT 1"),
    ("marketing_runs", "WHERE r2.run_date = v_run.run_date AND r2.replacement >= 1"),
    ("marketing_posts", "WHERE p.run_id = v_run.id AND p.metadata -> 'review' ->> 'replaced_by_run_id' = "
                        "v_newest.id::text"),
    ("marketing_posts", "WHERE p.run_id = v_run.id AND p.metadata -> 'review' ->> 'replaced_by_run_id' = "
                        "v_newest.id::text AND jsonb_typeof(p.metadata -> 'review_message_id') = 'number' AND "
                        "NULLIF(p.metadata ->> 'review_preview_at', '') IS NULL"),
    ("marketing_scripts", "WHERE s.run_id = p_run_id"),
    ("marketing_posts", "WHERE p.run_id = p_run_id ORDER BY p.id FOR UPDATE"),
    ("marketing_posts", "WHERE p.run_id = p_run_id AND p.status IN ('approved', 'queued', 'published', 'failed', "
                        "'retracted')"),
    ("marketing_posts", "WHERE p.run_id = p_run_id AND p.status = 'pending_review'"),
    ("marketing_runs", "WHERE r.run_date = v_run.run_date AND r.replacement >= 1"),
    ("marketing_posts", "WHERE p.run_id = p_run_id AND p.status = 'pending_review'"),
    ("marketing_posts", "WHERE jsonb_typeof(mid) = 'number' AND NULLIF(preview, '') IS NULL"),   # the FILTER
    ("marketing_runs", "WHERE id = p_run_id"),
)
#: Every `<variable> := <expression>` statement.
REQUEST_SQL_ASSIGNS = (
    "v_now := clock_timestamp()",
    "v_next := v_run.replacement + 1",
)

RESTORE_SQL_BRANCHES = (
    ("raise", "p_run_id IS NULL OR p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 64 OR p_now IS NULL"),
    ("not_found", "NOT FOUND"),
    ("not_found", "NOT FOUND"),
    ("not_restorable", "v_run.status <> 'media_ready'"),
    ("not_restorable", "COALESCE(v_rep_text ~ c_uuid, false) = false"),
    ("not_restorable", "v_newest IS DISTINCT FROM v_rep_id"),
    ("not_restorable", "NOT FOUND"),
    ("not_restorable", "v_rep.replaces_run_id IS DISTINCT FROM v_run.id"),
    ("replacement_running", "NOT (v_rep.status IN ('skipped', 'published', 'media_ready') OR (v_rep.status = 'failed' "
                            "AND v_rep.attempts >= c_max_attempts))"),
    ("replacement_has_posts", "EXISTS (SELECT 1 FROM public.marketing_posts p WHERE p.run_id = v_rep.id)"),
    ("restored:replayed", "v_run.metadata ? 'restored_from'"),
    ("too_late", "(v_judge AT TIME ZONE c_zone)::date <> v_run.run_date"),
)
RESTORE_SQL_WHERES = (
    ("marketing_runs", "WHERE id = p_run_id"),
    ("marketing_runs", "WHERE id = p_run_id FOR UPDATE"),
    ("marketing_runs", "WHERE r.run_date = v_run.run_date AND r.lane = v_run.lane ORDER BY r.replacement DESC LIMIT 1"),
    ("marketing_runs", "WHERE id = v_rep_id FOR UPDATE"),
    ("marketing_posts", "WHERE p.run_id = v_rep.id"),
    ("marketing_posts", "WHERE p.run_id = v_run.id AND p.metadata -> 'restored' ->> 'by_run_id' = v_rep.id::text"),
    ("marketing_posts", "WHERE p.run_id = v_run.id ORDER BY p.id FOR UPDATE"),
    ("marketing_posts", "WHERE p.run_id = v_run.id AND p.status = 'rejected' AND p.metadata -> 'review' ->> "
                        "'replaced_by_run_id' = v_rep.id::text"),
    ("marketing_runs", "WHERE id = v_run.id"),
)
RESTORE_SQL_ASSIGNS = (
    "v_now := clock_timestamp()",
    "v_rep_text := v_run.metadata -> 'replaced_by' ->> 'run_id'",
    "v_rep_id := v_rep_text::uuid",
    "v_judge := GREATEST(v_now, p_now)",
)


def _api_error(code: str, message: str) -> Exception:
    """What postgrest-py raises for a Postgres / PostgREST error: a str `.code`."""
    from postgrest.exceptions import APIError

    return APIError({"code": code, "message": message, "details": None, "hint": None})


def _ts(value: Any) -> datetime:
    """A timestamptz parameter as PostgREST hands it to Postgres: an ISO string (naive = UTC, the session zone).
    Anything unparseable is the 22007 Postgres raises before the function runs."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise _api_error("22007", f"invalid input syntax for type timestamp with time zone: {value!r}") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _canonical_uuid(value: Any) -> bool:
    """The restore's `c_uuid` regex: lower-case canonical 8-4-4-4-12 hex."""
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(_uuid.UUID(value)) == value
    except ValueError:
        return False


def _uuid_param(value: Any) -> str:
    """A uuid parameter: Postgres accepts any case and answers in canonical lower case; anything else is 22P02."""
    try:
        return str(_uuid.UUID(str(value)))
    except ValueError:
        raise _api_error("22P02", f"invalid input syntax for type uuid: {value!r}") from None


def _day(value: Any) -> str:
    """A `date` column as text (the run-service fake stores what the service sent: a date or its ISO text)."""
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def _et_day(at: datetime) -> str:
    return at.astimezone(_ET).date().isoformat()


def _iso(at: datetime) -> str:
    return at.isoformat()


def _json_number(value: Any) -> bool:
    """`jsonb_typeof(x) = 'number'` (a JSON bool is 'boolean', never a number)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _empty_text(value: Any) -> bool:
    """`NULLIF(metadata ->> key, '') IS NULL`: the key absent, JSON null, or the empty string."""
    return value is None or value == ""


class _Call:
    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn

    def execute(self):
        return SimpleNamespace(data=self._fn())


class ReplacementRpcFake:
    """`supabase.rpc(...)` for the two 215 functions, over plain row lists."""

    def __init__(self, runs: Optional[List[dict]] = None, scripts: Optional[List[dict]] = None,
                 posts: Optional[List[dict]] = None, *, clock: Optional[Callable[[], datetime]] = None,
                 new_id: Optional[Callable[[], str]] = None) -> None:
        self.runs: List[dict] = runs if runs is not None else []
        self.scripts: List[dict] = scripts if scripts is not None else []
        self.posts: List[dict] = posts if posts is not None else []
        #: (name, params) of every call, in order.
        self.calls: List[tuple] = []
        #: The statements the LAST call ran, in order (the names the SQL scan reads).
        self.trace: List[str] = []
        #: (function name, trace) of every call.
        self.traces: List[tuple] = []
        #: The advisory-lock keys taken, in order.
        self.locks: List[str] = []
        #: Function names whose call raises a transient error before anything runs, or "*" for both.
        self.fail: set = set()
        #: Function names whose call COMMITS and then loses its response (the caller sees a transport error).
        self.lose_response: set = set()
        #: True → every call raises `missing_code` (migration 215 not applied).
        self.missing = False
        self.missing_code = "PGRST202"
        #: True → UNIQUE (run_date, lane) is back (210 re-run after 215): every replacement INSERT is a 23505.
        self.legacy_unique = False
        #: The SQL's constants (tests may change one to reach a backstop arm the other masks).
        self.per_day = C_PER_DAY
        self.max_replacement = C_MAX
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._new_id = new_id or (lambda: str(_uuid.uuid4()))

    # ── plumbing ──────────────────────────────────────────────────────────────
    def now(self) -> datetime:
        return self._clock()

    def rpc(self, name: str, params: dict):
        def run():
            self.calls.append((name, dict(params)))
            if self.missing:
                raise _api_error(self.missing_code, f"Could not find the function public.{name}")
            if name in self.fail or "*" in self.fail:
                raise RuntimeError(f"{name}: upstream connection reset")
            handler = {REQUEST: self.marketing_request_replacement, RESTORE: self.marketing_restore_replaced}.get(name)
            if handler is None:
                raise _api_error("PGRST202", f"Could not find the function public.{name}")
            out = handler(**params)
            if name in self.lose_response:
                raise RuntimeError(f"{name}: connection reset after COMMIT (the response was lost)")
            return out
        return _Call(run)

    def _begin(self, name: str) -> None:
        self.trace = []
        self.traces.append((name, self.trace))

    def _at(self, statement: str) -> None:
        self.trace.append(statement)

    def _lock(self, run_date: Any) -> None:
        self._at("lock")
        self.locks.append(f"marketing_request_replacement:{_day(run_date)}")

    # ── row helpers mirroring the columns ─────────────────────────────────────
    @staticmethod
    def _lane(row: dict) -> str:
        return row.get("lane") or "main"

    @staticmethod
    def _replacement(row: dict) -> int:
        n = row.get("replacement")
        return 0 if n is None else n

    @staticmethod
    def _meta(row: dict) -> dict:
        return row["metadata"] if isinstance(row.get("metadata"), dict) else {}

    def _run(self, run_id: str) -> Optional[dict]:
        return next((r for r in self.runs if str(r.get("id")) == run_id), None)

    def _newest(self, run: dict) -> dict:
        """`SELECT … FROM marketing_runs r WHERE run_date = … AND lane = … ORDER BY r.replacement DESC LIMIT 1`."""
        same = [r for r in self.runs
                if _day(r.get("run_date")) == _day(run.get("run_date")) and self._lane(r) == self._lane(run)]
        return max(same, key=self._replacement)

    def _posts_of(self, run_id: Any) -> List[dict]:
        return [p for p in self.posts if str(p.get("run_id")) == str(run_id)]

    @staticmethod
    def _validate(fn: str, p_run_id: Any, p_by: Any, stamp: Any, stamp_name: str) -> None:
        if p_run_id is None or p_by is None or not 1 <= len(p_by) <= 64 or stamp is None:
            raise _api_error("22023", f"{fn}: p_run_id, p_by (1-64 chars) and {stamp_name} are required")

    # ── marketing_request_replacement ────────────────────────────────────────
    def marketing_request_replacement(self, p_run_id: Any, p_by: Any, p_not_after: Any) -> Dict[str, Any]:
        self._begin(REQUEST)
        self._validate(REQUEST, p_run_id, p_by, p_not_after, "p_not_after")
        run_id = _uuid_param(p_run_id)
        not_after = _ts(p_not_after)

        self._at("not_found")
        found = self._run(run_id)
        if found is None:
            return {"outcome": "not_found"}

        self._lock(found.get("run_date"))
        now = self.now()

        self._at("lock:marketing_runs")
        run = self._run(run_id)
        self._at("not_found")
        if run is None:
            return {"outcome": "not_found"}

        # The replay is answered BEFORE the time check (a retry after the cutoff of a request that committed before it).
        newest = self._newest(run)
        self._at("requested:replayed")
        if (str(newest.get("replaces_run_id")) == str(run.get("id"))
                and self._replacement(newest) == self._replacement(run) + 1
                and "restored_from" not in self._meta(run)):
            return self._replay(run, newest)

        self._at("too_late")
        if now >= not_after or _et_day(now) != _day(run.get("run_date")):
            return {"outcome": "too_late"}
        self._at("already_replaced")
        if self._replacement(newest) > self._replacement(run):
            return {"outcome": "already_replaced", "run_id": newest.get("id"),
                    "replacement": self._replacement(newest), "status": newest.get("status")}
        self._at("not_ready")
        if run.get("status") != "media_ready":
            return {"outcome": "not_ready", "status": run.get("status")}

        script = next((s for s in self.scripts if str(s.get("run_id")) == run_id), None)
        self._at("not_money_map")
        if script is None:
            return {"outcome": "not_money_map"}
        self._at("not_money_map")
        if script.get("status") != "accepted" or script.get("template_id") != C_SERIES:
            return {"outcome": "not_money_map"}

        self._at("lock:marketing_posts")
        posts = sorted(self._posts_of(run_id), key=lambda p: str(p.get("id")))
        blocking = sorted({p.get("status") for p in posts if p.get("status") in BLOCKING})
        self._at("member_decided")
        if blocking:
            return {"outcome": "member_decided", "statuses": ",".join(blocking)}
        pending = [p for p in posts if p.get("status") == "pending_review"]
        self._at("nothing_pending")
        if not pending:
            return {"outcome": "nothing_pending"}

        today = sum(1 for r in self.runs
                    if _day(r.get("run_date")) == _day(run.get("run_date")) and self._replacement(r) >= 1)
        self._at("cap_reached")
        if today >= self.per_day or self._replacement(run) >= self.max_replacement:
            return {"outcome": "cap_reached", "today": today}
        nxt = self._replacement(run) + 1

        self._at("insert:marketing_runs")
        lane = self._lane(run)
        for r in self.runs:   # the claim key, checked before the row lands (a raise writes nothing)
            same_day_lane = _day(r.get("run_date")) == _day(run.get("run_date")) and self._lane(r) == lane
            if same_day_lane and (self.legacy_unique or self._replacement(r) == nxt):
                raise _api_error("23505", "duplicate key value violates unique constraint "
                                          "marketing_runs_run_date_lane_replacement_key")
        new_id = self._new_id()
        stamp = _iso(now)
        self.runs.append({
            "id": new_id, "run_date": run.get("run_date"), "lane": lane, "replacement": nxt,
            "replaces_run_id": run.get("id"), "status": "planned", "stage": "planned",
            "content_class": run.get("content_class", "A"), "dry_run": run.get("dry_run", True), "attempts": 0,
            "timings": {},
            "metadata": {"replacement_request": {"at": stamp, "from_run_id": run.get("id"),
                                                 "from_source_ref": script.get("source_ref")}},
            "created_at": stamp, "updated_at": stamp,
        })

        self._at("update:marketing_posts")
        rejected, messages = [], set()
        for p in pending:
            meta = dict(self._meta(p))
            meta["review"] = {"decision": "rejected", "by": p_by, "at": stamp,
                              "replaced_by_run_id": new_id, "replacement": nxt}
            p.update(status="rejected", updated_at=stamp, metadata=meta)
            rejected.append(str(p.get("id")))
            mid = meta.get("review_message_id")
            if _json_number(mid) and _empty_text(meta.get("review_preview_at")):
                messages.add(mid)

        self._at("update:marketing_runs")
        run["metadata"] = {**self._meta(run), "replaced_by": {
            "run_id": new_id, "replacement": nxt, "at": stamp, "by": p_by, "rejected": len(rejected),
            "replacements_today": today + 1, "not_after": _iso(not_after)}}
        run["updated_at"] = stamp

        self._at("requested")
        return {"outcome": "requested", "replayed": False, "run_id": new_id, "replacement": nxt,
                "replacements_today": today + 1, "rejected_post_ids": sorted(rejected),
                "review_message_ids": sorted(messages), "from_source_ref": script.get("source_ref")}

    def _replay(self, run: dict, newest: dict) -> Dict[str, Any]:
        """The replay branch's read-backs (no write)."""
        new_id = str(newest.get("id"))
        recorded = (self._meta(run).get("replaced_by") or {}).get("replacements_today") \
            if isinstance(self._meta(run).get("replaced_by"), dict) else None
        if not _json_number(recorded):
            recorded = sum(1 for r in self.runs
                           if _day(r.get("run_date")) == _day(run.get("run_date")) and self._replacement(r) >= 1)
        mine = [p for p in self._posts_of(run.get("id"))
                if isinstance(self._meta(p).get("review"), dict)
                and str(self._meta(p)["review"].get("replaced_by_run_id")) == new_id]
        messages = {self._meta(p).get("review_message_id") for p in mine
                    if _json_number(self._meta(p).get("review_message_id"))
                    and _empty_text(self._meta(p).get("review_preview_at"))}
        request = self._meta(newest).get("replacement_request")
        return {"outcome": "requested", "replayed": True, "run_id": newest.get("id"),
                "replacement": self._replacement(newest), "status": newest.get("status"),
                "replacements_today": recorded,
                "rejected_post_ids": sorted(str(p.get("id")) for p in mine),
                "review_message_ids": sorted(messages),
                "from_source_ref": request.get("from_source_ref") if isinstance(request, dict) else None}

    # ── marketing_restore_replaced ───────────────────────────────────────────
    def marketing_restore_replaced(self, p_run_id: Any, p_by: Any, p_now: Any) -> Dict[str, Any]:
        self._begin(RESTORE)
        self._validate(RESTORE, p_run_id, p_by, p_now, "p_now")
        run_id = _uuid_param(p_run_id)
        caller_now = _ts(p_now)

        self._at("not_found")
        found = self._run(run_id)
        if found is None:
            return {"outcome": "not_found"}

        self._lock(found.get("run_date"))
        now = self.now()

        self._at("lock:marketing_runs")
        run = self._run(run_id)
        self._at("not_found")
        if run is None:
            return {"outcome": "not_found"}

        # (1) the replaced run is still media_ready
        self._at("not_restorable")
        if run.get("status") != "media_ready":
            return {"outcome": "not_restorable", "status": run.get("status")}

        # (2) replaced_by names the newest run of the (day, lane), which replaces this one, and that run is over
        replaced_by = self._meta(run).get("replaced_by")
        rep_text = replaced_by.get("run_id") if isinstance(replaced_by, dict) else None
        self._at("not_restorable")
        if not _canonical_uuid(rep_text):
            return {"outcome": "not_restorable"}
        newest = self._newest(run)
        self._at("not_restorable")
        if str(newest.get("id")) != rep_text:
            return {"outcome": "not_restorable"}
        self._at("lock:marketing_runs")
        rep = self._run(rep_text)
        self._at("not_restorable")
        if rep is None:
            return {"outcome": "not_restorable"}
        self._at("not_restorable")
        if str(rep.get("replaces_run_id")) != str(run.get("id")):
            return {"outcome": "not_restorable"}
        self._at("replacement_running")
        over = rep.get("status") in FINAL_RUN_STATUSES or (
            rep.get("status") == "failed" and int(rep.get("attempts") or 0) >= C_MAX_ATTEMPTS)
        if not over:
            return {"outcome": "replacement_running", "status": rep.get("status"),
                    "attempts": int(rep.get("attempts") or 0)}

        # (3) the replacement made no post at all, of any status
        self._at("replacement_has_posts")
        if self._posts_of(rep.get("id")):
            return {"outcome": "replacement_has_posts"}

        # (5) already restored: read back, never a second write — answered before the day check (4)
        self._at("restored:replayed")
        if "restored_from" in self._meta(run):
            ids = [str(p.get("id")) for p in self._posts_of(run_id)
                   if isinstance(self._meta(p).get("restored"), dict)
                   and str(self._meta(p)["restored"].get("by_run_id")) == rep_text]
            return {"outcome": "restored", "replayed": True, "post_ids": sorted(ids)}

        # (4) the run's own ET day — until its end, not the tap cutoff; the later clock judges
        judge = max(now, caller_now)
        self._at("too_late")
        if _et_day(judge) != _day(run.get("run_date")):
            return {"outcome": "too_late"}

        self._at("lock:marketing_posts")
        stamp = _iso(now)
        self._at("update:marketing_posts")
        restored = []
        for p in sorted(self._posts_of(run_id), key=lambda x: str(x.get("id"))):
            meta = self._meta(p)
            review = meta.get("review")
            if p.get("status") != "rejected" or not isinstance(review, dict) \
                    or str(review.get("replaced_by_run_id")) != rep_text:
                continue
            kept = {k: v for k, v in meta.items() if k != "review" and k not in C_OFFER_STAMPS}
            kept["restored"] = {"by_run_id": rep_text, "at": stamp, "by": p_by}
            p.update(status="pending_review", updated_at=stamp, metadata=kept)
            restored.append(str(p.get("id")))

        self._at("update:marketing_runs")
        run["metadata"] = {**self._meta(run), "restored_from": {"run_id": rep_text, "at": stamp}}
        run["updated_at"] = stamp

        self._at("restored")
        return {"outcome": "restored", "replayed": False, "post_ids": sorted(restored)}
