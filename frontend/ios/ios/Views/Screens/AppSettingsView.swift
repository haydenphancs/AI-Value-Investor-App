//
//  AppSettingsView.swift
//  ios
//
//  Screen: General app settings — research defaults, playback, subscription,
//  security (App Lock), data, about, and account deletion.
//

import SwiftUI
import StoreKit
import OSLog

struct AppSettingsView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.requestReview) private var requestReview

    // Synced preferences (see SettingsSyncManager key lists).
    @AppStorage(AnalysisPersona.defaultPersonaStorageKey) private var defaultPersona: String = AnalysisPersona.warrenBuffett.key
    @AppStorage("playback_speed") private var playbackSpeedRaw: Double = PlaybackSpeed.normal.rawValue
    @AppStorage("autoplay_next") private var autoplayNext: Bool = true
    @AppStorage("haptic_feedback") private var hapticFeedback: Bool = true

    // Device-local (NOT synced): App Lock security state.
    @State private var appLockEnabled: Bool = AppLockManager.shared.isEnabled

    @State private var showDeleteConfirmation = false
    /// "Confirm it's you" before the deletion (SEC-11): the sheet that gets the re-auth proof.
    @State private var showConfirmIdentity = false
    /// "Sign out of all devices" (migration 214): its confirmation, in-flight guard and failure.
    @State private var showSignOutEverywhereConfirmation = false
    @State private var isSigningOutEverywhere = false
    @State private var signOutEverywhereError: String?
    /// In-flight guard for account deletion + the local failure message. See `deleteAccount()`
    /// for why the failure cannot go through `appState.currentError` from this screen.
    @State private var isDeleting = false
    @State private var deleteError: String?
    @State private var showClearCacheConfirmation = false
    @State private var isRestoring = false
    @State private var restoreMessage: String?
    /// In-flight guard for "Manage Subscription": presenting Apple's sheet and the refresh
    /// after it closes. See `openManageSubscription()`.
    @State private var isOpeningSubscriptions = false
    @State private var cacheSize = "Calculating..."
    /// Set when the App Store subscriptions deep link cannot be opened (the Simulator has no
    /// App Store). An ALERT, not the global toast: `ToastView` is an `.overlay` on `RootView`
    /// and this screen sits inside the Account `.fullScreenCover`, so a toast raised from here
    /// is drawn behind the cover and never seen. Verified on the Simulator.
    @State private var appStoreUnavailable = false
    /// "Rate the App" tapped in a TestFlight / App Review / Xcode build, where there is no
    /// live listing to review and iOS never shows `requestReview()`. An alert for the same
    /// reason as `appStoreUnavailable`.
    @State private var showPreReleaseRating = false
    /// Pushes "Help Us Improve" — the alert's way forward, so the tap never dead-ends.
    @State private var showFeedback = false

    /// Third-party AI processing consent. 5.1.1(ii)/5.1.2 require an accessible way to
    /// withdraw consent, so it is surfaced here rather than only at the first-send gate.
    @ObservedObject private var aiConsent = AIConsentStore.shared
    @StateObject private var personalizationConsent = PersonalizationConsentViewModel()
    @State private var showWithdrawAIConsentConfirmation = false

    // `body` is split in two on purpose: as ONE modifier chain (content + the synced-row
    // `.onChange` pushes + seven alerts) it exceeded the type checker's budget — "unable to
    // type-check this expression in reasonable time" — once the per-row pushes were added.
    // `settingsScreen` carries the content and its change handlers; `body` adds the alerts.
    var body: some View {
        accountActions(settingsScreen)
            .alert("No App Store", isPresented: $appStoreUnavailable) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("This device can't open the App Store. Manage your subscription in iOS Settings → your name → Subscriptions.")
            }
            // Worded to stay true for TestFlight, App Review and Xcode builds, before AND after
            // launch — so it never has to be edited or removed. And deliberately NEUTRAL: App
            // Review installs are sandbox installs too, so a reviewer who taps the row reads this,
            // and a build that calls itself "pre-release" / "beta" / "not available" invites a
            // Guideline 2.2 / 2.1 rejection.
            .alert("Rate Caydex", isPresented: $showPreReleaseRating) {
                Button("Send Feedback") { showFeedback = true }
                Button("Not Now", role: .cancel) {}
            } message: {
                Text("Ratings are left on the App Store version of Caydex. Want to tell us what you think here instead?")
            }
            // This screen is pushed inside ProfileView's NavigationStack — the same stack that
            // pushes FeedbackView from "Help Us Improve".
            .navigationDestination(isPresented: $showFeedback) {
                // No reward copy: this starts at a rating prompt, and Apple forbids rewarding ratings.
                FeedbackView(showsReward: false)
            }
            .alert("Clear Cache", isPresented: $showClearCacheConfirmation) {
                Button("Cancel", role: .cancel) {}
                Button("Clear", role: .destructive) { clearCache() }
            } message: {
                Text("This will clear cached images and data. Your account, settings, and saved research will not be affected.")
            }
            .alert("Withdraw AI Chat Permission", isPresented: $showWithdrawAIConsentConfirmation) {
                Button("Cancel", role: .cancel) {}
                Button("Withdraw", role: .destructive) { aiConsent.withdraw() }
            } message: {
                Text("Cay AI chat will stop working until you allow it again. Everything else in Caydex keeps working. Conversations you've already saved are not deleted — you can remove those individually from chat history.")
            }
            .alert("Restore Purchases", isPresented: restoreFinished) {
                Button("OK", role: .cancel) { restoreMessage = nil }
            } message: {
                Text(restoreMessage ?? "")
            }
    }

    /// The account-ending actions' presentations — deletion (its confirmation, the "confirm it's
    /// you" sheet, its failure) and sign-out-everywhere — kept OUT of `body`'s modifier chain for
    /// the type-checker budget described above it.
    private func accountActions<Content: View>(_ content: Content) -> some View {
        content
            .alert("Delete Account", isPresented: $showDeleteConfirmation) {
                Button("Cancel", role: .cancel) {}
                // Not the deletion itself: the re-authentication step first (SEC-11).
                Button("Delete Forever", role: .destructive) { showConfirmIdentity = true }
                    .disabled(isDeleting)
            } message: {
                // The subscription sentence is Apple's account-deletion guidance for apps that sell
                // auto-renewable subscriptions: billing is Apple's, so deleting the account here
                // cannot stop it, and the user must be told how to.
                Text("This action is permanent and cannot be undone. All your research reports, watchlists, and settings will be deleted.\n\nDeleting your account does not cancel a Pro or Max subscription billed by Apple. Cancel it first under Manage Subscription, or in Settings › your name › Subscriptions.\n\nA new account made with the same email, Apple ID or Google account within 90 days won't get the free welcome credits again.")
            }
            .sheet(isPresented: $showConfirmIdentity) {
                ConfirmIdentityView(
                    viewModel: ConfirmIdentityViewModel(authService: appState.authService),
                    // Unknown (nil) shows the field: hiding it from a password account would leave
                    // only provider buttons that are not theirs.
                    showsPassword: appState.user.profile?.hasPassword != false,
                    accountEmail: appState.user.profile?.email ?? "",
                    onConfirmed: { proof in deleteAccount(reauthToken: proof) }
                )
                // A sheet does not inherit this screen's `\.appState` (ios-swiftui.md).
                .environment(\.appState, appState)
            }
            // Local, for the reason spelled out in `deleteAccount()`: `appState.currentError` renders
            // on the root, which this fullScreenCover is drawn above.
            .alert(
                "Couldn't delete your account",
                isPresented: Binding(
                    get: { deleteError != nil },
                    set: { if !$0 { deleteError = nil } }
                )
            ) {
                Button("OK", role: .cancel) { deleteError = nil }
            } message: {
                Text(deleteError ?? "")
            }
            .alert("Sign Out of All Devices?", isPresented: $showSignOutEverywhereConfirmation) {
                Button("Cancel", role: .cancel) {}
                Button("Sign Out Everywhere", role: .destructive) { signOutEverywhere() }
                    .disabled(isSigningOutEverywhere)
            } message: {
                Text("This signs your account out on every device, including this iPhone — use it if you've lost a device or see activity you don't recognize. You'll need to sign in again.")
            }
            .alert(
                "Couldn't sign out everywhere",
                isPresented: Binding(
                    get: { signOutEverywhereError != nil },
                    set: { if !$0 { signOutEverywhereError = nil } }
                )
            ) {
                Button("OK", role: .cancel) { signOutEverywhereError = nil }
            } message: {
                Text(signOutEverywhereError ?? "")
            }
    }

    private var settingsScreen: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                // VStack, NOT LazyVStack. All six sections below are unconditional (bar one
                // capability check) and cheap, so laziness bought nothing — while a lazy stack
                // whose child RESIZES IN PLACE is the documented trigger for the permanent
                // 100%-CPU main-thread hang in `LazySubviewPlacements` -> `LazyStack.place` ->
                // `_ViewList_Node.applyNodes`, which took down the Home feed the same way.
                //
                // This screen acquired exactly that shape when the password row started
                // switching between "Change Password" / "Update your account password" and
                // "Set a Password" / "Add a password to sign in without <provider>" — two
                // different heights, chosen by `hasPassword`, which arrives asynchronously from
                // `GET /users/me` and can therefore change while this view is on screen. One
                // such wedge was captured here on 2026-09-01 (2206/2206 samples inside that
                // recursion, taps and scrolls both dead).
                VStack(spacing: AppSpacing.xxl) {
                    aiResearchSection
                    playbackSection
                    subscriptionSection
                    if AppLockManager.shared.isAvailable { securitySection }
                    generalSection
                    aboutSection
                    // Signed-in only, matching Change Password in `generalSection`.
                    // `.deleteAccount` is `.signInRequired`, so `APIClient` refuses the call
                    // before it goes out — a guest tapping "Delete Forever" got a guaranteed
                    // no-op behind a confirmation alert that promised it was permanent. A guest
                    // also has no account to delete: their data is per-install, not per-user.
                    if appState.auth.status == .authenticated {
                        dangerZoneSection
                    }

                    Spacer().frame(height: AppSpacing.xxxl)
                }
                .padding(.top, AppSpacing.md)
            }
        }
        .navigationTitle("General Settings")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onAppear { calculateCacheSize() }
        // The consent record is server-side, so it has to be fetched — the row must show
        // what the backend will actually honour, not a local guess.
        .task { await personalizationConsent.load() }
        // Sync general prefs to the backend when leaving (no-op for guests). A backstop now,
        // not the only push — see the per-row `.onChange` pushes below.
        .onDisappear { SettingsSyncManager.shared.push() }
        // Every synced row PUSHES ON CHANGE, not only on leaving.
        //
        // These four are synced keys, and the next cold launch's hydrate overwrites any synced
        // key the server does not already hold. With the push only in `.onDisappear`, a change
        // made here and then backgrounded/killed before the screen closed never reached the
        // server (and was never marked dirty), so the next launch put the old value back.
        // `push()` marks the keys dirty durably BEFORE its PUT, so a kill mid-request replays.
        //
        // Tell the Research tab immediately. Its ViewModel is a `@StateObject` on a view
        // `ContentView` mounts once for the whole app process, and this screen is a
        // `fullScreenCover` above that tree — so nothing here rebuilds it and, without this
        // post, the new analyst would not apply until the next cold launch.
        .onChange(of: defaultPersona) { _, _ in
            NotificationCenter.default.post(name: .caydexDefaultPersonaChanged, object: nil)
            SettingsSyncManager.shared.push()
        }
        // @AppStorage has already written the key; the live player only has to catch up.
        // `adoptStoredPlaybackSpeed()` reads, never writes, so an unknown stored speed is
        // played at the default without being recorded as a choice.
        .onChange(of: playbackSpeedRaw) { _, _ in
            AudioManager.shared.adoptStoredPlaybackSpeed()
            SettingsSyncManager.shared.push()
        }
        .onChange(of: autoplayNext) { _, _ in SettingsSyncManager.shared.push() }
        .onChange(of: hapticFeedback) { _, _ in SettingsSyncManager.shared.push() }
        .onChange(of: appLockEnabled) { _, newValue in
            // Ignore programmatic reverts (state already matches the manager).
            guard newValue != AppLockManager.shared.isEnabled else { return }
            Task {
                let ok = await AppLockManager.shared.setEnabled(newValue)
                if newValue && !ok { appLockEnabled = false }  // enable cancelled → revert
            }
        }
    }

    // MARK: - AI & Research

    private var aiResearchSection: some View {
        settingsGroup(title: "AI & Research", icon: "brain") {
            settingsPickerRow(
                title: "Default Analyst",
                subtitle: "Pre-selected for new research",
                options: AnalysisPersona.allCases.map { ($0.key, $0.compactName) },
                selection: $defaultPersona
            )

            // The editor the consent row's copy has always pointed at. Until now these
            // questions were asked once during first-run onboarding and were then
            // unreachable for the life of the install — so "add some interests in
            // Settings" was an instruction nobody could follow, and a reader who skipped
            // onboarding had no way to make the feature they consented to do anything.
            NavigationLink {
                InvestorPreferencesView()
            } label: {
                HStack {
                    settingsLabel(
                        title: "Learning Preferences",
                        subtitle: "How Cay AI explains things to you"
                    )
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconXS)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackground)
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
            // Re-read on return so the consent row's status reflects an edit made above.
            .onDisappear { Task { await personalizationConsent.load() } }

            // Explicit, revocable opt-in. NOT a local @AppStorage toggle: the consent
            // record lives on the server (`user_investor_profile.consented_at`) because
            // that is what the backend actually checks before personalizing, and a
            // device-local flag would drift across devices and vanish on reinstall.
            SettingsToggleRow(
                title: "Personalized Explanations",
                subtitle: personalizationConsent.statusText,
                isOn: Binding(
                    get: { personalizationConsent.isOn },
                    // Not optimistic: the row reflects what the SERVER stored. A failed
                    // grant that looked successful is the worst bug this switch could have.
                    set: { newValue in
                        Task { await personalizationConsent.setConsent(newValue) }
                    }
                )
            )
            // Also disabled when the state is UNKNOWN. A failed load leaves `isOn` at its
            // initial `false`, so leaving the switch live let the user act on a baseline
            // that may be the opposite of what the server holds — toggling "on" for consent
            // that is already granted, or "off" believing it was on. `statusText` says
            // "Couldn't check" in that state; the control must match the sentence.
            .disabled(
                personalizationConsent.isSaving
                    || personalizationConsent.isLoading
                    || personalizationConsent.hasLoadFailed
            )
            // Retry is the only action that makes sense while the state is unknown.
            .onTapGesture {
                guard personalizationConsent.hasLoadFailed,
                      !personalizationConsent.isLoading else { return }
                Task { await personalizationConsent.load() }
            }
        }
    }

    // MARK: - Playback

    private var playbackSection: some View {
        settingsGroup(title: "Playback", icon: "headphones") {
            settingsPickerRow(
                title: "Default Speed",
                subtitle: "For book & lesson narration",
                options: PlaybackSpeed.allCases.map { ($0.rawValue, $0.label) },
                selection: $playbackSpeedRaw
            )

            SettingsToggleRow(
                title: "Autoplay Next",
                subtitle: "Continue to the next track automatically",
                isOn: $autoplayNext
            )
        }
    }

    // MARK: - Subscription

    private var subscriptionSection: some View {
        settingsGroup(title: "Subscription", icon: "creditcard") {
            Button(action: openManageSubscription) {
                actionRow(title: "Manage Subscription",
                          subtitle: "Change or cancel in the App Store",
                          systemImage: "arrow.up.right.square",
                          // The sheet takes a moment to present and the post-sheet refresh is
                          // two round trips; without this the row looked dead and a second tap
                          // queued a second sheet.
                          isBusy: isOpeningSubscriptions)
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(isOpeningSubscriptions)
            .opacity(isOpeningSubscriptions ? 0.5 : 1)

            Button {
                guard !isRestoring else { return }
                isRestoring = true
                Task {
                    defer { isRestoring = false }
                    // App Store sync, then BOTH sweeps (`StoreKitService.syncAndRestore()`).
                    //
                    // The sync is what restores on a new or switched device: without it
                    // `Transaction.currentEntitlements` holds only what this device already
                    // knows. The unfinished sweep is the consumable restore: entitlements
                    // EXCLUDE consumables by design, so a credit pack whose
                    // `POST /billing/verify` never landed was invisible to this button and it
                    // answered "No previous purchases found" to someone Apple had charged.
                    // Mirrors `BuyCreditsViewModel.restore()` and `PaywallViewModel.restore()`.
                    //
                    // The copy is classified from what the backend APPLIED, not from counts:
                    // "Restored your subscription." only when a paid tier was really applied,
                    // and a purchase that failed beside one that worked is still reported.
                    let result = await StoreKitService.shared.syncAndRestore()
                    restoreMessage = result.subscriptionMessage
                }
            } label: {
                actionRow(
                    title: "Restore Purchases",
                    // Says what it actually does now that the consumable sweep runs too.
                    subtitle: "Recover a subscription or credits on this Apple ID",
                    systemImage: "arrow.clockwise",
                    // `isRestoring` was assigned and never read, so two sweeps' worth of
                    // network round-trips looked like a dead button and invited a second tap.
                    isBusy: isRestoring
                )
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(isRestoring)
            .opacity(isRestoring ? 0.5 : 1)
        }
    }

    // MARK: - Security

    private var securitySection: some View {
        settingsGroup(title: "Security", icon: "lock.shield") {
            SettingsToggleRow(
                title: "App Lock",
                subtitle: "Require \(AppLockManager.shared.biometryLabel) to open Caydex",
                isOn: $appLockEnabled
            )
        }
    }

    // MARK: - General

    private var generalSection: some View {
        settingsGroup(title: "General", icon: "wrench.and.screwdriver") {
            SettingsToggleRow(
                title: "Haptic Feedback",
                subtitle: "Vibrations for interactions",
                isOn: $hapticFeedback
            )

            Button(action: { showClearCacheConfirmation = true }) {
                HStack {
                    settingsLabel(title: "Clear Cache", subtitle: cacheSize)
                    Spacer()
                    Image(systemName: "trash")
                        .font(AppTypography.iconSmall)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackground)
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // Password — signed-in only. Guests have no password to change, and recovery for
            // signed-out users lives on the sign-in screen instead.
            //
            // The row's WORDING is driven by `hasPassword`, because an Apple/Google account has
            // no password at all and this row used to send those users to a form that demanded
            // a current password they could never supply — the server answered "Your current
            // password is incorrect" about a password that has never existed. Reported from
            // TestFlight.
            //
            // ⚠️ `hasPassword == nil` means UNKNOWN (an older backend, or a failed probe) and
            // MUST be treated as `true`: keeping the classic row is harmless for an OAuth user
            // who now gets an honest error, whereas guessing "Set a Password" would strand a
            // password-having user in a flow the server refuses outright.
            if appState.auth.status == .authenticated {
                let needsPassword = appState.user.profile?.hasPassword == false
                NavigationLink {
                    ChangePasswordView(mode: needsPassword ? .set : .change)
                } label: {
                    HStack {
                        settingsLabel(
                            title: needsPassword ? "Set a Password" : "Change Password",
                            subtitle: needsPassword
                                ? "Add a password to sign in without \(appState.user.profile?.primaryProviderLabel ?? "your provider")"
                                : "Update your account password"
                        )
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconXS)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())

                // Email (audit CC-09). Same `hasPassword` reading as the password row: nil is
                // UNKNOWN and treated as "has one" — the server answers a password sent for a
                // password-less account with AUTH_PASSWORD_NOT_SET, which names the code route,
                // whereas guessing "no password" would ask a password user for a reset code.
                NavigationLink {
                    ChangeEmailView(usesPassword: !needsPassword)
                } label: {
                    HStack {
                        settingsLabel(
                            title: "Change Email",
                            subtitle: appState.user.profile?.email ?? "Move your account to a new address"
                        )
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconXS)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }

            // AI processing consent — shown only once granted; before that the first-send
            // gate is the surface. Withdrawing stops chat until it's granted again.
            if aiConsent.hasConsented {
                Button(action: { showWithdrawAIConsentConfirmation = true }) {
                    HStack {
                        settingsLabel(
                            title: "AI Chat Data Permission",
                            subtitle: aiConsent.grantedAt
                                .map { "Allowed \(Self.consentDateFormatter.string(from: $0))" }
                                ?? "Allowed"
                        )
                        Spacer()
                        Text("Withdraw")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.bearish)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }

    private var restoreFinished: Binding<Bool> {
        Binding(
            get: { restoreMessage != nil },
            set: { if !$0 { restoreMessage = nil } }
        )
    }

    private static let consentDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f
    }()

    // MARK: - About

    private var aboutSection: some View {
        settingsGroup(title: "About", icon: "info.circle") {
            // `requestReview()` alone was a dead tap for most users most of the time: iOS
            // rate-limits it to three prompts per 365 days and documents it as "may not
            // display", with no callback to say which happened. As the only behaviour behind a
            // row the user deliberately tapped, that is a button that silently does nothing.
            //
            // The deep link always works on an App Store install, and `openInSystem` reports it
            // when it cannot open (the Simulator has no App Store) rather than failing silently
            // — the same treatment "Manage Subscription" already gets.
            //
            // It was still a dead tap on TestFlight (1.0 (3) and 1.0 (6)): the App Store id
            // was blank until launch, so the row fell back to `requestReview()`, which iOS
            // never shows there. What a tap does now depends on where THIS copy came from —
            // see `rateTheApp()` and `InstallSourcePolicy.rateAction`.
            Button(action: rateTheApp) {
                actionRow(title: "Rate the App",
                          subtitle: "Tell us what you think",
                          systemImage: "star")
            }
            .buttonStyle(PlainButtonStyle())

            NavigationLink {
                AcknowledgementsView()
            } label: {
                actionRow(title: "Acknowledgements",
                          subtitle: "Open-source licenses",
                          systemImage: "doc.plaintext")
            }

            HStack {
                settingsLabel(title: "Version", subtitle: "Caydex")
                Spacer()
                Text("\(appVersion) (\(buildNumber))")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
        }
    }

    // MARK: - Danger Zone

    private var dangerZoneSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.bearish)
                Text("DANGER ZONE")
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.bearish)
                    .tracking(0.5)
            }
            .padding(.leading, AppSpacing.xs)

            signOutEverywhereRow

            Button(action: { showDeleteConfirmation = true }) {
                HStack(spacing: AppSpacing.md) {
                    Image(systemName: "person.crop.circle.badge.xmark")
                        .font(AppTypography.iconDefault)
                        .foregroundColor(AppColors.bearish)
                        .frame(width: 28, height: 28)
                        .background(AppColors.bearish.opacity(0.15))
                        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.small))

                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text("Delete Account")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.bearish)
                        Text("Permanently remove your account and all data")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Spacer()

                    if isDeleting {
                        ProgressView()
                            .controlSize(.small)
                            .tint(AppColors.textMuted)
                    } else {
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .cardFill()
                        .overlay(
                            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                                .stroke(AppColors.bearish.opacity(0.2), lineWidth: 1)
                        )
                )
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(isDeleting)
            // Dim the WHOLE control rather than swapping a fill — a disabled state built by
            // changing the fill hides the site from the theme greps and can invert the ink.
            .opacity(isDeleting ? 0.5 : 1)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    /// "Sign out of all devices" (migration 214). In the danger zone because it ends THIS session
    /// too — the lever for a lost phone or a session the user does not recognize, and the only
    /// one an Apple/Google account has (it has no password to change).
    private var signOutEverywhereRow: some View {
        Button(action: { showSignOutEverywhereConfirmation = true }) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: "rectangle.portrait.and.arrow.right")
                    .font(AppTypography.iconDefault)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 28, height: 28)

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Sign Out of All Devices")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Ends every session, including this one")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                if isSigningOutEverywhere {
                    ProgressView()
                        .controlSize(.small)
                        .tint(AppColors.textMuted)
                } else {
                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
            .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(isSigningOutEverywhere || isDeleting)
        .opacity(isSigningOutEverywhere ? 0.5 : 1)
    }

    // MARK: - Builders

    @ViewBuilder
    private func settingsGroup<Content: View>(
        title: String,
        icon: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.textMuted)
                Text(title.uppercased())
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .tracking(0.5)
            }
            .padding(.leading, AppSpacing.xs)

            VStack(spacing: 1) { content() }
                .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                // Card on the page background: an edge in light, nothing in dark.
                .cardBorder(cornerRadius: AppCornerRadius.large)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    @ViewBuilder
    private func settingsLabel(title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(title)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
            Text(subtitle)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    /// A settings row whose value is chosen from a menu.
    ///
    /// Replaces two near-identical `HStack { label; Spacer(); Picker }` blocks, and fixes the
    /// layout they shared. A stock menu `Picker` renders its value at BODY size on a greedy
    /// single line — fine for "1x", wrong for "The Everyday Growth Hunter" (26 chars). The
    /// value wrapped to two lines, the label block sat vertically centred against it, and the
    /// row read as unbalanced with the value competing with the title for weight.
    ///
    /// A `Menu` with an explicit label gives the control `Picker` does not:
    ///
    /// * **One step down the type scale.** `bodySmall`, not `body` — a settings value is
    ///   secondary to the row it belongs to, and at equal size the eye cannot tell which is
    ///   which. This is a per-view repoint, not a token change (`project_typography_system`).
    /// * **Top-aligned, not centred.** The title and the value's first line share a line, so a
    ///   value that wraps grows downward instead of pushing the label off-centre.
    /// * **Two lines, right-aligned, with a scale floor.** These names cannot truncate
    ///   readably — "The Everyday Grow…" is useless — so they wrap, and `minimumScaleFactor`
    ///   absorbs the rest at large Dynamic Type instead of clipping.
    /// * **`layoutPriority` on the label** so the value never squeezes the title into wrapping;
    ///   the value takes the leftover width. Deliberately not a fixed `maxWidth`, which would
    ///   clip at accessibility sizes.
    private func settingsPickerRow<Value: Hashable>(
        title: String,
        subtitle: String,
        options: [(value: Value, label: String)],
        selection: Binding<Value>
    ) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            settingsLabel(title: title, subtitle: subtitle)
                .layoutPriority(1)

            Spacer(minLength: AppSpacing.sm)

            Menu {
                // `ForEach(options.indices)` rather than `id: \.value` — Swift has no key paths
                // into tuples, so a labelled-tuple array cannot be iterated by member.
                ForEach(options.indices, id: \.self) { index in
                    let option = options[index]
                    Button {
                        selection.wrappedValue = option.value
                    } label: {
                        // The tick is what makes this read as a picker rather than a list of
                        // actions — the stock Picker draws it for free and a Menu does not.
                        if option.value == selection.wrappedValue {
                            Label(option.label, systemImage: "checkmark")
                        } else {
                            Text(option.label)
                        }
                    }
                }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                    Text(options.first { $0.value == selection.wrappedValue }?.label ?? "")
                        .font(AppTypography.bodySmall)
                        .multilineTextAlignment(.trailing)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .fixedSize(horizontal: false, vertical: true)

                    Image(systemName: "chevron.up.chevron.down")
                        .font(AppTypography.iconXS)
                }
                .foregroundColor(AppColors.primaryBlue)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
    }

    @ViewBuilder
    private func actionRow(
        title: String,
        subtitle: String,
        systemImage: String,
        isBusy: Bool = false
    ) -> some View {
        HStack {
            settingsLabel(title: title, subtitle: subtitle)
            Spacer()
            if isBusy {
                ProgressView()
                    .controlSize(.small)
                    .tint(AppColors.textMuted)
            } else {
                Image(systemName: systemImage)
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .contentShape(Rectangle())
    }

    // MARK: - Helpers

    // Both come from `AppInfo`, not a local copy: these were byte-identical in two screens
    // and a third variant in APIClient, and the feedback report needs them too.
    private var appVersion: String { AppInfo.appVersion }
    private var buildNumber: String { AppInfo.buildNumber }

    private func rateTheApp() {
        switch InstallSourcePolicy.rateAction(for: InstallSourceStore.current,
                                              reviewURL: AppInfo.reviewURL) {
        case .openReview(let url):
            openInSystem(url, action: "open the App Store") {
                appStoreUnavailable = true
            }
        case .explainPreRelease:
            // TestFlight / App Review / Xcode: the listing may not exist yet, and
            // `requestReview()` never displays here — the silent tap testers reported.
            showPreReleaseRating = true
        case .systemPrompt:
            // Only if the App Store id is ever blank again. The system prompt may not show,
            // which is precisely why it is the last resort rather than the behaviour.
            requestReview()
        }
    }

    private func openManageSubscription() {
        // In-flight guard, set synchronously so a double tap cannot queue a second sheet.
        guard !isOpeningSubscriptions else { return }
        isOpeningSubscriptions = true
        Task { @MainActor in
            defer { isOpeningSubscriptions = false }
            // StoreKit's own sheet first. The web page below lists only PRODUCTION
            // subscriptions, so a sandbox subscriber — App Review — tapped "Manage Subscription"
            // and found nothing to manage. The sheet shows whichever environment is live.
            if let scene = UIApplication.shared.connectedScenes
                .first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene {
                do {
                    try await AppStore.showManageSubscriptions(in: scene)
                    // The sheet has CLOSED. An upgrade or downgrade made in it reaches us as a
                    // transaction; if its background verify failed it sits unfinished, and the
                    // screen kept showing the old plan until the next cold launch. Submit it
                    // now, then re-read the tier and credits regardless — a cancellation
                    // changes nothing on Apple's side until the period ends, but re-reading is
                    // cheap and keeps the badge honest. Never `AppStore.sync()` here: that can
                    // prompt for the Apple Account password, which nobody asked for.
                    //
                    // Only for a live session: both calls are `.signInRequired`, so without one
                    // they fail before any I/O and log a failure that is not one. A session
                    // still `.restoring` is drained when it authenticates (`onAuthenticated`).
                    if appState.auth.status == .authenticated {
                        await StoreKitService.shared.drainUnfinishedTransactions()
                        await appState.refreshEntitlement()
                    }
                    return
                } catch {
                    Self.log.warning("showManageSubscriptions failed, falling back to the App Store URL: \(String(describing: type(of: error)), privacy: .public)")
                }
            }
            // Fallback. Deliberately NOT the in-app browser: this is an App Store deep link that
            // must be handled by the App Store app, and `SFSafariViewController` would render a
            // sign-in wall instead. It goes through `openInSystem` so a device that cannot open
            // it — the Simulator has no App Store — says so rather than doing nothing.
            guard let url = URL(string: "https://apps.apple.com/account/subscriptions") else { return }
            openInSystem(url, action: "open the App Store") {
                appStoreUnavailable = true
            }
        }
    }

    private static let log = Logger(subsystem: "com.phan.caydex", category: "settings")

    private func calculateCacheSize() {
        let cacheURL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        guard let cacheURL else {
            cacheSize = "Unable to calculate"
            return
        }
        DispatchQueue.global(qos: .utility).async {
            var size: Int64 = 0
            if let enumerator = FileManager.default.enumerator(
                at: cacheURL,
                includingPropertiesForKeys: [.fileSizeKey],
                options: [.skipsHiddenFiles]
            ) {
                for case let fileURL as URL in enumerator {
                    if let fileSize = try? fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize {
                        size += Int64(fileSize)
                    }
                }
            }
            let formatter = ByteCountFormatter()
            formatter.countStyle = .file
            let formatted = formatter.string(fromByteCount: size)
            DispatchQueue.main.async { cacheSize = formatted }
        }
    }

    private func clearCache() {
        // ⚠️ Do NOT delete the Caches directory wholesale. `URLCache.shared` and
        // `LearnAudioCache` both keep live on-disk stores in there, and removing the directory
        // out from under them leaves each with an index pointing at files that no longer exist.
        // Ask each owner to purge its own store instead.
        URLCache.shared.removeAllCachedResponses()
        LearnAudioCache.shared.purgeAll()
        StockRepository.shared.clearCache()
        // The on-device Home, Updates and Tracking snapshots. Each delete is queued behind any
        // pending write on that store's own disk tail (off the main thread), so the size is
        // counted again only once ALL three have landed. Each purge keeps the account binding,
        // so the next live load saves a fresh snapshot.
        let homeSnapshotPurge = HomeDashboardSnapshotStore.shared.purgeCache()
        let updatesSnapshotPurge = UpdatesFeedSnapshotStore.shared.purgeCache()
        let trackingSnapshotPurge = TrackingSnapshotStore.shared.purgeCache()
        calculateCacheSize()
        Task {
            await homeSnapshotPurge?.value
            await updatesSnapshotPurge?.value
            await trackingSnapshotPurge?.value
            calculateCacheSize()
        }
    }

    /// Sign out of all devices. The local sign-out runs only after the SERVER confirmed — telling
    /// someone a thief was signed out when the call failed would be the worst kind of silent
    /// failure — and a failure stays on this screen in a local alert (same reason as deletion).
    private func signOutEverywhere() {
        guard !isSigningOutEverywhere else { return }
        isSigningOutEverywhere = true
        Task {
            defer { isSigningOutEverywhere = false }
            do {
                try await appState.signOutEverywhere()
            } catch {
                guard !Task.isCancelled else { return }
                let appError = AppError.from(error)
                Self.log.warning("sign-out-all failed: \(appError.analyticsCode, privacy: .public)")
                signOutEverywhereError = appError.message
            }
        }
    }

    private func deleteAccount(reauthToken: String) {
        // Actually delete the account server-side (cascades all user data) BEFORE
        // signing out. Surface a failure instead of silently signing out.
        //
        // ⚠️ TWO things here were broken, and both hid the most destructive action in the app.
        //
        // 1. `appState.currentError` is rendered ONLY by an `.overlay` on the ROOT view. This
        //    screen sits inside the Account `.fullScreenCover`, which is drawn above the root —
        //    the same trap the `appStoreUnavailable` comment at the top of this file already
        //    documents as Simulator-verified. So a failed deletion showed the user NOTHING: the
        //    alert closed, the account still existed, and nothing said so. A LOCAL alert is used
        //    instead, deliberately: `ErrorPresentationHost` fixes shared machinery, but this is
        //    a failure this screen owns.
        // 2. There was no in-flight guard, so a double tap on "Delete Forever" fired two
        //    DELETEs. The first removes the auth row, so the second 401s (`get_current_user`
        //    finds no user) — the user would have been told their deletion failed at the exact
        //    moment it had actually succeeded.
        guard !isDeleting else { return }
        isDeleting = true
        Task {
            defer { isDeleting = false }
            do {
                try await appState.apiClient.request(endpoint: .deleteAccount(reauthToken: reauthToken))
                // Revokes this device's Google grant, then signs out (SOC-04).
                appState.signOutAfterAccountDeletion()
            } catch {
                deleteError = AppError.from(error).message
            }
        }
    }
}

// MARK: - Settings Toggle Row

struct SettingsToggleRow: View {
    let title: String
    let subtitle: String
    @Binding var isOn: Bool

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                Text(subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            Spacer()
            Toggle("", isOn: $isOn)
                .labelsHidden()
                .tint(AppColors.primaryBlue)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        AppSettingsView()
            .environment(\.appState, AppState())
    }
}
