//
//  CaydexFairValueRow.swift
//  ios
//
//  Molecule: the Caydex Fair Value Estimate as ONE row — value, range, the gap of the price
//  against the estimate, the "model estimate · not a price target" line, and a tap-through
//  to the assumptions. Used by the Analysis tab's Valuation card and the report's Wall
//  Street card, so both say exactly the same thing.
//
//  ⚠️ Wording is pinned by backend/tests/test_ios_fair_value.py: never Undervalued /
//  Overvalued / Buy / Sell, and never a value without its range.
//

import SwiftUI

struct CaydexFairValueRow: View {
    let estimate: CaydexFairValue
    /// The price the gap is measured against: the LIVE header price on the Analysis tab,
    /// the price frozen with the report in a report.
    let currentPrice: Double?
    /// Appended to the gap line when the price is not live ("at report time").
    var priceContext: String? = nil

    @State private var showAssumptions = false

    var body: some View {
        Button {
            showAssumptions = true
        } label: {
            content
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows the model's assumptions")
        .sheet(isPresented: $showAssumptions) {
            CaydexFairValueSheet(estimate: estimate, currentPrice: currentPrice,
                                 priceContext: priceContext)
        }
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            HStack(alignment: .firstTextBaseline) {
                Text(CaydexFairValue.title)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                Spacer(minLength: AppSpacing.md)
                switch estimate.state {
                case .estimate:
                    Text(estimate.formattedValue ?? "—")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                case .refused:
                    Text("Not modelled")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                Image(systemName: "info.circle")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
            }

            switch estimate.state {
            case .estimate:
                if let range = estimate.formattedRange {
                    Text(range)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
                if let gap = estimate.formattedGap(versus: currentPrice) {
                    Text(priceContext.map { "\(gap) (\($0))" } ?? gap)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
                Text(CaydexFairValue.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            case .refused(let reason):
                Text(reason)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(spacing: AppSpacing.lg) {
            CaydexFairValueRow(
                estimate: CaydexFairValue(
                    state: .estimate(value: 229.53, low: 187.17, high: 269.29),
                    alternativeValue: 210.39,
                    assumptions: [.init(label: "Discount rate (cost of equity)", value: "8.75%")],
                    asOf: "2026-09-25"
                ),
                currentPrice: 341.07
            )
            CaydexFairValueRow(
                estimate: CaydexFairValue(state: .refused(
                    reason: "Banks, insurers and asset managers earn on their balance sheet, so a cash-flow model doesn't fit them."
                )),
                currentPrice: 300
            )
        }
        .padding()
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding()
    }
}
