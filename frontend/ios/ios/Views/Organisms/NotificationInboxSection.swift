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
            // ONE row shape for the whole Alerts tab. This used to be a private
            // `NotificationRow` — a full-bleed square slab with no icon and an 8pt dot,
            // sitting beside the digest's rounded, tinted, shadowed cards. Same screen,
            // same importance, two visual languages.
            //
            // Its `.background(AppColors.cardBackground)` was also a bare colour with no
            // shape and no edge: #FFFFFF on the #F4F5F8 page in LIGHT mode, 1.09:1,
            // invisible. Dark separates by fill, which is why it looked fine in review.
            ActivityRow(
                systemName: item.iconName,
                iconColor: item.iconColor,
                title: item.title,
                subtitle: item.body,
                footnote: footnote(for: item),
                isNew: viewModel.showsUnreadDot(item),
                onTap: {
                    Task { await viewModel.markRead(item) }
                    // `.inbox` means the payload was unroutable — staying put is the
                    // honest outcome, and the row itself is already the content.
                    if item.destination != .inbox {
                        route.wrappedValue = item.destination
                    }
                }
            )
            .task { await viewModel.loadMoreIfNeeded(currentItem: item) }
        }

        if viewModel.isLoadingMore {
            ProgressView()
                .tint(AppColors.textSecondary)
                .padding(.vertical, AppSpacing.lg)
        }
    }

    /// Relative time, plus an explanation when the phone never buzzed.
    ///
    /// The delivery note is only shown when delivery did NOT happen — saying
    /// "delivered" on every other row would be permanent chrome carrying no information.
    private static func footnote(for item: NotificationEventDTO) -> String {
        let note: String?
        switch item.deliveryState {
        case "deferred":  note = "held during quiet hours"
        case "no_device": note = "not sent to this device"
        case "failed":    note = "couldn't be delivered"
        default:          note = nil
        }
        guard let note else { return item.relativeTime }
        return item.relativeTime.isEmpty ? note : "\(item.relativeTime) · \(note)"
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

// MARK: - Route presentation

/// `NotificationRoute` is an enum, and `fullScreenCover(item:)` needs `Identifiable`.
/// Boxing it here keeps the route type itself free of a UI protocol.
struct NotificationRouteBox: Identifiable {
    let route: NotificationRoute
    var id: String { String(describing: route) }
}
