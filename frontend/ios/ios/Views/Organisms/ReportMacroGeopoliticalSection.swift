//
//  ReportMacroGeopoliticalSection.swift
//  ios
//
//  Organism: Macro-Economic & Geopolitical deep dive content.
//  Intelligence briefing design: DEFCON-style threat level bar,
//  severity-ranked risk-factor rows (top 3 + "show more"), and a
//  classified-style AI intelligence brief.
//

import SwiftUI

struct ReportMacroGeopoliticalSection: View {
    let data: ReportMacroData

    @State private var showAllFactors = false

    private let collapsedCount = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Threat Level Bar
            threatLevelSection

            // Risk factors (severity-ranked, expandable rows)
            if !data.riskFactors.isEmpty {
                riskFactorsSection
            }

            // Intelligence Brief
            intelligenceBriefSection
        }
    }

    // MARK: - Threat Level

    private var threatLevelSection: some View {
        // Just the DEFCON bar — the old AI headline was redundant now that
        // the individual risk factors are listed below it.
        ReportThreatLevelBar(level: data.overallThreatLevel)
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .stroke(data.overallThreatLevel.color.opacity(0.2), lineWidth: 1)
                    )
            )
    }

    // MARK: - Risk Factors

    // Most material first: highest severity, then highest impact.
    private var sortedFactors: [MacroRiskFactor] {
        data.riskFactors.sorted {
            if $0.severity.numericLevel != $1.severity.numericLevel {
                return $0.severity.numericLevel > $1.severity.numericLevel
            }
            return $0.impact > $1.impact
        }
    }

    private var riskFactorsSection: some View {
        let factors = sortedFactors
        let visible = showAllFactors ? factors : Array(factors.prefix(collapsedCount))
        let hiddenCount = factors.count - visible.count

        return VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Flat rows in the standard report-list style (matches Key
            // Management / Recent Transactions / Congress) — each row carries
            // its own hairline divider, no outer card.
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                ForEach(visible) { factor in
                    ReportRiskFactorCard(factor: factor)
                }
            }

            // Show more / less — only when there's something hidden.
            if factors.count > collapsedCount {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { showAllFactors.toggle() }
                } label: {
                    HStack(spacing: AppSpacing.xxs) {
                        Text(showAllFactors ? "Show less" : "Show \(hiddenCount) more")
                            .font(AppTypography.captionEmphasis)
                        Image(systemName: showAllFactors ? "chevron.up" : "chevron.down")
                            .font(AppTypography.iconTiny).fontWeight(.semibold)
                    }
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.xs)
                }
            }
        }
    }

    // MARK: - Intelligence Brief

    private var intelligenceBriefSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "sparkles.2")
                    .foregroundStyle(
                        LinearGradient(
                            colors: [.indigo],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .font(AppTypography.iconDefault).fontWeight(.semibold)

                Text("Insight")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundStyle(LinearGradient(
                        colors: [.indigo, .cyan],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ))
            }

            Text(data.intelligenceBrief)
                .font(AppTypography.label)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
    }
}

#Preview {
    ScrollView {
        ReportMacroGeopoliticalSection(
            data: TickerReportData.sampleOracle.macroData
        )
        .padding()
    }
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
