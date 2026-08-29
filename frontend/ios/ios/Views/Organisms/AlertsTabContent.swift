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
//  The three sections are titled: Upcoming / Activity / Price Alerts.
//
//  ⚠️ The third section was called "Price Rules" on the reasoning that nothing inside a tab
//  called "Alerts" should also be called "Alerts". A TestFlight tester read the two surfaces
//  as unrelated features because of it, and the name was the outlier rather than the rule:
//  the backend router is tagged "Price Alerts", Settings says "My Price Alerts", and this
//  section's own footer and empty state already said "alerts". Do not rename it back.
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
//  That rule is why the badge fix hoisted the view model to a SINGLETON instead of adding a
//  small refresher object beside it: `refreshUnreadCount()` is a fifth path through the same
//  writer, not a second writer. The Alerts segment in `TrackingHeader` now carries the same
//  count as the tab bar, and it is only ever visible from Assets/Whales — because arriving here
//  marks everything read.
//
//  ACTIVITY IS FILTERABLE. A tester: *"Activity is a very long list. Should add tags on the top
//  (same row as Activity), just like in the Report tab."* The chips key off
//  `NotificationEventDTO.category` — a field that had been decoded and read by NOTHING — plus a
//  small mapping for the roll-up cards, so one chip means the same thing across both families.
//  See `ActivityFilter`. Only buckets with rows on screen are offered, and an unrecognised
//  category fails OPEN so a newer backend cannot make rows invisible in an older build.
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

    // The SHARED inbox, not a local view model. It owns `AppState.unreadNotificationCount`, and
    // while it lived and died with THIS view that count could only ever be refreshed by the one
    // screen that marks everything read on sight — so the badge was blank everywhere it mattered.
    // See `NotificationInboxViewModel.shared`.
    @ObservedObject private var notifications = NotificationInboxViewModel.shared

    /// Activity chip selection, PERSISTED across launches. Multi-select, empty = show all.
    ///
    /// ⚠️ This deliberately reverses the first shipped behaviour, so do not "restore" it. The
    /// original comment argued that a surviving filter would be an invisible mode — a user
    /// returning to a short list with no memory of narrowing it. A tester asked for the
    /// opposite, and they are right: the chips are ON SCREEN and highlighted whenever a filter
    /// is applied, so the narrowing is never hidden. There is no invisible mode to protect
    /// against; there was only work being thrown away on every visit.
    ///
    /// Stored as a comma-joined string because `@AppStorage` carries scalars — see
    /// `ActivityFilter.encode/decode`, which own the format, and `ActivityFilter.storageKey`,
    /// which `AppState.discardDataForEndedSession` clears (auth.md §7: a UserDefaults key with
    /// no user id in it, or the next account inherits this one's filter).
    ///
    /// NOT in `SettingsSyncManager`: that manifest round-trips to the server, and a chip
    /// selection has no business crossing devices or costing a request.
    @AppStorage(ActivityFilter.storageKey) private var activityFiltersRaw: String = ""

    /// The stored selection as a Set, writable by the chip bar.
    private var activityFilters: Binding<Set<ActivityFilter>> {
        Binding(
            get: { ActivityFilter.decode(activityFiltersRaw) },
            set: { activityFiltersRaw = ActivityFilter.encode($0) }
        )
    }
    // The SHARED store, not a local view model: the detail-header bell reads the same
    // array, so a rule created behind the bell shows up here with no refetch and no
    // staleness window. See PriceAlertStore.
    @ObservedObject private var priceAlerts = PriceAlertStore.shared

    /// Set when a notification row is tapped. Carries the whole collapsed group, because the
    /// detail screen lists every member and works out the destinations itself.
    @State private var selectedNotification: NotificationInboxSection.CollapsedGroup?

    /// Chosen in the detail sheet while it is still on screen. Parked rather than acted on,
    /// because a `fullScreenCover` CANNOT be presented while its sheet is up — the hand-off
    /// happens in the sheet's `onDismiss` below.
    @State private var pendingDestination: AlertDestination?

    /// Drives the cover. See `AlertDestinationCover` for why this is a cover and not a push.
    @State private var openedDestination: AlertDestination?

    var body: some View {
        ScrollView(showsIndicators: false) {
            // THE one lazy stack. Do not add another inside any section below.
            LazyVStack(spacing: AppSpacing.lg) {
                upcomingSection()
                activitySection()
                priceAlertsSection()
            }
            .padding(.top, AppSpacing.sm)
            .padding(.bottom, AppSpacing.xxxl)
        }
        .refreshable { await refreshAll() }
        // The DETAIL first, then the user picks where to go.
        //
        // A tap used to drop the user straight onto the ticker screen with the alert's own text
        // left behind, so the detail is a sheet now.
        //
        // ⚠️ ITS DESTINATIONS DO NOT PUSH WITHIN IT. They did, and Back returning here rather
        // than to the feed was the point — but pushing inside this sheet meant `TickerDetailView`
        // rendered INSIDE a sheet, and that screen carries seven `.sheet` modifiers of its own.
        // A tester found its search icon doing nothing; search was just the one they tapped.
        // The destination now opens in a cover with its own `NavigationStack`, exactly as Home's
        // Daily Scanners opens a ticker, which costs the breadcrumb: Back lands on the feed, and
        // re-tapping the row reopens this. See `AlertDestinationCover`.
        //
        // The sheet must be GONE before the cover is presented, hence the `onDismiss` hand-off.
        .sheet(item: $selectedNotification, onDismiss: {
            openedDestination = pendingDestination
            pendingDestination = nil
        }) { group in
            NavigationStack {
                NotificationDetailView(group: group) { destination in
                    pendingDestination = destination
                    selectedNotification = nil
                }
            }
            .environment(appState)
        }
        .alertDestinationCover($openedDestination)
        // auth.md §7 — this tab shows three lists of the CALLER'S OWN data on device-global
        // view models. Without this the next account to sign in on the phone inherits the
        // previous user's notifications and price rules.
        .reloadOnIdentityChange { isActive in
            notifications.reset()
            priceAlerts.reset()
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
        let blockedRules = priceAlerts.state == .reconnecting || priceAlerts.state == .signedOut
        return blockedNotifications || blockedRules
    }

    // MARK: - Loading

    private func loadAll() async {
        Analytics.shared.track(.notificationInboxOpened)
        // Await the first page BEFORE marking read — that is what clears the badge, and there
        // is nothing to mark until the page lands.
        await notifications.loadAndWait()
        await notifications.markAllReadOnView()
        await priceAlerts.loadIfStale()
    }

    /// Pull-to-refresh. AWAITED, unlike the fire-and-forget `load()` this replaced — an
    /// un-awaited refresh dismisses the spinner before the data lands, which reads as
    /// "refreshing did nothing".
    private func refreshAll() async {
        await notifications.loadAndWait()
        await notifications.markAllReadOnView()
        await priceAlerts.load()
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
        let upcomingTickers = Set(
            trackingViewModel.filteredAlerts.filter(\.isUpcoming).compactMap(upcomingTicker)
        )
        // Everything this section COULD show, before the chips narrow it. The available
        // chip set and the visible list must both derive from this same array — deriving
        // the chips from the unsuppressed list would offer an Earnings chip whose only row
        // is hidden, i.e. a chip that filters to nothing on its first tap.
        let candidates = notifications.items.filter {
            !duplicatesUpcomingCard($0, upcomingTickers: upcomingTickers)
        }
        let available = availableFilters(rollups: rollups, items: candidates)
        // Intersect on EVERY read rather than trusting the stored set. A pull-to-refresh can
        // empty the bucket a chip was filtering on; without this the list would go blank with
        // no chip left on screen to explain why, and no way to undo it.
        let selection = activityFilters.wrappedValue.intersection(available)
        let visibleRollups = rollups.filter { ActivityFilter.admits(selection, rollup: $0) }
        let visibleItems = candidates.filter {
            ActivityFilter.admits(selection, category: $0.category)
        }

        ActivityFilterBar(
            title: "Activity",
            available: available,
            selection: activityFilters
        )
        .padding(.horizontal, AppSpacing.lg)

        ForEach(visibleRollups) { alert in
            AlertCardView(alert: alert) { trackingViewModel.viewAlertDetail(alert) }
                .padding(.horizontal, AppSpacing.lg)
        }

        notificationRows(
            items: visibleItems,
            hasRollups: !visibleRollups.isEmpty,
            isFiltered: !selection.isEmpty
        )
    }

    /// The ticker an Upcoming card is about, or nil when it carries none.
    ///
    /// Only `.earnings` does in practice — `.market` is the decoder's catch-all for a `type`
    /// string this build does not know, and the backend emits no such row today.
    private func upcomingTicker(_ alert: AppAlert) -> String? {
        if case .earnings(let data) = alert {
            let symbol = data.ticker.uppercased()
            return symbol.isEmpty ? nil : symbol
        }
        return nil
    }

    /// Is this notification row a strictly poorer copy of a card already on screen above it?
    ///
    /// THE DUPLICATE IS REAL, and a tester spotted it: *"Earnings seems redundancy? because we
    /// already have Upcoming"*. Two independent producers read the same FMP calendar —
    /// `tracking_service._get_earnings_alerts` (today .. +14d) builds the Upcoming card, and
    /// `earnings_sender.select_upcoming` (tomorrow only) sends the notification. Since
    /// "tomorrow" is always inside "today +14d", essentially every `earnings_upcoming` row is
    /// a second, WEAKER telling of a card sitting ~40pt above it: the card carries the EPS and
    /// revenue consensus, the row is a one-liner.
    ///
    /// ⚠️ FAILS OPEN. Suppression requires the twin to be genuinely on screen, so if the
    /// tracking feed is empty or failed to load, the row stays and the user still learns their
    /// ticker reports tomorrow. Hiding it unconditionally would delete the information on
    /// exactly the request where it is the only copy left.
    ///
    /// The badge is unaffected: `markAllReadOnView()` runs over the view model's full `items`,
    /// not this filtered view, so a suppressed row is still marked read and still counted.
    private func duplicatesUpcomingCard(
        _ item: NotificationEventDTO,
        upcomingTickers: Set<String>
    ) -> Bool {
        guard item.kind == "earnings_upcoming" else { return false }
        let ticker = (item.route["ticker"] ?? "").uppercased()
        guard !ticker.isEmpty else { return false }
        return upcomingTickers.contains(ticker)
    }

    /// The buckets that have at least one row on screen right now, in a stable order.
    ///
    /// Derived from the data rather than hardcoded to `allCases`, so a chip can never filter to
    /// an empty list on its first tap — an account that has never had an earnings notification
    /// is not offered an Earnings chip.
    private func availableFilters(
        rollups: [AppAlert],
        items: [NotificationEventDTO]
    ) -> [ActivityFilter] {
        var present: Set<ActivityFilter> = []
        for alert in rollups {
            if let bucket = ActivityFilter.bucket(forRollup: alert) { present.insert(bucket) }
        }
        for item in items {
            if let bucket = ActivityFilter.bucket(forCategory: item.category) {
                present.insert(bucket)
            }
        }
        return ActivityFilter.allCases.filter { present.contains($0) }
    }

    /// The notification half of Activity.
    ///
    /// `hasRollups` decides whether an EMPTY notification list is worth saying out loud:
    /// with roll-ups above it, "No notifications yet" under a populated section reads as
    /// a broken sub-list rather than a quiet inbox.
    @ViewBuilder
    private func notificationRows(
        items: [NotificationEventDTO],
        hasRollups: Bool,
        isFiltered: Bool
    ) -> some View {
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
            if items.isEmpty {
                // `.loaded` with nothing to draw can only mean the filter excluded every row —
                // a genuinely empty inbox is `.empty`. Say so and offer the way out, rather
                // than leaving a gap under a row of chips.
                //
                // Suppressed when roll-ups DID match: the section is not empty, only its
                // notification half is, and a "nothing matches" notice under visible cards
                // reads as a broken sub-list.
                if isFiltered && !hasRollups {
                    InlineRetryNotice(
                        message: "Nothing in Activity matches that filter right now.",
                        systemImage: "line.3.horizontal.decrease.circle",
                        iconColor: AppColors.textMuted,
                        retryTitle: "Clear",
                        onRetry: { activityFiltersRaw = "" }
                    )
                    .padding(.horizontal, AppSpacing.lg)
                }
            } else {
                // A bare `ForEach`, spliced DIRECTLY into the LazyVStack above — no wrapper
                // stack. This is the only unbounded section (30 rows a page, infinite scroll)
                // and the only reason the stack is lazy at all.
                NotificationInboxSection.rows(
                    viewModel: notifications,
                    items: items,
                    selection: $selectedNotification
                )
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }

    // MARK: - Section 3 — Price Alerts

    @ViewBuilder
    private func priceAlertsSection() -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            SectionHeader(title: "Price Alerts")
                .padding(.horizontal, AppSpacing.lg)

            switch priceAlerts.state {
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
                    Task { await priceAlerts.load() }
                }
                .padding(.horizontal, AppSpacing.lg)

            case .loaded where priceAlerts.isEmpty:
                InlineRetryNotice(
                    // Names every asset class that actually has a bell — the previous
                    // copy omitted indexes and commodities, which do.
                    message: "No price alerts yet. Open any ticker and tap the bell to be "
                        + "told when it hits your number.",
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
                    ForEach(priceAlerts.alerts) { rule in
                        PriceAlertRuleRow(
                            alert: rule,
                            // The only place in the app that lists rules across tickers, so
                            // the ticker is the field that tells two rows apart.
                            showsTicker: true,
                            onToggle: { Task { await priceAlerts.toggleActive(rule) } },
                            onDelete: { Task { await priceAlerts.delete(rule) } }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)

                // ACTIVE only, matching `price_alert_service._count_for_user`, which filters
                // `is_active = True`. Counting every row showed "20 of 20" while a 21st
                // was still creatable.
                Text("\(priceAlerts.activeCount()) of \(priceAlerts.maxPerUser) alerts")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}
