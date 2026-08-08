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
                            .tint(AppColors.textOnFill)
                    } else {
                        Image(systemName: "trash")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                    }
                    Text(selectedCount > 0 ? "Delete (\(selectedCount) Selected)" : "Delete")
                        .font(AppTypography.labelSmallEmphasis)
                }
                .foregroundColor(AppColors.textOnFill)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.sm)
                // OPAQUE in both states, with the disabled fade applied to the COMPOSED
                // button below instead. Fading only the fill was a 2.12:1 defect: `lossFill`
                // at 40% over the page composites to #713D44, and the near-black `textOnFill`
                // this button carries measured 2.12 on it in dark where the white it replaced
                // measured 8.56. `isEnabled` is false while `isDeleting`, so the spinner and
                // its label went near-invisible exactly while the user watched the delete run.
                //
                // Fading ink and surface TOGETHER keeps their ratio at 6.41 dark / 5.55 light
                // in every state, which also makes the fill-ink contract honest rather than
                // something this site has to work around.
                .background(Capsule().fill(AppColors.lossFill))
                // Subtle shadow so the floating button reads over scrolling
                // content — there's no longer a bar behind it.
                .shadow(color: AppColors.shadowKey, radius: 6, y: 2)
                .opacity(isEnabled ? 1 : 0.4)
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
}
