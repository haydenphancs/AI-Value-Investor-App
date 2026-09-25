//
//  TrillionClubModels.swift
//  ios
//
//  Home › "Trillion-Dollar Club Bets": what the companies valued at $1 trillion or more own
//  in other companies. Wire DTOs for `HomeDashboardResponse.trillion_club` and
//  `GET /api/v1/home/trillion-club/{slug}` (backend `app/schemas/trillion_club.py`), the
//  presentation models the views render, and every line of copy those views show.
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`. There is no XCTest target, so
//  `backend/tests/test_trillion_club_schema_parity.py` EXECUTES this file with `xcrun swift -`:
//  it decodes malformed payloads through these DTOs, maps them, and checks the strings.
//  Colours are chosen in the views, never here.
//
//  WIRE RULES
//   • Every DTO field is Optional and decoded ON ITS OWN (`ClubDecode.field`): a missing key,
//     a null or a wrong type yields nil for that one field. An array element that cannot be
//     decoded is dropped and logged — it never fails its siblings, and the whole group never
//     fails the Home dashboard it rides on (`TrillionClubGroupDTO.init(from:)` cannot throw).
//   • An unknown enum string maps to `.unknown` and is HIDDEN — a new server value never
//     renders as a blank chip or a guessed label.
//   • Dates are date-only ISO strings, printed "MMM d, yyyy" in UTC terms: the calendar date
//     is taken verbatim, so no time zone can move it a day.
//
//  COPY RULES (plan §Copy; pinned by backend/tests/test_ios_trillion_club_guards.py): a
//  holding that first appears is "Newly reported" — most were IPO or merger conversions — and
//  a value is "carried at", "invested" or "committed up to". Nothing here reads as advice.
//

import Foundation
import os

// MARK: - Logging

nonisolated enum TrillionClubLog {
    static let logger = Logger(subsystem: "com.phan.caydex", category: "trillion-club")
}

// MARK: - Tolerant decoding

/// Field-by-field decoding. `nonisolated` because the target defaults to MainActor and the
/// DTOs decode inside `APIClient`'s actor.
nonisolated enum ClubDecode {
    /// One field: absent, null or the wrong type → nil (a wrong type is logged).
    static func field<T: Decodable, K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) -> T? {
        do {
            return try c.decodeIfPresent(T.self, forKey: key)
        } catch {
            TrillionClubLog.logger.warning(
                "trillion club decode: field \(key.stringValue, privacy: .public) unreadable (\(String(describing: type(of: error)), privacy: .public)) — treated as absent")
            return nil
        }
    }

    /// An array whose unreadable ELEMENTS are dropped, one by one, instead of failing the
    /// array. A value that is not an array at all → nil.
    static func list<T: Decodable, K: CodingKey>(_ c: KeyedDecodingContainer<K>, _ key: K) -> [T]? {
        let raw: [ClubFailable<T>]?
        do {
            raw = try c.decodeIfPresent([ClubFailable<T>].self, forKey: key)
        } catch {
            TrillionClubLog.logger.warning(
                "trillion club decode: list \(key.stringValue, privacy: .public) is not an array (\(String(describing: type(of: error)), privacy: .public)) — treated as absent")
            return nil
        }
        guard let raw else { return nil }
        let kept = raw.compactMap(\.value)
        if kept.count != raw.count {
            TrillionClubLog.logger.warning(
                "trillion club decode: dropped \(raw.count - kept.count) of \(raw.count) unreadable \(key.stringValue, privacy: .public) rows")
        }
        return kept
    }
}

/// Decodes one element or yields nil — the element is consumed either way, so the next one
/// still decodes. Counted and logged by `ClubDecode.list`.
nonisolated private struct ClubFailable<Wrapped: Decodable>: Decodable {
    let value: Wrapped?

    init(from decoder: Decoder) throws {
        value = try? Wrapped(from: decoder)
    }
}

// MARK: - Wire DTOs (mirror backend/app/schemas/trillion_club.py EXACTLY)
//
// Explicit snake_case `CodingKeys`: `APIClient` does not convert case. Pinned field-for-field,
// type-for-type by backend/tests/test_trillion_club_schema_parity.py.

/// `ClubChangeCountsResponse` — holdings per share-change outcome vs the previous quarter.
nonisolated struct ClubChangeCountsDTO: Codable, Sendable {
    let newlyReported: Int?
    let increased: Int?
    let decreased: Int?
    let noLongerReported: Int?
    let unchanged: Int?
    let corporateAction: Int?

    enum CodingKeys: String, CodingKey {
        case newlyReported = "newly_reported"
        case increased
        case decreased
        case noLongerReported = "no_longer_reported"
        case unchanged
        case corporateAction = "corporate_action"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        newlyReported = ClubDecode.field(c, .newlyReported)
        increased = ClubDecode.field(c, .increased)
        decreased = ClubDecode.field(c, .decreased)
        noLongerReported = ClubDecode.field(c, .noLongerReported)
        unchanged = ClubDecode.field(c, .unchanged)
        corporateAction = ClubDecode.field(c, .corporateAction)
    }
}

/// `ClubHoldingResponse` — one U.S.-listed position from a 13F, as of the quarter end.
nonisolated struct ClubHoldingDTO: Codable, Sendable {
    let name: String?
    let symbol: String?
    /// FRACTION of the filing's reported value (0.473 = 47.3%).
    let weight: Double?
    let shares: Double?
    let value: Double?
    let change: String?
    let newlyListed: Bool?
    let isSmall: Bool?
    let clubMemberSlug: String?
    let sector: String?

    enum CodingKeys: String, CodingKey {
        case name
        case symbol
        case weight
        case shares
        case value
        case change
        case newlyListed = "newly_listed"
        case isSmall = "is_small"
        case clubMemberSlug = "club_member_slug"
        case sector
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = ClubDecode.field(c, .name)
        symbol = ClubDecode.field(c, .symbol)
        weight = ClubDecode.field(c, .weight)
        shares = ClubDecode.field(c, .shares)
        value = ClubDecode.field(c, .value)
        change = ClubDecode.field(c, .change)
        newlyListed = ClubDecode.field(c, .newlyListed)
        isSmall = ClubDecode.field(c, .isSmall)
        clubMemberSlug = ClubDecode.field(c, .clubMemberSlug)
        sector = ClubDecode.field(c, .sector)
    }
}

/// `ClubChangeResponse` — one share change vs the previous quarter (never "unchanged").
nonisolated struct ClubChangeDTO: Codable, Sendable {
    let name: String?
    let symbol: String?
    let change: String?
    let newlyListed: Bool?
    let shares: Double?
    let prevShares: Double?
    let shareChange: Double?
    let value: Double?
    let weight: Double?

    enum CodingKeys: String, CodingKey {
        case name
        case symbol
        case change
        case newlyListed = "newly_listed"
        case shares
        case prevShares = "prev_shares"
        case shareChange = "share_change"
        case value
        case weight
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = ClubDecode.field(c, .name)
        symbol = ClubDecode.field(c, .symbol)
        change = ClubDecode.field(c, .change)
        newlyListed = ClubDecode.field(c, .newlyListed)
        shares = ClubDecode.field(c, .shares)
        prevShares = ClubDecode.field(c, .prevShares)
        shareChange = ClubDecode.field(c, .shareChange)
        value = ClubDecode.field(c, .value)
        weight = ClubDecode.field(c, .weight)
    }
}

/// `ClubStakeResponse` — a hand-kept stake outside the 13F, with its primary source.
nonisolated struct ClubStakeDTO: Codable, Sendable {
    let investeeName: String?
    let kind: String?
    let symbol: String?
    let localListing: String?
    /// PERCENT as the source states it (25.0 = 25%).
    let ownershipPct: Double?
    let ownershipBasis: String?
    let disclosedValue: Double?
    let valueBasis: String?
    let asOf: String?
    let sourceTitle: String?
    let sourceUrl: String?
    let tiedToDeal: Bool?
    let listedSince: String?
    let background: String?
    let verifiedOn: String?
    let isStale: Bool?
    let clubMemberSlug: String?

    enum CodingKeys: String, CodingKey {
        case investeeName = "investee_name"
        case kind
        case symbol
        case localListing = "local_listing"
        case ownershipPct = "ownership_pct"
        case ownershipBasis = "ownership_basis"
        case disclosedValue = "disclosed_value"
        case valueBasis = "value_basis"
        case asOf = "as_of"
        case sourceTitle = "source_title"
        case sourceUrl = "source_url"
        case tiedToDeal = "tied_to_deal"
        case listedSince = "listed_since"
        case background
        case verifiedOn = "verified_on"
        case isStale = "is_stale"
        case clubMemberSlug = "club_member_slug"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        investeeName = ClubDecode.field(c, .investeeName)
        kind = ClubDecode.field(c, .kind)
        symbol = ClubDecode.field(c, .symbol)
        localListing = ClubDecode.field(c, .localListing)
        ownershipPct = ClubDecode.field(c, .ownershipPct)
        ownershipBasis = ClubDecode.field(c, .ownershipBasis)
        disclosedValue = ClubDecode.field(c, .disclosedValue)
        valueBasis = ClubDecode.field(c, .valueBasis)
        asOf = ClubDecode.field(c, .asOf)
        sourceTitle = ClubDecode.field(c, .sourceTitle)
        sourceUrl = ClubDecode.field(c, .sourceUrl)
        tiedToDeal = ClubDecode.field(c, .tiedToDeal)
        listedSince = ClubDecode.field(c, .listedSince)
        background = ClubDecode.field(c, .background)
        verifiedOn = ClubDecode.field(c, .verifiedOn)
        isStale = ClubDecode.field(c, .isStale)
        clubMemberSlug = ClubDecode.field(c, .clubMemberSlug)
    }
}

/// `ClubMemberBriefResponse` — a club member named without a card.
nonisolated struct ClubMemberBriefDTO: Codable, Sendable {
    let slug: String?
    let name: String?

    enum CodingKeys: String, CodingKey {
        case slug
        case name
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = ClubDecode.field(c, .slug)
        name = ClubDecode.field(c, .name)
    }
}

/// `ClubHistoryPointResponse` — one earlier quarter of a 13F filer (Pro).
nonisolated struct ClubHistoryPointDTO: Codable, Sendable {
    let period: String?
    let periodEnd: String?
    let totalValue: Double?
    let positionCount: Int?

    enum CodingKeys: String, CodingKey {
        case period
        case periodEnd = "period_end"
        case totalValue = "total_value"
        case positionCount = "position_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        period = ClubDecode.field(c, .period)
        periodEnd = ClubDecode.field(c, .periodEnd)
        totalValue = ClubDecode.field(c, .totalValue)
        positionCount = ClubDecode.field(c, .positionCount)
    }
}

/// `TrillionClubCompanyResponse` — one company card (also the detail screen's header).
nonisolated struct TrillionClubCompanyDTO: Codable, Sendable {
    let slug: String?
    let name: String?
    let cardKind: String?
    let logoSymbol: String?
    let detailSymbol: String?
    let marketCap: Double?
    let marketCapAsOf: String?
    let capIsManual: Bool?
    let period: String?
    let periodEnd: String?
    let filedOn: String?
    let amendedOn: String?
    let nextDue: String?
    let positionCount: Int?
    let totalValue: Double?
    let topHoldings: [ClubHoldingDTO]?
    let changeCounts: ClubChangeCountsDTO?
    let comparison: String?
    let prevPeriod: String?
    let notice: String?
    let stakes: [ClubStakeDTO]?
    /// Every published stake of the company — the card carries only the material ones.
    let stakeCount: Int?
    /// Published stakes that are not a note on a 13F holding — the card's "· N stakes".
    let otherStakeCount: Int?
    let whaleId: String?
    let reviewedOn: String?

    enum CodingKeys: String, CodingKey {
        case slug
        case name
        case cardKind = "card_kind"
        case logoSymbol = "logo_symbol"
        case detailSymbol = "detail_symbol"
        case marketCap = "market_cap"
        case marketCapAsOf = "market_cap_as_of"
        case capIsManual = "cap_is_manual"
        case period
        case periodEnd = "period_end"
        case filedOn = "filed_on"
        case amendedOn = "amended_on"
        case nextDue = "next_due"
        case positionCount = "position_count"
        case totalValue = "total_value"
        case topHoldings = "top_holdings"
        case changeCounts = "change_counts"
        case comparison
        case prevPeriod = "prev_period"
        case notice
        case stakes
        case stakeCount = "stake_count"
        case otherStakeCount = "other_stake_count"
        case whaleId = "whale_id"
        case reviewedOn = "reviewed_on"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = ClubDecode.field(c, .slug)
        name = ClubDecode.field(c, .name)
        cardKind = ClubDecode.field(c, .cardKind)
        logoSymbol = ClubDecode.field(c, .logoSymbol)
        detailSymbol = ClubDecode.field(c, .detailSymbol)
        marketCap = ClubDecode.field(c, .marketCap)
        marketCapAsOf = ClubDecode.field(c, .marketCapAsOf)
        capIsManual = ClubDecode.field(c, .capIsManual)
        period = ClubDecode.field(c, .period)
        periodEnd = ClubDecode.field(c, .periodEnd)
        filedOn = ClubDecode.field(c, .filedOn)
        amendedOn = ClubDecode.field(c, .amendedOn)
        nextDue = ClubDecode.field(c, .nextDue)
        positionCount = ClubDecode.field(c, .positionCount)
        totalValue = ClubDecode.field(c, .totalValue)
        topHoldings = ClubDecode.list(c, .topHoldings)
        changeCounts = ClubDecode.field(c, .changeCounts)
        comparison = ClubDecode.field(c, .comparison)
        prevPeriod = ClubDecode.field(c, .prevPeriod)
        notice = ClubDecode.field(c, .notice)
        stakes = ClubDecode.list(c, .stakes)
        stakeCount = ClubDecode.field(c, .stakeCount)
        otherStakeCount = ClubDecode.field(c, .otherStakeCount)
        whaleId = ClubDecode.field(c, .whaleId)
        reviewedOn = ClubDecode.field(c, .reviewedOn)
    }
}

/// `TrillionClubGroupResponse` — the Home section (`HomeDashboardResponse.trillion_club`).
nonisolated struct TrillionClubGroupDTO: Codable, Sendable {
    let companies: [TrillionClubCompanyDTO]?
    let alsoInClub: [ClubMemberBriefDTO]?

    enum CodingKeys: String, CodingKey {
        case companies
        case alsoInClub = "also_in_club"
    }

    /// ⚠️ NEVER THROWS. This group rides inside `HomeDashboardResponseDTO`, and a throw here
    /// would fail the WHOLE Home decode — every other section blanked because this one was
    /// malformed. Anything that is not an object decodes as an empty (hidden) section.
    init(from decoder: Decoder) throws {
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {
            TrillionClubLog.logger.warning(
                "trillion club decode: trillion_club is not an object — section hidden")
            companies = nil
            alsoInClub = nil
            return
        }
        companies = ClubDecode.list(c, .companies)
        alsoInClub = ClubDecode.list(c, .alsoInClub)
    }
}

/// `TrillionClubDetailResponse` — a company's drill-down.
nonisolated struct TrillionClubDetailDTO: Codable, Sendable {
    let company: TrillionClubCompanyDTO?
    let holdings: [ClubHoldingDTO]?
    let changes: [ClubChangeDTO]?
    let stakes: [ClubStakeDTO]?
    let history: [ClubHistoryPointDTO]?
    let isLocked: Bool?
    let tierRequired: String?
    let lockedHoldingsCount: Int?
    /// Earlier quarters withheld from a Free caller; 0 = nothing behind the History lock.
    let lockedHistoryCount: Int?
    let otherMembers: [ClubMemberBriefDTO]?

    enum CodingKeys: String, CodingKey {
        case company
        case holdings
        case changes
        case stakes
        case history
        case isLocked = "is_locked"
        case tierRequired = "tier_required"
        case lockedHoldingsCount = "locked_holdings_count"
        case lockedHistoryCount = "locked_history_count"
        case otherMembers = "other_members"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        company = ClubDecode.field(c, .company)
        holdings = ClubDecode.list(c, .holdings)
        changes = ClubDecode.list(c, .changes)
        stakes = ClubDecode.list(c, .stakes)
        history = ClubDecode.list(c, .history)
        isLocked = ClubDecode.field(c, .isLocked)
        tierRequired = ClubDecode.field(c, .tierRequired)
        lockedHoldingsCount = ClubDecode.field(c, .lockedHoldingsCount)
        lockedHistoryCount = ClubDecode.field(c, .lockedHistoryCount)
        otherMembers = ClubDecode.list(c, .otherMembers)
    }
}

// MARK: - Wire enums (unknown → .unknown → hidden)
//
// Raw values mirror the backend constants (CARD_KINDS, CHANGE_KINDS, COMPARISON_*,
// NOTICE_KINDS, STAKE_KINDS, VALUE_BASES) — pinned by the parity test.

nonisolated enum ClubCardKind: String, CaseIterable, Sendable {
    case thirteenF = "thirteen_f"
    case noThirteenF = "no_thirteen_f"
    case nonUS = "non_us"
    case whaleLink = "whale_link"
    case unknown

    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }
}

nonisolated enum ClubChangeKind: String, CaseIterable, Sendable {
    case newlyReported = "newly_reported"
    case noLongerReported = "no_longer_reported"
    case increased
    case decreased
    case unchanged
    case corporateAction = "corporate_action"
    case unknown

    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }

    /// The neutral pill. Share counts only — a value that rose with the price is "Unchanged".
    var pillLabel: String? {
        switch self {
        case .newlyReported: return "Newly reported"
        case .noLongerReported: return "No longer reported"
        case .increased: return "Increased shares"
        case .decreased: return "Decreased shares"
        case .unchanged: return "Unchanged"
        case .corporateAction: return "Corporate action"
        case .unknown: return nil
        }
    }

    /// What the pill does NOT mean. The first two are the plan's exact wording.
    var helpText: String? {
        switch self {
        case .newlyReported: return "First time on a 13F — not necessarily a new purchase"
        case .noLongerReported: return "Left the filing — sold, merged, too small to report, or kept confidential"
        case .increased: return "More shares than the quarter before"
        case .decreased: return "Fewer shares than the quarter before"
        case .unchanged: return "Same share count; value changed with the price"
        case .corporateAction: return "Share count changed through a split, merger or similar event"
        case .unknown: return nil
        }
    }
}

nonisolated enum ClubStakeKind: String, CaseIterable, Sendable {
    case privateCompany = "private"
    case nonUSListed = "non_us_listed"
    case usListedOff13F = "us_listed_off_13f"
    case commitment
    case on13FNote = "on_13f_note"
    case unknown

    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }
}

nonisolated enum ClubValueBasis: String, CaseIterable, Sendable {
    case carryingValue = "carrying_value"
    case fairValue = "fair_value"
    case invested
    case committedUpTo = "committed_up_to"
    case unknown

    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }

    /// The ONLY verbs a disclosed figure is shown with. nil → the figure is not shown.
    func phrase(_ amount: String) -> String? {
        switch self {
        case .carryingValue: return "carried at \(amount)"
        case .fairValue: return "fair value \(amount)"
        case .invested: return "\(amount) invested"
        case .committedUpTo: return "committed up to \(amount)"
        case .unknown: return nil
        }
    }
}

nonisolated enum ClubComparison: String, CaseIterable, Sendable {
    case quarter
    case firstFiling = "first_filing"
    case gap
    case unknown

    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }
}

nonisolated enum ClubNotice: String, CaseIterable, Sendable {
    case latestNotIn = "latest_not_in"
    case amended
    case firstFiling = "first_filing"
    case noNewerFiling = "no_newer_filing"
    case unknown

    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }
}

// MARK: - Dates and periods

/// A date-only wire value ("2026-06-30", or a timestamp whose first ten characters are one).
/// Kept as its calendar components and printed from them, so no device time zone can shift
/// it a day — the same guarantee `ThemeReviewDate` gets from a UTC formatter, and it is
/// Sendable and locale-free, so a Buddhist- or Japanese-calendar phone still reads the year
/// the filing does.
nonisolated struct ClubDate: Comparable, Hashable, Sendable {
    let year: Int
    let month: Int
    let day: Int

    private static let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    private static let spokenMonths = ["January", "February", "March", "April", "May", "June", "July",
                                       "August", "September", "October", "November", "December"]

    init?(iso: String?) {
        guard let raw = iso?.trimmingCharacters(in: .whitespacesAndNewlines), raw.count >= 10 else {
            return nil
        }
        let head = Array(raw.prefix(10))
        guard head[4] == "-", head[7] == "-" else { return nil }
        func number(_ range: Range<Int>) -> Int? {
            let chars = head[range]
            guard chars.allSatisfy({ ("0"..."9").contains($0) }) else { return nil }
            return Int(String(chars))
        }
        guard let y = number(0..<4), let m = number(5..<7), let d = number(8..<10),
              (1900...2200).contains(y), (1...12).contains(m),
              d >= 1, d <= Self.daysIn(month: m, year: y) else { return nil }
        year = y
        month = m
        day = d
    }

    init?(year: Int, month: Int, day: Int) {
        guard (1900...2200).contains(year), (1...12).contains(month),
              day >= 1, day <= Self.daysIn(month: month, year: year) else { return nil }
        self.year = year
        self.month = month
        self.day = day
    }

    static func daysIn(month: Int, year: Int) -> Int {
        switch month {
        case 2:
            let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
            return leap ? 29 : 28
        case 4, 6, 9, 11: return 30
        default: return 31
        }
    }

    /// "Jun 30, 2026" — the app's "MMM d, yyyy".
    var long: String { "\(Self.months[month - 1]) \(day), \(year)" }

    /// "June 30, 2026" — for VoiceOver, which reads "Jun" letter by letter on some voices.
    var spoken: String { "\(Self.spokenMonths[month - 1]) \(day), \(year)" }

    static func < (lhs: ClubDate, rhs: ClubDate) -> Bool {
        (lhs.year, lhs.month, lhs.day) < (rhs.year, rhs.month, rhs.day)
    }
}

/// A 13F reporting period, "2026-Q2" on the wire.
nonisolated struct ClubPeriod: Hashable, Sendable {
    let year: Int
    let quarter: Int

    init?(wire: String?) {
        guard let raw = wire?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        let chars = Array(raw)
        guard chars.count == 7, chars[4] == "-", chars[5] == "Q",
              chars[0..<4].allSatisfy({ ("0"..."9").contains($0) }),
              let y = Int(String(chars[0..<4])), (1900...2200).contains(y),
              let q = Int(String(chars[6])), (1...4).contains(q) else { return nil }
        year = y
        quarter = q
    }

    init(year: Int, quarter: Int) {
        self.year = year
        self.quarter = quarter
    }

    /// "Q2 2026".
    var label: String { "Q\(quarter) \(year)" }

    /// "Q1" beside a period of the same year ("vs Q1"), "Q4 2025" across a year boundary.
    func label(relativeTo other: ClubPeriod?) -> String {
        other?.year == year ? "Q\(quarter)" : label
    }

    var next: ClubPeriod {
        quarter == 4 ? ClubPeriod(year: year + 1, quarter: 1) : ClubPeriod(year: year, quarter: quarter + 1)
    }
}

// MARK: - Number formatting

/// Every number this section prints. nil means "do not show" — never "$nan", never a
/// negative holding, never a weight over 100%.
nonisolated enum TrillionClubFormat {

    /// "$5.45T", "$63.4B", "$4.42B", "$10B", "$400M", "$5.8M", "$950K", "$512". The unit is
    /// chosen AFTER rounding, so $999.96B prints "$1T" rather than "$1000.0B".
    static func dollars(_ value: Double?) -> String? {
        guard let value, value.isFinite, value >= 0 else { return nil }
        if value == 0 { return "$0" }
        // (scale, suffix, decimals below 10, decimals from 10)
        let units: [(Double, String, Int, Int)] = [
            (1, "", 0, 0), (1e3, "K", 1, 0), (1e6, "M", 1, 0), (1e9, "B", 2, 1), (1e12, "T", 2, 2),
        ]
        var index = 0
        for (i, unit) in units.enumerated() where value >= unit.0 { index = i }
        while true {
            let (scale, suffix, small, large) = units[index]
            let scaled = value / scale
            var decimals = scaled < 10 ? small : large
            var rounded = round(scaled, decimals)
            if decimals == small && rounded >= 10 {
                decimals = large
                rounded = round(scaled, decimals)
            }
            if rounded >= 1000 && index + 1 < units.count {
                index += 1
                continue
            }
            return "$" + trimmedDecimal(String(format: "%.\(decimals)f", rounded)) + suffix
        }
    }

    /// "10.0" → "10", "1.50" → "1.5", "4.42" → "4.42": a disclosed "$10 billion" reads as
    /// "$10B", not a false precision of "$10.0B".
    private static func trimmedDecimal(_ s: String) -> String {
        guard s.contains(".") else { return s }
        var out = s
        while out.hasSuffix("0") { out.removeLast() }
        if out.hasSuffix(".") { out.removeLast() }
        return out
    }

    /// Share counts: "833,325", "214.8M", "1M", "1.25B". Whole shares.
    static func shares(_ value: Double?) -> String? {
        guard let value, value.isFinite, value >= 0, value < 1e15 else { return nil }
        let whole = value.rounded()
        if whole < 1_000_000 { return grouped(Int(whole)) }
        if whole < 999_950_000 { return trimmedDecimal(String(format: "%.1f", whole / 1e6)) + "M" }
        return trimmedDecimal(String(format: "%.2f", whole / 1e9)) + "B"
    }

    /// "+400,000", "-1.2M"; nil for non-finite, or for less than one whole share ("+0").
    static func signedShares(_ value: Double?) -> String? {
        guard let value, value.isFinite, abs(value).rounded() >= 1, let body = shares(abs(value)) else { return nil }
        return (value > 0 ? "+" : "-") + body
    }

    /// A holding's weight, a FRACTION: 0.4727 → "47%", 0.004 → "<1%", 0.996 → ">99%".
    /// Zero, negative, non-finite, or more than the whole filing → nil.
    static func weight(_ fraction: Double?) -> String? {
        guard let fraction, fraction.isFinite, fraction > 0, fraction <= 1.0005 else { return nil }
        if fraction < 0.01 { return "<1%" }
        let percent = Int((fraction * 100).rounded())
        if percent >= 100 && fraction < 1 { return ">99%" }
        return "\(min(percent, 100))%"
    }

    /// An ownership PERCENT as the source states it: 25 → "25%", 9.3 → "9.3%".
    ///
    /// Below 100 it NEVER prints 100: 99.97 rounded to "100%" reads as wholly owned, which the
    /// source says it is not. One more decimal ("99.97%"), or "<100%" when even that rounds up.
    static func ownership(_ percent: Double?) -> String? {
        guard let percent, percent.isFinite, percent > 0, percent <= 100 else { return nil }
        if percent < 0.1 { return "<0.1%" }
        let whole = percent.rounded()
        let text = abs(percent - whole) < 0.05
            ? String(format: "%.0f", whole)
            : String(format: "%.1f", percent)
        if percent < 100, text.hasPrefix("100") {
            let finer = String(format: "%.2f", percent)
            return finer.hasPrefix("100") ? "<100%" : finer + "%"
        }
        return text + "%"
    }

    /// "1,234,567" without a NumberFormatter, so no device locale can change the separator.
    static func grouped(_ value: Int) -> String {
        // `magnitude`, not `abs`: abs(Int.min) traps.
        let digits = String(value.magnitude)
        var out = ""
        for (i, ch) in digits.enumerated() {
            if i > 0 && (digits.count - i) % 3 == 0 { out.append(",") }
            out.append(ch)
        }
        return value < 0 ? "-" + out : out
    }

    private static func round(_ value: Double, _ decimals: Int) -> Double {
        let factor = pow(10.0, Double(decimals))
        return (value * factor).rounded() / factor
    }
}

// MARK: - Sanitising helpers (wire → presentation)

nonisolated enum ClubSanitize {
    /// A routable ticker or nil. Letters, digits, "." and "-" only — a space or an emoji in a
    /// symbol would build a detail route to nowhere, so it is not tappable at all.
    static func symbol(_ raw: String?) -> String? {
        guard let s = text(raw), s.count <= 15,
              s.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "." || $0 == "-") })
        else { return nil }
        return s.uppercased()
    }

    /// A U.S. ticker the logo CDN keys on, or nil: 1–5 letters, optionally "-" and one class
    /// letter ("BRK-B"). A local listing ("2222.SR", "005930.KS") is not one — the logo tile
    /// would fall back to a DIGIT while it loads (and for good when the CDN has no such PNG),
    /// and a guessed symbol can pull a different listed company's logo.
    static func usTicker(_ raw: String?) -> String? {
        guard let s = symbol(raw) else { return nil }
        let parts = s.split(separator: "-", omittingEmptySubsequences: false)
        func letters(_ part: Substring, _ lengths: ClosedRange<Int>) -> Bool {
            lengths.contains(part.count) && part.allSatisfy { ("A"..."Z").contains($0) }
        }
        switch parts.count {
        case 1: return letters(parts[0], 1...5) ? s : nil
        case 2: return letters(parts[0], 1...5) && letters(parts[1], 1...1) ? s : nil
        default: return nil
        }
    }

    /// The exchange suffixes the logo CDN keys a local listing on ("2222.SR", "005930.KS").
    static let logoExchangeSuffixes: Set<String> = [
        "SR", "KS", "KQ", "T", "HK", "SS", "SZ", "TW", "L", "PA", "DE", "AS", "SW", "TO", "AX", "NS", "BO",
    ]

    /// The symbol the logo CDN keys on, or nil: a U.S. ticker (`usTicker`), or a local
    /// listing with a REQUIRED exchange suffix from `logoExchangeSuffixes` ("2222.SR",
    /// "005930.KS"). A dotted class share ("BRK.B") or a bare local code ("2222") is not
    /// guessed at — a guessed symbol can pull a different listed company's logo. For logos
    /// only: it never makes a company routable (`detailSymbol` has its own rule), and the
    /// placeholder while a logo loads is the company's monogram, so a local listing never
    /// flashes a DIGIT tile.
    static func logoSymbol(_ raw: String?) -> String? {
        if let us = usTicker(raw) { return us }
        guard let s = symbol(raw) else { return nil }
        let parts = s.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 2, (1...10).contains(parts[0].count),
              parts[0].allSatisfy({ ("A"..."Z").contains($0) || ("0"..."9").contains($0) }),
              logoExchangeSuffixes.contains(String(parts[1])) else { return nil }
        return s
    }

    /// The migration's slug rule: `^[a-z0-9-]{1,40}$`.
    static func slug(_ raw: String?) -> String? {
        guard let s = raw, (1...40).contains(s.count),
              s.allSatisfy({ ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "-" })
        else { return nil }
        return s
    }

    /// Trimmed, or nil when empty.
    static func text(_ raw: String?) -> String? {
        guard let s = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s
    }

    /// A finite, non-negative amount.
    static func amount(_ raw: Double?) -> Double? {
        guard let raw, raw.isFinite, raw >= 0 else { return nil }
        return raw
    }

    /// An https URL with a host — anything else is not offered as a link.
    static func httpsURL(_ raw: String?) -> URL? {
        guard let s = text(raw), let url = URL(string: s),
              url.scheme?.lowercased() == "https", let host = url.host, !host.isEmpty else { return nil }
        return url
    }
}

// MARK: - Chips

/// The grey chips on a stake. Labels are nouns, never a verdict: stakes listed abroad trade,
/// so "Non-U.S. listed" rather than "not tradable".
nonisolated enum ClubChip: Hashable, Identifiable, Sendable {
    case privateCompany
    case nonUSListed
    case tiedToDeal
    case commitment
    case clubMember
    /// A stake that went public — 13F cards only, where "not on a 13F yet" is true.
    case listedSince(ClubDate)

    var id: String { label }

    var label: String {
        switch self {
        case .privateCompany: return "Private"
        case .nonUSListed: return "Non-U.S. listed"
        case .tiedToDeal: return "Tied to a deal"
        case .commitment: return "Commitment"
        case .clubMember: return "Club member"
        case .listedSince(let date): return "Listed since \(date.long) — not on a 13F yet"
        }
    }

    /// A full sentence for VoiceOver — the label alone ("Private") says nothing about why.
    var accessibilityText: String { accessibilityText(source: nil) }

    /// The same sentence, naming the stake's source where the chip describes a disclosure.
    ///
    /// ⚠️ "Commitment" covers an "up to" investment agreement AND a warrant the INVESTEE issued
    /// (Meta's AMD warrant: AMD's 10-Q, nothing Meta agreed to invest), and a commitment can
    /// already be partly funded (Anthropic's Series G took part of Microsoft's and NVIDIA's).
    /// So the sentence claims neither an agreement to invest nor that nothing is held yet —
    /// only that the source discloses it and a 13F-style holding is not what it is.
    func accessibilityText(source: String?) -> String {
        switch self {
        case .privateCompany: return "Private: not publicly traded, so there's no market price."
        case .nonUSListed: return "Non-U.S. listed: listed outside the U.S., so it never appears on a 13F."
        case .tiedToDeal: return "Tied to a deal: this stake came with a business agreement between the two companies."
        case .commitment:
            let from = ClubSanitize.text(source).map { "in \($0)" } ?? "in its source"
            return "Commitment: a commitment or right disclosed \(from), not a reported holding."
        case .clubMember: return "Club member: this company is itself valued at $1 trillion or more."
        case .listedSince(let date): return "Listed since \(date.spoken), and not on a 13F yet."
        }
    }

    /// A chip that is a short SENTENCE rather than a word. It is laid out on its own line,
    /// outside the flow of word chips, so it is offered the column's width and wraps
    /// (`FlowLayout` measured every child at its one-line width until 2026-09-24).
    var isSentence: Bool {
        if case .listedSince = self { return true }
        return false
    }

    var systemImage: String {
        switch self {
        case .privateCompany: return "building.2"
        case .nonUSListed: return "globe"
        case .tiedToDeal: return "link"
        case .commitment: return "doc.text"
        case .clubMember: return "seal"
        case .listedSince: return "calendar"
        }
    }
}

// MARK: - Presentation models

nonisolated struct ClubMemberBrief: Identifiable, Hashable, Sendable {
    let slug: String
    let name: String
    var id: String { slug }
}

nonisolated extension ClubMemberBrief {
    init?(dto: ClubMemberBriefDTO) {
        guard let slug = ClubSanitize.slug(dto.slug), let name = ClubSanitize.text(dto.name) else { return nil }
        self.init(slug: slug, name: name)
    }

    /// Drops unreadable rows and duplicate slugs (first wins) — `ForEach` needs unique ids.
    static func list(_ dtos: [ClubMemberBriefDTO]?) -> [ClubMemberBrief] {
        var seen = Set<String>()
        return (dtos ?? []).compactMap { ClubMemberBrief(dto: $0) }.filter { seen.insert($0.slug).inserted }
    }
}

nonisolated struct ClubChangeCounts: Equatable, Sendable {
    var newlyReported = 0
    var increased = 0
    var decreased = 0
    var noLongerReported = 0
    var unchanged = 0
    var corporateAction = 0

    /// The non-zero outcomes, in a fixed order, "unchanged" left out. Share counts only.
    var parts: [String] {
        var out: [String] = []
        if newlyReported > 0 { out.append("\(newlyReported) newly reported") }
        if increased > 0 { out.append("\(increased) with more shares") }
        if decreased > 0 { out.append("\(decreased) with fewer shares") }
        if noLongerReported > 0 { out.append("\(noLongerReported) no longer reported") }
        if corporateAction > 0 {
            out.append(corporateAction == 1 ? "1 corporate action" : "\(corporateAction) corporate actions")
        }
        return out
    }
}

nonisolated extension ClubChangeCounts {
    /// A negative or absent count is 0 — a count can only ever be a count.
    init(dto: ClubChangeCountsDTO) {
        func clamp(_ v: Int?) -> Int { max(0, v ?? 0) }
        self.init(newlyReported: clamp(dto.newlyReported), increased: clamp(dto.increased),
                  decreased: clamp(dto.decreased), noLongerReported: clamp(dto.noLongerReported),
                  unchanged: clamp(dto.unchanged), corporateAction: clamp(dto.corporateAction))
    }
}

/// One 13F position — a HOLDING (as of the quarter end) or a CHANGE row (vs the quarter
/// before). One type for both so a single row molecule renders either.
nonisolated struct ClubPosition: Identifiable, Sendable {
    let id: String
    let name: String
    /// Routable U.S. ticker, or nil (unresolved / non-U.S.) — nil is never tappable.
    let symbol: String?
    let weight: Double?
    let shares: Double?
    let value: Double?
    /// nil when there is nothing to say (a first filing, or an unknown server value).
    let change: ClubChangeKind?
    let newlyListed: Bool
    let isSmall: Bool
    let clubMemberSlug: String?
    let prevShares: Double?
    let shareChange: Double?
}

nonisolated extension ClubPosition {
    init?(holding dto: ClubHoldingDTO, index: Int) {
        let symbol = ClubSanitize.symbol(dto.symbol)
        guard let name = ClubSanitize.text(dto.name) ?? symbol else { return nil }
        let change = ClubChangeKind(wire: dto.change)
        self.init(id: "h\(index)-\(name)", name: name, symbol: symbol,
                  weight: dto.weight.flatMap { $0.isFinite ? $0 : nil },
                  shares: ClubSanitize.amount(dto.shares), value: ClubSanitize.amount(dto.value),
                  change: change == .unknown ? nil : change,
                  newlyListed: dto.newlyListed ?? false, isSmall: dto.isSmall ?? false,
                  clubMemberSlug: ClubSanitize.slug(dto.clubMemberSlug),
                  prevShares: nil, shareChange: nil)
    }

    /// nil for an "unchanged" row (never sent, dropped if it is) and for an unknown outcome.
    init?(change dto: ClubChangeDTO, index: Int) {
        let symbol = ClubSanitize.symbol(dto.symbol)
        let change = ClubChangeKind(wire: dto.change)
        guard let name = ClubSanitize.text(dto.name) ?? symbol,
              change != .unknown, change != .unchanged else { return nil }
        self.init(id: "c\(index)-\(name)", name: name, symbol: symbol,
                  weight: dto.weight.flatMap { $0.isFinite ? $0 : nil },
                  shares: ClubSanitize.amount(dto.shares), value: ClubSanitize.amount(dto.value),
                  change: change, newlyListed: dto.newlyListed ?? false, isSmall: false,
                  clubMemberSlug: nil,
                  prevShares: ClubSanitize.amount(dto.prevShares),
                  shareChange: dto.shareChange.flatMap { $0.isFinite ? $0 : nil })
    }

    /// This position with no share-change outcome — for a filing that was NOT compared with
    /// the quarter before (a gap, or a first filing). "Unchanged" there would describe a
    /// comparison nobody made.
    func withoutChange() -> ClubPosition {
        ClubPosition(id: id, name: name, symbol: symbol, weight: weight, shares: shares, value: value,
                     change: nil, newlyListed: false, isSmall: isSmall, clubMemberSlug: clubMemberSlug,
                     prevShares: nil, shareChange: nil)
    }

    var weightText: String? { TrillionClubFormat.weight(weight) }
    var valueText: String? { TrillionClubFormat.dollars(value) }
    var sharesText: String? { TrillionClubFormat.shares(shares).map { "\($0) shares" } }

    /// "INTC · 214.8M shares · $29.99B".
    var holdingLine: String? {
        let parts = [symbol, sharesText, valueText].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// What moved, in shares. Neutral wording: a change row never says why.
    var changeLine: String? {
        guard let change else { return nil }
        let now = TrillionClubFormat.shares(shares)
        let before = TrillionClubFormat.shares(prevShares)
        switch change {
        case .newlyReported:
            let base = now.map { "\($0) shares" }
            let listed = newlyListed ? "first 13F since it began trading" : nil
            let parts = [base, listed].compactMap { $0 }
            return parts.isEmpty ? nil : parts.joined(separator: " · ")
        case .noLongerReported:
            return before.map { "Previously \($0) shares" }
        case .increased, .decreased, .corporateAction:
            guard let before, let now else { return now.map { "\($0) shares" } }
            let delta = TrillionClubFormat.signedShares(shareChange).map { " (\($0))" } ?? ""
            return "\(before) → \(now) shares\(delta)"
        case .unchanged:
            return now.map { "\($0) shares" }
        case .unknown:
            return nil
        }
    }

    var smallText: String? { isSmall ? "Small position · under 1% of reported holdings" : nil }

    /// The detail row's short grey line: "INTC · $30B" — symbol and value, no share count
    /// (VoiceOver keeps the full `holdingLine`). A holding on its first 13F because it began
    /// trading says so here too, so its "Newly reported" pill never reads as a purchase.
    var shortHoldingLine: String? {
        let listed = change == .newlyReported && newlyListed ? "first 13F since it began trading" : nil
        let parts = [symbol, valueText, listed].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var accessibilityText: String {
        var parts = [name]
        if let w = weightText { parts.append("\(w) of reported holdings") }
        if let line = holdingLine { parts.append(line) }
        if clubMemberSlug != nil { parts.append(ClubChip.clubMember.accessibilityText) }
        if let label = change?.pillLabel {
            parts.append(change?.helpText.map { "\(label): \($0)" } ?? label)
        }
        if let line = changeLine, change != nil { parts.append(line) }
        return parts.joined(separator: ". ")
    }
}

/// A hand-kept stake with its primary source.
nonisolated struct ClubStake: Identifiable, Sendable {
    let id: String
    let investeeName: String
    let kind: ClubStakeKind
    let symbol: String?
    let localListing: String?
    let ownershipPct: Double?
    let ownershipBasis: String?
    let disclosedValue: Double?
    let valueBasis: ClubValueBasis?
    let asOf: ClubDate
    let sourceTitle: String
    let sourceURL: URL?
    let tiedToDeal: Bool
    let listedSince: ClubDate?
    let background: String?
    let verifiedOn: ClubDate?
    let isStale: Bool
    let clubMemberSlug: String?
}

nonisolated extension ClubStake {
    /// nil unless the stake is fully attributable: a name, a known kind, a source and the
    /// date it describes. An unsourced figure is never shown (the backend drops these too).
    init?(dto: ClubStakeDTO, index: Int) {
        let kind = ClubStakeKind(wire: dto.kind)
        guard let name = ClubSanitize.text(dto.investeeName), kind != .unknown,
              let source = ClubSanitize.text(dto.sourceTitle),
              let asOf = ClubDate(iso: dto.asOf) else {
            TrillionClubLog.logger.warning(
                "trillion club: stake row \(index) dropped — missing name, known kind, source or date")
            return nil
        }
        let basis = dto.valueBasis.map { ClubValueBasis(wire: $0) }
        self.init(id: "s\(index)-\(name)-\(kind.rawValue)", investeeName: name, kind: kind,
                  symbol: ClubSanitize.symbol(dto.symbol),
                  localListing: ClubSanitize.text(dto.localListing),
                  ownershipPct: dto.ownershipPct, ownershipBasis: ClubSanitize.text(dto.ownershipBasis),
                  disclosedValue: dto.disclosedValue,
                  valueBasis: basis == .unknown ? nil : basis,
                  asOf: asOf, sourceTitle: source, sourceURL: ClubSanitize.httpsURL(dto.sourceUrl),
                  tiedToDeal: dto.tiedToDeal ?? false,
                  listedSince: ClubDate(iso: dto.listedSince),
                  background: ClubSanitize.text(dto.background),
                  verifiedOn: ClubDate(iso: dto.verifiedOn),
                  isStale: dto.isStale ?? false,
                  clubMemberSlug: ClubSanitize.slug(dto.clubMemberSlug))
    }

    /// The kind chip, when the kind has one. A note on a 13F holding and an off-13F U.S.
    /// listing carry no kind chip — they are ordinary listed shares.
    var kindChip: ClubChip? {
        switch kind {
        case .privateCompany: return .privateCompany
        case .nonUSListed: return .nonUSListed
        case .commitment: return .commitment
        case .usListedOff13F, .on13FNote, .unknown: return nil
        }
    }

    /// Every chip for this stake. `listedSince` only on a 13F card — on any other card
    /// "not on a 13F yet" would be false (that owner never files one).
    func chips(onThirteenFCard: Bool) -> [ClubChip] {
        var out: [ClubChip] = []
        if let kindChip { out.append(kindChip) }
        if tiedToDeal { out.append(.tiedToDeal) }
        if clubMemberSlug != nil { out.append(.clubMember) }
        if onThirteenFCard, let listedSince { out.append(.listedSince(listedSince)) }
        return out
    }

    /// "27% as-converted · carried at $3.01B". A figure whose basis is unknown is not shown:
    /// without its verb it would read as a market price.
    var figureText: String? {
        var parts: [String] = []
        if let pct = TrillionClubFormat.ownership(ownershipPct) {
            parts.append(ownershipBasis.map { "\(pct) \($0)" } ?? pct)
        }
        if let value = disclosedValue, value > 0, let amount = TrillionClubFormat.dollars(value),
           let phrase = valueBasis?.phrase(amount) {
            parts.append(phrase)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// The detail row's figure line — never empty. A stake with no disclosed figure still
    /// says what it IS: Meta's AMD warrant (no value, no percent) must not read as a
    /// holding of AMD shares once the chips are gone from the row.
    var rowFigureText: String {
        if let figureText {
            // A commitment sized as a percent ("10% of shares", a warrant) reads as a holding
            // without its kind; only the "committed up to" verb says it on its own.
            let saysCommitment = valueBasis == .committedUpTo && (disclosedValue ?? 0) > 0
            return kind == .commitment && !saysCommitment ? "Commitment · \(figureText)" : figureText
        }
        switch kind {
        case .commitment: return "Commitment — not a reported holding"
        case .privateCompany: return "Private stake — no figure disclosed"
        case .nonUSListed:
            return localListing.map { "Listed in \($0) — no figure disclosed" }
                ?? "Listed outside the U.S. — no figure disclosed"
        case .usListedOff13F: return "U.S.-listed, not on a 13F — no figure disclosed"
        case .on13FNote, .unknown: return "No figure disclosed"
        }
    }

    /// "per Microsoft 10-K (FY2026), Jun 30, 2026".
    var sourceText: String { "per \(sourceTitle), \(asOf.long)" }

    var staleText: String? {
        guard isStale else { return nil }
        return verifiedOn.map { "Last checked \($0.long) — may be out of date." } ?? "May be out of date."
    }

}

/// One earlier quarter of a 13F filer (Pro).
nonisolated struct ClubHistoryPoint: Identifiable, Sendable {
    let period: ClubPeriod
    let periodEnd: ClubDate?
    let totalValue: Double?
    let positionCount: Int?
    var id: String { period.label }
}

nonisolated extension ClubHistoryPoint {
    init?(dto: ClubHistoryPointDTO) {
        guard let period = ClubPeriod(wire: dto.period) else { return nil }
        self.init(period: period, periodEnd: ClubDate(iso: dto.periodEnd),
                  totalValue: ClubSanitize.amount(dto.totalValue),
                  positionCount: dto.positionCount.flatMap { $0 >= 0 ? $0 : nil })
    }

    /// "Q1 2026 · Mar 31, 2026".
    var title: String { periodEnd.map { "\(period.label) · \($0.long)" } ?? period.label }

    /// "8 holdings · $18.4B reported".
    var detail: String? {
        let count = positionCount.map { $0 == 1 ? "1 holding" : "\(TrillionClubFormat.grouped($0)) holdings" }
        let value = TrillionClubFormat.dollars(totalValue).map { "\($0) reported" }
        let parts = [count, value].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// One company card — also the header of its detail screen.
nonisolated struct TrillionClubCompany: Identifiable, Sendable {
    let slug: String
    let name: String
    let kind: ClubCardKind
    /// A symbol the logo CDN knows (`ClubSanitize.logoSymbol`: a U.S. ticker, or a local
    /// listing with a known exchange suffix). nil → a letter tile, never a guessed
    /// pseudo-ticker (which could pull a DIFFERENT listed company's logo).
    let logoSymbol: String?
    let detailSymbol: String?
    let marketCap: Double?
    let marketCapAsOf: ClubDate?
    let capIsManual: Bool
    let period: ClubPeriod?
    let periodEnd: ClubDate?
    let filedOn: ClubDate?
    let amendedOn: ClubDate?
    let nextDue: ClubDate?
    let positionCount: Int?
    let totalValue: Double?
    let topHoldings: [ClubPosition]
    let changeCounts: ClubChangeCounts?
    let comparison: ClubComparison?
    let prevPeriod: ClubPeriod?
    let notice: ClubNotice?
    let stakes: [ClubStake]
    /// Every published stake, material or not (`stakes` holds only the card's material ones).
    /// nil when the server did not say — then no count is claimed.
    let stakeCount: Int?
    /// Published stakes that are not a note on a 13F holding — exactly the detail's "Other
    /// stakes" list. nil when the server did not say (a build before the field).
    let otherStakeCount: Int?
    let whaleId: String?
    let reviewedOn: ClubDate?

    var id: String { slug }
}

nonisolated extension TrillionClubCompany {
    /// nil for a card this build cannot render honestly: no slug, no name, or a card kind it
    /// does not know (a new server kind is hidden, not guessed at).
    init?(dto: TrillionClubCompanyDTO) {
        let kind = ClubCardKind(wire: dto.cardKind)
        guard let slug = ClubSanitize.slug(dto.slug), let name = ClubSanitize.text(dto.name),
              kind != .unknown else {
            TrillionClubLog.logger.warning(
                "trillion club: company card dropped — slug=\(dto.slug ?? "nil", privacy: .public) kind=\(dto.cardKind ?? "nil", privacy: .public)")
            return nil
        }
        let comparison = dto.comparison.map { ClubComparison(wire: $0) }
        let notice = dto.notice.map { ClubNotice(wire: $0) }
        let whale = ClubSanitize.text(dto.whaleId)
        let stakes = (dto.stakes ?? []).enumerated().compactMap {
            ClubStake(dto: $0.element, index: $0.offset)
        }
        // A "gap" or a first filing was NOT compared with the quarter before: its holdings
        // carry no outcome and it has no counts (an all-zero count there would print "no
        // share-count changes" about a comparison nobody made).
        let compared = comparison != .gap && comparison != .firstFiling
        let holdings = (dto.topHoldings ?? []).enumerated().compactMap {
            ClubPosition(holding: $0.element, index: $0.offset)
        }
        self.init(
            slug: slug, name: name, kind: kind,
            logoSymbol: ClubSanitize.logoSymbol(dto.logoSymbol),
            detailSymbol: ClubSanitize.symbol(dto.detailSymbol),
            marketCap: dto.marketCap.flatMap { $0.isFinite && $0 > 0 ? $0 : nil },
            marketCapAsOf: ClubDate(iso: dto.marketCapAsOf),
            capIsManual: dto.capIsManual ?? false,
            period: ClubPeriod(wire: dto.period),
            periodEnd: ClubDate(iso: dto.periodEnd),
            filedOn: ClubDate(iso: dto.filedOn),
            amendedOn: ClubDate(iso: dto.amendedOn),
            nextDue: ClubDate(iso: dto.nextDue),
            positionCount: dto.positionCount.flatMap { $0 >= 0 ? $0 : nil },
            totalValue: ClubSanitize.amount(dto.totalValue),
            topHoldings: compared ? holdings : holdings.map { $0.withoutChange() },
            changeCounts: compared ? dto.changeCounts.map { ClubChangeCounts(dto: $0) } : nil,
            comparison: comparison == .unknown ? nil : comparison,
            prevPeriod: ClubPeriod(wire: dto.prevPeriod),
            notice: notice == .unknown ? nil : notice,
            stakes: stakes,
            // Never fewer than the stakes actually on the card; a negative count is no count.
            stakeCount: dto.stakeCount.flatMap { $0 >= 0 ? min(max($0, stakes.count), 100_000) : nil },
            otherStakeCount: dto.otherStakeCount.flatMap { $0 >= 0 ? min($0, 100_000) : nil },
            // Only a link card opens a profile; a stray id on any other card is ignored.
            whaleId: kind == .whaleLink ? whale : nil,
            reviewedOn: ClubDate(iso: dto.reviewedOn)
        )
    }

    // MARK: Copy

    /// The letter tile when there is no logo: the name's first LETTER, never a digit or a
    /// symbol ("3M" → "M"), so a tile can never read as a number.
    var monogram: String {
        if let letter = name.first(where: \.isLetter) { return String(letter).uppercased() }
        return String(name.prefix(1)).uppercased()
    }

    /// A 13F filing row was read for this card. A 13F filer can be served before its first
    /// filing is processed (a failed first run, an unreadable row) — then nothing may be said
    /// about a filing: no holdings count, no dates, no notice, no comparison.
    var hasFilingOnFile: Bool { period != nil || periodEnd != nil || filedOn != nil || amendedOn != nil }

    var badgeText: String {
        switch kind {
        case .thirteenF: return "13F filer"
        case .noThirteenF: return "No 13F"
        case .nonUS: return "Non-U.S. company"
        case .whaleLink: return "Investor profile"
        case .unknown: return ""
        }
    }

    /// "Market value $5.45T as of Sep 23, 2026". An owner-entered cap (a non-U.S. listing,
    /// converted at a recorded exchange rate) says "about" — it is not a dated U.S. close.
    var marketValueLine: String? {
        guard let cap = TrillionClubFormat.dollars(marketCap) else { return nil }
        let figure = capIsManual ? "about \(cap)" : cap
        return marketCapAsOf.map { "Market value \(figure) as of \($0.long)" } ?? "Market value \(figure)"
    }

    /// "8 U.S.-listed holdings · $63.4B reported" — 13F cards with a filing on file only.
    var holdingsStatLine: String? {
        guard kind == .thirteenF, hasFilingOnFile else { return nil }
        if positionCount == 0 {
            return period.map { "No U.S.-listed stock holdings reported for \($0.label)" }
                ?? "No U.S.-listed stock holdings reported"
        }
        let count = positionCount.map {
            $0 == 1 ? "1 U.S.-listed holding" : "\(TrillionClubFormat.grouped($0)) U.S.-listed holdings"
        }
        let value = TrillionClubFormat.dollars(totalValue).map { "\($0) reported" }
        let parts = [count, value].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// "vs Q1: 1 newly reported" — share counts only, in neutral ink.
    var changeLine: String? {
        guard kind == .thirteenF, let comparison else { return nil }
        switch comparison {
        case .firstFiling:
            return "First 13F on file — no earlier quarter to compare"
        case .gap:
            // The filing before this one on file is NOT the adjacent quarter, so nothing was
            // compared: no counts, no "no share-count changes", no pills.
            return period.map { "No filing for the quarter before \($0.label) to compare with." }
                ?? "No filing for the quarter before to compare with."
        case .quarter:
            guard let counts = changeCounts else { return nil }
            let prefix = prevPeriod.map { "vs \($0.label(relativeTo: period))" } ?? "vs the quarter before"
            let parts = counts.parts
            return "\(prefix): " + (parts.isEmpty ? "no share-count changes" : parts.joined(separator: " · "))
        case .unknown:
            return nil
        }
    }

    /// "Holdings on Jun 30, 2026 · filed Aug 14, 2026".
    var filingDatesLine: String? {
        guard kind == .thirteenF else { return nil }
        switch (periodEnd, filedOn) {
        case let (end?, filed?): return "Holdings on \(end.long) · filed \(filed.long)"
        case let (end?, nil): return "Holdings on \(end.long)"
        case let (nil, filed?): return "Filed \(filed.long)"
        case (nil, nil): return nil
        }
    }

    /// "Next 13F due by Nov 16, 2026" — the legal deadline, not a promise of a date.
    var nextDueLine: String? {
        guard kind == .thirteenF, hasFilingOnFile, let nextDue else { return nil }
        return "Next 13F due by \(nextDue.long)"
    }

    /// The card notice, worded conservatively: a filing that has not reached us is "not
    /// received", never "not filed".
    var noticeText: String? {
        guard kind == .thirteenF, hasFilingOnFile, let notice else { return nil }
        switch notice {
        case .latestNotIn:
            return period.map { "We haven't received the \($0.next.label) filing yet — showing \($0.label)." }
                ?? "We haven't received the latest filing yet."
        case .amended:
            return amendedOn.map { "Includes an amended filing from \($0.long)." } ?? "Includes an amended filing."
        case .firstFiling:
            // The change line already says exactly this.
            return comparison == .firstFiling ? nil : "First 13F on file — no earlier quarter to compare."
        case .noNewerFiling:
            return period.map { "No newer 13F found — the latest on file is \($0.label)." } ?? "No newer 13F found."
        case .unknown:
            return nil
        }
    }

    /// The one-line body of a card without 13F holdings on screen.
    var explainer: String? {
        switch kind {
        case .noThirteenF:
            return "\(name) doesn't file a 13F, so there's no quarterly list of its U.S.-listed holdings."
        case .nonUS:
            // Always true, whatever the rows cite: "annual report" was false for Samsung
            // (interim statements), and "its own financial reports" for its Anthropic row
            // (Anthropic's announcement). Each stake line shows its own source and date.
            return "Each stake names its source and date."
        case .whaleLink:
            // The profile sentence only when there IS a profile to open — the button is shown
            // on the same condition. A card kept without its whales row says nothing about one.
            return whaleId != nil
                ? "\(name) files 13Fs as an investor — see its full portfolio on its profile."
                : nil
        case .thirteenF:
            return hasFilingOnFile ? nil : "Holdings appear after the first 13F is processed."
        case .unknown:
            return nil
        }
    }

    /// The heading over a card's stakes.
    var stakesHeading: String? {
        guard !stakes.isEmpty else { return nil }
        // "its 13F doesn't list" presumes a 13F on file.
        guard kind == .thirteenF, hasFilingOnFile else { return "Disclosed stakes" }
        return stakes.contains { $0.kind != .on13FNote }
            ? "Also holds stakes its 13F doesn't list"
            : "From its other filings"
    }

    /// "+2 more in the details" under a card that shows `shown` of its stakes. The count is
    /// every PUBLISHED stake (`stakeCount`), not the card's material ones — the detail lists
    /// them all. Without a server count no number is claimed; nothing more → nil.
    func moreStakesText(shown: Int) -> String? {
        guard let total = stakeCount else {
            return stakes.count > shown ? "More in the details" : nil
        }
        let more = total - max(shown, 0)
        return more > 0 ? "+\(TrillionClubFormat.grouped(more)) more in the details" : nil
    }

    /// The Home card's one grey line, never empty: "8 holdings · 2 stakes", "1 stake". It
    /// counts what the detail LISTS. A 13F filer with a filing on file gets the Holdings /
    /// Other stakes split, so its notes on 13F holdings are among the holdings and only the
    /// other stakes are counted (`otherStakeCount`; before the server sends it, the card's
    /// own material, never-a-note stakes stand in). Every other card's detail is one list of
    /// EVERY stake, notes included, so all of them are counted (`stakeCount`).
    var cardLine: String {
        var parts: [String] = []
        let segmented = kind == .thirteenF && hasFilingOnFile
        if segmented, let n = positionCount, n > 0 {
            parts.append(n == 1 ? "1 holding" : "\(TrillionClubFormat.grouped(n)) holdings")
        }
        let others = segmented ? (otherStakeCount ?? stakes.count) : (stakeCount ?? stakes.count)
        if others > 0 {
            parts.append(others == 1 ? "1 stake" : "\(TrillionClubFormat.grouped(others)) stakes")
        }
        return parts.isEmpty ? "See details" : parts.joined(separator: " · ")
    }

    /// VoiceOver for the Home card: the name and the same line the card shows.
    var cardAccessibilityText: String { "\(name). \(cardLine)." }

    var accessibilityText: String {
        let lines: [String?] = [name, badgeText, marketValueLine, holdingsStatLine, changeLine,
                                filingDatesLine, explainer, noticeText]
        return lines.compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: ". ")
    }
}

/// The Home section. Empty → hidden.
nonisolated struct TrillionClubGroup: Sendable {
    let companies: [TrillionClubCompany]
    let alsoInClub: [ClubMemberBrief]

    static let empty = TrillionClubGroup(companies: [], alsoInClub: [])

    var isEmpty: Bool { companies.isEmpty }
}

nonisolated extension TrillionClubGroup {
    /// Unreadable cards dropped; a DUPLICATE slug keeps the first card (SwiftUI's `ForEach`
    /// needs unique ids, and a doubled card is a server bug to log, not to render).
    init(dto: TrillionClubGroupDTO?) {
        var seen = Set<String>()
        var companies: [TrillionClubCompany] = []
        for raw in dto?.companies ?? [] {
            guard let company = TrillionClubCompany(dto: raw) else { continue }
            guard seen.insert(company.slug).inserted else {
                TrillionClubLog.logger.warning(
                    "trillion club: duplicate card \(company.slug, privacy: .public) dropped")
                continue
            }
            companies.append(company)
        }
        // A member with a card is not also listed as "without a card".
        let also = ClubMemberBrief.list(dto?.alsoInClub).filter { !seen.contains($0.slug) }
        self.init(companies: companies, alsoInClub: also)
    }
}

/// A company's drill-down.
nonisolated struct TrillionClubDetail: Sendable {
    let company: TrillionClubCompany
    let holdings: [ClubPosition]
    let changes: [ClubPosition]
    let stakes: [ClubStake]
    let history: [ClubHistoryPoint]
    let isLocked: Bool
    let tierRequired: String?
    /// Holdings withheld from a Free caller — the "+N more holdings" row.
    let lockedHoldingsCount: Int
    /// Earlier quarters withheld from a Free caller. 0 → nothing is behind the History lock,
    /// so no paywall is shown for it. nil → the server did not say (the lock follows
    /// `isLocked`, as before the count existed).
    let lockedHistoryCount: Int?
    /// Every OTHER member of the club, with or without a card — never "members without a
    /// card" (that list is the Home group's `alsoInClub`).
    let otherMembers: [ClubMemberBrief]
}

nonisolated extension TrillionClubDetail {
    /// nil when the company header itself is unreadable — there is no screen without it.
    init?(dto: TrillionClubDetailDTO) {
        guard let companyDTO = dto.company, let company = TrillionClubCompany(dto: companyDTO) else {
            TrillionClubLog.logger.error("trillion club: detail payload has no readable company")
            return nil
        }
        var seenPeriods = Set<ClubPeriod>()
        // Same rule as the card: a gap or a first filing was never compared, so its holdings
        // carry no outcome and there are no change rows to list.
        let compared = company.comparison != .gap && company.comparison != .firstFiling
        let holdings = (dto.holdings ?? []).enumerated().compactMap {
            ClubPosition(holding: $0.element, index: $0.offset)
        }
        self.init(
            company: company,
            holdings: compared ? holdings : holdings.map { $0.withoutChange() },
            changes: compared
                ? (dto.changes ?? []).enumerated().compactMap { ClubPosition(change: $0.element, index: $0.offset) }
                : [],
            stakes: (dto.stakes ?? []).enumerated().compactMap {
                ClubStake(dto: $0.element, index: $0.offset)
            },
            history: (dto.history ?? []).compactMap { ClubHistoryPoint(dto: $0) }
                .filter { seenPeriods.insert($0.period).inserted },
            isLocked: dto.isLocked ?? false,
            tierRequired: ClubSanitize.text(dto.tierRequired),
            lockedHoldingsCount: min(max(dto.lockedHoldingsCount ?? 0, 0), 100_000),
            lockedHistoryCount: dto.lockedHistoryCount.map { min(max($0, 0), 100_000) },
            otherMembers: ClubMemberBrief.list(dto.otherMembers)
        )
    }

    /// The three 13F segments (Holdings / Other stakes / History) — only for a 13F filer
    /// with a filing on file. Without one, every segment would describe a filing
    /// that does not exist ("No U.S.-listed holdings on this filing."); the screen lists the
    /// disclosed stakes instead.
    var showsThirteenFSegments: Bool { company.kind == .thirteenF && company.hasFilingOnFile }

    /// The History paywall — only when there is something behind it. A Free caller of a
    /// filer with no earlier quarter would otherwise be sold content that does not exist.
    var showsHistoryLock: Bool { isLocked && (lockedHistoryCount ?? 1) > 0 }

    /// The line for "no row changed". Only a real quarter-on-quarter comparison can have "no
    /// share-count changes"; a gap or a first filing is explained by `company.changeLine`.
    /// (The Changes segment that showed it was removed on 2026-09-24; the rule stays pinned
    /// by the parity harness for whatever shows change state next.)
    var changesEmptyText: String? {
        switch company.comparison {
        case .quarter?: return "No share-count changes vs the quarter before."
        case .gap?, .firstFiling?: return nil
        case .unknown?, nil: return "No comparison with an earlier quarter is available."
        }
    }

    /// Notes attached to 13F rows (a deal, a 13G figure) — shown under Holdings.
    var holdingNotes: [ClubStake] { stakes.filter { $0.kind == .on13FNote } }

    /// Everything else — the "Other stakes" segment (the card's `otherStakeCount`).
    var otherStakes: [ClubStake] { stakes.filter { $0.kind != .on13FNote } }

    /// "No longer reported: Arm Holdings, Snowflake" — the holdings that left this filing,
    /// under the Holdings list now that there is no Changes segment. Change rows exist only
    /// after a real quarter-on-quarter comparison (a gap or a first filing has none), so
    /// this never describes a comparison nobody made.
    var noLongerReportedText: String? {
        let names = changes.filter { $0.change == .noLongerReported }.map(\.name)
        guard !names.isEmpty else { return nil }
        return "No longer reported: " + names.joined(separator: ", ")
    }

    /// One line per change the Holdings list does NOT already show as a pill, then the
    /// holdings that left: "Newly reported: Arm Holdings", "Increased shares: Coherent",
    /// "No longer reported: Snowflake". A Free caller receives only the top 3 holdings but
    /// EVERY changed row (the server's "the latest changes are free"), so for Free this is
    /// where the rest of the quarter's changes are named; for Pro every changed holding is
    /// already a pill and only the "no longer reported" line appears. Empty after a gap or a
    /// first filing, when the model empties `changes`.
    var unlistedChangeLines: [String] {
        // Already drawn when a holdings row has the same name OR the same symbol — the two
        // lists come from the same 13F rows, and a change row may lack a symbol its holding has
        // (or the reverse); either match means it already shows as a pill.
        let drawnNames = Set(holdings.map(\.name))
        let drawnSymbols = Set(holdings.compactMap(\.symbol))
        func isDrawn(_ p: ClubPosition) -> Bool {
            drawnNames.contains(p.name) || p.symbol.map { drawnSymbols.contains($0) } == true
        }
        let kinds: [ClubChangeKind] = [.newlyReported, .increased, .decreased, .corporateAction]
        var lines: [String] = kinds.compactMap { kind in
            let names = changes.filter { $0.change == kind && !isDrawn($0) }.map(\.name)
            guard !names.isEmpty, let label = kind.pillLabel else { return nil }
            return "\(label): " + names.joined(separator: ", ")
        }
        if let gone = noLongerReportedText { lines.append(gone) }
        return lines
    }

    /// "+5 more holdings" — nil when nothing is withheld.
    var lockedHoldingsText: String? {
        guard lockedHoldingsCount > 0 else { return nil }
        return lockedHoldingsCount == 1 ? "+1 more holding" : "+\(TrillionClubFormat.grouped(lockedHoldingsCount)) more holdings"
    }

    /// The unchanged rows are counted, never listed — and only after a real comparison.
    var unchangedText: String? {
        guard company.comparison == .quarter, let n = company.changeCounts?.unchanged, n > 0 else { return nil }
        return n == 1 ? "1 holding unchanged" : "\(TrillionClubFormat.grouped(n)) holdings unchanged"
    }
}

// MARK: - Fixed copy

/// Section-level strings, in one place so the copy guard can see them.
nonisolated enum TrillionClubCopy {
    static let title = "Trillion-Dollar Club Bets"
    static let subtitle = "What the $1 trillion companies own in other companies"
    static let footer = "From SEC filings and company reports · Not a recommendation"
    static let detailFooter = "Informational only — not a recommendation to buy, sell or hold any security."
    static let membershipRule = "A company joins after 10 straight trading days closing at $1 trillion or more and leaves after 20 straight days below."
    static let lockedHoldingsHint = "See every holding with Pro"
    static let openProfile = "Open profile"
}

// MARK: - Sample data (previews and `MockHomeRepository`)
//
// Built by DECODING wire-shaped JSON through the real DTOs, so a preview exercises the same
// path production does and a drifted key shows up as a missing card, not a silent pass.
// Figures are from the filings the research verified (13F Q2 2026; 10-K/10-Q; 20-F;
// Berkshire's 2025 letter).

nonisolated enum TrillionClubSamples {
    static let groupJSON = #"""
    {
      "companies": [
        {
          "slug": "nvidia", "name": "NVIDIA", "card_kind": "thirteen_f",
          "logo_symbol": "NVDA", "detail_symbol": "NVDA",
          "market_cap": 5445000000000, "market_cap_as_of": "2026-09-23", "cap_is_manual": false,
          "period": "2026-Q2", "period_end": "2026-06-30", "filed_on": "2026-08-14",
          "next_due": "2026-11-16", "position_count": 8, "total_value": 63439974569,
          "top_holdings": [
            {"name": "Intel", "symbol": "INTC", "weight": 0.4727, "shares": 214776632,
             "value": 29989261126, "change": "unchanged", "sector": "Technology"},
            {"name": "SpaceX", "symbol": "SPCX", "weight": 0.3307, "shares": 122764805,
             "value": 20980000000, "change": "newly_reported", "newly_listed": true,
             "club_member_slug": "spacex"},
            {"name": "CoreWeave", "symbol": "CRWV", "weight": 0.0741, "shares": 47213353,
             "value": 4700000000, "change": "unchanged"}
          ],
          "change_counts": {"newly_reported": 1, "increased": 0, "decreased": 0,
                            "no_longer_reported": 0, "unchanged": 7, "corporate_action": 0},
          "comparison": "quarter", "prev_period": "2026-Q1", "stake_count": 4,
          "stakes": [
            {"investee_name": "Private companies (not named)", "kind": "private",
             "disclosed_value": 47900000000, "value_basis": "carrying_value",
             "as_of": "2026-07-26", "source_title": "NVIDIA 10-Q",
             "source_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1045810",
             "verified_on": "2026-09-24"},
            {"investee_name": "Anthropic", "kind": "commitment",
             "disclosed_value": 10000000000, "value_basis": "committed_up_to",
             "as_of": "2025-11-18", "source_title": "Microsoft, NVIDIA and Anthropic announcement",
             "source_url": "https://blogs.microsoft.com/blog/2025/11/18/microsoft-nvidia-and-anthropic-announce-strategic-partnerships/",
             "tied_to_deal": true, "verified_on": "2026-09-24"}
          ]
        },
        {
          "slug": "microsoft", "name": "Microsoft", "card_kind": "no_thirteen_f",
          "logo_symbol": "MSFT", "detail_symbol": "MSFT",
          "market_cap": 3717000000000, "market_cap_as_of": "2026-09-23", "stake_count": 3,
          "stakes": [
            {"investee_name": "OpenAI Group PBC", "kind": "private", "ownership_pct": 27,
             "ownership_basis": "as-converted", "as_of": "2025-10-28",
             "source_title": "Microsoft announcement",
             "source_url": "https://blogs.microsoft.com/blog/2025/10/28/the-next-chapter-of-the-microsoft-openai-partnership/",
             "tied_to_deal": true, "verified_on": "2026-09-24"},
            {"investee_name": "G42", "kind": "private", "disclosed_value": 1500000000,
             "value_basis": "invested", "as_of": "2024-04-16", "source_title": "Microsoft announcement",
             "source_url": "https://news.microsoft.com/source/2024/04/16/microsoft-invests-1-5-billion-in-abu-dhabis-g42-to-accelerate-ai-development-and-global-expansion/",
             "verified_on": "2026-09-24"},
            {"investee_name": "Anthropic", "kind": "commitment", "disclosed_value": 5000000000,
             "value_basis": "committed_up_to", "as_of": "2025-11-18",
             "source_title": "Microsoft, NVIDIA and Anthropic announcement",
             "source_url": "https://blogs.microsoft.com/blog/2025/11/18/microsoft-nvidia-and-anthropic-announce-strategic-partnerships/",
             "tied_to_deal": true, "verified_on": "2026-09-24"}
          ]
        },
        {
          "slug": "tsmc", "name": "TSMC", "card_kind": "non_us", "logo_symbol": "TSM",
          "detail_symbol": "TSM", "market_cap": 2316000000000, "market_cap_as_of": "2026-09-23",
          "stake_count": 3,
          "stakes": [
            {"investee_name": "Vanguard International Semiconductor", "kind": "non_us_listed",
             "ownership_pct": 27.6, "local_listing": "Taiwan", "as_of": "2026-02-28",
             "source_title": "TSMC 20-F (FY2025)",
             "source_url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm",
             "verified_on": "2026-09-24"},
            {"investee_name": "Global Unichip", "kind": "non_us_listed", "ownership_pct": 34.8,
             "local_listing": "Taiwan", "as_of": "2026-02-28", "source_title": "TSMC 20-F (FY2025)",
             "source_url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm",
             "verified_on": "2026-09-24"},
            {"investee_name": "Systems on Silicon Manufacturing", "kind": "private",
             "ownership_pct": 38.8, "local_listing": "Singapore", "as_of": "2026-02-28",
             "source_title": "TSMC 20-F (FY2025)",
             "source_url": "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362/tsm-20251231.htm",
             "verified_on": "2026-09-24"}
          ]
        },
        {
          "slug": "berkshire", "name": "Berkshire Hathaway", "card_kind": "whale_link",
          "logo_symbol": "BRK-B", "detail_symbol": "BRK-B",
          "market_cap": 1085000000000, "market_cap_as_of": "2026-09-23",
          "whale_id": "warren-buffett", "stake_count": 3,
          "stakes": [
            {"investee_name": "Mitsubishi", "kind": "non_us_listed", "ownership_pct": 10.8,
             "local_listing": "Japan", "as_of": "2025-12-31",
             "source_title": "Berkshire Hathaway 2025 shareholder letter",
             "source_url": "https://www.berkshirehathaway.com/letters/2025ltr.pdf", "verified_on": "2026-09-24"},
            {"investee_name": "Mitsui", "kind": "non_us_listed", "ownership_pct": 10.4,
             "local_listing": "Japan", "as_of": "2025-12-31",
             "source_title": "Berkshire Hathaway 2025 shareholder letter",
             "source_url": "https://www.berkshirehathaway.com/letters/2025ltr.pdf", "verified_on": "2026-09-24"},
            {"investee_name": "ITOCHU", "kind": "non_us_listed", "ownership_pct": 10.1,
             "local_listing": "Japan", "as_of": "2025-12-31",
             "source_title": "Berkshire Hathaway 2025 shareholder letter",
             "source_url": "https://www.berkshirehathaway.com/letters/2025ltr.pdf", "verified_on": "2026-09-24"}
          ]
        }
      ],
      "also_in_club": [
        {"slug": "broadcom", "name": "Broadcom"},
        {"slug": "spacex", "name": "SpaceX"},
        {"slug": "micron", "name": "Micron"}
      ]
    }
    """#

    /// NVIDIA's drill-down as a FREE caller receives it: top 3 holdings, the changes, every
    /// stake, no history, five holdings and three earlier quarters withheld.
    static let nvidiaDetailJSON = #"""
    {
      "company": {
        "slug": "nvidia", "name": "NVIDIA", "card_kind": "thirteen_f", "logo_symbol": "NVDA",
        "detail_symbol": "NVDA", "market_cap": 5445000000000, "market_cap_as_of": "2026-09-23",
        "period": "2026-Q2", "period_end": "2026-06-30", "filed_on": "2026-08-14",
        "next_due": "2026-11-16", "position_count": 8, "total_value": 63439974569,
        "change_counts": {"newly_reported": 1, "unchanged": 7},
        "comparison": "quarter", "prev_period": "2026-Q1", "stake_count": 4
      },
      "holdings": [
        {"name": "Intel", "symbol": "INTC", "weight": 0.4727, "shares": 214776632,
         "value": 29989261126, "change": "unchanged"},
        {"name": "SpaceX", "symbol": "SPCX", "weight": 0.3307, "shares": 122764805,
         "value": 20980000000, "change": "newly_reported", "newly_listed": true,
         "club_member_slug": "spacex"},
        {"name": "CoreWeave", "symbol": "CRWV", "weight": 0.0741, "shares": 47213353,
         "value": 4700000000, "change": "unchanged"}
      ],
      "changes": [
        {"name": "SpaceX", "symbol": "SPCX", "change": "newly_reported", "newly_listed": true,
         "shares": 122764805, "value": 20980000000, "weight": 0.3307}
      ],
      "stakes": [
        {"investee_name": "Intel", "kind": "on_13f_note", "symbol": "INTC",
         "disclosed_value": 5000000000, "value_basis": "invested", "as_of": "2025-09-18",
         "source_title": "NVIDIA announcement",
         "source_url": "https://nvidianews.nvidia.com/news/nvidia-and-intel-to-develop-ai-infrastructure-and-personal-computing-products",
         "tied_to_deal": true,
         "background": "Share purchase announced Sep 2025 with a chip co-development agreement.",
         "verified_on": "2026-09-24"},
        {"investee_name": "Nebius", "kind": "on_13f_note", "symbol": "NBIS", "ownership_pct": 9.3,
         "as_of": "2026-07-13", "source_title": "NVIDIA Schedule 13G",
         "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000062/0001045810-26-000062.txt",
         "verified_on": "2026-09-24"},
        {"investee_name": "Private companies (not named)", "kind": "private",
         "disclosed_value": 47900000000, "value_basis": "carrying_value", "as_of": "2026-07-26",
         "source_title": "NVIDIA 10-Q",
         "source_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1045810",
         "verified_on": "2026-09-24"},
        {"investee_name": "Anthropic", "kind": "commitment", "disclosed_value": 10000000000,
         "value_basis": "committed_up_to", "as_of": "2025-11-18",
         "source_title": "Microsoft, NVIDIA and Anthropic announcement",
         "source_url": "https://blogs.microsoft.com/blog/2025/11/18/microsoft-nvidia-and-anthropic-announce-strategic-partnerships/",
         "tied_to_deal": true, "verified_on": "2026-09-24"}
      ],
      "history": [],
      "is_locked": true, "tier_required": "pro", "locked_holdings_count": 5,
      "locked_history_count": 3,
      "other_members": [{"slug": "broadcom", "name": "Broadcom"}, {"slug": "micron", "name": "Micron"}]
    }
    """#

    static let group: TrillionClubGroup = decodeGroup(groupJSON)

    static let nvidiaDetailLocked: TrillionClubDetail? = decodeDetail(nvidiaDetailJSON)

    static func decodeGroup(_ json: String) -> TrillionClubGroup {
        do {
            return TrillionClubGroup(dto: try JSONDecoder().decode(TrillionClubGroupDTO.self, from: Data(json.utf8)))
        } catch {
            TrillionClubLog.logger.error("trillion club samples: group JSON unreadable: \(String(describing: error), privacy: .public)")
            return .empty
        }
    }

    static func decodeDetail(_ json: String) -> TrillionClubDetail? {
        do {
            return TrillionClubDetail(dto: try JSONDecoder().decode(TrillionClubDetailDTO.self, from: Data(json.utf8)))
        } catch {
            TrillionClubLog.logger.error("trillion club samples: detail JSON unreadable: \(String(describing: error), privacy: .public)")
            return nil
        }
    }
}
