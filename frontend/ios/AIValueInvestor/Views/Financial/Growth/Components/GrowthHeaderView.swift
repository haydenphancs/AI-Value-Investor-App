import SwiftUI

/// Header component for the Growth section
/// Displays title, info button, and detail navigation link
struct GrowthHeaderView: View {

    // MARK: - Properties

    /// Action triggered when info button is tapped
    var onInfoTapped: () -> Void

    /// Action triggered when detail link is tapped
    var onDetailTapped: () -> Void

    // MARK: - Body

    var body: some View {
        HStack(alignment: .center) {
            titleWithInfo
            Spacer()
            detailLink
        }
    }

    // MARK: - Private Views

    private var titleWithInfo: some View {
        HStack(spacing: 8) {
            Text("Growth")
                .font(AppFonts.sectionHeader)
                .foregroundColor(AppColors.textPrimary)

            infoButton
        }
    }

    private var infoButton: some View {
        Button(action: onInfoTapped) {
            ZStack {
                Circle()
                    .fill(AppColors.tabUnselected)
                    .frame(width: 24, height: 24)

                Text("i")
                    .font(.system(size: 14, weight: .semibold, design: .serif))
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Growth information")
        .accessibilityHint("Shows explanation of growth metrics")
    }

    private var detailLink: some View {
        Button(action: onDetailTapped) {
            Text("Detail")
                .font(AppFonts.detailLink)
                .foregroundColor(AppColors.accentBlue)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View growth details")
    }
}

// MARK: - Preview

#Preview {
    GrowthHeaderView(
        onInfoTapped: {},
        onDetailTapped: {}
    )
    .padding()
    .background(AppColors.backgroundCard)
}
