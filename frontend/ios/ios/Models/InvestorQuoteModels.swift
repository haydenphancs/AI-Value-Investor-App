//
//  InvestorQuoteModels.swift
//  ios
//
//  An attributed investor quotation, plus the bundled set of 52 that the brand cover
//  (`CaydexSloganView`) rotates through one per ISO week.
//
//  BUNDLE-ONLY by product decision (2026-09-23): the list ships in
//  `Resources/InvestorQuotes/weekly_investor_quotes.json`, is not seeded to Supabase and is
//  not served by the backend, so changing a quote needs an app update. Every entry carries a
//  verifiable PRIMARY source — attribution accuracy is the whole point on a finance app, and
//  `backend/tests/test_ios_weekly_investor_quotes.py` pins the file's contract.
//
//  `InvestorQuote` moved here from `InvestorPathModels.swift`; the Journey card's
//  `.buffettQuote` extension stays there and is untouched by the weekly rotation.
//

import Foundation
import OSLog

// MARK: - UI model

struct InvestorQuote {
    let text: String
    let author: String
    /// Work title only ("The Intelligent Investor"); the year lives in `year`.
    /// Defaults keep `InvestorQuote(text:author:)` — `.buffettQuote` — compiling.
    var source: String? = nil
    var year: Int? = nil

    /// "The Intelligent Investor · 1973" — nil when the quote carries no source. A middle
    /// dot, not a comma: some titles end in their own full stop ("You Can’t Predict. You Can
    /// Prepare."), and ", 2001" after it rendered as "Prepare., 2001" on the cover.
    var citation: String? {
        guard let source, !source.isEmpty else { return nil }
        return year.map { "\(source) · \($0)" } ?? source
    }

    /// One VoiceOver element for the whole attribution block.
    var accessibilityLabel: String {
        "Quote: \(text) — \(author)" + (citation.map { ", \($0)" } ?? "")
    }
}

// MARK: - Bundled JSON (DTOs)

/// One entry of `weekly_investor_quotes.json`. `source`/`year` are optional so a missing
/// citation degrades ONE entry to "author only" instead of failing the whole decode.
/// (`locator` and `url` in the file are reviewer aids and are deliberately not decoded.)
struct WeeklyInvestorQuoteDTO: Decodable {
    let week: Int
    let text: String
    let author: String
    let source: String?
    let year: Int?
}

struct WeeklyInvestorQuotesFile: Decodable {
    let version: Int
    let quotes: [WeeklyInvestorQuoteDTO]
}

// MARK: - Loader

enum BundledInvestorQuotes {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "investor-quotes")

    /// Decoded once, on first use (the first time a brand cover opens).
    static let all: [InvestorQuote] = load()

    /// This week's quote for the brand cover, or nil when the bundle has none (the cover then
    /// shows the slogan alone, exactly as before this feature).
    static func quoteOfTheWeek(now: Date = Date(),
                               timeZone: TimeZone = .autoupdatingCurrent) -> InvestorQuote? {
        #if DEBUG
        // The Simulator cannot change its date, so this is the only way to see ISO week 53 or
        // the longest quote before they come round: `SIMCTL_CHILD_CAYDEX_QUOTE_WEEK=53`.
        // `off` gives a clean brand cover for App Store capture (a real investor's name must
        // not appear in store screenshots — documents/legal/app-store-listing.md).
        if let raw = ProcessInfo.processInfo.environment["CAYDEX_QUOTE_WEEK"] {
            if raw == "off" { return nil }
            if let week = Int(raw), !all.isEmpty {
                return all[WeeklyQuotePicker.slot(forISOWeek: week) % all.count]
            }
        }
        #endif
        return WeeklyQuotePicker.pick(all, on: now, timeZone: timeZone)
    }

    /// Fails LOUDLY (error log + DEBUG assertion) and degrades to what is usable — never to a
    /// crash, and never silently: an empty rotation would otherwise look like a layout choice.
    static func load(from bundle: Bundle = .main) -> [InvestorQuote] {
        guard let url = bundle.url(forResource: "weekly_investor_quotes", withExtension: "json") else {
            log.error("weekly_investor_quotes.json missing from the bundle — the brand cover shows the slogan only")
            assertionFailure("weekly_investor_quotes.json missing from the bundle")
            return []
        }

        let file: WeeklyInvestorQuotesFile
        do {
            file = try JSONDecoder().decode(WeeklyInvestorQuotesFile.self, from: Data(contentsOf: url))
        } catch {
            log.error("weekly_investor_quotes.json unreadable (\(String(describing: type(of: error)), privacy: .public)): \(String(describing: error), privacy: .public)")
            assertionFailure("weekly_investor_quotes.json unreadable: \(error)")
            return []
        }

        var usable: [InvestorQuote] = []
        var weeks: [Int] = []
        for entry in file.quotes.sorted(by: { $0.week < $1.week }) {
            let text = entry.text.trimmingCharacters(in: .whitespacesAndNewlines)
            let author = entry.author.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty, !author.isEmpty else {
                log.warning("weekly quote for week \(entry.week, privacy: .public) has an empty text or author — skipped")
                continue
            }
            let source = entry.source?.trimmingCharacters(in: .whitespacesAndNewlines)
            usable.append(InvestorQuote(text: text, author: author,
                                        source: (source?.isEmpty ?? true) ? nil : source,
                                        year: entry.year))
            weeks.append(entry.week)
        }

        let expected = Array(1...WeeklyQuotePicker.weeksPerCycle)
        if weeks != expected {
            // Content bug, not a runtime condition: release builds still rotate (`% count`).
            log.error("weekly_investor_quotes.json has \(usable.count, privacy: .public) usable entries for weeks \(weeks, privacy: .public) — expected exactly weeks 1…\(WeeklyQuotePicker.weeksPerCycle, privacy: .public)")
            assertionFailure("weekly_investor_quotes.json must hold exactly weeks 1…\(WeeklyQuotePicker.weeksPerCycle)")
        }
        return usable
    }
}
