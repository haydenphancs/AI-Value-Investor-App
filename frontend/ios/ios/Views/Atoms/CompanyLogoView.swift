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
    /// What the placeholder tile says while the logo loads or when the CDN has none. nil →
    /// the ticker's first character — which is a DIGIT for a local listing ("2222.SR"), so a
    /// caller showing one passes the company's monogram instead.
    let fallbackText: String?

    init(ticker: String, imageName: String? = nil, size: CGFloat = 40, gradientColors: [String]? = nil,
         fallbackText: String? = nil) {
        self.ticker = ticker
        self.imageName = imageName
        self.size = size
        self.gradientColors = gradientColors
        self.fallbackText = fallbackText
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

    /// FMP serves a per-ticker logo PNG (public CDN, no API key). Used when no
    /// real bundled asset is supplied; falls back to initials on load/failure.
    private var remoteLogoURL: URL? {
        let symbol = ticker.uppercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !symbol.isEmpty else { return nil }
        return URL(string: "https://images.financialmodelingprep.com/symbol/\(symbol).png")
    }

    /// Only resolves when `imageName` names a real asset in the catalog. Several
    /// `icon_*` names referenced elsewhere don't exist, so this guards against
    /// rendering a blank `Image("missing")` instead of falling through to the logo.
    private var bundledImage: Image? {
        guard let imageName, UIImage(named: imageName) != nil else { return nil }
        return Image(imageName)
    }

    /// White ink belongs to a caller's saturated brand gradient. The default gradient is
    /// the card fills (near-white in light mode), where white ink vanished — so it carries
    /// the TEXT-role `textPrimary`, which clears 4.5:1 on both card fills in both modes.
    private var initialsInk: Color {
        if let colors = gradientColors, colors.count >= 2 { return AppColors.textOnAccent }
        return AppColors.textPrimary
    }

    private var initialsView: some View {
        Text(fallbackText ?? String(ticker.prefix(1)))
            .font(.system(size: size * 0.4, weight: .bold))
            .foregroundColor(initialsInk)
            .frame(width: size, height: size)
            .background(fallbackGradient)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.25))
    }

    var body: some View {
        ZStack {
            if let bundledImage {
                // A real bundled asset wins.
                bundledImage
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: size * 0.6, height: size * 0.6)
                    .frame(width: size, height: size)
                    .background(fallbackGradient)
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.25))
            } else if let remoteLogoURL {
                // Remote FMP logo on a white chip (logos are often dark/transparent,
                // which would vanish on the dark UI). Initials show while loading
                // or if the symbol has no logo.
                AsyncImage(url: remoteLogoURL) { phase in
                    if case .success(let image) = phase {
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .padding(size * 0.16)
                            .frame(width: size, height: size)
                            .background(AppColors.mediaSurface)
                            .accessibilityIgnoresInvertColors()
                            .clipShape(RoundedRectangle(cornerRadius: size * 0.25))
                    } else {
                        initialsView
                    }
                }
            } else {
                initialsView
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
            .foregroundColor(AppColors.textOnAccent)
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
