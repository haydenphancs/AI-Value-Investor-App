//
//  OnboardingView.swift
//  ios
//
//  First-run flow. Three pages, and only the middle one does real work: capture a
//  few tickers.
//
//  WHY TICKERS AND NOTHING ELSE
//  ----------------------------
//  Not a risk profile, not a questionnaire. A populated watchlist is the single input
//  that makes the rest of the app relevant — it drives the Updates tab's pills, the
//  personalized Home strip, the insight sweeper's scopes, and (later) push. Everything
//  downstream compounds from it, and nothing else asked at first run would.
//
//  Before this, a cold user went splash → disclaimer → a dashboard identical for
//  every human on earth, with no explanation of personas, credits, or what a report is.
//
//  RULES
//  -----
//  • Skippable on every page. A first-run screen that traps someone is worse than no
//    first-run screen; "Skip" is always visible, never buried.
//  • Never blocks on the network. Selection is local and instant; the watchlist write
//    happens behind it (see OnboardingViewModel).
//  • Shown AFTER the disclaimer acknowledgement — legal first, always.
//

import SwiftUI

struct OnboardingView: View {
    /// Persisted so first run happens once per install.
    @AppStorage("has_completed_onboarding") private var hasCompletedOnboarding = false

    @StateObject private var viewModel = OnboardingViewModel()
    @State private var page = 0

    private let lastPage = 2

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: 0) {
                header

                TabView(selection: $page) {
                    welcomePage.tag(0)
                    tickerPage.tag(1)
                    readyPage.tag(2)
                }
                .tabViewStyle(.page(indexDisplayMode: .never))

                pageDots
                footer
            }
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $viewModel.showSearch) {
            // Reuses the existing company search rather than building a second one.
            TargetSearchSheet { result in
                viewModel.addFromSearch(symbol: result.ticker, name: result.companyName)
            }
            .preferredColorScheme(.dark)
        }
    }

    // MARK: - Chrome

    private var header: some View {
        HStack {
            Spacer()
            // Always available, on every page.
            Button("Skip") { finish(skipped: true) }
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.md)
    }

    private var pageDots: some View {
        HStack(spacing: 6) {
            ForEach(0...lastPage, id: \.self) { i in
                Capsule()
                    .fill(i == page ? AppColors.primaryBlue : AppColors.textMuted.opacity(0.3))
                    .frame(width: i == page ? 18 : 6, height: 6)
                    .animation(.easeInOut(duration: 0.2), value: page)
            }
        }
        .padding(.bottom, AppSpacing.lg)
    }

    private var footer: some View {
        Button {
            if page < lastPage {
                withAnimation { page += 1 }
            } else {
                finish(skipped: false)
            }
        } label: {
            Text(primaryButtonTitle)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(AppColors.primaryBlue)
                )
        }
        .buttonStyle(PlainButtonStyle())
        .padding(.horizontal, AppSpacing.lg)
        .padding(.bottom, AppSpacing.xxl)
    }

    private var primaryButtonTitle: String {
        switch page {
        case 0: return "Get started"
        // Honest about state instead of a dead-feeling "Next": nothing is required,
        // and the copy says so rather than implying the user is stuck.
        case 1: return viewModel.selected.isEmpty ? "I'll pick later" : "Continue"
        default: return "Start exploring"
        }
    }

    // MARK: - Pages

    private var welcomePage: some View {
        VStack(spacing: AppSpacing.lg) {
            Spacer()
            Image(systemName: "chart.line.uptrend.xyaxis")
                .font(.system(size: 52))
                .foregroundColor(AppColors.primaryBlue)

            Text("Research, not hype")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)

            Text("Caydex reads the filings, the numbers and the news for you, then explains what it found in plain English.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
            Spacer()
            Spacer()
        }
    }

    private var tickerPage: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What do you want to follow?")
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)

            Text("Pick a few. This shapes your Home, your Updates feed, and the alerts you'll get.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.lg)

            ScrollView(showsIndicators: false) {
                // VStack is REQUIRED, not stylistic: a ScrollView's ViewBuilder does
                // not stack multiple children, so the chips and the search button
                // rendered ON TOP of each other.
                VStack(alignment: .leading, spacing: 0) {
                FlowChips(
                    items: viewModel.allChips,
                    isSelected: { viewModel.isSelected($0.symbol) },
                    onTap: { viewModel.toggle($0) }
                )
                .padding(.horizontal, AppSpacing.lg)

                Button {
                    viewModel.showSearch = true
                } label: {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "magnifyingglass")
                        Text("Search for something else")
                    }
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(PlainButtonStyle())
                .padding(.top, AppSpacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppSpacing.lg)
                }
            }
        }
    }

    private var readyPage: some View {
        VStack(spacing: AppSpacing.lg) {
            Spacer()
            Image(systemName: "sparkles")
                .font(.system(size: 52))
                .foregroundColor(AppColors.primaryBlue)

            Text("You're all set")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)

            Text(readyBody)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
            Spacer()
            Spacer()
        }
    }

    private var readyBody: String {
        if viewModel.selected.isEmpty {
            return "Add tickers any time from the star icon. Head to Research to generate your first AI analysis on any company."
        }
        return "\(viewModel.selected.count) added — you'll see them on Home and in Updates. Next, try generating a full AI analysis from the Research tab."
    }

    // MARK: - Exit

    private func finish(skipped: Bool) {
        if skipped {
            viewModel.trackSkipped()
        } else {
            viewModel.trackCompleted()
        }
        hasCompletedOnboarding = true
    }
}

/// Wrapping chip layout. `Layout` rather than a fixed grid so chips of different
/// widths pack naturally and nothing is clipped at large text sizes.
private struct FlowChips: View {
    let items: [OnboardingTicker]
    let isSelected: (OnboardingTicker) -> Bool
    let onTap: (OnboardingTicker) -> Void

    var body: some View {
        // Reuses the existing Views/Atoms/FlowLayout rather than adding a second
        // wrapping layout (see the atom reuse rule in .claude/rules/ios-swiftui.md).
        FlowLayout(spacing: AppSpacing.sm) {
            ForEach(items) { item in
                let on = isSelected(item)
                Button { onTap(item) } label: {
                    HStack(spacing: 6) {
                        if on {
                            Image(systemName: "checkmark")
                                .font(AppTypography.iconXS)
                        }
                        Text(item.symbol)
                            .font(AppTypography.bodySmallEmphasis)
                        Text(item.name)
                            .font(AppTypography.caption)
                            .foregroundColor(on ? .white.opacity(0.8) : AppColors.textMuted)
                    }
                    .foregroundColor(on ? .white : AppColors.textPrimary)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.vertical, AppSpacing.sm)
                    .background(
                        Capsule().fill(on ? AppColors.primaryBlue : AppColors.cardBackgroundLight)
                    )
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }
}

#Preview {
    OnboardingView()
}
