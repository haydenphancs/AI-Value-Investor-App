import SwiftUI

/// Horizontal scrollable tab bar for selecting growth metrics
struct GrowthMetricTabsView: View {

    // MARK: - Properties

    /// Available metric types to display
    let metrics: [GrowthMetricType]

    /// Currently selected metric
    @Binding var selectedMetric: GrowthMetricType

    // MARK: - Body

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(metrics) { metric in
                    MetricTabButton(
                        metric: metric,
                        isSelected: metric == selectedMetric,
                        action: { selectedMetric = metric }
                    )
                }
            }
            .padding(.horizontal, 2)
        }
    }
}

// MARK: - Metric Tab Button

/// Individual tab button for a metric type
private struct MetricTabButton: View {

    let metric: GrowthMetricType
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(metric.displayName)
                .font(AppFonts.tabLabel)
                .foregroundColor(foregroundColor)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(background)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(metric.displayName)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private var foregroundColor: Color {
        isSelected ? AppColors.textPrimary : AppColors.tabUnselectedText
    }

    @ViewBuilder
    private var background: some View {
        if isSelected {
            Capsule()
                .fill(AppColors.accentBlue)
        } else {
            Capsule()
                .fill(AppColors.tabUnselected)
        }
    }
}

// MARK: - Preview

#Preview {
    VStack {
        GrowthMetricTabsView(
            metrics: GrowthMetricType.allCases,
            selectedMetric: .constant(.revenue)
        )

        GrowthMetricTabsView(
            metrics: GrowthMetricType.allCases,
            selectedMetric: .constant(.eps)
        )
    }
    .padding()
    .background(AppColors.backgroundCard)
}
