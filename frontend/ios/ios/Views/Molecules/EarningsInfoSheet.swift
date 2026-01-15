//
//  EarningsInfoSheet.swift
//  ios
//
//  Molecule: Info sheet explaining the earnings chart and its indicators
//

import SwiftUI

struct EarningsInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Header description
                    Text("This chart displays quarterly earnings performance comparing actual results against analyst estimates.")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.bottom, AppSpacing.sm)

                    // Data Types Section
                    infoSection(
                        title: "Data Types",
                        items: [
                            InfoItem(
                                icon: "dollarsign.circle.fill",
                                iconColor: AppColors.primaryBlue,
                                title: "EPS (Earnings Per Share)",
                                description: "Company's profit divided by outstanding shares. Higher EPS indicates better profitability."
                            ),
                            InfoItem(
                                icon: "chart.bar.fill",
                                iconColor: AppColors.accentCyan,
                                title: "Revenue",
                                description: "Total income generated from sales before expenses. Shows business growth trajectory."
                            )
                        ]
                    )

                    // Indicators Section
                    infoSection(
                        title: "Chart Indicators",
                        items: [
                            InfoItem(
                                dotColor: AppColors.bullish,
                                title: "Beat",
                                description: "Actual earnings exceeded analyst estimates - a positive signal."
                            ),
                            InfoItem(
                                dotColor: AppColors.bearish,
                                title: "Missed",
                                description: "Actual earnings fell short of estimates - may indicate challenges."
                            ),
                            InfoItem(
                                dotColor: AppColors.bullish,
                                hasDashedBorder: true,
                                title: "Matched",
                                description: "Actual earnings met estimates exactly (0% surprise)."
                            ),
                            InfoItem(
                                dotColor: AppColors.textSecondary,
                                title: "Estimate",
                                description: "Analyst consensus estimate for upcoming quarters."
                            )
                        ]
                    )

                    // Surprise Percentage Section
                    infoSection(
                        title: "Surprise Percentage",
                        items: [
                            InfoItem(
                                icon: "percent",
                                iconColor: AppColors.neutral,
                                title: "Earnings Surprise",
                                description: "Shows how much actual earnings differed from estimates. Positive surprises (green) often lead to stock price increases, while negative surprises (red) may cause declines."
                            )
                        ]
                    )

                    // Price Toggle Section
                    infoSection(
                        title: "Price Overlay",
                        items: [
                            InfoItem(
                                icon: "chart.line.uptrend.xyaxis",
                                iconColor: AppColors.accentCyan,
                                title: "Price Toggle",
                                description: "Enable to overlay historical stock price movement on the earnings chart. This helps visualize how the stock reacted to earnings reports."
                            )
                        ]
                    )

                    // Tips Section
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        Text("Tips")
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)

                        tipRow(icon: "lightbulb.fill", text: "Consistent beats often indicate strong management execution")
                        tipRow(icon: "lightbulb.fill", text: "Compare EPS growth with revenue growth to assess quality")
                        tipRow(icon: "lightbulb.fill", text: "Use 3Y view to identify long-term earnings trends")
                    }
                    .padding(AppSpacing.lg)
                    .background(AppColors.cardBackgroundLight)
                    .cornerRadius(AppCornerRadius.medium)

                    Spacer()
                        .frame(height: AppSpacing.xl)
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Earnings Guide")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Helper Views

    private func infoSection(title: String, items: [InfoItem]) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(title)
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ForEach(items, id: \.title) { item in
                    infoRow(item: item)
                }
            }
        }
    }

    private func infoRow(item: InfoItem) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Icon or dot
            if let dotColor = item.dotColor {
                ZStack {
                    Circle()
                        .fill(dotColor)
                        .frame(width: 16, height: 16)

                    if item.hasDashedBorder {
                        Circle()
                            .stroke(
                                AppColors.textPrimary,
                                style: StrokeStyle(lineWidth: 2, dash: [3, 2])
                            )
                            .frame(width: 20, height: 20)
                    }
                }
                .frame(width: 32, height: 32)
            } else if let icon = item.icon {
                Image(systemName: icon)
                    .font(.system(size: 16))
                    .foregroundColor(item.iconColor ?? AppColors.textSecondary)
                    .frame(width: 32, height: 32)
                    .background(
                        Circle()
                            .fill(AppColors.cardBackgroundLight)
                    )
            }

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(item.title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(item.description)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer()
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }

    private func tipRow(icon: String, text: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: icon)
                .font(.system(size: 12))
                .foregroundColor(AppColors.neutral)

            Text(text)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

// MARK: - Info Item Model

private struct InfoItem {
    let icon: String?
    let iconColor: Color?
    let dotColor: Color?
    let hasDashedBorder: Bool
    let title: String
    let description: String

    init(icon: String, iconColor: Color, title: String, description: String) {
        self.icon = icon
        self.iconColor = iconColor
        self.dotColor = nil
        self.hasDashedBorder = false
        self.title = title
        self.description = description
    }

    init(dotColor: Color, hasDashedBorder: Bool = false, title: String, description: String) {
        self.icon = nil
        self.iconColor = nil
        self.dotColor = dotColor
        self.hasDashedBorder = hasDashedBorder
        self.title = title
        self.description = description
    }
}

#Preview {
    EarningsInfoSheet()
}
