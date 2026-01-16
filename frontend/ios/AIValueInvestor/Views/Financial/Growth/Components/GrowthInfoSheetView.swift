import SwiftUI
import Charts

/// Educational sheet explaining growth metrics for value investing
struct GrowthInfoSheetView: View {

    // MARK: - Properties

    @Environment(\.dismiss) private var dismiss

    // MARK: - Body

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    overviewSection
                    metricsExplainerSection
                    howToReadChartSection
                    valueInvestingTipsSection
                }
                .padding()
            }
            .background(AppColors.backgroundPrimary)
            .navigationTitle("Understanding Growth")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.accentBlue)
                }
            }
        }
    }

    // MARK: - Private Views

    private var overviewSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("What is Growth?")

            Text("Growth measures how a company's financial metrics change over time. For value investors, sustainable growth at a reasonable price is key to finding undervalued opportunities.")
                .font(AppFonts.body)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    private var metricsExplainerSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionHeader("Key Metrics Explained")

            ForEach(GrowthMetricType.allCases) { metric in
                metricExplainerRow(metric: metric)
            }
        }
    }

    private func metricExplainerRow(metric: GrowthMetricType) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(metric.displayName)
                .font(AppFonts.headline)
                .foregroundColor(AppColors.textPrimary)

            Text(metric.description)
                .font(AppFonts.subheadline)
                .foregroundColor(AppColors.textSecondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.backgroundCard)
        .cornerRadius(12)
    }

    private var howToReadChartSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionHeader("Reading the Chart")

            VStack(alignment: .leading, spacing: 12) {
                chartLegendExplanation(
                    color: AppColors.chartValue,
                    title: "Blue Bars (Value)",
                    description: "Absolute value of the metric for each period"
                )

                chartLegendExplanation(
                    color: AppColors.chartYoY,
                    title: "Yellow Line (YoY)",
                    description: "Year-over-Year percentage change showing growth rate"
                )

                chartLegendExplanation(
                    color: AppColors.chartSectorAverage,
                    title: "Gray Dashed Line (Sector Average)",
                    description: "Industry benchmark for comparison"
                )
            }
            .padding()
            .background(AppColors.backgroundCard)
            .cornerRadius(12)

            // Sample mini chart
            sampleChartView
        }
    }

    private func chartLegendExplanation(color: Color, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(color)
                .frame(width: 12, height: 12)
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(AppFonts.subheadline)
                    .fontWeight(.medium)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppFonts.caption1)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }

    private var sampleChartView: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Example: Strong Growth Pattern")
                .font(AppFonts.caption1)
                .foregroundColor(AppColors.textTertiary)

            Chart {
                ForEach(sampleGrowthData, id: \.period) { data in
                    BarMark(
                        x: .value("Year", data.period),
                        y: .value("Value", data.value)
                    )
                    .foregroundStyle(AppColors.chartValue)
                    .cornerRadius(3)
                }
            }
            .chartXAxis {
                AxisMarks { _ in
                    AxisValueLabel()
                        .font(AppFonts.chartAxis)
                        .foregroundStyle(AppColors.textSecondary)
                }
            }
            .chartYAxis {
                AxisMarks { _ in
                    AxisValueLabel()
                        .font(AppFonts.chartAxis)
                        .foregroundStyle(AppColors.textTertiary)
                }
            }
            .frame(height: 120)
        }
        .padding()
        .background(AppColors.backgroundCard)
        .cornerRadius(12)
    }

    private var valueInvestingTipsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionHeader("Value Investing Tips")

            VStack(alignment: .leading, spacing: 12) {
                tipRow(
                    icon: "chart.line.uptrend.xyaxis",
                    title: "Consistent Growth",
                    description: "Look for companies with steady, predictable growth rather than volatile spikes."
                )

                tipRow(
                    icon: "arrow.up.right.circle",
                    title: "Growth vs. Price",
                    description: "High growth is only valuable if the stock price hasn't already priced it in."
                )

                tipRow(
                    icon: "chart.bar",
                    title: "Compare to Sector",
                    description: "A company growing faster than its sector average may have a competitive advantage."
                )

                tipRow(
                    icon: "exclamationmark.triangle",
                    title: "Watch for Declines",
                    description: "Consistent negative YoY may indicate fundamental problems or industry headwinds."
                )
            }
            .padding()
            .background(AppColors.backgroundCard)
            .cornerRadius(12)
        }
    }

    private func tipRow(icon: String, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18))
                .foregroundColor(AppColors.accentBlue)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(AppFonts.subheadline)
                    .fontWeight(.medium)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppFonts.caption1)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(AppFonts.title3)
            .foregroundColor(AppColors.textPrimary)
    }

    // MARK: - Sample Data

    private var sampleGrowthData: [(period: String, value: Double)] {
        [
            ("Y1", 50),
            ("Y2", 65),
            ("Y3", 85),
            ("Y4", 110),
            ("Y5", 140)
        ]
    }
}

// MARK: - Preview

#Preview {
    GrowthInfoSheetView()
}
