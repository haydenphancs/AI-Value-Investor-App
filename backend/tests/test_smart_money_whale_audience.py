"""Whale / congress pushes reach EVERY watcher of the filing, each told about THEIR tickers.

C15 (2026-09-25, owner decision "Fix audience + copy"). `_run_whale_phase` rolled a
filing up per (whale, direction) and then:

  * built the audience from `watchers_of` of `group["tickers"][:5]` — the first five in
    READ order, which is arbitrary within one bulk upsert (shared `created_at`, random
    uuid `id`) — so a user watching ticker #8 of a 12-ticker PTR, who did not follow the
    member, was never notified;
  * sent every reader the same title (the first three tickers, possibly none of theirs),
    the same route (`tickers[0]`) and the body "Disclosed activity totalling $X on your
    watchlist", where $X summed the WHOLE filing — including tickers the reader does not
    watch, and told to a follower who watches none of them at all.

Now: one paged read of the watchers of ALL the tickers (`watchers_of_any`), readers split
into copy variants by the subset they watch (title, amount, route and dedup key from that
subset), and follower-only readers get neutral copy without "on your watchlist".

No network: `fetch_all_rows` is patched for the whale read, and the dispatcher is a fake
that records every `notify_users` call. The fake also answers the OLD per-ticker
`watchers_of`, so a revert fails on an assertion rather than on a missing attribute.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import push_dispatch_service as pds
from app.services._whale_common import format_amount_range, parse_congress_amount_bounds
from app.services.notification_senders import smart_money_sender as sm
from app.services.push_dispatch_service import PushDispatchService
from app.utils.postgrest_paging import PAGE_SIZE

NOW = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)          # 18:00 ET run
_MEMBER = {"name": "Member A", "firm_name": None, "data_source": "congressional_house"}
_FUND = {"name": "Fund", "firm_name": "Fund B", "data_source": "13f"}


def _ptr(ticker, rng="$1,001 - $15,000", *, whale="w1", action="bought"):
    return {
        "id": str(uuid.uuid4()), "ticker": ticker, "company_name": ticker, "action": action,
        "amount": 0, "amount_range": rng, "date": "2026-07-28", "disclosure_date": "2026-08-10",
        "created_at": "2026-08-12T06:00:00+00:00", "whale_id": whale, "whales": dict(_MEMBER),
    }


def _13f(ticker, amount, *, whale="f1", when="2026-06-30"):
    return {
        "id": str(uuid.uuid4()), "ticker": ticker, "company_name": ticker, "action": "bought",
        "amount": amount, "amount_range": None, "date": when, "disclosure_date": None,
        "created_at": "2026-08-15T02:00:00+00:00", "whale_id": whale, "whales": dict(_FUND),
    }


class _Dispatcher:
    def __init__(self, watch=None, follows=None, cap_keep=None):
        self.watch = {u: list(ts) for u, ts in (watch or {}).items()}
        self.follows = follows or {}
        self.cap_keep = cap_keep
        self.calls, self.capped = [], []

    # the OLD selector, so a revert runs and fails on behaviour
    def watchers_of(self, ticker):
        return sorted(u for u, ts in self.watch.items() if ticker in ts)

    def watchers_of_any(self, tickers):
        wanted = [str(t).upper() for t in tickers]
        return {u: [t for t in wanted if t in ts] for u, ts in self.watch.items()
                if any(t in ts for t in wanted)}

    def followers_of_whale(self, whale_id):
        return list(self.follows.get(whale_id, []))

    def _cap_after_preferences(self, users, kind, dedup_key, now):
        self.capped.append((list(users), kind.key, dedup_key))
        return sorted(users)[: self.cap_keep]

    async def notify_users(self, users, *, kind, title, body, dedup_key, route, **kw):
        self.calls.append({"users": list(users), "kind": kind, "title": title, "body": body,
                           "key": dedup_key, "route": dict(route)})
        return len(users)

    def seen_by(self, uid):
        got = [c for c in self.calls if uid in c["users"]]
        assert len(got) <= 1, f"{uid} got {len(got)} notifications for one filing"
        return got[0] if got else None


async def _run(monkeypatch, rows, dispatcher, now=NOW):
    monkeypatch.setattr(sm, "fetch_all_rows", lambda *a, **k: list(rows))
    monkeypatch.setattr(sm, "get_supabase", lambda: SimpleNamespace(table=lambda name: None))
    monkeypatch.setattr(sm, "get_push_dispatch_service", lambda: dispatcher)
    return await sm._run_whale_phase(now=now, cursor=datetime(2026, 8, 11, tzinfo=timezone.utc))


TWELVE = [f"T{i:02d}" for i in range(1, 13)]


# ── audience ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_watcher_of_the_eighth_ticker_is_reached_and_told_about_it(monkeypatch):
    rows = [_ptr(t) for t in TWELVE]                                    # T08 is 8th read
    d = _Dispatcher(watch={"alice": ["T08"]})
    await _run(monkeypatch, rows, d)
    got = d.seen_by("alice")
    assert got is not None, "a watcher of ticker #8 was never notified"
    assert "T08" in got["title"] and "T01" not in got["title"]
    assert got["route"]["ticker"] == "T08" and got["route"]["whale_id"] == "w1"
    assert got["key"] == sm.whale_dedup_key("w1", "bought", "2026-07-28", ["T08"])
    assert got["body"].endswith("on your watchlist.")


@pytest.mark.asyncio
async def test_a_follower_who_watches_none_gets_neutral_copy_over_the_whole_filing(monkeypatch):
    rows = [_ptr(t) for t in TWELVE]
    d = _Dispatcher(follows={"w1": ["bob"]})
    await _run(monkeypatch, rows, d)
    got = d.seen_by("bob")
    assert got is not None
    assert "on your watchlist" not in got["body"].lower()
    assert got["body"].startswith("New disclosed activity totalling ")
    assert "+9 more" in got["title"]
    total = format_amount_range(12 * 1001.0, 12 * 15000.0)
    assert total in got["body"]
    assert got["key"] == sm.whale_dedup_key("w1", "bought", "2026-07-28", TWELVE)
    for directive in ("follow", "copy", "consider", "you should"):
        assert directive not in got["body"].lower()


@pytest.mark.asyncio
async def test_a_watcher_of_one_of_three_is_told_that_tickers_amount_not_the_sum(monkeypatch):
    ranges = {"AAA": "$1,001 - $15,000", "BBB": "$15,001 - $50,000", "CCC": "$50,001 - $100,000"}
    rows = [_ptr(t, r) for t, r in ranges.items()]
    d = _Dispatcher(watch={"carol": ["BBB"]})
    await _run(monkeypatch, rows, d)
    got = d.seen_by("carol")
    mine = format_amount_range(*parse_congress_amount_bounds(ranges["BBB"]))
    whole = format_amount_range(1001.0 + 15001.0 + 50001.0, 15000.0 + 50000.0 + 100000.0)
    assert f"totalling {mine} on your watchlist" in got["body"]
    assert whole not in got["body"]
    assert got["title"] == "Member A bought BBB"


@pytest.mark.asyncio
async def test_a_reader_who_watches_and_follows_gets_exactly_one_watcher_alert(monkeypatch):
    rows = [_ptr(t) for t in ("AAA", "BBB", "CCC")]
    d = _Dispatcher(watch={"dan": ["CCC", "AAA"], "erin": ["AAA"]},
                    follows={"w1": ["dan", "frank"]})
    await _run(monkeypatch, rows, d)
    dan, erin, frank = d.seen_by("dan"), d.seen_by("erin"), d.seen_by("frank")
    assert dan["body"].endswith("on your watchlist.") and "BBB" not in dan["title"]
    assert dan["key"] == sm.whale_dedup_key("w1", "bought", "2026-07-28", ["AAA", "CCC"])
    assert erin["key"] != dan["key"]
    assert "on your watchlist" not in frank["body"]
    assert sorted(u for c in d.calls for u in c["users"]) == ["dan", "erin", "frank"]


@pytest.mark.asyncio
async def test_an_open_ended_range_only_reaches_readers_of_that_ticker(monkeypatch):
    rows = [_ptr("BIG", "Over $50,000,000"), _ptr("SMALL", "$1,001 - $15,000")]
    d = _Dispatcher(watch={"gil": ["SMALL"], "hal": ["BIG"]})
    await _run(monkeypatch, rows, d)
    assert "+" not in d.seen_by("gil")["body"].split("totalling ")[1]
    assert d.seen_by("hal")["body"].split("totalling ")[1].split(" ")[0].endswith("+")


@pytest.mark.asyncio
async def test_a_retry_of_the_same_data_derives_the_same_keys(monkeypatch):
    rows = [_ptr(t) for t in TWELVE]
    watch = {"a": ["T08"], "b": ["T02", "T11"], "c": ["T05"]}
    first, second = _Dispatcher(watch=watch, follows={"w1": ["z"]}), \
        _Dispatcher(watch=watch, follows={"w1": ["z"]})
    await _run(monkeypatch, rows, first)
    await _run(monkeypatch, list(reversed(rows)), second)       # different read order
    keys = lambda d: {u: c["key"] for c in d.calls for u in c["users"]}
    copy = lambda d: {u: (c["title"], c["body"], c["route"]["ticker"]) for c in d.calls for u in c["users"]}
    assert keys(first) == keys(second)
    assert copy(first) == copy(second), "the copy depended on the read order"


@pytest.mark.asyncio
async def test_a_deadline_day_13f_filing_reaches_its_watcher(monkeypatch):
    """C13 end to end: Q2 (06-30) filed on its 08-14 deadline, read 08-15 at 18:00 ET."""
    rows = [_13f("NVDA", 4_000_000.0), _13f("AAPL", 9_000_000.0)]
    d = _Dispatcher(watch={"ivy": ["NVDA"]})
    sent, _ = await _run(monkeypatch, rows, d, now=datetime(2026, 8, 15, 22, 0, tzinfo=timezone.utc))
    got = d.seen_by("ivy")
    assert sent == 1 and got is not None and got["kind"] == "whale_13f"
    assert got["title"] == "Fund B bought NVDA" and "$4" in got["body"]


# ── the per-event scope cap ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_scope_cap_applies_to_the_whole_event_not_per_variant(monkeypatch):
    monkeypatch.setattr(sm, "MAX_RECIPIENTS_PER_SCOPE", 3)
    rows = [_ptr(t) for t in ("AAA", "BBB", "CCC")]
    d = _Dispatcher(watch={"u1": ["AAA"], "u2": ["BBB"], "u3": ["CCC"], "u4": ["AAA", "BBB"]},
                    follows={"w1": ["u5"]}, cap_keep=3)
    await _run(monkeypatch, rows, d)
    assert len(d.capped) == 1, "the event audience must be capped once, before the split"
    audience, kind, _key = d.capped[0]
    assert sorted(audience) == ["u1", "u2", "u3", "u4", "u5"] and kind == "congress_trade"
    notified = sorted(u for c in d.calls for u in c["users"])
    assert notified == ["u1", "u2", "u3"]


@pytest.mark.asyncio
async def test_an_audience_under_the_cap_is_not_pre_filtered(monkeypatch):
    rows = [_ptr(t) for t in ("AAA", "BBB")]
    d = _Dispatcher(watch={"u1": ["AAA"]}, follows={"w1": ["u2"]})
    await _run(monkeypatch, rows, d)
    assert d.capped == []
    assert sorted(u for c in d.calls for u in c["users"]) == ["u1", "u2"]


# ── the pure splitter ─────────────────────────────────────────────────────────


def test_variants_partition_the_audience_and_ignore_foreign_tickers():
    ranked = ["AAA", "BBB", "CCC"]
    watched = {"x": ["ZZZ"], "y": ["bbb", "AAA"], "z": ["AAA", "BBB"], "": ["AAA"]}
    variants = sm._whale_audience_variants(ranked, watched, ["x", "y", "q", None, "q"])
    assert variants == [(("AAA", "BBB"), ["y", "z"]), ((), ["q", "x"])]


def test_no_readers_means_no_variants():
    assert sm._whale_audience_variants(["AAA"], {}, []) == []
    assert sm._whale_audience_variants(["AAA"], {"u": ["ZZZ"]}, []) == []


def test_whale_copy_follower_form_keeps_the_informational_shape():
    title, body = sm.whale_copy("Fund B", "sold", ["AAA", "BBB", "CCC", "DDD"], "$2M",
                                on_watchlist=False)
    assert title == "Fund B sold AAA, BBB, CCC +1 more"
    assert body == "New disclosed activity totalling $2M."
    # The default form is unchanged for existing callers.
    assert sm.whale_copy("Fund B", "sold", ["AAA"], "$2M")[1] == \
        "Disclosed activity totalling $2M on your watchlist."


# ── the dispatcher's read helper ──────────────────────────────────────────────


class _WatchStore:
    """`watchlist_items` behind PostgREST: clamps pages, sorts on the ORDER key asked for."""

    def __init__(self, rows, fail_on=None):
        self.rows, self.fail_on, self.reads = rows, fail_on, []

    def table(self, name):
        assert name == "watchlist_items"
        return _WatchQ(self)


class _WatchQ:
    def __init__(self, store):
        self.store, self.tickers, self.order_key, self.rng = store, None, None, None

    def select(self, cols):
        assert "ticker" in cols and "user_id" in cols
        return self

    def in_(self, col, values):
        assert col == "ticker"
        self.tickers = list(values)
        return self

    def order(self, col, desc=False):
        self.order_key = col
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        self.store.reads.append((len(self.tickers), self.order_key))
        if self.store.fail_on and self.store.fail_on in self.tickers:
            raise RuntimeError("chunk read failed")
        rows = sorted((r for r in self.store.rows if r["ticker"] in self.tickers),
                      key=lambda r: r[self.order_key])
        a, b = self.rng
        return SimpleNamespace(data=rows[a:min(b, a + PAGE_SIZE - 1) + 1])


def _svc(store):
    svc = object.__new__(PushDispatchService)
    svc.supabase = store
    svc._push = None
    return svc


def test_watchers_of_any_pages_past_the_clamp_and_keeps_each_users_subset():
    tickers = [f"S{i:03d}" for i in range(450)]                     # three URL-safe chunks
    rows = [{"id": f"{i:06d}", "user_id": f"u{i % 1300}", "ticker": tickers[i % 450]}
            for i in range(2600)]
    rows.append({"id": "999998", "user_id": "u1", "ticker": tickers[1]})   # a duplicate
    store = _WatchStore(rows)
    got = _svc(store).watchers_of_any([t.lower() for t in tickers] + ["", None])
    expected = {}
    for r in rows:
        expected.setdefault(r["user_id"], set()).add(r["ticker"])
    assert {u: set(ts) for u, ts in got.items()} == expected
    order = {t: i for i, t in enumerate(tickers)}
    assert all(ts == sorted(ts, key=order.__getitem__) for ts in got.values())
    assert {n for n, _ in store.reads} <= {200, 50} and all(k == "id" for _, k in store.reads)
    assert len(store.reads) > 3, "a chunk bigger than one page must be paged"


def test_one_failed_chunk_keeps_the_others(caplog):
    tickers = [f"S{i:03d}" for i in range(250)]
    rows = [{"id": f"{i:04d}", "user_id": f"u{i}", "ticker": t} for i, t in enumerate(tickers)]
    store = _WatchStore(rows, fail_on="S220")
    with caplog.at_level("WARNING"):
        got = _svc(store).watchers_of_any(tickers)
    assert set(got) == {f"u{i}" for i in range(200)}
    assert any("watcher lookup failed" in r.getMessage() for r in caplog.records)


def test_no_tickers_means_no_read():
    store = _WatchStore([])
    assert _svc(store).watchers_of_any([]) == {} and store.reads == []
    assert _svc(store).watchers_of_any(["", None, "  "]) == {} and store.reads == []


def test_the_new_selector_pages_the_whole_audience_and_never_caps():
    """Same pin `test_push_audience_cap_after_preferences` holds on the other selectors:
    the cap belongs after the preference filter, never in a selector."""
    src = "\n".join(l for l in inspect.getsource(PushDispatchService.watchers_of_any).splitlines()
                    if not l.strip().startswith("#"))
    assert "fetch_all_rows(" in src
    assert "MAX_RECIPIENTS_PER_SCOPE" not in src and ".limit(" not in src
    assert pds.MAX_AUDIENCE_SCAN_PAGES >= 10
