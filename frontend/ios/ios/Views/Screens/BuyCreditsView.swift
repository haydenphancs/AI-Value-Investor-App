//
//  BuyCreditsView.swift
//  ios
//
//  Buy Credits — one-off CONSUMABLE credit packs.
//
//  Every "Add More Credits" affordance used to open `PaywallView`, the Free/Pro/Max
//  subscription sheet. That is the wrong destination for someone who has just run out
//  mid-task: they want to finish the thing they were doing, not commit to a monthly plan.
//  This screen sells credits directly and keeps the plans one tap away.
//
//  Two things on this screen are compliance, not decoration:
//
//  1. **Purchased credits never expire** (App Store Guideline 3.1.1), while the monthly
//     allowance resets. The balance header therefore splits the two, and the reset date is
//     only shown against the part that actually resets — a "Renews Aug 31" label sitting over
//     a total that includes purchased credits tells the user something untrue.
//
//  2. **Restore** exists because Apple does not restore consumables. `Transaction`
//     `currentEntitlements` excludes them, so the server ledger IS the restore mechanism and
//     the button drains `Transaction.unfinished` instead.
//

import SwiftUI

struct BuyCreditsView: View {

    @Environment(\.dismiss) private var dismiss
    @Environment(\.appState) private var appState

    @StateObject private var viewModel = BuyCreditsViewModel()
    /// Nested `ObservableObject` changes don't propagate through the view model, so the
    /// per-pack "Processing…" state needs its own observation. Same reason `PaywallView`
    /// observes it directly.
    @ObservedObject private var store = StoreKitService.shared

    // MARK: - Alert bindings
    //
    // Optional-backed so each alert has exactly one source of truth and clearing it is the
    // dismissal. Mirrors PaywallView.

    private var purchaseSucceeded: Binding<Bool> {
        Binding(
            get: { viewModel.grantedCredits != nil },
            set: { if !$0 { viewModel.grantedCredits = nil } }
        )
    }

    private var purchaseFailed: Binding<Bool> {
        Binding(
            get: { viewModel.purchaseError != nil },
            set: { if !$0 { viewModel.purchaseError = nil } }
        )
    }

    private var restoreFinished: Binding<Bool> {
        Binding(
            get: { viewModel.restoreMessage != nil },
            set: { if !$0 { viewModel.restoreMessage = nil } }
        )
    }

