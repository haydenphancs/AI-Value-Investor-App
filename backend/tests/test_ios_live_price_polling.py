"""The FMP live-price WebSocket is gone, and the REST polling it fed must NOT go with it.

Deleting the stack was the easy half. The dangerous half is that five ViewModels gated
real work on `livePriceManager.isConnected`, and that flag is now absent rather than
false — so a careless delete leaves behind conditions that can never fire:

  * `TickerDetailViewModel` idled its 15s quote poll with
    `if isConnected { sleep; continue }`. Leaving a permanently-false check there is
    harmless; leaving the *poll* gated on a socket that no longer exists is not.
  * `ETFDetailViewModel.maybeStartStreaming` opened with `guard !isConnected` — a guard
    whose whole job was "not started yet". Replaced by anything that can latch, the 30s
    live-slice refresh never arms and the header price freezes for the life of the screen.
  * Ticker/ETF/Index all gated the intraday chart-refresh timer on
    `isIntraday && isConnected`. That conjunct false-forever means the intraday chart
    never refreshes again.

None of this is visible in a build or at a glance: every one of those screens still
compiles, still loads, and still paints a correct FIRST price. It just never moves after.

Per `.claude/rules/testing.md` §3 every scan below is comment-stripped BEFORE it is
brace-bounded (stripping after lets a `{` inside a comment unbalance the walk), anchored
on an identifier rather than a count, and was mutation-tested by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_VIEWMODELS = [
    "TickerDetailViewModel", "ETFDetailViewModel", "IndexDetailViewModel",
    "CommodityDetailViewModel", "CryptoDetailViewModel",
]


def _read(rel: str) -> str:
    p = _IOS / rel
    if not p.exists():
        pytest.fail(f"expected file is missing: {p}")
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `///`+`//` lines, trailing `//` tails, and `/* */` blocks.

    Load-bearing: every replacement doc-comment in this change NAMES the thing it removed
    ("Was `connectLivePrice()`", "`livePriceManager.isConnected`"). An un-stripped scan for
    the ABSENCE of those tokens fails on the explanation; an un-stripped scan for their
    PRESENCE passes on a revert whose comment survived.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _func_body(src: str, header: str) -> str:
    """Brace-balanced body of a declaration. Comments are stripped FIRST, so a brace
    inside a comment cannot unbalance the walk."""
    code = _strip_comments(src)
    start = code.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = code.index("{", start)
    depth = 0
    for i in range(open_brace, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[open_brace:i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1. The stack stays deleted ───────────────────────────────────────────────

def test_the_websocket_stack_is_gone_from_the_whole_swift_tree():
    """Streaming is excluded from the FMP Order Form (ToS §2.10). It must not come back
    by way of a revert or a copy-paste into a sixth screen."""
    # WORD-BOUNDED. A bare substring test matches `LivePriceUpdate` inside this change's
    # own `startLivePriceUpdates()`, which made the first version of this guard fail on
    # the fix rather than on the bug.
    banned = re.compile(r"\b(LivePriceWebSocketManager|livePriceManager|LivePriceUpdate)\b")
    offenders = []
    for path in _IOS.rglob("*.swift"):
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for m in set(banned.findall(code)):
            offenders.append(f"{path.relative_to(_REPO)}: {m}")
    assert not offenders, "the FMP live-price WebSocket is back:\n  " + "\n  ".join(offenders)


def test_the_backend_socket_is_gone_too():
    for rel in ("app/services/live_price_manager.py",
                "app/api/v1/endpoints/live_price.py",
                "app/services/certs/fmp_ws_intermediates.pem"):
        assert not (_REPO / "backend" / rel).exists(), f"{rel} is back"
    api = (_REPO / "backend/app/api/v1/api.py").read_text(encoding="utf-8")
    assert "live_price" not in api, "the live-price router is registered again"


# ── 2. …but every timer it used to gate still runs ───────────────────────────

def test_the_ticker_quote_poll_is_no_longer_gated_on_a_socket():
    body = _func_body(_read("ViewModels/TickerDetailViewModel.swift"),
                      "func startLivePriceUpdates()")
    # Anti-vacuity: this really is the poll loop.
    assert "pollQuotePrice()" in body, "scan drifted — this is not the quote poll"
    assert "while !Task.isCancelled" in body
    assert "isConnected" not in body, "the poll is gated on a socket that cannot connect"
    # Market hours is the ONE legitimate gate.
    assert "MarketHoursUtil.isMarketActive()" in body


def test_the_etf_refresh_arms_instead_of_guarding_on_a_socket():
    body = _func_body(_read("ViewModels/ETFDetailViewModel.swift"),
                      "private func maybeStartStreaming()")
    assert "startChartRefreshTimer()" in body, "the 30s live-slice refresh never arms"
    assert "isConnected" not in body, "a permanently-false guard blocks the refresh"


@pytest.mark.parametrize("vm", ["TickerDetailViewModel", "ETFDetailViewModel",
                                "IndexDetailViewModel"])
def test_the_intraday_chart_timer_is_gated_on_the_interval_alone(vm):
    """`isIntraday && isConnected` is false forever once the socket is gone."""
    code = _strip_comments(_read(f"ViewModels/{vm}.swift"))
    m = re.search(r"if\s+[\w.]*[Rr]ange\.defaultInterval\.isIntraday([^\{]*)\{", code)
    assert m, f"{vm}: the intraday chart-refresh gate is missing — scan drifted"
    assert "isConnected" not in m.group(1), (
        f"{vm}: the chart timer is still conjoined with a socket check"
    )


@pytest.mark.parametrize("vm", _VIEWMODELS)
def test_every_detail_viewmodel_exposes_the_renamed_lifecycle_pair(vm):
    """The Screens call these from onDisappear / willResignActive / didBecomeActive."""
    code = _strip_comments(_read(f"ViewModels/{vm}.swift"))
    for fn in ("func startLivePriceUpdates()", "func stopLivePriceUpdates()"):
        assert fn in code, f"{vm} is missing {fn}"
    assert "func connectLivePrice()" not in code, f"{vm} still has the old socket entry point"


@pytest.mark.parametrize("screen,expect_resume", [
    ("TickerDetailView", True), ("ETFDetailView", True), ("IndexDetailView", True),
    ("CommodityDetailView", True), ("CryptoDetailView", False),
])
def test_every_detail_screen_stops_and_resumes_the_refresh(screen, expect_resume):
    code = _strip_comments(_read(f"Views/Screens/{screen}.swift"))
    assert "viewModel.stopLivePriceUpdates()" in code, f"{screen} leaks the refresh timer"
    # CryptoDetailView deliberately has no resign/resume pair — crypto is 24/7 and it
    # re-arms from `.task` on reappear. Pinned so the asymmetry is a decision, not a gap.
    assert ("viewModel.startLivePriceUpdates()" in code) is expect_resume, (
        f"{screen}: resume-on-foreground presence changed"
    )
