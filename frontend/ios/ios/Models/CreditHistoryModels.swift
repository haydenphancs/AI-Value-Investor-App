//
//  CreditHistoryModels.swift
//  ios
//
//  DTOs for the credit statement behind the Account balance — every spend, refund,
//  grant and purchase, with what each one was for.
//
//  Co-located per the model-file convention: the `Codable` DTOs and the UI-facing
//  helpers live in ONE file per feature.
//
//  ⚠️ Every DTO field below is decoded from a backend response. A field the backend
//  renames — or makes nullable — without a matching change here is a DECODE CRASH in
//  production, on a MONEY screen. `backend/tests/test_credit_history_schema_parity.py`
//  is the guard rail; keep it updated in the same change as any shape edit.
//
//  ⚠️ The user-facing COPY (`title`, `subtitle`, `poolNote`) is authored by the backend
//  and rendered verbatim. Do NOT reintroduce a `reason`-to-label switch here: `reason` is
//  unconstrained text, one family of values is composed at runtime, and a Swift-side map
//  would render a blank row on every already-shipped build the moment a new reason
//  appeared. `reason` is carried for diagnostics only.
//

import Foundation
import SwiftUI

// MARK: - One movement in the ledger

struct CreditTransactionDTO: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let createdAt: String?
    /// Signed, exactly as stored. Negative = credits left the account.
    let delta: Int
    /// `spend` | `refund` | `grant` | `purchase` | `revoke` | `other`.
    let kind: String
    /// What it was for, in the user's language. Backend-authored.
    let title: String
    /// The specific thing, when the ledger can name one — usually a ticker.
    let subtitle: String?
    /// Set only when purchased credits moved. Nil is the common, correct case.
    let poolNote: String?
    /// This debit was later reversed by a refund row.
    let isReversed: Bool
    /// Raw ledger reason. Diagnostics only — never rendered.
    let reason: String

    enum CodingKeys: String, CodingKey {
        case id, delta, kind, title, subtitle, reason
        case createdAt = "created_at"
        case poolNote = "pool_note"
        case isReversed = "is_reversed"
    }

    /// Every field except `id` is `decodeIfPresent`-defaulted.
    ///
    /// Same contract as `NotificationEventDTO`: a backend that adds a column, or answers
    /// an older shape during a partial deploy, must not crash an already-shipped build.
    /// `id` alone is required — a row without one cannot be rendered or diffed anyway.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        delta = try c.decodeIfPresent(Int.self, forKey: .delta) ?? 0
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "other"
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Credit adjustment"
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle)
        poolNote = try c.decodeIfPresent(String.self, forKey: .poolNote)
        isReversed = try c.decodeIfPresent(Bool.self, forKey: .isReversed) ?? false
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
    }

    /// Memberwise init for previews and tests (the decoder above suppresses the synthesized one).
    init(
        id: String,
        createdAt: String? = nil,
        delta: Int = 0,
        kind: String = "other",
        title: String = "Credit adjustment",
        subtitle: String? = nil,
        poolNote: String? = nil,
        isReversed: Bool = false,
        reason: String = ""
    ) {
        self.id = id
        self.createdAt = createdAt
        self.delta = delta
        self.kind = kind
        self.title = title
        self.subtitle = subtitle
        self.poolNote = poolNote
        self.isReversed = isReversed
        self.reason = reason
    }

    // MARK: - Amount

    /// `"+540"` / `"−20"` / `"0"`.
    ///
    /// The sign is ALWAYS explicit so direction survives without colour (WCAG 1.4.1) —
    /// the same reason `PriceChangeLabel` prints a leading `+`.
    var amountText: String {
        if delta == 0 { return "0" }
        return delta < 0 ? "−\(abs(delta))" : "+\(abs(delta))"
    }

    /// Spelled out, because the display string uses a MINUS SIGN (U+2212) rather than a
    /// hyphen and VoiceOver should not have to guess at it.
    var accessibilityAmount: String {
        let magnitude = abs(delta)
        let unit = magnitude == 1 ? "credit" : "credits"
        if delta == 0 { return "no change" }
        return delta < 0 ? "minus \(magnitude) \(unit)" : "plus \(magnitude) \(unit)"
    }

    /// A debit that was refunded nets to zero, so shouting it in red would overstate it.
    /// It stays in the list — the statement must reconcile — but reads as settled.
    var amountColor: Color {
        if isReversed { return AppColors.textMuted }
        if delta > 0 { return AppColors.gain }
        if delta < 0 { return AppColors.loss }
        return AppColors.textMuted
    }

    // MARK: - Visual vocabulary
    //
    // Keyed off `kind`, which is a closed vocabulary the backend owns, NOT off `title` or
    // `reason`. An unrecognised kind lands on the neutral default rather than a gap.

    var iconName: String {
        switch kind {
        case "spend":    return "minus.circle.fill"
        case "refund":   return "arrow.uturn.backward.circle.fill"
        case "grant":    return "gift.fill"
        case "purchase": return "creditcard.fill"
        case "revoke":   return "xmark.circle.fill"
        default:         return "circle.dashed"
        }
    }

    /// ⚠️ TEXT-role tokens only. This glyph carries the row's category — it is meaningful
    /// content, not chart furniture — so it needs the 4.5:1 bar, never a `*Graphic` token.
    /// See the role table in `.claude/rules/ios-swiftui.md`.
    ///
    /// Deliberately CATEGORICAL rather than directional: the amount beside it already
    /// carries direction, and tinting every "Ask Cay AI" row red would make ordinary use
    /// of the product read as a warning.
    var iconColor: Color {
        switch kind {
        case "refund", "grant", "purchase": return AppColors.gain
        case "revoke":                      return AppColors.caution
        case "spend":                       return AppColors.primaryBlue
        default:                            return AppColors.textSecondary
        }
    }

    /// Small print under the row: the pool split, plus the settled marker when the debit
    /// was refunded. Nil when neither applies, which is the common case.
    var footnote: String? {
        var parts: [String] = []
        if isReversed { parts.append("Refunded") }
        if let poolNote, !poolNote.isEmpty { parts.append(poolNote) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// `subtitle` is often nil (a plain chat turn names nothing), and `ActivityRow`
    /// requires a non-optional subtitle. The time reads better there than an empty line.
    var rowSubtitle: String {
        if let subtitle, !subtitle.isEmpty { return subtitle }
        return timeOfDay
    }

    // MARK: - Time

    var date: Date? { Self.parseISO(createdAt) }

    /// `"2:41 PM"`. Empty when the timestamp is unparseable — the row still renders.
    var timeOfDay: String {
        guard let date else { return "" }
        return Self.timeFormatter.string(from: date)
    }

    /// Two parsers, matching `NotificationEventDTO`.
    ///
    /// Postgres emits `timestamptz` with fractional seconds sometimes and not others, and
    /// `ISO8601DateFormatter` is strict about which it was configured for — a single
    /// formatter silently returns nil for the other shape, which would collapse every row
    /// into one undated group.
    ///
    /// MEASURED against what production actually returns (2026-08-24): PostgREST emits
    /// 5- and 6-digit fractions (`…T18:13:39.271827+00:00`) and `.withFractionalSeconds`
    /// parses all of them, plus `Z` and `+00:00` offsets. A third fraction-trimming pass
    /// was written on the assumption that it did not, and removed once the probe disproved
    /// it — the only shape neither parser accepts is a fraction with NO timezone, which
    /// `timestamptz` never produces and which is genuinely ambiguous anyway.
    static func parseISO(_ raw: String?) -> Date? {
        guard let raw, !raw.isEmpty else { return nil }
        if let date = isoFractional.date(from: raw) { return date }
        return isoPlain.date(from: raw)
    }

    private static let isoFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoPlain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .none
        f.timeStyle = .short
        return f
    }()
}

