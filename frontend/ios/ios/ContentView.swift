//
//  ContentView.swift
//  ios
//
//  Created by Hai Phan on 12/30/25.
//

import SwiftUI

struct ContentView: View {
    // Needed to observe a notification tap (`pendingPushNotification`) and present its detail.
    @Environment(AppState.self) private var appState
    @State private var selectedTab: HomeTab = .home
    @State private var researchTickerSymbol: String? = nil
    @State private var researchSubTab: ResearchTab = .research

    /// The ONE general-purpose Cay AI conversation, owned here so it survives tab switches and
    /// the chat resumes wherever it is reopened from.
    ///
    /// It used to live in `LearnContentView`, which made "resume" a Wiser-only property: the
    /// header bar's chat door is in `GlobalHeaderView` now, embedded by four tab headers, and a
    /// per-header view model would have meant four unrelated threads. Contextual chats keep
    /// their own view models on purpose — the reading screens' "Ask the Agent" and the
    /// asset detail bars each seed a grounded conversation that must not clobber this one.
    @StateObject private var chatViewModel = ChatViewModel()

    /// A TAPPED PUSH whose detail screen is up.
    ///
    /// Owned by the SHELL, not a tab: the detail opens over whatever tab is showing (Home on a
    /// cold launch), and Done returns the user to exactly where they were. It used to be split
    /// between this view (pick the tab) and `HomeDashboardView` (present the ticker), a two-owner
    /// arrangement whose handlers had to partition the routes so neither cleared the value
    /// before the other read it. One owner has no such race.
    @State private var pushDetail: PushedNotification?
    /// The destination chosen on that detail, PARKED until the sheet has finished closing — a
    /// cover cannot present while its sheet is still up, so assigning the cover straight from
    /// the row tap races the dismissal and the tap does nothing (`AlertsTabContent`, same idiom).
    @State private var pendingPushDestination: AlertDestination?
    /// …then opened in `AlertDestinationCover`, whose own `NavigationStack` gives the ticker
    /// screen a working search and all seven of its sheets.
    @State private var openedPushDestination: AlertDestination?

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            // Keep all tab views alive to avoid re-creating ViewModels on every tab switch.
            // Only the selected tab is visible; the others are hidden but retained in memory
            // so their @StateObject instances persist and don't re-trigger data loading.
            //
            // New Caydex Home dashboard — backend-connected via the live
            // `HomeRepository` (the ViewModel's default; `MockHomeRepository` is
            // previews-only). All four sections fetch `GET /home/dashboard`.
            // (The legacy `HomeViewWithBinding` that used to sit below has been deleted —
            // see the header of Views/Screens/HomeView.swift for why.)
            HomeDashboardView(selectedTab: $selectedTab)
            .opacity(selectedTab == .home ? 1 : 0)
            .allowsHitTesting(selectedTab == .home)
            .environment(\.isActiveTab, selectedTab == .home)

            UpdatesView(selectedTab: $selectedTab)
                .opacity(selectedTab == .updates ? 1 : 0)
                .allowsHitTesting(selectedTab == .updates)
                .environment(\.isActiveTab, selectedTab == .updates)

            ResearchViewWithBinding(
                selectedTab: $selectedTab,  
                prefilledTicker: researchTickerSymbol,
                initialSubTab: researchSubTab
            )
            .opacity(selectedTab == .research ? 1 : 0)
            .allowsHitTesting(selectedTab == .research)
            .environment(\.isActiveTab, selectedTab == .research)

            TrackingViewWithBinding(selectedTab: $selectedTab)
            .opacity(selectedTab == .tracking ? 1 : 0)
            .allowsHitTesting(selectedTab == .tracking)
            .environment(\.isActiveTab, selectedTab == .tracking)

