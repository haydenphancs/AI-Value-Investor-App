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

// MARK: - Credit pack (one consumable row)

/// One consumable credit pack from `GET /billing/credit-packs`.
///
/// `credits` is authoritative — the same value the server grants on a verified purchase, so
/// what this screen promises can never disagree with what lands in the balance. `priceLabel`
/// is a FALLBACK only: the price actually charged is Apple's localized `displayPrice`, read
/// from the matching StoreKit `Product`. Showing "$9.99" while Apple charges €10.99 is a
/// refund request and an App Review note, so the StoreKit value always wins when present.
struct CreditPackDTO: Codable, Identifiable, Sendable, Equatable {
    let productId: String       // must match App Store Connect exactly
    let displayName: String     // "Starter" | "Plus" | "Power" | "Mega"
    let credits: Int
    let priceCents: Int
    let priceLabel: String      // precomputed ("$4.99") — fallback only
    let sortOrder: Int

    var id: String { productId }

    enum CodingKeys: String, CodingKey {
        case productId = "product_id"
        case displayName = "display_name"
        case credits
        case priceCents = "price_cents"
        case priceLabel = "price_label"
        case sortOrder = "sort_order"
    }
}

// MARK: - Credit pack catalog (the Buy Credits screen's data source)

struct CreditPackCatalog: Codable, Sendable, Equatable {
    let packs: [CreditPackDTO]
    let reportCost: Int
    let chatCost: Int

    enum CodingKeys: String, CodingKey {
        case packs
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


// MARK: - Purchase verification

/// Response from `POST /billing/verify` — the entitlement state after the server verified
/// an Apple-signed transaction.
struct VerifyPurchaseResponse: Codable, Sendable, Equatable {
    /// The WINNING tier across all of the user's subscriptions, not just this transaction's,
    /// so replaying an old Pro receipt can't appear to demote a Max subscriber.
    let tier: String
    let status: String
    let currentPeriodEnd: String?
    /// True when this transaction had already been applied (StoreKit replays on launch,
    /// restore re-submits). Informational, not an error.
    let wasReplay: Bool

    // MARK: Consumable credit packs
    //
    // All THREE are Optional even though the backend defaults the first two, because the app
    // and the backend deploy independently: a build carrying this type can hit a Railway
    // instance that predates credit packs, and a non-Optional field would fail to decode a
    // purchase the user has already paid for. Same deliberate over-tolerance as
    // `PasswordChangedResponse` (see APIEndpoint.swift).

    /// `"subscription"` or `"credit_pack"`. Absent on an older backend → treated as a
    /// subscription, which is what it would have been.
    let kind: String?
    /// Credits added by THIS delivery. 0 on a replay — never claim credits were added that
    /// the user can check against their balance and find missing.
    let creditsGranted: Int?
    /// Full spendable balance after applying this transaction, so the success screen can show
    /// a number without racing the `.caydexEntitlementChanged` refresh.
    let creditsSpendable: Int?

    var userTier: UserTier { UserTier(rawValue: tier) ?? .free }

    var isCreditPack: Bool { kind == "credit_pack" }

    enum CodingKeys: String, CodingKey {
        case tier, status, kind
        case currentPeriodEnd = "current_period_end"
        case wasReplay = "was_replay"
        case creditsGranted = "credits_granted"
        case creditsSpendable = "credits_spendable"
    }
}

// MARK: - Applied purchase (what the app acts on)

/// What the backend actually did with a verified transaction.
///
/// `StoreKitService.handle` used to return a bare tier `String`, which cannot express a
/// consumable purchase: a pack grants credits and leaves the tier alone, so a tier string
/// would have made a $19.99 top-up indistinguishable from a no-op. Widening the return type
/// (rather than adding a parallel `purchaseCredits()` path) keeps the finish/notify/
/// terminal-error handling in ONE place — that logic is the reason StoreKitService exists.
struct PurchaseApplied: Sendable, Equatable {
    /// `"subscription"` or `"credit_pack"`.
    let kind: String
    /// The user's WINNING tier — for a pack this is their existing tier, never "free".
    let tier: String
    /// Credits added by this delivery. 0 for a subscription, and 0 for a replayed pack.
    let creditsGranted: Int
    /// Spendable balance after the grant, when the server reported one.
    let creditsSpendable: Int?
    let wasReplay: Bool

    var isCreditPack: Bool { kind == "credit_pack" }

    init(from response: VerifyPurchaseResponse) {
        self.kind = response.kind ?? "subscription"
        self.tier = response.tier
        self.creditsGranted = response.creditsGranted ?? 0
        self.creditsSpendable = response.creditsSpendable
        self.wasReplay = response.wasReplay
    }
}
