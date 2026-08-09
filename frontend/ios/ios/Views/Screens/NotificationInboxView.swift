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

import SwiftUI

struct NotificationInboxView: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel = NotificationInboxViewModel()

    /// Set when a row is tapped; drives the same detail presentation Home uses.
    @State private var route: NotificationRoute?

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            switch viewModel.state {
            case .loading:
                ProgressView()
                    .tint(AppColors.textSecondary)

            case .empty:
                emptyState

            case .error(let message):
                errorState(message)

            case .loaded:
                list
            }
        }
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
        // One tap opens one screen: the route is cleared by the presentation, matching
        // how the push deep-link chain consumes `pendingPushRoute`.
        .fullScreenCover(item: Binding(
            get: { route.map(RouteBox.init) },
            set: { route = $0?.route }
        )) { box in
            NotificationRouteDestination(route: box.route)
                .environment(appState)
        }
    }

    // MARK: - List

    private var list: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: 1) {
                ForEach(viewModel.items) { item in
                    Button {
                        Task { await viewModel.markRead(item) }
                        // `.inbox` means the payload was unroutable — staying put is the
                        // honest outcome, and the row itself is already the content.
                        if item.destination != .inbox {
                            route = item.destination
                        }
                    } label: {
                        NotificationRow(item: item, isRead: viewModel.isRead(item))
                    }
                    .buttonStyle(.plain)
                    .task { await viewModel.loadMoreIfNeeded(currentItem: item) }
                }

                if viewModel.isLoadingMore {
                    ProgressView()
                        .tint(AppColors.textSecondary)
                        .padding(.vertical, AppSpacing.lg)
                }
            }
            .padding(.top, AppSpacing.sm)
        }
    }

    // MARK: - Non-content states

    private var emptyState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "bell.slash")
                .font(.system(size: 44))
                .foregroundColor(AppColors.textMuted)
            Text("No notifications yet")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text("Alerts about the stocks you track will show up here — even the ones you "
                 + "miss on your lock screen.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, AppSpacing.xxl)
    }

    private func errorState(_ message: String) -> some View {
        // Deliberately NOT the empty state. The backend answers 503 rather than an empty
        // 200 precisely so these two can look different — "you have none" rendered over a
        // read failure is a bug nobody reports, because it looks intentional.
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 44))
                .foregroundColor(AppColors.caution)
            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
            Button("Try Again") { viewModel.load() }
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.primaryBlue)
        }
        .padding(.horizontal, AppSpacing.xxl)
    }
}

// MARK: - Row

private struct NotificationRow: View {
    let item: NotificationEventDTO
    let isRead: Bool

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Circle()
                .fill(isRead ? Color.clear : AppColors.primaryGraphic)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(item.title)
                    .font(isRead ? AppTypography.body : AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(item.body)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: AppSpacing.xs) {
                    Text(item.relativeTime)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)

                    // Only shown when the phone did NOT buzz. Saying "delivered" on every
                    // other row would be permanent chrome carrying no information.
                    if let note = undeliveredNote {
                        Text("· \(note)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }

            Spacer(minLength: 0)

            Image(systemName: "chevron.right")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, 2)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(isRead ? "" : "Unread. ")\(item.title). \(item.body)")
    }

    /// Explains a row the user is seeing here FIRST.
    private var undeliveredNote: String? {
        switch item.deliveryState {
        case "deferred":  return "held during quiet hours"
        case "no_device": return "not sent to this device"
        case "failed":    return "couldn't be delivered"
        default:          return nil
        }
    }
}

// MARK: - Route presentation

/// `NotificationRoute` is an enum, and `fullScreenCover(item:)` needs `Identifiable`.
/// Boxing it here keeps the route type itself free of a UI protocol.
private struct RouteBox: Identifiable {
    let route: NotificationRoute
    var id: String { String(describing: route) }
}

#Preview {
    // No `AppState.preview` exists in this codebase — previews build their own.
    NavigationStack {
        NotificationInboxView()
            .environment(AppState())
    }
}
