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
