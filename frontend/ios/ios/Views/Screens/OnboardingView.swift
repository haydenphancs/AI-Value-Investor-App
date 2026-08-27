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

    // Tickers first (proven, and the one input everything downstream compounds from),
    // then the two preference steps. Adding steps to a first-run screen costs completion
    // rate, so this is capped at two and both are pure taps with no typing.
    private let lastPage = 4

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: 0) {
                header

                TabView(selection: $page) {
                    welcomePage.tag(0)
                    tickerPage.tag(1)
                    experiencePage.tag(2)
                    topicsPage.tag(3)
                    readyPage.tag(4)
                }
                .tabViewStyle(.page(indexDisplayMode: .never))

                pageDots
                footer
            }
        }
        .sheet(isPresented: $viewModel.showSearch) {
            // Reuses the existing company search rather than building a second one.
            TargetSearchSheet { result in
                viewModel.addFromSearch(symbol: result.ticker, name: result.companyName)
            }
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
                .foregroundColor(AppColors.textOnAccent)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(AppColors.primaryFill)
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
        case 2: return viewModel.experienceLevel == nil ? "Skip this" : "Continue"
        case 3: return viewModel.topics.isEmpty ? "Skip this" : "Continue"
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

    /// Step 3 — how Cay AI should EXPLAIN things.
    ///
    /// ⚠️ This asks about reading preference, never about money. No risk tolerance, net
    /// worth, income, time horizon or goals — collecting those would make the app's
    /// output personalized investment advice rather than education. See
    /// InvestorProfileModels.swift for the full reasoning before adding a question here.
    private var experiencePage: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("How should Cay AI explain things?")
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)

            Text("This only changes how answers are written for you — never what the numbers say.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.lg)

            ScrollView(showsIndicators: false) {
                // VStack required — a ScrollView's ViewBuilder does not stack siblings
                // (the same trap documented on tickerPage).
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Where are you at?")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textSecondary)
                        FlowOptionChips(
                            options: InvestorExperienceLevel.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.experienceLevel == $0 },
                            // Tapping the selected chip clears it — nothing is required,
                            // so an answer must be un-answerable.
                            onTap: { viewModel.experienceLevel = (viewModel.experienceLevel == $0) ? nil : $0 }
                        )
                    }

                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Language")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textSecondary)
                        FlowOptionChips(
                            options: InvestorExplanationStyle.allCases,
                            title: { $0.title },
                            isSelected: { viewModel.explanationStyle == $0 },
                            onTap: { viewModel.explanationStyle = (viewModel.explanationStyle == $0) ? nil : $0 }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }

    /// Step 4 — subjects the reader likes. Drives WHAT gets covered first, never a claim
    /// that anything in these areas is suitable for them.
    private var topicsPage: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What do you like reading about?")
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)

            Text("Cay AI will lead with these. It's about what we cover first, not what you should own.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.lg)

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 0) {
                    FlowOptionChips(
                        options: InvestorTopic.allCases,
                        title: { $0.title },
                        isSelected: { viewModel.isTopicSelected($0) },
                        onTap: { viewModel.toggleTopic($0) }
                    )
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }

    private var readyPage: some View {
        VStack(spacing: AppSpacing.lg) {
            Spacer()
            Image(systemName: AppSymbols.ai)
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
        // Save whatever they answered, on BOTH exits. "Skip" on the last page must not
        // throw away preferences chosen two pages earlier — the button ends the flow, it
        // does not undo it. Self-guarding: writes nothing when nothing was answered, so a
        // straight skip still leaves no row.
        viewModel.savePreferences()

        if skipped {
            viewModel.trackSkipped()
        } else {
            viewModel.trackCompleted()
        }

        // Ask for notifications HERE, and only if they actually picked something.
        // This is the one moment the request is self-explanatory — they've just told
        // us which tickers they care about, so "we'll tell you when these move" needs
        // no justification. Asking on cold launch, before the app has done anything
        // for them, is how you get denied permanently (iOS only ever prompts once).
        //
        // Skipped or empty → don't ask. There is nothing to notify them about yet, and
        // a wasted prompt is a permanent one.
        if !skipped, !viewModel.selected.isEmpty {
            PushNotificationManager.shared.requestAuthorization()
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
                            .foregroundColor(on ? AppColors.textOnAccent.opacity(0.8) : AppColors.textMuted)
                    }
                    .foregroundColor(on ? AppColors.textOnAccent : AppColors.textPrimary)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.vertical, AppSpacing.sm)
                    .background(
                        Capsule().fill(on ? AppColors.primaryFill : AppColors.cardBackgroundLight)
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