    private var credits: CreditInfo? { appState.user.credits }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.xl) {
                        balanceHeader
                        whatCreditsBuy

                        // The sign-in gate now sits ABOVE the packs instead of replacing them.
                        // `/billing/credit-packs` is public precisely so this screen renders
                        // before we know who is looking, and hiding what a pack contains from
                        // the person deciding whether to make an account is both worse selling
                        // and the opposite of what `PaywallView` does. The safety rule it
                        // enforces is untouched: no purchase button until there is an account
                        // to attach a consumable to — that was always about the BUTTON.
                        if !appState.auth.isAuthenticated {
                            signInGate
                        }

                        if !viewModel.packs.isEmpty {
                            // The notice sits ABOVE the packs and never instead of them. When
                            // Apple has no products the cards still say what each pack grants;
                            // only the price and the Buy button are withheld.
                            if let notice = viewModel.storefrontNotice,
                               appState.auth.isAuthenticated {
                                InlineRetryNotice(message: notice) {
                                    Task { await viewModel.load() }
                                }
                            }
                            VStack(spacing: AppSpacing.md) {
                                ForEach(viewModel.packs) { pack in
                                    packCard(pack)
                                }
                            }
                            neverExpiresNote
                        } else if viewModel.isLoading {
                            ProgressView()
                                .tint(AppColors.primaryBlue)
                                .padding(.top, AppSpacing.xxl)
                        } else if let error = viewModel.errorMessage {
                            errorView(error)
                        }

                        seePlansButton
                        legalLinks
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.xxxl)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Add Credits")
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
            .alert("Credits added", isPresented: purchaseSucceeded) {
                Button("Done", role: .cancel) { dismiss() }
            } message: {
                Text("\(viewModel.grantedCredits ?? 0) credits are now in your balance. "
                     + "They never expire.")
            }
            .alert("Already added", isPresented: $viewModel.alreadyApplied) {
                Button("OK", role: .cancel) {}
            } message: {
                // A replayed delivery. Claiming "N credits added" here would be a number the
                // user can check against their balance and find false.
                Text("This purchase had already been applied to your account, so your balance "
                     + "is unchanged.")
            }
            .alert("Waiting for approval", isPresented: $viewModel.isPendingApproval) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("This purchase needs approval before it completes. Once approved, your "
                     + "credits will be added automatically.")
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
            .sheet(isPresented: $viewModel.showPlans) {
                // Presented ON TOP of this screen rather than dismiss-then-represent: the
                // cross-presentation dance is where SwiftUI drops the second sheet.
                // PaywallView dismisses itself on success, popping back here.
                PaywallView(context: .moreCredits)
                    .environment(\.appState, appState)
            }
        }
        .task {
            Analytics.shared.track(.creditPackShown)
            // Re-read the balance on open. Nothing else does: `AppState.user.credits` is
            // populated at sign-in and refreshed only by an entitlement change, so a user who
            // spent credits elsewhere in the session arrived here to a stale number — on the
            // one screen where the balance is the whole point.
            if appState.auth.isAuthenticated {
                await appState.refreshCredits()
            }
            await viewModel.load()
        }
    }

    // MARK: - Balance

    private var balanceHeader: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "creditcard.fill")
                .font(.system(size: 32))
                .foregroundColor(AppColors.alertOrange)

            // HIDE an unknown balance rather than rendering `?? 0`. "0 credits available" on
            // the buy screen reads as "you have none" to someone who may have plenty — and it
            // is the one number on this screen a user will check against what they just paid
            // for. Matches the policy the rest of the app already follows for an unknown
            // balance (see `CreditBalance.mock`'s note and `GenerateAnalysisSection`).
            if let credits {
                Text("\(credits.remaining)")
                    .font(AppTypography.dataHero)
                    .foregroundColor(AppColors.textPrimary)
                Text("credits available")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            } else {
                Text("Your balance")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }

            balanceBreakdown
        }
    }

    /// Splits the balance so the reset date is attached ONLY to the part that resets.
    /// Rendering "Renews Aug 31" over a total that includes purchased credits would tell the
    /// user their paid credits expire — which Guideline 3.1.1 forbids them from doing.
    @ViewBuilder
    private var balanceBreakdown: some View {
        if let credits {
            let purchased = credits.purchasedCredits
            let monthly = credits.monthlyRemaining
            HStack(spacing: AppSpacing.xs) {
                Text("\(monthly) monthly")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                if purchased > 0 {
                    Text("·")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text("\(purchased) purchased")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.gain)
                }
            }
        }
    }

    private var whatCreditsBuy: some View {
        Text("Each AI report costs \(viewModel.reportCost) credits · each Cay AI question "
             + "costs \(viewModel.chatCost).")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.center)
    }

    // MARK: - Pack card

    private func packName(_ pack: CreditPackDTO) -> some View {
        Text(pack.displayName)
            .font(AppTypography.heading)
            .foregroundColor(AppColors.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// Apple's localized price, or NOTHING — never our USD `priceLabel`.
    ///
    /// The old fallback to `priceLabel` was unreachable when it was safe and wrong when it was
    /// reachable: if the `Product` exists then so does `displayPrice`, so the fallback could
    /// only ever fire in exactly the state where we cannot tell "App Store Connect has no
    /// products" from "this storefront can't quote a price". `priceLabel` is USD from our own
    /// display config, and printing it there claims a price we might not charge.
    ///
    /// `credits` on the card below is the opposite case — it is the server's authoritative
    /// grant value, read from the DB on every purchase, so the card can still say exactly what
    /// you would get. Assert what we know; visibly refuse to assert what we don't.
    @ViewBuilder
    private func priceLabel(_ pack: CreditPackDTO) -> some View {
        if let price = viewModel.displayPrice(for: pack) {
            Text(price)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
        } else {
            Text("Price unavailable")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func packCard(_ pack: CreditPackDTO) -> some View {
        let reports = viewModel.reportCost > 0 ? pack.credits / viewModel.reportCost : 0
        let isThisPack = viewModel.isPurchasing(pack)
        return VStack(alignment: .leading, spacing: AppSpacing.md) {
            // "Price unavailable" is far wider than "$5.99", so the name/price row is allowed
            // to stack rather than truncate the pack name at the accessibility text sizes.
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline) {
                    packName(pack)
                    Spacer()
                    priceLabel(pack)
                }
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    packName(pack)
                    priceLabel(pack)
                }
            }
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "creditcard.fill")
                    .font(AppTypography.iconTiny)
                    .foregroundColor(AppColors.alertOrange)
                Text("\(pack.credits) credits")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                if reports > 0 {
                    Text("· ~\(reports) reports")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            if !appState.auth.isAuthenticated {
                // The one real sign-in CTA is the gate card above; repeating it four times
                // would be four buttons for one decision. The card states what it contains
                // and defers.
                Text("Sign in to buy")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.cardBackgroundLight)
                    )
            } else if viewModel.isPurchasable(pack) {
                Button {
                    Task { await viewModel.purchase(pack,
                                                    accountID: appState.user.profile?.id) }
                } label: {
                    HStack(spacing: AppSpacing.xs) {
                        // See PaywallView: a spinner appears on exactly ONE card, so "which pack am
                        // I buying" is answered at a glance rather than inferred from which button
                        // is dimmer.
                        if isThisPack {
                            ProgressView()
                                .progressViewStyle(.circular)
                                .tint(AppColors.textOnAccent)
                                .scaleEffect(0.8)
                        }
                        Text(isThisPack ? "Processing…" : "Buy")
                    }
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryFill)
                    )
                }
                // Every button disables while ANY purchase is in flight — two concurrent StoreKit
                // sheets is not a state worth supporting — but only the one being bought says
                // "Processing…", and the rest desaturate so they read as unavailable rather than
                // as also-working.
                .disabled(store.isPurchasing)
                .opacity(store.isPurchasing && !isThisPack ? 0.3 : 1)
                .saturation(store.isPurchasing && !isThisPack ? 0 : 1)
            } else {
                // A non-button, NOT a `.disabled()` Button: a disabled button is still an
                // accessibility button and still invites the tap that cannot do anything.
                // Same height and radius as the Buy button so a partial App Store Connect
                // drift leaves a visible hole in the ladder rather than reflowing the list.
                Text("Unavailable")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.cardBackgroundLight)
                    )
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }

    private var neverExpiresNote: some View {
        Text("Purchased credits never expire. Your monthly credits reset each month.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.center)
    }

    // MARK: - Plans

    private var seePlansButton: some View {
        Button {
            viewModel.showPlans = true
        } label: {
            HStack(spacing: AppSpacing.xs) {
                Text("Need credits every month? See plans")
                    .font(AppTypography.bodySmallEmphasis)
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconTiny)
            }
            .foregroundColor(AppColors.primaryBlue)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .cardFill()
            )
        }
        .disabled(store.isPurchasing)
    }

    /// Shown INSTEAD of the pack list when the caller is signed out.
    ///
    /// The gate has to be in front of the StoreKit sheet, not behind it. `verifyPurchase` is
    /// `.signInRequired`, so `APIClient` refuses the call — but only AFTER Apple has already
    /// charged the card. A subscription survives that (it is restorable); a consumable does
    /// not, because guest identity here is per-install and rotatable, so the credits would be
    /// stranded on an install the user can wipe. Hence: no purchase button at all until there
    /// is a real account to attach them to.
    private var signInGate: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "person.crop.circle.badge.plus")
                .font(AppTypography.iconXL)
                .foregroundColor(AppColors.primaryBlue)
            Text("Sign in to buy credits")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
            Text("Credits are tied to your account so they follow you across devices and "
                 + "survive reinstalling the app.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
            Button {
                dismiss()
                appState.requestSignIn(for: "buy credits")
            } label: {
                Text("Sign In")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryFill)
                    )
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
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

    // MARK: - Legal

    private var legalLinks: some View {
        VStack(spacing: AppSpacing.xs) {
            // Apple does NOT restore consumables — `Transaction.currentEntitlements` excludes
            // them — so this drains `Transaction.unfinished` instead. That is what recovers a
            // pack the user paid for when the app was killed mid-purchase or they were signed
            // out when Apple delivered it.
            Button {
                Task { await viewModel.restore() }
            } label: {
                Text("Restore Purchases")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .disabled(store.isPurchasing)
            .padding(.bottom, AppSpacing.xs)

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
            Text("Credit packs are a one-time purchase, not a subscription.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
        }
        .padding(.top, AppSpacing.sm)
    }
}

#Preview {
    BuyCreditsView()
        .environment(\.appState, AppState())
}
