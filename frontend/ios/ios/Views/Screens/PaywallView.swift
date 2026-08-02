//
//  PaywallView.swift
//  ios
//
//  Screen: Upgrade / plan catalog. Renders the LIVE tier catalog (Free / Pro / Max
//  from GET /billing/plans) with the user's current tier highlighted. The actual
//  App Store purchase is deferred (no StoreKit yet) — the CTA surfaces an honest
//  "coming soon" state. Apple requires functional Terms of Use + Privacy Policy
//  links in any subscription purchase flow, so both are linked at the bottom.
//

import SwiftUI

struct PaywallView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = PaywallViewModel()
    /// Observed so the CTA reflects `isPurchasing` — the ViewModel holds the service, and
    /// changes on a nested ObservableObject don't propagate through it automatically.
    @ObservedObject private var store = StoreKitService.shared

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

    private var currentTier: UserTier {
        appState.user.tier
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.xl) {
                        header

                        if let catalog = viewModel.catalog {
                            VStack(spacing: AppSpacing.md) {
                                ForEach(catalog.plans) { plan in
                                    planCard(plan, reportCost: catalog.reportCost)
                                }
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
                Button("Done", role: .cancel) { dismiss() }
            } message: {
                Text("Your subscription is active and your credits have been added.")
            }
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
        }
        .task {
            Analytics.shared.track(.paywallShown)
            await viewModel.load()
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "bolt.fill")
                .font(.system(size: 32))
                .foregroundColor(AppColors.alertOrange)
            Text("Unlock more research")
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
            Text("More monthly credits for AI reports and Cay AI chat, plus priority analysis.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
    }

    // MARK: - Plan card

    private func planCard(_ plan: PlanDTO, reportCost: Int) -> some View {
        let isCurrent = plan.userTier == currentTier
        let reports = reportCost > 0 ? plan.monthlyCredits / reportCost : 0
        return VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(alignment: .firstTextBaseline) {
                HStack(spacing: AppSpacing.sm) {
                    Text(plan.displayName)
                        .font(AppTypography.heading)
                        .foregroundColor(AppColors.textPrimary)
                    if isCurrent {
                        Text("CURRENT")
                            .font(AppTypography.captionEmphasis)
                            .foregroundColor(.white)
                            .padding(.horizontal, AppSpacing.sm)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(AppColors.primaryBlue))
                    }
                }
                Spacer()
                Text(plan.priceLabel)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                + Text(plan.priceCents > 0 ? " /mo" : "")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "creditcard.fill")
                    .font(AppTypography.iconTiny)
                    .foregroundColor(AppColors.alertOrange)
                Text("\(plan.monthlyCredits) credits / month")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                if reports > 0 {
                    Text("· ~\(reports) reports")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            planCTA(plan, isCurrent: isCurrent)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .stroke(
                            isCurrent ? AppColors.primaryBlue.opacity(0.5) : Color.clear,
                            lineWidth: 1.5
                        )
                )
        )
    }

    @ViewBuilder
    private func planCTA(_ plan: PlanDTO, isCurrent: Bool) -> some View {
        if isCurrent {
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
            EmptyView()
        } else {
            Button {
                Task { await viewModel.purchase(tier: plan.tier) }
            } label: {
                Text(viewModel.store.isPurchasing
                     ? "Processing\u{2026}"
                     : "Choose \(plan.displayName)")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(
                                LinearGradient(
                                    colors: [Color(hex: "F97316"), Color(hex: "EA580C")],
                                    startPoint: .leading, endPoint: .trailing
                                )
                            )
                    )
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(viewModel.store.isPurchasing)
        }
    }

    private func costNote(_ catalog: PlanCatalog) -> some View {
        Text("Each AI report costs \(catalog.reportCost) credits · each Cay AI chat costs \(catalog.chatCost). Unused credits reset monthly.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.center)
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
            Text("Subscriptions renew monthly until canceled.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.top, AppSpacing.sm)
    }
}

#Preview {
    PaywallView()
        .environment(\.appState, AppState())
}
