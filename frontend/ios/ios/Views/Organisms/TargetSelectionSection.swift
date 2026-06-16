//
//  TargetSelectionSection.swift
//  ios
//
//  Organism: Target selection. Shows a tap-to-search button when no target
//  is chosen, or a "selected company" chip with an x-to-clear when one is.
//  Constraint: only one ticker at a time.
//

import SwiftUI

struct TargetSelectionSection: View {
    /// Currently selected company. `nil` → show the search-bar prompt.
    let selectedTarget: StockSearchResult?
    /// Fallback ticker text when `selectedTarget` is nil but a ticker was set
    /// from a non-search path (e.g. a trending analysis tap).
    let fallbackTicker: String

    var onTapSearch: (() -> Void)?
    var onClearTarget: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Your Target:")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            if let target = selectedTarget {
                selectedChip(
                    ticker: target.ticker,
                    companyName: target.companyName,
                    exchange: target.exchange
                )
            } else if !fallbackTicker.isEmpty {
                selectedChip(
                    ticker: fallbackTicker,
                    companyName: fallbackTicker,
                    exchange: nil
                )
            } else {
                searchPrompt
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Search prompt (no selection yet)

    private var searchPrompt: some View {
        Button(action: { onTapSearch?() }) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "magnifyingglass")
                    .font(AppTypography.iconDefault).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
                Text("Find a company…")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.medium)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: - Selected chip (one target at a time)

    private func selectedChip(ticker: String, companyName: String, exchange: String?) -> some View {
        HStack(spacing: AppSpacing.md) {
            // Ticker badge
            Text(ticker)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .fill(AppColors.primaryBlue.opacity(0.2))
                        .overlay(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .stroke(AppColors.primaryBlue.opacity(0.5), lineWidth: 1)
                        )
                )

            VStack(alignment: .leading, spacing: 2) {
                Text(companyName)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                if let exchange {
                    Text(exchange)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Button(action: { onClearTarget?() }) {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 22))
                    .foregroundColor(AppColors.textMuted)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Clear selected company")
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        TargetSelectionSection(
            selectedTarget: nil,
            fallbackTicker: ""
        )
        TargetSelectionSection(
            selectedTarget: StockSearchResult(
                ticker: "AAPL",
                companyName: "Apple Inc.",
                exchange: "NASDAQ",
                sector: nil,
                logoUrl: nil,
                type: "stock"
            ),
            fallbackTicker: ""
        )
        Spacer()
    }
    .padding(.top, AppSpacing.lg)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
