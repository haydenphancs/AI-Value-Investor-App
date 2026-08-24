//
//  AlertsTabContent.swift
//  ios
//
//  Tracking → Alerts: everything alert-shaped in the app, in one place.
//
//  WHY THIS EXISTS IN THIS SHAPE. Three unrelated features were all called "Alerts", and only
//  one of them lived in the Alerts tab:
//
//    • delivered notification EVENTS      — this tab
//    • "Alerts & Upcoming Events" digest  — the ASSETS tab, of all places
//    • user-created price-alert RULES     — only ever reachable from the bell on a detail
//                                           screen, so there was nowhere to see them all
//
//  and a fourth surface, `Profile → Notification History`, was a second copy of the first one.
//  That copy is gone: it showed the identical list through the identical view model, while the
//  tab-bar badge pointed HERE, so it was the one surface the badge could not lead you to.
//
//  The three sections are titled so that nothing inside a tab called "Alerts" is also called
//  "Alerts": Upcoming & Events / Price Rules / Notifications.
//
//  THE FIX FOR THE ORIGINAL REPORTED BUG, preserved here because it explains the read
//  semantics below. The tab-bar badge counts unread `notification_events`, and it used to sit
//  on Updates — a news feed with no connection to the inbox — so tapping the badged tab could
//  not clear it ("Updates shows 6, touch it and move out, it still shows 6"). The badge sits on
//  Tracking now and THIS is what it points at.
//
//  Which is why the notification list marks everything read ON SIGHT rather than behind a
//  button: a badge the user cannot clear by looking at the thing it counts is the bug, not the
//  fix. `showsUnreadDot` keeps the dots visible for the current viewing, so the screen you
//  opened to see what was new still tells you.
//
//  ⚠️ ONE LAZY STACK, and it is the ScrollView's direct child. Nested `LazyVStack`s render
//  eagerly, and a child `View` struct is an opaque boundary that re-eager-renders — so every
//  section is emitted by a `@ViewBuilder` func here, and the notification rows come from
//  `NotificationInboxSection.rows` as a bare `ForEach` spliced straight into this stack.
//  See `project_home_feed_lazyvstack_hang`: the same mistake on Home was a permanent
//  100%-CPU main-thread hang that left nothing in the simulator log.
//
//  ⚠️ NO `.onChange(of: notifications.unreadCount, initial: true)` here, ever.
//
//  It was a SECOND writer of `appState.unreadNotificationCount`, and `initial: true` made it
//  publish the ViewModel's placeholder `0` on appear — BEFORE any page had loaded. Opening the
//  list therefore cleared the tab badge immediately, and if the load then failed (offline, 503
//  NOTIFICATIONS_UNAVAILABLE) it stayed cleared: the user's unread notifications were still
//  unread, with nothing on screen saying so.
//
//  `NotificationInboxViewModel` already routes every REAL count through
//  `AppState.notificationUnreadDidChange(_:)` (load, next page, mark-read, mark-all), which
//  `iosApp` observes. One writer, and it only ever fires on data that exists.
//

import SwiftUI

struct AlertsTabContent: View {
    @Environment(AppState.self) private var appState
    /// Injected by `ContentView` and read here so leaving the Tracking tab and coming back
    /// refreshes a list that is now the ONLY place these notifications can be seen.
    @Environment(\.isActiveTab) private var isActiveTab

    /// Owned by the Tracking screen — the "Upcoming & Events" digest and its detail sheet both
    /// hang off this, so it must be the SAME instance the Assets tab uses, not a new one.
    @ObservedObject var trackingViewModel: TrackingViewModel

    @StateObject private var notifications = NotificationInboxViewModel()
    @StateObject private var priceRules = PriceAlertRulesViewModel()

    /// Set when a notification row is tapped; drives the same detail presentation Home uses.
    @State private var route: NotificationRoute?

