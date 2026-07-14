//
//  JourneyContentStore.swift
//  ios
//
//  Source of Investor Journey lesson content (the swipeable story cards).
//
//  Primary source: the backend `/api/v1/learn/journey` endpoint, which serves each
//  lesson's story_content (cards with remote audio/image URLs) from Supabase.
//  Offline fallback: the bundled journey_lessons.json (text only — narration then
//  falls back to on-device speech until the backend/Storage is reachable).
//
//  Both sources are mapped into the same LessonTopicCard model, so the views and
//  ViewModel never care where the content came from.
//

import Foundation

// MARK: - Backend DTOs (GET /api/v1/learn/journey)
//
// Decoding is LENIENT by design. The backend serves each lesson's `story_content` JSONB verbatim
// (no card-shape validation), so a single row hand-edited in Supabase Studio — a card missing
// `type`, a card that isn't even an object, a lesson missing `title` — must NOT fail the decode of
// the WHOLE response. Swift array decoding is all-or-nothing, so one bad card would otherwise throw
// and drop ALL ~27 lessons back to the text-only bundled fallback (every DB-only lesson vanishes,
// every lesson loses its remote audio + read-along). Instead, a bad card/lesson is dropped and the
// rest survive — the same defensive stance ReadAlongSentence already takes for its timings.

/// Never-throwing wrapper: a failed element decodes to `nil` instead of failing the whole array.
private struct FailableDecodable<Wrapped: Decodable>: Decodable {
    let value: Wrapped?
    init(from decoder: Decoder) throws {
        value = try? Wrapped(from: decoder)
    }
}

private struct JourneyAPIResponse: Decodable {
    let lessons: [JourneyAPILesson]

    private enum CodingKeys: String, CodingKey { case lessons }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let raw = ((try? c.decodeIfPresent([FailableDecodable<JourneyAPILesson>].self, forKey: .lessons)) ?? nil) ?? []
        lessons = raw.compactMap { $0.value }   // drop any lesson that failed (e.g. missing title)
    }
}

private struct JourneyAPILesson: Decodable {
    let title: String              // required: it's the stable lesson key; a title-less lesson is dropped
    let storyContent: JourneyAPIStory?

    enum CodingKeys: String, CodingKey {
        case title
        case storyContent = "story_content"
    }
}

private struct JourneyAPIStory: Decodable {
    let cards: [JourneyAPICard]

    private enum CodingKeys: String, CodingKey { case cards }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let raw = ((try? c.decodeIfPresent([FailableDecodable<JourneyAPICard>].self, forKey: .cards)) ?? nil) ?? []
        cards = raw.compactMap { $0.value }   // drop malformed cards; keep the good ones
    }
}

private struct JourneyAPICard: Decodable {
    let type: String
    let headline: String?
    let text: String?
    let audioUrl: String?   // remote Supabase Storage URL
    let imageUrl: String?
    let videoUrl: String?
    let cta: String?
    let readAlongWords: [ReadAlongWord]?   // per-word narration timings (forced-aligned)

    enum CodingKeys: String, CodingKey {
        case type, headline, text, audioUrl, imageUrl, videoUrl, cta, readAlongWords
    }

    init(from decoder: Decoder) throws {
        // A card object with a missing/null `type` still decodes — it maps to a content card
        // (makeCard's default branch) rather than throwing and dropping every sibling card.
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.type = (((try? c.decodeIfPresent(String.self, forKey: .type)) ?? nil) ?? "content")
        self.headline = ((try? c.decodeIfPresent(String.self, forKey: .headline)) ?? nil)
        self.text = ((try? c.decodeIfPresent(String.self, forKey: .text)) ?? nil)
        self.audioUrl = ((try? c.decodeIfPresent(String.self, forKey: .audioUrl)) ?? nil)
        self.imageUrl = ((try? c.decodeIfPresent(String.self, forKey: .imageUrl)) ?? nil)
        self.videoUrl = ((try? c.decodeIfPresent(String.self, forKey: .videoUrl)) ?? nil)
        self.cta = ((try? c.decodeIfPresent(String.self, forKey: .cta)) ?? nil)
        self.readAlongWords = ((try? c.decodeIfPresent([ReadAlongWord].self, forKey: .readAlongWords)) ?? nil)
    }
}

// MARK: - Bundled DTOs (journey_lessons.json)
//
// Decoded with the SAME leniency as the remote path above. The bundle ships in-binary, so a bad
// element is an authoring mistake rather than a Studio edit — but a single bundled card missing
// `type` (or a lesson missing `title`) would otherwise throw in JSONDecoder().decode and drop the
// ENTIRE offline fallback (all 27 lessons), taking down offline mode AND the offline fallback for
// remote-only content. Drop the one bad element, keep the rest — matching JourneyAPICard.

