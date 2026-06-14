//
//  MoneyMovesContentModels.swift
//  ios
//
//  Codable transport models for Money Moves article content.
//
//  One DTO shape is shared by BOTH sources:
//    • the backend `GET /api/v1/learn/money-moves` response, and
//    • the bundled offline fallback `Resources/MoneyMoves/money_moves.json`.
//  Both decode to `[MoneyMoveArticleDTO]`, which maps into the existing (non-Codable)
//  `MoneyMoveArticle` UI model via `toArticle()`. Keeping a single transport shape
//  means the backend ↔ iOS contract lives in exactly one place (guarded by
//  test_money_moves_schema_parity.py).
//
//  JSON keys are camelCase and match these property names exactly — APIClient's decoder
//  does NOT apply .convertFromSnakeCase, and the bundle loader uses a plain decoder.
//

import Foundation

// MARK: - Top-level containers

/// Bundled file `Resources/MoneyMoves/money_moves.json`.
struct MoneyMovesContentFile: Decodable {
    let version: Int?
    let articles: [MoneyMoveArticleDTO]
}

/// Backend response from `GET /api/v1/learn/money-moves` — each article is the row's
/// `content` blob (same shape as the bundle's articles).
struct MoneyMovesAPIResponse: Decodable {
    let articles: [MoneyMoveArticleDTO]
}

// MARK: - Article DTO

struct MoneyMoveArticleDTO: Decodable {
    let slug: String
    let title: String
    let subtitle: String          // rich subtitle shown on the article hero
    let cardSubtitle: String?     // short subtitle for the catalog card (falls back to subtitle)
    let category: String
    let author: ArticleAuthorDTO
    let readTimeMinutes: Int
    let viewCount: String
    let learnerCount: String?     // small "X investors learning" count shown on the card
    let sortOrder: Int?           // catalog ordering within its category
    let commentCount: Int?
    let publishedDaysAgo: Int?
    let tagLabel: String?
    let isFeatured: Bool?
    let hasAudioVersion: Bool?
    let audioUrl: String?
    let heroGradientColors: [String]
    let keyHighlights: [ArticleHighlightDTO]
    let sections: [ArticleSectionDTO]
    let statistics: [ArticleStatisticDTO]?
    let comments: [ArticleCommentDTO]?
    let relatedArticles: [RelatedArticleDTO]?

    /// Map the transport DTO into the UI model the views/AudioManager consume.
    func toArticle() -> MoneyMoveArticle {
        let published = Calendar.current.date(
            byAdding: .day, value: -(publishedDaysAgo ?? 3), to: Date()
        ) ?? Date()
        let mappedComments = (comments ?? []).map { $0.toComment() }
        return MoneyMoveArticle(
            title: title,
            subtitle: subtitle,
            category: MoneyMoveArticleDTO.category(from: category),
            author: author.toAuthor(),
            publishedAt: published,
            readTimeMinutes: readTimeMinutes,
            viewCount: viewCount,
            commentCount: commentCount ?? mappedComments.count,
            isBookmarked: false,
            hasAudioVersion: hasAudioVersion ?? (audioUrl != nil),
            heroGradientColors: heroGradientColors,
            tagLabel: tagLabel,
            isFeatured: isFeatured ?? false,
            keyHighlights: keyHighlights.map { $0.toHighlight() },
            sections: sections.map { $0.toSection() },
            statistics: (statistics ?? []).map { $0.toStatistic() },
            comments: mappedComments,
            relatedArticles: (relatedArticles ?? []).map { $0.toRelated() }
        )
    }

    /// Lightweight card (row tile) derived from the same authored content, so the
    /// catalog can be served from the backend/bundle instead of hardcoded in Swift.
    func toCard() -> MoneyMove {
        MoneyMove(
            title: title,
            subtitle: cardSubtitle ?? subtitle,
            category: MoneyMoveArticleDTO.category(from: category),
            estimatedMinutes: readTimeMinutes,
            learnerCount: learnerCount ?? viewCount,
            isBookmarked: false
        )
    }

