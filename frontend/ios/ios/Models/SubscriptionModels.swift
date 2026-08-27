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
import SwiftUI
import UIKit

// MARK: - Plan feature (one paywall row)

/// One row on the upgrade screen, served by `backend/app/services/plan_features.py`.
///
/// The numbers in `title` are read from `entitlements.py` — the same module that enforces
/// the gate — so the paywall cannot drift from what the server actually allows. That is
/// the entire reason this crosses the wire instead of living in a Swift constant.
///
/// ⚠️ EVERY FIELD DECODES WITH A DEFAULT. This shape is new, so a build carrying it can
/// reach a Railway instance that predates it, and a stricter decode would turn a missing
/// key into a blank upgrade screen — a failure that looks like a working screen.
struct PlanFeatureDTO: Codable, Identifiable, Sendable, Equatable {
    let key: String
    let title: String
    let detail: String
    let icon: String
    let accent: String
    let included: Bool
    let group: String

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, title, detail, icon, accent, included, group
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // `try?` per field, not just `decodeIfPresent`: a wrong-TYPED value (a number
        // where a string belongs) throws even when the key is present, and one such field
        // would otherwise discard the whole row.
        key = (try? c.decodeIfPresent(String.self, forKey: .key)) ?? ""
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        detail = (try? c.decodeIfPresent(String.self, forKey: .detail)) ?? ""
        icon = (try? c.decodeIfPresent(String.self, forKey: .icon)) ?? ""
        accent = (try? c.decodeIfPresent(String.self, forKey: .accent)) ?? "neutral"
        included = (try? c.decodeIfPresent(Bool.self, forKey: .included)) ?? true
        group = (try? c.decodeIfPresent(String.self, forKey: .group)) ?? "plan"
    }

    /// Memberwise init for previews and tests (the custom `init(from:)` suppresses the
    /// synthesized one).
    init(
        key: String,
        title: String,
        detail: String,
        icon: String,
        accent: String,
        included: Bool = true,
        group: String = "plan"
    ) {
        self.key = key
        self.title = title
        self.detail = detail
        self.icon = icon
        self.accent = accent
        self.included = included
        self.group = group
    }
}

// MARK: - Plan (one tier row)

struct PlanDTO: Codable, Identifiable, Sendable, Equatable {
    let tier: String            // free | pro | premium
    let displayName: String     // "Free" | "Pro" | "Max"
    let monthlyCredits: Int
    let priceCents: Int
    let priceLabel: String      // precomputed ("Free" | "$14.99")
    /// Paywall rows. `nil` or empty → the client renders `PlanFeature.bundled(...)`.
    let features: [PlanFeatureDTO]?

    var id: String { tier }

    /// Map the backend tier string to the app's `UserTier` enum.
    var userTier: UserTier { UserTier(rawValue: tier) ?? .free }

    enum CodingKeys: String, CodingKey {
        case tier
        case displayName = "display_name"
        case monthlyCredits = "monthly_credits"
        case priceCents = "price_cents"
        case priceLabel = "price_label"
        case features
    }

    /// Hand-written so a malformed `features` array cannot take the plan down with it.
    ///
    /// `Optional` alone is NOT enough: the synthesized init still THROWS if an element
    /// isn't a keyed container (a bare string in the array, a `features: {}` object). That
    /// throw propagates out of the plan, out of `PlanCatalog`, and the whole upgrade screen
    /// shows a network error for a field that is decorative. `try?` degrades exactly that
    /// case to the bundled fallback. The five original fields stay strict — losing `tier`
    /// or `priceLabel` genuinely is unrecoverable.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tier = try c.decode(String.self, forKey: .tier)
        displayName = try c.decode(String.self, forKey: .displayName)
        monthlyCredits = try c.decode(Int.self, forKey: .monthlyCredits)
        priceCents = try c.decode(Int.self, forKey: .priceCents)
        priceLabel = try c.decode(String.self, forKey: .priceLabel)
        features = try? c.decodeIfPresent([PlanFeatureDTO].self, forKey: .features)
    }

    /// Memberwise init for previews and the bundled fallback path (the custom `init(from:)`
    /// suppresses the synthesized one).
    init(
        tier: String,
        displayName: String,
        monthlyCredits: Int,
        priceCents: Int,
        priceLabel: String,
        features: [PlanFeatureDTO]? = nil
    ) {
        self.tier = tier
        self.displayName = displayName
        self.monthlyCredits = monthlyCredits
        self.priceCents = priceCents
        self.priceLabel = priceLabel
        self.features = features
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

    /// The rows to render for one plan, already clamped and with the fallback applied.
    func features(for plan: PlanDTO) -> [PlanFeature] {
        PlanFeature.rows(
            from: plan.features,
            tier: plan.userTierIfKnown,
            monthlyCredits: plan.monthlyCredits,
            reportCost: reportCost,
            chatCost: chatCost
        )
    }
}

