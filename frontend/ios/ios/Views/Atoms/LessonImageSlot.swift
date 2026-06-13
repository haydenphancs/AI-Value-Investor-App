//
//  LessonImageSlot.swift
//  ios
//
//  Atom: Reserves space for a lesson image. Renders the named bundle asset if it
//  exists, otherwise a labeled placeholder so artwork can be dropped in later.
//

import SwiftUI

struct LessonImageSlot: View {
    let imageName: String?
    var height: CGFloat = 200
    var horizontalPadding: CGFloat = AppSpacing.xxl

    var body: some View {
        Group {
            if let name = imageName, name.hasPrefix("http"), let url = URL(string: name) {
                // Remote image (Supabase Storage); show placeholder while loading / on failure.
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().aspectRatio(contentMode: .fit)
                    } else if phase.error != nil {
                        placeholder
                    } else {
                        placeholder
                    }
                }
                .frame(maxWidth: .infinity)
                .frame(height: height)
            } else if let name = imageName, !name.isEmpty, UIImage(named: name) != nil {
                Image(name)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: .infinity)
                    .frame(height: height)
            } else {
                placeholder
                    .frame(maxWidth: .infinity)
                    .frame(height: height)
            }
        }
        .padding(.horizontal, horizontalPadding)
    }

    private var placeholder: some View {
        RoundedRectangle(cornerRadius: AppCornerRadius.large)
            .fill(AppColors.cardBackground)
            .overlay(
                VStack(spacing: AppSpacing.sm) {
                    Image(systemName: "photo")
                        .font(.system(size: 28, weight: .light))
                        .foregroundColor(AppColors.textMuted)
                    Text("Image coming soon")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textMuted)
                }
            )
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(spacing: AppSpacing.xl) {
            LessonImageSlot(imageName: nil)                 // placeholder
            LessonImageSlot(imageName: "journey_missing")    // placeholder (asset absent)
        }
    }
    .preferredColorScheme(.dark)
}
