import SwiftUI

/// Row displaying YoY percentages for each period
/// Colors percentages based on positive (green) or negative (red) values
struct GrowthYoYRowView: View {

    // MARK: - Properties

    /// Data points containing YoY percentages
    let dataPoints: [GrowthDataPoint]

    // MARK: - Body

    var body: some View {
        HStack(spacing: 0) {
            // YoY indicator
            yoyIndicator
                .frame(width: 40)

            // Percentage values
            HStack(spacing: 0) {
                ForEach(dataPoints) { point in
                    percentageCell(for: point)
                }
            }
        }
    }

    // MARK: - Private Views

    private var yoyIndicator: some View {
        Circle()
            .fill(AppColors.chartYoY)
            .frame(width: 10, height: 10)
    }

    private func percentageCell(for point: GrowthDataPoint) -> some View {
        Text(formatPercentage(point.yoyPercentage))
            .font(AppFonts.percentage)
            .foregroundColor(Color.forPercentage(point.yoyPercentage))
            .frame(maxWidth: .infinity)
            .multilineTextAlignment(.center)
    }

    // MARK: - Private Methods

    /// Formats percentage value with sign and % symbol
    private func formatPercentage(_ value: Double) -> String {
        let formatted = String(format: "%.2f", abs(value))
        let sign = value >= 0 ? "" : "-"
        return "\(sign)\(formatted)%"
    }
}

// MARK: - Preview

#Preview {
    GrowthYoYRowView(
        dataPoints: GrowthMetricData.sampleRevenue.dataPoints
    )
    .padding()
    .background(AppColors.backgroundCard)
}
