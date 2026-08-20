//
//  WhaleDTOs.swift
//  ios
//
//  Codable DTOs for whale API responses.
//  Maps backend snake_case JSON to Swift camelCase view models.
//

import Foundation

// MARK: - Trending Whale DTO

struct TrendingWhaleDTO: Codable, Identifiable {
    let id: String
    let name: String
    let category: String
    let avatarUrl: String?
    let followersCount: Int
    let isFollowing: Bool
    let title: String
    let description: String
    let recentTradeCount: Int
    /// Firm a person-fronted whale runs ("Bridgewater Associates" for Ray
    /// Dalio). Optional — nil for institutions/politicians and old backends.
    let firmName: String?
    /// This caller's plan does not allow TRACKING this whale (Free outside its one free
    /// slot, or Pro at its limit). The row still renders in full — only the Follow button
    /// locks. Optional so an older backend reads as absent → `?? false` → unlocked, i.e.
    /// exactly today's behaviour rather than a phantom lock.
    let isLocked: Bool?
    /// The caller follows this whale but their plan doesn't surface it (the activity feed
    /// truncates to the covered subset). Optional for the same reason as `isLocked`: an
    /// older backend reads as absent → `?? false` → today's behaviour, no phantom badge.
    let isFollowingInactive: Bool?
    /// Whether this filer is still producing data — a fund can deregister, a politician
    /// can retire or simply stop trading. `""`/nil means "not computed" and the row shows
    /// no chip. Optional so an older backend reads as absent rather than as a decode
    /// failure, the same reason `isLocked` is.
    let activityStatus: String?
    /// The sentence the chip renders, e.g. "Last filed Q3 2025". The client never builds
    /// this copy from the status enum — the server owns the wording.
    let activityLabel: String?

    enum CodingKeys: String, CodingKey {
        case id, name, category, title, description
        case avatarUrl = "avatar_url"
        case followersCount = "followers_count"
        case isFollowing = "is_following"
        case recentTradeCount = "recent_trade_count"
        case firmName = "firm_name"
        case isLocked = "is_locked"
        case isFollowingInactive = "is_following_inactive"
        case activityStatus = "activity_status"
        case activityLabel = "activity_label"
    }

    func toTrendingWhale() -> TrendingWhale {
        TrendingWhale(
            id: id,
            name: name,
            category: WhaleCategory.fromBackend(category),
            avatarName: avatarUrl ?? "",
            followersCount: followersCount,
            isFollowing: isFollowing,
            title: title,
            description: description,
            recentTradeCount: recentTradeCount,
            firmName: firmName,
            isLocked: isLocked ?? false,
            isFollowingInactive: isFollowingInactive ?? false,
            activityStatus: activityStatus ?? "",
            activityLabel: activityLabel ?? ""
        )
    }
}

// MARK: - Whale Trade Group Activity DTO

struct WhaleTradeGroupActivityDTO: Codable, Identifiable {
    let id: String
    let whaleId: String
    let entityName: String
    let entityAvatarName: String
    let entityFirmName: String?
    let category: String?
    let action: String
    let tradeCount: Int
    let totalAmount: String
    let summary: String?
    let date: String

    enum CodingKeys: String, CodingKey {
        case id, category, action, summary, date
        case whaleId = "whale_id"
        case entityName = "entity_name"
        case entityAvatarName = "entity_avatar_name"
        case entityFirmName = "entity_firm_name"
        case tradeCount = "trade_count"
        case totalAmount = "total_amount"
    }

    func toWhaleTradeGroupActivity() -> WhaleTradeGroupActivity {
        WhaleTradeGroupActivity(
            id: id,
            whaleId: whaleId,
            entityName: entityName,
            entityAvatarName: entityAvatarName,
            entityFirmName: entityFirmName,
            category: category.map { WhaleCategory.fromBackend($0) },
            action: WhaleAction(rawValue: action) ?? .bought,
            tradeCount: tradeCount,
            totalAmount: totalAmount,
            summary: summary,
            date: DateParser.parseDate(date)
        )
    }
}

// MARK: - Whale Profile DTO

