"""Market Pulse must not paint an unknown move as a flat green day.

`_fetch_pulse_item` set `change_percent = 0.0` whenever the change was unmeasurable, and
the strip colours off `>= 0` — so all five tiles rendered "+0.00%" in GREEN. The comment
there justified it with "these five symbols are heavily traded ETFs read from
`/stable/profile`, so an absent change is a corrupt token rather than a routine state".
That premise expired: `_build_pulse` reads them through the SCREENER batch now, and
`_from_screener` leaves the change None on any stale or missing `market_close_snapshot`
row — i.e. one missed overnight ingest paints the whole strip green (found 2026-09-12).

The fix is the three-state pattern the comment itself named: keep 0.0 on the wire for
shipped builds, add `change_known`, and give EVERY iOS reader a neutral state — the text,
the colour, the sparkline direction and the dashed reference line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.home_dashboard import MarketPulseItemResponse

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


# ── the wire ────────────────────────────────────────────────────────────────────────


def test_change_percent_stays_a_non_optional_float_with_a_known_companion():
    f = MarketPulseItemResponse.model_fields
    assert f["change_percent"].annotation is float, (
        "making a shipped non-Optional Double nullable crashes every build in the field"
    )
    assert f["change_known"].default is True, "absent must mean 'measured', the old meaning"


@pytest.mark.asyncio
async def test_an_unmeasurable_change_is_served_as_unknown_not_flat(monkeypatch, caplog):
    import app.services.home_dashboard_service as hd

    svc = hd.HomeDashboardService.__new__(hd.HomeDashboardService)

    async def _spark(symbol, extended_hours=False):
        return [], 0.0, 1.0

    monkeypatch.setattr(svc, "_fetch_sparkline", _spark, raising=False)
    cfg = {"name": "S&P 500 ETF", "type": "etf", "symbol": "SPY"}
    with caplog.at_level("WARNING", logger="app.services.home_dashboard_service"):
        tile = await svc._fetch_pulse_item(
            cfg, {"price": 651.20, "changePercentage": None, "previousClose": None}
        )
    assert tile is not None, "a good price must still render a tile"
    assert tile.change_percent == 0.0, "the wire sentinel for shipped builds is unchanged"
    assert tile.change_known is False, "the tile claimed a measured flat day"
    assert any("change_known=false" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_measured_change_is_marked_known(monkeypatch):
    import app.services.home_dashboard_service as hd

    svc = hd.HomeDashboardService.__new__(hd.HomeDashboardService)

    async def _spark(symbol, extended_hours=False):
        return [1.0, 2.0], 0.0, 1.0

    monkeypatch.setattr(svc, "_fetch_sparkline", _spark, raising=False)
    tile = await svc._fetch_pulse_item(
        {"name": "S&P 500 ETF", "type": "etf", "symbol": "SPY"},
        {"price": 651.20, "changePercentage": -1.8, "previousClose": 663.1},
    )
    assert tile.change_known is True and tile.change_percent == -1.8


# ── every iOS reader has a neutral state ────────────────────────────────────────────


def test_the_dto_decodes_the_flag_optionally_and_defaults_to_measured():
    dto = _block(_strip((_IOS / "Models" / "HomeDashboardModels.swift").read_text()),
                 r"struct MarketPulseItemDTO\s*:\s*Decodable\s*")
    assert "let changePercent: Double\n" in dto, "the wire field must stay non-Optional"
    assert "let changeKnown: Bool?" in dto, (
        "a non-Optional new field named in CodingKeys throws keyNotFound against a backend "
        "that predates it — that would blank the WHOLE dashboard"
    )
    assert 'case changeKnown = "change_known"' in dto
    repo = _strip((_IOS / "Core" / "Repositories" / "HomeRepository.swift").read_text())
    assert "dto.changeKnown ?? true" in repo


@pytest.mark.parametrize("reader", ["changeText", "isPositive"])
def test_the_mapper_neutralises_both_derived_fields(reader):
    repo = _strip((_IOS / "Core" / "Repositories" / "HomeRepository.swift").read_text())
    body = _block(repo, r"private static func mapPulse\(_ dto: MarketPulseItemDTO\)")
    line = next((l for l in body.splitlines() if l.strip().startswith(reader + ":")), None)
    assert line and "changeKnown" in line, (
        f"{reader} does not consult changeKnown — an unknown move still renders as a "
        "signed percentage / a direction"
    )


def test_the_card_has_a_neutral_state_for_colour_and_for_the_sparkline():
    card = _strip((_IOS / "Views" / "Molecules" / "MarketPulseCard.swift").read_text())
    colour = _block(card, r"private var changeColor: Color")
    assert "item.changeKnown" in colour, (
        "the tile would paint a RED dash — a fabricated decline. This is the exact trap the "
        "index header hit in the 2026-09-11 pass: a *_known flag needs a neutral state in "
        "EVERY reader."
    )
    body = _block(card, r"var body: some View")
    assert "item.changeKnown\n" in body or "item.changeKnown ?" in body, \
        "the sparkline still colours off a direction derived from an unknown change"
    assert "referencePrice: item.changeKnown ? item.previousClose : nil" in body


def test_the_watchlist_tile_carries_the_flag_through():
    repo = _strip((_IOS / "Core" / "Repositories" / "HomeRepository.swift").read_text())
    body = _block(repo, r"private static func mapWatchlistTile\(_ dto: MarketPulseItemDTO\)")
    assert "changeKnown: tile.changeKnown" in body, \
        "re-constructing the item dropped the flag back to its `true` default"


# ── the flag has to reach the MODEL, not just the two derived strings ────────────────


def test_map_pulse_puts_change_known_on_the_item_it_builds():
    """THE REGRESSION. `mapPulse` computed `changeKnown` and spent it on `changeText` and
    `isPositive`, then built `MarketPulseItem(...)` WITHOUT passing it — and the init
    defaults it to `true`. So `item.changeKnown` was always true and all three of
    `MarketPulseCard`'s neutral branches were dead code.

    Worse than a no-op: `isPositive` is deliberately `false` for an unknown move, so the
    colour guard fell through to `item.isPositive ? bullish : bearish` → BEARISH. The tile
    painted a RED "—" — a fabricated decline — on all five tiles at once, which is exactly
    the fabrication the flag was added to prevent. `mapWatchlistTile` forwards
    `tile.changeKnown`, so the Your Watchlist strip broke identically.
    """
    src = _strip((_IOS / "Core" / "Repositories" / "HomeRepository.swift").read_text())
    body = _block(src, r"static func mapPulse\s*\(")
    assert "changeKnown: changeKnown" in body, (
        "mapPulse never puts the flag on the model — every neutral branch in "
        "MarketPulseCard is unreachable and an unknown move renders BEARISH"
    )
    # …and the two derived strings still read it (control: a guard that only checked the
    # init would pass on a mapper that stopped using the flag for the text).
    assert "changeKnown ?" in body


def test_the_card_still_has_all_three_neutral_readers():
    """Control for the test above: the flag reaching the model proves nothing if the card
    stopped gating on it."""
    src = _strip((_IOS / "Views" / "Molecules" / "MarketPulseCard.swift").read_text())
    assert src.count("item.changeKnown") >= 3, (
        "the card lost a neutral branch — colour, sparkline direction and the dashed "
        "reference line each need one"
    )

