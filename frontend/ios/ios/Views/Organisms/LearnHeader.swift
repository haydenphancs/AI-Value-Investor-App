//
//  LearnHeader.swift
//  ios
//
//  Organism: Header for Learn screen with search and tabs
//

import SwiftUI

struct LearnHeader: View {
    @Binding var searchText: String
    @Binding var selectedTab: LearnTab
    var onSearchSubmit: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Search bar
            HStack(spacing: AppSpacing.md) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(AppColors.textMuted)

                    TextField("Search topics, books, or ask AI...", text: $searchText)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .onSubmit {
                            onSearchSubmit?()
                        }

                    if !searchText.isEmpty {
                        Button(action: {
                            searchText = ""
                        }) {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 16))
                                .foregroundColor(AppColors.textMuted)
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.large)

                // Profile button
                Button(action: {
                    // Profile action
                }) {
                    Circle()
                        .fill(AppColors.cardBackground)
                        .frame(width: 44, height: 44)
                        .overlay(
                            Image(systemName: "person.fill")
                                .font(.system(size: 18, weight: .medium))
                                .foregroundColor(AppColors.textSecondary)
                        )
                }
            }

            // Tab control
            LearnTabControl(selectedTab: $selectedTab)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.sm)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var searchText = ""
        @State private var selectedTab = LearnTab.learn

        var body: some View {
            VStack {
                LearnHeader(searchText: $searchText, selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
