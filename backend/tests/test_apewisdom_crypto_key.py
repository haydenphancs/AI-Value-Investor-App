"""ApeWisdom keys every coin as `<BASE>.X`; the app looked coins up by their bare base.

Measured against the public API on 2026-09-21: `all-crypto` rows are `BTC.X` (198 mentions),
`ETH.X` (70), `BNB.X`, `SOL.X` — all 155 of them; `all-stocks` rows are bare (`META`, `AMD`).
The cache and the daily `social_mentions_history` snapshot store the key verbatim, so
`get_mentions_24h("ETH")` missed a POPULATED cache — which is, by design, a real zero ("Reddit
is not talking about it") — and every coin's Sentiment card said "Reddit data unavailable"
with `known=True`. Right semantics, wrong key. These pin the key on every path.

Hermetic: the ApeWisdom module state is monkeypatched, Supabase is a recording fake, and the
sentiment service's other arms are stubbed.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest

import app.integrations.apewisdom as ape
import app.services.sentiment_service as ss
import app.services.social_mentions_service as sms
from app.integrations.apewisdom import CRYPTO_TICKER_SUFFIX, _parse_page, crypto_ticker
from app.services.social_mentions_service import apewisdom_key


# ── the pure key helpers ─────────────────────────────────────────────────────


def test_the_suffix_is_dot_x():
    assert CRYPTO_TICKER_SUFFIX == ".X"


@pytest.mark.parametrize(
    "base,expected",
    [
        ("ETH", "ETH.X"), ("eth", "ETH.X"), (" eth ", "ETH.X"), ("ETH.X", "ETH.X"),
        ("btc", "BTC.X"), ("", ""), (None, ""),
    ],
)
def test_crypto_ticker_appends_the_suffix_once(base, expected):
    assert crypto_ticker(base) == expected


@pytest.mark.parametrize(
    "ticker,is_crypto,expected",
    [
        ("ETH", True, "ETH.X"),
        ("BTC", True, "BTC.X"),
        ("LINK", True, "LINK.X"),
        ("ETH.X", True, "ETH.X"),         # already ApeWisdom's key
        # Stablecoin BASES end in USD. The callers already stripped the pair's quote once;
        # a second strip here made these `T.X` / `PY.X` — the same confident zero.
        ("TUSD", True, "TUSD.X"),
        ("PYUSD", True, "PYUSD.X"),
        ("FDUSD", True, "FDUSD.X"),
        ("BUSD", True, "BUSD.X"),
        ("USDT", True, "USDT.X"),
        ("AAPL", False, "AAPL"),
        ("aapl ", False, "AAPL"),
        ("COIN", False, "COIN"),          # a stock that shares a coin's name stays a stock
        ("LINK", False, "LINK"),
        ("ETHUSD", False, "ETHUSD"),      # stocks are never stripped
        ("", True, ""),
    ],
)
def test_apewisdom_key_by_asset_class(ticker, is_crypto, expected):
    assert apewisdom_key(ticker, is_crypto=is_crypto) == expected


def test_parse_page_keeps_the_served_key_verbatim():
    """The storage key is ApeWisdom's. Stripping `.X` at parse time would look like a fix
    and would zero the 7-day sums for a week (the table already holds `.X` rows)."""
    into = {}
    _parse_page({"results": [
        {"ticker": "ETH.X", "name": "Ethereum", "mentions": "70", "mentions_24h_ago": 37,
         "upvotes": 5, "rank": 2},
        {"ticker": "btc.x", "mentions": 198},
        {"ticker": "", "mentions": 9},
        {"mentions": 9},
    ]}, into, "all-crypto")
    assert set(into) == {"ETH.X", "BTC.X"}
    assert into["ETH.X"]["mentions"] == 70 and into["ETH.X"]["mentions_24h_ago"] == 37
    assert into["ETH.X"]["_filter"] == "all-crypto"


# ── the service reads with the right key ─────────────────────────────────────


class _Query:
    """Records every filter call so a test can assert the exact `eq("ticker", …)`."""

    def __init__(self, plan, calls):
        self.plan, self.calls = plan, calls

    def __getattr__(self, name):
        def _record(*a, **k):
            self.calls.append((name, a))
            return self
        return _record

    def execute(self):
        nxt = self.plan.pop(0) if self.plan else []
        if isinstance(nxt, Exception):
            raise nxt
        return SimpleNamespace(data=nxt)


class _Supabase:
    def __init__(self, plan):
        self.plan, self.calls = list(plan), []

    def table(self, _name):
        return _Query(self.plan, self.calls)


def _svc(plan):
    svc = sms.SocialMentionsService.__new__(sms.SocialMentionsService)
    svc.supabase = _Supabase(plan)
    return svc


def _eq_tickers(sb):
    return [a[1] for name, a in sb.calls if name == "eq" and a[0] == "ticker"]


@pytest.fixture(autouse=True)
def _warm_apewisdom(monkeypatch):
    """Both filters landed; the cache holds a coin under ApeWisdom's key and a stock bare."""
    monkeypatch.setattr(ape, "_cache", {
        "ETH.X": {"mentions": 70, "mentions_24h_ago": 37, "_filter": "all-crypto"},
        "AAPL": {"mentions": 18, "mentions_24h_ago": 20, "_filter": "all-stocks"},
        "LINK": {"mentions": 3, "mentions_24h_ago": 1, "_filter": "all-stocks"},
        "LINK.X": {"mentions": 11, "mentions_24h_ago": 9, "_filter": "all-crypto"},
    })
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": True, "all-crypto": True})
    monkeypatch.setattr(ape, "_kick_background_refresh", lambda: None)


