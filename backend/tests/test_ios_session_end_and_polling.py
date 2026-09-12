"""Four iOS defects from the 2026-09-12 pass, all confirmed by two skeptics.

Two are the auth.md §7 shape — a request that resolves AFTER sign-out writes the ended
session's data back into device-global state that `reset()` had just cleared:

* `WhaleService.syncFromAPIResponse` re-persisted the follows key with NO epoch check,
  while its sibling `toggleFollow` has one. Same bleed, different door.
* `PriceAlertStore.performLoad` re-published the previous account's alerts AND stamped
  `lastLoadedAt`, which then suppressed the reload that would have healed it.

Two are lifecycle:

* `TrackingViewModel.stopPriceRefreshTimer()` had NO caller anywhere, so the 30-second
  `/tracking/assets` poll ran for the life of the process — from every other tab, in the
  background, after sign-out.
* `IndexDetailViewModel`'s range sink STOPPED the 30-second timer on any non-intraday
  range, permanently killing the level refresh — directly contradicting the timer body,
  which already says "The level header refreshes either way".

Brace-bound and comment-stripped: every fix EXPLAINS the bug it replaced, so an
un-stripped scan would pass on the prose after a revert (.claude/rules/testing.md §3).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())


def _block(src: str, header_re: str) -> str:
    m = re.search(header_re, src)
    assert m, f"declaration not found: {header_re}"
    i = src.index("{", m.end())
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError("unbalanced braces")


def _code(rel: str) -> str:
    return _strip((_IOS / rel).read_text(encoding="utf-8"))


def test_the_comment_stripper_actually_strips():
    raw = (_IOS / "Views" / "Screens" / "WhaleService.swift").read_text(encoding="utf-8")
    assert "//" in raw and "//" not in _strip(raw)


# ── the two session-end bleeds ──────────────────────────────────────────────────────


def test_the_whale_follow_sync_refuses_an_ended_session():
    src = _code("Views/Screens/WhaleService.swift")
    body = _block(src, r"func syncFromAPIResponse\(_ whales: \[TrendingWhale\], asOf epoch: Int\)")
    assert "epoch == identityEpoch" in body, (
        "the follows key has a SECOND writer with no epoch check — a response landing "
        "after sign-out re-persists the ended session's follows for the next account"
    )
    assert "saveFollowedWhales()" in body, "the test would be vacuous if nothing persisted"
    reset = _block(src, r"func reset\(\)")
    assert "identityEpoch &+= 1" in reset and "removeObject(forKey:" in reset


def test_the_caller_captures_the_epoch_before_its_request():
    src = _code("ViewModels/TrackingViewModel.swift")
    body = _block(src, r"private func loadWhaleList\(retryCount: Int = 3\) async")
    cap = body.index("WhaleService.shared.currentIdentityEpoch")
    use = body.index("syncFromAPIResponse(")
    assert cap < use, "the epoch must be read BEFORE the request, not after it returns"
    assert "asOf: whaleSyncEpoch" in body


def test_the_price_alert_store_refuses_an_ended_session():
    src = _code("Core/Services/PriceAlertStore.swift")
    load = _block(src, r"private func performLoad\(\) async")
    assert "let epoch = identityEpoch" in load
    assert load.count("epoch == identityEpoch") >= 2, (
        "both the success and the failure arm must bail: the success arm also stamps "
        "lastLoadedAt, which suppresses the reload that would heal it"
    )
    assert load.index("let epoch = identityEpoch") < load.index("fetchPriceAlerts"), \
        "the epoch must be captured before the await"
    reset = _block(src, r"func reset\(\)")
    assert "identityEpoch &+= 1" in reset
    assert reset.index("identityEpoch &+= 1") < reset.index("alerts = []"), \
        "bump FIRST, or an in-flight load re-fills what reset just cleared"


# ── the two lifecycle leaks ─────────────────────────────────────────────────────────


def test_the_tracking_price_poll_is_actually_stopped():
    view = _code("Views/Screens/TrackingView.swift")
    task = _block(view, r"\.task\(id: isActiveTab\)")
    assert "stopPriceRefreshTimer()" in task, (
        "the 30-second /tracking/assets poll had no stop caller at all"
    )
    guard_idx = task.index("guard isActiveTab else")
    stop_idx = task.index("stopPriceRefreshTimer()")
    load_idx = task.index("loadIfNeeded()")
    assert guard_idx < stop_idx < load_idx, (
        "the stop belongs in the not-active branch; a trailing defer would fire the moment "
        "the load returned and kill the timer it had just started"
    )
    assert "defer { viewModel.stopPriceRefreshTimer() }" not in task


def test_the_tracking_price_poll_is_restarted_when_the_tab_comes_back():
    """A stop with no matching start is a permanent freeze, not a fix.

    `ContentView` keeps every tab's `@StateObject` alive, so `hasLoadedOnce` is still true
    when the user returns — and `loadIfNeeded()`'s `guard !hasLoadedOnce else { return }`
    used to return BEFORE reaching `startPriceRefreshTimer()`, which sits inside that
    guard. The stop had a caller and the start did not: one tab switch froze Holdings
    prices and P/L for the rest of the process, with no spinner and no error. `hasLoadedOnce`
    is reset in exactly one place — sign-in/sign-out — so nothing else could recover it.
    """
    vm = _code("ViewModels/TrackingViewModel.swift")
    fn = _block(vm, r"func loadIfNeeded\(\) async")
    assert "guard !hasLoadedOnce else" in fn
    early = fn[fn.index("guard !hasLoadedOnce else"):]
    early = early[:early.index("return") + len("return")]
    assert "startPriceRefreshTimer()" in early, (
        "re-activating the tab never restarts the 30-second poll — after one tab switch "
        "the holdings prices are frozen for the life of the process"
    )
    # …and the first-load path still starts it (control).
    assert fn.count("startPriceRefreshTimer()") >= 2


def test_starting_the_price_poll_twice_cannot_leak_a_task():
    """The restart above is only safe because the starter cancels first."""
    vm = _code("ViewModels/TrackingViewModel.swift")
    fn = _block(vm, r"func startPriceRefreshTimer\(\)")
    assert fn.index("priceRefreshTask?.cancel()") < fn.index("priceRefreshTask = Task"), (
        "re-arming the poll would leave the previous task running — two pollers, double "
        "the /tracking/assets traffic per tab switch"
    )


def test_the_index_level_refresh_survives_a_range_change():
    src = _code("ViewModels/IndexDetailViewModel.swift")
    # The RANGE sink is the one that publishes `newRange`; it ends where the INTERVAL
    # sink begins. Anchored on the closure parameter rather than a publisher name so a
    # rename of the @Published property does not silently empty this slice.
    i = src.index("{ [weak self] newRange in")
    j = src.index("chartSettings.$selectedInterval", i)
    sink = src[i:j]
    assert "loadChartData(range: newRange)" in sink, "the range sink slice is empty"
    assert "stopChartRefreshTimer()" not in sink, (
        "tapping 3M permanently killed the 30-second LEVEL refresh, with nothing to "
        "restart it — while the timer's own body already decides per tick whether to "
        "include chart bars"
    )
    assert "startChartRefreshTimer()" in sink
    # …and the teardown that SHOULD stop it is still wired.
    assert "stopChartRefreshTimer()" in _block(src, r"func stopLivePriceUpdates\(\)")
