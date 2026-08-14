//
//  AccountSettingsModels.swift
//  ios
//
//  DTOs for backend-synced preferences (GET/PUT /users/me/settings) and APNs
//  device registration (POST /users/me/devices).
//
//  Preferences are a heterogeneous key→value blob (the existing @AppStorage keys:
//  ~13 notification bools + appearance string + 6 general prefs). We model the
//  values as `PreferenceValue` so the blob can carry Bool/String/Int/Double
//  without a rigid struct that must change every time a toggle is added.
//

import Foundation

// MARK: - Preference value (bool | string | int | double)

enum PreferenceValue: Codable, Equatable, Sendable {
    case bool(Bool)
    case string(String)
    case int(Int)
    case double(Double)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        // Order matters: JSON booleans decode only as Bool; try before numbers.
        if let b = try? container.decode(Bool.self) { self = .bool(b); return }
        if let i = try? container.decode(Int.self) { self = .int(i); return }
        if let d = try? container.decode(Double.self) { self = .double(d); return }
        if let s = try? container.decode(String.self) { self = .string(s); return }
        throw DecodingError.dataCorruptedError(
            in: container,
            debugDescription: "Unsupported preference value type"
        )
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .bool(let b): try container.encode(b)
        case .string(let s): try container.encode(s)
        case .int(let i): try container.encode(i)
        case .double(let d): try container.encode(d)
        }
    }

    // Convenience accessors used by the sync layer.
    var boolValue: Bool? { if case .bool(let b) = self { return b }; return nil }
    var stringValue: String? { if case .string(let s) = self { return s }; return nil }
}

// MARK: - Settings response

/// Dynamic coding key for iterating an arbitrary JSON object's keys.
private struct PreferenceKey: CodingKey {
    var stringValue: String
    var intValue: Int?
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { self.stringValue = String(intValue); self.intValue = intValue }
}

struct UserSettingsDTO: Decodable, Sendable {
    let preferences: [String: PreferenceValue]

    private enum CodingKeys: String, CodingKey { case preferences }

    init(preferences: [String: PreferenceValue]) {
        self.preferences = preferences
    }

    /// Lenient decode: iterate the preferences object key-by-key and SKIP any value
    /// that isn't a supported scalar (null / nested object / array). A single bad key
    /// must not fail the whole decode and silently void ALL synced settings — decode
    /// the good ones and drop the rest.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        var result: [String: PreferenceValue] = [:]
        if let prefs = try? container.nestedContainer(keyedBy: PreferenceKey.self, forKey: .preferences) {
            for key in prefs.allKeys {
                if let value = try? prefs.decode(PreferenceValue.self, forKey: key) {
                    result[key.stringValue] = value
                }
            }
        }
        self.preferences = result
    }
}

// MARK: - Device registration response

struct DeviceRegisterResult: Codable, Sendable {
    let registered: Bool
}

// MARK: - Guest-data claim response

/// Result of `POST /users/me/claim-guest-data`.
///
/// The backend answers with one of three shapes, all of which must decode:
///   `{"claimed": {...}}`                      — normal
///   `{"claimed": {...}, "skipped": "..."}`    — no per-install guest id, nothing to claim
///   `{"claimed": {...}, "error": "..."}`      — partial failure, reported rather than raised
/// `claimed` and both counts are always present, so they are non-optional here; `skipped` and
/// `error` are mutually exclusive extras.
///
/// `APIClient` deliberately does NOT set `.convertFromSnakeCase` (see APIClient.swift), so the
/// `watchlist_items` mapping has to be spelled out — without it this decodes as a throw, on the
/// sign-in path.
struct ClaimGuestDataResult: Codable, Sendable {
    struct Counts: Codable, Sendable {
        let watchlistItems: Int
        let portfolios: Int
        /// Learn completions + book bookmarks (one `user_learn_progress` table, four
        /// content types). OPTIONAL on purpose: this key was added to the endpoint after the
        /// first version of this DTO shipped, and the backend deploys independently of the
        /// app — a Railway rollback would omit it, and a non-optional `Int` would then throw
        /// on decode, on the sign-in path.
        let learnProgress: Int?
        /// Deep-research reports (migration 110 partitions these per install too). Optional for
        /// the same reason as `learnProgress`: the key was added after this DTO shipped and the
        /// backend deploys independently, so a rollback must not turn into a decode throw on
        /// the sign-in path.
        let researchReports: Int?
        /// Chat sessions (migration 111). The backend has ALWAYS returned this key and this DTO
        /// simply never declared it, so `didClaimAnything` answered false for a guest whose only
        /// carried-over work was a Cay AI conversation. Optional for the same
        /// independent-deploy reason as the two above.
        let chatSessions: Int?
        /// Guest portfolios MERGED into an account portfolio of the same name rather than
        /// re-pointed — the "Holdings" case, which is the common one. Counted separately from
        /// `portfolios` because the row is deleted rather than moved, so folding it into that
        /// counter would make the log read as if a portfolio had been carried across intact.
        let portfoliosMerged: Int?
        /// Learning preferences (`user_investor_profile`, migration 131). The backend has
        /// always returned this key in BOTH the normal and early-return dicts and this DTO
        /// never declared it — so a guest whose only carried-over work was their onboarding
        /// answers made `didClaimAnything` answer false. Verbatim the same omission the
        /// `chatSessions` comment above describes, repeated for the newest claim step.
        /// Optional for the same independent-deploy reason as the others.
        let investorProfile: Int?

        enum CodingKeys: String, CodingKey {
            case watchlistItems = "watchlist_items"
            case portfolios
            case learnProgress = "learn_progress"
            case researchReports = "research_reports"
            case chatSessions = "chat_sessions"
            case portfoliosMerged = "portfolios_merged"
            case investorProfile = "investor_profile"
        }
    }

    let claimed: Counts
    let skipped: String?
    let error: String?

    /// Whether anything actually moved — the only bit callers care about.
    var didClaimAnything: Bool {
        claimed.watchlistItems > 0
            || claimed.portfolios > 0
            || (claimed.portfoliosMerged ?? 0) > 0
            || (claimed.learnProgress ?? 0) > 0
            || (claimed.researchReports ?? 0) > 0
            || (claimed.chatSessions ?? 0) > 0
            || (claimed.investorProfile ?? 0) > 0
    }
}
