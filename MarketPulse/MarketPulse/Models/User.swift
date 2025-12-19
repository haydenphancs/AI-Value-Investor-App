import Foundation
import Combine

// MARK: - User Models

struct User: Codable, Identifiable {
    let id: String
    let email: String
    let fullName: String?
    let tier: UserTier
    let createdAt: Date
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, email, tier
        case fullName = "full_name"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct UserUpdate: Codable {
    let fullName: String?
    let preferredTimezone: String?
    let notificationPreferences: [String: Bool]?

    enum CodingKeys: String, CodingKey {
        case fullName = "full_name"
        case preferredTimezone = "preferred_timezone"
        case notificationPreferences = "notification_preferences"
    }
}

struct UsageStats: Codable {
    let deepResearch: UsageLimit
    let resetAt: Date?

    enum CodingKeys: String, CodingKey {
        case deepResearch = "deep_research"
        case resetAt = "reset_at"
    }
}

struct UsageLimit: Codable {
    let used: Int
    let limit: Int?
    let remaining: Int?

    var isUnlimited: Bool {
        limit == nil
    }

    var progressPercentage: Double {
        guard let limit = limit, limit > 0 else { return 0 }
        return Double(used) / Double(limit)
    }
}

struct UserStats: Codable {
    let watchlistCount: Int
    let reportsGenerated: Int
    let chatSessions: Int
    let lastActivity: Date?

    enum CodingKeys: String, CodingKey {
        case watchlistCount = "watchlist_count"
        case reportsGenerated = "reports_generated"
        case chatSessions = "chat_sessions"
        case lastActivity = "last_activity"
    }
}

// MARK: - Auth Models

struct AuthToken: Codable {
    let accessToken: String
    let refreshToken: String?
    let tokenType: String
    let expiresIn: Int?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
        case expiresIn = "expires_in"
    }
}

struct AuthRequest: Codable {
    let supabaseToken: String

    enum CodingKeys: String, CodingKey {
        case supabaseToken = "supabase_token"
    }
}