// MARK: - Plan feature (UI model)

/// The view-facing form of a paywall row: colours resolved to audited tokens and the SF
/// Symbol validated, so `PlanFeatureRowView` takes one type whether the row arrived over
/// the wire or came from the bundled fallback.
struct PlanFeature: Identifiable, Equatable, Sendable {
    let key: String
    let title: String
    let detail: String
    /// `nil` when the server sent a symbol this OS build does not have — the row then
    /// renders without a glyph rather than with SwiftUI's placeholder box.
    let symbol: String?
    let accentKey: String
    let included: Bool
    /// True for the "Included on every plan" block — identical on all three cards.
    let isAlwaysIncluded: Bool

    var id: String { key }

    /// Server accent key → an AUDITED text-role `AppColors` token.
    ///
    /// Semantic keys rather than a hex on the wire, deliberately. `Color(themedHex:role:)`
    /// exists for values the backend genuinely owns (persona hues); this vocabulary is
    /// ours and fixed, so mapping it client-side keeps every colour inside
    /// `AppColors.auditManifest` where `ThemeContrastAudit` already proves it in both
    /// appearances. TEXT role, not `*Graphic`: `IconTile` draws the glyph IN this colour
    /// and it reads as a meaningful icon, so it must clear 4.5:1.
    ///
    /// ⚠️ Every case here must exist in `plan_features.ACCENTS` on the backend, and vice
    /// versa — pinned by `tests/test_paywall_copy_guards.py`.
    static func accentColor(_ key: String) -> Color {
        switch key {
        case "credits": return AppColors.alertOrange
        case "updates": return AppColors.primaryBlue
        case "signals": return AppColors.accentCyan
        case "whales": return AppColors.alertPurple
        case "learn": return AppColors.gain
        case "neutral": return AppColors.textSecondary
        // An unrecognised key is a newer backend, not a bug worth blanking the row for.
        default: return AppColors.textSecondary
        }
    }

    var accent: Color { PlanFeature.accentColor(accentKey) }

    /// Validate at the MODEL boundary, not in the view — the same discipline the theme
    /// rules impose on server-supplied colour. `Image(systemName:)` on an unknown name
    /// draws a placeholder box, which reads as a broken screen.
    ///
    /// The implementation moved to `AppSymbols.resolved` so the Money Moves decoder can share
    /// it — this stays as the named entry point the plan-feature path already reads.
    static func validatedSymbol(_ name: String) -> String? {
        AppSymbols.resolved(name)
    }

    init(from dto: PlanFeatureDTO) {
        key = dto.key
        title = dto.title
        detail = dto.detail
        symbol = PlanFeature.validatedSymbol(dto.icon)
        accentKey = dto.accent
        included = dto.included
        isAlwaysIncluded = dto.group == "always"
    }

    init(
        key: String,
        title: String,
        detail: String,
        symbol: String?,
        accentKey: String,
        included: Bool = true,
        isAlwaysIncluded: Bool = false
    ) {
        self.key = key
        self.title = title
        self.detail = detail
        self.symbol = symbol
        self.accentKey = accentKey
        self.included = included
        self.isAlwaysIncluded = isAlwaysIncluded
    }

