"""A daily `percent_move` alert measures TODAY's move — never yesterday's, read at 4 AM.

Same trap as the Updates sweeper's `ticker_move` alert (TestFlight, 2026-09-15): the
price-alert loop runs from 04:00 ET, and pre-market — before a ticker's first print — the
batch row's `changePercentage` is still YESTERDAY's whole session (the screener price is
yesterday's close, the denominator the close before it), stamped `changeSession` with
yesterday's date. A `daily` rule keys its dedup on the ET date, so the 04:00 pass
re-fired yesterday's move under a fresh key. `evaluate_once` now reads the change through
`session_change_percent`, and a prior-session change holds with `no_percent_reading`.

No network: the price source is the documented `svc.price` seam, the rules and the
persistence are patched, and the dispatcher is a recording stub.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.services.price_alert_service as mod
from app.services.price_alert_engine import evaluate_alert
from app.services.price_alert_service import PriceAlertService
from app.utils.market_hours import previous_trading_day, session_trading_date

CURRENT = session_trading_date()
PRIOR = previous_trading_day(CURRENT)


class _Quotes:
    def __init__(self, rows):
        self.rows = rows

    async def get_quotes_list(self, tickers):
        return list(self.rows)


class _Dispatcher:
    def __init__(self):
        self.sent = []

    async def notify_users(self, users, **kw):
        self.sent.append(kw)
        return len(users)


def _rule(**over):
    base = {
        "id": "11111111-1111-1111-1111-111111111111", "user_id": "u1", "ticker": "TER",
        "asset_type": "stock", "kind": "percent_move", "threshold": 5.0,
        "repeat_mode": "daily", "armed": True, "last_price": None, "trigger_count": 0,
    }
    base.update(over)
    return base


async def _run(rows, rules):
    svc = PriceAlertService()
    svc.price = _Quotes(rows)
    dispatcher = _Dispatcher()
    persisted = []
    with patch.object(PriceAlertService, "_active_universe", lambda self, *a, **k: ["TER"]), \
         patch.object(PriceAlertService, "_active_rules", lambda self, tickers: list(rules)), \
         patch.object(PriceAlertService, "_persist", lambda self, rule, decision: persisted.append(decision)), \
         patch.object(mod, "get_push_dispatch_service", lambda: dispatcher):
        stats = await svc.evaluate_once()
    return stats, dispatcher, persisted


def _ter(change_pct, stamp):
    return {"symbol": "TER", "price": 371.47, "change": -57.0, "changePercentage": change_pct,
            "changesPercentage": change_pct, "changeSession": stamp}


@pytest.mark.asyncio
async def test_yesterdays_move_read_pre_market_does_not_fire():
    stats, dispatcher, persisted = await _run([_ter(-13.3, PRIOR.isoformat())], [_rule()])
    assert stats["fired"] == 0 and dispatcher.sent == []
    assert persisted and persisted[0].reason == "no_percent_reading"


@pytest.mark.asyncio
async def test_todays_move_still_fires():
    stats, dispatcher, persisted = await _run([_ter(-6.1, CURRENT.isoformat())], [_rule()])
    assert stats["fired"] == 1 and len(dispatcher.sent) == 1
    assert persisted[0].reason == "percent_move:-6.10"


@pytest.mark.asyncio
async def test_an_unstamped_row_keeps_firing():
    """Crypto rows (rolling 24h change, no session) and older shapes: fail open."""
    row = _ter(-6.1, None)
    row.pop("changeSession")
    stats, dispatcher, _ = await _run([row], [_rule()])
    assert stats["fired"] == 1 and len(dispatcher.sent) == 1


@pytest.mark.asyncio
async def test_price_level_rules_are_untouched_by_the_session_stamp():
    """`price_above` / `price_below` read the PRICE, which is live either way. A crossing
    seen pre-market against a prior-session stamp still fires."""
    row = _ter(-13.3, PRIOR.isoformat())
    rule = _rule(kind="price_below", threshold=400.0, last_price=428.0, repeat_mode="once")
    stats, dispatcher, persisted = await _run([row], [rule])
    assert stats["fired"] == 1 and len(dispatcher.sent) == 1


def test_the_engine_holds_on_none_rather_than_treating_it_as_zero():
    """The seam: `session_change_percent` hands the engine None for a prior session, and
    the engine must hold (no baseline clobber, no fire), never read None as 0.0."""
    d = evaluate_alert(kind="percent_move", threshold=5.0, repeat_mode="daily", armed=True,
                       last_price=None, price=371.47, change_percent=None, rearm_pct=0.01)
    assert d.fire is False and d.reason == "no_percent_reading"


def test_the_call_site_reads_the_change_through_the_session_helper():
    """Source pin: the engine cannot know which session a number belongs to, so the
    translation has to happen at the ONE call site that has the row. A revert to the raw
    field passes every engine test and re-opens the 4 AM re-fire."""
    import inspect

    src = inspect.getsource(PriceAlertService.evaluate_once)
    assert "change_percent=session_change_percent(quote)" in src
    assert 'change_percent=(quote or {}).get("changePercentage")' not in src
