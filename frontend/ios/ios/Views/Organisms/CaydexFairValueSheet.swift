//
//  CaydexFairValueSheet.swift
//  ios
//
//  Organism: the assumptions behind a Caydex Fair Value Estimate, what the number is and is
//  not, and why it can move. The methodology is documents/research/dcf-methodology-v1.md;
//  this sheet must say what the model does and nothing more (spec hard rule 5).
//

import SwiftUI

struct CaydexFairValueSheet: View {
    let estimate: CaydexFairValue
    let currentPrice: Double?
    var priceContext: String? = nil

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    summaryCard
                    if !estimate.assumptions.isEmpty {
                        assumptionsSection
                    }
                    explanationSection
                    if !estimate.notes.isEmpty {
                        notesSection
                    }
                    Text(Self.disclaimer)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Fair Value Estimate")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    static let disclaimer = "A model estimate for education, not a price target and not a recommendation to buy or sell. It is the same for every reader and does not consider your circumstances. Caydex is not a registered investment adviser."

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(CaydexFairValue.title)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            switch estimate.state {
            case .estimate:
                // Range first, the estimate as its middle mark — same order as the row.
                Text(CaydexFairValue.rangeLabel)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textMuted)
                Text(estimate.formattedRangeBounds ?? "—")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
                    .accessibilityLabel(estimate.rangeAccessibilityLabel ?? CaydexFairValue.rangeLabel)
                if let mid = estimate.formattedEstimate {
                    Text(mid)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                }
                if let gap = estimate.formattedGap(versus: currentPrice) {
                    Text(priceContext.map { "\(gap) (\($0))" } ?? gap)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                }
            case .refused(let reason):
                Text(reason)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(CaydexFairValue.subtitle)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    private var assumptionsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Assumptions")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            VStack(spacing: AppSpacing.sm) {
                ForEach(estimate.assumptions, id: \.self) { item in
                    HStack(alignment: .firstTextBaseline) {
                        Text(item.label)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: AppSpacing.md)
                        Text(item.value)
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                            .multilineTextAlignment(.trailing)
                    }
                }
            }
        }
    }

    private var explanationSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("How it works")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            ForEach(Self.explanation, id: \.self) { line in
                Text(line)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Notes")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            ForEach(estimate.notes, id: \.self) { note in
                Text("• \(note)")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Mirrors documents/research/dcf-methodology-v1.md §§1-2. Keep them in step.
    static let explanation: [String] = [
        "It adds up the free cash flow the company is expected to hand shareholders over the next 10 years, plus a value for the years after, and discounts it all back to today.",
        "The first years come from analysts' consensus forecasts of earnings, turned into cash flow with the company's own recent cash conversion. Later years are extended from that trend, slowing to a long-run growth rate.",
        "Stock-based pay is counted as a cost when the company reports it.",
        "The range shows the revenue-based cross-check and a discount rate one point higher or lower. Much of the value sits in the years after year 10 (the share is listed above), so small changes in the assumptions move it a lot.",
        "It changes when analysts revise their forecasts, when the company reports (the share count each quarter, the cash ratios each year), when the stock's beta or long-run interest rates change, and a little each day as the forecast years get closer. After each fiscal year ends, the forecast window moves forward gradually over about three months. The share price does not change the estimate itself; it is used only to check the share count and the debt load.",
        "Banks, insurers, REITs, utilities and companies whose data doesn't fit the model get no estimate rather than a misleading one.",
    ]
}

#Preview {
    CaydexFairValueSheet(estimate: .sampleEstimate, currentPrice: 341.07)
}