struct WhaleProfileDTO: Codable {
    let id: String
    let name: String
    let title: String
    let description: String
    let firmName: String?
    let avatarUrl: String?
    let riskProfile: String
    let portfolioValue: Double
    let ytdReturn: Double
    let sectorExposure: [WhaleSectorAllocationDTO]
    let currentHoldings: [WhaleHoldingDTO]
    let recentTradeGroups: [WhaleTradeGroupDTO]
    let recentTrades: [WhaleTradeDTO]
    let behaviorSummary: WhaleBehaviorSummaryDTO
    let sentimentSummary: String
    let isFollowing: Bool
    let dataSource: String?
    let returnSource: String?
    let returnLabel: String?
    /// Stat-tile provenance (migration 127). ALL Optional — Swift synthesises
    /// `decodeIfPresent` only for Optionals, so a non-Optional-with-default would
    /// throw `keyNotFound` against a backend that predates these fields and fail
    /// the WHOLE profile decode.
    let returnStatus: String?
    let returnWindowYears: Int?
    let portfolioStatus: String?
    let portfolioAsOf: String?
    let filingDate: String?
    /// Pro/Max gate. When true the backend has WITHHELD the position-level sections —
    /// `currentHoldings`, `recentTradeGroups`, `recentTrades` arrive empty,
    /// `sentimentSummary` blank and `behaviorSummary` neutral — while the header, stat
    /// tiles and `sectorExposure` are intact. Optional for the same reason as the
    /// provenance block above: absent must mean unlocked, not a decode failure.
    /// Activity disclosure (migration 145). All Optional for the same reason as the
    /// provenance block above: absent must mean "nothing to say", not a decode failure.
    let activityStatus: String?
    let activityLabel: String?
    let lastActivityDate: String?
    /// CURATED prose written by a human, shown verbatim. Overrides `activityLabel` — a
    /// stated reason beats an inferred one. "Nancy Pelosi retired" can never be derived.
    let lifecycleNote: String?
    let isLocked: Bool?
    /// The plan that unlocks ("pro"), echoed from the server so the client never carries a
    /// second copy of the tier ladder.
    let tierRequired: String?

    enum CodingKeys: String, CodingKey {
        case id, name, title, description
        case firmName = "firm_name"
        case avatarUrl = "avatar_url"
        case riskProfile = "risk_profile"
        case portfolioValue = "portfolio_value"
        case ytdReturn = "ytd_return"
        case sectorExposure = "sector_exposure"
        case currentHoldings = "current_holdings"
        case recentTradeGroups = "recent_trade_groups"
        case recentTrades = "recent_trades"
        case behaviorSummary = "behavior_summary"
        case sentimentSummary = "sentiment_summary"
        case isFollowing = "is_following"
        case dataSource = "data_source"
        case returnSource = "return_source"
        case returnLabel = "return_label"
        case returnStatus = "return_status"
        case returnWindowYears = "return_window_years"
        case portfolioStatus = "portfolio_status"
        case portfolioAsOf = "portfolio_as_of"
        case filingDate = "filing_date"
        case activityStatus = "activity_status"
        case activityLabel = "activity_label"
        case lastActivityDate = "last_activity_date"
        case lifecycleNote = "lifecycle_note"
        case isLocked = "is_locked"
        case tierRequired = "tier_required"
    }

