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
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Header: Company name + Status
                HStack(alignment: .top) {
                    HStack(spacing: AppSpacing.sm) {
                        // Company logo (FMP CDN on a white chip, initials fallback)
                        CompanyLogoView(ticker: report.ticker, size: 36)

                        VStack(alignment: .leading, spacing: AppSpacing.xs) {
                            Text(report.companyName)
                                .font(AppTypography.headingSmall)
                                .foregroundColor(AppColors.textPrimary)

                            Text(report.tickerAndIndustry)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                        ReportStatusBadge(status: report.status)

                        if report.status == .failed && report.isRefunded {
                            // Mirror the "ORCL • Software - Infrastructure"
                            // subtitle styling so the refund note reads as
                            // metadata, not a second status pill. Amount is
                            // sourced from the same constant the charger
                            // reads (AnalysisCost.standard), so retry will
                            // re-debit the matching number.
                            Text("Refunded \(AnalysisCost.standard.credits) credits")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
                }

                // Persona info with score gauge on the right (for ready status).
                // The date sits directly under the persona block (left column),
                // tucked into the gauge's vertical band — so there's no separate
                // bottom date row and the card stays compact.
                if report.status == .ready, let rating = report.rating {
                    HStack(spacing: AppSpacing.sm) {
                        // Left: Persona info + date underneath
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            HStack(spacing: AppSpacing.sm) {
                                PersonaIcon(
                                    persona: report.persona,
                                    size: 32,
                                    isSelected: true
                                )

                                VStack(alignment: .leading, spacing: 0) {
                                    Text(report.persona.rawValue)
                                        .font(AppTypography.labelSmall)
                                        .fontWeight(.semibold)
                                        .foregroundColor(AppColors.textPrimary)

                                    Text(report.persona.tagline)
                                        .font(AppTypography.caption)
                                        .foregroundColor(AppColors.textSecondary)
                                }
                            }

                            Text(report.formattedDate)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                        }

                        Spacer()

                        // Right: Score Gauge
                        ReportScoreGauge(
                            score: rating,
                            maxScore: 100,
                            label: "",
                            size: .small
                        )
                    }
                } else {
                    // Persona info without gauge (for processing/failed).
                    // For .failed, the Retry button lives at the trailing
                    // end of this row (below the Failed badge), not on a
                    // separate row underneath.
                    HStack(spacing: AppSpacing.sm) {
                        PersonaIcon(
                            persona: report.persona,
                            size: 32,
                            isSelected: true
                        )

                        VStack(alignment: .leading, spacing: 0) {
                            // While running, show "Buffett Agent" instead of the
                            // full name; failed keeps the full name.
                            Text(report.status == .processing
                                 ? report.persona.agentLabel
                                 : report.persona.rawValue)
                                .font(AppTypography.labelSmall)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.textPrimary)

                            Text(report.persona.tagline)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }

                        if report.status == .failed {
                            Spacer()
                            Button(action: {
                                onRetry?()
                            }) {
                                HStack(spacing: AppSpacing.xs) {
                                    Image(systemName: "arrow.clockwise")
                                        .font(AppTypography.iconXS).fontWeight(.semibold)
                                    Text("Retry Analysis")
                                        .font(AppTypography.labelSmall)
                                        .fontWeight(.semibold)
                                }
                                .foregroundColor(report.status.color)
                            }
                            .buttonStyle(PlainButtonStyle())
                        }
                    }

                    // Status-specific content for non-ready reports
                    switch report.status {
                    case .processing:
                        // Progress bar + live step text from current_step
                        VStack(alignment: .leading, spacing: AppSpacing.xs) {
                            ProgressBar(
                                progress: report.progress ?? 0,
                                color: AppColors.primaryBlue
                            )
                            if let step = report.currentStep, !step.isEmpty {
                                Text(step)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textSecondary)
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                            }
                        }

                    case .failed, .ready:
                        EmptyView()
                    }
                }

                // Date row for non-ready cards only (processing right-aligned,
                // failed left-aligned). Ready shows the date under the persona
                // block above, so it has no bottom row.
                if report.status != .ready {
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
