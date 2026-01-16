import SwiftUI

/// Main Growth Section view for the Financial tab
/// Displays growth metrics with chart visualization
struct GrowthSectionView: View {

    // MARK: - Properties

    @StateObject private var viewModel: GrowthViewModel

    // MARK: - Initialization

    init(ticker: String) {
        _viewModel = StateObject(wrappedValue: GrowthViewModel(ticker: ticker))
    }

    /// Initializer with injected ViewModel (for testing/previews)
    init(viewModel: GrowthViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            // Header with title, info, and detail link
            GrowthHeaderView(
                onInfoTapped: { viewModel.showInfo() },
                onDetailTapped: { viewModel.navigateToDetail() }
            )

            // Metric selection tabs
            GrowthMetricTabsView(
                metrics: viewModel.availableMetrics,
                selectedMetric: $viewModel.selectedMetric
            )

            // Period toggle (Annual/Quarterly)
            GrowthPeriodToggleView(
                periods: viewModel.availablePeriods,
                selectedPeriod: $viewModel.selectedPeriod
            )

            // Chart area with loading state
            chartSection

            // YoY percentage row
            if !viewModel.dataPoints.isEmpty {
                GrowthYoYRowView(dataPoints: viewModel.dataPoints)
            }

            // Legend
            GrowthLegendView()
                .frame(maxWidth: .infinity)
        }
        .padding(16)
        .background(AppColors.backgroundCard)
        .cornerRadius(16)
        .sheet(isPresented: $viewModel.isShowingInfo) {
            GrowthInfoSheetView()
        }
    }

    // MARK: - Private Views

    @ViewBuilder
    private var chartSection: some View {
        if viewModel.isLoading {
            loadingView
        } else if let error = viewModel.errorMessage {
            errorView(message: error)
        } else if let data = viewModel.currentData {
            GrowthChartView(
                dataPoints: data.dataPoints,
                metricType: data.metricType,
                maxValue: data.maxValue
            )
            .transition(.opacity)
        } else {
            emptyView
        }
    }

    private var loadingView: some View {
        VStack {
            ProgressView()
                .progressViewStyle(CircularProgressViewStyle(tint: AppColors.accentBlue))
            Text("Loading growth data...")
                .font(AppFonts.caption1)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(height: 220)
        .frame(maxWidth: .infinity)
    }

    private func errorView(message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 32))
                .foregroundColor(AppColors.warning)

            Text(message)
                .font(AppFonts.subheadline)
                .foregroundColor(AppColors.textSecondary)

            Button("Retry") {
                Task {
                    await viewModel.refreshData()
                }
            }
            .font(AppFonts.subheadline)
            .foregroundColor(AppColors.accentBlue)
        }
        .frame(height: 220)
        .frame(maxWidth: .infinity)
    }

    private var emptyView: some View {
        VStack(spacing: 8) {
            Image(systemName: "chart.bar.xaxis")
                .font(.system(size: 32))
                .foregroundColor(AppColors.textTertiary)

            Text("No growth data available")
                .font(AppFonts.subheadline)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(height: 220)
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Preview

#Preview("Growth Section - Revenue") {
    ScrollView {
        GrowthSectionView(ticker: "AAPL")
            .padding()
    }
    .background(AppColors.backgroundPrimary)
}

#Preview("Growth Section - Dark Mode") {
    ScrollView {
        GrowthSectionView(ticker: "AAPL")
            .padding()
    }
    .background(AppColors.backgroundPrimary)
    .preferredColorScheme(.dark)
}
