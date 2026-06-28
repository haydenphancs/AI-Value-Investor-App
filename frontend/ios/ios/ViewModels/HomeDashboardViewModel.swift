//
//  HomeDashboardViewModel.swift
//  ios
//
//  ViewModel for the redesigned Home dashboard — MVVM + Repository.
//
//  Owns data fetching via `HomeRepositoryProtocol` and exposes view-ready state.
//  Defaults to `MockHomeRepository` (UI only, no backend) but accepts any
//  conforming repository via init for testing / a future live implementation.
//  Matches the codebase convention: `ObservableObject` + `@Published`, with
//  boolean loading / error flags (see HomeViewModel).
//

import Foundation
import Combine

@MainActor
final class HomeDashboardViewModel: ObservableObject {

    // MARK: - Published state
    @Published private(set) var data: HomeDashboardData?
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var errorMessage: String?

    // MARK: - Dependencies
    private let repository: HomeRepositoryProtocol

    // MARK: - Init
    // Optional + nil-coalesce (matches the codebase's repository-injection idiom,
    // e.g. SearchViewModel) so the default mock is built inside the @MainActor
    // init rather than in a nonisolated default-argument context.
    init(repository: HomeRepositoryProtocol? = nil) {
        self.repository = repository ?? MockHomeRepository()
    }

    // MARK: - Loading

    /// Initial load — call from `.task` so it runs once when the screen appears.
    func loadIfNeeded() async {
        guard data == nil else { return }
        await load()
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            data = try await repository.fetchHomeDashboard()
        } catch {
            errorMessage = "Unable to load your dashboard. Pull to refresh."
            #if DEBUG
            print("❌ [HomeDashboardVM] load failed: \(type(of: error)): \(error)")
            #endif
        }
        isLoading = false
    }

    /// Pull-to-refresh.
    func refresh() async {
        await load()
    }
}