private struct JourneyBundleFile: Decodable {
    let lessons: [JourneyBundleLesson]

    private enum CodingKeys: String, CodingKey { case lessons }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let raw = ((try? c.decodeIfPresent([FailableDecodable<JourneyBundleLesson>].self, forKey: .lessons)) ?? nil) ?? []
        lessons = raw.compactMap { $0.value }
    }
}

private struct JourneyBundleLesson: Decodable {
    let title: String
    let cards: [JourneyBundleCard]

    private enum CodingKeys: String, CodingKey { case title, cards }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decode(String.self, forKey: .title)   // title-less lesson dropped by the outer lenient array
        let raw = ((try? c.decodeIfPresent([FailableDecodable<JourneyBundleCard>].self, forKey: .cards)) ?? nil) ?? []
        cards = raw.compactMap { $0.value }
    }
}

private struct JourneyBundleCard: Decodable {
    let type: String
    let headline: String?
    let text: String?
    let audioClip: String?   // bundle resource basename (legacy / offline)
    let hasImage: Bool?
    let cta: String?
    let readAlongWords: [ReadAlongWord]?   // baked in by align_journey_audio.py

    private enum CodingKeys: String, CodingKey {
        case type, headline, text, audioClip, hasImage, cta, readAlongWords
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = (((try? c.decodeIfPresent(String.self, forKey: .type)) ?? nil) ?? "content")
        headline = ((try? c.decodeIfPresent(String.self, forKey: .headline)) ?? nil)
        text = ((try? c.decodeIfPresent(String.self, forKey: .text)) ?? nil)
        audioClip = ((try? c.decodeIfPresent(String.self, forKey: .audioClip)) ?? nil)
        hasImage = ((try? c.decodeIfPresent(Bool.self, forKey: .hasImage)) ?? nil)
        cta = ((try? c.decodeIfPresent(String.self, forKey: .cta)) ?? nil)
        readAlongWords = ((try? c.decodeIfPresent([ReadAlongWord].self, forKey: .readAlongWords)) ?? nil)
    }
}

// MARK: - Store

@MainActor
final class JourneyContentStore {
    static let shared = JourneyContentStore()

    private var bundledByTitle: [String: [LessonTopicCard]] = [:]
    private var remoteByTitle: [String: [LessonTopicCard]] = [:]
    private var didPrefetch = false

    private init() {
        loadBundled()
    }

    /// Authored cards for a lesson: prefer fresh backend content, fall back to bundled.
    /// Never returns an EMPTY array — a backend lesson served with `cards: []` would otherwise
    /// win over the bundled/generated fallback and crash the player on `cards[0]`. Returning nil
    /// here lets the ViewModel's generated-cards fallback take over instead.
    func cards(forLessonTitled title: String) -> [LessonTopicCard]? {
        if let remote = remoteByTitle[title], !remote.isEmpty { return remote }
        if let bundled = bundledByTitle[title], !bundled.isEmpty { return bundled }
        return nil
    }

    func hasContent(forLessonTitled title: String) -> Bool {
        remoteByTitle[title] != nil || bundledByTitle[title] != nil
    }

    // MARK: - Duration estimate

    /// Estimated minutes to complete a lesson, derived from the lesson's actual narration
    /// content instead of a hand-entered number — so the "X min" the UI shows reflects the
    /// real lesson. Returns nil when no content is available for the title.
    func estimatedMinutes(forLessonTitled title: String) -> Int? {
        guard let cards = cards(forLessonTitled: title) else { return nil }
        return Self.estimatedMinutes(for: cards)
    }

    /// Words narrated at ~150 WPM (the ~170 WPM clips, eased for comprehension and pauses)
    /// plus a short per-card transition, rounded up with a 2-minute floor.
    static func estimatedMinutes(for cards: [LessonTopicCard]) -> Int {
        let words = cards.reduce(0) { $0 + wordCount(of: $1) }
        let listeningWPM = 150.0
        let perCardSeconds = 2.0
        let seconds = Double(words) / listeningWPM * 60.0 + Double(cards.count) * perCardSeconds
        return max(2, Int((seconds / 60.0).rounded(.up)))
    }

    private static func wordCount(of card: LessonTopicCard) -> Int {
        let text: String
        if let audio = card.audioText, !audio.isEmpty {
            text = audio
        } else {
            let segments = (card.subtitleSegments ?? []) + (card.contentSegments ?? [])
            let joined = segments.map(\.text).joined(separator: " ")
            text = joined.isEmpty ? (card.completionSubtitle ?? "") : joined
        }
        return text.split { !$0.isLetter && !$0.isNumber }.count
    }

