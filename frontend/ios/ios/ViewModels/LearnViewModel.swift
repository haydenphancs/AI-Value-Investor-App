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
    @Published var creditBalance: CreditBalance?
    @Published var isLoading: Bool = false
    @Published var error: String?
    @Published var searchText: String = ""

    // MARK: - Initialization
    init() {
        loadMockData()
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        // Simulate network delay
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.loadJourneyData()
            self?.loadNextLesson()
            self?.loadMoneyMoves()
            self?.loadBooks()
            self?.loadDiscussions()
            self?.loadCreditBalance()
            self?.isLoading = false
        }
    }

    func refresh() async {
        isLoading = true
        try? await Task.sleep(nanoseconds: 800_000_000)
        loadMockData()
    }

    // MARK: - Private Loaders
    private func loadJourneyData() {
        journeyTrack = JourneyTrack.sampleBeginner
        currentLevel = .foundation
    }

    private func loadNextLesson() {
        nextLesson = NextLesson.sampleData
    }

    private func loadMoneyMoves() {
        moneyMoves = MoneyMove.sampleData
    }

    private func loadBooks() {
        books = EducationBook.sampleData
    }

    private func loadDiscussions() {
        discussions = CommunityDiscussion.sampleData
    }

    private func loadCreditBalance() {
        creditBalance = CreditBalance(
            credits: 47,
            renewalDate: Calendar.current.date(from: DateComponents(year: 2025, month: 1, day: 1)) ?? Date()
        )
    }

    // MARK: - Actions
    func selectTab(_ tab: LearnTab) {
        selectedTab = tab
    }

    func toggleBookmark(for moneyMove: MoneyMove) {
        if let index = moneyMoves.firstIndex(where: { $0.id == moneyMove.id }) {
            let updatedMoneyMove = MoneyMove(
                title: moneyMove.title,
                subtitle: moneyMove.subtitle,
                iconName: moneyMove.iconName,
                iconBackgroundColor: moneyMove.iconBackgroundColor,
                estimatedMinutes: moneyMove.estimatedMinutes,
                learnerCount: moneyMove.learnerCount,
                isBookmarked: !moneyMove.isBookmarked
            )
            moneyMoves[index] = updatedMoneyMove
        }
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

    func chatWithBook(_ book: EducationBook) {
        print("Chat with book: \(book.title)")
    }

    func readKeyIdeas(_ book: EducationBook) {
        print("Read key ideas: \(book.title)")
    }

    func openDiscussion(_ discussion: CommunityDiscussion) {
        print("Open discussion by: \(discussion.authorName)")
    }

    func addCredits() {
        print("Add credits tapped")
    }
}
