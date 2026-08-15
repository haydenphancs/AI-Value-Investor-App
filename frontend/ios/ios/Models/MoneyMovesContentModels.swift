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
///
/// Every drop is LOGGED. Leniency without logging is the worst of both worlds: an article that
/// vanishes here is invisible (the outer decode still succeeds, the store silently serves the stale
/// bundled copy), so the only symptom is "the new article never showed up" with nothing to grep.
private struct FailableDecodable<Wrapped: Decodable>: Decodable {
    let value: Wrapped?
    init(from decoder: Decoder) throws {
        do {
            value = try Wrapped(from: decoder)
        } catch {
            value = nil
            // Best-effort identity FIRST (slug/title if this element is an object at all), then the
            // coding path — together they name the Supabase row a human has to go fix.
            let id = (try? DroppedElementIdentity(from: decoder))?.label ?? "unidentifiable"
            print("[MoneyMovesContentModels] dropped \(Wrapped.self) at "
                  + "\(decoder.codingPath.pathDescription) [\(id)]: \(error)")
        }
    }
}

/// Best-effort identity peek at a dropped element. Both fields are optional, so this decodes for any
/// JSON object and simply fails (=> nil) for a scalar element.
private struct DroppedElementIdentity: Decodable {
    let slug: String?
    let title: String?

    var label: String {
        let parts = [slug.map { "slug=\($0)" }, title.map { "title=\($0)" }].compactMap { $0 }
        return parts.isEmpty ? "no slug/title" : parts.joined(separator: " ")
    }
}

private extension Array where Element == CodingKey {
    /// `articles[3].sections[1].content[2]` — enough to point at the offending blob.
    var pathDescription: String {
        map { $0.intValue.map { i in "[\(i)]" } ?? ".\($0.stringValue)" }
            .joined()
            .trimmingCharacters(in: CharacterSet(charactersIn: "."))
    }
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

    /// Decode a `[String]` element-by-element, coercing each the way `flexibleString` does.
    /// `decodeIfPresent([String].self)` is all-or-nothing: ONE non-string bullet (`["a", 5, "b"]`)
    /// makes it nil, `toContent()` then returns nil for the block, and the ENTIRE bulletList
    /// disappears from the article — the good bullets punished for a bad neighbour. Per-element,
    /// only the uncoercible bullet (an object/array/null) is dropped. nil => no usable items.
    func flexibleStringArray(forKey key: Key) -> [String]? {
        guard let wrapped = (try? decodeIfPresent([FlexibleStringElement].self, forKey: key)) ?? nil
        else { return nil }
        let items = wrapped.compactMap { $0.value }
        return items.isEmpty ? nil : items
    }
}

/// Single-value counterpart of `flexibleString` — used per array element, and never throws, so one
/// bad element can't fail the array decode.
private struct FlexibleStringElement: Decodable {
    let value: String?

