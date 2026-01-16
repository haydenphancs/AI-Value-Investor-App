import SwiftUI

/// Toggle component for switching between Annual and Quarterly periods
struct GrowthPeriodToggleView: View {

    // MARK: - Properties

    /// Available period types
    let periods: [GrowthPeriodType]

    /// Currently selected period
    @Binding var selectedPeriod: GrowthPeriodType

    // MARK: - Private Properties

    @Namespace private var animation

    // MARK: - Body

    var body: some View {
        HStack(spacing: 0) {
            ForEach(periods) { period in
                periodButton(for: period)
            }
        }
        .background(AppColors.toggleBackground)
        .clipShape(Capsule())
    }

    // MARK: - Private Views

    private func periodButton(for period: GrowthPeriodType) -> some View {
        let isSelected = period == selectedPeriod

        return Button {
            withAnimation(.easeInOut(duration: 0.2)) {
                selectedPeriod = period
            }
        } label: {
            Text(period.displayName)
                .font(AppFonts.tabLabel)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background {
                    if isSelected {
                        Capsule()
                            .fill(AppColors.tabUnselected)
                            .matchedGeometryEffect(id: "periodToggle", in: animation)
                    }
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(period.displayName)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

// MARK: - Preview

#Preview {
    VStack(spacing: 20) {
        GrowthPeriodToggleView(
            periods: GrowthPeriodType.allCases,
            selectedPeriod: .constant(.annual)
        )

        GrowthPeriodToggleView(
            periods: GrowthPeriodType.allCases,
            selectedPeriod: .constant(.quarterly)
        )
    }
    .padding()
    .background(AppColors.backgroundCard)
}
