//
//  ShareSheet.swift
//  ios
//
//  Atom: Share sheet component using UIActivityViewController
//  A UIKit bridge for presenting the native iOS share sheet
//

import SwiftUI

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    var excludedActivityTypes: [UIActivity.ActivityType]? = nil

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: items, applicationActivities: nil)
        controller.excludedActivityTypes = Self.effectiveExclusions(
            excludedActivityTypes,
            canAddToPhotos: Bundle.main.object(forInfoDictionaryKey: "NSPhotoLibraryAddUsageDescription") != nil
        )
        return controller
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}

    /// "Save Image" writes to Photos, and iOS TERMINATES the app when that happens without an
    /// `NSPhotoLibraryAddUsageDescription` in Info.plist. The app declares none (it never needs
    /// Photos write access), yet the bug-report share path hands the sheet a `UIImage` — so
    /// the activity is removed whenever the key is absent. Adding the key re-enables it.
    static func effectiveExclusions(
        _ requested: [UIActivity.ActivityType]?,
        canAddToPhotos: Bool
    ) -> [UIActivity.ActivityType]? {
        guard !canAddToPhotos else { return requested }
        let base = requested ?? []
        return base.contains(.saveToCameraRoll) ? base : base + [.saveToCameraRoll]
    }
}

// MARK: - Preview
#Preview {
    Text("Share Sheet")
        .sheet(isPresented: .constant(true)) {
            ShareSheet(items: ["Sample content to share"])
        }
}
