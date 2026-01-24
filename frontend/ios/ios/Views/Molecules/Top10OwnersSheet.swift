//
//  Top10OwnersSheet.swift
//  ios
//
//  Molecule: Sheet displaying top 10 institutional and insider owners
//  Shows ranked list with name, category/title, value, and ownership percentage
//

import SwiftUI

struct Top10OwnersSheet: View {
    @Environment(\.dismiss) private var dismiss

    let data: Top10OwnersData

    @State private var selectedTab: Top10OwnerTab = .institutions
    @State private var showInfoSheet: Bool = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Tab Selector
                Top10OwnerTabSelector(selectedTab: $selectedTab)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.md)
                    .padding(.bottom, AppSpacing.lg)

                // List Content
                ScrollView {
                    LazyVStack(spacing: AppSpacing.sm) {
                        switch selectedTab {
                        case .institutions:
                            ForEach(data.institutions) { institution in
                                Top10InstitutionRow(institution: institution)
                            }
                        case .insiders:
                            ForEach(data.insiders) { insider in
                                Top10InsiderRow(insider: insider)
                            }
                        }
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.xxxl)
                }
            }
            .background(AppColors.background)
            .navigationTitle("Top 10 Owner")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showInfoSheet = true
                    } label: {
                        Image(systemName: "info.circle")
                            .font(.system(size: 16))
                            .foregroundColor(AppColors.textMuted)
                    }
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .sheet(isPresented: $showInfoSheet) {
            Top10OwnersInfoSheet()
        }
    }
}

// MARK: - Tab Selector

struct Top10OwnerTabSelector: View {
    @Binding var selectedTab: Top10OwnerTab

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Top10OwnerTab.allCases, id: \.rawValue) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTab = tab
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedTab == tab
                                ? AppColors.cardBackgroundLight
                                : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xs)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Institution Row

struct Top10InstitutionRow: View {
    let institution: TopInstitution

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Rank
            Text("#\(institution.rank)")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(width: 28, alignment: .leading)

            // Name and Category
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(institution.name)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(institution.category)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
            }

            Spacer()

            // Value and Percentage
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(institution.formattedValue)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(institution.formattedPercent)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Insider Row

struct Top10InsiderRow: View {
    let insider: TopInsider

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Rank
            Text("#\(insider.rank)")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(width: 28, alignment: .leading)

            // Name and Title
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(insider.name)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Text(insider.title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
            }

            Spacer()

            // Value and Percentage
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(insider.formattedValue)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(insider.formattedPercent)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Info Sheet

struct Top10OwnersInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Header
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "building.2.fill")
                                .font(.system(size: 24))
                                .foregroundColor(AppColors.primaryBlue)

                            Text("Understanding Top Owners")
                                .font(AppTypography.title2)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("This list shows the largest shareholders of the company, ranked by the dollar value of their holdings.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .padding(AppSpacing.lg)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .fill(AppColors.cardBackground)
                    )

                    // Institutions Section
                    infoSection(
                        icon: "building.columns.fill",
                        title: "Institutional Owners",
                        description: "Large investment firms that manage money for others. High institutional ownership often indicates professional confidence in the company.",
                        tips: [
                            "Watch for increasing positions by respected funds",
                            "Diversified ownership is healthier than concentration",
                            "Quarterly 13F filings reveal position changes"
                        ]
                    )

                    // Insiders Section
                    infoSection(
                        icon: "person.fill.checkmark",
                        title: "Insider Owners",
                        description: "Company executives and directors who own shares. Their ownership aligns their interests with shareholders.",
                        tips: [
                            "CEO ownership shows skin in the game",
                            "Directors owning shares signals confidence",
                            "Watch for recent buying activity"
                        ]
                    )

                    // What the Numbers Mean
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        Text("What the Numbers Mean")
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)

                        VStack(spacing: AppSpacing.sm) {
                            numberExplanation(
                                title: "Value ($14.5B)",
                                description: "The total market value of shares owned, based on current stock price."
                            )

                            numberExplanation(
                                title: "Percentage (5.2%)",
                                description: "The portion of all outstanding shares owned by this entity."
                            )
                        }
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("About Top Owners")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    private func infoSection(icon: String, title: String, description: String, tips: [String]) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(.system(size: 18))
                    .foregroundColor(AppColors.primaryBlue)

                Text(title)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(description)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(tips, id: \.self) { tip in
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 14))
                            .foregroundColor(AppColors.bullish)

                        Text(tip)
                            .font(AppTypography.callout)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    private func numberExplanation(title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: "number.circle.fill")
                .font(.system(size: 20))
                .foregroundColor(AppColors.primaryBlue)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Preview

#Preview {
    Top10OwnersSheet(data: Top10OwnersData.sampleData)
}

#Preview("Info Sheet") {
    Top10OwnersInfoSheet()
}
