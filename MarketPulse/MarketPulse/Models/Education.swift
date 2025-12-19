import Foundation
import Combine

// MARK: - Education Models

struct EducationContent: Codable, Identifiable {
    let id: String
    let type: ContentType
    let title: String
    let author: String?
    let publicationYear: Int?
    let summary: String?
    let fullText: String?
    let topics: [String]?
    let sourceUrl: String?
    let coverImageUrl: String?
    let chunkCount: Int
    let isProcessed: Bool
    let createdAt: Date
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, type, title, author, summary, topics
        case publicationYear = "publication_year"
        case fullText = "full_text"
        case sourceUrl = "source_url"
        case coverImageUrl = "cover_image_url"
        case chunkCount = "chunk_count"
        case isProcessed = "is_processed"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var truncatedSummary: String? {
        guard let summary = summary else { return nil }
        if summary.count > 500 {
            return String(summary.prefix(497)) + "..."
        }
        return summary
    }
}

struct EducationContentBrief: Codable, Identifiable {
    let id: String
    let type: ContentType
    let title: String
    let author: String?
    let publicationYear: Int?

    enum CodingKeys: String, CodingKey {
        case id, type, title, author
        case publicationYear = "publication_year"
    }
}
