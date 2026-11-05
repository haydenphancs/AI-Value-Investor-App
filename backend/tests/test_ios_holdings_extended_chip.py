"""The Tracking row's pre-market / after-hours chip (2026-10-30), iOS half — source scans.

The backend sends `extended_price` / `extended_change_percent` / `extended_session` outside
the regular session (`test_tracking_extended_print.py`). The row draws them as a small chip
LEFT of the day change, `☾ +0.08%  ▲ +1.48%`, on the line that already exists, because the
owner ruled out a taller card. Pinned here, each guard broken once in memory by `MUTATIONS`:

  wire_parity      the DTO decodes the three keys leniently, and `ExtendedSession`'s raw values
                   ARE the backend's `market_hours.WIRE_STATUS_*` (read from Python, not copied);
  whole_or_nothing `ViewThatFits` with a zero-size fallback: on a phone too narrow for chip and
                   day change together the chip is left out, never squeezed into "…";
  priority         the price button is offered its width FIRST (`.layoutPriority(1)`), so the
                   fit is measured against the space really left after the 200pt navigation
                   side — at equal priority it was offered half the row and the row overflowed
                   its card on a 375pt phone; the chip yields to the day change (`-1`);
  same_height      the chip's 10pt text sits on the 13pt change line (the day change keeps the
                   default size), so `AssetsListSection`'s height probe is still exact;
  new_york_gate    a snapshot-painted chip hides once the bell rings, by New York's clock
                   (`MarketHoursUtil.liveSessionStatus`, the owner's "ET for markets" rule);
  glyph            `PriceChangeLabel.leadingSymbol` replaces the arrow only — sign, colour and
                   the unknown-move guards still come from the percent;
  voiceover        the extended move is read whether or not the chip found room.

Comments are stripped before every check (the prose beside these fixes names their tokens).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict

import pytest

from app.utils.market_hours import WIRE_STATUS_AFTER_HOURS, WIRE_STATUS_PRE_MARKET

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_ROW = "Views/Molecules/AssetRow.swift"
_LABEL = "Views/Atoms/PriceChangeLabel.swift"
_MODELS = "Models/TrackingModels.swift"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//") or line.strip().startswith("///"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    """The brace-balanced declaration that starts at ``header``, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace:i + 1])
    raise AssertionError(f"unbalanced braces after {header!r}")


def _sources() -> Dict[str, str]:
    return {rel: (_IOS / rel).read_text(encoding="utf-8") for rel in (_ROW, _LABEL, _MODELS)}


# ── the guards ────────────────────────────────────────────────────────────────


def wire_parity(src: Dict[str, str]) -> None:
    dto = _block(src[_MODELS], "struct TrackedAssetDTO")
    for case, key in (("extendedPrice", "extended_price"),
                      ("extendedChangePercent", "extended_change_percent"),
                      ("extendedSession", "extended_session")):
        assert f'case {case} = "{key}"' in dto, f"CodingKeys lost {key}"
    assert "lenient(Double.self, .extendedPrice).finiteOrNil" in dto
    assert "lenient(Double.self, .extendedChangePercent).finiteOrNil" in dto
    assert "lenient(String.self, .extendedSession)" in dto
    assert "extendedSession.flatMap(ExtendedSession.init(rawValue:))" in dto, (
        "an unknown session string must map to nil (no chip), never to a guessed glyph"
    )
    enum = _block(src[_MODELS], "enum ExtendedSession: String")
    raw = dict(re.findall(r'case (\w+) = "([^"]+)"', enum))
    assert raw == {"preMarket": WIRE_STATUS_PRE_MARKET, "afterHours": WIRE_STATUS_AFTER_HOURS}, (
        f"ExtendedSession drifted from the backend's wire values: {raw}"
    )


def whole_or_nothing(src: Dict[str, str]) -> None:
    chip = _block(src[_ROW], "private var extendedMoveChip: some View")
    assert "ViewThatFits(in: .horizontal)" in chip
    assert "Color.clear.frame(width: 0, height: 0)" in chip, (
        "without a zero-size LAST child, ViewThatFits shows the chip even when it cannot fit"
    )
    assert chip.index("PriceChangeLabel(") < chip.index("Color.clear.frame(width: 0, height: 0)"), (
        "the chip must be the FIRST choice and the empty view the fallback"
    )


def priority(src: Dict[str, str]) -> None:
    body = _block(src[_ROW], "var body: some View")
    second = body.index("Button {", body.index("Button {") + 1)
    navigation_button, toggle_button = body[:second], body[second:]
    assert ".layoutPriority(1)" in toggle_button, "the price button must be offered its width first"
    assert ".layoutPriority(1)" not in navigation_button
    assert "extendedMoveChip" in toggle_button and "extendedMoveChip" not in navigation_button
    chip = _block(src[_ROW], "private var extendedMoveChip: some View")
    assert ".layoutPriority(-1)" in chip, "the chip must yield to the day change"


