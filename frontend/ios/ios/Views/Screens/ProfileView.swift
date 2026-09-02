//
//  ProfileView.swift
//  ios
//
//  Screen: Account & Settings Dashboard
//  Sections: Identity, Credits, Settings, About, Auth
//

import SwiftUI
import Combine

struct ProfileView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = ProfileViewModel()
    /// Presents the sign-in sheet (guest-first: sign-in is optional and offered here).
    @State private var showSignIn = false
    /// Working copy for the display-name editor, so cancelling leaves the profile alone.
    @State private var editedName = ""
    /// Presents the subscription paywall — the Upgrade card ONLY.
    ///
    /// Split from `showBuyCredits` deliberately: the two used to share one flag, so
    /// repointing "Add Credits" at the credit packs would have taken the Upgrade card with
    /// it and left Profile with no way to change plan at all.
    @State private var showPaywall = false
    /// Presents the consumable credit packs — the "+ Add Credits" pill.
    @State private var showBuyCredits = false

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
                    // The direct children here are a fixed, hand-written list, so laziness bought nothing,
                    // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
                    // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
                    //
                    // The worst instance in the tree, and the reason this sweep happened: the resizing child is
                    // SLOT 0, so every successor offset depends on it. `userIdentitySection` is a 3-way branch
                    // over isAuthenticated / isRestoring / guest - all different heights - and `.restoring` is,
                    // per ProfileViewModel's own doc comment, "the ordinary case, not an edge one". On top of
                    // that: the whole credits section is inserted on the same flag, the avatar swaps component,
                    // isSavingName swaps a spinner for a pencil mid-row, and two credit rows arrive with the
                    // network. `.onChange(of: appState.auth.status)` re-triggers the lot.
                    //
                    // AppSettingsView is pushed INSIDE this NavigationStack, so this container was still mounted
                    // as the stack root when a wedge was sampled there on 2026-09-01 (2206/2206 main-thread
                    // samples in that recursion). It is at least as likely to have been the culprit as the one
                    // that screen had.
                    VStack(spacing: AppSpacing.xxl) {
                        // Section 1: User Identity & Tier
                        userIdentitySection

                        // Section 2: Credit Management (signed-in only — guests are
                        // not credit-metered, so the balance is meaningless for them)
                        if viewModel.isAuthenticated {
                            creditManagementSection
                        }

                        // Section 3: App Settings & Preferences
                        settingsSection

                        // Section 4: About & Legal
                        aboutSection

                        // Section 5: Sign Out.
                        //
                        // Shown during `.restoring` too. This is the app's ONLY sign-out
                        // control, and a user whose session is stuck healing is exactly the
                        // one most likely to want it — hiding it strands them. `signOut()`
                        // tolerates a failed backend call (it resets local state first and
                        // fires `/auth/logout` in the background), so it works offline.
                        //
                        // Credits deliberately stay hidden while restoring: the balance on
                        // screen would be stale and "+ Add Credits" opens a purchase flow that
                        // needs a live token.
                        if viewModel.isAuthenticated || viewModel.isRestoring {
                            signOutSection
                        }

                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.md)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Account")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .onAppear {
                viewModel.appState = appState
                viewModel.loadData()
            }
        }
        // Account is a `.fullScreenCover`, and the root's error toast / toast overlays are drawn
        // BEHIND one — so every failure raised on this screen was invisible. See
        // `ErrorPresentationHost`. This must stay on the cover's root view.
        .errorPresentationHost()
        .sheet(isPresented: $showSignIn) {
            SignInView()
                .environment(appState)
        }
        // A failed rename / credit load used to set `viewModel.errorMessage` and stop there —
        // nothing read it, so the alert simply closed and the old name stayed. `.claude/rules/
        // auth.md` §6 bans exactly that on a user-initiated mutation. An ALERT rather than the
        // shared toast because both live inside this cover; the host above fixes the toast for
        // errors raised by SHARED machinery, but a ViewModel-local failure belongs to the screen.
        .alert(
            "Something went wrong",
            isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.clearError() } }
            )
        ) {
            Button("OK", role: .cancel) { viewModel.clearError() }
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
        .alert("Display Name", isPresented: $viewModel.isEditingName) {
            TextField("Your name", text: $editedName)
                .textInputAutocapitalization(.words)
                .autocorrectionDisabled()
            Button("Cancel", role: .cancel) {}
            Button("Save") { viewModel.saveDisplayName(editedName) }
        } message: {
            Text("This is the name shown on your profile.")
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView(context: .general)
                .environment(\.appState, appState)
        }
        .sheet(isPresented: $showBuyCredits) {
            BuyCreditsView()
                .environment(\.appState, appState)
        }
        .onChange(of: appState.auth.status) { _, newStatus in
            // Auth just succeeded from the sheet → dismiss it and refresh
            // the now-real profile + credits.
            if newStatus == .authenticated {
                showSignIn = false
                viewModel.loadData()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .caydexSettingsHydrated)) { _ in
            // Settings just synced from the server → reflect any appearance change
            // in the picker (otherwise it shows the pre-sign-in selection).
            viewModel.syncAppearanceFromStore()
        }
    }

    // MARK: - Section 1: User Identity & Tier

    private var userIdentitySection: some View {
        VStack(spacing: AppSpacing.lg) {
            VStack(spacing: AppSpacing.md) {
                // `.circle`, unlike the two headers: the squircle exists to rhyme with
                // `CaydexLogoMark` across a nav bar, and there is no logo opposite this hero.
                // Tappable ONLY when signed in. A guest has no `public.users` row to own an
                // avatar, and the endpoint is `.signInRequired` — offering the picker would
                // open the system photo sheet and then fail at the network boundary.
                if viewModel.isAuthenticated {
                    AvatarPickerButton(
                        avatarUrl: viewModel.avatarUrl,
                        size: 80,
                        isUploading: viewModel.isUploadingAvatar,
                        hasAvatar: (viewModel.avatarUrl?.isEmpty == false),
                        onPicked: { viewModel.saveAvatar($0) },
                        onRemove: { viewModel.removeAvatar() },
                        onFailed: { viewModel.reportAvatarPickFailed() }
                    )
                } else {
                    ProfileAvatarView(
                        avatarUrl: viewModel.avatarUrl,
                        size: 80,
                        shape: .circle
                    )
                }

                if viewModel.isAuthenticated {
                    // Signed in: real name / email / tier / member-since.
                    VStack(spacing: AppSpacing.xs) {
                        // Tap the name to rename. PATCH /users/me existed on both sides
                        // but nothing called it, so the name was read-only in the app.
                        Button {
                            editedName = viewModel.displayName
                            viewModel.isEditingName = true
                        } label: {
                            HStack(spacing: AppSpacing.xs) {
                                Text(viewModel.displayName)
                                    .font(AppTypography.titleCompact)
                                    .foregroundColor(AppColors.textPrimary)
                                // `isSavingName` was published and never read, so the rename
                                // looked instantaneous and a slow save looked like nothing had
                                // happened — which is how a FAILED one was indistinguishable
                                // from a no-op. The alert dismisses on tap; this is the only
                                // signal that the write is still in flight.
                                if viewModel.isSavingName {
                                    ProgressView()
                                        .controlSize(.small)
                                        .tint(AppColors.textMuted)
                                } else {
                                    Image(systemName: "pencil")
                                        .font(AppTypography.iconXS)
                                        .foregroundColor(AppColors.textMuted)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                        .disabled(viewModel.isSavingName)
                        .accessibilityLabel("Edit display name. Currently \(viewModel.displayName)")
                        .accessibilityValue(viewModel.isSavingName ? "Saving" : "")

                        Text(viewModel.email)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    TierBadge(tier: viewModel.userTier)

                    Text(viewModel.memberSince)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                } else if viewModel.isRestoring {
                    // We HOLD a credential we could not validate. Not a guest — telling this
                    // user to "sign in to unlock monthly credits" for the account they already
                    // have is the failure `.claude/rules/auth.md` §5 exists to prevent. Keep the
                    // last-known identity on screen and say what is actually happening.
                    VStack(spacing: AppSpacing.xs) {
                        Text(viewModel.displayName)
                            .font(AppTypography.titleCompact)
                            .foregroundColor(AppColors.textPrimary)

                        HStack(spacing: AppSpacing.xs) {
                            ProgressView()
                                .controlSize(.small)
                                .tint(AppColors.textMuted)
                            Text("Reconnecting…")
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.textSecondary)
                        }

                        Text("You're still signed in. We'll refresh your account as soon as the connection is back.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Reconnecting. You are still signed in as \(viewModel.displayName).")
                } else {
                    // Guest: prompt to sign in.
                    VStack(spacing: AppSpacing.xs) {
                        Text("Guest")
                            .font(AppTypography.titleCompact)
                            .foregroundColor(AppColors.textPrimary)

                        Text("Sign in to sync your account and unlock monthly credits.")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Button(action: { showSignIn = true }) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "person.crop.circle.badge.plus")
                                .font(AppTypography.iconSmall)
                            Text("Sign In")
                                .font(AppTypography.bodyEmphasis)
                        }
                        .foregroundColor(AppColors.textOnAccent)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.md)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.primaryFill)
                        )
                    }
                    .buttonStyle(PlainButtonStyle())
                    .padding(.top, AppSpacing.xs)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 2: Credit Management

    private var creditManagementSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "Credit Management", icon: "creditcard.fill")

            VStack(spacing: AppSpacing.sm) {
                // Usage Card
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    HStack {
                        Text("Monthly Credits")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)

                        Spacer()

                        Text("\(viewModel.creditsUsed)/\(viewModel.creditsTotal)")
                            .font(AppTypography.dataHeading)
                            .foregroundColor(AppColors.primaryBlue)
                    }

                    // Progress Bar
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 4)
                                .fill(AppColors.cardBackgroundLight)
                                .frame(height: 8)

                            RoundedRectangle(cornerRadius: 4)
                                .fill(usageBarColor)
                                .frame(
                                    width: geometry.size.width * min(viewModel.creditUsagePercent, 1.0),
                                    height: 8
                                )
                        }
                    }
                    .frame(height: 8)

                    // Purchased credits, when there are any.
                    //
                    // Shown as its own row rather than folded into the bar above, because the
                    // two behave differently and the difference is the whole point: the bar and
                    // its reset date describe the MONTHLY allowance, while these were bought
                    // with real money and — per App Store Guideline 3.1.1 — never expire.
                    // Counting them in "Monthly Credits N/M · Resets on <date>" told the user
                    // the opposite.
                    if viewModel.purchasedCredits > 0 {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "infinity")
                                .font(AppTypography.iconTiny)
                                .foregroundColor(AppColors.gain)
                            Text("+\(viewModel.purchasedCredits) purchased · never expire")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.gain)
                            Spacer()
                        }
                    }

                    // Reset Date + Add Credits
                    HStack {
                        Text(viewModel.creditResetLabel)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Spacer()

                        Button(action: {
                            showBuyCredits = true
                        }) {
                            HStack(spacing: AppSpacing.xs) {
                                Image(systemName: "plus")
                                    .font(AppTypography.iconTiny).fontWeight(.bold)
                                Text("Add Credits")
                                    .font(AppTypography.captionEmphasis)
                            }
                            .foregroundColor(AppColors.textOnAccent)
                            .padding(.horizontal, AppSpacing.md)
                            .padding(.vertical, AppSpacing.xs)
                            .background(
                                Capsule()
                                    .fill(
                                        // `alertOrangeFill`, not `alertOrange`. The ink here was
                                        // already right; the SURFACE was the adaptive text token,
                                        // which lightens to #F97316 in dark where white on it is
                                        // 2.80. The mirror of the Upgrade card below — that one
                                        // is broken in light, this one was broken in dark.
                                        LinearGradient(
                                            colors: [AppColors.alertOrangeFill, AppColors.alertOrangeFill],
                                            startPoint: .leading,
                                            endPoint: .trailing
                                        )
                                    )
                            )
                        }
                        .buttonStyle(PlainButtonStyle())
                    }
                }
                .padding(AppSpacing.lg)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .cardFill()
                )

                // Credit History — the statement behind the number above.
                //
                // Slotted in CREDIT MANAGEMENT rather than the settings card below on
                // purpose: this section is gated on `viewModel.isAuthenticated` while
                // `settingsSection` renders for guests too, and the route is
                // `.signInRequired`. It also belongs directly under the balance it explains.
                //
                // This section has no rows-card of its own, so the row carries its own
                // one-row card chrome, mirroring the settings card below.
                NavigationLink {
                    CreditHistoryView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "clock.arrow.circlepath",
                        iconColor: AppColors.textSecondary,
                        title: "Credit History",
                        subtitle: "Where your credits went"
                    )
                }
                .background(AppColors.cardBackground)
                .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                .cardBorder(cornerRadius: AppCornerRadius.large)

                // Upgrade CTA
                if viewModel.userTier == .free {
                    Button(action: { showPaywall = true }) {
                        UpgradeCard()
                    }
                    .buttonStyle(PlainButtonStyle())
                }

            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 3: App Settings

    private var settingsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "Settings & Preferences", icon: "gearshape.fill")

            VStack(spacing: 0) {
                // Appearance Picker — restored now the forced-dark sweep is done
                // (per-view `.preferredColorScheme(.dark)` removed + adaptive AppColors).
                // Gated on the flag so it can be killed instantly if a regression appears.
                if FeatureFlags.appearanceModeEnabled {
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        HStack(spacing: AppSpacing.md) {
                            Image(systemName: "eye")
                                .font(AppTypography.iconDefault)
                                .foregroundColor(AppColors.textSecondary)
                                .frame(width: 28, height: 28)

                            Text("Appearance")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        HStack(spacing: 2) {
                            ForEach(AppearanceMode.allCases) { mode in
                                Button {
                                    viewModel.appearanceMode = mode
                                } label: {
                                    HStack(spacing: AppSpacing.xs) {
                                        Image(systemName: mode.icon)
                                            .font(AppTypography.iconXS)
                                        Text(mode.rawValue)
                                            .font(AppTypography.caption)
                                    }
                                    // 44pt, per the HIG minimum and WCAG 2.2 SC 2.5.8. The
                                    // chip was ~22pt tall — an 11pt caption plus a 4pt pad —
                                    // because nothing here sized it; `contentShape` makes the
                                    // whole frame tappable rather than just the glyphs.
                                    .frame(maxWidth: .infinity, minHeight: 44)
                                    .background(
                                        Capsule()
                                            // The app's audited segmented-control tokens,
                                            // not a hand-composited `textMuted.opacity(0.3)`.
                                            // An alpha-composited pair is invisible to
                                            // `ThemeContrastAudit` (which resolves declared
                                            // tokens against declared surfaces), so it could
                                            // never be proven — these two are, and the
                                            // manifest already pins `textPrimary` on
                                            // `toggleSelectedBackground` at 14.18 / 10.31.
                                            .fill(viewModel.appearanceMode == mode
                                                  ? AppColors.toggleSelectedBackground
                                                  : Color.clear)
                                    )
                                    .contentShape(Capsule())
                                    .foregroundColor(viewModel.appearanceMode == mode
                                                     ? AppColors.textPrimary
                                                     : AppColors.textMuted)
                                }
                                .buttonStyle(PlainButtonStyle())
                                // VoiceOver could not tell these three apart: selection was
                                // carried ONLY by fill and ink shade, so all three read as
                                // plain buttons and the current mode was unannounced.
                                .accessibilityAddTraits(
                                    viewModel.appearanceMode == mode ? [.isButton, .isSelected] : [.isButton]
                                )
                            }
                        }
                        .padding(2)
                        .background(
                            Capsule()
                                .fill(AppColors.toggleBackground)
                        )
                        // One control, not three loose buttons.
                        .accessibilityElement(children: .contain)
                        .accessibilityLabel("Appearance")
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)

                    settingsRowDivider
                }

                // NO "Notification History" row here any more.
                //
                // It opened a second copy of the same list Tracking → Alerts already shows —
                // same organism, same view-model type — and the tab-bar badge points at
                // Tracking, so this was the one surface the badge could not lead you to.
                // Two surfaces meant two sets of read semantics to keep correct for no gain.
                // The only thing it had that Alerts lacks was an explicit "Mark all read"
                // button, and Alerts marks read ON SIGHT, which makes that redundant.
                //
                // The row below is notification PREFERENCES — a different feature. Do not
                // confuse the two and delete it as well.

                // Notification PREFERENCES. The flag used to gate this because every
                // toggle wrote a preference nothing read; each one now has a real sender
                // behind it, and both directions of that invariant are pinned by
                // `test_push_preference_typing.py`.
                if FeatureFlags.notificationPreferencesEnabled {
                    NavigationLink {
                        NotificationsSettingsView()
                    } label: {
                        ProfileSettingsRowContent(
                            icon: "bell.badge.fill",
                            iconColor: AppColors.textSecondary,
                            title: "Notifications"
                        )
                    }

                    settingsRowDivider
                }

                // General Settings
                NavigationLink {
                    AppSettingsView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "slider.horizontal.3",
                        iconColor: AppColors.textSecondary,
                        title: "General Settings"
                    )
                }

                settingsRowDivider

                // Feedback — a native screen, like every other row in this card.
                //
                // Was a bare `mailto:` with a fixed subject and no body: it did nothing at all
                // on a device with no mail client, and where it did work it dropped the user
                // into an empty compose window, so the reports that arrived were untriageable.
                NavigationLink {
                    FeedbackView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "bubble.left.and.bubble.right.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Help Us Improve"
                    )
                }
            }
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            // Card on the page background: an edge in light, nothing in dark.
            .cardBorder(cornerRadius: AppCornerRadius.large)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 4: About & Legal

    private var aboutSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "About & Legal", icon: "info.circle.fill")

            VStack(spacing: 0) {
                // Help & Support — a native screen, like the four rows below it.
                //
                // This was a bare `mailto:` handler, which meant the row did NOTHING on the
                // Simulator (no Mail app to hand off to) and on any device without a mail
                // account. It was also the only row here that tried to leave the app. Email is
                // still offered, inside SupportView, on a screen that works without it.
                NavigationLink {
                    SupportView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "questionmark.circle.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Help & Support"
                    )
                }

                settingsRowDivider

                // Disclaimers
                NavigationLink {
                    DisclaimersView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "exclamationmark.shield.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Disclaimers"
                    )
                }

                settingsRowDivider

                // Data Sources — attribution for every upstream market-data provider.
                NavigationLink {
                    DataSourcesView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "chart.bar.doc.horizontal.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Data Sources"
                    )
                }

                settingsRowDivider

                // Terms of Use (native in-app screen)
                NavigationLink {
                    TermsOfUseView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "doc.text.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Terms of Use"
                    )
                }

                settingsRowDivider

                // Privacy Policy (native in-app screen)
                NavigationLink {
                    PrivacyPolicyView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "hand.raised.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Privacy Policy"
                    )
                }
            }
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            // Card on the page background: an edge in light, nothing in dark.
            .cardBorder(cornerRadius: AppCornerRadius.large)

            // App Version
            HStack {
                Spacer()
                Text("Caydex v\(appVersion) (\(buildNumber))")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Spacer()
            }
            .padding(.top, AppSpacing.xs)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 5: Sign Out

    private var signOutSection: some View {
        VStack(spacing: AppSpacing.md) {
            Button(action: {
                viewModel.showSignOutConfirmation = true
            }) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "rectangle.portrait.and.arrow.right")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)

                    Text("Sign Out")
                        .font(AppTypography.bodyEmphasis)
                }
                .foregroundColor(AppColors.bearish)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.lg)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .cardFill()
                )
            }
            .buttonStyle(PlainButtonStyle())
            .alert("Sign Out", isPresented: $viewModel.showSignOutConfirmation) {
                Button("Cancel", role: .cancel) {}
                Button("Sign Out", role: .destructive) {
                    viewModel.signOut()
                    dismiss()
                }
            } message: {
                Text("Are you sure you want to sign out? You'll need to sign in again to access your account.")
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Helpers

    /// Inset divider that starts after the icon column (like iOS Settings)
    private var settingsRowDivider: some View {
        Divider()
            .overlay(AppColors.textMuted.opacity(0.3))
            .padding(.leading, AppSpacing.lg + 28 + AppSpacing.md)
    }

    private var usageBarColor: Color {
        if viewModel.creditUsagePercent > 0.9 { return AppColors.bearish }
        if viewModel.creditUsagePercent > 0.7 { return AppColors.neutral }
        return AppColors.primaryBlue
    }

    // Both come from `AppInfo`, not a local copy: these were byte-identical in two screens
    // and a third variant in APIClient, and the feedback report needs them too.
    private var appVersion: String { AppInfo.appVersion }
    private var buildNumber: String { AppInfo.buildNumber }

    // No mail handler lives on this screen any more. Both rows that used to own one are now
    // native screens — Help & Support is `SupportView`, Help Us Improve is `FeedbackView` —
    // and the mail hand-off happens there, through `MFMailComposeViewController`, which can be
    // asked up front whether the device can send mail at all. That is what a bare `mailto:`
    // could never do, and why both rows silently did nothing on the Simulator for so long.
}