    /// Fetch lesson content + media URLs from the backend once per session.
    func prefetch() async {
        guard !didPrefetch else { return }
        didPrefetch = true
        do {
            let response = try await APIClient.shared.request(
                endpoint: .getJourney,
                responseType: JourneyAPIResponse.self
            )
            for lesson in response.lessons {
                // Skip nil OR empty card lists — an empty remote array must not shadow the
                // bundled/generated fallback (would crash on cards[0] when the lesson opens).
                guard let story = lesson.storyContent, !story.cards.isEmpty else { continue }
                remoteByTitle[lesson.title] = story.cards.map {
                    makeCard(
                        title: lesson.title,
                        type: $0.type,
                        headline: $0.headline,
                        text: $0.text ?? "",
                        audio: $0.audioUrl,
                        image: $0.imageUrl,
                        cta: $0.cta,
                        readAlong: $0.readAlongWords
                    )
                }
            }
        } catch {
            // Stay on bundled content; never block the screen on a network hiccup. But surface the
            // failure loudly + legibly: a DECODE failure here is backend↔iOS contract drift that
            // silently hides just-published content, so it must be diagnosable — not a bare swallow.
            didPrefetch = false
            let appError = AppError.from(error)
            print("[JourneyContentStore] remote fetch failed [\(appError.title)]: \(appError.message) — raw: \(error). Falling back to bundled content.")
        }
    }

    // MARK: - Bundled loading

    private func loadBundled() {
        guard let url = Bundle.main.url(forResource: "journey_lessons", withExtension: "json"),
              let data = try? Data(contentsOf: url) else {
            print("[JourneyContentStore] journey_lessons.json not found in bundle")
            return
        }
        do {
            let file = try JSONDecoder().decode(JourneyBundleFile.self, from: data)
            for lesson in file.lessons {
                let slug = Self.slug(lesson.title)
                bundledByTitle[lesson.title] = lesson.cards.map { card in
                    let isCorner = card.type == "title" || card.type == "completion"
                    let image = (card.hasImage == true) ? "journey_\(slug)_\(card.type)" : nil
                    return makeCard(
                        title: lesson.title,
                        type: card.type,
                        headline: card.headline,
                        text: card.text ?? "",
                        audio: card.audioClip,
                        image: image ?? (isCorner ? "journey_\(slug)_\(card.type)" : nil),
                        cta: card.cta,
                        readAlong: card.readAlongWords
                    )
                }
            }
        } catch {
            print("[JourneyContentStore] bundled decode failed: \(error)")
        }
    }

    // MARK: - DTO -> LessonTopicCard (shared by both sources)

    private func makeCard(title lessonTitle: String, type: String, headline: String?,
                          text: String, audio: String?, image: String?, cta: String?,
                          readAlong: [ReadAlongWord]? = nil) -> LessonTopicCard {
        let slug = Self.slug(lessonTitle)
        switch type {
        case "title":
            return .titleCard(
                title: headline ?? "",
                subtitle: Self.segments(from: text),
                audioText: Self.spoken(from: text),
                audioClip: audio,
                imageName: image ?? "journey_\(slug)_title",
                readAlongWords: readAlong
            )
        case "completion":
            return .completionCard(
                title: headline ?? "You're ready.",
                subtitle: Self.spoken(from: text),
                ctaDestination: Self.cta(cta),
                imageName: image ?? "journey_\(slug)_completion"
            )
        default: // content
            return .contentCard(
                imageName: image,
                content: Self.segments(from: text),
                audioText: Self.spoken(from: text),
                audioClip: audio,
                readAlongWords: readAlong
            )
        }
    }

    // MARK: - Markup helpers

    static func spoken(from markup: String) -> String {
        markup.replacingOccurrences(of: "**", with: "")
    }

    static func segments(from markup: String) -> [HighlightedTextSegment] {
        let parts = markup.components(separatedBy: "**")
        var result: [HighlightedTextSegment] = []
        for (index, part) in parts.enumerated() where !part.isEmpty {
            result.append(HighlightedTextSegment(part, highlighted: index % 2 == 1))
        }
        return result.isEmpty ? [HighlightedTextSegment(spoken(from: markup))] : result
    }

    static func cta(_ key: String?) -> LessonCTADestination {
        switch key {
        case "viewPortfolio": return .viewPortfolio
        case "practiceQuiz": return .practiceQuiz
        default: return .analyzeStock
        }
    }

    static func slug(_ title: String) -> String {
        title.lowercased()
            .replacingOccurrences(of: ".", with: "")
            .replacingOccurrences(of: "/", with: "")
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { !$0.isEmpty }
            .joined(separator: "_")
    }
}
