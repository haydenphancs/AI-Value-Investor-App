//
//  TickerDetailCompanyProfileSection.swift
//  ios
//
//  Organism: Company Profile section for Ticker Detail with expandable content
//

import SwiftUI

struct TickerDetailCompanyProfileSection: View {
    let profile: CompanyProfile
    var onWebsiteTap: (() -> Void)?
    @State private var isExpanded: Bool = false

    // Number of lines to show when collapsed
    private let collapsedLineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title inside card styling
            Text("Company Profile")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            // Profile content
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Description with expandable text
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(profile.description)
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .lineLimit(isExpanded ? nil : collapsedLineLimit)
                        .fixedSize(horizontal: false, vertical: true)

                    // More/Less button
                    Button(action: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            isExpanded.toggle()
                        }
                    }) {
                        Text(isExpanded ? "Show less" : "more")
                            .font(AppTypography.footnote)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Info rows - show basic info always
                CompanyProfileRow(label: "CEO", value: profile.ceo)
                CompanyProfileRow(label: "Founded", value: profile.founded)
                CompanyProfileRow(label: "Employees", value: profile.formattedEmployees)
                CompanyProfileRow(label: "Headquarters", value: profile.headquarters)

                // Additional info shown when expanded
                if isExpanded {
                    // Add more profile details here if available
                    // For now we show the same info, but this structure allows for expansion

                    // Divider
                    Rectangle()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(height: 1)

                    // Additional company details (placeholder for future expansion)
                    if let additionalInfo = getAdditionalInfo() {
                        ForEach(additionalInfo, id: \.label) { info in
                            CompanyProfileRow(label: info.label, value: info.value)
                        }
                    }
                }

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Website
                HStack {
                    Text("Website")
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)

                    Spacer()

                    Button(action: {
                        onWebsiteTap?()
                    }) {
                        HStack(spacing: AppSpacing.xs) {
                            Text(profile.website)
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.primaryBlue)

                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(AppColors.primaryBlue)
                        }
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // Helper to get additional profile info (can be extended based on available data)
    private func getAdditionalInfo() -> [(label: String, value: String)]? {
        // Return nil if no additional info available
        // This can be populated with more company data in the future
        return nil
    }
}

#Preview {
    ScrollView {
        TickerDetailCompanyProfileSection(
            profile: CompanyProfile(
                description: "Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories worldwide. The company offers iPhone, Mac, iPad, and Wearables, Home and Accessories products. Apple was founded by Steve Jobs, Steve Wozniak, and Ronald Wayne in April 1976 to develop and sell personal computers. The company has since grown to become one of the most valuable companies in the world.",
                ceo: "Tim Cook",
                founded: "April 1, 1976",
                employees: 161000,
                headquarters: "Cupertino, CA",
                website: "www.apple.com"
            )
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
