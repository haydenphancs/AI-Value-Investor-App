//
//  NotificationInboxSection.swift
//  ios
//
//  The notification list — rows, paging and the non-content states — as PIECES a parent
//  emits, not as a self-contained screen.
//
//  WHY IT IS NOT A `View`. It used to be `NotificationInboxContent`, a View that owned its own
//  `ScrollView` + `LazyVStack`, which was fine while it was the entire body of two screens. It
//  is now one of THREE sections stacked in Tracking → Alerts, and three sections cannot each
//  own a scroll container.
//
//  Emitting the rows through a `@ViewBuilder` func rather than a child `View` struct is
//  deliberate and load-bearing:
//
//    • Nested `LazyVStack`s render their contents EAGERLY — only the outermost one, the
//      ScrollView's direct child, virtualizes. A nested one would materialize every row at
//      once on the main thread.
//    • A child `View` struct is an OPAQUE boundary to the enclosing lazy stack: it
//      re-eager-renders and breaks header pinning. A `@ViewBuilder` func splices its output
//      straight into the parent's subview list, so the rows really are children of the one
//      lazy stack.
//
//  Both traps are documented at `project_updates_screen_wiring` / `project_home_feed_lazyvstack_hang`;
//  the second one cost a 100%-CPU main-thread hang that left nothing in the simulator log.
//
//  The list is still backed by ONE `NotificationInboxViewModel`, which remains the single
//  writer of `AppState.notificationUnreadDidChange(_:)`.
//

import SwiftUI

enum NotificationInboxSection {

    /// The rows themselves, plus the paging spinner.
    ///
    /// Returns a `ForEach` (not a stack) so the caller's `LazyVStack` sees each row as its own
    /// subview and can virtualize them. Do NOT wrap this in a `VStack` at the call site.
    @ViewBuilder
    static func rows(
        viewModel: NotificationInboxViewModel,
        route: Binding<NotificationRoute?>
    ) -> some View {
        ForEach(viewModel.items) { item in
            Button {
                Task { await viewModel.markRead(item) }
                // `.inbox` means the payload was unroutable — staying put is the
                // honest outcome, and the row itself is already the content.
                if item.destination != .inbox {
                    route.wrappedValue = item.destination
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

    /// Nothing has ever been sent to this account.
    @ViewBuilder
    static func emptyNotice() -> some View {
        InlineRetryNotice(
            message: "No notifications yet. Alerts about the stocks and investors you track "
                + "will show up here — even the ones you miss on your lock screen.",
            systemImage: "bell.slash",
            // Not `caution`: an empty inbox is not a failure.
            iconColor: AppColors.textMuted
        )
    }

    /// Deliberately NOT the empty state. The backend answers 503 rather than an empty 200
    /// precisely so these two can look different — "you have none" rendered over a read
    /// failure is a bug nobody reports, because it looks intentional.
    @ViewBuilder
    static func errorNotice(_ message: String, onRetry: @escaping () -> Void) -> some View {
        InlineRetryNotice(message: message, onRetry: onRetry)
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
struct NotificationRouteBox: Identifiable {
    let route: NotificationRoute
    var id: String { String(describing: route) }
}
