//
//  ReportEarningsTimelineView.swift
//  ios
//
//  Screen (sheet): the "continuity of thought" earnings view opened from the
//  Future Forecast module. One yearly timeline of historical ACTUAL revenue +
//  EPS flowing into the analyst forecast, with an optional share-price overlay
//  (toggle). The forecast/EPS series come from the report payload
//  (revenueForecast.annualTimeline); the price is fetched lazily on open from
//  the existing /earnings endpoint (not carried in the report).
//

import SwiftUI

struct ReportEarningsTimelineView: View {
    let ticker: String
    let timeline: [RevenueProjection]   // gapless actuals -> forecast
    let analystCount: Int?              // analysts behind the nearest forecast year

    @Environment(\.dismiss) private var dismiss
    @State private var showPrice = true
    @State private var dailyPrices: [EarningsDailyPricePoint] = []
    @State private var didLoad = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    Text("Reported results flowing into the analyst forecast. Toggle Price to overlay the share price.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)

                    HStack {
                        Spacer()
                        EarningsPriceToggle(isEnabled: $showPrice)
                    }

                    EarningsTimelineChart(
                        timeline: timeline,
                        dailyPrices: dailyPrices,
                        showPrice: showPrice
                    )

                    legend

                    if let n = analystCount {
                        Text("Forecast: consensus of \(n) analyst\(n == 1 ? "" : "s").")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .frame(maxWidth: .infinity, alignment: .center)
                    }

                    Text("Solid bars are reported actuals; lighter bars from the dashed marker on are the analyst forecast. Scroll to see all years.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background.ignoresSafeArea())
            .navigationTitle("Earnings Timeline")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .task {
            guard !didLoad else { return }
            didLoad = true
            // Lazy, on-demand — price isn't in the report payload. Reuses the
            // cached /earnings endpoint; silent on failure (chart just omits the
            // price line).
            if let dto = try? await StockRepository.shared.getEarnings(ticker: ticker) {
                dailyPrices = dto.toDisplayModel().dailyPriceHistory
            }
        }
    }

    private enum LegendShape { case bar, dot, line }

    private var legend: some View {
        HStack(spacing: AppSpacing.lg) {
            legendItem(color: AppColors.primaryBlue, shape: .bar, label: "Revenue")
            legendItem(color: AppColors.accentYellow, shape: .dot, label: "EPS")
            if showPrice {
                legendItem(color: AppColors.accentCyan, shape: .line, label: "Price")
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func legendItem(color: Color, shape: LegendShape, label: String) -> some View {
        HStack(spacing: AppSpacing.xs) {
            Group {
                switch shape {
                case .bar: RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 12, height: 12)
                case .dot: Circle().fill(color).frame(width: 8, height: 8)
                case .line: RoundedRectangle(cornerRadius: 1).fill(color).frame(width: 14, height: 3)
                }
            }
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}
