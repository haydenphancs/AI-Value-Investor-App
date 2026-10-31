"""`DELETE /watchlist` with `only_if_unreferenced` — the server-side guard behind
"remove fully if in no group" (plan: Manage Tickers + Sort By pass, 2026-10-31).

The iOS "remove from this portfolio" paths now also take a ticker off the watchlist when
no other group holds it. The client decides that from its own store, which can be stale:
another device may have just put the ticker in a group. The watchlist DELETE always runs a
delete-through that strips the ticker from EVERY group, shares included — so trusting the
client would delete another device's holding. With the flag, the server re-checks
`portfolio_items` across all the caller's groups for the exact stored ticker the delete
would remove, and keeps the row (200, `removed: false`) when any group still holds it.

Pinned:
  * referenced → no watchlist delete, no delete-through, no cache drop, `removed: false`;
  * unreferenced → today's delete + delete-through, `removed: true`;
  * flag absent → today's behaviour byte for byte, plus `removed`;
  * the check follows the handler's raw/canonical order and a declared "crypto" pair;
  * a failed reference read fails CLOSED: nothing deleted, a 503 in the error contract.

No network / Supabase — an in-memory fake that understands the filters the handler uses.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import pytest

import app.api.v1.endpoints.watchlist as wl
import app.utils.supabase_errors as supabase_errors
from app.schemas.watchlist import RemoveFromWatchlistRequest

_USER = "u-remove"
_OTHER = "u-someone-else"


class _Q:
    def __init__(self, sb: "_SB", table: str):
        self.sb, self.table = sb, table
        self._op = "select"
        self._eq: Dict[str, Any] = {}
        self._in: Dict[str, List[Any]] = {}
        self._limit = None

    def select(self, *_a, **_k): self._op = "select"; return self
    def delete(self): self._op = "delete"; return self
    def eq(self, c, v): self._eq[c] = v; return self
    def in_(self, c, vs): self._in[c] = list(vs); return self
    def limit(self, n): self._limit = n; return self
    def order(self, *_a, **_k): return self
    def range(self, *_a, **_k): return self

    def execute(self):
        self.sb.calls.append((self._op, self.table, dict(self._eq), dict(self._in)))
        fail = self.sb.fail.get((self._op, self.table))
        if fail is not None:
            if fail["times"] > 0:
                fail["times"] -= 1
                raise fail["exc"]
        rows = self.sb.store.setdefault(self.table, [])
        matched = [
            r for r in rows
            if all(r.get(k) == v for k, v in self._eq.items())
            and all(r.get(k) in vs for k, vs in self._in.items())
        ]
        if self._op == "delete":
            for r in matched:
                rows.remove(r)
        elif self._limit is not None:
            matched = matched[: self._limit]
        return type("R", (), {"data": [dict(r) for r in matched]})()


class _SB:
    def __init__(self, store: Dict[str, List[Dict[str, Any]]]):
        self.store = store
        self.calls: List[tuple] = []
        self.fail: Dict[tuple, Dict[str, Any]] = {}

    def table(self, name):
        return _Q(self, name)

    def fail_on(self, op, table, exc, times=10**6):
        self.fail[(op, table)] = {"exc": exc, "times": times}

    def deletes(self, table):
        return [c for c in self.calls if c[0] == "delete" and c[1] == table]


def _wl(ticker, user=_USER):
    return {"user_id": user, "ticker": ticker, "company_name": ticker}


def _group(gid, user=_USER):
    return {"id": gid, "user_id": user, "name": gid}


def _item(gid, ticker):
    return {"portfolio_id": gid, "ticker": ticker}


@pytest.fixture
def spies(monkeypatch):
    """Record the cache drop and the delete-through — the latter still RUNS (wrapped), so a
    test can see what it actually removed from `portfolio_items`."""
    seen = {"invalidate": [], "delete_through": []}
    real = wl._delete_through_from_groups

    def _through(sb, user_id, ticker):
        seen["delete_through"].append(ticker)
        return real(sb, user_id, ticker)

    monkeypatch.setattr(wl, "invalidate_feed_cache", lambda uid: seen["invalidate"].append(uid))
    monkeypatch.setattr(wl, "_delete_through_from_groups", _through)
    return seen


async def _remove(sb, stock_id, *, asset_type=None, flag=None):
    kwargs: Dict[str, Any] = {"stock_id": stock_id, "asset_type": asset_type}
    if flag is not None:
        kwargs["only_if_unreferenced"] = flag
    return await wl.remove_from_watchlist(RemoveFromWatchlistRequest(**kwargs), {"id": _USER}, sb)


def _tickers(sb, table="watchlist_items", user=_USER):
    return sorted(r["ticker"] for r in sb.store.get(table, []) if r.get("user_id", user) == user)


# ── schema ───────────────────────────────────────────────────────────────────

def test_the_flag_defaults_off_so_every_shipped_build_keeps_the_plain_remove():
    assert RemoveFromWatchlistRequest(stock_id="AAPL").only_if_unreferenced is False
    assert RemoveFromWatchlistRequest(
        stock_id="AAPL", only_if_unreferenced=True).only_if_unreferenced is True


# ── referenced → kept ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_ticker_another_group_holds_is_kept_with_no_delete_and_no_delete_through(
    spies, caplog,
):
    sb = _SB({
        "watchlist_items": [_wl("AAPL"), _wl("MSFT")],
        "portfolios": [_group("g-active"), _group("g-other")],
        # AAPL already left the active group on this device; another device keeps it in
        # g-other with a hand-entered holding.
        "portfolio_items": [_item("g-other", "AAPL") | {"shares": 12}],
    })
    with caplog.at_level(logging.INFO, logger=wl.logger.name):
        body = await _remove(sb, "aapl", flag=True)

    assert body == {"message": "AAPL not removed: a portfolio still holds it",
                    "removed": False}
    assert _tickers(sb) == ["AAPL", "MSFT"], "the watchlist row was deleted"
    assert sb.store["portfolio_items"] == [{"portfolio_id": "g-other", "ticker": "AAPL",
                                            "shares": 12}], "another group lost its holding"
    assert sb.deletes("watchlist_items") == [] and sb.deletes("portfolio_items") == []
    assert spies["delete_through"] == [] and spies["invalidate"] == []
    kept = [r.getMessage() for r in caplog.records if "kept" in r.getMessage()]
    assert kept and _USER in kept[0] and "AAPL" in kept[0]


@pytest.mark.asyncio
async def test_another_users_group_never_counts_as_a_reference(spies):
    sb = _SB({
        "watchlist_items": [_wl("AAPL")],
        "portfolios": [_group("g-mine"), _group("g-theirs", user=_OTHER)],
        "portfolio_items": [_item("g-theirs", "AAPL")],
    })
    body = await _remove(sb, "AAPL", flag=True)
    assert body["removed"] is True
    assert _tickers(sb) == []
    assert sb.store["portfolio_items"] == [_item("g-theirs", "AAPL")], (
        "the delete-through reached another user's group"
    )


# ── unreferenced → removed ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreferenced_ticker_is_removed_and_says_so(spies):
    sb = _SB({
        "watchlist_items": [_wl("AAPL"), _wl("MSFT")],
        "portfolios": [_group("g1")],
        "portfolio_items": [_item("g1", "MSFT")],
    })
    body = await _remove(sb, "AAPL", flag=True)
    assert body == {"message": "AAPL removed from watchlist", "removed": True}
    assert _tickers(sb) == ["MSFT"]
    assert spies["delete_through"] == ["AAPL"] and spies["invalidate"] == [_USER]
    assert sb.store["portfolio_items"] == [_item("g1", "MSFT")]


@pytest.mark.asyncio
async def test_a_user_with_no_groups_is_unreferenced(spies):
    sb = _SB({"watchlist_items": [_wl("AAPL")], "portfolios": [], "portfolio_items": []})
    body = await _remove(sb, "AAPL", flag=True)
    assert body["removed"] is True and _tickers(sb) == []


@pytest.mark.asyncio
async def test_with_no_watchlist_row_the_flag_still_guards_the_delete_through(spies):
    """No row for either spelling: today's handler still runs the delete-through on the
    first spelling (it cleans orphans). With the flag, a group holding that ticker keeps
    it — the whole point is that this request can never strip a group."""
    sb = _SB({
        "watchlist_items": [],
        "portfolios": [_group("g1")],
        "portfolio_items": [_item("g1", "AAPL")],
    })
    body = await _remove(sb, "AAPL", flag=True)
    assert body["removed"] is False
    assert sb.store["portfolio_items"] == [_item("g1", "AAPL")]
    assert spies["delete_through"] == []


@pytest.mark.asyncio
async def test_no_row_and_no_reference_keeps_todays_body_with_removed_false(spies):
    sb = _SB({"watchlist_items": [_wl("MSFT")], "portfolios": [_group("g1")],
              "portfolio_items": []})
    body = await _remove(sb, "AAPL", flag=True)
    assert body == {"message": "AAPL removed from watchlist", "removed": False}
    assert _tickers(sb) == ["MSFT"]
    assert spies["invalidate"] == [], "a no-op delete must not drop the feed cache"


# ── flag absent → today's behaviour ──────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, False])
async def test_without_the_flag_a_referenced_ticker_is_still_removed_everywhere(spies, flag):
    """The unconditional remove (detail-screen star, long-press "Remove from all
    portfolios") is unchanged: watchlist row AND every group row go."""
    sb = _SB({
        "watchlist_items": [_wl("AAPL"), _wl("MSFT")],
        "portfolios": [_group("g1"), _group("g2")],
        "portfolio_items": [_item("g1", "AAPL"), _item("g2", "AAPL"), _item("g2", "MSFT")],
    })
    body = await _remove(sb, "AAPL", flag=flag)
    assert body == {"message": "AAPL removed from watchlist", "removed": True}
    assert _tickers(sb) == ["MSFT"]
    assert sb.store["portfolio_items"] == [_item("g2", "MSFT")]
    # And no reference read happened: the flag-off path makes exactly today's calls — the
    # only SELECT is the delete-through's own group list.
    selects = [(c[1], c[2]) for c in sb.calls if c[0] == "select"]
    assert selects == [("portfolios", {"user_id": _USER})], selects


@pytest.mark.asyncio
async def test_without_the_flag_a_missing_row_keeps_todays_message(spies):
    sb = _SB({"watchlist_items": [], "portfolios": [], "portfolio_items": []})
    body = await _remove(sb, "AAPL")
    assert body == {"message": "AAPL removed from watchlist", "removed": False}


# ── the check follows the handler's spelling order ──────────────────────────

@pytest.mark.asyncio
async def test_a_declared_crypto_pair_is_checked_and_removed_as_the_pair(spies):
    """The crypto screen sends the bare symbol with "crypto": the PAIR row is the target.
    The same-ticker REIT (bare LTC) sits in a group — it must not block the coin's removal,
    and must survive it."""
    sb = _SB({
        "watchlist_items": [_wl("LTC"), _wl("LTCUSD")],
        "portfolios": [_group("g1")],
        "portfolio_items": [_item("g1", "LTC")],
    })
    body = await _remove(sb, "LTC", asset_type="crypto", flag=True)
    assert body == {"message": "LTCUSD removed from watchlist", "removed": True}
    assert _tickers(sb) == ["LTC"]
    assert sb.store["portfolio_items"] == [_item("g1", "LTC")]


@pytest.mark.asyncio
async def test_a_declared_crypto_pair_held_by_a_group_is_kept(spies):
    sb = _SB({
        "watchlist_items": [_wl("LTC"), _wl("LTCUSD")],
        "portfolios": [_group("g1")],
        "portfolio_items": [_item("g1", "LTCUSD")],
    })
    body = await _remove(sb, "LTC", asset_type="crypto", flag=True)
    assert body == {"message": "LTCUSD not removed: a portfolio still holds it",
                    "removed": False}
    assert _tickers(sb) == ["LTC", "LTCUSD"]


@pytest.mark.asyncio
async def test_an_undeclared_bare_symbol_checks_the_raw_row_first(spies):
    """Undeclared: raw first. The bare LTC (the REIT) is the target, so a group holding the
    PAIR is irrelevant — and a group holding the REIT keeps it."""
    sb = _SB({
        "watchlist_items": [_wl("LTC"), _wl("LTCUSD")],
        "portfolios": [_group("g1")],
        "portfolio_items": [_item("g1", "LTCUSD")],
    })
    body = await _remove(sb, "LTC", flag=True)
    assert body == {"message": "LTC removed from watchlist", "removed": True}
    assert _tickers(sb) == ["LTCUSD"]
    assert sb.store["portfolio_items"] == [_item("g1", "LTCUSD")]


@pytest.mark.asyncio
async def test_with_the_raw_row_absent_the_canonical_row_is_the_one_checked(spies):
    """The handler falls through to the canonical spelling when the raw row is missing — so
    must the check, or a group holding the pair would be stripped."""
    sb = _SB({
        "watchlist_items": [_wl("BTCUSD")],
        "portfolios": [_group("g1")],
        "portfolio_items": [_item("g1", "BTCUSD")],
    })
    body = await _remove(sb, "BTC", flag=True)
    assert body == {"message": "BTCUSD not removed: a portfolio still holds it",
                    "removed": False}
    assert _tickers(sb) == ["BTCUSD"] and spies["delete_through"] == []


# ── a failed reference read fails CLOSED ─────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("op_table", [
    ("select", "watchlist_items"),
    ("select", "portfolios"),
    ("select", "portfolio_items"),
])
async def test_a_failed_reference_read_deletes_nothing_and_answers_503(spies, caplog, op_table):
    sb = _SB({
        "watchlist_items": [_wl("AAPL")],
        "portfolios": [_group("g1")],
        "portfolio_items": [],
    })
    sb.fail_on(*op_table, RuntimeError("supabase exploded"))
    with caplog.at_level(logging.WARNING, logger=wl.logger.name):
        resp = await _remove(sb, "AAPL", flag=True)

    assert resp.status_code == 503
    body = json.loads(resp.body)
    assert body["error_code"] == "WATCHLIST_UNAVAILABLE"
    assert set(body) == {"error_code", "message", "user_message", "action", "details"}
    assert "AAPL" in body["user_message"] and body["action"] == "retry_later"
    assert _tickers(sb) == ["AAPL"]
    assert sb.deletes("watchlist_items") == [] and sb.deletes("portfolio_items") == []
    assert spies["delete_through"] == [] and spies["invalidate"] == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a non-transient failure must log at ERROR"
    assert errors[0].exc_info is not None
    msg = errors[0].getMessage()
    assert "AAPL" in msg and _USER in msg and "RuntimeError" in msg


