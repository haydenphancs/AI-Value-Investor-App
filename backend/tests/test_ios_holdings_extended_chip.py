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
                   column is min(row - the 176pt navigation floor, the column cap) — at equal
                   priority the stack sizes the column first with half the row, it takes its
                   whole cap, and on a 375pt phone the row overflows its card; the chip yields
                   to the day change (`-1`);
  stable_fit       the change line AND the chip's slot fill their width (2026-11-11). Hugging
                   their content, a card was SIZED with the chip and then re-offered exactly
                   chip + day change when placed; the chip's fit test landed a float step short
                   and it vanished from a box already sized for it — ORCL, ACHR, NVDA, PLUG,
                   MRVL, INTC drew no chip and their price and change sat ~25pt left of the
                   edge, centred in the chip-sized box;
  reserved_room    the list reserves the after-hours column outside New York's regular session
                   (the same rule as the chip's own gate), so a card with no print keeps the
                   same column as the cards with one;
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
_SPARK = "Views/Atoms/SparklineView.swift"


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
    return {rel: (_IOS / rel).read_text(encoding="utf-8")
            for rel in (_ROW, _LABEL, _MODELS, _SPARK)}


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


def _after_block(src: str, header: str) -> str:
    """The text right after the brace-balanced block that starts at ``header`` (``src`` is
    already comment-stripped), leading whitespace dropped."""
    start = src.index(header)
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:].lstrip()
    raise AssertionError(f"unbalanced braces after {header!r}")


def stable_fit(src: Dict[str, str]) -> None:
    body = _block(src[_ROW], "var body: some View")
    assert _after_block(body, "HStack(spacing: 0) {").startswith(
        ".frame(maxWidth: .infinity, alignment: .trailing)"
    ), (
        "the change line hugs its content again: the column is then re-offered exactly chip + "
        "day change when it is placed, and the chip can drop from a box sized for it"
    )
    chip = _block(src[_ROW], "private var extendedMoveChip: some View")
    assert re.search(
        r"\}\s*\.frame\(maxWidth: \.infinity, alignment: \.trailing\)\s*\.layoutPriority\(-1\)", chip
    ), (
        "the chip's slot must FILL what the day change leaves (then yield with -1), so its "
        "width is never recomputed by subtraction when the line is placed"
    )


def reserved_room(src: Dict[str, str]) -> None:
    gate = _block(src[_ROW], "static var isOutsideRegularSession: Bool")
    assert "if case .open? = MarketHoursUtil.liveSessionStatus() { return false }" in gate, (
        "the list's reservation must follow the chip's own New York gate"
    )
    assert "return true" in gate
    modifier = _block(src[_ROW], "struct HoldingsPriceColumn: ViewModifier")
    assert "reservesExtendedMove ? AssetRow.extendedPriceColumnWidth : 0" in modifier, (
        "the column no longer widens for the chip, so after hours the chip has no room"
    )



# ── the sparkline's gray after-hours tail (2026-11-11) ────────────────────────
#
# Owner: "after market close, the chart will have a gray color ... the green/red is during
# market, after that is a gray color like an extend chart ... for crypto, it should not have
# gray because they are 24h." The backend sends the tail (`extended_sparkline`,
# `extended_regular_share`, `extended_span_to`; test_tracking_extended_print.py §6).

_TAIL_KEYS = {"extendedSparkline": "extended_sparkline",
              "extendedRegularShare": "extended_regular_share",
              "extendedSpanTo": "extended_span_to"}


def tail_wire_parity(src: Dict[str, str]) -> None:
    from app.schemas.tracking import TrackedAssetResponse
    dto = _block(src[_MODELS], "struct TrackedAssetDTO")
    for case, key in _TAIL_KEYS.items():
        assert key in TrackedAssetResponse.model_fields, f"the backend lost {key}"
        assert f'case {case} = "{key}"' in dto, f"CodingKeys lost {key}"
    assert "(lenient([Double].self, .extendedSparkline) ?? []).filter(\\.isFinite)" in dto, (
        "the tail must decode leniently and drop non-finite points — a strict decode of one "
        "bad value would blank the whole Holdings list"
    )
    assert "lenient(Double.self, .extendedRegularShare).finiteOrNil" in dto
    assert "lenient(Double.self, .extendedSpanTo).finiteOrNil" in dto
    for case in _TAIL_KEYS:
        assert f"{case}: {case}" in dto, f"toTrackedAsset() drops {case}"


