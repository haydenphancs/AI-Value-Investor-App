import Foundation

// MARK: - Chat Models

struct ChatSession: Codable, Identifiable {
    let id: String
    let userId: String
    let title: String
    let sessionType: SessionType
    let sessionEmoji: String
    let stockId: String?
    let stock: Stock?
    let educationContentId: String?
    let educationContent: EducationContentBrief?
    let messageCount: Int
    let lastMessageAt: Date?
    let previewMessage: String?
    let createdAt: Date
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, title, stock
        case userId = "user_id"
        case sessionType = "session_type"
        case sessionEmoji = "session_emoji"
        case stockId = "stock_id"
        case educationContentId = "education_content_id"
        case educationContent = "education_content"
        case messageCount = "message_count"
        case lastMessageAt = "last_message_at"
        case previewMessage = "preview_message"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var displayTitle: String {
        if !title.isEmpty {
            return title
        }
        switch sessionType {
        case .education:
            return educationContent?.title ?? "Education Chat"
        case .stockAnalysis:
            return stock?.ticker ?? "Stock Analysis"
        case .general:
            return "General Chat"
        }
    }
}

struct ChatMessage: Codable, Identifiable {
    let id: String
    let sessionId: String
    let role: MessageRole
    let content: String
    let citations: [Citation]?
    let metadata: [String: AnyCodable]?
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, role, content, citations, metadata
        case sessionId = "session_id"
        case createdAt = "created_at"
    }

    var isUser: Bool {
        role == .user
    }

    var isAssistant: Bool {
        role == .assistant
    }
}

enum MessageRole: String, Codable {
    case user
    case assistant
    case system
}

struct Citation: Codable, Identifiable {
    let id: String
    let source: String
    let sourceType: String?
    let sourceUrl: String?
    let excerpt: String?

    enum CodingKeys: String, CodingKey {
        case id, source, excerpt
        case sourceType = "source_type"
        case sourceUrl = "source_url"
    }
}

struct ChatSessionCreate: Codable {
    let sessionType: SessionType
    let title: String?
    let stockId: String?
    let educationContentId: String?

    enum CodingKeys: String, CodingKey {
        case title
        case sessionType = "session_type"
        case stockId = "stock_id"
        case educationContentId = "education_content_id"
    }
}

struct ChatMessageCreate: Codable {
    let content: String
}

struct ChatSessionWithMessages: Codable {
    let session: ChatSession
    let messages: [ChatMessage]
}
