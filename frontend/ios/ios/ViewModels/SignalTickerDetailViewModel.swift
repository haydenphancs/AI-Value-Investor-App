//
//  SignalTickerDetailViewModel.swift
//  ios
//
//  Loads the per-ticker signal drill-down (who bought/added a ticker) from
//  `GET /api/v1/home/signals/{kind}/{ticker}` and maps it to a display model.
//

import SwiftUI

@MainActor
final class SignalTickerDetailViewModel: ObservableObject {
    @Published var detail: SignalTickerDetail?
    @Published var isLoading = false
    @Published var errorMessage: String?

    let kind: String        // "whale" | "congress"
    let ticker: String
    private let apiClient: APIClient

    init(kind: String, ticker: String, apiClient: APIClient = .shared) {
        self.kind = kind
        self.ticker = ticker
        self.apiClient = apiClient
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            let dto = try await apiClient.request(
                endpoint: .getSignalDetail(kind: kind, ticker: ticker),
                responseType: SignalTickerDetailDTO.self
            )
            detail = dto.toDisplay()
        } catch {
            // Never surface a raw backend string — route through AppError.
            errorMessage = AppError.from(error).message
        }
        isLoading = false
    }
}
