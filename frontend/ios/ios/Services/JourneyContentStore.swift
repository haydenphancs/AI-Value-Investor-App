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

private struct JourneyAPIResponse: Decodable {
    let lessons: [JourneyAPILesson]
}

private struct JourneyAPILesson: Decodable {
    let title: String
    let storyContent: JourneyAPIStory?

    enum CodingKeys: String, CodingKey {
        case title
        case storyContent = "story_content"
    }
}

private struct JourneyAPIStory: Decodable {
    let cards: [JourneyAPICard]
}

private struct JourneyAPICard: Decodable {
    let type: String
    let headline: String?
    let text: String?
    let audioUrl: String?   // remote Supabase Storage URL
    let imageUrl: String?
    let videoUrl: String?
    let cta: String?
}

// MARK: - Bundled DTOs (journey_lessons.json)

private struct JourneyBundleFile: Decodable {
    let lessons: [JourneyBundleLesson]
}

private struct JourneyBundleLesson: Decodable {
    let title: String
    let cards: [JourneyBundleCard]
}

private struct JourneyBundleCard: Decodable {
    let type: String
    let headline: String?
    let text: String?
    let audioClip: String?   // bundle resource basename (legacy / offline)
    let hasImage: Bool?
    let cta: String?
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
    func cards(forLessonTitled title: String) -> [LessonTopicCard]? {
        remoteByTitle[title] ?? bundledByTitle[title]
    }

    func hasContent(forLessonTitled title: String) -> Bool {
        remoteByTitle[title] != nil || bundledByTitle[title] != nil
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
                guard let story = lesson.storyContent else { continue }
                remoteByTitle[lesson.title] = story.cards.map {
                    makeCard(
                        title: lesson.title,
                        type: $0.type,
                        headline: $0.headline,
                        text: $0.text ?? "",
                        audio: $0.audioUrl,
                        image: $0.imageUrl,
                        cta: $0.cta
                    )
                }
            }
        } catch {
            // Stay on bundled content; never block the screen on a network hiccup.
            didPrefetch = false
            print("[JourneyContentStore] backend fetch failed, using bundled content: \(error)")
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
                        cta: card.cta
                    )
                }
            }
        } catch {
            print("[JourneyContentStore] bundled decode failed: \(error)")
        }
    }

    // MARK: - DTO -> LessonTopicCard (shared by both sources)

    private func makeCard(title lessonTitle: String, type: String, headline: String?,
                          text: String, audio: String?, image: String?, cta: String?) -> LessonTopicCard {
        let slug = Self.slug(lessonTitle)
        switch type {
        case "title":
            return .titleCard(
                title: headline ?? "",
                subtitle: Self.segments(from: text),
                audioText: Self.spoken(from: text),
                audioClip: audio,
                imageName: image ?? "journey_\(slug)_title"
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
                audioClip: audio
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
