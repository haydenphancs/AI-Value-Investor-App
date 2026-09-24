//
//  TrillionClubDetailViewModel.swift
//  ios
//
//  Loads one Trillion-Dollar Club company's drill-down from
//  `GET /api/v1/home/trillion-club/{slug}` and maps it to `TrillionClubDetail`. Mirrors
//  `ThemeDetailViewModel`, plus two things that screen does not need:
//
//   • RELOAD SAFETY. The screen reloads when a purchase lands (`entitlementGeneration`), so a
//     second load can start while the first is in flight. A generation counter drops the
//     stale answer — a slow Free response arriving after the Pro one would re-lock the list.
//   • KEEP WHAT WORKS. A failed reload keeps the detail already on screen and logs; only a
//     first load with nothing to show becomes the error state.
//
//  The tier gate is SERVER-side (`redact_trillion_club_detail`): a Free caller is never sent
//  the withheld holdings, so nothing here hides anything — it renders what arrived.
//

import Combine
import Foundation
import os

@MainActor
final class TrillionClubDetailViewModel: ObservableObject {
    @Published private(set) var detail: TrillionClubDetail?
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?

    let slug: String
    private let apiClient: APIClient
    private var loadGeneration = 0
    private let log = Logger(subsystem: "com.phan.caydex", category: "trillion-club")

    init(slug: String, apiClient: APIClient = .shared) {
        self.slug = slug
        self.apiClient = apiClient
    }

    /// Previews only: a view model that already holds a detail and never touches the network
    /// until `load()` is called.
    init(slug: String, preview detail: TrillionClubDetail?, errorMessage: String? = nil) {
        self.slug = slug
        self.apiClient = .shared
        self.detail = detail
        self.errorMessage = errorMessage
    }

    func load() async {
        loadGeneration &+= 1
        let generation = loadGeneration
        isLoading = true
        if detail == nil { errorMessage = nil }
        defer { if generation == loadGeneration { isLoading = false } }

        do {
            let dto = try await apiClient.request(
                endpoint: .getTrillionClubDetail(slug: slug),
                responseType: TrillionClubDetailDTO.self
            )
            guard generation == loadGeneration else { return }
            guard let mapped = TrillionClubDetail(dto: dto) else {
                // Decoded, but the company header was unreadable — there is no screen without it.
                log.error("trillion club detail \(self.slug, privacy: .public): payload had no readable company")
                if detail == nil {
                    errorMessage = "This company's stakes can't be shown right now."
                }
                return
            }
            detail = mapped
            errorMessage = nil
        } catch is CancellationError {
            // The screen went away mid-load; nothing to report.
            return
        } catch {
            guard generation == loadGeneration else { return }
            // Never surface a raw backend string — route through AppError.
            let appError = AppError.from(error)
            log.error("trillion club detail \(self.slug, privacy: .public) failed: \(String(describing: type(of: error)), privacy: .public): \(appError.message, privacy: .public)")
            if detail == nil {
                errorMessage = appError.message
            }
        }
    }
}

// MARK: - Preview fixtures

extension TrillionClubDetailViewModel {
    static var mockLocked: TrillionClubDetailViewModel {
        TrillionClubDetailViewModel(slug: "nvidia", preview: TrillionClubSamples.nvidiaDetailLocked)
    }

    static var mockError: TrillionClubDetailViewModel {
        TrillionClubDetailViewModel(slug: "nvidia", preview: nil,
                                    errorMessage: "The requested company could not be found.")
    }
}