    func toWhaleProfile() -> WhaleProfile {
        WhaleProfile(
            id: id,
            name: name,
            title: title,
            description: description,
            firmName: firmName,
            avatarURL: avatarUrl,
            riskProfile: WhaleRiskProfile.fromBackend(riskProfile),
            portfolioValue: portfolioValue,
            ytdReturn: ytdReturn,
            sectorExposure: sectorExposure.map { $0.toWhaleSectorAllocation() },
            currentHoldings: currentHoldings.map { $0.toWhaleHolding() },
            recentTradeGroups: recentTradeGroups.map { $0.toWhaleTradeGroup() },
            recentTrades: recentTrades.map { $0.toWhaleTrade() },
            behaviorSummary: behaviorSummary.toWhaleBehaviorSummary(),
            sentimentSummary: sentimentSummary,
            isFollowing: isFollowing,
            dataSource: dataSource ?? "",
            returnLabel: returnLabel ?? "",
            // `returnSource` was already decoded and then DROPPED here. The
            // stat tiles need it: for the five whales with an associated
            // ticker it is the only signal that the percent is a share-price
            // CAGR rather than a 13F figure.
            // Raw string kept alongside the parsed enum so the badge can hide when the
            // backend has no classification (see WhaleProfile.hasRiskProfile).
            activityStatus: activityStatus ?? "",
            activityLabel: activityLabel ?? "",
            lastActivityDate: lastActivityDate,
            lifecycleNote: lifecycleNote,
            riskProfileRaw: riskProfile,
            returnSource: returnSource ?? "",
            returnStatus: returnStatus ?? "",
            returnWindowYears: returnWindowYears,
            portfolioStatus: portfolioStatus ?? "",
            portfolioAsOf: portfolioAsOf,
            filingDate: filingDate,
            // Absent → unlocked. An older backend served every section in full, so reading
            // a missing flag as locked would hide data the user is entitled to.
            isLocked: isLocked ?? false,
            tierRequired: tierRequired
        )
    }
}

// MARK: - Whale Sector Allocation DTO

struct WhaleSectorAllocationDTO: Codable {
    let id: String
    let name: String
    let percentage: Double
    let colorHex: String

    enum CodingKeys: String, CodingKey {
        case id, name, percentage
        case colorHex = "color_hex"
    }

    func toWhaleSectorAllocation() -> WhaleSectorAllocation {
        WhaleSectorAllocation(id: id, name: name, percentage: percentage, colorHex: colorHex)
    }
}

// MARK: - Whale Holding DTO

struct WhaleHoldingDTO: Codable {
    let id: String
    let ticker: String
    let companyName: String
    let logoUrl: String?
    let allocation: Double
    let changePercent: Double
    let assetType: String?

    enum CodingKeys: String, CodingKey {
        case id, ticker, allocation
        case companyName = "company_name"
        case logoUrl = "logo_url"
        case changePercent = "change_percent"
        case assetType = "asset_type"
    }

    func toWhaleHolding() -> WhaleHolding {
        WhaleHolding(
            id: id,
            ticker: ticker,
            companyName: CompanyNameFormatter.clean(companyName),
            logoURL: logoUrl,
            allocation: allocation,
            changePercent: changePercent,
            assetType: assetType ?? "stock"
        )
    }
}

// MARK: - Whale Trade Group DTO

struct WhaleTradeGroupDTO: Codable {
    let id: String
    let date: String
    let tradeCount: Int
    let netAction: String
    let netAmount: Double
    let netAmountRange: String?
    let disclosureDate: String?
    let transactionDate: String?
    let summary: String?
    let insights: [String]
    let trades: [WhaleTradeDTO]

    enum CodingKeys: String, CodingKey {
        case id, date, summary, insights, trades
        case tradeCount = "trade_count"
        case netAction = "net_action"
        case netAmount = "net_amount"
        case netAmountRange = "net_amount_range"
        case disclosureDate = "disclosure_date"
        case transactionDate = "transaction_date"
    }

    func toWhaleTradeGroup() -> WhaleTradeGroup {
        WhaleTradeGroup(
            id: id,
            date: DateParser.parseDate(date),
            tradeCount: tradeCount,
            netAction: WhaleTradeAction(rawValue: netAction) ?? .bought,
            netAmount: netAmount,
            netAmountRange: netAmountRange,
            disclosureDate: disclosureDate.flatMap { DateParser.parseDateOptional($0) },
            transactionDate: transactionDate.flatMap { DateParser.parseDateOptional($0) },
            summary: summary,
            insights: insights,
            trades: trades.map { $0.toWhaleTrade() }
        )
    }
}

// MARK: - Whale Trade DTO

struct WhaleTradeDTO: Codable {
    let id: String
    let ticker: String
    let companyName: String
    let action: String
    let tradeType: String
    let amount: Double
    let amountRange: String?
    let previousAllocation: Double
    let newAllocation: Double
    let date: String
    let disclosureDate: String?
    let assetType: String?