    init(from decoder: Decoder) throws {
        guard let c = try? decoder.singleValueContainer() else { value = nil; return }
        if let s = try? c.decode(String.self) { value = s }
        else if let i = try? c.decode(Int.self) { value = String(i) }
        else if let d = try? c.decode(Double.self), d.isFinite {
            value = d == d.rounded() ? String(Int(d)) : String(d)
        }
        else if let b = try? c.decode(Bool.self) { value = String(b) }
        else { value = nil }   // object / array / null: nothing sensible to render
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
    /// Narration is Pro/Max and this caller doesn't have it: the server stripped every
    /// `audioUrl` and read-along span. Optional so an older backend reads absent → unlocked,
    /// i.e. exactly today's behaviour rather than a phantom lock.
    let audioLocked: Bool?
    let tierRequired: String?

    private enum CodingKeys: String, CodingKey {
        case articles
        case audioLocked = "audio_locked"
        case tierRequired = "tier_required"
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        articles = c.lenientArray(MoneyMoveArticleDTO.self, forKey: .articles)
        audioLocked = (try? c.decodeIfPresent(Bool.self, forKey: .audioLocked)) ?? nil
        tierRequired = (try? c.decodeIfPresent(String.self, forKey: .tierRequired)) ?? nil
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
    /// The REAL publication instant, ISO-8601 (`"2026-06-12T14:03:21Z"`), served from the
    /// `published_at` column. Optional because the bundled offline JSON carries only
    /// `publishedDaysAgo`, and because a row that predates the seeder has a NULL column —
    /// `resolvedPublishedAt` falls back in both cases.
    let publishedAt: String?
    let tagLabel: String?
    let isFeatured: Bool?
    let hasAudioVersion: Bool?
    let audioUrl: String?
    let audioDurationSeconds: Int?    // real narration length (sec) — drives the Listen time
    // Cover artwork, served from the PUBLIC money-moves-images bucket. Optional at every
    // layer on purpose: an article with no plate falls back to `heroGradientColors`, which is
    // exactly how every article looked before artwork existed, so the degraded state is the
    // old design rather than a hole. Unlike narration these are never signed or expired, and
    // `redact_money_moves` deliberately leaves them alone — artwork is free on every tier.
    let imageUrl: String?             // 1206x678 — the article header card + the featured card
    let imageCardUrl: String?         // 640x360  — the 200pt catalog tile
    let heroGradientColors: [String]
    let keyHighlights: [ArticleHighlightDTO]
    let sections: [ArticleSectionDTO]
    let statistics: [ArticleStatisticDTO]?
    let comments: [ArticleCommentDTO]?
    let relatedArticles: [RelatedArticleDTO]?

    /// Map the transport DTO into the UI model the views/AudioManager consume.
    ///
    /// `trustAudioFlag` says whether this DTO came from the BACKEND (true) or from the bundled
    /// offline JSON (false). It exists because the two disagree about what `hasAudioVersion`
    /// means. On the wire the backend deliberately keeps it TRUE for a locked article — the
    /// narration is real, the URL is merely withheld — so the Listen control stays visible as
    /// an upgrade offer. But all 13 articles in `Resources/MoneyMoves/money_moves.json` also
    /// ship `hasAudioVersion: true` with `audioUrl: null`, and for an ENTITLED user offline
    /// that renders a saturated "Listen Now" (not the paywall) whose tap dies in
    /// `AudioManager.isMissingNarration`. Bundled articles therefore derive the flag from the
    /// URL, which is the honest signal there.
    /// The publication instant, preferring the REAL one.
    ///
    /// ⚠️ ONE implementation, consumed by both `toArticle()` and `toCard()`. They used to
    /// derive this separately from `publishedDaysAgo`, and the comment on the second copy
    /// claimed "a card and the article it opens can never disagree about how old the piece
    /// is" — a claim maintained by hand across two expressions. Now it is structural.
    ///
    /// The fallback is the old derivation, `now − publishedDaysAgo` days. It DRIFTS: the
    /// offset is a constant in the content blob, so a piece authored with
    /// `publishedDaysAgo: 12` reads as 12 days old forever. That is harmless for ordering
    /// (relative order is preserved) and wrong for a rendered date, which is why the backend
    /// now serves `published_at` and why this prefers it. The fallback only survives for the
    /// bundled offline JSON and for rows that predate the seeder.
    var resolvedPublishedAt: Date {
        if let iso = publishedAt, let parsed = MoneyMoveDateFormatting.parseISO8601(iso) {
            return parsed
        }
        return Calendar.current.date(
            byAdding: .day, value: -(publishedDaysAgo ?? 3), to: Date()
        ) ?? Date()
    }

    func toArticle(trustAudioFlag: Bool = false) -> MoneyMoveArticle {
        let published = resolvedPublishedAt
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
            // Trust the server's flag when it sends one; fall back to the URL only for the
            // bundled/offline blob, which carries no URLs at all. Deriving this purely from
            // `audioUrl != nil` would make a LOCKED article (URL stripped, narration very
            // much real) indistinguishable from one that was never narrated — and the Listen
            // control hides entirely on false, so the upgrade offer would vanish from exactly
            // the articles that would sell it.
            hasAudioVersion: trustAudioFlag ? (hasAudioVersion ?? (audioUrl != nil)) : (audioUrl != nil),
            heroGradientColors: heroGradientColors,
            tagLabel: tagLabel,
            isFeatured: isFeatured ?? false,
            keyHighlights: keyHighlights.map { $0.toHighlight() },
            sections: sections.map { $0.toSection() },
            statistics: (statistics ?? []).map { $0.toStatistic() },
            comments: mappedComments,
            relatedArticles: (relatedArticles ?? []).map { $0.toRelated() },
            audioUrl: audioUrl,
            audioDurationSeconds: audioDurationSeconds,
            imageUrl: imageUrl,
            imageCardUrl: imageCardUrl
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
            // The tile uses the SMALL derivative. Falling back to the hero would pull a
            // 1206px plate into a 600px slot for every card in a horizontal scroll row.
            imageUrl: imageCardUrl ?? imageUrl,
            // The SAME resolver `toArticle` uses, so a card and the article it opens cannot
            // disagree about how old the piece is — the card renders this date now, so a
            // divergence would be visible rather than merely latent.
            createdAt: resolvedPublishedAt
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
             learnerCount, sortOrder, commentCount, publishedDaysAgo, publishedAt, tagLabel,
             isFeatured, hasAudioVersion, audioUrl, audioDurationSeconds, imageUrl, imageCardUrl,
             heroGradientColors, keyHighlights, sections, statistics, comments, relatedArticles
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
        // Empty-string coalesced to nil for the same reason as the image urls: an empty value
        // occupies the slot and would suppress the publishedDaysAgo fallback while never
        // parsing to a date.
        publishedAt = c.flexibleString(forKey: .publishedAt).flatMap { $0.isEmpty ? nil : $0 }
        tagLabel = c.flexibleString(forKey: .tagLabel)
        isFeatured = (try? c.decodeIfPresent(Bool.self, forKey: .isFeatured)) ?? nil
        hasAudioVersion = (try? c.decodeIfPresent(Bool.self, forKey: .hasAudioVersion)) ?? nil
        audioUrl = (try? c.decodeIfPresent(String.self, forKey: .audioUrl)) ?? nil
        audioDurationSeconds = c.flexibleInt(forKey: .audioDurationSeconds)
        // Empty-string coalesced to nil: an empty url still occupies the slot and would
        // suppress the gradient fallback while never resolving to a picture.
        imageUrl = c.flexibleString(forKey: .imageUrl).flatMap { $0.isEmpty ? nil : $0 }
        imageCardUrl = c.flexibleString(forKey: .imageCardUrl).flatMap { $0.isEmpty ? nil : $0 }
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
            // Default to NOT verified. A "verified" badge is a factual claim about a
            // real person; defaulting it to true means any article that omits the
            // field silently asserts one (App Review 1.1.6 / 2.3.1).
            isVerified: isVerified ?? false,
            followerCount: followerCount ?? "0"
        )
    }
}

struct ArticleHighlightDTO: Decodable {
    let icon: String
    let title: String
    let description: String

