//
//  PaywallView.swift
//  ios
//
//  Screen: Upgrade / plan catalog. Renders the LIVE tier catalog (Free / Pro / Max from
//  GET /billing/plans) with the user's current tier marked, and — for each plan — the
//  feature rows the backend derives from `entitlements.py`, the same module that enforces
//  the gates. Purchases go through StoreKit 2 (`StoreKitService`); Apple requires
//  functional Terms of Use + Privacy Policy links and a restore mechanism in any
//  subscription flow, so all three are at the bottom.
//
//  WHY THE FEATURE LIST COMES OVER THE WIRE
//  ----------------------------------------
//  This screen used to describe every plan with one line — "1,200 credits / month · ~60
//  reports" — and nothing else, so nothing on it mentioned that a plan unlocks investor
//  holdings, the signal tickers, the full Updates feed or Money Moves narration. The two
//  sentences that did try to sell ("plus priority analysis") described a priority tier
//  that has never existed: report scheduling is global and no code path reads `tier`.
//  `entitlements.py`'s own docstring names the failure this fixes — "silent drift between
//  the gate and the paywall" — so the numbers are read from there rather than typed here.
//  `PlanFeature.bundled(...)` is the offline mirror for a backend that predates it.
//
//  The sheet also knows WHY it was opened (`PaywallContext`), because it is presented from
//  eight different locks and used to look identical from all of them.
//

import SwiftUI

struct PaywallView: View {
    /// Why the sheet was opened. Drives the header, which feature row is highlighted, the
    /// default plan selection, and the `reason` analytics dimension.
    var context: PaywallContext = .general

    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = PaywallViewModel()
    /// Observed so the CTA reflects `isPurchasing` — the ViewModel holds the service, and
    /// changes on a nested ObservableObject don't propagate through it automatically.
    @ObservedObject private var store = StoreKitService.shared
    /// Personalization only APPLIES on Pro/Max, so the upgrade is the one moment the
    /// consent ask is self-explanatory — the reader has just bought the thing it belongs
    /// to. Loaded lazily; the sheet is offered only if they have not already consented.
    @StateObject private var personalizationConsent = PersonalizationConsentViewModel()
    @State private var showPersonalizationConsent = false

    /// The plan the user has TAPPED. Stays nil until they pick one, and the displayed
    /// selection falls back to `defaultSelection` while it is.
    ///
    /// Deliberately not seeded once the catalog arrives: the default depends on
    /// `appState.user.tier`, and the catalog is a public endpoint that routinely resolves
    /// BEFORE the profile does (and again after a session restore, which `AppState` runs
    /// on launch, foreground and network-restore). Anything computed at catalog-time is
    /// computed against whatever tier happened to be known then — a Pro subscriber would
    /// open on Pro, be told "Current Plan", and see no upgrade at all. Deriving on read
    /// makes the selection follow the tier until the moment the user overrides it.
    @State private var selectedTier: String?

    /// Alerts keyed off optional state, so dismissing clears the value rather than leaving
    /// a stale flag that re-presents.
    private var purchaseSucceeded: Binding<Bool> {
        Binding(
            get: { viewModel.purchasedTier != nil },
            set: { if !$0 { viewModel.purchasedTier = nil } }
        )
    }

    private var restoreFinished: Binding<Bool> {
        Binding(
            get: { viewModel.restoreMessage != nil },
            set: { if !$0 { viewModel.restoreMessage = nil } }
        )
    }

    /// A purchase failure must NOT go through the inline `errorView` below: that branch is
    /// reachable only while `catalog == nil`, and a plan button cannot exist until the catalog
    /// has loaded. Anything written there after a tap is unreachable, which is exactly how a
    /// failing purchase came to show nothing at all.
    private var purchaseFailed: Binding<Bool> {
        Binding(
            get: { viewModel.purchaseError != nil },
            set: { if !$0 { viewModel.purchaseError = nil } }
        )
    }

    private var currentTier: UserTier { appState.user.tier }

    private var isSignedIn: Bool { appState.auth.isAuthenticated }

    private var selectedPlan: PlanDTO? {
        guard let catalog = viewModel.catalog else { return nil }
        let tier = selectedTier ?? defaultSelection(catalog)
        return catalog.plans.first { $0.tier == tier } ?? catalog.plans.last
    }

