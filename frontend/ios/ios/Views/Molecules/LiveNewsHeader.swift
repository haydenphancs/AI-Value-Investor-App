//
//  LiveNewsHeader.swift
//  ios
//
//  Molecule: Static header for Live News section (non-scrolling)
//

import SwiftUI

struct LiveNewsHeader: View {
    /// Reflects the active filters ("All", "2 filters"). Defaulted so existing
    /// call sites and previews keep compiling.
    var filterLabel: String = "All"
    var hasActiveFilters: Bool = false
    /// "Updated 4:02 PM" while the stories below are the on-device snapshot, nil once they are
    /// live. The header then reads "News" + that time, with no pulsing dot: nothing on screen
    /// is live, so it must not say "Live".
    var snapshotStatusText: String? = nil
    var onFilterTapped: (() -> Void)?

    var body: some View {
        HStack(alignment: .center) {
            title

            Spacer()

            Button(action: { onFilterTapped?() }) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "line.3.horizontal.decrease")
                        .font(AppTypography.iconXS).fontWeight(.medium)

                    Text(filterLabel)
                        .font(AppTypography.bodySmall)
                }
                .foregroundColor(hasActiveFilters ? AppColors.primaryBlue : AppColors.textSecondary)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .frame(height: 44) // Fixed height to prevent layout shifts
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.background)
    }

    /// The title cluster. Both arms sit on the same line in the same fixed-height row, so the
    /// snapshot → live swap never moves the list below.
    @ViewBuilder
    private var title: some View {
        if let snapshotStatusText {
            HStack(spacing: AppSpacing.sm) {
                Text("News")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                // Only this label gives way at large Dynamic Type sizes (the row is 44 pt
                // tall and shares the line with the filter control); the tokens stay as is.
                Text(snapshotStatusText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }
            // One VoiceOver element: "News, Updated 4:02 PM".
            .accessibilityElement(children: .combine)
        } else {
            HStack(spacing: AppSpacing.sm) {
                LiveIndicator()
                    .alignmentGuide(VerticalAlignment.center) { d in d[VerticalAlignment.center] }

                Text("Live News")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
            }
        }
    }
}

#Preview {
    VStack {
        LiveNewsHeader()
        Spacer()
    }
    .background(AppColors.background)
}

#Preview("Snapshot") {
    VStack {
        LiveNewsHeader(snapshotStatusText: "Updated 4:02 PM")
        LiveNewsHeader(hasActiveFilters: true, snapshotStatusText: "Updated Sep 28, 4:02 PM")
        Spacer()
    }
    .background(AppColors.background)
}
