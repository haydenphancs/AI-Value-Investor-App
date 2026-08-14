//
//  iosApp.swift
//  ios
//
//  Created by Hai Phan on 12/30/25.
//
//  App Entry Point
//
//  This is where we:
//  1. Initialize global AppState
//  2. Configure services (API client, auth)
//  3. Inject state via Environment
//

import SwiftUI

@main
struct iosApp: App {

    // MARK: - Global State

    /// Single source of truth for app-wide state
    /// Injected into all views via .environment()
    @State private var appState = AppState()

    /// Tracks if services have been configured
    @State private var isConfigured = false

    /// APNs remote-notification callbacks (device token registration).
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    /// The persisted appearance choice (System / Dark / Light), read reactively.
    /// Drives a single root-level `.preferredColorScheme` so the correct scheme is
    /// applied from frame 0 (no cold-launch flash) and updates instantly when the
    /// user changes it. `AppearanceManager` still applies the window-level override
    /// (the robust path across sheets/covers); both read this same key, so they
    /// always agree. Defaults to Dark — the shipped look — until the user opts in.
    @AppStorage(AppearanceManager.storageKey) private var appearanceModeRaw = AppearanceMode.dark.rawValue

    /// Resolved SwiftUI color scheme for the root modifier (`nil` = follow System).
    /// Goes through `AppearanceManager.parse` rather than re-deriving the mode here:
    /// the two writers of the window style are only guaranteed to agree if they share
    /// one parse, and this used to be a second, independent copy of it.
    private var preferredColorScheme: ColorScheme? {
        AppearanceManager.parse(appearanceModeRaw).colorScheme
    }

    // MARK: - Initialization

    init() {
        // Start error monitoring first so even startup crashes are captured.
        // No-op until the Sentry package is added and a DSN is set (see
        // Core/Monitoring/MonitoringConfig.swift).
        startErrorMonitoring()

        // Configure appearance
        configureAppearance()

        // Give AsyncImage a real cache. BookLibraryView is a LazyVStack, so its cards
        // recycle and AsyncImage re-requests on every identity change — without this the
        // ten covers flash gradient->image on each scroll pass. The default shared cache
        // is far too small for image payloads. This is app-wide on purpose: it also helps
        // news thumbnails, whale avatars, company logos and the header.
        configureImageCache()

        #if DEBUG
        // Runs here, before any window exists, so the nav-bar flatten check reports
        // what `configureAppearance()` actually captured rather than what a later
        // trait resolution would mask.
        AppearanceProbe.selfTest()
        AppearanceProbe.dump("init")
        #endif

        // Observe StoreKit transactions from LAUNCH, not from when the paywall opens.
        // Apple delivers renewals, Ask-to-Buy approvals, and purchases that completed while
        // the app was closed through `Transaction.updates`. Starting the listener later
        // means those are never seen, and a renewal silently fails to apply.
        //
        // Also the redelivery path: a transaction whose backend verification failed is left
        // unfinished, and Apple re-offers it here on the next launch.
        Task { @MainActor in
            StoreKitService.shared.startObservingTransactions()
        }

        // Product analytics. `app_open` per cold launch is the denominator for every
        // activation and retention number — without it none of the others can be
        // read as a rate.
        Analytics.shared.track(.appOpen)
    }

