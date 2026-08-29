//
//  TickerReportView.swift
//  ios
//
//  Screen: Full stock report view (persona-styled analysis)
//  Combines all report organisms into a scrollable report layout
//

import SwiftUI

struct TickerReportView: View {
    @StateObject private var viewModel: TickerReportViewModel
    @Environment(\.dismiss) private var dismiss
    /// Keyed rather than `@Environment(AppState.self)`: this screen is presented from
    /// `fullScreenCover`s and from a `#Preview` with no injection, and the keyed entry has a
    /// default so those cannot trap. The root supplies the real one (`iosApp.swift`).
    @Environment(\.appState) private var appState

    // Overflow-menu UI state (••• menu actions)
    @State private var showDeleteConfirm: Bool = false
    /// Buy Credits, raised from the error view when the report failed for lack of credits.
    @State private var showBuyCreditsFromError = false
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

    /// Open the report a notification is about. See `TickerReportViewModel`'s matching init
    /// for why the persona is required and what happens without it.
    init(ticker: String, persona: String?, reportId: String?) {
        _viewModel = StateObject(wrappedValue: TickerReportViewModel(
            ticker: ticker, persona: persona, reportId: reportId
        ))
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
                // A failed pull-to-refresh, shown OVER the report rather than instead of
                // it. `body` renders `reportData` before `error`, so the ViewModel's
                // `error` field is unreachable once a report is loaded — this is the only
                // surface a refresh failure has.
                if let message = viewModel.transientError {
                    refreshErrorBanner(message)
                        .zIndex(1)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }

                // The reader must ENCLOSE the ScrollView. A `ScrollViewReader` nested
                // INSIDE one contains no scroll view for the proxy to scan, so `scrollTo`
                // compiles, runs, and silently does nothing — every reader in this app is
                // on the outside (BookCoreDetailView, ChatMessagesList, DailyScannersSection).
                ScrollViewReader { proxy in
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
                        deepDiveModulesSection(report, proxy: proxy)

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
                // A refresh that found no cached report asks BEFORE spending. The cost is
                // stated on the button, matching `GenerateAnalysisButton`'s "Uses N Credits" —
                // the app's established disclosure pattern. Nothing is charged until this tap.
                .alert("Report no longer cached", isPresented: $viewModel.needsPaidRegeneration) {
                    Button("Cancel", role: .cancel) {}
                    Button("Regenerate · \(AnalysisCost.standard.credits) credits") {
                        Task { await viewModel.regenerateForCredits() }
                    }
                } message: {
                    Text("This analysis has aged out of the cache. Generating a fresh one uses "
                         + "\(AnalysisCost.standard.credits) credits.")
                }
                }   // ScrollViewReader

                // Floating chat bar
                CaydexAIChatBar(
                    inputText: $viewModel.aiInputText,
                    placeholder: "Chat with the report...",
                    onSend: handleReportChatSend
                )
            }
        }
    }

    /// Dismissible banner for a refresh that failed while a report is on screen.
    private func refreshErrorBanner(_ message: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: "wifi.exclamationmark")
                .foregroundColor(AppColors.caution)
                .font(AppTypography.iconSmall)
            VStack(alignment: .leading, spacing: 2) {
                Text("Couldn't refresh")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Text(message)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Button {
                withAnimation { viewModel.transientError = nil }
            } label: {
                Image(systemName: "xmark")
                    .font(AppTypography.iconTiny)
                    .foregroundColor(AppColors.textMuted)
            }
            .buttonStyle(PlainButtonStyle())
            .accessibilityLabel("Dismiss")
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill(AppColors.cardBackgroundNested)
        )
        .padding(.horizontal, AppSpacing.lg)
        .frame(maxHeight: .infinity, alignment: .top)
        .accessibilityElement(children: .combine)
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

            // Age warning for older snapshots. The date line above is easy to skim
            // past; this states the consequence.
            if let (days, severity) = stalenessInfo(report) {
                StaleDataBanner(daysOld: days, severity: severity)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.md)
            }
        }
    }

    /// Soft warning past a week, strong past a month. A week avoids firing on a
    /// Friday close read on Monday, or across a long holiday weekend.
    private static let stalenessCautionDays = 7
    private static let stalenessStrongDays = 30

    /// Age of the report's data in whole days, with a severity — or nil when it is
    /// fresh, undated, or the date is in the future (a clock skew must not render a
    /// negative age).
    private func stalenessInfo(_ report: TickerReportData) -> (Int, StaleDataBanner.Severity)? {
        guard let iso = report.priceCloseDate,
              let date = Self.isoDateParser.date(from: iso) else { return nil }
        let cal = Self.utcCalendar
        let from = cal.startOfDay(for: date)
        let to = cal.startOfDay(for: Date())
        guard let days = cal.dateComponents([.day], from: from, to: to).day, days > 0 else {
            return nil
        }
        if days >= Self.stalenessStrongDays { return (days, .strong) }
        if days >= Self.stalenessCautionDays { return (days, .caution) }
        return nil
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
            // No disclaimer line here on purpose — `disclaimerSection` at the bottom of the
            // scroll already carries the full text plus the Details link, and repeating the
            // short form under the score read as duplication.
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.sm)
    }

    /// Put a just-collapsed module's own header back at the top of the viewport.
    ///
    /// Unanimated on purpose. An animated `scrollTo` is an in-flight scroll animation over a
    /// `contentSize` that is changing underneath it — the same family as the two freezes this
    /// app has already shipped (`DailyScannersSection`'s `.scrollPosition` deadlock and the
    /// `LazyVStack` hang on Home), and the collapse itself is instant, so a glide would only
    /// stream unfamiliar content past the reader.
    ///
    /// `withTransaction` REPLACES the ambient transaction rather than merging, so this stays
    /// unanimated even if someone later wraps the collapse in `withAnimation`.
    ///
    /// Called twice, following `BookCoreDetailView.scrollToTop`. The destination itself cannot
    /// move — collapsing removes height BELOW the header, so the header's content-space y is
    /// identical before and after — but the reachable offset can: the content just shrank, so
    /// near the end of the report the target may exceed the new maximum and be clamped. The
    /// synchronous call resolves against pre-collapse layout, the async one against settled
    /// layout. Because the destination is invariant, the retry can never fight the first call.
    private func scrollModuleHeaderToTop(_ id: DeepDiveModule.ID, proxy: ScrollViewProxy) {
        func jump() {
            var transaction = Transaction()
            transaction.animation = nil
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                proxy.scrollTo(id, anchor: .top)
            }
        }
        jump()
        DispatchQueue.main.async { jump() }
    }

    // MARK: - Deep Dive Modules

    private func deepDiveModulesSection(_ report: TickerReportData,
                                        proxy: ScrollViewProxy) -> some View {
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
                        isLast: index == modules.count - 1,
                        onCollapse: { scrollModuleHeaderToTop(module.id, proxy: proxy) }
                    ) {
                        deepDiveContent(for: module.type, report: report)
                    }
                    // The scroll anchor. Safe to attach here ONLY because
                    // `TickerReportViewModel.deepDiveModules` is a stored `let` — its
                    // `UUID`s are minted once. A computed property would hand out fresh
                    // ids on every render, and a changing `.id()` resets the child's
                    // `@State isExpanded`, so every module would silently re-collapse.
                    .id(module.id)
                }
            }
            // One rounded card (top + bottom curves) wrapping the whole stack,
            // matching the Bull/Bear case + Critical Factors cards. clipShape
            // rounds the first module's top and the last module's bottom; the
            // per-module dividers separate them inside.
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
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

            // The primary button follows the error, not the other way round.
            //
            // This was an unconditional "Retry", which is a dead end for the one error the
            // user can actually resolve: a 402 out-of-credits re-issues the same request and
            // 402s again, on the single screen that could have sold them the credits.
            // `.upgrade` sends them to Buy Credits instead — matching the routing in
            // `ErrorPresentationHost` and SYSTEM_DESIGN_GUIDELINES §9b.7.
            switch viewModel.errorAction {
            case .upgrade:
                errorActionButton(icon: "creditcard", title: "Get Credits") {
                    showBuyCreditsFromError = true
                }
            case .signIn:
                errorActionButton(icon: "person.crop.circle", title: "Sign In") {
                    // Via AppActions, not `appState` — the prompt is owned by the root and
                    // this screen can be inside a cover that cannot present a sheet itself.
                    AppActions.shared.requestSignIn(for: "AI research reports")
                }
            case .goBack:
                errorActionButton(icon: "chevron.left", title: "Go Back") { dismiss() }
            default:
                errorActionButton(icon: "arrow.clockwise", title: "Retry") {
                    viewModel.loadReport()
                }
            }

            // Suppressed when it would duplicate the primary button above.
            if viewModel.errorAction != .goBack {
                Button("Go Back") { dismiss() }
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .sheet(isPresented: $showBuyCreditsFromError) {
            BuyCreditsView()
                .environment(\.appState, appState)
        }
    }

    /// The error view's primary button. One shape, three destinations.
    private func errorActionButton(
        icon: String, title: String, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                Text(title)
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
    }

    // MARK: - Report Chat

    /// Seed the unified full-screen chat with this report's context, then present it.
    private func handleReportChatSend() {
        let text = viewModel.aiInputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        viewModel.aiInputText = ""

        // Context is fetched server-side by reference_id — no more shipping the
        // executive summary from the client on every chat.
        //
        // The report id is the THIRD segment, and it is what makes the answer match
        // the screen. Without it the backend can only look in `ticker_report_cache`,
        // which is CLOSE-ALIGNED: it holds nothing written before the most recent
        // weekday 18:00 ET. So opening a saved report the day after generating it and
        // tapping this bar resolved to no report context, and the assistant answered
        // from live market data under a placeholder that says "Chat with the report".
        // The backend scopes the lookup to the signed-in user, so sending the id
        // grants no access the caller did not already have.
        let symbol = viewModel.reportData?.symbol ?? ""
        let reference: String? = {
            guard !symbol.isEmpty else { return nil }
            let base = "\(symbol)|\(viewModel.personaKey)"
            guard let rid = viewModel.backendReportId, !rid.isEmpty else { return base }
            return "\(base)|\(rid)"
        }()

        chatViewModel.startNewConversation(
            firstMessage: text,
            stockId: viewModel.reportData?.symbol,
            contextType: .tickerReport,
            referenceId: reference
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

            // Tap-through to the full DisclaimersView, which previously hung off the
            // short notice under the score. `disclaimerText` already says the same
            // thing in full ("educational purposes only … not financial advice"), so
            // only the link comes down here — no repeated sentence.
            InlineDisclaimerNotice(text: "", linkLabel: "Details")
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        TickerReportView(preloadedReport: .sampleOracle)
    }
}
