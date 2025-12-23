//
//  ResearchCard.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct ResearchCard: View {
    let research: ResearchItem

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Company Logo/Icon
            ZStack {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(getLogoBackground())
                    .frame(height: 100)

                // Using SF Symbol as placeholder for company logo
                Image(systemName: getLogoSystemIcon())
                    .font(.system(size: 40, weight: .bold))
                    .foregroundColor(.white)
            }

            VStack(alignment: .leading, spacing: 8) {
                // AI Analysis Badge and Time
                HStack {
                    Text("AI Analysis")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundColor(AppColors.secondaryText)

                    Spacer()

                    Text(research.postedTime)
                        .font(.system(size: 10, weight: .regular))
                        .foregroundColor(AppColors.tertiaryText)
                }

                // Title
                Text(research.title)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(AppColors.primaryText)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                // Description
                Text(research.description)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundColor(AppColors.secondaryText)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)

                // Rating and Target
                HStack(spacing: 8) {
                    HStack(spacing: 4) {
                        Text(research.rating.rawValue)
                            .font(.system(size: 11, weight: .bold))
                            .foregroundColor(.white)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(getRatingColor())
                    .cornerRadius(6)

                    Text("Target: \(research.targetPrice)")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(AppColors.secondaryText)

                    Spacer()

                    Button(action: {
                        // Read report action
                    }) {
                        Text("Read Report")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(AppColors.lightBlue)
                    }
                }
                .padding(.top, 4)
            }
            .padding(12)
        }
        .frame(width: 280)
        .background(AppColors.cardBackground)
        .cornerRadius(16)
    }

    private func getLogoBackground() -> Color {
        switch research.company {
        case "Microsoft":
            return AppColors.blue
        case "Google":
            return AppColors.lightBlue
        case "AMD":
            return AppColors.blue
        default:
            return AppColors.blue
        }
    }

    private func getLogoSystemIcon() -> String {
        switch research.company {
        case "Microsoft":
            return "square.grid.2x2.fill"
        case "Google":
            return "g.circle.fill"
        case "AMD":
            return "a.circle.fill"
        default:
            return "building.2.fill"
        }
    }

    private func getRatingColor() -> Color {
        switch research.rating {
        case .buy:
            return AppColors.ratingBuy
        case .hold:
            return AppColors.ratingHold
        case .sell:
            return AppColors.ratingSell
        }
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 12) {
            ForEach(ResearchItem.mockData) { research in
                ResearchCard(research: research)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