            WiserViewWithBinding(selectedTab: $selectedTab)
                .opacity(selectedTab == .wiser ? 1 : 0)
                .allowsHitTesting(selectedTab == .wiser)
                .environment(\.isActiveTab, selectedTab == .wiser)
        }
        // The global chat cover. Presented here rather than per-tab so every header's sparkle
        // opens the SAME conversation. `Binding(get:set:)` onto the @Observable AppState is the
        // idiom `iosApp.swift` already uses for `signInPrompt`.
        .aiChatCover(
            isPresented: Binding(
                get: { appState.isAIChatPresented },
                set: { appState.isAIChatPresented = $0 }
            ),
            viewModel: chatViewModel
        )
        // auth.md §7 — clear UNCONDITIONALLY on a real identity change, and before anything that
        // could gate on the active tab. `chatViewModel` is a `@StateObject` on a view that never
        // leaves the hierarchy, so without this the next account to sign in on this device reads
        // the previous user's messages and history titles. `identityGeneration` bumps on a real
        // change and NOT on the first resolution of a process, so a cold launch never wipes the
        // thread. Dismiss first: leaving the cover up would show the new identity an empty chat
        // it never opened.
        .reloadOnIdentityChange { _ in
            appState.isAIChatPresented = false
            chatViewModel.resetForIdentityChange()
        }
        // The chat cover and a tapped push's detail (plus the destination cover it opens) are
        // the presentations the SHELL owns rather than a tab, so no tab root's
        // `.onPresentationReset` can reach them.
        .onPresentationReset {
            appState.isAIChatPresented = false
            // The parked choice FIRST: nil-ing `pushDetail` runs the sheet's `onDismiss`, which
            // would otherwise promote the parked destination into a cover mid-unwind.
            pendingPushDestination = nil
            pushDetail = nil
            openedPushDestination = nil
        }
        // A notification TAPPED outside the app → its DETAIL screen, never the ticker directly.
        //
        // `initial: true` is load-bearing: on a COLD launch from a tap, AppDelegate parks the
        // notification before this view has ever rendered, and a plain `.onChange` only fires on
        // a change AFTER first render — the exact scenario the tap handler exists for. Warm taps
        // would still work, so it would survive manual testing. DO NOT "clean it up".
        //
        // Consume-and-clear, so one tap opens one screen and a dismissed detail can't re-open.
        .onChange(of: appState.pendingPushNotification, initial: true) { _, pushed in
            guard let pushed else { return }
            appState.pendingPushNotification = nil
            presentTappedPush(pushed)
        }
        .sheet(item: $pushDetail, onDismiss: {
            openedPushDestination = pendingPushDestination
            pendingPushDestination = nil
        }) { pushed in
            NavigationStack {
                PushNotificationDetailScreen(pushed: pushed) { destination in
                    pendingPushDestination = destination
                    pushDetail = nil
                }
            }
            .environment(appState)
        }
        .alertDestinationCover($openedPushDestination)
        // "AI Deep Research" on a stock detail screen, from ANY of its ~14 entry points.
        //
        // ONE OWNER PER PARKED INTENT (the push handler above is the same shape): ContentView is
        // this value's only observer and its only clearer, so nothing can clear it before it is
        // read. `initial: true` matches the push handler — a cold launch could park the intent
        // before this view exists.
        //
        // Order matters: seed the ticker BEFORE switching tabs, so the Research tab's own
        // `onChange(of: prefilledTicker)` sees a non-nil value the moment it comes forward.
        .onChange(of: appState.pendingResearchTicker, initial: true) { _, ticker in
            guard let ticker, !ticker.isEmpty else { return }
            researchTickerSymbol = ticker
            researchSubTab = .research
            selectedTab = .research
            appState.pendingResearchTicker = nil
        }
        .onChange(of: selectedTab) { oldValue, newValue in
            // Which tabs actually get used. `HomeTab` is a fixed 5-case enum, so this
            // is a low-cardinality dimension, not free text.
            Analytics.shared.track(.screenView, ["tab": .string(newValue.rawValue)])

            // Clear the research ticker when leaving research tab
            if oldValue == .research && newValue != .research {
                researchTickerSymbol = nil
                researchSubTab = .research
            }
        }
    }

    /// Present a tapped push — its DETAIL, or for a report the REPORT itself — taking down
    /// whatever is on screen first.
    ///
    /// A report skips the detail by request (`AlertDestination.directDestination`, which the
    /// Alerts rows read too): it opens straight into the same destination cover the detail's
    /// "Read the full report" row would have used. Opening it IS reading it, so it is marked read
    /// by the payload's `dedup_key`, as an Alerts row marks itself read on tap.
    ///
    /// A sheet or cover cannot present while another view's is up; SwiftUI QUEUES it instead
    /// ("only presenting a single sheet is supported"), so a push tapped while a ticker was open
    /// from a Home tile did nothing until the user closed that ticker by hand, and then the
    /// detail appeared out of nowhere. Measured on the simulator.
    ///
    /// So: the same teardown AI Deep Research uses (every tab root clears its own presentation
    /// state, this view included), then present once UIKit reports the stack clear. Assigning
    /// straight after `dismissAllPresentations()` would not work — this view's own
    /// `.onPresentationReset` runs on that bump and would nil it again.
    ///
    /// Nothing presented (the cold launch, the common case): present at once, no teardown.
    private func presentTappedPush(_ pushed: PushedNotification) {
        let direct = AlertDestination.directDestination(for: pushed.event)
        if direct != nil {
            Task {
                await NotificationInboxViewModel.shared.markReadFromNotificationAction(
                    dedupKey: pushed.dedupKey
                )
            }
        }
        let present = {
            if let direct {
                openedPushDestination = direct
            } else {
                pushDetail = pushed
            }
        }
        guard ModalPresentationProbe.isAnythingPresented else {
            present()
            return
        }
        appState.dismissAllPresentations()
        Task { @MainActor in
            _ = await ModalPresentationProbe.waitUntilNothingPresented()
            present()
        }
    }
}

