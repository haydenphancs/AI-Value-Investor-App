//
//  CompanyLogoView.swift
//  ios
//
//  Atom: Company logo placeholder with fallback to initials
//

import SwiftUI

struct CompanyLogoView: View {
    let ticker: String
    let imageName: String?
    let size: CGFloat
    let gradientColors: [String]?

    init(ticker: String, imageName: String? = nil, size: CGFloat = 40, gradientColors: [String]? = nil) {
        self.ticker = ticker
        self.imageName = imageName
        self.size = size
        self.gradientColors = gradientColors
    }

    private var fallbackGradient: LinearGradient {
        if let colors = gradientColors, colors.count >= 2 {
            return LinearGradient(
                colors: colors.map { Color(hex: $0) },
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
        return LinearGradient(
            colors: [AppColors.cardBackgroundLight, AppColors.cardBackground],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    var body: some View {
        ZStack {
            if let imageName = imageName {
                // Try to load asset image
                Image(imageName)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: size * 0.6, height: size * 0.6)
                    .frame(width: size, height: size)
                    .background(fallbackGradient)
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.25))
            } else {
                // Fallback to initials
                Text(String(ticker.prefix(1)))
                    .font(.system(size: size * 0.4, weight: .bold))
                    .foregroundColor(.white)
                    .frame(width: size, height: size)
                    .background(fallbackGradient)
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.25))
            }
        }
    }
}

// MARK: - System Symbol Logo (for placeholder)
struct SystemLogoView: View {
    let systemName: String
    let size: CGFloat
    let gradientColors: [String]

    private var gradient: LinearGradient {
        LinearGradient(
            colors: gradientColors.map { Color(hex: $0) },
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    var body: some View {
        Image(systemName: systemName)
            .font(.system(size: size * 0.4, weight: .semibold))
            .foregroundColor(.white)
            .frame(width: size, height: size)
            .background(gradient)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.25))
    }
}

#Preview {
    VStack(spacing: 20) {
        CompanyLogoView(ticker: "MSFT", size: 50, gradientColors: ["0078D4", "00BCF2"])
        CompanyLogoView(ticker: "GOOGL", size: 50, gradientColors: ["4285F4", "34A853"])
        CompanyLogoView(ticker: "AMD", size: 50, gradientColors: ["ED1C24", "FF6B6B"])
        SystemLogoView(systemName: "building.2.fill", size: 50, gradientColors: ["0078D4", "00BCF2"])
    }
    .padding()
    .background(AppColors.background)
}
