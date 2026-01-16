import SwiftUI

/// Demo view showcasing the Growth Section component
/// Use this for development previews and testing
struct GrowthSectionDemoView: View {

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    // Stock header (placeholder)
                    stockHeader

                    // Growth Section
                    GrowthSectionView(ticker: "AAPL")

                    // Additional sections placeholder
                    placeholderSection(title: "Valuation")
                    placeholderSection(title: "Profitability")
                }
                .padding()
            }
            .background(AppColors.backgroundPrimary)
            .navigationTitle("AAPL")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    // MARK: - Private Views

    private var stockHeader: some View {
        HStack(spacing: 12) {
            // Logo placeholder
            RoundedRectangle(cornerRadius: 12)
                .fill(AppColors.backgroundElevated)
                .frame(width: 56, height: 56)
                .overlay {
                    Text("AAPL")
                        .font(AppFonts.caption1)
                        .foregroundColor(AppColors.textSecondary)
                }

            VStack(alignment: .leading, spacing: 4) {
                Text("Apple Inc.")
                    .font(AppFonts.headline)
                    .foregroundColor(AppColors.textPrimary)

                Text("Technology • NASDAQ")
                    .font(AppFonts.caption1)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 4) {
                Text("$185.92")
                    .font(AppFonts.headline)
                    .foregroundColor(AppColors.textPrimary)

                Text("+2.34%")
                    .font(AppFonts.caption1)
                    .foregroundColor(AppColors.positive)
            }
        }
        .padding()
        .background(AppColors.backgroundCard)
        .cornerRadius(16)
    }

    private func placeholderSection(title: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(AppFonts.sectionHeader)
                .foregroundColor(AppColors.textPrimary)

            RoundedRectangle(cornerRadius: 12)
                .fill(AppColors.backgroundElevated)
                .frame(height: 150)
                .overlay {
                    Text("\(title) Section Coming Soon")
                        .font(AppFonts.subheadline)
                        .foregroundColor(AppColors.textTertiary)
                }
        }
        .padding()
        .background(AppColors.backgroundCard)
        .cornerRadius(16)
    }
}

// MARK: - Preview

#Preview {
    GrowthSectionDemoView()
        .preferredColorScheme(.dark)
}
