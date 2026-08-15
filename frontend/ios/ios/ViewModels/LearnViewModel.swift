//
//  LearnViewModel.swift
//  ios
//
//  ViewModel for Learn (Wiser) screen - MVVM Architecture
//

import Foundation
import Combine

@MainActor
class LearnViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var selectedTab: LearnTab = .learn
    @Published var currentLevel: InvestorLevel = .foundation
    @Published var journeyTrack: JourneyTrack?
    @Published var nextLesson: NextLesson?
    @Published var moneyMoves: [MoneyMove] = []
    @Published var books: [EducationBook] = []
    @Published var discussions: [CommunityDiscussion] = []
    // NOTE: no `creditBalance` here on purpose. This used to be hardcoded to
    // `47 credits, renews Jan 1 2025` — a fabricated balance shown to every user
    // that contradicted the real one on Research and Profile. The Learn screen now
    // reads `appState.user.credits`, the documented single source of truth.
    @Published var isLoading: Bool = false
    @Published var error: String?
    @Published var searchText: String = ""
    /// Client-side keyword filter over the Money Moves + Books shown on the Wiser
    /// dashboard (no backend learn-search endpoint exists). Distinct from the top
    /// "Search or ask Cay AI" bar, which is global entity/AI search.
    @Published var contentFilter: String = ""

    private var cancellables = Set<AnyCancellable>()

    // MARK: - Content filter (Wiser dashboard)

    /// Whether the keyword filter is narrowing the dashboard.
    var isFilteringContent: Bool {
        !contentFilter.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Money Moves matching the keyword (title or subtitle). Full list when empty.
    var filteredMoneyMoves: [MoneyMove] {
        let kw = contentFilter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !kw.isEmpty else { return moneyMoves }
        return moneyMoves.filter {
            $0.title.lowercased().contains(kw) || $0.subtitle.lowercased().contains(kw)
        }
    }

    /// Books matching the keyword (title or author). Full list when empty.
    var filteredBooks: [EducationBook] {
        let kw = contentFilter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !kw.isEmpty else { return books }
        return books.filter {
            $0.title.lowercased().contains(kw) || $0.author.lowercased().contains(kw)
        }
    }

    // MARK: - Initialization
    init() {
        loadMockData()

        // The journey card is derived from the shared completion store and rebuilt whenever a
        // lesson is finished anywhere (e.g. inside the full-screen journey), so the two surfaces
        // always agree. Build once now for an instant, correct first paint.
        rebuildJourney()
        JourneyProgressStore.shared.$completedTitles
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.rebuildJourney() }
            .store(in: &cancellables)

        // Completing a Money Move (here, from its card, or by finishing its narration) re-sorts
        // the row so unread moves stay on the left and completed ones slide to the end.
        MoneyMovesProgressStore.shared.$completed
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.resortMoneyMoves() }
            .store(in: &cancellables)
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        // Simulate network delay. Journey progress is NOT loaded here — it's derived live from
        // the shared completion store via rebuildJourney() (see init).
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.loadMoneyMoves()
            self?.loadBooks()
            self?.loadDiscussions()
            self?.isLoading = false
        }
    }

    func refresh() async {
        isLoading = true
        try? await Task.sleep(nanoseconds: 800_000_000)
        loadMockData()
        rebuildJourney()
    }

    /// Upgrade the Money Moves row to fresh backend content. The store serves bundled content
    /// synchronously for first paint, then this swaps in /learn/money-moves. Safe to call
    /// repeatedly — the store prefetches once per session. Mirrors MoneyMovesDetailView.
    func prefetchMoneyMoves() async {
        await MoneyMovesContentStore.shared.prefetch()
        loadMoneyMoves()
    }

    // MARK: - Journey Progress (shared with the full-screen journey)

    /// Rebuild the Learn-tab journey card from the shared completion store over the lesson
    /// catalog (InvestorJourneyData.sampleData). The card tracks the *current level* — the one
    /// holding the global next-incomplete lesson — so its lit badge advances Foundation →
    /// Analyst → … as levels finish. Mirrors the full-screen journey so both always agree.
    private func rebuildJourney() {
        let completed = JourneyProgressStore.shared.completedTitles
        let levels = InvestorJourneyData.sampleData.levels

        // Current level = first level with an unfinished lesson; if all done, the final level.
        let activeLevel = levels.first { level in
            level.lessons.contains { !completed.contains($0.title) }
        } ?? levels.last

        guard let active = activeLevel else {
            journeyTrack = nil
            nextLesson = nil
            return
        }

        currentLevel = investorLevel(for: active.level)

        // The first unfinished lesson in the active level is the global next lesson.
        let next = active.lessons.first { !completed.contains($0.title) }

        let items: [JourneyItem] = active.lessons.enumerated().map { index, lesson in
            JourneyItem(
                title: lesson.title,
                isCompleted: completed.contains(lesson.title),
                isActive: lesson.title == next?.title,
                stepNumber: index + 1
            )
        }

        journeyTrack = JourneyTrack(
            level: currentLevel,
            completedCount: active.lessons.filter { completed.contains($0.title) }.count,
            totalCount: active.lessons.count,
            items: items
        )

        // Next-lesson detail (kept in sync even though this screen doesn't render it today).
        if let next {
            nextLesson = NextLesson(
                journeyNumber: active.level.rawValue,
                journeyTitle: active.level.title,
                lessonTitle: next.title,
                lessonDescription: next.description,
                estimatedMinutes: JourneyContentStore.shared.estimatedMinutes(forLessonTitled: next.title)
                    ?? next.durationMinutes,
                chapterCount: active.lessons.count
            )
        } else {
            nextLesson = nil
        }
    }

    /// Map the full-journey level enum onto the Learn-tab's level enum.
    private func investorLevel(for level: JourneyLevel) -> InvestorLevel {
        switch level {
        case .foundation: return .foundation
        case .analysis:   return .analyst
        case .strategies: return .strategist
        case .mastery:    return .master
        }
    }

    // MARK: - Private Loaders
    private func loadMoneyMoves() {
        // Authored catalog (backend → bundled, via MoneyMovesContentStore) first; fill the rest
        // with not-yet-authored sample placeholders so the row is never empty. Adding an article
        // server-side makes its card appear here with no app update. Mirrors MoneyMovesDetailView.
        var cards = MoneyMovesContentStore.shared.cards()
        let authoredTitles = Set(cards.map { $0.title })
        cards += MoneyMove.sampleData.filter { !authoredTitles.contains($0.title) }
        moneyMoves = sortedIncompleteFirst(cards)
    }

    /// Re-sort the existing cards (preserving identity) so a just-completed move slides to the end.
    private func resortMoneyMoves() {
        moneyMoves = sortedIncompleteFirst(moneyMoves)
    }

    /// Unread moves on the left, completed ones at the end; NEWEST FIRST inside each group.
    ///
    /// The two keys compose rather than compete. Unread-first is the existing behaviour and
    /// stays primary. Newest-first is nested under it so that a freshly seeded article — which
    /// is unread by definition — lands at the very front of the row, which is the whole point
    /// of carrying `createdAt`. Sorting purely by date would instead bury a brand new article
    /// behind everything the reader has already finished.
    ///
    /// `sorted(by:)` is not stable in Swift, so the date comparison falls back to the title to
    /// keep a deterministic order among same-day articles rather than letting them shuffle
    /// between renders.
    private func sortedIncompleteFirst(_ cards: [MoneyMove]) -> [MoneyMove] {
        let store = MoneyMovesProgressStore.shared
        return Self.newestFirst(cards.filter { !store.isCompleted(slug: $0.slug) })
            + Self.newestFirst(cards.filter { store.isCompleted(slug: $0.slug) })
    }

    static func newestFirst(_ cards: [MoneyMove]) -> [MoneyMove] {
        cards.sorted {
            $0.createdAt == $1.createdAt ? $0.title < $1.title : $0.createdAt > $1.createdAt
        }
    }

    private func loadBooks() {
        books = EducationBook.sampleData
    }

    private func loadDiscussions() {
        discussions = CommunityDiscussion.sampleData
    }


    // MARK: - Actions
    func selectTab(_ tab: LearnTab) {
        selectedTab = tab
    }

    func continueJourney() {
        print("Continue journey tapped")
    }

    func startLesson(_ lesson: NextLesson) {
        print("Start lesson: \(lesson.lessonTitle)")
    }

    func openMoneyMove(_ moneyMove: MoneyMove) {
        print("Open money move: \(moneyMove.title)")
    }

    func openBook(_ book: EducationBook) {
        print("Open book: \(book.title)")
    }

    func openDiscussion(_ discussion: CommunityDiscussion) {
        print("Open discussion by: \(discussion.authorName)")
    }

}
