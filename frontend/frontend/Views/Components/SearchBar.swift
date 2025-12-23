//
//  SearchBar.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct SearchBar: View {
    @State private var searchText = ""

    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(AppColors.secondaryText)
                    .font(.system(size: 16))

                TextField("Search Ticker or Ask AI...", text: $searchText)
                    .foregroundColor(AppColors.primaryText)
                    .font(.system(size: 15))
                    .autocapitalization(.none)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(AppColors.surfaceBackground)
            .cornerRadius(12)

            Button(action: {
                // Profile action
            }) {
                Image(systemName: "person.circle.fill")
                    .font(.system(size: 32))
                    .foregroundColor(AppColors.secondaryText)
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 12)
    }
}

#Preview {
    SearchBar()
        .background(AppColors.background)
}
