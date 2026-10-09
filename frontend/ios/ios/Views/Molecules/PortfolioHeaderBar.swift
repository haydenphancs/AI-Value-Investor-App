//
//  PortfolioHeaderBar.swift
//  ios
//
//  Molecule: row at the top of the Assets tab. Modeled on Apple Stocks.
//  The left-side "Holdings ⌄" chip is the portfolio surface — switch the
//  active portfolio, create a new one, or open Edit Portfolios. The
//  right-side "..." menu is scoped to the active portfolio (sort + open
//  Manage Tickers to reorder/remove its tickers).
//
//  These used to be native SwiftUI `Menu`s, but iOS owns a native menu's
//  width, row height, and open animation — none are tweakable. We render
//  custom anchored popups instead: narrower, tighter rows, and NO open
//  animation. The header emits each trigger's bounds via an anchor
//  preference; `PortfolioHeaderMenuOverlay` (hosted by the parent scroll
//  view) draws the active popup + a full-screen dismiss scrim above the list.
//

import SwiftUI

// MARK: - Menu identity + anchor plumbing

enum PortfolioHeaderMenu: Hashable {
    case portfolio   // left "Holdings ⌄" chip
    case options     // right "..." button
}

/// Carries each trigger's bounds up to the parent so the floating popup can
/// anchor itself directly beneath the button that opened it.
struct PortfolioHeaderMenuAnchorKey: PreferenceKey {
    static var defaultValue: [PortfolioHeaderMenu: Anchor<CGRect>] = [:]
    static func reduce(
        value: inout [PortfolioHeaderMenu: Anchor<CGRect>],
        nextValue: () -> [PortfolioHeaderMenu: Anchor<CGRect>]
    ) {
        value.merge(nextValue()) { $1 }
    }
}

