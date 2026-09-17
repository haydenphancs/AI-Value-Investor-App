//
//  ValuationInfoSheet.swift
//  ios
//
//  Molecule: what the Valuation Meter and the DCF row mean — and, as importantly, what
//  they do not. Mirrors `SentimentInfoSheet`.
//

import SwiftUI

struct ValuationInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    headerSection
                    meterSection
                    dcfSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Valuation")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "scalemass.fill")
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)
                Text("Valuation")
                    .font(AppTypography.titleCompact)
                    .foregroundColor(AppColors.textPrimary)
            }
            Text("Valuation asks what you pay for each dollar of earnings, sales and cash flow — and how that compares with similar companies. It is a starting point for judgment, not a verdict.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    private var meterSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("The Valuation Meter")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            VStack(spacing: AppSpacing.md) {
                row(label: "Expensive / Pricey", color: AppColors.bearish,
                    description: "Multiples run 20% or more above the sector median. Growth may justify it — or not.")
                row(label: "Fair", color: AppColors.caution,
                    description: "Within about 20% of the sector median on price-to-earnings, sales, book, free cash flow and EV/EBITDA.")
                row(label: "Cheap / Bargain", color: AppColors.bullish,
                    description: "Multiples sit well below peers. Check why: a discount can be an opportunity or a warning.")
            }
            Text("This is the same rating the Overview tab's Valuation card shows, so the two never disagree.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var dcfSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("The DCF model value")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
            Text("A discounted-cash-flow (DCF) value is an estimate of what the business is worth today — its projected future cash flows, discounted back at a required return. It is not a prediction of where the share price will go.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("The figure shown is a mechanical model built from past free cash flow. Fast-growing companies usually trade far above it, and a company with negative cash flow has no meaningful DCF value at all. Treat it as one reference point among several, never as a target.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.large).cardFill())
    }

    private func row(label: String, color: Color, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle().fill(color).frame(width: 10, height: 10)
                Text(label)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }
            Text(description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: AppCornerRadius.medium).cardFill())
    }
}

#Preview {
    ValuationInfoSheet()
}
