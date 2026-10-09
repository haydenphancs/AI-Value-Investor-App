//
//  PortfolioInsightsSection.swift
//  ios
//
//  Organism: Portfolio Insights section with a toggle.
//
//  Hidden behind an opt-in `Toggle` because the diversification score is only
//  meaningful when the user has filled in shares / market value for at least
//  some of their watchlist tickers — first-run users see the toggle and an
//  inviting blurb instead of a misleading "0" score.
//

import SwiftUI

struct PortfolioInsightsSection: View {
    let score: DiversificationScore?
    var coverageNote: String? = nil
    /// See `DiversificationCard.hint`.
    var hint: String? = nil
    /// Number of tickers the user has actually entered shares / dollars for.
    /// When this is between 1 and `minimumHoldings - 1` the score is nil (you
    /// can't diversify a single position), so we show an explanatory hint
    /// instead of the first-run empty state.
    var enteredHoldingsCount: Int = 0
    /// The answer is not known yet (not "too few holdings", not a failure): a neutral loading
    /// card with no call to action. Never the first-run "Set up" card over an unknown answer.
    var isResolving: Bool = false
    /// The spinner inside the loading card — only while a request is actually on the wire, so
    /// a hidden, never-opened tab mounts no ProgressView.
    var showsProgress: Bool = true
    /// The answer could not be had: says so, with `onRetry`.
    var didFail: Bool = false
    /// The account gate is up for Holdings: a neutral line — no spinner, no Retry (a retry
    /// would re-send a request the client refuses before it leaves the device).
    var isGated: Bool = false
    /// False until the portfolio list is live: the holdings editors are shown but disabled
    /// (hiding them would shift the card when the list lands).
    var configureEnabled: Bool = true
    @Binding var isEnabled: Bool
    var onConfigureTapped: (() -> Void)?
    var onRetry: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header — title + toggle, always visible.
            HStack {
                Text("Portfolio Insights")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Toggle("", isOn: $isEnabled)
                    .labelsHidden()
                    .tint(AppColors.primaryBlue)
            }
            .padding(.horizontal, AppSpacing.lg)

