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
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Severity icon
            Image(systemName: factor.severity.iconName)
                .font(.system(size: 16))
                .foregroundColor(factor.severity.color)
                .frame(width: 24, height: 24)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(factor.title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(factor.description)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(2)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
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
