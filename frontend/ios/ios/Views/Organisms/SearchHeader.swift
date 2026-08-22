//
//  SearchHeader.swift
//  ios
//
//  Organism: Header for the search screen — back button + search field.
//

import SwiftUI

/// The expanded form of `TappableSearchBar`, and it must keep looking like it: same grey
/// `magnifyingglass`, same placeholder. It used to show a blue `sparkles.2` and read
/// "Search or ask Cay AI…", back when this screen could start a chat. It can't any more — chat
/// has its own door in the header (`AskCayAIButton`) — so the AI signalling is gone from here
/// along with the starter-question chips that fed it.
struct SearchHeader: View {
    @Binding var searchText: String
    var onBackTapped: (() -> Void)?
    var onSearchSubmit: (() -> Void)?

    var body: some View {
        // Search bar row
        HStack(spacing: AppSpacing.md) {
            // Back button
            Button(action: {
                onBackTapped?()
            }) {
                Image(systemName: "chevron.down")
                    .font(AppTypography.iconMedium).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 32, height: 32)
            }
            .buttonStyle(PlainButtonStyle())

            // Search bar
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "magnifyingglass")
                    .font(AppTypography.iconDefault).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)

                TextField("", text: $searchText, prompt: Text("Search")
                    .foregroundColor(AppColors.textMuted))
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .onSubmit {
                        onSearchSubmit?()
                    }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.md)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.md)
    }
}

#Preview {
    VStack {
        SearchHeader(searchText: .constant(""))

        Spacer()
    }
    .background(AppColors.background)
}
