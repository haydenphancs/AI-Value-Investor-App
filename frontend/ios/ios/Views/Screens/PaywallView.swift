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
    @State private var showComingSoon = false

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
            .alert("Coming Soon", isPresented: $showComingSoon) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("In-app upgrades will be available in an upcoming release. Thanks for your patience!")
            }
        }
        .preferredColorScheme(.dark)
        .task { await viewModel.load() }
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
            Button(action: { showComingSoon = true }) {
                Text("Choose \(plan.displayName)")
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
        .preferredColorScheme(.dark)
}