@pytest.mark.asyncio
async def test_a_coin_is_found_under_its_dot_x_key():
    assert await _svc([]).get_mentions_24h("ETH", is_crypto=True) == (70, 37, True)


@pytest.mark.asyncio
async def test_the_pre_fix_lookup_is_a_confident_zero_which_is_the_whole_bug():
    """A populated-cache miss is a REAL zero by design — so the bare key was indistinguishable
    from "nobody mentions it". This is what "Reddit data unavailable" on every coin was."""
    assert await _svc([]).get_mentions_24h("ETH") == (0, 0, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("held", ["eth", " ETH ", "ETH.X"])
async def test_every_shape_of_the_base_reaches_the_same_coin(held):
    assert await _svc([]).get_mentions_24h(held, is_crypto=True) == (70, 37, True)


@pytest.mark.asyncio
async def test_the_service_never_strips_a_base_that_ends_in_usd(monkeypatch):
    ape._cache["TUSD.X"] = {"mentions": 4, "mentions_24h_ago": 2, "_filter": "all-crypto"}
    assert await _svc([]).get_mentions_24h("TUSD", is_crypto=True) == (4, 2, True)
    svc = _svc([[{"mentions": 4}], []])
    await svc.get_mentions_7d("TUSD", is_crypto=True)
    assert _eq_tickers(svc.supabase) == ["TUSD.X", "TUSD.X"]


@pytest.mark.asyncio
async def test_a_stock_that_shares_a_coins_name_is_the_stock():
    assert await _svc([]).get_mentions_24h("LINK") == (3, 1, True)
    assert await _svc([]).get_mentions_24h("LINK", is_crypto=True) == (11, 9, True)


@pytest.mark.asyncio
async def test_a_coin_absent_from_a_populated_cache_is_still_a_real_zero():
    assert await _svc([]).get_mentions_24h("ZZZZ", is_crypto=True) == (0, 0, True)


@pytest.mark.asyncio
async def test_the_7d_windows_query_the_dot_x_rows(monkeypatch):
    svc = _svc([[{"mentions": 70}, {"mentions": 37}, {"mentions": 34}], [{"mentions": 15}]])
    assert await svc.get_mentions_7d("ETH", is_crypto=True) == (141, 15, True)
    assert _eq_tickers(svc.supabase) == ["ETH.X", "ETH.X"]


@pytest.mark.asyncio
async def test_the_7d_windows_for_a_stock_stay_bare():
    svc = _svc([[{"mentions": 18}], []])
    assert await svc.get_mentions_7d("aapl") == (18, 0, True)
    assert _eq_tickers(svc.supabase) == ["AAPL", "AAPL"]


@pytest.mark.asyncio
async def test_the_cold_cache_fallback_queries_the_dot_x_row(monkeypatch):
    monkeypatch.setattr(ape, "_cache", {})
    monkeypatch.setattr(ape, "_loaded", {"all-stocks": False, "all-crypto": False})
    svc = _svc([[{"mentions": 70, "snapshot_date": "2026-09-22"},
                 {"mentions": 37, "snapshot_date": "2026-09-21"}]])
    assert await svc.get_mentions_24h("ETH", is_crypto=True) == (70, 37, True)
    assert _eq_tickers(svc.supabase) == ["ETH.X"]


@pytest.mark.asyncio
async def test_a_failed_7d_query_logs_the_key_it_used(caplog):
    svc = _svc([RuntimeError("42501")])
    with caplog.at_level("WARNING"):
        assert await svc.get_mentions_7d("ETH", is_crypto=True) == (0, 0, False)
    assert "ETH.X" in caplog.text


# ── the sentiment service forwards the asset class to BOTH arms ──────────────


def _sentiment_svc(monkeypatch):
    ss._cache.clear()
    svc = ss.SentimentService.__new__(ss.SentimentService)

    async def _articles(*a, **k):
        return []

    async def _price(*a, **k):
        return {}

    async def _hist(*a, **k):
        return []

    monkeypatch.setattr(svc, "_get_articles", _articles)
    monkeypatch.setattr(svc, "_fetch_price_data", _price)
    monkeypatch.setattr(svc, "_fetch_historical_prices", _hist)
    return svc


class _RecordingSocial:
    def __init__(self):
        self.calls = []

    async def get_mentions_24h(self, ticker, *, is_crypto=False):
        self.calls.append(("24h", ticker, is_crypto))
        return (70, 37, True) if is_crypto else (18, 20, True)

    async def get_mentions_7d(self, ticker, *, is_crypto=False):
        self.calls.append(("7d", ticker, is_crypto))
        return (141, 15, True) if is_crypto else (60, 50, True)


@pytest.mark.asyncio
async def test_a_crypto_request_reaches_both_social_arms_as_crypto(monkeypatch):
    svc = _sentiment_svc(monkeypatch)
    social = _RecordingSocial()
    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: social)
    resp = await svc.get_sentiment("ETHUSD", social_ticker="ETH", is_crypto=True)
    assert sorted(social.calls) == [("24h", "ETH", True), ("7d", "ETH", True)]
    assert resp.social_mentions == 70.0 and resp.social_mentions_known is True
    assert resp.social_mentions_7d == 141.0 and resp.social_mentions_7d_known is True
    assert resp.social_data_available is True