struct PortfolioHeaderBar: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Binding var activeMenu: PortfolioHeaderMenu?
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        // At accessibility text sizes the higher-priority picker takes the row and would squeeze
        // "Updated <time>" — the only on-screen sign that the rows are not live — down to "U…".
        // There it gets a line of its own, under the picker.
        if dynamicTypeSize.isAccessibilitySize, let label = viewModel.snapshotUpdatedLabel {
            stackedLayout(label)
        } else {
            rowLayout
        }
    }

    private var rowLayout: some View {
        HStack(spacing: AppSpacing.md) {
            portfolioPicker
                .layoutPriority(1)
            Spacer(minLength: AppSpacing.xs)
            // Same row as the picker, so nothing below moves when live data replaces it.
            if let label = viewModel.snapshotUpdatedLabel {
                snapshotStatus(label)
            }
            optionsButton
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private func stackedLayout(_ label: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            HStack(spacing: AppSpacing.md) {
                portfolioPicker
                    .layoutPriority(1)
                Spacer(minLength: AppSpacing.xs)
                optionsButton
            }
            snapshotStatus(label)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Left: portfolio switcher trigger

    /// Read-only while the rows are a saved snapshot and no live group list has arrived: the
    /// switcher could only list groups the store does not have.
    private var pickerIsReadOnly: Bool {
        viewModel.isShowingSnapshot && !viewModel.canEditPortfolio
    }

    private var portfolioPicker: some View {
        Button {
            toggle(.portfolio)
        } label: {
            HStack(spacing: AppSpacing.xs) {
                // The group the rows below describe — the snapshot's while it is shown.
                Text(viewModel.presentedPortfolio?.name ?? "Holdings")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                if !pickerIsReadOnly {
                    Image(systemName: "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textSecondary)
                        .rotationEffect(.degrees(activeMenu == .portfolio ? 180 : 0))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(pickerIsReadOnly)
        .anchorPreference(key: PortfolioHeaderMenuAnchorKey.self, value: .bounds) {
            [.portfolio: $0]
        }
    }

    // MARK: - Snapshot status ("Updated <time>")

    /// The saved snapshot's age, a mini spinner while the live load runs, and — once that
    /// load has failed — "Couldn't refresh" with a Retry. Snapshot prices must never pass for
    /// live ones, so this shows whenever a snapshot is on screen.
    @ViewBuilder
    private func snapshotStatus(_ label: String) -> some View {
        if viewModel.snapshotRefreshFailed {
            Button {
                Task { await viewModel.refresh() }
            } label: {
                VStack(alignment: .trailing, spacing: 0) {
                    Text(label)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                    HStack(spacing: AppSpacing.xxs) {
                        Image(systemName: "arrow.clockwise")
                            .font(AppTypography.iconXS)
                        Text("Couldn't refresh · Retry")
                            .font(AppTypography.caption)
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
                .padding(.vertical, AppSpacing.xxs)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Couldn't refresh. \(label).")
            .accessibilityHint("Retries loading your holdings")
        } else {
            HStack(spacing: AppSpacing.xxs) {
                if viewModel.isRefreshingHoldings {
                    ProgressView()
                        .controlSize(.mini)
                } else {
                    Image(systemName: "clock")
                        .font(AppTypography.iconXS)
                }
                Text(label)
                    .font(AppTypography.caption)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }
            .foregroundColor(AppColors.textSecondary)
            .accessibilityElement(children: .combine)
        }
    }

    // MARK: - Right: management trigger

    private var optionsButton: some View {
        MoreOptionsButton { toggle(.options) }
            .anchorPreference(key: PortfolioHeaderMenuAnchorKey.self, value: .bounds) {
                [.options: $0]
            }
    }

    private func toggle(_ menu: PortfolioHeaderMenu) {
        // Plain assignment — no `withAnimation`, so the popup appears instantly.
        activeMenu = (activeMenu == menu) ? nil : menu
    }
}

// MARK: - Floating popup overlay (hosted by the parent scroll view)

struct PortfolioHeaderMenuOverlay: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Binding var activeMenu: PortfolioHeaderMenu?
    let anchors: [PortfolioHeaderMenu: Anchor<CGRect>]
    let proxy: GeometryProxy

    private let menuWidth: CGFloat = 196
    private let edgeInset: CGFloat = 8

    var body: some View {
        if let menu = activeMenu, let anchor = anchors[menu] {
            let rect = proxy[anchor]
            ZStack(alignment: .topLeading) {
                // Full-screen, near-invisible scrim: tap anywhere to dismiss.
                Color.black.opacity(0.001)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .contentShape(Rectangle())
                    .onTapGesture { activeMenu = nil }

                panel(for: menu)
                    .frame(width: menuWidth, alignment: .leading)
                    .padding(.vertical, AppSpacing.xs)
                    // Native iOS 26 Liquid Glass. The material itself supplies the
                    // translucent frost, the rounded shape, the adaptive edge
                    // highlight, AND the floating-layer shadow — so there's no
                    // manual fill / stroke / clipShape / shadow (an opaque fill
                    // would defeat the translucency; a manual shadow would double
                    // up). It also honors Reduce Transparency automatically.
                    .glassPanel(cornerRadius: AppCornerRadius.large)
                    .offset(x: xOffset(for: menu, rect: rect), y: rect.maxY + AppSpacing.xs)
            }
            // No insertion/removal transition — the popup is on or off, instantly.
            .transition(.identity)
        }
    }

    /// Left chip opens leading-aligned; the "..." button opens trailing-aligned.
    /// Clamp so the panel never spills past a screen edge.
    private func xOffset(for menu: PortfolioHeaderMenu, rect: CGRect) -> CGFloat {
        let desired = menu == .options ? rect.maxX - menuWidth : rect.minX
        let maxX = proxy.size.width - menuWidth - edgeInset
        return min(max(edgeInset, desired), max(edgeInset, maxX))
    }

    @ViewBuilder
    private func panel(for menu: PortfolioHeaderMenu) -> some View {
        switch menu {
        case .portfolio: portfolioPanel
        case .options:   optionsPanel
        }
    }

    // MARK: Portfolio switcher panel

    private var portfolioPanel: some View {
        VStack(spacing: 0) {
            ForEach(viewModel.portfolioStore.portfolios) { portfolio in
                PortfolioMenuRow(
                    title: portfolio.name,
                    isChecked: portfolio.id == viewModel.portfolioStore.activePortfolioId
                ) {
                    viewModel.setActivePortfolio(portfolio.id)
                    activeMenu = nil
                }
            }

            if !viewModel.portfolioStore.portfolios.isEmpty {
                PortfolioMenuDivider()
            }

            // Group edits are built from the LIVE list: disabled until it has arrived.
            PortfolioMenuRow(
                title: "New Portfolio", systemImage: "plus",
                isDisabled: !viewModel.canEditPortfolio
            ) {
                viewModel.openNewPortfolioSheet()
                activeMenu = nil
            }
            PortfolioMenuRow(
                title: "Edit Portfolios", systemImage: "pencil",
                isDisabled: !viewModel.canEditPortfolio
            ) {
                viewModel.openEditPortfolioSheet()
                activeMenu = nil
            }
        }
    }

    // MARK: Sort + management panel

    private var optionsPanel: some View {
        VStack(spacing: 0) {
            Text("Sort By")
                .font(AppTypography.caption).fontWeight(.semibold)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppSpacing.md)
                .padding(.top, AppSpacing.sm)
                .padding(.bottom, AppSpacing.xxs)

            ForEach(AssetSortOption.allCases, id: \.self) { option in
                PortfolioMenuRow(
                    title: option.displayName,
                    isChecked: option == viewModel.sortOption
                ) {
                    viewModel.selectSortOption(option)
                    activeMenu = nil
                }
            }

            PortfolioMenuRow(
                title: viewModel.sortAscending ? "Descending" : "Ascending",
                systemImage: viewModel.sortAscending ? "arrow.down" : "arrow.up"
            ) {
                viewModel.toggleSort()
                activeMenu = nil
            }

            PortfolioMenuDivider()

            PortfolioMenuRow(
                title: "Manage Tickers",
                systemImage: "line.3.horizontal",
                isDisabled: viewModel.portfolioStore.activePortfolio == nil
            ) {
                viewModel.openManageTickersSheet()
                activeMenu = nil
            }
        }
    }
}

// MARK: - Compact popup row

private struct PortfolioMenuRow: View {
    let title: String
    var systemImage: String? = nil
    var isChecked: Bool = false
    var isDisabled: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.sm) {
                // Reserved leading slot keeps every label aligned, whether the
                // row shows a checkmark, an action icon, or nothing.
                ZStack {
                    if isChecked {
                        Image(systemName: "checkmark")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(AppColors.primaryBlue)
                    } else if let systemImage {
                        Image(systemName: systemImage)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
                .frame(width: 16)

                Text(title)
                    .font(.system(size: 14))
                    .foregroundColor(isDisabled ? AppColors.textMuted : AppColors.textPrimary)

                Spacer(minLength: 0)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, 7)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isDisabled)
    }
}

private struct PortfolioMenuDivider: View {
    var body: some View {
        Rectangle()
            .fill(AppColors.textPrimary.opacity(0.08))
            .frame(height: 0.5)
            .padding(.vertical, AppSpacing.xs)
    }
}

#Preview {
    PortfolioHeaderBar(viewModel: TrackingViewModel(), activeMenu: .constant(nil))
        .padding(.vertical)
        .background(AppColors.background)
}
