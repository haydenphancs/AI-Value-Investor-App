"""The placeholder set is ONE definition with three readers: the Python writers
(`_classification_common.PLACEHOLDER_TEXT`), the feed's on-read normalisation, and the
one-shot clean-up in migration 172. Drift between them re-creates the "N/A 0%" legend row
(TestFlight 1.0 (8)) through whichever door was left open.
"""

import re
from pathlib import Path

import pytest

import app.services.tracking_service as ts
from app.services._classification_common import PLACEHOLDER_TEXT
from app.services.tracking_service import TrackingService

from test_tracking_feed_inflight import _FakeSupabase

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO / "backend/database/migrations/172_null_placeholder_sector_industry.sql"


def _sql_sets():
    sql = _MIGRATION.read_text(encoding="utf-8")
    # Only the UPDATE statements' IN lists — the VERIFY block is a comment.
    code = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    lists = re.findall(r"IN \(([^)]*)\)", code)
    assert len(lists) == 2, "expected one IN list for sector and one for industry"
    return [set(re.findall(r"'([^']*)'", body)) for body in lists]


def test_migration_172_nulls_exactly_the_python_placeholder_set():
    for literal_set in _sql_sets():
        assert literal_set == set(PLACEHOLDER_TEXT), (literal_set ^ set(PLACEHOLDER_TEXT))


def test_migration_172_is_idempotent_and_touches_only_the_two_columns():
    sql = _MIGRATION.read_text(encoding="utf-8")
    code = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    assert code.count("UPDATE public.watchlist_items") == 2
    assert re.search(r"SET sector = NULL\s+WHERE sector IS NOT NULL", code)
    assert re.search(r"SET industry = NULL\s+WHERE industry IS NOT NULL", code)
    assert "country" not in code, "country has a 'US' default and no observed placeholder"
    assert "DELETE" not in code.upper() and "DROP" not in code.upper()


@pytest.mark.asyncio
async def test_the_feed_never_publishes_a_placeholder_sector(monkeypatch):
    """Belt and braces until 172 runs: a row that still holds "N/A" goes out `null`."""
    watchlist = [{"id": 1, "ticker": "SPY", "company_name": "SPDR", "asset_type": "etf", "sector": "N/A"},
                 {"id": 2, "ticker": "NVDA", "company_name": "NVIDIA", "asset_type": "stock", "sector": "Technology"}]
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def _quotes(self, tickers):
        return {t: {"price": 100.0, "previousClose": 99.0} for t in tickers}
    async def _nothing(self, *a, **k): return {}
    async def _no_list(self, *a, **k): return []
    async def _backfill(self, user_id, watchlist): return None
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _nothing),
                     ("_get_earnings_alerts", _no_list), ("_get_whale_trade_alerts", _no_list),
                     ("_get_analyst_rating_alerts", _no_list),
                     ("_get_insider_transaction_alerts", _no_list),
                     ("_backfill_classification", _backfill)]:
        monkeypatch.setattr(TrackingService, name, fn)
    ts._feed_cache.clear(); ts._feed_inflight.clear()

    feed = await TrackingService().get_tracking_feed("u-placeholder")
    by = {a.ticker: a for a in feed.assets}
    assert by["SPY"].sector is None
    assert by["NVDA"].sector == "Technology"
