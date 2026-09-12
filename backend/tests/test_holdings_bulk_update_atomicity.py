"""A 400 from the bulk-holdings endpoint must mean NOTHING was written.

`PUT /portfolios/{id}/holdings` validated and wrote in one loop, then raised 400 at the
end — so a payload with one bad row persisted every good row that preceded it AND answered
a failure (found 2026-09-12).

That is worse than either outcome alone. iOS routes a non-2xx through
`AppActions.reportMutationFailure` and reverts its optimistic UI (`auth.md` §6), so the
user watched their edit undo itself while the server kept half of it, and the two stayed
out of sync until something forced a refetch.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.portfolios as pf

_SRC = pathlib.Path(pf.__file__).read_text(encoding="utf-8")


def _fn(name: str):
    tree = ast.parse(_SRC)
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


class _Q:
    """Records every write it is asked to perform."""

    def __init__(self, writes, table):
        self.writes, self.table_name, self._payload = writes, table, None

    def update(self, values):
        self._payload = values
        return self

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        if col == "ticker":
            self._ticker = val
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        if self._payload is not None:
            self.writes.append((self.table_name, getattr(self, "_ticker", None), self._payload))
            return type("R", (), {"data": [{"ticker": getattr(self, "_ticker", None)}]})()
        return type("R", (), {"data": [{"id": "p1", "user_id": "u1", "name": "Holdings",
                                        "sort_order": 0, "is_active": True,
                                        "created_at": "2026-01-01T00:00:00+00:00",
                                        "updated_at": "2026-01-01T00:00:00+00:00"}]})()


class _SB:
    def __init__(self):
        self.writes = []

    def table(self, name):
        return _Q(self.writes, name)


@pytest.mark.asyncio
async def test_a_rejected_payload_writes_nothing(monkeypatch):
    sb = _SB()
    req = pf.SetPortfolioHoldingsRequest(items=[
        pf.HoldingItem(ticker="AAPL", shares=10.0, market_value=1900.0),   # valid, FIRST
        pf.HoldingItem(ticker="MSFT", shares=-5.0),                        # invalid
    ])
    with pytest.raises(HTTPException) as info:
        await pf.set_portfolio_holdings(
            "p1", req, user={"id": "u1"}, supabase=sb,
        )
    assert info.value.status_code == 400
    assert "MSFT" in str(info.value.detail)
    assert sb.writes == [], (
        "the valid rows ahead of the invalid one were persisted and the call still "
        f"answered 400 — wrote {sb.writes}"
    )


@pytest.mark.asyncio
async def test_a_fully_valid_payload_still_writes_every_row(monkeypatch):
    """Control: a guard that rejected everything would pass the test above."""
    sb = _SB()
    monkeypatch.setattr(pf, "_fetch_portfolio_items", lambda *a, **k: [])
    req = pf.SetPortfolioHoldingsRequest(items=[
        pf.HoldingItem(ticker="AAPL", shares=10.0, market_value=1900.0),
        pf.HoldingItem(ticker="MSFT", shares=5.0, market_value=2100.0),
    ])
    await pf.set_portfolio_holdings("p1", req, user={"id": "u1"}, supabase=sb)
    written = [t for tbl, t, _v in sb.writes if tbl == "portfolio_items"]
    assert written == ["AAPL", "MSFT"], written


def test_validation_precedes_every_write_in_source():
    """Behavioural twin: the handler must not regain a write inside the validation loop."""
    body = ast.get_source_segment(_SRC, _fn("set_portfolio_holdings"))
    assert body
    raise_at = body.index('raise HTTPException(status_code=400')
    first_update = body.index('.update(values)')
    assert raise_at < first_update, (
        "a holdings write happens before the payload is fully validated — a 400 then "
        "leaves partial state behind"
    )
