//
//  TickerDetailCompanyProfileSection.swift
//  ios
//
//  Organism: Company Profile section for Ticker Detail
//

import SwiftUI

struct TickerDetailCompanyProfileSection: View {
    let profile: CompanyProfile
    var onWebsiteTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("Company Profile")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Profile card
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Description
                Text(profile.description)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Info rows
                CompanyProfileRow(label: "CEO", value: profile.ceo)
                CompanyProfileRow(label: "Founded", value: profile.founded)
                CompanyProfileRow(label: "Employees", value: profile.formattedEmployees)
                CompanyProfileRow(label: "Headquarters", value: profile.headquarters)

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
                        Text(profile.website)
                            .font(AppTypography.footnoteBold)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailCompanyProfileSection(
            profile: CompanyProfile(
                description: "Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories worldwide. The company offers iPhone, Mac, iPad, and Wearables, Home and Accessories products.",
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