// MARK: - One page

struct CreditHistoryDTO: Decodable, Sendable {
    let items: [CreditTransactionDTO]
    /// Keyset cursor for the next page — the `id` of the last row. `nil` = no more.
    let nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case items
        case nextCursor = "next_cursor"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = try c.decodeIfPresent([CreditTransactionDTO].self, forKey: .items) ?? []
        nextCursor = try c.decodeIfPresent(String.self, forKey: .nextCursor)
    }

    init(items: [CreditTransactionDTO], nextCursor: String? = nil) {
        self.items = items
        self.nextCursor = nextCursor
    }
}

// MARK: - Day grouping

/// A day's worth of movements, newest day first.
///
/// Grouping is by the DEVICE's calendar day, matching how the user remembers "yesterday".
struct CreditHistoryDay: Identifiable, Sendable {
    let id: String
    let label: String
    let items: [CreditTransactionDTO]

    /// Groups a newest-first list, preserving order within each day.
    ///
    /// Rows whose timestamp will not parse are kept in a trailing "Earlier" group rather
    /// than dropped — a statement that silently omits a movement is worse than one with a
    /// vaguely-labelled section.
    static func group(_ items: [CreditTransactionDTO], now: Date = Date()) -> [CreditHistoryDay] {
        let calendar = Calendar.current
        var order: [String] = []
        var buckets: [String: [CreditTransactionDTO]] = [:]

        for item in items {
            let key: String
            if let date = item.date {
                let day = calendar.startOfDay(for: date)
                key = ISO8601DateFormatter.string(
                    from: day, timeZone: .current, formatOptions: [.withFullDate]
                )
            } else {
                key = "unknown"
            }
            if buckets[key] == nil { order.append(key) }
            buckets[key, default: []].append(item)
        }

        return order.map { key in
            let rows = buckets[key] ?? []
            return CreditHistoryDay(id: key, label: label(for: rows.first?.date, calendar: calendar, now: now), items: rows)
        }
    }

    private static func label(for date: Date?, calendar: Calendar, now: Date) -> String {
        guard let date else { return "Earlier" }
        if calendar.isDateInToday(date) { return "Today" }
        if calendar.isDateInYesterday(date) { return "Yesterday" }
        // Drop the year only within the current one, so an old row is never ambiguous.
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
        return (sameYear ? dayFormatter : dayYearFormatter).string(from: date)
    }

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("MMMd")
        return f
    }()

    private static let dayYearFormatter: DateFormatter = {
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("MMMdyyyy")
        return f
    }()
}
