//
//  DelistedListing.swift
//  ios
//
//  "Delisted": the label for a ticker the backend has CONFIRMED is no longer listed
//  (owner decisions 2026-11-13, D1-D4). The backend decides; this type only reads its four
//  wire keys and writes every line the app shows about it, so the Tracking row, the Home
//  tile, the detail screen, the share text and the Cay AI context all say the same thing.
//
//  The wire (all null unless a delisting is confirmed, then all set together):
//    listing_status     "delisted" | null   — the ONLY key that turns the label on
//    delisted_date      "yyyy-MM-dd"        — the day FMP lists it delisted (ET calendar)
//    delisted_exchange  "NASDAQ"            — the exchange it left
//    last_trade_date    "yyyy-MM-dd"        — its last traded session (the last close's day)
//
//  Fails OPEN: a missing, null or unknown status is "not delisted" — today's behaviour. The
//  three decorations are dropped when missing or unreadable, never guessed. The copy never
//  gives a reason (FMP does not give one, so "acquired" would be a guess) and never claims the
//  company stopped trading: an exchange move (NYSE → the Texas exchange, D4) reads
//  "Delisted from NYSE", which is true.
//
//  DATES NEVER SHIFT WITH THE PHONE'S ZONE: a "yyyy-MM-dd" is a calendar day, not an instant.
//  It is parsed and printed in the SAME pinned zone (America/New_York, the market's day) and
//  the Gregorian calendar both ways, so "2026-11-09" is "Nov 9, 2026" in Tokyo and in Samoa.
//  No phone zone, calendar or auto-updating locale is read here
//  (test_ios_market_times_eastern.py: "A date-only value never shifts by zone").
//
//  ⚠️ FOUNDATION ONLY — no SwiftUI import. There is no XCTest target, so
//  `backend/tests/test_ios_delisted_listing.py` pipes this file into `xcrun swift -` and runs
//  it under two extreme time zones. `nonisolated`: the target defaults to MainActor isolation,
//  and the DTO mappings that build it run off the main actor.
//

import Foundation

