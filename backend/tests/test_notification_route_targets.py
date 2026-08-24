"""Every notification must open where it is ABOUT, on the right kind of screen.

Two defects this pins, both invisible in every server log because the damage happens on
the phone:

  1. **`ticker_move` emitted no `asset_type`.** `NotificationRoute.assetType(from:)`
     falls back to `.stock` for an unknown or missing value — a deliberate fallback for
     unknown values, which then became the PRIMARY path for the app's most common
     notification. Every BTC and ETH price alert opened `TickerDetailView`, the equity
     screen, to render stock fundamentals for a coin. Confirmed live: BTC and ETH
     `ticker_move` rows exist in `notification_events`.

  2. **Every notification landed on Overview.** "Insider activity in ACHR" made the user
     find the Holders tab themselves, then the Insiders sub-tab inside it.

`route_tab` / `route_section` live on the registry rather than in each sender because the
registry's own docstring reserves route shape to itself — and because the shape had
already drifted per-sender (`profile_match` set `kind` where it meant `route`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.notification_kinds import (
    NOTIFICATION_KINDS,
    _SECTIONS_BY_TAB,
    _VALID_SECTIONS,
    _VALID_TABS,
    ticker_route,
)

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_TICKER_TABS = _IOS / "Models" / "TickerDetailModels.swift"
_HOLDERS = _IOS / "Models" / "HoldersModels.swift"
_SENDERS = Path(__file__).resolve().parents[1] / "app" / "services"


def _swift_enum_raw_values(path: Path, enum_name: str) -> set:
    """Lowercased `rawValue`s of a Swift enum, read from source.

    There is no XCTest target, so the Swift side is pinned by scanning it.
    """
    src = path.read_text(encoding="utf-8")
    start = src.index(f"enum {enum_name}")
    body = src[start : src.index("\n}", start)]
    return {m.lower() for m in re.findall(r'case\s+\w+\s*=\s*"([^"]+)"', body)}


# ── the tab/section vocabulary matches iOS ───────────────────────────────────


def test_every_tab_the_registry_can_emit_exists_on_the_ios_screen():
    """An unknown tab is NOT an error on the client — it silently lands on Overview.
    So a typo here would ship as "the deep link just doesn't work", with nothing in any
    log. This is the only place it can fail loudly."""
    ios = _swift_enum_raw_values(_TICKER_TABS, "TickerDetailTab")
    assert ios, "could not parse TickerDetailTab — the scan is vacuous"
    missing = _VALID_TABS - ios
    assert not missing, f"tabs with no TickerDetailTab counterpart: {sorted(missing)}"


def test_every_holders_section_exists_on_the_ios_screen():
    ios = _swift_enum_raw_values(_HOLDERS, "RecentActivitiesTab")
    assert ios, "could not parse RecentActivitiesTab — the scan is vacuous"
    missing = _SECTIONS_BY_TAB["holders"] - ios
    assert not missing, f"sections with no RecentActivitiesTab counterpart: {sorted(missing)}"


def test_registered_kinds_only_use_known_tabs_and_sections():
    for key, kind in NOTIFICATION_KINDS.items():
        if kind.route_tab is not None:
            assert kind.route_tab in _VALID_TABS, key
        if kind.route_section is not None:
            assert kind.route_section in _VALID_SECTIONS, key
            assert kind.route_tab is not None, f"{key}: section with no tab"


# ── the kinds that must deep-link ────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind_key,tab,section",
    [
        ("insider_trade", "holders", "insiders"),
        ("whale_13f", "holders", "institutions"),
        ("congress_trade", "holders", "congress"),
        ("earnings_result", "financials", None),
        ("earnings_upcoming", "financials", None),
    ],
)
def test_the_kinds_that_have_a_home_still_point_at_it(kind_key, tab, section):
    route = ticker_route(kind_key, "ACHR")
    assert route["tab"] == tab
    assert route.get("section") == section


def test_ticker_move_stays_on_overview():
    """Its body is a headline about anything — macro, regulatory, product — so a tab
    would be wrong for most of them. Absent, not empty: an empty string decodes on iOS
    as a present-but-blank tab and defeats the nil check."""
    route = ticker_route("ticker_move", "PLUG")
    assert "tab" not in route
    assert "section" not in route


# ── the route shape itself ───────────────────────────────────────────────────


def test_every_ticker_route_carries_an_asset_type():
    """The BTC-opens-the-equity-screen bug. `.stock` is the client's fallback for an
    UNKNOWN value, not a substitute for sending one."""
    for key in NOTIFICATION_KINDS:
        route = ticker_route(key, "BTC", asset_type="crypto")
        assert route.get("asset_type") == "crypto", key
        assert route.get("route"), key
        assert route.get("ticker") == "BTC", key


def test_the_asset_type_is_normalised_and_never_empty():
    assert ticker_route("ticker_move", "btc", asset_type="  Crypto ")["asset_type"] == "crypto"
    assert ticker_route("ticker_move", "AAPL", asset_type="")["asset_type"] == "stock"
    assert ticker_route("ticker_move", "AAPL", asset_type=None)["asset_type"] == "stock"


def test_no_sender_hand_writes_a_ticker_route_any_more():
    """Every sender goes through `ticker_route`. A hand-written dict is how `ticker_move`
    lost its `asset_type` and how `profile_match` came to set `kind` where it meant
    `route` — both of which work fine on the server and fail on the phone."""
    offenders = []
    for path in list((_SENDERS / "notification_senders").glob("*.py")) + [
        _SENDERS / "updates_insight_sweeper.py",
        _SENDERS / "price_alert_service.py",
    ]:
        code = "\n".join(
            "" if line.strip().startswith("#") else line
            for line in path.read_text(encoding="utf-8").splitlines()
        )
        # A route/data dict literal that spells out "route": "ticker" by hand.
        if re.search(r'["\']route["\']\s*:\s*["\']ticker["\']', code):
            offenders.append(path.name)
    assert not offenders, (
        f"hand-written ticker routes in {offenders} — use "
        f"notification_kinds.ticker_route() so the shape stays in one place"
    )