def same_height(src: Dict[str, str]) -> None:
    chip = _block(src[_ROW], "private var extendedMoveChip: some View")
    assert "fontSize: 10" in chip
    body = _block(src[_ROW], "var body: some View")
    day = re.search(r"PriceChangeLabel\((.*?)\n\s*\)", body, re.S)
    assert day and "fontSize" not in day.group(1), (
        "the day change keeps the default 13pt — the line height the chip must not exceed"
    )
    assert "HStack(spacing: 0)" in body and body.count("extendedMoveChip") == 1


def new_york_gate(src: Dict[str, str]) -> None:
    gate = _block(src[_ROW], "private var extendedMove:")
    assert "if case .open? = MarketHoursUtil.liveSessionStatus() { return nil }" in gate
    assert "percent.isFinite" in gate
    chip = _block(src[_ROW], "private var extendedMoveChip: some View")
    assert "if let move = extendedMove" in chip


def glyph(src: Dict[str, str]) -> None:
    label = _block(src[_LABEL], "struct PriceChangeLabel")
    assert re.search(r"var leadingSymbol:\s*String\?\s*=\s*nil", label)
    arrow = re.search(r"if showArrow, isFinite \{(.*?)\n            \}", label, re.S)
    assert arrow and "leadingSymbol" in arrow.group(1), (
        "the glyph must replace the arrow INSIDE its isFinite guard, never draw on its own"
    )
    for reader in ("isPositive", "color", "formattedChange"):
        blk = re.search(rf"var {reader}:[^{{]*\{{(.*?)\n    \}}", label, re.S)
        assert blk and "leadingSymbol" not in blk.group(1), f"{reader} must not read the glyph"
    chip = _block(src[_ROW], "private var extendedMoveChip: some View")
    assert "leadingSymbol: move.session.symbolName" in chip
    assert "changeAmount: asset.extendedChangeAmount" in chip and "mode: changeDisplayMode" in chip


def voiceover(src: Dict[str, str]) -> None:
    body = _block(src[_ROW], "var body: some View")
    assert "\\(extendedAccessibilityValue)" in body, "the price button no longer reads the move"
    spoken = _block(src[_ROW], "private var extendedAccessibilityValue: String")
    assert "guard let move = extendedMove" in spoken, (
        "VoiceOver must read the same move the chip would show — gated by the same rule"
    )


GUARDS: Dict[str, Callable[[Dict[str, str]], None]] = {
    "wire_parity": wire_parity,
    "whole_or_nothing": whole_or_nothing,
    "priority": priority,
    "same_height": same_height,
    "new_york_gate": new_york_gate,
    "glyph": glyph,
    "voiceover": voiceover,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_guard_holds(name):
    GUARDS[name](_sources())


# ── each guard can fail ───────────────────────────────────────────────────────

MUTATIONS = [
    ("wire_parity", _MODELS, 'case afterHours = "after_hours"', 'case afterHours = "afterhours"'),
    ("wire_parity", _MODELS, "lenient(Double.self, .extendedPrice).finiteOrNil",
     "lenient(Double.self, .extendedPrice)"),
    ("whole_or_nothing", _ROW, "                Color.clear.frame(width: 0, height: 0)\n", ""),
    ("priority", _ROW, "            .layoutPriority(1)\n", ""),
    ("priority", _ROW, "            .layoutPriority(-1)\n", ""),
    ("same_height", _ROW, "fontSize: 10", "fontSize: 13"),
    ("new_york_gate", _ROW, "        if case .open? = MarketHoursUtil.liveSessionStatus() { return nil }\n", ""),
    ("glyph", _LABEL, "Image(systemName: leadingSymbol\n                      ?? (isPositive",
     "Image(systemName: (isPositive"),
    ("voiceover", _ROW, "\\(changeAccessibilityValue)\\(extendedAccessibilityValue)",
     "\\(changeAccessibilityValue)"),
]


@pytest.mark.parametrize("name,rel,old,new", MUTATIONS,
                         ids=[f"{m[0]}:{m[2][:24].strip()}" for m in MUTATIONS])
def test_every_guard_kills_its_mutation(name, rel, old, new):
    src = _sources()
    assert src[rel].count(old) == 1, f"mutation anchor drifted: {old!r}"
    src[rel] = src[rel].replace(old, new)
    with pytest.raises(AssertionError):
        GUARDS[name](src)


def test_every_guard_has_a_mutation():
    assert {m[0] for m in MUTATIONS} == set(GUARDS)
