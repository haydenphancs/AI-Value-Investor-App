//
//  PriceAlertsViewModel.swift
//  ios
//
//  The CREATE FORM behind the bell in the detail header.
//
//  It owns the draft only. The alerts themselves live in `PriceAlertStore`, because the
//  detail-header bell has to know whether a ticker has alerts BEFORE this view model exists —
//  it is constructed inside the sheet, i.e. only once the bell has been tapped. This type
//  survives because `ticker` is non-optional here and a price-target form has no meaning
//  without one; everything that is not the draft was promoted to the store.
//

import Combine
import Foundation

@MainActor
final class PriceAlertsViewModel: ObservableObject {

    @Published private(set) var isSaving = false

    // Draft state for the create form.
    @Published var draftKind: PriceAlertKind = .above
    @Published var draftThreshold: String = ""
    @Published var draftRepeat: PriceAlertRepeat = .once

    let ticker: String
    let assetType: String
    private let store: PriceAlertStore

    init(
        ticker: String,
        assetType: String = "stock",
        store: PriceAlertStore? = nil
    ) {
        self.ticker = ticker.uppercased()
        self.assetType = assetType
        self.store = store ?? PriceAlertStore.shared
    }

    var maxPerTicker: Int { store.maxPerTicker }

    /// True when this ticker is already at its per-ticker cap. Drives a disabled Add
    /// button with a reason, rather than letting the user fill in a form that will 409.
    /// ACTIVE only, matching the server's own quota.
    var atCap: Bool { store.activeCount(ticker: ticker) >= store.maxPerTicker }

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
        await store.load()
    }

    // MARK: - Mutate

    func create() async {
        guard let threshold = parsedThreshold else { return }
        isSaving = true
        defer { isSaving = false }
        // Server-first, inside the store: it mints the id, seeds `last_price` from a live
        // quote and decides `armed`. Reporting the failure is the store's job (auth.md §6).
        let created = await store.create(
            ticker: ticker,
            kind: draftKind,
            threshold: threshold,
            assetType: assetType,
            repeatMode: draftRepeat
        )
        guard created else { return }
        // `kind` is the fixed rule type. NEVER the ticker or the threshold — those
        // are user-specific values, useless as dimensions and a privacy footgun.
        Analytics.shared.track(.priceAlertCreated, ["kind": .string(draftKind.rawValue)])
        draftThreshold = ""
    }

    func toggleActive(_ alert: PriceAlertDTO) async {
        await store.toggleActive(alert)
    }

    func delete(_ alert: PriceAlertDTO) async {
        await store.delete(alert)
    }
}