    enum CodingKeys: String, CodingKey {
        case id, ticker, action, amount, date
        case companyName = "company_name"
        case tradeType = "trade_type"
        case amountRange = "amount_range"
        case previousAllocation = "previous_allocation"
        case newAllocation = "new_allocation"
        case disclosureDate = "disclosure_date"
        case assetType = "asset_type"
    }

    func toWhaleTrade() -> WhaleTrade {
        WhaleTrade(
            id: id,
            ticker: ticker,
            companyName: CompanyNameFormatter.clean(companyName),
            action: WhaleTradeAction(rawValue: action) ?? .bought,
            tradeType: WhaleTradeType(rawValue: tradeType) ?? .increased,
            amount: amount,
            amountRange: amountRange,
            previousAllocation: previousAllocation,
            newAllocation: newAllocation,
            date: DateParser.parseDate(date),
            disclosureDate: disclosureDate.flatMap { DateParser.parseDateOptional($0) },
            assetType: assetType ?? "stock"
        )
    }
}

// MARK: - Whale Behavior Summary DTO

struct WhaleBehaviorSummaryDTO: Codable {
    let action: String
    let primaryFocus: String
    let secondaryAction: String
    let secondaryFocus: String

    enum CodingKeys: String, CodingKey {
        case action
        case primaryFocus = "primary_focus"
        case secondaryAction = "secondary_action"
        case secondaryFocus = "secondary_focus"
    }

    func toWhaleBehaviorSummary() -> WhaleBehaviorSummary {
        WhaleBehaviorSummary(
            action: action,
            primaryFocus: primaryFocus,
            secondaryAction: secondaryAction,
            secondaryFocus: secondaryFocus
        )
    }
}

// MARK: - Follow Response DTO

struct FollowResponseDTO: Codable {
    let isFollowing: Bool
    let followersCount: Int

    enum CodingKeys: String, CodingKey {
        case isFollowing = "is_following"
        case followersCount = "followers_count"
    }
}

// MARK: - Lenient Array Decoding

/// Decodes an array, DROPPING elements that fail to decode instead of failing the
/// whole array.
///
/// `Swift`'s synthesised `[T]` decode is all-or-nothing: one malformed row — a NULL in
/// a non-Optional field, a renamed key, a type that drifted — throws, and the caller
/// receives nothing. For the whale roster that meant a single bad `whales` row emptied
/// the ENTIRE Whales tab, with `TrackingViewModel` then retrying three times and giving
/// up. Losing one investor is strictly better than losing all 53.
///
/// Every drop is logged: a silent skip is how a contract drift survives unnoticed, and
/// there is no XCTest target here to catch it.
/// Consumes exactly one element of an unkeyed container without inspecting it, so a
/// failed decode cannot desynchronise the cursor and cascade into the following rows.
/// Declared at file scope because Swift forbids nesting a type inside a generic function.
private struct _SkippedElement: Decodable {}

struct LenientArray<Element: Decodable>: Decodable {
    let elements: [Element]
    let droppedCount: Int

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var decoded: [Element] = []
        var dropped = 0
        while !container.isAtEnd {
            do {
                decoded.append(try container.decode(Element.self))
            } catch {
                dropped += 1
                _ = try? container.decode(_SkippedElement.self)
                print("[LenientArray] ⚠️ Dropped a malformed \(Element.self): \(error)")
            }
        }
        self.elements = decoded
        self.droppedCount = dropped
    }
}

// MARK: - Date Parser

enum DateParser {
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    private static let isoFormatter = ISO8601DateFormatter()

    /// Optional parse — returns nil (never a fabricated "now") on empty/bad input.
    static func parseDateOptional(_ dateString: String) -> Date? {
        if dateString.isEmpty { return nil }
        if let date = dateFormatter.date(from: dateString) { return date }
        if let date = isoFormatter.date(from: dateString) { return date }
        #if DEBUG
        print("[DateParser] ⚠️ Failed to parse date: \(dateString)")
        #endif
        return nil
    }

    /// Non-optional parse. On failure returns `.distantPast` (a sentinel the
    /// formatters treat as "date unavailable") — NOT `Date()`, which would
    /// masquerade an unparseable date as a fabricated "Today".
    static func parseDate(_ dateString: String) -> Date {
        parseDateOptional(dateString) ?? .distantPast
    }
}
