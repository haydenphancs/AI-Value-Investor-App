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

    // MARK: - State

    @State private var showInfoSheet: Bool = false

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, status badge, info icon, and detail link
            headerSection

            // Metric cards in horizontal scroll
            metricsSection
        }
        .padding(.vertical, AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
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
                .font(AppTypography.title3)
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

            // Detail link
            Button(action: onDetailTapped) {
                Text("Detail")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .buttonStyle(.plain)
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
            }
            .padding()
        }
    }
}
