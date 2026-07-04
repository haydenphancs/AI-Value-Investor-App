//
//  ThemeDetailViewModel.swift
//  ios
//
//  Loads the Emerging Frontiers theme drill-down (hero + constituent companies)
//  from `GET /api/v1/home/themes/{slug}` and maps it to a display model.
//  Mirrors `SignalTickerDetailViewModel`.
//

import Foundation
import Combine

@MainActor
final class ThemeDetailViewModel: ObservableObject {
    @Published var detail: ThemeDetail?
    @Published var isLoading = false
    @Published var errorMessage: String?

    let slug: String
    private let apiClient: APIClient

    init(slug: String, apiClient: APIClient = .shared) {
        self.slug = slug
        self.apiClient = apiClient
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            let dto = try await apiClient.request(
                endpoint: .getThemeDetail(slug: slug),
                responseType: ThemeDetailDTO.self
            )
            detail = dto.toDisplay()
        } catch {
            // Never surface a raw backend string — route through AppError.
            errorMessage = AppError.from(error).message
        }
        isLoading = false
    }
}
