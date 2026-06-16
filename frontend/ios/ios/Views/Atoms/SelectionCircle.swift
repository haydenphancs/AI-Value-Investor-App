//
//  SelectionCircle.swift
//  ios
//
//  Atom: Multi-select indicator — empty circle ↔ filled checkmark.
//  Domain-free: takes a plain Bool, knows nothing about the app's models.
//

import SwiftUI

struct SelectionCircle: View {
    let isSelected: Bool

    var body: some View {
        Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
            .font(.system(size: 22))
            .foregroundColor(isSelected ? AppColors.primaryBlue : AppColors.textMuted)
            .animation(.easeInOut(duration: 0.15), value: isSelected)
            .accessibilityLabel(isSelected ? "Selected" : "Not selected")
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        SelectionCircle(isSelected: false)
        SelectionCircle(isSelected: true)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
