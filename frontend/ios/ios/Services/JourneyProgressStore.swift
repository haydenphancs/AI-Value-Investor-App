//
//  JourneyProgressStore.swift
//  ios
//
//  Single source of truth for Investor Journey lesson completion.
//
//  Both the full-screen journey (InvestorJourneyViewModel) and the Learn-tab overview card
//  (LearnViewModel) read completion from here so they always agree. The store owns the
//  persistence (UserDefaults) and broadcasts changes via @Published, so any surface that
//  observes it updates live when a lesson is finished anywhere.
//
//  It is model-agnostic: it only knows lesson *titles* (the stable lesson key). Each surface
//  maps that set onto its own view-model shape.
//

import Foundation
import Combine

@MainActor
final class JourneyProgressStore: ObservableObject {
    static let shared = JourneyProgressStore()

    /// Titles of lessons the learner has finished.
    @Published private(set) var completedTitles: Set<String> = []

    private static let defaultsKey = "investorJourney.completedLessonTitles"

    private init() {
        let saved = UserDefaults.standard.stringArray(forKey: Self.defaultsKey) ?? []
        completedTitles = Set(saved)
    }

    func isCompleted(_ title: String) -> Bool {
        completedTitles.contains(title)
    }

    /// Record a finished lesson. Idempotent; persists and broadcasts on first insert.
    func markCompleted(_ title: String) {
        guard !completedTitles.contains(title) else { return }
        completedTitles.insert(title)
        UserDefaults.standard.set(Array(completedTitles), forKey: Self.defaultsKey)
    }

    /// Clear all progress (debug / "reset journey" affordances).
    func reset() {
        guard !completedTitles.isEmpty else { return }
        completedTitles.removeAll()
        UserDefaults.standard.removeObject(forKey: Self.defaultsKey)
    }
}