    // MARK: - Body

    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .environment(\.appState, appState)
                .preferredColorScheme(preferredColorScheme)
                .onChange(of: scenePhase) { _, phase in
                    // Buffered events would otherwise be lost when iOS suspends us.
                    if phase == .background { Analytics.shared.flushNow() }
                }
                .task {
                    guard !isConfigured else { return }
                    isConfigured = true

                    // Resolve backend URL (local vs Railway) before APIClient initializes
                    await ServerEnvironmentManager.shared.resolve()

                    // Get services (APIClient.shared is safe to access in async context)
                    let apiClient = APIClient.shared
                    let authService = AuthService(apiClient: apiClient)

                    // Settings sync + push need the AppState ref before auth restore.
                    SettingsSyncManager.shared.configure(appState: appState)
                    PushNotificationManager.shared.configure(appState: appState)
                    // Same pattern, for the singleton services that need to ask "am I signed
                    // in?" and to surface a failed user action (WhaleService, PortfolioStore).
                    AppActions.shared.configure(appState: appState)

                    // Apply the persisted appearance choice (default .dark).
                    //
                    // The probe brackets this call on purpose. `.task` runs AFTER the
                    // first frame, so `task-pre` is SwiftUI's opinion (it has already
                    // applied the root `.preferredColorScheme`) and `task-post` is
                    // AppearanceManager's. Two writers touch the same property; if
                    // they ever disagree, this pair is what shows it.
                    #if DEBUG
                    AppearanceProbe.dump("task-pre")
                    #endif

                    AppearanceManager.applyStored()

                    // Raise the lock window if we launched locked. `AppLockManager.init` cannot
                    // do it — no scene exists yet at singleton-init time — and this is the first
                    // point where one is guaranteed. Idempotent, so the `didBecomeActive` call
                    // below can repeat it without a second window.
                    AppLockManager.shared.syncLockWindow()

                    #if DEBUG
                    AppearanceProbe.dump("task-post")
                    Task { await AppearanceProbe.runTransitionSelfTestIfRequested() }
                    // Measures whether `Font.system(size:)` — the construction all 40
                    // AppTypography tokens use — responds to Dynamic Type at all.
                    TypographyProbe.dumpDynamicTypeScaling()
                    #endif

                    #if DEBUG
                    // Assert every palette token clears its WCAG floor in BOTH
                    // appearances. Compiled out of release; ~1ms in debug.
                    ThemeContrastAudit.run()
                    #endif

                    // Enable 401 → refresh → retry BEFORE auth restore runs, so the
                    // interceptor is armed when restoreAuthState makes its first call.
                    // On an expired token the APIClient asks AuthService to refresh
                    // (rewriting the Keychain + the client's token) and retries once.
                    await apiClient.setTokenRefresher {
                        // Never fall back to the old (expired) token — that would launder a
                        // transient outage into a doomed retry.
                        //
                        // But "failed" is not one thing. Returning a bare nil for ANY error
                        // told APIClient the credential was DEAD, so a 429 from the shared
                        // per-IP refresh limiter (a campus or carrier NAT) signed the user out
                        // and cleared their Keychain with a valid refresh token in hand. Only a
                        // genuine auth failure may end the session; everything else keeps it.
                        do {
                            try await authService.refreshToken()
                            guard let token = await authService.getStoredToken() else {
                                return .credentialRejected
                            }
                            return .refreshed(token)
                        } catch {
                            return AppError.from(error).isAuthError
                                ? .credentialRejected
                                : .transientFailure
                        }
                    }

                    // Let the network layer tell AppState when it could not authenticate, so a
                    // dead credential is cleared once, centrally — and so a request that went
                    // out tokenless while a stored token exists kicks off a session restore
                    // instead of just failing.
                    await apiClient.setAuthFailureHandler { [weak appState] failure in
                        await appState?.handleAuthFailure(failure)
                    }

                    // Configure AppState with services. Token restoration runs inside
                    // restoreSession() (kicked off here) and relies on the interceptor.
                    appState.configure(
                        apiClient: apiClient,
                        authService: authService
                    )
                }
                // Authoritative unread count, broadcast from wherever it was last
                // observed: an inbox load, a mark-read, or a badge arriving on a push.
                // Routed through NotificationCenter rather than written directly because
                // the inbox ViewModel is an ObservableObject with no AppState reference,
                // and reaching for a global inside it would break the "dependencies
                // arrive via init" rule.
                .onReceive(NotificationCenter.default.publisher(
                    for: .caydexNotificationUnreadChanged
                )) { note in
                    guard let count = note.userInfo?["count"] as? Int, count >= 0 else { return }
                    appState.unreadNotificationCount = count
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
                    #if DEBUG
                    AppearanceProbe.dump("foreground")
                    #endif

                    // Windows created while we were backgrounded never received the
                    // override: `apply()` only touches windows that exist at call
                    // time. A scene reconnecting after suspension, a UIKit-created
                    // alert/overlay window, or a second scene would render in the OS
                    // style while the rest of the app honours the user's choice.
                    //
                    // This is NOT a fix for a live OS appearance flip — under
                    // `.unspecified` that propagates on its own through the trait
                    // system. It is cheap insurance: an O(scenes × windows) loop.
                    AppearanceManager.applyStored()

                    // Same gap, for the lock: a scene that reconnected while we were suspended
                    // has no lock window, and `didEnterBackground` already set `isLocked`. This
                    // is what puts the cover back up before the user sees the content.
                    AppLockManager.shared.syncLockWindow()

                    // Re-probe localhost vs Railway each time app comes to foreground.
                    // Handles: started/stopped localhost while app was backgrounded.
                    Task { await ServerEnvironmentManager.shared.resolve() }
                    // Second healing trigger. Covers the common real-world case the network
                    // monitor misses: the app was suspended (so no path callback fired) and the
                    // user returns with working connectivity. No-ops unless a stored credential
                    // is actually going unused.
                    Task { await appState.restoreSessionIfNeeded(trigger: "foreground") }
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
                    // Re-lock the app when it backgrounds (if App Lock is enabled).
                    AppLockManager.shared.lockIfEnabled()
                }
        }
    }

    // MARK: - Appearance Configuration

    /// Raise `URLCache.shared` so `AsyncImage` actually caches.
    ///
    /// Every remote image in the app goes through the shared cache, whose default is
    /// sized for API JSON, not for artwork. The visible symptom was the book library:
    /// a `LazyVStack` recycles its cards, `AsyncImage` re-requests on identity change,
    /// and each scroll pass re-flashed gradient -> image.
    private func configureImageCache() {
        let memory = 32 * 1024 * 1024      // 32 MB
        let disk = 128 * 1024 * 1024       // 128 MB
        URLCache.shared = URLCache(memoryCapacity: memory, diskCapacity: disk)
    }

    private func configureAppearance() {
        // Navigation bar appearance.
        // Bridged straight from the token: `AppColors.background` is a dynamic
        // UIColor underneath, so it keeps resolving per-trait through UIKit and
        // follows the window's appearance override (AppearanceManager).
        //
        // This used to re-declare F4F5F8/171B26 by hand here, which meant the
        // nav bar silently drifted from the page behind it whenever the token
        // changed. Read the token; never copy its hexes.
        let navAppearance = UINavigationBarAppearance()
        navAppearance.configureWithOpaqueBackground()
        navAppearance.backgroundColor = UIColor(AppColors.background)

        // Titles track the palette rather than UIColor.label, so nav copy and
        // screen copy are the same ink.
        let titleColor = UIColor(AppColors.textPrimary)
        navAppearance.titleTextAttributes = [.foregroundColor: titleColor]
        navAppearance.largeTitleTextAttributes = [.foregroundColor: titleColor]

        UINavigationBar.appearance().standardAppearance = navAppearance
        UINavigationBar.appearance().compactAppearance = navAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navAppearance

        // Tab bar. `UINavigationBarAppearance` was the ONLY UIKit proxy configured, and
        // the tab bar is the second-largest piece of persistent chrome in the app — it
        // took whatever a SwiftUI view happened to supply, with `UITabBarAppearance`
        // untouched. On iOS 15+ an unconfigured `scrollEdgeAppearance` is TRANSPARENT,
        // so content scrolling under the bar showed through it.
        //
        // Same discipline as above: read the tokens, never re-declare their hexes. They
        // are dynamic `UIColor`s underneath, so they keep resolving per-trait through
        // UIKit and follow the window override.
        let tabAppearance = UITabBarAppearance()
        tabAppearance.configureWithOpaqueBackground()
        tabAppearance.backgroundColor = UIColor(AppColors.tabBarBackground)

        for item in [tabAppearance.stackedLayoutAppearance,
                     tabAppearance.inlineLayoutAppearance,
                     tabAppearance.compactInlineLayoutAppearance] {
            item.normal.iconColor = UIColor(AppColors.textMuted)
            item.normal.titleTextAttributes = [.foregroundColor: UIColor(AppColors.textMuted)]
            item.selected.iconColor = UIColor(AppColors.primaryBlue)
            item.selected.titleTextAttributes = [.foregroundColor: UIColor(AppColors.primaryBlue)]
        }

        UITabBar.appearance().standardAppearance = tabAppearance
        UITabBar.appearance().scrollEdgeAppearance = tabAppearance
    }
}

