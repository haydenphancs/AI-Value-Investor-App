//
//  Extensions.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

// MARK: - View Extensions
extension View {
    /// Applies a card style with background and corner radius
    func cardStyle(backgroundColor: Color = AppColors.cardBackground) -> some View {
        self
            .background(backgroundColor)
            .cornerRadius(16)
    }

    /// Applies a section header style
    func sectionHeaderStyle() -> some View {
        self
            .font(.system(size: 18, weight: .semibold))
            .foregroundColor(AppColors.primaryText)
    }

    /// Applies see all button style
    func seeAllButtonStyle() -> some View {
        self
            .font(.system(size: 14, weight: .medium))
            .foregroundColor(AppColors.lightBlue)
    }
}

// MARK: - Font Extensions
extension Font {
    static func appFont(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        return .system(size: size, weight: weight)
    }
}

// MARK: - Double Extensions
extension Double {
    var formattedPercentage: String {
        let sign = self >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", self))%"
    }

    var formattedPrice: String {
        return "$\(String(format: "%.2f", self))"
    }
}
