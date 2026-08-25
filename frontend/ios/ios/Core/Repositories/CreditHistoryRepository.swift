//
//  CreditHistoryRepository.swift
//  ios
//
//  Network access for the credit statement (Account → Credit History).
//
//  Mirrors `NotificationRepository`'s protocol + implementation shape so the ViewModel
//  can be driven by a fake in previews without a URLSession. Every call goes through
//  `APIClient` — never a direct `URLSession` — so auth, retries, the 401 refresh
//  interceptor and error mapping all apply uniformly.
//
//  READ-ONLY by construction: there is no write surface on the ledger from the client.
//

import Foundation

protocol CreditHistoryRepositoryProtocol: Sendable {
    func fetchCreditHistory(limit: Int, before: String?) async throws -> CreditHistoryDTO
}

struct CreditHistoryRepository: CreditHistoryRepositoryProtocol {
    private let apiClient: APIClient

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    func fetchCreditHistory(limit: Int = 30, before: String? = nil) async throws -> CreditHistoryDTO {
        try await apiClient.request(
            endpoint: .listCreditHistory(limit: limit, before: before),
            responseType: CreditHistoryDTO.self
        )
    }
}
