//
//  PushNotificationDetailScreen.swift
//  ios
//
//  What tapping a notification OUTSIDE the app opens — the lock screen, Notification Center,
//  a banner, or its "View" action.
//

import SwiftUI

/// The detail of a tapped push: the SAME `NotificationDetailView` Tracking → Alerts opens for a
/// row, fed from the push.
///
/// WHY. A push tap used to open the ticker (or report) screen directly, so the alert's own words
/// were gone the moment it was tapped. The developer: *"open the detail screen first, so they can
/// read the content before they decide to go any further. Not to open the ticker right away."*
///
/// Reusing the Alerts screen rather than drawing a second one is the point: the two doors show
/// the same thing and offer the same destinations (`AlertDestination.destinations(for:)`), and
/// cannot drift apart.
///
/// Like `NotificationDetailView`, this screen NAVIGATES NOWHERE. The choice goes up through
/// `onOpen`, and `ContentView` closes the sheet and opens the destination in a cover — see
/// `AlertDestinationCover` for why a destination must never be pushed inside a sheet.
struct PushNotificationDetailScreen: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel: PushNotificationDetailViewModel

    var onOpen: (AlertDestination) -> Void

    init(pushed: PushedNotification, onOpen: @escaping (AlertDestination) -> Void) {
        _viewModel = StateObject(wrappedValue: PushNotificationDetailViewModel(pushed: pushed))
        self.onOpen = onOpen
    }

    var body: some View {
        NotificationDetailView(group: viewModel.group, onOpen: onOpen)
            .task { await viewModel.load() }
            // A cold launch FROM the tap usually lands here before the session is armed, so the
            // first fetch is refused pre-flight. Nothing else would ask again — the tabs' own
            // reload triggers deliberately skip the launch hop `.restoring → .authenticated` —
            // so this screen heals itself, and only when it is actually owed the row.
            .onChange(of: appState.auth.status) { _, status in
                guard status == .authenticated, viewModel.awaitingSession else { return }
                Task { await viewModel.load() }
            }
    }
}

#Preview("Ticker move") {
    NavigationStack {
        PushNotificationDetailScreen(
            pushed: PushedNotification(
                identifier: "preview-1",
                title: "TER -10.4%",
                body: "Down 10.4% in today's session — no single company-specific catalyst found in current sources. Open TER for the latest coverage.",
                userInfo: [
                    "kind": "ticker_move", "route": "ticker", "ticker": "TER",
                    "asset_type": "stock", "dedup_key": "move:TER:2026-09-14",
                ],
                deliveredAt: Date()
            ),
            onOpen: { _ in }
        )
    }
    .environment(AppState())
}

#Preview("No destination") {
    NavigationStack {
        PushNotificationDetailScreen(
            pushed: PushedNotification(
                identifier: "preview-2",
                title: "Your analysis couldn't finish",
                body: "Your credits have been returned.",
                userInfo: ["kind": "research_failed"],
                deliveredAt: Date()
            ),
            onOpen: { _ in }
        )
    }
    .environment(AppState())
}
