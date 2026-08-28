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
    ///
    /// `items` is passed in rather than read off the view model because the Activity section
    /// filters it. Read state, paging and the unread count still come from `viewModel`, which
    /// remains the single owner of all three.
    ///
    /// `selection` receives the whole `CollapsedGroup`, not a route: the detail screen needs
    /// every member to list them, and it derives the destinations itself.
    @ViewBuilder
    static func rows(
        viewModel: NotificationInboxViewModel,
        items: [NotificationEventDTO],
        selection: Binding<CollapsedGroup?>
    ) -> some View {
        let groups = collapse(items)
        // Paging is decided from the FLAT list, never from the collapsed groups.
        //
        // The trigger is "is this row near the end of what we fetched". Measuring that on
        // the groups is wrong twice over: a page that collapses heavily produces fewer rows
        // than the trigger window, so nothing ever satisfies it and the list silently stops —
        // the same failure as the model-tail trigger this replaced, one level up.
        let pagingTrigger = Set(items.suffix(3).map(\.id))

        ForEach(groups) { group in
            let item = group.newest
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
                // ANY unread member lights the dot. A collapsed row that looked read while
                // hiding an unread one would be a notification the user can never find.
                isNew: group.items.contains { viewModel.showsUnreadDot($0) },
                onTap: {
                    // EVERY member, not just the one on screen. The others have no row of
                    // their own any more, so marking only the newest would strand them
                    // unread forever and hold the badge up with nothing to clear it.
                    Task {
                        for member in group.items { await viewModel.markRead(member) }
                    }
                    // The DETAIL screen, not the destination. A tap used to land straight on
                    // the ticker, which meant the alert's own text existed nowhere but this
                    // row — clamped to three lines. `NotificationDetailView` shows it whole
                    // and then offers where to go.
                    //
                    // Opened even for an unroutable payload. The old code stayed put on
                    // `.inbox` because "the row itself is already the content"; that stopped
                    // being true the moment the row started truncating.
                    selection.wrappedValue = group
                },
                trailing: {
                    if group.count > 1 {
                        TintedTagBadge(text: "×\(group.count)", color: AppColors.textSecondary)
                    }
                }
            )
            .task {
                guard group.items.contains(where: { pagingTrigger.contains($0.id) }) else {
                    return
                }
                await viewModel.loadNextPage()
            }
        }

        if viewModel.isLoadingMore {
            ProgressView()
                .tint(AppColors.textSecondary)
                .padding(.vertical, AppSpacing.lg)
        }
    }

    // MARK: - Collapsing repeats

    /// One rendered row: a single notification, or several ADJACENT ones saying the same
    /// thing about the same ticker.
    struct CollapsedGroup: Identifiable {
        /// Newest first, matching the feed order. Never empty.
        let items: [NotificationEventDTO]
        var id: String { items[0].id }
        var newest: NotificationEventDTO { items[0] }
        var count: Int { items.count }
    }

    /// Merge CONSECUTIVE rows that share a ticker and a kind.
    ///
    /// WHY. A tester read two `ticker_move` alerts for CRM on consecutive days as one
    /// duplicate. They were two genuinely separate >=2-sigma sessions — the dedup key is
    /// `move:{TICKER}:{ET-date}` — but the title was the bare ticker on both and the bodies
    /// were two Gemini paraphrases of the same earnings story, so they read as a repeat.
    /// The server now puts the percentage in the title, which fixes it going forward; this
    /// fixes the rows already in the inbox, and any future run of genuinely repetitive
    /// alerts about one ticker.
    ///
    /// ⚠️ ADJACENT ONLY, and the feed is never reordered. Grouping globally by ticker would
    /// pull a week-old row up next to today's and silently rewrite the chronology, which is
    /// the same "sorting on invented data" the Upcoming/Activity split exists to avoid.
    ///
    /// A row with no ticker in its route is never collapsed — `groupKey` returns nil and it
    /// starts its own group. Two unroutable rows are not evidence of the same event.
    static func collapse(_ items: [NotificationEventDTO]) -> [CollapsedGroup] {
        var groups: [CollapsedGroup] = []
        for item in items {
            if let key = groupKey(item),
               let last = groups.last,
               key == groupKey(last.newest) {
                groups[groups.count - 1] = CollapsedGroup(items: last.items + [item])
            } else {
                groups.append(CollapsedGroup(items: [item]))
            }
        }
        return groups
    }

    /// `nil` = never collapse this row.
    private static func groupKey(_ item: NotificationEventDTO) -> String? {
        let ticker = (item.route["ticker"] ?? "").uppercased()
        guard !ticker.isEmpty else { return nil }
        return "\(item.kind)|\(ticker)"
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