/// A confirmed delisting, as the backend sent it. Build one only through
/// `DelistedListing.from(status:delistedDate:exchange:lastTradeDate:)` from a wire payload.
nonisolated struct DelistedListing: Hashable, Sendable {

    /// The pill's word on the Tracking row, the Home tile, the detail header and the
    /// pinned nav-bar price.
    static let pillText = "Delisted"

    /// Preview / sample data: ROKU, delisted from NASDAQ on 2026-11-09, last traded 2026-11-06.
    static let sample = DelistedListing(delistedDay: "2026-11-09", exchange: "NASDAQ", lastTradeDay: "2026-11-06")

    /// A validated "yyyy-MM-dd", or nil.
    let delistedDay: String?
    /// Trimmed and non-empty, or nil.
    let exchange: String?
    /// A validated "yyyy-MM-dd", or nil.
    let lastTradeDay: String?

    /// Every field is cleaned here, so no caller can hold an unvalidated day.
    init(delistedDay: String?, exchange: String?, lastTradeDay: String?) {
        self.delistedDay = Self.validDay(delistedDay)
        self.exchange = Self.cleanExchange(exchange)
        self.lastTradeDay = Self.validDay(lastTradeDay)
    }

    // MARK: - The one wire rule

    /// THE one wire rule: nil unless `listing_status` is "delisted" (trimmed, any case).
    /// Every DTO mapping calls this, so the rule cannot drift between surfaces.
    static func from(status: String?, delistedDate: String?, exchange: String?,
                     lastTradeDate: String?) -> DelistedListing? {
        guard let status else { return nil }
        let normalized: String = status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard normalized == "delisted" else { return nil }
        return DelistedListing(delistedDay: delistedDate, exchange: exchange, lastTradeDay: lastTradeDate)
    }

    // MARK: - Days

    /// `raw` when it is EXACTLY a real "yyyy-MM-dd" day (10 characters, digits and two
    /// dashes, a date that exists), else nil. "2026-02-30", "11/09/2026", a timestamp and a
    /// padded string are all nil.
    static func validDay(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let bytes: [UInt8] = Array(raw.utf8)
        guard bytes.count == 10 else { return nil }
        for (index, byte) in bytes.enumerated() {
            if index == 4 || index == 7 {
                guard byte == UInt8(ascii: "-") else { return nil }
            } else {
                guard byte >= UInt8(ascii: "0"), byte <= UInt8(ascii: "9") else { return nil }
            }
        }
        // The round trip rejects a day the calendar does not have, whatever the parser's
        // leniency does with it.
        guard let date = parser.date(from: raw), parser.string(from: date) == raw else { return nil }
        return raw
    }

    /// "Nov 9, 2026" for "2026-11-09", printed in the same zone and calendar it was parsed
    /// in. `locale` nil = the phone's month names (the app's other date labels do the same);
    /// the tests pass "en_US". Nil for an unreadable day.
    static func dayLabel(_ ymd: String, locale: Locale? = nil) -> String? {
        guard let day = validDay(ymd), let date = parser.date(from: day) else { return nil }
        guard let locale else { return display.string(from: date) }
        return makeDisplayFormatter(locale: locale).string(from: date)
    }

    // MARK: - Copy (the phone's locale)

    /// "NASDAQ · Nov 9, 2026" | "NASDAQ" | "Nov 9, 2026" | nil.
    var exchangeAndDate: String? { exchangeAndDate(locale: nil) }
    /// The line under the detail header's pill: "from NASDAQ · Nov 9, 2026" | "from NASDAQ" |
    /// "Nov 9, 2026" | nil. Pill + line read as `headline`.
    var detailLine: String? { detailLine(locale: nil) }
    /// "Delisted from NASDAQ · Nov 9, 2026" | "Delisted from NASDAQ" | "Delisted · Nov 9, 2026" | "Delisted".
    var headline: String { headline(locale: nil) }
    /// VoiceOver: "Delisted from NASDAQ, Nov 9, 2026".
    var spokenHeadline: String { spokenHeadline(locale: nil) }
    /// "Last close · Nov 6, 2026" | "Last close" — the detail header's line under the price.
    var lastCloseLine: String { lastCloseLine(locale: nil) }

    // MARK: - Copy (an explicit locale; nil = the phone's)

    func exchangeAndDate(locale: Locale?) -> String? {
        let parts: [String] = [exchange, delistedLabel(locale: locale)].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    func detailLine(locale: Locale?) -> String? {
        let date: String? = delistedLabel(locale: locale)
        guard let exchange else { return date }
        guard let date else { return "from \(exchange)" }
        return "from \(exchange) · \(date)"
    }

    func headline(locale: Locale?) -> String {
        var text: String = Self.pillText
        if let exchange { text += " from \(exchange)" }
        if let date = delistedLabel(locale: locale) { text += " · \(date)" }
        return text
    }

    func spokenHeadline(locale: Locale?) -> String {
        var text: String = Self.pillText
        if let exchange { text += " from \(exchange)" }
        if let date = delistedLabel(locale: locale) { text += ", \(date)" }
        return text
    }

    func lastCloseLine(locale: Locale?) -> String {
        guard let date = lastTradeLabel(locale: locale) else { return "Last close" }
        return "Last close · \(date)"
    }

    /// The detail screen's notice:
    /// "ROKU was delisted from NASDAQ on Nov 9, 2026. The price shown is its last close, from
    /// Nov 6, 2026, and no longer updates." A missing field drops its clause; never a reason.
    func notice(symbol: String, locale: Locale? = nil) -> String {
        let trimmed: String = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        var first: String = trimmed.isEmpty ? "This stock was delisted" : "\(trimmed) was delisted"
        if let exchange { first += " from \(exchange)" }
        if let date = delistedLabel(locale: locale) { first += " on \(date)" }
        first += "."
        var second: String = "The price shown is its last close"
        if let date = lastTradeLabel(locale: locale) { second += ", from \(date)," }
        second += " and no longer updates."
        return "\(first) \(second)"
    }

    /// The share text's price line, in place of "$160.52 +1.20 (+0.75%)":
    /// "Delisted from NASDAQ · Nov 9, 2026\nLast close $160.52 (Nov 6, 2026)".
    func shareLine(lastClose: String, locale: Locale? = nil) -> String {
        var close: String = "Last close \(lastClose)"
        if let date = lastTradeLabel(locale: locale) { close += " (\(date))" }
        return "\(headline(locale: locale))\n\(close)"
    }

    /// The Cay AI context lines, in place of "Price: $160.52 …", so the assistant never
    /// reads the last close as a live quote.
    func aiContextLines(lastClose: String, locale: Locale? = nil) -> [String] {
        var status: String = "Listing status: delisted"
        if let exchange { status += " from \(exchange)" }
        if let date = delistedLabel(locale: locale) { status += " on \(date)" }
        status += "; there is no live quote."
        var close: String = "Last close: \(lastClose)"
        if let date = lastTradeLabel(locale: locale) { close += " on \(date)" }
        close += "."
        return [status, close]
    }

    // MARK: - Private

    private func delistedLabel(locale: Locale?) -> String? {
        guard let delistedDay else { return nil }
        return Self.dayLabel(delistedDay, locale: locale)
    }

    private func lastTradeLabel(locale: Locale?) -> String? {
        guard let lastTradeDay else { return nil }
        return Self.dayLabel(lastTradeDay, locale: locale)
    }

    /// Trimmed; nil when blank or implausibly long for an exchange name (a long string is a
    /// payload problem, not a label).
    private static func cleanExchange(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let trimmed: String = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 40 else { return nil }
        return trimmed
    }

    /// The market's zone. Parse and display share it, which is what keeps a day from moving;
    /// the fallback is only reachable if the zone database lost New York, and it is shared too.
    private static let marketZone: TimeZone = TimeZone(identifier: "America/New_York") ?? .gmt

    /// PARSING: fixed pattern, so `en_US_POSIX` + Gregorian (an unpinned formatter reads the
    /// pattern in the device's calendar), not lenient.
    private static let parser: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = marketZone
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.isLenient = false
        return formatter
    }()

    /// DISPLAY: the phone's month names, Gregorian, the SAME zone as `parser`. Built once.
    private static let display: DateFormatter = makeDisplayFormatter(locale: nil)

    private static func makeDisplayFormatter(locale: Locale?) -> DateFormatter {
        let formatter = DateFormatter()
        if let locale { formatter.locale = locale }
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = marketZone
        formatter.setLocalizedDateFormatFromTemplate("MMMdyyyy")
        return formatter
    }
}