// MARK: - Root View

/// Root view that handles auth state and navigation
struct RootView: View {
    @Environment(AppState.self) private var appState
    // No `AppLockManager` observation here any more: the lock is a `UIWindow` owned by the
    // manager, not a view in this tree. Observing it would re-render the root on every lock
    // transition for no reason. See the note where the overlay used to be.

    /// Read so the tree re-evaluates when the user's text size changes. NOT debug-only,
    /// unlike `colorScheme` below.
    ///
    /// `AppTypography`'s tokens are computed `static var`s that resolve through
    /// `UIFontMetrics` against the CURRENT content size category — so they produce the
    /// right size whenever a body runs, but nothing would make a body run. A view that
    /// only calls `.font(AppTypography.body)` declares no dependency on the type size, so
    /// SwiftUI has no reason to re-render it and the app would keep the sizes it launched
    /// with until something unrelated invalidated the view.
    ///
    /// Reading the value HERE puts that dependency at the root, so the whole tree
    /// re-evaluates once on a change. Deliberately not `.id(dynamicTypeSize)`, which would
    /// also work but would tear down and rebuild every view — dropping navigation state
    /// and scroll position because the user changed their text size.
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    #if DEBUG
    /// Read only to drive the DEBUG appearance probe below. Nothing observes trait
    /// changes otherwise (no `traitCollectionDidChange`, no `registerForTraitChanges`),
    /// so this is the only thing that can tell us whether a LIVE OS appearance flip
    /// reaches the app at all — the difference between an app bug and a harness that
    /// flipped the wrong simulator.
    ///
    /// Compiled out of release along with its `.onChange`: reading it here would
    /// otherwise add a colour-scheme dependency to the ROOT view, invalidating its
    /// body on every appearance flip for no shipping benefit.
    @Environment(\.colorScheme) private var colorScheme
    #endif
    /// One-time first-run disclaimer acknowledgement. See
    /// DisclaimerAcknowledgementView for why this exists.
    @AppStorage("has_acknowledged_disclaimers") private var hasAcknowledgedDisclaimers = false
    @AppStorage("has_completed_onboarding") private var hasCompletedOnboarding = false

