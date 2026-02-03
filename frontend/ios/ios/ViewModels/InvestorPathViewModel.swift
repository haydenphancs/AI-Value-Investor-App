// note
//  InvestorJourneyViewModel.swift
//  ios
// 
//  ViewModel for The Investor Journey screen - MVVM Architecture
//

import Foundation
import Combine

@MainActor
class InvestorJourneyViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var journeyData: InvestorJourneyData?
    @Published var studySchedule: StudySchedule = .defaultSchedule
    @Published var quote: InvestorQuote = .buffettQuote
    @Published var isLoading: Bool = false
    @Published var error: String?
    @Published var selectedLesson: Lesson?
    @Published var showLessonStory: Bool = false

    // MARK: - Computed Properties
    var totalLessonsCompleted: Int {
        journeyData?.totalLessonsCompleted ?? 0
    }

    var totalLessons: Int {
        journeyData?.totalLessons ?? 0
    }

    var levels: [LevelProgress] {
        journeyData?.levels ?? []
    }

    var nextLessonId: UUID? {
        // Find the first incomplete lesson across all levels
        for level in levels {
            if let firstIncomplete = level.firstIncompleteLessonId {
                return firstIncomplete
            }
        }
        return nil
    }

    var nextLessonLevelId: UUID? {
        // Find the level containing the first incomplete lesson
        for level in levels {
            if level.firstIncompleteLessonId != nil {
                return level.id
            }
        }
        return nil
    }

    // MARK: - Initialization
    init() {
        loadData()
    }

    // MARK: - Data Loading
    func loadData() {
        isLoading = true

        // Simulate network delay
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.journeyData = InvestorJourneyData.sampleData
            self?.isLoading = false
        }
    }

    func refresh() async {
        isLoading = true
        try? await Task.sleep(nanoseconds: 500_000_000)
        loadData()
    }

    // MARK: - Actions
    func selectLesson(_ lesson: Lesson) {
        selectedLesson = lesson
        showLessonStory = true
    }

    func dismissLessonStory() {
        showLessonStory = false
        selectedLesson = nil
    }

    /// Generate story content for a lesson
    func getStoryContent(for lesson: Lesson) -> LessonStoryContent {
        // Find the level containing this lesson
        let levelInfo = findLevelForLesson(lesson)

        // For now, return sample content based on the lesson title
        // In a real app, this would fetch from a backend or local storage
        return LessonStoryContent(
            lessonLabel: "LESSON \(levelInfo.lessonIndex + 1): \(lesson.title.uppercased())",
            lessonNumber: levelInfo.lessonIndex + 1,
            totalLessonsInLevel: levelInfo.totalInLevel,
            estimatedMinutes: lesson.durationMinutes,
            cards: generateCardsForLesson(lesson)
        )
    }

    private func findLevelForLesson(_ lesson: Lesson) -> (level: JourneyLevel, lessonIndex: Int, totalInLevel: Int) {
        for levelProgress in levels {
            if let index = levelProgress.lessons.firstIndex(where: { $0.id == lesson.id }) {
                return (levelProgress.level, index, levelProgress.lessons.count)
            }
        }
        return (.foundation, 0, 1)
    }

    private func generateCardsForLesson(_ lesson: Lesson) -> [LessonTopicCard] {
        // Generate appropriate cards based on the lesson
        // This is sample content - in production, this would come from a CMS or backend
        var cards: [LessonTopicCard] = []

        // Title card
        cards.append(.titleCard(
            title: lesson.title,
            subtitle: generateHighlightedSubtitle(from: lesson.description),
            audioText: lesson.description
        ))

        // Add 2-3 content cards based on the lesson
        let contentCards = generateContentCards(for: lesson)
        cards.append(contentsOf: contentCards)

        // Completion card
        cards.append(.completionCard(
            title: "You're ready.",
            subtitle: "You've learned the core idea. Practice with a real stock to reinforce it.",
            ctaDestination: determineCTADestination(for: lesson)
        ))

        return cards
    }

    private func generateHighlightedSubtitle(from description: String) -> [HighlightedTextSegment] {
        // Find a key word to highlight (first word or important term)
        let words = description.split(separator: " ")
        guard let firstWord = words.first else {
            return [.init(description)]
        }

        let rest = description.dropFirst(firstWord.count + 1)
        return [
            .init(String(firstWord), highlighted: true),
            .init(" " + rest)
        ]
    }

    private func generateContentCards(for lesson: Lesson) -> [LessonTopicCard] {
        // Generate content based on lesson title
        // This provides meaningful sample content for each lesson type
        switch lesson.title {
        case "The Buffett Way":
            return [
                .contentCard(
                    content: [
                        .init("Price is what the "),
                        .init("market", highlighted: true),
                        .init(" asks. Value is what the business is worth. The gap between them is where investing opportunities are found.")
                    ],
                    audioText: "Price is what the market asks. Value is what the business is worth. The gap between them is where investing opportunities are found."
                ),
                .contentCard(
                    content: [
                        .init("Price "),
                        .init("changes", highlighted: true),
                        .init(" with emotion. Value is anchored in fundamentals. Knowing the difference helps you invest, not speculate.")
                    ],
                    audioText: "Price changes with emotion. Value is anchored in fundamentals. Knowing the difference helps you invest, not speculate."
                )
            ]
        case "Compound Interest":
            return [
                .contentCard(
                    content: [
                        .init("Your money "),
                        .init("grows", highlighted: true),
                        .init(" on itself. Interest earns interest, creating a snowball effect over time.")
                    ],
                    audioText: "Your money grows on itself. Interest earns interest, creating a snowball effect over time."
                ),
                .contentCard(
                    content: [
                        .init("Time is your greatest "),
                        .init("ally", highlighted: true),
                        .init(". The earlier you start, the more powerful the compounding effect becomes.")
                    ],
                    audioText: "Time is your greatest ally. The earlier you start, the more powerful the compounding effect becomes."
                )
            ]
        case "Mr. Market":
            return [
                .contentCard(
                    content: [
                        .init("Imagine a "),
                        .init("moody", highlighted: true),
                        .init(" business partner who offers to buy or sell his share every day at different prices.")
                    ],
                    audioText: "Imagine a moody business partner who offers to buy or sell his share every day at different prices."
                ),
                .contentCard(
                    content: [
                        .init("You're not "),
                        .init("obligated", highlighted: true),
                        .init(" to trade with Mr. Market. Only act when the price is in your favor.")
                    ],
                    audioText: "You're not obligated to trade with Mr. Market. Only act when the price is in your favor."
                )
            ]
        default:
            // Generic content for other lessons
            return [
                .contentCard(
                    content: [
                        .init("Understanding this "),
                        .init("concept", highlighted: true),
                        .init(" is essential for making informed investment decisions.")
                    ],
                    audioText: "Understanding this concept is essential for making informed investment decisions."
                ),
                .contentCard(
                    content: [
                        .init("Apply this "),
                        .init("knowledge", highlighted: true),
                        .init(" to analyze real stocks and build your investment skills.")
                    ],
                    audioText: "Apply this knowledge to analyze real stocks and build your investment skills."
                )
            ]
        }
    }

    private func determineCTADestination(for lesson: Lesson) -> LessonCTADestination {
        // Determine appropriate CTA based on lesson content
        switch lesson.title {
        case "Key Statistics", "The Income Statement", "The Balance Sheet", "Cash Flow is King":
            return .analyzeStock
        case "Portfolio Gardening":
            return .viewPortfolio
        default:
            return .analyzeStock
        }
    }

    func openChatWithBook() {
        print("Opening chat with The Intelligent Investor")
        // Navigate to chat with book
    }

    func updateMorningSessionTime(_ date: Date) {
        studySchedule.morningSessionTime = date
        saveSchedule()
    }

    func updateReviewTime(_ date: Date) {
        studySchedule.reviewTime = date
        saveSchedule()
    }

    func toggleDailyReminder(_ enabled: Bool) {
        studySchedule.dailyReminderEnabled = enabled
        saveSchedule()
    }

    // MARK: - Private Methods
    private func saveSchedule() {
        // Save schedule to persistent storage
        print("Schedule updated: Daily reminder \(studySchedule.dailyReminderEnabled ? "enabled" : "disabled")")
        print("Morning session: \(studySchedule.formattedMorningTime)")
        print("Review time: \(studySchedule.formattedReviewTime)")
    }

    // MARK: - Level Access
    func getLevelProgress(for level: JourneyLevel) -> LevelProgress? {
        levels.first { $0.level == level }
    }

    // MARK: - Progress Tracking
    func markLessonCompleted(_ lesson: Lesson, in level: JourneyLevel) {
        // In a real app, this would update the backend and local state
        print("Marked lesson '\(lesson.title)' as completed in \(level.title)")
    }
}
