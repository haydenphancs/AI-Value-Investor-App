//
//  CaydexFairValueRow.swift
//  ios
//
//  Molecule: the Caydex Fair Value Estimate's header — RANGE FIRST (the owner's rule,
//  2026-09-26: "not only the exact price"), the estimate as the range's middle mark, the gap
//  of the price against the estimate, the "model estimate · not a price target" line, and a
//  tap-through to the assumptions. Used by the Analysis tab's Valuation card and the report's
//  "Valuation & Institutions" section, so both say exactly the same thing; the chart under
//  it is `CaydexFairValueRangeChart`.
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
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                Spacer(minLength: AppSpacing.md)
                Image(systemName: "info.circle")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
            }

            switch estimate.state {
            case .estimate:
                // The RANGE is the headline; the estimate is its middle mark.
                Text(CaydexFairValue.rangeLabel)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textMuted)
                Text(estimate.formattedRangeBounds ?? "—")
                    .font(AppTypography.dataTitle)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .accessibilityLabel(estimate.rangeAccessibilityLabel ?? CaydexFairValue.rangeLabel)
                if let mid = estimate.formattedEstimate {
                    Text(mid)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                }
                if let gap = estimate.formattedGap(versus: currentPrice) {
                    Text(priceContext.map { "\(gap) (\($0))" } ?? gap)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textSecondary)
                }
                Text(CaydexFairValue.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            case .refused(let reason):
                Text(CaydexFairValue.refusedHeadline)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
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
            CaydexFairValueRow(estimate: .sampleEstimate, currentPrice: 341.07)
            CaydexFairValueRow(estimate: .sampleRefused, currentPrice: 300)
        }
        .padding()
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .padding()
    }
}