@pytest.mark.asyncio
async def test_a_transient_blip_is_retried_then_the_remove_proceeds(spies, monkeypatch):
    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _s: None)
    sb = _SB({"watchlist_items": [_wl("AAPL")], "portfolios": [_group("g1")],
              "portfolio_items": []})
    sb.fail_on("select", "portfolios", RuntimeError("Server disconnected"), times=1)
    body = await _remove(sb, "AAPL", flag=True)
    assert body["removed"] is True and _tickers(sb) == []


@pytest.mark.asyncio
async def test_a_transient_failure_that_outlives_the_retries_still_fails_closed(
    spies, monkeypatch, caplog,
):
    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _s: None)
    sb = _SB({"watchlist_items": [_wl("AAPL")], "portfolios": [_group("g1")],
              "portfolio_items": []})
    sb.fail_on("select", "portfolio_items", RuntimeError("Server disconnected"))
    with caplog.at_level(logging.WARNING, logger=wl.logger.name):
        resp = await _remove(sb, "AAPL", flag=True)
    assert resp.status_code == 503 and _tickers(sb) == ["AAPL"]
    final = [r for r in caplog.records if "nothing deleted" in r.getMessage()]
    assert final and final[0].levelno == logging.WARNING, "a transient blip is WARNING"


@pytest.mark.asyncio
async def test_the_503_user_message_is_bounded_for_an_absurd_ticker(spies):
    """`stock_id` has no length bound and `user_message` is shown verbatim in a toast."""
    sb = _SB({"watchlist_items": [], "portfolios": [], "portfolio_items": []})
    sb.fail_on("select", "watchlist_items", RuntimeError("supabase exploded"))
    resp = await _remove(sb, "A" * 5_000, flag=True)
    assert resp.status_code == 503
    assert len(json.loads(resp.body)["user_message"]) < 120