            content
        }
        .padding(.top, AppSpacing.lg)
        .padding(.bottom, AppSpacing.sm)
    }

    @ViewBuilder
    private var content: some View {
        if !isEnabled {
            collapsedHint
        } else if isGated {
            gatedHint
        } else if let score = score {
            VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                DiversificationCard(score: score, coverageNote: coverageNote, hint: hint)

                if onConfigureTapped != nil {
                    Button {
                        onConfigureTapped?()
                    } label: {
                        HStack(spacing: AppSpacing.xxs) {
                            Image(systemName: "pencil")
                                .font(AppTypography.iconXS)
                            Text("Edit holdings")
                                .font(AppTypography.bodySmallEmphasis)
                        }
                        .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(.plain)
                    .disabled(!configureEnabled)
                    .opacity(configureEnabled ? 1 : 0.5)
                }

                // Disclaimer — this is the one score computed from the user's OWN
                // holdings, so it is the surface most likely to read as personal
                // advice. It must say plainly that it isn't.
                AnalysisDisclaimerText(
                    text: "Data Disclaimer: A diversification measure of the holdings you entered — "
                        + "not personalized investment advice or a recommendation to buy or sell. "
                        + "For educational purposes only."
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, AppSpacing.lg)
        } else if isResolving {
            resolvingState
        } else if didFail {
            failedState
        } else if enteredHoldingsCount > 0
                    && enteredHoldingsCount < DiversificationThresholds.minimumHoldings {
            needsMoreHoldingsState
        } else {
            emptyState
        }
    }

    private var collapsedHint: some View {
        Text("Toggle on to score your portfolio's diversification.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, AppSpacing.xs)
    }

    /// The account gate is up above (Reconnecting / Sign in): nothing here can load or retry
    /// until it clears, and "sign in" would be false during a reconnect — so a neutral line.
    private var gatedHint: some View {
        Text("Your diversification score appears here once your account is connected.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, AppSpacing.xs)
    }

    /// Unknown yet. The same card frame as the empty state, so the answer replaces it in place.
    private var resolvingState: some View {
        VStack(spacing: AppSpacing.md) {
            ZStack {
                if showsProgress {
                    ProgressView()
                } else {
                    Image(systemName: "chart.pie")
                        .font(AppTypography.iconDisplay)
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .frame(minHeight: 32)

            Text("Loading your diversification score…")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.lg)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding(.horizontal, AppSpacing.lg)
        .accessibilityElement(children: .combine)
    }

    /// The answer could not be had. Says so, and offers one retry — never the "Set up" card,
    /// which would tell a user with entered holdings that they have none.
    private var failedState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "exclamationmark.triangle")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.neutral)
                .accessibilityHidden(true)

            Text("Couldn't load your diversification score.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.lg)

            if let onRetry {
                Button {
                    onRetry()
                } label: {
                    Text("Retry")
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(AppColors.cardBackgroundLight)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Loads your diversification score again")
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding(.horizontal, AppSpacing.lg)
    }

    /// Shown when the user has entered at least one holding but fewer than the
    /// minimum needed to score (a single position can't be "diversified"). This
    /// replaces the silent dead-end where one entered holding looked identical
    /// to having entered nothing.
    private var needsMoreHoldingsState: some View {
        let minimum = DiversificationThresholds.minimumHoldings
        return VStack(spacing: AppSpacing.md) {
            Image(systemName: "chart.pie")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)

            Text("Diversification needs at least \(minimum) holdings — you've entered \(enteredHoldingsCount). Add another ticker to this portfolio and enter its shares or amount to see your score.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.lg)

            if let onConfigureTapped = onConfigureTapped {
                Button {
                    onConfigureTapped()
                } label: {
                    HStack(spacing: AppSpacing.xxs) {
                        Image(systemName: "pencil")
                            .font(AppTypography.iconXS)
                        Text("Edit holdings")
                            .font(AppTypography.bodySmallEmphasis)
                    }
                    .foregroundColor(AppColors.textOnAccent)
                    .padding(.horizontal, AppSpacing.xl)
                    .padding(.vertical, AppSpacing.sm)
                    .background(AppColors.primaryFill)
                    .cornerRadius(AppCornerRadius.pill)
                }
                .buttonStyle(.plain)
                .disabled(!configureEnabled)
                .opacity(configureEnabled ? 1 : 0.5)
                .padding(.top, AppSpacing.xs)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding(.horizontal, AppSpacing.lg)
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "chart.pie")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)

            Text("Enter shares or amounts for the tickers you own to see your diversification score.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.lg)

            if let onConfigureTapped = onConfigureTapped {
                Button {
                    onConfigureTapped()
                } label: {
                    Text("Set up Portfolio Insights")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.xl)
                        .padding(.vertical, AppSpacing.sm)
                        .background(AppColors.primaryFill)
                        .cornerRadius(AppCornerRadius.pill)
                }
                .buttonStyle(.plain)
                .disabled(!configureEnabled)
                .opacity(configureEnabled ? 1 : 0.5)
                .padding(.top, AppSpacing.xs)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xxl) {
        PortfolioInsightsSection(
            score: DiversificationScore.sampleData,
            coverageNote: "Based on 2 of 3 tickers",
            hint: DiversificationHint.make(scoredHoldings: 2, enteredTickers: 2, totalTickers: 3),
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            enteredHoldingsCount: 1,
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            isEnabled: .constant(false),
            onConfigureTapped: {}
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}

#Preview("Unknown, failed, gated") {
    VStack(spacing: AppSpacing.xxl) {
        PortfolioInsightsSection(
            score: nil,
            isResolving: true,
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
        PortfolioInsightsSection(
            score: nil,
            didFail: true,
            configureEnabled: false,
            isEnabled: .constant(true),
            onConfigureTapped: {},
            onRetry: {}
        )
        PortfolioInsightsSection(
            score: nil,
            isGated: true,
            isEnabled: .constant(true),
            onConfigureTapped: {}
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
