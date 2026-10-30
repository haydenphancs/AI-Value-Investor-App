//
//  EarningsLiveResultsCard.swift
//  ios
//
//  Organism: one report's results against consensus — the top of the Earnings Alert sheet and
//  the whole of `EarningsLiveScreen` (a tapped live-results push). Owner request 2026-10-29:
//  "the result will show right away … compare results for estimate vs actual … whatever comes
//  out first will be shown right away … red or green for miss or beat".
//
//  Each metric row stands alone: Revenue can be in while EPS still reads "Waiting…". While the
//  report is LIVE the card polls its own endpoint (`EarningsLiveViewModel`); it opens on the
//  Tracking card's own figures (`fallback`) so it is never blank while the first answer loads.
//
//  Honesty rules, each from the server (`earnings_live_service`), rendered as given:
//    * a figure from the company's own release is marked "Preliminary" with a link to the
//      filing, until FMP's figure replaces it;
//    * a GAAP EPS is labelled GAAP and never coloured — the consensus is adjusted;
//    * a figure held by a sanity guard reads "Confirming…", never as a number.
//

import SwiftUI

struct EarningsLiveResultsCard: View {
    @StateObject private var viewModel: EarningsLiveViewModel
    /// The Tracking card's own figures — what is shown until the first answer arrives.
    private let fallback: AppAlert.EarningsData?
    @State private var browserLink: BrowserLink?

    init(ticker: String, reportDate: String?, fallback: AppAlert.EarningsData? = nil) {
        _viewModel = StateObject(wrappedValue: EarningsLiveViewModel(ticker: ticker,
                                                                    reportDate: reportDate))
        self.fallback = fallback
    }

    // MARK: Unified reads (live answer first, then the card's figures)

    private var live: EarningsLiveResponse? { viewModel.live }

    private var status: EarningsLiveStatus {
        live?.liveStatus ?? fallback?.status ?? .upcoming
    }

    private var eps: EarningsMetricOutcome {
        live?.epsOutcome ?? fallback?.epsOutcome
            ?? EarningsMetricOutcome(estimate: nil, actual: nil, surprisePct: nil)
    }

    private var revenue: EarningsMetricOutcome {
        live?.revenueOutcome ?? fallback?.revenueOutcome
            ?? EarningsMetricOutcome(estimate: nil, actual: nil, surprisePct: nil)
    }

    private var isPreliminary: Bool { live?.preliminary ?? fallback?.isPreliminary ?? false }

    private var checkedAt: Date? { live?.checkedDate ?? fallback?.checkedAt }

    private var companyName: String {
        let name = live?.companyName ?? fallback?.companyName ?? ""
        return name.isEmpty ? viewModel.ticker : name
    }

    // MARK: Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            header

            if status == .notScheduled {
                Text("No earnings report for \(viewModel.ticker) in the next two weeks.")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            } else {
                metricRow(title: "Revenue", outcome: revenue, format: EarningsFigureFormat.revenue)
                metricRow(title: eps.isGAAP ? "EPS (GAAP)" : "EPS (adjusted)", outcome: eps,
                          format: EarningsFigureFormat.eps)

                if isPreliminary { preliminaryNote }
                if let yearAgo = live?.yearAgo { yearAgoBlock(yearAgo) }
                if let quarters = live?.quarterResults, !quarters.isEmpty {
                    lastQuartersBlock(quarters)
                }
                if let guidance = live?.guidance, !guidance.isEmpty { guidanceBlock(guidance) }

                footnote
            }

