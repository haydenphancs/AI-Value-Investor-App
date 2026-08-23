//
//  NotificationInboxContent.swift
//  ios
//
//  Organism: the notification list itself — rows, paging, empty/error states and the
//  row-tap route presentation.
//
//  WHY IT IS SPLIT OUT. Two surfaces show this list: the Tracking tab's "Alerts" segment
//  (where the tab-bar badge now lives) and the standalone `NotificationInboxView` still
//  reachable from Profile and from an unroutable push. They MUST share one
//  `NotificationInboxViewModel`, because that view model is the single writer of
//  `AppState.notificationUnreadDidChange(_:)` — a second, forked copy of this list would
//  be a second writer of the badge, which is the exact bug the `.onChange` note in
//  `NotificationInboxView` documents.
//
//  Each surface owns its own INSTANCE (a `@StateObject` per screen); what is shared is the
//  type and the list UI, not one global object.
//

import SwiftUI

struct NotificationInboxContent: View {
    @Environment(AppState.self) private var appState
    @ObservedObject var viewModel: NotificationInboxViewModel

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
                        NotificationRow(item: item, isNew: viewModel.showsUnreadDot(item))
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
            Text("Alerts about the stocks and investors you track will show up here — even "
                 + "the ones you miss on your lock screen.")
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
    /// Whether to draw the "new" dot. NOT simply `!isRead`: the Alerts tab marks everything
    /// read the moment you look at it (that is what clears the badge), so keying the dot off
    /// read-state alone would blank every row exactly as it appeared. See
    /// `NotificationInboxViewModel.showsUnreadDot(_:)`.
    let isNew: Bool

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Circle()
                .fill(isNew ? AppColors.primaryGraphic : Color.clear)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(item.title)
                    .font(isNew ? AppTypography.bodyEmphasis : AppTypography.body)
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
        .accessibilityLabel("\(isNew ? "New. " : "")\(item.title). \(item.body)")
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