    /// Wire rows if there are any usable ones, else the bundled table.
    ///
    /// A row with no title carries nothing and is dropped: the fields all decode with
    /// defaults, so a malformed row arrives as blanks rather than as an error, and an
    /// empty row would render as an unexplained gap.
    static func rows(
        from dtos: [PlanFeatureDTO]?,
        tier: UserTier?,
        monthlyCredits: Int,
        reportCost: Int,
        chatCost: Int
    ) -> [PlanFeature] {
        let served = (dtos ?? [])
            // `.map { … }`, not `.map(PlanFeature.init(from:))` — an unapplied initializer
            // reference becomes a *nonisolated* function type. See HomeRepository's note.
            .map { PlanFeature(from: $0) }
            .filter { !$0.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        if !served.isEmpty { return served }

        // Unknown tier → nothing, never Free's rows under another plan's header. Matches
        // `plan_features.features_for_tier`, which returns [] for the same reason.
        guard let tier else { return [] }
        return bundled(
            for: tier,
            monthlyCredits: monthlyCredits,
            reportCost: reportCost,
            chatCost: chatCost
        )
    }
}

extension PlanDTO {
    /// `userTier` folds an unrecognised tier to `.free`, which is right for entitlement
    /// checks and wrong for rendering a plan card — it would label another plan with
    /// Free's copy. This one stays nil instead.
    var userTierIfKnown: UserTier? { UserTier(rawValue: tier) }
}

// MARK: - Bundled fallback

extension PlanFeature {
    /// Offline mirror of `backend/app/services/plan_features.py`.
    ///
    /// WHY THIS EXISTS AT ALL, given the whole point was to serve the copy:
    /// the app and Railway deploy independently, and a TestFlight build can be in a
    /// reviewer's hands before the backend ships. Without this the upgrade screen would
    /// render three bare price tags — which looks like a finished screen, so nobody would
    /// report it. It also covers a `plan_credits` outage that predates the fallback
    /// catalog, and any future response the client cannot parse.
    ///
    /// ⚠️ The credit figures are computed from THIS PLAN's `monthlyCredits`, which is on
    /// the wire even on a pre-features backend — so the number most likely to be retuned
    /// never goes stale here. The ticker and follow limits are the residual duplication,
    /// and `backend/tests/test_paywall_copy_guards.py` pins them against
    /// `entitlements.UPDATES_TICKER_LIMITS` / `WHALE_FOLLOW_LIMITS` so they cannot drift
    /// silently. Do not add a third hardcoded number here without extending that guard.
    static func bundled(
        for tier: UserTier,
        monthlyCredits: Int,
        reportCost: Int,
        chatCost: Int
    ) -> [PlanFeature] {
        // Mirrors entitlements.UPDATES_TICKER_LIMITS / WHALE_FOLLOW_LIMITS.
        // `whaleFollows == nil` means unlimited.
        let updatesTickers: Int
        let whaleFollows: Int?
        switch tier {
        case .free:    updatesTickers = 1;  whaleFollows = 1
        case .pro:     updatesTickers = 15; whaleFollows = 10
        case .premium: updatesTickers = 30; whaleFollows = nil
        }
        let paid = tier != .free

        return [
            PlanFeature(
                key: "credits",
                title: "\(formatted(monthlyCredits)) credits a month",
                detail: creditsDetail(monthlyCredits, reportCost: reportCost, chatCost: chatCost),
                symbol: validatedSymbol("creditcard.fill"),
                accentKey: "credits"
            ),
            PlanFeature(
                key: "updates_tickers",
                title: "\(updatesTickers) watchlist \(updatesTickers == 1 ? "ticker" : "tickers") in Updates",
                detail: updatesTickers <= 1
                    ? "The Market feed is free on every plan. Your first ticker alphabetically gets its own news tab with an AI insight card."
                    : "Each gets its own news tab with an AI insight card, alongside the always-free Market feed.",
                symbol: validatedSymbol("chart.bar.doc.horizontal"),
                accentKey: "updates"
            ),
            PlanFeature(
                key: "signals",
                title: "Signal tickers",
                detail: paid
                    ? "See the exact ticker behind every Congress, institutional and earnings signal, plus the investors driving it."
                    : "Free shows every App-Exclusive Signal and how many investors are moving — the ticker symbols stay hidden.",
                symbol: validatedSymbol("antenna.radiowaves.left.and.right"),
                accentKey: "signals",
                included: paid
            ),
            PlanFeature(
                key: "whale_tracking",
                title: whaleFollows.map { "Track \($0) \($0 == 1 ? "investor" : "investors")" }
                    ?? "Track unlimited investors",
                detail: whaleFollows == nil
                    ? "Follow the whole roster and keep every one of them on your Tracking tab."
                    : (whaleFollows! <= 1
                        ? "One preset investor to start. Browsing every investor is free on every plan."
                        : "Pick any from the full roster. Browsing is free on every plan."),
                symbol: validatedSymbol("person.2.fill"),
                accentKey: "whales"
            ),
            PlanFeature(
                key: "whale_detail",
                title: "Holdings, trades & AI sentiment",
                detail: paid
                    ? "Current Picks, Recent Trades and the AI sentiment summary on every investor you open."
                    : "Free shows each investor's profile, risk badge and sector exposure. Current Picks, Recent Trades and the AI sentiment summary are paid.",
                symbol: validatedSymbol("chart.pie.fill"),
                accentKey: "whales",
                included: paid
            ),
            PlanFeature(
                key: "learn_audio",
                title: "Money Moves & book narration",
                detail: paid
                    ? "Listen to Money Moves and the book library with synced read-along highlighting."
                    : "Every article and book stays free to read. Narration with read-along highlighting is paid.",
                symbol: validatedSymbol("headphones"),
                accentKey: "learn",
                included: paid
            ),
            // ── Included on every plan ──────────────────────────────────────────────
            PlanFeature(
                key: "market_data",
                title: "Market data & company research",
                detail: "Stocks, ETFs, crypto, indices and commodities — detail screens, charts, news and search, on every plan.",
                symbol: validatedSymbol("chart.xyaxis.line"),
                accentKey: "neutral",
                isAlwaysIncluded: true
            ),
            PlanFeature(
                key: "tracking_tools",
                title: "Watchlists, portfolios & price alerts",
                detail: "Unlimited watchlists and portfolios with a diversification score, plus price alerts. No plan required.",
                symbol: validatedSymbol("list.bullet.rectangle.portrait"),
                accentKey: "neutral",
                isAlwaysIncluded: true
            ),
            PlanFeature(
                key: "learn_text",
                title: "The full Wiser library",
                detail: "Every lesson, article and book guide is free to read, with progress and bookmarks, on every plan.",
                symbol: validatedSymbol("book.fill"),
                accentKey: "neutral",
                isAlwaysIncluded: true
            ),
            // Journey narration is free on EVERY tier — never move this into the paid
            // block above. 207 of the 230 clips are Journey, so a blanket "unlock
            // narration" sells the reader something they already have.
            PlanFeature(
                key: "journey_audio",
                title: "Investor Journey narration",
                detail: "Every Journey lesson reads itself aloud with read-along highlighting — on every plan, including Free.",
                symbol: validatedSymbol("waveform"),
                accentKey: "neutral",
                isAlwaysIncluded: true
            ),
            PlanFeature(
                key: "pdf_export",
                title: "PDF export",
                detail: "Export any AI research report as a multi-page PDF. No plan required.",
                symbol: validatedSymbol("square.and.arrow.up"),
                accentKey: "neutral",
                isAlwaysIncluded: true
            ),
        ]
    }