    /// The DB enum / JSON store the case name ("blueprints"), not the display rawValue.
    static func category(from raw: String) -> MoneyMoveCategory {
        switch raw {
        case "blueprints": return .blueprints
        case "valueTraps", "value_traps": return .valueTraps
        case "battles": return .battles
        default: return .blueprints
        }
    }
}

// MARK: - Nested DTOs

struct ArticleAuthorDTO: Decodable {
    let name: String
    let title: String
    let isVerified: Bool?
    let followerCount: String?

    func toAuthor() -> ArticleAuthor {
        ArticleAuthor(
            name: name,
            avatarName: nil,
            title: title,
            isVerified: isVerified ?? true,
            followerCount: followerCount ?? "0"
        )
    }
}

struct ArticleHighlightDTO: Decodable {
    let icon: String
    let title: String
    let description: String

    func toHighlight() -> ArticleHighlight {
        ArticleHighlight(icon: icon, title: title, description: description)
    }
}

struct ArticleSectionDTO: Decodable {
    let title: String
    let icon: String?
    let hasGlowEffect: Bool?
    let content: [ArticleSectionContentDTO]

    func toSection() -> ArticleSection {
        ArticleSection(
            title: title,
            icon: icon,
            content: content.compactMap { $0.toContent() },
            hasGlowEffect: hasGlowEffect ?? false
        )
    }
}

/// A single content block. `type` is the discriminator:
/// paragraph | subheading | bulletList | quote | callout. Unknown types are dropped.
struct ArticleSectionContentDTO: Decodable {
    let type: String
    let text: String?
    let items: [String]?
    let attribution: String?
    let icon: String?
    let style: String?

    func toContent() -> ArticleSectionContent? {
        switch type {
        case "paragraph":
            return text.map { .paragraph($0) }
        case "subheading":
            return text.map { .subheading($0) }
        case "bulletList":
            return items.map { .bulletList($0) }
        case "quote":
            return text.map { .quote(text: $0, attribution: attribution) }
        case "callout":
            guard let text else { return nil }
            return .callout(
                icon: icon ?? "info.circle.fill",
                text: text,
                style: ArticleSectionContentDTO.calloutStyle(from: style)
            )
        default:
            return nil
        }
    }

    static func calloutStyle(from raw: String?) -> CalloutStyle {
        switch raw {
        case "warning": return .warning
        case "success": return .success
        case "highlight": return .highlight
        default: return .info
        }
    }
}

struct ArticleStatisticDTO: Decodable {
    let value: String
    let label: String
    let trend: String?
    let trendValue: String?

    func toStatistic() -> ArticleStatistic {
        ArticleStatistic(
            value: value,
            label: label,
            trend: ArticleStatisticDTO.trend(from: trend),
            trendValue: trendValue
        )
    }

    static func trend(from raw: String?) -> StatisticTrend? {
        switch raw {
        case "up": return .up
        case "down": return .down
        case "neutral": return .neutral
        default: return nil
        }
    }
}

struct ArticleCommentDTO: Decodable {
    let authorName: String
    let content: String
    let likeCount: Int?
    let replyCount: Int?
    let isVerified: Bool?
    let hoursAgo: Int?

    func toComment() -> ArticleComment {
        let posted = Calendar.current.date(
            byAdding: .hour, value: -(hoursAgo ?? 3), to: Date()
        ) ?? Date()
        return ArticleComment(
            authorName: authorName,
            authorAvatar: nil,
            content: content,
            postedAt: posted,
            likeCount: likeCount ?? 0,
            replyCount: replyCount ?? 0,
            isVerified: isVerified ?? false
        )
    }
}

struct RelatedArticleDTO: Decodable {
    let title: String
    let subtitle: String
    let category: String
    let readTimeMinutes: Int
    let viewCount: String
    let gradientColors: [String]

    func toRelated() -> RelatedArticle {
        RelatedArticle(
            title: title,
            subtitle: subtitle,
            category: MoneyMoveArticleDTO.category(from: category),
            readTimeMinutes: readTimeMinutes,
            viewCount: viewCount,
            gradientColors: gradientColors
        )
    }
}
