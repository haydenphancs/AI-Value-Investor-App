//
//  CryptoProfileSection.swift
//  ios
//
//  Organism: Crypto Profile section for Crypto Detail with expandable content
//

import SwiftUI

struct CryptoProfileSection: View {
    let profile: CryptoProfile
    var onWebsiteTap: (() -> Void)?
    var onWhitepaperTap: (() -> Void)?
    @State private var isExpanded: Bool = false

    // Number of lines to show when collapsed
    private let collapsedLineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title
            Text("Crypto Profile")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            // Profile content
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Description with expandable text
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(profile.description)
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .lineLimit(isExpanded ? nil : collapsedLineLimit)
                        .fixedSize(horizontal: false, vertical: true)

                    // More/Less button
                    Button(action: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            isExpanded.toggle()
                        }
                    }) {
                        Text(isExpanded ? "Show less" : "more")
                            .font(AppTypography.footnote)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Info rows
                CompanyProfileRow(label: "Symbol", value: profile.symbol)
                CompanyProfileRow(label: "Launch Date", value: profile.formattedLaunchDate)
                CompanyProfileRow(label: "Consensus", value: profile.consensusMechanism)
                CompanyProfileRow(label: "Blockchain", value: profile.blockchain)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Website
                HStack {
                    Text("Website")
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)

                    Spacer()

                    Button(action: {
                        onWebsiteTap?()
                    }) {
                        HStack(spacing: AppSpacing.xs) {
                            Text(profile.website)
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.primaryBlue)

                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(AppColors.primaryBlue)
                        }
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Whitepaper (if available)
                if let whitepaper = profile.whitepaper {
                    HStack {
                        Text("Whitepaper")
                            .font(AppTypography.footnote)
                            .foregroundColor(AppColors.textSecondary)

                        Spacer()

                        Button(action: {
                            onWhitepaperTap?()
                        }) {
                            HStack(spacing: AppSpacing.xs) {
                                Text(whitepaper)
                                    .font(AppTypography.footnoteBold)
                                    .foregroundColor(AppColors.primaryBlue)

                                Image(systemName: "arrow.up.right")
                                    .font(.system(size: 10, weight: .semibold))
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .buttonStyle(PlainButtonStyle())
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ScrollView {
        CryptoProfileSection(
            profile: CryptoProfile(
                description: "Bitcoin is the first decentralized cryptocurrency, created in 2009 by an anonymous entity known as Satoshi Nakamoto. It introduced blockchain technology as a peer-to-peer electronic cash system, enabling trustless transactions without intermediaries. Bitcoin uses a Proof-of-Work consensus mechanism and has a fixed supply cap of 21 million coins, making it a deflationary digital asset often referred to as 'digital gold.'",
                symbol: "BTC",
                launchDate: "January 3, 2009",
                consensusMechanism: "Proof of Work (PoW)",
                blockchain: "Bitcoin",
                website: "bitcoin.org",
                whitepaper: "bitcoin.org/bitcoin.pdf"
            )
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