# ── review round (2026-10-31): the delete never reaches a spelling the check did not clear ──

@pytest.mark.asyncio
async def test_a_cleared_bare_row_that_vanishes_never_falls_through_to_the_coin(monkeypatch, spies):
    """The check cleared bare LTC (no group). Another device removes that row before the
    delete runs. The plain raw→canonical fall-through would then delete LTCUSD — never
    checked — and its delete-through would strip the coin and its shares from group Q."""
    sb = _SB({
        "watchlist_items": [_wl("LTC"), _wl("LTCUSD")],
        "portfolios": [_group("Q")],
        "portfolio_items": [_item("Q", "LTCUSD")],
    })
    real = wl._delete_target_and_reference

    def _check_then_other_device_deletes(supabase, user_id, first, second):
        answer = real(supabase, user_id, first, second)
        sb.store["watchlist_items"] = [r for r in sb.store["watchlist_items"] if r["ticker"] != "LTC"]
        return answer

    monkeypatch.setattr(wl, "_delete_target_and_reference", _check_then_other_device_deletes)
    body = await _remove(sb, "LTC", flag=True)
    assert _tickers(sb) == ["LTCUSD"], "the coin the check never cleared was deleted"
    assert [r["ticker"] for r in sb.store["portfolio_items"]] == ["LTCUSD"], "group Q lost the coin"
    assert "LTCUSD" not in spies["delete_through"]
    assert all(c[2].get("ticker") != "LTCUSD" for c in sb.deletes("watchlist_items"))
    assert body["removed"] is False


class _PagedQ(_Q):
    """`range` really slices (ordered by id), so a single un-paged read would miss page 2."""

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        result = super().execute()
        window = getattr(self, "_range", None)
        if window is None or self._op != "select":
            return result
        rows = sorted(result.data, key=lambda r: str(r.get("id")))
        return type("R", (), {"data": rows[window[0]: window[1] + 1]})()


class _PagedSB(_SB):
    def table(self, name):
        return _PagedQ(self, name)


@pytest.mark.asyncio
async def test_the_group_list_is_paged_so_a_holder_on_page_two_still_keeps_the_row(spies):
    """A truncated group read answers "unreferenced" — the direction that deletes."""
    groups = [_group(f"g{i:05d}") for i in range(1205)]
    holder = groups[-1]["id"]                      # sorts last → only on the second page
    sb = _PagedSB({
        "watchlist_items": [_wl("AAPL")],
        "portfolios": groups,
        "portfolio_items": [_item(holder, "AAPL")],
    })
    body = await _remove(sb, "AAPL", flag=True)
    assert body["removed"] is False
    assert _tickers(sb) == ["AAPL"]
    assert spies["delete_through"] == []