@pytest.mark.asyncio
@pytest.mark.parametrize("pair,base", [("ETHUSD", "ETH"), ("ETHUSDT", "ETH"), ("TUSDUSD", "TUSD"), ("BTC", "BTC")])
async def test_a_crypto_caller_without_a_social_ticker_gets_exactly_one_strip(monkeypatch, pair, base):
    """The strip lives at the service boundary, once: a pair loses its quote currency
    (`USDT` before `USD`), a bare base passes through, a stablecoin base keeps its USD."""
    svc = _sentiment_svc(monkeypatch)
    social = _RecordingSocial()
    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: social)
    await svc.get_sentiment(pair, is_crypto=True)
    assert sorted(social.calls) == [("24h", base, True), ("7d", base, True)]


@pytest.mark.asyncio
async def test_a_stablecoin_base_from_the_endpoint_is_not_stripped_again(monkeypatch):
    svc = _sentiment_svc(monkeypatch)
    social = _RecordingSocial()
    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: social)
    await svc.get_sentiment("TUSDUSD", social_ticker="TUSD", is_crypto=True)
    assert sorted(social.calls) == [("24h", "TUSD", True), ("7d", "TUSD", True)]
    assert apewisdom_key("TUSD", is_crypto=True) == "TUSD.X"


@pytest.mark.asyncio
async def test_an_explicit_social_ticker_wins_over_the_derived_base(monkeypatch):
    """The caller's `social_ticker` is authoritative — the boundary strip is only the
    fallback for a caller that passes the pair alone."""
    svc = _sentiment_svc(monkeypatch)
    social = _RecordingSocial()
    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: social)
    await svc.get_sentiment("ETHUSD", social_ticker="weth", is_crypto=True)
    assert sorted(social.calls) == [("24h", "WETH", True), ("7d", "WETH", True)]


@pytest.mark.asyncio
async def test_a_stock_request_reaches_both_social_arms_as_stock(monkeypatch):
    svc = _sentiment_svc(monkeypatch)
    social = _RecordingSocial()
    monkeypatch.setattr(ss, "get_social_mentions_service", lambda: social)
    resp = await svc.get_sentiment("AAPL")
    assert sorted(social.calls) == [("24h", "AAPL", False), ("7d", "AAPL", False)]
    assert resp.social_mentions == 18.0


# ── the callers keep passing what the service needs ──────────────────────────


def _calls_in(source: str):
    out = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(".get_sentiment"):
            out.append({k.arg: ast.unparse(k.value) for k in node.keywords} | {"_args": [ast.unparse(a) for a in node.args]})
    return out


def test_the_crypto_endpoint_passes_the_bare_base_and_the_flag():
    from app.api.v1.endpoints import crypto as ep

    calls = _calls_in(inspect.getsource(ep))
    assert calls, "the crypto sentiment route no longer calls get_sentiment"
    for c in calls:
        assert c.get("is_crypto") == "True", c
        assert c.get("social_ticker") == "symbol", "the bare base is the social key; the service adds `.X`"


def test_the_chat_service_passes_the_flag_it_computed():
    from app.services import chat_service as cs

    calls = _calls_in(inspect.getsource(cs))
    assert calls, "chat no longer calls get_sentiment"
    for c in calls:
        assert c.get("is_crypto") == "is_crypto", c
        assert c.get("social_ticker") == "social_ticker", c
