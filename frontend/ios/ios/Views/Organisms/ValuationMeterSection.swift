//
//  ValuationMeterSection.swift
//  ios
//
//  Organism: the Analysis tab's Valuation card — meter, the multiples that drive it, and
//  FMP's discounted-cash-flow value as ONE labelled row with honest states. Mirrors the
//  anatomy of `TechnicalAnalysisSection` / `SentimentAnalysisSection` (header → centred
//  meter → rows → tappable disclaimer).
//
//  ⚠️ The DCF is an intrinsic-value estimate as of today, not a price forecast, and a
//  mechanical one: FMP's single-stage model on trailing free cash flow read −59% on AAPL
//  and −87% on TER the day this shipped. It is therefore shown as "DCF model value" with
//  the gap against the LIVE price, a caveat beyond ±50%, "No DCF" for a loss-maker whose
//  model is negative, and nothing at all when FMP has no model. Never "fair value".
//
//  When the snapshot carries the Caydex Fair Value Estimate (backend DCF_ENABLED), that
//  row — `CaydexFairValueRow`, with its range and "not a price target" line — REPLACES the
//  FMP row; the backend then sends no `dcf` at all.
//

import SwiftUI

struct ValuationMeterSection: View {
    let snapshot: SnapshotItem
    /// The live header price the DCF gap is measured against (nil → no gap, no caveat).
    let currentPrice: Double?

    @State private var showInfoSheet: Bool = false

    static let dcfModelLabel = "DCF model value"
    static let dcfSubtitle = "FMP discounted-cash-flow model · not a price target"
    static let dcfCaveat = "The model extrapolates past free cash flow; fast growers often trade far above it."
    static let dcfNegativeCopy = "No DCF — negative free cash flow"

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            AnalysisSectionHeader(
                title: "Valuation",
                onAction: { showInfoSheet = true },
                iconType: .info
            )

            HStack {
                Spacer()
                ValuationMeter(rating: snapshot.rating)
                Spacer()
            }

            if !snapshot.metrics.isEmpty {
                multiplesRows
            }

            if let estimate = snapshot.caydexEstimate {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Divider().overlay(AppColors.divider)
                        .padding(.bottom, AppSpacing.xs)
                    CaydexFairValueRow(estimate: estimate, currentPrice: currentPrice)
                }
            } else if let dcf = snapshot.dcf {
                dcfRow(dcf)
            }

            // `.fairValue` copy: a model number beside a live price. Full width so the
            // two-line wrap stays centred under the meter (TestFlight E10).
            AnalysisDisclaimerText.fairValue
                .frame(maxWidth: .infinity)
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            ValuationInfoSheet(showsCaydexEstimate: snapshot.caydexEstimate != nil,
                               showsFmpDcf: snapshot.dcf != nil)
        }
    }

    // MARK: - Multiples

    private var multiplesRows: some View {
        VStack(spacing: AppSpacing.sm) {
            ForEach(Array(snapshot.metrics.enumerated()), id: \.offset) { _, metric in
                HStack(alignment: .firstTextBaseline) {
                    Text(metric.name)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: AppSpacing.md)
                    Text(metric.value)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                }
            }
        }
        .padding(.top, AppSpacing.xs)
    }

    // MARK: - DCF row

    @ViewBuilder
    private func dcfRow(_ dcf: DcfEstimate) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Divider().overlay(AppColors.divider)
                .padding(.bottom, AppSpacing.xs)

            switch dcf.status {
            case .ok:
                HStack(alignment: .firstTextBaseline) {
                    Text(Self.dcfModelLabel)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                    Spacer(minLength: AppSpacing.md)
                    HStack(spacing: AppSpacing.xs) {
                        Text(dcf.formattedValue ?? "—")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                        if let gap = dcf.formattedGap(versus: currentPrice) {
                            Text("· \(gap)")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                        }
                    }
                }
                Text(Self.dcfSubtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                if dcf.needsCaveat(versus: currentPrice) {
                    Text(Self.dcfCaveat)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            case .negativeCashFlow:
                HStack(alignment: .firstTextBaseline) {
                    Text(Self.dcfModelLabel)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                    Spacer(minLength: AppSpacing.md)
                    Text(Self.dcfNegativeCopy)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .multilineTextAlignment(.trailing)
                }
            case .unavailable:
                EmptyView()
            }
        }
        .accessibilityElement(children: .combine)
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                ValuationMeterSection(
                    snapshot: SnapshotItem(
                        category: .price,
                        rating: .weak,
                        metrics: [
                            SnapshotMetric(name: "P/E 1.35x sector avg 25", value: "33.80"),
                            SnapshotMetric(name: "EV/EBITDA 1.10x sector avg 18", value: "27.59"),
                        ],
                        dcf: DcfEstimate(status: .ok, value: 135.83, asOf: "2026-09-17")
                    ),
                    currentPrice: 332.41
                )
                ValuationMeterSection(
                    snapshot: SnapshotItem(
                        category: .price,
                        rating: .unavailable,
                        metrics: [],
                        dcf: DcfEstimate(status: .negativeCashFlow, value: nil, asOf: "2026-09-17")
                    ),
                    currentPrice: 2.02
                )
            }
            .padding()
        }
    }
}