def tail_drawable(src: Dict[str, str]) -> None:
    model = _block(src[_MODELS], "var afterHoursTail: SparklineTail?")
    assert "share > 0, share < spanTo, spanTo <= 1" in model, (
        "a tail is drawn only on an axis where the session ends before the tail does"
    )
    tail = _block(src[_SPARK], "struct SparklineTail: Equatable")
    assert "regularShare > 0 && regularShare < spanTo && spanTo <= 1" in tail, (
        "the atom must refuse an unusable tail itself, whoever its caller is"
    )


def tail_gate(src: Dict[str, str]) -> None:
    gate = _block(src[_ROW], "private var afterHoursTail: SparklineTail?")
    assert "guard Self.isOutsideRegularSession else { return nil }" in gate, (
        "a saved snapshot painted after the bell would draw yesterday's after-hours over the "
        "new day's line — the tail follows the chip's New York gate"
    )
    assert "return asset.afterHoursTail" in gate
    body = _block(src[_ROW], "var body: some View")
    call = re.search(r"SparklineView\((.*?)\n\s*\)", body, re.S)
    assert call and "tail: afterHoursTail" in call.group(1), "the row no longer passes the tail"


def tail_tone(src: Dict[str, str]) -> None:
    spark = _strip_comments(src[_SPARK])
    assert "private static let tailTone = AppColors.textMuted" in spark, (
        "the tail must be NEUTRAL gray: green or red would read as a gain or loss the "
        "session line did not make"
    )
    stroke = _block(src[_SPARK], "private func strokeTail(")
    assert ".color(Self.tailTone)" in stroke
    assert "AppColors.bullish" not in stroke and "AppColors.bearish" not in stroke


GUARDS: Dict[str, Callable[[Dict[str, str]], None]] = {
    "wire_parity": wire_parity,
    "whole_or_nothing": whole_or_nothing,
    "priority": priority,
    "same_height": same_height,
    "new_york_gate": new_york_gate,
    "glyph": glyph,
    "voiceover": voiceover,
    "stable_fit": stable_fit,
    "reserved_room": reserved_room,
    "tail_wire_parity": tail_wire_parity,
    "tail_drawable": tail_drawable,
    "tail_gate": tail_gate,
    "tail_tone": tail_tone,
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
    ("stable_fit", _ROW, "                    .frame(maxWidth: .infinity, alignment: .trailing)\n", ""),
    ("stable_fit", _ROW, ".frame(maxWidth: .infinity, alignment: .trailing)\n            .layoutPriority(-1)",
     ".layoutPriority(-1)"),
    ("reserved_room", _ROW, "if case .open? = MarketHoursUtil.liveSessionStatus() { return false }",
     "if case .preMarket? = MarketHoursUtil.liveSessionStatus() { return false }"),
    ("reserved_room", _ROW, "reservesExtendedMove ? AssetRow.extendedPriceColumnWidth : 0", "0"),
    ("tail_wire_parity", _MODELS, 'case extendedSpanTo = "extended_span_to"',
     'case extendedSpanTo = "extended_span_end"'),
    ("tail_wire_parity", _MODELS, "            extendedSparkline: extendedSparkline,\n", ""),
    ("tail_drawable", _MODELS, "share > 0, share < spanTo, spanTo <= 1", "share > 0, spanTo <= 1"),
    ("tail_drawable", _SPARK, "regularShare > 0 && regularShare < spanTo && spanTo <= 1",
     "regularShare > 0 && spanTo <= 1"),
    ("tail_gate", _ROW, "        guard Self.isOutsideRegularSession else { return nil }\n", ""),
    ("tail_gate", _ROW, "                        tail: afterHoursTail\n", "                        tail: nil\n"),
    ("tail_tone", _SPARK, "private static let tailTone = AppColors.textMuted",
     "private static let tailTone = AppColors.bullish"),
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