    // Coerced like the parent article (see `flexibleString`): the backend serves the authored
    // `content` blob VERBATIM, so a Studio/programmatic row can carry a number where a string is
    // expected. With the synthesized decode that threw, and `lenientArray` then dropped this whole
    // highlight from the article — content silently missing rather than merely mis-typed.
    private enum CodingKeys: String, CodingKey { case icon, title, description }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        icon = c.flexibleString(forKey: .icon) ?? ""
        title = c.flexibleString(forKey: .title) ?? ""
        description = c.flexibleString(forKey: .description) ?? ""
    }

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
    /// dropping the article. Leaf spans are already defensively decoded (ReadAlongModels). `items`
    /// goes through `flexibleStringArray` so one non-string bullet costs only that bullet.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = ((try? c.decodeIfPresent(String.self, forKey: .type)) ?? nil) ?? ""
        text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? nil
        items = c.flexibleStringArray(forKey: .items)
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

    // `value` is the field most likely to be authored as a NUMBER (`"value": 180`) — it renders a
    // figure. Coerce rather than throw, or the whole stat tile disappears from the article.
    private enum CodingKeys: String, CodingKey { case value, label, trend, trendValue }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        value = c.flexibleString(forKey: .value) ?? ""
        label = c.flexibleString(forKey: .label) ?? ""
        trend = c.flexibleString(forKey: .trend)
        trendValue = c.flexibleString(forKey: .trendValue)
    }

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

    // Counts authored as strings (`"likeCount": "47"`) are the common slip here; the Optionals
    // do NOT save us, because `decodeIfPresent(Int.self)` still throws on a type mismatch and the
    // comment is then dropped whole.
    private enum CodingKeys: String, CodingKey {
        case authorName, content, likeCount, replyCount, isVerified, hoursAgo
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        authorName = c.flexibleString(forKey: .authorName) ?? ""
        content = c.flexibleString(forKey: .content) ?? ""
        likeCount = c.flexibleInt(forKey: .likeCount)
        replyCount = c.flexibleInt(forKey: .replyCount)
        isVerified = (try? c.decodeIfPresent(Bool.self, forKey: .isVerified)) ?? nil
        hoursAgo = c.flexibleInt(forKey: .hoursAgo)
    }

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
    // Stamped in by seed_money_moves.py from a title -> card-url map built across every
    // article, so the tile needs no runtime title->slug lookup and no second request. Nil
    // for a related entry pointing at an article that has no plate yet — it falls back to
    // its own `gradientColors`, which is what the tile has always drawn.
    let imageCardUrl: String?

    // Mirrors the PARENT article's handling of the very same fields (`readTimeMinutes` via
    // `flexibleInt`, `viewCount` via `flexibleString`) — they were coerced there and strict here,
    // so an authoring slip that the article itself survived still deleted its related-article card.
    private enum CodingKeys: String, CodingKey {
        case title, subtitle, category, readTimeMinutes, viewCount, gradientColors, imageCardUrl
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = c.flexibleString(forKey: .title) ?? ""
        subtitle = c.flexibleString(forKey: .subtitle) ?? ""
        category = c.flexibleString(forKey: .category) ?? ""
        readTimeMinutes = c.flexibleInt(forKey: .readTimeMinutes) ?? 0
        viewCount = c.flexibleString(forKey: .viewCount) ?? "0"
        gradientColors = c.flexibleStringArray(forKey: .gradientColors) ?? []
        imageCardUrl = c.flexibleString(forKey: .imageCardUrl).flatMap { $0.isEmpty ? nil : $0 }
    }

    func toRelated() -> RelatedArticle {
        RelatedArticle(
            title: title,
            subtitle: subtitle,
            category: MoneyMoveArticleDTO.category(from: category),
            readTimeMinutes: readTimeMinutes,
            viewCount: viewCount,
            gradientColors: gradientColors,
            imageCardUrl: imageCardUrl
        )
    }
}
