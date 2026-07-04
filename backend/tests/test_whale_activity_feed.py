"""
Whale activity feed — NULL-column and missing-whale degradation tests.

The production bug this pins: a NULL DB column arrives as an EXISTING key with
value None, so ``dict.get(k, "")`` returns None (the default is dead code) and
an explicit None fails pydantic ``str`` fields — one followed avatar-less whale
500'd the ENTIRE /whales/activity feed. Most whales have NULL avatar_url
(sync/hydration never write it), so this was guaranteed for the 26 new
registry whales.

No network / Supabase: a chainable fake shim feeds the pure mapping logic.
Run via `python -m pytest` (no conftest — cwd must be backend/).
"""

import pytest

from app.services import whale_service as wsvc
from app.services.whale_service import WhaleService, _whale_activity_cache


class _FakeQuery:
    """Ignores filters (tests supply already-filtered canned rows)."""

    def __init__(self, data):
        self._data = data

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        class _R:
            pass
        r = _R()
        r.data = self._data
        return r


class _FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


def _feed(monkeypatch, tables, user_id="u1"):
    _whale_activity_cache.clear()
    monkeypatch.setattr(wsvc, "get_supabase", lambda: _FakeSupabase(tables))
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        WhaleService().get_whale_activity_feed(user_id)
    )


_TRADE_GROUP = {
    "id": "tg1", "whale_id": "w1", "date": "2026-07-01",
    "net_action": "BOUGHT", "trade_count": 3, "net_amount": 1_200_000.0,
    "summary": "Added 3 positions",
}


def test_null_avatar_url_does_not_500_the_feed(monkeypatch):
    # avatar_url NULL → key EXISTS with None. Must degrade to "", not raise
    # ValidationError (which killed the whole feed for the user).
    tables = {
        "whale_follows": [{"whale_id": "w1"}],
        "whale_trade_groups": [_TRADE_GROUP],
        "whales": [{
            "id": "w1", "name": "Stanley Druckenmiller", "avatar_url": None,
            "category": "investors", "firm_name": "Duquesne Family Office",
        }],
    }
    feed = _feed(monkeypatch, tables)
    assert len(feed) == 1
    assert feed[0].entity_name == "Stanley Druckenmiller"
    assert feed[0].entity_avatar_name == ""
    assert feed[0].entity_firm_name == "Duquesne Family Office"


def test_missing_whale_row_degrades_to_unknown(monkeypatch):
    # Trade group whose whale row no longer exists (e.g. deleted) — the join
    # miss must degrade per-row, never break the feed.
    tables = {
        "whale_follows": [{"whale_id": "w-gone"}],
        "whale_trade_groups": [{**_TRADE_GROUP, "whale_id": "w-gone"}],
        "whales": [],
    }
    feed = _feed(monkeypatch, tables)
    assert len(feed) == 1
    assert feed[0].entity_name == "Unknown"
    assert feed[0].entity_avatar_name == ""
    assert feed[0].entity_firm_name is None
    assert feed[0].category is None


def test_whitespace_firm_name_normalized_to_none(monkeypatch):
    # A blank/whitespace firm (bad row edit) must NOT reach iOS as a
    # non-empty string — the `!firm.isEmpty` guard would render a blank line.
    tables = {
        "whale_follows": [{"whale_id": "w1"}],
        "whale_trade_groups": [_TRADE_GROUP],
        "whales": [{
            "id": "w1", "name": "Renaissance Technologies",
            "avatar_url": "https://cdn/x.png", "category": "institutions",
            "firm_name": "   ",
        }],
    }
    feed = _feed(monkeypatch, tables)
    assert feed[0].entity_firm_name is None
    assert feed[0].entity_avatar_name == "https://cdn/x.png"


def test_null_firm_name_and_unicode_firm_pass_through(monkeypatch):
    tables = {
        "whale_follows": [{"whale_id": "w1"}, {"whale_id": "w2"}],
        "whale_trade_groups": [
            _TRADE_GROUP,
            {**_TRADE_GROUP, "id": "tg2", "whale_id": "w2"},
        ],
        "whales": [
            {"id": "w1", "name": "Norges Bank", "avatar_url": None,
             "category": "institutions", "firm_name": None},
            {"id": "w2", "name": "Duan Yongping", "avatar_url": None,
             "category": "investors",
             "firm_name": "H&H International Investment"},
        ],
    }
    feed = _feed(monkeypatch, tables)
    by_name = {a.entity_name: a for a in feed}
    assert by_name["Norges Bank"].entity_firm_name is None
    # Ampersand firm names are live registry data — must survive untouched.
    assert by_name["Duan Yongping"].entity_firm_name == "H&H International Investment"


def test_no_follows_returns_empty(monkeypatch):
    tables = {"whale_follows": [], "whale_trade_groups": [], "whales": []}
    assert _feed(monkeypatch, tables) == []
