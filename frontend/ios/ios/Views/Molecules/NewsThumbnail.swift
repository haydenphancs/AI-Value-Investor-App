//
//  NewsThumbnail.swift
//  ios
//
//  Molecule: Thumbnail image for news articles
//

import SwiftUI

struct NewsThumbnail: View {
    let imageName: String?
    var imageURL: URL? = nil
    var width: CGFloat = 100
    var height: CGFloat = 70

    var body: some View {
        Group {
            if let url = imageURL {
                // Remote image via AsyncImage
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                    case .failure:
                        placeholder
                    default:
                        ZStack {
                            AppColors.cardBackgroundLight
                            ProgressView()
                                .tint(AppColors.textMuted)
                        }
                    }
                }
                .frame(width: width, height: height)
                .clipped()
            } else if let name = imageName {
                // Local asset image
                Image(name)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: width, height: height)
                    .clipped()
            } else {
                placeholder
            }
        }
        .cornerRadius(AppCornerRadius.medium)
    }

    private var placeholder: some View {
        ZStack {
            AppColors.cardBackgroundLight

            Image(systemName: "photo")
                .font(AppTypography.iconXL)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(width: width, height: height)
    }
}

#Preview {
    VStack(spacing: 20) {
        NewsThumbnail(imageName: nil)
        NewsThumbnail(imageName: "news_placeholder", width: 120, height: 80)
    }
    .padding()
    .background(AppColors.background)
}