    /// Destinations for the global error toast's action button.
    @State private var showPaywallFromError = false
    @State private var showSignInFromError = false

    /// Route the toast's action button to somewhere useful, then clear the error so the
    /// toast doesn't sit on top of the sheet it just opened.
    private func handleErrorAction(_ action: ErrorAction) {
        switch action {
        case .upgrade:
            appState.clearError()
            showPaywallFromError = true
        case .signIn:
            appState.clearError()
            showSignInFromError = true
        case .retry, .waitAndRetry, .waitForConnection, .goBack, .fixInput, .contactSupport:
            // No global destination: retry/back are owned by whichever screen raised the
            // error, and the rest are acknowledgements. Dismiss is the honest behaviour.
            appState.clearError()
        }
    }

    var body: some View {
        Group {
            // ONE branch renders RootContainerView, for all three signed-in/guest/restoring
            // states. This is a structural-identity requirement, not a style preference.
            //
            // SwiftUI gives each arm of a `switch` in a ViewBuilder its OWN identity
            // (`_ConditionalContent`). `.authenticated` used to be a separate arm from
            // `.unauthenticated, .restoring` even though both returned `RootContainerView()`,
            // so every auth transition swapped branches and SwiftUI tore the entire tree down
            // and rebuilt it. Signing in therefore reset the selected tab, every
            // NavigationStack's path, and every tab ViewModel's @StateObject — including the
            // ticker screen the user was reading when they were asked to sign in. It reads as
            // "the app threw me out and lost my place", and it fires on the single most
            // important conversion moment in the product.
            //
            // Same family as the `.loading`-destroys-the-sign-in-screen bug: the auth state
            // machine was right, the VIEW IDENTITY was wrong.
            //
            // Keep `.unknown`/`.loading` separate — that one SHOULD be a different view.
            if appState.auth.status == .unknown || appState.auth.status == .loading {
                // Launch / restore only. Never set from a rendered screen: doing so swaps the
                // root out mid-request and takes the presenting sheet with it.
                SplashView()
            } else {
                // Guest-first BY DESIGN: the app is fully usable signed-out. Requests without
                // a Bearer token fall back to the backend GUEST_USER_ID; sign-in is offered
                // from the Account screen and unlocks real per-user profile / credits / tier.
                //
                // `.restoring` renders the SAME container, deliberately. We hold a credential
                // we could not validate — on the wire that is a guest, so showing guest
                // content is honest. Sending it to SplashView instead would trade a usable
                // app for a spinner on exactly the flaky networks where restore takes longest.
                RootContainerView()
            }
        }
        #if DEBUG
        .onChange(of: colorScheme) { _, scheme in
            AppearanceProbe.dump("colorScheme→\(scheme)")
        }
        #endif
        .overlay {
            // Global error toast
            if let error = appState.currentError {
                ErrorToastView(
                    error: error,
                    onDismiss: { appState.clearError() },
                    onAction: { handleErrorAction($0) }
                )
            }
        }
        // The toast's action button used to call onDismiss for EVERY action, so the
        // "Upgrade" button on an INSUFFICIENT_CREDITS error (402, action: "upgrade")
        // just closed the toast — the one moment a user is most ready to pay. Same for
        // "Sign In" on a 401. Both now actually go somewhere.
        // A 402 INSUFFICIENT_CREDITS lands here. It goes to Buy Credits rather than the
        // subscription paywall: the user was mid-action and wants to finish it, and a one-tap
        // top-up unblocks them in seconds where a $14.99/mo commitment is where they bounce.
        // BuyCreditsView carries a "See plans" button, so the subscription stays discoverable
        // without being forced.
        .sheet(isPresented: $showPaywallFromError) {
            BuyCreditsView()
                .environment(\.appState, appState)
        }
        .sheet(isPresented: $showSignInFromError) {
            SignInView()
                .environment(appState)
        }
        // The shared "this needs an account" prompt. Presented once, here, so any call site in
        // the app can raise it via `appState.requestSignIn(for:)` without owning presentation
        // state or a route to the navigation tree.
        .sheet(item: Binding(
            get: { appState.signInPrompt },
            set: { appState.signInPrompt = $0 }
        )) { prompt in
            SignInRequiredSheet(feature: prompt.feature)
                .environment(appState)
        }
        .overlay {
            // Global toast messages
            if let toast = appState.toastMessage {
                ToastView(message: toast)
            }
        }
        .overlay {
            // First-run disclaimer acknowledgement. Above normal content but BELOW the
            // App Lock cover — a locked app must not leak content behind this sheet.
            // Not shown while auth state is still resolving (splash), so it doesn't
            // flash over the launch screen.
            if !hasAcknowledgedDisclaimers, appState.auth.status != .unknown,
               appState.auth.status != .loading {
                DisclaimerAcknowledgementView()
                    .transition(.opacity)
                    .zIndex(900)
            }
        }
        .overlay {
            // First-run onboarding, STRICTLY AFTER the disclaimer — legal first, then
            // product. Same z-band (below the App Lock cover) and the same guard
            // against showing over the splash while auth resolves.
            //
            // Its only job is capturing a few tickers: a populated watchlist is what
            // makes Updates, the personalized Home strip, and push relevant at all.
            // Skippable on every page.
            if hasAcknowledgedDisclaimers, !hasCompletedOnboarding,
               appState.auth.status != .unknown, appState.auth.status != .loading {
                OnboardingView()
                    .transition(.opacity)
                    .zIndex(890)
            }
        }
        // ⚠️ NO App Lock overlay here, deliberately.
        //
        // It used to be `.overlay { if appLock.isLocked { AppLockView().zIndex(1000) } }`, and
        // that could not work: a `.fullScreenCover` / `.sheet` is a separate presented
        // UIViewController drawn ABOVE this whole hierarchy, and `zIndex` only orders siblings
        // WITHIN a hierarchy. So the lock rendered behind every modal in the app — background
        // the app with Account or Cay AI chat open, return, and the content sat there readable.
        // The privacy control failed open in the ordinary case.
        //
        // `AppLockManager` now owns a `UIWindow` at `.alert + 1`. See the header of that file.
    }
}