// MARK: - Tier Badge

struct TierBadge: View {
    let tier: UserTier

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: tierIcon)
                .font(AppTypography.iconXS).fontWeight(.bold)

            Text(tierLabel)
                .font(AppTypography.captionEmphasis)
        }
        .foregroundColor(tierTextColor)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs + 2)
        .background(
            Capsule()
                .fill(tierBackgroundColor)
                .overlay(
                    Capsule()
                        .stroke(tierBorderColor, lineWidth: 1)
                )
        )
    }

    private var tierLabel: String {
        switch tier {
        case .free: return "FREE"
        case .pro: return "PRO"
        case .premium: return "MAX"   // 'premium' enum is displayed as "Max" (plan_credits.display_name)
        }
    }

    private var tierIcon: String {
        switch tier {
        case .free: return "person.fill"
        case .pro: return "bolt.fill"
        case .premium: return "crown.fill"
        }
    }

    private var tierTextColor: Color {
        switch tier {
        case .free: return AppColors.textSecondary
        case .pro: return AppColors.primaryBlue
        case .premium: return AppColors.accentYellow
        }
    }

    private var tierBackgroundColor: Color {
        switch tier {
        case .free: return AppColors.textSecondary.opacity(0.1)
        case .pro: return AppColors.primaryBlue.opacity(0.15)
        case .premium: return AppColors.accentYellow.opacity(0.12)
        }
    }

    private var tierBorderColor: Color {
        switch tier {
        case .free: return AppColors.textSecondary.opacity(0.2)
        case .pro: return AppColors.primaryBlue.opacity(0.3)
        case .premium: return AppColors.accentYellow.opacity(0.3)
        }
    }
}