            if viewModel.loadFailed && fallback == nil {
                InlineRetryNotice(
                    message: "Couldn't load the latest results.",
                    onRetry: { viewModel.start() }
                )
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .onAppear { viewModel.start() }
        .onDisappear { viewModel.stop() }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            viewModel.stop()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            viewModel.start()
        }
        .inAppBrowser(link: $browserLink)
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.sm) {
                Text("Results vs. estimates")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Spacer(minLength: 0)
                if status == .live {
                    LiveBadge()
                }
            }
            Text(statusLine)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .accessibilityElement(children: .combine)
    }

    private var statusLine: String {
        switch status {
        case .live:
            if let checkedAt {
                return "Checking for results · updated \(Self.relative(checkedAt))"
            }
            return "Checking for results as they come out"
        case .reported:
            if let accepted = live?.acceptedDate {
                return "Released \(EarningsLiveDates.etTime(accepted))"
            }
            return "Reported"
        case .awaiting:
            return "Results aren't in yet"
        case .upcoming:
            return "Results will appear here the moment they're out"
        case .notScheduled:
            return "Nothing scheduled"
        }
    }

    private static func relative(_ date: Date) -> String {
        let seconds = max(0, Date().timeIntervalSince(date))
        if seconds < 60 { return "just now" }
        let minutes = Int(seconds / 60)
        if minutes < 60 { return "\(minutes) min ago" }
        return "\(minutes / 60) h ago"
    }

    // MARK: Metric rows

    private func metricRow(title: String, outcome: EarningsMetricOutcome,
                           format: (Double) -> String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            HStack(alignment: .firstTextBaseline) {
                Text(title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Spacer(minLength: AppSpacing.sm)
                outcomeLabel(outcome)
            }
            HStack(spacing: AppSpacing.lg) {
                figure(label: "Estimate", value: outcome.estimate.map(format) ?? "—",
                       color: AppColors.textSecondary)
                figure(label: "Actual", value: actualText(outcome, format: format),
                       color: outcome.hasLanded ? outcome.color : AppColors.textMuted,
                       emphasized: outcome.hasLanded)
                Spacer(minLength: 0)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium, style: .continuous)
                .fill(AppColors.cardBackgroundLight)
        )
        .accessibilityElement(children: .combine)
    }

    private func actualText(_ outcome: EarningsMetricOutcome,
                            format: (Double) -> String) -> String {
        if let actual = outcome.actual { return format(actual) }
        return outcome.isChecking ? "Confirming…" : "Waiting…"
    }

    @ViewBuilder
    private func outcomeLabel(_ outcome: EarningsMetricOutcome) -> some View {
        if outcome.isGAAP, outcome.hasLanded {
            Text("Not comparable")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)
        } else if let word = outcome.word {
            HStack(spacing: 3) {
                if let arrow = outcome.arrowSymbol {
                    Image(systemName: arrow)
                        .font(.system(size: 9, weight: .bold))
                }
                Text([word, outcome.surpriseText].compactMap { $0 }.joined(separator: " "))
                    .font(AppTypography.captionEmphasis)
            }
            .foregroundColor(outcome.color)
        } else if !outcome.hasLanded, status == .live {
            ProgressView()
                .controlSize(.small)
        }
    }

    private func figure(label: String, value: String, color: Color,
                        emphasized: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Text(value)
                .font(emphasized ? AppTypography.bodyEmphasis : AppTypography.body)
                .foregroundColor(color)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
    }

    // MARK: Preliminary

    private var preliminaryNote: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: "doc.text")
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.caution)
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(preliminaryText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let url = live?.filingURL {
                    Button {
                        openExternal(url, into: &browserLink)
                    } label: {
                        Text("View the filing")
                            .font(AppTypography.captionEmphasis)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var preliminaryText: String {
        var text = "Preliminary · read from \(companyName)'s own press release (SEC 8-K)"
        if let accepted = live?.acceptedDate {
            text += ", filed \(EarningsLiveDates.etTime(accepted))"
        }
        return text + ". It updates when FMP confirms the figures."
    }

    // MARK: Extras

    private func yearAgoBlock(_ yearAgo: EarningsLiveYearAgoDTO) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(yearAgo.quarter.map { "Vs. the same quarter last year (\($0))" }
                 ?? "Vs. the same quarter last year")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            if let rev = yearAgo.revenue {
                yearAgoLine(label: "Revenue", then: EarningsFigureFormat.revenue(rev),
                            growth: yearAgo.revenueGrowthPct)
            }
            if let eps = yearAgo.eps {
                yearAgoLine(label: "EPS", then: EarningsFigureFormat.eps(eps),
                            growth: yearAgo.epsGrowthPct)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func yearAgoLine(label: String, then: String, growth: Double?) -> some View {
        HStack {
            Text(label)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
            Spacer()
            Text("\(then) a year ago")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            if let growth {
                Text(EarningsFigureFormat.growth(growth))
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(growth >= 0 ? AppColors.gain : AppColors.loss)
            }
        }
    }

    private func lastQuartersBlock(
        _ quarters: [(quarter: String, result: EarningsQuarterResult, surprisePct: Double?)]
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Last \(quarters.count) quarters")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            HStack(spacing: 0) {
                ForEach(Array(quarters.enumerated()), id: \.offset) { _, q in
                    VStack(spacing: AppSpacing.xs) {
                        EarningsResultDot(result: q.result, size: 12)
                        Text(q.quarter)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                        Text(Self.quarterCaption(q.result, q.surprisePct))
                            .font(AppTypography.captionEmphasis)
                            .foregroundColor(q.result.dotColor)
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                    }
                    .frame(maxWidth: .infinity)
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }

    private static func quarterCaption(_ result: EarningsQuarterResult, _ pct: Double?) -> String {
        switch result {
        case .noEstimate, .pending: return "—"
        case .matched: return "In line"
        case .beat, .missed:
            guard let pct, pct.isFinite else { return result == .beat ? "Beat" : "Miss" }
            return EarningsQuarterData.surpriseText(pct, isMatch: false)
        }
    }

    private func guidanceBlock(_ guidance: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text("Outlook")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text(guidance)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("From the company's release")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .accessibilityElement(children: .combine)
    }

    private var footnote: some View {
        Text("EPS is adjusted (non-GAAP), the same basis as the consensus estimate. "
             + "Estimates and confirmed results from FMP.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

#Preview {
    ScrollView {
        EarningsLiveResultsCard(
            ticker: "INTC",
            reportDate: "2026-10-29",
            fallback: AppAlert.EarningsData(
                ticker: "INTC", companyName: "Intel", reportTime: nil,
                consensus: "EPS Est: $0.41 · Rev Est: $16.6B", day: 29, month: "OCT",
                status: .live, epsEstimate: 0.41, revenueEstimate: 16.6e9,
                revenueActual: 17.1e9, revenueSurprisePct: 3.01, isPreliminary: true
            )
        )
        .padding()
    }
    .background(AppColors.background)
}
