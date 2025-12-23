//
//  HoldingView.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct HoldingView: View {
    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack {
                Text("Holding")
                    .font(.system(size: 32, weight: .bold))
                    .foregroundColor(AppColors.primaryText)

                Text("Coming Soon")
                    .font(.system(size: 16, weight: .regular))
                    .foregroundColor(AppColors.secondaryText)
                    .padding(.top, 8)
            }
        }
    }
}

#Preview {
    HoldingView()
}
