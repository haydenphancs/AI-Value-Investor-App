//
//  HomeView.swift
//  ios
//
//  Now holds only `LoadingOverlay`, which many live screens use.
//
//  The three Home views that used to live here and in ContentView.swift —
//  `HomeContentView`, `HomeView` and `HomeViewWithBinding` — were deleted. All three were
//  unreferenced (`HomeDashboardView` has been the live Home for some time), and each was a
//  loaded gun rather than a harmless rollback copy:
//
//  - `HomeView` was `var body: some View { ContentView() }`. Rendering it anywhere would have
//    built a SECOND entire ContentView — a second copy of all five tabs and all five
//    ViewModels, with none of the per-VM dedup spanning the two.
//  - `HomeContentView` owned a `HomeViewModel`, whose `init` fired a network request. That is
//    the one pattern `test_ios_tabs_reload_on_identity_change.py` explicitly pins against, and
//    it was the last instance of it in the codebase (`HomeViewModel.swift` went with it).
//
//  Per the `project_research_screen_live_vs_preview` lesson: a duplicate of a live screen is
//  where fixes go to die.
//

import SwiftUI

// MARK: - Loading Overlay
struct LoadingOverlay: View {
    var body: some View {
        ZStack {
            Color.black.opacity(0.3)
                .ignoresSafeArea()

            // On the scrim above, so `textOnAccent` (constant #FFFFFF by design) is the
            // right token — same pixels as the bare `.white` it replaces, but declared.
            ProgressView()
                .progressViewStyle(CircularProgressViewStyle(tint: AppColors.textOnAccent))
                .scaleEffect(1.5)
        }
    }
}
