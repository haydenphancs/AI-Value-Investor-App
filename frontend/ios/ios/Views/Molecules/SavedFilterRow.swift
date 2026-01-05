//
//  SavedFilterRow.swift
//  ios
//
//  Molecule: Horizontal scrolling row of filter pills for saved items
//

import SwiftUI

struct SavedFilterRow: View {
    @Binding var selectedFilter: SavedFilterType
    var onFilterChange: ((SavedFilterType) -> Void)?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(SavedFilterType.allCases, id: \.rawValue) { filter in
                    SavedFilterPill(
                        filter: filter,
                        isSelected: selectedFilter == filter
                    ) {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedFilter = filter
                            onFilterChange?(filter)
                        }
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedFilter: SavedFilterType = .all

        var body: some View {
            SavedFilterRow(selectedFilter: $selectedFilter)
        }
    }

    return PreviewWrapper()
        .padding(.vertical, AppSpacing.lg)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
