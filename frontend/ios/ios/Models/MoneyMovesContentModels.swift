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

// MARK: - Lenient array decoding
//
// The backend serves each row's `content` JSONB VERBATIM (schemas/money_moves.py = List[Dict], no
// shape validation; the service only skips non-dict rows). MoneyMoveArticleDTO has ~10 required
// fields, and Swift's synthesized array decode is all-or-nothing — so ONE served article missing a
// required field (a row hand-edited in Supabase Studio, a legacy/partial row, a null slug) would
// throw and drop EVERY remote article (all content + audio) back to the stale bundle, not just the
// bad row. Decode the article array element-by-element instead: a bad article is skipped, the rest
// survive — mirroring the per-row hardening money_moves_content_service._load already does.

/// Never-throwing wrapper: a failed element decodes to `nil` instead of failing the whole array.
private struct FailableDecodable<Wrapped: Decodable>: Decodable {
    let value: Wrapped?
    init(from decoder: Decoder) throws { value = try? Wrapped(from: decoder) }
}

private extension KeyedDecodingContainer {
    /// Decode an array, dropping any element that fails to decode. Missing/non-array key => [].
    func lenientArray<T: Decodable>(_ type: T.Type, forKey key: Key) -> [T] {
        let wrapped = ((try? decodeIfPresent([FailableDecodable<T>].self, forKey: key)) ?? nil) ?? []
        return wrapped.compactMap { $0.value }
    }

    /// Decode an Int that tolerates a value authored as a JSON float (`5.0`/`5.5`) or numeric string
    /// (`"5"`). A wrong TYPE must degrade to nil (or a caller default), NOT throw and drop the whole
    /// article — the backend serves `content` verbatim, so a Studio/programmatic row can carry these.
    func flexibleInt(forKey key: Key) -> Int? {
        if let i = (try? decodeIfPresent(Int.self, forKey: key)) ?? nil { return i }
        if let d = (try? decodeIfPresent(Double.self, forKey: key)) ?? nil, d.isFinite { return Int(d.rounded()) }
        if let s = (try? decodeIfPresent(String.self, forKey: key)) ?? nil {
            if let i = Int(s) { return i }
            if let d = Double(s), d.isFinite { return Int(d.rounded()) }
        }
        return nil
    }

    /// Decode a String that tolerates a value authored as a JSON number/bool by stringifying it.
    func flexibleString(forKey key: Key) -> String? {
        if let s = (try? decodeIfPresent(String.self, forKey: key)) ?? nil { return s }
        if let i = (try? decodeIfPresent(Int.self, forKey: key)) ?? nil { return String(i) }
        if let d = (try? decodeIfPresent(Double.self, forKey: key)) ?? nil {
            return d == d.rounded() ? String(Int(d)) : String(d)
        }
        if let b = (try? decodeIfPresent(Bool.self, forKey: key)) ?? nil { return String(b) }
        return nil
    }
}

// MARK: - Top-level containers

/// Bundled file `Resources/MoneyMoves/money_moves.json`.
struct MoneyMovesContentFile: Decodable {
    let version: Int?
    let articles: [MoneyMoveArticleDTO]

    private enum CodingKeys: String, CodingKey { case version, articles }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = ((try? c.decodeIfPresent(Int.self, forKey: .version)) ?? nil)
        articles = c.lenientArray(MoneyMoveArticleDTO.self, forKey: .articles)
    }
}

/// Backend response from `GET /api/v1/learn/money-moves` — each article is the row's
/// `content` blob (same shape as the bundle's articles).
struct MoneyMovesAPIResponse: Decodable {
    let articles: [MoneyMoveArticleDTO]

