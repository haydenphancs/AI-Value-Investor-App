import SwiftUI

/// Legend component showing chart series identification
struct GrowthLegendView: View {

    // MARK: - Body

    var body: some View {
        HStack(spacing: 24) {
            legendItem(color: AppColors.chartYoY, label: "YoY")
            legendItem(color: AppColors.chartValue, label: "Value")
            sectorAverageLegendItem
        }
    }

    // MARK: - Private Views

    private func legendItem(color: Color, label: String) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)

            Text(label)
                .font(AppFonts.caption1)
                .foregroundColor(AppColors.textSecondary)
        }
    }

    private var sectorAverageLegendItem: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(AppColors.chartSectorAverage)
                .frame(width: 10, height: 10)

            VStack(spacing: 0) {
                Text("Sector Average")
                    .font(AppFonts.caption1)
                    .foregroundColor(AppColors.textSecondary)
                Text("(YoY)")
                    .font(AppFonts.caption2)
                    .foregroundColor(AppColors.textTertiary)
            }
        }
    }
}

// MARK: - Preview

#Preview {
    GrowthLegendView()
        .padding()
        .background(AppColors.backgroundCard)
}
