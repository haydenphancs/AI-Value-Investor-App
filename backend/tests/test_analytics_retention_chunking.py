"""Retention must actually walk its chunks.

`sweep_expired` selects ids with `.limit(_SWEEP_CHUNK)` and stops when a page comes back
short. With `_SWEEP_CHUNK = 5000` against PostgREST's ~1,000-row server cap, every page was
short by construction: the loop exited after ONE chunk, `_SWEEP_MAX_CHUNKS = 20` was dead
code, and the "≤100k rows per pass" contract in its own comment delivered ~1,000. On the
highest-volume table in the schema that lets retention fall permanently behind ingest while
the log reports success (found 2026-09-12 — the sixth instance of the inert-`.limit()` bug
this codebase has hit).
"""

from __future__ import annotations

import pytest

import app.services.analytics_service as an

_SERVER_MAX_ROWS = 1000          # what PostgREST actually returns on this project


class _Table:
    def __init__(self, state):
        self.s = state
        self._op = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def lt(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def delete(self, **k):
        self._op = "delete"
        return self

    def in_(self, _col, ids):
        self._ids = ids
        return self

    def execute(self):
        if self._op == "select":
            n = min(getattr(self, "_limit", _SERVER_MAX_ROWS), _SERVER_MAX_ROWS)
            take = self.s["rows"][:n]
            self.s["selects"].append(len(take))
            return type("R", (), {"data": [{"id": i} for i in take]})()
        self.s["rows"] = [r for r in self.s["rows"] if r not in set(self._ids)]
        self.s["deleted"] += len(self._ids)
        return type("R", (), {"data": []})()


def _svc(n_rows):
    state = {"rows": list(range(n_rows)), "deleted": 0, "selects": []}
    svc = an.AnalyticsService.__new__(an.AnalyticsService)
    svc.supabase = type("S", (), {"table": lambda _s, _n: _Table(state)})()
    return svc, state


def test_the_chunk_size_does_not_exceed_the_server_row_cap():
    assert an.AnalyticsService._SWEEP_CHUNK <= _SERVER_MAX_ROWS, (
        "a chunk larger than PostgREST's cap makes every page 'short', so the loop exits "
        "after one iteration and the chunk budget is dead code"
    )


def test_one_pass_deletes_far_more_than_a_single_page():
    svc, state = _svc(7_500)
    deleted = svc.sweep_expired()
    assert len(state["selects"]) > 1, (
        "the sweep stopped after ONE page — the chunk loop never iterates"
    )
    assert deleted == 7_500, f"only {deleted} of 7,500 expired rows were removed"


def test_the_pass_is_still_bounded():
    """Chunking exists so one pass cannot become an unbounded DELETE that times out and
    retries forever without progress."""
    svc, state = _svc(400_000)
    deleted = svc.sweep_expired()
    cap = an.AnalyticsService._SWEEP_CHUNK * an.AnalyticsService._SWEEP_MAX_CHUNKS
    assert deleted == cap, f"expected the pass to stop at {cap}, got {deleted}"
    assert state["rows"], "the sweep drained the table in one pass — it is unbounded"


def test_an_empty_table_costs_one_select_and_no_delete():
    svc, state = _svc(0)
    assert svc.sweep_expired() == 0
    assert state["deleted"] == 0 and len(state["selects"]) == 1
