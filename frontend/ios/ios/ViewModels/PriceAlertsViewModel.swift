//
//  PriceAlertsViewModel.swift
//  ios
//
//  CRUD for the price alerts on one ticker, behind the bell in the detail header.
//

import Combine
import Foundation

@MainActor
final class PriceAlertsViewModel: ObservableObject {

    enum State: Equatable {
        case loading
        case loaded
        case error(String)
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var alerts: [PriceAlertDTO] = []
    @Published private(set) var maxPerTicker: Int = 3
    @Published private(set) var isSaving = false

    // Draft state for the create form.
    @Published var draftKind: PriceAlertKind = .above
    @Published var draftThreshold: String = ""
    @Published var draftRepeat: PriceAlertRepeat = .once

    let ticker: String
    let assetType: String
    private let repository: NotificationRepositoryProtocol

    init(
        ticker: String,
        assetType: String = "stock",
        repository: NotificationRepositoryProtocol = NotificationRepository()
    ) {
        self.ticker = ticker.uppercased()
        self.assetType = assetType
        self.repository = repository
    }

    /// True when this ticker is already at its per-ticker cap. Drives a disabled Add
    /// button with a reason, rather than letting the user fill in a form that will 409.
    var atCap: Bool { alerts.filter(\.isActive).count >= maxPerTicker }

    var parsedThreshold: Double? {
        // Accept a leading "$" or a trailing "%" — people type what the field means.
        let cleaned = draftThreshold
            .replacingOccurrences(of: "$", with: "")
            .replacingOccurrences(of: "%", with: "")
            .replacingOccurrences(of: ",", with: "")
            .trimmingCharacters(in: .whitespaces)
        guard let value = Double(cleaned), value.isFinite, value > 0 else { return nil }
        // Mirrors the backend's own ceiling so the refusal happens before a round trip.
        if draftKind.isPercent && value > 100 { return nil }
        return value
    }

    var canSave: Bool { parsedThreshold != nil && !isSaving && !atCap }

    // MARK: - Load

    func load() async {
        do {
            let page = try await repository.fetchPriceAlerts(ticker: ticker)
            alerts = page.items
            maxPerTicker = page.maxPerTicker
            state = .loaded
        } catch {
            state = .error(AppError.from(error).message)
        }
    }

    // MARK: - Mutate

    func create() async {
        guard let threshold = parsedThreshold else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            let created = try await repository.createPriceAlert(
                ticker: ticker,
                kind: draftKind,
                threshold: threshold,
                assetType: assetType,
                repeatMode: draftRepeat
            )
            alerts.insert(created, at: 0)
            // `kind` is the fixed rule type. NEVER the ticker or the threshold — those
            // are user-specific values, useless as dimensions and a privacy footgun.
            Analytics.shared.track(.priceAlertCreated, ["kind": .string(draftKind.rawValue)])
            draftThreshold = ""
            state = .loaded
        } catch {
            // Never a bare `try?` and never a DEBUG-only print: a user-initiated mutation
            // that fails silently looks like a UI glitch and leaves no trace anywhere.
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "create that price alert"
            )
        }
    }

    func toggleActive(_ alert: PriceAlertDTO) async {
        do {
            let updated = try await repository.updatePriceAlert(
                id: alert.id, threshold: nil, isActive: !alert.isActive, repeatMode: nil
            )
            replace(updated)
        } catch {
            AppActions.shared.reportMutationFailure(
                AppError.from(error),
                action: alert.isActive ? "turn off that alert" : "turn on that alert"
            )
            // Re-read rather than guess: the row's `armed` state also changes server-side
            // when it is re-enabled, and showing a stale one is how "my alert never fires"
            // becomes unexplainable.
            await load()
        }
    }

    func delete(_ alert: PriceAlertDTO) async {
        // Optimistic in MEMORY only — nothing is persisted before the server confirms.
        let snapshot = alerts
        alerts.removeAll { $0.id == alert.id }
        do {
            _ = try await repository.deletePriceAlert(id: alert.id)
        } catch {
            alerts = snapshot
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "delete that price alert"
            )
        }
    }

    private func replace(_ updated: PriceAlertDTO) {
        if let index = alerts.firstIndex(where: { $0.id == updated.id }) {
            alerts[index] = updated
        }
    }
}
