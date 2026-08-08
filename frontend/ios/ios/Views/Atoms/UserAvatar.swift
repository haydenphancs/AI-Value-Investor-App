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

    /// `*Fill`, not the text tokens. This circle carries ink, and the text family LIGHTENS in
    /// dark — white on `gain` #22C55E is 2.28, on `primaryBlue` #60A5FA 2.24. Each `*Fill` is
    /// its text token's LIGHT arm frozen, so light mode is byte-identical.
    private var backgroundColor: Color {
        // Generate consistent color based on name
        let colors: [Color] = [
            AppColors.primaryFill,
            AppColors.gainFill,
            AppColors.alertOrangeFill,
            AppColors.alertPurpleFill,
            AppColors.accentCyanFill,
            AppColors.lossFill
        ]
        let index = abs(name.hashValue) % colors.count
        return colors[index]
    }

    /// Ink for `backgroundColor`, INDEX FOR INDEX with the palette above. Slots 1 and 5 are the
    /// ADAPTIVE `gainFill`/`lossFill` and need near-black in dark; the other four are frozen and
    /// need white. One ink cannot serve both — near-black on frozen `primaryFill` is 3.35.
    /// Mirrors `WhaleAvatarView.backgroundInk`, which is the same construction.
    private var backgroundInk: Color {
        let inks: [Color] = [
            AppColors.textOnAccent,
            AppColors.textOnFill,
            AppColors.textOnAccent,
            AppColors.textOnAccent,
            AppColors.textOnAccent,
            AppColors.textOnFill
        ]
        return inks[abs(name.hashValue) % inks.count]
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
                            .foregroundColor(backgroundInk)
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
}
