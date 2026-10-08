//
//  HealthCheckSectionCard.swift
//  ios
//
//  Organism: Complete Health Check Section card for the Financial tab
//  Displays financial health metrics with gauges showing position vs sector averages
//

import SwiftUI

struct HealthCheckSectionCard: View {
    // MARK: - Properties

    let healthCheckData: HealthCheckSectionData
    let onDetailTapped: () -> Void
    /// The PEER lookup failed upstream (`degraded` holds "benchmarks"): the ratios are the
    /// company's own, but no "vs industry / vs sector" median backs them, so a muted line
    /// says so. Defaults to false, so a caller that does not pass it keeps today's card.
    var peerComparisonUnavailable: Bool = false

    // MARK: - State

    @State private var showInfoSheet: Bool = false

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, status badge, info icon, and detail link
            headerSection

            // The peer lookup failed: say why no metric shows a "vs" median.
            if peerComparisonUnavailable {
                PeerComparisonUnavailableNote()
                    .padding(.horizontal, AppSpacing.lg)
            }

            // Metric cards in horizontal scroll
            metricsSection
        }
        .padding(.vertical, AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            HealthCheckInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack(alignment: .center, spacing: AppSpacing.sm) {
            // Title
            Text("Health Check")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Status badge (e.g., "[2/4] Mix")
            HealthCheckStatusBadge(
                rating: healthCheckData.overallRating,
                passedCount: healthCheckData.passedCount,
                totalCount: healthCheckData.totalCount
            )

            // Info icon
            HealthCheckInfoIcon {
                showInfoSheet = true
            }

            Spacer()

            // The "Details" affordance is hidden: all six handlers in
            // TickerDetailViewModel are `print()` stubs — no detail screen
            // exists — so the button did nothing when tapped. The callback
            // parameter is intentionally kept so re-enabling is a one-line
            // change once the drill-down ships.
            // // Detail link
            // Button(action: onDetailTapped) {
            // Text("Details")
            // .font(AppTypography.bodySmallEmphasis)
            // .foregroundColor(AppColors.primaryBlue)
            // }
            // .buttonStyle(.plain)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Metrics Section

    private var metricsSection: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: AppSpacing.md) {
                ForEach(healthCheckData.metrics) { metric in
                    HealthCheckMetricCard(metric: metric)
                        .frame(width: 280)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.lg) {
                HealthCheckSectionCard(
                    healthCheckData: HealthCheckSectionData.sampleData,
                    onDetailTapped: {}
                )

                HealthCheckSectionCard(
                    healthCheckData: HealthCheckSectionData.sampleApple,
                    onDetailTapped: {}
                )

                // Negative equity: ROE shows "N/M" and is outside the [0/2] count.
                HealthCheckSectionCard(
                    healthCheckData: HealthCheckSectionData.sampleNegativeEquity,
                    onDetailTapped: {}
                )

                // `degraded: ["benchmarks"]`: no row carries a peer median, one muted note.
                HealthCheckSectionCard(
                    healthCheckData: HealthCheckSectionData.sampleNegativeEquity,
                    onDetailTapped: {},
                    peerComparisonUnavailable: true
                )
            }
            .padding()
        }
    }
}
