"""ONEQ is labelled "Nasdaq ETF" everywhere its short label is shown (TestFlight 1.0(8), home E3).

Tester: *"For Nasdaq, remove the composite, keep Nasdaq ETF only."* Developer decision
(2026-09-23): the Home-screen widget's "Nasdaq Comp ETF" goes too. The symbol stays ONEQ —
the Composite tracker, deliberately NOT QQQ (Nasdaq-100) — and the detail screen still
names the fund in full, which is what this rename must not touch.

The short label lives in THREE server lists plus the iOS mock that mirrors the shipped
strip; they drifted independently before, so they are pinned against each other here.
Category 1 (pure).
"""
import re
from pathlib import Path

from app.services import home_dashboard_service as HD
from app.services import home_service as HS
from app.services import index_service as IS
from app.services import widget_movers_service as WM

IOS_REPO = Path(__file__).resolve().parents[2] / "frontend/ios/ios/Core/Repositories/HomeRepository.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def _labels():
    return {
        "_PULSE_SYMBOLS": {r["symbol"]: r["name"] for r in HD._PULSE_SYMBOLS},
        "DEFAULT_MARKET_TICKERS": {r["symbol"]: r["name"] for r in HS.DEFAULT_MARKET_TICKERS},
        "widget _INDEX_SYMBOLS": dict(WM._INDEX_SYMBOLS),
    }


def test_oneq_is_nasdaq_etf_in_every_server_list():
    for where, labels in _labels().items():
        assert labels.get("ONEQ") == "Nasdaq ETF", (where, labels.get("ONEQ"))


def test_every_short_label_names_an_etf_and_none_says_composite():
    for where, labels in _labels().items():
        for sym, name in labels.items():
            if sym == HD._CRYPTO_PULSE_SYMBOL["symbol"]:
                continue
            assert "ETF" in name, (where, sym, name)       # 2026-09-08: a label names the fund
            assert "Comp" not in name, (where, sym, name)


def test_the_two_home_strips_agree_on_every_shared_symbol():
    pulse, legacy = _labels()["_PULSE_SYMBOLS"], _labels()["DEFAULT_MARKET_TICKERS"]
    shared = set(pulse) & set(legacy)
    assert shared >= {"SPY", "ONEQ", "DIA", "GLD"}
    for sym in shared:
        assert pulse[sym] == legacy[sym], sym


def test_the_ios_mock_mirrors_the_shipped_strip():
    src = _strip_comments(IOS_REPO.read_text())
    at = src.find("static let pulse: [MarketPulseItem] = [")
    assert at >= 0, "MockHomeRepository.pulse moved"
    depth, end = 0, None
    for i in range(src.index("[", src.index("=", at)), len(src)):
        if src[i] == "[":
            depth += 1
        elif src[i] == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = src[at:end]
    tiles = re.findall(r'MarketPulseItem\(\s*name:\s*"([^"]*)",\s*symbol:\s*"([^"]*)"', block)
    expected = [(r["name"], r["symbol"]) for r in HD._PULSE_SYMBOLS]
    expected.append((HD._CRYPTO_PULSE_SYMBOL["name"], HD._CRYPTO_PULSE_SYMBOL["symbol"]))
    assert len(tiles) == len(HD._PULSE_SYMBOLS) + 1, tiles     # anti-vacuity: the parser found them
    assert tiles == expected


def test_the_fund_name_on_the_detail_screen_is_unchanged():
    """Control: only the SHORT label was renamed — the index proxy still names the fund."""
    assert IS._INDEX_PROFILES["^IXIC"]["proxy_symbol"] == "ONEQ"
    assert IS._INDEX_PROFILES["^IXIC"]["name"] == "Fidelity Nasdaq Composite Index ETF"
