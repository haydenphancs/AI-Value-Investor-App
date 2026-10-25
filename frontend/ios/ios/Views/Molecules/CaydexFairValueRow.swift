//
//  CaydexFairValueRow.swift
//  ios
//
//  Molecule: the Caydex Fair Value Estimate's header — RANGE FIRST (the owner's rule,
//  2026-09-26: "not only the exact price"), the estimate as the range's middle mark, the gap
//  of the price against the estimate, and a tap-through to the assumptions. The header names
//  the model ("– DCF Model"); the "not a price target · not a recommendation" line lives in
//  that sheet and the cards' disclaimers, not here (owner, 2026-10-25). Used by the Analysis tab's Valuation card and the report's
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
                Text(CaydexFairValue.rowTitle)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: AppSpacing.md)
                Image(systemName: "info.circle")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
            }

            switch estimate.state {
            case .estimate:
                // The RANGE is the headline; the estimate is its middle mark.
                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                    Text(CaydexFairValue.rowRangeLabel)
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textMuted)
                    // 14 pt, the size of the Estimate line under it (owner, 2026-10-25:
                    // "reduce the size of the range price", picked "14 pt").
                    Text(estimate.formattedRangeBounds ?? "—")
                        .font(AppTypography.dataMedium)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                        .accessibilityLabel(estimate.rangeAccessibilityLabel ?? CaydexFairValue.rangeLabel)
                }
                if let mid = estimate.formattedEstimate {
                    // The range's own colour, like the estimate dot and its chart label
                    // (owner, 2026-10-25). accentCyan is the TEXT twin of the pole's
                    // accentGraphic — a *Graphic token never inks text.
                    Text(mid)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.accentCyan)
                }
                if let gap = estimate.gapLine(versus: currentPrice, context: priceContext) {
                    Text(gap)
                        .font(AppTypography.label)
                }
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

extension CaydexFairValue {
    /// The gap line ("Price 79% above the estimate (at report time)") with its "N% above" words
    /// in loss ink when the price is above the estimate and gain ink when below (owner,
    /// 2026-10-25); the rest, and "Price in line with the estimate", in textSecondary. Shared by
    /// this row and `CaydexFairValueSheet` so both colour it the same way. nil when there is no
    /// gap to state.
    func gapLine(versus price: Double?, context: String? = nil) -> AttributedString? {
        guard let gap = formattedGap(versus: price) else { return nil }
        var line = AttributedString(context.map { "\(gap) (\($0))" } ?? gap)
        line.foregroundColor = AppColors.textSecondary
        if let emphasis = gapEmphasis(versus: price), let range = line.range(of: emphasis.words) {
            line[range].foregroundColor = emphasis.priceAbove ? AppColors.loss : AppColors.gain
        }
        return line
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
