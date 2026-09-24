//
//  TrillionClubDetailView.swift
//  ios
//
//  Screen: one Trillion-Dollar Club company's stakes, opened from its Home card
//  (GET /home/trillion-club/{slug}). A 13F filer WITH A FILING ON FILE gets four segments —
//  Holdings, Changes, Private & non-U.S., History — and every other card (including a 13F filer
//  whose first filing has not been processed yet) gets its disclosed stakes.
//
//  FREE vs PRO is decided on the SERVER (`redact_trillion_club_detail`): a Free caller receives
//  the top 3 holdings, the latest changes and every stake, plus a COUNT of what is withheld.
//  This screen draws those counts as a locked "+N more holdings" row and — only when earlier
//  quarters were actually withheld (`locked_history_count` > 0) — a locked History card, both
//  opening `PaywallView(context: .trillionClub)`. It reloads the moment a purchase lands
//  (`appState.entitlementGeneration`), so the list unlocks in place instead of at the next
//  visit — pinned by backend/tests/test_ios_trillion_club_guards.py.
//
//  Home has no NavigationStack, so this screen is a cover and presents its OWN ticker cover
//  (like ThemeDetailView) rather than routing back through Home. Every cover it presents
//  injects BOTH `AppState` spellings: `TickerDetailView` reads `AppState.self`,
//  `WhaleProfileView` and `PaywallView` read `\.appState`, and a presentation inherits neither
//  for free.
//

import SwiftUI

/// The detail's segments. `History` is Pro (locked card for Free).
enum TrillionClubDetailSegment: String, CaseIterable, Identifiable {
    case holdings = "Holdings"
    case changes = "Changes"
    case stakes = "Private & non-U.S."
    case history = "History"

    var id: String { rawValue }
}

/// "Open profile" on an investor-profile company → its whale profile.
private struct TrillionClubProfileTarget: Identifiable {
    let id = UUID()
    let whaleId: String
}

