"""Unit tests for FMPClient._latest_perf_snapshot — the sector/industry
performance-snapshot fix.

FMP changed the `*-performance-snapshot` endpoints so that:
  1. `date` is REQUIRED (a param-less call now 400s), and
  2. the value field was renamed `changesPercentage` → `averageChange`, and
  3. a non-trading `date` returns `[]`.

`_latest_perf_snapshot` walks back from today to the latest trading day with
data and re-aliases the field to `changesPercentage` (the key downstream
consumers read). These tests pin that behavior with a fake `_make_request`
(NO network / no live FMP — per tests/testing rules).
"""

from datetime import datetime, timezone, timedelta

import pytest

from app.integrations.fmp import FMPClient


def _client_with_responses(responses: dict) -> FMPClient:
    """FMPClient whose `_make_request` returns canned data keyed by the `date`
    param and records the dates it was asked for (on `._asked`)."""
    client = FMPClient()
    asked: list = []

    async def fake_make_request(endpoint, params=None):
        d = (params or {}).get("date")
        asked.append(d)
        return responses.get(d, [])

    client._make_request = fake_make_request  # type: ignore[assignment]
    client._asked = asked  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_walks_back_to_latest_trading_day_and_aliases():
    # Only the date 2 days back has data (today + yesterday are "weekend" → []).
    today = datetime.now(timezone.utc).date()
    d2 = (today - timedelta(days=2)).isoformat()
    client = _client_with_responses(
        {d2: [{"sector": "Technology", "averageChange": 1.25}]}
    )

    out = await client._latest_perf_snapshot("sector-performance-snapshot")

    assert len(out) == 1
    assert out[0]["sector"] == "Technology"
    assert out[0]["changesPercentage"] == 1.25          # aliased from averageChange
    assert client._asked == [                            # stopped at first non-empty
        today.isoformat(),
        (today - timedelta(days=1)).isoformat(),
        d2,
    ]


@pytest.mark.asyncio
async def test_empty_all_days_returns_empty_and_exhausts_lookback():
    client = _client_with_responses({})  # every date → []
    out = await client._latest_perf_snapshot(
        "industry-performance-snapshot", max_lookback=4
    )
    assert out == []
    assert len(client._asked) == 4       # tried the full lookback window, gave up


@pytest.mark.asyncio
async def test_alias_never_overwrites_existing_and_tolerates_missing_field():
    today = datetime.now(timezone.utc).date().isoformat()
    client = _client_with_responses(
        {today: [
            {"industry": "A", "averageChange": 2.0, "changesPercentage": 9.9},
            {"industry": "B", "averageChange": 3.0},
            {"industry": "C"},  # neither field present
            "not-a-dict",       # malformed upstream row must not crash
        ]}
    )

    out = await client._latest_perf_snapshot("industry-performance-snapshot")

    assert out[0]["changesPercentage"] == 9.9   # existing value preserved
    assert out[1]["changesPercentage"] == 3.0   # aliased
    assert "changesPercentage" not in out[2]    # nothing to alias → left as-is
    assert out[3] == "not-a-dict"               # passed through untouched