// MARK: - Profile Stat Item

struct ProfileStatItem: View {
    let value: String
    let label: String
    let icon: String

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            Image(systemName: icon)
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.primaryBlue)

            Text(value)
                .font(AppTypography.dataLarge)
                .foregroundColor(AppColors.textPrimary)

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Section Header

struct ProfileSectionHeader: View {
    let title: String
    let icon: String

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: icon)
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textMuted)
                .textCase(.uppercase)
                .tracking(0.5)
        }
        .padding(.leading, AppSpacing.xs)
    }
}

// MARK: - Credit Info Pill

struct CreditInfoPill: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            Text("\(label): \(value)")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

// MARK: - Upgrade Card

struct UpgradeCard: View {
    // `alertOrangeFill` (#C2410C both modes), never `alertOrange` — the ink below is constant
    // white, and a fill that lightens in dark drops it to 2.80.
    private let gradientColors = [
        AppColors.alertOrangeFill,
        AppColors.alertOrangeFill
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    // `textOnAccent` (constant white), NOT `textPrimary` — that is #0F172A in
                    // LIGHT and #FFFFFF in dark, so it inverted against a fill that did not and
                    // rendered near-black on orange at 3.43:1 in light. 5.18 now, both modes.
                    // ⚠️ No `.opacity()`: white at 0.8 on this fill is 3.85, below AA.
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "bolt.fill")
                            .font(AppTypography.iconSmall)
                            .foregroundColor(AppColors.textOnAccent)

