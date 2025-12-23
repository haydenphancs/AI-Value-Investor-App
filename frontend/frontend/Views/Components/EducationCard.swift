//
//  EducationCard.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct EducationCard: View {
    let education: EducationItem

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Icon/Header Section
            ZStack(alignment: .topTrailing) {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(getCardGradient())
                    .frame(height: 80)

                // Bookmark icon
                Image(systemName: "bookmark")
                    .font(.system(size: 18))
                    .foregroundColor(.white)
                    .padding(12)

                // Type Icon
                HStack {
                    Image(systemName: getTypeIcon())
                        .font(.system(size: 32, weight: .bold))
                        .foregroundColor(.white)
                    Spacer()
                }
                .padding(.leading, 16)
                .padding(.top, 24)
            }

            VStack(alignment: .leading, spacing: 8) {
                // Type Badge and Read Time
                HStack {
                    Text(education.type.rawValue)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(getTypeBadgeColor())
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(getTypeBadgeColor().opacity(0.15))
                        .cornerRadius(4)

                    Spacer()

                    Text(education.readTime)
                        .font(.system(size: 10, weight: .regular))
                        .foregroundColor(AppColors.tertiaryText)
                }

                // Title
                Text(education.title)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(AppColors.primaryText)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                // Description or Author with Rating
                if let author = education.author, let rating = education.rating {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(author)
                            .font(.system(size: 12, weight: .regular))
                            .foregroundColor(AppColors.secondaryText)

                        HStack(spacing: 2) {
                            ForEach(0..<5) { index in
                                Image(systemName: index < Int(rating) ? "star.fill" : "star")
                                    .font(.system(size: 10))
                                    .foregroundColor(.orange)
                            }

                            Text(String(format: "%.1f", rating))
                                .font(.system(size: 11, weight: .medium))
                                .foregroundColor(AppColors.secondaryText)
                                .padding(.leading, 4)
                        }
                    }
                } else {
                    Text(education.description)
                        .font(.system(size: 12, weight: .regular))
                        .foregroundColor(AppColors.secondaryText)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }

                // Ask AI Button
                Button(action: {
                    // Ask AI action
                }) {
                    Text("Ask AI")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(AppColors.lightBlue)
                        .cornerRadius(8)
                }
                .padding(.top, 4)
            }
            .padding(12)
        }
        .frame(width: 240)
        .background(AppColors.cardBackground)
        .cornerRadius(16)
    }

    private func getCardGradient() -> LinearGradient {
        switch education.type {
        case .strategy:
            return LinearGradient(
                colors: [AppColors.educationGradientStart, AppColors.educationGradientEnd],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        case .book:
            return LinearGradient(
                colors: [AppColors.educationOrange, AppColors.educationOrange.opacity(0.8)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        case .article:
            return LinearGradient(
                colors: [AppColors.educationBlue, AppColors.educationBlue.opacity(0.8)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }

    private func getTypeIcon() -> String {
        switch education.type {
        case .strategy:
            return "chart.line.uptrend.xyaxis"
        case .book:
            return "book.fill"
        case .article:
            return "lightbulb.fill"
        }
    }

    private func getTypeBadgeColor() -> Color {
        switch education.type {
        case .strategy:
            return AppColors.educationGradientEnd
        case .book:
            return AppColors.educationOrange
        case .article:
            return AppColors.educationBlue
        }
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 12) {
            ForEach(EducationItem.mockData) { education in
                EducationCard(education: education)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
