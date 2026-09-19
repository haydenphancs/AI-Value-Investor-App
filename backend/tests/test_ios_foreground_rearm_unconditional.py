"""F16-8 / F19-3 — the foreground / arm gate must not read the model's STALE `marketStatus`.

`ETFDetailViewModel.maybeStartStreaming` and `ETFDetailView`'s `didBecomeActive` handler
armed the only price refresh on `MarketHoursUtil.shouldStreamLivePrice(for:
etfData.marketStatus)` — a value captured at the LAST fetch, never re-read from the clock.
Open SPY at 20:30 ET (`.closed`), background overnight, foreground at 09:40: the model
still said `.closed`, nothing armed, and the header kept last night's price under a
"Market Closed" badge for the whole live session. The docstring above the arm site said
the guard "must NOT be replaced by anything that can latch" — and it had been.

The correct shape is what `IndexDetailViewModel` (load) and `CommodityDetailView`
(foreground) already do: arm UNCONDITIONALLY and let the timer body's per-tick
`MarketHoursUtil.isMarketActive()` gate decide. Gating the ARM on `isMarketActive()`
would only move the latch (foreground at 03:55 ET → never arms at 04:00).

This file pins the ETF screen (this lane's files). `TickerDetailView`,
`IndexDetailView` and `TickerDetailViewModel` carry the same gate and are another
lane's — see the fix report. Source-scan guards: comments stripped BEFORE brace-bounding,
anchored on the declaration, mutation-tested by hand.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _read(rel: str) -> str:
    p = _IOS / rel
    if not p.exists():
        pytest.fail(f"expected file is missing: {p}")
    return p.read_text(encoding="utf-8")


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _bounded(src: str, header: str) -> str:
    """The `{…}` block that follows `header`, on comment-STRIPPED source."""
    code = _strip(src)
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


def test_etf_maybe_start_streaming_arms_unconditionally():
    body = _bounded(_read("ViewModels/ETFDetailViewModel.swift"),
                    "private func maybeStartStreaming()")
    assert "startChartRefreshTimer()" in body, "the 30s live-slice refresh never arms"
    for latch in ("marketStatus", "shouldStreamLivePrice", "isMarketActive", "guard "):
        assert latch not in body, (
            f"maybeStartStreaming gates the ARM on `{latch}` — a stale model status (or "
            "the clock at arm time) latches the refresh off across a background/foreground"
        )


def test_etf_foreground_handler_re_arms_unconditionally():
    body = _bounded(_read("Views/Screens/ETFDetailView.swift"),
                    "UIApplication.didBecomeActiveNotification)) { _ in")
    assert "viewModel.startLivePriceUpdates()" in body, "foreground no longer resumes the refresh"
    for latch in ("marketStatus", "shouldStreamLivePrice", "isMarketActive", "if let", "guard "):
        assert latch not in body, (
            f"didBecomeActive gates the re-arm on `{latch}`: foreground at 09:40 after an "
            "overnight background keeps last night's price under a Closed badge"
        )


def test_etf_timer_body_is_the_one_legitimate_gate():
    """Anti-vacuity for the two tests above: arming unconditionally is only safe because
    the timer body gates EVERY tick on the wall clock, and the light slice it fetches
    rewrites `marketStatus` so the badge heals on the first live tick."""
    body = _bounded(_read("ViewModels/ETFDetailViewModel.swift"),
                    "private func startChartRefreshTimer()")
    assert "guard MarketHoursUtil.isMarketActive() else { continue }" in body
    assert "refreshLiveSlice(" in body
    merged = _bounded(_read("Models/ETFDetailResponseModels.swift"),
                      "func merged(\n        into data: ETFDetailData,")
    assert "out.marketStatus = marketStatus.resolvedMarketStatus" in merged


def test_etf_start_live_price_updates_is_the_timer():
    body = _bounded(_read("ViewModels/ETFDetailViewModel.swift"), "func startLivePriceUpdates()")
    assert "startChartRefreshTimer()" in body
