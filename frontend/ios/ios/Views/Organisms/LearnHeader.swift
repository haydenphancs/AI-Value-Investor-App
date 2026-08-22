//
//  LearnHeader.swift
//  ios
//
//  Organism: Learn (Wiser) screen header — uses the standardized GlobalHeaderView
//  plus a tab control below.
//

import SwiftUI

/// The Wiser header is now just the shared global row.
///
/// It used to carry a `Learn | Chat` segmented control underneath, which was two separate
/// problems: "Chat" never actually selected (it presented `AIChatScreen` as a cover while "Learn"
/// stayed highlighted), and once the global header bar grew its own Cay AI door the two sat
/// ~40pt apart wearing the same blue sparkle and read as duplicate controls. The chat door lives
/// in `GlobalHeaderView` now — one per surface, on four tabs instead of just this one.
struct LearnHeader: View {
    var onSearchTapped: (() -> Void)?
    var onProfileTapped: (() -> Void)?

    var body: some View {
        GlobalHeaderView(
            searchPlaceholder: "Search",
            onSearchTapped: onSearchTapped,
            onProfileTapped: onProfileTapped
        )
        .padding(.bottom, AppSpacing.sm)
    }
}

#Preview {
    VStack {
        LearnHeader()
        Spacer()
    }
    .background(AppColors.background)
}
