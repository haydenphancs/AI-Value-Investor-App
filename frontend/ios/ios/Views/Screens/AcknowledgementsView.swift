//
//  AcknowledgementsView.swift
//  ios
//
//  Screen: open-source acknowledgements / licenses. Update this list when adding or
//  removing a third-party dependency (Swift Package).
//

import SwiftUI

struct AcknowledgementItem: Identifiable {
    let id = UUID()
    let name: String
    let license: String
    let url: String
}

struct AcknowledgementsView: View {
    // Keep in sync with the app's Swift Package dependencies.
    private let items: [AcknowledgementItem] = [
        AcknowledgementItem(
            name: "Sentry (sentry-cocoa)",
            license: "MIT License",
            url: "https://github.com/getsentry/sentry-cocoa"
        ),
    ]

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    Text("Caydex is built with the help of these open-source projects.")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.md)

                    VStack(spacing: 1) {
                        ForEach(items) { item in
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text(item.name)
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)
                                Text(item.license)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                                Text(item.url)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(AppSpacing.lg)
                            .background(AppColors.cardBackground)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                    // Card on the page background: an edge in light, nothing in dark.
                    .cardBorder(cornerRadius: AppCornerRadius.large)
                    .padding(.horizontal, AppSpacing.lg)

                    Spacer().frame(height: AppSpacing.xxxl)
                }
            }
        }
        .navigationTitle("Acknowledgements")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }
}

#Preview {
    NavigationStack { AcknowledgementsView() }
}