    private enum CodingKeys: String, CodingKey { case articles }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        articles = c.lenientArray(MoneyMoveArticleDTO.self, forKey: .articles)
    }
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
    let audioDurationSeconds: Int?    // real narration length (sec) — drives the Listen time
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
            slug: slug,
            title: title,
            subtitle: subtitle,
            category: MoneyMoveArticleDTO.category(from: category),
            author: author.toAuthor(),
            publishedAt: published,
            readTimeMinutes: readTimeMinutes,
            viewCount: viewCount,
            commentCount: commentCount ?? mappedComments.count,
            isBookmarked: false,
            hasAudioVersion: audioUrl != nil,   // honest gate: only show Listen when narration exists
            heroGradientColors: heroGradientColors,
            tagLabel: tagLabel,
            isFeatured: isFeatured ?? false,
            keyHighlights: keyHighlights.map { $0.toHighlight() },
            sections: sections.map { $0.toSection() },
            statistics: (statistics ?? []).map { $0.toStatistic() },
            comments: mappedComments,
            relatedArticles: (relatedArticles ?? []).map { $0.toRelated() },
            audioUrl: audioUrl,
            audioDurationSeconds: audioDurationSeconds
        )
    }

    /// Lightweight card (row tile) derived from the same authored content, so the
    /// catalog can be served from the backend/bundle instead of hardcoded in Swift.
    func toCard() -> MoneyMove {
        MoneyMove(
            slug: slug,
            isFeatured: isFeatured ?? false,
            title: title,
            subtitle: cardSubtitle ?? subtitle,
            category: MoneyMoveArticleDTO.category(from: category),
            estimatedMinutes: readTimeMinutes,
            learnerCount: learnerCount ?? viewCount,
            hasAudio: audioUrl != nil
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

// MARK: - Defensive decoding (mirror the always-lenient Journey path)
//
// The backend serves each article's `content` JSONB VERBATIM (no shape validation). Journey's iOS
// decoder degrades at every layer (lenient card array, `type` defaults, timings via `try?`); Money
// Moves used to be lenient ONLY at the outer article array, so ONE malformed nested value (a content
// block missing `type`, a FLAT `itemsReadAlong`, a section missing `title`, a numeric `viewCount`, a
// fractional `readTimeMinutes`) threw and SILENTLY dropped the whole article (with no log — the outer
// lenient decode still succeeds). These custom inits route every inner array through `lenientArray`
// and coerce the scalar types, so a bad nested value degrades in place. Well-formed content (the whole
// current bundle) decodes identically. Defined in extensions to preserve the memberwise inits.

extension MoneyMoveArticleDTO {
    private enum CodingKeys: String, CodingKey {
        case slug, title, subtitle, cardSubtitle, category, author, readTimeMinutes, viewCount,
             learnerCount, sortOrder, commentCount, publishedDaysAgo, tagLabel, isFeatured,
             hasAudioVersion, audioUrl, audioDurationSeconds, heroGradientColors, keyHighlights,
             sections, statistics, comments, relatedArticles
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Identity fields: a missing slug/title SHOULD drop just this article (via the outer lenient
        // article array) — an article with no stable identity is unusable.
        slug = try c.decode(String.self, forKey: .slug)
        title = try c.decode(String.self, forKey: .title)
        // Everything else degrades in place rather than dropping the whole article.
        subtitle = ((try? c.decodeIfPresent(String.self, forKey: .subtitle)) ?? nil) ?? ""
        cardSubtitle = c.flexibleString(forKey: .cardSubtitle)
        category = ((try? c.decodeIfPresent(String.self, forKey: .category)) ?? nil) ?? "blueprints"
        author = ((try? c.decodeIfPresent(ArticleAuthorDTO.self, forKey: .author)) ?? nil) ?? .placeholder
        readTimeMinutes = c.flexibleInt(forKey: .readTimeMinutes) ?? 0
        viewCount = c.flexibleString(forKey: .viewCount) ?? ""
        learnerCount = c.flexibleString(forKey: .learnerCount)
        sortOrder = c.flexibleInt(forKey: .sortOrder)
        commentCount = c.flexibleInt(forKey: .commentCount)
        publishedDaysAgo = c.flexibleInt(forKey: .publishedDaysAgo)
        tagLabel = c.flexibleString(forKey: .tagLabel)
        isFeatured = (try? c.decodeIfPresent(Bool.self, forKey: .isFeatured)) ?? nil
        hasAudioVersion = (try? c.decodeIfPresent(Bool.self, forKey: .hasAudioVersion)) ?? nil
        audioUrl = (try? c.decodeIfPresent(String.self, forKey: .audioUrl)) ?? nil
        audioDurationSeconds = c.flexibleInt(forKey: .audioDurationSeconds)
        heroGradientColors = c.lenientArray(String.self, forKey: .heroGradientColors)
        keyHighlights = c.lenientArray(ArticleHighlightDTO.self, forKey: .keyHighlights)
        sections = c.lenientArray(ArticleSectionDTO.self, forKey: .sections)
        let stats = c.lenientArray(ArticleStatisticDTO.self, forKey: .statistics)
        statistics = stats.isEmpty ? nil : stats
        let cmts = c.lenientArray(ArticleCommentDTO.self, forKey: .comments)
        comments = cmts.isEmpty ? nil : cmts
        let related = c.lenientArray(RelatedArticleDTO.self, forKey: .relatedArticles)
        relatedArticles = related.isEmpty ? nil : related
    }
}

// MARK: - Nested DTOs

struct ArticleAuthorDTO: Decodable {
    let name: String
    let title: String
    let isVerified: Bool?
    let followerCount: String?

    /// Fallback when a served article omits/malforms its author, so the article still renders instead
    /// of being dropped whole (a missing required author field would otherwise throw the DTO decode).
    static let placeholder = ArticleAuthorDTO(
        name: "Caydex", title: "Research", isVerified: nil, followerCount: nil
    )

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

    private enum CodingKeys: String, CodingKey { case title, icon, hasGlowEffect, content }

    /// Defensive decode: `content` goes through `lenientArray` so one malformed block drops just that
    /// block, not the whole article; a missing section title degrades to "".
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = ((try? c.decodeIfPresent(String.self, forKey: .title)) ?? nil) ?? ""
        icon = (try? c.decodeIfPresent(String.self, forKey: .icon)) ?? nil
        hasGlowEffect = (try? c.decodeIfPresent(Bool.self, forKey: .hasGlowEffect)) ?? nil
        content = c.lenientArray(ArticleSectionContentDTO.self, forKey: .content)
    }

    func toSection() -> ArticleSection {
        // Build content and its parallel read-along array in lockstep, so dropped (unknown-type)
        // blocks don't misalign the timings index.
        var blocks: [ArticleSectionContent] = []
        var timings: [ReadAlongGroup?] = []
        for dto in content {
            guard let block = dto.toContent() else { continue }
            blocks.append(block)
            timings.append(dto.readAlongGroup())
        }
        return ArticleSection(
            title: title,
            icon: icon,
            content: blocks,
            hasGlowEffect: hasGlowEffect ?? false,
            readAlong: timings
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
    let readAlong: [ReadAlongSentence]?          // per-sentence timings (text blocks)
    let itemsReadAlong: [[ReadAlongSentence]]?   // per-item sentence timings (bulletList)

    /// Read-along timings for this block, shaped to match its type (nil => none yet).
    /// An EMPTY (but non-nil) timings array is treated as "no timings" — otherwise `.sentences([])`
    /// would drive ReadAlongText with zero spans and render the block's prose BLANK (the empty
    /// AttributedString shows nothing). Empty can come from an alignment run that produced no spans
    /// for a block, or a hand-edited Studio row; it must degrade to plain text, not vanish.
    func readAlongGroup() -> ReadAlongGroup? {
        if type == "bulletList" {
            guard let items = itemsReadAlong, !items.isEmpty else { return nil }
            return .items(items)
        }
        guard let ra = readAlong, !ra.isEmpty else { return nil }
        return .sentences(ra)
    }

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

extension ArticleSectionContentDTO {
    private enum CodingKeys: String, CodingKey {
        case type, text, items, attribution, icon, style, readAlong, itemsReadAlong
    }

    /// Defensive decode. A missing/blank `type` decodes cleanly, then `toContent()` drops just this
    /// block (returns nil) instead of throwing and dropping the whole article. `readAlong` /
    /// `itemsReadAlong` decode via `try?` so a mis-shaped timing container — most likely a FLAT
    /// `itemsReadAlong` authored where the nested `[[…]]` is expected — degrades to nil rather than
    /// dropping the article. Leaf spans are already defensively decoded (ReadAlongModels).
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = ((try? c.decodeIfPresent(String.self, forKey: .type)) ?? nil) ?? ""
        text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? nil
        items = (try? c.decodeIfPresent([String].self, forKey: .items)) ?? nil
        attribution = (try? c.decodeIfPresent(String.self, forKey: .attribution)) ?? nil
        icon = (try? c.decodeIfPresent(String.self, forKey: .icon)) ?? nil
        style = (try? c.decodeIfPresent(String.self, forKey: .style)) ?? nil
        readAlong = (try? c.decodeIfPresent([ReadAlongSentence].self, forKey: .readAlong)) ?? nil
        itemsReadAlong = (try? c.decodeIfPresent([[ReadAlongSentence]].self, forKey: .itemsReadAlong)) ?? nil
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
