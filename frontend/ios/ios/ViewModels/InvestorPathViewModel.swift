//
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
        print("Selected lesson: \(lesson.title)")
        // Navigate to lesson detail
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
