//
//  SuggestionChipsRow.swift
//  ios
//
//  Molecule: Horizontal row of suggestion chips that wraps
//

import SwiftUI

struct SuggestionChipsRow: View {
    let chips: [SuggestionChip]
    var onChipTap: ((SuggestionChip) -> Void)?

    var body: some View {
        // Use a flow layout approach with multiple rows
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // First row - first 3 chips
            HStack(spacing: AppSpacing.md) {
                ForEach(Array(chips.prefix(3))) { chip in
                    SuggestionChipView(chip: chip) {
                        onChipTap?(chip)
                    }
                }
            }

            // Second row - remaining chips
            if chips.count > 3 {
                HStack(spacing: AppSpacing.md) {
                    ForEach(Array(chips.dropFirst(3))) { chip in
                        SuggestionChipView(chip: chip) {
                            onChipTap?(chip)
                        }
                    }
                }
            }
        }
    }
}

#Preview {
    SuggestionChipsRow(chips: SuggestionChip.sampleData)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
