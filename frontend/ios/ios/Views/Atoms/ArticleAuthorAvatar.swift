//
//  ArticleAuthorAvatar.swift
//  ios
//
//  Atom: Avatar display for article authors with initials fallback
//

import SwiftUI

struct ArticleAuthorAvatar: View {
    let name: String
    let imageName: String?
    var size: CGFloat = 40
    var showVerifiedBadge: Bool = false

    private var initials: String {
        let components = name.components(separatedBy: " ")
        let initialsArray = components.compactMap { $0.first }.prefix(2)
        return String(initialsArray).uppercased()
    }

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            // Avatar
            if let imageName = imageName {
                Image(imageName)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: size, height: size)
                    .clipShape(Circle())
            } else {
                // Initials fallback with gradient
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(hex: "3B82F6"),
                                Color(hex: "8B5CF6")
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: size, height: size)
                    .overlay(
                        Text(initials)
                            .font(.system(size: size * 0.4, weight: .semibold))
                            .foregroundColor(.white)
                    )
            }

            // Verified badge
            if showVerifiedBadge {
                Image(systemName: "checkmark.seal.fill")
                    .font(.system(size: size * 0.35))
                    .foregroundColor(AppColors.primaryBlue)
                    .background(
                        Circle()
                            .fill(AppColors.background)
                            .frame(width: size * 0.4, height: size * 0.4)
                    )
                    .offset(x: 2, y: 2)
            }
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        ArticleAuthorAvatar(name: "The Alpha", imageName: nil)
        ArticleAuthorAvatar(name: "John Doe", imageName: nil, size: 50, showVerifiedBadge: true)
        ArticleAuthorAvatar(name: "Sarah", imageName: nil, size: 32)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
