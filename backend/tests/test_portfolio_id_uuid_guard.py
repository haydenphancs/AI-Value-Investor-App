"""A non-UUID portfolio id is a 404 answered before any query, never a 500.

Sentry 2026-10-08: `GET /api/v1/portfolios/store-shot-sample/insights` ended in an unhandled
`APIError 22P02 invalid input syntax for type uuid`. A DEBUG build's store-screenshot mode had
saved its sample group id as the device's active-group hint, and the next normal launch sent
it. `portfolios.id` is a Postgres `uuid`, so PostgREST forwarded the string and Postgres'
cast failed. Every `/{portfolio_id}` route resolves the row through `_get_portfolio_or_404`,
which now refuses such an id itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import portfolios as ep

_GOOD = "3f2b8c1e-9a4d-4e6b-8f21-0c5d7a9e1b42"


@pytest.mark.parametrize(
    "bad",
    [
        "store-shot-sample",          # the reported id
        "",
        " ",
        "123",
        _GOOD + " ",                  # trailing space
        " " + _GOOD,
        _GOOD.replace("-", ""),       # Python's uuid.UUID takes it; we only issue the canonical form
        "urn:uuid:" + _GOOD,          # uuid.UUID takes it; Postgres answers 22P02
        "{" + _GOOD + "}",
        _GOOD[:-1] + "g",             # not hex
        _GOOD + "0",                  # too long
        "x" * 10_000,                 # huge input: answered without a query
        None,
        12345,
    ],
)
def test_a_non_uuid_id_is_404_before_any_query(bad):
    sb = MagicMock()
    with pytest.raises(HTTPException) as exc:
        ep._get_portfolio_or_404(sb, "user-1", bad)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Portfolio not found"
    sb.table.assert_not_called()


@pytest.mark.parametrize("good", [_GOOD, _GOOD.upper()])
def test_a_uuid_id_still_reaches_the_owner_scoped_query(good):
    sb = MagicMock()
    query = sb.table.return_value.select.return_value.eq.return_value.eq.return_value
    query.limit.return_value.execute.return_value = MagicMock(data=[{"id": good, "user_id": "user-1"}])
    row = ep._get_portfolio_or_404(sb, "user-1", good)
    assert row == {"id": good, "user_id": "user-1"}
    sb.table.assert_called_once_with("portfolios")
    sb.table.return_value.select.return_value.eq.assert_called_once_with("user_id", "user-1")
    sb.table.return_value.select.return_value.eq.return_value.eq.assert_called_once_with("id", good)


def test_a_uuid_with_no_row_is_still_404():
    sb = MagicMock()
    query = sb.table.return_value.select.return_value.eq.return_value.eq.return_value
    query.limit.return_value.execute.return_value = MagicMock(data=[])
    with pytest.raises(HTTPException) as exc:
        ep._get_portfolio_or_404(sb, "user-1", _GOOD)
    assert exc.value.status_code == 404


def test_the_insights_route_resolves_through_the_guard():
    """The reported route must reach `_get_portfolio_or_404` before the insights service,
    or the service's own `.eq("portfolio_id", …)` would hit the same 22P02."""
    import inspect

    src = inspect.getsource(ep.get_portfolio_insights)
    guard = src.find("_get_portfolio_or_404")
    service = src.find("compute_insights_for_portfolio")
    assert guard != -1 and service != -1
    assert guard < service