    private static func formatted(_ value: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: value)) ?? "\(value)"
    }

    /// ⚠️ ONE POOL — reports and chat draw on the same balance, so these are alternatives,
    /// never a sum. Both costs are guarded against 0: they arrive from the wire.
    private static func creditsDetail(_ credits: Int, reportCost: Int, chatCost: Int) -> String {
        var clauses: [String] = []
        if reportCost > 0 { clauses.append("\(formatted(credits / reportCost)) AI research reports") }
        if chatCost > 0 { clauses.append("\(formatted(credits / chatCost)) Cay AI replies") }
        switch clauses.count {
        case 2:
            return "About \(clauses[0]) or \(clauses[1]) — one shared pool that resets at the start of each month."
        case 1:
            return "About \(clauses[0]). Resets at the start of each month."
        default:
            return "Resets at the start of each month."
        }
    }
}

// MARK: - Paywall context

/// WHY the upgrade sheet was opened.
///
/// `PaywallView` is presented from eight places — the Updates "+N more" chip, the Home
/// signals lock, two whale-follow limits, the whale profile's locked sections, the Learn
/// narration lock, Buy Credits, and Account — and every one of them used to show the
/// identical generic screen. Someone who tapped a narration lock had to go hunting for
/// narration in the list to find out whether upgrading would even fix it.
///
/// `rawValue` doubles as the analytics dimension (`reason` on `paywall_shown`), which is
/// what finally makes conversion-per-lock readable; before this, a paywall impression
/// carried no trace of what triggered it.
enum PaywallContext: String, Sendable, CaseIterable {
    case general
    case moreCredits = "more_credits"
    case updatesTickers = "updates_tickers"
    case signals
    case whaleFollowLimit = "whale_follow_limit"
    case whaleDetail = "whale_detail"
    case learnAudio = "learn_audio"

