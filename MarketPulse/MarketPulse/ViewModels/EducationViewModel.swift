import SwiftUI
import Combine

@MainActor
class EducationLibraryViewModel: ObservableObject {
    @Published var allContent: [EducationContent] = []
    @Published var books: [EducationContent] = []
    @Published var articles: [EducationContent] = []
    @Published var selectedTab = 0 // 0: All, 1: Books, 2: Articles
    @Published var searchQuery = ""
    @Published var searchResults: [EducationContent] = []
    @Published var isLoading = false
    @Published var isSearching = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    private var searchTask: Task<Void, Never>?

    var displayedContent: [EducationContent] {
        if !searchQuery.isEmpty {
            return searchResults
        }

        switch selectedTab {
        case 1: return books
        case 2: return articles
        default: return allContent
        }
    }

    func loadContent() async {
        isLoading = true
        errorMessage = nil

        async let allData: () = loadAllContent()
        async let booksData: () = loadBooks()
        async let articlesData: () = loadArticles()

        await allData
        await booksData
        await articlesData

        isLoading = false
    }

    private func loadAllContent() async {
        do {
            allContent = try await apiService.getEducationContent(limit: 50)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadBooks() async {
        do {
            books = try await apiService.getEducationBooks()
        } catch {
            // Non-critical, ignore
        }
    }

    private func loadArticles() async {
        do {
            articles = try await apiService.getEducationArticles()
        } catch {
            // Non-critical, ignore
        }
    }

    func search() {
        searchTask?.cancel()

        guard !searchQuery.isEmpty else {
            searchResults = []
            return
        }

        searchTask = Task {
            isSearching = true

            try? await Task.sleep(nanoseconds: 300_000_000) // Debounce

            guard !Task.isCancelled else { return }

            do {
                searchResults = try await apiService.searchEducation(query: searchQuery)
            } catch {
                errorMessage = error.localizedDescription
            }

            isSearching = false
        }
    }
}

@MainActor
class EducationDetailViewModel: ObservableObject {
    @Published var content: EducationContent?
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var createdChatSession: ChatSession?

    private let apiService = APIService()
    let contentId: String

    init(contentId: String) {
        self.contentId = contentId
    }

    func loadContent() async {
        isLoading = true
        errorMessage = nil

        do {
            content = try await apiService.getEducationContentDetail(contentId: contentId)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func startChat() async {
        guard let content = content else { return }

        do {
            let request = ChatSessionCreate(
                sessionType: .education,
                title: "Chat about \(content.title)",
                stockId: nil,
                educationContentId: content.id
            )
            createdChatSession = try await apiService.createChatSession(request)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