// MARK: - ResearchView with Binding Support
struct ResearchViewWithBinding: View {
    @Environment(AppState.self) private var appState
    /// Injected at ContentView.swift's tab ZStack. It was already being provided for this tab
    /// and simply never read here, which is why Home and Updates recovered from a raced load
    /// and Research did not.
    @Environment(\.isActiveTab) private var isActiveTab
    @StateObject private var viewModel: ResearchViewModel
    @Binding var selectedTab: HomeTab
    let prefilledTicker: String?
    let initialSubTab: ResearchTab
    // Carry the full AnalysisReport (not just ticker) so the detail view
    // receives backendId + persona and can short-circuit to the cached
    // ticker_report_data JSONB instead of regenerating.
    @State private var selectedReport: AnalysisReport?
    @State private var selectedTrendingAnalysis: TrendingAnalysis?
    @State private var showProfile = false

    init(selectedTab: Binding<HomeTab>, prefilledTicker: String? = nil, initialSubTab: ResearchTab = .research) {
        self._selectedTab = selectedTab
        self.prefilledTicker = prefilledTicker
        self.initialSubTab = initialSubTab
        self._viewModel = StateObject(wrappedValue: ResearchViewModel(prefilledTicker: prefilledTicker))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Header (pinned outside scroll)
                ResearchHeader(
                    selectedTab: $viewModel.selectedTab,
                    onProfileTapped: handleProfileTapped
                )

                // Tab Content
                if viewModel.selectedTab == .research {
                    researchTabContent
                } else {
                    reportsTabContent
                }

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }

            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .onAppear {
            viewModel.selectedTab = initialSubTab
        }
        // The `init` seed below covers a COLD LAUNCH only, and nothing else did.
        //
        // `prefilledTicker` reaches the ViewModel through
        // `StateObject(wrappedValue: ResearchViewModel(prefilledTicker:))`, and that autoclosure
        // is evaluated exactly ONCE — all five tabs are opacity-mounted in one ZStack and this
        // view is never re-created. So every later handoff ("open Research for NOC") switched the
        // tab and then presented an EMPTY search field, which is indistinguishable from the
        // button having done nothing. This is the half of the bug that survived even on the one
        // entry point that was wired up.
        .onChange(of: prefilledTicker) { _, ticker in
            guard let ticker, !ticker.isEmpty else { return }
            // Through the ViewModel, NOT `viewModel.searchText = ticker`. The chip renders
            // `selectedTarget` and falls back to `searchText` only when it is nil, while
            // `generateAnalysis()` reads `searchText` — so writing one of them left the screen
            // showing a stale target while Generate charged 20 credits for this one.
            viewModel.applyPrefilledTicker(ticker)
        }
        // Live-poll the Reports list while anything is generating.
        //
        // This was wired ONLY in `Views/Screens/ResearchView.swift`, the preview-only
        // copy of this screen that was never presented (that file has since been deleted —
        // this view is the only Research screen) — so in the shipping app nothing
        // ever armed it. The generation stream refreshes the list at 25% boundaries,
        // which hid the gap while a stream was alive, but a report started in a
        // previous app run, or one whose stream ended on a network error, left its
        // card frozen at whatever percentage the last load returned until the user
        // pulled to refresh. `startReportsPolling` self-terminates once nothing is
        // `.processing`, so arming it on tab entry costs one Supabase query per 5s
        // only while work is genuinely in flight.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            viewModel.startReportsPolling()
        }
        .onChange(of: viewModel.selectedTab) { _, tab in
            if tab == .reports { viewModel.startReportsPolling() }
        }
        .onDisappear { viewModel.stopReportsPolling() }
        // Heals a load that raced session restore. The ViewModel's only unconditional load is
        // in `init`, and all five tabs mount eagerly in one ZStack — so it runs at launch, while
        // auth is still `.restoring`, and `requiresSignInForReports` latched `true` with nothing
        // to clear it. `loadIfStale` makes re-entry cheap; a signed-out/reconnecting pass is
        // never marked fresh, so arriving here after signing in always refetches.
        // Same `.task(id: isActiveTab)` idiom as HomeDashboardView and UpdatesView.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            await viewModel.loadIfStale()
        }
        // Re-seed the analyst from Settings → "Default Analyst" on every entry to this tab.
        //
        // The notification observers in the ViewModel cover a change made while the app is
        // running; this covers the rest — a value hydrated from the server, and the promise the
        // setting's own subtitle makes ("Pre-selected for new research"). It also clears the
        // manual-override flag, which is what makes a one-off pick last for the visit it was
        // made in rather than for the whole app process.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            viewModel.researchTabDidActivate()
        }
        // The direct case, and the one that has no other cure: signing in or out from THIS
        // tab. `SignInRequiredSheet` dismisses itself on `.authenticated`, and
        // `onAuthenticated()`'s fan-out hydrates credits, settings and the Learn stores but
        // nothing research-related — so without this the tab behind the sheet keeps rendering
        // "Sign in to see your analyses" to a user who just signed in.
        .reloadOnIdentityChange { isActive in await viewModel.handleIdentityChange(isActiveTab: isActive) }
        // This tab is the DESTINATION of the research handoff, so it must be clear when the user
        // arrives — landing on Research behind a report cover is the same bug, one screen along.
        .onPresentationReset {
            selectedReport = nil
            selectedTrendingAnalysis = nil
            showProfile = false
            viewModel.showCreditsSheet = false
            viewModel.showPersonasSheet = false
            viewModel.showTargetSearchSheet = false
        }
        .fullScreenCover(item: $selectedReport) { report in
            NavigationStack {
                TickerReportView(report: report)
            }
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
        }
        .fullScreenCover(item: $selectedTrendingAnalysis) { analysis in
            NavigationStack {
                TrendingAnalysisDetailView(analysis: analysis) { ticker in
                    // Same pairing rule as the deep-research handoff: writing `searchText`
                    // alone leaves a stale `selectedTarget` rendering in the chip while
                    // Generate charges this ticker.
                    viewModel.applyPrefilledTicker(ticker)
                }
            }
        }
        // "Add More Credits" now sells credits instead of opening the subscription paywall.
        // A user who ran out mid-task wants to finish it, not commit to a monthly plan;
        // BuyCreditsView carries a "See plans" button so the paywall stays one tap away.
        .sheet(isPresented: $viewModel.showCreditsSheet) {
            BuyCreditsView()
                .environment(\.appState, appState)
        }
        .sheet(isPresented: $viewModel.showPersonasSheet) {
            PersonasSheet(
                personas: viewModel.personas,
                selectedPersona: personaBinding
            )
        }
        .sheet(isPresented: $viewModel.showTargetSearchSheet) {
            TargetSearchSheet { result in
                viewModel.selectTarget(result)
            }
        }
        .alert("Delete \(viewModel.selectedReportCount) report\(viewModel.selectedReportCount == 1 ? "" : "s")?",
               isPresented: $viewModel.showDeleteConfirm) {
            Button("Delete", role: .destructive) {
                Task { await viewModel.deleteSelectedReports() }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This can't be undone.")
        }
        .alert("Error", isPresented: Binding(
            get: { viewModel.error != nil },
            set: { if !$0 { viewModel.error = nil } }
        )) {
            Button("OK") { viewModel.error = nil }
        } message: {
            Text(viewModel.error ?? "")
        }
    }

    // MARK: - Research Tab Content
    /// The balance to display, preferring the ViewModel's own copy and falling back to
    /// `AppState`.
    ///
    /// The ViewModel deliberately keeps its OWN `creditBalance` (it has no AppState
    /// reference) so it can adopt a purchase the instant `.caydexEntitlementChanged`
    /// fires. That copy is authoritative when present — but it is nil until
    /// `loadCredits()` returns, and it STAYS nil if that one request fails. The card and
    /// the Generate badge then vanish entirely, while the Wiser tab — which reads
    /// `appState.user.credits`, the documented single source of truth — shows the real
    /// number on the same screen-swipe. Two surfaces disagreeing about the user's
    /// balance reads as a bug in the credits system, not as a failed fetch.
    ///
    /// Falling back rather than replacing keeps the purchase-adoption path intact.
    private var effectiveCreditBalance: CreditBalance? {
        // `.map { … }`, not `.map(CreditBalance.from)`: an unapplied method reference
        // becomes a *nonisolated* function type, and `from` reads the @MainActor
        // `purchasedCredits`. A closure literal here inherits this view's isolation.
        viewModel.creditBalance ?? appState.user.credits.map { CreditBalance.from($0) }
    }

    private var researchTabContent: some View {
        ScrollView(showsIndicators: false) {
            // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
            // The direct children here are a fixed, hand-written list, so laziness bought nothing,
            // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
            // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
            //
            // `if let balance = effectiveCreditBalance` inserts the credits card once it resolves.
            VStack(spacing: AppSpacing.xxl) {
                // Target Selection Section
                TargetSelectionSection(
                    selectedTarget: viewModel.selectedTarget,
                    fallbackTicker: viewModel.searchText,
                    onTapSearch: { viewModel.openTargetSearch() },
                    onClearTarget: { viewModel.clearTarget() }
                )
                .padding(.top, AppSpacing.md)

                // Persona Selection Section
                PersonaSelectionSection(
                    personas: viewModel.personas,
                    selectedPersona: personaBinding,
                    onViewAllTapped: handleViewAllPersonas
                )

                // Generate Analysis Section
                GenerateAnalysisSection(
                    cost: viewModel.analysisCost,
                    remainingCredits: effectiveCreditBalance?.credits,
                    isEnabled: viewModel.canStartNewGeneration,
                    // The cap is its own state, not "loading": the button is disabled
                    // under a notice that says why (E2). A cap reached from ANOTHER
                    // device is not counted here and surfaces as the server's 409 alert.
                    isAtCap: viewModel.isAtConcurrencyCap,
                    activeCount: viewModel.activeGenerationCount,
                    onGenerate: handleGenerateAnalysis,
                    onViewProgress: { viewModel.selectedTab = .reports }
                )

                // What You'll Get Section
                WhatYouGetSection(features: viewModel.features)

                // Credits Balance Card — only once a real balance is known.
                if let balance = effectiveCreditBalance {
                    CreditsBalanceCard(
                        balance: balance,
                        onAddCredits: handleAddCredits
                    )
                    .padding(.horizontal, AppSpacing.lg)
                }

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
    }

    // MARK: - Reports Tab Content
    private var reportsTabContent: some View {
        ScrollView(showsIndicators: false) {
            // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
            // The direct children here are a fixed, hand-written list, so laziness bought nothing,
            // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
            // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
            //
            // `isSelectingReports` animates a 72pt spacer in and out.
            VStack(spacing: AppSpacing.xxl) {
                // Reports List Section
                ReportsListSection(
                    sections: viewModel.groupedReports,
                    sortOption: $viewModel.reportSortOption,
                    searchText: $viewModel.reportSearchText,
                    isSearchActive: $viewModel.isReportSearchActive,
                    isSelecting: $viewModel.isSelectingReports,
                    selectedIds: viewModel.selectedReportIds,
                    personaTags: viewModel.personas,
                    selectedPersonaKeys: viewModel.selectedPersonaKeys,
                    onReportTapped: handleReportTapped,
                    onRetryTapped: handleRetryTapped,
                    onToggleSelect: handleToggleSelect,
                    onToggleSelectingMode: handleToggleSelectingMode,
                    onTogglePersonaTag: { viewModel.togglePersonaTag($0) },
                    // First-run CTA: send them to the tab that can actually make one.
                    onGenerateFirst: { viewModel.selectedTab = .research },
                    // Reports are account-scoped now, so "you have none" and "you're signed
                    // out" are different situations and get different copy + CTA.
                    requiresSignIn: viewModel.requiresSignInForReports,
                    onSignIn: { appState.requestSignIn(for: "see your analyses") },
                    isReconnecting: viewModel.isReconnectingReports
                )
                .padding(.top, AppSpacing.sm)

                // Community Insights — deferred. Backend feature pending; the
                // mock data + stub handlers are kept in the codebase for the
                // future read/write feed.

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)

                // Extra inset while selecting so the last card scrolls clear
                // of the floating selection bar.
                if viewModel.isSelectingReports {
                    Spacer().frame(height: 72)
                }
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
        // Floating selection bar — pinned to the bottom of the reports list,
        // which sits just above the CustomTabBar.
        .overlay(alignment: .bottom) {
            if viewModel.isSelectingReports {
                ReportsSelectionBar(
                    selectedCount: viewModel.selectedReportCount,
                    isDeleting: viewModel.isDeletingReports,
                    onDelete: { viewModel.showDeleteConfirm = true }
                )
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.22), value: viewModel.isSelectingReports)
    }

    // MARK: - Action Handlers

    /// The analyst binding handed to both picker surfaces.
    ///
    /// NOT `$viewModel.selectedPersona`. `PersonaSelectionSection` and `PersonasSheet` write
    /// through a plain `@Binding`, which would set the property directly and skip
    /// `selectPersona(_:)` — the only place that records the pick as deliberate. A default
    /// re-applied a moment later would then silently overwrite the user's tap. Routing the
    /// setter through the ViewModel keeps one writer; `selectedPersona` is `private(set)` so a
    /// future direct binding fails to compile rather than reintroducing this quietly.
    private var personaBinding: Binding<AnalysisPersona> {
        Binding(
            get: { viewModel.selectedPersona },
            set: { viewModel.selectPersona($0) }
        )
    }

    private func handleProfileTapped() {
        showProfile = true
    }

    private func handleViewAllPersonas() {
        viewModel.viewAllPersonas()
    }

    private func handleGenerateAnalysis() {
        viewModel.generateAnalysis()
    }

    private func handleAddCredits() {
        viewModel.addMoreCredits()
    }

    private func handleTrendingAnalysisTapped(_ analysis: TrendingAnalysis) {
        selectedTrendingAnalysis = analysis
    }

    private func handleReportTapped(_ report: AnalysisReport) {
        guard report.status == .ready else { return }
        // Pass the full report so the detail view receives backendId
        // (cached JSONB lookup) and persona (correct agent selection).
        selectedReport = report
    }

    private func handleRetryTapped(_ report: AnalysisReport) {
        viewModel.retryReport(report)
    }

    private func handleToggleSelect(_ report: AnalysisReport) {
        viewModel.toggleReportSelection(report)
    }

    private func handleToggleSelectingMode() {
        if viewModel.isSelectingReports {
            viewModel.exitSelectionMode()
        } else {
            viewModel.isSelectingReports = true
        }
    }
}

// MARK: - TrackingView with Binding Support
struct TrackingViewWithBinding: View {
    // The tab bar renders on every one of these wrappers, so the unread badge
    // must be readable from all of them — otherwise it appears and vanishes as
    // the user switches tabs.
    @Environment(AppState.self) private var appState
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                TrackingContentViewWithBinding(selectedTab: $selectedTab)

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }
        }
    }
}

// MARK: - WiserView with Binding Support (Learn)
struct WiserViewWithBinding: View {
    // The tab bar renders on every one of these wrappers, so the unread badge
    // must be readable from all of them — otherwise it appears and vanishes as
    // the user switches tabs.
    @Environment(AppState.self) private var appState
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // The Wiser "Chat" tab now opens AIChatScreen as a full-screen cover that owns its
                // own audio compact/island via .globalAudioOverlay, so the Wiser screen no longer
                // needs an active-tab signal here.
                LearnContentView()

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }
        }
    }
}

// MARK: - Placeholder View for Other Tabs
struct TabPlaceholderView: View {
    // The tab bar renders on every one of these wrappers, so the unread badge
    // must be readable from all of them — otherwise it appears and vanishes as
    // the user switches tabs.
    @Environment(AppState.self) private var appState
    let title: String
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack {
                Spacer()

                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "hammer.fill")
                        .font(AppTypography.iconHero)
                        .foregroundColor(AppColors.textMuted)

                    Text(title)
                        .font(AppTypography.title)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Coming Soon")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }
        }
    }
}

#Preview {
    ContentView()
        .environment(AppState())
        .environmentObject(AudioManager.shared)
}
