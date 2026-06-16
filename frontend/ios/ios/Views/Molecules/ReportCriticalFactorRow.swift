//
//  ReportCriticalFactorRow.swift
//  ios
//
//  Molecule: Individual critical factor warning row
//

import SwiftUI

struct ReportCriticalFactorRow: View {
    let factor: CriticalFactor

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Icon + title share ONE row (mirrors the "Insight" header), so the
            // description and Watch line below span the FULL card width with no
            // left gutter — instead of being indented past a tall icon column.
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: factor.severity.iconName)
                    .font(AppTypography.iconDefault)
                    .foregroundColor(factor.severity.color)
                Text(factor.title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(factor.description)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)

            // Forward-looking action — what to monitor next. Hidden when the
            // backend didn't produce one (older cached reports / fallback).
            if let watch = factor.watch, !watch.isEmpty {
                let watchLabel = Text("Watch: ")
                    .font(AppTypography.label).fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
                let watchText = Text(watch)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                Text("\(watchLabel)\(watchText)")
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, AppSpacing.xxs)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(TickerReportData.sampleOracle.criticalFactors) { factor in
            ReportCriticalFactorRow(factor: factor)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
