//
//  CreditHistoryRowFormat.swift
//  ios
//
//  The pure half of the compact Credit History row: where a row sits inside its day's group,
//  and how its secondary text is composed.
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`. There is no XCTest target, so the only way to
//  EXECUTE this logic is to pipe this file into `xcrun swift -` from pytest
//  (`backend/tests/test_ios_credit_history_compact.py`, the mechanism `WeeklyQuotePicker`
//  uses). The SwiftUI side (corner radii, drawing) lives in `CreditHistoryRow`.
//
//  WHY (developer request, 2026-09-24): each movement was a full `ActivityRow` card — a 40pt
//  glyph circle, three lines, 16pt padding — about 100pt a row, ~7 to a screen, on the one
//  screen a heavy user scrolls the most. Rows are now two tight lines inside one rounded
//  group per day. The group cannot be a container view: the screen's rows must stay DIRECT
//  children of its one `LazyVStack` (a lazy child that resizes in place is the documented
//  100%-CPU hang), so every row draws its own segment of the group, and needs to know which.
//

import Foundation

/// Where a row sits within its day's group — decides which corners round, and whether a
/// divider and a shared edge sit above or below it.
enum RowGroupPosition: Equatable {
    case only
    case first
    case middle
    case last

    /// Defensive: a count of 0 or 1, or an index outside `0..<count`, is `.only` — a row that
    /// draws a complete card of its own is always a safe picture, a half-open one is not.
    init(index: Int, count: Int) {
        guard count > 1, index >= 0, index < count else {
            self = .only
            return
        }
        if index == 0 { self = .first }
        else if index == count - 1 { self = .last }
        else { self = .middle }
    }

    var hasRowAbove: Bool { self == .middle || self == .last }
    var hasRowBelow: Bool { self == .first || self == .middle }
}

enum CreditHistoryRowFormat {

    /// The part after "Title · " — usually a ticker. Nil when there is nothing to show, so the
    /// title never trails a dangling " · ".
    static func detail(_ subtitle: String?) -> String? {
        guard let trimmed = subtitle?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }

    /// The small second line: `time · Refunded · pool note`, skipping blank parts.
    ///
    /// Time FIRST because it is the one part every row has; "Refunded" before the pool split
    /// because it changes what the amount means. Nil when all three are empty, and the row
    /// then renders a single line.
    static func metaLine(time: String, poolNote: String?, isReversed: Bool) -> String? {
        var parts: [String] = []
        let time = time.trimmingCharacters(in: .whitespacesAndNewlines)
        if !time.isEmpty { parts.append(time) }
        if isReversed { parts.append("Refunded") }
        if let note = poolNote?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty {
            parts.append(note)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
