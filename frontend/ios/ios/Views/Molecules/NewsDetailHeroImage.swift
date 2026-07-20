//
//  NewsDetailHeroImage.swift
//  ios
//
//  Molecule: Full-width hero image for news article
//

import SwiftUI

struct NewsDetailHeroImage: View {
    let imageName: String?
    /// Publisher's remote image. Preferred over `imageName`, which is a legacy
    /// local-asset path — none of the historically referenced `news_*` assets
    /// actually ship in Assets.xcassets, so that branch rendered blank.
    var imageURL: URL? = nil
    var height: CGFloat = 220
    var cornerRadius: CGFloat = AppCornerRadius.large

    var body: some View {
        Group {
            if let imageURL {
                AsyncImage(url: imageURL) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                    case .failure:
                        placeholderView
                    default:
                        ZStack {
                            AppColors.cardBackgroundLight
                            ProgressView().tint(AppColors.textMuted)
                        }
                    }
                }
                .frame(height: height)
                .frame(maxWidth: .infinity)
                .clipped()
                .cornerRadius(cornerRadius)
            } else if let imageName = imageName {
                // Legacy local asset path.
                Image(imageName)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(height: height)
                    .frame(maxWidth: .infinity)
                    .clipped()
                    .cornerRadius(cornerRadius)
            } else {
                // Placeholder with gradient
                placeholderView
            }
        }
    }

    private var placeholderView: some View {
        ZStack {
            // Gradient background
            LinearGradient(
                gradient: Gradient(colors: [
                    AppColors.cardBackgroundLight,
                    AppColors.cardBackground
                ]),
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            // Pattern overlay for visual interest
            GeometryReader { geometry in
                Path { path in
                    let width = geometry.size.width
                    let height = geometry.size.height
                    let spacing: CGFloat = 30

                    // Draw diagonal lines pattern
                    var x: CGFloat = -height
                    while x < width + height {
                        path.move(to: CGPoint(x: x, y: height))
                        path.addLine(to: CGPoint(x: x + height, y: 0))
                        x += spacing
                    }
                }
                .stroke(AppColors.textMuted.opacity(0.1), lineWidth: 1)
            }

            // Center icon
            VStack(spacing: AppSpacing.sm) {
                Image(systemName: "photo.fill")
                    .font(AppTypography.iconXXL).fontWeight(.light)
                    .foregroundColor(AppColors.textMuted.opacity(0.5))

                Text("Image unavailable")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .frame(height: height)
        .frame(maxWidth: .infinity)
        .cornerRadius(cornerRadius)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        NewsDetailHeroImage(imageName: "news_nvidia_hero")

        NewsDetailHeroImage(imageName: nil)

        NewsDetailHeroImage(imageName: nil, height: 160)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
