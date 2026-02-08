//
//  TickerReportView.swift
//  ios
//
//  Screen: Full stock report view (Buffett Agent style analysis)
//  Combines all report organisms into a scrollable report layout
//

import SwiftUI

struct TickerReportView: View {
    @StateObject private var viewModel: TickerReportViewModel
    @Environment(\.dismiss) private var dismiss

    init(ticker: String) {
        _viewModel = StateObject(wrappedValue: TickerReportViewModel(ticker: ticker))
    }

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            if viewModel.isLoading {
                loadingView
            } else if let report = viewModel.reportData {
                reportContent(report)
            }
        }
        .navigationBarHidden(true)
    }

    // MARK: - Loading View

    private var loadingView: some View {
        VStack(spacing: AppSpacing.lg) {
            ProgressView()
                .tint(AppColors.primaryBlue)
                .scaleEffect(1.2)
            Text("Loading report...")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    // MARK: - Report Content

    private func reportContent(_ report: TickerReportData) -> some View {
        ZStack(alignment: .bottom) {
            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    // Header
                    headerSection(report)

                    // Agent Badge + Score
                    agentScoreSection(report)

                    // Executive Summary
                    ReportExecutiveSummaryCard(
                        summaryText: report.executiveSummaryText,
                        bullets: report.executiveSummaryBullets
                    )
                    .padding(.horizontal, AppSpacing.lg)

                    // Key Vitals
                    ReportKeyVitalsSection(vitals: report.keyVitals)

                    // Core Thesis
                    ReportCoreThesisSection(thesis: report.coreThesis)

                    // Deep Dive Modules
                    deepDiveModulesSection(report)

                    // Critical Factors
                    ReportCriticalFactorsSection(factors: report.criticalFactors)

                    // View Detailed Analysis button
                    detailedAnalysisButton

                    // Disclaimer
                    disclaimerSection(report)

                    // Bottom padding for chat bar
                    Spacer()
                        .frame(height: 80)
                }
                .padding(.top, AppSpacing.sm)
            }
            .refreshable {
                await viewModel.refresh()
            }

            // Floating chat bar
            ReportChatBar(onTapped: viewModel.chatWithReport)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.bottom, AppSpacing.sm)
        }
    }

    // MARK: - Header Section

    private func headerSection(_ report: TickerReportData) -> some View {
        VStack(spacing: AppSpacing.xs) {
            ReportHeaderBar(
                companyName: report.companyName,
                ticker: report.symbol,
                exchange: report.exchange,
                onBack: { dismiss() },
                onShare: viewModel.shareTapped
            )

            Text(report.liveDate)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppSpacing.lg)
        }
    }

    // MARK: - Agent + Score Section

    private func agentScoreSection(_ report: TickerReportData) -> some View {
        VStack(spacing: AppSpacing.lg) {
            ReportAgentBadge(agent: report.agent)

            ReportScoreGauge(
                score: report.qualityRating.score,
                maxScore: report.qualityRating.maxScore,
                label: report.qualityRating.label
            )
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.sm)
    }

    // MARK: - Deep Dive Modules

    private func deepDiveModulesSection(_ report: TickerReportData) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Deep Dive Modules")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.bottom, AppSpacing.md)

            VStack(spacing: 0) {
                ForEach(viewModel.deepDiveModules) { module in
                    ReportDeepDiveSection(
                        module: module,
                        isExpanded: viewModel.isSectionExpanded(module.type),
                        onToggle: { viewModel.toggleSection(module.type) },
                        content: AnyView(deepDiveContent(for: module.type, report: report))
                    )
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            .padding(.horizontal, AppSpacing.lg)
        }
    }

    // MARK: - Deep Dive Content Router

    @ViewBuilder
    private func deepDiveContent(for type: DeepDiveModuleType, report: TickerReportData) -> some View {
        switch type {
        case .recentPriceMovement:
            placeholderContent("Price chart and recent movement analysis")
        case .fundamentalsGrowth:
            ReportFundamentalsSection(
                metrics: report.fundamentalMetrics,
                assessment: report.overallAssessment
            )
        case .futureForecast:
            ReportFutureForecastSection(forecast: report.revenueForecast)
        case .insiderManagement:
            ReportInsiderSection(
                insiderData: report.insiderData,
                management: report.keyManagement
            )
        case .moatCompetition:
            placeholderContent("Competitive advantage and moat analysis")
        case .macroGeopolitical:
            placeholderContent("Macro-economic factors and geopolitical risks")
        case .wallStreetConsensus:
            ReportWallStreetSection(consensus: report.wallStreetConsensus)
        }
    }

    private func placeholderContent(_ text: String) -> some View {
        Text(text)
            .font(AppTypography.subheadline)
            .foregroundColor(AppColors.textMuted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, AppSpacing.md)
    }

    // MARK: - View Detailed Analysis Button

    private var detailedAnalysisButton: some View {
        Button(action: viewModel.viewDetailedAnalysis) {
            HStack(spacing: AppSpacing.sm) {
                Text("View Detailed Analysis")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)

                Image(systemName: "arrow.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Disclaimer

    private func disclaimerSection(_ report: TickerReportData) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Disclaimer")
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.textSecondary)

            Text(report.disclaimerText)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .lineSpacing(3)
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        TickerReportView(ticker: "ORCL")
    }
    .preferredColorScheme(.dark)
}
