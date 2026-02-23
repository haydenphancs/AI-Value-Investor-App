//
//  UpdatesHeader.swift
//  ios
//
//  Organism: Updates screen header — uses the standardized GlobalHeaderView
//

import SwiftUI

struct UpdatesHeader: View {
    var onSearchTapped: (() -> Void)?
    var onProfileTapped: (() -> Void)?

    var body: some View {
        GlobalHeaderView(
            searchPlaceholder: "Search market news...",
            onSearchTapped: onSearchTapped,
            onProfileTapped: onProfileTapped
        )
    }
}

#Preview {
    VStack {
        UpdatesHeader()
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