                        Text("Upgrade Plan")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textOnAccent)
                    }

                    // Names things the app actually gates. The previous line promised
                    // "priority AI and advanced analytics" — there is no priority tier
                    // (report scheduling is global; no code path reads `tier`) and no
                    // analytics surface is gated at all.
                    Text("More credits each month, plus investor holdings, signal tickers and narrated lessons.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textOnAccent)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer()

                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                    .foregroundColor(AppColors.textOnAccent)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        )
    }
}

// MARK: - Settings Row (Tappable)

enum RoundedCornerPosition {
    case top, bottom, all, none
}

struct ProfileSettingsRow: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil
    var showChevron: Bool = true
    var roundedCorners: RoundedCornerPosition = .none
    var action: () -> Void = {}

    var body: some View {
        Button(action: action) {
            ProfileSettingsRowContent(
                icon: icon,
                iconColor: iconColor,
                title: title,
                subtitle: subtitle
            )
        }
        .buttonStyle(PlainButtonStyle())
        .background(AppColors.cardBackground)
        .clipShape(
            UnevenRoundedRectangle(
                topLeadingRadius: roundedCorners == .top || roundedCorners == .all ? AppCornerRadius.large : 0,
                bottomLeadingRadius: roundedCorners == .bottom || roundedCorners == .all ? AppCornerRadius.large : 0,
                bottomTrailingRadius: roundedCorners == .bottom || roundedCorners == .all ? AppCornerRadius.large : 0,
                topTrailingRadius: roundedCorners == .top || roundedCorners == .all ? AppCornerRadius.large : 0
            )
        )
    }
}

// MARK: - Settings Row Content (for NavigationLink usage)

struct ProfileSettingsRowContent: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(AppTypography.iconDefault)
                .foregroundColor(iconColor)
                .frame(width: 28, height: 28)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)

                if let subtitle {
                    Text(subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .contentShape(Rectangle())
    }
}

// MARK: - Preview

#Preview {
    ProfileView()
        .environment(AppState())
}