    var body: some View {
        ScrollView(showsIndicators: false) {
            // THE one lazy stack. Do not add another inside any section below.
            LazyVStack(spacing: AppSpacing.lg) {
                upcomingSection()
                activitySection()
                priceRulesSection()
            }
            .padding(.top, AppSpacing.sm)
            .padding(.bottom, AppSpacing.xxxl)
        }
        .refreshable { await refreshAll() }
        // One tap opens one screen: the route is cleared by the presentation, matching
        // how the push deep-link chain consumes `pendingPushRoute`.
        .fullScreenCover(item: Binding(
            get: { route.map(NotificationRouteBox.init) },
            set: { route = $0?.route }
        )) { box in
            NotificationRouteDestination(route: box.route)
                .environment(appState)
        }
        // auth.md §7 — this tab shows three lists of the CALLER'S OWN data on device-global
        // view models. Without this the next account to sign in on the phone inherits the
        // previous user's notifications and price rules.
        .reloadOnIdentityChange { isActive in
            notifications.reset()
            priceRules.reset()
            guard isActive else { return }
            await loadAll()
        }
        // The first load, and every re-entry. `.task` alone fires when the SEGMENT is picked
        // (the parent's switch tears this view down), but NOT when the user leaves Tracking
        // entirely and returns with Alerts already selected — which, now that this is the only
        // notification surface, would leave a stale list with no way to notice.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            await loadAll()
        }
        // Heal a load that RACED session restore.
        //
        // Neither existing trigger covers this. `.reloadOnIdentityChange` deliberately does
        // not fire on the launch hop `.restoring → .authenticated` (`identityGeneration` does
        // not move there — that hop is "discovering an identity", not changing one, and firing
        // would re-fetch every tab on every launch). `.task(id: isActiveTab)` only re-runs
        // when the tab is switched away from and back.
        //
        // Landing DIRECTLY here from a push tap is a new case where both are silent: at cold
        // launch the first load runs while auth is still `.restoring`, both account-scoped
        // sections render "Reconnecting…", and nothing ever re-ran them. Observed live — the
        // list sat on "Reconnecting your account…" indefinitely and only a manual pull fixed
        // it, on a screen the user was sent to by a notification.
        //
        // Gated on actually being in an auth-blocked state, so a genuine sign-in (already
        // covered by `.reloadOnIdentityChange`) does not double-fetch.
        .onChange(of: appState.auth.status) { _, status in
            guard status == .authenticated, isAuthBlocked else { return }
            Task { await loadAll() }
        }
    }

    /// True when a section is showing "you're not signed in" / "reconnecting" copy — i.e. the
    /// last load was answered by the auth guard rather than by data.
    private var isAuthBlocked: Bool {
        let blockedNotifications = notifications.state == .reconnecting
            || notifications.state == .signedOut
        let blockedRules = priceRules.state == .reconnecting || priceRules.state == .signedOut
        return blockedNotifications || blockedRules
    }

    // MARK: - Loading

    private func loadAll() async {
        Analytics.shared.track(.notificationInboxOpened)
        // Await the first page BEFORE marking read — that is what clears the badge, and there
        // is nothing to mark until the page lands.
        await notifications.loadAndWait()
        await notifications.markAllReadOnView()
        await priceRules.loadIfStale()
    }

    /// Pull-to-refresh. AWAITED, unlike the fire-and-forget `load()` this replaced — an
    /// un-awaited refresh dismisses the spinner before the data lands, which reads as
    /// "refreshing did nothing".
    private func refreshAll() async {
        await notifications.loadAndWait()
        await notifications.markAllReadOnView()
        await priceRules.load()
        await trackingViewModel.refresh()
    }

    // MARK: - Section 1 — Upcoming

    /// Forward-looking digest items only: earnings dates and dated market events.
    ///
    /// `GET /tracking/assets` is guest-allowed, so this is the one section with content
    /// for a signed-out user.
    @ViewBuilder
    private func upcomingSection() -> some View {
        AlertsEventsSection(
            title: "Upcoming",
            emptyMessage: "Nothing scheduled for what you track. Earnings dates will "
                + "appear here as they're confirmed.",
            emptyIcon: "calendar",
            alerts: trackingViewModel.filteredAlerts.filter(\.isUpcoming),
            onAlertTapped: { trackingViewModel.viewAlertDetail($0) }
        )
    }

    // MARK: - Section 2 — Activity

    /// Everything that already HAPPENED, in one grammar: the digest's roll-ups first,
    /// then the delivered notifications, newest first.
    ///
    /// ⚠️ NOT interleaved chronologically, and that is deliberate. The roll-up containers
    /// (`WhaleTradeAlertData` / `AnalystRatingAlertData` / `InsiderTransactionAlertData`)
    /// carry a `timeWindowLabel` like "this week" and NO date; their items have a day and
    /// month with no year. Sorting them against `NotificationEventDTO.createdAt` would
    /// mean fabricating a timestamp, and a feed that sorts on invented data is worse than
    /// one that groups honestly. Roll-ups are this week's summary, so they lead.
    @ViewBuilder
    private func activitySection() -> some View {
        let rollups = trackingViewModel.filteredAlerts.filter { !$0.isUpcoming }

        SectionHeader(title: "Activity")
            .padding(.horizontal, AppSpacing.lg)

        ForEach(rollups) { alert in
            AlertCardView(alert: alert) { trackingViewModel.viewAlertDetail(alert) }
                .padding(.horizontal, AppSpacing.lg)
        }

        notificationRows(hasRollups: !rollups.isEmpty)
    }

    /// The notification half of Activity.
    ///
    /// `hasRollups` decides whether an EMPTY notification list is worth saying out loud:
    /// with roll-ups above it, "No notifications yet" under a populated section reads as
    /// a broken sub-list rather than a quiet inbox.
    @ViewBuilder
    private func notificationRows(hasRollups: Bool) -> some View {
        switch notifications.state {
        case .loading:
            ProgressView()
                .tint(AppColors.textSecondary)
                .frame(maxWidth: .infinity)

        case .empty:
            if !hasRollups {
                NotificationInboxSection.emptyNotice()
                    .padding(.horizontal, AppSpacing.lg)
            }

        case .reconnecting:
            InlineRetryNotice(
                message: "Reconnecting your account…",
                systemImage: "arrow.clockwise",
                iconColor: AppColors.textMuted
            )
            .padding(.horizontal, AppSpacing.lg)

        case .signedOut:
            InlineRetryNotice(
                message: "Sign in to see your notifications. They're kept on your account, so "
                    + "an alert you miss on your lock screen is still here later.",
                systemImage: "person.crop.circle.badge.checkmark",
                iconColor: AppColors.textMuted,
                retryTitle: "Sign In",
                onRetry: { appState.requestSignIn(for: "see your notifications") }
            )
            .padding(.horizontal, AppSpacing.lg)

        case .error(let message):
            NotificationInboxSection.errorNotice(message) { notifications.load() }
                .padding(.horizontal, AppSpacing.lg)

        case .loaded:
            // A bare `ForEach`, spliced DIRECTLY into the LazyVStack above — no wrapper
            // stack. This is the only unbounded section (30 rows a page, infinite scroll)
            // and the only reason the stack is lazy at all.
            NotificationInboxSection.rows(viewModel: notifications, route: $route)
                .padding(.horizontal, AppSpacing.lg)
        }
    }

    // MARK: - Section 2 — Price Rules

    @ViewBuilder
    private func priceRulesSection() -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            SectionHeader(title: "Price Rules")
                .padding(.horizontal, AppSpacing.lg)

            switch priceRules.state {
            case .loading:
                ProgressView()
                    .tint(AppColors.textSecondary)
                    .frame(maxWidth: .infinity)

            case .reconnecting:
                // NOT the sign-in prompt: this user is not signed out, and `requestSignIn`
                // declines to prompt during a restore, so the button would do nothing.
                InlineRetryNotice(
                    message: "Reconnecting your account…",
                    systemImage: "arrow.clockwise",
                    iconColor: AppColors.textMuted
                )
                .padding(.horizontal, AppSpacing.lg)

            case .signedOut:
                InlineRetryNotice(
                    message: "Sign in to see the price alerts you've set. They're saved to "
                        + "your account so they follow you across devices.",
                    systemImage: "person.crop.circle.badge.checkmark",
                    iconColor: AppColors.textMuted,
                    retryTitle: "Sign In",
                    onRetry: { appState.requestSignIn(for: "see your price alerts") }
                )
                .padding(.horizontal, AppSpacing.lg)

            case .error(let message):
                InlineRetryNotice(message: message) {
                    Task { await priceRules.load() }
                }
                .padding(.horizontal, AppSpacing.lg)

            case .loaded where priceRules.isEmpty:
                InlineRetryNotice(
                    message: "No price alerts yet. Open any stock, crypto or ETF and tap the "
                        + "bell to be told when it hits your number.",
                    systemImage: "bell.badge",
                    iconColor: AppColors.textMuted
                )
                .padding(.horizontal, AppSpacing.lg)

            case .loaded:
                // Separate cards, not a bordered GROUP with 1pt separators. Each row is
                // its own `ActivityRow` now, and wrapping self-carded rows in another
                // card is the nested-card trap: in dark the inner fill measures 1.00:1
                // against the outer one and the rows vanish.
                VStack(spacing: AppSpacing.md) {
                    ForEach(priceRules.rules) { rule in
                        PriceAlertRuleRow(
                            alert: rule,
                            // The only place in the app that lists rules across tickers, so
                            // the ticker is the field that tells two rows apart.
                            showsTicker: true,
                            onToggle: { Task { await priceRules.toggleActive(rule) } },
                            onDelete: { Task { await priceRules.delete(rule) } }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)

                Text("\(priceRules.rules.count) of \(priceRules.maxPerUser) alerts")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}
