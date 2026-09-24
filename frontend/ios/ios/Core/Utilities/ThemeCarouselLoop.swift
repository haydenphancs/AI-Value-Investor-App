//
//  ThemeCarouselLoop.swift
//  ios
//
//  The arithmetic behind the endless "Emerging Frontiers" carousel (`TrendingThemesSection`).
//
//  The strip is K identical copies of the theme columns laid side by side. The reader starts
//  in the MIDDLE copy; whenever scrolling comes fully to rest in an outer copy, the section
//  jumps — unanimated — to the same column of the middle copy. Every copy draws the same
//  pixels, so the jump cannot be seen, and there is always a buffer of columns on both sides:
//  a swipe never meets an end.
//
//  CoreGraphics only — no SwiftUI or UIKit import — so
//  `backend/tests/test_ios_themes_carousel_guards.py` can EXECUTE it with `xcrun swift -`
//  (there is no XCTest target).
//

import CoreGraphics

nonisolated enum ThemeCarouselLoop {
    /// Four themes or fewer fill at most two columns — one screen — so there is nothing to
    /// loop through, and a copy would put the same theme on screen twice.
    static let minimumThemesToLoop = 5
    /// Columns of runway kept on EACH side of the middle copy. One swipe moves at most about
    /// a screen (two columns), so this survives ~4 back-to-back flings before the reader
    /// could reach an end — and any pause re-centres.
    static let bufferColumnsPerSide = 8

    struct Layout: Equatable {
        let themeCount: Int
        let loops: Bool
        /// Themes in one repeating period. An odd count is played TWICE when looping, so
        /// every column is a full pair and no half-empty column sits mid-strip.
        let periodLength: Int
        /// Columns in one period (two tiles per column).
        let unit: Int
        /// Identical copies of the period laid side by side (1 when not looping).
        let copies: Int

        var middle: Int { copies / 2 }
        var totalColumns: Int { unit * copies }
        var firstMiddleColumn: Int { middle * unit }
    }

    static func layout(themeCount: Int, voiceOverEnabled: Bool) -> Layout {
        let count = max(themeCount, 0)
        // VoiceOver reads a list, not a picture: an endless strip would make the rotor walk
        // the same themes forever, so VoiceOver gets the plain static row.
        let loops = count >= minimumThemesToLoop && !voiceOverEnabled
        let period = (loops && count % 2 == 1) ? count * 2 : count
        let unit = (period + 1) / 2
        let copies: Int
        if loops, unit > 0 {
            let perSide = Int((Double(bufferColumnsPerSide) / Double(unit)).rounded(.up))
            copies = max(3, 2 * perSide + 1)
        } else {
            copies = 1
        }
        return Layout(themeCount: count, loops: loops, periodLength: period,
                      unit: unit, copies: copies)
    }

    /// Indices into the theme list shown in global column `g` (one or two of them).
    static func themeIndices(inColumn g: Int, layout: Layout) -> [Int] {
        guard layout.unit > 0, layout.themeCount > 0, layout.periodLength > 0 else { return [] }
        let column = ((g % layout.unit) + layout.unit) % layout.unit
        let start = column * 2
        let end = min(start + 2, layout.periodLength)
        guard start < end else { return [] }
        return (start..<end).map { $0 % layout.themeCount }
    }

    /// The column the strip is resting on, or nil when the offset is not on a column
    /// boundary (still settling, or at a clamped end).
    ///
    /// `offsetX + leadingInset` is the content x at the leading content margin; a column
    /// `g` rests there at `g · pitch`, where `pitch` is one column plus one gap.
    static func restingColumn(offsetX: CGFloat, leadingInset: CGFloat, contentWidth: CGFloat,
                              spacing: CGFloat, totalColumns: Int,
                              tolerance: CGFloat = 1) -> Int? {
        guard totalColumns > 0, offsetX.isFinite, leadingInset.isFinite,
              contentWidth.isFinite, contentWidth > 0,
              spacing.isFinite, spacing >= 0 else { return nil }
        let pitch = (contentWidth + spacing) / CGFloat(totalColumns)
        guard pitch.isFinite, pitch > 0 else { return nil }
        let x = offsetX + leadingInset
        let g = Int((x / pitch).rounded())
        guard g >= 0, g < totalColumns, abs(x - CGFloat(g) * pitch) < tolerance else { return nil }
        return g
    }

    /// Where to jump from a resting column `g`: the same column of the middle copy, or nil
    /// when already in the middle copy (or not looping).
    static func recentreTarget(from g: Int, layout: Layout) -> Int? {
        guard layout.loops, layout.unit > 0, g >= 0, g < layout.totalColumns else { return nil }
        guard g / layout.unit != layout.middle else { return nil }
        return layout.firstMiddleColumn + g % layout.unit
    }

    /// Where to park the strip after it is first laid out or its layout changes — the middle
    /// copy, on the same column (`phase`) the reader last rested on.
    static func parkingColumn(phase: Int, layout: Layout) -> Int {
        guard layout.unit > 0 else { return 0 }
        let column = ((phase % layout.unit) + layout.unit) % layout.unit
        return layout.firstMiddleColumn + column
    }

    /// Whether global column `g` is exposed to assistive tech (Voice Control, Full Keyboard
    /// Access, Switch Control). Exactly ONE period is exposed — every theme once — starting
    /// at the column the reader rests on, so both columns on screen are always reachable.
    /// Exposing the middle COPY instead hid the second visible column whenever the reader
    /// rested on a copy's last column (n = 8 at phase 3: themes 0 and 1 on screen, unnamed).
    static func isExposed(_ g: Int, restingPhase: Int, layout: Layout) -> Bool {
        guard layout.loops, layout.unit > 0 else { return true }
        let lead = parkingColumn(phase: restingPhase, layout: layout)
        return g >= lead && g < lead + layout.unit
    }
}
