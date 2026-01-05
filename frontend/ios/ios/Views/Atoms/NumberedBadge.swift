//
//  NumberedBadge.swift
//  ios
//
//  Atom: Circular numbered badge for key takeaways
//

import SwiftUI

struct NumberedBadge: View {
    let number: Int
    var size: CGFloat = 24
    var backgroundColor: Color = AppColors.primaryBlue
    var textColor: Color = AppColors.textPrimary

    var body: some View {
        Text("\(number)")
            .font(.system(size: size * 0.5, weight: .bold, design: .rounded))
            .foregroundColor(textColor)
            .frame(width: size, height: size)
            .background(backgroundColor)
            .clipShape(Circle())
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        HStack(spacing: AppSpacing.md) {
            NumberedBadge(number: 1)
            NumberedBadge(number: 2)
            NumberedBadge(number: 3)
            NumberedBadge(number: 4)
        }

        HStack(spacing: AppSpacing.md) {
            NumberedBadge(number: 1, size: 20)
            NumberedBadge(number: 2, size: 28)
            NumberedBadge(number: 3, size: 32)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
