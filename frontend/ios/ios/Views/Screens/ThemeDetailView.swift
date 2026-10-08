//
//  ThemeDetailView.swift
//  ios
//
//  Screen: the Emerging Frontiers theme drill-down, opened by tapping a theme
//  card on Home. A hero image with the theme title + subtitle overlaid, then a
//  "Companies" list of the theme's constituents (logo + name + ticker + current
//  price + green/red daily change), each tappable → the stock's TickerDetailView.
//  All content is server-driven (GET /home/themes/{slug}).
//
//  FREE vs PRO is decided on the SERVER (`theme_detail_redaction.py`, 2026-10-04): a Free
//  caller receives the theme's first companies plus a COUNT of the rest. This screen draws
//  one blurred stand-in row per withheld company — the list keeps its real length — and
//  every one of them opens `PaywallView(context: .themeCompanies)`. It reloads the moment a
//  purchase lands (`appState.entitlementGeneration`), so the list unlocks in place.
//

import SwiftUI

struct ThemeDetailView: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel: ThemeDetailViewModel
    @Environment(\.dismiss) private var dismiss

    /// A tapped company row → the full TickerDetailView (same router as Home).
    @State private var selectedTicker: MarketTicker?
    /// A tapped news headline → the in-app browser (never ejects to Safari).
    @State private var browserLink: BrowserLink?
    @State private var showMethodology = false
    /// A tapped blurred row (or a withheld change) → the plan sheet.
    @State private var showPaywall = false

    init(slug: String) {
        _viewModel = StateObject(wrappedValue: ThemeDetailViewModel(slug: slug))
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            AppColors.background.ignoresSafeArea()
            content
            backButton
        }
        .navigationBarHidden(true)
        .task { await viewModel.load() }
        // Unlock the moment a purchase lands. Keyed on `entitlementGeneration`, not
        // `user.tier` — the tier hydrates from its `.free` default on every cold launch,
        // which is not an unlock (same reasoning as TrillionClubDetailView / Home).
        .onChange(of: appState.entitlementGeneration) {
            guard appState.auth.status == .authenticated else { return }
            Task { await viewModel.load() }
        }
        .inAppBrowser(link: $browserLink)
        .sheet(isPresented: $showMethodology) { ThemeMethodologySheet() }
        // A PLAN gate, so the plan sheet. The `\.appState` injection is REQUIRED — without
        // it PaywallView resolves that key's default `AppState()` and highlights the wrong
        // "current plan".
        .sheet(isPresented: $showPaywall) {
            PaywallView(context: .themeCompanies)
                .environment(\.appState, appState)
        }
        .fullScreenCover(item: $selectedTicker) { ticker in
            NavigationStack {
                TickerDetailView(tickerSymbol: ticker.symbol)
                    .navigationBarHidden(true)
            }
            .environment(appState)
            .environment(\.appState, appState)
        }
    }

    // MARK: - Content states

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading && viewModel.detail == nil {
            ProgressView().tint(AppColors.primaryBlue)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let error = viewModel.errorMessage, viewModel.detail == nil {
            errorState(error)
        } else if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    hero(detail)

                    // Monthly review + daily insights. Each block renders only when the
                    // server sent it, so an older backend shows the screen as it was.
                    if let insight = detail.insight {
                        ThemeInsightCard(insight: insight, onTickerTap: openTicker)
                            .padding(.horizontal, AppSpacing.lg)
                    }
                    if let performance = detail.performance {
                        ThemePerformanceCard(performance: performance, accent: detail.chartAccent)
                            .padding(.horizontal, AppSpacing.lg)
                    }
                    if detail.showsChangesCard {
                        ThemeChangesCard(reviewedOn: detail.reviewedOn, changes: detail.changes,
                                         onTickerTap: openTicker,
                                         lockedCount: detail.lockedChangeCount,
                                         onLockedTap: openPaywall)
                            .padding(.horizontal, AppSpacing.lg)
                    }

                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        Text("Companies")
                            .font(AppTypography.heading)
                            .foregroundColor(AppColors.textPrimary)
                            .padding(.horizontal, AppSpacing.lg)

                        if detail.isEmpty {
                            emptyState
                        } else {
                            companyList(detail)
                                .padding(.horizontal, AppSpacing.lg)
                        }
                    }

                    if !detail.news.isEmpty {
                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            Text("Latest news")
                                .font(AppTypography.heading)
                                .foregroundColor(AppColors.textPrimary)
                            ThemeNewsList(items: detail.news) { url in
                                openExternal(url, into: &browserLink, action: "open that article")
                            }
                        }
                        .padding(.horizontal, AppSpacing.lg)
                    }

                    methodologyFooter
                        .padding(.horizontal, AppSpacing.lg)

                    Spacer().frame(height: 40)
                }
            }
            .ignoresSafeArea(edges: .top)   // hero bleeds under the status bar
        } else {
            Color.clear
        }
    }

    // MARK: - Hero (image + scrim + overlaid title/subtitle)

    private func hero(_ detail: ThemeDetail) -> some View {
        ZStack(alignment: .bottomLeading) {
            heroImage(detail)

            LinearGradient(
                colors: [.clear, .black.opacity(0.15), .black.opacity(0.78)],
                startPoint: .top, endPoint: .bottom
            )

            VStack(alignment: .leading, spacing: 6) {
                // This ink's contrast comes from the 0.78 black scrim above, NOT from
                // the accent stops behind it — over a white photo the scrim alone still
                // gives 11.73:1. If that scrim is ever removed or weakened, these stop
                // being safe and the fallback gradient's own alphas become load-bearing.
                Text(detail.title)
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textOnAccent)
                    .lineLimit(2)
                if !detail.subtitle.isEmpty {
                    Text(detail.subtitle)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textOnAccent.opacity(0.9))
                        .lineLimit(2)
                }
            }
            .padding(AppSpacing.lg)
        }
        .frame(height: 300)
        .frame(maxWidth: .infinity)
        .clipped()
    }

    @ViewBuilder
    private func heroImage(_ detail: ThemeDetail) -> some View {
        if let s = detail.imageUrl, s.hasPrefix("http"), let url = URL(string: s) {
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    heroFallback(detail)   // loading + error both fall back
                }
            }
        } else {
            heroFallback(detail)
        }
    }

    private func heroFallback(_ detail: ThemeDetail) -> some View {
        // `detail.accent` is now `.fill`-clamped (darker in both appearances), so the
        // old 0.25 low stop washed out to near-nothing. 0.55 keeps this reading as a
        // tile rather than a fade. The title ink above rides the 0.78 scrim, not these
        // stops — see `hero(_:)`.
        LinearGradient(
            colors: [detail.accent.opacity(0.85), detail.accent.opacity(0.55)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    // MARK: - Company list (card with hairline dividers)

    private func companyList(_ detail: ThemeDetail) -> some View {
        let companies = detail.companies
        let lockedCount = detail.lockedCompanyCount
        return VStack(spacing: 0) {
            ForEach(Array(companies.enumerated()), id: \.element.id) { pair in
                ThemeCompanyRow(company: pair.element) {
                    selectedTicker = MarketTicker(
                        name: pair.element.name,
                        symbol: pair.element.ticker,
                        type: .stock,
                        price: 0,
                        changePercent: 0,
                        sparklineData: []
                    )
                }
                if pair.offset < companies.count - 1 || lockedCount > 0 {
                    rowDivider
                }
            }
            if lockedCount > 0 {
                lockedRows(lockedCount)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }

    private var rowDivider: some View {
        Rectangle()
            .fill(AppColors.textPrimary.opacity(0.06))
            .frame(height: 1)
            .padding(.leading, 68)   // align under the text, past the logo
    }

    /// One blurred stand-in per company the caller's plan withholds, so the list keeps its
    /// real length, each opening the plan sheet; the first carries the unlock badge. Drawn
    /// from the COUNT alone — no withheld company ever reached this device.
    private func lockedRows(_ count: Int) -> some View {
        let spoken: String = count == 1
            ? "1 more company, locked"
            : "\(count) more companies, locked"
        return VStack(spacing: 0) {
            ForEach(0..<count, id: \.self) { index in
                ThemeCompanyRow(company: .lockedPlaceholder(index), onTap: openPaywall, isLocked: true)
                    .overlay {
                        if index == 0 {
                            unlockBadge(count)
                        }
                    }
                if index < count - 1 {
                    rowDivider
                }
            }
        }
        // ONE VoiceOver element for the whole block — not a list of placeholder rows.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
        .accessibilityHint("Shows upgrade options")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { openPaywall() }
    }

    private func unlockBadge(_ count: Int) -> some View {
        let title: String = count == 1 ? "See 1 more company" : "See \(count) more companies"
        return Label(title, systemImage: "lock.fill")
            .font(AppTypography.labelEmphasis)
            // `textOnAccent` is the ink `primaryFill` DECLARES (a frozen fill → white ink).
            .foregroundColor(AppColors.textOnAccent)
            .lineLimit(1)
            .minimumScaleFactor(0.8)
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(Capsule().fill(AppColors.primaryFill))
            // Decoration over the row: the row beneath takes the tap.
            .allowsHitTesting(false)
    }

    private func openPaywall() {
        showPaywall = true
    }

    // MARK: - Methodology

    private var methodologyFooter: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button { showMethodology = true } label: {
                Label("How we pick these stocks", systemImage: "info.circle")
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .buttonStyle(.plain)
            Text("Informational only — not a recommendation to buy or sell any security.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func openTicker(_ symbol: String) {
        selectedTicker = MarketTicker(name: symbol, symbol: symbol, type: .stock,
                                      price: 0, changePercent: 0, sparklineData: [])
    }

    // MARK: - Back button (floats over the hero)

    private var backButton: some View {
        Button { dismiss() } label: {
            Image(systemName: "chevron.left")
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(AppColors.textOnAccent)
                .frame(width: 36, height: 36)
                // This floats over an arbitrary hero image, so the worst case is a
                // near-white photo: `Color.black.opacity(0.35)` composited to #A6A6A6
                // and put this chevron at 2.44:1. `mediaScrim` is 0.60 — 5.74:1 on
                // white, better on anything darker.
                .background(Circle().fill(AppColors.mediaScrim))
                .hitSlop(reaching: 36)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
    }

    // MARK: - Empty / error

    private var emptyState: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "tray")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)
            Text("No companies to show yet.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "wifi.exclamationmark")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.neutral)
            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
            Button("Retry") { Task { await viewModel.load() } }
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.primaryBlue)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(AppSpacing.xl)
    }
}

/// "How we pick these stocks" — the plain-language methodology behind every theme list.
/// Static copy on purpose: it describes the rules in `services/theme_rotation`, and a
/// change to those rules must change this text in the same release.
private struct ThemeMethodologySheet: View {
    @Environment(\.dismiss) private var dismiss

    private let points: [String] = [
        "Every month we re-check each theme's stocks against the strongest candidates.",
        "What counts most is how much of a company's business is the theme — from its revenue by segment or its own business description — and whether the leading funds that track the theme hold it.",
        "Size and trading volume matter too, and recent 3-6 month performance is only a small tie-breaker.",
        "A company usually has to rank lower for two months in a row before it is replaced, the biggest names stay put, and at most about a third of a list can change in a month. Most months change only a few names, or none.",
        "Performance is shown for the theme's current stocks, equal-weighted — it is not the record of an investable fund.",
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    ForEach(points, id: \.self) { point in
                        HStack(alignment: .top, spacing: AppSpacing.sm) {
                            Text("•").foregroundColor(AppColors.textMuted)
                            Text(point)
                                .foregroundColor(AppColors.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .font(AppTypography.bodySmall)
                    }
                    Text("Theme lists are for education and information only. They are not a recommendation to buy, sell or hold any security.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .padding(.top, AppSpacing.sm)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("How we pick stocks")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}

#Preview {
    NavigationStack {
        ThemeDetailView(slug: "silicon-rush")
    }
    .environment(AppState())
}
