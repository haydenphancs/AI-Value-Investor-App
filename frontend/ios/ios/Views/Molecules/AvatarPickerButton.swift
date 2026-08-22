//
//  AvatarPickerButton.swift
//  ios
//
//  Molecule: the Account screen's profile picture, made tappable.
//
//  `PhotosPicker` runs OUT OF PROCESS — the system picker hands back the ONE image the user
//  chose and the app never gains access to the library. That is why no
//  NSPhotoLibraryUsageDescription is required and no permission prompt appears, and it is the
//  same mechanism FeedbackView already relies on. The App Privacy filing already declares
//  Photos (Linked, App Functionality) because of that screen; storing the picked image on our
//  server does not change the data type, only the prose describing where it goes.
//

import PhotosUI
import SwiftUI

struct AvatarPickerButton: View {
    let avatarUrl: String?
    var size: CGFloat = 80
    var isUploading: Bool = false
    var hasAvatar: Bool = false

    /// Receives an already-processed square JPEG, never the raw photo.
    var onPicked: (Data) -> Void
    var onRemove: () -> Void
    /// The picked item could not be decoded. The caller MUST tell the user something.
    var onFailed: () -> Void

    @State private var pickedItem: PhotosPickerItem?
    @State private var showRemoveConfirmation = false

    var body: some View {
        PhotosPicker(selection: $pickedItem, matching: .images, photoLibrary: .shared()) {
            ProfileAvatarView(avatarUrl: avatarUrl, size: size, shape: .circle)
                .overlay(alignment: .bottomTrailing) { cameraBadge }
                .overlay { uploadingOverlay }
        }
        .buttonStyle(.plain)
        .disabled(isUploading)
        .accessibilityLabel(hasAvatar ? "Change profile picture" : "Add a profile picture")
        // Long-press to remove, so the common action (change) stays a single tap and the
        // destructive one needs deliberate intent. Only offered when there is something to
        // remove — a Remove option over the placeholder glyph does nothing and reads as broken.
        .onLongPressGesture {
            guard hasAvatar, !isUploading else { return }
            showRemoveConfirmation = true
        }
        .confirmationDialog(
            "Profile picture", isPresented: $showRemoveConfirmation, titleVisibility: .visible
        ) {
            Button("Remove photo", role: .destructive, action: onRemove)
            Button("Cancel", role: .cancel) { }
        }
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task {
                // Cleared FIRST so picking the SAME photo twice fires onChange again — without
                // this, a failed upload could not be retried with the same image.
                defer { pickedItem = nil }
                guard let raw = try? await item.loadTransferable(type: Data.self),
                      let jpeg = AvatarImageProcessor.jpegForUpload(raw) else {
                    // Both failures are real and both are silent if unhandled: an iCloud photo
                    // that never downloaded returns nil data, and a format UIImage cannot
                    // decode returns nil from the processor.
                    onFailed()
                    return
                }
                onPicked(jpeg)
            }
        }
    }

    private var cameraBadge: some View {
        Image(systemName: "camera.fill")
            .font(AppTypography.iconXS)
            // `textOnAccent` on a *Fill token — the pair that keeps white legible on a
            // saturated background in BOTH appearances.
            .foregroundColor(AppColors.textOnAccent)
            .frame(width: size * 0.32, height: size * 0.32)
            .background(Circle().fill(AppColors.primaryFill))
            // Separates the badge from the avatar behind it whatever the photo contains.
            .overlay(Circle().stroke(AppColors.background, lineWidth: 2))
    }

    @ViewBuilder
    private var uploadingOverlay: some View {
        if isUploading {
            ZStack {
                Circle().fill(AppColors.background.opacity(0.6))
                ProgressView().tint(AppColors.textPrimary)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        AvatarPickerButton(avatarUrl: nil, hasAvatar: false,
                           onPicked: { _ in }, onRemove: {}, onFailed: {})
        AvatarPickerButton(avatarUrl: nil, isUploading: true, hasAvatar: true,
                           onPicked: { _ in }, onRemove: {}, onFailed: {})
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
}
