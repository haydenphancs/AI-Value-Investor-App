//
//  NewsThumbnail.swift
//  ios
//
//  Molecule: Thumbnail image for news articles
//

import SwiftUI

struct NewsThumbnail: View {
    let imageName: String?
    var width: CGFloat = 100
    var height: CGFloat = 70

    var body: some View {
        Group {
            if let name = imageName {
                Image(name)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: width, height: height)
                    .clipped()
            } else {
                // Placeholder
                ZStack {
                    AppColors.cardBackgroundLight

                    Image(systemName: "photo")
                        .font(.system(size: 24))
                        .foregroundColor(AppColors.textMuted)
                }
                .frame(width: width, height: height)
            }
        }
        .cornerRadius(AppCornerRadius.medium)
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
