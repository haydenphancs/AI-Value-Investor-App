"""Replacing a group's tickers must not be able to lose the user's holdings.

`PUT /portfolios/{id}/tickers` is DELETE-all-then-INSERT as two independent PostgREST
statements. The in-memory `rows` is the ONLY carrier of the hand-entered `shares` /
`market_value` for the tickers being kept, so an INSERT that failed after the DELETE
committed left the group empty AND destroyed those numbers: the client's retry re-reads
`existing_items` (now empty) and restores membership with every `shares` NULL, which reads
as "you never entered them" (found 2026-09-12).

`retry_idempotent_async` exists for exactly this shape — its docstring says so — and was
not used here.

⚠️ It must be the ASYNC form. The handler is `async def`, and the sync twin's backoff is a
bare `time.sleep` in the coroutine's own frame: three attempts freeze the single Railway
uvicorn worker for ~0.75 s plus up to six serialised blocking PostgREST round trips, worst
exactly when Supabase is degraded and the most requests are queued.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import app.api.v1.endpoints.portfolios as pf

_SRC = Path(pf.__file__).read_text(encoding="utf-8")


def _fn_source(name: str) -> str:
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(_SRC, fn)


def test_the_delete_and_insert_are_one_replayable_unit():
    body = _fn_source("set_portfolio_tickers")
    code = "\n".join(re.sub(r"#.*$", "", l) for l in body.splitlines())
    assert "retry_idempotent_async(" in code, (
        "the DELETE and the INSERT are still two unguarded statements — an INSERT failure "
        "leaves the group empty and the holdings unrecoverable"
    )
    assert "retry_idempotent_sync(" not in code, (
        "the SYNC retry blocks the event loop from inside an `async def` handler — "
        "`time.sleep` backoff plus blocking PostgREST round trips on the one worker"
    )
    # The callable must CONTAIN the delete, not sit beside it: a retry that replays only
    # the insert would double-insert on a partial success.
    inner = code[code.index("def _replace_items"):code.index("retry_idempotent_async(")]
    assert ".delete()" in inner and ".insert(" in inner, (
        "the retried callable must own BOTH statements"
    )


def test_the_holdings_snapshot_is_taken_outside_the_retried_block():
    """A retry re-reads nothing: it must rebuild from the snapshot taken BEFORE the
    delete, or the second attempt writes NULL shares for every kept ticker."""
    body = _fn_source("set_portfolio_tickers")
    assert body.index("existing_holdings = {") < body.index("def _replace_items")


def test_a_failure_is_logged_with_the_ids_and_re_raised():
    body = _fn_source("set_portfolio_tickers")
    tail = body[body.index("retry_idempotent_async("):]
    assert "logger.error(" in tail and "exc_info=True" in tail, (
        "a data-loss window must not degrade quietly"
    )
    assert "portfolio_id" in tail and "user[\"id\"]" in tail
    assert re.search(r"\n\s+raise\b", tail), "the handler must not swallow it into a 200"


@pytest.mark.asyncio
async def test_a_transient_insert_failure_is_retried_and_the_holdings_survive(monkeypatch):
    """Behavioural: the second attempt must restore shares, not NULL them."""
    store = {"items": [
        {"ticker": "AAPL", "shares": 10.0, "market_value": 1900.0, "position": 0},
        {"ticker": "MSFT", "shares": 5.0, "market_value": 2100.0, "position": 1},
    ]}
    attempts = {"insert": 0}

    class _Q:
        def __init__(self, table):
            self.table_name = table
            self._op = None

        def select(self, *a, **k):
            self._op = "select"
            return self

        def eq(self, *a, **k):
            return self

        def delete(self):
            self._op = "delete"
            return self

        def insert(self, rows):
            self._op, self._rows = "insert", rows
            return self

        def update(self, *a, **k):
            self._op = "update"
            return self

        def execute(self):
            if self.table_name == "portfolio_items":
                if self._op == "select":
                    return type("R", (), {"data": list(store["items"])})()
                if self._op == "delete":
                    store["items"] = []
                    return type("R", (), {"data": []})()
                if self._op == "insert":
                    attempts["insert"] += 1
                    if attempts["insert"] == 1:
                        # The shape `is_transient_supabase_error` recognises: an INT code
                        # on a postgrest APIError is a Cloudflare EDGE status, not a
                        # PostgREST answer (memory: project_supabase_transient_520).
                        exc = RuntimeError("Error 520: ")
                        exc.code = 520
                        raise exc
                    store["items"] = [dict(r) for r in self._rows]
                    return type("R", (), {"data": []})()
            return type("R", (), {"data": [{"id": "p1", "name": "Holdings"}]})()

    class _SB:
        def table(self, name):
            return _Q(name)

    sb = _SB()
    # Drive the inner block the handler builds, through the real retry helper.
    existing = {r["ticker"]: {"shares": r["shares"], "market_value": r["market_value"]}
                for r in store["items"]}

    def _replace():
        sb.table("portfolio_items").delete().eq("portfolio_id", "p1").execute()
        rows = [{"portfolio_id": "p1", "ticker": t, "position": i,
                 "shares": existing.get(t, {}).get("shares"),
                 "market_value": existing.get(t, {}).get("market_value")}
                for i, t in enumerate(["MSFT", "AAPL"])]
        sb.table("portfolio_items").insert(rows).execute()

    await pf.retry_idempotent_async(_replace, what="test", backoff_seconds=0)
    assert attempts["insert"] == 2, "the transient was not retried"
    by_ticker = {r["ticker"]: r for r in store["items"]}
    assert by_ticker["AAPL"]["shares"] == 10.0 and by_ticker["MSFT"]["shares"] == 5.0, (
        "the retry rebuilt the rows with NULL shares — the snapshot must survive the retry"
    )
