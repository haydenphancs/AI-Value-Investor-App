//
//  UserAvatar.swift
//  ios
//
//  Atom: User avatar circle with fallback initials
//

import SwiftUI

struct UserAvatar: View {
    let name: String
    let imageName: String?
    var size: CGFloat = 40

    private var initials: String {
        let components = name.components(separatedBy: " ")
        let firstInitial = components.first?.first.map(String.init) ?? ""
        let lastInitial = components.count > 1 ? components.last?.first.map(String.init) ?? "" : ""
        return "\(firstInitial)\(lastInitial)"
    }

    private var backgroundColor: Color {
        // Generate consistent color based on name
        let colors: [Color] = [
            Color(hex: "3B82F6"),
            Color(hex: "22C55E"),
            Color(hex: "F97316"),
            Color(hex: "A855F7"),
            Color(hex: "06B6D4"),
            Color(hex: "EF4444")
        ]
        let index = abs(name.hashValue) % colors.count
        return colors[index]
    }

    var body: some View {
        ZStack {
            if let imageName = imageName, !imageName.isEmpty {
                // Try to load image
                Image(imageName)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: size, height: size)
                    .clipShape(Circle())
            } else {
                // Fallback to initials
                Circle()
                    .fill(backgroundColor)
                    .frame(width: size, height: size)
                    .overlay(
                        Text(initials)
                            .font(.system(size: size * 0.4, weight: .semibold))
                            .foregroundColor(AppColors.textPrimary)
                    )
            }
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.md) {
        UserAvatar(name: "David Martinez", imageName: nil)
        UserAvatar(name: "Sarah Johnson", imageName: nil)
        UserAvatar(name: "John Doe", imageName: nil, size: 32)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
