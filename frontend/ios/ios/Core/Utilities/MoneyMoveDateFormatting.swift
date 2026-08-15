//
//  MoneyMoveDateFormatting.swift
//  ios
//
//  The publication-date label on a Money Move card: "Today" / "Yesterday" / "Wednesday" /
//  "Aug 3" / "Aug 3, 2025".
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`, and `now`/`calendar` are INJECTED rather than
//  read from the ambient environment. That is not stylistic. There is no XCTest target in
//  this project, so the only way to actually execute branching Swift logic in CI is to pipe
//  this file into `xcrun swift -` from pytest (`backend/tests/test_money_moves_date_label.py`).
//  A SwiftUI import makes the file unrunnable there, and a hardcoded `Date()` makes every
//  case except "Today" untestable. Keep both properties.
//
//  Why a new formatter at all: nine one-off `RelativeDateTimeFormatter` copies exist across
//  the models, and not one of them produces a weekday name — which is the whole middle of
//  this ladder.
//

import Foundation

enum MoneyMoveDateFormatting {

    /// `.full` is the default; `.short` is the compact fallback `ViewThatFits` drops to when
    /// the meta row would otherwise wrap at large Dynamic Type sizes.
    /// Only the WEEKDAY differs between the two. A two-digit year was tried and dropped: a
    /// localized template can only produce "Aug 3, 25" (a hardcoded `'25` would break
    /// localization), which reads as the 25th. The weekday is where the width actually is —
    /// "Wednesday" is more than twice "Wed" — and the cross-year absolute date is the rarest
    /// branch, so it stays unabbreviated and unambiguous in both styles.
    enum Style {
        case full   // "Wednesday" · "Aug 3, 2025"
        case short  // "Wed"       · "Aug 3, 2025"
    }

    // MARK: - Public

    /// A human label for a publication date, or `nil` when there is no date worth showing.
    ///
    /// Returning `nil` is a real branch, not a defensive stub: `MoneyMove.createdAt` defaults
    /// to `.distantPast`, and **seven** placeholder cards ship with exactly that today (the
    /// "coming soon" teasers in `MoneyMove.sampleData` that the bundle has not superseded).
    /// Formatted naively those render "Jan 1, 1".
    static func label(for date: Date,
                      style: Style = .full,
                      now: Date = Date(),
                      calendar: Calendar = .current) -> String? {
        guard isRenderable(date) else { return nil }

        let startOfDate = calendar.startOfDay(for: date)
        let startOfNow = calendar.startOfDay(for: now)

        // Whole CALENDAR days apart, never `timeIntervalSince / 86400`: a DST day is 23 or 25
        // hours, so the arithmetic version reports 2-days-ago as 1 twice a year.
        guard let days = calendar.dateComponents([.day], from: startOfDate, to: startOfNow).day
        else { return nil }

        // Future dates clamp to "Today". A device clock behind the server (or a seed stamped
        // slightly ahead) otherwise falls straight through to the absolute branch and prints a
        // date that has not happened yet.
        if days <= 0 { return Strings.today }
        if days == 1 { return Strings.yesterday }

        // 2...6 only. SEVEN is deliberately excluded: a weekday name exactly one week back is
        // the same word as today's, so "Wednesday" would read as today.
        if days < 7 {
            return style == .full
                ? fullWeekday.string(from: date)
                : shortWeekday.string(from: date)
        }

        // Same CALENDAR year, not "within 365 days" — on Jan 5 the previous Dec 31 is a
        // different year and must carry it, while Jan 2 is still inside the weekday branch.
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
        if sameYear { return monthDay.string(from: date) }
        return monthDayYear.string(from: date)
    }

    /// Parses the ISO-8601 the backend serves for `publishedAt`.
    ///
    /// The service normalizes to whole seconds (`"2026-06-12T14:03:21Z"`) precisely so this
    /// stays a single-format parse — PostgREST's native rendering carries up to six fractional
    /// digits and `ISO8601DateFormatter.withFractionalSeconds` accepts exactly three. The
    /// fractional formatter is kept as a second attempt anyway, so a value that reaches us
    /// un-normalized (an older backend, a hand-written row) still resolves instead of silently
    /// dropping the article back onto its drifting estimate.
    static func parseISO8601(_ raw: String) -> Date? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return plainISO.date(from: trimmed) ?? fractionalISO.date(from: trimmed)
    }

    /// `.distantPast` is the "we have no date" sentinel on `MoneyMove.createdAt`. Compared with
    /// a wide margin rather than for equality because the value round-trips through
    /// `Calendar.date(byAdding:)` on the fallback path, which does not preserve it exactly.
    static func isRenderable(_ date: Date) -> Bool {
        date.timeIntervalSince1970 > Self.earliestPlausible
    }

    // MARK: - Internals

    /// 1990-01-01. Any Money Moves publication date is far later; `.distantPast` and any
    /// arithmetic near it are far earlier.
    private static let earliestPlausible: TimeInterval = 631_152_000

    private enum Strings {
        static let today = NSLocalizedString("Today", comment: "Publication date, same day")
        static let yesterday = NSLocalizedString("Yesterday", comment: "Publication date, previous day")
    }

    // Cached: constructing a DateFormatter per access is a known cost in this codebase, and
    // these are read once per card in a horizontal scroll row.
    //
    // Localized TEMPLATES, not hardcoded `dateFormat` strings — this is user-facing text, and
    // `setLocalizedDateFormatFromTemplate` reorders for the locale ("3 Aug" in en_GB). The
    // `en_US_POSIX` convention used elsewhere in the app is for PARSING fixed machine formats,
    // which is the opposite job; it is used below for the ISO parsers only.
    private static let fullWeekday = localized("EEEE")
    private static let shortWeekday = localized("EEE")
    private static let monthDay = localized("MMMd")
    private static let monthDayYear = localized("MMMdyyyy")

    private static func localized(_ template: String) -> DateFormatter {
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate(template)
        return f
    }

    private static let plainISO: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let fractionalISO: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}
