import Foundation
import Combine

// MARK: - Enums

enum UserTier: String, Codable {
    case free
    case pro
    case premium

    var displayName: String {
        switch self {
        case .free: return "Free"
        case .pro: return "Pro"
        case .premium: return "Premium"
        }
    }

    var monthlyReportLimit: Int? {
        switch self {
        case .free: return 1
        case .pro: return 10
        case .premium: return nil // Unlimited
        }
    }
}

enum SentimentType: String, Codable {
    case bullish
    case bearish
    case neutral

    var emoji: String {
        switch self {
        case .bullish: return "📈"
        case .bearish: return "📉"
        case .neutral: return "➖"
        }
    }

    var color: String {
        switch self {
        case .bullish: return "green"
        case .bearish: return "red"
        case .neutral: return "gray"
        }
    }
}

enum ReportStatus: String, Codable {
    case pending
    case processing
    case completed
    case failed

    var displayName: String {
        rawValue.capitalized
    }
}

enum InvestorPersona: String, Codable, CaseIterable {
    case buffett
    case ackman
    case munger
    case lynch
    case graham

    var displayName: String {
        switch self {
        case .buffett: return "Warren Buffett"
        case .ackman: return "Bill Ackman"
        case .munger: return "Charlie Munger"
        case .lynch: return "Peter Lynch"
        case .graham: return "Benjamin Graham"
        }
    }

    var emoji: String {
        switch self {
        case .buffett: return "🎩"
        case .ackman: return "💼"
        case .munger: return "🧠"
        case .lynch: return "📊"
        case .graham: return "📚"
        }
    }

    var description: String {
        switch self {
        case .buffett:
            return "Value investing focused on business quality and long-term competitive advantages"
        case .ackman:
            return "Activist value investing with focus on high-quality franchises"
        case .munger:
            return "Patient capital allocation with emphasis on great companies at fair prices"
        case .lynch:
            return "Growth at reasonable price (GARP) with bottom-up stock picking"
        case .graham:
            return "Deep value investing with margin of safety and quantitative analysis"
        }
    }
}

enum ContentType: String, Codable {
    case book
    case article

    var displayName: String {
        rawValue.capitalized
    }
}

enum SessionType: String, Codable {
    case education
    case stockAnalysis = "stock_analysis"
    case general

    var displayName: String {
        switch self {
        case .education: return "Education"
        case .stockAnalysis: return "Stock Analysis"
        case .general: return "General"
        }
    }

    var emoji: String {
        switch self {
        case .education: return "📚"
        case .stockAnalysis: return "📈"
        case .general: return "💬"
        }
    }
}

// MARK: - Base Response Models

struct SuccessResponse: Codable {
    let success: Bool
    let message: String
    let data: [String: AnyCodable]?
}

struct ErrorResponse: Codable {
    let success: Bool
    let error: String
    let detail: String?
    let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case success, error, detail
        case errorCode = "error_code"
    }
}

struct PaginatedResponse<T: Codable>: Codable {
    let items: [T]
    let total: Int
    let page: Int
    let pageSize: Int
    let hasNext: Bool
    let hasPrev: Bool

    enum CodingKeys: String, CodingKey {
        case items, total, page
        case pageSize = "page_size"
        case hasNext = "has_next"
        case hasPrev = "has_prev"
    }
}

// MARK: - Helpers

// Helper to handle Any type in Codable
struct AnyCodable: Codable, Equatable {
    let value: Any

    init(_ value: Any) {
        self.value = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()

        if let intValue = try? container.decode(Int.self) {
            value = intValue
        } else if let doubleValue = try? container.decode(Double.self) {
            value = doubleValue
        } else if let stringValue = try? container.decode(String.self) {
            value = stringValue
        } else if let boolValue = try? container.decode(Bool.self) {
            value = boolValue
        } else if let arrayValue = try? container.decode([AnyCodable].self) {
            value = arrayValue.map { $0.value }
        } else if let dictValue = try? container.decode([String: AnyCodable].self) {
            value = dictValue.mapValues { $0.value }
        } else {
            value = NSNull()
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()

        switch value {
        case let intValue as Int:
            try container.encode(intValue)
        case let doubleValue as Double:
            try container.encode(doubleValue)
        case let stringValue as String:
            try container.encode(stringValue)
        case let boolValue as Bool:
            try container.encode(boolValue)
        case let arrayValue as [Any]:
            try container.encode(arrayValue.map { AnyCodable($0) })
        case let dictValue as [String: Any]:
            try container.encode(dictValue.mapValues { AnyCodable($0) })
        default:
            try container.encodeNil()
        }
    }

    static func == (lhs: AnyCodable, rhs: AnyCodable) -> Bool {
        switch (lhs.value, rhs.value) {
        case let (lInt as Int, rInt as Int):
            return lInt == rInt
        case let (lDouble as Double, rDouble as Double):
            return lDouble == rDouble
        case let (lString as String, rString as String):
            return lString == rString
        case let (lBool as Bool, rBool as Bool):
            return lBool == rBool
        case let (lArray as [Any], rArray as [Any]):
            guard lArray.count == rArray.count else { return false }
            return zip(lArray, rArray).allSatisfy { AnyCodable($0) == AnyCodable($1) }
        case let (lDict as [String: Any], rDict as [String: Any]):
            guard lDict.keys == rDict.keys else { return false }
            return lDict.keys.allSatisfy { key in
                guard let lValue = lDict[key], let rValue = rDict[key] else { return false }
                return AnyCodable(lValue) == AnyCodable(rValue)
            }
        case (_ as NSNull, _ as NSNull):
            return true
        default:
            return false
        }
    }
}
