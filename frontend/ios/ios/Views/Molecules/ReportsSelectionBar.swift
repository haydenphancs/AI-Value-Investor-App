//
//  ReportsSelectionBar.swift
//  ios
//
//  Molecule: Bottom action bar shown in Reports selection mode. Hosts the
//  destructive "Delete (N Selected)" button. Mirrors ArticleActionBar's
//  glassmorphism + top shadow so it reads as a floating bottom bar above
//  the app tab bar.
//

import SwiftUI

struct ReportsSelectionBar: View {
    let selectedCount: Int
    var isDeleting: Bool = false
    var onDelete: (() -> Void)?

    private var isEnabled: Bool { selectedCount > 0 && !isDeleting }

    var body: some View {
        HStack {
            Spacer()
            Button(action: { onDelete?() }) {
                HStack(spacing: AppSpacing.xs) {
                    if isDeleting {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: "trash")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                    }
                    Text(selectedCount > 0 ? "Delete (\(selectedCount) Selected)" : "Delete")
                        .font(AppTypography.labelSmallEmphasis)
                }
                .foregroundColor(.white)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Capsule()
                        .fill(isEnabled ? AppColors.bearish : AppColors.bearish.opacity(0.4))
                )
                // Subtle shadow so the floating button reads over scrolling
                // content — there's no longer a bar behind it.
                .shadow(color: Color.black.opacity(0.25), radius: 6, y: 2)
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(!isEnabled)
            .accessibilityHint("Deletes \(selectedCount) report\(selectedCount == 1 ? "" : "s")")
            Spacer()
        }
        .padding(.bottom, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: 0) {
        Spacer()
        ReportsSelectionBar(selectedCount: 0)
        ReportsSelectionBar(selectedCount: 3)
        ReportsSelectionBar(selectedCount: 1, isDeleting: true)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
