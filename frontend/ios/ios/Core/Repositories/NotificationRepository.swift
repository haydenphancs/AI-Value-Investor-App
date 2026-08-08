//
//  NotificationRepository.swift
//  ios
//
//  Network access for the notification inbox and user-set price alerts.
//
//  Mirrors `AccountRepository`'s protocol + implementation shape so a ViewModel can be
//  tested against a fake without a URLSession. Every call goes through `APIClient` —
//  never a direct `URLSession` — so auth, retries, the 401 refresh interceptor and error
//  mapping all apply uniformly.
//

import Foundation

protocol NotificationRepositoryProtocol: Sendable {
    func fetchNotifications(limit: Int, before: String?) async throws -> NotificationListDTO
    func markRead(ids: [String]) async throws -> MarkNotificationsReadDTO
    func markAllRead() async throws -> MarkNotificationsReadDTO

    func fetchPriceAlerts(ticker: String?) async throws -> PriceAlertListDTO
    func createPriceAlert(
        ticker: String, kind: PriceAlertKind, threshold: Double,
        assetType: String, repeatMode: PriceAlertRepeat
    ) async throws -> PriceAlertDTO
    func updatePriceAlert(
        id: String, threshold: Double?, isActive: Bool?, repeatMode: PriceAlertRepeat?
    ) async throws -> PriceAlertDTO
    func deletePriceAlert(id: String) async throws -> Bool
}

struct NotificationRepository: NotificationRepositoryProtocol {
    private let apiClient: APIClient

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    // MARK: - Inbox

    func fetchNotifications(limit: Int = 30, before: String? = nil) async throws -> NotificationListDTO {
        try await apiClient.request(
            endpoint: .listNotifications(limit: limit, before: before),
            responseType: NotificationListDTO.self
        )
    }

    func markRead(ids: [String]) async throws -> MarkNotificationsReadDTO {
        try await apiClient.request(
            endpoint: .markNotificationsRead(ids: ids, all: false),
            responseType: MarkNotificationsReadDTO.self
        )
    }

    /// One request, not N. "Mark all read" on a full inbox would otherwise be dozens of
    /// round trips, each able to fail independently and leave the badge wrong.
    func markAllRead() async throws -> MarkNotificationsReadDTO {
        try await apiClient.request(
            endpoint: .markNotificationsRead(ids: [], all: true),
            responseType: MarkNotificationsReadDTO.self
        )
    }

    // MARK: - Price alerts

    func fetchPriceAlerts(ticker: String? = nil) async throws -> PriceAlertListDTO {
        try await apiClient.request(
            endpoint: .listPriceAlerts(ticker: ticker),
            responseType: PriceAlertListDTO.self
        )
    }

    func createPriceAlert(
        ticker: String,
        kind: PriceAlertKind,
        threshold: Double,
        assetType: String,
        repeatMode: PriceAlertRepeat
    ) async throws -> PriceAlertDTO {
        try await apiClient.request(
            endpoint: .createPriceAlert(
                ticker: ticker.uppercased(),
                kind: kind.rawValue,
                threshold: threshold,
                assetType: assetType,
                repeatMode: repeatMode.rawValue
            ),
            responseType: PriceAlertDTO.self
        )
    }

    func updatePriceAlert(
        id: String,
        threshold: Double? = nil,
        isActive: Bool? = nil,
        repeatMode: PriceAlertRepeat? = nil
    ) async throws -> PriceAlertDTO {
        try await apiClient.request(
            endpoint: .updatePriceAlert(
                id: id, threshold: threshold, isActive: isActive,
                repeatMode: repeatMode?.rawValue
            ),
            responseType: PriceAlertDTO.self
        )
    }

    func deletePriceAlert(id: String) async throws -> Bool {
        try await apiClient.request(
            endpoint: .deletePriceAlert(id: id),
            responseType: DeletePriceAlertDTO.self
        ).deleted
    }
}
