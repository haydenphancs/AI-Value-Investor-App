"""Tests for FMP live-price WebSocket routing + TLS.

Guards the two Sentry-reported failures on the Bitcoin path:

1. `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate` —
   the WebSocket handshake must use the certifi trust store, not the host's
   default OpenSSL CA path.
2. BTCUSD silently streaming nothing — crypto pairs must route to FMP's dedicated
   crypto socket with a `login` event and a lowercase ticker, not the stock socket.

No network: `websockets.connect` is monkeypatched with a fake socket that records
the URL, kwargs, and every message sent.
"""

import json
import ssl

import pytest

import app.services.live_price_manager as m


# ---------------------------------------------------------------------------
# Pure routing helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ticker", ["BTCUSD", "btcusd", "ETHUSD", "SOLUSD"])
def test_crypto_pairs_route_to_crypto_socket(ticker):
    url, send_login, sub_ticker = m._fmp_ws_target(ticker)
    assert url == m.FMP_WS_URL_CRYPTO
    assert send_login is True
    assert sub_ticker == ticker.lower()  # crypto socket wants lowercase


@pytest.mark.parametrize("ticker", ["AAPL", "aapl", "BRK.B", "T", "SPY"])
def test_stocks_route_to_stock_socket(ticker):
    url, send_login, sub_ticker = m._fmp_ws_target(ticker)
    assert url.startswith(m.FMP_WS_URL_STOCK)
    assert "apikey=" in url  # stock socket authenticates via URL query param
    assert send_login is False
    assert sub_ticker == ticker.upper()


def test_short_usd_ticker_is_not_treated_as_crypto():
    # "USD" alone (len 3) fails the >= 5 crypto heuristic -> stock path, no crash.
    url, send_login, _ = m._fmp_ws_target("USD")
    assert url.startswith(m.FMP_WS_URL_STOCK)
    assert send_login is False


# ---------------------------------------------------------------------------
# TLS trust store
# ---------------------------------------------------------------------------

def test_ssl_context_is_certifi_backed_and_verifying():
    assert isinstance(m._SSL_CONTEXT, ssl.SSLContext)
    assert m._SSL_CONTEXT.verify_mode == ssl.CERT_REQUIRED
    assert m._SSL_CONTEXT.check_hostname is True
    # Loaded a real bundle, independent of the host's default OpenSSL CA path.
    assert len(m._SSL_CONTEXT.get_ca_certs()) > 0


# ---------------------------------------------------------------------------
# _open_fmp_ws end-to-end (fake socket)
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))


def _install_fake_connect(monkeypatch):
    record = {"url": None, "kwargs": None}

    async def _connect(url, **kwargs):
        record["url"] = url
        record["kwargs"] = kwargs
        return _FakeWS()

    monkeypatch.setattr(m.websockets, "connect", _connect)
    return record


@pytest.mark.asyncio
async def test_open_crypto_ws_logs_in_then_subscribes_lowercase(monkeypatch):
    record = _install_fake_connect(monkeypatch)
    mgr = m.LivePriceManager()

    ws = await mgr._open_fmp_ws("BTCUSD")

    # Correct endpoint + certifi TLS context passed through.
    assert record["url"] == m.FMP_WS_URL_CRYPTO
    assert record["kwargs"].get("ssl") is m._SSL_CONTEXT

    # login MUST precede subscribe on the crypto socket.
    events = [e["event"] for e in ws.sent]
    assert events == ["login", "subscribe"]
    assert ws.sent[0]["data"]["apiKey"] == m.settings.FMP_API_KEY
    assert ws.sent[1]["data"]["ticker"] == "btcusd"


@pytest.mark.asyncio
async def test_open_stock_ws_subscribes_without_login(monkeypatch):
    record = _install_fake_connect(monkeypatch)
    mgr = m.LivePriceManager()

    ws = await mgr._open_fmp_ws("aapl")

    assert record["url"].startswith(m.FMP_WS_URL_STOCK)
    assert record["kwargs"].get("ssl") is m._SSL_CONTEXT

    # Stock socket: no login event, uppercase ticker.
    events = [e["event"] for e in ws.sent]
    assert events == ["subscribe"]
    assert ws.sent[0]["data"]["ticker"] == "AAPL"