    var headline: String {
        switch self {
        case .general:          return "Get more out of Caydex"
        case .moreCredits:      return "Credits every month, automatically"
        case .updatesTickers:   return "See every ticker in Updates"
        case .signals:          return "Unlock the signal tickers"
        case .whaleFollowLimit: return "Track more investors"
        case .whaleDetail:      return "See what they're actually holding"
        case .learnAudio:       return "Listen instead of reading"
        }
    }

    /// Locked contexts name what the current plan KEEPS, not only what it withholds —
    /// the same posture the backend gates are built around (the card still demonstrates
    /// what it does), finally stated on the paywall instead of only in a code comment.
    var subheadline: String {
        switch self {
        case .general:
            return "More credits each month, the full investor detail, and narrated lessons."
        case .moreCredits:
            return "A plan refills your balance every month — and unlocks the paid screens with it."
        case .updatesTickers:
            return "Free covers one watchlist ticker. A plan gives more of them their own news tab."
        case .signals:
            return "You already see every signal and how many investors are moving. A plan reveals which tickers they are."
        case .whaleFollowLimit:
            return "Free tracks one preset investor. Browsing every investor stays free either way."
        case .whaleDetail:
            return "Current Picks, Recent Trades and the AI sentiment summary on any investor."
        case .learnAudio:
            return "Narration and read-along for Money Moves and the book library. Investor Journey narration is already free on every plan."
        }
    }

    /// The server feature `key` this context is about. Drives both the highlight and the
    /// default plan selection, so it must match a key `plan_features.py` actually emits.
    var featureKey: String {
        switch self {
        case .general, .moreCredits: return "credits"
        case .updatesTickers:        return "updates_tickers"
        case .signals:               return "signals"
        case .whaleFollowLimit:      return "whale_tracking"
        case .whaleDetail:           return "whale_detail"
        case .learnAudio:            return "learn_audio"
        }
    }

    var symbol: String {
        switch self {
        case .general:          return AppSymbols.ai
        case .moreCredits:      return "creditcard.fill"
        case .updatesTickers:   return "chart.bar.doc.horizontal"
        case .signals:          return "antenna.radiowaves.left.and.right"
        case .whaleFollowLimit: return "person.2.fill"
        case .whaleDetail:      return "chart.pie.fill"
        case .learnAudio:       return "headphones"
        }
    }

    var accent: Color {
        switch self {
        case .general, .moreCredits: return AppColors.alertOrange
        case .updatesTickers:        return AppColors.primaryBlue
        case .signals:               return AppColors.accentCyan
        case .whaleFollowLimit, .whaleDetail: return AppColors.alertPurple
        case .learnAudio:            return AppColors.gain
        }
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
