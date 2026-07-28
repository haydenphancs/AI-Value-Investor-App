//
//  SubscriptionModels.swift
//  ios
//
//  DTOs for the tier catalog (GET /billing/plans) and the current user's
//  subscription (GET /users/me/subscription). Read-only on the client — tier
//  changes are written server-side by receipt validation, never self-assigned.
//
//  The APIClient decoder does NOT use .convertFromSnakeCase, so every DTO maps
//  snake_case → camelCase with explicit CodingKeys.
//

import Foundation

// MARK: - Plan (one tier row)

struct PlanDTO: Codable, Identifiable, Sendable, Equatable {
    let tier: String            // free | pro | premium
    let displayName: String     // "Free" | "Pro" | "Max"
    let monthlyCredits: Int
    let priceCents: Int
    let priceLabel: String      // precomputed ("Free" | "$14.99")

    var id: String { tier }

    /// Map the backend tier string to the app's `UserTier` enum.
    var userTier: UserTier { UserTier(rawValue: tier) ?? .free }

    enum CodingKeys: String, CodingKey {
        case tier
        case displayName = "display_name"
        case monthlyCredits = "monthly_credits"
        case priceCents = "price_cents"
        case priceLabel = "price_label"
    }
}

// MARK: - Plan catalog (the paywall's data source)

struct PlanCatalog: Codable, Sendable, Equatable {
    let plans: [PlanDTO]
    let reportCost: Int
    let chatCost: Int

    enum CodingKeys: String, CodingKey {
        case plans
        case reportCost = "report_cost"
        case chatCost = "chat_cost"
    }
}

// MARK: - Current subscription

struct SubscriptionDTO: Codable, Sendable, Equatable {
    let tier: String
    let displayName: String
    let status: String                  // active | grace | expired | canceled
    let currentPeriodEnd: String?       // ISO-8601
    let store: String?                  // apple | stripe | promo

    var userTier: UserTier { UserTier(rawValue: tier) ?? .free }

    enum CodingKeys: String, CodingKey {
        case tier
        case displayName = "display_name"
        case status
        case currentPeriodEnd = "current_period_end"
        case store
    }
}
