//
//  NotificationInboxView.swift
//  ios
//
//  The in-app record of every notification Caydex decided to send you.
//
//  WHY IT EXISTS — a push that arrives while the phone is face-down is gone. Before this
//  screen the app kept no record at all: the dedup ledger stored `(user_id, dedup_key,
//  sent_at)` and nothing else, so a missed banner was unrecoverable and "what was that
//  alert about NVDA?" had no answer anywhere in the product.
//
//  It shows rows the phone never buzzed for, too — deferred by quiet hours, or sent to an
//  account with no registered device. That is deliberate: those are exactly the
//  notifications a user has no other way to discover.
//
//  THIS IS NO LONGER THE PRIMARY SURFACE. The tab-bar badge now lives on Tracking, whose
//  "Alerts" segment shows the same list via `NotificationInboxContent` — because a badge on
//  a tab that could not clear it was the entire reported bug. This screen stays because two
//  things still reach it: `Profile → Notification History`, and a push whose payload cannot
//  be routed. It keeps the explicit "Mark all read" button; Alerts clears on sight instead.
//

import SwiftUI

struct NotificationInboxView: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel = NotificationInboxViewModel()

    var body: some View {
        NotificationInboxContent(viewModel: viewModel)
            .navigationTitle("Notifications")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                if viewModel.unreadCount > 0 {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Mark all read") {
                            Task { await viewModel.markAllRead() }
                        }
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                    }
                }
            }
            .task {
                Analytics.shared.track(.notificationInboxOpened)
                viewModel.load()
            }
            .refreshable { viewModel.load() }
        // ⚠️ NO `.onChange(of: viewModel.unreadCount, initial: true)` here.
        //
        // It was a SECOND writer of `appState.unreadNotificationCount`, and `initial: true`
        // made it publish the ViewModel's placeholder `0` on appear — BEFORE any page had
        // loaded. Opening the inbox therefore cleared the tab badge immediately, and if the
        // load then failed (offline, 503 NOTIFICATIONS_UNAVAILABLE) it stayed cleared: the
        // user's unread notifications were still unread, with nothing on screen saying so.
        //
        // The ViewModel already routes every REAL count through
        // `AppState.notificationUnreadDidChange(_:)` (load, next page, mark-read, mark-all),
        // which `iosApp` observes. One writer, and it only ever fires on data that exists.
    }
}

#Preview {
    // No `AppState.preview` exists in this codebase — previews build their own.
    NavigationStack {
        NotificationInboxView()
            .environment(AppState())
    }
}
