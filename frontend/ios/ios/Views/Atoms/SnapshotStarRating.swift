//
//  SnapshotStarRating.swift
//  ios
//
//  Atom: Star rating display for Snapshots with colored/gray stars
//

import SwiftUI

struct SnapshotStarRating: View {
    let rating: SnapshotRatingLevel
    let maxRating: Int = 5
    var starSize: CGFloat = 12

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<maxRating, id: \.self) { index in
                Image(systemName: index < rating.starCount ? "star.fill" : "star")
                    .font(.system(size: starSize))
                    .foregroundColor(index < rating.starCount ? rating.color : AppColors.textMuted)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        HStack(spacing: AppSpacing.lg) {
            SnapshotStarRating(rating: .excellent)
            Text("Excellent").foregroundColor(.white)
        }
        HStack(spacing: AppSpacing.lg) {
            SnapshotStarRating(rating: .strong)
            Text("Strong").foregroundColor(.white)
        }
        HStack(spacing: AppSpacing.lg) {
            SnapshotStarRating(rating: .average)
            Text("Average").foregroundColor(.white)
        }
        HStack(spacing: AppSpacing.lg) {
            SnapshotStarRating(rating: .weak)
            Text("Weak").foregroundColor(.white)
        }
        HStack(spacing: AppSpacing.lg) {
            SnapshotStarRating(rating: .poor)
            Text("Poor").foregroundColor(.white)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