    /// The plan to open on: the cheapest one ABOVE the user's current tier that actually
    /// includes what they were locked out of; failing that, the cheapest paid plan.
    ///
    /// Derived rather than hardcoded, and that is what keeps it correct in both directions.
    /// The backend's own helpers are a mix — `required_tier_for_signals/_whales/
    /// _learn_audio` are FLOORS that always answer Pro, while `required_tier_for_more_
    /// tickers` walks a real ladder — and this single rule reproduces both, because the
    /// catalog arrives sorted cheapest → dearest. So a Free user hitting the signals lock
    /// lands on Pro, and a Pro user who has run out of Updates chips lands on Max, with no
    /// second copy of the ladder on the client.
    private func defaultSelection(_ catalog: PlanCatalog) -> String? {
        let paid = catalog.plans.filter { $0.priceCents > 0 }
        guard !paid.isEmpty else { return catalog.plans.first?.tier }

        let currentIndex = catalog.plans.firstIndex { $0.userTier == currentTier } ?? -1
        let above = catalog.plans.enumerated()
            .filter { $0.offset > currentIndex && $0.element.priceCents > 0 }
            .map(\.element)

        let unlocks = above.first { plan in
            catalog.features(for: plan)
                .first { $0.key == context.featureKey }?
                .included ?? false
        }
        return (unlocks ?? above.first ?? paid.first)?.tier
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.xl) {
                        PaywallHeaderView(context: context)

                        if let catalog = viewModel.catalog {
                            planSelector(catalog)

                            if let plan = selectedPlan {
                                PlanFeatureListSection(
                                    planName: plan.displayName,
                                    features: catalog.features(for: plan),
                                    highlightedKey: context.featureKey
                                )
                                .animation(.easeInOut(duration: 0.2), value: plan.tier)
                            }

                            costNote(catalog)
                        } else if viewModel.isLoading {
                            ProgressView()
                                .tint(AppColors.primaryBlue)
                                .padding(.top, AppSpacing.xxl)
                        } else if let error = viewModel.errorMessage {
                            errorView(error)
                        }

                        legalLinks
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.xxxl)
                }
                // Pinned, not scrolled to. The feature list is eleven wrapping rows, and at
                // the accessibility text sizes a CTA at the bottom of it is several screens
                // below the plan the user just picked.
                .safeAreaInset(edge: .bottom) { purchaseBar }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Upgrade Plan")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .alert("You're all set", isPresented: purchaseSucceeded) {
                Button("Done", role: .cancel) {
                    // Offer the opt-in instead of dismissing, but ONLY when they have not
                    // already consented — a repeat purchase or a re-subscribe must not
                    // re-ask. Falls through to dismiss if the consent state is unknown
                    // (a failed load), because a purchase flow must never dead-end.
                    // "Unknown" covers THREE states, and only one was handled. A failed load
                    // fell through correctly, but a load still IN FLIGHT (the common case on
                    // a slow read — the purchase can easily finish first) was treated as
                    // "has not consented", so a subscriber who consented months ago was asked
                    // again on a re-subscribe. That is the exact thing the comment above
                    // forbids. And with nothing stated there is nothing to consent ABOUT: the
                    // sheet opens "You told us how you like to learn", which would be false.
                    let stateIsUnknown = personalizationConsent.hasLoadFailed
                        || personalizationConsent.isLoading
                    if personalizationConsent.isOn
                        || stateIsUnknown
                        || !personalizationConsent.hasPreferences {
                        dismiss()
                    } else {
                        showPersonalizationConsent = true
                    }
                }
            } message: {
                Text("Your subscription is active and your credits have been added.")
            }
            .sheet(isPresented: $showPersonalizationConsent, onDismiss: { dismiss() }) {
                PersonalizationConsentSheet(viewModel: personalizationConsent)
            }
            // Fetched up front so the "Done" tap can decide instantly rather than
            // stalling the purchase flow on a network call.
            .task { await personalizationConsent.load() }
            .alert("Waiting for approval", isPresented: $viewModel.isPendingApproval) {
                Button("OK", role: .cancel) {}
            } message: {
                // Ask to Buy / SCA. Apple delivers the transaction later via
                // Transaction.updates, so it applies on its own — no action needed.
                Text("This purchase needs approval before it completes. Once approved, your subscription will be applied automatically.")
            }
            .alert("Restore Purchases", isPresented: restoreFinished) {
                Button("OK", role: .cancel) { viewModel.restoreMessage = nil }
            } message: {
                Text(viewModel.restoreMessage ?? "")
            }
            .alert("Purchase Failed", isPresented: purchaseFailed) {
                Button("OK", role: .cancel) { viewModel.purchaseError = nil }
            } message: {
                Text(viewModel.purchaseError ?? "")
            }
        }
        .task {
            // `reason` is what makes conversion readable PER LOCK. Without it a paywall
            // impression carried no trace of what triggered it, so there was no way to
            // tell which gate actually sells a plan.
            Analytics.shared.track(.paywallShown, [
                "reason": .string(context.rawValue),
                "tier": .string(currentTier.rawValue),
            ])
            viewModel.context = context
            await viewModel.load()
        }
    }

    // MARK: - Plan selector

    private func planSelector(_ catalog: PlanCatalog) -> some View {
        VStack(spacing: AppSpacing.sm) {
            ForEach(catalog.plans) { plan in
                PlanSelectorRow(
                    plan: plan,
                    // Apple's localized price wins when the product has loaded — the
                    // catalog's `priceLabel` is USD from our own config.
                    displayPrice: viewModel.displayPrice(forTier: plan.tier),
                    isSelected: plan.tier == selectedPlan?.tier,
                    isCurrent: plan.userTier == currentTier,
                    action: { selectedTier = plan.tier }
                )
            }
        }
    }

    // MARK: - Purchase bar (pinned)

    @ViewBuilder
    private var purchaseBar: some View {
        if let plan = selectedPlan {
            VStack(spacing: 0) {
                Divider().overlay(AppColors.divider)
                Group {
                    if !isSignedIn {
                        PaywallSignInGate(onSignIn: {
                            dismiss()
                            appState.requestSignIn(for: "subscribe")
                        })
                    } else {
                        planCTA(plan)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.md)
                .padding(.bottom, AppSpacing.sm)
            }
            .background(AppColors.background)
        }
    }

    @ViewBuilder
    private func planCTA(_ plan: PlanDTO) -> some View {
        if plan.userTier == currentTier {
            Text("Current Plan")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(AppColors.cardBackgroundLight)
                )
        } else if plan.priceCents == 0 {
            // The free tier is never a purchasable "upgrade" target.
            Text("Included with every account")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
        } else {
            // Is THIS plan the one being bought? `isPurchasing` alone is global, so driving the
            // label from it made every plan read "Processing…" the moment any one of them
            // was tapped. Compare the product id the store is actually working on.
            let isThisPlan = store.purchasingProductID != nil
                && store.purchasingProductID == store.productID(for: plan.tier)

            Button {
                Task { await viewModel.purchase(tier: plan.tier,
                                                accountID: appState.user.profile?.id) }
            } label: {
                HStack(spacing: AppSpacing.xs) {
                    // A SPINNER, not just a word — a moving indicator settles "is this the
                    // one being bought?" at a glance where a dimmed label does not.
                    if isThisPlan {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .tint(AppColors.textOnAccent)
                            .scaleEffect(0.8)
                    }
                    Text(isThisPlan ? "Processing\u{2026}" : "Choose \(plan.displayName)")
                }
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textOnAccent)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        // `alertOrangeFill`, not `alertOrange`: this button carries
                        // constant-white `textOnAccent`, and the text token lightens to
                        // #F97316 in dark where white on it is 2.80. 5.18 both modes now.
                        .fill(AppColors.alertOrangeFill)
                )
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(store.isPurchasing)
            .opacity(store.isPurchasing && !isThisPlan ? 0.3 : 1)
        }
    }

    private func costNote(_ catalog: PlanCatalog) -> some View {
        // "Unused credits reset monthly" was true of the MONTHLY pool and false of the app:
        // purchased credits are a consumable and App Store Guideline 3.1.1 forbids expiring
        // them (`ensure_credit_period` never touches that pool). Said next to a purchase CTA,
        // the unqualified version told the buyer their pack would be wiped at month end.
        // Phrased as "allowance resets" so the sentence names the thing that actually resets.
        Text("Each AI report costs \(catalog.reportCost) credits · each Cay AI chat costs \(catalog.chatCost). Your monthly allowance resets at the start of each month; credits you buy as a pack never expire.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func errorView(_ message: String) -> some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "exclamationmark.triangle")
                .font(AppTypography.iconMedium)
                .foregroundColor(AppColors.neutral)
            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
            Button("Try Again") {
                Task { await viewModel.load() }
            }
            .font(AppTypography.bodyEmphasis)
            .foregroundColor(AppColors.primaryBlue)
        }
        .padding(.top, AppSpacing.xxl)
    }

    // MARK: - Legal (required in the subscription purchase flow)

    private var legalLinks: some View {
        VStack(spacing: AppSpacing.xs) {
            // Restore is REQUIRED by guideline 3.1.1 ("make sure you have a restore
            // mechanism for any restorable in-app purchases"), and it's what a user
            // reinstalling or switching devices reaches for first.
            //
            // ⚠️ Gated on sign-in for the same reason the CTA is: restore submits the
            // recovered transaction to `POST /billing/verify`, which is `.signInRequired`.
            // A signed-out tap reported "no previous purchases found" to a paying
            // subscriber — the request never left the client.
            if isSignedIn {
                Button {
                    Task { await viewModel.restore() }
                } label: {
                    Text("Restore Purchases")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .disabled(store.isPurchasing)
                .padding(.bottom, AppSpacing.xs)
            }

            HStack(spacing: AppSpacing.xs) {
                NavigationLink {
                    TermsOfUseView()
                } label: {
                    Text("Terms of Use")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                }
                Text("·")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                NavigationLink {
                    PrivacyPolicyView()
                } label: {
                    Text("Privacy Policy")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            Text("Subscriptions renew monthly until canceled.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.top, AppSpacing.sm)
    }
}

#Preview {
    PaywallView(context: .learnAudio)
        .environment(\.appState, AppState())
}
