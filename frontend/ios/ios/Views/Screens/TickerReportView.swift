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

    /// Preview-only initializer: skips async loading for faster previews.
    fileprivate init(preloadedReport: TickerReportData) {
        _viewModel = StateObject(wrappedValue: TickerReportViewModel(
            ticker: preloadedReport.symbol,
            preloadedReport: preloadedReport
        ))
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
            } else if let error = viewModel.error {
                errorView(error)
            }
        }
        .navigationBarHidden(true)
        .sheet(isPresented: $viewModel.showChatResponse) {
            chatResponseSheet
        }
    }

    // MARK: - Loading View

    private var loadingView: some View {
        VStack(spacing: AppSpacing.lg) {
            ProgressView()
                .tint(AppColors.primaryBlue)
                .scaleEffect(1.2)
            Text("Loading report...")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    // MARK: - Report Content

    private func reportContent(_ report: TickerReportData) -> some View {
        VStack(spacing: 0) {
            // Sticky Header
            headerSection(report)

            ZStack(alignment: .bottom) {
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
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
                CaydexAIChatBar(
                    inputText: $viewModel.aiInputText,
                    placeholder: "Chat with the report...",
                    onSend: viewModel.chatWithReport
                )
            }
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
        LazyVStack(alignment: .leading, spacing: 0) {
            Text("Deep Dive Modules")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.bottom, AppSpacing.md)

            LazyVStack(spacing: 0) {
                ForEach(viewModel.deepDiveModules) { module in
                    ReportDeepDiveSection(
                        module: module,
                        isExpanded: viewModel.isSectionExpanded(module.type),
                        onToggle: { viewModel.toggleSection(module.type) }
                    ) {
                        deepDiveContent(for: module.type, report: report)
                    }
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
            ReportPriceMovementSection(data: report.priceAction)
        case .revenueEngine:
            ReportRevenueEngineSection(data: report.revenueEngine)
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
            ReportMoatCompetitionSection(data: report.moatCompetition)
        case .macroGeopolitical:
            ReportMacroGeopoliticalSection(data: report.macroData)
        case .wallStreetConsensus:
            ReportWallStreetSection(consensus: report.wallStreetConsensus)
        }
    }

    // MARK: - View Detailed Analysis Button

    private var detailedAnalysisButton: some View {
        Button(action: viewModel.viewDetailedAnalysis) {
            HStack(spacing: AppSpacing.sm) {
                Text("View Detailed Analysis")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)

                Image(systemName: "arrow.right")
                    .font(AppTypography.iconXS).fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.lg)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Error View

    private func errorView(_ message: String) -> some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundColor(AppColors.textMuted)

            Text("Unable to Load Report")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xxl)

            Button(action: { viewModel.loadReport() }) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "arrow.clockwise")
                    Text("Retry")
                }
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.primaryBlue)
                .padding(.horizontal, AppSpacing.xxl)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .stroke(AppColors.primaryBlue, lineWidth: 1)
                )
            }

            Button("Go Back") { dismiss() }
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
        }
    }

    // MARK: - Chat Response Sheet

    private var chatResponseSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    // User question
                    if let question = viewModel.chatUserQuestion {
                        HStack {
                            Spacer()
                            Text(question)
                                .font(AppTypography.bodySmall)
                                .foregroundColor(.white)
                                .padding(.horizontal, AppSpacing.lg)
                                .padding(.vertical, AppSpacing.md)
                                .background(AppColors.primaryBlue)
                                .cornerRadius(AppCornerRadius.large)
                        }
                        .padding(.horizontal, AppSpacing.lg)
                    }

                    // AI response or loading
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        Image(systemName: "sparkles")
                            .font(AppTypography.iconSmall)
                            .foregroundColor(AppColors.primaryBlue)
                            .padding(.top, 2)

                        if viewModel.isChatLoading {
                            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                                ProgressView()
                                    .tint(AppColors.primaryBlue)
                                Text("Thinking...")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        } else if let response = viewModel.chatResponse {
                            Text(response)
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.textPrimary)
                                .lineSpacing(4)
                                .textSelection(.enabled)
                        }

                        Spacer()
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
                .padding(.top, AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("AI Insight")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        viewModel.dismissChatResponse()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    // MARK: - Disclaimer

    private func disclaimerSection(_ report: TickerReportData) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Disclaimer")
                .font(AppTypography.labelSmallEmphasis)
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
        TickerReportView(preloadedReport: .sampleOracle)
    }
    .preferredColorScheme(.dark)
}
