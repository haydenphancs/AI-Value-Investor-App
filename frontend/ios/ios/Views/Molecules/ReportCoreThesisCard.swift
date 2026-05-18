//
//  ReportCoreThesisCard.swift
//  ios
//
//  Molecule: Bull case or Bear case thesis card with bullet points
//

import SwiftUI

struct ReportCoreThesisCard: View {
    let isBullCase: Bool
    let bullets: [CoreThesisBullet]

    private var title: String {
        isBullCase ? "Bull Case" : "Bear Case"
    }

    private var iconName: String {
        isBullCase ? "arrow.up.forward.circle.fill" : "arrow.down.forward.circle.fill"
    }

    private var accentColor: Color {
        isBullCase ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: iconName)
                    .font(AppTypography.iconDefault)
                    .foregroundColor(accentColor)

                Text(title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Bullets
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(bullets) { bullet in
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Circle()
                            .fill(accentColor)
                            .frame(width: 5, height: 5)
                            .padding(.top, 6)

                        Text(bullet.text)
                            .font(AppTypography.label)
                            .foregroundColor(AppColors.textSecondary)
                            .lineSpacing(3)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        // Stretch to the container width so Bull and Bear cards always
        // match — otherwise the VStack sizes to its longest text line
        // and the shorter case (typically Bull) ends up narrower.
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    let sample = TickerReportData.sampleOracle
    VStack(spacing: AppSpacing.md) {
        ReportCoreThesisCard(isBullCase: true, bullets: sample.coreThesis.bullCase)
        ReportCoreThesisCard(isBullCase: false, bullets: sample.coreThesis.bearCase)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
