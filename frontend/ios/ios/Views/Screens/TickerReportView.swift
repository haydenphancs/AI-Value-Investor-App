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

    // Overflow-menu UI state (••• menu actions)
    @State private var showDeleteConfirm: Bool = false
    /// Stable token keying this screen's compact-mode request + audio overlay host registration.
    @State private var compactToken = UUID().uuidString
    /// Owns the chat conversation for this report so it resumes while the screen is open.
    @StateObject private var chatViewModel = ChatViewModel()
    @State private var showAIChat = false

    init(ticker: String) {
        _viewModel = StateObject(wrappedValue: TickerReportViewModel(ticker: ticker))
    }

    /// Init from a Reports-tab AnalysisReport. Carries the backend
    /// row ID + the persona used to generate the report so the view
    /// model can fetch the cached ticker_report_data instantly.
    init(report: AnalysisReport) {
        _viewModel = StateObject(wrappedValue: TickerReportViewModel(report: report))
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
        // Audio collapses to the top status island while this report screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        .sheet(item: $viewModel.pdfSheet) { mode in
            ReportPDFView(
                reportId: viewModel.pdfReportId ?? "",
                autoShare: mode == .share
            )
        }
        .alert("Delete this report?", isPresented: $showDeleteConfirm) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                Task {
                    let ok = await viewModel.deleteReport()
                    if ok { dismiss() }
                }
            }
        } message: {
            Text("This will permanently remove the report from your Reports tab.")
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
                    VStack(spacing: AppSpacing.xxl) {
                        // Agent Badge + Score
                        agentScoreSection(report)

                        // Executive Summary
                        ReportExecutiveSummaryCard(
                            summaryText: report.executiveSummaryText
                        )
                        .padding(.horizontal, AppSpacing.lg)

                        // Core Thesis
                        ReportCoreThesisSection(thesis: report.coreThesis)

                        // Deep Dive Modules
                        deepDiveModulesSection(report)

                        // Critical Factors
                        ReportCriticalFactorsSection(factors: report.criticalFactors)

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
                    onSend: handleReportChatSend
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
                currentPrice: report.wallStreetConsensus.currentPrice,
                onBack: { dismiss() },
                onShare: viewModel.shareTapped,
                onViewDetailedAnalysis: viewModel.viewDetailedAnalysis,
                onDelete: { showDeleteConfirm = true },
                canExportPDF: viewModel.canExportPDF
            )

            Text(closeDateLabel(report))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppSpacing.lg)
                // Breathing room so the close-date line isn't kissing the
                // separator / next section below.
                .padding(.bottom, AppSpacing.md)
        }
    }

    /// Header date label, formatted RENDER-TIME from the report's actual
    /// last-completed-close date (e.g. "Previous Close · Jun 16"). Same calendar
    /// year → "MMM d"; a past year (a report opened in a later year) →
    /// "MMM d, yyyy" so the data's age is explicit. Falls back to the baked
    /// `liveDate` string for legacy reports that predate `priceCloseDate`.
    private func closeDateLabel(_ report: TickerReportData) -> String {
        guard let iso = report.priceCloseDate,
              let date = Self.isoDateParser.date(from: iso) else {
            return report.liveDate
        }
        let cal = Self.utcCalendar
        let sameYear = cal.component(.year, from: date) == cal.component(.year, from: Date())
        let formatter = sameYear ? Self.monthDayFormatter : Self.monthDayYearFormatter
        return "Previous Close · \(formatter.string(from: date))"
    }

    private static let utcCalendar: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC") ?? .current
        return c
    }()

    private static func utcFormatter(_ fmt: String) -> DateFormatter {
        let f = DateFormatter()
        f.timeZone = TimeZone(identifier: "UTC")
        f.locale = Locale(identifier: "en_US_POSIX")  // stable English month abbr.
        f.dateFormat = fmt
        return f
    }
    private static let isoDateParser = utcFormatter("yyyy-MM-dd")
    private static let monthDayFormatter = utcFormatter("MMM d")
    private static let monthDayYearFormatter = utcFormatter("MMM d, yyyy")

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
        // Hide the Hidden Market Signals module when no congress / short-interest
        // data is available for this ticker.
        let modules = viewModel.deepDiveModules.filter { module in
            module.type != .hiddenMarketSignals || report.hiddenMarketSignals != nil
        }
        return VStack(alignment: .leading, spacing: 0) {
            Text("Deep Dive Modules")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.bottom, AppSpacing.md)

            VStack(spacing: 0) {
                ForEach(Array(modules.enumerated()), id: \.element.id) { index, module in
                    ReportDeepDiveSection(
                        module: module,
                        isLast: index == modules.count - 1
                    ) {
                        deepDiveContent(for: module.type, report: report)
                    }
                }
            }
            // One rounded card (top + bottom curves) wrapping the whole stack,
            // matching the Bull/Bear case + Critical Factors cards. clipShape
            // rounds the first module's top and the last module's bottom; the
            // per-module dividers separate them inside.
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
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
                assessment: report.overallAssessment,
                growthData: report.growthChart,
                profitabilityData: report.profitabilityMarginSeries
            )
        case .futureForecast:
            ReportFutureForecastSection(
                forecast: report.revenueForecast,
                ticker: report.symbol
            )
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
        case .hiddenMarketSignals:
            if let hms = report.hiddenMarketSignals {
                ReportHiddenMarketSignalsSection(data: hms)
            }
        }
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

    // MARK: - Report Chat

    /// Seed the unified full-screen chat with this report's context, then present it.
    private func handleReportChatSend() {
        let text = viewModel.aiInputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        viewModel.aiInputText = ""

        let report = viewModel.reportData
        let summary = String((report?.executiveSummaryText ?? "").prefix(800))
        let context = "The user is viewing an in-depth research report on \(report?.symbol ?? "this company"). Report summary: \(summary). Answer questions grounded in this report."

        chatViewModel.startNewConversation(
            firstMessage: text,
            stockId: report?.symbol,
            context: context
        )
        showAIChat = true
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
