//
//  ThemeDetailViewModel.swift
//  ios
//
//  Loads the Emerging Frontiers theme drill-down (hero + constituent companies)
//  from `GET /api/v1/home/themes/{slug}` and maps it to a display model.
//  Mirrors `SignalTickerDetailViewModel`, plus (since the plan gate, 2026-10-04) the two
//  things `TrillionClubDetailViewModel` needs for the same reason:
//
//   • RELOAD SAFETY. The screen reloads when a purchase lands (`entitlementGeneration`), so a
//     second load can start while the first is in flight. A generation counter drops the
//     stale answer — a slow Free response arriving after the Pro one would re-lock the list.
//   • KEEP WHAT WORKS. A failed reload keeps the detail already on screen and logs; only a
//     first load with nothing to show becomes the error state.
//
//  The tier gate is SERVER-side (`theme_detail_redaction.py`): a Free caller is never sent the
//  withheld companies, so nothing here hides anything — it renders what arrived.
//

import Foundation
import Combine
import os

@MainActor
final class ThemeDetailViewModel: ObservableObject {
    @Published var detail: ThemeDetail?
    @Published var isLoading = false
    @Published var errorMessage: String?

    let slug: String
    private let apiClient: APIClient
    private var loadGeneration = 0
    private let log = Logger(subsystem: "com.phan.caydex", category: "theme-detail")

    init(slug: String, apiClient: APIClient = .shared) {
        self.slug = slug
        self.apiClient = apiClient
    }

    func load() async {
        loadGeneration &+= 1
        let generation = loadGeneration
        isLoading = true
        if detail == nil { errorMessage = nil }
        defer { if generation == loadGeneration { isLoading = false } }

        do {
            let dto = try await apiClient.request(
                endpoint: .getThemeDetail(slug: slug),
                responseType: ThemeDetailDTO.self
            )
            guard generation == loadGeneration else { return }
            detail = dto.toDisplay()
            errorMessage = nil
        } catch {
            // APIClient wraps cancellation, so `catch is CancellationError` never matches —
            // the screen went away mid-load; nothing to report.
            guard !Task.isCancelled, generation == loadGeneration else { return }
            // Never surface a raw backend string — route through AppError.
            let appError = AppError.from(error)
            log.error("theme detail \(self.slug, privacy: .public) failed: \(String(describing: type(of: error)), privacy: .public): \(appError.message, privacy: .public)")
            if detail == nil {
                errorMessage = appError.message
            }
        }
    }
}
