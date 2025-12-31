//
//  ReportCard.swift
//  ios
//
//  Molecule: Analysis report card showing status, persona, and details
//

import SwiftUI

struct ReportCard: View {
    let report: AnalysisReport
    var onTap: (() -> Void)?
    var onRetry: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Header: Company name + Status
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        Text(report.companyName)
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)

                        Text(report.tickerAndIndustry)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                        ReportStatusBadge(status: report.status)

                        if report.status == .failed && report.isRefunded {
                            Text("[Refunded]")
                                .font(AppTypography.caption)
                                .foregroundColor(report.status.color)
                        }
                    }
                }

                // Persona info
                HStack(spacing: AppSpacing.sm) {
                    PersonaIcon(
                        persona: report.persona,
                        size: 32,
                        isSelected: true
                    )

                    VStack(alignment: .leading, spacing: 0) {
                        Text(report.persona.rawValue)
                            .font(AppTypography.footnote)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.textPrimary)

                        Text(report.persona.tagline)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }

                // Status-specific content
                switch report.status {
                case .processing:
                    // Progress bar
                    ProgressBar(
                        progress: report.progress ?? 0,
                        color: AppColors.primaryBlue
                    )

                case .failed:
                    // Retry button
                    Button(action: {
                        onRetry?()
                    }) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 12, weight: .semibold))
                            Text("Retry Analysis")
                                .font(AppTypography.footnote)
                                .fontWeight(.semibold)
                        }
                        .foregroundColor(report.status.color)
                    }
                    .buttonStyle(PlainButtonStyle())

                case .ready:
                    // Star rating
                    HStack {
                        StarRatingView(rating: report.rating ?? 0)
                        Spacer()
                    }
                }

                // Date (for ready and failed)
                if report.status != .processing || report.status == .processing {
                    HStack {
                        if report.status == .processing {
                            Spacer()
                        }
                        Text(report.formattedDate)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                        if report.status != .processing {
                            Spacer()
                        }
                    }
                }
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(report.status == .processing)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            ForEach(AnalysisReport.mockReports) { report in
                ReportCard(report: report)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