// MARK: - Splash View

struct SplashView: View {
    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 20) {
                CaydexLogoMark(size: 160)

                ProgressView()
                    .tint(AppColors.primaryBlue)
            }
        }
    }
}

// MARK: - Error Toast View

struct ErrorToastView: View {
    let error: AppError
    let onDismiss: () -> Void
    /// Invoked by the action button. Separate from `onDismiss` so an actionable error
    /// (Upgrade, Sign In) can route somewhere instead of silently closing.
    var onAction: ((ErrorAction) -> Void)? = nil

    var body: some View {
        VStack {
            Spacer()

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(AppColors.bearish)

                    Text(error.title)
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Button(action: onDismiss) {
                        Image(systemName: "xmark")
                            .foregroundColor(AppColors.textSecondary)
                    }
                }

                Text(error.message)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)

                if error.suggestedAction != .fixInput {
                    Button {
                        if let onAction {
                            onAction(error.suggestedAction)
                        } else {
                            onDismiss()
                        }
                    } label: {
                        Text(error.suggestedAction.buttonTitle)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
            }
            .padding()
            .cardSurface(cornerRadius: AppCornerRadius.large, elevation: .raised)
            .padding(.horizontal)
            .padding(.bottom, 100) // Above tab bar
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
        .animation(.spring(), value: error.id)
    }
}

// MARK: - Toast View

struct ToastView: View {
    let message: ToastMessage

    var body: some View {
        VStack {
            Spacer()

            HStack {
                Image(systemName: iconName)
                    .foregroundColor(iconColor)

                Text(message.message)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .cardSurface(cornerRadius: AppCornerRadius.pill, elevation: .raised)
            .padding(.bottom, 100)
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private var iconName: String {
        switch message.type {
        case .success: return "checkmark.circle.fill"
        case .error: return "exclamationmark.circle.fill"
        case .info: return "info.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        }
    }

    private var iconColor: Color {
        switch message.type {
        case .success: return AppColors.bullish
        case .error: return AppColors.bearish
        case .info: return AppColors.primaryBlue
        case .warning: return AppColors.neutral
        }
    }
}