struct TrillionClubDetailView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: TrillionClubDetailViewModel

    @State private var segment: TrillionClubDetailSegment = .holdings
    /// A tapped holding or stake with a U.S. ticker → the full TickerDetailView.
    @State private var selectedTicker: MarketTicker?
    @State private var profileTarget: TrillionClubProfileTarget?
    /// A tapped source → the in-app browser (never ejects to Safari).
    @State private var browserLink: BrowserLink?
    @State private var showPaywall = false
    @State private var showInfo = false

    init(slug: String) {
        _viewModel = StateObject(wrappedValue: TrillionClubDetailViewModel(slug: slug))
    }

    /// Previews: a view model that already holds a detail (or an error).
    init(viewModel: @autoclosure @escaping () -> TrillionClubDetailViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel())
    }

    var body: some View {
        content
            .background(AppColors.background.ignoresSafeArea())
            .navigationTitle(viewModel.detail?.company.name ?? "")
            .navigationBarTitleDisplayMode(.inline)
            .navigationBarBackButtonHidden(true)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    NavBackButton(weight: .medium, accessibilityText: "Close") { dismiss() }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showInfo = true
                    } label: {
                        Image(systemName: "info.circle")
                            .font(AppTypography.iconMedium)
                            .foregroundColor(AppColors.textPrimary)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("About \(TrillionClubCopy.title)")
                }
            }
            .task { await viewModel.load() }
            // Unlock the moment a purchase lands. Keyed on `entitlementGeneration`, not
            // `user.tier` — the tier hydrates from its `.free` default on every cold launch,
            // which is not an unlock (same reasoning as HomeDashboardView / WhaleProfileView).
            .onChange(of: appState.entitlementGeneration) {
                guard appState.auth.status == .authenticated else { return }
                Task { await viewModel.load() }
            }
            .inAppBrowser(link: $browserLink)
            // `.others`: `otherMembers` is EVERY other member, carded or not, so it must never
            // sit under the no-card caption Home's list carries.
            .sheet(isPresented: $showInfo) {
                TrillionClubInfoSheet(members: .others(viewModel.detail?.otherMembers ?? []))
            }
            // A PLAN gate, so the plan sheet. The `\.appState` injection is REQUIRED — without
            // it PaywallView resolves that key's default `AppState()` and highlights the wrong
            // "current plan".
            .sheet(isPresented: $showPaywall) {
                PaywallView(context: .trillionClub)
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
            .fullScreenCover(item: $profileTarget) { target in
                NavigationStack {
                    WhaleProfileView(whaleId: target.whaleId)
                }
                .environment(appState)
                .environment(\.appState, appState)
            }
    }

    // MARK: - Content states

    @ViewBuilder
    private var content: some View {
        if let detail = viewModel.detail {
            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    header(detail)

                    if detail.showsThirteenFSegments {
                        segmentPicker
                        segmentContent(detail)
                    } else {
                        stakesSection(detail.stakes, detail: detail,
                                      emptyText: "No disclosed stakes on file yet.")
                    }

                    if detail.company.kind == .whaleLink, let whaleId = detail.company.whaleId {
                        profileButton(whaleId: whaleId, name: detail.company.name)
                    }

                    footer
                    Spacer().frame(height: 40)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.md)
            }
        } else if let error = viewModel.errorMessage, !viewModel.isLoading {
            errorState(error)
        } else {
            ProgressView()
                .tint(AppColors.primaryBlue)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    // MARK: - Header

    private func header(_ detail: TrillionClubDetail) -> some View {
        let company = detail.company
        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.md) {
                logo(company)
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(company.name)
                        .font(AppTypography.title)
                        .foregroundColor(AppColors.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    TintedTagBadge(text: company.badgeText, color: AppColors.primaryBlue,
                                   backgroundOpacity: 0.08)
                }
                Spacer(minLength: 0)
            }

            ForEach(headerLines(company), id: \.self) { line in
                Text(line)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let notice = company.noticeText {
                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                    Image(systemName: "clock")
                        .font(AppTypography.iconXS)
                        .foregroundColor(AppColors.textMuted)
                        .accessibilityHidden(true)
                    Text(notice)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if let symbol = company.detailSymbol {
                Button {
                    openTicker(symbol, name: company.name)
                } label: {
                    Label("View \(symbol)", systemImage: "chart.line.uptrend.xyaxis")
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Opens \(company.name)'s stock page")
            }
        }
    }

    /// Market value, the 13F stat line and dates, the next due date, or the explainer.
    private func headerLines(_ company: TrillionClubCompany) -> [String] {
        [company.marketValueLine, company.holdingsStatLine, company.filingDatesLine,
         company.nextDueLine, company.explainer].compactMap { $0 }
    }

    @ViewBuilder
    private func logo(_ company: TrillionClubCompany) -> some View {
        if let symbol = company.logoSymbol {
            CompanyLogoView(ticker: symbol, size: 48)
                .accessibilityHidden(true)
        } else {
            Text(company.monogram)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 48, height: 48)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .cardFill(AppColors.cardBackgroundNested)
                )
                .accessibilityHidden(true)
        }
    }

    // MARK: - Segments (13F filers)

    private var segmentPicker: some View {
        FlowLayout(spacing: AppSpacing.xs, lineSpacing: AppSpacing.xs) {
            ForEach(TrillionClubDetailSegment.allCases) { item in
                // Resting ink is `textSecondary`, not `primaryBlue`: the chip draws its ink on a
                // 15% tint of itself, and primaryBlue there measures 3.87:1 on the light page
                // (textSecondary: 5.56 light / 6.59 dark). Selected stays white on primaryFill.
                AccentFilterChip(
                    label: item.rawValue,
                    accent: AppColors.textSecondary,
                    accentFill: AppColors.primaryFill,
                    isSelected: segment == item,
                    action: { segment = item }
                )
            }
        }
    }

    @ViewBuilder
    private func segmentContent(_ detail: TrillionClubDetail) -> some View {
        switch segment {
        case .holdings: holdingsSection(detail)
        case .changes: changesSection(detail)
        case .stakes:
            stakesSection(detail.otherStakes, detail: detail,
                          emptyText: "No private or non-U.S. stakes on file.")
        case .history: historySection(detail)
        }
    }

    // MARK: Holdings

    private func holdingsSection(_ detail: TrillionClubDetail) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            if detail.holdings.isEmpty && detail.lockedHoldingsText == nil {
                emptyText("No U.S.-listed holdings on this filing.")
            } else {
                listCard {
                    ForEach(Array(detail.holdings.enumerated()), id: \.element.id) { pair in
                        positionRow(pair.element, style: .holding)
                        if pair.offset < detail.holdings.count - 1 || detail.lockedHoldingsText != nil {
                            Divider().overlay(AppColors.divider)
                        }
                    }
                    if let locked = detail.lockedHoldingsText {
                        lockedHoldingsRow(locked)
                    }
                }
            }

            if !detail.holdingNotes.isEmpty {
                stakesSection(detail.holdingNotes, detail: detail, title: "Notes from its filings",
                              emptyText: nil)
            }
        }
    }

    private func lockedHoldingsRow(_ text: String) -> some View {
        Button {
            showPaywall = true
        } label: {
            HStack(spacing: AppSpacing.md) {
                // A TEXT-role token — the glyph must clear 4.5:1 in both appearances.
                Image(systemName: "lock.fill")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.primaryBlue)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(text)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text(TrillionClubCopy.lockedHoldingsHint)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
            }
            .padding(.vertical, AppSpacing.md)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(text), locked")
        .accessibilityHint("Shows upgrade options")
    }

    // MARK: Changes

    private func changesSection(_ detail: TrillionClubDetail) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            if let line = detail.company.changeLine {
                Text(line)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            let kinds = changeKinds(in: detail.changes)
            if !kinds.isEmpty {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    ForEach(kinds, id: \.self) { kind in
                        if let label = kind.pillLabel, let help = kind.helpText {
                            Text("\(label): \(help)")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            if detail.changes.isEmpty {
                // Only a real quarter-on-quarter comparison can have "no changes"; a gap or a
                // first filing is explained by the change line above.
                if let text = detail.changesEmptyText {
                    emptyText(text)
                }
            } else {
                listCard {
                    ForEach(Array(detail.changes.enumerated()), id: \.element.id) { pair in
                        positionRow(pair.element, style: .change)
                        if pair.offset < detail.changes.count - 1 {
                            Divider().overlay(AppColors.divider)
                        }
                    }
                }
            }

            if let unchanged = detail.unchangedText {
                Text(unchanged)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    /// The outcomes present, once each, in a fixed order — the legend explains only what is on
    /// screen.
    private func changeKinds(in changes: [ClubPosition]) -> [ClubChangeKind] {
        let present = Set(changes.compactMap(\.change))
        return ClubChangeKind.allCases.filter { present.contains($0) }
    }

    // MARK: History (Pro)

    @ViewBuilder
    private func historySection(_ detail: TrillionClubDetail) -> some View {
        // A lock only over something: with no earlier quarter withheld, a Free caller sees the
        // same empty state a Pro caller does, not an upgrade pitch for nothing.
        if detail.showsHistoryLock {
            LockedSectionCard(
                title: "History",
                message: "Earlier quarters of this company's 13F are part of a plan. "
                    + "The latest quarter stays free."
            ) {
                showPaywall = true
            }
        } else if detail.history.isEmpty {
            emptyText("No earlier quarters on file yet.")
        } else {
            listCard {
                ForEach(Array(detail.history.enumerated()), id: \.element.id) { pair in
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(pair.element.title)
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        if let line = pair.element.detail {
                            Text(line)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, AppSpacing.sm)
                    .accessibilityElement(children: .combine)
                    if pair.offset < detail.history.count - 1 {
                        Divider().overlay(AppColors.divider)
                    }
                }
            }
        }
    }

    // MARK: - Positions

    @ViewBuilder
    private func positionRow(_ position: ClubPosition, style: ClubHoldingRow.Style) -> some View {
        if let symbol = position.symbol {
            Button {
                openTicker(symbol, name: position.name)
            } label: {
                ClubHoldingRow(position: position, style: style, showsChevron: true)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(position.accessibilityText)
            .accessibilityHint("Opens \(symbol)")
        } else {
            ClubHoldingRow(position: position, style: style)
        }
    }

    // MARK: - Stakes

    @ViewBuilder
    private func stakesSection(_ stakes: [ClubStake], detail: TrillionClubDetail,
                               title: String? = nil, emptyText text: String?) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            if let title {
                Text(title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
            }
            if stakes.isEmpty {
                if let text { emptyText(text) }
            } else {
                listCard {
                    ForEach(Array(stakes.enumerated()), id: \.element.id) { pair in
                        stakeRow(pair.element, onThirteenFCard: detail.company.kind == .thirteenF)
                        if pair.offset < stakes.count - 1 {
                            Divider().overlay(AppColors.divider)
                        }
                    }
                }
            }
        }
    }

    private func stakeRow(_ stake: ClubStake, onThirteenFCard: Bool) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
                Text(stake.investeeName)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: AppSpacing.sm)
                if let symbol = stake.symbol {
                    Button {
                        openTicker(symbol, name: stake.investeeName)
                    } label: {
                        HStack(spacing: AppSpacing.xxs) {
                            Text(symbol)
                            Image(systemName: "chevron.right")
                                .font(AppTypography.iconXS)
                                .accessibilityHidden(true)
                        }
                        .font(AppTypography.labelEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open \(symbol)")
                }
            }

            if let figure = stake.figureText {
                Text(figure)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let listing = stake.localListing {
                Text("Listed in \(listing)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            let chips = stake.chips(onThirteenFCard: onThirteenFCard)
            if !chips.isEmpty {
                ClubChipGroup(chips: chips, source: stake.sourceTitle)
            }

            if let background = stake.background {
                Text("Background: \(background)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let url = stake.sourceURL {
                Button {
                    openExternal(url, into: &browserLink, action: "open that source")
                } label: {
                    Text(stake.sourceText)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                        .underline()
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Opens the source document")
            } else {
                Text(stake.sourceText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let stale = stake.staleText {
                Text(stale)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, AppSpacing.md)
    }

    // MARK: - Pieces

    private func profileButton(whaleId: String, name: String) -> some View {
        Button {
            profileTarget = TrillionClubProfileTarget(whaleId: whaleId)
        } label: {
            Label(TrillionClubCopy.openProfile, systemImage: "person.crop.circle")
                .font(AppTypography.labelEmphasis)
                .foregroundColor(AppColors.primaryBlue)
                .frame(maxWidth: .infinity, minHeight: 44)
                // Text on its own tint: 8% keeps primaryBlue at 4.63:1 in light.
                .background(Capsule().fill(AppColors.primaryBlue.opacity(0.08)))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens \(name)'s investor profile")
    }

    private func listCard<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            content()
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs)
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    private func emptyText(_ text: String) -> some View {
        Text(text)
            .font(AppTypography.bodySmall)
            .foregroundColor(AppColors.textSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(TrillionClubCopy.detailFooter)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            InlineDisclaimerNotice()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "wifi.exclamationmark")
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)
                .accessibilityHidden(true)
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

    private func openTicker(_ symbol: String, name: String) {
        selectedTicker = MarketTicker(name: name, symbol: symbol, type: .stock,
                                      price: 0, changePercent: 0, sparklineData: [])
    }
}

#Preview("Free — NVIDIA") {
    NavigationStack {
        TrillionClubDetailView(viewModel: TrillionClubDetailViewModel.mockLocked)
    }
    .environment(AppState())
}

#Preview("Error") {
    NavigationStack {
        TrillionClubDetailView(viewModel: TrillionClubDetailViewModel.mockError)
    }
    .environment(AppState())
}
